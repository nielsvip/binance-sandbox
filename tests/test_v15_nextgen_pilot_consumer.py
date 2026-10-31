import ast
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import v15_nextgen_pilot_consumer as C


PILOT = Path('data/reports/nextgen_consumer_integration_20261031/v15_pilot.py')


def extract(name, namespace):
    tree = ast.parse(PILOT.read_text())
    node = next(item for item in ast.walk(tree) if isinstance(item, ast.FunctionDef) and item.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(PILOT), 'exec'), namespace)
    return namespace[name]


def fixture(tmp_path):
    import openpyxl
    path = tmp_path / 'v12_quick_engine.py'
    path.write_text('source fixture only')
    digest = C.priority.evidence.sha(path)
    row = dict(tab='EXIT', row=3, switch='FLAG', cand=True)
    workbook = openpyxl.Workbook()
    workbook.active.title = 'EXIT'
    workbook.active.append(['title'])
    workbook.active.append(['Switch', 'cand', 'AVG_DELTA'])
    workbook.active.append(['FLAG', True, -1])
    template = tmp_path / 'TEMPLATE_FIXTURE.xlsx'
    workbook.save(template)
    workbook.close()
    order = C.bind_row_order(dict(path=str(template), sha256=C.priority.evidence.sha(template)), ['EXIT'])
    epoch = C.priority.bind_epoch([dict(asset=path.name, path=str(path), sha256=digest), dict(asset=template.name, path=str(template), sha256=C.priority.evidence.sha(template))])
    contexts = []
    def context(chain):
        result = dict(phase='post_kg_first_worst_pass', row_order='worst_first', row_order_proof=order, current_epoch=epoch, scientific_identity=dict(engine_sha256=digest, npz_sha256='b'*64, accepted_chain_sha256=C.priority.evidence.guard.fingerprint(chain), wiring_epoch_sha256=epoch['id'], sym_side='FIXTURE_LONG', window=dict(window_days=30, offset_days=0, input_cutoff_sha256='d'*64)))
        contexts.append(result)
        return result
    reservations = []
    def reserve(allocation, current):
        identity = C.priority.evidence.guard.fingerprint(dict(allocation=allocation, scientific_identity=C.priority.scientific_identity(current)))
        target = tmp_path / ('reservation_' + str(len(reservations)) + '.json')
        receipt = dict(status='reserved', allocation_sha256=identity)
        target.write_text(json.dumps(receipt))
        reservations.append(target)
        return json.loads(target.read_text())
    classify = lambda cell, current: dict(row, eligible=True, current_epoch_measured=True, pos_sym=0)
    consumer = C.FirstPassConsumer(context, classify, reserve, {}, lambda row, current: 2, lambda row, current: 'newly_wired')
    consumer.begin_batch({'FLAG': False})
    return row, consumer, contexts, reservations


def test_actual_row_disc_captures_exact_legal_domain_before_sampling(tmp_path):
    row, consumer, _, _ = fixture(tmp_path)
    legal = dict(yellow={'FILTER=15m'}, hdrs=['FILTER=15m'], h2f={'FILTER=15m': dict(filter='FILTER', opt='15m')}, excluded_tab_level=[], excluded_unwired=[], type_skipped=[], mand_notes=[])
    ns = dict(_nextgen_consumer=consumer, _nextgen_capture_legal=C.capture_legal, os=SimpleNamespace(environ={}), _header_disc_cache={}, tab_level_filters=lambda *args: [], map_key_for_symside=lambda *args: 'FIXTURE', new_symside='FIXTURE_LONG', mandatory_bases_for_tab=lambda *args: [], _header_disc_precompute=lambda *args: {}, header_maps={'EXIT': {}}, defaults={}, ever_yellow_cells=lambda *args: set(), WT_CORE_MANDATORY_BASES=set(), _header_disc_combine=lambda *args: copy.deepcopy(legal), _mandatory_note=lambda *args: None)
    info = dict(kind='eval')
    extract('_row_disc', ns)(info, 'EXIT', 'FLAG', True)
    info['hdrs'].clear()
    assert info['nextgen_legal']['hdrs'] == ('FILTER=15m',)
    assert tuple(info['nextgen_legal']['h2f']) == ('FILTER=15m',)
    with pytest.raises(TypeError):
        info['nextgen_legal']['h2f']['FILTER=15m']['opt'] = 'D'
    ns.update(wb={'EXIT': SimpleNamespace(cell=lambda *args, **kwargs: SimpleNamespace(font=None))}, UNWIRED_SWITCHES=set(), UNWIRED_FILTERS=set(), GLOBAL_ONLY_KEYS=set(), known_config_fields=lambda: {'FLAG'}, _switch_overrides=lambda switch, cand: {switch: cand}, _parse_opt_value=lambda cand, default: cand, _switch_type_violation=lambda *args: None, _maxaug_is_zero=lambda *args: False, _brain_naked=True, carry_decide=lambda *args: pytest.fail('prepolicy census must not invoke carry suppression'), _brain_row_out=lambda *args: pytest.fail('prepolicy census must not invoke old brain suppression'))
    original = extract('_nextgen_prepolicy_discovery', ns)('EXIT', 3, 'FLAG', True)
    assert original['kind'] == 'eval'
    assert original['nextgen_legal']['hdrs'] == ('FILTER=15m',)


@pytest.mark.parametrize('reason', ['BRAIN_SAMPLED_OUT', 'CARRIED_DEAD: old era', 'SKIPPED_SAMPLING(pos_sym=0)', 'ZERO_FORMULA_ROW: old zeros'])
def test_actual_row_plan_restores_low_frequency_new_yellow_without_static_measurement_cache(tmp_path, reason):
    row, consumer, contexts, reservations = fixture(tmp_path)
    legal = C.capture_legal(dict(hdrs=['FILTER=15m', 'FILTER=1h'], h2f={'FILTER=15m': dict(filter='FILTER', opt='15m'), 'FILTER=1h': dict(filter='FILTER', opt='1h')}))
    sampled = dict(kind='skip', reason=reason, hdrs=[], h2f={}, nextgen_prepolicy=dict(kind='eval', nextgen_legal=legal))
    ns = dict(_nextgen_consumer=consumer, _row_static=lambda *args: sampled, cumulative_overrides={'FLAG': False}, defaults={}, _parse_opt_value=lambda value, default: value, _switch_overrides=lambda switch, cand: {switch: cand}, _same_val=lambda a,b:a==b, sanitize_overrides=lambda value, defaults:(value, {}), initial_overrides={'FLAG': False}, _v17_on=True, _SETTLED_NAKED_ON=False)
    plan = extract('_row_plan', ns)
    first = plan('EXIT', 3, 'FLAG', True)
    assert first['items'][0][0] == 'naked'
    assert {label for label, settings in first['items']} == {'naked', 'FILTER=15m', 'FILTER=1h'}
    assert sampled['hdrs'] == [] and sampled['kind'] == 'skip'
    ns['cumulative_overrides'] = {'FLAG': True, 'OTHER_WINNER': '15m'}
    second = plan('EXIT', 3, 'FLAG', True)
    assert all(settings['OTHER_WINNER'] == '15m' for label, settings in second['items'])
    assert all(label != 'naked' for label, settings in second['items'])
    assert len(reservations) == 2
    assert len({item['scientific_identity']['accepted_chain_sha256'] for item in contexts}) == 2
    assert second['static']['nextgen_allocation']['current_epoch_measured_cells'] == 0


def test_resume_or_print_cannot_claim_first_pass_completion(tmp_path):
    row, consumer, _, _ = fixture(tmp_path)
    result = C.first_pass_completion([row], [dict(row=row, status='evaluated')], [])
    assert result['accounting_complete'] and not result['complete']
    assert not C.first_pass_completion([row], [], [])['accounting_complete']
    with pytest.raises(ValueError, match='source-bound'):
        consumer.require_first_pass_complete([row], {'done': {'old': {'delta': 0}}, 'printed_workbook_complete': True}, {'FLAG': True})


def test_actual_row_done_does_not_turn_zero_eval_policy_skip_into_measured_completion(tmp_path):
    row, consumer, _, _ = fixture(tmp_path)
    progress, logged = {}, []
    ns = dict(_delta_log=logged.append, _v18_setsha_on=False, utcnow=lambda: '2026-10-31T00:00:00.000000Z', new_symside='FIXTURE_LONG', nav_mode='sequential', _t=SimpleNamespace(time=lambda: 1.0), _row_t0=0.0, cumulative_gain=0.0, cumulative_overrides={'FLAG': False}, _nextgen_consumer=consumer, progress=progress)
    extract('_row_done', ns)('EXIT', 3, 'FLAG', True, 0, None, False)
    assert logged[0]['label'] == 'ROW_DONE' and logged[0]['n_evals'] == 0
    receipt = progress['nextgen_first_pass_rows']['EXIT!3:FLAG=True']
    assert receipt['complete'] is False and receipt['status'] == 'unknown_no_actual_plan'


def test_review_patch_defers_research_and_preserves_exact_phase_source():
    source = PILOT.read_text()
    assert source.index('if _nextgen_consumer is None:\n        if _v18_phases_on:') < source.index('for qi, (sname, rr, switch, cand) in enumerate(pending_queue):')
    assert source.index('require_first_pass_complete(queue, progress, cumulative_overrides)') > source.index('for qi, (sname, rr, switch, cand) in enumerate(pending_queue):')
    assert 'queue = [(sname, rr, sw, cand) for sname in tabs for (rr, sw, cand) in per_tab_rows[sname]]' in source
    assert 'kindergarten_first_contract' in source


def test_stale_actual_measurement_remains_unknown_and_batch_end_detects_source_drift(tmp_path):
    row, consumer, contexts, _ = fixture(tmp_path)
    old = C.priority.scientific_identity(contexts[-1])
    old['accepted_chain_sha256'] = 'f' * 64
    identity = C.priority.evidence.guard.fingerprint(dict(row=row, filter='FILTER', opt='15m', scientific_context=old))
    path = tmp_path / 'old_measurement.json'
    path.write_text(json.dumps(dict(schema='current_yellow_measurement/1', cell_identity=identity, scientific_identity=old, evaluation_status='evaluated_valid', engine_proof=dict(effective_config_sha256='1'*64, execution_ledger_sha256='2'*64, result_sha256='3'*64, input_cutoff_sha256='d'*64))))
    proof = C.priority.bind_measurement(dict(path=str(path), sha256=C.priority.evidence.sha(path)))
    consumer.classify = lambda cell, context: dict(row, eligible=True, measurement_proof=proof)
    legal = C.capture_legal(dict(hdrs=['FILTER=15m'], h2f={'FILTER=15m': dict(filter='FILTER', opt='15m')}))
    result = consumer.apply(dict(kind='eval', hdrs=[], h2f={}), legal, row, {'FLAG': True})
    assert result['nextgen_allocation']['current_epoch_measured_cells'] == 0
    assert result['hdrs'] == ['FILTER=15m']
    assert result['nextgen_allocation']['selected'][0]['measurement_status'] == 'different_cell_or_scientific_context'
    (tmp_path / 'v12_quick_engine.py').write_text('changed during batch')
    with pytest.raises(ValueError, match='changed during'):
        consumer.end_batch()


def test_actual_worker_artifact_binding_rejects_result_tamper_and_shared_thread(tmp_path, monkeypatch):
    import multiprocessing
    row, consumer, contexts, _ = fixture(tmp_path)
    current = contexts[-1]
    epoch = current['current_epoch']
    engine = SimpleNamespace(__file__=str(tmp_path / 'v12_quick_engine.py'))
    monkeypatch.setitem(sys.modules, 'v12_quick_engine', engine)
    monkeypatch.setitem(sys.modules, 'tools.opt.v12_pilot', SimpleNamespace(evaluate_prepared_sanitized=lambda *args: None))
    directory = tmp_path / 'worker_evidence'
    directory.mkdir()
    capture = dict(directory=str(directory), source_epoch=epoch, npz_sha256=epoch['sources'][1]['sha256'])
    result = dict(valid=True, gain_pct=0.0, execution_ledger=[])
    configs = [{'FLAG': True}]
    proof = dict(input_cutoff_sha256='d'*64, effective_config_values=configs, effective_config_sha256=C.priority.evidence.guard.fingerprint(configs), execution_ledger_sha256=C.priority.evidence.guard.fingerprint([]), result_sha256=C.priority.evidence.guard.fingerprint(C.priority.evidence.guard.result_fields(result)))
    monkeypatch.setattr(C.priority.evidence.guard, 'capture_actual', lambda *args: (result, proof))
    with pytest.raises(ValueError, match='isolated fork'):
        C.evaluate_fork_worker({}, {'FLAG': True}, 30, capture)
    monkeypatch.setattr(multiprocessing, 'current_process', lambda: SimpleNamespace(name='ForkProcess-test-fixture'))
    assert C.evaluate_fork_worker({}, {'FLAG': True}, 30, capture) is result
    path = next(directory.glob('*.json'))
    source = dict(path=str(path), sha256=C.priority.evidence.sha(path))
    scientific = dict(current['scientific_identity'], npz_sha256=capture['npz_sha256'])
    bound_context = dict(current, scientific_identity=scientific)
    cell = dict(row, filter='FILTER', opt='15m')
    bound = C.bind_worker_measurement(source, cell, bound_context, {'FLAG': True})
    assert bound['record']['evaluation_status'] == 'evaluated_valid'
    data = json.loads(path.read_text())
    original = copy.deepcopy(data)
    data['result'].update(valid=False, invalid_reason='DATA_ERROR: missing indicator')
    data['actual_engine_proof']['result_sha256'] = C.priority.evidence.guard.fingerprint(C.priority.evidence.guard.result_fields(data['result']))
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='execution failure'):
        C.bind_worker_measurement(dict(path=str(path), sha256=C.priority.evidence.sha(path)), cell, bound_context, {'FLAG': True})
    data = original
    data['result'].update(valid=False, invalid_reason='TIM 90.0% >80% (vomit)')
    data['actual_engine_proof']['result_sha256'] = C.priority.evidence.guard.fingerprint(C.priority.evidence.guard.result_fields(data['result']))
    path.write_text(json.dumps(data))
    infeasible = C.bind_worker_measurement(dict(path=str(path), sha256=C.priority.evidence.sha(path)), cell, bound_context, {'FLAG': True})
    assert infeasible['record']['evaluation_status'] == 'evaluated_inadmissible'
    data = original
    data['result']['gain_pct'] = 123.0
    path.write_text(json.dumps(data))
    changed = dict(path=str(path), sha256=C.priority.evidence.sha(path))
    with pytest.raises(ValueError, match='artifacts mismatch'):
        C.bind_worker_measurement(changed, cell, bound_context, {'FLAG': True})


def test_chained_actual_row_receipts_control_completion_and_resume_not_done_booleans(tmp_path, monkeypatch):
    import multiprocessing
    row, consumer, contexts, _ = fixture(tmp_path)
    original_factory = consumer.context_factory
    npz_digest = contexts[-1]['current_epoch']['sources'][1]['sha256']
    def context(chain):
        result = original_factory(chain)
        result['scientific_identity'] = dict(result['scientific_identity'], npz_sha256=npz_digest)
        result['first_pass_start_chain_sha256'] = C.priority.evidence.guard.fingerprint({'FLAG': False})
        return result
    consumer.end_batch()
    consumer.context_factory = context
    consumer.begin_batch({'FLAG': False})
    directory = tmp_path / 'captured'
    directory.mkdir()
    consumer.capture_directory = str(directory)
    monkeypatch.setitem(sys.modules, 'v12_quick_engine', SimpleNamespace(__file__=str(tmp_path / 'v12_quick_engine.py')))
    monkeypatch.setitem(sys.modules, 'tools.opt.v12_pilot', SimpleNamespace(evaluate_prepared_sanitized=lambda *args: None))
    monkeypatch.setattr(multiprocessing, 'current_process', lambda: SimpleNamespace(name='ForkProcess-test-fixture'))
    def capture(prepared, settings, evaluate, days):
        result = dict(valid=True, gain_pct=0.0, execution_ledger=[])
        configs = [dict(settings)]
        return result, dict(input_cutoff_sha256='d'*64, effective_config_values=configs, effective_config_sha256=C.priority.evidence.guard.fingerprint(configs), execution_ledger_sha256=C.priority.evidence.guard.fingerprint([]), result_sha256=C.priority.evidence.guard.fingerprint(C.priority.evidence.guard.result_fields(result)))
    monkeypatch.setattr(C.priority.evidence.guard, 'capture_actual', capture)
    legal = C.capture_legal(dict(hdrs=['FILTER=15m'], h2f={'FILTER=15m': dict(filter='FILTER', opt='15m')}))
    consumer.apply(dict(kind='eval', hdrs=[], h2f={}), legal, row, {'FLAG': False})
    items = [('naked', {'FLAG': True}), ('FILTER=15m', {'FLAG': True, 'FILTER': '15m'})]
    consumer.register_plan(row, {'FLAG': False}, {'FLAG': True}, items, False)
    incomplete = consumer.finish_row(row, {'FLAG': False})
    assert not incomplete['complete']
    for label, settings in items:
        C.evaluate_fork_worker({}, settings, 30, consumer.worker_capture_spec())
    assert consumer.finish_row(row, {'FLAG': False})['complete']
    receipt = consumer.require_first_pass_complete([('EXIT', 3, 'FLAG', True)], {'done': {}}, {'FLAG': False})
    assert receipt['complete'] and len(receipt['actual_row_sources']) == 1
    consumer.end_batch()
    consumer.completed.clear()
    consumer.plans.clear()
    consumer.measurements.clear()
    consumer.begin_batch({'FLAG': False})
    assert consumer.resume_completed_rows({'FLAG': False}) == [row]
    with pytest.raises(ValueError, match='no winner reset'):
        consumer.resume_completed_rows({'FLAG': True})
    worker = next(directory.glob(C.priority.evidence.guard.fingerprint(items[0][1]) + '_*.json'))
    worker.write_text('{}')
    assert not consumer.verified_completed_rows()['complete']
    with pytest.raises(ValueError, match='incomplete'):
        consumer.require_first_pass_complete([('EXIT', 3, 'FLAG', True)], {'done': {'fake': {'complete': True}}}, {'FLAG': False})
