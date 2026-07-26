#!/usr/bin/env python3
"""Causal GR / WT_DC / structural-Stoch overlays on the frozen ladder.

This is a vector-first shortlist, not a live-promotion tool.  It consumes one
completed ``vec_band_ladder_walkforward`` artifact and keeps, fold by fold:

* the selected six-number ladder curve and its 8x/$16k capacity;
* E02 completed-4h Donchian N=30;
* next-RTH execution and mandatory zero-buffer reclaim;
* the same $2,000 side-correct B&H comparison.

Only the entry event mask changes.  ``filter`` requires the candidate path on a
frozen ladder event. ``direct`` fires at the start of a candidate signal episode
and sizes that request from the same frozen ladder curve using the latest
completed D/4h/1h band positions.  HTF features are rebuilt from completed-bar
availability timestamps and forward-filled only after availability.

The large grids are first ranked by vector forward returns on training data.
Only a bounded shortlist enters the stateful accounting loop.  Untouched
validation rows are then compared with both B&H and the exact same frozen
ladder+E02 control.  No row is matrix/live eligible without exact V8 replay.
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
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vec_band_ladder_walkforward as ladder  # noqa: E402
import vec_top_exit_campaign as top  # noqa: E402
from vec_paths.gr_filter_vec import _tf_score_vec  # noqa: E402
from vec_paths.wt_dc_entry import compute_wt_dc_entry_vec  # noqa: E402


@dataclasses.dataclass(frozen=True)
class Candidate:
    family: str
    role: str
    params_json: str

    @property
    def params(self) -> dict[str, Any]:
        return json.loads(self.params_json)

    @property
    def label(self) -> str:
        digest = hashlib.sha256(self.params_json.encode()).hexdigest()[:10]
        return f"{self.family}_{self.role}_{digest}"


def _candidate(family: str, role: str, **params: Any) -> Candidate:
    return Candidate(
        family,
        role,
        json.dumps(params, sort_keys=True, separators=(",", ":")),
    )


def _base_arr(data: top.ExecutionData, *keys: str, default: float = 0.0) -> np.ndarray:
    for key in keys:
        if key in data.z.files:
            return np.asarray(data.z[key], dtype=np.float64)[data.full_indices]
    return np.full(len(data.ts), default, dtype=np.float64)


def _completed_arr(
    data: top.ExecutionData,
    htf: top.HTFData,
    *keys: str,
    default: float = 0.0,
) -> np.ndarray:
    for key in keys:
        if key in data.z.files:
            values = np.asarray(data.z[key], dtype=np.float64)[
                data.full_indices[htf.event_index]
            ]
            return top._align_feature(len(data.ts), htf, values)
    return np.full(len(data.ts), default, dtype=np.float64)


def _causal_npz_view(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Materialize only completed HTF values on the RTH execution timeline."""
    view: dict[str, np.ndarray] = {
        "close": np.asarray(data.close, dtype=np.float64),
        "close_5m": np.asarray(data.close, dtype=np.float64),
        "k_5m": _base_arr(data, "k_5m", "stoch_k_5m", default=50.0),
        "stoch_k_5m": _base_arr(data, "stoch_k_5m", "k_5m", default=50.0),
    }
    features = (
        "wt1",
        "wt2",
        "rsi",
        "mfi",
        "dc_pct",
        "dc_position",
        "dc_high",
        "dc_low",
        "bb_pct_b",
        "relative_volume",
        "k",
        "stoch_k",
        "adx",
        "macd_hist",
        "ha_color",
        "d",
        "stoch_d",
        "wt_cross",
        "wt_cross_bull",
        "wt_cross_bear",
        "lrL_pct_b",
        "high",
        "low",
    )
    audit: dict[str, Any] = {}
    for tf, h in htfs.items():
        future = int(np.count_nonzero(h.source_ts > data.ts[h.event_index]))
        audit[tf] = {
            "completed_bars": int(len(h.event_index)),
            "source_timestamp_future_count": future,
        }
        for feature in features:
            key = f"{feature}_{tf}"
            if key in data.z.files:
                view[key] = _completed_arr(data, h, key)
        if f"dc_pct_{tf}" not in view and f"dc_position_{tf}" in view:
            view[f"dc_pct_{tf}"] = view[f"dc_position_{tf}"]
    return view, audit


def _latest_band_mult(
    view: dict[str, np.ndarray],
    curve: ladder.Curve,
    side: str,
) -> np.ndarray:
    rows = []
    for tf in ladder.TF_ORDER:
        pb = np.asarray(
            view.get(f"lrL_pct_b_{tf}", np.full(len(view["close"]), np.nan)),
            dtype=np.float64,
        )
        if side == "SHORT":
            pb = 1.0 - pb
        bottom, top_ = curve.pair(tf)
        rows.append(
            np.fromiter(
                (
                    ladder.ladder_mult(float(value), bottom, top_, curve.mode)
                    for value in pb
                ),
                dtype=np.float64,
                count=len(pb),
            )
        )
    stacked = np.vstack(rows)
    result = (
        np.sum(stacked, axis=0)
        if curve.semantics == "add"
        else np.max(stacked, axis=0)
    )
    return np.clip(result, 0.0, ladder.MAX_MULT)


def _episode_starts(mask: np.ndarray) -> np.ndarray:
    mask = np.asarray(mask, dtype=bool)
    return mask & ~np.r_[False, mask[:-1]]


def _entry_mult(
    candidate_mask: np.ndarray,
    control: ladder.SignalData,
    direct_mult: np.ndarray,
    role: str,
) -> np.ndarray:
    if role == "filter":
        return np.where(candidate_mask, control.entry_mult, 0.0)
    return np.where(_episode_starts(candidate_mask), direct_mult, 0.0)


def _candidate_entry_mult(
    candidate: Candidate,
    candidate_mask: np.ndarray,
    control: ladder.SignalData,
    direct_mult: np.ndarray,
    curve: ladder.Curve,
    green: ladder.SignalData | None = None,
) -> np.ndarray:
    if candidate.family != "ENTRY_STOCH_HHHL":
        return _entry_mult(
            candidate_mask, control, direct_mult, candidate.role
        )
    structure = np.where(
        _episode_starts(candidate_mask), direct_mult, 0.0
    )
    if candidate.role == "direct":
        return structure
    if green is None:
        raise ValueError("union-with-green requires green signal schedule")
    combined = (
        structure + green.entry_mult
        if curve.semantics == "add"
        else np.maximum(structure, green.entry_mult)
    )
    return np.clip(combined, 0.0, ladder.MAX_MULT)


def _gr_candidates() -> list[Candidate]:
    weights = {
        "equal": (1.0, 1.0, 1.0, 1.0),
        "htf": (0.5, 1.0, 2.0, 3.0),
        "daily": (0.0, 1.0, 2.0, 4.0),
    }
    rows = []
    for role, min_ind, min_tfs, (weight_name, values), fraction in itertools.product(
        ("filter", "direct"),
        (3, 4, 5, 6),
        (1, 2, 3),
        weights.items(),
        (0.35, 0.50, 0.65, 0.80),
    ):
        rows.append(
            _candidate(
                "ENTRY_GOLDEN_RULE",
                role,
                min_ind=min_ind,
                min_tfs=min_tfs,
                weight_name=weight_name,
                weights=list(values),
                threshold_fraction=fraction,
            )
        )
    return rows


def _wt_candidates() -> list[Candidate]:
    rows = []
    for role, threshold, gate, align, stoch in itertools.product(
        ("filter", "direct"),
        (20.0, 35.0, 45.0, 60.0, 75.0, 85.0),
        ("none", "1h", "4h", "4h_D"),
        (0, 1, 2, 3),
        (60.0, 80.0, 100.0),
    ):
        rows.append(
            _candidate(
                "ENTRY_WT_DC",
                role,
                threshold=threshold,
                htf_gate=gate,
                htf_align_required=align,
                combined_stoch_gate=stoch,
            )
        )
    return rows


def _stoch_candidates() -> list[Candidate]:
    tf_sets = (
        ("1h",),
        ("4h",),
        ("D",),
        ("1h", "4h"),
        ("1h", "D"),
        ("4h", "D"),
        ("1h", "4h", "D"),
    )
    rows = []
    for role, threshold, enabled_tfs, confirmations in itertools.product(
        ("direct", "union-with-green"),
        (15.0, 20.0, 25.0, 30.0, 35.0, 40.0),
        tf_sets,
        (1, 2),
    ):
        if confirmations > len(enabled_tfs):
            continue
        rows.append(
            _candidate(
                "ENTRY_STOCH_HHHL",
                role,
                stoch_threshold=threshold,
                enabled_tfs=list(enabled_tfs),
                min_confirming_tfs=confirmations,
            )
        )
    return rows


def _structure_states(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    side: str,
) -> dict[tuple[str, float], np.ndarray]:
    """Latest completed bar's HH/HL+Stoch state for each TF/threshold."""
    out: dict[tuple[str, float], np.ndarray] = {}
    is_long = side == "LONG"
    for tf in ("1h", "4h", "D"):
        h = htfs[tf]
        full_event = data.full_indices[h.event_index]
        key = f"stoch_k_{tf}"
        if key not in data.z.files:
            raise ValueError(f"{data.symbol}: missing {key}")
        stoch = np.asarray(data.z[key], dtype=np.float64)[full_event]
        prior_stoch = np.r_[np.nan, stoch[:-1]]
        for threshold in (15.0, 20.0, 25.0, 30.0, 35.0, 40.0):
            state = np.zeros(len(h.event_index), dtype=bool)
            if is_long:
                state[1:] = (
                    (h.high[1:] > h.high[:-1])
                    & (h.low[1:] > h.low[:-1])
                    & (stoch[1:] <= threshold)
                    & (stoch[1:] > prior_stoch[1:])
                )
            else:
                state[1:] = (
                    (h.high[1:] < h.high[:-1])
                    & (h.low[1:] < h.low[:-1])
                    & (stoch[1:] >= 100.0 - threshold)
                    & (stoch[1:] < prior_stoch[1:])
                )
            out[(tf, threshold)] = (
                top._align_feature(
                    len(data.ts), h, state.astype(np.float64)
                )
                > 0.5
            )
    return out


def _mask(
    candidate: Candidate,
    view: dict[str, np.ndarray],
    side: str,
    gr_scores: dict[str, np.ndarray],
    structure_states: dict[tuple[str, float], np.ndarray] | None = None,
) -> np.ndarray:
    p = candidate.params
    is_long = side == "LONG"
    n = len(view["close"])
    if candidate.family == "ENTRY_WT_DC":
        return compute_wt_dc_entry_vec(
            view,
            n,
            is_long,
            threshold=p["threshold"],
            htf_gate=p["htf_gate"],
            htf_align_required=p["htf_align_required"],
            combined_stoch_gate=p["combined_stoch_gate"],
        )[0]
    if candidate.family == "ENTRY_STOCH_HHHL":
        if structure_states is None:
            raise ValueError("structure states are required")
        threshold = float(p["stoch_threshold"])
        count = np.zeros(n, dtype=np.int8)
        for tf in p["enabled_tfs"]:
            count += structure_states[(tf, threshold)].astype(np.int8)
        return count >= int(p["min_confirming_tfs"])
    passed = np.vstack(
        [gr_scores[tf] >= int(p["min_ind"]) for tf in ("15m", "1h", "4h", "D")]
    )
    tf_count = np.sum(passed, axis=0)
    weights = np.asarray(p["weights"], dtype=np.float64)[:, None]
    weighted = np.sum(passed * weights, axis=0)
    threshold = float(p["threshold_fraction"]) * float(np.sum(weights))
    return (tf_count >= int(p["min_tfs"])) & (weighted >= threshold)


def _forward_rank(
    candidates: Iterable[Candidate],
    masks: dict[Candidate, np.ndarray],
    controls: dict[Candidate, np.ndarray],
    close: np.ndarray,
    side: str,
    left: int,
    right: int,
    shortlist: int,
) -> list[Candidate]:
    """Cheap first pass: episode forward returns, no accounting claims."""
    horizon = 78 * 5
    scored = []
    sign = 1.0 if side == "LONG" else -1.0
    for candidate in candidates:
        starts = np.flatnonzero(controls[candidate] > 0)
        starts = starts[(starts >= left) & (starts + horizon < right)]
        if len(starts) < 4:
            continue
        ret = sign * (close[starts + horizon] / close[starts] - 1.0) * 100.0
        # Robust central return plus a small breadth term prevents a single
        # episode from winning the stateful shortlist.
        score = float(np.median(ret) + 0.25 * np.mean(ret) + 0.01 * min(len(ret), 50))
        scored.append((score, -len(ret), candidate.label, candidate))
    scored.sort(reverse=True)
    # Always retain historical/live-adjacent settings as sentinels even when
    # the cheap forward-return screen ranks them poorly.
    sentinels = []
    for candidate in candidates:
        p = candidate.params
        if candidate.family == "ENTRY_WT_DC" and (
            p["threshold"] in {20.0, 45.0}
            and p["htf_gate"] in {"none", "4h_D"}
            and p["htf_align_required"] in {0, 2}
            and p["combined_stoch_gate"] in {60.0, 100.0}
        ):
            sentinels.append(candidate)
        if candidate.family == "ENTRY_GOLDEN_RULE" and (
            p["min_ind"] == 5
            and p["min_tfs"] in {1, 3}
            and p["weight_name"] in {"equal", "htf"}
            and p["threshold_fraction"] in {0.50, 0.65}
        ):
            sentinels.append(candidate)
        if candidate.family == "ENTRY_STOCH_HHHL" and (
            p["stoch_threshold"] in {20.0, 30.0}
            and p["enabled_tfs"] == ["1h", "4h", "D"]
        ):
            sentinels.append(candidate)
    out: dict[str, Candidate] = {
        row[3].label: row[3] for row in scored[:shortlist]
    }
    for candidate in sentinels:
        out.setdefault(candidate.label, candidate)
    return list(out.values())


def _signals(control: ladder.SignalData, entry_mult: np.ndarray) -> ladder.SignalData:
    return ladder.SignalData(
        entry_mult=np.ascontiguousarray(entry_mult),
        event_tf=control.event_tf,
        exit_event=control.exit_event,
        exit_ref=control.exit_ref,
        causality=control.causality,
    )


def _entry_sources(
    entry_mult: np.ndarray,
    htfs: dict[str, top.HTFData],
) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for idx in np.flatnonzero(entry_mult > 0):
        sources = {}
        for tf in ("15m", "1h", "4h", "D"):
            h = htfs[tf]
            slot = int(np.searchsorted(h.event_index, idx, side="right") - 1)
            if slot >= 0:
                sources[tf] = int(h.source_ts[slot])
        out[int(idx)] = sources
    return out


def build_frozen_overlay_signals(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    curve: ladder.Curve,
    selected_candidate: dict[str, Any],
    side: str,
) -> ladder.SignalData:
    """Rebuild one frozen overlay for exact schedule materialization."""
    view, audit = _causal_npz_view(data, htfs)
    if any(row["source_timestamp_future_count"] for row in audit.values()):
        raise ValueError(f"{data.symbol}: future HTF source found")
    candidate = _candidate(
        str(selected_candidate["family"]),
        str(selected_candidate["role"]),
        **dict(selected_candidate["params"]),
    )
    gr_scores = {
        tf: _tf_score_vec(view, tf, len(data.ts), side == "LONG", True)
        for tf in ("15m", "1h", "4h", "D")
    }
    structure_states = _structure_states(data, htfs, side)
    control = ladder._build_signals(data, htfs, curve, 30, side)
    green_curve = dataclasses.replace(curve, trigger="green")
    green = ladder._build_signals(data, htfs, green_curve, 30, side)
    mask = _mask(
        candidate, view, side, gr_scores, structure_states
    )
    entry_mult = _candidate_entry_mult(
        candidate,
        mask,
        control,
        _latest_band_mult(view, curve, side),
        curve,
        green,
    )
    return ladder.SignalData(
        entry_mult=np.ascontiguousarray(entry_mult),
        event_tf=control.event_tf,
        exit_event=control.exit_event,
        exit_ref=control.exit_ref,
        causality={**control.causality, "entry_overlay": audit},
        entry_source_ts=_entry_sources(entry_mult, htfs),
    )


def run(args: argparse.Namespace) -> Path:
    artifact = Path(args.control_artifact).resolve()
    control_payload = json.loads((artifact / "result.json").read_text())
    manifest = control_payload["manifest"]
    symbol = manifest["symbol"]
    side = manifest["side"]
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported side: {side}")
    npz_path = Path(manifest["npz"])
    if not npz_path.exists():
        npz_path = Path(args.npz_dir) / f"{symbol}.npz"
    if hashlib.sha256(npz_path.read_bytes()).hexdigest() != manifest["npz_sha256"]:
        raise ValueError(f"{symbol}: control NPZ hash mismatch")
    data = top._load_execution(symbol, npz_path.parent, args.start, "ladder", args.end)
    htfs = {tf: top._compress_htf(data, tf) for tf in ("15m", "1h", "4h", "D")}
    view, causality = _causal_npz_view(data, htfs)
    if any(row["source_timestamp_future_count"] for row in causality.values()):
        raise ValueError(f"{symbol}: future HTF source found")
    gr_scores = {
        tf: _tf_score_vec(view, tf, len(data.ts), side == "LONG", True)
        for tf in ("15m", "1h", "4h", "D")
    }
    structure_states = _structure_states(data, htfs, side)
    family_candidates = {
        "ENTRY_GOLDEN_RULE": _gr_candidates,
        "ENTRY_WT_DC": _wt_candidates,
        "ENTRY_STOCH_HHHL": _stoch_candidates,
    }[args.family]()
    masks = {
        c: _mask(c, view, side, gr_scores, structure_states)
        for c in family_candidates
    }
    folds = []
    for source_fold in control_payload["outer_folds"]:
        curve = ladder.Curve(**source_fold["selected_curve"])
        control_signals = ladder._build_signals(data, htfs, curve, 30, side)
        green_signals = ladder._build_signals(
            data, htfs, dataclasses.replace(curve, trigger="green"), 30, side
        )
        direct_mult = _latest_band_mult(view, curve, side)
        train_start, validation_start = source_fold["train"]
        _, validation_end = source_fold["validation"]
        tl = ladder._date_index(data, train_start)
        tr = ladder._date_index(data, validation_start)
        vl, vr = tr, ladder._date_index(data, validation_end)
        candidate_entries = {
            c: _candidate_entry_mult(
                c,
                masks[c],
                control_signals,
                direct_mult,
                curve,
                green_signals,
            )
            for c in family_candidates
        }
        shortlisted = _forward_rank(
            family_candidates,
            masks,
            candidate_entries,
            data.close,
            side,
            tl,
            tr,
            args.shortlist,
        )
        ranked = []
        for candidate in shortlisted:
            signal = _signals(control_signals, candidate_entries[candidate])
            inner = [
                ladder._simulate(
                    data,
                    signal,
                    curve,
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
        if not ranked:
            raise RuntimeError(f"{symbol} fold {source_fold['fold']}: empty shortlist")
        # Exposure policy is an explicit constraint, not a post-hoc validation
        # selection. Within the compliant training set, robust return wins.
        ranked.sort(key=lambda row: row[:4])
        _, _, negative_score, _, winner, inner, robust_tim = ranked[0]
        score = -negative_score
        candidate_validation = ladder._simulate(
            data,
            _signals(control_signals, candidate_entries[winner]),
            curve,
            vl,
            vr,
            args.commission_bps / 10_000.0,
            args.slippage_bps / 10_000.0,
            side,
        )
        control_validation = ladder._simulate(
            data,
            control_signals,
            curve,
            vl,
            vr,
            args.commission_bps / 10_000.0,
            args.slippage_bps / 10_000.0,
            side,
        )
        folds.append(
            {
                "fold": source_fold["fold"],
                "train": source_fold["train"],
                "validation": source_fold["validation"],
                "curve": dataclasses.asdict(curve),
                "shortlisted": len(shortlisted),
                "selection_score": score,
                "training_robust_weighted_tim_pct": robust_tim,
                "training_exposure_policy_pass": (
                    args.target_tim_low <= robust_tim <= args.target_tim_high
                ),
                "selected_candidate": {
                    "label": winner.label,
                    "family": winner.family,
                    "role": winner.role,
                    "params": winner.params,
                },
                "inner_metrics": inner,
                "validation_metrics": candidate_validation,
                "same_frozen_ladder_e02_control": control_validation,
                "delta_vs_control_pp": (
                    candidate_validation["capital_return_pct"]
                    - control_validation["capital_return_pct"]
                ),
                "beats_bh": (
                    candidate_validation["capital_return_pct"]
                    > candidate_validation["bh_capital_return_pct"]
                ),
                "beats_control": (
                    candidate_validation["capital_return_pct"]
                    > control_validation["capital_return_pct"]
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
        "candidate_bh_multiple": candidate_sum / bh_sum if abs(bh_sum) > 1e-12 else None,
        "candidate_control_multiple": (
            candidate_sum / control_sum if abs(control_sum) > 1e-12 else None
        ),
        "alpha_vs_bh_pp_sum": candidate_sum - bh_sum,
        "delta_vs_control_pp_sum": candidate_sum - control_sum,
        "weighted_tim_pct": sum(
            f["validation_metrics"]["exposure_weighted_tim_pct"]
            * f["validation_metrics"]["rows"]
            for f in folds
        )
        / max(1, rows),
        "all_folds_beat_bh": all(f["beats_bh"] for f in folds),
        "all_folds_beat_control": all(f["beats_control"] for f in folds),
        "all_mandatory_reclaim": all(
            f["validation_metrics"]["bars_flat_beyond_reclaim"] == 0 for f in folds
        ),
        "future_htf_count": sum(
            row["source_timestamp_future_count"] for row in causality.values()
        ),
    }
    aggregate["exposure_policy"] = {
        "cohort": "TOP_10_LONG" if side == "LONG" else "BOTTOM_10_SHORT",
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
        and aggregate["future_htf_count"] == 0
        and aggregate["exposure_policy"]["pass"]
    )
    output = {
        "manifest": {
            "tier": "VEC_RESEARCH",
            "promotion_allowed": False,
            "matrix_written": False,
            "symbol": symbol,
            "side": side,
            "family": args.family,
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
            "commission_bps_one_way": args.commission_bps,
            "slippage_bps_one_way": args.slippage_bps,
            "grid_candidates": len(family_candidates),
            "vector_shortlist_per_fold": args.shortlist,
            "exposure_policy": {
                "cohort": (
                    "TOP_10_LONG" if side == "LONG" else "BOTTOM_10_SHORT"
                ),
                "min_weighted_tim_pct": args.target_tim_low,
                "max_weighted_tim_pct": args.target_tim_high,
            },
            "direct_semantics": (
                "signal-episode start sized by latest completed D/4h/1h band "
                "position using the fold's frozen ladder curve"
            ),
            "filter_semantics": "candidate gate AND frozen ladder event",
            "stoch_semantics": (
                "completed-bar HH+HL and low+rising Stoch for LONG; "
                "completed-bar LH+LL and high+falling Stoch for SHORT; "
                "direct or union with frozen green-arrow schedule"
            ),
            "causality": causality,
        },
        "outer_folds": folds,
        "aggregate": aggregate,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir) / f"entry_overlay_{args.family}_{stamp}_{symbol}_{side}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(
        json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    with gzip.open(out / "frozen_fold_rows.jsonl.gz", "wt") as fh:
        for row in folds:
            fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    source = out / "source_snapshot"
    source.mkdir()
    source.joinpath(Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"artifact": str(out), **aggregate}, sort_keys=True))
    data.z.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-artifact", required=True)
    ap.add_argument(
        "--family",
        required=True,
        choices=("ENTRY_GOLDEN_RULE", "ENTRY_WT_DC", "ENTRY_STOCH_HHHL"),
    )
    ap.add_argument("--npz-dir", default=str(top.DEFAULT_NPZ))
    ap.add_argument("--out-dir", default=str(top.DEFAULT_OUT))
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end")
    ap.add_argument("--shortlist", type=int, default=24)
    ap.add_argument("--target-tim-low", type=float, default=70.0)
    ap.add_argument("--target-tim-high", type=float, default=80.0)
    ap.add_argument("--commission-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
