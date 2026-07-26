#!/usr/bin/env python3
"""Research-only structural-break -> lower rebound top + WT1 exit experiment.

The proposed path is intentionally causal:

* a completed 4h structural break/lower low arms an obligation;
* a later completed 1h rebound must form below the pre-break price top and
  below the pre-break WT1 top;
* a distinct subsequent 1h adverse structure bar plus WT1 rollover fires;
* execution is at the next RTH open;
* E11 may re-enter lower, while E10 zero-buffer reclaim remains mandatory and
  latched until filled.

LONG and SHORT are entirely separate.  This script never writes live config,
the switch matrix, or promotion state.
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


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import vec_top_exit_campaign as base  # noqa: E402


@dataclasses.dataclass(frozen=True)
class Fold:
    label: str
    start: str
    end: str | None


FOLDS = (
    Fold("FROZEN_2025Q4", "2025-10-01", "2026-01-01"),
    Fold("FROZEN_2026_JAN_FEB", "2026-01-01", "2026-03-01"),
    Fold("RECENT_GAP_CLEAN", "2026-06-10", None),
)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _completed_feature(data: base.ExecutionData, htf: base.HTFData, key: str) -> np.ndarray:
    full_event = data.full_indices[htf.event_index]
    if key not in data.z.files:
        raise KeyError(f"{data.symbol}: missing {key}")
    return np.ascontiguousarray(
        np.asarray(data.z[key], dtype=np.float64)[full_event]
    )


def structural_wt_rebound_signal(
    arm_h: base.HTFData,
    trigger_h: base.HTFData,
    wt_arm: np.ndarray,
    wt_trigger: np.ndarray,
    side: int,
    *,
    rebound_atr: float = 0.5,
    prebreak_lookback: int = 6,
    max_wait_1h: int = 30,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """Return completed-1h causal exit events and stored rebound-top refs."""
    if side not in (-1, 1):
        raise ValueError("side must be +1 LONG or -1 SHORT")
    n = len(trigger_h.close)
    event = np.zeros(n, dtype=np.uint8)
    ref = np.full(n, np.nan, dtype=np.float64)
    stats = {
        "arms": 0,
        "rebound_seen": 0,
        "confirmed_exits": 0,
        "expired": 0,
        "invalidated": 0,
    }
    arm_slot = np.searchsorted(
        arm_h.source_ts, trigger_h.source_ts, side="right"
    ) - 1
    state = 0  # 0 idle, 1 armed/wait rebound, 2 rebound top observed
    last_arm_seen = -1
    wait = 0
    damage_extreme = math.nan
    rebound_price = math.nan
    rebound_wt = math.nan
    rebound_bar = -1
    prebreak_price_top = math.nan
    prebreak_wt_top = math.nan

    for j in range(2, n):
        armed_now = False
        k = int(arm_slot[j])
        if k >= max(1, prebreak_lookback) and k != last_arm_seen:
            last_arm_seen = k
            if side > 0:
                structural_break = (
                    arm_h.low[k] < arm_h.low[k - 1]
                    and arm_h.close[k] < arm_h.low[k - 1]
                )
            else:
                structural_break = (
                    arm_h.high[k] > arm_h.high[k - 1]
                    and arm_h.close[k] > arm_h.high[k - 1]
                )
            if structural_break and state == 0:
                prior = slice(k - prebreak_lookback, k)
                if side > 0:
                    prebreak_price_top = float(np.nanmax(arm_h.high[prior]))
                    prebreak_wt_top = float(np.nanmax(wt_arm[prior]))
                    damage_extreme = float(trigger_h.low[j])
                    rebound_price = -math.inf
                    rebound_wt = -math.inf
                else:
                    prebreak_price_top = float(np.nanmin(arm_h.low[prior]))
                    prebreak_wt_top = float(np.nanmin(wt_arm[prior]))
                    damage_extreme = float(trigger_h.high[j])
                    rebound_price = math.inf
                    rebound_wt = math.inf
                if (
                    math.isfinite(prebreak_price_top)
                    and math.isfinite(prebreak_wt_top)
                ):
                    state = 1
                    wait = 0
                    rebound_bar = -1
                    stats["arms"] += 1
                    armed_now = True

        # The structural break bar cannot also count as the later rebound.
        if state == 0 or k < 0 or armed_now:
            continue
        wait += 1
        atr = float(arm_h.atr[k])
        if not (math.isfinite(atr) and atr > 0):
            continue

        if side > 0:
            damage_extreme = min(damage_extreme, float(trigger_h.low[j]))
            if trigger_h.high[j] >= rebound_price:
                rebound_price = float(trigger_h.high[j])
                rebound_wt = max(rebound_wt, float(wt_trigger[j]))
                rebound_bar = j
            enough_rebound = rebound_price - damage_extreme >= rebound_atr * atr
            lower_price_top = rebound_price < prebreak_price_top
            lower_wt_top = rebound_wt < prebreak_wt_top
            adverse_price = (
                j > rebound_bar >= 0
                and trigger_h.high[j] < trigger_h.high[j - 1]
                and trigger_h.low[j] < trigger_h.low[j - 1]
                and trigger_h.close[j] < trigger_h.close[j - 1]
            )
            wt_rollover = wt_trigger[j] < wt_trigger[j - 1] <= rebound_wt
            invalid = (
                trigger_h.close[j] > prebreak_price_top
                or wt_trigger[j] > prebreak_wt_top
            )
        else:
            damage_extreme = max(damage_extreme, float(trigger_h.high[j]))
            if trigger_h.low[j] <= rebound_price:
                rebound_price = float(trigger_h.low[j])
                rebound_wt = min(rebound_wt, float(wt_trigger[j]))
                rebound_bar = j
            enough_rebound = damage_extreme - rebound_price >= rebound_atr * atr
            lower_price_top = rebound_price > prebreak_price_top
            lower_wt_top = rebound_wt > prebreak_wt_top
            adverse_price = (
                j > rebound_bar >= 0
                and trigger_h.high[j] > trigger_h.high[j - 1]
                and trigger_h.low[j] > trigger_h.low[j - 1]
                and trigger_h.close[j] > trigger_h.close[j - 1]
            )
            wt_rollover = wt_trigger[j] > wt_trigger[j - 1] >= rebound_wt
            invalid = (
                trigger_h.close[j] < prebreak_price_top
                or wt_trigger[j] < prebreak_wt_top
            )

        if state == 1 and enough_rebound and lower_price_top and lower_wt_top:
            state = 2
            stats["rebound_seen"] += 1
            # Confirmation must occur on a distinct later completed bar.
            continue
        if state == 2 and adverse_price and wt_rollover:
            event[j] = 1
            ref[j] = rebound_price
            state = 0
            stats["confirmed_exits"] += 1
        elif invalid:
            state = 0
            stats["invalidated"] += 1
        elif wait >= max_wait_1h:
            state = 0
            stats["expired"] += 1
    return event, ref, stats


def _proposed_candidate(
    data: base.ExecutionData,
    htfs: dict[str, base.HTFData],
    side: int,
    *,
    rebound_atr: float = 0.5,
    prebreak_lookback: int = 6,
    max_wait_1h: int = 30,
    gate_label: str = "UNGATED",
    min_profit_gate: float = -1.0,
    min_mfe_atr_gate: float = -1.0,
) -> tuple[base.ExitCandidate, dict[str, int]]:
    wt4 = _completed_feature(data, htfs["4h"], "wt1_4h")
    wt1 = _completed_feature(data, htfs["1h"], "wt1_1h")
    event_h, ref_h, stats = structural_wt_rebound_signal(
        htfs["4h"],
        htfs["1h"],
        wt4,
        wt1,
        side,
        rebound_atr=rebound_atr,
        prebreak_lookback=prebreak_lookback,
        max_wait_1h=max_wait_1h,
    )
    event, ref = base._map_events(len(data.ts), htfs["1h"], event_h, ref_h)
    atr_exec = base._align_feature(len(data.ts), htfs["4h"], htfs["4h"].atr)
    return (
        base.ExitCandidate(
            family="STRUCTURAL_WT_LOWER_REBOUND_TOP",
            label=(
                f"E14_4h_BREAK__1h_TOP__R{rebound_atr:g}_"
                f"L{prebreak_lookback}_W{max_wait_1h}__{gate_label}"
            ),
            params={
                "arm_tf": "4h",
                "confirm_tf": "1h",
                "rebound_atr": rebound_atr,
                "prebreak_lookback": prebreak_lookback,
                "max_wait_1h": max_wait_1h,
                "gate": gate_label,
                "min_profit_above_round_trip_cost": min_profit_gate,
                "min_prior_mfe_atr": min_mfe_atr_gate,
                "completed_bars_only": True,
            },
            exit_mode=2,
            exit_event=event,
            raw_stop=np.full(len(data.ts), np.nan, dtype=np.float64),
            struct_ref=ref,
            atr_exec=atr_exec,
            min_profit_gate=min_profit_gate,
            min_mfe_atr_gate=min_mfe_atr_gate,
        ),
        stats,
    )


def _dc_low4_candidate(data: base.ExecutionData, side: int) -> base.ExitCandidate:
    key = "dc_low4_5m" if side > 0 else "dc_high4_5m"
    level = np.asarray(data.z[key], dtype=np.float64)[data.full_indices]
    atr = np.asarray(data.z["atr_5m"], dtype=np.float64)[data.full_indices]
    return base.ExitCandidate(
        family="DC_LOW4_5M" if side > 0 else "DC_HIGH4_5M",
        label=f"{key.upper()}_IMMEDIATE",
        params={
            "tf": "5m",
            "key": key,
            "semantics": "freeze level at each entry/reentry",
        },
        exit_mode=4,
        exit_event=np.zeros(len(data.ts), dtype=np.uint8),
        raw_stop=np.ascontiguousarray(level),
        struct_ref=np.ascontiguousarray(level),
        atr_exec=np.ascontiguousarray(atr),
    )


def _no_exit_candidate(data: base.ExecutionData) -> base.ExitCandidate:
    return base.ExitCandidate(
        family="NO_EXIT_RUNNER",
        label="NO_EXIT_RUNNER",
        params={},
        exit_mode=2,
        exit_event=np.zeros(len(data.ts), dtype=np.uint8),
        raw_stop=np.full(len(data.ts), np.nan, dtype=np.float64),
        struct_ref=np.full(len(data.ts), np.nan, dtype=np.float64),
        atr_exec=np.full(len(data.ts), 1.0, dtype=np.float64),
    )


def _loss_metrics(events: list[dict[str, Any]], side: int, cost_rate: float) -> dict[str, Any]:
    returns = []
    for event in events:
        if event["type"] != "EXIT":
            continue
        raw = side * (float(event["fill_px"]) - float(event["entry_px"])) / float(
            event["entry_px"]
        )
        returns.append(100.0 * (raw - 2.0 * cost_rate))
    return {
        "losing_exits": sum(value < 0 for value in returns),
        "winning_exits": sum(value > 0 for value in returns),
        "mean_exit_leg_return_pct": float(np.mean(returns)) if returns else None,
        "total_exit_leg_return_pct": float(np.sum(returns)) if returns else 0.0,
        "worst_exit_leg_return_pct": float(np.min(returns)) if returns else None,
    }


def _run_fold(
    symbol: str,
    side_name: str,
    fold: Fold,
    npz_dir: Path,
    lib: Any,
    cost_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    side = 1 if side_name == "LONG" else -1
    data = base._load_execution(symbol, npz_dir, fold.start, "ladder", fold.end)
    htfs = {tf: base._compress_htf(data, tf) for tf in ("1h", "4h")}
    lower_event = base._lower_reentry_events(len(data.ts), htfs["1h"], side)
    proposed, state_stats = _proposed_candidate(data, htfs, side)
    candidates = [proposed, _dc_low4_candidate(data, side), _no_exit_candidate(data)]
    cost_rate = cost_bps / 10_000.0
    slip_rate = slippage_bps / 10_000.0
    bh = base._side_bh(data, side, cost_rate, slip_rate)
    reentry = ("E11_G1+E10_RB0_LATCHED", 2, 0.0, 1.0)
    rows = []
    for candidate in candidates:
        row = base._scan(
            lib,
            data,
            candidate,
            lower_event,
            side,
            reentry,
            cost_rate,
            slip_rate,
            bh,
        )
        reference, events = base._reference_replay(
            data,
            candidate,
            lower_event,
            side,
            reentry,
            cost_rate,
            slip_rate,
        )
        parity = {
            "gain_pct": abs(row["gain_pct"] - reference["gain_pct"]) <= 1e-8,
            "tim_rth_pct": abs(row["tim_rth_pct"] - reference["tim_rth_pct"]) <= 1e-8,
            "technical_exits": row["technical_exits"] == reference["technical_exits"],
            "reentries": row["reentries"] == reference["reentries"],
        }
        if not all(parity.values()):
            data.z.close()
            raise AssertionError(f"{symbol} {fold.label} parity failed: {parity}")
        row.update(
            {
                "fold": fold.label,
                "start": fold.start,
                "end_exclusive": fold.end,
                "contract_valid": bool(data.contract["valid"]),
                "contract_errors": data.contract["errors"],
                "contract_warnings": data.contract["warnings"],
                "quarantine_diagnostic": not bool(data.contract["valid"]),
                "reentry_obligation_latched": True,
                "reference_parity": parity,
                **_loss_metrics(events, side, cost_rate),
            }
        )
        if candidate.family == "STRUCTURAL_WT_LOWER_REBOUND_TOP":
            row["signal_state_counts"] = state_stats
        rows.append(row)
    output = {
        "symbol": symbol,
        "side": side_name,
        "fold": dataclasses.asdict(fold),
        "npz_sha256": base._sha256_file(data.path),
        "rows": len(data.ts),
        "first_ts": int(data.ts[0]),
        "last_ts": int(data.ts[-1]),
        "contract_valid": bool(data.contract["valid"]),
        "results": rows,
    }
    data.z.close()
    return output


def _aggregate(folds: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    families = sorted(
        {
            row["family"]
            for fold in folds
            for row in fold["results"]
        }
    )
    for family in families:
        rows = [
            row
            for fold in folds
            for row in fold["results"]
            if row["family"] == family
        ]
        output[family] = {
            "folds": len(rows),
            "valid_folds": sum(not row["quarantine_diagnostic"] for row in rows),
            "mean_gain_pct": float(np.mean([row["gain_pct"] for row in rows])),
            "median_alpha_vs_bh_pp": float(
                np.median([row["alpha_vs_bh_pp"] for row in rows])
            ),
            "positive_alpha_fold_rate": float(
                np.mean([row["alpha_vs_bh_pp"] > 0 for row in rows])
            ),
            "mean_tim_rth_pct": float(np.mean([row["tim_rth_pct"] for row in rows])),
            "technical_exits": int(sum(row["technical_exits"] for row in rows)),
            "reentries": int(sum(row["reentries"] for row in rows)),
            "losing_exits": int(sum(row["losing_exits"] for row in rows)),
            "winning_exits": int(sum(row["winning_exits"] for row in rows)),
            "all_reclaim_latched": all(
                row["reentry_obligation_latched"]
                and row["mandatory_reclaim_policy"]
                for row in rows
            ),
        }
    return output


def _grid_specs() -> list[dict[str, Any]]:
    gates = (
        ("UNGATED", -1.0, -1.0),
        ("PROFIT_COST_PLUS_0", 0.0, -1.0),
        ("PROFIT_COST_PLUS_0.25PCT", 0.0025, -1.0),
        ("PROFIT_COST_PLUS_0.50PCT", 0.005, -1.0),
        ("PRIOR_MFE_0.5ATR", -1.0, 0.5),
        ("PRIOR_MFE_1ATR", -1.0, 1.0),
        ("PRIOR_MFE_2ATR", -1.0, 2.0),
    )
    return [
        {
            "rebound_atr": rebound,
            "prebreak_lookback": lookback,
            "max_wait_1h": wait,
            "gate_label": gate[0],
            "min_profit_gate": gate[1],
            "min_mfe_atr_gate": gate[2],
        }
        for rebound, lookback, wait, gate in itertools.product(
            (0.25, 0.5, 1.0),
            (4, 6, 10),
            (12, 20, 30),
            gates,
        )
    ]


def _scan_grid_fold(
    symbol: str,
    side_name: str,
    fold: Fold,
    specs: list[dict[str, Any]],
    npz_dir: Path,
    lib: Any,
    cost_bps: float,
    slippage_bps: float,
    *,
    reference_selected: bool = False,
    reentry_mode: int = 2,
) -> list[dict[str, Any]]:
    side = 1 if side_name == "LONG" else -1
    data = base._load_execution(symbol, npz_dir, fold.start, "ladder", fold.end)
    htfs = {tf: base._compress_htf(data, tf) for tf in ("1h", "4h")}
    lower_event = base._lower_reentry_events(len(data.ts), htfs["1h"], side)
    cost_rate = cost_bps / 10_000.0
    slip_rate = slippage_bps / 10_000.0
    bh = base._side_bh(data, side, cost_rate, slip_rate)
    reentry = (
        (
            "E11_G1+E10_RB0_RESTING"
            if reentry_mode == 4
            else "E11_G1+E10_RB0_DELAYED"
        ),
        reentry_mode,
        0.0,
        1.0,
    )
    rows = []
    for spec in specs:
        candidate, state_stats = _proposed_candidate(data, htfs, side, **spec)
        row = base._scan(
            lib,
            data,
            candidate,
            lower_event,
            side,
            reentry,
            cost_rate,
            slip_rate,
            bh,
        )
        row.update(
            {
                "symbol": symbol,
                "side": side_name,
                "fold": fold.label,
                "fold_start": fold.start,
                "fold_end_exclusive": fold.end,
                "spec": spec,
                "signal_state_counts": state_stats,
                "contract_valid": bool(data.contract["valid"]),
                "contract_errors": data.contract["errors"],
                "contract_warnings": data.contract["warnings"],
                "quarantine_diagnostic": not bool(data.contract["valid"]),
                "reentry_obligation_latched": True,
            }
        )
        if reference_selected:
            reference, _ = base._reference_replay(
                data,
                candidate,
                lower_event,
                side,
                reentry,
                cost_rate,
                slip_rate,
            )
            row["reference_parity"] = {
                "gain_pct": abs(row["gain_pct"] - reference["gain_pct"]) <= 1e-8,
                "tim_rth_pct": (
                    abs(row["tim_rth_pct"] - reference["tim_rth_pct"]) <= 1e-8
                ),
                "technical_exits": (
                    row["technical_exits"] == reference["technical_exits"]
                ),
                "reentries": row["reentries"] == reference["reentries"],
                "rejected_exit_signals": (
                    row["rejected_exit_signals"]
                    == reference["rejected_exit_signals"]
                ),
            }
            if not all(row["reference_parity"].values()):
                data.z.close()
                raise AssertionError(
                    f"selected parity failed {symbol} {fold.label}: "
                    f"{row['reference_parity']}"
                )
        rows.append(row)
    data.z.close()
    return rows


def _spec_key(spec: dict[str, Any]) -> str:
    return json.dumps(spec, sort_keys=True, separators=(",", ":"))


def _run_grid(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    """Select once on 2025Q4 MU+VT, freeze for later folds."""
    lib = base._compile_scanner()
    specs = _grid_specs()
    discovery = []
    discovery_fold = FOLDS[0]
    for symbol in ("MU", "VT"):
        discovery.extend(
            _scan_grid_fold(
                symbol,
                "LONG",
                discovery_fold,
                specs,
                args.npz_dir,
                lib,
                args.cost_bps,
                args.slippage_bps,
            )
        )
    by_spec: dict[str, list[dict[str, Any]]] = {}
    for row in discovery:
        by_spec.setdefault(_spec_key(row["spec"]), []).append(row)
    ranked = []
    for key, rows in by_spec.items():
        alpha = np.array([row["alpha_vs_bh_pp"] for row in rows], dtype=float)
        exits = sum(row["technical_exits"] for row in rows)
        losses = sum(row["losing_exits"] for row in rows)
        rejected = sum(row["rejected_exit_signals"] for row in rows)
        loss_rate = losses / exits if exits else 1.0
        # Fail field-by-field overfit: reward the weakest symbol, then median;
        # explicitly penalize losing exits and degenerate no-trade settings.
        score = (
            float(np.min(alpha))
            + 0.25 * float(np.median(alpha))
            - 5.0 * loss_rate
            - (100.0 if exits < 4 else 0.0)
        )
        ranked.append(
            {
                "spec": rows[0]["spec"],
                "score": score,
                "min_alpha_pp": float(np.min(alpha)),
                "median_alpha_pp": float(np.median(alpha)),
                "positive_symbol_rate": float(np.mean(alpha > 0)),
                "technical_exits": exits,
                "losing_exits": losses,
                "losing_exit_rate": loss_rate,
                "rejected_exit_signals": rejected,
                "discovery_rows": rows,
            }
        )
    ranked.sort(
        key=lambda row: (
            row["score"],
            row["positive_symbol_rate"],
            -row["losing_exit_rate"],
        ),
        reverse=True,
    )
    selected = ranked[0]
    selected_spec = selected["spec"]

    frozen = []
    for symbol in ("MU", "VT"):
        for fold in FOLDS[1:]:
            frozen.extend(
                _scan_grid_fold(
                    symbol,
                    "LONG",
                    fold,
                    [selected_spec],
                    args.npz_dir,
                    lib,
                    args.cost_bps,
                    args.slippage_bps,
                    reference_selected=True,
                )
            )
    # HAO uses the universal MU+VT discovery choice and remains quarantined.
    frozen.extend(
        _scan_grid_fold(
            "HAO",
            "SHORT",
            FOLDS[2],
            [selected_spec],
            args.npz_dir,
            lib,
            args.cost_bps,
            args.slippage_bps,
            reference_selected=True,
        )
    )
    valid_long = [
        row
        for row in frozen
        if row["side"] == "LONG" and not row["quarantine_diagnostic"]
    ]
    validation = {
        "valid_long_folds": len(valid_long),
        "positive_alpha_fold_rate": (
            float(np.mean([row["alpha_vs_bh_pp"] > 0 for row in valid_long]))
            if valid_long
            else None
        ),
        "median_alpha_vs_bh_pp": (
            float(np.median([row["alpha_vs_bh_pp"] for row in valid_long]))
            if valid_long
            else None
        ),
        "technical_exits": sum(row["technical_exits"] for row in valid_long),
        "losing_exits": sum(row["losing_exits"] for row in valid_long),
        "winning_exits": sum(row["winning_exits"] for row in valid_long),
        "rejected_exit_signals": sum(
            row["rejected_exit_signals"] for row in valid_long
        ),
        "all_reference_parity": all(
            all(row.get("reference_parity", {}).values()) for row in valid_long
        )
        if valid_long
        else False,
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_root / f"structural_wt_profit_grid_{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "promotion_eligible": False,
        "live_config_write": False,
        "selection": (
            "one universal spec selected on MU_LONG+VT_LONG FROZEN_2025Q4 "
            "only; frozen before 2026 Jan-Feb and recent validation"
        ),
        "grid_size": len(specs),
        "selected": selected,
        "top20_discovery": ranked[:20],
        "frozen_validation": frozen,
        "valid_long_validation": validation,
        "baseline_artifact": str(args.baseline_artifact),
        "baseline_reused_not_rerun": True,
    }
    (out / "digest.json").write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
            default=_json_default,
        )
        + "\n"
    )
    with gzip.open(out / "discovery_grid.jsonl.gz", "wt") as handle:
        for row in discovery:
            handle.write(
                json.dumps(
                    row,
                    sort_keys=True,
                    allow_nan=False,
                    default=_json_default,
                )
                + "\n"
            )
    lines = [
        "# Profit/MFE-Gated Structural WT Exit Grid",
        "",
        payload["selection"] + ".",
        "",
        f"Selected: `{selected_spec}`",
        "",
        "| Symbol/side | Validation fold | Gain | B&H | Alpha | TIM | Exits | Win/Loss exits | Rejected signals | Data |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in frozen:
        lines.append(
            f"| {row['symbol']}_{row['side']} | {row['fold']} | "
            f"{row['gain_pct']:+.2f}% | {row['bh_net_side_pct']:+.2f}% | "
            f"{row['alpha_vs_bh_pp']:+.2f}pp | {row['tim_rth_pct']:.1f}% | "
            f"{row['technical_exits']} | {row['winning_exits']}/{row['losing_exits']} | "
            f"{row['rejected_exit_signals']} | "
            f"{'VALID' if not row['quarantine_diagnostic'] else 'QUARANTINE'} |"
        )
    lines.extend(
        [
            "",
            "All rejected lower-top signals remain counted. A rejected signal "
            "does not alter the position or the latched reentry obligation.",
            "",
            f"Baseline reused: `{args.baseline_artifact}`.",
            "",
            "## Decision",
            "",
            f"Valid LONG validation: "
            f"{validation['winning_exits']} winning / "
            f"{validation['losing_exits']} losing executed exits, "
            f"{validation['rejected_exit_signals']} rejected signals, "
            f"{validation['positive_alpha_fold_rate']*100:.1f}% folds above "
            f"B&H, median alpha {validation['median_alpha_vs_bh_pp']:+.2f}pp.",
            "",
            "A zero losing-exit count is not sufficient to create alpha: "
            "mandatory reclaim may fill materially worse after a gap. Preserve "
            "the profit/MFE gate and target reclaim execution or a partial "
            "runner next; do not keep searching lower-top fields in isolation.",
            "",
        ]
    )
    (out / "DECISION_REPORT.md").write_text("\n".join(lines))
    return out, payload


def _run_resting_compare(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    source = json.loads((args.grid_artifact / "digest.json").read_text())
    selected_spec = source["selected"]["spec"]
    source_rows = [
        row
        for row in source["frozen_validation"]
        if row["side"] == "LONG" and not row["quarantine_diagnostic"]
    ]
    fold_by_label = {fold.label: fold for fold in FOLDS}
    lib = base._compile_scanner()
    delayed_rows = []
    resting_rows = []
    for source_row in source_rows:
        delayed_rows.extend(
            _scan_grid_fold(
                source_row["symbol"],
                source_row["side"],
                fold_by_label[source_row["fold"]],
                [selected_spec],
                args.npz_dir,
                lib,
                args.cost_bps,
                args.slippage_bps,
                reference_selected=True,
                reentry_mode=2,
            )
        )
        resting_rows.extend(
            _scan_grid_fold(
                source_row["symbol"],
                source_row["side"],
                fold_by_label[source_row["fold"]],
                [selected_spec],
                args.npz_dir,
                lib,
                args.cost_bps,
                args.slippage_bps,
                reference_selected=True,
                reentry_mode=4,
            )
        )
    old_by_key = {(row["symbol"], row["fold"]): row for row in delayed_rows}
    comparisons = []
    for new in resting_rows:
        old = old_by_key[(new["symbol"], new["fold"])]
        comparisons.append(
            {
                "symbol": new["symbol"],
                "side": new["side"],
                "fold": new["fold"],
                "old_delayed": old,
                "resting": new,
                "delta_gain_pct_points": new["gain_pct"] - old["gain_pct"],
                "delta_alpha_pct_points": (
                    new["alpha_vs_bh_pp"] - old["alpha_vs_bh_pp"]
                ),
                "overshoot_reduction_pct_points": (
                    old["mean_reclaim_overshoot_pct"]
                    - new["mean_reclaim_overshoot_pct"]
                ),
                "missed_move_reduction_pct_points": (
                    old["mean_missed_move_pct"]
                    - new["mean_missed_move_pct"]
                ),
            }
        )
    valid = [item["resting"] for item in comparisons]
    aggregate = {
        "folds": len(comparisons),
        "old_median_alpha_pp": float(
            np.median([item["old_delayed"]["alpha_vs_bh_pp"] for item in comparisons])
        ),
        "resting_median_alpha_pp": float(
            np.median([item["resting"]["alpha_vs_bh_pp"] for item in comparisons])
        ),
        "median_gain_improvement_pp": float(
            np.median([item["delta_gain_pct_points"] for item in comparisons])
        ),
        "old_mean_reclaim_overshoot_pct": float(
            np.mean(
                [
                    item["old_delayed"]["mean_reclaim_overshoot_pct"]
                    for item in comparisons
                ]
            )
        ),
        "resting_mean_reclaim_overshoot_pct": float(
            np.mean(
                [
                    item["resting"]["mean_reclaim_overshoot_pct"]
                    for item in comparisons
                ]
            )
        ),
        "old_mean_missed_move_pct": float(
            np.mean(
                [item["old_delayed"]["mean_missed_move_pct"] for item in comparisons]
            )
        ),
        "resting_mean_missed_move_pct": float(
            np.mean(
                [item["resting"]["mean_missed_move_pct"] for item in comparisons]
            )
        ),
        "positive_alpha_fold_rate": float(
            np.mean([row["alpha_vs_bh_pp"] > 0 for row in valid])
        ),
        "all_reference_parity": all(
            all(row["reference_parity"].values()) for row in valid
        ),
        "all_mandatory_reclaim": all(
            row["mandatory_reclaim_policy"] for row in valid
        ),
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_root / f"resting_reclaim_compare_{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "promotion_eligible": False,
        "live_config_write": False,
        "source_grid_artifact": str(args.grid_artifact),
        "selected_spec_frozen": selected_spec,
        "old_model": "close reclaim signal -> next RTH open",
        "resting_model": (
            "persistent stop: adverse gap fills at open+slippage; otherwise "
            "intrabar touch fills stored reclaim level+slippage"
        ),
        "comparisons": comparisons,
        "aggregate": aggregate,
    }
    (out / "digest.json").write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
            default=_json_default,
        )
        + "\n"
    )
    lines = [
        "# Persistent Resting Reclaim vs Delayed E10",
        "",
        "The exit specification was frozen from the prior discovery grid. Only "
        "reclaim execution changed.",
        "",
        "| Symbol | Fold | B&H | Old gain | Resting gain | Gain delta | Old overshoot | Resting overshoot | Old missed move | Resting missed move |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in comparisons:
        old, new = item["old_delayed"], item["resting"]
        lines.append(
            f"| {item['symbol']}_{item['side']} | {item['fold']} | "
            f"{new['bh_net_side_pct']:+.2f}% | {old['gain_pct']:+.2f}% | "
            f"{new['gain_pct']:+.2f}% | {item['delta_gain_pct_points']:+.2f}pp | "
            f"{old['mean_reclaim_overshoot_pct']:.2f}% | "
            f"{new['mean_reclaim_overshoot_pct']:.2f}% | "
            f"{old['mean_missed_move_pct']:.2f}% | "
            f"{new['mean_missed_move_pct']:.2f}% |"
        )
    lines.extend(
        [
            "",
            f"Median alpha: old {aggregate['old_median_alpha_pp']:+.2f}pp; "
            f"resting {aggregate['resting_median_alpha_pp']:+.2f}pp.",
            "",
            f"Mean reclaim overshoot: old "
            f"{aggregate['old_mean_reclaim_overshoot_pct']:.2f}%; resting "
            f"{aggregate['resting_mean_reclaim_overshoot_pct']:.2f}%.",
            "",
            f"Mean missed move: old {aggregate['old_mean_missed_move_pct']:.2f}%; "
            f"resting {aggregate['resting_mean_missed_move_pct']:.2f}%.",
            "",
            "Research only; no promotion, matrix, config, or live write.",
            "",
        ]
    )
    (out / "DECISION_REPORT.md").write_text("\n".join(lines))
    return out, payload


def _partial_runner_fold(
    symbol: str,
    side_name: str,
    fold: Fold,
    spec: dict[str, Any],
    fractions: tuple[float, ...],
    npz_dir: Path,
    cost_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    import vec_partial_regime_walkforward as partial

    side = 1 if side_name == "LONG" else -1
    data = base._load_execution(symbol, npz_dir, fold.start, "ladder", fold.end)
    htfs = {tf: base._compress_htf(data, tf) for tf in ("1h", "4h")}
    lower = base._lower_reentry_events(len(data.ts), htfs["1h"], side)
    candidate, signal_stats = _proposed_candidate(data, htfs, side, **spec)
    lib = partial._compile_scanner()
    n = len(data.ts)
    zeros = np.zeros(n, dtype=np.uint8)
    blank = np.full(n, np.nan, dtype=np.float64)
    regime = np.zeros(n, dtype=np.int8)
    cost_rt = 2.0 * cost_bps / 10_000.0
    slip = slippage_bps / 10_000.0
    bh = base._side_bh(data, side, cost_rt / 2.0, slip)
    rows = []
    for fraction in fractions:
        out = partial.PartialMetrics()
        rc = lib.vec_partial_regime_scan(
            n,
            side,
            12,
            0,
            data.ts,
            data.open,
            data.high,
            data.low,
            data.close,
            np.ascontiguousarray(candidate.atr_exec, dtype=np.float64),
            np.ascontiguousarray(candidate.exit_event, dtype=np.uint8),
            np.ascontiguousarray(candidate.struct_ref, dtype=np.float64),
            zeros,
            blank,
            2,
            zeros,
            blank,
            blank,
            regime,
            lower,
            fraction,
            0.0,
            1.0,
            1,
            float(spec["min_profit_gate"]),
            cost_rt,
            slip,
            partial.ctypes.byref(out),
        )
        if rc:
            data.z.close()
            raise RuntimeError(f"partial runner scanner returned {rc}")
        reentries = int(out.reentries)
        rows.append(
            {
                "symbol": symbol,
                "side": side_name,
                "fold": fold.label,
                "start": fold.start,
                "end_exclusive": fold.end,
                "exit_fraction": fraction,
                "runner_fraction": 1.0 - fraction,
                "gain_pct": 100.0 * (out.final_equity - 1.0),
                **bh,
                "alpha_vs_bh_pp": (
                    100.0 * (out.final_equity - 1.0) - bh["bh_net_side_pct"]
                ),
                "weighted_tim_rth_pct": (
                    100.0 * out.weighted_exposure_bars / n
                ),
                "binary_tim_rth_pct": 100.0 * out.binary_exposure_bars / n,
                "max_drawdown_pct": out.max_drawdown_pct,
                "technical_exits": int(out.technical_exit_count),
                "winning_exits": int(out.winning_exit_count),
                "losing_exits": int(out.losing_exit_count),
                "rejected_exit_signals": int(out.rejected_exit_signals),
                "reentries": reentries,
                "resting_reclaim_reentries": int(
                    out.resting_reclaim_reentries
                ),
                "lower_reentries": int(out.lower_reentries),
                "mean_saved_price_pct": (
                    out.saved_price_sum_pct / reentries if reentries else 0.0
                ),
                "mean_reclaim_overshoot_pct": (
                    out.reclaim_overshoot_sum_pct / out.reclaim_reentries
                    if out.reclaim_reentries
                    else 0.0
                ),
                "max_reclaim_overshoot_pct": out.reclaim_overshoot_max_pct,
                "mean_missed_move_pct": (
                    out.missed_move_sum_pct / reentries if reentries else 0.0
                ),
                "max_missed_move_pct": out.missed_move_max_pct,
                "turnover_one_way": out.turnover,
                "estimated_cost_equity": out.total_cost,
                "weighted_capacity_never_exceeds_one": bool(
                    out.weighted_exposure_bars <= n + 1e-9
                ),
                "contract_valid": bool(data.contract["valid"]),
                "quarantine_diagnostic": not bool(data.contract["valid"]),
                "signal_state_counts": signal_stats,
                "tier": "VEC_RESEARCH",
                "matrix_eligible": False,
            }
        )
    data.z.close()
    return rows


def _run_partial_runner(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    source = json.loads((args.grid_artifact / "digest.json").read_text())
    spec = source["selected"]["spec"]
    fractions = (0.25, 0.50, 0.75, 1.0)
    discovery = []
    for symbol in ("MU", "VT"):
        discovery.extend(
            _partial_runner_fold(
                symbol,
                "LONG",
                FOLDS[0],
                spec,
                fractions,
                args.npz_dir,
                args.cost_bps,
                args.slippage_bps,
            )
        )
    ranked = []
    for fraction in fractions:
        rows = [row for row in discovery if row["exit_fraction"] == fraction]
        alpha = np.array([row["alpha_vs_bh_pp"] for row in rows])
        ranked.append(
            {
                "exit_fraction": fraction,
                "runner_fraction": 1.0 - fraction,
                "min_alpha_pp": float(np.min(alpha)),
                "median_alpha_pp": float(np.median(alpha)),
                "positive_symbol_rate": float(np.mean(alpha > 0)),
                "mean_max_drawdown_pct": float(
                    np.mean([row["max_drawdown_pct"] for row in rows])
                ),
                "score": float(np.min(alpha) + 0.25 * np.median(alpha)),
                "discovery_rows": rows,
            }
        )
    ranked.sort(
        key=lambda row: (
            row["score"],
            row["positive_symbol_rate"],
            -row["mean_max_drawdown_pct"],
        ),
        reverse=True,
    )
    selected_fraction = ranked[0]["exit_fraction"]
    validation = []
    for symbol in ("MU", "VT"):
        for fold in FOLDS[1:]:
            validation.extend(
                _partial_runner_fold(
                    symbol,
                    "LONG",
                    fold,
                    spec,
                    (selected_fraction,),
                    args.npz_dir,
                    args.cost_bps,
                    args.slippage_bps,
                )
            )
    valid = [row for row in validation if not row["quarantine_diagnostic"]]
    aggregate = {
        "valid_folds": len(valid),
        "median_alpha_pp": float(
            np.median([row["alpha_vs_bh_pp"] for row in valid])
        ),
        "positive_alpha_fold_rate": float(
            np.mean([row["alpha_vs_bh_pp"] > 0 for row in valid])
        ),
        "mean_weighted_tim_rth_pct": float(
            np.mean([row["weighted_tim_rth_pct"] for row in valid])
        ),
        "mean_binary_tim_rth_pct": float(
            np.mean([row["binary_tim_rth_pct"] for row in valid])
        ),
        "mean_max_drawdown_pct": float(
            np.mean([row["max_drawdown_pct"] for row in valid])
        ),
        "mean_reclaim_overshoot_pct": float(
            np.mean([row["mean_reclaim_overshoot_pct"] for row in valid])
        ),
        "all_capacity_valid": all(
            row["weighted_capacity_never_exceeds_one"] for row in valid
        ),
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_root / f"partial_runner_reclaim_{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "promotion_eligible": False,
        "live_config_write": False,
        "source_tool": str(Path(__file__).resolve()),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "partial_scanner_sha256": hashlib.sha256(
            (Path(__file__).with_name("vec_partial_regime_scan.c")).read_bytes()
        ).hexdigest(),
        "frozen_exit_spec": spec,
        "selection": (
            "exit fraction selected universally on MU_LONG+VT_LONG "
            "FROZEN_2025Q4 only"
        ),
        "fractions": fractions,
        "ranked_discovery": ranked,
        "selected_exit_fraction": selected_fraction,
        "selected_runner_fraction": 1.0 - selected_fraction,
        "frozen_validation": validation,
        "aggregate": aggregate,
        "benchmark_status": {
            "passes_bh_on_all_frozen_folds": bool(
                all(row["alpha_vs_bh_pp"] > 0 for row in valid)
            ),
            "same_entry_same_window_ladder_control_run": False,
            "decision": "FAIL_CLOSED",
            "reason": (
                "This partial-runner probe starts with one full unit and does "
                "not replay the frozen grey-band ladder entry curve. It is an "
                "exit/reentry diagnostic, not evidence of improvement over "
                "the mandatory same-entry/same-window ladder control."
            ),
            "documented_mu_reference_only": {
                "window": "2026-01-01 through 2026-07-25",
                "strategy": "frozen ladder + E02_DONCHIAN 4h N30",
                "gain_pct": 1316.021,
                "bh_pct": 205.252,
                "multiple_vs_bh": 6.4117,
                "weighted_tim_pct": 77.092,
                "comparable_to_these_folds": False,
            },
        },
        "config": {
            "cost_bps_one_way": args.cost_bps,
            "slippage_bps_one_way": args.slippage_bps,
            "reentry": "E11_G1 then persistent resting E10",
            "capacity": "two clips sum to exactly one unlevered unit",
        },
    }
    (out / "digest.json").write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
            default=_json_default,
        )
        + "\n"
    )
    lines = [
        "# Frozen Partial Runner + Resting Reclaim",
        "",
        payload["selection"] + ". Exit signal and profit gate were not re-optimized.",
        "",
        f"Selected exit fraction: {selected_fraction:.0%}; retained runner: "
        f"{1.0-selected_fraction:.0%}.",
        "",
        "| Symbol | Fold | Gain | B&H | Alpha | Weighted TIM | Binary TIM | DD | Overshoot |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in validation:
        lines.append(
            f"| {row['symbol']}_{row['side']} | {row['fold']} | "
            f"{row['gain_pct']:+.2f}% | {row['bh_net_side_pct']:+.2f}% | "
            f"{row['alpha_vs_bh_pp']:+.2f}pp | "
            f"{row['weighted_tim_rth_pct']:.1f}% | "
            f"{row['binary_tim_rth_pct']:.1f}% | "
            f"{row['max_drawdown_pct']:.1f}% | "
            f"{row['mean_reclaim_overshoot_pct']:.2f}% |"
        )
    lines.extend(
        [
            "",
            f"Median alpha {aggregate['median_alpha_pp']:+.2f}pp; "
            f"{aggregate['positive_alpha_fold_rate']*100:.1f}% positive folds.",
            "",
            "## Benchmark decision",
            "",
            "**FAIL CLOSED.** VT remains below B&H on both frozen folds. "
            "More importantly, this probe initializes one full unit and does "
            "not replay the frozen grey-band ladder entry curve, so it has no "
            "same-entry/same-window ladder control and cannot demonstrate "
            "incremental alpha.",
            "",
            "For scale only, the documented MU 2026 frozen ladder + 4h N=30 "
            "E02 control made +1,316.02% versus +205.25% B&H (6.4117×) at "
            "77.09% weighted exposure. That is a different window, not a "
            "substitute for the missing fold-matched control.",
            "",
            "Research diagnostic only; no promotion, matrix, config, or live "
            "write. The next run must replay the identical frozen ladder "
            "entries for both candidate and control.",
            "",
        ]
    )
    (out / "DECISION_REPORT.md").write_text("\n".join(lines))
    return out, payload


def _write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines = [
        "# Structural + WT1 Lower-Rebound-Top Exit Experiment",
        "",
        "Research only. Signals use completed bars and next-RTH-open fills. "
        "E11 lower-price reentry is paired with a zero-buffer E10 reclaim "
        "obligation that remains latched until fill.",
        "",
        "| Symbol/side | Fold | Exit | Gain | B&H | Alpha | Exposure | Exits | Reentries | Losing exits | Data |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for fold in payload["fold_results"]:
        key = f"{fold['symbol']}_{fold['side']}"
        for row in fold["results"]:
            quality = "VALID" if not row["quarantine_diagnostic"] else "QUARANTINE"
            lines.append(
                f"| {key} | {fold['fold']['label']} | {row['family']} | "
                f"{row['gain_pct']:+.2f}% | {row['bh_net_side_pct']:+.2f}% | "
                f"{row['alpha_vs_bh_pp']:+.2f}pp | {row['tim_rth_pct']:.1f}% | "
                f"{row['technical_exits']} | {row['reentries']} | "
                f"{row['losing_exits']} | {quality} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation contract",
            "",
            "- `NO_EXIT_RUNNER` is the causal no-exit/B&H control.",
            "- `DC_LOW4_5M`/`DC_HIGH4_5M` is the immediate losing-stop comparator.",
            "- `STRUCTURAL_WT_LOWER_REBOUND_TOP` waits for a distinct rebound and "
            "then a price plus WT1 rollover confirmation.",
            "- HAO remains diagnostic-only because its NPZ contract is invalid; "
            "its numbers cannot support promotion.",
            "- No result is promotion- or matrix-eligible.",
            "",
        ]
    )
    path.write_text("\n".join(lines))


def _self_test() -> None:
    # Minimal synthetic completed bars: LONG damage at arm k=6, rebound, then
    # distinct lower price + WT rollover confirmation.
    source = np.arange(10, dtype=np.int64) * 14_400
    arm = base.HTFData(
        tf="4h",
        event_index=np.arange(10),
        source_ts=source,
        open=np.array([10, 11, 12, 13, 14, 15, 14, 13, 13, 12], dtype=float),
        high=np.array([11, 12, 13, 14, 15, 16, 14, 14, 14, 13], dtype=float),
        low=np.array([9, 10, 11, 12, 13, 14, 12, 11, 11, 10], dtype=float),
        close=np.array([10, 11, 12, 13, 14, 15, 12.5, 13, 12, 11], dtype=float),
        rsi=np.full(10, 50.0),
        atr=np.full(10, 1.0),
    )
    m = 40
    ts = np.arange(m, dtype=np.int64) * 3_600
    high = np.full(m, 12.0)
    low = np.full(m, 11.0)
    close = np.full(m, 11.5)
    open_ = np.full(m, 11.4)
    # Damage becomes available at trigger j=24, rebound top at 26, distinct
    # rollover confirmation at 27.
    low[24] = 10.0
    high[25], low[25], close[25] = 10.4, 10.1, 10.3
    high[26], low[26], close[26] = 11.2, 10.4, 11.0
    high[27], low[27], close[27] = 11.0, 10.2, 10.4
    trigger = base.HTFData(
        tf="1h",
        event_index=np.arange(m),
        source_ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        rsi=np.full(m, 50.0),
        atr=np.full(m, 0.5),
    )
    wt_arm = np.array([20, 25, 30, 35, 40, 45, 20, 25, 20, 10], dtype=float)
    wt_trigger = np.full(m, 10.0)
    wt_trigger[24:28] = [5.0, 12.0, 20.0, 15.0]
    event, _, stats = structural_wt_rebound_signal(
        arm, trigger, wt_arm, wt_trigger, 1
    )
    assert event[27] == 1, np.flatnonzero(event)
    assert event[26] == 0
    assert stats["confirmed_exits"] == 1

    # Resting reclaim fills an intrabar touch at the stored level; the old
    # close->next-open model suffers the following opening gap.
    o = np.array([100, 101, 98, 99, 110, 111], dtype=np.float64)
    synthetic = base.ExecutionData(
        symbol="TEST",
        path="synthetic",
        ts=np.arange(6, dtype=np.int64) * 300,
        open=o,
        high=np.array([101, 102, 99, 101, 111, 112], dtype=np.float64),
        low=np.array([99, 100, 97, 98, 109, 110], dtype=np.float64),
        close=np.array([100, 101, 98, 100.5, 110, 111], dtype=np.float64),
        synthetic=np.zeros(6, dtype=np.uint8),
        full_indices=np.arange(6),
        z=None,
        contract={"valid": True},
    )
    exit_event = np.zeros(6, dtype=np.uint8)
    exit_event[1] = 1
    reclaim_ref = np.full(6, np.nan)
    reclaim_ref[1] = 100.0
    candidate = base.ExitCandidate(
        family="TEST",
        label="TEST",
        params={},
        exit_mode=2,
        exit_event=exit_event,
        raw_stop=np.full(6, np.nan),
        struct_ref=reclaim_ref,
        atr_exec=np.ones(6),
    )
    lib = base._compile_scanner()
    bh = base._side_bh(synthetic, 1, 0.0, 0.0)
    lower = np.zeros(6, dtype=np.uint8)
    old = base._scan(
        lib,
        synthetic,
        candidate,
        lower,
        1,
        ("DELAYED", 2, 0.0, 1.0),
        0.0,
        0.0,
        bh,
    )
    resting = base._scan(
        lib,
        synthetic,
        candidate,
        lower,
        1,
        ("RESTING", 4, 0.0, 1.0),
        0.0,
        0.0,
        bh,
    )
    assert old["mean_reclaim_overshoot_pct"] == 10.0, old
    assert resting["mean_reclaim_overshoot_pct"] == 0.0, resting
    assert resting["resting_reclaim_reentries"] == 1, resting
    assert resting["gain_pct"] > old["gain_pct"], (old, resting)

    short_open = np.array([100, 99, 102, 101, 90, 89], dtype=np.float64)
    short_data = base.ExecutionData(
        symbol="TEST",
        path="synthetic",
        ts=np.arange(6, dtype=np.int64) * 300,
        open=short_open,
        high=np.array([101, 100, 103, 102, 91, 90], dtype=np.float64),
        low=np.array([99, 98, 101, 99, 89, 88], dtype=np.float64),
        close=np.array([100, 99, 102, 99.5, 90, 89], dtype=np.float64),
        synthetic=np.zeros(6, dtype=np.uint8),
        full_indices=np.arange(6),
        z=None,
        contract={"valid": True},
    )
    short_bh = base._side_bh(short_data, -1, 0.0, 0.0)
    short_old = base._scan(
        lib,
        short_data,
        candidate,
        lower,
        -1,
        ("DELAYED", 2, 0.0, 1.0),
        0.0,
        0.0,
        short_bh,
    )
    short_resting = base._scan(
        lib,
        short_data,
        candidate,
        lower,
        -1,
        ("RESTING", 4, 0.0, 1.0),
        0.0,
        0.0,
        short_bh,
    )
    assert short_old["mean_reclaim_overshoot_pct"] == 10.0, short_old
    assert short_resting["mean_reclaim_overshoot_pct"] == 0.0, short_resting
    assert short_resting["gain_pct"] > short_old["gain_pct"]
    print(
        json.dumps(
            {
                "status": "PASS",
                "causal_distinct_confirmation": True,
                "resting_touch_beats_delayed_gap": True,
                "short_mirror_resting_touch": True,
            }
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npz-dir", type=Path, default=base.DEFAULT_NPZ)
    parser.add_argument("--output-root", type=Path, default=base.DEFAULT_OUT)
    parser.add_argument("--cost-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--resting-compare", action="store_true")
    parser.add_argument("--partial-runner", action="store_true")
    parser.add_argument(
        "--baseline-artifact",
        type=Path,
        default=base.DEFAULT_OUT / "structural_wt_rebound_20260726T062654Z",
    )
    parser.add_argument(
        "--grid-artifact",
        type=Path,
        default=base.DEFAULT_OUT / "structural_wt_profit_grid_20260726T063646Z",
    )
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if args.grid:
        out, payload = _run_grid(args)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "artifact": str(out),
                    "selected": payload["selected"]["spec"],
                    **payload["valid_long_validation"],
                },
                default=_json_default,
            )
        )
        return 0
    if args.resting_compare:
        out, payload = _run_resting_compare(args)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "artifact": str(out),
                    **payload["aggregate"],
                },
                default=_json_default,
            )
        )
        return 0
    if args.partial_runner:
        out, payload = _run_partial_runner(args)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "artifact": str(out),
                    "selected_exit_fraction": payload["selected_exit_fraction"],
                    "selected_runner_fraction": payload["selected_runner_fraction"],
                    **payload["aggregate"],
                },
                default=_json_default,
            )
        )
        return 0
    lib = base._compile_scanner()
    fold_results = []
    for symbol, side in (("MU", "LONG"), ("VT", "LONG"), ("HAO", "SHORT")):
        for fold in FOLDS:
            if symbol == "HAO" and fold.label != "RECENT_GAP_CLEAN":
                continue
            fold_results.append(
                _run_fold(
                    symbol,
                    side,
                    fold,
                    args.npz_dir,
                    lib,
                    args.cost_bps,
                    args.slippage_bps,
                )
            )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_root / f"structural_wt_rebound_{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "promotion_eligible": False,
        "live_config_write": False,
        "side_isolation": True,
        "signals": "completed 4h arm + completed 1h rebound/confirmation",
        "fill": "next RTH open",
        "cost_bps_one_way": args.cost_bps,
        "slippage_bps_one_way": args.slippage_bps,
        "reentry": "E11_G1 + E10_RB0 mandatory latched reclaim",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fold_results": fold_results,
        "aggregate": _aggregate(fold_results),
    }
    (out / "digest.json").write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            allow_nan=False,
            default=_json_default,
        )
        + "\n"
    )
    with gzip.open(out / "results.jsonl.gz", "wt") as handle:
        for fold in fold_results:
            for row in fold["results"]:
                handle.write(
                    json.dumps(
                        {
                            "symbol": fold["symbol"],
                            "side": fold["side"],
                            "fold": fold["fold"],
                            **row,
                        },
                        sort_keys=True,
                        allow_nan=False,
                        default=_json_default,
                    )
                    + "\n"
                )
    _write_markdown(payload, out / "DECISION_REPORT.md")
    print(
        json.dumps(
            {"status": "PASS", "artifact": str(out), **payload["aggregate"]},
            default=_json_default,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
