#!/bin/bash
# 2026-10-28: board-universe stocks that are NOT in the closed-15m feed set (symbols_{tra,trb,trc}_{long,short}.json) never got fresh klines on S5, so the
# S5 tail service could not append their bar (ARM/CLS/... 8-17 bars behind). Ship just those klines files; S5 appends ONE bar per symbol per close.
cd /Users/niels/Documents/binance || exit 1
LIST=/tmp/stock_universe_kline_push.list
/usr/bin/python3 - > "$LIST" <<'PY'
import json, glob
d = json.load(open(sorted(glob.glob('data/daily_universe/20??????.json'))[-1]))
uni = {s.rsplit('_', 1)[0] for s in d['allowed_sym_sides'] if not s.rsplit('_', 1)[0].endswith(('USDT', 'USDC'))}
feed = set()
for a in ('tra', 'trb', 'trc'):
    for sd in ('long', 'short'):
        try:
            feed |= set(json.load(open(f'symbols_{a}_{sd}.json')))
        except Exception:
            pass
try:
    extra = {x.split('_')[0] for x in open('data/append_universe_extra.txt').read().split() if not x.upper().endswith(('USDT', 'USDC'))}
except OSError:
    extra = set()
print("\n".join(f"{s}_15m.json" for s in sorted((uni | extra) - feed)))
PY
[ -s "$LIST" ] || exit 0
/usr/bin/rsync -a --timeout=30 -e "ssh -o BatchMode=yes -o ConnectTimeout=10 -o ControlMaster=no -o ControlPath=none" --files-from="$LIST" klines_cache/tradier/ s5:binance-sandbox/klines_cache/tradier/
