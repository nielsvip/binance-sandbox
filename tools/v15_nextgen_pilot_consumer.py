"""Isolated pilot adapter; requires explicit source-bound batch and actual row classifier."""
import copy
import math
import json
import os
from datetime import datetime, timezone
import uuid

from tools import v15_nextgen_protection_priority as priority


def evaluate_fork_worker(prepared, settings, window_days, capture):
    import multiprocessing
    import threading
    import v12_quick_engine as engine
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    if multiprocessing.current_process().name == 'MainProcess' or threading.current_thread() is not threading.main_thread():
        raise ValueError('actual configuration capture requires an isolated fork worker, never shared serial threads')
    if os.environ.get('V15_BASELINE_RAW_CAPTURE'):
        raise ValueError('worker capture cannot share a baseline raw-capture destination')
    epoch = capture['source_epoch']
    priority._check_epoch(epoch)
    engine_source = next((source for source in epoch['sources'] if source['asset'] == 'v12_quick_engine.py'), None)
    if engine_source is None or priority.Path(engine.__file__).resolve() != priority.Path(engine_source['path']).resolve():
        raise ValueError('actual worker engine module differs from bound source')
    if not any(source['sha256'] == capture['npz_sha256'] for source in epoch['sources']):
        raise ValueError('worker NPZ is absent from bound batch sources')
    result, proof = priority.evidence.guard.capture_actual(prepared, settings, evaluate_prepared_sanitized, window_days)
    directory = priority.Path(capture['directory'])
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('precreated isolated worker evidence directory required')
    record = dict(schema='nextgen_actual_worker_measurement/1', observed_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), source_epoch_sha256=epoch['id'], engine_sha256=engine_source['sha256'], npz_sha256=capture['npz_sha256'], requested_settings=priority.evidence.guard.normalized(settings), actual_engine_proof=proof, result=priority.evidence.guard.normalized(result), freshness_semantics='actual evaluation observation; not market data end', process_id=os.getpid())
    settings_id = priority.evidence.guard.fingerprint(settings)
    path = directory / (settings_id + '_' + uuid.uuid4().hex + '.json')
    payload = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return result


def bind_worker_measurement(source, cell, context, settings):
    path = priority.Path(source['path'])
    payload = path.read_bytes()
    if priority.evidence.hashlib.sha256(payload).hexdigest() != source['sha256']:
        raise ValueError('worker measurement source changed')
    record = json.loads(payload)
    scientific = priority.scientific_identity(context)
    proof = record.get('actual_engine_proof') or {}
    result = priority.evidence.guard.decoded(record.get('result'))
    if record.get('schema') != 'nextgen_actual_worker_measurement/1' or record.get('source_epoch_sha256') != scientific['wiring_epoch_sha256'] or record.get('engine_sha256') != scientific['engine_sha256'] or record.get('npz_sha256') != scientific['npz_sha256']:
        raise ValueError('worker measurement belongs to another source epoch')
    if priority.evidence.guard.fingerprint(priority.evidence.guard.decoded(record['requested_settings'])) != priority.evidence.guard.fingerprint(settings):
        raise ValueError('worker measurement requested settings mismatch')
    if proof.get('input_cutoff_sha256') != scientific['window']['input_cutoff_sha256'] or priority.evidence.guard.fingerprint(result.get('execution_ledger')) != proof.get('execution_ledger_sha256') or priority.evidence.guard.fingerprint(priority.evidence.guard.result_fields(result)) != proof.get('result_sha256') or priority.evidence.guard.fingerprint(priority.evidence.guard.decoded(proof.get('effective_config_values'))) != proof.get('effective_config_sha256'):
        raise ValueError('worker measurement actual engine artifacts mismatch')
    gain = result.get('gain_pct')
    if result.get('valid') not in (True, False) or not isinstance(gain, (int, float)) or not math.isfinite(gain):
        raise ValueError('missing or nonfinite actual measurement remains unknown')
    row = {key: cell[key] for key in ('tab', 'row', 'switch', 'cand')}
    identity = priority.evidence.guard.fingerprint(dict(row=row, filter=cell['filter'], opt=cell['opt'], scientific_context=scientific))
    bound = dict(schema='current_yellow_measurement/1', cell_identity=identity, scientific_identity=scientific, engine_proof={key: proof[key] for key in ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256')}, evaluation_status='evaluated_valid' if result['valid'] else 'evaluated_inadmissible', worker_source=source)
    return priority.SourceBoundMeasurement(record=priority._freeze(bound), source=priority._freeze(source))


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
    def __init__(self, context_factory, classify, reserve, state, budget, row_status, completion_audit, capture_directory=None):
        self.context_factory = context_factory
        self.classify = classify
        self.reserve = reserve
        self.state = copy.deepcopy(state)
        self.budget = budget
        self.row_status = row_status
        self.completion_audit = completion_audit
        self.attempts = {}
        self.batch_epoch = None
        self.capture_directory = capture_directory
        self.batch_context = None
        self.measurements = {}
        self.legal_rows = {}
    def begin_batch(self, chain):
        context = self.context_factory(copy.deepcopy(chain))
        priority.validate_epoch_sources(context['current_epoch'])
        self.batch_epoch = context['current_epoch']
        self.batch_context = context
    def worker_capture_spec(self):
        if self.batch_epoch is None or self.capture_directory is None:
            raise ValueError('explicit isolated worker evidence directory and validated batch required')
        path = priority.Path(self.capture_directory)
        if not path.is_dir() or path.is_symlink():
            raise ValueError('worker evidence directory must already exist and be isolated')
        return dict(directory=str(path), source_epoch=self.batch_epoch, npz_sha256=priority.scientific_identity(self.batch_context)['npz_sha256'])
    def verify_queue(self, queue):
        order = (self.batch_context or {}).get('row_order_proof')
        if not isinstance(order, VerifiedRowOrder):
            raise ValueError('actual physical row order proof required')
        resolved = [dict(tab=tab, row=row, switch=switch, cand=cand) for tab, row, switch, cand in queue]
        if priority.evidence.guard.fingerprint(resolved) != order['physical_row_order_sha256']:
            raise ValueError('resolved pilot queue differs from source-bound physical template order')
        return True
    def end_batch(self):
        if self.batch_epoch is None:
            raise ValueError('evaluation batch was not source-validated')
        priority.validate_epoch_sources(self.batch_epoch)
        self.batch_epoch = None
        self.batch_context = None
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
        self.legal_rows[priority.evidence.guard.fingerprint(row)] = legal
        def classify(cell):
            resolved = dict(self.classify(cell, context))
            identity = priority.evidence.guard.fingerprint(dict(row=row, filter=cell['filter'], opt=cell['opt'], scientific_context=priority.scientific_identity(context)))
            if identity in self.measurements:
                resolved['measurement_proof'] = self.measurements[identity]
            return resolved
        allocation = priority.first_pass_yellows(cells, row, self.state, context, self.budget(row, context), classify)
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
    def observe_result(self, row, label, settings, result, error, chain):
        legal = self.legal_rows.get(priority.evidence.guard.fingerprint(row))
        if legal is None or label not in legal['h2f'] or error or result is None or self.capture_directory is None:
            return None
        context = self.context_factory(copy.deepcopy(chain))
        binding = legal['h2f'][label]
        cell = dict(row, filter=binding['filter'], opt=binding['opt'])
        prefix = priority.evidence.guard.fingerprint(settings)
        result_hash = priority.evidence.guard.fingerprint(priority.evidence.guard.result_fields(result))
        for path in sorted(priority.Path(self.capture_directory).glob(prefix + '_*.json')):
            source = dict(path=str(path), sha256=priority.evidence.sha(path))
            try:
                measurement = bind_worker_measurement(source, cell, context, settings)
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
            if measurement['record']['engine_proof']['result_sha256'] != result_hash:
                continue
            self.measurements[measurement['record']['cell_identity']] = measurement
            return measurement
        return None
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
