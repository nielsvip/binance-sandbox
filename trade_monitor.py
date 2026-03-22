#!/usr/bin/env python3
"""
Trade Monitor v2 — watches all account logs, captures market context + gain%/$,
classifies each trade as SMART or STUPID. Deduplicates triple-logged lines.
"""
import os, sys, time, json, re
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '/home/niels/binance')

try:
    import redis
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    r.ping()
    REDIS_OK = True
except Exception:
    REDIS_OK = False

MONITOR_HOURS = 10
LOG_DIR = '/home/niels/logs'
BASE = '/home/niels/binance'
OUT_FILE = f'{LOG_DIR}/trade_monitor_{datetime.now().strftime("%Y%m%d_%H%M")}.log'
CRYPTO_ACCOUNTS = ['ang', 'inf', 'flz', 'men', 'fin']
TRADIER_ACCOUNTS = ['trb', 'trc']

WATCH_FILES = {}
for acc in CRYPTO_ACCOUNTS:
    p = f'{LOG_DIR}/ez_manage_{acc}.log'
    if os.path.exists(p): WATCH_FILES[p] = f'crypto:{acc}'
for acc in TRADIER_ACCOUNTS:
    p = f'{LOG_DIR}/tradier_manage_{acc}.log'
    if os.path.exists(p): WATCH_FILES[p] = f'tradier:{acc}'

# Match lines with Reason: (the SUBMITTED line, has pos_key and reason)
RE_REASON = re.compile(r'\[TRADE\]\s+(\w+)\s+(OPEN|CLOSE|REDUCE|AUGMENT)\s+(BUY|SELL)\s+\|\s+Qty:\s*([\d.]+)\s+@\s*\$?([\d.]+)\s+\|\s+Status:\s*(\w+)\s+\|.*?Reason:\s*(.+?)(?:\s*\||\s*$)')
POS_KEY_RE = re.compile(r'(\w+:\w+_(?:LONG|SHORT))')
# Crypto format: Position Value before: X, Augment|Reduce by: Y, Gain: Z ... Reason: R
RE_CRYPTO = re.compile(r'(\w+:\w+_(?:LONG|SHORT)).*?Position Value before:\s*([\d.]+).*?(Augment|Reduce) by:\s*([\d.]+).*?Gain:\s*([\d.\-]+).*?Reason:\s*(.*?)(?:\s*\||\s*\$)')

def sf(v, d=0.0):
    try: return float(v)
    except Exception: return d

_pos_cache = {}
_pos_ts = 0

def load_positions():
    global _pos_cache, _pos_ts
    now = time.time()
    if now - _pos_ts < 15: return
    _pos_ts = now
    for acc in CRYPTO_ACCOUNTS + TRADIER_ACCOUNTS:
        for side in ['long', 'short']:
            fp = f'{BASE}/{acc}/{side}_positions.json'
            try:
                with open(fp) as f:
                    d = json.load(f)
                for k, v in d.items():
                    _pos_cache[k] = v
            except Exception: pass

def get_entry_price(pos_key):
    load_positions()
    return sf(_pos_cache.get(pos_key, {}).get('entry_price', 0))

_trad_prices = {}
_trad_prices_ts = 0

def get_tradier_price(symbol):
    global _trad_prices, _trad_prices_ts
    now = time.time()
    if now - _trad_prices_ts > 10:
        try:
            raw = r.get('tradier_prices_latest')
            if raw:
                d = json.loads(raw)
                _trad_prices = d.get('data', d) if isinstance(d, dict) else {}
                _trad_prices_ts = now
        except Exception: pass
    s = _trad_prices.get(symbol, {})
    return sf(s.get('price', 0)) if isinstance(s, dict) else sf(s)

_trad_ind = {}
_trad_ind_ts = 0

def get_tradier_indicators(symbol):
    global _trad_ind, _trad_ind_ts
    now = time.time()
    if now - _trad_ind_ts > 10:
        try:
            raw = r.get('tradier_indicators_latest')
            if raw:
                _trad_ind = json.loads(raw)
                _trad_ind_ts = now
        except Exception: pass
    return _trad_ind.get(symbol, {})

def get_crypto_indicators(symbol):
    if not REDIS_OK: return {}
    result = {}
    try:
        raw = r.get(f'hot_metrics:{symbol}')
        if raw:
            d = json.loads(raw)
            result['stoch_k_1m'] = d.get('k_1m', 50)
            result['stoch_d_1m'] = d.get('d_1m', 50)
            result['stoch_k_3m'] = d.get('k_3m', 50)
            result['stoch_d_3m'] = d.get('d_3m', 50)
    except Exception: pass
    return result

def calc_gain(pos_key, current_price, entry_price=None):
    if not entry_price: entry_price = get_entry_price(pos_key)
    if entry_price <= 0 or current_price <= 0: return 0.0
    if pos_key.endswith('_LONG'):
        return ((current_price - entry_price) / entry_price) * 100
    else:
        return ((entry_price - current_price) / entry_price) * 100

def classify_trade(action, side, symbol, price, ind, pos_key, system):
    reasons = []
    score = 0
    is_long = pos_key.endswith('_LONG') if pos_key else (side == 'BUY' and action == 'OPEN')
    is_entry = action in ('OPEN', 'AUGMENT')
    if system == 'tradier':
        k_s = sf(ind.get('stoch_k_5m', 50)); d_s = sf(ind.get('stoch_d_5m', 50))
    else:
        k_s = sf(ind.get('stoch_k_3m', 50)); d_s = sf(ind.get('stoch_d_3m', 50))
    k_m = sf(ind.get('stoch_k_15m', 50)); d_m = sf(ind.get('stoch_d_15m', 50))
    k_1h = sf(ind.get('stoch_k_1h', 50)); d_1h = sf(ind.get('stoch_d_1h', 50))
    k_4h = sf(ind.get('stoch_k_4h', 50)); d_4h = sf(ind.get('stoch_d_4h', 50))
    ha_1h = ind.get('ha_1h', 'neutral')
    uncalc = []
    if k_s == 50 and d_s == 50: uncalc.append('ltf')
    if k_m == 50 and d_m == 50: uncalc.append('15m')
    if k_1h == 50 and d_1h == 50: uncalc.append('1h')
    if uncalc:
        reasons.append(f"UNCALC({','.join(uncalc)})")
        if is_entry:
            if system == 'tradier': score -= 20  # all data available for tradier
            elif 'ltf' in uncalc: score -= 10  # only penalize if short-term missing for crypto
    if is_entry:
        if is_long:
            if k_s < 30: score += 5; reasons.append(f"k_low({k_s:.0f})")
            elif k_s > 70: score -= 5; reasons.append(f"k_HIGH({k_s:.0f})")
            if not (k_m == 50 and d_m == 50):
                if k_m < 30: score += 5; reasons.append(f"k15_low({k_m:.0f})")
                elif k_m > 70: score -= 3; reasons.append(f"k15_HIGH({k_m:.0f})")
            if not (k_1h == 50 and d_1h == 50):
                if k_1h > d_1h: score += 3; reasons.append("1h_bull")
                else: score -= 3; reasons.append("1h_BEAR")
            if not (k_4h == 50 and d_4h == 50):
                if k_4h > d_4h: score += 2; reasons.append("4h_bull")
                else: score -= 2; reasons.append("4h_BEAR")
            if k_s > d_s: score += 2; reasons.append("ltf_bull")
            else: score -= 2; reasons.append("ltf_BEAR")
            if ha_1h == 'green': score += 2; reasons.append("ha1h_grn")
            elif ha_1h == 'red': score -= 2; reasons.append("ha1h_RED")
        else:
            if k_s > 70: score += 5; reasons.append(f"k_high({k_s:.0f})")
            elif k_s < 30: score -= 5; reasons.append(f"k_LOW({k_s:.0f})")
            if not (k_m == 50 and d_m == 50):
                if k_m > 70: score += 5; reasons.append(f"k15_high({k_m:.0f})")
                elif k_m < 30: score -= 3; reasons.append(f"k15_LOW({k_m:.0f})")
            if not (k_1h == 50 and d_1h == 50):
                if k_1h < d_1h: score += 3; reasons.append("1h_bear")
                else: score -= 3; reasons.append("1h_BULL")
            if not (k_4h == 50 and d_4h == 50):
                if k_4h < d_4h: score += 2; reasons.append("4h_bear")
                else: score -= 2; reasons.append("4h_BULL")
            if k_s < d_s: score += 2; reasons.append("ltf_bear")
            else: score -= 2; reasons.append("ltf_BULL")
            if ha_1h == 'red': score += 2; reasons.append("ha1h_red")
            elif ha_1h == 'green': score -= 2; reasons.append("ha1h_GRN")
    else:  # EXIT
        if is_long:
            if k_s > 80: score += 3; reasons.append(f"exit_top({k_s:.0f})")
            if k_m > 80: score += 3; reasons.append(f"exit_15top({k_m:.0f})")
            if k_s < 20: score -= 5; reasons.append(f"exit_BOT({k_s:.0f})")
        else:
            if k_s < 20: score += 3; reasons.append(f"exit_bot({k_s:.0f})")
            if k_m < 20: score += 3; reasons.append(f"exit_15bot({k_m:.0f})")
            if k_s > 80: score -= 5; reasons.append(f"exit_TOP({k_s:.0f})")
    if score >= 8: grade = "SMART"
    elif score >= 3: grade = "OK"
    elif score >= -3: grade = "NEUTRAL"
    elif score >= -8: grade = "BAD"
    else: grade = "STUPID"
    return grade, score, reasons

def fmt_stoch(ind, sys_type):
    tfs = [('1m','stoch_k_1m','stoch_d_1m')]
    if sys_type == 'tradier': tfs.append(('5m','stoch_k_5m','stoch_d_5m'))
    else: tfs.append(('3m','stoch_k_3m','stoch_d_3m'))
    tfs += [('15m','stoch_k_15m','stoch_d_15m'),('1h','stoch_k_1h','stoch_d_1h'),('4h','stoch_k_4h','stoch_d_4h')]
    return " ".join(f"{t}:{sf(ind.get(k,50)):.0f}/{sf(ind.get(d,50)):.0f}{'!' if sf(ind.get(k,50))==50 and sf(ind.get(d,50))==50 else ''}" for t,k,d in tfs)

def main():
    end_time = time.time() + MONITOR_HOURS * 3600
    with open(OUT_FILE, 'w') as out:
        out.write(f"=== TRADE MONITOR v2 — {datetime.now().isoformat()} — {MONITOR_HOURS}h ===\n")
        out.write(f"Accounts: {list(WATCH_FILES.values())} | Redis: {'OK' if REDIS_OK else 'NO'}\n")
        out.write("=" * 150 + "\n\n")
        out.flush()
        print(f"[MON] Started -> {OUT_FILE}")
        fpos = {f: os.path.getsize(f) for f in WATCH_FILES if os.path.exists(f)}
        stats = {'total': 0, 'smart': 0, 'stupid': 0, 'ok': 0, 'neutral': 0, 'bad': 0}
        trades = []
        seen = set()
        total_pnl = 0.0
        last_summ = time.time()
        while time.time() < end_time:
            for fpath, label in WATCH_FILES.items():
                try:
                    csz = os.path.getsize(fpath)
                    if csz < fpos.get(fpath, 0): fpos[fpath] = 0
                    if csz <= fpos.get(fpath, 0): continue
                    with open(fpath, 'r', errors='replace') as f:
                        f.seek(fpos[fpath])
                        lines = f.readlines()
                        fpos[fpath] = f.tell()
                    for line in lines:
                        line = re.sub(r'\[[0-9;]*m', '', line)  # strip ANSI
                        if '[SHARED_MEM]' in line: continue
                        m = RE_REASON.search(line)
                        mc = None
                        if not m:
                            mc = RE_CRYPTO.search(line)
                            if not mc: continue
                        if m:
                            symbol, action, side, qty_s, price_s, status, reason = m.groups()
                            price = sf(price_s); qty = sf(qty_s); reason = reason.strip()
                            pk_m = POS_KEY_RE.search(line)
                            pos_key = pk_m.group(1) if pk_m else ''
                        else:
                            pos_key, pv_before_s, act_kw, amount_s, gain_s, reason = mc.groups()
                            reason = reason.strip()
                            pv_before = sf(pv_before_s); amount = sf(amount_s)
                            # Determine symbol from pos_key (acc:SYMBOL_LONG -> SYMBOL)
                            _pk_parts = pos_key.split(':',1)
                            _sym_dir = _pk_parts[1] if len(_pk_parts) > 1 else _pk_parts[0]
                            symbol = '_'.join(_sym_dir.split('_')[:-1])
                            is_long = pos_key.endswith('_LONG')
                            if act_kw == 'Augment':
                                action = 'OPEN' if pv_before < 0.01 else 'AUGMENT'
                                side = 'BUY' if is_long else 'SELL'
                            else:
                                action = 'CLOSE' if abs(amount - pv_before) < 0.01 and pv_before > 0 else 'REDUCE'
                                side = 'SELL' if is_long else 'BUY'
                            # Get price from Redis for crypto
                            _cp = 0.0
                            try:
                                _raw = r.get(f'mark_price:{symbol}') if REDIS_OK else None
                                if _raw:
                                    _d = json.loads(_raw)
                                    _cp = sf(_d.get('price', 0)) if isinstance(_d, dict) else sf(_raw)
                            except Exception: pass
                            if _cp <= 0:
                                try:
                                    _raw = r.get(f'latest_kline:{symbol}') if REDIS_OK else None
                                    if _raw:
                                        _d = json.loads(_raw)
                                        _cp = sf(_d.get('close', 0)) if isinstance(_d, dict) else 0
                                except Exception: pass
                            price = _cp
                            qty = amount / price if price > 0 else 0
                            qty_s = str(qty); price_s = str(price)
                        ts_m = re.match(r'\[?(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                        if not ts_m:
                            ts_m = re.match(r'(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                        ts = ts_m.group(1) if ts_m else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        if len(ts) < 11: ts = f'2026-{ts}' 
                        # Dedup: same (symbol, action, price) within same minute from same label
                        _dk_ts = ts[-14:-3] if len(ts) > 14 else ts[:11]  # normalize to MM-DD HH:MM
                        dk = f"{symbol}_{action}_{side}_{price_s}_{label}_{_dk_ts}"
                        if dk in seen: continue
                        seen.add(dk)
                        if len(seen) > 2000: seen = set(list(seen)[-1000:])
                        system = 'tradier' if 'tradier' in label else 'crypto'
                        ind = get_tradier_indicators(symbol) if system == 'tradier' else get_crypto_indicators(symbol)
                        entry_price = get_entry_price(pos_key) if pos_key else 0
                        gain_pct = calc_gain(pos_key, price, entry_price) if pos_key and entry_price > 0 else 0
                        pos_value = abs(qty * price)
                        gain_dollar = pos_value * gain_pct / 100 if gain_pct else 0
                        if action in ('CLOSE', 'REDUCE'):
                            total_pnl += gain_dollar
                        grade, sc, cr = classify_trade(action, side, symbol, price, ind, pos_key, system)
                        stoch = fmt_stoch(ind, system)
                        stats['total'] += 1
                        stats[grade.lower()] = stats.get(grade.lower(), 0) + 1
                        gi = {'SMART':'+','OK':'~','NEUTRAL':'.','BAD':'?','STUPID':'X'}[grade]
                        gain_str = f"{gain_pct:+.2f}% ${gain_dollar:+.1f}" if entry_price > 0 else "new"
                        entry = (
                            f"[{gi}] {ts} | {label:12s} | {pos_key or symbol:25s} | "
                            f"{action:7s} {side:4s} x{qty:<6.0f} @${price:<10.2f} val=${pos_value:<8.0f} | "
                            f"{gain_str:>16s} | {grade:7s} sc:{sc:+3d} | {stoch}\n"
                            f"    {reason[:110]}\n"
                            f"    {', '.join(cr)}\n"
                        )
                        out.write(entry); out.flush()
                        print(entry.rstrip())
                        trades.append({'ts': ts, 'label': label, 'pk': pos_key, 'action': action,
                                       'side': side, 'price': price, 'qty': qty, 'grade': grade,
                                       'score': sc, 'reason': reason, 'symbol': symbol,
                                       'gain_pct': gain_pct, 'gain_dollar': gain_dollar,
                                       'entry_price': entry_price, 'stoch': stoch})
                except Exception as e:
                    print(f"[ERR] {fpath}: {e}")
            now = time.time()
            if now - last_summ > 1800 and stats['total'] > 0:
                last_summ = now
                closes = [t for t in trades if t['action'] in ('CLOSE','REDUCE')]
                wins = [t for t in closes if t['gain_pct'] > 0]
                losses = [t for t in closes if t['gain_pct'] < 0]
                s = (f"\n{'='*120}\n"
                     f"  SUMMARY {datetime.now().strftime('%H:%M')} | Trades:{stats['total']} | "
                     f"SMART:{stats.get('smart',0)} OK:{stats.get('ok',0)} BAD:{stats.get('bad',0)} STUPID:{stats.get('stupid',0)}\n"
                     f"  Closes: {len(closes)} | Wins: {len(wins)} | Losses: {len(losses)} | "
                     f"Win%: {100*len(wins)/max(1,len(closes)):.0f}% | PnL: ${total_pnl:+.2f}\n"
                     f"{'='*120}\n\n")
                out.write(s); out.flush(); print(s.rstrip())
            time.sleep(3)
        # Final
        closes = [t for t in trades if t['action'] in ('CLOSE','REDUCE')]
        wins = [t for t in closes if t['gain_pct'] > 0]
        losses = [t for t in closes if t['gain_pct'] < 0]
        final = (f"\n{'='*150}\n=== FINAL REPORT — {datetime.now().isoformat()} ===\n"
                 f"Trades: {stats['total']} | Entries: {sum(1 for t in trades if t['action'] in ('OPEN','AUGMENT'))} | Exits: {len(closes)}\n"
                 f"Wins: {len(wins)} | Losses: {len(losses)} | Win%: {100*len(wins)/max(1,len(closes)):.0f}%\n"
                 f"Total PnL: ${total_pnl:+.2f}\n"
                 f"Grades: SMART:{stats.get('smart',0)} OK:{stats.get('ok',0)} NEUTRAL:{stats.get('neutral',0)} BAD:{stats.get('bad',0)} STUPID:{stats.get('stupid',0)}\n\n")
        accts = {}
        for t in trades:
            a = t['label']
            accts.setdefault(a, {'t':0,'s':0,'x':0,'pnl':0,'w':0,'l':0})
            accts[a]['t'] += 1
            if t['grade'] == 'SMART': accts[a]['s'] += 1
            elif t['grade'] == 'STUPID': accts[a]['x'] += 1
            if t['action'] in ('CLOSE','REDUCE'):
                accts[a]['pnl'] += t['gain_dollar']
                if t['gain_pct'] > 0: accts[a]['w'] += 1
                elif t['gain_pct'] < 0: accts[a]['l'] += 1
        final += "Per Account:\n"
        for a, v in sorted(accts.items()):
            final += f"  {a:15s} {v['t']:3d} trades W:{v['w']:2d} L:{v['l']:2d} PnL:${v['pnl']:+.2f} SMART:{v['s']:2d} STUPID:{v['x']:2d}\n"
        closes_sorted = sorted(closes, key=lambda t: t['gain_dollar'])
        if closes_sorted:
            final += f"\nBiggest Losses:\n"
            for t in closes_sorted[:10]:
                final += f"  {t['ts']} {t['label']:12s} {t['pk']:25s} {t['action']:7s} @${t['price']:.2f} {t['gain_pct']:+.2f}% ${t['gain_dollar']:+.1f} | {t['reason'][:50]}\n"
            final += f"\nBiggest Wins:\n"
            for t in reversed(closes_sorted[-10:]):
                final += f"  {t['ts']} {t['label']:12s} {t['pk']:25s} {t['action']:7s} @${t['price']:.2f} {t['gain_pct']:+.2f}% ${t['gain_dollar']:+.1f} | {t['reason'][:50]}\n"
        stupid_entries = [t for t in trades if t['grade'] == 'STUPID' and t['action'] in ('OPEN','AUGMENT')]
        if stupid_entries:
            final += f"\nStupidest Entries ({len(stupid_entries)}):\n"
            for t in stupid_entries[:20]:
                final += f"  {t['ts']} {t['label']:12s} {t['pk']:25s} @${t['price']:.2f} sc:{t['score']:+d} | {t['stoch']}\n"
        out.write(final); out.flush(); print(final)
        with open(OUT_FILE.replace('.log','.json'), 'w') as jf:
            json.dump(trades, jf, indent=2, default=str)
        print(f"[MON] Done. {OUT_FILE}")

if __name__ == '__main__':
    main()
