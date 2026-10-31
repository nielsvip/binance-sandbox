"""Reverify a copied previous-oracle fixture; never launch its evaluator."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    base = Path(args.root).resolve()
    root, source, side = base / 'reference', base / 'source_provenance', base / 'side'
    runtime = json.loads((source / 'runtime_manifest.json').read_text())
    snapshot = json.loads((source / 'snapshot_manifest.json').read_text())
    routes = json.loads((base / 'harness/routing_assets/manifest.json').read_text())
    expected = dict(runtime['files'])
    for name, value in snapshot['frozen_data_sha256'].items():
        relative = name.replace('templates/', 'SPREADSHEETS/TEMPLATE_FINAL_NORM/', 1) if name.startswith('templates/') else name
        expected[relative] = value
    for symbol in ['1INCHUSDT', 'COIN', 'CDE', 'TMO']:
        expected['backtest_v8/indicators/' + symbol + '.npz'] = snapshot['npz'][symbol]['sha256']
    files = {}
    for relative, value in expected.items():
        if relative in routes['source_sha256']:
            if value != routes['source_sha256'][relative]:
                raise ValueError('routing source lineage mismatch')
            value = routes['candidate_sha256'][relative]
        path = root / relative
        if path.is_symlink() or not path.is_file() or sha(path) != value:
            raise ValueError(f'previous fixture copy changed: {relative}')
        stat = path.stat()
        files[relative] = {'sha256': value, 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'inode': stat.st_ino}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    receipt_path = source / 'materialization_receipt.json'
    request_path = side / 'oracle_request.json'
    for path in [receipt_path, request_path]:
        shutil.copy2(path, path.with_name(path.name + '.before_refresh_' + stamp))
    receipt = {'schema': 'v15_prior_runtime_materialization/1', 'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'COPIED_FIXTURE_FULL_HASH_VERIFIED_NO_EVALUATION', 'reference_root': str(root), 'previous_runtime_manifest_sha256': sha(source / 'runtime_manifest.json'), 'snapshot_manifest_sha256': sha(source / 'snapshot_manifest.json'), 'files': files, 'routing_derived_from_original_source': routes['source_sha256']}
    receipt_path.write_text(json.dumps(receipt, indent=2))
    request = json.loads(request_path.read_text())
    request['materialization_receipt'] = {'path': str(receipt_path), 'sha256': sha(receipt_path)}
    request_path.write_text(json.dumps(request, indent=2))
    print(json.dumps({'status': receipt['status'], 'files': len(files), 'request_sha256': sha(request_path)}), flush=True)


if __name__ == '__main__':
    main()
