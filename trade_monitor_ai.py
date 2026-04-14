#!/usr/bin/env python3
"""
Trade Monitor v3 — persistent service that analyzes trades every 10 minutes.
Pure rule-based analysis (no API calls). Writes actionable recommendations
to TRADE_MONITOR_LIVE.md. Runs as systemd service.
"""
import os, sys, json, time, logging, math
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict

sys.path.insert(0, '/home/niels/binance')

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s [TRADE_MON] %(message)s", handlers=[logging.FileHandler("/home/niels/logs/trade_monitor_ai.log"), logging.StreamHandler()])
logger = logging.getLogger("trade_monitor")

BASE = '/home/niels/binance'
OUT_FILE = f'{BASE}/TRADE_MONITOR_LIVE.md'
CRYPTO_ACCOUNTS = ['ang', 'inf', 'flz', 'men', 'fin']
STOCK_ACCOUNTS = ['trb']
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + STOCK_ACCOUNTS
CYCLE_SECONDS = 600
STRICT_NO_LOSS = ['ang', 'inf', 'men', 'fin']
SCALP_ACCOUNTS = ['ang', 'men', 'flz']


def get_recent_decisions(minutes=12):
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(minutes=minutes)
    today_str = now_utc.strftime('%Y%m%d')
    yesterday_str = (now_utc - timedelta(days=1)).strftime('%Y%m%d')
    all_decisions = []
    for acct in ALL_ACCOUNTS:
        for date_str in [yesterday_str, today_str]:
            fpath = f'{BASE}/data/decisions/decisions_{acct}_{date_str}.jsonl'
            if not os.path.exists(fpath):
                continue
            try:
                with open(fpath, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                            ts_str = d.get('timestamp', '')
                            if ts_str:
                                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                                if ts >= cutoff:
                                    d['_account'] = acct
                                    d['_ts'] = ts
                                    all_decisions.append(d)
                        except (json.JSONDecodeError, ValueError):
                            continue
            except Exception as e:
                logger.error(f"Error reading {fpath}: {e}")
    return all_decisions


def get_todays_decisions():
    """Get ALL decisions from today for pattern detection."""
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime('%Y%m%d')
    counts = defaultdict(lambda: defaultdict(int))
    for acct in ALL_ACCOUNTS:
        fpath = f'{BASE}/data/decisions/decisions_{acct}_{today_str}.jsonl'
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        pk = d.get('position_key', '')
                        action = d.get('action', '')
                        counts[acct][(pk, action)] += 1
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            continue
    return counts


def get_position_snapshots():
    snapshots = {}
    for acct in CRYPTO_ACCOUNTS:
        acct_data = {'longs_active': 0, 'shorts_active': 0, 'total_upnl': 0.0, 'winners': 0, 'losers': 0, 'big_winners': [], 'big_losers': [], 'unaugmented_winners': []}
        for side_file, side_label in [('long_positions.json', 'LONG'), ('short_positions.json', 'SHORT')]:
            fpath = f'{BASE}/{acct}/{side_file}'
            try:
                with open(fpath) as f:
                    positions = json.load(f)
                for key, p in positions.items():
                    amt = abs(float(p.get("positionAmt") or 0))
                    if amt == 0:
                        continue
                    upnl = float(p.get('unrealized_pnl') or p.get('unrealizedProfit') or 0)
                    pct = float(p.get('gain') or p.get('percentage') or 0)
                    value = abs(amt * float(p.get('mark_price') or p.get('markPrice') or p.get('entryPrice') or 0))
                    if side_label == 'LONG':
                        acct_data['longs_active'] += 1
                    else:
                        acct_data['shorts_active'] += 1
                    acct_data['total_upnl'] += upnl
                    if upnl > 0:
                        acct_data['winners'] += 1
                    elif upnl < 0:
                        acct_data['losers'] += 1
                    if pct > 3.0:
                        acct_data['big_winners'].append({'key': key, 'pct': pct, 'upnl': upnl, 'value': value})
                    if pct > 3.0 and value < 15:
                        acct_data['unaugmented_winners'].append({'key': key, 'pct': pct, 'value': value})
                    if pct < -5.0:
                        acct_data['big_losers'].append({'key': key, 'pct': pct, 'upnl': upnl})
            except Exception as e:
                logger.error(f"Error reading {fpath}: {e}")
        snapshots[acct] = acct_data
    return snapshots


def analyze(decisions, snapshots, daily_counts):
    """Pure rule-based analysis — returns markdown string."""
    now = datetime.now(timezone.utc)
    issues = []
    recommendations = []
    notable = []
    # Per-account analysis
    acct_data = defaultdict(lambda: {'actions': Counter(), 'reasons': Counter(), 'symbols': Counter(), 'churn': defaultdict(list), 'bad_entries': [], 'failed_hedges': 0})
    for d in decisions:
        acct = d.get('_account', 'unknown')
        action = d.get('action', 'UNKNOWN')
        reason = d.get('reason', d.get('reason_text', ''))
        pk = d.get('position_key', '')
        snap = d.get('snapshot', d.get('indicators', {}))
        acct_data[acct]['actions'][action] += 1
        acct_data[acct]['reasons'][reason[:60]] += 1
        acct_data[acct]['symbols'][pk] += 1
        acct_data[acct]['churn'][pk].append((action, d.get('_ts', now)))
        # Bad entry detection: LONG at exhausted stoch, SHORT at oversold
        if action in ('QUICK_OPEN', 'OPEN', 'REENTRY'):
            k15 = float(snap.get('k_15m', snap.get('stoch_k_15m', 50)))
            is_long = pk.endswith('_LONG')
            if is_long and k15 > 85:
                acct_data[acct]['bad_entries'].append(f"{pk} LONG@k15={k15:.0f}")
            elif not is_long and k15 < 15:
                acct_data[acct]['bad_entries'].append(f"{pk} SHORT@k15={k15:.0f}")
        if 'HEDGE_NO_CANDIDATE' in reason:
            acct_data[acct]['failed_hedges'] += 1
    # === ISSUE DETECTION ===
    # 1. Churn detection (same symbol, >4 decisions in 10 min)
    for acct, ad in acct_data.items():
        churn_syms = []
        for pk, entries in ad['churn'].items():
            if len(entries) > 4:
                actions_str = Counter(a for a, _ in entries)
                churn_syms.append(f"`{pk}` ({len(entries)}x: {dict(actions_str)})")
        if churn_syms:
            issues.append(f"**CHURN [{acct}]**: {', '.join(churn_syms[:3])} — repeated open/close/reduce burning fees")
    # 2. Emergency hedge failures
    for acct, ad in acct_data.items():
        if ad['failed_hedges'] > 2:
            issues.append(f"**HEDGE FAILURE [{acct}]**: {ad['failed_hedges']}x HEDGE_NO_CANDIDATE_EMERGENCY_CLOSE in 10 min — hedge candidate pool exhausted")
            recommendations.append(f"[{acct}] Expand hedge candidate pool or reduce hedge trigger sensitivity")
    # 3. Bad entries (exhausted stochastics)
    for acct, ad in acct_data.items():
        if ad['bad_entries']:
            issues.append(f"**BAD ENTRY [{acct}]**: {'; '.join(ad['bad_entries'][:3])} — entering at exhausted stochastics")
    # 4. Excessive IN_GAIN_TREND_EXIT
    for acct, ad in acct_data.items():
        ige_count = sum(1 for r in ad['reasons'] if 'IN_GAIN_TREND_EXIT' in r)
        total_count = sum(ad['reasons'].values())
        if ige_count > 5 and total_count > 0:
            pct = ige_count / total_count * 100
            issues.append(f"**EXIT SPAM [{acct}]**: {ige_count}x IN_GAIN_TREND_EXIT ({pct:.0f}% of decisions) — exit logic too aggressive on small gains")
    # 5. Sentiment cuts too aggressive
    for acct, ad in acct_data.items():
        sent_cuts = sum(v for r, v in ad['reasons'].items() if 'SENTIMENT_CUT' in r)
        if sent_cuts > 5:
            issues.append(f"**SENTIMENT CHOP [{acct}]**: {sent_cuts}x sentiment cuts in 10 min — sentiment model too reactive")
    # 6. Unaugmented winners (>3% gain, small position)
    for acct, snap in snapshots.items():
        if snap['unaugmented_winners']:
            syms = [f"`{w['key']}` ({w['pct']:.1f}%, val=${w['value']:.0f})" for w in snap['unaugmented_winners'][:5]]
            issues.append(f"**MISSED AUGMENT [{acct}]**: {'; '.join(syms)} — >3% gain but position too small (augmentation failing?)")
            recommendations.append(f"[{acct}] Check augmentation pipeline for these winners — they should have been scaled into")
    # 7. L/S ratio imbalance
    for acct, snap in snapshots.items():
        la, sa = snap['longs_active'], snap['shorts_active']
        if la > 0 and sa > 0:
            ratio = la / sa if sa > 0 else 999
            if ratio > 2.0:
                issues.append(f"**L/S IMBALANCE [{acct}]**: {la}L/{sa}S (ratio {ratio:.1f}) — heavily long-biased")
            elif ratio < 0.5:
                issues.append(f"**L/S IMBALANCE [{acct}]**: {la}L/{sa}S (ratio {ratio:.1f}) — heavily short-biased")
    # 8. Daily churn check — symbols with >50 total decisions today
    for acct, pk_counts in daily_counts.items():
        symbol_totals = defaultdict(int)
        for (pk, action), count in pk_counts.items():
            symbol_totals[pk] += count
        mega_churn = [(pk, cnt) for pk, cnt in symbol_totals.items() if cnt > 50]
        if mega_churn:
            mega_churn.sort(key=lambda x: -x[1])
            syms = [f"`{pk}` ({cnt}x)" for pk, cnt in mega_churn[:3]]
            issues.append(f"**DAILY MEGA-CHURN [{acct}]**: {', '.join(syms)} — tug-of-war between augment/exit logic")
            recommendations.append(f"[{acct}] Add cooldown or mutual exclusion between HAIKU_AUGMENT and IN_GAIN_TREND_EXIT for these symbols")
    # 9. Fill failures (check from decisions: same symbol, repeated CLOSE with same reason)
    for acct, ad in acct_data.items():
        for pk, entries in ad['churn'].items():
            close_count = sum(1 for a, _ in entries if a == 'CLOSE')
            if close_count > 3:
                issues.append(f"**FILL FAILURE [{acct}]**: `{pk}` — {close_count}x CLOSE attempts in 10 min (orders not filling?)")
                break  # one per account is enough
    # === NOTABLE ===
    for acct, snap in snapshots.items():
        for w in snap['big_winners'][:2]:
            if w['pct'] > 8:
                notable.append(f"[{acct}] `{w['key']}` at **+{w['pct']:.1f}%** (${w['upnl']:.2f}) — strong winner")
        for l in snap['big_losers'][:2]:
            if l['pct'] < -10:
                notable.append(f"[{acct}] `{l['key']}` at **{l['pct']:.1f}%** (${l['upnl']:.2f}) — deep loss, holding per L/S hedge rule")
    # === BUILD REPORT ===
    lines = []
    # Account snapshots
    lines.append("### Account Snapshots")
    for acct in ALL_ACCOUNTS:
        if acct in snapshots:
            s = snapshots[acct]
            decisions_10m = sum(acct_data.get(acct, {}).get('actions', {}).values()) if acct in acct_data else 0
            lines.append(f"- **{acct}**: uPnL=${s['total_upnl']:.2f} | L={s['longs_active']} S={s['shorts_active']} | W/L={s['winners']}/{s['losers']} | {decisions_10m} decisions/10m")
        elif acct in acct_data:
            decisions_10m = sum(acct_data[acct]['actions'].values())
            top_action = acct_data[acct]['actions'].most_common(1)
            lines.append(f"- **{acct}** (stock): {decisions_10m} decisions/10m | top: {top_action[0][0] if top_action else 'none'}")
    if not issues and not notable:
        lines.append("")
        lines.append("All quiet — no issues detected.")
        return "\n".join(lines)
    if issues:
        lines.append("")
        lines.append("### Issues Detected")
        for issue in issues:
            lines.append(f"- {issue}")
    if recommendations:
        lines.append("")
        lines.append("### Recommendations")
        for rec in recommendations:
            lines.append(f"- {rec}")
    if notable:
        lines.append("")
        lines.append("### Notable")
        for n in notable:
            lines.append(f"- {n}")
    return "\n".join(lines)


def write_report(analysis, decisions_count):
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    header_exists = os.path.exists(OUT_FILE) and os.path.getsize(OUT_FILE) > 0
    with open(OUT_FILE, 'a') as f:
        if not header_exists:
            f.write("# Trade Monitor — Live Recommendations\n\n")
            f.write("Auto-generated by `trade_monitor_ai.py` every 10 minutes.\n\n")
        f.write(f"---\n## {now} ({decisions_count} decisions)\n\n")
        f.write(analysis)
        f.write("\n\n")
    logger.info(f"Report written ({decisions_count} decisions)")


def trim_report_file(max_reports=144):
    if not os.path.exists(OUT_FILE):
        return
    try:
        with open(OUT_FILE, 'r') as f:
            content = f.read()
        sections = content.split('\n---\n## ')
        if len(sections) <= max_reports + 1:
            return
        header = sections[0]
        kept = sections[-(max_reports):]
        with open(OUT_FILE, 'w') as f:
            f.write(header)
            for s in kept:
                f.write('\n---\n## ' + s)
        logger.info(f"Trimmed to {max_reports} entries")
    except Exception as e:
        logger.error(f"Trim error: {e}")


def analyze_blocked_trades():
    """Analyze blocked trades and compute what-if PnL at current prices."""
    import redis as _redis
    try:
        r = _redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.ping()
    except Exception:
        return "Redis unavailable for blocked trade analysis"
    blocked_dir = f'{BASE}/data/blocked_trades'
    if not os.path.exists(blocked_dir):
        return "No blocked trades data yet"
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime('%Y%m%d')
    lines = []
    for acct in ALL_ACCOUNTS:
        fpath = f'{blocked_dir}/blocked_{acct}_{today}.jsonl'
        if not os.path.exists(fpath):
            continue
        entries = []
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception:
            continue
        if not entries:
            continue
        missed_pnl = 0.0
        missed_count = 0
        details = []
        for e in entries[-50:]:
            symbol = e.get('symbol', '')
            block_price = float(e.get('price_at_block', 0))
            qty = float(e.get('qty', 0))
            pk = e.get('position_key', '')
            action = e.get('action', '')
            block_reason = e.get('block_reason', '')
            if block_price <= 0 or qty <= 0:
                continue
            try:
                raw = r.get(f'mark_price:{symbol}')
                if not raw:
                    continue
                d = json.loads(raw) if raw.startswith('{') else {'price': raw}
                current_price = float(d.get('price', raw))
            except Exception:
                continue
            is_long = pk.endswith('_LONG')
            if 'OPEN' in action or 'AUGMENT' in action:
                if is_long:
                    pnl_pct = ((current_price - block_price) / block_price) * 100
                else:
                    pnl_pct = ((block_price - current_price) / block_price) * 100
                pnl_usd = qty * block_price * pnl_pct / 100
                missed_pnl += pnl_usd
                missed_count += 1
                if abs(pnl_pct) > 1.0:
                    details.append(f"`{pk}` blocked@${block_price:.4f} now {pnl_pct:+.2f}% (${pnl_usd:+.2f}) [{block_reason}]")
        if missed_count > 0:
            lines.append(f"**{acct}**: {missed_count} blocked trades, missed PnL: ${missed_pnl:+.2f}")
            for d in details[:5]:
                lines.append(f"  - {d}")
    if not lines:
        return "No blocked trades today (or no price data available)"
    return "\n".join(lines)


def main():
    logger.info(f"Trade Monitor v3 started — cycle every {CYCLE_SECONDS}s, output: {OUT_FILE}")
    while True:
        try:
            cycle_start = time.time()
            decisions = get_recent_decisions(minutes=12)
            snapshots = get_position_snapshots()
            daily_counts = get_todays_decisions()
            if not decisions and not any(s.get('longs_active', 0) + s.get('shorts_active', 0) > 0 for s in snapshots.values()):
                logger.info("No activity — skipping")
                time.sleep(CYCLE_SECONDS)
                continue
            logger.info(f"Analyzing {len(decisions)} decisions across {len(set(d.get('_account','') for d in decisions))} accounts")
            analysis = analyze(decisions, snapshots, daily_counts)
            blocked_report = analyze_blocked_trades()
            if blocked_report and 'No blocked' not in blocked_report:
                analysis += chr(10) + chr(10) + '### Blocked Trades (What-If PnL)' + chr(10) + blocked_report
            write_report(analysis, len(decisions))
            trim_report_file()
            elapsed = time.time() - cycle_start
            logger.info(f"Cycle done in {elapsed:.1f}s")
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
        sleep_time = max(10, CYCLE_SECONDS - (time.time() - cycle_start))
        time.sleep(sleep_time)


if __name__ == '__main__':
    main()


# ---- BLOCKED TRADES PnL TRACKER ----
def analyze_blocked_trades():
    """Analyze blocked trades and compute what-if PnL at current prices."""
    import redis
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        r.ping()
    except Exception:
        return "Redis unavailable for blocked trade analysis"
    blocked_dir = f'{BASE}/data/blocked_trades'
    if not os.path.exists(blocked_dir):
        return "No blocked trades data yet"
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime('%Y%m%d')
    lines = []
    for acct in ALL_ACCOUNTS:
        fpath = f'{blocked_dir}/blocked_{acct}_{today}.jsonl'
        if not os.path.exists(fpath):
            continue
        entries = []
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception:
            continue
        if not entries:
            continue
        missed_pnl = 0.0
        missed_count = 0
        details = []
        for e in entries[-50:]:
            symbol = e.get('symbol', '')
            block_price = float(e.get('price_at_block', 0))
            qty = float(e.get('qty', 0))
            pk = e.get('position_key', '')
            action = e.get('action', '')
            block_reason = e.get('block_reason', '')
            if block_price <= 0 or qty <= 0:
                continue
            try:
                raw = r.get(f'mark_price:{symbol}')
                if not raw:
                    continue
                d = json.loads(raw) if raw.startswith('{') else {'price': raw}
                current_price = float(d.get('price', raw))
            except Exception:
                continue
            is_long = pk.endswith('_LONG')
            if 'OPEN' in action or 'AUGMENT' in action:
                if is_long:
                    pnl_pct = ((current_price - block_price) / block_price) * 100
                else:
                    pnl_pct = ((block_price - current_price) / block_price) * 100
                pnl_usd = qty * block_price * pnl_pct / 100
                missed_pnl += pnl_usd
                missed_count += 1
                if abs(pnl_pct) > 1.0:
                    details.append(f"`{pk}` blocked@${block_price:.4f} now {pnl_pct:+.2f}% (${pnl_usd:+.2f}) [{block_reason}]")
        if missed_count > 0:
            lines.append(f"**{acct}**: {missed_count} blocked trades, missed PnL: ${missed_pnl:+.2f}")
            for d in details[:5]:
                lines.append(f"  - {d}")
    if not lines:
        return "No blocked trades today (or no price data available)"
    return "\n".join(lines)
