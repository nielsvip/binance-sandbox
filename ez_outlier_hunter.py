#!/usr/bin/env python3
"""
EZ OUTLIER HUNTER — Independent 24/7 outlier detection + scalping for fin account.

Monitors ALL Binance USDT futures (400+ symbols) via public ticker API.
Detects symbols making abnormal moves vs the market baseline.
Places orders DIRECTLY on fin account — no dependency on ez_manage's data pipeline.

Design:
- Polls fapi/v1/ticker/24hr every 30s (ALL symbols, no subscription needed)
- Keeps rolling 1m/5m/15m price snapshots to detect sudden moves
- Outlier = symbol moving significantly more than the median
- Enters via Binance futures API on fin account
- Exits via trailing stop + time-based exit
- STRICT_NO_LOSS: never closes at a loss (holds until recovery)

Thresholds (relative to market median):
- 5m window: >2% delta = candidate
- 15m window: >3% delta = strong candidate
- 1h (from 24hr ticker): >5% delta = major outlier

Run: python3 ez_outlier_hunter.py
"""
import asyncio, aiohttp, json, logging, os, platform, signal, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from io import StringIO
from pathlib import Path
from subprocess import run as _run

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE_PATH))
LOG_DIR = Path("/Users/niels/logs") if platform.system() == "Darwin" else Path("/home/niels/logs")
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [OUTLIER] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("outlier_hunter")
fh = logging.FileHandler(str(LOG_DIR / "ez_outlier_hunter.log"), encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [OUTLIER] %(message)s", "%Y-%m-%d %H:%M:%S"))
logger.addHandler(fh)
ACCT = "fin"
STATE_FILE = BASE_PATH / "data" / "outlier_hunter_state.json"
MIN_QTY_FILE = BASE_PATH / "min_qty.json"
FAPI = "https://fapi.binance.com"
POLL_INTERVAL = 30
MAX_POSITIONS = 12
MIN_SIZE_USD = 20.0
MAX_SIZE_USD = 100.0
COOLDOWN_SECS = 900
TRAIL_PCT = 0.5
MAX_HOLD_HOURS = 24
DELTA_5M = 2.0
DELTA_15M = 3.0
DELTA_1H = 5.0
MIN_VOLUME_USD = 5_000_000
SYMBOLS_FILE = BASE_PATH / "symbols.json"
SYMBOLS_FIN_FILE = BASE_PATH / "symbols_fin.json"
TRADEABLE_KEYS_FILE = BASE_PATH / "tradeable_keys.json"
SHUTDOWN = False

_ADD_NEW_SYMBOLS_SCRIPT = BASE_PATH / "add_new_symbols.py"
_INJECTED_SYMS = set()

def _run_add_new_symbols():
    """Run add_new_symbols.py to sync position structures after symbols.json changes."""
    try:
        import subprocess
        r = subprocess.run([sys.executable, str(_ADD_NEW_SYMBOLS_SCRIPT)], capture_output=True, text=True, timeout=30, cwd=str(BASE_PATH))
        if r.returncode == 0:
            logger.info(f"add_new_symbols.py OK: {r.stdout.strip()[-200:]}")
        else:
            logger.warning(f"add_new_symbols.py FAIL: {r.stderr.strip()[-200:]}")
    except Exception as e:
        logger.warning(f"add_new_symbols.py error: {e}")

def inject_symbol_into_tracking(sym):
    """Add symbol to symbols.json, symbols_fin.json, tradeable_keys.json + run add_new_symbols.py."""
    changed = False
    for fpath in (SYMBOLS_FILE, SYMBOLS_FIN_FILE):
        if fpath == SYMBOLS_FILE: logger.warning(f"🚫 [symbols.json write blocked] Skipping inject of {sym}"); continue
        try:
            data = json.loads(fpath.read_text()) if fpath.exists() else []
            if sym not in data:
                data.append(sym)
                data.sort()
                tmp = str(fpath) + ".tmp"
                Path(tmp).write_text(json.dumps(data, indent=2))
                os.replace(tmp, str(fpath))
                changed = True
                logger.info(f"Injected {sym} into {fpath.name} (total={len(data)})")
        except Exception as e:
            logger.warning(f"Failed to inject {sym} into {fpath.name}: {e}")
    try:
        keys = json.loads(TRADEABLE_KEYS_FILE.read_text()) if TRADEABLE_KEYS_FILE.exists() else []
        added = []
        for side in ("LONG", "SHORT"):
            k = f"{ACCT}:{sym}_{side}"
            if k not in keys:
                keys.append(k)
                added.append(k)
        if added:
            keys.sort()
            tmp = str(TRADEABLE_KEYS_FILE) + ".tmp"
            Path(tmp).write_text(json.dumps(keys, indent=2))
            os.replace(tmp, str(TRADEABLE_KEYS_FILE))
            logger.info(f"Injected {len(added)} tradeable_keys for {sym}: {added}")
            changed = True
    except Exception as e:
        logger.warning(f"Failed to inject tradeable_keys for {sym}: {e}")
    if changed:
        _INJECTED_SYMS.add(sym)
        _run_add_new_symbols()
    return changed

def eject_symbol_from_tracking(sym):
    """Remove outlier symbol from symbols.json, symbols_fin.json, tradeable_keys.json when position closed."""
    if sym not in _INJECTED_SYMS:
        return False
    changed = False
    for fpath in (SYMBOLS_FILE, SYMBOLS_FIN_FILE):
        if fpath == SYMBOLS_FILE: logger.warning(f"🚫 [symbols.json write blocked] Skipping eject of {sym}"); continue
        try:
            data = json.loads(fpath.read_text()) if fpath.exists() else []
            if sym in data:
                data.remove(sym)
                tmp = str(fpath) + ".tmp"
                Path(tmp).write_text(json.dumps(data, indent=2))
                os.replace(tmp, str(fpath))
                changed = True
                logger.info(f"Ejected {sym} from {fpath.name} (total={len(data)})")
        except Exception as e:
            logger.warning(f"Failed to eject {sym} from {fpath.name}: {e}")
    try:
        keys = json.loads(TRADEABLE_KEYS_FILE.read_text()) if TRADEABLE_KEYS_FILE.exists() else []
        removed = []
        for side in ("LONG", "SHORT"):
            k = f"{ACCT}:{sym}_{side}"
            if k in keys:
                keys.remove(k)
                removed.append(k)
        if removed:
            tmp = str(TRADEABLE_KEYS_FILE) + ".tmp"
            Path(tmp).write_text(json.dumps(keys, indent=2))
            os.replace(tmp, str(TRADEABLE_KEYS_FILE))
            logger.info(f"Ejected {len(removed)} tradeable_keys for {sym}: {removed}")
            changed = True
    except Exception as e:
        logger.warning(f"Failed to eject tradeable_keys for {sym}: {e}")
    if changed:
        _INJECTED_SYMS.discard(sym)
        _run_add_new_symbols()
    return changed

def _sigterm(*_):
    global SHUTDOWN
    SHUTDOWN = True
    logger.warning("SIGTERM received — shutting down gracefully")
signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)

def load_gpg_env():
    gpg_path = BASE_PATH / ".env.gpg"
    if not gpg_path.exists():
        logger.error(f".env.gpg not found at {gpg_path}")
        return False
    r = _run(f"gpg --batch --yes --decrypt '{gpg_path}'", shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        logger.error(f"GPG decrypt failed: {r.stderr}")
        return False
    from dotenv import dotenv_values
    vals = dotenv_values(stream=StringIO(r.stdout))
    os.environ.update({k: v for k, v in vals.items() if v and v.strip()})
    logger.info(f"Loaded {len(vals)} env vars from GPG")
    return True

def load_state():
    try:
        return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"positions": {}, "cooldowns": {}, "stats": {"entered": 0, "exited": 0, "pnl": 0.0}}
    except Exception:
        return {"positions": {}, "cooldowns": {}, "stats": {"entered": 0, "exited": 0, "pnl": 0.0}}

def save_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(STATE_FILE) + ".tmp"
    Path(tmp).write_text(json.dumps(s, indent=2, default=str))
    os.replace(tmp, STATE_FILE)

def load_min_qty():
    try:
        data = json.loads(MIN_QTY_FILE.read_text())
        return {sym: float(info["minQty"]) for sym, info in data.items()}
    except Exception:
        return {}

def qty_str(qty, step_size=0.001, qty_prec=3):
    if qty_prec == 0:
        return str(int(qty))
    d = Decimal(str(qty))
    fmt = Decimal(10) ** -qty_prec
    return str(d.quantize(fmt, rounding=ROUND_DOWN))

async def fetch_json(session, url, params=None):
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                return await r.json()
            logger.warning(f"API {r.status}: {url}")
            return None
    except Exception as e:
        logger.warning(f"API error: {e}")
        return None

async def get_all_tickers(session):
    data = await fetch_json(session, f"{FAPI}/fapi/v1/ticker/24hr")
    if not data:
        return {}
    out = {}
    for d in data:
        sym = d.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            p = float(d["lastPrice"])
            vol = float(d["quoteVolume"])
            chg = float(d["priceChangePercent"])
            if p <= 0 or vol < MIN_VOLUME_USD:
                continue
            out[sym] = {"price": p, "vol": vol, "chg_24h": chg}
        except (KeyError, ValueError):
            continue
    return out

async def get_exchange_info(session):
    data = await fetch_json(session, f"{FAPI}/fapi/v1/exchangeInfo")
    if not data:
        return {}
    info = {}
    for s in data.get("symbols", []):
        sym = s.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        status = s.get("status", "")
        if status != "TRADING":
            continue
        step = 0.001
        min_q = 0.001
        price_prec = 2
        qty_prec = s.get("quantityPrecision", 3)
        for f in s.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                step = float(f.get("stepSize", 0.001))
                min_q = float(f.get("minQty", 0.001))
            elif f["filterType"] == "PRICE_FILTER":
                tick = f.get("tickSize", "0.01")
                price_prec = max(0, -Decimal(tick).as_tuple().exponent)
        info[sym] = {"step": step, "min_qty": min_q, "price_prec": price_prec, "qty_prec": qty_prec}
    return info

class OutlierHunter:
    def __init__(self):
        self.snapshots_1m = {}
        self.snapshots_5m = {}
        self.snapshots_15m = {}
        self.ts_1m = 0
        self.ts_5m = 0
        self.ts_15m = 0
        self.cooldowns = {}
        self.state = load_state()
        self.min_qty = load_min_qty()
        self.exch_info = {}
        self.api_key = os.getenv(f"{ACCT}_API_KEY", "")
        self.api_secret = os.getenv(f"{ACCT}_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            logger.error(f"Missing {ACCT}_API_KEY / {ACCT}_API_SECRET — cannot trade!")
        self.cooldowns = {k: float(v) for k, v in self.state.get("cooldowns", {}).items()}
        for pos in self.state.get("positions", {}).values():
            _INJECTED_SYMS.add(pos["sym"])

    def update_snapshots(self, tickers, now):
        tradeable = {s: t for s, t in tickers.items() if s in self.exch_info}
        if now - self.ts_1m >= 60:
            self.snapshots_1m = {s: t["price"] for s, t in tradeable.items()}
            self.ts_1m = now
        if now - self.ts_5m >= 300:
            self.snapshots_5m = {s: t["price"] for s, t in tradeable.items()}
            self.ts_5m = now
        if now - self.ts_15m >= 900:
            self.snapshots_15m = {s: t["price"] for s, t in tradeable.items()}
            self.ts_15m = now

    def find_outliers(self, tickers, now):
        self.cooldowns = {s: t for s, t in self.cooldowns.items() if now - t < COOLDOWN_SECS}
        tradeable = {s: t for s, t in tickers.items() if s in self.exch_info}
        changes_5m = {}
        changes_15m = {}
        for sym, t in tradeable.items():
            p = t["price"]
            p5 = self.snapshots_5m.get(sym)
            p15 = self.snapshots_15m.get(sym)
            if p5 and p5 > 0:
                changes_5m[sym] = (p - p5) / p5 * 100
            if p15 and p15 > 0:
                changes_15m[sym] = (p - p15) / p15 * 100
        if len(changes_5m) < 30:
            return [], []
        med_5m = sorted(changes_5m.values())[len(changes_5m) // 2]
        med_15m = sorted(changes_15m.values())[len(changes_15m) // 2] if len(changes_15m) > 30 else 0
        chg_24h_vals = sorted([x["chg_24h"] for x in tradeable.values()])
        med_1h = chg_24h_vals[len(chg_24h_vals) // 2] if len(chg_24h_vals) > 50 else 0
        long_cands = []
        short_cands = []
        for sym, t in tradeable.items():
            if sym in self.cooldowns:
                continue
            key_l = f"{sym}_LONG"
            key_s = f"{sym}_SHORT"
            if key_l in self.state["positions"] or key_s in self.state["positions"]:
                continue
            d5 = changes_5m.get(sym, 0) - med_5m
            d15 = changes_15m.get(sym, 0) - med_15m
            d1h = t["chg_24h"] - med_1h
            strength_5m = d5 / DELTA_5M if DELTA_5M > 0 else 0
            strength_15m = d15 / DELTA_15M if DELTA_15M > 0 else 0
            strength_1h = d1h / DELTA_1H if DELTA_1H > 0 else 0
            strength = max(abs(strength_5m), abs(strength_15m), abs(strength_1h))
            is_long = (d5 > DELTA_5M or d15 > DELTA_15M or d1h > DELTA_1H)
            is_short = (d5 < -DELTA_5M or d15 < -DELTA_15M or d1h < -DELTA_1H)
            if is_long:
                long_cands.append({"sym": sym, "side": "LONG", "d5": d5, "d15": d15, "d1h": d1h, "strength": strength, "price": t["price"], "vol": t["vol"]})
            elif is_short:
                short_cands.append({"sym": sym, "side": "SHORT", "d5": d5, "d15": d15, "d1h": d1h, "strength": strength, "price": t["price"], "vol": t["vol"]})
        long_cands.sort(key=lambda x: -x["strength"])
        short_cands.sort(key=lambda x: -x["strength"])
        return long_cands[:5], short_cands[:5]

    async def place_order(self, session, sym, side, position_side, price):
        if not self.api_key:
            logger.error(f"No API key — cannot place order for {sym}")
            return False
        info = self.exch_info.get(sym)
        if not info:
            logger.debug(f"Skip {sym} — not in exchange info (not TRADING status)")
            return False
        step = info["step"]
        min_q = info["min_qty"]
        mq_file = self.min_qty.get(sym, min_q)
        min_q = max(min_q, mq_file)
        size_usd = min(MAX_SIZE_USD, max(MIN_SIZE_USD, 30.0))
        raw_qty = size_usd / price
        raw_qty = max(raw_qty, min_q * 1.2)
        q = qty_str(raw_qty, step, info["qty_prec"])
        if float(q) <= 0:
            logger.warning(f"Qty zero for {sym} @ {price}")
            return False
        logger.error(f"[OUTLIER_HUNTER_ORDER_DISABLED] {sym} {side} {position_side} qty={q} @ {price} — direct REST order KILLED. All orders must route through execute_now.")
        return False

    async def enter_outlier(self, session, cand, now):
        sym = cand["sym"]
        side_str = cand["side"]
        price = cand["price"]
        order_side = "BUY" if side_str == "LONG" else "SELL"
        ok = await self.place_order(session, sym, order_side, side_str, price)
        if ok:
            key = f"{sym}_{side_str}"
            self.state["positions"][key] = {"sym": sym, "side": side_str, "entry_price": price, "entry_ts": datetime.now(timezone.utc).isoformat(), "max_gain": 0.0, "d5": round(cand["d5"], 2), "d15": round(cand["d15"], 2), "d1h": round(cand["d1h"], 2), "strength": round(cand["strength"], 2)}
            self.cooldowns[sym] = now
            self.state["stats"]["entered"] = self.state["stats"].get("entered", 0) + 1
            logger.warning(f"ENTERED {side_str} {sym} @ {price:.6f} | d5={cand['d5']:+.2f}% d15={cand['d15']:+.2f}% d1h={cand['d1h']:+.2f}% str={cand['strength']:.1f}x")
            try:
                import redis as _redis
                rc = _redis.Redis(port=6379)
                rc.publish("signals_data", json.dumps({"event_type": f"OUTLIER_HUNTER_{side_str}", "symbol": sym, "side": side_str, "price": price, "source": "outlier_hunter", "account": ACCT, "timestamp": datetime.now(timezone.utc).isoformat()}))
                rc.close()
            except Exception:
                pass
            inject_symbol_into_tracking(sym)
            save_state(self.state)
            return True
        return False

    async def manage_exits(self, session, tickers, now):
        """Monitor positions for closure by ez_manage. Eject symbols when position gone.
        Also self-exit as fallback if gain hits trailing stop and ez_manage hasn't acted."""
        if not self.state["positions"]:
            return
        api_positions = await self._fetch_api_positions(session)
        for key, pos in list(self.state["positions"].items()):
            sym = pos["sym"]
            side = pos["side"]
            t = tickers.get(sym)
            if not t:
                continue
            p = t["price"]
            ep = pos["entry_price"]
            gain = ((p - ep) / ep * 100) if side == "LONG" else ((ep - p) / ep * 100)
            pos["max_gain"] = max(pos.get("max_gain", 0), gain)
            mg = pos["max_gain"]
            api_key = f"{sym}_{side}"
            api_amt = api_positions.get(api_key, None)
            if api_amt is not None and abs(api_amt) < 0.0000001:
                logger.warning(f"CLOSED_BY_MANAGE {side} {sym} gain={gain:+.2f}% max={mg:.2f}% (ez_manage handled exit)")
                self.state["stats"]["exited"] = self.state["stats"].get("exited", 0) + 1
                self.state["stats"]["pnl"] = self.state["stats"].get("pnl", 0) + (gain / 100 * MIN_SIZE_USD)
                del self.state["positions"][key]
                remaining_for_sym = any(v["sym"] == sym for v in self.state["positions"].values())
                if not remaining_for_sym:
                    eject_symbol_from_tracking(sym)
                save_state(self.state)
                continue
            reason = ""
            if mg > TRAIL_PCT * 2 and gain < mg - TRAIL_PCT:
                reason = f"TRAIL(g={gain:.2f}%,max={mg:.2f}%)"
            elif gain > 8.0:
                reason = f"BIG_TP({gain:.2f}%)"
            if gain < 0:
                reason = ""
            if reason:
                close_side = "SELL" if side == "LONG" else "BUY"
                ok = await self.place_order(session, sym, close_side, side, p)
                if ok:
                    logger.warning(f"EXIT {side} {sym} @ {p:.6f} gain={gain:+.2f}% reason={reason}")
                    self.state["stats"]["exited"] = self.state["stats"].get("exited", 0) + 1
                    self.state["stats"]["pnl"] = self.state["stats"].get("pnl", 0) + (gain / 100 * MIN_SIZE_USD)
                    del self.state["positions"][key]
                    remaining_for_sym = any(v["sym"] == sym for v in self.state["positions"].values())
                    if not remaining_for_sym:
                        eject_symbol_from_tracking(sym)
                    save_state(self.state)

    async def _fetch_api_positions(self, session):
        """Fetch current positions from Binance API for fin account."""
        if not self.api_key:
            return {}
        try:
            import hmac, hashlib, urllib.parse
            ts = int(time.time() * 1000)
            params = {"timestamp": ts, "recvWindow": 5000}
            query = urllib.parse.urlencode(params)
            sig = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            headers = {"X-MBX-APIKEY": self.api_key}
            async with session.get(f"{FAPI}/fapi/v2/positionRisk?{query}&signature={sig}", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return {}
                data = await r.json()
                out = {}
                for p in data:
                    sym = p.get("symbol", "")
                    ps = p.get("positionSide", "")
                    amt = float(p.get("positionAmt", 0))
                    if sym and ps in ("LONG", "SHORT"):
                        out[f"{sym}_{ps}"] = amt
                return out
        except Exception as e:
            logger.debug(f"API position fetch error: {e}")
            return {}

    async def run(self):
        logger.warning(f"=== OUTLIER HUNTER STARTED === account={ACCT} poll={POLL_INTERVAL}s max_pos={MAX_POSITIONS}")
        logger.warning(f"Thresholds: d5>{DELTA_5M}% d15>{DELTA_15M}% d1h>{DELTA_1H}% trail={TRAIL_PCT}% min_vol=${MIN_VOLUME_USD/1e6:.0f}M")
        async with aiohttp.ClientSession() as session:
            self.exch_info = await get_exchange_info(session)
            logger.info(f"Loaded exchange info for {len(self.exch_info)} symbols")
            heartbeat_ts = 0
            while not SHUTDOWN:
                try:
                    tickers = await get_all_tickers(session)
                    if not tickers:
                        logger.warning("No ticker data — retrying in 30s")
                        await asyncio.sleep(30)
                        continue
                    now = time.time()
                    self.update_snapshots(tickers, now)
                    if not self.snapshots_5m:
                        logger.info(f"Building baselines... {len(tickers)} symbols tracked. Waiting for 5m snapshot.")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    longs, shorts = self.find_outliers(tickers, now)
                    cur_count = len(self.state["positions"])
                    if now - heartbeat_ts > 120:
                        heartbeat_ts = now
                        top_l = f"{longs[0]['sym']}({longs[0]['d5']:+.1f}%)" if longs else "none"
                        top_s = f"{shorts[0]['sym']}({shorts[0]['d5']:+.1f}%)" if shorts else "none"
                        logger.warning(f"SCAN {len(tickers)} syms | pos={cur_count}/{MAX_POSITIONS} | longs={len(longs)} shorts={len(shorts)} | top_L={top_l} top_S={top_s} | stats={self.state['stats']}")
                    if longs or shorts:
                        for cand in longs[:3]:
                            if cur_count >= MAX_POSITIONS:
                                break
                            logger.warning(f"CANDIDATE LONG {cand['sym']} d5={cand['d5']:+.2f}% d15={cand['d15']:+.2f}% d1h={cand['d1h']:+.2f}% str={cand['strength']:.1f}x vol=${cand['vol']/1e6:.0f}M")
                            ok = await self.enter_outlier(session, cand, now)
                            if ok:
                                cur_count += 1
                        for cand in shorts[:3]:
                            if cur_count >= MAX_POSITIONS:
                                break
                            logger.warning(f"CANDIDATE SHORT {cand['sym']} d5={cand['d5']:+.2f}% d15={cand['d15']:+.2f}% d1h={cand['d1h']:+.2f}% str={cand['strength']:.1f}x vol=${cand['vol']/1e6:.0f}M")
                            ok = await self.enter_outlier(session, cand, now)
                            if ok:
                                cur_count += 1
                    await self.manage_exits(session, tickers, now)
                    self.state["cooldowns"] = {s: t for s, t in self.cooldowns.items() if now - t < COOLDOWN_SECS}
                    save_state(self.state)
                except Exception as e:
                    logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
        logger.warning("=== OUTLIER HUNTER STOPPED ===")

if __name__ == "__main__":
    if not load_gpg_env():
        logger.error("Cannot load API keys — exiting")
        sys.exit(1)
    hunter = OutlierHunter()
    asyncio.run(hunter.run())
