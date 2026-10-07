#!/usr/bin/env python3
"""365D confirmation certifier — the ONLY sanctioned writer of data/confirmed_365d.json.

USER MANDATE 2026-09-28: crypto sym_sides may only trade live when their recipe is
confirmed by the HIGHEST GAIN candidate on a 365D window (valid, gain>0, trades>=30).
ez_manage.execute_now blocks every position-increasing action without a fresh record
(CONFIRM_365D_MAX_AGE_DAYS, default 30 — re-confirm monthly or trading stops).

Candidates evaluated per sym_side (highest 365d gain wins, ties -> fewer trades):
  1. engine defaults (QuickConfig baseline)
  2. cumulative_overrides from data/reports/lifecycle_pilot/{SYM_SIDE}_v14_progress.json
  3. hustler_best.json entry for the sym_side (if present)
A record is written ONLY when the winner is valid AND gain_365d > 0 AND trades >= 30.
Otherwise any existing record for the sym_side is REMOVED (fail-closed) unless --keep-existing.

Usage:
  python tools/confirm_365d.py --sym-side ZECUSDC_LONG
  python tools/confirm_365d.py --all            # every sym_side with a progress JSON
  python tools/confirm_365d.py --list           # show current certifications
  python tools/confirm_365d.py --revoke KEY     # remove one record
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIRM_PATH = ROOT / "data" / "confirmed_365d.json"
# Default pilot dir; the mega sweep keeps per-sym_side progress under
# ~/v15_mega_progress/MEGA_<venue>_jump/<SYM_SIDE>/ — pass --progress-dir (repeatable) or set
# V15_PROGRESS_DIR to certify those in a separate pass (binance-99 request 2026-09-28: the mega
# pilots are frozen without the DONE-stage hook to avoid per-finish 365D prepares / OOM).
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
EXTRA_PROGRESS_DIRS: list = []


def _progress_jsons_for(symside: str) -> list:
    out = [PROGRESS_DIR / f"{symside}_v14_progress.json"]
    for d in EXTRA_PROGRESS_DIRS:
        d = Path(d)
        out.append(d / f"{symside}_v14_progress.json")
        out.append(d / symside / f"{symside}_v14_progress.json")
        if d.is_dir():
            out.extend(sorted(d.glob(f"**/{symside}*progress*.json"))[:3])
    seen = set(); uniq = []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p)); uniq.append(p)
    return uniq


def _load_confirmations() -> dict:
    if CONFIRM_PATH.exists():
        try:
            return json.loads(CONFIRM_PATH.read_text())
        except Exception:
            return {}
    return {}


def _atomic_write(data: dict):
    tmp = CONFIRM_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
    os.replace(tmp, CONFIRM_PATH)


def _candidates_for(symside: str) -> list:
    cands = [("defaults", {})]
    for pj in _progress_jsons_for(symside):
        if not pj.exists():
            continue
        try:
            d = json.loads(pj.read_text())
            co = d.get("cumulative_overrides") or {}
            if co and all(co != o for _, o in cands):
                cands.append((f"cumulative_overrides:{pj.parent.name}", dict(co)))
        except Exception as e:
            print(f"[warn] {symside}: {pj} unreadable ({e})")
    hb = ROOT / "hustler_best.json"
    if hb.exists():
        try:
            h = json.loads(hb.read_text())
            rec = h.get(symside) or {}
            ov = rec.get("overrides") or rec.get("cumulative_overrides") or {}
            if ov:
                cands.append(("hustler_best", dict(ov)))
        except Exception:
            pass
    return cands


def confirm_symside(symside: str, keep_existing: bool = False, window_days: int = 365) -> dict:
    """Evaluate all candidates at 365d, certify the highest-gain valid one. Returns the record or {}."""
    os.environ.setdefault("V12_NPZ_CACHE", "4")
    from tools.opt.v12_pilot import evaluate_sanitized
    results = []
    for name, overrides in _candidates_for(symside):
        try:
            r = evaluate_sanitized(symside, dict(overrides), window_days=window_days)
        except Exception as e:
            r = {"valid": False, "invalid_reason": f"eval error {e}", "gain_pct": None, "trades": 0}
        results.append((name, overrides, r))
        print(f"  [{symside}] {name}: gain_365d={r.get('gain_pct')} trades={r.get('trades')} valid={r.get('valid')} {r.get('invalid_reason') or ''}")
    ok = [(n, o, r) for n, o, r in results
          if r.get("valid") and r.get("gain_pct") is not None
          and float(r["gain_pct"]) > 0 and int(r.get("trades") or 0) >= 30]
    data = _load_confirmations()
    if not ok:
        if symside in data and not keep_existing:
            del data[symside]
            _atomic_write(data)
            print(f"  [{symside}] NO valid positive 365D candidate — existing record REMOVED (fail-closed)")
        else:
            print(f"  [{symside}] NO valid positive 365D candidate — not certified")
        return {}
    ok.sort(key=lambda x: (-float(x[2]["gain_pct"]), int(x[2].get("trades") or 0)))
    name, overrides, r = ok[0]
    rec = {
        "valid": True,
        "gain_365d": float(r["gain_pct"]),
        "bh_365d": r.get("bh_pct"),
        "trades": int(r.get("trades") or 0),
        "pool_sharpe": r.get("pool_sharpe"),
        "tim_pct": r.get("tim_pct"),
        "max_dd_pct": r.get("max_dd_pct"),
        "recipe_source": name,
        "recipe": overrides,
        "window_days": window_days,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "engine": "v12_quick_engine (vector) via tools.opt.v12_pilot.evaluate_sanitized",
    }
    data[symside] = rec
    _atomic_write(data)
    print(f"  [{symside}] CERTIFIED: {name} gain_365d={rec['gain_365d']:.2f}% trades={rec['trades']}")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--revoke")
    ap.add_argument("--keep-existing", action="store_true")
    ap.add_argument("--window-days", type=int, default=365)
    ap.add_argument("--progress-dir", action="append", default=[], help="extra progress dirs (e.g. ~/v15_mega_progress/MEGA_crypto_jump); V15_PROGRESS_DIR also honored")
    args = ap.parse_args()
    for _d in args.progress_dir + ([os.environ["V15_PROGRESS_DIR"]] if os.environ.get("V15_PROGRESS_DIR") else []):
        EXTRA_PROGRESS_DIRS.append(Path(os.path.expanduser(_d)))
    if args.list:
        data = _load_confirmations()
        print(f"{len(data)} certified sym_sides in {CONFIRM_PATH}")
        for k in sorted(data):
            r = data[k]
            print(f"  {k}: gain_365d={r.get('gain_365d')} trades={r.get('trades')} source={r.get('recipe_source')} at={r.get('confirmed_at')}")
        return
    if args.revoke:
        data = _load_confirmations()
        if args.revoke in data:
            del data[args.revoke]
            _atomic_write(data)
            print(f"revoked {args.revoke}")
        else:
            print(f"{args.revoke} not certified")
        return
    if args.sym_side:
        confirm_symside(args.sym_side, keep_existing=args.keep_existing, window_days=args.window_days)
        return
    if args.all:
        _names = {p.name.replace("_v14_progress.json", "") for p in PROGRESS_DIR.glob("*_v14_progress.json")}
        for _d in EXTRA_PROGRESS_DIRS:
            _d = Path(_d)
            if _d.is_dir():
                _names |= {q.name for q in _d.iterdir() if q.is_dir() and ("_LONG" in q.name or "_SHORT" in q.name)}
                _names |= {q.name.replace("_v14_progress.json", "") for q in _d.glob("**/*_v14_progress.json")}
        symsides = sorted(_names)
        print(f"confirming {len(symsides)} sym_sides at {args.window_days}d …")
        n_ok = 0
        for s in symsides:
            try:
                if confirm_symside(s, keep_existing=args.keep_existing, window_days=args.window_days):
                    n_ok += 1
            except Exception as e:
                print(f"  [{s}] ERROR {e}")
        print(f"done: {n_ok}/{len(symsides)} certified")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
