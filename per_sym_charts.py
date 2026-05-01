#!/usr/bin/env python3
"""per_sym_charts — generate per-symbol PnL charts from optimized config profiles.

Reads winner JSONs from data/hourly_reconfig/trb/_candidates/ (or flz/_candidates/),
re-runs v8_quick_engine with the winning config to get per-trade time series,
then plots cumulative PnL + per-trade bars.

Usage:
  python3 per_sym_charts.py --account trb --out plots/
  python3 per_sym_charts.py --account trb --days 60 --out plots/   # last 60 days of trades
  python3 per_sym_charts.py --account flz --out plots/
  python3 per_sym_charts.py --account trb --sym ADP,CLF,MU --out plots/
  python3 per_sym_charts.py --account trb --all --out plots/   # include FALLBACK too

--days N: show only the most recent N calendar days of trades (0 = all, default 0).
  Adds _60d / _Nd suffix to output filenames.

Runs on S2 (tradier NPZs) or S1 (flz NPZs). Copy charts back to MacBook after.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.patches import Patch
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("WARNING: matplotlib not available — cannot generate charts")

NPZ_DIR = ROOT / "backtest_v8" / "indicators"

PROMOTE_DD_MAX = 10.0
PROMOTE_POOL_MIN = 0.0
PROMOTE_TRADES_MIN = 30

ACCOUNT_CONFIGS = {
    "trb": {
        "cand_dir": ROOT / "data" / "hourly_reconfig" / "trb" / "_candidates",
        "prefix": "trb_",
        "mode": "tradier",
        "label": "trb (stocks)",
    },
    "trc": {
        "cand_dir": ROOT / "data" / "hourly_reconfig" / "trc" / "_candidates",
        "prefix": "trc_",
        "mode": "tradier",
        "label": "trc (stocks)",
    },
    "flz": {
        "cand_dir": ROOT / "data" / "hourly_reconfig" / "_candidates",
        "prefix": "btc_",
        "mode": "crypto",
        "label": "flz (BTC-dedicated)",
    },
    "ang": {
        "cand_dir": ROOT / "data" / "hourly_reconfig" / "ang" / "_candidates",
        "prefix": "ang_sym_",
        "mode": "crypto",
        "label": "ang (crypto)",
    },
    "men": {
        "cand_dir": ROOT / "data" / "hourly_reconfig" / "men" / "_candidates",
        "prefix": "men_sym_",
        "mode": "crypto",
        "label": "men (crypto)",
    },
    "fin": {
        "cand_dir": ROOT / "data" / "hourly_reconfig" / "fin" / "_candidates",
        "prefix": "fin_sym_",
        "mode": "crypto",
        "label": "fin (crypto)",
    },
}

_SIDE_OVRS = {
    "LONG_ONLY": {"LONG_ENABLED": True, "SHORT_ENABLED": False,
                  "WT_DC_LONG_ENABLED": True, "WT_DC_SHORT_ENABLED": False},
    "SHORT_ONLY": {"LONG_ENABLED": False, "SHORT_ENABLED": True,
                   "WT_DC_LONG_ENABLED": False, "WT_DC_SHORT_ENABLED": True},
    "BOTH": {"LONG_ENABLED": True, "SHORT_ENABLED": True,
             "WT_DC_LONG_ENABLED": True, "WT_DC_SHORT_ENABLED": True},
    "LONG": {"LONG_ENABLED": True, "SHORT_ENABLED": False,  # flz variant
             "WT_DC_LONG_ENABLED": True, "WT_DC_SHORT_ENABLED": False},
    "SHORT": {"LONG_ENABLED": False, "SHORT_ENABLED": True,  # flz variant
              "WT_DC_LONG_ENABLED": False, "WT_DC_SHORT_ENABLED": True},
}


def load_winners_per_sym(cand_dir: Path, prefix: str, qualified_only: bool = True) -> List[Dict]:
    """Load per_sym_*_profiles.py winner JSONs (ang/men/fin format).
    Files: {prefix}{sym}_winner.json  with _meta field (no _pool_sharpe fields)."""
    winners = []
    if not cand_dir.exists():
        print(f"  No candidates dir: {cand_dir}")
        return winners
    for p in sorted(cand_dir.glob(f"{prefix}*_winner.json")):
        try:
            d = json.loads(p.read_text())
            meta = d.get("_meta", "")
            pool = 0.0; tg = 0.0; dd = 0.0; tr = 0; wr = 0.0
            for part in meta.split("|"):
                part = part.strip()
                if part.startswith("pool="):
                    try: pool = float(part[5:])
                    except Exception: pass
                elif part.startswith("trades="):
                    try: tr = int(part[7:])
                    except Exception: pass
                elif part.startswith("dd="):
                    try: dd = float(part[3:].rstrip("%"))
                    except Exception: pass
                elif part.startswith("wr="):
                    try: wr = float(part[3:].rstrip("%"))
                    except Exception: pass
            stem = p.stem  # e.g. ang_sym_AAVEUSDC_winner
            sym = stem.replace(prefix, "").replace("_winner", "")
            is_qualified = (pool > PROMOTE_POOL_MIN and dd <= PROMOTE_DD_MAX
                            and tr >= PROMOTE_TRADES_MIN)
            ovr = {k: v for k, v in d.items() if not k.startswith("_")}
            winners.append({
                "sym": sym, "side": "BOTH", "pool": pool, "total_gain": tg,
                "dd": dd, "wr": wr, "trades": tr, "overrides": ovr,
                "qualified": is_qualified, "path": p,
            })
        except Exception as e:
            print(f"  ERR loading {p.name}: {e}")
    if qualified_only:
        winners = [w for w in winners if w["qualified"]]
    return winners


def make_ohlc_overlay_chart(sym: str, npz: dict, trades: List[Tuple], meta: Dict,
                             out_path: Path, days_window: int = 60) -> None:
    """OHLC price chart with entry/exit trade overlays + PnL bars.
    trades: [(pnl_pct, entry_ts, exit_ts, entry_price, exit_price, side), ...]
    where side = 'LONG' or 'SHORT'."""
    if not HAS_MPL:
        return
    if not trades:
        print(f"  {sym}: no trades to chart")
        return
    ts_arr = npz.get("timestamps_15m", npz.get("timestamps", None))
    close_arr = npz.get("close_15m", None)
    if ts_arr is None or close_arr is None or len(ts_arr) == 0:
        print(f"  {sym}: no OHLC data in NPZ, skipping chart")
        return
    cutoff_ts = int(__import__("time").time()) - max(1, days_window) * 86400
    mask = ts_arr >= cutoff_ts
    plot_ts = ts_arr[mask]
    plot_close = close_arr[mask]
    if len(plot_ts) == 0:
        print(f"  {sym}: no recent OHLC bars in last {days_window}d")
        return
    trades_win = [(t[0], t[1], t[2], t[3], t[4], t[5] if len(t) > 5 else "LONG")
                  for t in trades if t[2] >= cutoff_ts and t[1] >= cutoff_ts]
    if not trades_win:
        trades_win = [(t[0], t[1], t[2], t[3], t[4], t[5] if len(t) > 5 else "LONG")
                      for t in trades]
    pnl = [t[0] for t in trades_win]
    cum_pnl = []
    running = 0.0
    for p in pnl:
        running += p
        cum_pnl.append(running)
    entry_ts_list = [t[1] for t in trades_win]
    exit_ts_list = [t[2] for t in trades_win]
    entry_prices = [t[3] for t in trades_win]
    exit_prices = [t[4] for t in trades_win]
    sides = [t[5] for t in trades_win]
    dt_price = [datetime.fromtimestamp(int(ts), tz=timezone.utc) for ts in plot_ts if ts > 0]
    dt_entry = [datetime.fromtimestamp(int(ts), tz=timezone.utc) for ts in entry_ts_list if ts > 0]
    dt_exit = [datetime.fromtimestamp(int(ts), tz=timezone.utc) for ts in exit_ts_list if ts > 0]
    dt_cum = [datetime.fromtimestamp(int(t[2]), tz=timezone.utc) for t in trades_win if t[2] > 0]
    fig = plt.figure(figsize=(18, 11), facecolor="#0d1117")
    fig.patch.set_facecolor("#0d1117")
    gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1.2, 1], hspace=0.06, figure=fig)
    ax_price = fig.add_subplot(gs[0])
    ax_price.set_facecolor("#0d1117")
    if len(dt_price) == len(plot_close):
        ax_price.plot(dt_price, plot_close, color="#c9d1d9", linewidth=0.8, zorder=2)
    if dt_entry and len(dt_entry) == len(entry_prices):
        longs = [(dt_entry[i], entry_prices[i]) for i in range(len(sides)) if sides[i] == "LONG" and i < len(dt_entry)]
        shorts = [(dt_entry[i], entry_prices[i]) for i in range(len(sides)) if sides[i] == "SHORT" and i < len(dt_entry)]
        if longs:
            ax_price.scatter([x[0] for x in longs], [x[1] for x in longs],
                             marker="^", s=60, color="#3fb950", zorder=5, label="LONG entry")
        if shorts:
            ax_price.scatter([x[0] for x in shorts], [x[1] for x in shorts],
                             marker="v", s=60, color="#f0883e", zorder=5, label="SHORT entry")
    if dt_exit and len(dt_exit) == len(exit_prices):
        ax_price.scatter(dt_exit, exit_prices, marker="x", s=40, color="#f85149",
                         zorder=5, linewidths=1.5, label="Exit")
    n_win = sum(1 for p in pnl if p > 0)
    title_str = (f"{sym}  |  pool={meta['pool']:+.4f}  wr={meta['wr']:.1f}%  "
                 f"dd={meta['dd']:.2f}%  trades={meta['trades']}  wins={n_win}/{len(pnl)}")
    ax_price.set_title(title_str, color="#c9d1d9", fontsize=10, pad=6)
    ax_price.set_ylabel("Price", color="#c9d1d9", fontsize=9)
    ax_price.tick_params(colors="#6e7681", labelsize=7)
    ax_price.legend(loc="upper left", fontsize=8, facecolor="#161b22",
                    edgecolor="#30363d", labelcolor="#c9d1d9")
    for spine in ax_price.spines.values():
        spine.set_color("#30363d")
    ax_price.grid(color="#21262d", linewidth=0.4, zorder=0)
    ax_cum = fig.add_subplot(gs[1], sharex=ax_price)
    ax_cum.set_facecolor("#0d1117")
    if dt_cum and len(dt_cum) == len(cum_pnl):
        ax_cum.plot(dt_cum, cum_pnl, color="#58a6ff", linewidth=1.3, zorder=3)
        ax_cum.fill_between(dt_cum, cum_pnl, alpha=0.12, color="#58a6ff")
    ax_cum.axhline(0, color="#6e7681", linewidth=0.5, linestyle="--")
    ax_cum.set_ylabel("Cum PnL %", color="#c9d1d9", fontsize=9)
    ax_cum.tick_params(colors="#6e7681", labelsize=7)
    for spine in ax_cum.spines.values():
        spine.set_color("#30363d")
    ax_cum.grid(color="#21262d", linewidth=0.4, zorder=0)
    ax_bar = fig.add_subplot(gs[2], sharex=ax_price)
    ax_bar.set_facecolor("#0d1117")
    if dt_cum and len(dt_cum) == len(pnl):
        colors_bar = ["#3fb950" if p > 0 else "#f85149" for p in pnl]
        ax_bar.bar(dt_cum, pnl, color=colors_bar, width=0.4, zorder=3)
    ax_bar.axhline(0, color="#6e7681", linewidth=0.5, linestyle="--")
    ax_bar.set_ylabel("Trade %", color="#c9d1d9", fontsize=9)
    ax_bar.tick_params(colors="#6e7681", labelsize=7)
    for spine in ax_bar.spines.values():
        spine.set_color("#30363d")
    ax_bar.grid(color="#21262d", linewidth=0.4, zorder=0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  → {out_path.name}")


def load_winners(cand_dir: Path, prefix: str, qualified_only: bool = True) -> List[Dict]:
    winners = []
    if not cand_dir.exists():
        print(f"  No candidates dir: {cand_dir}")
        return winners
    for p in sorted(cand_dir.glob(f"{prefix}*_winner.json")):
        try:
            d = json.loads(p.read_text())
            pool = d.get("_pool_sharpe", 0.0)
            tg = d.get("_total_gain_pct", 0.0)
            dd = d.get("_dd", 0.0)
            tr = d.get("_trades", 0)
            wr = d.get("_wr", 0.0)
            side = d.get("_best_side", "LONG_ONLY")
            stem = p.stem  # e.g. trb_ADP_SHORT_ONLY_winner or btc_BTCUSDC_LONG_top
            parts = stem.split("_")
            # sym is second segment; rest before _winner/_top is side
            sym = parts[1]
            is_qualified = (pool > PROMOTE_POOL_MIN and dd <= PROMOTE_DD_MAX
                            and tr >= PROMOTE_TRADES_MIN and tg > 0)
            ovr = {k: v for k, v in d.items() if not k.startswith("_")}
            winners.append({
                "sym": sym, "side": side, "pool": pool, "total_gain": tg,
                "dd": dd, "wr": wr, "trades": tr, "overrides": ovr,
                "qualified": is_qualified, "path": p,
            })
        except Exception as e:
            print(f"  ERR loading {p.name}: {e}")
    if qualified_only:
        winners = [w for w in winners if w["qualified"]]
    return winners


def run_engine(sym: str, overrides: Dict, mode: str, run_dir: Path, run_id: str,
               npz: dict) -> List[Tuple[float, int, int, float, float]]:
    """Returns [(pnl_pct, entry_ts, exit_ts, entry_price, exit_price), ...]."""
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    try:
        from v8_quick_engine import simulate, QuickConfig
    except ImportError as e:
        print(f"  Cannot import v8_quick_engine: {e}")
        return []
    cfg = QuickConfig()
    cfg.MODE = mode
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
        print(f"  [engine] EXC {sym}: {e}")
        return []
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    trades = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    trades.append((
                        float(rec.get("pnl_pct", 0.0)),
                        int(rec.get("entry_ts", 0)),
                        int(rec.get("exit_ts", 0)),
                        float(rec.get("entry_price", 0.0)),
                        float(rec.get("exit_price", 0.0)),
                    ))
                except Exception:
                    pass
    return trades


def make_chart(sym: str, side: str, trades: List[Tuple], meta: Dict,
               overrides: Dict, out_path: Path, days_window: int = 0) -> None:
    if not trades:
        print(f"  {sym}: no trades to chart")
        return

    # Optional: filter to last N calendar days of trades
    if days_window > 0:
        cutoff_ts = int(__import__("time").time()) - days_window * 86400
        trades = [t for t in trades if t[2] >= cutoff_ts]
        if not trades:
            print(f"  {sym}: no trades in last {days_window}d window")
            return

    pnl = [t[0] for t in trades]
    exit_ts = [t[2] for t in trades]
    exit_dt = [datetime.fromtimestamp(ts, tz=timezone.utc) for ts in exit_ts if ts > 0]
    if not exit_dt:
        exit_dt = list(range(len(pnl)))

    cum_pnl = []
    running = 0.0
    for p in pnl:
        running += p
        cum_pnl.append(running)

    n_win = sum(1 for p in pnl if p > 0)
    n_lose = len(pnl) - n_win
    pool = meta["pool"]
    total_gain = meta["total_gain"]
    dd = meta["dd"]
    wr = meta["wr"]
    tr = meta["trades"]

    side_keys = {"LONG_ENABLED", "SHORT_ENABLED", "WT_DC_LONG_ENABLED", "WT_DC_SHORT_ENABLED"}
    ovr_str = " | ".join(f"{k}={v}" for k, v in overrides.items() if k not in side_keys)
    if not ovr_str:
        ovr_str = "baseline"

    fig = plt.figure(figsize=(16, 9), facecolor="#0d1117")
    fig.patch.set_facecolor("#0d1117")
    gs = gridspec.GridSpec(2, 1, height_ratios=[2, 1], hspace=0.08, figure=fig)

    # ── Top: cumulative PnL curve ──────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor("#0d1117")
    ax1.plot(exit_dt if isinstance(exit_dt[0], datetime) else range(len(cum_pnl)),
             cum_pnl, color="#58a6ff", linewidth=1.5, zorder=3)
    ax1.fill_between(exit_dt if isinstance(exit_dt[0], datetime) else range(len(cum_pnl)),
                     cum_pnl, alpha=0.15, color="#58a6ff")
    ax1.axhline(0, color="#6e7681", linewidth=0.6, linestyle="--")
    ax1.set_ylabel("Cumulative PnL %", color="#c9d1d9", fontsize=10)
    ax1.tick_params(colors="#6e7681", labelsize=8)
    for spine in ax1.spines.values():
        spine.set_color("#30363d")
    ax1.xaxis.set_visible(False)
    ax1.grid(axis="y", color="#21262d", linewidth=0.5, zorder=0)

    title = (f"{sym}  ·  {side.replace('_', ' ')}  ·  "
             f"pool_sharpe={pool:+.4f}  total={total_gain:+.1f}%  "
             f"dd={dd:.2f}%  wr={wr:.1f}%  trades={tr}")
    ax1.set_title(title, color="#c9d1d9", fontsize=11, pad=8, fontweight="bold")

    ovr_label = f"config: {ovr_str}"
    ax1.text(0.01, 0.02, ovr_label, transform=ax1.transAxes,
             fontsize=7.5, color="#8b949e", va="bottom")

    # ── Bottom: per-trade bars ─────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor("#0d1117")
    x = exit_dt if isinstance(exit_dt[0], datetime) else range(len(pnl))
    colors = ["#3fb950" if p > 0 else "#f85149" for p in pnl]
    if isinstance(x[0], datetime):
        ax2.bar(x, pnl, color=colors, width=0.5, zorder=3)
    else:
        ax2.bar(x, pnl, color=colors, zorder=3)
    ax2.axhline(0, color="#6e7681", linewidth=0.5, linestyle="--")
    ax2.set_ylabel("Trade PnL %", color="#c9d1d9", fontsize=9)
    ax2.tick_params(colors="#6e7681", labelsize=8)
    for spine in ax2.spines.values():
        spine.set_color("#30363d")
    ax2.grid(axis="y", color="#21262d", linewidth=0.5, zorder=0)

    legend_patches = [
        Patch(facecolor="#3fb950", label=f"Win ({n_win})"),
        Patch(facecolor="#f85149", label=f"Loss ({n_lose})"),
    ]
    ax2.legend(handles=legend_patches, loc="upper right", fontsize=8,
               facecolor="#161b22", edgecolor="#30363d",
               labelcolor="#c9d1d9")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  → {out_path.name}")


def process_winner(w: Dict, mode: str, run_root: Path, out_dir: Path,
                   days_window: int = 0) -> Optional[Path]:
    sym = w["sym"]
    side = w["side"]
    ovr = w["overrides"]
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists():
        print(f"  {sym}: NPZ not found at {npz_path}")
        return None
    print(f"  {sym} ({side}) — running engine...")
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()

    run_dir = run_root / f"{sym}_{side}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"chart_{sym}_{side}_{int(time.time())}"
    trades = run_engine(sym, ovr, mode, run_dir, run_id, npz)
    if not trades:
        print(f"  {sym}: engine produced 0 trades")
        return None

    suffix = f"_{days_window}d" if days_window > 0 else ""
    acct_prefix = "trb" if mode == "tradier" else "flz"
    out_path = out_dir / f"{acct_prefix}_{sym}_{side.replace('_', '').lower()}{suffix}.png"
    make_chart(sym, side, trades, w, {k: v for k, v in ovr.items()
                                      if k not in {"LONG_ENABLED","SHORT_ENABLED",
                                                   "WT_DC_LONG_ENABLED","WT_DC_SHORT_ENABLED"}},
               out_path, days_window=days_window)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="trb", choices=list(ACCOUNT_CONFIGS.keys()))
    ap.add_argument("--sym", default="", help="comma-separated subset (default: qualified only)")
    ap.add_argument("--all", dest="all_winners", action="store_true",
                    help="include FALLBACK winners too (default: QUALIFIED only)")
    ap.add_argument("--out", default="plots", help="output directory")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--days", type=int, default=0,
                    help="show only last N calendar days of trades (0=all); adds _Nd filename suffix")
    args = ap.parse_args()

    if not HAS_MPL:
        print("ERROR: matplotlib required. Run: pip install matplotlib")
        return 1

    cfg = ACCOUNT_CONFIGS[args.account]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    qualified_only = not args.all_winners
    _per_sym_accts = {"ang", "men", "fin"}
    if args.account in _per_sym_accts:
        winners = load_winners_per_sym(cfg["cand_dir"], cfg["prefix"], qualified_only=qualified_only)
    else:
        winners = load_winners(cfg["cand_dir"], cfg["prefix"], qualified_only=qualified_only)

    if args.sym:
        syms = {s.strip() for s in args.sym.split(",") if s.strip()}
        winners = [w for w in winners if w["sym"] in syms]

    days_window = max(0, args.days)
    print(f"\nper_sym_charts — {cfg['label']}")
    print(f"  {len(winners)} winners to chart ({'qualified' if qualified_only else 'all'})")
    print(f"  NPZ dir: {NPZ_DIR}")
    print(f"  Output: {out_dir}")
    if days_window > 0:
        print(f"  Window: last {days_window} days of trades")
    print()

    run_root = Path("/tmp") / f"chart_runs_{int(time.time())}"
    run_root.mkdir(parents=True, exist_ok=True)

    generated = []
    for w in winners:
        try:
            out_path = process_winner(w, cfg["mode"], run_root, out_dir, days_window=days_window)
            if out_path:
                generated.append(out_path)
        except Exception as e:
            print(f"  ERR {w['sym']}: {e}")

    print(f"\nGenerated {len(generated)} charts in {out_dir}/")
    for p in generated:
        print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
