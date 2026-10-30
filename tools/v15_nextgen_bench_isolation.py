"""Research benchmark path routing and audited Python I/O; not an OS sandbox."""
import argparse
import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import shlex
import tempfile
import subprocess
import sys


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def inside(path, roots):
    path = Path(path).resolve()
    return any(path == root or root in path.parents for root in roots)


def install_audit(root, side):
    if getattr(sys, "_v15_bench_audited", False):
        return
    root, side = Path(root).resolve(), Path(side).resolve()
    roots = (root, side)
    logs = side / "audit"
    logs.mkdir(parents=True, exist_ok=True)
    writes = open(logs / "denied_writes.jsonl", "a", buffering=1)
    reads = open(logs / "frozen_reads.jsonl", "a", buffering=1)
    def audit(event, args):
        paths = []
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            mode, flags = args[1], args[2]
            write = (isinstance(mode, str) and any(char in mode for char in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if write:
                paths = [path]
            elif path.suffix == ".npz" or path.name.startswith(("v18_brain_", "digest_", "v18_data_brain_", "v18_data_learn_digest_")):
                allowed = inside(path, (root,))
                reads.write(json.dumps({"at_utc": utc(), "pid": os.getpid(), "phase": os.environ.get("V15_BENCH_AUDIT_PHASE"), "path": str(path), "inside_frozen_root": allowed}) + "\n")
                if not allowed:
                    raise PermissionError(f"benchmark input outside frozen root: {path}")
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.utime"):
            paths = [args[0]]
        elif event in ("os.rename", "os.link", "os.symlink"):
            paths = list(args[:2])
        elif event == "sqlite3.connect" and args[0] != ":memory:":
            paths = [args[0]]
        for value in paths:
            if isinstance(value, int):
                continue
            path = Path(os.fsdecode(value)).resolve()
            if path == Path("/dev/null"):
                continue
            if not inside(path, roots):
                writes.write(json.dumps({"at_utc": utc(), "pid": os.getpid(), "phase": os.environ.get("V15_BENCH_AUDIT_PHASE"), "event": event, "path": str(path)}) + "\n")
                raise PermissionError(f"benchmark write outside private roots: {path}")
    sys._v15_bench_audited = True
    sys.addaudithook(audit)


def install_routes(root, side, assets=None):
    root, side = Path(root).resolve(), Path(side).resolve()
    assets = Path(assets or Path(__file__).with_name("v15_nextgen_routing_assets")).resolve()
    meta = json.loads((assets / "manifest.json").read_text())
    receipt = {}
    for name, before in meta["source_sha256"].items():
        origin, target = assets / name, root / name
        if origin.is_symlink() or target.is_symlink():
            raise ValueError("routing source and target must be materialized")
        payload = origin.read_bytes()
        after = hashlib.sha256(payload).hexdigest()
        if after != meta["candidate_sha256"][name]:
            raise ValueError(f"routing asset hash mismatch: {name}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual not in (before, after):
            raise ValueError(f"unapproved routing preimage: {name}")
        if actual == before:
            backup = root / "harness_backups" / ("before_routing_" + name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(target.read_bytes())
            descriptor, temporary = tempfile.mkstemp(prefix=".private_route_", dir=target.parent)
            os.close(descriptor)
            Path(temporary).write_bytes(payload)
            os.replace(temporary, target)
        receipt[name] = {"source_sha256": before, "routed_sha256": after}
    (side / "routing_preflight.json").write_text(json.dumps({"at_utc": utc(), "scope": meta["scope"], "files": receipt}, indent=2))
    return receipt


def prepare(root, side, env, symbol, window_days, assets=None):
    root, side = Path(root).resolve(), Path(side).resolve()
    if root == side or root.parent != side.parent:
        raise ValueError("benchmark root and side directory must be distinct siblings")
    npz = root / "backtest_v8/indicators" / f"{symbol}.npz"
    if not npz.is_file() or npz.is_symlink():
        raise ValueError(f"missing materialized frozen NPZ: {npz}")
    receipt = root / ".benchmark_interpreter.json"
    install_routes(root, side, assets)
    executable = Path(json.loads(receipt.read_text())["executable"]) if receipt.exists() else (root / ".venv/bin/python").resolve()
    if not executable.is_file():
        raise ValueError("benchmark interpreter missing")
    link = root / ".venv"
    if link.is_symlink():
        link.unlink()  # only the new builder-created interpreter link, never its target
    if not receipt.exists():
        receipt.write_text(json.dumps({"executable": str(executable), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}))
        bridge = root / ".venv/bin/python"
        bridge.parent.mkdir(parents=True, exist_ok=True)
        bridge.write_text("#!/bin/sh\nexec " + shlex.quote(str(executable)) + ' "$@"\n')
        bridge.chmod(0o755)
    links = [str(path) for path in root.rglob("*") if path.is_symlink()]
    if links:
        raise ValueError(f"benchmark root contains symlinks: {links[:5]}")
    result = dict(env)
    routes = {"BASE_PATH": root, "PER_SYM_STORE_DB": side / "private_store/per_sym_store.db", "V15_FLAGS_DIR": side / "flags", "V15_V17_CARRY_DIR": side / "carry", "V15_EVAL_CACHE_DB": side / "eval_cache.db", "V15_PREV_BEST_DIR": side / "prev_best", "V15_PRIORITY_DIR": root / "data/cat_avg", "V15_GATE_FLAGS": root / "data/v15_gate_flags.json", "V15_BRAIN_DIR": root / "data/brain", "V15_LEDGER_JSON": root / "data/v15_symside_ledger.json", "V15_ENDGAME_KNOWLEDGE": root / "data/endgame/endgame_knowledge_fleet.json", "SWITCH_PARITY_CACHE_DIR": side / "parity_cache", "TMPDIR": side / "tmp", "NUMBA_CACHE_DIR": side / "numba_cache", "MPLCONFIGDIR": side / "mpl", "XDG_CACHE_HOME": side / "xdg_cache"}
    for name, path in routes.items():
        path = path.resolve()
        if not inside(path, (root, side)):
            raise ValueError(f"private route escaped benchmark: {name}")
        result[name] = str(path)
        (path.parent if path.suffix else path).mkdir(parents=True, exist_ok=True)
    if int(window_days) != 7:
        result.pop("V12_NPZ_DIR", None)  # this is specifically a live 7D overlay contract
    result.update(SWITCH_PARITY_REGISTER="0", PYTHONDONTWRITEBYTECODE="1", V15_BENCH_ROOT=str(root), V15_BENCH_SIDE=str(side), V15_BENCH_AUDIT_PHASE="preflight")
    bootstrap = side / "audit_bootstrap"
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / "sitecustomize.py").write_text("import os\nfrom v15_nextgen_bench_isolation import install_audit\ninstall_audit(os.environ['V15_BENCH_ROOT'], os.environ['V15_BENCH_SIDE'])\n")
    result["PYTHONPATH"] = os.pathsep.join((str(bootstrap), str(Path(__file__).resolve().parent), str(root)))
    return str(executable), result


def probe(root, side):
    root, side = Path(root).resolve(), Path(side).resolve()
    install_audit(root, side)
    sys.path.insert(0, str(root))
    config = importlib.import_module("config")
    tradier = importlib.import_module("config_tradier")
    store = importlib.import_module("per_sym_store")
    paths = {"Config.BASE_PATH": config.Config.BASE_PATH, "Config.DATA_DIR": config.Config.DATA_DIR, "TradierConfig.BASE_PATH": tradier.TradierConfig.BASE_PATH, "per_sym_store.DB_PATH": store.DB_PATH}
    for name, path in paths.items():
        if not inside(path, (root, side)):
            raise ValueError(f"actual imported config path escaped benchmark: {name}={path}")
    result = {"at_utc": utc(), "status": "ACTUAL_CONFIG_PRIVATE_PATHS_PASS", "paths": {name: str(Path(path).resolve()) for name, path in paths.items()}, "config_sha256": hashlib.sha256((root / "config.py").read_bytes()).hexdigest(), "tradier_config_sha256": hashlib.sha256((root / "config_tradier.py").read_bytes()).hexdigest(), "audit_scope": "audited Python operations only; not all native syscalls"}
    (side / "config_path_preflight.json").write_text(json.dumps(result, indent=2))
    return result


def check_config(executable, root, side, env):
    result = subprocess.run([executable, str(Path(__file__).resolve()), "probe", str(root), str(side)], cwd=root, env=env, capture_output=True, text=True)
    if result.returncode:
        (Path(side) / "config_preflight_failure.json").write_text(json.dumps({"at_utc": utc(), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}, indent=2))
        raise ValueError(f"actual config isolation preflight failed; see {side}/config_preflight_failure.json")
    return json.loads((Path(side) / "config_path_preflight.json").read_text())


def command(executable, root, side, pilot_args):
    return [executable, "-u", str(Path(__file__).resolve()), "run", str(root), str(side), *pilot_args]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("probe", "run"))
    parser.add_argument("root")
    parser.add_argument("side")
    parser.add_argument("pilot_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root, side = Path(args.root).resolve(), Path(args.side).resolve()
    install_audit(root, side)
    if args.action == "probe":
        print(json.dumps(probe(root, side)))
        return
    os.environ["V15_BENCH_AUDIT_PHASE"] = "runtime"
    sys.path.insert(0, str(root))
    sys.argv = [str(root / "v15_pilot.py"), *args.pilot_args]
    runpy.run_path(str(root / "v15_pilot.py"), run_name="__main__")


if __name__ == "__main__":
    main()
