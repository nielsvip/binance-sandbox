"""Review-only finite-quota allocation from an existing evaluation budget; no defaults or execution hook."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re

try:
    import v15_nextgen_interaction_executor as evidence
except ModuleNotFoundError:
    from tools import v15_nextgen_interaction_executor as evidence


class FrozenRecord(dict):
    def _refuse(self, *args, **kwargs):
        raise TypeError('source-bound record is immutable')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _refuse
    def __deepcopy__(self, memo):
        return self


def _freeze(value):
    if isinstance(value, dict):
        return FrozenRecord({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class VerifiedEpoch(FrozenRecord):
    pass


class SourceBoundMeasurement(FrozenRecord):
    pass


class PredictionArtifact(FrozenRecord):
    pass


class RetainedPrediction(FrozenRecord):
    pass


class BoundDomain(FrozenRecord):
    pass


class BoundBudgetPolicy(FrozenRecord):
    pass


def bind_budget_policy(source):
    payload = Path(source['path']).read_bytes()
    if hashlib.sha256(payload).hexdigest() != source['sha256']:
        raise ValueError('budget-policy source changed')
    policy = json.loads(payload)
    fraction = policy.get('new_row_minimum_reserved_fraction')
    if policy.get('schema') != 'first_pass_budget_policy/1' or policy.get('phase_completion') != 'budgeted_with_durable_debt' or policy.get('allow_old_cell_defer') is not True or type(fraction) not in (int, float) or not 0 < fraction <= 1:
        raise ValueError('explicit approved budgeted first-pass policy required')
    return BoundBudgetPolicy(_source=_freeze(source), **_freeze(policy))


def bind_domain_sources(epoch, references):
    _check_epoch(epoch)
    if not references or any(not any(source['path'] == reference['path'] and source['sha256'] == reference['sha256'] for source in epoch['sources']) for reference in references):
        raise ValueError('domain references must belong to the immutable batch source epoch')
    return BoundDomain(epoch_id=epoch['id'], references=_freeze(references))


def validate_domain(domain, context):
    bound = domain.get('bound_domain')
    if bound is not None:
        _check_epoch(context['current_epoch'])
        if not isinstance(bound, BoundDomain) or bound['epoch_id'] != context['current_epoch']['id']:
            raise ValueError('domain belongs to a different source epoch')
    for source in domain.get('evidence_refs', []):
        if evidence.sha(Path(source['path'])) != source['sha256']:
            raise ValueError('domain evidence source changed')


def bind_prediction_artifact(source):
    payload = Path(source['path']).read_bytes()
    if hashlib.sha256(payload).hexdigest() != source['sha256']:
        raise ValueError('NN prediction artifact content mismatch')
    document = json.loads(payload)
    if not str((document.get('meta') or {}).get('schema', '')).startswith('v18_linknet/'):
        raise ValueError('supported retained NN proposal artifact required')
    return PredictionArtifact(source=_freeze(source), document=_freeze(document))


def retained_prediction(artifact, index, proposal, context, resolve_original):
    from tools.v18_brain import cat_side_of
    if not isinstance(artifact, PredictionArtifact):
        raise ValueError('source-bound NN artifact required')
    if artifact['document']['meta'].get('cat_side') != cat_side_of(scientific_identity(context)['sym_side']):
        raise ValueError('retained NN artifact belongs to another category/side')
    if context.get('model_usage') != 'research_priority_only':
        raise ValueError('retained model window lineage is not verified for holdout qualification; export/corpus timestamps are insufficient; research-priority use must be explicit')
    original = artifact['document']['template_untried'][index]
    resolved = resolve_original(original)
    if resolved.get('eligible') is not True or evidence.pair_identity(resolved['x'], resolved['y']) != evidence.pair_identity(proposal['x'], proposal['y']):
        raise ValueError('retained NN score does not bind the exact current eligible pair')
    score = original.get('p_pos_pred')
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('finite retained NN probability required')
    return RetainedPrediction(source=artifact['source'], index=index, pair_id=evidence.pair_identity(proposal['x'], proposal['y']), scientific_identity=_freeze(scientific_identity(context)), score=float(score), quality_qualified=False, model_usage='research_priority_only', semantics='retained historical NN prediction used only as fair priority tie-break; not current measured benefit or independent holdout evidence')


def prediction_tiebreak(proposal, domain, scientific, context):
    prediction = domain.get('retained_prediction')
    if prediction is None:
        return 0.0
    if not isinstance(prediction, RetainedPrediction) or context.get('model_usage') != prediction['model_usage'] or prediction['pair_id'] != evidence.pair_identity(proposal['x'], proposal['y']) or evidence.guard.fingerprint(prediction['scientific_identity']) != evidence.guard.fingerprint(scientific):
        raise ValueError('retained NN tie-break has stale context or unrelated pair')
    return float(prediction['score'])


def validate_prediction_artifact(artifact):
    if not isinstance(artifact, PredictionArtifact) or evidence.sha(Path(artifact['source']['path'])) != artifact['source']['sha256']:
        raise ValueError('retained NN artifact changed during allocation batch')
    return True


def bind_measurement(source):
    payload = Path(source['path']).read_bytes()
    if hashlib.sha256(payload).hexdigest() != source['sha256']:
        raise ValueError('measurement source content mismatch')
    record = json.loads(payload)
    if record.get('schema') != 'current_yellow_measurement/1':
        raise ValueError('current yellow measurement receipt required')
    return SourceBoundMeasurement(record=_freeze(record), source=_freeze(dict(source)))


def _measurement_state(resolved, identity, scientific):
    bound = resolved.get('measurement_proof')
    if not isinstance(bound, SourceBoundMeasurement):
        return False, 'no_source_bound_measurement'
    record = bound['record']
    if record.get('cell_identity') != identity or evidence.guard.fingerprint(record.get('scientific_identity')) != evidence.guard.fingerprint(scientific):
        return False, 'different_cell_or_scientific_context'
    proof = record.get('engine_proof') or {}
    keys = ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256')
    if any(not re.fullmatch('[a-f0-9]{64}', str(proof.get(key))) for key in keys) or proof.get('input_cutoff_sha256') != scientific['window']['input_cutoff_sha256']:
        return False, 'missing_or_different_actual_engine_proof'
    if record.get('evaluation_status') not in ('evaluated_valid', 'evaluated_inadmissible'):
        return False, 'not_a_completed_measurement'
    return True, 'source_bound_current_measurement'


def bind_epoch(sources):
    if not sources:
        raise ValueError('explicit current wiring sources required')
    checked = {}
    for source in sources:
        asset, path, expected = source['asset'], Path(source['path']), source['sha256']
        if asset in checked or Path(asset).is_absolute() or '..' in Path(asset).parts or path.name != Path(asset).name or evidence.sha(path) != expected:
            raise ValueError('wiring source identity/content mismatch')
        checked[asset] = expected
    identity = evidence.guard.fingerprint(sorted(checked.items()))
    result = VerifiedEpoch(schema='verified_wiring_sources/1', id=identity, sources=_freeze(sources))
    result.bound_id = identity
    result.bound_sources = evidence.guard.fingerprint(result['sources'])
    return result


def validate_epoch_sources(epoch):
    _check_epoch(epoch)
    for source in epoch['sources']:
        if evidence.sha(Path(source['path'])) != source['sha256']:
            raise ValueError('wiring source changed during evaluation batch')
    return True


def _check_epoch(epoch):
    if not isinstance(epoch, VerifiedEpoch) or epoch.get('id') != getattr(epoch, 'bound_id', None) or evidence.guard.fingerprint(epoch.get('sources')) != getattr(epoch, 'bound_sources', None):
        raise ValueError('current epoch must be source-bound and unchanged')


def scientific_identity(context):
    epoch = context.get('current_epoch')
    _check_epoch(epoch)
    raw = context.get('scientific_identity') or {}
    keys = ('engine_sha256', 'npz_sha256', 'accepted_chain_sha256', 'wiring_epoch_sha256', 'sym_side')
    if any(key not in raw for key in keys) or raw['wiring_epoch_sha256'] != epoch['id']:
        raise ValueError('explicit engine/NPZ/window/accepted-chain/era identity required')
    if any(not re.fullmatch('[a-f0-9]{64}', str(raw[key])) for key in keys if key != 'sym_side'):
        raise ValueError('scientific identity hashes must be full SHA256')
    if not any(source['asset'] == 'v12_quick_engine.py' and source['sha256'] == raw['engine_sha256'] for source in epoch['sources']):
        raise ValueError('scientific engine hash is not bound to current engine source')
    window = raw.get('window') or {}
    if not {'window_days', 'offset_days', 'input_cutoff_sha256'} <= set(window) or not re.fullmatch('[a-f0-9]{64}', str(window['input_cutoff_sha256'])):
        raise ValueError('actual prepared window identity required')
    return dict({key: raw[key] for key in keys}, window={key: window[key] for key in ('window_days', 'offset_days', 'input_cutoff_sha256')})


def allocate(experiments, state, context, evaluation_budget, current_domain, selection_contract, measured=(), quotas=None, baseline_guard_cost=1):
    if context.get('phase') != 'post_kg_post_worst_first':
        raise ValueError('pinpoint allocation requires retained KG best and completed first worst-first pass')
    scientific = scientific_identity(context)
    quotas = dict(quotas or {'protection': 1, 'recovery': 1, 'unknown': 1})
    if set(quotas) != {'protection', 'recovery', 'unknown'} or any(type(value) is not int or value < 1 for value in quotas.values()):
        raise ValueError('explicit positive protection, recovery and unknown quotas required')
    if type(evaluation_budget) is not int or evaluation_budget < 0 or type(baseline_guard_cost) is not int or baseline_guard_cost < 0:
        raise ValueError('integer existing evaluation budget required')
    capacity = max(0, evaluation_budget - baseline_guard_cost) // 4
    next_state = copy.deepcopy(state)
    turn = int(next_state.get('round', 0)) + 1
    last = next_state.setdefault('last_allocated', {})
    debts = next_state.setdefault('quota_debt', {})
    eligible, rejected = {}, []
    for original in experiments:
        proposal = copy.deepcopy(original)
        domain = current_domain(proposal)
        if not isinstance(domain, dict) or domain.get('eligible') is not True:
            rejected.append({'experiment': proposal, 'reason': 'not_actual_current_domain'})
            continue
        if selection_contract(proposal) is not True:
            rejected.append({'experiment': proposal, 'reason': 'per_side_selection_or_declared_experiment_contract_violation'})
            continue
        validate_domain(domain, context)
        identity = evidence.guard.fingerprint({'pair': evidence.pair_identity(proposal['x'], proposal['y']), 'scientific_context': scientific})
        if identity in eligible:
            eligible[identity]['mirrored_source_ids'].append(proposal['id'])
            continue
        ranked = evidence.prioritize_confirmation([proposal], measured, context, lambda *args: True)[0]
        unknown = not ranked['measured_sources']
        bands = {name for name in ('protection', 'recovery') if domain.get(name) is True}
        if unknown:
            bands.add('unknown')
        eligible[identity] = {'id': proposal['id'], 'identity': identity, 'experiment': proposal, 'domain': domain, 'bands': sorted(bands), 'unknown': unknown, 'ranked_evidence': ranked, 'retained_nn_tiebreak': prediction_tiebreak(proposal, domain, scientific, context), 'mirrored_source_ids': []}
    active = {band for row in eligible.values() for band in row['bands']}
    for band in quotas:
        debts[band] = int(debts.get(band, 0)) + quotas[band] if band in active else 0
    selected, fulfilled, remaining = [], {band: 0 for band in quotas}, dict(eligible)
    order = {'protection': 0, 'recovery': 1, 'unknown': 2}
    def score(row, band):
        if band == 'unknown':
            return (int(last.get(row['identity'], 0)), -row['retained_nn_tiebreak'], row['identity'])
        target = row['domain'].get(band + '_target')
        key = {'x_given_y': 'Z_minus_Y', 'y_given_x': 'Z_minus_X'}.get(target)
        effects = row['ranked_evidence']['measured_relationship_features']
        conditional = max((item['effects'][key] for item in effects), default=0.0) if key else 0.0
        return (-int(row['domain'].get('structurally_masked_research_priority') is True), -conditional, int(last.get(row['identity'], 0)), -row['retained_nn_tiebreak'], row['identity'])
    while remaining and len(selected) < capacity:
        available_bands = {band for row in remaining.values() for band in row['bands'] if debts.get(band, 0) > 0}
        if available_bands:
            band = min(available_bands, key=lambda name: (-debts[name], order[name]))
            row = min((row for row in remaining.values() if band in row['bands']), key=lambda row: score(row, band))
            reason = 'reserved_' + band + '_quota'
        else:
            band = None
            row = min(remaining.values(), key=lambda row: (int(last.get(row['identity'], 0)), -row['retained_nn_tiebreak'], row['identity']))
            reason = 'remaining_existing_budget_fair_queue'
        remaining.pop(row['identity'])
        row['allocation_reason'] = reason
        selected.append(row)
        last[row['identity']] = turn
        for band in row['bands']:
            if band == 'unknown' and reason != 'reserved_unknown_quota':
                continue
            fulfilled[band] += 1
            debts[band] = max(0, debts[band] - 1)
    next_state['round'] = turn
    return {'schema': 'protection_recovery_test_allocation/1', 'status': 'review_only_allocation_not_execution', 'context': context, 'existing_evaluation_budget': evaluation_budget, 'baseline_guard_cost': baseline_guard_cost, 'quad_evaluation_cost': 4, 'allocated_evaluation_cost': baseline_guard_cost + 4 * len(selected) if selected else 0, 'requested_quotas': quotas, 'fulfilled_quotas': fulfilled, 'deferred_quota_debt': dict(debts), 'eligible_catalog_size': len(eligible), 'eligible_unknown_count': sum(row['unknown'] for row in eligible.values()), 'selected': selected, 'deferred': list(remaining.values()), 'rejected': rejected, 'next_state': next_state, 'baseline_modified': False, 'production_hooked': False, 'scope': 'finite caller-supplied current-domain catalog; no all-pairs claim; old pos_sym/model scores never remove eligible tests; source-valid same-context marginals only', 'coverage_contract': 'unknown quota uses least-recently-allocated order; positive quota debt carries across budget-limited rounds; allocation does not assert successful measurement'}


def first_pass_yellows(cells, row_identity, state, context, evaluation_budget, classify_cell, new_row_coverage_target=1.0):
    if context.get('phase') != 'post_kg_first_worst_pass' or context.get('row_order') != 'worst_first':
        raise ValueError('ordinary exploration belongs inside the post-KG worst-first pass')
    scientific = scientific_identity(context)
    if type(evaluation_budget) is not int or evaluation_budget < 0 or not 0 < new_row_coverage_target <= 1:
        raise ValueError('explicit existing cell budget and positive exploration quota required')
    next_state = copy.deepcopy(state)
    last = next_state.setdefault('last_yellow_allocated', {})
    turn = int(next_state.get('yellow_round', 0)) + 1
    eligible, rejected = {}, []
    for source in cells:
        cell = copy.deepcopy(source)
        resolved = classify_cell(cell)
        if not isinstance(resolved, dict) or resolved.get('eligible') is not True:
            rejected.append({'cell': cell, 'reason': 'not_actual_row_yellow'})
            continue
        validate_domain(resolved, context)
        actual_row = {name: resolved.get(name) for name in ('tab', 'row', 'switch', 'cand')}
        if evidence.guard.fingerprint(actual_row) != evidence.guard.fingerprint(row_identity):
            raise ValueError('first-pass yellow allocator cannot change or rerank the current row')
        identity = evidence.guard.fingerprint({'row': actual_row, 'filter': cell['filter'], 'opt': cell['opt'], 'scientific_context': scientific})
        measured, measurement_status = _measurement_state(resolved, identity, scientific)
        eligible.setdefault(identity, {'cell': cell, 'identity': identity, 'current_epoch_measured': measured, 'measurement_status': measurement_status})
    zero_old_budget = context.get('row_status') == 'known_existing' and isinstance(context.get('budget_policy'), BoundBudgetPolicy) and context['budget_policy']['allow_old_cell_defer'] is True
    if eligible and evaluation_budget == 0 and not zero_old_budget:
        raise ValueError('mandatory yellow exploration has no existing budget; row remains incomplete, never a measured zero')
    new_row = context.get('row_status') in ('new', 'newly_wired', 'unobserved')
    unknown = [row for row in eligible.values() if not row['current_epoch_measured']]
    pool = unknown or list(eligible.values())
    quota = math.ceil(len(pool) * new_row_coverage_target) if new_row else min(1, len(pool))
    ordered = sorted(pool, key=lambda row: (int(last.get(row['identity'], 0)), row['identity']))
    selected = ordered[:min(quota, evaluation_budget)]
    selected_ids = {row['identity'] for row in selected}
    remainder = sorted((row for row in eligible.values() if row['identity'] not in selected_ids), key=lambda row: (int(last.get(row['identity'], 0)), row['identity']))
    selected.extend(remainder[:max(0, evaluation_budget - len(selected))])
    for row in selected:
        last[row['identity']] = turn
    next_state['yellow_round'] = turn
    return {'schema': 'first_worst_pass_yellow_allocation/1', 'status': 'review_only_allocation_not_measurement', 'row_identity': dict(row_identity), 'row_order_changed': False, 'requires_naked_row_before_yellows': True, 'new_row_coverage_target': new_row_coverage_target, 'eligible_cells': len(eligible), 'current_epoch_measured_cells': len(eligible) - len(unknown), 'allocation_fraction': len(selected) / len(eligible) if eligible else None, 'measured_coverage_fraction': (len(eligible) - len(unknown)) / len(eligible) if eligible else None, 'planned_rounds_to_target_at_current_budget': math.ceil(quota / evaluation_budget) if quota and evaluation_budget else None, 'mandatory_exploration_quota': quota, 'mandatory_quota_deferred': max(0, quota - evaluation_budget), 'budget_provenance': 'caller-bound existing budget; no savings assumed from historical zeros, masks or skips', 'selected': selected, 'deferred': [row for row in eligible.values() if row['identity'] not in {item['identity'] for item in selected}], 'rejected': rejected, 'next_state': next_state, 'allocated_evaluations': len(selected), 'baseline_modified': False, 'production_hooked': False, 'scope': 'same actual row only; new/newly-wired cold-domain breadth targets all eligible cells by default; deferred allocation is not measured completion; no speed-reduction claim'}
