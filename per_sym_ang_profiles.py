#!/usr/bin/env python3
"""per_sym_ang_profiles — standard-path per-sym sweep for ang account symbols.

Sweeps WA_MIN_GAIN_PCT + PYRAMID_MIN_GAIN_PCT (1–4%) plus core v8_quick timing
knobs for all ang symbols (union of symbols_ang_long + symbols_ang_short) that
have NPZ files. Writes winner JSONs to data/hourly_reconfig/ang/_candidates/.

Identical sweep grid to per_sym_fin_profiles — only account name, symbol source,
and output dir differ.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
SWEEP_DIR = ROOT / "data" / "sweep_results"
CAND_OUT_DIR = ROOT / "data" / "hourly_reconfig" / "ang" / "_candidates"
ACTIVE_CONFIG_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
CHARTS_OUT_DIR = ROOT / "plots" / "ang"
SYMBOLS_LONG_FILE = ROOT / "symbols_ang_long.json"
SYMBOLS_SHORT_FILE = ROOT / "symbols_ang_short.json"

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 5000
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_PER_YR_MIN = 100.0
PROMOTE_WR_MIN = 60.0


def load_ang_symbols() -> List[str]:
    longs = json.loads(SYMBOLS_LONG_FILE.read_text())
    shorts = json.loads(SYMBOLS_SHORT_FILE.read_text())
    syms = sorted(set(longs) | set(shorts))
    return [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]


def load_full_trades(run_dir: Path, run_id: str, sym: str) -> List[Dict]:
    """Read full trade records (with timestamps) from the JSONL written by v8_quick_engine."""
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    records: List[Dict] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def generate_sym_chart(sym: str, npz: dict, trades: List[Dict], m: Dict,
                       out_dir: Path, days: int = 60) -> Optional[Path]:
    """Generate OHLC price chart with entry/exit trade overlays + PnL panels."""
    if not HAS_MPL or not trades:
        return None
    ts_arr = npz.get("timestamps_15m", npz.get("timestamps", None))
    close_arr = npz.get("close_15m", None)
    if ts_arr is None or close_arr is None or len(ts_arr) == 0:
        return None
    cutoff_ts = int(time.time()) - max(1, days) * 86400
    mask = ts_arr >= cutoff_ts
    plot_ts = ts_arr[mask]
    plot_close = close_arr[mask]
    if len(plot_ts) == 0:
        return None
    trades_win = [t for t in trades if t.get("exit_ts", 0) >= cutoff_ts]
    if not trades_win:
        trades_win = trades
    pnl = [float(t.get("pnl_pct", 0)) for t in trades_win]
    cum_pnl = []
    running = 0.0
    for p in pnl:
        running += p
        cum_pnl.append(running)
    dt_price = [datetime.fromtimestamp(int(ts), tz=timezone.utc) for ts in plot_ts if ts > 0]
    dt_entry = [datetime.fromtimestamp(int(t["entry_ts"]), tz=timezone.utc)
                for t in trades_win if t.get("entry_ts", 0) > 0]
    ep_list = [float(t.get("entry_price", 0)) for t in trades_win if t.get("entry_ts", 0) > 0]
    dt_exit = [datetime.fromtimestamp(int(t["exit_ts"]), tz=timezone.utc)
               for t in trades_win if t.get("exit_ts", 0) > 0]
    xp_list = [float(t.get("exit_price", 0)) for t in trades_win if t.get("exit_ts", 0) > 0]
    sides = [t.get("side", "LONG") for t in trades_win if t.get("entry_ts", 0) > 0]
    dt_cum = [datetime.fromtimestamp(int(t.get("exit_ts", 0)), tz=timezone.utc)
              for t in trades_win if t.get("exit_ts", 0) > 0]
    n_win = sum(1 for p in pnl if p > 0)
    fig = plt.figure(figsize=(18, 11), facecolor="#0d1117")
    fig.patch.set_facecolor("#0d1117")
    gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1.2, 1], hspace=0.06, figure=fig)
    ax_p = fig.add_subplot(gs[0])
    ax_p.set_facecolor("#0d1117")
    if len(dt_price) == len(plot_close):
        ax_p.plot(dt_price, plot_close, color="#c9d1d9", linewidth=0.8, zorder=2)
    if dt_entry and len(dt_entry) == len(ep_list):
        longs = [(dt_entry[i], ep_list[i]) for i in range(len(sides)) if sides[i] == "LONG"]
        shorts = [(dt_entry[i], ep_list[i]) for i in range(len(sides)) if sides[i] == "SHORT"]
        if longs:
            ax_p.scatter([x[0] for x in longs], [x[1] for x in longs],
                         marker="^", s=55, color="#3fb950", zorder=5, label=f"LONG entry ({len(longs)})")
        if shorts:
            ax_p.scatter([x[0] for x in shorts], [x[1] for x in shorts],
                         marker="v", s=55, color="#f0883e", zorder=5, label=f"SHORT entry ({len(shorts)})")
    if dt_exit and len(dt_exit) == len(xp_list):
        ax_p.scatter(dt_exit, xp_list, marker="x", s=35, color="#f85149",
                     zorder=5, linewidths=1.3, label=f"Exit ({len(dt_exit)})")
    ax_p.set_title(
        f"ang | {sym}  pool={m['pool_sharpe']:+.4f}  wr={m['wr_pct']:.1f}%  "
        f"dd={m['max_dd_pct']:.2f}%  trades={m['trades']}  wins={n_win}/{len(pnl)}  "
        f"({days}d window)",
        color="#c9d1d9", fontsize=10, pad=6,
    )
    ax_p.set_ylabel("Price", color="#c9d1d9", fontsize=9)
    ax_p.tick_params(colors="#6e7681", labelsize=7)
    ax_p.legend(loc="upper left", fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
    for spine in ax_p.spines.values(): spine.set_color("#30363d")
    ax_p.grid(color="#21262d", linewidth=0.4, zorder=0)
    ax_c = fig.add_subplot(gs[1], sharex=ax_p)
    ax_c.set_facecolor("#0d1117")
    if dt_cum and len(dt_cum) == len(cum_pnl):
        ax_c.plot(dt_cum, cum_pnl, color="#58a6ff", linewidth=1.3, zorder=3)
        ax_c.fill_between(dt_cum, cum_pnl, alpha=0.12, color="#58a6ff")
    ax_c.axhline(0, color="#6e7681", linewidth=0.5, linestyle="--")
    ax_c.set_ylabel("Cum PnL %", color="#c9d1d9", fontsize=9)
    ax_c.tick_params(colors="#6e7681", labelsize=7)
    for spine in ax_c.spines.values(): spine.set_color("#30363d")
    ax_c.grid(color="#21262d", linewidth=0.4, zorder=0)
    ax_b = fig.add_subplot(gs[2], sharex=ax_p)
    ax_b.set_facecolor("#0d1117")
    if dt_cum and len(dt_cum) == len(pnl):
        bar_colors = ["#3fb950" if p > 0 else "#f85149" for p in pnl]
        ax_b.bar(dt_cum, pnl, color=bar_colors, width=0.4, zorder=3)
    ax_b.axhline(0, color="#6e7681", linewidth=0.5, linestyle="--")
    ax_b.set_ylabel("Trade %", color="#c9d1d9", fontsize=9)
    ax_b.tick_params(colors="#6e7681", labelsize=7)
    for spine in ax_b.spines.values(): spine.set_color("#30363d")
    ax_b.grid(color="#21262d", linewidth=0.4, zorder=0)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ang_{sym}_{days}d.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  [chart] → {out_path.name}")
    return out_path


def promote_winners_to_active_config(winners: Dict[str, Tuple[Dict, Dict]]) -> None:
    """Promote per-sym sweep winners to active_config.json for live ang trading."""
    existing: Dict = {}
    if ACTIVE_CONFIG_PATH.exists():
        try:
            existing = json.loads(ACTIVE_CONFIG_PATH.read_text())
        except Exception:
            existing = {}
    now_tag = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    for sym, (ovr, m) in winners.items():
        clean_ovr = {k: v for k, v in ovr.items() if not k.startswith("_")}
        entry = {
            "winning_tag": f"per_sym_{sym}_{now_tag}",
            "wsharpe": float(m.get("pool_sharpe", 0)),
            "trades": int(m.get("trades", 0)),
            "sample_tag": "PER_SYM",
            "overrides": clean_ovr,
        }
        for side in ("LONG", "SHORT"):
            existing[f"{sym}_{side}"] = entry
    ACTIVE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ACTIVE_CONFIG_PATH.open("w") as f:
        json.dump(existing, f, indent=2, default=str)
    print(f"  [active_config] Updated {ACTIVE_CONFIG_PATH} ({len(existing)} entries)")


def per_sym_metrics(rets: List[float], years: float, sym: str) -> Dict:
    n = len(rets)
    n_wins = sum(1 for r in rets if r > 0)
    wr = (100.0 * n_wins / n) if n else 0.0
    total_gain = float(sum(rets))
    avg_gain = (total_gain / n) if n else 0.0
    yrs = max(0.01, years)
    pool = mg.pool_sharpe(rets)
    sym_pool_capped = max(-mg.PER_SYM_SHARPE_CAP, min(mg.PER_SYM_SHARPE_CAP, pool))
    eq = 0.0; peak = 0.0; worst = 0.0
    for r in rets:
        eq += r
        if eq > peak:
            peak = eq
        elif peak - eq > worst:
            worst = peak - eq
    return {
        "pool_sharpe": pool, "sym_sharpe": sym_pool_capped,
        "avg_gain_trade": avg_gain, "gain_per_yr": total_gain / yrs,
        "gain_sym_yr": total_gain / yrs, "trades": n, "n_syms": 1,
        "years": yrs, "total_gain_pct": total_gain, "wr_pct": wr,
        "max_dd_pct": worst, "n_wins": n_wins, "tag": f"ang_per_sym_{sym}",
    }


def verdict(m: Dict) -> str:
    if m["max_dd_pct"] > PROMOTE_DD_MAX:
        return "REJECT_DD"
    if m["wr_pct"] < PROMOTE_WR_MIN:
        return "REJECT_WR"
    if m["trades"] < PROMOTE_TRADES_MIN:
        return "REJECT_TRADES"
    if m["gain_per_yr"] < PROMOTE_GAIN_PER_YR_MIN:
        return "REJECT_PNL"
    if m["pool_sharpe"] < PROMOTE_POOL_MIN:
        return "DIAGNOSTIC"
    return "PROMOTE"


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


def fmt_canonical(sym: str, m: Dict, v: str) -> str:
    return (
        f"{sym:14s} | pool={m['pool_sharpe']:+.4f} | wr={m['wr_pct']:.1f}% | "
        f"avg_gain={m['avg_gain_trade']:+.4f}% | gpy={m['gain_per_yr']:+.1f}% | "
        f"trades={m['trades']:,} | dd={m['max_dd_pct']:.2f}% | {v}"
    )


def run_engine_for_sym(sym: str, overrides: Dict, run_dir: Path, run_id: str,
                       npz: dict) -> List[float]:
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if not k.startswith("_"):
            try:
                setattr(cfg, k, v)
            except Exception:
                pass
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit:
        pass
    except Exception as e:
        print(f"    [engine] EXC {sym} {e}")
        return []
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    rets: List[float] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rets.append(float(json.loads(line).get("pnl_pct", 0.0)))
                except Exception:
                    pass
    return rets


def mutation_grid() -> List[Tuple[str, Dict]]:
    grid: List[Tuple[str, Dict]] = []
    grid.append(("baseline", {}))
    for mg_pct in (1.0, 2.0, 3.0, 4.0):
        grid.append((f"mingain_{mg_pct:.0f}pct",
                     {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct}))
    for es in (12.0, 15.0, 18.0, 24.0):
        grid.append((f"es_{es:.0f}", {"ENTRY_SCORE_THRESHOLD": es}))
    for mh in (3, 5, 10, 20):
        grid.append((f"mh_{mh}", {"MIN_HOLD_BARS": mh}))
    for wt in (1, 2, 3, 4):
        grid.append((f"wt_exit_{wt}", {"WT_EXIT_MIN_TFS": wt}))
    for cd in (1, 3, 6, 12):
        grid.append((f"cd_{cd}", {"COOLDOWN_BARS": cd}))
    grid.append(("dtrend_on", {"D_TREND_REQUIRED": True}))
    grid.append(("dtrend_off", {"D_TREND_REQUIRED": False}))
    for htf in (1, 2, 3):
        grid.append((f"htf_{htf}", {"HTF_MIN_ALIGNED": htf}))
    for mg_pct, es in ((1.0, 15.0), (2.0, 18.0), (3.0, 18.0), (4.0, 24.0),
                       (1.0, 24.0), (2.0, 15.0)):
        grid.append((f"mg{mg_pct:.0f}_es{es:.0f}",
                     {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
                      "ENTRY_SCORE_THRESHOLD": es}))
    for mg_pct, mh in ((1.0, 5), (2.0, 10), (3.0, 10), (4.0, 20)):
        grid.append((f"mg{mg_pct:.0f}_mh{mh}",
                     {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
                      "MIN_HOLD_BARS": mh}))
    return grid


def sweep_sym(sym: str, run_root: Path, sweep_csv: Path,
              full_years: float) -> Tuple[Dict, Dict]:
    print(f"\n{'─'*60}")
    print(f"  {sym}")
    print(f"{'─'*60}")
    npz_path = NPZ_DIR / f"{sym}.npz"
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()
    run_dir = run_root / sym
    run_dir.mkdir(parents=True, exist_ok=True)
    grid = mutation_grid()
    print(f"  grid size: {len(grid)}")
    results: List[Tuple[str, Dict, Dict]] = []
    t0 = time.time()
    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}__{tag}__{i:03d}"
        rets = run_engine_for_sym(sym, ovr, run_dir, run_id, npz)
        m = per_sym_metrics(rets, full_years, sym)
        m["tag"] = f"ang_mut_{sym}_{tag}"
        v = verdict(m)
        m["verdict"] = v
        m["effective_score"] = effective_score(m)
        results.append((tag, ovr, m))
        try:
            mg.write_sharpe_row(sweep_csv, m, mode="crypto", append=True)
        except Exception as e:
            print(f"    [csv] REFUSED {tag}: {e}")
        if i <= 4 or i % 10 == 0:
            print(f"    [{i:3d}/{len(grid)}] {tag:25s} pool={m['pool_sharpe']:+.4f} "
                  f"tr={m['trades']:>5d} dd={m['max_dd_pct']:.2f}% eff={m['effective_score']:+.3f} {v}")
    print(f"  done in {time.time()-t0:.1f}s")
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["max_dd_pct"] <= PROMOTE_DD_MAX and m["wr_pct"] >= PROMOTE_WR_MIN
                 and m["gain_per_yr"] >= PROMOTE_GAIN_PER_YR_MIN and m["trades"] >= 30]
    if qualified:
        best = max(qualified, key=lambda t: t[2]["effective_score"])
        bucket = "QUALIFIED"
    else:
        viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= 30] or results
        best = max(viable, key=lambda t: t[2]["effective_score"])
        bucket = "FALLBACK"
    tag, ovr, m = best
    print(f"  WINNER ({bucket}): {tag} pool={m['pool_sharpe']:+.4f} "
          f"tr={m['trades']:,} dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
          f"gpy={m['gain_per_yr']:+.1f}% eff={m['effective_score']:+.3f}")
    winner_idx = next((i+1 for i, (t, _, _) in enumerate(results) if t == tag), 1)
    winner_run_id = f"{sym}__{tag}__{winner_idx:03d}"
    winner_trades = load_full_trades(run_dir, winner_run_id, sym)
    return ovr, m, winner_trades, run_dir / sym, winner_run_id


def write_winner_json(sym: str, overrides: Dict, m: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CAND_OUT_DIR / f"ang_sym_{sym}_winner.json"
    payload = dict(overrides)
    payload["_meta"] = (
        f"ang per_sym winner for {sym} | pool={m['pool_sharpe']:+.4f} "
        f"trades={m['trades']} dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
        f"verdict={m.get('verdict')} effective_score={m.get('effective_score', 0):+.3f}"
    )
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="", help="comma-separated subset (default all with NPZ)")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--charts", action="store_true", help="generate per-symbol price+overlay charts")
    ap.add_argument("--chart-days", type=int, default=60, help="days window for charts (default 60)")
    args = ap.parse_args()
    all_syms = load_ang_symbols()
    syms = [s for s in args.syms.split(",") if s] if args.syms else all_syms
    syms = [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]
    print("=" * 72)
    print(f"per_sym_ang_profiles — {len(syms)} symbols")
    print(f"NPZ dir: {NPZ_DIR}")
    print(f"Out: {CAND_OUT_DIR}")
    print("=" * 72)
    print(f"Symbols: {syms}")
    ts = int(time.time())
    sweep_csv = SWEEP_DIR / f"canonical_per_sym_ang_{ts}.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / f"per_sym_ang_{ts}" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)
    winners: Dict[str, Tuple[Dict, Dict]] = {}
    for sym in syms:
        try:
            ovr, m, winner_trades, _run_dir, _run_id = sweep_sym(sym, run_root, sweep_csv, args.years)
            winners[sym] = (ovr, m)
            p = write_winner_json(sym, ovr, m)
            print(f"  [winner] {p.name}")
            if args.charts and HAS_MPL and winner_trades:
                try:
                    z = np.load(str(NPZ_DIR / f"{sym}.npz"))
                    npz_c = {k: z[k] for k in z.files}
                    z.close()
                    generate_sym_chart(sym, npz_c, winner_trades, m, CHARTS_OUT_DIR, days=args.chart_days)
                except Exception as ce:
                    print(f"  [chart] ERR {sym}: {ce}")
        except Exception as e:
            print(f"  EXC {sym}: {e}")
        gc.collect()
    print()
    print("=" * 72)
    print("Promoting winners to active_config.json")
    print("=" * 72)
    try:
        promote_winners_to_active_config(winners)
    except Exception as e:
        print(f"  [promote] ERR: {e}")
    print()
    print("LEADERBOARD (effective_score = pool_sharpe × √(trades/1000))")
    print("─" * 72)
    table = sorted(winners.items(), key=lambda kv: effective_score(kv[1][1]), reverse=True)
    for i, (sym, (ovr, m)) in enumerate(table, 1):
        print(f"  {i:3d}. {fmt_canonical(sym, m, m.get('verdict', verdict(m)))}  eff={effective_score(m):+.3f}")
    print()
    print(f"CSV: {sweep_csv}")
    print(f"Candidates: {CAND_OUT_DIR}")
    if args.charts:
        print(f"Charts: {CHARTS_OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
