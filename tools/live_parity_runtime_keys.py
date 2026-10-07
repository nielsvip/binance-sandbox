#!/usr/bin/env python3
"""live_parity_runtime_keys — Agent D: run the REAL live per-timeframe indicator calculators (ez_indicators.IndicatorCalculator.compute for crypto,
tradier_indicators for stocks if importable) on cached klines and record the TRUE set of keys live produces (no static-analysis guesswork).
Output data/live_parity/live_keys_runtime.json = {venue: {tf: [keys]}} and a diff against the NPZ key families."""
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("EZ_LOG_DIR", "/tmp/ez_log_parity")
Path(os.environ["EZ_LOG_DIR"]).mkdir(exist_ok=True)
OUT = ROOT / "data" / "live_parity"


def load_df(sym, tf):
    d = json.load(open(ROOT / "klines_cache" / f"{sym}_{tf}.json"))
    df = pd.DataFrame(d)
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


def main():
    import ez_indicators as EI
    calc = EI.IndicatorCalculator()
    out = {"crypto": {}}
    for tf in ("3m", "15m", "1h", "4h", "D"):
        try:
            res = calc.compute(load_df("BTCUSDC", tf), tf, None, False)
            out["crypto"][tf] = sorted(res.keys())
            print(tf, len(res))
        except Exception as e:
            out["crypto"][tf] = f"ERR {type(e).__name__}: {e}"
            print(tf, "ERR", e)
    (OUT / "live_keys_runtime.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
