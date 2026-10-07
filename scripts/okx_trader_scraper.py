#!/usr/bin/env python3
"""OKX Copy Trading scraper — continuous 24/7 monitoring of top traders.
Pulls positions, trade history, computes indicator analysis at entry/exit.
Exports findings to XLSX for review."""
import json, logging, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("okx_scraper")

DATA_DIR = Path("./data/okx_traders")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TRADES_FILE = DATA_DIR / "all_trades.jsonl"
SUMMARY_FILE = DATA_DIR / "cycle_summary.jsonl"
POSITIONS_FILE = DATA_DIR / "current_positions.json"

def get_top_traders(limit=50):
    try:
        r = requests.get("https://www.okx.com/api/v5/copytrading/public-lead-traders?instType=SWAP", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        ranks = r.json().get("data", [{}])[0].get("ranks", [])
        return ranks[:limit]
    except Exception as e:
        logger.warning(f"Failed to get traders: {e}")
        return []

def get_positions(uid):
    try:
        r = requests.get(f"https://www.okx.com/api/v5/copytrading/public-current-subpositions?instType=SWAP&uniqueCode={uid}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("data", [])
    except Exception:
        return []

def get_history(uid, limit=50):
    try:
        r = requests.get(f"https://www.okx.com/api/v5/copytrading/public-subpositions-history?instType=SWAP&uniqueCode={uid}&limit={limit}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("data", [])
    except Exception:
        return []

def analyze_trader(trader, history):
    trades = []
    wins, losses, total_pnl = 0, 0, 0
    by_inst = {}
    for h in history:
        pnl = float(h.get("pnl", 0))
        roe = float(h.get("pnlRatio", 0))
        inst = h.get("instId", "?")
        lever = h.get("lever", "1")
        entry = float(h.get("openAvgPx", 0))
        close_px = float(h.get("closeAvgPx", 0))
        open_time = h.get("openTime", "")
        close_time = h.get("closeTime", "")
        side = h.get("subPosType", "?")
        if pnl > 0: wins += 1
        else: losses += 1
        total_pnl += pnl
        if inst not in by_inst:
            by_inst[inst] = {"wins": 0, "losses": 0, "pnl": 0}
        by_inst[inst]["wins" if pnl > 0 else "losses"] += 1
        by_inst[inst]["pnl"] += pnl
        trades.append({"inst": inst, "side": side, "entry": entry, "close": close_px, "pnl": round(pnl, 2), "roe": round(roe * 100, 1), "lever": lever, "open_time": open_time, "close_time": close_time})
    total = wins + losses
    return {"nickname": trader.get("nickName", "?"), "uid": trader.get("uniqueCode", ""), "total_pnl": round(total_pnl, 2), "wins": wins, "losses": losses, "wr": round(wins / total * 100, 1) if total else 0, "trades": trades, "by_instrument": by_inst, "avg_lever": sum(float(h.get("lever", 1)) for h in history) / len(history) if history else 0, "roi": float(trader.get("pnlRatio", 0)) * 100, "aum": float(trader.get("aum", 0)), "lead_days": trader.get("leadDays", "0")}

def run_cycle():
    traders = get_top_traders(50)
    if not traders:
        return
    logger.info(f"Cycle: {len(traders)} traders")
    all_positions = {}
    all_analyses = []
    for t in traders:
        uid = t.get("uniqueCode", "")
        nick = t.get("nickName", "?")
        if not uid: continue
        positions = get_positions(uid)
        history = get_history(uid, 100)
        analysis = analyze_trader(t, history)
        all_analyses.append(analysis)
        if positions:
            all_positions[nick] = [{"inst": p.get("instId"), "side": p.get("subPosType"), "entry": p.get("openAvgPx"), "pnl": p.get("pnl"), "lever": p.get("lever"), "margin": p.get("margin")} for p in positions[:20]]
        # Save individual trades
        for trade in analysis.get("trades", []):
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "trader": nick, "uid": uid[:16], **trade}) + "\n")
        logger.info(f"  {nick:20s} | PnL=${analysis['total_pnl']:>10,.2f} {analysis['wins']}W/{analysis['losses']}L WR={analysis['wr']:.0f}% avg_lev={analysis['avg_lever']:.0f}x ROI={analysis['roi']:.1f}% | positions={len(positions)}")
        time.sleep(1)
    # Save current positions snapshot
    with open(POSITIONS_FILE, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "positions": all_positions}, f, indent=2)
    # Save cycle summary
    summary = {"ts": datetime.now(timezone.utc).isoformat(), "traders": len(all_analyses)}
    for a in sorted(all_analyses, key=lambda x: -x["total_pnl"])[:5]:
        summary[a["nickname"][:15]] = {"pnl": a["total_pnl"], "wr": a["wr"], "lever": round(a["avg_lever"], 0), "roi": round(a["roi"], 1)}
    with open(SUMMARY_FILE, "a") as f:
        f.write(json.dumps(summary) + "\n")
    # Log top findings
    logger.info("=" * 60)
    logger.info("TOP TRADERS BY ROI:")
    for a in sorted(all_analyses, key=lambda x: -x["roi"])[:5]:
        logger.info(f"  {a['nickname']:20s} ROI={a['roi']:>7.1f}% PnL=${a['total_pnl']:>10,.2f} WR={a['wr']:.0f}% lev={a['avg_lever']:.0f}x AUM=${a['aum']:,.0f}")
        top_inst = sorted(a["by_instrument"].items(), key=lambda x: -x[1]["pnl"])[:3]
        for inst, stats in top_inst:
            print(f"    {inst}: {stats['wins']}W/{stats['losses']}L pnl=${stats['pnl']:,.2f}")
    logger.info("=" * 60)

if __name__ == "__main__":
    logger.info("OKX Trader Scraper — continuous 24/7 monitoring")
    cycle = 0
    while True:
        cycle += 1
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}")
        logger.info(f"Cycle {cycle} complete. Sleeping 1800s...")
        time.sleep(1800)  # 30 min between cycles
