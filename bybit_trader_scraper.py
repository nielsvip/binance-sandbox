#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""Bybit Copy Trading scraper: discover top traders, pull trade histories.

Auth: Bybit V5 HMAC-SHA256 (reads .env.bybit for BYBIT_API_KEY + BYBIT_API_SECRET).
Falls back to public endpoints + web scraping if no credentials.
Outputs unified CSV compatible with trader_deep_analyzer.py pipeline.
"""
import base64
import csv
import hashlib
import hmac
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR / "bybit_traders"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("bybit_scraper")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]
RATE_DELAY = 0.5
DISCOVERED_FILE = DATA_DIR / "discovered_traders.json"
API_BASE = "https://api.bybit.com"


def load_credentials() -> Dict[str, str]:
    """Load Bybit API credentials from .env.bybit or environment."""
    env_path = BASE_PATH / ".env.bybit"
    creds = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    creds.setdefault("BYBIT_API_KEY", os.environ.get("BYBIT_API_KEY", ""))
    creds.setdefault("BYBIT_API_SECRET", os.environ.get("BYBIT_API_SECRET", ""))
    if not creds["BYBIT_API_KEY"] or not creds["BYBIT_API_SECRET"]:
        logger.info("No Bybit credentials — will use public endpoints only")
        return {}
    return creds


def _sign_bybit(ts: str, api_key: str, recv_window: str, query_string: str, secret: str) -> str:
    """Bybit V5 HMAC-SHA256 signature: timestamp + api_key + recv_window + queryString"""
    param_str = ts + api_key + recv_window + query_string
    return hmac.new(secret.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256).hexdigest()


def _auth_headers(query_string: str, creds: Dict[str, str]) -> Dict[str, str]:
    ts = str(int(time.time() * 1000))
    recv_window = "20000"
    sign = _sign_bybit(ts, creds["BYBIT_API_KEY"], recv_window, query_string, creds["BYBIT_API_SECRET"])
    return {"X-BAPI-API-KEY": creds["BYBIT_API_KEY"], "X-BAPI-SIGN": sign, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv_window, "Content-Type": "application/json"}


def _auth_get(path: str, params: Dict = None, creds: Dict = None) -> Optional[Dict]:
    """Authenticated GET request to Bybit V5 API."""
    if not creds:
        return None
    query_string = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    url = API_BASE + path
    if query_string:
        url += "?" + query_string
    headers = _auth_headers(query_string, creds)
    headers["User-Agent"] = random.choice(USER_AGENTS)
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("retCode") == 0:
                return data.get("result", {})
            logger.debug(f"Bybit API error: {data.get('retMsg', 'unknown')}")
        return None
    except Exception as e:
        logger.debug(f"Bybit auth request failed: {e}")
        return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": random.choice(USER_AGENTS), "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"})
    return s


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — DISCOVER TOP TRADERS
# ═══════════════════════════════════════════════════════════════════

def _scrape_bybit_web_leaderboard(sess: requests.Session, limit: int = 50) -> List[Dict]:
    """Scrape Bybit copy-trading leaderboard from the web frontend.
    Bybit's api2 is Cloudflare-protected, so we scrape the web page."""
    traders = []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("beautifulsoup4 not installed")
        return []
    urls = [
        "https://www.bybit.com/copyTrade/tradeLeader",
        "https://www.bybit.com/en/copy-trading/leaders",
    ]
    for url in urls:
        try:
            r = sess.get(url, timeout=20, headers={"User-Agent": random.choice(USER_AGENTS), "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"})
            if r.status_code != 200:
                continue
            # Try to extract JSON data embedded in page (Next.js __NEXT_DATA__ or similar)
            soup = BeautifulSoup(r.text, "html.parser")
            scripts = soup.find_all("script")
            for script in scripts:
                text = script.string or ""
                if "leaderMark" in text or "nickName" in text or "winRate" in text:
                    # Found embedded trader data — extract JSON
                    import re as _re
                    json_matches = _re.findall(r'\{[^{}]*"(?:leaderMark|nickName|winRate)"[^{}]*\}', text)
                    for jm in json_matches:
                        try:
                            data = json.loads(jm)
                            uid = data.get("leaderMark", data.get("uid", ""))
                            if uid:
                                traders.append({"uid": uid, "nickname": data.get("nickName", data.get("nickname", "")), "roi7d": float(data.get("roiRate7D", data.get("roi", 0))), "pnl7d": float(data.get("pnl7D", data.get("pnl", 0))), "winRate": float(data.get("winRate", 0)), "followerCount": int(data.get("followerCount", 0)), "source": "bybit_web"})
                        except Exception:
                            continue
                # Also try extracting from __NEXT_DATA__
                if "__NEXT_DATA__" in text:
                    try:
                        next_data = json.loads(text.split("__NEXT_DATA__")[1].split("=", 1)[1].rsplit("</script>", 1)[0].strip().rstrip(";"))
                        # Walk the JSON tree looking for trader arrays
                        def _find_traders(obj, depth=0):
                            if depth > 5:
                                return
                            if isinstance(obj, list):
                                for item in obj:
                                    if isinstance(item, dict) and ("leaderMark" in item or "nickName" in item):
                                        uid = item.get("leaderMark", item.get("uid", ""))
                                        if uid:
                                            traders.append({"uid": uid, "nickname": item.get("nickName", ""), "roi7d": float(item.get("roiRate7D", 0)), "pnl7d": float(item.get("pnl7D", 0)), "winRate": float(item.get("winRate", 0)), "followerCount": int(item.get("followerCount", 0)), "source": "bybit_nextdata"})
                                    elif isinstance(item, (dict, list)):
                                        _find_traders(item, depth + 1)
                            elif isinstance(obj, dict):
                                for v in obj.values():
                                    if isinstance(v, (dict, list)):
                                        _find_traders(v, depth + 1)
                        _find_traders(next_data)
                    except Exception:
                        pass
            if traders:
                logger.info(f"  Web scrape: found {len(traders)} traders from {url}")
                break
        except Exception as e:
            logger.warning(f"  Web scrape failed for {url}: {e}")
    return traders[:limit]


def discover_traders(limit: int = 100) -> List[Dict]:
    """Discover top copy-trading leaders from Bybit via multiple methods."""
    logger.info(f"Discovering top {limit} Bybit traders...")
    all_traders = {}
    sess = _session()
    creds = load_credentials()
    # Method 0: Authenticated V5 copy-trading endpoints (most reliable)
    if creds:
        logger.info("  Trying authenticated V5 copy-trading API...")
        for endpoint, params in [
            ("/v5/copy-trading/get-leading-masters", {"sortBy": "TOTAL_PNL", "limit": "50"}),
            ("/v5/copy-trading/get-leading-masters", {"sortBy": "WIN_RATE", "limit": "50"}),
            ("/v5/copy-trading/get-leading-masters", {"sortBy": "FOLLOWERS", "limit": "50"}),
        ]:
            result = _auth_get(endpoint, params, creds)
            if result:
                leaders = result.get("list", result.get("leaderDetails", []))
                for t in leaders:
                    uid = t.get("leaderId", t.get("leaderMark", ""))
                    if uid and uid not in all_traders:
                        all_traders[uid] = {"uid": uid, "nickname": t.get("leaderName", t.get("nickName", "")), "roi7d": float(t.get("roi7D", t.get("roiRate7D", t.get("roi", 0)))), "pnl7d": float(t.get("pnl7D", t.get("totalPnl", 0))), "winRate": float(t.get("winRate", 0)), "followerCount": int(t.get("currentFollowers", t.get("followerCount", 0))), "source": "bybit_v5_auth"}
                logger.info(f"  V5 auth: found {len(leaders)} traders (total: {len(all_traders)})")
                time.sleep(RATE_DELAY)
    # Method 1: Try public API endpoints (may be blocked by Cloudflare)
    if len(all_traders) < 10:
        api_endpoints = [
            ("api2 beehive", "https://api2.bybit.com/fapi/beehive/public/v1/common/leader-list", {"timeRange": "DATA_RANGE_SEVEN_DAY", "dataType": "DATA_TYPE_WIN_RATE", "page": 1, "pageSize": 20}),
            ("api v5 copy", "https://api.bybit.com/v5/copy-trading/get-leader-boards", {"sortBy": "TOTAL_PNL", "pageSize": "50"}),
        ]
    else:
        api_endpoints = []
    for name, url, params in api_endpoints:
        try:
            r = sess.get(url, params=params, timeout=10)
            if r.status_code != 200 or "<HTML>" in r.text[:50]:
                logger.info(f"  {name}: blocked (status {r.status_code})")
                continue
            data = r.json()
            traders = data.get("result", {}).get("leaderDetails", data.get("result", {}).get("list", []))
            for t in traders:
                uid = t.get("leaderMark", t.get("leaderId", ""))
                if uid and uid not in all_traders:
                    all_traders[uid] = {"uid": uid, "nickname": t.get("nickName", t.get("leaderName", "")), "roi7d": float(t.get("roiRate7D", t.get("roi", 0))), "pnl7d": float(t.get("pnl7D", t.get("totalPnl", 0))), "winRate": float(t.get("winRate", 0)), "followerCount": int(t.get("followerCount", t.get("currentFollowers", 0))), "source": f"bybit_{name}"}
            if traders:
                logger.info(f"  {name}: found {len(traders)} traders")
            time.sleep(RATE_DELAY)
        except Exception as e:
            logger.info(f"  {name}: failed ({e})")
    # Method 2: Web scraping fallback
    if len(all_traders) < 10:
        logger.info("  API endpoints blocked — trying web scrape...")
        web_traders = _scrape_bybit_web_leaderboard(sess, limit)
        for t in web_traders:
            uid = t.get("uid", "")
            if uid and uid not in all_traders:
                all_traders[uid] = t
    # Method 3: Use cached traders if we have them
    if not all_traders and DISCOVERED_FILE.exists():
        try:
            cached = json.loads(DISCOVERED_FILE.read_text())
            logger.info(f"  Using {len(cached)} cached traders from previous discovery")
            return cached[:limit]
        except Exception:
            pass
    traders_list = sorted(all_traders.values(), key=lambda x: x.get("pnl7d", 0), reverse=True)[:limit]
    if traders_list:
        with open(DISCOVERED_FILE, "w") as f:
            json.dump(traders_list, f, indent=2)
    logger.info(f"Discovered {len(traders_list)} unique Bybit traders")
    return traders_list


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — PULL TRADE HISTORY
# ═══════════════════════════════════════════════════════════════════

def get_trader_positions(sess: requests.Session, uid: str) -> List[Dict]:
    """Get current open positions for a trader."""
    try:
        url = f"https://api2.bybit.com/fapi/beehive/public/v1/common/leader-position"
        r = sess.get(url, params={"leaderMark": uid}, timeout=10)
        data = r.json()
        return data.get("result", {}).get("data", [])
    except Exception:
        return []


def get_trader_history(sess: requests.Session, uid: str, limit: int = 200, creds: Dict = None) -> List[Dict]:
    """Get closed trade history for a trader."""
    trades = []
    # Method 1: Authenticated V5 endpoint (most reliable)
    if creds:
        try:
            cursor = ""
            pages = 0
            while pages < 10:
                params = {"leaderId": uid, "limit": "50"}
                if cursor:
                    params["cursor"] = cursor
                result = _auth_get("/v5/copy-trading/get-leader-closed-pnl", params, creds)
                if not result:
                    break
                batch = result.get("list", [])
                if not batch:
                    break
                trades.extend(batch)
                cursor = result.get("nextPageCursor", "")
                pages += 1
                if not cursor or len(trades) >= limit:
                    break
                time.sleep(RATE_DELAY)
        except Exception as e:
            logger.debug(f"V5 auth history failed for {uid}: {e}")
    # Method 2: Beehive public (may be blocked)
    if not trades:
        try:
            url = "https://api2.bybit.com/fapi/beehive/public/v1/common/leader-history"
            cursor = ""
            pages = 0
            while pages < 10:
                params = {"leaderMark": uid, "pageSize": 50}
                if cursor:
                    params["cursor"] = cursor
                r = sess.get(url, params=params, timeout=15)
                if r.status_code != 200 or "<HTML>" in r.text[:50]:
                    break
                data = r.json()
                result = data.get("result", {})
                batch = result.get("data", [])
                if not batch:
                    break
                trades.extend(batch)
                cursor = result.get("nextPageCursor", "")
                pages += 1
                if not cursor or len(trades) >= limit:
                    break
                time.sleep(RATE_DELAY)
        except Exception as e:
            logger.debug(f"Beehive history failed for {uid}: {e}")
    return trades[:limit]


def _parse_bybit_trade(raw: Dict, trader_id: str) -> Optional[Dict]:
    """Normalize a Bybit trade record to our unified CSV format."""
    try:
        symbol = raw.get("symbol", raw.get("pair", "")).replace("/", "").upper()
        if not symbol:
            return None
        side = raw.get("side", raw.get("direction", "")).upper()
        if "BUY" in side or "LONG" in side:
            side = "LONG"
        elif "SELL" in side or "SHORT" in side:
            side = "SHORT"
        entry_price = float(raw.get("entryPrice", raw.get("avgEntryPrice", raw.get("openPrice", 0))))
        exit_price = float(raw.get("exitPrice", raw.get("avgExitPrice", raw.get("closePrice", 0))))
        pnl = float(raw.get("closedPnl", raw.get("pnl", raw.get("totalPnl", 0))))
        pnl_pct = float(raw.get("pnlRate", raw.get("roe", 0)))
        if abs(pnl_pct) > 0 and abs(pnl_pct) < 1:
            pnl_pct *= 100
        leverage = float(raw.get("leverage", raw.get("lever", 1)))
        size_usd = float(raw.get("positionValue", raw.get("orderValue", raw.get("qty", 0))))
        # Parse timestamps
        entry_time = raw.get("createdAt", raw.get("openTime", raw.get("createdTime", "")))
        exit_time = raw.get("updatedAt", raw.get("closeTime", raw.get("updatedTime", "")))
        if isinstance(entry_time, (int, float)):
            if entry_time > 1e12:
                entry_time = datetime.fromtimestamp(entry_time / 1000, tz=timezone.utc).isoformat()
            else:
                entry_time = datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat()
        if isinstance(exit_time, (int, float)):
            if exit_time > 1e12:
                exit_time = datetime.fromtimestamp(exit_time / 1000, tz=timezone.utc).isoformat()
            else:
                exit_time = datetime.fromtimestamp(exit_time, tz=timezone.utc).isoformat()
        return {"trader_id": trader_id, "symbol": symbol, "side": side, "entry_price": entry_price, "exit_price": exit_price, "entry_time": str(entry_time), "exit_time": str(exit_time), "pnl": round(pnl, 4), "pnl_pct": round(pnl_pct, 4), "leverage": leverage, "position_size_usd": round(size_usd, 2)}
    except Exception as e:
        logger.debug(f"Parse error: {e} — {raw}")
        return None


def scrape_all(limit_traders: int = 100) -> Path:
    """Full scrape: discover traders → pull histories → export CSV."""
    traders = discover_traders(limit_traders)
    if not traders:
        # Fallback to cached
        if DISCOVERED_FILE.exists():
            traders = json.loads(DISCOVERED_FILE.read_text())
            logger.info(f"Using {len(traders)} cached traders")
        else:
            logger.error("No traders discovered and no cache")
            return DATA_DIR / "empty.csv"
    sess = _session()
    creds = load_credentials()
    all_trades = []
    for i, t in enumerate(traders):
        uid = t["uid"]
        nick = t.get("nickname", uid[:12])
        logger.info(f"  [{i+1}/{len(traders)}] {nick} — pulling history...")
        history = get_trader_history(sess, uid, creds=creds)
        if not history:
            logger.info(f"    No history for {nick}")
            continue
        parsed = 0
        for raw in history:
            trade = _parse_bybit_trade(raw, uid)
            if trade:
                all_trades.append(trade)
                parsed += 1
        logger.info(f"    {parsed} trades parsed")
        time.sleep(RATE_DELAY)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = DATA_DIR / f"{today}_trades.csv"
    if all_trades:
        fieldnames = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd"]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_trades)
        logger.info(f"Exported {len(all_trades)} trades from {len(traders)} traders → {csv_path}")
    else:
        logger.warning("No trades collected")
    return csv_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bybit Copy Trading Scraper")
    parser.add_argument("--full", action="store_true", help="Full scrape cycle")
    parser.add_argument("--discover", action="store_true", help="Discover traders only")
    parser.add_argument("--limit", type=int, default=100, help="Max traders to scrape")
    args = parser.parse_args()
    if args.discover:
        discover_traders(args.limit)
    else:
        scrape_all(args.limit)
