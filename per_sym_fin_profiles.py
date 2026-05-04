#!/usr/bin/env python3
"""per_sym_fin_profiles — standard-path per-sym sweep for fin account symbols.

Sweeps WA_MIN_GAIN_PCT + PYRAMID_MIN_GAIN_PCT (1–4%) plus core v8_quick timing
knobs for all fin symbols that have NPZ files. Writes winner JSONs to
data/hourly_reconfig/fin/_candidates/ for future per-sym config overlay wiring.

Differences from per_sym_flz8_profiles.py:
  - Uses STANDARD entry/exit path (not BTC_DEDICATED)
  - Symbols = intersection of symbols_fin.json and available NPZ files
  - Mutation grid covers WA_MIN_GAIN_PCT, PYRAMID_MIN_GAIN_PCT, ENTRY_SCORE,
    MIN_HOLD_BARS, WT_EXIT_MIN_TFS, COOLDOWN_BARS, D_TREND_REQUIRED
  - Output dir: data/hourly_reconfig/fin/_candidates/
  - CSV: data/sweep_results/canonical_per_sym_fin_<ts>.csv
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

import shutil

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
SWEEP_DIR = ROOT / "data" / "sweep_results"
CAND_OUT_DIR = ROOT / "data" / "hourly_reconfig" / "fin" / "_candidates"
ACTIVE_CONFIG_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
CHARTS_OUT_DIR = ROOT / "plots"
SYMBOLS_FILE = ROOT / "symbols_fin.json"

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def _prune_old_per_sym_runs(sweep_dir: Path, prefix: str, keep: int = 2) -> None:
    dirs = sorted(sweep_dir.glob(f"{prefix}_*/"), key=lambda p: p.name)
    for old in dirs[:-keep] if keep > 0 else dirs:
        try:
            shutil.rmtree(old)
        except Exception as e:
            print(f"  [prune] could not remove {old}: {e}")


# Promote criteria — same revised thresholds as flz
PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 5000
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_PER_YR_MIN = 100.0
PROMOTE_WR_MIN = 60.0


def load_fin_symbols() -> List[str]:
    syms = json.loads(SYMBOLS_FILE.read_text())
    return [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]


def load_full_trades(run_dir: Path, run_id: str, sym: str) -> List[Dict]:
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


def _load_tf_ohlc(sym: str, tf: str) -> Optional[tuple]:
    """Load (timestamps, high, low, close) arrays from klines_cache_backtest JSON."""
    klines_dir = ROOT / "klines_cache_backtest"
    for name in [sym, sym.replace("USDC", "USDT")]:
        p = klines_dir / f"{name}_{tf}.json"
        if p.exists():
            try:
                bars = json.loads(p.read_text())
                if not bars:
                    continue
                ts = np.array([b["timestamp"] for b in bars], dtype=np.float64)
                h = np.array([float(b.get("high", b["close"])) for b in bars])
                l = np.array([float(b.get("low", b["close"])) for b in bars])
                c = np.array([float(b["close"]) for b in bars])
                return ts, h, l, c
            except Exception:
                pass
    return None


def generate_sym_chart(sym: str, npz: dict, trades: List[Dict], m: Dict,
                       overrides: Dict, days: int = 60) -> Optional[Path]:
    """Generate multi-TF OHLC chart matching ez_rankings dark theme with trade overlays."""
    if not HAS_MPL:
        return None
    BG = "#272d30"
    LINE = "#c0c0c0"
    GRID = "#4a4a4a"
    TICK = "#d0d0d0"
    TITLE = "#9c864e"
    cutoff_ts = time.time() - max(1, days) * 86400
    trades_w = [t for t in trades if t.get("exit_ts", 0) >= cutoff_ts] or list(trades)
    pnl = [float(t.get("pnl_pct", 0)) for t in trades_w]
    cum_pnl: List[float] = []
    s = 0.0
    for p in pnl:
        s += p
        cum_pnl.append(s)
    dt_cum = [datetime.fromtimestamp(int(t.get("exit_ts", 0)), tz=timezone.utc)
              for t in trades_w if t.get("exit_ts", 0) > 0]
    n_win = sum(1 for p in pnl if p > 0)
    TFS = ["4h", "1h", "15m", "3m"]
    fig = plt.figure(figsize=(18, 4 * (len(TFS) + 1)), facecolor=BG, dpi=110)
    gs = gridspec.GridSpec(len(TFS) + 1, 1, height_ratios=[3, 2, 2, 2, 1.5], hspace=0.08, figure=fig)
    fig.patch.set_facecolor(BG)

    def _style(ax):
        ax.set_facecolor(BG)
        for sp in ax.spines.values():
            sp.set_color(TICK)
        ax.tick_params(colors=TICK, labelsize=7)
        ax.grid(color=GRID, alpha=0.5, linestyle=":")

    def _trade_markers(ax):
        tr = [(datetime.fromtimestamp(int(t["entry_ts"]), tz=timezone.utc),
               float(t.get("entry_price", 0)),
               t.get("side", "LONG"),
               datetime.fromtimestamp(int(t["exit_ts"]), tz=timezone.utc),
               float(t.get("exit_price", 0)))
              for t in trades_w if t.get("entry_ts", 0) > 0 and t.get("exit_ts", 0) > 0]
        longs = [(e, ep) for e, ep, side, _, _ in tr if side == "LONG"]
        shorts = [(e, ep) for e, ep, side, _, _ in tr if side == "SHORT"]
        exits = [(x, xp) for _, _, _, x, xp in tr]
        if longs:
            ax.scatter([v[0] for v in longs], [v[1] for v in longs],
                       marker="^", s=45, color="#3fb950", zorder=5, alpha=0.85)
        if shorts:
            ax.scatter([v[0] for v in shorts], [v[1] for v in shorts],
                       marker="v", s=45, color="#f0883e", zorder=5, alpha=0.85)
        if exits:
            ax.scatter([v[0] for v in exits], [v[1] for v in exits],
                       marker="x", s=28, color="#f85149", zorder=5, linewidths=1.2, alpha=0.75)

    for i, tf in enumerate(TFS):
        ax = fig.add_subplot(gs[i])
        _style(ax)
        ohlc = _load_tf_ohlc(sym, tf)
        if ohlc is not None:
            ts_a, h_a, l_a, c_a = ohlc
            mask = ts_a >= cutoff_ts
            ts_a, h_a, l_a, c_a = ts_a[mask], h_a[mask], l_a[mask], c_a[mask]
            if len(ts_a) > 0:
                dts = [datetime.fromtimestamp(int(v), tz=timezone.utc) for v in ts_a]
                ax.plot(dts, c_a, color=LINE, lw=0.9, zorder=2)
                ax.fill_between(dts, l_a, h_a, alpha=0.10, color=LINE, zorder=1)
        else:
            npz_close = npz.get(f"close_{tf}")
            npz_ts = npz.get("timestamps_15m", npz.get("timestamps"))
            if npz_close is not None and npz_ts is not None:
                n = min(len(npz_close), len(npz_ts))
                mask = npz_ts[:n] >= cutoff_ts
                dts = [datetime.fromtimestamp(int(v), tz=timezone.utc) for v in npz_ts[:n][mask]]
                ax.plot(dts, npz_close[:n][mask], color=LINE, lw=0.9, zorder=2)
        _trade_markers(ax)
        ax.set_ylabel(tf, color=TICK, fontsize=8)
        if i == 0:
            ovr_str = "  ".join(f"{k}={v}" for k, v in list(overrides.items())[:10]
                                if not k.startswith("_"))
            ax.set_title(
                f"OPT | {sym}  pool={m['pool_sharpe']:+.4f}  wr={m.get('wr_pct', 0):.1f}%  "
                f"dd={m.get('max_dd_pct', 0):.1f}%  trades={m['trades']}  wins={n_win}/{len(pnl)}  ({days}d)\n"
                f"{ovr_str}",
                color=TITLE, fontsize=9, pad=4,
            )

    ax_pnl = fig.add_subplot(gs[len(TFS)])
    _style(ax_pnl)
    if dt_cum and cum_pnl:
        ax_pnl.plot(dt_cum, cum_pnl, color="#58a6ff", lw=1.3, zorder=3)
        ax_pnl.fill_between(dt_cum, cum_pnl, alpha=0.15, color="#58a6ff")
        ax_pnl.axhline(0, color="#6e7681", lw=0.5, linestyle="--")
    ax_pnl.set_ylabel("Cum PnL%", color=TICK, fontsize=8)

    CHARTS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CHARTS_OUT_DIR / f"OPT_{sym}.png"
    plt.savefig(out_path, dpi=110, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  [chart] {out_path.name}")
    return out_path


def promote_winners_to_active_config(winners: Dict[str, Tuple[Dict, Dict]]) -> None:
    existing: Dict = {}
    if ACTIVE_CONFIG_PATH.exists():
        try: existing = json.loads(ACTIVE_CONFIG_PATH.read_text())
        except Exception: existing = {}
    now_tag = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    for sym, (ovr, m) in winners.items():
        clean_ovr = {k: v for k, v in ovr.items() if not k.startswith("_")}
        live_ovr = {k: v for k, v in clean_ovr.items() if k == 'ENTRY_SCORE_THRESHOLD'}
        ps = float(m.get("pool_sharpe", 0)); tr = int(m.get("trades", 0))
        if ps <= 0 or tr < 30 or not live_ovr:
            print(f"  [active_config] SKIP {sym}: pool_sharpe={ps:+.4f} trades={tr} live_ovr={live_ovr} — below quality gate or no live-applicable params")
            continue
        entry = {"winning_tag": f"per_sym_{sym}_{now_tag}", "wsharpe": ps,
                 "trades": tr, "sample_tag": "PER_SYM", "overrides": live_ovr}
        for side in ("LONG", "SHORT"): existing[f"{sym}_{side}"] = entry
    ACTIVE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ACTIVE_CONFIG_PATH.open("w") as f: json.dump(existing, f, indent=2, default=str)
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
        "max_dd_pct": worst, "n_wins": n_wins, "tag": f"fin_per_sym_{sym}",
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
    """Sweep grid for standard fin path — MIN_GAIN, hold, exit strictness, score."""
    grid: List[Tuple[str, Dict]] = []
    base: Dict = {}
    grid.append(("baseline", dict(base)))

    # 1. MIN_GAIN / augment threshold — the main target of this sweep
    for mg_pct in (1.0, 2.0, 3.0, 4.0):
        o = {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct}
        grid.append((f"mingain_{mg_pct:.0f}pct", o))

    # 2. Entry score threshold
    for es in (12.0, 15.0, 18.0, 24.0):
        grid.append((f"es_{es:.0f}", {"ENTRY_SCORE_THRESHOLD": es}))

    # 3. MIN_HOLD_BARS
    for mh in (3, 5, 10, 20):
        grid.append((f"mh_{mh}", {"MIN_HOLD_BARS": mh}))

    # 4. WT exit strictness
    for wt in (1, 2, 3, 4):
        grid.append((f"wt_exit_{wt}", {"WT_EXIT_MIN_TFS": wt}))

    # 5. Cooldown
    for cd in (1, 3, 6, 12):
        grid.append((f"cd_{cd}", {"COOLDOWN_BARS": cd}))

    # 6. D_TREND_REQUIRED
    grid.append(("dtrend_on", {"D_TREND_REQUIRED": True}))
    grid.append(("dtrend_off", {"D_TREND_REQUIRED": False}))

    # 7. HTF alignment
    for htf in (1, 2, 3):
        grid.append((f"htf_{htf}", {"HTF_MIN_ALIGNED": htf}))

    # 8. MIN_GAIN × entry score combos (top-movers crossed)
    for mg_pct, es in ((1.0, 15.0), (2.0, 18.0), (3.0, 18.0), (4.0, 24.0),
                       (1.0, 24.0), (2.0, 15.0)):
        grid.append((f"mg{mg_pct:.0f}_es{es:.0f}",
                     {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
                      "ENTRY_SCORE_THRESHOLD": es}))

    # 9. MIN_GAIN × hold combos
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
        m["tag"] = f"fin_mut_{sym}_{tag}"
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
    # Inject best ENTRY_SCORE_THRESHOLD — the only param live trading can apply from active_config
    if 'ENTRY_SCORE_THRESHOLD' not in ovr:
        baseline_eff = next((m2['effective_score'] for t2, _, m2 in results if t2 == 'baseline'), 0.0)
        es_only = [(t2, o2, m2) for t2, o2, m2 in results
                   if list(o2.keys()) == ['ENTRY_SCORE_THRESHOLD'] and m2['trades'] >= 30]
        if es_only:
            best_es = max(es_only, key=lambda x: x[2]['effective_score'])
            if best_es[2]['effective_score'] > baseline_eff + 0.01:
                ovr = dict(ovr)
                ovr['ENTRY_SCORE_THRESHOLD'] = best_es[1]['ENTRY_SCORE_THRESHOLD']
                print(f"  [LIVE_INJECT] Added ENTRY_SCORE_THRESHOLD={ovr['ENTRY_SCORE_THRESHOLD']} "
                      f"(eff={best_es[2]['effective_score']:+.3f} vs baseline={baseline_eff:+.3f})")
    return ovr, m, winner_trades, run_dir / sym, winner_run_id


def write_winner_json(sym: str, overrides: Dict, m: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CAND_OUT_DIR / f"fin_sym_{sym}_winner.json"
    payload = dict(overrides)
    payload["_meta"] = (
        f"fin per_sym winner for {sym} | pool={m['pool_sharpe']:+.4f} "
        f"trades={m['trades']} dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
        f"verdict={m.get('verdict')} effective_score={m.get('effective_score', 0):+.3f}"
    )
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="", help="comma-separated subset (default all with NPZ)")
    ap.add_argument("--years", type=float, default=2.0, help="assumed years for metrics (default 2.0)")
    ap.add_argument("--chart-days", type=int, default=60, help="days window for charts (default 60)")
    args = ap.parse_args()

    all_syms = load_fin_symbols()
    syms = [s for s in args.syms.split(",") if s] if args.syms else all_syms
    syms = [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]

    print("=" * 72)
    print(f"per_sym_fin_profiles — {len(syms)} symbols")
    print(f"NPZ dir: {NPZ_DIR}")
    print(f"Out: {CAND_OUT_DIR}")
    print("=" * 72)
    print(f"Symbols: {syms}")

    ts = int(time.time())
    sweep_csv = SWEEP_DIR / f"canonical_per_sym_fin_{ts}.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / f"per_sym_fin_{ts}" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)

    winners: Dict[str, Tuple[Dict, Dict]] = {}
    for sym in syms:
        try:
            ovr, m, winner_trades, _run_dir, _run_id = sweep_sym(sym, run_root, sweep_csv, args.years)
            winners[sym] = (ovr, m)
            p = write_winner_json(sym, ovr, m)
            print(f"  [winner] {p.name}")
            if HAS_MPL and winner_trades:
                try:
                    z = np.load(str(NPZ_DIR / f"{sym}.npz"))
                    npz_c = {k: z[k] for k in z.files}
                    z.close()
                    generate_sym_chart(sym, npz_c, winner_trades, m, ovr, days=args.chart_days)
                except Exception as ce:
                    print(f"  [chart] ERR {sym}: {ce}")
        except Exception as e:
            print(f"  EXC {sym}: {e}")
        gc.collect()

    print()
    print("=" * 72)
    print("Promoting winners to active_config")
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
        eff = effective_score(m)
        v = m.get("verdict", verdict(m))
        print(f"  {i:3d}. {fmt_canonical(sym, m, v)}  eff={eff:+.3f}")

    rejects = [(sym, m) for sym, (ovr, m) in winners.items() if m["max_dd_pct"] > PROMOTE_DD_MAX]
    if rejects:
        print()
        print("REJECT_DD (DD > 10%):")
        for sym, m in rejects:
            print(f"  {sym}: dd={m['max_dd_pct']:.2f}%")

    print()
    print(f"CSV: {sweep_csv}")
    print(f"Candidates: {CAND_OUT_DIR}")
    print(f"Charts: {CHARTS_OUT_DIR}")
    _prune_old_per_sym_runs(SWEEP_DIR, "per_sym_fin", keep=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
