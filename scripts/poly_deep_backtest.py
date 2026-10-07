#!/usr/bin/env python3
"""
Polymarket Deep Backtest — comprehensive analysis of resolved markets
Fetches up to 5000 resolved markets, classifies by category, computes win rates
and EV at multiple confidence thresholds.
"""

import asyncio
import aiohttp
import json
import os
import sys
import math
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Category keyword map
# ---------------------------------------------------------------------------
CATEGORIES = {
    "sports_soccer": [
        "fc", "united", "city", "arsenal", "chelsea", "premier league", "la liga",
        "bundesliga", "champions league", "serie a", "ligue 1", "mls", "liverpool",
        "real madrid", "barcelona", "tottenham", "manchester", "napoli", "juventus",
        "wolves", "brighton", "brentford", "everton", "fulham", "bournemouth",
        "newcastle", "aston villa", "crystal", "leeds", "burnley", "leicester",
        "galatasaray",
    ],
    "sports_nba": [
        "nba", "lakers", "celtics", "warriors", "bulls", "nets", "knicks", "heat",
        "bucks", "nuggets", "76ers", "suns", "clippers", "grizzlies", "kings",
        "maverick", "thunder", "blazers", "raptors", "pistons", "rockets",
        "hornets", "spurs", "pelicans", "hawks", "wizards", "pacers", "jazz",
        "wolves", "timberwolves",
    ],
    "sports_nhl": [
        "nhl", "stanley cup", "bruins", "maple leafs", "rangers", "penguins",
        "blackhawks", "red wings", "canadiens", "flyers", "senators", "canucks",
        "oilers", "flames", "avalanche", "lightning", "capitals", "golden knights",
        "jets", "wild",
    ],
    "sports_nfl": [
        "nfl", "super bowl", "patriots", "cowboys", "chiefs", "eagles", "49ers",
        "packers", "ravens", "steelers", "broncos", "seahawks", "rams", "chargers",
        "dolphins", "bills",
    ],
    "sports_mlb": [
        "mlb", "world series", "yankees", "dodgers", "red sox", "cubs", "astros",
        "mets", "giants", "braves", "padres", "phillies", "cardinals", "athletics",
        "blue jays",
    ],
    "sports_esports": [
        "esports", "counter-strike", "valorant", "league of legends", "dota",
        "overwatch", "fortnite", "map 1", "map 2", "bo3", "bo5", "esl",
        "pro league", "major", "lan",
    ],
    "sports_combat": [
        "ufc", "boxing", "mma", "fight", "knockout", "title fight",
        "welterweight", "heavyweight",
    ],
    "politics_us": [
        "president", "congress", "senate", "house", "governor", "primary",
        "republican", "democrat", "trump", "biden", "harris", "election 2026",
        "election 2028", "nominee",
    ],
    "politics_intl": [
        "prime minister", "parliament", "election", "vote", "minister",
        "chancellor", "referendum", "coalition", "majority",
    ],
    "economics": [
        "fed", "federal reserve", "interest rate", "bps", "gdp", "inflation",
        "cpi", "fomc", "ecb", "bank of england", "bank of japan", "tariff",
        "recession", "unemployment",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "sol", "solana",
        "binance", "bnb", "xrp", "ripple", "matic", "polygon", "chainlink",
    ],
    "entertainment": [
        "oscar", "academy award", "grammy", "emmy", "golden globe", "film",
        "movie", "album", "chart", "billboard", "spotify", "youtube",
        "netflix", "box office",
    ],
    "weather": [
        "temperature", "rainfall", "hurricane", "earthquake", "celsius",
        "fahrenheit", "storm", "flood", "wildfire", "tornado",
    ],
    "tech": [
        "ipo", "merger", "acquisition", "launch", "release", "gpt", "ai model",
        "anthropic", "openai", "spacex", "tesla", "apple", "google", "microsoft",
    ],
}

THRESHOLDS = [0.92, 0.95, 0.97, 0.98]

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

MAX_CONCURRENT = 10
SAMPLE_PER_CATEGORY = 10  # for CLOB price history sampling


def classify(question: str) -> str:
    q = question.lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw in q for kw in keywords):
            return cat
    return "other"


def parse_ts(s):
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def safe_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def fetch_json(session, url, params=None, sem=None, retries=3):
    for attempt in range(retries):
        try:
            ctx = sem if sem else asyncio.nullcontext()
            async with ctx:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        return None
        except Exception:
            if attempt < retries - 1:
                await asyncio.sleep(1)
    return None


async def fetch_resolved_markets(session, sem, total=5000):
    """Paginate resolved markets from gamma API."""
    markets = []
    limit = 500
    offset = 0
    print(f"Fetching resolved markets (target: {total})...")
    while offset < total:
        batch_size = min(limit, total - offset)
        data = await fetch_json(session, f"{GAMMA_BASE}/markets", params={
            "active": "false",
            "closed": "true",
            "order": "closedTime",
            "ascending": "false",
            "limit": batch_size,
            "offset": offset,
        }, sem=sem)
        if not data:
            break
        batch = data if isinstance(data, list) else data.get("markets", data.get("data", []))
        if not batch:
            break
        markets.extend(batch)
        print(f"  fetched {len(markets)} resolved markets so far...")
        if len(batch) < batch_size:
            break
        offset += batch_size
    print(f"Total resolved markets fetched: {len(markets)}")
    return markets


async def fetch_active_markets(session, sem):
    """Fetch all active (not closed) markets."""
    markets = []
    limit = 500
    offset = 0
    print("Fetching active markets...")
    while True:
        data = await fetch_json(session, f"{GAMMA_BASE}/markets", params={
            "active": "true",
            "closed": "false",
            "order": "volumeNum",
            "ascending": "false",
            "limit": limit,
            "offset": offset,
        }, sem=sem)
        if not data:
            break
        batch = data if isinstance(data, list) else data.get("markets", data.get("data", []))
        if not batch:
            break
        markets.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    print(f"Total active markets fetched: {len(markets)}")
    return markets


async def fetch_clob_history(session, sem, token_id):
    """Fetch CLOB price history for a token."""
    data = await fetch_json(session, f"{CLOB_BASE}/prices-history", params={
        "market": token_id,
        "interval": "1d",
        "fidelity": "2",
    }, sem=sem)
    return data


# ---------------------------------------------------------------------------
# Process a resolved market
# ---------------------------------------------------------------------------

def process_resolved_market(m):
    """Extract fields needed for backtest from a resolved market dict."""
    question = m.get("question", "") or m.get("title", "") or ""
    category = classify(question)

    # outcome prices
    outcome_prices_raw = m.get("outcomePrices", [])
    if isinstance(outcome_prices_raw, str):
        try:
            outcome_prices_raw = json.loads(outcome_prices_raw)
        except Exception:
            outcome_prices_raw = []

    final_yes = None
    if outcome_prices_raw:
        v = safe_float(outcome_prices_raw[0])
        if v is not None:
            final_yes = 1.0 if v >= 0.95 else 0.0

    # last trade price (proxy for pre-resolution price)
    last_trade = safe_float(m.get("lastTradePrice")) or safe_float(m.get("bestAsk")) or safe_float(m.get("bestBid"))

    # resolution lag
    closed_str = m.get("closedTime") or m.get("resolutionTime")
    end_str = m.get("endDateIso") or m.get("endDate")
    resolution_lag_hours = None
    if closed_str and end_str:
        ct = parse_ts(closed_str)
        et = parse_ts(end_str)
        if ct and et:
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
            if et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)
            resolution_lag_hours = (ct - et).total_seconds() / 3600

    # token id for CLOB
    tokens = m.get("tokens") or []
    yes_token_id = None
    if isinstance(tokens, list) and tokens:
        for t in tokens:
            if isinstance(t, dict) and t.get("outcome", "").upper() == "YES":
                yes_token_id = t.get("token_id") or t.get("tokenId")
                break
        if not yes_token_id and isinstance(tokens[0], dict):
            yes_token_id = tokens[0].get("token_id") or tokens[0].get("tokenId")
    if not yes_token_id:
        yes_token_id = m.get("conditionId") or m.get("marketId")

    vol = safe_float(m.get("volume24hr")) or safe_float(m.get("volume")) or 0.0

    return {
        "question": question,
        "category": category,
        "final_yes": final_yes,
        "last_trade_price": last_trade,
        "resolution_lag_hours": resolution_lag_hours,
        "yes_token_id": yes_token_id,
        "volume": vol,
        "closed_time": closed_str,
        "end_date": end_str,
    }


# ---------------------------------------------------------------------------
# CLOB sampling
# ---------------------------------------------------------------------------

async def sample_clob_prices(session, sem, processed_markets):
    """For 10 markets per category, try to get CLOB entry price at threshold crossing."""
    by_cat = defaultdict(list)
    for m in processed_markets:
        if m["yes_token_id"] and m["final_yes"] is not None:
            by_cat[m["category"]].append(m)

    sample_map = {}  # token_id -> list of history points
    tasks = []
    sampled = []

    for cat, mlist in by_cat.items():
        for m in mlist[:SAMPLE_PER_CATEGORY]:
            sampled.append(m)
            tasks.append(fetch_clob_history(session, sem, m["yes_token_id"]))

    print(f"Fetching CLOB price history for {len(tasks)} sample markets...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    clob_entry_prices = {}
    for m, result in zip(sampled, results):
        if isinstance(result, Exception) or not result:
            continue
        history = result.get("history", []) if isinstance(result, dict) else []
        if history:
            # Find the first price that crossed each threshold from below
            prices = [(safe_float(h.get("t", 0)), safe_float(h.get("p", 0))) for h in history]
            prices = [(t, p) for t, p in prices if t is not None and p is not None]
            prices.sort(key=lambda x: x[0])
            clob_entry_prices[m["yes_token_id"]] = prices

    return clob_entry_prices


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------

def run_backtest(processed_markets, clob_prices):
    """Compute stats per category x threshold."""
    # stats[category][threshold] = {wins, losses, gross_returns, lags, false_positives}
    stats = defaultdict(lambda: defaultdict(lambda: {
        "wins": 0, "losses": 0, "gross_returns": [], "lags": [],
        "false_positives": [], "entries": [],
    }))

    for m in processed_markets:
        cat = m["category"]
        ltp = m["last_trade_price"]
        fy = m["final_yes"]
        lag = m["resolution_lag_hours"]

        if ltp is None or fy is None:
            continue

        for thresh in THRESHOLDS:
            if ltp >= thresh:
                entry = ltp
                # Check CLOB for better entry estimate
                tid = m.get("yes_token_id")
                if tid and tid in clob_prices:
                    history = clob_prices[tid]
                    # Find first time price crossed threshold
                    for _, p in history:
                        if p >= thresh:
                            entry = p
                            break

                # Exit at 1.0 if YES, 0.0 if NO
                exit_price = 1.0 if fy >= 0.95 else 0.0
                gross_return = (exit_price - entry) / entry * 100 if entry > 0 else 0.0

                s = stats[cat][thresh]
                if fy >= 0.95:
                    s["wins"] += 1
                    s["gross_returns"].append(gross_return)
                else:
                    s["losses"] += 1
                    s["gross_returns"].append(-100.0)  # full loss
                    s["false_positives"].append({
                        "question": m["question"][:120],
                        "last_trade": ltp,
                        "threshold": thresh,
                        "category": cat,
                    })
                if lag is not None:
                    s["lags"].append(lag)
                s["entries"].append(entry)

    return stats


def compute_ev_table(stats):
    rows = []
    for cat, thresh_data in stats.items():
        for thresh, s in thresh_data.items():
            total = s["wins"] + s["losses"]
            if total == 0:
                continue
            win_rate = s["wins"] / total
            loss_rate = 1 - win_rate
            avg_gross = sum(s["gross_returns"]) / len(s["gross_returns"]) if s["gross_returns"] else 0.0
            avg_lag = sum(s["lags"]) / len(s["lags"]) if s["lags"] else None
            avg_entry = sum(s["entries"]) / len(s["entries"]) if s["entries"] else thresh

            # EV = winRate * win_pct + lossRate * (-100)
            win_return = (1.0 - avg_entry) / avg_entry * 100 if avg_entry > 0 else 0.0
            ev = win_rate * win_return - loss_rate * 100

            rows.append({
                "category": cat,
                "threshold": thresh,
                "total": total,
                "wins": s["wins"],
                "losses": s["losses"],
                "win_rate": round(win_rate * 100, 2),
                "avg_gross_return": round(avg_gross, 2),
                "avg_entry": round(avg_entry, 4),
                "ev": round(ev, 3),
                "avg_lag_hours": round(avg_lag, 1) if avg_lag is not None else None,
                "false_positives": s["false_positives"][:5],
            })

    rows.sort(key=lambda r: r["ev"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Active market analysis
# ---------------------------------------------------------------------------

def analyze_active_markets(active_markets, now):
    past_end = []
    expiring_soon = []

    for m in active_markets:
        question = m.get("question", "") or m.get("title", "") or ""
        category = classify(question)

        ltp = safe_float(m.get("lastTradePrice")) or safe_float(m.get("bestAsk")) or 0.0
        if ltp < 0.92:
            continue

        end_str = m.get("endDateIso") or m.get("endDate")
        et = parse_ts(end_str)
        if et is None:
            continue
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)

        hours_past_end = (now - et).total_seconds() / 3600
        vol24h = safe_float(m.get("volume24hr")) or 0.0

        # Determine tier
        if ltp >= 0.98:
            tier = "0.98+"
        elif ltp >= 0.97:
            tier = "0.97+"
        elif ltp >= 0.95:
            tier = "0.95+"
        else:
            tier = "0.92+"

        net_return_pct = (1.0 - ltp) / ltp * 100 if ltp > 0 else 0.0

        record = {
            "question": question[:100],
            "category": category,
            "tier": tier,
            "last_trade": ltp,
            "end_date": end_str,
            "hours_past_end": round(hours_past_end, 1),
            "net_return_pct": round(net_return_pct, 2),
            "vol24h": vol24h,
        }

        if hours_past_end > 0:
            past_end.append(record)
        elif abs(hours_past_end) <= 24:
            expiring_soon.append(record)

    past_end.sort(key=lambda x: x["last_trade"], reverse=True)
    expiring_soon.sort(key=lambda x: x["last_trade"], reverse=True)
    return past_end, expiring_soon


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def print_separator(char="=", width=90):
    print(char * width)


def print_ev_table(rows):
    print_separator()
    print("TOP 10 CATEGORY + THRESHOLD COMBINATIONS BY EXPECTED VALUE")
    print_separator()
    print(f"{'#':<4} {'Category':<20} {'Thresh':<8} {'N':<6} {'WinRate':<9} {'AvgEntry':<10} {'EV%':<8} {'AvgLag(h)':<12}")
    print("-" * 90)
    for i, r in enumerate(rows[:10], 1):
        lag_str = f"{r['avg_lag_hours']:.1f}" if r["avg_lag_hours"] is not None else "N/A"
        print(f"{i:<4} {r['category']:<20} {r['threshold']:<8} {r['total']:<6} {r['win_rate']:<9.1f} {r['avg_entry']:<10.4f} {r['ev']:<8.3f} {lag_str:<12}")
    print()


def print_false_positives(rows):
    print_separator()
    print("FALSE POSITIVE EXAMPLES (High confidence → Resolved NO)")
    print_separator()
    shown = 0
    for r in rows:
        fps = r.get("false_positives", [])
        if not fps:
            continue
        for fp in fps[:2]:
            if shown >= 20:
                break
            print(f"  [{fp['category']} @ {fp['threshold']}] price={fp['last_trade']:.3f}")
            print(f"    Q: {fp['question']}")
            shown += 1
    if shown == 0:
        print("  No false positives found in sample.")
    print()


def print_active_opportunities(past_end, expiring_soon):
    print_separator()
    print("CURRENT PAST-END OPPORTUNITIES (Active markets past endDate, price >= 0.92)")
    print_separator()
    if not past_end:
        print("  None found.")
    else:
        for r in past_end[:20]:
            print(f"  [{r['category']} {r['tier']}] price={r['last_trade']:.3f} | "
                  f"+{r['hours_past_end']:.1f}h past end | "
                  f"net={r['net_return_pct']:.2f}% | vol24h={r['vol24h']:.0f}")
            print(f"    Q: {r['question']}")
    print()

    print_separator()
    print("MARKETS EXPIRING < 24H AT 92%+ (Active, endDate within 24h)")
    print_separator()
    if not expiring_soon:
        print("  None found.")
    else:
        for r in expiring_soon[:20]:
            hrs_left = abs(r["hours_past_end"])
            print(f"  [{r['category']} {r['tier']}] price={r['last_trade']:.3f} | "
                  f"{hrs_left:.1f}h until end | "
                  f"net={r['net_return_pct']:.2f}% | vol24h={r['vol24h']:.0f}")
            print(f"    Q: {r['question']}")
    print()


def print_recommendations(rows, past_end, expiring_soon):
    print_separator()
    print("RECOMMENDED LIVE SETTINGS")
    print_separator()
    if rows:
        best = rows[0]
        print(f"  Best category: {best['category']}")
        print(f"  Best threshold: {best['threshold']}")
        print(f"  Expected EV: {best['ev']:.3f}%")
        print(f"  Win rate: {best['win_rate']:.1f}%")
        print(f"  Avg resolution lag: {best['avg_lag_hours']}h")
        print()
        print("  Position sizing guidance:")
        if best["ev"] > 2:
            print("    → Strong edge: 2-4% of capital per trade")
        elif best["ev"] > 0:
            print("    → Mild edge: 1-2% of capital per trade")
        else:
            print("    → No edge at top category — consider higher threshold or other categories")
        print()
        print("  Top 5 by EV:")
        for r in rows[:5]:
            print(f"    {r['category']} @ {r['threshold']}: EV={r['ev']:.3f}%, WR={r['win_rate']:.1f}%, N={r['total']}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    t0 = time.time()
    now = datetime.now(timezone.utc)

    print("=" * 90)
    print("POLYMARKET DEEP BACKTEST ANALYSIS")
    print(f"Run time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 90)
    print()

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(limit=20, ssl=False)
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # 1. Fetch resolved + active markets in parallel
        resolved_task = fetch_resolved_markets(session, sem, total=5000)
        active_task = fetch_active_markets(session, sem)
        resolved_raw, active_raw = await asyncio.gather(resolved_task, active_task)

        print()

        # 2. Process resolved markets
        print("Processing resolved markets...")
        processed = [process_resolved_market(m) for m in resolved_raw]
        valid = [m for m in processed if m["final_yes"] is not None and m["last_trade_price"] is not None]
        print(f"  Valid for backtest: {len(valid)} / {len(processed)}")

        # Category distribution
        by_cat = defaultdict(int)
        for m in valid:
            by_cat[m["category"]] += 1
        print("  Category distribution:")
        for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
            print(f"    {cat:<25} {cnt}")
        print()

        # 3. CLOB sampling
        clob_prices = await sample_clob_prices(session, sem, valid)
        print(f"  CLOB histories fetched: {len(clob_prices)}")
        print()

    # 4. Run backtest
    print("Running backtest analysis...")
    stats = run_backtest(valid, clob_prices)
    ev_rows = compute_ev_table(stats)
    print(f"  Computed {len(ev_rows)} category×threshold combinations")
    print()

    # 5. Analyze active markets
    past_end, expiring_soon = analyze_active_markets(active_raw, now)

    # 6. Print results
    print_ev_table(ev_rows)
    print_false_positives(ev_rows)
    print_active_opportunities(past_end, expiring_soon)
    print_recommendations(ev_rows, past_end, expiring_soon)

    # 7. Save full results
    out_dir = "/Users/niels/Documents/binance/data/poly/highconf"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "deep_backtest.json")

    output = {
        "run_time": now.isoformat(),
        "total_resolved_fetched": len(resolved_raw),
        "total_valid": len(valid),
        "ev_table": ev_rows,
        "past_end_opportunities": past_end,
        "expiring_soon": expiring_soon,
        "category_distribution": dict(by_cat),
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    elapsed = time.time() - t0
    print_separator()
    print(f"Full results saved to: {out_path}")
    print(f"Total elapsed: {elapsed:.1f}s")
    print_separator()


if __name__ == "__main__":
    asyncio.run(main())
