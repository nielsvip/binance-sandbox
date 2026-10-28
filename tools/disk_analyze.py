#!/usr/bin/env python3
"""disk_analyze — read-only classification of every top-level item on a server (S1/S2/S5/S6) or the Mac.

Classes (action is a RECOMMENDATION only; nothing is deleted here):
  LIVE_INPUT        read by live trading or the boards each cycle (NPZ indicators, live DBs, cat_side defaults)  -> KEEP
  IMPORTANT_HISTORY learned / archived / audit data that cannot be regenerated (learning DBs, libraries, backups) -> KEEP, 2 copies on WD25
  REGENERABLE       rebuilt by a job (reports, caches, eval/cache rebuilds) older than --regen-days            -> DELETE after a WD25 copy exists
  OBSOLETE_LOG      *.log / *.out / *.err / rotated logs older than --log-days                                 -> DELETE
  OBSOLETE_TEMP     tmp, pycache, lock, partial (.part/.tmp) files                                             -> DELETE
  BACKUP_OFFLOAD    backups/ and *.bak older than --backup-days (backups are the only reliable history)         -> COPY to WD25 (verified), then delete local only if the WD25 copy is verified
  UNKNOWN           anything else                                                                               -> REVIEW

Usage: python3 disk_analyze.py [--root PATH] [--regen-days 14] [--log-days 7] [--backup-days 30] [--json OUT]
Output: one row per item: size, newest mtime, class, reason, action.
"""
import argparse, json, os, sys, time

LIVE_PATHS = ("backtest_v8/indicators", "data/per_sym_store.db", "data/per_sym_full_sets.json", "data/per_sym_settings.json",
              "data/hourly_reconfig", "data/vec_live", "data/pure_board", "data/parity", "data/tradier", "data/preopen_7d",
              "data/causal_learner.db", "data/v17_combo_library.sqlite", "data/eval_cache_v2.db", "data/sweep_results",
              "SPREADSHEETS", "config.py", "config_tradier.py", "live_guardian.json")
HISTORY_PATHS = ("backups", "binance-sandbox/backups", "archive", "data/experience_cold", "data/learn", "data/wiring")
REGEN_PREFIX = ("data/reports", "data/cache", "cache", "data/eval_cache", "data/klines_cache", "plots", "plots_tradier")
TEMP_NAMES = ("__pycache__", ".pytest_cache", "tmp", ".tmp", "lock", "locks")
LOG_EXT = (".log", ".out", ".err")


def size_and_mtime(path):
    total, newest = 0, 0.0
    if os.path.isfile(path):
        st = os.lstat(path)
        return st.st_size, st.st_mtime
    for dp, dn, fn in os.walk(path, onerror=lambda e: None):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            total += st.st_size
            newest = max(newest, st.st_mtime)
    return total, newest


def classify(rel, is_dir, size, newest, a):
    now = time.time()
    age_d = (now - newest) / 86400 if newest else 9999
    low = rel.lower()
    if any(rel == p or rel.startswith(p + "/") for p in LIVE_PATHS):
        return "LIVE_INPUT", "read by live trading or boards each cycle", "KEEP"
    if any(rel == p or rel.startswith(p + "/") for p in HISTORY_PATHS):
        if age_d > a.backup_days:
            return "BACKUP_OFFLOAD", f"history/backup not touched for {age_d:.0f}d", "COPY to WD25 (verified) then delete local only after the WD25 copy checks out"
        return "IMPORTANT_HISTORY", f"history/backup touched {age_d:.0f}d ago", "KEEP; ensure WD25 holds a verified copy"
    if not is_dir and low.endswith(LOG_EXT) or ".log." in low or low.endswith(".log.gz"):
        if age_d > a.log_days:
            return "OBSOLETE_LOG", f"log not written for {age_d:.0f}d", "DELETE"
        return "UNKNOWN", f"live log ({age_d:.0f}d)", "REVIEW (keep while the job runs)"
    if os.path.basename(rel) in TEMP_NAMES or low.endswith((".part", ".tmp", ".lock", ".pyc")):
        return "OBSOLETE_TEMP", "temp / cache / lock artifact", "DELETE"
    if any(rel == p or rel.startswith(p + "/") for p in REGEN_PREFIX):
        if age_d > a.regen_days:
            return "REGENERABLE", f"rebuilt output not written for {age_d:.0f}d", "DELETE after a WD25 copy exists (or confirm it is rebuilt)"
        return "UNKNOWN", f"recent output ({age_d:.0f}d)", "REVIEW"
    return "UNKNOWN", "no rule matched", "REVIEW"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/binance-sandbox"))
    ap.add_argument("--regen-days", type=int, default=14)
    ap.add_argument("--log-days", type=int, default=7)
    ap.add_argument("--backup-days", type=int, default=30)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    rows = []
    for name in sorted(os.listdir(a.root)):
        p = os.path.join(a.root, name)
        if os.path.islink(p):
            continue
        is_dir = os.path.isdir(p)
        size, newest = size_and_mtime(p)
        cls, reason, action = classify(name, is_dir, size, newest, a)
        # split large directories one level down so the big items are visible
        rows.append({"path": name, "is_dir": is_dir, "size_gb": round(size / 1e9, 3), "age_days": round((time.time() - newest) / 86400, 1) if newest else None, "class": cls, "reason": reason, "action": action})
        if is_dir and name in ("data", "backtest_v8", "backups", "SPREADSHEETS"):
            for sub in sorted(os.listdir(p)):
                sp = os.path.join(p, sub)
                if os.path.islink(sp):
                    continue
                ssize, snewest = size_and_mtime(sp)
                rel = f"{name}/{sub}"
                scls, sreason, saction = classify(rel, os.path.isdir(sp), ssize, snewest, a)
                rows.append({"path": rel, "is_dir": os.path.isdir(sp), "size_gb": round(ssize / 1e9, 3), "age_days": round((time.time() - snewest) / 86400, 1) if snewest else None, "class": scls, "reason": sreason, "action": saction})
    rows.sort(key=lambda r: -r["size_gb"])
    tot = {}
    for r in rows:
        tot[r["class"]] = round(tot.get(r["class"], 0) + r["size_gb"], 2)
    print(f"{'GB':>8} {'age_d':>7}  {'class':<17} path  -> action")
    for r in rows[:60]:
        print(f"{r['size_gb']:8.2f} {str(r['age_days']):>7}  {r['class']:<17} {r['path']}  -> {r['action']}")
    print("\nTOTAL GB BY CLASS:", json.dumps(tot, indent=1))
    if a.json:
        json.dump({"root": a.root, "rows": rows, "totals": tot}, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
