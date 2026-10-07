#!/usr/bin/env python3
"""poly_lag_analyzer.py — Find where Polymarket oracle lag is biggest.

Analyses historical resolution lags across all market categories, then
identifies LIVE opportunities where an external source already shows the
outcome but Polymarket hasn't updated yet.

Data sources (all free, no API keys):
  - Polymarket Gamma API  (market metadata, closedTime)
  - ESPN public API       (NBA, NFL, MLB, NHL, soccer scores)
  - AP/BBC RSS feeds      (elections, geopolitical events)
  - Binance public API    (crypto price markets)

Usage:
  python poly_lag_analyzer.py                    # full analysis + live scan
  python poly_lag_analyzer.py --mode history     # historical lag by category only
  python poly_lag_analyzer.py --mode live        # live opportunity scanner only
  python poly_lag_analyzer.py --mode monitor     # continuous real-time monitor
  python poly_lag_analyzer.py --top 30           # show top 30 lag markets
"""

import argparse
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_lag")

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
BINANCE_URL = "https://api.binance.com/api/v3"
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports"
CACHE_DIR = Path("./data/poly/lag_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OPP_LOG = Path("./data/poly/lag_opportunities.jsonl")

# ESPN sport/league endpoints
ESPN_LEAGUES = {
    "nba": ("basketball", "nba"),
    "nfl": ("football", "nfl"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "epl": ("soccer", "eng.1"),
    "champions_league": ("soccer", "uefa.champions"),
    "la_liga": ("soccer", "esp.1"),
    "mls": ("soccer", "usa.1"),
    "bundesliga": ("soccer", "ger.1"),
    "serie_a": ("soccer", "ita.1"),
}

# Crypto price market tracking
CRYPTO_PAIRS = {
    "bitcoin": "BTCUSDC", "btc": "BTCUSDC",
    "ethereum": "ETHUSDC", "eth": "ETHUSDC",
    "solana": "SOLUSDC", "sol": "SOLUSDC",
}

REQUEST_DELAY = 0.3


# ── Cache ──────────────────────────────────────────────────────────────────────

def _cache_get(key: str, max_age_secs: int = 3600) -> Optional[object]:
    p = CACHE_DIR / f"{re.sub(r'[^a-zA-Z0-9_-]', '_', key)[:100]}.json"
    if p.exists() and (time.time() - p.stat().st_mtime) < max_age_secs:
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _cache_set(key: str, data: object):
    p = CACHE_DIR / f"{re.sub(r'[^a-zA-Z0-9_-]', '_', key)[:100]}.json"
    try:
        with open(p, "w") as f:
            json.dump(data, f, default=str)
    except Exception:
        pass


# ── Polymarket Data ────────────────────────────────────────────────────────────

def fetch_resolved_markets(limit: int = 500) -> List[dict]:
    key = f"resolved_{limit}"
    cached = _cache_get(key, max_age_secs=7200)
    if cached:
        return cached
    results = []
    offset = 0
    while len(results) < limit:
        try:
            resp = requests.get(f"{GAMMA_URL}/markets", params={"closed": "true", "order": "volumeNum", "ascending": "false", "limit": 100, "offset": offset}, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            offset += 100
            if len(batch) < 100:
                break
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.error(f"fetch_resolved: {e}")
            break
    results = results[:limit]
    _cache_set(key, results)
    return results

def fetch_active_markets(limit: int = 500) -> List[dict]:
    key = f"active_{limit}"
    cached = _cache_get(key, max_age_secs=300)
    if cached:
        return cached
    results = []
    offset = 0
    while len(results) < limit:
        try:
            resp = requests.get(f"{GAMMA_URL}/markets", params={"active": "true", "closed": "false", "order": "volumeNum", "ascending": "false", "limit": 100, "offset": offset}, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            offset += 100
            if len(batch) < 100:
                break
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.error(f"fetch_active: {e}")
            break
    results = results[:limit]
    _cache_set(key, results)
    return results

def get_market_price(token_id: str) -> Optional[float]:
    try:
        resp = requests.get(f"{CLOB_URL}/last-trade-price", params={"token_id": token_id}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            p = data.get("price")
            return float(p) if p else None
    except Exception:
        pass
    return None


# ── Market Classification ──────────────────────────────────────────────────────

def classify_market(m: dict) -> dict:
    question = m.get("question", "").lower()
    events = m.get("events", [])
    ev = events[0] if events else {}
    slug = ev.get("slug", "").lower()
    text = question + " " + slug

    if any(x in text for x in ["nba", "lakers", "celtics", "warriors", "heat", "knicks", "bulls", "nets", "playoff", "basketball"]):
        return {"category": "sports_nba", "emoji": "🏀"}
    if any(x in text for x in ["nfl", "super bowl", "superbowl", "chiefs", "eagles", "cowboys", "patriots", "rams", "football", "quarterback"]):
        return {"category": "sports_nfl", "emoji": "🏈"}
    if any(x in text for x in ["champions league", "premier league", "la liga", "bundesliga", "serie a", "mls", "world cup", "soccer", "football match", "goal", "epl"]):
        return {"category": "sports_soccer", "emoji": "⚽"}
    if any(x in text for x in ["mlb", "world series", "baseball", "yankees", "dodgers", "cubs", "red sox"]):
        return {"category": "sports_mlb", "emoji": "⚾"}
    if any(x in text for x in ["nhl", "stanley cup", "hockey", "bruins", "maple leafs", "rangers", "penguins"]):
        return {"category": "sports_nhl", "emoji": "🏒"}
    if any(x in text for x in ["boxing", "mma", "ufc", "fight", "bout"]):
        return {"category": "sports_combat", "emoji": "🥊"}
    if any(x in text for x in ["election", "presidential", "senator", "governor", "vote", "ballot", "primary", "runoff"]):
        return {"category": "election", "emoji": "🗳️"}
    if any(x in text for x in ["trump", "biden", "harris", "musk", "president", "congress", "senate"]):
        return {"category": "politics", "emoji": "🏛️"}
    if any(x in text for x in ["btc", "bitcoin", "eth", "ethereum", "crypto", "sol", "fed rate", "interest rate"]):
        return {"category": "crypto_econ", "emoji": "💰"}
    if any(x in text for x in ["oscar", "grammy", "emmy", "award", "movie", "album", "song"]):
        return {"category": "entertainment", "emoji": "🎬"}
    if any(x in text for x in ["weather", "temperature", "rain", "snow", "hurricane", "celsius", "fahrenheit"]):
        return {"category": "weather", "emoji": "🌤️"}
    return {"category": "other", "emoji": "📋"}

def parse_lag(m: dict) -> Optional[float]:
    closed_raw = m.get("closedTime", "")
    end_date = m.get("endDateIso", "")
    if not closed_raw or not end_date:
        return None
    try:
        closed_dt = datetime.fromisoformat(closed_raw.replace("+00", "+00:00"))
        end_dt = datetime.fromisoformat(end_date + "T23:59:59+00:00")
        return (closed_dt - end_dt).total_seconds() / 3600
    except Exception:
        return None


# ── ESPN Sports API ────────────────────────────────────────────────────────────

def fetch_espn_games(sport: str, league: str, date_str: str) -> List[dict]:
    key = f"espn_{sport}_{league}_{date_str}"
    cached = _cache_get(key, max_age_secs=1800)
    if cached:
        return cached
    try:
        resp = requests.get(f"{ESPN_URL}/{sport}/{league}/scoreboard", params={"dates": date_str}, timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
        games = []
        for ev in events:
            status = ev.get("status", {}).get("type", {})
            comps = ev.get("competitions", [{}])[0]
            competitors = comps.get("competitors", [])
            teams = []
            winner = None
            for c in competitors:
                name = c.get("team", {}).get("displayName", "")
                short = c.get("team", {}).get("shortDisplayName", "")
                score = c.get("score", "0")
                is_winner = c.get("winner", False)
                teams.append({"name": name, "short": short, "score": score, "winner": is_winner})
                if is_winner:
                    winner = name
            games.append({"id": ev.get("id"), "name": ev.get("name", ""), "start_time": ev.get("date", ""), "completed": status.get("completed", False), "status": status.get("description", ""), "teams": teams, "winner": winner, "sport": sport, "league": league})
        _cache_set(key, games)
        time.sleep(REQUEST_DELAY)
        return games
    except Exception as e:
        logger.debug(f"ESPN {sport}/{league} {date_str}: {e}")
        return []

def fetch_espn_games_range(sport: str, league: str, days_back: int = 2) -> List[dict]:
    all_games = []
    for d in range(days_back + 1):
        date = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y%m%d")
        all_games.extend(fetch_espn_games(sport, league, date))
    return all_games

def fetch_all_recent_games(days_back: int = 2) -> List[dict]:
    all_games = []
    for name, (sport, league) in ESPN_LEAGUES.items():
        games = fetch_espn_games_range(sport, league, days_back)
        all_games.extend(games)
        if games:
            logger.info(f"ESPN {name}: {len(games)} games (last {days_back}d)")
    return all_games


# ── Fuzzy Market ↔ Game Matching ──────────────────────────────────────────────

def extract_team_hints(question: str) -> List[str]:
    q = question.lower()
    words = re.findall(r"[a-z]+", q)
    stop = {"will", "the", "win", "beat", "vs", "against", "game", "match", "championship", "series", "playoffs", "tonight", "today", "over", "their", "who", "which", "advance", "make", "reach", "nba", "nfl", "mlb", "nhl"}
    hints = [w for w in words if len(w) > 3 and w not in stop]
    return hints

def match_game_to_market(question: str, games: List[dict]) -> Optional[dict]:
    hints = extract_team_hints(question)
    best_score = 0
    best_game = None
    for game in games:
        game_text = game["name"].lower() + " " + " ".join(t["name"].lower() + " " + t["short"].lower() for t in game["teams"])
        score = sum(1 for h in hints if h in game_text)
        if score > best_score and score >= 2:
            best_score = score
            best_game = game
    return best_game


# ── Binance Price Verifier ─────────────────────────────────────────────────────

def get_binance_price(symbol: str) -> Optional[float]:
    try:
        resp = requests.get(f"{BINANCE_URL}/ticker/price", params={"symbol": symbol}, timeout=5)
        return float(resp.json()["price"])
    except Exception:
        return None

def verify_crypto_outcome(question: str, target_price: float, direction: str) -> Optional[dict]:
    q_lower = question.lower()
    for kw, pair in CRYPTO_PAIRS.items():
        if kw in q_lower:
            current = get_binance_price(pair)
            if current is None:
                return None
            if direction == "above":
                outcome_known = current > target_price
            else:
                outcome_known = current < target_price
            return {"pair": pair, "current_price": current, "target": target_price, "direction": direction, "outcome_known": outcome_known, "likely_yes": outcome_known}
    return None


# ── Historical Lag Analysis ────────────────────────────────────────────────────

def analyze_historical_lags(markets: List[dict], top_n: int = 20) -> dict:
    rows = []
    for m in markets:
        lag_h = parse_lag(m)
        if lag_h is None:
            continue
        clf = classify_market(m)
        events = m.get("events", [])
        ev = events[0] if events else {}
        rows.append({"question": m.get("question", "")[:70], "category": clf["category"], "emoji": clf["emoji"], "lag_hours": lag_h, "volume_m": float(m.get("volumeNum", 0) or 0) / 1e6, "end_date": m.get("endDateIso", ""), "closed_time": m.get("closedTime", "")[:16], "slug": ev.get("slug", "")[:40]})

    cat_lags = defaultdict(list)
    for r in rows:
        cat_lags[r["category"]].append(r["lag_hours"])

    import statistics
    cat_stats = {}
    for cat, lags in cat_lags.items():
        positive_lags = [l for l in lags if l > 0]
        cat_stats[cat] = {"count": len(lags), "median_lag_h": round(statistics.median(lags), 1), "positive_count": len(positive_lags), "median_positive_lag_h": round(statistics.median(positive_lags), 1) if positive_lags else 0, "max_lag_h": round(max(lags), 1), "pct_slow": round(len(positive_lags) / len(lags) * 100, 1)}

    positive_rows = [r for r in rows if r["lag_hours"] > 0]
    top_slow = sorted(positive_rows, key=lambda x: -x["lag_hours"])[:top_n]
    return {"category_stats": cat_stats, "top_slow_markets": top_slow, "total_analyzed": len(rows)}


# ── Live Opportunity Scanner ───────────────────────────────────────────────────

def find_live_opportunities(active_markets: List[dict], recent_games: List[dict]) -> List[dict]:
    opportunities = []
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    for m in active_markets:
        q = m.get("question", "")
        end_date = m.get("endDateIso", "")
        last_price = float(m.get("lastTradePrice", 0.5) or 0.5)
        vol = float(m.get("volumeNum", 0) or 0)
        token_ids_raw = m.get("clobTokenIds", "[]")
        try:
            token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
        except Exception:
            token_ids = []
        yes_token = token_ids[0] if token_ids else None

        if end_date not in (today, yesterday) and end_date > today:
            continue
        if vol < 5000:
            continue

        clf = classify_market(m)
        cat = clf["category"]
        opp = None

        if cat.startswith("sports_"):
            matched_game = match_game_to_market(q, recent_games)
            if matched_game and matched_game["completed"] and matched_game["winner"]:
                q_lower = q.lower()
                winner_name = matched_game["winner"].lower()
                winner_tokens = winner_name.split()
                question_mentions_winner = any(tok in q_lower for tok in winner_tokens if len(tok) > 3)
                teams = matched_game["teams"]
                loser = next((t["name"] for t in teams if not t["winner"]), "")
                question_mentions_loser = any(tok in q_lower for tok in loser.lower().split() if len(tok) > 3)
                if question_mentions_winner and 0.01 < last_price < 0.90:
                    # YES token represents winner, but market hasn't moved to 1.0 yet
                    opp = {"type": "sports_lag", "market": q[:70], "end_date": end_date, "current_price": last_price, "game": matched_game["name"], "winner": matched_game["winner"], "expected_outcome": "YES", "expected_price": 1.0, "potential_profit_pct": round((1.0 - last_price) / last_price * 100, 1), "volume_24h": vol, "confidence": "HIGH", "source": f"ESPN {matched_game['league']}", "action": f"BUY YES at {last_price:.3f} → resolves to 1.0 when oracle updates"}
                elif question_mentions_loser and 0.10 < last_price < 0.85:
                    # YES token represents loser, market still shows 10-85% win chance — lag!
                    opp = {"type": "sports_lag", "market": q[:70], "end_date": end_date, "current_price": last_price, "game": matched_game["name"], "winner": matched_game["winner"], "expected_outcome": "NO", "expected_price": 0.0, "potential_profit_pct": round(last_price / (1 - last_price) * 100, 1), "volume_24h": vol, "confidence": "HIGH", "source": f"ESPN {matched_game['league']}", "action": f"BUY NO at {1-last_price:.3f} → resolves to 1.0 when oracle updates"}

        elif cat in ("election", "politics"):
            if (last_price > 0.85 or last_price < 0.15) and end_date <= today:
                opp = {"type": "election_near_resolution", "market": q[:70], "end_date": end_date, "current_price": last_price, "expected_outcome": "YES" if last_price > 0.85 else "NO", "expected_price": 1.0, "potential_profit_pct": round((1.0 - max(last_price, 1 - last_price)) / max(last_price, 1 - last_price) * 100, 1), "volume_24h": vol, "confidence": "MEDIUM", "source": "endDate passed + extreme price", "action": "Verify outcome via news API / AP / BBC before entering"}

        elif cat == "crypto_econ":
            price_match = re.search(r"\$([0-9,]+(?:\.[0-9]+)?)", q)
            direction_above = "above" in q.lower() or "reach" in q.lower() or "hit" in q.lower() or "exceed" in q.lower()
            direction_below = "below" in q.lower() or "under" in q.lower() or "fall" in q.lower()
            if price_match and (direction_above or direction_below):
                target = float(price_match.group(1).replace(",", ""))
                direction = "above" if direction_above else "below"
                result = verify_crypto_outcome(q, target, direction)
                if result and result["outcome_known"]:
                    expected = "YES" if result["likely_yes"] else "NO"
                    entry_price = last_price if expected == "YES" else (1 - last_price)
                    if entry_price < 0.90:
                        opp = {"type": "crypto_oracle_lag", "market": q[:70], "end_date": end_date, "current_price": last_price, "expected_outcome": expected, "binance_price": result["current_price"], "target_price": target, "direction": direction, "potential_profit_pct": round((1.0 - entry_price) / entry_price * 100, 1), "volume_24h": vol, "confidence": "HIGH", "source": f"Binance {result['pair']}", "action": f"BUY {expected} at {entry_price:.3f} → outcome verified via Binance"}

        if opp:
            if yes_token:
                live_p = get_market_price(yes_token)
                if live_p is not None:
                    opp["live_price"] = live_p
            opportunities.append(opp)

    opportunities.sort(key=lambda x: -x["potential_profit_pct"])
    return opportunities


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_lag_report(analysis: dict, top_n: int = 20):
    print("\n" + "=" * 76)
    print("  POLYMARKET ORACLE LAG ANALYSIS — Historical Resolution Delays")
    print("=" * 76)
    print(f"\n  {'Category':<20} {'Markets':>8} {'Slow%':>7} {'Med Lag':>10} {'Med Slow Lag':>14} {'Max Lag':>10}")
    print("  " + "-" * 72)
    stats = analysis["category_stats"]
    for cat, s in sorted(stats.items(), key=lambda x: -x[1]["median_positive_lag_h"]):
        if s["count"] < 2:
            continue
        print(f"  {cat:<20} {s['count']:>8} {s['pct_slow']:>6.0f}% {s['median_lag_h']:>9.1f}h {s['median_positive_lag_h']:>13.1f}h {s['max_lag_h']:>9.1f}h")
    print()
    print(f"  TOP {top_n} MARKETS WITH LARGEST ORACLE LAG (positive = oracle was slow)")
    print("  " + "-" * 72)
    for r in analysis["top_slow_markets"][:top_n]:
        print(f"  {r['emoji']} lag={r['lag_hours']:>7.1f}h  ${r['volume_m']:>7.1f}M  {r['question']}")
    print()

def print_opportunities(opportunities: List[dict]):
    print("=" * 76)
    print("  LIVE ORACLE LAG OPPORTUNITIES — Enter BEFORE oracle updates")
    print("=" * 76)
    if not opportunities:
        print("  No live opportunities found at this moment.")
        print("  (Check again after sports events end or after election closes)")
    else:
        for i, opp in enumerate(opportunities, 1):
            conf_icon = "🟢" if opp["confidence"] == "HIGH" else "🟡"
            print(f"\n  [{i}] {conf_icon} {opp['type'].upper()}")
            print(f"  Market : {opp['market']}")
            print(f"  Price  : {opp['current_price']:.3f} → expected {opp['expected_outcome']} = 1.000")
            print(f"  Profit : {opp['potential_profit_pct']:.1f}% potential | vol=${opp['volume_24h']:,.0f}")
            print(f"  Source : {opp['source']}")
            print(f"  Action : {opp['action']}")
            if "game" in opp:
                print(f"  Game   : {opp['game']} → WINNER: {opp['winner']}")
            if "binance_price" in opp:
                print(f"  Binance: {opp['pair'] if 'pair' in opp else ''} = ${opp.get('binance_price',0):,.2f} vs target ${opp.get('target_price',0):,.0f}")
    print()

def print_strategic_summary(analysis: dict):
    print("=" * 76)
    print("  STRATEGIC RECOMMENDATIONS")
    print("=" * 76)
    stats = analysis["category_stats"]
    ranked = sorted(stats.items(), key=lambda x: -x[1]["median_positive_lag_h"])
    print()
    print("  WHERE THE BIGGEST ORACLE LAGS ARE (rank by median slow-resolution lag):")
    print()
    for rank, (cat, s) in enumerate(ranked[:8], 1):
        if s["positive_count"] < 2:
            continue
        if s["median_positive_lag_h"] > 24:
            rec = "🔥 Very exploitable — lag measured in days"
        elif s["median_positive_lag_h"] > 6:
            rec = "⚡ Exploitable — lag measured in hours"
        elif s["median_positive_lag_h"] > 1:
            rec = "✅ Moderate — lag measured in hours"
        else:
            rec = "⚪ Fast oracle — small window only"
        print(f"  {rank}. {cat:<22} med lag = {s['median_positive_lag_h']:>6.1f}h  {rec}")
    print()
    print("  TACTICAL GUIDE BY CATEGORY:")
    print()
    print("  🗳️  ELECTIONS: Biggest lag (15-3000h). AP/Fox call race hours before oracle.")
    print("       → Monitor AP election results API. Enter YES on called winner immediately.")
    print()
    print("  🏀🏈⚾ SPORTS: Lag is minutes-to-hours after final whistle.")
    print("       → Monitor ESPN live scores. When STATUS_FINAL, enter before oracle.")
    print("       → Best targets: game-winner markets, not season-winner markets.")
    print()
    print("  💰  CRYPTO: Oracle lag 60-300min on hourly price markets.")
    print("       → Binance price instantly tells you if target was hit.")
    print("       → Best: '5min BTC price' markets if they appear on Polymarket.")
    print()
    print("  🥊  COMBAT SPORTS: Lag up to 2800h (Tyson-Paul fight!). Very slow oracle.")
    print("       → Check ESPN boxing results → enter before Poly updates.")
    print()
    print("  🎬  ENTERTAINMENT: Awards shows — winner known at ceremony, oracle slow.")
    print("       → Live-watch Oscars/Grammys. Enter YES on winner during broadcast.")
    print()
    print("=" * 76)
    print()


# ── Continuous Monitor Mode ────────────────────────────────────────────────────

def run_monitor(interval_secs: int = 60):
    logger.info(f"Starting continuous oracle lag monitor (interval={interval_secs}s)")
    cycle = 0
    while True:
        try:
            if cycle % 10 == 0:
                logger.info("Refreshing market data and ESPN scores...")
                active = fetch_active_markets(300)
                games = fetch_all_recent_games(days_back=1)
            opps = find_live_opportunities(active, games)
            if opps:
                print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}] {len(opps)} LIVE OPPORTUNITIES:")
                for opp in opps[:5]:
                    print(f"  {opp['type']} | {opp['market'][:55]} | profit={opp['potential_profit_pct']:.1f}% | conf={opp['confidence']}")
                    with open(OPP_LOG, "a") as f:
                        f.write(json.dumps({**opp, "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
            else:
                logger.info(f"Cycle {cycle}: no live opportunities")
            cycle += 1
            time.sleep(interval_secs)
        except KeyboardInterrupt:
            logger.info("Monitor stopped.")
            break
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            time.sleep(10)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket oracle lag analyzer")
    parser.add_argument("--mode", choices=["history", "live", "monitor", "all"], default="all")
    parser.add_argument("--top", type=int, default=20, help="Number of top slow markets to show")
    parser.add_argument("--resolved", type=int, default=500, help="Number of resolved markets for history analysis")
    parser.add_argument("--monitor-interval", type=int, default=60, help="Monitor scan interval in seconds")
    args = parser.parse_args()

    if args.mode in ("history", "all"):
        logger.info(f"Fetching {args.resolved} resolved markets for lag analysis...")
        resolved = fetch_resolved_markets(args.resolved)
        logger.info(f"Analyzing lag patterns across {len(resolved)} markets...")
        analysis = analyze_historical_lags(resolved, top_n=args.top)
        print_lag_report(analysis, args.top)
        print_strategic_summary(analysis)

    if args.mode in ("live", "all"):
        logger.info("Fetching active markets...")
        active = fetch_active_markets(300)
        logger.info(f"Fetching ESPN games (last 2 days across {len(ESPN_LEAGUES)} leagues)...")
        games = fetch_all_recent_games(days_back=2)
        logger.info(f"ESPN returned {len(games)} games total. Scanning for oracle lag opportunities...")
        opps = find_live_opportunities(active, games)
        print_opportunities(opps)
        if opps:
            report_path = Path("./data/poly/live_opportunities.json")
            with open(report_path, "w") as f:
                json.dump(opps, f, indent=2, default=str)
            logger.info(f"Opportunities saved → {report_path}")

    if args.mode == "monitor":
        run_monitor(args.monitor_interval)

if __name__ == "__main__":
    main()
