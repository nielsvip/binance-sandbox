#!/usr/bin/env python3
"""Morning Briefing Email — stocks (tra + trb) + options.
Cron: 13:35 UTC (9:35 AM ET) weekdays.

Data sources:
  - Tradier API: real positions, balances, live quotes
  - Yahoo Finance RSS: per-ticker news
  - CNBC RSS: market headlines
  - Redis: Fear & Greed
  - Options scanner JSON: outlier plays + spreads
"""
import asyncio
import warnings
warnings.filterwarnings("ignore", message="Unclosed client session")
warnings.filterwarnings("ignore", message="unclosed.*<aiohttp")
import json
import logging
import os
import platform
import smtplib
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from xml.etree import ElementTree

BASE = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE))
DATA_DIR = BASE / "data"
TRADIER_DIR = DATA_DIR / "tradier"
TO_EMAIL = "nielsvip@gmail.com"
FROM_EMAIL = "nielsvip@gmail.com"
PUBLIC_RECIPIENTS = [
    "fekkevddoelen@outlook.com",
]

CSS = """
body { font-family: -apple-system, 'Segoe UI', Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 15px; background: #fafafa; color: #222; font-size: 13px; }
h1 { color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 8px; font-size: 20px; margin-bottom: 5px; }
h2 { color: #1a1a2e; border-bottom: 1px solid #ddd; padding-bottom: 5px; margin-top: 28px; font-size: 16px; }
h3 { color: #333; font-size: 13px; margin: 12px 0 6px 0; }
table { border-collapse: collapse; width: 100%; font-size: 12px; border: 1px solid #ddd; margin-bottom: 10px; }
th { background: #1a1a2e; color: #fff; padding: 5px 7px; text-align: left; font-weight: 600; font-size: 11px; }
td { padding: 4px 7px; border-bottom: 1px solid #eee; }
tr:nth-child(even) { background: #f5f5f5; }
p { font-size: 13px; line-height: 1.5; margin: 6px 0; }
.g { color: #2e7d32; } .r { color: #c62828; } .o { color: #ef6c00; } .gr { color: #888; }
.b { font-weight: 700; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.pg { background: #e8f5e9; color: #2e7d32; } .pr { background: #ffebee; color: #c62828; }
.po { background: #fff3e0; color: #ef6c00; } .pb { background: #e3f2fd; color: #1565c0; }
.box { padding: 10px 14px; background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; margin: 8px 0; }
.news { padding: 6px 12px; background: #f8f9fa; border-left: 3px solid #546e7a; margin: 4px 0; font-size: 12px; line-height: 1.4; }
.news b { color: #1a1a2e; }
"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
logger = logging.getLogger("morning_email")


def get_gmail_password():
    try:
        return subprocess.check_output(["security", "find-generic-password", "-a", FROM_EMAIL, "-s", "gmail-app-password", "-w"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        logger.warning("Keychain unavailable (cron?), trying file fallback")
    try:
        pw_file = os.path.expanduser("~/.gmail_app_pw")
        if os.path.exists(pw_file):
            with open(pw_file) as f:
                pw = f.read().strip()
            if pw:
                return pw
    except Exception:
        pass
    logger.error("Failed to get Gmail password from Keychain AND file fallback")
    return None


# ── Tradier API helpers ──────────────────────────────────────────────────────

def get_weekly_closed_local(account_key):
    """Get trades closed this week from local JSONL history files — no API."""
    history_dir = BASE / "data" / "tradier" / "history" / account_key
    if not history_dir.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    events = []
    for jf in sorted(history_dir.glob("*.jsonl")):
        stem = jf.stem
        parts = stem.rsplit("_", 1)
        symbol = parts[0] if len(parts) == 2 else stem
        side = parts[1] if len(parts) == 2 else "UNKNOWN"
        try:
            for line in jf.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    events.append({"ts": rec.get("ts", ""), "type": rec.get("type", ""), "symbol": symbol, "side": side, "account": account_key, "qty": float(rec.get("qty", 0)), "price": float(rec.get("price", 0)), "value": float(rec.get("value", 0)), "reason": rec.get("reason", ""), "is_stock": True})
                except (json.JSONDecodeError, ValueError):
                    continue
        except Exception:
            continue
    events.sort(key=lambda e: e.get("ts", ""))
    from collections import defaultdict
    grouped = defaultdict(list)
    for e in events:
        grouped[f"{e['symbol']}_{e['side']}"].append(e)
    closed = []
    for key, evts in grouped.items():
        evts.sort(key=lambda x: x.get("ts", ""))
        sym, side = key.rsplit("_", 1)
        open_qty, open_cost = 0.0, 0.0
        for ev in evts:
            t = ev["type"].upper()
            qty, price, ts = ev["qty"], ev["price"], ev["ts"]
            if t in ("AUGMENT", "OPEN", "QUICK_OPEN", "REENTRY"):
                open_cost += qty * price
                open_qty += qty
            elif t in ("REDUCE", "CLOSE", "QUICK_CLOSE") and open_qty > 0:
                reduce_qty = min(qty, open_qty)
                entry_avg = open_cost / open_qty
                pnl = (price - entry_avg) * reduce_qty if side == "LONG" else (entry_avg - price) * reduce_qty
                pnl_pct = ((price - entry_avg) / entry_avg * 100) * (1 if side == "LONG" else -1)
                if ts[:10] >= cutoff:
                    closed.append({"symbol": sym, "gain_loss": round(pnl, 2), "gain_pct": round(pnl_pct, 2), "close_date": ts[:10], "side": side})
                remaining = open_qty - reduce_qty
                open_cost = entry_avg * remaining if remaining > 0 else 0.0
                open_qty = remaining
    closed.sort(key=lambda x: x["close_date"], reverse=True)
    return closed


def get_account_data_local(account_key):
    """Load positions from local files — no API calls. Source of truth is binance/{account_key}/."""
    _OPT_RE = __import__("re").compile(r"^[A-Z]{1,6}\d{6}[CP]\d{4,}$")
    result = {"positions": [], "balance": {}, "quotes": {}, "account_key": account_key, "option_positions": [], "pending_orders": []}
    prices_path = DATA_DIR / "tradier" / "tradier_prices_latest.json"
    prices = {}
    try:
        raw = json.loads(prices_path.read_text())
        prices = raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw
    except Exception:
        pass
    positions = []
    for side_label, fname in [("LONG", "long_positions.json"), ("SHORT", "short_positions.json")]:
        pos_path = BASE / account_key / fname
        if not pos_path.exists():
            continue
        try:
            data = json.loads(pos_path.read_text())
        except Exception:
            continue
        for key, pos in data.items():
            sym = pos.get("symbol", "")
            if not sym:
                continue
            amt = float(pos.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            if _OPT_RE.match(sym):
                cost_per_unit = float(pos.get("entry_price", 0) or 0)
                result["option_positions"].append({"symbol": sym, "quantity": amt, "cost_basis": round(cost_per_unit * amt * 100, 2)})
                continue
            entry_price = float(pos.get("entry_price", 0) or 0)
            mark = float(pos.get("mark_price") or 0) or float((prices.get(sym) or {}).get("last", 0) or 0)
            pnl = float(pos.get("unrealized_pnl", 0) or 0)
            gain_pct = float(pos.get("gain", 0) or 0)
            mkt_val = mark * amt if mark else entry_price * amt
            positions.append({"symbol": sym, "side": side_label, "qty": amt, "avg_cost": round(entry_price, 4), "last": round(mark, 4), "prev_close": 0, "day_chg": 0.0, "mkt_val": round(mkt_val, 2), "cost_basis": round(entry_price * amt, 2), "pnl": round(pnl, 2), "gain_pct": round(gain_pct, 2), "date_acquired": (pos.get("opened_at") or "")[:10], "last_updated": (pos.get("mark_price_last_updated") or pos.get("last_updated") or "")})
    result["positions"] = sorted(positions, key=lambda x: abs(x["mkt_val"]), reverse=True)
    open_pl = sum(p["pnl"] for p in positions)
    long_val = sum(p["mkt_val"] for p in positions if p["side"] == "LONG")
    short_val = sum(p["mkt_val"] for p in positions if p["side"] == "SHORT")
    result["balance"] = {"total_equity": 0, "total_cash": 0, "open_pl": round(open_pl, 2), "close_pl": 0, "stock_long_value": round(long_val, 2), "short_market_value": round(-short_val, 2), "option_long_value": 0}
    return result


# ── News fetchers ────────────────────────────────────────────────────────────

def fetch_rss(url, timeout=6):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tree = ElementTree.parse(resp)
            items = tree.findall(".//item")
            results = []
            for item in items:
                title = item.find("title")
                pub = item.find("pubDate")
                link = item.find("link")
                if title is not None and title.text:
                    results.append({"title": title.text.strip(), "date": pub.text.strip() if pub is not None else "", "url": link.text.strip() if link is not None else ""})
            return results
    except Exception:
        return []


def get_market_headlines():
    our_symbols = get_relevant_symbols()
    headlines = []
    for url, source in [("https://www.investing.com/rss/news.rss", "Investing.com"), ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC"), ("https://fortune.com/feed/", "Fortune"), ("https://www.benzinga.com/feed", "Benzinga")]:
        for item in fetch_rss(url)[:10]:
            item["source"] = source
            title_upper = item["title"].upper()
            item["_relevant"] = any(f" {sym} " in f" {title_upper} " or title_upper.startswith(f"{sym} ") or title_upper.endswith(f" {sym}") or f"({sym})" in title_upper for sym in our_symbols)
            headlines.append(item)
    seen = set()
    relevant = []
    general = []
    for h in headlines:
        k = h["title"][:50]
        if k in seen:
            continue
        seen.add(k)
        if h.get("_relevant"):
            relevant.append(h)
        else:
            general.append(h)
    return relevant[:6] + general[:max(2, 8 - len(relevant[:6]))]


def get_relevant_symbols():
    """Get tradeable symbols from symbols_trb_long.json + symbols_trb_short.json."""
    syms = set()
    for fname in ("symbols_trb_long.json", "symbols_trb_short.json"):
        try:
            d = json.loads((BASE / fname).read_text())
            if isinstance(d, list):
                syms.update(s.upper() for s in d if isinstance(s, str))
        except Exception:
            pass
    return syms


def get_ticker_news(symbols):
    """Fetch per-ticker news from Google News RSS for open positions only."""
    all_news = {}
    for sym in symbols[:15]:
        url = f"https://news.google.com/rss/search?q={sym}+stock&hl=en-US&gl=US&ceid=US:en"
        items = fetch_rss(url, timeout=5)
        if items:
            all_news[sym] = items[:2]
    return all_news


def load_news_scanner_data():
    """Load all ez_news_scanner output: sentiment, picks, paper trades, macro events."""
    result = {"stock_sentiment": {}, "crypto_sentiment": {}, "daily_picks": {}, "paper_trades": {}, "paper_summary": {}, "meta": {}, "macro_events": [], "log_summary": []}
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
        stocks = r.get("news_sentiment_stocks")
        if stocks:
            result["stock_sentiment"] = json.loads(stocks)
        crypto = r.get("news_sentiment_crypto")
        if crypto:
            result["crypto_sentiment"] = json.loads(crypto)
        meta = r.get("news_sentiment_meta")
        if meta:
            result["meta"] = json.loads(meta)
    except Exception:
        pass
    picks_path = DATA_DIR / "news_daily_picks.json"
    if picks_path.exists():
        try:
            with open(picks_path) as f:
                result["daily_picks"] = json.load(f)
        except Exception:
            pass
    paper_path = DATA_DIR / "news_paper_trades.json"
    if paper_path.exists():
        try:
            with open(paper_path) as f:
                result["paper_trades"] = json.load(f)
        except Exception:
            pass
    summary_path = DATA_DIR / "news_paper_summary.json"
    if summary_path.exists():
        try:
            with open(summary_path) as f:
                result["paper_summary"] = json.load(f)
        except Exception:
            pass
    log_path = Path("/Users/niels/logs/ez_news_scanner.log")
    if log_path.exists():
        try:
            lines = log_path.read_text().strip().split("\n")[-200:]
            macro = []
            for line in lines:
                if "MACRO_DETECT" in line:
                    macro.append(line.split("MACRO_DETECT] ")[-1] if "MACRO_DETECT] " in line else line)
                elif "INJECT" in line and "symbol" in line.lower():
                    result["log_summary"].append(line.split(" - ")[-1].strip() if " - " in line else line.strip())
            if macro:
                result["macro_events"] = [macro[-1]]
        except Exception:
            pass
    return result


def build_news_scanner_section():
    """Build news scanner intelligence section."""
    ns = load_news_scanner_data()
    html = ""
    meta = ns.get("meta", {})
    if meta:
        last_poll = meta.get("last_poll", "?")
        sources = meta.get("sources", "?")
        fg = meta.get("fear_greed", {})
        html += f'<div class="box"><b>Scanner status:</b> Last poll {last_poll[:19] if last_poll != "?" else "?"} UTC &nbsp; Sources: {sources}'
        if fg:
            fg_val = fg.get("value", "?")
            fg_cls = fg.get("classification", "?")
            fp = "pr" if isinstance(fg_val, int) and fg_val < 25 else "po" if isinstance(fg_val, int) and fg_val < 45 else "pg"
            html += f' &nbsp; <span class="pill {fp}">Crypto F&G: {fg_val} — {fg_cls}</span>'
        html += "</div>"
    for evt in ns.get("macro_events", []):
        html += f'<div class="news" style="border-left-color:#c62828"><b>Macro Alert:</b> {evt}</div>'
    stock_sent = ns.get("stock_sentiment", {})
    if stock_sent:
        bullish = sorted([(s, sc) for s, sc in stock_sent.items() if sc > 0.2], key=lambda x: x[1], reverse=True)
        bearish = sorted([(s, sc) for s, sc in stock_sent.items() if sc < -0.2], key=lambda x: x[1])
        html += "<h3>Stock Sentiment (from Finnhub + RSS + AlphaVantage)</h3><table>"
        html += "<tr><th>Symbol</th><th>Sentiment</th><th>Score</th><th>Signal</th></tr>"
        for sym, score in bullish[:8]:
            strength = "STRONG BUY" if score > 0.6 else "BULLISH"
            pill = "pg"
            html += f'<tr><td><b>{sym}</b></td><td><span class="pill {pill}">{strength}</span></td><td style="text-align:right" class="g b">{score:+.3f}</td><td>News positive</td></tr>'
        for sym, score in bearish[:5]:
            strength = "STRONG SELL" if score < -0.5 else "BEARISH"
            pill = "pr"
            html += f'<tr><td><b>{sym}</b></td><td><span class="pill {pill}">{strength}</span></td><td style="text-align:right" class="r b">{score:+.3f}</td><td>News negative</td></tr>'
        html += "</table>"
    crypto_sent = ns.get("crypto_sentiment", {})
    if crypto_sent:
        items = sorted(crypto_sent.items(), key=lambda x: abs(x[1]), reverse=True)
        html += "<h3>Crypto Sentiment</h3><table>"
        html += "<tr><th>Symbol</th><th>Score</th><th>Signal</th></tr>"
        for sym, score in items[:8]:
            c = "g" if score > 0.2 else "r" if score < -0.2 else "gr"
            label = "BULLISH" if score > 0.2 else "BEARISH" if score < -0.2 else "NEUTRAL"
            html += f'<tr><td><b>{sym}</b></td><td style="text-align:right" class="{c} b">{score:+.3f}</td><td>{label}</td></tr>'
        html += "</table>"
    picks = ns.get("daily_picks", {})
    if picks:
        picks_date = picks.get("date", "?")
        try:
            pd = datetime.strptime(picks_date, "%Y-%m-%d")
            age_days = (datetime.now() - pd).days
        except Exception:
            age_days = 999
        if age_days <= 7:
            stock_syms = []
            for section in ["stock_long", "stock_short"]:
                for item in picks.get(section, []):
                    sym = item.get("symbol", "")
                    if sym:
                        stock_syms.append(sym)
            pick_prices = {}
            if stock_syms:
                try:
                    from config_tradier import TradierConfig
                    from tradier_api import TradierAPIClient
                    cfg = TradierConfig()
                    client = TradierAPIClient(config=cfg, account_key="trb")
                    loop = asyncio.new_event_loop()
                    quotes = loop.run_until_complete(client.get_quotes(stock_syms[:15]))
                    for sym in stock_syms:
                        q = quotes.get(sym, {})
                        now_price = q.get("last") or q.get("close", 0)
                        if now_price:
                            hist = loop.run_until_complete(client.get_history(sym, start=picks_date, end=picks_date))
                            then_price = hist[0].get("close", 0) if hist else 0
                            pick_prices[sym] = {"now": now_price, "then": then_price}
                    loop.close()
                except Exception:
                    pass
            html += f'<h3>News Picks Scorecard — {picks_date} ({age_days}d ago)</h3>'
            html += '<table><tr><th>Signal</th><th>Symbol</th><th>Reason</th><th>Then</th><th>Now</th><th>Move</th></tr>'
            for section, label in [("stock_long", "BUY"), ("stock_short", "SELL")]:
                for item in picks.get(section, []):
                    sym = item.get("symbol", "")
                    reason = item.get("reason", "")[:70]
                    pill = "pg" if "long" in section.lower() else "pr"
                    pp = pick_prices.get(sym, {})
                    then = pp.get("then", 0)
                    now_p = pp.get("now", 0)
                    then_str = f"${then:.2f}" if then else "—"
                    now_str = f"${now_p:.2f}" if now_p else "—"
                    if then > 0 and now_p > 0:
                        move = (now_p - then) / then * 100
                        mc = "g" if (move > 0 and "long" in section) or (move < 0 and "short" in section) else "r"
                        correct = "right" if mc == "g" else "wrong"
                        move_str = f'<span class="{mc} b">{move:+.1f}% ({correct})</span>'
                    else:
                        move_str = "—"
                    html += f'<tr><td><span class="pill {pill}">{label}</span></td><td><b>{sym}</b></td><td style="font-size:11px">{reason}</td><td style="text-align:right">{then_str}</td><td style="text-align:right">{now_str}</td><td style="text-align:right">{move_str}</td></tr>'
            for section, label in [("crypto_long", "LONG"), ("crypto_short", "SHORT")]:
                for item in picks.get(section, []):
                    sym = item.get("symbol", "")
                    reason = item.get("reason", "")[:70]
                    pill = "pg" if "long" in section.lower() else "pr"
                    html += f'<tr><td><span class="pill {pill}">{label}</span></td><td><b>{sym}</b></td><td style="font-size:11px">{reason}</td><td style="text-align:right">—</td><td style="text-align:right">—</td><td style="text-align:right" class="gr">crypto</td></tr>'
            html += "</table>"
    paper = ns.get("paper_trades", {})
    paper_sum = ns.get("paper_summary", {})
    open_trades = paper.get("open", [])
    if open_trades or paper_sum:
        html += "<h3>Paper Trading Performance</h3>"
        if paper_sum:
            wins = paper_sum.get("wins", 0)
            losses = paper_sum.get("losses", 0)
            wr = paper_sum.get("win_rate_pct", 0)
            total_pnl = paper_sum.get("total_pnl_pct", 0)
            open_pnl = paper_sum.get("open_unrealized_pnl_pct", 0)
            html += f'<div class="box">Closed: {wins}W/{losses}L (WR: {wr}%) | Closed PnL: {total_pnl:+.1f}% | Open PnL: {open_pnl:+.2f}%</div>'
        if open_trades:
            html += "<table><tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Current</th><th>PnL%</th><th>Sentiment</th></tr>"
            for t in open_trades:
                c = "g" if t.get("unrealized_pnl_pct", 0) > 0 else "r"
                sp = "pg" if t.get("side") == "LONG" else "pr"
                html += f'<tr><td><b>{t["symbol"]}</b></td><td><span class="pill {sp}">{t.get("side","?")}</span></td><td style="text-align:right">${t.get("entry_price",0):.4f}</td><td style="text-align:right">${t.get("current_price",0):.4f}</td><td style="text-align:right" class="{c} b">{t.get("unrealized_pnl_pct",0):+.2f}%</td><td style="text-align:right">{t.get("sentiment_score",0):+.3f}</td></tr>'
            html += "</table>"
    if not stock_sent and not crypto_sent and not picks:
        html = '<p class="gr">News scanner not producing data. Check if ez_news_scanner.py is running.</p>'
    return html


def get_fear_greed():
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
        meta = r.get("news_sentiment_meta")
        if meta:
            d = json.loads(meta)
            fg = d.get("fear_greed", {})
            return fg.get("value", "?"), fg.get("classification", "Unknown")
    except Exception:
        pass
    try:
        req = urllib.request.Request("https://api.alternative.me/fng/?limit=1", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read())
            return int(d["data"][0]["value"]), d["data"][0]["value_classification"]
    except Exception:
        return "?", "Unknown"


# ── Section builders ─────────────────────────────────────────────────────────

def build_market_section(market_quotes):
    fg_val, fg_cls = get_fear_greed()
    html = '<div class="box">'
    if fg_val != "?":
        fg_pill = "pr" if int(fg_val) < 25 else "po" if int(fg_val) < 45 else "pg" if int(fg_val) > 55 else "pb"
        html += f'<span class="pill {fg_pill}">Fear & Greed: {fg_val} — {fg_cls}</span>&nbsp;&nbsp;'
    for sym in ["SPY", "QQQ", "DIA", "IWM"]:
        q = market_quotes.get(sym, {})
        last = q.get("last") or q.get("close", 0)
        prev = q.get("prevclose") or q.get("previous_close", 0)
        if last and prev:
            chg = (last - prev) / prev * 100
            c = "g" if chg > 0 else "r"
            html += f'<b>{sym}</b> ${last:.2f} <span class="{c}">({chg:+.2f}%)</span>&nbsp;&nbsp;&nbsp;'
    vix = market_quotes.get("VIX", {})
    vix_last = vix.get("last") or vix.get("close", 0)
    if vix_last:
        vc = "r" if vix_last > 25 else "o" if vix_last > 18 else "g"
        html += f'<b>VIX</b> <span class="{vc}">{vix_last:.1f}</span>'
    html += "</div>"
    spy = market_quotes.get("SPY", {})
    spy_last = spy.get("last") or spy.get("close", 0)
    spy_prev = spy.get("prevclose") or spy.get("previous_close", 0)
    if spy_last and spy_prev:
        gap = (spy_last - spy_prev) / spy_prev * 100
        if abs(gap) > 0.3:
            direction = "UP" if gap > 0 else "DOWN"
            html += f'<p><b>Pre-market direction:</b> SPY gap <span class="{"g" if gap > 0 else "r"} b">{direction} {abs(gap):.2f}%</span></p>'
    headlines = get_market_headlines()
    if headlines:
        html += "<h3>Market Headlines</h3>"
        for h in headlines:
            link = h.get("url", "")
            title_html = f'<a href="{link}" style="color:#1a1a2e;text-decoration:none">{h["title"]}</a>' if link else h["title"]
            tag = ' <span class="pill pb">OUR STOCK</span>' if h.get("_relevant") else ""
            html += f'<div class="news">{title_html}{tag} <span class="gr">— {h["source"]}</span></div>'
    return html


def build_account_section(data):
    acct = data["account_key"]
    positions = data["positions"]
    balance = data["balance"]
    option_positions = data.get("option_positions", [])
    if not positions and not option_positions:
        return '<p class="gr">No active positions.</p>'
    html = ""
    total_equity = balance.get("total_equity", 0)
    total_cash = balance.get("total_cash", 0)
    open_pl = balance.get("open_pl", 0)
    close_pl = balance.get("close_pl", 0)
    stock_long = balance.get("stock_long_value", 0)
    stock_short = abs(balance.get("short_market_value", 0))
    opt_long = balance.get("option_long_value", 0)
    pl_color = "g" if open_pl >= 0 else "r"
    html += '<div class="box">'
    html += f'<b>Open P/L:</b> <span class="{pl_color} b">${open_pl:+,.0f}</span> &nbsp; '
    html += f'Long exposure: ${stock_long:,.0f} &nbsp; Short exposure: ${stock_short:,.0f} &nbsp; '
    html += f'<b>Positions:</b> {len(positions)} stocks'
    if option_positions:
        html += f' + {len(option_positions)} options'
    html += "</div>"
    if positions:
        syms = [p["symbol"] for p in positions]
        ticker_news = get_ticker_news(syms[:12])
        if ticker_news:
            html += "<h3>News for Open Positions</h3>"
            for sym, articles in ticker_news.items():
                for a in articles[:1]:
                    link = a.get("url", "")
                    title_html = f'<a href="{link}" style="color:#1a1a2e;text-decoration:none">{a["title"]}</a>' if link else a["title"]
                    html += f'<div class="news"><b>{sym}:</b> {title_html}</div>'
        html += "<table>"
        html += '<tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Avg Cost</th><th>Last</th><th>Day %</th><th>Gain %</th><th>P/L $</th><th>Value</th></tr>'
        long_val = sum(p["mkt_val"] for p in positions if p["side"] == "LONG")
        short_val = sum(p["mkt_val"] for p in positions if p["side"] == "SHORT")
        total_pnl = sum(p["pnl"] for p in positions)
        for p in positions:
            gc = "g" if p["gain_pct"] > 0 else "r" if p["gain_pct"] < -1 else "o"
            dc = "g" if p["day_chg"] > 0 else "r" if p["day_chg"] < 0 else "gr"
            sp = "pg" if p["side"] == "LONG" else "pr"
            html += f'<tr><td><b>{p["symbol"]}</b></td><td><span class="pill {sp}">{p["side"]}</span></td><td style="text-align:right">{p["qty"]:.0f}</td><td style="text-align:right">${p["avg_cost"]:.2f}</td><td style="text-align:right">${p["last"]:.2f}</td><td style="text-align:right" class="{dc}">{p["day_chg"]:+.2f}%</td><td style="text-align:right" class="{gc} b">{p["gain_pct"]:+.2f}%</td><td style="text-align:right" class="{gc}">${p["pnl"]:+,.0f}</td><td style="text-align:right">${p["mkt_val"]:,.0f}</td></tr>'
        tc = "g" if total_pnl >= 0 else "r"
        html += f'<tr style="background:#e8eaf6;font-weight:700"><td>TOTAL</td><td></td><td></td><td></td><td></td><td></td><td></td><td style="text-align:right" class="{tc}">${total_pnl:+,.0f}</td><td style="text-align:right">L:${long_val:,.0f} S:${short_val:,.0f}</td></tr>'
        html += "</table>"
    if option_positions:
        # Parse OCC symbols to determine put/call ratio
        opt_calls = []
        opt_puts = []
        for op in option_positions:
            occ = op.get("symbol", "")
            qty = abs(float(op.get("quantity", 0)))
            cost = abs(float(op.get("cost_basis", 0)))
            # OCC format: SYMBOL + YYMMDD + C/P + strike*1000
            is_put = len(occ) > 6 and "P" in occ[6:]
            if is_put:
                opt_puts.append({"occ": occ, "qty": qty, "cost": cost})
            else:
                opt_calls.append({"occ": occ, "qty": qty, "cost": cost})
        call_val = sum(o["cost"] for o in opt_calls)
        put_val = sum(o["cost"] for o in opt_puts)
        call_qty = sum(o["qty"] for o in opt_calls)
        put_qty = sum(o["qty"] for o in opt_puts)
        total_val = call_val + put_val
        ratio_str = f'{call_qty:.0f}C : {put_qty:.0f}P'
        ratio_color = "g" if 0.3 <= (put_qty / (call_qty + put_qty + 0.001)) <= 0.7 else "o"
        html += f'<h3>Open Options &mdash; <span class="{ratio_color} b">{ratio_str}</span> &nbsp; Calls ${call_val:,.0f} / Puts ${put_val:,.0f} / Total ${total_val:,.0f}</h3><table>'
        html += "<tr><th>Contract</th><th>Type</th><th>Qty</th><th>Cost Basis</th></tr>"
        for op in option_positions:
            occ = op.get("symbol", "")
            is_put = len(occ) > 6 and "P" in occ[6:]
            pill = "pr" if is_put else "pg"
            typ = "PUT" if is_put else "CALL"
            html += f'<tr><td>{occ}</td><td><span class="pill {pill}">{typ}</span></td><td style="text-align:right">{op.get("quantity",0)}</td><td style="text-align:right">${abs(float(op.get("cost_basis",0))):,.0f}</td></tr>'
        html += "</table>"
    pending = data.get("pending_orders", [])
    if pending:
        html += f'<h3>Pending Orders ({len(pending)})</h3><table>'
        html += "<tr><th>ID</th><th>Side</th><th>Contract</th><th>Qty</th><th>Limit $</th><th>Status</th></tr>"
        for o in pending:
            pill = "pg" if "buy" in str(o.get("side", "")).lower() else "pr"
            html += f'<tr><td class="gr">{o["id"]}</td><td><span class="pill {pill}">{o["side"]}</span></td><td><b>{o["symbol"]}</b></td><td style="text-align:right">{o["qty"]}</td><td style="text-align:right">${float(o.get("price",0)):.2f}</td><td>{o["status"]}</td></tr>'
        html += "</table>"
    return html


def build_sync_health(tra_data, trb_data):
    """Position file health check — mark price freshness and position counts. No API calls."""
    now_utc = datetime.now(timezone.utc)
    all_stale = []
    rows = []
    for data in [tra_data, trb_data]:
        acct = data["account_key"]
        for p in data.get("positions", []):
            lu = p.get("last_updated", "")
            age_min = None
            if lu:
                try:
                    dt = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                    age_min = int((now_utc - dt).total_seconds() // 60)
                except Exception:
                    pass
            stale = age_min is not None and age_min > 60
            if stale:
                all_stale.append(f"{acct}:{p['symbol']} mark {age_min}m old")
            rows.append({"acct": acct, "sym": p["symbol"], "side": p["side"], "qty": p["qty"], "entry": p["avg_cost"], "mark": p["last"], "gain_pct": p["gain_pct"], "pnl": p["pnl"], "age_min": age_min, "stale": stale})
    is_synced = len(all_stale) == 0
    total = len(rows)
    stale_count = len(all_stale)
    if is_synced:
        html = f'<div class="box" style="border-left:4px solid #2e7d32"><span class="pill pg">FILES OK</span> {total} positions loaded from local files. Mark prices current. <span class="gr" style="font-size:11px">(API sync disabled — avoids ban risk; wrong data visible here = tradier_manage.py is wrong too)</span></div>'
    else:
        html = f'<div class="box" style="border-left:4px solid #ef6c00;background:#fff8e1"><span class="pill po">STALE MARKS — {stale_count} positions</span> Mark prices &gt;60min old — tradier_manage.py may be trading on stale prices.<br>'
        for s in all_stale[:5]:
            html += f'<span class="r" style="font-size:11px">{s}</span><br>'
        html += '</div>'
    html += '<table style="font-size:11px"><tr><th>Acct</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry $</th><th>Mark $</th><th>Gain %</th><th>P/L $</th><th>Mark Age</th></tr>'
    for r in sorted(rows, key=lambda x: abs(x["pnl"]), reverse=True):
        gc = "g" if r["gain_pct"] > 0 else "r" if r["gain_pct"] < -1 else "o"
        sp = "pg" if r["side"] == "LONG" else "pr"
        age_str = f'{r["age_min"]}m' if r["age_min"] is not None else "?"
        age_cls = "r" if r.get("stale") else "gr"
        html += f'<tr><td>{r["acct"]}</td><td><b>{r["sym"]}</b></td><td><span class="pill {sp}">{r["side"]}</span></td><td style="text-align:right">{r["qty"]:.0f}</td><td style="text-align:right">${r["entry"]:.2f}</td><td style="text-align:right">${r["mark"]:.2f}</td><td style="text-align:right" class="{gc} b">{r["gain_pct"]:+.2f}%</td><td style="text-align:right" class="{gc}">${r["pnl"]:+,.0f}</td><td style="text-align:right" class="{age_cls}">{age_str}</td></tr>'
    html += "</table>"
    return html, is_synced


def build_tv_bias_section():
    """WaveTrend bias assessment from data/tv_morning_brief.json (written by tv_morning_brief.py at 9:25 AM ET)."""
    brief_path = DATA_DIR / "tv_morning_brief.json"
    if not brief_path.exists():
        return '<p class="gr">TV bias snapshot not available — runs at 9:25 AM ET via launchd.</p>'
    try:
        brief = json.loads(brief_path.read_text())
        gen_at = brief.get("generated_at", "")
        try:
            dt = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
            age_min = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
        except Exception:
            age_min = 999
        if age_min > 180:
            return f'<p class="o">TV bias snapshot is {age_min}m old (data_age={brief.get("data_age_minutes","?")}min) — script may not have run today.</p>'

        summary = brief.get("summary", "")
        data_age = brief.get("data_age_minutes", "?")
        html = f'<p class="gr" style="font-size:11px">Generated {gen_at[:16]} UTC &bull; indicator data {data_age}min old &bull; {summary}</p>'

        mkt = brief.get("market_bias", "")
        if mkt:
            cls = "pg" if mkt == "LONG" else "pr" if mkt == "SHORT" else "po"
            html += f'<span class="pill {cls}">{mkt} bias</span>&nbsp;&nbsp;'

        def tf_icons(tf_bias):
            icons = []
            for tf in ["1h", "4h", "D"]:
                v = tf_bias.get(tf, "?")
                if v == "BULL":
                    icons.append(f'<span style="color:#2e7d32;font-weight:700">{tf}&#9650;</span>')
                elif v == "BEAR":
                    icons.append(f'<span style="color:#c62828;font-weight:700">{tf}&#9660;</span>')
                else:
                    icons.append(f'<span style="color:#888">{tf}?</span>')
            return "&nbsp;".join(icons)

        def assessment_row(sym, a, pill_cls):
            conf = a.get("confidence", "?")
            conf_cls = "pg" if conf == "HIGH" else "po" if conf == "MEDIUM" else "pr"
            tfs_html = tf_icons(a.get("tf_bias", {}))
            note = a.get("note", "")
            caution_str = a.get("cautions", [""])[0] if a.get("cautions") else ""
            rec = a.get("recommendation", "?")
            return (
                f'<tr>'
                f'<td><b>{sym}</b></td>'
                f'<td><span class="pill {pill_cls}">{rec}</span></td>'
                f'<td><span class="pill {conf_cls}">{conf}</span></td>'
                f'<td style="white-space:nowrap">{tfs_html}</td>'
                f'<td class="gr" style="font-size:11px">{note}</td>'
                f'<td class="o" style="font-size:11px">{caution_str}</td>'
                f'</tr>'
            )

        assessments = brief.get("assessments", {})

        # Collect currently held symbols from trb position files + options,
        # keyed to the side actually held (assessments are now stored per-side
        # since ~16 symbols appear in both the long and short trb universes —
        # a flat assessments[sym] would silently show the wrong side's verdict).
        held_syms = {}
        try:
            for side in ("long", "short"):
                pos_path = BASE / "trb" / f"{side}_positions.json"
                if pos_path.exists():
                    for key, v in json.loads(pos_path.read_text()).items():
                        if float(v.get("positionAmt", 0) or 0) > 0:
                            sym = v.get("symbol") or key.rsplit("_", 1)[0].split(":")[-1]
                            if sym:
                                held_syms[sym] = side
            opts_path = BASE / "trb" / "options_positions.json"
            if opts_path.exists():
                for p in json.loads(opts_path.read_text()).get("positions", []):
                    if p.get("qty", 0) > 0 and p.get("underlying"):
                        held_syms[p["underlying"]] = "short" if p.get("option_type") == "PUT" else "long"
        except Exception:
            pass

        tbl_header = '<tr><th>Symbol</th><th>Signal</th><th>Confidence</th><th>TF Alignment</th><th>Assessment</th><th>Caution</th></tr>'

        # ── Section 1: Open positions first ──────────────────────────────
        held_with_data = [(s, assessments[s][held_syms[s]]) for s in sorted(held_syms) if s in assessments and held_syms[s] in assessments[s]]
        if held_with_data:
            html += '<p class="b" style="margin-top:12px">Open positions — WT bias:</p><table>' + tbl_header
            for sym, a in held_with_data:
                rec = a.get("recommendation", "?")
                pill = "pg" if rec == "BUY" else "pr" if rec in ("SHORT", "AVOID") else "po"
                html += assessment_row(sym, a, pill)
            html += "</table>"

        # ── Section 2: Top HIGH-confidence picks from the trb universe ───
        for label, list_key, side_key, pill_cls in (
            ("High-confidence BUY setups (trb long universe)", "top_buys", "long", "pg"),
            ("High-confidence SHORT setups (trb short universe)", "top_shorts", "short", "pr"),
        ):
            top = brief.get(list_key, [])
            high_conf = [
                item for item in top
                if (item.get("confidence") if isinstance(item, dict) else
                    assessments.get(item, {}).get(side_key, {}).get("confidence")) == "HIGH"
                and (item.get("symbol") if isinstance(item, dict) else item) not in held_syms
            ]
            if not high_conf:
                continue
            html += f'<p class="b" style="margin-top:12px">{label}:</p><table>' + tbl_header
            for item in high_conf[:8]:
                sym = item.get("symbol", "?") if isinstance(item, dict) else item
                a = assessments.get(sym, {}).get(side_key, {})
                html += assessment_row(sym, a, pill_cls)
            html += "</table>"

        return html
    except Exception as e:
        return f'<p class="r">TV bias section error: {e}</p>'


def build_premarket_section():
    """Pre-market scanner candidates (runs 8:00 AM ET) + yesterday's daily performance report."""
    html = ""
    scan_path = TRADIER_DIR / "premarket_scan_latest.json"
    if scan_path.exists():
        try:
            scan = json.loads(scan_path.read_text())
            ts = scan.get("timestamp", "")[:16]
            universe = scan.get("universe_size", 0)
            tradier_syms = get_relevant_symbols()
            longs = [c for c in scan.get("long_candidates", []) if c["symbol"] in tradier_syms] if tradier_syms else scan.get("long_candidates", [])
            shorts = [c for c in scan.get("short_candidates", []) if c["symbol"] in tradier_syms] if tradier_syms else scan.get("short_candidates", [])
            html += f'<h3>Pre-Market Scanner ({ts} UTC, {universe} symbols scanned)</h3>'
            if longs:
                html += '<p class="b">Top Long Candidates (oversold + mean-reversion setup):</p><table>'
                html += '<tr><th>Symbol</th><th>Score</th><th>MFI 15m</th><th>Stoch K</th><th>BB %B</th><th>HA</th><th>RVOL</th><th>10d Ret</th></tr>'
                for c in longs[:10]:
                    rc = "g" if c.get("ret_10d", 0) > 0 else "r"
                    html += f'<tr><td><b>{c["symbol"]}</b></td><td style="text-align:center" class="b">{c["score"]:.0f}</td><td style="text-align:right">{c.get("mfi_15m",0):.0f}</td><td style="text-align:right">{c.get("k_15m",0):.0f}</td><td style="text-align:right">{c.get("bb_1h",0):.2f}</td><td style="text-align:center">{c.get("ha","?")}</td><td style="text-align:right">{c.get("rvol",0):.2f}</td><td style="text-align:right" class="{rc}">{c.get("ret_10d",0):+.1f}%</td></tr>'
                html += "</table>"
            if shorts:
                html += '<p class="b">Top Short Candidates (overbought + reversal setup):</p><table>'
                html += '<tr><th>Symbol</th><th>Score</th><th>MFI 15m</th><th>Stoch K</th><th>BB %B</th><th>HA</th><th>RVOL</th><th>10d Ret</th></tr>'
                for c in shorts[:10]:
                    rc = "r" if c.get("ret_10d", 0) > 0 else "g"
                    html += f'<tr><td><b>{c["symbol"]}</b></td><td style="text-align:center" class="b">{c["score"]:.0f}</td><td style="text-align:right">{c.get("mfi_15m",0):.0f}</td><td style="text-align:right">{c.get("k_15m",0):.0f}</td><td style="text-align:right">{c.get("bb_1h",0):.2f}</td><td style="text-align:center">{c.get("ha","?")}</td><td style="text-align:right">{c.get("rvol",0):.2f}</td><td style="text-align:right" class="{rc}">{c.get("ret_10d",0):+.1f}%</td></tr>'
                html += "</table>"
        except Exception:
            pass
    report_dir = DATA_DIR / "daily_reports"
    if report_dir.exists():
        try:
            reports = sorted(report_dir.glob("report_*.txt"), reverse=True)
            if reports:
                report_text = reports[0].read_text()
                report_date = reports[0].stem.replace("report_", "")
                stock_lines = []
                in_stock = False
                for line in report_text.split("\n"):
                    if "STOCK ACCOUNTS" in line:
                        in_stock = True
                        continue
                    if "SYSTEM CHANGES" in line:
                        break
                    if in_stock and line.strip():
                        stock_lines.append(line.strip())
                if stock_lines:
                    html += f'<h3>Yesterday\'s Performance ({report_date[:4]}-{report_date[4:6]}-{report_date[6:]})</h3>'
                    html += '<div class="box" style="font-size:11px;font-family:monospace;white-space:pre-wrap">'
                    for line in stock_lines[:15]:
                        line = line.replace("$", "&#36;")
                        html += line + "\n"
                    html += "</div>"
                crypto_summary = ""
                for line in report_text.split("\n"):
                    if "CRYPTO TOTAL" in line:
                        crypto_summary = line.strip()
                        break
                if crypto_summary:
                    html += f'<p style="font-size:11px;font-family:monospace">{crypto_summary.replace("$", "&#36;")}</p>'
        except Exception:
            pass
    if not html:
        html = '<p class="gr">No pre-market scanner or daily report data available.</p>'
    return html


# Symbol → sector grouping for hedge analysis
_SECTOR_MAP = {
    # Gold / Precious Metals
    "GLD": "Gold/Metals", "GDX": "Gold/Metals", "IAU": "Gold/Metals",
    "NEM": "Gold/Metals", "AEM": "Gold/Metals", "RGLD": "Gold/Metals",
    "WPM": "Gold/Metals", "FNV": "Gold/Metals",
    # Base Metals / Mining
    "COPX": "Base Metals", "FCX": "Base Metals", "X": "Base Metals",
    "CLF": "Base Metals", "NUE": "Base Metals",
    # Energy / Oil
    "USO": "Energy/Oil", "BNO": "Energy/Oil", "XOM": "Energy/Oil",
    "CVX": "Energy/Oil", "UNG": "Energy/Gas", "XLE": "Energy/Oil",
    "OXY": "Energy/Oil",
    # Crypto / Bitcoin
    "IBIT": "Crypto", "MSTR": "Crypto", "COIN": "Crypto",
    "MARA": "Crypto", "CLSK": "Crypto", "BITO": "Crypto", "RIOT": "Crypto",
    # Big Tech
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "AMD": "Tech",
    "AVGO": "Tech", "META": "Tech", "GOOGL": "Tech", "AMZN": "Tech",
    "TSLA": "Tech", "ORCL": "Tech", "CRM": "Tech",
    # Growth Tech / Software
    "PLTR": "Growth Tech", "SNDK": "Growth Tech", "WDAY": "Growth Tech",
    "NOW": "Growth Tech", "SNOW": "Growth Tech", "DDOG": "Growth Tech",
    "ZS": "Growth Tech", "CRWD": "Growth Tech", "PANW": "Growth Tech",
    "TTD": "Growth Tech", "FIVN": "Growth Tech",
    # Fintech / Payments
    "PYPL": "Fintech", "SQ": "Fintech", "V": "Fintech", "MA": "Fintech",
    "SOFI": "Fintech", "AFRM": "Fintech",
    # Consumer / Retail
    "NKE": "Consumer", "LULU": "Consumer", "ASTS": "Consumer",
    # Healthcare / Biotech
    "LLY": "Healthcare", "UNH": "Healthcare", "MRNA": "Healthcare",
    "ABBV": "Healthcare", "JNJ": "Healthcare",
    # ETFs / Broad
    "SPY": "Broad Market", "QQQ": "Broad Market", "IWM": "Broad Market",
    "XLK": "Broad Market", "XLF": "Financials", "XLV": "Healthcare",
}


def _parse_occ_simple(sym: str):
    """Minimal OCC parser: returns dict with symbol/option_type/strike/expiration or None."""
    import re
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', sym)
    if not m:
        return None
    underlying = m.group(1)
    exp_raw = m.group(2)
    otype = "call" if m.group(3) == "C" else "put"
    strike = int(m.group(4)) / 1000.0
    exp_str = f"20{exp_raw[:2]}-{exp_raw[2:4]}-{exp_raw[4:]}"
    return {"symbol": underlying, "option_type": otype, "strike": strike, "expiration": exp_str}


def build_options_positions_section():
    """Open options positions from local position files + sector hedge analysis."""
    html = ""
    accts = ["tra", "trb", "trc"]
    # ── Collect options with P/L from local position files ────────────────────
    positions = []
    for acct in accts:
        for side in ["long", "short"]:
            path = BASE / acct / f"{side}_positions.json"
            if not path.exists():
                continue
            try:
                data = json.load(open(path))
            except Exception:
                continue
            for key, pos in data.items():
                sym = pos.get("symbol", "")
                parsed = _parse_occ_simple(sym)
                if not parsed:
                    continue
                amt = float(pos.get("positionAmt", 0) or 0)
                if amt <= 0:
                    continue
                entry_price = float(pos.get("entry_price", 0) or 0)
                mark_price = float(pos.get("mark_price", 0) or 0)
                cost_per_contract = entry_price
                current_per_contract = mark_price * 100
                total_cost = cost_per_contract * amt
                total_value = current_per_contract * amt
                pnl = total_value - total_cost
                pnl_pct = (pnl / total_cost * 100) if total_cost else 0
                positions.append({
                    "account": acct,
                    "occ": sym,
                    "underlying": parsed["symbol"],
                    "option_type": parsed["option_type"],
                    "strike": parsed["strike"],
                    "expiration": parsed["expiration"],
                    "qty": int(amt),
                    "cost_per_contract": cost_per_contract,
                    "mark_price": mark_price,
                    "total_cost": total_cost,
                    "total_value": total_value,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "sector": _SECTOR_MAP.get(parsed["symbol"], "Other"),
                })
    # ── Known open options from truth file (watchdog writes this each cycle) ────
    # Do NOT use options_equity_hedges.json — it tracks hedge placements, not open
    # positions, and goes stale when options close without coordinated hedge unwind.
    truth_file = BASE / "trb" / "options_positions.json"
    truth_ts = ""
    untracked_known = []
    local_occs = {p["occ"] for p in positions}
    if truth_file.exists():
        try:
            truth_data = json.load(open(truth_file))
            truth_ts = truth_data.get("last_updated", "")[:16]
            for entry in truth_data.get("positions", []):
                occ = entry.get("occ_symbol", "")
                if not occ or occ in local_occs:
                    continue
                parsed = _parse_occ_simple(occ)
                if not parsed:
                    continue
                untracked_known.append({
                    "occ": occ,
                    "underlying": entry.get("underlying", parsed["symbol"]),
                    "option_type": entry.get("option_type", parsed["option_type"]),
                    "strike": entry.get("strike", parsed["strike"]),
                    "expiration": entry.get("expiration", parsed["expiration"]),
                    "qty": int(entry.get("qty", 1)),
                    "sector": _SECTOR_MAP.get(entry.get("underlying", parsed["symbol"]), "Other"),
                })
        except Exception:
            pass
    untracked_hedged = untracked_known  # keep variable name for rest of function
    # ── Positions table (local tracked with P/L) ──────────────────────────────
    if positions:
        total_pnl = sum(p["pnl"] for p in positions)
        total_cost = sum(p["total_cost"] for p in positions)
        pnl_cls = "g" if total_pnl >= 0 else "r"
        html += f'<p class="b">{len(positions)} tracked option(s) &mdash; Cost: ${total_cost:,.0f} &mdash; Open P/L: <span class="{pnl_cls} b">${total_pnl:+,.0f}</span></p>'
        html += '<table><tr><th>Acct</th><th>Contract</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Qty</th><th>Cost/Ct</th><th>Mark</th><th>Total Cost</th><th>P/L $</th><th>P/L %</th><th>Sector</th></tr>'
        for p in sorted(positions, key=lambda x: x["pnl_pct"], reverse=True):
            pc = "g" if p["pnl_pct"] >= 0 else "r"
            otype_pill = "pg" if p["option_type"] == "call" else "pr"
            exp_short = p["expiration"][5:] if p["expiration"] else ""
            html += (
                f'<tr><td class="b">{p["account"]}</td>'
                f'<td><b>{p["underlying"]}</b> <span class="gr" style="font-size:10px">{p["occ"]}</span></td>'
                f'<td><span class="pill {otype_pill}">{p["option_type"].upper()}</span></td>'
                f'<td style="text-align:right">${p["strike"]:.0f}</td>'
                f'<td>{exp_short}</td>'
                f'<td style="text-align:center">{p["qty"]}</td>'
                f'<td style="text-align:right">${p["cost_per_contract"]:.0f}</td>'
                f'<td style="text-align:right">${p["mark_price"]:.2f}</td>'
                f'<td style="text-align:right">${p["total_cost"]:,.0f}</td>'
                f'<td style="text-align:right" class="{pc} b">${p["pnl"]:+,.0f}</td>'
                f'<td style="text-align:right" class="{pc} b">{p["pnl_pct"]:+.1f}%</td>'
                f'<td style="font-size:11px">{p["sector"]}</td></tr>'
            )
        html += "</table>"
    else:
        html += '<p class="gr" style="font-size:11px">No options with qty&gt;0 in local position files (tradier_manage.py may have zeroed them). Known open options from truth file shown below.</p>'
    # ── Known open options from truth file (no live P/L until watchdog runs) ───
    if untracked_known:
        ts_note = f"as of {truth_ts} UTC" if truth_ts else "manually seeded"
        html += f'<h3>Known Open Options ({len(untracked_known)}) &mdash; <span class="gr" style="font-size:11px">{ts_note} — no live P/L, run watchdog for marks</span></h3>'
        html += '<table><tr><th>Contract</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Qty</th><th>Sector</th></tr>'
        for h in untracked_known:
            otype_pill = "pg" if h["option_type"] == "call" else "pr"
            exp_short = h["expiration"][5:] if h["expiration"] else ""
            html += (
                f'<tr><td><b>{h["underlying"]}</b> <span class="gr" style="font-size:10px">{h["occ"]}</span></td>'
                f'<td><span class="pill {otype_pill}">{h["option_type"].upper()}</span></td>'
                f'<td style="text-align:right">${h["strike"]:.0f}</td>'
                f'<td>{exp_short}</td>'
                f'<td style="text-align:center">{h["qty"]}</td>'
                f'<td style="font-size:11px">{h["sector"]}</td></tr>'
            )
        html += "</table>"
    # ── Sector hedge analysis — always show based on equity + known options ───
    sector_exposure: dict = {}
    def _add(sector, delta, label):
        if sector not in sector_exposure:
            sector_exposure[sector] = {"long_usd": 0, "short_usd": 0, "items": []}
        if delta >= 0:
            sector_exposure[sector]["long_usd"] += delta
        else:
            sector_exposure[sector]["short_usd"] += abs(delta)
        sector_exposure[sector]["items"].append(label)
    # Tracked options (with real notional)
    for p in positions:
        notional = p["total_value"] if p["total_value"] else p["total_cost"]
        if p["option_type"] == "call":
            _add(p["sector"], notional, f'{p["underlying"]} {p["qty"]}C')
        else:
            _add(p["sector"], -notional, f'{p["underlying"]} {p["qty"]}P')
    # Hedged options (use rough notional estimate from strike × qty × 100)
    for h in untracked_hedged:
        notional_est = h["strike"] * h["qty"] * 100 * 0.1  # rough delta-adjusted notional
        sec = h["sector"]
        if h["option_type"] == "call":
            _add(sec, notional_est, f'{h["underlying"]} {h["qty"]}C*')
        else:
            _add(sec, -notional_est, f'{h["underlying"]} {h["qty"]}P*')
    # Equity positions (long = bullish, short = bearish)
    for acct in accts:
        for side, sign in [("long", 1), ("short", -1)]:
            path = BASE / acct / f"{side}_positions.json"
            if not path.exists():
                continue
            try:
                data = json.load(open(path))
            except Exception:
                continue
            for key, pos in data.items():
                sym = pos.get("symbol", "")
                if _parse_occ_simple(sym):
                    continue
                amt = float(pos.get("positionAmt", 0) or 0)
                if amt <= 0:
                    continue
                mark = float(pos.get("mark_price", 0) or 0)
                notional = amt * mark * sign
                sec = _SECTOR_MAP.get(sym, "Other")
                label = f'{sym} {int(amt)}{"L" if sign > 0 else "S"}'
                _add(sec, notional, label)
    if not sector_exposure:
        return html
    html += "<h3>Sector Exposure (equity + options)</h3>"
    html += '<table><tr><th>Sector</th><th>Long $</th><th>Short $</th><th>Net $</th><th>Bias</th><th>Positions</th><th>Recommendation</th></tr>'
    for sector, exp in sorted(sector_exposure.items(), key=lambda x: abs(x[1]["long_usd"] - x[1]["short_usd"]), reverse=True):
        lv = exp["long_usd"]
        sv = exp["short_usd"]
        net = lv - sv
        total = lv + sv
        if total < 50:
            continue
        net_cls = "g" if net > 0 else "r"
        bias = "LONG" if net > 0 else "SHORT"
        bias_pill = "pg" if net > 0 else "pr"
        skew = abs(net) / total if total else 0
        if skew < 0.2:
            rec = '<span class="g">Balanced — no action</span>'
        elif skew < 0.5:
            side_str = "calls/longs" if net > 0 else "puts/shorts"
            rec = f'<span class="o">Mildly {bias} — consider small hedge via {side_str}</span>'
        else:
            hedge_str = "puts or short equity" if net > 0 else "calls or long equity"
            rec = f'<span class="r b">{bias} heavy ({skew:.0%}) — hedge with {hedge_str}</span>'
        items_str = ", ".join(exp["items"][:6])
        html += (
            f'<tr><td class="b">{sector}</td>'
            f'<td style="text-align:right" class="g">${lv:,.0f}</td>'
            f'<td style="text-align:right" class="r">${sv:,.0f}</td>'
            f'<td style="text-align:right" class="{net_cls} b">${net:+,.0f}</td>'
            f'<td><span class="pill {bias_pill}">{bias}</span></td>'
            f'<td style="font-size:11px">{items_str}</td>'
            f'<td style="font-size:11px">{rec}</td></tr>'
        )
    html += "</table>"
    return html


def build_options_section():
    data = load_options_data()
    if not data:
        return '<p class="gr">No options scan data. Scanner may not have run yet.</p>'
    html = ""
    if data.get("_stale"):
        html += f'<p class="o">Scan data is {data["_age_hours"]}h old.</p>'
    signals = data.get("signals", [])
    outliers = data.get("outliers", [])
    spreads = data.get("spreads", [])
    if signals:
        html += f'<p class="b">{len(signals)} directional signals:</p><table>'
        html += "<tr><th>Symbol</th><th>Direction</th><th>Conviction</th><th>Price</th><th>Key Signals</th></tr>"
        for s in signals[:12]:
            pill = "pg" if s["direction"] == "LONG" else "pr"
            arrow = "&#9650;" if s["direction"] == "LONG" else "&#9660;"
            sigs = ", ".join([k for k in s.get("signals", {}).keys() if k != "forced"][:5])
            html += f'<tr><td><b>{s["symbol"]}</b></td><td><span class="pill {pill}">{arrow} {s["direction"]}</span></td><td style="text-align:center">{s["conviction"]}/100</td><td style="text-align:right">${s["price"]:.2f}</td><td style="font-size:11px">{sigs}</td></tr>'
        html += "</table>"
    if outliers:
        html += "<h3>Top Option Outliers</h3><table>"
        html += "<tr><th>Action</th><th>Symbol</th><th>Type</th><th>Strike</th><th>Exp</th><th>Bid</th><th>Ask</th><th>Mid</th><th>IV</th><th>Edge</th><th>&#916;</th><th>OI</th><th>Score</th></tr>"
        for o in outliers[:12]:
            rec = o.get("recommendation", "")
            is_buy = "BUY" in rec
            pill = "pg" if is_buy else "pr"
            short_rec = rec.replace("BUY_CHEAP_", "BUY ").replace("BUY_UNDERPRICED_", "BUY ").replace("SELL_OVERPRICED_", "SELL ").replace("SELL_RICH_", "SELL ")
            ec = "g" if o.get("edge_pct", 0) > 0 else "r"
            bid = o.get("bid", 0)
            ask = o.get("ask", 0)
            mid = (bid + ask) / 2 if bid and ask else 0
            iv = o.get("iv_computed", 0)
            iv_pct = iv * 100 if isinstance(iv, (int, float)) and iv < 1 else iv
            html += f'<tr><td><span class="pill {pill}">{short_rec}</span></td><td><b>{o["symbol"]}</b></td><td>{o.get("type","").upper()}</td><td style="text-align:right">${o["strike"]:.0f}</td><td>{o["expiration"]}<br><span class="gr">{o["dte"]}d</span></td><td style="text-align:right">${bid:.2f}</td><td style="text-align:right">${ask:.2f}</td><td style="text-align:right" class="b">${mid:.2f}</td><td style="text-align:right">{iv_pct:.0f}%</td><td style="text-align:right" class="{ec} b">{o.get("edge_pct",0):+.1f}%</td><td style="text-align:right">{o.get("delta",0):+.3f}</td><td style="text-align:right">{o.get("open_interest",0):,}</td><td style="text-align:center" class="b">{o["score"]}</td></tr>'
        html += "</table>"
    if spreads:
        html += "<h3>Top Spreads</h3><table>"
        html += "<tr><th>Symbol</th><th>Type</th><th>Strikes</th><th>Exp</th><th>Cost</th><th>Max Profit</th><th>Max Loss</th><th>R:R</th><th>P(win)</th><th>Score</th></tr>"
        for sp in spreads[:6]:
            is_credit = sp.get("net_debit", 0) < 0
            cost_str = f'<span class="g">Cr ${abs(sp["net_debit"]):.2f}</span>' if is_credit else f'Dr ${sp["net_debit"]:.2f}'
            html += f'<tr><td><b>{sp["symbol"]}</b></td><td>{sp["type"]}</td><td>${sp["long_strike"]:.0f}/${sp["short_strike"]:.0f}</td><td>{sp["expiration"]}</td><td style="text-align:right">{cost_str}</td><td style="text-align:right" class="g">${sp["max_profit"]:.2f}</td><td style="text-align:right" class="r">${sp["max_loss"]:.2f}</td><td style="text-align:center" class="b">{sp["risk_reward"]:.1f}</td><td style="text-align:center">{sp["probability_profit"]:.0%}</td><td style="text-align:center" class="b">{sp["score"]}</td></tr>'
        html += "</table>"
    log_path = Path("/Users/niels/logs/options_agent.log") if platform.system() == "Darwin" else Path("/home/niels/logs/options_agent.log")
    if log_path.exists():
        try:
            lines = log_path.read_text().strip().split("\n")
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            summary = []
            for line in lines[-50:]:
                if today_str not in line:
                    continue
                for kw in ["No trades recommended", "EXECUTED", "Overall:", "Fear/Greed:", "Market mode:", "Total exposure:", "VIOLATION"]:
                    if kw in line:
                        clean = line.split(" - ")[-1].strip() if " - " in line else line.strip()
                        summary.append(clean)
                        break
            if summary:
                html += '<div class="news"><b>Options Agent:</b><br>' + "<br>".join(summary[:8]) + "</div>"
        except Exception:
            pass
    # ── Active GTC orders with price history (populated by gather_options_history) ──
    gtc_file = TRADIER_DIR / "options_gtc_orders.json"
    hist_file = TRADIER_DIR / "options_price_history.json"
    if gtc_file.exists():
        try:
            with open(gtc_file) as f:
                gtc = json.load(f)
            price_hist = {}
            if hist_file.exists():
                with open(hist_file) as f:
                    price_hist = json.load(f)
            if gtc:
                buys = {k: v for k, v in gtc.items() if v.get("side") == "buy_to_open"}
                sells = {k: v for k, v in gtc.items() if v.get("side") != "buy_to_open"}
                n_calls = sum(1 for v in buys.values() if v.get("type") == "call")
                n_puts = sum(1 for v in buys.values() if v.get("type") == "put")
                html += f'<h3>Active GTC Orders: {len(buys)} buys ({n_calls}C/{n_puts}P), {len(sells)} sells</h3>'
                html += '<table><tr><th>Contract</th><th>GTC$</th><th>Age</th><th>Option (5d)</th><th>Stock (5d)</th><th>Reason</th></tr>'
                now = datetime.now()
                for occ, info in sorted(buys.items()):
                    age = "?"
                    try:
                        age = f'{(now - datetime.fromisoformat(info["placed_at"])).days}d'
                    except Exception:
                        pass
                    sym = info.get("symbol", "?")
                    typ = info.get("type", "?").upper()
                    strike = info.get("strike", "?")
                    pill = "pg" if typ == "CALL" else "pr"
                    gtc_price = info.get("target_price", 0)
                    # Option price history + sparkline
                    opt_hist = price_hist.get(f"opt:{occ}", {})
                    opt_prices = opt_hist.get("closes", [])
                    opt_spark = _sparkline(opt_prices) if opt_prices else "—"
                    opt_last = f'${opt_prices[-1]:.2f}' if opt_prices else "?"
                    opt_5d = " ".join(f'${p:.2f}' for p in opt_prices[-5:]) if opt_prices else ""
                    # Equity price history + sparkline
                    eq_hist = price_hist.get(f"eq:{sym}", {})
                    eq_prices = eq_hist.get("closes", [])
                    eq_spark = _sparkline(eq_prices) if eq_prices else "—"
                    eq_last = f'${eq_prices[-1]:.2f}' if eq_prices else "?"
                    # Color the GTC price vs last option close
                    gtc_vs_last = ""
                    if opt_prices and gtc_price > 0:
                        diff_pct = (gtc_price - opt_prices[-1]) / opt_prices[-1] * 100
                        color = "g" if diff_pct < 0 else "r"
                        gtc_vs_last = f' <span class="{color}" style="font-size:10px">({diff_pct:+.0f}%)</span>'
                    html += f'<tr><td><b>{sym}</b> <span class="pill {pill}">{typ}</span> ${strike}</td>'
                    html += f'<td style="text-align:right">${gtc_price:.2f}{gtc_vs_last}</td><td>{age}</td>'
                    html += f'<td style="font-size:11px;white-space:nowrap">{opt_spark} {opt_last}<br><span class="gr">{opt_5d}</span></td>'
                    html += f'<td style="font-size:11px;white-space:nowrap">{eq_spark} {eq_last}</td>'
                    html += f'<td style="font-size:11px">{info.get("reason", "")[:40]}</td></tr>'
                html += '</table>'
                html += '<p class="gr" style="font-size:11px">Pre-market adjustment at 13:23 UTC. Sparklines show last 5 closes.</p>'
        except Exception as e:
            html += f'<p class="r">Error loading GTC data: {e}</p>'
    return html


def _sparkline(values):
    """Generate a unicode sparkline from a list of numeric values."""
    if not values or len(values) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    mn = min(values)
    mx = max(values)
    rng = mx - mn
    if rng < 0.001:
        return blocks[4] * len(values)
    result = ""
    for v in values[-8:]:
        idx = int((v - mn) / rng * 7)
        idx = max(0, min(7, idx))
        result += blocks[idx]
    # Color: green if last > first, red if last < first
    trend = "g" if values[-1] >= values[0] else "r"
    return f'<span class="{trend}" style="font-size:14px;letter-spacing:1px">{result}</span>'


def load_options_data():
    path = TRADIER_DIR / "options_analysis_latest.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        ts = data.get("timestamp", "")
        if ts:
            try:
                scan_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if "Z" in ts else datetime.fromisoformat(ts)
                if scan_dt.tzinfo:
                    age_h = (datetime.now(timezone.utc) - scan_dt).total_seconds() / 3600
                else:
                    age_h = (datetime.now() - scan_dt).total_seconds() / 3600
                if age_h > 2:
                    data["_stale"] = True
                    data["_age_hours"] = round(age_h, 1)
            except Exception:
                pass
        return data
    except Exception:
        return None


# ── Assembly ─────────────────────────────────────────────────────────────────

def build_weekly_pnl_summary(tra_closed, trb_closed):
    """Compact weekly closed P/L for morning email."""
    html = ""
    for acct, closed in [("tra", tra_closed), ("trb", trb_closed)]:
        if not closed:
            continue
        total = sum(c["gain_loss"] for c in closed)
        winners = sum(1 for c in closed if c["gain_loss"] > 0)
        losers = sum(1 for c in closed if c["gain_loss"] <= 0)
        tc = "g" if total >= 0 else "r"
        html += f'<b>{acct}:</b> <span class="{tc} b">${total:+,.2f}</span> ({winners}W/{losers}L, {len(closed)} trades) &nbsp;&nbsp;'
    if not html:
        html = '<span class="gr">No closed trades this week.</span>'
    return f'<div class="box">{html}</div>'


def build_oil_spread_section():
    """USO/BNO spread z-score + state for morning email."""
    try:
        state_file = TRADIER_DIR / "oil_spread_state.json"
        state = {}
        if state_file.exists():
            with open(state_file) as f:
                state = json.load(f)
        # Try to compute fresh z-score
        from oil_spread_monitor import compute_ratio_zscore
        zdata = compute_ratio_zscore()
        if "error" in zdata:
            return f'<p class="gr">Oil spread: {zdata["error"]}</p>'
        z = zdata["z_score"]
        ratio = zdata["ratio"]
        mean = zdata["mean"]
        uso = zdata["uso_price"]
        bno = zdata["bno_price"]
        signal = zdata["signal"]
        # Z-score gauge
        z_color = "r" if abs(z) > 2.5 else ("o" if abs(z) > 1.5 else "g")
        signal_pill = "pr" if signal == "SHORT_SPREAD" else ("pg" if signal == "LONG_SPREAD" else "pb")
        signal_text = "USO puts + BNO calls" if signal == "SHORT_SPREAD" else ("USO calls + BNO puts" if signal == "LONG_SPREAD" else "No signal")
        html = f'<div class="box">'
        html += f'<b>Z-Score: <span class="{z_color}" style="font-size:16px">{z:+.2f}</span></b> &nbsp; '
        html += f'Ratio: {ratio:.4f} (mean={mean:.4f}) &nbsp; '
        html += f'USO=${uso:.2f} BNO=${bno:.2f}<br>'
        html += f'<span class="pill {signal_pill}">{signal}</span> {signal_text}<br>'
        html += f'Entry threshold: z&gt;{2.5} | Current: {abs(z)/2.5*100:.0f}% of trigger'
        # Show open positions
        positions = state.get("positions", [])
        if positions:
            html += f'<br><b>{len(positions)} spread position(s) open</b>'
            for p in positions:
                html += f'<br>&nbsp;&nbsp;{p["direction"]} entered z={p.get("entry_z",0):+.2f} on {p.get("entry_time","?")[:10]}'
        html += '</div>'
        return html
    except Exception as e:
        return f'<p class="gr">Oil spread error: {e}</p>'


def build_congress_section():
    """Build congressional/insider trading section from stock_trader_scanner data."""
    html = ""
    stock_dir = DATA_DIR / "stock_traders"
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    csv_path = stock_dir / f"{today}_trades.csv"
    if not csv_path.exists():
        csv_path = stock_dir / f"{yesterday}_trades.csv"
    conv_path = stock_dir / f"{today}_conviction.json"
    if not conv_path.exists():
        conv_path = stock_dir / f"{yesterday}_conviction.json"
    # Conviction signals (multi-source agreement)
    if conv_path.exists():
        try:
            conviction = json.loads(conv_path.read_text())
            if conviction:
                html += '<h3>Multi-Source Conviction Signals</h3>'
                html += '<table><tr><th>Symbol</th><th>Direction</th><th>Sources</th><th>Total $</th><th>Who</th></tr>'
                for c in conviction[:15]:
                    side_cls = "g" if c["side"] == "LONG" else "r"
                    side_icon = "BUY" if c["side"] == "LONG" else "SELL"
                    sources = ", ".join(c.get("sources", []))
                    traders = ", ".join(t[:20] for t in c.get("traders", [])[:3])
                    val = c.get("total_value", 0)
                    val_str = f"${val:,.0f}" if val > 0 else "-"
                    html += f'<tr><td><b>{c["symbol"]}</b></td><td class="{side_cls} b">{side_icon}</td><td>{c.get("conviction_sources", 0)} ({sources})</td><td>{val_str}</td><td>{traders}</td></tr>'
                html += '</table>'
        except Exception:
            pass
    # Congress trades breakdown
    if csv_path.exists():
        try:
            import csv as csvmod
            congress_trades = []
            insider_trades = []
            with open(csv_path) as f:
                reader = csvmod.DictReader(f)
                for row in reader:
                    src = row.get("source", "")
                    if "congress" in src:
                        congress_trades.append(row)
                    elif src in ("openinsider", "finviz_insider"):
                        insider_trades.append(row)
            trump_trades = [t for t in congress_trades if "trump_circle" in t.get("source", "")]
            other_congress = [t for t in congress_trades if "trump_circle" not in t.get("source", "")]
            if trump_trades:
                html += f'<h3 style="color:#c62828">Trump Inner Circle ({len(trump_trades)} trades)</h3>'
                html += '<table><tr><th>Politician</th><th>Symbol</th><th>Action</th><th>Price</th><th>Size</th><th>Date</th></tr>'
                seen_trump = set()
                for t in trump_trades[:20]:
                    key = f"{t.get('trader_name','')}_{t.get('symbol','')}_{t.get('side','')}"
                    if key in seen_trump:
                        continue
                    seen_trump.add(key)
                    side_cls = "g" if t.get("side") == "LONG" else "r"
                    side_label = "BUY" if t.get("side") == "LONG" else "SELL"
                    price = float(t.get("entry_price", 0) or 0)
                    price_str = f"${price:.2f}" if price > 0 else "-"
                    size = float(t.get("position_size_usd", 0) or 0)
                    size_str = f"${size:,.0f}" if size > 0 else "-"
                    html += f'<tr><td><b>{t.get("trader_name","?")}</b> <span class="gr">({t.get("trader_title","")})</span></td><td><b>{t.get("symbol","")}</b></td><td class="{side_cls} b">{side_label}</td><td>{price_str}</td><td>{size_str}</td><td>{t.get("entry_time","")}</td></tr>'
                html += '</table>'
            if insider_trades:
                html += f'<h3>Insider Trades ({len(insider_trades)})</h3>'
                html += '<table><tr><th>Insider</th><th>Symbol</th><th>Action</th><th>Price</th><th>Value</th><th>Title</th></tr>'
                for t in insider_trades[:15]:
                    side_cls = "g" if t.get("side") == "LONG" else "r"
                    side_label = "BUY" if t.get("side") == "LONG" else "SELL"
                    price = float(t.get("entry_price", 0) or 0)
                    price_str = f"${price:.2f}" if price > 0 else "-"
                    val = float(t.get("position_size_usd", 0) or 0)
                    val_str = f"${val:,.0f}" if val > 0 else "-"
                    html += f'<tr><td>{t.get("trader_name","?")}</td><td><b>{t.get("symbol","")}</b></td><td class="{side_cls} b">{side_label}</td><td>{price_str}</td><td>{val_str}</td><td>{t.get("trader_title","")}</td></tr>'
                html += '</table>'
        except Exception:
            pass
    # Active injections from this scanner
    inj_file = DATA_DIR / "news_injections.json"
    if inj_file.exists():
        try:
            inj_data = json.loads(inj_file.read_text())
            stock_inj = [i for i in inj_data.get("active", []) if i.get("_source") == "stock_trader_scanner"]
            if stock_inj:
                html += f'<h3>Injected into Rankings ({len(stock_inj)} active)</h3><div class="box">'
                for i in stock_inj:
                    side_cls = "pg" if i["side"] == "LONG" else "pr"
                    html += f'<span class="pill {side_cls}">{i["symbol"]} {i["side"]}</span> '
                html += f'<br><span class="gr">Auto-expires after {INJECTION_TTL_HOURS}h. Source: stock_trader_scanner.</span></div>'
        except Exception:
            pass
    # Performance scorecard — did past conviction picks move in the right direction?
    scorecard_html = _build_conviction_scorecard()
    if scorecard_html:
        html += scorecard_html
    if not html:
        html = '<div class="box"><span class="gr">No stock trader data available. Scanner runs every 4h.</span></div>'
    return html


def _build_conviction_scorecard():
    """Check past conviction picks against actual price moves at 7/14/30 day windows."""
    stock_dir = DATA_DIR / "stock_traders"
    html = ""
    # Collect conviction files from last 30 days
    picks = []
    now = datetime.now(timezone.utc)
    for days_ago in range(1, 31):
        dt = (now - timedelta(days=days_ago)).strftime("%Y%m%d")
        conv_path = stock_dir / f"{dt}_conviction.json"
        if not conv_path.exists():
            continue
        try:
            data = json.loads(conv_path.read_text())
            for c in data:
                picks.append({"symbol": c["symbol"], "side": c["side"], "sources": c.get("conviction_sources", 0), "date": dt, "days_ago": days_ago})
        except Exception:
            continue
    if not picks:
        return ""
    # Deduplicate (same symbol+side = keep earliest)
    seen = set()
    unique_picks = []
    for p in sorted(picks, key=lambda x: -x["days_ago"]):
        key = f"{p['symbol']}_{p['side']}"
        if key not in seen:
            seen.add(key)
            unique_picks.append(p)
    # Get current prices via Yahoo Finance (quick batch)
    symbols = list(set(p["symbol"] for p in unique_picks))
    current_prices = {}
    try:
        sym_str = ",".join(symbols[:30])
        req = urllib.request.Request(f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={sym_str}&fields=regularMarketPrice,regularMarketPreviousClose", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            for q in data.get("quoteResponse", {}).get("result", []):
                current_prices[q["symbol"]] = q.get("regularMarketPrice", 0)
    except Exception:
        pass
    # Get historical prices for scoring
    scored = []
    for p in unique_picks[:20]:
        sym = p["symbol"]
        if sym not in current_prices or current_prices[sym] <= 0:
            continue
        current = current_prices[sym]
        # Get price on pick date via Yahoo chart API
        pick_date = datetime.strptime(p["date"], "%Y%m%d")
        try:
            period1 = int((pick_date - timedelta(days=1)).timestamp())
            period2 = int((pick_date + timedelta(days=2)).timestamp())
            req2 = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={period1}&period2={period2}&interval=1d", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=8) as resp2:
                chart = json.loads(resp2.read().decode())
                closes = chart.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                pick_price = closes[0] if closes else 0
        except Exception:
            pick_price = 0
        if pick_price <= 0:
            continue
        move_pct = ((current - pick_price) / pick_price) * 100
        if p["side"] == "SHORT":
            move_pct = -move_pct
        is_right = move_pct > 0
        scored.append({**p, "pick_price": pick_price, "current_price": current, "move_pct": move_pct, "is_right": is_right})
    if not scored:
        return ""
    right = sum(1 for s in scored if s["is_right"])
    total = len(scored)
    wr = right / total * 100 if total else 0
    avg_move = sum(s["move_pct"] for s in scored) / total if total else 0
    wr_cls = "g" if wr >= 55 else "r" if wr < 45 else "o"
    html = f'<h3>Conviction Scorecard ({total} picks, {right}/{total} right = <span class="{wr_cls} b">{wr:.0f}%</span>, avg move <span class="{wr_cls}">{avg_move:+.1f}%</span>)</h3>'
    html += '<table><tr><th>Symbol</th><th>Side</th><th>Pick Date</th><th>Pick $</th><th>Now $</th><th>Move</th><th>Result</th><th>Sources</th></tr>'
    for s in sorted(scored, key=lambda x: -abs(x["move_pct"])):
        side_cls = "g" if s["side"] == "LONG" else "r"
        move_cls = "g" if s["is_right"] else "r"
        result = "RIGHT" if s["is_right"] else "WRONG"
        result_cls = "pg" if s["is_right"] else "pr"
        html += f'<tr><td><b>{s["symbol"]}</b></td><td class="{side_cls}">{s["side"]}</td><td>{s["date"]}</td><td>${s["pick_price"]:.2f}</td><td>${s["current_price"]:.2f}</td><td class="{move_cls} b">{s["move_pct"]:+.1f}%</td><td><span class="pill {result_cls}">{result}</span></td><td>{s["sources"]}</td></tr>'
    html += '</table>'
    html += '<p class="gr">Scorecard: did the stock move in the direction of the conviction signal since the pick date? Sources = number of independent data sources that agreed.</p>'
    return html


INJECTION_TTL_HOURS = 48


def build_emergency_brake_section():
    """Independent emergency-brake status. Reads data/emergency_brake/{config,state,halt}."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt
    base = _Path("/Users/niels/Documents/binance/data/emergency_brake")
    parts = []
    halt_path = base / "halt.flag"
    if halt_path.exists():
        try:
            payload = _json.loads(halt_path.read_text())
            parts.append(f"<p style='font-size:14px;color:#c00'><b>&#128721; HALT FLAG ACTIVE</b> — set at {payload.get('set_at','?')}<br>Reason: {payload.get('reason','?')}</p>")
            parts.append("<p>All NEW options entries are REFUSED by tradier_options_agent.make_decisions and _daily_find_opportunities until manual reset.<br>To clear: <code>python3 tradier_emergency_brake.py --reset-halt</code></p>")
        except Exception:
            parts.append("<p style='color:#c00'><b>HALT FLAG present (unparseable payload)</b></p>")
    else:
        parts.append("<p style='color:#080'><b>&#9989; No halt flag</b> — system armed.</p>")
    cfg_path = base / "config.json"
    if cfg_path.exists():
        try:
            cfg = _json.loads(cfg_path.read_text())
            dry = cfg.get("DRY_RUN", True)
            mode = ("<span style='color:#e60'><b>DRY_RUN</b></span>" if dry else "<span style='color:#c00'><b>LIVE</b></span>")
            tiers = cfg.get("TIERS", {})
            parts.append(f"<p>Brake mode: {mode}. Polling: {cfg.get('POLL_INTERVAL_SEC', 60)}s. Accounts: {cfg.get('ACCOUNTS', [])}</p>")
            rows = []
            order = ["alert", "hedge", "double", "halt"]
            tier_descr = {"alert": "ALERT only", "hedge": "Auto equity hedge (delta-sized)",
                          "double": "Double hedge (1.5× delta)", "halt": "CIRCUIT BREAKER (cancel pending buys + halt flag)"}
            for name in order:
                t = tiers.get(name, {})
                rows.append(f"<tr><td>{name}</td><td>{t.get('loss_pct','?')}%</td><td>{t.get('hedge_mult','?')}×</td><td>{tier_descr.get(name,'')}</td></tr>")
            parts.append("<table><tr><th>tier</th><th>premium loss</th><th>hedge mult</th><th>action</th></tr>" + "".join(rows) + "</table>")
        except Exception as e:
            parts.append(f"<p>(config error: {e})</p>")
    today = _dt.utcnow().strftime("%Y%m%d")
    log_path = base / f"{today}.jsonl"
    if log_path.exists() and log_path.stat().st_size > 0:
        try:
            actions = [_json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
        except Exception:
            actions = []
        if actions:
            parts.append(f"<p><b>Brake actions today UTC ({len(actions)}):</b></p>")
            rows = []
            for a in actions[-15:]:
                rows.append(f"<tr><td>{a.get('ts','')[:19].replace('T',' ')}</td><td>{a.get('occ','')}</td><td>{a.get('tier','')}</td><td>{a.get('action','')}</td><td>{a.get('loss_pct','?')}%</td><td>{a.get('hedge_side','')}</td><td>{a.get('hedge_qty','')}</td><td>{'DRY' if a.get('dry_run') else 'LIVE'}</td></tr>")
            parts.append("<table><tr><th>ts</th><th>occ</th><th>tier</th><th>action</th><th>loss%</th><th>side</th><th>qty</th><th>mode</th></tr>" + "".join(rows) + "</table>")
    return "\n".join(parts)


def build_opening_buffer_section():
    """Surface opening-buffer status (no-trade until 14:00 UTC) + first 30m + VWAP
    snapshot so owner sees data-quality state at email-send (13:35 UTC = 5m into open)."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt
    try:
        import pytz
        now_et = _dt.now(pytz.timezone("America/New_York"))
        is_weekend = now_et.weekday() >= 5
        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        mins_since_open = (now_et - open_et).total_seconds() / 60.0
    except Exception:
        is_weekend = False
        mins_since_open = -999
    if is_weekend:
        return "<p>Weekend — buffer/VWAP not applicable.</p>"
    parts = []
    BUFFER_MIN = 30.0
    if mins_since_open < 0:
        parts.append(f"<p><b>Pre-market</b> ({-mins_since_open:.0f}m to open). NO TRADES will fire — premarket orders held (OPTIONS_PREMARKET_NO_FIRE=True). Opening buffer activates at 9:30 AM ET.</p>")
    elif mins_since_open < BUFFER_MIN:
        remaining = BUFFER_MIN - mins_since_open
        parts.append(f"<p style='font-size:14px;color:#e60'><b>&#9888;&#65039; OPENING BUFFER ACTIVE</b> — {mins_since_open:.0f}m / {BUFFER_MIN:.0f}m elapsed. <b>{remaining:.0f}m until trades can fire.</b></p>")
        parts.append("<p>All evaluate_open / evaluate_augment / evaluate_reentry / evaluate_stop calls return NO_ACTION until buffer expires. Options auto_sell + _daily_find_opportunities also held.</p>")
    else:
        parts.append(f"<p><b>Buffer expired</b> ({mins_since_open:.0f}m since open). Trade actions enabled. First-30m metrics below should be used as confirmation.</p>")
    spy_ind = {}
    qqq_ind = {}
    ind_path = _Path("/Users/niels/Documents/binance/data/tradier_indicators_latest.json")
    if not ind_path.exists():
        ind_path = _Path("/Users/niels/Documents/binance/data/tradier/tradier_indicators_latest.json")
    if ind_path.exists():
        try:
            ind_map = _json.loads(ind_path.read_text())
            spy_ind = ind_map.get("SPY", {}) or {}
            qqq_ind = ind_map.get("QQQ", {}) or {}
        except Exception:
            pass
    rows = []
    for sym, ind in [("SPY", spy_ind), ("QQQ", qqq_ind)]:
        if not ind:
            rows.append(f"<tr><td>{sym}</td><td colspan='6'>(no indicators yet)</td></tr>")
            continue
        px = ind.get("current_price", "?")
        vwap = ind.get("vwap", "?")
        vd = ind.get("vwap_distance_pct", "?")
        dc_h = ind.get("dc_high_5m", "?")
        dc_l = ind.get("dc_low_5m", "?")
        k_15m = ind.get("stoch_k_15m", "?")
        wt_d = ind.get("wt_cross_D", "?")
        rows.append(f"<tr><td><b>{sym}</b></td><td>{px}</td><td>{vwap}</td><td>{vd}</td><td>{dc_l} / {dc_h}</td><td>{k_15m}</td><td>{wt_d}</td></tr>")
    parts.append("<p><b>Index data quality:</b></p>")
    parts.append("<table><tr><th>sym</th><th>price</th><th>VWAP</th><th>vwap_dist%</th><th>DC_5m L / H</th><th>K_15m</th><th>wt_cross_D</th></tr>" + "".join(rows) + "</table>")
    parts.append("<p style='font-size:11px;color:#888'>VWAP_FILTER_ENABLED=False (sweep-validated — VWAP as hard gate hurt Sharpe). VWAP shown as decision-support context only. Use it to gauge whether opening prints favor your existing positions before manually firing held orders.</p>")
    return "\n".join(parts)


def build_options_alerts_and_held_section():
    """Loss-deepening alerts (data/options_state/alerts_<date>.jsonl) + held
    premarket orders (data/daily_plan.json with premarket_held flag).
    Read-only — surfaces what the system DETECTED for owner review."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt
    base = _Path("/Users/niels/Documents/binance/data/options_state")
    today = _dt.utcnow().strftime("%Y%m%d")
    parts = []
    alerts_path = base / f"alerts_{today}.jsonl"
    if alerts_path.exists() and alerts_path.stat().st_size > 0:
        try:
            alerts = [_json.loads(line) for line in alerts_path.read_text().splitlines() if line.strip()]
        except Exception:
            alerts = []
        if alerts:
            seen_keys = set()
            unique = []
            for a in reversed(alerts):
                k = (a.get("occ"), a.get("kind"))
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                unique.append(a)
            unique = list(reversed(unique))
            rows = []
            for a in unique[-20:]:
                rows.append(f"<tr><td>{a.get('ts','')[:19].replace('T',' ')}</td><td>{a.get('symbol','')}</td><td>{a.get('occ','')}</td><td><span class='r b'>{a.get('kind','')}</span></td><td>{a.get('curr_pct','?'):+.1f}%</td><td>{a.get('drop_pp','')}</td><td>${a.get('unrealized_pnl','?'):+,.2f}</td><td>{a.get('note','')}</td></tr>")
            parts.append("<p><b>Loss-deepening alerts (today UTC):</b></p>")
            parts.append("<table><tr><th>ts UTC</th><th>sym</th><th>occ</th><th>kind</th><th>curr%</th><th>drop pp</th><th>$ pnl</th><th>note</th></tr>" + "".join(rows) + "</table>")
        else:
            parts.append("<p>No alerts fired yet today.</p>")
    else:
        parts.append("<p>No alerts file for today (snapshot loop may not have started).</p>")
    plan_path = _Path("/Users/niels/Documents/binance/data/options_daily_plan.json")
    if not plan_path.exists():
        plan_path = _Path("/Users/niels/Documents/binance/data/daily_plan.json")
    if plan_path.exists():
        try:
            plan = _json.loads(plan_path.read_text())
        except Exception:
            plan = {}
        if plan.get("premarket_held"):
            held = plan.get("orders", []) or []
            rows = []
            for o in held:
                rows.append(f"<tr><td>{o.get('symbol','')}</td><td>{o.get('type','')}</td><td>{o.get('strike','')}</td><td>{o.get('expiration','')}</td><td>{o.get('qty',1)}</td><td>${o.get('gtc_price',0):.2f}</td><td>{o.get('reason','')[:60]}</td></tr>")
            parts.append(f"<p><b>Premarket orders HELD ({len(held)}) — OPTIONS_PREMARKET_NO_FIRE=True. Held at {plan.get('premarket_held','')[:19]}.</b></p>")
            parts.append("<table><tr><th>sym</th><th>type</th><th>strike</th><th>exp</th><th>qty</th><th>limit</th><th>reason</th></tr>" + "".join(rows) + "</table>")
            parts.append("<p style='font-size:11px;color:#888'>To fire any of these manually after review: edit config_tradier.OPTIONS_PREMARKET_NO_FIRE=False, then run <code>python3 tradier_options_agent.py --premarket</code>.</p>")
    return "\n".join(parts) if parts else "<p>No alerts or held orders.</p>"


def build_hedge_proposals_section():
    """Read latest hedge_proposals_<date>.jsonl. Each cycle the snapshot loop
    runs decide_hedge_action against losing positions; this surfaces the
    proposals so owner can verify ladder behavior before flipping live.
    NEVER fires orders."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt
    base = _Path("/Users/niels/Documents/binance/data/options_state")
    today = _dt.utcnow().strftime("%Y%m%d")
    path = base / f"hedge_proposals_{today}.jsonl"
    if not path.exists() or path.stat().st_size == 0:
        return "<p>No hedge proposals yet today (snapshot loop not started or no losing positions hit -10% trigger).</p>"
    try:
        lines = [_json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except Exception:
        return "<p>(error reading hedge proposals)</p>"
    if not lines:
        return "<p>No hedge proposals.</p>"
    latest_by_occ = {}
    for rec in lines:
        latest_by_occ[rec.get("occ", "?")] = rec
    rows = []
    for occ, rec in latest_by_occ.items():
        action = rec.get("action", "?")
        color_class = "g b" if action == "HOLD" else ("o b" if action == "BUY_PUT" else "r b")
        details = rec.get("reason", "")
        if action == "BUY_PUT":
            details = (f"buy {rec.get('put_qty',1)} × {rec.get('put_occ','')} @ ${rec.get('put_limit_price',0):.2f}  "
                       f"(IV rank {rec.get('put_iv_chain_rank',0):.0f}%, Δ={rec.get('put_delta',0):+.2f}, DTE {rec.get('put_dte',0)}) — cost ≈ ${rec.get('put_qty',1) * rec.get('put_limit_price',0) * 100:.0f}")
        rows.append(f"<tr><td>{rec.get('ts','')[:19].replace('T',' ')}</td><td>{rec.get('symbol','')}</td><td>{occ}</td><td>{rec.get('sellable_pct','?'):+.1f}%</td><td><span class='{color_class}'>{action}</span></td><td>{details}</td></tr>")
    out = ["<p style='font-size:11px;color:#888'>Hedge ladder = HOLD@bottom → BUY_PUT@underpriced → EQUITY_HEDGE. Currently <b>shadow only</b> (OPTIONS_HEDGE_LADDER_ENABLED=False). To activate: review proposals over 1-2 days, then flip the flag.</p>"]
    out.append("<table><tr><th>ts UTC</th><th>sym</th><th>occ</th><th>sellable%</th><th>action</th><th>details</th></tr>" + "".join(rows) + "</table>")
    return "\n".join(out)


def build_persym_campaign_section():
    digest = DATA_DIR / "reports" / "persym_campaign_digest.md"
    uni = DATA_DIR / "reports" / "universe_vs_random_baseline.json"
    parts = []
    try:
        if digest.exists():
            age_h = (datetime.now(timezone.utc).timestamp() - digest.stat().st_mtime) / 3600
            body = digest.read_text()
            lines = [ln for ln in body.splitlines() if ln.strip()][:28]
            parts.append(f'<div class="box"><p style="font-size:11px;color:#888">digest age {age_h:.1f}h (S1 pulls hourly; stale &gt;24h = campaign stalled)</p><pre style="font-size:11px;white-space:pre-wrap">{chr(10).join(lines)}</pre></div>')
        if uni.exists():
            data = json.loads(uni.read_text())
            rows = "".join(f"<tr><td>{k}</td><td>{v.get('n_keys')}</td><td>{v.get('mean_pool_sharpe')}</td><td>{v.get('trades')}</td><td>{v.get('mean_delta_vs_bh_mo')}</td></tr>" for k, v in data.items() if isinstance(v, dict))
            if rows:
                parts.append(f'<div class="box"><b>Universe (curated point-in-time) vs off-universe keys — identical baseline runs</b><table><tr><th>set</th><th>keys</th><th>mean pool_sharpe</th><th>trades</th><th>mean Δgain/mo vs b&amp;h</th></tr>{rows}</table></div>')
    except Exception as e:
        parts.append(f'<p class="gr">campaign section error: {e}</p>')
    return "".join(parts) or '<p class="gr">No campaign digest yet (S1 grind not reporting).</p>'


def build_email_html(tra_data, trb_data, market_quotes, tra_closed=None, trb_closed=None):
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc - timedelta(hours=4)
    sync_html, is_synced = build_sync_health(tra_data, trb_data)
    banner = ""
    if not is_synced:
        banner = '<div style="background:#c62828;color:#fff;padding:12px 18px;border-radius:6px;margin-bottom:15px;font-size:14px"><b>&#9888; POSITION SYNC BROKEN</b> — Position files do NOT match Tradier API. Numbers below are from the API (correct), but tradier_manage.py may be trading on stale/wrong data. See Sync Health section below.</div>'
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Morning Briefing &mdash; {now_et.strftime('%A, %B %d %Y')} &middot; {now_et.strftime('%I:%M %p')} ET</h1>
{banner}

<h2>Week P/L (closed positions)</h2>
{build_weekly_pnl_summary(tra_closed or [], trb_closed or [])}

<h2>Market Overview</h2>
{build_market_section(market_quotes)}

<h2>TradingView WT Bias (trb universe)</h2>
{build_tv_bias_section()}

<h2>News Scanner Intelligence</h2>
{build_news_scanner_section()}

<h2>tra — Non-Margin HODL ({len(tra_data.get('positions',[]))} positions, open P/L ${tra_data['balance'].get('open_pl',0):+,.0f})</h2>
{build_account_section(tra_data)}

<h2>trb — Live ({len(trb_data.get('positions',[]))} positions, open P/L ${trb_data['balance'].get('open_pl',0):+,.0f})</h2>
{build_account_section(trb_data)}

<h2>Position Sync Health — API vs Files</h2>
{sync_html}

<h2>Pre-Market Analysis</h2>
{build_premarket_section()}

<h2>Open Options Positions &amp; Sector Hedge Analysis</h2>
{build_options_positions_section()}

<h2>&#128721; Emergency Brake Status</h2>
{build_emergency_brake_section()}

<h2>&#9203; Opening Buffer + VWAP Status</h2>
{build_opening_buffer_section()}

<h2>&#128680; Loss Alerts &amp; Held Premarket Orders</h2>
{build_options_alerts_and_held_section()}

<h2>&#128737;&#65039; Hedge Ladder Proposals (shadow — no orders fired)</h2>
{build_hedge_proposals_section()}

<h2>Options Scanner</h2>
{build_options_section()}

<h2>Oil Spread (USO/BNO)</h2>
{build_oil_spread_section()}

<h2>&#127981; Corrupt Politicians' Trades to Copy</h2>
{build_congress_section()}

<h2>&#128300; Per-Sym Baseline Campaign (S1)</h2>
{build_persym_campaign_section()}

<hr style="margin-top:30px;border:none;border-top:1px solid #ccc">
<p style="font-size:10px;color:#aaa">Generated {now_utc.strftime('%Y-%m-%d %H:%M')} UTC &mdash; positions and prices from local files &mdash; news from Yahoo Finance + CNBC RSS</p>
</body></html>"""
    return html


def send_email(html_body, to=None, subject=None):
    pwd = get_gmail_password()
    if not pwd:
        logger.error("No Gmail password")
        return False
    recipients = to if isinstance(to, list) else [to or TO_EMAIL]
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    subject = subject or f"Morning Briefing — {now_et.strftime('%a %b %d')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText("Open in HTML email client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    # 2026-06-04: this network blocks outbound 587 (STARTTLS) but allows 465 (SSL) — all email
    # was failing "Connection refused" on 587. Try SSL/465 first, fall back to STARTTLS/587.
    last_err = None
    for mode, port in (("ssl", 465), ("starttls", 587)):
        try:
            if mode == "ssl":
                server = smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=30)
            else:
                server = smtplib.SMTP("smtp.gmail.com", port, timeout=30)
                server.starttls()
            server.login(FROM_EMAIL, pwd)
            server.sendmail(FROM_EMAIL, recipients, msg.as_string())
            server.quit()
            logger.info(f"Email sent to {', '.join(recipients)} via {mode}:{port}")
            return True
        except Exception as e:
            last_err = e
            logger.warning(f"Send via {mode}:{port} failed: {e}")
    logger.error(f"Send failed on all transports: {last_err}")
    return False


def run_scanner_if_stale():
    data = load_options_data()
    if data and not data.get("_stale"):
        return
    logger.info("Running options scanner...")
    py = "/opt/anaconda3/envs/binance_env/bin/python" if platform.system() == "Darwin" else "/home/niels/.conda/envs/binance_env/bin/python"
    try:
        subprocess.run([py, str(BASE / "tradier_options_analyzer.py"), "scan", "--top", "30"], cwd=str(BASE), timeout=300, capture_output=True)
    except Exception as e:
        logger.warning(f"Scanner failed: {e}")


async def gather_options_history():
    """Fetch last 5 daily closes for each option contract and underlying in GTC orders."""
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    gtc_file = TRADIER_DIR / "options_gtc_orders.json"
    if not gtc_file.exists():
        return
    try:
        with open(gtc_file) as f:
            gtc = json.load(f)
    except Exception:
        return
    if not gtc:
        return
    cfg = TradierConfig()
    client = TradierAPIClient(config=cfg, account_key="trb")
    start = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    price_hist = {}
    symbols_done = set()
    for occ, info in gtc.items():
        if info.get("side") != "buy_to_open":
            continue
        sym = info.get("symbol", "")
        # Fetch option history
        try:
            opt_data = await client.get_history(occ, start=start, interval="daily")
            if opt_data:
                closes = [d.get("close", 0) for d in opt_data if d.get("close")]
                if closes:
                    price_hist[f"opt:{occ}"] = {"closes": closes[-5:], "dates": [d.get("date", "") for d in opt_data[-5:]]}
        except Exception:
            pass
        # Fetch equity history (once per symbol)
        if sym and sym not in symbols_done:
            symbols_done.add(sym)
            try:
                eq_data = await client.get_history(sym, start=start, interval="daily")
                if eq_data:
                    closes = [d.get("close", 0) for d in eq_data if d.get("close")]
                    if closes:
                        price_hist[f"eq:{sym}"] = {"closes": closes[-5:], "dates": [d.get("date", "") for d in eq_data[-5:]]}
            except Exception:
                pass
        await asyncio.sleep(0.1)
    # Save to file for the sync build_options_section to read
    out_file = TRADIER_DIR / "options_price_history.json"
    with open(out_file, "w") as f:
        json.dump(price_hist, f, indent=2)
    logger.info(f"Options price history: {len(price_hist)} entries saved")


async def gather_data():
    tra_data = get_account_data_local("tra")
    trb_data = get_account_data_local("trb")
    tra_closed = get_weekly_closed_local("tra")
    trb_closed = get_weekly_closed_local("trb")
    prices_path = DATA_DIR / "tradier" / "tradier_prices_latest.json"
    market_quotes = {}
    try:
        raw = json.loads(prices_path.read_text())
        prices = raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw
        for sym in ["SPY", "QQQ", "DIA", "IWM", "VIX"]:
            if sym in prices:
                market_quotes[sym] = {"last": prices[sym].get("last", 0), "prevclose": 0, "change_percentage": 0}
    except Exception:
        pass
    await gather_options_history()
    return tra_data, trb_data, market_quotes, tra_closed, trb_closed


def build_public_news_section():
    """News scanner section without paper trade details."""
    ns = load_news_scanner_data()
    html = ""
    stock_sent = ns.get("stock_sentiment", {})
    if stock_sent:
        bullish = sorted([(s, sc) for s, sc in stock_sent.items() if sc > 0.2], key=lambda x: x[1], reverse=True)
        bearish = sorted([(s, sc) for s, sc in stock_sent.items() if sc < -0.2], key=lambda x: x[1])
        if bullish or bearish:
            html += "<h3>Stock Sentiment (Finnhub + RSS)</h3><table>"
            html += "<tr><th>Symbol</th><th>Sentiment</th><th>Score</th></tr>"
            for sym, score in bullish[:6]:
                strength = "STRONG BUY" if score > 0.6 else "BULLISH"
                html += f'<tr><td><b>{sym}</b></td><td><span class="pill pg">{strength}</span></td><td style="text-align:right" class="g b">{score:+.3f}</td></tr>'
            for sym, score in bearish[:4]:
                strength = "STRONG SELL" if score < -0.5 else "BEARISH"
                html += f'<tr><td><b>{sym}</b></td><td><span class="pill pr">{strength}</span></td><td style="text-align:right" class="r b">{score:+.3f}</td></tr>'
            html += "</table>"
    for evt in ns.get("macro_events", []):
        html += f'<div class="news" style="border-left-color:#c62828"><b>Macro Alert:</b> {evt}</div>'
    if not html:
        html = '<p class="gr">News scanner data not available.</p>'
    return html


def build_public_email_html(market_quotes, tra_data, trb_data):
    """Public version — market analysis, news, scanner picks. No dollar values, quantities, or account details."""
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc - timedelta(hours=4)
    seen = set()
    all_positions = []
    for data in [tra_data, trb_data]:
        for p in data.get("positions", []):
            if p["symbol"] not in seen:
                seen.add(p["symbol"])
                all_positions.append(p)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Market Briefing &mdash; {now_et.strftime('%A, %B %d %Y')} &middot; {now_et.strftime('%I:%M %p')} ET</h1>

<h2>Market Overview</h2>
{build_market_section(market_quotes)}

<h2>TradingView WT Bias</h2>
{build_tv_bias_section()}

<h2>News Sentiment</h2>
{build_public_news_section()}

<h2>Active Trades</h2>"""
    if all_positions:
        html += '<table><tr><th>Symbol</th><th>Direction</th><th>Day %</th><th>Total %</th></tr>'
        for p in sorted(all_positions, key=lambda x: abs(x["gain_pct"]), reverse=True):
            gc = "g" if p["gain_pct"] > 0 else "r" if p["gain_pct"] < -1 else "o"
            dc = "g" if p["day_chg"] > 0 else "r" if p["day_chg"] < 0 else "gr"
            sp = "pg" if p["side"] == "LONG" else "pr"
            html += f'<tr><td><b>{p["symbol"]}</b></td><td><span class="pill {sp}">{p["side"]}</span></td><td style="text-align:right" class="{dc}">{p["day_chg"]:+.1f}%</td><td style="text-align:right" class="{gc} b">{p["gain_pct"]:+.1f}%</td></tr>'
        html += "</table>"
    html += f"""
<h2>Pre-Market Candidates</h2>
{build_premarket_section()}

<h2>Open Options Positions &amp; Sector Hedge Analysis</h2>
{build_options_positions_section()}

<h2>&#128721; Emergency Brake Status</h2>
{build_emergency_brake_section()}

<h2>&#9203; Opening Buffer + VWAP Status</h2>
{build_opening_buffer_section()}

<h2>&#128680; Loss Alerts &amp; Held Premarket Orders</h2>
{build_options_alerts_and_held_section()}

<h2>&#128737;&#65039; Hedge Ladder Proposals (shadow — no orders fired)</h2>
{build_hedge_proposals_section()}

<h2>Options Scanner</h2>
{build_options_section()}

<h2>&#127981; Corrupt Politicians' Trades to Copy</h2>
{build_congress_section()}

<hr style="margin-top:30px;border:none;border-top:1px solid #ccc">
<p style="font-size:10px;color:#aaa">Generated {now_utc.strftime('%Y-%m-%d %H:%M')} UTC &mdash; Market analysis only, no account data</p>
</body></html>"""
    return html


def _already_sent_today(tag: str) -> bool:
    """Return True if we already sent this email variant today (ET date)."""
    stamp_file = DATA_DIR / f".morning_email_sent_{tag}"
    if not stamp_file.exists():
        return False
    try:
        sent_date = stamp_file.read_text().strip()
        today_et = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")
        return sent_date == today_et
    except Exception:
        return False

def _mark_sent_today(tag: str) -> None:
    today_et = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")
    (DATA_DIR / f".morning_email_sent_{tag}").write_text(today_et)

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-only", action="store_true", help="Generate and send ONLY the public version")
    parser.add_argument("--force", action="store_true", help="Force send even if already sent today")
    parser.add_argument("--html-only", action="store_true", help="Write HTML but do not send email")
    args = parser.parse_args()
    logger.info("=== Morning Briefing Email ===")
    run_scanner_if_stale()
    loop = asyncio.new_event_loop()
    tra_data, trb_data, market_quotes, tra_closed, trb_closed = loop.run_until_complete(gather_data())
    loop.close()
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    if not args.public_only:
        html = build_email_html(tra_data, trb_data, market_quotes)
        (DATA_DIR / "morning_email_latest.html").write_text(html)
        if args.html_only:
            logger.info("HTML written (--html-only, not sending)")
        elif not args.force and _already_sent_today("private"):
            logger.info("Private version already sent today — skipping (use --force to override)")
        elif send_email(html):
            logger.info("Private version sent")
            _mark_sent_today("private")
    if PUBLIC_RECIPIENTS or args.public_only:
        pub_html = build_public_email_html(market_quotes, tra_data, trb_data)
        (DATA_DIR / "morning_email_public.html").write_text(pub_html)
        recipients = PUBLIC_RECIPIENTS if PUBLIC_RECIPIENTS else [TO_EMAIL]
        subject = f"Market Briefing — {now_et.strftime('%a %b %d')}"
        if args.html_only:
            logger.info("Public HTML written (--html-only, not sending)")
        elif not args.force and _already_sent_today("public"):
            logger.info("Public version already sent today — skipping (use --force to override)")
        elif send_email(pub_html, to=recipients, subject=subject):
            logger.info(f"Public version sent to {', '.join(recipients)}")
            _mark_sent_today("public")


if __name__ == "__main__":
    main()
