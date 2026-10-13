#!/usr/bin/env python3
"""klines_guard — SECURITY: no S1 kline <400d old is ever deleted.

Two mechanisms:
1. guarded_write(path, rows): atomic JSON write that REFUSES to shrink a
   file's time-span (tails-only growth + head-extension allowed; truncation
   blocked). All canonical-cache writers MUST use it.
2. --tripwire (cron */5): hardlink snapshot + state compare; on ANY shrink
   vs snapshot: ALERT loudly + RESTORE the file from snapshot.

  python3 tools/klines_guard.py --tripwire   # cron: detect + restore + snapshot
  python3 tools/klines_guard.py --check      # report only, exit 1 on shrink
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "klines_cache_backtest"]
STATE = ROOT / "data" / "klines_guard_state.json"
SNAP = ROOT / "klines_cache_backtest" / ".guard_snap"
ALERT = ROOT / "data" / "klines_guard_alert.json"
MIN_KEEP_DAYS = 400
# USER 2026-10-13: staging copies (gateway/Mac feeds) roll by design — guarding them reverted fresh bars every 5 min
STAGING_DIRS = {"gateway", "macbook", "backups", "consolidated_klines"}


def span_of(rows):
    try:
        if len(rows) < 2:
            return 0.0
        a = rows[0].get("timestamp", "")[:19]
        b = rows[-1].get("timestamp", "")[:19]
        import calendar
        ta = calendar.timegm(time.strptime(a, "%Y-%m-%dT%H:%M:%S"))
        tb = calendar.timegm(time.strptime(b, "%Y-%m-%dT%H:%M:%S"))
        return max(0.0, (tb - ta) / 86400.0)
    except Exception:
        return 0.0


def guarded_write(path, rows, min_keep_days=MIN_KEEP_DAYS):
    """Write rows to path unless it destroys <min_keep_days history. Returns (ok, reason). Never raises."""
    try:
        p = Path(path)
        old = []
        if p.exists():
            try:
                old = json.loads(p.read_text())
            except Exception:
                old = []
        if old:
            so, sn = span_of(old), span_of(rows)
            if len(rows) < len(old) and so >= min_keep_days:
                return (False, f"REFUSED would shrink {len(old)}->{len(rows)} rows span {so:.0f}d->{sn:.0f}d")
            if sn + 1.0 < so:
                return (False, f"REFUSED would truncate span {so:.0f}d->{sn:.0f}d")
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows))
        os.replace(tmp, p)
        return (True, f"wrote {len(rows)} rows span {span_of(rows):.0f}d")
    except Exception as e:
        return (False, f"ERROR {type(e).__name__}: {e}"[:160])


def snapshot_files():
    out = []
    for t in TARGETS:
        if t.is_dir():
            out += [p for p in t.rglob("*.json") if ".guard_snap" not in p.parts and ".tmp" not in p.name and not (set(p.relative_to(t).parts[:-1]) & STAGING_DIRS)]
    return out


def scan(prev=None):
    prev = prev or {}
    state = {}
    for p in snapshot_files():
        k = str(p.relative_to(ROOT))
        try:
            st = p.stat()
            sig = [st.st_mtime, st.st_size]
            pe = prev.get(k)
            if pe and len(pe) == 5 and pe[3] == sig[0] and pe[4] == sig[1]:
                state[k] = pe
                continue
            rows = json.loads(p.read_text())
            state[k] = [round(span_of(rows), 1), len(rows), (rows[0].get("timestamp", "")[:10] if rows else ""), sig[0], sig[1]]
        except Exception as e:
            state[k] = ["ERR", 0, str(e)[:60], 0, 0]
    return state


def take_snapshot():
    try:
        SNAP.mkdir(parents=True, exist_ok=True)
        for p in snapshot_files():
            rel = p.relative_to(ROOT / "klines_cache_backtest")
            dst = SNAP / rel
            try:
                st = p.stat()
                try:
                    ds = dst.stat()
                    if ds.st_mtime == st.st_mtime and ds.st_size == st.st_size:
                        continue
                    dst.unlink()
                except FileNotFoundError:
                    pass
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
            except Exception:
                continue
        return True
    except Exception:
        return False


def tripwire(restore=True):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:
        prev = {}
    cur = scan(prev)
    shrunk = []
    if prev:
        for k, v in cur.items():
            if k not in prev or prev[k][0] == "ERR" or v[0] == "ERR":
                continue
            if v[1] < prev[k][1] and prev[k][0] >= MIN_KEEP_DAYS:
                shrunk.append((k, prev[k], v))
            elif isinstance(v[0], (int, float)) and isinstance(prev[k][0], (int, float)) and v[0] + 1.0 < prev[k][0]:
                shrunk.append((k, prev[k], v))
    restored = []
    if shrunk and restore:
        for k, a, b in shrunk:
            try:
                src = SNAP / k.split("klines_cache_backtest/")[-1]
                dst = ROOT / k
                if src.exists():
                    shutil.copy2(src, dst)
                    restored.append(k)
            except Exception:
                pass
    alert = None
    if shrunk:
        alert = {"ts": now, "shrunk": [{"file": k, "was": a, "now": b, "restored": k in restored} for k, a, b in shrunk]}
        # USER 2026-10-13 "find out what cuts them": snapshot every process at detection time so the writer is named, not guessed.
        try:
            import subprocess
            alert["ps"] = subprocess.run(["ps", "-eo", "pid,ppid,lstart,args"], capture_output=True, text=True, timeout=10).stdout[-60000:]
            hist = ROOT / "data" / "klines_guard_alerts.jsonl"
            with open(hist, "a") as fh:
                fh.write(json.dumps(alert) + "\n")
        except Exception:
            pass
        try:
            ALERT.write_text(json.dumps(alert, indent=1))
        except Exception:
            pass
    if not shrunk:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(cur, indent=1, sort_keys=True))
        take_snapshot()
    return {"files": len(cur), "shrunk": len(shrunk), "restored": len(restored), "alert": alert}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tripwire", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.tripwire:
        r = tripwire(restore=True)
        print(json.dumps({"files": r["files"], "shrunk": r["shrunk"], "restored": r["restored"]}))
        if r["alert"]:
            print("GUARD_ALERT " + json.dumps(r["alert"])[:2000])
        return 0
    if a.check:
        r = tripwire(restore=False)
        print(json.dumps({"files": r["files"], "shrunk": r["shrunk"]}))
        return 1 if r["shrunk"] else 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
