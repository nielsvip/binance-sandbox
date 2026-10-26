"""Fail-closed runtime checks for the FIN/MEN crypto-only 7D lane."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SCOPE = "fin_men_crypto"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _crypto_symbol(symbol: str) -> bool:
    from tools.opt.evaluate_v12 import is_crypto, resolve_tokenised

    return is_crypto(symbol) and not resolve_tokenised(symbol)[1]


def assert_vector_stage_ready(lane: Path, root: Path | None = None) -> dict:
    lane = Path(lane)
    root = Path(root or Path(__file__).resolve().parents[1])
    report = json.loads((lane / "validation.json").read_text())
    now = dt.datetime.now(dt.timezone.utc)
    try:
        created = dt.datetime.fromisoformat(str(report.get("created_utc")).replace("Z", "+00:00"))
        expires = dt.datetime.fromisoformat(str(report.get("expires_utc")).replace("Z", "+00:00"))
        day = dt.date.fromisoformat(str(report.get("day")))
    except ValueError as exc:
        raise RuntimeError("7D stage has invalid date provenance") from exc
    expected_expiry = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(9, 30), NY).astimezone(dt.timezone.utc)
    if (created.tzinfo is None or created > now or expires.tzinfo is None or expires <= now
            or day != now.astimezone(NY).date() or created.astimezone(NY).date() != day
            or report.get("scope") != SCOPE or report.get("account") != "fin"
            or report.get("queue_n") != report.get("selected_7d") or int(report.get("queue_n") or 0) < 1
            or report.get("require_replay") is not True
            or report.get("replay_count") != report.get("selected_7d")
            or report.get("fallback_30d") != 0 or not report.get("stage_only")
            or abs((expires - expected_expiry).total_seconds()) > 1):
        raise RuntimeError("7D stage is incomplete, stale, non-FIN, or outside today's scope")
    sets_path = lane / "vec_driven_sets.json"
    if report.get("sets_sha256") != _sha(sets_path):
        raise RuntimeError("7D vector sets differ from validated stage")
    sets = json.loads(sets_path.read_text())
    registry = json.loads((lane / "vec_driven.json").read_text())
    registry_expiry = dt.datetime.fromisoformat(str(registry.get("expires_utc")).replace("Z", "+00:00"))
    if registry_expiry.tzinfo is None or abs((registry_expiry - expires).total_seconds()) > 1:
        raise RuntimeError("7D vector registry expiry differs from validation")
    set_keys = {key for key, value in sets.items() if not key.startswith("_") and isinstance(value, dict)}
    registry_keys = {key for key, value in registry.items() if not key.startswith("_") and isinstance(value, dict)}
    if not set_keys or set_keys != registry_keys:
        raise RuntimeError("7D registry and verified settings have different symbol sets")
    from tools.account_comparison_books import side_allowed, source_account

    if source_account("fin", now) != "men":
        raise RuntimeError("7D FIN lane is not currently mirrored to MEN")
    for sym_side in set_keys:
        if "_" not in sym_side:
            raise RuntimeError(f"invalid 7D key {sym_side!r}")
        symbol, side = sym_side.rsplit("_", 1)
        entry = sets[sym_side]
        auth = registry[sym_side]
        if (side not in ("LONG", "SHORT") or not _crypto_symbol(symbol)
                or entry.get("mode") != "live" or entry.get("account") != "fin"
                or entry.get("window_days") != 7 or entry.get("verified") is not True
                or entry.get("source") != "7d_optimized"
                or not isinstance(entry.get("overrides"), dict) or not entry["overrides"]
                or auth.get("mode") != "live" or auth.get("account") != "fin"
                or not side_allowed(root, "fin", symbol, side, now)):
            raise RuntimeError(f"7D key is not a verified current MEN crypto side: {sym_side}")
    return registry


def fin_side_authorized(lane: Path, root: Path, symbol: str, side: str) -> bool:
    registry = assert_vector_stage_ready(lane, root)
    sym_side = f"{str(symbol).upper()}_{str(side).upper()}"
    entry = registry.get(sym_side)
    return bool(isinstance(entry, dict) and entry.get("mode") == "live" and entry.get("account") == "fin")


def assert_ready(account: str, root: Path) -> None:
    if os.environ.get("VEC_LIVE_SCOPE", "30d").lower() != "7d":
        return
    if account != "fin":
        raise RuntimeError(f"7D lane is FIN/MEN crypto only, got {account!r}")
    from tools.account_comparison_books import source_account

    now = dt.datetime.now(dt.timezone.utc)
    if source_account("fin", now) != "men":
        raise RuntimeError("7D FIN lane requires the current MEN mirror")
    db = Path(os.environ.get("PER_SYM_STORE_DB") or "").resolve()
    if db.name != "per_sym_store_7d.db" or not db.is_file():
        raise RuntimeError("7D lane requires its staged per_sym_store_7d.db")
    report_path = db.parent / "validation.json"
    report = json.loads(report_path.read_text())
    if report.get("scope") != SCOPE or report.get("account") != "fin" or report.get("day") != now.astimezone(NY).date().isoformat():
        raise RuntimeError("7D settings database is not today's FIN/MEN stage")
    if report.get("db_sha256") != _sha(db):
        raise RuntimeError("7D settings database differs from validated stage")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("7D settings database failed integrity check")
    finally:
        con.close()
    lane = Path(root) / "data" / "vec_live_7d"
    registry = assert_vector_stage_ready(lane, root)
    sets = json.loads((lane / "vec_driven_sets.json").read_text())
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for sym_side, entry in sets.items():
            row = con.execute("SELECT overrides_json, meta_json FROM per_sym_active WHERE sym_side=?", (sym_side,)).fetchone()
            if row is None:
                raise RuntimeError(f"7D database is missing {sym_side}")
            overrides = json.loads(row[0] or "{}")
            meta = json.loads(row[1] or "{}")
            if overrides != entry.get("overrides") or meta.get("window_days") != 7:
                raise RuntimeError(f"7D database settings do not match verified set for {sym_side}")
    finally:
        con.close()
    if not any(isinstance(v, dict) and v.get("account") == account and v.get("mode") == "live"
               for k, v in registry.items() if not k.startswith("_")):
        raise RuntimeError(f"7D vector registry has no live {account} keys")
