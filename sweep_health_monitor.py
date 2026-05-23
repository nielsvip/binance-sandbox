#!/usr/bin/env python3
"""
sweep_health_monitor.py — fires every 5min via launchd.

Checks the S1 sweep pipeline for the failure modes that silently wasted weeks of compute:
 - both coords (crypto + tradier) running
 - ledger advancing (new row in last 30min)
 - USELESS-rate over last 20 rows (catches "every arm produces 0 trades" cascades)
 - DUPLICATE_OF-rate (catches dead-knob queue floods)
 - memory headroom on S1
 - last GOOD result (status != USELESS/NO_RESULT/PARSE_ERROR and pool_sharpe >= useless_threshold) age
 - tradier worker procs alive (per CLAUDE.md sweep-liveness mandate)

On any alert, fires a macOS notification (osascript) and appends to alerts.log.
Always writes data/sweep_health/last_status.json so a future Claude session can read
recent state without polling.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
DATA = BASE / "data" / "sweep_health"
DATA.mkdir(parents=True, exist_ok=True)
STATUS_PATH = DATA / "last_status.json"
ALERTS_LOG = DATA / "alerts.log"
LAST_ALERT_PATH = DATA / "last_alert_state.json"

S1_HOST = "s1-int"
LEDGER_REMOTE = "/home/niels/binance-sandbox/data/sweep_coordinator/ledger.jsonl"

WINDOW = 20
STALE_LEDGER_MIN = 30
STALE_GOOD_HRS = 4
USELESS_RATE_CRIT = 0.85
DUPLICATE_RATE_WARN = 0.60
MEM_FREE_MB_WARN = 2048
USELESS_THRESHOLD = 0.4


def ssh_run(cmd: str, timeout: int = 45) -> tuple[int, str, str]:
    p = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", S1_HOST, cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return p.returncode, p.stdout, p.stderr


def notify(title: str, message: str) -> None:
    safe_title = title.replace('"', "'")
    safe_msg = message.replace('"', "'")[:240]
    subprocess.run(
        ["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}" sound name "Submarine"'],
        capture_output=True,
    )


def collect_state() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    state = {"ts_utc": now, "checks": {}, "alerts": []}

    rc, out, err = ssh_run(
        "pgrep -af 'sweep_coordinator.py --mode crypto' | grep -v grep | grep -v 'bash -c' | head -1; "
        "echo '---'; "
        "pgrep -af 'sweep_coordinator.py --mode tradier' | grep -v grep | grep -v 'bash -c' | head -1; "
        "echo '---'; "
        "pgrep -afc 'v8_vec_sweep.py --mode crypto' 2>/dev/null || echo 0; "
        "echo '---'; "
        "pgrep -afc 'v8_vec_sweep.py --mode tradier' 2>/dev/null || echo 0; "
        "echo '---'; "
        "free -m | awk '/^Mem:/ {print $2,$3,$4,$7}'; "
        "echo '---'; "
        f"tail -{WINDOW} {LEDGER_REMOTE}"
    )
    if rc != 0:
        state["checks"]["ssh"] = {"ok": False, "rc": rc, "stderr": err.strip()[:200]}
        state["alerts"].append(("CRITICAL", "S1_SSH_UNREACHABLE", err.strip()[:120] or "rc=" + str(rc)))
        return state
    state["checks"]["ssh"] = {"ok": True}

    parts = out.split("---")
    crypto_coord = parts[0].strip()
    tradier_coord = parts[1].strip() if len(parts) > 1 else ""
    crypto_workers = int(parts[2].strip() or 0) if len(parts) > 2 else 0
    tradier_workers = int(parts[3].strip() or 0) if len(parts) > 3 else 0
    mem_fields = (parts[4].strip().split() if len(parts) > 4 else ["0", "0", "0", "0"])
    ledger_block = parts[5] if len(parts) > 5 else ""

    state["checks"]["crypto_coord_alive"] = bool(crypto_coord)
    state["checks"]["tradier_coord_alive"] = bool(tradier_coord)
    state["checks"]["crypto_workers"] = crypto_workers
    state["checks"]["tradier_workers"] = tradier_workers
    try:
        total_mb, used_mb, free_mb, available_mb = [int(x) for x in mem_fields[:4]]
    except Exception:
        total_mb = used_mb = free_mb = available_mb = 0
    state["checks"]["mem_total_mb"] = total_mb
    state["checks"]["mem_used_mb"] = used_mb
    state["checks"]["mem_available_mb"] = available_mb

    if not crypto_coord:
        state["alerts"].append(("CRITICAL", "CRYPTO_COORD_DOWN", "no sweep_coordinator --mode crypto proc on S1"))
    if not tradier_coord:
        state["alerts"].append(("CRITICAL", "TRADIER_COORD_DOWN", "no sweep_coordinator --mode tradier proc on S1"))
    if crypto_workers == 0 and crypto_coord:
        state["alerts"].append(("WARN", "CRYPTO_NO_WORKERS", "coord up but 0 v8_vec_sweep workers"))
    if tradier_workers == 0 and tradier_coord:
        state["alerts"].append(("WARN", "TRADIER_NO_WORKERS", "coord up but 0 v8_vec_sweep workers"))
    if available_mb and available_mb < MEM_FREE_MB_WARN:
        state["alerts"].append(("WARN", "S1_LOW_MEM", f"available={available_mb}MB < {MEM_FREE_MB_WARN}MB"))

    rows = []
    for line in ledger_block.strip().splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    state["checks"]["rows_parsed"] = len(rows)

    if not rows:
        state["alerts"].append(("CRITICAL", "LEDGER_EMPTY_TAIL", f"tail -{WINDOW} returned 0 rows"))
        return _finalize(state)

    latest = rows[-1]
    state["checks"]["latest_test_id"] = latest.get("test_id", "?")
    state["checks"]["latest_status"] = latest.get("status", "?")
    state["checks"]["latest_verdict"] = latest.get("verdict", "?")
    state["checks"]["latest_trades"] = latest.get("trades", "?")
    state["checks"]["latest_pool_sharpe"] = latest.get("pool_sharpe", "?")
    state["checks"]["latest_ts_utc"] = latest.get("ts_utc", "?")

    try:
        latest_ts = datetime.fromisoformat(latest["ts_utc"])
        age_min = (datetime.now(timezone.utc) - latest_ts).total_seconds() / 60.0
        state["checks"]["latest_age_min"] = round(age_min, 1)
        if age_min > STALE_LEDGER_MIN:
            state["alerts"].append(("CRITICAL", "LEDGER_STALE", f"last row {age_min:.0f}min old > {STALE_LEDGER_MIN}min"))
    except Exception as e:
        state["checks"]["latest_age_min"] = None
        state["alerts"].append(("WARN", "LEDGER_TS_PARSE", str(e)[:80]))

    useless = sum(1 for r in rows if r.get("status") == "USELESS")
    no_result = sum(1 for r in rows if r.get("status") == "NO_RESULT")
    duplicate = sum(1 for r in rows if "DUPLICATE_OF_" in (r.get("verdict") or ""))
    zero_trade = sum(1 for r in rows if (r.get("trades") or 0) == 0 and r.get("status") in ("USELESS", "NO_RESULT"))
    state["checks"]["useless_rate"] = round(useless / len(rows), 2)
    state["checks"]["no_result_rate"] = round(no_result / len(rows), 2)
    state["checks"]["duplicate_rate"] = round(duplicate / len(rows), 2)
    state["checks"]["zero_trade_rate"] = round(zero_trade / len(rows), 2)

    if (useless + no_result) / len(rows) >= USELESS_RATE_CRIT:
        state["alerts"].append(("CRITICAL", "USELESS_CASCADE",
                                f"{useless+no_result}/{len(rows)} rows USELESS+NO_RESULT (>={int(USELESS_RATE_CRIT*100)}%)"))
    if duplicate / len(rows) >= DUPLICATE_RATE_WARN:
        sources = {}
        for r in rows:
            v = r.get("verdict") or ""
            if "DUPLICATE_OF_" in v:
                src = v.split("DUPLICATE_OF_", 1)[1].split("_USELESS")[0].split("_pool_sharpe")[0][:40]
                sources[src] = sources.get(src, 0) + 1
            top_src = max(sources, key=sources.get) if sources else "?"
            top_n = sources.get(top_src, 0)
        state["alerts"].append(("WARN", "DUPLICATE_FLOOD",
                                f"{duplicate}/{len(rows)} DUPLICATE; top source={top_src} ({top_n}x)"))
    if zero_trade >= len(rows) * 0.7:
        state["alerts"].append(("CRITICAL", "ZERO_TRADE_CASCADE",
                                f"{zero_trade}/{len(rows)} rows have trades=0 (sweep broken)"))

    good_age_hr = None
    for r in reversed(rows):
        if r.get("status") in ("PROMOTE",) or (
            r.get("status") not in ("USELESS", "NO_RESULT", "PARSE_ERROR", "HANG")
            and isinstance(r.get("pool_sharpe"), (int, float))
            and r["pool_sharpe"] >= USELESS_THRESHOLD
        ):
            try:
                good_age_hr = (datetime.now(timezone.utc) - datetime.fromisoformat(r["ts_utc"])).total_seconds() / 3600.0
                state["checks"]["last_good_test_id"] = r.get("test_id", "?")
                state["checks"]["last_good_sharpe"] = r.get("pool_sharpe")
                state["checks"]["last_good_trades"] = r.get("trades")
            except Exception:
                pass
            break
    state["checks"]["last_good_age_hr"] = good_age_hr
    if good_age_hr is None or good_age_hr > STALE_GOOD_HRS:
        state["alerts"].append(("WARN", "NO_GOOD_RESULT",
                                f"no row pool_sharpe>={USELESS_THRESHOLD} in last {WINDOW} rows" if good_age_hr is None
                                else f"last good result {good_age_hr:.1f}hr ago > {STALE_GOOD_HRS}hr"))

    return _finalize(state)


def _finalize(state: dict) -> dict:
    return state


def main() -> int:
    try:
        state = collect_state()
    except subprocess.TimeoutExpired:
        state = {"ts_utc": datetime.now(timezone.utc).isoformat(),
                 "checks": {"ssh": {"ok": False, "reason": "timeout"}},
                 "alerts": [("CRITICAL", "SSH_TIMEOUT", "ssh s1-int timed out >45s")]}
    except Exception as e:
        state = {"ts_utc": datetime.now(timezone.utc).isoformat(),
                 "checks": {"monitor": {"ok": False, "error": str(e)[:200]}},
                 "alerts": [("CRITICAL", "MONITOR_CRASH", str(e)[:160])]}

    STATUS_PATH.write_text(json.dumps(state, indent=2, default=str))

    if state["alerts"]:
        prev = {}
        if LAST_ALERT_PATH.exists():
            try:
                prev = json.loads(LAST_ALERT_PATH.read_text())
            except Exception:
                prev = {}
        cur_keys = {a[1] for a in state["alerts"]}
        debounce_min = 30
        now = time.time()
        with ALERTS_LOG.open("a") as f:
            for level, key, detail in state["alerts"]:
                last = prev.get(key, 0)
                if now - last >= debounce_min * 60:
                    msg = f"{level}: {key} — {detail}"
                    f.write(f"{state['ts_utc']} {msg}\n")
                    notify(f"⚠ Sweep {level}: {key}", detail)
                    prev[key] = now
        for k in list(prev):
            if k not in cur_keys and now - prev[k] > 6 * 3600:
                prev.pop(k, None)
        LAST_ALERT_PATH.write_text(json.dumps(prev))

    return 0 if not any(a[0] == "CRITICAL" for a in state["alerts"]) else 1


if __name__ == "__main__":
    sys.exit(main())
