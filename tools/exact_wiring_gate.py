#!/usr/bin/env python3
"""Current-contract two-value wiring gate for the expensive exact matrix lane.

The gate is deliberately per key and per contract fingerprint.  It never mixes
historical rows, opposite sides, vector screens, or stale engine code.  Two
distinct exact values that both reproduce the current baseline are enough to
stop the rest of that key's grid only when:

* the owning master is enabled (or the parameter is itself the master);
* the proposed values are semantically valid;
* threshold values partition an observed NPZ indicator domain when a mapping is
  known; and
* the source read is not hidden behind another account namespace.

Rows are reported RED_RECONNECT / RED_GATED_OFF / RED_BAD_RANGE rather than
being counted as useful exact coverage.  Existing evidence is never deleted.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_tradier import TradierConfig  # noqa: E402
from sweep_value_semantics import (  # noqa: E402
    executable_values,
    semantic_domain,
    validate_test_values,
)


STOCK_SOURCES = (
    "tradier_manage.py",
    "tradier_indicators.py",
    "tradier_positions.py",
    "tradier_entry_contract.py",
    "tradier_augment_gates.py",
    "backtest_v8_engine.py",
)
VALID_STATUSES = (
    "PASS",
    "PASS_WITH_CAPACITY_CLAMPS",
    "INCOMPLETE_NO_REAL_CLOSE",
)
THRESHOLDISH = re.compile(
    r"THRESH|EXTREME|EXHAUST|_MIN(?:_|$)|_MAX(?:_|$)|_LOW(?:_|$)|_HIGH(?:_|$)",
    re.I,
)
FEATURE_TOKENS = (
    ("STOCH", ("stoch_k", "stoch_d")),
    ("CONNORS_RSI", ("connors_rsi",)),
    ("RSI", ("rsi",)),
    ("MFI", ("mfi",)),
    ("ADX", ("adx",)),
    ("MACD", ("macd",)),
    ("ATR", ("atr",)),
    ("WT", ("wt1", "wt2", "wt_vel", "wt_momentum")),
    ("PCTB", ("pctb",)),
    ("PCT_B", ("pct_b",)),
)


def _parse_value(raw: Any, numeric: Any = None) -> Any:
    if numeric is not None:
        return numeric
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


@functools.lru_cache(maxsize=4096)
def read_site_class(param: str) -> dict[str, Any]:
    hits = []
    word = re.compile(rf"\b{re.escape(param)}\b")
    for relative in STOCK_SOURCES:
        path = ROOT / relative
        if not path.exists():
            continue
        for number, line in enumerate(
            path.read_text(errors="ignore").splitlines(), 1
        ):
            if word.search(line) and not line.lstrip().startswith("#"):
                hits.append(
                    {"file": relative, "line": number, "text": line.strip()[:240]}
                )
    if not hits:
        verdict = "NO_STOCK_READ_SITE"
    elif any(
        "_cfg(" in row["text"] or "_psym_get(" in row["text"]
        for row in hits
    ):
        verdict = "PER_KEY_READ"
    elif any(
        "getattr(config" in row["text"]
        or "getattr(self.config" in row["text"]
        for row in hits
    ):
        verdict = "GLOBAL_ONLY_READ"
    else:
        verdict = "READ_SITE_PRESENT"
    return {"class": verdict, "hits": hits[:8]}


def _overlay_value(
    path: Path, key: str, param: str
) -> tuple[bool, Any]:
    try:
        entry = json.loads(path.read_text()).get(key, {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return False, None
    if isinstance(entry, dict) and isinstance(entry.get("overrides"), dict):
        entry = entry["overrides"]
    if isinstance(entry, dict) and param in entry:
        return True, entry[param]
    return False, None


def master_state(
    param: str,
    symbol: str | None = None,
    side: str | None = None,
    account: str = "trb",
) -> dict[str, Any]:
    if param.endswith("_ENABLED"):
        return {
            "master": param,
            "enabled": True,
            "reason": "parameter is its own master switch",
        }
    parts = param.split("_")
    candidates = [
        "_".join(parts[:i]) + "_ENABLED"
        for i in range(len(parts), 0, -1)
    ]
    for candidate in candidates:
        if hasattr(TradierConfig, candidate):
            value = getattr(TradierConfig, candidate)
            source = "TradierConfig default"
            if symbol and side:
                key = f"{symbol}_{side}".upper()
                # Same precedence as tradier_manage._cfg: account overlay,
                # then global accepted per-symbol baseline.
                for path, label in (
                    (
                        ROOT / "data/hourly_reconfig" / account / "active_config.json",
                        f"{account} hourly overlay",
                    ),
                    (
                        ROOT / "data/hourly_reconfig/per_sym_active_config.json",
                        "accepted per-symbol overlay",
                    ),
                ):
                    found, overlay = _overlay_value(path, key, candidate)
                    if found:
                        value, source = overlay, label
                        break
            return {
                "master": candidate,
                "enabled": bool(value),
                "reason": f"longest declared family master from {source}",
            }
    return {
        "master": None,
        "enabled": None,
        "reason": "no declared family master; do not infer a disabled gate",
    }


def _feature_needles(param: str) -> tuple[str, ...]:
    upper = param.upper()
    for token, needles in FEATURE_TOKENS:
        if token in upper:
            return needles
    return ()


@functools.lru_cache(maxsize=1024)
def _partition_observed_domain(
    npz_path: str,
    signature: tuple[int, int],
    needles: tuple[str, ...],
    low: float,
    high: float,
) -> dict[str, Any]:
    del signature
    if not needles:
        return {"status": "UNKNOWN_FEATURE_MAPPING"}
    matched = []
    observed_min = float("inf")
    observed_max = float("-inf")
    partition_count = 0
    with np.load(npz_path, allow_pickle=False) as z:
        for key in z.files:
            lower = key.lower()
            if not any(needle in lower for needle in needles):
                continue
            array = np.asarray(z[key])
            if array.ndim != 1 or array.dtype.kind not in "fiu":
                continue
            finite = np.asarray(array[np.isfinite(array)], dtype=np.float64)
            if not len(finite):
                continue
            matched.append(key)
            observed_min = min(observed_min, float(np.min(finite)))
            observed_max = max(observed_max, float(np.max(finite)))
            partition_count += int(np.count_nonzero((finite > low) & (finite < high)))
    if not matched:
        return {"status": "UNKNOWN_FEATURE_MAPPING"}
    return {
        "status": "BINDING_OBSERVED_DOMAIN" if partition_count else "NONBINDING_OBSERVED_DOMAIN",
        "matched_arrays": matched[:32],
        "observed_min": observed_min,
        "observed_max": observed_max,
        "observations_between_extremes": partition_count,
    }


def range_binding(
    param: str,
    values: list[Any],
    npz_path: Path | None,
) -> dict[str, Any]:
    default = getattr(TradierConfig, param, None)
    validation = validate_test_values(param, default, values)
    executable = executable_values(validation)
    if executable is None:
        return {"status": "INVALID_OR_AMBIGUOUS_RANGE", "validation": validation}
    if isinstance(default, bool):
        return {"status": "BINDING_BY_TYPE", "validation": validation}
    domain = semantic_domain(param, default)
    if domain["kind"] in {
        "nonnegative_integer_count",
        "fraction",
        "bounded_oscillator",
        "normalized_pctb",
    } and not THRESHOLDISH.search(param):
        return {"status": "BINDING_BY_TYPE", "validation": validation}
    if not THRESHOLDISH.search(param):
        return {"status": "BINDING_BY_TYPE", "validation": validation}
    numeric = sorted(
        {float(value) for value in executable if isinstance(value, (int, float))}
    )
    if len(numeric) < 2 or npz_path is None or not npz_path.exists():
        return {"status": "UNKNOWN_FEATURE_MAPPING", "validation": validation}
    stat = npz_path.stat()
    observed = _partition_observed_domain(
        str(npz_path),
        (stat.st_size, stat.st_mtime_ns),
        _feature_needles(param),
        numeric[0],
        numeric[-1],
    )
    return {**observed, "validation": validation}


def rows_for_key(
    con: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    campaign: str,
    contract_fingerprint: str,
) -> tuple[str | None, dict[str, list[dict[str, Any]]]]:
    baseline = con.execute(
        "SELECT trades_fingerprint FROM key_baseline "
        "WHERE mode='tradier' AND campaign=? AND symbol=? AND side=? "
        "AND contract_fingerprint=? AND validation_status IN (?,?,?)",
        (campaign, symbol, side, contract_fingerprint, *VALID_STATUSES),
    ).fetchone()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    query = (
        "SELECT param,value_json,value_num,trades_fingerprint,inert,"
        "validation_status FROM param_cells WHERE mode='tradier' "
        "AND campaign=? AND symbol=? AND side=? AND tier='ENGINE' "
        "AND contract_fingerprint=? AND validation_status IN (?,?,?)"
    )
    for row in con.execute(
        query,
        (campaign, symbol, side, contract_fingerprint, *VALID_STATUSES),
    ):
        grouped[row[0]].append(
            {
                "value": _parse_value(row[1], row[2]),
                "value_json": row[1],
                "fingerprint": row[3],
                "inert": bool(row[4]),
                "validation_status": row[5],
            }
        )
    return (baseline[0] if baseline else None), grouped


def classify_param(
    param: str,
    rows: list[dict[str, Any]],
    baseline_fingerprint: str | None,
    npz_path: Path | None,
    *,
    symbol: str | None = None,
    side: str | None = None,
) -> dict[str, Any]:
    by_value = {}
    for row in rows:
        by_value[str(row["value_json"])] = row
    values = [row["value"] for row in by_value.values()]
    fingerprints = {
        row["fingerprint"] for row in by_value.values() if row["fingerprint"]
    }
    source = read_site_class(param)
    master = master_state(param, symbol, side)
    default = getattr(TradierConfig, param, object())
    binding_values = list(values)
    if isinstance(default, bool) and default not in binding_values:
        # The accepted baseline is the exact default-value pass for a boolean.
        # Include that semantic value in range/type validation without
        # fabricating a second param_cells row.
        binding_values.append(default)
    binding = range_binding(param, binding_values, npz_path) if values else {
        "status": "NO_VALUES"
    }
    nondefault = [value for value in values if value != default]
    required = 1 if isinstance(default, bool) else 2

    if not master["enabled"] and master["enabled"] is not None:
        verdict = "RED_GATED_OFF"
        reason = "family master is disabled; field-by-field exact values cannot bind"
    elif len(set(map(repr, nondefault))) < required:
        verdict = "NEEDS_TWO_VALUE_SMOKE"
        reason = f"needs {required} distinct non-default exact value(s)"
    elif len(fingerprints) == 1 and baseline_fingerprint in fingerprints:
        if source["class"] in {"NO_STOCK_READ_SITE", "GLOBAL_ONLY_READ"}:
            verdict = "RED_RECONNECT"
            reason = f"{source['class']} and exact fingerprints reproduce baseline"
        elif binding["status"] in {"BINDING_BY_TYPE", "BINDING_OBSERVED_DOMAIN"}:
            verdict = "RED_RECONNECT"
            reason = "semantically binding exact values both reproduce baseline"
        else:
            verdict = "RED_BAD_RANGE"
            reason = "identical exact fingerprints but range does not prove a binding perturbation"
    elif (
        isinstance(default, bool)
        and baseline_fingerprint
        and len(fingerprints) == 1
    ):
        # A boolean has only one non-default exact value.  Its second pass is
        # the accepted baseline, deliberately not re-executed as a duplicate
        # cell.  A fingerprint different from that baseline proves wiring; it
        # is not a one-value numeric degeneracy.
        verdict = "WIRED_DIFFERENT"
        reason = "non-default boolean produced a fingerprint different from baseline"
    elif len(fingerprints) == 1:
        if binding["status"] in {"BINDING_BY_TYPE", "BINDING_OBSERVED_DOMAIN"}:
            verdict = "RED_DEGENERATE"
            reason = "values changed baseline once but are identical to each other"
        else:
            verdict = "RED_BAD_RANGE"
            reason = "same-value fingerprint with an unproven/nonbinding range"
    else:
        verdict = "WIRED_DIFFERENT"
        reason = "exact values produced different trade fingerprints"
    return {
        "verdict": verdict,
        "skip_remaining_exact": verdict.startswith("RED_"),
        "reason": reason,
        "tested_values": values,
        "distinct_fingerprints": sorted(fingerprints),
        "baseline_fingerprint": baseline_fingerprint,
        "source": source,
        "master": master,
        "range_binding": binding,
    }


def audit_key(
    con: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    campaign: str,
    contract_fingerprint: str,
    npz_path: Path | None,
) -> dict[str, Any]:
    baseline, grouped = rows_for_key(
        con,
        symbol=symbol,
        side=side,
        campaign=campaign,
        contract_fingerprint=contract_fingerprint,
    )
    params = {
        param: classify_param(
            param,
            rows,
            baseline,
            npz_path,
            symbol=symbol,
            side=side,
        )
        for param, rows in sorted(grouped.items())
    }
    counts: dict[str, int] = defaultdict(int)
    for row in params.values():
        counts[row["verdict"]] += 1
    return {
        "symbol": symbol,
        "side": side,
        "campaign": campaign,
        "contract_fingerprint": contract_fingerprint,
        "baseline_fingerprint": baseline,
        "counts": dict(sorted(counts.items())),
        "params": params,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data/param_results_stocks.db")
    parser.add_argument("--symbols", default="MU,TTD,ACN")
    parser.add_argument("--sides", default="LONG,SHORT")
    parser.add_argument(
        "--keys",
        default="",
        help="explicit comma-separated SYMBOL_SIDE keys; avoids a Cartesian product",
    )
    parser.add_argument("--campaign", default="stocks_repaired_20260725_c2")
    parser.add_argument(
        "--npz-dir",
        type=Path,
        default=ROOT / "data/matrix_npz/stocks_repaired_20260725_c2",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data/reports/EXACT_WIRING_GATES.json",
    )
    parser.add_argument(
        "--use-latest-row-fingerprint",
        action="store_true",
        help="historical audit only: bind to the newest exact row fingerprint instead of current code",
    )
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "tools"))
    import persym_baseline_campaign as psc

    con = sqlite3.connect(str(args.db))
    results = {}
    if args.keys:
        requested = [
            tuple(value.strip().upper().rsplit("_", 1))
            for value in args.keys.split(",")
            if value.strip()
        ]
    else:
        requested = [
            (symbol, side)
            for symbol in [
                x.strip().upper() for x in args.symbols.split(",") if x.strip()
            ]
            for side in [
                x.strip().upper() for x in args.sides.split(",") if x.strip()
            ]
        ]
    for symbol, side in requested:
        key = f"{symbol}_{side}"
        if args.use_latest_row_fingerprint:
            row = con.execute(
                "SELECT contract_fingerprint FROM param_cells "
                "WHERE mode='tradier' AND campaign=? AND symbol=? AND side=? "
                "AND tier='ENGINE' AND contract_fingerprint IS NOT NULL "
                "ORDER BY ts DESC LIMIT 1",
                (args.campaign, symbol, side),
            ).fetchone()
            fp = row[0] if row else psc.matrix_contract_fingerprint(symbol, side)
        else:
            fp = psc.matrix_contract_fingerprint(symbol, side)
        results[key] = audit_key(
            con,
            symbol=symbol,
            side=side,
            campaign=args.campaign,
            contract_fingerprint=fp,
            npz_path=args.npz_dir / f"{symbol}.npz",
        )
    con.close()
    payload = {
        "contract": "CURRENT_EXACT_TWO_VALUE_WIRING_GATE_V1",
        "campaign": args.campaign,
        "keys": results,
        "historical_or_stale_rows_accepted": bool(
            args.use_latest_row_fingerprint
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(f".{args.out.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, args.out)
    print(json.dumps({key: row["counts"] for key, row in results.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
