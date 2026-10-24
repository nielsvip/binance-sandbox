"""Tradier GOLDEN_RULE producer (USER parity: wire missing producer). Pins the shared
scalar admission the lane calls: breakout cross fires phase 1, retest fires phase 2,
state advances, gate off silences."""
from vec_decisions import golden_loop as g


class C:
    GOLDEN_RULE_ENABLED = True
    GOLDEN_RULE_MULT_BREAKOUT = 0.1
    GOLDEN_RULE_MULT_RETEST = 5.0
    GOLDEN_RULE_RETEST_WINDOW_S = 14400.0
    GOLDEN_RULE_BASE_USD = 5.0


def _row(px=105.0, dc_h=100.0, dc_b=99.0, bb_u=101.0, w1=10.0, w2=5.0, k=50.0, d=45.0, kp=40.0):
    return {"current_price": px, "dc_high_1h": dc_h, "dc_basis_1h": dc_b,
            "bb_upper_1h": bb_u, "wt1_15m": w1, "wt2_15m": w2,
            "k_15m": k, "d_15m": d, "k_15m_prev": kp, "stoch_k_15m": k,
            "stoch_d_15m": d}


def test_breakout_phase1():
    out = g.scalar_fire(_row(), False, 0.0, 1792000000.0, True, "tradier", C())
    assert out["prev_above"] is True and out["breakout_ts"] == 1792000000.0


def test_no_cross_no_fire():
    out = g.scalar_fire(_row(px=99.0), False, 0.0, 1792000000.0, True, "tradier", C())
    assert out["fire"] is False and out["prev_above"] is False


def test_gate_off_silent():
    class Off(C):
        GOLDEN_RULE_ENABLED = False
    out = g.scalar_fire(_row(), False, 0.0, 1792000000.0, True, "tradier", Off())
    assert out["fire"] is False


def test_state_threads_through():
    o1 = g.scalar_fire(_row(), False, 0.0, 1792000000.0, True, "tradier", C())
    o2 = g.scalar_fire(_row(), o1["prev_above"], o1["breakout_ts"], 1792001000.0, True, "tradier", C())
    assert o2["prev_above"] is True and o2["breakout_ts"] == 1792000000.0


def test_retest_phase2_after_breakout():
    o1 = g.scalar_fire(_row(), False, 0.0, 1792000000.0, True, "tradier", C())
    # back at/above basis, k turning up, WT aligned, inside window -> phase 2
    o2 = g.scalar_fire(_row(px=99.5), o1["prev_above"], o1["breakout_ts"], 1792003600.0, True, "tradier", C())
    assert o2["fire"] is True and o2["phase"] == 2
