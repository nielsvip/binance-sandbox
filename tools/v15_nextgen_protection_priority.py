"""Review-only finite-quota allocation from an existing evaluation budget; no defaults or execution hook."""
import copy
from pathlib import Path

try:
    import v15_nextgen_interaction_executor as evidence
except ModuleNotFoundError:
    from tools import v15_nextgen_interaction_executor as evidence


def allocate(experiments, state, context, evaluation_budget, current_domain, selection_contract, measured=(), quotas=None, baseline_guard_cost=1):
    if context.get('phase') != 'post_kg_post_worst_first':
        raise ValueError('pinpoint allocation requires retained KG best and completed first worst-first pass')
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
        for source in domain.get('evidence_refs', []):
            if evidence.sha(Path(source['path'])) != source['sha256']:
                raise ValueError('priority evidence source changed')
        identity = evidence.guard.fingerprint({'pair': evidence.pair_identity(proposal['x'], proposal['y']), 'context': context})
        if identity in eligible:
            eligible[identity]['mirrored_source_ids'].append(proposal['id'])
            continue
        ranked = evidence.prioritize_confirmation([proposal], measured, context, lambda *args: True)[0]
        unknown = not ranked['measured_sources']
        bands = {name for name in ('protection', 'recovery') if domain.get(name) is True}
        if unknown:
            bands.add('unknown')
        eligible[identity] = {'id': proposal['id'], 'identity': identity, 'experiment': proposal, 'domain': domain, 'bands': sorted(bands), 'unknown': unknown, 'ranked_evidence': ranked, 'mirrored_source_ids': []}
    active = {band for row in eligible.values() for band in row['bands']}
    for band in quotas:
        debts[band] = int(debts.get(band, 0)) + quotas[band] if band in active else 0
    selected, fulfilled, remaining = [], {band: 0 for band in quotas}, dict(eligible)
    order = {'protection': 0, 'recovery': 1, 'unknown': 2}
    def score(row, band):
        if band == 'unknown':
            return (int(last.get(row['identity'], 0)), row['identity'])
        target = row['domain'].get(band + '_target')
        key = {'x_given_y': 'Z_minus_Y', 'y_given_x': 'Z_minus_X'}.get(target)
        effects = row['ranked_evidence']['measured_relationship_features']
        conditional = max((item['effects'][key] for item in effects), default=0.0) if key else 0.0
        return (-int(row['domain'].get('structurally_masked_research_priority') is True), -conditional, int(last.get(row['identity'], 0)), row['identity'])
    while remaining and len(selected) < capacity:
        available_bands = {band for row in remaining.values() for band in row['bands'] if debts.get(band, 0) > 0}
        if available_bands:
            band = min(available_bands, key=lambda name: (-debts[name], order[name]))
            row = min((row for row in remaining.values() if band in row['bands']), key=lambda row: score(row, band))
            reason = 'reserved_' + band + '_quota'
        else:
            band = None
            row = min(remaining.values(), key=lambda row: (int(last.get(row['identity'], 0)), row['identity']))
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


def first_pass_yellows(cells, row_identity, state, context, evaluation_budget, classify_cell, new_row_quota=2):
    if context.get('phase') != 'post_kg_first_worst_pass' or context.get('row_order') != 'worst_first':
        raise ValueError('ordinary exploration belongs inside the post-KG worst-first pass')
    if type(evaluation_budget) is not int or evaluation_budget < 0 or type(new_row_quota) is not int or new_row_quota < 1:
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
        actual_row = {name: resolved.get(name) for name in ('tab', 'row', 'switch', 'cand')}
        if evidence.guard.fingerprint(actual_row) != evidence.guard.fingerprint(row_identity):
            raise ValueError('first-pass yellow allocator cannot change or rerank the current row')
        identity = evidence.guard.fingerprint({'row': actual_row, 'filter': cell['filter'], 'opt': cell['opt'], 'epoch': context.get('current_epoch')})
        eligible.setdefault(identity, {'cell': cell, 'identity': identity, 'current_epoch_measured': resolved.get('current_epoch_measured') is True})
    if eligible and evaluation_budget == 0:
        raise ValueError('mandatory yellow exploration has no existing budget; row remains incomplete, never a measured zero')
    new_row = context.get('row_status') in ('new', 'newly_wired', 'unobserved')
    unknown = [row for row in eligible.values() if not row['current_epoch_measured']]
    pool = unknown or list(eligible.values())
    quota = min(len(pool), new_row_quota if new_row else 1)
    ordered = sorted(pool, key=lambda row: (int(last.get(row['identity'], 0)), row['identity']))
    selected = ordered[:min(quota, evaluation_budget)]
    selected_ids = {row['identity'] for row in selected}
    remainder = sorted((row for row in eligible.values() if row['identity'] not in selected_ids), key=lambda row: (int(last.get(row['identity'], 0)), row['identity']))
    selected.extend(remainder[:max(0, evaluation_budget - len(selected))])
    for row in selected:
        last[row['identity']] = turn
    next_state['yellow_round'] = turn
    return {'schema': 'first_worst_pass_yellow_allocation/1', 'status': 'review_only_allocation_not_measurement', 'row_identity': dict(row_identity), 'row_order_changed': False, 'requires_naked_row_before_yellows': True, 'mandatory_exploration_quota': quota, 'mandatory_quota_deferred': max(0, quota - evaluation_budget), 'selected': selected, 'deferred': [row for row in eligible.values() if row['identity'] not in {item['identity'] for item in selected}], 'rejected': rejected, 'next_state': next_state, 'allocated_evaluations': len(selected), 'baseline_modified': False, 'production_hooked': False, 'scope': 'same actual row only; new/newly-wired rows receive more mandatory yellow exploration; skipped/masked/unknown cells never become measured zero labels'}
