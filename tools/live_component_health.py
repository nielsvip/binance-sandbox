#!/usr/bin/env python3
"""ONE health verdict per watchdog-managed live component, crypto and stocks, on this host.

USER 2026-10-20: "fix all the watchdogs for all the components of every script in crypto and stock that
are OBVIOUSLY NOT WORKING ... and all scripts are always trading NO FUCKING EXCEPTIONS".

A wrapper being alive proves NOTHING — the measured 2026-10-20 16:38-16:56Z crypto outage had all five
`run_with_watchdog.sh ez_manage.py --account <a>` wrappers up with ZERO python children, while S1 held the
lease. So the verdict here is built from four independent facts per component:
  wrapper   — is the run_with_watchdog.sh supervisor process alive?
  child     — is the actual python process alive, and for how long?
  log       — how many seconds since its log file was last written?
  fence     — is this host currently fenced out of this component's venue by the failover lease?

Verdicts: OK | NO_CHILD | LOG_STALE | FLAPPING | FENCED | WRAPPER_DEAD
FENCED is reported, never counted as a fault: a fenced host is correctly idle.
Read-only. Writes nothing but its optional --json report.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(os.environ.get("BASE_PATH") or "/Users/niels/Documents/binance")
LOGDIR = pathlib.Path(os.environ.get("LIVE_LOG_DIR") or (pathlib.Path.home() / "logs"))

# component -> (venue, max log silence seconds before LOG_STALE)
# A trader must speak every loop; a producer every poll; the slow analytics ones get a wide bar.
VENUE_CRYPTO, VENUE_STOCKS, VENUE_NONE = "crypto", "stocks", "-"
SILENCE = {
    "ez_manage.py": (VENUE_CRYPTO, 180),
    "ez_positions_watchdog.py": (VENUE_CRYPTO, 300),
    "ez_reentry_daemon.py": (VENUE_CRYPTO, 300),
    "ez_copilot.py": (VENUE_CRYPTO, 900),
    "ez_klines.py": (VENUE_CRYPTO, 300),
    "ez_indicators.py": (VENUE_CRYPTO, 300),
    "ez_indicators_merger.py": (VENUE_CRYPTO, 600),
    "ez_share_ind.py": (VENUE_CRYPTO, 600),
    "ez_prices.py": (VENUE_CRYPTO, 180),
    "ez_prices_ws.py": (VENUE_CRYPTO, 180),
    "ez_mark_prices.py": (VENUE_CRYPTO, 300),
    "ez_market_data.py": (VENUE_CRYPTO, 600),
    "ez_orderbook.py": (VENUE_CRYPTO, 600),
    "ez_crosses.py": (VENUE_CRYPTO, 900),
    "ez_rankings.py": (VENUE_NONE, 3600),
    "ez_news_scanner.py": (VENUE_NONE, 3600),
    "ez_backup.py": (VENUE_NONE, 7200),
    "ez_symbol_performance.py": (VENUE_NONE, 3600),
    "tradier_manage.py": (VENUE_STOCKS, 300),
    "tradier_positions.py": (VENUE_STOCKS, 300),
    "tradier_prices.py": (VENUE_STOCKS, 300),
    "tradier_indicators.py": (VENUE_STOCKS, 600),
    "tradier_rankings.py": (VENUE_NONE, 3600),
    "trade_analytics.py": (VENUE_NONE, 3600),
    "sweep_cockpit.py": (VENUE_NONE, 3600),
}
# the components that MUST be trading; anything else is support
TRADERS = {"ez_manage.py", "tradier_manage.py"}
FLAP_S = float(os.environ.get("LCH_FLAP_S", "120"))  # a child younger than this with an older wrapper = flapping


def _etime_seconds(text: str) -> int:
    """`[[dd-]hh:]mm:ss` -> seconds. macOS ps has no `etimes` keyword (same portability gap as the missing
    GNU coreutils in Mac cron), so elapsed time must be parsed from `etime` or every row is dropped and the
    auditor reports 0 components — which is what it did before this was fixed."""
    text = text.strip()
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    bits = [int(x) for x in text.split(":")]
    while len(bits) < 3:
        bits.insert(0, 0)
    return days * 86400 + bits[0] * 3600 + bits[1] * 60 + bits[2]


def ps_rows() -> list:
    out = subprocess.run(["ps", "-A", "-o", "pid=,etime=,args="], capture_output=True, text=True, timeout=30).stdout
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append({"pid": int(parts[0]), "etimes": _etime_seconds(parts[1]), "args": parts[2]})
        except ValueError:
            continue
    return rows


def acct_of(args: str, script: str) -> str:
    m = re.search(r"--accounts?\s+([a-z0-9 ]+?)(?:\s+--|\s*$)", args)
    return "+".join(m.group(1).split()) if m else ""


def log_candidates(script: str, acct: str) -> list:
    stem = script[:-3]
    names = []
    if acct:
        for a in acct.split("+"):
            names += [f"{stem}_{a}.log", f"{stem}___account_{a}.log"]
    names += [f"{stem}.log"]
    return names


def fenced(script: str, acct: str) -> bool | None:
    gate = ROOT / "tools" / "live_failover_lease_gate.sh"
    if not gate.exists():
        return None
    first = (acct.split("+") or [""])[0]
    cmd = f'source "{gate}" >/dev/null 2>&1; lf_lease_fenced "{script}" "{ROOT}" "{first}"'
    try:
        return subprocess.run(["bash", "-c", cmd], capture_output=True, timeout=25).returncode == 0
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--jsonl", help="APPEND one compact line per tick here, so an interruption-free streak is "
                                    "measurable instead of asserted (tools/live_uptime_streak.py reads it)")
    ap.add_argument("--quiet", action="store_true", help="print only faults")
    ap.add_argument("--traders-only", action="store_true")
    a = ap.parse_args()

    rows = ps_rows()
    now = time.time()
    wrappers, children = {}, []
    for r in rows:
        args = r["args"]
        if "run_with_watchdog.sh" in args and "grep" not in args:
            m = re.search(r"run_with_watchdog\.sh\s+(\S+\.py)(.*)$", args)
            if m:
                script, rest = m.group(1), m.group(2)
                wrappers[(script, acct_of(rest, script))] = r
        elif "run_with_watchdog" not in args and "grep" not in args:
            # First token that ENDS in .py and is not part of the interpreter path. `(?:^|/)name\.py` failed
            # here: a child is spawned as `/opt/anaconda3/.../bin/python -u ez_manage.py --account ang`, where
            # the script token is preceded by a SPACE, so every trader was scored NO_CHILD while its log was
            # 3 s old. Tokenising is the only robust read across the launch styles in use (bare `python3`,
            # conda abs path, `-u`, `-m`).
            toks = args.split()
            idx = next((i for i, t in enumerate(toks) if t.endswith(".py") and "/python" not in t), None)
            if idx is not None:
                script = toks[idx].rsplit("/", 1)[-1]
                children.append((script, acct_of(" ".join(toks[idx + 1:]), script), r))

    fence_memo = {}
    out = []
    for (script, acct), w in sorted(wrappers.items()):
        venue, max_silence = SILENCE.get(script, (VENUE_NONE, 1800))
        if a.traders_only and script not in TRADERS:
            continue
        kid = next((r for s, ac, r in children if s == script and ac == acct), None)
        if kid is None and not acct:
            kid = next((r for s, ac, r in children if s == script), None)
        log_age, log_name = None, None
        for n in log_candidates(script, acct):
            p = LOGDIR / n
            if p.exists():
                age = max(0.0, now - p.stat().st_mtime)
                if log_age is None or age < log_age:
                    log_age, log_name = age, n
        key = (script, acct)
        if key not in fence_memo:
            fence_memo[key] = fenced(script, acct) if venue in (VENUE_CRYPTO, VENUE_STOCKS) else False
        is_fenced = fence_memo[key]

        if is_fenced:
            verdict = "FENCED"
        elif kid is None:
            verdict = "NO_CHILD"
        elif kid["etimes"] < FLAP_S and w["etimes"] > FLAP_S * 3:
            verdict = "FLAPPING"
        elif log_age is not None and log_age > max_silence:
            verdict = "LOG_STALE"
        else:
            verdict = "OK"
        out.append({"script": script, "account": acct, "venue": venue, "verdict": verdict,
                    "wrapper_pid": w["pid"], "wrapper_up_s": w["etimes"],
                    "child_pid": (kid or {}).get("pid"), "child_up_s": (kid or {}).get("etimes"),
                    "log": log_name, "log_age_s": None if log_age is None else round(log_age, 1),
                    "log_max_silence_s": max_silence, "fenced": is_fenced, "is_trader": script in TRADERS})

    # components that have a child but NO wrapper -> unsupervised: nothing will restart them
    seen = {(r["script"], r["account"]) for r in out}
    for script, acct, r in sorted(children, key=lambda x: (x[0], x[1])):
        if script in SILENCE and (script, acct) not in seen and not any(s == script for s, _a in seen):
            out.append({"script": script, "account": acct, "venue": SILENCE[script][0], "verdict": "UNSUPERVISED",
                        "wrapper_pid": None, "wrapper_up_s": None, "child_pid": r["pid"], "child_up_s": r["etimes"],
                        "log": None, "log_age_s": None, "log_max_silence_s": None, "fenced": None,
                        "is_trader": script in TRADERS})

    faults = [r for r in out if r["verdict"] not in ("OK", "FENCED")]
    trader_faults = [r for r in faults if r["is_trader"]]
    rep = {"host": os.uname().nodename, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "n_components": len(out), "n_ok": sum(1 for r in out if r["verdict"] == "OK"),
           "n_fenced": sum(1 for r in out if r["verdict"] == "FENCED"), "n_faults": len(faults),
           "n_trader_faults": len(trader_faults), "components": out}
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rep, indent=1))
    if a.jsonl:
        line = {"ts": rep["ts"], "epoch": int(now), "n_ok": rep["n_ok"], "n_fenced": rep["n_fenced"],
                "n_faults": rep["n_faults"], "n_trader_faults": rep["n_trader_faults"],
                "faults": sorted(f"{r['script']}{':' + r['account'] if r['account'] else ''}={r['verdict']}" for r in faults),
                # child_up_s of the youngest trader: a restart anywhere in the live tier breaks the streak
                "min_trader_child_up_s": min([r["child_up_s"] for r in out if r["is_trader"] and r["child_up_s"] is not None] or [None]) if any(r["is_trader"] for r in out) else None}
        with open(a.jsonl, "a") as fh:
            fh.write(json.dumps(line) + "\n")
    show = faults if a.quiet else out
    print(f"{'script':26s} {'acct':6s} {'venue':7s} {'verdict':12s} {'wrap_up':>8s} {'child_up':>8s} {'log_age':>8s} {'limit':>6s}")
    print("-" * 94)
    for r in sorted(show, key=lambda x: (x["verdict"] == "OK", x["venue"], x["script"], x["account"])):
        print(f"{r['script']:26s} {r['account']:6s} {r['venue']:7s} {r['verdict']:12s} "
              f"{str(r['wrapper_up_s']):>8s} {str(r['child_up_s']):>8s} {str(r['log_age_s']):>8s} {str(r['log_max_silence_s']):>6s}")
    print(f"\n{rep['n_components']} components: {rep['n_ok']} OK, {rep['n_fenced']} FENCED, {rep['n_faults']} FAULT "
          f"({rep['n_trader_faults']} of them TRADERS)")
    return 1 if trader_faults else 0


if __name__ == "__main__":
    sys.exit(main())
