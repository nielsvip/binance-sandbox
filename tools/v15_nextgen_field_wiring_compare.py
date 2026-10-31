"""Independent FALSE control before existing-switch wiring variants; never NN evidence."""
import argparse
import datetime
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

FIELDS = {'FIXED_QUANTITY_ENABLED', 'WT_MOMENTUM_EXIT_ENABLED'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--oracle-runner', required=True)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    if request.get('experiment_role') != 'existing_switch_wiring_diagnostic':
        raise ValueError('explicit diagnostic role required')
    variant = request.get('requested_variant', {})
    if set(variant) - FIELDS or any(type(value) is not bool for value in variant.values()):
        raise ValueError('only declared boolean wiring variants allowed')
    old_pin = request['unchanged_corrected_control']
    if sha(old_pin['path']) != old_pin['sha256']:
        raise ValueError('independent prior corrected control changed')
    old = json.loads(Path(old_pin['path']).read_text())['previous_actual_engine_proof']
    spec = importlib.util.spec_from_file_location('field_wiring_oracle', args.oracle_runner)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    capture = runner.capture_actual
    evidence = {}
    def measured(prepared, settings, evaluate, window_days):
        if any(settings.get(field) is not False for field in FIELDS):
            raise ValueError('this control contract requires independently accepted FALSE inputs')
        import v12_quick_engine as engine
        import numpy as np
        scorer = importlib.import_module('vec_decisions.mtf_exit_scorer')
        root = Path(request['reference_root']).resolve()
        if root not in Path(scorer.__file__).resolve().parents:
            raise ValueError('actual scorer escaped pinned runtime')
        native = []
        def observe(original, label):
            def wrapped(*values, **keywords):
                result = original(*values, **keywords)
                outputs = result if isinstance(result, tuple) else (result,)
                fingerprints = []
                for output in outputs:
                    if output is None:
                        fingerprints.append(None)
                    else:
                        array = np.ascontiguousarray(output)
                        fingerprints.append({'shape': list(array.shape), 'dtype': str(array.dtype), 'sha256': hashlib.sha256(array.tobytes()).hexdigest()})
                native.append({'phase': evidence.get('phase'), 'function': label, 'native_output_fingerprints': fingerprints})
                return result
            return wrapped
        originals = [(engine, 'compute_regime_sizing_mult', engine.compute_regime_sizing_mult), (scorer, 'score_array_parts', scorer.score_array_parts)]
        raw = os.environ.get('V15_BASELINE_RAW_CAPTURE')
        try:
            for module, name, original in originals:
                setattr(module, name, observe(original, module.__name__ + '.' + name))
            evidence['phase'] = 'FALSE_control'
            if raw:
                os.environ['V15_BASELINE_RAW_CAPTURE'] = raw + '.false_control.pickle'
            base_result, base = capture(prepared, settings, evaluate, window_days)
            base_time = utc()
            cfgs = base['effective_config_values']
            if len(cfgs) != 1 or set(cfgs[0]) != set(old['effective_config_values'][0]) | FIELDS or any(cfgs[0][field] is not False for field in FIELDS):
                raise ValueError('new FALSE fields/configuration scope differs')
            projected = {key: value for key, value in cfgs[0].items() if key not in FIELDS}
            if projected != old['effective_config_values'][0]:
                raise ValueError('FALSE control changed previous effective configuration')
            for key in ('execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256'):
                if base[key] != old[key]:
                    raise ValueError(f'FALSE wiring control changed prior actual output: {key}')
            requested = dict(settings, **variant)
            evidence.update(false_control_evaluated_at_utc=base_time, false_control_proof=base, requested_variant=variant, requested_settings_sha256=runner.fingerprint(requested), baseline_accepted_settings_sha256=runner.fingerprint(settings))
            if variant:
                evidence['phase'] = 'explicit_variant'
                if raw:
                    os.environ['V15_BASELINE_RAW_CAPTURE'] = raw + '.explicit_variant.pickle'
                result, proof = capture(prepared, requested, evaluate, window_days)
            else:
                result, proof = base_result, base
            for field in FIELDS:
                if proof['effective_config_values'][0][field] is not requested[field]:
                    raise ValueError('requested real boolean field did not reach actual effective configuration')
            evidence.update(variant_evaluated_at_utc=utc(), native_observations=native, native_scorer_sha256=sha(scorer.__file__))
            evidence.pop('phase', None)
            return result, proof
        finally:
            for module, name, original in reversed(originals):
                setattr(module, name, original)
            if raw:
                os.environ['V15_BASELINE_RAW_CAPTURE'] = raw
    def dumps(value, *values, **keywords):
        if isinstance(value, dict) and value.get('schema') == 'v15_previous_baseline_oracle/1':
            value = dict(value)
            proof = value.pop('previous_actual_engine_proof')
            proof['oracle']['baseline_accepted_settings_sha256'] = proof['oracle'].pop('accepted_settings_sha256')
            proof['oracle']['requested_settings_sha256'] = evidence['requested_settings_sha256']
            value.update(schema='v15_existing_switch_wiring_diagnostic/1', status='MEASURED_WIRING_VARIANT_NOT_ACCEPTED_HISTORY_OR_NN_GAIN', baseline_source=value.pop('accepted_source'), measured_variant_engine_proof=proof, wiring_evidence=evidence, independent_control=old_pin, scope='FALSE control must match independent previous corrected ledger/result/cutoff and prior-config projection; TRUE values are explicit research variations, never claimed previously accepted.')
        elif isinstance(value, dict) and value.get('phase') == 'PREVIOUS_BASELINE_ORACLE_SAVED':
            value = dict(value, phase='EXISTING_SWITCH_WIRING_DIAGNOSTIC_SAVED')
        return json.dumps(value, *values, **keywords)
    runner.capture_actual = measured
    runner.json = types.SimpleNamespace(loads=json.loads, dumps=dumps)
    sys.argv = [args.oracle_runner, '--request', args.request]
    runner.main()
    if sha(old_pin['path']) != old_pin['sha256']:
        raise ValueError('independent corrected control mutated')


if __name__ == '__main__':
    main()
