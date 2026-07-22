"""Minimal Home Assistant stubs so the integration imports without HA installed.

Pulling in Home Assistant just to assert on DP parsing and schema shape would
make the suite slow and tie it to an HA version. Only what the modules touch at
import time is stubbed; everything a test actually exercises is real code.
"""

from __future__ import annotations

import pathlib
import sys
import types
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pytest
import voluptuous as _vol

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENTS = REPO_ROOT / "custom_components"


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


class _Generic:
    """Base for the generics HA subscripts, e.g. DataUpdateCoordinator[T]."""

    def __class_getitem__(cls, _item: Any) -> type:
        return cls

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


@dataclass(frozen=True, kw_only=True)
class _EntityDescription:
    """Superset of the description fields the integration sets.

    Frozen because the integration subclasses these with frozen dataclasses, and
    Python refuses to inherit a frozen dataclass from a non-frozen one.
    """

    key: str
    translation_key: str | None = None
    name: str | None = None
    icon: str | None = None
    device_class: Any = None
    state_class: Any = None
    entity_category: Any = None
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None
    options: list[str] | None = None
    native_min_value: float | None = None
    native_max_value: float | None = None
    native_step: float | None = None
    mode: Any = None
    entity_registry_enabled_default: bool = True
    extra: dict = field(default_factory=dict)


def _install_stubs() -> None:
    if "homeassistant" in sys.modules:
        return

    _module("homeassistant")

    class Platform(StrEnum):
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        SENSOR = "sensor"
        NUMBER = "number"
        SELECT = "select"
        SWITCH = "switch"
        TIME = "time"

    _module(
        "homeassistant.const",
        Platform=Platform,
        CONF_HOST="host",
        CONF_DEVICE_ID="device_id",
        CONF_DOMAIN="domain",
        CONF_ENTITY_ID="entity_id",
        CONF_FOR="for",
        CONF_PLATFORM="platform",
        CONF_TYPE="type",
        PERCENTAGE="%",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        UnitOfElectricCurrent=types.SimpleNamespace(AMPERE="A"),
        UnitOfElectricPotential=types.SimpleNamespace(VOLT="V"),
        UnitOfEnergy=types.SimpleNamespace(KILO_WATT_HOUR="kWh"),
        UnitOfPower=types.SimpleNamespace(WATT="W", KILO_WATT="kW"),
        UnitOfTemperature=types.SimpleNamespace(CELSIUS="C"),
        UnitOfTime=types.SimpleNamespace(SECONDS="s"),
    )

    class ConfigEntry:
        def __init__(self, **kwargs: Any) -> None:
            self.data: dict = {}
            self.options: dict = {}
            self.entry_id = "test"

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs: Any) -> None:  # accepts domain=...
            super().__init_subclass__()

    class OptionsFlow:
        pass

    _module(
        "homeassistant.config_entries",
        ConfigEntry=ConfigEntry,
        ConfigFlow=ConfigFlow,
        OptionsFlow=OptionsFlow,
        ConfigEntryState=types.SimpleNamespace(LOADED="loaded"),
    )
    _module(
        "homeassistant.core",
        HomeAssistant=type("HomeAssistant", (), {}),
        CALLBACK_TYPE=object,
        ServiceCall=type("ServiceCall", (), {}),
        Event=_Generic,
        EventStateChangedData=dict,
        callback=lambda func: func,
    )
    _module(
        "homeassistant.exceptions",
        HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
        ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
        ServiceValidationError=type("ServiceValidationError", (Exception,), {}),
    )
    _module("homeassistant.data_entry_flow", FlowResult=dict)

    # --- helpers -----------------------------------------------------------
    _module("homeassistant.helpers")
    _module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=type("DataUpdateCoordinator", (_Generic,), {}),
        CoordinatorEntity=type("CoordinatorEntity", (_Generic,), {}),
        UpdateFailed=type("UpdateFailed", (Exception,), {}),
    )

    class EntityCategory(StrEnum):
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    _module("homeassistant.helpers.entity", EntityCategory=EntityCategory)
    _module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    _module(
        "homeassistant.helpers.device_registry",
        DeviceInfo=dict,
        format_mac=lambda mac: str(mac).lower(),
        CONNECTION_NETWORK_MAC="mac",
        async_get=lambda hass: None,
    )
    _module(
        "homeassistant.helpers.issue_registry",
        async_create_issue=lambda *a, **k: None,
        async_delete_issue=lambda *a, **k: None,
        IssueSeverity=types.SimpleNamespace(WARNING="warning", ERROR="error"),
    )
    _module("homeassistant.helpers.storage", Store=_Generic)

    class RegistryEntryDisabler(StrEnum):
        USER = "user"
        INTEGRATION = "integration"
        CONFIG_ENTRY = "config_entry"

    _module(
        "homeassistant.helpers.entity_registry",
        async_get=lambda hass: None,
        async_entries_for_config_entry=lambda registry, entry_id: [],
        async_entries_for_device=lambda registry, device_id: [],
        RegistryEntry=object,
        RegistryEntryDisabler=RegistryEntryDisabler,
    )
    _module(
        "homeassistant.components.repairs",
        RepairsFlow=type("RepairsFlow", (), {}),
        ConfirmRepairFlow=type("ConfirmRepairFlow", (), {}),
    )
    class _Selector:
        """Selectors must be callable: voluptuous compiles them as validators."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.config = args[0] if args else kwargs

        def __call__(self, value: Any) -> Any:
            return value

    _module(
        "homeassistant.helpers.selector",
        EntitySelector=type("EntitySelector", (_Selector,), {}),
        EntitySelectorConfig=lambda **k: k,
        SelectSelector=type("SelectSelector", (_Selector,), {}),
        SelectSelectorConfig=lambda **k: k,
        SelectOptionDict=lambda **k: k,
        SelectSelectorMode=types.SimpleNamespace(LIST="list"),
        TextSelector=type("TextSelector", (_Selector,), {}),
        TextSelectorConfig=lambda **k: k,
    )
    _module(
        "homeassistant.helpers.config_validation",
        entity_id=str,
        positive_time_period_dict=dict,
    )
    _module(
        "homeassistant.helpers.trigger",
        TriggerActionType=object,
        TriggerInfo=object,
    )
    _module("homeassistant.helpers.typing", ConfigType=dict)
    _module("homeassistant.helpers.service_info")
    _module("homeassistant.helpers.service_info.dhcp", DhcpServiceInfo=object)
    _module(
        "homeassistant.helpers.event",
        async_track_state_change_event=lambda *a, **k: (lambda: None),
    )
    _module("homeassistant.util")
    _module(
        "homeassistant.util.dt",
        utcnow=lambda: __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        now=lambda: __import__("datetime").datetime.now(),
        as_local=lambda value: value,
        parse_time=lambda value: None,
    )

    # --- entity platforms --------------------------------------------------
    _module("homeassistant.components")
    _module("homeassistant.components.diagnostics", async_redact_data=lambda d, keys: d)
    # Device automation: only the base schema and the state trigger it delegates to.
    _module(
        "homeassistant.components.device_automation",
        DEVICE_TRIGGER_BASE_SCHEMA=_vol.Schema({}, extra=_vol.ALLOW_EXTRA),
    )
    _module("homeassistant.components.homeassistant")
    _module("homeassistant.components.homeassistant.triggers")
    _module(
        "homeassistant.components.homeassistant.triggers.state",
        CONF_PLATFORM="platform",
        CONF_ENTITY_ID="entity_id",
        CONF_FROM="from",
        CONF_TO="to",
        async_validate_trigger_config=None,
        async_attach_trigger=None,
    )
    _module("homeassistant.components.persistent_notification", async_create=lambda **k: None)
    _module("homeassistant.components.binary_sensor",
            BinarySensorEntity=object, BinarySensorEntityDescription=_EntityDescription)
    _module("homeassistant.components.button", ButtonEntity=object)
    _module("homeassistant.components.select", SelectEntity=object)
    _module("homeassistant.components.switch", SwitchEntity=object)
    _module("homeassistant.components.time", TimeEntity=object)

    class SensorDeviceClass(StrEnum):
        VOLTAGE = "voltage"
        CURRENT = "current"
        POWER = "power"
        ENERGY = "energy"
        TEMPERATURE = "temperature"
        DURATION = "duration"
        ENUM = "enum"

    class SensorStateClass(StrEnum):
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    _module("homeassistant.components.sensor",
            SensorEntity=object, SensorEntityDescription=_EntityDescription,
            SensorDeviceClass=SensorDeviceClass, SensorStateClass=SensorStateClass)

    class NumberMode(StrEnum):
        BOX = "box"
        SLIDER = "slider"

    _module("homeassistant.components.number",
            NumberEntity=object, NumberEntityDescription=_EntityDescription,
            NumberMode=NumberMode)


@pytest.fixture(scope="session", autouse=True)
def _stub_homeassistant() -> None:
    _install_stubs()
    if str(COMPONENTS) not in sys.path:
        sys.path.insert(0, str(COMPONENTS))


@pytest.fixture
def charging_dps() -> dict:
    """A payload captured from a real charger mid-session (8.7 A at 227.0 V)."""
    return {
        "101": 300,
        "102": '{"L1":[2270,87,19],"L2":[0,0,0],"L3":[0,0,0],'
               '"t":510,"p":19,"d":94590,"e":52}',
        "105": '{"t":"2026-07-21 10:21:05","s":"10:21","e":"10:51","d":1801,"c":13}',
        "106": '{"r":"Type B, AC 30mA + DC 6mA","fv":"1.9.7"}',
        "107": "[6, 8, 10, 13, 16]",
        "109": "WORKING",
        "150": 10,
        "151": '{"m":0,"dt":0,"ss":"00:00","se":"08:00"}',
        "152": 16,
        "157": 1,
        "188": False,
        "189": 561,
    }
