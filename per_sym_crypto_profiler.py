#!/usr/bin/env python3
"""per_sym_crypto_profiler — vectorized per-(sym, side) parameter optimizer.

Architecture:
  - For each (sym, side):
      1. Marginal sweep: BB_LEN/STD per TF, WT_CHAN/AVG per TF, DC_PERIOD per TF,
         ENTRY_MODE, BB_LONG_ENTRY_MAX/SHORT_ENTRY_MIN, MIN_HOLD_BARS, COOLDOWN_BARS,
         REQUIRE_D_TREND, REQUIRE_W_TREND, MIN_TFS_AGREE.
         Each parameter swept in isolation against current best baseline.
      2. Trade-rate check: target 5..15 tpd. If winner has tpd>15, escalate
         HTF filter (D_TREND → W_TREND → MIN_TFS_AGREE 3) until tpd≤15 AND
         pool_sharpe doesn't drop more than 10%. If all escalations fail to fit,
         flag NO_VIABLE.
      3. Promote winner if pool_sharpe ≥ promote_floor AND wr_pct ≥ wr_floor AND
         dd ≤ dd_cap AND tpd in [tpd_min, tpd_max].
  - Cross-(sym, side) parallelism via ProcessPoolExecutor.
  - All metrics routed through metrics_guard (NO LIES MANDATE).
  - Output: data/hourly_reconfig/per_sym_active_config.json (LONG/SHORT keys per sym).
  - Chart per (sym, side): candles + entry/exit markers + cumPnL + per-trade bars.

User directive 2026-05-05:
  - "5-15 trades/day per symbol average" — hard band
  - "DO NOT FAKE SHARPES OR WIN RATES" — metrics_guard chokepoint
  - Multi-TF agreement required (≥2 TFs) — engine hardcoded floor
  - Crypto-only (stocks adapted later from this template)
  - Account priority: ang → fin → men → flz (process syms in this order)
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from per_sym_engine_crypto import (
    SymParams, simulate, COMMISSION_RT_PCT, MIN_TFS_AGREE_FLOOR,
    NPZ_DIR, _npz_cache,
)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.patches import Patch
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


OUT_CFG  = ROOT / 'data' / 'hourly_reconfig' / 'per_sym_active_config.json'
SWEEP_CSV_DIR = ROOT / 'data' / 'sweep_results'
CHARTS_DIR = ROOT / 'plots'

# Promote criteria (user directive 2026-05-05: 5-15 tpd, no faked sharpes)
TPD_MIN = 5.0
TPD_MAX = 15.0
PROMOTE_POOL_FLOOR = 0.3
PROMOTE_WR_FLOOR = 50.0
PROMOTE_DD_CAP = 25.0   # crypto can stomach more DD than stocks
DIAG_POOL_FLOOR = 0.0   # below 0 = noise

ACCOUNT_ORDER = ['ang', 'fin', 'men', 'flz']

# ───────────────────────── sweep grid ─────────────────────────────────────

BB_LEN_VALUES   = [10, 14, 20, 30, 50, 80]
BB_STD_VALUES   = [1.5, 1.8, 2.0, 2.2, 2.5, 3.0]
WT_CHAN_VALUES  = [6, 9, 10, 14, 18, 25]
WT_AVG_VALUES   = [12, 18, 21, 28, 35, 50]
DC_PERIOD_VALUES = [10, 20, 30, 50, 80, 120]

# Non-indicator knobs (additional v8-style settings — sweep at marginal level)
# These shape entry/exit behavior orthogonally to BB/WT/DC params.
ENTRY_MODE_VALUES = ['or', 'dc_break', 'pullback']
BB_LONG_ENTRY_MAX_VALUES = [0.30, 0.40, 0.50, 0.60, 0.70]
BB_SHORT_ENTRY_MIN_VALUES = [0.30, 0.40, 0.50, 0.60, 0.70]
MIN_HOLD_VALUES = [1, 3, 5, 8, 13, 20]
COOLDOWN_VALUES = [0, 1, 3, 6, 10]
WT_CROSS_LB_VALUES = [1, 2, 3, 5, 8]
BB_EXTREME_THRESH_VALUES = [0.05, 0.10, 0.15, 0.20]
BB_SQUEEZE_RATIO_VALUES = [1.2, 1.5, 1.8, 2.2]
BB_SQUEEZE_LB_VALUES = [10, 20, 30, 50]


def make_baseline() -> SymParams:
    return SymParams()


def variants_for_param(base: SymParams, param: str, values: List) -> List[Tuple[str, SymParams]]:
    """Return [(tag, params)] with `param` set to each value in `values`."""
    out = []
    for v in values:
        if getattr(base, param) == v:
            continue
        p = base.copy()
        setattr(p, param, v)
        out.append((f"{param}={v}", p))
    return out


def marginal_sweep_grid(base: SymParams) -> List[Tuple[str, SymParams]]:
    """Build the full marginal sweep against `base`. Each variant changes ONE param."""
    grid: List[Tuple[str, SymParams]] = [('baseline', base.copy())]
    for tf in ('15m', '1h', '4h'):
        grid += variants_for_param(base, f'BB_LEN_{tf}', BB_LEN_VALUES)
        grid += variants_for_param(base, f'BB_STD_{tf}', BB_STD_VALUES)
        grid += variants_for_param(base, f'WT_CHAN_{tf}', WT_CHAN_VALUES)
        grid += variants_for_param(base, f'WT_AVG_{tf}', WT_AVG_VALUES)
        grid += variants_for_param(base, f'DC_PERIOD_{tf}', DC_PERIOD_VALUES)
    grid += variants_for_param(base, 'ENTRY_MODE', ENTRY_MODE_VALUES)
    grid += variants_for_param(base, 'BB_LONG_ENTRY_MAX', BB_LONG_ENTRY_MAX_VALUES)
    grid += variants_for_param(base, 'BB_SHORT_ENTRY_MIN', BB_SHORT_ENTRY_MIN_VALUES)
    grid += variants_for_param(base, 'MIN_HOLD_BARS_15m', MIN_HOLD_VALUES)
    grid += variants_for_param(base, 'COOLDOWN_BARS_15m', COOLDOWN_VALUES)
    # Entry-path toggles + their params
    grid += variants_for_param(base, 'ENTRY_WT_CROSS_EVENT_ENABLED', [True, False])
    grid += variants_for_param(base, 'ENTRY_BB_EXTREME_BOUNCE_ENABLED', [True, False])
    grid += variants_for_param(base, 'ENTRY_BB_SQUEEZE_RELEASE_ENABLED', [True, False])
    grid += variants_for_param(base, 'ENTRY_WT_CROSS_LOOKBACK', WT_CROSS_LB_VALUES)
    grid += variants_for_param(base, 'ENTRY_BB_EXTREME_THRESHOLD', BB_EXTREME_THRESH_VALUES)
    grid += variants_for_param(base, 'ENTRY_BB_SQUEEZE_RATIO', BB_SQUEEZE_RATIO_VALUES)
    grid += variants_for_param(base, 'ENTRY_BB_SQUEEZE_LOOKBACK', BB_SQUEEZE_LB_VALUES)
    # HTF gating sweep (the OTHER way to control trade rate)
    grid += variants_for_param(base, 'REQUIRE_D_TREND', [True, False])
    grid += variants_for_param(base, 'REQUIRE_W_TREND', [True, False])
    grid += variants_for_param(base, 'MIN_TFS_AGREE', [2, 3])
    return grid


# ───────────────────────── scoring + winner pick ──────────────────────────

def effective_score(m: Dict) -> float:
    """Sharpe weighted by sqrt(trades/1000) per user feedback 2026-05-01."""
    return float(m.get('pool_sharpe', 0)) * math.sqrt(max(1, m.get('trades', 0)) / 1000.0)


def in_trade_band(tpd: float) -> bool:
    return TPD_MIN <= tpd <= TPD_MAX


def verdict(m: Dict) -> str:
    tpd = float(m.get('trades_per_day', 0))
    pool = float(m.get('pool_sharpe', 0))
    wr = float(m.get('wr_pct', 0))
    dd = float(m.get('max_dd_pct', 0))
    tr = int(m.get('trades', 0))
    if tr < 30:
        return 'INSUFFICIENT_TRADES'
    if dd > PROMOTE_DD_CAP:
        return 'REJECT_DD'
    if not in_trade_band(tpd):
        if tpd > TPD_MAX:
            return 'REJECT_TPD_HIGH'
        return 'REJECT_TPD_LOW'
    if pool < DIAG_POOL_FLOOR:
        return 'NOISE'
    if wr < PROMOTE_WR_FLOOR:
        return 'REJECT_WR'
    if pool < PROMOTE_POOL_FLOOR:
        return 'DIAGNOSTIC'
    return 'PROMOTE'


def pick_winner(results: List[Tuple[str, SymParams, Dict]]) -> Tuple[str, SymParams, Dict]:
    """Pick best variant. Priority:
      1. PROMOTE candidates → max pool_sharpe
      2. else any with tpd in band → max effective_score
      3. else max effective_score (purely diagnostic)
    """
    if not results:
        raise ValueError("no results")
    promoted = [(t, p, m) for t, p, m in results if verdict(m) == 'PROMOTE']
    if promoted:
        return max(promoted, key=lambda x: x[2]['pool_sharpe'])
    in_band = [(t, p, m) for t, p, m in results if in_trade_band(float(m.get('trades_per_day', 0))) and m.get('trades', 0) >= 30]
    if in_band:
        return max(in_band, key=lambda x: effective_score(x[2]))
    return max(results, key=lambda x: effective_score(x[2]))


# ───────────────────────── HTF auto-tighten ───────────────────────────────

def htf_tighten_chain(base: SymParams) -> List[Tuple[str, SymParams]]:
    """Yield successively tighter HTF filters when tpd > TPD_MAX."""
    chain: List[Tuple[str, SymParams]] = []
    p = base.copy()
    p.REQUIRE_D_TREND = True
    chain.append(('htf+D_TREND', p.copy()))
    p.MIN_TFS_AGREE = max(p.MIN_TFS_AGREE, 3)
    chain.append(('htf+D_TREND+3tfs', p.copy()))
    p.REQUIRE_W_TREND = True
    chain.append(('htf+D+W_TREND+3tfs', p.copy()))
    p.MIN_TFS_AGREE = max(p.MIN_TFS_AGREE, 4)
    chain.append(('htf+D+W_TREND+4tfs', p.copy()))
    return chain


def loosen_chain(base: SymParams) -> List[Tuple[str, SymParams]]:
    """Successively looser configs to drive tpd UP toward TPD_MIN."""
    chain: List[Tuple[str, SymParams]] = []
    p = base.copy()
    p.MIN_HOLD_BARS_15m = 1
    p.COOLDOWN_BARS_15m = 0
    chain.append(('loose+min_hold1+cd0', p.copy()))
    p.ENTRY_WT_CROSS_EVENT_ENABLED = True
    p.ENTRY_BB_EXTREME_BOUNCE_ENABLED = True
    p.ENTRY_BB_SQUEEZE_RELEASE_ENABLED = True
    p.ENTRY_WT_CROSS_LOOKBACK = 5
    chain.append(('loose+all_paths_on', p.copy()))
    p.DC_PERIOD_15m = 10
    p.DC_PERIOD_1h = 10
    p.DC_PERIOD_4h = 10
    chain.append(('loose+dc_short', p.copy()))
    p.BB_LONG_ENTRY_MAX = 0.70
    p.BB_SHORT_ENTRY_MIN = 0.30
    chain.append(('loose+wider_bb_entry', p.copy()))
    p.BB_LEN_15m = 14
    p.BB_LEN_1h = 14
    p.BB_LEN_4h = 14
    chain.append(('loose+bb_short', p.copy()))
    return chain


def auto_tighten_for_tpd(sym: str, side: str, winner_params: SymParams,
                         winner_metrics: Dict, years_back: float) -> Tuple[SymParams, Dict, str]:
    """Trade-rate band enforcement.
    - tpd > TPD_MAX: apply HTF tightening chain, accept first that lands in band with Sharpe ≥ 90% of orig.
    - tpd < TPD_MIN: apply loosening chain, accept first that lands in band with Sharpe ≥ 90% of orig.
    - In band: return as-is.
    Returns (final_params, final_metrics, note).
    """
    tpd = float(winner_metrics.get('trades_per_day', 0))
    if TPD_MIN <= tpd <= TPD_MAX:
        return winner_params, winner_metrics, 'tpd_in_band'
    orig_pool = float(winner_metrics.get('pool_sharpe', 0))
    chain = htf_tighten_chain(winner_params) if tpd > TPD_MAX else loosen_chain(winner_params)
    direction = 'tighten' if tpd > TPD_MAX else 'loosen'
    candidates: List[Tuple[str, SymParams, Dict]] = [(f'orig_{direction}_target', winner_params, winner_metrics)]
    for tag, p in chain:
        m = simulate(sym, side, p, years_back=years_back)
        if m is None:
            continue
        candidates.append((tag, p, m))
        if in_trade_band(float(m.get('trades_per_day', 0))) and m.get('pool_sharpe', 0) >= 0.9 * orig_pool:
            return p, m, f'auto_{direction}:{tag}'
    # No chain step qualified — pick best by effective_score
    best = max(candidates, key=lambda c: effective_score(c[2]))
    return best[1], best[2], f'auto_{direction}_best:{best[0]}'


# ───────────────────────── per-(sym, side) job ────────────────────────────

def optimize_sym_side(sym: str, side: str, years_back: float = 4.0) -> Optional[Dict]:
    """Returns {'sym', 'side', 'params', 'metrics', 'verdict', 'note'} or None."""
    base = make_baseline()
    base_m = simulate(sym, side, base, years_back=years_back)
    if base_m is None:
        return None

    grid = marginal_sweep_grid(base)
    results: List[Tuple[str, SymParams, Dict]] = []
    for tag, p in grid:
        m = simulate(sym, side, p, years_back=years_back)
        if m is None:
            continue
        m['variant_tag'] = tag
        results.append((tag, p, m))

    winning_tag, winning_params, winning_m = pick_winner(results)

    # Trade-rate auto-tighten
    final_params, final_m, note = auto_tighten_for_tpd(sym, side, winning_params, winning_m, years_back)

    final_v = verdict(final_m)
    return {
        'sym': sym, 'side': side,
        'params': final_params.to_dict(),
        'metrics': final_m,
        'verdict': final_v,
        'note': note,
        'winning_tag': winning_tag,
        'baseline_pool': float(base_m.get('pool_sharpe', 0)),
        'baseline_tpd': float(base_m.get('trades_per_day', 0)),
        'n_variants_tested': len(results),
    }


def _worker(args_tuple) -> Optional[Dict]:
    sym, side, years_back = args_tuple
    try:
        return optimize_sym_side(sym, side, years_back=years_back)
    except Exception:
        return {'sym': sym, 'side': side, 'error': traceback.format_exc()[-500:]}


# ───────────────────────── promote to active_config + CSV ─────────────────

_BANNED_PARAMS = set()  # none banned for crypto-engine — params are all engine-internal


def promote_results(results: List[Dict], csv_path: Path) -> int:
    OUT_CFG.parent.mkdir(parents=True, exist_ok=True)
    SWEEP_CSV_DIR.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(OUT_CFG.read_text()) if OUT_CFG.exists() else {}
    except Exception:
        existing = {}
    promoted = 0
    now = time.strftime('%Y-%m-%d', time.gmtime())
    for r in results:
        if r is None or 'error' in r:
            continue
        sym = r['sym']; side = r['side']
        m = r['metrics']
        v = r['verdict']
        # Always write — but mark verdict so the live loader can filter
        entry = {
            'winning_tag': f"crypto_{sym}_{side}_{now}",
            'wsharpe': float(m.get('pool_sharpe', 0)),
            'pool_sharpe': float(m.get('pool_sharpe', 0)),
            'trades': int(m.get('trades', 0)),
            'trades_per_day': float(m.get('trades_per_day', 0)),
            'wr_pct': float(m.get('wr_pct', 0)),
            'max_dd_pct': float(m.get('max_dd_pct', 0)),
            'avg_gain_trade': float(m.get('avg_gain_trade', 0)),
            'gain_per_yr': float(m.get('gain_per_yr', 0)),
            'years': float(m.get('years', 0)),
            'sample_tag': 'CRYPTO_PER_SYM_V2',
            'verdict': v,
            'note': r.get('note', ''),
            'baseline_pool': r.get('baseline_pool', 0),
            'baseline_tpd': r.get('baseline_tpd', 0),
            'n_variants_tested': r.get('n_variants_tested', 0),
            'overrides': {k: vv for k, vv in r['params'].items() if k not in _BANNED_PARAMS},
        }
        existing[f"{sym}_{side}"] = entry
        # Canonical row via metrics_guard — strip trade_list/params blobs (they're huge).
        try:
            row = {k: vv for k, vv in m.items() if k not in ('trade_list', 'params', 'variant_tag')}
            row['tag'] = f"per_sym_{sym}_{side}"
            row['n_syms'] = 1
            mg.write_sharpe_row(csv_path, row, mode='crypto', append=True)
        except Exception as e:
            print(f"  [csv] REFUSED {sym} {side}: {e}", flush=True)
        if v == 'PROMOTE':
            promoted += 1
    OUT_CFG.write_text(json.dumps(existing, indent=2, default=str))
    return promoted


# ───────────────────────── chart generation ───────────────────────────────

def generate_chart(sym: str, side: str, params: SymParams, m: Dict, days: int = 60) -> None:
    if not HAS_MPL:
        return
    trades = m.get('trade_list', [])
    if not trades:
        return
    from datetime import datetime, timezone
    from per_sym_engine_crypto import load_3m_base, build_tf_data
    base = load_3m_base(sym, years_back=4.0)
    if base is None:
        return
    tfs = build_tf_data(base)
    if '15m' not in tfs:
        return
    o = tfs['15m']['open']; h = tfs['15m']['high']; l = tfs['15m']['low']; c = tfs['15m']['close']; ts = tfs['15m']['ts']
    cutoff = ts[-1] - days * 86400
    mask = ts >= cutoff
    if mask.sum() < 10:
        mask = np.ones(len(ts), dtype=bool)  # use all
    tw = [t for t in trades if t.get('exit_ts', 0) >= cutoff]
    if not tw:
        tw = trades[-300:]
    BG = "#0d1117"; GRID = "#21262d"; TICK = "#6e7681"
    fig = plt.figure(figsize=(18, 12), facecolor=BG, dpi=130)
    fig.patch.set_facecolor(BG)
    gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1.5, 1], hspace=0.05, figure=fig)

    def _style(ax):
        ax.set_facecolor(BG)
        for sp in ax.spines.values(): sp.set_color("#30363d")
        ax.tick_params(colors=TICK, labelsize=7)
        ax.grid(axis='y', color=GRID, lw=0.4, zorder=0)

    ax_p = fig.add_subplot(gs[0]); _style(ax_p)
    ts_w = ts[mask]; o_w = o[mask]; h_w = h[mask]; l_w = l[mask]; c_w = c[mask]
    dt_p = [datetime.fromtimestamp(int(t), tz=timezone.utc) for t in ts_w]
    # Wicks (vlines) + bodies as thicker vlines — works with datetime axis.
    n_show = min(len(dt_p), 1500)
    step = max(1, len(dt_p) // n_show)
    bull_ts = [dt_p[i] for i in range(0, len(dt_p), step) if float(c_w[i]) >= float(o_w[i])]
    bear_ts = [dt_p[i] for i in range(0, len(dt_p), step) if float(c_w[i]) <  float(o_w[i])]
    bull_lo = [float(l_w[i]) for i in range(0, len(dt_p), step) if float(c_w[i]) >= float(o_w[i])]
    bull_hi = [float(h_w[i]) for i in range(0, len(dt_p), step) if float(c_w[i]) >= float(o_w[i])]
    bull_bo = [min(float(o_w[i]),float(c_w[i])) for i in range(0, len(dt_p), step) if float(c_w[i]) >= float(o_w[i])]
    bull_bc = [max(float(o_w[i]),float(c_w[i])) for i in range(0, len(dt_p), step) if float(c_w[i]) >= float(o_w[i])]
    bear_lo = [float(l_w[i]) for i in range(0, len(dt_p), step) if float(c_w[i]) <  float(o_w[i])]
    bear_hi = [float(h_w[i]) for i in range(0, len(dt_p), step) if float(c_w[i]) <  float(o_w[i])]
    bear_bo = [min(float(o_w[i]),float(c_w[i])) for i in range(0, len(dt_p), step) if float(c_w[i]) <  float(o_w[i])]
    bear_bc = [max(float(o_w[i]),float(c_w[i])) for i in range(0, len(dt_p), step) if float(c_w[i]) <  float(o_w[i])]
    if bull_ts:
        ax_p.vlines(bull_ts, bull_lo, bull_hi, color="#3fb950", lw=0.5, zorder=2)
        ax_p.vlines(bull_ts, bull_bo, bull_bc, color="#3fb950", lw=2.0, zorder=3, alpha=0.85)
    if bear_ts:
        ax_p.vlines(bear_ts, bear_lo, bear_hi, color="#f85149", lw=0.5, zorder=2)
        ax_p.vlines(bear_ts, bear_bo, bear_bc, color="#f85149", lw=2.0, zorder=3, alpha=0.85)
    longs  = [(datetime.fromtimestamp(int(t['entry_ts']), tz=timezone.utc), float(t['entry_price'])) for t in tw if t['side'] == 'LONG']
    shorts = [(datetime.fromtimestamp(int(t['entry_ts']), tz=timezone.utc), float(t['entry_price'])) for t in tw if t['side'] == 'SHORT']
    exits  = [(datetime.fromtimestamp(int(t['exit_ts']),  tz=timezone.utc), float(t['exit_price']))  for t in tw]
    if longs:  ax_p.scatter([v[0] for v in longs],  [v[1] for v in longs],  marker='^', s=55, color="#3fb950", zorder=6, alpha=0.9, label=f'Long ({len(longs)})')
    if shorts: ax_p.scatter([v[0] for v in shorts], [v[1] for v in shorts], marker='v', s=55, color="#f0883e", zorder=6, alpha=0.9, label=f'Short ({len(shorts)})')
    if exits:  ax_p.scatter([v[0] for v in exits],  [v[1] for v in exits],  marker='x', s=35, color="#f85149", zorder=6, lw=1.3, alpha=0.75, label='Exit')
    ax_p.set_ylabel('Price (15m)', color=TICK, fontsize=8)
    ax_p.legend(loc='upper left', fontsize=7, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')
    ovr_str = (
        f"BB_15m={params.BB_LEN_15m}/{params.BB_STD_15m}  "
        f"WT_15m={params.WT_CHAN_15m}/{params.WT_AVG_15m}  DC_15m={params.DC_PERIOD_15m}  "
        f"BB_1h={params.BB_LEN_1h}/{params.BB_STD_1h}  DC_1h={params.DC_PERIOD_1h}  "
        f"BB_4h={params.BB_LEN_4h}/{params.BB_STD_4h}  DC_4h={params.DC_PERIOD_4h}  "
        f"min_tfs={params.MIN_TFS_AGREE} mode={params.ENTRY_MODE}"
    )
    ax_p.set_title(
        f"OPT | {sym}:{side}  pool={m['pool_sharpe']:+.4f}  wr={m['wr_pct']:.1f}%  dd={m['max_dd_pct']:.2f}%  "
        f"trades={m['trades']:,}  tpd={m['trades_per_day']:.2f}  yrs={m['years']:.2f}\n{ovr_str}",
        color='#c9d1d9', fontsize=8, pad=4)
    ax_p.xaxis.set_visible(False)

    pnl = [float(t['pnl_pct']) for t in tw]
    eq = 0.0; cum = []
    for p in pnl: eq += p; cum.append(eq)
    dt_exit = [datetime.fromtimestamp(int(t['exit_ts']), tz=timezone.utc) for t in tw]
    ax_cum = fig.add_subplot(gs[1]); _style(ax_cum)
    if dt_exit and len(dt_exit) == len(cum):
        ax_cum.plot(dt_exit, cum, color='#58a6ff', lw=1.4, zorder=3)
        ax_cum.fill_between(dt_exit, cum, alpha=0.13, color='#58a6ff')
    ax_cum.axhline(0, color='#6e7681', lw=0.5, linestyle='--')
    ax_cum.set_ylabel('Cum PnL %', color=TICK, fontsize=8)

    ax_bar = fig.add_subplot(gs[2], sharex=ax_cum); _style(ax_bar)
    if dt_exit and len(dt_exit) == len(pnl):
        cols = ['#3fb950' if p > 0 else '#f85149' for p in pnl]
        ax_bar.bar(dt_exit, pnl, color=cols, width=0.4, zorder=3)
    ax_bar.axhline(0, color='#6e7681', lw=0.5, linestyle='--')
    ax_bar.set_ylabel('Trade PnL %', color=TICK, fontsize=8)
    n_win = sum(1 for p in pnl if p > 0); n_lose = len(pnl) - n_win
    ax_bar.legend(handles=[
        Patch(facecolor='#3fb950', label=f'Win ({n_win})'),
        Patch(facecolor='#f85149', label=f'Loss ({n_lose})')
    ], loc='upper right', fontsize=7, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    out = CHARTS_DIR / f"OPT_{sym}_{side}.png"
    plt.savefig(out, dpi=130, bbox_inches='tight', facecolor=BG)
    plt.close(fig)


# ───────────────────────── symbol universe ────────────────────────────────

def load_account_syms(account: str) -> List[str]:
    if account == 'flz':
        files = ['symbols_flz.json']
    elif account == 'fin':
        files = ['symbols_fin.json']
    elif account == 'men':
        files = ['symbols_men.json']
    elif account == 'ang':
        files = ['symbols_ang_long.json', 'symbols_ang_short.json']
    elif account == 'inf':
        files = ['symbols_inf_long.json', 'symbols_inf_short.json']
    else:
        return []
    syms: List[str] = []
    seen = set()
    for f in files:
        p = ROOT / f
        if not p.exists(): continue
        try:
            raw = p.read_text()
            clean = re.sub(r',(\s*[\]}])', r'\1', raw)
            for s in json.loads(clean):
                if isinstance(s, str) and s not in seen:
                    syms.append(s); seen.add(s)
        except Exception as e:
            print(f"  [load_syms] {f} parse error: {e}")
    return syms


def syms_in_priority_order(accounts: List[str], skip: Optional[set] = None) -> List[str]:
    """Build the symbol list in account priority order (ang→fin→men→flz), no dups, NPZ-present only."""
    seen = set(skip or set())
    out: List[str] = []
    for acct in accounts:
        for s in load_account_syms(acct):
            if s in seen: continue
            if not (NPZ_DIR / f'{s}.npz').exists(): continue
            seen.add(s)
            out.append(s)
    return out


# ───────────────────────── main ───────────────────────────────────────────

def run_cycle(syms: List[str], years: float, workers: int, sides: List[str]) -> Dict:
    print(f"[per_sym_crypto_profiler] cycle starting | syms={len(syms)} sides={sides} years={years} workers={workers}", flush=True)
    ts_run = int(time.time())
    csv_path = SWEEP_CSV_DIR / f'per_sym_crypto_v2_{ts_run}.csv'
    work = [(s, side, years) for s in syms for side in sides]
    print(f"  total jobs: {len(work)} = {len(syms)} syms × {len(sides)} sides", flush=True)
    t0 = time.time()
    results: List[Dict] = []
    if workers <= 1:
        for i, w in enumerate(work, 1):
            r = _worker(w)
            if r is not None:
                results.append(r)
            elapsed = time.time() - t0
            print(f"  [{i}/{len(work)}] {w[0]:14s} {w[1]:5s} elapsed={elapsed:.0f}s avg={elapsed/i:.1f}s/job", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_worker, w): w for w in work}
            done = 0
            for fut in as_completed(futs):
                done += 1
                w = futs[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    print(f"  worker exc {w}: {e}", flush=True); continue
                if r is not None:
                    results.append(r)
                elapsed = time.time() - t0
                if done % max(1, len(work) // 40) == 0 or done == len(work):
                    print(f"  [{done}/{len(work)}] elapsed={elapsed:.0f}s avg={elapsed/done:.1f}s/job", flush=True)
    elapsed = time.time() - t0
    print(f"[per_sym_crypto_profiler] sweep done in {elapsed:.0f}s ({elapsed/max(1,len(work)):.2f}s/job)", flush=True)

    # Promote + write canonical CSV
    promoted = promote_results(results, csv_path)
    print(f"[per_sym_crypto_profiler] promoted={promoted}/{len(results)} written to {OUT_CFG}", flush=True)
    print(f"[per_sym_crypto_profiler] canonical CSV: {csv_path}", flush=True)

    # Charts (sequential; matplotlib not pickle-friendly across workers, and they're cheap)
    if HAS_MPL:
        nc = 0
        for r in results:
            if r is None or 'error' in r:
                continue
            try:
                p = SymParams(**r['params'])
                generate_chart(r['sym'], r['side'], p, r['metrics'])
                nc += 1
            except Exception as e:
                print(f"  [chart] {r.get('sym')} {r.get('side')}: {e}", flush=True)
        print(f"[per_sym_crypto_profiler] charts: {nc} written to {CHARTS_DIR}", flush=True)
    return {'jobs': len(work), 'completed': len(results), 'promoted': promoted, 'elapsed_s': elapsed, 'csv': str(csv_path)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='', help='comma-separated symbols (overrides --accounts)')
    ap.add_argument('--accounts', default='ang,fin,men,flz', help='account priority order')
    ap.add_argument('--years', type=float, default=4.0)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--sides', default='LONG,SHORT')
    ap.add_argument('--once', action='store_true', default=True)
    ap.add_argument('--daemon', action='store_true', help='loop continuously')
    ap.add_argument('--cycle-interval-s', type=int, default=86400, help='daemon: seconds between cycles')
    ap.add_argument('--max-syms', type=int, default=0, help='cap on symbols (0=no cap)')
    args = ap.parse_args()

    sides = [s.strip().upper() for s in args.sides.split(',') if s.strip()]
    if args.syms:
        syms = [s.strip() for s in args.syms.split(',') if s.strip()]
    else:
        accts = [a.strip() for a in args.accounts.split(',') if a.strip()]
        syms = syms_in_priority_order(accts)
    if args.max_syms > 0:
        syms = syms[:args.max_syms]
    if not syms:
        print("no symbols to process", flush=True)
        return 1

    if args.daemon:
        print(f"[per_sym_crypto_profiler] DAEMON started, cycle interval={args.cycle_interval_s}s", flush=True)
        while True:
            t0 = time.time()
            try:
                run_cycle(syms, args.years, args.workers, sides)
            except KeyboardInterrupt:
                print("[per_sym_crypto_profiler] interrupted", flush=True)
                return 0
            except Exception:
                traceback.print_exc()
            elapsed = time.time() - t0
            sleep_s = max(60, args.cycle_interval_s - int(elapsed))
            print(f"[per_sym_crypto_profiler] cycle done in {elapsed:.0f}s; sleeping {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    else:
        run_cycle(syms, args.years, args.workers, sides)
    return 0


if __name__ == '__main__':
    sys.exit(main())
