from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud import TuyaCloudError, async_fetch_local_key
from .const import (
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_API_SECRET,
    CONF_CLOUD_REGION,
    CONF_DEVICE_ID,
    DEFAULT_CLOUD_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOCAL_KEY_REFRESH_COOLDOWN_SECONDS,
    REDISCOVERY_COOLDOWN_SECONDS,
    FAULT_DIAGNOSIS_COOLDOWN_SECONDS,
    REDISCOVERY_SCAN_SECONDS,
    ConnectionFault,
)
from .discovery import async_scan_devices_by_id
from .repairs import ISSUE_CONNECTION_REFUSED, async_clear, async_raise
from .tuya_ev_charger import EVMetrics, TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


def _looks_like_gwid(device_id: str) -> bool:
    """True when the stored device_id is a real Tuya gwId, not a legacy IP.

    Old buggy scans stored the IP in the device_id field; those have dots and are
    short. A genuine gwId is a long dotless token (e.g. bf23dbbd3d2eb2c804aswb).
    """
    return len(device_id) >= 12 and "." not in device_id


class TuyaEVChargerDataUpdateCoordinator(DataUpdateCoordinator[EVMetrics]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: TuyaEVChargerClient,
        entry: ConfigEntry,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self.entry = entry
        self.last_discovery: dict | None = None
        self.new_local_key: str | None = None
        # Set by async_setup_entry once storage has been loaded.
        self.vehicle_tracker: Any = None
        self._last_rediscovery_at: float = 0.0
        self._last_key_refresh_at: float = 0.0
        # Diagnosing a failure costs a TCP connect and possibly a full read, so
        # the verdict is cached: a charger that stays broken must not be probed
        # on every single poll.
        self._last_fault: ConnectionFault | None = None
        self._last_fault_at: float = 0.0
        self._relocating: Any = None

    async def _async_update_data(self) -> EVMetrics:
        metrics = await self._async_fetch_metrics()
        if metrics is not None:
            self._async_note_success()
            await self._async_track_vehicle_energy(metrics)
            return metrics

        # Communication failed: after a power cycle the charger's DHCP IP may
        # have changed. Relocate it (scan + live read) and retry once before failing.
        if await self._async_try_rediscover_host():
            metrics = await self._async_fetch_metrics()
            if metrics is not None:
                self._async_note_success()
                await self._async_track_vehicle_energy(metrics)
                return metrics

        # Still failing while the control port answers: the payload does not
        # decrypt, which means the local_key was rotated by a re-pairing.
        if await self._async_try_refresh_local_key():
            metrics = await self._async_fetch_metrics()
            if metrics is not None:
                self._async_note_success()
                await self._async_track_vehicle_energy(metrics)
                return metrics

        message = await self._async_failure_message()
        if self._last_fault == ConnectionFault.UNDECRYPTABLE:
            # The charger answers but nothing decrypts: the key was rotated by a
            # re-pairing. ConfigEntryAuthFailed is what puts a "Reconfigure"
            # banner in front of the user; UpdateFailed would just retry forever.
            raise ConfigEntryAuthFailed(message)
        raise UpdateFailed(message)

    async def _async_failure_message(self) -> str:
        """Explain *why* the poll failed, and raise a repair issue when useful."""
        host = self.client.host
        entry_id = self.entry.entry_id
        fault = await self._async_classify_fault_throttled()
        if fault is None:
            return f"Charger unreachable at {host} (no telemetry received)."

        if fault == ConnectionFault.REFUSED:
            async_raise(
                self.hass, entry_id, ISSUE_CONNECTION_REFUSED,
                translation_placeholders={"host": host},
            )
            return (
                f"Charger at {host} refused the connection. These chargers accept "
                "a single local connection at a time — check that the Smart Life "
                "app or another Tuya integration is not holding it."
            )

        async_clear(self.hass, entry_id, ISSUE_CONNECTION_REFUSED)

        if fault == ConnectionFault.UNDECRYPTABLE:
            return (
                f"Charger at {host} answers but its replies cannot be decrypted; "
                "the local_key was most likely changed by a re-pairing."
            )
        return f"Charger unreachable at {host} (nothing answers at that address)."

    async def _async_classify_fault_throttled(self) -> ConnectionFault | None:
        """Diagnose the failure, at most once per cooldown.

        Classifying opens a TCP connection and, when the port answers, performs a
        full status read. A charger that stays broken would otherwise pay that
        cost on every poll, forever. Returns None when the diagnosis itself
        failed and no cached verdict is available.
        """
        now = self.hass.loop.time()
        if (
            self._last_fault is not None
            and now - self._last_fault_at < FAULT_DIAGNOSIS_COOLDOWN_SECONDS
        ):
            return self._last_fault

        try:
            fault = await self.client.async_classify_fault()
        except Exception as err:  # noqa: BLE001 - diagnosis must never mask the failure
            LOGGER.debug("Fault diagnosis failed: %s", err)
            return self._last_fault

        self._last_fault = fault
        self._last_fault_at = now
        return fault

    def _async_note_success(self) -> None:
        """The charger answered: drop any stale diagnosis and repair issue."""
        if self._last_fault is not None:
            self._last_fault = None
            self._last_fault_at = 0.0
            async_clear(self.hass, self.entry.entry_id, ISSUE_CONNECTION_REFUSED)

    async def _async_track_vehicle_energy(self, metrics: EVMetrics) -> None:
        """Route charged energy into the active vehicle's total.

        Driven by the per-session counter, which resets to zero at the start of
        each session; the tracker re-baselines on a decrease, so each session's
        increments land on whichever vehicle is selected.
        """
        tracker = self.vehicle_tracker
        if tracker is None:
            return
        try:
            await tracker.async_process_counter(metrics.session_energy_kwh)
        except Exception as err:  # noqa: BLE001 - accounting must never break polling
            LOGGER.debug("Vehicle energy tracking failed: %s", err)

    async def _async_fetch_metrics(self) -> EVMetrics | None:
        try:
            return await self.client.async_get_metrics()
        except Exception as err:  # noqa: BLE001 - surfaced as UpdateFailed upstream
            LOGGER.debug("Charger poll failed: %s", err)
            return None

    async def _async_try_rediscover_host(self) -> bool:
        """Relocate the charger when its IP changed.

        Listens for our charger's own broadcast (``wantids``) and trusts the IP
        it advertises for our device_id — the definitive identity. This ignores
        any unrelated Tuya device on the LAN. Only the in-memory client host is
        updated (immediate recovery, no config-entry reload); the new IP is
        persisted lazily on the next setup. Throttled so an offline charger does
        not scan on every poll.
        """
        now = self.hass.loop.time()
        if now - self._last_rediscovery_at < REDISCOVERY_COOLDOWN_SECONDS:
            return False
        self._last_rediscovery_at = now

        if self.data is not None:
            # Routine poll: listening for a broadcast takes seconds, and blocking
            # the update loop for that long stalls every entity. Relocate in the
            # background and let the next poll use the result.
            self._schedule_relocation()
            return False

        # First refresh: setup rebuilds the client from the stored host on every
        # retry, so an in-memory fix from a background task would be thrown away.
        # This one has to resolve inline.
        return await self._async_relocate()

    def _schedule_relocation(self) -> None:
        """Run one relocation in the background, never two at once."""
        if self._relocating is not None and not self._relocating.done():
            return

        async def _run() -> None:
            try:
                if await self._async_relocate():
                    # The host changed; pull fresh data straight away rather than
                    # waiting out the poll interval.
                    await self.async_request_refresh()
            except Exception as err:  # noqa: BLE001 - background task must not escape
                LOGGER.debug("Background relocation failed: %s", err)

        self._relocating = self.entry.async_create_background_task(
            self.hass, _run(), name=f"{DOMAIN}_relocate"
        )

    async def _async_relocate(self) -> bool:
        """Scan for our charger and adopt its advertised address."""
        device_id = str(self.entry.data.get(CONF_DEVICE_ID, "")).strip()
        current_host = self.client.host

        # Target our charger specifically so a neighbour's device broadcasting
        # first does not end the scan before ours is heard.
        wantids = [device_id] if _looks_like_gwid(device_id) else None
        candidates = await async_scan_devices_by_id(
            self.hass, scantime=REDISCOVERY_SCAN_SECONDS, wantids=wantids
        )
        if not candidates:
            LOGGER.debug("Re-discovery scan found no Tuya devices on the network.")
            return False

        # Strong path: our device_id is broadcasting -> trust the advertised IP.
        mine = candidates.get(device_id) if device_id else None
        if mine and mine.get("ip"):
            new_host = str(mine["ip"]).strip()
            if new_host and new_host != current_host:
                LOGGER.info(
                    "Charger %s found at %s (was %s) via broadcast; updating host.",
                    device_id,
                    new_host,
                    current_host,
                )
                self.last_discovery = mine
                await self.client.async_update_host(new_host)
                return True
            LOGGER.debug(
                "Charger %s still advertises %s; connection issue is not an IP change.",
                device_id,
                current_host,
            )
            return False

        # Our device_id is a proper gwId but was not heard: the charger is
        # offline or slow to broadcast. Do NOT probe unrelated devices with our
        # credentials — just fail this cycle and let the next poll retry.
        if _looks_like_gwid(device_id):
            LOGGER.debug(
                "Charger %s not heard on the network this cycle; will retry.",
                device_id,
            )
            return False

        # Legacy fallback only: the stored device_id is not a gwId (an old buggy
        # scan saved the IP there). Identify our charger by a live read.
        for host in self._other_candidate_hosts(candidates, current_host):
            if not await self.client.async_probe_host(host):
                continue
            LOGGER.info(
                "Charger confirmed at %s (was %s) via live telemetry read.",
                host,
                current_host,
            )
            self.last_discovery = self._discovery_for_host(candidates, host)
            await self.client.async_update_host(host)
            return True

        LOGGER.debug(
            "Re-discovery found %d device(s) but none matched our stored id/key.",
            len(candidates),
        )
        return False

    async def _async_try_refresh_local_key(self) -> bool:
        """Re-download the local_key from the Tuya Cloud after a re-pairing.

        Only attempted when the charger's control port *does* answer: a reachable
        port with unreadable payloads points at a rotated key rather than a
        network problem. Requires cloud credentials to have been configured;
        without them this is a no-op. Heavily throttled (cloud API quotas).
        """
        data = self.entry.data
        api_key = str(data.get(CONF_CLOUD_API_KEY, "")).strip()
        api_secret = str(data.get(CONF_CLOUD_API_SECRET, "")).strip()
        device_id = str(data.get(CONF_DEVICE_ID, "")).strip()
        if not (api_key and api_secret and device_id):
            return False

        now = self.hass.loop.time()
        if now - self._last_key_refresh_at < LOCAL_KEY_REFRESH_COOLDOWN_SECONDS:
            return False

        if not await self.client.async_tcp_reachable():
            # Port is down: this is a connectivity problem, not a key problem.
            return False
        self._last_key_refresh_at = now

        region = str(data.get(CONF_CLOUD_REGION, DEFAULT_CLOUD_REGION)).strip() or DEFAULT_CLOUD_REGION
        try:
            new_key = await async_fetch_local_key(
                self.hass, region, api_key, api_secret, device_id
            )
        except TuyaCloudError as err:
            LOGGER.warning("Could not refresh local_key from Tuya Cloud: %s", err)
            return False

        if not new_key or new_key == self.client.local_key:
            LOGGER.debug("Tuya Cloud returned no new local_key for %s.", device_id)
            return False

        LOGGER.info(
            "local_key for charger %s changed (re-pairing); adopting the new key.",
            device_id,
        )
        await self.client.async_update_local_key(new_key)
        self.new_local_key = new_key
        return True

    @staticmethod
    def _other_candidate_hosts(candidates: dict[str, dict], current_host: str) -> list[str]:
        """Discovered IPs to probe, excluding the already-failed current host."""
        hosts: list[str] = []
        for info in candidates.values():
            host = str(info.get("ip", "")).strip()
            if host and host != current_host and host not in hosts:
                hosts.append(host)
        return hosts

    @staticmethod
    def _discovery_for_host(candidates: dict[str, dict], host: str) -> dict:
        for info in candidates.values():
            if str(info.get("ip", "")).strip() == host:
                return info
        return {"ip": host}
