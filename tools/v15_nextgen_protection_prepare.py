"""Prepare a fresh observation side using independently rehashed previous runtime."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--harness', required=True)
    parser.add_argument('--host-receipt', required=True)
    parser.add_argument('--host-receipt-sha256', required=True)
    args = parser.parse_args()
    base, harness = Path(args.base), Path(args.harness)
    receipt = Path(args.host_receipt)
    if receipt.is_symlink() or sha(receipt) != args.host_receipt_sha256:
        raise ValueError('fresh independent host materialization receipt changed')
    host = json.loads(receipt.read_text())
    request = json.loads((base / 'side/oracle_request.json').read_text())
    if host.get('reference_root') != request['reference_root'] or len(host.get('files', {})) != 4366:
        raise ValueError('unexpected frozen fixture inventory')
    if host.get('previous_runtime_manifest_sha256') != request['previous_runtime_manifest']['sha256']:
        raise ValueError('previous runtime identity differs')
    for relative, metadata in host['files'].items():
        stat = (Path(request['reference_root']) / relative).stat()
        if any(stat_value != metadata[key] for key, stat_value in [('bytes', stat.st_size), ('inode', stat.st_ino), ('mtime_ns', stat.st_mtime_ns)]):
            raise ValueError('materialized inputs changed after independent rehash')
    expected = harness / 'expected_previous_baseline_oracle.json'
    if sha(expected) != '0a822a68a84275b6b1c71c7e38c005c55c0e65a25b3d70c026450f296bb8591a':
        raise ValueError('genuine measured prior oracle identity differs')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    side = base / ('side_protection_trace_' + stamp)
    side.mkdir()
    old_side = Path(request['side_dir'])
    env = json.loads(Path(request['prepared_env_file']).read_text())
    env = {key: value.replace(str(old_side), str(side)).replace(str(base / 'harness'), str(harness)) if isinstance(value, str) else value for key, value in env.items()}
    for directory in ['private_store', 'flags', 'carry', 'prev_best', 'parity_cache', 'tmp', 'numba_cache', 'mpl', 'xdg_cache', 'audit_bootstrap']:
        (side / directory).mkdir()
    for relative in ['private_store/per_sym_store.db', 'audit_bootstrap/sitecustomize.py']:
        source = old_side / relative
        if source.exists():
            if source.is_symlink():
                raise ValueError('private seed must be a regular file')
            shutil.copyfile(source, side / relative)
    env_file = side / 'prepared_env.json'
    env_file.write_text(json.dumps(env, indent=2) + '\n')
    request.update(side_dir=str(side), prepared_env_file=str(env_file), materialization_receipt={'path': str(receipt), 'sha256': args.host_receipt_sha256})
    request_file = side / 'oracle_request.json'
    request_file.write_text(json.dumps(request, indent=2) + '\n')
    manifest = {'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'PREPARED_NO_ENGINE_EXECUTION', 'side': str(side), 'request': str(request_file), 'request_sha256': sha(request_file), 'env_sha256': sha(env_file), 'host_receipt_sha256': args.host_receipt_sha256, 'harness_sha256': {path.name: sha(path) for path in harness.glob('*.py')}, 'expected_oracle_sha256': sha(expected)}
    (side / 'preparation_receipt.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
