#!/usr/bin/env python3
"""Evaluate verified 30D winner sets on the current seven-day NPZ window.

Every result is isolated from 30D progress/workbooks. Invalid or stale NPZs stay
invalid; this tool never promotes settings or places orders.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _cat_side(ss):
    sym, side = ss.rsplit("_", 1)
    return ("CRYPTO" if sym.endswith(("USDT", "USDC")) else "STOCKS") + "_" + side


def _atomic_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True) + "\n")
    os.replace(tmp, path)


def evaluate_one(ss, queue):
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
    from tools.opt.evaluate_v12 import resolve_tokenised
    from tools.v15_persym_golive import sanitize

    source = queue["sym_sides"][ss]
    rec = {"sym_side": ss, "window_days": 7, "source": "published_30D_best", "source_published_at": source.get("published_at"),
           "source_history_id": source.get("history_id"), "source_overrides_md5": source.get("overrides_md5"),
           "run_at": dt.datetime.now(dt.timezone.utc).isoformat(), "account_candidates": [a for a, sides in queue["accounts"].items() if ss in sides]}
    overrides = sanitize(source["overrides"], {})
    rec["evaluated_overrides_sha256"] = hashlib.sha256(json.dumps(overrides, sort_keys=True, default=str).encode()).hexdigest()
    rec["engine_sha256"] = hashlib.sha256((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()
    try:
        import numpy as np
        npz_sym, _ = resolve_tokenised(ss.rsplit("_", 1)[0])
        npz_path = ROOT / "backtest_v8" / "indicators" / f"{npz_sym}.npz"
        with np.load(npz_path, allow_pickle=False) as z:
            rec["npz_last_bar_ts"] = int(z["timestamps"][-1])
        rec["npz_path"] = str(npz_path)
    except Exception as exc:
        rec["npz_error"] = f"{type(exc).__name__}: {exc}"
    try:
        prepared = prepare_batch(ss, 7)
        ev = evaluate_prepared_sanitized(prepared, overrides, 7)
        rec["evidence"] = {k: ev.get(k) for k in ("valid", "invalid_reason", "gain_pct", "trades", "tim_pct", "max_dd_pct", "pool_sharpe", "window_days")}
        rec["valid"] = bool(ev.get("valid"))
    except Exception as exc:
        rec["valid"] = False
        rec["evidence"] = {"valid": False, "invalid_reason": f"{type(exc).__name__}: {exc}"}
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--cat-side", choices=("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"))
    ap.add_argument("--sym-side")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--timeout-sec", type=int, default=240)
    args = ap.parse_args()
    queue = json.loads(Path(args.queue).read_text())
    out = Path(args.out_dir)
    syms = [args.sym_side] if args.sym_side else sorted(queue["sym_sides"])
    if args.cat_side:
        syms = [ss for ss in syms if _cat_side(ss) == args.cat_side]

    def run(ss):
        p = out / f"{ss}.json"
        if args.sym_side:
            rec = evaluate_one(ss, queue)
            _atomic_json(p, rec)
            return ss, rec["valid"], rec["evidence"].get("invalid_reason")
        cmd = [sys.executable, __file__, "--queue", args.queue, "--out-dir", str(out), "--sym-side", ss]
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=args.timeout_sec)
            if r.returncode == 0 and p.exists():
                doc = json.loads(p.read_text())
                return ss, doc.get("valid"), (doc.get("evidence") or {}).get("invalid_reason")
            why = f"worker rc={r.returncode}: {r.stderr[-300:]}"
        except subprocess.TimeoutExpired:
            why = f"worker timeout after {args.timeout_sec}s"
        _atomic_json(p, {"sym_side": ss, "window_days": 7, "valid": False, "evidence": {"valid": False, "invalid_reason": why}})
        return ss, False, why

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for future in as_completed({pool.submit(run, ss): ss for ss in syms}):
            results.append(future.result())
    summary = {"cat_side": args.cat_side, "n": len(syms), "valid": sum(bool(x[1]) for x in results),
               "invalid": [x for x in sorted(results) if not x[1]],
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if not args.sym_side:
        _atomic_json(out / "summary.json", summary)
    print(json.dumps(summary))
    return 0 if not summary["invalid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
