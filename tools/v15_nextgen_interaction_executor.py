"""Isolated prospective factorial measurements; never changes a starting baseline or production policy."""
import copy
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import sys

try:
    import v15_nextgen_baseline_guard as guard
except ModuleNotFoundError:
    from tools import v15_nextgen_baseline_guard as guard


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)


def operation_identity(change):
    return guard.fingerprint({'field': change['field'], 'value': change['value']})


def pair_identity(x, y):
    return guard.fingerprint(sorted((operation_identity(x), operation_identity(y))))


def prepared_arrays_identity(prepared):
    arrays = []
    def visit(value, path):
        if hasattr(value, 'dtype') and hasattr(value, 'shape') and hasattr(value, 'tobytes'):
            content = guard.fingerprint(value) if value.dtype.kind == 'O' else hashlib.sha256(value.tobytes(order='C')).hexdigest()
            arrays.append((path, str(value.dtype), list(value.shape), content))
        elif isinstance(value, dict):
            for key, item in sorted(value.items(), key=lambda item: str(item[0])):
                visit(item, path + '/' + str(key))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, path + '/' + str(index))
    visit(prepared, '')
    if not arrays:
        raise ValueError('actual prepared arrays required')
    return guard.fingerprint(arrays)


def derived_effects(gains):
    return {'X_minus_B': gains['X'] - gains['B'], 'Y_minus_B': gains['Y'] - gains['B'], 'Z_minus_X': gains['Z'] - gains['X'], 'Z_minus_Y': gains['Z'] - gains['Y'], 'Z_minus_B': gains['Z'] - gains['B'], 'interaction_Z_minus_X_minus_Y_plus_B': gains['Z'] - gains['X'] - gains['Y'] + gains['B']}


def pure_factorial(records, x, y):
    configs = {}
    for name, row in records.items():
        values = row['actual_engine_proof'].get('effective_config_values') or []
        if len(values) != 1:
            return False
        configs[name] = guard.decoded(values[0])
    baseline = configs['B']
    expected = {'B': baseline, 'X': dict(baseline, **{x['field']: x['value']}), 'Y': dict(baseline, **{y['field']: y['value']}), 'Z': dict(baseline, **{x['field']: x['value'], y['field']: y['value']})}
    return all(guard.fingerprint(configs[name]) == guard.fingerprint(config) for name, config in expected.items())


def assert_baseline(pin, settings, proof, window_context):
    guard.assert_settings(pin, settings)
    if pin.get('window_context') != window_context:
        raise ValueError('accepted baseline window context changed')
    previous = pin['previous_actual_engine_proof']
    expected = []
    for item in previous.get('effective_config_values') or []:
        config = dict(guard.decoded(item))
        for key, entry in pin.get('neutral_new_keys', {}).items():
            if key in config:
                raise ValueError('neutral field shadows previous config')
            config[key] = guard.decoded(entry['value'])
        expected.append(config)
    if not expected or guard.fingerprint(expected) != proof.get('effective_config_sha256'):
        raise ValueError('accepted effective baseline configuration drift')
    if any(previous.get(key) != proof.get(key) for key in ('input_cutoff_sha256', 'execution_ledger_sha256', 'result_sha256')):
        raise ValueError('accepted baseline cutoff, execution ledger or result drift')


def validate_inputs(context, evaluate):
    root = Path(context['runtime_root']).resolve()
    if Path(inspect.getsourcefile(evaluate)).resolve() != root / 'tools/opt/v12_pilot.py':
        raise ValueError('evaluator escaped frozen runtime')
    engine = sys.modules.get('v12_quick_engine')
    if engine is None or Path(engine.__file__).resolve() != root / 'v12_quick_engine.py':
        raise ValueError('actual vector engine escaped frozen runtime')
    for relative, expected in context['source_sha256'].items():
        path = root / relative
        if path.is_symlink() or sha(path) != expected:
            raise ValueError('frozen source changed: ' + relative)
    if not {'v12_quick_engine.py', 'tools/opt/v12_pilot.py', 'tools/opt/evaluate_v12.py'} <= set(context['source_sha256']):
        raise ValueError('complete direct engine/evaluator source pins required')
    npz = Path(context['npz_path']).resolve()
    if root not in npz.parents or npz.is_symlink() or sha(npz) != context['npz_sha256']:
        raise ValueError('frozen NPZ identity changed')
    receipt_pin = context['materialization_receipt']
    if sha(receipt_pin['path']) != receipt_pin['sha256']:
        raise ValueError('materialization receipt changed')
    materialized = json.loads(Path(receipt_pin['path']).read_text())
    if materialized.get('reference_root') != str(root):
        raise ValueError('materialized runtime root differs')
    routing_pin, runtime_pin = context['routing_manifest'], context['runtime_manifest']
    if sha(routing_pin['path']) != routing_pin['sha256'] or sha(runtime_pin['path']) != runtime_pin['sha256']:
        raise ValueError('runtime or routing manifest changed')
    routing = json.loads(Path(routing_pin['path']).read_text())
    runtime = json.loads(Path(runtime_pin['path']).read_text())
    for relative, metadata in materialized['files'].items():
        path = root / relative
        if relative in routing.get('source_sha256', {}):
            if runtime['files'].get(relative) != routing['source_sha256'][relative] or sha(path) != routing['candidate_sha256'][relative]:
                raise ValueError('explicit routing transformation lineage changed')
            continue
        stat = path.stat()
        if path.is_symlink() or any(getattr(stat, 'st_' + field) != metadata[key] for field, key in [('size', 'bytes'), ('mtime_ns', 'mtime_ns'), ('ino', 'inode')]):
            raise ValueError('materialized source changed since verified hashes: ' + relative)


def execute_quartet(prepared, pin, x, y, context, output, eligible, pin_loading=None):
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    if context.get('phase') != 'postbaseline_research' or eligible(x, y, pin['settings']) is not True:
        raise ValueError('explicit postbaseline phase and actual current-domain proof required')
    if not x.get('field') or not y.get('field') or x['field'] == y['field']:
        raise ValueError('two distinct variable identities required')
    baseline = copy.deepcopy(pin['settings'])
    oracle_pin = context['baseline_oracle']
    if sha(oracle_pin['path']) != oracle_pin['sha256']:
        raise ValueError('independent baseline oracle source changed')
    oracle = json.loads(Path(oracle_pin['path']).read_text())
    if oracle['previous_actual_engine_proof'] != pin['previous_actual_engine_proof']:
        raise ValueError('self-asserted baseline proof differs from independent oracle')
    source = oracle['accepted_source']
    if sha(source['path']) != source['sha256']:
        raise ValueError('accepted reader input source changed')
    accepted = json.loads(Path(source['path']).read_text())
    for key in source.get('settings_pointer', []):
        accepted = accepted[key]
    if pin_loading is not None:
        resolved = guard.load_pin(**pin_loading)
        if guard.fingerprint(resolved) != guard.fingerprint(pin):
            raise ValueError('reader projection differs from actual guard.load_pin result')
    else:
        expected_reader = dict(accepted)
        expected_reader.update({key: guard.decoded(entry['value']) for key, entry in pin.get('neutral_new_keys', {}).items()})
        if guard.fingerprint(baseline) != guard.fingerprint(expected_reader):
            raise ValueError('accepted reader inputs were removed, expanded or replaced')
    for change in (x, y):
        if change['field'] in baseline and guard.fingerprint(baseline[change['field']]) == guard.fingerprint(change['value']):
            raise ValueError('identity treatment is not an interaction experiment')
    validate_inputs(context, evaluate_prepared_sanitized)
    original_arrays = prepared_arrays_identity(prepared)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 'prospective_quartet/1', 'status': 'measuring', 'context': context, 'x': x, 'y': y, 'canonical_pair_id': pair_identity(x, y), 'components': {}, 'baseline_modified': False, 'runtime_hooked': False, 'source_sha256': sha(__file__), 'recorded_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}
    configs = {'B': baseline, 'X': dict(baseline, **{x['field']: x['value']}), 'Y': dict(baseline, **{y['field']: y['value']}), 'Z': dict(baseline, **{x['field']: x['value'], y['field']: y['value']})}
    try:
        for name, settings in configs.items():
            case = copy.deepcopy(prepared)
            before_arrays = prepared_arrays_identity(case)
            if before_arrays != original_arrays:
                raise ValueError('component prepared arrays differ before execution')
            result, proof = guard.capture_actual(case, settings, evaluate_prepared_sanitized, context['window_days'])
            ledger = result.get('execution_ledger')
            record = {'component': name, 'requested_reader_settings': settings, 'actual_engine_proof': proof, 'prepared_arrays_before_sha256': before_arrays, 'prepared_arrays_after_sha256': prepared_arrays_identity(case), 'gain_pct': guard.normalized(result.get('gain_pct')), 'valid': result.get('valid'), 'invalid_reason': result.get('invalid_reason'), 'trades': result.get('trades'), 'reader_transformations': proof['reader_transformations']}
            ledger_path = output / (name + '_execution_ledger.json')
            write_new(ledger_path, guard.normalized(ledger))
            record['ledger'] = {'file': ledger_path.name, 'sha256': sha(ledger_path)}
            record['eval_id'] = 'prospective1:' + guard.fingerprint({'context': context, 'settings': settings, 'proof': proof})
            record_path = output / (name + '_measurement.json')
            write_new(record_path, record)
            manifest['components'][name] = {'file': record_path.name, 'sha256': sha(record_path), 'eval_id': record['eval_id']}
            if name == 'B':
                assert_baseline(pin, baseline, proof, context['window_context'])
            validate_inputs(context, evaluate_prepared_sanitized)
            if prepared_arrays_identity(prepared) != original_arrays:
                raise ValueError('shared original prepared arrays mutated')
        gains = {name: json.loads((output / item['file']).read_text()) for name, item in manifest['components'].items()}
        valid = all(row['valid'] is True and isinstance(row['gain_pct'], (float, int)) and math.isfinite(row['gain_pct']) and not row['invalid_reason'] for row in gains.values())
        factorial = pure_factorial(gains, x, y)
        manifest['pure_effective_config_factorial'] = factorial
        manifest['status'] = 'measured_operation_interaction' if valid else 'measured_diagnostic_constraint_or_invalid'
        if valid:
            manifest['interaction_delta'] = gains['Z']['gain_pct'] - gains['X']['gain_pct'] - gains['Y']['gain_pct'] + gains['B']['gain_pct']
            manifest['joint_delta_vs_baseline'] = gains['Z']['gain_pct'] - gains['B']['gain_pct']
            manifest['derived_diagnostics'] = derived_effects({name: row['gain_pct'] for name, row in gains.items()})
        baseline_effective = gains['B']['actual_engine_proof']['effective_config_values'][0]
        manifest['effective_expanded_fields_by_component'] = {name: sorted(key for key in set(baseline_effective) | set(row['actual_engine_proof']['effective_config_values'][0]) if guard.fingerprint(baseline_effective.get(key)) != guard.fingerprint(row['actual_engine_proof']['effective_config_values'][0].get(key))) for name, row in gains.items()}
        if guard.fingerprint(pin['settings']) != guard.fingerprint(baseline):
            raise ValueError('accepted baseline mutated')
    except BaseException as error:
        manifest.update(status='failed_or_partial_not_verified', error=repr(error))
        write_new(output / 'quartet.json', manifest)
        raise
    write_new(output / 'quartet.json', manifest)
    return manifest


def load_measured_quartet(path, expected_sha256):
    path = Path(path)
    if sha(path) != expected_sha256:
        raise ValueError('quartet receipt changed')
    proof = json.loads(path.read_text())
    if proof.get('schema') != 'prospective_quartet/1' or proof.get('status') != 'measured_operation_interaction' or set(proof.get('components') or {}) != {'B', 'X', 'Y', 'Z'}:
        raise ValueError('complete valid measured quartet required')
    gains, records = {}, {}
    for name, item in proof['components'].items():
        component = path.parent / item['file']
        if component.parent != path.parent or sha(component) != item['sha256']:
            raise ValueError('component source changed')
        record = json.loads(component.read_text())
        if record.get('valid') is not True or record.get('invalid_reason') or not isinstance(record.get('gain_pct'), (int, float)) or not math.isfinite(record['gain_pct']):
            raise ValueError('invalid or nonfinite component cannot verify interaction')
        ledger = path.parent / record['ledger']['file']
        if ledger.parent != path.parent or sha(ledger) != record['ledger']['sha256']:
            raise ValueError('actual ledger source changed')
        observed_ledger = json.loads(ledger.read_text())
        if guard.fingerprint(observed_ledger) != record['actual_engine_proof']['execution_ledger_sha256']:
            raise ValueError('ledger differs from actual engine capture')
        gains[name] = record['gain_pct']
        records[name] = record
    if proof['pure_effective_config_factorial'] != pure_factorial(records, proof['x'], proof['y']):
        raise ValueError('effective configuration evidence tier changed')
    baseline = records['B']['requested_reader_settings']
    expected = {'B': baseline, 'X': dict(baseline, **{proof['x']['field']: proof['x']['value']}), 'Y': dict(baseline, **{proof['y']['field']: proof['y']['value']}), 'Z': dict(baseline, **{proof['x']['field']: proof['x']['value'], proof['y']['field']: proof['y']['value']})}
    if any(guard.fingerprint(records[name]['requested_reader_settings']) != guard.fingerprint(settings) for name, settings in expected.items()):
        raise ValueError('requested operations differ beyond the two interventions')
    if len({row['prepared_arrays_before_sha256'] for row in records.values()}) != 1:
        raise ValueError('component prepared arrays differ')
    if len({row['actual_engine_proof']['input_cutoff_sha256'] for row in records.values()}) != 1:
        raise ValueError('component input windows differ')
    if proof['interaction_delta'] != gains['Z'] - gains['X'] - gains['Y'] + gains['B'] or proof['joint_delta_vs_baseline'] != gains['Z'] - gains['B']:
        raise ValueError('measured quartet arithmetic changed')
    if proof['derived_diagnostics'] != derived_effects(gains) or proof['canonical_pair_id'] != pair_identity(proof['x'], proof['y']):
        raise ValueError('derived marginals or canonical pair identity changed')
    return proof


def prioritize_confirmation(proposals, measured, current_context, eligible):
    result = []
    for proposal in proposals:
        item = dict(proposal, measured_priority=0.0, measured_sources=[], next_phase='independent_window_confirmation', current_confirmation_verified=False)
        for path, digest in measured:
            proof = load_measured_quartet(path, digest)
            if proof['context'] != current_context or eligible(proof['x'], proof['y'], proposal) is not True:
                continue
            if pair_identity(proposal['x'], proposal['y']) == proof['canonical_pair_id']:
                item['measured_priority'] = max(item['measured_priority'], proof['joint_delta_vs_baseline'])
                item['measured_sources'].append(digest)
        result.append(item)
    return sorted(result, key=lambda item: (-item['measured_priority'], str(item.get('id', ''))))
