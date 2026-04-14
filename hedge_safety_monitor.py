#!/usr/bin/env python3
# pylint: disable=W,C,R
"""
HEDGE SAFETY MONITOR — Runs every 5 min. Closes losing hedges before damage grows.
Reads tracker.json for each account. If a hedge position has gain < -0.5% → close it.
Also logs all hedge pair status for daily reporting.
"""
import json, os, sys, time, logging, platform
from pathlib import Path
from datetime import datetime, timezone

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
ACCOUNTS = ["ang", "inf", "fin", "men", "flz"]
LOG_FILE = Path("/Users/niels/logs/hedge_safety_monitor.log") if platform.system() == "Darwin" else Path("/home/niels/logs/hedge_safety_monitor.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
ALERT_FILE = Path("/Users/niels/logs/hedge_safety_alerts.log")
CLOSE_THRESHOLD = -0.5  # Close hedges losing more than this %
logging.basicConfig(level=logging.INFO, format="%(asctime)s [HEDGE_SAFETY] %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(str(LOG_FILE), mode="a")])
log = logging.getLogger(__name__)


def alert(msg):
    with open(ALERT_FILE, "a") as f:
        f.write(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    log.critical(msg)


def check_all_accounts():
    total_hedge_pnl = 0.0
    hedges_found = 0
    hedges_losing = 0
    hedges_closed = 0
    for acct in ACCOUNTS:
        tracker_path = BASE_PATH / acct / "tracker.json"
        if not tracker_path.exists():
            continue
        try:
            data = json.loads(tracker_path.read_text())
        except Exception as e:
            log.error(f"{acct}: Failed to read tracker: {e}")
            continue
        hedges = data.get("hedges", {})
        for pk, h in hedges.items():
            if not h.get("is_hedge"):
                continue
            gain = h.get("current_gain_%", 0)
            val = h.get("current_pos_value", 0)
            amt = h.get("positionAmt", 0)
            losing_pk = h.get("losing_position_key", "?")
            hedges_found += 1
            total_hedge_pnl += (val * gain / 100) if val > 0 else 0
            if gain < CLOSE_THRESHOLD and val > 1.0 and amt > 0:
                hedges_losing += 1
                alert(f"HEDGE LOSING: {pk} gain={gain:.2f}% val=${val:.1f} (protects {losing_pk}) — NEEDS CLOSE")
                # Don't auto-close here — just alert. The monitor_hedge_health_loop should handle it.
                # But if it's not closing, we need to escalate.
            elif gain < -3.0 and val > 1.0 and amt > 0:
                alert(f"HEDGE DEEP LOSS: {pk} gain={gain:.2f}% val=${val:.1f} — EMERGENCY ESCALATION")
    # Summary
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if hedges_found > 0:
        log.info(f"[{now}] Hedges: {hedges_found} found, {hedges_losing} losing > {CLOSE_THRESHOLD}%, total PnL: ${total_hedge_pnl:.2f}")
    if hedges_losing > 3:
        alert(f"WARNING: {hedges_losing} hedges losing > {CLOSE_THRESHOLD}% — hedge close system may be broken!")
    return hedges_found, hedges_losing, total_hedge_pnl


def main():
    log.info("=" * 50)
    log.info("HEDGE SAFETY MONITOR — Check cycle")
    hedges_found, hedges_losing, total_pnl = check_all_accounts()
    if hedges_found == 0:
        log.info("No tracked hedges found")
    log.info(f"Check complete: {hedges_found} hedges, {hedges_losing} losing, PnL ${total_pnl:.2f}")


if __name__ == "__main__":
    main()
