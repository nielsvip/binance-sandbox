"""Previous accepted baseline pins; actual engine capture, never expected metric invention."""
import dataclasses
import copy
import pickle
import datetime
import hashlib
import json
import math
import os
from pathlib import Path


class PinnedBaselineError(SystemExit):
    pass


def normalized(value):
    tag = '__v15_baseline_type__'
    if isinstance(value, Path):
        scope, text = 'absolute' if value.is_absolute() else 'relative', str(value)
        for name, environment in [('root', 'V15_BENCH_ROOT'), ('side', 'V15_BENCH_SIDE')]:
            base = os.environ.get(environment)
            if base and value.is_absolute():
                try:
                    text, scope = str(value.relative_to(Path(base))), name
                    break
                except ValueError:
                    pass
        result = {tag: 'path', 'scope': scope, 'value': text}
        if value.is_file():
            result['asset_sha256'] = hashlib.sha256(value.read_bytes()).hexdigest()
        return result
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: normalized(item) for key, item in value.items()}
        items = [[normalized(key), normalized(item)] for key, item in value.items()]
        return {tag: 'dict', 'items': sorted(items, key=lambda item: json.dumps(item[0], sort_keys=True))}
    if isinstance(value, tuple):
        return {tag: 'tuple', 'items': [normalized(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        return {tag: 'frozenset' if isinstance(value, frozenset) else 'set', 'items': sorted([normalized(item) for item in value], key=lambda item: json.dumps(item, sort_keys=True))}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if type(value) is datetime.timedelta:
        return {tag: 'timedelta', 'days': value.days, 'seconds': value.seconds, 'microseconds': value.microseconds}
    if type(value) in (datetime.datetime, datetime.date, datetime.time):
        return {tag: type(value).__name__, 'value': value.isoformat()}
    if hasattr(value, 'dtype') and hasattr(value, 'tolist'):
        return {tag: 'numpy', 'dtype': str(value.dtype), 'shape': list(getattr(value, 'shape', ())), 'value': normalized(value.tolist())}
    if isinstance(value, float) and not math.isfinite(value):
        return {'nonfinite_float': 'nan' if math.isnan(value) else ('positive_infinity' if value > 0 else 'negative_infinity')}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise PinnedBaselineError(f'unsupported explicit baseline value type: {type(value).__module__}.{type(value).__qualname__}')


def decoded(value):
    if isinstance(value, list):
        return [decoded(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get('__v15_baseline_type__')
    if kind == 'path':
        scope = value['scope']
        base = os.environ.get('V15_BENCH_ROOT' if scope == 'root' else 'V15_BENCH_SIDE') if scope in ('root', 'side') else None
        if scope in ('root', 'side') and not base:
            raise PinnedBaselineError('routing root required to decode previous path')
        return Path(base) / value['value'] if base else Path(value['value'])
    if kind in ('tuple', 'set', 'frozenset'):
        return {'tuple': tuple, 'set': set, 'frozenset': frozenset}[kind](decoded(item) for item in value['items'])
    if kind == 'dict':
        return {decoded(key): decoded(item) for key, item in value['items']}
    if kind == 'timedelta':
        return datetime.timedelta(days=value['days'], seconds=value['seconds'], microseconds=value['microseconds'])
    if kind in ('datetime', 'date', 'time'):
        return getattr(datetime, kind).fromisoformat(value['value'])
    if kind == 'numpy':
        import numpy
        array = numpy.asarray(decoded(value['value']), dtype=value['dtype']).reshape(value['shape'])
        return array[()] if not value['shape'] else array
    if set(value) == {'nonfinite_float'}:
        return {'nan': float('nan'), 'positive_infinity': float('inf'), 'negative_infinity': float('-inf')}[value['nonfinite_float']]
    return {key: decoded(item) for key, item in value.items()}


def fingerprint(value):
    return hashlib.sha256(json.dumps(normalized(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_pin(path, sym_side, defaults, npz, window_days, supported_fields=None, global_fields=()):
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
    if not pin.get('window_context') or pin['window_context'] != oracle.get('window_context'):
        raise PinnedBaselineError('full previous fit/confirm/holdout window context required')
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
    accepted_settings = dict(pin.get('accepted_full_settings', {}))
    configs = proof.get('effective_config_values')
    if not accepted_settings or not isinstance(configs, list) or len(configs) != 1 or fingerprint(configs) != proof.get('effective_config_sha256'):
        raise PinnedBaselineError('independent previous actual effective config required; raw SQL/default key counts are not a configuration oracle')
    previous_config = decoded(configs[0])
    supported = set(supported_fields) if supported_fields is not None else set(defaults)
    managed = (set(global_fields) & supported) | {key for key, value in previous_config.items() if isinstance(value, Path)}
    per_sym = supported - managed
    retired = set(previous_config) - supported
    if retired:
        raise PinnedBaselineError(f'previous effective engine fields disappeared: {sorted(retired)}')
    new = supported - set(previous_config)
    neutral = pin.get('neutral_new_keys', {})
    if set(neutral) != new:
        raise PinnedBaselineError('every new key/engine field requires explicit previous-behavior neutral manifest')
    for key in new:
        entry = neutral[key]
        if 'value' not in entry or not entry.get('previous_behavior') or not entry.get('evidence_sha256'):
            raise PinnedBaselineError('neutral new key lacks explicit previous-behavior evidence')
    settings = {key: value for key, value in previous_config.items() if key in per_sym}
    settings.update({key: decoded(entry['value']) for key, entry in neutral.items() if key in per_sym})
    if set(settings) != per_sym:
        raise PinnedBaselineError('incomplete supported per-sym effective configuration')
    pin['global_context'] = {key: value for key, value in previous_config.items() if key in managed}
    pin['reader_transformations'] = {'raw_accepted_keys_not_engine_fields': sorted(set(accepted_settings) - supported), 'globally_managed_keys_not_per_sym_overrides': sorted(managed), 'prior_defaults_keys_not_engine_fields': sorted(set(previous) - supported), 'source': 'previous actual evaluator coercion/dependencies/template/market-mode context; effective config independently measured'}
    pin['settings'] = settings
    return pin


def assert_settings(pin, settings):
    if fingerprint(settings) != fingerprint(pin['settings']):
        raise PinnedBaselineError('accepted starting baseline was replaced or overlaid')



RESULT_FIELDS = frozenset('symside offset_days gain_pct gain_per_mo bh_pct bh_per_mo delta_vs_bh delta_per_mo max_dd_pct net_gain_dollars max_invested_dollars avg_invested_dollars flat_gain_pct sizing_lift_pct size_return_corr lifecycles engine_max_dd_pct_RAW tim_pct wr_pct engine_wr_RAW pool_sharpe trades ledger execution_ledger peak_notional exposure_series equity_curve seeded gain_dollars mean_deployed spike_trades_dropped spike_frac behavior_fingerprint htf_align years bars window_days data_days favorable_capture_pct adverse_exposure_pct directional_precision_pct slope_size_alignment valid invalid_reason beats_bh beats_bh_10x score binding'.split())
RESULT_SEPARATE_IDENTITY = frozenset(['overrides', 'npz_provenance', 'dropped_retired_keys', 'dep_forced'])


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
        configs.append(copy.deepcopy({field.name: getattr(effective, field.name) for field in dataclasses.fields(engine.QuickConfig)}))
        return effective
    engine.ensure_template_layer = ensure
    try:
        result = evaluate(prepared, dict(settings), window_days=window_days, include_ledger=True)
    finally:
        engine.ensure_template_layer = original
    raw_path = os.environ.get('V15_BASELINE_RAW_CAPTURE')
    if raw_path:
        path = Path(raw_path)
        census = [{key: type(value).__module__ + '.' + type(value).__qualname__ for key, value in config.items()} for config in configs]
        metadata = {'scope': 'trusted own-run diagnostic capture before codec; not a promotion artifact', 'request_sha256': os.environ.get('V15_BASELINE_REQUEST_SHA256'), 'at_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'config_type_census': census}
        try:
            with path.open('xb') as stream:
                prepared_identity = {'timestamps': (prepared.get('npz_prepared') or {}).get('timestamps'), 'window_days': prepared.get('window_days'), 'offset_days': prepared.get('offset_days', 0), 'npz_provenance': prepared.get('npz_provenance'), 'window_context': json.loads(os.environ.get('V15_BASELINE_WINDOW_CONTEXT', 'null'))}
                pickle.dump({'configs': configs, 'result': result, 'prepared_identity': prepared_identity}, stream, protocol=5)
            metadata['pickle_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as error:
            metadata['capture_error'] = repr(error)
        path.with_suffix('.provenance.json').write_text(json.dumps(metadata, indent=2))
    ledger = result.get('execution_ledger')
    if not configs or not isinstance(ledger, list):
        raise PinnedBaselineError('real engine configuration and execution ledger required; cache-only proof forbidden')
    timestamps = (prepared.get('npz_prepared') or {}).get('timestamps')
    if timestamps is None:
        raise PinnedBaselineError('actual prepared input cutoff identity required')
    cutoff = fingerprint({'timestamps': timestamps, 'window_days': prepared.get('window_days'), 'offset_days': prepared.get('offset_days', 0)})
    return result, {'reader_transformations': {key: normalized(result.get(key)) for key in ['dropped_retired_keys', 'dep_forced']}, 'input_cutoff_sha256': cutoff, 'effective_config_sha256': fingerprint(configs), 'effective_config_values': normalized(configs), 'execution_ledger_sha256': fingerprint(ledger), 'result_sha256': fingerprint(result_fields(result))}


def enforce_actual(pin, prepared, settings, evaluate, window_days, window_context=None):
    assert_settings(pin, settings)
    if pin.get('window_context') is not None and window_context != pin['window_context']:
        raise PinnedBaselineError('actual fit/confirm/holdout window changed')
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
        extended = dict(decoded(previous))
        for key, entry in neutral.items():
            if key in extended:
                raise PinnedBaselineError('neutral-new-key declaration shadows previous effective config')
            extended[key] = decoded(entry['value'])
        expected_configs.append(extended)
    if fingerprint(expected_configs) != actual['effective_config_sha256'] or any(proof.get(key) != actual[key] for key in ['input_cutoff_sha256', 'execution_ledger_sha256', 'result_sha256']):
        raise PinnedBaselineError('initial effective configuration, real execution ledger or result drifted; no optimizer permitted')
    return result
