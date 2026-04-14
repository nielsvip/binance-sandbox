#!/usr/bin/env python3
"""Bitget Copy Trading scraper: discover top traders, pull trade histories, export CSV for deep analysis.

Authentication: Bitget V2 API requires HMAC-SHA256 + passphrase.
Required in .env.bitget: BITGET_API_KEY, BITGET_API_SECRET, BITGET_PASSPHRASE

Trader discovery uses the V2 broker endpoint (query-traders) and web scraping fallback.
Trade history uses V2 broker endpoint (query-history-traces) with follower fallback.
All V1 endpoints are decommissioned as of 2025-11-28.
"""
import argparse
import base64
import csv
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR / "bitget_traders"

logger = logging.getLogger("bitget_scraper")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)

API_BASE = "https://api.bitget.com"
RATE_LIMIT_DELAY = 0.22  # ~4.5 req/s to stay under 5/s limit
MAX_PAGE_SIZE = 100

# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — CREDENTIALS
# ═══════════════════════════════════════════════════════════════════

def load_credentials() -> Dict[str, str]:
    env_path = BASE_PATH / ".env.bitget"
    creds = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    creds.setdefault("BITGET_API_KEY", os.environ.get("BITGET_API_KEY", ""))
    creds.setdefault("BITGET_API_SECRET", os.environ.get("BITGET_API_SECRET", ""))
    creds.setdefault("BITGET_PASSPHRASE", os.environ.get("BITGET_PASSPHRASE", ""))
    if not creds["BITGET_API_KEY"] or not creds["BITGET_API_SECRET"]:
        logger.error("Missing BITGET_API_KEY or BITGET_API_SECRET in .env.bitget")
        sys.exit(1)
    if not creds["BITGET_PASSPHRASE"]:
        logger.error("BITGET_PASSPHRASE is REQUIRED by Bitget API (error 40012 without it). Add BITGET_PASSPHRASE=<your_passphrase> to .env.bitget — this is the passphrase you set when creating the API key on bitget.com")
        sys.exit(1)
    return creds

# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — AUTHENTICATION (HMAC-SHA256 + Base64)
# ═══════════════════════════════════════════════════════════════════

def _sign(timestamp: str, method: str, request_path: str, body: str, secret: str) -> str:
    """Signature = Base64(HMAC-SHA256(timestamp + METHOD + requestPath + body, secret))"""
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


def _auth_headers(method: str, request_path: str, body: str, creds: Dict[str, str]) -> Dict[str, str]:
    ts = str(int(time.time() * 1000))
    sign = _sign(ts, method, request_path, body, creds["BITGET_API_SECRET"])
    return {"ACCESS-KEY": creds["BITGET_API_KEY"], "ACCESS-SIGN": sign, "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": creds["BITGET_PASSPHRASE"], "Content-Type": "application/json", "locale": "en-US"}


def _request(method: str, path: str, params: Dict = None, body: Dict = None, creds: Dict = None, authenticated: bool = True) -> Optional[Any]:
    url = API_BASE + path
    if params:
        query_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
        full_path = path + "?" + query_str if query_str else path
        url = API_BASE + full_path
    else:
        full_path = path
    body_str = json.dumps(body) if body else ""
    if authenticated:
        headers = _auth_headers(method, full_path, body_str, creds)
    else:
        headers = {"Content-Type": "application/json", "locale": "en-US"}
    for attempt in range(3):
        try:
            time.sleep(RATE_LIMIT_DELAY)
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=15)
            else:
                resp = requests.post(url, headers=headers, data=body_str, timeout=15)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Rate limited (429), waiting {wait}s...")
                time.sleep(wait)
                ts = str(int(time.time() * 1000))
                if authenticated:
                    headers = _auth_headers(method, full_path, body_str, creds)
                continue
            data = resp.json()
            if data.get("code") == "00000":
                return data.get("data")
            code = data.get("code", "")
            msg = data.get("msg", "")
            if code in ("40012", "40037", "40014", "40015", "40006"):
                logger.error(f"Auth error {path}: code={code} msg={msg} — check API key, secret, passphrase")
                return None
            if code == "30032":
                logger.error(f"V1 decommissioned: {path} — this endpoint no longer exists")
                return None
            logger.error(f"API error {path}: code={code} msg={msg}")
            return None
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on {path}, attempt {attempt+1}/3")
        except Exception as e:
            logger.error(f"Request error {path}: {e}")
            return None
    return None

# ═══════════════════════════════════════════════════════════════════
# SECTION 3 — DISCOVER TOP TRADERS
# ═══════════════════════════════════════════════════════════════════

def discover_traders_api(creds: Dict, top_n: int = 50) -> List[Dict]:
    """Discover traders via V2 broker endpoint — returns top traders with ROI, PnL, win rate, MDD, follower count."""
    all_traders = []
    seen_ids = set()
    page_size = 20  # Bitget max per page
    for _ in range(max(1, (top_n + page_size - 1) // page_size)):
        params = {"pageSize": str(page_size)}
        data = _request("GET", "/api/v2/copy/mix-broker/query-traders", params=params, creds=creds)
        if not data:
            break
        traders_raw = data if isinstance(data, list) else data.get("traderList", data.get("list", []))
        if not traders_raw:
            break
        for t in traders_raw:
            tid = t.get("traderId", "")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                all_traders.append(_normalize_trader(t))
        if len(traders_raw) < page_size:
            break
    logger.info(f"Broker API: discovered {len(all_traders)} traders")
    return all_traders[:top_n]


def discover_traders_follower(creds: Dict) -> List[Dict]:
    """Discover traders via V2 follower endpoint (lists traders available to follow)."""
    data = _request("GET", "/api/v2/copy/mix-follower/query-traders", creds=creds)
    if not data:
        return []
    traders_raw = data if isinstance(data, list) else data.get("traderList", data.get("list", []))
    traders = []
    for t in traders_raw:
        traders.append(_normalize_trader(t))
    logger.info(f"Follower API: discovered {len(traders)} traders")
    return traders


def discover_traders_web(top_n: int = 50) -> List[Dict]:
    """Scrape Bitget leaderboard web page as fallback (no auth needed)."""
    traders = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
    for ranking_type in ["futures-roi", "futures-pnl", "futures-follwers"]:
        try:
            url = f"https://www.bitget.com/copy-trading/leaderboard-ranking/{ranking_type}"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            uid_matches = re.findall(r'"(?:traderUid|uid|traderId|encUid)"\s*:\s*"([^"]+)"', resp.text)
            name_matches = re.findall(r'"(?:nickName|traderNickName|nickname)"\s*:\s*"([^"]+)"', resp.text)
            roi_matches = re.findall(r'"(?:roi|profitRate|winRatio)"\s*:\s*"?([0-9.]+)"?', resp.text)
            for i, uid in enumerate(uid_matches):
                if uid in [t["trader_id"] for t in traders]:
                    continue
                traders.append({"trader_id": uid, "nickname": name_matches[i] if i < len(name_matches) else "", "roi": float(roi_matches[i]) if i < len(roi_matches) else 0.0, "total_pnl": 0.0, "win_rate": 0.0, "follower_count": 0, "total_orders": 0, "source": f"web_{ranking_type}"})
            logger.info(f"Web scrape {ranking_type}: found {len(uid_matches)} UIDs")
        except Exception as e:
            logger.warning(f"Web scrape {ranking_type} failed: {e}")
    seen = set()
    unique = []
    for t in traders:
        if t["trader_id"] not in seen:
            seen.add(t["trader_id"])
            unique.append(t)
    return unique[:top_n]


def discover_traders_from_file() -> List[Dict]:
    """Load previously discovered traders from JSON cache."""
    cache_path = DATA_DIR / "discovered_traders.json"
    if not cache_path.exists():
        return []
    try:
        with open(cache_path) as f:
            traders = json.load(f)
        logger.info(f"Loaded {len(traders)} traders from cache {cache_path}")
        return traders
    except Exception as e:
        logger.warning(f"Failed to load trader cache: {e}")
        return []


def discover_traders(creds: Dict, top_n: int = 50) -> List[Dict]:
    """Multi-strategy trader discovery: API → follower → web scrape → cache."""
    traders = discover_traders_api(creds, top_n)
    if not traders:
        logger.info("Broker API returned no traders, trying follower endpoint...")
        traders = discover_traders_follower(creds)
    if not traders:
        logger.info("API returned no traders, trying web scrape...")
        traders = discover_traders_web(top_n)
    if not traders:
        logger.info("Web scrape returned no traders, trying cache...")
        traders = discover_traders_from_file()
    if not traders:
        logger.error("All discovery methods failed — check credentials or provide trader IDs manually with --trader")
    return traders[:top_n]


def _normalize_trader(t: Dict) -> Dict:
    cols = {}
    for c in t.get("columnList", []):
        key = str(c.get("describe", "")).lower().strip()
        val = str(c.get("value", "0")).replace("$", "").replace(",", "").replace("%", "").strip()
        cols[key] = val
    roi = _safe_float(cols.get("roi")) if cols.get("roi") else _safe_float(t.get("roi", t.get("profitRate", 0)))
    pnl_raw = cols.get("total pnl", "")
    total_pnl = _safe_float(pnl_raw) if pnl_raw else _safe_float(t.get("accProfit", t.get("totalProfit", 0)))
    win_rate = _safe_float(cols.get("win rate")) if cols.get("win rate") else _safe_float(t.get("winRate", t.get("averageWinRate", 0)))
    mdd = _safe_float(cols.get("mdd")) if cols.get("mdd") else _safe_float(t.get("maxCallbackRate", 0))
    aum_raw = cols.get("aum", "")
    aum = _safe_float(aum_raw) if aum_raw else 0.0
    copier_profit_raw = cols.get("copier profit", "")
    copier_profit = _safe_float(copier_profit_raw) if copier_profit_raw else _safe_float(t.get("followerTotalProfit", 0))
    follower_count = int(_safe_float(t.get("totalFollowers", t.get("followerNum", t.get("followerCount", 0)))))
    total_orders = int(_safe_float(t.get("tradeCount", t.get("tradeOrders", t.get("totalOrders", 0)))))
    return {"trader_id": t.get("traderId", t.get("traderUid", t.get("uid", t.get("encUid", "")))), "nickname": t.get("traderName", t.get("traderNickName", t.get("nickName", ""))), "roi": roi, "total_pnl": total_pnl, "win_rate": win_rate, "follower_count": follower_count, "total_orders": total_orders, "mdd": mdd, "aum": aum, "copier_profit": copier_profit, "can_follow": t.get("canTrace", "No") == "Yes", "current_symbols": t.get("currentTradingList", []), "profit_count": int(_safe_float(t.get("profitCount", 0))), "loss_count": int(_safe_float(t.get("lossCount", 0))), "trade_days": int(_safe_float(t.get("tradeDays", 0))), "source": "api"}

# ═══════════════════════════════════════════════════════════════════
# SECTION 4 — PULL TRADE HISTORY (V2 only)
# ═══════════════════════════════════════════════════════════════════

def pull_trader_history_broker(creds: Dict, trader_id: str, product_type: str = "USDT-FUTURES", days_back: int = 90) -> List[Dict]:
    """Pull trade history via V2 broker endpoint: /api/v2/copy/mix-broker/query-history-traces"""
    all_trades = []
    end_time = str(int(time.time() * 1000))
    start_time = str(int((time.time() - days_back * 86400) * 1000))
    cursor = None
    for batch in range(200):
        params = {"traderId": trader_id, "productType": product_type, "startTime": start_time, "endTime": end_time, "limit": str(MAX_PAGE_SIZE)}
        if cursor:
            params["idLessThan"] = cursor
        data = _request("GET", "/api/v2/copy/mix-broker/query-history-traces", params=params, creds=creds)
        if not data:
            break
        orders = data if isinstance(data, list) else data.get("trackingList", data.get("list", data.get("traceList", [])))
        if not orders:
            break
        for o in orders:
            trade = _parse_order(o, trader_id)
            if trade:
                all_trades.append(trade)
        last_id = orders[-1].get("trackingNo", orders[-1].get("orderId", orders[-1].get("traceId", "")))
        if last_id and last_id != cursor:
            cursor = last_id
        else:
            break
        logger.info(f"Broker history {trader_id[:10]}.. batch {batch+1}: {len(orders)} orders (total: {len(all_trades)})")
        if len(orders) < MAX_PAGE_SIZE:
            break
    return all_trades


def pull_trader_history_follower(creds: Dict, product_type: str = "USDT-FUTURES") -> List[Dict]:
    """Pull OWN follower history via V2: /api/v2/copy/mix-follower/query-history-orders (trades we copied)."""
    all_trades = []
    cursor = None
    for batch in range(200):
        params = {"productType": product_type, "limit": str(MAX_PAGE_SIZE)}
        if cursor:
            params["idLessThan"] = cursor
        data = _request("GET", "/api/v2/copy/mix-follower/query-history-orders", params=params, creds=creds)
        if not data:
            break
        orders = data if isinstance(data, list) else data.get("list", data.get("orderList", []))
        if not orders:
            break
        for o in orders:
            tid = o.get("traderId", o.get("traderUid", "follower_self"))
            trade = _parse_order(o, tid)
            if trade:
                all_trades.append(trade)
        last_id = orders[-1].get("trackingNo", orders[-1].get("orderId", ""))
        if last_id and last_id != cursor:
            cursor = last_id
        else:
            break
        logger.info(f"Follower history batch {batch+1}: {len(orders)} orders (total: {len(all_trades)})")
        if len(orders) < MAX_PAGE_SIZE:
            break
    return all_trades


def pull_trader_current_broker(creds: Dict, trader_id: str, product_type: str = "USDT-FUTURES") -> List[Dict]:
    """Pull current open positions for a trader via V2 broker endpoint."""
    params = {"traderId": trader_id, "productType": product_type, "limit": str(MAX_PAGE_SIZE)}
    data = _request("GET", "/api/v2/copy/mix-broker/query-current-traces", params=params, creds=creds)
    if not data:
        return []
    orders = data if isinstance(data, list) else data.get("list", data.get("traceList", []))
    positions = []
    for o in orders:
        positions.append({"trader_id": trader_id, "symbol": _clean_symbol(o.get("symbol", "")), "side": "LONG" if str(o.get("posSide", o.get("side", "long"))).lower() in ("long", "buy") else "SHORT", "entry_price": _safe_float(o.get("openPrice", o.get("openAvgPrice", 0))), "leverage": _safe_float(o.get("leverage", 1)), "margin": _safe_float(o.get("marginAmount", o.get("openMargin", 0))), "unrealized_pnl": _safe_float(o.get("achievedProfits", o.get("unrealizedPnl", 0))), "open_time": _ts_to_dt(o.get("openTime", o.get("cTime", "")))})
    return positions


def _parse_order(o: Dict, trader_id: str) -> Optional[Dict]:
    symbol = _clean_symbol(o.get("symbol", ""))
    if not symbol:
        return None
    side_raw = str(o.get("posSide", o.get("side", "long"))).lower()
    side = "LONG" if side_raw in ("long", "buy") else "SHORT"
    entry_price = _safe_float(o.get("openPriceAvg", o.get("openPrice", o.get("openAvgPrice", 0))))
    exit_price = _safe_float(o.get("closePriceAvg", o.get("closePrice", o.get("closeAvgPrice", 0))))
    pnl = _safe_float(o.get("netProfit", o.get("achievedProfits", o.get("profit", 0))))
    leverage = _safe_float(o.get("openLeverage", o.get("leverage", 1)))
    open_size = _safe_float(o.get("openSize", o.get("openAvgAmount", 0)))
    position_size = open_size * entry_price if open_size > 0 and entry_price > 0 else 0
    if pnl == 0 and entry_price > 0 and exit_price > 0 and open_size > 0:
        if side == "LONG":
            pnl = (exit_price - entry_price) * open_size
        else:
            pnl = (entry_price - exit_price) * open_size
    pnl_pct = ((exit_price - entry_price) / entry_price * 100 * (1 if side == "LONG" else -1)) if entry_price > 0 and exit_price > 0 else 0
    entry_time = _ts_to_dt(o.get("openTime", o.get("openUtcTime", o.get("cTime", ""))))
    exit_time = _ts_to_dt(o.get("closeTime", o.get("closeUtcTime", o.get("uTime", ""))))
    if not entry_time:
        return None
    return {"trader_id": trader_id, "symbol": symbol, "side": side, "entry_price": entry_price, "exit_price": exit_price, "entry_time": entry_time, "exit_time": exit_time, "pnl": round(pnl, 4), "pnl_pct": round(pnl_pct, 4), "leverage": leverage, "position_size_usd": round(position_size, 2)}

# ═══════════════════════════════════════════════════════════════════
# SECTION 5 — FOLLOW / UNFOLLOW (V2)
# ═══════════════════════════════════════════════════════════════════

def follow_trader(creds: Dict, trader_id: str, trace_value: float = 5.0) -> bool:
    """Follow a trader with minimal capital via V2 follower settings endpoint."""
    settings_item = {"symbol": "BTCUSDT", "productType": "USDT-FUTURES", "marginType": "trader", "marginCoin": "USDT", "leverType": "trader", "traceType": "amount", "traceValue": str(trace_value), "maxHoldSize": "5000"}
    body = {"traderId": trader_id, "settings": [settings_item]}
    data = _request("POST", "/api/v2/copy/mix-follower/settings", body=body, creds=creds)
    if data is not None:
        logger.info(f"Followed trader {trader_id[:12]}.. with {trace_value} USDT/trade")
        return True
    # Try the newer copy-settings endpoint
    body2 = {"traderId": trader_id, "productType": "USDT-FUTURES", "marginCoin": "USDT", "marginType": "crossed", "leverType": "trader", "copyAmount": str(trace_value), "copyMode": "fixedAmount"}
    data2 = _request("POST", "/api/v2/copy/mix-follower/copy-settings", body=body2, creds=creds)
    if data2 is not None:
        logger.info(f"Followed trader {trader_id[:12]}.. via copy-settings with {trace_value} USDT/trade")
        return True
    logger.warning(f"Failed to follow trader {trader_id[:12]}..")
    return False


def unfollow_trader(creds: Dict, trader_id: str) -> bool:
    """Unfollow a trader via V2 endpoint."""
    body = {"traderId": trader_id}
    data = _request("POST", "/api/v2/copy/mix-follower/cancel-trader", body=body, creds=creds)
    if data is not None:
        logger.info(f"Unfollowed trader {trader_id[:12]}..")
        return True
    return False


def get_my_followed_traders(creds: Dict) -> List[Dict]:
    """List traders we currently follow via V2."""
    data = _request("GET", "/api/v2/copy/mix-follower/query-traders", creds=creds)
    if not data:
        return []
    traders = data if isinstance(data, list) else data.get("traderList", data.get("list", []))
    return [{"trader_id": t.get("traderId", t.get("traderUid", "")), "nickname": t.get("nickName", t.get("traderNickName", "")), "acc_profit": _safe_float(t.get("accProfit", t.get("totalProfit", 0))), "acc_margin": _safe_float(t.get("accMargin", t.get("totalMargin", 0)))} for t in traders]

# ═══════════════════════════════════════════════════════════════════
# SECTION 6 — EXPORT TO CSV
# ═══════════════════════════════════════════════════════════════════

CSV_COLUMNS = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd"]


def export_trades_csv(trades: List[Dict], output_path: Path = None) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = DATA_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%d')}_trades.csv"
    existing_count = 0
    if output_path.exists():
        try:
            with open(output_path) as f:
                existing_count = sum(1 for _ in f) - 1
        except Exception:
            pass
    mode = "a" if existing_count > 0 else "w"
    with open(output_path, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if mode == "w":
            writer.writeheader()
        for t in trades:
            row = {k: t.get(k, "") for k in CSV_COLUMNS}
            if isinstance(row.get("entry_time"), datetime):
                row["entry_time"] = row["entry_time"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(row.get("exit_time"), datetime):
                row["exit_time"] = row["exit_time"].strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow(row)
    total = existing_count + len(trades)
    logger.info(f"Exported {len(trades)} trades to {output_path} (total: {total})")
    return output_path

# ═══════════════════════════════════════════════════════════════════
# SECTION 7 — AUTO-ANALYZE (invoke trader_deep_analyzer.py)
# ═══════════════════════════════════════════════════════════════════

def run_deep_analysis(csv_path: Path) -> None:
    analyzer_path = BASE_PATH / "trader_deep_analyzer.py"
    if not analyzer_path.exists():
        logger.warning(f"trader_deep_analyzer.py not found at {analyzer_path}")
        return
    python = sys.executable
    cmd = [python, str(analyzer_path), "--input", str(csv_path), "--skip-indicators"]
    logger.info(f"Running deep analysis: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=False, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"Deep analysis exited with code {result.returncode}")
    except subprocess.TimeoutExpired:
        logger.error("Deep analysis timed out after 300s")
    except Exception as e:
        logger.error(f"Deep analysis failed: {e}")

# ═══════════════════════════════════════════════════════════════════
# SECTION 8 — HELPERS
# ═══════════════════════════════════════════════════════════════════

def _safe_float(val: Any) -> float:
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def _clean_symbol(raw: str) -> str:
    return raw.replace("_UMCBL", "").replace("_DMCBL", "").replace("_CMCBL", "").replace("_SUMCBL", "").replace("_SDMCBL", "").replace("_", "").strip()


def _ts_to_dt(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, (int, float)):
        ts = val
    elif isinstance(val, str):
        if val.isdigit():
            ts = int(val)
        else:
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return None
    else:
        return None
    if ts > 1e12:
        ts = ts / 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def print_traders_table(traders: List[Dict]) -> None:
    print(f"\n{'='*155}")
    print(f"  TOP TRADERS DISCOVERED ({len(traders)} total)")
    print(f"{'='*155}")
    print(f"  {'#':>3}  {'Nickname':<22}  {'ROI':>10}  {'PnL':>14}  {'Win Rate':>9}  {'MDD':>8}  {'Orders':>7}  {'Followers':>9}  {'Copier PnL':>14}  {'AUM':>12}  {'Follow':>6}")
    print(f"  {'─'*3}  {'─'*22}  {'─'*10}  {'─'*14}  {'─'*9}  {'─'*8}  {'─'*7}  {'─'*9}  {'─'*14}  {'─'*12}  {'─'*6}")
    for i, t in enumerate(traders, 1):
        roi_str = f"{t['roi']:.1f}%" if abs(t['roi']) < 10000 else f"{t['roi']:.0f}%"
        pnl_str = f"${t['total_pnl']:,.0f}" if abs(t['total_pnl']) >= 1 else f"${t['total_pnl']:.2f}"
        wr_str = f"{t['win_rate']:.1f}%"
        mdd_str = f"{t.get('mdd', 0):.1f}%"
        aum_str = f"${t.get('aum', 0):,.0f}"
        cp = t.get('copier_profit', 0)
        cp_str = f"${cp:,.0f}" if abs(cp) >= 1 else f"${cp:.2f}"
        follow_str = "YES" if t.get('can_follow') else "no"
        print(f"  {i:>3}  {t.get('nickname', '')[:22]:<22}  {roi_str:>10}  {pnl_str:>14}  {wr_str:>9}  {mdd_str:>8}  {t.get('total_orders', 0):>7}  {t.get('follower_count', 0):>9}  {cp_str:>14}  {aum_str:>12}  {follow_str:>6}")
    print(f"{'='*155}\n")


def print_trades_summary(trades: List[Dict], trader_id: str = "") -> None:
    if not trades:
        print(f"  No trades found{' for ' + trader_id[:16] if trader_id else ''}")
        return
    winners = [t for t in trades if t.get("pnl", 0) > 0]
    losers = [t for t in trades if t.get("pnl", 0) <= 0]
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    symbols = set(t.get("symbol", "") for t in trades)
    longs = [t for t in trades if t.get("side") == "LONG"]
    shorts = [t for t in trades if t.get("side") == "SHORT"]
    label = f" ({trader_id[:16]})" if trader_id else ""
    print(f"\n  Trade Summary{label}:")
    print(f"    Total trades: {len(trades)}  |  Winners: {len(winners)} ({len(winners)/len(trades)*100:.1f}%)  |  Losers: {len(losers)}")
    print(f"    Total PnL: ${total_pnl:,.2f}  |  Avg PnL/trade: ${total_pnl/len(trades):.2f}")
    print(f"    Longs: {len(longs)}  |  Shorts: {len(shorts)}  |  Symbols: {len(symbols)}")
    if winners:
        avg_win = sum(t["pnl"] for t in winners) / len(winners)
        max_win = max(t["pnl"] for t in winners)
        print(f"    Avg win: ${avg_win:.2f}  |  Max win: ${max_win:.2f}")
    if losers:
        avg_loss = sum(t["pnl"] for t in losers) / len(losers)
        max_loss = min(t["pnl"] for t in losers)
        print(f"    Avg loss: ${avg_loss:.2f}  |  Max loss: ${max_loss:.2f}")

# ═══════════════════════════════════════════════════════════════════
# SECTION 9 — MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════

def cmd_discover(creds: Dict, args) -> List[Dict]:
    top_n = getattr(args, "top", 50)
    logger.info(f"Discovering top {top_n} traders...")
    traders = discover_traders(creds, top_n=top_n)
    if traders:
        print_traders_table(traders)
        out = DATA_DIR / "discovered_traders.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(traders, f, indent=2, default=str)
        logger.info(f"Saved {len(traders)} traders to {out}")
    else:
        logger.warning("No traders discovered — check API credentials")
    return traders


def cmd_scrape_trader(creds: Dict, trader_id: str) -> List[Dict]:
    logger.info(f"Scraping trader {trader_id[:16]}...")
    trades = pull_trader_history_broker(creds, trader_id)
    if not trades:
        logger.info("Broker endpoint returned no data, trying as follower...")
        if follow_trader(creds, trader_id, trace_value=5.0):
            time.sleep(2)
            trades = pull_trader_history_broker(creds, trader_id)
    if not trades:
        logger.info("Trying COIN-FUTURES product type...")
        trades = pull_trader_history_broker(creds, trader_id, product_type="COIN-FUTURES")
    if not trades:
        logger.info("Trying USDC-FUTURES product type...")
        trades = pull_trader_history_broker(creds, trader_id, product_type="USDC-FUTURES")
    print_trades_summary(trades, trader_id)
    return trades


def cmd_full_scrape(creds: Dict, args) -> None:
    top_n = getattr(args, "top", 50)
    no_analyze = getattr(args, "no_analyze", False)
    traders = discover_traders(creds, top_n=top_n)
    if not traders:
        logger.error("No traders discovered, aborting. Provide trader IDs manually: --trader TRADER_ID")
        return
    print_traders_table(traders)
    all_trades = []
    failed = []
    for i, trader in enumerate(traders, 1):
        tid = trader["trader_id"]
        logger.info(f"[{i}/{len(traders)}] Scraping {trader.get('nickname', tid[:12])} ({tid[:12]}..)")
        trades = cmd_scrape_trader(creds, tid)
        if trades:
            all_trades.extend(trades)
        else:
            failed.append(tid[:16])
    if not all_trades:
        logger.error("No trades collected from any trader")
        return
    print(f"\n{'='*80}")
    print(f"  TOTAL: {len(all_trades)} trades from {len(traders) - len(failed)}/{len(traders)} traders")
    if failed:
        print(f"  Failed: {', '.join(failed)}")
    print(f"{'='*80}")
    csv_path = export_trades_csv(all_trades)
    if not no_analyze:
        run_deep_analysis(csv_path)
    else:
        logger.info(f"Skipping analysis (--no-analyze). CSV at: {csv_path}")


def cmd_scrape_multi(creds: Dict, trader_ids: List[str], args) -> None:
    no_analyze = getattr(args, "no_analyze", False)
    all_trades = []
    for i, tid in enumerate(trader_ids, 1):
        logger.info(f"[{i}/{len(trader_ids)}] Scraping {tid[:16]}...")
        trades = cmd_scrape_trader(creds, tid)
        if trades:
            all_trades.extend(trades)
    if not all_trades:
        logger.error("No trades collected")
        return
    csv_path = export_trades_csv(all_trades)
    if not no_analyze:
        run_deep_analysis(csv_path)


def main():
    parser = argparse.ArgumentParser(description="Bitget Copy Trading scraper — discover top traders, pull histories, analyze", formatter_class=argparse.RawDescriptionHelpFormatter, epilog="Examples:\n  python3 bitget_trader_scraper.py --discover\n  python3 bitget_trader_scraper.py --trader TRADER_UID_HERE\n  python3 bitget_trader_scraper.py --top 50\n  python3 bitget_trader_scraper.py --trader ID1,ID2,ID3\n  python3 bitget_trader_scraper.py --my-traders\n  python3 bitget_trader_scraper.py --follow TRADER_ID\n  python3 bitget_trader_scraper.py --follower-history")
    parser.add_argument("--discover", action="store_true", help="Just list top traders (no trade scraping)")
    parser.add_argument("--trader", type=str, help="Scrape specific trader ID(s) — comma-separated for multiple")
    parser.add_argument("--top", type=int, default=50, help="Number of top traders to scrape (default: 50)")
    parser.add_argument("--no-analyze", action="store_true", help="Skip auto-analysis after CSV export")
    parser.add_argument("--follow", type=str, help="Follow a trader with minimal $5 capital")
    parser.add_argument("--unfollow", type=str, help="Unfollow a trader")
    parser.add_argument("--my-traders", action="store_true", help="List traders you currently follow")
    parser.add_argument("--positions", type=str, help="Show current positions for a trader ID")
    parser.add_argument("--follower-history", action="store_true", help="Pull your own copy-trade history (trades you copied)")
    parser.add_argument("--output", type=str, help="Custom CSV output path")
    parser.add_argument("--days", type=int, default=90, help="Days of history to pull (default: 90)")
    parser.add_argument("--continuous", action="store_true", help="24/7 mode: discover → scrape → analyze → repeat every --interval seconds")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between continuous cycles (default: 3600 = 1 hour)")
    args = parser.parse_args()
    creds = load_credentials()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.continuous:
        cmd_continuous(creds, args)
    elif args.discover:
        cmd_discover(creds, args)
    elif args.trader:
        trader_ids = [t.strip() for t in args.trader.split(",") if t.strip()]
        if len(trader_ids) == 1:
            trades = cmd_scrape_trader(creds, trader_ids[0])
            if trades:
                out_path = Path(args.output) if args.output else None
                csv_path = export_trades_csv(trades, output_path=out_path)
                if not args.no_analyze:
                    run_deep_analysis(csv_path)
        else:
            cmd_scrape_multi(creds, trader_ids, args)
    elif args.follow:
        follow_trader(creds, args.follow)
    elif args.unfollow:
        unfollow_trader(creds, args.unfollow)
    elif args.my_traders:
        traders = get_my_followed_traders(creds)
        if traders:
            print(f"\n  Currently following {len(traders)} traders:")
            for t in traders:
                print(f"    {t['trader_id'][:16]}  {t.get('nickname', ''):<20}  PnL: ${t['acc_profit']:,.2f}  Margin: ${t['acc_margin']:,.2f}")
        else:
            print("  Not following any traders (or auth failed)")
    elif args.positions:
        positions = pull_trader_current_broker(creds, args.positions)
        if positions:
            print(f"\n  Current positions for {args.positions[:16]}:")
            for p in positions:
                print(f"    {p['symbol']:<16} {p['side']:<6} entry={p['entry_price']:.4f}  lev={p['leverage']:.0f}x  margin=${p['margin']:.2f}  uPnL=${p['unrealized_pnl']:.2f}")
        else:
            print(f"  No open positions for {args.positions[:16]}")
    elif args.follower_history:
        trades = pull_trader_history_follower(creds)
        if trades:
            print_trades_summary(trades)
            out_path = Path(args.output) if args.output else None
            csv_path = export_trades_csv(trades, output_path=out_path)
            if not args.no_analyze:
                run_deep_analysis(csv_path)
        else:
            print("  No follower trade history found")
    else:
        cmd_full_scrape(creds, args)


def cmd_continuous(creds: Dict, args):
    """24/7 continuous scraping: discover → scrape all → analyze → wait → repeat.
    Cycles through top traders, pulls new trades, runs analysis, saves results.
    Each cycle: discover top N → scrape each → export CSV → analyze → sleep."""
    cycle = 0
    interval = getattr(args, "interval", 3600)  # default: 1 hour between full cycles
    top_n = getattr(args, "top", 30)
    while True:
        cycle += 1
        logger.info(f"{'='*60}")
        logger.info(f"CONTINUOUS CYCLE #{cycle} — discovering top {top_n} traders")
        logger.info(f"{'='*60}")
        try:
            traders = discover_traders(creds, top_n=top_n)
            if not traders:
                logger.warning("No traders discovered this cycle, retrying in 5 min")
                time.sleep(300)
                continue
            all_trades = []
            failed = []
            for i, trader in enumerate(traders, 1):
                tid = trader["trader_id"]
                logger.info(f"[{i}/{len(traders)}] Scraping {trader.get('nickname', tid[:12])}")
                try:
                    trades = cmd_scrape_trader(creds, tid)
                    if trades:
                        all_trades.extend(trades)
                    else:
                        failed.append(tid[:16])
                except Exception as e:
                    logger.warning(f"  Error scraping {tid[:12]}: {e}")
                    failed.append(tid[:16])
                time.sleep(2)  # rate limit between traders
            if all_trades:
                logger.info(f"Cycle #{cycle}: {len(all_trades)} trades from {len(traders) - len(failed)}/{len(traders)} traders")
                csv_path = export_trades_csv(all_trades)
                try:
                    run_deep_analysis(csv_path)
                except Exception as e:
                    logger.warning(f"Analysis error: {e}")
                # Save cycle summary
                summary = {"cycle": cycle, "ts": datetime.now(timezone.utc).isoformat(), "traders_scraped": len(traders) - len(failed), "traders_failed": len(failed), "total_trades": len(all_trades), "csv_path": str(csv_path)}
                summary_file = DATA_DIR / "continuous_summary.jsonl"
                with open(summary_file, "a") as f:
                    f.write(json.dumps(summary) + "\n")
            else:
                logger.warning(f"Cycle #{cycle}: no trades collected")
        except Exception as e:
            logger.error(f"Cycle #{cycle} error: {e}")
        logger.info(f"Cycle #{cycle} complete. Sleeping {interval}s until next cycle...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
