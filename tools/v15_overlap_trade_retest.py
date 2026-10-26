#!/usr/bin/env python3
"""Compare exact V12 trade events on the 28-day overlap of original/+2d windows."""
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def event_time(event, timestamps):
    raw = float(event.get("ts") or 0)
    if raw > 1e11:
        raw /= 1000.0
    if raw > 0:
        return raw
    bar = event.get("bar_exit", event.get("bar", event.get("bar_entry")))
    return float(timestamps[int(bar)]) if bar is not None and 0 <= int(bar) < len(timestamps) else 0.0


def evaluate_at(symbol_side, overrides, endpoint):
    import numpy as np
    from tools.opt import evaluate_v12 as E
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    from tools.v15_forward_state import _open_ledger_position

    old_start_date, old_store = E._frozen_npz_start_date, E._store
    end = float(endpoint) if endpoint is not None else time.time()
    symbol = symbol_side.rsplit("_", 1)[0]
    crypto = symbol.endswith(("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI"))
    def fixed_start(sym, window_days, offset_days=0):
        back = 45 if crypto else 90
        return dt.datetime.fromtimestamp(end - back * 86400, dt.timezone.utc).strftime("%Y-%m-%d")
    def cut_store(sym, mode, start):
        raw = old_store(sym, mode, start)
        if raw is None or endpoint is None:
            return raw
        ts = np.asarray(raw.get("timestamps", ()), dtype="float64")
        scale = 1000.0 if ts.size and ts[-1] > 1e11 else 1.0
        keep = ts <= end * scale
        stop = int(np.flatnonzero(keep)[-1] + 1) if keep.any() else 0
        return {key: (value[:stop] if isinstance(value, np.ndarray) and value.ndim and len(value) == len(ts) else value) for key, value in raw.items()}
    E._frozen_npz_start_date, E._store = fixed_start, cut_store
    try:
        prep = E.prepare(symbol_side, 30, sim_account="trb")
        if prep is None:
            return {"ok": False, "reason": "prepare_failed"}
        result = evaluate_prepared_sanitized(prep, dict(overrides), 30, include_ledger=True) or {}
        timestamps = np.asarray((prep.get("npz_prepared") or {}).get("timestamps", ()), dtype="float64")
        tail = float(timestamps[-1]) if timestamps.size else 0.0
        if tail > 1e11:
            tail /= 1000.0
        ledger = [row for row in (result.get("ledger") or []) if isinstance(row, dict)]
        trades = []
        for row in ledger:
            kind = str(row.get("type") or "").upper()
            if kind not in ("OPEN", "AUGMENT", "REDUCE", "CLOSE"):
                continue
            ts = event_time(row, timestamps)
            if ts:
                trades.append({"ts": ts, "type": kind, "reason": str(row.get("reason") or row.get("exit_reason") or row.get("entry_reason") or ""),
                               "qty": row.get("qty"), "price": row.get("price")})
        current = _open_ledger_position(ledger, len(timestamps) - 1)
        return {"ok": True, "tail": tail, "gain_pct": result.get("gain_pct"), "trades_n": result.get("trades"),
                "performance_valid": result.get("valid"), "invalid_reason": result.get("invalid_reason"),
                "open": current is not None, "events": trades}
    finally:
        E._frozen_npz_start_date, E._store = old_start_date, old_store


def signatures(result, start, end):
    rows = []
    for row in result.get("events", []):
        if start <= float(row["ts"]) <= end:
            rows.append((int(float(row["ts"]) // 900), row["type"], row["reason"], row.get("qty"), row.get("price")))
    return rows


def main():
    import argparse
    import hashlib
    import per_sym_live_set
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = json.loads(Path(args.rows).read_text())
    out = []
    cache = {}
    for i, job in enumerate(rows, 1):
        ss = job["sym_side"]
        overrides = per_sym_live_set._apply_caps(ss, job.get("full_config") or {})
        original_end = float(job.get("original_end_ts") or job["campaign_ts"])
        key = (ss, job.get("settings_sha256"), int(original_end // 900))
        try:
            if key not in cache:
                original = evaluate_at(ss, overrides, original_end)
                plus2 = evaluate_at(ss, overrides, original_end + 2 * 86400)
                latest = evaluate_at(ss, overrides, None)
                cache[key] = {"original": original, "plus2d": plus2, "latest": latest}
            result = cache[key]
            original, plus2, latest = result["original"], result["plus2d"], result["latest"]
            overlap_start = max(float(original.get("tail", 0)) - 28 * 86400, float(plus2.get("tail", 0)) - 30 * 86400)
            overlap_end = min(float(original.get("tail", 0)), float(plus2.get("tail", 0)) - 2 * 86400)
            a = signatures(original, overlap_start, overlap_end)
            b = signatures(plus2, overlap_start, overlap_end)
            latest_start = max(float(original.get("tail", 0)) - 30 * 86400, float(latest.get("tail", 0)) - 30 * 86400)
            latest_end = min(float(original.get("tail", 0)), float(latest.get("tail", 0)))
            c = signatures(original, latest_start, latest_end)
            d = signatures(latest, latest_start, latest_end)
            out.append({"id": job.get("id"), "sym_side": ss, "settings_sha256": job.get("settings_sha256"),
                        "settings_source": job.get("settings_source"), "template_md5": job.get("template_md5"),
                        "original": {k: v for k, v in original.items() if k != "events"},
                        "plus2d": {k: v for k, v in plus2.items() if k != "events"},
                        "latest": {k: v for k, v in latest.items() if k != "events"},
                        "overlap_28d": {"start": overlap_start, "end": overlap_end, "original_events": len(a), "plus2d_events": len(b), "identical": a == b},
                        "original_vs_latest_overlap": {"start": latest_start, "end": latest_end, "original_events": len(c), "latest_events": len(d), "identical": c == d}})
        except Exception as exc:
            out.append({"id": job.get("id"), "sym_side": ss, "error": f"{type(exc).__name__}:{exc}"[:240]})
        if i % 10 == 0 or i == len(rows):
            print(f"{i}/{len(rows)} {ss}", flush=True)
    Path(args.out).write_text("\n".join(json.dumps(row, default=str) for row in out) + "\n")
    eligible = [row for row in out if "overlap_28d" in row]
    print(json.dumps({"rows": len(out), "unique_replays": len(cache), "exact_28d_overlap": sum(row["overlap_28d"]["identical"] for row in eligible),
                      "latest_overlap_identical": sum(row["original_vs_latest_overlap"]["identical"] for row in eligible), "report": args.out}, sort_keys=True))


if __name__ == "__main__":
    main()
