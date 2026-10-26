#!/usr/bin/env python3
"""Recheck an optimized 7D set on the 09:15 ET premarket NPZ snapshot.

The 15m row labelled 09:15 is forming at 09:15. The latest fully closed row
at that time is labelled 09:00. This replay requires a freshly written 09:15
row, then uses the engine's normal prepared window and records its exact NPZ.
It only writes evidence and never promotes or places orders.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_preopen_7d_stage import digest, validate_one  # noqa: E402

NY = ZoneInfo("America/New_York")


def replay_one(queue_path: Path, result_dir: Path, ss: str,
               now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    local = now.astimezone(NY)
    cutoff = local.replace(hour=9, minute=15, second=0, microsecond=0)
    if local.weekday() >= 5 or local < cutoff:
        raise ValueError("09:15 ET premarket snapshot has not arrived")
    queue = json.loads(queue_path.read_text())
    source = queue["sym_sides"][ss]
    result = validate_one(ss, source, result_dir, now.date().isoformat())
    full = result["full_config"]
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, npz_freshness, prepare_batch
    from tools.opt.evaluate_v12 import resolve_tokenised
    import numpy as np

    symbol, _ = resolve_tokenised(ss.rsplit("_", 1)[0])
    npz = ROOT / "backtest_v8/indicators" / f"{symbol}.npz"
    with np.load(npz, allow_pickle=False) as z:
        last = float(z["timestamps"][-1])
    last_utc = dt.datetime.fromtimestamp(last / (1000 if last > 1e11 else 1), dt.timezone.utc)
    if last_utc < cutoff.astimezone(dt.timezone.utc) or npz.stat().st_mtime < cutoff.timestamp():
        raise ValueError(f"{ss}: 09:15 ET NPZ row has not arrived")
    fresh, why = npz_freshness(ss)
    if not fresh:
        raise ValueError(f"{ss}: stale replay NPZ: {why}")
    prepared = prepare_batch(ss, 7)
    if prepared.get("_stale"):
        raise ValueError(f"{ss}: replay preparation stale: {prepared['_stale']}")
    ev = evaluate_prepared_sanitized(prepared, full, 7)
    evidence = {k: ev.get(k) for k in ("valid", "invalid_reason", "gain_pct", "trades", "tim_pct", "max_dd_pct", "window_days")}
    record = {"sym_side": ss, "account": next(a for a, sides in queue["accounts"].items() if ss in sides),
              "evaluated_at_utc": now.isoformat(), "bar_cutoff_et": cutoff.isoformat(),
              "npz_last_bar_utc": last_utc.isoformat(), "npz_mtime_utc": dt.datetime.fromtimestamp(npz.stat().st_mtime, dt.timezone.utc).isoformat(),
              "source_30d_sha256": digest(source["overrides"]), "optimized_full_sha256": digest(full),
              "manifest_path": result["manifest_path"], "evidence": evidence,
              "valid": ev.get("valid") is True and int(ev.get("trades") or 0) >= 10}
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--result-dir", type=Path, required=True)
    ap.add_argument("--sym-side", required=True)
    args = ap.parse_args()
    path = args.result_dir / args.sym_side / "replay.json"
    try:
        rec = replay_one(args.queue, args.result_dir, args.sym_side)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rec = {"sym_side": args.sym_side, "valid": False, "why": str(exc),
               "evaluated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True, default=str) + "\n")
    os.replace(tmp, path)
    print(json.dumps({"sym_side": args.sym_side, "valid": rec["valid"], "why": rec.get("why")}))
    return 0 if rec["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
