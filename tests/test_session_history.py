"""The rolling session log.

The charger has no session id, so "is this a session I have already logged?" is
answered by comparing (duration, energy). Getting that wrong logs the same
session on every poll, or invents one at every restart.
"""

from __future__ import annotations

import asyncio

import pytest


class _MemoryStore:
    def __init__(self, *args, **kwargs):
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


@pytest.fixture
def history(monkeypatch):
    from tuya_ev_charger import session_history

    monkeypatch.setattr(session_history, "Store", _MemoryStore)
    instance = session_history.SessionHistory(hass=None, entry_id="test")
    asyncio.run(instance.async_load())
    return instance


def _record(**kwargs):
    from tuya_ev_charger.session_history import SessionRecord

    base = {
        "ended_at": "2026-07-22T05:30:00",
        "duration_s": 7200,
        "energy_kwh": 14.8,
        "off_peak_minutes": 120,
        "peak_minutes": 0,
    }
    base.update(kwargs)
    return SessionRecord(**base)


def test_a_new_session_is_recognised(history):
    assert history.is_new_session(7200, 14.8) is True


def test_the_same_session_is_not_logged_twice(history):
    """DP 105 is re-read on every poll; without this it would log every 30 s."""
    asyncio.run(history.async_record(_record()))
    assert history.is_new_session(7200, 14.8) is False


def test_a_different_session_after_one_already_logged(history):
    asyncio.run(history.async_record(_record()))
    assert history.is_new_session(3600, 7.4) is True


@pytest.mark.parametrize(
    ("duration", "energy"),
    [(None, 14.8), (7200, None), (None, None), (0, 0.0)],
)
def test_an_absent_or_empty_record_is_not_a_session(history, duration, energy):
    """Chargers that have never charged report zeros, not absence."""
    assert history.is_new_session(duration, energy) is False


def test_a_pre_existing_record_can_be_accepted_without_logging(history):
    """After a restart the charger's last session may be weeks old."""
    asyncio.run(history.async_note_seen(7200, 14.8))
    assert history.is_new_session(7200, 14.8) is False
    assert history.sessions == []


def test_newest_first(history):
    asyncio.run(history.async_record(_record(energy_kwh=1.0)))
    asyncio.run(history.async_record(_record(energy_kwh=2.0)))
    assert [s["energy_kwh"] for s in history.sessions] == [2.0, 1.0]
    assert history.latest["energy_kwh"] == 2.0


def test_the_log_is_bounded(history):
    from tuya_ev_charger.session_history import MAX_SESSIONS

    for index in range(MAX_SESSIONS + 10):
        asyncio.run(history.async_record(_record(energy_kwh=float(index))))

    assert len(history.sessions) == MAX_SESSIONS
    # The oldest went, not the newest.
    assert history.sessions[0]["energy_kwh"] == float(MAX_SESSIONS + 9)


def test_totals(history):
    asyncio.run(history.async_record(_record(energy_kwh=10.0, cost=2.0)))
    asyncio.run(history.async_record(_record(energy_kwh=5.0, cost=1.25)))
    assert history.total_energy_kwh() == 15.0
    assert history.total_cost() == 3.25


def test_unpriced_sessions_do_not_dilute_the_total(history):
    """Sessions logged before a price was configured have cost None."""
    asyncio.run(history.async_record(_record(cost=None)))
    assert history.total_cost() is None

    asyncio.run(history.async_record(_record(cost=2.0)))
    assert history.total_cost() == 2.0


def test_everything_survives_a_restart(history, monkeypatch):
    from tuya_ev_charger import session_history

    asyncio.run(history.async_record(_record(energy_kwh=9.0, cost=1.5)))

    store = history._store
    monkeypatch.setattr(session_history, "Store", lambda *a, **kw: store)
    revived = session_history.SessionHistory(hass=None, entry_id="test")
    asyncio.run(revived.async_load())

    assert revived.total_energy_kwh() == 9.0
    # Crucially the signature survives too, or the restart re-logs the session.
    assert revived.is_new_session(7200, 9.0) is False
