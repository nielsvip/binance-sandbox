"""PARITY LOOP STOCKS 2026-10-06 — WT cross-TF composite: live tradier_indicators == NPZ builder formula (shared function)."""
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vec_decisions.wt_cross_tf_composite import composite_fields, scalar_fields  # noqa: E402


def _builder_reference(merged, tfs, n):
    """verbatim arithmetic of backtest_v8_precompute.py WT composite pass (~1941-2023)."""
    bull_count = np.zeros(n, dtype=np.int8)
    bear_count = np.zeros(n, dtype=np.int8)
    comp_long = np.zeros(n, dtype=np.float32)
    comp_short = np.zeros(n, dtype=np.float32)
    for tf in tfs:
        b = merged.get(f"wt_bullish_{tf}")
        if b is not None:
            bull_count += (np.asarray(b) > 0).astype(np.int8)
            bear_count += (np.asarray(b) <= 0).astype(np.int8)
        w = {"15m": 2, "1h": 3, "4h": 4, "D": 5, "W": 2, "M": 1}.get(tf, 1)
        score = merged.get(f"wt_score_{tf}")
        if score is not None:
            s = np.asarray(score, dtype=np.float64)
            comp_long += np.maximum(0, s) * w
            comp_short += np.maximum(0, -s) * w
    return {"wt_bull_alignment": bull_count, "wt_bear_alignment": bear_count, "wt_composite_long": comp_long.astype(np.float32),
            "wt_composite_short": comp_short.astype(np.float32), "wt_composite_delta": (comp_long - comp_short).astype(np.float32),
            "wt_composite_bias": np.where(comp_long > comp_short, 1, np.where(comp_short > comp_long, -1, 0)).astype(np.int8)}


def _synthetic(n=3000, seed=1):
    r = np.random.RandomState(seed)
    m = {}
    for tf in ("15m", "1h", "4h", "D", "W", "M"):
        w1 = r.normal(0, 40, n)
        w2 = w1 + r.normal(0, 8, n)
        m[f"wt_score_{tf}"] = (w1 - w2).astype(np.float32)
        m[f"wt_bullish_{tf}"] = (w1 > w2).astype(np.int8)
    return m


def test_array_bitwise_equals_builder():
    m = _synthetic()
    ref = _builder_reference(m, ("15m", "1h", "4h", "D", "W", "M"), 3000)
    got = composite_fields(m.get)
    for k, v in ref.items():
        assert got[k].dtype == v.dtype, k
        assert np.array_equal(got[k], v), k


def test_missing_tf_is_skipped_like_builder():
    m = _synthetic()
    for k in ("wt_score_M", "wt_bullish_M"):
        m.pop(k)
    ref = _builder_reference(m, ("15m", "1h", "4h", "D", "W", "M"), 3000)
    got = composite_fields(m.get)
    for k, v in ref.items():
        assert np.array_equal(got[k], v), k


def test_scalar_rows_equal_array():
    m = _synthetic(400)
    arr = composite_fields(m.get)
    lab = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}
    for i in range(400):
        row = {k: v[i].item() for k, v in m.items()}
        s = scalar_fields(row.get)
        assert s["wt_composite_long"] == float(arr["wt_composite_long"][i])
        assert s["wt_composite_short"] == float(arr["wt_composite_short"][i])
        assert s["wt_composite_delta"] == float(arr["wt_composite_delta"][i])
        assert s["wt_bull_alignment"] == int(arr["wt_bull_alignment"][i])
        assert s["wt_composite_bias"] == lab[int(arr["wt_composite_bias"][i])]


def test_wt1_wt2_fallback_equals_score_inputs():
    r = np.random.RandomState(3)
    row_a, row_b = {}, {}
    for tf in ("15m", "1h", "4h", "D", "W", "M"):
        w1, w2 = float(r.normal(0, 40)), float(r.normal(0, 40))
        row_a.update({f"wt1_{tf}": w1, f"wt2_{tf}": w2})
        row_b.update({f"wt_score_{tf}": float(np.float32(w1 - w2)), f"wt_bullish_{tf}": int(w1 > w2)})
    assert scalar_fields(row_a.get) == scalar_fields(row_b.get)


def test_live_tradier_indicators_uses_shared_formula():
    src = (ROOT / "tradier_indicators.py").read_text()
    assert "from vec_decisions.wt_cross_tf_composite import scalar_fields as _wtc_scalar" in src
    assert "symbol_data.update(_wtc_scalar(symbol_data.get))" in src
    assert 'symbol_data["wt_composite_long"] = max(-100.0, min(100.0, long_score))' not in src
    assert "self._inject_wm_wavetrend(symbol_data, df, symbol=symbol)" in src
    import tradier_indicators as TI
    sd = {f"wt1_{tf}": 10.0 * (k + 1) for k, tf in enumerate(("5m", "15m", "1h", "4h", "D"))}
    sd.update({f"wt2_{tf}": 5.0 for tf in ("5m", "15m", "1h", "4h", "D")})
    TI.TradierIndicatorOrchestrator._inject_wt_composite(None, "X", sd)
    exp = scalar_fields(sd.get)
    for k, v in exp.items():
        assert sd[k] == v, k
    assert sd["wt_composite_long"] == float(np.float32((20 - 5) * 2 + (30 - 5) * 3 + (40 - 5) * 4 + (50 - 5) * 5))
