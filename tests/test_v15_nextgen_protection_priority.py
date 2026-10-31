import copy

from tools import v15_nextgen_protection_priority as P


def catalog():
    return [{'id': name, 'x': {'field': name, 'value': True}, 'y': {'field': 'COMMON', 'value': 1}, 'pos_sym': 0, 'model_p': 0} for name in ['protection', 'recovery', 'general_a', 'general_b', 'general_c']]


def domain(row):
    return {'eligible': True, 'protection': row['id'] == 'protection', 'recovery': row['id'] == 'recovery', 'protection_target': 'x_given_y', 'recovery_target': 'x_given_y', 'structurally_masked_research_priority': row['id'] == 'protection'}


def test_reserved_tests_use_existing_budget_even_with_zero_historical_frequency():
    proposals = catalog()
    before = copy.deepcopy(proposals)
    result = P.allocate(proposals, {}, {'fixture_context': 1}, 13, domain, lambda row: True)
    ids = {row['id'] for row in result['selected']}
    assert {'protection', 'recovery'} <= ids
    assert result['fulfilled_quotas']['unknown'] >= 1
    assert result['allocated_evaluation_cost'] <= 13
    assert proposals == before


def test_finite_unknown_catalog_is_allocated_without_probability_draws():
    proposals, state, seen = catalog(), {}, set()
    for _ in range(30):
        result = P.allocate(proposals, state, {'fixture_context': 1}, 5, domain, lambda row: True)
        state = result['next_state']
        seen.update(row['id'] for row in result['selected'])
        assert result['allocated_evaluation_cost'] <= 5
    assert seen == {row['id'] for row in proposals}


def test_mirror_dedup_and_selection_contract_rejection_do_not_create_training_negatives():
    proposals = catalog()[:2]
    mirror = dict(proposals[0], id='mirror', x=proposals[0]['y'], y=proposals[0]['x'])
    result = P.allocate(proposals + [mirror], {}, {'fixture_context': 1}, 13, domain, lambda row: row['id'] != 'recovery')
    assert result['eligible_catalog_size'] == 1
    assert len(result['rejected']) == 1
    assert result['selected'][0]['mirrored_source_ids'] == ['mirror']
    assert result['production_hooked'] is False
