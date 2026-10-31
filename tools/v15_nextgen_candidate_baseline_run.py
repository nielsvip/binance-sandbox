"""Run only the actual candidate pilot's initial pinned-baseline verification."""
import argparse
import ast
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import resource
import signal
import threading
from v15_nextgen_bench_isolation import install_audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--side', required=True)
    parser.add_argument('--sym-side', required=True)
    args = parser.parse_args()
    root, side = Path(args.root), Path(args.side)
    if os.environ.get('V15_BASELINE_PRECHECK') != '1' or os.environ.get('V15_BASELINE_VERIFY_ONLY') != '1':
        raise ValueError('explicit baseline-only precheck required; optimizer is forbidden')
    os.environ['V15_PAIR_MODE'] = '0'
    os.environ['V18_PAIR_CONCURRENT'] = '0'
    if os.getpgrp() != os.getpid():
        os.setsid()
    owned_pgid = os.getpgrp()
    if owned_pgid != os.getpid():
        raise ValueError('owned process group required')
    resource.setrlimit(resource.RLIMIT_AS, (6 * (1 << 30), 6 * (1 << 30)))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * (1 << 20), 64 * (1 << 20)))
    memory = next(int(line.split()[1]) / 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    if memory < 7000 or shutil.disk_usage(root).free < 10000 * (1 << 20):
        raise ValueError('original gate memory/disk floor not met; no candidate baseline evaluation')
    os.chdir(root)
    protected = {str(path.resolve()) for path in root.rglob('*') if path.is_file()}
    stopped = threading.Event()
    def output_monitor():
        while not stopped.wait(0.2):
            total = 0
            for directory in (root, side):
                for path in directory.rglob('*'):
                    try:
                        if path.is_file() and str(path.resolve()) not in protected:
                            total += path.stat().st_size
                    except FileNotFoundError:
                        pass
            if total > 64 * (1 << 20):
                (side / 'output_cap_exceeded.json').write_text(json.dumps({'new_output_bytes': total, 'owned_pgid': owned_pgid}))
                if os.getpgrp() == owned_pgid == os.getpid():
                    os.killpg(owned_pgid, signal.SIGTERM)
                return
    threading.Thread(target=output_monitor, daemon=True).start()
    def immutable_inputs(event, values):
        if event == 'open' and isinstance(values[0], (str, bytes, os.PathLike)):
            writing = any(char in (values[1] or '') for char in 'wax+') or bool(values[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and str(Path(os.fsdecode(values[0])).resolve()) in protected:
                raise PermissionError('candidate baseline may not mutate shared immutable inputs')
        elif event in ['os.remove', 'os.unlink', 'os.rename', 'os.replace']:
            candidates = values[:2] if event in ['os.rename', 'os.replace'] else values[:1]
            if any(isinstance(value, (str, bytes, os.PathLike)) and str(Path(os.fsdecode(value)).resolve()) in protected for value in candidates):
                raise PermissionError('candidate baseline may not replace shared immutable inputs')
    sys.addaudithook(immutable_inputs)
    install_audit(root, side)
    sys.path.insert(0, str(root))
    os.environ['V15_BASELINE_RAW_CAPTURE'] = str(side / 'candidate_raw_capture.pickle')
    tree = ast.parse((root / 'v15_pilot.py').read_bytes())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'map_key_for_symside')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(root / 'v15_pilot.py'), 'exec'), namespace)
    template = 'TEMPLATE_' + namespace['map_key_for_symside'](args.sym_side) + '.xlsx'
    sys.argv = [str(root / 'v15_pilot.py'), '--sym-side', args.sym_side, '--template', str(root / 'SPREADSHEETS/TEMPLATE_FINAL_NORM' / template), '--seq-mode', 'worst2best', '--window-days', '30', '--vector-only', '--workers', '2']
    try:
        runpy.run_path(str(root / 'v15_pilot.py'), run_name='__main__')
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    finally:
        stopped.set()
    if not (side / 'candidate_baseline_parity.json').exists():
        raise ValueError('candidate returned without real baseline-parity receipt')


if __name__ == '__main__':
    main()
