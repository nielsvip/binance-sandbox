"""Lossless research-training sort without materializing decompressed input files."""
import argparse
import glob
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def sorted_events(spill_dirs, tmp_dir, out_path):
    files = []
    for directory in spill_dirs:
        expanded = os.path.expanduser(directory)
        files.extend(sorted(glob.glob(os.path.join(expanded, 'spill_*.tsv')) + glob.glob(os.path.join(expanded, 'spill_*.tsv.gz'))))
    seen = set()
    files = [path for path in files if not (os.path.realpath(path) in seen or seen.add(os.path.realpath(path)))]
    if not files:
        raise ValueError('no source spill files')
    output, temporary = Path(out_path), Path(tmp_dir)
    receipt = output.with_name(output.name + '.stream_sort_receipt.json')
    if output.exists() or receipt.exists():
        raise FileExistsError('fresh output required; preserve previous sort evidence')
    temporary.mkdir(parents=True, exist_ok=True)
    before = {path: sha(path) for path in files}
    command = ['sort', '-s', '-t', '\t', '-k1,1', '-S', os.environ.get('V18_SORT_MEM', '1G'), '-T', str(temporary), '-o', str(output)]
    if os.environ.get('V18_SORT_COMPRESS'):
        command.append('--compress-program=' + os.environ['V18_SORT_COMPRESS'])
    process = subprocess.Popen(command, stdin=subprocess.PIPE, env=dict(os.environ, LC_ALL='C'))
    stream_sha, stream_bytes, added_newlines = hashlib.sha256(), 0, 0
    try:
        for path in files:
            opener = gzip.open if path.endswith('.gz') else open
            last = b''
            with opener(path, 'rb') as source:
                for block in iter(lambda: source.read(4194304), b''):
                    process.stdin.write(block)
                    stream_sha.update(block)
                    stream_bytes += len(block)
                    last = block[-1:]
            if last and last != b'\n':
                process.stdin.write(b'\n')
                stream_sha.update(b'\n')
                stream_bytes += 1
                added_newlines += 1
        process.stdin.close()
        if process.wait() != 0:
            raise subprocess.CalledProcessError(process.returncode, command)
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    if {path: sha(path) for path in files} != before:
        raise ValueError('source bytes changed during sorting; output not approved')
    value = {'schema': 'lossless_stream_sort/1', 'source_order': files, 'source_sha256': before, 'normalized_input_sha256': stream_sha.hexdigest(), 'normalized_input_bytes': stream_bytes, 'missing_final_newlines_added': added_newlines, 'output_sha256': sha(output), 'output_bytes': output.stat().st_size, 'decompressed_source_files_created': 0, 'scientific_equivalence': 'requires comparison with canonical sorter before adoption'}
    with receipt.open('x') as target:
        json.dump(value, target, indent=2)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', required=True)
    parser.add_argument('--brain-sha256', required=True)
    parser.add_argument('brain_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(args.code_root).resolve()
    source = root / 'tools/v18_brain.py'
    if sha(source) != args.brain_sha256:
        raise ValueError('frozen training source hash mismatch')
    sys.path.insert(0, str(root))
    brain = importlib.import_module('tools.v18_brain')
    if Path(brain.__file__).resolve() != source:
        raise ValueError('training module escaped frozen source')
    brain.sorted_events = sorted_events
    forwarded = args.brain_args[1:] if args.brain_args[:1] == ['--'] else args.brain_args
    sys.argv = [str(source)] + forwarded
    brain.main()


if __name__ == '__main__':
    main()
