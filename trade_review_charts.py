#!/usr/bin/env python3
"""trade_review_charts.py — OHLC candlestick charts with trade overlays for trade review.

Produces 3-panel charts per symbol:
  Panel 1 (60%): OHLC candlestick price chart with entry/exit overlays
  Panel 2 (25%): Cumulative PnL %
  Panel 3 (15%): Per-trade PnL bars

Usage:
  python3 trade_review_charts.py --account flz --days 60 --out plots/review/
  python3 trade_review_charts.py --account trb --days 60 --out plots/review/
  python3 trade_review_charts.py --sym BTCUSDC,ETHUSDC --days 60
  python3 trade_review_charts.py --all-accounts --days 60

Data sources (in priority order):
  1. data/canonical_trades/{run_dir}/  — glob for *__{sym}.jsonl
  2. data/hourly_reconfig/{account}/_candidates/  — winner JSONs (re-run engine)
  3. data/decisions/{date}_{account}.jsonl  — live decisions fallback

NPZ: backtest_v8/indicators/{sym}.npz
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
    from matplotlib.patches import Patch, Rectangle
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    print("WARNING: matplotlib not available — install with: pip install matplotlib")

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
CANONICAL_TRADES_DIR = ROOT / "data" / "canonical_trades"
DECISIONS_DIR = ROOT / "data" / "decisions"
RECONFIG_DIR = ROOT / "data" / "hourly_reconfig"

BG = "#0d1117"
GRID = "#21262d"
TICK = "#6e7681"
TITLE_C = "#c9d1d9"
GREEN = "#3fb950"
RED = "#f85149"
ORANGE = "#f0883e"
BLUE = "#58a6ff"
DIM = "#8b949e"

ACCOUNT_CONFIGS = {
    "flz": {
        "canonical_dir_pattern": "flz8_BEST_*",
        "jsonl_prefix": "flz8_BEST__",
        "cand_dir": RECONFIG_DIR / "_candidates",
        "cand_prefix": "btc_",
        "mode": "crypto",
        "base_tf": "15m",
        "label": "flz (BTC-dedicated)",
    },
    "ang": {
        "canonical_dir_pattern": "canonical_48sym_*",
        "jsonl_prefix": "canonical_48sym_BEST__",
        "cand_dir": RECONFIG_DIR / "ang" / "_candidates",
        "cand_prefix": "ang_sym_",
        "mode": "crypto",
        "base_tf": "15m",
        "label": "ang (crypto)",
    },
    "fin": {
        "canonical_dir_pattern": "canonical_48sym_*",
        "jsonl_prefix": "canonical_48sym_BEST__",
        "cand_dir": RECONFIG_DIR / "fin" / "_candidates",
        "cand_prefix": "fin_sym_",
        "mode": "crypto",
        "base_tf": "15m",
        "label": "fin (crypto)",
    },
    "men": {
        "canonical_dir_pattern": "canonical_48sym_*",
        "jsonl_prefix": "canonical_48sym_BEST__",
        "cand_dir": RECONFIG_DIR / "men" / "_candidates",
        "cand_prefix": "men_sym_",
        "mode": "crypto",
        "base_tf": "15m",
        "label": "men (crypto)",
    },
    "inf": {
        "canonical_dir_pattern": "canonical_48sym_*",
        "jsonl_prefix": "canonical_48sym_BEST__",
        "cand_dir": RECONFIG_DIR / "inf" / "_candidates",
        "cand_prefix": "inf_sym_",
        "mode": "crypto",
        "base_tf": "15m",
        "label": "inf (crypto)",
    },
    "trb": {
        "canonical_dir_pattern": None,
        "jsonl_prefix": None,
        "cand_dir": RECONFIG_DIR / "trb" / "_candidates",
        "cand_prefix": "trb_",
        "mode": "tradier",
        "base_tf": "5m",
        "label": "trb (stocks)",
    },
    "trc": {
        "canonical_dir_pattern": None,
        "jsonl_prefix": None,
        "cand_dir": RECONFIG_DIR / "trc" / "_candidates",
        "cand_prefix": "trc_",
        "mode": "tradier",
        "base_tf": "5m",
        "label": "trc (stocks)",
    },
}

ALL_ACCOUNTS = list(ACCOUNT_CONFIGS.keys())


def _pool_sharpe(rets: List[float]) -> float:
    if len(rets) < 2:
        return 0.0
    a = np.array(rets, dtype=float)
    s = float(np.std(a))
    if s < 1e-12:
        return 0.0
    return float(np.mean(a) / s)


def load_jsonl_trades(path: Path, cutoff_ts: float = 0.0) -> List[Dict]:
    trades: List[Dict] = []
    if not path.exists():
        return trades
    with path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
                if cutoff_ts > 0:
                    if float(rec.get("exit_ts", 0) or rec.get("entry_ts", 0) or 0) < cutoff_ts:
                        continue
                if rec.get("pnl_pct") is not None:
                    trades.append(rec)
            except Exception:
                pass
    return trades


def find_canonical_jsonl(account: str, sym: str) -> Optional[Path]:
    cfg = ACCOUNT_CONFIGS[account]
    pattern = cfg.get("canonical_dir_pattern")
    prefix = cfg.get("jsonl_prefix")
    if not pattern or not prefix:
        return None
    # Find the newest canonical dir matching the pattern
    dirs = sorted(CANONICAL_TRADES_DIR.glob(pattern), key=lambda p: p.name)
    for d in reversed(dirs):
        p = d / f"{prefix}{sym}.jsonl"
        if p.exists():
            return p
    return None


def load_trades_from_decisions(account: str, sym: str, days: int = 60) -> List[Dict]:
    cutoff = time.time() - days * 86400
    trades: List[Dict] = []
    # Scan decision files newest-first
    all_files = sorted(DECISIONS_DIR.glob(f"decisions_{account}_*.jsonl"))
    for f in reversed(all_files):
        try:
            with f.open() as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                        # Live decisions have action=CLOSE/QUICK_CLOSE and trade dict
                        action = rec.get("action", "")
                        if "CLOSE" not in action and "close" not in action:
                            continue
                        trade_d = rec.get("trade", {})
                        pk = rec.get("position_key", "")
                        side = "LONG" if pk.endswith("_LONG") else "SHORT" if pk.endswith("_SHORT") else ""
                        rec_sym = pk.split(":")[-1].rsplit("_", 1)[0] if ":" in pk else ""
                        if rec_sym != sym or not side:
                            continue
                        ts_str = rec.get("timestamp", "")
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                        except Exception:
                            continue
                        if ts < cutoff:
                            continue
                        gain = float(trade_d.get("gain_pct", 0.0))
                        entry_p = float(trade_d.get("entry_price", 0.0))
                        close_p = float(trade_d.get("price", 0.0))
                        trades.append({
                            "symbol": rec_sym,
                            "side": side,
                            "entry_ts": int(ts) - 900,  # estimate; decisions only have close
                            "entry_price": entry_p,
                            "exit_ts": int(ts),
                            "exit_price": close_p,
                            "pnl_pct": gain,
                            "duration_bars": 1,
                            "_source": "decisions",
                        })
                    except Exception:
                        pass
        except Exception:
            pass
    return trades


def load_trades_for_sym(account: str, sym: str, days: int = 60) -> List[Dict]:
    cutoff = time.time() - days * 86400
    # 1. Try canonical JSONL
    p = find_canonical_jsonl(account, sym)
    if p:
        trades = load_jsonl_trades(p, cutoff_ts=cutoff)
        if trades:
            return trades
        # File exists but all trades older — return all for context
        trades = load_jsonl_trades(p)
        if trades:
            return trades
    # 2. Try decisions fallback
    trades = load_trades_from_decisions(account, sym, days=max(days, 30))
    if trades:
        return trades
    return []


def compute_metrics(trades: List[Dict], days: int) -> Dict:
    pnls = [float(t.get("pnl_pct", 0)) for t in trades]
    n = len(pnls)
    if n == 0:
        return {"pool_sharpe": 0.0, "wr": 0.0, "dd": 0.0, "n_trades": 0, "days": days}
    wins = sum(1 for p in pnls if p > 0)
    wr = 100.0 * wins / n
    pool = _pool_sharpe(pnls)
    eq = 0.0
    peak = 0.0
    worst_dd = 0.0
    for p in pnls:
        eq += p
        if eq > peak:
            peak = eq
        elif peak - eq > worst_dd:
            worst_dd = peak - eq
    return {"pool_sharpe": pool, "wr": wr, "dd": worst_dd, "n_trades": n, "days": days}


def load_npz_ohlc(sym: str, mode: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray],
                                                 Optional[np.ndarray], Optional[np.ndarray],
                                                 Optional[np.ndarray]]:
    """Returns (ts, open, high, low, close) arrays for the base TF, or Nones."""
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists():
        return None, None, None, None, None
    try:
        z = np.load(str(npz_path))
        fields = z.files
        tf = "5m" if mode == "tradier" else "15m"
        ts = z[f"timestamps_{tf}"] if f"timestamps_{tf}" in fields else None
        close = z[f"close_{tf}"] if f"close_{tf}" in fields else None
        open_ = z[f"open_{tf}"] if f"open_{tf}" in fields else None
        high = z[f"high_{tf}"] if f"high_{tf}" in fields else None
        low = z[f"low_{tf}"] if f"low_{tf}" in fields else None
        z.close()
        return ts, open_, high, low, close
    except Exception as e:
        print(f"  [npz] {sym}: {e}")
        return None, None, None, None, None


def _ax_style(ax):
    ax.set_facecolor(BG)
    for sp in ax.spines.values():
        sp.set_color("#30363d")
    ax.tick_params(colors=TICK, labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.4, zorder=0)


def draw_candlesticks(ax, ts_arr, open_arr, high_arr, low_arr, close_arr,
                      cutoff_ts: float, max_bars: int = 2000):
    mask = ts_arr >= cutoff_ts
    idx = np.where(mask)[0]
    if len(idx) > max_bars:
        idx = idx[-max_bars:]
    ts_w = ts_arr[idx]
    o_w = open_arr[idx]
    h_w = high_arr[idx]
    l_w = low_arr[idx]
    c_w = close_arr[idx]
    dt_w = np.array([datetime.fromtimestamp(int(v), tz=timezone.utc) for v in ts_w])
    if len(dt_w) < 2:
        return
    # bar width in datetime units (80% of interval)
    delta = (dt_w[-1] - dt_w[0]).total_seconds() / max(1, len(dt_w) - 1)
    bar_w_sec = delta * 0.7
    from matplotlib.dates import date2num
    bar_w = bar_w_sec / 86400.0  # in matplotlib date units (days)
    x_num = np.array([date2num(d) for d in dt_w])
    bull = c_w >= o_w
    bear = ~bull
    # Wicks
    ax.vlines(x_num[bull], l_w[bull], h_w[bull], color=GREEN, linewidth=0.5, zorder=2)
    ax.vlines(x_num[bear], l_w[bear], h_w[bear], color=RED, linewidth=0.5, zorder=2)
    # Bodies — vectorized via bar
    body_h_bull = np.maximum(c_w[bull] - o_w[bull], 1e-10)
    body_h_bear = np.maximum(o_w[bear] - c_w[bear], 1e-10)
    ax.bar(x_num[bull], body_h_bull, bottom=o_w[bull], width=bar_w,
           color=GREEN, linewidth=0, zorder=3)
    ax.bar(x_num[bear], body_h_bear, bottom=c_w[bear], width=bar_w,
           color=RED, linewidth=0, zorder=3)
    ax.xaxis_date(tz=timezone.utc)


def draw_close_line(ax, ts_arr, close_arr, cutoff_ts: float):
    mask = ts_arr >= cutoff_ts
    dt_p = [datetime.fromtimestamp(int(v), tz=timezone.utc) for v in ts_arr[mask]]
    if dt_p:
        ax.plot(dt_p, close_arr[mask], color=TITLE_C, lw=0.7, zorder=2)


def draw_trade_overlays(ax, trades: List[Dict], cutoff_ts: float, label_threshold: float = 0.5):
    from matplotlib.dates import date2num
    longs_entry: List[Tuple] = []
    shorts_entry: List[Tuple] = []
    exits: List[Tuple] = []
    labeled = 0
    for t in trades:
        ets = int(t.get("entry_ts") or 0)
        xts = int(t.get("exit_ts") or 0)
        if xts < cutoff_ts and ets < cutoff_ts:
            continue
        pnl = float(t.get("pnl_pct", 0))
        side = str(t.get("side", "LONG")).upper()
        ep = float(t.get("entry_price") or 0)
        xp = float(t.get("exit_price") or 0)
        if ep <= 0:
            continue
        entry_dt = datetime.fromtimestamp(max(ets, int(cutoff_ts)), tz=timezone.utc) if ets else None
        exit_dt = datetime.fromtimestamp(xts, tz=timezone.utc) if xts else None
        if entry_dt and exit_dt and ets > 0 and xts > 0:
            x0 = date2num(entry_dt)
            x1 = date2num(exit_dt)
            span_color = "#3fb95028" if pnl > 0 else "#f8514928"
            ax.axhspan(ep * 0.9999, ep * 1.0001, xmin=0, xmax=1, alpha=0, zorder=1)
            ax.fill_betweenx([ep * 0.9998, ep * 1.0002], x0, x1,
                             color=span_color, zorder=1, linewidth=0)
        if entry_dt and ep > 0:
            if side == "LONG":
                longs_entry.append((entry_dt, ep))
            else:
                shorts_entry.append((entry_dt, ep))
        if exit_dt and xp > 0:
            exits.append((exit_dt, xp))
        if abs(pnl) >= label_threshold and exit_dt and xp > 0 and labeled < 40:
            color = GREEN if pnl > 0 else RED
            ax.annotate(f"{pnl:+.2f}%", xy=(exit_dt, xp),
                        xytext=(3, 4 if pnl > 0 else -10),
                        textcoords="offset points",
                        fontsize=5.5, color=color, zorder=6, ha="left")
            labeled += 1
    if longs_entry:
        ax.scatter([v[0] for v in longs_entry], [v[1] for v in longs_entry],
                   marker="^", s=50, color=GREEN, zorder=5, alpha=0.9,
                   label=f"Long ({len(longs_entry)})")
    if shorts_entry:
        ax.scatter([v[0] for v in shorts_entry], [v[1] for v in shorts_entry],
                   marker="v", s=50, color=ORANGE, zorder=5, alpha=0.9,
                   label=f"Short ({len(shorts_entry)})")
    if exits:
        ax.scatter([v[0] for v in exits], [v[1] for v in exits],
                   marker="s", s=25, color="white", zorder=5, alpha=0.8, linewidths=0,
                   label=f"Exit ({len(exits)})")


def generate_chart(sym: str, account: str, trades: List[Dict], days: int, out_dir: Path) -> Optional[Path]:
    if not HAS_MPL:
        return None
    cfg = ACCOUNT_CONFIGS[account]
    mode = cfg["mode"]
    cutoff_ts = time.time() - days * 86400

    trades_w = [t for t in trades if float(t.get("exit_ts") or t.get("entry_ts") or 0) >= cutoff_ts]
    if not trades_w:
        trades_w = list(trades[-500:])
    if not trades_w:
        print(f"  {sym}: no trades to chart")
        return None

    pnls = [float(t.get("pnl_pct", 0)) for t in trades_w]
    cum_pnl: List[float] = []
    eq = 0.0
    for p in pnls:
        eq += p
        cum_pnl.append(eq)

    dt_exit = []
    for t in trades_w:
        xts = int(t.get("exit_ts") or 0)
        if xts > 0:
            dt_exit.append(datetime.fromtimestamp(xts, tz=timezone.utc))
        else:
            dt_exit.append(None)

    n_win = sum(1 for p in pnls if p > 0)
    n_lose = len(pnls) - n_win
    m = compute_metrics(trades_w, days)

    ts_arr, open_arr, high_arr, low_arr, close_arr = load_npz_ohlc(sym, mode)
    has_ohlc = (ts_arr is not None and close_arr is not None
                and open_arr is not None and high_arr is not None and low_arr is not None
                and len(ts_arr) > 0)
    has_close_only = (ts_arr is not None and close_arr is not None
                      and len(ts_arr) > 0 and not has_ohlc)

    fig = plt.figure(figsize=(18, 12), facecolor=BG, dpi=150)
    fig.patch.set_facecolor(BG)
    gs = gridspec.GridSpec(3, 1, height_ratios=[6, 2.5, 1.5], hspace=0.05, figure=fig)

    # ── Panel 0: Price chart (candlesticks or close line) + trade overlays ────
    ax_price = fig.add_subplot(gs[0])
    _ax_style(ax_price)

    if has_ohlc:
        draw_candlesticks(ax_price, ts_arr, open_arr, high_arr, low_arr, close_arr,
                         cutoff_ts=cutoff_ts)
    elif has_close_only:
        draw_close_line(ax_price, ts_arr, close_arr, cutoff_ts=cutoff_ts)

    draw_trade_overlays(ax_price, trades_w, cutoff_ts=cutoff_ts, label_threshold=0.5)

    if has_ohlc or has_close_only:
        ax_price.xaxis.set_visible(False)
    ax_price.set_ylabel("Price", color=TICK, fontsize=8)
    ax_price.legend(loc="upper left", fontsize=7, facecolor="#161b22",
                    edgecolor="#30363d", labelcolor=TITLE_C, framealpha=0.9)

    tf_label = cfg["base_tf"]
    source_note = trades_w[0].get("_source", "canonical") if trades_w else "canonical"
    title = (f"{sym} | pool_sharpe={m['pool_sharpe']:+.4f} | wr={m['wr']:.1f}% | "
             f"dd={m['dd']:.2f}% | {m['n_trades']} trades | last {days}d | "
             f"{cfg['label']} | {tf_label} | src={source_note}")
    ax_price.set_title(title, color=TITLE_C, fontsize=9, pad=5)

    # ── Panel 1: Cumulative PnL ───────────────────────────────────────────────
    ax_cum = fig.add_subplot(gs[1])
    _ax_style(ax_cum)
    valid_cum = [(dt, c) for dt, c in zip(dt_exit, cum_pnl) if dt is not None]
    if valid_cum:
        vdt, vc = zip(*valid_cum)
        ax_cum.plot(list(vdt), list(vc), color=BLUE, lw=1.4, zorder=3)
        ax_cum.fill_between(list(vdt), list(vc), alpha=0.13, color=BLUE)
    ax_cum.axhline(0, color=TICK, lw=0.5, linestyle="--")
    ax_cum.set_ylabel("Cum PnL %", color=TICK, fontsize=8)
    ax_cum.xaxis.set_visible(False)

    # ── Panel 2: Per-trade bars ───────────────────────────────────────────────
    ax_bar = fig.add_subplot(gs[2])
    _ax_style(ax_bar)
    if valid_cum:
        valid_pnl_pairs = [(dt, p) for dt, p in zip(dt_exit, pnls) if dt is not None]
        if valid_pnl_pairs:
            bdt, bp = zip(*valid_pnl_pairs)
            bar_colors = [GREEN if p > 0 else RED for p in bp]
            ax_bar.bar(list(bdt), list(bp), color=bar_colors, width=0.4, zorder=3)
    ax_bar.axhline(0, color=TICK, lw=0.5, linestyle="--")
    ax_bar.set_ylabel("Trade PnL %", color=TICK, fontsize=8)
    ax_bar.legend(
        handles=[Patch(facecolor=GREEN, label=f"Win ({n_win})"),
                 Patch(facecolor=RED, label=f"Loss ({n_lose})")],
        loc="upper right", fontsize=7, facecolor="#161b22",
        edgecolor="#30363d", labelcolor=TITLE_C, framealpha=0.9)
    ax_bar.tick_params(colors=TICK, labelsize=7)

    out_path = out_dir / f"{account}_{sym}_review_{days}d.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  -> {out_path.name} | trades={m['n_trades']} | pool_sharpe={m['pool_sharpe']:+.4f} | wr={m['wr']:.1f}% | dd={m['dd']:.2f}%")
    return out_path


def get_syms_for_account(account: str) -> List[str]:
    cfg = ACCOUNT_CONFIGS[account]
    syms = set()
    # From canonical JSONL dirs
    pattern = cfg.get("canonical_dir_pattern")
    prefix = cfg.get("jsonl_prefix")
    if pattern and prefix:
        dirs = list(CANONICAL_TRADES_DIR.glob(pattern))
        for d in dirs:
            for jp in d.glob(f"{prefix}*.jsonl"):
                sym = jp.stem.replace(prefix, "")
                if sym:
                    syms.add(sym)
    # From candidates dir
    cand_dir = cfg.get("cand_dir")
    cand_prefix = cfg.get("cand_prefix", "")
    if cand_dir and Path(cand_dir).exists():
        for jp in Path(cand_dir).glob(f"{cand_prefix}*_winner.json"):
            stem = jp.stem
            sym = stem.replace(cand_prefix, "").replace("_winner", "")
            # strip side suffix like _LONG_ONLY, _SHORT_ONLY
            for suffix in ("_LONG_ONLY", "_SHORT_ONLY", "_BOTH", "_LONG", "_SHORT"):
                if sym.endswith(suffix):
                    sym = sym[: -len(suffix)]
                    break
            if sym:
                syms.add(sym)
    # From decisions files
    for f in DECISIONS_DIR.glob(f"decisions_{account}_*.jsonl"):
        try:
            with f.open() as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                        pk = rec.get("position_key", "")
                        if ":" in pk:
                            raw = pk.split(":")[-1]
                            sym = raw.rsplit("_", 1)[0] if raw.endswith(("_LONG", "_SHORT")) else raw
                            if sym:
                                syms.add(sym)
                    except Exception:
                        pass
        except Exception:
            pass
    return sorted(syms)


def main() -> int:
    ap = argparse.ArgumentParser(description="Trade review candlestick charts with overlays")
    ap.add_argument("--account", default="", help="account key (flz,trb,trc,ang,fin,men,inf)")
    ap.add_argument("--all-accounts", action="store_true", help="process all accounts")
    ap.add_argument("--sym", default="", help="comma-separated symbols to chart (overrides account discovery)")
    ap.add_argument("--days", type=int, default=60, help="lookback window in days (default 60)")
    ap.add_argument("--out", default="plots/review", help="output directory (default: plots/review/)")
    args = ap.parse_args()

    if not HAS_MPL:
        print("ERROR: matplotlib required. Install with: pip install matplotlib")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build list of (account, sym) pairs to process
    tasks: List[Tuple[str, str]] = []

    if args.sym:
        syms = [s.strip() for s in args.sym.split(",") if s.strip()]
        acct = args.account if args.account else "flz"
        for sym in syms:
            tasks.append((acct, sym))
    elif args.all_accounts:
        for acct in ALL_ACCOUNTS:
            syms = get_syms_for_account(acct)
            for sym in syms:
                tasks.append((acct, sym))
    elif args.account:
        acct = args.account
        if acct not in ACCOUNT_CONFIGS:
            print(f"ERROR: unknown account '{acct}'. Valid: {list(ACCOUNT_CONFIGS.keys())}")
            return 1
        syms = get_syms_for_account(acct)
        for sym in syms:
            tasks.append((acct, sym))
    else:
        print("ERROR: specify --account, --sym, or --all-accounts")
        ap.print_help()
        return 1

    if not tasks:
        print("No symbols found to chart.")
        return 0

    print(f"\ntrade_review_charts — {len(tasks)} symbol(s) | last {args.days}d | out={out_dir}")
    print()

    generated: List[Path] = []
    skipped = 0
    for acct, sym in tasks:
        trades = load_trades_for_sym(acct, sym, days=args.days)
        if not trades:
            print(f"  {acct}/{sym}: no trade data found — skipping")
            skipped += 1
            continue
        try:
            out_path = generate_chart(sym, acct, trades, days=args.days, out_dir=out_dir)
            if out_path:
                generated.append(out_path)
        except Exception as e:
            print(f"  {acct}/{sym}: ERROR {e}")

    print()
    print(f"Generated {len(generated)} charts, skipped {skipped} in {out_dir}/")
    for p in generated:
        print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
