"""SESSION_OPEN_STOP_REFREEZE (USER 2026-10-13, stocks): first RTH bar of each ET date re-freezes a carried position's stop."""
import calendar
import time

import numpy as np

from vec_decisions.fixed_stop_session import session_open_mask


def _ts(s):
    return float(calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M")))


def test_first_rth_bar_of_each_date_only():
    # 13:30Z = 09:30 ET (EDT); 20:00Z = 16:00 ET. Two sessions + pre-market + after-hours bars.
    bars = ["2026-10-12 13:15", "2026-10-12 13:30", "2026-10-12 13:45", "2026-10-12 19:45", "2026-10-12 20:00",
            "2026-10-13 13:00", "2026-10-13 13:30", "2026-10-13 13:45"]
    m = session_open_mask(np.array([_ts(b) for b in bars]), 15.0)
    assert m.tolist() == [False, True, False, False, False, False, True, False]


def test_weekend_has_no_session_open():
    bars = ["2026-10-17 13:30", "2026-10-18 13:30", "2026-10-19 13:30"]  # Sat, Sun, Mon
    assert session_open_mask(np.array([_ts(b) for b in bars]), 15.0).tolist() == [False, False, True]


def test_switch_defaults_off_everywhere():
    import config_tradier
    from v12_quick_engine import QuickConfig

    assert config_tradier.TradierConfig().SESSION_OPEN_STOP_REFREEZE_ENABLED is False
    assert QuickConfig().SESSION_OPEN_STOP_REFREEZE_ENABLED is False
