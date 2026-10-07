#!/usr/bin/env python3
"""OKX Fresh Scan — pull top copy traders + their trade history, analyze for edge."""
import json, time, sys, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

BASE = Path("/Users/niels/Documents/binance")
OUT = BASE / "data" / "reverse_engineered" / "okx_fresh_scan.json"

HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
BASE_URL = "https://www.okx.com/api/v5/copytrading"

req_count = 0
last_req_time = 0.0

def throttled_get(url, params=None, timeout=15):
    global req_count, last_req_time
    # 3 req/sec max
    elapsed = time.time() - last_req_time
    if elapsed < 0.35:
        time.sleep(0.35 - elapsed)
    last_req_time = time.time()
    req_count += 1
    try:
        r = requests.get(url, params=params, headers=HDR, timeout=timeout)
        return r.json()
    except Exception as e:
        print(f"  [ERR] {url}: {e}")
        return {}

def fetch_top_traders():
    """Fetch top traders across multiple sort types and pages."""
    all_traders = {}
    # Try multiple sort types to get diverse set
    for sort_type in ["pnl", "winRatio", "currentCopyTraderNum"]:
        for page in range(1, 6):  # 5 pages
            print(f"  Fetching traders: sort={sort_type} page={page}")
            data = throttled_get(f"{BASE_URL}/public-lead-traders", params={
                "instType": "SWAP",
                "sortType": sort_type,
                "limit": "10",
                "pageNo": str(page),
            })
            if data.get("code") != "0":
                print(f"  API error: {data.get('code')} {data.get('msg', '')}")
                # Try alternative response structure
                ranks = data.get("data", [])
                if isinstance(ranks, list) and ranks and isinstance(ranks[0], dict) and "ranks" in ranks[0]:
                    ranks = ranks[0]["ranks"]
                elif isinstance(ranks, list):
                    pass
                else:
                    ranks = []
            else:
                ranks = data.get("data", [])
                if isinstance(ranks, list) and ranks and isinstance(ranks[0], dict) and "ranks" in ranks[0]:
                    ranks = ranks[0]["ranks"]
            for t in ranks:
                uid = t.get("uniqueCode", "")
                if uid and uid not in all_traders:
                    all_traders[uid] = t
            if not ranks:
                break
    print(f"  Total unique traders found: {len(all_traders)}")
    return list(all_traders.values())

def fetch_trade_history(uid, limit=100):
    """Fetch closed trade history for a trader."""
    data = throttled_get(f"{BASE_URL}/public-subpositions-history", params={
        "instType": "SWAP",
        "uniqueCode": uid,
        "limit": str(limit),
    })
    if data.get("code") != "0":
        # Try nested structure
        result = data.get("data", [])
        if isinstance(result, list) and result and isinstance(result[0], dict) and "subPositions" in result[0]:
            return result[0]["subPositions"]
        return result if isinstance(result, list) else []
    result = data.get("data", [])
    if isinstance(result, list) and result and isinstance(result[0], dict) and "subPositions" in result[0]:
        return result[0]["subPositions"]
    return result if isinstance(result, list) else []

def fetch_current_positions(uid):
    """Fetch current open positions."""
    data = throttled_get(f"{BASE_URL}/public-current-subpositions", params={
        "instType": "SWAP",
        "uniqueCode": uid,
    })
    result = data.get("data", [])
    if isinstance(result, list) and result and isinstance(result[0], dict) and "subPositions" in result[0]:
        return result[0]["subPositions"]
    return result if isinstance(result, list) else []

def analyze_trader(trader_info, history, positions):
    uid = trader_info.get("uniqueCode", "")
    nick = trader_info.get("nickName", "?")
    wins, losses, total_pnl = 0, 0, 0.0
    by_inst = {}
    trades = []
    hold_times = []
    sides = {"long": 0, "short": 0}
    for h in history:
        pnl = float(h.get("pnl", 0))
        roe = float(h.get("pnlRatio", 0))
        inst = h.get("instId", "?")
        lever = h.get("lever", "1")
        side = h.get("subPosType", h.get("posSide", "?"))
        open_t = h.get("openTime", h.get("openTimeStr", ""))
        close_t = h.get("closeTime", h.get("closeTimeStr", ""))
        entry_px = h.get("openAvgPx", "0")
        close_px = h.get("closeAvgPx", "0")
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        total_pnl += pnl
        # Track side
        side_lower = str(side).lower()
        if "long" in side_lower or side == "1":
            sides["long"] += 1
        elif "short" in side_lower or side == "2":
            sides["short"] += 1
        # By instrument
        if inst not in by_inst:
            by_inst[inst] = {"wins": 0, "losses": 0, "pnl": 0.0, "count": 0}
        by_inst[inst]["wins" if pnl > 0 else "losses"] += 1
        by_inst[inst]["pnl"] += pnl
        by_inst[inst]["count"] += 1
        # Hold time
        try:
            ot = int(open_t) if open_t else 0
            ct = int(close_t) if close_t else 0
            if ot and ct:
                hold_min = (ct - ot) / 60000.0
                hold_times.append(hold_min)
        except (ValueError, TypeError):
            pass
        trades.append({
            "inst": inst, "side": side, "pnl": round(pnl, 2),
            "roe_pct": round(roe * 100, 2), "lever": lever,
            "entry_px": entry_px, "close_px": close_px,
            "open_time": open_t, "close_time": close_t,
        })
    total = wins + losses
    wr = round(wins / total * 100, 1) if total else 0
    avg_pnl = round(total_pnl / total, 2) if total else 0
    avg_hold_min = round(sum(hold_times) / len(hold_times), 1) if hold_times else 0
    median_hold = sorted(hold_times)[len(hold_times)//2] if hold_times else 0
    unique_symbols = list(set(t["inst"] for t in trades if t["inst"] != "?"))
    btc_eth_pct = sum(1 for t in trades if "BTC" in t["inst"] or "ETH" in t["inst"]) / total * 100 if total else 0
    # Classify
    is_diverse = len(unique_symbols) >= 5 and btc_eth_pct < 60
    is_short_biased = sides["short"] > sides["long"] * 1.2
    avg_lever = 0
    try:
        levers = [float(h.get("lever", 1)) for h in history]
        avg_lever = round(sum(levers) / len(levers), 1) if levers else 0
    except:
        pass
    # Profit factor
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999
    # Top instruments by PnL
    top_inst = sorted(by_inst.items(), key=lambda x: -x[1]["pnl"])[:10]
    return {
        "nickname": nick,
        "uid": uid,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": avg_pnl,
        "profit_factor": pf,
        "avg_lever": avg_lever,
        "avg_hold_min": avg_hold_min,
        "median_hold_min": round(median_hold, 1),
        "unique_symbols": len(unique_symbols),
        "symbols": unique_symbols,
        "btc_eth_pct": round(btc_eth_pct, 1),
        "is_diverse": is_diverse,
        "is_short_biased": is_short_biased,
        "sides": sides,
        "top_instruments": {k: v for k, v in top_inst},
        "current_positions": len(positions),
        "current_pos_detail": [{"inst": p.get("instId"), "side": p.get("subPosType", p.get("posSide")), "pnl": p.get("pnl"), "lever": p.get("lever")} for p in positions[:10]],
        "roi_pct": round(float(trader_info.get("pnlRatio", 0)) * 100, 1),
        "aum": round(float(trader_info.get("aum", 0)), 0),
        "copiers": trader_info.get("currentCopyTraderNum", "0"),
        "lead_days": trader_info.get("leadDays", "0"),
        "trades_raw": trades,
    }

def main():
    print("=" * 70)
    print("OKX FRESH TRADER SCAN")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    # Step 1: Fetch traders
    print("\n[1/3] Fetching top traders...")
    traders = fetch_top_traders()
    if not traders:
        print("NO TRADERS RETURNED. API may be blocked or changed.")
        # Try raw request to diagnose
        print("\nDiagnostic: raw API call...")
        try:
            r = requests.get(f"{BASE_URL}/public-lead-traders", params={"instType": "SWAP"}, headers=HDR, timeout=15)
            print(f"  Status: {r.status_code}")
            print(f"  Body[:500]: {r.text[:500]}")
        except Exception as e:
            print(f"  Failed: {e}")
        OUT.write_text(json.dumps({"error": "no_traders_returned", "ts": datetime.now(timezone.utc).isoformat()}, indent=2))
        return
    # Step 2: Fetch history for each
    print(f"\n[2/3] Fetching trade history for {len(traders)} traders...")
    results = []
    for i, t in enumerate(traders):
        uid = t.get("uniqueCode", "")
        nick = t.get("nickName", "?")
        if not uid:
            continue
        print(f"  [{i+1}/{len(traders)}] {nick[:25]:25s}", end="", flush=True)
        history = fetch_trade_history(uid)
        positions = fetch_current_positions(uid)
        analysis = analyze_trader(t, history, positions)
        results.append(analysis)
        print(f" | {analysis['total_trades']}T WR={analysis['wr']}% PnL=${analysis['total_pnl']:>10,.2f} syms={analysis['unique_symbols']} btc_eth={analysis['btc_eth_pct']:.0f}% PF={analysis['profit_factor']}")
    # Step 3: Analyze and output
    print(f"\n[3/3] Analysis...")
    print(f"Total requests made: {req_count}")
    # Sort by interestingness
    diverse = [r for r in results if r["is_diverse"] and r["wr"] >= 55 and r["total_trades"] >= 10]
    high_wr = [r for r in results if r["wr"] >= 65 and r["total_trades"] >= 10]
    short_biased = [r for r in results if r["is_short_biased"] and r["total_trades"] >= 10]
    alt_traders = [r for r in results if r["btc_eth_pct"] < 40 and r["total_trades"] >= 10]
    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"Total traders scraped: {len(results)}")
    print(f"With 10+ trades: {sum(1 for r in results if r['total_trades'] >= 10)}")
    print(f"Diverse (5+ symbols, <60% BTC/ETH): {len(diverse)}")
    print(f"High WR (>=65%): {len(high_wr)}")
    print(f"Short-biased: {len(short_biased)}")
    print(f"Alt-focused (<40% BTC/ETH): {len(alt_traders)}")
    print(f"\n--- DIVERSE TRADERS (interesting for reverse-engineering) ---")
    for r in sorted(diverse, key=lambda x: -x["profit_factor"])[:15]:
        print(f"  {r['nickname'][:25]:25s} WR={r['wr']}% PF={r['profit_factor']} PnL=${r['total_pnl']:>10,.2f} syms={r['unique_symbols']} btc_eth={r['btc_eth_pct']:.0f}% hold={r['avg_hold_min']:.0f}m lev={r['avg_lever']}x")
        print(f"    Top: {', '.join(list(r['top_instruments'].keys())[:5])}")
        print(f"    Sides: L={r['sides']['long']} S={r['sides']['short']}")
    print(f"\n--- HIGH WR TRADERS ---")
    for r in sorted(high_wr, key=lambda x: -x["wr"])[:10]:
        print(f"  {r['nickname'][:25]:25s} WR={r['wr']}% PF={r['profit_factor']} trades={r['total_trades']} PnL=${r['total_pnl']:>10,.2f} btc_eth={r['btc_eth_pct']:.0f}%")
    print(f"\n--- ALT-FOCUSED TRADERS ---")
    for r in sorted(alt_traders, key=lambda x: -x["total_pnl"])[:10]:
        print(f"  {r['nickname'][:25]:25s} WR={r['wr']}% PnL=${r['total_pnl']:>10,.2f} syms={r['unique_symbols']} btc_eth={r['btc_eth_pct']:.0f}%")
        print(f"    Symbols: {', '.join(r['symbols'][:8])}")
    print(f"\n--- SHORT-BIASED TRADERS ---")
    for r in sorted(short_biased, key=lambda x: -x["total_pnl"])[:10]:
        print(f"  {r['nickname'][:25]:25s} L={r['sides']['long']} S={r['sides']['short']} WR={r['wr']}% PnL=${r['total_pnl']:>10,.2f}")
    # Detailed pattern analysis for top diverse traders
    print(f"\n{'='*70}")
    print(f"DETAILED PATTERN ANALYSIS — Top Diverse Traders")
    print(f"{'='*70}")
    for r in sorted(diverse, key=lambda x: -x["profit_factor"])[:5]:
        print(f"\n>>> {r['nickname']} (WR={r['wr']}% PF={r['profit_factor']} trades={r['total_trades']})")
        print(f"    AUM: ${r['aum']:,.0f} | Copiers: {r['copiers']} | Lead days: {r['lead_days']}")
        print(f"    Avg hold: {r['avg_hold_min']:.0f}m | Median hold: {r['median_hold_min']:.0f}m | Avg lever: {r['avg_lever']}x")
        # Winning vs losing patterns
        winners = [t for t in r["trades_raw"] if t["pnl"] > 0]
        losers = [t for t in r["trades_raw"] if t["pnl"] <= 0]
        if winners:
            avg_win = sum(t["pnl"] for t in winners) / len(winners)
            avg_win_roe = sum(t["roe_pct"] for t in winners) / len(winners)
            print(f"    Avg win: ${avg_win:.2f} ({avg_win_roe:.1f}% ROE)")
        if losers:
            avg_loss = sum(t["pnl"] for t in losers) / len(losers)
            avg_loss_roe = sum(t["roe_pct"] for t in losers) / len(losers)
            print(f"    Avg loss: ${avg_loss:.2f} ({avg_loss_roe:.1f}% ROE)")
        # Side breakdown
        long_wins = sum(1 for t in winners if "long" in str(t["side"]).lower() or t["side"] == "1")
        short_wins = sum(1 for t in winners if "short" in str(t["side"]).lower() or t["side"] == "2")
        long_losses = sum(1 for t in losers if "long" in str(t["side"]).lower() or t["side"] == "1")
        short_losses = sum(1 for t in losers if "short" in str(t["side"]).lower() or t["side"] == "2")
        if long_wins + long_losses > 0:
            print(f"    Long WR: {long_wins}/{long_wins+long_losses} = {long_wins/(long_wins+long_losses)*100:.0f}%")
        if short_wins + short_losses > 0:
            print(f"    Short WR: {short_wins}/{short_wins+short_losses} = {short_wins/(short_wins+short_losses)*100:.0f}%")
        # Instrument breakdown
        print(f"    Instruments:")
        for inst, stats in sorted(r["top_instruments"].items(), key=lambda x: -x[1]["pnl"])[:8]:
            inst_wr = stats["wins"] / stats["count"] * 100 if stats["count"] else 0
            print(f"      {inst:20s} {stats['wins']}W/{stats['losses']}L WR={inst_wr:.0f}% PnL=${stats['pnl']:,.2f}")
    # Save
    output = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_traders": len(results),
        "total_requests": req_count,
        "summary": {
            "diverse_count": len(diverse),
            "high_wr_count": len(high_wr),
            "short_biased_count": len(short_biased),
            "alt_focused_count": len(alt_traders),
        },
        "diverse_traders": sorted(diverse, key=lambda x: -x["profit_factor"]),
        "high_wr_traders": sorted(high_wr, key=lambda x: -x["wr"]),
        "alt_traders": sorted(alt_traders, key=lambda x: -x["total_pnl"]),
        "short_biased_traders": sorted(short_biased, key=lambda x: -x["total_pnl"]),
        "all_traders": sorted(results, key=lambda x: -x["total_pnl"]),
    }
    # Remove raw trades from saved file to keep size reasonable (keep top 10)
    for category in ["diverse_traders", "high_wr_traders", "alt_traders", "short_biased_traders"]:
        for t in output[category]:
            pass  # keep raw trades for these interesting ones
    for t in output["all_traders"]:
        if t not in output["diverse_traders"] and t not in output["high_wr_traders"]:
            t.pop("trades_raw", None)
    OUT.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved to: {OUT}")

if __name__ == "__main__":
    main()
