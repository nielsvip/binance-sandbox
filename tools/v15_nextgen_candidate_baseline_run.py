"""Run only the actual candidate pilot's initial pinned-baseline verification."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
from v15_nextgen_bench_isolation import install_audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--side', required=True)
    parser.add_argument('--sym-side', required=True)
    args = parser.parse_args()
    root, side = Path(args.root), Path(args.side)
    memory = next(int(line.split()[1]) / 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    if memory < 7000:
        raise ValueError('original gate memory floor not met; no candidate baseline evaluation')
    os.chdir(root)
    protected = {str(path.resolve()) for path in root.rglob('*') if path.is_file()}
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
    sys.argv = [str(root / 'v15_pilot.py'), '--sym-side', args.sym_side, '--template', str(root / 'SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx'), '--seq-mode', 'worst2best', '--window-days', '30', '--vector-only', '--workers', '2']
    runpy.run_path(str(root / 'v15_pilot.py'), run_name='__main__')
    if not (side / 'candidate_baseline_parity.json').exists():
        raise ValueError('candidate returned without real baseline-parity receipt')


if __name__ == '__main__':
    main()
