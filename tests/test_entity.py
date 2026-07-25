"""The shared base entity: device grouping and the DeviceInfo it builds.

DeviceInfo is what puts every entity under one device in the UI. It reads the
charger's self-reported info, which many firmwares leave blank, so the fallbacks
matter -- a blank field must become a sensible default, not an empty label.
"""

from __future__ import annotations

import types


def _entity(*, charger_info=None, title="My Charger", host="192.168.1.50", mac=None, variant=None):
    from tuya_ev_charger.entity import TuyaEVChargerEntity

    entity = TuyaEVChargerEntity.__new__(TuyaEVChargerEntity)
    data = types.SimpleNamespace(charger_info=charger_info or {}, product_variant=variant)
    entity.coordinator = types.SimpleNamespace(data=data)
    entity._entry = types.SimpleNamespace(
        title=title, entry_id="e1", data={"mac": mac} if mac else {}
    )
    entity._runtime_data = types.SimpleNamespace(
        client=types.SimpleNamespace(device_id="bf23", host=host)
    )
    entity._card_role = None
    entity._card_index = None
    return entity


def test_all_entities_share_one_device_identifier():
    """The identifier is the device id, so every entity lands on one device."""
    from tuya_ev_charger.const import DOMAIN

    info = _entity().device_info
    assert info["identifiers"] == {(DOMAIN, "bf23")}


def test_blank_charger_info_falls_back_to_sensible_defaults():
    info = _entity(charger_info={}).device_info
    assert info["manufacturer"] == "Tuya"
    assert info["model"] == "EV Charger"


def test_reported_charger_info_is_used_when_present():
    info = _entity(
        charger_info={"manufacturer": "dé", "model": "Portable 3.5kW", "firmware_version": "2.9.3"}
    ).device_info
    assert info["manufacturer"] == "dé"
    assert info["model"] == "Portable 3.5kW"
    assert info["sw_version"] == "2.9.3"


def test_the_configuration_url_points_at_the_charger():
    info = _entity(host="192.168.1.99").device_info
    assert info["configuration_url"] == "http://192.168.1.99"


def test_the_mac_is_registered_for_dhcp_discovery():
    """Without the connection, a new DHCP lease could not auto-update the IP."""
    from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC

    info = _entity(mac="AA:BB:CC:DD:EE:FF").device_info
    assert any(conn[0] == CONNECTION_NETWORK_MAC for conn in info["connections"])


def test_no_mac_means_no_connections_block():
    info = _entity(mac=None).device_info
    assert "connections" not in info


def test_the_hardware_version_comes_from_the_product_variant():
    info = _entity(variant=2).device_info
    assert info["hw_version"] == "2"


def test_technical_attributes_carry_the_entry_identity():
    from tuya_ev_charger.entity import (
        ATTR_CHARGER_DEVICE_ID,
        ATTR_CHARGER_ENTRY_ID,
    )

    attrs = _entity()._technical_state_attributes()
    assert attrs[ATTR_CHARGER_ENTRY_ID] == "e1"
    assert attrs[ATTR_CHARGER_DEVICE_ID] == "bf23"
