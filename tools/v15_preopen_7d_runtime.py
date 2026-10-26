"""Startup gate for FIN/TRC processes explicitly routed to the 7D lane."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_ready(account: str, root: Path) -> None:
    if os.environ.get("VEC_LIVE_SCOPE", "30d").lower() != "7d":
        return
    if account not in ("fin", "trc"):
        raise RuntimeError(f"7D lane is only authorized for FIN/TRC, got {account!r}")
    db = Path(os.environ.get("PER_SYM_STORE_DB") or "").resolve()
    if db.name != "per_sym_store_7d.db" or not db.is_file():
        raise RuntimeError("7D lane requires its staged per_sym_store_7d.db")
    report_path = db.parent / "validation.json"
    report = json.loads(report_path.read_text())
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if report.get("day") != today or report.get("queue_n") != report.get("selected_7d") or not report.get("stage_only"):
        raise RuntimeError("7D stage is incomplete or for another market day")
    if report.get("db_sha256") != _sha(db):
        raise RuntimeError("7D settings database differs from validated stage")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("7D settings database failed integrity check")
    finally:
        con.close()
    lane = Path(root) / "data" / "vec_live_7d"
    sets_path = lane / "vec_driven_sets.json"
    registry_path = lane / "vec_driven.json"
    if report.get("sets_sha256") != _sha(sets_path):
        raise RuntimeError("7D vector sets differ from validated stage")
    registry = json.loads(registry_path.read_text())
    expiry = dt.datetime.fromisoformat(str(registry.get("expires_utc")).replace("Z", "+00:00"))
    if expiry <= dt.datetime.now(dt.timezone.utc):
        raise RuntimeError("7D vector registry expired")
    if not any(isinstance(v, dict) and v.get("account") == account and v.get("mode") == "live"
               for k, v in registry.items() if not k.startswith("_")):
        raise RuntimeError(f"7D vector registry has no live {account} keys")
