#!/usr/bin/env python3
"""v15_npz_keeper — runs ON s1 (cron */10, flock singleton). Keeps the STOCK NPZs minutes-fresh just before each symbol's sweep turn.

AUTOPILOT 2026-10-02 (USER: "NPZ must be regenerated just before each sym's turn and sent to the server that will need it").
  1. Look-ahead window: the next --lookahead PENDING stock symbols (scheduler order, not finished in the current round's progress dirs, not running anywhere).
  2. A symbol is STALE when its NPZ last bar is older than (last completed US session end - 90 min).
  3. Stale -> tools/stkt_rebuild_tail.py (Tradier time&sales tail rebuild, unchanged builder, validated, npz_guard, atomic install; rolling cutoff via STKT_CUTOFF_EPOCH).
  4. Installed NPZ is pushed to s2 and s5 (skipped for a host that is running that symbol), md5 verified.
  5. Failures are counted per symbol (data/autopilot/npz_fail.json); 3 consecutive failures -> skipped until the next UTC day (the scheduler's own gate then decides).
NEVER shortens an NPZ (npz_guard), NEVER replaces a file a pilot holds, NEVER touches crypto NPZs (crypto tail refresh = NPZB loop, see AUTOPILOT_RUNBOOK.md).
usage: v15_npz_keeper.py [--lookahead 10] [--dry-run] [--symbols A,B]
"""
import argparse, datetime as dt, fcntl, hashlib, json, os, subprocess, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.path.expanduser("~/binance-sandbox"))
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
IND = ROOT / "backtest_v8" / "indicators"
AP = ROOT / "data" / "autopilot"
FAIL = AP / "npz_fail.json"
FRESH = AP / "npz_fresh.json"
ET = ZoneInfo("America/New_York")
PUSH_HOSTS = {"s2": "niels@10.0.0.4", "s5": "niels@10.0.0.5"}
PY = str(ROOT / ".venv" / "bin" / "python")


def jload(p, d):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return d


def jsave(p, obj):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    t = p.with_suffix(".tmp"); t.write_text(json.dumps(obj, indent=1)); t.replace(p)


def last_session_end_utc(now):
    """end (19:45 ET last bar + 15 min) of the most recent COMPLETED US session day (weekends skipped; holidays not modelled: harmless, only triggers a refresh)."""
    n = now.astimezone(ET)
    d = n.date()
    if n.hour < 20:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return int(dt.datetime(d.year, d.month, d.day, 20, 0, tzinfo=ET).timestamp())


def npz_last_bar(sym):
    import numpy as np
    p = IND / f"{sym}.npz"
    if not p.exists():
        return None
    try:
        z = np.load(p, allow_pickle=True)
        ts = z["timestamps"].astype("int64")
        return int(ts[-1] // 1000 if ts[-1] > 1e11 else ts[-1])
    except Exception:
        return None


def sh(host_ip, cmd, timeout=60):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host_ip, cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def held_on(host_ip, sym):
    out = sh(host_ip, f"ps -eo args | grep -E '[v]15_(pilot|365)|[r]ow365' | grep -cE '{sym}_(LONG|SHORT)'")
    try:
        return int(out.strip() or "0") > 0
    except Exception:
        return True


def pending_stocks(limit):
    """next pending stock symbols in scheduler order from the scheduler's per-tick snapshot (data/autopilot/sched_snapshot.json); none if the snapshot is older than 15 min."""
    snap = jload(AP / "sched_snapshot.json", {})
    try:
        age = time.time() - dt.datetime.fromisoformat(snap["at"]).timestamp()
    except Exception:
        return []
    if age > 900:
        return []
    running = {ss.rsplit("_", 1)[0] for ss in snap.get("running", [])}
    return [s for s in snap.get("pending_stocks", []) if s not in running][:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookahead", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--symbols")
    a = ap.parse_args()
    lk = open("/tmp/v15_npz_keeper.lock", "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[keeper] locked"); return
    now = dt.datetime.now(dt.timezone.utc)
    want = last_session_end_utc(now) - 90 * 60
    today = now.strftime("%Y%m%d")
    fails = jload(FAIL, {})
    fresh = jload(FRESH, {})
    cands = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else pending_stocks(a.lookahead)
    stale = []
    for s in cands:
        lb = npz_last_bar(s)
        f = fails.get(s, {})
        if f.get("day") == today and f.get("n", 0) >= 3:
            continue
        if lb is None or lb < want:
            stale.append(s)
    print(json.dumps({"now": now.isoformat(), "want_last_bar": want, "candidates": cands, "stale": stale}))
    if a.dry_run or not stale:
        return
    # rolling cutoff: 8 days back at 08:00Z (never earlier than the validated 2026-09-24 cutoff)
    cut = max(int(dt.datetime(2026, 9, 24, 8, 0, tzinfo=dt.timezone.utc).timestamp()), int((now - dt.timedelta(days=8)).replace(hour=8, minute=0, second=0, microsecond=0).timestamp()))
    env = dict(os.environ, STKT_CUTOFF_EPOCH=str(cut), STKT_OVERLAP_START=(now - dt.timedelta(days=40)).strftime("%Y-%m-%d"))
    batch = stale[:4]
    cmd = ["nice", "-n", "10", PY, "-u", str(ROOT / "tools" / "stkt_rebuild_tail.py"), "--symbols", ",".join(batch), "--parallel", "2", "--install",
           "--out-dir", os.path.expanduser(f"~/stkt_npz_out_keeper"), "--backup-dir", os.path.expanduser(f"~/stkt_npz_backup_{today}")]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, env=env, cwd=str(ROOT))
        lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
    except subprocess.TimeoutExpired:
        lines = []
    got = {}
    for l in lines:
        try:
            j = json.loads(l); got[j.get("symbol")] = j
        except Exception:
            pass
    for s in batch:
        j = got.get(s) or {}
        inst = (j.get("install") or {}) if isinstance(j.get("install"), dict) else {}
        ok = bool(j.get("ok")) and bool(inst.get("installed") or j.get("installed"))
        lb = npz_last_bar(s)
        if ok or (lb is not None and lb >= want):
            fails.pop(s, None)
            m = hashlib.md5((IND / f"{s}.npz").read_bytes()).hexdigest()
            fresh[s] = {"at": now.isoformat(), "last_bar": lb, "md5": m}
            for hn, ip in PUSH_HOSTS.items():
                if held_on(ip, s):
                    fresh[s].setdefault("push_skipped_held", []).append(hn); continue
                tmp = f".{s}.keeper.npz"
                subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(IND / f"{s}.npz"), f"{ip}:~/binance-sandbox/backtest_v8/indicators/{tmp}"], timeout=300)
                out = sh(ip, f"cd ~/binance-sandbox/backtest_v8/indicators && [ \"$(md5sum {tmp} | cut -d' ' -f1)\" = {m} ] && mv {tmp} {s}.npz && echo OK || (rm -f {tmp}; echo BAD)")
                fresh[s].setdefault("pushed", {})[hn] = out.strip()
        else:
            f = fails.get(s, {"n": 0})
            fails[s] = {"n": (f.get("n", 0) + 1) if f.get("day") == today else 1, "day": today, "reason": str(j.get("error") or inst.get("reason") or "no result")[:160]}
    jsave(FAIL, fails); jsave(FRESH, fresh)
    print(json.dumps({"batch": batch, "failed": {s: fails[s] for s in batch if s in fails}}))


if __name__ == "__main__":
    main()
