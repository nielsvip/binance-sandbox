"""PARITY 2026-10-06 — guard: every crypto NPZ field the builder writes must equal what live ez_indicators emits at the same bar.

For each symbol x TF the builder (backtest_v8_precompute.compute_tf_arrays, MODE='crypto') runs once on the klines frame; live
(ez_indicators.IndicatorCalculator.compute) runs on the prefix ending at each cut bar.  Every field both produce is compared
(strings via the NPZ integer encodings).  Tolerance: 5e-3 absolute (float32 storage) — anything else is a live != vec disparity.
Before the staged builder patch (data/parity/precompute_live_equal_20261006.patch) this FAILS by design (155 field x TF).
Env: PARITY_CRYPTO_SYMS (default BTCUSDC), PARITY_CRYPTO_CUTS (default 8), PARITY_CRYPTO_KLINES (default <BASE_PATH>/klines_cache),
PARITY_BUILDER_MODULE (default backtest_v8_precompute).  Needs scipy-free ez/tradier imports + klines: server-side."""
import importlib
import os
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TFS = ("15m", "1h", "4h", "D")
TOL = 5e-3
_STR = {"HIDDEN_BULL": 2.0, "HIDDEN_BEAR": -2.0, "BUY": 1.0, "SELL": -1.0, "TRANSITIONING": 0.0, "green": 1.0, "red": -1.0, "neutral": 0.0}


def _num(P, k, v):
    from tradier_vec_exact import _encode_label
    if v is None and k.startswith(("wt_divergence_", "wt_cross_rising_", "wt_peak", "wt_trough", "wt_cross_value_", "wt_cross_prev_value_")):
        return 0.0
    if isinstance(v, (bool, np.bool_, int, float, np.integer, np.floating)):
        return float(v)
    if isinstance(v, str):
        if k.startswith(("wt_divergence_", "wt_signal_", "wt_wave_phase_", "ha_")) and v in _STR:
            return _STR[v]
        if k.startswith("bar_pattern_") and hasattr(P, "BAR_PATTERN_CODES"):
            return float(P.BAR_PATTERN_CODES.get(v, -99))
        if k.startswith("bar_vol_regime_") and hasattr(P, "BAR_VOL_REGIME_CODES"):
            return float(P.BAR_VOL_REGIME_CODES.get(v, -99))
        e = _encode_label(k, v)
        return float(e) if isinstance(e, (int, float)) else None
    return None


def _audit():
    P = importlib.import_module(os.environ.get("PARITY_BUILDER_MODULE", "backtest_v8_precompute"))
    import ez_indicators as EZ
    P.MODE = "crypto"
    kdir = pathlib.Path(os.environ.get("PARITY_CRYPTO_KLINES", str(pathlib.Path(os.environ.get("BASE_PATH", str(ROOT))) / "klines_cache")))
    syms = os.environ.get("PARITY_CRYPTO_SYMS", "BTCUSDC").split(",")
    ncut = int(os.environ.get("PARITY_CRYPTO_CUTS", "8"))
    bad, compared = [], 0
    for s in syms:
        for tf in TFS:
            f = P.load_klines(kdir / f"{s}_{tf}.json")
            if f is None or len(f) < 450:
                continue
            f = f.tail(2000)
            b = P.compute_tf_arrays(f, tf)
            n = len(f)
            calc = EZ.IndicatorCalculator()
            for j in np.linspace(400, n - 1, ncut).astype(int):
                sl = f.iloc[: j + 1].copy()
                sl["timestamp_dt"] = sl.index
                sl = sl.reset_index(drop=True)
                live = calc.compute(sl, tf, None, False) or {}
                for k, v in live.items():
                    if (not k.endswith("_" + tf) and not k.endswith(f"_{tf}_prev")) or k not in b:
                        continue
                    a = np.asarray(b[k])
                    if a.ndim != 1 or len(a) != n:
                        continue
                    lv = _num(P, k, v)
                    bv = float(a[j]) if a.dtype.kind in "fiub" else _num(P, k, str(a[j]))
                    if lv is None or bv is None:
                        continue
                    compared += 1
                    tol = max(TOL, 1e-6 * abs(bv))  # float32 storage: absolute 5e-3, relative 1e-6 for large magnitudes
                    if abs(lv - bv) > tol:
                        bad.append((s, tf, int(j), k, lv, bv))
    return compared, bad


def test_crypto_builder_equals_live_ez_indicators():
    base = pathlib.Path(os.environ.get("BASE_PATH", str(ROOT)))
    kdir = pathlib.Path(os.environ.get("PARITY_CRYPTO_KLINES", str(base / "klines_cache")))
    if not (kdir / f"{os.environ.get('PARITY_CRYPTO_SYMS', 'BTCUSDC').split(',')[0]}_15m.json").exists():
        pytest.skip(f"no crypto klines under {kdir}")
    try:
        compared, bad = _audit()
    except ImportError as e:
        pytest.skip(f"indicator deps missing here: {e}")
    assert compared > 0
    fields = sorted({(k, tf) for _, tf, _, k, _, _ in bad})
    assert not bad, f"{len(bad)} live!=builder values over {len(fields)} field x TF, e.g. {bad[:5]}"
