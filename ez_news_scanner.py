# pylint: disable=W,C,R,I
"""ez_news_scanner v6 — News-Driven Trading + World News EXTREME_MODE Trigger.
Daily 9am ET (cron): fetch all free sources, score multi-source agreement,
inject high-conviction picks into account symbol lists, boost Redis sentiment.
Daemon mode (systemd): continuous sentiment + re-inject symbols if rankings overwrites.
NEW in v6: Scans world/macro news (Reuters, AP, BBC, CNBC, etc.) for market-moving events.
When big news detected (war, tariffs, rate decisions, sanctions, etc.) → triggers EXTREME_MODE
across ez_rankings + tradier_rankings via data/market_mode.json + Redis.
Modes: --daily | --cleanup | --report | --test | (default: daemon)
Targets: ang/inf/men (crypto), trb (stocks)."""
import asyncio
import fcntl
import json
import logging
import os
import re
import signal
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import aiohttp
import aiofiles
from config import Config
from utils import get_simple_redis_manager, load_environment_from_gpg, setup_logger

config = Config()
logger = setup_logger('ez_news_scanner', str(config.LOG_DIR / 'ez_news_scanner.log'), logging.INFO)
load_environment_from_gpg(logger)
BASE_PATH = Path(os.getenv('EZ_BASE_PATH', str(config.BASE_PATH)))
DATA_DIR = BASE_PATH / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
shutdown_event = asyncio.Event()

# ============================================================
# TRACKING FILES + INJECTION CONFIG
# ============================================================
INJECTION_FILE = DATA_DIR / 'news_injections.json'
DAILY_PICKS_FILE = DATA_DIR / 'news_daily_picks.json'
INJECTION_TTL_HOURS = 48
CRYPTO_INJECT_TARGETS = {
    'ang': {'long': BASE_PATH / 'symbols_ang_long.json', 'short': BASE_PATH / 'symbols_ang_short.json'},
    'inf': {'long': BASE_PATH / 'symbols_inf_long.json', 'short': BASE_PATH / 'symbols_inf_short.json'},
    'men': {'single': BASE_PATH / 'symbols_men.json'},
}
STOCK_INJECT_ACCOUNTS = ('trb',)
TRADIER_NEWS_WRITABLE_FILES = {
    'symbols_trb_long.json',
    'symbols_trb_short.json',
}
TRADIER_DERIVED_FILES_LOCK = BASE_PATH / '.symbols_trb_trc.lock'
MIN_CONVICTION = 0.40
MAX_CRYPTO_PICKS = 5
MAX_STOCK_PICKS = 3
RE_INJECT_INTERVAL = 120

# ============================================================
# ALIASES + PATTERNS
# ============================================================
CRYPTO_ALIASES = {
    "BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP",
    "CARDANO": "ADA", "DOGECOIN": "DOGE", "POLKADOT": "DOT", "AVALANCHE": "AVAX",
    "CHAINLINK": "LINK", "POLYGON": "MATIC", "LITECOIN": "LTC", "UNISWAP": "UNI",
    "SHIBA": "SHIB", "SHIBAINU": "SHIB", "PEPE": "PEPE", "BONK": "BONK",
    "FLOKI": "FLOKI", "WORLDCOIN": "WLD", "ARBITRUM": "ARB", "OPTIMISM": "OP",
    "CELESTIA": "TIA", "SEI": "SEI", "SUI": "SUI", "APTOS": "APT",
    "INJECTIVE": "INJ", "JUPITER": "JUP", "RENDER": "RNDR", "RENDERTOKEN": "RNDR",
    "NEAR": "NEAR", "FILECOIN": "FIL", "COSMOS": "ATOM", "ALGORAND": "ALGO",
    "TONCOIN": "TON", "TRON": "TRX", "STELLAR": "XLM", "MONERO": "XMR",
    "ZCASH": "ZEC", "DASH": "DASH", "MAKER": "MKR", "COMPOUND": "COMP",
    "AAVE": "AAVE", "CURVE": "CRV", "SYNTHETIX": "SNX", "YEARN": "YFI",
    "SUSHISWAP": "SUSHI", "1INCH": "1INCH", "BALANCER": "BAL",
    "PANCAKESWAP": "CAKE", "BNBCHAIN": "BNB", "BINANCE": "BNB",
    "HEDERA": "HBAR", "VECHAIN": "VET", "FANTOM": "FTM", "HARMONY": "ONE",
    "ELROND": "EGLD", "MULTIVERSX": "EGLD", "THETA": "THETA", "IOTA": "IOTA",
    "ZILLIQA": "ZIL", "WAVES": "WAVES", "BAND": "BAND", "ORACLE": "BAND",
    "DECENTRALAND": "MANA", "SANDBOX": "SAND", "AXS": "AXS", "AXIEINFINITY": "AXS",
    "GALA": "GALA", "ENJ": "ENJ", "ENJIN": "ENJ", "CHILIZ": "CHZ",
    "FLOW": "FLOW", "IMMUTABLEX": "IMX", "LOOPRING": "LRC",
    "STACKS": "STX", "CELO": "CELO", "OCEAN": "OCEAN", "FETCH": "FET",
    "AGIX": "AGIX", "SINGULARITYNET": "AGIX", "NUMER": "NMR",
    "GRAPHPROTOCOL": "GRT", "GRAPH": "GRT", "BASICATTENTION": "BAT",
    "STORJ": "STORJ", "NKN": "NKN", "HELIUM": "HNT", "IOTEX": "IOTX",
    "SKALE": "SKL", "MINA": "MINA", "COTI": "COTI", "ANKR": "ANKR",
    "CARTESI": "CTSI", "API3": "API3", "DYDX": "DYDX", "PERPETUAL": "PERP",
    "UMEE": "UMEE", "OSMOSIS": "OSMO", "JUNO": "JUNO", "EVMOS": "EVMOS",
    "KAVA": "KAVA", "TERRA": "LUNA", "TERRACLASSIC": "LUNC",
    "FRAX": "FRAX", "CONVEX": "CVX", "LIDO": "LDO", "ROCKET": "RPL",
    "ROCKETPOOL": "RPL", "OASIS": "ROSE", "CASPER": "CSPR",
    "PYTH": "PYTH", "JTO": "JTO", "JITO": "JTO", "DRIFT": "DRIFT",
    "WORMHOLE": "W", "ETHENA": "ENA", "TRUMP": "TRUMP", "MELANIA": "MELANIA",
    "POPCAT": "POPCAT", "MEW": "MEW", "WIF": "WIF", "DOGWIFHAT": "WIF",
    "BOME": "BOME", "BOOKOFMEME": "BOME", "EIGEN": "EIGEN", "EIGENLAYER": "EIGEN",
    "HYPERLIQUID": "HYPE", "VIRTUALS": "VIRTUAL", "AI16Z": "AI16Z",
    "ZEREBRO": "ZEREBRO", "FARTCOIN": "FARTCOIN", "GRIFFAIN": "GRIFFAIN",
}
STOCK_TICKERS_PATTERN = re.compile(r'\$([A-Z]{1,5})\b')
CRYPTO_TICKER_PATTERN = re.compile(r'(?:\$|#)([A-Z]{2,10})\b')
HTML_TAG_RE = re.compile(r'<[^>]+>')
# Company name -> ticker fallback for headlines like "Apple beats" without $AAPL
STOCK_COMPANY_MAP = {
    'APPLE': 'AAPL', 'MICROSOFT': 'MSFT', 'NVIDIA': 'NVDA', 'TESLA': 'TSLA', 'AMAZON': 'AMZN',
    'META': 'META', 'FACEBOOK': 'META', 'GOOGLE': 'GOOGL', 'ALPHABET': 'GOOGL', 'NETFLIX': 'NFLX',
    'AMD': 'AMD', 'INTEL': 'INTC', 'BOEING': 'BA', 'CATERPILLAR': 'CAT', 'JPMORGAN': 'JPM',
    'GOLDMAN': 'GS', 'MORGAN STANLEY': 'MS', 'BANK OF AMERICA': 'BAC', 'WALMART': 'WMT',
    'COCA COLA': 'KO', 'PEPSI': 'PEP', 'PFIZER': 'PFE', 'JOHNSON': 'JNJ', 'EXXON': 'XOM',
    'CHEVRON': 'CVX', 'DISNEY': 'DIS', 'NIKE': 'NKE', 'COSTCO': 'COST', 'ORACLE': 'ORCL',
    'ADOBE': 'ADBE', 'SALESFORCE': 'CRM', 'IBM': 'IBM', 'CISCO': 'CSCO', 'QUALCOMM': 'QCOM',
    'TEXAS INSTRUMENTS': 'TXN', 'BROADCOM': 'AVGO', 'MICRON': 'MU', 'APPLIED MATERIALS': 'AMAT',
    'PALANTIR': 'PLTR', 'COINBASE': 'COIN', 'ROBINHOOD': 'HOOD', 'BLOCK': 'SQ', 'PAYPAL': 'PYPL',
    'ALIBABA': 'BABA', 'BAIDU': 'BIDU', 'ELI LILLY': 'LLY', 'ABBVIE': 'ABBV', 'UNITEDHEALTH': 'UNH',
    'BERKSHIRE': 'BRK.B', 'VISA': 'V', 'MASTERCARD': 'MA', 'HOME DEPOT': 'HD', 'MCDONALD': 'MCD',
}
COMMON_WORDS = frozenset({
    'AI','GAS','KEY','PAX','PAR','ACE','ACT','ADD','AGE','AID','AIM','AIR',
    'ALL','ARM','ART','ASK','BAD','BAG','BAN','BAR','BASE','BAY','BID',
    'BIT','BOT','BOX','BUY','CAN','CAP','CAR','CAT','COL','COM','COR',
    'CUT','DAD','DEN','DIG','DIM','DIP','DOC','DOG','DOM','DOT','DUE',
    'EAT','END','ERA','ERR','ETA','ETC','EYE','FAD','FAR','FAT','FEE',
    'FEW','FIG','FIN','FIT','FIX','FLY','FOG','FOR','FUN','GAP','GAY',
    'GEM','GET','GIG','GOT','GUN','GUY','HAD','HAS','HAT','HIT','HOT',
    'HOW','HUB','ICE','ILL','INT','ION','ITS','JAR','JOB','JOT','JOY',
    'KIT','LAB','LAG','LAP','LAW','LAX','LAY','LED','LEG','LET','LIE',
    'LOG','LOT','LOW','MAP','MAR','MAX','MEN','MET','MIX','MOB','MOD',
    'MOM','MOT','MUD','NAV','NET','NEW','NOD','NOR','NOT','NOW','NUT',
    'ODD','OFF','OIL','OLD','ONE','OPT','ORB','ORE','OUR','OUT','OWE',
    'OWN','PAD','PAN','PAY','PEA','PEG','PER','PET','PIN','PIT','PLY',
    'POP','POT','POW','PRE','PRO','PUB','PUT','RAG','RAM','RAN','RAP',
    'RAT','RAW','RAY','RED','RIG','RIM','RIP','ROB','ROD','ROT','ROW',
    'RUB','RUG','RUN','SAP','SAT','SAW','SAY','SET','SEW','SIN','SIP',
    'SIT','SKI','SKY','SLY','SOB','SOD','SON','SPA','SPY','STY','SUE',
    'SUM','SUN','TAB','TAG','TAN','TAP','TAR','TAX','TIP','TON','TOO',
    'TOP','TOW','TOY','TUB','TUG','TIP','USE','VAT','VIA','VIE','WAR',
    'WAX','WAY','WEB','WED','WET','WHO','WHY','WIG','WIN','WIT','WOE',
    'WOK','WON','WOO','YAK','YAM','YAP','YEA','YEN','YEP','YES','YET',
    'ZAP','ZIT','TOKEN','TOKENS','COIN','COINS','MOVE','MOVES','SUPER',
    'SUPER','HIGH','LOW','BAND','BANDS','LEND','LENDS','BOND','BONDS',
    'LEVER','LINK','LINKS','TRUST','TRUSTS','SHARE','SHARES','NEXT',
    'FLOW','FLOWS','WELL','WELLS','RISE','RISES','BASE','BASES',
    'STORM','STORMS','STORM','PORT','PORTS','CORE','CORES','KEEP',
    'KEEPS','EARN','EARNS','ASSET','ASSETS',
})

# ============================================================
# SOURCE URLS
# ============================================================
COINGECKO_TRENDING_URL = 'https://api.coingecko.com/api/v3/search/trending'
COINGECKO_MARKETS_URL = 'https://api.coingecko.com/api/v3/coins/markets'
FEAR_GREED_URL = 'https://api.alternative.me/fng/?limit=1'
ALPHAVANTAGE_URL = 'https://www.alphavantage.co/query'
FINNHUB_NEWS_URL = 'https://finnhub.io/api/v1/news'
CRYPTO_RSS_FEEDS = [
    ('https://www.coindesk.com/arc/outboundfeeds/rss/', 'coindesk'),
    ('https://cointelegraph.com/rss', 'cointelegraph'),
    ('https://decrypt.co/feed', 'decrypt'),
    ('https://bitcoinmagazine.com/.rss/full/', 'btcmag'),
    ('https://cryptopanic.com/news/rss/', 'cryptopanic'),
    ('https://www.reddit.com/r/CryptoCurrency/.rss', 'reddit_crypto'),
    ('https://www.reddit.com/r/Bitcoin/.rss', 'reddit_btc'),
    ('https://www.reddit.com/r/ethereum/.rss', 'reddit_eth'),
    ('https://www.reddit.com/r/altcoin/.rss', 'reddit_alt'),
    ('https://www.reddit.com/r/CryptoMoonShots/.rss', 'reddit_moonshots'),
    ('https://www.reddit.com/r/solana/.rss', 'reddit_sol'),
]
STOCK_RSS_FEEDS = [
    ('https://www.reddit.com/r/wallstreetbets/.rss', 'reddit_wsb'),
    ('https://www.reddit.com/r/stocks/.rss', 'reddit_stocks'),
    ('https://www.reddit.com/r/investing/.rss', 'reddit_investing'),
    ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US', 'yahoo_finance'),
    ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US', 'yahoo_aapl'),
    ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US', 'yahoo_nvda'),
    ('https://www.benzinga.com/feed', 'benzinga'),
    ('https://www.investing.com/rss/news_25.rss', 'investing_stocks'),
    ('https://seekingalpha.com/feed.xml', 'seekingalpha'),
    ('https://www.marketwatch.com/rss/marketpulse', 'marketwatch_pulse'),
]
WORLD_NEWS_RSS_FEEDS = [
    ('https://feeds.bbci.co.uk/news/world/rss.xml', 'bbc_world'),
    ('https://feeds.bbci.co.uk/news/business/rss.xml', 'bbc_business'),
    ('https://rss.nytimes.com/services/xml/rss/nyt/World.xml', 'nyt_world'),
    ('https://rss.nytimes.com/services/xml/rss/nyt/Business.xml', 'nyt_business'),
    ('https://www.cnbc.com/id/100003114/device/rss/rss.html', 'cnbc_world'),
    ('https://www.cnbc.com/id/10001147/device/rss/rss.html', 'cnbc_economy'),
    ('https://www.cnbc.com/id/15839135/device/rss/rss.html', 'cnbc_finance'),
    ('https://feeds.reuters.com/reuters/businessNews', 'reuters_business'),
    ('https://feeds.reuters.com/reuters/worldNews', 'reuters_world'),
    ('https://www.aljazeera.com/xml/rss/all.xml', 'aljazeera'),
    ('https://www.ft.com/?format=rss', 'ft'),
    ('https://feeds.marketwatch.com/marketwatch/topstories/', 'marketwatch'),
    ('https://www.reddit.com/r/worldnews/.rss', 'reddit_worldnews'),
    ('https://www.reddit.com/r/economics/.rss', 'reddit_economics'),
    ('https://www.reddit.com/r/geopolitics/.rss', 'reddit_geopolitics'),
]
# ============================================================
# MACRO-EVENT DETECTION — EXTREME_MODE TRIGGER
# ============================================================
# Keywords/phrases that signal market-moving macro events.
# Each category has a base impact score (0-1). Multiple source agreement amplifies.
MACRO_IMPACT_KEYWORDS = {
    'war': {'keywords': ['declares war', 'military strike', 'invasion', 'troops deployed', 'missile strike', 'air strike', 'airstrike', 'nuclear', 'armed conflict', 'military escalation', 'war breaks out', 'bombing', 'artillery', 'ground offensive'], 'impact': 0.95},
    'sanctions': {'keywords': ['sanctions imposed', 'new sanctions', 'trade sanctions', 'economic sanctions', 'sanctions package', 'asset freeze', 'sanctions against', 'embargo', 'trade embargo', 'export ban', 'import ban'], 'impact': 0.75},
    'tariffs': {'keywords': ['new tariff', 'tariff hike', 'trade war', 'retaliatory tariff', 'import duty', 'tariff increase', 'tariff threat', 'tariff announcement', 'trade barrier', 'tariff escalation', 'reciprocal tariff', 'tariff war'], 'impact': 0.80},
    'rates': {'keywords': ['rate hike', 'rate cut', 'interest rate decision', 'fed decision', 'federal reserve', 'ecb decision', 'rate increase', 'rate decrease', 'monetary policy', 'basis points', 'fed raises', 'fed cuts', 'hawkish', 'dovish', 'rate surprise', 'emergency rate', 'quantitative easing', 'quantitative tightening', 'tapering'], 'impact': 0.85},
    'crash': {'keywords': ['market crash', 'flash crash', 'stock market crash', 'crypto crash', 'black swan', 'circuit breaker', 'trading halt', 'market meltdown', 'sell-off', 'selloff', 'panic selling', 'liquidity crisis', 'market collapse', 'bear market', 'capitulation'], 'impact': 0.90},
    'regulation': {'keywords': ['crypto ban', 'bitcoin ban', 'exchange shutdown', 'regulatory crackdown', 'sec lawsuit', 'sec charges', 'cftc charges', 'criminal charges', 'fraud charges', 'ponzi scheme', 'money laundering', 'exchange hack', 'exchange bankrupt'], 'impact': 0.80},
    'geopolitical': {'keywords': ['coup', 'government collapse', 'state of emergency', 'martial law', 'assassination', 'terrorist attack', 'cyberattack', 'infrastructure attack', 'oil pipeline', 'energy crisis', 'oil embargo', 'opec cut', 'opec decision', 'suez canal', 'strait of hormuz', 'taiwan strait'], 'impact': 0.85},
    'fiscal': {'keywords': ['debt default', 'sovereign default', 'government shutdown', 'debt ceiling', 'fiscal cliff', 'bailout', 'bank failure', 'bank run', 'systemic risk', 'credit downgrade', 'rating downgrade', 'lehman moment', 'contagion', 'too big to fail'], 'impact': 0.90},
    'pandemic': {'keywords': ['pandemic', 'lockdown', 'new variant', 'health emergency', 'quarantine', 'travel ban', 'border closure', 'who emergency'], 'impact': 0.80},
}
# Minimum score to trigger EXTREME_MODE from news alone
NEWS_EXTREME_THRESHOLD = 0.70
# Minimum number of distinct sources that must report the same macro event
NEWS_EXTREME_MIN_SOURCES = 2
# Cooldown: don't flip mode more than once per N seconds
NEWS_EXTREME_COOLDOWN_SECONDS = 600
_last_extreme_trigger_time = 0.0
MARKET_MODE_FILE = 'data/market_mode.json'

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    vader = SentimentIntensityAnalyzer()
except ImportError:
    logger.warning("vaderSentiment not installed — using keyword scoring")
    vader = None

# ============================================================
# HELPERS
# ============================================================

def build_coin_map(symbols: List[str]) -> Dict[str, str]:
    coin_map = {}
    for sym in symbols:
        for suffix in ('USDT', 'USDC', 'BUSD'):
            if sym.endswith(suffix):
                base = sym[:-len(suffix)]
                if base not in coin_map:
                    coin_map[base] = sym
                break
    for sym in symbols:
        m = re.match(r'^(\d{4,})([A-Z]+)(USDT|USDC|BUSD)$', sym)
        if m:
            bare = m.group(2)
            if bare not in coin_map:
                coin_map[bare] = sym
    for alias, code in CRYPTO_ALIASES.items():
        if code in coin_map:
            coin_map[alias] = coin_map[code]
    return coin_map

def load_symbols() -> List[str]:
    try:
        with open(BASE_PATH / 'symbols.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load symbols.json: {e}")
        return []

def load_stock_symbols() -> List[str]:
    """Load stocks exclusively from the authoritative Tradier allowlist."""
    p = BASE_PATH / 'symbols_tradier.json'
    try:
        with open(p, 'r') as f:
            syms = json.load(f)
        return list(dict.fromkeys(
            str(s).upper() for s in syms if str(s).isalpha() and len(str(s)) <= 5
        ))
    except Exception as e:
        logger.error(f"Failed to load symbols_tradier.json: {e}")
        return []

def vader_score(text: str) -> float:
    if vader:
        return vader.polarity_scores(text)['compound']
    text_lower = text.lower()
    pos = sum(1 for w in ('surge', 'soar', 'bull', 'rally', 'gain', 'pump', 'moon', 'breakout', 'upgrade', 'adoption', 'partnership', 'launch', 'approval', 'record', 'profit', 'growth', 'ath', 'all-time high', 'bullish', 'buy', 'accumulate') if w in text_lower)
    neg = sum(1 for w in ('crash', 'dump', 'bear', 'drop', 'fall', 'hack', 'scam', 'ban', 'fraud', 'lawsuit', 'sec', 'investigation', 'exploit', 'rug', 'bankrupt', 'loss', 'fear', 'bearish', 'sell', 'liquidat', 'collapse', 'plummet') if w in text_lower)
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)

def strip_html(text: str) -> str:
    return unescape(HTML_TAG_RE.sub('', text)).strip()

def extract_symbols_from_text(text: str, coin_map: Dict[str, str]) -> List[str]:
    found = set()
    for m in CRYPTO_TICKER_PATTERN.finditer(text):
        code = m.group(1)
        if code in coin_map:
            found.add(coin_map[code])
    for word in re.split(r'[\s\-_/\\.,;:!?()\[\]{}"\']', text.upper()):
        word_clean = re.sub(r'[^A-Z0-9]', '', word)
        if len(word_clean) >= 3 and word_clean not in COMMON_WORDS and word_clean in coin_map:
            found.add(coin_map[word_clean])
    return list(found)

def extract_stock_tickers(text: str, stock_set: set) -> List[str]:
    found = set()
    for m in STOCK_TICKERS_PATTERN.finditer(text):
        ticker = m.group(1)
        if ticker in stock_set:
            found.add(ticker)
    text_upper = text.upper()
    for word in text_upper.split():
        word_clean = re.sub(r'[^A-Z]', '', word)
        if 1 < len(word_clean) <= 5 and word_clean in stock_set:
            found.add(word_clean)
    for company, ticker in STOCK_COMPANY_MAP.items():
        if company in text_upper and ticker in stock_set:
            found.add(ticker)
    return list(found)

def parse_rss_date(date_str: str) -> Optional[datetime]:
    for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%f%z'):
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc) if 'z' not in fmt.lower() or '+' in date_str or 'Z' in date_str else datetime.strptime(date_str.strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None

def _read_symbol_list(path: Path) -> List[str]:
    try:
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _write_symbol_list(path: Path, symbols: List[str]) -> bool:
    """Atomic writer for crypto lists; TRB uses the locked mutator below."""
    temp_path = path.with_name(
        f'.{path.name}.{os.getpid()}.{time.time_ns()}.tmp'
    )
    try:
        normalized = list(dict.fromkeys(str(s).upper() for s in symbols if s))
        with open(temp_path, 'w') as f:
            json.dump(normalized, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        return True
    except Exception as e:
        logger.error(f"[WRITE] Failed to write {path.name}: {e}")
        return False
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _tradier_derived_files_lock():
    """Serialize news mutations with tradier_rankings publication."""
    with open(TRADIER_DERIVED_FILES_LOCK, 'a+') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _mutate_tradier_symbol_list(
    path: Path, *, add: Optional[List[str]] = None, remove: Optional[List[str]] = None
) -> Optional[Tuple[set, set]]:
    """Atomically mutate a valid TRB list; never rebuild from an empty read."""
    if path.name not in TRADIER_NEWS_WRITABLE_FILES:
        logger.error(f"[INJECT] REFUSED mutation of rankings-owned {path.name}")
        return None
    allowed = set(load_stock_symbols())
    requested_add = list(dict.fromkeys(str(s).upper() for s in (add or []) if s))
    blocked = [s for s in requested_add if s not in allowed]
    if blocked:
        logger.warning(f"[INJECT] Blocked non-master TRB symbols: {blocked}")
    requested_add = [s for s in requested_add if s in allowed]
    requested_remove = set(str(s).upper() for s in (remove or []) if s)
    try:
        with _tradier_derived_files_lock():
            if not path.exists():
                raise ValueError('file is missing')
            with open(path, 'r') as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                raise ValueError('JSON root is not a list')
            current = list(dict.fromkeys(str(s).upper() for s in raw if s))
            if len(current) < 20:
                raise ValueError(f'unsafe existing size {len(current)} (<20)')
            before = set(current)
            updated = [s for s in current if s not in requested_remove]
            updated.extend(s for s in requested_add if s not in set(updated))
            if not _write_symbol_list(path, updated):
                raise OSError('atomic write failed')
            after = set(updated)
            return after - before, before - after
    except Exception as e:
        logger.error(
            f"[INJECT] REFUSED unsafe mutation of {path.name}: {e}; "
            "existing list preserved"
        )
        return None

async def get_current_price(session: aiohttp.ClientSession, symbol: str) -> float:
    try:
        async with session.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data.get('price', 0.0))
    except Exception:
        pass
    return 0.0

# ============================================================
# SOURCE POLLERS
# ============================================================

async def poll_coingecko_trending(session: aiohttp.ClientSession, coin_map: Dict[str, str]) -> Dict[str, float]:
    try:
        headers = {'User-Agent': 'ez_news_scanner/5.0', 'Accept': 'application/json'}
        async with session.get(COINGECKO_TRENDING_URL, timeout=aiohttp.ClientTimeout(total=12), headers=headers) as resp:
            if resp.status == 429:
                logger.debug("[COINGECKO_TREND] Rate limited (429)")
                return {}
            if resp.status != 200:
                logger.debug(f"[COINGECKO_TREND] HTTP {resp.status}")
                return {}
            data = await resp.json()
            result = {}
            coins = data.get('coins', [])
            for i, item in enumerate(coins[:10]):
                coin = item.get('item', {})
                symbol = coin.get('symbol', '').upper()
                name = coin.get('name', '').upper()
                price_change = coin.get('data', {}).get('price_change_percentage_24h', {}).get('usd', 0.0)
                rank_score = max(0.35, 0.88 - (i * 0.07))
                direction = 1 if price_change >= 0 else -1
                final_score = rank_score * direction if direction > 0 else 0.0
                full_sym = coin_map.get(symbol) or coin_map.get(name)
                if full_sym:
                    result[full_sym] = min(1.0, final_score)
                    logger.debug(f"[COINGECKO_TREND] #{i+1} {symbol} ({full_sym}): score={final_score:+.3f} 24h={price_change:+.1f}%")
            logger.info(f"[COINGECKO_TREND] {len(result)} trending coins matched: {list(result.keys())}")
            return result
    except Exception as e:
        logger.warning(f"[COINGECKO_TREND] Error: {e}")
        return {}

async def poll_coingecko_movers(session: aiohttp.ClientSession, coin_map: Dict[str, str], top_n: int = 200) -> List[dict]:
    """CoinGecko /coins/markets — free. Top coins by mcap with 24h change. Big movers (>3%) become strong signals."""
    articles = []
    try:
        headers = {'User-Agent': 'ez_news_scanner/5.0', 'Accept': 'application/json'}
        for page in (1, 2):
            params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 100, 'page': page, 'sparkline': 'false', 'price_change_percentage': '24h'}
            async with session.get(COINGECKO_MARKETS_URL, timeout=aiohttp.ClientTimeout(total=15), params=params, headers=headers) as resp:
                if resp.status == 429:
                    logger.debug("[COINGECKO_MOVERS] Rate limited")
                    break
                if resp.status != 200:
                    break
                data = await resp.json()
                for coin in data:
                    symbol = coin.get('symbol', '').upper()
                    pct_24h = coin.get('price_change_percentage_24h') or 0.0
                    mcap_rank = coin.get('market_cap_rank') or 999
                    full_sym = coin_map.get(symbol)
                    if not full_sym or abs(pct_24h) < 3.0:
                        continue
                    magnitude = min(1.0, abs(pct_24h) / 20.0) * 0.8 + 0.2
                    direction = 1 if pct_24h > 0 else -1
                    reliability = 1.0 if mcap_rank <= 50 else 0.8 if mcap_rank <= 100 else 0.6
                    score = direction * magnitude * reliability
                    articles.append({'symbol': full_sym, 'text': f"24h {pct_24h:+.1f}% (mcap #{mcap_rank})", 'score': score, 'engagement': 15, 'source': 'coingecko_movers', 'published': datetime.now(timezone.utc).isoformat(), 'pct_24h': pct_24h, 'mcap_rank': mcap_rank})
            await asyncio.sleep(1.5)
        logger.info(f"[COINGECKO_MOVERS] {len(articles)} significant movers (>3%) from top {top_n}")
    except Exception as e:
        logger.warning(f"[COINGECKO_MOVERS] Error: {e}")
    return articles

async def poll_fear_greed(session: aiohttp.ClientSession) -> Optional[dict]:
    try:
        async with session.get(FEAR_GREED_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            fg = data.get('data', [{}])[0]
            value = int(fg.get('value', 50))
            classification = fg.get('value_classification', 'Neutral')
            score = (value - 50) / 50.0
            logger.info(f"[FEAR_GREED] {classification} ({value}/100) -> score={score:+.2f}")
            return {'value': value, 'classification': classification, 'score': score}
    except Exception as e:
        logger.warning(f"[FEAR_GREED] Error: {e}")
        return None

async def poll_alphavantage(session: aiohttp.ClientSession, coin_map: Dict[str, str]) -> List[dict]:
    api_key = os.getenv('ALPHAVANTAGE_API_KEY', '')
    if not api_key:
        return []
    articles = []
    try:
        tickers = ','.join(f'CRYPTO:{c}' for c in ('BTC','ETH','SOL','XRP','BNB','DOGE','ADA','AVAX','LINK','DOT') if c in coin_map)
        if not tickers:
            return []
        params = {'function': 'NEWS_SENTIMENT', 'tickers': tickers, 'sort': 'LATEST', 'limit': 50, 'apikey': api_key}
        async with session.get(ALPHAVANTAGE_URL, timeout=aiohttp.ClientTimeout(total=15), params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            if 'Note' in data or 'Information' in data:
                logger.warning(f"[ALPHAVANTAGE] Rate limited: {data.get('Note', data.get('Information', ''))[:100]}")
                return []
            for item in data.get('feed', []):
                title = item.get('title', '')
                published = item.get('time_published', '')
                for ticker_info in item.get('ticker_sentiment', []):
                    ticker = ticker_info.get('ticker', '')
                    ticker_score = float(ticker_info.get('ticker_sentiment_score', 0.0))
                    relevance = float(ticker_info.get('relevance_score', 0.0))
                    if relevance < 0.1:
                        continue
                    if ticker.startswith('CRYPTO:'):
                        base = ticker[7:]
                        resolved = coin_map.get(base)
                        if not resolved:
                            continue
                        ticker = resolved
                    articles.append({'symbol': ticker, 'text': title[:200], 'score': ticker_score, 'engagement': max(1, int(relevance * 20)), 'source': 'alphavantage', 'published': published, 'relevance': relevance})
        logger.info(f"[ALPHAVANTAGE] {len(articles)} crypto ticker-sentiments")
    except Exception as e:
        logger.warning(f"[ALPHAVANTAGE] Error: {e}")
    return articles

async def poll_finnhub(session: aiohttp.ClientSession, coin_map: Dict[str, str], stock_set: set, category: str = 'crypto') -> List[dict]:
    api_key = os.getenv('FINNHUB_API_KEY', '')
    if not api_key:
        return []
    articles = []
    try:
        params = {'category': category, 'token': api_key}
        async with session.get(FINNHUB_NEWS_URL, timeout=aiohttp.ClientTimeout(total=15), params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            for item in data[:50]:
                title = item.get('headline', '')
                summary = item.get('summary', '')[:300]
                combined = f"{title} {summary}"
                published = datetime.fromtimestamp(item.get('datetime', 0), tz=timezone.utc).isoformat() if item.get('datetime') else ''
                sentiment_val = vader_score(combined)
                related_raw = [r.strip().upper() for r in item.get('related', '').split(',') if r.strip()]
                source = item.get('source', 'finnhub')
                if category == 'crypto':
                    resolved = set()
                    for r in related_raw:
                        full = coin_map.get(r)
                        if full:
                            resolved.add(full)
                    for sym in extract_symbols_from_text(combined, coin_map):
                        resolved.add(sym)
                    for sym in resolved:
                        articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment_val, 'engagement': 8, 'source': f'finnhub_{source}', 'published': published})
                    if not resolved:
                        articles.append({'symbol': '_MARKET_', 'text': title[:200], 'score': sentiment_val, 'engagement': 3, 'source': f'finnhub_{source}', 'published': published})
                else:
                    matched = set()
                    for r in related_raw:
                        if r in stock_set:
                            matched.add(r)
                    for t in extract_stock_tickers(combined, stock_set):
                        matched.add(t)
                    for sym in matched:
                        articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment_val, 'engagement': 8, 'source': f'finnhub_{source}', 'published': published, '_is_stock': True})
                    if not matched:
                        articles.append({'symbol': '_MARKET_', 'text': title[:200], 'score': sentiment_val, 'engagement': 3, 'source': f'finnhub_{source}', 'published': published})
        logger.info(f"[FINNHUB:{category}] {len(articles)} mentions")
    except Exception as e:
        logger.warning(f"[FINNHUB:{category}] Error: {e}")
    return articles

async def poll_rss_feeds(session: aiohttp.ClientSession, feeds: List[Tuple[str, str]], coin_map: Dict[str, str], stock_set: set, is_crypto: bool = True) -> List[dict]:
    articles = []
    for feed_url, source in feeds:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; ez_news_scanner/5.0)'}
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=12), headers=headers) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                root = ET.fromstring(text)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                items = root.findall('.//item') or root.findall('.//atom:entry', ns)
                for item in items[:40]:
                    title_el = item.find('title') or item.find('atom:title', ns)
                    title = strip_html(title_el.text or '') if title_el is not None and title_el.text else ''
                    desc_el = item.find('description') or item.find('atom:content', ns) or item.find('atom:summary', ns)
                    desc = strip_html(desc_el.text or '')[:400] if desc_el is not None and desc_el.text else ''
                    combined = f"{title} {desc}"
                    if not combined.strip():
                        continue
                    pub_el = item.find('pubDate') or item.find('atom:updated', ns) or item.find('atom:published', ns)
                    pub_dt = parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else None
                    published = pub_dt.isoformat() if pub_dt else ''
                    sentiment = vader_score(combined)
                    if is_crypto:
                        found = extract_symbols_from_text(combined, coin_map)
                        for sym in found:
                            articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment, 'engagement': 5, 'source': f'rss_{source}', 'published': published})
                        if not found and abs(sentiment) > 0.2:
                            articles.append({'symbol': '_MARKET_', 'text': title[:200], 'score': sentiment, 'engagement': 2, 'source': f'rss_{source}', 'published': published})
                    else:
                        for t in extract_stock_tickers(combined, stock_set):
                            articles.append({'symbol': t, 'text': title[:200], 'score': sentiment, 'engagement': 5, 'source': f'rss_{source}', 'published': published, '_is_stock': True})
        except ET.ParseError:
            pass
        except Exception as e:
            logger.debug(f"[RSS:{source}] Error: {e}")
    logger.info(f"[RSS {'crypto' if is_crypto else 'stocks'}] {len(articles)} mentions from {len(feeds)} feeds")
    return articles

_finnhub_stocks_shard = 0

async def poll_finnhub_stocks(session: aiohttp.ClientSession, stock_set: set, max_symbols: int = 60) -> List[dict]:
    global _finnhub_stocks_shard
    api_key = os.getenv('FINNHUB_API_KEY', '')
    if not api_key or not stock_set:
        return []
    articles = []
    from_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime('%Y-%m-%d')
    to_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    all_syms = sorted(s for s in stock_set if s.isalpha() and len(s) <= 5)
    if not all_syms:
        return []
    shard_count = max(1, (len(all_syms) + max_symbols - 1) // max_symbols)
    start = (_finnhub_stocks_shard % shard_count) * max_symbols
    targets = all_syms[start:start + max_symbols]
    _finnhub_stocks_shard += 1
    for sym in targets:
        try:
            params = {'symbol': sym, 'from': from_date, 'to': to_date, 'token': api_key}
            async with session.get('https://finnhub.io/api/v1/company-news', timeout=aiohttp.ClientTimeout(total=8), params=params) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                for item in data[:10]:
                    title = item.get('headline', '')
                    summary = item.get('summary', '')[:200]
                    combined = f"{title} {summary}"
                    published = datetime.fromtimestamp(item.get('datetime', 0), tz=timezone.utc).isoformat() if item.get('datetime') else ''
                    sentiment_val = vader_score(combined)
                    if combined.strip():
                        articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment_val, 'engagement': 7, 'source': f'finnhub_co_{sym}', 'published': published, '_is_stock': True})
        except Exception as e:
            logger.debug(f"[FINNHUB_STOCKS:{sym}] Error: {e}")
    logger.info(f"[FINNHUB_STOCKS] {len(articles)} mentions for {len(targets)} tickers (shard {_finnhub_stocks_shard-1})")
    return articles

async def poll_stocktwits(session: aiohttp.ClientSession, stock_set: set, max_symbols: int = 30) -> List[dict]:
    articles = []
    all_syms = sorted(s for s in stock_set if s.isalpha() and len(s) <= 5)
    if not all_syms:
        return []
    import random
    batch = random.sample(all_syms, min(max_symbols, len(all_syms)))
    for sym in batch:
        try:
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8), headers={'User-Agent': 'ez_news_scanner/6.0'}) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                for msg in data.get('messages', [])[:15]:
                    body = msg.get('body', '')[:300]
                    created = msg.get('created_at', '')
                    try:
                        pub = datetime.fromisoformat(created.replace('Z', '+00:00')).isoformat() if created else ''
                    except Exception:
                        pub = ''
                    sentiment = vader_score(body)
                    articles.append({'symbol': sym, 'text': body[:200], 'score': sentiment, 'engagement': 6, 'source': f'stocktwits_{sym}', 'published': pub, '_is_stock': True})
        except Exception as e:
            logger.debug(f"[STOCKTWITS:{sym}] Error: {e}")
    logger.info(f"[STOCKTWITS] {len(articles)} mentions for {len(batch)} tickers")
    return articles

async def poll_reddit_search(session: aiohttp.ClientSession, stock_set: set, max_symbols: int = 20) -> List[dict]:
    articles = []
    all_syms = sorted(s for s in stock_set if s.isalpha() and len(s) <= 5)
    if not all_syms:
        return []
    import random
    batch = random.sample(all_syms, min(max_symbols, len(all_syms)))
    for sym in batch:
        try:
            url = f"https://www.reddit.com/r/wallstreetbets/search.json"
            params = {'q': sym, 'restrict_sr': '1', 'sort': 'new', 't': 'day', 'limit': '8'}
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8), headers={'User-Agent': 'ez_news_scanner/6.0'}, params=params) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                for child in data.get('data', {}).get('children', [])[:8]:
                    d = child.get('data', {})
                    title = d.get('title', '')[:200]
                    selftext = d.get('selftext', '')[:300]
                    combined = f"{title} {selftext}"
                    created = d.get('created_utc', 0)
                    pub = datetime.fromtimestamp(created, tz=timezone.utc).isoformat() if created else ''
                    sentiment = vader_score(combined)
                    articles.append({'symbol': sym, 'text': title[:200], 'score': sentiment, 'engagement': 5, 'source': f'reddit_search_{sym}', 'published': pub, '_is_stock': True})
            await asyncio.sleep(0.4)
        except Exception as e:
            logger.debug(f"[REDDIT_SEARCH:{sym}] Error: {e}")
    logger.info(f"[REDDIT_SEARCH] {len(articles)} mentions for {len(batch)} tickers")
    return articles

async def poll_tradingview_ideas(session: aiohttp.ClientSession, stock_set: set, max_symbols: int = 15) -> List[dict]:
    articles = []
    all_syms = sorted(s for s in stock_set if s.isalpha() and len(s) <= 5)
    if not all_syms:
        return []
    import random
    batch = random.sample(all_syms, min(max_symbols, len(all_syms)))
    for sym in batch:
        try:
            url = f"https://www.tradingview.com/symbols/{sym}/ideas/?sort=recent"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8), headers={'User-Agent': 'Mozilla/5.0 (compatible; ez_news_scanner/6.0)'}) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                titles = re.findall(r'class="[^"]*tv-widget-idea__title[^"]*"[^>]*>([^<]{10,120})<', text)[:8]
                for title in titles:
                    clean = strip_html(title).strip()
                    if len(clean) < 10:
                        continue
                    score = vader_score(clean)
                    articles.append({'symbol': sym, 'text': clean[:200], 'score': score, 'engagement': 4, 'source': f'tv_ideas_{sym}', 'published': datetime.now(timezone.utc).isoformat(), '_is_stock': True})
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.debug(f"[TV_IDEAS:{sym}] Error: {e}")
    logger.info(f"[TV_IDEAS] {len(articles)} ideas for {len(batch)} tickers")
    return articles

# ============================================================
# WORLD NEWS POLLING + MACRO-EVENT DETECTION
# ============================================================

async def poll_world_news(session: aiohttp.ClientSession) -> List[dict]:
    """Fetch world/macro news RSS feeds. Returns articles with _is_world=True."""
    articles = []
    for feed_url, source in WORLD_NEWS_RSS_FEEDS:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; ez_news_scanner/6.0)'}
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=12), headers=headers) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                root = ET.fromstring(text)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                items = root.findall('.//item') or root.findall('.//atom:entry', ns)
                for item in items[:30]:
                    title_el = item.find('title') or item.find('atom:title', ns)
                    title = strip_html(title_el.text or '') if title_el is not None and title_el.text else ''
                    desc_el = item.find('description') or item.find('atom:content', ns) or item.find('atom:summary', ns)
                    desc = strip_html(desc_el.text or '')[:500] if desc_el is not None and desc_el.text else ''
                    combined = f"{title} {desc}"
                    if not combined.strip():
                        continue
                    pub_el = item.find('pubDate') or item.find('atom:updated', ns) or item.find('atom:published', ns)
                    pub_dt = parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else None
                    published = pub_dt.isoformat() if pub_dt else ''
                    articles.append({'text': combined, 'title': title[:200], 'source': f'world_{source}', 'published': published, '_is_world': True})
        except ET.ParseError:
            pass
        except Exception as e:
            logger.debug(f"[WORLD_NEWS:{source}] Error: {e}")
    logger.info(f"[WORLD_NEWS] {len(articles)} articles from {len(WORLD_NEWS_RSS_FEEDS)} feeds")
    return articles

def detect_macro_events(articles: List[dict]) -> List[dict]:
    """Scan articles for macro-event keywords. Returns detected events with impact scores and source agreement."""
    events_by_category: Dict[str, List[dict]] = {}
    now = datetime.now(timezone.utc)
    for a in articles:
        if not a.get('_is_world'):
            continue
        text_lower = a.get('text', '').lower()
        pub = a.get('published', '')
        age_hours = 24.0
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
                age_hours = max(0.01, (now - pub_dt).total_seconds() / 3600)
            except Exception:
                pass
        if age_hours > 6.0:
            continue
        for category, cfg in MACRO_IMPACT_KEYWORDS.items():
            matched_kws = [kw for kw in cfg['keywords'] if kw in text_lower]
            if not matched_kws:
                continue
            kw_count = len(matched_kws)
            freshness = max(0.3, 1.0 - (age_hours / 6.0))
            hit_score = cfg['impact'] * freshness * min(1.5, 1.0 + (kw_count - 1) * 0.15)
            events_by_category.setdefault(category, []).append({'source': a.get('source', ''), 'title': a.get('title', '')[:120], 'score': min(1.0, hit_score), 'keywords': matched_kws[:3], 'age_hours': round(age_hours, 2)})
    detected = []
    for category, hits in events_by_category.items():
        source_families = set(h['source'].split('_')[1] if '_' in h['source'] else h['source'] for h in hits)
        n_sources = len(source_families)
        best_score = max(h['score'] for h in hits)
        source_boost = min(2.0, 1.0 + (n_sources - 1) * 0.20)
        final_score = min(1.0, best_score * source_boost)
        best_title = max(hits, key=lambda h: h['score'])['title']
        all_kws = set()
        for h in hits:
            all_kws.update(h['keywords'])
        detected.append({'category': category, 'score': round(final_score, 3), 'n_sources': n_sources, 'n_articles': len(hits), 'sources': list(source_families)[:6], 'keywords': list(all_kws)[:5], 'best_title': best_title, 'base_impact': MACRO_IMPACT_KEYWORDS[category]['impact']})
    detected.sort(key=lambda e: e['score'], reverse=True)
    if detected:
        logger.info(f"[MACRO_DETECT] {len(detected)} macro events detected: {[(e['category'], e['score'], e['n_sources']) for e in detected[:5]]}")
    return detected

async def trigger_extreme_mode_from_news(macro_events: List[dict], redis_mgr) -> Optional[str]:
    """If macro events exceed threshold, trigger EXTREME_MODE via market_mode.json + Redis."""
    global _last_extreme_trigger_time
    if not macro_events:
        return None
    top_event = macro_events[0]
    if top_event['score'] < NEWS_EXTREME_THRESHOLD:
        return None
    if top_event['n_sources'] < NEWS_EXTREME_MIN_SOURCES:
        return None
    now = time.time()
    if (now - _last_extreme_trigger_time) < NEWS_EXTREME_COOLDOWN_SECONDS:
        logger.debug(f"[EXTREME_TRIGGER] Cooldown active ({NEWS_EXTREME_COOLDOWN_SECONDS}s), skipping")
        return None
    qualifying = [e for e in macro_events if e['score'] >= NEWS_EXTREME_THRESHOLD and e['n_sources'] >= NEWS_EXTREME_MIN_SOURCES]
    if not qualifying:
        return None
    reason_parts = [f"{e['category']}({e['score']:.2f}, {e['n_sources']}src)" for e in qualifying[:3]]
    reason = f"NEWS_EXTREME: {', '.join(reason_parts)}"
    mode_data = {'market_index': 80.0, 'mode': 'EXTREME_MODE', 'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'thresholds': {'extreme': 65, 'light': 35}, 'mode_changed': True, 'news_trigger': True, 'news_reason': reason, 'news_events': [{'category': e['category'], 'score': e['score'], 'n_sources': e['n_sources'], 'best_title': e['best_title']} for e in qualifying[:5]]}
    mode_file = BASE_PATH / MARKET_MODE_FILE
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with aiofiles.open(mode_file, 'w') as f:
            await f.write(json.dumps(mode_data, indent=2))
    except Exception as e:
        logger.error(f"[EXTREME_TRIGGER] Failed to write market_mode.json: {e}")
        return None
    Config.set_market_mode('EXTREME_MODE')
    if redis_mgr and hasattr(redis_mgr, 'connections'):
        for name, conn in redis_mgr.connections.items():
            if conn is None:
                continue
            try:
                await conn.setex('news_extreme_mode', 3600, json.dumps(mode_data))
                await conn.publish('market_mode_change', json.dumps({'mode': 'EXTREME_MODE', 'source': 'news_scanner', 'reason': reason}))
            except Exception as e:
                logger.debug(f"Redis {name} extreme mode publish failed: {e}")
    _last_extreme_trigger_time = now
    logger.warning(f"[EXTREME_TRIGGER] EXTREME_MODE activated from world news: {reason}")
    return reason

async def check_extreme_mode_expiry(redis_mgr):
    """If EXTREME_MODE was triggered by news and no new triggers in 30 min, revert to NORMAL_MODE."""
    mode_file = BASE_PATH / MARKET_MODE_FILE
    try:
        if not mode_file.exists():
            return
        async with aiofiles.open(mode_file, 'r') as f:
            mode_data = json.loads(await f.read())
        if mode_data.get('mode') != 'EXTREME_MODE' or not mode_data.get('news_trigger'):
            return
        triggered_at = datetime.fromisoformat(mode_data['timestamp'].replace('Z', '+00:00'))
        age_minutes = (datetime.now(timezone.utc) - triggered_at).total_seconds() / 60
        if age_minutes < 30:
            return
        revert_data = {'market_index': 50.0, 'mode': 'NORMAL_MODE', 'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'thresholds': {'extreme': 65, 'light': 35}, 'mode_changed': True, 'news_trigger': False, 'reverted_from': 'NEWS_EXTREME', 'extreme_duration_min': round(age_minutes, 1)}
        async with aiofiles.open(mode_file, 'w') as f:
            await f.write(json.dumps(revert_data, indent=2))
        Config.set_market_mode('NORMAL_MODE')
        if redis_mgr and hasattr(redis_mgr, 'connections'):
            for name, conn in redis_mgr.connections.items():
                if conn is None:
                    continue
                try:
                    await conn.publish('market_mode_change', json.dumps({'mode': 'NORMAL_MODE', 'source': 'news_scanner', 'reason': f'NEWS_EXTREME expired after {age_minutes:.0f}min'}))
                except Exception:
                    pass
        logger.info(f"[EXTREME_EXPIRY] Reverted to NORMAL_MODE after {age_minutes:.0f}min news-triggered EXTREME")
    except Exception as e:
        logger.debug(f"[EXTREME_EXPIRY] Check error: {e}")

# ============================================================
# AGGREGATION (daemon mode — continuous Redis sentiment)
# ============================================================

def aggregate_scores(articles: List[dict], trending_coins: Dict[str, float], coin_map: Dict[str, str], decay_hours: int = 4, min_articles: int = 2, fear_greed: Optional[dict] = None) -> Tuple[Dict[str, float], Dict[str, float]]:
    now = datetime.now(timezone.utc)
    crypto_per_sym: Dict[str, List[Tuple[float, float]]] = {}
    stock_per_sym: Dict[str, List[Tuple[float, float]]] = {}
    fg_bias = fear_greed['score'] * 0.12 if fear_greed else 0.0
    for a in articles:
        sym = a['symbol']
        if sym in ('_MARKET_', ''):
            continue
        is_stock = a.get('_is_stock', False)
        score = a.get('score', 0.0)
        engagement = a.get('engagement', 1)
        pub = a.get('published', '')
        age_hours = decay_hours
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
                age_hours = max(0.01, (now - pub_dt).total_seconds() / 3600)
            except Exception:
                pass
        decay = max(0.1, 1.0 - (age_hours / decay_hours)) if age_hours < decay_hours else 0.1
        weight = decay * min(engagement, 1000) ** 0.3
        if is_stock:
            stock_per_sym.setdefault(sym, []).append((score * weight, weight))
        else:
            crypto_per_sym.setdefault(sym, []).append((score * weight, weight))
    for sym, trend_score in trending_coins.items():
        crypto_per_sym.setdefault(sym, []).append((trend_score * 6.0, 6.0))
    crypto_scores: Dict[str, float] = {}
    for sym, weighted in crypto_per_sym.items():
        if len(weighted) < min_articles and sym not in trending_coins:
            continue
        total_weight = sum(w for _, w in weighted)
        if total_weight <= 0:
            continue
        raw = sum(s for s, _ in weighted) / total_weight + fg_bias
        crypto_scores[sym] = max(-1.0, min(1.0, raw))
    stock_scores: Dict[str, float] = {}
    for sym, weighted in stock_per_sym.items():
        if len(weighted) < 1:
            continue
        total_weight = sum(w for _, w in weighted)
        if total_weight <= 0:
            continue
        raw = sum(s for s, _ in weighted) / total_weight
        stock_scores[sym] = max(-1.0, min(1.0, raw))
    return crypto_scores, stock_scores

# ============================================================
# CONVICTION SCORING (daily mode — pick selection)
# ============================================================

def score_picks(articles: List[dict], trending_coins: Dict[str, float], coin_map: Dict[str, str], fear_greed: Optional[dict] = None) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    """Multi-source agreement scoring. Returns (crypto_long, crypto_short, stock_long, stock_short) picks."""
    crypto_mentions: Dict[str, List[dict]] = {}
    stock_mentions: Dict[str, List[dict]] = {}
    for a in articles:
        sym = a['symbol']
        if sym in ('_MARKET_', ''):
            continue
        bucket = stock_mentions if a.get('_is_stock') else crypto_mentions
        bucket.setdefault(sym, []).append({'score': a.get('score', 0.0), 'source': a.get('source', ''), 'text': a.get('text', ''), 'engagement': a.get('engagement', 1)})
    for sym, trend_score in trending_coins.items():
        crypto_mentions.setdefault(sym, []).append({'score': trend_score, 'source': 'coingecko_trending', 'text': f'Trending (score={trend_score:.2f})', 'engagement': 20})
    fg_bias = fear_greed['score'] * 0.1 if fear_greed else 0.0
    def _rank(mentions, fg=0.0):
        picks = []
        for sym, entries in mentions.items():
            source_families = set(e['source'].split('_')[0] for e in entries)
            n_sources = len(source_families)
            total_w = sum(e['engagement'] for e in entries)
            if total_w == 0:
                continue
            avg_score = sum(e['score'] * e['engagement'] for e in entries) / total_w + fg
            avg_score = max(-1.0, min(1.0, avg_score))
            source_boost = min(2.0, 1.0 + (n_sources - 1) * 0.25)
            conviction = min(1.0, abs(avg_score) * source_boost)
            top_text = max(entries, key=lambda e: e['engagement'])['text']
            picks.append({'symbol': sym, 'conviction': round(conviction, 3), 'direction': 'LONG' if avg_score > 0 else 'SHORT', 'avg_score': round(avg_score, 3), 'n_sources': n_sources, 'n_mentions': len(entries), 'sources': list(set(e['source'] for e in entries))[:5], 'reason': top_text[:120]})
        picks.sort(key=lambda p: p['conviction'], reverse=True)
        return picks
    crypto_picks = _rank(crypto_mentions, fg_bias)
    stock_picks = _rank(stock_mentions)
    crypto_long = [p for p in crypto_picks if p['direction'] == 'LONG' and p['conviction'] >= MIN_CONVICTION][:MAX_CRYPTO_PICKS]
    crypto_short = [p for p in crypto_picks if p['direction'] == 'SHORT' and p['conviction'] >= MIN_CONVICTION][:MAX_CRYPTO_PICKS]
    stock_long = [p for p in stock_picks if p['direction'] == 'LONG' and p['conviction'] >= MIN_CONVICTION][:MAX_STOCK_PICKS]
    stock_short = [p for p in stock_picks if p['direction'] == 'SHORT' and p['conviction'] >= MIN_CONVICTION][:MAX_STOCK_PICKS]
    return crypto_long, crypto_short, stock_long, stock_short

# ============================================================
# SYMBOL INJECTION SYSTEM
# ============================================================

def load_injections() -> dict:
    try:
        if INJECTION_FILE.exists():
            with open(INJECTION_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {'active': [], 'history': []}

def save_injections(data: dict):
    temp_path = INJECTION_FILE.with_name(
        f'.{INJECTION_FILE.name}.{os.getpid()}.{time.time_ns()}.tmp'
    )
    try:
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, INJECTION_FILE)
    except Exception as e:
        logger.error(f"[INJECT] Save error: {e}")
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass

def _resolve_injection_path(filename: str) -> Path:
    """Resolve a symbol list filename to the current BASE_PATH, regardless of where it was created."""
    return BASE_PATH / Path(filename).name

def inject_symbols(crypto_long: List[dict], crypto_short: List[dict], stock_long: List[dict], stock_short: List[dict]) -> List[dict]:
    """Inject picked symbols into account symbol lists. Track all injections."""
    injections = load_injections()
    now_iso = datetime.now(timezone.utc).isoformat()
    already_active = {(i['symbol'], i['account'], i['side']) for i in injections['active']}
    active_by_key = {
        (i['symbol'], i['account'], i['side']): i for i in injections['active']
    }
    new_injections = []
    for acct, targets in CRYPTO_INJECT_TARGETS.items():
        if 'single' in targets:
            path = targets['single']
            current = _read_symbol_list(path)
            added = []
            for pick in crypto_long + crypto_short:
                sym = pick['symbol']
                key = (sym, acct, pick['direction'])
                if key in already_active:
                    continue
                was_new = sym not in current
                if was_new:
                    current.append(sym)
                    added.append(sym)
                new_injections.append({'symbol': sym, 'account': acct, 'side': pick['direction'], 'file': path.name, 'injected_at': now_iso, 'conviction': pick['conviction'], 'reason': pick['reason'][:80], 'was_new': was_new})
            if added:
                _write_symbol_list(path, current)
                logger.info(f"[INJECT] {acct}: +{len(added)} symbols -> {path.name}: {added}")
        else:
            for side_key, picks in [('long', crypto_long), ('short', crypto_short)]:
                path = targets[side_key]
                current = _read_symbol_list(path)
                added = []
                direction = 'LONG' if side_key == 'long' else 'SHORT'
                for pick in picks:
                    sym = pick['symbol']
                    key = (sym, acct, direction)
                    if key in already_active:
                        continue
                    was_new = sym not in current
                    if was_new:
                        current.append(sym)
                        added.append(sym)
                    new_injections.append({'symbol': sym, 'account': acct, 'side': direction, 'file': path.name, 'injected_at': now_iso, 'conviction': pick['conviction'], 'reason': pick['reason'][:80], 'was_new': was_new})
                if added:
                    _write_symbol_list(path, current)
                    logger.info(f"[INJECT] {acct}_{side_key}: +{len(added)} symbols -> {path.name}: {added}")
    allowed_stocks = set(load_stock_symbols())
    for acct in STOCK_INJECT_ACCOUNTS:
        for side_key, picks in [('long', stock_long), ('short', stock_short)]:
            direction = 'LONG' if side_key == 'long' else 'SHORT'
            eligible = []
            ensure_symbols = []
            for pick in picks:
                sym = pick['symbol'].upper()
                if sym not in allowed_stocks:
                    logger.warning(
                        f"[INJECT] Blocked stock {sym}: not in symbols_tradier.json"
                    )
                    continue
                ensure_symbols.append(sym)
                key = (sym, acct, direction)
                if key in already_active:
                    active_by_key[key]['last_relevant_at'] = now_iso
                    active_by_key[key]['conviction'] = pick['conviction']
                    active_by_key[key]['reason'] = pick['reason'][:80]
                    continue
                eligible.append(pick)
            if not ensure_symbols:
                continue
            path = BASE_PATH / f'symbols_{acct}_{side_key}.json'
            mutation = _mutate_tradier_symbol_list(
                path, add=ensure_symbols
            )
            if mutation is None:
                continue
            actually_added, _ = mutation
            for sym in ensure_symbols:
                key = (sym, acct, direction)
                if key in active_by_key and sym in actually_added:
                    active_by_key[key]['was_new'] = True
                    active_by_key[key]['direct_write'] = True
            for pick in eligible:
                sym = pick['symbol'].upper()
                new_injections.append({
                    'symbol': sym,
                    'account': acct,
                    'side': direction,
                    'file': path.name,
                    'injected_at': now_iso,
                    'last_relevant_at': now_iso,
                    'conviction': pick['conviction'],
                    'reason': pick['reason'][:80],
                    '_is_stock': True,
                    'was_new': sym in actually_added,
                    'direct_write': True,
                })
            if actually_added:
                logger.info(
                    f"[INJECT] {acct}_{side_key}: atomically added "
                    f"{len(actually_added)} allowlisted stocks: {sorted(actually_added)}"
                )
    injections['active'].extend(new_injections)
    save_injections(injections)
    return new_injections

def cleanup_expired() -> List[dict]:
    """Remove symbols after they have been irrelevant for the configured TTL."""
    injections = load_injections()
    now = datetime.now(timezone.utc)
    still_active = []
    removed = []
    for inj in injections['active']:
        try:
            relevance_time = inj.get('last_relevant_at', inj['injected_at'])
            injected_at = datetime.fromisoformat(relevance_time)
        except Exception:
            still_active.append(inj)
            continue
        age_hours = (now - injected_at).total_seconds() / 3600
        if age_hours > INJECTION_TTL_HOURS:
            path = _resolve_injection_path(inj['file'])
            sym = inj['symbol']
            if inj.get('_is_stock') and inj.get('was_new', True):
                mutation = _mutate_tradier_symbol_list(path, remove=[sym])
                if mutation is not None and sym in mutation[1]:
                    logger.info(
                        f"[CLEANUP] Removed expired news-added {sym} from "
                        f"{path.name} (age={age_hours:.1f}h)"
                    )
            elif inj.get('was_new', True):
                current = _read_symbol_list(path)
                if sym in current:
                    current.remove(sym)
                    _write_symbol_list(path, current)
                    logger.info(f"[CLEANUP] Removed {sym} from {path.name} ({inj['account']}, age={age_hours:.1f}h)")
            inj['removed_at'] = now.isoformat()
            inj['age_hours'] = round(age_hours, 1)
            injections['history'].append(inj)
            removed.append(inj)
        else:
            still_active.append(inj)
    injections['active'] = still_active
    injections['history'] = injections['history'][-500:]
    save_injections(injections)
    return removed

def re_inject_active() -> int:
    """Re-inject active (non-expired) symbols if ez_rankings.py overwrote the lists. Only re-injects symbols we originally added."""
    injections = load_injections()
    re_added = 0
    files_modified = set()
    for inj in injections['active']:
        if inj.get('_is_stock'):
            path = _resolve_injection_path(inj['file'])
            mutation = _mutate_tradier_symbol_list(path, add=[inj['symbol']])
            if mutation is not None and inj['symbol'].upper() in mutation[0]:
                re_added += 1
                files_modified.add(path.name)
            continue
        if not inj.get('was_new', True):
            continue
        path = _resolve_injection_path(inj['file'])
        current = _read_symbol_list(path)
        if inj['symbol'] not in current:
            current.append(inj['symbol'])
            _write_symbol_list(path, current)
            re_added += 1
            files_modified.add(path.name)
    if re_added:
        logger.info(f"[RE-INJECT] Re-added {re_added} symbols removed by rankings cycle: {list(files_modified)}")
    return re_added

# ============================================================
# REDIS PUBLISHING
# ============================================================

async def publish_to_redis(crypto_scores: Dict[str, float], stock_scores: Dict[str, float], fear_greed: Optional[dict], redis_mgr) -> bool:
    try:
        combined = {**crypto_scores, **stock_scores}
        bulk_json = json.dumps(combined)
        crypto_json = json.dumps(crypto_scores)
        stock_json = json.dumps(stock_scores)
        meta = {'last_poll': datetime.now(timezone.utc).isoformat(), 'total_symbols': len(combined), 'crypto_symbols': len(crypto_scores), 'stock_symbols': len(stock_scores), 'avg_score_crypto': sum(crypto_scores.values()) / max(1, len(crypto_scores)), 'sources': 'v5_coingecko+movers+alphavantage+finnhub+rss+fear_greed'}
        if fear_greed:
            meta['fear_greed'] = fear_greed
        meta_json = json.dumps(meta)
        if redis_mgr and hasattr(redis_mgr, 'connections'):
            for name, conn in redis_mgr.connections.items():
                if conn is None:
                    continue
                try:
                    await conn.setex('news_sentiment_bulk', 14400, bulk_json)
                    await conn.setex('news_sentiment_crypto', 14400, crypto_json)
                    if stock_scores:
                        await conn.setex('news_sentiment_stocks', 14400, stock_json)
                    await conn.setex('news_sentiment_meta', 14400, meta_json)
                except Exception as e:
                    logger.debug(f"Redis {name} publish failed: {e}")
        return True
    except Exception as e:
        logger.error(f"[REDIS] Publish error: {e}")
        return False

async def boost_sentiment_redis(crypto_long: List[dict], crypto_short: List[dict], stock_long: List[dict], stock_short: List[dict], redis_mgr):
    """Set strong conviction-based sentiment scores in Redis. Rankings reads these and applies a 0.1x-1.9x multiplier."""
    try:
        existing_crypto = {}
        existing_stocks = {}
        if redis_mgr and hasattr(redis_mgr, 'connections'):
            for name, conn in redis_mgr.connections.items():
                if conn is None:
                    continue
                try:
                    raw_c = await conn.get('news_sentiment_crypto')
                    if raw_c:
                        existing_crypto = json.loads(raw_c)
                    raw_s = await conn.get('news_sentiment_stocks')
                    if raw_s:
                        existing_stocks = json.loads(raw_s)
                    break
                except Exception:
                    pass
        for pick in crypto_long:
            existing_crypto[pick['symbol']] = min(1.0, pick['conviction'] * 1.3)
        for pick in crypto_short:
            existing_crypto[pick['symbol']] = max(-1.0, -pick['conviction'] * 1.3)
        for pick in stock_long:
            existing_stocks[pick['symbol']] = min(1.0, pick['conviction'] * 1.3)
        for pick in stock_short:
            existing_stocks[pick['symbol']] = max(-1.0, -pick['conviction'] * 1.3)
        combined = {**existing_crypto, **existing_stocks}
        if redis_mgr and hasattr(redis_mgr, 'connections'):
            for name, conn in redis_mgr.connections.items():
                if conn is None:
                    continue
                try:
                    await conn.setex('news_sentiment_bulk', 14400, json.dumps(combined))
                    await conn.setex('news_sentiment_crypto', 14400, json.dumps(existing_crypto))
                    if existing_stocks:
                        await conn.setex('news_sentiment_stocks', 14400, json.dumps(existing_stocks))
                except Exception as e:
                    logger.debug(f"Redis {name} boost failed: {e}")
        logger.info(f"[BOOST] Redis sentiment boosted: {len(crypto_long)}L+{len(crypto_short)}S crypto, {len(stock_long)}L+{len(stock_short)}S stocks")
    except Exception as e:
        logger.error(f"[BOOST] Error: {e}")

async def save_fallback(crypto_scores: Dict[str, float], stock_scores: Dict[str, float]):
    try:
        combined = {**crypto_scores, **stock_scores}
        with open(DATA_DIR / 'news_sentiment.json', 'w') as f:
            json.dump(combined, f)
        with open(DATA_DIR / 'news_sentiment_crypto.json', 'w') as f:
            json.dump(crypto_scores, f)
        if stock_scores:
            with open(DATA_DIR / 'news_sentiment_stocks.json', 'w') as f:
                json.dump(stock_scores, f)
    except Exception as e:
        logger.error(f"[FALLBACK] Save error: {e}")

# ============================================================
# PERFORMANCE TRACKING + 96h PRICE HISTORY
# ============================================================

NEWS_TRACK_DIR = DATA_DIR / 'news_tracker'
NEWS_TRACK_DIR.mkdir(parents=True, exist_ok=True)
NEWS_TRACK_INDEX = NEWS_TRACK_DIR / 'index.json'
NEWS_TRACK_HISTORY_HOURS = 96


def _track_id(symbol: str, side: str, ts_iso: str) -> str:
    return f"{symbol}_{side}_{ts_iso[:13].replace(':', '').replace('-', '').replace('T', '_')}"


def load_track_index() -> Dict:
    try:
        with open(NEWS_TRACK_INDEX) as f:
            return json.load(f)
    except Exception:
        return {'recommendations': []}


def save_track_index(data: Dict):
    with open(NEWS_TRACK_INDEX, 'w') as f:
        json.dump(data, f, indent=1)


def register_recommendation(symbol: str, side: str, conviction: float, reason: str, entry_price: float, sources: List[str] = None, n_sources: int = 0):
    """Register a new recommendation for 96h price tracking."""
    idx = load_track_index()
    now_iso = datetime.now(timezone.utc).isoformat()
    track_id = _track_id(symbol, side, now_iso)
    rec = {'track_id': track_id, 'symbol': symbol, 'side': side, 'conviction': conviction, 'reason': reason[:120], 'entry_price': entry_price, 'recommended_at': now_iso, 'sources': (sources or [])[:5], 'n_sources': n_sources, 'status': 'active', 'snapshots': 0, 'latest_price': entry_price, 'latest_pnl_pct': 0.0, 'peak_pnl_pct': 0.0, 'trough_pnl_pct': 0.0, 'final_pnl_pct': None}
    idx['recommendations'].append(rec)
    # Write first snapshot
    snap_file = NEWS_TRACK_DIR / f'{track_id}.jsonl'
    snap = {'ts': now_iso, 'price': entry_price, 'pnl_pct': 0.0, 'elapsed_h': 0.0}
    with open(snap_file, 'a') as f:
        f.write(json.dumps(snap) + '\n')
    save_track_index(idx)
    logger.info(f"[TRACK] Registered {track_id}: {symbol} {side} @ {entry_price:.4f} conv={conviction:.2f}")


async def update_price_snapshots(session: aiohttp.ClientSession):
    """Fetch current prices for all active recommendations and append snapshots."""
    idx = load_track_index()
    now = datetime.now(timezone.utc)
    updated = False
    for rec in idx['recommendations']:
        if rec['status'] != 'active':
            continue
        try:
            rec_time = datetime.fromisoformat(rec['recommended_at'])
        except Exception:
            continue
        if rec_time.tzinfo is None:
            rec_time = rec_time.replace(tzinfo=timezone.utc)
        elapsed_h = (now - rec_time).total_seconds() / 3600
        if elapsed_h > NEWS_TRACK_HISTORY_HOURS:
            rec['status'] = 'completed'
            rec['final_pnl_pct'] = rec.get('latest_pnl_pct', 0.0)
            updated = True
            logger.info(f"[TRACK] Completed {rec['track_id']}: final_pnl={rec['final_pnl_pct']:.2f}%")
            continue
        sym = rec['symbol']
        if rec.get('_is_stock'):
            continue
        price = await get_current_price(session, sym)
        if price <= 0:
            continue
        entry = rec['entry_price']
        if rec['side'] == 'LONG':
            pnl_pct = (price - entry) / entry * 100
        else:
            pnl_pct = (entry - price) / entry * 100
        pnl_pct = round(pnl_pct, 4)
        rec['latest_price'] = price
        rec['latest_pnl_pct'] = pnl_pct
        rec['peak_pnl_pct'] = max(rec.get('peak_pnl_pct', 0.0), pnl_pct)
        rec['trough_pnl_pct'] = min(rec.get('trough_pnl_pct', 0.0), pnl_pct)
        rec['snapshots'] = rec.get('snapshots', 0) + 1
        snap_file = NEWS_TRACK_DIR / f"{rec['track_id']}.jsonl"
        snap = {'ts': now.isoformat(), 'price': price, 'pnl_pct': pnl_pct, 'elapsed_h': round(elapsed_h, 2)}
        with open(snap_file, 'a') as f:
            f.write(json.dumps(snap) + '\n')
        updated = True
    if updated:
        save_track_index(idx)


async def track_injection_performance(session: aiohttp.ClientSession):
    """Check current prices of injected symbols and log directional accuracy."""
    injections = load_injections()
    updated = False
    for inj in injections['active']:
        if inj.get('_is_stock'):
            continue
        sym = inj['symbol']
        price = await get_current_price(session, sym)
        if price <= 0:
            continue
        if 'entry_price' not in inj:
            inj['entry_price'] = price
            # Register for 96h tracking on first price
            register_recommendation(sym, inj.get('side', 'LONG'), inj.get('conviction', 0.5), inj.get('reason', ''), price, sources=inj.get('sources', []), n_sources=inj.get('n_sources', 1))
            updated = True
            continue
        entry = inj['entry_price']
        if inj['side'] == 'LONG':
            pnl_pct = (price - entry) / entry * 100
        else:
            pnl_pct = (entry - price) / entry * 100
        inj['current_price'] = price
        inj['pnl_pct'] = round(pnl_pct, 2)
        updated = True
    if updated:
        save_injections(injections)
    # Update 96h price snapshots for all active recommendations
    await update_price_snapshots(session)

# ============================================================
# FETCH ALL SOURCES (shared by daily + daemon)
# ============================================================

async def fetch_all_sources(session: aiohttp.ClientSession, coin_map: Dict[str, str], stock_set: set) -> Tuple[Optional[dict], Dict[str, float], List[dict], List[dict]]:
    """Fetch all sources in parallel. Returns (fear_greed, trending_coins, all_articles, world_articles)."""
    tasks = [
        poll_fear_greed(session),
        poll_coingecko_trending(session, coin_map),
        poll_coingecko_movers(session, coin_map),
        poll_rss_feeds(session, CRYPTO_RSS_FEEDS, coin_map, stock_set, is_crypto=True),
        poll_rss_feeds(session, STOCK_RSS_FEEDS, coin_map, stock_set, is_crypto=False),
        poll_finnhub(session, coin_map, stock_set, category='crypto'),
        poll_finnhub(session, coin_map, stock_set, category='general'),
        poll_finnhub_stocks(session, stock_set),
        poll_alphavantage(session, coin_map),
        poll_world_news(session),
        poll_stocktwits(session, stock_set, max_symbols=30),
        poll_reddit_search(session, stock_set, max_symbols=20),
        poll_tradingview_ideas(session, stock_set, max_symbols=15),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    fg = results[0] if not isinstance(results[0], Exception) else None
    trending = results[1] if not isinstance(results[1], Exception) else {}
    all_articles = []
    for r in results[2:12]:
        if isinstance(r, list):
            all_articles.extend(r)
    world_articles = results[12] if isinstance(results[12], list) else []
    return fg, trending, all_articles, world_articles

# ============================================================
# DAILY SCAN MODE
# ============================================================

async def daily_scan():
    """Full daily scan: cleanup expired -> fetch -> score -> inject -> boost -> track -> report."""
    logger.info("=== DAILY NEWS SCAN v5 START ===")
    print("=" * 70)
    print("NEWS SCANNER v5 — DAILY SCAN")
    print("=" * 70)
    removed = cleanup_expired()
    if removed:
        print(f"\nCLEANUP: Removed {len(removed)} expired symbols")
        for r in removed:
            print(f"  - {r['symbol']} from {Path(r['file']).name} ({r['account']}, age={r.get('age_hours', '?')}h)")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_set = set(load_stock_symbols())
    redis_mgr = await get_simple_redis_manager()
    async with aiohttp.ClientSession() as session:
        fg, trending, all_articles, world_articles = await fetch_all_sources(session, coin_map, stock_set)
        macro_events = detect_macro_events(world_articles)
        extreme_reason = await trigger_extreme_mode_from_news(macro_events, redis_mgr)
        print(f"\nMARKET CONTEXT:")
        if fg:
            print(f"  Fear & Greed: {fg['classification']} ({fg['value']}/100)")
        print(f"  CoinGecko Trending: {list(trending.keys())[:7]}")
        print(f"  Total articles/signals: {len(all_articles)}")
        print(f"  World news articles: {len(world_articles)}")
        if macro_events:
            print(f"\n  MACRO EVENTS DETECTED ({len(macro_events)}):")
            for ev in macro_events[:5]:
                marker = " ** EXTREME TRIGGER **" if ev['score'] >= NEWS_EXTREME_THRESHOLD and ev['n_sources'] >= NEWS_EXTREME_MIN_SOURCES else ""
                print(f"    {ev['category']:15s}  score={ev['score']:.2f}  sources={ev['n_sources']}  articles={ev['n_articles']}{marker}")
                print(f"                     [{', '.join(ev['sources'][:4])}] kw: {', '.join(ev['keywords'][:3])}")
                print(f"                     {ev['best_title'][:80]}")
        if extreme_reason:
            print(f"\n  *** EXTREME_MODE ACTIVATED: {extreme_reason} ***")
        crypto_long, crypto_short, stock_long, stock_short = score_picks(all_articles, trending, coin_map, fg)
        crypto_scores, stock_scores = aggregate_scores(all_articles, trending, coin_map, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=1, fear_greed=fg)
        await publish_to_redis(crypto_scores, stock_scores, fg, redis_mgr)
        await save_fallback(crypto_scores, stock_scores)
        print(f"\nCRYPTO LONG PICKS ({len(crypto_long)}):")
        for p in crypto_long:
            print(f"  {p['symbol']:>15s}  conviction={p['conviction']:.2f}  sources={p['n_sources']}  mentions={p['n_mentions']}")
            print(f"                   [{', '.join(p['sources'][:3])}]")
            print(f"                   {p['reason'][:80]}")
        print(f"\nCRYPTO SHORT PICKS ({len(crypto_short)}):")
        for p in crypto_short:
            print(f"  {p['symbol']:>15s}  conviction={p['conviction']:.2f}  sources={p['n_sources']}  mentions={p['n_mentions']}")
            print(f"                   [{', '.join(p['sources'][:3])}]")
            print(f"                   {p['reason'][:80]}")
        print(f"\nSTOCK LONG PICKS ({len(stock_long)}):")
        for p in stock_long:
            print(f"  {p['symbol']:>10s}  conviction={p['conviction']:.2f}  sources={p['n_sources']}  [{', '.join(p['sources'][:3])}]")
        print(f"\nSTOCK SHORT PICKS ({len(stock_short)}):")
        for p in stock_short:
            print(f"  {p['symbol']:>10s}  conviction={p['conviction']:.2f}  sources={p['n_sources']}  [{', '.join(p['sources'][:3])}]")
        if not crypto_long and not crypto_short and not stock_long and not stock_short:
            print("\n  No high-conviction picks today (min conviction threshold not met).")
            logger.info("[DAILY] No picks met conviction threshold")
            daily_data = {'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'time': datetime.now(timezone.utc).isoformat(), 'fear_greed': fg, 'crypto_long': [], 'crypto_short': [], 'stock_long': [], 'stock_short': [], 'total_articles': len(all_articles), 'trending': list(trending.keys()), 'picks_count': 0}
            with open(DAILY_PICKS_FILE, 'w') as f:
                json.dump(daily_data, f, indent=2)
            print(f"\n{'=' * 70}")
            return
        new_inj = inject_symbols(crypto_long, crypto_short, stock_long, stock_short)
        print(f"\nINJECTED: {len(new_inj)} symbol-account pairs")
        for inj in new_inj:
            print(f"  {inj['symbol']:>15s} -> {inj['account']}_{inj['side']} (conviction={inj['conviction']:.2f})")
        await boost_sentiment_redis(crypto_long, crypto_short, stock_long, stock_short, redis_mgr)
        await track_injection_performance(session)
        injections = load_injections()
        active_with_pnl = [i for i in injections['active'] if 'pnl_pct' in i]
        if active_with_pnl:
            print(f"\nACTIVE INJECTION PERFORMANCE:")
            total_pnl = 0.0
            for i in active_with_pnl:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(i['injected_at'])).total_seconds() / 3600
                print(f"  {i['symbol']:>15s} {i['side']:5s}  pnl={i['pnl_pct']:+.2f}%  age={age:.0f}h  conviction={i.get('conviction', 0):.2f}")
                total_pnl += i['pnl_pct']
            correct = sum(1 for i in active_with_pnl if i['pnl_pct'] > 0)
            print(f"  Accuracy: {correct}/{len(active_with_pnl)} ({correct/len(active_with_pnl)*100:.0f}%)  Total directional P&L: {total_pnl:+.1f}%")
        recent_history = injections.get('history', [])[-20:]
        if recent_history:
            hist_with_pnl = [h for h in recent_history if 'pnl_pct' in h]
            if hist_with_pnl:
                hist_correct = sum(1 for h in hist_with_pnl if h['pnl_pct'] > 0)
                hist_total_pnl = sum(h['pnl_pct'] for h in hist_with_pnl)
                print(f"\nRECENT HISTORY ({len(hist_with_pnl)} closed):")
                print(f"  Accuracy: {hist_correct}/{len(hist_with_pnl)} ({hist_correct/len(hist_with_pnl)*100:.0f}%)  Total: {hist_total_pnl:+.1f}%")
        daily_data = {'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'time': datetime.now(timezone.utc).isoformat(), 'fear_greed': fg, 'crypto_long': crypto_long, 'crypto_short': crypto_short, 'stock_long': stock_long, 'stock_short': stock_short, 'total_articles': len(all_articles), 'trending': list(trending.keys()), 'picks_count': len(crypto_long) + len(crypto_short) + len(stock_long) + len(stock_short), 'injected': len(new_inj)}
        with open(DAILY_PICKS_FILE, 'w') as f:
            json.dump(daily_data, f, indent=2)
    print(f"\n{'=' * 70}")
    logger.info(f"[DAILY] Complete: {len(crypto_long)}CL {len(crypto_short)}CS {len(stock_long)}SL {len(stock_short)}SS, {len(new_inj)} injected")

# ============================================================
# REPORT MODE
# ============================================================

async def report_mode():
    """Show current state of all injections and their performance."""
    print("=" * 70)
    print("NEWS SCANNER v6 — INJECTION REPORT")
    print("=" * 70)
    injections = load_injections()
    now = datetime.now(timezone.utc)
    print(f"\nACTIVE INJECTIONS ({len(injections['active'])}):")
    if not injections['active']:
        print("  (none)")
    async with aiohttp.ClientSession() as session:
        for inj in injections['active']:
            age = 0
            try:
                age = (now - datetime.fromisoformat(inj['injected_at'])).total_seconds() / 3600
            except Exception:
                pass
            if not inj.get('_is_stock') and 'entry_price' in inj:
                price = await get_current_price(session, inj['symbol'])
                if price > 0:
                    entry = inj['entry_price']
                    pnl = ((price - entry) / entry * 100) if inj['side'] == 'LONG' else ((entry - price) / entry * 100)
                    print(f"  {inj['symbol']:>15s} {inj['side']:5s} -> {inj['account']:4s}  age={age:.0f}h  pnl={pnl:+.2f}%  conviction={inj.get('conviction', 0):.2f}  expires_in={max(0, INJECTION_TTL_HOURS - age):.0f}h")
                    continue
            print(f"  {inj['symbol']:>15s} {inj['side']:5s} -> {inj['account']:4s}  age={age:.0f}h  conviction={inj.get('conviction', 0):.2f}  expires_in={max(0, INJECTION_TTL_HOURS - age):.0f}h")
    history = injections.get('history', [])
    recent = [h for h in history if 'pnl_pct' in h][-20:]
    if recent:
        print(f"\nRECENT CLOSED ({len(recent)}):")
        wins = sum(1 for h in recent if h['pnl_pct'] > 0)
        total_pnl = sum(h['pnl_pct'] for h in recent)
        for h in recent[-10:]:
            print(f"  {h['symbol']:>15s} {h['side']:5s} -> {h['account']:4s}  pnl={h['pnl_pct']:+.2f}%  held={h.get('age_hours', 0):.0f}h")
        print(f"  Win rate: {wins}/{len(recent)} ({wins/len(recent)*100:.0f}%)  Total: {total_pnl:+.1f}%")
    if DAILY_PICKS_FILE.exists():
        try:
            with open(DAILY_PICKS_FILE, 'r') as f:
                daily = json.load(f)
            print(f"\nLAST DAILY SCAN: {daily.get('date', '?')} — {daily.get('picks_count', 0)} picks, {daily.get('total_articles', 0)} articles")
        except Exception:
            pass
    print(f"\n{'=' * 70}")

# ============================================================
# AGENT-ASSISTED EXIT MONITOR (stocks intraday)
# ============================================================

EXIT_ADVISORY_INTERVAL = 90
EXIT_ADVISORY_TTL = 600
DAILY_INJECT_HOUR_UTC = 13
DAILY_INJECT_MINUTE_UTC = 5
STOCK_BEAR_EXIT_THRESHOLD = -0.45
STOCK_BULL_EXIT_THRESHOLD = 0.45
_last_daily_inject_date = ""
_last_exit_advisory_time = 0.0
_last_exit_advisory_sig = ""


def _is_tradier_market_open_utc() -> bool:
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 780 <= mins < 1200


async def _load_open_tradier_positions(redis_mgr) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if redis_mgr and hasattr(redis_mgr, 'connections'):
        for name, conn in redis_mgr.connections.items():
            if conn is None:
                continue
            for acct in ('tra', 'trb', 'trc'):
                try:
                    raw = await conn.get(f'tradier:positions:{acct}')
                    if not raw:
                        continue
                    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                    positions = data.get('positions', data) if isinstance(data, dict) else {}
                    if not isinstance(positions, dict):
                        continue
                    for pk, pdata in positions.items():
                        if not isinstance(pdata, dict):
                            continue
                        sym = str(pdata.get('symbol') or pk.split(':')[0] if ':' in pk else pk).upper()
                        side = str(pdata.get('side') or pdata.get('position_side') or '').upper()
                        if not side:
                            qty = float(pdata.get('qty', pdata.get('quantity', 0)) or 0)
                            if qty != 0:
                                side = 'LONG' if qty > 0 else 'SHORT'
                        if sym and side in ('LONG', 'SHORT'):
                            result[sym] = side
                except Exception:
                    continue
            if result:
                break
    if not result:
        for acct in ('tra', 'trb', 'trc'):
            for side_file in ('long_positions.json', 'short_positions.json'):
                p = BASE_PATH / 'data' / 'tradier' / acct / side_file
                alt = BASE_PATH / acct / side_file
                for cand in (p, alt):
                    if not cand.exists():
                        continue
                    try:
                        raw = json.loads(cand.read_text())
                        positions = raw if isinstance(raw, dict) else {}
                        for k, v in positions.items():
                            sym = str(v.get('symbol', k)).upper() if isinstance(v, dict) else str(k).upper()
                            side = 'LONG' if 'long' in side_file else 'SHORT'
                            if sym:
                                result[sym] = side
                    except Exception:
                        pass
    return result


async def publish_exit_advisories(stock_scores: Dict[str, float], crypto_scores: Dict[str, float], redis_mgr):
    global _last_exit_advisory_time, _last_exit_advisory_sig
    now = time.time()
    if (now - _last_exit_advisory_time) < EXIT_ADVISORY_INTERVAL:
        return
    if not _is_tradier_market_open_utc():
        return
    if not stock_scores and not crypto_scores:
        return
    open_positions = await _load_open_tradier_positions(redis_mgr)
    if not open_positions:
        return
    advisories = []
    for sym, side in open_positions.items():
        score = stock_scores.get(sym, crypto_scores.get(sym, 0.0))
        if side == 'LONG' and score <= STOCK_BEAR_EXIT_THRESHOLD:
            advisories.append({'symbol': sym, 'side': side, 'action': 'CONSIDER_EXIT', 'reason': f'bearish_news_score={score:.2f} threshold={STOCK_BEAR_EXIT_THRESHOLD}', 'score': round(score, 3), 'ts': datetime.now(timezone.utc).isoformat()})
        elif side == 'SHORT' and score >= STOCK_BULL_EXIT_THRESHOLD:
            advisories.append({'symbol': sym, 'side': side, 'action': 'CONSIDER_EXIT', 'reason': f'bullish_news_score={score:.2f} threshold={STOCK_BULL_EXIT_THRESHOLD}', 'score': round(score, 3), 'ts': datetime.now(timezone.utc).isoformat()})
    if not advisories:
        return
    sig = json.dumps(sorted((a['symbol'], a['side'], round(a['score'], 2)) for a in advisories), sort_keys=True)
    if sig == _last_exit_advisory_sig:
        return
    _last_exit_advisory_sig = sig
    _last_exit_advisory_time = now
    payload = {'generated_at': datetime.now(timezone.utc).isoformat(), 'count': len(advisories), 'advisories': advisories}
    payload_json = json.dumps(payload)
    advisory_file = DATA_DIR / 'news_exit_advisories.json'
    try:
        with open(advisory_file, 'w') as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logger.debug(f"[EXIT_ADVISORY] file write failed: {e}")
    if redis_mgr and hasattr(redis_mgr, 'connections'):
        for name, conn in redis_mgr.connections.items():
            if conn is None:
                continue
            try:
                await conn.setex('news_exit_advisories', EXIT_ADVISORY_TTL, payload_json)
                await conn.publish('news_exit_advisory', payload_json)
            except Exception as e:
                logger.debug(f"Redis {name} exit advisory publish failed: {e}")
    logger.warning(f"[EXIT_ADVISORY] {len(advisories)} open positions flagged: {[(a['symbol'], a['side'], a['score']) for a in advisories[:8]]}")


async def maybe_daily_inject(session: aiohttp.ClientSession, coin_map: Dict[str, str], stock_set: set, redis_mgr, fear_greed_data, trending_coins: Dict[str, float]):
    global _last_daily_inject_date
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    if _last_daily_inject_date == today:
        return
    if now.hour != DAILY_INJECT_HOUR_UTC or now.minute < DAILY_INJECT_MINUTE_UTC or now.minute >= DAILY_INJECT_MINUTE_UTC + 8:
        return
    if now.weekday() >= 5:
        return
    logger.info(f"[DAILY_INJECT] Triggering scheduled daily injection for {today} at {now.strftime('%H:%M UTC')}")
    try:
        fg, trending, all_articles, world_articles = await fetch_all_sources(session, coin_map, stock_set)
        if fg:
            fear_greed_data = fg
        if trending:
            trending_coins.update(trending)
        macro_events = detect_macro_events(world_articles)
        if macro_events:
            await trigger_extreme_mode_from_news(macro_events, redis_mgr)
        eff_fg = fear_greed_data
        crypto_long, crypto_short, stock_long, stock_short = score_picks(all_articles, trending_coins, coin_map, eff_fg)
        if crypto_long or crypto_short or stock_long or stock_short:
            new_inj = inject_symbols(crypto_long, crypto_short, stock_long, stock_short)
            await boost_sentiment_redis(crypto_long, crypto_short, stock_long, stock_short, redis_mgr)
            logger.info(f"[DAILY_INJECT] Complete: {len(crypto_long)}CL {len(crypto_short)}CS {len(stock_long)}SL {len(stock_short)}SS, {len(new_inj)} injected")
        else:
            logger.info("[DAILY_INJECT] No high-conviction picks met threshold")
        _last_daily_inject_date = today
    except Exception as e:
        logger.error(f"[DAILY_INJECT] Failed: {e}", exc_info=True)


# ============================================================
# DAEMON MODE (systemd service)
# ============================================================

async def main_loop():
    logger.info("=== ez_news_scanner v6 starting (daemon mode: sentiment + re-injection + world news EXTREME_MODE) ===")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_set = set(load_stock_symbols())
    logger.info(f"[SCANNER] Loaded {len(coin_map)} coin mappings, {len(stock_set)} stock tickers")
    redis_mgr = await get_simple_redis_manager()
    rss_interval = 180
    coingecko_interval = 900
    movers_interval = 1800
    alphavantage_interval = 3600
    finnhub_interval = 300
    fg_interval = 1800
    world_news_interval = 120
    stocktwits_interval = 300
    reddit_interval = 300
    tv_ideas_interval = 600
    last = {'rss': 0.0, 'coingecko': 0.0, 'movers': 0.0, 'alphavantage': 0.0, 'finnhub': 0.0, 'fh_stocks': 0.0, 'fg': 0.0, 'reinject': 0.0, 'track_snap': 0.0, 'world_news': 0.0, 'stocktwits': 0.0, 'reddit': 0.0, 'tv_ideas': 0.0}
    fear_greed_data = None
    trending_coins: Dict[str, float] = {}
    async with aiohttp.ClientSession() as session:
        while not shutdown_event.is_set():
            try:
                now = time.time()
                all_articles = []
                if (now - last['coingecko']) >= coingecko_interval:
                    trending_coins = await poll_coingecko_trending(session, coin_map)
                    last['coingecko'] = now
                if (now - last['movers']) >= movers_interval:
                    all_articles.extend(await poll_coingecko_movers(session, coin_map))
                    last['movers'] = now
                if (now - last['fg']) >= fg_interval:
                    fear_greed_data = await poll_fear_greed(session)
                    last['fg'] = now
                if (now - last['rss']) >= rss_interval:
                    all_articles.extend(await poll_rss_feeds(session, CRYPTO_RSS_FEEDS, coin_map, stock_set, is_crypto=True))
                    all_articles.extend(await poll_rss_feeds(session, STOCK_RSS_FEEDS, coin_map, stock_set, is_crypto=False))
                    last['rss'] = now
                if (now - last['alphavantage']) >= alphavantage_interval:
                    all_articles.extend(await poll_alphavantage(session, coin_map))
                    last['alphavantage'] = now
                if (now - last['finnhub']) >= finnhub_interval:
                    all_articles.extend(await poll_finnhub(session, coin_map, stock_set, category='crypto'))
                    all_articles.extend(await poll_finnhub(session, coin_map, stock_set, category='general'))
                    last['finnhub'] = now
                if (now - last['fh_stocks']) >= 900 and stock_set:
                    all_articles.extend(await poll_finnhub_stocks(session, stock_set))
                    last['fh_stocks'] = now
                if (now - last['stocktwits']) >= stocktwits_interval and stock_set:
                    all_articles.extend(await poll_stocktwits(session, stock_set))
                    last['stocktwits'] = now
                if (now - last['reddit']) >= reddit_interval and stock_set:
                    all_articles.extend(await poll_reddit_search(session, stock_set))
                    last['reddit'] = now
                if (now - last['tv_ideas']) >= tv_ideas_interval and stock_set:
                    all_articles.extend(await poll_tradingview_ideas(session, stock_set))
                    last['tv_ideas'] = now
                if (now - last['world_news']) >= world_news_interval:
                    world_articles = await poll_world_news(session)
                    if world_articles:
                        macro_events = detect_macro_events(world_articles)
                        if macro_events:
                            extreme_reason = await trigger_extreme_mode_from_news(macro_events, redis_mgr)
                            if extreme_reason:
                                logger.warning(f"[SCANNER] EXTREME_MODE triggered: {extreme_reason}")
                    await check_extreme_mode_expiry(redis_mgr)
                    last['world_news'] = now
                crypto_scores = {}
                stock_scores = {}
                if all_articles or trending_coins:
                    crypto_scores, stock_scores = aggregate_scores(all_articles, trending_coins, coin_map, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=config.NEWS_SENTIMENT_MIN_ARTICLES, fear_greed=fear_greed_data)
                    await publish_to_redis(crypto_scores, stock_scores, fear_greed_data, redis_mgr)
                    await save_fallback(crypto_scores, stock_scores)
                    pos_c = sum(1 for v in crypto_scores.values() if v > 0.1)
                    neg_c = sum(1 for v in crypto_scores.values() if v < -0.1)
                    fg_str = f"fg={fear_greed_data['value']}" if fear_greed_data else 'fg=?'
                    mode_str = f"mode={Config._CURRENT_MARKET_MODE}"
                    logger.info(f"[SCANNER] {len(all_articles)} articles -> crypto:{len(crypto_scores)} ({pos_c}u{neg_c}d) stocks:{len(stock_scores)} trending:{len(trending_coins)} {fg_str} {mode_str}")
                await maybe_daily_inject(session, coin_map, stock_set, redis_mgr, fear_greed_data, trending_coins)
                if stock_scores or crypto_scores:
                    await publish_exit_advisories(stock_scores, crypto_scores, redis_mgr)
                if (now - last['reinject']) >= RE_INJECT_INTERVAL:
                    re_inject_active()
                    cleanup_expired()
                    last['reinject'] = now
                if (now - last['track_snap']) >= 1800:
                    await update_price_snapshots(session)
                    last['track_snap'] = now
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"[SCANNER] Main loop error: {e}", exc_info=True)
                await asyncio.sleep(60)

# ============================================================
# TEST MODE
# ============================================================

async def test_mode():
    logger.info("=== TEST MODE v6 ===")
    symbols = load_symbols()
    coin_map = build_coin_map(symbols)
    stock_set = set(load_stock_symbols())
    async with aiohttp.ClientSession() as session:
        fg, trending, all_articles, world_articles = await fetch_all_sources(session, coin_map, stock_set)
        crypto_long, crypto_short, stock_long, stock_short = score_picks(all_articles, trending, coin_map, fg)
        crypto_scores, stock_scores = aggregate_scores(all_articles, trending, coin_map, decay_hours=config.NEWS_SENTIMENT_DECAY_HOURS, min_articles=1, fear_greed=fg)
        macro_events = detect_macro_events(world_articles)
    print(f"\n{'=' * 70}")
    print(f"NEWS SCANNER v6 TEST — {len(all_articles)} articles, {len(crypto_scores)} crypto, {len(stock_scores)} stock scores, {len(world_articles)} world news")
    if fg:
        print(f"Fear & Greed: {fg['classification']} ({fg['value']}/100)")
    print(f"CoinGecko Trending: {list(trending.keys())[:7]}")
    if macro_events:
        print(f"\nMACRO EVENTS DETECTED ({len(macro_events)}):")
        for ev in macro_events[:8]:
            trigger_mark = " ** WOULD TRIGGER EXTREME **" if ev['score'] >= NEWS_EXTREME_THRESHOLD and ev['n_sources'] >= NEWS_EXTREME_MIN_SOURCES else ""
            print(f"  {ev['category']:15s}  score={ev['score']:.2f}  sources={ev['n_sources']}  articles={ev['n_articles']}{trigger_mark}")
            print(f"                   [{', '.join(ev['sources'][:4])}]")
            print(f"                   {ev['best_title'][:90]}")
    else:
        print(f"\nMACRO EVENTS: (none detected)")
    print(f"{'=' * 70}")
    print(f"\nTOP CRYPTO PICKS (LONG, conviction >= {MIN_CONVICTION}):")
    for p in crypto_long:
        print(f"  {p['symbol']:>15s}  conv={p['conviction']:.2f}  avg={p['avg_score']:+.3f}  src={p['n_sources']}  [{', '.join(p['sources'][:3])}]")
    if not crypto_long:
        print("  (none)")
    print(f"\nTOP CRYPTO PICKS (SHORT, conviction >= {MIN_CONVICTION}):")
    for p in crypto_short:
        print(f"  {p['symbol']:>15s}  conv={p['conviction']:.2f}  avg={p['avg_score']:+.3f}  src={p['n_sources']}  [{', '.join(p['sources'][:3])}]")
    if not crypto_short:
        print("  (none)")
    print(f"\nTOP STOCK PICKS (LONG):")
    for p in stock_long:
        print(f"  {p['symbol']:>10s}  conv={p['conviction']:.2f}  avg={p['avg_score']:+.3f}  src={p['n_sources']}  [{', '.join(p['sources'][:3])}]")
    if not stock_long:
        print("  (none)")
    print(f"\nTOP STOCK PICKS (SHORT):")
    for p in stock_short:
        print(f"  {p['symbol']:>10s}  conv={p['conviction']:.2f}  avg={p['avg_score']:+.3f}  src={p['n_sources']}  [{', '.join(p['sources'][:3])}]")
    if not stock_short:
        print("  (none)")
    if crypto_scores:
        sorted_c = sorted(crypto_scores.items(), key=lambda x: x[1], reverse=True)
        print(f"\nAll Crypto Sentiment (top 15 / bottom 10):")
        for sym, v in sorted_c[:15]:
            print(f"  {sym:>15s}: {v:+.3f}")
        print(f"  ...")
        for sym, v in sorted_c[-10:]:
            print(f"  {sym:>15s}: {v:+.3f}")

# ============================================================
# CLI ENTRY POINT
# ============================================================

def handle_shutdown(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    shutdown_event.set()

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    if '--daily' in sys.argv:
        asyncio.run(daily_scan())
    elif '--cleanup' in sys.argv:
        removed = cleanup_expired()
        print(f"Cleaned up {len(removed)} expired injections")
        for r in removed:
            print(f"  - {r['symbol']} from {Path(r['file']).name} ({r['account']})")
    elif '--report' in sys.argv:
        asyncio.run(report_mode())
    elif '--test' in sys.argv:
        asyncio.run(test_mode())
    else:
        asyncio.run(main_loop())
