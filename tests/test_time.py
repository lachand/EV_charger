"""The schedule start/end time entities.

Each writes the whole schedule, preserving the other end and the enabled flag, so
setting one time must not clear the other.
"""

from __future__ import annotations

import asyncio
import types
from datetime import time


class _Client:
    def __init__(self, *, ok=True):
        self.calls: list[tuple] = []
        self._ok = ok

    async def async_set_schedule(self, enabled, start, end):
        self.calls.append((enabled, start, end))
        return self._ok


def _time_entity(cls, *, data=None, client=None):
    entity = cls.__new__(cls)
    refreshes: list[int] = []

    async def _refresh():
        refreshes.append(1)

    entity.coordinator = types.SimpleNamespace(data=data, async_request_refresh=_refresh)
    entity._runtime_data = types.SimpleNamespace(client=client or _Client())
    entity.refreshes = refreshes
    return entity


def _metrics(**kwargs):
    base = {"schedule_start": "22:00", "schedule_end": "06:00", "schedule_enabled": True}
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def test_start_time_reads_the_stored_value():
    from tuya_ev_charger.time import TuyaEVChargerScheduleStartTime as T

    entity = _time_entity(T, data=_metrics(schedule_start="22:30"))
    assert entity.native_value == time(22, 30)


def test_setting_the_start_time_preserves_the_end_and_enabled_flag():
    """Writing one end of the window must carry the other over, not blank it."""
    from tuya_ev_charger.time import TuyaEVChargerScheduleStartTime as T

    client = _Client()
    entity = _time_entity(
        T, data=_metrics(schedule_end="07:00", schedule_enabled=True), client=client
    )
    asyncio.run(entity.async_set_value(time(23, 0)))
    assert client.calls == [(True, "23:00", "07:00")]


def test_setting_the_end_time_preserves_the_start():
    from tuya_ev_charger.time import TuyaEVChargerScheduleEndTime as T

    client = _Client()
    entity = _time_entity(T, data=_metrics(schedule_start="21:00"), client=client)
    asyncio.run(entity.async_set_value(time(5, 30)))
    assert client.calls == [(True, "21:00", "05:30")]


def test_a_failed_schedule_write_raises():
    import pytest
    from tuya_ev_charger.time import HomeAssistantError
    from tuya_ev_charger.time import TuyaEVChargerScheduleStartTime as T

    entity = _time_entity(T, data=_metrics(), client=_Client(ok=False))
    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_value(time(23, 0)))


def test_the_time_is_none_before_the_first_poll():
    from tuya_ev_charger.time import TuyaEVChargerScheduleStartTime as T

    assert _time_entity(T, data=None).native_value is None
