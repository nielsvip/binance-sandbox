#!/usr/bin/env python3
"""
EZ AUTORESEARCH MONITOR — Live Safety Guard with Automatic Rollback
====================================================================
Watches live P&L after any config/script change and IMMEDIATELY reverts
if performance degrades. Runs as a daemon (every 60s via cron or loop).

Degradation triggers (ANY one fires = REVERT):
  DOLLAR_LOSS:          Unrealized P&L drops > $50 from baseline
  NEW_DEEP_LOSERS:      3+ positions go below -5% that weren't there before
  WIN_RATE_CRASH:       Win rate of trades in window < 30% (min 5 trades)
  POSITION_BLOWUP:      Any single position loses > 10% in the window
  EMERGENCY_BRAKE_SURGE: EMERGENCY_BRAKE blocks 3x vs pre-change rate

Usage:
  python3 ez_autoresearch_monitor.py              # Run once (cron mode)
  python3 ez_autoresearch_monitor.py --daemon      # Run continuously (60s loop)
  python3 ez_autoresearch_monitor.py --status      # Show current monitoring state
  python3 ez_autoresearch_monitor.py --force-revert # Force revert all pending changes
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════
# PATHS — binance-sandbox monitors LIVE binance/ changes
# ═══════════════════════════════════════════════════════════════════════
import platform
if platform.system() == "Linux":
    LIVE_BASE = Path("/home/niels/binance")
    SANDBOX_BASE = Path("/home/niels/binance-sandbox")
else:
    LIVE_BASE = Path("/Users/niels/Documents/binance")
    SANDBOX_BASE = LIVE_BASE  # local dev

AUTORESEARCH_DIR = SANDBOX_BASE / "data" / "autoresearch"
STATE_FILE = AUTORESEARCH_DIR / "monitor_state.json"
PENDING_FILE = AUTORESEARCH_DIR / "pending_change.json"
REVERT_LOG = AUTORESEARCH_DIR / "REVERT_LOG.md"
BACKUPS_DIR = LIVE_BASE / "backups"
LOG_FILE = AUTORESEARCH_DIR / "monitor.log"

# Accounts to monitor
CRYPTO_ACCOUNTS = ["ang", "inf", "men", "fin", "flz"]

# Files to watch for changes (in LIVE binance/)
WATCHED_FILES = [
    "config.py",
    "ez_rankings.py",
    "ez_indicators.py",
    "ez_breakout_agent.py",
    "ez_news_scanner.py",
    "ez_positions_quick.py",
    "ez_manage.py",
    "tradier_manage.py",
    "tradier_indicators.py",
    "tradier_rankings.py",
]

# ═══════════════════════════════════════════════════════════════════════
# THRESHOLDS — degradation triggers
# ═══════════════════════════════════════════════════════════════════════
DOLLAR_LOSS_THRESHOLD = -50.0          # Unrealized P&L drops > $50
NEW_DEEP_LOSER_THRESHOLD = -5.0        # Position gain% to count as "deep loser"
NEW_DEEP_LOSER_COUNT = 3               # How many new deep losers = trigger
WIN_RATE_MIN = 0.30                    # Min win rate in observation window
WIN_RATE_MIN_TRADES = 5                # Min trades before checking win rate
POSITION_BLOWUP_THRESHOLD = -10.0      # Single position loss% trigger
EMERGENCY_BRAKE_SURGE_MULT = 3.0       # 3x increase in EMERGENCY_BRAKE blocks
OBSERVATION_WINDOW_MINUTES = 30        # How long to watch after a change
COOLDOWN_SECONDS = 7200                # 2h cooldown after revert before re-applying
POLL_INTERVAL = 60                     # seconds between checks

# ═══════════════════════════════════════════════════════════════════════
# CRASH INTERVENTION — DC crossunder = actively reduce longs
# ═══════════════════════════════════════════════════════════════════════
CRASH_ENABLED = True
# How many TFs must show dc_low crossunder to trigger crash mode
CRASH_DC_LOW_MIN_TFS = 2               # 2+ TFs breaking dc_low = CRASH
# How many TFs showing dc_basis crossunder (weaker signal, but still bad)
CRASH_DC_BASIS_MIN_TFS = 3             # 3+ TFs breaking basis = WARNING → reduce
# Max gain% on a LONG to be eligible for crash reduction (don't cut winners)
CRASH_REDUCE_MAX_GAIN = 2.0            # Only reduce longs with gain < 2%
# Can't close losing longs (STRICT_NO_LOSS) — instead OPEN NEW SHORTS to rebalance
CRASH_SHORT_SIZE_USD = 15.0            # Size of each new crash-hedge short ($15)
CRASH_MAX_NEW_SHORTS = 5               # Max new shorts per intervention cycle
# Cooldown between crash interventions per symbol (seconds)
CRASH_COOLDOWN_PER_SYMBOL = 300        # 5 min cooldown per symbol
# Minimum L/S ratio to trigger (only intervene when dangerously long-heavy)
CRASH_MIN_LS_RATIO = 1.3               # Only if L/S > 1.3
# Redis channel for publishing new short orders
CRASH_OPEN_CHANNEL = "haiku_agent_augments"
# Accounts eligible for crash hedge shorts
CRASH_HEDGE_ACCOUNTS = ["ang", "inf", "men"]


# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════
def log(msg, also_print=True):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] MONITOR: {msg}"
    if also_print:
        print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════
def load_state():
    """Load monitor state from disk."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "file_mtimes": {},           # {filename: last_known_mtime}
        "observation": None,         # Active observation window or None
        "cooldowns": {},             # {param_name: expiry_timestamp}
        "last_check": None,
        "reverts_today": 0,
        "last_revert_date": None,
    }


def save_state(state):
    """Persist state to disk."""
    AUTORESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
# LIVE P&L SNAPSHOT — reads position files from live binance/
# ═══════════════════════════════════════════════════════════════════════
def snapshot_pnl():
    """Take a snapshot of current live P&L across all accounts."""
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_unrealized_pnl": 0.0,
        "total_notional": 0.0,
        "n_positions": 0,
        "n_winning": 0,
        "n_losing": 0,
        "deep_losers": [],       # positions with gain < -5%
        "worst_position": None,  # single worst position
        "per_account": {},
    }

    for acct in CRYPTO_ACCOUNTS:
        acct_pnl = 0.0
        acct_positions = 0
        acct_winners = 0

        for side in ["long", "short"]:
            pos_file = LIVE_BASE / acct / f"{side}_positions.json"
            if not pos_file.exists():
                continue

            try:
                with open(pos_file) as f:
                    positions = json.load(f)
            except Exception:
                continue

            if not isinstance(positions, dict):
                continue

            for pk, pos in positions.items():
                if not isinstance(pos, dict):
                    continue

                amt = abs(float(pos.get("positionAmt", 0)))
                if amt == 0:
                    continue

                gain = float(pos.get("gain", 0))
                entry = float(pos.get("entry_price", 0))
                mark = float(pos.get("mark_price", entry))
                unrealized = float(pos.get("unrealized_pnl_USD", 0))

                # If unrealized_pnl_USD not available, compute from gain
                if unrealized == 0 and entry > 0:
                    notional = amt * entry
                    unrealized = notional * (gain / 100.0)
                else:
                    notional = amt * mark if mark > 0 else amt * entry

                acct_pnl += unrealized
                acct_positions += 1
                snapshot["total_notional"] += notional

                if gain > 0:
                    acct_winners += 1
                    snapshot["n_winning"] += 1
                else:
                    snapshot["n_losing"] += 1

                if gain < NEW_DEEP_LOSER_THRESHOLD:
                    snapshot["deep_losers"].append({
                        "key": pk,
                        "gain": gain,
                        "unrealized": unrealized,
                        "account": acct,
                    })

                if snapshot["worst_position"] is None or gain < snapshot["worst_position"]["gain"]:
                    snapshot["worst_position"] = {
                        "key": pk,
                        "gain": gain,
                        "unrealized": unrealized,
                        "account": acct,
                    }

        snapshot["total_unrealized_pnl"] += acct_pnl
        snapshot["n_positions"] += acct_positions
        snapshot["per_account"][acct] = {
            "unrealized_pnl": acct_pnl,
            "positions": acct_positions,
            "winners": acct_winners,
        }

    return snapshot


# ═══════════════════════════════════════════════════════════════════════
# TRADE HISTORY — compute win rate from recent trades
# ═══════════════════════════════════════════════════════════════════════
def get_recent_trades(since_timestamp):
    """Get trades from history JSONL files since a given timestamp."""
    trades = []
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    for acct in CRYPTO_ACCOUNTS:
        history_dir = LIVE_BASE / "data" / "history" / acct
        if not history_dir.exists():
            continue

        # Check today's and yesterday's files
        for day_offset in [0, 1]:
            day = (datetime.now(timezone.utc) - timedelta(days=day_offset)).strftime("%Y%m%d")
            pattern = str(history_dir / f"*_{day}*.jsonl")
            for fpath in glob.glob(pattern):
                try:
                    with open(fpath) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                                ts = event.get("ts", "")
                                if ts >= since_timestamp:
                                    event["_account"] = acct
                                    trades.append(event)
                            except json.JSONDecodeError:
                                continue
                except Exception:
                    continue

    return trades


def compute_win_rate(trades):
    """Compute win rate from trade events (CLOSE and REDUCE events with gain info)."""
    closes = [t for t in trades if t.get("type") in ("CLOSE", "REDUCE")]
    if len(closes) < WIN_RATE_MIN_TRADES:
        return None, len(closes)

    wins = sum(1 for t in closes if float(t.get("gain", t.get("value", 0))) > 0)
    return wins / len(closes), len(closes)


# ═══════════════════════════════════════════════════════════════════════
# EMERGENCY BRAKE DETECTION — parse logs for block surges
# ═══════════════════════════════════════════════════════════════════════
def count_emergency_brakes(since_minutes=30):
    """Count EMERGENCY_BRAKE blocks in recent log lines."""
    count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    for acct in CRYPTO_ACCOUNTS:
        log_path = Path(f"/home/niels/logs/ez_manage_{acct}_app.log")
        if not log_path.exists():
            continue

        try:
            # Read last 5000 lines efficiently
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                read_size = min(size, 500_000)  # ~500KB tail
                f.seek(max(0, size - read_size))
                tail = f.read().decode("utf-8", errors="ignore")

            for line in tail.split("\n"):
                if "EMERGENCY_BRAKE" in line:
                    count += 1
        except Exception:
            continue

    return count


# ═══════════════════════════════════════════════════════════════════════
# FILE CHANGE DETECTION
# ═══════════════════════════════════════════════════════════════════════
def detect_file_changes(state):
    """Check if any watched files have been modified since last check."""
    changes = []
    current_mtimes = {}

    for filename in WATCHED_FILES:
        filepath = LIVE_BASE / filename
        if not filepath.exists():
            continue

        mtime = filepath.stat().st_mtime
        current_mtimes[filename] = mtime

        old_mtime = state["file_mtimes"].get(filename, 0)
        if mtime > old_mtime and old_mtime > 0:
            changes.append({
                "file": filename,
                "old_mtime": old_mtime,
                "new_mtime": mtime,
                "changed_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
            })

    state["file_mtimes"] = current_mtimes
    return changes


def find_backup_for(filename):
    """Find the most recent backup file for a given script."""
    if not BACKUPS_DIR.exists():
        return None

    stem = Path(filename).stem
    # Look for patterns: before_optimizer_{stem}_*, before_lab_{stem}_*, {stem}.*.bak
    candidates = []

    for pattern in [f"before_optimizer_{stem}_*", f"before_lab_{stem}_*", f"{stem}.*"]:
        for f in BACKUPS_DIR.glob(pattern):
            candidates.append((f.stat().st_mtime, f))

    # Also check autoresearch backups
    ar_backups = AUTORESEARCH_DIR / "backups"
    if ar_backups.exists():
        for f in ar_backups.glob(f"{Path(filename).name}.*"):
            candidates.append((f.stat().st_mtime, f))

    if not candidates:
        return None

    # Return most recent backup
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ═══════════════════════════════════════════════════════════════════════
# DEGRADATION CHECK — the core safety logic
# ═══════════════════════════════════════════════════════════════════════
def check_degradation(baseline, current, observation):
    """Check all degradation triggers. Returns (triggered, trigger_name, details)."""

    # Trigger 1: DOLLAR_LOSS — unrealized P&L dropped significantly
    pnl_delta = current["total_unrealized_pnl"] - baseline["total_unrealized_pnl"]
    if pnl_delta < DOLLAR_LOSS_THRESHOLD:
        return True, "DOLLAR_LOSS", (
            f"P&L dropped ${abs(pnl_delta):.2f} "
            f"(baseline: ${baseline['total_unrealized_pnl']:.2f} → "
            f"current: ${current['total_unrealized_pnl']:.2f})"
        )

    # Trigger 2: NEW_DEEP_LOSERS — new positions went deep red
    baseline_losers = set(d["key"] for d in baseline.get("deep_losers", []))
    current_losers = [d for d in current.get("deep_losers", []) if d["key"] not in baseline_losers]
    if len(current_losers) >= NEW_DEEP_LOSER_COUNT:
        loser_str = ", ".join(f"{d['key']}({d['gain']:.1f}%)" for d in current_losers[:5])
        return True, "NEW_DEEP_LOSERS", (
            f"{len(current_losers)} new positions below {NEW_DEEP_LOSER_THRESHOLD}%: {loser_str}"
        )

    # Trigger 3: WIN_RATE_CRASH — trades since change have terrible win rate
    since_ts = observation.get("started_at", "")
    if since_ts:
        trades = get_recent_trades(since_ts)
        wr, n_trades = compute_win_rate(trades)
        if wr is not None and wr < WIN_RATE_MIN:
            return True, "WIN_RATE_CRASH", (
                f"Win rate {wr:.1%} on {n_trades} trades since change (min: {WIN_RATE_MIN:.0%})"
            )

    # Trigger 4: POSITION_BLOWUP — single position catastrophic loss
    if current["worst_position"]:
        worst = current["worst_position"]
        baseline_worst_gain = None
        if baseline.get("worst_position"):
            # Check if this specific position got much worse
            for dl in baseline.get("deep_losers", []):
                if dl["key"] == worst["key"]:
                    baseline_worst_gain = dl["gain"]
                    break

        if baseline_worst_gain is not None:
            delta = worst["gain"] - baseline_worst_gain
            if delta < POSITION_BLOWUP_THRESHOLD:
                return True, "POSITION_BLOWUP", (
                    f"{worst['key']} dropped {delta:.1f}% "
                    f"(was {baseline_worst_gain:.1f}% → now {worst['gain']:.1f}%)"
                )
        elif worst["gain"] < POSITION_BLOWUP_THRESHOLD:
            # New position that immediately went deep
            if worst["key"] not in [d["key"] for d in baseline.get("deep_losers", [])]:
                return True, "POSITION_BLOWUP", (
                    f"New position {worst['key']} at {worst['gain']:.1f}%"
                )

    # Trigger 5: EMERGENCY_BRAKE_SURGE
    baseline_brakes = observation.get("baseline_emergency_brakes", 0)
    current_brakes = count_emergency_brakes(since_minutes=10)
    if baseline_brakes > 0 and current_brakes > baseline_brakes * EMERGENCY_BRAKE_SURGE_MULT:
        return True, "EMERGENCY_BRAKE_SURGE", (
            f"EMERGENCY_BRAKE blocks surged {baseline_brakes} → {current_brakes} "
            f"({current_brakes/max(baseline_brakes,1):.1f}x increase)"
        )

    return False, None, None


# ═══════════════════════════════════════════════════════════════════════
# REVERT — undo changes immediately
# ═══════════════════════════════════════════════════════════════════════
def revert_changes(changes, trigger_name, trigger_details, baseline, current, state):
    """Revert all changed files from backups."""
    reverted = []

    for change in changes:
        filename = change["file"]
        backup = find_backup_for(filename)

        if backup is None:
            log(f"  WARNING: No backup found for {filename} — cannot revert!")
            continue

        target = LIVE_BASE / filename
        try:
            shutil.copy2(str(backup), str(target))
            reverted.append({
                "file": filename,
                "backup_used": str(backup),
                "restored_mtime": backup.stat().st_mtime,
            })
            log(f"  REVERTED {filename} from {backup.name}")
        except Exception as e:
            log(f"  ERROR reverting {filename}: {e}")

    # Update file mtimes in state so we don't re-trigger on the revert itself
    for r in reverted:
        filepath = LIVE_BASE / r["file"]
        if filepath.exists():
            state["file_mtimes"][r["file"]] = filepath.stat().st_mtime

    # Set cooldowns
    pending = load_pending_change()
    if pending:
        param_name = pending.get("param", "unknown")
        expiry = time.time() + COOLDOWN_SECONDS
        state["cooldowns"][param_name] = expiry
        log(f"  COOLDOWN set for {param_name} until {datetime.fromtimestamp(expiry).strftime('%H:%M:%S')}")

    # Publish to Redis (if available)
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0)
        r.publish("autoresearch_revert", json.dumps({
            "trigger": trigger_name,
            "details": trigger_details,
            "files": [c["file"] for c in changes],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
        # Set cooldown key in Redis too (for autoresearch to check)
        if pending:
            r.setex(
                f"autoresearch_revert_cooldown:{pending.get('param', 'unknown')}",
                COOLDOWN_SECONDS,
                "1",
            )
        log(f"  Published revert alert to Redis")
    except Exception:
        pass  # Redis optional

    # Log to REVERT_LOG.md
    log_revert(trigger_name, trigger_details, changes, reverted, baseline, current)

    # Clear observation window
    state["observation"] = None

    # Clear pending change
    if PENDING_FILE.exists():
        try:
            # Rename to keep history
            archive = PENDING_FILE.with_suffix(f".reverted.{int(time.time())}.json")
            PENDING_FILE.rename(archive)
        except Exception:
            pass

    # Track daily reverts
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("last_revert_date") != today:
        state["reverts_today"] = 0
        state["last_revert_date"] = today
    state["reverts_today"] += 1

    return reverted


def log_revert(trigger, details, changes, reverted, baseline, current):
    """Append to REVERT_LOG.md."""
    AUTORESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    entry = f"""
## REVERT — {ts}

**Trigger:** `{trigger}`
**Details:** {details}

**P&L at baseline:** ${baseline['total_unrealized_pnl']:.2f} ({baseline['n_positions']} positions, {baseline['n_winning']} winning)
**P&L at revert:**   ${current['total_unrealized_pnl']:.2f} ({current['n_positions']} positions, {current['n_winning']} winning)
**Delta:** ${current['total_unrealized_pnl'] - baseline['total_unrealized_pnl']:.2f}

**Files changed:** {', '.join(c['file'] for c in changes)}
**Files reverted:** {', '.join(r['file'] for r in reverted)}
**Backups used:** {', '.join(r['backup_used'] for r in reverted)}

---
"""

    with open(REVERT_LOG, "a") as f:
        f.write(entry)

    log(f"  Logged revert to {REVERT_LOG}")


# ═══════════════════════════════════════════════════════════════════════
# PENDING CHANGE — integration with ez_autoresearch.py
# ═══════════════════════════════════════════════════════════════════════
def load_pending_change():
    """Load pending change written by autoresearch on KEEP."""
    if not PENDING_FILE.exists():
        return None
    try:
        with open(PENDING_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def confirm_change(state):
    """Mark a pending change as CONFIRMED (observation window passed without issues)."""
    pending = load_pending_change()
    if not pending:
        return

    log(f"  CONFIRMED: {pending.get('param', '?')} = {pending.get('new_value', '?')} passed observation window")

    # Archive the pending file
    archive = PENDING_FILE.with_suffix(f".confirmed.{int(time.time())}.json")
    try:
        PENDING_FILE.rename(archive)
    except Exception:
        pass

    # Update autoresearch results
    results_file = AUTORESEARCH_DIR / f"results_{pending.get('program', 'unknown')}.json"
    if results_file.exists():
        try:
            with open(results_file) as f:
                history = json.load(f)
            for h in reversed(history):
                if h.get("status") == "keep" and h.get("param") == pending.get("param"):
                    h["live_confirmed"] = True
                    h["confirmed_at"] = datetime.now(timezone.utc).isoformat()
                    break
            with open(results_file, "w") as f:
                json.dump(history, f, indent=2, default=str)
        except Exception:
            pass

    state["observation"] = None


# ═══════════════════════════════════════════════════════════════════════
# CRASH INTERVENTION — read DC indicators, reduce longs in crashes
# ═══════════════════════════════════════════════════════════════════════
def read_dc_indicators():
    """Read DC crossunder signals from indicator JSON files."""
    indicators_dir = LIVE_BASE / "data"
    results = {}  # {symbol: {dc_low_crossunder_TFs: [], dc_basis_crossunder_TFs: []}}

    for ind_file in indicators_dir.glob("indicators_worker*.json"):
        try:
            with open(ind_file) as f:
                data = json.load(f)
        except Exception:
            continue

        for symbol, ind in data.items():
            if not isinstance(ind, dict):
                continue

            if symbol not in results:
                results[symbol] = {
                    "dc_low_crossunder": [],
                    "dc_basis_crossunder": [],
                    "dc_high_crossover": [],
                    "price": ind.get("price", 0),
                }

            for tf in ["3m", "15m", "1h", "4h", "D"]:
                if ind.get(f"dc_low_crossunder_{tf}"):
                    results[symbol]["dc_low_crossunder"].append(tf)
                if ind.get(f"dc_basis_crossunder_{tf}"):
                    results[symbol]["dc_basis_crossunder"].append(tf)
                if ind.get(f"dc_high_crossover_{tf}"):
                    results[symbol]["dc_high_crossover"].append(tf)

    return results


def get_long_positions():
    """Get all open LONG positions with their details."""
    longs = []
    for acct in CRYPTO_ACCOUNTS:
        pos_file = LIVE_BASE / acct / "long_positions.json"
        if not pos_file.exists():
            continue
        try:
            with open(pos_file) as f:
                positions = json.load(f)
        except Exception:
            continue

        for pk, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            amt = abs(float(pos.get("positionAmt", 0)))
            if amt == 0:
                continue

            # Extract symbol from position key (e.g., "ang:BTCUSDT_LONG" → "BTCUSDT")
            symbol = pk.split(":")[-1].replace("_LONG", "")

            longs.append({
                "key": pk,
                "symbol": symbol,
                "account": acct,
                "amount": amt,
                "gain": float(pos.get("gain", 0)),
                "entry_price": float(pos.get("entry_price", 0)),
                "mark_price": float(pos.get("mark_price", 0)),
                "notional": amt * float(pos.get("entry_price", 0)),
            })

    return longs


def check_crash_and_intervene(state):
    """Check for market crash via DC signals and reduce vulnerable longs."""
    if not CRASH_ENABLED:
        return

    dc_signals = read_dc_indicators()
    if not dc_signals:
        return

    # Count symbols with dc_low crossunder on multiple TFs
    crashing_symbols = {}
    for symbol, sigs in dc_signals.items():
        n_low = len(sigs["dc_low_crossunder"])
        n_basis = len(sigs["dc_basis_crossunder"])

        if n_low >= CRASH_DC_LOW_MIN_TFS:
            crashing_symbols[symbol] = {
                "severity": "CRASH",
                "dc_low_tfs": sigs["dc_low_crossunder"],
                "dc_basis_tfs": sigs["dc_basis_crossunder"],
            }
        elif n_basis >= CRASH_DC_BASIS_MIN_TFS:
            crashing_symbols[symbol] = {
                "severity": "WARNING",
                "dc_low_tfs": sigs["dc_low_crossunder"],
                "dc_basis_tfs": sigs["dc_basis_crossunder"],
            }

    if not crashing_symbols:
        return

    # Check L/S ratio — only intervene when dangerously long-heavy
    longs = get_long_positions()
    snap = snapshot_pnl()
    shorts_count = snap["n_positions"] - len(longs)
    ls_ratio = len(longs) / max(shorts_count, 1)

    if ls_ratio < CRASH_MIN_LS_RATIO:
        return  # L/S ratio is fine, no intervention needed

    # STRICT_NO_LOSS = can't close losing longs
    # Instead: OPEN NEW SHORTS on crashing symbols to rebalance the portfolio
    crash_cooldowns = state.get("crash_cooldowns", {})
    now = time.time()

    # Pick the best symbols to short: crashing + we hold losing longs in them
    short_candidates = []
    for pos in longs:
        symbol = pos["symbol"]
        if symbol not in crashing_symbols:
            continue
        if pos["gain"] > CRASH_REDUCE_MAX_GAIN:
            continue  # This long is winning despite crash — skip

        # Check cooldown
        if crash_cooldowns.get(symbol, 0) > now:
            continue

        severity = crashing_symbols[symbol]["severity"]
        short_candidates.append({
            "symbol": symbol,
            "severity": severity,
            "long_gain": pos["gain"],
            "long_notional": pos["notional"],
            "dc_low_tfs": crashing_symbols[symbol]["dc_low_tfs"],
            "dc_basis_tfs": crashing_symbols[symbol]["dc_basis_tfs"],
            "price": dc_signals[symbol]["price"],
        })

    if not short_candidates:
        return

    # Deduplicate by symbol (pick worst long per symbol)
    by_symbol = {}
    for c in short_candidates:
        sym = c["symbol"]
        if sym not in by_symbol or c["long_gain"] < by_symbol[sym]["long_gain"]:
            by_symbol[sym] = c
    short_candidates = sorted(by_symbol.values(), key=lambda x: (
        0 if x["severity"] == "CRASH" else 1,
        x["long_gain"],
    ))

    log(f"CRASH INTERVENTION: {len(crashing_symbols)} symbols crashing, "
        f"L/S={ls_ratio:.2f}, {len(short_candidates)} short candidates")

    # Publish OPEN SHORT orders via Redis
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0)
    except Exception as e:
        log(f"  ERROR: Cannot connect to Redis: {e}")
        return

    opened = []
    # Round-robin across eligible accounts
    acct_idx = 0
    for cand in short_candidates[:CRASH_MAX_NEW_SHORTS]:
        acct = CRASH_HEDGE_ACCOUNTS[acct_idx % len(CRASH_HEDGE_ACCOUNTS)]
        acct_idx += 1

        price = cand["price"]
        if price <= 0:
            continue
        qty = CRASH_SHORT_SIZE_USD / price

        position_key = f"{acct}:{cand['symbol']}_SHORT"
        dc_tfs = cand["dc_low_tfs"] or cand["dc_basis_tfs"]

        order = {
            "position_key": position_key,
            "action": "OPEN",
            "side": "SELL",
            "qty": qty,
            "reason": f"CRASH_HEDGE_{cand['severity']}",
            "source": "autoresearch_monitor",
            "dc_crossunder_tfs": dc_tfs,
            "opposing_long_gain": cand["long_gain"],
            "ls_ratio": ls_ratio,
        }

        try:
            r.publish(CRASH_OPEN_CHANNEL, json.dumps(order))
            opened.append(cand)
            crash_cooldowns[cand["symbol"]] = now + CRASH_COOLDOWN_PER_SYMBOL
            log(f"  OPEN SHORT {cand['severity']}: {position_key} "
                f"(${CRASH_SHORT_SIZE_USD}, opposing long at {cand['long_gain']:.1f}%, "
                f"dc_low={dc_tfs})")
        except Exception as e:
            log(f"  ERROR publishing short for {cand['symbol']}: {e}")

    state["crash_cooldowns"] = crash_cooldowns

    if opened:
        # Log crash intervention
        crash_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "OPEN_SHORTS",
            "crashing_symbols": {s: info for s, info in crashing_symbols.items()},
            "ls_ratio_before": ls_ratio,
            "shorts_opened": [{
                "symbol": c["symbol"],
                "severity": c["severity"],
                "opposing_long_gain": c["long_gain"],
                "dc_tfs": c["dc_low_tfs"] or c["dc_basis_tfs"],
            } for c in opened],
        }

        crash_log = AUTORESEARCH_DIR / "CRASH_INTERVENTIONS.jsonl"
        with open(crash_log, "a") as f:
            f.write(json.dumps(crash_entry, default=str) + "\n")

        log(f"  CRASH INTERVENTION: Opened {len(opened)} new shorts to rebalance. "
            f"Logged to CRASH_INTERVENTIONS.jsonl")


# ═══════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════
def run_once():
    """Run one monitoring cycle."""
    state = load_state()

    # Clean expired cooldowns
    now = time.time()
    state["cooldowns"] = {k: v for k, v in state["cooldowns"].items() if v > now}
    state["crash_cooldowns"] = {k: v for k, v in state.get("crash_cooldowns", {}).items() if v > now}

    # CRASH INTERVENTION — check DC crossunders and open shorts if needed
    try:
        check_crash_and_intervene(state)
    except Exception as e:
        log(f"  ERROR in crash intervention: {e}")

    # Check for file changes
    changes = detect_file_changes(state)

    if changes and state["observation"] is None:
        # NEW CHANGE DETECTED — start observation window
        log(f"CHANGE DETECTED: {', '.join(c['file'] for c in changes)}")
        baseline = snapshot_pnl()
        baseline_brakes = count_emergency_brakes(since_minutes=10)

        state["observation"] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=OBSERVATION_WINDOW_MINUTES)).isoformat(),
            "changes": changes,
            "baseline": baseline,
            "baseline_emergency_brakes": baseline_brakes,
            "samples": [],
        }
        log(f"  Baseline P&L: ${baseline['total_unrealized_pnl']:.2f} "
            f"({baseline['n_positions']} pos, {baseline['n_winning']} winning, "
            f"{len(baseline['deep_losers'])} deep losers)")
        log(f"  Observation window: {OBSERVATION_WINDOW_MINUTES} min")

    elif state["observation"]:
        # IN OBSERVATION WINDOW — check for degradation
        obs = state["observation"]
        current = snapshot_pnl()
        baseline = obs["baseline"]

        # Record sample
        obs["samples"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pnl": current["total_unrealized_pnl"],
            "n_positions": current["n_positions"],
            "n_winning": current["n_winning"],
            "deep_losers": len(current["deep_losers"]),
        })

        # Check degradation
        triggered, trigger_name, trigger_details = check_degradation(baseline, current, obs)

        if triggered:
            log(f"DEGRADATION DETECTED: {trigger_name}")
            log(f"  {trigger_details}")

            reverted = revert_changes(
                obs["changes"], trigger_name, trigger_details,
                baseline, current, state,
            )

            if reverted:
                log(f"  REVERTED {len(reverted)} files. System protected.")
            else:
                log(f"  WARNING: Could not revert any files!")
        else:
            # Check if observation window expired
            expires_at = obs.get("expires_at", "")
            if expires_at and datetime.now(timezone.utc).isoformat() >= expires_at:
                # Window passed — change is CONFIRMED
                pnl_delta = current["total_unrealized_pnl"] - baseline["total_unrealized_pnl"]
                log(f"OBSERVATION COMPLETE: No degradation detected over {OBSERVATION_WINDOW_MINUTES} min")
                log(f"  P&L delta: ${pnl_delta:+.2f} "
                    f"(baseline: ${baseline['total_unrealized_pnl']:.2f} → "
                    f"current: ${current['total_unrealized_pnl']:.2f})")
                confirm_change(state)
            else:
                # Still observing
                elapsed = len(obs["samples"])
                pnl_delta = current["total_unrealized_pnl"] - baseline["total_unrealized_pnl"]
                if elapsed % 5 == 0:  # Log every 5 minutes
                    log(f"  Observing... {elapsed}min elapsed, P&L delta: ${pnl_delta:+.2f}")

    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


def show_status():
    """Show current monitoring state."""
    state = load_state()

    print("=" * 70)
    print("AUTORESEARCH MONITOR STATUS")
    print("=" * 70)

    print(f"Last check: {state.get('last_check', 'never')}")
    print(f"Reverts today: {state.get('reverts_today', 0)}")

    # Active observation
    obs = state.get("observation")
    if obs:
        print(f"\nACTIVE OBSERVATION:")
        print(f"  Started: {obs.get('started_at', '?')}")
        print(f"  Expires: {obs.get('expires_at', '?')}")
        print(f"  Changes: {', '.join(c['file'] for c in obs.get('changes', []))}")
        bl = obs.get("baseline", {})
        print(f"  Baseline P&L: ${bl.get('total_unrealized_pnl', 0):.2f}")
        samples = obs.get("samples", [])
        if samples:
            latest = samples[-1]
            print(f"  Latest P&L:   ${latest.get('pnl', 0):.2f} ({len(samples)} samples)")
    else:
        print(f"\nNo active observation window.")

    # Cooldowns
    cooldowns = state.get("cooldowns", {})
    active_cd = {k: v for k, v in cooldowns.items() if v > time.time()}
    if active_cd:
        print(f"\nActive cooldowns:")
        for param, expiry in active_cd.items():
            remaining = int(expiry - time.time())
            print(f"  {param}: {remaining}s remaining")
    else:
        print(f"\nNo active cooldowns.")

    # Current snapshot
    snap = snapshot_pnl()
    print(f"\nCurrent live P&L: ${snap['total_unrealized_pnl']:.2f}")
    print(f"  Positions: {snap['n_positions']} ({snap['n_winning']} winning, {snap['n_losing']} losing)")
    print(f"  Deep losers (<{NEW_DEEP_LOSER_THRESHOLD}%): {len(snap['deep_losers'])}")
    if snap["worst_position"]:
        w = snap["worst_position"]
        print(f"  Worst: {w['key']} at {w['gain']:.1f}% (${w['unrealized']:.2f})")

    # Pending change
    pending = load_pending_change()
    if pending:
        print(f"\nPending change: {pending.get('param', '?')} = {pending.get('new_value', '?')}")
        print(f"  From: {pending.get('program', '?')} at {pending.get('timestamp', '?')}")

    # Revert log
    if REVERT_LOG.exists():
        with open(REVERT_LOG) as f:
            content = f.read()
        reverts = content.count("## REVERT")
        print(f"\nTotal reverts logged: {reverts}")

    print("=" * 70)


def force_revert():
    """Force revert all pending changes immediately."""
    state = load_state()
    obs = state.get("observation")

    if not obs:
        log("No active observation — nothing to revert")
        return

    changes = obs.get("changes", [])
    if not changes:
        log("No changes tracked in observation")
        return

    current = snapshot_pnl()
    baseline = obs.get("baseline", current)

    log("FORCE REVERT requested by operator")
    reverted = revert_changes(
        changes, "FORCE_REVERT", "Manual operator override",
        baseline, current, state,
    )

    if reverted:
        log(f"Force-reverted {len(reverted)} files")
    else:
        log("WARNING: No files could be reverted")

    save_state(state)


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════
def main():
    AUTORESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(description="Autoresearch Safety Monitor")
    parser.add_argument("--daemon", action="store_true", help="Run continuously (60s loop)")
    parser.add_argument("--status", action="store_true", help="Show monitoring state")
    parser.add_argument("--force-revert", action="store_true", help="Force revert pending changes")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.force_revert:
        force_revert()
        return

    if args.daemon:
        log("Monitor daemon starting (60s interval)")
        while True:
            try:
                run_once()
            except Exception as e:
                log(f"ERROR in monitor cycle: {e}")
            time.sleep(POLL_INTERVAL)
    else:
        # Single run (cron mode)
        try:
            run_once()
        except Exception as e:
            log(f"ERROR: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
