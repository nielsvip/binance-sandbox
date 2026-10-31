import copy
import json
from pathlib import Path

import numpy as np
import openpyxl
import pytest

from tools import v15_nextgen_runtime_factory as F


def runtime_fixture(tmp_path):
    assets = ['v12_quick_engine.py', 'v15_pilot.py', 'tools/opt/evaluate_v12.py', 'tools/opt/v12_pilot.py', 'tools/v15_kindergarten_first.py','tools/v15_daily_template_update.py', 'data.npz']
    sources = []
    classifier_source = "def exact(cell, context):\n return dict(cell, eligible=True)\n"
    for asset in assets:
        path = tmp_path / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(classifier_source if asset == 'v15_pilot.py' else 'unit source fixture, not a trading engine')
        sources.append(dict(asset=asset, path=str(path), sha256=F.priority.evidence.sha(path)))
    namespace = {}
    exec(compile(classifier_source, str(tmp_path / 'v15_pilot.py'), 'exec'), namespace)
    book = tmp_path / 'TEMPLATE.xlsx'
    workbook = openpyxl.Workbook()
    workbook.active.title = 'EXIT'
    workbook.active.append(['title'])
    workbook.active.append(['Switch', 'cand', 'AVG_DELTA','is_default'])
    workbook.active.append(['FLAG', True, -1,'YES'])
    workbook.save(book)
    workbook.close()
    sources.append(dict(asset=book.name, path=str(book), sha256=F.priority.evidence.sha(book)))
    row = dict(tab='EXIT', row=3, switch='FLAG', cand=True)
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps(dict(schema='first_pass_budget_policy/1', phase_completion='budgeted_with_durable_debt', allow_old_cell_defer=True, new_row_minimum_reserved_fraction=0.5, row_yellow_budgets={F.priority.evidence.guard.fingerprint(row): 1})))
    sources.append(dict(asset=policy.name, path=str(policy), sha256=F.priority.evidence.sha(policy)))
    evidence, reservations = tmp_path/'evidence', tmp_path/'reservations'
    evidence.mkdir()
    reservations.mkdir()
    prepared = dict(window_days=30, offset_days=0, npz_prepared=dict(timestamps=np.array([1,2,3])))
    chain, results, baseline = {'FLAG':False}, {'singles':[]}, {'unit_fixture':True}
    contract = dict(schema='v15_kindergarten_phase/1', completed=True, identity=baseline, results_sha256=F.priority.evidence.guard.fingerprint(results), output_chain_sha256=F.priority.evidence.guard.fingerprint(chain))
    progress = dict(kindergarten_first=results, kindergarten_first_contract=contract)
    defaults = {'FLAG':False}
    spec = dict(schema='nextgen_runtime_factory/1', model_usage='research_priority_only', sources=sources, defaults_sha256=F.priority.evidence.guard.fingerprint(defaults), template_asset=book.name, npz_asset='data.npz', budget_policy_asset=policy.name, tabs=['EXIT'], prepared_arrays_sha256=F.priority.evidence.prepared_arrays_identity(prepared), baseline_identity=baseline, sym_side='FIXTURE_LONG', evidence_directory=str(evidence), reservation_directory=str(reservations))
    return spec, prepared, progress, chain, defaults, namespace['exact'], row


def test_factory_binds_actual_inputs_budget_phase_and_classifier_without_per_cell_template_hash(tmp_path, monkeypatch):
    spec, prepared, progress, chain, defaults, classify, row = runtime_fixture(tmp_path)
    instance = F.build(spec, prepared, progress, chain, defaults, classify)
    instance.begin_batch(chain)
    legal = F.consumer.capture_legal(dict(hdrs=['FILTER=15m','FILTER=1h'], h2f={'FILTER=15m':dict(filter='FILTER',opt='15m'),'FILTER=1h':dict(filter='FILTER',opt='1h')}))
    original = F.priority.evidence.sha
    template_reads = []
    def observe(path):
        if str(path).endswith('TEMPLATE.xlsx'):
            template_reads.append(str(path))
        return original(path)
    monkeypatch.setattr(F.priority.evidence, 'sha', observe)
    result = instance.apply(dict(kind='eval',hdrs=[],h2f={}), legal, row, chain)
    assert len(result['hdrs']) == 1
    assert not template_reads
    reservation = result['nextgen_reservation']['source']
    payload = json.loads(Path(reservation['path']).read_text())
    assert payload['measurement_claimed'] is False
    assert len(payload['deferred_debt']) == 1
    instance.end_batch()
    assert template_reads
    assert list(Path(spec['evidence_directory']).glob('retained_kg_*.json'))


def test_factory_refuses_stale_results_arrays_defaults_and_unverified_template_order(tmp_path):
    values = runtime_fixture(tmp_path)
    spec, prepared, progress, chain, defaults, classify, row = values
    bad = copy.deepcopy(progress)
    bad['kindergarten_first']['injected'] = True
    with pytest.raises(ValueError, match='receipt/result'):
        F.build(spec, prepared, bad, chain, defaults, classify)
    with pytest.raises(ValueError, match='defaults'):
        F.build(spec, prepared, progress, chain, {'FLAG':True}, classify)
    changed = copy.deepcopy(prepared)
    changed['npz_prepared']['timestamps'][0] = 999
    with pytest.raises(ValueError, match='prepared arrays'):
        F.build(spec, changed, progress, chain, defaults, classify)
    with pytest.raises(ValueError, match='research-only'):
        F.build(dict(spec,model_usage='pre_holdout_qualified'), prepared, progress, chain, defaults, classify)
