#!/usr/bin/env python3
"""Fail-closed lineage audit for Tradier source bars and versioned NPZs.

This is deliberately read-only.  It answers three questions before a symbol is
regenerated or admitted to research:

1. Do the available raw 5m/15m bars cover the requested history?
2. Does each NPZ preserve authentic bars and causal synthetic-parent metadata?
3. Are two NPZs equivalent on their overlapping rows, or are they different
   datasets that must not share one result fingerprint?
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


OHLCV = ("open", "high", "low", "close", "volume")
AUDIT_FIELDS = tuple(
    f"{stem}_{tf}"
    for tf in ("5m", "15m", "1h", "4h", "D")
    for stem in ("close", "wt1", "stoch_k", "dc_position", "lrL_pct_b", "lrL_slope")
)
LINEAGE_FIELDS = {
    "timestamps",
    "synthetic_5m",
    "synthetic_5m_parent_close_ts",
    *(f"{name}_5m" for name in OHLCV),
    *(f"timestamp_{tf}" for tf in ("15m", "1h", "4h", "D")),
    *AUDIT_FIELDS,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _epoch(value: str) -> int:
    return int(
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .timestamp()
    )


def _iso(value: int) -> str:
    return datetime.fromtimestamp(int(value), timezone.utc).isoformat().replace("+00:00", "Z")


def load_source(path: Path) -> dict[str, np.ndarray]:
    rows = json.loads(path.read_text())
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("series") or rows.get("bars") or []
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a JSON row list")
    values: dict[str, list[Any]] = {"timestamps": []}
    values.update({name: [] for name in OHLCV})
    for row in rows:
        stamp = row.get("timestamp", row.get("time"))
        if stamp is None:
            continue
        values["timestamps"].append(_epoch(str(stamp)))
        for name in OHLCV:
            values[name].append(float(row[name]))
    return {
        name: np.asarray(items, dtype=np.int64 if name == "timestamps" else np.float64)
        for name, items in values.items()
    }


def source_stats(path: Path, arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    ts = arrays["timestamps"]
    close = arrays["close"]
    order = np.argsort(ts, kind="stable")
    ts = ts[order]
    close = close[order]
    gaps = np.diff(ts)
    returns = np.abs(np.diff(close) / np.maximum(np.abs(close[:-1]), 1e-12))
    material = np.flatnonzero(gaps > 7 * 86400)
    split_like = np.flatnonzero(
        (gaps <= 7 * 86400)
        & (
            np.isclose(close[1:] / np.maximum(close[:-1], 1e-12), 0.5, rtol=0.08)
            | np.isclose(close[1:] / np.maximum(close[:-1], 1e-12), 2.0, rtol=0.08)
        )
    )
    invalid_ohlc = (
        (arrays["high"] < np.maximum.reduce([arrays["open"], arrays["close"], arrays["low"]]))
        | (arrays["low"] > np.minimum.reduce([arrays["open"], arrays["close"], arrays["high"]]))
    )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "rows": int(len(ts)),
        "start": _iso(ts[0]) if len(ts) else None,
        "end": _iso(ts[-1]) if len(ts) else None,
        "duplicate_timestamps": int(len(ts) - len(np.unique(ts))),
        "strictly_increasing": bool(np.all(np.diff(ts) > 0)),
        "finite_ohlcv_pct": float(
            np.mean(np.column_stack([np.isfinite(arrays[name]) for name in OHLCV]))
            * 100.0
        ),
        "invalid_ohlc_rows": int(np.sum(invalid_ohlc)),
        "negative_volume_rows": int(np.sum(arrays["volume"] < 0)),
        "max_adjacent_close_jump_pct": float(np.max(returns) * 100.0) if len(returns) else 0.0,
        "split_like_events": [
            {
                "previous_timestamp": _iso(ts[i]),
                "timestamp": _iso(ts[i + 1]),
                "ratio": float(close[i + 1] / close[i]),
            }
            for i in split_like
        ],
        "material_gaps_gt_7d": [
            {
                "previous_timestamp": _iso(ts[i]),
                "timestamp": _iso(ts[i + 1]),
                "gap_seconds": int(gaps[i]),
                "gap_hours": float(gaps[i] / 3600.0),
                "previous_close": float(close[i]),
                "next_close": float(close[i + 1]),
                "cross_gap_return_pct": float((close[i + 1] / close[i] - 1.0) * 100.0),
            }
            for i in material
        ],
    }


def _array_hash(arr: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(arr)
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode())
    digest.update(str(contiguous.shape).encode())
    if contiguous.dtype.kind == "O":
        # Generated NPZs contain a few string-state arrays stored as object.
        # Canonicalize their scalar text rather than hashing pickle bytes.
        for item in contiguous.reshape(-1):
            encoded = str(item).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    else:
        digest.update(contiguous.view(np.uint8))
    return digest.hexdigest()


def _coverage(arr: np.ndarray) -> dict[str, Any]:
    values = np.asarray(arr)
    if values.dtype.kind not in "biufc":
        return {"dtype": str(values.dtype), "unique": int(len(np.unique(values)))}
    finite = np.isfinite(values)
    return {
        "dtype": str(values.dtype),
        "finite_pct": float(finite.mean() * 100.0) if len(values) else 0.0,
        "nonzero_pct": float(np.mean(finite & (np.abs(values) > 1e-8)) * 100.0)
        if len(values)
        else 0.0,
        "unique": int(len(np.unique(values[finite]))) if finite.any() else 0,
    }


def _native_source_parity(
    z: Any,
    source_5m: dict[str, np.ndarray],
    tolerance: float,
) -> dict[str, Any]:
    npz_ts = np.asarray(z["timestamps"], dtype=np.int64)
    synthetic = (
        np.asarray(z["synthetic_5m"], dtype=np.int8).astype(bool)
        if "synthetic_5m" in z.files
        else np.zeros(len(npz_ts), dtype=bool)
    )
    source_lookup = {int(ts): i for i, ts in enumerate(source_5m["timestamps"])}
    npz_lookup = {int(ts): i for i, ts in enumerate(npz_ts)}
    shared = sorted(set(source_lookup) & set(npz_lookup))
    authentic_shared = [ts for ts in shared if not synthetic[npz_lookup[ts]]]
    fields: dict[str, Any] = {}
    for name in OHLCV:
        npz_name = f"{name}_5m"
        if npz_name not in z.files:
            fields[name] = {"missing_npz_field": npz_name}
            continue
        npz_array = np.asarray(z[npz_name])
        source_values = np.asarray(
            [source_5m[name][source_lookup[ts]] for ts in authentic_shared], dtype=np.float64
        )
        npz_values = np.asarray(
            [npz_array[npz_lookup[ts]] for ts in authentic_shared], dtype=np.float64
        )
        delta = np.abs(source_values - npz_values)
        fields[name] = {
            "rows": int(len(delta)),
            "mismatch_rows": int(
                np.sum(
                    ~np.isclose(
                        source_values,
                        npz_values,
                        rtol=1e-6,
                        atol=tolerance,
                        equal_nan=True,
                    )
                )
            ),
            "max_abs_delta": float(np.max(delta)) if len(delta) else 0.0,
        }
    source_missing = sorted(set(source_lookup) - set(npz_lookup))
    source_marked_synthetic = sorted(
        ts for ts in shared if synthetic[npz_lookup[ts]]
    )
    return {
        "source_rows": int(len(source_lookup)),
        "shared_rows": int(len(shared)),
        "authentic_shared_rows": int(len(authentic_shared)),
        "source_rows_missing_from_npz": int(len(source_missing)),
        "first_missing_source_timestamp": _iso(source_missing[0]) if source_missing else None,
        "source_rows_marked_synthetic": int(len(source_marked_synthetic)),
        "fields": fields,
    }


def _parent_clock_stats(z: Any) -> dict[str, Any]:
    ts = np.asarray(z["timestamps"], dtype=np.int64)
    synthetic = (
        np.asarray(z["synthetic_5m"], dtype=np.int8).astype(bool)
        if "synthetic_5m" in z.files
        else np.zeros(len(ts), dtype=bool)
    )
    if "synthetic_5m_parent_close_ts" not in z.files:
        return {
            "field_present": False,
            "synthetic_rows": int(synthetic.sum()),
            "valid": False if synthetic.any() else True,
        }
    parent = np.asarray(z["synthetic_5m_parent_close_ts"], dtype=np.int64)
    expected = ((ts + 899) // 900) * 900
    lags = parent[synthetic] - ts[synthetic]
    return {
        "field_present": True,
        "synthetic_rows": int(synthetic.sum()),
        "mismatch_to_ceil_15m_rows": int(np.sum(parent[synthetic] != expected[synthetic])),
        "lag_counts_seconds": {
            str(int(value)): int(count)
            for value, count in zip(*np.unique(lags, return_counts=True))
        },
        "native_parent_equals_timestamp_rows": int(np.sum(parent[~synthetic] == ts[~synthetic])),
        "native_rows": int((~synthetic).sum()),
        # The field's contract applies to synthetic rows. Research execution
        # always uses the base timestamp for native rows, regardless of any
        # placeholder value stored in this synthetic-only provenance field.
        "valid": bool(
            len(parent) == len(ts)
            and np.all(parent[synthetic] == expected[synthetic])
        ),
    }


def npz_stats(
    path: Path,
    source_5m: dict[str, np.ndarray],
    tolerance: float,
) -> dict[str, Any]:
    # Inputs are locally generated research artifacts. Object/string state
    # arrays are canonicalized by _array_hash rather than compared as pickles.
    with np.load(path, allow_pickle=True) as z:
        ts = np.asarray(z["timestamps"], dtype=np.int64)
        close = np.asarray(z["close"], dtype=np.float64)
        jumps = np.abs(np.diff(close) / np.maximum(np.abs(close[:-1]), 1e-12))
        htf: dict[str, Any] = {}
        for tf in ("15m", "1h", "4h", "D"):
            name = f"timestamp_{tf}"
            if name not in z.files:
                htf[tf] = {"field_present": False}
                continue
            source_ts = np.asarray(z[name], dtype=np.int64)
            htf[tf] = {
                "field_present": True,
                "future_rows": int(np.sum(source_ts > ts)),
                "unique_completed_sources": int(len(np.unique(source_ts))),
                "monotone": bool(np.all(np.diff(source_ts) >= 0)),
            }
        fields = {
            name: _coverage(np.asarray(z[name]))
            for name in AUDIT_FIELDS
            if name in z.files
        }
        missing = [name for name in AUDIT_FIELDS if name not in z.files]
        return {
            "path": str(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "rows": int(len(ts)),
            "keys": int(len(z.files)),
            "start": _iso(ts[0]),
            "end": _iso(ts[-1]),
            "duplicate_timestamps": int(len(ts) - len(np.unique(ts))),
            "strictly_increasing": bool(np.all(np.diff(ts) > 0)),
            "max_adjacent_close_jump_pct": float(np.max(jumps) * 100.0) if len(jumps) else 0.0,
            "synthetic_rows": int(np.sum(z["synthetic_5m"]))
            if "synthetic_5m" in z.files
            else None,
            "parent_clock": _parent_clock_stats(z),
            "completed_htf": htf,
            "audit_fields": fields,
            "missing_audit_fields": missing,
            "native_source_parity": _native_source_parity(z, source_5m, tolerance),
            "array_hashes": {name: _array_hash(np.asarray(z[name])) for name in z.files},
        }


def compare_npzs(
    left_path: Path,
    right_path: Path,
    tolerance: float,
    left_hashes: dict[str, str] | None = None,
    right_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    with np.load(left_path, allow_pickle=True) as left, np.load(
        right_path, allow_pickle=True
    ) as right:
        left_ts = np.asarray(left["timestamps"], dtype=np.int64)
        right_ts = np.asarray(right["timestamps"], dtype=np.int64)
        left_lookup = {int(ts): i for i, ts in enumerate(left_ts)}
        right_lookup = {int(ts): i for i, ts in enumerate(right_ts)}
        shared_ts = sorted(set(left_lookup) & set(right_lookup))
        li = np.asarray([left_lookup[ts] for ts in shared_ts], dtype=np.int64)
        ri = np.asarray([right_lookup[ts] for ts in shared_ts], dtype=np.int64)
        shared_fields = sorted(set(left.files) & set(right.files))
        identical_rows = np.array_equal(left_ts, right_ts)
        if identical_rows and left_hashes is not None and right_hashes is not None:
            differing_hashes = {
                name: {
                    "left_hash": left_hashes[name],
                    "right_hash": right_hashes[name],
                }
                for name in shared_fields
                if left_hashes[name] != right_hashes[name]
            }
            return {
                "left": str(left_path),
                "right": str(right_path),
                "shared_rows": int(len(shared_ts)),
                "left_only_rows": 0,
                "right_only_rows": 0,
                "shared_fields": int(len(shared_fields)),
                "compared_fields": int(len(shared_fields)),
                "comparison_scope": "all_array_hashes_identical_rows",
                "left_only_fields": sorted(set(left.files) - set(right.files)),
                "right_only_fields": sorted(set(right.files) - set(left.files)),
                "differing_shared_fields": differing_hashes,
                "equivalent_on_overlap": not differing_hashes,
            }
        compare_fields = [name for name in shared_fields if name in LINEAGE_FIELDS]
        differing: dict[str, Any] = {}
        for name in compare_fields:
            la = np.asarray(left[name])
            ra = np.asarray(right[name])
            if (
                la.ndim == 0
                or ra.ndim == 0
                or len(la) != len(left_ts)
                or len(ra) != len(right_ts)
            ):
                if _array_hash(la) != _array_hash(ra):
                    differing[name] = {"non_row_array": True}
                continue
            lv, rv = la[li], ra[ri]
            if lv.dtype.kind in "biufc" and rv.dtype.kind in "biufc":
                finite_equal = np.array_equal(np.isfinite(lv), np.isfinite(rv))
                delta = np.abs(
                    np.nan_to_num(lv.astype(np.float64))
                    - np.nan_to_num(rv.astype(np.float64))
                )
                mismatch = (~np.isclose(lv, rv, rtol=0.0, atol=tolerance, equal_nan=True))
                if np.any(mismatch):
                    differing[name] = {
                        "mismatch_rows": int(np.sum(mismatch)),
                        "max_abs_delta": float(np.max(delta)),
                        "finite_mask_equal": bool(finite_equal),
                    }
            elif not np.array_equal(lv, rv):
                differing[name] = {"mismatch_rows": int(np.sum(lv != rv))}
        return {
            "left": str(left_path),
            "right": str(right_path),
            "shared_rows": int(len(shared_ts)),
            "left_only_rows": int(len(set(left_lookup) - set(right_lookup))),
            "right_only_rows": int(len(set(right_lookup) - set(left_lookup))),
            "shared_fields": int(len(shared_fields)),
            "compared_fields": int(len(compare_fields)),
            "comparison_scope": "lineage_and_audit_fields_on_timestamp_overlap",
            "left_only_fields": sorted(set(left.files) - set(right.files)),
            "right_only_fields": sorted(set(right.files) - set(left.files)),
            "differing_shared_fields": differing,
            "equivalent_on_overlap": not differing,
        }


def build_receipt(
    symbol: str,
    raw_5m_path: Path,
    raw_15m_path: Path,
    candidates: list[tuple[str, Path]],
    tolerance: float = 1e-4,
) -> dict[str, Any]:
    source_5m = load_source(raw_5m_path)
    source_15m = load_source(raw_15m_path)
    source_rows = {
        "5m": source_stats(raw_5m_path, source_5m),
        "15m": source_stats(raw_15m_path, source_15m),
    }
    npzs = {
        label: npz_stats(path, source_5m, tolerance) for label, path in candidates
    }
    comparisons = [
        compare_npzs(
            candidates[i][1],
            candidates[j][1],
            tolerance,
            npzs[candidates[i][0]]["array_hashes"],
            npzs[candidates[j][0]]["array_hashes"],
        )
        for i in range(len(candidates))
        for j in range(i + 1, len(candidates))
    ]
    material_gaps = source_rows["5m"]["material_gaps_gt_7d"]
    parent_failures = [
        label
        for label, row in npzs.items()
        if row["synthetic_rows"] and not row["parent_clock"]["valid"]
    ]
    native_parity_failures = []
    for label, row in npzs.items():
        parity = row["native_source_parity"]
        # A newer raw cache can replace rows that were legitimately synthetic
        # in an immutable older snapshot. Only rows declared native by that NPZ
        # are parity obligations.
        bad = any(
            field.get("mismatch_rows", 0) > 0 for field in parity["fields"].values()
        )
        if bad:
            native_parity_failures.append(label)
    source_complete = not material_gaps
    decision = (
        "REGENERATION_SUPPORTED"
        if source_complete and not parent_failures and not native_parity_failures
        else "FAIL_CLOSED_SOURCE_INCOMPLETE"
        if not source_complete
        else "FAIL_CLOSED_NPZ_PARITY"
    )
    return {
        "contract": "TRADIER_NPZ_LINEAGE_AUDIT_V1",
        "symbol": symbol.upper(),
        "generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tolerance": tolerance,
        "sources": source_rows,
        "npzs": npzs,
        "comparisons": comparisons,
        "decision": decision,
        "source_complete": source_complete,
        "parent_clock_failures": parent_failures,
        "native_parity_failures": native_parity_failures,
        "matrix_written": False,
        "promotion_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--raw-5m", required=True, type=Path)
    parser.add_argument("--raw-15m", required=True, type=Path)
    parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        help="LABEL=PATH; may be repeated",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tolerance", type=float, default=1e-4)
    args = parser.parse_args()
    candidates: list[tuple[str, Path]] = []
    for item in args.candidate:
        label, separator, path = item.partition("=")
        if not separator or not label:
            parser.error("--candidate must be LABEL=PATH")
        candidates.append((label, Path(path)))
    receipt = build_receipt(
        args.symbol,
        args.raw_5m,
        args.raw_15m,
        candidates,
        tolerance=args.tolerance,
    )
    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    return 0 if receipt["decision"] == "REGENERATION_SUPPORTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
