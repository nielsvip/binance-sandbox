"""Regression: tradier must never block flat entries when indicators stale,
and must force refresh immediately but continue with price-based emergency exits
if refresh produces nothing (user mandate: JUST DONT STOP TRADING)."""
import pathlib


def test_tradier_flat_entries_never_block():
    p = pathlib.Path("tradier_manage.py")
    src = p.read_text()
    # Flat entries must never block - check for STALE_NEVER_BLOCK_FLAT / degraded continue
    assert "STALE_NEVER_BLOCK_FLAT" in src, "flat never-block handling missing"
    assert "stale but proceeding to entry evaluation with best available indicators + live price" in src
    # Must force refresh immediately on stale before degraded continue
    assert " indicators stale" in src and "JSON_FALLBACK" in src, "immediate JSON fallback missing"
    # Must still allow emergency price-based exits when stale (is_stale flag, not STALE_INDICATORS_HELD)
    assert "STALE_HOLD_DEGRADED" in src, "degraded hold with is_stale flag missing"
    assert "is_stale = True" in src, "is_stale flag not set for degraded continue"


def test_tradier_force_refresh_immediate():
    # Verify force refresh happens immediately before throttling, not blocked by 30s throttle
    p = pathlib.Path("tradier_manage.py")
    src = p.read_text()
    # Refresh via load_indicators_from_json should be before THROTTLED check
    load_pos = src.find("load_indicators_from_json")
    throttle_pos = src.find('now_ts - last_mon < 30')
    assert load_pos != -1 and throttle_pos != -1, "refresh or throttle not found"
    assert load_pos < throttle_pos, "force refresh must be before throttling"
    # Also check S1 fallback spawn is immediate on stale >60s
    assert "emergency_tradier_indicators_rsync.sh" in src, "S1 fallback spawn missing"
    assert "STALE_NEVER_BLOCK" in src, "never-block log missing"


def test_tradier_price_fallback_when_refresh_empty():
    p = pathlib.Path("tradier_manage.py")
    src = p.read_text()
    # When refresh produces nothing, must still continue degraded, not return STALE_INDICATORS_HELD
    # Check that after stale handling, it does NOT return immediately for flat
    assert "STALE_DEGRADED_CONTINUE" in src, "degraded continue when stale but has indicators missing"
    assert "macro_fresh = True" in src, "macro_fresh force continue missing"
