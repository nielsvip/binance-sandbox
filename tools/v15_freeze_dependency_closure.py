"""Materialize source package trees and audit local import closure without live imports."""
import argparse
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--mac-inputs", type=Path, required=True)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    source = args.frozen / "code"
    dest = args.snapshot / "common_code"
    if args.audit_existing:
        assert dest.exists()
    else:
        assert not dest.exists(), "new common code root only"
        dest.mkdir()
    hashes = {}
    excluded = {"data", "backups", "archive", "logs", "node_modules", ".git", ".venv", "__pycache__", "scratch_gapfix"}
    extensions = {".py", ".pyi", ".json", ".csv", ".txt", ".yaml", ".yml", ".ini", ".cfg", ".toml", ".html", ".js", ".css", ".gz"}
    packages = []
    for child in source.iterdir():
        if child.is_dir() and not child.is_symlink() and child.name not in excluded and not child.name.startswith(("scratch", "v20_runtime")):
            if any(p.is_file() and not p.is_symlink() for p in child.glob("*.py")):
                packages.append(child.name)
    def install(file, relative):
        assert not file.is_symlink()
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # The source is an existing immutable snapshot, never production.
        os.link(file, target)
        hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    for file in ([] if args.audit_existing else source.iterdir()):
        if file.is_file() and not file.is_symlink() and file.suffix in extensions:
            install(file, file.relative_to(source))
    for package in ([] if args.audit_existing else packages):
        for directory, children, files in os.walk(source / package, followlinks=False):
            children[:] = [name for name in children if name not in excluded and not (Path(directory) / name).is_symlink()]
            for name in files:
                file = Path(directory) / name
                if file.is_file() and not file.is_symlink() and file.suffix in extensions:
                    install(file, file.relative_to(source))
    overrides = {"tools/npz_cutoffs.py": args.mac_inputs / "npz_cutoffs.py"}
    admission = json.loads((args.mac_inputs / "admission.json").read_text())
    overrides.update({name: args.mac_inputs / name for name in admission["book_sha256"]})
    for relative, file in ({} if args.audit_existing else overrides).items():
        target = dest / relative
        if target.exists():
            target.unlink()  # unlink only this NEW snapshot's hardlink before replacing
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.read_bytes())
        hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    def local_module(name):
        base = dest.joinpath(*name.split("."))
        file = base.with_suffix(".py")
        if file.is_file():
            return file
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
        if base.is_dir() and any(base.glob("*.py")):
            return base
        return None
    seeds = ["v15_pilot", "v12_quick_engine", "config", "config_tradier", "tools.opt.evaluate_v12", "tools.opt.v12_pilot", "ez_manage", "tradier_manage", "vec_paths", "vec_paths.gr_filter_vec", "vec_paths.funding_gate"]
    queue = list(seeds)
    seen = set()
    compile_failures = []
    local_missing = []
    dynamic = []
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        file = local_module(name)
        if file is None:
            if name in seeds:
                local_missing.append(name)
            continue
        if file.is_dir():
            continue
        try:
            text = file.read_text()
            tree = ast.parse(text)
            compile(tree, str(file), "exec")
        except (SyntaxError, UnicodeDecodeError) as error:
            compile_failures.append({"module": name, "error": str(error)})
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module, *[node.module + "." + item.name for item in node.names]]
            elif isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == "__import__") or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    names = [node.args[0].value]
                else:
                    dynamic.append({"module": name, "line": node.lineno, "expression": ast.unparse(node)[:160]})
            for imported in names:
                if local_module(imported):
                    queue.append(imported)
                else:
                    original = source.joinpath(*imported.split("."))
                    if original.with_suffix(".py").is_symlink() or (original.is_dir() and not original.is_symlink() and any(original.glob("*.py"))):
                        local_missing.append(imported)
    assert not any(p.is_symlink() for p in dest.rglob("*"))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "BASE_PATH": str(dest)}
    smoke = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import vec_paths, vec_paths.gr_filter_vec, vec_paths.funding_gate; from tools.npz_cutoffs import last_stock_data_bar, MAX_NPZ_AGE_MIN; print('pure dependency imports PASS')", str(dest)], cwd=dest, env=env, capture_output=True, text=True)
    if args.audit_existing:
        hashes = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.rglob("*") if p.is_file() and not p.is_symlink()}
    report = {"at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "common_code": str(dest), "frozen_source": str(args.frozen), "package_trees": sorted(packages), "files": len(hashes), "files_sha256": hashes, "static_modules_compiled": len(seen), "compile_failures": compile_failures, "local_missing": sorted(set(local_missing)), "dynamic_imports_requiring_runtime_review": dynamic, "pure_import_smoke": {"returncode": smoke.returncode, "stdout": smoke.stdout, "stderr": smoke.stderr}, "symlinks": [], "status": "SOURCE_CLOSURE_PREFLIGHT_PASS" if not compile_failures and not local_missing and smoke.returncode == 0 else "SOURCE_CLOSURE_REVIEW_REQUIRED", "limitations": ["Static closure cannot prove all runtime branch imports", "No live-manager imports executed; source compiled only", "A complete benchmark warning audit remains required; missing modules invalidate quality claims"]}
    receipt = args.snapshot / "dependency_closure.json"
    if receipt.exists():
        previous = args.snapshot / ("dependency_closure_before_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
        previous.write_bytes(receipt.read_bytes())
    receipt.write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key not in ("files_sha256", "dynamic_imports_requiring_runtime_review")}, indent=2), flush=True)


if __name__ == "__main__":
    main()
