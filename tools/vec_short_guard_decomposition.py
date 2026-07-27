#!/usr/bin/env python3
"""Preregistered, causal decomposition of stock SHORT entry guards.

The production guard is not changed.  This research asks which bullish-state
vetoes conflict with a qualified ``TOP_REJECTION_SHORT`` while proving that
causality, capacity, emergency cover and solvency protections remain invariant.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
from tools import vec_asymmetric_short_campaign as base  # noqa: E402
from tools import vec_top_exit_campaign as top  # noqa: E402


STAGE = "VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS"
CONFLICT_KEYS = (
    "DAY_GAIN",
    "RSI_15M",
    "RSI_1H",
    "BULL_D_CANDLE",
    "BULL_4H_CANDLE",
    "NO_BEAR_HTF_CONFIRM",
    "BULL_D_WT",
)
EMERGENCY_KEYS = (
    "DATA_CONTRACT_VALID",
    "COMPLETED_SOURCE_CAUSAL",
    "CAPACITY_WITHIN_16000",
    "NEXT_AVAILABILITY_FILL",
    "ATR_EMERGENCY_COVER_ENABLED",
    "SOLVENCY_GATE_ENABLED",
)


@dataclasses.dataclass(frozen=True)
class GuardProfile:
    label: str
    bypass: tuple[str, ...]


# Frozen before any campaign result was observed.  Single-component ablations
# identify wiring conflicts; the two coherent blocks and full path-scoped
# bypass show interactions without an unbounded 2^7 search.
PREREGISTERED_PROFILES = (
    GuardProfile("G00_ALL_VETOES", ()),
    *(GuardProfile(f"G1{i}_{key}", (key,)) for i, key in enumerate(CONFLICT_KEYS, 1)),
    GuardProfile("G20_OVERBOUGHT_BLOCK", ("DAY_GAIN", "RSI_15M", "RSI_1H")),
    GuardProfile(
        "G21_BULL_STATE_BLOCK",
        ("BULL_D_CANDLE", "BULL_4H_CANDLE", "NO_BEAR_HTF_CONFIRM", "BULL_D_WT"),
    ),
    GuardProfile("G99_TOP_REJECTION_PATH_SCOPED", CONFLICT_KEYS),
)


def guard_decision(
    emergencies: dict[str, bool],
    conflicts: dict[str, bool],
    profile: GuardProfile,
    *,
    qualified_top: bool,
    causal_rollover: bool,
) -> tuple[bool, tuple[str, ...]]:
    """Pure fail-closed decision used by the scanner and invariant tests.

    ``conflicts[key]`` means that production veto would fire.  A bypass applies
    only to a qualified TOP rejection with a completed structural rollover.
    Emergency protections are never bypassable.
    """
    failed_emergency = tuple(
        key for key in EMERGENCY_KEYS if not bool(emergencies.get(key, False))
    )
    if failed_emergency:
        return False, tuple(f"EMERGENCY:{x}" for x in failed_emergency)
    if not qualified_top:
        return False, ("NOT_QUALIFIED_TOP",)
    if not causal_rollover:
        return False, ("NO_CAUSAL_ROLLOVER",)
    bypass = set(profile.bypass)
    blocked = tuple(
        key for key in CONFLICT_KEYS
        if bool(conflicts.get(key, False)) and key not in bypass
    )
    return (not blocked), blocked


def _all_emergencies() -> dict[str, bool]:
    return {key: True for key in EMERGENCY_KEYS}


def _day_open(data: top.ExecutionData) -> np.ndarray:
    days = np.asarray(data.ts, dtype=np.int64) // 86400
    out = np.empty(len(data.ts), dtype=np.float64)
    start = 0
    while start < len(days):
        right = start + 1
        while right < len(days) and days[right] == days[start]:
            right += 1
        out[start:right] = float(data.open[start])
        start = right
    return out


def _events(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    candidate: base.Candidate,
    profile: GuardProfile,
) -> dict[str, Any]:
    n = len(data.ts)
    h15, h1, h4, day = (htfs[x] for x in ("15m", "1h", "4h", "D"))
    aligned = lambda h, field: base._aligned(data, h, field)
    k1 = aligned(h1, "stoch_k_1h")
    k4 = aligned(h4, "stoch_k_4h")
    rsi15 = aligned(h15, "rsi_15m")
    rsi1 = aligned(h1, "rsi_1h")
    pb4 = aligned(h4, "lrL_pct_b_4h")
    pbd = aligned(day, "lrL_pct_b_D")
    w11, w21 = aligned(h1, "wt1_1h"), aligned(h1, "wt2_1h")
    w14, w24 = aligned(h4, "wt1_4h"), aligned(h4, "wt2_4h")
    w1d, w2d = aligned(day, "wt1_D"), aligned(day, "wt2_D")
    o4, c4 = aligned(h4, "open_4h"), aligned(h4, "close_4h")
    od, cd = aligned(day, "open_D"), aligned(day, "close_D")
    sma15 = aligned(h15, "sma_200_15m")
    atr1 = top._align_feature(n, h1, h1.atr)
    h1_hi = top._align_feature(n, h1, h1.high)
    h1_lo = top._align_feature(n, h1, h1.low)
    h1_cl = top._align_feature(n, h1, h1.close)
    day_open = _day_open(data)

    event = np.zeros(n, dtype=np.uint8)
    armed = False
    armed_until = -1
    top_context = False
    block_counts = {key: 0 for key in CONFLICT_KEYS}
    other_blocks = {"NOT_QUALIFIED_TOP": 0, "NO_CAUSAL_ROLLOVER": 0}
    qualified = rollovers = allowed = future = 0
    for j, i0 in enumerate(h1.event_index):
        i = int(i0)
        if j < 2:
            continue
        future += int(h1.source_ts[j] > data.ts[i])
        overbought = (
            math.isfinite(k4[i])
            and k4[i] >= candidate.overbought_k
            and (
                (math.isfinite(pb4[i]) and pb4[i] >= candidate.overbought_pb)
                or (math.isfinite(pbd[i]) and pbd[i] >= candidate.overbought_pb)
            )
        )
        if candidate.book == "CORRECTION":
            context = (w1d[i] >= w2d[i]) or (w14[i] >= w24[i])
        else:
            context = (w1d[i] < w2d[i]) and (w14[i] < w24[i])
        if overbought and context:
            armed = True
            top_context = True
            armed_until = int(data.ts[i] + candidate.arm_hours * 3600)
        if armed and data.ts[i] > armed_until:
            armed = False
            top_context = False
        # Stronger than the first campaign: an actual completed 1h LH+LL whose
        # close breaks the prior low, plus bearish WT.  A cross alone cannot
        # enter.
        rollover = bool(
            armed
            and w11[i] < w21[i]
            and h1.high[j] < h1.high[j - 1]
            and h1.low[j] < h1.low[j - 1]
            and h1.close[j] < h1.low[j - 1]
        )
        if not armed:
            continue
        qualified += int(top_context)
        rollovers += int(rollover)
        price = float(data.close[i])
        day_gain = (
            (price / day_open[i] - 1.0) * 100.0 if day_open[i] > 0 else 0.0
        )
        below_sma = math.isfinite(sma15[i]) and price < sma15[i]
        bear_d = cd[i] < od[i]
        bear_4h = c4[i] < o4[i]
        conflicts = {
            "DAY_GAIN": day_gain >= 2.5,
            "RSI_15M": math.isfinite(rsi15[i]) and rsi15[i] >= 65.0,
            "RSI_1H": math.isfinite(rsi1[i]) and rsi1[i] >= 65.0,
            "BULL_D_CANDLE": bool(cd[i] > od[i] and not below_sma),
            "BULL_4H_CANDLE": bool(c4[i] > o4[i] and not below_sma),
            "NO_BEAR_HTF_CONFIRM": bool(not (bear_d or bear_4h or below_sma)),
            "BULL_D_WT": bool(w1d[i] > w2d[i] and not below_sma),
        }
        ok, reasons = guard_decision(
            _all_emergencies(), conflicts, profile,
            qualified_top=top_context, causal_rollover=rollover,
        )
        if ok:
            event[i] = 1
            allowed += 1
            armed = False
            top_context = False
        elif rollover:
            for reason in reasons:
                if reason in block_counts:
                    block_counts[reason] += 1
                elif reason in other_blocks:
                    other_blocks[reason] += 1
    return {
        "entry": event,
        "k1": k1,
        "w11": w11,
        "w21": w21,
        "atr1": atr1,
        "h1_hi": h1_hi,
        "h1_lo": h1_lo,
        "h1_cl": h1_cl,
        "source_future_count": future,
        "guard_audit": {
            "profile": profile.label,
            "bypassed_conflicts": list(profile.bypass),
            "qualified_top_observations": qualified,
            "causal_rollovers": rollovers,
            "allowed_entries": allowed,
            "blocked_rollovers_by_component": block_counts,
            "other_blocks": other_blocks,
            "emergency_invariants": _all_emergencies(),
        },
    }


def _select(discovery: list[dict[str, Any]]) -> tuple[bool, float]:
    passed = all(
        not r["insolvent"]
        and r["max_drawdown_account_pct"] < 100.0
        and r["capital_return_pct"] > 0.0
        and r["technical_exits"] >= 2
        and r["beats_opportunity_benchmark"]
        for r in discovery
    )
    score = (
        float(np.median([r["capital_return_pct"] for r in discovery]))
        - 0.4 * max(r["max_drawdown_account_pct"] for r in discovery)
        + 0.05 * float(np.median([r["correction_capture_pct"] for r in discovery]))
    )
    return passed, score


def run(args: argparse.Namespace) -> Path:
    cohorts = {
        "CORRECTION": tuple(x for x in args.correction_symbols.upper().split(",") if x),
        "BEAR": tuple(x for x in args.bear_symbols.upper().split(",") if x),
    }
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for book, symbols in cohorts.items():
        for symbol in symbols:
            try:
                data = top._load_execution(
                    symbol, Path(args.npz_dir), args.start, "ladder", args.end
                )
                if not data.contract["valid"]:
                    raise RuntimeError(str(data.contract["errors"]))
                htfs = {
                    tf: top._compress_htf(data, tf)
                    for tf in ("15m", "1h", "4h", "D")
                }
                windows = base._windows(data)
                if len(windows) < 3:
                    raise RuntimeError(f"requires 3 folds, got {len(windows)}")
                profile_rows = []
                for profile in PREREGISTERED_PROFILES:
                    tested = []
                    for candidate in base.candidates(book):
                        ev = _events(data, htfs, candidate, profile)
                        folds = {
                            name: base._simulate(
                                data, ev, candidate, left, right,
                                args.commission_bps, args.slippage_bps,
                            )
                            for name, left, right in windows
                        }
                        passed, score = _select([folds["D1"], folds["D2"]])
                        tested.append((passed, score, candidate, folds, ev["guard_audit"], ev["source_future_count"]))
                    tested.sort(key=lambda x: (not x[0], -x[1], x[2].label))
                    passed, score, candidate, folds, audit, future = tested[0]
                    final = folds["FINAL"]
                    survivor = bool(
                        passed
                        and future == 0
                        and not final["insolvent"]
                        and final["technical_exits"] >= 2
                        and final["beats_opportunity_benchmark"]
                    )
                    profile_rows.append({
                        "profile": dataclasses.asdict(profile),
                        "selected_candidate": dataclasses.asdict(candidate),
                        "selected_label": candidate.label,
                        "selected_on_discovery_only": True,
                        "discovery_gate_pass": passed,
                        "selection_score": score,
                        "guard_audit": audit,
                        "source_future_count": future,
                        "folds": folds,
                        "untouched_final": final,
                        "all_fold_survivor": survivor,
                        # Final shortlist status is assigned only after all
                        # profiles exist.  At most one discovery-ranked profile
                        # per symbol may proceed to exact replay; redundant
                        # all-fold passes remain gray.
                        "status": "ALL_FOLD_PASS_UNRANKED" if survivor else "GRAY_REJECTED",
                    })
                all_fold_rows = [r for r in profile_rows if r["all_fold_survivor"]]
                exact_shortlist = []
                if all_fold_rows:
                    chosen = sorted(
                        all_fold_rows,
                        key=lambda r: (-r["selection_score"], r["profile"]["label"]),
                    )[0]
                    chosen["status"] = "VECTOR_SURVIVOR_EXACT_PENDING"
                    exact_shortlist = [chosen["profile"]["label"]]
                    for row in all_fold_rows:
                        if row is not chosen:
                            row["status"] = "GRAY_REDUNDANT_ALL_FOLD_PASS"
                results.append({
                    "symbol": symbol,
                    "side": "SHORT",
                    "book": book,
                    "profiles": profile_rows,
                    "all_fold_profiles": [
                        r["profile"]["label"] for r in all_fold_rows
                    ],
                    "survivor_profiles": exact_shortlist,
                })
                data.z.close()
            except Exception as exc:
                errors.append({"symbol": symbol, "book": book, "error": str(exc)})

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir) / f"short_guard_decomposition_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tier": "VEC_RESEARCH",
        "stage": STAGE,
        "matrix_eligible": False,
        "promotion_allowed": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "preregistered_profiles": [dataclasses.asdict(x) for x in PREREGISTERED_PROFILES],
        "conflicting_vetoes": list(CONFLICT_KEYS),
        "emergency_invariants": list(EMERGENCY_KEYS),
        "entry_contract": "completed 1h LH+LL close below prior low AND bearish WT",
        "fill_contract": "first strictly later availability batch open",
        "fold_contract": {
            "D1": ["2024-03-26", "2025-07-01"],
            "D2": ["2025-07-01", base.FINAL_START],
            "FINAL_UNTOUCHED": [base.FINAL_START, base.FINAL_END],
        },
        "benchmark_contract": (
            "raw strategy, realized cash, open MTM, cash=0, fixed-notional "
            "short B&H, long B&H opportunity, DD, TIM, correction capture"
        ),
        "account_usd": base.ACCOUNT_USD,
        "base_usd": base.BASE_USD,
        "capacity_usd": base.CAPACITY_USD,
        "costs": {
            "commission_bps_one_way": args.commission_bps,
            "slippage_bps_one_way": args.slippage_bps,
        },
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    payload = {"manifest": manifest, "results": results, "errors": errors}
    (out / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    lines = [
        "# SHORT guard decomposition",
        "",
        "Research-only. Every row was selected on D1+D2 before FINAL was read.",
        "",
        "| book | key | profile | entries | strategy | short B&H | long B&H | DD | TIM | exits | emergency covers | verdict |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in results:
        for row in item["profiles"]:
            f = row["untouched_final"]
            lines.append(
                f"| {item['book']} | {item['symbol']}_SHORT | {row['profile']['label']} | "
                f"{row['guard_audit']['allowed_entries']} | {f['strategy_return_pct']:+.2f}% | "
                f"{f['short_bh_return_pct']:+.2f}% | {f['long_bh_opportunity_return_pct']:+.2f}% | "
                f"{f['max_drawdown_account_pct']:.2f}% | {f['time_in_market_pct']:.2f}% | "
                f"{f['technical_exits']} | {f['emergency_exit_count']} | {row['status']} |"
            )
    if errors:
        lines += ["", "## Data-contract errors", ""] + [
            f"- {x['book']} {x['symbol']}: {x['error']}" for x in errors
        ]
    (out / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({
        "artifact": str(out),
        "symbols": len(results),
        "profiles": sum(len(x["profiles"]) for x in results),
        "survivors": sum(len(x["survivor_profiles"]) for x in results),
        "errors": len(errors),
    }, sort_keys=True))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-dir", default=str(top.DEFAULT_NPZ))
    ap.add_argument("--out-dir", default=str(ROOT / "data/reports/vec_research"))
    ap.add_argument("--start", default="2024-03-26")
    ap.add_argument("--end")
    ap.add_argument("--correction-symbols", default=",".join(base.CORRECTION_DEFAULT))
    ap.add_argument("--bear-symbols", default=",".join(base.BEAR_DEFAULT))
    ap.add_argument("--commission-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
