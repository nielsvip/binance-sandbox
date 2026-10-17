"""Which python runs project code on THIS host — the Python twin of tools/pyinterp.sh.

USER 2026-10-17: "everything should be on conda so the binance venv can be discarded it is flawed".
The `.venv` rung is deliberately absent; see pyinterp.sh for the measured reason (Mac .venv 15 packages
vs conda 240; S1 ~/binance/.venv 9 vs conda 253) and for why S1's ~/binance-sandbox/.venv is NOT covered
here — its numpy 2.5.3 / pandas 3.0.5 would move board numbers and must be benched, never hand-swapped.
"""

import os
import shutil
import sys
from pathlib import Path

CANDIDATES = (
    "/opt/anaconda3/envs/binance_env/bin/python",
    "~/.conda/envs/binance_env_mac/bin/python",
    "~/.conda/envs/binance_env/bin/python",
    "/home/niels/.conda/envs/binance_env_mac/bin/python",
    "/home/niels/.conda/envs/binance_env/bin/python",
    "/usr/bin/python3",
)


def interpreter() -> str:
    """Absolute path to the conda interpreter for this host; PATH python3 as the last resort."""
    override = os.environ.get("BINANCE_PY")
    if override and os.access(override, os.X_OK):
        return override
    for candidate in CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which("python3") or sys.executable


if __name__ == "__main__":
    print(interpreter())
