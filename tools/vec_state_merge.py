import argparse
import json
import os
import tempfile
from pathlib import Path


def owns(symbol_side, shard):
    if shard == 'all':
        return True
    symbol, side = symbol_side.rsplit('_', 1)
    venue = 'crypto' if symbol.upper().endswith(('USDT', 'USDC', 'USD1')) else 'stock'
    return shard == f'{venue}_{side.lower()}'


def merge(source, destination, shard):
    source, destination = Path(source), Path(destination)
    counts = {'updated': 0, 'older': 0, 'off_shard': 0, 'errors': 0}
    for category in ('state', 'intents'):
        target = destination / category
        target.mkdir(parents=True, exist_ok=True)
        for path in (source / category).glob('*.json'):
            try:
                data = json.loads(path.read_text())
                if not owns(data['ss'], shard):
                    counts['off_shard'] += 1
                    continue
                output = target / path.name
                if output.exists():
                    previous = json.loads(output.read_text())
                    if category == 'state':
                        if float(previous.get('bar_ts') or 0) > float(data.get('bar_ts') or 0) or float(previous.get('written_at') or 0) >= float(data.get('written_at') or 0):
                            counts['older'] += 1
                            continue
                    elif previous == data:
                        continue
                    elif max((float(item.get('emitted_at') or 0) for item in previous.get('intents', [])), default=0) >= max((float(item.get('emitted_at') or 0) for item in data.get('intents', [])), default=0):
                        counts['older'] += 1
                        continue
                fd, temporary = tempfile.mkstemp(dir=str(target), prefix='.' + path.name, suffix='.tmp')
                try:
                    with os.fdopen(fd, 'w') as stream:
                        json.dump(data, stream, separators=(',', ':'))
                    os.replace(temporary, output)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                counts['updated'] += 1
            except (OSError, ValueError, KeyError, TypeError):
                counts['errors'] += 1
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--shard', required=True)
    args = parser.parse_args()
    result = merge(args.source, args.destination, args.shard)
    print(json.dumps(result))
    raise SystemExit(1 if result['errors'] else 0)
