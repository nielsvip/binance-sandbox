#!/usr/bin/env python3
"""
diagnose_exit_quality.py — Analyze trade exit quality and reentry behavior from tradier NPZ data.

For each trade:
  - Was it closed near the top (peak lookback window after exit)?
  - How much profit was left on the table?
  - Did price continue rallying after exit?
  - If rally continued: did a reentry happen within N bars?
  - What was the exit reason (WT signal, DC recovery, NOLOSS block)?

Usage:
    python3 diagnose_exit_quality.py [--symbols AAPL,MSFT,...] [--max-symbols 20] [--lookahead 24]
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from v8_quick_engine import QuickConfig, compute_entry_signals, compute_exit_signals, _safe, _safeb

TRADIER_NPZ_DIR = BASE_PATH / "backtest_v4_tradier" / "indicators"


def load_npz(symbol: str):
    p = TRADIER_NPZ_DIR / f"{symbol}.npz"
    if not p.exists():
        return None
    return dict(np.load(str(p), allow_pickle=False))


def diagnose_symbol(sym: str, npz: dict, cfg: QuickConfig, lookahead: int = 24):
    """
    Returns list of trade dicts with exit quality metrics.
    lookahead: bars after exit to look for peak / reentry
    """
    ltf = getattr(cfg, 'LTF', '5m')
    close_key = f'close_{ltf}'
    close = npz.get(close_key)
    if close is None or len(close) < 100:
        return []
    n = len(close)
    close = close.astype(np.float64)

    _dc_tf_map = {'dc_4h': ('dc_high_4h', 'dc_low_4h'), 'dc_1h': ('dc_high_1h', 'dc_low_1h'),
                  'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h')}
    _dc_hk, _dc_lk = _dc_tf_map.get(getattr(cfg, 'DC_RECOVERY_EXIT_TF', 'bb_1h'), ('bb_upper_1h', 'bb_lower_1h'))
    dc_high = _safe(npz, _dc_hk, n)
    dc_low = _safe(npz, _dc_lk, n)

    cooldown = cfg.COOLDOWN_BARS
    min_hold = cfg.MIN_HOLD_BARS
    min_gap = int(getattr(cfg, 'REENTRY_MIN_GAP_BARS', 0) or 0)

    trades = []

    for is_long in [True, False]:
        entry_sig = compute_entry_signals(npz, n, is_long, cfg)
        exit_sig = compute_exit_signals(npz, n, is_long, cfg)

        in_pos = False
        ep = 0.0
        eb = 0
        cd = 0

        for i in range(n):
            if cd > 0:
                cd -= 1
                continue
            px = close[i]
            if px <= 0:
                continue

            if not in_pos and entry_sig[i]:
                in_pos = True
                ep = px
                eb = i
                continue

            if in_pos:
                live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                hold_bars = i - eb

                # DC Recovery exit
                dc_exit = False
                if cfg.DC_RECOVERY_EXIT_ENABLED and cfg.NOLOSS_ENABLED and hold_bars >= min_hold and live_pnl < 0:
                    if is_long:
                        dc_exit = ep > dc_high[i] and dc_high[i] > 0
                    else:
                        dc_exit = ep < dc_low[i] and dc_low[i] > 0
                    if dc_exit:
                        _record_trade(trades, sym, is_long, eb, i, ep, px, live_pnl, 'DC_RECOVERY', close, n, lookahead, entry_sig, exit_sig, cfg)
                        in_pos = False
                        cd = max(cooldown, min_gap)
                        continue

                # WT exit signal
                if hold_bars >= min_hold and exit_sig[i]:
                    pnl = live_pnl
                    # NOLOSS block?
                    if cfg.NOLOSS_ENABLED and pnl < 0:
                        stranded = False
                        if cfg.DC_RECOVERY_EXIT_ENABLED:
                            if is_long:
                                stranded = ep > dc_high[i] and dc_high[i] > 0
                            else:
                                stranded = ep < dc_low[i] and dc_low[i] > 0
                        if not stranded:
                            # NOLOSS blocked — record as blocked exit, don't close
                            trades.append({
                                'sym': sym, 'side': 'LONG' if is_long else 'SHORT',
                                'entry_bar': int(eb), 'exit_bar': int(i),
                                'hold_bars': int(hold_bars),
                                'entry_px': float(ep), 'exit_px': float(px),
                                'exit_pnl_pct': float(pnl),
                                'exit_reason': 'NOLOSS_BLOCKED',
                                'peak_after_exit_pct': None,
                                'trough_after_exit_pct': None,
                                'profit_left_pct': None,
                                'reentry_bars_after': None,
                                'rally_continued': None,
                            })
                            continue
                    _record_trade(trades, sym, is_long, eb, i, ep, px, pnl, 'WT_SIGNAL', close, n, lookahead, entry_sig, exit_sig, cfg)
                    in_pos = False
                    cd = max(cooldown, min_gap)

        # Mark-to-market open position
        if in_pos and close[n - 1] > 0 and ep > 0:
            px = close[n - 1]
            pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
            trades.append({
                'sym': sym, 'side': 'LONG' if is_long else 'SHORT',
                'entry_bar': int(eb), 'exit_bar': int(n - 1),
                'hold_bars': int(n - 1 - eb),
                'entry_px': float(ep), 'exit_px': float(px),
                'exit_pnl_pct': float(pnl),
                'exit_reason': 'OPEN_AT_END',
                'peak_after_exit_pct': None, 'trough_after_exit_pct': None,
                'profit_left_pct': None, 'reentry_bars_after': None,
                'rally_continued': None,
            })

    return trades


def _record_trade(trades, sym, is_long, eb, i, ep, px, pnl, reason, close, n, lookahead, entry_sig, exit_sig, cfg):
    # Look ahead N bars for peak/trough and next reentry
    end = min(n, i + 1 + lookahead)
    future_prices = close[i + 1:end] if i + 1 < n else np.array([])

    if len(future_prices) > 0:
        if is_long:
            peak_px = float(np.max(future_prices))
            trough_px = float(np.min(future_prices))
            peak_pct = (peak_px - px) / px * 100
            trough_pct = (trough_px - px) / px * 100
            # profit "left on table" = max gain after exit from entry price
            profit_left = (peak_px - ep) / ep * 100 - pnl
        else:
            peak_px = float(np.min(future_prices))
            trough_px = float(np.max(future_prices))
            peak_pct = (px - peak_px) / px * 100   # how much more it fell
            trough_pct = (px - trough_px) / px * 100  # how much it reversed
            profit_left = (ep - peak_px) / ep * 100 - pnl
        rally_continued = peak_pct > 0.5  # price moved >0.5% more in our favour after exit
    else:
        peak_pct = trough_pct = profit_left = 0.0
        rally_continued = False

    # Find next reentry within lookahead bars
    reentry_bars = None
    cooldown_local = max(cfg.COOLDOWN_BARS, int(getattr(cfg, 'REENTRY_MIN_GAP_BARS', 0) or 0))
    search_start = i + cooldown_local + 1
    search_end = min(n, i + 1 + lookahead)
    for j in range(search_start, search_end):
        if entry_sig[j]:
            reentry_bars = j - i
            break

    trades.append({
        'sym': sym,
        'side': 'LONG' if is_long else 'SHORT',
        'entry_bar': int(eb),
        'exit_bar': int(i),
        'hold_bars': int(i - eb),
        'entry_px': float(ep),
        'exit_px': float(px),
        'exit_pnl_pct': float(pnl),
        'exit_reason': reason,
        'peak_after_exit_pct': float(peak_pct),
        'trough_after_exit_pct': float(trough_pct),
        'profit_left_pct': float(profit_left),
        'reentry_bars_after': reentry_bars,
        'rally_continued': bool(rally_continued),
    })


def print_summary(trades: list, lookahead: int):
    if not trades:
        print("No trades found.")
        return

    closed = [t for t in trades if t['exit_reason'] not in ('OPEN_AT_END',)]
    noloss_blocked = [t for t in trades if t['exit_reason'] == 'NOLOSS_BLOCKED']
    dc_exits = [t for t in trades if t['exit_reason'] == 'DC_RECOVERY']
    wt_exits = [t for t in trades if t['exit_reason'] == 'WT_SIGNAL']

    def avg(lst, key):
        vals = [t[key] for t in lst if t.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    def pct_true(lst, key):
        vals = [t[key] for t in lst if t.get(key) is not None]
        return 100 * sum(1 for v in vals if v) / len(vals) if vals else 0.0

    print(f"\n{'=' * 70}")
    print(f"EXIT QUALITY DIAGNOSTIC  ({len(trades)} total events, lookahead={lookahead} bars)")
    print(f"{'=' * 70}")
    print(f"  Closed trades:          {len(closed)}")
    print(f"  WT_SIGNAL exits:        {len(wt_exits)}")
    print(f"  DC_RECOVERY exits:      {len(dc_exits)}")
    print(f"  NOLOSS_BLOCKED events:  {len(noloss_blocked)}")
    print()

    for label, subset in [("WT_SIGNAL", wt_exits), ("DC_RECOVERY", dc_exits)]:
        if not subset:
            continue
        winners = [t for t in subset if t['exit_pnl_pct'] > 0]
        losers = [t for t in subset if t['exit_pnl_pct'] <= 0]
        rallied = [t for t in subset if t.get('rally_continued')]
        rallied_no_reentry = [t for t in rallied if t.get('reentry_bars_after') is None]
        print(f"  [{label}]  n={len(subset)}  winners={len(winners)}  losers={len(losers)}")
        print(f"    avg exit pnl:         {avg(subset, 'exit_pnl_pct'):+.2f}%")
        print(f"    avg peak after exit:  {avg(subset, 'peak_after_exit_pct'):+.2f}%  (favourable move after we closed)")
        print(f"    avg profit left:      {avg(subset, 'profit_left_pct'):+.2f}%  (gain we missed vs stay-in)")
        print(f"    rally continued:      {pct_true(subset, 'rally_continued'):.0f}% of exits  (>0.5% further)")
        print(f"    re-entered:           {100*(len(subset)-len(rallied_no_reentry)-sum(1 for t in subset if not t.get('rally_continued') and t.get('reentry_bars_after') is None)) / len(subset):.0f}%  within {lookahead} bars")
        if rallied:
            print(f"    rallied but NO reentry: {len(rallied_no_reentry)}/{len(rallied)} ({100*len(rallied_no_reentry)/len(rallied):.0f}%)  ← MISSED REENTRIES")
            print(f"    avg reentry gap (when reentered): {avg([t for t in rallied if t.get('reentry_bars_after')], 'reentry_bars_after'):.1f} bars")
        if winners:
            print(f"    winner avg peak after:  {avg(winners, 'peak_after_exit_pct'):+.2f}%  (exited winners too early?)")
        if losers:
            print(f"    loser avg peak after:   {avg(losers, 'peak_after_exit_pct'):+.2f}%  (DC recovery working?)")
        print()

    # NOLOSS_BLOCKED summary
    if noloss_blocked:
        print(f"  [NOLOSS_BLOCKED]  n={len(noloss_blocked)}  (positions that couldn't exit at a loss)")
        print(f"    avg pnl when blocked:  {avg(noloss_blocked, 'exit_pnl_pct'):+.2f}%")
        print(f"    avg hold so far:       {avg(noloss_blocked, 'hold_bars'):.0f} bars")
        print()

    # Worst losers — show detail
    print(f"  TOP 15 WORST PROFIT-LEFT-ON-TABLE (wt exits only):")
    worst = sorted([t for t in wt_exits if t.get('profit_left_pct') is not None], key=lambda t: -t['profit_left_pct'])[:15]
    print(f"  {'sym':6} {'side':5} {'hold':5} {'exit%':7} {'peak_after':10} {'left_pct':9} {'rallied':8} {'reentry_bars':12} {'reason':14}")
    for t in worst:
        re = str(t['reentry_bars_after']) if t['reentry_bars_after'] else '-'
        print(f"  {t['sym']:6} {t['side']:5} {t['hold_bars']:5} {t['exit_pnl_pct']:+.2f}%  {t['peak_after_exit_pct']:+.2f}%     {t['profit_left_pct']:+.2f}%   {'Y' if t['rally_continued'] else 'N':8} {re:12} {t['exit_reason']:14}")

    # Per-symbol breakdown
    print(f"\n  PER-SYMBOL EXIT QUALITY (WT exits only):")
    syms = sorted(set(t['sym'] for t in wt_exits))
    print(f"  {'sym':6} {'n':4} {'avg_exit%':9} {'avg_peak%':9} {'avg_left%':9} {'rally%':7} {'reentry%':9}")
    for sym in syms:
        s = [t for t in wt_exits if t['sym'] == sym]
        if not s:
            continue
        rallied_s = [t for t in s if t.get('rally_continued')]
        reentered_s = [t for t in s if t.get('reentry_bars_after') is not None]
        print(f"  {sym:6} {len(s):4} {avg(s,'exit_pnl_pct'):+.2f}%    {avg(s,'peak_after_exit_pct'):+.2f}%    {avg(s,'profit_left_pct'):+.2f}%   {100*len(rallied_s)/len(s):.0f}%    {100*len(reentered_s)/len(s):.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='', help='Comma-separated symbols, default=all')
    ap.add_argument('--max-symbols', type=int, default=30)
    ap.add_argument('--lookahead', type=int, default=24, help='Bars to look ahead after exit (default 24 = 2h at 5m)')
    ap.add_argument('--mode', default='tradier')
    args = ap.parse_args()

    cfg = QuickConfig()
    cfg.apply_tradier_defaults()
    # Use best-known config from sweep
    cfg.DC_RECOVERY_EXIT_ENABLED = True
    cfg.AUGMENT_WT_D_BOUNCE_ENABLED = True
    cfg.AUGMENT_WT_D_MULTIPLIER = 4.0
    cfg.AUGMENT_WT_D_REQUIRE_HIGHER_WT = False
    cfg.AUGMENT_WT_D_REQUIRE_HIGHER_PRICE = False
    cfg.MIN_HOLD_BARS = 40
    cfg.CT_WT_VELOCITY_1H_MIN = 2.0

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    else:
        symbols = sorted(p.stem for p in TRADIER_NPZ_DIR.glob('*.npz'))[:args.max_symbols]

    print(f"Diagnosing {len(symbols)} symbols — lookahead={args.lookahead} bars (~{args.lookahead*5}min at 5m)")
    all_trades = []
    for sym in symbols:
        npz = load_npz(sym)
        if npz is None:
            continue
        trades = diagnose_symbol(sym, npz, cfg, lookahead=args.lookahead)
        all_trades.extend(trades)

    print_summary(all_trades, args.lookahead)

    # Save full trade log
    out = BASE_PATH / 'data' / 'exit_quality_report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump(all_trades, f, indent=2)
    print(f"\n  Full trade log: {out}")


if __name__ == '__main__':
    main()
