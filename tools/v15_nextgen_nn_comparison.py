"""Explicit, fail-closed NN comparison fixtures and sandbox runtime identity guards."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pinned_json(pin):
    path = Path(pin['path'])
    if path.is_symlink() or sha(path) != pin['sha256']:
        raise ValueError(f'comparison manifest changed: {path}')
    return json.loads(path.read_text())


def check_files(root, files):
    root = Path(root)
    for relative, expected in files.items():
        path = root / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ValueError(f'comparison runtime mismatch: {relative}')


def bundle_files(root, declaration):
    try:
        from .v15_nextgen_gate_artifacts import declared_relative
    except ImportError:
        from v15_nextgen_gate_artifacts import declared_relative
    expected = declaration['expected_sha256_by_filename']
    files = {declared_relative(name): value for name, value in expected.items()}
    check_files(root, files)
    files['tools/v18_model_bundle.json'] = sha(Path(root) / 'tools/v18_model_bundle.json')
    return files


def validate(spec):
    if spec.get('schema') != 'v15_nn_comparison/1':
        raise ValueError('unknown NN comparison descriptor')
    runtime = pinned_json(spec['runtime'])
    if runtime.get('schema') != 'v15_nn_runtime/1':
        raise ValueError('unknown frozen runtime schema')
    check_files(runtime['code_root'], runtime['files'])
    compatibility = spec.get('compatibility', {})
    if set(compatibility) != {'tools/v15_v18_planner.py', 'tools/v18_brain.py'}:
        raise ValueError('comparison compatibility payload must declare planner and brain only')
    equivalence = pinned_json(spec['old_behavior_equivalence'])
    if equivalence.get('status') != 'passed_diagnostic_compatibility_not_gain':
        raise ValueError('old NN compatibility equivalence not established')
    source_hashes = equivalence.get('source_sha256', {})
    if source_hashes.get('tested_planner') != compatibility['tools/v15_v18_planner.py'] or source_hashes.get('tested_brain') != compatibility['tools/v18_brain.py']:
        raise ValueError('equivalence does not match exact compatibility code')
    results = equivalence.get('results', {})
    for category in ['CRYPTO_LONG', 'CRYPTO_SHORT', 'STOCKS_LONG', 'STOCKS_SHORT']:
        result = results.get(category, {})
        if not all(result.get(key) is True for key in ['prior_proposal_ranks_exact_equal', 'prior_profile_digest_must_exact_equal', 'prior_graph_decisions_M_B_exact_equal', 'promoted_binding_maps_loaded_both_versions']) or min(result.get('ranked_pairs', 0), result.get('graph_fixed_actual_cells', 0), result.get('must_items_equal_both_arms', 0)) <= 0:
            raise ValueError('equivalence lacks actual proposal/filter/MUST/graph cases for all categories')
    declarations = {}
    for arm in ['reference', 'candidate']:
        declaration = pinned_json(spec[arm])
        declarations[arm] = declaration
        root = Path(spec[arm]['path']).parent.parent
        bundle_files(root, declaration)
        if arm == 'candidate':
            check_files(root, compatibility)
    reference_root = Path(spec['reference']['path']).parent.parent
    candidate_root = Path(spec['candidate']['path']).parent.parent
    reference_names = declarations['reference']['expected_sha256_by_filename']
    candidate_names = declarations['candidate']['expected_sha256_by_filename']
    if set(reference_names) != set(candidate_names) or len(reference_names) != 32:
        raise ValueError('both NN arms must declare the same complete32 input paths')
    for name in reference_names:
        if name.startswith(('needed_filters_', 'switch_profile_', 'positives_all_', 'binding_map_', 'must_pos_')) and reference_names[name] != candidate_names[name]:
            raise ValueError(f'NN comparison ancillary differs: {name}')
        if name.startswith('digest_'):
            left = json.loads((reference_root / 'data/learn' / name).read_text())
            right = json.loads((candidate_root / 'data/learn' / name).read_text())
            retained = lambda value: {key: item for key, item in value.items() if key not in ('scorer', 'proposals')}
            if retained(left) != retained(right):
                raise ValueError(f'NN comparison digest ancillary fields differ: {name}')
    policy = pinned_json(spec['reference_policy'])
    if policy.get('schema') != 'v15_nn_reference_policy/1' or policy.get('pilot_sha256') != runtime['files'].get('v15_pilot.py'):
        raise ValueError('reference policy must execute exact frozen pilot flag loader')
    effective = policy.get('effective_policy', {})
    if effective.get('V18_LINKNET_FIRST') != '0' or effective.get('V18_LINKNET_BUNDLE_REQUIRED') != '0':
        raise ValueError('this mode requires proven prior inactive consumer policy')
    reference_flags = {'V18_LINKNET_FIRST': '0', 'V18_LINKNET_BUNDLE_REQUIRED': '0', 'V15_REQUIRE_MODEL_BUNDLE': '1', 'V15_NN_REFERENCE_LEGACY_POLICY': '1'}
    candidate_flags = {'V18_LINKNET_FIRST': '1', 'V18_LINKNET_BUNDLE_REQUIRED': '1', 'V15_REQUIRE_MODEL_BUNDLE': '1'}
    if spec.get('reference_runtime_flags') != reference_flags or spec.get('candidate_runtime_flags') != candidate_flags:
        raise ValueError('explicit prior-off versus candidate-active policy required; fixed budget and skip policy unchanged')
    if not spec.get('deployment_sandboxes'):
        raise ValueError('explicit sandbox deployment scope required')
    for target in spec['deployment_sandboxes']:
        if target['host'] not in ['local', '10.0.0.3'] or target['root'] != '/home/niels/binance-sandbox':
            raise ValueError('NN comparison may deploy only explicitly declared S1/S2 backtest sandboxes')
    return runtime



def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.nn_payload_', dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def materialize(spec, directory):
    runtime = validate(spec)
    directory = Path(directory)
    candidate_root = Path(spec['candidate']['path']).parent.parent
    outputs = {}
    for arm, name in [('reference', 'reference_tree'), ('candidate', 'tree')]:
        root = directory / name
        if root.exists():
            raise ValueError('comparison tree already exists')
        shutil.copytree(runtime['code_root'], root, copy_function=os.link)
        check_files(root, runtime['files'])
        declaration = pinned_json(spec[arm])
        bundle_root = Path(spec[arm]['path']).parent.parent
        files = bundle_files(bundle_root, declaration)
        for relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy(bundle_root / relative, target)
        for relative in spec['compatibility']:
            atomic_copy(candidate_root / relative, root / relative)
        check_files(root, {**files, **spec['compatibility']})
        outputs[arm] = str(root)
    check_files(runtime['code_root'], runtime['files'])
    return outputs


def guard_sandboxes(spec, deployed=False):
    runtime = validate(spec)
    expected = dict(runtime['files'])
    if deployed:
        expected.update(spec['compatibility'])
    script = 'import json,pathlib,hashlib,sys\nr=pathlib.Path(sys.argv[1]); e=json.load(sys.stdin); bad=[]\nfor n,h in e.items():\n p=r/n\n if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:bad.append(n)\nprint(json.dumps(bad));sys.exit(bool(bad))\n'
    receipts = []
    for target in spec['deployment_sandboxes']:
        command = ['python3', '-c', script, target['root']]
        if target['host'] != 'local':
            import shlex
            command = ['ssh', '-o', 'BatchMode=yes', target['host'], shlex.join(command)]
        result = subprocess.run(command, input=json.dumps(expected), capture_output=True, text=True)
        receipts.append({**target, 'ok': result.returncode == 0, 'mismatches': result.stdout, 'stderr': result.stderr})
    if not all(row['ok'] for row in receipts):
        raise ValueError(f'NN sandbox runtime identity guard failed: {receipts}')
    return receipts


def require_complete_results(reference, candidate, sides):
    if len(sides) != 8 or len(set(sides)) != 8:
        raise ValueError('declared NN comparison requires exact eight-side snapshot coverage')
    for arm in (reference, candidate):
        if set(arm) != set(sides) or any(row.get('err') or not isinstance(row.get('final_gain'), (int, float)) for row in arm.values()):
            raise ValueError('NN comparison has missing or failed real boards; no complete comparison')
