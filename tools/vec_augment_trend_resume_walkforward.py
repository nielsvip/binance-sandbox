#!/usr/bin/env python3
"""Causal, research-only reconstruction of the removed trend-resume augment.

The active stock manager no longer contains ``AUGMENT_TREND_RESUME_ENABLED``.
This adapter therefore models the last real implementation found in
``backups/before_desktop_tradier_fixes_20260721.py`` without claiming live
parity.  Frozen ladder entries, E02 4h N=30 exits, resting reclaim, capacity,
costs, and chronological folds are inherited from the accepted control.
Only additive scale-ins are added while a position is already open.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import itertools
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vec_band_ladder_walkforward as ladder  # noqa: E402
import vec_top_exit_campaign as top  # noqa: E402


FAMILY = "ENTRY_AUGMENT_TREND_RESUME_ENABLED"


@dataclasses.dataclass(frozen=True)
class Candidate:
    min_gain_pct: float
    rsi_boundary: float
    add_mult: float

    @property
    def label(self) -> str:
        raw = json.dumps(dataclasses.asdict(self), sort_keys=True)
        return f"TREND_RESUME_{hashlib.sha256(raw.encode()).hexdigest()[:10]}"


def candidates() -> list[Candidate]:
    # Includes the last-real source setting: gain=.5, RSI=70/30, add=.5x.
    return [
        Candidate(float(gain), float(rsi), float(mult))
        for gain, rsi, mult in itertools.product(
            (0.5, 1.0, 2.0, 3.0),
            (60.0, 70.0, 80.0, 100.0),
            (0.25, 0.5),
        )
    ]


def _base(data: top.ExecutionData, *keys: str, default: float) -> np.ndarray:
    for key in keys:
        if key in data.z.files:
            return np.asarray(data.z[key], dtype=np.float64)[data.full_indices]
    return np.full(len(data.ts), default, dtype=np.float64)


def trend_resume_mask(
    data: top.ExecutionData, side: str, rsi_boundary: float
) -> tuple[np.ndarray, dict[str, Any]]:
    """Mirror the last-real 5m state predicate; no HTF interpolation is used."""
    close = np.asarray(data.close, dtype=np.float64)
    basis = _base(data, "dc_basis_5m", default=np.nan)
    k = _base(data, "stoch_k_5m", "k_5m", default=50.0)
    d = _base(data, "stoch_d_5m", "d_5m", default=50.0)
    rsi = _base(data, "rsi_5m", default=50.0)
    valid = np.isfinite(basis) & (basis > 0)
    if side == "LONG":
        mask = valid & (close > basis) & (k > d) & (rsi < rsi_boundary)
        rsi_rule = f"rsi_5m < {rsi_boundary:g}"
    else:
        lower = 100.0 - rsi_boundary
        mask = valid & (close < basis) & (k < d) & (rsi > lower)
        rsi_rule = f"rsi_5m > {lower:g}"
    return np.ascontiguousarray(mask), {
        "source": "backups/before_desktop_tradier_fixes_20260721.py",
        "inputs": [
            "close_5m",
            "dc_basis_5m",
            "stoch_k_5m",
            "stoch_d_5m",
            "rsi_5m",
        ],
        "rsi_rule": rsi_rule,
        "future_htf_source_count": 0,
        "signal_rows": int(np.count_nonzero(mask)),
    }


def simulate(
    data: top.ExecutionData,
    signals: ladder.SignalData,
    curve: ladder.Curve,
    augment_mask: np.ndarray,
    candidate: Candidate,
    left: int,
    right: int,
    commission_rate: float,
    slippage_rate: float,
    side: str,
) -> dict[str, Any]:
    """Stateful accounting with additive trend-resume requests.

    The control's OPEN/EXIT/RECLAIM schedule remains authoritative.  An
    augment never opens a flat position and never changes exit timing.
    """
    side_sign = 1.0 if side == "LONG" else -1.0
    is_long = side == "LONG"
    cash = ladder.ACCOUNT_EQUITY
    qty = 0.0
    avg_entry = math.nan
    last_exit_fill = math.nan
    reclaim_level = math.nan
    prior_exit_notional = 0.0
    gap_seen = False
    pending: dict[str, Any] | None = None
    peak_equity = min_equity = ladder.ACCOUNT_EQUITY
    max_dd = 0.0
    requested = filled = 0.0
    clamp_count = fill_count = exit_count = 0
    reclaim_count = lower_count = augment_fill_count = augment_signal_count = 0
    augment_request_count = 0
    augment_requested = augment_filled = 0.0
    held_bars = 0
    weighted_exposure = 0.0
    peak_mark_notional = peak_post_fill_notional = 0.0
    beyond_reclaim = 0

    def equity(px: float) -> float:
        return cash + qty * px

    def has_position() -> bool:
        return side_sign * qty > 1e-12

    for i in range(left, right):
        op = float(data.open[i])
        close = float(data.close[i])
        if pending is not None:
            kind = pending["kind"]
            if kind == "exit" and has_position():
                px = op * (1.0 - side_sign * slippage_rate)
                close_qty = abs(qty)
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
                avg_entry = math.nan
                gap_seen = False
                exit_count += 1
            elif kind == "entry":
                px = op * (1.0 + side_sign * slippage_rate)
                old_shares = abs(qty)
                current = old_shares * px
                want_requested = float(pending["requested_notional"])
                want = (
                    max(0.0, want_requested - current)
                    if pending.get("absolute_target")
                    else want_requested
                )
                capacity_left = max(0.0, ladder.CAPACITY - current)
                actual = min(want, capacity_left)
                requested += max(0.0, want)
                filled += actual
                clamp_count += int(actual + 1e-9 < want)
                if pending.get("reason") in {"trend_resume", "dc_tier"}:
                    augment_requested += max(0.0, want)
                    augment_filled += actual
                if actual > 0:
                    new_shares = actual / px
                    prior_cost = (
                        old_shares * avg_entry
                        if old_shares > 0 and math.isfinite(avg_entry)
                        else 0.0
                    )
                    avg_entry = (prior_cost + new_shares * px) / (
                        old_shares + new_shares
                    )
                    cash -= side_sign * actual + commission_rate * actual
                    qty += side_sign * new_shares
                    peak_post_fill_notional = max(
                        peak_post_fill_notional, abs(qty) * px
                    )
                    fill_count += 1
                    reclaim_count += int(pending.get("reason") == "reclaim")
                    lower_count += int(
                        pending.get("reason")
                        in {"ladder_lower", "ladder_higher"}
                    )
                    augment_fill_count += int(
                        pending.get("reason") in {"trend_resume", "dc_tier"}
                    )
            pending = None

        mark_equity = equity(close)
        min_equity = min(min_equity, mark_equity)
        peak_equity = max(peak_equity, mark_equity)
        if peak_equity > 0:
            max_dd = max(
                max_dd, 100.0 * (peak_equity - mark_equity) / peak_equity
            )
        notional = abs(qty) * close
        peak_mark_notional = max(peak_mark_notional, notional)
        held_bars += int(has_position())
        weighted_exposure += min(ladder.CAPACITY, notional) / ladder.CAPACITY
        if i + 1 >= right:
            continue

        if has_position():
            if signals.exit_event[i]:
                ref = float(signals.exit_ref[i])
                if not math.isfinite(ref):
                    ref = float(data.high[i] if is_long else data.low[i])
                pending = {"kind": "exit", "ref": ref}
            elif signals.entry_mult[i] > 0:
                pending = {
                    "kind": "entry",
                    "requested_notional": (
                        ladder.BASE_UNIT * float(signals.entry_mult[i])
                    ),
                    "absolute_target": curve.semantics == "target",
                    "reason": "ladder_add",
                }
            elif augment_mask[i]:
                gain = (
                    side_sign * (close / avg_entry - 1.0) * 100.0
                    if math.isfinite(avg_entry) and avg_entry > 0
                    else -math.inf
                )
                if gain >= candidate.min_gain_pct:
                    augment_signal_count += 1
                    tier_profile = getattr(candidate, "tier_profile", None)
                    if tier_profile is not None:
                        tier = int(augment_mask[i])
                        target_mult = float(tier_profile[tier - 1])
                        target_notional = ladder.BASE_UNIT * target_mult
                        target_ratio = float(
                            getattr(candidate, "target_fill_ratio", 0.75)
                        )
                        if notional >= target_notional * target_ratio:
                            continue
                        request_notional = target_notional
                        absolute_target = True
                        reason = "dc_tier"
                    else:
                        request_notional = (
                            ladder.BASE_UNIT * candidate.add_mult
                        )
                        absolute_target = False
                        reason = "trend_resume"
                    augment_request_count += 1
                    pending = {
                        "kind": "entry",
                        "requested_notional": request_notional,
                        "absolute_target": absolute_target,
                        "reason": reason,
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
                    }
                elif signals.entry_mult[i] > 0 and gap_seen:
                    pending = {
                        "kind": "entry",
                        "requested_notional": (
                            ladder.BASE_UNIT * float(signals.entry_mult[i])
                        ),
                        "absolute_target": curve.semantics == "target",
                        "reason": "ladder_lower" if is_long else "ladder_higher",
                    }
                elif close > reclaim_level if is_long else close < reclaim_level:
                    beyond_reclaim += 1
            elif signals.entry_mult[i] > 0:
                pending = {
                    "kind": "entry",
                    "requested_notional": (
                        ladder.BASE_UNIT * float(signals.entry_mult[i])
                    ),
                    "absolute_target": curve.semantics == "target",
                    "reason": "initial_ladder",
                }

    if has_position():
        px = float(data.close[right - 1]) * (
            1.0 - side_sign * slippage_rate
        )
        notional = abs(qty) * px
        cash += side_sign * (
            notional - side_sign * commission_rate * notional
        )
    min_equity = min(min_equity, cash)
    strategy_pnl = cash - ladder.ACCOUNT_EQUITY
    bh_entry = float(data.open[left]) * (
        1.0 + side_sign * slippage_rate
    )
    bh_exit = float(data.close[right - 1]) * (
        1.0 - side_sign * slippage_rate
    )
    bh_pnl = (
        ladder.BASE_UNIT * side_sign * (bh_exit - bh_entry) / bh_entry
        - 2.0 * commission_rate * ladder.BASE_UNIT
    )
    bars = right - left
    return {
        "capital_return_pct": 100.0 * strategy_pnl / ladder.BASE_UNIT,
        "side": side,
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
        "entry_capacity_breach": bool(
            peak_post_fill_notional > ladder.CAPACITY + 1e-6
        ),
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
        "augment_signal_count": augment_signal_count,
        "augment_request_count": augment_request_count,
        "augment_fill_count": augment_fill_count,
        "augment_requested_notional_usd": augment_requested,
        "augment_filled_notional_usd": augment_filled,
        "start_ts": int(data.ts[left]),
        "end_ts": int(data.ts[right - 1]),
        "rows": bars,
    }


def run(args: argparse.Namespace) -> Path:
    artifact = Path(args.control_artifact).resolve()
    control = json.loads((artifact / "result.json").read_text())
    manifest = control["manifest"]
    symbol, side = manifest["symbol"], manifest["side"]
    npz_path = Path(manifest["npz"])
    if not npz_path.exists():
        npz_path = Path(args.npz_dir) / f"{symbol}.npz"
    if hashlib.sha256(npz_path.read_bytes()).hexdigest() != manifest["npz_sha256"]:
        raise ValueError(f"{symbol}: control NPZ hash mismatch")
    data = top._load_execution(
        symbol, npz_path.parent, args.start, "ladder", args.end
    )
    htfs = {tf: top._compress_htf(data, tf) for tf in ("1h", "4h", "D")}
    grid = candidates()
    masks_and_audits = {
        rsi: trend_resume_mask(data, side, rsi)
        for rsi in sorted({c.rsi_boundary for c in grid})
    }
    folds = []
    for source_fold in control["outer_folds"]:
        curve = ladder.Curve(**source_fold["selected_curve"])
        signals = ladder._build_signals(data, htfs, curve, 30, side)
        train_start, validation_start = source_fold["train"]
        _, validation_end = source_fold["validation"]
        tl = ladder._date_index(data, train_start)
        tr = ladder._date_index(data, validation_start)
        vl, vr = tr, ladder._date_index(data, validation_end)
        ranked = []
        for candidate in grid:
            mask = masks_and_audits[candidate.rsi_boundary][0]
            inner = [
                simulate(
                    data,
                    signals,
                    curve,
                    mask,
                    candidate,
                    left,
                    right,
                    args.commission_bps / 10_000.0,
                    args.slippage_bps / 10_000.0,
                    side,
                )
                for left, right in ladder._inner_slices(tl, tr)
            ]
            robust_tim = float(
                np.median([row["exposure_weighted_tim_pct"] for row in inner])
            )
            exposure_ok = args.target_tim_low <= robust_tim <= args.target_tim_high
            exposure_distance = max(
                args.target_tim_low - robust_tim,
                0.0,
                robust_tim - args.target_tim_high,
            )
            ranked.append(
                (
                    not exposure_ok,
                    exposure_distance,
                    -ladder._score(inner),
                    candidate.label,
                    candidate,
                    inner,
                    robust_tim,
                )
            )
        ranked.sort(key=lambda row: row[:4])
        _, _, negative_score, _, winner, inner, robust_tim = ranked[0]
        mask = masks_and_audits[winner.rsi_boundary][0]
        validation = simulate(
            data,
            signals,
            curve,
            mask,
            winner,
            vl,
            vr,
            args.commission_bps / 10_000.0,
            args.slippage_bps / 10_000.0,
            side,
        )
        control_validation = ladder._simulate(
            data,
            signals,
            curve,
            vl,
            vr,
            args.commission_bps / 10_000.0,
            args.slippage_bps / 10_000.0,
            side,
        )
        exposure_pass = (
            args.target_tim_low
            <= validation["exposure_weighted_tim_pct"]
            <= args.target_tim_high
        )
        folds.append(
            {
                "fold": source_fold["fold"],
                "train": source_fold["train"],
                "validation": source_fold["validation"],
                "curve": dataclasses.asdict(curve),
                "selection_score": -negative_score,
                "training_robust_weighted_tim_pct": robust_tim,
                "training_exposure_policy_pass": (
                    args.target_tim_low <= robust_tim <= args.target_tim_high
                ),
                "selected_candidate": {
                    "label": winner.label,
                    "family": FAMILY,
                    "role": "augment_only",
                    "params": dataclasses.asdict(winner),
                },
                "inner_metrics": inner,
                "validation_metrics": validation,
                "same_frozen_ladder_e02_control": control_validation,
                "delta_vs_control_pp": (
                    validation["capital_return_pct"]
                    - control_validation["capital_return_pct"]
                ),
                "beats_bh": (
                    validation["capital_return_pct"]
                    > validation["bh_capital_return_pct"]
                ),
                "beats_control": (
                    validation["capital_return_pct"]
                    > control_validation["capital_return_pct"]
                ),
                "exposure_policy_pass": exposure_pass,
            }
        )
    candidate_sum = sum(
        fold["validation_metrics"]["capital_return_pct"] for fold in folds
    )
    bh_sum = sum(
        fold["validation_metrics"]["bh_capital_return_pct"] for fold in folds
    )
    control_sum = sum(
        fold["same_frozen_ladder_e02_control"]["capital_return_pct"]
        for fold in folds
    )
    rows = sum(fold["validation_metrics"]["rows"] for fold in folds)
    aggregate = {
        "candidate_capital_return_pct_sum": candidate_sum,
        "bh_capital_return_pct_sum": bh_sum,
        "control_capital_return_pct_sum": control_sum,
        "candidate_bh_multiple": (
            candidate_sum / bh_sum if abs(bh_sum) > 1e-12 else None
        ),
        "candidate_control_multiple": (
            candidate_sum / control_sum if abs(control_sum) > 1e-12 else None
        ),
        "alpha_vs_bh_pp_sum": candidate_sum - bh_sum,
        "delta_vs_control_pp_sum": candidate_sum - control_sum,
        "weighted_tim_pct": sum(
            fold["validation_metrics"]["exposure_weighted_tim_pct"]
            * fold["validation_metrics"]["rows"]
            for fold in folds
        )
        / max(1, rows),
        "all_folds_beat_bh": all(fold["beats_bh"] for fold in folds),
        "all_folds_beat_control": all(fold["beats_control"] for fold in folds),
        "all_mandatory_reclaim": all(
            fold["validation_metrics"]["bars_flat_beyond_reclaim"] == 0
            for fold in folds
        ),
        "all_folds_exposure_policy_pass": all(
            fold["exposure_policy_pass"] for fold in folds
        ),
        "future_htf_count": 0,
        "augment_fill_count": sum(
            fold["validation_metrics"]["augment_fill_count"] for fold in folds
        ),
    }
    aggregate["exposure_policy"] = {
        "min_weighted_tim_pct": args.target_tim_low,
        "max_weighted_tim_pct": args.target_tim_high,
        "pass": (
            args.target_tim_low
            <= aggregate["weighted_tim_pct"]
            <= args.target_tim_high
        ),
    }
    aggregate["vector_survivor"] = bool(
        aggregate["all_folds_beat_bh"]
        and aggregate["all_folds_beat_control"]
        and aggregate["all_mandatory_reclaim"]
        and aggregate["all_folds_exposure_policy_pass"]
        and aggregate["exposure_policy"]["pass"]
    )
    output = {
        "manifest": {
            "tier": "VEC_RESEARCH",
            "promotion_allowed": False,
            "matrix_written": False,
            "symbol": symbol,
            "side": side,
            "family": FAMILY,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "control_artifact": str(artifact),
            "npz": str(npz_path.resolve()),
            "control_npz_sha256": manifest["npz_sha256"],
            "data_start": args.start,
            "frozen_exit": "E02_DONCHIAN_4h_N30",
            "frozen_reentry": "zero-buffer resting reclaim",
            "base_unit_usd": ladder.BASE_UNIT,
            "account_equity_usd": ladder.ACCOUNT_EQUITY,
            "hard_capacity_usd": ladder.CAPACITY,
            "grid_candidates": len(grid),
            "live_path_status": (
                "DISCONNECTED: absent from active tradier_manage/config; "
                "research reconstruction from 2026-07-21 backup"
            ),
            "last_real_source_semantics": (
                "open position; gain>=0.5%; LONG close>dc_basis_5m, K>D, "
                "RSI<70; SHORT mirror RSI>30; add 0.5x START_POSITION_SIZE"
            ),
            "five_min_input_audits": [
                masks_and_audits[rsi][1] for rsi in sorted(masks_and_audits)
            ],
            "causality": {
                tf: {
                    "source_timestamp_future_count": int(
                        np.count_nonzero(
                            top._compress_htf(data, tf).source_ts
                            > data.ts[top._compress_htf(data, tf).event_index]
                        )
                    )
                }
                for tf in ("1h", "4h", "D")
            },
        },
        "outer_folds": folds,
        "aggregate": aggregate,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (
        Path(args.out_dir)
        / f"entry_overlay_{FAMILY}_{stamp}_{symbol}_{side}"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(
        json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    with gzip.open(out / "frozen_fold_rows.jsonl.gz", "wt") as handle:
        for fold in folds:
            handle.write(json.dumps(fold, sort_keys=True) + "\n")
    snapshot = out / "source_snapshot"
    snapshot.mkdir()
    snapshot.joinpath(Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"artifact": str(out), **aggregate}, sort_keys=True))
    data.z.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-artifact", required=True)
    parser.add_argument("--npz-dir", default=str(top.DEFAULT_NPZ))
    parser.add_argument("--out-dir", default=str(top.DEFAULT_OUT))
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end")
    parser.add_argument("--target-tim-low", type=float, default=70.0)
    parser.add_argument("--target-tim-high", type=float, default=80.0)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=2.5)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
