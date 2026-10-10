"""TOTAL PARITY regression (USER 2026-10-10): vec-chart trades ONLY.

- allows(): vec reasons pass; native entries AND native exits refused;
  emergency/manual/sync exits exempt (exits only).
- try_claim(): same-key exposure increase within TTL refused (the 67ms
  double-fill hole the broker-confirmed wire guard cannot see).
"""
import parity_exact_gate as peg


def setup_function(_):
    peg._reset_for_tests()


def test_vec_reasons_allowed():
    assert peg.allows("B12 |VEC_EXACT", "OPEN") == (True, "VEC")
    assert peg.allows("EXIT_VELOCITY_WT against-long g-0.14% |VEC_EXACT", "REDUCE") == (True, "VEC")
    assert peg.allows("VEC_DRIVEN_OPEN something", "OPEN") == (True, "VEC")


def test_native_entries_refused():
    ok, code = peg.allows("Winner augment: 11.4% > 3.0%", "AUGMENT")
    assert (ok, code) == (False, "NATIVE_ENTRY")
    ok, code = peg.allows("SUBSTITUTION_FOR_men:EDUUSDT_LONG_g-10.23", "AUGMENT")
    assert (ok, code) == (False, "NATIVE_ENTRY")
    ok, code = peg.allows("ENTRY_SIGNAL", "OPEN")
    assert (ok, code) == (False, "NATIVE_ENTRY")


def test_native_exits_refused():
    for reason in ["MTF_ATR_TRAIL_15m_x2.0_lvl4.79_g-0.73%", "ORPHANED_HEDGE_PARENT_CLOSED", "CB_HTF_EXHAUST_BAD_ENTRY: g=-0.80%", "HYBRID_STRUCT_EXIT_15m_g-0.32%", "DAYTRADE_TARGET_dc_4h_low_+0.10%_TARGET_BUF_g0.00", "EXIT_VELOCITY_WT_1h_vel44.0-against-short_g0.79"]:
        ok, code = peg.allows(reason, "REDUCE")
        assert (ok, code) == (False, "NATIVE_EXIT"), reason


def test_emergency_manual_sync_exits_exempt():
    assert peg.allows("MARGIN_KILL something", "REDUCE") == (True, "EMERGENCY_EXIT")
    assert peg.allows("LIQUIDATION_GUARD", "CLOSE", True) == (True, "EMERGENCY_EXIT")
    assert peg.allows("Manual/System Detection", "REDUCE") == (True, "EMERGENCY_EXIT")
    assert peg.allows("BROKER_SYNC_RECONCILE", "REDUCE") == (True, "SYNC_EXIT")


def test_emergency_never_opens():
    ok, code = peg.allows("MARGIN_KILL something", "OPEN")
    assert (ok, code) == (False, "NATIVE_ENTRY")


def test_claim_blocks_double_inside_ttl():
    ok1, _ = peg.try_claim("fin:AMATUSDT_SHORT|INC", now=1000.0)
    ok2, age = peg.try_claim("fin:AMATUSDT_SHORT|INC", now=1000.067)
    assert ok1 is True
    assert ok2 is False and abs(age - 0.067) < 1e-9


def test_claim_expires_after_ttl():
    peg.try_claim("fin:RLCUSDT_LONG|INC", now=1000.0)
    ok, _ = peg.try_claim("fin:RLCUSDT_LONG|INC", now=1000.0 + peg.CLAIM_TTL_S + 1)
    assert ok is True


def test_claim_keys_are_independent():
    peg.try_claim("fin:A|INC", now=1000.0)
    ok, _ = peg.try_claim("fin:B|INC", now=1000.001)
    assert ok is True
