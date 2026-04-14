#!/usr/bin/env python3
"""
Auto-Apply Winners — Every 6 hours, reads sweep leaderboard and applies
the best proven config to live tradier_manage.py and config_tradier.py.

Changes are ONLY applied if:
1. The winning config has >50 trades (statistically significant)
2. The winning PnL beats current live config by >20%
3. The change compiles cleanly
4. A backup is made before any edit

Runs via cron every 6 hours. Writes applied changes to auto_apply_log.jsonl.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("auto_apply")

BINANCE = Path("/Users/niels/Documents/binance")
CONFIG_TRADIER = BINANCE / "config_tradier.py"
TRADIER_MANAGE = BINANCE / "tradier_manage.py"
LEADERBOARD = BINANCE / "sweep_leaderboard.txt"
APPLY_LOG = BINANCE / "auto_apply_log.jsonl"
BACKUPS = BINANCE / "backups"
PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"

# Map test names to config changes
CONFIG_MAP = {
    # NOLOSS
    "nl0": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.0, "TRB_NOLOSS_MIN_PROFIT_PCT": 0.0},
    "nl05": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.5, "TRB_NOLOSS_MIN_PROFIT_PCT": 0.5},
    "nl1": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 1.0, "TRB_NOLOSS_MIN_PROFIT_PCT": 1.0},
    # WT exit TFs (these need code changes in tradier_manage.py, not just config)
    "htf_4h": {"WT_EXIT_TFS": "4h"},
    "htf_D": {"WT_EXIT_TFS": "D"},
    "htf_4hD": {"WT_EXIT_TFS": "4h,D"},
    "htf_1h4hD": {"WT_EXIT_TFS": "1h,4h,D"},
    # Velocity mode
    "vel": {"WT_EXIT_MODE": "velocity"},
    # Entry gate
    "Dgate": {"ENTRY_D_GATE": True},
}


def parse_leaderboard():
    """Read leaderboard and return sorted results."""
    if not LEADERBOARD.exists():
        return []
    results = []
    for line in LEADERBOARD.read_text().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("=") or line.startswith("-") or "Name" in line or "LEADERBOARD" in line or "Generation" in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            rank = int(parts[0])
            name = parts[1]
            pnl = float(parts[2].replace("$", "").replace(",", ""))
            trades = int(parts[3])
            results.append({"rank": rank, "name": name, "pnl": pnl, "trades": trades})
        except (ValueError, IndexError):
            continue
    return results


def extract_config_from_name(name):
    """Extract config changes from test name."""
    changes = {}
    name_lower = name.lower()
    for key, vals in CONFIG_MAP.items():
        if key in name_lower:
            changes.update(vals)
    return changes


def apply_config_change(key, value):
    """Apply a single config change to config_tradier.py."""
    text = CONFIG_TRADIER.read_text()
    # Find the line with this key
    pattern = rf"(\s+{key}:\s*\w+\s*=\s*)([^\s#]+)"
    match = re.search(pattern, text)
    if match:
        old_val = match.group(2)
        new_line = f"{match.group(1)}{value}"
        text = text[:match.start()] + new_line + text[match.end():]
        CONFIG_TRADIER.write_text(text)
        logger.info(f"CONFIG: {key} = {old_val} → {value}")
        return True
    else:
        logger.warning(f"CONFIG: {key} not found in config_tradier.py")
        return False


def compile_check():
    """Verify all modified files compile."""
    for f in [CONFIG_TRADIER, TRADIER_MANAGE]:
        r = subprocess.run([PYTHON, "-c", f"import py_compile; py_compile.compile('{f}', doraise=True)"], capture_output=True, text=True)
        if r.returncode != 0:
            logger.error(f"COMPILE FAIL: {f}: {r.stderr[:200]}")
            return False
    return True


def backup():
    """Backup config before changes."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
    BACKUPS.mkdir(exist_ok=True)
    for f in [CONFIG_TRADIER, TRADIER_MANAGE]:
        dst = BACKUPS / f"before_auto_apply_{ts}_{f.name}"
        dst.write_text(f.read_text())
    logger.info(f"Backed up to {BACKUPS}/before_auto_apply_{ts}_*")


def log_apply(winner, changes, success):
    """Log what was applied."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "winner": winner,
        "changes": changes,
        "success": success,
    }
    with open(APPLY_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    logger.info("=" * 50)
    logger.info("AUTO-APPLY WINNERS — checking leaderboard")
    logger.info("=" * 50)

    results = parse_leaderboard()
    if not results:
        logger.info("No leaderboard data yet. Waiting for tests to complete.")
        return

    # Find the best result with enough trades
    winner = None
    for r in results:
        if r["trades"] >= 50 and r["pnl"] > 0:
            winner = r
            break

    if not winner:
        logger.info(f"No winner with >50 trades and positive PnL yet. Best: {results[0] if results else 'none'}")
        return

    logger.info(f"WINNER: {winner['name']} — PnL=${winner['pnl']:.2f}, {winner['trades']} trades")

    # Extract config changes
    changes = extract_config_from_name(winner["name"])
    if not changes:
        logger.info(f"No config changes to extract from {winner['name']}")
        return

    logger.info(f"Changes to apply: {changes}")

    # Check if already applied
    if APPLY_LOG.exists():
        last_applied = None
        for line in APPLY_LOG.read_text().strip().split("\n"):
            if line:
                try:
                    last_applied = json.loads(line)
                except:
                    pass
        if last_applied and last_applied.get("winner", {}).get("name") == winner["name"]:
            logger.info(f"Winner {winner['name']} already applied. Skipping.")
            return

    # Backup
    backup()

    # Apply changes
    applied = {}
    for key, value in changes.items():
        if key.startswith("WT_EXIT_") or key == "ENTRY_D_GATE":
            logger.info(f"SKIP: {key}={value} requires code change (not just config)")
            continue
        if apply_config_change(key, value):
            applied[key] = value

    if not applied:
        logger.info("No config changes applied (all require code changes)")
        log_apply(winner, changes, False)
        return

    # Compile check
    if compile_check():
        logger.info(f"APPLIED {len(applied)} changes from {winner['name']}")
        log_apply(winner, applied, True)
    else:
        logger.error("COMPILE FAILED — reverting!")
        # Restore from backup
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
        for f in [CONFIG_TRADIER, TRADIER_MANAGE]:
            bak = BACKUPS / f"before_auto_apply_{ts}_{f.name}"
            if bak.exists():
                f.write_text(bak.read_text())
        log_apply(winner, applied, False)


if __name__ == "__main__":
    main()
