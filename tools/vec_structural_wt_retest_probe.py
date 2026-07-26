#!/usr/bin/env python3
"""Contract-valid probe for the structural 4h arm → 1h price/WT retest exit.

The entry baseline is the latest frozen MU band-ladder curve.  The only exit is
``StructuralWtRetestExitBook`` from the exact-engine path.  Mandatory lower
ladder reentry plus zero-buffer reclaim is retained.  This is research-only and
never writes the switch matrix.
"""
from __future__ import annotations

import argparse
import dataclasses
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

from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from vec_paths.structural_wt_retest_exit import (  # noqa: E402
    CompletedBar,
    StructuralWtParams,
    StructuralWtRetestExitBook,
)
from reentry_contract import resting_reclaim_fill  # noqa: E402


def _events_by_row(
    htfs: dict[str, Any], params: StructuralWtParams
) -> dict[int, list[CompletedBar]]:
    out: dict[int, list[CompletedBar]] = {}
    for tf in (params.arm_tf, params.confirm_tf):
        h = htfs[tf]
        for slot, row in enumerate(h.event_index):
            out.setdefault(int(row), []).append(
                CompletedBar(
                    timeframe=tf,
                    source_ts=int(h.source_ts[slot]),
                    observed_ts=0,  # bound to the execution observation below
                    high=float(h.high[slot]),
                    low=float(h.low[slot]),
                    close=float(h.close[slot]),
                    wt1=math.nan,  # populated from the loaded NPZ below
                    atr=float(h.atr[slot]),
                )
            )
    # Stable arm-before-confirm ordering when both complete together.
    for rows in out.values():
        rows.sort(key=lambda bar: 0 if bar.timeframe == params.arm_tf else 1)
    return out


def simulate(
    data: Any,
    entry_signals: ladder.SignalData,
    curve: ladder.Curve,
    htfs: dict[str, Any],
    params: StructuralWtParams,
    *,
    commission_rate: float,
    slippage_rate: float,
) -> dict[str, Any]:
    book = StructuralWtRetestExitBook(params)
    events = _events_by_row(htfs, params)
    # WT1 is taken from the exact completed source row used by the campaign.
    wt1_at: dict[tuple[str, int], float] = {}
    for tf in (params.arm_tf, params.confirm_tf):
        h = htfs[tf]
        full = data.full_indices[h.event_index]
        values = np.asarray(data.z[f"wt1_{tf}"], dtype=np.float64)[full]
        for row, value in zip(h.event_index, values):
            wt1_at[(tf, int(row))] = float(value)

    cash = ladder.ACCOUNT_EQUITY
    qty = 0.0
    pending = None
    last_exit_fill = math.nan
    reclaim_level = math.nan
    prior_exit_notional = 0.0
    gap_seen = False
    held = 0
    weighted = 0.0
    peak_equity = ladder.ACCOUNT_EQUITY
    max_dd = 0.0
    requested = filled = 0.0
    clamps = entry_fills = exit_fills = lower = reclaim = 0
    signals = 0
    exit_row = -1

    for i in range(len(data.ts)):
        op = float(data.open[i])
        close = float(data.close[i])
        if pending is not None:
            if int(pending["signal_index"]) + 1 != i:
                raise RuntimeError("candidate did not fill on exact next RTH row")
            if pending["kind"] == "exit" and qty > 0:
                px = op * (1.0 - slippage_rate)
                notional = qty * px
                cash += notional - commission_rate * notional
                prior_exit_notional = min(ladder.CAPACITY, notional)
                last_exit_fill = px
                reclaim_level = max(px, float(pending["retest_price"]))
                qty = 0.0
                gap_seen = False
                exit_row = i
                exit_fills += 1
            elif pending["kind"] == "entry":
                px = op * (1.0 + slippage_rate)
                current = qty * px
                target = float(pending["requested_notional"])
                want = (
                    max(0.0, target - current)
                    if pending["absolute_target"]
                    else target
                )
                actual = min(want, max(0.0, ladder.CAPACITY - current))
                requested += want
                filled += actual
                clamps += int(actual + 1e-9 < want)
                if actual > 0:
                    cash -= actual + commission_rate * actual
                    qty += actual / px
                    entry_fills += 1
                    lower += int(pending["reason"] == "ladder_lower")
                    reclaim += int(pending["reason"] == "reclaim")
                    if qty * px > ladder.CAPACITY + 1e-6:
                        raise RuntimeError("entry capacity breach")
            pending = None

        # E10 is a persistent resting reclaim obligation.  It must recognize
        # the first OHLC touch and fill at the stored level; a gap through the
        # stop fills at the adverse open.  It is never close-cross/next-open.
        if (
            qty <= 0
            and i > exit_row >= 0
            and math.isfinite(reclaim_level)
        ):
            px = resting_reclaim_fill(
                is_long=True,
                reclaim_level=reclaim_level,
                bar_open=op,
                bar_high=float(data.high[i]),
                bar_low=float(data.low[i]),
                slippage_bps=slippage_rate * 10_000.0,
            )
            if px is not None:
                target = max(ladder.BASE_UNIT, prior_exit_notional)
                actual = min(target, ladder.CAPACITY)
                requested += target
                filled += actual
                clamps += int(actual + 1e-9 < target)
                cash -= actual + commission_rate * actual
                qty = actual / px
                entry_fills += 1
                reclaim += 1
                reclaim_level = math.nan
                last_exit_fill = math.nan

        equity = cash + qty * close
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, 100.0 * (peak_equity - equity) / peak_equity)
        held += int(qty > 0)
        weighted += min(ladder.CAPACITY, qty * close) / ladder.CAPACITY

        candidate = None
        for template in events.get(i, ()):
            bar = dataclasses.replace(
                template,
                observed_ts=int(data.ts[i]),
                wt1=wt1_at[(template.timeframe, i)],
            )
            if not all(
                math.isfinite(value)
                for value in (
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.wt1,
                    bar.atr,
                )
            ) or bar.atr <= 0:
                continue
            candidate = (
                book.update(
                    symbol=data.symbol,
                    position_side="LONG",
                    active=qty > 0,
                    bar=bar,
                )
                or candidate
            )
        if i + 1 >= len(data.ts):
            continue
        if candidate is not None and qty > 0:
            pending = {
                "kind": "exit",
                "signal_index": i,
                "retest_price": candidate.retest_price,
            }
            signals += 1
            continue
        if qty > 0:
            if entry_signals.entry_mult[i] > 0:
                pending = {
                    "kind": "entry",
                    "signal_index": i,
                    "requested_notional": (
                        ladder.BASE_UNIT * float(entry_signals.entry_mult[i])
                    ),
                    "absolute_target": curve.semantics == "target",
                    "reason": "ladder_add",
                }
        elif math.isfinite(last_exit_fill):
            gap_seen |= float(data.low[i]) < last_exit_fill
            if entry_signals.entry_mult[i] > 0 and gap_seen:
                pending = {
                    "kind": "entry",
                    "signal_index": i,
                    "requested_notional": (
                        ladder.BASE_UNIT * float(entry_signals.entry_mult[i])
                    ),
                    "absolute_target": curve.semantics == "target",
                    "reason": "ladder_lower",
                }
        elif entry_signals.entry_mult[i] > 0:
            pending = {
                "kind": "entry",
                "signal_index": i,
                "requested_notional": (
                    ladder.BASE_UNIT * float(entry_signals.entry_mult[i])
                ),
                "absolute_target": curve.semantics == "target",
                "reason": "initial_ladder",
            }

    if qty > 0:
        px = float(data.close[-1]) * (1.0 - slippage_rate)
        notional = qty * px
        cash += notional - commission_rate * notional
    pnl = cash - ladder.ACCOUNT_EQUITY
    bh_entry = float(data.open[0]) * (1.0 + slippage_rate)
    bh_exit = float(data.close[-1]) * (1.0 - slippage_rate)
    bh_pnl = (
        ladder.BASE_UNIT * (bh_exit / bh_entry - 1.0)
        - 2.0 * commission_rate * ladder.BASE_UNIT
    )
    return {
        "capital_return_pct": 100.0 * pnl / ladder.BASE_UNIT,
        "bh_capital_return_pct": 100.0 * bh_pnl / ladder.BASE_UNIT,
        "strategy_bh_multiple": pnl / bh_pnl if bh_pnl else None,
        "alpha_vs_bh_pp": 100.0 * (pnl - bh_pnl) / ladder.BASE_UNIT,
        "binary_tim_pct": 100.0 * held / len(data.ts),
        "exposure_weighted_tim_pct": 100.0 * weighted / len(data.ts),
        "max_drawdown_account_pct": max_dd,
        "signals": signals,
        "exit_fills": exit_fills,
        "entry_fills": entry_fills,
        "lower_reentries": lower,
        "reclaim_reentries": reclaim,
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "clamp_count": clamps,
        "future_htf_source_count": sum(
            int(bar.source_ts > int(data.ts[row]))
            for row, bars in events.items()
            for bar in bars
        ),
        "rows": len(data.ts),
        "start_ts": int(data.ts[0]),
        "end_ts": int(data.ts[-1]),
        "mandatory_reclaim_execution": (
            "RESTING_TOUCH_LEVEL_OR_ADVERSE_GAP_OPEN"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    source = json.loads((args.artifact / "result.json").read_text())
    frozen = source["outer_folds"][-1]
    start, end = frozen["validation"]
    symbol = source["manifest"]["symbol"]
    data = ladder.top._load_execution(
        symbol, args.npz_dir, start, "ladder", end
    )
    if not data.contract["valid"]:
        raise RuntimeError(data.contract["errors"])
    htfs = {
        tf: ladder.top._compress_htf(data, tf)
        for tf in ("1h", "4h", "D")
    }
    curve = ladder.Curve(**frozen["selected_curve"])
    entry = ladder._build_signals(
        data, htfs, curve, int(source["manifest"]["exit"]["n"])
    )
    grid = []
    for lookback in (4, 6, 10):
        for rebound in (0.25, 0.5, 1.0):
            for wait in (12, 20, 30):
                params = StructuralWtParams(
                    rebound_atr=rebound,
                    prebreak_lookback=lookback,
                    max_wait_1h=wait,
                )
                metrics = simulate(
                    data,
                    entry,
                    curve,
                    htfs,
                    params,
                    commission_rate=(
                        float(source["manifest"]["commission_bps_one_way"])
                        / 10_000.0
                    ),
                    slippage_rate=(
                        float(source["manifest"]["slippage_bps_one_way"])
                        / 10_000.0
                    ),
                )
                grid.append(
                    {"params": dataclasses.asdict(params), "metrics": metrics}
                )
    grid.sort(
        key=lambda row: (
            -float(row["metrics"]["strategy_bh_multiple"] or -1e9),
            abs(float(row["metrics"]["exposure_weighted_tim_pct"]) - 75.0),
        )
    )
    payload = {
        "tier": "VEC_RESEARCH",
        "matrix_written": False,
        "promotion_allowed": False,
        "symbol": symbol,
        "side": "LONG",
        "source_artifact": str(args.artifact.resolve()),
        "npz_sha256": source["manifest"]["npz_sha256"],
        "entry_curve": frozen["selected_curve"],
        "arm_tf": "4h",
        "confirm_tf": "1h",
        "mandatory_reclaim_execution": (
            "RESTING_TOUCH_LEVEL_OR_ADVERSE_GAP_OPEN"
        ),
        "candidate_count": len(grid),
        "results": grid,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps({"output": str(args.out_dir), "best": grid[0]}, sort_keys=True))
    data.z.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
