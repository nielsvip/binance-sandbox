#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from npz_tail_store import compact_tail, tail_len
from tools.npz_tail_service import load_universe, npz_for, owned_symbols


MIN_TAIL_ROWS = max(1, int(os.environ.get('NPZ_COMPACT_AT', '1')))


def maintenance_phase(now):
    return 90 <= float(now) % 900 < 720


def compact_once(base, cat_side, clock=time.time):
    result = {'at': clock(), 'cat_side': cat_side, 'compacted': 0, 'symbols': 0, 'errors': []}
    if not maintenance_phase(clock()):
        return result
    for symbol in owned_symbols(load_universe(base), cat_side):
        if not maintenance_phase(clock()):
            break
        if symbol.endswith(('_LONG', '_SHORT')):
            symbol = symbol.rsplit('_', 1)[0]
        path = npz_for(base, symbol)
        if not path or tail_len(path) < MIN_TAIL_ROWS:
            continue
        try:
            result['compacted'] += compact_tail(path)
            result['symbols'] += 1
        except Exception as exc:
            result['errors'].append({'symbol': symbol, 'error': str(exc)})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--cat-side', required=True)
    parser.add_argument('--loop', action='store_true')
    args = parser.parse_args()
    lock_path = Path(args.base) / 'data' / f'.npz_tail_compactor_{args.cat_side}.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            print(json.dumps(compact_once(args.base, args.cat_side)), flush=True)
            if not args.loop:
                return
            time.sleep(5)


if __name__ == '__main__':
    main()
