#!/usr/bin/env python3
"""Backtest-only exact replay adapter for a frozen band-ladder validation fold.

The vector campaign performs candidate selection.  This module has a narrower
job: materialize one already-frozen validation curve as an auditable fill
schedule, bind every fill and completed-HTF source timestamp to the source NPZ,
and replay the schedule through ``backtest_v8_engine``.

Nothing in this module is imported by live processes.  Specs are fail-closed,
explicitly forbid promotion, and support one symbol/position side only.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from tools import vec_band_ladder_walkforward as ladder
from tools.research_availability_clock import (
    CLOCK_CONTRACT,
    build_availability_clock,
    next_strictly_later_index,
)


SPEC_KIND = "V8_RESEARCH_BAND_LADDER_REPLAY"
SPEC_VERSION = 3
LEGACY_SPEC_VERSION = 1
PARENT_UNSAFE_SPEC_VERSION = 2
REASON_PREFIX = "V8_RESEARCH_BAND_LADDER_REPLAY"


class LadderReplayError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _write_schedule_deterministic(
    path: str | Path, events: list[dict[str, Any]]
) -> Path:
    """Write a byte-stable gzip schedule.

    ``gzip.open(..., "wt")`` embeds wall-clock mtime and the output filename in
    the gzip header.  That made the exact-replay fingerprint change even when
    the frozen event ledger did not.  A blank filename plus mtime=0 makes the
    compressed bytes a pure function of the ordered events.
    """
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as raw:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            mtime=0,
        ) as compressed:
            with io.TextIOWrapper(
                compressed, encoding="utf-8", newline="\n"
            ) as text:
                for event in events:
                    text.write(
                        json.dumps(
                            event,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                        + "\n"
                    )
    return target


def audit_execution_provenance(
    data: Any,
    *,
    left: int,
    right: int,
) -> dict[str, Any]:
    """Return the shared parent-close availability contract for one window."""
    left = int(left)
    right = int(right)
    synthetic = np.asarray(data.synthetic, dtype=np.uint8)[left:right].astype(
        bool
    )
    try:
        full_clock = build_availability_clock(data)
    except ValueError as exc:
        return {
            "status": "BLOCKED",
            "safe_for_exact_engine": False,
            "reason": str(exc),
            "rows": int(right - left),
            "synthetic_rows": int(synthetic.sum()),
            "future_parent_rows": None,
        }
    availability = full_clock.availability_ts[left:right]
    source_ts = full_clock.source_ts[left:right]
    lag = availability[synthetic] - source_ts[synthetic]
    window_hash = _canonical_sha256(
        [
            [
                int(availability[i]),
                int(source_ts[i]),
                int(full_clock.source_row_index[left + i]),
                int(synthetic[i]),
            ]
            for i in range(right - left)
        ]
    )
    return {
        "status": "PASS",
        "safe_for_exact_engine": True,
        "reason": None,
        "policy": CLOCK_CONTRACT,
        "ordering": "availability_ts_then_source_row_index",
        "rows": int(right - left),
        "synthetic_rows": int(synthetic.sum()),
        "synthetic_pct": (
            100.0 * float(synthetic.mean()) if len(synthetic) else 0.0
        ),
        "future_parent_rows": int(np.count_nonzero(lag > 0)),
        "parent_lag_min_seconds": int(lag.min()) if len(lag) else 0,
        "parent_lag_max_seconds": int(lag.max()) if len(lag) else 0,
        "shared_availability_rows": int(
            len(availability) - len(np.unique(availability))
        ),
        "availability_source_rows_sha256": window_hash,
    }


@dataclass(frozen=True)
class LadderReplayAction:
    fill_ts: int
    fill_index: int
    signal_ts: int
    signal_index: int
    event_type: str
    action: str
    order_side: str
    position_side: str
    fill_price: float
    quantity: float
    full_close: bool
    reason: str
    source_event: dict[str, Any]


def _close_enough(left: float, right: float, *, rel: float = 1e-10) -> bool:
    return abs(left - right) <= max(1e-9, abs(right) * rel)


def _event_sources(
    signals: ladder.SignalData,
    htfs: dict[str, Any],
    signal_index: int,
) -> dict[str, int]:
    if signals.entry_source_ts is not None and signal_index in signals.entry_source_ts:
        return {
            str(tf): int(source_ts)
            for tf, source_ts in signals.entry_source_ts[signal_index].items()
        }
    out: dict[str, int] = {}
    for slot, tf in enumerate(ladder.TF_ORDER):
        if not bool(signals.event_tf[slot, signal_index]):
            continue
        matches = np.flatnonzero(htfs[tf].event_index == signal_index)
        if len(matches) != 1:
            raise LadderReplayError(
                f"{tf} ladder event at row {signal_index} has no unique completed HTF source"
            )
        out[tf] = int(htfs[tf].source_ts[int(matches[0])])
    return out


def trace_frozen_curve(
    data: Any,
    signals: ladder.SignalData,
    htfs: dict[str, Any],
    curve: ladder.Curve,
    *,
    commission_rate: float,
    slippage_rate: float,
    side: str = "LONG",
    left: int = 0,
    right: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reference accounting loop with a complete exact-fill event ledger."""
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise LadderReplayError(f"unsupported ladder side: {side}")
    side_sign = 1.0 if side == "LONG" else -1.0
    is_long = side == "LONG"
    right = len(data.ts) if right is None else int(right)
    left = int(left)
    if right - left < 100:
        raise LadderReplayError("frozen replay window is too short")
    cash = ladder.ACCOUNT_EQUITY
    qty = 0.0
    last_exit_fill = math.nan
    reclaim_level = math.nan
    prior_exit_notional = 0.0
    gap_seen = False
    pending: dict[str, Any] | None = None
    peak_equity = ladder.ACCOUNT_EQUITY
    min_equity = ladder.ACCOUNT_EQUITY
    max_dd = 0.0
    requested = filled = 0.0
    clamp_count = fill_count = exit_count = reclaim_count = lower_count = 0
    held_bars = 0
    weighted_exposure = 0.0
    peak_mark_notional = 0.0
    peak_post_fill_notional = 0.0
    beyond_reclaim = 0
    events: list[dict[str, Any]] = []

    def equity(px: float) -> float:
        return cash + side_sign * qty * px

    def clock_fields(prefix: str, index: int) -> dict[str, int]:
        source_ts = np.asarray(
            getattr(data, "source_ts", data.ts), dtype=np.int64
        )
        return {
            f"{prefix}_ts": int(data.ts[index]),
            f"{prefix}_availability_ts": int(data.ts[index]),
            f"{prefix}_source_ts": int(source_ts[index]),
            f"{prefix}_source_row_index": int(data.full_indices[index]),
            f"{prefix}_clock_index": int(index),
        }

    for i in range(left, right):
        op = float(data.open[i])
        close = float(data.close[i])
        if pending is not None and i == int(pending["fill_index"]):
            kind = str(pending["kind"])
            signal_index = int(pending["signal_index"])
            signal_ts = int(data.ts[signal_index])
            if int(data.ts[i]) <= signal_ts:
                raise LadderReplayError(
                    f"{kind} signal row {signal_index} did not fill at the "
                    f"next strictly later availability row {i}"
                )
            if kind == "exit" and qty > 0:
                px = op * (1.0 - side_sign * slippage_rate)
                close_qty = qty
                notional = close_qty * px
                cash += side_sign * (
                    notional - side_sign * commission_rate * notional
                )
                prior_exit_notional = min(ladder.CAPACITY, notional)
                last_exit_fill = px
                reclaim_level = (
                    max(px, float(pending["ref"]))
                    if is_long
                    else min(px, float(pending["ref"]))
                )
                qty = 0.0
                exit_count += 1
                events.append(
                    {
                        "type": "EXIT",
                        "reason": "E02_DONCHIAN_4h_N30",
                        "signal_index": signal_index - left,
                        "fill_index": i - left,
                        "latency_rth_bars": 1,
                        "fill_px": px,
                        "quantity": close_qty,
                        "filled_notional_usd": notional,
                        "position_qty_after_fill": 0.0,
                        "entry_capacity_usd": ladder.CAPACITY,
                        "completed_htf_source_ts": {
                            "4h": int(pending["source_ts"])
                        },
                        **clock_fields("signal", signal_index),
                        **clock_fields("fill", i),
                    }
                )
                gap_seen = False
            elif kind == "entry":
                px = op * (1.0 + side_sign * slippage_rate)
                current = qty * px
                requested_notional = float(pending["requested_notional"])
                absolute_target = bool(pending["absolute_target"])
                want = (
                    max(0.0, requested_notional - current)
                    if absolute_target
                    else requested_notional
                )
                capacity_left = max(0.0, ladder.CAPACITY - current)
                actual = min(want, capacity_left)
                requested += max(0.0, want)
                filled += actual
                clamped = actual + 1e-9 < want
                clamp_count += int(clamped)
                if actual > 0:
                    add_qty = actual / px
                    prior_qty = qty
                    cash -= side_sign * actual + commission_rate * actual
                    qty += add_qty
                    post_fill_notional = qty * px
                    if post_fill_notional > ladder.CAPACITY + 1e-6:
                        raise LadderReplayError(
                            f"entry capacity breach: {post_fill_notional}"
                        )
                    peak_post_fill_notional = max(
                        peak_post_fill_notional, post_fill_notional
                    )
                    fill_count += 1
                    reason = str(pending["reason"])
                    reclaim_count += int(reason == "reclaim")
                    lower_count += int(
                        reason in {"ladder_lower", "ladder_higher"}
                    )
                    events.append(
                        {
                            "type": "ENTRY" if prior_qty <= 0 else "AUGMENT",
                            "reason": reason,
                            "signal_index": signal_index - left,
                            "fill_index": i - left,
                            "latency_rth_bars": 1,
                            "fill_px": px,
                            "quantity": add_qty,
                            "requested_notional_usd": want,
                            "requested_target_notional_usd": (
                                requested_notional if absolute_target else None
                            ),
                            "filled_notional_usd": actual,
                            "position_qty_before_fill": prior_qty,
                            "position_qty_after_fill": qty,
                            "post_fill_notional_usd": post_fill_notional,
                            "entry_capacity_usd": ladder.CAPACITY,
                            "clamped": clamped,
                            "semantics": "target" if absolute_target else "add",
                            "entry_multiplier": float(
                                pending.get("entry_multiplier", 0.0)
                            ),
                            "completed_htf_source_ts": dict(
                                pending.get("completed_htf_source_ts") or {}
                            ),
                            **clock_fields("signal", signal_index),
                            **clock_fields("fill", i),
                        }
                    )
                elif want > 0:
                    # A request made while already at hard capacity is part of
                    # the vector clamp accounting even though it must not emit
                    # an engine order. Preserve it as an auditable source
                    # signal so exact replay neither invents a zero-quantity
                    # action nor silently drops the clamp.
                    events.append(
                        {
                            "type": "CAPACITY_NO_FILL",
                            "reason": str(pending["reason"]),
                            "signal_index": signal_index - left,
                            "fill_index": i - left,
                            "latency_rth_bars": 1,
                            "requested_notional_usd": want,
                            "filled_notional_usd": 0.0,
                            "clamped": True,
                            "entry_multiplier": float(
                                pending.get("entry_multiplier", 0.0)
                            ),
                            "completed_htf_source_ts": dict(
                                pending.get("completed_htf_source_ts") or {}
                            ),
                            **clock_fields("signal", signal_index),
                            **clock_fields("fill", i),
                        }
                    )
            pending = None

        mark_equity = equity(close)
        min_equity = min(min_equity, mark_equity)
        peak_equity = max(peak_equity, mark_equity)
        if peak_equity > 0:
            max_dd = max(
                max_dd, 100.0 * (peak_equity - mark_equity) / peak_equity
            )
        notional = qty * close
        peak_mark_notional = max(peak_mark_notional, notional)
        held_bars += int(qty > 0)
        weighted_exposure += min(ladder.CAPACITY, notional) / ladder.CAPACITY

        if pending is not None:
            continue
        if i + 1 >= right:
            continue
        fill_index = next_strictly_later_index(data.ts, i, right)
        if fill_index is None:
            continue
        if qty > 0:
            if signals.exit_event[i]:
                ref = float(signals.exit_ref[i])
                if not math.isfinite(ref):
                    ref = float(data.high[i] if is_long else data.low[i])
                h4 = htfs["4h"]
                match = np.flatnonzero(h4.event_index == i)
                if len(match) != 1:
                    raise LadderReplayError(
                        f"exit at row {i} has no unique completed 4h source"
                    )
                pending = {
                    "kind": "exit",
                    "ref": ref,
                    "signal_index": i,
                    "source_ts": int(h4.source_ts[int(match[0])]),
                    "fill_index": fill_index,
                }
            elif signals.entry_mult[i] > 0:
                mult = float(signals.entry_mult[i])
                pending = {
                    "kind": "entry",
                    "requested_notional": ladder.BASE_UNIT * mult,
                    "absolute_target": curve.semantics == "target",
                    "reason": "ladder_add",
                    "signal_index": i,
                    "fill_index": fill_index,
                    "entry_multiplier": mult,
                    "completed_htf_source_ts": _event_sources(
                        signals, htfs, i
                    ),
                }
        else:
            if math.isfinite(last_exit_fill):
                gap_seen |= (
                    float(data.low[i]) < last_exit_fill
                    if is_long
                    else float(data.high[i]) > last_exit_fill
                )
                reclaim_crossed = (
                    close >= reclaim_level if is_long else close <= reclaim_level
                )
                if reclaim_crossed:
                    pending = {
                        "kind": "entry",
                        "requested_notional": max(
                            ladder.BASE_UNIT, prior_exit_notional
                        ),
                        "absolute_target": True,
                        "reason": "reclaim",
                        "signal_index": i,
                        "fill_index": fill_index,
                        "entry_multiplier": 0.0,
                        "completed_htf_source_ts": {},
                    }
                elif signals.entry_mult[i] > 0 and gap_seen:
                    mult = float(signals.entry_mult[i])
                    pending = {
                        "kind": "entry",
                        "requested_notional": ladder.BASE_UNIT * mult,
                        "absolute_target": curve.semantics == "target",
                        "reason": "ladder_lower" if is_long else "ladder_higher",
                        "signal_index": i,
                        "fill_index": fill_index,
                        "entry_multiplier": mult,
                        "completed_htf_source_ts": _event_sources(
                            signals, htfs, i
                        ),
                    }
                elif (
                    close > reclaim_level if is_long else close < reclaim_level
                ):
                    beyond_reclaim += 1
            elif signals.entry_mult[i] > 0:
                mult = float(signals.entry_mult[i])
                pending = {
                    "kind": "entry",
                    "requested_notional": ladder.BASE_UNIT * mult,
                    "absolute_target": curve.semantics == "target",
                    "reason": "initial_ladder",
                    "signal_index": i,
                    "fill_index": fill_index,
                    "entry_multiplier": mult,
                    "completed_htf_source_ts": _event_sources(
                        signals, htfs, i
                    ),
                }

    if qty > 0:
        i = right - 1
        px = float(data.close[i]) * (1.0 - side_sign * slippage_rate)
        close_qty = qty
        notional = close_qty * px
        cash += side_sign * (
            notional - side_sign * commission_rate * notional
        )
        qty = 0.0
        events.append(
            {
                "type": "MTM_FINAL",
                "reason": "END_OF_VALIDATION_MTM",
                "signal_index": i - left,
                "fill_index": i - left,
                "latency_rth_bars": 0,
                "fill_px": px,
                "quantity": close_qty,
                "filled_notional_usd": notional,
                "position_qty_after_fill": 0.0,
                "entry_capacity_usd": ladder.CAPACITY,
                "completed_htf_source_ts": {},
                **clock_fields("signal", i),
                **clock_fields("fill", i),
            }
        )

    min_equity = min(min_equity, cash)
    strategy_pnl = cash - ladder.ACCOUNT_EQUITY
    bh_entry = float(data.open[left]) * (1.0 + side_sign * slippage_rate)
    bh_exit = float(data.close[right - 1]) * (
        1.0 - side_sign * slippage_rate
    )
    bh_pnl = (
        ladder.BASE_UNIT * side_sign * (bh_exit - bh_entry) / bh_entry
        - 2.0 * commission_rate * ladder.BASE_UNIT
    )
    bars = right - left
    metrics = {
        "side": side,
        "capital_return_pct": 100.0 * strategy_pnl / ladder.BASE_UNIT,
        "account_return_pct": 100.0 * strategy_pnl / ladder.ACCOUNT_EQUITY,
        "bh_capital_return_pct": 100.0 * bh_pnl / ladder.BASE_UNIT,
        "alpha_vs_bh_pp": 100.0 * (strategy_pnl - bh_pnl) / ladder.BASE_UNIT,
        "strategy_bh_multiple": strategy_pnl / bh_pnl if bh_pnl > 1e-12 else None,
        "max_drawdown_account_pct": max_dd,
        "minimum_account_equity_usd": min_equity,
        "insolvent": bool(min_equity <= 0.0),
        "binary_tim_pct": 100.0 * held_bars / bars,
        "exposure_weighted_tim_pct": 100.0 * weighted_exposure / bars,
        "peak_mark_to_market_notional_usd": peak_mark_notional,
        "peak_mark_to_market_capacity_pct": (
            100.0 * peak_mark_notional / ladder.CAPACITY
        ),
        "peak_post_fill_notional_usd": peak_post_fill_notional,
        "entry_capacity_breach": peak_post_fill_notional > ladder.CAPACITY + 1e-6,
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "fill_ratio": filled / requested if requested else 1.0,
        "clamp_count": clamp_count,
        "fill_count": fill_count,
        "exit_count": exit_count,
        "reclaim_reentries": reclaim_count,
        "lower_reentries": lower_count if is_long else 0,
        "higher_reentries": lower_count if not is_long else 0,
        "bars_flat_beyond_reclaim": beyond_reclaim,
        "start_ts": int(data.ts[left]),
        "end_ts": int(data.ts[right - 1]),
        "rows": bars,
    }
    return metrics, events


def _assert_metrics_match(
    actual: dict[str, Any], expected: dict[str, Any]
) -> None:
    keys = (
        "capital_return_pct",
        "account_return_pct",
        "bh_capital_return_pct",
        "alpha_vs_bh_pp",
        "max_drawdown_account_pct",
        "binary_tim_pct",
        "exposure_weighted_tim_pct",
        "peak_post_fill_notional_usd",
        "requested_notional_usd",
        "filled_notional_usd",
        "fill_count",
        "exit_count",
        "clamp_count",
        "reclaim_reentries",
        "lower_reentries",
        "bars_flat_beyond_reclaim",
        "rows",
        "start_ts",
        "end_ts",
    )
    mismatches = []
    for key in keys:
        if key not in expected:
            mismatches.append(f"{key}: missing expected")
            continue
        left, right = actual[key], expected[key]
        if isinstance(left, float):
            if not _close_enough(float(left), float(right), rel=1e-9):
                mismatches.append(f"{key}: actual={left} expected={right}")
        elif left != right:
            mismatches.append(f"{key}: actual={left} expected={right}")
    if mismatches:
        raise LadderReplayError(
            "frozen validation metric mismatch: " + "; ".join(mismatches)
        )


def build_spec_and_schedule(
    artifact_dir: str | Path,
    *,
    schedule_path: str | Path,
    account: str = "trb",
    npz_path_override: str | Path | None = None,
    result_path: str | Path | None = None,
) -> dict[str, Any]:
    artifact = Path(artifact_dir).resolve()
    source_result = Path(result_path or artifact / "result.json").resolve()
    if source_result.parent != artifact:
        raise LadderReplayError(
            "source result must be inside the research artifact directory"
        )
    payload = json.loads(source_result.read_text())
    manifest = payload["manifest"]
    if manifest.get("promotion_allowed") or manifest.get("matrix_eligible"):
        raise LadderReplayError("source ladder artifact must remain research-only")
    side = str(manifest.get("side")).upper()
    if side not in {"LONG", "SHORT"}:
        raise LadderReplayError(f"unsupported source side: {side!r}")
    folds = payload.get("outer_folds") or []
    if not folds:
        raise LadderReplayError("source artifact contains no frozen outer fold")
    frozen = folds[-1]
    start, end = frozen["validation"]
    # Frozen artifacts retain an absolute S1 path, but the rolling indicator archive at that
    # path is legitimately refreshed. Permit an explicit immutable copy only when its bytes
    # still match the artifact fingerprint; this relocates data without changing evidence.
    is_overlay = str(manifest.get("family", "")).startswith("ENTRY_")
    npz_path = Path(npz_path_override or manifest["npz"])
    source_start = str(manifest.get("data_start") or "2024-01-01")
    data = ladder.top._load_execution(
        str(manifest["symbol"]).upper(),
        npz_path.resolve().parent,
        source_start,
        "ladder",
        end,
    )
    expected_npz_sha = str(
        manifest.get("control_npz_sha256") or manifest["npz_sha256"]
    )
    try:
        if sha256_file(data.path) != expected_npz_sha:
            raise LadderReplayError(
                "source artifact NPZ fingerprint no longer matches"
            )
        htf_names = (
            ("15m", "1h", "4h", "D") if is_overlay else ladder.TF_ORDER
        )
        htfs = {tf: ladder.top._compress_htf(data, tf) for tf in htf_names}
        curve = ladder.Curve(
            **(frozen["curve"] if is_overlay else frozen["selected_curve"])
        )
        if is_overlay:
            from tools.vec_entry_overlay_walkforward import (
                build_frozen_overlay_signals,
            )

            signals = build_frozen_overlay_signals(
                data, htfs, curve, frozen["selected_candidate"], side
            )
            expected_metrics = frozen["validation_metrics"]
        else:
            signals = ladder._build_signals(
                data, htfs, curve, int(manifest["exit"]["n"]), side
            )
            expected_metrics = frozen["validation_metrics"]
        left = ladder._date_index(data, start)
        right = ladder._date_index(data, end)
        execution_provenance = audit_execution_provenance(
            data, left=left, right=right
        )
        if not execution_provenance["safe_for_exact_engine"]:
            raise LadderReplayError(
                "unsafe synthetic 5m provenance: "
                + str(execution_provenance["reason"])
                + f"; future_parent_rows="
                f"{execution_provenance['future_parent_rows']}"
            )
        metrics, events = trace_frozen_curve(
            data,
            signals,
            htfs,
            curve,
            commission_rate=float(manifest["commission_bps_one_way"])
            / 10_000.0,
            slippage_rate=float(manifest["slippage_bps_one_way"])
            / 10_000.0,
            side=side,
            left=left,
            right=right,
        )
        _assert_metrics_match(metrics, expected_metrics)
    finally:
        data.z.close()

    schedule = _write_schedule_deterministic(schedule_path, events)
    selection_inputs = {
        "fold": int(frozen["fold"]),
        "train": list(frozen["train"]),
        "selected_curve": (
            frozen["curve"] if is_overlay else frozen["selected_curve"]
        ),
        "selected_candidate": (
            frozen["selected_candidate"] if is_overlay else None
        ),
        "selection_score": frozen.get("selection_score"),
        "inner_metrics": frozen.get("inner_metrics"),
    }
    validation_evidence = {
        "validation": list(frozen["validation"]),
        "validation_metrics": frozen["validation_metrics"],
    }
    source_snapshot = artifact / "source_snapshot" / (
        "vec_entry_overlay_walkforward.py"
        if is_overlay
        else "vec_band_ladder_walkforward.py"
    )
    return {
        "kind": SPEC_KIND,
        "version": SPEC_VERSION,
        "promotion_allowed": False,
        "matrix_written": False,
        "source_artifact": str(artifact),
        "source_result": str(source_result),
        "source_result_relative": source_result.name,
        "expected_source_result_sha256": sha256_file(source_result),
        "source_vector_code_sha256": (
            sha256_file(source_snapshot) if source_snapshot.exists() else None
        ),
        "symbol": str(manifest["symbol"]).upper(),
        "side": side,
        "account": account,
        "event_schedule": str(schedule),
        "expected_schedule_sha256": sha256_file(schedule),
        "entry_schedule": {
            "path": str(schedule),
            "sha256": sha256_file(schedule),
            "ordered_events": len(events),
            "executable_actions": sum(
                event["type"] != "CAPACITY_NO_FILL" for event in events
            ),
            "fill_rule": "next_RTH_open_except_final_MTM_at_close",
        },
        "npz_path": str(npz_path.resolve()),
        "expected_npz_sha256": expected_npz_sha,
        "source_npz": {
            "path": str(npz_path.resolve()),
            "sha256": expected_npz_sha,
            "contract": manifest.get("contract"),
            "execution_provenance": execution_provenance,
        },
        "availability_clock": {
            "kind": CLOCK_CONTRACT,
            "ordering": "availability_ts_then_source_row_index",
            "window_rows_sha256": execution_provenance[
                "availability_source_rows_sha256"
            ],
            "native_rows_at_source_ts": True,
            "synthetic_rows_at_parent_close_ts": True,
            "next_rth_fill": "first_strictly_later_availability",
            "preserve_duplicate_availability_rows": True,
        },
        "validation_start": start,
        "validation_end_exclusive": end,
        "source_start": source_start,
        "exit_n": int(manifest["exit"]["n"]),
        "exit_contract": {
            "family": str(manifest["exit"]["family"]),
            "timeframe": str(manifest["exit"]["tf"]),
            "n": int(manifest["exit"]["n"]),
            "signal": (
                "completed_4h_close_below_prior_N_low"
                if side == "LONG"
                else "completed_4h_close_above_prior_N_high"
            ),
        },
        "fold_boundaries": {
            "fold": int(frozen["fold"]),
            "train_start": str(frozen["train"][0]),
            "train_end_exclusive": str(frozen["train"][1]),
            "validation_start": str(start),
            "validation_end_exclusive": str(end),
        },
        "selection_provenance": {
            "frozen_fold": int(frozen["fold"]),
            "selected_from_training_only": True,
            "validation_or_final_metrics_used_for_selection": False,
            "selection_inputs_sha256": _canonical_sha256(selection_inputs),
            "validation_evidence_sha256": _canonical_sha256(
                validation_evidence
            ),
            "selection_inputs": selection_inputs,
        },
        "curve": frozen["curve"] if is_overlay else frozen["selected_curve"],
        "ladder_multipliers": {
            "D": {
                "bottom": float(curve.d_bottom),
                "top": float(curve.d_top),
            },
            "4h": {
                "bottom": float(curve.h4_bottom),
                "top": float(curve.h4_top),
            },
            "1h": {
                "bottom": float(curve.h1_bottom),
                "top": float(curve.h1_top),
            },
            "hard_max": float(manifest["hard_max_multiplier"]),
        },
        "entry_overlay": (
            frozen["selected_candidate"] if is_overlay else None
        ),
        "trigger_contract": {
            "families": (
                [str(manifest["family"])]
                if is_overlay
                else (
                    ["wt_cross_bull", "HH_HL_low_rising_stoch"]
                    if side == "LONG"
                    else ["wt_cross_bear", "LH_LL_high_falling_stoch"]
                )
            ),
            "combination": (
                str(frozen["selected_candidate"]["role"])
                if is_overlay
                else str(curve.trigger)
            ),
            "completed_htf_only": True,
            "timeframes": list(ladder.TF_ORDER),
        },
        "semantics": str(curve.semantics),
        "base_unit_usd": float(manifest["base_unit_usd"]),
        "account_equity_usd": float(manifest["account_equity_usd"]),
        "hard_capacity_usd": float(manifest["hard_capacity_usd"]),
        "commission_bps_one_way": float(manifest["commission_bps_one_way"]),
        "slippage_bps_one_way": float(manifest["slippage_bps_one_way"]),
        "expected_metrics": metrics,
        "expected_counts": {
            "entry_fills": sum(
                event["type"] in {"ENTRY", "AUGMENT"} for event in events
            ),
            "technical_exits": sum(event["type"] == "EXIT" for event in events),
            "mtm_final": sum(event["type"] == "MTM_FINAL" for event in events),
            "actions": sum(
                event["type"] != "CAPACITY_NO_FILL" for event in events
            ),
            "capacity_no_fill_audits": sum(
                event["type"] == "CAPACITY_NO_FILL" for event in events
            ),
        },
        "accounting_tolerance_bp": 1e-4,
    }


def emit_replay_bundle(
    artifact_dir: str | Path,
    *,
    account: str = "trb",
    npz_path_override: str | Path | None = None,
    result_path: str | Path | None = None,
) -> dict[str, Any]:
    """Materialize one parser-validated replay bundle or a blocked receipt.

    The function deliberately returns a receipt instead of turning a valid
    vector artifact into a failed campaign. Exact-engine eligibility is a
    stricter tier. A blocked receipt is machine-readable evidence that no spec
    may be handed to ``backtest_v8_engine``.
    """
    artifact = Path(artifact_dir).resolve()
    bundle = artifact / "research_ladder_replay"
    bundle.mkdir(parents=True, exist_ok=True)
    source_result = Path(result_path or artifact / "result.json").resolve()
    receipt_path = bundle / "receipt.json"
    try:
        schedule = bundle / "frozen_ladder_schedule.jsonl.gz"
        spec = build_spec_and_schedule(
            artifact,
            schedule_path=schedule,
            account=account,
            npz_path_override=npz_path_override,
            result_path=source_result,
        )
        spec_path = bundle / "research_ladder_spec.json"
        spec_path.write_text(
            json.dumps(spec, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
        # Parse the exact bytes that the engine will consume before advertising
        # the bundle as runnable.
        parsed = LadderReplayAdapter(spec_path)
        receipt = {
            "status": "READY_FOR_EXACT_ENGINE",
            "tier": "VEC_RESEARCH_REPLAY_SPEC_V3",
            "source_result": str(source_result),
            "source_result_sha256": sha256_file(source_result),
            "spec": str(spec_path),
            "spec_sha256": sha256_file(spec_path),
            "schedule": str(schedule),
            "schedule_sha256": sha256_file(schedule),
            "actions": len(parsed.actions),
            "synthetic_execution_provenance": spec["source_npz"][
                "execution_provenance"
            ],
            "selection_final_leakage": False,
            "promotion_allowed": False,
            "matrix_written": False,
        }
    except (LadderReplayError, KeyError, ValueError) as exc:
        receipt = {
            "status": "BLOCKED_FAIL_CLOSED",
            "tier": "VEC_RESEARCH_REPLAY_SPEC_V3",
            "source_result": str(source_result),
            "source_result_sha256": (
                sha256_file(source_result) if source_result.exists() else None
            ),
            "reason": str(exc),
            "spec_emitted": False,
            "selection_final_leakage": False,
            "promotion_allowed": False,
            "matrix_written": False,
        }
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    return receipt


class LadderReplayAdapter:
    """Validated action schedule and faithful-engine audit accumulator."""

    def __init__(self, spec_path: str | Path):
        self.spec_path = Path(spec_path).resolve()
        self.spec = json.loads(self.spec_path.read_text())
        self._validate_spec()
        self.symbol = str(self.spec["symbol"]).upper()
        self.position_side = str(self.spec["side"]).upper()
        self.account = str(self.spec["account"])
        self.capacity = float(self.spec["hard_capacity_usd"])
        schedule = Path(self.spec["event_schedule"])
        if not schedule.is_absolute():
            schedule = (self.spec_path.parent / schedule).resolve()
        self.schedule_path = schedule
        if sha256_file(schedule) != str(self.spec["expected_schedule_sha256"]):
            raise LadderReplayError("event schedule hash mismatch")
        with gzip.open(schedule, "rt") as fh:
            self.events = [json.loads(line) for line in fh if line.strip()]
        self.audit_events = [
            event
            for event in self.events
            if str(event.get("type", "")).upper() == "CAPACITY_NO_FILL"
        ]
        self.actions = self._build_actions()
        self._by_ts: dict[int, list[LadderReplayAction]] = {}
        self._by_clock: dict[tuple[int, int], list[LadderReplayAction]] = {}
        for action in self.actions:
            self._by_ts.setdefault(action.fill_ts, []).append(action)
            source_row = action.source_event.get("fill_source_row_index")
            if source_row is not None:
                self._by_clock.setdefault(
                    (action.fill_ts, int(source_row)), []
                ).append(action)
        self.runtime_clock: list[dict[str, int]] = []
        self.executed: list[dict[str, Any]] = []
        self.refused: list[dict[str, Any]] = []
        self._observed_rows = 0
        self._held_rows = 0
        self._weighted_exposure = 0.0
        self._peak_post_fill_notional = 0.0
        self._requested = sum(
            float(event["requested_notional_usd"])
            for event in self.audit_events
        )
        self._filled = 0.0
        self._clamps = len(self.audit_events)

    def _validate_spec(self) -> None:
        if self.spec.get("kind") != SPEC_KIND:
            raise LadderReplayError(f"wrong spec kind: {self.spec.get('kind')!r}")
        version = int(self.spec.get("version", 0))
        if version == PARENT_UNSAFE_SPEC_VERSION:
            raise LadderReplayError(
                "v2 ladder replay specs are fail-closed: they do not bind "
                "the shared synthetic parent-close availability clock"
            )
        if version not in {LEGACY_SPEC_VERSION, SPEC_VERSION}:
            raise LadderReplayError("unsupported ladder replay spec version")
        required = {
            "symbol",
            "side",
            "account",
            "event_schedule",
            "expected_schedule_sha256",
            "npz_path",
            "expected_npz_sha256",
            "hard_capacity_usd",
            "commission_bps_one_way",
            "slippage_bps_one_way",
            "expected_metrics",
            "expected_counts",
        }
        missing = sorted(required - set(self.spec))
        if missing:
            raise LadderReplayError(f"missing spec fields: {missing}")
        if self.spec.get("promotion_allowed") or self.spec.get("matrix_written"):
            raise LadderReplayError("ladder replay cannot allow promotion/matrix writes")
        if str(self.spec["side"]).upper() not in {"LONG", "SHORT"}:
            raise LadderReplayError("ladder replay side must be LONG or SHORT")
        if float(self.spec["hard_capacity_usd"]) <= 0:
            raise LadderReplayError("hard_capacity_usd must be positive")
        if version == SPEC_VERSION:
            strict_required = {
                "source_result",
                "expected_source_result_sha256",
                "source_npz",
                "entry_schedule",
                "fold_boundaries",
                "selection_provenance",
                "ladder_multipliers",
                "exit_n",
                "exit_contract",
                "availability_clock",
            }
            strict_missing = sorted(strict_required - set(self.spec))
            if strict_missing:
                raise LadderReplayError(
                    f"missing v2 replay provenance fields: {strict_missing}"
                )
            source_result = Path(self.spec["source_result"]).resolve()
            if not source_result.is_file():
                raise LadderReplayError("source result is unavailable")
            if sha256_file(source_result) != str(
                self.spec["expected_source_result_sha256"]
            ):
                raise LadderReplayError("source result fingerprint mismatch")
            selection = self.spec["selection_provenance"]
            if (
                not bool(selection.get("selected_from_training_only"))
                or bool(
                    selection.get(
                        "validation_or_final_metrics_used_for_selection"
                    )
                )
            ):
                raise LadderReplayError(
                    "frozen selection is not proven training-only"
                )
            if _canonical_sha256(
                selection.get("selection_inputs")
            ) != str(selection.get("selection_inputs_sha256")):
                raise LadderReplayError("selection input fingerprint mismatch")
            bounds = self.spec["fold_boundaries"]
            if (
                str(bounds.get("train_end_exclusive"))
                != str(bounds.get("validation_start"))
                or str(bounds.get("validation_start"))
                != str(self.spec.get("validation_start"))
                or str(bounds.get("validation_end_exclusive"))
                != str(self.spec.get("validation_end_exclusive"))
            ):
                raise LadderReplayError(
                    "non-contiguous or mismatched frozen fold boundaries"
                )
            provenance = self.spec["source_npz"].get(
                "execution_provenance", {}
            )
            if not bool(provenance.get("safe_for_exact_engine")):
                raise LadderReplayError(
                    "unsafe synthetic 5m provenance in replay spec"
                )
            clock = self.spec["availability_clock"]
            if (
                str(clock.get("kind")) != CLOCK_CONTRACT
                or str(clock.get("ordering"))
                != "availability_ts_then_source_row_index"
                or not bool(clock.get("native_rows_at_source_ts"))
                or not bool(clock.get("synthetic_rows_at_parent_close_ts"))
                or str(clock.get("next_rth_fill"))
                != "first_strictly_later_availability"
                or not bool(clock.get("preserve_duplicate_availability_rows"))
                or str(clock.get("window_rows_sha256"))
                != str(provenance.get("availability_source_rows_sha256"))
            ):
                raise LadderReplayError(
                    "invalid or mismatched availability clock contract"
                )
            if (
                str(self.spec["source_npz"].get("sha256"))
                != str(self.spec["expected_npz_sha256"])
                or str(self.spec["entry_schedule"].get("sha256"))
                != str(self.spec["expected_schedule_sha256"])
            ):
                raise LadderReplayError(
                    "nested provenance fingerprint mismatch"
                )
            exit_contract = self.spec["exit_contract"]
            if (
                str(exit_contract.get("family")) != "E02_DONCHIAN"
                or str(exit_contract.get("timeframe")) != "4h"
                or int(exit_contract.get("n", 0)) <= 0
                or int(exit_contract["n"]) != int(self.spec["exit_n"])
            ):
                raise LadderReplayError(
                    "unsupported or inconsistent ladder exit contract"
                )

    def _build_actions(self) -> list[LadderReplayAction]:
        out: list[LadderReplayAction] = []
        qty = 0.0
        last_fill_index = -1
        for event in self.events:
            kind = str(event.get("type", "")).upper()
            if kind == "CAPACITY_NO_FILL":
                if (
                    float(event.get("requested_notional_usd", 0.0)) <= 0.0
                    or float(event.get("filled_notional_usd", -1.0)) != 0.0
                    or not bool(event.get("clamped"))
                ):
                    raise LadderReplayError("invalid capacity no-fill audit event")
                continue
            if kind not in {"ENTRY", "AUGMENT", "EXIT", "MTM_FINAL"}:
                raise LadderReplayError(f"unsupported ladder event: {kind}")
            fill_index = int(event["fill_index"])
            signal_index = int(event["signal_index"])
            latency = int(event["latency_rth_bars"])
            if fill_index <= last_fill_index:
                raise LadderReplayError("fill indices must be strictly increasing")
            last_fill_index = fill_index
            if kind == "MTM_FINAL":
                if latency != 0 or signal_index != fill_index:
                    raise LadderReplayError("MTM_FINAL must occur on its mark row")
            elif int(self.spec.get("version", 0)) == SPEC_VERSION:
                required_clock = {
                    "signal_availability_ts",
                    "signal_source_ts",
                    "signal_source_row_index",
                    "fill_availability_ts",
                    "fill_source_ts",
                    "fill_source_row_index",
                }
                missing_clock = sorted(required_clock - set(event))
                if missing_clock:
                    raise LadderReplayError(
                        f"event missing v3 clock fields: {missing_clock}"
                    )
                if (
                    latency != 1
                    or int(event["fill_availability_ts"])
                    <= int(event["signal_availability_ts"])
                    or int(event["fill_ts"])
                    != int(event["fill_availability_ts"])
                    or int(event["signal_ts"])
                    != int(event["signal_availability_ts"])
                ):
                    raise LadderReplayError(
                        "ladder signal must fill at the first strictly later "
                        "availability"
                    )
            elif latency != 1 or fill_index != signal_index + 1:
                raise LadderReplayError("ladder signal must fill at next RTH row")
            fill_px = float(event["fill_px"])
            action_qty = float(event["quantity"])
            if fill_px <= 0 or action_qty <= 0:
                raise LadderReplayError("non-positive fill price/quantity")
            if kind in {"ENTRY", "AUGMENT"}:
                if kind == "ENTRY" and qty > 1e-9:
                    raise LadderReplayError("ENTRY while replay ledger is open")
                if kind == "AUGMENT" and qty <= 1e-9:
                    raise LadderReplayError("AUGMENT while replay ledger is flat")
                qty += action_qty
                post = qty * fill_px
                if not _close_enough(
                    post, float(event["post_fill_notional_usd"]), rel=1e-9
                ):
                    raise LadderReplayError("post-fill notional mismatch")
                if post > self.capacity + 1e-6:
                    raise LadderReplayError("scheduled entry breaches hard capacity")
                action = "OPEN" if kind == "ENTRY" else "AUGMENT"
                full_close = False
                leaf = str(event["reason"]).upper()
            else:
                if qty <= 1e-9 or not _close_enough(qty, action_qty, rel=1e-9):
                    raise LadderReplayError("full-close quantity does not match open ledger")
                qty = 0.0
                action = "CLOSE"
                full_close = True
                leaf = (
                    "E02_DONCHIAN_4H_N30"
                    if kind == "EXIT"
                    else "END_OF_VALIDATION_MTM"
                )
            out.append(
                LadderReplayAction(
                    fill_ts=int(event["fill_ts"]),
                    fill_index=fill_index,
                    signal_ts=int(event["signal_ts"]),
                    signal_index=signal_index,
                    event_type=kind,
                    action=action,
                    order_side=(
                        ("SELL" if self.position_side == "LONG" else "BUY")
                        if full_close
                        else ("BUY" if self.position_side == "LONG" else "SELL")
                    ),
                    position_side=self.position_side,
                    fill_price=fill_px,
                    quantity=action_qty,
                    full_close=full_close,
                    reason=f"{REASON_PREFIX}__{leaf}",
                    source_event=event,
                )
            )
        if qty > 1e-9:
            raise LadderReplayError("ladder schedule ends with open quantity")
        expected_actions = int(self.spec["expected_counts"]["actions"])
        if len(out) != expected_actions:
            raise LadderReplayError(
                f"action count mismatch: {len(out)} != {expected_actions}"
            )
        return out

    def validate_runtime(
        self,
        *,
        account: str,
        symbols: list[str],
        mode: str,
        seed_positions_file: str,
        round_trip_cost_pct: float,
    ) -> None:
        if mode != "tradier":
            raise LadderReplayError("ladder replay is tradier-backtest only")
        if account != self.account or [s.upper() for s in symbols] != [self.symbol]:
            raise LadderReplayError("ladder replay runtime account/symbol mismatch")
        if seed_positions_file:
            raise LadderReplayError("seeded live positions are forbidden")
        expected = 2.0 * float(self.spec["commission_bps_one_way"]) / 100.0
        if abs(float(round_trip_cost_pct) - expected) > 1e-12:
            raise LadderReplayError(
                f"cost mismatch: runtime={round_trip_cost_pct} expected={expected}"
            )

    def validate_npz(self, npz_path: str | Path) -> str:
        actual_sha = sha256_file(npz_path)
        if actual_sha != str(self.spec["expected_npz_sha256"]):
            raise LadderReplayError("NPZ fingerprint mismatch")
        data = ladder.top._load_execution(
            self.symbol,
            Path(npz_path).resolve().parent,
            str(self.spec.get("source_start") or self.spec["validation_start"]),
            "ladder",
            str(self.spec["validation_end_exclusive"]),
        )
        try:
            version = int(self.spec.get("version", 0))
            left = ladder._date_index(
                data, str(self.spec["validation_start"])
            )
            right = ladder._date_index(
                data, str(self.spec["validation_end_exclusive"])
            )
            if version == SPEC_VERSION:
                live_provenance = audit_execution_provenance(
                    data,
                    left=left,
                    right=right,
                )
                if not live_provenance["safe_for_exact_engine"]:
                    raise LadderReplayError(
                        "loaded NPZ has unsafe synthetic 5m provenance: "
                        + str(live_provenance["reason"])
                    )
                expected_provenance = self.spec["source_npz"][
                    "execution_provenance"
                ]
                for key in (
                    "rows",
                    "synthetic_rows",
                    "future_parent_rows",
                    "parent_lag_min_seconds",
                    "parent_lag_max_seconds",
                    "shared_availability_rows",
                    "availability_source_rows_sha256",
                ):
                    if live_provenance.get(key) != expected_provenance.get(key):
                        raise LadderReplayError(
                            f"execution provenance mismatch for {key}"
                        )
                self.runtime_clock = [
                    {
                        "availability_ts": int(data.ts[i]),
                        "source_ts": int(data.source_ts[i]),
                        "source_row_index": int(data.full_indices[i]),
                        "clock_index": int(i),
                    }
                    for i in range(left, right)
                ]
            elif bool(np.any(data.synthetic[left:right])):
                raise LadderReplayError(
                    "legacy ladder replay spec is unsafe for synthetic 5m "
                    "rows because it does not bind the parent-close clock"
                )
            is_overlay = bool(self.spec.get("entry_overlay"))
            htf_names = (
                ("15m", "1h", "4h", "D")
                if is_overlay
                else ladder.TF_ORDER
            )
            htfs = {
                tf: ladder.top._compress_htf(data, tf) for tf in htf_names
            }
            curve = ladder.Curve(**self.spec["curve"])
            if is_overlay:
                from tools.vec_entry_overlay_walkforward import (
                    build_frozen_overlay_signals,
                )

                signals = build_frozen_overlay_signals(
                    data,
                    htfs,
                    curve,
                    self.spec["entry_overlay"],
                    self.position_side,
                )
            else:
                exit_n = (
                    int(self.spec["exit_contract"]["n"])
                    if int(self.spec.get("version", 0)) == SPEC_VERSION
                    else int(self.spec.get("exit_n", 30))
                )
                signals = ladder._build_signals(
                    data,
                    htfs,
                    curve,
                    exit_n,
                    self.position_side,
                )
            self._validate_actions_against_npz(data, htfs, signals)
        finally:
            data.z.close()
        return actual_sha

    def _validate_actions_against_npz(
        self,
        data: Any,
        htfs: dict[str, Any],
        signals: ladder.SignalData,
    ) -> None:
        version = int(self.spec.get("version", 0))
        for action in self.actions:
            event = action.source_event
            if version == SPEC_VERSION:
                source_fill_index = int(event["fill_clock_index"])
                source_signal_index = int(event["signal_clock_index"])
                if int(data.full_indices[source_fill_index]) != int(
                    event["fill_source_row_index"]
                ):
                    raise LadderReplayError(
                        "fill source-row identity mismatch in loaded NPZ"
                    )
                if int(data.full_indices[source_signal_index]) != int(
                    event["signal_source_row_index"]
                ):
                    raise LadderReplayError(
                        "signal source-row identity mismatch in loaded NPZ"
                    )
                expected_next = next_strictly_later_index(
                    data.ts,
                    source_signal_index,
                    len(data.ts),
                )
                if action.event_type != "MTM_FINAL" and (
                    expected_next is None or expected_next != source_fill_index
                ):
                    raise LadderReplayError(
                        "fill is not the first strictly later availability row"
                    )
            else:
                source_fill_index = int(
                    event.get("source_fill_index", action.fill_index)
                )
                source_signal_index = int(
                    event.get("source_signal_index", action.signal_index)
                )
            if source_fill_index >= len(data.ts):
                raise LadderReplayError(
                    "fill index exceeds loaded validation rows"
                )
            if int(data.ts[source_fill_index]) != action.fill_ts:
                raise LadderReplayError("fill timestamp/index mismatch in loaded NPZ")
            if int(data.ts[source_signal_index]) != action.signal_ts:
                raise LadderReplayError("signal timestamp/index mismatch in loaded NPZ")
            for tf, source_ts in (
                event.get("completed_htf_source_ts") or {}
            ).items():
                if int(source_ts) > action.signal_ts:
                    raise LadderReplayError(
                        f"future {tf} HTF source at ladder signal {action.signal_ts}"
                    )
            if action.event_type in {"ENTRY", "AUGMENT"} and event.get(
                "reason"
            ) != "reclaim":
                actual_mult = float(signals.entry_mult[source_signal_index])
                if not _close_enough(
                    actual_mult, float(event["entry_multiplier"]), rel=1e-9
                ):
                    raise LadderReplayError("ladder multiplier signal mismatch")
                actual_sources = _event_sources(
                    signals, htfs, source_signal_index
                )
                if actual_sources != {
                    str(k): int(v)
                    for k, v in event["completed_htf_source_ts"].items()
                }:
                    raise LadderReplayError("completed HTF event source mismatch")
            if action.event_type == "EXIT" and not bool(
                signals.exit_event[source_signal_index]
            ):
                raise LadderReplayError("scheduled exit is absent from source signal")
            raw = (
                float(data.close[source_fill_index])
                if action.event_type == "MTM_FINAL"
                else float(data.open[source_fill_index])
            )
            expected_fill = self.expected_fill_from_loaded_bar(action, raw)
            if not _close_enough(expected_fill, action.fill_price):
                raise LadderReplayError("fill price does not match loaded NPZ bar")
        for event in self.audit_events:
            if version == SPEC_VERSION:
                source_signal_index = int(event["signal_clock_index"])
                source_fill_index = int(event["fill_clock_index"])
                expected_next = next_strictly_later_index(
                    data.ts, source_signal_index, len(data.ts)
                )
                if expected_next != source_fill_index:
                    raise LadderReplayError(
                        "capacity no-fill did not occur at first strictly "
                        "later availability"
                    )
            else:
                source_signal_index = int(event["source_signal_index"])
                source_fill_index = int(event["source_fill_index"])
                if source_fill_index != source_signal_index + 1:
                    raise LadderReplayError(
                        "capacity no-fill did not occur at next RTH row"
                    )
            if int(data.ts[source_signal_index]) != int(event["signal_ts"]):
                raise LadderReplayError("capacity no-fill signal timestamp mismatch")
            if int(data.ts[source_fill_index]) != int(event["fill_ts"]):
                raise LadderReplayError("capacity no-fill timestamp mismatch")
            actual_mult = float(signals.entry_mult[source_signal_index])
            if not _close_enough(
                actual_mult, float(event["entry_multiplier"]), rel=1e-9
            ):
                raise LadderReplayError(
                    "capacity no-fill multiplier signal mismatch"
                )
            actual_sources = _event_sources(
                signals, htfs, source_signal_index
            )
            if actual_sources != {
                str(k): int(v)
                for k, v in event["completed_htf_source_ts"].items()
            }:
                raise LadderReplayError(
                    "capacity no-fill completed HTF source mismatch"
                )

    def expected_fill_from_loaded_bar(
        self, action: LadderReplayAction, raw_price: float
    ) -> float:
        slip = float(self.spec["slippage_bps_one_way"]) / 10_000.0
        side_sign = 1.0 if self.position_side == "LONG" else -1.0
        return raw_price * (
            1.0 - side_sign * slip
            if action.full_close
            else 1.0 + side_sign * slip
        )

    def actions_at(
        self, ts: int, source_row_index: int | None = None
    ) -> list[LadderReplayAction]:
        if int(self.spec.get("version", 0)) == SPEC_VERSION:
            if source_row_index is None:
                raise LadderReplayError(
                    "v3 replay actions require source_row_index identity"
                )
            return list(
                self._by_clock.get(
                    (int(ts), int(source_row_index)),
                    (),
                )
            )
        return list(self._by_ts.get(int(ts), ()))

    def record_result(
        self,
        action: LadderReplayAction,
        *,
        result: str,
        actual_quantity: float,
        actual_price: float,
        post_position_qty: float,
    ) -> None:
        row = {
            "fill_ts": action.fill_ts,
            "event_type": action.event_type,
            "result": result,
            "expected_quantity": action.quantity,
            "actual_quantity": actual_quantity,
            "expected_price": action.fill_price,
            "actual_price": actual_price,
            "expected_post_position_qty": float(
                action.source_event["position_qty_after_fill"]
            ),
            "actual_post_position_qty": post_position_qty,
        }
        if result != "SUCCESS":
            self.refused.append(row)
            return
        self.executed.append(row)
        if action.event_type in {"ENTRY", "AUGMENT"}:
            event = action.source_event
            self._requested += float(event["requested_notional_usd"])
            self._filled += float(event["filled_notional_usd"])
            self._clamps += int(bool(event["clamped"]))
            post = post_position_qty * actual_price
            self._peak_post_fill_notional = max(
                self._peak_post_fill_notional, post
            )
            if post > self.capacity + 1e-6:
                raise LadderReplayError(
                    f"faithful engine entry capacity breach: {post}"
                )

    def observe_bar(self, *, close_price: float, position_qty: float) -> None:
        if close_price <= 0 or position_qty < 0:
            raise LadderReplayError("invalid bar observation")
        self._observed_rows += 1
        self._held_rows += int(position_qty > 1e-9)
        self._weighted_exposure += (
            min(self.capacity, position_qty * close_price) / self.capacity
        )

    def final_audit(self) -> dict[str, Any]:
        expected = self.spec["expected_metrics"]
        binary = 100.0 * self._held_rows / max(1, self._observed_rows)
        weighted = (
            100.0 * self._weighted_exposure / max(1, self._observed_rows)
        )
        tim = {
            "rows": self._observed_rows,
            "held_rows": self._held_rows,
            "expected_binary_pct": float(expected["binary_tim_pct"]),
            "actual_binary_pct": binary,
            "binary_delta_pp": binary - float(expected["binary_tim_pct"]),
            "expected_weighted_pct": float(expected["exposure_weighted_tim_pct"]),
            "actual_weighted_pct": weighted,
            "weighted_delta_pp": weighted
            - float(expected["exposure_weighted_tim_pct"]),
        }
        tim["status"] = (
            "PASS"
            if (
                self._observed_rows == int(expected["rows"])
                and abs(tim["binary_delta_pp"]) <= 1e-8
                and abs(tim["weighted_delta_pp"]) <= 1e-8
            )
            else "FAIL"
        )
        mismatches = [
            row
            for row in self.executed
            if (
                not _close_enough(
                    float(row["actual_quantity"]),
                    float(row["expected_quantity"]),
                    rel=1e-9,
                )
                or not _close_enough(
                    float(row["actual_price"]),
                    float(row["expected_price"]),
                )
                or not _close_enough(
                    float(row["actual_post_position_qty"]),
                    float(row["expected_post_position_qty"]),
                    rel=1e-9,
                )
            )
        ]
        capacity = {
            "expected_capacity_usd": self.capacity,
            "expected_peak_post_fill_notional_usd": float(
                expected["peak_post_fill_notional_usd"]
            ),
            "actual_peak_post_fill_notional_usd": self._peak_post_fill_notional,
            "requested_notional_usd": self._requested,
            "filled_notional_usd": self._filled,
            "clamp_count": self._clamps,
            "capacity_no_fill_audit_events": len(self.audit_events),
            "entry_capacity_breach": (
                self._peak_post_fill_notional > self.capacity + 1e-6
            ),
        }
        capacity["status"] = (
            "PASS"
            if (
                not capacity["entry_capacity_breach"]
                and _close_enough(
                    self._requested,
                    float(expected["requested_notional_usd"]),
                    rel=1e-9,
                )
                and _close_enough(
                    self._filled,
                    float(expected["filled_notional_usd"]),
                    rel=1e-9,
                )
                and self._clamps == int(expected["clamp_count"])
            )
            else "FAIL"
        )
        schedule_status = (
            "PASS"
            if (
                len(self.executed) == len(self.actions)
                and not self.refused
                and not mismatches
            )
            else "FAIL"
        )
        return {
            "status": (
                "PASS"
                if schedule_status == tim["status"] == capacity["status"] == "PASS"
                else "FAIL"
            ),
            "schedule_status": schedule_status,
            "scheduled": len(self.actions),
            "executed": len(self.executed),
            "refused": self.refused,
            "mismatches": mismatches,
            "time_in_market": tim,
            "capacity": capacity,
            "completed_htf_only": True,
            "next_rth_open_fills": True,
            "semantics": self.spec["semantics"],
            "signal_parity": True,
            "promotion_allowed": False,
            "matrix_written": False,
        }

    def accounting_audit(
        self, executed_trades: list[dict[str, Any]]
    ) -> dict[str, Any]:
        closes = [
            row
            for row in executed_trades
            if str(row.get("reason", "")).startswith(REASON_PREFIX)
            and row.get("pnl_dollars") is not None
        ]
        pnl = sum(float(row["pnl_dollars"]) for row in closes)
        actual = 100.0 * pnl / float(self.spec["base_unit_usd"])
        expected = float(self.spec["expected_metrics"]["capital_return_pct"])
        delta_bp = 100.0 * (actual - expected)
        status = (
            "PASS"
            if abs(delta_bp) <= float(self.spec["accounting_tolerance_bp"])
            else "FAIL"
        )
        return {
            "status": status,
            "expected_capital_return_pct": expected,
            "actual_capital_return_pct": actual,
            "delta_bp": delta_bp,
            "total_pnl_dollars": pnl,
            "closed_lifecycles": len(closes),
            "commission_bps_one_way": float(
                self.spec["commission_bps_one_way"]
            ),
            "slippage_bps_one_way": float(self.spec["slippage_bps_one_way"]),
            "promotion_allowed": False,
        }
