"""Evaluate an independent previous-version baseline only; never invoke its optimizer."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import time
from v15_nextgen_baseline_guard import capture_actual, fingerprint
from v15_nextgen_bench_isolation import install_audit


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pinned(pin):
    path = Path(pin['path'])
    if path.is_symlink() or sha(path) != pin['sha256']:
        raise ValueError(f'previous source pin changed: {path}')
    return json.loads(path.read_text())


def selected(pin):
    value = pinned(pin)
    for key in pin.get('settings_pointer', []):
        value = value[key]
    if not isinstance(value, dict):
        raise ValueError('previous settings/defaults source must select an object')
    return value


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    if request.get('schema') != 'v15_prior_baseline_request/1':
        raise ValueError('independent previous-baseline request required')
    root, side = Path(request['reference_root']).resolve(), Path(request['side_dir']).resolve()
    if root.parent != side.parent or root == side:
        raise ValueError('previous root and private side must be distinct siblings')
    if any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('previous evaluator root must be fully materialized')
    runtime = pinned(request['previous_runtime_manifest'])
    policy = pinned(request['previous_policy_receipt'])
    if policy.get('effective_policy', {}).get('V18_LINKNET_FIRST') != '0':
        raise ValueError('previous baseline must preserve proven inactive proposal consumer')
    settings, defaults = selected(request['accepted_source']), selected(request['previous_defaults_source'])
    if not request['accepted_source'].get('previous_acceptance_id') or not settings or not defaults:
        raise ValueError('independently accepted full settings and complete previous defaults required')
    expected = runtime['files']
    materialization = pinned(request['materialization_receipt'])
    if materialization.get('previous_runtime_manifest_sha256') != request['previous_runtime_manifest']['sha256'] or materialization.get('reference_root') != str(root):
        raise ValueError('immutable previous-runtime materialization proof mismatch')
    route = pinned(request['routing_manifest'])
    for relative, metadata in materialization['files'].items():
        path = root / relative
        if relative in route.get('source_sha256', {}):
            if expected.get(relative) != route['source_sha256'][relative] or sha(path) != route['candidate_sha256'][relative]:
                raise ValueError('previous routing lineage changed')
            continue
        stat = path.stat()
        if stat.st_size != metadata['bytes'] or stat.st_mtime_ns != metadata['mtime_ns'] or stat.st_ino != metadata['inode']:
            raise ValueError(f'previous immutable file changed since full hash verification: {relative}')
    for relative in ['v12_quick_engine.py', 'tools/opt/evaluate_v12.py', 'tools/opt/v12_pilot.py']:
        if sha(root / relative) != expected[relative]:
            raise ValueError(f'previous evaluator/engine differs from independent runtime: {relative}')
    npz = root / 'backtest_v8/indicators' / (request['sym_side'].rsplit('_', 1)[0] + '.npz')
    if sha(npz) != request['npz_sha256']:
        raise ValueError('previous baseline NPZ differs from intended frozen bytes')
    memory = next(int(line.split()[1]) / 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    disk = shutil.disk_usage(root).free / (1 << 20)
    if memory < 7000 or disk < 20000:
        raise ValueError(f'previous-baseline launch held: MemAvailable={memory:.1f}MiB disk={disk:.1f}MiB; original gate floors retained')
    side.mkdir(parents=True, exist_ok=True)
    output = side / 'previous_baseline_oracle.json'
    if output.exists():
        raise ValueError('new oracle side directory required; preserve prior receipt')
    env = json.loads(Path(request['prepared_env_file']).read_text())
    if env.get('BASE_PATH') != str(root) or env.get('V18_LINKNET_FIRST') != '0' or env.get('V18_LINKNET_BUNDLE_REQUIRED') != '0':
        raise ValueError('private exact previous-policy environment required')
    if float(env.get('V15_HOLDOUT_DAYS', 0)) != float(request['holdout_days']):
        raise ValueError('previous oracle must use exact official holdout policy')
    os.environ.update(env)
    sys.dont_write_bytecode = True
    protected = {str(path.resolve()) for path in root.rglob('*') if path.is_file()}
    def readonly_inputs(event, values):
        if event == 'open' and isinstance(values[0], (str, bytes, os.PathLike)):
            path = str(Path(os.fsdecode(values[0])).resolve())
            mode, flags = values[1], values[2]
            writing = any(char in (mode or '') for char in 'wax+') or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and path in protected:
                raise PermissionError('oracle may not modify materialized immutable input')
        elif event in ['os.remove', 'os.unlink', 'os.rename', 'os.replace']:
            paths = values[:2] if event in ['os.rename', 'os.replace'] else values[:1]
            if any(isinstance(value, (str, bytes, os.PathLike)) and str(Path(os.fsdecode(value)).resolve()) in protected for value in paths):
                raise PermissionError('oracle may not replace immutable input')
    sys.addaudithook(readonly_inputs)
    install_audit(root, side)
    os.chdir(root)
    sys.path.insert(0, str(root))
    print(json.dumps({'phase': 'IMPORT_PREVIOUS_RUNTIME_NO_OPTIMIZER', 'pid': os.getpid(), 'at_utc': utc()}), flush=True)
    started = time.monotonic()
    import numpy
    expected_environment = request['expected_environment']
    actual_environment = {'sys_prefix': sys.prefix, 'python_version': sys.version, 'numpy_version': numpy.__version__}
    if actual_environment != expected_environment:
        raise ValueError('previous execution environment identity differs')
    spec = importlib.util.spec_from_file_location('independent_previous_pilot', root / 'v15_pilot.py')
    pilot = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pilot
    spec.loader.exec_module(pilot)
    pilot._install_holdout(request['sym_side'])
    pilot.install_sim_account_hook(request['sym_side'])
    if float(request['holdout_days']) > 0 and not pilot._HOLDOUT.get('installed'):
        raise ValueError('actual previous holdout installer did not install; no oracle')
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    prepared = prepare_batch(request['sym_side'], request['window_days'])
    if not prepared or prepared.get('_stale'):
        raise ValueError('previous actual engine preparation failed')
    origins = {}
    for name, module in list(sys.modules.items()):
        if name == 'config' or name == 'config_tradier' or name == 'v12_quick_engine' or name.startswith(('tools.', 'vec_paths.', 'vec_decisions.')):
            filename = getattr(module, '__file__', None)
            if filename:
                path = Path(filename).resolve()
                if root not in path.parents:
                    raise ValueError(f'first-party dependency escaped previous root: {name}={path}')
                origins[name] = str(path.relative_to(root))
    print(json.dumps({'phase': 'ACTUAL_PREVIOUS_BASELINE_EVALUATION' , 'pid': os.getpid(), 'at_utc': utc()}), flush=True)
    window_context = {key: pilot._HOLDOUT.get(key) for key in ['days', 'confirm_days', 'cut', 'confirm_end', 'on', 'installed']}
    os.environ['V15_BASELINE_WINDOW_CONTEXT'] = json.dumps(window_context, sort_keys=True)
    os.environ['V15_BASELINE_RAW_CAPTURE'] = str(side / ('previous_raw_capture_' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.pickle'))
    os.environ['V15_BASELINE_REQUEST_SHA256'] = sha(args.request)
    result, proof = capture_actual(prepared, settings, evaluate_prepared_sanitized, request['window_days'])
    if sha(npz) != request['npz_sha256']:
        raise ValueError('frozen NPZ changed during previous baseline evaluation')
    ledger_path = side / 'previous_execution_ledger.json'
    ledger_path.write_text(json.dumps(result['execution_ledger'], default=lambda value: value.item() if hasattr(value, 'item') else value.tolist(), allow_nan=False))
    proof['oracle'] = {'evaluated_at_utc': utc(), 'previous_runtime_manifest_sha256': request['previous_runtime_manifest']['sha256'], 'previous_engine_sha256': expected['v12_quick_engine.py'], 'previous_evaluator_sha256': expected['tools/opt/evaluate_v12.py'], 'npz_sha256': request['npz_sha256'], 'accepted_settings_sha256': fingerprint(settings), 'window_days': request['window_days'], 'holdout_days': request['holdout_days'], 'window_context': window_context, 'environment': actual_environment, 'module_origins': origins, 'materialization_receipt_sha256': request['materialization_receipt']['sha256'], 'ledger_source': {'path': str(ledger_path), 'sha256': sha(ledger_path)}}
    usage = resource.getrusage(resource.RUSAGE_SELF)
    receipt = {'schema': 'v15_previous_baseline_oracle/1', 'status': 'MEASURED_PREVIOUS_BASELINE_ONLY_NOT_CANDIDATE_PARITY', 'request_sha256': sha(args.request), 'accepted_source': request['accepted_source'], 'previous_defaults_source': request['previous_defaults_source'], 'previous_actual_engine_proof': proof, 'resources': {'wall_s': time.monotonic() - started, 'self_cpu_s': usage.ru_utime + usage.ru_stime, 'self_maxrss_linux_kib': usage.ru_maxrss, 'measurement_scope': 'this direct evaluator process; no optimizer launched'}, 'audit_scope': 'audited Python operations, not all native syscalls'}
    output.write_text(json.dumps(receipt, indent=2))
    print(json.dumps({'phase': 'PREVIOUS_BASELINE_ORACLE_SAVED', 'receipt': str(output), 'at_utc': utc()}), flush=True)


if __name__ == '__main__':
    main()
