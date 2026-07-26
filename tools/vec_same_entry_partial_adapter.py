#!/usr/bin/env python3
"""Compiled same-entry screen for the two-stage partial-runner registry path."""
from __future__ import annotations

import argparse
import ctypes
import dataclasses
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import vec_partial_regime_walkforward as partial  # noqa: E402
from tools import vec_same_entry_exit_adapter as shared  # noqa: E402
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402


C_SOURCE = ROOT / "tools" / "vec_same_entry_partial_scan.c"
_LIBRARY: ctypes.CDLL | None = None


class PartialMetrics(ctypes.Structure):
    _fields_ = [
        ("capital_return_pct", ctypes.c_double),
        ("bh_capital_return_pct", ctypes.c_double),
        ("exposure_weighted_tim_pct", ctypes.c_double),
        ("binary_tim_pct", ctypes.c_double),
        ("max_drawdown_account_pct", ctypes.c_double),
        ("minimum_account_equity_usd", ctypes.c_double),
        ("peak_post_fill_notional_usd", ctypes.c_double),
        ("requested_notional_usd", ctypes.c_double),
        ("filled_notional_usd", ctypes.c_double),
        ("realized_partial_gross_usd", ctypes.c_double),
        ("realized_partial_net_usd", ctypes.c_double),
        ("realized_full_gross_usd", ctypes.c_double),
        ("realized_full_net_usd", ctypes.c_double),
        ("unfilled_obligation_notional_usd", ctypes.c_double),
        ("insolvent", ctypes.c_int),
        ("entry_capacity_breach", ctypes.c_int),
        ("partial_signals", ctypes.c_int),
        ("regime_vetoed_partial_signals", ctypes.c_int),
        ("partial_exit_fills", ctypes.c_int),
        ("full_exit_fills", ctypes.c_int),
        ("technical_exit_fills", ctypes.c_int),
        ("entry_fills", ctypes.c_int),
        ("reclaim_reentries", ctypes.c_int),
        ("lower_reentries", ctypes.c_int),
        ("clamp_count", ctypes.c_int),
        ("reclaim_obligations_created", ctypes.c_int),
        ("reclaim_obligations_filled", ctypes.c_int),
        ("reclaim_obligations_unfilled_at_end", ctypes.c_int),
        ("bars_flat_beyond_reclaim", ctypes.c_int),
        ("future_htf_count", ctypes.c_int),
        ("rows", ctypes.c_int),
    ]


def _library() -> ctypes.CDLL:
    global _LIBRARY
    if _LIBRARY is not None:
        return _LIBRARY
    digest = hashlib.sha256(C_SOURCE.read_bytes()).hexdigest()[:16]
    path = Path("/tmp") / f"vec_same_entry_partial_{digest}.so"
    if not path.exists():
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        subprocess.run(
            [
                os.environ.get("CC", "cc"),
                "-O3",
                "-std=c11",
                "-fPIC",
                "-shared",
                str(C_SOURCE),
                "-o",
                str(temporary),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        os.replace(temporary, path)
    lib = ctypes.CDLL(str(path))
    i64 = np.ctypeslib.ndpointer(np.int64, ndim=1, flags="C_CONTIGUOUS")
    f64 = np.ctypeslib.ndpointer(np.float64, ndim=1, flags="C_CONTIGUOUS")
    u8 = np.ctypeslib.ndpointer(np.uint8, ndim=1, flags="C_CONTIGUOUS")
    lib.vec_same_entry_partial_scan.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        i64, f64, f64, f64, f64, f64,
        u8, i64, f64,
        ctypes.c_int, u8, i64, f64, f64, u8,
        ctypes.c_double, ctypes.c_double, ctypes.c_int,
        ctypes.c_double, ctypes.c_double,
        ctypes.POINTER(PartialMetrics),
    ]
    lib.vec_same_entry_partial_scan.restype = ctypes.c_int
    _LIBRARY = lib
    return lib


@dataclasses.dataclass(frozen=True)
class EventArrays:
    event: np.ndarray
    source: np.ndarray
    ref: np.ndarray
    mode: int = 2
    raw_stop: np.ndarray | None = None


@dataclasses.dataclass(frozen=True)
class PartialSetting:
    first_clip_fraction: float
    second_clip_fraction: float
    fast_family: str
    slow_family: str
    regime_switch: bool


def registry_grid() -> list[PartialSetting]:
    rows = [
        PartialSetting(f1, f2, fast, slow, regime)
        for f1 in (0.15, 0.25, 0.33, 0.50)
        for f2 in (0.0, 0.15, 0.25, 0.33)
        for fast in ("E05", "E06", "WT")
        for slow in ("E01", "E02")
        for regime in (False, True)
        if f1 + f2 <= 1.0
    ]
    # The registry's published fields multiply to 192, not 128. All sums are
    # <=0.83, so no listed pair is invalid and none may be silently discarded.
    assert len(rows) == 192
    return rows


def _event_sources(n: int, htf: Any) -> np.ndarray:
    rows = np.arange(n, dtype=np.int64)
    slots = np.searchsorted(htf.event_index, rows, side="right") - 1
    out = np.zeros(n, dtype=np.int64)
    valid = slots >= 0
    out[valid] = np.asarray(htf.source_ts, dtype=np.int64)[slots[valid]]
    return out


def _component_arrays(data: Any, htfs: dict[str, Any], side: str) -> dict[str, EventArrays]:
    n = len(data.ts)
    sign = 1 if side == "LONG" else -1
    fast, _, slow = partial._component_pools(data, htfs, sign)
    e05 = next(item for item in fast if item.family.startswith("E05"))
    e06 = next(item for item in fast if item.family.startswith("E06"))
    source4 = _event_sources(n, htfs["4h"])
    wt = shared.build_wt_mtf_book(
        data,
        htfs,
        shared.WtMtfParams(
            timeframes=("1h", "4h"),
            min_against_tfs=1,
            extreme=65.0,
            velocity=0.5,
        ),
        side=side,
    )
    wt_source = np.zeros(n, dtype=np.int64)
    for row, sources in wt.source_by_row.items():
        wt_source[row] = max(sources.values(), default=0)
    e01 = next(
        item
        for item in slow
        if item.family.startswith("E01") and item.params["tf"] == "4h"
    )
    return {
        "E05": EventArrays(
            np.ascontiguousarray(e05.event, dtype=np.uint8),
            source4,
            np.ascontiguousarray(e05.ref, dtype=np.float64),
        ),
        "E06": EventArrays(
            np.ascontiguousarray(e06.event, dtype=np.uint8),
            source4,
            np.ascontiguousarray(e06.ref, dtype=np.float64),
        ),
        "WT": EventArrays(
            np.ascontiguousarray(wt.events, dtype=np.uint8),
            wt_source,
            np.ascontiguousarray(wt.references, dtype=np.float64),
        ),
        "E01": EventArrays(
            np.zeros(n, dtype=np.uint8),
            source4,
            np.ascontiguousarray(e01.ref, dtype=np.float64),
            mode=1,
            raw_stop=np.ascontiguousarray(e01.raw_stop, dtype=np.float64),
        ),
    }


def _e02_arrays(
    data: Any, htfs: dict[str, Any], signals: ladder.SignalData
) -> EventArrays:
    book = shared.build_e02_book(data, signals, htfs)
    source = np.zeros(len(data.ts), dtype=np.int64)
    for row, sources in book.source_by_row.items():
        source[row] = max(sources.values(), default=0)
    return EventArrays(book.events, source, book.references)


def _scan(
    data: Any,
    htfs: dict[str, Any],
    ctx: dict[str, Any],
    components: dict[str, EventArrays],
    setting: PartialSetting,
    regime: np.ndarray,
    commission: float,
    slippage: float,
    side: str,
) -> dict[str, Any]:
    fast = components[setting.fast_family]
    slow = (
        components["E01"]
        if setting.slow_family == "E01"
        else _e02_arrays(data, htfs, ctx["signals"])
    )
    out = PartialMetrics()
    blank = np.full(len(data.ts), np.nan, dtype=np.float64)
    rc = _library().vec_same_entry_partial_scan(
        len(data.ts), ctx["left"], ctx["right"],
        1 if side == "LONG" else -1,
        0 if ctx["curve"].semantics == "target" else 1,
        np.ascontiguousarray(data.ts, dtype=np.int64),
        np.ascontiguousarray(data.open, dtype=np.float64),
        np.ascontiguousarray(data.high, dtype=np.float64),
        np.ascontiguousarray(data.low, dtype=np.float64),
        np.ascontiguousarray(data.close, dtype=np.float64),
        np.ascontiguousarray(ctx["signals"].entry_mult, dtype=np.float64),
        fast.event, fast.source, fast.ref,
        slow.mode, slow.event, slow.source,
        slow.raw_stop if slow.raw_stop is not None else blank,
        slow.ref,
        np.ascontiguousarray(regime > 0, dtype=np.uint8),
        setting.first_clip_fraction,
        setting.second_clip_fraction,
        int(setting.regime_switch),
        commission, slippage, ctypes.byref(out),
    )
    if rc:
        raise RuntimeError(f"compiled partial scan failed with {rc}")
    requested = float(out.requested_notional_usd)
    return {
        "capital_return_pct": float(out.capital_return_pct),
        "bh_capital_return_pct": float(out.bh_capital_return_pct),
        "exposure_weighted_tim_pct": float(out.exposure_weighted_tim_pct),
        "binary_tim_pct": float(out.binary_tim_pct),
        "max_drawdown_account_pct": float(out.max_drawdown_account_pct),
        "minimum_account_equity_usd": float(out.minimum_account_equity_usd),
        "peak_post_fill_notional_usd": float(out.peak_post_fill_notional_usd),
        "requested_notional_usd": requested,
        "filled_notional_usd": float(out.filled_notional_usd),
        "fill_ratio": (
            float(out.filled_notional_usd) / requested
            if requested > 0 else 1.0
        ),
        "realized_partial_pnl_gross_usd": float(
            out.realized_partial_gross_usd
        ),
        "realized_partial_pnl_net_usd": float(out.realized_partial_net_usd),
        "realized_full_pnl_gross_usd": float(out.realized_full_gross_usd),
        "realized_full_pnl_net_usd": float(out.realized_full_net_usd),
        "unfilled_obligation_notional_usd": float(
            out.unfilled_obligation_notional_usd
        ),
        "insolvent": bool(out.insolvent),
        "entry_capacity_breach": bool(out.entry_capacity_breach),
        "partial_signals": int(out.partial_signals),
        "regime_vetoed_partial_signals": int(
            out.regime_vetoed_partial_signals
        ),
        "partial_exit_fills": int(out.partial_exit_fills),
        "full_exit_fills": int(out.full_exit_fills),
        "technical_exit_fills": int(out.technical_exit_fills),
        "entry_fills": int(out.entry_fills),
        "reclaim_reentries": int(out.reclaim_reentries),
        "lower_or_higher_reentries": int(out.lower_reentries),
        "clamp_count": int(out.clamp_count),
        "reclaim_obligations_created": int(
            out.reclaim_obligations_created
        ),
        "reclaim_obligations_filled": int(out.reclaim_obligations_filled),
        "clip_obligations_unfilled_at_end": int(
            out.reclaim_obligations_unfilled_at_end
        ),
        "bars_flat_beyond_reclaim": int(out.bars_flat_beyond_reclaim),
        "future_htf_source_count": int(out.future_htf_count),
        "rows": int(out.rows),
        "frozen_entry_schedule_sha256": shared._entry_schedule_hash(
            data, ctx["signals"], ctx["curve"], ctx["left"], ctx["right"]
        ),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_rows = sum(row["rows"] for row in rows)
    sum_keys = (
        "capital_return_pct", "bh_capital_return_pct",
        "requested_notional_usd", "filled_notional_usd",
        "realized_partial_pnl_gross_usd", "realized_partial_pnl_net_usd",
        "realized_full_pnl_gross_usd", "realized_full_pnl_net_usd",
        "unfilled_obligation_notional_usd", "partial_signals",
        "regime_vetoed_partial_signals", "partial_exit_fills",
        "full_exit_fills", "technical_exit_fills", "entry_fills",
        "reclaim_reentries", "lower_or_higher_reentries", "clamp_count",
        "reclaim_obligations_created", "reclaim_obligations_filled",
        "clip_obligations_unfilled_at_end", "bars_flat_beyond_reclaim",
        "future_htf_source_count",
    )
    out = {key: sum(row[key] for row in rows) for key in sum_keys}
    out.update(
        {
            "folds": len(rows),
            "exposure_weighted_tim_pct": sum(
                row["exposure_weighted_tim_pct"] * row["rows"]
                for row in rows
            ) / max(1, total_rows),
            "binary_tim_pct": sum(
                row["binary_tim_pct"] * row["rows"] for row in rows
            ) / max(1, total_rows),
            "max_drawdown_account_pct": max(
                row["max_drawdown_account_pct"] for row in rows
            ),
            "minimum_account_equity_usd": min(
                row["minimum_account_equity_usd"] for row in rows
            ),
            "peak_post_fill_notional_usd": max(
                row["peak_post_fill_notional_usd"] for row in rows
            ),
            "insolvent_folds": sum(row["insolvent"] for row in rows),
            "entry_capacity_breach": any(
                row["entry_capacity_breach"] for row in rows
            ),
            "entry_schedule_sha256_by_fold": [
                row["frozen_entry_schedule_sha256"] for row in rows
            ],
        }
    )
    out["fill_ratio"] = (
        out["filled_notional_usd"] / out["requested_notional_usd"]
        if out["requested_notional_usd"] > 0 else 1.0
    )
    return out


def screen_artifact(
    artifact: Path, npz_dir: Path, exposure_min: float, exposure_max: float
) -> dict[str, Any]:
    source, data, htfs, contexts = shared._fold_contexts(
        artifact, npz_dir, fold_mode="nested"
    )
    manifest = source["manifest"]
    side = manifest["side"]
    commission = float(manifest["commission_bps_one_way"]) / 10_000.0
    slippage = float(manifest["slippage_bps_one_way"]) / 10_000.0
    controls = [
        shared.simulate(
            data, ctx["signals"], ctx["curve"],
            shared.build_e02_book(data, ctx["signals"], htfs),
            ctx["left"], ctx["right"], commission, slippage, side=side,
        )
        for ctx in contexts
    ]
    control = shared._aggregate(controls)
    components = _component_arrays(data, htfs, side)
    sign = 1 if side == "LONG" else -1
    regime = partial._regime_array(
        len(data.ts), htfs["D"], sign, 20, 20, 50
    )
    candidates: list[dict[str, Any]] = []
    started = time.perf_counter()
    for setting in registry_grid():
        folds = [
            _scan(
                data, htfs, ctx, components, setting, regime,
                commission, slippage, side,
            )
            for ctx in contexts
        ]
        discovery = _aggregate(folds[:-1])
        validation = _aggregate(folds[-1:])
        control_discovery = shared._aggregate(controls[:-1])
        control_validation = shared._aggregate(controls[-1:])
        evidence = []
        for row, ctrl, ctx in zip(folds, controls, contexts):
            evidence.append(
                {
                    "fold": ctx["fold"],
                    "strategy_return_pct": row["capital_return_pct"],
                    "bh_return_pct": row["bh_capital_return_pct"],
                    "same_entry_e02_return_pct": ctrl["capital_return_pct"],
                    "alpha_vs_bh_pp": (
                        row["capital_return_pct"] - row["bh_capital_return_pct"]
                    ),
                    "alpha_vs_same_entry_e02_pp": (
                        row["capital_return_pct"] - ctrl["capital_return_pct"]
                    ),
                    "weighted_tim_pct": row[
                        "exposure_weighted_tim_pct"
                    ],
                    "partial_exit_fills": row["partial_exit_fills"],
                    "realized_partial_pnl_net_usd": row[
                        "realized_partial_pnl_net_usd"
                    ],
                    "unfilled_obligations": row[
                        "clip_obligations_unfilled_at_end"
                    ],
                    "insolvent": row["insolvent"],
                }
            )
        params = dataclasses.asdict(setting)
        row = {
            "family": "EXIT_PARTIAL_RUNNER",
            "params": params,
            "fold_evidence": evidence,
            "metrics": _aggregate(folds),
            "nested": {
                "discovery": discovery,
                "validation": validation,
                "discovery_alpha_vs_bh_pp": (
                    discovery["capital_return_pct"]
                    - discovery["bh_capital_return_pct"]
                ),
                "discovery_alpha_vs_same_entry_e02_pp": (
                    discovery["capital_return_pct"]
                    - control_discovery["capital_return_pct_sum"]
                ),
                "validation_alpha_vs_bh_pp": (
                    validation["capital_return_pct"]
                    - validation["bh_capital_return_pct"]
                ),
                "validation_alpha_vs_same_entry_e02_pp": (
                    validation["capital_return_pct"]
                    - control_validation["capital_return_pct_sum"]
                ),
            },
        }
        row["nested"]["robust_discovery_all_folds"] = all(
            e["alpha_vs_bh_pp"] > 0
            and e["alpha_vs_same_entry_e02_pp"] > 0
            and e["partial_exit_fills"] > 0
            and e["unfilled_obligations"] == 0
            and not e["insolvent"]
            for e in evidence[:-1]
        )
        row["nested"]["robust_validation_fold"] = (
            evidence[-1]["alpha_vs_bh_pp"] > 0
            and evidence[-1]["alpha_vs_same_entry_e02_pp"] > 0
            and evidence[-1]["partial_exit_fills"] > 0
            and evidence[-1]["unfilled_obligations"] == 0
            and not evidence[-1]["insolvent"]
        )
        candidates.append(row)
    elapsed = time.perf_counter() - started
    candidates.sort(
        key=lambda row: (
            not row["nested"]["robust_discovery_all_folds"],
            not (
                exposure_min
                <= row["nested"]["discovery"]["exposure_weighted_tim_pct"]
                <= exposure_max
            ),
            -row["nested"]["discovery_alpha_vs_same_entry_e02_pp"],
            -row["nested"]["discovery_alpha_vs_bh_pp"],
        )
    )
    winner = candidates[0]
    n = winner["nested"]
    m = winner["metrics"]
    survivor = bool(
        n["robust_discovery_all_folds"]
        and n["robust_validation_fold"]
        and exposure_min
        <= n["discovery"]["exposure_weighted_tim_pct"]
        <= exposure_max
        and exposure_min
        <= n["validation"]["exposure_weighted_tim_pct"]
        <= exposure_max
        and m["partial_exit_fills"] > 0
        and m["technical_exit_fills"] > 0
        and m["clip_obligations_unfilled_at_end"] == 0
        and m["bars_flat_beyond_reclaim"] == 0
        and m["future_htf_source_count"] == 0
        and m["insolvent_folds"] == 0
        and not m["entry_capacity_breach"]
    )
    schedule = control["entry_schedule_sha256_by_fold"]
    if any(
        row["metrics"]["entry_schedule_sha256_by_fold"] != schedule
        for row in candidates
    ):
        data.z.close()
        raise RuntimeError("partial candidate changed frozen entry schedule")
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_PARTIAL_RUNNER",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "symbol": manifest["symbol"],
        "side": side,
        "source_artifact": str(artifact.resolve()),
        "source_npz_sha256": manifest["npz_sha256"],
        "registry_grid_declared_count_correction": (
            "4x4x3x2x2=192; all clip sums <=0.83, so none are invalid"
        ),
        "candidate_count": len(candidates),
        "compiled_grid_elapsed_seconds": elapsed,
        "same_adapter_e02_control": control,
        "frozen_discovery_winners": [winner],
        "survivor_count": int(survivor),
        "survivors": [winner] if survivor else [],
        "exact_replay_allowed": survivor,
        "dc_low4_profit_exit_used": False,
        "candidates": candidates,
    }
    data.z.close()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--npz-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--exposure-min-pct", type=float, default=70.0)
    parser.add_argument("--exposure-max-pct", type=float, default=80.0)
    args = parser.parse_args()
    payload = screen_artifact(
        args.artifact.resolve(), args.npz_dir.resolve(),
        args.exposure_min_pct, args.exposure_max_pct,
    )
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "symbol": payload["symbol"],
                "side": payload["side"],
                "candidate_count": payload["candidate_count"],
                "survivor_count": payload["survivor_count"],
                "elapsed": payload["compiled_grid_elapsed_seconds"],
                "output": str(args.out_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
