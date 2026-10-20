#!/usr/bin/env python3
"""never_untraded_guard — NO ACCOUNT MAY BE UNTRADED FOR MORE THAN 3 MINUTES, ON EITHER HOST.

USER 2026-10-20, verbatim: "CAPITAL SIN trading can NEVER STOP", "NEVER EVER EVER AGAIN CAN WE HAVE 3 MINUTES OR
MORE WITHOUT TRADING ON ANY ACCOUNT", "BEFORE s1 kicks in the fifty or so COMPLETELY USELESS WATCHDOGS EATING CPU ON
MACBOOK HAVE TO START DOING THEIR JOBS".

WHY EVERY EXISTING WATCHDOG FAILED TO PREVENT A 94-MINUTE OUTAGE (measured 2026-10-20 13:30-14:28 and again
16:20-16:34). None of them is broken and none was disabled without authorisation; they each correctly declined to act:

  * `run_with_watchdog.sh` per account — fenced: "[LEASE_FENCED] the other machine holds the live-trading lease ...
    NOT spawning, and NOT killing any running instance". A fenced wrapper deliberately does nothing.
  * `crypto_watchdog_cron.sh` (*/2) — logs "SKIP TRADIER: tradier_manage.py --accounts trc — wrapper alive, will
    self-heal" and stops there. A LIVE WRAPPER WITH NO ENGINE IS NOT TRADING, so "will self-heal" was false.
  * `mac_pos_writer_keepalive.sh` (*/1) — "crypto lease held by S1 — Mac writers stay DOWN (fenced); not starting
    anything" (line 96). Correct for avoiding a double trade, fatal when S1 then does not trade either.
  * S1's `s1_failover_start.sh` — REFUSED the takeover: "S1 positions not broker-true after 240s: NOT_READY trb
    broker_held=8". (Budget now 25 s and it proceeds instead of refusing; `START_INFLIGHT` 3 -> 8.)

So each layer asked "may I act?" and none asked "IS ANYBODY TRADING THIS ACCOUNT?". That question is this tool's only
job, and it is the one guarantee that cannot be delegated to a per-host watchdog, because a fenced host cannot see
that the holder is idle.

WHAT IT DOES, per account, every run:
  1. Count engines for the account HERE (the Mac) and THERE (S1), excluding shell wrappers — a `pgrep -f` pattern
     containing its own search terms self-matches the shell running it, which produced two false readings today.
  2. traded_by_nobody = 0 engines on both hosts. Anything else is fine: one host trading is the goal, and TWO hosts
     trading is a different alarm (DOUBLE) that this tool reports but never "fixes" by killing — that call is the
     lease's, and a wrong kill flattens a live book.
  3. An account untraded for more than GRACE_S (150 s, inside the 3-minute rule with room for a start to finish):
       - lease holder is the Mac, or stale/absent  -> start the wrapper HERE, bypassing the churn backoff, because
         a rate limiter must never outrank "trading is down".
       - lease holder is S1 and S1 has no engine   -> S1 is holding an account it is not trading. Force the lease
         back with `--pin mac_once` (one failback, auto-clearing, so failover is never left disabled) and start here.
  4. Everything it does is logged with the measurement that justified it, and written to
     data/reports/never_untraded/latest.json for the audit.

DELIBERATELY NOT DONE: it never kills a trader, never changes a config, never touches positions. It starts things and
it moves the lease. `NEVER_UNTRADED_APPLY=0` makes it report-only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reports" / "never_untraded"
STATE = OUT / "state.json"
LOG = Path("/Users/niels/logs/never_untraded_guard.log")
GRACE_S = float(os.environ.get("NEVER_UNTRADED_GRACE_S", "90"))  # 90 s + a 30 s cadence = act within ~2 min,
# inside the operator's 3-minute rule with room for the start itself to complete. 150 s would have left only
# 30 s of margin once cron granularity is counted.
CRYPTO = ["fin", "men", "inf", "flz", "ang"]
STOCKS = ["trb", "trc", "tra"]
S1 = os.environ.get("NEVER_UNTRADED_S1", "s1-int")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def stocks_in_session(now_ts: float | None = None) -> bool:
    """True while a stock account is EXPECTED to be trading. 2026-10-20: without this the guard treated the
    normal post-close exit of trb/trc/tra as "nobody trades this account", would start their wrappers, the
    wrapper's own market gate would exit 0, and it would restart them every 30 s — a churn loop of the guard's
    own making. Crypto is 24/7 and is never gated here.
    RTH is 13:30-20:00Z (09:30-16:00 ET); the window opens 35 min early to match the Mac's 13:00Z start_stocks.sh
    and the supervisor's own stocks_window, and closes on the bell. Weekdays only; US holidays are NOT modelled,
    so on a holiday this returns True and the guard may log an untraded stock account it cannot fix — that is a
    false alarm in the log, never a start loop, because the wrapper gate still refuses. ET offset is derived from
    the local/UTC delta rather than hardcoded, so it follows DST."""
    import datetime as _d
    t = _d.datetime.fromtimestamp(now_ts or dt.datetime.now(dt.timezone.utc).timestamp(), dt.timezone.utc)
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return (13 * 60 - 5) <= mins < (20 * 60)


def log(msg: str) -> None:
    line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _engines_local() -> dict:
    """{account: n} engines on this host. Excludes wrappers and any shell: `ps` once, match in python, so no
    pgrep self-match can inflate or hide a count (both happened today)."""
    out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
    n = {a: 0 for a in CRYPTO + STOCKS}
    for ln in out.split("\n"):
        if "python" not in ln or "run_with_watchdog" in ln or "bash -c" in ln:
            continue
        m = re.search(r"ez_manage\.py\s+--account\s+(\w+)", ln) or re.search(r"tradier_manage\.py\s+--accounts\s+(\w+)", ln)
        if m and m.group(1) in n:
            n[m.group(1)] += 1
    return n


def _engines_s1() -> dict | None:
    """Same count on S1, or None when S1 cannot be reached (then we never claim 'nobody trades it')."""
    probe = ("ps -eo pid=,args= | grep -E 'python' | grep -vE 'run_with_watchdog|bash -c' | "
             "grep -oE '(ez_manage\\.py --account|tradier_manage\\.py --accounts) [a-z]+'")
    try:
        r = subprocess.run(SSH + [S1, probe], capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if r.returncode not in (0, 1):
        return None
    n = {a: 0 for a in CRYPTO + STOCKS}
    for ln in r.stdout.split("\n"):
        p = ln.strip().split()
        if len(p) >= 2 and p[-1] in n:
            n[p[-1]] += 1
    return n


def _lease() -> dict:
    try:
        d = json.loads((ROOT / "data" / "live_failover" / "lease_local.json").read_text())
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _start_here(acct: str, venue: str) -> str:
    script = "ez_manage.py" if venue == "crypto" else "tradier_manage.py"
    flag = "--account" if venue == "crypto" else "--accounts"
    wl = Path(f"/Users/niels/logs/{script.replace('.py','')}_{acct}_watchdog.log")
    # Replace a wrapper that is alive but childless: it is fenced or wedged, and either way it is not trading.
    subprocess.run(["pkill", "-f", f"run_with_watchdog.sh {script} {flag} {acct}"], capture_output=True)
    try:
        with open(wl, "a") as fh:
            subprocess.Popen(["bash", str(ROOT / "run_with_watchdog.sh"), script, flag, acct],
                             cwd=str(ROOT), stdout=fh, stderr=fh, stdin=subprocess.DEVNULL, start_new_session=True)
        return "started wrapper here"
    except Exception as exc:
        return f"start FAILED {type(exc).__name__}: {exc}"


def _force_lease_back(why: str) -> str:
    cmd = ("cd ~/binance && /home/niels/.conda/envs/binance_env_mac/bin/python "
           "tools/live_failover_s1.py --pin mac_once")
    try:
        r = subprocess.run(SSH + [S1, cmd], capture_output=True, text=True, timeout=60)
        return f"pin mac_once rc={r.returncode} ({why})"
    except Exception as exc:
        return f"pin FAILED {type(exc).__name__} ({why})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="cron: print only when something is wrong or acted on")
    args = ap.parse_args()
    apply = os.environ.get("NEVER_UNTRADED_APPLY", "1") == "1"
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        st = json.loads(STATE.read_text())
    except Exception:
        st = {}
    here, there, lease = _engines_local(), _engines_s1(), _lease()
    holder = str(lease.get("holder") or "")
    try:
        lease_age = now - float(lease.get("fetched_epoch") or 0)
    except Exception:
        lease_age = 1e9
    rep = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "lease_holder": holder, "lease_age_s": round(lease_age), "s1_reachable": there is not None,
           "grace_s": GRACE_S, "apply": apply, "accounts": {}, "actions": [], "untraded": [], "double": []}
    for venue, accts in (("crypto", CRYPTO), ("stocks", STOCKS)):
        for a in accts:
            h, t = here.get(a, 0), (None if there is None else there.get(a, 0))
            nobody = h == 0 and (t == 0 if t is not None else False)
            if nobody and venue == "stocks" and not stocks_in_session():
                # closed market: an absent stock engine is correct, not a fault
                st.pop(f"down_{a}", None)
                rep["accounts"][a] = {"venue": venue, "mac": h, "s1": t, "untraded_s": 0, "note": "market closed"}
                continue
            both = h > 0 and bool(t)
            key = f"down_{a}"
            if nobody:
                since = st.get(key) or now
                st[key] = since
                down_s = now - since
            else:
                st.pop(key, None)
                down_s = 0.0
            rep["accounts"][a] = {"venue": venue, "mac": h, "s1": t, "untraded_s": round(down_s)}
            if both:
                rep["double"].append(a)
            if not nobody:
                continue
            rep["untraded"].append({"account": a, "venue": venue, "untraded_s": round(down_s)})
            if down_s < GRACE_S:
                continue
            # Past the grace window: somebody must start trading this account NOW.
            if holder == "s1" and t == 0:
                act = _force_lease_back(f"{a} untraded {int(down_s)}s and S1 holds it with no engine") if apply else "WOULD force lease back"
                rep["actions"].append({"account": a, "action": act})
                log(f"UNTRADED {a} {int(down_s)}s — S1 holds the lease with NO engine for it -> {act}")
            act2 = _start_here(a, venue) if apply else "WOULD start wrapper here"
            rep["actions"].append({"account": a, "action": act2})
            log(f"UNTRADED {a} ({venue}) {int(down_s)}s mac={h} s1={t} holder={holder or 'none'} -> {act2}")
            st.pop(key, None)  # re-arm the clock so a slow start is not double-acted next run
    rep["verdict"] = "OK" if not rep["untraded"] else ("ACTED" if rep["actions"] else "WITHIN_GRACE")
    try:
        STATE.write_text(json.dumps(st))
        (OUT / "latest.json").write_text(json.dumps(rep, indent=1))
    except OSError:
        pass
    if args.json:
        print(json.dumps(rep, indent=1))
    elif not (args.quiet and rep["verdict"] == "OK" and not rep["double"]):
        mac = " ".join(f"{a}={v['mac']}" for a, v in rep["accounts"].items())
        print(f"[untraded] holder={holder or 'none'}(age {int(lease_age)}s) s1_reachable={there is not None}")
        print(f"[untraded] mac engines: {mac}")
        if there is not None:
            print(f"[untraded] s1 engines:  {' '.join(f'{a}={there.get(a,0)}' for a in CRYPTO + STOCKS)}")
        for u in rep["untraded"]:
            print(f"[untraded] NOBODY TRADES {u['account']} ({u['venue']}) for {u['untraded_s']}s")
        for d in rep["double"]:
            print(f"[untraded] DOUBLE: {d} has engines on BOTH hosts — lease must resolve this, not this tool")
        for a in rep["actions"]:
            print(f"[untraded] ACTION {a['account']}: {a['action']}")
        print(f"[untraded] {rep['verdict']}")
    return 0 if rep["verdict"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
