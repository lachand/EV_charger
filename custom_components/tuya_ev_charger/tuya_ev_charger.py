from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Callable
from typing import Any

import tinytuya

from .charger_metrics import (
    PLUG_IN_ACTION_MAP,
    EVMetrics,
    decode_metrics,
    values_match,
)
from .const import (
    ALLOWED_CURRENTS,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DP_SCHEDULE,
    TUYA_CONTROL_PORT,
    ConnectionFault,
)
from .dp_profile import resolve_profile

LOGGER = logging.getLogger(__name__)
# The charger's relay and status can lag a re-read by several seconds, notably
# when do_charge turns off. 3 x 0.5s was too tight and produced false "not
# reflected" errors on chargers that *do* report the DP, just late.
COMMAND_VERIFY_RETRIES = 8
COMMAND_VERIFY_DELAY_S = 1.0

# Socket tuning. tinytuya defaults to 5 retries x 5s delay, so a single failed
# read blocks for ~35s and floods the log. A charger only accepts one local
# connection at a time, so we fail fast and let the next poll retry instead.
SOCKET_TIMEOUT_S = 5
SOCKET_RETRY_LIMIT = 1
SOCKET_RETRY_DELAY_S = 1
# Safety net above tinytuya's own bound (about 11s worst case with the settings
# above). It must never fire on a slow-but-working charger; it only exists so a
# thread stuck inside tinytuya cannot hold the I/O lock, and with it every poll
# and command, forever.
IO_TIMEOUT_S = 20


def _configure_device(device: tinytuya.Device) -> None:
    device.set_socketTimeout(SOCKET_TIMEOUT_S)
    device.set_socketRetryLimit(SOCKET_RETRY_LIMIT)
    device.set_socketRetryDelay(SOCKET_RETRY_DELAY_S)


class TuyaEVChargerClient:
    def __init__(
        self,
        device_id: str,
        host: str,
        local_key: str,
        protocol_version: str,
        charger_profile: str = DEFAULT_CHARGER_PROFILE,
        charger_profile_json: str = DEFAULT_CHARGER_PROFILE_JSON,
    ) -> None:
        self._device_id = device_id
        self._host = host
        self._local_key = local_key
        self._protocol_version = protocol_version
        self._dp_profile, self._dp = resolve_profile(
            charger_profile,
            charger_profile_json,
        )
        self._device: tinytuya.Device | None = None
        # The charger accepts a single local connection and tinytuya's Device is
        # not thread-safe. A command runs on one worker thread while the
        # coordinator's poll runs on another, both on this one Device object, so
        # every access to `self._device` is serialised here. Without it a write
        # that lands mid-poll corrupts the socket and tinytuya returns None,
        # which used to read as "Command rejected for DP 140".
        self._io_lock = asyncio.Lock()
        # Set when a stuck call made us drop the socket; the next access rebuilds it.
        self._needs_reconnect = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def host(self) -> str:
        return self._host

    @property
    def local_key(self) -> str:
        return self._local_key

    @property
    def dp_profile(self) -> str:
        return self._dp_profile

    async def async_connect(self) -> None:
        async with self._io_lock:
            self._build_device()

    def _build_device(self) -> tinytuya.Device:
        """Replace the current Device with a fresh one. Call with the I/O lock held."""
        if self._device is not None:
            # Close the previous socket so it never lingers on the charger's
            # single local-connection slot.
            self._device.close()
        device = tinytuya.Device(
            dev_id=self._device_id,
            address=self._host,
            local_key=self._local_key,
            version=self._protocol_version,
        )
        _configure_device(device)
        self._device = device
        self._needs_reconnect = False
        return device

    async def _async_in_thread(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking tinytuya call off the loop, bounded by ``IO_TIMEOUT_S``.

        Must be called with ``self._io_lock`` held. A thread cannot be cancelled,
        so on a timeout the call is still running against the socket. The charger
        accepts a single local connection, so the socket is closed here, before
        the lock is released: that makes the stuck call fail fast instead of
        racing the next one, and the next access starts from a fresh connection.
        """
        try:
            async with asyncio.timeout(IO_TIMEOUT_S):
                return await asyncio.to_thread(func, *args)
        except TimeoutError:
            LOGGER.warning(
                "Charger I/O did not finish within %ss; dropping the connection.", IO_TIMEOUT_S
            )
            device, self._device = self._device, None
            self._needs_reconnect = True
            if device is not None:
                try:
                    await asyncio.to_thread(device.close)
                except OSError as err:
                    LOGGER.debug("Closing the stuck connection failed: %s", err)
            raise

    async def async_update_host(self, host: str) -> None:
        """Point the client at a new IP (after a DHCP change) and reconnect."""
        self._host = host
        await self.async_connect()

    async def async_update_local_key(self, local_key: str) -> None:
        """Adopt a rotated local_key (after re-pairing) and reconnect."""
        self._local_key = local_key
        await self.async_connect()

    async def _async_probe_port(self) -> ConnectionFault:
        """Classify what the control port does when we knock on it."""

        def _connect() -> ConnectionFault:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT_S)
            try:
                sock.connect((self._host, TUYA_CONTROL_PORT))
                return ConnectionFault.OK
            except ConnectionRefusedError:
                # The host is up and actively rejecting us: a Tuya charger takes
                # a single local connection, so something else almost certainly
                # holds it.
                return ConnectionFault.REFUSED
            except OSError:
                return ConnectionFault.UNREACHABLE
            finally:
                sock.close()

        try:
            async with asyncio.timeout(IO_TIMEOUT_S):
                return await asyncio.to_thread(_connect)
        except TimeoutError:
            return ConnectionFault.UNREACHABLE

    async def async_classify_fault(self) -> ConnectionFault:
        """Work out why reads are failing, so the user gets an actionable message.

        Separates a wrong or absent address, from a port that refuses us, from a
        port that talks but whose payload no longer decrypts (rotated local_key).
        """
        verdict = await self._async_probe_port()
        if verdict != ConnectionFault.OK:
            return verdict
        # The port answers, so a failed read points at the credentials.
        return (
            ConnectionFault.OK
            if await self.async_probe_host(self._host)
            else ConnectionFault.UNDECRYPTABLE
        )

    async def async_tcp_reachable(self) -> bool:
        """True when the control port accepts a TCP connection."""
        return await self._async_probe_port() == ConnectionFault.OK

    async def async_close(self) -> None:
        """Close the socket so it never lingers on the charger's single slot.

        A Tuya charger accepts only one local connection at a time; not closing
        on unload/reload leaves a zombie socket that makes the device refuse
        every later connection (including our own next instance).
        """
        async with self._io_lock:
            if self._device is not None:
                device, self._device = self._device, None
                try:
                    async with asyncio.timeout(IO_TIMEOUT_S):
                        await asyncio.to_thread(device.close)
                except TimeoutError:
                    # The device is already forgotten, so the lock is free either way.
                    LOGGER.debug("Closing the charger connection timed out; abandoning it.")
            self._needs_reconnect = False

    async def async_probe_host(self, host: str) -> bool:
        """Return True if our charger answers at ``host``.

        Opens a throwaway connection with our own device_id/local_key and reads
        the live status (grid voltage & co). Only the real charger decrypts the
        reply with our local_key, so a successful read confirms identity without
        relying on the MAC or the advertised device_id. The socket is always
        closed afterwards so the probe never holds the charger's single local
        connection slot, and the live client is left untouched.
        """

        def _probe() -> bool:
            device = tinytuya.Device(
                dev_id=self._device_id,
                address=host,
                local_key=self._local_key,
                version=self._protocol_version,
            )
            _configure_device(device)
            try:
                payload: Any = device.status()
            except Exception:  # noqa: BLE001 - any tinytuya failure means the host is not our charger
                return False
            finally:
                device.close()
            return (
                isinstance(payload, dict)
                and "Error" not in payload
                and isinstance(payload.get("dps"), dict)
                and bool(payload["dps"])
            )

        try:
            async with asyncio.timeout(IO_TIMEOUT_S):
                return await asyncio.to_thread(_probe)
        except TimeoutError:
            return False

    async def async_set_charge_current(self, amperage: int, max_current: int | None = None) -> bool:
        upper = max(ALLOWED_CURRENTS)
        if max_current is not None:
            # Respect the charger's own hardware limit (DP 152) on top of the
            # range the integration supports.
            upper = min(upper, max_current)
        if amperage < min(ALLOWED_CURRENTS) or amperage > upper:
            raise ValueError(
                f"Current setpoint {amperage}A is out of supported range "
                f"({min(ALLOWED_CURRENTS)}-{upper}A)."
            )
        return await self._async_send_command(self._dp.current_target, amperage)

    async def async_set_charge_enabled(self, enabled: bool) -> bool:
        return await self._async_send_command(self._dp.do_charge, enabled)

    async def async_set_nfc_enabled(self, enabled: bool) -> bool:
        return await self._async_send_command(self._dp.nfc_cfg, enabled)

    async def async_set_plug_in_action(self, action: str) -> bool:
        """Choose what the charger does when a cable is plugged in.

        "idle" stops the car auto-starting a charge, which is the supported way
        to hold a session rather than driving the current below the 6 A the
        IEC 61851 pilot signal defines.
        """
        for raw_value, name in PLUG_IN_ACTION_MAP.items():
            if name == action:
                return await self._async_send_command(self._dp.plug_in_action, raw_value)
        raise ValueError(f"Unsupported plug-in action '{action}'.")

    async def async_set_work_state(self, state: int) -> bool:
        """Write the charger's operating state (DP 101).

        Writable per tuya_local's config for this product. Used to put the
        charger back into "ready to charge" after a session, which is what
        clears a stale power reading on some firmwares.
        """
        return await self._async_send_command(self._dp.work_state, state)

    async def async_reboot(self) -> bool:
        # Depending on firmware variants, reboot may accept bool, int, or string payloads.
        for payload in (True, 1, "1"):
            if await self._async_send_command(self._dp.reboot, payload, verify=False):
                return True
        return False

    async def async_get_metrics(self) -> EVMetrics | None:
        async with self._io_lock:
            dps = await self._async_get_dps_payload()
        if dps is None:
            return None
        return decode_metrics(dps, self._dp)

    async def async_set_schedule(self, enabled: bool, start: str, end: str) -> bool:
        payload = json.dumps(
            {"m": 2 if enabled else 0, "dt": 0, "ss": start, "se": end},
            separators=(",", ":"),
        )
        return await self._async_send_command(DP_SCHEDULE, payload, verify=False)

    async def async_get_raw_dps(self) -> dict[str, Any] | None:
        async with self._io_lock:
            return await self._async_get_dps_payload()

    async def _async_send_command(self, dp_id: str, value: Any, verify: bool = True) -> bool:
        """Write a DP and confirm it took, holding the charger's single slot throughout.

        tinytuya's ``set_value`` returns one of three things, and they mean
        different things:

        * a ``dict`` **with** an ``"Error"`` key -- a genuine transport failure
          (offline, timeout, undecryptable). Every real failure looks like this.
        * ``None`` -- the charger sent a bare 28-byte ACK and no ``dps`` echo.
          This is the *normal* reply to a write-only DP on protocol 3.4/3.5
          (DP 140 in particular), not a rejection. It used to be logged as
          "Command rejected for DP 140: None" and fail the whole start.
        * a ``dict`` without ``"Error"`` -- accepted with an echo.

        Only the first is a failure. The other two fall through to read-back
        verification, which already tolerates a charger that never echoes the DP.
        """
        async with self._io_lock:
            device = self._get_device()
            try:
                response: Any = await self._async_in_thread(device.set_value, dp_id, value)
            except TimeoutError:
                # Sent or not, we cannot tell: only a read-back can say.
                if not verify:
                    return False
                response = None

            if isinstance(response, dict) and "Error" in response:
                LOGGER.warning("Command to DP %s failed: %s", dp_id, response["Error"])
                return False

            if response is None:
                LOGGER.debug(
                    "DP %s: charger acknowledged without echoing it back; verifying by read-back.",
                    dp_id,
                )

            if not verify:
                return True

            verdict = await self._async_verify_command(dp_id, value)
            if verdict is not False:
                # True (echoed match) or None (this charger never reports the DP).
                return True

        LOGGER.warning("Command accepted but not reflected in status for DP %s.", dp_id)
        return False

    async def _async_verify_command(self, dp_id: str, expected: Any) -> bool | None:
        """Check the charger echoes back a written DP.

        Returns True on a match, False on a genuine mismatch, and None when the
        DP is simply absent from the status payload. Several models never report
        the write-only DPs their profile declares (for example DP 140 does not
        exist on the depow 3.5kW), so demanding an echo there would fail every
        command even though the charger obeyed it.

        The full retry budget is kept for chargers that *do* report the DP but
        echo it late; a charger that omits it from two clean reads is taken to
        never report it, so the caller is not made to wait out all the retries.

        Must be called with ``self._io_lock`` held.
        """
        saw_dp = False
        reads_without_dp = 0
        for _ in range(COMMAND_VERIFY_RETRIES):
            await asyncio.sleep(COMMAND_VERIFY_DELAY_S)
            dps = await self._async_get_dps_payload()
            if dps is None:
                continue
            if dp_id not in dps:
                reads_without_dp += 1
                if reads_without_dp >= 2:
                    break
                continue
            saw_dp = True
            if values_match(dps.get(dp_id), expected):
                return True

        if not saw_dp:
            LOGGER.debug(
                "DP %s is not reported by this charger; assuming the command was applied.",
                dp_id,
            )
            return None
        return False

    async def _async_get_dps_payload(self) -> dict[str, Any] | None:
        """Read the charger's DPS. Must be called with ``self._io_lock`` held."""
        device = self._get_device()
        try:
            payload: Any = await self._async_in_thread(device.status)
        except TimeoutError:
            return None

        if not isinstance(payload, dict):
            LOGGER.error("Invalid status payload type: %s", type(payload).__name__)
            return None

        if "Error" in payload:
            LOGGER.error("Charger returned an error payload: %s", payload["Error"])
            return None

        dps: Any = payload.get("dps", {})
        if not isinstance(dps, dict):
            LOGGER.error("Missing or invalid DPS payload.")
            return None
        return dps

    def _get_device(self) -> tinytuya.Device:
        if self._device is None and self._needs_reconnect:
            return self._build_device()
        if self._device is None:
            raise RuntimeError("Device client is not initialized. Call async_connect first.")
        return self._device
