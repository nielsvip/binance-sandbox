import sys
sys.path.insert(0, "/Users/niels/Documents/binance")
import json, pathlib, time
from v8_vec_sweep import sweep_config_for_mode, load_npz, simulate_one_symbol, _apply_per_task_overrides
import metrics_guard

ledger_path = pathlib.Path("/Users/niels/Documents/binance/data/reports/gui_lab/next_gen_beam_per_sym.json")
j = json.loads(ledger_path.read_text())
ledger = j.get("ledger", [])
print(f"ledger {len(ledger)} stock")

# Filter stocks only
stocks = [r for r in ledger if not any(r["symside"].startswith(x) for x in ["BTC","ETH","SOL","BNB","XRP","DOGE","HYPE","ZEC","WLD"]) and not r["symside"].split("_")[0].endswith("USDT") and not r["symside"].split("_")[0].endswith("USDC")]
print(f"stocks filtered {len(stocks)} example {[r['symside'] for r in stocks[:5]]}")

results=[]
for rec in stocks[:5]:  # test first 5 as sample, then expand
    symside = rec["symside"]
    sym, side = symside.rsplit("_",1)
    mode="tradier"
    try:
        npz, ts = load_npz(sym, mode)
        start_ts = int(ts[-1] - 365*86400)
        base_overrides = rec["best"].get("overrides", {})
        bh = rec["best"].get("bh_gain_pct", 0)
        cfg_base = sweep_config_for_mode(mode)
        _apply_per_task_overrides(cfg_base, base_overrides)
        cfg_base.KINDERGARTEN_EMA_GATE_ENABLED=False
        e0,r0,n0 = simulate_one_symbol(sym, side, mode, cfg_base, start_ts=start_ts, _npz_cache=(npz,ts))
        gain0 = sum(r0) if r0 else 0
        cfg_kg = sweep_config_for_mode(mode)
        _apply_per_task_overrides(cfg_kg, base_overrides)
        cfg_kg.KINDERGARTEN_EMA_GATE_ENABLED=True
        cfg_kg.EMA_9_21_FILTER_ENABLED=True
        e1,r1,n1 = simulate_one_symbol(sym, side, mode, cfg_kg, start_ts=start_ts, _npz_cache=(npz,ts))
        gain1 = sum(r1) if r1 else 0
        delta = gain1 - gain0
        apply = delta > 0 and (gain1 - bh) > 0
        print(f"{symside:15} base {gain0:.1f} ({len(r0)} tr) -> KG {gain1:.1f} ({len(r1)} tr) delta {delta:+.1f} BH {bh:.1f} {'APPLY' if apply else 'skip'}")
        results.append((symside, gain0, gain1, delta, apply))
    except Exception as e:
        print(f"{symside} err {e}")
        import traceback
        traceback.print_exc()

print(f"results {len(results)}")
