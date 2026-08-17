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
MAX_RECENT_PARENT_LAG_SECONDS = {
    "15m": 3 * 86400,
    "1h": 4 * 86400,
    "4h": 5 * 86400,
    "D": 10 * 86400,
}
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


def _is_transient_roundtrip_jump(close: np.ndarray, jump_idx: int, window: int = 8) -> bool:
    """Distinguish an isolated volatile print from a persistent split-scale shift."""
    values = np.asarray(close, dtype=np.float64)
    left = values[max(0, jump_idx - window + 1) : jump_idx + 1]
    right = values[jump_idx + 2 : jump_idx + 2 + window]
    left = left[np.isfinite(left) & (left > 0)]
    right = right[np.isfinite(right) & (right > 0)]
    if len(left) < 3 or len(right) < 3:
        return False
    before = float(np.median(left))
    after = float(np.median(right))
    scale_change = abs(after / before - 1.0)
    return scale_change <= 0.50


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
        selected = arr[mask]
        finite, nonzero, unique = _finite_nonzero_coverage(selected)
        finite_mask = np.isfinite(selected) if selected.dtype.kind in "biufc" else np.zeros(len(selected), dtype=bool)
        missing_idx = np.flatnonzero(~finite_mask)
        warmup_only = False
        warmup_rows = 0
        if len(missing_idx):
            warmup_rows = int(missing_idx[-1]) + 1
            finite_tail_rows = len(selected) - warmup_rows
            prefix_finite = selected[:warmup_rows][finite_mask[:warmup_rows]]
            prefix_is_only_unavailable_sentinel = (
                len(prefix_finite) == 0
                or bool(np.all(np.abs(prefix_finite.astype(float)) <= 1e-12))
            )
            warmup_only = (
                finite_tail_rows >= 20
                and prefix_is_only_unavailable_sentinel
                and bool(finite_mask[warmup_rows:].all())
            )
        audit.stats[f"{name}_finite_pct"] = round(finite * 100.0, 3)
        audit.stats[f"{name}_nonzero_pct"] = round(nonzero * 100.0, 3)
        audit.stats[f"{name}_unique"] = unique
        if warmup_only:
            audit.stats[f"{name}_warmup_rows"] = warmup_rows
        if (finite < 0.95 and not warmup_only) or nonzero < min_nonzero or unique < 8:
            audit.fail(
                f"{name} unusable: finite={finite:.1%}, nonzero={nonzero:.1%}, unique={unique}"
            )


def _require_formation_series(
    z,
    mask: np.ndarray,
    names: Iterable[str],
    audit: Audit,
    *,
    require_nonzero_tail: bool,
    min_tail_rows: int = 100,
    min_tail_nonzero: float = 0.05,
) -> None:
    """Validate causal OHLCV tails while accepting only a leading warm-up.

    Some older but otherwise causal NPZs encode unavailable parent bars as a
    leading zero/NaN prefix.  Global coverage thresholds incorrectly reject
    those files.  Formation detection only consumes the usable tail, so accept
    that prefix but continue to quarantine any zero/NaN hole after the first
    usable observation.
    """
    for name in names:
        if name not in z.files:
            audit.fail(f"missing required field {name}")
            continue
        arr = np.asarray(z[name])
        if len(arr) != len(mask):
            audit.fail(f"{name} length {len(arr)} != timestamps length {len(mask)}")
            continue
        selected = arr[mask]
        finite, nonzero, unique = _finite_nonzero_coverage(selected)
        audit.stats[f"{name}_finite_pct"] = round(finite * 100.0, 3)
        audit.stats[f"{name}_nonzero_pct"] = round(nonzero * 100.0, 3)
        audit.stats[f"{name}_unique"] = unique
        if selected.dtype.kind not in "biufc":
            audit.fail(f"{name} unusable: non-numeric dtype={selected.dtype}")
            continue
        finite_mask = np.isfinite(selected)
        nonzero_mask = finite_mask & (np.abs(selected) > 1e-8)
        usable_idx = np.flatnonzero(nonzero_mask)
        if not len(usable_idx):
            audit.fail(f"{name} unusable: no finite nonzero observations")
            continue
        warmup_rows = int(usable_idx[0])
        tail = selected[warmup_rows:]
        tail_finite = np.isfinite(tail)
        tail_nonzero = tail_finite & (np.abs(tail) > 1e-8)
        tail_unique = int(len(np.unique(tail[tail_finite]))) if tail_finite.any() else 0
        tail_nonzero_rate = float(tail_nonzero.mean()) if len(tail) else 0.0
        audit.stats[f"{name}_warmup_rows"] = warmup_rows
        audit.stats[f"{name}_usable_tail_rows"] = int(len(tail))
        audit.stats[f"{name}_tail_nonzero_pct"] = round(tail_nonzero_rate * 100.0, 3)
        tail_valid = bool(tail_finite.all())
        if require_nonzero_tail:
            tail_valid = tail_valid and bool(tail_nonzero.all())
        else:
            tail_valid = tail_valid and tail_nonzero_rate >= min_tail_nonzero
        if len(tail) < min_tail_rows or not tail_valid or tail_unique < 8:
            audit.fail(
                f"{name} unusable tail: warmup={warmup_rows}, rows={len(tail)}, "
                f"finite={float(tail_finite.mean()) if len(tail) else 0.0:.1%}, "
                f"nonzero={tail_nonzero_rate:.1%}, unique={tail_unique}"
            )


def _require_recent_parent_freshness(
    base_ts: np.ndarray,
    available_ts: np.ndarray,
    timeframe: str,
    audit: Audit,
    recent_rows: int = 1000,
) -> None:
    """Reject an NPZ that forward-fills a stale HTF source into its tail.

    The causal precomputer may prepend older authentic HTF history for warmup,
    so this check intentionally covers only the recent materialized tail.  The
    limits include weekends/holidays and match the raw-source freshness guard.
    """
    limit = MAX_RECENT_PARENT_LAG_SECONDS[timeframe]
    count = min(len(base_ts), len(available_ts), max(1, int(recent_rows)))
    if count <= 0:
        audit.fail(f"timestamp_{timeframe} has no recent rows")
        return
    recent_base = np.asarray(base_ts[-count:], dtype=np.int64)
    recent_available = np.asarray(available_ts[-count:], dtype=np.int64)
    usable = recent_available > 0
    if not bool(usable.any()):
        audit.fail(f"timestamp_{timeframe} recent tail has no available parent")
        return
    lags = recent_base[usable] - recent_available[usable]
    max_lag = int(lags.max()) if len(lags) else limit + 1
    terminal_lag = int(recent_base[-1] - recent_available[-1])
    audit.stats[f"timestamp_{timeframe}_recent_parent_lag_max_s"] = max_lag
    audit.stats[f"timestamp_{timeframe}_terminal_parent_lag_s"] = terminal_lag
    if terminal_lag < 0 or max_lag > limit or terminal_lag > limit:
        audit.fail(
            f"timestamp_{timeframe} stale recent parent: "
            f"max_lag={max_lag}s terminal_lag={terminal_lag}s limit={limit}s"
        )


def _require_formation_parent_freshness(
    base_ts: np.ndarray,
    available_ts: np.ndarray,
    timeframe: str,
    audit: Audit,
    recent_rows: int = 1000,
) -> None:
    """Require a fresh causal terminal parent without rejecting market closures.

    Weekend and exchange-holiday gaps can make the maximum historical lag in a
    recent window exceed a wall-clock threshold even when the terminal parent
    is current.  That is not look-ahead or stale-tail evidence.  Preserve the
    maximum as an explicit warning and fail on an unavailable, future, or stale
    terminal parent.
    """
    limit = MAX_RECENT_PARENT_LAG_SECONDS[timeframe]
    count = min(len(base_ts), len(available_ts), max(1, int(recent_rows)))
    if count <= 0:
        audit.fail(f"timestamp_{timeframe} has no recent rows")
        return
    recent_base = np.asarray(base_ts[-count:], dtype=np.int64)
    recent_available = np.asarray(available_ts[-count:], dtype=np.int64)
    usable = recent_available > 0
    if not bool(usable.any()) or int(recent_available[-1]) <= 0:
        audit.fail(f"timestamp_{timeframe} recent tail has no terminal available parent")
        return
    lags = recent_base[usable] - recent_available[usable]
    max_lag = int(lags.max()) if len(lags) else limit + 1
    terminal_lag = int(recent_base[-1] - recent_available[-1])
    audit.stats[f"timestamp_{timeframe}_recent_parent_lag_max_s"] = max_lag
    audit.stats[f"timestamp_{timeframe}_terminal_parent_lag_s"] = terminal_lag
    if terminal_lag < 0 or terminal_lag > limit:
        audit.fail(
            f"timestamp_{timeframe} stale terminal parent: "
            f"terminal_lag={terminal_lag}s limit={limit}s"
        )
    elif max_lag > limit:
        audit.warnings.append(
            f"timestamp_{timeframe} historical market-closure lag={max_lag}s "
            f"exceeds {limit}s but terminal lag is {terminal_lag}s"
        )


def audit_npz(
    symbol: str,
    npz_path: str | Path | None = None,
    profile: str = "ladder",
    start: str | None = None,
) -> Audit:
    """Audit one symbol. Profiles: floor, core, ladder, formations."""
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
        selected_ts = ts[mask]
        gaps_h = np.diff(selected_ts).astype(np.float64) / 3600.0
        max_gap_h = float(np.max(gaps_h)) if len(gaps_h) else 0.0
        audit.stats["max_timestamp_gap_hours"] = round(max_gap_h, 3)
        audit.stats["timestamp_gaps_gt_7d"] = int((gaps_h > 24 * 7).sum())
        if max_gap_h > 24 * 7:
            audit.warnings.append(
                f"source history contains an internal {max_gap_h / 24.0:.1f}-day gap; "
                "fields are valid on available bars but the replay is not calendar-continuous"
            )
        finite, nonzero, unique = _finite_nonzero_coverage(selected_close)
        audit.stats.update(close_finite_pct=round(finite * 100, 3), close_unique=unique)
        if finite < 0.999 or nonzero < 0.999 or unique < 50:
            audit.fail(f"close unusable: finite={finite:.1%}, nonzero={nonzero:.1%}, unique={unique}")

        returns = np.abs(np.diff(selected_close) / np.maximum(np.abs(selected_close[:-1]), 1e-12))
        max_jump = float(np.nanmax(returns)) if len(returns) else 0.0
        audit.stats["max_bar_jump_pct"] = round(max_jump * 100.0, 3)
        if len(returns):
            jump_idx = int(np.nanargmax(returns))
            audit.stats["max_bar_jump_from_timestamp"] = int(selected_ts[jump_idx])
            audit.stats["max_bar_jump_to_timestamp"] = int(selected_ts[jump_idx + 1])
            audit.stats["max_bar_jump_from_close"] = float(selected_close[jump_idx])
            audit.stats["max_bar_jump_to_close"] = float(selected_close[jump_idx + 1])
        transient_roundtrip = bool(
            len(returns)
            and profile == "formations"
            and _is_transient_roundtrip_jump(selected_close, jump_idx)
        )
        audit.stats["max_bar_jump_classification"] = (
            "TRANSIENT_ROUNDTRIP" if transient_roundtrip else "PERSISTENT_OR_UNVERIFIED"
        )
        # A one-bar round trip is not harmless for formation research: it can
        # manufacture a wedge, breakout, head/shoulders pivot, entry or exit.
        # Classification is retained for diagnosis, but every >80% jump fails
        # until the source is corrected.
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
        if (
            "synthetic_5m_parent_close_ts" in z.files
            and len(z["synthetic_5m_parent_close_ts"]) == len(mask)
        ):
            parent_close = np.asarray(
                z["synthetic_5m_parent_close_ts"], dtype=np.int64
            )[execution_mask]
            execution_ts = ts[execution_mask]
            synthetic_rows = (
                np.asarray(z["synthetic_5m"], dtype=np.int8)[execution_mask].astype(bool)
                if "synthetic_5m" in z.files
                else np.zeros(len(execution_ts), dtype=bool)
            )
            lags = parent_close[synthetic_rows] - execution_ts[synthetic_rows]
            audit.stats["synthetic_5m_parent_lag_max_s"] = (
                int(lags.max()) if len(lags) else 0
            )
            audit.stats["synthetic_5m_parent_lag_min_s"] = (
                int(lags.min()) if len(lags) else 0
            )
            if len(lags) and (int(lags.min()) < 0 or int(lags.max()) > 600):
                audit.fail(
                    "synthetic 5m provenance points outside its containing "
                    f"15m bar: lag range={int(lags.min())}..{int(lags.max())}s"
                )
        if synthetic_rate > 0.05:
            audit.warnings.append(
                f"interpolated 5m execution bars={synthetic_rate:.1%} in window; "
                "accepted bounded containing-15m approximation and disclosed in results"
            )

        if profile in {"core", "ladder"}:
            base_ts = ts[mask]
            for tf in ("15m", "1h", "4h", "D"):
                name = f"timestamp_{tf}"
                if name not in z.files or len(z[name]) != len(mask):
                    audit.fail(f"missing closed-bar availability field {name}")
                    continue
                avail = np.asarray(z[name], dtype=np.int64)[mask]
                equal_rate = float((avail == base_ts).mean())
                future_rate = float((avail > base_ts).mean())
                audit.stats[f"{name}_equals_base_pct"] = round(equal_rate * 100.0, 3)
                audit.stats[f"{name}_future_pct"] = round(future_rate * 100.0, 6)
                if equal_rate > 0.95:
                    audit.fail(f"{name} is base timestamp alias; NPZ predates closed-bar fix")
                if future_rate > 0.001:
                    audit.fail(f"{name} contains future availability on {future_rate:.2%} of rows")
                _require_recent_parent_freshness(base_ts, avail, tf, audit)

            _require_continuous(
                z,
                mask,
                [f"{stem}_{tf}" for tf in SIGNAL_TFS for stem in ("wt1", "stoch_k", "dc_position")],
                audit,
            )
        if profile == "formations":
            # Classic formations derive their vector fields directly from the
            # causally materialized OHLCV arrays. Validate exactly those inputs
            # instead of unrelated WT/DC/linear-regression ladder indicators.
            _require_formation_series(
                z,
                mask,
                [
                    f"{stem}_{tf}"
                    for tf in SIGNAL_TFS
                    for stem in ("open", "high", "low", "close")
                ],
                audit,
                require_nonzero_tail=True,
            )
            _require_formation_series(
                z,
                mask,
                [f"volume_{tf}" for tf in SIGNAL_TFS],
                audit,
                require_nonzero_tail=False,
            )
            base_ts = ts[mask]
            for tf in SIGNAL_TFS:
                name = f"timestamp_{tf}"
                if name not in z.files or len(z[name]) != len(mask):
                    audit.fail(f"missing closed-bar availability field {name}")
                    continue
                available = np.asarray(z[name], dtype=np.int64)[mask]
                future_rate = float((available > base_ts).mean())
                audit.stats[f"{name}_future_pct"] = round(future_rate * 100.0, 6)
                if future_rate > 0.001:
                    audit.fail(f"{name} contains future availability on {future_rate:.2%} of rows")
                _require_formation_parent_freshness(base_ts, available, tf, audit)
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
        "reclaim_pending": int(float(result.get("reclaim_pending", 0) or 0)),
        "reentry_violations": int(float(result.get("reentry_violations", 0) or 0)),
    }
    diagnostics["valid"] = (
        diagnostics["trades"] > 0
        and diagnostics["requested_side_opens"] > 0
        and diagnostics["opposite_opens"] == 0
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


def audit_stage0_result(result: dict, side: str) -> dict:
    """Require an uncontaminated one-unit B&H seed floor.

    A no-exit replay is not stage 0 if ordinary entry paths add inventory after
    the seed. `opens_<side>` counts flat-to-active transitions and therefore
    cannot see augments; sizing telemetry is the authoritative attempt count.
    """
    diagnostics = audit_ladder_result(result, side, require_sizing=True)
    wanted = side.lower()
    benchmark = float(result.get("benchmark_deployed_usd", 0) or 0)
    diagnostics.update({
        "real_closes": int(float(result.get("real_closes", 0) or 0)),
        "mtm_count": int(float(result.get("mtm_count", 0) or 0)),
        "time_in_mkt_pct": float(result.get(f"time_in_mkt_{wanted}_pct", 0) or 0),
        "max_filled_start_mult": float(result.get("max_filled_start_mult", 0) or 0),
        "open_notional_sum": float(result.get("open_notional_sum", 0) or 0),
        "max_open_notional": float(result.get("max_open_notional", 0) or 0),
        "benchmark_deployed_usd": benchmark,
    })
    diagnostics["non_seed_sized_open_events"] = max(
        0, diagnostics["sized_open_events"] - 1
    )
    notional_tol = max(1.0, benchmark * 0.01)
    diagnostics["valid"] = bool(
        diagnostics["valid"]
        and diagnostics["trades"] == 1
        and diagnostics["requested_side_opens"] == 1
        and diagnostics["real_closes"] == 0
        and diagnostics["mtm_count"] == 1
        and diagnostics["time_in_mkt_pct"] >= 99.0
        and diagnostics["sized_open_events"] == 1
        and diagnostics["non_seed_sized_open_events"] == 0
        and abs(diagnostics["max_requested_mult"] - 1.0) <= 0.001
        and abs(diagnostics["max_filled_start_mult"] - 1.0) <= 0.001
        and benchmark > 0
        and abs(diagnostics["open_notional_sum"] - benchmark) <= notional_tol
        and abs(diagnostics["max_open_notional"] - benchmark) <= notional_tol
    )
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--npz", default="")
    parser.add_argument(
        "--profile",
        choices=("floor", "core", "ladder", "formations"),
        default="ladder",
    )
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
