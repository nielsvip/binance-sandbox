
import asyncio
import aiofiles
import os
import glob
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import statistics

# --- CONFIGURATION ---
LOG_DIR = Path.home() / "logs"
REPORT_INTERVAL = 3600       # Hourly summary
STALE_THRESHOLD = 120        # Alert if key silent > 120s
WATCHDOG_INTERVAL = 10       # Check stale keys every 10s
ACTIVE_WINDOW = 1800         # Track keys seen in last 30m

# --- PATTERNS TO MUTE (Live Feed) ---
# These are normal operations that don't need to be seen in real-time
MUTE_PATTERNS = [
    "Heartbeat", "Processing", "monitoring loop", "fetch loop",
    "updated mark price", "Saving", "Loaded", "refreshed",
    "monitor_entries", "process_position", "cycle #", "No conditions met"
]

class C:
    HEAD = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARN = '\033[93m'
    FAIL = '\033[91m'
    BOLD = '\033[1m'
    UNDER = '\033[4m'
    END = '\033[0m'
    BG_RED = '\033[41m'

# --- STATE ---
class SystemState:
    def __init__(self):
        self.last_seen = defaultdict(lambda: defaultdict(float))
        
        # Performance Tracking
        self.strategy_pnl = defaultdict(float)  # {strategy_name: pnl_sum}
        self.strategy_counts = defaultdict(lambda: {'wins': 0, 'loss': 0})
        self.symbol_pnl = defaultdict(float)
        
        # Error tracking
        self.errors = defaultdict(int)

state = SystemState()

# --- REGEX ---
# Extract key: account:SYMBOL_SIDE
REGEX_KEY = re.compile(r'\b([a-z]{3}:[A-Z0-9]+_(?:LONG|SHORT))\b')
# Extract PnL info from actions.log
REGEX_TRADE = re.compile(r"([^:]+):.*(OPEN|AUGMENT|REDUCE|CLOSE|PROFIT_TAKE).*Gain:\s*([-\d\.]+)%.*Reason:\s*(.*)")

# --- STRATEGY EXTRACTION HELPER ---
def extract_strategy_name(reason_str):
    """
    Intelligently extracts the strategy name from reason strings.
    Examples:
    - 's10_fin_longs_...' -> 'S10_FIN'
    - 'MASTER_STOP_LOSS...' -> 'MASTER_STOP'
    - 'STOCH_CROSS_ENTRY...' -> 'STOCH_CROSS'
    """
    r = reason_str.strip().upper()
    
    # Specific Strategy Codes
    if r.startswith("S") and len(r) > 2 and r[1].isdigit():
        # Matches s1, s10, s2_price_based, etc.
        parts = r.split('_')
        # Return first two parts (e.g., S10_FIN, S2_PRICE)
        return "_".join(parts[:2])
    
    # Standard Logic
    if "MASTER_STOP" in r: return "MASTER_STOP"
    if "STOP_LOSS" in r: return "STOP_LOSS"
    if "PLAN_B" in r: return "PLAN_B"
    if "HEDGE" in r: return "HEDGE_LOGIC"
    if "LEADERBOARD" in r: return "LEADERBOARD"
    if "RANKING" in r: return "RANKING"
    
    # Default: First 2 words
    parts = r.split('_')
    return "_".join(parts[:2]) if parts else "UNKNOWN"

# --- TAILING ---

async def follow_file(file_path):
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            await f.seek(0, 2)
            while True:
                line = await f.readline()
                if not line:
                    await asyncio.sleep(0.1)
                    continue
                yield line
    except Exception:
        pass

async def parser(file_path, log_type):
    filename = Path(file_path).name
    # Infer account from filename if possible
    acc_guess = "unknown"
    if "_" in filename:
        parts = filename.split('_')
        if len(parts) >= 2 and len(parts[-1]) >= 3:
             acc_guess = parts[-1].split('.')[0]

    print(f"{C.GREEN}Watching {filename}{C.END}")

    async for line in follow_file(file_path):
        now = time.time()

        # 1. Update Heartbeats (Silence check)
        # Any mention of a valid key counts as a heartbeat
        key_match = REGEX_KEY.search(line)
        if key_match:
            key = key_match.group(1)
            # Ensure account matches valid list
            acc_key = key.split(':')[0]
            if acc_key in ['ang', 'inf', 'men', 'flz', 'fin']:
                state.last_seen[acc_key][key] = now

        # 2. Parse Trades (actions.log)
        if log_type == 'action' and "Gain:" in line:
            match = REGEX_TRADE.search(line)
            if match:
                key, action, gain_str, reason = match.groups()
                try:
                    gain = float(gain_str)
                    strategy = extract_strategy_name(reason)
                    
                    # Record Stats
                    state.strategy_pnl[strategy] += gain
                    if gain > 0: state.strategy_counts[strategy]['wins'] += 1
                    else: state.strategy_counts[strategy]['loss'] += 1
                    
                    # Print Trade (Always significant)
                    color = C.GREEN if gain > 0 else C.FAIL
                    print(f"{color}[TRADE] {key} {action} | {gain:+.2f}% | Strat: {strategy}{C.END}")
                except Exception: pass

        # 3. Print Critical Errors (Immediate Alert)
        if "CRITICAL" in line or "Traceback" in line:
             if not any(ign in line for ign in ["Timestamp", "1021"]): # Ignore common noise
                print(f"{C.FAIL}{C.BOLD}[ALERT] {filename}: {line.strip()[:150]}{C.END}")

# --- ANALYSIS ENGINES ---

async def stale_watchdog():
    """Monitors for keys that have stopped updating."""
    warned_keys = set()
    
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)
        now = time.time()
        stale_count = 0
        
        for acc, keys in state.last_seen.items():
            for key, last_ts in keys.items():
                age = now - last_ts
                
                # Logic: Only warn if key was active recently (last 30m) but went silent > 2m
                if age < ACTIVE_WINDOW: 
                    if age > STALE_THRESHOLD:
                        stale_count += 1
                        if key not in warned_keys:
                            print(f"{C.BG_RED}{C.BOLD} ⚠️  STALE KEY ALERT: {key} silent for {age:.0f}s {C.END}")
                            warned_keys.add(key)
                    else:
                        # Recovery
                        if key in warned_keys:
                            print(f"{C.GREEN} ✅ RECOVERED: {key} is back active.{C.END}")
                            warned_keys.remove(key)

        if stale_count > 5 and stale_count % 10 == 0:
            print(f"{C.WARN} ... and {stale_count} other keys are stale.{C.END}")

async def hourly_report():
    """Generates the deep dive analysis."""
    while True:
        await asyncio.sleep(REPORT_INTERVAL)
        
        print(f"\n{C.HEAD}{'='*60}")
        print(f" HOURLY STRATEGY ANALYSIS - {datetime.now().strftime('%H:%M')}")
        print(f"{'='*60}{C.END}")

        # 1. Strategy Leaderboard
        print(f"\n{C.BOLD}🏆 STRATEGY PERFORMANCE (Sorted by PnL){C.END}")
        sorted_strats = sorted(state.strategy_pnl.items(), key=lambda x: x[1], reverse=True)
        
        print(f"{'STRATEGY':<30} | {'PnL':<8} | {'W/L':<10}")
        print("-" * 55)
        
        for strat, pnl in sorted_strats:
            counts = state.strategy_counts[strat]
            color = C.GREEN if pnl > 0 else C.FAIL
            print(f"{strat:<30} | {color}{pnl:>7.1f}%{C.END} | {counts['wins']}/{counts['loss']}")

        # 2. Recommendations
        print(f"\n{C.BOLD}🤖 AI RECOMMENDATIONS{C.END}")
        
        # Kill losers
        losers = [s for s, p in sorted_strats if p < -10.0]
        if losers:
            print(f"{C.FAIL}❌ DISABLE THESE STRATEGIES:{C.END}")
            for l in losers:
                print(f"   - {l} (Loss: {state.strategy_pnl[l]:.1f}%)")
        
        # Boost winners
        winners = [s for s, p in sorted_strats if p > 20.0]
        if winners:
            print(f"{C.GREEN}✅ SCALE UP THESE STRATEGIES:{C.END}")
            for w in winners:
                print(f"   - {w} (Gain: {state.strategy_pnl[w]:.1f}%)")

        # Monitoring Health
        total_tracked = sum(len(k) for k in state.last_seen.values())
        print(f"\n{C.CYAN}ℹ️  System tracking {total_tracked} active keys across {len(state.last_seen)} accounts.{C.END}")
        print(f"{C.HEAD}{'='*60}{C.END}\n")

async def stale_key_watchdog():
    """
    Runs continuously. 
    Checks if active keys have stopped reporting for > 120s.
    """
    print(f"{C.CYAN}Watchdog active: Warning on >{STALE_THRESHOLD}s silence.{C.CYAN}")
    
    # Track which keys we have already warned about to avoid console spam (one warning per "outage")
    warned_keys = set() 

    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)
        now = time.time()
        
        # Build list of stale items
        stale_items = []
        
        for account, keys in state.last_seen.items():
            for key, last_ts in keys.items():
                time_since = now - last_ts
                
                # Logic:
                # 1. Key must have been seen recently (ACTIVE_WINDOW) to be considered "active".
                #    This prevents warning about positions closed yesterday.
                # 2. Key must have exceeded STALE_THRESHOLD gap.
                
                if time_since < ACTIVE_WINDOW: # It's considered an active position
                    if time_since > STALE_THRESHOLD:
                        stale_items.append((account, key, time_since))
                        
                        if key not in warned_keys:
                            print(f"{C.BG_RED}{C.BOLD} [ALERT] {key} SKIPPED MONITORING! Last seen {time_since:.0f}s ago {C.CYAN}")
                            warned_keys.add(key)
                    else:
                        # It's fresh, remove from warned set so we can warn again if it stalls later
                        if key in warned_keys:
                            warned_keys.remove(key)
        
        # Optional: Print a summary line if multiple keys are stale
        if len(stale_items) > 3:
            print(f"{C.WARN}⚠️  {len(stale_items)} keys are currently stale (>120s). Check processes.{C.CYAN}")

# --- MAIN ---

async def main():
    tasks = []
    
    # 1. Actions Log (PnL Source)
    if (LOG_DIR / "actions.log").exists():
        tasks.append(parser(LOG_DIR / "actions.log", 'action'))
        
    # 2. Monitor Logs
    accounts = ['ang', 'inf', 'men', 'flz', 'fin']
    for acc in accounts:
        # Check both manage and quick logs
        for prefix in ["ez_manage", "ez_positions_quick"]:
            log_path = LOG_DIR / f"{prefix}_{acc}.log"
            if log_path.exists():
                tasks.append(parser(log_path, 'manage'))

    # 3. Service Log
    svc = LOG_DIR / "ez_positions_service.log"
    if svc.exists(): tasks.append(parser(svc, 'manage'))

    # 4. Engines
    tasks.append(stale_key_watchdog())
    tasks.append(hourly_report())

    print(f"{C.BOLD}Deep Analysis Running... (Ctrl+C to stop){C.END}")
    
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
