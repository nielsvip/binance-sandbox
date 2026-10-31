"""Isolated pilot adapter; requires explicit source-bound batch and actual row classifier."""
import copy

from tools import v15_nextgen_protection_priority as priority


def capture_legal(info):
    headers = tuple(info.get('hdrs') or ())
    mapping = info.get('h2f') or {}
    if any(header not in mapping for header in headers):
        raise ValueError('legal discovery lacks exact header binding')
    return priority._freeze({'hdrs': headers, 'h2f': {header: copy.deepcopy(mapping[header]) for header in headers}, 'yellow': tuple(info.get('yellow') or ())})


class FirstPassConsumer:
    def __init__(self, context_factory, classify, reserve, state, budget, row_status):
        self.context_factory = context_factory
        self.classify = classify
        self.reserve = reserve
        self.state = copy.deepcopy(state)
        self.budget = budget
        self.row_status = row_status
        self.attempts = {}
    def apply(self, sampled, legal, row, chain):
        context = self.context_factory(copy.deepcopy(chain))
        context = dict(context, phase='post_kg_first_worst_pass', row_order='worst_first', row_status=self.row_status(row, context))
        priority.validate_epoch_sources(context['current_epoch'])
        cells = [dict(row, filter=legal['h2f'][header]['filter'], opt=legal['h2f'][header]['opt'], header=header) for header in legal['hdrs']]
        allocation = priority.first_pass_yellows(cells, row, self.state, context, self.budget(row, context), lambda cell: self.classify(cell, context))
        result = copy.deepcopy(sampled)
        selected = [item['cell']['header'] for item in allocation['selected']]
        illegal = set(result.get('hdrs') or ()) - set(legal['hdrs'])
        if illegal:
            raise ValueError('sampled headers escaped actual legal row census')
        if selected:
            if result.get('kind') == 'skip' and not str(result.get('reason') or '').startswith(('CARRIED_', 'BRAIN_', 'PRIOR_', 'V20_', 'LOW_YIELD', 'ZERO_FORMULA', 'POS_SYM')):
                raise ValueError('structural skip cannot be rescued as exploration')
            result['kind'] = 'eval'
            result['reason'] = ''
        result['hdrs'] = list(dict.fromkeys(list(result.get('hdrs') or ()) + selected))
        result['h2f'] = dict(result.get('h2f') or {})
        result['h2f'].update({header: dict(legal['h2f'][header]) for header in selected})
        reservation = self.reserve(allocation, context)
        expected = priority.evidence.guard.fingerprint({'allocation': allocation, 'scientific_identity': priority.scientific_identity(context)})
        if not isinstance(reservation, dict) or reservation.get('status') != 'reserved' or reservation.get('allocation_sha256') != expected:
            raise ValueError('durable exact allocation reservation required before state advance')
        priority.validate_epoch_sources(context['current_epoch'])
        self.state = allocation['next_state']
        result['nextgen_allocation'] = allocation
        result['nextgen_reservation'] = reservation
        result['nextgen_scientific_identity'] = priority.scientific_identity(context)
        priority.validate_epoch_sources(context['current_epoch'])
        return result
    def record_attempt(self, reservation, header, status, measurement=None):
        if status not in ('failed', 'skipped', 'evaluated_valid', 'evaluated_inadmissible'):
            raise ValueError('explicit actual attempt status required')
        if status.startswith('evaluated_') and not isinstance(measurement, priority.SourceBoundMeasurement):
            raise ValueError('completed coverage requires source-bound actual measurement')
        self.attempts[(reservation, header)] = {'status': status, 'measurement': measurement}


def first_pass_completion(expected_rows, accounted_rows, deferred_cells):
    expected = {priority.evidence.guard.fingerprint(row) for row in expected_rows}
    accounted = {priority.evidence.guard.fingerprint(item['row']) for item in accounted_rows if item.get('status') in ('evaluated', 'structurally_ineligible')}
    missing = expected - accounted
    return {'schema': 'nextgen_first_pass_completion/1', 'complete': not missing and not deferred_cells, 'expected_rows': len(expected), 'accounted_rows': len(expected & accounted), 'missing_rows': sorted(missing), 'deferred_cells': list(deferred_cells), 'publication_print_is_proof': False}
