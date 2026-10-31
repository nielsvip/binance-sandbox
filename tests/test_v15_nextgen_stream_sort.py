import gzip
import json
from pathlib import Path
import subprocess

import pytest

from tools import v15_nextgen_stream_sort as streaming


def test_byte_identical_stable_sort_with_missing_newlines_and_duplicate_paths(tmp_path):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir()
    second.mkdir()
    (first / 'spill_1.tsv').write_bytes(b'2026-02-01\tz\n2026-01-01\tfirst')
    with gzip.open(second / 'spill_1.tsv.gz', 'wb') as target:
        target.write(b'2026-01-01\tsecond\n2026-01-01\tthird\n')
    (second / 'spill_2.tsv').write_bytes(b'')
    canonical_plain = tmp_path / 'canonical_input.tsv'
    with gzip.open(second / 'spill_1.tsv.gz', 'rb') as source:
        canonical_plain.write_bytes(source.read())
    canonical = tmp_path / 'canonical.tsv'
    subprocess.run(['sort', '-s', '-t', '\t', '-k1,1', '-o', str(canonical), str(first / 'spill_1.tsv'), str(canonical_plain), str(second / 'spill_2.tsv')], check=True, env=dict(streaming.os.environ, LC_ALL='C'))
    output = tmp_path / 'stream.tsv'
    files = streaming.sorted_events([str(first), str(second), str(first)], tmp_path / 'scratch', output)
    assert output.read_bytes() == canonical.read_bytes()
    assert output.read_bytes().startswith(b'2026-01-01\tfirst\n2026-01-01\tsecond\n2026-01-01\tthird\n')
    assert len(files) == 3
    receipt = json.loads(output.with_name(output.name + '.stream_sort_receipt.json').read_text())
    assert receipt['missing_final_newlines_added'] == 1
    assert receipt['decompressed_source_files_created'] == 0
    assert not list((tmp_path / 'scratch').iterdir())


def test_corrupt_source_never_produces_verified_receipt(tmp_path):
    source = tmp_path / 'spill_bad.tsv.gz'
    source.write_bytes(b'not gzip')
    output = tmp_path / 'output.tsv'
    with pytest.raises(gzip.BadGzipFile):
        streaming.sorted_events([str(tmp_path)], tmp_path / 'scratch', output)
    assert not output.with_name(output.name + '.stream_sort_receipt.json').exists()


def test_existing_output_is_preserved(tmp_path):
    (tmp_path / 'spill_input.tsv').write_bytes(b'2026-01-01\trow\n')
    output = tmp_path / 'output.tsv'
    output.write_bytes(b'prior evidence')
    with pytest.raises(FileExistsError):
        streaming.sorted_events([str(tmp_path)], tmp_path / 'scratch', output)
    assert output.read_bytes() == b'prior evidence'
