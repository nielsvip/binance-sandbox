#!/usr/bin/env python3
"""per_sym_charts — generate per-symbol PnL charts from optimized config profiles.

Reads winner JSONs from data/hourly_reconfig/trb/_candidates/ (or flz/_candidates/),
re-runs v8_quick_engine with the winning config to get per-trade time series,
then plots cumulative PnL + per-trade bars.

Usage:
  python3 per_sym_charts.py --account trb --out plots/
  python3 per_sym_charts.py --account flz --out plots/
  python3 per_sym_charts.py --account trb --sym ADP,CLF,MU --out plots/
  python3 per_sym_charts.py --account trb --all --out plots/   # include FALLBACK too

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
               overrides: Dict, out_path: Path) -> None:
    if not trades:
        print(f"  {sym}: no trades to chart")
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

    fig = plt.figure(figsize=(14, 8), facecolor="#0d1117")
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
    plt.savefig(out_path, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  → {out_path.name}")


def process_winner(w: Dict, mode: str, run_root: Path, out_dir: Path) -> Optional[Path]:
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

    out_path = out_dir / f"trb_{sym}_{side.replace('_', '').lower()}.png"
    make_chart(sym, side, trades, w, {k: v for k, v in ovr.items()
                                      if k not in {"LONG_ENABLED","SHORT_ENABLED",
                                                   "WT_DC_LONG_ENABLED","WT_DC_SHORT_ENABLED"}},
               out_path)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="trb", choices=list(ACCOUNT_CONFIGS.keys()))
    ap.add_argument("--sym", default="", help="comma-separated subset (default: qualified only)")
    ap.add_argument("--all", dest="all_winners", action="store_true",
                    help="include FALLBACK winners too (default: QUALIFIED only)")
    ap.add_argument("--out", default="plots", help="output directory")
    ap.add_argument("--years", type=float, default=2.0)
    args = ap.parse_args()

    if not HAS_MPL:
        print("ERROR: matplotlib required. Run: pip install matplotlib")
        return 1

    cfg = ACCOUNT_CONFIGS[args.account]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    qualified_only = not args.all_winners
    winners = load_winners(cfg["cand_dir"], cfg["prefix"], qualified_only=qualified_only)

    if args.sym:
        syms = {s.strip() for s in args.sym.split(",") if s.strip()}
        winners = [w for w in winners if w["sym"] in syms]

    print(f"\nper_sym_charts — {cfg['label']}")
    print(f"  {len(winners)} winners to chart ({'qualified' if qualified_only else 'all'})")
    print(f"  NPZ dir: {NPZ_DIR}")
    print(f"  Output: {out_dir}")
    print()

    run_root = Path("/tmp") / f"chart_runs_{int(time.time())}"
    run_root.mkdir(parents=True, exist_ok=True)

    generated = []
    for w in winners:
        try:
            out_path = process_winner(w, cfg["mode"], run_root, out_dir)
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
