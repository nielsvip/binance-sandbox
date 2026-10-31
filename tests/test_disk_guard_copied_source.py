import os
from pathlib import Path
import shlex
import subprocess
import time


def test_new_copy_with_old_source_mtime_is_not_a_cleanup_candidate(tmp_path):
    source = tmp_path / 'cat_side_defaults.py'
    source.write_text('VALUE = 1\n')
    old = time.time() - 90 * 86400
    os.utime(source, (old, old))
    assert source.stat().st_mtime < time.time() - 3 * 86400
    assert source.stat().st_ctime > time.time() - 60
    script = Path('tools/s2_disk_guard.sh').read_text()
    selector = script.split('find /tmp -xdev -type f ', 1)[1].split(' -user niels', 1)[0]
    current = subprocess.run(['find', str(tmp_path), '-type', 'f', *shlex.split(selector), '-print'], check=True, capture_output=True, text=True)
    old_rule = subprocess.run(['find', str(tmp_path), '-type', 'f', '-mtime', '+3', '-print'], check=True, capture_output=True, text=True)
    assert str(source) in old_rule.stdout
    assert current.stdout == ''
    assert source.read_text() == 'VALUE = 1\n'
