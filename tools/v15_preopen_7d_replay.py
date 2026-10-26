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
import shutil
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_preopen_7d_stage import digest, validate_one  # noqa: E402

NY = ZoneInfo("America/New_York")


def replay_one(queue_path: Path, result_dir: Path, ss: str,
               now: dt.datetime | None = None, bar_minute: int = 15) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    local = now.astimezone(NY)
    if bar_minute not in (0, 15):
        raise ValueError("premarket replay bar must be 09:00 or 09:15 ET")
    cutoff = local.replace(hour=9, minute=bar_minute, second=0, microsecond=0)
    if local.weekday() >= 5 or local < cutoff:
        raise ValueError("09:15 ET premarket snapshot has not arrived")
    queue = json.loads(queue_path.read_text())
    meta = queue.get("_meta", {})
    if (meta.get("scope") != "fin_men_crypto" or meta.get("window_days") != 7
            or meta.get("fin_book_source") != "men" or meta.get("require_replay") is not True
            or queue.get("accounts", {}).get("trc")
            or set(queue.get("accounts", {}).get("fin", [])) != set(queue.get("sym_sides", {}))):
        raise ValueError("replay accepts only the current SQL-backed FIN/MEN crypto 7D queue")
    source = queue["sym_sides"][ss]
    if (source.get("source_kind") != "per_sym_sql" or source.get("sql_baseline_ok") is not True
            or not str(source.get("cat_side", "")).startswith("CRYPTO_")
            or ss not in queue.get("accounts", {}).get("fin", [])):
        raise ValueError(f"{ss}: replay source is not an authorized FIN/MEN crypto SQL side")
    from tools.opt.evaluate_v12 import is_crypto, resolve_tokenised
    symbol = ss.rsplit("_", 1)[0]
    if not is_crypto(symbol) or resolve_tokenised(symbol)[1]:
        raise ValueError(f"{ss}: replay only supports non-tokenized crypto")
    result = validate_one(ss, source, result_dir, now.date().isoformat())
    full = result["full_config"]
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
    from tools.opt.evaluate_v12 import resolve_tokenised, is_crypto
    import numpy as np

    symbol, _ = resolve_tokenised(ss.rsplit("_", 1)[0])
    stock_overlay = not is_crypto(ss.rsplit("_", 1)[0])
    overlay_dir = result_dir / ss / "replay_npz_overlay"
    overlay = None
    try:
        if stock_overlay:
            from tools.v15_preopen_7d_npz import build_overlay, install_overlay_for_process
            overlay = build_overlay(symbol, overlay_dir, now)
            os.environ["V12_NPZ_DIR"] = str(overlay_dir)
            overlay_freshness = install_overlay_for_process()
            npz = overlay_dir / f"{symbol}.npz"
        else:
            npz = ROOT / "backtest_v8/indicators" / f"{symbol}.npz"
        with np.load(npz, allow_pickle=False) as z:
            last = float(z["timestamps"][-1])
        last_utc = dt.datetime.fromtimestamp(last / (1000 if last > 1e11 else 1), dt.timezone.utc)
        if stock_overlay:
            latest_premarket = dt.datetime.fromtimestamp(max(overlay["premarket_rows"]), dt.timezone.utc)
            if latest_premarket > now.astimezone(dt.timezone.utc) or latest_premarket.astimezone(NY).date() != local.date():
                raise ValueError(f"{ss}: temporary NPZ premarket rows are not current-day completed bars")
        if stock_overlay:
            fresh, why = overlay_freshness(ss)
        else:
            from tools.opt.v12_pilot import npz_freshness
            fresh, why = npz_freshness(ss)
        if not fresh:
            raise ValueError(f"{ss}: stale replay NPZ: {why}")
        prepared = prepare_batch(ss, 7)
        if prepared.get("_stale"):
            raise ValueError(f"{ss}: replay preparation stale: {prepared['_stale']}")
        ev = evaluate_prepared_sanitized(prepared, full, 7)
    finally:
        if stock_overlay:
            os.environ.pop("V12_NPZ_DIR", None)
        shutil.rmtree(overlay_dir, ignore_errors=True)
    evidence = {k: ev.get(k) for k in ("valid", "invalid_reason", "gain_pct", "trades", "tim_pct", "max_dd_pct", "window_days")}
    record = {"sym_side": ss, "account": next(a for a, sides in queue["accounts"].items() if ss in sides),
              "evaluated_at_utc": now.isoformat(), "bar_cutoff_et": cutoff.isoformat(),
              "npz_last_bar_utc": last_utc.isoformat(), "npz_mtime_utc": dt.datetime.fromtimestamp(npz.stat().st_mtime, dt.timezone.utc).isoformat(),
              "source_30d_sha256": digest(source["overrides"]), "optimized_full_sha256": digest(full),
              "manifest_path": result["manifest_path"], "evidence": evidence,
              "npz_overlay_sha256": overlay["overlay_sha256"] if overlay else None,
              "premarket_row_count": overlay["premarket_row_count"] if overlay else 0,
              "valid": ev.get("valid") is True and int(ev.get("trades") or 0) >= 10}
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--result-dir", type=Path, required=True)
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--bar-minute", type=int, choices=(0, 15), default=15)
    args = ap.parse_args()
    path = args.result_dir / args.sym_side / "replay.json"
    try:
        rec = replay_one(args.queue, args.result_dir, args.sym_side, bar_minute=args.bar_minute)
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
