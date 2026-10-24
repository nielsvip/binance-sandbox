"""B12 WT-momentum native wiring (USER 2026-10-24 VEC-LAST catch-up).

The vector sweeps WT_CROSS_B12 (REENTRY_B12_WT_MOM_ENABLED); the tradier live
lane must fire the same predicate through the shared core (not via the twin).
"""
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_core_scalar_vec_agree():
    import random
    from vec_decisions import check_entry_b12_wt_mom as B

    rng = random.Random(20261024)
    for trial in range(300):
        vals = [rng.uniform(-60, 60) for _ in range(7)]
        for long in (True, False):
            s = B.b12_fires_scalar(*vals, long)
            v = bool(B.b12_mask_vec(*([ [x] for x in vals ]), long)[0])
            assert s == v, (trial, long, vals)


def test_core_fires_and_silent():
    from vec_decisions import check_entry_b12_wt_mom as B

    assert B.b12_fires_scalar(10, 5, 8, 3, 6, 1, 2.5, True) is True
    assert B.b12_fires_scalar(-10, -5, -8, -3, -6, -1, -2.5, False) is True
    assert B.b12_fires_scalar(10, 5, 8, 3, 6, 1, 0.5, True) is False
    assert B.b12_fires_scalar(10, 5, 1, 3, 6, 1, 2.5, True) is False
    assert B.b12_fires_scalar(10, 5, 8, 3, 6, 1, 2.5, False) is False


def test_live_lane_falls_back_15m_for_missing_3m():
    src = Path("tradier_manage.py").read_text()
    assert "NO-3M parity" in src
    assert '"wt_velocity_15m"' in src or "'wt_velocity_15m'" in src or "wt_velocity_15m" in src


def test_tradier_lane_wires_b12_on_own_switch():
    src = Path("tradier_manage.py").read_text()
    i = src.index("TRADIER_B12_WT_MOM_")
    block_start = src.rindex("if action_type", 0, i)
    gate = src[block_start:i]
    assert "REENTRY_B12_WT_MOM_ENABLED" in gate, gate[:300]
    assert "DC_BREAKOUT_ENTRY_ENABLED" not in gate
    assert "b12_fires_scalar" in src
    assert '_cfg_auto("REENTRY_B12_WT_MOM_ENABLED", True)' in src
    assert "_entry_score = max(float(_entry_score or 0.0), float(conf))" in src
