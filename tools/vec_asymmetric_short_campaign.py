#!/usr/bin/env python3
"""Causal vector-first research for asymmetric stock SHORTs.

This campaign deliberately does not mirror the LONG ladder.  It studies two
different books:

* ``CORRECTION``: an overbought LONG-universe name is armed patiently, entered
  only after a completed-1h rollover, scaled only after downside confirmation,
  and covered quickly when the correction exhausts.
* ``BEAR``: a crypto-related/bear-regime equity is shorted after a rally fails
  inside a completed-D/4h downtrend and may be held longer.

Discovery folds select one bounded candidate per symbol.  The final fold is
read only after that selection is frozen.  Results are research-only and
cannot mutate live configuration or claim exact-engine evidence.
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
from tools import vec_top_exit_campaign as top  # noqa: E402
from research_availability_clock import (  # noqa: E402
    next_strictly_later_index,
)


ACCOUNT_USD = 10_000.0
BASE_USD = 2_000.0
CAPACITY_USD = 16_000.0
CORRECTION_DEFAULT = ("NVDA", "MU", "SNDK", "ARM", "PLTR", "MRVL", "LRCX")
BEAR_DEFAULT = ("MSTR", "IBIT", "COIN")
FINAL_START = "2026-01-01"
FINAL_END = "2026-07-25"


@dataclasses.dataclass(frozen=True)
class Candidate:
    book: str
    overbought_k: int
    overbought_pb: float
    arm_hours: int
    entry_mult: float
    scale_trigger_atr: float
    max_mult: float
    profit_atr: float
    trail_fraction: float
    cover_k: int
    emergency_atr: float
    max_hold_hours: int

    @property
    def label(self) -> str:
        return (
            f"{self.book}_K{self.overbought_k}_PB{self.overbought_pb:g}"
            f"_A{self.arm_hours}_I{self.entry_mult:g}"
            f"_S{self.scale_trigger_atr:g}x{self.max_mult:g}"
            f"_P{self.profit_atr:g}_T{self.trail_fraction:g}"
            f"_C{self.cover_k}_E{self.emergency_atr:g}"
            f"_H{self.max_hold_hours}"
        )


def candidates(book: str) -> list[Candidate]:
    """A bounded block grid; fields move in coherent profiles, not OFAT."""
    book = book.upper()
    if book == "CORRECTION":
        profiles = [
            # calm arm, modest first fill, rapid downside capture
            (70, 0.70, 8, 1, 0.50, 4, 2.0, 0.35, 25, 2.5, 32),
            (75, 0.75, 12, 1, 0.50, 4, 2.5, 0.35, 20, 3.0, 48),
            (80, 0.80, 12, 2, 0.75, 6, 2.5, 0.45, 20, 3.0, 48),
            (80, 0.85, 18, 1, 0.75, 4, 3.0, 0.45, 15, 3.5, 64),
            (85, 0.85, 18, 2, 1.00, 6, 3.0, 0.55, 15, 3.5, 64),
            (75, 0.80, 24, 1, 1.00, 4, 3.5, 0.55, 20, 4.0, 80),
        ]
    elif book == "BEAR":
        profiles = [
            # rally failure in a downtrend; looser cover and longer hold
            (55, 0.55, 12, 1, 0.50, 4, 2.5, 0.40, 25, 3.0, 64),
            (60, 0.60, 18, 1, 0.75, 4, 3.0, 0.45, 20, 3.5, 96),
            (65, 0.65, 18, 2, 0.75, 6, 3.5, 0.50, 20, 4.0, 120),
            (70, 0.70, 24, 2, 1.00, 6, 4.0, 0.55, 15, 4.5, 160),
        ]
    else:
        raise ValueError(book)
    return [Candidate(book, *p) for p in profiles]


def _aligned(data: top.ExecutionData, h: top.HTFData, field: str) -> np.ndarray:
    full = data.full_indices[h.event_index]
    if field not in data.z.files:
        return np.full(len(data.ts), np.nan, dtype=np.float64)
    values = np.asarray(data.z[field], dtype=np.float64)[full]
    return top._align_feature(len(data.ts), h, values)


def _events(data: top.ExecutionData, htfs: dict[str, top.HTFData], c: Candidate):
    n = len(data.ts)
    h1, h4, day = htfs["1h"], htfs["4h"], htfs["D"]
    k1 = _aligned(data, h1, "stoch_k_1h")
    k4 = _aligned(data, h4, "stoch_k_4h")
    pb4 = _aligned(data, h4, "lrL_pct_b_4h")
    pbd = _aligned(data, day, "lrL_pct_b_D")
    w11, w21 = _aligned(data, h1, "wt1_1h"), _aligned(data, h1, "wt2_1h")
    w14, w24 = _aligned(data, h4, "wt1_4h"), _aligned(data, h4, "wt2_4h")
    w1d, w2d = _aligned(data, day, "wt1_D"), _aligned(data, day, "wt2_D")
    atr1 = top._align_feature(n, h1, h1.atr)
    h1_hi = top._align_feature(n, h1, h1.high)
    h1_lo = top._align_feature(n, h1, h1.low)
    h1_cl = top._align_feature(n, h1, h1.close)

    event = np.zeros(n, dtype=np.uint8)
    source_future = 0
    armed = False
    armed_until = -1
    prior_wt_bear = False
    slots = h1.event_index
    for j, i in enumerate(slots):
        i = int(i)
        if j < 2:
            continue
        # All h1/h4/D values came from completed bars.  Retain an explicit
        # source audit rather than trusting the aligned indicator values.
        source_future += int(h1.source_ts[j] > data.ts[i])
        overbought = (
            math.isfinite(k4[i])
            and k4[i] >= c.overbought_k
            and (
                (math.isfinite(pb4[i]) and pb4[i] >= c.overbought_pb)
                or (math.isfinite(pbd[i]) and pbd[i] >= c.overbought_pb)
            )
        )
        if c.book == "CORRECTION":
            # Bullish D is allowed and expected.  We are waiting for its
            # short-lived correction, not pretending the secular trend is bear.
            context = (w1d[i] >= w2d[i]) or (w14[i] >= w24[i])
        else:
            context = (w1d[i] < w2d[i]) and (w14[i] < w24[i])
        if overbought and context:
            armed = True
            armed_until = int(data.ts[i] + c.arm_hours * 3600)
        wt_bear = w11[i] < w21[i]
        fresh_wt_bear = wt_bear and not prior_wt_bear
        lh_ll = (
            h1.high[j] < h1.high[j - 1]
            and h1.low[j] < h1.low[j - 1]
            and h1.close[j] < h1.low[j - 1]
        )
        # Patient veto: overbought alone never shorts.  A completed-1h
        # rollover plus price confirmation is mandatory.
        confirmed = wt_bear and (fresh_wt_bear or lh_ll) and (
            h1.close[j] < h1.close[j - 1]
        )
        if armed and data.ts[i] <= armed_until and confirmed:
            event[i] = 1
            armed = False
        elif armed and data.ts[i] > armed_until:
            armed = False
        prior_wt_bear = wt_bear
    return {
        "entry": event,
        "k1": k1,
        "w11": w11,
        "w21": w21,
        "atr1": atr1,
        "h1_hi": h1_hi,
        "h1_lo": h1_lo,
        "h1_cl": h1_cl,
        "source_future_count": source_future,
    }


def _downside_opportunity(close: np.ndarray) -> float:
    if len(close) < 2:
        return 0.0
    ret = close[1:] / close[:-1] - 1.0
    return float(-np.minimum(ret, 0.0).sum() * 100.0)


def _simulate(
    data: top.ExecutionData,
    ev: dict[str, Any],
    c: Candidate,
    left: int,
    right: int,
    commission_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    commission = commission_bps / 10_000.0
    slip = slippage_bps / 10_000.0
    cash = ACCOUNT_USD
    qty = 0.0  # negative while short
    avg_entry = 0.0
    entry_atr = math.nan
    entry_i = -1
    next_scale = math.nan
    peak_gain = 0.0
    open_entry_fees = 0.0
    pending: tuple[str, int, str] | None = None
    realized = 0.0
    peak_eq = ACCOUNT_USD
    min_eq = ACCOUNT_USD
    max_dd = 0.0
    held = 0
    entries = scales = exits = wins = 0
    correction_pnl = 0.0
    trade_returns: list[float] = []

    def equity(px: float) -> float:
        return cash + qty * px

    def notional(px: float) -> float:
        return abs(qty) * px

    def schedule(kind: str, signal_i: int, reason: str) -> None:
        nonlocal pending
        if pending is not None:
            return
        fill = next_strictly_later_index(data.ts, signal_i, right)
        if fill is not None:
            pending = (kind, int(fill), reason)

    for i in range(left, right):
        op, close = float(data.open[i]), float(data.close[i])
        if pending is not None and i == pending[1]:
            kind, _, reason = pending
            pending = None
            if kind == "ENTRY" and qty == 0:
                px = op * (1.0 + slip)
                target = min(CAPACITY_USD, BASE_USD * c.entry_mult)
                q = target / px
                entry_fee = commission * q * px
                cash += q * px - entry_fee
                qty = -q
                open_entry_fees = entry_fee
                avg_entry = px
                entry_atr = float(ev["atr1"][i])
                entry_i = i
                next_scale = px - c.scale_trigger_atr * entry_atr
                peak_gain = 0.0
                entries += 1
            elif kind == "SCALE" and qty < 0:
                px = op * (1.0 + slip)
                cap = min(CAPACITY_USD, BASE_USD * c.max_mult)
                add_notional = max(0.0, min(BASE_USD, cap - notional(px)))
                if add_notional > 1.0:
                    add_q = add_notional / px
                    old_q = abs(qty)
                    entry_fee = commission * add_q * px
                    cash += add_q * px - entry_fee
                    qty -= add_q
                    open_entry_fees += entry_fee
                    avg_entry = (avg_entry * old_q + px * add_q) / (old_q + add_q)
                    next_scale = px - c.scale_trigger_atr * entry_atr
                    scales += 1
            elif kind == "EXIT" and qty < 0:
                px = op * (1.0 - slip)
                q = abs(qty)
                exit_fee = commission * q * px
                pnl = (avg_entry - px) * q - open_entry_fees - exit_fee
                cash -= q * px + exit_fee
                realized += pnl
                ret = (avg_entry - px) / avg_entry * 100.0
                trade_returns.append(ret)
                correction_pnl += max(0.0, ret)
                wins += int(ret > 0)
                exits += 1
                qty = 0.0
                avg_entry = 0.0
                open_entry_fees = 0.0
                entry_i = -1

        if qty == 0:
            if ev["entry"][i]:
                schedule("ENTRY", i, "CONFIRMED_1H_ROLLOVER")
        else:
            held += 1
            gain = (avg_entry - close) / avg_entry
            peak_gain = max(peak_gain, gain)
            atr = entry_atr if math.isfinite(entry_atr) and entry_atr > 0 else close * 0.02
            h = max(0.0, (int(data.ts[i]) - int(data.ts[entry_i])) / 3600.0)
            wt_bull = ev["w11"][i] > ev["w21"][i]
            exhausted = ev["k1"][i] <= c.cover_k and wt_bull
            profit_target = gain * avg_entry >= c.profit_atr * atr
            trail = (
                peak_gain * avg_entry >= 1.0 * atr
                and gain <= peak_gain * (1.0 - c.trail_fraction)
            )
            emergency = close >= avg_entry + c.emergency_atr * atr
            time_exit = h >= c.max_hold_hours
            correction_ended = wt_bull and close > ev["h1_hi"][i]
            if exhausted or profit_target or trail or emergency or time_exit or correction_ended:
                schedule(
                    "EXIT",
                    i,
                    "EXHAUST" if exhausted else
                    "PROFIT_ATR" if profit_target else
                    "TRAIL" if trail else
                    "EMERGENCY" if emergency else
                    "MAX_HOLD" if time_exit else
                    "CORRECTION_END",
                )
            elif (
                close <= next_scale
                and ev["w11"][i] < ev["w21"][i]
                and notional(close) < min(CAPACITY_USD, BASE_USD * c.max_mult) - 1.0
            ):
                schedule("SCALE", i, "CONFIRMED_DOWNSIDE")

        eq = equity(close)
        peak_eq = max(peak_eq, eq)
        min_eq = min(min_eq, eq)
        if peak_eq > 0:
            max_dd = max(max_dd, (peak_eq - eq) / peak_eq * 100.0)

    final_px = float(data.close[right - 1])
    final_eq = equity(final_px)
    mtm = (avg_entry - final_px) * abs(qty) if qty < 0 else 0.0
    strategy_return = (final_eq / ACCOUNT_USD - 1.0) * 100.0
    long_bh = (final_px / float(data.close[left]) - 1.0) * 100.0
    short_bh = -long_bh
    opp = max(0.0, short_bh)
    rows = max(1, right - left)
    downside = _downside_opportunity(data.close[left:right])
    return {
        "strategy_return_pct": strategy_return,
        "realized_cash_return_pct": realized / ACCOUNT_USD * 100.0,
        "open_mtm_return_pct": mtm / ACCOUNT_USD * 100.0,
        "cash_benchmark_pct": 0.0,
        "short_bh_return_pct": short_bh,
        "long_bh_opportunity_return_pct": long_bh,
        "opportunity_benchmark_pct": opp,
        "strategy_bh_multiple": strategy_return / short_bh if short_bh > 0 else None,
        "beats_opportunity_benchmark": strategy_return > opp,
        "max_drawdown_account_pct": max_dd,
        "minimum_account_equity_usd": min_eq,
        "insolvent": min_eq <= 0.0,
        "time_in_market_pct": held / rows * 100.0,
        "entries": entries,
        "scale_fills": scales,
        "technical_exits": exits,
        "win_rate_pct": wins / max(1, exits) * 100.0,
        "downside_variation_opportunity_pct": downside,
        "correction_capture_pct": correction_pnl / max(1e-12, downside) * 100.0,
        "trade_returns_pct": trade_returns,
        "rows": rows,
        "start_ts": int(data.ts[left]),
        "end_ts": int(data.ts[right - 1]),
    }


def _idx(data: top.ExecutionData, date: str) -> int:
    epoch = int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())
    return int(np.searchsorted(data.ts, epoch, side="left"))


def _windows(data: top.ExecutionData):
    bounds = [
        ("D1", "2024-03-26", "2025-07-01"),
        ("D2", "2025-07-01", FINAL_START),
        ("FINAL", FINAL_START, FINAL_END),
    ]
    out = []
    for name, a, b in bounds:
        left, right = _idx(data, a), _idx(data, b)
        if right - left >= 200:
            out.append((name, left, right))
    return out


def _select(discovery: list[dict[str, Any]]) -> tuple:
    solvent = all(not r["insolvent"] and r["max_drawdown_account_pct"] < 100 for r in discovery)
    positive = all(r["strategy_return_pct"] > 0 for r in discovery)
    active = all(r["technical_exits"] >= 2 for r in discovery)
    score = (
        float(np.median([r["strategy_return_pct"] for r in discovery]))
        - 0.35 * max(r["max_drawdown_account_pct"] for r in discovery)
        + 0.05 * float(np.median([r["correction_capture_pct"] for r in discovery]))
    )
    return solvent and positive and active, score


def run(args: argparse.Namespace) -> Path:
    cohorts = {
        "CORRECTION": tuple(x.upper() for x in args.correction_symbols.split(",") if x),
        "BEAR": tuple(x.upper() for x in args.bear_symbols.split(",") if x),
    }
    rows = []
    errors = []
    for book, symbols in cohorts.items():
        for symbol in symbols:
            try:
                data = top._load_execution(
                    symbol, Path(args.npz_dir), args.start, "ladder", args.end
                )
                if not data.contract["valid"]:
                    raise RuntimeError(str(data.contract["errors"]))
                htfs = {tf: top._compress_htf(data, tf) for tf in ("1h", "4h", "D")}
                windows = _windows(data)
                if len(windows) < 3:
                    raise RuntimeError(f"requires 3 folds, got {len(windows)}")
                tested = []
                for c in candidates(book):
                    ev = _events(data, htfs, c)
                    metrics = {
                        name: _simulate(
                            data, ev, c, left, right,
                            args.commission_bps, args.slippage_bps,
                        )
                        for name, left, right in windows
                    }
                    discovery = [metrics["D1"], metrics["D2"]]
                    passed, score = _select(discovery)
                    tested.append((passed, score, c, metrics, ev["source_future_count"]))
                # Freeze solely on discovery.  Passing candidates outrank failed
                # candidates; final metrics never participate in selection.
                tested.sort(key=lambda x: (not x[0], -x[1], x[2].label))
                passed, score, winner, metrics, future = tested[0]
                final = metrics["FINAL"]
                exact_eligible = bool(
                    passed
                    and future == 0
                    and not final["insolvent"]
                    and final["technical_exits"] >= 2
                    and final["beats_opportunity_benchmark"]
                )
                rows.append({
                    "symbol": symbol,
                    "side": "SHORT",
                    "book": book,
                    "selected_on_discovery_only": dataclasses.asdict(winner),
                    "selected_label": winner.label,
                    "discovery_gate_pass": passed,
                    "selection_score": score,
                    "source_future_count": future,
                    "folds": metrics,
                    "untouched_final": final,
                    "status": (
                        "VECTOR_SURVIVOR_EXACT_PENDING"
                        if exact_eligible else "GRAY_REJECTED"
                    ),
                    "exact_replay_eligible": exact_eligible,
                })
                data.z.close()
            except Exception as exc:
                errors.append({"symbol": symbol, "book": book, "error": str(exc)})

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir) / f"asymmetric_short_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "promotion_allowed": False,
        "exact_replay_allowed_only_for_survivors": True,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "cohorts": cohorts,
        "fold_contract": {
            "D1": ["2024-03-26", "2025-07-01"],
            "D2": ["2025-07-01", FINAL_START],
            "FINAL_UNTOUCHED": [FINAL_START, FINAL_END],
        },
        "candidate_counts": {book: len(candidates(book)) for book in cohorts},
        "account_usd": ACCOUNT_USD,
        "base_usd": BASE_USD,
        "capacity_usd": CAPACITY_USD,
        "costs": {
            "commission_bps_one_way": args.commission_bps,
            "slippage_bps_one_way": args.slippage_bps,
        },
        "benchmark_contract": (
            "raw strategy, cash=0, side-specific dollar-PnL short B&H, "
            "long B&H opportunity, DD, TIM and correction capture; no ratio "
            "when short B&H<=0"
        ),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    payload = {"manifest": manifest, "results": rows, "errors": errors}
    (out / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    md = [
        "# Asymmetric SHORT vector campaign",
        "",
        "Research only. Selection used D1+D2; FINAL was untouched until frozen.",
        "",
        "| book | symbol | status | strategy | cash | short B&H | long B&H | DD | TIM | capture | trades |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        f = r["untouched_final"]
        md.append(
            f"| {r['book']} | {r['symbol']}_SHORT | {r['status']} | "
            f"{f['strategy_return_pct']:+.2f}% | +0.00% | "
            f"{f['short_bh_return_pct']:+.2f}% | "
            f"{f['long_bh_opportunity_return_pct']:+.2f}% | "
            f"{f['max_drawdown_account_pct']:.2f}% | "
            f"{f['time_in_market_pct']:.2f}% | "
            f"{f['correction_capture_pct']:.2f}% | "
            f"{f['technical_exits']} |"
        )
    if errors:
        md += ["", "## Errors", ""] + [
            f"- {e['book']} {e['symbol']}: {e['error']}" for e in errors
        ]
    (out / "RESULTS.md").write_text("\n".join(md) + "\n")
    print(json.dumps({
        "artifact": str(out),
        "rows": len(rows),
        "errors": len(errors),
        "survivors": sum(r["exact_replay_eligible"] for r in rows),
    }, sort_keys=True))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-dir", default=str(top.DEFAULT_NPZ))
    ap.add_argument("--out-dir", default=str(ROOT / "data/reports/vec_research"))
    ap.add_argument("--start", default="2024-03-26")
    ap.add_argument("--end")
    ap.add_argument("--correction-symbols", default=",".join(CORRECTION_DEFAULT))
    ap.add_argument("--bear-symbols", default=",".join(BEAR_DEFAULT))
    ap.add_argument("--commission-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
