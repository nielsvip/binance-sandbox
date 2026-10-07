#!/usr/bin/env python3
"""OKX Fresh Scan v2 — try all available API params to maximize trader discovery."""
import json, time, sys, os
from datetime import datetime, timezone
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

def probe_api():
    """Probe different endpoint variants and params to find what works."""
    print("=== API PROBING ===")

    # Test 1: Default call, inspect full response structure
    print("\n[Probe 1] Default lead-traders call...")
    data = throttled_get(f"{BASE_URL}/public-lead-traders", {"instType": "SWAP"})
    print(f"  code={data.get('code')} msg={data.get('msg', '')}")
    top_keys = list(data.keys())
    print(f"  top-level keys: {top_keys}")
    d = data.get("data", [])
    if d:
        if isinstance(d, list) and len(d) > 0:
            print(f"  data is list, len={len(d)}")
            first = d[0]
            if isinstance(first, dict):
                print(f"  first item keys: {list(first.keys())[:20]}")
                if "ranks" in first:
                    ranks = first["ranks"]
                    print(f"  ranks list len={len(ranks)}")
                    if ranks:
                        print(f"  first rank keys: {list(ranks[0].keys())}")
                else:
                    print(f"  first item sample: {json.dumps(first, indent=2)[:300]}")

    # Test 2: Try with after param for pagination
    print("\n[Probe 2] Pagination with after/before...")
    for p in [{"instType": "SWAP", "after": "10"}, {"instType": "SWAP", "before": "10"},
              {"instType": "SWAP", "limit": "20"}, {"instType": "SWAP", "limit": "50"}]:
        data = throttled_get(f"{BASE_URL}/public-lead-traders", p)
        d = data.get("data", [])
        count = 0
        if isinstance(d, list) and d and isinstance(d[0], dict) and "ranks" in d[0]:
            count = len(d[0].get("ranks", []))
        elif isinstance(d, list):
            count = len(d)
        print(f"  params={p} → count={count}")

    # Test 3: Try different instTypes
    print("\n[Probe 3] Different instTypes...")
    for inst in ["SWAP", "FUTURES", "SPOT", "MARGIN"]:
        data = throttled_get(f"{BASE_URL}/public-lead-traders", {"instType": inst})
        d = data.get("data", [])
        count = 0
        if isinstance(d, list) and d and isinstance(d[0], dict) and "ranks" in d[0]:
            count = len(d[0].get("ranks", []))
        elif isinstance(d, list):
            count = len(d)
        print(f"  instType={inst} → code={data.get('code')} count={count}")

    # Test 4: Try v5 copy-trading endpoints
    print("\n[Probe 4] Alternative endpoints...")
    alt_endpoints = [
        "/public-lead-traders?instType=SWAP&sortType=pnl&limit=100",
        "/public-lead-traders?instType=SWAP&sortType=roi&limit=20",
        "/public-lead-traders?instType=SWAP&sortType=copiers&limit=20",
        "/public-lead-traders?instType=SWAP&sortType=aum&limit=20",
    ]
    for ep in alt_endpoints:
        data = throttled_get(f"{BASE_URL}{ep}")
        d = data.get("data", [])
        count = 0
        if isinstance(d, list) and d and isinstance(d[0], dict) and "ranks" in d[0]:
            count = len(d[0].get("ranks", []))
        elif isinstance(d, list):
            count = len(d)
        print(f"  {ep.split('?')[1][:50]:50s} → code={data.get('code')} count={count} msg={data.get('msg','')[:50]}")

    # Test 5: Web API (different base)
    print("\n[Probe 5] Web/priapi endpoints...")
    web_endpoints = [
        "https://www.okx.com/priapi/v5/ecotrade/public/lead-traders?instType=SWAP&limit=20",
        "https://www.okx.com/priapi/v5/ecotrade/public/lead-traders?t=swap&sortType=pnl&limit=20",
    ]
    for ep in web_endpoints:
        data = throttled_get(ep)
        d = data.get("data", [])
        count = 0
        if isinstance(d, list):
            count = len(d)
            if d and isinstance(d[0], dict):
                if "ranks" in d[0]:
                    count = len(d[0].get("ranks", []))
                else:
                    print(f"    keys: {list(d[0].keys())[:15]}")
        print(f"  {ep.split('okx.com')[1][:60]:60s} → code={data.get('code')} count={count}")

    return data

def fetch_all_traders():
    """Fetch as many unique traders as possible."""
    all_traders = {}

    def extract_ranks(data):
        d = data.get("data", [])
        if isinstance(d, list) and d and isinstance(d[0], dict) and "ranks" in d[0]:
            return d[0].get("ranks", []), int(d[0].get("totalPage", 1))
        elif isinstance(d, list):
            return [x for x in d if isinstance(x, dict) and "uniqueCode" in x], 1
        return [], 1

    # Method 1: SWAP with limit=20, paginate using totalPage
    for sort in ["pnl", "aum"]:
        print(f"\n[Fetch] SWAP sortType={sort} limit=20, paginating...")
        for page in range(1, 20):
            data = throttled_get(f"{BASE_URL}/public-lead-traders", {
                "instType": "SWAP", "sortType": sort, "limit": "20", "pageNo": str(page)
            })
            ranks, total_pages = extract_ranks(data)
            new = 0
            for t in ranks:
                uid = t.get("uniqueCode", "")
                if uid and uid not in all_traders:
                    all_traders[uid] = t
                    new += 1
            print(f"  Page {page}/{total_pages}: {len(ranks)} traders ({new} new)")
            if not ranks or page >= total_pages:
                break

    # Method 2: SPOT traders too
    print(f"\n[Fetch] SPOT traders...")
    for page in range(1, 6):
        data = throttled_get(f"{BASE_URL}/public-lead-traders", {
            "instType": "SPOT", "limit": "20", "pageNo": str(page)
        })
        ranks, total_pages = extract_ranks(data)
        new = 0
        for t in ranks:
            uid = t.get("uniqueCode", "")
            if uid and uid not in all_traders:
                all_traders[uid] = t
                new += 1
        print(f"  SPOT page {page}/{total_pages}: {len(ranks)} ({new} new)")
        if not ranks or page >= total_pages:
            break

    # Method 3: Default (no sort) — sometimes different set
    data = throttled_get(f"{BASE_URL}/public-lead-traders", {"instType": "SWAP", "limit": "20"})
    ranks, _ = extract_ranks(data)
    new = sum(1 for t in ranks if t.get("uniqueCode") and t["uniqueCode"] not in all_traders)
    for t in ranks:
        uid = t.get("uniqueCode", "")
        if uid and uid not in all_traders:
            all_traders[uid] = t
    print(f"\n[Fetch] Default no-sort: {len(ranks)} ({new} new)")

    print(f"\n  TOTAL UNIQUE TRADERS: {len(all_traders)}")
    return list(all_traders.values())

def fetch_trade_history(uid, limit=100):
    # Try both API variants
    for base in [BASE_URL, "https://www.okx.com/priapi/v5/ecotrade"]:
        data = throttled_get(f"{base}/public-subpositions-history", {
            "instType": "SWAP", "uniqueCode": uid, "limit": str(limit),
        })
        result = data.get("data", [])
        if isinstance(result, list) and result:
            if isinstance(result[0], dict) and "subPositions" in result[0]:
                return result[0]["subPositions"]
            if isinstance(result[0], dict) and ("pnl" in result[0] or "instId" in result[0]):
                return result
        if data.get("code") == "0" and result:
            return result
    return []

def fetch_current_positions(uid):
    data = throttled_get(f"{BASE_URL}/public-current-subpositions", {
        "instType": "SWAP", "uniqueCode": uid,
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
        side_lower = str(side).lower()
        if "long" in side_lower or side == "1":
            sides["long"] += 1
        elif "short" in side_lower or side == "2":
            sides["short"] += 1
        if inst not in by_inst:
            by_inst[inst] = {"wins": 0, "losses": 0, "pnl": 0.0, "count": 0}
        by_inst[inst]["wins" if pnl > 0 else "losses"] += 1
        by_inst[inst]["pnl"] += pnl
        by_inst[inst]["count"] += 1
        try:
            ot = int(open_t) if open_t else 0
            ct = int(close_t) if close_t else 0
            if ot and ct:
                hold_times.append((ct - ot) / 60000.0)
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
    avg_hold = round(sum(hold_times) / len(hold_times), 1) if hold_times else 0
    median_hold = sorted(hold_times)[len(hold_times)//2] if hold_times else 0
    unique_symbols = list(set(t["inst"] for t in trades if t["inst"] != "?"))
    btc_eth_pct = sum(1 for t in trades if "BTC" in t["inst"] or "ETH" in t["inst"]) / total * 100 if total else 0
    is_diverse = len(unique_symbols) >= 5 and btc_eth_pct < 60
    is_short_biased = sides["short"] > sides["long"] * 1.2 if sides["long"] > 0 else sides["short"] > 5
    avg_lever = 0
    try:
        levers = [float(h.get("lever", 1)) for h in history]
        avg_lever = round(sum(levers) / len(levers), 1) if levers else 0
    except:
        pass
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999
    top_inst = sorted(by_inst.items(), key=lambda x: -x[1]["pnl"])[:10]
    # Compute win streak
    max_streak = 0
    cur_streak = 0
    for t in trades:
        if t["pnl"] > 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0
    return {
        "nickname": nick, "uid": uid,
        "total_trades": total, "wins": wins, "losses": losses, "wr": wr,
        "total_pnl": round(total_pnl, 2), "avg_pnl": avg_pnl, "profit_factor": pf,
        "avg_lever": avg_lever, "avg_hold_min": avg_hold, "median_hold_min": round(median_hold, 1),
        "max_win_streak": max_streak,
        "unique_symbols": len(unique_symbols), "symbols": unique_symbols,
        "btc_eth_pct": round(btc_eth_pct, 1),
        "is_diverse": is_diverse, "is_short_biased": is_short_biased,
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

def print_report(results):
    diverse = [r for r in results if r["is_diverse"] and r["wr"] >= 55 and r["total_trades"] >= 10]
    high_wr = [r for r in results if r["wr"] >= 65 and r["total_trades"] >= 10]
    short_biased = [r for r in results if r["is_short_biased"] and r["total_trades"] >= 10]
    alt_traders = [r for r in results if r["btc_eth_pct"] < 40 and r["total_trades"] >= 10]
    has_trades = [r for r in results if r["total_trades"] >= 10]

    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}")
    print(f"Total traders scraped: {len(results)}")
    print(f"With 10+ trades: {len(has_trades)}")
    print(f"Diverse (5+ symbols, <60% BTC/ETH): {len(diverse)}")
    print(f"High WR (>=65%): {len(high_wr)}")
    print(f"Short-biased: {len(short_biased)}")
    print(f"Alt-focused (<40% BTC/ETH): {len(alt_traders)}")

    # ALL traders with trades
    print(f"\n--- ALL TRADERS WITH 10+ TRADES (sorted by PF) ---")
    for r in sorted(has_trades, key=lambda x: -x["profit_factor"]):
        tag = ""
        if r["is_diverse"]: tag += " [DIVERSE]"
        if r["is_short_biased"]: tag += " [SHORT-BIASED]"
        if r["btc_eth_pct"] < 40: tag += " [ALT-FOCUSED]"
        print(f"  {r['nickname'][:25]:25s} WR={r['wr']:>5.1f}% PF={r['profit_factor']:>6.2f} PnL=${r['total_pnl']:>12,.2f} T={r['total_trades']:>3d} syms={r['unique_symbols']:>2d} btc_eth={r['btc_eth_pct']:>4.0f}% hold={r['avg_hold_min']:>6.0f}m lev={r['avg_lever']:>4.1f}x{tag}")
        print(f"    Symbols: {', '.join(r['symbols'][:8])}")
        print(f"    L={r['sides']['long']} S={r['sides']['short']} | MaxStreak={r['max_win_streak']} | AUM=${r['aum']:,.0f} | Copiers={r['copiers']}")

    # Detailed pattern analysis
    interesting = [r for r in has_trades if r["wr"] >= 55]
    if interesting:
        print(f"\n{'='*70}")
        print(f"DETAILED PATTERN ANALYSIS")
        print(f"{'='*70}")
        for r in sorted(interesting, key=lambda x: -x["profit_factor"])[:10]:
            print(f"\n>>> {r['nickname']} (WR={r['wr']}% PF={r['profit_factor']} trades={r['total_trades']})")
            print(f"    AUM: ${r['aum']:,.0f} | Copiers: {r['copiers']} | Lead days: {r['lead_days']}")
            print(f"    Avg hold: {r['avg_hold_min']:.0f}m ({r['avg_hold_min']/60:.1f}h) | Median: {r['median_hold_min']:.0f}m | Avg lever: {r['avg_lever']}x")
            winners = [t for t in r["trades_raw"] if t["pnl"] > 0]
            losers = [t for t in r["trades_raw"] if t["pnl"] <= 0]
            if winners:
                avg_win = sum(t["pnl"] for t in winners) / len(winners)
                avg_win_roe = sum(t["roe_pct"] for t in winners) / len(winners)
                max_win = max(t["pnl"] for t in winners)
                print(f"    Avg win: ${avg_win:,.2f} ({avg_win_roe:.1f}% ROE) | Max win: ${max_win:,.2f}")
            if losers:
                avg_loss = sum(t["pnl"] for t in losers) / len(losers)
                avg_loss_roe = sum(t["roe_pct"] for t in losers) / len(losers)
                max_loss = min(t["pnl"] for t in losers)
                print(f"    Avg loss: ${avg_loss:,.2f} ({avg_loss_roe:.1f}% ROE) | Max loss: ${max_loss:,.2f}")
            # Per-instrument
            print(f"    Instruments:")
            for inst, stats in sorted(r["top_instruments"].items(), key=lambda x: -x[1]["pnl"])[:8]:
                inst_wr = stats["wins"] / stats["count"] * 100 if stats["count"] else 0
                print(f"      {inst:22s} {stats['wins']:>2d}W/{stats['losses']:>2d}L WR={inst_wr:>5.0f}% PnL=${stats['pnl']:>10,.2f}")
            # Current positions
            if r["current_pos_detail"]:
                print(f"    OPEN NOW:")
                for p in r["current_pos_detail"]:
                    pnl_val = p.get('pnl') or 0
                    print(f"      {p['inst']} {p['side']} lev={p['lever']}x pnl=${float(pnl_val):,.2f}")

    return diverse, high_wr, alt_traders, short_biased

def main():
    print("=" * 70)
    print("OKX FRESH TRADER SCAN v2")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    # Probe first
    probe_api()

    # Fetch all traders
    print(f"\n{'='*70}")
    print("FETCHING ALL TRADERS")
    print(f"{'='*70}")
    traders = fetch_all_traders()

    if not traders:
        print("NO TRADERS. Saving error state.")
        OUT.write_text(json.dumps({"error": "no_traders", "ts": datetime.now(timezone.utc).isoformat()}))
        return

    # Fetch history
    print(f"\nFetching history for {len(traders)} traders...")
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
        status = f" | {analysis['total_trades']}T WR={analysis['wr']}% PnL=${analysis['total_pnl']:>10,.2f} syms={analysis['unique_symbols']}"
        print(status)

    # Report
    diverse, high_wr, alt_traders, short_biased = print_report(results)

    # Save
    output = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_traders": len(results),
        "total_requests": req_count,
        "summary": {
            "with_trades": sum(1 for r in results if r["total_trades"] >= 10),
            "diverse_count": len(diverse),
            "high_wr_count": len(high_wr),
            "alt_focused_count": len(alt_traders),
            "short_biased_count": len(short_biased),
        },
        "all_traders": sorted(results, key=lambda x: -x["total_pnl"]),
    }
    OUT.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nTotal API requests: {req_count}")
    print(f"Saved to: {OUT}")

if __name__ == "__main__":
    main()
