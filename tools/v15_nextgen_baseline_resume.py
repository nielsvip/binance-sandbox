"""Pinned initial baseline and resumed optimization chain have separate identities."""
import copy
import hashlib
import json
from pathlib import Path
from tools.v15_nextgen_baseline_guard import PinnedBaselineError, assert_settings, fingerprint

_ACTIVE = {}

def identity(pin):
    return {'pin_sha256': pin['pin_sha256'], 'npz_sha256': pin['npz_sha256'], 'window_days': pin['window_days'], 'window_context': pin['window_context']}

def validate(pin, progress):
    if not progress.get('pinned_baseline_resume') and not progress.get('done'):
        if progress.get('cumulative_overrides') is not None:
            assert_settings(pin, progress['cumulative_overrides'])
        return
    receipt = progress.get('pinned_baseline_resume') or {}
    if receipt.get('identity') != identity(pin):
        raise PinnedBaselineError('resume lacks matching original baseline provenance; explicit legacy migration required')
    if receipt.get('chain_sha256') != fingerprint(progress.get('cumulative_overrides', {})):
        raise PinnedBaselineError('saved optimization chain differs from pinned resume receipt')

def activate(path, pin):
    path = Path(path)
    if path.exists():
        validate(pin, json.loads(path.read_text()))
    _ACTIVE[str(path.resolve())] = copy.deepcopy(pin)

def stamp(path, progress):
    pin = _ACTIVE.get(str(Path(path).resolve()))
    if pin is not None:
        progress['pinned_baseline_resume'] = {'schema': 'v15_baseline_resume/1', 'identity': identity(pin), 'chain_sha256': fingerprint(progress.get('cumulative_overrides', pin['settings']))}

def migration_capture(pin, immutable_progress_path, prepared, evaluate, window_days, window_context):
    from tools.v15_nextgen_baseline_guard import capture_actual
    source = Path(immutable_progress_path)
    if source.is_symlink() or not source.is_file():
        raise PinnedBaselineError('immutable regular legacy progress source required')
    raw = source.read_bytes()
    progress = json.loads(raw)
    if not progress.get('done') or window_days != pin['window_days'] or window_context != pin['window_context']:
        raise PinnedBaselineError('legacy migration scope mismatch')
    chain = progress.get('cumulative_overrides')
    if not isinstance(chain, dict):
        raise PinnedBaselineError('legacy migration has no exact saved reader inputs')
    _, actual = capture_actual(prepared, chain, evaluate, window_days)
    if source.read_bytes() != raw:
        raise PinnedBaselineError('legacy source changed during actual chain evaluation')
    return {'schema': 'v15_legacy_resume_migration/1', 'identity': identity(pin), 'legacy_progress_sha256': hashlib.sha256(raw).hexdigest(), 'chain_sha256': fingerprint(chain), 'actual_current_chain': actual, 'scope': 'current-chain reevaluation only; historical cells retain original provenance and are not newly verified'}


def accept_migration(pin, immutable_progress_path, receipt, prepared, evaluate, window_days, window_context, npz_path):
    from tools.v15_nextgen_baseline_guard import enforce_actual
    npz = Path(npz_path)
    if npz.is_symlink() or not npz.is_file() or hashlib.sha256(npz.read_bytes()).hexdigest() != pin['npz_sha256']:
        raise PinnedBaselineError('migration frozen NPZ bytes differ')
    enforce_actual(pin, prepared, pin['settings'], evaluate, window_days, window_context=window_context)
    fresh = migration_capture(pin, immutable_progress_path, prepared, evaluate, window_days, window_context)
    for key in ('schema', 'identity', 'legacy_progress_sha256', 'chain_sha256'):
        if fresh.get(key) != receipt.get(key):
            raise PinnedBaselineError('legacy migration receipt identity differs')
    for key in ('effective_config_sha256', 'execution_ledger_sha256', 'result_sha256', 'input_cutoff_sha256'):
        if fresh['actual_current_chain'].get(key) != receipt.get('actual_current_chain', {}).get(key):
            raise PinnedBaselineError('legacy current-chain actual reevaluation differs')
    progress = json.loads(Path(immutable_progress_path).read_text())
    progress['legacy_resume_migration'] = copy.deepcopy(receipt)
    progress['pinned_baseline_resume'] = {'schema': 'v15_baseline_resume/1', 'identity': identity(pin), 'chain_sha256': fingerprint(progress['cumulative_overrides'])}
    return progress
