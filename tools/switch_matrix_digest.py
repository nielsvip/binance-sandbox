#!/usr/bin/env python3
"""Build a compact, truthful progress digest for SWITCH_MATRIX_TRB.

This is a monitoring report, never a promotion signal.  It reads the same SQLite store as
``export_switch_matrix_xls.py`` and highlights the three current pilot keys, matrix coverage,
campaign freshness, and ladder/entry/exit interaction results.  Compute suspension must not
suspend this report.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "param_results_stocks.db"
REPORTS = BASE / "data" / "reports"
DEFAULT_KEYS = ("MU_LONG", "VT_LONG", "HAO_SHORT")
CURRENT_ENGINE_CAMPAIGN = "stocks_repaired_20260725_c2"
CURRENT_ENGINE_CUTOFF = "2026-07-26T04:15:00Z"


def current_contract_fingerprints(keys: tuple[str, ...]) -> dict[str, str]:
    """Exact current code+NPZ+side fingerprints; absent/invalid keys simply get no credit."""
    try:
        try:
            from tools import persym_baseline_campaign as psc
        except ModuleNotFoundError:
            # Direct ``python tools/switch_matrix_digest.py`` puts tools/, not its parent,
            # on sys.path.
            import persym_baseline_campaign as psc

        return {
            key: psc.matrix_contract_fingerprint(*parse_key(key))
            for key in keys
        }
    except Exception as exc:
        raise RuntimeError(
            f"cannot compute repaired matrix contract fingerprints: {exc}"
        ) from exc


def norm_val(value) -> str:
    text = str(value).strip()
    low = text.lower()
    if low in {"true", "yes", "on"}:
        return "true"
    if low in {"false", "no", "off"}:
        return "false"
    if low in {"none", "null", ""}:
        return "none"
    try:
        number = float(text)
        if number != number or number in (float("inf"), float("-inf")):
            return low
        return str(int(number)) if number == int(number) else str(number)
    except (TypeError, ValueError, OverflowError):
        return low


def manifest_rows() -> set[tuple[str, str]]:
    path = BASE / "data" / "param_sweep_manifest_tradier.json"
    if not path.exists():
        return set()
    params = json.loads(path.read_text()).get("params", {})
    rows: set[tuple[str, str]] = set()
    for name, spec in params.items():
        if isinstance(spec, dict) and not bool(spec.get("sweepable")):
            continue
        if isinstance(spec, dict):
            values = None
            for field in ("test_values", "values", "sweep_values", "range"):
                if isinstance(spec.get(field), (list, tuple)) and spec[field]:
                    values = list(spec[field])
                    break
            if values is None:
                values = [spec.get("default")]
        elif isinstance(spec, (list, tuple)):
            values = list(spec)
        else:
            values = [spec]
        for value in values:
            if "OPTION" not in name:
                rows.add((name, norm_val(value)))
    return rows


def parse_key(key: str) -> tuple[str, str]:
    symbol, side = key.rsplit("_", 1)
    return symbol.upper(), side.upper()


def fmt(value, digits=3, suffix="") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def iso_age(ts: str | None, now: datetime) -> str:
    if not ts:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        seconds = max(0, int((now - parsed).total_seconds()))
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 86400:
            return f"{seconds / 3600:.1f}h"
        return f"{seconds / 86400:.1f}d"
    except ValueError:
        return "unknown"


def strategy_where() -> str:
    return """(
        campaign LIKE '%ladder%' OR campaign LIKE '%combo%' OR
        param IN ('STOP_PACK','TF_EXCLUDE') OR
        param LIKE '%ENTRY%' OR param LIKE '%EXIT%' OR param LIKE '%REENTRY%' OR
        param LIKE '%LADDER%' OR param LIKE 'GR_%' OR param LIKE 'MTF_%' OR
        source_file LIKE 'exposure_ladder/%' OR
        source_file LIKE 'band_ladder_sweep/%' OR
        source_file LIKE 'combo_search/%' OR
        source_file LIKE '%interaction%'
    )"""


def verdict(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    if (
        "validation_status" in keys
        and row["validation_status"] == "PASS_WITH_CAPACITY_CLAMPS"
    ):
        return "RED: CAPACITY CLAMPS"
    if "validation_status" in keys and row["validation_status"] != "PASS":
        return "INCOMPLETE: NO REAL CLOSE"
    if "reentry_violations" in keys and (row["reentry_violations"] or 0) > 0:
        return "INVALID REENTRY"
    if "inert" in keys and (row["inert"] or 0) == 1:
        return "INERT / RECONNECT"
    trades = row["trades"]
    gain = row["gain_per_mo"]
    delta = row["delta_gain_mo_vs_bh"]
    tim = row["time_in_mkt_pct"]
    if gain is None or delta is None:
        return "INCOMPLETE METRICS"
    if trades is None or trades < 1:
        return "ZERO-TRADE / INVALID"
    if row["param"] in {
        "DC_LOW4_STOP_ENABLED",
        "R1_DC_LOW4_3M_EMERGENCY_ENABLED",
    }:
        if delta <= 0:
            return "GRAY: ENTRY-QUALITY FAILURE / LOSING CHURN"
        return "DIAGNOSTIC ONLY: FAILED-ENTRY FILTER"
    if delta > 0:
        if trades <= 1 or (tim is not None and tim >= 99.5):
            return "B&H FLOOR ONLY"
        return "BEATS B&H"
    if gain <= 0:
        return "REJECT"
    return "BELOW B&H"


def matrix_description_stats() -> tuple[int, int]:
    path = REPORTS / "SWITCH_MATRIX_TRB.csv.gz"
    if not path.exists():
        return 0, 0
    total = described = 0
    try:
        with gzip.open(path, "rt", newline="") as handle:
            reader = csv.DictReader(line for line in handle if not line.startswith("#"))
            for row in reader:
                total += 1
                described += bool((row.get("description") or "").strip())
    except (OSError, csv.Error):
        return 0, 0
    return total, described


def artifact_line(path: Path, now: datetime) -> str:
    if not path.exists():
        return f"`{path.name}`: MISSING"
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return (
        f"`{path.name}`: {modified.strftime('%Y-%m-%d %H:%M:%SZ')} "
        f"({iso_age(modified.isoformat(), now)} old, {path.stat().st_size:,} bytes)"
    )


def load_vec_research(keys: tuple[str, ...]) -> dict[str, list[dict]]:
    """Load the latest isolated top-exit screen for each distinct test window.

    These artifacts are deliberately *not* SQLite/matrix evidence.  Surfacing them here
    makes fast-screen progress visible without allowing a VEC_RESEARCH result to paint a
    Tier-2 cell green.
    """
    root = REPORTS / "vec_research"
    out: dict[str, list[dict]] = {key: [] for key in keys}
    if not root.exists():
        return out
    for key in keys:
        symbol, side = parse_key(key)
        newest_by_window: dict[tuple[str, str], tuple[float, dict]] = {}
        patterns = (
            f"top_exit_*_{symbol}_{side}",
            f"top_exit_*_{symbol}_{side}_QUARANTINE",
        )
        seen: set[Path] = set()
        for pattern in patterns:
            for directory in root.glob(pattern):
                if directory in seen or not directory.is_dir():
                    continue
                seen.add(directory)
                digest_path = directory / f"digest_{symbol}_{side}.json"
                if not digest_path.exists():
                    quarantine_path = directory / "quarantine.json"
                    if quarantine_path.exists():
                        try:
                            payload = json.loads(quarantine_path.read_text())
                        except (OSError, json.JSONDecodeError):
                            continue
                        payload.setdefault("start", "unknown")
                        payload.setdefault("end_exclusive", None)
                        payload["contract_valid"] = False
                        payload["invalid_data_diagnostic"] = True
                        payload["_artifact"] = str(directory.relative_to(BASE))
                        payload["_mtime"] = datetime.fromtimestamp(
                            quarantine_path.stat().st_mtime, timezone.utc
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        window = (
                            str(payload.get("start") or "unknown"),
                            str(payload.get("end_exclusive") or "present"),
                        )
                        stamp = quarantine_path.stat().st_mtime
                        if window not in newest_by_window or stamp > newest_by_window[window][0]:
                            newest_by_window[window] = (stamp, payload)
                    continue
                try:
                    payload = json.loads(digest_path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                window = (
                    str(payload.get("start") or "unknown"),
                    str(payload.get("end_exclusive") or "present"),
                )
                stamp = digest_path.stat().st_mtime
                if window not in newest_by_window or stamp > newest_by_window[window][0]:
                    payload["_artifact"] = str(directory.relative_to(BASE))
                    payload["_mtime"] = datetime.fromtimestamp(
                        stamp, timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    newest_by_window[window] = (stamp, payload)
        out[key] = [
            pair[1]
            for pair in sorted(
                newest_by_window.values(),
                key=lambda pair: (
                    str(pair[1].get("start") or ""),
                    str(pair[1].get("end_exclusive") or "9999"),
                ),
            )
        ]
    return out


def vec_candidate(payload: dict) -> dict | None:
    candidates = payload.get("policy_top20") or []
    if candidates:
        return candidates[0]
    candidates = payload.get("overall_top20") or []
    return candidates[0] if candidates else None


def load_top_exit_walk_forward(key: str) -> dict | None:
    path = REPORTS / "vec_research" / f"top_exit_walk_forward_summary_{key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("kind") != "VEC_RESEARCH_TOP_EXIT_WALK_FORWARD":
        return None
    return payload


def load_exact_replays() -> dict[str, dict]:
    """Return the newest exact-engine execution replay for each vector artifact."""
    root = REPORTS / "vec_research"
    latest: dict[str, tuple[float, dict]] = {}
    if not root.exists():
        return {}
    for summary_path in root.glob("v8_exact_replay_*/run_summary.json"):
        try:
            payload = json.loads(summary_path.read_text())
            source = str(Path(payload["source_artifact"]).resolve())
            stamp = summary_path.stat().st_mtime
        except (KeyError, OSError, json.JSONDecodeError, TypeError):
            continue
        if source not in latest or stamp > latest[source][0]:
            latest[source] = (stamp, payload)
    return {source: pair[1] for source, pair in latest.items()}


def load_latest_robust_walk_forward(keys: tuple[str, ...]) -> dict[str, dict | None]:
    """Newest nested walk-forward digest for the newer E03/E06/E08/E09 lane."""
    root = REPORTS / "vec_research"
    out: dict[str, dict | None] = {key: None for key in keys}
    for key in keys:
        symbol, side = parse_key(key)
        matches = sorted(
            root.glob(f"walkforward_top_exit_*_{symbol}_{side}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if root.exists() else []
        for directory in matches:
            path = directory / f"walkforward_digest_{symbol}_{side}.json"
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key] = payload
            break
    return out


def load_latest_partial_regime_walk_forward(
    keys: tuple[str, ...],
) -> dict[str, dict | None]:
    """Newest E12 partial-runner / E13 regime-switch frozen-OOS digest."""
    root = REPORTS / "vec_research"
    out: dict[str, dict | None] = {key: None for key in keys}
    for key in keys:
        symbol, side = parse_key(key)
        matches = sorted(
            root.glob(f"partial_regime_walkforward_*_{symbol}_{side}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if root.exists() else []
        for directory in matches:
            path = directory / f"digest_{symbol}_{side}.json"
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key] = payload
            break
    return out


def load_latest_ladder_walk_forward(
    keys: tuple[str, ...],
) -> dict[str, dict[str, dict | None]]:
    """Newest causal ladder selection plus its exact-engine replay, when present."""
    root = REPORTS / "vec_research"
    out: dict[str, dict[str, dict | None]] = {
        key: {"research": None, "exact": None} for key in keys
    }
    if not root.exists():
        return out
    for key in keys:
        symbol, side = parse_key(key)
        research_dirs = sorted(
            root.glob(f"band_ladder_walkforward_*_{symbol}_{side}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for directory in research_dirs:
            path = directory / "result.json"
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key]["research"] = payload
            break
        exact_dirs = sorted(
            root.glob(f"v8_exact_ladder_replay_*_{symbol}_{side}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for directory in exact_dirs:
            path = directory / "run_summary.json"
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            payload["_artifact"] = str(directory.relative_to(BASE))
            out[key]["exact"] = payload
            break
    return out


def load_mu_daily_deep_pareto_holdout() -> dict | None:
    """Load the sealed MU Pareto holdout receipt as gray research evidence."""
    path = (
        REPORTS
        / "vec_research"
        / "MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260727.json"
    )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    payload["_artifact"] = str(path.relative_to(BASE))
    return payload


def load_hao_short_native_phase3() -> dict | None:
    """Load the corrected, sealed HAO SHORT-native phase-3 receipt."""
    path = REPORTS / "vec_research" / "HAO_SHORT_NATIVE_PHASE3_RECEIPT_20260727.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    payload["_artifact"] = str(path.relative_to(BASE))
    return payload


def load_hao_short_exposure_phase4() -> dict | None:
    """Load gray HAO phase-4 persistence evidence; never imply promotion."""
    path = (
        REPORTS
        / "vec_research"
        / "HAO_SHORT_EXPOSURE_PHASE4_RECEIPT_20260727.json"
    )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    payload["_artifact"] = str(path.relative_to(BASE))
    return payload


def load_latest_ladder_retunes() -> list[dict]:
    """Latest frozen exposure-retune artifact for every discovered symbol/side.

    These are vector-first research rows.  They are deliberately kept outside
    the authoritative matrix tables, but exposing them here prevents a useful
    top/bottom-cohort campaign from disappearing from the progress digest.
    """
    root = REPORTS / "vec_research"
    if not root.exists():
        return []
    newest: dict[str, dict] = {}
    directories = sorted(
        root.glob("ladder_exposure_retune_*_*_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in directories:
        path = directory / "result.json"
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        manifest = payload.get("manifest") or {}
        symbol = str(manifest.get("symbol") or "").upper()
        side = str(manifest.get("side") or "").upper()
        if not symbol or side not in {"LONG", "SHORT"}:
            continue
        key = f"{symbol}_{side}"
        if key in newest:
            continue
        payload["_artifact"] = str(directory.relative_to(BASE))
        payload["_key"] = key
        newest[key] = payload

    exact_by_key: dict[str, dict] = {}
    exact_dirs = sorted(
        root.glob("v8_exact_ladder_replay_*_*_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in exact_dirs:
        parts = directory.name.rsplit("_", 2)
        if len(parts) != 3:
            continue
        key = f"{parts[-2].upper()}_{parts[-1].upper()}"
        if key in exact_by_key:
            continue
        try:
            exact_by_key[key] = json.loads(
                (directory / "run_summary.json").read_text()
            )
        except (OSError, json.JSONDecodeError):
            continue
    for key, payload in newest.items():
        exact = exact_by_key.get(key)
        source_name = Path(str((exact or {}).get("source_artifact") or "")).name
        artifact_name = Path(str(payload.get("_artifact") or "")).name
        payload["_exact"] = (
            exact if exact and source_name and source_name == artifact_name else None
        )
    return [newest[key] for key in sorted(newest)]


def load_tradier_5m_coverage() -> dict:
    """Native/interpolated execution provenance produced by the retention audit."""
    path = REPORTS / "tradier_5m_coverage_latest.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_path_fleet_progress() -> dict:
    """Current claimable entry/exit fleet state and its latest result rows.

    The fleet database is a separate research ledger.  Surfacing it in the
    email digest must not let vector controls fill or color exact matrix cells.
    """
    root = REPORTS / "path_fleet"
    db_path = root / "queue.db"
    universe_path = root / "universe.json"
    if not db_path.exists():
        return {}
    try:
        fleet = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        fleet.row_factory = sqlite3.Row
        states = {
            row["status"]: row["n"]
            for row in fleet.execute(
                "SELECT status,COUNT(*) n FROM jobs GROUP BY status ORDER BY status"
            )
        }
        jobs = fleet.execute(
            "SELECT COUNT(*) n,MAX(heartbeat_at) latest FROM jobs"
        ).fetchone()
        rows = [
            dict(row)
            for row in fleet.execute(
                """SELECT j.path_id,r.symbol,r.side,r.stage,r.status,
                          r.strategy_return_pct,r.bh_return_pct,
                          r.same_entry_control_return_pct,r.alpha_vs_bh_pp,
                          r.alpha_vs_control_pp,r.tim_pct,r.trades,r.created_at
                          ,r.payload_json
                   FROM results r JOIN jobs j ON j.id=r.job_id
                   ORDER BY r.created_at DESC LIMIT 30"""
            )
        ]
        fleet.close()
    except (OSError, sqlite3.Error):
        return {}
    for row in rows:
        try:
            payload = json.loads(row.pop("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        row["metric_scope"] = payload.get("metric_scope") or "LEGACY_UNSCOPED"
        row["return_unit"] = payload.get("return_unit") or "LEGACY_UNSCOPED"
        row["return_aggregation"] = (
            payload.get("return_aggregation") or "LEGACY_UNSCOPED"
        )
        row["tim_unit"] = payload.get("tim_unit") or "LEGACY_UNSCOPED"
        row["tim_aggregation"] = (
            payload.get("tim_aggregation") or "LEGACY_UNSCOPED"
        )
        row["tim_metric"] = payload.get("tim_metric")
        row["tim_binary_pct"] = payload.get("tim_binary_pct")
        row["tim_weighted_pct"] = payload.get("tim_weighted_pct")
        row["capital_base_usd"] = payload.get("capital_base_usd")
        row["fold"] = payload.get("fold")
    try:
        universe = json.loads(universe_path.read_text())
    except (OSError, json.JSONDecodeError):
        universe = {}
    return {
        "root": root,
        "states": states,
        "job_count": int(jobs["n"] or 0),
        "latest_heartbeat": jobs["latest"],
        "rows": rows,
        "universe": universe,
    }


def format_fleet_metric_scope(row: dict) -> str:
    """Human-readable scope without hiding binary/weighted exact TIM."""
    scope = row.get("metric_scope") or "LEGACY_UNSCOPED"
    ret = row.get("return_unit") or "LEGACY_UNSCOPED"
    ret_agg = row.get("return_aggregation") or "LEGACY_UNSCOPED"
    tim_unit = row.get("tim_unit") or "LEGACY_UNSCOPED"
    tim_agg = row.get("tim_aggregation") or "LEGACY_UNSCOPED"
    parts = [
        f"{scope}",
        f"return={ret} ({ret_agg})",
        f"TIM={tim_unit} ({tim_agg})",
    ]
    binary = row.get("tim_binary_pct")
    weighted = row.get("tim_weighted_pct")
    if binary is not None or weighted is not None:
        parts.append(
            f"binary={fmt(binary, 3, '%')}; weighted={fmt(weighted, 3, '%')}"
        )
    if row.get("fold"):
        parts.append(f"fold={row['fold']}")
    return "; ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", default=",".join(DEFAULT_KEYS),
                        help="comma-separated SYMBOL_SIDE pilot keys")
    parser.add_argument("--output", default=str(REPORTS / "SWITCH_MATRIX_TRB_DIGEST.md"))
    parser.add_argument("--recent", type=int, default=8,
                        help="recent strategy rows per pilot key")
    args = parser.parse_args()
    keys = tuple(k.strip().upper() for k in args.keys.split(",") if k.strip())
    out = Path(args.output)
    now = datetime.now(timezone.utc)

    if not DB.exists():
        raise SystemExit(f"missing result database: {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")

    tables = {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    cell_columns = {
        row[1] for row in con.execute("PRAGMA table_info(param_cells)")
    }
    contract_columns = {
        "validation_status",
        "contract_fingerprint",
        "real_closes",
        "reentry_violations",
        "inert",
    }.issubset(cell_columns)
    contract_fps = current_contract_fingerprints(keys)
    latest_parts = [
        f"SELECT MAX(ts) ts FROM {name}"
        for name in ("param_cells", "key_baseline", "stage_results")
        if name in tables
    ]
    db_latest = con.execute(
        "SELECT MAX(ts) FROM (" + " UNION ALL ".join(latest_parts) + ")"
    ).fetchone()[0]
    engine_total = con.execute(
        "SELECT COUNT(*) FROM param_cells WHERE COALESCE(tier,'ENGINE')='ENGINE'"
    ).fetchone()[0]
    current_engine = 0
    quarantined_engine = engine_total
    vec_total = con.execute(
        "SELECT COUNT(*) FROM param_cells WHERE tier='VEC'"
    ).fetchone()[0]
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_engine = (
        con.execute(
            "SELECT COUNT(*) FROM param_cells "
            "WHERE COALESCE(tier,'ENGINE')='ENGINE' AND campaign=? AND ts >= ? "
            "AND validation_status IN "
            "('PASS','PASS_WITH_CAPACITY_CLAMPS','INCOMPLETE_NO_REAL_CLOSE')",
            (CURRENT_ENGINE_CAMPAIGN, max(cutoff, CURRENT_ENGINE_CUTOFF)),
        ).fetchone()[0]
        if contract_columns
        else 0
    )

    actionable = manifest_rows()
    target_predicate = " OR ".join("(symbol=? AND side=?)" for _ in keys)
    target_args = [part for key in keys for part in parse_key(key)]
    raw_cells = (
        con.execute(
            "SELECT symbol,side,param,value_json,ts,contract_fingerprint,"
            "validation_status FROM param_cells "
            "WHERE COALESCE(tier,'ENGINE')='ENGINE' AND campaign=? AND ts>=? AND ("
            + target_predicate
            + ") ORDER BY ts",
            [CURRENT_ENGINE_CAMPAIGN, CURRENT_ENGINE_CUTOFF, *target_args],
        ).fetchall()
        if contract_columns
        else []
    )
    filled: dict[str, set[tuple[str, str]]] = {key: set() for key in keys}
    latest_by_key: dict[str, str | None] = {key: None for key in keys}
    for row in raw_cells:
        key = f"{row['symbol']}_{row['side']}"
        if (
            row["validation_status"] not in (
                "PASS",
                "PASS_WITH_CAPACITY_CLAMPS",
                "INCOMPLETE_NO_REAL_CLOSE",
            )
            or row["contract_fingerprint"] != contract_fps.get(key)
        ):
            continue
        logical = (row["param"], norm_val(row["value_json"]))
        if logical in actionable:
            filled[key].add(logical)
        latest_by_key[key] = row["ts"]
        current_engine += 1
    valid_statuses = {
        "PASS",
        "PASS_WITH_CAPACITY_CLAMPS",
        "INCOMPLETE_NO_REAL_CLOSE",
    }
    stale_fingerprint_rows = sum(
        1
        for row in raw_cells
        if row["validation_status"] in valid_statuses
        and row["contract_fingerprint"]
        != contract_fps.get(f"{row['symbol']}_{row['side']}")
    )
    invalid_status_rows = sum(
        1 for row in raw_cells if row["validation_status"] not in valid_statuses
    )
    current_matrix_latest = max(
        (ts for ts in latest_by_key.values() if ts),
        default=None,
    )
    recent_engine = sum(
        1 for row in raw_cells
        if row["ts"] >= max(cutoff, CURRENT_ENGINE_CUTOFF)
        and row["validation_status"] in (
            "PASS",
            "PASS_WITH_CAPACITY_CLAMPS",
            "INCOMPLETE_NO_REAL_CLOSE",
        )
        and row["contract_fingerprint"]
        == contract_fps.get(f"{row['symbol']}_{row['side']}")
    )
    quarantined_engine = max(0, engine_total - current_engine)

    strategy_rows: dict[str, list[sqlite3.Row]] = {}
    for key in keys:
        symbol, side = parse_key(key)
        strategy_rows[key] = con.execute(
            "SELECT symbol,side,campaign,param,value_json,acc_gain_pct,gain_per_mo,"
            "delta_gain_mo_vs_bh,trades,time_in_mkt_pct,pool_sharpe,source_file,ts,"
            "validation_status,contract_fingerprint,real_closes,reentry_violations,inert "
            "FROM param_cells WHERE COALESCE(tier,'ENGINE')='ENGINE' "
            "AND symbol=? AND side=? AND campaign=? AND ts>=? AND "
            "validation_status IN "
            "('PASS','PASS_WITH_CAPACITY_CLAMPS','INCOMPLETE_NO_REAL_CLOSE') AND "
            + strategy_where()
            + " ORDER BY ts DESC",
            (symbol, side, CURRENT_ENGINE_CAMPAIGN, CURRENT_ENGINE_CUTOFF),
        ).fetchall() if contract_columns else []
        strategy_rows[key] = [
            row for row in strategy_rows[key]
            if row["contract_fingerprint"] == contract_fps.get(key)
        ]

    campaign_rows = con.execute(
        "SELECT COALESCE(tier,'ENGINE') tier,campaign,COUNT(*) n,MAX(ts) latest "
        "FROM param_cells WHERE campaign=? AND ts>=? "
        "GROUP BY COALESCE(tier,'ENGINE'),campaign ORDER BY latest DESC LIMIT 12",
        (CURRENT_ENGINE_CAMPAIGN, CURRENT_ENGINE_CUTOFF),
    ).fetchall()
    desc_total, desc_filled = matrix_description_stats()
    vec_research = load_vec_research(keys)
    walk_forward = {
        key: load_top_exit_walk_forward(key) for key in keys
    }
    exact_replays = load_exact_replays()
    robust_walk_forward = load_latest_robust_walk_forward(keys)
    partial_regime_walk_forward = load_latest_partial_regime_walk_forward(keys)
    ladder_walk_forward = load_latest_ladder_walk_forward(keys)
    mu_pareto_holdout = load_mu_daily_deep_pareto_holdout()
    hao_short_phase3 = load_hao_short_native_phase3()
    hao_short_phase4 = load_hao_short_exposure_phase4()
    ladder_retunes = load_latest_ladder_retunes()
    coverage_5m = load_tradier_5m_coverage()
    path_fleet = load_path_fleet_progress()
    coverage_symbols = coverage_5m.get("symbols", {})
    covered_native_symbols = sum(
        int(row.get("native_source", {}).get("rows", 0) or 0) > 0
        for row in coverage_symbols.values()
    )
    covered_native_rows = sum(
        int(row.get("native_source", {}).get("rows", 0) or 0)
        for row in coverage_symbols.values()
    )

    lines = [
        f"# SWITCH_MATRIX_TRB progress digest — {now.strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 "
        "replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.",
        "",
        "## Freshness",
        "",
        f"- Current repaired matrix latest row: `{current_matrix_latest or 'none'}` "
        f"({iso_age(current_matrix_latest, now)} old).",
        f"- Generic DB activity (includes historical/stage tables): `{db_latest or 'none'}` "
        f"({iso_age(db_latest, now)} old); it is not matrix freshness.",
        f"- Current repaired-contract ENGINE rows: **{current_engine:,}**; "
        f"new current rows in 24h: **{recent_engine:,}**.",
        f"- Raw repaired-campaign pilot rows since cutoff: **{len(raw_cells):,}**; "
        f"**{stale_fingerprint_rows:,}** are preserved but invalidated by the newer "
        f"code+NPZ+side fingerprint, and **{invalid_status_rows:,}** fail validation status. "
        "Blank current cells must be regenerated; they are not silently backfilled from old code.",
        f"- Historical/pre-fix ENGINE rows quarantined from current rankings: "
        f"**{quarantined_engine:,}/{engine_total:,}**. They remain preserved as evidence.",
        f"- Current contract: campaign `{CURRENT_ENGINE_CAMPAIGN}`, cutoff "
        f"`{CURRENT_ENGINE_CUTOFF}`, exact code+NPZ+side fingerprint required.",
        f"- VEC diagnostic rows: **{vec_total:,}** (never matrix proof).",
        f"- " + artifact_line(REPORTS / "SWITCH_MATRIX_TRB.xlsx", now),
        f"- " + artifact_line(REPORTS / "SWITCH_MATRIX_TRB.csv.gz", now),
        f"- Description coverage in current CSV: **{desc_filled:,}/{desc_total:,}** rows.",
        "",
        "## Stocks 5m execution provenance",
        "",
        "Historical native 5m availability is provider-limited. Older rows use the disclosed "
        "containing-15m interpolation; native bars replace it permanently as they are collected.",
        f"Retention report scope: **{len(coverage_symbols):,} symbols**, "
        f"**{covered_native_symbols:,} native archives**, **{covered_native_rows:,} native rows**, "
        f"**{len(coverage_5m.get('warnings') or []):,} availability warnings**, "
        f"**{len(coverage_5m.get('errors') or []):,} hard errors**.",
        "",
        "| key | native rows | native coverage | native range | interpolated rows | interpolated coverage | retention |",
        "|---|---:|---:|---|---:|---:|---|",
    ]
    for key in keys:
        symbol, _side = parse_key(key)
        row = coverage_symbols.get(symbol, {})
        source = row.get("native_source", {})
        npz = row.get("npz", {})
        native = npz.get("native", {})
        interpolated = npz.get("interpolated", {})
        errors = row.get("errors") or []
        if not row:
            lines.append(f"| {key} | — | — | — | — | — | MISSING REPORT |")
            continue
        native_range = (
            f"{str(source.get('start') or '—')[:10]} → "
            f"{str(source.get('end') or '—')[:10]}"
        )
        lines.append(
            f"| {key} | {source.get('rows', '—')} | "
            f"{fmt(native.get('pct'), 2, '%')} | {native_range} | "
            f"{interpolated.get('rows', '—')} | "
            f"{fmt(interpolated.get('pct'), 2, '%')} | "
            f"{'PASS' if not errors else 'FAIL: ' + '; '.join(errors[:2])} |"
        )
    lines += [
        "",
        "The append/merge ledger blocks any refresh that shrinks native row count, advances "
        "the first timestamp, regresses the last timestamp, or introduces duplicate/out-of-order "
        "timestamps. `synthetic_5m_parent_close_ts` records the bounded 0/5/10-minute parent lag.",
        "",
        "## Pilot-key matrix coverage",
        "",
        "| key | actionable cells filled | coverage | latest Tier-2 row | age | strategy tests |",
        "|---|---:|---:|---|---:|---:|",
    ]
    denominator = len(actionable)
    for key in keys:
        n = len(filled[key])
        pct = 100.0 * n / denominator if denominator else 0.0
        lines.append(
            f"| {key} | {n:,}/{denominator:,} | {pct:.1f}% | "
            f"{latest_by_key[key] or '—'} | {iso_age(latest_by_key[key], now)} | "
            f"{len(strategy_rows[key]):,} |"
        )

    lines += [
        "",
        "Coverage counts exact `(switch,value)` cells in the current actionable manifest. "
        "VEC rows do not fill Tier-2 cells, and duplicate campaigns do not inflate coverage.",
        "",
        "## Path interpretation guardrails",
        "",
        "- `DC_LOW4_STOP_ENABLED` and stock `R1_DC_LOW4_3M_EMERGENCY_ENABLED` "
        "(legacy name; actual stock level is `dc_low4_5m`/`dc_high4_5m`) are "
        "**ENTRY-QUALITY FAILURE DIAGNOSTICS / LOSING-CHURN EXIT EVIDENCE**. They close a "
        "recently failed entry at a tight loss; they are not top/profit-taking exits. "
        "Below-B&H observations stay gray and preserved so they are not blindly retested.",
        "- The current `LONG_STRUCT_EXIT_TF` / `SHORT_STRUCT_EXIT_TF` path closes immediately "
        "on its selected structural break. The first armed-break → lower price/WT1 rebound-top "
        "baseline is **VEC-REJECTED / NO Tier-2 result**: "
        "`data/reports/vec_research/structural_wt_rebound_20260726T062654Z` scored 0/6 "
        "contract-valid LONG folds above side-and-hold, median alpha -4.70pp, mean TIM "
        "77.9%, and 50 exits / 30 losing. HAO_SHORT's +20.18pp is "
        "quarantined because its NPZ contract is invalid. Profit/MFE-gated variants remain "
        "research candidates and do not fill matrix cells.",
        "- The nested profit-gated grid "
        "`structural_wt_profit_grid_20260726T063646Z` selected one universal setting on "
        "MU+VT 2025Q4, then froze it. Validation had 9/9 winning exits but only 1/4 folds "
        "above B&H (median alpha -1.04pp). The remaining loss is reentry execution: delayed "
        "E10 reclaim filled 10.01% worse on recent MU and 1.46% worse on recent VT. A "
        "persistent resting-reclaim model is now the priority; this grid remains VEC-only.",
        "- Frozen exit settings with causal resting reclaim "
        "(`resting_reclaim_compare_20260726T064428Z`) improved median validation alpha to "
        "+1.94pp and cut mean reclaim overshoot from 0.80% to 0.02%. Recent MU returned "
        "+13.59% versus B&H +2.60%, but VT still trailed; this is evidence for the execution "
        "fix, not a universal promotion.",
        "- MU ladder discovery `struct_wt_resting_reclaim_probe_20260726T070000Z_MU_LONG` "
        "reached +409.93% versus B&H +204.90% (2.0006×) at 52.3% weighted exposure, but "
        "is **REJECTED versus the same-entry research control**. The source frozen 2026 "
        "ladder + 4h N=30 E02 fold returned +1,316.02% versus B&H +205.25% (6.4117×) "
        "at 77.1% exposure. The structural candidate discarded 906.09pp of return; beating "
        "B&H alone is not sufficient. Both paths remain VEC-only pending exact replay.",
        "- Nested/frozen same-ladder validation "
        "(`struct_wt_nested_frozen_20260726T073000Z`) selected only on MU Jan-Mar, "
        "then scored MU Apr-Jul without reselection. Structural returned +359.24% "
        "(2.191× B&H) but the same ladder returned +1,380.56% (8.420× B&H): "
        "-1,021.32pp and only 0.260× of ladder return. The universal VT score improved "
        "a losing ladder (-8.90% versus -61.85%) but remained below B&H (+1.57%). "
        "Verdict **REJECT / NO MATRIX**; every exit candidate must beat both B&H and "
        "the strongest same-entry/same-window causal control.",
        "- The complete registered `EXIT_STRUCTURAL_WT_LOWER_TOP` screen now supersedes "
        "the exploratory structural grids: 20 frozen top/bottom symbol-sides × 768 "
        "settings, with completed HTF bars, side isolation, resting reclaim and a "
        "compiled-to-Python parity gate. All 20 selected winners reproduced exactly, "
        "but **0/20 passed** both B&H + same-entry E02 and the 70–80% discovery/validation "
        "TIM gates. MU validated +1,127.00pp over B&H but -44.324pp versus E02 at "
        "65.21% TIM; SNDK's headline used 98.70% TIM. Path-fleet job 37 preserves all "
        "rows gray and correctly queues no exact replay.",
        "- `EXIT_PARTIAL_RUNNER` completed the corrected 192-setting registry grid on "
        "20 frozen keys (the listed 4×4×3×2×2 fields cannot equal 128; all clip sums "
        "are valid). It produced **0 strict survivors**. Five selected validation "
        "winners had zero fast partial fills; E05/E06 filled in only 25%/40% of their "
        "validation settings versus WT 100%. The winners created 919 per-exit reclaim "
        "obligations, filled 633 and left 286 open. MU made one partial (-$107.98 net) "
        "versus ten slow full exits and lost 122.07pp to E02. Job 39 preserves all "
        "20 rows gray and queues no exact replay.",
        "- `EXIT_E06_REGRESSION_RETEST` completed 3,840 same-entry candidates with "
        "**0 strict survivors**. The registry is stale: `rebound_atr` is actually "
        "passed to `corr_gate`, active code has no ATR rebound/later retest state, and "
        "there is no live Tradier E06 config key. Correlation gate 1.0 was 960/960 "
        "zero-signal/zero-fill (red); all 20 frozen winners had actual signals/exits. "
        "MU was in-band at 73.46% but lost 303.33pp to E02 and left one reclaim open. "
        "Job 46 preserves 20 gray rows and queues no exact replay.",
        "- Reentry invariant for structural research: after an exit, the stored exit/top level and "
        "reopen obligation remain latched. WT/stochastic vetoes may postpone reopening but "
        "must never erase it or allow price to outrun the stored level without reopening. "
        "This is a design requirement, not a measured performance claim.",
        "",
        "### Preserved dc_low4 diagnostic evidence (pre-repair; gray/quarantined)",
        "",
        "| key | switch | gain/mo | TIM | trades | status |",
        "|---|---|---:|---:|---:|---|",
        "| MU_LONG | False | 0.0275 | 0.13% | 17 | PRE-REPAIR / validation NULL |",
        "| MU_LONG | True | -0.1045 | 0.21% | 103 | LOSING-CHURN SIGNATURE; validation NULL |",
        "| MU_SHORT | False | -0.4670 | 6.89% | 48 | PRE-REPAIR / validation NULL |",
        "| MU_SHORT | True | -0.1061 | 0.15% | 155 | NEGATIVE + HIGH-CHURN; validation NULL |",
        "",
        "These historical rows are retained for diagnosis and excluded from the automatic "
        "matrix-fill queues. They are not valid proof because the repaired contract fields "
        "were absent.",
        "",
        "## New ladder / entry / exit / interaction strategy results",
        "",
    ]
    for key in keys:
        rows = strategy_rows[key]
        lines += [
            f"### {key}",
            "",
            "| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        if not rows:
            lines.append("| — | — | — | — | — | — | — | — | — | NO STRATEGY RESULTS |")
            lines.append("")
            continue
        for row in rows[: args.recent]:
            gain = row["gain_per_mo"]
            delta = row["delta_gain_mo_vs_bh"]
            bh = gain - delta if gain is not None and delta is not None else None
            capture = gain / bh if gain is not None and bh not in (None, 0) else None
            campaign = (row["campaign"] or "—").replace("stocks_baseline_v2_s4h__", "")
            lines.append(
                f"| {row['ts']} | {campaign} | `{row['param']}={row['value_json']}` | "
                f"{fmt(gain, 4)} | {fmt(bh, 4)} | {fmt(delta, 4)} | "
                f"{fmt(capture, 3)}× | {fmt(row['time_in_mkt_pct'], 2, '%')} | "
                f"{row['trades'] if row['trades'] is not None else '—'} | {verdict(row)} |"
            )
        lines.append("")

        candidates = [
            row for row in rows
            if row["delta_gain_mo_vs_bh"] is not None
            and row["trades"] is not None and row["trades"] >= 2
            and row["gain_per_mo"] is not None
            and row["validation_status"] == "PASS"
            and (row["real_closes"] or 0) >= 1
            and (row["reentry_violations"] or 0) == 0
            and (row["inert"] or 0) == 0
            and row["delta_gain_mo_vs_bh"] > 0
        ]
        best = max(candidates, key=lambda row: row["delta_gain_mo_vs_bh"], default=None)
        if best is None:
            lines.append("Best trading candidate: **none with at least two trades and complete return metrics**.")
        else:
            lines.append(
                "Best measured trading candidate: "
                f"`{best['param']}={best['value_json']}` — gain/mo {fmt(best['gain_per_mo'], 4)}, "
                f"vs B&H/mo {fmt(best['delta_gain_mo_vs_bh'], 4)}, "
                f"TIM {fmt(best['time_in_mkt_pct'], 2, '%')}, trades {best['trades']} "
                f"(**{verdict(best)}**)."
            )
        lines.append("")

    lines += [
        "## Frozen walk-forward verdict",
        "",
        "| key | policy | discovery | frozen validation | validation TIM | verdict |",
        "|---|---|---:|---:|---:|---|",
    ]
    for key in keys:
        payload = walk_forward.get(key)
        if not payload:
            lines.append(f"| {key} | — | — | — | — | PENDING |")
            continue
        selection = payload["selection_test"]
        policy = selection["frozen_policy"]
        discovery = selection["discovery"]
        validation = selection["validation"]
        lines.append(
            f"| {key} | `{policy['strategy']} + {policy['reentry']}` | "
            f"{fmt(discovery.get('strategy_bh_multiple'), 3)}× B&H | "
            f"{fmt(validation.get('strategy_bh_multiple'), 3)}× B&H | "
            f"{fmt(validation.get('tim_rth_pct'), 2, '%')} | "
            f"{'PASS' if selection.get('pass') else 'FAIL / NO PROMOTION'} |"
        )
    lines += [
        "",
        "This table freezes the discovery choice before reading validation. It takes "
        "precedence over each window's separately re-optimized best row.",
        "",
        "## New causal-exit nested walk-forward",
        "",
        "| key | families | strict frozen result | strict TIM | clean frozen result | stability | verdict |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        payload = robust_walk_forward.get(key)
        if not payload:
            lines.append(f"| {key} | — | — | — | — | — | PENDING |")
            continue
        strict = payload.get("strict_policy_frozen_validation")
        clean = payload.get("data_clean_frozen_validation")
        stability = payload.get("parameter_stability") or {}
        families = "/".join(payload.get("families") or [])
        if strict:
            strict_result = (
                f"{fmt(strict.get('strategy_compounded_gain_pct'), 3, '%')} vs "
                f"{fmt(strict.get('bh_compounded_gain_pct'), 3, '%')} B&H "
                f"({fmt(strict.get('gain_bh_multiple'), 3)}×)"
            )
            strict_tim = fmt(strict.get("weighted_tim_rth_pct"), 2, "%")
        else:
            strict_result = "NO STRICT FOLDS"
            strict_tim = "—"
        if clean:
            clean_result = (
                f"{fmt(clean.get('strategy_compounded_gain_pct'), 3, '%')} vs "
                f"{fmt(clean.get('bh_compounded_gain_pct'), 3, '%')} B&H "
                f"({fmt(clean.get('gain_bh_multiple'), 3)}×)"
            )
        else:
            clean_result = "—"
        repeat = fmt(100.0 * float(stability.get("exact_strategy_repeat_rate") or 0), 1, "%")
        promoted = bool(
            strict
            and strict.get("gain_bh_multiple") is not None
            and float(strict["gain_bh_multiple"]) > 1.0
            and bool(strict.get("tim_target_70_80"))
            and bool(strict.get("all_mandatory_reclaim"))
        )
        verdict_text = "SCREEN PASS; REPLAY REQUIRED" if promoted else "REJECT / NO PROMOTION"
        lines.append(
            f"| {key} | {families or '—'} | {strict_result} | {strict_tim} | "
            f"{clean_result} | {repeat} exact repeat | {verdict_text} |"
        )
    lines += [
        "",
        "This lane uses nested 12-month discovery with frozen three-month validation, "
        "faithful-engine cost semantics, and excludes VT folds overlapping its known source gap.",
        "",
        "## Partial-runner and regime nested walk-forward",
        "",
        "| key | family | frozen strategy vs B&H | weighted TIM | partial P&L / exits | stability | verdict |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        payload = partial_regime_walk_forward.get(key)
        if not payload:
            lines.append(f"| {key} | — | — | — | — | — | PENDING |")
            continue
        results = payload.get("results") or {}
        for family in ("E12_PARTIAL_THEN_RUNNER", "E13_REGIME_SWITCHED"):
            row = results.get(family) or {}
            aggregate = row.get("aggregate_gap_clean_frozen") or {}
            stability = row.get("stability") or {}
            ratio = aggregate.get("strategy_to_bh_equity_ratio")
            comparison = (
                f"{fmt(aggregate.get('strategy_compounded_gain_pct'), 3, '%')} vs "
                f"{fmt(aggregate.get('bh_compounded_gain_pct'), 3, '%')} "
                f"({fmt(ratio, 3)}× equity)"
            )
            partial_pnl = aggregate.get("realized_partial_pnl_net_equity")
            partial_exits = aggregate.get("partial_exit_count")
            partial_text = (
                f"{fmt(partial_pnl, 4)} / {partial_exits}"
                if partial_pnl is not None and partial_exits is not None
                else "—"
            )
            stable = bool(stability.get("stable"))
            accepted = (
                row.get("status") == "ACCEPTED"
                and ratio is not None
                and float(ratio) > 1.0
            )
            lines.append(
                f"| {key} | {family.replace('_', ' ')} | {comparison} | "
                f"{fmt(aggregate.get('weighted_exposure_time_pct'), 2, '%')} | "
                f"{partial_text} | {'STABLE' if stable else 'UNSTABLE'} | "
                f"{'SCREEN PASS; REPLAY REQUIRED' if accepted else 'REJECT / NO PROMOTION'} |"
            )
    lines += [
        "",
        "E12 reports net realized partial P&L separately. Positive partial clips do not "
        "constitute edge when lost runner exposure and re-add timing leave compounded equity "
        "below B&H.",
        "",
        "## Causal ladder multiplier walk-forward",
        "",
        "| key | frozen OOS strategy | B&H | multiple | weighted TIM | exact-engine parity | verdict |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for key in keys:
        payload = ladder_walk_forward.get(key) or {}
        research = payload.get("research")
        exact = payload.get("exact")
        if not research:
            status = "QUARANTINED / INVALID DATA" if key == "HAO_SHORT" else "PENDING"
            lines.append(f"| {key} | — | — | — | — | — | {status} |")
            continue
        aggregate = research.get("frozen_oos_aggregate") or {}
        promotion_allowed = bool(
            (research.get("manifest") or {}).get("promotion_allowed")
        )
        exact_ok = bool(
            exact
            and exact.get("status") == "PASS"
            and exact.get("signal_parity") is True
            and (exact.get("audit") or {}).get("status") == "PASS"
        )
        exact_text = "PASS" if exact_ok else ("FAIL" if exact else "PENDING")
        multiple = aggregate.get("strategy_bh_multiple")
        accepted = bool(
            exact_ok
            and promotion_allowed
            and multiple is not None
            and float(multiple) > 1.0
        )
        verdict_text = (
            "PROMOTION ELIGIBLE"
            if accepted
            else (
                "RESEARCH EDGE; PROMOTION BLOCKED"
                if exact_ok and multiple is not None and float(multiple) > 1.0
                else "REJECT / NO PROMOTION"
            )
        )
        lines.append(
            f"| {key} | {fmt(aggregate.get('capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('bh_capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(multiple, 3)}× | "
            f"{fmt(aggregate.get('exposure_weighted_tim_pct_row_weighted'), 2, '%')} | "
            f"{exact_text} | {verdict_text} |"
        )
    lines += [
        "",
        "The MU exact replay covers the latest frozen fold: 34/34 actions, "
        "+1,316.021% return, 77.09% weighted TIM, zero future HTF sources, zero clamps, "
        "and exact signal/fill/accounting parity. It remains research-only because the "
        "campaign explicitly sets `promotion_allowed=false`. VT fails frozen OOS; HAO "
        "remains data-quarantined.",
        "",
        "## MU stable-ladder Pareto holdout",
        "",
        "| candidate | discovery | untouched holdout | B&H | multiple | TIM | exact | verdict |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    if not mu_pareto_holdout:
        lines.append("| — | — | — | — | — | — | — | NO RECEIPT |")
    else:
        candidate = mu_pareto_holdout.get("candidate") or {}
        discovery = mu_pareto_holdout.get("discovery") or {}
        final = mu_pareto_holdout.get("final") or {}
        exact = mu_pareto_holdout.get("exact_v3") or {}
        exact_ok = bool(
            exact.get("status") == "PASS"
            and exact.get("signal_parity") is True
            and int(exact.get("actions_scheduled") or 0)
            == int(exact.get("actions_executed") or -1)
            and int(exact.get("future_htf_count") or 0) == 0
        )
        failures = ", ".join(final.get("failures") or []) or "none"
        verdict_text = (
            "PROMOTION ELIGIBLE"
            if bool(final.get("pass"))
            and exact_ok
            and bool(mu_pareto_holdout.get("promotion_allowed"))
            else f"GRAY / HOLDOUT REJECT ({failures})"
        )
        lines.append(
            f"| C{candidate.get('number', '—')} `{candidate.get('label', '—')}` | "
            f"{'PASS' if discovery.get('pass') else 'FAIL'} | "
            f"{fmt(final.get('return_pct'), 3, '%')} | "
            f"{fmt(final.get('bh_return_pct'), 3, '%')} | "
            f"{fmt(final.get('bh_multiple'), 3)}× | "
            f"{fmt(final.get('weighted_tim_pct'), 2, '%')} | "
            f"{'PASS' if exact_ok else 'FAIL/PENDING'} | {verdict_text} |"
        )
    lines += [
        "",
        "This lane used a preregistered Pareto contract before opening the final fold. "
        "A strong return cannot repair a missed exposure gate after the result is known; "
        "the row therefore remains gray and is not written to the promotion matrix.",
        "",
        "## HAO SHORT-native phase 3",
        "",
        "| contract | candidates | E02 beat side benchmark | E05 beat side benchmark | "
        "TIM-valid | final | exact | verdict |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    if not hao_short_phase3:
        lines.append("| — | — | — | — | — | — | — | NO RECEIPT |")
    else:
        exits = hao_short_phase3.get("exit_summary") or {}
        e02 = exits.get("EXIT_E02_DONCHIAN") or {}
        e05 = exits.get("EXIT_E05_DIVERGENCE_RETEST") or {}
        tim_valid = sum(
            int((row or {}).get("weighted_tim_70_80_both_discovery_folds") or 0)
            for row in (e02, e05)
        )
        lines.append(
            f"| `{hao_short_phase3.get('contract', '—')}` | "
            f"{hao_short_phase3.get('candidate_count', '—')} | "
            f"{e02.get('beats_short_bh_or_cash_both_discovery_folds', '—')} | "
            f"{e05.get('beats_short_bh_or_cash_both_discovery_folds', '—')} | "
            f"{tim_valid} | "
            f"{hao_short_phase3.get('final_fold_status', '—')} | "
            f"{hao_short_phase3.get('exact_replay_status', '—')} | "
            f"{hao_short_phase3.get('status', '—')} |"
        )
    lines += [
        "",
        "V1–V3 were explicitly invalidated. V4 fixes the persistent-reentry state "
        "contract, uses the shared completed-parent clock, and keeps the final fold sealed "
        "because no discovery candidate met every exposure/control gate.",
        "",
        "## HAO SHORT exposure/persistence phase 4",
        "",
        "| contract | discovery strict | D1 strategy / B&H / TIM | "
        "D2 strategy / B&H / TIM | final strategy / B&H / TIM | exact | verdict |",
        "|---|---:|---|---|---|---|---|",
    ]
    if not hao_short_phase4:
        lines.append("| — | — | — | — | — | — | NO RECEIPT |")
    else:
        folds = hao_short_phase4.get("frozen_discovery") or []
        final = hao_short_phase4.get("final") or {}

        def phase4_fold_text(index: int) -> str:
            if index >= len(folds):
                return "—"
            row = folds[index] or {}
            return (
                f"{fmt(row.get('strategy_return_pct'), 2, '%')} / "
                f"{fmt(row.get('bh_return_pct'), 2, '%')} / "
                f"{fmt(row.get('weighted_tim_pct'), 2, '%')}"
            )

        final_text = (
            f"{fmt(final.get('strategy_return_pct'), 2, '%')} / "
            f"{fmt(final.get('bh_return_pct'), 2, '%')} / "
            f"{fmt(final.get('weighted_tim_pct'), 2, '%')}"
        )
        lines.append(
            f"| `{hao_short_phase4.get('contract', '—')}` | "
            f"{hao_short_phase4.get('discovery_strict_count', '—')} | "
            f"{phase4_fold_text(0)} | {phase4_fold_text(1)} | {final_text} | "
            f"{hao_short_phase4.get('exact_replay_status', '—')} | "
            f"{hao_short_phase4.get('status', '—')} |"
        )
    lines += [
        "",
        "The frozen phase-4 E02 policy achieved 70–80% weighted exposure and beat "
        "side-aware B&H plus the phase-3 same-exit control in both discovery folds. "
        "Its untouched final return remained strong but weighted TIM fell to 36.05%; "
        "the row is gray, exact did not run, and no matrix/live state changed.",
        "",
        "## Top-10 ladder exposure retune",
        "",
        "> Frozen nested-OOS research. Aggregate exposure can hide unstable folds; a row "
        "is not promotable unless every fold also passes the fixed-control, causality, "
        "capacity, reclaim, and exposure gates.",
        "",
        "| key | strategy | B&H | multiple | identical control | alpha control | TIM | "
        "fold TIM | fills | clamps | exact | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---|",
    ]
    if not ladder_retunes:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | NO ARTIFACTS |")
    for payload in ladder_retunes:
        aggregate = payload.get("frozen_oos_aggregate") or {}
        folds = payload.get("outer_folds") or []
        fold_tim = " / ".join(
            fmt(
                (row.get("validation_metrics") or row).get(
                    "exposure_weighted_tim_pct_row_weighted",
                    (row.get("validation_metrics") or row).get(
                        "exposure_weighted_tim_pct",
                        (row.get("validation_metrics") or row).get("weighted_tim_pct"),
                    ),
                ),
                2,
                "%",
            )
            for row in folds
        ) or "—"
        exact = payload.get("_exact")
        exact_ok = bool(
            exact
            and exact.get("status") == "PASS"
            and exact.get("signal_parity") is True
            and (exact.get("audit") or {}).get("status") == "PASS"
        )
        strict = bool(aggregate.get("vector_survivor"))
        relaxed = bool(aggregate.get("relaxed_bh_survivor"))
        if strict and exact_ok:
            ladder_verdict = "EXACT PASS; PROMOTION POLICY CHECK"
        elif strict:
            ladder_verdict = "VECTOR SURVIVOR; EXACT REQUIRED"
        elif relaxed and exact_ok:
            ladder_verdict = "RESEARCH EDGE; FOLD/CONTROL BLOCKED"
        elif relaxed:
            ladder_verdict = "RESEARCH EDGE; EXACT/FOLD BLOCKED"
        else:
            ladder_verdict = "REJECT / NO PROMOTION"
        lines.append(
            f"| {payload.get('_key', '—')} | "
            f"{fmt(aggregate.get('capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('bh_capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('strategy_bh_multiple'), 3)}× | "
            f"{fmt(aggregate.get('same_control_capital_return_pct_sum'), 3, '%')} | "
            f"{fmt(aggregate.get('delta_vs_same_control_pp_sum'), 3, 'pp')} | "
            f"{fmt(aggregate.get('exposure_weighted_tim_pct_row_weighted'), 2, '%')} | "
            f"{fold_tim} | {fmt((aggregate.get('fill_ratio') or 0) * 100, 1, '%')} | "
            f"{aggregate.get('clamp_count', '—')} | "
            f"{'PASS' if exact_ok else ('FAIL' if exact else '—')} | "
            f"{ladder_verdict} |"
        )
    lines += [
        "",
        "The retune keeps exits fixed at completed-4h E02 N=30. It changes only the "
        "bounded ladder curve/trigger semantics, so alpha against the identical control "
        "does not come from a different exit or a different B&H budget.",
        "",
        "## Isolated vector research — not matrix evidence",
        "",
        "> Fast causal screens only. These rows never fill or color Tier-2 cells. Promotion "
        "requires an independent audit, a faithful-engine replay, stable out-of-sample behavior, "
        "real closes, and a changed trade fingerprint.",
        "",
        "| key | window | best visible candidate | gain | B&H | multiple | TIM | data/policy status |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        payloads = vec_research.get(key) or []
        if not payloads:
            lines.append(f"| {key} | — | — | — | — | — | — | NO VEC_RESEARCH ARTIFACT |")
            continue
        for payload in payloads:
            candidate = vec_candidate(payload)
            window = (
                f"{payload.get('start') or 'unknown'} → "
                f"{payload.get('end_exclusive') or 'present'}"
            )
            contract_ok = bool(payload.get("contract_valid"))
            diagnostic = bool(payload.get("invalid_data_diagnostic"))
            if not candidate:
                status = "QUARANTINED / INVALID DATA" if diagnostic or not contract_ok else "NO CANDIDATE"
                lines.append(
                    f"| {key} | {window} | — | — | — | — | — | {status} |"
                )
                continue
            gain = candidate.get("gain_pct")
            bh = candidate.get("bh_net_side_pct")
            multiple = candidate.get("strategy_bh_multiple")
            tim = candidate.get("tim_rth_pct")
            policy = bool(candidate.get("policy_compliant"))
            artifact_path = str((BASE / payload.get("_artifact", "")).resolve())
            replay = exact_replays.get(artifact_path)
            if diagnostic or not contract_ok:
                status = "QUARANTINED / INVALID DATA"
            elif replay and replay.get("status") == "FAIL":
                status = "EXACT ENGINE REPLAY FAIL; REJECT"
            elif (
                replay
                and replay.get("status") == "PASS"
                and multiple is not None
                and float(multiple) > 1.0
            ):
                status = "EXACT EXECUTION REPLAY PASS; ROBUSTNESS/PROMOTION BLOCKED"
            elif policy and multiple is not None and float(multiple) > 1.0:
                status = "EXPOSURE/RECLAIM PASS; FAITHFUL REPLAY PENDING"
            elif multiple is not None and float(multiple) <= 1.0:
                status = "BELOW B&H; REJECT"
            else:
                status = "RESEARCH ONLY / POLICY FAIL"
            label = f"{candidate.get('strategy', '—')} + {candidate.get('reentry', '—')}"
            lines.append(
                f"| {key} | {window} | `{label}` | {fmt(gain, 3, '%')} | "
                f"{fmt(bh, 3, '%')} | {fmt(multiple, 3)}× | {fmt(tim, 2, '%')} | {status} |"
            )

    lines += [
        "",
        "The full-period MU multiple is an optimization-screen headline, not a robust claim. "
        "Read the holdout row beside it: exposure drift or sub-B&H holdout performance blocks "
        "promotion even when the full-period row is above B&H.",
        "",
    ]

    lines += [
        "## Top/bottom-10 entry/exit path fleet",
        "",
        "> Claimable vector-first research queue. Control rows establish the frozen benchmark "
        "that later paths must beat; they are not exact-engine promotion evidence.",
        "",
    ]
    if not path_fleet:
        lines += [
            "Path fleet ledger is missing.",
            "",
        ]
    else:
        universe = path_fleet.get("universe") or {}
        snapshot = universe.get("tradeable_snapshot") or {}
        top_long = ", ".join(
            row.get("symbol", "—") for row in universe.get("top_long") or []
        )
        bottom_short = ", ".join(
            row.get("symbol", "—") for row in universe.get("bottom_short") or []
        )
        states = ", ".join(
            f"{name}={count:,}"
            for name, count in sorted((path_fleet.get("states") or {}).items())
        )
        lines += [
            f"- Jobs: **{path_fleet.get('job_count', 0):,}**; states: {states or '—'}.",
            f"- Frozen tradeable hashes: LONG `{snapshot.get('long_sha256') or '—'}`; "
            f"SHORT `{snapshot.get('short_sha256') or '—'}`.",
            f"- Top LONG cohort: {top_long or '—'}.",
            f"- Bottom SHORT cohort: {bottom_short or '—'}.",
            "",
            "| path | key | stage | metric scope / units | state | strategy | B&H | multiple | alpha B&H | same-entry alpha | TIM | trades |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        fleet_rows = path_fleet.get("rows") or []
        if not fleet_rows:
            lines.append("| — | — | — | — | — | — | — | — | — | — | — | — |")
        for row in fleet_rows:
            strategy = row.get("strategy_return_pct")
            bh = row.get("bh_return_pct")
            multiple = (
                float(strategy) / float(bh)
                if strategy is not None and bh is not None and float(bh) > 0
                else None
            )
            lines.append(
                f"| `{row.get('path_id') or '—'}` | "
                f"{row.get('symbol') or '—'}_{row.get('side') or '—'} | "
                f"{row.get('stage') or '—'} | "
                f"{format_fleet_metric_scope(row)} | "
                f"{row.get('status') or '—'} | "
                f"{fmt(strategy, 3, '%')} | {fmt(bh, 3, '%')} | "
                f"{fmt(multiple, 3)}× | {fmt(row.get('alpha_vs_bh_pp'), 3, 'pp')} | "
                f"{fmt(row.get('alpha_vs_control_pp'), 3, 'pp')} | "
                f"{fmt(row.get('tim_pct'), 2, '%')} | {row.get('trades', '—')} |"
            )
        short_rows_present = any(row.get("side") == "SHORT" for row in fleet_rows)
        lines += [
            "",
            "Metric guardrail: `VEC_NESTED_FOLD_AGGREGATE` returns are sums of "
            "outer-validation-fold capital-return percentages and are not a "
            "single holdout return. Only `FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD` "
            "rows use single-fold capital-return percentages; `LEGACY_UNSCOPED` "
            "rows are historical evidence and must not drive promotion.",
            "",
            (
                "SHORT vector controls are present but remain research-only until exact replay "
                "and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-"
                "reclaim gates pass. No LONG result is inverted or pooled."
                if short_rows_present
                else
                "The SHORT cohort remains blocked until its causal ladder mirror passes the same "
                "completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim "
                "contract. No LONG result is inverted or pooled to manufacture SHORT evidence."
            ),
            "",
        ]

    lines += [
        "## Campaign activity",
        "",
        "| tier | campaign | rows | latest | age |",
        "|---|---|---:|---|---:|",
    ]
    for row in campaign_rows:
        lines.append(
            f"| {row['tier']} | {row['campaign']} | {row['n']:,} | "
            f"{row['latest']} | {iso_age(row['latest'], now)} |"
        )

    lines += [
        "",
        "## Reading the matrix",
        "",
        "- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.",
        "- White: positive but below B&H; retain as evidence, not as a winner.",
        "- Gray: non-viable/losing result; retain so it is not blindly retested.",
        "- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.",
        "- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, "
        "not an optimized strategy.",
        "",
    ]
    con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(out)
    print(out)


if __name__ == "__main__":
    main()
