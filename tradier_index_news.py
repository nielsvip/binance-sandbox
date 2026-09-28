# pylint: disable=W,C,R,I
"""tradier_index_news v1 — SPY/QQQ/VT news-driven premarket + opening-range strategy.
Research-grounded rules (2026-09-28 deep dive):
  - 30m opening-range long break: 58% winners on QQQ over 1301 trades / 2yr, positive expectancy all configs.
  - ~45% of 1-2% QQQ overnight gaps fill same day; Monday gap-ups historically fade -> no Monday gap chase.
  - 200-day SMA regime filter: roughly halves max drawdown vs buy-and-hold at similar CAGR -> longs only above, shorts only below.
News pipeline reuses ez_news_scanner (fetch_all_sources, detect_macro_events, vader) — macro risk-off events,
world-news sentiment breadth, fear&greed, and data/market_mode.json EXTREME override form a [-1,+1] daily bias.
Premarket (07:00-09:25 ET): long-only limit entries with duration='pre' (Tradier forbids premarket shorts/markets).
RTH: opening-range breakout in bias direction with structural stop at the opposite range edge.
Risk discipline (there is NO never-lose strategy — this is the honest approximation):
  capped structural stop per trade, breakeven trail at +0.5R, 2-consecutive-loss symbol block,
  daily realized-loss cap halting new entries, EOD flat, master kill switch + KILL file.
SAFETY: real-money orders require INDEX_NEWS_LIVE_TRADING_ENABLED=True; default account trc = Tradier SANDBOX.
Run: python tradier_index_news.py  (daemon) | --report (print day summary) | --scan (one bias scan, no trading)
"""
import asyncio
import json
import logging
import math
import os
import signal
import sys
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import aiohttp
from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from utils import setup_logger

config = TradierConfig()
BASE_PATH = Path(os.getenv('EZ_BASE_PATH', str(getattr(config, 'BASE_PATH', Path(__file__).parent))))
DATA_DIR = BASE_PATH / 'data' / 'index_news'
DATA_DIR.mkdir(parents=True, exist_ok=True)
logger = setup_logger('tradier_index_news', str(BASE_PATH / 'logs' / 'tradier_index_news.log'), logging.INFO) if (BASE_PATH / 'logs').exists() else logging.getLogger('tradier_index_news')
ET = ZoneInfo('America/New_York')
STATE_FILE = DATA_DIR / 'state.json'
TRADES_FILE = DATA_DIR / 'trades.jsonl'
BIAS_FILE = DATA_DIR / 'bias.json'
KILL_FILE = DATA_DIR / 'KILL'
MARKET_MODE_FILE = BASE_PATH / 'data' / 'market_mode.json'
shutdown_event = asyncio.Event()

def now_et() -> datetime:
    return datetime.now(ET)

def et_time() -> dt_time:
    return now_et().time()

def is_weekday() -> bool:
    return now_et().weekday() < 5

def parse_hhmm(s: str) -> dt_time:
    h, m = s.split(':')
    return dt_time(int(h), int(m))

def phase() -> str:
    if not is_weekday():
        return 'CLOSED'
    t = et_time()
    if parse_hhmm(config.INDEX_NEWS_PREMARKET_START_ET) <= t < dt_time(9, 25):
        return 'PREMARKET'
    if dt_time(9, 25) <= t < dt_time(9, 30):
        return 'PRE_FREEZE'
    if dt_time(9, 30) <= t < dt_time(15, 50):
        return 'RTH'
    if dt_time(15, 50) <= t < dt_time(16, 0):
        return 'EOD_FLAT'
    return 'CLOSED'

def load_state() -> Dict:
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
    except Exception:
        st = {}
    today = now_et().strftime('%Y-%m-%d')
    if st.get('date') != today:
        carry_options = st.get('options', {})
        st = {'date': today, 'positions': {}, 'realized_pnl_usd': 0.0, 'consec_losses': {}, 'blocked_symbols': [], 'premarket_entered': [], 'orb_entered': [], 'halted': False, 'options': carry_options, 'opt_triggered': []}
    st.setdefault('options', {})
    st.setdefault('opt_triggered', [])
    return st

def save_state(st: Dict):
    tmp = STATE_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATE_FILE)

def record_trade(rec: Dict):
    rec['ts'] = datetime.now(timezone.utc).isoformat()
    with open(TRADES_FILE, 'a') as f:
        f.write(json.dumps(rec) + '\n')

class NewsBias:
    def __init__(self):
        self.bias = 0.0
        self.detail = {}
        self.updated = 0.0

    async def scan(self):
        try:
            import ez_news_scanner as ns
            coin_map = ns.build_coin_map(ns.load_symbols())
            stock_set = set(ns.load_stock_symbols())
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                fear_greed, _trending, all_articles, world_articles = await ns.fetch_all_sources(session, coin_map, stock_set)
            macro_events = ns.detect_macro_events(all_articles)
            risk_off = max((e['score'] for e in macro_events), default=0.0)
            now = datetime.now(timezone.utc)
            sents = []
            for a in world_articles or []:
                pub = a.get('published', '')
                try:
                    age_h = (now - datetime.fromisoformat(pub.replace('Z', '+00:00'))).total_seconds() / 3600 if pub else 24.0
                except Exception:
                    age_h = 24.0
                if age_h <= 6.0:
                    sents.append(ns.vader_score(a.get('text', a.get('title', ''))))
            sentiment = sum(sents) / len(sents) if sents else 0.0
            fg_term = 0.0
            try:
                fg_term = (float(fear_greed.get('value', 50)) - 50.0) / 200.0 if fear_greed else 0.0
            except Exception:
                fg_term = 0.0
            bias = max(-1.0, min(1.0, sentiment * 1.5 + fg_term - risk_off * 0.8))
            extreme = False
            try:
                with open(MARKET_MODE_FILE) as f:
                    mode = json.load(f)
                extreme = mode.get('mode') == 'EXTREME_MODE' and mode.get('news_trigger', False)
            except Exception:
                extreme = False
            if extreme:
                bias = min(bias, -0.5)
            self.bias = bias
            self.detail = {'bias': round(bias, 3), 'sentiment': round(sentiment, 3), 'risk_off': round(risk_off, 3), 'fg_term': round(fg_term, 3), 'extreme': extreme, 'n_world_articles': len(sents), 'macro_events': [{'category': e['category'], 'score': e['score'], 'n_sources': e['n_sources']} for e in macro_events[:5]], 'updated': datetime.now(timezone.utc).isoformat()}
            self.updated = time.time()
            with open(BIAS_FILE, 'w') as f:
                json.dump(self.detail, f, indent=2)
            logger.info(f"[BIAS] {self.detail}")
        except Exception as e:
            logger.error(f"[BIAS] scan failed: {e}")

    def symbol_bias(self, symbol: str) -> float:
        if symbol == 'VT':
            return self.bias * 0.7
        return self.bias

class MarketData:
    def __init__(self, client: TradierAPIClient):
        self.client = client
        self.daily: Dict[str, List[Dict]] = {}

    async def refresh_daily(self, symbols: List[str]):
        end = now_et().strftime('%Y-%m-%d')
        start = (now_et() - __import__('datetime').timedelta(days=400)).strftime('%Y-%m-%d')
        for symbol in symbols:
            try:
                bars = await self.client.get_history(symbol, start=start, end=end, interval='daily')
                if bars:
                    self.daily[symbol] = bars
            except Exception as e:
                logger.error(f"[DATA] daily history {symbol}: {e}")

    def sma200(self, symbol: str) -> Optional[float]:
        bars = self.daily.get(symbol) or []
        closes = [float(b['close']) for b in bars if b.get('close') is not None][-200:]
        if len(closes) < 150:
            return None
        return sum(closes) / len(closes)

    def prev_close(self, symbol: str) -> Optional[float]:
        bars = self.daily.get(symbol) or []
        today = now_et().strftime('%Y-%m-%d')
        past = [b for b in bars if b.get('date') != today and b.get('close') is not None]
        return float(past[-1]['close']) if past else None

    def regime_long(self, symbol: str) -> bool:
        sma = self.sma200(symbol)
        pc = self.prev_close(symbol)
        return bool(sma and pc and pc > sma)

    async def quote(self, symbol: str) -> Dict:
        try:
            return await self.client.get_quote(symbol) or {}
        except Exception:
            return {}

    async def five_min_closes(self, symbol: str, n: int = 3) -> List[float]:
        try:
            start = now_et().strftime('%Y-%m-%d 09:30')
            end = now_et().strftime('%Y-%m-%d %H:%M')
            bars = await self.client.get_timesales(symbol, interval='5min', start=start, end=end)
            return [float(b['close']) for b in (bars or []) if b.get('close') is not None][-n:]
        except Exception:
            return []

    async def day_bars(self, symbol: str) -> List[Dict]:
        try:
            start = now_et().strftime('%Y-%m-%d 09:30')
            end = now_et().strftime('%Y-%m-%d %H:%M')
            bars = await self.client.get_timesales(symbol, interval='5min', start=start, end=end)
            return [b for b in (bars or []) if b.get('close') is not None]
        except Exception:
            return []

    async def opening_range(self, symbol: str, minutes: int = None) -> Optional[Dict]:
        minutes = minutes or config.INDEX_NEWS_ORB_MINUTES
        try:
            start = now_et().strftime('%Y-%m-%d 09:30')
            open_dt = now_et().replace(hour=9, minute=30)
            end_dt = open_dt + __import__('datetime').timedelta(minutes=minutes)
            bars = await self.client.get_timesales(symbol, interval='1min', start=start, end=end_dt.strftime('%Y-%m-%d %H:%M'))
            highs = [float(b['high']) for b in (bars or []) if b.get('high') is not None]
            lows = [float(b['low']) for b in (bars or []) if b.get('low') is not None]
            opens = [float(b['open']) for b in (bars or []) if b.get('open') is not None]
            if len(highs) < minutes * 0.6:
                return None
            return {'high': max(highs), 'low': min(lows), 'open': opens[0] if opens else None}
        except Exception as e:
            logger.error(f"[DATA] opening range {symbol}: {e}")
            return None

class Executor:
    """All orders for this strategy pass through here. Guards: kill switch, sandbox lock,
    duplicate open-order check, opposing-position check, size cap, premarket limit-only."""

    def __init__(self, client: TradierAPIClient, account_key: str):
        self.client = client
        self.account_key = account_key
        self.sim_mode = False

    def _guard_account(self) -> bool:
        if not config.INDEX_NEWS_LIVE_TRADING_ENABLED and self.account_key != 'trc':
            logger.critical(f"[GUARD] account '{self.account_key}' refused: INDEX_NEWS_LIVE_TRADING_ENABLED=False permits trc sandbox only")
            return False
        return True

    async def _dedup_ok(self, symbol: str, side: str) -> bool:
        try:
            orders = await self.client.get_orders(self.account_key)
            for o in orders or []:
                if str(o.get('symbol', '')).upper() == symbol and str(o.get('status', '')).lower() in ('open', 'pending', 'submitted', 'partially_filled'):
                    logger.warning(f"[GUARD] {symbol}: open {o.get('side')} order id={o.get('id')} exists — refusing duplicate")
                    return False
        except Exception as e:
            logger.error(f"[GUARD] order check failed {symbol}: {e} — fail-closed")
            return False
        return True

    async def place(self, symbol: str, side: str, qty: int, price: float, premarket: bool, reason: str) -> Optional[Dict]:
        if KILL_FILE.exists() or not config.INDEX_NEWS_ENABLED:
            logger.critical(f"[GUARD] kill switch active — refusing {side} {qty} {symbol}")
            return None
        if not self._guard_account():
            return None
        if premarket and side not in ('buy', 'sell'):
            logger.warning(f"[GUARD] premarket {side} not allowed (longs only premarket)")
            return None
        if qty < 1 or qty * price > config.INDEX_NEWS_MAX_POSITION_USD * 1.05:
            logger.warning(f"[GUARD] size violation {symbol} qty={qty} @ {price}")
            return None
        if not await self._dedup_ok(symbol, side):
            return None
        duration = 'pre' if premarket else 'day'
        try:
            res = await self.client.place_order(self.account_key, symbol, side, qty, order_type='limit', price=price, duration=duration)
            order = (res or {}).get('order', {})
            order_id = order.get('id')
            if not order_id:
                logger.error(f"[EXEC] {symbol} {side} rejected: {res}")
                return self._sim_fill(symbol, side, qty, price, reason) if self.sim_mode else None
            for _ in range(8):
                await asyncio.sleep(2.5)
                status = await self.client.get_order_status(self.account_key, order_id)
                if str(status.get('status', '')).lower() == 'filled':
                    fill_price = float(status.get('avg_fill_price') or price)
                    logger.info(f"[EXEC] FILLED {side} {qty} {symbol} @ {fill_price} ({reason})")
                    return {'symbol': symbol, 'side': side, 'qty': qty, 'fill_price': fill_price, 'order_id': order_id, 'mode': 'sandbox' if self.account_key == 'trc' else 'live', 'reason': reason}
            await self.client.cancel_order(self.account_key, order_id)
            logger.warning(f"[EXEC] {symbol} {side} limit not filled in 20s — cancelled ({reason})")
            return None
        except Exception as e:
            logger.error(f"[EXEC] {symbol} {side} error: {e}")
            return self._sim_fill(symbol, side, qty, price, reason) if self.sim_mode else None

    def _sim_fill(self, symbol: str, side: str, qty: int, price: float, reason: str) -> Dict:
        logger.info(f"[SIM] simulated fill {side} {qty} {symbol} @ {price} ({reason})")
        return {'symbol': symbol, 'side': side, 'qty': qty, 'fill_price': price, 'order_id': f"sim_{int(time.time())}", 'mode': 'sim', 'reason': reason}

class OptionsPaper:
    """PAPER-ONLY directional options layer. Honors the repo-wide OPTIONS_LIVE_TRADING_ENABLED
    hard lock by never calling place_option_order — fills are hypothetical at mid + 25% half-spread,
    marked to live mid each cycle. Triggers: news bias threshold, technical top/bottom rejection."""

    def __init__(self, client: TradierAPIClient, data: MarketData, state: Dict):
        self.client = client
        self.data = data
        self.state = state

    async def pick_contract(self, symbol: str, opt_type: str) -> Optional[Dict]:
        try:
            exps = await self.client.get_option_expirations(symbol)
        except Exception as e:
            logger.error(f"[OPT] expirations {symbol}: {e}")
            return None
        today = now_et().date()
        scored_exps = []
        for e in exps or []:
            try:
                dte = (datetime.strptime(e, '%Y-%m-%d').date() - today).days
            except Exception:
                continue
            if config.INDEX_NEWS_OPTIONS_DTE_MIN <= dte <= config.INDEX_NEWS_OPTIONS_DTE_MAX:
                scored_exps.append((abs(dte - 14), e, dte))
        scored_exps.sort()
        best = None
        for _, exp, dte in scored_exps[:4]:
            try:
                chain = await self.client.get_option_chain(symbol, exp, greeks=True)
            except Exception as e:
                logger.error(f"[OPT] chain {symbol} {exp}: {e}")
                continue
            min_oi = config.INDEX_NEWS_OPTIONS_MIN_OI // 4 if symbol == 'VT' else config.INDEX_NEWS_OPTIONS_MIN_OI
            for c in chain or []:
                if str(c.get('option_type', '')).lower() != opt_type:
                    continue
                bid, ask = float(c.get('bid') or 0), float(c.get('ask') or 0)
                if bid <= 0 or ask <= bid:
                    continue
                mid = (bid + ask) / 2
                spread_pct = (ask - bid) / mid * 100.0
                oi = int(c.get('open_interest') or 0)
                delta = abs(float((c.get('greeks') or {}).get('delta') or 0))
                if spread_pct > config.INDEX_NEWS_OPTIONS_MAX_SPREAD_PCT or oi < min_oi:
                    continue
                if not 0.30 <= delta <= 0.60 or mid * 100 > config.INDEX_NEWS_OPTIONS_MAX_PREMIUM_USD:
                    continue
                score = spread_pct * 2.0 + abs(delta - config.INDEX_NEWS_OPTIONS_TARGET_DELTA) * 20.0
                cand = {'occ': c.get('symbol'), 'type': opt_type, 'strike': float(c.get('strike') or 0), 'expiry': exp, 'dte': dte, 'bid': bid, 'ask': ask, 'mid': round(mid, 4), 'delta': round(delta, 3), 'oi': oi, 'spread_pct': round(spread_pct, 2), 'score': round(score, 3)}
                if best is None or cand['score'] < best['score']:
                    best = cand
        return best

    async def detect_tech_extreme(self, symbol: str) -> Optional[str]:
        bars = await self.data.day_bars(symbol)
        if len(bars) < 12:
            return None
        highs = [float(b['high']) for b in bars]
        lows = [float(b['low']) for b in bars]
        last_close = float(bars[-1]['close'])
        day_high, day_low = max(highs), min(lows)
        hi_idx, lo_idx = highs.index(day_high), lows.index(day_low)
        rev = config.INDEX_NEWS_OPTIONS_REVERSAL_PCT
        if hi_idx < len(bars) - 1 and len(bars) - 1 - hi_idx <= 12 and (day_high - last_close) / day_high * 100 >= rev:
            return 'top'
        if lo_idx < len(bars) - 1 and len(bars) - 1 - lo_idx <= 12 and (last_close - day_low) / day_low * 100 >= rev:
            return 'bottom'
        return None

    async def open_option(self, symbol: str, opt_type: str, trigger: str, bias: float):
        contract = await self.pick_contract(symbol, opt_type)
        if not contract:
            logger.info(f"[OPT] {symbol}: no {opt_type} passed best-price filters (spread/OI/delta/premium) for {trigger}")
            return
        fill = round(contract['mid'] + 0.25 * (contract['ask'] - contract['mid']), 4)
        rec = {'symbol': symbol, 'occ': contract['occ'], 'type': opt_type, 'strike': contract['strike'], 'expiry': contract['expiry'], 'entry_mid': fill, 'entry_premium_usd': round(fill * 100, 2), 'delta': contract['delta'], 'oi': contract['oi'], 'spread_pct': contract['spread_pct'], 'trigger': trigger, 'bias': round(bias, 3), 'opened_at': datetime.now(timezone.utc).isoformat(), 'opened_date': now_et().strftime('%Y-%m-%d'), 'mark': fill, 'mark_ts': datetime.now(timezone.utc).isoformat(), 'mode': 'paper_mid'}
        self.state['options'][contract['occ']] = rec
        save_state(self.state)
        record_trade({'event': 'OPTION_OPEN', **{k: rec[k] for k in ('symbol', 'occ', 'type', 'strike', 'expiry', 'entry_mid', 'entry_premium_usd', 'delta', 'spread_pct', 'trigger', 'bias', 'mode')}})
        logger.warning(f"[OPT] PAPER OPEN {symbol} {opt_type} {contract['strike']} {contract['expiry']} @ {fill} (${fill * 100:.0f}) trigger={trigger} bias={bias:+.2f} spread={contract['spread_pct']}% oi={contract['oi']}")

    async def scan_triggers(self, symbols: List[str], bias_fn):
        if not config.INDEX_NEWS_OPTIONS_ENABLED:
            return
        for symbol in symbols:
            if any(o['symbol'] == symbol for o in self.state['options'].values()):
                continue
            bias = bias_fn(symbol)
            fired = None
            if bias >= config.INDEX_NEWS_OPTIONS_BIAS_MIN:
                fired = ('call', 'news_bullish')
            elif bias <= -config.INDEX_NEWS_OPTIONS_BIAS_MIN:
                fired = ('put', 'news_bearish')
            else:
                extreme = await self.detect_tech_extreme(symbol)
                if extreme == 'top' and bias <= 0.1:
                    fired = ('put', 'tech_top')
                elif extreme == 'bottom' and bias >= -0.1:
                    fired = ('call', 'tech_bottom')
            if not fired:
                continue
            key = f"{now_et().strftime('%Y-%m-%d')}_{symbol}_{fired[1]}"
            if key in self.state['opt_triggered']:
                continue
            self.state['opt_triggered'].append(key)
            save_state(self.state)
            await self.open_option(symbol, fired[0], fired[1], bias)

    async def manage(self):
        for occ in list(self.state['options'].keys()):
            pos = self.state['options'][occ]
            try:
                q = await self.client.get_quote(occ)
            except Exception:
                q = {}
            bid, ask = float(q.get('bid') or 0), float(q.get('ask') or 0)
            if bid <= 0 or ask <= 0:
                continue
            mid = (bid + ask) / 2
            pos['mark'] = round(mid, 4)
            pos['mark_ts'] = datetime.now(timezone.utc).isoformat()
            gain_pct = (mid / pos['entry_mid'] - 1) * 100.0
            held_days = (now_et().date() - datetime.strptime(pos['opened_date'], '%Y-%m-%d').date()).days
            dte_left = (datetime.strptime(pos['expiry'], '%Y-%m-%d').date() - now_et().date()).days
            reason = None
            if gain_pct >= config.INDEX_NEWS_OPTIONS_TP_PCT:
                reason = 'TP'
            elif gain_pct <= -config.INDEX_NEWS_OPTIONS_SL_PCT:
                reason = 'SL'
            elif held_days >= config.INDEX_NEWS_OPTIONS_MAX_HOLD_SESSIONS + 2:
                reason = 'MAX_HOLD'
            elif dte_left <= 3:
                reason = 'DTE_EXIT'
            if reason:
                exit_px = round(mid - 0.25 * (mid - bid), 4)
                exit_gain = (exit_px / pos['entry_mid'] - 1) * 100.0
                pnl = round((exit_px - pos['entry_mid']) * 100, 2)
                self.state['realized_pnl_usd'] = self.state.get('realized_pnl_usd', 0.0) + pnl
                record_trade({'event': 'OPTION_CLOSE', 'symbol': pos['symbol'], 'occ': occ, 'type': pos['type'], 'strike': pos['strike'], 'expiry': pos['expiry'], 'entry_mid': pos['entry_mid'], 'exit_mid': exit_px, 'gain_pct': round(exit_gain, 2), 'pnl_usd': pnl, 'trigger': pos['trigger'], 'reason': reason, 'mode': 'paper_mid'})
                logger.warning(f"[OPT] PAPER CLOSE {pos['symbol']} {pos['type']} {pos['strike']} @ {exit_px} {exit_gain:+.1f}% (${pnl:+.2f}) reason={reason}")
                del self.state['options'][occ]
            save_state(self.state)

class Strategy:
    def __init__(self):
        self.account_key = config.INDEX_NEWS_ACCOUNT_KEY
        self.client = TradierAPIClient(config, account_key=self.account_key)
        self.data = MarketData(self.client)
        self.news = NewsBias()
        self.executor = Executor(self.client, self.account_key)
        if self.account_key == 'trc' and not getattr(self.client, '_current_key', None):
            self.executor.sim_mode = True
            logger.warning("[START] no trc sandbox credentials — pure-simulation fills (mode='sim' in records)")
        self.symbols = list(config.INDEX_NEWS_SYMBOLS)
        self.state = load_state()
        self.options = OptionsPaper(self.client, self.data, self.state)

    def param(self, symbol: str, key: str):
        base = {'orb': config.INDEX_NEWS_ORB_MINUTES, 'be_r': config.INDEX_NEWS_BREAKEVEN_AT_R, 'gap_max': config.INDEX_NEWS_GAP_CHASE_MAX_PCT, 'rng_cap': 1.0, 'gap_min': getattr(config, 'INDEX_NEWS_GAP_MIN_PCT', 0.0)}
        per = getattr(config, 'INDEX_NEWS_SYMBOL_PARAMS', {}) or {}
        return per.get(symbol, {}).get(key, base[key])

    def allocation_usd(self) -> float:
        return config.INDEX_NEWS_MAX_POSITION_USD * len(self.symbols)

    def entries_halted(self) -> bool:
        if self.state.get('halted'):
            return True
        cap = self.allocation_usd() * config.INDEX_NEWS_DAILY_MAX_LOSS_PCT / 100.0
        if self.state.get('realized_pnl_usd', 0.0) <= -cap:
            logger.critical(f"[RISK] daily loss cap hit ({self.state['realized_pnl_usd']:.2f} <= -{cap:.2f}) — NO new entries today")
            self.state['halted'] = True
            save_state(self.state)
            return True
        return False

    def symbol_blocked(self, symbol: str) -> bool:
        return symbol in self.state.get('blocked_symbols', [])

    def size_for(self, symbol: str, price: float) -> int:
        usd = config.INDEX_NEWS_MAX_POSITION_USD
        if symbol == 'VT':
            usd *= config.INDEX_NEWS_VT_SIZE_FACTOR
        return max(0, math.floor(usd / price))

    async def premarket_pass(self):
        if not config.INDEX_NEWS_PREMARKET_ENABLED or self.entries_halted():
            return
        for symbol in self.symbols:
            if symbol in self.state['premarket_entered'] or symbol in self.state['positions'] or self.symbol_blocked(symbol):
                continue
            bias = self.news.symbol_bias(symbol)
            min_bias = config.INDEX_NEWS_BIAS_LONG_MIN if symbol != 'VT' else 0.5
            if bias < min_bias:
                continue
            if not self.data.regime_long(symbol):
                logger.info(f"[PRE] {symbol}: below 200SMA regime — no premarket long")
                continue
            q = await self.data.quote(symbol)
            bid, ask = float(q.get('bid') or 0), float(q.get('ask') or 0)
            pc = self.data.prev_close(symbol)
            if not (bid > 0 and ask > 0 and pc):
                logger.info(f"[PRE] {symbol}: no premarket book — skip")
                continue
            if (ask - bid) / ask > 0.002:
                logger.info(f"[PRE] {symbol}: spread too wide ({bid}/{ask}) — skip")
                continue
            mid = (bid + ask) / 2
            gap_pct = (mid - pc) / pc * 100.0
            if gap_pct > self.param(symbol, 'gap_max') or gap_pct < -0.3:
                logger.info(f"[PRE] {symbol}: gap {gap_pct:+.2f}% outside chase band — skip")
                continue
            if abs(gap_pct) < self.param(symbol, 'gap_min'):
                logger.info(f"[PRE] {symbol}: gap {gap_pct:+.2f}% below catalyst floor {self.param(symbol, 'gap_min')}% — skip (sweep-confirmed churn filter)")
                continue
            if config.INDEX_NEWS_MONDAY_GAP_FADE and now_et().weekday() == 0 and gap_pct > 0.3:
                logger.info(f"[PRE] {symbol}: Monday gap-up {gap_pct:+.2f}% — fade stat, skip")
                continue
            qty = self.size_for(symbol, ask)
            if qty < 1:
                continue
            fill = await self.executor.place(symbol, 'buy', qty, round(min(ask, mid + 0.01), 2), premarket=True, reason=f"PRE_NEWS_LONG bias={bias:.2f} gap={gap_pct:+.2f}%")
            if fill:
                stop = round(fill['fill_price'] * 0.994, 2)
                self.state['positions'][symbol] = {'side': 'LONG', 'qty': fill['qty'], 'entry': fill['fill_price'], 'stop': stop, 'risk': fill['fill_price'] - stop, 'be_r': self.param(symbol, 'be_r'), 'opened_at': datetime.now(timezone.utc).isoformat(), 'breakeven': False, 'mode': fill['mode'], 'entry_reason': fill['reason']}
                self.state['premarket_entered'].append(symbol)
                save_state(self.state)
                record_trade({'event': 'OPEN', 'symbol': symbol, 'side': 'LONG', 'qty': fill['qty'], 'price': fill['fill_price'], 'mode': fill['mode'], 'reason': fill['reason']})

    async def orb_pass(self):
        if self.entries_halted():
            return
        open_dt = now_et().replace(hour=9, minute=30)
        mins_since_open = (now_et() - open_dt).total_seconds() / 60
        if mins_since_open > 120:
            return
        for symbol in self.symbols:
            orb_minutes = int(self.param(symbol, 'orb'))
            if mins_since_open < orb_minutes + 5:
                continue
            if symbol in self.state['orb_entered'] or symbol in self.state['positions'] or self.symbol_blocked(symbol):
                continue
            orange = await self.data.opening_range(symbol, minutes=orb_minutes)
            if not orange:
                continue
            pc = self.data.prev_close(symbol)
            if pc and orange.get('open'):
                day_gap = (orange['open'] - pc) / pc * 100.0
                if abs(day_gap) < self.param(symbol, 'gap_min'):
                    logger.info(f"[ORB] {symbol}: day gap {day_gap:+.2f}% below catalyst floor — skip day (sweep-confirmed churn filter)")
                    self.state['orb_entered'].append(symbol)
                    save_state(self.state)
                    continue
            rng_pct = (orange['high'] - orange['low']) / orange['low'] * 100.0
            rng_cap = self.param(symbol, 'rng_cap')
            if rng_pct > rng_cap:
                logger.info(f"[ORB] {symbol}: range {rng_pct:.2f}% > {rng_cap}% risk cap — skip")
                continue
            closes = await self.data.five_min_closes(symbol, n=2)
            if not closes:
                continue
            last_close = closes[-1]
            bias = self.news.symbol_bias(symbol)
            regime_long = self.data.regime_long(symbol)
            q = await self.data.quote(symbol)
            ask, bid = float(q.get('ask') or 0), float(q.get('bid') or 0)
            if bias >= config.INDEX_NEWS_BIAS_LONG_MIN and regime_long and last_close > orange['high'] and ask > 0:
                qty = self.size_for(symbol, ask)
                fill = await self.executor.place(symbol, 'buy', qty, round(ask, 2), premarket=False, reason=f"ORB_LONG bias={bias:.2f} or_high={orange['high']}") if qty >= 1 else None
                if fill:
                    self.state['positions'][symbol] = {'side': 'LONG', 'qty': fill['qty'], 'entry': fill['fill_price'], 'stop': orange['low'], 'risk': fill['fill_price'] - orange['low'], 'be_r': self.param(symbol, 'be_r'), 'opened_at': datetime.now(timezone.utc).isoformat(), 'breakeven': False, 'mode': fill['mode'], 'entry_reason': fill['reason']}
                    self.state['orb_entered'].append(symbol)
                    save_state(self.state)
                    record_trade({'event': 'OPEN', 'symbol': symbol, 'side': 'LONG', 'qty': fill['qty'], 'price': fill['fill_price'], 'mode': fill['mode'], 'reason': fill['reason']})
            elif getattr(config, 'INDEX_NEWS_SHORTS_ENABLED', False) and bias <= config.INDEX_NEWS_BIAS_SHORT_MAX and not regime_long and last_close < orange['low'] and bid > 0:
                qty = self.size_for(symbol, bid)
                fill = await self.executor.place(symbol, 'sell_short', qty, round(bid, 2), premarket=False, reason=f"ORB_SHORT bias={bias:.2f} or_low={orange['low']}") if qty >= 1 else None
                if fill:
                    self.state['positions'][symbol] = {'side': 'SHORT', 'qty': fill['qty'], 'entry': fill['fill_price'], 'stop': orange['high'], 'risk': orange['high'] - fill['fill_price'], 'be_r': self.param(symbol, 'be_r'), 'opened_at': datetime.now(timezone.utc).isoformat(), 'breakeven': False, 'mode': fill['mode'], 'entry_reason': fill['reason']}
                    self.state['orb_entered'].append(symbol)
                    save_state(self.state)
                    record_trade({'event': 'OPEN', 'symbol': symbol, 'side': 'SHORT', 'qty': fill['qty'], 'price': fill['fill_price'], 'mode': fill['mode'], 'reason': fill['reason']})

    async def manage_pass(self, force_flat: bool = False):
        for symbol in list(self.state['positions'].keys()):
            pos = self.state['positions'][symbol]
            closes = await self.data.five_min_closes(symbol, n=2)
            q = await self.data.quote(symbol)
            last = float(q.get('last') or 0) or (closes[-1] if closes else 0)
            if not last:
                continue
            is_long = pos['side'] == 'LONG'
            gain = (last - pos['entry']) if is_long else (pos['entry'] - last)
            be_r = float(pos.get('be_r', config.INDEX_NEWS_BREAKEVEN_AT_R))
            if not pos['breakeven'] and pos['risk'] > 0 and gain >= be_r * pos['risk']:
                pos['stop'] = pos['entry']
                pos['breakeven'] = True
                save_state(self.state)
                logger.info(f"[MANAGE] {symbol}: +{be_r}R reached — stop moved to breakeven {pos['entry']}")
            stop_hit = bool(closes) and ((is_long and closes[-1] < pos['stop']) or (not is_long and closes[-1] > pos['stop']))
            if force_flat or stop_hit:
                reason = 'EOD_FLAT' if force_flat else ('OR_STRUCTURE_BREAK' if not pos['breakeven'] else 'BREAKEVEN_STOP')
                close_side = 'sell' if is_long else 'buy_to_cover'
                px = round((float(q.get('bid') or last) if is_long else float(q.get('ask') or last)), 2)
                fill = await self.executor.place(symbol, close_side, pos['qty'], px, premarket=False, reason=reason)
                if fill:
                    pnl = (fill['fill_price'] - pos['entry']) * pos['qty'] * (1 if is_long else -1)
                    gain_pct = (fill['fill_price'] / pos['entry'] - 1) * 100 * (1 if is_long else -1)
                    self.state['realized_pnl_usd'] = self.state.get('realized_pnl_usd', 0.0) + pnl
                    if pnl < 0:
                        self.state['consec_losses'][symbol] = self.state['consec_losses'].get(symbol, 0) + 1
                        if self.state['consec_losses'][symbol] >= config.INDEX_NEWS_MAX_CONSEC_LOSSES:
                            self.state.setdefault('blocked_symbols', []).append(symbol)
                            logger.critical(f"[RISK] {symbol}: {self.state['consec_losses'][symbol]} consecutive losses — blocked rest of day")
                    else:
                        self.state['consec_losses'][symbol] = 0
                    record_trade({'event': 'CLOSE', 'symbol': symbol, 'side': pos['side'], 'qty': pos['qty'], 'entry': pos['entry'], 'exit': fill['fill_price'], 'gain_pct': round(gain_pct, 4), 'pnl_usd': round(pnl, 2), 'mode': fill['mode'], 'reason': reason, 'entry_reason': pos.get('entry_reason', '')})
                    del self.state['positions'][symbol]
                    save_state(self.state)
                    logger.info(f"[CLOSE] {symbol} {pos['side']} {gain_pct:+.2f}% ({pnl:+.2f} USD) reason={reason}")

    async def run(self):
        if not config.INDEX_NEWS_ENABLED:
            logger.critical("[KILL] INDEX_NEWS_ENABLED=False — daemon exiting (flip switch in config_tradier.py to run)")
            return
        await self.client.connect()
        await self.data.refresh_daily(self.symbols)
        last_scan = 0.0
        last_daily = time.time()
        logger.warning(f"[START] symbols={self.symbols} account={self.account_key} live={config.INDEX_NEWS_LIVE_TRADING_ENABLED} paper_sandbox={self.account_key == 'trc'}")
        while not shutdown_event.is_set():
            try:
                if KILL_FILE.exists():
                    logger.critical("[KILL] KILL file present — daemon halting (positions untouched)")
                    break
                ph = phase()
                if self.state.get('date') != now_et().strftime('%Y-%m-%d'):
                    self.state = load_state()
                    self.options.state = self.state
                    save_state(self.state)
                    logger.info(f"[ROLLOVER] new session {self.state['date']} — day counters reset, {len(self.state['options'])} option position(s) carried")
                if ph in ('PREMARKET', 'PRE_FREEZE', 'RTH') and time.time() - last_scan > config.INDEX_NEWS_SCAN_INTERVAL_SECONDS:
                    await self.news.scan()
                    last_scan = time.time()
                if time.time() - last_daily > 6 * 3600:
                    await self.data.refresh_daily(self.symbols)
                    last_daily = time.time()
                if ph == 'PREMARKET':
                    await self.premarket_pass()
                elif ph == 'RTH':
                    await self.orb_pass()
                    await self.manage_pass()
                    if et_time() >= dt_time(10, 0):
                        await self.options.scan_triggers(self.symbols, self.news.symbol_bias)
                    await self.options.manage()
                elif ph == 'EOD_FLAT' and config.INDEX_NEWS_EOD_FLAT:
                    await self.manage_pass(force_flat=True)
                await asyncio.sleep(30 if ph in ('PREMARKET', 'RTH', 'EOD_FLAT') else 300)
            except Exception as e:
                logger.error(f"[LOOP] {e}", exc_info=True)
                await asyncio.sleep(30)
        await self.client.close()

def report():
    trades = []
    try:
        with open(TRADES_FILE) as f:
            trades = [json.loads(line) for line in f if line.strip()]
    except Exception:
        pass
    closes = [t for t in trades if t.get('event') == 'CLOSE']
    if not closes:
        print("No closed trades recorded yet.")
        return
    by_mode: Dict[str, List[Dict]] = {}
    for t in closes:
        by_mode.setdefault(t.get('mode', '?'), []).append(t)
    for mode, rows in by_mode.items():
        wins = [t for t in rows if t['pnl_usd'] > 0]
        total = sum(t['pnl_usd'] for t in rows)
        print(f"[{mode}] trades={len(rows)} wins={len(wins)} win_rate={len(wins)/len(rows)*100:.1f}% total_pnl={total:+.2f} USD avg_gain_trade={sum(t['gain_pct'] for t in rows)/len(rows):+.3f}%")
        print(f"[{mode}] per-trade returns (source of truth): {[round(t['gain_pct'], 3) for t in rows[-30:]]}")

def handle_shutdown(sig, frame):
    logger.info(f"signal {sig} — shutting down")
    shutdown_event.set()

def acquire_singleton() -> Optional[object]:
    import fcntl
    lock_path = DATA_DIR / 'daemon.lock'
    fh = open(lock_path, 'w')
    for attempt in range(8):
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.write(str(os.getpid()))
            fh.flush()
            return fh
        except OSError:
            time.sleep(5)
    logger.warning("[SINGLETON] another tradier_index_news daemon holds the lock after 40s — exiting")
    return None

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    if '--report' in sys.argv:
        report()
    elif '--scan' in sys.argv:
        nb = NewsBias()
        asyncio.run(nb.scan())
        print(json.dumps(nb.detail, indent=2))
    else:
        _lock = acquire_singleton()
        if _lock:
            asyncio.run(Strategy().run())
