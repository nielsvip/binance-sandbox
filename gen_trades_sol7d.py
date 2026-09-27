import sys, pathlib, json
sys.path.insert(0, "/Users/niels/Documents/binance")
from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
import dataclasses, v12_quick_engine as V, config, config_tradier

def get_defaults(symside):
    is_crypto=symside.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
    defaults={}
    for f in dataclasses.fields(V.QuickConfig):
        defaults[f.name]=f.default if f.default is not dataclasses.MISSING else None
        if defaults[f.name] is None and f.default_factory is not dataclasses.MISSING:
            try: defaults[f.name]=f.default_factory()
            except: defaults[f.name]=None
    live_cls=config.Config if is_crypto else config_tradier.TradierConfig
    for k in dir(live_cls):
        if k.startswith("_"): continue
        if k not in defaults:
            try: defaults[k]=getattr(live_cls,k)
            except: pass
    return defaults

SYM="SOLUSDC_LONG"
WINDOW=7
defaults=get_defaults(SYM)
prep=prepare_batch(SYM, WINDOW)
import pathlib as pl, json
prog_path=pl.Path(f"/Users/niels/Documents/binance/data/reports/lifecycle_pilot/{SYM}_7d_progress.json")
if prog_path.exists():
    prog=json.loads(prog_path.read_text())
    cum_overrides=prog.get("cumulative_overrides", {})
    overrides=dict(defaults)
    overrides.update(cum_overrides)
    print(f"using progress cum_overrides {len(cum_overrides)} cum {prog.get('cumulative_gain'):.4f}")
else:
    overrides=dict(defaults)
    print("no progress, using defaults")
    prog={"done":{}, "cumulative_gain": 0}

res=evaluate_prepared_sanitized(prep, overrides, WINDOW, include_ledger=True)
print(f"gain {res.get('gain_pct'):.4f} bh {res.get('bh_pct'):.4f} trades {res.get('trades')} valid {res.get('valid')}")
ledger=res.get("ledger") or []
print(f"ledger {len(ledger)}")
close=prep["npz_prepared"]["close"]
timestamps=prep["npz_prepared"]["timestamps"]
candles=[]
for i in range(len(close)):
    candles.append({"time": int(timestamps[i]), "open": float(close[i]), "high": float(close[i]*1.01), "low": float(close[i]*0.99), "close": float(close[i])})
if len(candles)>500:
    step=len(candles)//500
    candles=candles[::step]
trades=[]
for t in ledger[:30]:
    try:
        trades.append({
            "entry_time": int(t.get("entry_time") or timestamps[t.get("entry_idx",0)]),
            "exit_time": int(t.get("exit_time") or timestamps[t.get("exit_idx",0)]),
            "entry_price": float(t.get("entry_price") or 0),
            "exit_price": float(t.get("exit_price") or 0),
            "pnl": float(t.get("pnl") or t.get("pnl_pct") or 0),
            "side": t.get("side") or "LONG"
        })
    except: pass

deltas=[]
prog_data=json.loads(prog_path.read_text()) if prog_path.exists() else {"done":{}}
for k,v in list(prog_data.get("done",{}).items())[:30]:
    deltas.append({"switch": k, "delta": float(v.get("delta") or 0), "cum_before": float(v.get("cumulative_before") or 0), "cum_after": float(v.get("cumulative_after") or 0)})

out={
    "symbol": SYM,
    "window": WINDOW,
    "baseline": float(res.get("gain_pct") or 0),
    "bh": float(res.get("bh_pct") or 0),
    "candles": candles[:200],
    "trades": trades[:20],
    "deltas": deltas[:20],
    "progress": {"done": len(prog_data.get("done",{})) if prog_path.exists() else 0, "cum": float(prog_data.get("cumulative_gain") or 0) if prog_path.exists() else 0}
}
pl.Path("/Users/niels/Documents/binance/data/sol7d_trades.json").write_text(json.dumps(out))
print(f"written data/sol7d_trades.json with {len(candles)} candles {len(trades)} trades")
