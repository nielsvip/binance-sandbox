"""Process-local I/O guard installed before importing a paper trading engine."""
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

_ROOT = None
_READONLY_SQLITE = frozenset()

def install(write_root, readonly_sqlite_paths=()):
    global _ROOT, _READONLY_SQLITE
    root = Path(write_root).resolve(strict=True)
    readonly_sqlite_paths = frozenset(Path(path).resolve(strict=True) for path in readonly_sqlite_paths)
    if not root.is_dir():
        raise ValueError("paper write root must be an existing private directory")
    if _ROOT is not None:
        if root != _ROOT or readonly_sqlite_paths != _READONLY_SQLITE:
            raise RuntimeError("paper I/O guard cannot change root or read-only SQLite allowlist")
        return require_installed()
    def allowed(path):
        if isinstance(path, int):
            raise PermissionError("paper worker cannot open inherited file descriptors for writing")
        resolved = Path(os.fsdecode(path)).resolve()
        if resolved != root and root not in resolved.parents:
            raise PermissionError(f"paper write outside isolated root: {resolved}")
    def guard(event, args):
        if event in {"socket.getaddrinfo", "socket.connect", "socket.connect_ex", "socket.bind", "socket.sendto", "socket.sendmsg", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"}:
            raise PermissionError(f"paper worker external I/O refused: {event}")
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and any(flag in mode for flag in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                allowed(path)
        elif event == "sqlite3.connect":
            database = os.fsdecode(args[0])
            if database != ":memory:":
                if database.startswith("file:"):
                    parsed = urlsplit(database)
                    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
                    requested = Path(unquote(parsed.path)).resolve()
                    if parsed.scheme != "file" or parsed.netloc or parsed.fragment or query != [("mode", "ro")] or requested not in _READONLY_SQLITE:
                        raise PermissionError("paper worker SQLite URI is not an exact registered read-only database")
                elif Path(database).resolve() not in _READONLY_SQLITE:
                    allowed(database)
                else:
                    raise PermissionError("registered source SQLite must use an explicit mode=ro URI")
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.utime", "os.truncate"}:
            allowed(args[0])
            if event in {"os.remove", "os.rmdir", "os.mkdir"} and len(args) > 1 and args[-1] not in (-1, None):
                raise PermissionError("paper worker dir_fd mutations refused")
        elif event in {"os.rename", "os.link"}:
            allowed(args[0])
            allowed(args[1])
            if any(fd not in (-1, None) for fd in args[2:]):
                raise PermissionError("paper worker dir_fd mutations refused")
        elif event == "os.symlink":
            raise PermissionError("paper worker symlink creation refused")
    sys.addaudithook(guard)
    _ROOT = root
    _READONLY_SQLITE = readonly_sqlite_paths
    return require_installed()

def require_installed(write_root=None):
    if _ROOT is None:
        raise RuntimeError("paper worker I/O guard must be installed before engine imports")
    if write_root is not None and Path(write_root).resolve() != _ROOT:
        raise RuntimeError("paper worker output root mismatch")
    return {"network_denied": True, "write_root": str(_ROOT), "readonly_sqlite_paths": sorted(map(str, _READONLY_SQLITE)), "pid": os.getpid()}
