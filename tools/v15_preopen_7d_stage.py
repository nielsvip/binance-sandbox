#!/usr/bin/env python3
"""Validate a complete 7D optimization pass and stage account-specific artifacts.

This tool never changes the active per-sym store or the active vector directory.
Its output is inert until the account supervisors explicitly select it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


def digest(value: object, algorithm: str = "sha256") -> str:
    return getattr(hashlib, algorithm)(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_one(ss: str, source: dict, result_dir: Path, today: str) -> dict:
    progress_path = result_dir / ss / "progress" / f"{ss}_7d_progress.json"
    out_dir = result_dir / ss / "out"
    progress = json.loads(progress_path.read_text())
    if progress.get("window_days") != 7:
        raise ValueError(f"{ss}: progress is not 7D")
    if not progress.get("result_done_utc") or not progress.get("final_path"):
        raise ValueError(f"{ss}: 7D optimization has not completed")
    if progress.get("publish_class") == "BEST_EFFORT" or progress.get("not_compliant") or progress.get("unprotected"):
        raise ValueError(f"{ss}: 7D result failed the pilot qualification gate")
    expected_source = digest(source["overrides"])
    if progress.get("source_30d_sha256") != expected_source:
        raise ValueError(f"{ss}: 30D source hash mismatch")
    if source.get("source_kind") == "per_sym_sql":
        challenger = progress.get("defaults_challenger") or {}
        if challenger.get("choose_mode") is True and challenger.get("chosen") != "start":
            raise ValueError(f"{ss}: SQL baseline was replaced by {challenger.get('chosen')}")
    overrides = progress.get("cumulative_overrides")
    defaults = progress.get("defaults_snapshot_7d")
    full = progress.get("full_config_7d")
    if not all(isinstance(v, dict) and v for v in (overrides, defaults, full)):
        raise ValueError(f"{ss}: missing 7D overrides/defaults/full set")
    if full != {**defaults, **overrides}:
        raise ValueError(f"{ss}: full set does not match defaults plus overrides")
    live_verify = progress.get("live_verified") or {}
    if live_verify.get("reason") or (live_verify.get("parity") or [False])[0] is not True:
        raise ValueError(f"{ss}: live/vector parity is unverified or failed")
    manifests = list(out_dir.glob(f"{ss}*_manifest.json"))
    if len(manifests) != 1:
        raise ValueError(f"{ss}: expected exactly one 7D manifest, found {len(manifests)}")
    manifest_path = manifests[0]
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("symside") != ss or manifest.get("window_days") != 7:
        raise ValueError(f"{ss}: manifest identity/window mismatch")
    if manifest.get("source_30d_sha256") != expected_source:
        raise ValueError(f"{ss}: manifest 30D source hash mismatch")
    if manifest.get("full_config_sha256") != digest(full):
        raise ValueError(f"{ss}: full config hash mismatch")
    if manifest.get("overrides_md5") != digest(overrides, "md5"):
        raise ValueError(f"{ss}: override hash mismatch")
    final_name = manifest.get("final_name")
    if not isinstance(final_name, str) or Path(final_name).name != final_name:
        raise ValueError(f"{ss}: invalid workbook name")
    workbook = out_dir / final_name
    if not workbook.is_file() or manifest.get("file_md5") != file_md5(workbook):
        raise ValueError(f"{ss}: workbook hash mismatch")
    metrics = manifest.get("metrics") or {}
    if metrics.get("valid") is not True or int(metrics.get("trades") or 0) < 10:
        raise ValueError(f"{ss}: no valid 7D trade result")
    if manifest.get("npz_md5") in (None, "") or manifest.get("pilot_md5") in (None, ""):
        raise ValueError(f"{ss}: missing NPZ/pilot provenance")
    age = manifest.get("npz_age_min")
    if age is None or float(age) > 20 or float(age) < -15:
        raise ValueError(f"{ss}: stale or invalid NPZ age {age}")
    published = dt.datetime.fromisoformat(str(manifest.get("published_utc")).replace("Z", "+00:00"))
    if published.astimezone(dt.timezone.utc).date().isoformat() != today:
        raise ValueError(f"{ss}: result is not from the requested premarket day")
    return {"sym_side": ss, "overrides": overrides, "defaults": defaults, "full_config": full,
            "metrics": metrics, "manifest": manifest, "manifest_path": str(manifest_path),
            "source_30d_sha256": expected_source}


def _account(ss: str, queue: dict) -> str:
    accts = [a for a, sides in queue["accounts"].items() if ss in sides]
    if len(accts) != 1 or accts[0] not in ("fin", "trc"):
        raise ValueError(f"{ss}: expected exactly one FIN/TRC account")
    return accts[0]


def _fallback_sets(source_sets: dict, published: dict, stock_symbols: set[str]) -> dict:
    out = {}
    published_sides = published.get("sym_sides") or {}
    for ss, entry in source_sets.items():
        if (ss.startswith("_") or not isinstance(entry, dict) or entry.get("mode") != "live"
                or entry.get("set_tier") != "30D_BEST"):
            continue
        if not isinstance(entry.get("overrides"), dict) or not entry["overrides"]:
            continue
        proof = published_sides.get(ss) or {}
        if proof.get("manifest_ok") is not True or proof.get("overrides") != entry["overrides"]:
            continue
        sym, side = ss.rsplit("_", 1) if "_" in ss else (ss, "")
        if side not in ("LONG", "SHORT"):
            continue
        existing_accounts = entry.get("account") or []
        existing_accounts = [existing_accounts] if isinstance(existing_accounts, str) else existing_accounts
        account = "fin" if "fin" in existing_accounts else ("trc" if sym in stock_symbols else None)
        if account is None:
            continue
        out[ss] = {"mode": "live", "account": account, "window_days": 30,
                   "verified": True, "source": "verified_30d_fallback",
                   "overrides": entry["overrides"], "source_set_tier": entry.get("set_tier")}
    return out


def stage(queue_path: Path, result_dir: Path, source_db: Path, source_sets_path: Path,
          stock_symbols_path: Path, out_dir: Path, today: str,
          published_selection_path: Path | None = None) -> dict:
    queue = json.loads(queue_path.read_text())
    sources = queue.get("sym_sides") or {}
    if not sources or queue.get("_meta", {}).get("window_days") != 7:
        raise ValueError("empty or non-7D selection queue")
    sql_sources = {ss: rec for ss, rec in sources.items() if rec.get("source_kind") == "per_sym_sql"}
    if any(rec.get("manifest_ok") is not True and ss not in sql_sources for ss, rec in sources.items()):
        raise ValueError("queue contains an unverified baseline source")
    if sql_sources:
        db_sha256 = file_sha256(source_db)
        src_check = sqlite3.connect(f"file:{source_db.resolve()}?mode=ro", uri=True)
        try:
            for ss, rec in sql_sources.items():
                if rec.get("sql_baseline_ok") is not True or rec.get("source_db_sha256") != db_sha256:
                    raise ValueError(f"{ss}: SQL baseline database hash mismatch")
                row = src_check.execute("SELECT updated_at, overrides_json, defaults_snapshot_json, full_config_json FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
                if row is None:
                    raise ValueError(f"{ss}: SQL baseline row missing")
                updated_at, overrides_raw, defaults_raw, full_raw = row
                overrides, defaults, full = (json.loads(value) for value in (overrides_raw, defaults_raw, full_raw))
                row_sha256 = digest({"sym_side": ss, "updated_at": updated_at, "overrides": overrides, "defaults": defaults, "full": full})
                if rec.get("source_row_sha256") != row_sha256 or rec.get("overrides") != overrides or full != {**defaults, **overrides}:
                    raise ValueError(f"{ss}: SQL baseline row changed or inconsistent")
        finally:
            src_check.close()
    results = {ss: validate_one(ss, rec, result_dir, today) for ss, rec in sorted(sources.items())}
    if len(results) != int(queue["_meta"]["n"]):
        raise ValueError("incomplete 7D queue")
    replays = {}
    if queue.get("_meta", {}).get("require_replay") is True:
        for ss, result in results.items():
            replay_path = result_dir / ss / "replay.json"
            replay = json.loads(replay_path.read_text())
            cutoff = dt.datetime.fromisoformat(str(replay.get("bar_cutoff_et")))
            npz_bar = dt.datetime.fromisoformat(str(replay.get("npz_last_bar_utc")))
            stock_replay = str(sources[ss].get("cat_side", "")).startswith("STOCKS_")
            if stock_replay:
                from tools.npz_cutoffs import is_premarket_bar_start
                _bar_epoch = int(npz_bar.timestamp()) if npz_bar.tzinfo is not None else 0
                _bar_et = npz_bar.astimezone(ZoneInfo("America/New_York")) if npz_bar.tzinfo is not None else None
                _bar_completed = _bar_et + dt.timedelta(minutes=15) if _bar_et else None
                _premarket_ok = (_bar_et is not None and _bar_et.date().isoformat() == today
                                 and is_premarket_bar_start(_bar_epoch)
                                 and _bar_completed <= cutoff.astimezone(ZoneInfo("America/New_York")))
                _rows_ok = int(replay.get("premarket_row_count") or 0) == 4 and bool(replay.get("npz_overlay_sha256"))
            else:
                _premarket_ok = npz_bar.tzinfo is not None and npz_bar >= cutoff.astimezone(dt.timezone.utc)
                _rows_ok = True
            if (replay.get("valid") is not True or replay.get("sym_side") != ss
                    or replay.get("optimized_full_sha256") != digest(result["full_config"])
                    or replay.get("source_30d_sha256") != digest(sources[ss]["overrides"])
                    or cutoff.tzinfo is None or cutoff.date().isoformat() != today
                    or cutoff.hour != 9 or cutoff.minute != int(queue.get("_meta", {}).get("bar_minute", 15))
                    or not _premarket_ok or not _rows_ok):
                raise ValueError(f"{ss}: missing or invalid current-day replay proof")
            replays[ss] = replay
    source_sets = json.loads(source_sets_path.read_text())
    if published_selection_path is None:
        published_selection_path = Path(__file__).resolve().parents[1] / "data/published_selection.json"
    published = json.loads(published_selection_path.read_text())
    stock_symbols = set(json.loads(stock_symbols_path.read_text()))
    # Only sides with a complete, verified 7D result may enter the 7D lane.
    sets = {}
    for ss, result in results.items():
        account = _account(ss, queue)
        sets[ss] = {"mode": "live", "account": account, "window_days": 7,
                    "verified": True, "source": "7d_optimized", "overrides": result["overrides"],
                    "source_30d_sha256": result["source_30d_sha256"],
                    "manifest_path": result["manifest_path"]}
    if not all(ss in sets and sets[ss]["window_days"] == 7 for ss in sources):
        raise ValueError("selected 7D side missing from vector sets")
    out_dir.mkdir(parents=True, exist_ok=True)
    db_tmp = out_dir / "per_sym_store_7d.staging.db"
    if db_tmp.exists():
        db_tmp.unlink()
    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    dst = sqlite3.connect(db_tmp)
    try:
        src.backup(dst)
        for ss, result in results.items():
            row = dst.execute("SELECT meta_json FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
            if row is None:
                raise ValueError(f"{ss}: no incumbent per_sym row to clone")
            meta = json.loads(row[0] or "{}")
            meta.update({"window_days": 7, "source_30d_sha256": result["source_30d_sha256"],
                         "source_7d_manifest": result["manifest_path"]})
            metrics = result["metrics"]
            dst.execute("""UPDATE per_sym_active SET updated_at=?, winning_tag=?,
                acc_gain_pct=?, trades=?, pool_sharpe=?, max_dd_pct=?, overrides_json=?,
                defaults_snapshot_json=?, full_config_json=?, meta_json=? WHERE sym_side=?""",
                (dt.datetime.now(dt.timezone.utc).isoformat(), f"v15_7D_{today}_{ss}",
                 float(metrics["gain_pct"]), int(metrics["trades"]),
                 float(metrics.get("pool_sharpe") or 0), float(metrics.get("max_dd_pct") or 0),
                 json.dumps(result["overrides"], sort_keys=True), json.dumps(result["defaults"], sort_keys=True),
                 json.dumps(result["full_config"], sort_keys=True), json.dumps(meta, sort_keys=True), ss))
        dst.commit()
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("staged 7D SQLite integrity check failed")
    finally:
        src.close()
        dst.close()
    db_final = out_dir / "per_sym_store_7d.db"
    os.replace(db_tmp, db_final)
    registry = {ss: {"mode": "live", "account": entry["account"]} for ss, entry in sets.items()}
    # Crypto FIN continues through the overnight session until the next
    # premarket pass. Bound the handoff even if the next pass never completes.
    registry["expires_utc"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=26)).isoformat()
    (out_dir / "vec_driven_sets.json").write_text(json.dumps(sets, sort_keys=True) + "\n")
    (out_dir / "vec_driven.json").write_text(json.dumps(registry, sort_keys=True) + "\n")
    report = {"day": today, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "queue_n": len(sources), "selected_7d": len(results),
              "require_replay": queue.get("_meta", {}).get("require_replay") is True,
              "replay_count": len(replays), "replay_sha256": digest(replays) if replays else None,
              "fallback_30d": len(sets) - len(results), "stage_only": True,
              "db": str(db_final), "sets": str(out_dir / "vec_driven_sets.json"),
              "db_sha256": file_sha256(db_final),
              "sets_sha256": file_sha256(out_dir / "vec_driven_sets.json")}
    (out_dir / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    for name in ("queue", "result-dir", "source-db", "source-sets", "stock-symbols", "out-dir"):
        ap.add_argument(f"--{name}", required=True)
    ap.add_argument("--published-selection", required=True)
    ap.add_argument("--day", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    a = ap.parse_args()
    print(json.dumps(stage(Path(a.queue), Path(a.result_dir), Path(a.source_db),
                           Path(a.source_sets), Path(a.stock_symbols), Path(a.out_dir), a.day,
                           Path(a.published_selection))))


if __name__ == "__main__":
    main()
