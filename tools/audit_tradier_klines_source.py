#!/usr/bin/env python3
"""Read-only provenance/coverage audit for Tradier kline source selection."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest_v8_precompute import load_klines


def frame_stats(path: Path) -> dict:
    df = load_klines(path)
    if df is None or df.empty:
        return {"path": str(path), "exists": path.exists(), "rows": 0}
    close = df["close"].astype(float)
    returns = close.pct_change().replace([np.inf, -np.inf], np.nan).abs()
    top_jump_idx = returns.nlargest(5).index
    top_jumps = []
    for idx in top_jump_idx:
        loc = int(df.index.get_loc(idx))
        if loc <= 0:
            continue
        top_jumps.append({
            "timestamp": idx.isoformat(),
            "previous_timestamp": df.index[loc - 1].isoformat(),
            "previous_close": float(close.iloc[loc - 1]),
            "close": float(close.iloc[loc]),
            "jump_pct": round(float(returns.iloc[loc]) * 100.0, 4),
        })
    by_day = pd.Series(1, index=df.index).groupby(df.index.date).sum()
    gaps = df.index.to_series().diff().dt.total_seconds().div(3600.0)
    top_gap_idx = gaps.nlargest(5).index
    top_gaps = []
    for idx in top_gap_idx:
        loc = int(df.index.get_loc(idx))
        if loc <= 0:
            continue
        top_gaps.append({
            "previous_timestamp": df.index[loc - 1].isoformat(),
            "timestamp": idx.isoformat(),
            "gap_hours": round(float(gaps.iloc[loc]), 3),
        })
    return {
        "path": str(path),
        "exists": True,
        "rows": int(len(df)),
        "start": df.index[0].isoformat(),
        "end": df.index[-1].isoformat(),
        "span_days": round((df.index[-1] - df.index[0]).total_seconds() / 86400.0, 3),
        "finite_ohlcv_pct": round(
            float(np.isfinite(df[["open", "high", "low", "close", "volume"]].astype(float)).mean().mean())
            * 100.0,
            4,
        ),
        "unique_close": int(close.nunique()),
        "min_close": float(close.min()),
        "max_close": float(close.max()),
        "max_consecutive_jump_pct": round(float(returns.max() or 0.0) * 100.0, 4),
        "jumps_gt_30pct": int((returns > 0.30).sum()),
        "top_jumps": top_jumps,
        "days": int(len(by_day)),
        "median_rows_per_day": float(by_day.median()),
        "max_gap_hours": round(float(gaps.max() or 0.0), 3),
        "gaps_gt_7d": int((gaps > 24 * 7).sum()),
        "top_gaps": top_gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument(
        "--root",
        default=str(ROOT / "klines_cache_backtest" / "tradier"),
    )
    args = parser.parse_args()
    source_root = Path(args.root)
    payload = {
        tf: frame_stats(source_root / f"{args.symbol.upper()}_{tf}.json")
        for tf in ("1m", "5m", "15m", "1h", "4h", "D")
    }
    valid = {tf: row for tf, row in payload.items() if row.get("rows", 0) >= 30}
    if "5m" in valid and (
        "15m" not in valid or valid["15m"]["span_days"] < valid["5m"]["span_days"] * 0.80
    ):
        selected = "5m"
    elif "15m" in valid:
        selected = "15m"
    else:
        selected = None
    print(json.dumps({"symbol": args.symbol.upper(), "selected_htf_source": selected, "frames": payload}, sort_keys=True))
    return 0 if selected else 2


if __name__ == "__main__":
    raise SystemExit(main())
