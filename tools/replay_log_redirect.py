from pathlib import Path
import logging
import logging.handlers
import os

_REDIRECT_STATE = None


def _redirect_path(filename, log_dir):
    path = Path(filename).expanduser()
    try:
        resolved = path.resolve(strict=False)
        root = log_dir.resolve(strict=False)
        if resolved.is_relative_to(root):
            return str(resolved)
    except (OSError, RuntimeError):
        pass
    return str(log_dir / path.name)


def install_replay_log_redirect(log_dir):
    """Route standard file log handlers into this replay's output directory."""
    global _REDIRECT_STATE
    root = Path(log_dir).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    if _REDIRECT_STATE is not None:
        if _REDIRECT_STATE["log_dir"] != root:
            raise RuntimeError(f"replay log redirect already installed for {_REDIRECT_STATE['log_dir']}")
        return {"log_dir": str(root), "preserves_formatters": True}
    original_file_handler = logging.FileHandler
    original_rotating_handler = logging.handlers.RotatingFileHandler
    original_log_env = {key: os.environ.get(key) for key in ("EZ_LOG_DIR", "TRADIER_API_LOG_DIR")}
    os.environ["EZ_LOG_DIR"] = str(root)
    os.environ["TRADIER_API_LOG_DIR"] = str(root)

    class ReplayFileHandler(original_file_handler):
        def __init__(self, filename, *args, **kwargs):
            original_file_handler.__init__(self, _redirect_path(filename, root), *args, **kwargs)

    class ReplayRotatingFileHandler(original_rotating_handler):
        def __init__(self, filename, *args, **kwargs):
            super().__init__(_redirect_path(filename, root), *args, **kwargs)

    ReplayFileHandler.__name__ = "ReplayFileHandler"
    ReplayRotatingFileHandler.__name__ = "ReplayRotatingFileHandler"
    logging.FileHandler = ReplayFileHandler
    logging.handlers.RotatingFileHandler = ReplayRotatingFileHandler
    _REDIRECT_STATE = {
        "log_dir": root,
        "original_file_handler": original_file_handler,
        "original_rotating_handler": original_rotating_handler,
        "original_log_env": original_log_env,
    }
    return {"log_dir": str(root), "preserves_formatters": True}


def uninstall_replay_log_redirect():
    """Restore handler classes; intended for isolated unit tests only."""
    global _REDIRECT_STATE
    if _REDIRECT_STATE is None:
        return
    logging.FileHandler = _REDIRECT_STATE["original_file_handler"]
    logging.handlers.RotatingFileHandler = _REDIRECT_STATE["original_rotating_handler"]
    for key, value in _REDIRECT_STATE["original_log_env"].items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _REDIRECT_STATE = None
