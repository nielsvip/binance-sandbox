"""Materialize three isolated code-repair arms from immutable previous inputs."""
import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-base', required=True)
    parser.add_argument('--source-receipt', required=True)
    parser.add_argument('--source-receipt-sha256', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--patch-manifest', required=True)
    parser.add_argument('--harness', required=True)
    parser.add_argument('--expected-oracle')
    parser.add_argument('--expected-oracle-sha256')
    args = parser.parse_args()
    base, destination, candidate, harness = map(Path, (args.source_base, args.destination, args.candidate, args.harness))
    source_receipt = Path(args.source_receipt)
    if source_receipt.is_symlink() or sha(source_receipt) != args.source_receipt_sha256:
        raise ValueError('original independently rehashed source receipt differs')
    source = json.loads(source_receipt.read_text())
    request = json.loads((base / 'side/oracle_request.json').read_text())
    source_root = Path(request['reference_root'])
    runtime_source = Path(request['previous_runtime_manifest']['path'])
    if sha(runtime_source) != request['previous_runtime_manifest']['sha256'] or source.get('reference_root') != str(source_root):
        raise ValueError('original runtime identity differs')
    files = source['files']
    for relative, metadata in files.items():
        path = source_root / relative
        stat = path.stat()
        if path.is_symlink() or not metadata.get('sha256') or (stat.st_size, stat.st_ino, stat.st_mtime_ns) != (metadata['bytes'], metadata['inode'], metadata['mtime_ns']):
            raise ValueError(f'immutable source changed: {relative}')
    manifest = json.loads(Path(args.patch_manifest).read_text())
    changes = ('v12_quick_engine.py', 'tools/opt/evaluate_v12.py')
    original_runtime = json.loads(runtime_source.read_text())
    for relative in changes:
        entry = manifest['files'][relative]
        if original_runtime['files'][relative] != entry['preimage_sha256'] or sha(candidate / relative) != entry['candidate_sha256']:
            raise ValueError(f'code-repair preimage/candidate differs: {relative}')
    if bool(args.expected_oracle) != bool(args.expected_oracle_sha256):
        raise ValueError('explicit oracle path and hash must be paired')
    expected = Path(args.expected_oracle) if args.expected_oracle else harness / 'expected_previous_baseline_oracle.json'
    expected_sha = sha(expected)
    intended_oracle_sha = args.expected_oracle_sha256 or '0a822a68a84275b6b1c71c7e38c005c55c0e65a25b3d70c026450f296bb8591a'
    if expected_sha != intended_oracle_sha:
        raise ValueError('unchanged genuine old oracle differs')
    destination.mkdir(parents=True, exist_ok=False)
    outputs = {}
    for arm, payload in [('layer_only', ('tools/opt/evaluate_v12.py',)), ('orphan_only', ('v12_quick_engine.py',)), ('both', changes)]:
        root, side = destination / (arm + '_reference'), destination / (arm + '_side')
        root.mkdir()
        side.mkdir()
        for relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source_root / relative, target)
        runtime = copy.deepcopy(original_runtime)
        for relative in payload:
            target = root / relative
            fd, temporary = tempfile.mkstemp(prefix='.authority_overlay_', dir=target.parent)
            os.close(fd)
            shutil.copyfile(candidate / relative, temporary)
            os.replace(temporary, target)
            runtime['files'][relative] = sha(target)
        runtime_file = destination / (arm + '_runtime_manifest.json')
        write_json(runtime_file, runtime)
        measured_files = {}
        for relative, metadata in files.items():
            target = root / relative
            stat = target.stat()
            digest = sha(target) if relative in payload else metadata['sha256']
            if relative not in payload and stat.st_ino != (source_root / relative).stat().st_ino:
                raise ValueError('immutable hardlink identity not preserved')
            measured_files[relative] = dict(metadata, sha256=digest, bytes=stat.st_size, inode=stat.st_ino, mtime_ns=stat.st_mtime_ns)
        materialization = dict(source, reference_root=str(root), previous_runtime_manifest_sha256=sha(runtime_file), files=measured_files, authority_arm=arm, source_receipt_sha256=args.source_receipt_sha256, proof_scope='Original full-content receipt plus checked immutable inode identity; overlay bytes fully SHA-verified. No fresh NPZ/data or source copied from live.')
        materialization_file = destination / (arm + '_materialization_receipt.json')
        write_json(materialization_file, materialization)
        env = json.loads(Path(request['prepared_env_file']).read_text())
        env = {key: value.replace(str(source_root), str(root)).replace(str(base / 'side'), str(side)).replace(str(base / 'harness'), str(harness)) if isinstance(value, str) else value for key, value in env.items()}
        for name in ('private_store', 'flags', 'carry', 'prev_best', 'parity_cache', 'tmp', 'numba_cache', 'mpl', 'xdg_cache', 'audit_bootstrap'):
            (side / name).mkdir()
        for relative in ('private_store/per_sym_store.db', 'audit_bootstrap/sitecustomize.py'):
            seed = base / 'side' / relative
            if seed.exists():
                if seed.is_symlink():
                    raise ValueError('private seed is not regular')
                shutil.copyfile(seed, side / relative)
        env_file = side / 'prepared_env.json'
        write_json(env_file, env)
        arm_request = dict(request, reference_root=str(root), side_dir=str(side), previous_runtime_manifest={'path': str(runtime_file), 'sha256': sha(runtime_file)}, materialization_receipt={'path': str(materialization_file), 'sha256': sha(materialization_file)}, prepared_env_file=str(env_file), experiment_role='authority_correction_diagnostic', authority_arm=arm, unchanged_previous_oracle={'path': str(expected), 'sha256': expected_sha})
        request_file = side / 'authority_request.json'
        write_json(request_file, arm_request)
        outputs[arm] = {'request': str(request_file), 'request_sha256': sha(request_file), 'side': str(side), 'runtime_manifest_sha256': sha(runtime_file), 'materialization_sha256': sha(materialization_file), 'source_overlay': {relative: runtime['files'][relative] for relative in payload}}
    receipt = {'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'PREPARED_THREE_AUTHORITY_ARMS_NO_ENGINE_EXECUTION', 'unchanged_previous_oracle_sha256': expected_sha, 'source_receipt_sha256': args.source_receipt_sha256, 'frozen_templates': {relative: metadata['sha256'] for relative, metadata in files.items() if relative.startswith('SPREADSHEETS/')}, 'arms': outputs, 'harness_sha256': {name: sha(harness / name) for name in ('v15_nextgen_authority_compare.py', 'v15_nextgen_baseline_oracle.py', 'v15_nextgen_baseline_guard.py', 'v15_nextgen_bench_isolation.py')}}
    write_json(destination / 'preparation_receipt.json', receipt)
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
