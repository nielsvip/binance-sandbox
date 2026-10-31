"""Review-only native quartet proof and postbaseline experiment queue; no runtime hook."""
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from tools import v18_experience as experience


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def read_native_components(path, expected_sha256, eval_ids):
    path = Path(path).resolve()
    if file_sha(path) != expected_sha256:
        raise ValueError('source snapshot hash mismatch')
    result = {}
    with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for name, eval_id in eval_ids.items():
            raw = connection.execute('SELECT * FROM evals WHERE eval_id=?', (eval_id,)).fetchone()
            if raw is None:
                raise ValueError('missing exact component eval_id')
            row = dict(raw)
            base = connection.execute('SELECT gz FROM sets WHERE set_id=?', (row['base_set_id'],)).fetchone()
            if base is None:
                raise ValueError('missing exact full base set')
            values = json.loads(gzip.decompress(base[0]))
            if experience.Canon(values).set_id != row['base_set_id']:
                raise ValueError('producer canonical base hash mismatch')
            if row['delta_rule'] != 'explicit' or row['provenance'] != 'native':
                raise ValueError('native explicit delta required')
            config = experience.apply_delta(values, json.loads(row['delta'] or '{}'))
            config_sha = experience.Canon(config).set_id
            if not row.get('set_id') or config_sha != row['set_id']:
                raise ValueError('missing or inconsistent exact component set hash')
            extra = json.loads(row.get('extra') or '{}')
            result[name] = dict(row, config=config, config_sha256=config_sha, source_file=str(path), source_sha256=expected_sha256, engine_sha256=extra.get('engine_sha256'), market_data_end=extra.get('market_data_end'))
    if file_sha(path) != expected_sha256:
        raise ValueError('source changed during component extraction')
    return result


def verify_quartet(components, x, y):
    reasons = []
    if set(components) != {'B', 'X', 'Y', 'Z'}:
        return {'status': 'unverified', 'reasons': ['four_exact_components_required']}
    if x.get('kind') not in ('switch', 'filter') or y.get('kind') not in ('switch', 'filter') or not x.get('field') or not y.get('field') or x['field'] == y['field']:
        return {'status': 'unverified', 'reasons': ['distinct_typed_fields_required']}
    rows = list(components.values())
    for row in rows:
        if row.get('valid') != 1 or not isinstance(row.get('gain'), (int, float)) or not math.isfinite(row['gain']) or row.get('invalid_reason'):
            reasons.append('invalid_or_nonfinite_component')
        if not re.fullmatch(r'[a-f0-9]{64}', str(row.get('engine_sha256') or '')):
            reasons.append('unknown_engine_era')
        if not row.get('market_data_end'):
            reasons.append('unknown_market_data_end')
        if not re.fullmatch(r'[a-f0-9]{64}', str(row.get('source_sha256') or '')) or not row.get('eval_id'):
            reasons.append('missing_component_reference')
    context_keys = ('sym_side', 'cat_side', 'base_set_id', 'window_days', 'offset_days', 'npz_md5', 'engine_sha256', 'market_data_end')
    for key in context_keys:
        values = [row.get(key) for row in rows]
        if any(value is None or value == '' for value in values) or any(value != values[0] for value in values[1:]):
            reasons.append('missing_or_mixed_' + key)
    base = dict(components['B']['config'])
    expected = {'B': base, 'X': dict(base, **{x['field']: x['value']}), 'Y': dict(base, **{y['field']: y['value']}), 'Z': dict(base, **{x['field']: x['value'], y['field']: y['value']})}
    if x['field'] in base and base[x['field']] == x['value'] or y['field'] in base and base[y['field']] == y['value']:
        reasons.append('identity_is_not_an_intervention')
    for name, row in components.items():
        if row['config'] != expected[name] or experience.Canon(row['config']).set_id != row.get('config_sha256'):
            reasons.append('component_config_mismatch_' + name)
    if reasons:
        return {'status': 'unverified', 'reasons': sorted(set(reasons)), 'components': components}
    kind = 'SWITCH_FILTER' if x['kind'] != y['kind'] else (x['kind'] + '_' + y['kind']).upper()
    gain = {name: row['gain'] for name, row in components.items()}
    proof = {'schema': 'verified_native_interaction/1', 'kind': kind, 'x': x, 'y': y, 'context': {key: rows[0][key] for key in context_keys}, 'components': components, 'interaction_delta': gain['Z'] - gain['X'] - gain['Y'] + gain['B'], 'status': 'verified_measurement', 'quality_oos': False}
    proof['proof_sha256'] = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return proof


def postbaseline_queue(proofs, accepted_baseline, eligible, limit):
    baseline_sha = experience.Canon(accepted_baseline).set_id
    queued, diagnostics = [], []
    for proof in proofs:
        if proof.get('status') != 'verified_measurement':
            diagnostics.append({'reason': 'unverified_source', 'proof': proof})
            continue
        x, y = proof['x'], proof['y']
        if eligible(x, y, accepted_baseline) is not True:
            diagnostics.append({'reason': 'outside_actual_current_domain', 'proof_sha256': proof['proof_sha256']})
            continue
        configs = [dict(accepted_baseline), dict(accepted_baseline, **{x['field']: x['value']}), dict(accepted_baseline, **{y['field']: y['value']}), dict(accepted_baseline, **{x['field']: x['value'], y['field']: y['value']})]
        queued.append({'kind': proof['kind'], 'source_proof_sha256': proof['proof_sha256'], 'baseline_sha256': baseline_sha, 'phase': 'postbaseline_only', 'measurements_required': [{'component': name, 'config': config, 'config_sha256': experience.Canon(config).set_id} for name, config in zip(('B', 'X', 'Y', 'Z'), configs)], 'current_interaction_verified': False})
    return {'schema': 'interaction_experiment_queue/1', 'queue': queued[:max(0, limit)], 'deferred_budget': len(queued[max(0, limit):]), 'diagnostics': diagnostics, 'runtime_hooked': False, 'baseline_modified': False}
