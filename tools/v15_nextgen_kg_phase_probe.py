"""Source-bound real-engine KG phase diagnostic; no row pass or production publication."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--oracle-runner', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    if request.get('experiment_role') != 'source_bound_kg_phase_diagnostic':
        raise ValueError('explicit KG diagnostic role required')
    runner = load('pinned_kg_oracle_runner', args.oracle_runner)
    capture = runner.capture_actual
    control_pin = request['unchanged_corrected_control']
    if sha(control_pin['path']) != control_pin['sha256']:
        raise ValueError('independent corrected baseline changed')
    expected = json.loads(Path(control_pin['path']).read_text())['previous_actual_engine_proof']
    helper_pin, array_pin = request['kg_helper'], request['prepared_array_helper']
    for pin in (helper_pin, array_pin, request['kg_template']):
        if Path(pin['path']).is_symlink() or sha(pin['path']) != pin['sha256']:
            raise ValueError('KG source/template pin changed')
    kg = load('pinned_kg_phase_source', helper_pin['path'])
    array_identity = load('pinned_kg_array_source', array_pin['path']).prepared_arrays_identity
    side = Path(request['side_dir'])
    def measured(prepared, settings, evaluate, window_days):
        pristine = copy.deepcopy(prepared)
        input_hash = array_identity(prepared)
        if array_identity(pristine) != input_hash:
            raise ValueError('pristine KG arrays changed')
        records = []
        def actual(overrides):
            index = len(records)
            case = copy.deepcopy(pristine)
            if array_identity(case) != input_hash:
                raise ValueError('KG arrays differ before evaluation')
            os.environ['V15_BASELINE_RAW_CAPTURE'] = str(side / ('kg_raw_%03d.pickle' % index))
            result, proof = capture(case, overrides, evaluate, window_days)
            arrays_after = {'case': array_identity(case), 'pristine': array_identity(pristine), 'original': array_identity(prepared)}
            record = {'index': index, 'requested_settings_sha256': runner.fingerprint(overrides), 'requested_changes': {key: value for key, value in overrides.items() if key not in settings or runner.fingerprint(value) != runner.fingerprint(settings[key])}, 'arrays_before_sha256': input_hash, 'arrays_after': arrays_after, 'proof': proof}
            with (side / ('kg_actual_%03d.json' % index)).open('x') as stream:
                json.dump(record, stream, indent=2)
            if any(value != input_hash for value in arrays_after.values()):
                raise ValueError('KG prepared arrays mutated; no phase completion')
            records.append(record)
            return result, proof
        baseline, proof = actual(settings)
        for key in ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256'):
            if proof[key] != expected[key]:
                raise ValueError('corrected initial baseline differs before KG: ' + key)
        options, grey = kg.template_options(request['kg_template']['path'])
        if grey or not options:
            raise ValueError('mandatory KG options missing/grey')
        singles = sum(len(values) for values in options.values())
        gain = next((baseline[key] for key in ('gain_pct', 'gain', 'final_gain') if key in baseline), None)
        progress = {}
        phase = kg.run(request['sym_side'], settings, runner.selected(request['previous_defaults_source']), request['kg_template']['path'], lambda overrides: actual(overrides)[0], gain, progress=progress, max_combos=singles + request.get('pair_tail_limit', 4), budget_s=request.get('pair_tail_budget_s', 600), minimum_trades=baseline.get('sanitized_floor'), log=print)
        if phase.get('error') or phase.get('grey_rows') or phase.get('singles_unmeasured') or phase.get('singles_never_ran'):
            raise ValueError('mandatory KG phase incomplete; no row pass permitted')
        winner = dict(settings)
        if phase.get('promoted'):
            winner.update(phase['overrides'])
        identity = {'runtime': request['previous_runtime_manifest']['sha256'], 'materialization': request['materialization_receipt']['sha256'], 'npz': request['npz_sha256'], 'cutoff': proof['input_cutoff_sha256'], 'baseline': control_pin['sha256'], 'template': request['kg_template']['sha256'], 'kg_helper': helper_pin['sha256']}
        progress.update(cumulative_overrides=winner, cumulative_gain=phase['best']['gain'] if phase.get('promoted') else gain, kindergarten_first_contract={'schema': 'v15_kindergarten_phase_diagnostic/1', 'completed': True, 'identity': identity, 'input_chain_sha256': runner.fingerprint(settings), 'output_chain_sha256': runner.fingerprint(winner), 'results_sha256': runner.fingerprint(phase), 'order': 'baseline; blanket off and KG; worst-first row pass; pinpoint settings'})
        temporary, final = side / 'kg_progress.pending.json', side / 'kg_progress.json'
        with temporary.open('x') as stream:
            json.dump(progress, stream, indent=2)
        os.replace(temporary, final)
        restored = json.loads(final.read_text())
        contract = restored['kindergarten_first_contract']
        if contract['identity'] != identity or contract['results_sha256'] != runner.fingerprint(restored['kindergarten_first']) or contract['output_chain_sha256'] != runner.fingerprint(restored['cumulative_overrides']):
            raise ValueError('persisted KG winner/resume identity changed')
        receipt = {'schema': 'v15_kg_phase_probe/1', 'status': 'ACTUAL_HELPER_PHASE_AND_PERSISTENCE_DIAGNOSTIC_NO_ROW_PASS', 'identity': identity, 'single_option_count': singles, 'actual_evaluations': len(records), 'progress_sha256': sha(final), 'initial_baseline_match': True, 'scope': 'Actual source helper and actual engine cells; explicit diagnostic persistence contract. Does not execute or prove full pilot row-pass/resume dispatch.'}
        with (side / 'kg_phase_receipt.json').open('x') as stream:
            json.dump(receipt, stream, indent=2)
        raise SystemExit(0)
    runner.capture_actual = measured
    sys.argv = [args.oracle_runner, '--request', args.request]
    runner.main()


if __name__ == '__main__':
    main()
