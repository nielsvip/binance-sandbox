"""
ez_key_levels_monitor.py — Key Level Intervention Monitor
==========================================================
Runs every 60 seconds. When dc_low breaks on multiple timeframes:
  - IMMEDIATELY reduces/closes LONG positions (crash protection)
  - Opens emergency shorts if crash severity >= 3
  - Logs all interventions for audit

When dc_high breaks on multiple timeframes:
  - Favors longs, blocks new shorts
  - Can augment winning longs

This is the OVERRIDE system — it supersedes normal trading logic
when market structure breaks down.
"""

import asyncio
import json
import logging
import os
import platform
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup — NEVER hardcode paths
# ---------------------------------------------------------------------------
if platform.system() == "Darwin":
    _BASE = Path("/Users/niels/Documents/binance")
else:
    _BASE = Path("/home/niels/binance")

ENV_BASE = os.environ.get("BASE_PATH")
if ENV_BASE:
    _BASE = Path(ENV_BASE)

sys.path.insert(0, str(_BASE))

from ez_key_levels import (
    compute_all_key_levels,
    load_key_levels,
    save_key_levels,
    load_symbols,
    KEY_LEVELS_DIR,
)

DATA_DIR = _BASE / "data"
POSITIONS_DIR = DATA_DIR / "positions"
INTERVENTION_LOG = KEY_LEVELS_DIR / "INTERVENTIONS.jsonl"
STATE_FILE = KEY_LEVELS_DIR / "monitor_state.json"

LOG_DIR = Path("/Users/niels/logs") if platform.system() == "Darwin" else Path("/home/niels/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ez_key_levels_monitor.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("kl_monitor")


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Scan interval
SCAN_INTERVAL_S = 60

# Crash intervention thresholds
CRASH_REDUCE_THRESHOLD = 2   # 2 TFs with dc_low broken → start reducing longs
CRASH_CLOSE_THRESHOLD = 3    # 3 TFs broken → close ALL longs
CRASH_SHORT_THRESHOLD = 4    # 4 TFs broken → open emergency shorts

# Breakout thresholds
BREAKOUT_ADD_THRESHOLD = 3   # 3 TFs with dc_high broken → add longs

# Cooldown: don't intervene on same symbol within N seconds
INTERVENTION_COOLDOWN_S = 300

# Max % of position to reduce per intervention
REDUCE_PCT_CRASH_2 = 0.50    # 50% reduce at severity 2
REDUCE_PCT_CRASH_3 = 1.00    # 100% close at severity 3+
EMERGENCY_SHORT_USD = 20.0   # Emergency short size

# Accounts to protect
PROTECTED_ACCOUNTS = ["ang", "inf", "men", "fin", "flz"]


# ═══════════════════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

class MonitorState:
    """Track intervention cooldowns and state."""

    def __init__(self):
        self.last_intervention: Dict[str, float] = {}  # symbol → timestamp
        self.crash_counts: Dict[str, int] = {}  # symbol → consecutive crash scans
        self.interventions_today: int = 0
        self.load()

    def load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                self.last_intervention = data.get("last_intervention", {})
                self.crash_counts = data.get("crash_counts", {})
                self.interventions_today = data.get("interventions_today", 0)
            except Exception:
                pass

    def save(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "last_intervention": self.last_intervention,
                    "crash_counts": self.crash_counts,
                    "interventions_today": self.interventions_today,
                    "updated": datetime.now(timezone.utc).isoformat(),
                }, f, indent=2)
        except Exception as e:
            log.error(f"Failed saving state: {e}")

    def can_intervene(self, symbol: str) -> bool:
        last = self.last_intervention.get(symbol, 0)
        return (time.time() - last) > INTERVENTION_COOLDOWN_S

    def record_intervention(self, symbol: str):
        self.last_intervention[symbol] = time.time()
        self.interventions_today += 1
        self.save()

    def increment_crash(self, symbol: str) -> int:
        self.crash_counts[symbol] = self.crash_counts.get(symbol, 0) + 1
        return self.crash_counts[symbol]

    def clear_crash(self, symbol: str):
        self.crash_counts.pop(symbol, None)


# ═══════════════════════════════════════════════════════════════════════════
# POSITION READING
# ═══════════════════════════════════════════════════════════════════════════

def load_positions() -> Dict[str, Dict]:
    """Load all active positions from disk."""
    positions = {}

    # Try positions directory
    if POSITIONS_DIR.exists():
        for fpath in POSITIONS_DIR.glob("*.json"):
            try:
                with open(fpath) as f:
                    pos = json.load(f)
                if isinstance(pos, dict):
                    pk = fpath.stem  # position_key from filename
                    positions[pk] = pos
            except Exception:
                continue

    # Fallback: try all_positions.json
    all_pos_file = DATA_DIR / "all_positions.json"
    if all_pos_file.exists() and not positions:
        try:
            with open(all_pos_file) as f:
                data = json.load(f)
            if isinstance(data, dict):
                positions.update(data)
        except Exception:
            pass

    return positions


def get_long_positions(positions: Dict[str, Dict], symbol: str) -> List[Dict]:
    """Get all LONG positions for a symbol across accounts."""
    result = []
    for pk, pos in positions.items():
        if not pk.endswith("_LONG"):
            continue
        # Extract symbol from position key: account:SYMBOL_LONG
        parts = pk.split(":", 1)
        if len(parts) < 2:
            continue
        sym_part = parts[1].replace("_LONG", "")
        if sym_part == symbol:
            amt = pos.get("positionAmt", 0)
            if isinstance(amt, str):
                try:
                    amt = float(amt)
                except ValueError:
                    continue
            if abs(amt) > 0:
                result.append({
                    "position_key": pk,
                    "account": parts[0],
                    "symbol": symbol,
                    "positionAmt": amt,
                    "entry_price": pos.get("entry_price", 0),
                    "gain": pos.get("gain", 0),
                    "current_price": pos.get("current_price", 0),
                    "notional": abs(amt) * pos.get("current_price", pos.get("entry_price", 0)),
                })
    return result


def get_short_positions(positions: Dict[str, Dict], symbol: str) -> List[Dict]:
    """Get all SHORT positions for a symbol across accounts."""
    result = []
    for pk, pos in positions.items():
        if not pk.endswith("_SHORT"):
            continue
        parts = pk.split(":", 1)
        if len(parts) < 2:
            continue
        sym_part = parts[1].replace("_SHORT", "")
        if sym_part == symbol:
            amt = pos.get("positionAmt", 0)
            if isinstance(amt, str):
                try:
                    amt = float(amt)
                except ValueError:
                    continue
            if abs(amt) > 0:
                result.append({
                    "position_key": pk,
                    "account": parts[0],
                    "symbol": symbol,
                    "positionAmt": amt,
                    "notional": abs(amt) * pos.get("current_price", pos.get("entry_price", 0)),
                })
    return result


# ═══════════════════════════════════════════════════════════════════════════
# INTERVENTION ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

def log_intervention(action: str, symbol: str, details: Dict):
    """Append intervention to JSONL log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "symbol": symbol,
        **details,
    }
    try:
        with open(INTERVENTION_LOG, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log.error(f"Failed logging intervention: {e}")


async def reduce_long_positions(symbol: str, positions: List[Dict],
                                 reduce_pct: float, reason: str) -> int:
    """
    Reduce LONG positions for a symbol.
    Returns count of interventions made.

    NOTE: This creates intervention SIGNALS that the trading system reads.
    It does NOT directly place orders (safety: only the trading system places orders).
    """
    count = 0
    signals_dir = KEY_LEVELS_DIR / "signals"
    signals_dir.mkdir(exist_ok=True)

    for pos in positions:
        pk = pos["position_key"]
        amt = pos["positionAmt"]
        account = pos["account"]

        if account not in PROTECTED_ACCOUNTS:
            continue

        reduce_amt = abs(amt) * reduce_pct
        if reduce_amt < 0.001:
            continue

        signal = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "position_key": pk,
            "account": account,
            "symbol": symbol,
            "side": "SELL",
            "position_side": "LONG",
            "action": "REDUCE" if reduce_pct < 1.0 else "CLOSE",
            "qty": reduce_amt,
            "reduce_pct": reduce_pct,
            "reason": reason,
            "urgency": "CRITICAL",
            "source": "key_levels_monitor",
        }

        signal_file = signals_dir / f"{pk.replace(':', '_')}_REDUCE.json"
        try:
            with open(signal_file, "w") as f:
                json.dump(signal, f, indent=2)
            count += 1
            log.warning(f"INTERVENTION: {reason} — {pk} reduce {reduce_pct*100:.0f}% "
                       f"(amt={amt}, reduce={reduce_amt:.4f})")
            log_intervention("REDUCE_LONG", symbol, {
                "position_key": pk,
                "account": account,
                "reduce_pct": reduce_pct,
                "reduce_amt": reduce_amt,
                "positionAmt": amt,
                "reason": reason,
            })
        except Exception as e:
            log.error(f"Failed writing signal for {pk}: {e}")

    return count


async def signal_emergency_short(symbol: str, accounts: List[str],
                                  reason: str, size_usd: float = EMERGENCY_SHORT_USD) -> int:
    """
    Signal the trading system to open emergency short positions.
    """
    count = 0
    signals_dir = KEY_LEVELS_DIR / "signals"
    signals_dir.mkdir(exist_ok=True)

    for account in accounts:
        if account not in PROTECTED_ACCOUNTS:
            continue

        signal = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "account": account,
            "symbol": symbol,
            "side": "SELL",
            "position_side": "SHORT",
            "action": "OPEN",
            "size_usd": size_usd,
            "reason": reason,
            "urgency": "CRITICAL",
            "source": "key_levels_monitor",
        }

        signal_file = signals_dir / f"{account}_{symbol}_EMERGENCY_SHORT.json"
        try:
            with open(signal_file, "w") as f:
                json.dump(signal, f, indent=2)
            count += 1
            log.warning(f"EMERGENCY SHORT: {account}:{symbol} ${size_usd} — {reason}")
            log_intervention("EMERGENCY_SHORT", symbol, {
                "account": account,
                "size_usd": size_usd,
                "reason": reason,
            })
        except Exception as e:
            log.error(f"Failed writing emergency short signal: {e}")

    return count


# ═══════════════════════════════════════════════════════════════════════════
# MAIN SCAN LOOP
# ═══════════════════════════════════════════════════════════════════════════

async def scan_and_intervene(state: MonitorState, symbols: List[str]):
    """
    One scan cycle:
    1. Compute key levels for all symbols
    2. Check for crash/breakout signals
    3. Intervene if thresholds are breached
    """
    positions = load_positions()
    interventions = 0

    for symbol in symbols:
        try:
            levels = compute_all_key_levels(symbol)
            save_key_levels(levels, symbol)

            crash_severity = levels.get("crash_severity", 0)
            breakout_strength = levels.get("breakout_strength", 0)
            dc_lows_broken = levels.get("dc_lows_broken", [])
            dc_highs_broken = levels.get("dc_highs_broken", [])
            action = levels.get("action", "HOLD")

            # --- CRASH HANDLING ---
            if crash_severity >= CRASH_REDUCE_THRESHOLD:
                consecutive = state.increment_crash(symbol)
                log.warning(f"CRASH DETECTED: {symbol} severity={crash_severity} "
                           f"consecutive={consecutive} dc_lows_broken={dc_lows_broken}")

                if not state.can_intervene(symbol):
                    log.info(f"  Cooldown active for {symbol}, skipping intervention")
                    continue

                longs = get_long_positions(positions, symbol)
                if not longs:
                    log.info(f"  No long positions for {symbol}")
                    continue

                # Severity 2: reduce 50% of longs
                if crash_severity == CRASH_REDUCE_THRESHOLD:
                    reason = f"CRASH_REDUCE_S{crash_severity}_DC_BROKEN_{'+'.join(dc_lows_broken)}"
                    cnt = await reduce_long_positions(
                        symbol, longs, REDUCE_PCT_CRASH_2, reason
                    )
                    interventions += cnt

                # Severity 3+: close ALL longs
                elif crash_severity >= CRASH_CLOSE_THRESHOLD:
                    reason = f"CRASH_CLOSE_S{crash_severity}_DC_BROKEN_{'+'.join(dc_lows_broken)}"
                    cnt = await reduce_long_positions(
                        symbol, longs, REDUCE_PCT_CRASH_3, reason
                    )
                    interventions += cnt

                # Severity 4+: also open emergency shorts
                if crash_severity >= CRASH_SHORT_THRESHOLD:
                    shorts = get_short_positions(positions, symbol)
                    if not shorts:
                        accounts_with_longs = list(set(p["account"] for p in longs))
                        reason = f"EMERGENCY_SHORT_S{crash_severity}"
                        cnt = await signal_emergency_short(
                            symbol, accounts_with_longs, reason
                        )
                        interventions += cnt

                state.record_intervention(symbol)

            else:
                # Not crashing — clear consecutive counter
                state.clear_crash(symbol)

            # --- BREAKOUT HANDLING ---
            if breakout_strength >= BREAKOUT_ADD_THRESHOLD:
                log.info(f"BREAKOUT: {symbol} strength={breakout_strength} "
                        f"dc_highs_broken={dc_highs_broken}")
                # Block any pending short signals
                signals_dir = KEY_LEVELS_DIR / "signals"
                if signals_dir.exists():
                    for sig_file in signals_dir.glob(f"*_{symbol}_EMERGENCY_SHORT.json"):
                        sig_file.unlink(missing_ok=True)
                        log.info(f"  Cancelled short signal: {sig_file.name}")

        except Exception as e:
            log.error(f"Error scanning {symbol}: {e}")

    return interventions


async def run_monitor():
    """Main monitor loop."""
    log.info("=" * 60)
    log.info("KEY LEVELS MONITOR STARTED")
    log.info(f"  Scan interval: {SCAN_INTERVAL_S}s")
    log.info(f"  Crash reduce threshold: {CRASH_REDUCE_THRESHOLD} TFs")
    log.info(f"  Crash close threshold: {CRASH_CLOSE_THRESHOLD} TFs")
    log.info(f"  Protected accounts: {PROTECTED_ACCOUNTS}")
    log.info("=" * 60)

    state = MonitorState()
    symbols = load_symbols()
    log.info(f"Monitoring {len(symbols)} symbols")

    # Clean up old signals
    signals_dir = KEY_LEVELS_DIR / "signals"
    if signals_dir.exists():
        for old_sig in signals_dir.glob("*.json"):
            try:
                with open(old_sig) as f:
                    sig = json.load(f)
                ts = sig.get("timestamp", "")
                if ts:
                    sig_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - sig_time).total_seconds() > 3600:
                        old_sig.unlink(missing_ok=True)
            except Exception:
                pass

    cycle = 0
    while True:
        cycle += 1
        try:
            t0 = time.time()

            # Reload symbols periodically
            if cycle % 10 == 1:
                symbols = load_symbols()

            interventions = await scan_and_intervene(state, symbols)
            elapsed = time.time() - t0

            if interventions > 0:
                log.warning(f"Cycle {cycle}: {interventions} interventions in {elapsed:.1f}s")
            elif cycle % 30 == 0:
                log.info(f"Cycle {cycle}: all clear, {len(symbols)} symbols in {elapsed:.1f}s")

        except Exception as e:
            log.error(f"Scan cycle {cycle} failed: {e}")

        await asyncio.sleep(SCAN_INTERVAL_S)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Key Level Intervention Monitor")
    parser.add_argument("--daemon", action="store_true", help="Run as continuous monitor")
    parser.add_argument("--scan-once", action="store_true", help="Run one scan cycle and exit")
    parser.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--symbol", type=str, help="Scan single symbol")
    args = parser.parse_args()

    if args.status:
        state = MonitorState()
        print(f"\nKey Levels Monitor State")
        print(f"{'='*40}")
        print(f"Interventions today: {state.interventions_today}")
        print(f"Active crash watches: {len(state.crash_counts)}")
        for sym, count in sorted(state.crash_counts.items(), key=lambda x: -x[1]):
            print(f"  {sym}: {count} consecutive crash scans")
        print(f"Cooldowns active: {sum(1 for t in state.last_intervention.values() if time.time() - t < INTERVENTION_COOLDOWN_S)}")

        # Show pending signals
        signals_dir = KEY_LEVELS_DIR / "signals"
        if signals_dir.exists():
            sigs = list(signals_dir.glob("*.json"))
            print(f"\nPending signals: {len(sigs)}")
            for sf in sigs:
                try:
                    with open(sf) as f:
                        sig = json.load(f)
                    print(f"  {sf.name}: {sig.get('action')} {sig.get('symbol')} "
                          f"{sig.get('reason')} ({sig.get('urgency')})")
                except Exception:
                    pass

        # Show recent interventions
        if INTERVENTION_LOG.exists():
            print(f"\nRecent interventions:")
            with open(INTERVENTION_LOG) as f:
                lines = f.readlines()
            for line in lines[-10:]:
                try:
                    entry = json.loads(line)
                    print(f"  {entry['timestamp'][:19]} {entry['action']:20s} {entry['symbol']:15s} {entry.get('reason','')}")
                except Exception:
                    pass

    elif args.scan_once or args.symbol:
        async def _run():
            state = MonitorState()
            if args.symbol:
                symbols = [args.symbol]
            else:
                symbols = load_symbols()
            count = await scan_and_intervene(state, symbols)
            print(f"Scan complete: {count} interventions")
        asyncio.run(_run())

    elif args.daemon:
        asyncio.run(run_monitor())

    else:
        parser.print_help()
