#!/usr/bin/env python3
"""Per-symbol Sharpe breakdown for tradier — identify top-tier stocks."""
import sys, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, compute_entry_signals, compute_exit_signals, _safe, FAST_SYMBOLS_TRADIER

stores = load_npz('tradier', FAST_SYMBOLS_TRADIER.split(","), '2024-01-01')
print(f"Loaded {len(stores)} symbols")

cfg = QuickConfig()
cfg.apply_tradier_defaults()
cfg.STRENGTH_FILTER_ENABLED = True
cfg.STRENGTH_MIN_SCORE = 5.0
cfg.HTF_MIN_ALIGNED = 1
cfg.D_TREND_REQUIRED = True
cfg.MIN_HOLD_BARS = 10
cfg.WT_EXIT_MIN_TFS = 2
cfg.COOLDOWN_BARS = 3
cfg.PROFIT_TARGET_ENABLED = True
cfg.PROFIT_TARGET_PCT = 1.6

per_sym_sharpes = []
per_sym_wrs = []
per_sym_avgs = []
per_sym_trades = []
rows = []
for sym, npz in stores.items():
    ts = npz.get('timestamps', np.array([]))
    n = len(ts)
    if n < 100: continue
    close = _safe(npz, 'close_3m', n)
    if close.sum() == 0: close = _safe(npz, 'close_5m', n)
    sym_pnl = []
    for is_long in [True, False]:
        entry = compute_entry_signals(npz, n, is_long, cfg)
        exit_sig = compute_exit_signals(npz, n, is_long, cfg)
        in_pos = False; ep = 0.0; eb = 0; cd = 0
        pt_enabled = cfg.PROFIT_TARGET_ENABLED
        pt_pct = cfg.PROFIT_TARGET_PCT
        for i in range(n):
            if cd > 0: cd -= 1; continue
            px = close[i]
            if px <= 0: continue
            if not in_pos and entry[i]:
                in_pos = True; ep = px; eb = i
                continue
            if in_pos:
                live = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                if pt_enabled and live >= pt_pct:
                    sym_pnl.append(live); in_pos = False; cd = 3; continue
            if in_pos and exit_sig[i] and (i - eb) >= cfg.MIN_HOLD_BARS:
                pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                sym_pnl.append(pnl)
                in_pos = False; cd = 3
    if len(sym_pnl) >= 3:
        p = np.array(sym_pnl)
        m, s = p.mean(), p.std()
        sharpe = m / s if s > 0 else 0
        wr = (p > 0).mean() * 100
        per_sym_sharpes.append(sharpe)
        per_sym_wrs.append(wr)
        per_sym_avgs.append(m)
        per_sym_trades.append(len(p))
        rows.append((sym, sharpe, len(p), wr, m))

rows.sort(key=lambda x: x[1], reverse=True)
print(f"\n{'SYMBOL':<10} {'SHARPE':>8} {'TRADES':>8} {'WR':>6} {'AVG%':>8}")
for sym, sharpe, n, wr, m in rows:
    print(f"{sym:<10} {sharpe:>8.4f} {n:>8} {wr:>5.1f}% {m:>7.3f}%")

if per_sym_sharpes:
    print(f"\n=== AVG ACROSS {len(per_sym_sharpes)} SYMBOLS ===")
    print(f"  Avg Sharpe:    {np.mean(per_sym_sharpes):.4f}")
    print(f"  Avg WR:        {np.mean(per_sym_wrs):.1f}%")
    print(f"  Avg PnL/trade: {np.mean(per_sym_avgs):.3f}%")
    print(f"  Total trades:  {sum(per_sym_trades)}")
    print(f"\n=== TOP-5 (per-Sharpe) ===")
    top5 = [r[0] for r in rows[:5]]
    print(f"  {top5}")
