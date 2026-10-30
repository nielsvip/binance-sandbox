"""Previous accepted baseline pins; actual engine capture, never expected metric invention."""
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path


class PinnedBaselineError(SystemExit):
    pass


def normalized(value):
    if isinstance(value, float) and not math.isfinite(value):
        return {'nonfinite_float': 'nan' if math.isnan(value) else ('positive_infinity' if value > 0 else 'negative_infinity')}
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
    if pin.get('schema') != 'v15_pinned_baseline/1' or pin.get('sym_side') != sym_side or pin.get('window_days') != window_days:
        raise PinnedBaselineError('baseline pin scope mismatch')
    if hashlib.sha256(Path(npz).read_bytes()).hexdigest() != pin.get('npz_sha256'):
        raise PinnedBaselineError('baseline NPZ differs from previous accepted fixture')
    source = pin.get('accepted_source', {})
    if not source.get('sha256') or not source.get('previous_acceptance_id'):
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
    if oracle.get('window_days') != window_days or oracle.get('holdout_days') != pin.get('holdout_days') or float(pin.get('holdout_days', 0)) != float(os.environ.get('V15_HOLDOUT_DAYS', '0')):
        raise PinnedBaselineError('previous oracle window/holdout policy differs')
    defaults = {key: value for key, value in defaults.items() if str(key).isupper() and not callable(value)}
    previous = pin.get('previous_effective_defaults', {})
    defaults_source = pin.get('previous_defaults_source', {})
    defaults_path = Path(defaults_source.get('path', ''))
    if not defaults_path.is_file() or defaults_path.is_symlink() or hashlib.sha256(defaults_path.read_bytes()).hexdigest() != defaults_source.get('sha256'):
        raise PinnedBaselineError('previous effective defaults source bytes required')
    previous_source = json.loads(defaults_path.read_text())
    for key in defaults_source.get('settings_pointer', []):
        previous_source = previous_source[key]
    if fingerprint(previous_source) != fingerprint(previous):
        raise PinnedBaselineError('previous effective defaults differ from anchored source')
    settings = dict(pin.get('accepted_full_settings', {}))
    if not previous or not settings or not set(previous).issubset(settings):
        raise PinnedBaselineError('complete previous defaults and accepted full settings required')
    known_previous = set(previous) | set(settings)
    new = set(defaults) - known_previous
    neutral = pin.get('neutral_new_keys', {})
    if set(neutral) != new:
        raise PinnedBaselineError('every new key requires explicit previous-behavior neutral manifest')
    if known_previous - set(defaults):
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



RESULT_FIELDS = frozenset('symside offset_days gain_pct gain_per_mo bh_pct bh_per_mo delta_vs_bh delta_per_mo max_dd_pct net_gain_dollars max_invested_dollars avg_invested_dollars flat_gain_pct sizing_lift_pct size_return_corr lifecycles engine_max_dd_pct_RAW tim_pct wr_pct engine_wr_RAW pool_sharpe trades ledger execution_ledger peak_notional exposure_series equity_curve seeded gain_dollars mean_deployed spike_trades_dropped spike_frac behavior_fingerprint htf_align years bars window_days data_days favorable_capture_pct adverse_exposure_pct directional_precision_pct slope_size_alignment valid invalid_reason beats_bh beats_bh_10x score binding'.split())
RESULT_SEPARATE_IDENTITY = frozenset(['overrides', 'npz_provenance'])


def result_fields(result):
    unknown = set(result) - RESULT_FIELDS - RESULT_SEPARATE_IDENTITY
    if unknown:
        raise PinnedBaselineError(f'unreviewed baseline result fields: {sorted(unknown)}')
    return {key: value for key, value in result.items() if key in RESULT_FIELDS}


def capture_actual(prepared, settings, evaluate, window_days):
    import v12_quick_engine as engine
    original = engine.ensure_template_layer
    configs = []
    def ensure(cfg, symbol, is_long):
        effective = original(cfg, symbol, is_long)
        configs.append({field.name: getattr(effective, field.name) for field in dataclasses.fields(engine.QuickConfig)})
        return effective
    engine.ensure_template_layer = ensure
    try:
        result = evaluate(prepared, dict(settings), window_days=window_days, include_ledger=True)
    finally:
        engine.ensure_template_layer = original
    ledger = result.get('execution_ledger')
    if not configs or not isinstance(ledger, list):
        raise PinnedBaselineError('real engine configuration and execution ledger required; cache-only proof forbidden')
    timestamps = (prepared.get('npz_prepared') or {}).get('timestamps')
    if timestamps is None:
        raise PinnedBaselineError('actual prepared input cutoff identity required')
    cutoff = fingerprint({'timestamps': timestamps, 'window_days': prepared.get('window_days'), 'offset_days': prepared.get('offset_days', 0)})
    return result, {'input_cutoff_sha256': cutoff, 'effective_config_sha256': fingerprint(configs), 'effective_config_values': normalized(configs), 'execution_ledger_sha256': fingerprint(ledger), 'result_sha256': fingerprint(result_fields(result))}


def enforce_actual(pin, prepared, settings, evaluate, window_days):
    assert_settings(pin, settings)
    try:
        result, actual = capture_actual(prepared, settings, evaluate, window_days)
    except Exception as error:
        raise PinnedBaselineError('actual baseline capture failed; optimizer forbidden') from error
    proof = pin.get('previous_actual_engine_proof', {})
    previous_configs = proof.get('effective_config_values')
    if not previous_configs or fingerprint(previous_configs) != proof.get('effective_config_sha256'):
        raise PinnedBaselineError('actual previous-version complete effective configuration source required')
    neutral = pin.get('neutral_new_keys', {})
    expected_configs = []
    for previous in previous_configs:
        extended = dict(previous)
        for key, entry in neutral.items():
            if key in extended:
                raise PinnedBaselineError('neutral-new-key declaration shadows previous effective config')
            extended[key] = entry['value']
        expected_configs.append(extended)
    if fingerprint(expected_configs) != actual['effective_config_sha256'] or any(proof.get(key) != actual[key] for key in ['input_cutoff_sha256', 'execution_ledger_sha256', 'result_sha256']):
        raise PinnedBaselineError('initial effective configuration, real execution ledger or result drifted; no optimizer permitted')
    return result
