#!/usr/bin/env python3
"""Small causal regime-conditioned exposure grid for single entry schedules."""
from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ADVERSE = 0
NEUTRAL = 1
SUPPORTIVE = 2
REGIME_NAMES = {ADVERSE: "ADVERSE", NEUTRAL: "NEUTRAL", SUPPORTIVE: "SUPPORTIVE"}
HARD_MAX_MULT = 8.0
REQUIRED_FEATURES = (
    "lrL_pct_b_4h",
    "lrL_pct_b_D",
    "stoch_k_1h",
    "stoch_d_1h",
    "adx_4h",
    "relative_volume_4h",
)


@dataclasses.dataclass(frozen=True)
class Policy:
    name: str
    regime_count: int
    multiplier_scale: tuple[float, float, float]
    min_gap_completed_1h_bars: tuple[int, int, int]
    regime_max_mult: tuple[float, float, float]

    def validate(self) -> None:
        if self.regime_count not in (2, 3):
            raise ValueError("regime_count must be 2 or 3")
        if not (
            self.multiplier_scale[ADVERSE]
            <= self.multiplier_scale[NEUTRAL]
            <= self.multiplier_scale[SUPPORTIVE]
        ):
            raise ValueError("multiplier scales must be monotonic")
        if not (
            self.min_gap_completed_1h_bars[ADVERSE]
            >= self.min_gap_completed_1h_bars[NEUTRAL]
            >= self.min_gap_completed_1h_bars[SUPPORTIVE]
        ):
            raise ValueError("entry density must be monotonic")
        if not (
            self.regime_max_mult[ADVERSE]
            <= self.regime_max_mult[NEUTRAL]
            <= self.regime_max_mult[SUPPORTIVE]
            <= HARD_MAX_MULT
        ):
            raise ValueError("regime caps must be monotonic and <=8x")

    def effective_regime(self, regime: int) -> int:
        if self.regime_count == 2 and regime == NEUTRAL:
            return ADVERSE
        return int(regime)


def policy_grid() -> list[Policy]:
    rows = [
        Policy("binary_gentle", 2, (0.875, 0.875, 1.125), (1, 1, 0), (6, 6, 8)),
        Policy("binary_balanced", 2, (0.75, 0.75, 1.25), (2, 2, 0), (4, 4, 8)),
        Policy("ternary_gentle", 3, (0.875, 1.0, 1.125), (1, 1, 0), (6, 6, 8)),
        Policy("ternary_balanced", 3, (0.75, 1.0, 1.25), (2, 1, 0), (4, 6, 8)),
        Policy("ternary_strong", 3, (0.625, 1.0, 1.375), (3, 1, 0), (3, 6, 8)),
    ]
    for row in rows:
        row.validate()
    return rows


def classify_completed_regime(
    view: dict[str, np.ndarray],
    side: str,
) -> np.ndarray:
    """Classify only already-aligned completed 1h/4h/D feature arrays."""
    if side not in {"LONG", "SHORT"}:
        raise ValueError(side)
    missing = [key for key in REQUIRED_FEATURES if key not in view]
    if missing:
        raise KeyError(f"missing completed regime features: {missing}")
    arrays = {
        key: np.asarray(view[key], dtype=np.float64)
        for key in REQUIRED_FEATURES
    }
    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1:
        raise ValueError("regime feature arrays must have equal length")
    n = lengths.pop()
    valid = np.ones(n, dtype=bool)
    for values in arrays.values():
        valid &= np.isfinite(values)

    q4 = arrays["lrL_pct_b_4h"]
    qd = arrays["lrL_pct_b_D"]
    if side == "SHORT":
        q4 = 1.0 - q4
        qd = 1.0 - qd
    k1 = arrays["stoch_k_1h"]
    d1 = arrays["stoch_d_1h"]
    stoch_favorable = k1 > d1 if side == "LONG" else k1 < d1
    stoch_adverse = k1 < d1 if side == "LONG" else k1 > d1
    favorable_votes = (
        (q4 >= 0.55).astype(np.int8)
        + (qd >= 0.55).astype(np.int8)
        + stoch_favorable.astype(np.int8)
    )
    adverse_votes = (
        (q4 <= 0.45).astype(np.int8)
        + (qd <= 0.45).astype(np.int8)
        + stoch_adverse.astype(np.int8)
    )
    participation = (
        (arrays["adx_4h"] >= 20.0)
        | (arrays["relative_volume_4h"] >= 1.0)
    )
    regime = np.full(n, NEUTRAL, dtype=np.int8)
    regime[valid & (adverse_votes >= 2)] = ADVERSE
    regime[valid & (favorable_votes >= 2) & participation] = SUPPORTIVE
    # Missing data is neutral/fail-safe and never manufactured supportive.
    return regime


def apply_policy(
    entry_mult: np.ndarray,
    regime: np.ndarray,
    completed_1h_slot: np.ndarray,
    policy: Policy,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Scale and causally thin one frozen family schedule; never combine paths."""
    policy.validate()
    entry_mult = np.asarray(entry_mult, dtype=np.float64)
    regime = np.asarray(regime, dtype=np.int8)
    slots = np.asarray(completed_1h_slot, dtype=np.int64)
    if not (len(entry_mult) == len(regime) == len(slots)):
        raise ValueError("schedule, regime and completed-slot arrays must align")
    if np.any(np.diff(slots) < 0):
        raise ValueError("completed 1h slots must be monotonic")
    out = np.zeros(len(entry_mult), dtype=np.float64)
    last_accepted_slot: int | None = None
    requests = accepted = 0
    by_regime = {
        name: {"requests": 0, "accepted": 0}
        for name in REGIME_NAMES.values()
    }
    for idx in np.flatnonzero(entry_mult > 0):
        requests += 1
        raw_regime = int(regime[idx])
        effective = policy.effective_regime(raw_regime)
        name = REGIME_NAMES[raw_regime]
        by_regime[name]["requests"] += 1
        gap = policy.min_gap_completed_1h_bars[effective]
        if (
            last_accepted_slot is not None
            and int(slots[idx]) - last_accepted_slot <= gap
        ):
            continue
        scaled = entry_mult[idx] * policy.multiplier_scale[effective]
        out[idx] = min(
            max(0.0, scaled),
            policy.regime_max_mult[effective],
            HARD_MAX_MULT,
        )
        if out[idx] > 0:
            accepted += 1
            by_regime[name]["accepted"] += 1
            last_accepted_slot = int(slots[idx])
    return out, {
        "policy": dataclasses.asdict(policy),
        "requests": requests,
        "accepted": accepted,
        "rejected_by_density": requests - accepted,
        "max_output_mult": float(np.max(out)) if len(out) else 0.0,
        "hard_max_mult": HARD_MAX_MULT,
        "by_regime": by_regime,
    }


def audit_npz_schema(paths: list[Path]) -> dict[str, Any]:
    coverage = {key: 0 for key in REQUIRED_FEATURES}
    for path in paths:
        with np.load(path, allow_pickle=False) as data:
            for key in REQUIRED_FEATURES:
                coverage[key] += int(key in data.files)
    return {
        "npz_count": len(paths),
        "required_features": list(REQUIRED_FEATURES),
        "coverage": coverage,
        "all_required_present": bool(paths) and all(
            count == len(paths) for count in coverage.values()
        ),
    }


def artifact_npzs(report_path: Path) -> list[Path]:
    report = json.loads(report_path.read_text())
    root = report_path.resolve().parents[3]
    paths = set()
    for row in report["rows"]:
        artifact = Path(row["artifact"])
        if not artifact.is_absolute():
            artifact = root / artifact
        result = json.loads((artifact / "result.json").read_text())
        path = Path(result["manifest"]["npz"])
        if not path.is_absolute():
            path = root / path
        paths.add(path.resolve())
    return sorted(paths)


def spec(report_path: Path) -> dict[str, Any]:
    schema = audit_npz_schema(artifact_npzs(report_path))
    if not schema["all_required_present"]:
        raise RuntimeError(schema)
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_report": str(report_path),
        "schema_audit": schema,
        "classifier": {
            "features": list(REQUIRED_FEATURES),
            "completed_only": True,
            "side_mirror": "SHORT uses 1-lrL_pct_b and falling Stoch",
            "supportive": (
                "at least 2 of q4>=.55, qD>=.55, favorable 1h Stoch; "
                "plus ADX4h>=20 or relative_volume4h>=1"
            ),
            "adverse": "at least 2 mirrored adverse votes at .45 thresholds",
            "neutral": "all other or non-finite rows",
            "symbol_specific_thresholds": False,
            "final_fold_features_used_for_selection": False,
        },
        "grid": [dataclasses.asdict(row) for row in policy_grid()],
        "contract": {
            "consumer_input": (
                "one pre-final-frozen component candidate and ladder curve"
            ),
            "single_entry_family_only": True,
            "blended_entry_overlays": False,
            "selection_data": "discovery folds only",
            "final_fold": "evaluation only",
            "execution": "completed 1h slot monotonic; no future HTF values",
            "hard_capacity": "entry multiplier <=8x; account simulator still enforces $16k",
            "promotion": "none; every grid arm requires causal re-simulation",
        },
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Causal regime-conditioned exposure grid — 2026-07-26",
        "",
        "All six features are present in all "
        f"{payload['schema_audit']['npz_count']} frozen NPZs. The classifier "
        "uses only completed 1h/4h/D values and fixed global thresholds. "
        "It consumes one pre-final-frozen entry family; no blended overlay or "
        "final-fold selection is allowed.",
        "",
        "| policy | regimes | adverse scale/gap/cap | neutral | supportive |",
        "|---|---:|---|---|---|",
    ]
    for row in payload["grid"]:
        values = [
            (
                f"{row['multiplier_scale'][idx]:.3f}/"
                f"{row['min_gap_completed_1h_bars'][idx]}/"
                f"{row['regime_max_mult'][idx]:.1f}x"
            )
            for idx in (ADVERSE, NEUTRAL, SUPPORTIVE)
        ]
        lines.append(
            f"| {row['name']} | {row['regime_count']} | "
            f"{values[0]} | {values[1]} | {values[2]} |"
        )
    lines += [
        "",
        "Each cell is multiplier scale / minimum completed-1h entry gap / "
        "absolute multiplier cap. Scales and density are monotonic from adverse "
        "to supportive, and every cap is at or below 8x.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()
    payload = spec(args.report)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.md_out.write_text(markdown(payload) + "\n")
    print(
        json.dumps(
            {
                "npz_count": payload["schema_audit"]["npz_count"],
                "all_required_present": payload["schema_audit"][
                    "all_required_present"
                ],
                "grid_arms": len(payload["grid"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
