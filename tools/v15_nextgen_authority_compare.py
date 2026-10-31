"""Run one pinned code-authority diagnostic, never label a code repair as NN gain."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--oracle-runner', required=True)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    if request.get('experiment_role') != 'authority_correction_diagnostic' or request.get('authority_arm') not in ('layer_only', 'orphan_only', 'both'):
        raise ValueError('explicit isolated authority arm required')
    old_source = request['unchanged_previous_oracle']
    if sha(old_source['path']) != old_source['sha256']:
        raise ValueError('original genuine oracle changed')
    original = json.loads(Path(old_source['path']).read_text())['previous_actual_engine_proof']
    spec = importlib.util.spec_from_file_location('isolated_authority_oracle', args.oracle_runner)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    capture = runner.capture_actual
    comparison = {}
    def measured(*capture_args, **capture_kwargs):
        result, proof = capture(*capture_args, **capture_kwargs)
        old_configs, new_configs = original['effective_config_values'], proof['effective_config_values']
        if len(old_configs) != 1 or len(new_configs) != 1:
            raise ValueError('single actual baseline configuration required')
        keys = sorted(set(old_configs[0]) | set(new_configs[0]))
        comparison.update({'old_oracle_sha256': old_source['sha256'], 'old_evaluated_at_utc': original['oracle']['evaluated_at_utc'], 'old_hashes': {key: original[key] for key in ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256')}, 'new_hashes': {key: proof[key] for key in ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256')}, 'effective_field_differences': [{'field': key, 'old_present': key in old_configs[0], 'new_present': key in new_configs[0], 'old': old_configs[0].get(key), 'new': new_configs[0].get(key)} for key in keys if key not in old_configs[0] or key not in new_configs[0] or old_configs[0][key] != new_configs[0][key]]})
        if proof['input_cutoff_sha256'] != original['input_cutoff_sha256']:
            raise ValueError('authority comparison changed actual frozen input cutoff')
        return result, proof
    def dumps(value, *dump_args, **dump_kwargs):
        if isinstance(value, dict):
            if value.get('schema') == 'v15_previous_baseline_oracle/1':
                value = dict(value, schema='v15_authority_correction_diagnostic/1', status='MEASURED_CODE_REPAIR_DIAGNOSTIC_NOT_NN_GAIN_OR_PRIOR_ORACLE', authority_arm=request['authority_arm'], comparison_to_unchanged_previous_oracle=comparison, scope='Same accepted reader inputs and frozen NPZ; code authority correction may change config/trades. Original historical oracle untouched. Not promotion evidence.')
            elif value.get('phase') == 'PREVIOUS_BASELINE_ORACLE_SAVED':
                value = dict(value, phase='AUTHORITY_DIAGNOSTIC_SAVED')
        return json.dumps(value, *dump_args, **dump_kwargs)
    runner.capture_actual = measured
    runner.json = types.SimpleNamespace(loads=json.loads, dumps=dumps)
    sys.argv = [args.oracle_runner, '--request', args.request]
    runner.main()
    if sha(old_source['path']) != old_source['sha256']:
        raise ValueError('original oracle changed during diagnostic')


if __name__ == '__main__':
    main()
