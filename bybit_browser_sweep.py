#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""Bybit Copy Trading sweeper — uses headed Playwright browser.

Launches a visible Chrome window, navigates Bybit's copy trading pages,
intercepts all API responses, extracts trader + trade data.

The /x-api/ proxy on www.bybit.com bypasses Akamai CDN.
Browser must stay open during the sweep.

Usage:
    python3 bybit_browser_sweep.py              # Full sweep (leaders + trade histories)
    python3 bybit_browser_sweep.py --leaders     # Leaders only (fast)
    python3 bybit_browser_sweep.py --limit 30    # Limit number of traders
"""
import argparse
import csv
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
DATA_DIR = config.DATA_DIR / "bybit_traders"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("bybit_sweep")

LEADERS_FILE = DATA_DIR / "discovered_traders_pw.json"


def parse_metric_value(raw: str) -> float:
    """Parse Bybit metric strings like '+10.26%', '30,928.18', '14.20 : 0'."""
    if not raw:
        return 0.0
    raw = raw.strip().replace(",", "").replace("+", "")
    if ":" in raw:
        parts = raw.split(":")
        try:
            return float(parts[0].strip())
        except ValueError:
            return 0.0
    raw = raw.replace("%", "")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def extract_leaders_from_responses(responses: List[Dict]) -> List[Dict]:
    """Extract trader data from intercepted API responses."""
    all_traders = {}
    for resp in responses:
        data = resp.get("json", {})
        if not isinstance(data, dict):
            continue
        result = data.get("result", {})
        # dynamic-leader-list → leaderDetails[]
        for leader in result.get("leaderDetails", []):
            uid = leader.get("leaderMark", "")
            if not uid:
                continue
            metrics = leader.get("metricValues", [])
            all_traders[uid] = {"uid": uid, "nickname": leader.get("nickName", ""), "user_id": leader.get("leaderUserId", ""), "roi": parse_metric_value(metrics[0] if len(metrics) > 0 else ""), "drawdown": parse_metric_value(metrics[1] if len(metrics) > 1 else ""), "follower_profit": parse_metric_value(metrics[2] if len(metrics) > 2 else ""), "winRate": parse_metric_value(metrics[3] if len(metrics) > 3 else ""), "pnl_ratio": parse_metric_value(metrics[4] if len(metrics) > 4 else ""), "sharpe": parse_metric_value(metrics[5] if len(metrics) > 5 else ""), "followers": int(leader.get("currentFollowerCount", 0)), "max_followers": int(leader.get("maxFollowerCount", 0)), "level": leader.get("leaderLevel", ""), "source": "dynamic-leader-list"}
        # trader-leaderboard → typeTraderList[].traderList[]
        for group in result.get("typeTraderList", []):
            rank_type = group.get("rankingType", "")
            for trader in group.get("traderList", []):
                uid = trader.get("leaderMark", "")
                if not uid or uid in all_traders:
                    continue
                all_traders[uid] = {"uid": uid, "nickname": trader.get("leaderNickname", ""), "user_id": trader.get("leaderUserId", ""), "pnl": float(trader.get("traderPnlData", 0)), "follower_profit": float(trader.get("followersProfitsData", 0)), "aum": float(trader.get("aum", 0)), "ranking": rank_type, "source": "leaderboard"}
        # recommend-leaders → leaderRecommendInfoList[].leaderRecommendDetailList[]
        for group in result.get("leaderRecommendInfoList", []):
            tag = group.get("leaderTag", "")
            for detail in group.get("leaderRecommendDetailList", []):
                # Metrics are in mIL array
                mil = detail.get("mIL", [])
                metrics_dict = {}
                for m in mil:
                    key = m.get("mK", "")
                    val = m.get("mV", "0")
                    exp = m.get("mVE", "")
                    try:
                        fval = float(val)
                        if exp == "e4":
                            fval /= 10000
                        elif exp == "e8":
                            fval /= 100000000
                        metrics_dict[key] = fval
                    except ValueError:
                        pass
                uid = detail.get("leaderMark", detail.get("lM", ""))
                nick = detail.get("nickName", detail.get("nN", ""))
                if not uid and not nick:
                    continue
                if uid not in all_traders:
                    all_traders[uid or nick] = {"uid": uid, "nickname": nick, "roi": metrics_dict.get("thirty_day_roi", 0), "drawdown": metrics_dict.get("thirty_day_draw_down", 0), "sharpe": metrics_dict.get("thirty_day_sharpe_ratio", 0), "winRate": metrics_dict.get("thirty_day_win_rate", 0), "tag": tag, "source": "recommended"}
    return list(all_traders.values())


def extract_trades_from_responses(responses: List[Dict], trader_uid: str = "") -> List[Dict]:
    """Extract trade data from intercepted API responses for a trader profile page."""
    trades = []
    for resp in responses:
        data = resp.get("json", {})
        if not isinstance(data, dict):
            continue
        result = data.get("result", {})
        # Look for trade lists in various formats
        for key in ["data", "list", "tradeList", "orderList", "positionList"]:
            items = result.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                symbol = item.get("symbol", item.get("pair", ""))
                if not symbol:
                    continue
                side = item.get("side", item.get("direction", "")).upper()
                if "BUY" in side or "LONG" in side:
                    side = "LONG"
                elif "SELL" in side or "SHORT" in side:
                    side = "SHORT"
                else:
                    side = side or "UNKNOWN"
                entry = float(item.get("entryPrice", item.get("avgEntryPrice", item.get("openPrice", 0))))
                exit_p = float(item.get("exitPrice", item.get("avgExitPrice", item.get("closePrice", 0))))
                pnl = float(item.get("closedPnl", item.get("pnl", item.get("totalPnl", 0))))
                pnl_pct = float(item.get("pnlRate", item.get("roe", item.get("profitRate", 0))))
                if abs(pnl_pct) > 0 and abs(pnl_pct) < 1:
                    pnl_pct *= 100
                leverage = float(item.get("leverage", item.get("lever", 1)))
                size = float(item.get("positionValue", item.get("orderValue", item.get("qty", 0))))
                entry_time = item.get("createdAt", item.get("openTime", item.get("createdTime", "")))
                exit_time = item.get("updatedAt", item.get("closeTime", item.get("updatedTime", "")))
                for ts_field in [entry_time, exit_time]:
                    if isinstance(ts_field, (int, float)) and ts_field > 1e12:
                        ts_field = datetime.fromtimestamp(ts_field / 1000, tz=timezone.utc).isoformat()
                trades.append({"trader_id": trader_uid, "symbol": symbol, "side": side, "entry_price": entry, "exit_price": exit_p, "entry_time": str(entry_time), "exit_time": str(exit_time), "pnl": round(pnl, 4), "pnl_pct": round(pnl_pct, 4), "leverage": leverage, "position_size_usd": round(size, 2)})
    return trades


def run_sweep(leaders_only: bool = False, limit: int = 50):
    """Run full Bybit sweep using headed browser."""
    from playwright.sync_api import sync_playwright
    logger.info("Launching Bybit browser sweep...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900}, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = ctx.new_page()
        # Collect all API responses
        api_responses = []
        def on_response(resp):
            if "/x-api/" in resp.url and resp.status == 200:
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        api_responses.append({"url": resp.url, "json": resp.json()})
                except Exception:
                    pass
        page.on("response", on_response)
        # Phase 1: Leaderboard
        logger.info("Phase 1: Scraping leaderboard...")
        page.goto("https://www.bybit.com/copyTrade/tradeLeader", wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
        # Dismiss popups
        for sel in ['button:has-text("Accept")', 'button:has-text("OK")', 'button:has-text("Got it")', '[class*="close-icon"]']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    time.sleep(0.5)
            except Exception:
                pass
        # Scroll to load more traders
        for i in range(8):
            page.evaluate("window.scrollBy(0, 600)")
            time.sleep(1.5)
        # Try clicking "Show More" or pagination
        for sel in ['button:has-text("Show More")', 'button:has-text("Load More")', '[class*="load-more"]', '[class*="show-more"]']:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    time.sleep(3)
            except Exception:
                pass
        leaders = extract_leaders_from_responses(api_responses)
        logger.info(f"Found {len(leaders)} traders from leaderboard")
        for t in sorted(leaders, key=lambda x: x.get("roi", 0), reverse=True)[:10]:
            logger.info(f"  {t.get('nickname', '?'):<25} ROI={t.get('roi', 0):>8.2f}% WR={t.get('winRate', 0):>5.1f}% followers={t.get('followers', '?')} Sharpe={t.get('sharpe', '?')}")
        with open(LEADERS_FILE, "w") as f:
            json.dump(leaders, f, indent=2)
        logger.info(f"Saved {len(leaders)} traders to {LEADERS_FILE.name}")
        if leaders_only:
            browser.close()
            return
        # Phase 2: Scrape individual trader profiles for trade history
        logger.info(f"\nPhase 2: Scraping trade histories for top {min(len(leaders), limit)} traders...")
        all_trades = []
        traders_to_scrape = sorted(leaders, key=lambda x: x.get("roi", 0), reverse=True)[:limit]
        for i, trader in enumerate(traders_to_scrape):
            uid = trader.get("uid", "")
            nick = trader.get("nickname", uid[:12])
            if not uid:
                continue
            logger.info(f"  [{i+1}/{len(traders_to_scrape)}] {nick}...")
            # Clear previous responses for this trader
            pre_count = len(api_responses)
            # Navigate to trader profile
            profile_urls = [f"https://www.bybit.com/copyTrade/trade-leader/detail/{uid}"]
            for url in profile_urls:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    time.sleep(3)
                    # Click on trade history tab
                    for tab in ["Trade History", "History", "Closed P&L", "Orders"]:
                        try:
                            el = page.query_selector(f'text="{tab}"')
                            if el and el.is_visible():
                                el.click()
                                time.sleep(2)
                                break
                        except Exception:
                            pass
                    # Scroll to load trade data
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 400)")
                        time.sleep(1)
                    break
                except Exception as e:
                    logger.debug(f"Profile failed: {e}")
            new_responses = api_responses[pre_count:]
            trades = extract_trades_from_responses(new_responses, uid)
            if trades:
                all_trades.extend(trades)
                logger.info(f"    {len(trades)} trades extracted")
            else:
                logger.info(f"    No trade data in API responses")
            time.sleep(1)
        # Export all trades
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        csv_path = DATA_DIR / f"{today}_trades.csv"
        if all_trades:
            fieldnames = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd"]
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_trades)
            logger.info(f"Exported {len(all_trades)} trades → {csv_path}")
        # Save all raw API responses for future analysis
        raw_path = DATA_DIR / f"raw_api_{today}.json"
        with open(raw_path, "w") as f:
            json.dump([{"url": r["url"]} for r in api_responses], f, indent=2)
        logger.info(f"\nSweep complete: {len(leaders)} traders, {len(all_trades)} trades")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bybit Browser Sweep")
    parser.add_argument("--leaders", action="store_true", help="Leaders only (no trade histories)")
    parser.add_argument("--limit", type=int, default=30, help="Max traders to scrape histories for")
    args = parser.parse_args()
    run_sweep(leaders_only=args.leaders, limit=args.limit)
