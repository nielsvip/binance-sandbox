"""run_v24_short_sweep.py — Full universe sweep of short-side v24 config.
Mirrors run_v24_full_sweep.py but for SHORT positions.
"""
from __future__ import annotations

import csv, datetime, json, sys, time
from pathlib import Path
from collections import Counter

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_struct_v4_aggressive_short import ShortCfg, simulate_aggressive_short

BASE = Path(__file__).resolve().parent
RESULTS_DIR = BASE / "data" / "sweep_results"
HISTORY_DIR = BASE / "data" / "history"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _get_crypto_symbols():
    npz_dir = BASE / "backtest_v8" / "indicators"
    syms = []
    for f in npz_dir.glob("*.npz"):
        name = f.stem
        if name.endswith("USDC") or name.endswith("USDT"):
            syms.append(name)
    return sorted(syms)


TRADIER_114 = [
    "ACN","AEM","AGCO","AGI","AG","ALB","AMD","AMZN","ARM","AR",
    "ASML","ATI","AU","AVGO","BA","BABA","BG","BHP","BK","BKR",
    "CAT","CCJ","CDE","CENX","CF","CHRD","CLF","CLX","CMC","CME",
    "COIN","COST","CRWD","CSCO","CVX","DAL","DE","DIS","DKNG","DVN",
    "EGLE","EQT","FIVN","FCX","FLR","FSLR","GD","GE","GILD","GOLD",
    "GOOG","GS","HAL","HES","IBM","INTC","JD","JPM","KGC","KMI",
    "LLY","LMT","LOW","MAR","MCD","META","MOD","MPLX","MRK","MSFT",
    "MU","NET","NLR","NOC","NOV","NUKZ","NVDA","NVO","OKE","ORCL",
    "OXY","PBR","PFE","PG","PLTR","PYPL","QCOM","RIG","RTX","SHOP",
    "SMG","SNDK","SNOW","SQ","TGT","TMUS","TSLA","TSM","TXN","UNH",
    "UNP","USAR","V","VLO","VZ","WFC","WM","WMT","XLE","XOM",
    "XOP","ZIM","ZM","ZTS",
]


def cfg_v24_short():
    return ShortCfg()


def run_universe(symbols, mode, cfg, start_date, label, write_history=False):
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    all_returns = []
    rows = []
    all_ep = Counter()
    all_xp = Counter()
    t0 = time.time()
    for sym in symbols:
        r = simulate_aggressive_short(sym, mode, cfg, start_ts=start_ts,
                                      return_events=write_history)
        if r.get("skip"):
            continue
        all_returns.extend(r["trade_returns"])
        for k, v in r.get("entry_paths", {}).items():
            all_ep[k] += v
        for k, v in r.get("exit_paths", {}).items():
            all_xp[k] += v
        rows.append({
            "sym": r["sym"], "years": r["years"], "trades": r["trades"],
            "wr_pct": r["wr_pct"], "avg_gain": r["avg_gain_pct"],
            "bh_mult": r["bh_mult"], "compound_mult": r["compound_mult"],
            "ratio_vs_bh": r["ratio_vs_bh"],
            "worst_trade": min(r["trade_returns"]) if r["trade_returns"] else 0,
        })
        if write_history and r.get("events"):
            hist_dir = HISTORY_DIR / label
            hist_dir.mkdir(parents=True, exist_ok=True)
            with open(hist_dir / f"{sym}_SHORT.jsonl", "w") as f:
                for ev in r["events"]:
                    f.write(json.dumps(ev) + "\n")
    elapsed = time.time() - t0
    n_syms = len(rows)
    total_trades = sum(r["trades"] for r in rows)
    if len(all_returns) > 1:
        arr = np.array(all_returns)
        pool_sharpe = float(np.mean(arr) / np.std(arr)) if np.std(arr) > 0 else 0.0
    else:
        pool_sharpe = 0.0
    beat_bh = sum(1 for r in rows if r["compound_mult"] > r["bh_mult"])
    cleared_4x = sum(1 for r in rows if r["ratio_vs_bh"] >= 4.0)
    losers = sum(1 for r in rows if r["compound_mult"] < 1.0)
    ratios = [r["ratio_vs_bh"] for r in rows]
    med_ratio = float(np.median(ratios)) if ratios else 0
    mean_wr = float(np.mean([r["wr_pct"] for r in rows])) if rows else 0
    worst_trade = min(all_returns) if all_returns else 0

    print(f"\n{'='*80}")
    print(f"  {label} | {mode} | SHORT | start={start_date} | {n_syms} syms | {elapsed:.0f}s")
    print(f"{'='*80}")
    print(f"  pool_sharpe  = {pool_sharpe:+.4f}")
    print(f"  trades       = {total_trades}")
    print(f"  WR%          = {mean_wr:.1f}%")
    print(f"  med xBH      = {med_ratio:.2f}")
    print(f"  beat B&H     = {beat_bh}/{n_syms} ({beat_bh/max(n_syms,1)*100:.0f}%)")
    print(f"  cleared 4x   = {cleared_4x}/{n_syms} ({cleared_4x/max(n_syms,1)*100:.0f}%)")
    print(f"  losers       = {losers}")
    print(f"  worst trade  = {worst_trade:.2f}%")
    print(f"  entries: {dict(all_ep.most_common())}")
    print(f"  exits:   {dict(all_xp.most_common())}")

    ts_label = int(time.time())
    csv_path = RESULTS_DIR / f"struct_v4_v24_SHORT_{label}_{ts_label}_per_sym.csv"
    if rows:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    summary_path = RESULTS_DIR / f"struct_v4_v24_SHORT_{label}_{ts_label}_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "label": label, "mode": mode, "side": "SHORT", "start": start_date,
            "n_syms": n_syms, "total_trades": total_trades,
            "pool_sharpe": pool_sharpe, "mean_wr": mean_wr,
            "med_ratio_vs_bh": med_ratio, "beat_bh": beat_bh,
            "cleared_4x": cleared_4x, "losers": losers,
            "worst_trade": worst_trade, "elapsed_s": elapsed,
            "entry_paths": dict(all_ep), "exit_paths": dict(all_xp),
        }, f, indent=2)

    return {
        "label": label, "n_syms": n_syms, "total_trades": total_trades,
        "pool_sharpe": pool_sharpe, "mean_wr": mean_wr,
        "med_ratio_vs_bh": med_ratio, "beat_bh": beat_bh,
        "cleared_4x": cleared_4x, "losers": losers,
        "worst_trade": worst_trade,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-history", action="store_true")
    ap.add_argument("--crypto-only", action="store_true")
    ap.add_argument("--tradier-only", action="store_true")
    args = ap.parse_args()
    cfg = cfg_v24_short()
    summaries = []

    print("\n" + "X"*80)
    print("  v24 SHORT — BEARISH SWEEP")
    print("X"*80)

    if not args.tradier_only:
        crypto_syms = _get_crypto_symbols()
        print(f"\n  Found {len(crypto_syms)} crypto NPZs")
        s = run_universe(crypto_syms, "crypto", cfg, "2022-01-01", "v24_short_crypto",
                         write_history=args.write_history)
        summaries.append(s)

    if not args.crypto_only:
        s = run_universe(TRADIER_114, "tradier", cfg, "2024-04-01", "v24_short_tradier",
                         write_history=args.write_history)
        summaries.append(s)

    print(f"\n\n{'='*100}")
    print("  v24 SHORT FINAL RESULTS")
    print(f"{'='*100}")
    hdr = f"{'Universe':<30}{'Sharpe':>8}{'Trades':>8}{'WR%':>6}{'MedxBH':>8}{'4x':>5}{'Losers':>7}{'Worst':>7}"
    print(hdr)
    print("-" * 85)
    for s in summaries:
        print(f"{s['label']:<30}{s['pool_sharpe']:>+8.4f}{s['total_trades']:>8}"
              f"{s['mean_wr']:>6.1f}{s['med_ratio_vs_bh']:>8.2f}"
              f"{s['cleared_4x']:>5}{s['losers']:>7}{s['worst_trade']:>7.1f}")


if __name__ == "__main__":
    main()
