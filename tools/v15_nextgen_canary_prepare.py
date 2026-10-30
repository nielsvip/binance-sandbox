"""Prepare a diagnostic command and immutable-source arm; never launch a pilot."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
from v15_nextgen_nn_comparison import atomic_copy, bundle_files, check_files, sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--review', required=True)
    parser.add_argument('--runtime-manifest', required=True)
    parser.add_argument('--bundle-root', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--snapshot', default='20261030T210717Z_current8_r2')
    parser.add_argument('--side', default='AAVEUSDC_LONG')
    args = parser.parse_args()
    review, work, bundle = Path(args.review).resolve(), Path(args.work).resolve(), Path(args.bundle_root).resolve()
    if work.exists():
        raise ValueError('new canary preparation work directory required')
    runtime = json.loads(Path(args.runtime_manifest).read_text())
    check_files(runtime['code_root'], runtime['files'])
    declaration = json.loads((bundle / 'tools/v18_model_bundle.json').read_text())
    artifacts = bundle_files(bundle, declaration)
    if len(artifacts) != 33:
        raise ValueError('exact32 declared assets plus declaration required')
    compatibility = {'tools/v15_v18_planner.py': 'f95d4212f7e6d05784f4ba3d62ea80c74c5c16443ee96be3a7b6e19e8a47e0f7', 'tools/v18_brain.py': '1edb5e0e552a7f6a5514c670d28ddb063e1698682d3d56ad5b61ee0d6ec7ef1a'}
    check_files(bundle, compatibility)
    required = sum((bundle / name).stat().st_size for name in {**artifacts, **compatibility}) + (16 << 20)
    if shutil.disk_usage(review).free - required < 20000 * (1 << 20):
        raise ValueError('preparation would breach configured20,000MiB disk reserve')
    work.mkdir(parents=True)
    code = work / 'code'
    shutil.copytree(runtime['code_root'], code, copy_function=os.link)
    for relative in {**artifacts, **compatibility}:
        atomic_copy(bundle / relative, code / relative)
    expected = dict(runtime['files'], **compatibility)
    check_files(code, expected)
    check_files(runtime['code_root'], runtime['files'])
    module_spec = importlib.util.spec_from_file_location('canary_gate', review / 'tools/v15_version_gate.py')
    gate = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(gate)
    gate.SNAP = args.snapshot
    champion = json.loads((gate.CHAMP / 'meta.json').read_text())
    env = gate.bench_env(gate.champ_env(champion))
    flags = dict(champion.get('flags') or {})
    flags.update(V18_LINKNET_FIRST='1', V18_LINKNET_BUNDLE_REQUIRED='1', V15_REQUIRE_MODEL_BUNDLE='1')
    pin = gate.snapshot_pin(args.snapshot)
    (work / 'snapshot_inputs.json').write_text(json.dumps(pin['inputs']))
    (work / 'tested_runtime_code.json').write_text(json.dumps(expected))
    harness_env = {'V15_PINNED_SNAPSHOT_MANIFEST_SHA256': pin['manifest_sha256'], 'V15_PINNED_SNAPSHOT_INPUTS_PATH': str(work / 'snapshot_inputs.json'), 'V15_NN_EXPECTED_CODE_PATH': str(work / 'tested_runtime_code.json')}
    (work / 'env.json').write_text(json.dumps(env, indent=2))
    (work / 'flags.json').write_text(json.dumps(flags, indent=2))
    interpreter = '/home/niels/.conda/envs/binance_env_mac/bin/python'
    command = [interpreter, '-u', str(review / 'tools/v15_npz_bench.py'), 'run', '--snap', args.snapshot, '--label', work.name, '--code', str(code), '--only', args.side, '--window-days', '30', '--env-file', str(work / 'env.json'), '--parallel', '1', '--workers', '2', '--nice', '10', '--min-free-mb', '7000', '--set', 'V15_HOLDOUT_DAYS=7']
    for name, value in flags.items():
        command += ['--set', f'{name}={value}']
    (work / 'launch_argv.json').write_text(json.dumps(command, indent=2))
    (work / 'harness_env.json').write_text(json.dumps(harness_env, indent=2))
    (work / 'command.sh').write_text('#!/bin/sh\nexec env ' + shlex.join([f'{name}={value}' for name, value in harness_env.items()] + command) + '\n')
    receipt = {'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'status': 'PREPARED_NOT_LAUNCHED_DIAGNOSTIC_CANDIDATE_ONLY', 'runtime_manifest_sha256': sha(args.runtime_manifest), 'candidate_declaration_sha256': sha(bundle / 'tools/v18_model_bundle.json'), 'champion_meta_sha256': sha(gate.CHAMP / 'meta.json'), 'frozen_flags_sha256': sha(gate.BENCH / ('snap_' + args.snapshot) / 'data/v15_gate_flags.json'), 'gate_sha256': sha(review / 'tools/v15_version_gate.py'), 'bench_sha256': sha(review / 'tools/v15_npz_bench.py'), 'candidate_behavior_addition': 'V18_LINKNET_FIRST=1; prior effective policy0, not merely provenance', 'prepared_copy_budget_bytes': required, 'fit_days_requested': 30, 'holdout_days': 7, 'source_files': len(expected), 'artifacts': 32, 'command': str(work / 'command.sh')}
    (work / 'preparation_receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
