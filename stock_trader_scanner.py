#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""Stock Trader Scanner — discovers successful equity traders and their strategies.

Sources (all free, no API keys required):
  1. Congressional trades — SEC periodic disclosure reports (Capitol Trades / House Stock Watcher)
  2. Insider trades — SEC Form 4 filings (OpenInsider / SEC EDGAR)
  3. Institutional 13F changes — whale position changes (SEC EDGAR quarterly)
  4. YouTube stock traders — channels with verifiable stock trade history
  5. Finviz insider screen — insider buying/selling aggregation
  6. Reddit — r/wallstreetbets, r/stocks verified gains

Output: CSV in data/stock_traders/{YYYYMMDD}_trades.csv
Same schema as crypto scrapers: trader_id, symbol, side, entry_price, exit_price,
entry_time, exit_time, pnl, pnl_pct, leverage, position_size_usd

Usage:
  python3 stock_trader_scanner.py                    # Run all sources
  python3 stock_trader_scanner.py --source congress   # Congress trades only
  python3 stock_trader_scanner.py --source insider    # Insider trades only
  python3 stock_trader_scanner.py --source 13f        # 13F filings only
  python3 stock_trader_scanner.py --source youtube    # YouTube stock traders only
  python3 stock_trader_scanner.py --source finviz     # Finviz insider screen
  python3 stock_trader_scanner.py --full              # All sources + deep scan
"""
import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR / "stock_traders"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("stock_scanner")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Load our tradeable symbols to filter for relevant stocks
SYMBOLS_FILE = BASE_PATH / "symbols_tradier.json"
OUR_SYMBOLS = set()
if SYMBOLS_FILE.exists():
    try:
        OUR_SYMBOLS = set(json.loads(SYMBOLS_FILE.read_text()))
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════
# INJECTION INTO LIVE TRADING (same mechanism as ez_news_scanner.py)
# ═══════════════════════════════════════════════════════════════════
# Writes only to data/news_injections.json. tradier_rankings.py is the sole
# owner of symbols_trb_long/short.json and applies the master allowlist before
# publishing those derived files.
INJECTION_FILE = config.DATA_DIR / "news_injections.json"
INJECTION_TTL_HOURS = 48
MIN_INJECT_CONVICTION = 2  # Minimum conviction_sources to inject (2+ sources agree)
MIN_INJECT_VALUE = 50000  # OR total_value > $50k from single source
MAX_INJECT_PICKS = 5  # Max symbols to inject per side per cycle
STOCK_INJECT_ACCOUNTS = ("trb",)

# Proven politicians (55%+ WR at 30d on 5+ trades from backtest) + Trump inner circle.
# ONLY these get injected into live trading. Everyone else is newsletter-only.
PROVEN_POLITICIANS = {
    # Trump inner circle (backtest-proven)
    "kevin hern", "tommy tuberville", "marjorie taylor greene", "michael mccaul",
    # Top performers from backtest (55%+ WR, 5+ trades, 30d)
    "kelly morrison", "rob bresnahan", "sheri biggs", "tom suozzi", "katie britt",
    "lloyd doggett", "dan meuser", "john fetterman", "gary peters", "rick larsen",
    "rich mccormick", "jared moskowitz", "james comer", "cleo fields",
    "nancy pelosi", "val hoyle", "david taylor", "julia letlow", "august pfluger",
}

CSV_FIELDS = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd", "source", "trader_name", "trader_title"]


def _safe_float(val, default=0.0):
    try:
        return float(str(val).replace(",", "").replace("$", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default


def _normalize_symbol(sym: str) -> str:
    """Normalize stock symbols — strip exchange prefix, uppercase."""
    if not sym:
        return ""
    sym = sym.strip().upper()
    for prefix in ["NYSE:", "NASDAQ:", "AMEX:", "ARCA:", "OTC:"]:
        if sym.startswith(prefix):
            sym = sym[len(prefix):]
    return sym.replace(".", "").replace("-", "")


def _is_relevant_symbol(sym: str) -> bool:
    """Check if symbol is in our tradeable list or is a major stock."""
    sym = _normalize_symbol(sym)
    if not sym or len(sym) > 6:
        return False
    if OUR_SYMBOLS and sym in OUR_SYMBOLS:
        return True
    if not OUR_SYMBOLS:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════
# SOURCE 1: CONGRESSIONAL TRADES (Capitol Trades / House Stock Watcher)
# ═══════════════════════════════════════════════════════════════════

def scrape_congress_trades() -> List[Dict]:
    """Scrape congressional stock trades from Capitol Trades (HTML table)."""
    trades = []
    logger.info("[Congress] Fetching Capitol Trades data...")
    pages_to_scrape = 5
    for page in range(1, pages_to_scrape + 1):
        try:
            resp = SESSION.get(f"https://www.capitoltrades.com/trades?page={page}&pageSize=96", timeout=20)
            if resp.status_code != 200:
                logger.warning(f"[Congress] Page {page}: HTTP {resp.status_code}")
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
                issuer_cell = cols[1].get_text(strip=True)
                trade_date_raw = cols[3].get_text(strip=True)
                tx_type = cols[6].get_text(strip=True).lower()
                size_str = cols[7].get_text(strip=True)
                price_str = cols[8].get_text(strip=True)
                sym_match = re.search(r"([A-Z]{1,5}):US", issuer_cell)
                if not sym_match:
                    sym_match = re.search(r"([A-Z]{1,5})$", issuer_cell.split()[-1] if issuer_cell.split() else "")
                sym = _normalize_symbol(sym_match.group(1)) if sym_match else ""
                if not _is_relevant_symbol(sym):
                    continue
                if "buy" in tx_type or "purchase" in tx_type:
                    side = "LONG"
                elif "sell" in tx_type or "sale" in tx_type:
                    side = "SHORT"
                else:
                    continue
                amount = _parse_range_amount(size_str)
                price = _safe_float(price_str)
                trade_date = _parse_relative_date(trade_date_raw)
                chamber = "Senate" if "senator" in politician.lower() or "Senate" in cols[0].get_text() else "House"
                party_match = re.search(r"(Republican|Democrat)", cols[0].get_text())
                party = party_match.group(1)[:1] if party_match else "?"
                clean_name = re.sub(r"(Republican|Democrat|House|Senate)", "", politician).strip()
                trades.append({"trader_id": f"congress_{clean_name.replace(' ', '_').lower()[:30]}", "symbol": sym, "side": side, "entry_price": price, "exit_price": 0.0, "entry_time": trade_date, "exit_time": trade_date, "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": amount, "source": f"congress_{chamber.lower()}", "trader_name": clean_name, "trader_title": f"US {chamber} ({party})"})
            time.sleep(1)
        except Exception as e:
            logger.warning(f"[Congress] Page {page} failed: {e}")
    logger.info(f"[Congress] Total: {len(trades)} trades from {pages_to_scrape} pages")
    return trades


def _parse_relative_date(date_str: str) -> str:
    """Parse Capitol Trades date like '16 Mar2026' or 'Today' into YYYY-MM-DD."""
    if not date_str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_str = date_str.strip()
    if "today" in date_str.lower():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    months = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}
    match = re.search(r"(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})", date_str)
    if match:
        day = int(match.group(1))
        mon = months.get(match.group(2).lower()[:3], "01")
        year = match.group(3)
        return f"{year}-{mon}-{day:02d}"
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _parse_range_amount(amount_str: str) -> float:
    """Parse congressional disclosure amounts like '$1,001 - $15,000' → midpoint."""
    if not amount_str:
        return 0.0
    amount_str = amount_str.replace(",", "").replace("$", "").strip()
    parts = re.findall(r"[\d.]+", amount_str)
    if len(parts) >= 2:
        return (float(parts[0]) + float(parts[1])) / 2
    elif len(parts) == 1:
        return float(parts[0])
    return 0.0


# ═══════════════════════════════════════════════════════════════════
# SOURCE 2: INSIDER TRADES (OpenInsider)
# ═══════════════════════════════════════════════════════════════════

def scrape_insider_trades() -> List[Dict]:
    """Scrape insider purchases from OpenInsider — large purchases by officers/directors."""
    trades = []
    logger.info("[Insider] Fetching OpenInsider cluster buys...")
    urls = [
        "http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=30&fdr=&td=0&tdr=&feession=&fes=&islt=&isceo=1&iscfo=1&isvp=1&isd=1&isof=&isother=&cnt=100&page=1",
        "http://openinsider.com/screener?s=&o=&pl=100&ph=&ll=&lh=&fd=30&fdr=&td=0&tdr=&feession=&fes=&islt=&cnt=100&page=1",
    ]
    seen = set()
    for url in urls:
        try:
            resp = SESSION.get(url, timeout=30)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", {"class": "tinytable"})
            if not table:
                continue
            rows = table.find_all("tr")[1:]
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 13:
                    continue
                filing_date = cols[1].get_text(strip=True)
                trade_date = cols[2].get_text(strip=True)
                sym = _normalize_symbol(cols[3].get_text(strip=True))
                if not _is_relevant_symbol(sym):
                    continue
                insider_name = cols[4].get_text(strip=True)
                title = cols[5].get_text(strip=True)
                trade_type = cols[6].get_text(strip=True).upper()
                price = _safe_float(cols[8].get_text(strip=True))
                qty = _safe_float(cols[9].get_text(strip=True))
                value = _safe_float(cols[12].get_text(strip=True))
                if trade_type.startswith("P") or "BUY" in trade_type:
                    side = "LONG"
                elif trade_type.startswith("S") or "SELL" in trade_type:
                    side = "SHORT"
                else:
                    continue
                key = f"{sym}_{trade_date}_{insider_name}_{side}"
                if key in seen:
                    continue
                seen.add(key)
                trades.append({"trader_id": f"insider_{insider_name.replace(' ', '_').lower()[:30]}", "symbol": sym, "side": side, "entry_price": price, "exit_price": 0.0, "entry_time": trade_date, "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": abs(value), "source": "openinsider", "trader_name": insider_name, "trader_title": title})
            logger.info(f"[Insider] Parsed {len(trades)} insider trades so far")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"[Insider] OpenInsider scrape failed: {e}")
    logger.info(f"[Insider] Total: {len(trades)} trades")
    return trades


# ═══════════════════════════════════════════════════════════════════
# SOURCE 3: INSTITUTIONAL 13F FILINGS (SEC EDGAR)
# ═══════════════════════════════════════════════════════════════════

def scrape_13f_filings() -> List[Dict]:
    """Scrape recent 13F filings from SEC EDGAR for major institutional changes."""
    trades = []
    logger.info("[13F] Fetching recent institutional filings from SEC EDGAR...")
    edgar_headers = {"User-Agent": "TradingResearch research@example.com", "Accept-Encoding": "gzip, deflate"}
    # Top hedge funds / institutional filers to track
    whale_ciks = {
        "0001067983": ("Berkshire Hathaway", "Warren Buffett"),
        "0001336528": ("Pershing Square", "Bill Ackman"),
        "0001649339": ("Citadel Advisors", "Ken Griffin"),
        "0001037389": ("Renaissance Technologies", "Jim Simons"),
        "0001061768": ("Druckenmiller (Duquesne)", "Stanley Druckenmiller"),
        "0001535392": ("Appaloosa Management", "David Tepper"),
        "0001350694": ("Tiger Global", "Chase Coleman"),
        "0001484148": ("Coatue Management", "Philippe Laffont"),
        "0001029160": ("Baupost Group", "Seth Klarman"),
        "0001167557": ("Third Point", "Dan Loeb"),
        "0001040273": ("Lone Pine Capital", "Stephen Mandel"),
        "0001345471": ("Viking Global", "Andreas Halvorsen"),
        "0001273087": ("Point72", "Steve Cohen"),
        "0001510387": ("Bridgewater Associates", "Ray Dalio"),
        "0001582846": ("ARK Invest", "Cathie Wood"),
    }
    for cik, (fund_name, manager) in whale_ciks.items():
        try:
            url = f"https://efts.sec.gov/LATEST/search-index?q=%2213F%22&dateRange=custom&startdt={(datetime.now(timezone.utc) - timedelta(days=120)).strftime('%Y-%m-%d')}&enddt={datetime.now(timezone.utc).strftime('%Y-%m-%d')}&forms=13F-HR&ciks={cik}"
            resp = SESSION.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=edgar_headers, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            recent_filings = data.get("filings", {}).get("recent", {})
            forms = recent_filings.get("form", [])
            dates = recent_filings.get("filingDate", [])
            accessions = recent_filings.get("accessionNumber", [])
            for i, form in enumerate(forms[:5]):
                if "13F" not in form:
                    continue
                acc_clean = accessions[i].replace("-", "")
                filing_date = dates[i]
                info_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc_clean}/"
                try:
                    resp2 = SESSION.get(f"https://data.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc_clean}/infotable.xml", headers=edgar_headers, timeout=15)
                    if resp2.status_code != 200:
                        resp2 = SESSION.get(f"https://data.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc_clean}/primary_doc.xml", headers=edgar_headers, timeout=15)
                    if resp2.status_code == 200 and len(resp2.text) > 500:
                        holdings = _parse_13f_xml(resp2.text, fund_name, manager, filing_date)
                        trades.extend(holdings)
                        logger.info(f"[13F] {fund_name}: {len(holdings)} holdings from {filing_date}")
                except Exception as e2:
                    logger.debug(f"[13F] {fund_name} filing detail failed: {e2}")
                break
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"[13F] {fund_name} failed: {e}")
    logger.info(f"[13F] Total: {len(trades)} institutional holdings/trades")
    return trades


def _parse_13f_xml(xml_text: str, fund_name: str, manager: str, filing_date: str) -> List[Dict]:
    """Parse 13F XML info table into trade records."""
    trades = []
    soup = BeautifulSoup(xml_text, "html.parser")
    entries = soup.find_all(re.compile(r"infotable", re.IGNORECASE))
    for entry in entries:
        name_tag = entry.find(re.compile(r"nameofissuer", re.IGNORECASE))
        cusip_tag = entry.find(re.compile(r"cusip", re.IGNORECASE))
        value_tag = entry.find(re.compile(r"value", re.IGNORECASE))
        shares_tag = entry.find(re.compile(r"sshprnamt|shares", re.IGNORECASE))
        put_call_tag = entry.find(re.compile(r"putcall", re.IGNORECASE))
        if not name_tag:
            continue
        issuer = name_tag.get_text(strip=True)
        value = _safe_float(value_tag.get_text(strip=True)) * 1000 if value_tag else 0.0
        shares = _safe_float(shares_tag.get_text(strip=True)) if shares_tag else 0.0
        put_call = put_call_tag.get_text(strip=True).upper() if put_call_tag else ""
        sym = _issuer_to_symbol(issuer)
        if not sym or not _is_relevant_symbol(sym):
            continue
        if put_call == "PUT":
            side = "SHORT"
        else:
            side = "LONG"
        price = value / shares if shares > 0 else 0.0
        trades.append({"trader_id": f"13f_{fund_name.replace(' ', '_').lower()[:30]}", "symbol": sym, "side": side, "entry_price": price, "exit_price": 0.0, "entry_time": filing_date, "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": value, "source": "sec_13f", "trader_name": manager, "trader_title": fund_name})
    return trades


def _issuer_to_symbol(issuer: str) -> str:
    """Best-effort mapping from issuer name to ticker symbol."""
    issuer_map = {
        "APPLE INC": "AAPL", "MICROSOFT CORP": "MSFT", "AMAZON COM INC": "AMZN",
        "ALPHABET INC": "GOOGL", "META PLATFORMS": "META", "NVIDIA CORP": "NVDA",
        "TESLA INC": "TSLA", "BERKSHIRE HATHAWAY": "BRK", "JPMORGAN CHASE": "JPM",
        "VISA INC": "V", "MASTERCARD": "MA", "JOHNSON & JOHNSON": "JNJ",
        "UNITEDHEALTH": "UNH", "EXXON MOBIL": "XOM", "CHEVRON CORP": "CVX",
        "PROCTER & GAMBLE": "PG", "HOME DEPOT": "HD", "COCA COLA CO": "KO",
        "PEPSICO INC": "PEP", "PFIZER INC": "PFE", "MERCK & CO": "MRK",
        "INTEL CORP": "INTC", "ADVANCED MICRO": "AMD", "QUALCOMM": "QCOM",
        "BROADCOM INC": "AVGO", "TEXAS INSTRUMENT": "TXN", "SALESFORCE": "CRM",
        "ADOBE INC": "ADBE", "NETFLIX INC": "NFLX", "PAYPAL": "PYPL",
        "BLOCK INC": "SQ", "COINBASE": "COIN", "PALANTIR": "PLTR",
        "CROWDSTRIKE": "CRWD", "SNOWFLAKE": "SNOW", "DATADOG": "DDOG",
        "ELI LILLY": "LLY", "ABBVIE INC": "ABBV", "THERMO FISHER": "TMO",
        "WALMART INC": "WMT", "COSTCO": "COST", "TARGET CORP": "TGT",
        "CONOCOPHILLIPS": "COP", "SCHLUMBERGER": "SLB", "DEVON ENERGY": "DVN",
        "PIONEER NATURAL": "PXD", "MARATHON PETRO": "MPC", "PHILLIPS 66": "PSX",
        "FREEPORT MCMORAN": "FCX", "NEWMONT": "NEM", "BARRICK GOLD": "GOLD",
        "SPDR S&P 500": "SPY", "INVESCO QQQ": "QQQ",
    }
    issuer_upper = issuer.upper()
    for key, sym in issuer_map.items():
        if key in issuer_upper:
            return sym
    return ""


# ═══════════════════════════════════════════════════════════════════
# SOURCE 4: YOUTUBE STOCK TRADERS
# ═══════════════════════════════════════════════════════════════════

def scrape_youtube_stock_traders() -> List[Dict]:
    """Search YouTube for stock traders sharing verifiable trade results."""
    trades = []
    logger.info("[YouTube-Stocks] Searching for stock trader channels...")
    search_queries = [
        "stock trading results broker statement 2026",
        "my stock trades this week real money",
        "options trading P&L real account 2026",
        "swing trading stocks real results proof",
        "day trading stocks live account profit",
        "stock portfolio results verified trades",
        "tradier trading results",
        "thinkorswim trading journal results",
        "interactive brokers trade history",
        "stock trading challenge real money results",
        "best stock picks verified performance",
        "stock trader journal entries proof",
    ]
    findings_file = DATA_DIR / "youtube_stock_findings.jsonl"
    try:
        import subprocess as sp
        for query in search_queries[:8]:
            cmd = ["yt-dlp", "--flat-playlist", "--dump-json", f"ytsearch10:{query}"]
            try:
                result = sp.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    continue
                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        video = json.loads(line)
                        title = video.get("title", "")
                        channel = video.get("channel", video.get("uploader", ""))
                        url = video.get("url", video.get("webpage_url", ""))
                        vid_id = video.get("id", "")
                        description = video.get("description", "")
                        if not _is_stock_trade_video(title, description):
                            continue
                        finding = {"video_id": vid_id, "title": title, "channel": channel, "url": url, "query": query, "found_at": datetime.now(timezone.utc).isoformat(), "type": "stock_trader"}
                        with open(findings_file, "a") as f:
                            f.write(json.dumps(finding) + "\n")
                        symbols_mentioned = _extract_stock_symbols_from_text(f"{title} {description}")
                        for sym in symbols_mentioned:
                            trades.append({"trader_id": f"yt_{channel.replace(' ', '_').lower()[:30]}", "symbol": sym, "side": "LONG", "entry_price": 0.0, "exit_price": 0.0, "entry_time": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": 0.0, "source": "youtube_stocks", "trader_name": channel, "trader_title": title[:80]})
                    except json.JSONDecodeError:
                        continue
            except sp.TimeoutExpired:
                continue
            time.sleep(2)
    except Exception as e:
        logger.warning(f"[YouTube-Stocks] Failed: {e}")
    logger.info(f"[YouTube-Stocks] Found {len(trades)} stock mentions from trader videos")
    return trades


def _is_stock_trade_video(title: str, description: str) -> bool:
    """Check if a YouTube video is about verifiable stock trades."""
    text = f"{title} {description}".lower()
    must_have = ["trade", "stock", "profit", "result", "p&l", "portfolio", "account", "return", "gain", "position"]
    verify_words = ["real", "proof", "actual", "verified", "statement", "broker", "live", "journal"]
    has_trade = any(w in text for w in must_have)
    has_verify = any(w in text for w in verify_words)
    return has_trade and has_verify


def _extract_stock_symbols_from_text(text: str) -> List[str]:
    """Extract stock ticker symbols from text ($AAPL, AAPL, etc)."""
    symbols = set()
    dollar_tickers = re.findall(r"\$([A-Z]{1,5})\b", text.upper())
    for sym in dollar_tickers:
        if _is_relevant_symbol(sym):
            symbols.add(sym)
    words = re.findall(r"\b([A-Z]{2,5})\b", text)
    noise = {"THE", "AND", "FOR", "WITH", "THIS", "THAT", "FROM", "HAVE", "WILL", "JUST", "LIKE", "BEEN", "ONLY", "ALSO", "THAN", "MORE", "SOME", "VERY", "REAL", "BEST", "MADE", "EACH", "OVER", "WEEK", "YEAR", "LIVE", "NEXT", "LAST", "CALL", "SELL", "HOLD", "LONG", "SHORT", "GAIN", "LOSS", "PICK", "PUTS", "DAY", "BUY", "TOP", "NEW", "NOT", "ALL", "HOW", "WHY", "GET"}
    for word in words:
        if word not in noise and _is_relevant_symbol(word):
            symbols.add(word)
    return list(symbols)


# ═══════════════════════════════════════════════════════════════════
# SOURCE 5: FINVIZ INSIDER SCREEN
# ═══════════════════════════════════════════════════════════════════

def scrape_finviz_insiders() -> List[Dict]:
    """Scrape Finviz insider trading screen for significant buys."""
    trades = []
    logger.info("[Finviz] Fetching insider trading data...")
    try:
        resp = SESSION.get("https://finviz.com/insidertrading.ashx?or=-10&tv=100000&tc=1&o=-transactionValue", timeout=15)
        if resp.status_code != 200:
            logger.warning(f"[Finviz] HTTP {resp.status_code}")
            return trades
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"class": "body-table"})
        if not table:
            tables = soup.find_all("table")
            for t in tables:
                if t.find("a", {"class": "tab-link"}):
                    table = t
                    break
        if not table:
            logger.warning("[Finviz] Could not find insider table")
            return trades
        rows = table.find_all("tr")[1:]
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 10:
                continue
            sym = _normalize_symbol(cols[0].get_text(strip=True))
            if not _is_relevant_symbol(sym):
                continue
            insider = cols[1].get_text(strip=True)
            relationship = cols[2].get_text(strip=True)
            trade_date = cols[3].get_text(strip=True)
            tx_type = cols[4].get_text(strip=True).upper()
            price = _safe_float(cols[6].get_text(strip=True))
            value = _safe_float(cols[8].get_text(strip=True))
            if "BUY" in tx_type or "PURCHASE" in tx_type:
                side = "LONG"
            elif "SALE" in tx_type or "SELL" in tx_type:
                side = "SHORT"
            else:
                continue
            trades.append({"trader_id": f"finviz_{insider.replace(' ', '_').lower()[:30]}", "symbol": sym, "side": side, "entry_price": price, "exit_price": 0.0, "entry_time": trade_date, "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": abs(value), "source": "finviz_insider", "trader_name": insider, "trader_title": relationship})
        logger.info(f"[Finviz] Parsed {len(trades)} insider trades")
    except Exception as e:
        logger.warning(f"[Finviz] Failed: {e}")
    return trades


# ═══════════════════════════════════════════════════════════════════
# SOURCE 6: UNUSUAL OPTIONS ACTIVITY (Barchart free page)
# ═══════════════════════════════════════════════════════════════════

def scrape_unusual_options() -> List[Dict]:
    """Scrape unusual options activity from free sources — signals institutional conviction."""
    trades = []
    logger.info("[Options] Fetching unusual activity...")
    try:
        resp = SESSION.get("https://www.barchart.com/options/unusual-activity/stocks?orderBy=volume&orderDir=desc", timeout=15)
        if resp.status_code != 200:
            logger.warning(f"[Options] Barchart HTTP {resp.status_code}")
            return trades
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("tr[data-ng-repeat], tr.bc-table-row, tbody tr")
        for row in rows[:50]:
            cols = row.find_all("td")
            if len(cols) < 8:
                continue
            sym = _normalize_symbol(cols[0].get_text(strip=True))
            if not _is_relevant_symbol(sym):
                continue
            option_type = cols[2].get_text(strip=True).upper() if len(cols) > 2 else ""
            volume = _safe_float(cols[5].get_text(strip=True)) if len(cols) > 5 else 0
            if "CALL" in option_type:
                side = "LONG"
            elif "PUT" in option_type:
                side = "SHORT"
            else:
                continue
            trades.append({"trader_id": "unusual_options_flow", "symbol": sym, "side": side, "entry_price": 0.0, "exit_price": 0.0, "entry_time": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": 0.0, "source": "unusual_options", "trader_name": "Options Flow", "trader_title": f"{option_type} Vol={volume}"})
        logger.info(f"[Options] Found {len(trades)} unusual options signals")
    except Exception as e:
        logger.warning(f"[Options] Barchart scrape failed: {e}")
    return trades


# ═══════════════════════════════════════════════════════════════════
# SOURCE 7: TRUMP INNER CIRCLE — Congress members who front-run policy
# ═══════════════════════════════════════════════════════════════════

# Politicians known to actively trade ahead of Trump policy announcements
TRUMP_CIRCLE_POLITICIANS = [
    "Tommy Tuberville", "Tuberville",
    "Marjorie Taylor Greene", "Taylor Greene",
    "Dan Crenshaw", "Crenshaw",
    "Mark Green",
    "Tim Burchett", "Burchett",
    "Michael McCaul", "McCaul",
    "Rick Scott",
    "Nancy Mace",
    "Josh Hawley", "Hawley",
    "Ted Cruz",
    "Jim Jordan",
    "Kevin Hern",
    "Mike Johnson",
    "Elise Stefanik",
    "JD Vance", "Vance",
]


def scrape_trump_circle_congress() -> List[Dict]:
    """Filter Capitol Trades for known Trump-aligned active traders."""
    trades = []
    logger.info("[TrumpCircle] Filtering Capitol Trades for inner circle politicians...")
    for page in range(1, 8):
        try:
            resp = SESSION.get(f"https://www.capitoltrades.com/trades?page={page}&pageSize=96&party=republican", timeout=20)
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
                is_inner_circle = any(name.lower() in politician.lower() for name in TRUMP_CIRCLE_POLITICIANS)
                if not is_inner_circle:
                    continue
                issuer_cell = cols[1].get_text(strip=True)
                tx_type = cols[6].get_text(strip=True).lower()
                size_str = cols[7].get_text(strip=True)
                price_str = cols[8].get_text(strip=True)
                trade_date_raw = cols[3].get_text(strip=True)
                sym_match = re.search(r"([A-Z]{1,5}):US", issuer_cell)
                if not sym_match:
                    sym_match = re.search(r"([A-Z]{1,5})$", issuer_cell.split()[-1] if issuer_cell.split() else "")
                sym = _normalize_symbol(sym_match.group(1)) if sym_match else ""
                if not sym:
                    continue
                if "buy" in tx_type or "purchase" in tx_type:
                    side = "LONG"
                elif "sell" in tx_type or "sale" in tx_type:
                    side = "SHORT"
                else:
                    continue
                amount = _parse_range_amount(size_str)
                price = _safe_float(price_str)
                trade_date = _parse_relative_date(trade_date_raw)
                party_match = re.search(r"(Republican|Democrat)", cols[0].get_text())
                party = party_match.group(1)[:1] if party_match else "R"
                clean_name = re.sub(r"(Republican|Democrat|House|Senate)", "", politician).strip()
                trades.append({"trader_id": f"trump_circle_{clean_name.replace(' ', '_').lower()[:30]}", "symbol": sym, "side": side, "entry_price": price, "exit_price": 0.0, "entry_time": trade_date, "exit_time": trade_date, "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": amount, "source": "trump_circle", "trader_name": clean_name, "trader_title": f"Trump Circle ({party})"})
            time.sleep(1)
        except Exception as e:
            logger.warning(f"[TrumpCircle] Page {page} failed: {e}")
    logger.info(f"[TrumpCircle] Found {len(trades)} trades from Trump inner circle")
    return trades


# ═══════════════════════════════════════════════════════════════════
# SOURCE 8: DJT/TMTG INSIDER TRADES (SEC EDGAR Form 4)
# ═══════════════════════════════════════════════════════════════════

TMTG_CIK = "0001849635"  # Trump Media & Technology Group


def scrape_djt_insiders() -> List[Dict]:
    """Scrape SEC EDGAR Form 4 filings for DJT/TMTG insiders (Trump, Nunes, etc)."""
    trades = []
    logger.info("[DJT] Fetching TMTG insider filings from SEC EDGAR...")
    edgar_headers = {"User-Agent": "TradingResearch research@example.com"}
    try:
        resp = SESSION.get(f"https://data.sec.gov/submissions/CIK{TMTG_CIK}.json", headers=edgar_headers, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"[DJT] EDGAR HTTP {resp.status_code}")
            return trades
        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        for i, form in enumerate(forms):
            if form != "4" and form != "4/A":
                continue
            if dates[i] < cutoff:
                continue
            acc_clean = accessions[i].replace("-", "")
            filing_url = f"https://data.sec.gov/Archives/edgar/data/{TMTG_CIK.lstrip('0')}/{acc_clean}/{primary_docs[i]}"
            try:
                resp2 = SESSION.get(filing_url, headers=edgar_headers, timeout=10)
                if resp2.status_code != 200:
                    continue
                form4_trades = _parse_form4_xml(resp2.text, dates[i])
                trades.extend(form4_trades)
                if form4_trades:
                    logger.info(f"[DJT] {dates[i]}: {len(form4_trades)} transactions from Form 4")
            except Exception as e2:
                logger.debug(f"[DJT] Failed to parse filing {dates[i]}: {e2}")
            time.sleep(0.3)
    except Exception as e:
        logger.warning(f"[DJT] EDGAR scrape failed: {e}")
    logger.info(f"[DJT] Total: {len(trades)} insider trades")
    return trades


def _parse_form4_xml(xml_text: str, filing_date: str) -> List[Dict]:
    """Parse SEC Form 4 XML for transaction details."""
    trades = []
    soup = BeautifulSoup(xml_text, "html.parser")
    reporter = soup.find(re.compile(r"rptownername", re.IGNORECASE))
    reporter_name = reporter.get_text(strip=True) if reporter else "Unknown"
    reporter_title_tag = soup.find(re.compile(r"officertitle", re.IGNORECASE))
    reporter_title = reporter_title_tag.get_text(strip=True) if reporter_title_tag else ""
    transactions = soup.find_all(re.compile(r"nonderivativetransaction|derivativetransaction", re.IGNORECASE))
    for tx in transactions:
        code_tag = tx.find(re.compile(r"transactioncode", re.IGNORECASE))
        shares_tag = tx.find(re.compile(r"transactionshares|transactiontotalvalue", re.IGNORECASE))
        price_tag = tx.find(re.compile(r"transactionpricepershare", re.IGNORECASE))
        date_tag = tx.find(re.compile(r"transactiondate", re.IGNORECASE))
        acq_disp_tag = tx.find(re.compile(r"transactionacquireddisposedcode", re.IGNORECASE))
        code = code_tag.find(re.compile(r"value", re.IGNORECASE)).get_text(strip=True) if code_tag else ""
        if code not in ("P", "S", "A", "D", "M"):
            continue
        acq_disp = ""
        if acq_disp_tag:
            val = acq_disp_tag.find(re.compile(r"value", re.IGNORECASE))
            acq_disp = val.get_text(strip=True) if val else ""
        if code == "P" or acq_disp == "A":
            side = "LONG"
        elif code == "S" or acq_disp == "D":
            side = "SHORT"
        else:
            continue
        shares = 0.0
        if shares_tag:
            val = shares_tag.find(re.compile(r"value", re.IGNORECASE))
            shares = _safe_float(val.get_text(strip=True)) if val else 0.0
        price = 0.0
        if price_tag:
            val = price_tag.find(re.compile(r"value", re.IGNORECASE))
            price = _safe_float(val.get_text(strip=True)) if val else 0.0
        tx_date = filing_date
        if date_tag:
            val = date_tag.find(re.compile(r"value", re.IGNORECASE))
            tx_date = val.get_text(strip=True) if val else filing_date
        value = shares * price
        trades.append({"trader_id": f"djt_{reporter_name.replace(' ', '_').lower()[:30]}", "symbol": "DJT", "side": side, "entry_price": price, "exit_price": 0.0, "entry_time": tx_date, "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": value, "source": "djt_insider", "trader_name": reporter_name, "trader_title": f"TMTG {reporter_title}"})
    return trades


# ═══════════════════════════════════════════════════════════════════
# SOURCE 9: WLFI ON-CHAIN (World Liberty Financial — Trump family DeFi)
# ═══════════════════════════════════════════════════════════════════

WLFI_WALLET = "0x5be9a4959308A0D0c43571a4483bBceb33cB44Bd"
# Top ERC-20 tokens to track (ticker → contract address)
WLFI_TOKENS = {
    "ETH": None,  # native ETH tracked separately
    "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "AAVE": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
    "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "ENA": "0x57e114B691Db790C35207b2e685D4A43181e6061",
    "ONDO": "0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3",
}


def scrape_wlfi_onchain() -> List[Dict]:
    """Track WLFI wallet transactions via multiple free block explorers."""
    trades = []
    logger.info("[WLFI] Fetching World Liberty Financial on-chain activity...")
    # Multiple known WLFI-associated addresses (verify periodically via Arkham/DeBank)
    wallets = [
        WLFI_WALLET,
        "0x0c11Ec46e527d1Ed882ee12EA801e48F3E9fB0E8",  # WLFI multisig reported by Arkham
    ]
    for wallet in wallets:
        try:
            # Blockscout API (free, no key)
            resp = SESSION.get(f"https://eth.blockscout.com/api/v2/addresses/{wallet}/token-transfers?type=ERC-20", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                for tx in items:
                    ts = tx.get("timestamp", "")[:19]
                    token = tx.get("token", {})
                    sym = token.get("symbol", "")
                    if not sym:
                        continue
                    decimals = int(token.get("decimals", "18") or "18")
                    total = tx.get("total", {})
                    val_raw = total.get("value", "0") if total else "0"
                    value = int(val_raw) / (10 ** decimals) if val_raw else 0
                    if value < 1:
                        continue
                    from_h = tx.get("from", {}).get("hash", "").lower() if isinstance(tx.get("from"), dict) else ""
                    is_outgoing = from_h == wallet.lower()
                    side = "SHORT" if is_outgoing else "LONG"
                    trades.append({"trader_id": "wlfi_treasury", "symbol": sym, "side": side, "entry_price": 0.0, "exit_price": 0.0, "entry_time": ts, "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": 0.0, "source": "wlfi_onchain", "trader_name": "WLFI Treasury (Trump)", "trader_title": f"{'Sent' if is_outgoing else 'Received'} {value:,.2f} {sym}"})
            # Also try ETH transactions
            resp2 = SESSION.get(f"https://eth.blockscout.com/api/v2/addresses/{wallet}/transactions", timeout=15)
            if resp2.status_code == 200:
                data2 = resp2.json()
                for tx in data2.get("items", []):
                    ts = tx.get("timestamp", "")[:19]
                    val_raw = tx.get("value", "0")
                    value_eth = int(val_raw) / 1e18 if val_raw else 0
                    if value_eth < 0.1:
                        continue
                    from_h = tx.get("from", {}).get("hash", "").lower() if isinstance(tx.get("from"), dict) else ""
                    is_outgoing = from_h == wallet.lower()
                    side = "SHORT" if is_outgoing else "LONG"
                    trades.append({"trader_id": "wlfi_treasury", "symbol": "ETH", "side": side, "entry_price": 0.0, "exit_price": 0.0, "entry_time": ts, "exit_time": "", "pnl": 0.0, "pnl_pct": 0.0, "leverage": 1.0, "position_size_usd": 0.0, "source": "wlfi_onchain", "trader_name": "WLFI Treasury (Trump)", "trader_title": f"{'Sent' if is_outgoing else 'Received'} {value_eth:.2f} ETH"})
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"[WLFI] Wallet {wallet[:10]}... failed: {e}")
    logger.info(f"[WLFI] Total: {len(trades)} on-chain transactions")
    return trades


# ═══════════════════════════════════════════════════════════════════
# AGGREGATION + SCORING
# ═══════════════════════════════════════════════════════════════════

def _is_proven_politician(trader_name: str) -> bool:
    """Check if trader is in the backtest-proven list (55%+ WR at 30d)."""
    if not trader_name:
        return False
    name_lower = trader_name.lower().strip()
    for proven in PROVEN_POLITICIANS:
        if proven in name_lower or name_lower in proven:
            return True
    return False


def score_conviction(all_trades: List[Dict]) -> List[Dict]:
    """Score each symbol by conviction — only proven politicians + non-congress sources count.
    Backtest showed: congress sells = anti-signal (46.6% WR), buys = edge (55% WR).
    Only inject from PROVEN_POLITICIANS (55%+ WR at 30d from backtest)."""
    symbol_signals = {}
    for t in all_trades:
        sym = t["symbol"]
        side = t["side"]
        source = t["source"]
        trader = t.get("trader_name", "")
        size = t.get("position_size_usd", 0) or 0
        # For congress/trump_circle sources: only count proven politicians
        if source in ("congress_house", "congress_senate", "trump_circle"):
            if not _is_proven_politician(trader):
                continue
            # Congress SELLS are anti-signal (46.6% WR) — skip for injection scoring
            if side == "SHORT":
                continue
        if sym not in symbol_signals:
            symbol_signals[sym] = {"LONG": [], "SHORT": []}
        symbol_signals[sym][side].append({"source": source, "trader": trader, "size": size, "date": t.get("entry_time", ""), "proven": _is_proven_politician(trader)})
    scored = []
    for sym, signals in symbol_signals.items():
        long_sources = set(s["source"] for s in signals["LONG"])
        short_sources = set(s["source"] for s in signals["SHORT"])
        long_value = sum(s["size"] for s in signals["LONG"])
        short_value = sum(s["size"] for s in signals["SHORT"])
        long_proven = sum(1 for s in signals["LONG"] if s.get("proven"))
        if len(long_sources) >= 2 or long_value > 100000:
            scored.append({"symbol": sym, "side": "LONG", "conviction_sources": len(long_sources), "total_value": long_value, "sources": list(long_sources), "traders": [s["trader"] for s in signals["LONG"][:5]], "proven_count": long_proven})
        # Shorts: only from non-congress sources (insider, finviz, options)
        short_non_congress = [s for s in signals["SHORT"] if s["source"] not in ("congress_house", "congress_senate", "trump_circle")]
        if len(set(s["source"] for s in short_non_congress)) >= 2 or short_value > 100000:
            scored.append({"symbol": sym, "side": "SHORT", "conviction_sources": len(short_sources), "total_value": short_value, "sources": list(short_sources), "traders": [s["trader"] for s in signals["SHORT"][:5]], "proven_count": 0})
    scored.sort(key=lambda x: (-x.get("proven_count", 0), -x["conviction_sources"], -x["total_value"]))
    return scored


# ═══════════════════════════════════════════════════════════════════
# MAIN — EXPORT
# ═══════════════════════════════════════════════════════════════════

def run_all_sources(sources: Optional[List[str]] = None) -> Tuple[List[Dict], Path]:
    """Run all (or selected) sources, merge, score, and export CSV."""
    all_trades = []
    source_map = {
        "congress": scrape_congress_trades,
        "insider": scrape_insider_trades,
        "13f": scrape_13f_filings,
        "youtube": scrape_youtube_stock_traders,
        "finviz": scrape_finviz_insiders,
        "options": scrape_unusual_options,
        "trump_circle": scrape_trump_circle_congress,
        "djt": scrape_djt_insiders,
        "wlfi": scrape_wlfi_onchain,
    }
    if sources is None:
        sources = list(source_map.keys())
    for src in sources:
        if src in source_map:
            try:
                result = source_map[src]()
                all_trades.extend(result)
                logger.info(f"[{src}] → {len(result)} trades")
            except Exception as e:
                logger.error(f"[{src}] FAILED: {e}")
    # Export
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = DATA_DIR / f"{today}_trades.csv"
    if all_trades:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_trades)
        logger.info(f"Exported {len(all_trades)} trades → {csv_path}")
    # Score conviction
    conviction = score_conviction(all_trades)
    if conviction:
        conv_path = DATA_DIR / f"{today}_conviction.json"
        with open(conv_path, "w") as f:
            json.dump(conviction, f, indent=2)
        logger.info(f"Conviction scores: {len(conviction)} symbols → {conv_path}")
        logger.info("TOP CONVICTION:")
        for c in conviction[:10]:
            logger.info(f"  {c['symbol']} {c['side']}: {c['conviction_sources']} sources, ${c['total_value']:,.0f} from {c['sources']}")
    # Save cumulative merged file
    merged_path = DATA_DIR / "all_stock_trades_merged.csv"
    existing_rows = []
    existing_keys = set()
    if merged_path.exists():
        try:
            with open(merged_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = f"{row.get('trader_id', '')}_{row.get('symbol', '')}_{row.get('entry_time', '')}_{row.get('side', '')}"
                    if key not in existing_keys:
                        existing_keys.add(key)
                        existing_rows.append(row)
        except Exception:
            pass
    new_count = 0
    for t in all_trades:
        key = f"{t.get('trader_id', '')}_{t.get('symbol', '')}_{t.get('entry_time', '')}_{t.get('side', '')}"
        if key not in existing_keys:
            existing_keys.add(key)
            existing_rows.append(t)
            new_count += 1
    with open(merged_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows)
    logger.info(f"Merged: {len(existing_rows)} total ({new_count} new) → {merged_path}")
    return all_trades, csv_path


# ═══════════════════════════════════════════════════════════════════
# INJECTION — write to same news_injections.json that tradier_rankings reads
# ═══════════════════════════════════════════════════════════════════

def _load_injections() -> dict:
    try:
        if INJECTION_FILE.exists():
            with open(INJECTION_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"active": [], "history": []}


def _save_injections(data: dict):
    try:
        with open(INJECTION_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"[INJECT] Save error: {e}")


def inject_conviction_symbols(conviction: List[Dict]) -> List[Dict]:
    """Inject high-conviction symbols into tradier ranking symbol lists.
    Uses the SAME news_injections.json format as ez_news_scanner.py so that
    tradier_rankings.py picks them up via _merge_news_injections(). This
    scanner must never write the generated symbols_trb_* files directly."""
    if not conviction:
        return []
    injections = _load_injections()
    now_iso = datetime.now(timezone.utc).isoformat()
    already_active = {(i["symbol"], i.get("account", ""), i["side"]) for i in injections["active"]}
    new_injections = []
    long_picks = [c for c in conviction if c["side"] == "LONG"][:MAX_INJECT_PICKS]
    short_picks = [c for c in conviction if c["side"] == "SHORT"][:MAX_INJECT_PICKS]
    for acct in STOCK_INJECT_ACCOUNTS:
        for side_key, picks in [("long", long_picks), ("short", short_picks)]:
            added = []
            direction = "LONG" if side_key == "long" else "SHORT"
            for pick in picks:
                sym = pick["symbol"].upper()
                if sym not in OUR_SYMBOLS:
                    logger.warning(
                        f"[INJECT] Blocked {sym}: not present in symbols_tradier.json"
                    )
                    continue
                sources_count = pick.get("conviction_sources", 0)
                total_val = pick.get("total_value", 0)
                if sources_count < MIN_INJECT_CONVICTION and total_val < MIN_INJECT_VALUE:
                    continue
                key = (sym, acct, direction)
                if key in already_active:
                    continue
                added.append(sym)
                conviction_score = min(1.0, sources_count / 4.0)
                reason = f"stock_scan: {sources_count} sources ({', '.join(pick.get('sources', [])[:3])}), ${total_val:,.0f}"
                new_injections.append({"symbol": sym, "account": acct, "side": direction, "file": f"symbols_{acct}_{side_key}.json", "injected_at": now_iso, "conviction": conviction_score, "reason": reason[:80], "_is_stock": True, "was_new": True, "_source": "stock_trader_scanner"})
            if added:
                logger.info(
                    f"[INJECT] {acct}_{side_key}: queued {len(added)} allowlisted "
                    f"stocks for tradier_rankings: {added}"
                )
    if new_injections:
        injections["active"].extend(new_injections)
        _save_injections(injections)
        logger.info(f"[INJECT] {len(new_injections)} new symbols injected into news_injections.json")
    else:
        logger.info("[INJECT] No symbols met injection threshold this cycle")
    return new_injections


def inject_sentiment_redis(conviction: List[Dict]):
    """Write conviction scores to Redis news_sentiment_stocks key.
    Same key that tradier_rankings.py reads via _load_news_sentiment_tradier()."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
        existing = {}
        raw = r.get("news_sentiment_stocks")
        if raw:
            existing = json.loads(raw)
        for c in conviction:
            sym = c["symbol"]
            direction = 1.0 if c["side"] == "LONG" else -1.0
            score = min(1.0, c.get("conviction_sources", 1) / 4.0) * direction
            if sym not in existing or abs(score) > abs(existing.get(sym, 0)):
                existing[sym] = score
        r.setex("news_sentiment_stocks", 14400, json.dumps(existing))
        sentiment_file = config.DATA_DIR / "news_sentiment_stocks.json"
        with open(sentiment_file, "w") as f:
            json.dump(existing, f, indent=2)
        logger.info(f"[REDIS] Updated news_sentiment_stocks: {len(existing)} symbols (TTL 4h)")
    except Exception as e:
        logger.warning(f"[REDIS] Sentiment write failed (non-fatal): {e}")


def main():
    parser = argparse.ArgumentParser(description="Stock Trader Scanner — discover successful equity trader strategies")
    parser.add_argument("--source", type=str, help="Single source: congress, insider, 13f, youtube, finviz, options")
    parser.add_argument("--full", action="store_true", help="Run all sources with deep scan")
    parser.add_argument("--no-inject", action="store_true", help="Skip injection into live trading system")
    args = parser.parse_args()
    sources = [args.source] if args.source else None
    trades, csv_path = run_all_sources(sources)
    if not args.no_inject:
        conviction = score_conviction(trades)
        injected = inject_conviction_symbols(conviction)
        inject_sentiment_redis(conviction)
        if injected:
            logger.info(f"INJECTED {len(injected)} symbols: {[i['symbol'] for i in injected]}")
    logger.info(f"DONE — {len(trades)} trades from {len(set(t.get('source', '') for t in trades))} sources → {csv_path}")


if __name__ == "__main__":
    main()
