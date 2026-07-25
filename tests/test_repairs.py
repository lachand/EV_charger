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
