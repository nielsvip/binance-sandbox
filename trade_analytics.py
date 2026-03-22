# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""Trade Analytics Dashboard — Flask + Plotly standalone app on port 5050."""
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "data" / "history"
TRADIER_HISTORY_DIR = BASE_DIR / "data" / "tradier" / "history"
LOGS_DIR = Path(os.environ.get("LOGS_DIR", "/home/niels/logs" if Path("/home/niels/logs").exists() else str(BASE_DIR / "logs")))
CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + STOCK_ACCOUNTS
TAIL_LINES = 2000
_OPT_RE = re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{4,}$')
def is_option(symbol):
    return bool(_OPT_RE.match((symbol or "").upper()))

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
CORS(app)

# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_all_history():
    """Scan data/history/*/*.jsonl and return list of trade events, deduplicating near-duplicates."""
    events = []
    if not HISTORY_DIR.exists():
        return events
    for account_dir in sorted(HISTORY_DIR.iterdir()):
        if not account_dir.is_dir():
            continue
        account = account_dir.name
        is_stock = account in STOCK_ACCOUNTS
        for jsonl_file in sorted(account_dir.glob("*.jsonl")):
            stem = jsonl_file.stem  # e.g. BTCUSDC_LONG
            parts = stem.rsplit("_", 1)
            symbol = parts[0] if len(parts) == 2 else stem
            side = parts[1] if len(parts) == 2 else "UNKNOWN"
            try:
                seen = set()
                with open(jsonl_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            dedup_key = (rec.get("ts", "")[:19], rec.get("type", ""), str(rec.get("qty", 0)))
                            if dedup_key in seen:
                                continue
                            seen.add(dedup_key)
                            events.append({"ts": rec.get("ts", ""), "type": rec.get("type", ""), "symbol": symbol, "side": side, "account": account, "qty": float(rec.get("qty", 0)), "price": float(rec.get("price", 0)), "value": float(rec.get("value", 0)), "reason": rec.get("reason", ""), "is_stock": is_stock})
                        except (json.JSONDecodeError, ValueError):
                            continue
            except Exception:
                continue
    return events


def load_tradier_history(accounts=None):
    """Load real broker fills from data/tradier/history/{account}/*.jsonl — these are written by tradier_positions.py with actual fill prices and quantities."""
    if accounts is None:
        accounts = STOCK_ACCOUNTS
    events = []
    if not TRADIER_HISTORY_DIR.exists():
        return events
    for account in accounts:
        acc_dir = TRADIER_HISTORY_DIR / account
        if not acc_dir.exists():
            continue
        for jsonl_file in sorted(acc_dir.glob("*.jsonl")):
            stem = jsonl_file.stem
            parts = stem.rsplit("_", 1)
            symbol = parts[0] if len(parts) == 2 else stem
            side = parts[1] if len(parts) == 2 else "UNKNOWN"
            try:
                seen = set()
                with open(jsonl_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            dedup_key = (rec.get("ts", "")[:19], rec.get("type", ""), str(rec.get("qty", 0)))
                            if dedup_key in seen:
                                continue
                            seen.add(dedup_key)
                            events.append({"ts": rec.get("ts", ""), "type": rec.get("type", ""), "symbol": symbol, "side": side, "account": account, "qty": float(rec.get("qty", 0)), "price": float(rec.get("price", 0)), "value": float(rec.get("value", 0)), "reason": rec.get("reason", ""), "is_stock": True})
                        except (json.JSONDecodeError, ValueError):
                            continue
            except Exception:
                continue
    events.sort(key=lambda e: e.get("ts", ""))
    return events


def parse_tradier_logs():
    """Parse tradier_actions.log for trades not yet in JSONL."""
    events = []
    log_path = LOGS_DIR / "tradier_actions.log"
    if not log_path.exists():
        return events
    # Format: "MM-DD HH:MM:SS - INFO - [acct] acct:SYM_SIDE: [TRADE] SENT: ✅ SYM  ACTION  BUY|SELL | Qty: N @ $P"
    pattern = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?(tr[bc]):(\w+)_(LONG|SHORT).*?\[TRADE\] SENT: ✅\s+(\w+)\s+(OPEN|AUGMENT|REDUCE|CLOSE)\s+(BUY|SELL)\s+\|\s+Qty:\s+(\d+\.?\d*)\s+@\s+\$([\d.]+)")
    try:
        with open(log_path, "r") as f:
            for line in f:
                m = pattern.search(line)
                if m:
                    ts_str, account, symbol, side, _, action, _, qty, price = m.groups()
                    ts_full = f"2026-{ts_str}"
                    events.append({"ts": ts_full, "type": action, "symbol": symbol, "side": side, "account": account, "qty": float(qty), "price": float(price), "value": round(float(qty) * float(price), 2), "reason": "tradier_log", "is_stock": True})
    except Exception:
        pass
    return events


def load_positions():
    """Load current position JSONs from all account dirs."""
    positions = {}
    for account in ALL_ACCOUNTS:
        acc_dir = BASE_DIR / account
        if not acc_dir.exists():
            continue
        for side_file in ["long_positions.json", "short_positions.json"]:
            path = acc_dir / side_file
            if not path.exists():
                continue
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                for key, pos in data.items():
                    pos["_key"] = key
                    pos["_account"] = account
                    pos["_is_stock"] = account in STOCK_ACCOUNTS
                    positions[key] = pos
            except Exception:
                continue
    return positions


def load_and_merge():
    """Merge all data sources into a unified event list, sorted by time."""
    events = load_all_history()
    log_events = parse_tradier_logs()
    seen_keys = {(e["ts"], e["account"], e["symbol"], e["side"], e["type"]) for e in events}
    for le in log_events:
        k = (le["ts"], le["account"], le["symbol"], le["side"], le["type"])
        if k not in seen_keys:
            events.append(le)
    events.sort(key=lambda e: e.get("ts", ""))
    return events

# ---------------------------------------------------------------------------
# Trade Reconstruction
# ---------------------------------------------------------------------------

def reconstruct_trades(events):
    """Group events by position key, match AUGMENT→REDUCE into round-trip trades."""
    grouped = defaultdict(list)
    for e in events:
        key = f"{e['account']}:{e['symbol']}_{e['side']}"
        grouped[key].append(e)
    trades = []
    for key, evts in grouped.items():
        evts.sort(key=lambda x: x.get("ts", ""))
        parts = key.split(":", 1)
        account = parts[0]
        rest = parts[1] if len(parts) > 1 else key
        rp = rest.rsplit("_", 1)
        symbol = rp[0] if len(rp) == 2 else rest
        side = rp[1] if len(rp) == 2 else "UNKNOWN"
        is_stock = account in STOCK_ACCOUNTS
        open_qty = 0.0
        open_cost = 0.0
        open_time = None
        open_reasons = []
        for ev in evts:
            t = ev["type"].upper()
            qty = ev["qty"]
            price = ev["price"]
            ts = ev["ts"]
            reason = ev.get("reason", "")
            if t in ("AUGMENT", "OPEN", "QUICK_OPEN", "REENTRY"):
                if open_qty == 0:
                    open_time = ts
                    open_reasons = []
                open_cost += qty * price
                open_qty += qty
                if reason:
                    open_reasons.append(reason)
            elif t in ("REDUCE", "CLOSE", "QUICK_CLOSE") and open_qty > 0:
                reduce_qty = min(qty, open_qty)
                entry_avg = open_cost / open_qty if open_qty > 0 else price
                mult = 100 if is_option(symbol) else 1
                if side == "LONG":
                    pnl = (price - entry_avg) * reduce_qty * mult
                else:
                    pnl = (entry_avg - price) * reduce_qty * mult
                pnl_pct = ((price - entry_avg) / entry_avg * 100) if entry_avg > 0 else 0
                if side == "SHORT":
                    pnl_pct = -pnl_pct
                duration_str = ""
                if open_time:
                    try:
                        t1 = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
                        t2 = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        dur = t2 - t1
                        hours = dur.total_seconds() / 3600
                        duration_str = f"{hours:.1f}h" if hours < 48 else f"{dur.days}d"
                    except Exception:
                        pass
                trades.append({"account": account, "symbol": symbol, "side": side, "entry_price": round(entry_avg, 6), "exit_price": round(price, 6), "qty": round(reduce_qty, 6), "pnl_usd": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "duration": duration_str, "reason": " | ".join(open_reasons[:3]) if open_reasons else reason, "open_time": open_time or "", "close_time": ts, "is_stock": is_stock})
                remaining = open_qty - reduce_qty
                if remaining > 0:
                    open_cost = entry_avg * remaining
                    open_qty = remaining
                else:
                    open_qty = 0.0
                    open_cost = 0.0
                    open_time = None
                    open_reasons = []
    trades.sort(key=lambda t: t.get("close_time", ""), reverse=True)
    return trades

# ---------------------------------------------------------------------------
# Analytics Engine
# ---------------------------------------------------------------------------

def _trading_day(dt_obj):
    """Return trading day string. Day boundary is 16:00 ET (20:00 UTC).
    Trades after 16:00 ET belong to the NEXT trading day."""
    utc_cutoff_hour = 20  # 16:00 ET = 20:00 UTC (EST; 19:00 during EDT)
    if dt_obj.hour >= utc_cutoff_hour:
        return (dt_obj + timedelta(days=1)).strftime("%Y-%m-%d")
    return dt_obj.strftime("%Y-%m-%d")


def compute_daily_pnl(trades):
    daily = defaultdict(float)
    minute_series = []
    for t in trades:
        try:
            dt_obj = datetime.fromisoformat(t["close_time"].replace("Z", "+00:00"))
            day = _trading_day(dt_obj)
        except Exception:
            continue
        daily[day] += t["pnl_usd"]
        minute_series.append({"ts": dt_obj.strftime("%Y-%m-%d %H:%M"), "pnl": t["pnl_usd"]})
    minute_series.sort(key=lambda x: x["ts"])
    cumulative = 0.0
    minute_cum = []
    for m in minute_series:
        cumulative += m["pnl"]
        minute_cum.append({"date": m["ts"], "pnl": round(m["pnl"], 2), "cumulative": round(cumulative, 2)})
    days = sorted(daily.keys())
    day_cum = 0.0
    daily_result = []
    for d in days:
        day_cum += daily[d]
        daily_result.append({"date": d, "pnl": round(daily[d], 2), "cumulative": round(day_cum, 2)})
    return minute_cum if minute_cum else daily_result


def compute_weekly_pnl(trades):
    weekly = defaultdict(float)
    for t in trades:
        try:
            dt_obj = datetime.fromisoformat(t["close_time"].replace("Z", "+00:00"))
            td = _trading_day(dt_obj)
            td_dt = datetime.strptime(td, "%Y-%m-%d")
            week = td_dt.strftime("%Y-W%W")
        except Exception:
            continue
        weekly[week] += t["pnl_usd"]
    weeks = sorted(weekly.keys())
    return [{"week": w, "pnl": round(weekly[w], 2)} for w in weeks]


def compute_strategy_stats(trades):
    strats = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "gains": [], "losses_list": []})
    for t in trades:
        reason = t.get("reason", "")
        strategy = _extract_strategy(reason)
        pnl = t["pnl_usd"]
        s = strats[strategy]
        s["total_pnl"] += pnl
        if pnl > 0:
            s["wins"] += 1
            s["gains"].append(pnl)
        else:
            s["losses"] += 1
            s["losses_list"].append(pnl)
    result = []
    for name, s in sorted(strats.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
        total = s["wins"] + s["losses"]
        win_rate = (s["wins"] / total * 100) if total > 0 else 0
        avg_gain = (sum(s["gains"]) / len(s["gains"])) if s["gains"] else 0
        avg_loss = (sum(s["losses_list"]) / len(s["losses_list"])) if s["losses_list"] else 0
        result.append({"strategy": name, "trades": total, "wins": s["wins"], "losses": s["losses"], "win_rate": round(win_rate, 1), "avg_gain": round(avg_gain, 2), "avg_loss": round(avg_loss, 2), "total_pnl": round(s["total_pnl"], 2), "status": "PROFITABLE" if s["total_pnl"] > 0 else "LOSING"})
    return result


def _extract_strategy(reason):
    if not reason:
        return "Unknown"
    reason_lower = reason.lower()
    keywords = ["gap_fill", "gap fill", "reentry", "re-entry", "double_down", "double down", "hedge", "scalp", "ladder", "cross", "storm", "trailing", "gain_protect", "sentiment", "rebalance", "ratio", "dc_breach", "manual"]
    for kw in keywords:
        if kw in reason_lower:
            return kw.replace(" ", "_").replace("-", "_").title()
    if "|" in reason:
        return reason.split("|")[0].strip()[:30]
    return reason[:30] if len(reason) > 30 else (reason or "Unknown")


def compute_account_stats(trades):
    accs = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "durations": []})
    for t in trades:
        a = accs[t["account"]]
        a["total_pnl"] += t["pnl_usd"]
        if t["pnl_usd"] > 0:
            a["wins"] += 1
        else:
            a["losses"] += 1
        if t.get("duration"):
            a["durations"].append(t["duration"])
    result = []
    for name, a in sorted(accs.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
        total = a["wins"] + a["losses"]
        win_rate = (a["wins"] / total * 100) if total > 0 else 0
        result.append({"account": name, "trades": total, "wins": a["wins"], "losses": a["losses"], "win_rate": round(win_rate, 1), "total_pnl": round(a["total_pnl"], 2), "avg_duration": a["durations"][len(a["durations"]) // 2] if a["durations"] else "N/A"})
    return result


def compute_symbol_stats(trades):
    syms = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0})
    for t in trades:
        s = syms[t["symbol"]]
        s["total_pnl"] += t["pnl_usd"]
        if t["pnl_usd"] > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1
    result = []
    for name, s in sorted(syms.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
        total = s["wins"] + s["losses"]
        win_rate = (s["wins"] / total * 100) if total > 0 else 0
        result.append({"symbol": name, "trades": total, "win_rate": round(win_rate, 1), "total_pnl": round(s["total_pnl"], 2)})
    return result


def compute_cumulative_by_account(trades):
    """Per-account cumulative PnL series for the chart."""
    acc_daily = defaultdict(lambda: defaultdict(float))
    for t in trades:
        try:
            dt_obj = datetime.fromisoformat(t["close_time"].replace("Z", "+00:00"))
            day = _trading_day(dt_obj)
        except Exception:
            continue
        acc_daily[t["account"]][day] += t["pnl_usd"]
    all_days = sorted({d for ad in acc_daily.values() for d in ad})
    result = {}
    for acc, daily in acc_daily.items():
        cum = 0.0
        series = []
        for d in all_days:
            cum += daily.get(d, 0)
            series.append({"date": d, "cumulative": round(cum, 2)})
        result[acc] = series
    return result


def generate_tips(strategy_stats, symbol_stats, account_stats, trades):
    tips = []
    for s in strategy_stats:
        if s["trades"] >= 5 and s["win_rate"] < 35 and s["total_pnl"] < 0:
            tips.append({"severity": "warning", "text": f"Strategy '{s['strategy']}' has {s['win_rate']}% win rate and ${s['total_pnl']:.0f} PnL over {s['trades']} trades — consider disabling or tuning."})
        elif s["trades"] >= 5 and s["win_rate"] > 65 and s["total_pnl"] > 0:
            tips.append({"severity": "success", "text": f"Strategy '{s['strategy']}' is strong: {s['win_rate']}% win rate, +${s['total_pnl']:.0f} over {s['trades']} trades."})
    winners = [s for s in symbol_stats if s["total_pnl"] > 0 and s["trades"] >= 3]
    losers = [s for s in symbol_stats if s["total_pnl"] < 0 and s["trades"] >= 3]
    if winners:
        best = winners[0]
        tips.append({"severity": "success", "text": f"Best symbol: {best['symbol']} — {best['win_rate']}% win rate, +${best['total_pnl']:.0f} over {best['trades']} trades."})
    if losers:
        worst = losers[-1]
        tips.append({"severity": "warning", "text": f"Worst symbol: {worst['symbol']} — {worst['win_rate']}% win rate, ${worst['total_pnl']:.0f} over {worst['trades']} trades."})
    if len(account_stats) >= 2:
        best_acc = max(account_stats, key=lambda a: a["win_rate"])
        worst_acc = min(account_stats, key=lambda a: a["win_rate"])
        if best_acc["account"] != worst_acc["account"]:
            tips.append({"severity": "info", "text": f"Account '{best_acc['account']}' has the highest win rate ({best_acc['win_rate']}%), while '{worst_acc['account']}' is lowest ({worst_acc['win_rate']}%)."})
    winning_trades = [t for t in trades if t["pnl_usd"] > 0]
    losing_trades = [t for t in trades if t["pnl_usd"] < 0]
    if winning_trades and losing_trades:
        avg_win = sum(t["pnl_usd"] for t in winning_trades) / len(winning_trades)
        avg_loss = abs(sum(t["pnl_usd"] for t in losing_trades) / len(losing_trades))
        if avg_loss > 0 and avg_loss > avg_win * 1.5:
            tips.append({"severity": "warning", "text": f"Average losing trade (${avg_loss:.2f}) is {avg_loss/avg_win:.1f}x your average winner (${avg_win:.2f}) — consider tightening stops."})
        elif avg_loss > 0 and avg_win > avg_loss * 1.5:
            tips.append({"severity": "success", "text": f"Good risk/reward: average winner (${avg_win:.2f}) is {avg_win/avg_loss:.1f}x your average loser (${avg_loss:.2f})."})
    if not tips:
        tips.append({"severity": "info", "text": "Not enough completed trades yet to generate actionable tips. Keep trading!"})
    return tips

# ---------------------------------------------------------------------------
# Log Monitoring — Signals, Executions, Blocks, Alerts
# ---------------------------------------------------------------------------

RE_ANSI = re.compile(r"\x1b\[[0-9;]*m")

def _tail_file(path, n=TAIL_LINES, strip_ansi=False):
    """Read last n lines of a file efficiently."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            avg_line = 800 if strip_ansi else 500
            block = min(size, n * avg_line)
            f.seek(max(0, size - block))
            data = f.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            result = lines[-n:]
            if strip_ansi:
                result = [RE_ANSI.sub("", l) for l in result]
            return result
    except Exception:
        return []

RE_SIGNAL = re.compile(r"\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(\w+)\] ([🚀🟢💥]+)\s+(STRONG_SELL|STRONG_BUY|GOOD_SELL|GOOD_BUY|NOW_REDUCE)\s+([🔴🔵💀]+)\s+(\w+:\w+_(?:LONG|SHORT))\s+.*?\|\s*([\d.]+)\s+\|.*?Sc:(\d+)\s+(.*)")
RE_EXECUTE = re.compile(r"\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] 🔄 \[EXECUTE\] (\w+:\w+_(?:LONG|SHORT)):\s+(AUGMENT|REDUCE|CLOSE)\s+\|\s+ReqQty=([\d.]+)\s+\|\s+RealAmt=([\d.]+)\s+\|\s+Gain=([-\d.]+)%")
RE_BLOCKED = re.compile(r"\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] 🛑 \[EXECUTE_BLOCKED\] (\w+:\w+_(?:LONG|SHORT)):\s+(AUGMENT|REDUCE)\s+rejected\.\s+(.+)")
RE_EXEC_FAILED = re.compile(r"\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[EXECUTE_FAILED\] (\w+:\w+_(?:LONG|SHORT))\s+(\w+)\s+(.*)")
RE_HEDGE_BLOCK = re.compile(r"(\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[HEDGE_MODE_BLOCK\]\s+(\w+:\w+_(?:LONG|SHORT)):\s+Blocking\s+(\w+)\s+\((.+?)\)")
RE_STRICT_BLOCK = re.compile(r"(\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[STRICT_NO_LOSS_BLOCK\]\[(\w+)\]\s+(\w+:\w+_(?:LONG|SHORT)).*?loss \(([-\d.]+)%\)")
RE_NOLOSS_BYPASS = re.compile(r"(\d{2}\s+\d{2}:\d{2}:\d{2}).*?\[STRICT_NO_LOSS_BYPASS\]\[(\w+)\]\s+(\w+:\w+_(?:LONG|SHORT))")
RE_WEBHOOK = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[(\w+)\].*?👷 \[(\w+:\w+_(?:LONG|SHORT))\] WEBHOOK_SENT")
RE_FILL = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[(\w+)\] (\w+:\w+_(?:LONG|SHORT)):.+Position was (augmented|reduced).*?Gain:\s*([-\d.]+).*?Reason:\s*(.+?)(?:\s*\||\s*$)")
RE_TRADIER_FILL = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[(\w+)\] (\w+:\w+_(?:LONG|SHORT)).*?\[TRADE\] SENT: ✅\s+(\w+)\s+(\w+)\s+(\w+)\s+\|\s+Qty:\s*(\d+)\s+@\s+\$([\d.]+)")
RE_TRADIER_SIGNAL = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]\s+([🚀🟢🛡️💥🔴⏳]+)\s+(\w+)\s+([🔴🔵💀]+)\s+(\w+:\w+_(?:LONG|SHORT))\s+.*?\|\s*([\d.]+)\s+\|.*?\|\s*Sn:([-\d]+)\s*\|.*?Sc:(\d+)\s+(.*?)(?:\s+\|\s+\$(\d+))?$")
RE_TRADIER_EXEC = re.compile(r"\[TRADE\] SENT: ✅\s+(\w+)\s+(\w+)\s+(\w+)\s+\|\s+Qty:\s*(\d+)\s+@\s+\$([\d.]+)\s+\|\s+Status:\s*(\w+)\s+\|\s+ID:\s*(.+)")
RE_TRADIER_BLOCKED = re.compile(r"✋ BLOCKED (\w+) (\w+):\s+(.+)")
RE_TRADIER_REBALANCE = re.compile(r"(📈|📉)\s+(\w+)\s+REBALANCE\s+(\w+):\s+(\w+)\s+ideal=(\d+)\s+cur=(\d+)")
RE_TRADIER_ENRICHED = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?(\w+:\w+_(?:LONG|SHORT)).*?\[TRADE\]\s+(\w+)\s+(OPEN|CLOSE|REDUCE|AUGMENT)\s+(BUY|SELL)\s+\|\s+Qty:\s*(\d+\.?\d*)\s+@\s+\$(\d+\.?\d*).*?Reason:\s*(.*?)\|.*?Gain:\s*([+\-\d.]+)%\s*\$([+\-\d.]+)\s*\|\s*Entry:\s*\$(\d+\.?\d*)\s+PosVal:\s*\$(\d+\.?\d*)")


def parse_signals(account, n=500):
    """Parse recent signals from ez_positions_quick_{acct}.log."""
    path = LOGS_DIR / f"ez_positions_quick_{account}.log"
    signals = []
    for line in _tail_file(path, n):
        m = RE_SIGNAL.search(line)
        if m:
            ts, acct, strength_emoji, signal_type, status_emoji, pos_key, price, score, reason = m.groups()
            signals.append({"ts": ts, "account": acct, "key": pos_key, "signal": signal_type, "price": float(price), "score": int(score), "reason": reason.strip()[:80], "has_position": "🔴" in status_emoji or "💀" in status_emoji, "critical": "💀" in status_emoji, "rocket": "🚀" in strength_emoji})
    return signals


def parse_executions(account, n=500):
    """Parse executions + blocks + failures from ez_positions_quick_general_{acct}.log."""
    path = LOGS_DIR / f"ez_positions_quick_general_{account}.log"
    execs = []
    blocks = []
    failures = []
    for line in _tail_file(path, n):
        m = RE_EXECUTE.search(line)
        if m:
            ts, pos_key, action, req_qty, real_amt, gain = m.groups()
            rq, ra = float(req_qty), float(real_amt)
            status = "sent" if ra > 0 else "zero_qty"
            execs.append({"ts": ts, "key": pos_key, "action": action, "req_qty": rq, "real_amt": ra, "gain": float(gain), "status": status})
            continue
        m = RE_EXEC_FAILED.search(line)
        if m:
            ts, pos_key, action_type, reason = m.groups()
            failures.append({"ts": ts, "key": pos_key, "action": action_type, "reason": reason.strip()[:80]})
            continue
        m = RE_BLOCKED.search(line)
        if m:
            ts, pos_key, action, reason = m.groups()
            blocks.append({"ts": ts, "key": pos_key, "action": action, "reason": reason.strip()[:80]})
    return execs, blocks, failures


def parse_confirmed_fills(n=1000):
    """Parse confirmed fills from actions.log (crypto) and tradier_actions.log."""
    fills = []
    path = LOGS_DIR / "actions.log"
    for line in _tail_file(path, n):
        m = RE_FILL.search(line)
        if m:
            ts, acct, pos_key, action, gain, reason = m.groups()
            fills.append({"ts": ts, "account": acct, "key": pos_key, "action": action, "gain": float(gain), "reason": reason.strip()[:60], "is_stock": False})
    path = LOGS_DIR / "tradier_actions.log"
    for line in _tail_file(path, n):
        m = RE_TRADIER_FILL.search(line)
        if m:
            ts, acct, pos_key, symbol, action, side, qty, price = m.groups()
            fills.append({"ts": ts, "account": acct, "key": pos_key, "action": action.lower(), "gain": 0.0, "reason": f"{action} {qty}@${price}", "is_stock": True})
    fills.sort(key=lambda f: f["ts"], reverse=True)
    return fills


def parse_webhooks_sent(account, n=500):
    """Parse webhook sends from ez_manage_{acct}.log."""
    path = LOGS_DIR / f"ez_manage_{account}.log"
    webhooks = []
    for line in _tail_file(path, n):
        m = RE_WEBHOOK.search(line)
        if m:
            ts, acct, pos_key = m.groups()
            webhooks.append({"ts": ts, "account": acct, "key": pos_key})
    return webhooks


def parse_tradier_signals(account, n=500):
    """Parse recent signals from tradier_manage_{acct}.log."""
    path = LOGS_DIR / f"tradier_manage_{account}.log"
    signals = []
    for line in _tail_file(path, n):
        m = RE_TRADIER_SIGNAL.search(line)
        if m:
            ts, emoji, signal_type, status_emoji, pos_key, price, sentiment, score, reason, usd = m.groups()
            signals.append({"ts": ts, "account": account, "key": pos_key, "signal": signal_type, "price": float(price), "score": int(score), "sentiment": int(sentiment), "reason": reason.strip()[:80], "usd": int(usd) if usd else 0, "has_position": "🔴" in status_emoji or "💀" in status_emoji, "critical": "💀" in status_emoji})
    return signals


def parse_tradier_executions(account, n=15000):
    """Parse executions + blocks from tradier_manage_{acct}.log."""
    execs = []
    blocks = []
    rebalances = []
    log_paths = [LOGS_DIR / f"tradier_manage_general_{account}.log", LOGS_DIR / f"tradier_manage_{account}.log"]
    for log_path in log_paths:
        for line in _tail_file(log_path, n, strip_ansi=True):
            m = RE_TRADIER_ENRICHED.search(line)
            if m:
                ts, pos_key, symbol, action, side, qty, price, reason, gain_pct, gain_dollar, entry, posval = m.groups()
                if len(ts) < 11: ts = f"2026-{ts}"
                ex_show_gain = action.upper() in ("REDUCE", "CLOSE", "QUICK_CLOSE")
                execs.append({"ts": ts, "symbol": symbol, "action": action, "side": side, "qty": int(float(qty)), "price": float(price), "status": "SUBMITTED", "order_id": "", "key": pos_key, "gain_pct": float(gain_pct) if ex_show_gain else 0.0, "gain_dollar": float(gain_dollar) if ex_show_gain else 0.0, "entry_price": float(entry), "reason": reason.strip()[:60]})
                continue
            m = RE_TRADIER_EXEC.search(line)
            if m:
                symbol, action, side, qty, price, status, order_id = m.groups()
                side_u = side.upper(); action_u = action.upper()
                pos_side = ("SHORT" if side_u == "BUY" else "LONG") if action_u in ("CLOSE", "REDUCE") else ("LONG" if side_u == "BUY" else "SHORT")
                execs.append({"ts": line[:19] if line[0] == "[" else line[:14], "symbol": symbol, "action": action, "side": side, "qty": int(qty), "price": float(price), "status": status, "order_id": order_id.strip()[:30], "key": f"{account}:{symbol}_{pos_side}"})
                continue
            m = RE_TRADIER_BLOCKED.search(line)
            if m:
                action, symbol, reason = m.groups()
                blocks.append({"ts": line[:19] if line[0] == '[' else line[:14], "symbol": symbol, "action": action, "reason": reason.strip()[:60], "key": f"{account}:{symbol}"})
                continue
            m = RE_TRADIER_REBALANCE.search(line)
            if m:
                direction, symbol, action, reason, ideal, cur = m.groups()
                rebalances.append({"ts": line[:19] if line[0] == '[' else line[:14], "symbol": symbol, "action": action, "reason": reason, "ideal": int(ideal), "current": int(cur), "direction": "UP" if direction == "📈" else "DOWN"})
    # Dedup executions — prefer enriched entries (with gain_pct) over old format
    seen_ex = {}
    for e in execs:
        dk = (e["ts"][-14:-3] if len(e["ts"]) > 14 else e["ts"][:11], e["symbol"], e["action"], e.get("side", ""))
        existing = seen_ex.get(dk)
        if existing is None or (e.get("gain_pct") is not None and existing.get("gain_pct") is None):
            seen_ex[dk] = e
    return list(seen_ex.values()), blocks, rebalances


def parse_tradier_confirmed_fills(n=2000):
    """Parse confirmed fills from tradier_actions.log.
    Gain% joined from 'Position was reduced/augmented' lines by (sym_side, minute)."""
    path = LOGS_DIR / "tradier_actions.log"
    lines = list(_tail_file(path, n))
    # Build gain lookup keyed by (sym_side, ts_minute) — ignores account prefix differences
    re_gain = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?(\w+:\w+_(?:LONG|SHORT)):.+Position was (?:augmented|reduced).*?Gain:\s*([-\d.]+)")
    gain_map = {}
    for line in lines:
        m = re_gain.search(line)
        if m:
            ts, pos_key, gain = m.groups()
            gain_map[(pos_key.split(":")[-1], ts[:14])] = float(gain)
    fills = []
    for line in lines:
        m = RE_TRADIER_FILL.search(line)
        if not m:
            continue
        ts, acct, pos_key, symbol, action, side, qty, price = m.groups()
        real_acct = pos_key.split(":")[0] if ":" in pos_key else acct
        qty_i, price_f = int(qty), float(price)
        gain_pct = gain_map.get((pos_key.split(":")[-1], ts[:14]), 0.0)
        gain_dollar = round(gain_pct / 100 * qty_i * price_f, 2) if gain_pct else 0.0
        act_lower = action.lower()
        show_gain = act_lower in ("reduce", "close", "quick_close", "sell")
        fills.append({"ts": ts, "account": real_acct, "key": pos_key, "symbol": symbol, "action": act_lower, "side": side, "qty": qty_i, "price": price_f, "value": round(qty_i * price_f, 2), "gain_pct": gain_pct if show_gain else 0.0, "gain_dollar": gain_dollar if show_gain else 0.0, "entry_price": 0.0})
    # Parse enriched [TRADE] format from tradier_manage_{acct}.log (has gain/entry/posval)
    for acct in ["trb", "trc"]:
        manage_path = LOGS_DIR / f"tradier_manage_{acct}.log"
        for line in _tail_file(manage_path, 5000, strip_ansi=True):
            m = RE_TRADIER_ENRICHED.search(line)
            if m:
                ts, pos_key, symbol, action, side, qty, price, reason, gain_pct, gain_dollar, entry, posval = m.groups()
                if len(ts) < 11: ts = f"2026-{ts}"
                real_acct = pos_key.split(":")[0] if ":" in pos_key else acct
                act_upper = action.upper()
                show_gain = act_upper in ("REDUCE", "CLOSE", "QUICK_CLOSE")
                fills.append({"ts": ts, "account": real_acct, "key": pos_key, "symbol": symbol, "action": action, "side": side, "qty": int(float(qty)), "price": float(price), "value": round(int(float(qty)) * float(price), 2), "gain_pct": float(gain_pct) if show_gain else 0.0, "gain_dollar": float(gain_dollar) if show_gain else 0.0, "entry_price": float(entry), "posval": float(posval), "reason": reason.strip()[:60]})
    fills.sort(key=lambda f: f["ts"], reverse=True)
    seen = set()
    deduped = []
    for f in fills:
        k = (f["ts"][:14], f["key"], f["action"])
        if k not in seen:
            seen.add(k)
            deduped.append(f)
    return deduped


def load_stock_decisions(accounts=None):
    """Load stock trade events from data/decisions/ JSONL files.
    Only includes records with trade details (qty/price). Falls back to log parsing for old data."""
    if accounts is None:
        accounts = STOCK_ACCOUNTS
    decisions_dir = BASE_DIR / "data" / "decisions"
    events = []
    fills = []
    seen_decisions = set()
    if not decisions_dir.exists():
        return events, fills
    for jsonl_file in sorted(decisions_dir.glob("decisions_*.jsonl")):
        acct = jsonl_file.stem.split("_")[1] if "_" in jsonl_file.stem else ""
        if acct not in accounts:
            continue
        try:
            with open(jsonl_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    action_raw = rec.get("action", "")
                    trade = rec.get("trade", {})
                    pk = rec.get("position_key", "")
                    ts = rec.get("timestamp", "")[:19]
                    parts = pk.rsplit("_", 1) if "_" in pk else (pk, "LONG")
                    sym_part = parts[0].split(":")[-1] if ":" in parts[0] else parts[0]
                    side = parts[1] if len(parts) == 2 else "LONG"
                    price = trade.get("price") or rec.get("indicators", {}).get("current_price", 0)
                    qty = trade.get("qty", 0)
                    action_type = trade.get("action_type", "")
                    if not action_type:
                        a = action_raw.upper().replace("🚀", "").replace("💥", "").replace("🟢", "").strip()
                        if "CLOSE" in a:
                            action_type = "CLOSE"
                        elif "AUGMENT" in a or "BUY" in a:
                            action_type = "AUGMENT"
                        elif "REDUCE" in a or "SELL" in a:
                            action_type = "REDUCE"
                        else:
                            continue
                    if action_type.upper() in ("OPEN", "AUGMENT", "REENTRY"):
                        ev_type = "AUGMENT"
                    elif action_type.upper() in ("CLOSE", "REDUCE", "QUICK_CLOSE"):
                        ev_type = "REDUCE"
                    else:
                        continue
                    if qty > 0 and price > 0:
                        dedup_k = (ts[:16], pk, ev_type, round(float(qty)))
                        if dedup_k in seen_decisions:
                            continue
                        seen_decisions.add(dedup_k)
                        entry_price = trade.get("entry_price", 0)
                        gain_pct = trade.get("gain_pct", 0)
                        is_exit = ev_type == "REDUCE"
                        gain_dollar = round(gain_pct / 100.0 * qty * price, 2) if (is_exit and gain_pct) else 0.0
                        events.append({"ts": ts, "type": ev_type, "symbol": sym_part, "side": side, "account": acct, "qty": float(qty), "price": float(price), "value": round(float(qty) * float(price), 2), "reason": rec.get("reason_text", ""), "is_stock": True})
                        fill_side = trade.get("side", "BUY" if ev_type == "AUGMENT" else "SELL")
                        fills.append({"ts": ts, "account": acct, "key": pk, "symbol": sym_part, "action": action_type, "side": fill_side, "qty": int(qty), "price": float(price), "value": round(float(qty) * float(price), 2), "gain_pct": round(gain_pct, 2) if is_exit else 0.0, "gain_dollar": gain_dollar if is_exit else 0.0, "entry_price": float(entry_price), "reason": rec.get("reason_text", "")[:60]})
        except Exception:
            continue
    events.sort(key=lambda e: e.get("ts", ""))
    fills.sort(key=lambda f: f["ts"], reverse=True)
    return events, fills


def build_stock_monitor():
    """Build stock-specific monitor for trb+trc accounts."""
    signals = parse_tradier_signals("trb", 500) + parse_tradier_signals("trc", 500)
    execs_trb, blocks_trb, rebalances_trb = parse_tradier_executions("trb")
    execs_trc, blocks_trc, rebalances_trc = parse_tradier_executions("trc")
    execs = execs_trb + execs_trc
    blocks = blocks_trb + blocks_trc
    rebalances = rebalances_trb + rebalances_trc
    # Primary: decisions JSONL (only actual trades)
    decision_events, decision_fills = load_stock_decisions(["trb", "trc"])
    # Fallback: log-based fills for older data without trade details in decisions
    log_fills = parse_tradier_confirmed_fills()
    # Merge fills — decisions take priority, fall back to log fills for data without trade details
    decision_fill_keys = {(f["ts"][:16], f["key"], f["action"].upper()) for f in decision_fills}
    merged_fills = list(decision_fills)
    for lf in log_fills:
        k = (lf["ts"][:16], lf["key"], lf["action"].upper())
        if k not in decision_fill_keys:
            merged_fills.append(lf)
    merged_fills.sort(key=lambda f: f["ts"], reverse=True)
    seen_fill = set()
    fills = []
    for f in merged_fills:
        dk = (f["ts"][:16], f["key"], f["action"].upper() if isinstance(f["action"], str) else f["action"])
        if dk not in seen_fill:
            seen_fill.add(dk)
            fills.append(f)
    positions = {}
    for acct in ["trb", "trc"]:
        for side_file in ["long_positions.json", "short_positions.json"]:
            path = BASE_DIR / acct / side_file
            if not path.exists():
                continue
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                for key, pos in data.items():
                    pos["_key"] = key
                    positions[key] = pos
            except Exception:
                continue
    alerts = []
    filled_keys = {f["key"] for f in fills}
    for ex in execs[-30:]:
        if ex["status"] == "filled" and ex["key"] not in filled_keys:
            alerts.append({"severity": "danger", "ts": ex["ts"], "key": ex["key"], "text": f"Trade {ex['action']} {ex['symbol']} {ex['qty']}@${ex['price']} filled but not in actions log"})
    for blk in blocks[-20:]:
        alerts.append({"severity": "warning", "ts": blk["ts"], "key": blk["key"], "text": f"Blocked {blk['action']} {blk['symbol']}: {blk['reason'][:50]}"})
    alerts.sort(key=lambda a: a["ts"], reverse=True)
    pos_list = []
    for key, pos in sorted(positions.items()):
        qty = float(pos.get("positionAmt", 0))
        if qty == 0:
            continue
        entry = float(pos.get("entry_price", 0))
        mark = float(pos.get("mark_price", 0))
        side = pos.get("position_side", "LONG")
        sym = pos.get("symbol", "")
        opt = is_option(sym)
        mult = 100 if opt else 1
        cur_val = qty * mark * mult
        pnl = (cur_val - qty * entry) if side == "LONG" else (qty * entry - cur_val)
        gain = round(((cur_val / qty - entry) / entry * 100) if (opt and entry > 0 and qty > 0) else pos.get("gain", 0), 2)
        pos_list.append({"key": key, "symbol": sym, "side": side, "qty": qty, "entry": entry, "mark": mark, "gain": gain, "max_gain": round(pos.get("max_gain", 0), 2), "value": round(cur_val, 2), "pnl": round(pnl, 2), "is_option": opt})
    stock_events = load_tradier_history()
    stock_events.sort(key=lambda e: e.get("ts", ""))
    stock_trades = reconstruct_trades(stock_events)
    stock_daily = compute_daily_pnl(stock_trades)
    stock_sym = compute_symbol_stats(stock_trades)
    total_pnl = sum(t["pnl_usd"] for t in stock_trades)
    total_unrealized = sum(p["pnl"] for p in pos_list)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_pnl = sum(t["pnl_usd"] for t in stock_trades if t.get("close_time", "").startswith(today_str))
    return {"signals": signals[-100:], "executions": execs[-50:], "blocks": blocks[-30:], "rebalances": rebalances[-30:], "fills": fills[:50], "positions": pos_list, "alerts": alerts[:30], "stock_trades": stock_trades[:100], "daily_pnl": stock_daily, "symbols": stock_sym, "pnl_summary": {"total_realized": round(total_pnl, 2), "total_unrealized": round(total_unrealized, 2), "today_realized": round(today_pnl, 2), "total_trades": len(stock_trades)}, "counts": {"signals": len(signals), "executions": len(execs), "blocks": len(blocks), "fills": len(fills), "positions": len(pos_list), "alerts_total": len(alerts)}}


_stock_cache = {"data": None, "ts": 0}
STOCK_TTL = 15
_last_stock_fill_ts = {"ts": ""}


def get_stock_monitor():
    now = datetime.now(timezone.utc).timestamp()
    if _stock_cache["data"] and now - _stock_cache["ts"] < STOCK_TTL:
        return _stock_cache["data"]
    result = build_stock_monitor()
    _stock_cache["data"] = result
    _stock_cache["ts"] = now
    return result


def parse_denials(account, n=2000):
    """Parse denial reasons from ez_manage_{acct}.log — hedge blocks, strict no-loss, etc."""
    path = LOGS_DIR / f"ez_manage_{account}.log"
    denials = defaultdict(int)
    denial_details = []
    for line in _tail_file(path, n, strip_ansi=True):
        m = RE_HEDGE_BLOCK.search(line)
        if m:
            ts, pos_key, action, reason = m.groups()
            denials["HEDGE_MODE_BLOCK"] += 1
            denial_details.append({"ts": ts.replace("  ", " "), "key": pos_key, "type": "HEDGE_BLOCK", "detail": f"Blocking {action}: {reason[:40]}"})
            continue
        m = RE_STRICT_BLOCK.search(line)
        if m:
            ts, acct, pos_key, loss_pct = m.groups()
            denials["STRICT_NO_LOSS"] += 1
            denial_details.append({"ts": ts.replace("  ", " "), "key": pos_key, "type": "NO_LOSS_BLOCK", "detail": f"Loss {loss_pct}%"})
            continue
    return denials, denial_details[-50:]


def build_execution_monitor():
    """Cross-reference signals → executions → fills to find gaps and classify denials."""
    all_signals = []
    all_execs = []
    all_blocks = []
    all_failures = []
    all_denial_counts = defaultdict(int)
    all_denial_details = []
    for acct in CRYPTO_ACCOUNTS:
        sigs = parse_signals(acct, 300)
        all_signals.extend(sigs)
        exs, blks, fails = parse_executions(acct, 300)
        all_execs.extend(exs)
        all_blocks.extend(blks)
        all_failures.extend(fails)
        dcounts, ddetails = parse_denials(acct, 500)
        for k, v in dcounts.items():
            all_denial_counts[k] += v
        all_denial_details.extend(ddetails)
    fills = parse_confirmed_fills(1000)
    filled_keys = {f["key"] for f in fills}
    executed_keys = {e["key"] for e in all_execs}
    alerts = []
    sent_execs = [e for e in all_execs if e["status"] == "sent"]
    zero_execs = [e for e in all_execs if e["status"] == "zero_qty"]
    for sig in [s for s in all_signals if s["rocket"]][-50:]:
        if sig["key"] not in executed_keys:
            alerts.append({"severity": "warning", "ts": sig["ts"], "key": sig["key"], "text": f"Signal {sig['signal']} Sc:{sig['score']} NOT executed — {sig['reason'][:50]}"})
    for ex in sent_execs[-50:]:
        if ex["key"] not in filled_keys:
            alerts.append({"severity": "danger", "ts": ex["ts"], "key": ex["key"], "text": f"{ex['action']} qty={ex['req_qty']} SENT but no fill confirmed"})
    for ex in zero_execs[-20:]:
        alerts.append({"severity": "info", "ts": ex["ts"], "key": ex["key"], "text": f"{ex['action']} RealAmt=0 (retention protection / no qty left)"})
    for f in all_failures[-20:]:
        alerts.append({"severity": "warning", "ts": f["ts"], "key": f["key"], "text": f"FAILED: {f['action']} — {f['reason'][:50]}"})
    for blk in all_blocks[-15:]:
        alerts.append({"severity": "info", "ts": blk["ts"], "key": blk["key"], "text": f"Blocked {blk['action']}: {blk['reason'][:50]}"})
    alerts.sort(key=lambda a: a["ts"], reverse=True)
    denial_summary = []
    denial_summary.append({"reason": "RealAmt=0 (retention/no qty)", "count": len(zero_execs), "severity": "info"})
    for f in all_failures:
        reason_key = f["reason"].split(" ")[0][:30] if f["reason"] else "Unknown"
        all_denial_counts[f"FAILED:{reason_key}"] += 1
    for reason, count in sorted(all_denial_counts.items(), key=lambda x: -x[1]):
        sev = "warning" if count > 20 else "info"
        denial_summary.append({"reason": reason, "count": count, "severity": sev})
    return {"signals": all_signals[-100:], "executions": all_execs[-100:], "blocks": all_blocks[-50:], "failures": all_failures[-50:], "fills": fills[:100], "alerts": alerts[:50], "denial_summary": denial_summary, "denial_details": all_denial_details[-50:], "counts": {"signals": len(all_signals), "executions_sent": len(sent_execs), "executions_zero": len(zero_execs), "failures": len(all_failures), "blocks": len(all_blocks), "fills": len(fills), "alerts_total": len(alerts)}}

_monitor_cache = {"data": None, "ts": 0}
MONITOR_TTL = 30

def get_monitor():
    now = datetime.now(timezone.utc).timestamp()
    if _monitor_cache["data"] and now - _monitor_cache["ts"] < MONITOR_TTL:
        return _monitor_cache["data"]
    result = build_execution_monitor()
    _monitor_cache["data"] = result
    _monitor_cache["ts"] = now
    return result

# ---------------------------------------------------------------------------
# Cache (simple in-memory, refreshes every 60s)
# ---------------------------------------------------------------------------

_cache = {"data": None, "ts": 0}
CACHE_TTL = 60


def get_analytics():
    now = datetime.now(timezone.utc).timestamp()
    if _cache["data"] and now - _cache["ts"] < CACHE_TTL:
        return _cache["data"]
    events = [e for e in load_and_merge() if not e.get("is_stock")]
    trades = reconstruct_trades(events)
    positions = load_positions()
    daily = compute_daily_pnl(trades)
    weekly = compute_weekly_pnl(trades)
    strat_stats = compute_strategy_stats(trades)
    acc_stats = compute_account_stats(trades)
    sym_stats = compute_symbol_stats(trades)
    cumulative_by_acc = compute_cumulative_by_account(trades)
    tips = generate_tips(strat_stats, sym_stats, acc_stats, trades)
    # PnL from position files is the source of truth for active positions
    crypto_pos = {k: v for k, v in positions.items() if v.get("_account") in CRYPTO_ACCOUNTS and float(v.get("positionAmt", 0)) != 0}
    pos_realized = sum(float(v.get("realized_pnl") or 0) for v in crypto_pos.values())
    pos_unrealized = sum(float(v.get("unrealized_pnl") or 0) for v in crypto_pos.values())
    total_pnl = pos_realized + pos_unrealized
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_pnl = sum(t["pnl_usd"] for t in trades if t.get("close_time", "").startswith(today_str))
    week_start = (datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())).strftime("%Y-%m-%d")
    week_pnl = sum(t["pnl_usd"] for t in trades if t.get("close_time", "")[:10] >= week_start)
    total_trades = len(trades)
    win_rate = (sum(1 for t in trades if t["pnl_usd"] > 0) / total_trades * 100) if total_trades > 0 else 0
    active_positions = len(crypto_pos)
    result = {"summary": {"total_pnl": round(total_pnl, 2), "today_pnl": round(today_pnl, 2), "week_pnl": round(week_pnl, 2), "total_trades": total_trades, "win_rate": round(win_rate, 1), "active_positions": active_positions, "pos_realized": round(pos_realized, 2), "pos_unrealized": round(pos_unrealized, 2)}, "daily_pnl": daily, "weekly_pnl": weekly, "trades": trades[:200], "strategies": strat_stats, "accounts": acc_stats, "symbols": sym_stats, "cumulative_by_account": cumulative_by_acc, "tips": tips, "total_events": len(events)}
    _cache["data"] = result
    _cache["ts"] = now
    return result

# ---------------------------------------------------------------------------
# Flask Routes
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    data = get_analytics()
    data["monitor"] = get_monitor()
    return render_template("dashboard.html", data=data)


@app.route("/api/summary")
def api_summary():
    data = get_analytics()
    return jsonify(data["summary"])


@app.route("/api/daily_pnl")
def api_daily_pnl():
    data = get_analytics()
    return jsonify(data["daily_pnl"])


@app.route("/api/trades")
def api_trades():
    data = get_analytics()
    trades = data["trades"]
    account = request.args.get("account")
    symbol = request.args.get("symbol")
    if account:
        trades = [t for t in trades if t["account"] == account]
    if symbol:
        trades = [t for t in trades if t["symbol"].upper() == symbol.upper()]
    return jsonify(trades)


@app.route("/api/strategies")
def api_strategies():
    data = get_analytics()
    return jsonify(data["strategies"])


@app.route("/api/tips")
def api_tips():
    data = get_analytics()
    return jsonify(data["tips"])


@app.route("/api/monitor")
def api_monitor():
    return jsonify(get_monitor())


@app.route("/api/monitor/alerts")
def api_monitor_alerts():
    data = get_monitor()
    return jsonify(data["alerts"])


@app.route("/stocks")
def stocks_dashboard():
    data = get_stock_monitor()
    return render_template("stocks.html", data=data)


@app.route("/api/stocks")
def api_stocks():
    return jsonify(get_stock_monitor())


@app.route("/api/stocks/new_fills")
def api_stock_new_fills():
    """Return fills newer than the client's last seen timestamp."""
    since = request.args.get("since", "")
    data = get_stock_monitor()
    if not since:
        return jsonify(data["fills"][:5])
    new_fills = [f for f in data["fills"] if f["ts"] > since]
    return jsonify(new_fills)


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------

POLY_DIR = BASE_DIR / "data" / "poly" / "highconf"
LIMITLESS_DIR = BASE_DIR / "data" / "poly" / "limitless_trader"

def _tail_jsonl(path, n=400):
    """Read last n lines of a file efficiently without loading the whole thing."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        buf = b""
        pos = size
        while pos > 0 and buf.count(b"\n") < n + 1:
            read_size = min(8192, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
        lines = buf.decode("utf-8", errors="ignore").splitlines()
        return lines[-n:]

def get_poly_data():
    data = {"positions": [], "closed": [], "executions": [], "opportunities": [], "paper_closed": [], "stats": {}}
    live_file = POLY_DIR / "live_positions.json"
    if live_file.exists():
        try:
            d = json.loads(live_file.read_text())
            data["positions"] = [{"market_id": k, **v} for k, v in d.get("positions", {}).items()]
            data["closed"] = d.get("closed", [])
        except Exception:
            pass
    exec_file = POLY_DIR / "executions.jsonl"
    if exec_file.exists():
        try:
            execs = []
            for line in exec_file.read_text().splitlines():
                if line.strip():
                    try:
                        execs.append(json.loads(line))
                    except Exception:
                        pass
            data["executions"] = list(reversed(execs))
        except Exception:
            pass
    paper_file = POLY_DIR / "paper_positions.json"
    if paper_file.exists():
        try:
            d = json.loads(paper_file.read_text())
            cl = d.get("closed", [])
            data["paper_closed"] = sorted(cl, key=lambda x: x.get("closed_at", ""), reverse=True)[:50]
        except Exception:
            pass
    opp_file = POLY_DIR / "opportunities.jsonl"
    if opp_file.exists():
        try:
            raw_lines = _tail_jsonl(opp_file, 400)
            opps = []
            latest_ts = ""
            for line in raw_lines:
                if line.strip():
                    try:
                        o = json.loads(line)
                        ts = o.get("scanned_at", "")
                        if ts > latest_ts:
                            latest_ts = ts
                        opps.append(o)
                    except Exception:
                        pass
            if latest_ts:
                cutoff = latest_ts[:15]
                opps = [o for o in opps if o.get("scanned_at", "")[:15] == cutoff]
            data["opportunities"] = sorted(opps, key=lambda o: (-int(o.get("past_end", False)), -int(o.get("expiring_soon", False)), -o.get("last_price", 0)))
        except Exception:
            pass
    live_pos = data["positions"]
    cl = data["closed"]
    wins = sum(1 for c in cl if c.get("exit_price", 0) >= 0.5)
    losses = sum(1 for c in cl if c.get("exit_price", 0) < 0.5)
    pnl = sum(c.get("pnl_usdc", 0) for c in cl)
    data["stats"] = {"open_count": len(live_pos), "exposure": round(sum(p.get("bet_usdc", 0) for p in live_pos), 2), "wins": wins, "losses": losses, "pnl": round(pnl, 4), "opp_count": len(data["opportunities"]), "past_end_count": sum(1 for o in data["opportunities"] if o.get("past_end")), "expiring_count": sum(1 for o in data["opportunities"] if o.get("expiring_soon") and not o.get("past_end"))}
    return data


@app.route("/polymarket")
def polymarket_dashboard():
    return render_template("polymarket.html", data=get_poly_data())


@app.route("/api/polymarket")
def api_polymarket():
    return jsonify(get_poly_data())


def get_limitless_data():
    data = {"positions": [], "closed": [], "trades": [], "alerts": [], "stats": {}, "spreads": []}
    pos_file = LIMITLESS_DIR / "positions.json"
    if pos_file.exists():
        try:
            d = json.loads(pos_file.read_text())
            data["positions"] = [{"market_id": k, **v} for k, v in d.get("positions", {}).items()]
            data["closed"] = sorted(d.get("closed", []), key=lambda x: x.get("closed_at", ""), reverse=True)
        except Exception:
            pass
    trades_file = LIMITLESS_DIR / "trades.jsonl"
    if trades_file.exists():
        try:
            trades = []
            for line in _tail_jsonl(trades_file, 200):
                if line.strip():
                    try:
                        trades.append(json.loads(line))
                    except Exception:
                        pass
            data["trades"] = list(reversed(trades))
        except Exception:
            pass
    alerts_file = LIMITLESS_DIR / "alerts.json"
    if alerts_file.exists():
        try:
            d = json.loads(alerts_file.read_text())
            data["alerts"] = d.get("edges", [])
        except Exception:
            pass
    stats_file = LIMITLESS_DIR / "stats.json"
    if stats_file.exists():
        try:
            data["stats"] = json.loads(stats_file.read_text())
        except Exception:
            pass
    cl = data["closed"]
    wins = [c for c in cl if c.get("pnl_usdc", 0) > 0]
    losses = [c for c in cl if c.get("pnl_usdc", 0) <= 0]
    total_pnl = sum(c.get("pnl_usdc", 0) for c in cl)
    total_bet = sum(c.get("bet_usdc", 0) for c in cl)
    data["summary"] = {"total": len(cl), "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / len(cl) * 100, 1) if cl else 0, "total_pnl": round(total_pnl, 2), "roi_pct": round(total_pnl / total_bet * 100, 2) if total_bet else 0, "avg_win": round(sum(w.get("pnl_usdc", 0) for w in wins) / len(wins), 2) if wins else 0, "avg_loss": round(sum(l.get("pnl_usdc", 0) for l in losses) / len(losses), 2) if losses else 0, "open_count": len(data["positions"]), "exposure": round(sum(p.get("bet_usdc", 0) for p in data["positions"]), 2), "by_ticker": {}, "by_side": {}}
    for ticker in set(c.get("ticker", "?") for c in cl):
        tc = [c for c in cl if c.get("ticker") == ticker]
        tw = [c for c in tc if c.get("pnl_usdc", 0) > 0]
        data["summary"]["by_ticker"][ticker] = {"total": len(tc), "wins": len(tw), "pnl": round(sum(c.get("pnl_usdc", 0) for c in tc), 2)}
    for side in ["YES", "NO"]:
        sc = [c for c in cl if c.get("side") == side]
        sw = [c for c in sc if c.get("pnl_usdc", 0) > 0]
        data["summary"]["by_side"][side] = {"total": len(sc), "wins": len(sw), "pnl": round(sum(c.get("pnl_usdc", 0) for c in sc), 2)}
    return data


@app.route("/limitless")
def limitless_dashboard():
    return render_template("limitless.html", data=get_limitless_data())


@app.route("/api/limitless")
def api_limitless():
    return jsonify(get_limitless_data())


# ---------------------------------------------------------------------------
# NEWS SCANNER SUCCESS TRACKER
# ---------------------------------------------------------------------------

NEWS_TRACK_DIR = BASE_DIR / "data" / "news_tracker"
NEWS_TRACK_INDEX = NEWS_TRACK_DIR / "index.json"
NEWS_INJECTIONS_FILE = BASE_DIR / "data" / "news_injections.json"


def get_news_tracker_data():
    """Load news tracker index + snapshot data for dashboard."""
    recs = []
    try:
        with open(NEWS_TRACK_INDEX) as f:
            idx = json.load(f)
        recs = idx.get('recommendations', [])
    except Exception:
        pass
    injections = {'active': [], 'history': []}
    try:
        with open(NEWS_INJECTIONS_FILE) as f:
            injections = json.load(f)
    except Exception:
        pass
    active = [r for r in recs if r.get('status') == 'active']
    completed = [r for r in recs if r.get('status') == 'completed']
    total = len(recs)
    if completed:
        winners = sum(1 for r in completed if (r.get('final_pnl_pct') or 0) > 0)
        avg_pnl = sum(r.get('final_pnl_pct', 0) for r in completed) / len(completed)
        total_pnl = sum(r.get('final_pnl_pct', 0) for r in completed)
        win_rate = winners / len(completed) * 100
        best = max(completed, key=lambda r: r.get('final_pnl_pct', 0))
        worst = min(completed, key=lambda r: r.get('final_pnl_pct', 0))
    else:
        winners = avg_pnl = total_pnl = win_rate = 0
        best = worst = None
    return {
        'total': total, 'active_count': len(active), 'completed_count': len(completed),
        'winners': winners, 'win_rate': round(win_rate, 1), 'avg_pnl': round(avg_pnl, 3),
        'total_pnl': round(total_pnl, 2),
        'best': best, 'worst': worst,
        'active': sorted(active, key=lambda r: r.get('recommended_at', ''), reverse=True),
        'completed': sorted(completed, key=lambda r: r.get('final_pnl_pct', 0), reverse=True),
        'injection_active': injections.get('active', []),
        'injection_history': injections.get('history', [])[-50:],
    }


def get_news_snapshots(track_id: str):
    """Load price snapshot JSONL for a specific recommendation."""
    snap_file = NEWS_TRACK_DIR / f"{track_id}.jsonl"
    snapshots = []
    try:
        with open(snap_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    snapshots.append(json.loads(line))
    except Exception:
        pass
    return snapshots


@app.route("/news")
def news_dashboard():
    return render_template("news.html", data=get_news_tracker_data())


@app.route("/api/news")
def api_news():
    return jsonify(get_news_tracker_data())


@app.route("/api/news/snapshots/<track_id>")
def api_news_snapshots(track_id):
    return jsonify(get_news_snapshots(track_id))


@app.route("/api/news/all_charts")
def api_news_all_charts():
    """Return snapshot data for all recommendations (for batch chart rendering)."""
    data = get_news_tracker_data()
    charts = {}
    for rec in data['active'] + data['completed']:
        tid = rec.get('track_id', '')
        if tid:
            snaps = get_news_snapshots(tid)
            if snaps:
                charts[tid] = {'rec': rec, 'snapshots': snaps}
    return jsonify(charts)


if __name__ == "__main__":
    print(f"Trade Analytics Dashboard starting on http://0.0.0.0:5050")
    print(f"History dir: {HISTORY_DIR}")
    print(f"Accounts: {ALL_ACCOUNTS}")
    app.run(host="0.0.0.0", port=5050, debug=False)
