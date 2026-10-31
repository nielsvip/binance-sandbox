from __future__ import annotations

import tradier_manage
from vec_decisions.wirem_dd_bounce import live_trigger_values
from vec_decisions.tradier_sim_clock import patch_tradier_sim_time


class _Clock:
    def __init__(self, ts):
        self.ts = float(ts)

    def time(self):
        return self.ts


def _candidate(now, last_aug_ts):
    return live_trigger_values(
        True,
        now,
        last_aug_ts,
        4.0,
        101.0,
        10.0,
        100.0,
        True,
        True,
        True,
        True,
        11.0,
        10.0,
        8.0,
        7.0,
    )


def test_tradier_dd_bounce_uses_sim_clock_and_four_hour_bar_interval(monkeypatch):
    original_time = tradier_manage.time
    first_fire_ts = 1_800_000_000.0
    clock = _Clock(first_fire_ts + 4 * 3600 - 1)
    monkeypatch.setattr(tradier_manage, "time", original_time)

    patch_tradier_sim_time(clock, "tradier")
    assert tradier_manage.time.time() == first_fire_ts + 4 * 3600 - 1
    assert _candidate(tradier_manage.time.time(), first_fire_ts) == (None, 0.0)

    clock.ts += 1
    assert _candidate(tradier_manage.time.time(), first_fire_ts) == ("D", 11.0)


def test_tradier_sim_clock_patch_is_mode_scoped(monkeypatch):
    original_time = tradier_manage.time
    clock = _Clock(123.0)
    patch_tradier_sim_time(clock, "crypto")
    assert tradier_manage.time is original_time

