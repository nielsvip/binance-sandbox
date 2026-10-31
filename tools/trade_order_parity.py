"""Compare canonical executed-order ledgers without changing bars or quantities."""
import argparse
import collections
import json
import math
import re
from pathlib import Path

def _number(value, field):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number

def validate_ledger(rows, initial_positions=None):
    positions = dict(initial_positions or {})
    indexed = collections.defaultdict(list)
    errors = []
    previous = {}
    for index, row in enumerate(rows):
        try:
            account = str(row.get("account", ""))
            symbol_side = str(row["sym_side"])
            if not symbol_side.endswith(("_LONG", "_SHORT")):
                raise ValueError("sym_side requires LONG or SHORT")
            bar = _number(row["bar_ts"], "bar_ts")
            if bar % 900 != 0:
                raise ValueError("bar_ts is not an exact 15-minute boundary; timestamps are never rounded")
            action = str(row["action"])
            if action not in {"OPEN", "AUGMENT", "REDUCE", "CLOSE"}:
                raise ValueError("action must identify an executed order, not an attempt or final valuation")
            quantity = _number(row["qty"], "qty")
            after = _number(row["position_after"], "position_after")
            if quantity <= 0 or after < 0:
                raise ValueError("qty must be positive and position_after nonnegative")
            position_key = (account, symbol_side)
            if bar < previous.get(position_key, bar):
                raise ValueError("ledger is not chronological")
            before = float(positions.get(position_key, 0.0))
            expected = before + quantity if action in {"OPEN", "AUGMENT"} else before - quantity
            if action == "OPEN" and before > 1e-9:
                raise ValueError("OPEN on a nonflat position must be classified as AUGMENT")
            if action in {"AUGMENT", "REDUCE", "CLOSE"} and before <= 0:
                raise ValueError("order requires an existing position; supply frozen initial_positions")
            if not math.isclose(after, expected, rel_tol=0, abs_tol=1e-9):
                raise ValueError(f"position conservation failed: before={before}, qty={quantity}, after={after}")
            if action == "CLOSE" and after > 1e-9:
                raise ValueError("CLOSE leaves a nonflat position")
            previous[position_key] = bar
            positions[position_key] = after
            indexed[(account, symbol_side, bar)].append({**row, "qty": quantity, "position_after": after})
        except (KeyError, TypeError, ValueError) as error:
            errors.append({"row": index, "error": str(error), "event": row})
    return indexed, positions, errors

def compare_orders(vector, native, initial_positions=None, quantity_tolerances=None, require_quantity_parity=False):
    vector_index, vector_final, vector_errors = validate_ledger(vector, initial_positions)
    native_index, native_final, native_errors = validate_ledger(native, initial_positions)
    tolerances = quantity_tolerances or {}
    differences = []
    quantity_differences = []
    for key in sorted(set(vector_index) | set(native_index)):
        expected, actual = vector_index.get(key, []), native_index.get(key, [])
        if len(expected) != len(actual):
            differences.append({"key": key, "kind": "ORDER_COUNT", "vector": expected, "native": actual})
            continue
        tolerance = _number(tolerances.get(key[1], 1e-9), "quantity_tolerance")
        if tolerance < 0:
            raise ValueError("quantity_tolerance must be nonnegative")
        for order_index, (left, right) in enumerate(zip(expected, actual)):
            fields = []
            quantity_fields = []
            if left["action"] != right["action"]:
                fields.append("action")
            for field in ("qty", "position_after"):
                if not math.isclose(left[field], right[field], rel_tol=0, abs_tol=tolerance):
                    quantity_fields.append(field)
            if quantity_fields:
                quantity_differences.append({"key": key, "order_index": order_index, "fields": quantity_fields, "vector": left, "native": right})
                if require_quantity_parity:
                    fields.extend(quantity_fields)
            if fields:
                differences.append({"key": key, "order_index": order_index, "kind": "ORDER_FIELDS", "fields": fields, "vector": left, "native": right})
    invalid = vector_errors or native_errors
    verdict = "INVALID" if invalid else "FAIL" if differences else "PASS" if vector or native else "NO_EVENTS"
    return {"verdict": verdict, "criterion": "exact action sequence and bars", "quantity_parity_required": require_quantity_parity, "vector_orders": len(vector), "native_orders": len(native), "timestamp_tolerance_seconds": 0, "quantity_tolerances": tolerances, "differences": differences, "quantity_differences_diagnostic": quantity_differences, "vector_errors": vector_errors, "native_errors": native_errors, "vector_final": {"|".join(k): v for k, v in vector_final.items()}, "native_final": {"|".join(k): v for k, v in native_final.items()}}

def audit_replay_logs(paths):
    findings = []
    counts = collections.Counter()
    for value in paths:
        path = Path(value)
        if not path.is_file():
            findings.append({"file": str(path), "kind": "MISSING_LOG"})
            counts["MISSING_LOG"] += 1
            continue
        if path.stat().st_size == 0:
            findings.append({"file": str(path), "kind": "EMPTY_LOG"})
            counts["EMPTY_LOG"] += 1
            continue
        with path.open(errors="replace") as stream:
            for line_number, line in enumerate(stream, 1):
                kinds = []
                if re.search(r"\bERROR\b|Traceback \(most recent call last\)", line):
                    kinds.append("RUNTIME_ERROR")
                if re.search(r"\bCRITICAL\b", line):
                    kinds.append("CRITICAL_MESSAGE_REQUIRES_REVIEW")
                if "VEC_" in line or "WOULD-BLOCK" in line:
                    kinds.append("VECTOR_DIAGNOSTIC_REQUIRES_REVIEW")
                for kind in kinds:
                    counts[kind] += 1
                    if len(findings) < 100:
                        findings.append({"file": str(path), "line": line_number, "kind": kind, "text": line.rstrip("\n")})
    return {"verdict": "UNREVIEWED" if counts else "PASS" if paths else "NOT_CHECKED", "counts": dict(counts), "first_findings": findings, "source_logs_unchanged": True}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vector", required=True)
    parser.add_argument("--native", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--log", action="append", default=[])
    parser.add_argument("--require-quantity-parity", action="store_true")
    args = parser.parse_args()
    report = compare_orders(json.loads(Path(args.vector).read_text()), json.loads(Path(args.native).read_text()), require_quantity_parity=args.require_quantity_parity)
    report["validation_scope"] = "Supplied executed-order ledgers and logs only; data coverage and all-path completeness are separate requirements"
    report["log_audit"] = audit_replay_logs(args.log)
    report["order_and_log_verdict"] = "PASS" if report["verdict"] == "PASS" and report["log_audit"]["verdict"] == "PASS" else "NOT_ACCEPTED"
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"orders": report["verdict"], "logs": report["log_audit"]["verdict"], "orders_and_logs": report["order_and_log_verdict"]}))
    return 0 if report["order_and_log_verdict"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
