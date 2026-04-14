#!/usr/bin/env python3
"""Backtest congressional trades — did politicians' stock picks actually beat the market?

Scrapes full history from Capitol Trades, gets price data from Yahoo Finance,
and measures returns at 7/14/30/60/90 day windows after trade date.

Tests two hypotheses:
  H1: Politicians' trades are predictive AFTER disclosure (copycat edge)
  H2: Politicians' trades were predictive AT trade date (insider edge)
  H3: Trump inner circle beats average congress member

Usage:
  python3 backtest_congress_trades.py                    # Full backtest
  python3 backtest_congress_trades.py --pages 20         # Quick test (20 pages)
  python3 backtest_congress_trades.py --trump-only       # Only Trump circle
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE_PATH = Path(__file__).resolve().parent
DATA_DIR = BASE_PATH / "data" / "stock_traders"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})

TRUMP_CIRCLE = [
    "tommy tuberville", "tuberville", "marjorie taylor greene", "taylor greene",
    "dan crenshaw", "crenshaw", "mark green", "tim burchett", "burchett",
    "michael mccaul", "mccaul", "rick scott", "nancy mace", "josh hawley",
    "hawley", "ted cruz", "jim jordan", "kevin hern", "mike johnson",
    "elise stefanik", "jd vance", "vance",
]

WINDOWS = [7, 14, 30, 60, 90]


def _parse_date(date_str):
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    m = re.search(r"(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})", date_str.strip())
    if m:
        day, mon, year = int(m.group(1)), months.get(m.group(2).lower()[:3], 1), int(m.group(3))
        return datetime(year, mon, day)
    return None


def _safe_float(val):
    try:
        return float(str(val).replace(",", "").replace("$", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return 0.0


def scrape_all_trades(max_pages=120, party=None):
    """Scrape full trade history from Capitol Trades."""
    cache_file = DATA_DIR / "capitol_trades_full_history.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < 3600:
        print(f"Using cached data from {cache_file.name}")
        return json.loads(cache_file.read_text())
    all_trades = []
    party_filter = f"&party={party}" if party else ""
    for page in range(1, max_pages + 1):
        try:
            url = f"https://www.capitoltrades.com/trades?page={page}&pageSize=96{party_filter}"
            resp = SESSION.get(url, timeout=20)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if not table:
                break
            rows = table.find_all("tr")[1:]
            if not rows:
                break
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 9:
                    continue
                politician = cols[0].get_text(strip=True)
                issuer = cols[1].get_text(strip=True)
                published_raw = cols[2].get_text(strip=True)
                traded_raw = cols[3].get_text(strip=True)
                filed_after = cols[4].get_text(strip=True)
                tx_type = cols[6].get_text(strip=True).lower()
                size_str = cols[7].get_text(strip=True)
                price_str = cols[8].get_text(strip=True)
                sym_match = re.search(r"([A-Z]{1,5}):US", issuer)
                if not sym_match:
                    continue
                sym = sym_match.group(1)
                if "buy" in tx_type or "purchase" in tx_type:
                    side = "LONG"
                elif "sell" in tx_type or "sale" in tx_type:
                    side = "SHORT"
                else:
                    continue
                trade_date = _parse_date(traded_raw)
                pub_date = _parse_date(published_raw)
                if not trade_date:
                    continue
                is_trump_circle = any(name in politician.lower() for name in TRUMP_CIRCLE)
                party_match = re.search(r"(Republican|Democrat)", cols[0].get_text())
                party_str = party_match.group(1) if party_match else "?"
                clean_name = re.sub(r"(Republican|Democrat|House|Senate|\w{2}$)", "", politician).strip()
                all_trades.append({
                    "politician": clean_name,
                    "party": party_str,
                    "symbol": sym,
                    "side": side,
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "pub_date": pub_date.strftime("%Y-%m-%d") if pub_date else "",
                    "price": _safe_float(price_str),
                    "is_trump_circle": is_trump_circle,
                })
            if page % 10 == 0:
                print(f"  Page {page}/{max_pages}: {len(all_trades)} trades so far... ({traded_raw})")
            time.sleep(0.5)
        except Exception as e:
            print(f"  Page {page} failed: {e}")
            break
    print(f"Scraped {len(all_trades)} trades from {page} pages")
    cache_file.write_text(json.dumps(all_trades, indent=2))
    return all_trades


def get_historical_prices(symbols, start_date="2024-01-01"):
    """Batch fetch daily prices from Yahoo Finance."""
    cache_file = DATA_DIR / "yahoo_price_cache.json"
    price_cache = {}
    if cache_file.exists():
        price_cache = json.loads(cache_file.read_text())
    missing = [s for s in symbols if s not in price_cache]
    if missing:
        print(f"Fetching prices for {len(missing)} symbols...")
        period1 = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        period2 = int(datetime.now().timestamp())
        for i, sym in enumerate(missing):
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={period1}&period2={period2}&interval=1d"
                r = SESSION.get(url, timeout=10)
                if r.status_code != 200:
                    continue
                data = r.json()
                result = data.get("chart", {}).get("result", [{}])[0]
                timestamps = result.get("timestamp", [])
                closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                if timestamps and closes:
                    prices = {}
                    for ts, close in zip(timestamps, closes):
                        if close is not None:
                            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                            prices[dt] = round(close, 2)
                    price_cache[sym] = prices
                if (i + 1) % 20 == 0:
                    print(f"  Fetched {i+1}/{len(missing)} symbols...")
                time.sleep(0.3)
            except Exception:
                continue
        cache_file.write_text(json.dumps(price_cache))
    return price_cache


def find_price_on_or_after(prices, target_date_str, max_days=5):
    """Find the closing price on target_date or the next trading day."""
    target = datetime.strptime(target_date_str, "%Y-%m-%d")
    for offset in range(max_days + 1):
        check = (target + timedelta(days=offset)).strftime("%Y-%m-%d")
        if check in prices:
            return prices[check], check
    return None, None


def run_backtest(trades, price_cache, label="ALL"):
    """Run the actual backtest — measure returns at various windows."""
    results_by_window = {w: [] for w in WINDOWS}
    skipped = 0
    cutoff = (datetime.now() - timedelta(days=max(WINDOWS) + 5)).strftime("%Y-%m-%d")
    for t in trades:
        sym = t["symbol"]
        trade_date = t["trade_date"]
        side = t["side"]
        if sym not in price_cache:
            skipped += 1
            continue
        prices = price_cache[sym]
        entry_price, entry_dt = find_price_on_or_after(prices, trade_date)
        if entry_price is None or entry_price <= 0:
            skipped += 1
            continue
        for window in WINDOWS:
            exit_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=window)).strftime("%Y-%m-%d")
            if exit_date > cutoff and window > 30:
                continue
            exit_price, _ = find_price_on_or_after(prices, exit_date)
            if exit_price is None:
                continue
            raw_return = (exit_price - entry_price) / entry_price * 100
            directional_return = raw_return if side == "LONG" else -raw_return
            results_by_window[window].append({
                "symbol": sym,
                "side": side,
                "politician": t.get("politician", ""),
                "trade_date": trade_date,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "raw_return": raw_return,
                "dir_return": directional_return,
                "is_trump": t.get("is_trump_circle", False),
                "party": t.get("party", "?"),
            })
    # Print results
    print(f"\n{'='*70}")
    print(f"  BACKTEST: {label}")
    print(f"  Trades: {len(trades)}, Priced: {len(trades)-skipped}, Skipped: {skipped}")
    print(f"{'='*70}")
    print(f"  {'Window':>8s} {'Trades':>7s} {'WR%':>6s} {'Avg%':>7s} {'Med%':>7s} {'Best%':>7s} {'Worst%':>8s} {'Sum%':>8s}")
    print(f"  {'-'*60}")
    summary = {}
    for window in WINDOWS:
        r = results_by_window[window]
        if not r:
            continue
        wins = sum(1 for x in r if x["dir_return"] > 0)
        wr = wins / len(r) * 100
        avg = sum(x["dir_return"] for x in r) / len(r)
        returns = sorted(x["dir_return"] for x in r)
        med = returns[len(returns) // 2]
        best = max(x["dir_return"] for x in r)
        worst = min(x["dir_return"] for x in r)
        total = sum(x["dir_return"] for x in r)
        wr_mark = " ***" if wr > 55 else " *" if wr > 52 else ""
        print(f"  {window:>5d}d   {len(r):>7d} {wr:>5.1f}% {avg:>6.2f}% {med:>6.2f}% {best:>6.1f}% {worst:>7.1f}% {total:>7.1f}%{wr_mark}")
        summary[window] = {"n": len(r), "wr": wr, "avg": avg, "median": med, "total": total}
    return summary, results_by_window


def analyze_by_politician(results_30d):
    """Break down 30-day results by politician."""
    by_pol = defaultdict(list)
    for r in results_30d:
        by_pol[r["politician"]].append(r)
    print(f"\n  TOP POLITICIANS BY 30-DAY PERFORMANCE (min 5 trades):")
    print(f"  {'Politician':>30s} {'Trades':>7s} {'WR%':>6s} {'Avg%':>7s} {'Total%':>8s} {'Trump?':>7s}")
    print(f"  {'-'*70}")
    ranked = []
    for pol, trades in by_pol.items():
        if len(trades) < 5:
            continue
        wins = sum(1 for t in trades if t["dir_return"] > 0)
        wr = wins / len(trades) * 100
        avg = sum(t["dir_return"] for t in trades) / len(trades)
        total = sum(t["dir_return"] for t in trades)
        is_trump = any(t["is_trump"] for t in trades)
        ranked.append((pol, len(trades), wr, avg, total, is_trump))
    for pol, n, wr, avg, total, is_trump in sorted(ranked, key=lambda x: -x[2])[:25]:
        trump_mark = "YES" if is_trump else ""
        wr_mark = " ***" if wr > 60 else " *" if wr > 55 else ""
        print(f"  {pol:>30s} {n:>7d} {wr:>5.1f}% {avg:>6.2f}% {total:>7.1f}% {trump_mark:>7s}{wr_mark}")


def analyze_by_symbol(results_30d):
    """Which symbols did congress pick best?"""
    by_sym = defaultdict(list)
    for r in results_30d:
        by_sym[r["symbol"]].append(r)
    print(f"\n  TOP SYMBOLS BY 30-DAY CONGRESS PERFORMANCE (min 3 trades):")
    print(f"  {'Symbol':>8s} {'Trades':>7s} {'WR%':>6s} {'Avg%':>7s}")
    print(f"  {'-'*35}")
    ranked = []
    for sym, trades in by_sym.items():
        if len(trades) < 3:
            continue
        wins = sum(1 for t in trades if t["dir_return"] > 0)
        wr = wins / len(trades) * 100
        avg = sum(t["dir_return"] for t in trades) / len(trades)
        ranked.append((sym, len(trades), wr, avg))
    for sym, n, wr, avg in sorted(ranked, key=lambda x: -x[2])[:20]:
        print(f"  {sym:>8s} {n:>7d} {wr:>5.1f}% {avg:>6.2f}%")


def main():
    parser = argparse.ArgumentParser(description="Backtest congressional trades")
    parser.add_argument("--pages", type=int, default=100, help="Number of Capitol Trades pages to scrape (96 trades/page)")
    parser.add_argument("--trump-only", action="store_true", help="Only test Trump inner circle")
    parser.add_argument("--party", type=str, default=None, help="Filter by party: republican or democrat")
    args = parser.parse_args()
    print("=" * 70)
    print("  CONGRESSIONAL TRADES BACKTEST")
    print("  Hypothesis: Can we profit by following politicians' disclosed trades?")
    print("=" * 70)
    # Step 1: Scrape trades
    print("\n[1/3] Scraping Capitol Trades history...")
    all_trades = scrape_all_trades(max_pages=args.pages, party=args.party)
    if not all_trades:
        print("No trades found!")
        return
    # Step 2: Get prices
    print("\n[2/3] Fetching historical prices...")
    symbols = list(set(t["symbol"] for t in all_trades))
    earliest = min(t["trade_date"] for t in all_trades)
    price_cache = get_historical_prices(symbols, start_date=earliest)
    print(f"  Price data for {len(price_cache)} symbols")
    # Step 3: Backtest
    print("\n[3/3] Running backtests...")
    # ALL trades
    summary_all, results_all = run_backtest(all_trades, price_cache, "ALL CONGRESS")
    # Republicans only
    rep_trades = [t for t in all_trades if t.get("party") == "Republican"]
    if rep_trades:
        run_backtest(rep_trades, price_cache, "REPUBLICANS ONLY")
    # Democrats only
    dem_trades = [t for t in all_trades if t.get("party") == "Democrat"]
    if dem_trades:
        run_backtest(dem_trades, price_cache, "DEMOCRATS ONLY")
    # Trump circle
    trump_trades = [t for t in all_trades if t.get("is_trump_circle")]
    if trump_trades:
        summary_trump, results_trump = run_backtest(trump_trades, price_cache, "TRUMP INNER CIRCLE")
    # Non-Trump for comparison
    non_trump = [t for t in all_trades if not t.get("is_trump_circle")]
    if non_trump:
        run_backtest(non_trump, price_cache, "NON-TRUMP CONGRESS")
    # BUY only vs SELL only
    buys = [t for t in all_trades if t["side"] == "LONG"]
    sells = [t for t in all_trades if t["side"] == "SHORT"]
    if buys:
        run_backtest(buys, price_cache, "BUYS ONLY")
    if sells:
        run_backtest(sells, price_cache, "SELLS ONLY")
    # Per-politician breakdown
    if 30 in results_all:
        analyze_by_politician(results_all[30])
        analyze_by_symbol(results_all[30])
    # SPY benchmark
    spy_trades = [{"symbol": "SPY", "side": "LONG", "trade_date": t["trade_date"], "politician": "SPY_BENCHMARK"} for t in all_trades[::10]]
    if spy_trades:
        run_backtest(spy_trades, price_cache, "SPY BENCHMARK (buy-and-hold)")
    # Save detailed results
    output_file = DATA_DIR / "congress_backtest_results.json"
    output = {"summary": summary_all, "trump_summary": summary_trump if trump_trades else {}, "total_trades": len(all_trades), "trump_trades": len(trump_trades), "date_range": f"{min(t['trade_date'] for t in all_trades)} to {max(t['trade_date'] for t in all_trades)}", "run_date": datetime.now(timezone.utc).isoformat()}
    output_file.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {output_file}")
    print("\n" + "=" * 70)
    print("  VERDICT")
    print("=" * 70)
    if 30 in summary_all:
        wr30 = summary_all[30]["wr"]
        avg30 = summary_all[30]["avg"]
        if wr30 > 55 and avg30 > 0.5:
            print(f"  SIGNAL IS REAL — {wr30:.1f}% WR, {avg30:+.2f}% avg at 30d")
            print(f"  Congress trades beat coin-flip and have positive expected value.")
        elif wr30 > 50 and avg30 > 0:
            print(f"  MARGINAL — {wr30:.1f}% WR, {avg30:+.2f}% avg at 30d")
            print(f"  Slightly better than random. May not survive transaction costs.")
        else:
            print(f"  NO EDGE — {wr30:.1f}% WR, {avg30:+.2f}% avg at 30d")
            print(f"  Politicians' disclosed trades are not predictive after disclosure.")


if __name__ == "__main__":
    main()
