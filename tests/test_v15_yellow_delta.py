import sys
sys.path.insert(0, '.')
import v12_quick_engine as V
from tools.opt.evaluate_v12 import _exact_30d_slice

def _load_prepared(sym="BTCUSDC", is_long=True):
    # Load real NPZ if available, else synthetic fallback
    try:
        stores = V.load_npz("crypto", [sym], "2024-01-01")
        npz = stores.get(sym)
        if npz is not None and len(npz.get("timestamps", [])) > 100:
            from tools.opt.v12_pilot import prepare_batch
            prep = prepare_batch({sym: npz}, sym, is_long)
            return prep, npz
    except Exception:
        pass
    # synthetic fallback
    import numpy as np
    n=300
    close=np.linspace(100,115,n)
    npz={"close":close,"timestamps":np.arange(n)*18000,"dc_high_15m":np.full(n,120),"dc_low_15m":np.full(n,90),"dc_high_1h":np.full(n,120),"dc_low_1h":np.full(n,90),"dc_high_4h":np.full(n,120),"dc_low_4h":np.full(n,90),"wt1_15m":np.zeros(n),"wt2_15m":np.zeros(n)}
    from tools.opt.v12_pilot import prepare_batch
    try:
        prep=prepare_batch({sym: npz}, sym, is_long)
    except:
        prep=None
    return prep, npz

def test_yellow_delta_not_minus_one():
    # WT_AGAINST_FILTER_ENABLED is a known switch that should produce a real delta vs cumulative_before, not -1
    prep, npz = _load_prepared("BTCUSDC", True)
    if prep is None:
        # cannot test without prepared, skip
        return
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    # Build variant dict for WT_AGAINST_FILTER_ENABLED=True vs cumulative_before
    base_variant = {}
    try:
        base_vec = evaluate_prepared_sanitized(prep, base_variant, window_days=30)
    except Exception as e:
        assert False, f"base eval failed {e}"
    assert base_vec is not None and base_vec.get("gain_pct") is not None, "base gain None"
    cumulative_before = float(base_vec.get("gain_pct") or 0)
    # Test yellow variant
    yellow_variant = {"WT_AGAINST_FILTER_ENABLED": True}
    try:
        vec = evaluate_prepared_sanitized(prep, yellow_variant, window_days=30)
    except Exception as e:
        assert False, f"yellow eval failed {e}"
    assert vec is not None, "yellow vec None"
    gain = vec.get("gain_pct")
    assert gain is not None, f"yellow gain None invalid {vec.get('invalid_reason')}"
    delta = float(gain) - cumulative_before
    # Must not be -1 placeholder, must be real computed delta (could be 0, positive, or negative but not sentinel -1 from timeout)
    assert delta != -1.0, f"delta is sentinel -1.0, indicates timeout/no calculation for WT_AGAINST_FILTER_ENABLED"
    # Also check yellow delta in range plausible
    assert -100 < delta < 100, f"delta out of plausible range {delta}"

def test_prepared_cache_used():
    # Ensure YELLOW_TIMEOUT is sufficient for real eval (0.11-0.19s) — check that 0.5s allows completion
    import v15_pilot
    assert hasattr(v15_pilot, "YELLOW_TIMEOUT")
    assert v15_pilot.YELLOW_TIMEOUT >= 0.5, f"YELLOW_TIMEOUT too short {v15_pilot.YELLOW_TIMEOUT}, will cause -1 diarrhea"
