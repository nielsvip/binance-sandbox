# pylint: disable=W,C,R,I
"""News & social sentiment scanner — RSS feeds, Fear&Greed, Messari, Finnhub. Publishes to Redis for ez_rankings/tradier_rankings."""
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
CRYPTO_ALIASES = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP", "CARDANO": "ADA", "DOGECOIN": "DOGE", "POLKADOT": "DOT", "AVALANCHE": "AVAX", "CHAINLINK": "LINK", "POLYGON": "MATIC", "LITECOIN": "LTC", "UNISWAP": "UNI", "SHIBA": "SHIB", "PEPE": "PEPE", "BONK": "BONK", "FLOKI": "FLOKI", "WORLDCOIN": "WLD", "ARBITRUM": "ARB", "OPTIMISM": "OP", "CELESTIA": "TIA", "SEI": "SEI", "SUI": "SUI", "APTOS": "APT", "INJECTIVE": "INJ", "JUPITER": "JUP", "RENDER": "RNDR", "NEAR": "NEAR", "FILECOIN": "FIL", "COSMOS": "ATOM", "ALGORAND": "ALGO", "TONCOIN": "TON", "TRON": "TRX"}
STOCK_TICKERS_PATTERN = re.compile(r'\$([A-Z]{1,5})\b')
CRYPTO_TICKER_PATTERN = re.compile(r'(?:\$|#)([A-Z]{2,10})\b')
HTML_TAG_RE = re.compile(r'<[^>]+>')
shutdown_event = asyncio.Event()
# RSS feeds — free, real-time, no API keys
CRYPTO_RSS_FEEDS = [
    ('https://www.coindesk.com/arc/outboundfeeds/rss/', 'coindesk'),
    ('https://cointelegraph.com/rss', 'cointelegraph'),
    ('https://decrypt.co/feed', 'decrypt'),
    ('https://bitcoinmagazine.com/.rss/full/', 'btcmag'),
    ('https://www.reddit.com/r/CryptoCurrency/.rss', 'reddit_crypto'),
    ('https://www.reddit.com/r/Bitcoin/.rss', 'reddit_btc'),
    ('https://www.reddit.com/r/ethtrader/.rss', 'reddit_eth'),
    ('https://www.reddit.com/r/solana/.rss', 'reddit_sol'),
]
STOCK_RSS_FEEDS = [
    ('https://www.reddit.com/r/wallstreetbets/.rss', 'reddit_wsb'),
    ('https://www.reddit.com/r/stocks/.rss', 'reddit_stocks'),
    ('https://seekingalpha.com/feed.xml', 'seekingalpha'),
]
# Fear & Greed — free, no key
FEAR_GREED_URL = 'https://api.alternative.me/fng/?limit=1'
# Messari — free tier, 20 req/min, real-time
MESSARI_NEWS_URL = 'https://data.messari.io/api/v1/news'
# Finnhub — free key needed, real-time stock news with sentiment
FINNHUB_NEWS_URL = 'https://finnhub.io/api/v1/news'

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
            p = BASE_PATH / 'data' / 'tradier' / fname
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

async def poll_rss_feeds(session: aiohttp.ClientSession, feeds: List[Tuple[str, str]], coin_map: Dict[str, str], stock_set: set, is_crypto: bool = True) -> List[dict]:
    articles = []
    for feed_url, source in feeds:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; ez_news_scanner/1.0)'}
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=12), headers=headers) as resp:
                if resp.status != 200:
                    logger.debug(f"[RSS:{source}] HTTP {resp.status}")
                    continue
                text = await resp.text()
                root = ET.fromstring(text)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                items = root.findall('.//item') or root.findall('.//atom:entry', ns)
                count = 0
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
                        syms = extract_symbols_from_text(combined, coin_map)
                        for sym in syms:
                            articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment, 'engagement': 5, 'source': f'rss_{source}', 'published': published})
                            count += 1
                    else:
                        tickers = extract_stock_tickers(combined, stock_set)
                        for t in tickers:
                            articles.append({'symbol': t, 'text': title[:200], 'score': sentiment, 'engagement': 5, 'source': f'rss_{source}', 'published': published})
                            count += 1
                if count > 0: logger.debug(f"[RSS:{source}] {count} mentions from {len(items)} items")
        except ET.ParseError as e:
            logger.debug(f"[RSS:{source}] XML parse error: {e}")
        except Exception as e:
            logger.warning(f"[RSS:{source}] Error: {e}")
    logger.info(f"[RSS] Total {len(articles)} mentions from {len(feeds)} feeds")
    return articles

async def poll_fear_greed(session: aiohttp.ClientSession) -> Optional[dict]:
    """Alternative.me Fear & Greed Index — free, no API key, updates daily."""
    try:
        async with session.get(FEAR_GREED_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200: return None
            data = await resp.json()
            fg = data.get('data', [{}])[0]
            value = int(fg.get('value', 50))
            classification = fg.get('value_classification', 'Neutral')
            score = (value - 50) / 50.0
            logger.info(f"[FEAR_GREED] {classification} ({value}/100) → market_score={score:+.2f}")
            return {'value': value, 'classification': classification, 'score': score}
    except Exception as e:
        logger.warning(f"[FEAR_GREED] Error: {e}")
        return None

async def poll_messari_news(session: aiohttp.ClientSession, coin_map: Dict[str, str]) -> List[dict]:
    """Messari API — free tier, 20 req/min, real-time crypto news."""
    articles = []
    try:
        async with session.get(MESSARI_NEWS_URL, timeout=aiohttp.ClientTimeout(total=15), params={'page': 1, 'as-markdown': False}) as resp:
            if resp.status != 200:
                logger.debug(f"[MESSARI] HTTP {resp.status}")
                return []
            data = await resp.json()
            for item in data.get('data', [])[:50]:
                title = item.get('title', '')
                published = item.get('published_at', '')
                references = [r.get('name', '') for r in item.get('references', [])]
                combined = f"{title} {' '.join(references)}"
                sentiment = vader_score(combined)
                syms = extract_symbols_from_text(combined, coin_map)
                for sym in syms:
                    articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment, 'engagement': 10, 'source': 'messari', 'published': published})
        logger.info(f"[MESSARI] {len(articles)} mentions from news feed")
    except Exception as e:
        logger.warning(f"[MESSARI] Error: {e}")
    return articles

async def poll_finnhub_news(session: aiohttp.ClientSession, stock_set: set) -> List[dict]:
    """Finnhub — free API key needed, real-time stock market news."""
    api_key = os.getenv('FINNHUB_API_KEY', '')
    if not api_key: return []
    articles = []
    try:
        params = {'category': 'general', 'token': api_key}
        async with session.get(FINNHUB_NEWS_URL, timeout=aiohttp.ClientTimeout(total=15), params=params) as resp:
            if resp.status != 200:
                logger.debug(f"[FINNHUB] HTTP {resp.status}")
                return []
            data = await resp.json()
            for item in data[:50]:
                title = item.get('headline', '')
                summary = item.get('summary', '')[:200]
                published = datetime.fromtimestamp(item.get('datetime', 0), tz=timezone.utc).isoformat() if item.get('datetime') else ''
                sentiment_val = vader_score(f"{title} {summary}")
                tickers = extract_stock_tickers(f"{title} {summary}", stock_set)
                related = item.get('related', '').split(',')
                for r in related:
                    r = r.strip().upper()
                    if r in stock_set: tickers.append(r)
                for t in set(tickers):
                    articles.append({'symbol': t, 'text': title[:200], 'score': sentiment_val, 'engagement': 8, 'source': 'finnhub', 'published': published})
        logger.info(f"[FINNHUB] {len(articles)} mentions from market news")
    except Exception as e:
        logger.warning(f"[FINNHUB] Error: {e}")
    return articles

async def poll_cryptopanic_backtest(session: aiohttp.ClientSession, api_key: str, coin_map: Dict[str, str]) -> List[dict]:
    """CryptoPanic — free tier has 24h delay, useful for backtesting/historical only."""
    if not api_key: return []
    articles = []
    try:
        url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&kind=news&filter=important&public=true"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200: return []
            data = await resp.json()
            for post in data.get('results', [])[:50]:
                title = post.get('title', '')
                published = post.get('published_at', '')
                currencies = [c.get('code', '') for c in post.get('currencies', [])]
                votes = post.get('votes', {})
                pos_votes = votes.get('positive', 0) + votes.get('important', 0) + votes.get('liked', 0)
                neg_votes = votes.get('negative', 0) + votes.get('disliked', 0) + votes.get('toxic', 0)
                engagement = max(1, pos_votes + neg_votes)
                syms = [coin_map[c] for c in currencies if c in coin_map]
                if not syms: syms = extract_symbols_from_text(title, coin_map)
                if not syms: continue
                sentiment = vader_score(title)
                vote_bias = (pos_votes - neg_votes) / engagement * 0.3
                sentiment = max(-1.0, min(1.0, sentiment + vote_bias))
                for sym in syms:
                    articles.append({'symbol': sym, 'text': title, 'score': sentiment, 'engagement': engagement, 'source': 'cryptopanic_delayed', 'published': published})
        logger.info(f"[CRYPTOPANIC] {len(articles)} articles (24h delayed — backtest use only)")
    except Exception as e:
        logger.error(f"[CRYPTOPANIC] Error: {e}")
    return articles

def aggregate_scores(articles: List[dict], decay_hours: int = 4, min_articles: int = 2, fear_greed: Optional[dict] = None) -> Dict[str, float]:
    now = datetime.now(timezone.utc)
    per_symbol: Dict[str, List[Tuple[float, float]]] = {}
    fg_bias = fear_greed['score'] * 0.15 if fear_greed else 0.0
    for a in articles:
        sym = a['symbol']
        score = a.get('score', 0.0) + fg_bias
        engagement = a.get('engagement', 1)
        pub = a.get('published', '')
        age_hours = decay_hours
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
                age_hours = max(0.01, (now - pub_dt).total_seconds() / 3600)
            except Exception: pass
        decay = max(0.1, 1.0 - (age_hours / decay_hours)) if age_hours < decay_hours else 0.1
        weight = decay * min(engagement, 1000) ** 0.3
        per_symbol.setdefault(sym, []).append((score * weight, weight))
    result = {}
    for sym, weighted in per_symbol.items():
        if len(weighted) < min_articles: continue
        total_weight = sum(w for _, w in weighted)
        if total_weight <= 0: continue
        result[sym] = max(-1.0, min(1.0, sum(s for s, _ in weighted) / total_weight))
    return result

async def publish_to_redis(scores: Dict[str, float], fear_greed: Optional[dict], redis_mgr) -> bool:
    try:
        bulk_json = json.dumps(scores)
        meta = {'last_poll': datetime.now(timezone.utc).isoformat(), 'total_symbols': len(scores), 'avg_score': sum(scores.values()) / max(1, len(scores)), 'sources': 'rss+messari+finnhub+fear_greed'}
        if fear_greed: meta['fear_greed'] = fear_greed
        meta_json = json.dumps(meta)
        if redis_mgr and hasattr(redis_mgr, 'connections'):
            for name, conn in redis_mgr.connections.items():
                if conn is None: continue
                try:
                    conn.setex('news_sentiment_bulk', 14400, bulk_json)
                    conn.setex('news_sentiment_meta', 14400, meta_json)
                except Exception as e:
                    logger.debug(f"Redis {name} publish failed: {e}")
        return True
    except Exception as e:
        logger.error(f"[REDIS] Publish error: {e}")
        return False

async def save_fallback(scores: Dict[str, float]):
    try:
        out_path = BASE_PATH / 'data' / 'news_sentiment.json'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w') as f: json.dump(scores, f)
    except Exception as e:
        logger.error(f"[FALLBACK] Save error: {e}")

async def main_loop():
    logger.info("=== ez_news_scanner v2 starting (RSS + Messari + Finnhub + Fear&Greed) ===")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_syms = load_stock_symbols()
    stock_set = set(stock_syms) if stock_syms else set()
    logger.info(f"[SCANNER] Loaded {len(coin_map)} coin mappings, {len(stock_set)} stock tickers")
    redis_mgr = await get_simple_redis_manager()
    rss_interval = 180
    messari_interval = config.NEWS_POLL_INTERVAL_CRYPTO
    fg_interval = 1800
    last_rss = 0.0
    last_messari = 0.0
    last_fg = 0.0
    fear_greed_data = None
    async with aiohttp.ClientSession() as session:
        while not shutdown_event.is_set():
            try:
                now = time.time()
                all_articles = []
                if (now - last_rss) >= rss_interval:
                    crypto_arts = await poll_rss_feeds(session, CRYPTO_RSS_FEEDS, coin_map, stock_set, is_crypto=True)
                    stock_arts = await poll_rss_feeds(session, STOCK_RSS_FEEDS, coin_map, stock_set, is_crypto=False)
                    all_articles.extend(crypto_arts)
                    all_articles.extend(stock_arts)
                    last_rss = now
                if (now - last_messari) >= messari_interval:
                    messari_arts = await poll_messari_news(session, coin_map)
                    all_articles.extend(messari_arts)
                    last_messari = now
                if (now - last_fg) >= fg_interval:
                    fear_greed_data = await poll_fear_greed(session)
                    last_fg = now
                finnhub_arts = await poll_finnhub_news(session, stock_set) if stock_set and (now - last_rss) < 5 else []
                all_articles.extend(finnhub_arts)
                if all_articles:
                    scores = aggregate_scores(all_articles, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=config.NEWS_SENTIMENT_MIN_ARTICLES, fear_greed=fear_greed_data)
                    await publish_to_redis(scores, fear_greed_data, redis_mgr)
                    await save_fallback(scores)
                    pos_count = sum(1 for v in scores.values() if v > 0.1)
                    neg_count = sum(1 for v in scores.values() if v < -0.1)
                    logger.info(f"[SCANNER] {len(all_articles)} articles → {len(scores)} symbols ({pos_count} bull, {neg_count} bear, fg={'%d' % fear_greed_data['value'] if fear_greed_data else '?'})")
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"[SCANNER] Main loop error: {e}", exc_info=True)
                await asyncio.sleep(60)

async def test_mode():
    logger.info("=== TEST MODE ===")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_set = set(load_stock_symbols())
    redis_mgr = await get_simple_redis_manager()
    async with aiohttp.ClientSession() as session:
        fg = await poll_fear_greed(session)
        crypto_arts = await poll_rss_feeds(session, CRYPTO_RSS_FEEDS, coin_map, stock_set, is_crypto=True)
        stock_arts = await poll_rss_feeds(session, STOCK_RSS_FEEDS, coin_map, stock_set, is_crypto=False)
        messari_arts = await poll_messari_news(session, coin_map)
        finnhub_arts = await poll_finnhub_news(session, stock_set)
        all_articles = crypto_arts + stock_arts + messari_arts + finnhub_arts
        scores = aggregate_scores(all_articles, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=1, fear_greed=fg)
        await publish_to_redis(scores, fg, redis_mgr)
        await save_fallback(scores)
    if scores:
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        print(f"\n{'='*60}")
        print(f"NEWS SENTIMENT SCORES ({len(scores)} symbols)")
        if fg: print(f"Fear & Greed: {fg['classification']} ({fg['value']}/100)")
        print(f"Sources: {len(all_articles)} articles (RSS:{len(crypto_arts)+len(stock_arts)} Messari:{len(messari_arts)} Finnhub:{len(finnhub_arts)})")
        print(f"{'='*60}")
        print(f"\nTop 10 Bullish:")
        for sym, sc in sorted_scores[:10]: print(f"  {sym:>15s}: {sc:+.3f}")
        print(f"\nTop 10 Bearish:")
        for sym, sc in sorted_scores[-10:]: print(f"  {sym:>15s}: {sc:+.3f}")
    else:
        print("No scores generated — check network connectivity.")

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
