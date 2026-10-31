"""Isolated pilot adapter; requires explicit source-bound batch and actual row classifier."""
import copy
import math

from tools import v15_nextgen_protection_priority as priority


class VerifiedRowOrder(priority.FrozenRecord):
    pass


def bind_row_order(template_source, tabs):
    import openpyxl
    path = priority.Path(template_source['path'])
    if priority.evidence.sha(path) != template_source['sha256']:
        raise ValueError('template order source content mismatch')
    workbook = openpyxl.load_workbook(path, read_only=False, data_only=True)
    rows, statistics = [], []
    try:
        for tab in tabs:
            sheet = workbook[tab]
            headers = {str(cell.value).strip(): cell.column for cell in sheet[2] if cell.value is not None}
            if 'AVG_DELTA' not in headers:
                raise ValueError('worst-first source lacks AVG_DELTA header')
            groups, positions = [], {}
            for number in range(3, sheet.max_row + 1):
                switch, cand = sheet.cell(number, 1).value, sheet.cell(number, 2).value
                if switch in (None, '') or cand is None:
                    continue
                orange = 'FFE699' in str(sheet.cell(number, 1).fill.fgColor.rgb or '')
                key = (str(switch).strip(), orange)
                if key not in positions:
                    positions[key] = len(groups)
                    groups.append({'switch': key[0], 'orange': orange, 'values': []})
                elif groups[-1] is not groups[positions[key]]:
                    raise ValueError('template switch group is not contiguous')
                value = sheet.cell(number, headers['AVG_DELTA']).value
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    groups[positions[key]]['values'].append(float(value))
                rows.append(dict(tab=tab, row=number, switch=str(switch).strip(), cand=cand))
            scored = [{**group, 'mean': sum(group['values']) / len(group['values']) if group['values'] else None} for group in groups]
            expected = sorted(scored, key=lambda group: (group['orange'], group['mean'] is None, group['mean'] if group['mean'] is not None else 0))
            if [(group['switch'], group['orange']) for group in scored] != [(group['switch'], group['orange']) for group in expected]:
                mismatch = next((actual, wanted) for actual, wanted in zip(scored, expected) if (actual['switch'], actual['orange']) != (wanted['switch'], wanted['orange']))
                raise ValueError(f"physical template order unverified in {tab}: first differing actual {mismatch[0]['switch']} mean={mismatch[0]['mean']}; expected {mismatch[1]['switch']} mean={mismatch[1]['mean']}; no re-sort performed")
            statistics.append(dict(tab=tab, groups=len(groups), scored_groups=sum(group['mean'] is not None for group in scored)))
    finally:
        workbook.close()
    if priority.evidence.sha(path) != template_source['sha256']:
        raise ValueError('template source changed during order census')
    return VerifiedRowOrder(schema='verified_physical_worst_first/1', source=priority._freeze(template_source), rows=priority._freeze(rows), physical_row_order_sha256=priority.evidence.guard.fingerprint(rows), statistics=priority._freeze(statistics))


def capture_legal(info):
    headers = tuple(info.get('hdrs') or ())
    mapping = info.get('h2f') or {}
    if any(header not in mapping for header in headers):
        raise ValueError('legal discovery lacks exact header binding')
    return priority._freeze({'hdrs': headers, 'h2f': {header: copy.deepcopy(mapping[header]) for header in headers}, 'yellow': tuple(info.get('yellow') or ())})


class FirstPassConsumer:
    def __init__(self, context_factory, classify, reserve, state, budget, row_status, completion_audit):
        self.context_factory = context_factory
        self.classify = classify
        self.reserve = reserve
        self.state = copy.deepcopy(state)
        self.budget = budget
        self.row_status = row_status
        self.completion_audit = completion_audit
        self.attempts = {}
        self.batch_epoch = None
    def begin_batch(self, chain):
        context = self.context_factory(copy.deepcopy(chain))
        priority.validate_epoch_sources(context['current_epoch'])
        self.batch_epoch = context['current_epoch']
    def end_batch(self):
        if self.batch_epoch is None:
            raise ValueError('evaluation batch was not source-validated')
        priority.validate_epoch_sources(self.batch_epoch)
        self.batch_epoch = None
    def apply(self, sampled, legal, row, chain):
        context = self.context_factory(copy.deepcopy(chain))
        context = dict(context, row_status=self.row_status(row, context))
        if context.get('phase') != 'post_kg_first_worst_pass' or context.get('row_order') != 'worst_first':
            raise ValueError('actual retained KG phase and verified worst-first order required')
        priority._check_epoch(context['current_epoch'])
        if self.batch_epoch is None or context['current_epoch']['id'] != self.batch_epoch['id']:
            raise ValueError('current source epoch has no active validated batch')
        order = context.get('row_order_proof')
        if not isinstance(order, VerifiedRowOrder) or not any(priority.evidence.guard.fingerprint(actual) == priority.evidence.guard.fingerprint(row) for actual in order['rows']):
            raise ValueError('current row must belong to verified physical template order')
        if not any(source['sha256'] == order['source']['sha256'] for source in context['current_epoch']['sources']):
            raise ValueError('physical row order template is absent from bound current sources')
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
        self.state = allocation['next_state']
        result['nextgen_allocation'] = allocation
        result['nextgen_reservation'] = reservation
        result['nextgen_scientific_identity'] = priority.scientific_identity(context)
        return result
    def require_first_pass_complete(self, physical_rows, progress, chain):
        context = self.context_factory(copy.deepcopy(chain))
        priority.validate_epoch_sources(context['current_epoch'])
        receipt = self.completion_audit(copy.deepcopy(physical_rows), copy.deepcopy(progress), context)
        if not isinstance(receipt, priority.FrozenRecord) or receipt.get('schema') != 'source_bound_first_pass_audit/1':
            raise ValueError('actual source-bound first-pass completion audit required; counters are not proof')
        if receipt.get('scientific_identity') != priority.scientific_identity(context) or receipt.get('physical_row_order_sha256') != priority.evidence.guard.fingerprint(physical_rows):
            raise ValueError('first-pass audit belongs to different chain or physical row order')
        if receipt.get('complete') is not True:
            raise ValueError('first-pass incomplete: research phases remain deferred')
        priority.validate_epoch_sources(context['current_epoch'])
        return receipt
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
    return {'schema': 'nextgen_first_pass_completion/1', 'complete': False, 'accounting_complete': not missing and not deferred_cells, 'source_bound_measurement_audit_required': True, 'expected_rows': len(expected), 'accounted_rows': len(expected & accounted), 'missing_rows': sorted(missing), 'deferred_cells': list(deferred_cells), 'publication_print_is_proof': False}
