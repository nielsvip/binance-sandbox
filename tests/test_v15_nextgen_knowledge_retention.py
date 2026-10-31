import json
from pathlib import Path

import pytest

from tools import v15_nextgen_knowledge_retention as K


def write_source(tmp_path, name, document, kind):
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return dict(path=str(path), sha256=K.evidence.sha(path), kind=kind, scope='unit-test model format fixture, not backtest evidence')


def test_prior_and_new_associations_are_retained_without_count_addition_or_deadness(tmp_path):
    columns = ['filter', 'row', 'wpos', 'wn', 'n', 'pos', 'n_rescue', 'n_kill', 'n_syn', 'syn_sum', 'n_sym', 'abl_n', 'abl_kill']
    prior = dict(_meta=dict(pair_cols=columns, cat_side='CRYPTO_LONG', last_ts='old'), nodes=['F:FILTER=15m', 'R:FLAG=True'], pair=[[0,1,0,30,30,0,0,0,0,0,5,0,0]])
    current = dict(prior, pair=[[0,1,1,2,2,1,0,0,0,0,1,0,0]])
    sources = [write_source(tmp_path, 'prior.json', prior, 'graph'), write_source(tmp_path, 'new.json', current, 'graph')]
    reference = tmp_path / 'domain.json'
    reference.write_text('actual typed domain fixture')
    domain = lambda raw: dict(eligible=True, x=dict(field='FLAG', value=True), y=dict(field='FILTER', value='15m'), evidence_refs=[dict(path=str(reference), sha256=K.evidence.sha(reference))])
    output = []
    report = K.build_catalog(sources, domain, output.append)
    assert report['counts']['unique_current_domain_pairs'] == 1
    assert len(output) == 1 and len(output[0]['retained_sources']) == 2
    assert [item['event']['raw_model_evidence']['n'] for item in output[0]['retained_sources']] == [30, 2]
    assert output[0]['can_suppress_test'] is False
    assert output[0]['current_confirmation_verified'] is False
    assert report['all_raw_training_rows_absorbed'] is False


def test_unresolved_historical_fields_remain_diagnostic_not_silently_dropped(tmp_path):
    document = dict(meta=dict(cat_side='STOCKS_SHORT'), template_untried=[dict(switch='HISTORICAL_FIELD', cand='True', filter='FILTER', opt='15m')])
    source = write_source(tmp_path, 'predictions.json', document, 'neural_proposals')
    rows = []
    report = K.build_catalog([source], lambda raw: dict(eligible=False, reason='outside_exact_current_template'), rows.append)
    assert report['counts']['diagnostic:outside_exact_current_template'] == 1
    assert rows[0]['status'] == 'preserved_diagnostic_not_runtime_eligible'
    assert rows[0]['raw']['x']['field'] == 'HISTORICAL_FIELD'
    Path(source['path']).write_text('{}')
    with pytest.raises(ValueError, match='immutable'):
        K.build_catalog([source], lambda raw: {}, rows.append)


def test_retained_catalog_invokes_actual_allocator_with_protection_recovery_unknown_slots(tmp_path):
    engine = tmp_path / 'v12_quick_engine.py'
    engine.write_text('allocator source fixture')
    digest = K.evidence.sha(engine)
    epoch = K.priority.bind_epoch([dict(asset=engine.name, path=str(engine), sha256=digest)])
    context = dict(phase='post_kg_post_worst_first', current_epoch=epoch, scientific_identity=dict(engine_sha256=digest, npz_sha256='b'*64, accepted_chain_sha256='c'*64, wiring_epoch_sha256=epoch['id'], sym_side='FIXTURE_LONG', window=dict(window_days=30, offset_days=0, input_cutoff_sha256='d'*64)))
    catalog = [dict(id=family, x=dict(field=family, value=True), y=dict(field='FILTER', value='15m'), can_suppress_test=False, current_confirmation_verified=False, pos_sym=0) for family in ('protection', 'recovery', 'unknown')]
    allocated = K.allocate_retained(catalog, {}, context, 13, lambda item: dict(eligible=True, protection=item['id']=='protection', recovery=item['id']=='recovery'), lambda item: True)
    assert {item['id'] for item in allocated['selected']} == {'protection', 'recovery', 'unknown'}
    assert allocated['allocated_evaluation_cost'] == 13
    assert allocated['baseline_modified'] is False
