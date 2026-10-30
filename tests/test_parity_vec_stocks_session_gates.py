"""Lane D parity: stocks live session gates — vec twin == live predicate on the same synthetic inputs.

Twins in vec_decisions/stocks_live_session_gates.py:
  RTH-only (live tradier execute_now MARKET_CLOSED outside Mon-Fri 09:30-16:00 ET)
  OPENING_BUFFER (live tradier_manage.in_opening_buffer)
  HTF_TREND_VETO_ON_REDUCE (live tradier_manage.execute_now)
"""
import ast
import datetime as dt
import pathlib
import sys
from zoneinfo import ZoneInfo

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NY = ZoneInfo("America/New_York")


def _ts_grid():
    start = dt.datetime(2026, 9, 28, 4, 0, tzinfo=NY)  # Monday
    return np.array([(start + dt.timedelta(minutes=15 * k)).timestamp() for k in range(7 * 96)])


def test_rth_mask_equals_live_rule_at_bar_close_and_open():
    import vec_decisions.stocks_live_session_gates as G
    ts = _ts_grid()
    m = G.rth_mask(ts, 15.0)
    for t, v in zip(ts, m):
        d = dt.datetime.fromtimestamp(t, NY)
        mod = d.hour * 60 + d.minute
        # bar actionable iff live is open at both the bar open and (just before) the bar close
        exp = G.live_in_rth(d.weekday(), mod) and G.live_in_rth(d.weekday(), mod + 15 - 1e-6)
        assert bool(v) == exp, (d, v, exp)
    assert m.sum() == 5 * 26  # 26 RTH bars per weekday, none on the weekend


def test_opening_buffer_equals_live_rule_at_decision_instant():
    import vec_decisions.stocks_live_session_gates as G
    ts = _ts_grid()
    for buf in (0.0, 15.0, 30.0, 45.0):
        m = G.opening_buffer_mask(ts, buf, 15.0)
        for t, v in zip(ts, m):
            d = dt.datetime.fromtimestamp(t, NY) + dt.timedelta(minutes=15)
            exp = G.live_in_opening_buffer(d.weekday(), d.hour * 60 + d.minute, buf)
            assert bool(v) == exp, (buf, d, v, exp)
    assert G.opening_buffer_mask(ts, 30.0, 15.0).sum() == 5 * 2


def test_live_buffer_replica_matches_live_source_logic():
    src = (ROOT / "tradier_manage.py").read_text()
    for needle in ("def in_opening_buffer", "open_et = now_et.replace(hour=9, minute=30", "if 0 <= mins < min_minutes:", "OPENING_BUFFER_NO_CLOSE_MINUTES"):
        assert needle in src, needle


def test_htf_veto_bypass_tokens_verbatim_from_live():
    import vec_decisions.stocks_live_session_gates as G
    src = (ROOT / "tradier_manage.py").read_text()
    tree = ast.parse(src)
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_htfr_bypass_substrings" for t in node.targets):
            found = ast.literal_eval(node.value)
    assert found is not None
    assert tuple(found) == G.HTF_VETO_BYPASS_TOKENS


def test_htf_veto_vec_equals_live_scalar():
    import vec_decisions.stocks_live_session_gates as G
    rng = np.random.default_rng(3)
    n = 3000
    w1 = rng.normal(0, 30, n); w2 = rng.normal(0, 30, n)
    w1[rng.random(n) < 0.05] = 0.0
    reasons = ["ULTIMATE_DC_HARD_STOP", "EXIT_VELOCITY_WT", "DAYTRADE_STOP dc_15m_low", "GAP_MOC_EXIT", "WT_DC_EXIT_40_g=1.00%_MANDATORY_REENTRY", "R1_DC_LOW", "DD_BOUNCE_STOP px", "EMERGENCY_DC4H", "STRUCTURAL_RANGE_SHIFT"]
    for is_long in (True, False):
        sup = G.daily_wt_supports_mask(w1, w2, is_long)
        for k in range(n):
            for r in reasons:
                assert G.htf_veto_blocks(r, sup[k]) == G.live_htf_veto_blocks(r, w1[k], w2[k], is_long), (k, r, is_long)


def test_vec_ppl_label_maps_to_live_bypass():
    import vec_decisions.stocks_live_session_gates as G
    assert G.htf_veto_bypassed("PPL_SL_CLOSE_BE+buffer_px1")
    assert not G.htf_veto_bypassed("DAYTRADE_TARGET dc_15m_high")


def test_engine_wiring_present_and_defaults_live():
    import v12_quick_engine as V
    t = V.QuickConfig(); t.apply_tradier_defaults()
    assert t.STOCKS_RTH_ONLY_ENABLED is True and t.HTF_TREND_VETO_ON_REDUCE_ENABLED is True
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "_slsg.rth_mask(" in src and "_htfv_block(i, 'ULTIMATE_DC_HARD_STOP')" in src and "_htfv_block(i, reason)" in src


def test_moc_window_from_timestamps_matches_live_last_90m():
    import vec_decisions.stocks_live_session_gates as G
    ts = _ts_grid()
    win, dl = G.moc_window_masks(ts, 15.0, 90.0)
    for t, w, d in zip(ts, win, dl):
        x = dt.datetime.fromtimestamp(t, NY)
        end = x.hour * 60 + x.minute + 15
        wk = x.weekday() < 5
        assert bool(w) == (wk and 870 < end <= 960), x
        assert bool(d) == (wk and end == 960), x
    assert win.sum() == 5 * 6 and dl.sum() == 5


def test_trading_day_lookback_start():
    import vec_decisions.stocks_live_session_gates as G
    ts = _ts_grid()  # 7 calendar days x 96 bars from Monday 04:00
    st = G.trading_day_lookback_start(ts, 2)
    dates = [dt.datetime.fromtimestamp(t, NY).date() for t in ts]
    firsts = [i for i in range(len(ts)) if i == 0 or dates[i] != dates[i - 1]]
    for i in range(len(ts)):
        k = max(j for j, f in enumerate(firsts) if f <= i)
        assert st[i] == firsts[max(0, k - 1)], (i, st[i])


def test_engine_gap_moc_and_buffer_wiring():
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "stocks_live_session_gates.moc_window_masks(_gts_lbl" in src and "trading_day_lookback_start(_gts_lbl" in src
    assert "STOCKS_OPENING_BUFFER_ENTRY_ENABLED', True)" in src


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_"):
            f(); print("PASS", k)
