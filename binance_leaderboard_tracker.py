#!/usr/bin/env python3
"""Monitor Binance Futures leaderboard traders' positions in real-time, building a trade history database."""
import argparse
import csv
import json
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = BASE_PATH / "data" / "binance_leaderboard"
TRADERS_FILE = DATA_DIR / "traders.json"
TRACKED_FILE = DATA_DIR / "tracked_traders.json"
TRADE_LOG_FILE = DATA_DIR / "trade_log.jsonl"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
EXPORT_CSV = DATA_DIR / "completed_trades.csv"
STATE_FILE = DATA_DIR / "tracker_state.json"
MAX_TRACKED = 50
POLL_INTERVAL = 60

logger = logging.getLogger("leaderboard_tracker")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
    fh = logging.FileHandler(Path.home() / "logs" / "binance_leaderboard_tracker.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

RANK_URL = "https://www.binance.com/bapi/futures/v1/public/future/leaderboard/getLeaderboardRank"
POSITION_URL = "https://www.binance.com/bapi/futures/v2/public/future/leaderboard/getOtherPosition"

_shutdown = False


def _set_poll_interval(val: int):
    global POLL_INTERVAL
    POLL_INTERVAL = val


def _signal_handler(sig, frame):
    global _shutdown
    _shutdown = True
    logger.info("Shutdown signal received, finishing current cycle...")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = Path.home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)


def _create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Origin": "https://www.binance.com", "Referer": "https://www.binance.com/en/futures-activity/leaderboard"})
    return s


def _rotate_ua(session: requests.Session):
    session.headers["User-Agent"] = random.choice(USER_AGENTS)


def _random_delay():
    time.sleep(random.uniform(1.0, 3.0))


def _load_json(path: Path) -> Any:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_json(path: Path, data: Any):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.rename(path)


def _append_jsonl(path: Path, record: dict):
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ts() -> float:
    return time.time()


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — DISCOVER TOP TRADERS
# ═══════════════════════════════════════════════════════════════════

def discover_traders(session: requests.Session) -> List[dict]:
    """Fetch top traders from leaderboard across multiple period types and stat types."""
    all_traders: Dict[str, dict] = {}
    backoff = 1.0
    for period in ["DAILY", "WEEKLY", "MONTHLY", "ALL"]:
        for stat_type in ["ROI", "PNL"]:
            _rotate_ua(session)
            _random_delay()
            payload = {"isShared": True, "isTrader": False, "periodType": period, "statisticsType": stat_type, "tradeType": "PERPETUAL"}
            try:
                resp = session.post(RANK_URL, json=payload, timeout=15)
                if resp.status_code in (429, 403):
                    backoff = min(backoff * 2, 300)
                    logger.warning(f"Rate limited on {period}/{stat_type}, backing off {backoff:.0f}s")
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                data = resp.json()
                backoff = 1.0
            except Exception as e:
                logger.error(f"Failed to fetch {period}/{stat_type}: {e}")
                continue
            ranks = data.get("data", [])
            if not ranks:
                logger.info(f"No data for {period}/{stat_type}")
                continue
            for r in ranks[:100]:
                uid = r.get("encryptedUid", "")
                if not uid:
                    continue
                if uid not in all_traders:
                    all_traders[uid] = {"encrypted_uid": uid, "nickname": r.get("nickName", "unknown"), "roi_pct": {}, "pnl_usd": {}, "discovered_at": _now_iso()}
                if stat_type == "ROI":
                    all_traders[uid]["roi_pct"][period] = r.get("roi", 0)
                else:
                    all_traders[uid]["pnl_usd"][period] = r.get("pnl", 0)
    result = list(all_traders.values())
    logger.info(f"Discovered {len(result)} unique traders across all periods")
    _save_json(TRADERS_FILE, result)
    return result


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — POSITION FETCHING
# ═══════════════════════════════════════════════════════════════════

def fetch_positions(session: requests.Session, uid: str) -> Optional[List[dict]]:
    """Fetch a trader's current open positions. Returns None on error, [] if hidden/empty."""
    _rotate_ua(session)
    payload = {"encryptedUid": uid, "tradeType": "PERPETUAL"}
    try:
        resp = session.post(POSITION_URL, json=payload, timeout=15)
        if resp.status_code in (429, 403):
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug(f"Error fetching positions for {uid}: {e}")
        return None
    positions_raw = data.get("data", {})
    if positions_raw is None:
        return []
    if isinstance(positions_raw, dict):
        other_pos = positions_raw.get("otherPositionRetList", [])
    elif isinstance(positions_raw, list):
        other_pos = positions_raw
    else:
        return []
    if other_pos is None:
        return []
    result = []
    for p in other_pos:
        amount = float(p.get("amount", 0))
        side = "LONG" if amount > 0 else "SHORT"
        result.append({"symbol": p.get("symbol", ""), "side": side, "entry_price": float(p.get("entryPrice", 0)), "mark_price": float(p.get("markPrice", 0)), "pnl": float(p.get("pnl", 0)), "roe": float(p.get("roe", 0)), "leverage": int(p.get("leverage", 1)), "amount": abs(amount), "notional_usd": abs(amount) * float(p.get("markPrice", 0)), "update_ts": p.get("updateTimeStamp", 0)})
    return result


def _position_key(pos: dict) -> str:
    return f"{pos['symbol']}_{pos['side']}"


# ═══════════════════════════════════════════════════════════════════
# SECTION 3 — MONITORING LOOP
# ═══════════════════════════════════════════════════════════════════

def _load_tracked() -> List[dict]:
    data = _load_json(TRACKED_FILE)
    if isinstance(data, list):
        return data[:MAX_TRACKED]
    return []


def _save_tracked(traders: List[dict]):
    _save_json(TRACKED_FILE, traders[:MAX_TRACKED])


def _load_state() -> dict:
    data = _load_json(STATE_FILE)
    if isinstance(data, dict):
        return data
    return {"started_at": _now_iso(), "cycles": 0, "total_entries": 0, "total_exits": 0, "errors": 0}


def _save_state(state: dict):
    _save_json(STATE_FILE, state)


def _load_previous_snapshot(uid: str) -> Dict[str, dict]:
    snap_file = SNAPSHOTS_DIR / f"{uid}.json"
    data = _load_json(snap_file)
    if isinstance(data, dict):
        return data
    return {}


def _save_snapshot(uid: str, positions: Dict[str, dict]):
    snap_file = SNAPSHOTS_DIR / f"{uid}.json"
    _save_json(snap_file, positions)


def _detect_changes(uid: str, nickname: str, prev: Dict[str, dict], current: List[dict], state: dict):
    """Compare previous and current positions, log entries and exits."""
    curr_map: Dict[str, dict] = {}
    for p in current:
        key = _position_key(p)
        curr_map[key] = p
    prev_keys: Set[str] = set(prev.keys())
    curr_keys: Set[str] = set(curr_map.keys())
    new_keys = curr_keys - prev_keys
    closed_keys = prev_keys - curr_keys
    for key in new_keys:
        p = curr_map[key]
        entry_record = {"event": "ENTRY", "trader_id": uid, "nickname": nickname, "symbol": p["symbol"], "side": p["side"], "entry_price": p["entry_price"], "leverage": p["leverage"], "amount": p["amount"], "notional_usd": p["notional_usd"], "timestamp": _now_iso()}
        _append_jsonl(TRADE_LOG_FILE, entry_record)
        state["total_entries"] = state.get("total_entries", 0) + 1
        logger.info(f"ENTRY: {nickname} opened {p['side']} {p['symbol']} @ {p['entry_price']:.4f} ({p['leverage']}x, ${p['notional_usd']:.0f})")
    for key in closed_keys:
        p = prev[key]
        pnl = p.get("pnl", 0)
        entry_price = p.get("entry_price", 0)
        mark_price = p.get("mark_price", entry_price)
        pnl_pct = 0.0
        if entry_price > 0:
            if p["side"] == "LONG":
                pnl_pct = ((mark_price - entry_price) / entry_price) * 100.0
            else:
                pnl_pct = ((entry_price - mark_price) / entry_price) * 100.0
        entry_ts_str = p.get("first_seen", _now_iso())
        try:
            entry_dt = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00"))
            hold_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600.0
        except Exception:
            hold_hours = 0.0
        exit_record = {"event": "EXIT", "trader_id": uid, "nickname": nickname, "symbol": p["symbol"], "side": p["side"], "entry_price": entry_price, "exit_price": mark_price, "pnl": pnl, "pnl_pct": round(pnl_pct, 2), "leverage": p.get("leverage", 1), "hold_hours": round(hold_hours, 2), "timestamp": _now_iso()}
        _append_jsonl(TRADE_LOG_FILE, exit_record)
        state["total_exits"] = state.get("total_exits", 0) + 1
        logger.info(f"EXIT: {nickname} closed {p['side']} {p['symbol']} PnL: {pnl_pct:+.2f}% (held {hold_hours:.1f}h)")
    return curr_map


def run_monitor():
    """Main monitoring loop — polls tracked traders every POLL_INTERVAL seconds."""
    _ensure_dirs()
    tracked = _load_tracked()
    if not tracked:
        logger.error("No tracked traders. Run --discover first, then --add UIDs to track.")
        return
    state = _load_state()
    session = _create_session()
    backoff = 30.0
    logger.info(f"Starting monitor for {len(tracked)} traders (poll every {POLL_INTERVAL}s)")
    while not _shutdown:
        cycle_start = time.time()
        state["cycles"] = state.get("cycles", 0) + 1
        errors_this_cycle = 0
        for trader in tracked:
            if _shutdown:
                break
            uid = trader["encrypted_uid"]
            nickname = trader.get("nickname", uid[:8])
            _random_delay()
            positions = fetch_positions(session, uid)
            if positions is None:
                errors_this_cycle += 1
                state["errors"] = state.get("errors", 0) + 1
                if errors_this_cycle >= 3:
                    logger.warning(f"Too many errors this cycle, backing off {backoff:.0f}s")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 300)
                continue
            backoff = 30.0
            prev = _load_previous_snapshot(uid)
            curr_map = {}
            for p in positions:
                key = _position_key(p)
                p["first_seen"] = prev.get(key, {}).get("first_seen", _now_iso())
                curr_map[key] = p
            _detect_changes(uid, nickname, prev, positions, state)
            _save_snapshot(uid, curr_map)
        state["last_cycle"] = _now_iso()
        _save_state(state)
        elapsed = time.time() - cycle_start
        sleep_time = max(1.0, POLL_INTERVAL - elapsed)
        logger.info(f"Cycle {state['cycles']} done in {elapsed:.1f}s, sleeping {sleep_time:.0f}s (entries: {state.get('total_entries', 0)}, exits: {state.get('total_exits', 0)})")
        wait_start = time.time()
        while not _shutdown and (time.time() - wait_start) < sleep_time:
            time.sleep(1.0)
    _save_state(state)
    logger.info("Monitor stopped cleanly.")


# ═══════════════════════════════════════════════════════════════════
# SECTION 4 — CLI COMMANDS
# ═══════════════════════════════════════════════════════════════════

def cmd_discover():
    _ensure_dirs()
    session = _create_session()
    traders = discover_traders(session)
    if traders:
        print(f"\nDiscovered {len(traders)} traders, saved to {TRADERS_FILE}")
        by_monthly_roi = sorted(traders, key=lambda t: t.get("roi_pct", {}).get("MONTHLY", 0), reverse=True)
        print("\nTop 10 by Monthly ROI:")
        for i, t in enumerate(by_monthly_roi[:10], 1):
            roi = t.get("roi_pct", {}).get("MONTHLY", 0)
            pnl = t.get("pnl_usd", {}).get("MONTHLY", 0)
            print(f"  {i:2d}. {t['nickname']:<20s} ROI: {roi:>8.2f}%  PnL: ${pnl:>12,.2f}  UID: {t['encrypted_uid'][:16]}...")


def cmd_add(uid_str: str):
    _ensure_dirs()
    uids = [u.strip() for u in uid_str.split(",") if u.strip()]
    tracked = _load_tracked()
    existing_uids = {t["encrypted_uid"] for t in tracked}
    all_traders = _load_json(TRADERS_FILE) or []
    trader_lookup = {t["encrypted_uid"]: t for t in all_traders}
    added = 0
    for uid in uids:
        if uid in existing_uids:
            print(f"  Already tracking: {uid[:16]}...")
            continue
        if len(tracked) >= MAX_TRACKED:
            print(f"  Max {MAX_TRACKED} traders reached. Remove some first.")
            break
        info = trader_lookup.get(uid, {})
        tracked.append({"encrypted_uid": uid, "nickname": info.get("nickname", uid[:12]), "added_at": _now_iso()})
        existing_uids.add(uid)
        added += 1
        print(f"  Added: {info.get('nickname', uid[:16])} ({uid[:16]}...)")
    _save_tracked(tracked)
    print(f"\nAdded {added} traders. Now tracking {len(tracked)}/{MAX_TRACKED}.")


def cmd_remove(uid_str: str):
    _ensure_dirs()
    uids_to_remove = {u.strip() for u in uid_str.split(",") if u.strip()}
    tracked = _load_tracked()
    before = len(tracked)
    tracked = [t for t in tracked if t["encrypted_uid"] not in uids_to_remove]
    _save_tracked(tracked)
    removed = before - len(tracked)
    print(f"Removed {removed} traders. Now tracking {len(tracked)}/{MAX_TRACKED}.")


def cmd_snapshot():
    _ensure_dirs()
    tracked = _load_tracked()
    if not tracked:
        print("No tracked traders. Run --discover then --add.")
        return
    session = _create_session()
    print(f"\n{'='*90}")
    print(f"  BINANCE LEADERBOARD — CURRENT POSITIONS ({_now_iso()})")
    print(f"{'='*90}")
    for trader in tracked:
        uid = trader["encrypted_uid"]
        nickname = trader.get("nickname", uid[:12])
        _random_delay()
        positions = fetch_positions(session, uid)
        if positions is None:
            print(f"\n  {nickname}: [error fetching]")
            continue
        if not positions:
            print(f"\n  {nickname}: [no positions / hidden]")
            continue
        print(f"\n  {nickname} ({uid[:12]}...):")
        for p in sorted(positions, key=lambda x: abs(x["notional_usd"]), reverse=True):
            pnl_str = f"{'+'if p['pnl']>=0 else ''}{p['pnl']:.2f}"
            roe_str = f"{'+'if p['roe']>=0 else ''}{p['roe']*100:.2f}%"
            print(f"    {p['side']:5s} {p['symbol']:<16s} entry:{p['entry_price']:<12.4f} mark:{p['mark_price']:<12.4f} {p['leverage']:>3d}x  ${p['notional_usd']:>10,.0f}  PnL:{pnl_str:>12s}  ROE:{roe_str:>8s}")
    print(f"\n{'='*90}")


def cmd_stats():
    _ensure_dirs()
    tracked = _load_tracked()
    state = _load_state()
    log_lines = 0
    entries = 0
    exits = 0
    unique_traders_in_log: Set[str] = set()
    unique_symbols: Set[str] = set()
    if TRADE_LOG_FILE.exists():
        with open(TRADE_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                log_lines += 1
                try:
                    rec = json.loads(line)
                    if rec.get("event") == "ENTRY":
                        entries += 1
                    elif rec.get("event") == "EXIT":
                        exits += 1
                    unique_traders_in_log.add(rec.get("trader_id", ""))
                    unique_symbols.add(rec.get("symbol", ""))
                except Exception:
                    continue
    print(f"\n{'='*60}")
    print(f"  BINANCE LEADERBOARD TRACKER — STATISTICS")
    print(f"{'='*60}")
    print(f"  Tracked traders:       {len(tracked)}/{MAX_TRACKED}")
    print(f"  Monitor started:       {state.get('started_at', 'never')}")
    print(f"  Last cycle:            {state.get('last_cycle', 'never')}")
    print(f"  Total cycles:          {state.get('cycles', 0)}")
    print(f"  Total errors:          {state.get('errors', 0)}")
    print(f"  ---")
    print(f"  Trade log events:      {log_lines}")
    print(f"  Entries logged:        {entries}")
    print(f"  Exits logged:          {exits}")
    print(f"  Unique traders in log: {len(unique_traders_in_log)}")
    print(f"  Unique symbols traded: {len(unique_symbols)}")
    print(f"  Log file:              {TRADE_LOG_FILE}")
    print(f"{'='*60}")


def cmd_export() -> Optional[Path]:
    """Export completed trades (matched ENTRY+EXIT) to CSV compatible with trader_deep_analyzer.py."""
    _ensure_dirs()
    if not TRADE_LOG_FILE.exists():
        print("No trade log found. Start monitoring first.")
        return None
    open_trades: Dict[str, dict] = {}
    completed: List[dict] = []
    with open(TRADE_LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            event = rec.get("event", "")
            key = f"{rec.get('trader_id', '')}:{rec.get('symbol', '')}:{rec.get('side', '')}"
            if event == "ENTRY":
                open_trades[key] = rec
            elif event == "EXIT":
                entry_rec = open_trades.pop(key, None)
                entry_ts = entry_rec["timestamp"] if entry_rec else rec.get("timestamp", "")
                completed.append({"trader_id": rec.get("trader_id", ""), "nickname": rec.get("nickname", ""), "symbol": rec.get("symbol", ""), "side": rec.get("side", ""), "entry_price": entry_rec.get("entry_price", rec.get("entry_price", 0)) if entry_rec else rec.get("entry_price", 0), "exit_price": rec.get("exit_price", 0), "entry_time": entry_ts, "exit_time": rec.get("timestamp", ""), "pnl": rec.get("pnl", 0), "pnl_pct": rec.get("pnl_pct", 0), "leverage": rec.get("leverage", 1), "position_size_usd": entry_rec.get("notional_usd", 0) if entry_rec else 0, "hold_hours": rec.get("hold_hours", 0)})
    if not completed:
        print("No completed trades yet (need both ENTRY and EXIT).")
        return None
    fieldnames = ["trader_id", "nickname", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd", "hold_hours"]
    with open(EXPORT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(completed)
    print(f"Exported {len(completed)} completed trades to {EXPORT_CSV}")
    return EXPORT_CSV


def cmd_analyze():
    """Export to CSV then run trader_deep_analyzer.py on it."""
    csv_path = cmd_export()
    if csv_path is None:
        return
    analyzer_path = BASE_PATH / "trader_deep_analyzer.py"
    if not analyzer_path.exists():
        print(f"trader_deep_analyzer.py not found at {analyzer_path}")
        return
    import subprocess
    python = sys.executable
    print(f"\nRunning trader_deep_analyzer.py on {csv_path} ...")
    result = subprocess.run([python, str(analyzer_path), "--csv", str(csv_path)], capture_output=False)
    if result.returncode != 0:
        print(f"Analyzer exited with code {result.returncode}")


def cmd_list():
    """List currently tracked traders."""
    tracked = _load_tracked()
    if not tracked:
        print("No tracked traders. Run --discover then --add.")
        return
    print(f"\nTracked traders ({len(tracked)}/{MAX_TRACKED}):")
    for i, t in enumerate(tracked, 1):
        print(f"  {i:2d}. {t.get('nickname', 'unknown'):<20s}  UID: {t['encrypted_uid'][:20]}...  Added: {t.get('added_at', '?')}")


def main():
    parser = argparse.ArgumentParser(description="Binance Futures Leaderboard Position Tracker")
    parser.add_argument("--discover", action="store_true", help="Discover top traders from leaderboard")
    parser.add_argument("--add", type=str, metavar="UIDs", help="Add trader UIDs to track (comma-separated)")
    parser.add_argument("--remove", type=str, metavar="UIDs", help="Remove trader UIDs from tracking (comma-separated)")
    parser.add_argument("--list", action="store_true", help="List tracked traders")
    parser.add_argument("--snapshot", action="store_true", help="Show current positions of all tracked traders")
    parser.add_argument("--export", action="store_true", help="Export completed trades to CSV")
    parser.add_argument("--stats", action="store_true", help="Show tracker statistics")
    parser.add_argument("--analyze", action="store_true", help="Export + run trader_deep_analyzer.py")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL, help=f"Poll interval in seconds (default: {POLL_INTERVAL})")
    args = parser.parse_args()
    _ensure_dirs()
    if args.discover:
        cmd_discover()
    elif args.add:
        cmd_add(args.add)
    elif args.remove:
        cmd_remove(args.remove)
    elif args.list:
        cmd_list()
    elif args.snapshot:
        cmd_snapshot()
    elif args.export:
        cmd_export()
    elif args.stats:
        cmd_stats()
    elif args.analyze:
        cmd_analyze()
    else:
        if args.interval != POLL_INTERVAL:
            _set_poll_interval(args.interval)
        run_monitor()


if __name__ == "__main__":
    main()
