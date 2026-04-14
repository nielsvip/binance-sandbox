#!/usr/bin/env python3
"""Post-Market Evening Recap — what happened today.
Cron: 20:15 UTC (4:15 PM ET) weekdays, right after market close.

Sections:
  1. Market Close — SPY/QQQ/DIA/IWM day performance, VIX
  2. Day's Trades — all opens, closes, augments with P/L
  3. Portfolio EOD — tra + trb positions with day change + total gain
  4. Today's News — all market + ticker headlines from the day
  5. Options Activity — watchdog alerts, P/L on option positions
  6. Position Sync Health — API vs files check
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
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from xml.etree import ElementTree

BASE = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE))
DATA_DIR = BASE / "data"
DECISIONS_DIR = DATA_DIR / "decisions"
TRADIER_DIR = DATA_DIR / "tradier"
TO_EMAIL = "nielsvip@gmail.com"
FROM_EMAIL = "nielsvip@gmail.com"
PUBLIC_RECIPIENTS = [
    # Add friend emails here — they get the public version (no account values)
    # "friend@example.com",
]

CSS = """
body { font-family: -apple-system, 'Segoe UI', Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 15px; background: #fafafa; color: #222; font-size: 13px; }
h1 { color: #1a1a2e; border-bottom: 3px solid #4a90d9; padding-bottom: 8px; font-size: 20px; margin-bottom: 5px; }
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
"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
logger = logging.getLogger("evening_email")


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


# ── Data loaders ─────────────────────────────────────────────────────────────

async def get_weekly_closed(account_key):
    """Get closed positions this week from Tradier gainloss API."""
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    cfg = TradierConfig()
    client = TradierAPIClient(config=cfg, account_key=account_key)
    try:
        gl = await client._request("GET", f"/accounts/{client._current_id}/gainloss", params={"page": 1, "limit": 100, "sortBy": "closeDate", "sort": "desc"}, use_data_context=False)
        if not gl or "gainloss" not in gl:
            return []
        positions = gl["gainloss"].get("closed_position", [])
        if isinstance(positions, dict):
            positions = [positions]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        week_closed = []
        for p in positions:
            close_date = p.get("close_date", "")[:10]
            if close_date < cutoff:
                break
            week_closed.append({"symbol": p.get("symbol", "?"), "qty": float(p.get("quantity", 0)), "cost": float(p.get("cost", 0)), "proceeds": float(p.get("proceeds", 0)), "gain_loss": float(p.get("gain_loss", 0)), "gain_pct": float(p.get("gain_loss_percent", 0)), "close_date": close_date, "open_date": p.get("open_date", "")[:10], "term": p.get("term", 0)})
        return week_closed
    except Exception as e:
        logger.warning(f"Failed to get gainloss for {account_key}: {e}")
        return []


async def get_account_data(account_key):
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    cfg = TradierConfig()
    client = TradierAPIClient(config=cfg, account_key=account_key)
    result = {"positions": [], "balance": {}, "option_positions": [], "account_key": account_key}
    try:
        raw_positions = await client.get_account_positions(account_key) or []
        stock_pos = [p for p in raw_positions if len(p.get("symbol", "")) <= 5]
        option_pos = [p for p in raw_positions if len(p.get("symbol", "")) > 5]
        symbols = list(set(p["symbol"] for p in stock_pos))
        quotes = await client.get_quotes(symbols) if symbols else {}
        positions = []
        for p in stock_pos:
            sym = p["symbol"]
            qty = float(p.get("quantity", 0))
            cost = float(p.get("cost_basis", 0))
            q = quotes.get(sym, {})
            last = q.get("last") or q.get("close", 0) or 0
            prev_close = q.get("prevclose") or q.get("previous_close", 0) or last
            mkt_val = qty * last
            pnl = mkt_val - cost
            avg_cost = abs(cost / qty) if qty != 0 else 0
            gain_pct = (pnl / abs(cost) * 100) if cost != 0 else 0
            day_chg = ((last - prev_close) / prev_close * 100) if prev_close else 0
            day_pnl = (last - prev_close) * qty if prev_close else 0
            side = "LONG" if qty > 0 else "SHORT"
            positions.append({"symbol": sym, "side": side, "qty": abs(qty), "avg_cost": avg_cost, "last": last, "day_chg": round(day_chg, 2), "day_pnl": round(day_pnl, 2), "mkt_val": round(abs(mkt_val), 2), "pnl": round(pnl, 2), "gain_pct": round(gain_pct, 2)})
        result["positions"] = sorted(positions, key=lambda x: abs(x["day_pnl"]), reverse=True)
        result["option_positions"] = option_pos
        bal = await client._request("GET", f"/accounts/{client._current_id}/balances", use_data_context=False)
        if bal and "balances" in bal:
            result["balance"] = bal["balances"]
    except Exception as e:
        logger.error(f"Failed to get {account_key} data: {e}")
    return result


def load_todays_decisions(acct):
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    rows = []
    path = DECISIONS_DIR / f"decisions_{acct}_{today}.jsonl"
    if not path.exists():
        return rows
    try:
        for line in path.read_text(errors="replace").strip().split("\n"):
            if line.strip():
                try:
                    rows.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return rows


def get_relevant_symbols():
    """Get symbols we care about: symbols_tradier watchlist."""
    syms = set()
    try:
        symbols_file = BASE / "symbols_tradier.json"
        if symbols_file.exists():
            d = json.loads(symbols_file.read_text())
            if isinstance(d, list):
                syms.update(s.upper() for s in d if isinstance(s, str) and len(s) <= 5)
    except Exception:
        pass
    return syms


def fetch_rss(url, timeout=6):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tree = ElementTree.parse(resp)
            return [{"title": item.find("title").text.strip(), "url": (item.find("link").text.strip() if item.find("link") is not None else ""), "date": (item.find("pubDate").text.strip() if item.find("pubDate") is not None else "")} for item in tree.findall(".//item") if item.find("title") is not None and item.find("title").text]
    except Exception:
        return []


# ── Section builders ─────────────────────────────────────────────────────────

def build_market_close(market_quotes):
    html = '<div class="box">'
    for sym in ["SPY", "QQQ", "DIA", "IWM"]:
        q = market_quotes.get(sym, {})
        last = q.get("last") or q.get("close", 0)
        prev = q.get("prevclose") or q.get("previous_close", 0)
        if last and prev:
            chg = (last - prev) / prev * 100
            c = "g" if chg > 0 else "r"
            html += f'<b>{sym}</b> ${last:.2f} <span class="{c} b">({chg:+.2f}%)</span>&nbsp;&nbsp;&nbsp;'
    vix = market_quotes.get("VIX", {})
    vix_last = vix.get("last") or vix.get("close", 0)
    if vix_last:
        vc = "r" if vix_last > 25 else "o" if vix_last > 18 else "g"
        html += f'<b>VIX</b> <span class="{vc}">{vix_last:.1f}</span>'
    html += "</div>"
    our_symbols = get_relevant_symbols()
    headlines = []
    for url, source in [("https://www.investing.com/rss/news.rss", "Investing.com"), ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC"), ("https://fortune.com/feed/", "Fortune"), ("https://www.benzinga.com/feed", "Benzinga")]:
        for item in fetch_rss(url)[:10]:
            item["source"] = source
            title_upper = item["title"].upper()
            item["_relevant"] = any(f" {sym} " in f" {title_upper} " or title_upper.startswith(f"{sym} ") or f"({sym})" in title_upper for sym in our_symbols)
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
    final = relevant[:6] + general[:max(2, 8 - len(relevant[:6]))]
    if final:
        html += "<h3>Closing Headlines</h3>"
        for h in final:
            link = h.get("url", "")
            title_html = f'<a href="{link}" style="color:#1a1a2e;text-decoration:none">{h["title"]}</a>' if link else h["title"]
            tag = ' <span class="pill pb">OUR STOCK</span>' if h.get("_relevant") else ""
            html += f'<div class="news">{title_html}{tag} <span class="gr">— {h.get("source","")}</span></div>'
    return html


def build_trades_section():
    """All trades that happened today across tra + trb."""
    html = ""
    for acct in ["tra", "trb"]:
        decisions = load_todays_decisions(acct)
        trade_actions = {"OPEN", "CLOSE", "AUGMENT", "REDUCE", "QUICK_OPEN", "QUICK_CLOSE", "QUICK_AUGMENT", "QUICK_REDUCE", "NOW_REDUCE", "STRONG_REDUCE", "\U0001f4a5CLOSE", "\U0001f680AUGMENT"}
        trades = [d for d in decisions if d.get("action", "") in trade_actions]
        actions = Counter(d.get("action", "?") for d in decisions)
        if not decisions:
            continue
        total = len(decisions)
        opens = sum(1 for d in decisions if "OPEN" in d.get("action", "") or "AUGMENT" in d.get("action", ""))
        closes = sum(1 for d in decisions if "CLOSE" in d.get("action", "") or "REDUCE" in d.get("action", ""))
        waits = actions.get("WAIT", 0)
        html += f'<h3>{acct} — {total} decisions ({opens} opens, {closes} closes, {waits} waits)</h3>'
        action_summary = ", ".join(f"{k}={v}" for k, v in sorted(actions.items(), key=lambda x: -x[1]) if k != "WAIT")
        html += f'<p style="font-size:11px" class="gr">{action_summary}</p>'
        if trades:
            html += "<table><tr><th>Action</th><th>Symbol</th><th>Gain%</th><th>Reason</th><th>Time</th></tr>"
            shown = 0
            for t in trades[-25:]:
                action = t.get("action", "?")
                sym = t.get("symbol", t.get("position_key", "?"))
                if ":" in sym:
                    sym = sym.split(":")[-1]
                gain = t.get("gain", "")
                reason = str(t.get("reason", ""))[:60]
                ts = t.get("timestamp", "")
                time_str = ts[11:19] if len(ts) > 19 else ts[-8:]
                is_close = "CLOSE" in action or "REDUCE" in action
                pill = "pr" if is_close else "pg"
                gc = ""
                gain_str = ""
                if gain and gain != "?":
                    try:
                        gf = float(gain)
                        gc = "g" if gf > 0 else "r"
                        gain_str = f"{gf:+.2f}%"
                    except (ValueError, TypeError):
                        gain_str = str(gain)
                html += f'<tr><td><span class="pill {pill}">{action}</span></td><td><b>{sym}</b></td><td style="text-align:right" class="{gc} b">{gain_str}</td><td style="font-size:11px">{reason}</td><td class="gr">{time_str}</td></tr>'
                shown += 1
            if len(trades) > 25:
                html += f'<tr><td colspan="5" class="gr">... and {len(trades)-25} more trades</td></tr>'
            html += "</table>"
    if not html:
        html = '<p class="gr">No trade activity today.</p>'
    return html


def build_eod_portfolio(data):
    acct = data["account_key"]
    positions = data["positions"]
    balance = data["balance"]
    if not positions:
        return '<p class="gr">No positions.</p>'
    total_equity = balance.get("total_equity", 0)
    open_pl = balance.get("open_pl", 0)
    close_pl = balance.get("close_pl", 0)
    total_day_pnl = sum(p["day_pnl"] for p in positions)
    pl_c = "g" if open_pl >= 0 else "r"
    day_c = "g" if total_day_pnl >= 0 else "r"
    cl_c = "g" if close_pl >= 0 else "r"
    html = '<div class="box">'
    html += f'<b>Equity:</b> ${total_equity:,.0f} &nbsp; <b>Open P/L:</b> <span class="{pl_c} b">${open_pl:+,.0f}</span> &nbsp; '
    html += f'<b>Day P/L:</b> <span class="{day_c} b">${total_day_pnl:+,.0f}</span> &nbsp; '
    html += f'<b>Realized Today:</b> <span class="{cl_c}">${close_pl:+,.0f}</span>'
    html += "</div>"
    html += "<table>"
    html += '<tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Avg</th><th>Close</th><th>Day %</th><th>Day $</th><th>Total %</th><th>Total $</th></tr>'
    for p in positions:
        gc = "g" if p["gain_pct"] > 0 else "r" if p["gain_pct"] < -1 else "o"
        dc = "g" if p["day_chg"] > 0 else "r" if p["day_chg"] < 0 else "gr"
        dpc = "g" if p["day_pnl"] > 0 else "r" if p["day_pnl"] < 0 else "gr"
        sp = "pg" if p["side"] == "LONG" else "pr"
        html += f'<tr><td><b>{p["symbol"]}</b></td><td><span class="pill {sp}">{p["side"]}</span></td><td style="text-align:right">{p["qty"]:.0f}</td><td style="text-align:right">${p["avg_cost"]:.2f}</td><td style="text-align:right">${p["last"]:.2f}</td><td style="text-align:right" class="{dc}">{p["day_chg"]:+.2f}%</td><td style="text-align:right" class="{dpc}">${p["day_pnl"]:+,.0f}</td><td style="text-align:right" class="{gc} b">{p["gain_pct"]:+.2f}%</td><td style="text-align:right" class="{gc}">${p["pnl"]:+,.0f}</td></tr>'
    long_val = sum(p["mkt_val"] for p in positions if p["side"] == "LONG")
    short_val = sum(p["mkt_val"] for p in positions if p["side"] == "SHORT")
    total_pnl = sum(p["pnl"] for p in positions)
    tc = "g" if total_pnl >= 0 else "r"
    html += f'<tr style="background:#e8eaf6;font-weight:700"><td>TOTAL</td><td></td><td></td><td></td><td></td><td></td><td style="text-align:right" class="{day_c}">${total_day_pnl:+,.0f}</td><td></td><td style="text-align:right" class="{tc}">${total_pnl:+,.0f}</td></tr>'
    html += "</table>"
    return html


def build_ticker_news_section(tra_data, trb_data):
    """Fetch today's news for all open tickers."""
    all_syms = set()
    for data in [tra_data, trb_data]:
        for p in data.get("positions", []):
            all_syms.add(p["symbol"])
    if not all_syms:
        return '<p class="gr">No ticker news.</p>'
    html = ""
    for sym in sorted(all_syms):
        url = f"https://news.google.com/rss/search?q={sym}+stock&hl=en-US&gl=US&ceid=US:en"
        items = fetch_rss(url, timeout=5)
        if items:
            for a in items[:2]:
                link = a.get("url", "")
                title_html = f'<a href="{link}" style="color:#1a1a2e;text-decoration:none">{a["title"]}</a>' if link else a["title"]
                html += f'<div class="news"><b>{sym}:</b> {title_html}</div>'
    if not html:
        html = '<p class="gr">No ticker news today.</p>'
    return html


def build_weekly_closed(tra_closed, trb_closed):
    """Weekly closed positions with P/L from Tradier gainloss API."""
    html = ""
    for acct, closed in [("tra", tra_closed), ("trb", trb_closed)]:
        if not closed:
            continue
        by_symbol = defaultdict(lambda: {"qty": 0, "cost": 0, "proceeds": 0, "gain_loss": 0, "trades": 0, "dates": set()})
        for c in closed:
            sym = c["symbol"]
            by_symbol[sym]["qty"] += abs(c["qty"])
            by_symbol[sym]["cost"] += abs(c["cost"])
            by_symbol[sym]["proceeds"] += abs(c["proceeds"])
            by_symbol[sym]["gain_loss"] += c["gain_loss"]
            by_symbol[sym]["trades"] += 1
            by_symbol[sym]["dates"].add(c["close_date"])
        total_gl = sum(v["gain_loss"] for v in by_symbol.values())
        winners = sum(1 for v in by_symbol.values() if v["gain_loss"] > 0)
        losers = sum(1 for v in by_symbol.values() if v["gain_loss"] <= 0)
        tc = "g" if total_gl >= 0 else "r"
        html += f'<h3>{acct} — {len(by_symbol)} symbols closed, <span class="{tc}">${total_gl:+,.2f}</span> net ({winners}W/{losers}L)</h3>'
        html += '<table><tr><th>Symbol</th><th>Trades</th><th>Shares</th><th>Cost</th><th>Proceeds</th><th>P/L $</th><th>P/L %</th><th>Dates</th></tr>'
        for sym, v in sorted(by_symbol.items(), key=lambda x: x[1]["gain_loss"]):
            gl = v["gain_loss"]
            gc = "g" if gl > 0 else "r"
            pct = (gl / v["cost"] * 100) if v["cost"] > 0 else 0
            dates = ", ".join(sorted(v["dates"]))[-20:]
            html += f'<tr><td><b>{sym}</b></td><td style="text-align:center">{v["trades"]}</td><td style="text-align:right">{v["qty"]:.0f}</td><td style="text-align:right">${v["cost"]:,.0f}</td><td style="text-align:right">${v["proceeds"]:,.0f}</td><td style="text-align:right" class="{gc} b">${gl:+,.2f}</td><td style="text-align:right" class="{gc}">{pct:+.2f}%</td><td class="gr" style="font-size:10px">{dates}</td></tr>'
        html += f'<tr style="background:#e8eaf6;font-weight:700"><td>TOTAL</td><td style="text-align:center">{sum(v["trades"] for v in by_symbol.values())}</td><td></td><td></td><td></td><td style="text-align:right" class="{tc}">${total_gl:+,.2f}</td><td></td><td></td></tr>'
        html += "</table>"
    if not html:
        html = '<p class="gr">No closed positions this week.</p>'
    return html


def build_options_activity():
    """Full options daily report: positions, GTC orders, daily cycle results, watchdog alerts."""
    html = ""
    # ── 1. Current option positions with put/call ratio ──
    watchdog_file = TRADIER_DIR / "options_watchdog_latest.json"
    if watchdog_file.exists():
        try:
            with open(watchdog_file) as f:
                wd = json.load(f)
            n_pos = wd.get("positions", 0)
            n_sell = wd.get("sell_signals", 0)
            n_gtc = wd.get("gtc_orders", 0)
            html += f'<p>Positions: <b>{n_pos}</b> &nbsp; Sell signals: <b>{n_sell}</b> &nbsp; GTC sells active: <b>{n_gtc}</b></p>'
        except Exception:
            pass
    # Get actual put/call ratio from account positions
    try:
        from config_tradier import TradierConfig
        import asyncio
        from tradier_api import TradierAPIClient
        cfg = TradierConfig()
        client = TradierAPIClient(config=cfg, account_key="trb")
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
        raw = loop.run_until_complete(client.get_account_positions("trb")) or []
        opts = [p for p in raw if len(p.get("symbol", "")) > 5]
        if opts:
            n_calls = sum(abs(float(p.get("quantity", 0))) for p in opts if "P" not in p.get("symbol", "")[6:])
            n_puts = sum(abs(float(p.get("quantity", 0))) for p in opts if "P" in p.get("symbol", "")[6:])
            call_cost = sum(abs(float(p.get("cost_basis", 0))) for p in opts if "P" not in p.get("symbol", "")[6:])
            put_cost = sum(abs(float(p.get("cost_basis", 0))) for p in opts if "P" in p.get("symbol", "")[6:])
            ratio_color = "g" if 0.3 <= (n_puts / (n_calls + n_puts + 0.001)) <= 0.7 else "o"
            html += f'<div class="box"><b>Put/Call Ratio:</b> <span class="{ratio_color} b">{n_calls:.0f}C : {n_puts:.0f}P</span> &nbsp; Calls ${call_cost:,.0f} / Puts ${put_cost:,.0f}</div>'
    except Exception:
        pass
    # ── 2. Active GTC buy orders (the daily cycle output) ──
    gtc_file = TRADIER_DIR / "options_gtc_orders.json"
    if gtc_file.exists():
        try:
            with open(gtc_file) as f:
                gtc = json.load(f)
            if gtc:
                buys = {k: v for k, v in gtc.items() if v.get("side") == "buy_to_open"}
                sells = {k: v for k, v in gtc.items() if v.get("side") != "buy_to_open"}
                if buys:
                    html += f'<h3>GTC Buy Orders ({len(buys)} pending — 7d fill window)</h3><table>'
                    html += '<tr><th>Contract</th><th>Qty</th><th>GTC Price</th><th>Age</th><th>Reason</th></tr>'
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
                        exp = info.get("expiration", "?")
                        pill = "pg" if typ == "CALL" else "pr"
                        html += f'<tr><td><b>{sym}</b> <span class="pill {pill}">{typ}</span> ${strike} {exp}</td><td style="text-align:center">1</td><td style="text-align:right">${info.get("target_price", 0):.2f}</td><td style="text-align:center">{age}</td><td style="font-size:11px">{info.get("reason", "")[:60]}</td></tr>'
                    html += '</table>'
                if sells:
                    html += f'<h3>GTC Sell Orders ({len(sells)} — sell at the top)</h3><table>'
                    html += '<tr><th>Contract</th><th>Target</th><th>Reason</th></tr>'
                    for occ, info in sorted(sells.items()):
                        html += f'<tr><td><b>{occ[:20]}</b></td><td style="text-align:right">${info.get("target_price", 0):.2f}</td><td style="font-size:11px">{info.get("reason", info.get("momentum_D", ""))[:50]}</td></tr>'
                    html += '</table>'
        except Exception:
            pass
    # ── 3. Daily cycle results ──
    plan_file = TRADIER_DIR / "options_daily_plan.json"
    if plan_file.exists():
        try:
            with open(plan_file) as f:
                plan = json.load(f)
            cycle_time = plan.get("cycle_time", "")
            if cycle_time:
                try:
                    ct = datetime.fromisoformat(cycle_time)
                    age_h = (datetime.now() - ct).total_seconds() / 3600
                    if age_h < 24:
                        n_selected = plan.get("selected", 0)
                        total_cost = plan.get("total_cost", 0)
                        calls = plan.get("calls", 0)
                        puts = plan.get("puts", 0)
                        results = plan.get("results", [])
                        placed = sum(1 for r in results if r.get("status") == "placed")
                        html += f'<h3>Daily Cycle ({ct.strftime("%H:%M")} UTC)</h3>'
                        html += f'<p>Selected {n_selected} opportunities (${total_cost:,.0f}) &nbsp; Placed: {placed} &nbsp; Portfolio: {calls:.0f} calls / {puts:.0f} puts</p>'
                        if results:
                            for r in results:
                                pill = "pg" if r.get("type") == "call" else "pr"
                                st_pill = "pg" if r.get("status") == "placed" else "po"
                                html += f'<div class="news"><span class="pill {pill}">{r.get("type","").upper()}</span> <b>{r.get("symbol","")}</b> ${r.get("strike","")} {r.get("expiration","")} @ ${r.get("gtc_price",0):.2f} — <span class="pill {st_pill}">{r.get("status","")}</span> {r.get("reason","")[:50]}</div>'
                except Exception:
                    pass
        except Exception:
            pass
    # ── 4. Watchdog alerts from today ──
    log_path = Path("/Users/niels/logs/options_watchdog.log")
    if log_path.exists():
        try:
            lines = log_path.read_text().strip().split("\n")
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_lines = [l for l in lines if today_str in l]
            alerts = []
            for line in today_lines:
                clean = line.replace("\033[91m", "").replace("\033[92m", "").replace("\033[93m", "").replace("\033[0m", "").strip()
                for kw in ["FILLED", "SELL", "CANCEL", "exit signal", "GTC"]:
                    if kw in clean:
                        alerts.append(clean.split(" - ")[-1].strip() if " - " in clean else clean)
                        break
            if alerts:
                html += '<h3>Watchdog Alerts</h3>'
                seen = set()
                for a in alerts[:10]:
                    k = a[:60]
                    if k not in seen:
                        seen.add(k)
                        html += f'<div class="news">{a}</div>'
        except Exception:
            pass
    if not html:
        html = '<p class="gr">No options activity today. Daily cycle may not have run.</p>'
    return html


# ── Assembly ─────────────────────────────────────────────────────────────────

def build_email_html(tra_data, trb_data, market_quotes, tra_closed, trb_closed):
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc - timedelta(hours=4)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h1>Evening Recap &mdash; {now_et.strftime('%A, %B %d %Y')} &middot; Market Close</h1>

<h2>Market Close</h2>
{build_market_close(market_quotes)}

<h2>Today's Trades</h2>
{build_trades_section()}

<h2>tra — EOD Positions</h2>
{build_eod_portfolio(tra_data)}

<h2>trb — EOD Positions (${trb_data['balance'].get('total_equity',0):,.0f} equity)</h2>
{build_eod_portfolio(trb_data)}

<h2>Closed Positions This Week</h2>
{build_weekly_closed(tra_closed, trb_closed)}

<h2>Ticker News Roundup</h2>
{build_ticker_news_section(tra_data, trb_data)}

<h2>Options Activity</h2>
{build_options_activity()}

<hr style="margin-top:30px;border:none;border-top:1px solid #ccc">
<p style="font-size:10px;color:#aaa">Generated {now_utc.strftime('%Y-%m-%d %H:%M')} UTC &mdash; Evening Recap by evening_email.py</p>
</body></html>"""
    return html


def send_email(html_body, to=None, subject=None):
    pwd = get_gmail_password()
    if not pwd:
        logger.error("No Gmail password")
        return False
    recipients = to if isinstance(to, list) else [to or TO_EMAIL]
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    subject = subject or f"Evening Recap — {now_et.strftime('%a %b %d')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText("Open in HTML email client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.starttls()
        server.login(FROM_EMAIL, pwd)
        server.sendmail(FROM_EMAIL, recipients, msg.as_string())
        server.quit()
        logger.info(f"Email sent to {', '.join(recipients)}")
        return True
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False


def build_public_evening_html(market_quotes, tra_data, trb_data):
    """Public evening version — market close, trades (% only), news. No dollar values."""
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
<h1>Market Recap &mdash; {now_et.strftime('%A, %B %d %Y')} &middot; Close</h1>

<h2>Market Close</h2>
{build_market_close(market_quotes)}

<h2>Active Positions — Day Performance</h2>"""
    if all_positions:
        html += '<table><tr><th>Symbol</th><th>Direction</th><th>Day %</th><th>Total %</th></tr>'
        for p in sorted(all_positions, key=lambda x: abs(x.get("day_pnl", 0) or x.get("day_chg", 0)), reverse=True):
            gc = "g" if p["gain_pct"] > 0 else "r" if p["gain_pct"] < -1 else "o"
            dc = "g" if p["day_chg"] > 0 else "r" if p["day_chg"] < 0 else "gr"
            sp = "pg" if p["side"] == "LONG" else "pr"
            html += f'<tr><td><b>{p["symbol"]}</b></td><td><span class="pill {sp}">{p["side"]}</span></td><td style="text-align:right" class="{dc}">{p["day_chg"]:+.1f}%</td><td style="text-align:right" class="{gc} b">{p["gain_pct"]:+.1f}%</td></tr>'
        html += "</table>"
    html += f"""
<h2>Ticker News</h2>
{build_ticker_news_section(tra_data, trb_data)}

<h2>Options Activity</h2>
{build_options_activity()}

<hr style="margin-top:30px;border:none;border-top:1px solid #ccc">
<p style="font-size:10px;color:#aaa">Generated {now_utc.strftime('%Y-%m-%d %H:%M')} UTC &mdash; Market analysis only, no account data</p>
</body></html>"""
    return html


async def gather_data():
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    cfg = TradierConfig()
    client = TradierAPIClient(config=cfg, account_key="trb")
    market_quotes = await client.get_quotes(["SPY", "QQQ", "DIA", "IWM", "VIX"])
    tra_data = await get_account_data("tra")
    trb_data = await get_account_data("trb")
    tra_closed = await get_weekly_closed("tra")
    trb_closed = await get_weekly_closed("trb")
    return tra_data, trb_data, market_quotes, tra_closed, trb_closed


def main():
    logger.info("=== Evening Recap Email ===")
    loop = asyncio.new_event_loop()
    tra_data, trb_data, market_quotes, tra_closed, trb_closed = loop.run_until_complete(gather_data())
    loop.close()
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    html = build_email_html(tra_data, trb_data, market_quotes, tra_closed, trb_closed)
    (DATA_DIR / "evening_email_latest.html").write_text(html)
    if send_email(html):
        logger.info("Private version sent")
    if PUBLIC_RECIPIENTS:
        pub_html = build_public_evening_html(market_quotes, tra_data, trb_data)
        (DATA_DIR / "evening_email_public.html").write_text(pub_html)
        subject = f"Market Recap — {now_et.strftime('%a %b %d')}"
        if send_email(pub_html, to=PUBLIC_RECIPIENTS, subject=subject):
            logger.info(f"Public version sent to {', '.join(PUBLIC_RECIPIENTS)}")


if __name__ == "__main__":
    main()
