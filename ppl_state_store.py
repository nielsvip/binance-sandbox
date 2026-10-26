from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path


def account_file(base_path, account):
    name = re.sub(r"[^A-Za-z0-9_-]", "_", str(account or ""))
    if not name:
        raise ValueError("account is required")
    return Path(base_path) / "data" / "ppl_state" / f"{name}.json"


def load_account(base_path, account):
    path = account_file(base_path, account)
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_account(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        tmp_name = None
        try:
            current = {}
            try:
                loaded = json.loads(path.read_text()) if path.exists() else {}
                current = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError, TypeError):
                current = {}
            current.update(data)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
                tmp_name = tmp.name
                json.dump(current, tmp, sort_keys=True, separators=(",", ":"))
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, path)
            tmp_name = None
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def save_state(base_path, account, position_key, state):
    _write_account(account_file(base_path, account), {str(position_key): dict(state)})


def delete_state(base_path, account, position_key):
    path = account_file(base_path, account)
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        tmp_name = None
        try:
            try:
                data = json.loads(path.read_text()) if path.exists() else {}
                data = data if isinstance(data, dict) else {}
            except (OSError, ValueError, TypeError):
                data = {}
            data.pop(str(position_key), None)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
                tmp_name = tmp.name
                json.dump(data, tmp, sort_keys=True, separators=(",", ":"))
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, path)
            tmp_name = None
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
