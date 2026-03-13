from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError

from .const import (
    CHARGER_PROFILES,
    CONF_CHARGER_PROFILE,
    CONF_CHARGER_PROFILE_JSON,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    CONF_SCAN_INTERVAL,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    PLATFORMS,
    SERVICE_FORCE_CHARGE_FOR,
    SERVICE_PAUSE_SURPLUS,
    SERVICE_PROFILE_ASSISTANT,
    SERVICE_SET_SURPLUS_PROFILE,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator
from .solar_surplus import SolarSurplusController
from .surplus_profiles import (
    apply_surplus_profile,
    is_supported_surplus_profile,
    normalize_surplus_profile,
)
from .tuya_ev_charger import TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)

SERVICE_DATA_ENTRY_ID = "entry_id"
SERVICE_DATA_DURATION_MINUTES = "duration_minutes"
SERVICE_DATA_CURRENT_A = "current_a"
SERVICE_DATA_APPLY = "apply"
SERVICE_DATA_PROFILE = "profile"

SERVICE_FORCE_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_DATA_ENTRY_ID): str,
        vol.Required(SERVICE_DATA_DURATION_MINUTES): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=24 * 60),
        ),
        vol.Optional(SERVICE_DATA_CURRENT_A): vol.All(
            vol.Coerce(int),
            vol.Range(min=6, max=32),
        ),
    }
)
SERVICE_PAUSE_SURPLUS_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_DATA_ENTRY_ID): str,
        vol.Required(SERVICE_DATA_DURATION_MINUTES): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=24 * 60),
        ),
    }
)
SERVICE_PROFILE_ASSISTANT_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_DATA_ENTRY_ID): str,
        vol.Optional(SERVICE_DATA_APPLY, default=False): bool,
    }
)
SERVICE_SET_SURPLUS_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_DATA_ENTRY_ID): str,
        vol.Required(SERVICE_DATA_PROFILE): vol.All(str, vol.Length(min=1)),
    }
)


@dataclass(slots=True)
class TuyaEVChargerRuntimeData:
    client: TuyaEVChargerClient
    coordinator: TuyaEVChargerDataUpdateCoordinator
    solar_surplus_controller: SolarSurplusController | None = None


def _scan_interval_seconds(entry: ConfigEntry) -> int:
    try:
        configured_value = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        configured_value = DEFAULT_SCAN_INTERVAL_SECONDS
    return max(MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, configured_value))


def _charger_profile(entry: ConfigEntry) -> str:
    configured_value = entry.options.get(
        CONF_CHARGER_PROFILE,
        entry.data.get(CONF_CHARGER_PROFILE, DEFAULT_CHARGER_PROFILE),
    )
    normalized = str(configured_value).strip().lower()
    if normalized in CHARGER_PROFILES:
        return normalized
    return DEFAULT_CHARGER_PROFILE


def _charger_profile_json(entry: ConfigEntry) -> str:
    configured_value = entry.options.get(
        CONF_CHARGER_PROFILE_JSON,
        entry.data.get(CONF_CHARGER_PROFILE_JSON, DEFAULT_CHARGER_PROFILE_JSON),
    )
    return str(configured_value or "").strip()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = TuyaEVChargerClient(
        device_id=entry.data[CONF_DEVICE_ID],
        host=entry.data[CONF_HOST],
        local_key=entry.data[CONF_LOCAL_KEY],
        protocol_version=entry.data[CONF_PROTOCOL_VERSION],
        charger_profile=_charger_profile(entry),
        charger_profile_json=_charger_profile_json(entry),
    )

    try:
        await client.async_connect()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Unable to initialize charger client for {entry.title}: {err}"
        ) from err

    coordinator = TuyaEVChargerDataUpdateCoordinator(
        hass=hass,
        client=client,
        update_interval=timedelta(seconds=_scan_interval_seconds(entry)),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Unable to fetch initial charger state for {entry.title}: {err}"
        ) from err

    runtime_data = TuyaEVChargerRuntimeData(client=client, coordinator=coordinator)
    runtime_data.solar_surplus_controller = SolarSurplusController(
        hass=hass,
        entry=entry,
        client=client,
        coordinator=coordinator,
    )
    await runtime_data.solar_surplus_controller.async_start()
    entry.runtime_data = runtime_data
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_services(hass)
    LOGGER.debug("Tuya EV charger integration initialized: %s", entry.title)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime_data: TuyaEVChargerRuntimeData | None = entry.runtime_data
    if runtime_data is not None and runtime_data.solar_surplus_controller is not None:
        await runtime_data.solar_surplus_controller.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_register_services(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("services_registered"):
        return

    async def _handle_force_charge(call: ServiceCall) -> None:
        entry = _resolve_entry_from_call(hass, call)
        controller = _resolve_controller(entry)
        duration_minutes = int(call.data[SERVICE_DATA_DURATION_MINUTES])
        current_a = call.data.get(SERVICE_DATA_CURRENT_A)
        await controller.async_force_charge_for(
            duration_s=duration_minutes * 60,
            current_a=int(current_a) if current_a is not None else None,
        )

    async def _handle_pause_surplus(call: ServiceCall) -> None:
        entry = _resolve_entry_from_call(hass, call)
        controller = _resolve_controller(entry)
        duration_minutes = int(call.data[SERVICE_DATA_DURATION_MINUTES])
        await controller.async_pause_for(duration_s=duration_minutes * 60)

    async def _handle_profile_assistant(call: ServiceCall) -> None:
        entry = _resolve_entry_from_call(hass, call)
        controller = _resolve_controller(entry)
        apply_suggestion = bool(call.data.get(SERVICE_DATA_APPLY, False))

        report = await controller.async_profile_assistant_report()
        suggested = str(report.get("suggested_profile", "")).lower()
        applied_profile: str | None = None
        if apply_suggestion and suggested in CHARGER_PROFILES:
            new_options = dict(entry.options)
            new_options[CONF_CHARGER_PROFILE] = suggested
            hass.config_entries.async_update_entry(entry, options=new_options)
            applied_profile = suggested

        payload = {
            "entry_id": entry.entry_id,
            "entry_title": entry.title,
            "suggested_profile": suggested or None,
            "applied_profile": applied_profile,
            "report": report,
        }
        hass.bus.async_fire(f"{DOMAIN}_profile_assistant", payload)
        persistent_notification.async_create(
            hass=hass,
            title=f"Tuya EV Charger Profile Assistant ({entry.title})",
            message=(
                "Profile assistant report:\n\n```json\n"
                f"{json.dumps(payload, indent=2, ensure_ascii=True)}\n```"
            ),
            notification_id=f"{DOMAIN}_{entry.entry_id}_profile_assistant",
        )

    async def _handle_set_surplus_profile(call: ServiceCall) -> None:
        entry = _resolve_entry_from_call(hass, call)
        raw_profile = call.data[SERVICE_DATA_PROFILE]
        if not is_supported_surplus_profile(raw_profile):
            raise ServiceValidationError(
                f"Unsupported surplus profile '{raw_profile}'. Use eco, balanced or fast."
            )
        normalized_profile = normalize_surplus_profile(raw_profile)
        new_options = apply_surplus_profile(dict(entry.options), normalized_profile)
        hass.config_entries.async_update_entry(entry, options=new_options)

    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_CHARGE_FOR,
        _handle_force_charge,
        schema=SERVICE_FORCE_CHARGE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PAUSE_SURPLUS,
        _handle_pause_surplus,
        schema=SERVICE_PAUSE_SURPLUS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PROFILE_ASSISTANT,
        _handle_profile_assistant,
        schema=SERVICE_PROFILE_ASSISTANT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SURPLUS_PROFILE,
        _handle_set_surplus_profile,
        schema=SERVICE_SET_SURPLUS_PROFILE_SCHEMA,
    )
    domain_data["services_registered"] = True


def _resolve_entry_from_call(hass: HomeAssistant, call: ServiceCall) -> ConfigEntry:
    entry_id = str(call.data.get(SERVICE_DATA_ENTRY_ID, "")).strip()
    entries = hass.config_entries.async_entries(DOMAIN)
    loaded_entries = [
        entry for entry in entries if getattr(entry, "runtime_data", None) is not None
    ]
    if entry_id:
        for entry in loaded_entries:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(
            f"Entry '{entry_id}' is not loaded for domain '{DOMAIN}'."
        )
    if len(loaded_entries) == 1:
        return loaded_entries[0]
    if not loaded_entries:
        raise ServiceValidationError(f"No loaded '{DOMAIN}' entries found.")
    raise ServiceValidationError(
        f"Multiple '{DOMAIN}' entries loaded, provide '{SERVICE_DATA_ENTRY_ID}'."
    )


def _resolve_controller(entry: ConfigEntry) -> SolarSurplusController:
    runtime_data: TuyaEVChargerRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime_data is None or runtime_data.solar_surplus_controller is None:
        raise ServiceValidationError(
            f"Solar surplus controller is unavailable for entry '{entry.entry_id}'."
        )
    return runtime_data.solar_surplus_controller
