"""Explicit, fail-closed NN comparison fixtures and sandbox runtime identity guards."""
import hashlib
import json
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
    from tools.v15_nextgen_gate_artifacts import declared_relative
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
    if equivalence.get('schema') != 'v15_nn_old_behavior_equivalence/1' or equivalence.get('mismatches') != 0:
        raise ValueError('old NN compatibility equivalence not established')
    if equivalence.get('tested_compatibility_sha256') != compatibility or not all(equivalence.get('cases', {}).get(key, 0) > 0 for key in ['proposal_decisions', 'filter_decisions', 'graph_decisions']):
        raise ValueError('equivalence must prove actual proposal/filter/graph decisions for exact compatibility code')
    for arm in ['reference', 'candidate']:
        declaration = pinned_json(spec[arm])
        root = Path(spec[arm]['path']).parent.parent
        bundle_files(root, declaration)
        if arm == 'candidate':
            check_files(root, compatibility)
    flags = spec.get('equal_runtime_flags', {})
    if flags != {'V18_LINKNET_FIRST': '1', 'V18_LINKNET_BUNDLE_REQUIRED': '1', 'V15_REQUIRE_MODEL_BUNDLE': '1'}:
        raise ValueError('NN comparison requires equal activation and provenance guards')
    if not spec.get('deployment_sandboxes'):
        raise ValueError('explicit sandbox deployment scope required')
    for target in spec['deployment_sandboxes']:
        if target['host'] not in ['local', '10.0.0.3'] or target['root'] != '/home/niels/binance-sandbox':
            raise ValueError('NN comparison may deploy only explicitly declared S1/S2 backtest sandboxes')
    return runtime


def materialize(spec, directory):
    runtime = validate(spec)
    directory = Path(directory)
    candidate_root = Path(spec['candidate']['path']).parent.parent
    outputs = {}
    for arm, name in [('reference', 'reference_tree'), ('candidate', 'tree')]:
        root = directory / name
        if root.exists():
            raise ValueError('comparison tree already exists')
        shutil.copytree(runtime['code_root'], root)
        check_files(root, runtime['files'])
        declaration = pinned_json(spec[arm])
        bundle_root = Path(spec[arm]['path']).parent.parent
        files = bundle_files(bundle_root, declaration)
        for relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle_root / relative, target)
        for relative in spec['compatibility']:
            shutil.copyfile(candidate_root / relative, root / relative)
        check_files(root, {**files, **spec['compatibility']})
        outputs[arm] = str(root)
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
