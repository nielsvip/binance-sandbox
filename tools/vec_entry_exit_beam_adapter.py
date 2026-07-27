#!/usr/bin/env python3
"""Materialize frozen entry-overlay artifacts for the exit beam adapters.

The established same-entry exit scanners originally accepted only plain
ladder artifacts.  This isolated shim supplies their fold-context hook with
the selected single-family overlay from each outer fold.  It never combines
entry families.

DC-tier is position-dependent under its source E02 exit.  Its accepted fold
is therefore materialized once into an exogenous absolute-target schedule
under the source E02 ledger.  Every exit candidate then receives that exact
same schedule.  This is a research freeze, not a claim of live equivalence.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import vec_band_ladder_walkforward as ladder  # noqa: E402
import vec_dc_tier_augment_walkforward as dc_tier  # noqa: E402
import vec_entry_overlay_walkforward as overlay  # noqa: E402
import regime_conditioned_exposure_grid as regime_grid  # noqa: E402
import state_aware_exposure_grid as state_grid  # noqa: E402
import vec_same_entry_e05_adapter as e05  # noqa: E402
import vec_same_entry_exit_adapter as generic  # noqa: E402
import vec_same_entry_peak_giveback_adapter as peak  # noqa: E402

ACTIVE_REGIME_POLICY: str | None = None
ACTIVE_STATE_POLICY: str | None = None


def _source_and_control(
    artifact: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    source = json.loads((artifact / "result.json").read_text())
    manifest = source["manifest"]
    control_path = Path(manifest.get("control_artifact", artifact))
    control = json.loads((control_path / "result.json").read_text())
    control_manifest = control["manifest"]
    # Exit adapters require accounting/provenance fields that overlay
    # manifests intentionally reference through their control artifact.
    for key in (
        "npz",
        "npz_sha256",
        "commission_bps_one_way",
        "slippage_bps_one_way",
        "exit",
    ):
        if key not in manifest and key in control_manifest:
            manifest[key] = control_manifest[key]
    if "npz_sha256" not in manifest:
        manifest["npz_sha256"] = manifest["control_npz_sha256"]
    source.setdefault(
        "frozen_oos_aggregate",
        source.get(
            "aggregate",
            control.get("frozen_oos_aggregate", control.get("aggregate", {})),
        ),
    )
    return source, control, control_path


def _dc_candidate(raw: dict[str, Any]) -> dc_tier.Candidate:
    params = dict(raw)
    params["tier_profile"] = tuple(float(x) for x in params["tier_profile"])
    maturity = params.get("maturity_atr")
    params["maturity_atr"] = (
        None if maturity is None else float(maturity)
    )
    return dc_tier.Candidate(**params)


def _targetized_dc_schedule(
    data: Any,
    htfs: dict[str, Any],
    curve: ladder.Curve,
    selected_candidate: dict[str, Any],
    left: int,
    right: int,
    side: str,
    slippage: float,
) -> tuple[ladder.SignalData, ladder.Curve, dict[str, Any]]:
    """Freeze source-E02 ladder+DC requests as absolute target multipliers."""
    candidate = _dc_candidate(selected_candidate["params"])
    base = ladder._build_signals(data, htfs, curve, 30, side)
    tier, audit = dc_tier.tier_state(
        data,
        htfs,
        side,
        candidate.buffer_fraction,
        candidate.maturity_atr,
    )
    target_mult = np.zeros(len(data.ts), dtype=np.float64)
    side_sign = 1.0 if side == "LONG" else -1.0
    is_long = side == "LONG"
    qty = 0.0
    avg_entry = math.nan
    last_exit_fill = math.nan
    reclaim_level = math.nan
    prior_exit_notional = 0.0
    gap_seen = False
    pending: dict[str, Any] | None = None
    dc_requests = 0

    def has_position() -> bool:
        return side_sign * qty > 1e-12

    for i in range(left, right):
        op = float(data.open[i])
        close = float(data.close[i])
        if pending is not None:
            if pending["kind"] == "exit" and has_position():
                px = op * (1.0 - side_sign * slippage)
                prior_exit_notional = min(
                    ladder.CAPACITY, abs(qty) * px
                )
                last_exit_fill = px
                reclaim_level = (
                    max(px, float(pending["ref"]))
                    if is_long
                    else min(px, float(pending["ref"]))
                )
                qty = 0.0
                avg_entry = math.nan
                gap_seen = False
            elif pending["kind"] == "entry":
                px = op * (1.0 + side_sign * slippage)
                old_shares = abs(qty)
                old_notional = old_shares * px
                desired = min(
                    ladder.CAPACITY,
                    float(pending["target_notional"]),
                )
                actual = max(0.0, desired - old_notional)
                if actual > 0:
                    new_shares = actual / px
                    old_cost = (
                        old_shares * avg_entry
                        if old_shares > 0 and math.isfinite(avg_entry)
                        else 0.0
                    )
                    avg_entry = (old_cost + new_shares * px) / (
                        old_shares + new_shares
                    )
                    qty += side_sign * new_shares
            pending = None
        if i + 1 >= right:
            continue

        notional = abs(qty) * close
        if has_position():
            if base.exit_event[i]:
                ref = float(base.exit_ref[i])
                if not math.isfinite(ref):
                    ref = float(data.high[i] if is_long else data.low[i])
                pending = {"kind": "exit", "ref": ref}
                continue
            if base.entry_mult[i] > 0:
                requested = ladder.BASE_UNIT * float(base.entry_mult[i])
                desired = (
                    requested
                    if curve.semantics == "target"
                    else notional + requested
                )
                desired = min(ladder.CAPACITY, desired)
                target_mult[i] = desired / ladder.BASE_UNIT
                pending = {"kind": "entry", "target_notional": desired}
                continue
            tier_number = int(tier[i])
            if tier_number > 0:
                gain = (
                    side_sign * (close / avg_entry - 1.0) * 100.0
                    if math.isfinite(avg_entry) and avg_entry > 0
                    else -math.inf
                )
                desired = (
                    ladder.BASE_UNIT
                    * float(candidate.tier_profile[tier_number - 1])
                )
                if (
                    gain >= candidate.min_gain_pct
                    and notional
                    < desired * float(candidate.target_fill_ratio)
                ):
                    desired = min(ladder.CAPACITY, desired)
                    target_mult[i] = desired / ladder.BASE_UNIT
                    pending = {"kind": "entry", "target_notional": desired}
                    dc_requests += 1
        else:
            if math.isfinite(last_exit_fill):
                gap_seen |= (
                    float(data.low[i]) < last_exit_fill
                    if is_long
                    else float(data.high[i]) > last_exit_fill
                )
                crossed = (
                    close >= reclaim_level
                    if is_long
                    else close <= reclaim_level
                )
                if crossed:
                    pending = {
                        "kind": "entry",
                        "target_notional": max(
                            ladder.BASE_UNIT, prior_exit_notional
                        ),
                    }
                elif base.entry_mult[i] > 0 and gap_seen:
                    requested = (
                        ladder.BASE_UNIT * float(base.entry_mult[i])
                    )
                    desired = (
                        requested
                        if curve.semantics == "target"
                        else requested
                    )
                    desired = min(ladder.CAPACITY, desired)
                    target_mult[i] = desired / ladder.BASE_UNIT
                    pending = {
                        "kind": "entry",
                        "target_notional": desired,
                    }
            elif base.entry_mult[i] > 0:
                requested = ladder.BASE_UNIT * float(base.entry_mult[i])
                desired = min(ladder.CAPACITY, requested)
                target_mult[i] = desired / ladder.BASE_UNIT
                pending = {"kind": "entry", "target_notional": desired}

    target_curve = dataclasses.replace(curve, semantics="target")
    signals = ladder.SignalData(
        entry_mult=np.ascontiguousarray(target_mult),
        event_tf=base.event_tf,
        exit_event=base.exit_event,
        exit_ref=base.exit_ref,
        causality={
            **base.causality,
            "dc_tier_frozen_materialization": {
                **audit,
                "source_exit": "E02_DONCHIAN_4h_N30",
                "absolute_target_schedule": True,
                "entry_family_blending": False,
                "dc_tier_requests": dc_requests,
            },
        },
        entry_source_ts=overlay._entry_sources(target_mult, htfs),
    )
    return signals, target_curve, {
        "dc_tier_requests": dc_requests,
        "nonzero_entry_requests": int(np.count_nonzero(target_mult)),
    }


def beam_fold_contexts(
    artifact: Path,
    npz_dir: Path,
    *,
    fold_mode: str,
) -> tuple[dict[str, Any], Any, dict[str, Any], list[dict[str, Any]]]:
    source, _, _ = _source_and_control(artifact)
    manifest = source["manifest"]
    symbol = str(manifest["symbol"]).upper()
    side = str(manifest["side"]).upper()
    data = ladder.top._load_execution(
        symbol, npz_dir, "2024-01-01", "ladder", None
    )
    if not data.contract["valid"]:
        data.z.close()
        raise RuntimeError(f"{symbol} quarantined: {data.contract['errors']}")
    current_sha = hashlib.sha256(Path(data.path).read_bytes()).hexdigest()
    if current_sha != manifest["npz_sha256"]:
        data.z.close()
        raise RuntimeError(
            f"{symbol} NPZ hash drift: "
            f"artifact={manifest['npz_sha256']} current={current_sha}"
        )
    htfs = {
        tf: ladder.top._compress_htf(data, tf)
        for tf in ("5m", "15m", "1h", "4h", "D", "W")
    }
    folds = list(source["outer_folds"])
    if fold_mode == "latest":
        folds = folds[-1:]
    contexts = []
    slippage = float(manifest["slippage_bps_one_way"]) / 10_000.0
    family = str(manifest.get("family", "ENTRY_LADDER_GREEN"))
    for fold in folds:
        curve = ladder.Curve(
            **fold.get("curve", fold.get("selected_curve"))
        )
        left = ladder._date_index(data, fold["validation"][0])
        right = ladder._date_index(data, fold["validation"][1])
        materialization: dict[str, Any] = {
            "entry_family": family,
            "entry_blending": False,
        }
        if family == "ENTRY_DC_TIER_AUG_ENABLED":
            signals, curve, extra = _targetized_dc_schedule(
                data,
                htfs,
                curve,
                fold["selected_candidate"],
                left,
                right,
                side,
                slippage,
            )
            materialization.update(extra)
        elif "selected_candidate" in fold:
            signals = overlay.build_frozen_overlay_signals(
                data,
                htfs,
                curve,
                fold["selected_candidate"],
                side,
            )
            materialization["selected_candidate"] = fold[
                "selected_candidate"
            ]
        else:
            signals = ladder._build_signals(
                data, htfs, curve, int(manifest["exit"]["n"]), side
            )
        contexts.append(
            {
                "fold": int(fold["fold"]),
                "validation": list(fold["validation"]),
                "left": left,
                "right": right,
                "curve": curve,
                "signals": signals,
                "entry_beam_materialization": materialization,
            }
        )
    if ACTIVE_REGIME_POLICY is not None:
        policy = next(
            row
            for row in regime_grid.policy_grid()
            if row.name == ACTIVE_REGIME_POLICY
        )
        view, audit = overlay._causal_npz_view(data, htfs)
        if any(
            row["source_timestamp_future_count"] for row in audit.values()
        ):
            data.z.close()
            raise RuntimeError("future source in regime feature view")
        regime = regime_grid.classify_completed_regime(view, side)
        rows = np.arange(len(data.ts), dtype=np.int64)
        slots = np.searchsorted(
            np.asarray(htfs["1h"].event_index, dtype=np.int64),
            rows,
            side="right",
        ) - 1
        for ctx in contexts:
            left, right = int(ctx["left"]), int(ctx["right"])
            conditioned, condition_audit = regime_grid.apply_policy(
                ctx["signals"].entry_mult[left:right],
                regime[left:right],
                slots[left:right],
                policy,
            )
            entry_mult = np.zeros(len(data.ts), dtype=np.float64)
            entry_mult[left:right] = conditioned
            original = ctx["signals"]
            ctx["signals"] = ladder.SignalData(
                entry_mult=np.ascontiguousarray(entry_mult),
                event_tf=original.event_tf,
                exit_event=original.exit_event,
                exit_ref=original.exit_ref,
                causality={
                    **original.causality,
                    "regime_conditioned_exposure": {
                        **condition_audit,
                        "classifier_completed_only": True,
                        "classifier_symbol_thresholds": False,
                    },
                },
                entry_source_ts=overlay._entry_sources(entry_mult, htfs),
            )
            ctx["entry_beam_materialization"][
                "regime_conditioned_exposure"
            ] = condition_audit
    if ACTIVE_STATE_POLICY is not None:
        policy = next(
            row
            for row in state_grid.policy_grid()
            if row.name == ACTIVE_STATE_POLICY
        )
        rows = np.arange(len(data.ts), dtype=np.int64)
        slots = np.searchsorted(
            np.asarray(htfs["1h"].event_index, dtype=np.int64),
            rows,
            side="right",
        ) - 1
        for ctx in contexts:
            left, right = int(ctx["left"]), int(ctx["right"])
            original = ctx["signals"]
            conditioned, condition_audit = state_grid.apply_policy(
                original.entry_mult[left:right],
                original.exit_event[left:right],
                original.exit_ref[left:right],
                data.open[left:right],
                data.high[left:right],
                data.low[left:right],
                slots[left:right],
                policy,
                side=side,
                target_semantics=ctx["curve"].semantics == "target",
            )
            entry_mult = np.zeros(len(data.ts), dtype=np.float64)
            entry_mult[left:right] = conditioned
            ctx["signals"] = ladder.SignalData(
                entry_mult=np.ascontiguousarray(entry_mult),
                event_tf=original.event_tf,
                exit_event=original.exit_event,
                exit_ref=original.exit_ref,
                causality={
                    **original.causality,
                    "state_aware_exposure": {
                        **condition_audit,
                        "completed_1h_event_time_only": True,
                        "market_regime_features": False,
                        "symbol_specific_thresholds": False,
                        "source_e02_state_exogenous": True,
                    },
                },
                entry_source_ts=overlay._entry_sources(entry_mult, htfs),
            )
            ctx["entry_beam_materialization"][
                "state_aware_exposure"
            ] = condition_audit
    return source, data, htfs, contexts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", choices=("generic", "e05", "peak"), required=True)
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--families", default="")
    ap.add_argument("--fold-mode", default="nested")
    ap.add_argument(
        "--regime-policy",
        choices=tuple(row.name for row in regime_grid.policy_grid()),
    )
    ap.add_argument(
        "--state-policy",
        choices=tuple(row.name for row in state_grid.policy_grid()),
    )
    ap.add_argument("--exposure-min-pct", type=float, default=70.0)
    ap.add_argument("--exposure-max-pct", type=float, default=80.0)
    args = ap.parse_args()

    global ACTIVE_REGIME_POLICY, ACTIVE_STATE_POLICY
    ACTIVE_REGIME_POLICY = args.regime_policy
    ACTIVE_STATE_POLICY = args.state_policy
    if ACTIVE_REGIME_POLICY is not None and ACTIVE_STATE_POLICY is not None:
        raise ValueError("market-regime and state-aware policies may not be blended")
    generic._fold_contexts = beam_fold_contexts
    e05.shared._fold_contexts = beam_fold_contexts
    peak.shared._fold_contexts = beam_fold_contexts
    if args.adapter == "generic":
        payload = generic.screen_artifact(
            args.artifact.resolve(),
            args.npz_dir.resolve(),
            families=tuple(args.families.split(",")),
            fold_mode=args.fold_mode,
            exposure_min_pct=args.exposure_min_pct,
            exposure_max_pct=args.exposure_max_pct,
        )
    elif args.adapter == "e05":
        payload = e05.screen_artifact(
            args.artifact.resolve(),
            args.npz_dir.resolve(),
            args.exposure_min_pct,
            args.exposure_max_pct,
        )
    else:
        payload = peak.screen_artifact(
            args.artifact.resolve(),
            args.npz_dir.resolve(),
            args.exposure_min_pct,
            args.exposure_max_pct,
        )
    payload["entry_beam_adapter"] = {
        "one_frozen_entry_family": True,
        "entry_blending": False,
        "context_materializer": __file__,
        "regime_policy": args.regime_policy,
        "state_policy": args.state_policy,
    }
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "symbol": payload["symbol"],
                "side": payload["side"],
                "adapter": args.adapter,
                "candidates": payload["candidate_count"],
                "survivors": payload["survivor_count"],
                "output": str(args.out_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
