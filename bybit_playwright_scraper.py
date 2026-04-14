#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""Bybit Copy Trading scraper via headless browser (Playwright).

Bypasses Cloudflare by running a real Chromium browser.
Scrapes the copy trading leaderboard and individual trader profiles.
Outputs unified CSV compatible with trader_deep_analyzer.py pipeline.

Usage:
    python3 bybit_playwright_scraper.py                    # Full scrape
    python3 bybit_playwright_scraper.py --leaders-only     # Just discover leaders
    python3 bybit_playwright_scraper.py --trader NICKNAME   # Scrape one trader
    python3 bybit_playwright_scraper.py --limit 20          # Limit traders
"""
import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR / "bybit_traders"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("bybit_pw")

LEADERS_FILE = DATA_DIR / "discovered_traders_pw.json"
LEADERBOARD_URL = "https://www.bybit.com/copyTrade/tradeLeader"


def scrape_leaderboard(limit: int = 50) -> List[Dict]:
    """Scrape the Bybit copy-trading leaderboard using Playwright."""
    from playwright.sync_api import sync_playwright
    traders = []
    logger.info(f"Launching headless browser for Bybit leaderboard...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = ctx.new_page()
        try:
            page.goto(LEADERBOARD_URL, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            # Try to intercept API calls made by the page
            api_data = []
            def handle_response(response):
                url = response.url
                if "leader" in url.lower() and ("beehive" in url.lower() or "copy" in url.lower()):
                    try:
                        body = response.json()
                        api_data.append({"url": url, "data": body})
                    except Exception:
                        pass
            page.on("response", handle_response)
            # Scroll to trigger lazy loading
            for i in range(5):
                page.evaluate("window.scrollBy(0, 800)")
                time.sleep(1.5)
            # Try to extract from intercepted API data
            for item in api_data:
                data = item.get("data", {})
                result = data.get("result", data)
                if isinstance(result, dict):
                    leaders = result.get("leaderDetails", result.get("list", result.get("data", [])))
                    if isinstance(leaders, list):
                        for t in leaders:
                            uid = t.get("leaderMark", t.get("leaderId", t.get("uid", "")))
                            if uid:
                                traders.append({"uid": uid, "nickname": t.get("nickName", t.get("leaderName", "")), "roi7d": float(t.get("roiRate7D", t.get("roi7D", t.get("roi", 0)))), "pnl7d": float(t.get("pnl7D", t.get("totalPnl", 0))), "winRate": float(t.get("winRate", 0)), "followerCount": int(t.get("followerCount", t.get("currentFollowers", 0))), "source": "bybit_pw_api"})
            # If API interception didn't work, parse the DOM
            if not traders:
                logger.info("No API data intercepted, parsing DOM...")
                # Get all trader cards on the page
                cards = page.query_selector_all("[class*='leader'], [class*='trader'], [class*='card'], [data-testid*='leader']")
                if not cards:
                    cards = page.query_selector_all("a[href*='copyTrade']")
                for card in cards[:limit]:
                    try:
                        text = card.inner_text()
                        href = card.get_attribute("href") or ""
                        # Extract nickname and stats from card text
                        lines = [l.strip() for l in text.split("\n") if l.strip()]
                        if len(lines) >= 2:
                            nickname = lines[0]
                            # Try to find ROI/PnL/WR numbers
                            roi = 0
                            wr = 0
                            for line in lines:
                                roi_match = re.search(r'([+-]?\d+\.?\d*)%', line)
                                if roi_match and roi == 0:
                                    roi = float(roi_match.group(1))
                                wr_match = re.search(r'(\d+\.?\d*)%\s*(?:win|WR)', line, re.IGNORECASE)
                                if wr_match:
                                    wr = float(wr_match.group(1))
                            # Extract UID from href
                            uid_match = re.search(r'/([A-Za-z0-9]+)/?$', href)
                            uid = uid_match.group(1) if uid_match else nickname
                            traders.append({"uid": uid, "nickname": nickname, "roi7d": roi, "pnl7d": 0, "winRate": wr, "followerCount": 0, "profile_url": href, "source": "bybit_pw_dom"})
                    except Exception:
                        continue
            # Also try getting the full page content and extracting JSON
            if not traders:
                logger.info("DOM parsing found nothing, trying page content JSON extraction...")
                content = page.content()
                # Look for embedded JSON with trader data
                json_blocks = re.findall(r'\{[^{}]*"(?:nickName|leaderMark|winRate)"[^{}]*\}', content)
                for jb in json_blocks[:limit]:
                    try:
                        data = json.loads(jb)
                        uid = data.get("leaderMark", data.get("uid", ""))
                        if uid:
                            traders.append({"uid": uid, "nickname": data.get("nickName", ""), "roi7d": float(data.get("roiRate7D", 0)), "winRate": float(data.get("winRate", 0)), "source": "bybit_pw_json"})
                    except Exception:
                        continue
            logger.info(f"Found {len(traders)} traders from Bybit leaderboard")
        except Exception as e:
            logger.error(f"Leaderboard scrape failed: {e}")
        finally:
            browser.close()
    # Dedupe
    seen = set()
    unique = []
    for t in traders:
        uid = t.get("uid", "")
        if uid and uid not in seen:
            seen.add(uid)
            unique.append(t)
    if unique:
        with open(LEADERS_FILE, "w") as f:
            json.dump(unique, f, indent=2)
    return unique[:limit]


def scrape_trader_profile(page, uid: str, nickname: str = "") -> List[Dict]:
    """Scrape a single trader's trade history from their profile page."""
    trades = []
    urls_to_try = [
        f"https://www.bybit.com/copyTrade/tradeLeader/detail/{uid}",
        f"https://www.bybit.com/copyTrade/trade-leader/{uid}",
    ]
    for url in urls_to_try:
        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
            time.sleep(2)
            # Click on "Trade History" or "Closed" tab if visible
            for tab_text in ["Trade History", "History", "Closed", "PnL"]:
                tab = page.query_selector(f"text={tab_text}")
                if tab:
                    tab.click()
                    time.sleep(2)
                    break
            # Intercept API calls for trade data
            # Also try parsing the trade table from DOM
            rows = page.query_selector_all("table tr, [class*='trade-row'], [class*='history-item'], [class*='order-item']")
            for row in rows:
                try:
                    text = row.inner_text()
                    # Parse trade data from row text
                    # Common format: SYMBOL SIDE ENTRY EXIT PNL TIME
                    parts = [p.strip() for p in text.split("\t") if p.strip()]
                    if not parts:
                        parts = [p.strip() for p in text.split("\n") if p.strip()]
                    if len(parts) >= 4:
                        symbol = ""
                        side = ""
                        entry_price = 0
                        exit_price = 0
                        pnl_pct = 0
                        for part in parts:
                            if "USDT" in part.upper():
                                symbol = part.replace("/", "").strip()
                            elif part.upper() in ("LONG", "SHORT", "BUY", "SELL"):
                                side = "LONG" if part.upper() in ("LONG", "BUY") else "SHORT"
                            pct_match = re.search(r'([+-]?\d+\.?\d*)%', part)
                            if pct_match:
                                pnl_pct = float(pct_match.group(1))
                            price_match = re.search(r'^\$?(\d+\.?\d+)$', part)
                            if price_match:
                                price = float(price_match.group(1))
                                if entry_price == 0:
                                    entry_price = price
                                else:
                                    exit_price = price
                        if symbol and side:
                            trades.append({"trader_id": uid, "nickname": nickname, "symbol": symbol, "side": side, "entry_price": entry_price, "exit_price": exit_price, "pnl_pct": pnl_pct, "leverage": 0})
                except Exception:
                    continue
            if trades:
                break
        except Exception as e:
            logger.debug(f"Profile scrape failed for {uid} at {url}: {e}")
    return trades


def scrape_all(limit: int = 50) -> Path:
    """Full scrape: leaderboard → trader profiles → unified CSV."""
    from playwright.sync_api import sync_playwright
    traders = scrape_leaderboard(limit)
    if not traders:
        logger.warning("No traders found on leaderboard")
        if LEADERS_FILE.exists():
            traders = json.loads(LEADERS_FILE.read_text())
            logger.info(f"Using {len(traders)} cached traders")
    if not traders:
        return DATA_DIR / "empty.csv"
    all_trades = []
    logger.info(f"Scraping trade history for {len(traders)} traders...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = ctx.new_page()
        for i, t in enumerate(traders[:limit]):
            uid = t.get("uid", "")
            nick = t.get("nickname", uid[:12])
            logger.info(f"  [{i+1}/{min(len(traders), limit)}] {nick}...")
            trades = scrape_trader_profile(page, uid, nick)
            all_trades.extend(trades)
            logger.info(f"    {len(trades)} trades found")
            time.sleep(1)
        browser.close()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = DATA_DIR / f"{today}_trades.csv"
    if all_trades:
        fieldnames = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd"]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for t in all_trades:
                t.setdefault("entry_time", "")
                t.setdefault("exit_time", "")
                t.setdefault("pnl", 0)
                t.setdefault("position_size_usd", 0)
                writer.writerow(t)
        logger.info(f"Exported {len(all_trades)} trades → {csv_path}")
    else:
        logger.warning("No trades scraped")
    return csv_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bybit Playwright Scraper")
    parser.add_argument("--leaders-only", action="store_true", help="Just discover leaders")
    parser.add_argument("--limit", type=int, default=50, help="Max traders")
    args = parser.parse_args()
    if args.leaders_only:
        traders = scrape_leaderboard(args.limit)
        for t in traders[:10]:
            print(f"  {t.get('nickname', '?'):<20} ROI={t.get('roi7d', 0):>7.1f}% WR={t.get('winRate', 0):>5.1f}% followers={t.get('followerCount', 0)}")
    else:
        scrape_all(args.limit)
