#!/usr/bin/env python3
"""per_sym_crypto_profiles — comprehensive per-symbol crypto sweeper for ang/inf/fin/men.

Reads tradeable_keys.json to get per-account symbol lists.
Runs full mutation grid (standard crypto params — NO BTC_DEDICATED_*).
Writes winner config per symbol to per_sym_active_config.json.
Generates OHLC candlestick chart per symbol.

Usage:
  python3 per_sym_crypto_profiles.py --account ang
  python3 per_sym_crypto_profiles.py --account inf --mutate-all
  python3 per_sym_crypto_profiles.py --account all --sym AAVEUSDC,BTCUSDC
  python3 per_sym_crypto_profiles.py --account ang --phase 12   (skip chart)
"""
from __future__ import annotations
import argparse, gc, json, math, os, sys, time
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
    from matplotlib.patches import Patch, FancyArrow
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

NPZ_DIR       = ROOT / "backtest_v8" / "indicators"
SWEEP_DIR     = ROOT / "data" / "sweep_results"
ACTIVE_CFG    = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
CHARTS_DIR    = ROOT / "plots"
TK_FILE       = ROOT / "tradeable_keys.json"

PROMOTE_POOL_MIN   = 0.0
PROMOTE_TRADES_MIN = 30
PROMOTE_DD_MAX     = 15.0
PROMOTE_WR_MIN     = 55.0

SUPPORTED_ACCOUNTS = ["ang", "inf", "fin", "men"]


# ── symbol loading ─────────────────────────────────────────────────────────

def load_symbols_for_account(account: str) -> List[str]:
    """Return unique symbols from tradeable_keys.json for this account."""
    if not TK_FILE.exists():
        print(f"  WARNING: {TK_FILE} not found — no symbols")
        return []
    keys = json.loads(TK_FILE.read_text())
    prefix = f"{account}:"
    syms = sorted(set(
        k[len(prefix):].rsplit("_", 1)[0]
        for k in keys if k.startswith(prefix)
    ))
    return syms


# ── metrics ───────────────────────────────────────────────────────────────

def per_sym_metrics(rets: List[float], years: float, sym: str) -> Dict:
    n = len(rets)
    if n == 0:
        return {"pool_sharpe": 0.0, "sym_sharpe": 0.0, "avg_gain_trade": 0.0,
                "gain_per_yr": 0.0, "gain_sym_yr": 0.0, "trades": 0, "n_syms": 1,
                "years": max(0.01, years), "total_gain_pct": 0.0, "wr_pct": 0.0,
                "max_dd_pct": 0.0, "n_wins": 0, "tag": f"per_sym_{sym}"}
    n_wins = sum(1 for r in rets if r > 0)
    total = float(sum(rets))
    yrs = max(0.01, years)
    pool = mg.pool_sharpe(rets)
    cap = mg.PER_SYM_SHARPE_CAP
    sym_s = max(-cap, min(cap, pool))
    eq = peak = worst = 0.0
    for r in rets:
        eq += r
        if eq > peak: peak = eq
        elif peak - eq > worst: worst = peak - eq
    return {"pool_sharpe": pool, "sym_sharpe": sym_s,
            "avg_gain_trade": total / n, "gain_per_yr": total / yrs,
            "gain_sym_yr": total / yrs, "trades": n, "n_syms": 1, "years": yrs,
            "total_gain_pct": total, "wr_pct": 100.0 * n_wins / n,
            "max_dd_pct": worst, "n_wins": n_wins, "tag": f"per_sym_{sym}"}


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


def verdict(m: Dict) -> str:
    if m["max_dd_pct"] > PROMOTE_DD_MAX:    return "REJECT_DD"
    if m["wr_pct"] < PROMOTE_WR_MIN:         return "REJECT_WR"
    if m["trades"] < PROMOTE_TRADES_MIN:     return "REJECT_TRADES"
    if m["pool_sharpe"] < PROMOTE_POOL_MIN:  return "DIAGNOSTIC"
    return "PROMOTE"


# ── mutation grid ──────────────────────────────────────────────────────────

def mutation_grid() -> List[Tuple[str, Dict]]:
    """Comprehensive crypto param sweep — standard (non-BTC-DEDICATED) symbols."""
    grid: List[Tuple[str, Dict]] = [("baseline", {})]

    def add(tag: str, d: Dict) -> None:
        grid.append((tag, d))

    # Entry score threshold — primary selectivity knob
    for es in (10.0, 12.0, 15.0, 18.0, 20.0, 22.0, 24.0, 28.0):
        add(f"es_{es:.0f}", {"ENTRY_SCORE_THRESHOLD": es})

    # Augment / pyramid gain gate
    for mg_pct in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
        add(f"mingain_{mg_pct:.1f}", {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct})

    # HTF alignment strictness
    for htf in (1, 2, 3):
        add(f"htf_{htf}", {"HTF_MIN_ALIGNED": htf})

    # Daily trend requirement
    add("dtrend_on",  {"D_TREND_REQUIRED": True})
    add("dtrend_off", {"D_TREND_REQUIRED": False})

    # WT exit signal strictness
    for wt in (1, 2, 3, 4):
        add(f"wt_exit_{wt}", {"WT_EXIT_MIN_TFS": wt})

    # Min hold bars
    for mh in (1, 3, 5, 10, 20):
        add(f"mh_{mh}", {"MIN_HOLD_BARS": mh})

    # Cooldown
    for cd in (1, 3, 6, 12):
        add(f"cd_{cd}", {"COOLDOWN_BARS": cd})

    # SATOSHIT toggle (if wired in engine)
    add("satoshit_on",  {"SATOSHIT_ENTRY_ENABLED": True})
    add("satoshit_off", {"SATOSHIT_ENTRY_ENABLED": False})

    # STDEV breakout / bounce
    add("stdev_break_on",  {"STDEV_BREAKOUT_ENABLED": True,  "STDEV_BOUNCE_ENABLED": False})
    add("stdev_bounce_on", {"STDEV_BREAKOUT_ENABLED": False, "STDEV_BOUNCE_ENABLED": True})
    add("stdev_both",      {"STDEV_BREAKOUT_ENABLED": True,  "STDEV_BOUNCE_ENABLED": True})
    add("stdev_off",       {"STDEV_BREAKOUT_ENABLED": False, "STDEV_BOUNCE_ENABLED": False})

    # Key combos
    for es, mg_pct in ((15.0, 1.0), (18.0, 2.0), (20.0, 2.0), (24.0, 3.0),
                       (18.0, 1.0), (15.0, 2.0), (12.0, 0.5), (24.0, 1.5)):
        add(f"es{es:.0f}_mg{mg_pct:.1f}",
            {"ENTRY_SCORE_THRESHOLD": es, "WA_MIN_GAIN_PCT": mg_pct,
             "PYRAMID_MIN_GAIN_PCT": mg_pct})

    for es, htf in ((18.0, 2), (20.0, 2), (24.0, 3), (15.0, 1), (20.0, 3)):
        add(f"es{es:.0f}_htf{htf}", {"ENTRY_SCORE_THRESHOLD": es, "HTF_MIN_ALIGNED": htf})

    for es, mh in ((15.0, 5), (18.0, 5), (20.0, 10), (24.0, 3)):
        add(f"es{es:.0f}_mh{mh}", {"ENTRY_SCORE_THRESHOLD": es, "MIN_HOLD_BARS": mh})

    for es, wt in ((15.0, 2), (18.0, 2), (20.0, 3), (24.0, 2)):
        add(f"es{es:.0f}_wt{wt}",
            {"ENTRY_SCORE_THRESHOLD": es, "WT_EXIT_MIN_TFS": wt})

    # Triple combos
    for es, mg_pct, htf in ((18.0, 1.5, 2), (20.0, 2.0, 2), (24.0, 2.0, 3),
                             (15.0, 1.0, 1), (18.0, 2.0, 1)):
        add(f"es{es:.0f}_mg{mg_pct:.1f}_htf{htf}",
            {"ENTRY_SCORE_THRESHOLD": es, "WA_MIN_GAIN_PCT": mg_pct,
             "PYRAMID_MIN_GAIN_PCT": mg_pct, "HTF_MIN_ALIGNED": htf})

    return grid


# ── engine runner ──────────────────────────────────────────────────────────

def run_engine(sym: str, overrides: Dict, run_dir: Path, run_id: str,
               npz: dict, start_ts: int = 0) -> Tuple[List[float], List[Dict]]:
    """Returns (rets, trades). trades has full JSONL records."""
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if k.startswith("_"): continue
        try: setattr(cfg, k, v)
        except Exception: pass
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit:
        pass
    except Exception as e:
        print(f"    [engine] EXC {sym}: {e}")
        return [], []
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    rets: List[float] = []; trades: List[Dict] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if start_ts and int(rec.get("exit_ts", 0) or 0) < start_ts:
                        continue
                    rets.append(float(rec.get("pnl_pct", 0.0)))
                    trades.append(rec)
                except Exception:
                    pass
    return rets, trades


# ── chart generation ───────────────────────────────────────────────────────

def generate_ohlc_chart(sym: str, trades: List[Dict], m: Dict, account: str, days: int = 60) -> None:
    if not HAS_MPL or not trades:
        return
    from datetime import datetime, timezone

    BG = "#0d1117"; GRID = "#21262d"; TICK = "#6e7681"; PRICE = "#c9d1d9"
    cutoff_ts = time.time() - days * 86400
    tw = [t for t in trades if t.get("exit_ts", 0) >= cutoff_ts]
    if not tw: tw = trades[-500:]
    if not tw: return

    pnl = [float(t.get("pnl_pct", 0)) for t in tw]
    eq = 0.0; cum: List[float] = []
    for p in pnl: eq += p; cum.append(eq)
    n_win = sum(1 for p in pnl if p > 0)
    dt_exit = [datetime.fromtimestamp(int(t["exit_ts"]), tz=timezone.utc) for t in tw if t.get("exit_ts")]
    if not dt_exit: return

    # Load OHLC from NPZ
    npz_path = NPZ_DIR / f"{sym}.npz"
    ts_arr = open_arr = high_arr = low_arr = close_arr = None
    if npz_path.exists():
        z = np.load(str(npz_path))
        npz = {k: z[k] for k in z.files}; z.close()
        ts_arr   = npz.get("timestamps_15m")
        close_arr= npz.get("close_15m")
        open_arr = npz.get("open_15m")
        high_arr = npz.get("high_15m")
        low_arr  = npz.get("low_15m")

    has_ohlc = (ts_arr is not None and close_arr is not None and len(ts_arr) > 0)
    has_full = has_ohlc and open_arr is not None and high_arr is not None and low_arr is not None

    ratios = ([3, 1.5, 1] if has_ohlc else [2, 1])
    fig = plt.figure(figsize=(18, 12), facecolor=BG, dpi=150)
    fig.patch.set_facecolor(BG)
    gs = gridspec.GridSpec(len(ratios), 1, height_ratios=ratios, hspace=0.05, figure=fig)

    def _style(ax):
        ax.set_facecolor(BG)
        for sp in ax.spines.values(): sp.set_color("#30363d")
        ax.tick_params(colors=TICK, labelsize=7)
        ax.grid(axis="y", color=GRID, lw=0.4, zorder=0)

    panel = 0
    ps = float(m.get("pool_sharpe", 0)); wr = float(m.get("wr_pct", 0))
    dd = float(m.get("max_dd_pct", 0)); ntrades = int(m.get("trades", len(pnl)))

    if has_ohlc:
        ax_p = fig.add_subplot(gs[panel]); _style(ax_p); panel += 1
        mask = ts_arr >= cutoff_ts
        n = min(int(mask.sum()), len(close_arr))
        ts_w = ts_arr[mask][:n]
        dt_p = [datetime.fromtimestamp(int(v), tz=timezone.utc) for v in ts_w]

        if has_full and len(dt_p) > 0:
            o_w = open_arr[mask][:n]; h_w = high_arr[mask][:n]
            l_w = low_arr[mask][:n];  c_w = close_arr[mask][:n]
            # Draw candlesticks as wick + body
            bar_width_days = max((ts_w[-1] - ts_w[0]) / max(len(ts_w), 1) / 86400 * 0.6, 0.003) if len(ts_w) > 1 else 0.01
            for i in range(min(len(dt_p), 2000)):  # cap for performance
                dt_i = dt_p[i]
                bull = float(c_w[i]) >= float(o_w[i])
                col = "#3fb950" if bull else "#f85149"
                ax_p.vlines(dt_i, float(l_w[i]), float(h_w[i]), color=col, lw=0.5, zorder=2)
                body_lo = min(float(o_w[i]), float(c_w[i]))
                body_hi = max(float(o_w[i]), float(c_w[i]))
                if body_hi > body_lo:
                    ax_p.fill_betweenx([body_lo, body_hi],
                                       [dt_i, dt_i], color=col, alpha=0.7, zorder=3,
                                       where=[True, True])
        elif len(dt_p) > 0:
            ax_p.plot(dt_p, close_arr[mask][:len(dt_p)], color=PRICE, lw=0.7, zorder=2)

        # Trade entry / exit markers
        longs  = [(datetime.fromtimestamp(int(t["entry_ts"]), tz=timezone.utc), float(t.get("entry_price", 0)))
                  for t in tw if t.get("entry_ts") and t.get("side","").upper() == "LONG"]
        shorts = [(datetime.fromtimestamp(int(t["entry_ts"]), tz=timezone.utc), float(t.get("entry_price", 0)))
                  for t in tw if t.get("entry_ts") and t.get("side","").upper() == "SHORT"]
        exits  = [(datetime.fromtimestamp(int(t["exit_ts"]),  tz=timezone.utc), float(t.get("exit_price", 0)))
                  for t in tw if t.get("exit_ts") and t.get("exit_price")]
        # Horizontal trade spans
        for t in tw:
            if not (t.get("entry_ts") and t.get("exit_ts") and t.get("entry_price")): continue
            dt_en = datetime.fromtimestamp(int(t["entry_ts"]), tz=timezone.utc)
            dt_ex = datetime.fromtimestamp(int(t["exit_ts"]),  tz=timezone.utc)
            ep = float(t.get("entry_price", 0))
            col = "#3fb95028" if float(t.get("pnl_pct", 0)) > 0 else "#f8514928"
            ax_p.axhspan(ep * 0.9995, ep * 1.0005, xmin=0, xmax=1, alpha=0, zorder=1)  # keep scale
            ax_p.fill_betweenx([ep * 0.9998, ep * 1.0002], [dt_en, dt_en], alpha=0, zorder=1)
        if longs:  ax_p.scatter([v[0] for v in longs],  [v[1] for v in longs],  marker="^", s=55, color="#3fb950", zorder=6, alpha=0.92, label=f"Long ({len(longs)})")
        if shorts: ax_p.scatter([v[0] for v in shorts], [v[1] for v in shorts], marker="v", s=55, color="#f0883e", zorder=6, alpha=0.92, label=f"Short ({len(shorts)})")
        if exits:  ax_p.scatter([v[0] for v in exits],  [v[1] for v in exits],  marker="x", s=35, color="#f85149", zorder=6, lw=1.3, alpha=0.75, label="Exit")
        ax_p.set_ylabel("Price (15m)", color=TICK, fontsize=8)
        ax_p.legend(loc="upper left", fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
        ax_p.set_title(
            f"OPT | {account}:{sym}  pool={ps:+.4f}  wr={wr:.1f}%  dd={dd:.2f}%  "
            f"trades={ntrades:,}  wins={n_win}/{len(pnl)}  (last {days}d)",
            color="#c9d1d9", fontsize=9, pad=4)
        ax_p.xaxis.set_visible(False)

    ax_cum = fig.add_subplot(gs[panel]); _style(ax_cum); panel += 1
    if len(dt_exit) == len(cum):
        ax_cum.plot(dt_exit, cum, color="#58a6ff", lw=1.4, zorder=3)
        ax_cum.fill_between(dt_exit, cum, alpha=0.13, color="#58a6ff")
    ax_cum.axhline(0, color="#6e7681", lw=0.5, linestyle="--")
    ax_cum.set_ylabel("Cum PnL %", color=TICK, fontsize=8)
    if not has_ohlc:
        ax_cum.set_title(f"OPT | {account}:{sym}  pool={ps:+.4f}  wr={wr:.1f}%  trades={ntrades:,}  (last {days}d)", color="#c9d1d9", fontsize=9)
    ax_cum.xaxis.set_visible(True)

    ax_bar = fig.add_subplot(gs[panel], sharex=ax_cum); _style(ax_bar)
    if len(dt_exit) == len(pnl):
        ax_bar.bar(dt_exit, pnl, color=["#3fb950" if p > 0 else "#f85149" for p in pnl], width=0.4, zorder=3)
    ax_bar.axhline(0, color="#6e7681", lw=0.5, linestyle="--")
    ax_bar.set_ylabel("Trade PnL %", color=TICK, fontsize=8)
    ax_bar.legend(handles=[Patch(facecolor="#3fb950", label=f"Win ({n_win})"),
                            Patch(facecolor="#f85149", label=f"Loss ({len(pnl)-n_win})")],
                  loc="upper right", fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    out = CHARTS_DIR / f"OPT_{sym}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  [chart] {out.name}")


# ── Phase 1: baseline from existing active_config ─────────────────────────

def phase1_baseline(syms: List[str], account: str) -> Dict[str, Dict]:
    print("=" * 80)
    print(f"PHASE 1 — baseline from per_sym_active_config.json ({account})")
    print("=" * 80)
    if not ACTIVE_CFG.exists():
        return {}
    try: existing = json.loads(ACTIVE_CFG.read_text())
    except Exception: return {}
    out: Dict[str, Dict] = {}
    for sym in syms:
        key = f"{sym}_LONG"
        if key not in existing:
            print(f"  {sym}: no existing config")
            continue
        entry = existing[key]
        ps = float(entry.get("wsharpe", 0))
        tr = int(entry.get("trades", 0))
        print(f"  {sym}: existing pool={ps:+.4f} trades={tr}")
        out[sym] = {"pool_sharpe": ps, "trades": tr, "wr_pct": 0.0,
                    "max_dd_pct": 0.0, "total_gain_pct": 0.0, "gain_per_yr": 0.0,
                    "years": 1.0, "n_wins": 0, "tag": f"existing_{sym}"}
    return out


# ── Phase 2: mutation sweep ────────────────────────────────────────────────

def phase2_sweep_sym(sym: str, run_root: Path, sweep_csv: Path,
                     years: float) -> Tuple[Dict, Dict, List[Dict]]:
    print(f"\n{'─'*70}")
    print(f"PHASE 2 — {sym}")
    print(f"{'─'*70}")
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists():
        print(f"  SKIP {sym}: NPZ not found")
        return {}, {}, []

    z = np.load(str(npz_path)); npz = {k: z[k] for k in z.files}; z.close()
    run_dir = run_root / sym; run_dir.mkdir(parents=True, exist_ok=True)
    grid = mutation_grid()
    print(f"  grid: {len(grid)} variants")
    results: List[Tuple[str, Dict, Dict]] = []
    best_trades: List[Dict] = []
    best_pnl = -1e9
    t0 = time.time()

    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}__{tag}__{i:03d}"
        rets, trades = run_engine(sym, ovr, run_dir, run_id, npz)
        m = per_sym_metrics(rets, years, sym)
        m["tag"] = f"crypto_mut_{sym}_{tag}"
        m["effective_score"] = effective_score(m)
        v = verdict(m)
        m["verdict"] = v
        results.append((tag, ovr, m))
        try: mg.write_sharpe_row(sweep_csv, m, mode="crypto", append=True)
        except Exception: pass
        if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX and m["trades"] >= PROMOTE_TRADES_MIN:
            if m["total_gain_pct"] > best_pnl:
                best_pnl = m["total_gain_pct"]; best_trades = trades
                marker = " ← BEST"
            else:
                marker = ""
        else:
            marker = ""
        if i <= 3 or i % 15 == 0 or marker:
            print(f"    [{i:3d}/{len(grid)}] {tag:35s} pool={m['pool_sharpe']:+.4f} "
                  f"pnl={m['total_gain_pct']:+8.1f}% tr={m['trades']:>6d} {v}{marker}")

    elapsed = time.time() - t0
    print(f"  {sym} done {elapsed:.1f}s ({elapsed/len(grid):.2f}s/variant)")

    # Pick winner
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX and m["trades"] >= PROMOTE_TRADES_MIN]
    if qualified:
        tag, ovr, m = max(qualified, key=lambda t: t[2]["total_gain_pct"])
    else:
        viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= PROMOTE_TRADES_MIN] or results
        tag, ovr, m = max(viable, key=lambda t: t[2].get("effective_score", 0))

    print(f"  WINNER: {tag} | pool={m['pool_sharpe']:+.4f} | pnl={m['total_gain_pct']:+.1f}% "
          f"| tr={m['trades']:,} | dd={m['max_dd_pct']:.2f}% | wr={m['wr_pct']:.1f}%")
    return ovr, m, best_trades


# ── Phase 3: write to active config ───────────────────────────────────────

_BANNED_PARAMS = {"D_TREND_REQUIRED", "MIN_HOLD_BARS"}  # structural — not per-symbol safe via overlay

def promote_to_active_config(account: str, winners: Dict[str, Tuple[Dict, Dict]]) -> None:
    print()
    print("=" * 80)
    print(f"PHASE 3 — promote to per_sym_active_config.json ({account})")
    print("=" * 80)
    ACTIVE_CFG.parent.mkdir(parents=True, exist_ok=True)
    try: existing = json.loads(ACTIVE_CFG.read_text())
    except Exception: existing = {}
    now_tag = time.strftime("%Y-%m-%d", time.gmtime())
    updated = 0
    for sym, (ovr, m) in winners.items():
        ps = float(m.get("pool_sharpe", 0)); tr = int(m.get("trades", 0))
        live_ovr = {k: v for k, v in ovr.items()
                    if not k.startswith("_") and k not in _BANNED_PARAMS}
        if ps <= 0 or tr < PROMOTE_TRADES_MIN or not live_ovr:
            print(f"  SKIP {sym}: pool={ps:+.4f} trades={tr} — quality gate")
            continue
        entry = {"winning_tag": f"{account}_{sym}_{now_tag}", "wsharpe": ps, "trades": tr,
                 "sample_tag": f"CRYPTO_{account.upper()}", "overrides": live_ovr}
        for side in ("LONG", "SHORT"):
            existing[f"{sym}_{side}"] = entry
        updated += 1
        print(f"  WRITE {sym}: pool={ps:+.4f} trades={tr:,} params={len(live_ovr)}")
    ACTIVE_CFG.write_text(json.dumps(existing, indent=2, default=str))
    print(f"  {updated}/{len(winners)} symbols written → {ACTIVE_CFG}")


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="ang",
                    help=f"account to sweep ({'/'.join(SUPPORTED_ACCOUNTS+['all'])})")
    ap.add_argument("--sym", default="", help="comma-separated symbol subset")
    ap.add_argument("--mutate-all", action="store_true",
                    help="force Phase 2 even if Phase 1 already passes")
    ap.add_argument("--phase", default="1234", help="phases to run (e.g. '12' skips chart)")
    ap.add_argument("--years", type=float, default=2.0, help="backtest years")
    ap.add_argument("--days-chart", type=int, default=60, help="days window for charts")
    args = ap.parse_args()

    accounts = SUPPORTED_ACCOUNTS if args.account == "all" else [args.account]
    for acct in accounts:
        if acct not in SUPPORTED_ACCOUNTS:
            print(f"Unknown account: {acct}. Choices: {SUPPORTED_ACCOUNTS + ['all']}")
            return 1

    ts = int(time.time())
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)

    for account in accounts:
        print()
        print(f"{'═'*80}")
        print(f"ACCOUNT: {account}")
        print(f"{'═'*80}")

        syms = load_symbols_for_account(account)
        if args.sym:
            filter_syms = {s.strip() for s in args.sym.split(",") if s.strip()}
            syms = [s for s in syms if s in filter_syms]
        if not syms:
            print(f"  No symbols found for account {account}")
            continue
        print(f"  {len(syms)} symbols: {' '.join(syms[:20])}{'...' if len(syms)>20 else ''}")

        sweep_csv = SWEEP_DIR / f"per_sym_crypto_{account}_{ts}.csv"
        run_root = SWEEP_DIR / f"per_sym_crypto_{account}_{ts}" / "runs"
        run_root.mkdir(parents=True, exist_ok=True)

        phase1: Dict[str, Dict] = {}
        if "1" in args.phase:
            phase1 = phase1_baseline(syms, account)

        winners: Dict[str, Tuple[Dict, Dict]] = {}
        winner_trades: Dict[str, List[Dict]] = {}

        if "2" in args.phase:
            for sym in syms:
                p1 = phase1.get(sym, {})
                ps1 = float(p1.get("pool_sharpe", 0))
                if ps1 > PROMOTE_POOL_MIN and not args.mutate_all:
                    winners[sym] = ({}, p1)
                    print(f"\n  {sym}: Phase 1 pool={ps1:+.4f} — using existing, no mutation (--mutate-all to force)")
                    continue
                if NPZ_DIR.joinpath(f"{sym}.npz").exists():
                    try:
                        ovr, m, trades = phase2_sweep_sym(sym, run_root, sweep_csv, args.years)
                        if m:
                            winners[sym] = (ovr, m)
                            winner_trades[sym] = trades
                    except Exception as e:
                        print(f"  PHASE2 EXC {sym}: {e}")
                    gc.collect()
                else:
                    print(f"\n  {sym}: NPZ not found — skip sweep")

        if "3" in args.phase and winners:
            promote_to_active_config(account, winners)

        if "4" in args.phase and winners:
            print()
            print("=" * 80)
            print(f"PHASE 4 — charts ({account})")
            print("=" * 80)
            for sym in syms:
                if sym not in winners: continue
                _, m = winners[sym]
                trades = winner_trades.get(sym, [])
                if trades:
                    generate_ohlc_chart(sym, trades, m, account, days=args.days_chart)
                else:
                    print(f"  {sym}: no trades for chart")

        print()
        print(f"CSV: {sweep_csv}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
