#!/usr/bin/env python3
"""Strategy Lab — test every possible combination overnight.

Tests:
1. EV thresholds: 3%, 5%, 8%, 10%, 15%
2. Time horizons: 3min, 5min, 7min, 10min
3. Ticker filtering: all, top4 only (BNB/XRP/ETH/SOL), top2 (BNB/XRP)
4. Strike distance: narrow (0.1-0.2%), medium (0.2-0.5%), wide (0.3-1.0%)
5. Stoch-only model (no LR) vs LR-only vs combined
6. DC position filter: only trade when NOT near DC high/low
7. HA alignment filter: only trade when 2+ TFs agree
8. Market-making sim: place both YES and NO, capture spread

Outputs results to data/poly/limitless_trader/strategy_lab_results.json
"""
import json, math, redis, time, sys
from collections import defaultdict
from pathlib import Path

OUTPUT = Path("./data/poly/limitless_trader/strategy_lab_results.json")

def load_klines(symbol):
    r = redis.Redis(decode_responses=True)
    raw = r.get(f"klines:{symbol}:3m")
    if not raw: return []
    return json.loads(raw).get("klines", [])

def compute_ind(klines, idx):
    if idx < 80: return None
    closes = [k["close"] for k in klines[max(0,idx-300):idx+1]]
    highs = [k["high"] for k in klines[max(0,idx-300):idx+1]]
    lows = [k["low"] for k in klines[max(0,idx-300):idx+1]]
    price = closes[-1]
    if not price: return None
    r = {"current_price": price}
    for label, period in [("1m",5),("3m",14),("15m",70),("1h",280)]:
        n = min(period, len(closes))
        if n < 5: continue
        wh,wl,wc = highs[-n:],lows[-n:],closes[-n:]
        hh,ll = max(wh),min(wl)
        rng = hh-ll if hh!=ll else 1
        k = (wc[-1]-ll)/rng*100
        kp = (wc[-2]-ll)/rng*100 if len(wc)>1 else k
        r[f"stoch_k_{label}"] = k
        r[f"k_{label}_prev"] = kp
        r[f"stoch_d_{label}"] = (k+kp+((wc[-3]-ll)/rng*100 if len(wc)>2 else k))/3
    for label, period in [("3m",14),("15m",70),("1h",280)]:
        n = min(period, len(closes))
        if n < 5: continue
        y = closes[-n:]
        xm = (n-1)/2; ym = sum(y)/n
        num = sum((i-xm)*(y[i]-ym) for i in range(n))
        den = sum((i-xm)**2 for i in range(n))
        r[f"lr_trend_{label}"] = num/den if den else 0
    for label, period in [("3m",14),("15m",70)]:
        n = min(period, len(closes)-1)
        if n < 2: continue
        trs = [max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(-n,0)]
        r[f"atr_{label}"] = sum(trs)/len(trs)
    for label, period in [("3m",14),("15m",70)]:
        n = min(period, len(highs))
        r[f"dc_high_{label}"] = max(highs[-n:])
        r[f"dc_low_{label}"] = min(lows[-n:])
    if len(closes)>=4:
        o,h,l,c = klines[idx]["open"],klines[idx]["high"],klines[idx]["low"],klines[idx]["close"]
        po,pc2 = klines[idx-1]["open"],klines[idx-1]["close"]
        r["ha_3m"] = "green" if (o+h+l+c)/4 > (po+pc2)/2 else "red"
        ppo = klines[idx-2]["open"] if idx>1 else po
        ppc = klines[idx-2]["close"] if idx>1 else pc2
        r["ha_15m"] = "green" if (po+klines[idx-1]["high"]+klines[idx-1]["low"]+pc2)/4 > (ppo+ppc)/2 else "red"
        r["ha_1h"] = r["ha_3m"]
    for label, period in [("3m",14),("15m",70)]:
        n = min(period, len(closes))
        if n<5: continue
        ema = sum(closes[-n:])/n
        r[f"wt_score_{label}"] = (price-ema)/(price*0.001) if price else 0
    if len(closes)>=3: r["velocity"] = (closes[-1]-closes[-2])-(closes[-2]-closes[-3])
    return r

def project_combined(ind, mins):
    """Full combined model."""
    price = ind.get("current_price",0)
    if not price: return 0,0
    spm,wt = 0,0
    for tf,cm,w in [("3m",3,0.40),("15m",15,0.30),("1h",60,0.20)]:
        s = ind.get(f"lr_trend_{tf}",0)
        if s: spm += (s/cm)*w; wt += w
    if wt>0: spm /= wt
    lr = price + spm*mins
    sa = 0
    for tf,w in [("1m",0.35),("3m",0.30),("15m",0.20),("1h",0.15)]:
        k = ind.get(f"stoch_k_{tf}",50); kp = ind.get(f"k_{tf}_prev",k); dk = k-kp
        if k>85 and dk<0: sa -= w*0.3
        elif k>85: sa += w*0.1
        elif k<15 and dk>0: sa += w*0.3
        elif k<15: sa -= w*0.1
        else: sa += w*(dk/30)
    atr = ind.get("atr_3m",price*0.003) if mins<=10 else ind.get("atr_15m",price*0.003)
    sa *= atr
    tf_dc = "3m" if mins<=10 else "15m"
    dh = ind.get(f"dc_high_{tf_dc}",price*1.1); dl = ind.get(f"dc_low_{tf_dc}",price*0.9)
    dr = dh-dl if dh>dl else 1; dp = (price-dl)/dr
    dc = -0.15*atr if dp>0.9 else (0.15*atr if dp<0.1 else 0)
    hs = sum(1 if ind.get(f"ha_{tf}")=="green" else (-1 if ind.get(f"ha_{tf}")=="red" else 0) for tf in ["3m","15m","1h"])
    ha = hs*0.05*atr
    wta = sum(ind.get(f"wt_score_{tf}",0)*0.005*atr for tf in ["3m","15m"])
    va = ind.get("velocity",0)*0.02*atr
    proj = lr+sa+dc+ha+wta+va
    comps = [spm*mins,sa,dc,ha]
    bu = sum(1 for c in comps if c>0); be = sum(1 for c in comps if c<0)
    return proj, abs(bu-be)/max(len(comps),1)

def project_lr_only(ind, mins):
    """LR slope only — no stoch/DC/HA."""
    price = ind.get("current_price",0)
    if not price: return 0,0.5
    spm,wt = 0,0
    for tf,cm,w in [("3m",3,0.50),("15m",15,0.30),("1h",60,0.20)]:
        s = ind.get(f"lr_trend_{tf}",0)
        if s: spm += (s/cm)*w; wt += w
    if wt>0: spm /= wt
    return price + spm*mins, 0.5

def project_stoch_only(ind, mins):
    """Stoch momentum only — no LR."""
    price = ind.get("current_price",0)
    if not price: return 0,0.5
    sa = 0
    for tf,w in [("1m",0.40),("3m",0.35),("15m",0.25)]:
        k = ind.get(f"stoch_k_{tf}",50); kp = ind.get(f"k_{tf}_prev",k); dk = k-kp
        if k>80 and dk<0: sa -= w*0.5
        elif k<20 and dk>0: sa += w*0.5
        else: sa += w*(dk/20)
    atr = ind.get("atr_3m",price*0.003)
    return price + sa*atr, 0.5

def get_prob(proj, price, strike, ind, mins):
    if proj<=0: return 0.5
    dist = proj-strike
    atr = ind.get("atr_3m",price*0.003) if mins<=10 else ind.get("atr_15m",price*0.003)
    if not atr: atr = price*0.003
    unc = atr*math.sqrt(max(1,mins/3))*0.5
    if unc<0.001: return 1.0 if dist>0 else 0.0
    z = dist/unc
    return max(0.01, min(0.99, 1.0/(1.0+math.exp(-2.0*z))))

def run_strategy(klines, horizon_min, offsets, ev_threshold, project_fn, filters=None):
    ca = horizon_min // 3
    if ca < 1: ca = 1
    wins, losses, total_pnl, skipped = 0, 0, 0.0, 0
    for i in range(80, len(klines)-ca):
        ind = compute_ind(klines, i)
        if not ind: continue
        price = ind["current_price"]
        future = klines[i+ca]["close"]
        # Apply filters
        if filters:
            if "ha_align" in filters:
                ha_count = sum(1 if ind.get(f"ha_{tf}")=="green" else (-1 if ind.get(f"ha_{tf}")=="red" else 0) for tf in ["3m","15m","1h"])
                if abs(ha_count) < 2:
                    skipped += 1
                    continue
            if "no_dc_extreme" in filters:
                dh = ind.get("dc_high_3m", price*1.1)
                dl = ind.get("dc_low_3m", price*0.9)
                dr = dh-dl if dh>dl else 1
                dp = (price-dl)/dr
                if dp > 0.85 or dp < 0.15:
                    skipped += 1
                    continue
        for off in offsets:
            strike = price * (1 + off/100)
            proj, conf = project_fn(ind, horizon_min)
            prob = get_prob(proj, price, strike, ind, horizon_min)
            # Simulate market price
            if off < 0:
                mp = max(0.55, min(0.92, 0.5 + abs(off)/100*200))
            else:
                mp = max(0.08, min(0.45, 0.5 - abs(off)/100*200))
            # EV check
            if prob > mp + ev_threshold/100:
                entry = mp
                if future > strike:
                    total_pnl += (1/entry-1)*5; wins += 1
                else:
                    total_pnl -= 5; losses += 1
            elif prob < mp - ev_threshold/100:
                entry = 1-mp
                if future <= strike:
                    total_pnl += (1/entry-1)*5; wins += 1
                else:
                    total_pnl -= 5; losses += 1
    total = wins+losses
    wr = wins/total*100 if total else 0
    roi = total_pnl/(total*5)*100 if total else 0
    return {"wins": wins, "losses": losses, "total": total, "skipped": skipped, "wr": round(wr,1), "pnl": round(total_pnl,1), "roi": round(roi,1)}

def main():
    symbols = {"BNBUSDC": "BNB", "XRPUSDC": "XRP", "ETHUSDC": "ETH", "SOLUSDC": "SOL", "BTCUSDC": "BTC", "DOGEUSDC": "DOGE"}
    all_klines = {}
    for sym in symbols:
        kl = load_klines(sym)
        if kl:
            all_klines[sym] = kl
            print(f"Loaded {sym}: {len(kl)} candles")
    results = {}
    test_count = 0
    # === TEST MATRIX ===
    configs = [
        # (name, horizon, offsets, ev_thresh, model, filters, tickers)
        ("combined_5m_ev5_narrow", 5, [-0.2,-0.1,0.1,0.2], 5, "combined", None, None),
        ("combined_5m_ev5_medium", 5, [-0.3,-0.2,0.2,0.3], 5, "combined", None, None),
        ("combined_5m_ev8_medium", 5, [-0.3,-0.2,0.2,0.3], 8, "combined", None, None),
        ("combined_5m_ev10_medium", 5, [-0.3,-0.2,0.2,0.3], 10, "combined", None, None),
        ("combined_3m_ev5_narrow", 3, [-0.2,-0.1,0.1,0.2], 5, "combined", None, None),
        ("combined_7m_ev5_medium", 7, [-0.3,-0.2,0.2,0.3], 5, "combined", None, None),
        ("combined_10m_ev5_medium", 10, [-0.3,-0.2,0.2,0.3], 5, "combined", None, None),
        ("lr_only_5m_ev5", 5, [-0.3,-0.2,0.2,0.3], 5, "lr_only", None, None),
        ("stoch_only_5m_ev5", 5, [-0.3,-0.2,0.2,0.3], 5, "stoch_only", None, None),
        ("combined_5m_ev5_ha_filter", 5, [-0.3,-0.2,0.2,0.3], 5, "combined", ["ha_align"], None),
        ("combined_5m_ev5_dc_filter", 5, [-0.3,-0.2,0.2,0.3], 5, "combined", ["no_dc_extreme"], None),
        ("combined_5m_ev5_both_filters", 5, [-0.3,-0.2,0.2,0.3], 5, "combined", ["ha_align","no_dc_extreme"], None),
        ("combined_5m_ev3_top4", 5, [-0.3,-0.2,0.2,0.3], 3, "combined", None, ["BNBUSDC","XRPUSDC","ETHUSDC","SOLUSDC"]),
        ("combined_5m_ev5_top2", 5, [-0.3,-0.2,0.2,0.3], 5, "combined", None, ["BNBUSDC","XRPUSDC"]),
        ("combined_5m_ev5_wide", 5, [-0.5,-0.3,0.3,0.5], 5, "combined", None, None),
        ("combined_5m_ev15_medium", 5, [-0.3,-0.2,0.2,0.3], 15, "combined", None, None),
    ]
    models = {"combined": project_combined, "lr_only": project_lr_only, "stoch_only": project_stoch_only}
    print(f"\nRunning {len(configs)} strategy configs × {len(all_klines)} symbols...")
    for cfg_name, horizon, offsets, ev_thresh, model_name, filters, ticker_filter in configs:
        cfg_results = {}
        agg = {"wins":0,"losses":0,"pnl":0,"total":0}
        for sym, kl in all_klines.items():
            if ticker_filter and sym not in ticker_filter:
                continue
            r = run_strategy(kl, horizon, offsets, ev_thresh, models[model_name], filters)
            cfg_results[symbols[sym]] = r
            agg["wins"] += r["wins"]; agg["losses"] += r["losses"]
            agg["pnl"] += r["pnl"]; agg["total"] += r["total"]
        agg["wr"] = round(agg["wins"]/agg["total"]*100,1) if agg["total"] else 0
        agg["roi"] = round(agg["pnl"]/(agg["total"]*5)*100,1) if agg["total"] else 0
        results[cfg_name] = {"config": {"horizon": horizon, "offsets": offsets, "ev_threshold": ev_thresh, "model": model_name, "filters": filters, "tickers": ticker_filter}, "by_ticker": cfg_results, "aggregate": agg}
        test_count += 1
        print(f"  [{test_count}/{len(configs)}] {cfg_name:40s} | {agg['wr']:5.1f}% WR | ROI={agg['roi']:+6.1f}% | pnl=${agg['pnl']:+8.0f} | {agg['total']} bets")
    # Sort by ROI
    print("\n" + "="*80)
    print("RANKED BY ROI (best → worst)")
    print("="*80)
    ranked = sorted(results.items(), key=lambda x: -x[1]["aggregate"]["roi"])
    for i, (name, data) in enumerate(ranked):
        a = data["aggregate"]
        cfg = data["config"]
        print(f"#{i+1:2d} {name:42s} ROI={a['roi']:+6.1f}% WR={a['wr']:5.1f}% pnl=${a['pnl']:+8.0f} bets={a['total']:>6d} | {cfg['horizon']}m ev{cfg['ev_threshold']}% {cfg['model']}")
    with open(OUTPUT, "w") as f:
        json.dump({"run_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "ranked": [(name, data) for name, data in ranked], "all_results": results}, f, indent=2)
    print(f"\nSaved to {OUTPUT}")

if __name__ == "__main__":
    main()
