"""env_gpg — load .env.gpg (GPG-encrypted KEY=VALUE secrets) into os.environ.

Encrypt side (run manually when rotating keys; also pinned at ez_manage.py top):
    gpg --encrypt --recipient Niels --output .env.gpg .env
Decrypt side: load_env_gpg() is called from ez_manage.py first lines so
cron-launched daemons (no interactive shell env) still get
{ang,fin,flz,inf,men}_API_KEY / _API_SECRET.
Fail-open: any error -> one stderr line, process continues on existing env.
NEVER prints or logs values.
"""
from __future__ import annotations
import os
import subprocess
import sys

_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")


def _has_any_key() -> bool:
    for a in _ACCOUNTS:
        if os.environ.get(f"{a}_API_KEY") or os.environ.get(f"{a.upper()}_API_KEY"):
            return True
    return False


def load_env_gpg() -> int:
    """Decrypt sibling .env.gpg into os.environ (setdefault only). Returns keys loaded."""
    if os.environ.get("EZ_SKIP_ENV_GPG") == "1":
        return 0
    if _has_any_key():
        return 0
    here = os.path.dirname(os.path.abspath(__file__))
    gpg_file = os.path.join(here, ".env.gpg")
    if not os.path.exists(gpg_file):
        return 0
    try:
        out = subprocess.run(
            ["gpg", "--batch", "--yes", "--decrypt", gpg_file],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20, check=False,
        )
    except Exception as e:
        print(f"[env_gpg] gpg fork failed: {type(e).__name__}", file=sys.stderr)
        return 0
    if out.returncode != 0 or not out.stdout:
        print("[env_gpg] decrypt failed (agent locked or missing key?)", file=sys.stderr)
        return 0
    n = 0
    for raw in out.stdout.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        if not k or not v or k in os.environ:
            continue
        os.environ[k] = v
        n += 1
    if n > 0:
        print(f"[env_gpg] loaded {n} keys from .env.gpg", file=sys.stderr)
    return n
