"""Immutable per-side contract inventories; never invent accepted settings or oracles."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def sha(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'regular immutable input required: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_pin(path):
    pin = json.loads(Path(path).read_text())
    side = pin.get('sym_side', '')
    if pin.get('schema') != 'v15_pinned_baseline/1' or not re.fullmatch(r'[A-Z0-9]+_(LONG|SHORT)', side):
        raise ValueError('invalid per-side baseline contract')
    oracle = pin.get('previous_actual_engine_proof', {}).get('oracle', {})
    if oracle.get('npz_sha256') != pin.get('npz_sha256') or oracle.get('window_days') != pin.get('window_days') or oracle.get('window_context') != pin.get('window_context'):
        raise ValueError('contract lacks matching actual previous baseline scope')
    if not all(oracle.get(key) for key in ('evaluated_at_utc', 'previous_runtime_manifest_sha256', 'previous_engine_sha256', 'previous_evaluator_sha256')):
        raise ValueError('independent previous-runtime oracle identity required')
    for name in ('accepted_source', 'previous_defaults_source'):
        source = pin.get(name, {})
        if sha(source.get('path', '')) != source.get('sha256'):
            raise ValueError('anchored baseline source bytes differ')
    return pin


def materialize(destination, pins):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('contract generation already exists; never overwrite')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.baseline_contracts_', dir=destination.parent))
    inventory = {}
    for source in pins:
        pin = inspect_pin(source)
        side = pin['sym_side']
        if side in inventory:
            raise ValueError('duplicate side in contract generation')
        target = staging / (side + '.json')
        target.write_bytes(Path(source).read_bytes())
        inventory[side] = {'pin_sha256': sha(target), 'npz_sha256': pin['npz_sha256'], 'window_days': pin['window_days'], 'window_context': pin['window_context']}
    if not inventory:
        raise ValueError('empty contract generation')
    payload = {'schema': 'v15_baseline_contract_set/1', 'sides': inventory}
    manifest = staging / 'manifest.json'
    manifest.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')
    digest = sha(manifest)
    for path in staging.iterdir():
        path.chmod(0o444)
    staging.chmod(0o555)
    os.rename(staging, destination)
    return digest


def resolve(directory, manifest_sha256, sym_side, window_days, npz_path, paired_sides=()):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or sha(directory / 'manifest.json') != manifest_sha256:
        raise ValueError('pinned contract generation differs')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('schema') != 'v15_baseline_contract_set/1':
        raise ValueError('unknown baseline contract generation')
    npz_sha = sha(npz_path)
    for side in (sym_side, *paired_sides):
        entry = manifest.get('sides', {}).get(side)
        if not entry or entry['window_days'] != window_days or entry['npz_sha256'] != npz_sha:
            raise ValueError('side missing or NPZ/window has changed; fresh genuine oracle required')
        path = directory / (side + '.json')
        if sha(path) != entry['pin_sha256']:
            raise ValueError('per-side contract bytes changed')
        inspect_pin(path)
    return {'V15_PINNED_BASELINE': str(directory), 'V15_BASELINE_CONTRACT_MANIFEST_SHA256': manifest_sha256}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--sym-side', required=True)
    parser.add_argument('--window-days', required=True, type=int)
    parser.add_argument('--npz', required=True)
    parser.add_argument('--paired-side', action='append', default=[])
    args = parser.parse_args()
    env = resolve(args.directory, args.manifest_sha256, args.sym_side, args.window_days, args.npz, args.paired_side)
    print(json.dumps({'status': 'CONTRACT_INPUTS_VERIFIED_NOT_ENGINE_PARITY', 'env': env}, sort_keys=True))


if __name__ == '__main__':
    main()
