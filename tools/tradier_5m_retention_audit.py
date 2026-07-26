#!/usr/bin/env python3
"""Audit durable native Tradier 5m data and NPZ provenance coverage."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path):
    try:
        with path.open() as handle:
            return json.load(handle)
    except Exception:
        return None


def _iso_epoch(value: int) -> str:
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


def _source_stats(path: Path) -> dict:
    data = _load_json(path)
    if not isinstance(data, list) or not data:
        return {"path": str(path), "exists": path.exists(), "rows": 0}
    timestamps = [
        row.get("timestamp") or row.get("close_time") or row.get("t")
        for row in data
        if isinstance(row, dict)
    ]
    timestamps = [str(value) for value in timestamps if value is not None]
    unique = set(timestamps)
    return {
        "path": str(path),
        "exists": True,
        "rows": len(data),
        "timestamp_rows": len(timestamps),
        "unique_timestamps": len(unique),
        "duplicates": len(timestamps) - len(unique),
        "monotonic": timestamps == sorted(timestamps),
        "start": min(timestamps) if timestamps else None,
        "end": max(timestamps) if timestamps else None,
        "bytes": path.stat().st_size,
    }


def _npz_stats(path: Path, parent_15m_path: Path | None = None) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False, "rows": 0}
    try:
        with np.load(path, allow_pickle=False) as z:
            timestamps = np.asarray(z["timestamps"], dtype=np.int64)
            if "synthetic_5m" not in z.files:
                return {
                    "path": str(path),
                    "exists": True,
                    "rows": len(timestamps),
                    "provenance_present": False,
                }
            synthetic = np.asarray(z["synthetic_5m"]).astype(bool)
            if len(synthetic) != len(timestamps):
                return {
                    "path": str(path),
                    "exists": True,
                    "rows": len(timestamps),
                    "provenance_present": True,
                    "provenance_length_valid": False,
                }
            native = ~synthetic

            def span(mask: np.ndarray) -> dict:
                selected = timestamps[mask]
                return {
                    "rows": int(mask.sum()),
                    "pct": round(float(mask.mean()) * 100.0, 3) if len(mask) else 0.0,
                    "start": _iso_epoch(selected[0]) if len(selected) else None,
                    "end": _iso_epoch(selected[-1]) if len(selected) else None,
                }

            result = {
                "path": str(path),
                "exists": True,
                "rows": len(timestamps),
                "start": _iso_epoch(timestamps[0]) if len(timestamps) else None,
                "end": _iso_epoch(timestamps[-1]) if len(timestamps) else None,
                "provenance_present": True,
                "provenance_length_valid": True,
                "native": span(native),
                "interpolated": span(synthetic),
            }
            if (
                "synthetic_5m_parent_close_ts" in z.files
                and len(z["synthetic_5m_parent_close_ts"]) == len(timestamps)
            ):
                parent_ts = np.asarray(z["synthetic_5m_parent_close_ts"], dtype=np.int64)
                lags = parent_ts[synthetic] - timestamps[synthetic]
                result["parent_provenance_present"] = True
                result["parent_lag_min_s"] = int(lags.min()) if len(lags) else 0
                result["parent_lag_max_s"] = int(lags.max()) if len(lags) else 0
                result["parent_lag_valid"] = bool(
                    not len(lags)
                    or (int(lags.min()) >= 0 and int(lags.max()) <= 600)
                )
            else:
                result["parent_provenance_present"] = False
            return result
    except Exception as exc:
        return {"path": str(path), "exists": True, "rows": 0, "error": str(exc)}


def _symbols(args) -> list[str]:
    values = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
    if args.symbols_file:
        payload = _load_json(Path(args.symbols_file))
        if isinstance(payload, list):
            values.extend(str(value).strip().upper() for value in payload if str(value).strip())
        elif isinstance(payload, dict):
            values.extend(str(value).strip().upper() for value in payload if str(value).strip())
    if not values:
        values.extend(path.name[:-8].upper() for path in Path(args.source_root).glob("*_5m.json"))
    return sorted(set(values))


def _retention_errors(
    symbol: str,
    current: dict,
    previous: dict | None,
    allow_missing_native: bool = False,
) -> list[str]:
    if current.get("rows", 0) <= 0:
        if allow_missing_native and not previous:
            return []
        return [f"{symbol}: native 5m source missing or empty"]
    errors = []
    if not current.get("monotonic"):
        errors.append(f"{symbol}: native timestamps are not monotonic")
    if current.get("duplicates", 0):
        errors.append(f"{symbol}: native source has {current['duplicates']} duplicate timestamps")
    if previous:
        if current["rows"] < int(previous.get("rows", 0)):
            errors.append(f"{symbol}: native rows shrank {previous.get('rows')} -> {current['rows']}")
        if previous.get("start") and current.get("start") > previous["start"]:
            errors.append(f"{symbol}: native start advanced {previous['start']} -> {current.get('start')}")
        if previous.get("end") and current.get("end") < previous["end"]:
            errors.append(f"{symbol}: native end regressed {previous['end']} -> {current.get('end')}")
    return errors


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(ROOT / "klines_cache_backtest" / "tradier"))
    parser.add_argument("--npz-root", default=str(ROOT / "backtest_v8" / "indicators"))
    parser.add_argument("--symbols", default="")
    parser.add_argument("--symbols-file", default="")
    parser.add_argument("--ledger", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--update-ledger", action="store_true")
    parser.add_argument(
        "--allow-missing-native",
        action="store_true",
        help="Bootstrap only: permit a symbol with no prior ledger entry before the first fetch",
    )
    args = parser.parse_args()

    prior = _load_json(Path(args.ledger)) if args.ledger else {}
    prior_symbols = prior.get("symbols", {}) if isinstance(prior, dict) else {}
    rows = {}
    errors = []
    for symbol in _symbols(args):
        source = _source_stats(Path(args.source_root) / f"{symbol}_5m.json")
        npz = _npz_stats(
            Path(args.npz_root) / f"{symbol}.npz",
            Path(args.source_root) / f"{symbol}_15m.json",
        )
        symbol_errors = _retention_errors(
            symbol,
            source,
            prior_symbols.get(symbol),
            allow_missing_native=args.allow_missing_native,
        )
        if npz.get("exists") and not npz.get("provenance_present", False):
            symbol_errors.append(f"{symbol}: NPZ is missing synthetic_5m provenance")
        if npz.get("provenance_present") and not npz.get("provenance_length_valid", False):
            symbol_errors.append(f"{symbol}: NPZ provenance length does not match timestamps")
        if npz.get("parent_provenance_present") and not npz.get(
            "parent_lag_valid", False
        ):
            symbol_errors.append(
                f"{symbol}: synthetic parent lag outside containing 15m bar "
                f"({npz.get('parent_lag_min_s')}..{npz.get('parent_lag_max_s')}s)"
            )
        rows[symbol] = {"native_source": source, "npz": npz, "errors": symbol_errors}
        errors.extend(symbol_errors)

    now = datetime.now(timezone.utc).isoformat()
    report = {
        "generated_at": now,
        "valid": not errors,
        "contract": (
            "native 5m JSON is append-only and durable; gaps may use disclosed containing-15m "
            "interpolation; NPZ synthetic_5m=0 means native and 1 means interpolated"
        ),
        "symbol_count": len(rows),
        "errors": errors,
        "symbols": rows,
    }
    if args.report:
        _write_atomic(Path(args.report), report)
    print(json.dumps(report, sort_keys=True))

    if args.update_ledger and not errors and args.ledger:
        ledger = {
            "updated_at": now,
            "symbols": {
                symbol: {
                    key: value
                    for key, value in row["native_source"].items()
                    if key in {"rows", "start", "end", "bytes"}
                }
                for symbol, row in rows.items()
            },
        }
        _write_atomic(Path(args.ledger), ledger)
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
