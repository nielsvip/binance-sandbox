"""Previous accepted baseline pins; actual engine capture, never expected metric invention."""
import dataclasses
import hashlib
import json
from pathlib import Path


class PinnedBaselineError(SystemExit):
    pass


def normalized(value):
    if isinstance(value, dict):
        return {str(key): normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    if hasattr(value, 'tolist'):
        return normalized(value.tolist())
    if hasattr(value, 'item'):
        return normalized(value.item())
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(normalized(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_pin(path, sym_side, defaults, npz, window_days):
    pin = json.loads(Path(path).read_text())
    if window_days != 30:
        raise PinnedBaselineError('pinned baseline contract requires30D; no alternate-window workaround')
    if pin.get('schema') != 'v15_pinned_baseline/1' or pin.get('sym_side') != sym_side or pin.get('window_days') != window_days:
        raise PinnedBaselineError('baseline pin scope mismatch')
    if hashlib.sha256(Path(npz).read_bytes()).hexdigest() != pin.get('npz_sha256'):
        raise PinnedBaselineError('baseline NPZ differs from previous accepted fixture')
    source = pin.get('accepted_source', {})
    if not source.get('sha256') or not source.get('computed_at_utc') or not source.get('previous_acceptance_id'):
        raise PinnedBaselineError('previous accepted baseline provenance required')
    source_path = Path(source.get('path', ''))
    if not source_path.is_file() or source_path.is_symlink() or hashlib.sha256(source_path.read_bytes()).hexdigest() != source['sha256']:
        raise PinnedBaselineError('previous accepted settings source bytes changed')
    accepted = json.loads(source_path.read_text())
    for key in source.get('settings_pointer', []):
        accepted = accepted[key]
    if fingerprint(accepted) != fingerprint(pin.get('accepted_full_settings')):
        raise PinnedBaselineError('accepted full settings do not match declared previous source')
    proof = pin.get('previous_actual_engine_proof', {})
    oracle = proof.get('oracle', {})
    if oracle.get('npz_sha256') != pin.get('npz_sha256') or oracle.get('accepted_settings_sha256') != fingerprint(accepted) or not all(oracle.get(key) for key in ['evaluated_at_utc', 'previous_runtime_manifest_sha256', 'previous_engine_sha256', 'previous_evaluator_sha256']):
        raise PinnedBaselineError('independent previous-version actual-engine oracle required on identical frozen NPZ')
    previous = pin.get('previous_effective_defaults', {})
    settings = pin.get('accepted_full_settings', {})
    if not previous or not settings or set(settings) != set(previous):
        raise PinnedBaselineError('complete previous defaults and accepted full settings required')
    new = set(defaults) - set(previous)
    neutral = pin.get('neutral_new_keys', {})
    if set(neutral) != new:
        raise PinnedBaselineError('every new key requires explicit previous-behavior neutral manifest')
    if set(previous) - set(defaults):
        raise PinnedBaselineError('previous baseline keys disappeared')
    for key in new:
        entry = neutral[key]
        if 'value' not in entry or not entry.get('previous_behavior') or not entry.get('evidence_sha256'):
            raise PinnedBaselineError('neutral new key lacks explicit previous-behavior evidence')
        settings[key] = entry['value']
    if set(settings) != set(defaults):
        raise PinnedBaselineError('incomplete effective baseline key coverage')
    pin['settings'] = settings
    return pin


def assert_settings(pin, settings):
    if fingerprint(settings) != fingerprint(pin['settings']):
        raise PinnedBaselineError('accepted starting baseline was replaced or overlaid')


def capture_actual(prepared, settings, evaluate, window_days):
    import v12_quick_engine as engine
    original = engine.simulate_one
    configs = []
    def simulate(npz, symbol, is_long, cfg, *args, **kwargs):
        configs.append({field.name: getattr(cfg, field.name) for field in dataclasses.fields(engine.QuickConfig)})
        return original(npz, symbol, is_long, cfg, *args, **kwargs)
    engine.simulate_one = simulate
    try:
        result = evaluate(prepared, dict(settings), window_days=window_days, include_ledger=True)
    finally:
        engine.simulate_one = original
    ledger = result.get('execution_ledger')
    if not configs or not isinstance(ledger, list):
        raise PinnedBaselineError('real engine configuration and execution ledger required; cache-only proof forbidden')
    return result, {'effective_config_sha256': fingerprint(configs), 'execution_ledger_sha256': fingerprint(ledger), 'result_sha256': fingerprint(result)}


def enforce_actual(pin, prepared, settings, evaluate, window_days):
    assert_settings(pin, settings)
    try:
        result, actual = capture_actual(prepared, settings, evaluate, window_days)
    except Exception as error:
        raise PinnedBaselineError('actual baseline capture failed; optimizer forbidden') from error
    proof = pin.get('previous_actual_engine_proof', {})
    if not proof or any(proof.get(key) != value for key, value in actual.items()):
        raise PinnedBaselineError('initial effective configuration, real execution ledger or result drifted; no optimizer permitted')
    return result
