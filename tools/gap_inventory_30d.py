#!/usr/bin/env python3
"""USER SPEC 2026-09-28: standalone daily gap calculator — every 24h, last 30 trading sessions,
one JSON in BASE/tradier/ that tradier_manage just READS per open sym_side:
  negative-avg-open-gap LONGs sell at a 5m/15m top in the last 90 min;
  positive-avg-close-gap SHORTs likewise; buy back at a bottom in the first 120 min.
Sessions are rebuilt from klines_cache/{SYM}USDT_15m.json RTH bars (13:30-20:00 UTC, weekdays) —
NEVER from the D bars (they contain phantom weekend rows). Also mirrors the two legacy files the
running tradier_manage hot-reloads (300s TTL), so fresh data reaches a live process without restart.
Cron: daily 20:15 UTC (after close). Run manually any time; atomic writes.
"""
import json, os, pathlib, datetime

BASE = pathlib.Path(os.environ.get('BASE_PATH', '/Users/niels/Documents/binance'))
KC = BASE / 'klines_cache'
OUT_PRIMARY = BASE / 'tradier' / 'gap_inventory_30d.json'
OUT_LEGACY_OPEN = BASE / 'data' / 'gap_inventory_tradier_per_symbol.json'
OUT_LEGACY_CLOSE = BASE / 'data' / 'gap_close_inventory_tradier_per_symbol.json'
N_SESSIONS = 30

def universe():
    syms = set()
    for f in ('symbols_trb_long.json', 'symbols_trb_short.json'):
        try:
            j = json.load(open(BASE / f))
            syms |= {str(s).upper() for s in (j if isinstance(j, list) else j.keys())}
        except Exception:
            pass
    try:
        book = json.load(open(BASE / 'data' / 'hourly_reconfig' / 'per_sym_active_config_stocks.json'))
        syms |= {k.rsplit('_', 1)[0] for k in book if isinstance(book.get(k), dict)}
    except Exception:
        pass
    return sorted(s for s in syms if s and not s.startswith('_'))

def sessions_from_15m(sym):
    p = KC / f'{sym}USDT_15m.json'
    if not p.exists():
        return []
    try:
        rows = json.load(open(p))
    except Exception:
        return []
    by_day = {}
    for r in rows:
        ts = str(r.get('timestamp', ''))
        if len(ts) < 16:
            continue
        d, hm = ts[:10], ts[11:16]
        if not ('13:30' <= hm < '20:00'):
            continue
        try:
            if datetime.date.fromisoformat(d).weekday() >= 5:
                continue
        except Exception:
            continue
        by_day.setdefault(d, []).append((hm, float(r['open']), float(r['close'])))
    out = []
    for d in sorted(by_day):
        bars = sorted(by_day[d])
        if len(bars) < 8 or bars[0][0] > '13:45':
            continue
        out.append({'date': d, 'open': bars[0][1], 'close': bars[-1][2]})
    return out

def main():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # 1) persistent session history: merge today's token-derived sessions in (dedup by date, cap 60)
    hist_path = BASE / 'tradier' / 'gap_sessions_history.json'
    try:
        hist = json.loads(hist_path.read_text()) if hist_path.exists() else {}
    except Exception:
        hist = {}
    for sym in set(universe()) | set(hist.keys()):
        by = {s['date']: s for s in hist.get(sym, [])}
        for s in sessions_from_15m(sym):
            by[s['date']] = s
        if by:
            hist[sym] = [by[d] for d in sorted(by)][-60:]
    # 2) legacy recorder top-up source (in-process 09:35 ET recorder history, restored from git Sep-23)
    legacy_topup = {}
    try:
        legacy_topup = json.loads(pathlib.Path('/tmp/legacy_gap_open_sep23.json').read_text())
    except Exception:
        pass
    primary, leg_open, leg_close = {}, {}, {}
    n_ok = 0
    for sym, ses in hist.items():
        open_gaps, close_gaps = [], []
        for prev, cur in zip(ses, ses[1:]):
            try:
                d1 = datetime.date.fromisoformat(prev['date']); d2 = datetime.date.fromisoformat(cur['date'])
                if (d2 - d1).days > 7:
                    continue  # never bridge data holes with a fake multi-week "gap"
            except Exception:
                continue
            if prev['close'] > 0 and cur['open'] > 0:
                open_gaps.append(round((cur['open'] - prev['close']) / prev['close'] * 100, 4))
        for s in ses[1:]:
            if s['open'] > 0:
                close_gaps.append(round((s['close'] - s['open']) / s['open'] * 100, 4))
        # top up to N_SESSIONS with older legacy-recorder gaps (prepend, session gaps stay newest)
        if len(open_gaps) < N_SESSIONS:
            lt = legacy_topup.get(sym, {}).get('history') or []
            need = N_SESSIONS - len(open_gaps)
            open_gaps = [round(float(g), 4) for g in lt[-need:]] + open_gaps
        open_gaps = open_gaps[-N_SESSIONS:]
        close_gaps = close_gaps[-N_SESSIONS:]
        if not open_gaps:
            continue
        n_ok += 1
        primary[sym] = {
            'avg_open_gap_pct': round(sum(open_gaps) / len(open_gaps), 4),
            'avg_close_gap_pct': round(sum(close_gaps) / len(close_gaps), 4) if close_gaps else None,
            'days': len(open_gaps), 'close_days': len(close_gaps),
            'last5_open': open_gaps[-5:], 'last5_close': close_gaps[-5:],
            'sessions_span': f"{ses[0]['date']}..{ses[-1]['date']}", 'updated_utc': now,
        }
        leg_open[sym] = {'days': len(open_gaps), 'sum_gap_pct': round(sum(open_gaps), 4),
                         'last5': open_gaps[-5:], 'history': open_gaps}
        if close_gaps:
            leg_close[sym] = {'days': len(close_gaps), 'sum_gap_pct': round(sum(close_gaps), 4),
                              'last5': close_gaps[-5:], 'history': close_gaps}
    for path, obj in ((hist_path, hist), (OUT_PRIMARY, primary), (OUT_LEGACY_OPEN, leg_open), (OUT_LEGACY_CLOSE, leg_close)):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(obj, indent=1))
        os.replace(tmp, path)
    print(f'gap_inventory_30d: {n_ok} symbols -> {OUT_PRIMARY} (+2 legacy mirrors, history persisted) at {now}')

if __name__ == '__main__':
    main()
