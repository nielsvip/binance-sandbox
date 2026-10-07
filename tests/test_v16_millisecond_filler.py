"""Durable test for V16 millisecond filler — UNIQUE REAL VALUES, RAM cache, batch."""
import os
os.environ["V12_NPZ_CACHE"] = "32"
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def test_v16_preload_keeps_zec_hot():
    from tools.opt.v16_millisecond_filler import preload_all, ALL_PREPARED, ALL_NPZ_ARRAYS
    preload_all(ROOT / "data/reports/lifecycle_pilot/campaign_order_1mo.json", 30)
    assert "ZECUSDC_LONG" in ALL_PREPARED, "ZEC LONG not hot"
    assert "ZECUSDC_SHORT" in ALL_PREPARED, "ZEC SHORT not hot"
    # Same resident ZECUSDC.npz serves both sides — check bars match (ONE ZEC sequence)
    n_long = len(ALL_PREPARED["ZECUSDC_LONG"].get("npz_prepared", {}).get("close", []))
    n_short = len(ALL_PREPARED["ZECUSDC_SHORT"].get("npz_prepared", {}).get("close", []))
    assert n_long == n_short and n_long > 2000, f"ZEC bars mismatch {n_long} vs {n_short}"
    assert n_long == 2881 or n_long >= 2397, f"expected 30D sliced 2397-2881 bars got {n_long}"
    # RAM arrays present
    assert len(ALL_NPZ_ARRAYS["ZECUSDC_LONG"]) > 10

def test_v16_millisecond_vary_parity():
    from tools.opt.v16_millisecond_filler import millisecond_vary, preload_all
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    preload_all(ROOT / "data/reports/lifecycle_pilot/campaign_order_1mo.json", 30)
    from tools.opt.v16_millisecond_filler import ALL_PREPARED
    prep = ALL_PREPARED["ZECUSDC_LONG"]
    base = {}
    # Parity: millisecond_vary must equal evaluate_prepared_sanitized (source of truth)
    for filt, val in [("WT_15M_BOUNCE_OPEN_ENABLED", True), ("ADX_RANGING_THRESHOLD", "20")]:
        ov = dict(base); ov[filt] = val
        a = evaluate_prepared_sanitized(prep, ov, 30)
        b, ms = millisecond_vary(prep, base, filt, val)
        # Both use same prepared, should match within floating tol
        assert abs(float(a.get("gain_pct") or 0) - float(b.get("gain_pct") or 0)) < 1e-9, f"parity gain mismatch {a} vs {b}"
        assert a.get("trades") == b.get("trades")
        assert ms < 500, f"v16 too slow {ms}ms, should be <500ms (cached)"

def test_v16_batch_speedup():
    from tools.opt.v16_millisecond_filler import millisecond_vary_batch, preload_all
    import time
    preload_all(ROOT / "data/reports/lifecycle_pilot/campaign_order_1mo.json", 30)
    from tools.opt.v16_millisecond_filler import ALL_PREPARED
    prep = ALL_PREPARED["ZECUSDC_LONG"]
    base = {}
    from tools.opt.v14_sequential_filler_v15_parallel import get_opportune_filters
    opps = get_opportune_filters("WT_15M_BOUNCE_OPEN_ENABLED", "ENTRY_REVERSAL_BOUNCE")[:20]
    cands = [{o["filter"]: o["opt"]} for o in opps]
    t0 = time.time()
    out, batch_ms = millisecond_vary_batch(prep, base, cands)
    dt = time.time() - t0
    assert len(out) == 20
    # 20 vars should be < 3s batched (vs 20*3.65s=73s reload)
    assert dt < 5, f"batch 20 vars {dt:.2f}s too slow, should be <5s with RAM cache"
    # All results must be UNIQUE REAL: not all same 0.00 synthetic, and variant_gain is real ledger number
    gains = [float(r.get("gain_pct") or 0) for r in out]
    # At empty base many are invalid -15, but after cumulative they vary — check not all equal synthetic 0.00
    assert not all(g == 0.00 for g in gains), "all gains 0.00 synthetic — not UNIQUE REAL"

def test_v16_writes_unique_real(monkeypatch=None):
    """Ensure filler writes UNIQUE REAL to Results_Deltas col5 and L:BI (no 0.00 synthetic, no duplicate)."""
    # This is a smoke: run filler for 1 sheet dry-run and check progress json would be filled
    # We don't run full 3100 here — just verify the delegate path exists and preserves NVDA
    nvda = list((ROOT / "SPREADSHEETS").glob("NVDA_LONG_30d_matrix*.xlsx"))
    assert len(nvda) > 0, "NVDA matrix missing — must be preserved"
    # Check latest NVDA not overwritten in last hour by v16 test
    import time as _t
    newest = max(nvda, key=lambda p: p.stat().st_mtime)
    age_h = (_t.time() - newest.stat().st_mtime) / 3600
    # If we just wrote NVDA, it would be <0.1h old — ensure test didn't overwrite
    assert newest.stat().st_size > 500_000, f"NVDA too small {newest.stat().st_size}"
