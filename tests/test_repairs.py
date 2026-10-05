"""The repair-issue helpers and the tidy-entities fix flow.

These decide which repair the user is offered and, for the fixable one, what
happens when they accept -- disabling advanced entities they did not choose.
"""

from __future__ import annotations

import asyncio


def test_a_config_problem_is_raised_and_the_resolved_ones_cleared(monkeypatch):
    """The sync raises every active problem and clears the rest, so a fixed
    setting drops its notice without a restart."""
    from tuya_ev_charger import repairs
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    raised, cleared = [], []
    monkeypatch.setattr(repairs, "async_raise", lambda h, e, k, **kw: raised.append(k))
    monkeypatch.setattr(repairs, "async_clear", lambda h, e, k: cleared.append(k))

    active = [ConfigProblem.SURPLUS_WITHOUT_SENSOR.value]
    repairs.async_sync_config_problems(None, "e1", active)

    assert ConfigProblem.SURPLUS_WITHOUT_SENSOR.value in raised
    # Every other problem is explicitly cleared, not left hanging.
    assert ConfigProblem.LOAD_LIMIT_WITHOUT_SENSOR.value in cleared


def test_an_anomaly_is_raised_and_the_resolved_ones_cleared(monkeypatch):
    from tuya_ev_charger import repairs
    from tuya_ev_charger.session_anomaly import SessionAnomaly

    raised, cleared = [], []
    monkeypatch.setattr(repairs, "async_raise", lambda h, e, k, **kw: raised.append(k))
    monkeypatch.setattr(repairs, "async_clear", lambda h, e, k: cleared.append(k))

    repairs.async_sync_session_anomalies(
        None, "e1", [SessionAnomaly.CHARGING_SLOWER_THAN_USUAL.value]
    )

    assert SessionAnomaly.CHARGING_SLOWER_THAN_USUAL.value in raised
    assert SessionAnomaly.REPEATED_SHORT_SESSIONS.value in cleared


def test_the_tidy_flow_is_offered_only_for_its_own_issue():
    from tuya_ev_charger.repairs import (
        ISSUE_TIDY_ENTITIES,
        TidyEntitiesFlow,
        async_create_fix_flow,
    )

    tidy = asyncio.run(async_create_fix_flow(None, f"{ISSUE_TIDY_ENTITIES}_e1", {"entry_id": "e1"}))
    assert isinstance(tidy, TidyEntitiesFlow)

    # Any other issue gets a plain confirm flow, not the entity-disabling one.
    other = asyncio.run(async_create_fix_flow(None, "connection_refused_e1", None))
    assert not isinstance(other, TidyEntitiesFlow)


def test_accepting_the_tidy_flow_disables_the_advanced_entities(monkeypatch):
    """Accepting must actually disable them; the whole point is a one-click tidy."""
    from tuya_ev_charger import entity_cleanup
    from tuya_ev_charger.repairs import TidyEntitiesFlow

    disabled = {}

    async def _disable(hass, entry_id, keys, reason):
        disabled["keys"] = keys
        return 7

    monkeypatch.setattr(entity_cleanup, "async_disable_entities", _disable)

    flow = TidyEntitiesFlow(entry_id="e1")
    flow.hass = None
    flow.async_create_entry = lambda title, data: {"data": data}
    result = asyncio.run(flow.async_step_confirm({}))
    assert result["data"]["disabled"] == 7
    assert disabled["keys"], "no entities were passed to be disabled"


# --- creating and clearing the issues themselves ---------------------------------------


class _Issues:
    def __init__(self):
        self.created: list[dict] = []
        self.deleted: list[tuple] = []
        self.IssueSeverity = type("S", (), {"WARNING": "warning"})

    def async_create_issue(self, hass, domain, issue_id, **kwargs):
        self.created.append({"domain": domain, "issue_id": issue_id, **kwargs})

    def async_delete_issue(self, hass, domain, issue_id):
        self.deleted.append((domain, issue_id))


def _issues(monkeypatch):
    from tuya_ev_charger import repairs

    recorder = _Issues()
    monkeypatch.setattr(repairs, "ir", recorder)
    return repairs, recorder


def test_a_raised_issue_is_per_entry_and_not_fixable(monkeypatch):
    repairs, issues = _issues(monkeypatch)

    repairs.async_raise(None, "e1", "connection_refused", translation_placeholders={"host": "h"})

    (issue,) = issues.created
    assert issue["domain"] == "tuya_ev_charger"
    assert issue["issue_id"] == "connection_refused_e1"
    assert issue["is_fixable"] is False
    assert issue["translation_key"] == "connection_refused"
    assert issue["translation_placeholders"] == {"host": "h"}


def test_two_entries_never_share_an_issue(monkeypatch):
    repairs, issues = _issues(monkeypatch)

    repairs.async_raise(None, "e1", "connection_refused")
    repairs.async_raise(None, "e2", "connection_refused")

    assert issues.created[0]["issue_id"] != issues.created[1]["issue_id"]


def test_clearing_deletes_that_entrys_issue(monkeypatch):
    repairs, issues = _issues(monkeypatch)

    repairs.async_clear(None, "e1", "connection_refused")

    assert issues.deleted == [("tuya_ev_charger", "connection_refused_e1")]


def test_the_tidy_offer_is_fixable_and_says_how_many(monkeypatch):
    repairs, issues = _issues(monkeypatch)

    repairs.async_offer_entity_cleanup(None, "e1", 4)

    (issue,) = issues.created
    assert issue["is_fixable"] is True
    assert issue["translation_placeholders"] == {"count": "4"}
    assert issue["data"] == {"entry_id": "e1"}


def test_the_tidy_flow_shows_a_confirmation_before_doing_anything():
    from tuya_ev_charger.repairs import TidyEntitiesFlow

    flow = TidyEntitiesFlow(entry_id="e1")
    flow.async_show_form = lambda **kw: {"type": "form", **kw}

    result = asyncio.run(flow.async_step_init())

    assert result["type"] == "form"
    assert result["step_id"] == "confirm"
