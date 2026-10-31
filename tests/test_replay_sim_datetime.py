from datetime import datetime, timezone

from dateutil.parser import isoparse

from tools.replay_clock import sim_datetime_class


def test_sim_datetime_accepts_parser_base_type_and_uses_sim_clock(monkeypatch):
    sim_ts = 1_793_413_800.0
    sim_now = lambda tz=None: datetime.fromtimestamp(sim_ts, tz=timezone.utc).astimezone(tz or timezone.utc)
    sim_utcnow = lambda: datetime.utcfromtimestamp(sim_ts)
    sim_datetime = sim_datetime_class(datetime, sim_now, sim_utcnow)
    parsed = isoparse("2026-10-31T02:30:00.000000Z")
    sim_now = sim_datetime.now(timezone.utc)
    assert type(parsed) is datetime
    assert isinstance(parsed, sim_datetime)
    assert sim_now.timestamp() == sim_ts
    assert (sim_now - parsed).total_seconds() == 0


def test_sim_datetime_keeps_real_staleness_visible(monkeypatch):
    sim_ts = 1_793_413_800.0
    sim_now = lambda tz=None: datetime.fromtimestamp(sim_ts, tz=timezone.utc).astimezone(tz or timezone.utc)
    sim_datetime = sim_datetime_class(datetime, sim_now, lambda: datetime.utcfromtimestamp(sim_ts))
    stale = isoparse("2026-10-31T02:29:50.000000Z")
    age = (sim_datetime.now(timezone.utc) - stale).total_seconds()
    assert age == 10
    assert age > 3
