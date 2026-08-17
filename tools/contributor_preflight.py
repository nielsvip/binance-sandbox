#!/usr/bin/env python3
"""Portable, read-only worker preflight and provenance manifest.

This tool never opens the matrix database and never writes live configuration.
It validates the mounted NPZ directory and creates an immutable run manifest
under the worker-owned result root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_FILES = ("tools/mu_combo_explorer.py", "tools/vec_band_ladder_walkforward.py", "BACKTEST_BIBLE.md")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _git_commit(root: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return None


def _npz_inventory(npz_root: Path, symbols: list[str]) -> dict[str, object]:
    if not npz_root.is_dir():
        return {"status": "QUARANTINED", "error": f"npz_root_not_directory:{npz_root}"}
    try:
        import numpy as np
    except Exception as exc:
        return {"status": "QUARANTINED", "error": f"numpy:{exc}"}
    rows: dict[str, object] = {}
    for symbol in symbols:
        path = npz_root / f"{symbol}.npz"
        if not path.is_file():
            rows[symbol] = {"status": "MISSING", "path": str(path)}
            continue
        if path.is_symlink():
            rows[symbol] = {"status": "QUARANTINED", "path": str(path), "error": "symlink_input_forbidden"}
            continue
        try:
            before = _sha256(path)
            with np.load(path, allow_pickle=False) as data:
                names = sorted(data.files)
                rows[symbol] = {
                    "status": "OK",
                    "path": str(path),
                    "sha256_before": before,
                    "fields": len(names),
                    "required_field_presence": {
                        name: name in data.files
                        for name in (
                            "timestamps", "open_5m", "close_5m", "timestamp_15m",
                            "timestamp_1h", "timestamp_4h", "timestamp_D",
                        )
                    },
                }
            after = _sha256(path)
            rows[symbol]["sha256_after"] = after
            if before != after:
                rows[symbol]["status"] = "QUARANTINED"
                rows[symbol]["error"] = "input_changed_during_preflight"
        except Exception as exc:
            rows[symbol] = {"status": "QUARANTINED", "path": str(path), "error": repr(exc)}
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-root", type=Path, required=True)
    ap.add_argument("--result-root", type=Path, required=True)
    ap.add_argument("--worker-id", default=f"{socket.gethostname()}-{platform.machine()}")
    ap.add_argument("--symbols", default="MU")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.result_root / args.worker_id / f"preflight_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    required = {
        rel: {"exists": (root / rel).is_file(), "sha256": _sha256(root / rel) if (root / rel).is_file() else None}
        for rel in REQUIRED_FILES
    }
    manifest = {
        "schema": "TRB_CONTRIBUTOR_PREFLIGHT_V1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "worker_id": args.worker_id,
        "host": socket.gethostname(),
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": sys.version},
        "git_commit": _git_commit(root),
        "repository_root": str(root),
        "npz_root": str(args.npz_root.resolve()),
        "npz_read_only": not os.access(args.npz_root, os.W_OK),
        "required_source_files": required,
        "npz_inventory": _npz_inventory(args.npz_root, symbols),
        "write_contract": {"result_root": str(args.result_root.resolve()), "matrix_drop": os.environ.get("MATRIX_DROP"), "matrix_db_write": False, "live_config_write": False},
        "symbols": symbols,
    }
    npz_rows = manifest["npz_inventory"]
    npz_ok = isinstance(npz_rows, dict) and bool(npz_rows) and all(
        isinstance(row, dict) and row.get("status") == "OK" for row in npz_rows.values()
    )
    manifest["status"] = "PASS" if all(v["exists"] for v in required.values()) and npz_ok else "QUARANTINED"
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"manifest": str(path), "status": manifest["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
