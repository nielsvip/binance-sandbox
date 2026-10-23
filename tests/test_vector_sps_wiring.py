"""Vector-suggested START sizing wiring (USER 2026-10-23).

Satellite entry lanes must size from the same vector suggestion as the main
lane — per-sym START from the frozen board set x the perf-tier mult — never
the bare global. Read-only: never writes the live store."""
import perf_tier_sizing as _pts


def _fresh_tiers():
    """Reload the tier module against the real tier file.

    An earlier suite re-imports perf_tier_sizing with PERSYM_SIZE_TIERS_PATH pointed at tmp; only a reload rebinds PATH (env is clean again post-suite) and drops memoised state."""
    import importlib
    import sys

    _pts = sys.modules.get("perf_tier_sizing")
    if _pts is not None:
        importlib.reload(_pts)
    _pts._CACHE.update(mtime=None, tiers={}, checked=0.0)
    _pts._TRIG.update(checked=0.0, sig=None, set=frozenset())


def _want(symbol, side, base):
    import sys

    import ez_positions_quick as _q

    _live = sys.modules.get("perf_tier_sizing") or _pts
    return _live.apply_usd(float(base), symbol, side, float(getattr(_q.config, "PERF_TIER_MIN_ORDER_USD", 6.0)), float(_q._psym_get(symbol, side, "MAX_ORDER_VALUE", getattr(_q.config, "MAX_ORDER_VALUE", 300.0))))


def test_q_sps_unknown_symbol_passes_base_through_tier():
    _fresh_tiers()
    import ez_positions_quick as _q

    base = float(_q._psym_get("ZZZUNKNOWNUSDT", "LONG", "START_POSITION_SIZE", 45.0))
    assert _q._q_sps_usd("ZZZUNKNOWNUSDT", "LONG", 45.0) == _want("ZZZUNKNOWNUSDT", "LONG", base)


def test_q_sps_ranked_symbol_carries_tier_boost():
    _fresh_tiers()
    import ez_positions_quick as _q

    base = float(_q._psym_get("SKLUSDT", "LONG", "START_POSITION_SIZE", 45.0))
    got = _q._q_sps_usd("SKLUSDT", "LONG", 45.0)
    assert got == _want("SKLUSDT", "LONG", base)
    assert got > base


def test_ez_psym_sps_unknown_symbol_passes_tier_through():
    import ez_manage as _ez

    assert _ez._psym_sps("ZZZUNKNOWNUSDT", "LONG") == _ez._psym_sps_raw("ZZZUNKNOWNUSDT", "LONG")
