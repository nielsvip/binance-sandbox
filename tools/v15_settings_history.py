#!/usr/bin/env python3
"""v15_settings_history — USER 2026-10-13: "make sure the db written by the fleet orchestrator with each sym_side's
historical settings day by day is intact and updated when the json is written for any sym_side as soon as its new
settings have been calculated."

Runs on the orchestrator (s1). Scans every host's round progress JSONs, and for each board that has just PUBLISHED a
final set (top-level numeric `final_gain`, no `needs_redo`) appends ONE row to `per_sym_history` in
`data/hourly_reconfig/per_sym_store.db` — the same table + columns `per_sym_store.upsert()` writes, so the day-by-day
trail stays queryable by the existing readers.

Deliberately HISTORY-ONLY: it never touches `per_sym_active`. Live per-sym settings stay behind the go-live gate
(365D verification, artefact stripping, blocklists) — a freshly calculated 30D set is evidence, not a promotion.

Idempotent: a board is recorded once per (sym_side, final_gain, npz id, day). Re-running is a no-op. Integrity is
checked before writing and the DB is left untouched on any failure.

Usage:
  python3 tools/v15_settings_history.py [--hosts 127.0.0.1,10.0.0.4,10.0.0.5,10.0.0.6] [--window-h 24] [--dry-run]
"""
import argparse
import datetime
import glob
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("PER_SYM_STORE_DB") or os.path.join(ROOT, "data", "hourly_reconfig", "per_sym_store.db")
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]

COLLECT = r'''
import glob, json, os, time
out = []
win = float(%(win)s)
now = time.time()
for d in glob.glob(os.path.expanduser("~/v15_run*/progress")) + [os.path.expanduser("~/binance-sandbox/data/reports/lifecycle_pilot")]:
    for f in glob.glob(os.path.join(d, "*_v14_progress.json")):
        try:
            if now - os.path.getmtime(f) > win * 3600.0:
                continue
            j = json.load(open(f))
        except Exception:
            continue
        fg = j.get("final_gain")
        if not isinstance(fg, (int, float)) or isinstance(fg, bool) or j.get("needs_redo"):
            continue
        ov = j.get("cumulative_overrides") or {}
        if not isinstance(ov, dict) or not ov:
            continue
        dr = j.get("diagnose_repair") or {}
        after = dr.get("after") if isinstance(dr.get("after"), dict) else {}
        out.append({
            "sym_side": os.path.basename(f)[: -len("_v14_progress.json")],
            "final_gain": float(fg),
            "baseline_gain": j.get("baseline_gain"),
            "bh": j.get("bh"),
            "trades": j.get("final_trades") or after.get("trades"),
            "tim": after.get("tim"),
            "dd": after.get("dd"),
            "pool_sharpe": (j.get("final_365d") or {}).get("pool_sharpe") if isinstance(j.get("final_365d"), dict) else None,
            "verdict": j.get("verdict"),
            "defaults_round": j.get("defaults_round"),
            "npz": (j.get("npz_id") or {}).get("md5") or (j.get("npz_id") or {}).get("mtime_ns"),
            "result_done_utc": j.get("result_done_utc"),
            "mtime": os.path.getmtime(f),
            "path": f,
            "overrides": ov,
            "n_overrides": len(ov),
        })
print(json.dumps(out))
'''


def collect(host, win):
    code = COLLECT % {"win": win}
    if host in ("127.0.0.1", "localhost", "local"):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    else:
        # the collector is piped on STDIN: shell-quoting a multi-line script through ssh mangles it (observed:
        # every remote host silently returned 0 boards while s2/s5/s6 had published results).
        r = subprocess.run(SSH + [f"niels@{host}", "python3 -"], input=code, capture_output=True, text=True, timeout=300)
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception:
        return []


def cat_side_of(ss):
    sym, _, side = ss.rpartition("_")
    return f"{'CRYPTO' if sym.upper().endswith(CRYPTO_SUFFIX) else 'STOCKS'}_{side}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", default=os.environ.get("V15_HIST_HOSTS", "127.0.0.1,10.0.0.4,10.0.0.5,10.0.0.6"))
    ap.add_argument("--window-h", type=float, default=float(os.environ.get("V15_HIST_WINDOW_H", "24")))
    ap.add_argument("--db", default=DB)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(json.dumps({"error": f"db missing: {args.db}"}))
        return 2
    con = sqlite3.connect(args.db, timeout=30.0)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        print(json.dumps({"error": f"integrity_check={ok} — refusing to write", "db": args.db}))
        return 3
    cols = {r[1] for r in con.execute("PRAGMA table_info(per_sym_history)").fetchall()}
    if not cols:
        print(json.dumps({"error": "per_sym_history table missing — run per_sym_store ensure first"}))
        return 3
    found, wrote, dup = 0, 0, 0
    per_host = {}
    for host in [h for h in args.hosts.split(",") if h.strip()]:
        rows = collect(host.strip(), args.window_h)
        per_host[host] = len(rows)
        for r in rows:
            found += 1
            ss = r["sym_side"]
            day = (str(r.get("result_done_utc") or "")[:10]) or datetime.datetime.utcfromtimestamp(r["mtime"]).strftime("%Y-%m-%d")
            # idempotency: same sym_side + same measured gain + same day already recorded -> skip
            hit = con.execute(
                "SELECT 1 FROM per_sym_history WHERE sym_side=? AND substr(updated_at,1,10)=? AND gain_pct IS NOT NULL "
                "AND abs(gain_pct - ?) < 1e-9 LIMIT 1", (ss, day, float(r["final_gain"]))).fetchone()
            if hit:
                dup += 1
                continue
            if args.dry_run:
                wrote += 1
                continue
            now_iso = datetime.datetime.utcfromtimestamp(r["mtime"]).strftime("%Y-%m-%dT%H:%M:%SZ")
            meta = {"source": "fleet_orchestrator_publish", "host": host, "progress": r["path"], "npz": r.get("npz"),
                    "verdict": r.get("verdict"), "n_overrides": r.get("n_overrides"), "recorded_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
            try:
                delta = (float(r["final_gain"]) - float(r["baseline_gain"])) if r.get("baseline_gain") is not None else None
            except (TypeError, ValueError):
                delta = None
            con.execute(
                "INSERT INTO per_sym_history (sym_side, updated_at, epoch, cat_side, template_md5, defaults_round, "
                "winning_tag, gain_pct, baseline_gain, delta_pct, bh_pct, trades, pool_sharpe, max_dd_pct, tim_pct, "
                "valid, overrides_json, full_config_json, defaults_snapshot_json, meta_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ss, now_iso, float(r["mtime"]), cat_side_of(ss), "", str(r.get("defaults_round") or ""),
                 f"pilot30d_{day}", float(r["final_gain"]),
                 _f(r.get("baseline_gain")), delta, _f(r.get("bh")), _i(r.get("trades")), _f(r.get("pool_sharpe")),
                 _f(r.get("dd")), _f(r.get("tim")), 1 if r.get("verdict") not in ("IMPOSSIBLE", "NO_TRADES") else 0,
                 json.dumps(r["overrides"], sort_keys=True), None, None, json.dumps(meta)))
            wrote += 1
    if not args.dry_run:
        con.commit()
    total = con.execute("SELECT COUNT(*) FROM per_sym_history").fetchone()[0]
    days = con.execute("SELECT COUNT(DISTINCT substr(updated_at,1,10)) FROM per_sym_history").fetchone()[0]
    syms = con.execute("SELECT COUNT(DISTINCT sym_side) FROM per_sym_history").fetchone()[0]
    newest = con.execute("SELECT MAX(updated_at) FROM per_sym_history").fetchone()[0]
    con.close()
    print(json.dumps({"db": args.db, "integrity": ok, "per_host_boards": per_host, "found": found,
                      "written": wrote, "already_recorded": dup, "history_rows": total, "distinct_days": days,
                      "distinct_sym_sides": syms, "newest_row": newest, "dry_run": bool(args.dry_run)}, indent=1))
    return 0


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _i(x):
    try:
        return int(float(x)) if x is not None else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
