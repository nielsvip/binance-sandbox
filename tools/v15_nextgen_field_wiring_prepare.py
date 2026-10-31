"""One immutable wiring runtime, four explicitly post-control research variants."""
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


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-request', required=True)
    parser.add_argument('--source-receipt', required=True)
    parser.add_argument('--source-receipt-sha256', required=True)
    parser.add_argument('--control-oracle', required=True)
    parser.add_argument('--control-oracle-sha256', required=True)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--harness', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.source_request).read_text())
    source_receipt = Path(args.source_receipt)
    if sha(source_receipt) != args.source_receipt_sha256 or sha(args.control_oracle) != args.control_oracle_sha256:
        raise ValueError('source/control pin changed')
    original = json.loads(source_receipt.read_text())
    source_root = Path(request['reference_root'])
    if original['reference_root'] != str(source_root):
        raise ValueError('source root differs')
    runtime_source = Path(request['previous_runtime_manifest']['path'])
    if sha(runtime_source) != request['previous_runtime_manifest']['sha256']:
        raise ValueError('source runtime manifest changed')
    runtime = json.loads(runtime_source.read_text())
    if runtime['files']['v12_quick_engine.py'] != '49410495ef3745cd8cd9cc2502f6dfba01ea3fe542b4bb8c07aa2c2c8e557b4f' or sha(args.engine) != 'b287467d1dc0e66e0fda7b81fc4bf2ddde49f186b6b0a83cf5fbeb9cdb6d5a43':
        raise ValueError('field-only semantic preimage/candidate differs')
    destination, harness = Path(args.destination), Path(args.harness)
    destination.mkdir(parents=True, exist_ok=False)
    root = destination / 'reference'
    root.mkdir()
    files = {}
    for relative, metadata in original['files'].items():
        source = source_root / relative
        stat = source.stat()
        if source.is_symlink() or (stat.st_size, stat.st_ino, stat.st_mtime_ns) != (metadata['bytes'], metadata['inode'], metadata['mtime_ns']):
            raise ValueError('source immutable inputs changed')
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, target)
        files[relative] = dict(metadata)
    engine = root / 'v12_quick_engine.py'
    fd, temporary = tempfile.mkstemp(prefix='.field_overlay_', dir=root)
    os.close(fd)
    shutil.copyfile(args.engine, temporary)
    os.replace(temporary, engine)
    runtime['files']['v12_quick_engine.py'] = sha(engine)
    runtime_file = destination / 'runtime_manifest.json'
    save(runtime_file, runtime)
    stat = engine.stat()
    files['v12_quick_engine.py'] = {'sha256': sha(engine), 'bytes': stat.st_size, 'inode': stat.st_ino, 'mtime_ns': stat.st_mtime_ns}
    materialization = dict(original, reference_root=str(root), files=files, previous_runtime_manifest_sha256=sha(runtime_file), source_receipt_sha256=args.source_receipt_sha256, proof_scope='Checked immutable inode lineage plus fully hashed field-only engine overlay; no live links/data refresh')
    materialization_file = destination / 'materialization_receipt.json'
    save(materialization_file, materialization)
    outputs = {}
    for name, variant in [('false_control', {}), ('fixed_true', {'FIXED_QUANTITY_ENABLED': True}), ('momentum_true', {'WT_MOMENTUM_EXIT_ENABLED': True}), ('both_true', {'FIXED_QUANTITY_ENABLED': True, 'WT_MOMENTUM_EXIT_ENABLED': True})]:
        side = destination / (name + '_side')
        side.mkdir()
        source_side = Path(request['side_dir'])
        env = json.loads(Path(request['prepared_env_file']).read_text())
        env = {key: value.replace(str(source_root), str(root)).replace(str(source_side), str(side)) if isinstance(value, str) else value for key, value in env.items()}
        for directory in ('private_store', 'flags', 'carry', 'prev_best', 'parity_cache', 'tmp', 'numba_cache', 'mpl', 'xdg_cache', 'audit_bootstrap'):
            (side / directory).mkdir()
        for relative in ('private_store/per_sym_store.db', 'audit_bootstrap/sitecustomize.py'):
            seed = source_side / relative
            if seed.exists():
                if seed.is_symlink():
                    raise ValueError('private seed is not regular')
                shutil.copyfile(seed, side / relative)
        env_file = side / 'prepared_env.json'
        save(env_file, env)
        item = dict(request, reference_root=str(root), side_dir=str(side), previous_runtime_manifest={'path': str(runtime_file), 'sha256': sha(runtime_file)}, materialization_receipt={'path': str(materialization_file), 'sha256': sha(materialization_file)}, prepared_env_file=str(env_file), experiment_role='existing_switch_wiring_diagnostic', requested_variant=variant, unchanged_corrected_control={'path': args.control_oracle, 'sha256': args.control_oracle_sha256})
        item.pop('authority_arm', None)
        item.pop('unchanged_previous_oracle', None)
        path = side / 'wiring_request.json'
        save(path, item)
        outputs[name] = {'request': str(path), 'request_sha256': sha(path), 'requested_variant': variant}
    result = {'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'PREPARED_FIELD_WIRING_VARIANTS_NO_ENGINE_EXECUTION', 'sym_side': request['sym_side'], 'runtime_manifest_sha256': sha(runtime_file), 'materialization_sha256': sha(materialization_file), 'control_oracle_sha256': args.control_oracle_sha256, 'engine_sha256': sha(engine), 'compare_helper_sha256': sha(harness / 'v15_nextgen_field_wiring_compare.py'), 'variants': outputs}
    save(destination / 'preparation_receipt.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
