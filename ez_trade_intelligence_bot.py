import os
import sys
import glob
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
DB_PATH = "/users/niels/documents/binance/data/autonomous/trade_intelligence.db"
REPORT_PATH = "/users/niels/documents/binance/data/autonomous/trade_intelligence_report.md"
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS raw_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, symbol TEXT, side TEXT, ts TEXT, type TEXT, qty REAL, price REAL, value REAL, reason TEXT, indicators TEXT)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS completed_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, symbol TEXT, side TEXT, entry_time TEXT, exit_time TEXT, qty REAL, entry_price REAL, exit_price REAL, pnl_pct REAL, duration_s INTEGER, entry_reason TEXT, exit_reason TEXT, entry_indicators TEXT, exit_indicators TEXT)""")
    conn.commit()
    conn.close()
def parse_iso(ts_str):
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
def process_trade_files():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM raw_trades")
    cursor.execute("DELETE FROM completed_trades")
    base_dir = Path("/users/niels/documents/binance/data/history")
    files = glob.glob(str(base_dir / "**/*.jsonl"), recursive=True)
    trades_by_key = {}
    for fpath in files:
        p = Path(fpath)
        if p.name == ".DS_Store" or p.name.endswith(".bak") or "struct_demo" in str(p) or "struct_v4" in str(p): continue
        try:
            account = p.parent.name
            fname = p.stem
            symbol, side = fname.split("_")
        except Exception:
            continue
        key = (account, symbol, side)
        if key not in trades_by_key: trades_by_key[key] = []
        with open(fpath, "r") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    t = json.loads(line)
                    t["account"] = account
                    t["symbol"] = symbol
                    t["side"] = side
                    trades_by_key[key].append(t)
                except Exception:
                    pass
    total_raw, total_completed = 0, 0
    for key, t_list in trades_by_key.items():
        account, symbol, side = key
        t_list.sort(key=lambda x: x["ts"])
        active_entries = []
        for t in t_list:
            ts, t_type, qty, price, val, reason = t["ts"], t["type"], float(t.get("qty", 0)), float(t.get("price", 0)), float(t.get("value", 0)), t.get("reason", "Unknown")
            ind_str = json.dumps(t.get("indicators", {}))
            cursor.execute("INSERT INTO raw_trades (account, symbol, side, ts, type, qty, price, value, reason, indicators) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (account, symbol, side, ts, t_type, qty, price, val, reason, ind_str))
            total_raw += 1
            if t_type in ["OPEN", "AUGMENT"]:
                active_entries.append({"ts": ts, "qty": qty, "price": price, "reason": reason, "indicators": t.get("indicators", {})})
            elif t_type in ["REDUCE", "CLOSE", "REVERSE"]:
                qty_rem = qty
                while qty_rem > 1e-6 and active_entries:
                    match = active_entries[0]
                    m_qty = min(match["qty"], qty_rem)
                    entry_val = m_qty * match["price"]
                    exit_val = m_qty * price
                    pnl = (exit_val - entry_val) if side == "LONG" else (entry_val - exit_val)
                    pnl_pct = pnl / entry_val if entry_val > 0 else 0.0
                    try:
                        dur = int((parse_iso(ts) - parse_iso(match["ts"])).total_seconds())
                    except Exception:
                        dur = 0
                    cursor.execute("INSERT INTO completed_trades (account, symbol, side, entry_time, exit_time, qty, entry_price, exit_price, pnl_pct, duration_s, entry_reason, exit_reason, entry_indicators, exit_indicators) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (account, symbol, side, match["ts"], ts, m_qty, match["price"], price, pnl_pct, dur, match["reason"], reason, json.dumps(match["indicators"]), ind_str))
                    total_completed += 1
                    match["qty"] -= m_qty
                    if match["qty"] < 1e-6: active_entries.pop(0)
                    qty_rem -= m_qty
    conn.commit()
    conn.close()
    return total_raw, total_completed
def generate_report():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), AVG(pnl_pct), SUM(pnl_pct) FROM completed_trades")
    total_trades, avg_pnl, sum_pnl = cursor.fetchone()
    if not total_trades: total_trades, avg_pnl, sum_pnl = 0, 0.0, 0.0
    cursor.execute("SELECT COUNT(*) FROM completed_trades WHERE pnl_pct > 0")
    wins = cursor.fetchone()[0] or 0
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    cursor.execute("SELECT entry_reason, COUNT(*), AVG(pnl_pct) * 100, SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) FROM completed_trades GROUP BY entry_reason ORDER BY COUNT(*) DESC LIMIT 20")
    entry_stats = cursor.fetchall()
    cursor.execute("SELECT exit_reason, COUNT(*), AVG(pnl_pct) * 100, SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) FROM completed_trades GROUP BY exit_reason ORDER BY COUNT(*) DESC LIMIT 20")
    exit_stats = cursor.fetchall()
    cursor.execute("SELECT account, symbol, side, entry_time, exit_time, qty, entry_price, exit_price, pnl_pct * 100, duration_s, entry_reason, exit_reason FROM completed_trades ORDER BY exit_time DESC LIMIT 200")
    latest_trades = cursor.fetchall()
    conn.close()
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("# Trading System Intelligence Report\n\n")
        f.write(f"Generated at: {datetime.now(timezone.utc).isoformat()}\n\n")
        f.write(f"### Overall System Metrics (All History)\n")
        f.write(f"- **Total Completed Trades matched**: {total_trades}\n")
        f.write(f"- **Win Rate**: {win_rate:.2f}%\n")
        f.write(f"- **Average PnL per Trade**: {avg_pnl * 100:.4f}%\n")
        f.write(f"- **Total Realized PnL (sum)**: {sum_pnl * 100:.2f}%\n\n")
        f.write("### Entry Strategy / Knob Candidates (Top 20)\n")
        f.write("| Entry Reason / Strategy | Trades | Avg PnL % | Win Rate % |\n")
        f.write("| --- | --- | --- | --- |\n")
        for row in entry_stats: f.write(f"| {row[0]} | {row[1]} | {row[2]:.4f}% | {row[3]:.1f}% |\n")
        f.write("\n### Exit Strategy / Knob Candidates (Top 20)\n")
        f.write("| Exit Reason / Strategy | Trades | Avg PnL % | Win Rate % |\n")
        f.write("| --- | --- | --- | --- |\n")
        for row in exit_stats: f.write(f"| {row[0]} | {row[1]} | {row[2]:.4f}% | {row[3]:.1f}% |\n")
        f.write("\n### Last 200 Trades Detail\n")
        f.write("| Account | Symbol | Side | Entry Time | Exit Time | Qty | Entry Px | Exit Px | PnL % | Dur (s) | Entry Reason | Exit Reason |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for t in latest_trades: f.write(f"| {t[0]} | {t[1]} | {t[2]} | {t[3][:19]} | {t[4][:19]} | {t[5]:.4f} | {t[6]:.6f} | {t[7]:.6f} | {t[8]:+.4f}% | {t[9]} | {t[10]} | {t[11]} |\n")
    print(f"Report generated: {REPORT_PATH}")
if __name__ == "__main__":
    t0 = time.time()
    raw, comp = process_trade_files()
    generate_report()
    print(f"Done in {time.time() - t0:.2f}s (processed {raw} raw fills, matched {comp} completed trades)")
