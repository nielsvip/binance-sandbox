# pylint: disable=W,C,R,I
"""News & social sentiment scanner — polls CryptoPanic, Reddit, Twitter/X, scores with VADER, publishes to Redis for ez_rankings/tradier_rankings consumption."""
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import aiohttp
from config import Config
from utils import get_simple_redis_manager, load_environment_from_gpg, setup_logger, safe_fetch_float

config = Config()
logger = setup_logger('ez_news_scanner', str(config.LOG_DIR / 'ez_news_scanner.log'), logging.INFO)
load_environment_from_gpg(logger)
BASE_PATH = Path(os.getenv('EZ_BASE_PATH', str(config.BASE_PATH)))
CRYPTO_ALIASES = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP", "CARDANO": "ADA", "DOGECOIN": "DOGE", "POLKADOT": "DOT", "AVALANCHE": "AVAX", "CHAINLINK": "LINK", "POLYGON": "MATIC", "LITECOIN": "LTC", "UNISWAP": "UNI", "SHIBA": "SHIB", "PEPE": "PEPE", "BONK": "BONK", "FLOKI": "FLOKI", "WORLDCOIN": "WLD", "ARBITRUM": "ARB", "OPTIMISM": "OP", "CELESTIA": "TIA", "SEI": "SEI", "SUI": "SUI", "APTOS": "APT", "INJECTIVE": "INJ", "JUPITER": "JUP", "RENDER": "RNDR", "NEAR": "NEAR", "FILECOIN": "FIL", "COSMOS": "ATOM", "ALGORAND": "ALGO", "TONCOIN": "TON", "TRON": "TRX"}
STOCK_TICKERS_PATTERN = re.compile(r'\$([A-Z]{1,5})\b')
CRYPTO_TICKER_PATTERN = re.compile(r'(?:\$|#)([A-Z]{2,10})\b')
shutdown_event = asyncio.Event()

def build_coin_map(symbols: List[str]) -> Dict[str, str]:
    """Map coin codes (BTC, ETH) and aliases to full Binance symbols (BTCUSDT)."""
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

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    vader = SentimentIntensityAnalyzer()
except ImportError:
    logger.warning("vaderSentiment not installed — using basic keyword scoring")
    vader = None

def vader_score(text: str) -> float:
    if vader: return vader.polarity_scores(text)['compound']
    text_lower = text.lower()
    pos = sum(1 for w in ('surge', 'soar', 'bull', 'rally', 'gain', 'high', 'pump', 'moon', 'breakout', 'upgrade', 'adoption', 'partnership', 'launch', 'approval') if w in text_lower)
    neg = sum(1 for w in ('crash', 'dump', 'bear', 'drop', 'fall', 'low', 'hack', 'scam', 'ban', 'fraud', 'lawsuit', 'sec', 'investigation', 'exploit', 'rug') if w in text_lower)
    if pos + neg == 0: return 0.0
    return (pos - neg) / (pos + neg)

def extract_symbols_from_text(text: str, coin_map: Dict[str, str]) -> List[str]:
    found = set()
    for m in CRYPTO_TICKER_PATTERN.finditer(text):
        code = m.group(1)
        if code in coin_map: found.add(coin_map[code])
    for word in text.upper().split():
        word_clean = re.sub(r'[^A-Z]', '', word)
        if word_clean in coin_map: found.add(coin_map[word_clean])
    return list(found)

def extract_stock_tickers(text: str, stock_set: set) -> List[str]:
    found = set()
    for m in STOCK_TICKERS_PATTERN.finditer(text):
        ticker = m.group(1)
        if ticker in stock_set: found.add(ticker)
    return list(found)

async def poll_cryptopanic(session: aiohttp.ClientSession, api_key: str, coin_map: Dict[str, str]) -> List[dict]:
    if not api_key: return []
    articles = []
    try:
        url = f"https://cryptopanic.com/api/v1/posts/?auth_token={api_key}&kind=news&filter=important&public=true"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"[CRYPTOPANIC] HTTP {resp.status}")
                return []
            data = await resp.json()
            for post in data.get('results', [])[:50]:
                title = post.get('title', '')
                published = post.get('published_at', '')
                currencies = [c.get('code', '') for c in post.get('currencies', [])]
                votes = post.get('votes', {})
                pos_votes = votes.get('positive', 0) + votes.get('important', 0) + votes.get('liked', 0)
                neg_votes = votes.get('negative', 0) + votes.get('disliked', 0) + votes.get('toxic', 0)
                engagement = max(1, pos_votes + neg_votes)
                syms = []
                for c in currencies:
                    if c in coin_map: syms.append(coin_map[c])
                if not syms: syms = extract_symbols_from_text(title, coin_map)
                if not syms: continue
                sentiment = vader_score(title)
                vote_bias = (pos_votes - neg_votes) / engagement * 0.3
                sentiment = max(-1.0, min(1.0, sentiment + vote_bias))
                for sym in syms:
                    articles.append({'symbol': sym, 'text': title, 'score': sentiment, 'engagement': engagement, 'source': 'cryptopanic', 'published': published})
        logger.info(f"[CRYPTOPANIC] Fetched {len(articles)} articles from {len(data.get('results', []))} posts")
    except Exception as e:
        logger.error(f"[CRYPTOPANIC] Error: {e}")
    return articles

async def poll_reddit(coin_map: Dict[str, str], stock_set: set) -> List[dict]:
    try:
        import praw
    except ImportError:
        logger.debug("[REDDIT] praw not installed, skipping")
        return []
    client_id = os.getenv('REDDIT_CLIENT_ID', '')
    client_secret = os.getenv('REDDIT_CLIENT_SECRET', '')
    username = os.getenv('REDDIT_USERNAME', 'ez_bot')
    if not client_id or not client_secret: return []
    articles = []
    try:
        reddit = praw.Reddit(client_id=client_id, client_secret=client_secret, user_agent=f'ez_news_scanner by /u/{username}')
        crypto_subs = ['CryptoCurrency', 'Bitcoin', 'ethtrader', 'solana']
        stock_subs = ['wallstreetbets', 'stocks', 'investing']
        def _fetch_sub(sub_name, is_crypto):
            results = []
            try:
                sub = reddit.subreddit(sub_name)
                for post in sub.hot(limit=50):
                    title = post.title
                    upvotes = post.score
                    comments = post.num_comments
                    engagement = max(1, upvotes + comments)
                    sentiment = vader_score(title)
                    sentiment = max(-1.0, min(1.0, sentiment * (1 + min(upvotes, 1000) / 5000)))
                    if is_crypto:
                        syms = extract_symbols_from_text(title, coin_map)
                        for sym in syms:
                            results.append({'symbol': sym, 'text': title, 'score': sentiment, 'engagement': engagement, 'source': f'reddit_{sub_name}', 'published': datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat()})
                    else:
                        tickers = extract_stock_tickers(title, stock_set)
                        for t in tickers:
                            results.append({'symbol': t, 'text': title, 'score': sentiment, 'engagement': engagement, 'source': f'reddit_{sub_name}', 'published': datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat()})
            except Exception as e:
                logger.warning(f"[REDDIT] Error fetching r/{sub_name}: {e}")
            return results
        for sub in crypto_subs:
            articles.extend(await asyncio.to_thread(_fetch_sub, sub, True))
        for sub in stock_subs:
            articles.extend(await asyncio.to_thread(_fetch_sub, sub, False))
        logger.info(f"[REDDIT] Fetched {len(articles)} mentions from {len(crypto_subs) + len(stock_subs)} subreddits")
    except Exception as e:
        logger.error(f"[REDDIT] Error: {e}")
    return articles

async def poll_twitter(coin_map: Dict[str, str], stock_set: set, top_n: int = 30) -> List[dict]:
    try:
        import tweepy
    except ImportError:
        logger.debug("[TWITTER] tweepy not installed, skipping")
        return []
    bearer = os.getenv('TWITTER_BEARER_TOKEN', '')
    if not bearer: return []
    articles = []
    try:
        client = tweepy.Client(bearer_token=bearer, wait_on_rate_limit=True)
        top_coins = sorted(coin_map.keys(), key=lambda k: len(k))[:top_n]
        queries = []
        batch = []
        for coin in top_coins:
            if len(coin) < 3 or coin in CRYPTO_ALIASES: continue
            batch.append(f'${coin}')
            if len(batch) >= 5:
                queries.append(' OR '.join(batch) + ' -is:retweet lang:en')
                batch = []
        if batch: queries.append(' OR '.join(batch) + ' -is:retweet lang:en')
        for query in queries[:6]:
            try:
                resp = await asyncio.to_thread(client.search_recent_tweets, query=query, max_results=20, tweet_fields=['created_at', 'public_metrics'])
                if not resp.data: continue
                for tweet in resp.data:
                    text = tweet.text
                    metrics = tweet.public_metrics or {}
                    likes = metrics.get('like_count', 0)
                    rts = metrics.get('retweet_count', 0)
                    engagement = max(1, likes + rts)
                    sentiment = vader_score(text)
                    sentiment = max(-1.0, min(1.0, sentiment * (1 + min(likes, 500) / 2000)))
                    syms = extract_symbols_from_text(text, coin_map)
                    tickers = extract_stock_tickers(text, stock_set)
                    for sym in syms:
                        articles.append({'symbol': sym, 'text': text[:200], 'score': sentiment, 'engagement': engagement, 'source': 'twitter', 'published': tweet.created_at.isoformat() if tweet.created_at else ''})
                    for t in tickers:
                        articles.append({'symbol': t, 'text': text[:200], 'score': sentiment, 'engagement': engagement, 'source': 'twitter', 'published': tweet.created_at.isoformat() if tweet.created_at else ''})
            except Exception as e:
                logger.warning(f"[TWITTER] Query error: {e}")
        logger.info(f"[TWITTER] Fetched {len(articles)} mentions from {len(queries)} queries")
    except Exception as e:
        logger.error(f"[TWITTER] Error: {e}")
    return articles

def aggregate_scores(articles: List[dict], decay_hours: int = 4, min_articles: int = 2) -> Dict[str, float]:
    now = datetime.now(timezone.utc)
    per_symbol: Dict[str, List[Tuple[float, float]]] = {}
    for a in articles:
        sym = a['symbol']
        score = a.get('score', 0.0)
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

async def publish_to_redis(scores: Dict[str, float], redis_mgr) -> bool:
    try:
        bulk_json = json.dumps(scores)
        meta_json = json.dumps({'last_poll': datetime.now(timezone.utc).isoformat(), 'total_symbols': len(scores), 'avg_score': sum(scores.values()) / max(1, len(scores))})
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

async def run_poll_cycle(session: aiohttp.ClientSession, coin_map: Dict[str, str], stock_set: set, api_key: str, redis_mgr) -> Dict[str, float]:
    tasks = [poll_cryptopanic(session, api_key, coin_map), poll_reddit(coin_map, stock_set), poll_twitter(coin_map, stock_set)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_articles = []
    for i, r in enumerate(results):
        src = ['cryptopanic', 'reddit', 'twitter'][i]
        if isinstance(r, Exception):
            logger.error(f"[{src.upper()}] Poll failed: {r}")
        elif isinstance(r, list):
            all_articles.extend(r)
    if not all_articles:
        logger.info("[SCANNER] No articles collected this cycle")
        return {}
    scores = aggregate_scores(all_articles, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=config.NEWS_SENTIMENT_MIN_ARTICLES)
    await publish_to_redis(scores, redis_mgr)
    await save_fallback(scores)
    pos_count = sum(1 for v in scores.values() if v > 0.1)
    neg_count = sum(1 for v in scores.values() if v < -0.1)
    logger.info(f"[SCANNER] Cycle complete: {len(all_articles)} articles → {len(scores)} symbols scored ({pos_count} bullish, {neg_count} bearish)")
    return scores

async def main_loop():
    logger.info("=== ez_news_scanner starting ===")
    api_key = os.getenv('CRYPTOPANIC_API_KEY', '')
    if not api_key: logger.warning("[SCANNER] CRYPTOPANIC_API_KEY not set — CryptoPanic polling disabled")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_syms = load_stock_symbols()
    stock_set = set(stock_syms) if stock_syms else set()
    logger.info(f"[SCANNER] Loaded {len(coin_map)} coin mappings, {len(stock_set)} stock tickers")
    redis_mgr = await get_simple_redis_manager()
    crypto_interval = config.NEWS_POLL_INTERVAL_CRYPTO
    social_interval = config.NEWS_POLL_INTERVAL_SOCIAL
    last_crypto = 0.0
    last_social = 0.0
    async with aiohttp.ClientSession() as session:
        while not shutdown_event.is_set():
            try:
                now = time.time()
                do_crypto = (now - last_crypto) >= crypto_interval
                do_social = (now - last_social) >= social_interval
                if not do_crypto and not do_social:
                    await asyncio.sleep(30)
                    continue
                coros = []
                if do_crypto: coros.append(poll_cryptopanic(session, api_key, coin_map))
                if do_social:
                    coros.append(poll_reddit(coin_map, stock_set))
                    coros.append(poll_twitter(coin_map, stock_set))
                results = await asyncio.gather(*coros, return_exceptions=True)
                all_articles = []
                for r in results:
                    if isinstance(r, list): all_articles.extend(r)
                    elif isinstance(r, Exception): logger.error(f"[SCANNER] Source error: {r}")
                if do_crypto: last_crypto = now
                if do_social: last_social = now
                if all_articles:
                    scores = aggregate_scores(all_articles, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=config.NEWS_SENTIMENT_MIN_ARTICLES)
                    await publish_to_redis(scores, redis_mgr)
                    await save_fallback(scores)
                    pos_count = sum(1 for v in scores.values() if v > 0.1)
                    neg_count = sum(1 for v in scores.values() if v < -0.1)
                    logger.info(f"[SCANNER] {len(all_articles)} articles → {len(scores)} symbols ({pos_count} bull, {neg_count} bear)")
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"[SCANNER] Main loop error: {e}", exc_info=True)
                await asyncio.sleep(60)

async def test_mode():
    """Single poll cycle for testing — prints results and exits."""
    logger.info("=== TEST MODE ===")
    api_key = os.getenv('CRYPTOPANIC_API_KEY', '')
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_set = set(load_stock_symbols())
    redis_mgr = await get_simple_redis_manager()
    async with aiohttp.ClientSession() as session:
        scores = await run_poll_cycle(session, coin_map, stock_set, api_key, redis_mgr)
    if scores:
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        print(f"\n{'='*60}")
        print(f"NEWS SENTIMENT SCORES ({len(scores)} symbols)")
        print(f"{'='*60}")
        print(f"\nTop 10 Bullish:")
        for sym, sc in sorted_scores[:10]: print(f"  {sym:>15s}: {sc:+.3f}")
        print(f"\nTop 10 Bearish:")
        for sym, sc in sorted_scores[-10:]: print(f"  {sym:>15s}: {sc:+.3f}")
    else:
        print("No scores generated — check API keys and network.")

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
