#!/usr/bin/env python3
"""Daily performance report — crypto + stocks.
Reads decisions JSONL, position files, and logs to generate a comprehensive report.
Run: python daily_performance_report.py [--date YYYY-MM-DD]"""
import json, os, sys, glob, csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

BASE = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = BASE / "data" / "decisions"
REPORT_DIR = BASE / "data" / "daily_reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

def parse_date(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except: return None

def load_decisions(acct, date_str):
    path = DATA_DIR / f"decisions_{acct}_{date_str.replace('-','')}.jsonl"
    if not path.exists(): return []
    rows = []
    with open(path, errors='replace') as f:
        for line in f:
            try: rows.append(json.loads(line.strip()))
            except: pass
    return rows

def load_positions(acct):
    positions = {}
    for side in ['long', 'short']:
        path = BASE / acct / f"{side}_positions.json"
        if not path.exists(): continue
        with open(path) as f:
            d = json.load(f)
        for k, v in d.items():
            amt = abs(float(v.get('positionAmt', 0) or 0))
            if amt == 0: continue
            ep = float(v.get('entry_price', 0) or 0)
            mp = float(v.get('mark_price', 0) or 0)
            if ep <= 0 or mp <= 0: continue
            is_long = k.endswith('_LONG')
            pnl_pct = ((mp - ep) / ep * 100) if is_long else ((ep - mp) / ep * 100)
            val = amt * mp
            positions[k] = {'amt': amt, 'entry': ep, 'mark': mp, 'pnl_pct': pnl_pct, 'value': val, 'side': 'LONG' if is_long else 'SHORT'}
    return positions

def analyze_account(acct, date_str, decisions):
    opens = [d for d in decisions if d.get('action', '') in ('OPEN', 'QUICK_OPEN', 'AUGMENT', 'QUICK_AUGMENT', 'REENTRY', 'HAIKU_AUGMENT')]
    closes = [d for d in decisions if d.get('action', '') in ('CLOSE', 'QUICK_CLOSE', 'REDUCE', 'QUICK_REDUCE', 'NOW_REDUCE', 'STRONG_REDUCE')]
    holds = [d for d in decisions if 'HOLD' in d.get('action', '') or 'WAIT' in d.get('action', '') or 'HOLD' in d.get('reason', '')]
    # Categorize by reason
    reason_counts = defaultdict(int)
    for d in decisions:
        reason = d.get('reason', 'unknown')
        # Extract the main reason tag
        tag = reason.split('_')[0] if '_' in reason else reason
        if 'WT_' in reason: tag = 'WT_GATE'
        elif 'NOLOSS' in reason: tag = 'NOLOSS_HOLD'
        elif 'SENTIMENT' in reason: tag = 'SENTIMENT'
        elif 'CYCLE_TP' in reason: tag = 'CYCLE_TP'
        elif 'ACCOUNT_TP' in reason: tag = 'ACCOUNT_TP'
        elif 'HEDGE' in reason: tag = 'HEDGE'
        elif 'DC_BREAK' in reason: tag = 'DC_BREAK'
        elif 'STRUCT' in reason: tag = 'STRUCTURE'
        elif 'RATIO' in reason: tag = 'RATIO'
        reason_counts[tag] += 1
    # WT gate blocks
    wt_blocks = [d for d in decisions if 'WT_' in d.get('reason', '')]
    wt_passes = [d for d in opens if 'WT_FULL' in d.get('reason', '') or 'WT_ALIGNED' in d.get('reason', '') or 'bc102' in d.get('reason', '')]
    return {
        'total': len(decisions), 'opens': len(opens), 'closes': len(closes), 'holds': len(holds),
        'wt_blocks': len(wt_blocks), 'wt_passes': len(wt_passes),
        'reason_counts': dict(reason_counts),
        'open_details': opens[:10], 'close_details': closes[:5]
    }

def generate_report(date_str):
    report_lines = []
    r = report_lines.append
    r(f"{'='*100}")
    r(f"DAILY PERFORMANCE REPORT — {date_str}")
    r(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    r(f"{'='*100}")
    # CRYPTO
    crypto_accounts = ['ang', 'inf', 'flz', 'men', 'fin']
    r(f"\n{'─'*50}")
    r(f"CRYPTO ACCOUNTS")
    r(f"{'─'*50}")
    total_crypto_value = 0
    total_crypto_pnl = 0
    total_opens = 0
    total_closes = 0
    for acct in crypto_accounts:
        decisions = load_decisions(acct, date_str)
        positions = load_positions(acct)
        analysis = analyze_account(acct, date_str, decisions)
        long_val = sum(p['value'] for p in positions.values() if p['side'] == 'LONG')
        short_val = sum(p['value'] for p in positions.values() if p['side'] == 'SHORT')
        total_val = long_val + short_val
        total_pnl = sum(p['pnl_pct'] * p['value'] / 100 for p in positions.values())
        long_count = sum(1 for p in positions.values() if p['side'] == 'LONG')
        short_count = sum(1 for p in positions.values() if p['side'] == 'SHORT')
        winners = sum(1 for p in positions.values() if p['pnl_pct'] > 0)
        losers = sum(1 for p in positions.values() if p['pnl_pct'] < 0)
        total_crypto_value += total_val
        total_crypto_pnl += total_pnl
        total_opens += analysis['opens']
        total_closes += analysis['closes']
        ratio = long_val / short_val if short_val > 0 else 999
        r(f"\n  {acct.upper()}: {long_count}L/{short_count}S | ${total_val:.0f} (L${long_val:.0f}/S${short_val:.0f}) | Ratio {ratio:.2f} | PnL ${total_pnl:+.2f}")
        r(f"    Decisions: {analysis['total']} total | {analysis['opens']} opens | {analysis['closes']} closes | {analysis['holds']} holds")
        r(f"    WT Gate: {analysis['wt_blocks']} blocked | {analysis['wt_passes']} passed")
        r(f"    Winners: {winners} | Losers: {losers} | WR: {winners/(winners+losers)*100:.0f}%" if (winners+losers) > 0 else f"    Winners: {winners} | Losers: {losers}")
        # Top reasons
        top_reasons = sorted(analysis['reason_counts'].items(), key=lambda x: -x[1])[:5]
        r(f"    Top reasons: {', '.join(f'{k}={v}' for k,v in top_reasons)}")
        # Worst 3 positions
        worst = sorted(positions.items(), key=lambda x: x[1]['pnl_pct'])[:3]
        if worst:
            _w = ' | '.join(k.split(':')[1] + ': ' + format(p['pnl_pct'], '+.1f') + '% $' + format(p['value'], '.0f') for k,p in worst)
            r(f"    Worst: {_w}")
        # Best 3 positions
        best = sorted(positions.items(), key=lambda x: -x[1]['pnl_pct'])[:3]
        if best:
            _b = ' | '.join(k.split(':')[1] + ': ' + format(p['pnl_pct'], '+.1f') + '% $' + format(p['value'], '.0f') for k,p in best)
            r(f"    Best:  {_b}")
    r(f"\n  CRYPTO TOTAL: ${total_crypto_value:.0f} | PnL ${total_crypto_pnl:+.2f} | Opens {total_opens} | Closes {total_closes}")
    # TRADIER (stocks)
    r(f"\n{'─'*50}")
    r(f"STOCK ACCOUNTS (Tradier)")
    r(f"{'─'*50}")
    stock_accounts = ['trb', 'trc']
    for acct in stock_accounts:
        positions = load_positions(acct)
        if not positions:
            r(f"\n  {acct.upper()}: No active positions")
            continue
        long_val = sum(p['value'] for p in positions.values() if p['side'] == 'LONG')
        short_val = sum(p['value'] for p in positions.values() if p['side'] == 'SHORT')
        total_val = long_val + short_val
        total_pnl = sum(p['pnl_pct'] * p['value'] / 100 for p in positions.values())
        long_count = sum(1 for p in positions.values() if p['side'] == 'LONG')
        short_count = sum(1 for p in positions.values() if p['side'] == 'SHORT')
        winners = sum(1 for p in positions.values() if p['pnl_pct'] > 0)
        losers = sum(1 for p in positions.values() if p['pnl_pct'] < 0)
        r(f"\n  {acct.upper()}: {long_count}L/{short_count}S | ${total_val:.0f} (L${long_val:.0f}/S${short_val:.0f}) | PnL ${total_pnl:+.2f}")
        r(f"    Winners: {winners} | Losers: {losers} | WR: {winners/(winners+losers)*100:.0f}%" if (winners+losers) > 0 else f"    Winners: {winners} | Losers: {losers}")
        worst = sorted(positions.items(), key=lambda x: x[1]['pnl_pct'])[:3]
        if worst:
            _w = ' | '.join(k.split(':')[1] + ': ' + format(p['pnl_pct'], '+.1f') + '% $' + format(p['value'], '.0f') for k,p in worst)
            r(f"    Worst: {_w}")
        best = sorted(positions.items(), key=lambda x: -x[1]['pnl_pct'])[:3]
        if best:
            _b = ' | '.join(k.split(':')[1] + ': ' + format(p['pnl_pct'], '+.1f') + '% $' + format(p['value'], '.0f') for k,p in best)
            r(f"    Best:  {_b}")
    # CHANGES IMPACT
    r(f"\n{'─'*50}")
    r(f"SYSTEM CHANGES IMPACT")
    r(f"{'─'*50}")
    r(f"  Active changes: BACKTEST_CHANGE_100 (structure entry/exit), 101 (stoch 14/7/7), 102 (WT alignment gate)")
    r(f"  WT alignment gate: requires 4/5 TF direction + 2/5 WT structure + velocity + no divergence")
    r(f"  TFs checked: 3m, 15m, 1h, 4h, D (WT peaks/troughs + score + velocity + divergence + signal)")
    r(f"  Ratio limits: min={0.05} max={9.0} — ratio follows WT direction, doesn't fight it")
    r(f"  Hedge system: breathing_hedge_scan + EMERGENCY_OVERSIZE guard active")
    r(f"{'='*100}")
    return '\n'.join(report_lines)

def main():
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if len(sys.argv) > 1 and sys.argv[1] == '--date' and len(sys.argv) > 2:
        date_str = sys.argv[2]
    report = generate_report(date_str)
    print(report)
    # Save report
    report_path = REPORT_DIR / f"report_{date_str.replace('-','')}.txt"
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\nSaved to {report_path}")

if __name__ == '__main__':
    main()
