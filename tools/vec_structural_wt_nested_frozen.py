#!/usr/bin/env python3
"""Freeze structural-WT params on early MU, score later MU and universal VT.

Research only: this script never writes the switch matrix or live config.
Every scored candidate is compared with both $2k B&H and the exact same frozen
$16k-capacity band-ladder baseline.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from tools.vec_structural_wt_retest_probe import simulate  # noqa: E402
from vec_paths.structural_wt_retest_exit import StructuralWtParams  # noqa: E402


def _load_source(path: Path) -> tuple[dict[str, Any], ladder.Curve]:
    source = json.loads((path / "result.json").read_text())
    return source, ladder.Curve(**source["outer_folds"][-1]["selected_curve"])


def _score_window(
    *,
    artifact: Path,
    npz_dir: Path,
    start: str,
    end: str,
    params: StructuralWtParams,
) -> dict[str, Any]:
    source, curve = _load_source(artifact)
    symbol = source["manifest"]["symbol"]
    data = ladder.top._load_execution(symbol, npz_dir, start, "ladder", end)
    if not data.contract["valid"]:
        data.z.close()
        raise RuntimeError(f"{symbol} quarantined: {data.contract['errors']}")
    htfs = {
        tf: ladder.top._compress_htf(data, tf)
        for tf in ("1h", "4h", "D")
    }
    entry = ladder._build_signals(
        data, htfs, curve, int(source["manifest"]["exit"]["n"])
    )
    commission_rate = (
        float(source["manifest"]["commission_bps_one_way"]) / 10_000.0
    )
    slippage_rate = (
        float(source["manifest"]["slippage_bps_one_way"]) / 10_000.0
    )
    candidate = simulate(
        data,
        entry,
        curve,
        htfs,
        params,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    )
    baseline = ladder._simulate(
        data,
        entry,
        curve,
        0,
        len(data.ts),
        commission_rate,
        slippage_rate,
    )
    row = {
        "symbol": symbol,
        "window": [start, end],
        "npz_sha256": source["manifest"]["npz_sha256"],
        "npz_contract_valid": True,
        "frozen_ladder_curve": dataclasses.asdict(curve),
        "params": dataclasses.asdict(params),
        "candidate": candidate,
        "ladder_baseline": baseline,
        "candidate_minus_ladder_pp": (
            candidate["capital_return_pct"]
            - baseline["capital_return_pct"]
        ),
        "candidate_ladder_multiple": (
            candidate["capital_return_pct"]
            / baseline["capital_return_pct"]
            if abs(baseline["capital_return_pct"]) > 1e-12
            else None
        ),
    }
    data.z.close()
    return row


def _grid() -> list[StructuralWtParams]:
    return [
        StructuralWtParams(
            rebound_atr=rebound,
            prebreak_lookback=lookback,
            max_wait_1h=wait,
        )
        for rebound in (0.25, 0.5, 1.0)
        for lookback in (4, 6, 10)
        for wait in (12, 20, 30)
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mu-artifact", type=Path, required=True)
    ap.add_argument("--vt-artifact", type=Path)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--discovery-start", default="2026-01-01")
    ap.add_argument("--discovery-end", default="2026-04-01")
    ap.add_argument("--validation-start", default="2026-04-01")
    ap.add_argument("--validation-end", default="2026-07-25")
    args = ap.parse_args()

    discovery = [
        _score_window(
            artifact=args.mu_artifact,
            npz_dir=args.npz_dir,
            start=args.discovery_start,
            end=args.discovery_end,
            params=params,
        )
        for params in _grid()
    ]
    # Select against the exact same ladder, not merely against B&H.
    discovery.sort(
        key=lambda row: (
            -float(row["candidate_minus_ladder_pp"]),
            float(row["candidate"]["max_drawdown_account_pct"]),
            -float(row["candidate"]["exposure_weighted_tim_pct"]),
        )
    )
    frozen = StructuralWtParams(**discovery[0]["params"])
    mu_validation = _score_window(
        artifact=args.mu_artifact,
        npz_dir=args.npz_dir,
        start=args.validation_start,
        end=args.validation_end,
        params=frozen,
    )

    vt_universal = None
    if args.vt_artifact:
        vt_source, _ = _load_source(args.vt_artifact)
        vt_start, vt_end = vt_source["outer_folds"][-1]["validation"]
        vt_universal = _score_window(
            artifact=args.vt_artifact,
            npz_dir=args.npz_dir,
            start=vt_start,
            end=vt_end,
            params=frozen,
        )

    payload = {
        "tier": "VEC_RESEARCH_NESTED_FROZEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "capital_contract": {
            "strategy_hard_capacity_usd": ladder.CAPACITY,
            "bh_denominator_usd": ladder.BASE_UNIT,
        },
        "selection": {
            "symbol": "MU",
            "window": [args.discovery_start, args.discovery_end],
            "objective": (
                "maximize candidate capital return minus exact same frozen "
                "band-ladder capital return"
            ),
            "candidate_count": len(discovery),
            "frozen_params": dataclasses.asdict(frozen),
            "winner": discovery[0],
        },
        "frozen_scores": {
            "MU_LONG": mu_validation,
            "VT_LONG": vt_universal,
        },
        "discovery_grid": discovery,
    }
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.out_dir),
                "frozen_params": dataclasses.asdict(frozen),
                "MU_LONG": mu_validation,
                "VT_LONG": vt_universal,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
