# pylint: disable=W,C,R,I
"""News sentiment scanner v3 — legit free sources + paper trading validation log.
Sources: SentiCrypt (unlimited/free), Alpha Vantage NEWS_SENTIMENT (25/day free), Finnhub (60/min free),
Alternative.me Fear&Greed (free), RSS feeds (CoinDesk, CoinTelegraph, Reddit RSS — free).
Paper trades log at data/news_paper_trades.json to validate signal quality before going live."""
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import aiohttp
from config import Config
from utils import get_simple_redis_manager, load_environment_from_gpg, safe_fetch_float, setup_logger

config = Config()
logger = setup_logger('ez_news_scanner', str(config.LOG_DIR / 'ez_news_scanner.log'), logging.INFO)
load_environment_from_gpg(logger)
BASE_PATH = Path(os.getenv('EZ_BASE_PATH', str(config.BASE_PATH)))
DATA_DIR = BASE_PATH / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
CRYPTO_ALIASES = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP", "CARDANO": "ADA", "DOGECOIN": "DOGE", "POLKADOT": "DOT", "AVALANCHE": "AVAX", "CHAINLINK": "LINK", "POLYGON": "MATIC", "LITECOIN": "LTC", "UNISWAP": "UNI", "SHIBA": "SHIB", "PEPE": "PEPE", "BONK": "BONK", "FLOKI": "FLOKI", "WORLDCOIN": "WLD", "ARBITRUM": "ARB", "OPTIMISM": "OP", "CELESTIA": "TIA", "SEI": "SEI", "SUI": "SUI", "APTOS": "APT", "INJECTIVE": "INJ", "JUPITER": "JUP", "RENDER": "RNDR", "NEAR": "NEAR", "FILECOIN": "FIL", "COSMOS": "ATOM", "ALGORAND": "ALGO", "TONCOIN": "TON", "TRON": "TRX"}
STOCK_TICKERS_PATTERN = re.compile(r'\$([A-Z]{1,5})\b')
CRYPTO_TICKER_PATTERN = re.compile(r'(?:\$|#)([A-Z]{2,10})\b')
HTML_TAG_RE = re.compile(r'<[^>]+>')
shutdown_event = asyncio.Event()
PAPER_TRADES_FILE = DATA_DIR / 'news_paper_trades.json'
PAPER_SUMMARY_FILE = DATA_DIR / 'news_paper_summary.json'
# --- Source URLs ---
SENTICRYPT_URL = 'https://api.senticrypt.com/v2/all.json'
FEAR_GREED_URL = 'https://api.alternative.me/fng/?limit=1'
ALPHAVANTAGE_URL = 'https://www.alphavantage.co/query'
FINNHUB_NEWS_URL = 'https://finnhub.io/api/v1/news'
FINNHUB_CRYPTO_URL = 'https://finnhub.io/api/v1/news'
CRYPTO_RSS_FEEDS = [
    ('https://www.coindesk.com/arc/outboundfeeds/rss/', 'coindesk'),
    ('https://cointelegraph.com/rss', 'cointelegraph'),
    ('https://decrypt.co/feed', 'decrypt'),
    ('https://bitcoinmagazine.com/.rss/full/', 'btcmag'),
    ('https://www.reddit.com/r/CryptoCurrency/.rss', 'reddit_crypto'),
    ('https://www.reddit.com/r/Bitcoin/.rss', 'reddit_btc'),
]
STOCK_RSS_FEEDS = [
    ('https://www.reddit.com/r/wallstreetbets/.rss', 'reddit_wsb'),
    ('https://www.reddit.com/r/stocks/.rss', 'reddit_stocks'),
]
# Paper trading thresholds
PAPER_ENTRY_THRESHOLD = 0.25
PAPER_EXIT_HOURS = 24
PAPER_POSITION_SIZE_USD = 1000.0

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    vader = SentimentIntensityAnalyzer()
except ImportError:
    logger.warning("vaderSentiment not installed — using keyword scoring")
    vader = None

def build_coin_map(symbols: List[str]) -> Dict[str, str]:
    coin_map = {}
    for sym in symbols:
        for suffix in ('USDT', 'USDC'):
            if sym.endswith(suffix):
                base = sym[:-len(suffix)]
                if base not in coin_map: coin_map[base] = sym
                break
    for alias, code in CRYPTO_ALIASES.items():
        if code in coin_map: coin_map[alias] = coin_map[code]
    return coin_map

def load_symbols() -> List[str]:
    try:
        with open(BASE_PATH / 'symbols.json', 'r') as f: return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load symbols.json: {e}")
        return []

def load_stock_symbols() -> List[str]:
    try:
        for fname in ('symbols_trb_long.json', 'symbols_tra_long.json'):
            p = DATA_DIR / 'tradier' / fname
            if p.exists():
                with open(p, 'r') as f: return json.load(f)
    except Exception as e:
        logger.debug(f"Failed to load stock symbols: {e}")
    return []

def vader_score(text: str) -> float:
    if vader: return vader.polarity_scores(text)['compound']
    text_lower = text.lower()
    pos = sum(1 for w in ('surge', 'soar', 'bull', 'rally', 'gain', 'pump', 'moon', 'breakout', 'upgrade', 'adoption', 'partnership', 'launch', 'approval', 'record', 'profit', 'growth') if w in text_lower)
    neg = sum(1 for w in ('crash', 'dump', 'bear', 'drop', 'fall', 'hack', 'scam', 'ban', 'fraud', 'lawsuit', 'sec', 'investigation', 'exploit', 'rug', 'bankrupt', 'loss', 'fear') if w in text_lower)
    if pos + neg == 0: return 0.0
    return (pos - neg) / (pos + neg)

def strip_html(text: str) -> str:
    return unescape(HTML_TAG_RE.sub('', text)).strip()

def extract_symbols_from_text(text: str, coin_map: Dict[str, str]) -> List[str]:
    found = set()
    for m in CRYPTO_TICKER_PATTERN.finditer(text):
        code = m.group(1)
        if code in coin_map: found.add(coin_map[code])
    for word in text.upper().split():
        word_clean = re.sub(r'[^A-Z]', '', word)
        if len(word_clean) >= 2 and word_clean in coin_map: found.add(coin_map[word_clean])
    return list(found)

def extract_stock_tickers(text: str, stock_set: set) -> List[str]:
    found = set()
    for m in STOCK_TICKERS_PATTERN.finditer(text):
        ticker = m.group(1)
        if ticker in stock_set: found.add(ticker)
    for word in text.upper().split():
        word_clean = re.sub(r'[^A-Z]', '', word)
        if len(word_clean) >= 2 and word_clean in stock_set: found.add(word_clean)
    return list(found)

def parse_rss_date(date_str: str) -> Optional[datetime]:
    for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%f%z'):
        try: return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc) if 'z' not in fmt.lower() or '+' in date_str or 'Z' in date_str else datetime.strptime(date_str.strip(), fmt)
        except (ValueError, TypeError): continue
    return None

# ============================================================
# SOURCE POLLERS
# ============================================================

async def poll_senticrypt(session: aiohttp.ClientSession) -> Dict[str, float]:
    """SentiCrypt — completely free, no key, unlimited. Returns BTC sentiment (-1 to 1) updated every 2h."""
    try:
        async with session.get(SENTICRYPT_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.debug(f"[SENTICRYPT] HTTP {resp.status}")
                return {}
            data = await resp.json(content_type=None)
            if isinstance(data, list) and data:
                latest = data[-1] if data else {}
                score = float(latest.get('mean', 0.0))
                logger.info(f"[SENTICRYPT] BTC sentiment: {score:+.4f} (from {len(data)} datapoints)")
                return {'BTC': score}
            elif isinstance(data, dict):
                score = float(data.get('mean', data.get('sentiment', 0.0)))
                return {'BTC': score}
    except Exception as e:
        logger.warning(f"[SENTICRYPT] Error: {e}")
    return {}

async def poll_fear_greed(session: aiohttp.ClientSession) -> Optional[dict]:
    """Alternative.me Fear & Greed Index — free, no key."""
    try:
        async with session.get(FEAR_GREED_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200: return None
            data = await resp.json()
            fg = data.get('data', [{}])[0]
            value = int(fg.get('value', 50))
            classification = fg.get('value_classification', 'Neutral')
            score = (value - 50) / 50.0
            logger.info(f"[FEAR_GREED] {classification} ({value}/100) → score={score:+.2f}")
            return {'value': value, 'classification': classification, 'score': score}
    except Exception as e:
        logger.warning(f"[FEAR_GREED] Error: {e}")
        return None

async def poll_alphavantage(session: aiohttp.ClientSession, tickers: str = 'CRYPTO:BTC,CRYPTO:ETH,CRYPTO:SOL') -> List[dict]:
    """Alpha Vantage NEWS_SENTIMENT — free key, 25 calls/day, AI/ML sentiment scores."""
    api_key = os.getenv('ALPHAVANTAGE_API_KEY', '')
    if not api_key: return []
    articles = []
    try:
        params = {'function': 'NEWS_SENTIMENT', 'tickers': tickers, 'sort': 'LATEST', 'limit': 50, 'apikey': api_key}
        async with session.get(ALPHAVANTAGE_URL, timeout=aiohttp.ClientTimeout(total=15), params=params) as resp:
            if resp.status != 200:
                logger.debug(f"[ALPHAVANTAGE] HTTP {resp.status}")
                return []
            data = await resp.json()
            if 'Note' in data or 'Information' in data:
                logger.warning(f"[ALPHAVANTAGE] Rate limited: {data.get('Note', data.get('Information', ''))[:100]}")
                return []
            for item in data.get('feed', []):
                title = item.get('title', '')
                published = item.get('time_published', '')
                overall_sentiment = float(item.get('overall_sentiment_score', 0.0))
                for ticker_info in item.get('ticker_sentiment', []):
                    ticker = ticker_info.get('ticker', '')
                    ticker_score = float(ticker_info.get('ticker_sentiment_score', 0.0))
                    relevance = float(ticker_info.get('relevance_score', 0.0))
                    if relevance < 0.1: continue
                    articles.append({'symbol': ticker, 'text': title[:200], 'score': ticker_score, 'engagement': max(1, int(relevance * 20)), 'source': 'alphavantage', 'published': published, 'relevance': relevance, 'av_label': ticker_info.get('ticker_sentiment_label', '')})
        logger.info(f"[ALPHAVANTAGE] {len(articles)} ticker-sentiments from news feed")
    except Exception as e:
        logger.warning(f"[ALPHAVANTAGE] Error: {e}")
    return articles

async def poll_finnhub(session: aiohttp.ClientSession, category: str = 'crypto') -> List[dict]:
    """Finnhub — free key, 60 req/min. category: 'crypto', 'general', 'forex', 'merger'."""
    api_key = os.getenv('FINNHUB_API_KEY', '')
    if not api_key: return []
    articles = []
    try:
        params = {'category': category, 'token': api_key}
        async with session.get(FINNHUB_NEWS_URL, timeout=aiohttp.ClientTimeout(total=15), params=params) as resp:
            if resp.status != 200:
                logger.debug(f"[FINNHUB:{category}] HTTP {resp.status}")
                return []
            data = await resp.json()
            for item in data[:50]:
                title = item.get('headline', '')
                summary = item.get('summary', '')[:300]
                combined = f"{title} {summary}"
                published = datetime.fromtimestamp(item.get('datetime', 0), tz=timezone.utc).isoformat() if item.get('datetime') else ''
                sentiment_val = vader_score(combined)
                related = [r.strip().upper() for r in item.get('related', '').split(',') if r.strip()]
                source = item.get('source', 'finnhub')
                for r in related:
                    if r: articles.append({'symbol': r, 'text': title[:200], 'score': sentiment_val, 'engagement': 8, 'source': f'finnhub_{source}', 'published': published})
                if not related:
                    articles.append({'symbol': '_MARKET_', 'text': title[:200], 'score': sentiment_val, 'engagement': 5, 'source': f'finnhub_{source}', 'published': published})
        logger.info(f"[FINNHUB:{category}] {len(articles)} mentions")
    except Exception as e:
        logger.warning(f"[FINNHUB:{category}] Error: {e}")
    return articles

async def poll_rss_feeds(session: aiohttp.ClientSession, feeds: List[Tuple[str, str]], coin_map: Dict[str, str], stock_set: set, is_crypto: bool = True) -> List[dict]:
    articles = []
    for feed_url, source in feeds:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; ez_news_scanner/3.0)'}
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=12), headers=headers) as resp:
                if resp.status != 200: continue
                text = await resp.text()
                root = ET.fromstring(text)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                items = root.findall('.//item') or root.findall('.//atom:entry', ns)
                for item in items[:40]:
                    title_el = item.find('title') or item.find('atom:title', ns)
                    title = strip_html(title_el.text or '') if title_el is not None and title_el.text else ''
                    desc_el = item.find('description') or item.find('atom:content', ns) or item.find('atom:summary', ns)
                    desc = strip_html(desc_el.text or '')[:300] if desc_el is not None and desc_el.text else ''
                    combined = f"{title} {desc}"
                    if not combined.strip(): continue
                    pub_el = item.find('pubDate') or item.find('atom:updated', ns) or item.find('atom:published', ns)
                    pub_dt = parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else None
                    published = pub_dt.isoformat() if pub_dt else ''
                    sentiment = vader_score(combined)
                    if is_crypto:
                        for sym in extract_symbols_from_text(combined, coin_map):
                            articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment, 'engagement': 5, 'source': f'rss_{source}', 'published': published})
                    else:
                        for t in extract_stock_tickers(combined, stock_set):
                            articles.append({'symbol': t, 'text': title[:200], 'score': sentiment, 'engagement': 5, 'source': f'rss_{source}', 'published': published})
        except ET.ParseError: pass
        except Exception as e:
            logger.debug(f"[RSS:{source}] Error: {e}")
    logger.info(f"[RSS] {len(articles)} mentions from {len(feeds)} feeds")
    return articles

# ============================================================
# AGGREGATION
# ============================================================

def aggregate_scores(articles: List[dict], senticrypt: Dict[str, float], coin_map: Dict[str, str], decay_hours: int = 4, min_articles: int = 2, fear_greed: Optional[dict] = None) -> Dict[str, float]:
    now = datetime.now(timezone.utc)
    per_symbol: Dict[str, List[Tuple[float, float]]] = {}
    fg_bias = fear_greed['score'] * 0.1 if fear_greed else 0.0
    for a in articles:
        sym = a['symbol']
        if sym == '_MARKET_': continue
        if sym.startswith('CRYPTO:'): sym = coin_map.get(sym.replace('CRYPTO:', ''), sym)
        score = a.get('score', 0.0)
        engagement = a.get('engagement', 1)
        pub = a.get('published', '')
        age_hours = decay_hours
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub.replace('Z', '+00:00').replace('T', 'T'))
                age_hours = max(0.01, (now - pub_dt).total_seconds() / 3600)
            except Exception: pass
        decay = max(0.1, 1.0 - (age_hours / decay_hours)) if age_hours < decay_hours else 0.1
        weight = decay * min(engagement, 1000) ** 0.3
        per_symbol.setdefault(sym, []).append((score * weight, weight))
    for coin_code, sc_score in senticrypt.items():
        sym = coin_map.get(coin_code, None)
        if sym:
            per_symbol.setdefault(sym, []).append((sc_score * 5.0, 5.0))
    result = {}
    for sym, weighted in per_symbol.items():
        if len(weighted) < min_articles: continue
        total_weight = sum(w for _, w in weighted)
        if total_weight <= 0: continue
        raw = sum(s for s, _ in weighted) / total_weight + fg_bias
        result[sym] = max(-1.0, min(1.0, raw))
    return result

# ============================================================
# PAPER TRADING LOG
# ============================================================

def load_paper_trades() -> dict:
    try:
        if PAPER_TRADES_FILE.exists():
            with open(PAPER_TRADES_FILE, 'r') as f: return json.load(f)
    except Exception as e:
        logger.warning(f"[PAPER] Load error: {e}")
    return {'open': [], 'closed': [], 'stats': {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pnl_pct': 0.0, 'avg_pnl_pct': 0.0}}

def save_paper_trades(trades: dict):
    try:
        with open(PAPER_TRADES_FILE, 'w') as f: json.dump(trades, f, indent=2)
    except Exception as e:
        logger.error(f"[PAPER] Save error: {e}")

async def get_current_price(session: aiohttp.ClientSession, symbol: str) -> float:
    """Get price from Binance public API (no key needed) or Redis."""
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data.get('price', 0.0))
    except Exception: pass
    return 0.0

async def update_paper_trades(session: aiohttp.ClientSession, scores: Dict[str, float], paper: dict):
    """Open new paper trades on strong signals, close expired ones, track P&L."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    existing_syms = {t['symbol'] for t in paper['open']}
    for sym, score in scores.items():
        if sym in existing_syms: continue
        if abs(score) < PAPER_ENTRY_THRESHOLD: continue
        price = await get_current_price(session, sym)
        if price <= 0: continue
        side = 'LONG' if score > 0 else 'SHORT'
        trade = {'symbol': sym, 'side': side, 'entry_price': price, 'entry_time': now_iso, 'sentiment_score': round(score, 4), 'size_usd': PAPER_POSITION_SIZE_USD, 'qty': round(PAPER_POSITION_SIZE_USD / price, 8)}
        paper['open'].append(trade)
        logger.info(f"[PAPER_OPEN] {side} {sym} @ {price:.6f} (sentiment={score:+.3f})")
    to_close = []
    for i, trade in enumerate(paper['open']):
        entry_time = datetime.fromisoformat(trade['entry_time'])
        hours_held = (now - entry_time).total_seconds() / 3600
        if hours_held < PAPER_EXIT_HOURS:
            current_price = await get_current_price(session, trade['symbol'])
            if current_price > 0: trade['current_price'] = current_price
            continue
        current_price = await get_current_price(session, trade['symbol'])
        if current_price <= 0: continue
        entry_price = trade['entry_price']
        if trade['side'] == 'LONG':
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - current_price) / entry_price) * 100
        trade['exit_price'] = current_price
        trade['exit_time'] = now_iso
        trade['pnl_pct'] = round(pnl_pct, 4)
        trade['pnl_usd'] = round(PAPER_POSITION_SIZE_USD * pnl_pct / 100, 2)
        trade['hours_held'] = round(hours_held, 1)
        paper['closed'].append(trade)
        to_close.append(i)
        won = pnl_pct > 0
        paper['stats']['total_trades'] += 1
        paper['stats']['wins'] += (1 if won else 0)
        paper['stats']['losses'] += (0 if won else 1)
        paper['stats']['total_pnl_pct'] = round(paper['stats']['total_pnl_pct'] + pnl_pct, 4)
        paper['stats']['avg_pnl_pct'] = round(paper['stats']['total_pnl_pct'] / paper['stats']['total_trades'], 4) if paper['stats']['total_trades'] > 0 else 0.0
        logger.info(f"[PAPER_CLOSE] {trade['side']} {trade['symbol']}: entry={entry_price:.6f} exit={current_price:.6f} pnl={pnl_pct:+.2f}% (${trade['pnl_usd']:+.2f}) held={hours_held:.1f}h")
    for i in sorted(to_close, reverse=True):
        paper['open'].pop(i)
    for trade in paper['open']:
        if 'current_price' in trade:
            ep = trade['entry_price']
            cp = trade['current_price']
            trade['unrealized_pnl_pct'] = round(((cp - ep) / ep * 100) if trade['side'] == 'LONG' else ((ep - cp) / ep * 100), 4)
    save_paper_trades(paper)
    save_paper_summary(paper)

def save_paper_summary(paper: dict):
    """Save a human-readable summary."""
    s = paper['stats']
    win_rate = (s['wins'] / s['total_trades'] * 100) if s['total_trades'] > 0 else 0
    open_unrealized = sum(t.get('unrealized_pnl_pct', 0) for t in paper['open'])
    summary = {'updated': datetime.now(timezone.utc).isoformat(), 'total_closed_trades': s['total_trades'], 'wins': s['wins'], 'losses': s['losses'], 'win_rate_pct': round(win_rate, 1), 'total_pnl_pct': s['total_pnl_pct'], 'avg_pnl_per_trade_pct': s['avg_pnl_pct'], 'open_positions': len(paper['open']), 'open_unrealized_pnl_pct': round(open_unrealized, 2), 'open_trades': [{'symbol': t['symbol'], 'side': t['side'], 'entry': t['entry_price'], 'sentiment': t['sentiment_score'], 'unrealized_pnl': t.get('unrealized_pnl_pct', 0)} for t in paper['open'][:20]], 'last_10_closed': [{'symbol': t['symbol'], 'side': t['side'], 'pnl_pct': t['pnl_pct'], 'pnl_usd': t['pnl_usd'], 'hours': t['hours_held']} for t in paper['closed'][-10:]]}
    try:
        with open(PAPER_SUMMARY_FILE, 'w') as f: json.dump(summary, f, indent=2)
    except Exception: pass

# ============================================================
# REDIS + FALLBACK
# ============================================================

async def publish_to_redis(scores: Dict[str, float], fear_greed: Optional[dict], redis_mgr) -> bool:
    try:
        bulk_json = json.dumps(scores)
        meta = {'last_poll': datetime.now(timezone.utc).isoformat(), 'total_symbols': len(scores), 'avg_score': sum(scores.values()) / max(1, len(scores)), 'sources': 'senticrypt+alphavantage+finnhub+rss+fear_greed'}
        if fear_greed: meta['fear_greed'] = fear_greed
        meta_json = json.dumps(meta)
        if redis_mgr and hasattr(redis_mgr, 'connections'):
            for name, conn in redis_mgr.connections.items():
                if conn is None: continue
                try:
                    await conn.setex('news_sentiment_bulk', 14400, bulk_json)
                    await conn.setex('news_sentiment_meta', 14400, meta_json)
                except Exception as e:
                    logger.debug(f"Redis {name} publish failed: {e}")
        return True
    except Exception as e:
        logger.error(f"[REDIS] Publish error: {e}")
        return False

async def save_fallback(scores: Dict[str, float]):
    try:
        with open(DATA_DIR / 'news_sentiment.json', 'w') as f: json.dump(scores, f)
    except Exception as e:
        logger.error(f"[FALLBACK] Save error: {e}")

# ============================================================
# MAIN LOOP
# ============================================================

async def main_loop():
    logger.info("=== ez_news_scanner v3 starting (SentiCrypt + AlphaVantage + Finnhub + RSS + F&G + Paper Trading) ===")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_set = set(load_stock_symbols())
    logger.info(f"[SCANNER] Loaded {len(coin_map)} coin mappings, {len(stock_set)} stock tickers")
    redis_mgr = await get_simple_redis_manager()
    paper = load_paper_trades()
    logger.info(f"[PAPER] Loaded {len(paper['open'])} open, {len(paper['closed'])} closed trades (win_rate={paper['stats'].get('wins',0)}/{paper['stats'].get('total_trades',0)})")
    rss_interval = 180
    senticrypt_interval = 7200
    alphavantage_interval = 3600
    finnhub_interval = 300
    fg_interval = 1800
    paper_interval = 300
    last = {'rss': 0.0, 'senticrypt': 0.0, 'alphavantage': 0.0, 'finnhub': 0.0, 'fg': 0.0, 'paper': 0.0}
    fear_greed_data = None
    senticrypt_data = {}
    async with aiohttp.ClientSession() as session:
        while not shutdown_event.is_set():
            try:
                now = time.time()
                all_articles = []
                if (now - last['senticrypt']) >= senticrypt_interval:
                    senticrypt_data = await poll_senticrypt(session)
                    last['senticrypt'] = now
                if (now - last['fg']) >= fg_interval:
                    fear_greed_data = await poll_fear_greed(session)
                    last['fg'] = now
                if (now - last['rss']) >= rss_interval:
                    all_articles.extend(await poll_rss_feeds(session, CRYPTO_RSS_FEEDS, coin_map, stock_set, is_crypto=True))
                    all_articles.extend(await poll_rss_feeds(session, STOCK_RSS_FEEDS, coin_map, stock_set, is_crypto=False))
                    last['rss'] = now
                if (now - last['alphavantage']) >= alphavantage_interval:
                    top_coins = ','.join(f'CRYPTO:{c}' for c in list(coin_map.keys())[:10] if len(c) <= 5 and c not in CRYPTO_ALIASES)
                    av_arts = await poll_alphavantage(session, tickers=top_coins)
                    all_articles.extend(av_arts)
                    last['alphavantage'] = now
                if (now - last['finnhub']) >= finnhub_interval:
                    all_articles.extend(await poll_finnhub(session, category='crypto'))
                    all_articles.extend(await poll_finnhub(session, category='general'))
                    last['finnhub'] = now
                if all_articles or senticrypt_data:
                    scores = aggregate_scores(all_articles, senticrypt_data, coin_map, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=config.NEWS_SENTIMENT_MIN_ARTICLES, fear_greed=fear_greed_data)
                    await publish_to_redis(scores, fear_greed_data, redis_mgr)
                    await save_fallback(scores)
                    pos_count = sum(1 for v in scores.values() if v > 0.1)
                    neg_count = sum(1 for v in scores.values() if v < -0.1)
                    fg_str = f"fg={fear_greed_data['value']}" if fear_greed_data else 'fg=?'
                    logger.info(f"[SCANNER] {len(all_articles)} articles → {len(scores)} symbols ({pos_count} bull, {neg_count} bear, {fg_str})")
                    if (now - last['paper']) >= paper_interval:
                        await update_paper_trades(session, scores, paper)
                        last['paper'] = now
                        s = paper['stats']
                        if s['total_trades'] > 0:
                            logger.info(f"[PAPER_STATS] {s['total_trades']} trades | W:{s['wins']} L:{s['losses']} ({s['wins']/s['total_trades']*100:.0f}%) | avg={s['avg_pnl_pct']:+.2f}% | total={s['total_pnl_pct']:+.2f}% | open={len(paper['open'])}")
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"[SCANNER] Main loop error: {e}", exc_info=True)
                await asyncio.sleep(60)

async def test_mode():
    logger.info("=== TEST MODE v3 ===")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_set = set(load_stock_symbols())
    redis_mgr = await get_simple_redis_manager()
    paper = load_paper_trades()
    async with aiohttp.ClientSession() as session:
        fg = await poll_fear_greed(session)
        sc = await poll_senticrypt(session)
        rss_crypto = await poll_rss_feeds(session, CRYPTO_RSS_FEEDS, coin_map, stock_set, is_crypto=True)
        rss_stocks = await poll_rss_feeds(session, STOCK_RSS_FEEDS, coin_map, stock_set, is_crypto=False)
        av_arts = await poll_alphavantage(session)
        fh_crypto = await poll_finnhub(session, category='crypto')
        fh_general = await poll_finnhub(session, category='general')
        all_articles = rss_crypto + rss_stocks + av_arts + fh_crypto + fh_general
        scores = aggregate_scores(all_articles, sc, coin_map, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=1, fear_greed=fg)
        await publish_to_redis(scores, fg, redis_mgr)
        await save_fallback(scores)
        await update_paper_trades(session, scores, paper)
    print(f"\n{'='*70}")
    print(f"NEWS SENTIMENT v3 — {len(scores)} symbols scored")
    if fg: print(f"Fear & Greed: {fg['classification']} ({fg['value']}/100)")
    if sc: print(f"SentiCrypt BTC: {sc.get('BTC', 0):+.4f}")
    print(f"Sources: RSS:{len(rss_crypto)+len(rss_stocks)} AV:{len(av_arts)} Finnhub:{len(fh_crypto)+len(fh_general)}")
    print(f"{'='*70}")
    if scores:
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        print(f"\nTop 10 Bullish:")
        for sym, sc_val in sorted_scores[:10]: print(f"  {sym:>15s}: {sc_val:+.3f}")
        print(f"\nTop 10 Bearish:")
        for sym, sc_val in sorted_scores[-10:]: print(f"  {sym:>15s}: {sc_val:+.3f}")
    print(f"\n--- Paper Trading ---")
    s = paper['stats']
    print(f"Open: {len(paper['open'])} | Closed: {s['total_trades']} | W:{s['wins']} L:{s['losses']} | Avg P&L: {s['avg_pnl_pct']:+.2f}%")
    for t in paper['open'][:10]:
        print(f"  [{t['side']:5s}] {t['symbol']:>15s} @ {t['entry_price']:.6f} sentiment={t['sentiment_score']:+.3f} unrealized={t.get('unrealized_pnl_pct', 0):+.2f}%")

def handle_shutdown(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    shutdown_event.set()

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    if '--test' in sys.argv:
        asyncio.run(test_mode())
    else:
        asyncio.run(main_loop())
