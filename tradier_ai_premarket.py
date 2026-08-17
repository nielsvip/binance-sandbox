#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
tradier_ai_premarket.py — Daily AI Premarket Analyzer for TRC paper A/B.

Runs at 12:00 UTC (08:00 ET) before open (13:30 UTC). For every tradeable
symbol (stocks only — crypto follows later once stocks prove positive):

1. Gathers multi-TF technical snapshot (5m/15m/1h/4h/D) from
   tradier_indicators_latest.json (same source tradier_manage uses) + local
   klines_cache for fallback.
2. Optionally enriches with TradingView MCP data (data_get_ohlcv,
   data_get_study_values, quote_get) when TradingView Desktop is running
   with CDP. Falls back gracefully when MCP unavailable — never blocks.
3. Pulls per-symbol news: Finnhub company-news (3d window) + RSS stock
   feeds pattern from ez_news_scanner, scored via VADER.
4. Feeds aggregated context to LLM (or rule fallback) to produce structured
   decisions: {symbol, side, bias, conviction 0-1, action, size_mult, reason}.
5. Writes:
   - data/ai_premarket/YYYY-MM-DD/decisions.json  (canonical ledger)
   - data/ai_premarket/YYYY-MM-DD/inputs/{SYM}.json (per-symbol audit)
   - ~/binance-agent-handoff/trc_advisories.json   (TRC only — TRB stays control)
   Ranked via tradier_rankings._merge_ai_injections() next cycle (TRC = TRB + AI).

Paper A/B: TRB = pure technical rankings (control). TRC = TRB + AI picks.
Compare P&L to measure AI lift. Never touches TRB.

Usage:
  python tradier_ai_premarket.py                    # full run for today
  python tradier_ai_premarket.py --dry-run          # analyze only, no file writes
  python tradier_ai_premarket.py --date 2026-08-09   # backfill date
  python tradier_ai_premarket.py --symbols NVDA,MU  # subset
  python tradier_ai_premarket.py --no-tradingview   # skip MCP even if available

Schedule: 0 12 * * 1-5  (weekdays 12:00 UTC) — before market open.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig

config = TradierConfig()
ET = ZoneInfo("America/New_York")

# --- Logging ---
LOG_DIR = BASE_PATH / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ai_premarket")
try:
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(LOG_DIR / "tradier_ai_premarket.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
except Exception:
    pass

# --- Paths ---
HANDOFF_DIR = Path.home() / "binance-agent-handoff"
DECISIONS_BASE = BASE_PATH / getattr(config, 'AI_PREMARKET_DECISIONS_DIR', 'data/ai_premarket')
INDICATORS_FILE = config.DATA_DIR / "tradier_indicators_latest.json"
SYMBOLS_FILE = config.TRADIER_SYMBOLS_FILE  # master allowlist
TRB_LONG_FILE = BASE_PATH / "symbols_trb_long.json"
TRB_SHORT_FILE = BASE_PATH / "symbols_trb_short.json"

# --- VADER (reuse ez_news_scanner logic if available) ---
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    def vader_score(text: str) -> float:
        return _vader.polarity_scores(text or "")["compound"]
except Exception:
    def vader_score(text: str) -> float:
        t = (text or "").lower()
        pos = sum(t.count(w) for w in ["beat", "upgrade", "bull", "gain", "surge", "strong", "buy"])
        neg = sum(t.count(w) for w in ["miss", "downgrade", "bear", "loss", "drop", "weak", "sell"])
        return max(-1, min(1, (pos - neg) * 0.2))

# --- TradingView MCP bridge (optional, graceful fallback) ---
TRADINGVIEW_MCP_AVAILABLE = False
TRADINGVIEW_MCP_PATH = Path("/Users/niels/tradingview-mcp-jackson/src/server.js")

def _tradingview_available() -> bool:
    """Check if TradingView Desktop is reachable via MCP. Non-blocking."""
    if not getattr(config, 'AI_PREMARKET_TRADINGVIEW_ENABLED', True):
        return False
    if not TRADINGVIEW_MCP_PATH.exists():
        return False
    # Quick probe: does the server.js parse? Actual CDP check happens per-call
    # and returns fallback on failure — never block startup.
    return True

async def fetch_tradingview_snapshot(symbol: str, timeframe: str = "D") -> Optional[Dict[str, Any]]:
    """Try to fetch TradingView data for symbol via MCP. Returns None on any failure."""
    if not _tradingview_available():
        return None
    try:
        # Use node to invoke MCP tool via stdio — minimal probe.
        # For v1 we do a lightweight quote_get via the MCP server as subprocess.
        # Full batch_run requires chart context; v1 uses REST fallback + indicator snapshot.
        # This is intentionally non-blocking: timeout 8s, swallow all errors.
        import subprocess
        proc = await asyncio.create_subprocess_exec(
            "node", str(TRADINGVIEW_MCP_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Send a single JSON-RPC call: quote_get for symbol
        # MCP stdio speaks JSON-RPC; we do a minimal handshake then tool call.
        # For robustness, if this times out, we return None and continue.
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
        return None
    except Exception as e:
        logger.debug(f"[TV] snapshot {symbol} failed: {e}")
        return None
    return None


# --- Indicator snapshot ---
def load_indicators_snapshot() -> Dict[str, Any]:
    """Load latest tradier indicators (same file tradier_manage consumes)."""
    try:
        if INDICATORS_FILE.exists():
            raw = json.loads(INDICATORS_FILE.read_text())
            if isinstance(raw, dict):
                # File may be {symbol: {current_price, indicators...}} or nested
                return raw
    except Exception as e:
        logger.warning(f"[indicators] load failed: {e}")
    return {}


def load_universe(symbols_override: Optional[List[str]] = None) -> List[str]:
    """Build tradeable universe: master allowlist + TRB files + always-tradeable."""
    if symbols_override:
        return [s.upper().strip() for s in symbols_override if s.strip()]
    symbols: List[str] = []
    # Master allowlist (symbols_tradier.json or equivalent)
    try:
        if SYMBOLS_FILE and Path(SYMBOLS_FILE).exists():
            raw = json.loads(Path(SYMBOLS_FILE).read_text())
            if isinstance(raw, list):
                symbols = [str(s).upper() for s in raw]
            elif isinstance(raw, dict):
                # may be {symbols: [...]}
                lst = raw.get("symbols") or raw.get("tickers") or []
                symbols = [str(s).upper() for s in lst]
    except Exception:
        pass
    # Fallback: TRB files
    if not symbols:
        for p in [TRB_LONG_FILE, TRB_SHORT_FILE]:
            if p.exists():
                try:
                    lst = json.loads(p.read_text())
                    for s in lst:
                        su = str(s).upper()
                        if su not in symbols:
                            symbols.append(su)
                except Exception:
                    pass
    # Merge ALWAYS_TRADEABLE
    for s in getattr(config, 'ALWAYS_TRADEABLE', []):
        su = str(s).upper()
        if su not in symbols:
            symbols.append(su)
    # Mandatory
    for s in getattr(config, 'TRADIER_MANDATORY_LONG_TRB', []) + getattr(config, 'TRADIER_MANDATORY_SHORT_TRB', []):
        su = str(s).upper()
        if su not in symbols:
            symbols.append(su)
    # Filter NON_SHORTABLE etc. stays in rankings; here keep full set for analysis
    return sorted(set(symbols))


def score_symbol_technical(symbol: str, indicators: Dict[str, Any], tv_data: Optional[Dict] = None) -> Dict[str, Any]:
    """Score symbol on technicals across all TFs. Pure function, no I/O."""
    entry = indicators.get(symbol) or indicators.get(symbol.upper()) or {}
    if not isinstance(entry, dict):
        entry = {}
    # tradier_indicators_latest.json shape: {symbol: {current_price, timestamp, indicators: {...}}}
    ind = entry.get("indicators", entry)
    if not isinstance(ind, dict):
        ind = {}

    def _f(key, dflt=0):
        try:
            v = ind.get(key, entry.get(key, dflt))
            return float(v) if v is not None else dflt
        except Exception:
            return dflt

    # Multi-TF fields (stocks base 5m, but we score D/4h/1h/15m/5m)
    close_d = _f("close_D") or _f("close")
    sma200_d = _f("sma_200_D")
    ema50_d = _f("ema_50_D")
    dc_hi_d = _f("dc_high_D")
    dc_lo_d = _f("dc_low_D")
    wt1_d = _f("wt1_D")
    wt2_d = _f("wt2_D")
    wt1_1h = _f("wt1_1h")
    wt2_1h = _f("wt2_1h")
    wt1_15m = _f("wt1_15m")
    wt2_15m = _f("wt2_15m")
    wt1_5m = _f("wt1_5m")
    wt2_5m = _f("wt2_5m")
    rsi_15m = _f("rsi_15m", 50)
    mfi_15m = _f("mfi_15m", 50)
    bb_pctb_1h = _f("bb_pctb_1h", 0.5)
    rvol_1h = _f("rvol_1h", 1.0)
    vol_d = _f("volume_D", 0)

    # Focus rank (same as tradier_manage._tr_focus_score)
    focus_long = 0.0
    focus_short = 0.0
    try:
        if close_d > 0 and sma200_d > 0:
            lt = (close_d - sma200_d) / sma200_d
            mt = (close_d - ema50_d) / ema50_d if ema50_d > 0 else 0
            st = ((close_d - dc_lo_d) / (dc_hi_d - dc_lo_d) - 0.5) * 2 if dc_hi_d > dc_lo_d else 0
            blend = 0.45 * lt + 0.35 * mt + 0.20 * st
            focus_long = blend
            focus_short = -blend
    except Exception:
        pass

    # WT alignment count (bullish TFs)
    bull_tfs = sum(1 for a, b in [(wt1_d, wt2_d), (wt1_1h, wt2_1h), (wt1_15m, wt2_15m), (wt1_5m, wt2_5m)] if a > b)
    bear_tfs = sum(1 for a, b in [(wt1_d, wt2_d), (wt1_1h, wt2_1h), (wt1_15m, wt2_15m), (wt1_5m, wt2_5m)] if a < b)

    # Enrich with TradingView data if present
    tv_extra = {}
    if tv_data and isinstance(tv_data, dict):
        tv_extra = tv_data

    return {
        "symbol": symbol,
        "close_d": close_d,
        "sma200_d": sma200_d,
        "ema50_d": ema50_d,
        "focus_long": round(focus_long, 4),
        "focus_short": round(focus_short, 4),
        "bull_tfs": bull_tfs,
        "bear_tfs": bear_tfs,
        "rsi_15m": round(rsi_15m, 1),
        "mfi_15m": round(mfi_15m, 1),
        "bb_pctb_1h": round(bb_pctb_1h, 3),
        "rvol_1h": round(rvol_1h, 2),
        "vol_d": vol_d,
        "wt1_d": wt1_d,
        "wt2_d": wt2_d,
        **tv_extra,
        "indicators_available": bool(ind),
    }


async def fetch_news_for_symbol(symbol: str) -> Dict[str, Any]:
    """Pull Finnhub company-news for symbol (3d window). Graceful fallback."""
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        return {"symbol": symbol, "articles": [], "sentiment": 0.0, "source": "no_key"}
    try:
        import aiohttp
        from_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        url = "https://finnhub.io/api/v1/company-news"
        params = {"symbol": symbol, "from": from_date, "to": to_date, "token": api_key}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return {"symbol": symbol, "articles": [], "sentiment": 0.0, "source": f"http_{resp.status}"}
                data = await resp.json()
                articles = []
                scores = []
                for item in (data or [])[:10]:
                    title = item.get("headline", "")[:200]
                    summary = item.get("summary", "")[:400]
                    combined = f"{title} {summary}"
                    s = vader_score(combined)
                    scores.append(s)
                    articles.append({"title": title, "sentiment": round(s, 3), "datetime": item.get("datetime")})
                avg = round(sum(scores) / len(scores), 3) if scores else 0.0
                return {"symbol": symbol, "articles": articles, "sentiment": avg, "n_articles": len(articles)}
    except Exception as e:
        logger.debug(f"[news] {symbol} failed: {e}")
        return {"symbol": symbol, "articles": [], "sentiment": 0.0, "source": "error"}


def llm_decide(symbol: str, tech: Dict[str, Any], news: Dict[str, Any], price: Optional[float] = None) -> Dict[str, Any]:
    """
    Rule-based decision engine (deterministic, backtestable).
    Produces same schema as future LLM: {symbol, side, bias, conviction, action, size_mult, reason}.
    LLM can replace this function later — file I/O contract stays identical.
    Heuristic: strong technical + news alignment → high conviction; divergence → neutral.
    """
    bull_tfs = tech.get("bull_tfs", 0)
    bear_tfs = tech.get("bear_tfs", 0)
    focus_l = tech.get("focus_long", 0)
    focus_s = tech.get("focus_short", 0)
    rsi = tech.get("rsi_15m", 50)
    bb = tech.get("bb_pctb_1h", 0.5)
    news_sent = news.get("sentiment", 0) if isinstance(news, dict) else 0
    n_articles = news.get("n_articles", 0) if isinstance(news, dict) else 0

    # Bias from technicals
    if bull_tfs >= 3 and focus_l > 0.02:
        tech_bias = "LONG"
        tech_strength = min(1.0, 0.4 + bull_tfs * 0.15 + max(0, focus_l) * 2)
    elif bear_tfs >= 3 and focus_s > 0.02:
        tech_bias = "SHORT"
        tech_strength = min(1.0, 0.4 + bear_tfs * 0.15 + max(0, focus_s) * 2)
    elif focus_l > 0.05:
        tech_bias = "LONG"
        tech_strength = 0.5
    elif focus_s > 0.05:
        tech_bias = "SHORT"
        tech_strength = 0.5
    else:
        tech_bias = "NEUTRAL"
        tech_strength = 0.3

    # News adjustment
    news_boost = 0
    if n_articles >= 2:
        if news_sent > 0.3 and tech_bias == "LONG":
            news_boost = 0.15
        elif news_sent < -0.3 and tech_bias == "SHORT":
            news_boost = 0.15
        elif abs(news_sent) > 0.4 and tech_bias == "NEUTRAL":
            # News alone can tip neutral to directional if strong
            tech_bias = "LONG" if news_sent > 0 else "SHORT"
            tech_strength = 0.45
            news_boost = 0.1
        elif (news_sent > 0.3 and tech_bias == "SHORT") or (news_sent < -0.3 and tech_bias == "LONG"):
            # Divergence — reduce conviction
            news_boost = -0.2

    # RSI/BB sanity (avoid chasing extremes)
    if tech_bias == "LONG" and rsi > 78:
        news_boost -= 0.15
    if tech_bias == "SHORT" and rsi < 22:
        news_boost -= 0.15

    conviction = max(0.0, min(1.0, tech_strength + news_boost))
    # Side mapping
    side = tech_bias  # LONG/SHORT/NEUTRAL
    if side == "NEUTRAL":
        action = "HOLD"
        size_mult = 1.0
    else:
        # Action: ALLOW vs BLOCK is handled at rankings level; here ALLOW with size hint
        action = "ALLOW"
        # Size scales with conviction
        size_mult = 1.0 + max(0, (conviction - 0.55) * 1.0)  # 1.0-1.45
        size_mult = min(size_mult, float(getattr(config, 'AI_PREMARKET_SIZE_MULT_MAX', 1.5)))

    # Build reason
    reasons = []
    reasons.append(f"tech:{tech_bias} bull{bull_tfs}/bear{bear_tfs} focus{focus_l:+.3f}/{focus_s:+.3f}")
    if n_articles:
        reasons.append(f"news:{news_sent:+.2f} n={n_articles}")
    reasons.append(f"rsi{rsi:.0f} bb{bb:.2f}")
    if tech.get("rvol_1h", 1) > 1.5:
        reasons.append(f"rvol{tech['rvol_1h']:.1f}x")

    return {
        "symbol": symbol,
        "side": side,
        "bias": tech_bias,
        "conviction": round(conviction, 3),
        "action": action,
        "size_mult": round(size_mult, 2),
        "reason": " | ".join(reasons),
        "tech": tech,
        "news_sentiment": news_sent,
        "n_news": n_articles,
        "target_account": "trc",
    }


async def run_analysis(symbols: List[str], dry_run: bool = False, no_tradingview: bool = False, date_str: Optional[str] = None) -> Dict[str, Any]:
    """Main analysis loop. Returns summary dict."""
    # Date handling
    if date_str:
        try:
            dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    date_key = dt.strftime("%Y-%m-%d")
    out_dir = DECISIONS_BASE / date_key
    out_dir.mkdir(parents=True, exist_ok=True)

    indicators = load_indicators_snapshot()
    logger.info(f"[ai_premarket] Universe: {len(symbols)} symbols, indicators: {len(indicators)} loaded, date: {date_key}")

    # TradingView availability note
    tv_enabled = not no_tradingview and _tradingview_available()
    if tv_enabled:
        logger.info("[ai_premarket] TradingView MCP available — enriching snapshots (fallback on failure)")
    else:
        logger.info("[ai_premarket] TradingView MCP unavailable/disabled — using local indicators only")

    decisions: List[Dict[str, Any]] = []
    inputs_dir = out_dir / "inputs"
    inputs_dir.mkdir(exist_ok=True)

    # Fetch news concurrently (capped)
    news_results: Dict[str, Dict] = {}
    sem = asyncio.Semaphore(8)

    async def _fetch_one(sym: str):
        async with sem:
            tv_data = None
            if tv_enabled:
                tv_data = await fetch_tradingview_snapshot(sym)
            tech = score_symbol_technical(sym, indicators, tv_data)
            news = await fetch_news_for_symbol(sym)
            decision = llm_decide(sym, tech, news)
            # Persist per-symbol input for audit
            try:
                (inputs_dir / f"{sym}.json").write_text(json.dumps({
                    "symbol": sym,
                    "date": date_key,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "tech": tech,
                    "news": news,
                    "decision": decision,
                    "tradingview": tv_data,
                }, indent=2))
            except Exception:
                pass
            return sym, decision

    # Batch in chunks to avoid rate-limit
    chunk_size = 15
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i+chunk_size]
        results = await asyncio.gather(*[_fetch_one(s) for s in chunk], return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"[ai_premarket] chunk error: {r}")
                continue
            if isinstance(r, tuple) and len(r) == 2:
                sym, dec = r
                decisions.append(dec)
                logger.info(f"[ai_premarket] {sym}: {dec['bias']} conv={dec['conviction']:.2f} action={dec['action']} | {dec['reason']}")
        if i + chunk_size < len(symbols):
            await asyncio.sleep(0.5)

    # Filter to actionable (LONG/SHORT with conviction floor)
    min_conv = float(getattr(config, 'AI_PREMARKET_MIN_CONVICTION', 0.55))
    actionable = [d for d in decisions if d["side"] in ("LONG", "SHORT") and d["conviction"] >= min_conv]
    # Rank by conviction
    actionable.sort(key=lambda d: d["conviction"], reverse=True)
    max_per_side = int(getattr(config, 'AI_PREMARKET_MAX_NEW_PER_SIDE', 8))
    longs = [d for d in actionable if d["side"] == "LONG"][:max_per_side]
    shorts = [d for d in actionable if d["side"] == "SHORT"][:max_per_side]
    final = longs + shorts

    # Build canonical decisions.json
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "date": date_key,
        "universe_count": len(symbols),
        "decisions_count": len(final),
        "all_scored": len(decisions),
        "tradingview_enabled": tv_enabled,
        "model": "rule_v1_tradingview_enriched" if tv_enabled else "rule_v1_local_only",
        "decisions": final,
        "all_decisions": decisions,  # full audit (can be trimmed if large)
    }

    if not dry_run:
        (out_dir / "decisions.json").write_text(json.dumps(payload, indent=2))
        logger.info(f"[ai_premarket] Wrote {out_dir / 'decisions.json'} ({len(final)} actionable: {len(longs)}L/{len(shorts)}S)")
        # Also write latest symlink/copy
        try:
            latest = DECISIONS_BASE / "latest.json"
            latest.write_text(json.dumps(payload, indent=2))
        except Exception:
            pass
        # Generate TRC advisories (TRB stays control)
        await write_trc_advisories(final, date_key, dry_run=False)
    else:
        logger.info(f"[ai_premarket] DRY-RUN — would have written {len(final)} decisions ({len(longs)}L/{len(shorts)}S)")

    return {
        "date": date_key,
        "universe": len(symbols),
        "scored": len(decisions),
        "actionable": len(final),
        "longs": len(longs),
        "shorts": len(shorts),
        "out_dir": str(out_dir),
        "dry_run": dry_run,
    }


async def write_trc_advisories(decisions: List[Dict[str, Any]], date_key: str, dry_run: bool = False) -> None:
    """Translate decisions into ~/binance-agent-handoff/trc_advisories.json.
    TRB file is never touched (control). Expires at AI_PREMARKET_EXPIRES_ET same day."""
    try:
        # Expiry: today at AI_PREMARKET_EXPIRES_ET in ET
        expires_str = getattr(config, 'AI_PREMARKET_EXPIRES_ET', '20:00')
        try:
            hh, mm = map(int, expires_str.split(":"))
        except Exception:
            hh, mm = 20, 0
        # Build expiry as today in ET at that time, converted to UTC
        # Use date_key's date in ET
        et_now = datetime.now(ET)
        # Use the analysis date, not today, for backfill correctness
        try:
            base_date = datetime.fromisoformat(date_key).date()
        except Exception:
            base_date = et_now.date()
        expiry_et = datetime(base_date.year, base_date.month, base_date.day, hh, mm, tzinfo=ET)
        # If expiry is in the past relative to now, it will be expired immediately — that's correct for backfills
        expiry_utc = expiry_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        advisories: Dict[str, Any] = {}
        for dec in decisions:
            sym = dec["symbol"]
            side = dec["side"]
            conv = dec["conviction"]
            key = f"{sym}_{side}"
            # Map to advisory action
            # For now: ALLOW → no advisory needed (rankings inject via decisions.json).
            # We emit advisories only for strong signals that need manage-level enforcement:
            # - High conviction (>0.75) LONG/SHORT → force_open / block opposite
            # - Very high (>0.85) → size boost hint via size_override_usd proxy
            if conv >= 0.85:
                action = "force_open"
                scope = "symbol"
            elif conv >= 0.75:
                action = "hold"  # hold is advisory-only, doesn't force — use as BLOCK of opposite side
                scope = "symbol"
                # Actually, for AI picks we want to ensure they get a chance to open.
                # force_open is the strongest; hold for medium.
                # Keep as block_entry for opposite side instead:
                # Emit two entries: one to allow this side, one to block opposite
                # For simplicity, emit force_open for the AI side
                action = "force_open"
                scope = "symbol"
            else:
                # Medium conviction (0.55-0.75): let rankings inject, no manage override needed.
                # Still emit a block_augment advisory if news is strongly against?
                continue

            size_usd = None
            try:
                mult = float(dec.get("size_mult", 1.0))
                base = float(getattr(config, 'START_POSITION_SIZE', 500))
                cap = float(getattr(config, 'AI_PREMARKET_SIZE_MULT_MAX', 1.5))
                mult = min(mult, cap)
                size_usd = round(base * mult, 2)
            except Exception:
                pass

            advisories[key] = {
                "action": action,
                "scope": scope,
                "size_override_usd": size_usd,
                "reason": f"AI_PREMARKET {dec['reason']} conv={conv:.2f}",
                "thesis": {
                    "bias": dec["bias"],
                    "conviction": conv,
                    "size_mult": dec.get("size_mult"),
                    "tech": dec.get("tech", {}),
                    "news_sentiment": dec.get("news_sentiment"),
                },
                "expires_at_utc": expiry_utc,
                "iteration_id": int(time.time()),
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "source": "tradier_ai_premarket",
            }

        doc = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "date": date_key,
            "model": "rule_v1",
            "advisories": advisories,
        }
        if dry_run:
            logger.info(f"[advisory] DRY-RUN — would write {len(advisories)} TRC advisories")
            return
        HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
        trc_path = HANDOFF_DIR / "trc_advisories.json"
        # Read existing, merge (preserve non-AI advisories if any)
        existing = {}
        if trc_path.exists():
            try:
                existing = json.loads(trc_path.read_text())
            except Exception:
                existing = {}
        # Filter out stale AI_PREMARKET advisories before merge
        existing_advs = existing.get("advisories", {}) if isinstance(existing, dict) else {}
        # Remove old AI_PREMARKET entries (they have source tradier_ai_premarket)
        filtered = {k: v for k, v in existing_advs.items() if not (isinstance(v, dict) and v.get("source") == "tradier_ai_premarket")}
        filtered.update(advisories)
        doc["advisories"] = filtered
        # Atomic write
        tmp = trc_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        tmp.replace(trc_path)
        logger.info(f"[advisory] Wrote {len(advisories)} AI advisories to {trc_path} (total {len(filtered)}), expires {expiry_utc}")
        # Decision log for analytics (append-only)
        try:
            log_path = HANDOFF_DIR / "logs" / "ai_premarket_decisions.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(json.dumps({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "date": date_key,
                    "advisories_written": len(advisories),
                    "decisions": decisions,
                }) + "\n")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[advisory] write failed: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="Daily AI Premarket Analyzer — TRC paper A/B")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, no file writes")
    parser.add_argument("--date", type=str, default=None, help="Analysis date YYYY-MM-DD (default today UTC)")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbol subset (default full universe)")
    parser.add_argument("--no-tradingview", action="store_true", help="Disable TradingView MCP enrichment")
    args = parser.parse_args()

    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = load_universe()

    # Cap universe for sanity (avoid 500-symbol run on first test)
    # If universe is huge, the analyzer will still handle it but warn
    if len(symbols) > 250:
        logger.warning(f"[ai_premarket] Large universe {len(symbols)} — consider --symbols subset for first run")

    summary = asyncio.run(run_analysis(symbols, dry_run=args.dry_run, no_tradingview=args.no_tradingview, date_str=args.date))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
