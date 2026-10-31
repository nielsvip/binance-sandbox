"""Explicit research factory for the isolated pilot consumer; no default paths or deployment."""
import copy
import json
from pathlib import Path
import os

from tools import v15_nextgen_pilot_consumer as consumer
from tools import v15_nextgen_protection_priority as priority


def write_immutable(path, document):
    payload = json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError('immutable runtime evidence already exists with different contents')
    else:
        with path.open('xb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return dict(path=str(path), sha256=priority.evidence.sha(path))


def build(spec, prepared, progress, current_chain, defaults, exact_classifier):
    if spec.get('schema') != 'nextgen_runtime_factory/1' or spec.get('model_usage') != 'research_priority_only':
        raise ValueError('explicit research-only factory specification required; no holdout qualification implied')
    sources = copy.deepcopy(spec['sources'])
    if any(Path(source['path']).is_symlink() for source in sources):
        raise ValueError('factory source symlink refused')
    epoch = priority.bind_epoch(sources)
    priority.validate_epoch_sources(epoch)
    lookup = {source['asset']: source for source in sources}
    required = ('v12_quick_engine.py', 'v15_pilot.py', 'tools/opt/evaluate_v12.py', 'tools/opt/v12_pilot.py', 'tools/v15_kindergarten_first.py')
    if any(asset not in lookup for asset in required):
        raise ValueError('actual pilot/engine/evaluator/KG source pins required')
    if Path(exact_classifier.__code__.co_filename).resolve() != Path(lookup['v15_pilot.py']['path']).resolve():
        raise ValueError('classifier must be the actual source-bound pilot closure')
    if priority.evidence.guard.fingerprint(defaults) != spec['defaults_sha256']:
        raise ValueError('actual pilot defaults differ from pinned factory snapshot')
    template = lookup[spec['template_asset']]
    npz = lookup[spec['npz_asset']]
    order = consumer.bind_row_order(template, spec['tabs'])
    arrays_hash = priority.evidence.prepared_arrays_identity(prepared)
    if arrays_hash != spec['prepared_arrays_sha256']:
        raise ValueError('actual prepared arrays differ from source-bound prepared snapshot')
    window = dict(window_days=prepared['window_days'], offset_days=prepared.get('offset_days', 0), input_cutoff_sha256=priority.evidence.guard.fingerprint(dict(timestamps=prepared['npz_prepared']['timestamps'], window_days=prepared['window_days'], offset_days=prepared.get('offset_days', 0))))
    contract = copy.deepcopy(progress.get('kindergarten_first_contract') or {})
    results = copy.deepcopy(progress.get('kindergarten_first'))
    if contract.get('schema') != 'v15_kindergarten_phase/1' or contract.get('completed') is not True or contract.get('identity') != spec['baseline_identity'] or contract.get('results_sha256') != priority.evidence.guard.fingerprint(results):
        raise ValueError('actual retained kindergarten phase receipt/result binding required')
    if not isinstance(results, dict) or any(results.get(key) for key in ('error', 'singles_unmeasured', 'singles_never_ran', 'grey_rows')):
        raise ValueError('kindergarten phase contains unresolved actual evaluations')
    directory = Path(spec['evidence_directory'])
    reservations = Path(spec['reservation_directory'])
    if any(not path.is_dir() or path.is_symlink() for path in (directory, reservations)):
        raise ValueError('explicit precreated isolated evidence/reservation directories required')
    phase_path = directory / ('retained_kg_' + priority.evidence.guard.fingerprint(contract) + '.json')
    if phase_path.exists():
        phase = json.loads(phase_path.read_text())
        if phase.get('contract') != contract or phase.get('results') != results:
            raise ValueError('retained KG snapshot changed on resume')
    else:
        if contract.get('output_chain_sha256') != priority.evidence.guard.fingerprint(current_chain):
            raise ValueError('resumed winning chain requires existing immutable KG snapshot; never reset winner')
        phase = dict(schema='retained_actual_kg_snapshot/1', contract=contract, results=results, output_chain=priority.evidence.guard.normalized(current_chain), source_epoch_sha256=epoch['id'])
        write_immutable(phase_path, phase)
    if phase.get('source_epoch_sha256') != epoch['id'] or priority.evidence.guard.fingerprint(priority.evidence.guard.decoded(phase['output_chain'])) != contract.get('output_chain_sha256'):
        raise ValueError('KG phase belongs to another source epoch or output chain')
    policy_source = lookup[spec['budget_policy_asset']]
    policy = json.loads(Path(policy_source['path']).read_text())
    budget_rows = policy['row_yellow_budgets']
    bound_domain = priority.bind_domain_sources(epoch, [template, lookup['v15_pilot.py'], lookup['v12_quick_engine.py']])
    defaults_hash = priority.evidence.guard.fingerprint(defaults)
    def context(chain):
        if priority.evidence.guard.fingerprint(defaults) != defaults_hash:
            raise ValueError('pilot defaults changed during source-bound batch')
        return dict(phase='post_kg_first_worst_pass', row_order='worst_first', row_order_proof=order, current_epoch=epoch, model_usage=spec['model_usage'], first_pass_start_chain_sha256=contract['output_chain_sha256'], scientific_identity=dict(engine_sha256=lookup['v12_quick_engine.py']['sha256'], npz_sha256=npz['sha256'], accepted_chain_sha256=priority.evidence.guard.fingerprint(chain), wiring_epoch_sha256=epoch['id'], sym_side=spec['sym_side'], window=window))
    def classify(cell, scientific):
        resolved = exact_classifier(cell, scientific)
        if not isinstance(resolved, dict):
            raise ValueError('actual pilot classifier returned no explicit verdict')
        return dict(resolved, bound_domain=bound_domain, evidence_refs=[])
    def budget(row, scientific):
        key = priority.evidence.guard.fingerprint(row)
        value = budget_rows.get(key)
        if type(value) is not int or value < 0:
            raise ValueError('actual row has no explicit caller-bound yellow budget')
        return value
    def reserve(allocation, scientific):
        identity = priority.evidence.guard.fingerprint(dict(allocation=allocation, scientific_identity=priority.scientific_identity(scientific)))
        record = dict(schema='first_pass_reservation/1', status='reserved', allocation_sha256=identity, allocation=allocation, scientific_identity=priority.scientific_identity(scientific), measurement_claimed=False, deferred_debt=allocation['deferred'])
        source = write_immutable(reservations / (identity + '.json'), record)
        return dict(status='reserved', allocation_sha256=identity, source=source)
    return consumer.FirstPassConsumer(context, classify, reserve, copy.deepcopy(spec.get('allocation_state') or {}), budget, lambda row, scientific: spec.get('row_status', {}).get(priority.evidence.guard.fingerprint(row), 'unobserved'), capture_directory=str(directory), budget_policy_source=policy_source)
