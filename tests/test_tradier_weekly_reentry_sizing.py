"""Weekly-max reentry sizing (ZCSH 19->1 bleed fix) — 10-150% of weekly max + STDEV 1-5x + bounce.

Requires:
- _weekly_max_shares_tradier uses max_quantity (7d decay) not last_reduction_amount
- _reentry_bounce_scale_tradier maps WT favor + k rising to 0.10-1.50
- _stdev_mult_tradier maps lrL_pct_b_D 0->5x (LONG bottom) 1->1x (LONG top) inverse SHORT
- _reentry_stdev_bounce_combined_scale blends to 0.10-1.50
- evaluate_reentry and reentry_monitor_loop use weekly, not stale 1-share chunk
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tradier_manage as tm
import config_tradier

def _mock_pos(max_q=20.9, amt=0.0, last_amt=1.0, hours_ago=1.0, side="LONG"):
    class P:
        pass
    p = P()
    p.positionAmt = amt
    p.max_quantity = max_q
    p.last_reduction_amount = last_amt
    p.last_reduction_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    p.position_side = side
    p.max_positionSize = 5000.0
    return p

def test_weekly_max_uses_peak_not_last_chunk():
    pos = _mock_pos(max_q=20.9, last_amt=1.0, hours_ago=1.0)
    weekly = tm._weekly_max_shares_tradier(pos, 125.0, "ZCSH", "trb")
    assert 20.0 <= weekly <= 21.0, f"weekly should be ~20.9 not {weekly}"
    # last_reduction_amount is 1 but weekly is 20.9 — fix ensures not using 1
    assert weekly > 10 * 1.0

def test_weekly_decay_24h_flat_to_target():
    pos = _mock_pos(max_q=20.9, hours_ago=8*24)  # 8 days flat
    weekly = tm._weekly_max_shares_tradier(pos, 125.0, "ZCSH", "trb")
    target = 2 * 500.0 / 125.0  # 8.0
    assert abs(weekly - target) < 0.5, f"after 8d should decay to {target} got {weekly}"
    # 1 hour flat should not decay
    pos2 = _mock_pos(max_q=20.9, hours_ago=1.0)
    assert tm._weekly_max_shares_tradier(pos2, 125.0, "ZCSH", "trb") == 20.9
    # 48h partial decay: between 20.9 and 8.0
    pos3 = _mock_pos(max_q=20.9, hours_ago=48)
    weekly3 = tm._weekly_max_shares_tradier(pos3, 125.0, "ZCSH", "trb")
    assert 8.0 < weekly3 < 20.9

def test_weekly_fallback_when_no_max():
    class P2:
        max_quantity = 0
        positionAmt = 0
        last_reduction_amount = 0
        last_reduction_time = None
        position_side = "LONG"
    weekly = tm._weekly_max_shares_tradier(P2(), 100.0, "FAKE", "trb")
    assert weekly == 500.0 / 100.0

def test_bounce_scale_weak_vs_strong():
    strong = {"wt1_5m":10,"wt2_5m":5,"wt1_15m":12,"wt2_15m":6,"wt1_1h":8,"wt2_1h":3,"k_5m":55,"k_5m_prev":40,"k_15m":60,"k_15m_prev":45}
    weak = {"wt1_5m":1,"wt2_5m":5,"wt1_15m":2,"wt2_15m":6,"wt1_1h":1,"wt2_1h":3,"k_5m":45,"k_5m_prev":50,"k_15m":40,"k_15m_prev":45}
    assert tm._reentry_bounce_scale_tradier(strong, True) == 1.5
    assert tm._reentry_bounce_scale_tradier(weak, True) < 0.3
    # bounds
    assert 0.10 <= tm._reentry_bounce_scale_tradier({}, True) <= 1.50
    assert 0.10 <= tm._reentry_bounce_scale_tradier(strong, False) <= 1.50

def test_stdev_mult_gradient():
    # LONG: bottom (pct_b=0) => 5x, top (1) =>1x, mid 0.5=>3x
    assert tm._stdev_mult_tradier({"lrL_pct_b_D":0.0}, True) == 5.0
    assert tm._stdev_mult_tradier({"lrL_pct_b_D":1.0}, True) == 1.0
    assert tm._stdev_mult_tradier({"lrL_pct_b_D":0.5}, True) == 3.0
    # SHORT inverse
    assert tm._stdev_mult_tradier({"lrL_pct_b_D":0.0}, False) == 1.0
    assert tm._stdev_mult_tradier({"lrL_pct_b_D":1.0}, False) == 5.0
    # below/above extremes clamp
    assert tm._stdev_mult_tradier({"lrL_pct_b_D":-0.2}, True) == 5.0
    assert tm._stdev_mult_tradier({"lrL_pct_b_D":1.5}, True) == 1.0
    # fallback neutral
    assert tm._stdev_mult_tradier({}, True) == 1.0
    # bb_pct_b fallback
    assert tm._stdev_mult_tradier({"bb_pct_b_D":0.0}, True) == 5.0

def test_combined_scale_within_10_150():
    strong_bottom = {"wt1_5m":10,"wt2_5m":5,"wt1_15m":12,"wt2_15m":6,"wt1_1h":8,"wt2_1h":3,"k_5m":55,"k_5m_prev":40,"k_15m":60,"k_15m_prev":45,"lrL_pct_b_D":0.0}
    weak_top = {"wt1_5m":1,"wt2_5m":5,"wt1_15m":2,"wt2_15m":6,"wt1_1h":1,"wt2_1h":3,"k_5m":45,"k_5m_prev":50,"k_15m":40,"k_15m_prev":45,"lrL_pct_b_D":1.0}
    s1, b1, st1 = tm._reentry_stdev_bounce_combined_scale(strong_bottom, True)
    s2, b2, st2 = tm._reentry_stdev_bounce_combined_scale(weak_top, True)
    assert 0.10 <= s1 <= 1.50
    assert 0.10 <= s2 <= 1.50
    assert s1 == 1.50  # strong bounce + bottom level => max
    assert s2 == 0.10  # weak + top => min
    # ZCSH case: weekly 20.9 * 1.5 = 31.3 (150%), *0.10 =2.09 (10%)
    pos = _mock_pos(max_q=20.9)
    weekly = tm._weekly_max_shares_tradier(pos, 125.0, "ZCSH", "trb")
    qty_max = weekly * s1
    qty_min = weekly * s2
    assert abs(qty_max - 31.35) < 0.1
    assert abs(qty_min - 2.09) < 0.1
    # old bug would have been 1 share
    assert qty_min > 1.0

def test_evaluate_reentry_uses_weekly_not_last_red():
    # Spot-check source no longer uses last_reduction_amount for base
    src = Path(ROOT / "tradier_manage.py").read_text()
    assert "_weekly_max_shares_tradier" in src
    assert "_reentry_stdev_bounce_combined_scale" in src
    assert "_stdev_mult_tradier" in src
    # old single-chunk pattern must not be the base calc
    assert "Size based on LAST REDUCTION" not in src
    assert "Size based on WEEKLY MAX" in src

def test_reentry_monitor_uses_weekly():
    src = Path(ROOT / "tradier_manage.py").read_text()
    # monitor loop should reference weekly
    assert "REENTRY_MONITOR_WEEKLY" in src
    assert "_weekly_max_shares_tradier(_mon_pos" in src
    # queue should not overwrite with last_reduction_amount
    assert "NEVER use last_reduction_amount" in src

def test_10_150_bounds_enforced():
    pos = _mock_pos(max_q=100.0, hours_ago=1.0)
    weekly = tm._weekly_max_shares_tradier(pos, 10.0, "BIG", "trb")  # 100 shares
    # Even with silly multipliers, helper clamps 0.10-1.50
    for ind in [{"wt1_5m":100,"wt2_5m":0,"wt1_15m":100,"wt2_15m":0,"wt1_1h":100,"wt2_1h":0,"k_5m":80,"k_5m_prev":20,"lrL_pct_b_D":0.0},
                {"wt1_5m":0,"wt2_5m":100,"wt1_15m":0,"wt2_15m":100,"wt1_1h":0,"wt2_1h":100,"k_5m":20,"k_5m_prev":80,"lrL_pct_b_D":1.0}]:
        s, _, _ = tm._reentry_stdev_bounce_combined_scale(ind, True)
        qty = weekly * s
        assert weekly * 0.10 - 1e-9 <= qty <= weekly * 1.50 + 1e-9
