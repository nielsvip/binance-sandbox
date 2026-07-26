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
        from tools import persym_baseline_campaign as psc

        return {
            key: psc.matrix_contract_fingerprint(*parse_key(key))
            for key in keys
        }
    except Exception:
        return {}


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
        f"- Historical/pre-fix ENGINE rows quarantined from current rankings: "
        f"**{quarantined_engine:,}/{engine_total:,}**. They remain preserved as evidence.",
        f"- Current contract: campaign `{CURRENT_ENGINE_CAMPAIGN}`, cutoff "
        f"`{CURRENT_ENGINE_CUTOFF}`, exact code+NPZ+side fingerprint required.",
        f"- VEC diagnostic rows: **{vec_total:,}** (never matrix proof).",
        f"- " + artifact_line(REPORTS / "SWITCH_MATRIX_TRB.xlsx", now),
        f"- " + artifact_line(REPORTS / "SWITCH_MATRIX_TRB.csv.gz", now),
        f"- Description coverage in current CSV: **{desc_filled:,}/{desc_total:,}** rows.",
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
