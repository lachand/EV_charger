"""Guards for failures that actually reached users.

Each test here maps to a released bug, so they are worth their weight even
though the suite is otherwise light.
"""

from __future__ import annotations

import json
import pathlib
from typing import ClassVar

import pytest
import voluptuous as vol

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = REPO_ROOT / "custom_components" / "tuya_ev_charger"


class _FakeSelector:
    """Stands in for Home Assistant's EntitySelector.

    Rejects non-entity values the way cv.entity_id_or_uuid does, and exposes
    serialize() the way the frontend serialiser expects.
    """

    def serialize(self):
        return {"selector": {"entity": {"domain": ["sensor"]}}}

    def __call__(self, data):
        if not isinstance(data, str) or "." not in data:
            raise vol.Invalid(f"Entity {data} is neither a valid entity ID nor a UUID")
        return data


def test_optional_entity_schema_serialises():
    """2.0.1 shipped vol.Any(selector, None): valid, but unserialisable.

    Home Assistant converts the options schema to JSON to draw the form, so a
    schema it cannot serialise makes the dialog return 500 before it opens.
    """
    voluptuous_serialize = pytest.importorskip("voluptuous_serialize")

    def serializer(schema):
        if isinstance(schema, _FakeSelector):
            return schema.serialize()
        return voluptuous_serialize.UNSUPPORTED

    schema = vol.Schema(
        {vol.Optional("sensor", description={"suggested_value": ""}): _FakeSelector()}
    )
    # Must not raise.
    assert voluptuous_serialize.convert(schema, custom_serializer=serializer)


def test_optional_entity_left_empty_still_validates():
    """The bug the 2.0.1 change was trying to fix, kept covered."""
    schema = vol.Schema(
        {vol.Optional("sensor", description={"suggested_value": ""}): _FakeSelector()}
    )
    assert schema({}) == {}
    assert schema({"sensor": "sensor.grid"}) == {"sensor": "sensor.grid"}
    with pytest.raises(vol.Invalid):
        schema({"sensor": "not_an_entity"})


def test_options_form_declares_no_defaults_on_entity_pickers():
    """A voluptuous default is validated on insert, so None fails the selector.

    Checked on the schema the flow actually builds, not on its source text, so
    the guard survives the form being restructured.
    """
    import types

    from tuya_ev_charger.options_flow import OPTIONAL_ENTITY_OPTIONS, TuyaEVChargerOptionsFlow

    flow = TuyaEVChargerOptionsFlow(types.SimpleNamespace(data={}, options={}, entry_id="test"))
    schema = flow._build_options_schema({}, computed={})

    # The pickers live inside collapsible sections now, so walk one level down.
    entity_markers = []
    for marker, value in schema.schema.items():
        inner = getattr(value, "schema", None)
        candidates = inner.schema.items() if inner is not None else [(marker, value)]
        entity_markers += [key for key, _ in candidates if str(key) in OPTIONAL_ENTITY_OPTIONS]

    assert len(entity_markers) == len(OPTIONAL_ENTITY_OPTIONS), (
        "every optional entity picker must still be reachable in the schema"
    )

    for marker in entity_markers:
        assert marker.default is vol.UNDEFINED, (
            f"{marker} carries a default; voluptuous validates inserted defaults, "
            "so an untouched picker would fail with 'not a valid entity ID'"
        )
        assert marker.description == {"suggested_value": None} or "suggested_value" in (
            marker.description or {}
        )


def test_diagnostics_redacts_every_secret():
    """Diagnostics get attached to public issues; cloud creds are account-wide."""
    from tuya_ev_charger.diagnostics import TO_REDACT

    for secret in ("local_key", "cloud_api_key", "cloud_api_secret", "mac", "device_id"):
        assert secret in TO_REDACT, f"{secret} would be published in diagnostics"


def test_a_discovery_record_is_redacted_under_tinytuyas_own_names():
    """Diagnostics now embed the last discovery scan, which is a tinytuya dict.

    Its keys are `ip`, `gwId`, `key` -- not the config-entry names covered
    above -- so the same identity would have been published under a different
    spelling.
    """
    from tuya_ev_charger.diagnostics import TO_REDACT

    scan_result = {
        "ip": "192.168.1.237",
        "gwId": "bf23dbbd3d2eb2c804aswb",
        "key": "a-local-key",
        "version": "3.5",
        "productKey": "keyxxxx",
        "name": "Charger",
    }
    leaked = {
        field: value
        for field, value in scan_result.items()
        if field in {"ip", "gwId", "key"} and field not in TO_REDACT
    }
    assert not leaked, f"discovery scan would publish {sorted(leaked)}"
    # Deliberately kept: it identifies the model, which is the point of the dump.
    assert "productKey" not in TO_REDACT


def test_every_module_imports():
    """A constant imported but never defined breaks the whole integration."""
    import importlib

    for path in sorted(COMPONENT.glob("*.py")):
        if path.stem == "__init__":
            continue  # importing the package would need the full HA runtime
        importlib.import_module(f"tuya_ev_charger.{path.stem}")


def test_translation_files_are_valid_and_aligned():
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    french = json.loads((COMPONENT / "translations" / "fr.json").read_text())

    # strings.json is Home Assistant's English source, not a second French copy:
    # any locale without its own file falls back to it.
    assert strings["entity"]["sensor"]["voltage_l1"]["name"] == "Voltage L1"
    assert set(english["entity"]["sensor"]) == set(french["entity"]["sensor"])


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix, node


def test_every_translation_has_exactly_the_english_keys():
    """A locale that lags behind shows a half-translated flow (fr.json once lacked
    the whole config flow), and a stray key is dead weight hassfest rejects."""
    import re

    english = dict(_leaves(json.loads((COMPONENT / "translations" / "en.json").read_text())))
    placeholder = re.compile(r"\{[a-z_]+\}")

    files = sorted((COMPONENT / "translations").glob("*.json"))
    assert {path.stem for path in files} >= {"en", "fr", "cs"}
    for path in files:
        translated = dict(_leaves(json.loads(path.read_text(encoding="utf-8"))))
        assert set(translated) == set(english), (
            f"{path.name}: missing {sorted(set(english) - set(translated))}, "
            f"extra {sorted(set(translated) - set(english))}"
        )
        # A dropped or renamed {placeholder} raises KeyError when HA formats it.
        broken = [
            key
            for key, text in translated.items()
            if sorted(placeholder.findall(str(text)))
            != sorted(placeholder.findall(str(english[key])))
        ]
        assert not broken, f"{path.name}: placeholders differ in {broken}"


def test_fault_diagnosis_is_throttled():
    """Diagnosing costs a TCP connect and often a full read.

    2.2.0 ran it on every failed poll, so a charger that stayed broken was
    probed every 30 s forever. Only one diagnosis may happen per cooldown.
    """
    import asyncio
    import types

    from tuya_ev_charger.const import ConnectionFault
    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator

    coordinator = TuyaEVChargerDataUpdateCoordinator.__new__(TuyaEVChargerDataUpdateCoordinator)
    coordinator._last_fault = None
    coordinator._last_fault_at = 0.0
    calls: list[int] = []

    class _Client:
        async def async_classify_fault(self):
            calls.append(1)
            return ConnectionFault.REFUSED

    clock = {"now": 1000.0}
    coordinator.client = _Client()
    coordinator.hass = types.SimpleNamespace(loop=types.SimpleNamespace(time=lambda: clock["now"]))

    from tuya_ev_charger.const import FAULT_DIAGNOSIS_COOLDOWN_SECONDS

    start = clock["now"]

    # A 30 s poll interval, stopping short of the cooldown boundary.
    polls_inside = (FAULT_DIAGNOSIS_COOLDOWN_SECONDS // 30) - 1

    async def _run():
        first = await coordinator._async_classify_fault_throttled()
        for _ in range(polls_inside):
            clock["now"] += 30
            await coordinator._async_classify_fault_throttled()
        assert clock["now"] < start + FAULT_DIAGNOSIS_COOLDOWN_SECONDS
        return first

    verdict = asyncio.run(_run())
    assert verdict == ConnectionFault.REFUSED
    assert len(calls) == 1, f"diagnosed {len(calls)} times inside the cooldown"

    # Once the cooldown has elapsed it may diagnose again.
    clock["now"] = start + FAULT_DIAGNOSIS_COOLDOWN_SECONDS
    asyncio.run(coordinator._async_classify_fault_throttled())
    assert len(calls) == 2


def _bare_coordinator(**overrides):
    """A coordinator without HA, for the bookkeeping that needs no I/O."""
    import types

    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator

    coordinator = TuyaEVChargerDataUpdateCoordinator.__new__(TuyaEVChargerDataUpdateCoordinator)
    coordinator._last_fault = None
    coordinator._last_fault_at = 0.0
    coordinator._undecryptable_since = None
    coordinator._reconnect_tried = False
    coordinator._polls_ok = 0
    coordinator._polls_failed = 0
    coordinator._consecutive_failures = 0
    coordinator._last_success_at = None
    coordinator._last_failure_at = None
    coordinator._relocations = 0
    coordinator._key_refreshes = 0
    coordinator.last_discovery = None
    coordinator._base_interval_s = 30
    coordinator._release_until = None
    coordinator.regulating = False
    coordinator.update_interval = None
    coordinator.hass = types.SimpleNamespace()
    coordinator.entry = types.SimpleNamespace(entry_id="test")

    async def _connect():
        return None

    coordinator.client = types.SimpleNamespace(host="192.168.1.237", async_connect=_connect)
    for name, value in overrides.items():
        setattr(coordinator, name, value)
    return coordinator


def test_success_clears_the_cached_fault():
    """A stale verdict must not survive the charger coming back."""
    from tuya_ev_charger.const import ConnectionFault

    coordinator = _bare_coordinator(_last_fault=ConnectionFault.REFUSED, _last_fault_at=1000.0)

    coordinator._async_note_success()
    assert coordinator._last_fault is None


def test_a_transient_undecryptable_poll_does_not_trigger_reauth():
    """#34, #52: undecryptable polls are a handshake glitch or another client holding
    the charger's single connection until proven otherwise.

    ConfigEntryAuthFailed (the reauth banner) also stops Home Assistant polling, so
    it needs both several consecutive failures and a grace period; before that the
    poll just fails and the next one can recover on its own.
    """
    import asyncio
    import types

    from tuya_ev_charger.const import (
        UNDECRYPTABLE_FAILURES_BEFORE_REAUTH,
        UNDECRYPTABLE_GRACE_SECONDS,
        ConnectionFault,
    )
    from tuya_ev_charger.coordinator import ConfigEntryAuthFailed, UpdateFailed

    now = [5_000.0]
    coordinator = _bare_coordinator()
    coordinator.hass = types.SimpleNamespace(loop=types.SimpleNamespace(time=lambda: now[0]))

    async def _no_metrics():
        return None

    async def _no():
        return False

    async def _message():
        coordinator._last_fault = ConnectionFault.UNDECRYPTABLE
        return "undecryptable"

    coordinator._async_fetch_metrics = _no_metrics
    coordinator._async_try_rediscover_host = _no
    coordinator._async_try_refresh_local_key = _no
    coordinator._async_failure_message = _message

    # Far more polls than the threshold, all inside the grace period: still just failing.
    for i in range(1, 3 * UNDECRYPTABLE_FAILURES_BEFORE_REAUTH):
        with pytest.raises(UpdateFailed) as caught:
            asyncio.run(coordinator._async_update_data())
        assert not isinstance(caught.value, ConfigEntryAuthFailed)
        assert coordinator._consecutive_failures == i
        now[0] += 10

    # Unreadable for the whole grace period: now it really looks like a rotated key.
    now[0] += UNDECRYPTABLE_GRACE_SECONDS
    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(coordinator._async_update_data())

    # And a good poll wipes the streak, so a later blip starts from zero again.
    coordinator._async_note_success()
    assert coordinator._consecutive_failures == 0
    assert coordinator._undecryptable_since is None


def test_connection_health_starts_unknown_not_perfect():
    """Before the first poll there is no rate; 100% would be a lie."""
    coordinator = _bare_coordinator()
    assert coordinator.connection_health["success_rate_pct"] is None


def test_connection_health_counts_and_resets():
    """Consecutive failures are what distinguish a blip from an outage."""
    coordinator = _bare_coordinator()

    coordinator._async_note_success()
    coordinator._async_note_success()
    coordinator._polls_failed += 1
    coordinator._consecutive_failures += 1

    health = coordinator.connection_health
    assert health["polls_ok"] == 2
    assert health["polls_failed"] == 1
    assert health["success_rate_pct"] == 66.7
    assert health["consecutive_failures"] == 1

    coordinator._async_note_success()
    assert coordinator.connection_health["consecutive_failures"] == 0


def test_routine_poll_does_not_block_on_the_scan():
    """Listening for a broadcast takes seconds; the update loop must not wait.

    On a routine poll the relocation is scheduled in the background. The first
    refresh is the exception: setup rebuilds the client from the stored host on
    every retry, so an in-memory fix from a background task would be lost.
    """
    import asyncio
    import types

    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator

    def _make(data):
        c = TuyaEVChargerDataUpdateCoordinator.__new__(TuyaEVChargerDataUpdateCoordinator)
        c._last_rediscovery_at = 0.0
        c._relocating = None
        c.data = data
        c.hass = types.SimpleNamespace(loop=types.SimpleNamespace(time=lambda: 100_000.0))
        c.scheduled = False
        c._schedule_relocation = lambda: setattr(c, "scheduled", True)
        return c

    # Routine poll: scheduled, and the caller is not made to wait.
    routine = _make(object())
    assert asyncio.run(routine._async_try_rediscover_host()) is False
    assert routine.scheduled is True

    # First refresh: resolved inline instead.
    first = _make(None)
    first._async_relocate = lambda: _returns(True)
    assert asyncio.run(first._async_try_rediscover_host()) is True
    assert first.scheduled is False


async def _returns(value):
    return value


def test_manifest_and_hacs_declare_requirements():
    """An unstated minimum version means cryptic errors on older installs."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text())

    # runtime_data, _get_reconfigure_entry and async_update_reload_and_abort all
    # need a recent core.
    assert hacs["homeassistant"] >= "2024.11.0"
    assert manifest["integration_type"] == "device"

    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:]), "hassfest wants the rest alphabetical"


def test_icons_live_in_icons_json():
    """HA 2024.2+ declares icons in icons.json, not in entity code."""
    icons = json.loads((COMPONENT / "icons.json").read_text())
    assert icons["entity"]["sensor"]["status"]["default"].startswith("mdi:")

    for path in COMPONENT.glob("*.py"):
        source = path.read_text()
        assert 'icon="mdi:' not in source, f"{path.name} still hardcodes an icon"
        assert "_attr_icon" not in source, f"{path.name} still hardcodes an icon"


def test_translation_keys_are_valid_for_hassfest():
    """hassfest rejects keys outside [a-z0-9-_], and only in CI.

    The `evcc_status` sensor shipped with `A`/`B`/`C` state translations -- the
    IEC 61851 letters evcc consumes -- which made hassfest fail on every push
    for months. The values must stay uppercase for evcc, so the labels were
    dropped rather than the states renamed. This keeps the class of mistake from
    coming back through any other key.
    """
    import json
    import pathlib
    import re

    valid = re.compile(r"^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$")
    root = pathlib.Path(__file__).resolve().parents[1] / "custom_components/tuya_ev_charger"

    def _walk(node, trail):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            # Placeholders like {entity_name} are values, not keys; only keys
            # are constrained.
            assert valid.match(key), f"{trail}/{key}"
            _walk(value, f"{trail}/{key}")

    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        payload = json.loads((root / name).read_text(encoding="utf-8"))
        _walk(payload, name)


def test_every_options_form_kind_is_handled():
    """An unknown `kind` falls through to the free-text branch silently.

    That is how a price field would become a text box that accepts "0,16" and
    stores a string, so the set of kinds is pinned to what the builder handles.
    """
    from tuya_ev_charger.options_flow import _OPTIONS_FORM

    handled = {
        "bool",
        "entity",
        "boolean_entity",
        "int",
        "price",
        "text",
        "choice",
        "multiline",
    }
    assert {opt.kind for opt in _OPTIONS_FORM} <= handled


def test_prices_are_not_rounded_to_integers():
    """A 0.16 EUR tariff coerced through int() becomes 0: every session free."""
    from tuya_ev_charger.option_values import option_float

    assert option_float({"peak_price": 0.16}, "peak_price", 0.0) == 0.16
    assert option_float({"peak_price": "0.27"}, "peak_price", 0.0) == 0.27
    # Typed by hand, so junk must fall back rather than raise.
    assert option_float({"peak_price": "0,27"}, "peak_price", 0.0) == 0.0
    # A negative price is a real tariff and keeps its sign (it used to read as 0).
    assert option_float({"peak_price": -1}, "peak_price", 0.0) == -1.0
    assert option_float({}, "peak_price", 0.0) == 0.0


def test_the_tinytuya_floor_matches_between_manifest_and_tests():
    """Dependabot cannot see `manifest.json`, so the two drift silently.

    Its pip ecosystem watches requirements files and does not understand Home
    Assistant's manifest format. A dependency PR therefore bumps the test floor
    and leaves the *runtime* requirement — the only one users install — behind.
    That happened with the tinytuya 1.20 bump, which carried a session-key nonce
    fix for protocol 3.4/3.5 that would have reached nobody.
    """
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    runtime = next(req for req in manifest["requirements"] if req.startswith("tinytuya"))

    requirements = (REPO_ROOT / "requirements-test.txt").read_text()
    tested = next(
        line.strip() for line in requirements.splitlines() if line.strip().startswith("tinytuya")
    )

    assert runtime == tested, (
        f"manifest requires {runtime!r} but the suite tests against {tested!r}; "
        "bump manifest.json by hand — Dependabot cannot"
    )


def test_every_decision_reason_has_a_translated_state():
    """The reason sensor is an enum, so Home Assistant *rejects* a state outside
    its options list -- the entity would go unavailable rather than show an
    untranslated string. A new reason therefore has to arrive with its
    translations, and that is only enforceable here.
    """
    from tuya_ev_charger.charge_gates import DecisionReason

    reasons = {reason.value for reason in DecisionReason}

    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        payload = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        states = payload["entity"]["sensor"]["surplus_last_decision_reason"]["state"]
        missing = sorted(reasons - set(states))
        extra = sorted(set(states) - reasons)
        assert not missing, f"{name} is missing translations for {missing}"
        assert not extra, f"{name} translates reasons that no longer exist: {extra}"


def test_the_reason_sensor_declares_every_reason_as_an_option():
    """The options list is what Home Assistant validates the state against."""
    from tuya_ev_charger.charge_gates import DecisionReason
    from tuya_ev_charger.sensor import SURPLUS_CONTROLLER_SENSOR_DESCRIPTIONS

    description = next(
        item
        for item in SURPLUS_CONTROLLER_SENSOR_DESCRIPTIONS
        if item.key == "surplus_last_decision_reason"
    )
    assert set(description.options) == {reason.value for reason in DecisionReason}


def test_every_config_problem_has_a_translated_repair_notice():
    """An untranslated repair shows as a raw key in Settings > Repairs, which is
    worse than not raising it: the user sees an alarm they cannot read."""
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    problems = {problem.value for problem in ConfigProblem}

    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        payload = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        issues = payload.get("issues", {})
        missing = sorted(problems - set(issues))
        assert not missing, f"{name} is missing repair notices for {missing}"
        for key in problems:
            assert issues[key].get("title"), f"{name}: {key} has no title"
            assert issues[key].get("description"), f"{name}: {key} has no description"


def test_sections_do_not_change_the_stored_option_shape():
    """Collapsible sections nest the submitted values; storage must stay flat.

    Every reader -- the surplus settings, diagnostics, existing installations --
    expects flat keys. Letting the display grouping leak into storage would need
    a migration for a purely cosmetic change.
    """
    from tuya_ev_charger.options_flow import _flatten_sections

    nested = {
        "device": {"scan_interval": 30},
        "protection": {"max_house_power_w": 9200, "total_load_sensor_entity_id": ""},
        "surplus": {"surplus_mode_enabled": True},
    }
    assert _flatten_sections(nested) == {
        "scan_interval": 30,
        "max_house_power_w": 9200,
        "total_load_sensor_entity_id": "",
        "surplus_mode_enabled": True,
    }


def test_flattening_tolerates_an_unsectioned_payload():
    """Older Home Assistant frontends, or a service call, may submit flat."""
    from tuya_ev_charger.options_flow import _flatten_sections

    assert _flatten_sections({"scan_interval": 30}) == {"scan_interval": 30}


def _validated_submission(
    schema: vol.Schema,
    *,
    overrides: dict[tuple[str, str], object] | None = None,
    omit: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, dict]:
    """Build a nested options submission and run it *through the schema*.

    A test that hand-builds a nested dict and hands it straight to
    `async_step_init` skips the validation step Home Assistant's own flow
    manager performs first (`data_schema(user_input)`, before the step ever
    runs). That is exactly the step where both the 19.0 entity-selector bug
    and #30 (text fields) actually happened: a field whose schema still
    carries a voluptuous `default=` gets silently refilled from that stale
    default right here, before the flow's own clear-if-absent logic gets a
    chance to see the key was ever missing. `tests/conftest.py`'s `_Section`
    stub genuinely calls `self.schema(value)`, so routing through the real
    built schema (`flow._build_options_schema(...)`) reproduces this
    faithfully.

    Every field not named in `overrides`/`omit` is submitted at its current
    schema value if the field carries one (every "bool"/"int"/"choice"/
    "price" field always does), or omitted if it doesn't (every "entity"/
    "text"/"multiline" field, post-#30-fix) -- the same shape a real
    unmodified form submission has.
    """
    overrides = overrides or {}
    submission: dict[str, dict] = {}
    for section_marker, section_obj in schema.schema.items():
        name = str(section_marker)
        inner: dict[str, object] = {}
        for field_marker in section_obj.schema.schema:
            key = str(field_marker)
            if (name, key) in omit:
                continue
            if (name, key) in overrides:
                inner[key] = overrides[(name, key)]
                continue
            default = field_marker.default
            if default is vol.UNDEFINED:
                continue
            inner[key] = default()
        submission[name] = inner
    return schema(submission)


def test_options_flow_keeps_a_populated_entity_selector_alongside_numbers():
    """2.19.0 flattened the merge but left this loop reading the pre-flatten,
    still-nested dict, so every submit silently cleared every entity-selector
    pick while numeric fields in the same section were kept."""
    import asyncio
    import types

    from tuya_ev_charger.options_flow import TuyaEVChargerOptionsFlow

    entry = types.SimpleNamespace(data={}, options={}, entry_id="test")
    flow = TuyaEVChargerOptionsFlow(entry)
    flow.async_create_entry = lambda data: {"type": "create_entry", "data": data}

    schema = flow._build_options_schema(entry.options, computed={})
    submission = _validated_submission(
        schema,
        overrides={
            ("protection", "max_inverter_power_w"): 6000,
            ("protection", "total_load_sensor_entity_id"): "sensor.total_load",
        },
    )
    result = asyncio.run(flow.async_step_init(submission))

    assert result["data"]["max_inverter_power_w"] == 6000
    assert result["data"]["total_load_sensor_entity_id"] == "sensor.total_load"


def test_options_flow_still_clears_an_entity_selector_left_blank():
    """The fix for the regression above must not stop a genuinely cleared
    picker from being wiped -- only a populated one must survive."""
    import asyncio
    import types

    from tuya_ev_charger.options_flow import TuyaEVChargerOptionsFlow

    entry = types.SimpleNamespace(
        data={},
        options={"total_load_sensor_entity_id": "sensor.old"},
        entry_id="test",
    )
    flow = TuyaEVChargerOptionsFlow(entry)
    flow.async_create_entry = lambda data: {"type": "create_entry", "data": data}

    schema = flow._build_options_schema(entry.options, computed={})
    submission = _validated_submission(
        schema,
        overrides={("protection", "max_inverter_power_w"): 6000},
        omit=frozenset({("protection", "total_load_sensor_entity_id")}),
    )
    result = asyncio.run(flow.async_step_init(submission))

    assert result["data"]["total_load_sensor_entity_id"] == ""


def test_options_form_declares_no_defaults_on_text_fields():
    """The same #30 guard as `..._on_entity_pickers` above, for "text"/
    "multiline" options: a voluptuous default here is what let HA-core's own
    schema validation silently refill a field the user had just cleared."""
    import types

    from tuya_ev_charger.options_flow import OPTIONAL_TEXT_OPTIONS, TuyaEVChargerOptionsFlow

    flow = TuyaEVChargerOptionsFlow(types.SimpleNamespace(data={}, options={}, entry_id="test"))
    schema = flow._build_options_schema({}, computed={})

    text_markers = []
    for _section_marker, section_obj in schema.schema.items():
        text_markers += [
            marker for marker in section_obj.schema.schema if str(marker) in OPTIONAL_TEXT_OPTIONS
        ]

    assert len(text_markers) == len(OPTIONAL_TEXT_OPTIONS), (
        "every optional text/multiline field must still be reachable in the schema"
    )

    for marker in text_markers:
        assert marker.default is vol.UNDEFINED, (
            f"{marker} carries a default; HA-core would silently refill it with the "
            "stale stored value whenever the frontend omits a blanked field (#30)"
        )


def test_options_flow_clears_every_text_field_left_blank():
    """#30: clearing e.g. "Off-peak windows" and submitting left the previous
    value in place. Parametrized over `_OPTIONS_FORM` itself, not a hand-listed
    set of keys, so a future text/multiline field added to the form is covered
    automatically rather than needing a matching new test."""
    import asyncio
    import types

    from tuya_ev_charger.options_flow import _OPTIONS_FORM, TuyaEVChargerOptionsFlow

    stale = {
        "off_peak_windows": "01:00-08:00",
        "departure_time": "07:30",
        "vehicles": "Zoe, Kangoo",
        "charger_profile_json": '{"custom": true}',
    }
    for opt in _OPTIONS_FORM:
        if opt.kind not in ("text", "multiline"):
            continue

        entry = types.SimpleNamespace(data={}, options=dict(stale), entry_id="test")
        flow = TuyaEVChargerOptionsFlow(entry)
        flow.async_create_entry = lambda data: {"type": "create_entry", "data": data}

        schema = flow._build_options_schema(entry.options, computed={})
        submission = _validated_submission(schema, omit=frozenset({(opt.section, opt.key)}))
        result = asyncio.run(flow.async_step_init(submission))

        assert result["data"][opt.key] == "", (
            f"{opt.key} kept its stale value {stale.get(opt.key)!r} after being left blank"
        )


def test_every_option_belongs_to_a_declared_section():
    """A typo in `section=` would silently drop the field from the form."""
    from tuya_ev_charger.options_flow import _OPTIONS_FORM, _SECTION_ORDER

    for opt in _OPTIONS_FORM:
        assert opt.section in _SECTION_ORDER, f"{opt.key} is in no rendered section"


def test_the_sectioned_schema_still_serialises():
    """The 2.0.1 outage was a schema that validated but could not be serialised.

    Home Assistant converts the options schema to JSON to draw the form, so
    wrapping it in sections must not reintroduce that failure.
    """
    import types

    voluptuous_serialize = pytest.importorskip("voluptuous_serialize")

    from tuya_ev_charger.options_flow import TuyaEVChargerOptionsFlow

    flow = TuyaEVChargerOptionsFlow(types.SimpleNamespace(data={}, options={}, entry_id="test"))
    schema = flow._build_options_schema({}, computed={})

    def serializer(value):
        inner = getattr(value, "schema", None)
        if inner is not None and hasattr(value, "options"):
            return {"type": "expandable", "schema": [], "expanded": True}
        if hasattr(value, "serialize"):
            return value.serialize()
        if hasattr(value, "config"):  # selectors from the stub
            return {"selector": {}}
        return voluptuous_serialize.UNSUPPORTED

    assert voluptuous_serialize.convert(schema, custom_serializer=serializer)


def test_every_option_field_has_a_label_in_its_section():
    """Sections move the labels under `sections.<name>.data`.

    A field whose label stayed at the old flat path renders as a raw key like
    `max_inverter_power_w` in the form, which is how the grouping could quietly
    degrade the UI it was meant to improve.
    """
    from tuya_ev_charger.options_flow import _OPTIONS_FORM

    expected: dict[str, set[str]] = {}
    for opt in _OPTIONS_FORM:
        expected.setdefault(opt.section, set()).add(opt.key)

    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        payload = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        sections = payload["options"]["step"]["init"]["sections"]

        assert set(sections) == set(expected), f"{name}: section list disagrees with the form"
        for section_name, keys in expected.items():
            block = sections[section_name]
            assert block.get("name"), f"{name}: section {section_name} has no title"
            missing = sorted(keys - set(block.get("data", {})))
            assert not missing, f"{name}: {section_name} is missing labels for {missing}"


def test_the_flow_and_the_migration_agree_on_the_entry_version():
    """A flow writing v2 entries while the migration only knows v1 would make
    every freshly created entry unloadable."""
    from tuya_ev_charger.config_flow import TuyaEVChargerConfigFlow
    from tuya_ev_charger.const import CONFIG_ENTRY_VERSION

    assert TuyaEVChargerConfigFlow.VERSION == CONFIG_ENTRY_VERSION


def test_a_newer_entry_is_refused_rather_than_misread():
    """After a downgrade, an entry written by a later release may hold fields
    this version would misinterpret. Refusing shows a clear error; loading it
    anyway would fail somewhere less obvious."""
    import asyncio
    import types

    import tuya_ev_charger as integration

    entry = types.SimpleNamespace(version=99, title="test")
    assert asyncio.run(integration.async_migrate_entry(None, entry)) is False


def test_a_current_entry_migrates_cleanly():
    import asyncio
    import types

    import tuya_ev_charger as integration
    from tuya_ev_charger.const import CONFIG_ENTRY_VERSION

    entry = types.SimpleNamespace(version=CONFIG_ENTRY_VERSION, title="test")
    assert asyncio.run(integration.async_migrate_entry(None, entry)) is True


def test_every_session_anomaly_has_a_translated_repair_notice():
    """An untranslated anomaly would show as a raw key in Settings > Repairs."""
    from tuya_ev_charger.session_anomaly import SessionAnomaly

    anomalies = {a.value for a in SessionAnomaly}
    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        payload = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        issues = payload.get("issues", {})
        missing = sorted(anomalies - set(issues))
        assert not missing, f"{name} is missing anomaly notices for {missing}"
        for key in anomalies:
            assert issues[key].get("title"), f"{name}: {key} has no title"
            assert issues[key].get("description"), f"{name}: {key} has no description"


# --- a lent connection must come back (audit B1) ---------------------------


class _FakeDevice:
    """Stands in for tinytuya.Device: just enough surface for the client."""

    built: ClassVar[list[_FakeDevice]] = []

    def __init__(self, *args, **kwargs):
        self.closed = False
        _FakeDevice.built.append(self)

    def status(self):
        return {"dps": {"101": "charging"}}

    def close(self):
        self.closed = True

    def set_socketTimeout(self, *_):
        pass

    def set_socketRetryLimit(self, *_):
        pass

    def set_socketRetryDelay(self, *_):
        pass


def _lent_connection(monkeypatch):
    """A real client behind a coordinator with a simulated clock."""
    import types

    from tuya_ev_charger import tuya_ev_charger as client_module
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    _FakeDevice.built = []
    monkeypatch.setattr(client_module.tinytuya, "Device", _FakeDevice)
    client = TuyaEVChargerClient("id", "192.0.2.1", "key", "3.4")
    clock = {"now": 1000.0}
    coordinator = _bare_coordinator(
        client=client,
        hass=types.SimpleNamespace(loop=types.SimpleNamespace(time=lambda: clock["now"])),
        entry=types.SimpleNamespace(title="test", entry_id="test"),
    )
    coordinator.clock = clock
    return coordinator, client


def test_polling_reconnects_when_a_lent_connection_expires(monkeypatch):
    """release_connection closed the client for good and nothing rebuilt it, so
    every poll after the loan raised until the integration was reloaded."""
    import asyncio

    coordinator, client = _lent_connection(monkeypatch)

    async def _run():
        await client.async_connect()
        await coordinator.async_release_connection(600)
        coordinator.clock["now"] += 601
        assert await coordinator._async_handle_release() is None
        return await client.async_get_raw_dps()

    assert asyncio.run(_run()) == {"101": "charging"}


def test_resuming_a_lent_connection_early_reconnects(monkeypatch):
    """The release service with 0 minutes takes the slot back before the loan ends."""
    import asyncio

    coordinator, client = _lent_connection(monkeypatch)

    async def _read():
        return await client.async_get_raw_dps()

    async def _fetch():
        return await _read()

    async def _noop(*_):
        return None

    coordinator._async_fetch_metrics = _fetch
    coordinator._async_note_success = lambda *_: None
    coordinator._async_on_metrics = _noop
    coordinator._async_retune_interval = lambda *_: None

    async def _run():
        await client.async_connect()
        await coordinator.async_release_connection(600)
        coordinator.async_resume_connection()
        return await coordinator._async_update_data()

    assert asyncio.run(_run()) == {"101": "charging"}


def test_a_write_does_not_take_back_a_lent_connection(monkeypatch):
    """The socket belongs to Smart Life during the loan: only the coordinator,
    once the loan ends, may reopen it."""
    import asyncio

    coordinator, client = _lent_connection(monkeypatch)

    async def _run():
        await client.async_connect()
        await coordinator.async_release_connection(600)
        built = len(_FakeDevice.built)
        with pytest.raises(RuntimeError):
            await client.async_set_charge_enabled(True)
        return built

    built = asyncio.run(_run())
    assert len(_FakeDevice.built) == built


# --- a refused current write is reported (audit B2) ---------------------------------


@pytest.mark.parametrize("decided", ["ADJUST_CURRENT", "LOAD_LIMIT_REDUCED"])
def test_a_refused_current_write_is_reported_not_the_decided_reason(decided):
    """`_async_apply` ignored the False from the current write, so the sensor
    kept saying `adjust_current` / `load_limit_reduced` while the charger had
    refused the value."""
    import asyncio
    import types

    from tuya_ev_charger import solar_surplus
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction, Verdict

    class _Client:
        async def async_set_charge_current(self, value):
            return False

    class _Coordinator:
        refreshes = 0
        regulating = False

        async def async_request_refresh(self):
            self.refreshes += 1

    coordinator = _Coordinator()
    entry = types.SimpleNamespace(options={}, title="t", entry_id="e1")
    hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda _e: None))
    controller = solar_surplus.SolarSurplusController(
        hass=hass, entry=entry, client=_Client(), coordinator=coordinator
    )
    reason = DecisionReason[decided]
    verdict = Verdict(GateAction.SET_CURRENT, reason, target_current=16, regulation_active=True)

    asyncio.run(controller._async_apply(verdict, data=None, now=1000.0))

    assert controller._last_decision_reason == "failed_set_current"
    assert controller._last_decision_trace["decided_reason"] == str(reason)
    assert coordinator.refreshes == 0


# --- controller state survives a restart (audit B4) ---


class _MemoryStore:
    """A `Store` stand-in shared by two controllers = a restart in between."""

    def __init__(self):
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data

    def async_delay_save(self, data_func, delay=0):
        self.data = data_func()


def _restarted(monkeypatch, store, **kwargs):
    """A fresh controller on the same store, restored the way setup does."""
    import asyncio

    from test_surplus_state_machine import Harness

    h = Harness(monkeypatch, **kwargs)
    h.controller._store = store
    asyncio.run(h.controller.async_restore())
    return h


def _charging_metrics(**kwargs):
    from test_surplus_state_machine import _metrics

    metrics = _metrics(charging=True, current_target=10, total_power_kw=2.3, **kwargs)
    metrics.status = "charging"
    return metrics


def test_a_pause_still_stops_a_surplus_session_after_a_restart(monkeypatch):
    import asyncio

    store = _MemoryStore()
    first = _restarted(monkeypatch, store, metrics=_charging_metrics())
    first.controller._start_session(first.now)

    second = _restarted(monkeypatch, store, metrics=_charging_metrics())
    asyncio.run(second.controller.async_pause_for(duration_s=600))
    second.tick()
    assert ("enabled", False) in second.writes


def test_force_charge_survives_a_restart(monkeypatch):
    import asyncio

    store = _MemoryStore()
    first = _restarted(monkeypatch, store)
    asyncio.run(first.controller.async_force_charge_for(3600))

    second = _restarted(monkeypatch, store)
    assert second.controller.snapshot.force_charge_active is True


def test_session_energy_survives_a_restart(monkeypatch):
    store = _MemoryStore()
    first = _restarted(monkeypatch, store, metrics=_charging_metrics())
    first.controller._start_session(first.now)
    first.tick(3600)  # 2.3 kW for an hour
    energy = first.controller._session_energy_kwh
    assert energy > 2.0

    second = _restarted(monkeypatch, store, metrics=_charging_metrics())
    assert second.controller._session_energy_kwh == pytest.approx(energy)


def test_a_restored_session_is_dropped_when_no_vehicle_is_plugged_in(monkeypatch):
    from test_surplus_state_machine import _metrics

    store = _MemoryStore()
    first = _restarted(monkeypatch, store, metrics=_charging_metrics())
    first.controller._start_session(first.now)
    first.controller._session_energy_kwh = 5.0
    first.controller._schedule_save()

    unplugged = _metrics(charging=False)
    unplugged.status = None
    second = _restarted(monkeypatch, store, metrics=unplugged)
    assert second.controller._session_active is True
    second.tick()
    assert second.controller._session_active is False
    assert second.controller._session_energy_kwh == 0.0


def test_shutdown_writes_the_state(monkeypatch):
    import asyncio

    store = _MemoryStore()
    h = _restarted(monkeypatch, store)
    h.controller._pause_until_ts = h.now + 600
    asyncio.run(h.controller.async_shutdown())
    assert store.data is not None
    assert store.data["pause_until"] is not None
