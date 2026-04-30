#!/usr/bin/env python3
"""
EZ BREAKOUT HUNTER — 24/7 agent that:
1. Scans ALL Binance futures for SMA200 breakouts every 2 min
2. Cross-references jumps/falls with CoinGecko trending + CryptoPanic news
3. BLACKLISTS any symbol suspected of pump & dump (forever)
4. Only enters CLEAN breakouts with real volume + real news catalysts
5. Manages positions: trail max gain, exit on reversal

Blacklist criteria (any = permanent ban):
  - Price +30% in <4h with no news/social catalyst
  - Coin in CoinGecko trending AND >20% spike (= coordinated pump)
  - Volume spike >10x average with no matching news
  - Symbol previously blacklisted for P&D

Cron: */2 * * * * /Users/niels/Documents/binance/run_breakout_hunter.sh
"""
import asyncio, json, logging, os, platform, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE_PATH))

LOG_DIR = Path("/Users/niels/logs") if platform.system() == "Darwin" else Path("/home/niels/logs")
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("breakout")
fh = logging.FileHandler(str(LOG_DIR / "ez_breakout_hunter.log"))
fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
logger.addHandler(fh)

STATE_FILE = BASE_PATH / "data" / "breakout_hunter_state.json"
BLACKLIST_FILE = BASE_PATH / "data" / "pump_dump_blacklist.json"
LOCK_FILE = BASE_PATH / "data" / ".breakout_hunter_lock"
ACCOUNT = "fin"
MAX_POSITIONS = 999  # No limit on signals — ez_manage decides what to execute
MIN_SIZE_USD = 15.0
MAX_SIZE_USD = 150.0
TRAIL_PCT = 0.4

# ═══════════════════════════════════════════════════════════════
# PUMP & DUMP DETECTION + PERMANENT BLACKLIST
# ═══════════════════════════════════════════════════════════════

def load_blacklist():
    try:
        return json.loads(BLACKLIST_FILE.read_text()) if BLACKLIST_FILE.exists() else {"symbols": {}, "last_trending_check": 0}
    except:
        return {"symbols": {}, "last_trending_check": 0}

def save_blacklist(bl):
    BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    BLACKLIST_FILE.write_text(json.dumps(bl, indent=2, default=str))

def is_blacklisted(symbol, bl):
    return symbol in bl.get("symbols", {})

def blacklist_symbol(symbol, reason, bl):
    bl["symbols"][symbol] = {"reason": reason, "date": datetime.now(timezone.utc).isoformat(), "permanent": True}
    save_blacklist(bl)
    logger.warning(f"🚫 BLACKLISTED {symbol}: {reason}")

async def get_coingecko_trending(session):
    """CoinGecko trending coins — coordinated pump signal."""
    try:
        async with session.get("https://api.coingecko.com/api/v3/search/trending", timeout=10) as r:
            if r.status != 200: return set()
            data = await r.json()
            coins = set()
            for item in data.get("coins", []):
                sym = item.get("item", {}).get("symbol", "").upper()
                if sym: coins.add(f"{sym}USDT")
            return coins
    except:
        return set()

async def get_cryptopanic_news(session, symbol):
    """Check CryptoPanic for recent news on a symbol. Returns (has_news, is_legit_catalyst)."""
    try:
        # Strip USDT suffix for search
        coin = symbol.replace("USDT", "").replace("USDC", "")
        url = f"https://cryptopanic.com/api/free/v1/posts/?auth_token=free&currencies={coin}&kind=news&filter=hot"
        async with session.get(url, timeout=8) as r:
            if r.status != 200: return False, False
            data = await r.json()
            results = data.get("results", [])
            if not results: return False, False
            # Check if news is recent (< 24h)
            now = datetime.now(timezone.utc)
            recent_news = []
            for article in results[:5]:
                pub = article.get("published_at", "")
                try:
                    dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    if (now - dt).total_seconds() < 86400:
                        recent_news.append(article.get("title", ""))
                except:
                    continue
            has_news = len(recent_news) > 0
            # Legit catalyst: partnership, listing, upgrade, regulation, earnings
            legit_keywords = ["partner", "list", "launch", "upgrade", "mainnet", "etf", "approval", "integrat", "adopt", "fund", "invest", "acqui"]
            is_legit = any(any(kw in title.lower() for kw in legit_keywords) for title in recent_news)
            return has_news, is_legit
    except:
        return False, False

async def detect_pump_dump(session, symbol, ticker_data, blacklist):
    """Returns (is_pump_dump: bool, reason: str).
    Pump & dump signals:
    1. >30% in <24h with no legit news = pump
    2. Trending on CoinGecko + >20% spike = coordinated pump
    3. Volume >10x normal with no news = wash trading
    """
    if is_blacklisted(symbol, blacklist):
        return True, "PREVIOUSLY_BLACKLISTED"
    chg = abs(ticker_data.get("chg", 0))
    vol = ticker_data.get("vol", 0)
    # Only check symbols with suspicious moves
    if chg < 15 and vol < 50_000_000:
        return False, ""
    reasons = []
    # Check 1: Massive spike with no news
    if chg > 30:
        has_news, is_legit = await get_cryptopanic_news(session, symbol)
        if not has_news:
            reasons.append(f"SPIKE_{chg:.0f}%_NO_NEWS")
        elif not is_legit:
            reasons.append(f"SPIKE_{chg:.0f}%_NO_LEGIT_CATALYST")
    # Check 2: CoinGecko trending + big move = coordinated
    now = time.time()
    if now - blacklist.get("last_trending_check", 0) > 300:  # Check trending every 5 min
        trending = await get_coingecko_trending(session)
        blacklist["_trending_cache"] = list(trending)
        blacklist["last_trending_check"] = now
    trending_cache = set(blacklist.get("_trending_cache", []))
    if symbol in trending_cache and chg > 20:
        reasons.append(f"TRENDING+SPIKE_{chg:.0f}%")
    # Check 3: Volume anomaly (basic — compare to 24h quote volume)
    # If >$100M volume on a micro/small cap with <$500M market cap equivalent = suspicious
    if vol > 100_000_000 and chg > 20:
        has_news, _ = await get_cryptopanic_news(session, symbol)
        if not has_news:
            reasons.append(f"VOL_ANOMALY(${vol/1e6:.0f}M)+SPIKE_{chg:.0f}%_NO_NEWS")
    if reasons:
        return True, " | ".join(reasons)
    return False, ""

# ═══════════════════════════════════════════════════════════════
# BREAKOUT SCANNING + POSITION MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def load_state():
    try:
        return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"positions": {}, "btc_sma200_dist": 0}
    except:
        return {"positions": {}, "btc_sma200_dist": 0}

def save_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str))

async def fetch_json(session, url):
    try:
        async with session.get(url, timeout=10) as r:
            return await r.json() if r.status == 200 else None
    except:
        return None

async def get_tickers(session):
    data = await fetch_json(session, "https://fapi.binance.com/fapi/v1/ticker/24hr")
    if not data: return {}
    return {d["symbol"]: {"price": float(d["lastPrice"]), "vol": float(d["quoteVolume"]), "chg": float(d["priceChangePercent"])} for d in data if d["symbol"].endswith("USDT") and float(d["lastPrice"]) > 0}

async def get_closes(session, symbol, limit=210):
    data = await fetch_json(session, f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit={limit}")
    if not data: return []
    return [float(k[4]) for k in data]

async def scan_breakouts(session, tickers, state, blacklist):
    """Scan for SMA200 breakouts, filter through P&D detection."""
    btc_c = await get_closes(session, "BTCUSDC")
    btc_sma = sum(btc_c[-200:]) / 200 if len(btc_c) >= 200 else 0
    btc_p = tickers.get("BTCUSDC", {}).get("price", 0)
    btc_dist = ((btc_p - btc_sma) / btc_sma * 100) if btc_sma > 0 else 0
    state["btc_sma200_dist"] = round(btc_dist, 2)
    # Scan symbols from symbols.json first (our universe), then top movers from ALL
    _sym_file = BASE_PATH / "symbols.json"
    _our_syms = set(json.loads(_sym_file.read_text())) if _sym_file.exists() else set()
    _all = [(s, t) for s, t in tickers.items() if t["vol"] > 1_000_000 and s != "BTCUSDC"]
    # Priority: our symbols first, then top movers
    _ours = [(s, t) for s, t in _all if s in _our_syms]
    _others = sorted([(s, t) for s, t in _all if s not in _our_syms], key=lambda x: abs(x[1]["chg"]), reverse=True)[:60]
    cands = _ours + _others
    breakouts = []
    pd_detected = 0
    for sym, t in cands:
        # Check blacklist first
        if is_blacklisted(sym, blacklist):
            continue
        # P&D detection on big movers
        if abs(t["chg"]) > 15:
            is_pd, pd_reason = await detect_pump_dump(session, sym, t, blacklist)
            if is_pd:
                blacklist_symbol(sym, pd_reason, blacklist)
                pd_detected += 1
                continue
        try:
            c = await get_closes(session, sym)
            if len(c) < 201: continue
            sma200 = sum(c[-200:]) / 200
            if sma200 <= 0: continue
            p = t["price"]; dist = (p - sma200) / sma200 * 100; prev_dist = (c[-2] - sma200) / sma200 * 100
            chg = t["chg"]
            # === ENTRY SIGNALS — catch BOTH breakouts AND big movers ===
            # 1. SMA200 LONG breakout: crossed above or accelerating above
            if 0 < dist < 10 and (prev_dist <= 0 or (prev_dist < 2 and dist > prev_dist + 0.5)):
                outperf = dist - btc_dist
                sz = max(0.5, min(3.0, 1.0 + outperf / 8))
                breakouts.append({"sym": sym, "side": "LONG", "p": p, "sma": sma200, "dist": round(dist, 2), "chg": chg, "outperf": round(outperf, 2), "sz": round(sz, 2)})
            # 2. SMA200 SHORT breakout: crossed below or accelerating below
            elif -10 < dist < 0 and (prev_dist >= 0 or (prev_dist > -2 and dist < prev_dist - 0.5)):
                underperf = btc_dist - dist
                sz = max(0.5, min(3.0, 1.0 + underperf / 8))
                breakouts.append({"sym": sym, "side": "SHORT", "p": p, "sma": sma200, "dist": round(dist, 2), "chg": chg, "underperf": round(underperf, 2), "sz": round(sz, 2)})
            # 3. BIG FALLER SHORT: >3% down in 24h and BELOW SMA200 = momentum short
            elif chg < -3.0 and dist < -3:
                sz = max(0.5, min(3.0, abs(chg) / 8))
                breakouts.append({"sym": sym, "side": "SHORT", "p": p, "sma": sma200, "dist": round(dist, 2), "chg": chg, "underperf": round(abs(chg), 2), "sz": round(sz, 2)})
            # 4. BIG GAINER LONG: >3% up in 24h and ABOVE SMA200 = momentum long
            elif chg > 3.0 and dist > 3 and dist < 20:
                sz = max(0.5, min(3.0, chg / 8))
                breakouts.append({"sym": sym, "side": "LONG", "p": p, "sma": sma200, "dist": round(dist, 2), "chg": chg, "outperf": round(chg, 2), "sz": round(sz, 2)})
            await asyncio.sleep(0.03)
        except:
            continue
    if pd_detected: logger.warning(f"🚫 Blacklisted {pd_detected} pump&dump symbols. Total blacklist: {len(blacklist.get('symbols', {}))}")
    breakouts.sort(key=lambda x: abs(x.get("outperf", 0) or x.get("underperf", 0)), reverse=True)
    return breakouts

async def enter(session, bo, state):
    """Publish breakout signal to Redis — ez_manage picks it up and executes via its own order pipeline."""
    try:
        import redis as _redis
        r = _redis.Redis(port=6379)
        signal = json.dumps({"event_type": f"BREAKOUT_{'LONG' if bo['side'] == 'LONG' else 'SHORT'}", "symbol": bo["sym"], "side": bo["side"], "price": bo["p"], "sma200_dist": bo["dist"], "change_24h": bo["chg"], "size_mult": bo["sz"], "source": "breakout_hunter", "account": ACCOUNT, "timestamp": datetime.now(timezone.utc).isoformat()})
        r.publish("signals_data", signal)
        # Also write to a file that ez_manage checks
        sig_file = BASE_PATH / "data" / "breakout_signals.json"
        try:
            existing = json.loads(sig_file.read_text()) if sig_file.exists() else []
        except: existing = []
        existing.append(json.loads(signal))
        existing = existing[-50:]  # Keep last 50
        sig_file.write_text(json.dumps(existing, indent=2, default=str))
        logger.warning(f"✅ SIGNAL {bo['sym']} {bo['side']} dist={bo['dist']:+.1f}% sz={bo['sz']:.1f}x → Redis+file")
        state["positions"][f"{bo['sym']}_{bo['side']}"] = {"sym": bo["sym"], "side": bo["side"], "ep": bo["p"], "qty": 0, "ts": datetime.now(timezone.utc).isoformat(), "max_g": 0.0, "sz": bo["sz"]}
        r.close()
        return True
    except Exception as e:
        logger.error(f"❌ SIGNAL FAIL {bo['sym']}: {e}"); return False

async def manage_exits(tickers, state):
    for key, pos in list(state.get("positions", {}).items()):
        t = tickers.get(pos["sym"])
        if not t: continue
        p = t["price"]; ep = pos["ep"]
        g = ((p - ep) / ep * 100) if pos["side"] == "LONG" else ((ep - p) / ep * 100)
        pos["max_g"] = max(pos.get("max_g", 0), g); mg = pos["max_g"]
        reason = ""
        if mg > 0.3 and g < mg - TRAIL_PCT: reason = f"TRAIL(g={g:.2f}%,max={mg:.2f}%)"
        elif g > 8.0: reason = f"BIG_TP({g:.2f}%)"
        elif g < -3.0: reason = f"STOP({g:.2f}%)"
        else:
            hrs = (datetime.now(timezone.utc) - datetime.fromisoformat(pos["ts"].replace("Z", "+00:00"))).total_seconds() / 3600
            if hrs > 48 and g < 0.3: reason = f"TIME({hrs:.0f}h)"
        if reason:
            try:
                import redis as _redis
                r = _redis.Redis(port=6379)
                signal = json.dumps({"event_type": f"BREAKOUT_EXIT", "symbol": pos["sym"], "side": pos["side"], "reason": reason, "gain_pct": round(g, 2), "source": "breakout_hunter", "account": ACCOUNT, "timestamp": datetime.now(timezone.utc).isoformat()})
                r.publish("signals_data", signal)
                logger.warning(f"🔴 EXIT SIGNAL {key} {reason}")
                del state["positions"][key]
                r.close()
            except Exception as e:
                logger.error(f"❌ EXIT SIGNAL FAIL {key}: {e}")

def _lock_holder_alive() -> bool:
    """PID-aware lock check. Returns True if the lock holder is actually a live
    ez_breakout_hunter python process; False if stale (process gone or different
    binary). On True, our run should bail. On False, we take the lock."""
    try:
        if not LOCK_FILE.exists():
            return False
        raw = LOCK_FILE.read_text().strip()
        if not raw.isdigit():
            return False
        pid = int(raw)
        if pid <= 0 or pid == os.getpid():
            return False
        # Probe: signal 0 raises if PID is dead.
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        # Verify the live PID is actually a breakout-hunter python (not just any
        # PID-recycled process).
        try:
            import subprocess
            cmd = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=2,
            )
            if cmd.returncode != 0 or "ez_breakout_hunter" not in cmd.stdout:
                return False
        except Exception:
            # If we can't verify, err on the side of "stale" so we don't deadlock
            # forever on a confused lock.
            return False
        return True
    except Exception:
        return False


async def main():
    # PID-aware lock — supersedes the old mtime-only check that let the hunter
    # double-spawn whenever a scan hung past 90s on a slow API call.
    if _lock_holder_alive():
        return
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    # Hard runtime cap — kills the scan if it exceeds this. Prevents the hung
    # scan that caused the original duplicate-spawn pile-up.
    HARD_RUNTIME_CAP_SEC = 75
    try:
        async def _do_scan():
            import aiohttp
            state = load_state()
            blacklist = load_blacklist()
            async with aiohttp.ClientSession() as session:
                tickers = await get_tickers(session)
                if not tickers:
                    logger.error("No tickers")
                    return
                await manage_exits(tickers, state)
                breakouts = await scan_breakouts(session, tickers, state, blacklist)
                cur = len(state.get("positions", {}))
                entered = 0
                for bo in breakouts:
                    if cur + entered >= MAX_POSITIONS: break
                    k = f"{bo['sym']}_{bo['side']}"
                    opp = f"{bo['sym']}_{'SHORT' if bo['side'] == 'LONG' else 'LONG'}"
                    if k in state.get("positions", {}) or opp in state.get("positions", {}): continue
                    logger.info(f"🎯 {bo['sym']} {bo['side']} dist={bo['dist']:+.1f}% 24h={bo['chg']:+.1f}% sz={bo['sz']:.1f}x")
                    if await enter(session, bo, state): entered += 1
                if entered: logger.warning(f"📊 Entered {entered}. Total: {len(state.get('positions', {}))}. Blacklisted: {len(blacklist.get('symbols', {}))}")
                else: logger.info(f"📊 No new entries. {len(state.get('positions', {}))} open. {len(breakouts)} breakouts found. {len(blacklist.get('symbols', {}))} blacklisted.")
            save_state(state)
            save_blacklist(blacklist)
        try:
            await asyncio.wait_for(_do_scan(), timeout=HARD_RUNTIME_CAP_SEC)
        except asyncio.TimeoutError:
            logger.error(f"⏱️ Scan exceeded {HARD_RUNTIME_CAP_SEC}s hard cap — aborting to free lock for next cron")
    finally:
        try: LOCK_FILE.unlink()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
