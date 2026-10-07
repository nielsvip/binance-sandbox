#!/usr/bin/env python3
"""Stocks replay harness for batch1 (Agent D): run tradier_indicators.IndicatorCalculator.compute on a recent window of REAL stock klines (klines_cache/<SYM>USDT_<tf>.json)
with the CURRENT live module and with the STAGED batch1 module, at many successive bars, and diff: (a) every key existing in the live output must be unchanged,
(b) list the keys batch1 adds, (c) compare the new close_3bar/5bar/ema_9_above_21/volume_sma_1h/choppiness_4h/wt_*_value/ha_green/velocity_prev against reference formulas.
  python tools/live_parity_stocks_replay.py [SYMBOL ...]   (default AAPL MSTR CRM)   exit 0 = no existing key changed"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("EZ_LOG_DIR", "/tmp/ez_log_parity")
STAGED = ROOT / "data/live_parity/staged/batch1/files/tradier_indicators.py"


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_df(sym, tf):
    d = pd.DataFrame(json.load(open(ROOT / "klines_cache" / f"{sym}USDT_{tf}.json")))
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["close"]).reset_index(drop=True)


def same(a, b):
    if a is b:
        return True
    try:
        if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
            return True
        return a == b
    except Exception:
        return str(a) == str(b)


def main():
    syms = sys.argv[1:] or ["AAPL", "MSTR", "CRM"]
    live = load_mod("tradier_indicators_live", ROOT / "tradier_indicators.py")
    stg = load_mod("tradier_indicators_staged", STAGED)
    changed_total, new_keys, n_runs, errors = 0, set(), 0, []
    ref_checks = {}
    for sym in syms:
        for tf in ("15m", "1h", "4h"):
            try:
                full = load_df(sym, tf)
            except Exception as e:
                errors.append(f"{sym} {tf}: {e}")
                continue
            if len(full) < 260:
                errors.append(f"{sym} {tf}: only {len(full)} bars")
                continue
            for end in range(len(full) - 8, len(full) + 1, 2):
                df = full.iloc[:end].copy()
                try:
                    a = live.IndicatorCalculator().compute(df.copy(), sym, tf, None, None, False)
                    b = stg.IndicatorCalculator().compute(df.copy(), sym, tf, None, None, False)
                except Exception as e:
                    errors.append(f"{sym} {tf} end={end}: {type(e).__name__}: {e}")
                    continue
                n_runs += 1
                for k, v in a.items():
                    if k in ("_tick_ts", "ts") or k.endswith("_mid") or k.endswith("_updated_at") or k.startswith("timestamp_1m"):  # wall-clock stamps
                        continue
                    if k not in b or not same(v, b[k]):
                        changed_total += 1
                        errors.append(f"CHANGED {sym} {tf} {k}: {v!r} -> {b.get(k, 'MISSING')!r}")
                new_keys |= set(b) - set(a)
                c = df["close"].astype(float)
                exp = {f"close_3bar_{tf}": float(c.rolling(3, min_periods=1).mean().iloc[-1]), f"close_5bar_{tf}": float(c.rolling(5, min_periods=1).mean().iloc[-1])}
                for k, e in exp.items():
                    if k in b:
                        ref_checks.setdefault(k, []).append(abs(b[k] - e) / (abs(e) + 1e-12))
    print(json.dumps({"runs": n_runs, "existing_keys_changed": changed_total, "new_keys": sorted(new_keys),
                      "ref_max_rel_diff": {k: float(max(v)) for k, v in ref_checks.items()}, "errors_sample": errors[:12], "n_errors": len(errors)}, indent=1))
    return 1 if changed_total else 0


if __name__ == "__main__":
    sys.exit(main())
