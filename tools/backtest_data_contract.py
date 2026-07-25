#!/usr/bin/env python3
"""Mechanical data-integrity preflight for stock backtest campaigns.

The matrix must not treat a structurally empty, synthetic, or look-ahead NPZ as
strategy evidence.  This module is intentionally read-only: callers receive a
valid/invalid verdict and must refuse DB writes when invalid.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDICATORS = ROOT / "backtest_v8" / "indicators"
SIGNAL_TFS = ("15m", "1h", "4h", "D")
LADDER_TFS = ("1h", "4h", "D")
_ET = ZoneInfo("America/New_York")


@dataclass
class Audit:
    symbol: str
    profile: str
    path: str
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.valid = False
        self.errors.append(message)


def _window_mask(timestamps: np.ndarray, start: str | None) -> np.ndarray:
    if not start:
        return np.ones(len(timestamps), dtype=bool)
    start_epoch = int(np.datetime64(start, "s").astype(np.int64))
    return np.asarray(timestamps, dtype=np.int64) >= start_epoch


def _rth_mask(timestamps: np.ndarray) -> np.ndarray:
    out = np.zeros(len(timestamps), dtype=bool)
    for i, value in enumerate(np.asarray(timestamps, dtype=np.int64)):
        dt = datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone(_ET)
        minute = dt.hour * 60 + dt.minute
        out[i] = dt.weekday() < 5 and 9 * 60 + 30 <= minute <= 16 * 60
    return out


def _finite_nonzero_coverage(arr: np.ndarray) -> tuple[float, float, int]:
    values = np.asarray(arr)
    if values.dtype.kind not in "biufc":
        return 0.0, 0.0, 0
    finite = np.isfinite(values)
    finite_rate = float(finite.mean()) if len(values) else 0.0
    nonzero_rate = float((finite & (np.abs(values) > 1e-8)).mean()) if len(values) else 0.0
    unique = int(len(np.unique(values[finite]))) if finite.any() else 0
    return finite_rate, nonzero_rate, unique


def _legacy_linear_triplet_rate(close: np.ndarray) -> float:
    """Recognize the old exact-linear 15m->5m fabrication without metadata."""
    values = np.asarray(close, dtype=np.float64)
    if len(values) < 30:
        return 0.0
    best = 0.0
    scale = max(1.0, float(np.nanmedian(np.abs(values))))
    tolerance = scale * 1e-7
    for offset in range(3):
        usable = values[offset : offset + ((len(values) - offset) // 3) * 3]
        if len(usable) < 30:
            continue
        groups = usable.reshape(-1, 3)
        affine = np.abs((groups[:, 2] - groups[:, 1]) - (groups[:, 1] - groups[:, 0])) <= tolerance
        best = max(best, float(affine.mean()))
    return best


def _require_continuous(
    z,
    mask: np.ndarray,
    names: Iterable[str],
    audit: Audit,
    min_nonzero: float = 0.20,
) -> None:
    for name in names:
        if name not in z.files:
            audit.fail(f"missing required field {name}")
            continue
        arr = np.asarray(z[name])
        if len(arr) != len(mask):
            audit.fail(f"{name} length {len(arr)} != timestamps length {len(mask)}")
            continue
        finite, nonzero, unique = _finite_nonzero_coverage(arr[mask])
        audit.stats[f"{name}_finite_pct"] = round(finite * 100.0, 3)
        audit.stats[f"{name}_nonzero_pct"] = round(nonzero * 100.0, 3)
        audit.stats[f"{name}_unique"] = unique
        if finite < 0.95 or nonzero < min_nonzero or unique < 8:
            audit.fail(
                f"{name} unusable: finite={finite:.1%}, nonzero={nonzero:.1%}, unique={unique}"
            )


def audit_npz(
    symbol: str,
    npz_path: str | Path | None = None,
    profile: str = "ladder",
    start: str | None = None,
) -> Audit:
    """Audit one symbol. Profiles: floor, core, ladder."""
    sym = symbol.upper()
    path = Path(npz_path) if npz_path else DEFAULT_INDICATORS / f"{sym}.npz"
    audit = Audit(sym, profile, str(path))
    if not path.exists():
        audit.fail("indicator NPZ does not exist")
        return audit
    try:
        z = np.load(path, allow_pickle=False)
    except Exception as exc:
        audit.fail(f"cannot load NPZ: {exc}")
        return audit
    with z:
        if "timestamps" not in z.files or "close" not in z.files:
            audit.fail("missing timestamps or close")
            return audit
        ts = np.asarray(z["timestamps"], dtype=np.int64)
        close = np.asarray(z["close"], dtype=np.float64)
        if len(ts) != len(close) or len(ts) < 100:
            audit.fail(f"invalid base arrays: timestamps={len(ts)}, close={len(close)}")
            return audit
        mask = _window_mask(ts, start)
        audit.stats["rows_total"] = int(len(ts))
        audit.stats["rows_window"] = int(mask.sum())
        if mask.sum() < 100:
            audit.fail(f"only {int(mask.sum())} rows in requested window")
            return audit
        selected_close = close[mask]
        finite, nonzero, unique = _finite_nonzero_coverage(selected_close)
        audit.stats.update(close_finite_pct=round(finite * 100, 3), close_unique=unique)
        if finite < 0.999 or nonzero < 0.999 or unique < 50:
            audit.fail(f"close unusable: finite={finite:.1%}, nonzero={nonzero:.1%}, unique={unique}")

        returns = np.abs(np.diff(selected_close) / np.maximum(np.abs(selected_close[:-1]), 1e-12))
        max_jump = float(np.nanmax(returns)) if len(returns) else 0.0
        audit.stats["max_bar_jump_pct"] = round(max_jump * 100.0, 3)
        if max_jump > 0.80:
            audit.fail(
                f"unadjusted/corrupt price discontinuity: max one-bar jump={max_jump:.1%}"
            )
        elif max_jump > 0.30:
            audit.warnings.append(f"large one-bar price jump={max_jump:.1%}; verify corporate actions")

        execution_mask = mask & _rth_mask(ts)
        audit.stats["rth_rows_window"] = int(execution_mask.sum())
        if execution_mask.sum() < 50:
            audit.fail(f"only {int(execution_mask.sum())} regular-session rows in requested window")
            return audit
        if "synthetic_5m" in z.files and len(z["synthetic_5m"]) == len(mask):
            synthetic_rate = float(np.asarray(z["synthetic_5m"])[execution_mask].astype(bool).mean())
            source = "provenance"
        else:
            synthetic_rate = _legacy_linear_triplet_rate(close[execution_mask])
            source = "legacy-linear-inference"
        audit.stats["synthetic_5m_pct"] = round(synthetic_rate * 100.0, 3)
        audit.stats["synthetic_detection"] = source
        if synthetic_rate > 0.05:
            audit.warnings.append(
                f"interpolated 5m execution bars={synthetic_rate:.1%} in window; "
                "accepted by campaign contract and disclosed in results"
            )

        if profile in {"core", "ladder"}:
            base_ts = ts[mask]
            for tf in ("1h", "4h", "D"):
                name = f"timestamp_{tf}"
                if name not in z.files or len(z[name]) != len(mask):
                    audit.fail(f"missing closed-bar availability field {name}")
                    continue
                avail = np.asarray(z[name], dtype=np.int64)[mask]
                equal_rate = float((avail == base_ts).mean())
                future_rate = float((avail > base_ts).mean())
                audit.stats[f"{name}_equals_base_pct"] = round(equal_rate * 100.0, 3)
                if equal_rate > 0.95:
                    audit.fail(f"{name} is base timestamp alias; NPZ predates closed-bar fix")
                if future_rate > 0.001:
                    audit.fail(f"{name} contains future availability on {future_rate:.2%} of rows")

            _require_continuous(
                z,
                mask,
                [f"{stem}_{tf}" for tf in SIGNAL_TFS for stem in ("wt1", "stoch_k", "dc_position")],
                audit,
            )
        if profile == "ladder":
            _require_continuous(
                z,
                mask,
                [f"{stem}_{tf}" for tf in LADDER_TFS for stem in ("lrL_pct_b", "lrL_slope")],
                audit,
            )
    return audit


def audit_ladder_result(result: dict, side: str, require_sizing: bool = True) -> dict:
    """Validate side isolation and observable sizing before a matrix write."""
    wanted = side.lower()
    opposite = "short" if wanted == "long" else "long"
    diagnostics = {
        "trades": int(float(result.get("trades", 0) or 0)),
        "requested_side_opens": int(float(result.get(f"opens_{wanted}", 0) or 0)),
        "opposite_opens": int(float(result.get(f"opens_{opposite}", 0) or 0)),
        "sized_open_events": int(float(result.get("sized_open_events", 0) or 0)),
        "max_requested_mult": float(result.get("max_requested_mult", 0) or 0),
        "requested_fill_ratio": float(result.get("requested_fill_ratio", 0) or 0),
        "size_clamp_count": int(float(result.get("size_clamp_count", 0) or 0)),
        "reentry_pending": int(float(result.get("reentry_pending", 0) or 0)),
        "reentry_violations": int(float(result.get("reentry_violations", 0) or 0)),
    }
    diagnostics["valid"] = (
        diagnostics["trades"] > 0
        and diagnostics["requested_side_opens"] > 0
        and diagnostics["opposite_opens"] == 0
        and diagnostics["reentry_pending"] == 0
        and diagnostics["reentry_violations"] == 0
        and (
            not require_sizing
            or (
                diagnostics["sized_open_events"] > 0
                and diagnostics["max_requested_mult"] > 0
                and diagnostics["requested_fill_ratio"] >= 0.90
                and diagnostics["size_clamp_count"] == 0
            )
        )
    )
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--npz", default="")
    parser.add_argument("--profile", choices=("floor", "core", "ladder"), default="ladder")
    parser.add_argument("--start", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit_npz(args.symbol, args.npz or None, args.profile, args.start or None)
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        state = "PASS" if result.valid else "QUARANTINE"
        print(f"[DATA_CONTRACT] {result.symbol} profile={result.profile} {state}")
        for message in result.errors:
            print(f"  ERROR: {message}")
        for message in result.warnings:
            print(f"  WARN: {message}")
        print("  stats=" + json.dumps(result.stats, sort_keys=True))
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
