#!/usr/bin/env python3
"""Parity-bound Python/compiled ladder scanner benchmark.

Loads each causal NPZ/HTF context once, builds one coherent schedule once, then
times only the repeated stateful accounting layer.  This separates scanner
throughput from NPZ I/O and signal construction while still using real symbol
arrays, parent availability timestamps, side accounting, and reentry.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import vec_band_ladder_walkforward as ladder


METRIC_TOLERANCE = 1e-8


def _assert_parity(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    if expected.keys() != actual.keys():
        raise AssertionError("compiled/Python metric keys differ")
    for key, value in expected.items():
        other = actual[key]
        if isinstance(value, float):
            if value is None and other is None:
                continue
            if not math.isclose(value, other, rel_tol=1e-10, abs_tol=METRIC_TOLERANCE):
                raise AssertionError(f"{key}: Python={value} compiled={other}")
        elif value != other:
            raise AssertionError(f"{key}: Python={value!r} compiled={other!r}")


def benchmark_case(
    symbol: str,
    side: str,
    npz_dir: Path,
    repeats: int,
) -> dict[str, Any]:
    if symbol == "HAO":
        # The recovery NPZ is immutable/hash-bound but intentionally isolated
        # from the canonical matrix directory.
        from tools import run_hao_solvency_ladder_grid as hao
        from tools import vec_top_exit_campaign as top

        hao._install_hash_bound_hao_exception(npz_dir / "HAO.npz")
        ladder.top.audit_npz = top.audit_npz
    data = ladder.top._load_execution(
        symbol, npz_dir, "2024-01-01", "ladder", "2026-07-25"
    )
    try:
        htfs = {
            timeframe: ladder.top._compress_htf(data, timeframe)
            for timeframe in ladder.TF_ORDER
        }
        curve = next(
            row for row in ladder._curves(17, 0)
            if row.label == "REM_linear_union_add"
        )
        signals = ladder._build_signals(data, htfs, curve, 30, side)
        windows = []
        for _, validation_start, validation_end in ladder._outer_windows(
            symbol, data
        ):
            left = ladder._date_index(data, validation_start)
            right = ladder._date_index(data, validation_end)
            if right - left >= 100:
                windows.append((left, right))
        if not windows:
            windows = [(0, len(data.ts))]

        # Compile/load outside the timed after sample.
        ladder._compiled_scan_library()

        def timed(function):
            wall0, cpu0 = time.perf_counter(), time.process_time()
            results = []
            for _ in range(repeats):
                for left, right in windows:
                    results.append(
                        function(
                            data,
                            signals,
                            curve,
                            left,
                            right,
                            0.0005,
                            0.0002,
                            side,
                        )
                    )
            return (
                time.perf_counter() - wall0,
                time.process_time() - cpu0,
                results,
            )

        before_wall, before_cpu, expected = timed(ladder._simulate_python)
        after_wall, after_cpu, actual = timed(ladder._simulate_compiled)
        for left, right in zip(expected, actual):
            _assert_parity(left, right)
        return {
            "symbol": symbol,
            "side": side,
            "npz": str(npz_dir / f"{symbol}.npz"),
            "rows": len(data.ts),
            "windows": len(windows),
            "repeats": repeats,
            "simulations": repeats * len(windows),
            "python_wall_seconds": before_wall,
            "python_cpu_seconds": before_cpu,
            "compiled_wall_seconds": after_wall,
            "compiled_cpu_seconds": after_cpu,
            "wall_speedup_x": before_wall / max(after_wall, 1e-12),
            "cpu_speedup_x": before_cpu / max(after_cpu, 1e-12),
            "compiled_python_parity": "PASS",
        }
    finally:
        data.z.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        action="append",
        required=True,
        help="SYMBOL:SIDE:NPZ_DIR (repeatable)",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows = []
    for value in args.case:
        symbol, side, directory = value.split(":", 2)
        rows.append(
            benchmark_case(
                symbol.upper(),
                side.upper(),
                Path(directory).resolve(),
                args.repeats,
            )
        )
    payload = {
        "contract": "CAUSAL_BAND_LADDER_SCANNER_BENCHMARK_V1",
        "rows": rows,
        "aggregate_wall_speedup_x": (
            sum(row["python_wall_seconds"] for row in rows)
            / sum(row["compiled_wall_seconds"] for row in rows)
        ),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
