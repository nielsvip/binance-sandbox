#!/usr/bin/env python3
"""Authoritative email view of the repaired direct-V8 research system.

This module is intentionally independent of ``SWITCH_MATRIX_TRB`` and the
retired vector/result stores.  The GUI-lab supervisor's audited receipts are
the source of truth: they contain the reconciled marked-equity metrics and the
exact settings used for each run.

The twice-daily results digest advances the cursor in a dedicated state file.
Morning/evening emails render the same snapshot without advancing that cursor,
so "since the last digest" has one unambiguous meaning across all emails.
"""
from __future__ import annotations

import html
import json
import math
import os
import platform
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
GUI_LAB_ROOT = Path(os.environ.get("GUI_LAB_ROOT", "/home/niels/binance-sandbox/data/reports/gui_lab"))
STATUS_NAME = "STATUS.json"
STATE_PATH = Path(
    os.environ.get("DIRECT_V8_DIGEST_STATE_PATH", str(BASE / "data" / "direct_v8_digest_state.json"))
)
TWELVE_MONTHS = 12
# Current-result views must not silently present an old one-year data surface
# as if it had reached today.  Keep the threshold equal to the runner's
# seven-day causal-NPZ guard, while still retaining old receipts on disk for
# forensic history.
MAX_RECEIPT_AGE_SECONDS = 7.0 * 86400.0
VALID_POLICIES = {
    "full_unlevered_fresh_entry_v1",
    "fractional_unlevered_selected_cash",
}
VALID_SOURCES = {
    "manual_direct_v8",
    "autonomous_direct_v8",
    "autonomous_direct_v8_combination",
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _timestamp(value: Any) -> float | None:
    number = _number(value)
    if number is not None:
        return number
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _iso(value: Any) -> str:
    stamp = _timestamp(value)
    if stamp is None:
        return "—"
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _status_path() -> Path:
    explicit = os.environ.get("DIRECT_V8_STATUS_PATH")
    if explicit:
        return Path(explicit)
    candidates = [
        GUI_LAB_ROOT / STATUS_NAME,
        Path("/home/niels/binance-sandbox/data/reports/gui_lab/STATUS.json"),
        BASE / "data" / "reports" / "gui_lab" / STATUS_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def load_status() -> tuple[dict[str, Any], Path]:
    """Load the S1 supervisor surface, with a run-folder fallback."""
    path = _status_path()
    status = _read_json(path)
    if isinstance(status, dict):
        return status, path
    rows: list[dict[str, Any]] = []
    run_root = path.parent / "manual" / "runs"
    try:
        for row_path in run_root.glob("*/status.json"):
            row = _read_json(row_path)
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        pass
    return {
        "schema": "direct-v8-digest-status-fallback-v1",
        "manual_runs": rows,
        "alerts": [],
        "direct_v8_combiner": {},
    }, path


def _flatten_row(source: dict[str, Any]) -> dict[str, Any]:
    """Normalize supervisor rows and individual run-folder status files."""
    row = dict(source)
    spec = source.get("spec") if isinstance(source.get("spec"), dict) else {}
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    for key, value in spec.items():
        row.setdefault(key, value)
    for key, value in metrics.items():
        row.setdefault(key, value)
    row.setdefault("symbol", spec.get("symbol"))
    row.setdefault("side", spec.get("side"))
    row.setdefault("months", spec.get("months"))
    row.setdefault("venue", spec.get("venue"))
    row.setdefault("asset", spec.get("venue"))
    row.setdefault("overrides", spec.get("overrides") or {})
    row.setdefault("capital_deployment_policy", spec.get("capital_deployment_policy"))
    row.setdefault("quantity_mode_requested", spec.get("quantity_mode_requested"))
    return row


def _is_current_row(row: dict[str, Any], now: float | None = None) -> bool:
    source = str(row.get("source") or "")
    try:
        months = int(row.get("months") or 0)
    except (TypeError, ValueError):
        months = 0
    if not (
        source in VALID_SOURCES
        and row.get("state") == "AUDITED"
        and months == TWELVE_MONTHS
        and str(row.get("capital_deployment_policy") or "") in VALID_POLICIES
        and bool(row.get("accounting_verified"))
        and bool(row.get("capital_normalized"))
    ):
        return False
    # Older test fixtures and a few legacy receipts do not carry a marked
    # window end.  They remain eligible for compatibility; a receipt that
    # does carry one is rejected from the current surface once it is stale.
    if now is not None:
        end = _timestamp(row.get("window_end_timestamp"))
        if end is not None and float(now) - end > MAX_RECEIPT_AGE_SECONDS:
            return False
    return True


def audited_rows(status: dict[str, Any], now: float | None = None) -> list[dict[str, Any]]:
    rows = status.get("manual_runs") if isinstance(status.get("manual_runs"), list) else []
    return [
        flattened
        for row in rows
        if isinstance(row, dict)
        for flattened in [_flatten_row(row)]
        if _is_current_row(flattened, now)
    ]


def stale_audited_rows(status: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """Return valid receipts excluded only because their window ended stale."""
    rows = status.get("manual_runs") if isinstance(status.get("manual_runs"), list) else []
    stale: list[dict[str, Any]] = []
    for source in rows:
        if not isinstance(source, dict):
            continue
        row = _flatten_row(source)
        if not _is_current_row(row):
            continue
        end = _timestamp(row.get("window_end_timestamp"))
        if end is not None and float(now) - end > MAX_RECEIPT_AGE_SECONDS:
            stale.append(row)
    return stale


def _row_time(row: dict[str, Any]) -> float:
    return _timestamp(row.get("finished_at")) or _timestamp(row.get("started_at")) or _timestamp(row.get("submitted_at")) or 0.0


def _metric(row: dict[str, Any], *names: str, default: str = "—") -> str:
    for name in names:
        value = row.get(name)
        number = _number(value)
        if number is not None:
            return f"{number:+.2f}%" if name.endswith("pct") or name.endswith("_mo_pct") else f"{number:.2f}"
    return default


def _rank(row: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    qualified = 1.0 if bool(row.get("screen_qualified")) else 0.0
    delta = _number(row.get("delta_vs_bh_per_mo_pct"), -math.inf) or -math.inf
    gain = _number(row.get("gain_per_mo_pct"), -math.inf) or -math.inf
    tim = _number(row.get("tim_pct"), math.inf) or math.inf
    dd = _number(row.get("max_dd_pct"), math.inf) or math.inf
    return (qualified, delta, gain, -tim, -dd, _row_time(row))


def best_by_symbol_side(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("symbol") or "?"), str(row.get("side") or "?"))
        grouped[key].append(row)
    return {key: max(items, key=_rank) for key, items in grouped.items()}


def _state_load() -> dict[str, Any]:
    value = _read_json(STATE_PATH)
    return value if isinstance(value, dict) else {}


def load_digest_cursor(state: dict[str, Any] | None = None) -> float:
    state = state if isinstance(state, dict) else _state_load()
    direct = state.get("direct_v8") if isinstance(state.get("direct_v8"), dict) else {}
    return _timestamp(direct.get("last_sent_ts")) or _timestamp(state.get("direct_v8_last_sent_ts")) or 0.0


def next_state(now: float, state: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = dict(state if isinstance(state, dict) else _state_load())
    previous["direct_v8"] = {
        "schema": "direct-v8-results-digest-state-v1",
        "last_sent_ts": now,
        "last_sent_iso": _iso(now),
    }
    return previous


def save_digest_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_name(f".{STATE_PATH.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, STATE_PATH)


def snapshot(now: float | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
    now = float(now if now is not None else time.time())
    state = state if isinstance(state, dict) else _state_load()
    status, status_path = load_status()
    rows = audited_rows(status, now)
    stale_rows = stale_audited_rows(status, now)
    cursor = load_digest_cursor(state)
    since = [row for row in rows if _row_time(row) > cursor and _row_time(row) <= now + 60.0]
    best = best_by_symbol_side(rows)
    best_since = best_by_symbol_side(since)
    source_age_h = (now - status_path.stat().st_mtime) / 3600.0 if status_path.exists() else None
    # The supervisor intentionally omits stale rows from STATUS.json before
    # the browser/email consumers see it.  Preserve its count so the digest
    # still explains why the current surface is empty rather than implying
    # that no stale receipts ever existed.
    supervisor_stale_count = _number(status.get("direct_v8_stale_receipt_count"), 0.0) or 0.0
    stale_count = max(len(stale_rows), int(supervisor_stale_count))
    return {
        "generated_at": now,
        "status_path": str(status_path),
        "status_updated_at": status.get("updated_at"),
        "status_age_h": source_age_h,
        "cursor": cursor,
        "cursor_iso": _iso(cursor) if cursor else "first direct-V8 digest",
        "rows": rows,
        "stale_rows": stale_rows,
        "stale_receipt_count": stale_count,
        "stale_receipt_max_age_days": MAX_RECEIPT_AGE_SECONDS / 86400.0,
        "since": since,
        "best": best,
        "best_since": best_since,
        "status": status,
        "combiner": status.get("direct_v8_combiner") if isinstance(status.get("direct_v8_combiner"), dict) else {},
        "alerts": status.get("alerts") if isinstance(status.get("alerts"), list) else [],
    }


def _settings(row: dict[str, Any]) -> str:
    overrides = row.get("overrides") if isinstance(row.get("overrides"), dict) else {}
    if not overrides:
        return "LR_BAND_LADDER_ENABLED=true; all other switches=false"
    values = [f"{key}={json.dumps(overrides[key], sort_keys=True)}" for key in sorted(overrides)]
    return "; ".join(values) + "; unspecified switches=false (LR ladder=true)"


def _escape_settings(row: dict[str, Any]) -> str:
    return html.escape(_settings(row))


def _result_row(row: dict[str, Any], *, include_settings: bool = False) -> str:
    symbol = html.escape(str(row.get("requested_symbol") or row.get("symbol") or "?"))
    contract = str(row.get("symbol") or "")
    if contract and contract != str(row.get("requested_symbol") or contract):
        symbol += f" → {html.escape(contract)}"
    side = html.escape(str(row.get("side") or "?"))
    status = "PASS" if row.get("screen_qualified") else "REJECT"
    reason_list = row.get("screen_qualification_reasons") if isinstance(row.get("screen_qualification_reasons"), list) else []
    reason = ", ".join(str(item) for item in reason_list) or "—"
    settings = f"<br><small>{_escape_settings(row)}</small>" if include_settings else ""
    return (
        "<tr>"
        f"<td><b>{symbol} {side}</b>{settings}</td>"
        f"<td>{html.escape(status)}<br><small>{html.escape(reason)}</small></td>"
        f"<td>{_metric(row, 'gain_per_mo_pct')}<br><small>total {_metric(row, 'gain_pct')}</small></td>"
        f"<td>{_metric(row, 'bh_gain_per_mo_pct')}<br><small>total {_metric(row, 'bh_gain_pct')}</small></td>"
        f"<td>{_metric(row, 'delta_vs_bh_per_mo_pct')}<br><small>total {_metric(row, 'delta_vs_bh_pct')}</small></td>"
        f"<td>{_metric(row, 'max_dd_pct')}</td><td>{_metric(row, 'tim_pct')}</td>"
        f"<td>{_metric(row, 'win_rate_pct')}<br><small>{_metric(row, 'closes_per_month', default='—')} closes/mo</small></td>"
        f"<td>{html.escape(str(row.get('combiner_label') or row.get('quantity_route_actual') or row.get('source') or '—'))}</td>"
        "</tr>"
    )


def render_section(snap: dict[str, Any], *, title: str = "DIRECT-V8 RESEARCH RESULTS") -> str:
    rows = snap.get("rows") or []
    since = snap.get("since") or []
    best = snap.get("best") or {}
    combiner = snap.get("combiner") or {}
    alerts = snap.get("alerts") or []
    status_age = snap.get("status_age_h")
    age_text = "unavailable" if status_age is None else f"{status_age:.1f}h old"
    source = html.escape(str(snap.get("status_path") or "unknown"))
    counts = Counter(str(row.get("source") or "unknown") for row in since)
    pass_count = sum(bool(row.get("screen_qualified")) for row in since)
    best_rows = []
    for key in sorted(best):
        best_rows.append(_result_row(best[key], include_settings=True))
    # Include the exact overrides on every newly completed receipt as well as
    # on the best-per-symbol/side table.  This makes the delta section
    # reproducible without opening a run folder or spreadsheet.
    since_rows = [_result_row(row, include_settings=True) for row in sorted(since, key=_row_time, reverse=True)]
    if not best_rows:
        best_rows = ["<tr><td colspan='9'>No audited direct-V8 result has completed yet.</td></tr>"]
    if not since_rows:
        since_rows = ["<tr><td colspan='9'>No new audited direct-V8 receipt since the last digest.</td></tr>"]
    alert_html = "" if not alerts else "<p class='r'><b>Supervisor alerts:</b> " + html.escape(", ".join(str(a.get("kind") or a) for a in alerts[:20])) + "</p>"
    stale_count = int(snap.get("stale_receipt_count") or 0)
    stale_html = (
        f"<p class='r'><b>Current-data warning:</b> {stale_count} audited receipt(s) were excluded because their "
        f"12-month price window ended more than {float(snap.get('stale_receipt_max_age_days') or 7.0):.0f} days ago. "
        "They remain forensic history, not current results.</p>"
        if stale_count else ""
    )
    source_counts = html.escape(", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none")
    return f"""
<h2>{html.escape(title)}</h2>
<p><b>Source:</b> <code>{source}</code> ({age_text}) · <b>Window:</b> 12 months · <b>Since:</b> {html.escape(str(snap.get('cursor_iso') or 'first direct-V8 digest'))}</p>
<p><b>Audited receipts:</b> {len(rows)} total · <b>{len(since)}</b> completed since the last digest ({pass_count} screen-passing) · sources in window: {source_counts}.</p>
<p><b>Combiner:</b> {html.escape(str(combiner.get('stage') or 'unknown'))} · baseline {html.escape(str(combiner.get('baseline_receipts', '—')))}/{html.escape(str(combiner.get('baseline_required', '—')))} · {html.escape(str(combiner.get('combination_audited', '—')))} audited combinations · {html.escape(str(combiner.get('boundary_clearing_receipts', '—')))} boundary-clearing. Legacy vector/SWITCH_MATRIX rows are excluded.</p>
{alert_html}
{stale_html}
<h3>Best audited result currently attained per symbol/side</h3>
<table><tr><th>symbol / side</th><th>screen</th><th>strategy gain/mo</th><th>B&amp;H benchmark/mo</th><th>strategy Δ vs B&amp;H/mo</th><th>DD</th><th>TIM</th><th>WR / closes</th><th>route / combination</th></tr>{''.join(best_rows)}</table>
<h3>Detailed audited results completed since the last digest</h3>
<table><tr><th>symbol / side</th><th>screen</th><th>strategy gain/mo</th><th>B&amp;H benchmark/mo</th><th>strategy Δ vs B&amp;H/mo</th><th>DD</th><th>TIM</th><th>WR / closes</th><th>route / combination</th></tr>{''.join(since_rows)}</table>
<p><small>Every row is a reconciled marked-equity receipt including unrealized P/L. Stock quantities are whole shares; Binance shadow quantities are fractional with the selected cash cap and 0.08% round-trip commission. A displayed REJECT is evidence for the combiner, not a recommendation.</small></p>
"""


def render_plain(snap: dict[str, Any]) -> str:
    rows = snap.get("since") or []
    lines = [
        "DIRECT-V8 RESEARCH RESULTS",
        f"Source: {snap.get('status_path')}",
        f"Since: {snap.get('cursor_iso')}",
        f"Audited current total: {len(snap.get('rows') or [])}; new: {len(rows)}; stale excluded: {int(snap.get('stale_receipt_count') or 0)}",
    ]
    for row in rows:
        lines.append(
            f"{row.get('requested_symbol') or row.get('symbol')} {row.get('side')}: "
            f"strategy gain/mo={_metric(row, 'gain_per_mo_pct')} B&H benchmark/mo={_metric(row, 'bh_gain_per_mo_pct')} "
            f"strategy delta/mo={_metric(row, 'delta_vs_bh_per_mo_pct')} DD={_metric(row, 'max_dd_pct')} "
            f"TIM={_metric(row, 'tim_pct')} settings={_settings(row)}"
        )
    return "\n".join(lines)
