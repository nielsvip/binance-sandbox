import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.v15_npz_bench import PYTHON_DETERMINISM_ENV, canonical_pilot_env


def test_canonical_pilot_env_adds_required_settings():
    env = canonical_pilot_env({"PATH": "/usr/bin"})
    assert env == {"PATH": "/usr/bin", **PYTHON_DETERMINISM_ENV}


@pytest.mark.parametrize(
    "key,value", [("PYTHONHASHSEED", "random"), ("V8_HASHSEED_LOCKED", "0")]
)
def test_canonical_pilot_env_rejects_conflicting_settings(key, value):
    with pytest.raises(ValueError, match="conflicting deterministic pilot environment"):
        canonical_pilot_env({key: value})


def test_canonical_env_prevents_import_guard_reexec_in_tiny_child():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        marker = root / "reexec_marker"
        child = root / "fake_engine_guard.py"
        child.write_text(
            "import os, pathlib, sys\n"
            "marker = pathlib.Path(sys.argv[1])\n"
            "if os.environ.get('PYTHONHASHSEED') != '0' and os.environ.get('V8_HASHSEED_LOCKED') != '1':\n"
            "    marker.write_text('exec called')\n"
            "    os.environ['PYTHONHASHSEED'] = '0'\n"
            "    os.environ['V8_HASHSEED_LOCKED'] = '1'\n"
            "    os.execv(sys.executable, [sys.executable] + sys.argv)\n"
            "print('guard passed')\n"
        )
        env = canonical_pilot_env({"PATH": os.environ.get("PATH", "")})
        result = subprocess.run(
            [sys.executable, str(child), str(marker)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "guard passed"
        assert not marker.exists()
