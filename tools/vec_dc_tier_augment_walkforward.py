#!/usr/bin/env python3
"""Causal screen for the active-but-unswitched stock DC-tier augment."""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import vec_augment_trend_resume_walkforward as aug  # noqa: E402
import vec_band_ladder_walkforward as ladder  # noqa: E402
import vec_entry_overlay_walkforward as overlay  # noqa: E402
import vec_top_exit_campaign as top  # noqa: E402


FAMILY = "ENTRY_DC_TIER_AUG_ENABLED"


@dataclasses.dataclass(frozen=True)
class Candidate:
    min_gain_pct: float
    buffer_fraction: float
    tier_profile: tuple[float, float, float, float]
    target_fill_ratio: float
    maturity_atr: float | None

    @property
    def label(self) -> str:
        raw = json.dumps(dataclasses.asdict(self), sort_keys=True)
        return f"DC_TIER_AUG_{hashlib.sha256(raw.encode()).hexdigest()[:10]}"


def candidates() -> list[Candidate]:
    """243 settings; source settings plus traceable research extensions."""
    return [
        Candidate(float(gain), float(buffer_), tuple(profile), float(ratio), maturity)
        for gain, buffer_, profile, ratio, maturity in itertools.product(
            (1.0, 3.0, 5.0),
            (0.0, 0.001, 0.002),
            (
                (1.0, 2.0, 3.0, 5.0),
                (1.0, 1.5, 2.5, 4.0),
                (1.0, 2.0, 4.0, 8.0),
            ),
            (0.50, 0.75, 0.90),
            (None, 0.7, 0.5),
        )
    ]


def tier_state(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    side: str,
    buffer_fraction: float,
    maturity_atr: float | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Highest broken prior completed channel, mirrored by side."""
    view, causal = overlay._causal_npz_view(data, htfs)
    price = np.asarray(data.close, dtype=np.float64)
    tier = np.zeros(len(price), dtype=np.int8)
    stem = "dc_high" if side == "LONG" else "dc_low"
    for number, tf in enumerate(("5m", "15m", "1h", "4h"), start=1):
        channel = np.asarray(
            view.get(f"{stem}_{tf}_prev", np.zeros(len(price))),
            dtype=np.float64,
        )
        valid = np.isfinite(channel) & (channel > 0)
        if side == "LONG":
            broken = valid & (price > channel * (1.0 + buffer_fraction))
        else:
            broken = valid & (price < channel * (1.0 - buffer_fraction))
        tier[broken] = number
    maturity_blocks = 0
    if maturity_atr is not None:
        open_d = overlay._base_arr(data, "open_D", default=np.nan)
        atr_d = overlay._completed_arr(data, htfs["D"], "atr_D", default=np.nan)
        valid = np.isfinite(open_d) & (open_d > 0) & np.isfinite(atr_d) & (atr_d > 0)
        same_direction = (
            price > open_d if side == "LONG" else price < open_d
        )
        mature = valid & same_direction & (
            np.abs(price - open_d) / np.maximum(atr_d, 1e-12) > maturity_atr
        )
        blocked = (tier == 4) & mature
        maturity_blocks = int(np.count_nonzero(blocked))
        tier[blocked] = 0
    return np.ascontiguousarray(tier), {
        "side": side,
        "buffer_fraction": buffer_fraction,
        "maturity_atr": maturity_atr,
        "tier_rows": {
            str(value): int(np.count_nonzero(tier == value))
            for value in range(1, 5)
        },
        "maturity_blocks": maturity_blocks,
        "future_htf_count": sum(
            int(row["source_timestamp_future_count"]) for row in causal.values()
        ),
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
    data = top._load_execution(symbol, npz_path.parent, args.start, "ladder", args.end)
    htfs = {tf: top._compress_htf(data, tf) for tf in ("15m", "1h", "4h", "D")}
    grid = candidates()
    states = {}
    audits = {}
    for buffer_, maturity in sorted(
        {(c.buffer_fraction, c.maturity_atr) for c in grid},
        key=lambda row: (row[0], -1 if row[1] is None else row[1]),
    ):
        state, audit = tier_state(data, htfs, side, buffer_, maturity)
        states[(buffer_, maturity)] = state
        audits[(buffer_, maturity)] = audit
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
            state = states[(candidate.buffer_fraction, candidate.maturity_atr)]
            inner = [
                aug.simulate(
                    data, signals, curve, state, candidate, left, right,
                    args.commission_bps / 10_000.0,
                    args.slippage_bps / 10_000.0, side,
                )
                for left, right in ladder._inner_slices(tl, tr)
            ]
            robust_tim = float(
                np.median([row["exposure_weighted_tim_pct"] for row in inner])
            )
            exposure_distance = max(
                args.target_tim_low - robust_tim,
                0.0,
                robust_tim - args.target_tim_high,
            )
            ranked.append(
                (
                    exposure_distance > 0,
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
        state = states[(winner.buffer_fraction, winner.maturity_atr)]
        validation = aug.simulate(
            data, signals, curve, state, winner, vl, vr,
            args.commission_bps / 10_000.0,
            args.slippage_bps / 10_000.0, side,
        )
        control_validation = ladder._simulate(
            data, signals, curve, vl, vr,
            args.commission_bps / 10_000.0,
            args.slippage_bps / 10_000.0, side,
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
                "beats_bh": (
                    validation["capital_return_pct"]
                    > validation["bh_capital_return_pct"]
                ),
                "beats_control": (
                    validation["capital_return_pct"]
                    > control_validation["capital_return_pct"]
                ),
                "exposure_policy_pass": (
                    args.target_tim_low
                    <= validation["exposure_weighted_tim_pct"]
                    <= args.target_tim_high
                ),
            }
        )
    candidate_sum = sum(f["validation_metrics"]["capital_return_pct"] for f in folds)
    bh_sum = sum(f["validation_metrics"]["bh_capital_return_pct"] for f in folds)
    control_sum = sum(
        f["same_frozen_ladder_e02_control"]["capital_return_pct"] for f in folds
    )
    rows = sum(f["validation_metrics"]["rows"] for f in folds)
    aggregate = {
        "candidate_capital_return_pct_sum": candidate_sum,
        "bh_capital_return_pct_sum": bh_sum,
        "control_capital_return_pct_sum": control_sum,
        "alpha_vs_bh_pp_sum": candidate_sum - bh_sum,
        "delta_vs_control_pp_sum": candidate_sum - control_sum,
        "weighted_tim_pct": sum(
            f["validation_metrics"]["exposure_weighted_tim_pct"]
            * f["validation_metrics"]["rows"] for f in folds
        ) / max(1, rows),
        "all_folds_beat_bh": all(f["beats_bh"] for f in folds),
        "all_folds_beat_control": all(f["beats_control"] for f in folds),
        "all_folds_exposure_policy_pass": all(
            f["exposure_policy_pass"] for f in folds
        ),
        "all_mandatory_reclaim": all(
            f["validation_metrics"]["bars_flat_beyond_reclaim"] == 0 for f in folds
        ),
        "all_capacity_safe": all(
            not f["validation_metrics"]["entry_capacity_breach"] for f in folds
        ),
        "future_htf_count": sum(
            audit["future_htf_count"] for audit in audits.values()
        ),
        "augment_signal_count": sum(
            f["validation_metrics"]["augment_signal_count"] for f in folds
        ),
        "augment_fill_count": sum(
            f["validation_metrics"]["augment_fill_count"] for f in folds
        ),
        "augment_request_count": sum(
            f["validation_metrics"]["augment_request_count"] for f in folds
        ),
    }
    aggregate["vector_survivor"] = bool(
        aggregate["all_folds_beat_bh"]
        and aggregate["all_folds_beat_control"]
        and aggregate["all_folds_exposure_policy_pass"]
        and aggregate["all_mandatory_reclaim"]
        and aggregate["all_capacity_safe"]
        and aggregate["future_htf_count"] == 0
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
            "npz": str(npz_path),
            "control_npz_sha256": manifest["npz_sha256"],
            "grid_candidates": len(grid),
            "frozen_exit": "E02_DONCHIAN_4h_N30",
            "frozen_reentry": "zero-buffer resting reclaim",
            "hard_capacity_usd": ladder.CAPACITY,
            "wiring": {
                "inventory_switch": "DC_TIER_AUG_ENABLED",
                "switch_status": "CONNECTED_DEFAULT_TRUE",
                "underlying_function_status": (
                    "ACTIVE_WHEN_ENABLED_INSIDE_evaluate_augment_AFTER_GAIN_GATE"
                ),
                "backtest_live_parity": {
                    "enabled": True,
                    "scope": "DC breakout-tier block only",
                    "default_preserves_previous_live_behavior": True,
                },
            },
            "source_setting": {
                "min_gain_pct": 3.0,
                "buffer_fraction": 0.001,
                "tier_profile": [1.0, 2.0, 3.0, 5.0],
                "target_fill_ratio": 0.75,
                "maturity_atr": None,
            },
            "research_extensions": {
                "min_gain_pct": [1.0, 5.0],
                "buffer_fraction": [0.0, 0.002],
                "tier_profiles": ["conservative", "aggressive"],
                "target_fill_ratio": [0.5, 0.9],
                "maturity_atr": [0.5, 0.7],
            },
            "state_audits": list(audits.values()),
        },
        "outer_folds": folds,
        "aggregate": aggregate,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir) / f"entry_overlay_{FAMILY}_{stamp}_{symbol}_{side}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(
        json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    with gzip.open(out / "frozen_fold_rows.jsonl.gz", "wt") as handle:
        for fold in folds:
            handle.write(json.dumps(fold, sort_keys=True) + "\n")
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
    parser.add_argument("--commission-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
