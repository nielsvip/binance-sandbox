import copy
import json
import sys
import types

import pytest

from tools import v15_nextgen_interaction_executor as E


def setup_fixture(tmp_path, monkeypatch, fail_baseline=False, invalid_x=False):
    baseline = {'A': False, 'B': 0}
    ledger = [{'fixture_event': 'unit_test_only', 'fixture_return': 1}]
    proof = {'effective_config_values': [baseline], 'effective_config_sha256': E.guard.fingerprint([baseline]), 'input_cutoff_sha256': 'fixture_cutoff', 'execution_ledger_sha256': E.guard.fingerprint(ledger), 'result_sha256': 'fixture_result', 'reader_transformations': {}}
    source = tmp_path / 'accepted.json'
    source.write_text(json.dumps(baseline))
    oracle = tmp_path / 'oracle.json'
    oracle.write_text(json.dumps({'previous_actual_engine_proof': proof, 'accepted_source': {'path': str(source), 'sha256': E.sha(source)}}))
    context = {'phase': 'postbaseline_research', 'window_days': 30, 'window_context': {'fixture': True}, 'baseline_oracle': {'path': str(oracle), 'sha256': E.sha(oracle)}}
    pin = {'settings': baseline, 'previous_actual_engine_proof': proof, 'window_context': context['window_context']}
    calls = []
    def capture(prepared, settings, evaluate, window_days):
        calls.append(copy.deepcopy(settings))
        actual = copy.deepcopy(proof)
        actual.update(effective_config_values=[copy.deepcopy(settings)], effective_config_sha256=E.guard.fingerprint([settings]))
        if fail_baseline:
            actual['input_cutoff_sha256'] = 'different_fixture_cutoff'
        identity = (settings['A'], settings['B'])
        value = {(False, 0): 1.0, (True, 0): 2.0, (False, 5): 3.0, (True, 5): 8.0}[identity]
        invalid = invalid_x and identity == (True, 0)
        return {'execution_ledger': ledger, 'gain_pct': value, 'valid': not invalid, 'invalid_reason': 'fixture_constraint' if invalid else None, 'trades': 20}, actual
    monkeypatch.setattr(E.guard, 'capture_actual', capture)
    monkeypatch.setattr(E, 'validate_inputs', lambda *args: None)
    module = types.ModuleType('tools.opt.v12_pilot')
    module.evaluate_prepared_sanitized = lambda *args: None
    monkeypatch.setitem(sys.modules, 'tools.opt.v12_pilot', module)
    return pin, context, calls


def test_four_component_records_drive_real_priority_consumer_without_baseline_mutation(tmp_path, monkeypatch):
    pin, context, calls = setup_fixture(tmp_path, monkeypatch)
    before = copy.deepcopy(pin)
    x, y = {'field': 'A', 'value': True}, {'field': 'B', 'value': 5}
    output = tmp_path / 'measurements'
    result = E.execute_quartet({}, pin, x, y, context, output, lambda *args: True)
    assert len(calls) == 4
    assert pin == before
    assert result['status'] == 'measured_valid_quartet'
    receipt = output / 'quartet.json'
    choices = [{'id': 'a_untested', 'x': {'field': 'C', 'value': 1}, 'y': y}, {'id': 'z_measured', 'x': x, 'y': y}]
    assert E.prioritize_confirmation(choices, [], context, lambda *args: True)[0]['id'] == 'a_untested'
    ranked = E.prioritize_confirmation(choices, [(receipt, E.sha(receipt))], context, lambda *args: True)
    assert ranked[0]['id'] == 'z_measured'
    assert len(ranked) == 2
    assert ranked[0]['current_confirmation_verified'] is False
    assert E.prioritize_confirmation(choices, [(receipt, E.sha(receipt))], dict(context, other=True), lambda *args: True)[0]['id'] == 'a_untested'
    ledger = output / 'Z_execution_ledger.json'
    ledger.write_text('[]')
    with pytest.raises(ValueError, match='ledger source changed'):
        E.load_measured_quartet(receipt, E.sha(receipt))


def test_baseline_drift_stops_before_interventions_and_preserves_partial_evidence(tmp_path, monkeypatch):
    pin, context, calls = setup_fixture(tmp_path, monkeypatch, fail_baseline=True)
    with pytest.raises(ValueError, match='baseline cutoff'):
        E.execute_quartet({}, pin, {'field': 'A', 'value': True}, {'field': 'B', 'value': 5}, context, tmp_path / 'partial', lambda *args: True)
    assert len(calls) == 1
    result = json.loads((tmp_path / 'partial/quartet.json').read_text())
    assert result['status'] == 'failed_or_partial_not_verified'
    assert set(result['components']) == {'B'}


def test_constraint_measurement_retains_finite_gain_without_becoming_verified(tmp_path, monkeypatch):
    pin, context, calls = setup_fixture(tmp_path, monkeypatch, invalid_x=True)
    output = tmp_path / 'constrained'
    result = E.execute_quartet({}, pin, {'field': 'A', 'value': True}, {'field': 'B', 'value': 5}, context, output, lambda *args: True)
    assert result['status'] == 'measured_diagnostic_constraint_dependency_or_noop'
    assert json.loads((output / 'X_measurement.json').read_text())['gain_pct'] == 2.0
    with pytest.raises(ValueError, match='valid measured quartet'):
        E.load_measured_quartet(output / 'quartet.json', E.sha(output / 'quartet.json'))
