import copy
import json
import pytest

from tools import v15_nextgen_protection_priority as P


def catalog():
    return [{'id': name, 'x': {'field': name, 'value': True}, 'y': {'field': 'COMMON', 'value': 1}, 'pos_sym': 0, 'model_p': 0} for name in ['protection', 'recovery', 'general_a', 'general_b', 'general_c']]


def context(tmp_path, phase='post_kg_post_worst_first'):
    path = tmp_path / 'v12_quick_engine.py'
    path.write_text('unit-test source fixture, not a trading engine')
    digest = P.evidence.sha(path)
    epoch = P.bind_epoch([{'asset': path.name, 'path': str(path), 'sha256': digest}])
    return {'phase': phase, 'current_epoch': epoch, 'scientific_identity': {'engine_sha256': digest, 'npz_sha256': 'b' * 64, 'accepted_chain_sha256': 'c' * 64, 'wiring_epoch_sha256': epoch['id'], 'sym_side': 'FIXTURE_LONG', 'window': {'window_days': 30, 'offset_days': 0, 'input_cutoff_sha256': 'd' * 64}}}


def domain(row):
    return {'eligible': True, 'protection': row['id'] == 'protection', 'recovery': row['id'] == 'recovery', 'protection_target': 'x_given_y', 'recovery_target': 'x_given_y', 'structurally_masked_research_priority': row['id'] == 'protection'}


def test_retained_nn_scores_change_fair_order_but_never_eligibility_or_starve_low_score(tmp_path):
    current = context(tmp_path)
    proposals = catalog()[2:4]
    def run(scores, name):
        path = tmp_path / name
        items = [dict(switch=row['x']['field'], cand=True, filter='COMMON', opt=1, p_pos_pred=score) for row, score in zip(proposals, scores)]
        path.write_text(json.dumps(dict(meta=dict(schema='v18_linknet/1', cat_side='STOCKS_LONG'), template_untried=items)))
        artifact = P.bind_prediction_artifact(dict(path=str(path), sha256=P.evidence.sha(path)))
        resolve = lambda original: dict(eligible=True, x=dict(field=original['switch'], value=True), y=dict(field='COMMON', value=1))
        bound = {row['id']: P.retained_prediction(artifact, index, row, current, resolve) for index, row in enumerate(proposals)}
        classify = lambda row: dict(eligible=True, retained_prediction=bound[row['id']])
        first = P.allocate(proposals, {}, current, 5, classify, lambda row: True)
        second = P.allocate(proposals, first['next_state'], current, 5, classify, lambda row: True)
        assert first['eligible_catalog_size'] == second['eligible_catalog_size'] == 2
        assert {first['selected'][0]['id'], second['selected'][0]['id']} == {row['id'] for row in proposals}
        assert P.validate_prediction_artifact(artifact)
        return first['selected'][0]['id']
    assert run([0.99, 0.0], 'left.json') == proposals[0]['id']
    assert run([0.0, 0.99], 'right.json') == proposals[1]['id']


def test_reserved_tests_use_existing_budget_even_with_zero_historical_frequency(tmp_path):
    proposals = catalog()
    before = copy.deepcopy(proposals)
    result = P.allocate(proposals, {}, context(tmp_path), 13, domain, lambda row: True)
    ids = {row['id'] for row in result['selected']}
    assert {'protection', 'recovery'} <= ids
    assert result['fulfilled_quotas']['unknown'] >= 1
    assert result['allocated_evaluation_cost'] <= 13
    assert proposals == before


def test_finite_unknown_catalog_is_allocated_without_probability_draws(tmp_path):
    proposals, state, seen = catalog(), {}, set()
    for index in range(30):
        current = dict(context(tmp_path), run_path='/different/run/' + str(index), receipt_timestamp=str(index))
        result = P.allocate(proposals, state, current, 5, domain, lambda row: True)
        state = result['next_state']
        seen.update(row['id'] for row in result['selected'])
        assert result['allocated_evaluation_cost'] <= 5
    assert seen == {row['id'] for row in proposals}


def test_mirror_dedup_and_selection_contract_rejection_do_not_create_training_negatives(tmp_path):
    proposals = catalog()[:2]
    mirror = dict(proposals[0], id='mirror', x=proposals[0]['y'], y=proposals[0]['x'])
    result = P.allocate(proposals + [mirror], {}, context(tmp_path), 13, domain, lambda row: row['id'] != 'recovery')
    assert result['eligible_catalog_size'] == 1
    assert len(result['rejected']) == 1
    assert result['selected'][0]['mirrored_source_ids'] == ['mirror']
    assert result['production_hooked'] is False


def test_first_pass_new_yellows_are_reserved_without_reordering_the_row(tmp_path):
    row = {'tab': 'EXIT', 'row': 5, 'switch': 'FLAG', 'cand': True}
    cells = [dict(row, filter='FILTER', opt=number, pos_sym=0, model_p=0) for number in range(6)]
    current = dict(context(tmp_path, 'post_kg_first_worst_pass'), row_order='worst_first', row_status='newly_wired')
    classify = lambda cell: dict(row, eligible=True, current_epoch_measured=False)
    state, seen = {}, set()
    for _ in range(3):
        result = P.first_pass_yellows(cells, row, state, current, 2, classify)
        state = result['next_state']
        seen.update(item['cell']['opt'] for item in result['selected'])
        assert result['row_order_changed'] is False
        assert result['mandatory_exploration_quota'] == 6
        assert result['allocation_fraction'] == 2 / 6
        assert result['measured_coverage_fraction'] == 0
        assert result['planned_rounds_to_target_at_current_budget'] == 3
    assert seen == set(range(6))
    with pytest.raises(ValueError, match='no existing budget'):
        P.first_pass_yellows(cells, row, {}, current, 0, classify)
    with pytest.raises(ValueError, match='cannot change'):
        P.first_pass_yellows(cells, row, {}, current, 2, lambda cell: dict(row, row=999, eligible=True))
    with pytest.raises(ValueError, match='pinpoint allocation'):
        P.allocate(catalog(), {}, current, 13, domain, lambda row: True)
    with pytest.raises(ValueError, match='source-bound'):
        P.first_pass_yellows(cells, row, {}, dict(current, current_epoch=None), 2, classify)


def test_epoch_is_immutable_serializable_and_checks_source_at_batch_boundary(tmp_path):
    current = context(tmp_path)
    epoch = current['current_epoch']
    before = P.scientific_identity(current)
    with pytest.raises(TypeError, match='immutable'):
        epoch['sources'][0]['sha256'] = 'f' * 64
    with pytest.raises(TypeError, match='immutable'):
        epoch.update(id='f' * 64)
    assert P.scientific_identity(copy.deepcopy(current)) == before
    assert json.loads(json.dumps(current))['current_epoch']['id'] == epoch['id']
    assert P.validate_epoch_sources(epoch)
    (tmp_path / 'v12_quick_engine.py').write_text('changed source after preparation')
    with pytest.raises(ValueError, match='source changed'):
        P.validate_epoch_sources(epoch)


def test_epoch_rejects_mutation_even_when_dict_base_method_is_used(tmp_path):
    current = context(tmp_path)
    dict.__setitem__(current['current_epoch']['sources'][0], 'path', 'another/source.py')
    with pytest.raises(ValueError, match='source-bound'):
        P.scientific_identity(current)


def test_only_bound_current_actual_measurements_count_as_coverage(tmp_path):
    row = {'tab': 'EXIT', 'row': 5, 'switch': 'FLAG', 'cand': True}
    cells = [dict(row, filter='FILTER', opt=15)]
    current = dict(context(tmp_path, 'post_kg_first_worst_pass'), row_order='worst_first', row_status='new')
    def run(**extra):
        return P.first_pass_yellows(cells, row, {}, current, 1, lambda cell: dict(row, eligible=True, **extra))
    assert run(current_epoch_measured=True)['current_epoch_measured_cells'] == 0
    identity = run()['selected'][0]['identity']
    scientific = P.scientific_identity(current)
    record = {'schema': 'current_yellow_measurement/1', 'cell_identity': identity, 'scientific_identity': scientific, 'evaluation_status': 'evaluated_valid', 'engine_proof': {'effective_config_sha256': 'e' * 64, 'execution_ledger_sha256': 'f' * 64, 'result_sha256': 'a' * 64, 'input_cutoff_sha256': 'd' * 64}}
    path = tmp_path / 'measurement.json'
    def bind(value):
        path.write_text(json.dumps(value))
        return P.bind_measurement({'path': str(path), 'sha256': P.evidence.sha(path)})
    proof = bind(record)
    assert run(measurement_proof=proof)['current_epoch_measured_cells'] == 1
    assert run(measurement_proof=record)['current_epoch_measured_cells'] == 0
    stale = copy.deepcopy(record)
    stale['scientific_identity']['accepted_chain_sha256'] = '0' * 64
    assert run(measurement_proof=bind(stale))['current_epoch_measured_cells'] == 0
    missing = copy.deepcopy(record)
    del missing['engine_proof']['execution_ledger_sha256']
    assert run(measurement_proof=bind(missing))['current_epoch_measured_cells'] == 0
    failed = dict(record, evaluation_status='evaluation_error')
    assert run(measurement_proof=bind(failed))['current_epoch_measured_cells'] == 0
    assert run(measurement_proof=bind(dict(record, evaluation_status='evaluated_inadmissible')))['current_epoch_measured_cells'] == 1
    with pytest.raises(ValueError, match='content mismatch'):
        P.bind_measurement({'path': str(path), 'sha256': '0' * 64})
    with pytest.raises(ValueError, match='domain evidence'):
        run(evidence_refs=[{'path': str(path), 'sha256': '0' * 64}])
