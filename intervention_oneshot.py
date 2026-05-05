#!/usr/bin/env python3
# One-shot intervention: pushes 9 LONG hedges + 5 starter LONGs to
# intervention_queue:<account> in Redis. The running ez_manage workers
# (with the new _intervention_queue_loop) pop and route through execute_now().
import json, sys, time, os
import redis
HOST = os.environ.get('REDIS_HOST', '127.0.0.1')
PORT = int(os.environ.get('REDIS_PORT', '6379'))
DRY = '--dry' in sys.argv
r = redis.Redis(host=HOST, port=PORT, decode_responses=True)
_LMD_CACHE = {}
def _lmd():
    if 'data' not in _LMD_CACHE:
        raw = r.get('latest_market_data')
        _LMD_CACHE['data'] = json.loads(raw) if raw else {}
    return _LMD_CACHE['data']
def fetch_mark(symbol: str):
    data = _lmd()
    rec = data.get(symbol) or data.get(symbol.replace('USDC','USDT')) or data.get(symbol.replace('USDT','USDC'))
    if not isinstance(rec, dict): return None
    for k in ('current_price','close_3m','close_15m','close_1h','mark_price','price'):
        v = rec.get(k)
        if v is not None:
            try:
                f = float(v)
                if f > 0: return f
            except Exception: pass
    return None
COMMANDS = []
HEDGES = [
    ('inf','CHRUSDT',10.0),
    ('inf','DOGEUSDC',10.0),
    ('inf','API3USDT',8.0),
    ('inf','ROSEUSDT',10.0),
    ('inf','ADAUSDC',10.0),
    ('men','SUIUSDC',8.0),
    ('men','ICPUSDT',8.0),
    ('men','FILUSDC',9.0),
    ('men','SNXUSDT',8.0),
    ('fin','PENGUUSDC',8.0),
    ('fin','WIFUSDC',8.0),
    ('fin','DOGEUSDC',14.0),
]
STARTERS = [
    ('inf','TONUSDT',6.0),
    ('inf','PENGUUSDC',5.0),
    ('inf','CHILLGUYUSDT',5.0),
    ('inf','DASHUSDT',6.0),
    ('inf','ZECUSDC',5.0),
]
def round_qty(qty, price):
    if qty * price < 1.0: return None
    if qty > 1000: return round(qty)
    if qty > 100: return round(qty, 1)
    if qty > 10: return round(qty, 2)
    if qty > 1: return round(qty, 3)
    return round(qty, 4)
ttl = int(time.time()) + 1800
for kind, items in (('HEDGE_LONG', HEDGES), ('STARTER_LONG', STARTERS)):
    for acct, sym, usd in items:
        mark = fetch_mark(sym)
        if not mark or mark <= 0:
            print(f'  SKIP {acct}:{sym} no mark price')
            continue
        qty = round_qty(usd / mark, mark)
        if qty is None or qty <= 0:
            print(f'  SKIP {acct}:{sym} qty too small (mark={mark})')
            continue
        cmd = {
            'symbol': sym, 'side': 'BUY', 'position_side': 'LONG',
            'quantity': qty,
            'reason': f'INTERVENTION_GOLDEN_{kind}_${usd:.0f}',
            'is_hedge': False,
            'ttl_epoch': ttl,
        }
        COMMANDS.append((acct, cmd))
        print(f'  {acct}:{sym} qty={qty} mark={mark:.6f} usd={qty*mark:.2f} reason={cmd["reason"]}')
print(f'\nTotal: {len(COMMANDS)} commands')
if DRY:
    print('DRY RUN — not pushing'); sys.exit(0)
for acct, cmd in COMMANDS:
    key = f'intervention_queue:{acct}'
    r.rpush(key, json.dumps(cmd))
    print(f'  PUSHED -> {key}')
print('Done. ez_manage workers will pop within 3s.')
