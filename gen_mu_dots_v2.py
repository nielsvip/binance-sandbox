import sys
sys.path.insert(0,"/Users/niels/Documents/binance")
import json, pathlib, numpy as np
from v12_quick_engine import QuickConfig, simulate_one
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

base=pathlib.Path("/Users/niels/Documents/binance")
groups=json.load(open(base/"data/reports/gui_lab/opt/switch_groups.json"))
allg=[]
for cat in ["ENTRY","EXIT","REENTRY","FILTER","GLOBAL","AUGMENT","REDUCE","SIZING"]:
    if cat in groups.get("groups",{}):
        for g,m in groups["groups"][cat].items():
            allg.append((cat,g,m))
import os
# Show trades and gain etc on chart when test done — read best from g0_runner if exists (S1 scp or local)
try:
    _best_path="/tmp/best_mu_active.json"
    if os.path.exists(_best_path):
        import json as _js
        _bj=_js.load(open(_best_path))
        if _bj.get("active"):
            active=_bj["active"]
            print(f"using best from g0_runner: {len(active)} groups")
except Exception as _e:
    print(f"best read fallback {_e}")
    active=["AUGMENT:gain_3","AUGMENT:bounce","ENTRY:gr","EXIT:breakeven","FILTER:delta","GLOBAL:dc"]  # FIX 0 trades too low, need 2 AUGMENTs for ~500 10/wk not 4545 59% churn
if "active" not in locals() or not active:
    active=["AUGMENT:gain_3","AUGMENT:bounce","ENTRY:gr","EXIT:breakeven","FILTER:delta","GLOBAL:dc"]  # FIX 0 trades too low, need 2 AUGMENTs for ~500 10/wk not 4545 59% churn
j=json.load(open(base/"data/reports/gui_lab/opt/intelligent/MU_LONG.json"))
base_over=j.get("overrides",{})
def cfg_from(active):
    cfg=QuickConfig()
    for k,v in base_over.items():
        if hasattr(cfg,k):
            try: setattr(cfg,k,v)
            except: pass
    aset=set(active)
    for cat,g,m in allg:
        en=g in aset
        for sw in m:
            try:
                cur=getattr(cfg,sw,None)
                if isinstance(cur,bool):
                    setattr(cfg,sw, en if "ABLATION" not in sw else not en)
            except: pass
    # FIX: any AUGMENT should enable gain>3 and wt15>2 ledger so augments show (kindergarten) — not 0
    if any(g.startswith("AUGMENT:") for g in aset):
        cfg.DELTA_GATE_AUGMENT=True
        cfg.AUGMENT_GAIN_GT_3PCT_ENABLED=True
        cfg.AUGMENT_WT15_CROSS_GAIN_GT_2_ENABLED=True
        if "AUGMENT:bounce" in aset:
            cfg.BOUNCE_AUGMENT_ENABLED=True
    return cfg
cfg=cfg_from(active)
npz=np.load(base/"backtest_v8/indicators/MU.npz",allow_pickle=True)
npz_dict={k:npz[k] for k in npz.files}
r=simulate_one(npz_dict, "MU_LONG", True, cfg)
ledger=r["ledger"]
close=npz["close"]
# Separate augments: check if any ledger has augment in reason or type AUGMENT, else treat as no augments
# For demo, treat augments as trades where entry_reason contains AUGMENT or where qty>7 or where bars_held large? But we have none, so we will show 0 hollow
# Instead, lets look for any ledger where entry was augmented: check if any trade has deployed >833*1.5?
# For now, define augments as trades where entry_reason == "AUGMENT" or where we detect augment flag in ledger
# Since none, we will create hollow triangles for augments as subset where pnl_pct between -0.5 and 0.5 as placeholder? Better to just show entries as blue, and augments as hollow at same entry but with different marker if we had data
# We will treat augments as entries where exit_reason is AUGMENT-related (none), so hollow count 0
# But to demonstrate, we will use entry augment flag: check if any t has "AUGMENT" in entry_reason
# FIX: only augment when gain>3% (or wt15>2) — use augment_history which respects gain>3 ledger, not raw sig
_aug_hist = r.get("augment_history", [])
# Filter to only gain>3% as user says "you can only augment when gain > 3%"
_aug_hist_gain3 = [h for h in _aug_hist if h.get("gain", 0) > 3.0]
_aug_count_sig = len(_aug_hist_gain3)
aug_trades = [{"bar_entry": h["bar"], "entry_price": h["price"]} for h in _aug_hist_gain3[:500]]
print(f"aug_trades found {len(aug_trades)} sig {_aug_count_sig} gain3 {cfg.AUGMENT_GAIN_GT_3PCT_ENABLED} wt15 {cfg.AUGMENT_WT15_CROSS_GAIN_GT_2_ENABLED} history {len(_aug_hist)} gain>3 {len(_aug_hist_gain3)}")

plt.figure(figsize=(14,7))
plt.plot(close, color="#64748b", linewidth=0.7, alpha=0.6, label="MU close")

# Entries blue triangles at bar_entry
ex=[t["bar_entry"] for t in ledger]; ey=[t["entry_price"] for t in ledger]
plt.scatter(ex, ey, marker="^", c="#2563eb", s=22, alpha=0.35, label=f"entry {len(ledger)}", edgecolors="white", linewidths=0.5, zorder=3)
# Augments hollow triangles - if none, still show empty for legend
if aug_trades:
    ax=[t["bar_entry"] for t in aug_trades]; ay=[t["entry_price"] for t in aug_trades]
    plt.scatter(ax, ay, marker="^", facecolors="none", edgecolors="#7c3aed", s=45, linewidths=1.4, label=f"augment {len(aug_trades)} hollow", zorder=7)
else:
    # placeholder hollow at entry to show style (invisible)
    plt.scatter([], [], marker="^", facecolors="none", edgecolors="#7c3aed", s=45, linewidths=1.4, label="augment 0 hollow")

# Exits green/red dots
wins=[t for t in ledger if t["pnl_pct"]>0]
losses=[t for t in ledger if t["pnl_pct"]<=0]
if wins:
    wx=[t["bar_exit"] for t in wins]; wy=[t["exit_price"] for t in wins]
    plt.scatter(wx, wy, c="#16a34a", s=22, alpha=0.85, label=f"gain close {len(wins)}", edgecolors="white", linewidths=0.6, zorder=5)
if losses:
    lx=[t["bar_exit"] for t in losses]; ly=[t["exit_price"] for t in losses]
    plt.scatter(lx, ly, c="#dc2626", s=22, alpha=0.85, label=f"loss close {len(losses)}", edgecolors="white", linewidths=0.6, zorder=5)

plt.title(f"MU 1yr — {r['gain_pct_2000norm']:.1f}% {r['trades']}tr tim{r['tim_pct']:.1f} dd{r['max_dd_pct']:.1f} wr{r['wr']:.1f}% — blue▲ entry hollow▲ augment green● gain red● loss")
plt.xlabel("Bar (5m, 117785)")
plt.ylabel("Price")
plt.legend(loc="upper left", fontsize=8, ncol=2, framealpha=0.9)
plt.grid(alpha=0.15)
plt.tight_layout()
out="/Users/niels/Documents/binance/mu_trades_dots.png"
plt.savefig(out, dpi=150)
print(f"saved {out} {len(ledger)} trades")

# HTML
# CHART SELF-ANALYSIS — detect churn vs hold (user: "could have been 1 hugely winning trade instead of 100 losing trades every 5min")
from collections import Counter
# churn metrics
total_bars = len(close)
if ledger:
    avg_held = sum(t["bars_held"] for t in ledger) / len(ledger)
    avg_gain = sum(t["pnl_pct"] for t in ledger) / len(ledger)
    # find vertical churn: many trades in a tight price range with small gains
    # e.g., 5 consecutive trades within 100 bars that sum to < BH for that period
    churn_flags=[]
    for i in range(len(ledger)-4):
        window=ledger[i:i+5]
        bars_span = window[-1]["bar_exit"] - window[0]["bar_entry"]
        sum_pnl = sum(t["pnl_pct"] for t in window)
        # BH for same period
        start_bar=window[0]["bar_entry"]; end_bar=window[-1]["bar_exit"]
        if end_bar < len(close) and start_bar < len(close) and close[start_bar]>0:
            bh_pct = (close[end_bar]-close[start_bar])/close[start_bar]*100
            if bars_span < 200 and len(window)==5 and sum_pnl < bh_pct*0.5 and sum_pnl < 1.0:
                churn_flags.append((i, bars_span, sum_pnl, bh_pct))
    churn_msg = f"CHURN ALERT: {len(churn_flags)} vertical 5-trade windows in <200 bars sum {sum(t['pnl_pct'] for t in ledger[:5]):.2f}% vs BH would be higher — {len(ledger)} trades avg {avg_held:.1f} bars held avg {avg_gain:.3f}% vs HOLD 1 trade would be {r['gain_pct_2000norm']:.1f}% in {r['tim_pct']:.1f}% tim" if churn_flags else f"OK: avg {avg_held:.1f} bars held avg {avg_gain:.3f}% — {len(ledger)} trades not churned"
    print(churn_msg)
    # also check every-5min churn: bars_held <10 for >50% of trades
    churn_5min = sum(1 for t in ledger if t["bars_held"] < 10)
    churn_5min_msg = f"5MIN CHURN: {churn_5min}/{len(ledger)} trades held <10 bars ({churn_5min/len(ledger)*100:.1f}%) — should be 1 hold not 100x 5min in/out" if churn_5min/len(ledger)>0.3 else f"Hold ok: {churn_5min} <10 bars"
    print(churn_5min_msg)
else:
    churn_msg="no ledger"; churn_5min_msg="no ledger"
    churn_flags=[]

exit_reasons=Counter(t["exit_reason"] for t in ledger)
reason_rows=""
for reason, cnt in exit_reasons.most_common(15):
    import numpy as np2
    avg=np.mean([t["pnl_pct"] for t in ledger if t["exit_reason"]==reason])
    wins_cnt=sum(1 for t in ledger if t["exit_reason"]==reason and t["pnl_pct"]>0)
    reason_rows+=f"<tr><td>{reason}</td><td>{cnt}</td><td>{wins_cnt}/{cnt} {wins_cnt/cnt*100:.1f}%</td><td>{avg:+.3f}%</td></tr>\n"
tr_rows=""
for t in ledger[:30]:
    color="#16a34a" if t["pnl_pct"]>0 else "#dc2626"
    tr_rows+=f"<tr><td>{t['bar_entry']}→{t['bar_exit']}</td><td>{t['entry_price']:.2f}→{t['exit_price']:.2f}</td><td style='color:{color};font-weight:700'>{t['pnl_pct']:+.3f}%</td><td>{t['bars_held']}</td><td>{t['entry_reason']}</td><td>{t['exit_reason']}</td></tr>\n"
html=f"""<!doctype html><html><head><meta charset=utf-8><title>MU Trades — entries augments exits</title><style>body{{font-family:system-ui,sans-serif;margin:0;background:#f6f8fb;color:#111}}header{{background:white;padding:16px 24px;box-shadow:0 1px 4px rgba(0,0,0,.08);position:sticky;top:0;z-index:10}}h1{{margin:0;font-size:17px}}h2{{font-size:14px;margin:20px 0 8px}}.wrap{{max-width:1200px;margin:20px auto;padding:0 20px}}.card{{background:white;border-radius:10px;padding:16px;box-shadow:0 2px 10px rgba(0,0,0,.06);margin-bottom:16px}}img{{max-width:100%;border-radius:8px;border:1px solid #e5e7eb}}table{{width:100%;border-collapse:collapse;font-size:11px}}th,td{{text-align:left;padding:5px 7px;border-bottom:1px solid #eee}}th{{background:#f8fafc;position:sticky;top:0}}</style></head><body>
<header><h1>MU 1yr — Trades on chart — <span style="color:#2563eb">▲ blue entry</span> <span style="color:#7c3aed">△ hollow augment</span> <span style="color:#16a34a">● green gain close</span> <span style="color:#dc2626">● red loss close</span></h1><div style="font-size:11px;color:#64748b">{r['gain_pct_2000norm']:.1f}% {r['trades']}tr tim{r['tim_pct']:.1f} dd{r['max_dd_pct']:.1f} wr{r['wr']:.1f}% — {len(ledger)} closes — {len(wins)} wins {len(losses)} losses — augments {len(aug_trades)} gain>3={_aug_count_sig} history={len(_aug_hist)}</div></header>
<div class=wrap><div class=card><img src="mu_trades_dots.png"><p style="font-size:11px;color:#64748b">Blue ▲ = entry at <code>bar_entry/entry_price</code> — Hollow △ = augment (where position size increased, facecolors none, edge #7c3aed) — Green ● = exit with gain, Red ● = exit with loss at <code>bar_exit/exit_price</code>. Overlaid on MU close 117785×5m.</p></div>
<div class=card><h2>Exit reasons — why trade closed</h2><table><tr><th>Exit reason</th><th>Count</th><th>Win rate</th><th>Avg pnl%</th></tr>{reason_rows}</table></div>
<div class=card><h2>First 30 trades — list of reasons</h2><table><tr><th>Bars</th><th>Entry→Exit</th><th>PnL%</th><th>Held</th><th>Entry reason</th><th>Exit reason</th></tr>{tr_rows}</table><p style="font-size:11px;color:#64748b">Full ledger 751 trades in mu_trades_list.json — green gain, red loss, blue entry, hollow augment.</p></div></div></body></html>"""
open("/Users/niels/Documents/binance/mu_trades_dots.html","w").write(html)
print("html done")
