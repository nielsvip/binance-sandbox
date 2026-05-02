#!/usr/bin/env python3
"""per_sym_flz8_profiles — comprehensive per-symbol BTC_DEDICATED optimizer.

Pipeline:
  Phase 1 — Baseline metrics from existing flz8_BEST canonical trade JSONLs.
  Phase 2 — Full parameter sweep per symbol. Winner = max(total_gain_pct) among
             configs with pool_sharpe > 0 AND dd <= PROMOTE_DD_MAX AND trades >= 30.
             total_gain_pct is the primary definer; pool_sharpe > 0 guards against
             negative-expectancy configs sneaking through via lucky PnL.
  Phase 3 — Write winner JSON per symbol to data/hourly_reconfig/_candidates/.
             Picked up automatically by flz_hourly_reconfig.py next hourly cycle.
  Phase 4 — Leaderboard sorted by total_gain_pct.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
CHARTS_OUT_DIR = ROOT / "plots"
SWEEP_DIR = ROOT / "data" / "sweep_results"
CAND_OUT_DIR = ROOT / "data" / "hourly_reconfig" / "_candidates"
BEST_JSONL_DIR = ROOT / "data" / "canonical_trades" / "flz8_BEST_1777522364"
BASE_OVERRIDE_PATH = ROOT / "backtest_v8" / "btc_loop_results" / "override_btc_BEST.json"

SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC",
           "XRPUSDC", "DOGEUSDC", "ZECUSDC", "BTCDOMUSDT"]

PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 5000
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_PER_YR_MIN = 100.0
PROMOTE_WR_MIN = 60.0

START_TS_2024_07_01 = 1719792000


def load_base_override() -> Dict:
    with BASE_OVERRIDE_PATH.open() as f:
        ovr = json.load(f)
    return {k: v for k, v in ovr.items() if not k.startswith("_")}


def jsonl_pnls_and_meta(path: Path) -> Tuple[List[float], int, int, float, float]:
    rs: List[float] = []
    wins = 0
    first = None
    last = 0
    with path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            r = float(rec.get("pnl_pct", 0.0))
            rs.append(r)
            if r > 0:
                wins += 1
            ets = int(rec.get("exit_ts", 0) or 0)
            if first is None or (ets and ets < first):
                first = ets
            if ets > last:
                last = ets
    return rs, len(rs), wins, float(first or 0), float(last or 0)


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
        "gain_sym_yr": total_gain / yrs,
        "trades": n, "n_syms": 1, "years": yrs,
        "total_gain_pct": total_gain, "wr_pct": wr,
        "max_dd_pct": worst, "n_wins": n_wins, "tag": f"per_sym_{sym}",
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


def fmt_canonical(sym: str, m: Dict, years_label: float, v: str) -> str:
    return (
        f"{sym} | pool={m['pool_sharpe']:+.4f} | sym={m['sym_sharpe']:+.4f} | "
        f"wr={m['wr_pct']:.1f}% | avg={m['avg_gain_trade']:+.4f}% | "
        f"gpy={m['gain_per_yr']:+.1f}% | pnl={m['total_gain_pct']:+.1f}% | "
        f"trades={m['trades']:,} | dd={m['max_dd_pct']:.2f}% | {v}"
    )


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


def pick_winner(results: List[Tuple[str, Dict, Dict]]) -> Tuple[str, Dict, Dict, str]:
    """Pick winner by total_gain_pct (primary) among configs with pool_sharpe>0, dd<=cap, trades>=30."""
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["pool_sharpe"] > 0
                 and m["max_dd_pct"] <= PROMOTE_DD_MAX
                 and m["trades"] >= 30]
    if qualified:
        best = max(qualified, key=lambda t: t[2]["total_gain_pct"])
        return best[0], best[1], best[2], "QUALIFIED_BY_PNL"
    # Fallback: any with trades >= 30, max effective_score
    viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= 30] or results
    best = max(viable, key=lambda t: t[2]["effective_score"])
    return best[0], best[1], best[2], "FALLBACK_EFF"


# ---------- Chart generation -------------------------------------------------

def generate_sym_chart(sym: str, trades: List[Dict], m: Dict, overrides: Dict, days: int = 60) -> None:
    """3-panel detailed chart: 15m price+markers | cumPnL | per-trade bars."""
    if not HAS_MPL:
        return
    from datetime import datetime, timezone
    from matplotlib.patches import Patch

    BG = "#0d1117"; GRID = "#21262d"; TICK = "#6e7681"; TITLE = "#c9d1d9"
    cutoff_ts = time.time() - max(1, days) * 86400

    # Filter to window; fall back to most recent 2000 if window is empty
    trades_w = [t for t in trades if t.get("exit_ts", 0) >= cutoff_ts]
    if not trades_w:
        trades_w = list(trades[-2000:])
    if not trades_w:
        print(f"  [chart] {sym}: no trades")
        return

    pnl = [float(t.get("pnl_pct", 0)) for t in trades_w]
    eq = 0.0; cum_pnl: List[float] = []
    for p in pnl:
        eq += p; cum_pnl.append(eq)
    n_win = sum(1 for p in pnl if p > 0)
    n_lose = len(pnl) - n_win

    dt_exit = [datetime.fromtimestamp(int(t.get("exit_ts", 0)), tz=timezone.utc)
               for t in trades_w if t.get("exit_ts", 0) > 0]
    if not dt_exit:
        print(f"  [chart] {sym}: no valid exit timestamps")
        return

    # Load NPZ for 15m price
    npz_path = NPZ_DIR / f"{sym}.npz"
    ts_arr = close_arr = None
    if npz_path.exists():
        z = np.load(str(npz_path))
        npz = {k: z[k] for k in z.files}; z.close()
        ts_arr = npz.get("timestamps_15m"); close_arr = npz.get("close_15m")
        if close_arr is None:
            close_arr = npz.get("close_4h")
            ts_arr = npz.get("timestamps_15m")

    fig = plt.figure(figsize=(18, 12), facecolor=BG, dpi=150)
    fig.patch.set_facecolor(BG)
    has_price = ts_arr is not None and close_arr is not None and len(ts_arr) > 0
    ratios = [3, 1.5, 1] if has_price else [2, 1]
    n_panels = len(ratios)
    gs = gridspec.GridSpec(n_panels, 1, height_ratios=ratios, hspace=0.05, figure=fig)

    def _style(ax):
        ax.set_facecolor(BG)
        for sp in ax.spines.values(): sp.set_color("#30363d")
        ax.tick_params(colors=TICK, labelsize=7)
        ax.grid(axis="y", color=GRID, linewidth=0.4, zorder=0)

    panel = 0

    # ── Panel 0: price + entry/exit markers ──────────────────────────────────
    if has_price:
        ax_p = fig.add_subplot(gs[panel]); _style(ax_p); panel += 1
        mask = ts_arr >= cutoff_ts
        dt_p = [datetime.fromtimestamp(int(v), tz=timezone.utc) for v in ts_arr[mask]]
        if len(dt_p) == int(mask.sum()) and len(dt_p) > 0:
            ax_p.plot(dt_p, close_arr[mask], color="#c9d1d9", lw=0.7, zorder=2)
        # entry markers
        longs  = [(datetime.fromtimestamp(int(t["entry_ts"]), tz=timezone.utc), float(t.get("entry_price", 0)))
                  for t in trades_w if t.get("entry_ts") and t.get("side", "").upper() == "LONG"]
        shorts = [(datetime.fromtimestamp(int(t["entry_ts"]), tz=timezone.utc), float(t.get("entry_price", 0)))
                  for t in trades_w if t.get("entry_ts") and t.get("side", "").upper() == "SHORT"]
        exits  = [(datetime.fromtimestamp(int(t["exit_ts"]), tz=timezone.utc), float(t.get("exit_price", 0)))
                  for t in trades_w if t.get("exit_ts") and t.get("exit_price")]
        if longs:
            ax_p.scatter([v[0] for v in longs], [v[1] for v in longs], marker="^", s=50, color="#3fb950", zorder=5, alpha=0.9, label=f"Long ({len(longs)})")
        if shorts:
            ax_p.scatter([v[0] for v in shorts], [v[1] for v in shorts], marker="v", s=50, color="#f0883e", zorder=5, alpha=0.9, label=f"Short ({len(shorts)})")
        if exits:
            ax_p.scatter([v[0] for v in exits], [v[1] for v in exits], marker="x", s=30, color="#f85149", zorder=5, linewidths=1.3, alpha=0.75, label="Exit")
        ax_p.set_ylabel("Price (15m)", color=TICK, fontsize=8)
        ax_p.legend(loc="upper left", fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")

        # Title on price panel
        # show overrides that differ from baseline (non-BTC_ prefix keys and key BTC_ knobs)
        key_ovr = {k: v for k, v in overrides.items()
                   if k in ("ENTRY_SCORE_THRESHOLD", "BTC_ACCEL_RAMP_MIN_TFS",
                            "BTC_MIN_HOLD_BARS", "BTC_HARD_LOSS_USD_PER_TRADE",
                            "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE", "WA_MIN_GAIN_PCT",
                            "BTC_TECH_EXIT_WT_MIN_TFS") and not k.startswith("_")}
        ovr_str = "  ".join(f"{k.replace('BTC_','')}={v}" for k, v in key_ovr.items())
        ax_p.set_title(
            f"OPT | {sym}  pool={m['pool_sharpe']:+.4f}  wr={m.get('wr_pct',0):.1f}%  "
            f"dd={m.get('max_dd_pct',0):.2f}%  trades={m['trades']:,}  wins={n_win}/{len(pnl)}  (last {days}d)\n"
            f"{ovr_str or '(baseline config)'}",
            color=TITLE, fontsize=9, pad=4)
        ax_p.xaxis.set_visible(False)

    # ── Panel 1: cumulative PnL ───────────────────────────────────────────────
    ax_cum = fig.add_subplot(gs[panel]); _style(ax_cum); panel += 1
    if dt_exit and len(dt_exit) == len(cum_pnl):
        ax_cum.plot(dt_exit, cum_pnl, color="#58a6ff", lw=1.4, zorder=3)
        ax_cum.fill_between(dt_exit, cum_pnl, alpha=0.13, color="#58a6ff")
    ax_cum.axhline(0, color="#6e7681", lw=0.5, linestyle="--")
    ax_cum.set_ylabel("Cum PnL %", color=TICK, fontsize=8)
    if not has_price:
        ax_cum.set_title(
            f"OPT | {sym}  pool={m['pool_sharpe']:+.4f}  wr={m.get('wr_pct',0):.1f}%  "
            f"dd={m.get('max_dd_pct',0):.2f}%  trades={m['trades']:,}  (last {days}d)",
            color=TITLE, fontsize=9, pad=4)
    ax_cum.xaxis.set_visible(panel < n_panels)

    # ── Panel 2: per-trade bars ───────────────────────────────────────────────
    ax_bar = fig.add_subplot(gs[panel], sharex=ax_cum); _style(ax_bar)
    if dt_exit and len(dt_exit) == len(pnl):
        colors = ["#3fb950" if p > 0 else "#f85149" for p in pnl]
        ax_bar.bar(dt_exit, pnl, color=colors, width=0.4, zorder=3)
    ax_bar.axhline(0, color="#6e7681", lw=0.5, linestyle="--")
    ax_bar.set_ylabel("Trade PnL %", color=TICK, fontsize=8)
    ax_bar.legend(handles=[Patch(facecolor="#3fb950", label=f"Win ({n_win})"),
                            Patch(facecolor="#f85149", label=f"Loss ({n_lose})")],
                  loc="upper right", fontsize=7, facecolor="#161b22",
                  edgecolor="#30363d", labelcolor="#c9d1d9")

    CHARTS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CHARTS_OUT_DIR / f"OPT_{sym}.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  [chart] {out_path.name}")


def load_sym_trades(sym: str) -> List[Dict]:
    jp = BEST_JSONL_DIR / f"flz8_BEST__{sym}.jsonl"
    if not jp.exists():
        return []
    trades: List[Dict] = []
    with jp.open() as f:
        for line in f:
            try: trades.append(json.loads(line))
            except Exception: pass
    return trades


# ---------- Phase 1 ----------------------------------------------------------

def phase1_baseline() -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    print("=" * 88)
    print("PHASE 1 — per-sym BEST baseline (from existing flz8_BEST_1777522364 JSONLs)")
    print("=" * 88)
    for sym in SYMBOLS:
        jp = BEST_JSONL_DIR / f"flz8_BEST__{sym}.jsonl"
        if not jp.exists():
            print(f"  {sym}: MISSING JSONL {jp}")
            continue
        rets, n, wins, first_ts, last_ts = jsonl_pnls_and_meta(jp)
        years = (last_ts - first_ts) / 86400.0 / 365.25 if (first_ts and last_ts) else 6.23
        m = per_sym_metrics(rets, years, sym)
        m["override_path"] = str(BASE_OVERRIDE_PATH)
        v = verdict(m)
        m["verdict"] = v
        out[sym] = m
        print(fmt_canonical(sym, m, years, v))
    return out


# ---------- Phase 2 — Comprehensive mutation grid ----------------------------

def mutation_grid(base: Dict) -> List[Tuple[str, Dict]]:
    """Comprehensive BTC_* sweep. Every major knob from config.py BTC_DEDICATED section.
    Systematic single-param variations from baseline + key combos.
    Winner selection uses total_gain_pct as primary criterion.
    """
    grid: List[Tuple[str, Dict]] = []

    def add(tag: str, deltas: Dict) -> None:
        o = dict(base)
        o.update(deltas)
        grid.append((tag, o))

    # ── Baseline ──────────────────────────────────────────────────────────────
    grid.append(("BEST", dict(base)))

    # ── 1. Accel ramp strictness (PRIMARY entry gate) ─────────────────────────
    for n in (2, 3, 4, 5):
        add(f"accel_tfs_{n}", {"BTC_ACCEL_RAMP_MIN_TFS": n})
    add("accel_off",  {"BTC_ACCEL_RAMP_ENABLED": False})
    add("accel_nopos", {"BTC_ACCEL_RAMP_REQUIRE_POSITIVE": False})

    # ── 2. Entry composition: RZ gate / accel requirement ─────────────────────
    add("norz_noaccel",  {"BTC_ENTRY_PRIMARY_REQUIRE_RZ": False,
                          "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP": False})
    add("norz_accel",    {"BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})
    add("rz_noaccel",    {"BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP": False})
    add("div_only_on",   {"BTC_ENTRY_DIV_ONLY_ENABLED": True})
    add("div_only_min2", {"BTC_ENTRY_DIV_ONLY_ENABLED": True, "BTC_ENTRY_DIV_ONLY_MIN_INDS": 2})

    # ── 3. Red zone proximity & boost mode ────────────────────────────────────
    for prox in (0.5, 1.0, 1.5, 2.0, 3.0):
        add(f"rz_prox_{prox:.1f}", {"BTC_RZ_PROXIMITY_PCT": prox})
    add("rz_gate_mode",  {"BTC_RZ_AS_BOOST_ENABLED": False})
    for soften in (0, 1, 2):
        add(f"rz_soften_{soften}", {"BTC_RZ_SOFTEN_ACCEL_BY": soften})

    # ── 4. Divergence ─────────────────────────────────────────────────────────
    add("div_off",       {"BTC_DIVERGENCE_ENABLED": False})
    add("div_noblk",     {"BTC_DIVERGENCE_BLOCK_AGAINST": False})
    add("div_noexit",    {"BTC_DIVERGENCE_EXIT_AGAINST": False})
    add("div_noblk_noexit", {"BTC_DIVERGENCE_BLOCK_AGAINST": False,
                              "BTC_DIVERGENCE_EXIT_AGAINST": False})
    for tf in ("1h", "4h", "D"):
        add(f"div_min_{tf}", {"BTC_DIVERGENCE_MIN_TF": tf})
    for lb in (2, 5, 10):
        add(f"div_lb_{lb}", {"BTC_DIVERGENCE_LB_4H": lb, "BTC_DIVERGENCE_LB_D": lb})

    # ── 5. Hold / cooldown ────────────────────────────────────────────────────
    for mh, bk in ((1, 1), (3, 1), (5, 3), (8, 5), (12, 8)):
        add(f"mh{mh}_bk{bk}", {"BTC_MIN_HOLD_BARS": mh, "BTC_BREAKOUT_MIN_HOLD_BARS": bk})
    for cd, bcd in ((1, 1), (3, 1), (3, 3), (5, 3), (8, 5)):
        add(f"cd{cd}_bcd{bcd}", {"BTC_COOLDOWN_BARS": cd, "BTC_BREAKOUT_COOLDOWN_BARS": bcd})

    # ── 6. Technical exit strictness ──────────────────────────────────────────
    for t in (1, 2, 3, 4):
        add(f"texit_{t}", {"BTC_TECH_EXIT_WT_MIN_TFS": t})
    add("texit_any_pnl_off", {"BTC_TECH_EXIT_AT_ANY_PNL": False})

    # ── 7. Hard loss budgets ───────────────────────────────────────────────────
    base_hl  = float(base.get("BTC_HARD_LOSS_USD_PER_TRADE", 10.0))
    base_bhl = float(base.get("BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE", 5.0))
    for f in (0.5, 0.7, 0.85, 1.0, 1.15, 1.3, 1.5):
        add(f"hl_x{f:.2f}", {
            "BTC_HARD_LOSS_USD_PER_TRADE": round(base_hl * f, 3),
            "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE": round(base_bhl * f, 3),
        })

    # ── 8. Breakout entry mode ─────────────────────────────────────────────────
    add("bk_off",        {"BTC_BREAKOUT_ENTRY_ENABLED": False})
    for ac in (1, 2, 3):
        add(f"bk_ac{ac}", {"BTC_BREAKOUT_ACCEL_MIN_TFS": ac})
    for ht in (1, 2, 3):
        add(f"bk_htf{ht}", {"BTC_BREAKOUT_HTF_MIN_ALIGNED": ht})
    add("bk_nohtf",      {"BTC_BREAKOUT_REQUIRE_HTF_ALIGNED": False})
    add("bk_nodiv",      {"BTC_BREAKOUT_BLOCK_OPPOSING_DIV": False})

    # ── 9. Reentry / follow-through / reverse ─────────────────────────────────
    add("guaren_off",    {"BTC_GUARANTEED_REENTRY_ENABLED": False})
    add("guaren_norz",   {"BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE": False})
    add("guaren_off_norz", {"BTC_GUARANTEED_REENTRY_ENABLED": False,
                             "BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE": False})
    for gap in (1, 3, 5, 10):
        add(f"guaren_gap{gap}", {"BTC_GUARANTEED_REENTRY_MIN_GAP_BARS": gap})
    add("rev_off",       {"BTC_REVERSE_ON_EXIT_ENABLED": False})
    add("rev_nohtf",     {"BTC_REVERSE_REQUIRE_HTF_ALIGNED": False})
    add("ft_off",        {"BTC_FOLLOW_THROUGH_REENTRY_ENABLED": False})
    for move in (0.1, 0.3, 0.5, 0.8):
        add(f"ft_move_{move:.1f}", {"BTC_FOLLOW_THROUGH_MIN_MOVE_PCT": move})

    # ── 10. Same-sym hedge ────────────────────────────────────────────────────
    add("hedge_on",      {"BTC_HEDGE_SAMESYM_ENABLED": True})
    add("hedge_on_htf2", {"BTC_HEDGE_SAMESYM_ENABLED": True,
                          "BTC_HEDGE_SAMESYM_REQUIRE_HTF_TFS_MIN": 2})

    # ── 11. MIN_GAIN / augment threshold (WA = winner-augment; PYRAMID = pyramid) ──
    for mg_pct in (1.0, 2.0, 3.0, 4.0):
        add(f"mingain_{mg_pct:.0f}pct", {
            "WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
        })

    # ── 12. Key 2-param combos (accel × RZ, hold × exit, breakout × reentry) ──
    add("ac3_norz",      {"BTC_ACCEL_RAMP_MIN_TFS": 3, "BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})
    add("ac2_norz",      {"BTC_ACCEL_RAMP_MIN_TFS": 2, "BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})
    add("mh5_texit2",    {"BTC_MIN_HOLD_BARS": 5, "BTC_TECH_EXIT_WT_MIN_TFS": 2})
    add("mh8_texit1",    {"BTC_MIN_HOLD_BARS": 8, "BTC_TECH_EXIT_WT_MIN_TFS": 1})
    add("bk_off_texit2", {"BTC_BREAKOUT_ENTRY_ENABLED": False, "BTC_TECH_EXIT_WT_MIN_TFS": 2})
    add("guaren_norz_mg2", {"BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE": False,
                             "WA_MIN_GAIN_PCT": 2.0, "PYRAMID_MIN_GAIN_PCT": 2.0})
    add("tight_hl_mh5",  {"BTC_HARD_LOSS_USD_PER_TRADE": round(base_hl * 0.7, 3),
                           "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE": round(base_bhl * 0.7, 3),
                           "BTC_MIN_HOLD_BARS": 5})
    add("loose_hl_mh3",  {"BTC_HARD_LOSS_USD_PER_TRADE": round(base_hl * 1.3, 3),
                           "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE": round(base_bhl * 1.3, 3),
                           "BTC_MIN_HOLD_BARS": 3})
    add("mg2_es15",      {"WA_MIN_GAIN_PCT": 2.0, "PYRAMID_MIN_GAIN_PCT": 2.0,
                          "ENTRY_SCORE_THRESHOLD": 15.0})
    add("mg1_mh5_ac3",   {"WA_MIN_GAIN_PCT": 1.0, "PYRAMID_MIN_GAIN_PCT": 1.0,
                          "BTC_MIN_HOLD_BARS": 5, "BTC_ACCEL_RAMP_MIN_TFS": 3})
    add("mg3_texit2_norz", {"WA_MIN_GAIN_PCT": 3.0, "PYRAMID_MIN_GAIN_PCT": 3.0,
                             "BTC_TECH_EXIT_WT_MIN_TFS": 2,
                             "BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})

    return grid


# ---------- Engine runner ─────────────────────────────────────────────────────

def run_engine_for_sym(sym: str, overrides: Dict, run_dir: Path, run_id: str,
                       npz: dict, start_ts: int = START_TS_2024_07_01) -> Tuple[List[float], List[Dict]]:
    """Returns (rets, trades). trades has full JSONL records (side, entry_price, etc.)."""
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if k.startswith("_"):
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass
    cfg.BTC_DEDICATED_SYMBOLS = (sym,)
    cfg.BTC_DEDICATED_ENABLED = True
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit:
        pass
    except Exception as e:
        print(f"    [engine] EXC {e}")
        return [], []
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    rets: List[float] = []
    trades: List[Dict] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if int(rec.get("exit_ts", 0) or 0) < start_ts:
                        continue
                    rets.append(float(rec.get("pnl_pct", 0.0)))
                    trades.append(rec)
                except Exception:
                    pass
    return rets, trades


# ---------- Phase 2 driver ───────────────────────────────────────────────────

def phase2_mutate_sym(sym: str, base: Dict, run_root: Path,
                      sweep_csv: Path, full_years: float) -> Tuple[Dict, Dict, List[Dict]]:
    """Returns (delta_overrides, metrics, winner_trades).
    delta_overrides contains ONLY params that changed from base — never untested baseline values."""
    print()
    print("─" * 80)
    print(f"PHASE 2 — {sym}")
    print("─" * 80)
    grid = mutation_grid(base)
    print(f"  grid size: {len(grid)} variants")

    npz_path = NPZ_DIR / f"{sym}.npz"
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()

    run_dir = run_root / sym
    run_dir.mkdir(parents=True, exist_ok=True)

    results: List[Tuple[str, Dict, Dict, str]] = []  # (tag, full_ovr, m, run_id)
    t0 = time.time()
    best_pnl = -1e9
    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}__{tag}__{i:03d}"
        rets, _trades = run_engine_for_sym(sym, ovr, run_dir, run_id, npz)
        m = per_sym_metrics(rets, full_years, sym)
        m["tag"] = f"mut_{sym}_{tag}"
        v = verdict(m)
        m["verdict"] = v
        m["effective_score"] = effective_score(m)
        results.append((tag, ovr, m, run_id))
        try:
            mg.write_sharpe_row(sweep_csv, m, mode="crypto", append=True)
        except Exception as e:
            print(f"    [csv] REFUSED {tag}: {e}")
        marker = ""
        if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX and m["trades"] >= 30:
            if m["total_gain_pct"] > best_pnl:
                best_pnl = m["total_gain_pct"]
                marker = " ← NEW BEST PNL"
        if i <= 5 or i % 20 == 0 or marker:
            print(f"    [{i:3d}/{len(grid)}] {tag:30s} pool={m['pool_sharpe']:+.4f} "
                  f"pnl={m['total_gain_pct']:+8.1f}% tr={m['trades']:>6d} "
                  f"dd={m['max_dd_pct']:.2f}% {v}{marker}")

    elapsed = time.time() - t0
    print(f"  {sym} done in {elapsed:.1f}s ({elapsed/len(grid):.1f}s/variant)")

    tag, full_ovr, m, bucket = pick_winner([(t, o, mm) for t, o, mm, _ in results])
    # Find winner's run_id to reload trades
    winner_run_id = next((rid for t, o, mm, rid in results if t == tag and mm is m), None)
    winner_trades: List[Dict] = []
    if winner_run_id:
        jp = run_dir / f"{winner_run_id}__{sym}.jsonl"
        if jp.exists():
            with jp.open() as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if int(rec.get("exit_ts", 0) or 0) >= START_TS_2024_07_01:
                            winner_trades.append(rec)
                    except Exception:
                        pass

    # Compute delta: only params that actually changed from base
    delta = {k: v for k, v in full_ovr.items()
             if not k.startswith("_") and str(base.get(k)) != str(v)}

    print(f"  WINNER ({bucket}): {tag} | pool={m['pool_sharpe']:+.4f} | "
          f"pnl={m['total_gain_pct']:+.1f}% | trades={m['trades']:,} | "
          f"dd={m['max_dd_pct']:.2f}% | wr={m['wr_pct']:.1f}% | gpy={m['gain_per_yr']:+.1f}%")
    print(f"  Delta (actually tested params): {delta}")
    return delta, m, winner_trades


# ---------- Phase 3 ──────────────────────────────────────────────────────────

_ACTIVE_CFG_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
_BANNED_PARAMS = {"D_TREND_REQUIRED", "HTF_MIN_ALIGNED", "MIN_HOLD_BARS", "_meta"}


def _per_side_metrics(trades: List[Dict], years: float, sym: str, side: str) -> Dict:
    """Compute metrics for one side (LONG or SHORT) from the trade list."""
    side_trades = [t for t in trades if t.get("side", "").upper() == side.upper()]
    if not side_trades:
        return {}
    rets = [float(t.get("pnl_pct", 0)) for t in side_trades]
    return per_sym_metrics(rets, years, f"{sym}_{side}")


def promote_winners_to_active_config(winners: Dict[str, tuple]) -> None:
    """Write per-symbol BTC_DEDICATED winners to per_sym_active_config.json.

    Writes ONLY the delta (params that actually changed from baseline for this symbol).
    Writes separate LONG/SHORT entries with per-side metrics.
    Quality gate: pool_sharpe > 0, trades >= 30.
    """
    _ACTIVE_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _ACTIVE_CFG_PATH.open() as f:
            existing: Dict = json.load(f)
    except Exception:
        existing = {}
    now_tag = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    updated = 0
    for sym, winner_tuple in winners.items():
        if len(winner_tuple) == 3:
            delta_ovr, m, trades = winner_tuple
        else:
            delta_ovr, m = winner_tuple
            trades = []
        ps = float(m.get("pool_sharpe", 0))
        tr = int(m.get("trades", 0))
        if ps <= 0 or tr < 30:
            print(f"  [active_config] SKIP {sym}: pool_sharpe={ps:+.4f} trades={tr} — quality gate")
            continue
        # Filter to safe params (no banned structural params)
        clean_delta = {k: v for k, v in delta_ovr.items() if k not in _BANNED_PARAMS}
        years = float(m.get("years", 2.0))
        for side in ("LONG", "SHORT"):
            side_m = _per_side_metrics(trades, years, sym, side) if trades else {}
            side_ps = float(side_m.get("pool_sharpe", ps))
            side_tr = int(side_m.get("trades", tr // 2))
            entry = {
                "winning_tag": f"flz8_{sym}_{now_tag}",
                "wsharpe": side_ps,
                "trades": side_tr,
                "total_trades_combined": tr,
                "sample_tag": "FLZ8",
                "side": side,
                "overrides": clean_delta,
                "_delta_params": len(clean_delta),
            }
            existing[f"{sym}_{side}"] = entry
        updated += 1
        print(f"  [active_config] WRITE {sym}: pool_combined={ps:+.4f} trades={tr:,} "
              f"delta_params={len(clean_delta)} {list(clean_delta.keys())[:5]}")
    with _ACTIVE_CFG_PATH.open("w") as f:
        json.dump(existing, f, indent=2, default=str)
    print(f"  [active_config] {updated}/{len(winners)} symbols written → {_ACTIVE_CFG_PATH}")


def write_winner_json(sym: str, overrides: Dict, m: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CAND_OUT_DIR / f"extra_btc_{sym}_winner.json"
    payload = dict(overrides)
    payload["_meta"] = (
        f"per_sym winner {sym} | "
        f"pool={m['pool_sharpe']:+.4f} trades={m['trades']} "
        f"dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
        f"pnl={m['total_gain_pct']:+.1f}% gpy={m['gain_per_yr']:+.1f}%/yr "
        f"verdict={m.get('verdict')} eff={m.get('effective_score', 0):+.3f}"
    )
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


# ---------- Driver ───────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="1234")
    ap.add_argument("--syms", default="")
    ap.add_argument("--mutate-all", action="store_true")
    args = ap.parse_args()

    syms = [s for s in (args.syms.split(",") if args.syms else SYMBOLS) if s]
    base = load_base_override()
    ts = int(time.time())
    sweep_csv = SWEEP_DIR / f"canonical_per_sym_flz_{ts}.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / f"per_sym_flz_{ts}" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)

    phase1: Dict[str, Dict] = {}
    if "1" in args.phase:
        phase1 = phase1_baseline()
        for sym, m in phase1.items():
            row = dict(m); row["tag"] = f"phase1_{sym}"
            try:
                mg.write_sharpe_row(sweep_csv, row, mode="crypto", append=True)
            except Exception as e:
                print(f"  [phase1 csv] REFUSED {sym}: {e}")

    # winners: {sym: (delta_ovr, m, trades)}
    winners: Dict[str, Tuple] = {}
    if "2" in args.phase:
        for sym in syms:
            p1 = phase1.get(sym, {})
            full_years = float(p1.get("years", 6.23))
            if p1.get("verdict") == "PROMOTE" and not args.mutate_all:
                # Phase 1 pass — delta is empty (no params changed), reload trades from JSONL
                phase1_trades = load_sym_trades(sym)
                winners[sym] = ({}, p1, phase1_trades)
                print(f"\n  {sym}: Phase 1 PROMOTE — using BEST baseline (--mutate-all to sweep)")
                continue
            try:
                delta, m, trades = phase2_mutate_sym(sym, base, run_root, sweep_csv, full_years)
                winners[sym] = (delta, m, trades)
            except Exception as e:
                print(f"  PHASE2 EXC {sym}: {e}")
            gc.collect()

    paths_written: List[Path] = []
    if "3" in args.phase:
        print()
        print("=" * 88)
        print("PHASE 3 — write winner JSONs → _candidates/ + per_sym_active_config.json")
        print("=" * 88)
        for sym in syms:
            if sym not in winners:
                if sym in phase1:
                    phase1_trades = load_sym_trades(sym)
                    winners[sym] = ({}, phase1[sym], phase1_trades)
                else:
                    print(f"  {sym}: no data — skipping")
                    continue
            delta, m, trades = winners[sym]
            p = write_winner_json(sym, delta, m)
            paths_written.append(p)
            print(f"  {p.name}")
        promote_winners_to_active_config(winners)
        print()
        print("=" * 88)
        print("PHASE 3b — generate OPT charts")
        print("=" * 88)
        for sym in syms:
            if sym not in winners:
                continue
            delta, m, trades = winners[sym]
            chart_trades = trades if trades else load_sym_trades(sym)
            generate_sym_chart(sym, chart_trades, m, delta)

    if "4" in args.phase:
        print()
        print("=" * 88)
        print("PHASE 4 — leaderboard (sorted by total_gain_pct — primary winner criterion)")
        print("=" * 88)
        table: List[Tuple[str, str, Dict]] = []
        for sym in syms:
            if sym in winners:
                _, m, _ = winners[sym]
            elif sym in phase1:
                m = phase1[sym]
            else:
                continue
            v = m.get("verdict", verdict(m))
            table.append((sym, v, m))
        table.sort(key=lambda t: t[2].get("total_gain_pct", 0), reverse=True)
        for i, (sym, v, m) in enumerate(table, 1):
            eff = effective_score(m)
            print(f"  {i}. {sym:12s} pnl={m.get('total_gain_pct',0):+9.1f}% "
                  f"pool={m['pool_sharpe']:+.4f} gpy={m['gain_per_yr']:+.1f}%/yr "
                  f"tr={m['trades']:>7,} dd={m['max_dd_pct']:5.2f}% "
                  f"wr={m['wr_pct']:.1f}% eff={eff:+.3f} [{v}]")

        rejects = [(sym, m) for sym, v, m in table if m["max_dd_pct"] > PROMOTE_DD_MAX]
        if rejects:
            print()
            print("REJECT_DD (dd > 10%) — consider removing from BTC_DEDICATED_SYMBOLS:")
            for sym, m in rejects:
                print(f"  {sym}: dd={m['max_dd_pct']:.2f}%")
        else:
            print("\nAll symbols within DD cap.")

    print()
    print(f"CSV: {sweep_csv}")
    print("Candidates written:")
    for p in paths_written:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
