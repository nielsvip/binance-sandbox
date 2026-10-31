import subprocess
import sys
from pathlib import Path

def test_isolation_in_disposable_process(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = '''
import os, socket, subprocess, sys
from pathlib import Path
from tools.paper_worker_isolation import install
root=Path(sys.argv[1]); install(root)
(root/'allowed').write_text('paper')
attempts=[lambda: (root.parent/'forbidden').write_text('live'), lambda: socket.create_connection(('127.0.0.1',9)), lambda: subprocess.run(['true']), lambda: os.rename(root/'allowed', root.parent/'escaped'), lambda: os.symlink(root.parent, root/'escape')]
for attempt in attempts:
    try: attempt()
    except PermissionError: continue
    raise RuntimeError('paper guard did not refuse I/O')
assert (root/'allowed').read_text()=='paper'
'''
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=root, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert not (tmp_path.parent / "forbidden").exists()
