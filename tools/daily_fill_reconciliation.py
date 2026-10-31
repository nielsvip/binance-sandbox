"""Exact chart-marker coverage against immutable account fill history."""

from __future__ import annotations

import datetime as dt
import math


VECTOR_DIRECT_SOURCES = frozenset(("S1_VECTOR_DIRECT", "S5_VECTOR_DIRECT"))
BAR_SECONDS = 900.0
QTY_PLACES = 8
PRICE_PLACES = 10
TIME_TOLERANCE_S = 1.0


def _scope(account, symside):
    return str(account or "").strip().lower(), str(symside or "").strip().upper()


def _timestamp(value):
    if value is None:
        return None
    try:
        number = float(value)
        if math.isfinite(number):
            return number / 1000.0 if number > 1e11 else number
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _action(value):
    action = str(value or "").strip().upper()
    if action.startswith("CLOSE ") or action.startswith("CLOSE("):
        return "CLOSE"
    return action


def _payload(row, source, account, symside):
    order_id = str(row.get("order_id") or "").strip()
    action = _action(row.get("type") if source == "history" else row.get("action"))
    timestamp = _timestamp(row.get("ts") if source == "history" else row.get("time"))
    try:
        qty = abs(float(row.get("qty")))
        price = float(row.get("price"))
    except (TypeError, ValueError):
        return None, f"{source} row has invalid qty/price"
    if not order_id:
        return None, f"{source} row has no broker order_id"
    if action not in ("OPEN", "AUGMENT", "REDUCE", "CLOSE"):
        return None, f"{source} row has unsupported action {action or '<blank>'}"
    if timestamp is None or not math.isfinite(qty) or not math.isfinite(price) or qty <= 0 or price <= 0:
        return None, f"{source} row has invalid fill time/qty/price"
    return {"account": account, "sym_side": symside, "order_id": order_id, "action": action, "timestamp": timestamp, "qty": round(qty, QTY_PLACES), "price": round(price, PRICE_PLACES)}, None


def _coverage_ok(coverage, start_ts, end_ts):
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        return False
    start = _timestamp(coverage.get("start_ts"))
    end = _timestamp(coverage.get("end_ts"))
    return start is not None and end is not None and start <= start_ts and end >= end_ts


def reconcile_fill_markers(expected_scopes, history_rows, chart_rows, coverage, start_ts, end_ts):
    """Verify all account fills are represented by chart markers, without claiming decision parity.

    `expected_scopes` is an explicit iterable of `{account, sym_side}` rows, including accounts with zero fills.
    Both row lists need those same account/sym_side fields. Every scope needs `coverage[(account, sym_side)]`
    with complete=True and a source interval covering the whole chart window; absent coverage is UNVERIFIED.
    """
    start, end = _timestamp(start_ts), _timestamp(end_ts)
    if start is None or end is None or end < start:
        raise ValueError("invalid reconciliation window")
    scopes = []
    seen_scopes = set()
    for item in expected_scopes:
        account, symside = _scope(item.get("account"), item.get("sym_side"))
        if not account or not symside:
            raise ValueError("expected scope requires account and sym_side")
        key = (account, symside)
        if key in seen_scopes:
            raise ValueError(f"duplicate expected scope: {account}/{symside}")
        seen_scopes.add(key)
        scopes.append(key)
    if not scopes:
        raise ValueError("at least one expected account/sym_side scope is required")

    indexed = {"history": {key: {} for key in scopes}, "chart": {key: {} for key in scopes}}
    issues = {key: [] for key in scopes}
    duplicates = {key: {"history": [], "chart": []} for key in scopes}
    unscoped_issues = []
    expected_sides = {symside for _account, symside in scopes}
    for source, rows in (("history", history_rows), ("chart", chart_rows)):
        for row in rows:
            if not isinstance(row, dict):
                unscoped_issues.append(f"{source} contains a non-object row")
                continue
            account, symside = _scope(row.get("account") or row.get("acct"), row.get("sym_side") or row.get("ss"))
            key = (account, symside)
            if not symside:
                unscoped_issues.append(f"{source} row has no sym_side metadata")
                continue
            if symside not in expected_sides:
                continue
            if not account:
                for scoped in scopes:
                    if scoped[1] == symside:
                        issues[scoped].append(f"{source} row has no account metadata")
                continue
            if key not in seen_scopes:
                unscoped_issues.append(f"{source} row belongs to undeclared scope {account}/{symside}")
                continue
            timestamp = _timestamp(row.get("ts") if source == "history" else row.get("time"))
            if timestamp is None:
                issues[key].append(f"{source} row has invalid fill timestamp")
                continue
            if not start <= timestamp <= end:
                continue
            payload, error = _payload(row, source, *key)
            if error:
                issues[key].append(error)
                continue
            order_id = payload["order_id"]
            if order_id in indexed[source][key]:
                duplicates[key][source].append(order_id)
            else:
                indexed[source][key][order_id] = payload
            if source == "history":
                provenance = row.get("prov")
                origin = str(provenance.get("src") or "") if isinstance(provenance, dict) else ""
                if origin not in VECTOR_DIRECT_SOURCES:
                    issues[key].append(f"history order {order_id} has unknown/non-vector_direct provenance {origin or '<missing>'}")

    results = []
    for account, symside in scopes:
        key = (account, symside)
        hist = indexed["history"][key]
        chart = indexed["chart"][key]
        missing = sorted(set(hist) - set(chart))
        extra = sorted(set(chart) - set(hist))
        changed = []
        for order_id in sorted(set(hist) & set(chart)):
            left, right = hist[order_id], chart[order_id]
            differences = []
            for field in ("action", "qty", "price"):
                if left[field] != right[field]:
                    differences.append(field)
            if abs(left["timestamp"] - right["timestamp"]) > TIME_TOLERANCE_S:
                differences.append("timestamp")
            if differences:
                changed.append({"order_id": order_id, "fields": differences})
        cov_ok = _coverage_ok(coverage.get(key) or coverage.get(f"{account}|{symside}"), start, end) if isinstance(coverage, dict) else False
        exact = cov_ok and not issues[key] and not duplicates[key]["history"] and not duplicates[key]["chart"] and not missing and not extra and not changed
        status = "MATCH_100_FILL_CAPTURE" if exact else "FAIL" if cov_ok and not issues[key] else "UNVERIFIED"
        results.append({"account": account, "sym_side": symside, "status": status, "coverage_verified": cov_ok,
                        "history_fills": len(hist), "chart_fills": len(chart), "matched_fills": len(set(hist) & set(chart)) - len(changed),
                        "missing_chart_order_ids": missing, "extra_chart_order_ids": extra,
                        "duplicate_history_order_ids": sorted(set(duplicates[key]["history"])),
                        "duplicate_chart_order_ids": sorted(set(duplicates[key]["chart"])),
                        "changed_fills": changed, "issues": sorted(set(issues[key]))})
    overall = "UNVERIFIED" if unscoped_issues or any(row["status"] == "UNVERIFIED" for row in results) else "FAIL" if any(row["status"] == "FAIL" for row in results) else "MATCH_100_FILL_CAPTURE"
    return {"status": overall, "scope": "IMMUTABLE_ACTUAL_FILL_MARKER_COVERAGE_ONLY_NOT_DECISION_PARITY",
            "window": {"start_ts": start, "end_ts": end}, "expected_scopes": len(results),
            "verified_scopes": sum(row["coverage_verified"] for row in results),
            "history_fills": sum(row["history_fills"] for row in results), "issues": sorted(set(unscoped_issues)),
            "chart_fills": sum(row["chart_fills"] for row in results), "scopes": results}
