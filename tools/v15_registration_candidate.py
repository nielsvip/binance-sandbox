from __future__ import annotations

import math
import re

SWITCH_SHEETS = (
    "STDEV_SLOPE_SIZING",
    "ENTRY_REVERSAL_BOUNCE",
    "ENTRY_BREAKOUT_CHANNEL",
    "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL",
    "EXIT_VELOCITY",
    "REENTRY_WINDOWED",
    "REENTRY_ADAPTIVE",
    "AUGMENT_TREND",
    "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK",
    "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
)


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    try:
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-12)
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _parse_pair(part):
    if not isinstance(part, str) or "=" not in part:
        return None
    key, raw = part.split("=", 1)
    key = key.strip()
    raw = raw.strip()
    if not key or not raw:
        return None
    return key, raw


def _typed(raw, default):
    low = raw.strip().lower()
    if isinstance(default, bool):
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            f = float(raw)
            if f.is_integer():
                return int(f)
        except ValueError:
            pass
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            pass
    return raw


def _row_parts(ws, row, c_col):
    if row is None or int(row) < 3 or int(row) > ws.max_row:
        return []
    value = ws.cell(int(row), c_col).value
    if not isinstance(value, str):
        return []
    return [p.strip() for p in value.split(" + ") if p.strip()]


def _promoted_pairs(progress, wb, cumulative, defaults):
    done = progress.get("done") or {}
    pairs = {}
    errors = []
    for done_key, rec in done.items():
        if not isinstance(rec, dict) or not rec.get("promoted"):
            continue
        source = []
        if isinstance(rec.get("parts"), list):
            source.extend(str(p).strip() for p in rec["parts"] if str(p).strip())
        else:
            switch = rec.get("switch")
            cand = rec.get("cand")
            if not switch or cand is None:
                try:
                    tail = str(done_key).split("!", 1)[1].split(":", 1)[1]
                    switch, cand = tail.split("=", 1)
                except Exception:
                    errors.append(f"{done_key}: promoted record has no switch/candidate provenance")
                    continue
            source.append(f"{switch}={cand}")
            source.extend(str(x).strip() for x in rec.get("promoted_filters") or [] if str(x).strip())
            dep = (rec.get("dep_forced") or {}).get("promoted") or {}
            source.extend(f"{k}={v}" for k, v in dep.items())
        source = list(dict.fromkeys(source))
        tab = rec.get("tab")
        row = rec.get("row")
        if (tab is None or row is None) and not str(rec.get("promoted_how") or "").startswith("v18_winners"):
            try:
                tab, tail = str(done_key).split("!", 1)
                row = int(tail.split(":", 1)[0])
            except Exception:
                pass
        has_row_proof = bool(tab in wb.sheetnames and row is not None and int(row) >= 3)
        row_parts = _row_parts(wb[tab], row, 3) if has_row_proof else []
        winner_proof = False
        if not has_row_proof and str(rec.get("promoted_how") or "").startswith("v18_winners"):
            winner_proof = _adopted_winner_proof(wb, source)
        for part in source:
            parsed = _parse_pair(part)
            if not parsed:
                errors.append(f"{done_key}: malformed promoted part {part!r}")
                continue
            key, raw = parsed
            if key not in cumulative:
                errors.append(f"{done_key}: promoted key {key} absent from final cumulative set")
                continue
            if key not in defaults:
                errors.append(f"{done_key}: promoted key {key} absent from config defaults")
                continue
            typed = _typed(raw, defaults.get(key))
            if not _same(cumulative[key], typed):
                continue  # later promotion superseded this historical value
            if not has_row_proof and not winner_proof:
                errors.append(f"{done_key}: final promoted value {key}={raw} has no workbook-row/adopted-winner proof")
                continue
            if has_row_proof and not any(_parse_pair(p) == (key, raw) for p in row_parts):
                errors.append(f"{done_key}: final promoted value {key}={raw} absent from {tab}!{row} C cell")
                continue
            pairs[key] = cumulative[key]
    return pairs, errors


def _adopted_winner_proof(wb, expected_parts):
    if "WINNERS" not in wb.sheetnames:
        return False
    wanted = {_parse_pair(p) for p in expected_parts}
    wanted.discard(None)
    ws = wb["WINNERS"]
    headers = {str(ws.cell(1, c).value or "").strip().lower(): c for c in range(1, ws.max_column + 1)}
    candidate_col = headers.get("candidate", 2)
    adopted_col = headers.get("adopted", 8)
    for row in range(2, ws.max_row + 1):
        candidate = ws.cell(row, candidate_col).value
        adopted = ws.cell(row, adopted_col).value
        if adopted is not True or not isinstance(candidate, str):
            continue
        if all(f"{key}={raw}" in candidate for key, raw in wanted):
            return True
    return False


def build_registration_candidate(wb, progress, cumulative_overrides, defaults, prior_overrides):
    """Build the registration set from applicable prior per-symbol keys plus proved final board promotions.

    Refuses on missing provenance, stale C values for a still-winning setting, or an unaccounted final delta.
    The returned mapping is passed through switch_parity's existing type and post-write gates.
    """
    cumulative = dict(cumulative_overrides or {})
    defaults = dict(defaults or {})
    prior = dict(prior_overrides or {})
    allowed = set()
    for sheet_name in SWITCH_SHEETS:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        for row in range(3, ws.max_row + 1):
            value = ws.cell(row, 1).value
            if isinstance(value, str) and value.strip():
                allowed.add(value.strip())
        for col in range(12, ws.max_column + 1):
            header = ws.cell(2, col).value
            if isinstance(header, str) and "=" in header and not header.upper().startswith("WHAT SWITCH"):
                allowed.add(header.split("=", 1)[0].strip())
    promotions, errors = _promoted_pairs(progress or {}, wb, cumulative, defaults)
    c_values = {}
    for sheet_name in SWITCH_SHEETS:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        for row in range(3, ws.max_row + 1):
            value = ws.cell(row, 3).value
            if not isinstance(value, str):
                continue
            for part in value.split(" + "):
                parsed = _parse_pair(part.strip())
                if not parsed:
                    continue
                key, raw = parsed
                if key not in allowed:
                    errors.append(f"{sheet_name}!{row} C contains non-board key {key}")
                    continue
                if key not in defaults or key not in cumulative:
                    errors.append(f"{sheet_name}!{row} C key {key} is absent from final/config set")
                    continue
                typed = _typed(raw, defaults.get(key))
                if not _same(typed, cumulative[key]):
                    errors.append(f"{sheet_name}!{row} C value {key}={raw} differs from final cumulative value {cumulative[key]!r}")
                    continue
                c_values[key] = cumulative[key]
    candidate = {}
    unsupported_prior = []
    for key, value in prior.items():
        if key not in allowed:
            continue
        if key not in defaults:
            unsupported_prior.append(key)
            continue
        candidate[key] = value
    candidate.update(c_values)
    candidate.update(promotions)
    initial = dict((progress or {}).get("initial_overrides") or {})
    for key, value in cumulative.items():
        if key not in allowed or key not in initial or _same(value, initial[key]):
            continue
        if key not in candidate or not _same(candidate[key], value):
            errors.append(f"final changed board key {key}={value!r} lacks current promotion provenance")
    return candidate, {"ok": not errors, "errors": errors, "n_prior_applicable": sum(k in allowed and k in defaults for k in prior), "unsupported_prior": unsupported_prior, "n_promoted_final": len(promotions), "n_board_c": len(c_values), "n_candidate": len(candidate), "n_board_keys": len(allowed)}
