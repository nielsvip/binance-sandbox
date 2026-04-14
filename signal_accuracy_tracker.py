#!/usr/bin/env python3
"""
Signal Accuracy Tracker
=======================
Tracks manipulation_history, news_sentiment_crypto, and news_sentiment_stocks
signals against actual price deltas at 15m, 1h, 4h, 8h, 1D, 2D, 4D.

Usage:
  python signal_accuracy_tracker.py snapshot   # Record current signals + prices
  python signal_accuracy_tracker.py resolve    # Fill in price deltas for past snapshots
  python signal_accuracy_tracker.py report     # Print accuracy report
  python signal_accuracy_tracker.py daemon     # Run snapshot every 5m + resolve every 15m
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import aiohttp

try:
    import config
    BASE_PATH = config.BASE_PATH
except Exception:
    BASE_PATH = Path(__file__).parent

DATA_DIR = Path(BASE_PATH) / "data"
TRACK_DIR = DATA_DIR / "signal_tracker"
TRACK_DIR.mkdir(parents=True, exist_ok=True)
SIGNALS_FILE = TRACK_DIR / "signals.jsonl"
REPORT_FILE = TRACK_DIR / "accuracy_report.json"
LAST_MANIP_TS_FILE = TRACK_DIR / ".last_manip_ts"
LAST_NEWS_SNAP_FILE = TRACK_DIR / ".last_news_snap_ts"
PROGRESS_FILE = TRACK_DIR / "progress.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIGTRACK] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("signal_tracker")

RESOLVE_WINDOWS = {
    "15m": 15 * 60,
    "1h": 3600,
    "4h": 4 * 3600,
    "8h": 8 * 3600,
    "1D": 24 * 3600,
    "2D": 2 * 24 * 3600,
    "4D": 4 * 24 * 3600,
}
MAX_WINDOW_SECS = 4 * 24 * 3600 + 3600  # 4D + 1h buffer

BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
SNAPSHOT_INTERVAL = 300  # 5 minutes
RESOLVE_INTERVAL = 900   # 15 minutes


def load_signals():
    """Load all signals from JSONL."""
    signals = []
    if not SIGNALS_FILE.exists():
        return signals
    with open(SIGNALS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                signals.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return signals


def append_signal(sig):
    """Append a signal to the JSONL file."""
    with open(SIGNALS_FILE, "a") as f:
        f.write(json.dumps(sig, default=str) + "\n")


def save_signals(signals):
    """Rewrite all signals (used after resolve updates)."""
    tmp = SIGNALS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for sig in signals:
            f.write(json.dumps(sig, default=str) + "\n")
    os.replace(tmp, SIGNALS_FILE)


def save_progress(signals):
    """Save progress summary to JSON so it survives restarts."""
    now = datetime.now(timezone.utc)
    total = len(signals)
    by_source = {}
    for s in signals:
        src = s.get("source", "unknown")
        if src not in by_source:
            by_source[src] = {"total": 0, "resolved_any": 0, "fully_resolved": 0, "windows": {}}
        by_source[src]["total"] += 1
        deltas = s.get("deltas", {})
        if deltas:
            by_source[src]["resolved_any"] += 1
        if len(deltas) >= len(RESOLVE_WINDOWS):
            by_source[src]["fully_resolved"] += 1
        for w in RESOLVE_WINDOWS:
            if w not in by_source[src]["windows"]:
                by_source[src]["windows"][w] = {"resolved": 0, "pending": 0}
            if w in deltas:
                by_source[src]["windows"][w]["resolved"] += 1
            else:
                by_source[src]["windows"][w]["pending"] += 1
    progress = {"updated_at": now.isoformat(), "total_signals": total, "by_source": by_source}
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save progress: {e}")


def _read_last_ts(path):
    try:
        return float(path.read_text().strip())
    except Exception:
        return 0.0


def _write_last_ts(path, ts):
    path.write_text(str(ts))


async def fetch_price_at(session, symbol, target_ts_ms):
    """Fetch the close price at a specific timestamp using Binance klines."""
    params = {"symbol": symbol, "interval": "1m", "startTime": int(target_ts_ms), "limit": 1}
    try:
        async with session.get(BINANCE_KLINES_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and len(data) > 0:
                    return float(data[0][4])  # close price
    except Exception:
        pass
    return None


async def fetch_current_price(session, symbol):
    """Fetch current price from Binance."""
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    try:
        async with session.get(url, params={"symbol": symbol}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data.get("price", 0))
    except Exception:
        pass
    return None


async def fetch_stock_price(session, symbol):
    """Fetch current stock price from Tradier."""
    try:
        env_file = Path(BASE_PATH) / ".env.tradier"
        if not env_file.exists():
            import subprocess
            result = subprocess.run(["gpg", "-d", str(Path(BASE_PATH) / ".env.gpg")], capture_output=True, text=True, timeout=10)
            lines = result.stdout.split("\n")
            token = None
            for line in lines:
                if "TRADIER_TOKEN" in line and "=" in line:
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
            if not token:
                return None
        else:
            with open(env_file) as f:
                for line in f:
                    if "TRADIER_TOKEN" in line and "=" in line:
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = f"https://api.tradier.com/v1/markets/quotes?symbols={symbol}"
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                quotes = data.get("quotes", {}).get("quote", {})
                if isinstance(quotes, list):
                    quotes = quotes[0] if quotes else {}
                return float(quotes.get("last", 0))
    except Exception:
        pass
    return None


def snapshot_manipulation():
    """Read manipulation_history.json and return new events since last snapshot."""
    hist_file = DATA_DIR / "manipulation_history.json"
    if not hist_file.exists():
        return []
    try:
        with open(hist_file) as f:
            history = json.load(f)
    except Exception:
        return []
    last_ts = _read_last_ts(LAST_MANIP_TS_FILE)
    new_events = []
    max_ts = last_ts
    for event in history:
        try:
            ts_str = event.get("timestamp", "")
            ts_dt = datetime.fromisoformat(ts_str)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            ts_epoch = ts_dt.timestamp()
            if ts_epoch > last_ts:
                new_events.append({
                    "source": "manipulation",
                    "symbol": event["symbol"],
                    "signal_ts": ts_str,
                    "signal_ts_epoch": ts_epoch,
                    "price_at_signal": event.get("price", 0),
                    "severity": event.get("severity", 0),
                    "reasons": event.get("reasons", []),
                    "forecast": "AVOID",  # manipulation = expect bad price action
                    "deltas": {},
                })
                max_ts = max(max_ts, ts_epoch)
        except Exception:
            continue
    if max_ts > last_ts:
        _write_last_ts(LAST_MANIP_TS_FILE, max_ts)
    return new_events


def snapshot_news_sentiment():
    """Read news_sentiment_crypto.json and news_sentiment_stocks.json, create signals."""
    signals = []
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_epoch = now.timestamp()
    # Dedupe: only snapshot every 30 min
    last_snap = _read_last_ts(LAST_NEWS_SNAP_FILE)
    if now_epoch - last_snap < 1800:
        return []
    for fname, source_name, is_stock in [
        ("news_sentiment_crypto.json", "news_crypto", False),
        ("news_sentiment_stocks.json", "news_stocks", True),
    ]:
        fpath = DATA_DIR / fname
        if not fpath.exists():
            continue
        try:
            with open(fpath) as f:
                scores = json.load(f)
        except Exception:
            continue
        if not isinstance(scores, dict):
            continue
        for symbol, score in scores.items():
            try:
                score = float(score)
            except (ValueError, TypeError):
                continue
            if abs(score) < 0.05:
                continue  # skip near-zero scores
            forecast = "BULLISH" if score > 0 else "BEARISH"
            signals.append({
                "source": source_name,
                "symbol": symbol,
                "signal_ts": now_iso,
                "signal_ts_epoch": now_epoch,
                "price_at_signal": 0,  # filled during resolve
                "score": round(score, 4),
                "forecast": forecast,
                "is_stock": is_stock,
                "deltas": {},
            })
    if signals:
        _write_last_ts(LAST_NEWS_SNAP_FILE, now_epoch)
    return signals


async def do_snapshot():
    """Take a snapshot of all signal sources."""
    manip_signals = snapshot_manipulation()
    news_signals = snapshot_news_sentiment()
    all_new = manip_signals + news_signals
    if not all_new:
        logger.info("No new signals to snapshot")
        return 0
    # Fetch current prices for news signals that need it
    need_price = [s for s in all_new if s["price_at_signal"] == 0]
    if need_price:
        async with aiohttp.ClientSession() as session:
            for sig in need_price:
                sym = sig["symbol"]
                if sig.get("is_stock"):
                    price = await fetch_stock_price(session, sym)
                else:
                    price = await fetch_current_price(session, sym)
                if price and price > 0:
                    sig["price_at_signal"] = price
                else:
                    sig["price_at_signal"] = 0
    # Filter out signals with no price
    valid = [s for s in all_new if s["price_at_signal"] > 0]
    # Dedupe against existing signals
    existing = load_signals()
    existing_keys = {f"{s['source']}_{s['symbol']}_{s['signal_ts']}" for s in existing}
    valid = [s for s in valid if f"{s['source']}_{s['symbol']}_{s['signal_ts']}" not in existing_keys]
    for sig in valid:
        append_signal(sig)
    logger.info(f"Snapshot: {len(manip_signals)} manipulation + {len(news_signals)} news = {len(valid)} valid signals saved ({len(all_new) - len(valid)} skipped, no price)")
    save_progress(load_signals())
    return len(valid)


async def do_resolve():
    """Fill in price deltas for past signals at each target window."""
    signals = load_signals()
    if not signals:
        logger.info("No signals to resolve")
        return
    now = time.time()
    updated = 0
    async with aiohttp.ClientSession() as session:
        for sig in signals:
            sig_ts = sig.get("signal_ts_epoch", 0)
            if sig_ts == 0:
                continue
            entry_price = sig.get("price_at_signal", 0)
            if entry_price <= 0:
                continue
            elapsed = now - sig_ts
            if elapsed > MAX_WINDOW_SECS + 7200:
                continue  # too old, skip
            deltas = sig.get("deltas", {})
            for window_name, window_secs in RESOLVE_WINDOWS.items():
                if window_name in deltas:
                    continue  # already resolved
                if elapsed < window_secs:
                    continue  # not enough time passed yet
                target_ts_ms = int((sig_ts + window_secs) * 1000)
                sym = sig["symbol"]
                is_stock = sig.get("is_stock", False)
                if is_stock:
                    # For stocks, we can only resolve if market was open — use current price as approximation for now
                    # TODO: proper historical stock price lookup
                    continue
                price = await fetch_price_at(session, sym, target_ts_ms)
                if price and price > 0:
                    delta_pct = round((price - entry_price) / entry_price * 100, 4)
                    if abs(delta_pct) > 50:
                        continue  # Skip data errors (delisted/denomination changes)
                    deltas[window_name] = {"price": price, "delta_pct": delta_pct, "resolved_at": datetime.now(timezone.utc).isoformat()}
                    updated += 1
                await asyncio.sleep(0.05)  # rate limit
            sig["deltas"] = deltas
    if updated > 0:
        save_signals(signals)
        logger.info(f"Resolved {updated} price deltas across {len(signals)} signals")
    else:
        logger.info(f"No new deltas to resolve ({len(signals)} signals checked)")
    save_progress(signals)


def do_report():
    """Generate and print accuracy report."""
    signals = load_signals()
    if not signals:
        print("No signals tracked yet. Run 'snapshot' first, then 'resolve' after time has passed.")
        return
    report = {}
    for source in ["manipulation", "news_crypto", "news_stocks"]:
        source_signals = [s for s in signals if s["source"] == source]
        if not source_signals:
            continue
        source_report = {"total_signals": len(source_signals), "windows": {}}
        for window_name in RESOLVE_WINDOWS:
            resolved = [s for s in source_signals if window_name in s.get("deltas", {})]
            if not resolved:
                source_report["windows"][window_name] = {"resolved": 0, "pending": len(source_signals)}
                continue
            deltas = [s["deltas"][window_name]["delta_pct"] for s in resolved]
            avg_delta = sum(deltas) / len(deltas)
            # Directional accuracy
            if source == "manipulation":
                # Manipulation = AVOID. If price dropped, the flag was "correct" (avoided a dump)
                correct = sum(1 for d in deltas if d < 0)
                direction = "price_dropped_after_flag"
            else:
                # News sentiment: BULLISH score should predict price up, BEARISH should predict price down
                correct = 0
                for s in resolved:
                    d = s["deltas"][window_name]["delta_pct"]
                    if s["forecast"] == "BULLISH" and d > 0:
                        correct += 1
                    elif s["forecast"] == "BEARISH" and d < 0:
                        correct += 1
                direction = "forecast_matched_direction"
            accuracy = round(correct / len(resolved) * 100, 1) if resolved else 0
            # Severity/score correlation for manipulation
            extra = {}
            if source == "manipulation":
                sev2 = [s["deltas"][window_name]["delta_pct"] for s in resolved if s.get("severity", 0) == 2]
                sev3 = [s["deltas"][window_name]["delta_pct"] for s in resolved if s.get("severity", 0) >= 3]
                if sev2:
                    extra["avg_delta_sev2"] = round(sum(sev2) / len(sev2), 4)
                if sev3:
                    extra["avg_delta_sev3"] = round(sum(sev3) / len(sev3), 4)
            if source.startswith("news"):
                bullish = [s for s in resolved if s["forecast"] == "BULLISH"]
                bearish = [s for s in resolved if s["forecast"] == "BEARISH"]
                if bullish:
                    bull_deltas = [s["deltas"][window_name]["delta_pct"] for s in bullish]
                    extra["bullish_count"] = len(bullish)
                    extra["bullish_avg_delta"] = round(sum(bull_deltas) / len(bull_deltas), 4)
                    extra["bullish_accuracy"] = round(sum(1 for d in bull_deltas if d > 0) / len(bull_deltas) * 100, 1)
                if bearish:
                    bear_deltas = [s["deltas"][window_name]["delta_pct"] for s in bearish]
                    extra["bearish_count"] = len(bearish)
                    extra["bearish_avg_delta"] = round(sum(bear_deltas) / len(bear_deltas), 4)
                    extra["bearish_accuracy"] = round(sum(1 for d in bear_deltas if d < 0) / len(bear_deltas) * 100, 1)
            # High conviction vs low conviction
            if source.startswith("news"):
                high_conv = [s for s in resolved if abs(s.get("score", 0)) >= 0.3]
                low_conv = [s for s in resolved if abs(s.get("score", 0)) < 0.3]
                if high_conv:
                    hc_correct = sum(1 for s in high_conv if (s["forecast"] == "BULLISH" and s["deltas"][window_name]["delta_pct"] > 0) or (s["forecast"] == "BEARISH" and s["deltas"][window_name]["delta_pct"] < 0))
                    extra["high_conviction_count"] = len(high_conv)
                    extra["high_conviction_accuracy"] = round(hc_correct / len(high_conv) * 100, 1)
                if low_conv:
                    lc_correct = sum(1 for s in low_conv if (s["forecast"] == "BULLISH" and s["deltas"][window_name]["delta_pct"] > 0) or (s["forecast"] == "BEARISH" and s["deltas"][window_name]["delta_pct"] < 0))
                    extra["low_conviction_count"] = len(low_conv)
                    extra["low_conviction_accuracy"] = round(lc_correct / len(low_conv) * 100, 1)
            source_report["windows"][window_name] = {
                "resolved": len(resolved),
                "pending": len(source_signals) - len(resolved),
                "avg_delta_pct": round(avg_delta, 4),
                "median_delta_pct": round(sorted(deltas)[len(deltas) // 2], 4),
                "accuracy_pct": accuracy,
                "direction_metric": direction,
                "min_delta": round(min(deltas), 4),
                "max_delta": round(max(deltas), 4),
                **extra,
            }
        report[source] = source_report
    # Save report
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)
    # Pretty print
    print(f"\n{'='*70}")
    print(f"  SIGNAL ACCURACY REPORT — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}")
    for source, sr in report.items():
        print(f"\n{'─'*50}")
        print(f"  {source.upper()} ({sr['total_signals']} total signals)")
        print(f"{'─'*50}")
        for window, wr in sr["windows"].items():
            if wr["resolved"] == 0:
                print(f"  {window:>4s}: {wr['pending']} pending")
                continue
            print(f"  {window:>4s}: accuracy={wr['accuracy_pct']:5.1f}% | avg_delta={wr['avg_delta_pct']:+7.3f}% | median={wr['median_delta_pct']:+7.3f}% | n={wr['resolved']} ({wr['pending']} pending)")
            if "avg_delta_sev2" in wr:
                print(f"        sev2_avg={wr['avg_delta_sev2']:+7.3f}%", end="")
                if "avg_delta_sev3" in wr:
                    print(f" | sev3_avg={wr['avg_delta_sev3']:+7.3f}%", end="")
                print()
            if "bullish_avg_delta" in wr:
                print(f"        bullish: n={wr['bullish_count']} acc={wr['bullish_accuracy']:.1f}% avg={wr['bullish_avg_delta']:+.3f}%", end="")
                if "bearish_avg_delta" in wr:
                    print(f" | bearish: n={wr['bearish_count']} acc={wr['bearish_accuracy']:.1f}% avg={wr['bearish_avg_delta']:+.3f}%", end="")
                print()
            if "high_conviction_accuracy" in wr:
                print(f"        high_conv(|s|≥0.3): n={wr['high_conviction_count']} acc={wr['high_conviction_accuracy']:.1f}%", end="")
                if "low_conviction_accuracy" in wr:
                    print(f" | low_conv: n={wr['low_conviction_count']} acc={wr['low_conviction_accuracy']:.1f}%", end="")
                print()
    print(f"\n{'='*70}")
    print(f"Report saved to: {REPORT_FILE}")


async def do_backfill_manipulation():
    """Backfill all existing manipulation_history entries and resolve what we can."""
    hist_file = DATA_DIR / "manipulation_history.json"
    if not hist_file.exists():
        logger.error("No manipulation_history.json found")
        return
    with open(hist_file) as f:
        history = json.load(f)
    existing = load_signals()
    existing_keys = set()
    for s in existing:
        if s["source"] == "manipulation":
            existing_keys.add(f"{s['symbol']}_{s['signal_ts']}")
    new_count = 0
    for event in history:
        key = f"{event['symbol']}_{event['timestamp']}"
        if key in existing_keys:
            continue
        ts_str = event.get("timestamp", "")
        try:
            ts_dt = datetime.fromisoformat(ts_str)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            ts_epoch = ts_dt.timestamp()
        except Exception:
            continue
        sig = {
            "source": "manipulation",
            "symbol": event["symbol"],
            "signal_ts": ts_str,
            "signal_ts_epoch": ts_epoch,
            "price_at_signal": event.get("price", 0),
            "severity": event.get("severity", 0),
            "reasons": event.get("reasons", []),
            "forecast": "AVOID",
            "deltas": {},
        }
        if sig["price_at_signal"] > 0:
            append_signal(sig)
            new_count += 1
    logger.info(f"Backfilled {new_count} manipulation events (skipped {len(history) - new_count} existing/no-price)")
    # Now resolve
    await do_resolve()


async def daemon():
    """Run snapshot + resolve on a loop."""
    logger.info("Starting signal accuracy tracker daemon")
    logger.info(f"  Snapshot interval: {SNAPSHOT_INTERVAL}s | Resolve interval: {RESOLVE_INTERVAL}s")
    last_resolve = 0
    while True:
        try:
            await do_snapshot()
            now = time.time()
            if now - last_resolve >= RESOLVE_INTERVAL:
                await do_resolve()
                last_resolve = now
        except Exception as e:
            logger.error(f"Daemon error: {e}", exc_info=True)
        await asyncio.sleep(SNAPSHOT_INTERVAL)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1].lower()
    if cmd == "snapshot":
        asyncio.run(do_snapshot())
    elif cmd == "resolve":
        asyncio.run(do_resolve())
    elif cmd == "report":
        do_report()
    elif cmd == "backfill":
        asyncio.run(do_backfill_manipulation())
    elif cmd == "daemon":
        asyncio.run(daemon())
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
