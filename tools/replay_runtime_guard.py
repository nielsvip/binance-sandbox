import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path

def _config_hash(value):
    return hashlib.sha256(json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

def assert_runtime_paths(code_root, run_root, paths):
    code = Path(code_root).resolve()
    run = Path(run_root).resolve()
    if code == run or not code.is_relative_to(run):
        raise RuntimeError("Replay code must be in a distinct run-owned subdirectory")
    for key in ("BASE_PATH", "V12_BASE_PATH"):
        if not os.environ.get(key) or Path(os.environ[key]).resolve() != code:
            raise RuntimeError(f"Replay {key} does not resolve to isolated code root")
    for name, value in paths.items():
        resolved = Path(value).resolve()
        if not resolved.is_relative_to(run):
            raise RuntimeError(f"Replay path escapes run directory: {name}={resolved}")
    return {name: str(Path(value).resolve()) for name, value in paths.items()}

def assert_frozen_active_row(db_path, active_file):
    active = json.loads(Path(active_file).read_text())
    side = str(active["sym_side"])
    expected = _config_hash(active["overrides"])
    if expected != active.get("overrides_sha256"):
        raise RuntimeError("Frozen override hash differs from its manifest")
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute("SELECT full_config_json FROM per_sym_active WHERE sym_side=?", (side,)).fetchone()
    if row is None or _config_hash(json.loads(row[0])) != expected:
        raise RuntimeError(f"Frozen active DB row missing or different for {side}")
    return {"sym_side": side, "full_config_sha256": expected}

def assert_state_module_paths(code_root, run_root, modules):
    paths = {}
    logger_module = modules.get("per_trade_logger")
    if logger_module is None:
        raise RuntimeError("Replay must import per_trade_logger before state-path preflight")
    for name in ("_BASE", "_OUT_DIR", "_ATTEMPT_DIR", "_IDEM_DIR"):
        value = getattr(logger_module, name, None)
        if value is None:
            raise RuntimeError(f"Replay per_trade_logger has no {name}")
        paths[f"per_trade_logger.{name}"] = value
    for name in ("config", "config_tradier"):
        module = modules.get(name)
        if module is None:
            continue
        for owner in (module, getattr(module, "Config", None)):
            if owner is None:
                continue
            for attribute in ("BASE_PATH", "DATA_DIR", "MARKET_MODE_FILE"):
                value = getattr(owner, attribute, None)
                if value is not None:
                    paths[f"{name}.{getattr(owner, '__name__', 'Config')}.{attribute}"] = value
    stale = modules.get("stale_set_halt")
    if stale is not None:
        paths["stale_set_halt.ROOT"] = stale.ROOT
        paths["stale_set_halt.base_path"] = stale.base_path(modules["ez_manage"]._ezm_base_config)
    if os.environ.get("PER_SYM_STORE_DB"):
        paths["PER_SYM_STORE_DB"] = os.environ["PER_SYM_STORE_DB"]
    return assert_runtime_paths(code_root, run_root, paths)

def assert_native_runtime(code_root, run_root, active_file, modules):
    from tools.replay_network_guard import require_installed
    network = require_installed()
    assert_runtime_paths(code_root, run_root, {"network_audit": network["audit_path"]})
    code = Path(code_root).resolve()
    paths = {"code_root": code, "active_file": active_file}
    for name in ("config", "ez_manage", "ez_positions_quick", "backtest_v12_engine", "per_sym_store", "live_twins.vec_exact"):
        module = modules.get(name)
        if module is None or not getattr(module, "__file__", None):
            raise RuntimeError(f"Required replay module is not loaded: {name}")
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(code):
            raise RuntimeError(f"Replay imported external project source: {name}={path}")
        paths[f"module:{name}"] = path
    config = modules["config"]
    paths["Config.BASE_PATH"] = config.Config.BASE_PATH
    paths["ez_manage.base_config.BASE_PATH"] = modules["ez_manage"]._ezm_base_config.BASE_PATH
    paths["vec_exact.BASE_PATH"] = modules["live_twins.vec_exact"]._base_path()
    paths["per_sym_store.DB_PATH"] = modules["per_sym_store"].DB_PATH
    paths["published_selection"] = code / "data" / "published_selection.json"
    for name in ("Config.BASE_PATH", "ez_manage.base_config.BASE_PATH", "vec_exact.BASE_PATH"):
        if Path(paths[name]).resolve() != code:
            raise RuntimeError(f"Native replay resolved wrong base: {name}={paths[name]}")
    if not Path(paths["published_selection"]).is_file():
        raise RuntimeError("Native replay is missing its frozen published selection")
    for name, logger in [("root", logging.getLogger()), *logging.Logger.manager.loggerDict.items()]:
        for index, handler in enumerate(getattr(logger, "handlers", [])):
            if getattr(handler, "baseFilename", None):
                paths[f"logger:{name}:{index}"] = handler.baseFilename
    resolved = assert_runtime_paths(code, run_root, paths)
    resolved.update(assert_state_module_paths(code, run_root, modules))
    active = assert_frozen_active_row(paths["per_sym_store.DB_PATH"], active_file)
    return {"status": "PREFLIGHT_PATHS_AND_ACTIVE_ROW_VERIFIED", "paths": resolved, "active": active, "network_guard": network, "note": "Does not prove trading parity or future file writes"}
