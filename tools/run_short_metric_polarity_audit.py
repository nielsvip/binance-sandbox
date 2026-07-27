#!/usr/bin/env python3
"""Causal vector-first audit of SHORT metric polarity and native asymmetry.

The audit has two independent products:

* an explicit metric inventory for the routed production/research SHORT stack;
* a small preregistered D1/D2 screen of correction and bear-continuation books.

FINAL is not loaded into the simulator for a key unless at least one candidate
passes both discovery folds.  The runner is research-only: it cannot modify
live configuration, canonical NPZ files or green matrix cells.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import sqlite3
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

from tools import path_fleet_campaign as fleet  # noqa: E402
from tools import vec_short_native_phase2 as native  # noqa: E402
from tools import vec_top_exit_campaign as top  # noqa: E402
from tools.research_availability_clock import CLOCK_CONTRACT  # noqa: E402


STAGE = "SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2"
CONTRACT = "SHORT_POLARITY_TWO_BOOKS_SEALED_FINAL_V2"
CORRECTION_SYMBOLS = ("NVDA", "MU", "SNDK", "MRVL", "ARM")
BEAR_SYMBOLS = ("MSTR", "COIN", "IBIT")
FINAL_LABEL = "FINAL"


def _metric(
    family: str,
    metric: str,
    classification: str,
    short_semantics: str,
    source: str,
    risk: str = "",
) -> dict[str, str]:
    return {
        "family": family,
        "metric": metric,
        "classification": classification,
        "short_semantics": short_semantics,
        "source": source,
        "risk": risk,
    }


def metric_inventory() -> list[dict[str, str]]:
    """Return the frozen metric map for SHORT paths exercised by this audit.

    The classification vocabulary is deliberately finite:
    SIGN_INVERTED, THRESHOLD_COMPLEMENTED, SIDE_NEUTRAL, SHORT_NATIVE.
    """
    m: list[dict[str, str]] = []
    add = m.append
    # WT/DC production entry.
    for name, semantics in (
        ("wt1_D/wt2_D", "require wt1_D < wt2_D"),
        ("wt1_4h/wt2_4h", "require wt1_4h < wt2_4h"),
        ("wt_cross_1h", "require BEAR, not numeric sign re-inversion"),
    ):
        add(_metric("WT_DC_ENTRY", name, "SIGN_INVERTED", semantics,
                    "wt_dc_entry_scorer.py:107-145"))
    add(_metric("WT_DC_ENTRY", "dc_position_1h", "THRESHOLD_COMPLEMENTED",
                "SHORT >0.50 mirrors LONG <0.50", "wt_dc_entry_scorer.py:107-145"))
    add(_metric("WT_DC_ENTRY", "stoch_k_5m", "THRESHOLD_COMPLEMENTED",
                "SHORT >60 mirrors LONG <40", "wt_dc_entry_scorer.py:107-145"))

    # Production exit scorer.  Extremes complement around 100, while gain
    # trailing and hard loss are already side-normalized by position.gain.
    for name, semantics in (
        ("wt_cross_1h", "cover on BULL"),
        ("wt1_4h/wt2_4h", "cover when wt1_4h > wt2_4h"),
        ("wt1_D/wt2_D", "cover when wt1_D > wt2_D"),
    ):
        add(_metric("WT_DC_EXIT", name, "SIGN_INVERTED", semantics,
                    "wt_dc_exit_scorer.py:75-128"))
    for name, semantics in (
        ("stoch_k_1h/stoch_k_4h", "cover <=100-K_EXTREME"),
        ("dc_position_1h/dc_position_4h", "cover <=1-DC_EXTREME"),
    ):
        add(_metric("WT_DC_EXIT", name, "THRESHOLD_COMPLEMENTED", semantics,
                    "wt_dc_exit_scorer.py:75-128"))
    for name in ("max_gain", "current_gain"):
        add(_metric("WT_DC_EXIT", name, "SIDE_NEUTRAL",
                    "uses position-side normalized gain", "wt_dc_entry_scorer.py:147-169"))

    # Dedicated correction and continuation context.
    for name, semantics in (
        ("pct_from_sma200_4h", "positive distance identifies extended rally"),
        ("lrL_pct_b_4h", "high percentile arms top rejection"),
        ("stoch_k_4h", "high raw K arms overbought correction"),
        ("bar_upper_wick_1h", "large upper wick is rejection"),
        ("peak_gap_atr_D", "small gap to peak for correction; large gap for bear"),
    ):
        add(_metric("SHORT_NATIVE_CONTEXT", name, "SHORT_NATIVE", semantics,
                    "tools/vec_short_native_phase2.py:156-327"))
    for name in ("atr_ratio_1h", "relative_volume_1h", "adx_14_4h"):
        add(_metric("SHORT_NATIVE_CONTEXT", name, "SIDE_NEUTRAL",
                    "magnitude/regime threshold; never negate",
                    "tools/vec_short_native_phase2.py:156-327"))
    for name, semantics in (
        ("price_velocity_atr_1h", "(previous-close - close)/ATR; positive means downside"),
        ("price_acceleration_atr_1h", "downside velocity minus prior downside velocity"),
        ("LH_LL_1h", "lower high + lower low + close below prior low"),
        ("failed_ema20_reclaim_1h", "rally touches EMA then closes back below"),
        ("price_vs_ema20/ema50/sma200", "bear continuation requires price below"),
    ):
        add(_metric("SHORT_NATIVE_TRIGGER", name, "SHORT_NATIVE", semantics,
                    "tools/vec_short_native_phase2.py:211-327"))

    # Correction-specific delta route and its contradictory common guard.
    for name, semantics in (
        ("TOP_REJECTION_SHORT", "arms on top; requires negative 1h/5m velocity"),
        ("BOTTOM_BOUNCE_COVER", "cover exhausted downside/bullish bounce"),
    ):
        add(_metric("DELTA_CORRECTION", name, "SHORT_NATIVE", semantics,
                    "wt_dc_delta.py:1081-1160"))
    for name, semantics, risk in (
        ("day_return_pct", "common guard blocks SHORT on strong up day",
         "contradicts patient correction arm"),
        ("rsi_15m/rsi_1h", "common guard blocks SHORT at RSI >= threshold",
         "contradicts overbought correction arm"),
        ("D/4h candle_bias", "requires bear agreement for all SHORTs",
         "reasonable continuation guard, wrong correction policy"),
        ("wt1_D/wt2_D", "blocks SHORT while daily WT bullish",
         "reasonable continuation guard, wrong correction policy"),
    ):
        add(_metric("DISASTER_GUARD", name, "SHORT_NATIVE", semantics,
                    "tradier_manage.py:11580-11790", risk))

    # Variance gates and actual post-entry management.
    add(_metric("POST_ENTRY_VETO", "K_ZONE_SHORT_THRESHOLD_TRADIER",
                "THRESHOLD_COMPLEMENTED", "block when K4h <= short threshold",
                "tradier_manage.py:3840-3852"))
    for name in ("wt_bear_alignment", "wt_composite_short"):
        add(_metric("POST_ENTRY_VETO", name, "SIGN_INVERTED",
                    "read side-specific bearish field; do not negate again",
                    "tradier_manage.py:3870-3881", "double-inversion risk"))
    for name, semantics in (
        ("wt_peak_structure_4h", "SHORT confluence requires LH"),
        ("wt_momentum_state_4h", "SHORT correction confluence accepts EXHAUST_UP"),
        ("wt_divergence_4h", "SHORT confluence accepts BEAR"),
    ):
        add(_metric("POST_ENTRY_VETO", name, "SHORT_NATIVE", semantics,
                    "tradier_manage.py:3853-3869"))
    add(_metric("POST_ENTRY_VETO", "dc_position_1h/dc_position_4h",
                "THRESHOLD_COMPLEMENTED", "SHORT requires >1-threshold",
                "tradier_manage.py:3882-3890"))

    for name, semantics in (
        ("low_water", "ratchets down; never use LONG high-water"),
        ("MFE_ATR", "(entry-low_water)/ATR"),
        ("bull_structural_reclaim", "cover above completed prior high + ATR buffer"),
        ("downside_exhaustion", "low K plus bullish WT"),
        ("volatility_trailing_cover", "cover rebound from low-water after MFE arm"),
        ("emergency_ATR", "cover adverse rise above entry"),
        ("max_hold_hours", "brief correction / bounded continuation liability"),
    ):
        add(_metric("SHORT_NATIVE_COVER", name, "SHORT_NATIVE", semantics,
                    "tools/vec_short_native_phase2.py:360-505"))
    add(_metric("SHORT_NATIVE_COVER", "notional/capacity", "SIDE_NEUTRAL",
                "absolute notional capped at $16k",
                "tools/vec_short_native_phase2.py:340-505"))

    # Ledger/reporting semantics are part of a polarity audit because a correct
    # signal can still appear inverted through the fill/P&L/benchmark layer.
    for name, semantics in (
        ("SHORT_open_fill", "raw*(1-slip)"),
        ("SHORT_cover_fill", "raw*(1+slip)"),
        ("SHORT_pnl", "(average_entry-cover)*quantity"),
        ("SHORT_BH", "(start-end)/start on one fixed $2k unit"),
        ("opportunity_floor", "max(short_BH, cash=0); multiple undefined if <=0"),
    ):
        add(_metric("EXECUTION_REPORTING", name, "SHORT_NATIVE", semantics,
                    "tools/research_fill_contract.py; tools/vec_short_native_phase2.py:360-535"))
    for name in ("commission_bps", "capacity_usd", "account_equity"):
        add(_metric("EXECUTION_REPORTING", name, "SIDE_NEUTRAL",
                    "same magnitude contract; side-aware cash ledger",
                    "tools/vec_short_native_phase2.py:340-535"))
    return m


# Four coherent profiles per book, not a Cartesian field sweep.  Two cover
# styles are included in the profile pair itself, so there are only eight
# evaluations per valid key.
PROFILE_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "CORRECTION": (
        ("C02_STRUCT_IMPULSE", "COV_C_FAST"),
        ("C04_STRUCT_OR_WT_VOL", "COV_C_BAL"),
        ("C06_REJECTION_WICK", "COV_C_FAST"),
        ("C08_ATR_ACCEL_SHOCK", "COV_C_RIDE"),
    ),
    "BEAR": (
        ("B01_FAILED_RECLAIM", "COV_B_FAST"),
        ("B03_STRUCT_CONTINUE", "COV_B_BAL"),
        ("B04_ATR_VOL_BREAK", "COV_B_FAST"),
        ("B07_WT_ACCEL_CONTINUE", "COV_B_RIDE"),
    ),
}


def frozen_profiles(book: str) -> list[tuple[native.EntryProfile, native.CoverProfile]]:
    entries = {x.label: x for x in native.ENTRY_PROFILES}
    covers = {x.label: x for x in native.COVER_PROFILES}
    return [(entries[e], covers[c]) for e, c in PROFILE_PAIRS[book]]


def sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discovery_gate(folds: dict[str, dict[str, Any]]) -> tuple[bool, list[str], float]:
    failures: list[str] = []
    for fold in ("D1", "D2"):
        row = folds[fold]
        if row["insolvent"] or row["minimum_account_equity_usd"] <= 0:
            failures.append(f"{fold}:SOLVENCY")
        if row["peak_post_fill_notional_usd"] > native.CAPACITY_USD + 1e-6:
            failures.append(f"{fold}:CAPACITY")
        if row["technical_exits"] < 2:
            failures.append(f"{fold}:ACTIVITY")
        if row["capital_return_pct"] <= row["opportunity_benchmark_pct"]:
            failures.append(f"{fold}:OPPORTUNITY")
    excess = [
        folds[f]["capital_return_pct"] - folds[f]["opportunity_benchmark_pct"]
        for f in ("D1", "D2")
    ]
    score = float(np.median(excess)) - .35 * max(
        folds[f]["max_drawdown_account_pct"] for f in ("D1", "D2")
    )
    return not failures, failures, score


def _fleet_ingest(root: Path, payload: dict[str, Any], artifact: Path) -> dict[str, Any]:
    db = root / "queue.db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db.with_name(f"queue.db.bak_short_metric_polarity_{stamp}")
    shutil.copy2(db, backup)
    con = sqlite3.connect(db)
    job_ids = {
        path_id: int(job_id)
        for job_id, path_id in con.execute(
            "SELECT id,path_id FROM jobs WHERE path_id IN (?,?)",
            ("ENTRY_DELTA_MTF", "ENTRY_WT_DC"),
        )
    }
    existing = {
        json.loads(raw).get("source_row_sha256")
        for raw, in con.execute("SELECT payload_json FROM results WHERE stage=?", (STAGE,))
    }
    con.close()
    out = root / "short_metric_polarity_ingest" / stamp
    appended = skipped = 0
    for row in payload["results"]:
        source_hash = sha({
            "contract": CONTRACT,
            "symbol": row["symbol"],
            "book": row["book"],
            "selected": row["selected"],
        })
        if source_hash in existing:
            skipped += 1
            continue
        path_id = "ENTRY_DELTA_MTF" if row["book"] == "CORRECTION" else "ENTRY_WT_DC"
        d1, d2 = row["selected"]["folds"]["D1"], row["selected"]["folds"]["D2"]
        result = {
            "job_id": job_ids[path_id],
            "symbol": row["symbol"],
            "side": "SHORT",
            "stage": STAGE,
            "status": row["status"],
            "path_id": path_id,
            "book": row["book"],
            "strategy_return_pct": float(np.median([
                d1["capital_return_pct"], d2["capital_return_pct"]
            ])),
            "bh_return_pct": float(np.median([
                d1["opportunity_benchmark_pct"], d2["opportunity_benchmark_pct"]
            ])),
            "tim_pct": float(np.mean([
                d1["time_in_market_pct"], d2["time_in_market_pct"]
            ])),
            "trades": int(d1["technical_exits"] + d2["technical_exits"]),
            "discovery_only": True,
            "untouched_oos": False,
            "final_evaluated": row["final_evaluated"],
            "exact_replay": False,
            "future_htf_count": int(
                row["selected"]["feature_audit"]["future_htf_sources"]
            ),
            "same_entry_control_return_pct": float(np.median([
                d1["capital_return_pct"], d2["capital_return_pct"]
            ])),
            "matrix_eligible": False,
            "promotion_allowed": False,
            "source_row_sha256": source_hash,
            "artifact": str(artifact),
            "selected_profile": row["selected"]["label"],
            "discovery_failures": row["selected"]["failures"],
        }
        p = out / f"{row['symbol']}_{row['book']}.json"
        fleet.atomic_json(p, result)
        fleet.add_result(root, p)
        appended += 1
    fleet.write_report(root)
    receipt = {
        "backup": str(backup),
        "appended": appended,
        "skipped": skipped,
        "matrix_written": False,
    }
    fleet.atomic_json(out / "summary.json", receipt)
    return receipt


def run(args: argparse.Namespace) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir).resolve() / f"short_metric_polarity_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    cohort = {
        "CORRECTION": [x for x in args.correction_symbols.upper().split(",") if x],
        "BEAR": [x for x in args.bear_symbols.upper().split(",") if x],
    }
    inventory = metric_inventory()
    prereg = {
        "contract": CONTRACT,
        "stage": STAGE,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "cohort": cohort,
        "profile_pairs": PROFILE_PAIRS,
        "folds": {
            "D1": [native.START, "2025-07-01"],
            "D2": ["2025-07-01", native.FINAL_START],
            "FINAL_SEALED": [native.FINAL_START, native.FINAL_END],
        },
        "discovery_gate": (
            "each D1,D2: solvent, <=$16k, >=2 exits, fixed-$2k capital "
            "return > max(fixed-$2k short B&H, cash=0)"
        ),
        "final_policy": "FINAL not simulated unless a discovery-strict candidate exists",
        "exact_policy": "exact replay only after discovery and FINAL both pass",
        "availability_clock": CLOCK_CONTRACT,
        "metric_inventory_sha256": sha(inventory),
        "costs": {"commission_bps": args.commission_bps,
                  "slippage_bps": args.slippage_bps},
    }
    prereg["sha256"] = sha(prereg)
    (out / "PREREGISTRATION.json").write_text(
        json.dumps(prereg, indent=2, sort_keys=True) + "\n"
    )
    (out / "METRIC_INVENTORY.json").write_text(
        json.dumps({"rows": inventory, "sha256": sha(inventory)},
                   indent=2, sort_keys=True) + "\n"
    )

    discovery_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for book, symbols in cohort.items():
        for symbol in symbols:
            data = None
            try:
                data = top._load_execution(
                    symbol, Path(args.npz_dir), native.START, "ladder", native.FINAL_END
                )
                if not data.contract["valid"]:
                    raise RuntimeError(str(data.contract["errors"]))
                htfs = {tf: top._compress_htf(data, tf) for tf in ("1h", "4h", "D")}
                windows = native._windows(data)
                if set(windows) != {"D1", "D2", FINAL_LABEL}:
                    raise RuntimeError(f"requires three folds, got {sorted(windows)}")
                features = native.build_features(data, htfs)
                event_cache: dict[str, tuple[np.ndarray, dict[str, int]]] = {}
                candidates: list[dict[str, Any]] = []
                for entry_profile, cover_profile in frozen_profiles(book):
                    if entry_profile.label not in event_cache:
                        event_cache[entry_profile.label] = native.entry_events(
                            data, htfs, features, entry_profile
                        )
                    event, feature_audit = event_cache[entry_profile.label]
                    folds = {
                        fold: native.simulate(
                            data, features, event, cover_profile, *windows[fold],
                            args.commission_bps, args.slippage_bps,
                        )
                        for fold in ("D1", "D2")
                    }
                    passed, failures, score = discovery_gate(folds)
                    candidates.append({
                        "label": f"{entry_profile.label}__{cover_profile.label}",
                        "entry_profile": dataclasses.asdict(entry_profile),
                        "cover_profile": dataclasses.asdict(cover_profile),
                        "feature_audit": feature_audit,
                        "folds": folds,
                        "passed": passed,
                        "failures": failures,
                        "score": score,
                    })
                candidates.sort(key=lambda x: (not x["passed"], -x["score"], x["label"]))
                selected = candidates[0]
                discovery_rows.append({
                    "symbol": symbol,
                    "side": "SHORT",
                    "book": book,
                    "npz_path": data.path,
                    "npz_sha256": file_sha256(Path(data.path)),
                    "data_contract": data.contract,
                    "candidate_count": len(candidates),
                    "discovery_pass_count": sum(x["passed"] for x in candidates),
                    "selected": selected,
                    "candidates": candidates,
                })
            except Exception as exc:
                errors.append({"symbol": symbol, "book": book, "error": str(exc)})
            finally:
                if data is not None:
                    data.z.close()

    # Seal the entire discovery grid and one selected candidate per key before
    # the first FINAL simulation.  Failed keys never enter the second loop.
    discovery_receipt = {
        "preregistration_sha256": prereg["sha256"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contains_final_metrics": False,
        "rows": discovery_rows,
        "errors": errors,
    }
    discovery_receipt["sha256"] = sha(discovery_receipt)
    (out / "DISCOVERY_GRID.json").write_text(
        json.dumps(discovery_receipt, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    freeze_receipt = {
        "preregistration_sha256": prereg["sha256"],
        "discovery_grid_sha256": discovery_receipt["sha256"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "selected_on_discovery_only": True,
        "rows": [
            {
                "symbol": row["symbol"],
                "book": row["book"],
                "npz_sha256": row["npz_sha256"],
                "selected_label": row["selected"]["label"],
                "selected_sha256": sha(row["selected"]),
                "discovery_pass": row["selected"]["passed"],
            }
            for row in discovery_rows
        ],
    }
    freeze_receipt["sha256"] = sha(freeze_receipt)
    (out / "DISCOVERY_FREEZE.json").write_text(
        json.dumps(freeze_receipt, indent=2, sort_keys=True) + "\n"
    )

    results: list[dict[str, Any]] = []
    for row in discovery_rows:
        selected = row["selected"]
        final = None
        final_pass = False
        # This is the essential seal: only a hash-frozen discovery survivor is
        # allowed to inspect FINAL.
        if selected["passed"]:
            data = None
            try:
                data = top._load_execution(
                    row["symbol"], Path(args.npz_dir), native.START,
                    "ladder", native.FINAL_END,
                )
                if file_sha256(Path(data.path)) != row["npz_sha256"]:
                    raise RuntimeError(f"{row['symbol']}: NPZ changed after discovery freeze")
                htfs = {tf: top._compress_htf(data, tf) for tf in ("1h", "4h", "D")}
                features = native.build_features(data, htfs)
                entry_profile = native.EntryProfile(**selected["entry_profile"])
                cover = native.CoverProfile(**selected["cover_profile"])
                event, final_audit = native.entry_events(
                    data, htfs, features, entry_profile
                )
                if final_audit != selected["feature_audit"]:
                    raise RuntimeError(f"{row['symbol']}: feature audit changed after freeze")
                final = native.simulate(
                    data, features, event, cover, *native._windows(data)[FINAL_LABEL],
                    args.commission_bps, args.slippage_bps, emit_schedule=True,
                )
                final_pass = bool(
                    not final["insolvent"]
                    and final["peak_post_fill_notional_usd"] <= native.CAPACITY_USD + 1e-6
                    and final["technical_exits"] >= 2
                    and final["capital_return_pct"] > final["opportunity_benchmark_pct"]
                )
            finally:
                if data is not None:
                    data.z.close()
        exact_eligible = bool(
            selected["passed"]
            and final_pass
            and selected["feature_audit"]["future_htf_sources"] == 0
        )
        results.append({
            **{k: v for k, v in row.items() if k != "candidates"},
            "discovery_grid_sha256": discovery_receipt["sha256"],
            "discovery_freeze_sha256": freeze_receipt["sha256"],
            "final_evaluated": final is not None,
            "final": final,
            "exact_replay_eligible": exact_eligible,
            "status": (
                "VECTOR_SURVIVOR_EXACT_PENDING"
                if exact_eligible else
                "GRAY_FINAL_REJECTED"
                if final is not None else
                "GRAY_DISCOVERY_REJECTED"
            ),
        })

    payload = {
        "manifest": {
            **prereg,
            "discovery_grid_sha256": discovery_receipt["sha256"],
            "discovery_freeze_sha256": freeze_receipt["sha256"],
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "tier": "VEC_RESEARCH",
            "matrix_eligible": False,
            "promotion_allowed": False,
        },
        "metric_inventory": inventory,
        "results": results,
        "errors": errors,
    }
    (out / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    if args.path_fleet_root:
        payload["fleet_ingest"] = _fleet_ingest(
            Path(args.path_fleet_root).resolve(), payload, out
        )
        (out / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
    print(json.dumps({
        "artifact": str(out),
        "results": len(results),
        "errors": errors,
        "discovery_survivors": sum(r["selected"]["passed"] for r in results),
        "final_evaluated": sum(r["final_evaluated"] for r in results),
        "exact_eligible": sum(r["exact_replay_eligible"] for r in results),
        "fleet": payload.get("fleet_ingest"),
    }, sort_keys=True))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-dir", default=str(top.DEFAULT_NPZ))
    ap.add_argument("--out-dir", default=str(ROOT / "data/reports/vec_research"))
    ap.add_argument("--correction-symbols", default=",".join(CORRECTION_SYMBOLS))
    ap.add_argument("--bear-symbols", default=",".join(BEAR_SYMBOLS))
    ap.add_argument("--commission-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--path-fleet-root")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
