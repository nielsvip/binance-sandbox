#!/usr/bin/env python
"""churn_loss_guard.py — safety circuit-breaker for LIVE trading (Mac).

Watches per account-key ledgers data/history/{account}/{SYM_SIDE}.jsonl. For each key in
tradeable_keys.json, reconstructs round-trips (OPEN/AUGMENT build avg entry; REDUCE/CLOSE
realize pnl by side) over a recent window and BLOCKS the key if it is:
  - CHURNING  : > CHURN_MAX realized exits within CHURN_WINDOW_MIN minutes, OR
  - LOSS STREAK: >= LOSS_STREAK consecutive losing exits.
BLOCK = remove that `account:SYM_SIDE` from tradeable_keys.json (backup first) + write a BIG
ALERT to data/reports/BLOCKED_KEYS_ALERT.md. Blocked keys stay blocked until a human clears
the alert and re-adds the key (review-before-trading, per user).

Usage:
  python tools/churn_loss_guard.py                 # one-shot check (dry-run: report only)
  python tools/churn_loss_guard.py --enforce       # actually remove offending keys + alert
  python tools/churn_loss_guard.py --enforce --loop 300   # watchdog every 300s
"""
import os, sys, json, time, argparse, datetime, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "history")
TKEYS = os.path.join(ROOT, "tradeable_keys.json")
ALERT = os.path.join(ROOT, "data", "reports", "BLOCKED_KEYS_ALERT.md")

CHURN_MAX = 10          # realized exits ...
CHURN_WINDOW_MIN = 60   # ... within this many minutes = churning
LOSS_STREAK = 4         # this many consecutive losing exits = block
RECENT_HOURS = 24       # only consider ledger activity in the last N hours


def _parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _round_trips(path, is_long, since):
    """Return list of (ts, realized_pnl) exits in ts order, within `since`."""
    if not os.path.exists(path):
        return []
    recs = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = _parse_ts(r.get("ts"))
        if t is None:
            continue
        recs.append((t, r))
    recs.sort(key=lambda x: x[0])
    qty = 0.0; avg = 0.0; exits = []
    for t, r in recs:
        ty = str(r.get("type", "")).upper(); q = float(r.get("qty", 0) or 0); px = float(r.get("price", 0) or 0)
        if ty in ("OPEN", "AUGMENT", "REENTRY") and q > 0 and px > 0:
            navg = (avg * qty + px * q) / (qty + q) if (qty + q) > 0 else px
            qty += q; avg = navg
        elif ty in ("REDUCE", "CLOSE") and q > 0 and px > 0 and qty > 0 and avg > 0:
            pnl = (px - avg) * q if is_long else (avg - px) * q
            if t >= since:
                exits.append((t, pnl))
            qty = max(0.0, qty - q)
            if qty <= 1e-9:
                avg = 0.0
    return exits


def check(enforce=False):
    keys = json.load(open(TKEYS)) if os.path.exists(TKEYS) else []
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(hours=RECENT_HOURS)
    win = datetime.timedelta(minutes=CHURN_WINDOW_MIN)
    offenders = []
    for key in list(keys):
        if ":" not in key:
            continue
        acct, symside = key.split(":", 1)
        is_long = symside.endswith("_LONG")
        path = os.path.join(HIST, acct, f"{symside}.jsonl")
        exits = _round_trips(path, is_long, since)
        if not exits:
            continue
        # churn: max exits in any rolling window
        churn = 0
        for i, (t, _) in enumerate(exits):
            c = sum(1 for (t2, _) in exits if t <= t2 < t + win)
            churn = max(churn, c)
        # consecutive losses (trailing)
        streak = 0
        for (_, pnl) in reversed(exits):
            if pnl < 0:
                streak += 1
            else:
                break
        reason = None
        if churn > CHURN_MAX:
            reason = f"CHURNING {churn} exits/{CHURN_WINDOW_MIN}min (>{CHURN_MAX})"
        elif streak >= LOSS_STREAK:
            reason = f"LOSS STREAK {streak} consecutive losing exits (>={LOSS_STREAK})"
        if reason:
            offenders.append((key, reason, len(exits), churn, streak))
    # report
    if not offenders:
        print(f"[guard] {now.isoformat()} OK — 0 offenders of {len(keys)} keys (window {RECENT_HOURS}h)")
        return offenders
    print(f"[guard] {now.isoformat()} !!! {len(offenders)} OFFENDERS !!!")
    for key, reason, n, churn, streak in offenders:
        print(f"   BLOCK {key}: {reason} (exits={n})")
    if enforce:
        shutil.copy(TKEYS, os.path.join(ROOT, "backups", f"tradeable_keys.BEFORE_guard_{now.strftime('%Y%m%d%H%M%S')}.json"))
        blocked = {o[0] for o in offenders}
        remaining = [k for k in keys if k not in blocked]
        json.dump(remaining, open(TKEYS, "w"), indent=1)
        with open(ALERT, "a") as f:
            f.write(f"\n# 🔴🔴🔴 BLOCKED {len(offenders)} KEYS {now.isoformat()} — REVIEW BEFORE RE-ENABLING 🔴🔴🔴\n")
            for key, reason, n, churn, streak in offenders:
                f.write(f"- **{key}** — {reason} (exits={n}, churn={churn}, streak={streak}). Removed from tradeable_keys.json. Find cause, fix, then re-add.\n")
        print(f"[guard] ENFORCED — removed {len(offenders)} keys, {len(remaining)} remain. ALERT -> {ALERT}")
    else:
        print("[guard] dry-run — nothing changed. Re-run with --enforce to block.")
    return offenders


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enforce", action="store_true")
    ap.add_argument("--loop", type=int, default=0, help="seconds between checks (0=one-shot)")
    a = ap.parse_args()
    while True:
        check(enforce=a.enforce)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
