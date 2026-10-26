#!/usr/bin/env python3
"""Rerun each stored V15 result two days later with its exact full switch set."""
import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


def export_rows(db, asset, target):
    import per_sym_store
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute("SELECT id,updated_at,sym_side,full_config_json,overrides_json,defaults_snapshot_json,template_md5,meta_json,gain_pct,trades FROM per_sym_history WHERE updated_at >= '2026-10-19T00:00:00Z' AND updated_at < '2026-10-22T00:00:00Z' ORDER BY updated_at,id").fetchall()
    finally:
        con.close()
    out = []
    for row_id, updated, ss, raw, overrides_raw, defaults_raw, template_md5, meta_raw, gain, trades in rows:
        is_crypto = ss.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIXES)
        if (asset == "crypto") != is_crypto:
            continue
        try:
            if raw:
                full = json.loads(per_sym_store._maybe_decompress(raw))
                settings_source = "full_config_json"
            else:
                defaults = json.loads(per_sym_store._maybe_decompress(defaults_raw)) if defaults_raw else {}
                overrides = json.loads(per_sym_store._maybe_decompress(overrides_raw)) if overrides_raw else {}
                full = {**defaults, **overrides}
                settings_source = "defaults_snapshot+overrides_json" if defaults else "overrides_json_only"
            meta = json.loads(meta_raw or "{}")
        except Exception as exc:
            out.append({"id": row_id, "updated_at": updated, "sym_side": ss, "error": f"settings_decode:{exc}"})
            continue
        campaign = float(meta.get("campaign_ts") or dt.datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp())
        out.append({"id": row_id, "updated_at": updated, "sym_side": ss, "full_config": full, "settings_source": settings_source,
                    "template_md5": template_md5,
                    "settings_sha256": hashlib.sha256(json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest(),
                    "campaign_ts": campaign, "npz_id": meta.get("npz_id"), "published_gain_pct": gain,
                    "published_trades": trades})
    Path(target).write_text(json.dumps(out, separators=(",", ":")))
    print(json.dumps({"asset": asset, "rows": len(out), "distinct_sides": len({r['sym_side'] for r in out}), "output": str(target)}))


def retest(rows_path, db, output):
    import numpy as np
    import per_sym_live_set
    import v12_quick_engine as V
    from tools.opt import evaluate_v12 as E
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    from tools.v15_forward_state import _open_ledger_position

    rows = json.loads(Path(rows_path).read_text())
    results = []
    cache = {}
    for index, job in enumerate(rows, 1):
        base = {key: job.get(key) for key in ("id", "updated_at", "sym_side", "settings_sha256", "settings_source", "template_md5", "npz_id", "published_gain_pct", "published_trades")}
        if job.get("error"):
            results.append({**base, "status": "ERROR", "reason": job["error"]})
            continue
        ss = job["sym_side"]
        symbol = ss.rsplit("_", 1)[0]
        crypto = symbol.endswith(CRYPTO_SUFFIXES)
        target = float(job["campaign_ts"]) + 2 * 86400
        cache_key = (ss, job["settings_sha256"], int(target // 900))
        try:
            if cache_key not in cache:
                old_start_date = E._frozen_npz_start_date
                old_store = E._store
                endpoint = target
                def fixed_start(sym, window_days, offset_days=0):
                    back = 45 if crypto else 90
                    return dt.datetime.fromtimestamp(endpoint - back * 86400, dt.timezone.utc).strftime("%Y-%m-%d")
                def cut_store(sym, mode, start):
                    raw = old_store(sym, mode, start)
                    if raw is None:
                        return None
                    ts = np.asarray(raw.get("timestamps", ()), dtype="float64")
                    scale = 1000.0 if ts.size and ts[-1] > 1e11 else 1.0
                    keep = ts <= endpoint * scale
                    stop = int(np.flatnonzero(keep)[-1] + 1) if keep.any() else 0
                    return {key: (value[:stop] if isinstance(value, np.ndarray) and value.ndim and len(value) == len(ts) else value) for key, value in raw.items()}
                E._frozen_npz_start_date = fixed_start
                E._store = cut_store
                try:
                    prep = E.prepare(ss, 30, sim_account="trb")
                    if prep is None:
                        value = {"status": "NO_NPZ"}
                    else:
                        overrides = per_sym_live_set._apply_caps(ss, job["full_config"])
                        replay = evaluate_prepared_sanitized(prep, dict(overrides), 30, include_ledger=True) or {}
                        timestamps = np.asarray((prep.get("npz_prepared") or {}).get("timestamps", ()))
                        ledger = [event for event in (replay.get("ledger") or []) if isinstance(event, dict)]
                        current = _open_ledger_position(ledger, len(timestamps) - 1)
                        tail = int(timestamps[-1]) if timestamps.size else 0
                        value = {"status": "REPLAYED", "target_ts": target, "npz_tail": tail,
                                 "gain_pct": replay.get("gain_pct"), "trades": replay.get("trades"),
                                 "performance_valid": replay.get("valid"), "performance_invalid_reason": replay.get("invalid_reason"),
                                 "open": current is not None}
                finally:
                    E._frozen_npz_start_date = old_start_date
                    E._store = old_store
                cache[cache_key] = value
            else:
                value = cache[cache_key]
            results.append({**base, **value})
        except Exception as exc:
            results.append({**base, "status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"[:240]})
        if index % 25 == 0 or index == len(rows):
            print(f"{index}/{len(rows)} {ss} {results[-1].get('status')} cache={len(cache)}", flush=True)
    Path(output).write_text("\n".join(json.dumps(row, default=str) for row in results) + "\n")
    counts = {}
    for row in results:
        counts[row.get("status", "?")] = counts.get(row.get("status", "?"), 0) + 1
    mismatches = sum(1 for row in results if row.get("status") == "REPLAYED" and
                     (row.get("trades") != row.get("published_trades") or
                      row.get("gain_pct") is None or row.get("published_gain_pct") is None or
                      abs(float(row["gain_pct"]) - float(row["published_gain_pct"])) > 1e-4))
    print(json.dumps({"rows": len(results), "unique_replays": len(cache), "counts": counts,
                      "two_day_metric_mismatches_vs_publication": mismatches, "report": str(output)}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-db")
    parser.add_argument("--asset", choices=("crypto", "stocks"), default="crypto")
    parser.add_argument("--rows")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.export_db:
        export_rows(args.export_db, args.asset, args.out)
    elif args.rows:
        retest(args.rows, None, args.out)
    else:
        parser.error("provide --export-db or --rows")


if __name__ == "__main__":
    main()
