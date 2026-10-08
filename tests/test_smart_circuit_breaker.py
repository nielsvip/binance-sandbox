"""Focused unit tests for SmartCircuitBreaker (ez_manage.py) — USER 2026-10-08 unlock.

Pins the 2026-10-08 fix: two-token path tags (one token silenced whole families),
distinct-position counting (one chronic loser re-diagnosed on every OPEN used to
disable a path alone), and 24h decay with strike purge (disables never cleared).
Also pins the untouched half: bad-entry criteria and the HTF_EXHAUST exit.
"""
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ez_manage import SmartCircuitBreaker, _cb_path_tag  # noqa: E402

TFS = ["3m", "15m", "1h", "4h", "D"]


def _ind(wt_against, dc_wrong, is_long=True):
    """indicators dict with WT against (wt1<wt2 for LONG) on the first wt_against TFs
    and DC away from the buy extreme on the first dc_wrong TFs."""
    d = {}
    for i, tf in enumerate(TFS):
        if is_long:
            d[f"wt1_{tf}"], d[f"wt2_{tf}"] = (-5.0, 5.0) if i < wt_against else (5.0, -5.0)
            d[f"dc_position_{tf}"] = 0.9 if i < dc_wrong else 0.1
        else:
            d[f"wt1_{tf}"], d[f"wt2_{tf}"] = (5.0, -5.0) if i < wt_against else (-5.0, 5.0)
            d[f"dc_position_{tf}"] = 0.1 if i < dc_wrong else 0.9
    return d


def _cb(tmp_path):
    cb = SmartCircuitBreaker()
    cb._log_path = tmp_path / "cb.jsonl"
    return cb


def _losing(gain=-2.0, amt=1.0):
    return SimpleNamespace(positionAmt=amt, gain=gain)


def test_tag_two_token_granularity():
    assert _cb_path_tag("B_KZONE |VEC_EXACT") == "B_KZONE |VEC"
    assert _cb_path_tag("B_MOM5 |VEC_EXACT") == "B_MOM5 |VEC"
    assert _cb_path_tag("ENTRY_BREAKOUT_CHANNEL_15m") == "ENTRY_BREAKOUT"
    assert _cb_path_tag("ENTRY_PULLBACK_X") == "ENTRY_PULLBACK"
    assert _cb_path_tag("DC_BREAKOUT_ENTRY |VEC_EXACT") == "DC_BREAKOUT"
    assert _cb_path_tag("B12 |VEC_EXACT") == "B12 |VEC_EXACT"
    assert _cb_path_tag("SCALPV3") == "SCALPV3"


def test_single_loser_cannot_disable(tmp_path):
    cb = _cb(tmp_path)
    cb.record_entry("inf:AUSDT_LONG", 1.0, "B_KZONE |VEC_EXACT", indicators=_ind(4, 4))
    pos = {"inf:AUSDT_LONG": _losing()}
    for _ in range(5):
        assert cb.check_and_update(pos, get_indicators_fn=lambda s: {}) == []
    assert not cb.is_blocked("B_KZONE |VEC_EXACT")


def test_three_distinct_bad_disable_only_that_subpath(tmp_path):
    cb = _cb(tmp_path)
    pos = {}
    for sym in ("AUSDT", "BUSDT", "CUSDT"):
        pk = f"inf:{sym}_LONG"
        cb.record_entry(pk, 1.0, "B_KZONE |VEC_EXACT", indicators=_ind(4, 4))
        pos[pk] = _losing()
    cb.check_and_update(pos, get_indicators_fn=lambda s: {})
    assert cb.is_blocked("B_KZONE |VEC_EXACT")
    assert cb.is_blocked("B_KZONE |VEC_EXACT") is True
    assert not cb.is_blocked("B_MOM5 |VEC_EXACT")
    assert not cb.is_blocked("ENTRY_BREAKOUT_X")


def test_decay_rearms_and_purges_strikes(tmp_path):
    cb = _cb(tmp_path)
    pos = {}
    for sym in ("AUSDT", "BUSDT", "CUSDT"):
        pk = f"inf:{sym}_LONG"
        cb.record_entry(pk, 1.0, "DC_BREAKOUT_X", indicators=_ind(4, 4))
        pos[pk] = _losing()
    cb.record_entry("inf:ZUSDT_LONG", 1.0, "B_KZONE |VEC_EXACT", indicators=_ind(4, 4))
    pos["inf:ZUSDT_LONG"] = _losing()
    cb.check_and_update(pos, get_indicators_fn=lambda s: {})
    assert cb.is_blocked("DC_BREAKOUT_X")
    assert len([b for b in cb._bad_entries if b[1] == "DC_BREAKOUT"]) == 3
    cb._disabled_paths["DC_BREAKOUT"] -= 25 * 3600
    assert not cb.is_blocked("DC_BREAKOUT_X")
    assert [b for b in cb._bad_entries if b[1] == "DC_BREAKOUT"] == []
    assert [b for b in cb._bad_entries if b[1] == "B_KZONE |VEC"] != []
    cb.check_and_update({"inf:AUSDT_LONG": _losing(), "inf:BUSDT_LONG": _losing()}, get_indicators_fn=lambda s: {})
    assert not cb.is_blocked("DC_BREAKOUT_X")


def test_diagnosis_and_exit_semantics_pinned(tmp_path):
    cb = _cb(tmp_path)
    cb.record_entry("inf:GUSDT_LONG", 1.0, "ENTRY_BREAKOUT_X", indicators=_ind(0, 0))
    bad, exit_, _d = cb.diagnose_position("inf:GUSDT_LONG", _losing(-1.0), {"wt1_1h": -5.0, "wt2_1h": 5.0, "wt1_4h": -5.0, "wt2_4h": 5.0, "wt1_D": 5.0, "wt2_D": -5.0})
    assert bad is False and exit_ is True
    cb.record_entry("inf:HUSDT_LONG", 1.0, "ENTRY_BREAKOUT_X", indicators=_ind(3, 0))
    bad, exit_, _d = cb.diagnose_position("inf:HUSDT_LONG", _losing(-1.0), {})
    assert bad is True and exit_ is False
    cb.record_entry("inf:IUSDT_LONG", 1.0, "ENTRY_BREAKOUT_X", indicators=_ind(2, 3))
    bad, _e, _d = cb.diagnose_position("inf:IUSDT_LONG", _losing(-1.0), {})
    assert bad is True
    cb.record_entry("inf:JUSDT_LONG", 1.0, "ENTRY_BREAKOUT_X", indicators=_ind(1, 1))
    bad, exit_, _d = cb.diagnose_position("inf:JUSDT_LONG", _losing(-0.1), {})
    assert bad is False and exit_ is False
    bad, exit_, _d = cb.diagnose_position("inf:MISSING_LONG", _losing(-1.0), {})
    assert (bad, exit_) == (False, False)
