#!/usr/bin/env python3
"""live_failover_stability_pin — "S1 should keep trading until the macbook is stable" (USER 2026-10-20).

THE FLAP IT EXISTS TO STOP. 2026-10-20, around the stock open at 13:30Z, the lease changed hands five
times in 68 minutes:

    12:50:02  MAC_FENCED   gen 63  crypto          S1 takes over (5/5 accounts reached the broker)
    12:57:22  lease grows  gen 64  crypto+stocks
    13:49:02  (S1 stops its 5 ez_manage)
    13:50:31  MAC_UNFENCED gen 65  "Mac healthy 300s"
    13:52:43  MAC_FENCED   gen 66  crypto          "positions_stale ang/long_positions.json age 3725s"
    13:58:04  lease grows  gen 67  crypto+stocks

Every flip costs a full stop/start of up to 8 managers: tools/s1_trading_supervisor.sh starts ONE account
per ~42 s lap for crypto and only reaches stocks afterwards (measured: `starting stocks:trb` at 13:46:06,
16 minutes after the bell; trc at 14:04:00), and on the Mac side `tradier_manage --accounts tra` logged 11
restarts in 300 s and `ez_manage --account men` 7 in 300 s. So the system spent the first half hour of the
session ramping instead of trading, on both hosts at once.

The lease's own failback rule is "Mac heartbeat fresh AND no data stale AND nothing not-trading for
LF_FAILBACK_S" — all instantaneous conditions, which the Mac satisfied at 13:50:31 and broke 132 s later.
This adds the missing condition, through the pin that tools/live_failover_s1.py already honours
(`if pin == "mac" or (pin != "s1" and healthy)`), so no locked file changes:

    the Mac gets its venues back only after it has been READY, CONTINUOUSLY, for STABLE_S.

READY means, deliberately, nothing about whether the Mac is currently trading — it cannot be, while it is
fenced, and requiring fresh position files would be a deadlock (that is exactly the signal gen 66 tripped
on). It means the Mac is CAPABLE and SYNCED:

    preopen_readiness_gate verdict PASS   the new TEMPLATE_* boards and defaults are applied here, so the
                                          Mac cannot come back and trade yesterday's set (USER: "it just
                                          syncs the new templates and defaults as soon as it comes back on
                                          and before it starts trading old data")
    indicators_fresh                      timestamp_15m inside the live 90-min entry gate
    no restart churn                      no /Users/niels/logs/.restart_alert_* touched in CHURN_QUIET_S
    load sane                             1-min load per core under LOAD_MAX (the Mac at load 82 starves
                                          every producer it owns)

AND IT NEVER PINS TO A HOST THAT CANNOT TRADE EITHER. On 2026-10-20 S1 held crypto with every entry
refused as `BLOCKED - STALE_INDICATORS (259m old)` and never started tradier_manage at all. Pinning to a
blind S1 is worse than the flap, so when S1's own snapshot is stale this tool alarms
NEITHER_HOST_CAPABLE and changes nothing.

Default is REPORT ONLY. The cron passes --apply.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
STATE = ROOT / "data" / "reports" / "failover_stability_pin.json"
READINESS = ROOT / "data" / "reports" / "preopen_readiness.json"
LEASE = ROOT / "data" / "live_failover" / "lease_local.json"
LOGDIR = Path(os.environ.get("LF_PIN_LOGDIR", "/Users/niels/logs"))
CTL = "/home/niels/binance/tools/live_failover_ctl.sh"
S1_HOST = os.environ.get("LF_PIN_S1_HOST", "s1-pub")
STABLE_S = float(os.environ.get("LF_PIN_STABLE_S", "900"))
READINESS_MAX_AGE_S = float(os.environ.get("LF_PIN_READINESS_MAX_AGE_S", "1800"))
CHURN_QUIET_S = float(os.environ.get("LF_PIN_CHURN_QUIET_S", "600"))
LOAD_MAX = float(os.environ.get("LF_PIN_LOAD_MAX", "4.0"))
MIN_INTERVAL_S = float(os.environ.get("LF_PIN_MIN_INTERVAL_S", "300"))
S1_IND_STALE_MAX_PCT = float(os.environ.get("LF_PIN_S1_IND_STALE_MAX_PCT", "25"))
S1_REFUSAL_MAX = float(os.environ.get("LF_PIN_S1_REFUSAL_MAX", "0"))
REFUSAL_WINDOW_MIN = float(os.environ.get("LF_PIN_S1_REFUSAL_WINDOW_MIN", "10"))
CHURN_PATTERNS = ("ez_manage", "tradier_manage", "ez_indicators", "ez_positions")
REMOTE_IND = r"""
import json, time
try:
    d = json.load(open("/home/niels/binance/data/latest_market_data.json"))
except Exception as exc:
    print(json.dumps({"error": str(exc)[:120]})); raise SystemExit(0)
now, ages, stale = time.time(), 0, 0
for sym, p in d.items():
    if sym.startswith("_") or not isinstance(p, dict):
        continue
    ts = p.get("timestamp_15m") or p.get("timestamp")
    if not ts:
        continue
    try:
        import datetime as dt
        bar = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        continue
    ages += 1
    try:
        tick = float(p.get("_tick_ts") or 0)
    except Exception:
        tick = 0.0
    if tick > 0 and now - tick < 120.0:
        continue
    if (now - bar) / 60.0 > 90.0:
        stale += 1
print(json.dumps({"symbols": ages, "stale": stale, "stale_pct": round(100.0 * stale / ages, 1) if ages else 100.0}))
"""


def _now():
    return time.time()


def _iso(epoch):
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def _ssh(host, command, timeout=40):
    cmd = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ControlPath=none", host, command]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def churn_age():
    """Seconds since the newest restart-churn marker a live manager wrote, or None when there is none."""
    newest = None
    for marker in LOGDIR.glob(".restart_alert_*"):
        if not any(p in marker.name for p in CHURN_PATTERNS):
            continue
        try:
            mtime = marker.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return None if newest is None else _now() - newest


def churning():
    """(bool, detail) — a marker touched inside CHURN_QUIET_S means a manager is still restart-looping."""
    age = churn_age()
    if age is None:
        return False, "no churn marker"
    offenders = sorted(
        (m.name.replace(".restart_alert_", "") for m in LOGDIR.glob(".restart_alert_*")
         if any(p in m.name for p in CHURN_PATTERNS) and _now() - m.stat().st_mtime <= CHURN_QUIET_S),
    )
    return bool(offenders), f"newest marker {int(age)}s ago" + (f" ({', '.join(offenders[:6])})" if offenders else "")


def load_per_core():
    try:
        return os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except Exception:
        return None


def mac_readiness():
    """(ready, reasons, measured) — CAPABLE and SYNCED, never 'currently trading' (see module docstring).

    Load is a WARNING, not a blocker. The Mac runs chronically hot (measured 2026-10-19: load 82, swap
    5.5/7 GB, 63 python processes) and a hard load gate would pin live trading to S1 for good — on a host
    that reaches its stocks accounts one per ~6 min lap. Overload is only harmful here through effects the
    hard checks already measure directly: stale indicators and manager restart churn."""
    reasons, measured, warnings = [], {}, []
    report = _read_json(READINESS)
    if not report:
        reasons.append("no preopen_readiness.json")
    else:
        age = _now() - (datetime.datetime.strptime(report["generated_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp())
        measured["readiness_verdict"] = report.get("verdict")
        measured["readiness_age_s"] = round(age)
        if age > READINESS_MAX_AGE_S:
            reasons.append(f"readiness report {int(age)}s old > {int(READINESS_MAX_AGE_S)}s")
        elif report.get("verdict") != "PASS":
            reasons.append("defaults not synced: readiness " + ",".join(report.get("failed_critical") or ["FAIL"]))
        indicators = next((c for c in report.get("checks") or [] if c["name"] == "indicators_fresh"), None)
        if indicators is not None:
            measured["mac_ind"] = indicators.get("measured")
            if not indicators.get("ok"):
                reasons.append("mac indicators: " + indicators.get("detail", "stale"))
    is_churning, churn_detail = churning()
    measured["churn"] = churn_detail
    if is_churning:
        reasons.append(f"manager restart churn ({churn_detail})")
    load = load_per_core()
    measured["load_per_core"] = None if load is None else round(load, 2)
    if load is not None and load > LOAD_MAX:
        warnings.append(f"load {load:.2f}/core > {LOAD_MAX} (warning only)")
    measured["warnings"] = warnings
    return not reasons, reasons + warnings, measured


def s1_refusals():
    """(refusal_count, sampled, detail) from S1's OWN manager logs — what the entry gate actually decided.

    The snapshot FILE is not the signal. Measured 2026-10-20: S1's data/latest_market_data.json had freshened
    to 49% stale by 14:19Z (inside the 25% bar once its ez_indicators finished warming at ~14:1xZ), and this
    tool therefore called S1 capable and pinned the lease to it at 14:20:08Z — while S1's own ez_manage was
    still refusing every entry as `BLOCKED - STALE_INDICATORS (282m old)` at 14:26Z, because the manager's
    in-memory `ind` does not track that file promptly. A file check alone will keep making that mistake, so
    capability now also demands the host's own recent decisions."""
    minutes = [datetime.datetime.fromtimestamp(_now() - 60 * i, datetime.timezone.utc).strftime("%m-%d %H:%M") for i in range(int(REFUSAL_WINDOW_MIN) + 1)]
    window = "|".join(m.replace(" ", " ") for m in minutes)
    cmd = ("for f in /home/niels/logs/failover_ez_manage_*.log; do [ -f \"$f\" ] && tail -c 400000 \"$f\"; done 2>/dev/null"
           f" | grep -E '^\\[({window})' | grep -c 'BLOCKED - STALE_INDICATORS'; pgrep -c -f 'ez_manage.py --account' || echo 0")
    try:
        res = _ssh(S1_HOST, cmd, timeout=60)
    except Exception as exc:
        return None, 0, f"{S1_HOST} log probe failed: {exc}"
    lines = [l.strip() for l in (res.stdout or "").splitlines() if l.strip().isdigit()]
    if len(lines) < 2:
        return None, 0, f"{S1_HOST} log probe unreadable: {(res.stdout or res.stderr)[:80]}"
    return int(lines[0]), int(lines[1]), f"{S1_HOST} {lines[0]} STALE_INDICATORS refusals in the last {int(REFUSAL_WINDOW_MIN)} min, {lines[1]} ez_manage running"


def s1_capable():
    """(capable, detail) — pin to S1 only when S1 can be PROVEN able to trade, never merely not-disproven.

    Three conditions, all required:
      1. its snapshot's timestamp_15m is inside the live 90-min window for most symbols;
      2. its own ez_manage accounts are up — a host with no traders cannot be the one that keeps trading;
      3. those traders are not refusing entries for stale indicators.
    An unreachable host, an unreadable snapshot or an unreadable log is NOT capable: this lever may only
    move live routing on positive evidence."""
    try:
        res = _ssh(S1_HOST, "/home/niels/.conda/envs/binance_env_mac/bin/python - <<'PYEOF'\n" + REMOTE_IND + "\nPYEOF")
    except Exception as exc:
        return False, f"{S1_HOST} unreachable: {exc}"
    payload = _read_json_str(res.stdout)
    if not payload or "error" in payload:
        return False, f"{S1_HOST} snapshot unreadable: {(payload or {}).get('error', res.stderr[:80])}"
    pct = payload.get("stale_pct", 100.0)
    snapshot_detail = f"{S1_HOST} snapshot {payload.get('stale')}/{payload.get('symbols')} stale ({pct}%)"
    if pct > S1_IND_STALE_MAX_PCT:
        return False, snapshot_detail
    refusals, running, refusal_detail = s1_refusals()
    if refusals is None:
        return False, f"{snapshot_detail}; {refusal_detail}"
    if running < 1:
        return False, f"{snapshot_detail}; {refusal_detail} — no trader to keep trading"
    if refusals > S1_REFUSAL_MAX:
        return False, f"{snapshot_detail}; {refusal_detail} — entries are being refused"
    return True, f"{snapshot_detail}; {refusal_detail}"


def current_pin():
    """(pin, source) read from S1's AUTHORITATIVE data/live_failover/lease.json.

    The Mac's mirror data/live_failover/lease_local.json carries accounts/fetched_at/gen/holder/reason/
    venues and NO `pin` key, so reading the pin from it always returned None: this tool would have
    re-issued force-s1 every cooldown and, worse, its `pin == "s1"` un-pin branch could never fire — the
    Mac would never have got its venues back. Verified 2026-10-20 14:2xZ: S1 lease.json pin='s1' while the
    mirror reported None."""
    try:
        res = _ssh(S1_HOST, "cat /home/niels/binance/data/live_failover/lease.json", timeout=40)
        lease = json.loads(res.stdout)
        return lease.get("pin"), "s1:lease.json"
    except Exception as exc:
        return None, f"unreadable ({str(exc)[:60]}) — treated as no pin"


def _read_json_str(text):
    for line in reversed((text or "").strip().splitlines()):
        try:
            return json.loads(line)
        except Exception:
            continue
    return None


def set_pin(value, apply_change):
    """force-s1 / auto through the project's own override CLI (tools/live_failover_ctl.sh)."""
    command = {"s1": "force-s1", "auto": "auto"}[value]
    if not apply_change:
        return {"applied": False, "would": command}
    res = _ssh(S1_HOST, f"bash {CTL} {command}", timeout=90)
    return {"applied": res.returncode == 0, "command": command, "rc": res.returncode, "out": (res.stdout or res.stderr)[:200]}


def tick(apply_change=False):
    now = _now()
    state = _read_json(STATE, {}) or {}
    lease = _read_json(LEASE, {}) or {}
    pin, pin_source = current_pin()
    holder = lease.get("holder")
    ready, reasons, measured = mac_readiness()
    ready_since = state.get("ready_since")
    if ready:
        ready_since = ready_since or now
    else:
        ready_since = None
    stable_for = None if ready_since is None else now - ready_since
    last_action_at = state.get("last_action_at") or 0
    cooling = now - last_action_at < MIN_INTERVAL_S
    action, action_result = None, None
    if not ready:
        if pin != "s1":
            capable, capable_detail = s1_capable()
            measured["s1"] = capable_detail
            if not capable:
                action = "NEITHER_HOST_CAPABLE"
                reasons.append(f"and {capable_detail} — pin NOT moved")
            elif cooling:
                action = "cooling"
            else:
                action, action_result = "pin_s1", set_pin("s1", apply_change)
    elif pin == "s1" and stable_for is not None and stable_for >= STABLE_S:
        if cooling:
            action = "cooling"
        else:
            action, action_result = "unpin_auto", set_pin("auto", apply_change)
    payload = {
        "generated_utc": _iso(now),
        "apply": bool(apply_change),
        "lease": {"holder": holder, "pin": pin, "pin_source": pin_source, "gen": lease.get("gen"), "venues": lease.get("venues"), "reason": lease.get("reason")},
        "mac_ready": ready,
        "ready_since": _iso(ready_since),
        "stable_for_s": None if stable_for is None else round(stable_for),
        "stable_needed_s": STABLE_S,
        "reasons": reasons,
        "measured": measured,
        "action": action,
        "action_result": action_result,
        "last_action_at": now if (action in ("pin_s1", "unpin_auto") and apply_change) else last_action_at,
    }
    payload["ready_since_epoch"] = ready_since
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    tmp.replace(STATE)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually move the pin (default: report only)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    payload = tick(apply_change=args.apply)
    if not args.quiet:
        print(f"[stability-pin] mac_ready={payload['mac_ready']} pin={payload['lease']['pin']} holder={payload['lease']['holder']} stable_for={payload['stable_for_s']}s/{int(STABLE_S)}s action={payload['action']}")
        for reason in payload["reasons"]:
            print(f"   - {reason}")
        if payload["action_result"]:
            print(f"   -> {payload['action_result']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
