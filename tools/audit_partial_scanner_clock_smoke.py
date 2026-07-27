#!/usr/bin/env python3
"""Standalone compiled/Python smoke for the repaired partial scanner clock."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import vec_same_entry_partial_adapter as partial
from tools.research_availability_clock import CLOCK_CONTRACT


def terminal_oracle(
    side: int,
    entry_open: float,
    terminal_close: float,
    target: float,
    commission: float,
    slippage: float,
) -> float:
    entry_px = entry_open * (1.0 + side * slippage)
    qty = target / entry_px
    cash = 10_000.0 - side * target - commission * target
    exit_px = terminal_close * (1.0 - side * slippage)
    notional = qty * exit_px
    cash += side * (notional - side * commission * notional)
    return 100.0 * (cash - 10_000.0) / 2_000.0


def exit_reclaim_oracle(
    *,
    entry_open: float,
    exit_open: float,
    reclaim_level: float,
    terminal_close: float,
    target: float,
    commission: float,
    slippage: float,
) -> float:
    """Independent cash oracle for the frozen LONG exit/reclaim fixture."""
    entry_px = entry_open * (1.0 + slippage)
    qty = target / entry_px
    cash = 10_000.0 - target - commission * target
    exit_px = exit_open * (1.0 - slippage)
    exit_notional = qty * exit_px
    cash += exit_notional - commission * exit_notional
    reclaim_px = reclaim_level * (1.0 + slippage)
    reclaim_notional = exit_notional
    cash -= reclaim_notional + commission * reclaim_notional
    qty = reclaim_notional / reclaim_px
    terminal_px = terminal_close * (1.0 - slippage)
    terminal_notional = qty * terminal_px
    cash += terminal_notional - commission * terminal_notional
    return 100.0 * (cash - 10_000.0) / 2_000.0


def compiled_fixture(side: int, slow_source: int | None = None) -> dict:
    n = 120
    availability = np.concatenate(
        (
            np.full(4, 400, dtype=np.int64),
            np.full(3, 700, dtype=np.int64),
            1000 + np.arange(n - 7, dtype=np.int64) * 300,
        )
    )
    price = np.full(n, 10.0, dtype=np.float64)
    price[-1] = 12.0
    high = np.maximum(price, 10.2)
    low = np.minimum(price, 9.8)
    entry = np.zeros(n, dtype=np.float64)
    entry[0] = 4.0
    event = np.zeros(n, dtype=np.uint8)
    source = availability.copy()
    if slow_source is not None:
        event[5] = 1
        source[5] = slow_source
    ref = np.full(n, 10.0, dtype=np.float64)
    blank = np.full(n, np.nan, dtype=np.float64)
    regime = np.zeros(n, dtype=np.uint8)
    out = partial.PartialMetrics()
    rc = partial._library().vec_same_entry_partial_scan(
        n, 0, n, side, 0,
        availability, price, high, low, price, entry,
        np.zeros(n, dtype=np.uint8), source, ref,
        2, event, source, blank, ref, regime,
        0.25, 0.0, 0, 0.0005, 0.0002, ctypes.byref(out),
    )
    if rc:
        raise RuntimeError(f"compiled smoke returned {rc}")
    return {
        name: getattr(out, name)
        for name, _kind in partial.PartialMetrics._fields_
    }


def run() -> dict:
    scenarios = []
    for side in (1, -1):
        actual = compiled_fixture(side)
        expected = terminal_oracle(
            side, 10.0, 12.0, 8_000.0, 0.0005, 0.0002
        )
        delta = actual["capital_return_pct"] - expected
        if not math.isclose(delta, 0.0, rel_tol=0, abs_tol=1e-9):
            raise RuntimeError(f"terminal oracle mismatch for side {side}: {delta}")
        if int(actual["entry_fills"]) != 1:
            raise RuntimeError("duplicate parent batch emitted multiple entries")
        scenarios.append(
            {
                "side": "LONG" if side > 0 else "SHORT",
                "compiled_capital_return_pct": actual[
                    "capital_return_pct"
                ],
                "python_oracle_capital_return_pct": expected,
                "delta_pp": delta,
                "entry_fills": int(actual["entry_fills"]),
                "binary_tim_pct": actual["binary_tim_pct"],
            }
        )
    synthetic = compiled_fixture(1, slow_source=400)
    native = compiled_fixture(1, slow_source=700)
    exit_oracle = exit_reclaim_oracle(
        entry_open=10.0,
        exit_open=10.0,
        reclaim_level=10.0,
        terminal_close=12.0,
        target=8_000.0,
        commission=0.0005,
        slippage=0.0002,
    )
    for label, actual in (("synthetic", synthetic), ("native", native)):
        if not math.isclose(
            actual["capital_return_pct"],
            exit_oracle,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise RuntimeError(
                f"{label} exit/reclaim oracle mismatch: "
                f"{actual['capital_return_pct']-exit_oracle}"
            )
    parity_keys = (
        "capital_return_pct",
        "full_exit_fills",
        "entry_fills",
        "future_htf_count",
        "binary_tim_pct",
        "exposure_weighted_tim_pct",
    )
    parity = {
        key: {
            "synthetic_parent_source": synthetic[key],
            "native_source": native[key],
            "delta": synthetic[key] - native[key],
        }
        for key in parity_keys
    }
    if any(abs(float(row["delta"])) > 1e-12 for row in parity.values()):
        raise RuntimeError("native/synthetic source parity failed")
    source = partial.C_SOURCE
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "clock_contract": CLOCK_CONTRACT,
        "scanner_source": str(source),
        "scanner_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "terminal_liquidation_booking_count": 1,
        "terminal_duplicate_diagnosis": "DISPROVED",
        "strictly_later_duplicate_batch": "PASS",
        "terminal_oracle": scenarios,
        "native_synthetic_parent_close_parity": parity,
        "exit_reclaim_python_oracle": {
            "capital_return_pct": exit_oracle,
            "synthetic_delta_pp": (
                synthetic["capital_return_pct"] - exit_oracle
            ),
            "native_delta_pp": native["capital_return_pct"] - exit_oracle,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", type=Path, required=True)
    args = ap.parse_args()
    payload = run()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
