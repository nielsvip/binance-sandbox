#!/usr/bin/env python3
import sys, os, json
from datetime import datetime
from pathlib import Path
def compile_trades(input_path: Path, output_dir: Path):
    rounds = {}
    closed = {}
    with open(input_path, "r") as f:
        for line in f:
            if not line.strip(): continue
            try: ev = json.loads(line)
            except: continue
            side, sym, kind = ev.get("side"), ev.get("symbol") or "", (ev.get("type") or "").upper()
            qty, price, ts_raw = float(ev.get("qty") or 0), float(ev.get("price") or 0), ev.get("ts") or ev.get("unix_ts", 0)
            if not side or not sym or qty <= 0 or price <= 0: continue
            ts = int(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()) if isinstance(ts_raw, str) else int(ts_raw)
            key = (sym, side)
            rd = rounds.get(key)
            if kind in ("OPEN", "AUGMENT", "HEDGE_OPEN"):
                if rd is None or rd.get("qty", 0) <= 0:
                    rd = {"side": side, "account": ev.get("account", "-"), "symbol": sym, "entry_ts": ts, "entry_price": price, "qty": qty, "peak_qty": qty, "entry_reason": ev.get("reason") or ev.get("entry_reason") or "", "events": [ev]}
                    rounds[key] = rd
                else:
                    new_qty = rd["qty"] + qty
                    rd["entry_price"] = (rd["entry_price"] * rd["qty"] + price * qty) / new_qty
                    rd["qty"] = new_qty
                    rd["peak_qty"] = max(rd.get("peak_qty", 0), new_qty)
                    rd["events"].append(ev)
            elif kind in ("REDUCE", "CLOSE", "HEDGE_CLOSE"):
                if rd is None or rd.get("qty", 0) <= 0: continue
                close_qty = min(qty, rd["qty"])
                pnl_pct = (price - rd["entry_price"]) / rd["entry_price"] * 100.0 if side == "LONG" else (rd["entry_price"] - price) / rd["entry_price"] * 100.0
                rd["events"].append(ev)
                rd["qty"] -= close_qty
                if rd["qty"] <= max(1e-9, rd.get("peak_qty", 0) * 0.005) or kind in ("CLOSE", "HEDGE_CLOSE"):
                    trade = {"symbol": sym, "side": side, "entry_ts": rd["entry_ts"], "entry_price": rd["entry_price"], "exit_ts": ts, "exit_price": price, "pnl_pct": pnl_pct, "pnl_usd": (pnl_pct / 100.0) * (rd["entry_price"] * close_qty), "entry_reason": rd["entry_reason"], "exit_reason": ev.get("reason") or ev.get("exit_reason") or "", "duration_sec": ts - rd["entry_ts"], "stream": "primary"}
                    closed.setdefault(sym, []).append(trade)
                    rounds[key] = None
    output_dir.mkdir(parents=True, exist_ok=True)
    for sym, trades in closed.items():
        out_file = output_dir / f"{output_dir.name}__{sym}.jsonl"
        with open(out_file, "w") as out:
            for t in trades: out.write(json.dumps(t) + "\n")
        print(f"Written {len(trades)} trades for {sym} to {out_file}", flush=True)
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 import_sweep_trades.py <input_trades.jsonl> [run_id]", flush=True)
        sys.exit(1)
    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Error: {input_path} not found", flush=True)
        sys.exit(1)
    run_id = sys.argv[2] if len(sys.argv) > 2 else input_path.stem.replace("_trades", "")
    output_dir = Path("/Users/niels/Documents/binance/data/canonical_trades") / run_id
    print(f"Compiling trades from {input_path} into {output_dir}...", flush=True)
    compile_trades(input_path, output_dir)
    print("Compilation complete!", flush=True)
if __name__ == "__main__":
    main()
