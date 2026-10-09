"""Platform setup: which entities are created, and that their identities are stable.

An entity's `unique_id` is what ties it to the user's history, dashboards and
automations. Changing one silently orphans the old entity and creates a new one
with no history, so the ids are pinned here. The classes are built through each
platform's real `async_setup_entry` (not `__new__`), which also exercises every
constructor, and the per-entity behaviour that the other test files reach only
through hand-assembled objects.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types

import pytest

DEVICE_ID = "bf1234567890abcdefxyz1"


class _Tracker:
    active_vehicle = "Zoe"

    def __init__(self):
        self.totals = {"Zoe": 1200.0}

    def total_for(self, vehicle):
        return self.totals.get(vehicle)


class _Curves:
    def __init__(self, points=None):
        self._points = points or {}

    def points_for(self, vehicle):
        return self._points.get(vehicle, [])


class _History:
    def __init__(self, sessions=()):
        self.sessions = list(sessions)
        self.latest = self.sessions[0] if self.sessions else None

    def total_energy_kwh(self):
        return sum(s["energy_kwh"] for s in self.sessions)

    def total_cost(self):
        return None


class _Controller:
    def __init__(self, trace=None):
        self.snapshot = types.SimpleNamespace(
            decision_trace=trace,
            decision_reason="idle",
            force_charge_active=False,
            target_current_a=10,
        )
        self.listeners: list = []

    def async_add_update_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


def _runtime(**overrides):
    base = {
        "client": types.SimpleNamespace(device_id=DEVICE_ID),
        "coordinator": types.SimpleNamespace(data=None, connection_health={}),
        "solar_surplus_controller": None,
        "vehicle_tracker": None,
        "session_history": None,
        "vehicle_curves": None,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _entry(options=None, runtime=None):
    entry = types.SimpleNamespace(
        entry_id="e1", title="Garage", data={}, options=options or {}, runtime_data=runtime
    )
    return entry


def _setup(module_name, entry):
    import importlib

    module = importlib.import_module(f"tuya_ev_charger.{module_name}")
    added: list = []
    asyncio.run(module.async_setup_entry(None, entry, lambda entities: added.extend(entities)))
    return added


PLATFORMS = ("sensor", "switch", "number", "select", "binary_sensor", "button", "time")


def _everything(options=None):
    entry = _entry(options, _runtime())
    return [e for name in PLATFORMS for e in _setup(name, entry)]


# --- identities --------------------------------------------------------------------


def test_every_entity_has_a_unique_id_tied_to_the_device():
    entities = _everything({"vehicles": "Zoe, Leaf"})
    ids = [e._attr_unique_id for e in entities]

    assert all(i.startswith(f"{DEVICE_ID}_") for i in ids)
    assert len(ids) == len(set(ids)), "two entities share a unique_id"


def test_the_established_unique_ids_never_change():
    """Renaming one orphans the user's entity, its history and its automations."""
    suffixes = {e._attr_unique_id.removeprefix(f"{DEVICE_ID}_") for e in _everything()}

    assert {
        "charge_session",
        "force_charge",
        "nfc_enabled",
        "surplus_mode",
        "schedule_enabled",
        "charge_current",
        "plug_in_action",
        "surplus_profile",
        "connection_health",
        "last_session_cost",
        "session_count",
    } <= suffixes


def test_the_vehicle_entities_follow_the_configured_vehicles():
    none = {e._attr_unique_id for e in _everything()}
    two = {e._attr_unique_id for e in _everything({"vehicles": "Zoe, Leaf"})}

    def tail(ids, marker):
        return {i.removeprefix(f"{DEVICE_ID}_") for i in ids if marker in i}

    assert tail(none, "vehicle_energy") == set()
    assert tail(none, "active_vehicle") == set()
    assert tail(none, "charge_curve") == {"charge_curve_car"}  # a lone car still gets one
    assert tail(two, "vehicle_energy") == {"vehicle_energy_zoe", "vehicle_energy_leaf"}
    assert tail(two, "active_vehicle") == {"active_vehicle"}
    assert tail(two, "charge_curve") == {"charge_curve_zoe", "charge_curve_leaf"}


def test_a_vehicle_name_becomes_a_safe_slug():
    ids = {e._attr_unique_id for e in _everything({"vehicles": "Tesla Model 3, Ö Car"})}

    assert f"{DEVICE_ID}_vehicle_energy_tesla_model_3" in ids


def test_entities_carry_their_dashboard_card_role_where_they_have_one():
    entities = {e._attr_unique_id.removeprefix(f"{DEVICE_ID}_"): e for e in _everything()}

    attrs = entities["charge_current"]._technical_state_attributes()
    assert attrs["tuya_ev_charger_entry_id"] == "e1"
    assert attrs["tuya_ev_charger_device_id"] == DEVICE_ID
    assert "tuya_ev_charger_card_role" in attrs
    assert "tuya_ev_charger_card_role" not in entities["nfc_enabled"]._technical_state_attributes()


# --- sensors ----------------------------------------------------------------------------


def _by_id(entities):
    return {e._attr_unique_id.removeprefix(f"{DEVICE_ID}_"): e for e in entities}


def test_the_vehicle_energy_sensor_reads_the_trackers_total():
    runtime = _runtime(vehicle_tracker=_Tracker())
    sensors = _by_id(_setup("sensor", _entry({"vehicles": "Zoe, Leaf"}, runtime)))

    assert sensors["vehicle_energy_zoe"].native_value == 1200.0
    assert sensors["vehicle_energy_leaf"].native_value is None


def test_the_vehicle_energy_sensor_is_empty_without_a_tracker():
    sensors = _by_id(_setup("sensor", _entry({"vehicles": "Zoe"}, _runtime())))

    assert sensors["vehicle_energy_zoe"].native_value is None


def test_the_charge_curve_sensor_reports_the_peak_and_the_curve():
    points = [{"power_kw": 3.2, "energy_kwh": 1}, {"power_kw": 7.1, "energy_kwh": 3}]
    runtime = _runtime(vehicle_curves=_Curves({"Zoe": points}))
    sensors = _by_id(_setup("sensor", _entry({"vehicles": "Zoe"}, runtime)))

    assert sensors["charge_curve_zoe"].native_value == 7.1
    assert sensors["charge_curve_zoe"].extra_state_attributes == {"curve": points}


def test_the_charge_curve_sensor_is_empty_until_a_curve_is_learned():
    for curves in (None, _Curves()):
        sensors = _by_id(
            _setup("sensor", _entry({"vehicles": "Zoe"}, _runtime(vehicle_curves=curves)))
        )

        assert sensors["charge_curve_zoe"].native_value is None
        assert sensors["charge_curve_zoe"].extra_state_attributes is None


def test_the_session_history_sensor_counts_sessions_and_lists_them():
    sessions = [{"energy_kwh": 5.0}, {"energy_kwh": 2.5}]
    sensors = _by_id(_setup("sensor", _entry(None, _runtime(session_history=_History(sessions)))))
    sensor = sensors["session_count"]

    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["total_energy_kwh"] == 7.5
    assert sensor.extra_state_attributes["sessions"] == sessions


def test_the_session_history_sensor_is_empty_without_a_history():
    sensor = _by_id(_setup("sensor", _entry(None, _runtime())))["session_count"]

    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_the_connection_health_sensor_reports_the_rate_and_hides_the_discovery_blob():
    health = {"success_rate_pct": 87.5, "host": "1.2.3.4", "last_discovery": {"ip": "x"}}
    runtime = _runtime(coordinator=types.SimpleNamespace(data=None, connection_health=health))
    sensor = _by_id(_setup("sensor", _entry(None, runtime)))["connection_health"]

    assert sensor.native_value == 87.5
    assert "last_discovery" not in sensor.extra_state_attributes
    assert sensor.extra_state_attributes["host"] == "1.2.3.4"


def test_a_measurement_sensor_reads_through_its_description():
    data = types.SimpleNamespace(phases={}, voltage_l1=231.0)
    runtime = _runtime(coordinator=types.SimpleNamespace(data=data, connection_health={}))
    sensors = _by_id(_setup("sensor", _entry(None, runtime)))

    assert sensors["voltage_l1"].native_value == 231.0
    assert sensors["voltage_l2"].native_value is None  # not wired on this model


def test_a_measurement_sensor_is_empty_before_the_first_poll():
    sensors = _by_id(_setup("sensor", _entry(None, _runtime())))

    assert sensors["voltage_l1"].native_value is None


def test_a_controller_sensor_reads_the_snapshot_and_listens_for_updates():
    controller = _Controller(trace={"gate": "battery"})
    runtime = _runtime(solar_surplus_controller=controller)
    sensors = _by_id(_setup("sensor", _entry(None, runtime)))
    sensor = sensors["surplus_last_decision_reason"]
    written: list = []
    sensor.async_write_ha_state = lambda: written.append(1)

    async def _lifecycle():
        await sensor.async_added_to_hass()
        controller.listeners[0]()  # the controller announces a change
        await sensor.async_will_remove_from_hass()

    asyncio.run(_lifecycle())

    assert written == [1]
    assert controller.listeners == []  # unsubscribed on removal
    assert sensor.extra_state_attributes == {"gate": "battery"}


def test_a_controller_sensor_is_inert_without_a_controller():
    sensor = _by_id(_setup("sensor", _entry(None, _runtime())))["surplus_last_decision_reason"]

    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None
    asyncio.run(sensor.async_added_to_hass())  # must not raise
    asyncio.run(sensor.async_will_remove_from_hass())


# --- switches ---------------------------------------------------------------------------


def _data(**kw):
    base = {
        "do_charge": None,
        "work_state_debug": "IDLE",
        "nfc_enabled": None,
        "schedule_enabled": False,
        "schedule_start": None,
        "schedule_end": None,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_the_nfc_and_schedule_switches_read_the_charger():
    runtime = _runtime(
        coordinator=types.SimpleNamespace(
            data=_data(nfc_enabled=True, schedule_enabled=True), connection_health={}
        )
    )
    switches = _by_id(_setup("switch", _entry(None, runtime)))

    assert switches["nfc_enabled"].is_on is True
    assert switches["schedule_enabled"].is_on is True


def test_a_failed_nfc_write_names_the_direction():
    class _Client:
        device_id = DEVICE_ID

        async def async_set_nfc_enabled(self, enabled):
            return False

    runtime = _runtime(
        client=_Client(), coordinator=types.SimpleNamespace(data=_data(nfc_enabled=False))
    )
    nfc = _by_id(_setup("switch", _entry(None, runtime)))["nfc_enabled"]

    with pytest.raises(Exception, match="nfc_enable_failed"):
        asyncio.run(nfc.async_turn_on())


def test_a_failed_schedule_write_is_reported():
    class _Client:
        device_id = DEVICE_ID

        async def async_set_schedule(self, enabled, start, end):
            return False

    runtime = _runtime(client=_Client(), coordinator=types.SimpleNamespace(data=_data()))
    schedule = _by_id(_setup("switch", _entry(None, runtime)))["schedule_enabled"]

    with pytest.raises(Exception, match="schedule_update_failed"):
        asyncio.run(schedule.async_turn_on())


# --- numbers -----------------------------------------------------------------------------


def test_the_current_number_reports_the_target_and_its_bounds():
    data = types.SimpleNamespace(
        current_target=16, max_current_cfg=32, adjust_current_options=[], phases={}
    )
    runtime = _runtime(coordinator=types.SimpleNamespace(data=data))
    number = _by_id(_setup("number", _entry(None, runtime)))["charge_current"]

    assert number.native_value == 16.0
    assert number.native_min_value == 6.0
    assert number.native_max_value == 32.0
    assert "allowed_currents" in number.extra_state_attributes


def test_the_current_number_is_empty_before_the_first_poll():
    number = _by_id(_setup("number", _entry(None, _runtime())))["charge_current"]

    assert number.native_value is None


def test_every_option_number_reads_its_stored_value_or_its_default():
    options = {
        "surplus_start_threshold_w": 2000,
        "surplus_stop_threshold_w": 1500,
        "surplus_battery_soc_high_threshold_pct": 90,
        "surplus_battery_soc_low_threshold_pct": 50,
        "surplus_max_battery_discharge_for_ev_w": 800,
    }
    numbers = _setup("number", _entry(options, _runtime()))
    values = {n.entity_description.option_key: n.native_value for n in numbers[1:]}

    assert values["surplus_start_threshold_w"] == 2000.0
    assert values["surplus_stop_threshold_w"] == 1500.0
    assert values["surplus_battery_soc_high_threshold_pct"] == 90.0
    assert values["surplus_battery_soc_low_threshold_pct"] == 50.0
    assert values["surplus_max_battery_discharge_for_ev_w"] == 800.0


def test_option_numbers_advertise_their_own_bounds():
    for number in _setup("number", _entry(None, _runtime()))[1:]:
        assert number.native_min_value == float(number.entity_description.option_min)
        assert number.native_max_value == float(number.entity_description.option_max)
        assert number.native_min_value < number.native_max_value


def test_a_non_integer_option_value_is_refused():
    number = _setup("number", _entry(None, _runtime()))[1]

    with pytest.raises(Exception, match="integer_required"):
        asyncio.run(number.async_set_native_value(10.5))


# --- selects -----------------------------------------------------------------------------


def test_the_vehicle_picker_only_exists_once_vehicles_are_named():
    def kinds(options):
        return {type(e).__name__ for e in _setup("select", _entry(options, _runtime()))}

    assert "TuyaEVChargerVehicleSelect" not in kinds(None)
    assert "TuyaEVChargerVehicleSelect" in kinds({"vehicles": "Zoe"})


def test_the_vehicle_picker_has_no_selection_without_a_tracker():
    picker = _by_id(_setup("select", _entry({"vehicles": "Zoe"}, _runtime())))["active_vehicle"]

    assert picker.current_option is None
    assert picker.options == ["Zoe"]
    with pytest.raises(Exception, match="vehicle_tracking_unavailable"):
        asyncio.run(picker.async_select_option("Zoe"))


def test_the_profile_select_offers_every_profile_and_reads_the_option():
    from tuya_ev_charger.surplus_profiles import SURPLUS_PROFILES

    select = _by_id(_setup("select", _entry({"surplus_profile": "eco"}, _runtime())))[
        "surplus_profile"
    ]

    assert select._attr_options == list(SURPLUS_PROFILES)
    assert select.current_option == "eco"


def test_the_plug_in_select_reads_the_charger():
    runtime = _runtime(
        coordinator=types.SimpleNamespace(data=types.SimpleNamespace(plug_in_action="idle"))
    )
    select = _by_id(_setup("select", _entry(None, runtime)))["plug_in_action"]

    assert select.current_option == "idle"
    assert select.available is True


# --- the base entity's availability --------------------------------------------------------


def test_entities_are_unavailable_when_the_coordinator_is_failing():
    from tuya_ev_charger.entity import TuyaEVChargerEntity

    entity = _by_id(_setup("switch", _entry(None, _runtime())))["nfc_enabled"]

    assert isinstance(entity, TuyaEVChargerEntity)
    assert entity.coordinator.data is None


# --- identity: the gwId, never the address or the MAC -----------------------------------


def _ids(entry):
    return {e._attr_unique_id for name in PLATFORMS for e in _setup(name, entry)}


def test_unique_ids_come_from_the_device_id_not_from_the_address_or_the_mac():
    """Changing the IP or the MAC must not orphan a single entity."""
    before = _entry({"vehicles": "Zoe"}, _runtime())
    before.data = {"host": "192.168.1.10", "mac": "aa:aa:aa:aa:aa:aa", "device_id": DEVICE_ID}
    after = _entry({"vehicles": "Zoe"}, _runtime())
    after.data = {"host": "10.9.9.9", "mac": "bb:bb:bb:bb:bb:bb", "device_id": DEVICE_ID}
    moved_runtime = _runtime(client=types.SimpleNamespace(device_id=DEVICE_ID, host="10.9.9.9"))
    after.runtime_data = moved_runtime

    assert _ids(before) == _ids(after)


def test_a_different_device_id_gives_a_different_set_of_ids():
    other = _entry(
        {"vehicles": "Zoe"}, _runtime(client=types.SimpleNamespace(device_id="bf_other"))
    )

    assert _ids(_entry({"vehicles": "Zoe"}, _runtime())).isdisjoint(_ids(other))


def test_the_device_is_identified_by_the_device_id_and_the_mac_is_only_a_connection():
    from tuya_ev_charger.entity import TuyaEVChargerEntity

    entity = _by_id(_setup("switch", _entry(None, _runtime())))["nfc_enabled"]
    entity._entry.data = {"mac": "AA:BB:CC:DD:EE:FF"}
    entity._runtime_data.client.host = "1.2.3.4"

    info = entity.device_info

    assert isinstance(entity, TuyaEVChargerEntity)
    assert info["identifiers"] == {("tuya_ev_charger", DEVICE_ID)}
    assert info["connections"] == {("mac", "aa:bb:cc:dd:ee:ff")}
