"""Read-only frozen benchmark input verification."""
import hashlib
import json
from pathlib import Path


def digest(path, algorithm):
    value = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            value.update(chunk)
    return value.hexdigest()


def verify_inputs(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    expected = {}
    for symbol, entry in manifest.get('npz', {}).items():
        expected['npz/' + symbol + '.npz'] = ('sha256', entry['sha256']) if 'sha256' in entry else ('md5', entry['md5'])
    for side, value in manifest.get('starts', {}).items():
        expected['starts/' + side + '_autopsy_base.json'] = ('md5', value)
    for name, value in manifest.get('templates', {}).items():
        expected['templates/' + name] = ('md5', value)
    for name, value in manifest.get('frozen_data_sha256', {}).items():
        expected[name] = ('sha256', value)
    inventory = {}
    for directory in ['npz', 'starts', 'templates', 'data']:
        base = snapshot / directory
        if base.is_symlink():
            raise ValueError(f'frozen input directory is symlinked: {base}')
        for path in sorted(base.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'frozen input symlink: {path}')
            if path.is_file():
                relative = str(path.relative_to(snapshot))
                sha = digest(path, 'sha256')
                inventory[relative] = sha
                if relative in expected:
                    algorithm, value = expected[relative]
                    actual = sha if algorithm == 'sha256' else digest(path, algorithm)
                    if actual != value:
                        raise ValueError(f'frozen input content mismatch: {relative}')
    missing = set(expected) - set(inventory)
    if missing:
        raise ValueError(f'frozen inputs missing: {sorted(missing)}')
    if 'frozen_data_sha256' in manifest:
        actual_data = {name for name in inventory if name.startswith('data/')}
        if actual_data != set(manifest['frozen_data_sha256']):
            raise ValueError('frozen data inventory changed')
    return {'inventory_sha256': hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'files': len(inventory)}
