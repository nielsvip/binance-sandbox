"""Twin fidelity for vec_decisions.live_native_exit_twins against the live branches in ez_manage.py.

The reference functions below are line-for-line transcriptions of ez_manage.py process_position
(DC15M_FORCE_EXIT ~60468-60500 and GAIN-TIERED PROFIT HARVEST ~60546-60592, md5 1a15f3c0 on
2026-10-27). They prove predicate equality on a fixture grid; they are NOT backtest proof.
"""
import itertools

import pytest

from vec_decisions import live_native_exit_twins as T


def _sff(v, d):
    try:
        return d if v is None else float(v)
    except (TypeError, ValueError):
        return d


def live_in_gain(current_gain, is_long, i, ha_15m, k_15m, d_15m, is_trend_acct, recently_reduced):
    if current_gain > 2.0 and not is_trend_acct and not recently_reduced:
        _k_1h = _sff(i.get("k_1h", i.get("k_1h")), 50.0)
        _d_1h = _sff(i.get("d_1h", i.get("d_1h")), 50.0)
        _ha_1h = i.get("ha_1h", i.get("ha_color_1h", ""))
        if current_gain >= 10.0:
            _harvest_ok = (is_long and _k_1h < _d_1h and str(_ha_1h).lower() == "red") or (not is_long and _k_1h > _d_1h and str(_ha_1h).lower() == "green")
        elif current_gain >= 5.0:
            _harvest_ok = (is_long and ha_15m == "red" and k_15m < d_15m) or (not is_long and ha_15m == "green" and k_15m > d_15m)
        else:
            _harvest_ok = False
        return _harvest_ok
    return False


def live_dc15(is_long, current_price, dc_low_15m, dc_high_15m, current_gain, delta_exit_on):
    fell_through_floor = (is_long and dc_low_15m > 0 and current_price < dc_low_15m) or (not is_long and dc_high_15m > 0 and current_price > dc_high_15m)
    return bool(fell_through_floor and current_gain < 0.0 and delta_exit_on)


GAINS = (-1.0, 1.9, 2.0, 2.1, 4.99, 5.0, 7.0, 9.99, 10.0, 15.0)
COLORS = ("red", "green", "neutral")
KD = ((30.0, 60.0), (60.0, 30.0), (50.0, 50.0))


@pytest.mark.parametrize("is_long", (True, False))
def test_in_gain_trend_matches_live(is_long):
    n = 0
    for gain, ha1, ha15, (k1, d1), (k15, d15), trend, rr in itertools.product(GAINS, COLORS, COLORS, KD, KD, (False, True), (False, True)):
        ind = {"k_1h": k1, "d_1h": d1, "ha_1h": ha1, "ha_15m": ha15, "k_15m": k15, "d_15m": d15}
        want = live_in_gain(gain, is_long, ind, ha15, k15, d15, trend, rr)
        got, reason = T.in_gain_trend_exit(lambda k, d: d, lambda k, d: ind.get(k, d), {"gain": gain, "trend_account": trend, "recently_reduced": rr}, is_long)
        assert got == want, (gain, ha1, ha15, k1, d1, k15, d15, trend, rr)
        assert (reason.startswith("IN_GAIN_TREND_EXIT_")) == got
        n += 1
    assert n == len(GAINS) * 3 * 3 * 3 * 3 * 4


def test_in_gain_trend_reads_npz_int8_ha():
    npz_bar = {"k_1h": 30.0, "d_1h": 60.0, "ha_1h": -1, "ha_15m": 1, "k_15m": 50.0, "d_15m": 50.0}
    assert T.in_gain_trend_exit(lambda k, d: d, lambda k, d: npz_bar.get(k, d), {"gain": 12.0}, True) == (True, "IN_GAIN_TREND_EXIT_BIG_WINNER_HTF")
    npz_bar["ha_1h"] = 1
    assert T.in_gain_trend_exit(lambda k, d: d, lambda k, d: npz_bar.get(k, d), {"gain": 12.0}, True)[0] is False


@pytest.mark.parametrize("is_long", (True, False))
def test_dc15_force_exit_matches_live(is_long):
    # 2026-10-28 (USER "DC15M_FORCE_EXIT_ENABLED ... tested under the same name in stocks and crypto"): the switch is its own
    # authority now, DELTA_EXIT_ENABLED no longer co-gates it (False on stocks -> the row would be a structural zero).
    # The floor/red part is still the 10-27 live formula; the live branch now calls this same function.
    for px, lo, hi, gain, on in itertools.product((9.0, 10.0, 11.0, 12.0), (0.0, 10.0), (0.0, 11.0), (-0.5, 0.0, 0.5), (True, False)):
        want = live_dc15(is_long, px, lo, hi, gain, True)
        ind = {"dc_low_15m": lo, "dc_high_15m": hi}
        got, reason = T.dc15m_force_exit(lambda k, d: on if k == "DELTA_EXIT_ENABLED" else d, lambda k, d: ind.get(k, d), {"px": px, "gain": gain}, is_long)
        assert got == want, (px, lo, hi, gain, on)
        assert (reason == "DELTA_EXIT_DC15M_FLOOR_BREAK") == got


def test_defaults_are_inert():
    assert T.active_exits(lambda k, d: d) == []
    on = {"DC15M_FORCE_EXIT_ENABLED": True, "IN_GAIN_TREND_EXIT_ENABLED": True}
    assert T.active_exits(lambda k, d: on.get(k, d)) == [T.dc15m_force_exit, T.in_gain_trend_exit]
