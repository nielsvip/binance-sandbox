"""Prepare an isolated baseline-only candidate command; never launch it."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
from v15_nextgen_bench_isolation import prepare, check_config


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.candidate_copy_tmp')
    with temporary.open('xb') as stream:
        stream.write(Path(source).read_bytes())
    os.replace(temporary, target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--previous-base', required=True)
    parser.add_argument('--bundle-root', required=True)
    parser.add_argument('--assets', required=True)
    parser.add_argument('--pilot', required=True)
    parser.add_argument('--helper', required=True)
    args = parser.parse_args()
    base, bundle, assets = Path(args.previous_base), Path(args.bundle_root), Path(args.assets)
    previous, root, side = base / 'reference', base / 'candidate', base / 'candidate_side'
    if root.exists() or side.exists():
        raise ValueError('new candidate paths required; preserve previous attempts')
    oracle = json.loads((base / 'side/previous_baseline_oracle.json').read_text())
    proof = oracle['previous_actual_engine_proof']
    request = json.loads((base / 'side/oracle_request.json').read_text())
    def selected(source):
        if sha(source['path']) != source['sha256']:
            raise ValueError('accepted source changed')
        value = json.loads(Path(source['path']).read_text())
        for key in source.get('settings_pointer', []):
            value = value[key]
        return value
    pin = {'schema': 'v15_pinned_baseline/1', 'sym_side': request['sym_side'], 'window_days': request['window_days'], 'holdout_days': request['holdout_days'], 'window_context': proof['oracle']['window_context'], 'npz_sha256': request['npz_sha256'], 'accepted_source': oracle['accepted_source'], 'accepted_full_settings': selected(oracle['accepted_source']), 'previous_defaults_source': oracle['previous_defaults_source'], 'previous_effective_defaults': selected(oracle['previous_defaults_source']), 'previous_actual_engine_proof': proof, 'neutral_new_keys': {}}
    declaration = json.loads((bundle / 'tools/v18_model_bundle.json').read_text())
    files = {'data/' + ('brain/' if name.startswith('v18_brain_') else 'learn/') + name: value for name, value in declaration['expected_sha256_by_filename'].items()}
    if len(files) != 32:
        raise ValueError('candidate requires complete32 declared artifact files')
    for relative, expected in files.items():
        if sha(bundle / relative) != expected:
            raise ValueError('candidate bundle source mismatch')
    size = sum((bundle / relative).stat().st_size for relative in ['tools/v15_v18_planner.py', 'tools/v18_brain.py', 'tools/v18_model_bundle.json']) + Path(args.pilot).stat().st_size * 3 + Path(args.helper).stat().st_size + (16 << 20)
    if shutil.disk_usage(base).free - size < 10000 * (1 << 20):
        raise ValueError('candidate preparation would breach original disk reserve')
    shutil.copytree(previous, root, copy_function=os.link, ignore=shutil.ignore_patterns("harness_backups", "__pycache__"))
    side.mkdir()
    for relative in files:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.immutable_link_tmp')
        os.link(bundle / relative, temporary)
        os.replace(temporary, target)
    for relative in ['tools/v15_v18_planner.py', 'tools/v18_brain.py', 'tools/v18_model_bundle.json']:
        atomic_copy(bundle / relative, root / relative)
    atomic_copy(args.pilot, root / 'v15_pilot.py')
    atomic_copy(args.helper, root / 'tools/v15_nextgen_baseline_guard.py')
    (side / 'baseline_pin.json').write_text(json.dumps(pin, indent=2))
    env = json.loads((base / 'side/prepared_env.json').read_text())
    env.update(V15_PROGRESS_DIR=str(side / 'progress'), V15_OUT_DIR=str(side / 'out'), V15_PINNED_BASELINE=str(side / 'baseline_pin.json'), V15_BASELINE_VERIFY_ONLY='1', V15_BASELINE_PRECHECK='1', V15_FRESH_RUN='1', V15_ISOLATED_PROOF='1', V18_LINKNET_FIRST='1', V18_LINKNET_BUNDLE_REQUIRED='1', V15_REQUIRE_MODEL_BUNDLE='1')
    for name in ['progress', 'out', 'tmp']:
        (side / name).mkdir()
    executable, env = prepare(root, side, env, request['sym_side'].rsplit('_', 1)[0], request['window_days'], assets)
    check_config(executable, root, side, env)
    (side / 'env.json').write_text(json.dumps(env, indent=2))
    command = [executable, '-u', str(base / 'harness/v15_nextgen_candidate_baseline_run.py'), '--root', str(root), '--side', str(side), '--sym-side', request['sym_side']]
    (side / 'run_candidate_baseline.sh').write_text('#!/bin/sh\nexec env ' + shlex.join([key + '=' + value for key, value in env.items()] + command) + '\n')
    receipt = {'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'CANDIDATE_BASELINE_PREPARED_NOT_EXECUTED', 'resource_contract': {'scope': 'baseline-only precheck, not official gain gate', 'available_memory_mib': 7000, 'disk_reserve_mib': 10000, 'hard_address_space_bytes': 6 * (1 << 30), 'new_output_bytes': 64 * (1 << 20)}, 'previous_oracle_sha256': sha(base / 'side/previous_baseline_oracle.json'), 'pin_sha256': sha(side / 'baseline_pin.json'), 'pilot_sha256': sha(root / 'v15_pilot.py'), 'helper_sha256': sha(root / 'tools/v15_nextgen_baseline_guard.py'), 'routing_manifest_sha256': sha(assets / 'manifest.json'), 'command': ['sh', str(side / 'run_candidate_baseline.sh')]}
    (side / 'preparation_receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
