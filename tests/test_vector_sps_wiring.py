"""Vector-suggested START sizing wiring (USER 2026-10-23).

Satellite entry lanes must size from the same vector suggestion as the main
lane — per-sym START from the frozen board set x the perf-tier mult — never
the bare global. Read-only: never writes the live store."""
import perf_tier_sizing as _pts


def test_q_sps_unknown_symbol_returns_default():
    import ez_positions_quick as _q

    assert _q._q_sps_usd("ZZZUNKNOWNUSDT", "LONG", 45.0) == 45.0


def test_q_sps_ranked_symbol_matches_tier_math():
    import ez_positions_quick as _q

    got = _q._q_sps_usd("SKLUSDT", "LONG", 45.0)
    want = _pts.apply_usd(45.0, "SKLUSDT", "LONG", 6.0, 300.0)
    assert got == want
    assert got >= 45.0


def test_ez_psym_sps_unknown_symbol_is_base():
    import ez_manage as _ez

    assert _ez._psym_sps("ZZZUNKNOWNUSDT", "LONG") == 45.0
