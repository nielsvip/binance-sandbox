"""IBM historical-settings fixture preparation only, no engine evaluation."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-base', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--source-receipt', required=True)
    parser.add_argument('--harness', required=True)
    args = parser.parse_args()
    base, destination, harness = map(Path, (args.source_base, args.destination, args.harness))
    receipt = Path(args.source_receipt)
    if sha(receipt) != '13ff0384a7ca6dc2f0c0f16cf472edd6cd1f182316429fa562a3c131ac48294b':
        raise ValueError('original full-content source receipt differs')
    original = json.loads(receipt.read_text())
    request = json.loads((base / 'side/oracle_request.json').read_text())
    source_root = Path(request['reference_root'])
    provenance = destination / 'source_provenance'
    captured = provenance / 'IBM_LONG_autopsy_base.json'
    history = provenance / 'accepted_baseline_ibm_long_history14341_readonly_20261031T0222Z.json'
    lineage = provenance / 'lineage_review.json'
    npz_seed = destination / 'frozen_inputs/IBM.npz'
    expected = {captured: 'e6fa1375321c6fb0d50cdc099b36455220070ea2aa3d4c40e155a99a46566a00', history: '78dda19324e8dd34924257fa5d5da69dff022fa9630b1565b598d1e82f471ad5', lineage: 'b2001df1781281a5bcfd279af12a597d66c719fbd8e8a43796a7e1468e0e5a40', npz_seed: '7dc50e9440e0853473127c805ce3f6f2afdf0fb9d3551d5b66812ca6bea8f475'}
    for path, digest in expected.items():
        if path.is_symlink() or sha(path) != digest:
            raise ValueError(f'IBM accepted/source input changed: {path}')
    exported = json.loads(history.read_text())
    if exported['row_identity']['id'] != 14341 or exported['row_identity']['sym_side'] != 'IBM_LONG' or not exported['documented_migration']['row']['documented_minhold_migration']['exact']:
        raise ValueError('IBM documented accepted reader lineage differs')
    root, side = destination / 'reference', destination / 'side'
    root.mkdir()
    side.mkdir()
    files = {}
    for relative, metadata in original['files'].items():
        source = source_root / relative
        stat = source.stat()
        if source.is_symlink() or (stat.st_size, stat.st_ino, stat.st_mtime_ns) != (metadata['bytes'], metadata['inode'], metadata['mtime_ns']):
            raise ValueError('immutable previous source changed')
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, target)
        files[relative] = dict(metadata)
    npz = root / 'backtest_v8/indicators/IBM.npz'
    shutil.copyfile(npz_seed, npz)
    stat = npz.stat()
    files[str(npz.relative_to(root))] = {'sha256': sha(npz), 'bytes': stat.st_size, 'inode': stat.st_ino, 'mtime_ns': stat.st_mtime_ns}
    materialization = dict(original, reference_root=str(root), files=files, source_receipt_sha256=sha(receipt), proof_scope='Original full-content receipt plus exact immutable hardlink identity; IBM NPZ independently copied and fully hashed from frozen current8_r2. No live source/data.')
    materialization_file = provenance / 'materialization_receipt.json'
    save(materialization_file, materialization)
    env = json.loads(Path(request['prepared_env_file']).read_text())
    env = {key: value.replace(str(source_root), str(root)).replace(str(base / 'side'), str(side)).replace(str(base / 'harness'), str(harness)) if isinstance(value, str) else value for key, value in env.items()}
    for name in ('private_store', 'flags', 'carry', 'prev_best', 'parity_cache', 'tmp', 'numba_cache', 'mpl', 'xdg_cache', 'audit_bootstrap'):
        (side / name).mkdir()
    for relative in ('private_store/per_sym_store.db', 'audit_bootstrap/sitecustomize.py'):
        source = base / 'side' / relative
        if source.exists():
            shutil.copyfile(source, side / relative)
    env_file = side / 'prepared_env.json'
    save(env_file, env)
    request.update(reference_root=str(root), side_dir=str(side), sym_side='IBM_LONG', window_days=30, npz_sha256=expected[npz_seed], materialization_receipt={'path': str(materialization_file), 'sha256': sha(materialization_file)}, prepared_env_file=str(env_file), accepted_source={'path': str(captured), 'sha256': sha(captured), 'previous_acceptance_id': 'SQL history14341 / documented MIN_HOLD reader migration exact consumed frozen start', 'stored_at_utc': exported['row_identity']['updated_at'], 'computed_at_utc': None, 'history_evidence': {'path': str(history), 'sha256': sha(history)}, 'lineage_evidence': {'path': str(lineage), 'sha256': sha(lineage)}, 'qualification': 'Stored acceptance lineage; no independently recovered historical execution receipt. New previous-runtime oracle evaluation is required.'}, previous_defaults_source={'path': str(history), 'sha256': sha(history), 'settings_pointer': ['decoded_payloads', 'defaults_snapshot']})
    request_file = side / 'oracle_request.json'
    save(request_file, request)
    result = {'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'IBM_PREPARED_NO_ENGINE_EXECUTION', 'request': str(request_file), 'request_sha256': sha(request_file), 'env_sha256': sha(env_file), 'materialization_sha256': sha(materialization_file), 'files': len(files), 'npz_sha256': sha(npz), 'npz_vintage': {'first_bar_utc': '2026-07-13T12:30:00.000000Z', 'last_bar_utc': '2026-10-30T19:45:00.000000Z', 'source': 'immutable S2 current8_r2 manifest'}, 'harness_sha256': {name: sha(harness / name) for name in ('v15_nextgen_baseline_oracle.py', 'v15_nextgen_baseline_guard.py', 'v15_nextgen_bench_isolation.py')}}
    save(destination / 'preparation_receipt.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
