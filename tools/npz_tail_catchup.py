#!/usr/bin/env python3
"""npz_tail_catchup - close a hole in a tail store by appending the missing closed 15m bars ONE ROW AT A TIME (no complete NPZ recompute).

2026-10-28 operator law: NPZs are never recomputed, only appended on S1 (crypto) and S5 (stocks). npz_tail_service appends only the latest closed bar,
so a symbol that joined the append universe late (or whose host was off) has a hole of N bars. This walks the klines' own closed bars in
(merged tip, last closed] and appends each with the same _compute_row_for_15m_close the service uses. A symbol with no kline for a bar is left alone.

usage: npz_tail_catchup.py --base ~/binance-sandbox --cat-side crypto|stocks [--symbols A,B] [--max-bars 300] [--workers 3] [--dry-run]
"""
import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def kline_closed_starts(base, symbol, mode, after_ts, upto_ts):
    from npz_live_generator import _load_klines_df, _resolve_kline_path

    path = _resolve_kline_path(symbol, "15m", mode, Path(base))
    if path is None:
        return []
    frame = _load_klines_df(path)
    if frame is None or frame.empty:
        return []
    import pandas as pd

    column = "timestamp" if "timestamp" in frame.columns else None
    index = pd.to_datetime(frame[column] if column else frame.index, utc=True)
    starts = sorted({int(t.timestamp()) for t in index})
    out = [t for t in starts if after_ts < t <= upto_ts]
    if mode == "tradier":
        from tools.npz_cutoffs import is_rth_bar_start

        out = [t for t in out if is_rth_bar_start(t)]
    return out


def catch_up_symbol(task):
    base, symbol, mode, max_bars, dry = task
    from npz_tail_store import append_tail_row, tail_len  # noqa: F401
    from tools.npz_tail_service import kline_last_close, npz_for, tip_of

    npz = npz_for(base, symbol)
    if npz is None:
        return symbol, "no-npz", 0
    last_close = kline_last_close(base, symbol, mode)
    tip = tip_of(npz)
    if last_close is None or tip is None:
        return symbol, "no-tip-or-kline", 0
    starts = kline_closed_starts(base, symbol, mode, int(tip), int(last_close))
    if not starts:
        return symbol, "current", 0
    if len(starts) > max_bars:
        return symbol, f"refused-gap-{len(starts)}-bars", 0
    if dry:
        return symbol, f"would-append-{len(starts)}", 0
    from tools.npz_tail_service import make_compute

    compute = make_compute(base, mode)
    done = 0
    for ts in starts:
        row = compute(symbol, int(ts))
        if not row:
            return symbol, f"compute-failed-at-{ts}", done
        final = dict(row, timestamps=int(ts))
        for tf in ("15m", "5m", "3m", "1m"):
            final[f"timestamp_{tf}"] = int(ts)
        if not append_tail_row(npz, final, now=time.time()):
            return symbol, f"append-refused-at-{ts}", done
        done += 1
    return symbol, "caught-up", done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--cat-side", required=True, choices=("crypto", "stocks"))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--max-bars", type=int, default=300)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from tools.npz_tail_service import load_universe, owned_symbols

    mode = "crypto" if a.cat_side == "crypto" else "tradier"
    syms = [s for s in a.symbols.split(",") if s] or sorted({s.rsplit("_", 1)[0] for s in owned_symbols(load_universe(Path(a.base)), a.cat_side)})
    tasks = [(a.base, s, mode, a.max_bars, a.dry_run) for s in syms]
    with ProcessPoolExecutor(max_workers=max(1, a.workers), mp_context=get_context("fork")) as pool:
        for future in as_completed([pool.submit(catch_up_symbol, t) for t in tasks]):
            symbol, status, n = future.result()
            if status != "current":
                print(f"{symbol} {status} rows={n}", flush=True)


if __name__ == "__main__":
    main()
