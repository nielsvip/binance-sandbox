#!/usr/bin/env python3
"""Fast Tier-1 exposure-ladder screen for one stock key.

Loads the symbol NPZ once, seeds one initial position, then evaluates a small,
explicitly allowlisted set of vector-parity exit families in-process. Results
are candidates only: winners must be replayed by backtest_v8_engine.py (Tier 2)
before acceptance or promotion.

Unlike the old vec_screen_daemon this does not start a Python process and reload
the same NPZ for every value. A few hundred cells therefore take minutes rather
than hours.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np

SBX = Path(os.environ.get("V8_SBX", "/home/niels/binance-sandbox"))
if not SBX.exists():
    SBX = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SBX))
sys.path.insert(0, str(SBX / "tools"))

import v8_vec_sweep as vec  # noqa: E402

REGISTRY = SBX / "data" / "knob_registry.json"
MANIFEST = SBX / "data" / "param_sweep_manifest_tradier.json"
ACTIVE = SBX / "data" / "hourly_reconfig" / "trb" / "active_config.json"
REPORT = SBX / "data" / "reports" / "VEC_EXPOSURE_LADDER_TRB.jsonl"

# Positive allowlist only. A family absent here gets no number and remains blank.
# These paths are implemented directly in v8_vec_sweep and do not call live-only
# network/broker paths. Expanding this list requires a recorded Tier-1/Tier-2
# parity check, not merely a matching config field name.
SAFE_FAMILIES = {
    "WT_CROSSUNDER_FINAL",
    "DC_LOW4_STOP",
    "DC_LOW_STOP",
    "DC_LOW_FROZEN",
    "BB_FROZEN_STOP",
}


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _apply(cfg: vec.SweepConfig, values: Dict[str, Any]) -> Dict[str, Any]:
    """Apply only real SweepConfig fields and return refused names."""
    fields = {f.name for f in dataclasses.fields(cfg)}
    refused = {}
    for name, value in values.items():
        if name in fields:
            setattr(cfg, name, value)
        else:
            refused[name] = value
    return refused


def _accepted(symbol: str, side: str) -> Dict[str, Any]:
    try:
        row = _load_json(ACTIVE).get(f"{symbol}_{side}", {})
        values = row.get("overrides", {})
        return dict(values) if isinstance(values, dict) else {}
    except Exception:
        return {}


def _exit_off_values(reg: Dict[str, Any]) -> Dict[str, Any]:
    # Use the Tier-2 ladder's audited inventory when available. It includes
    # non-boolean exits (TF selectors and numeric thresholds) that the registry
    # MAIN_SWITCH slice cannot express; omitting them made an "all exits off"
    # vector baseline fire HYBRID_STRUCT_EXIT_D and report only ~5% exposure.
    try:
        import exposure_ladder as tier2_ladder
        return dict(tier2_ladder.all_exits_off())
    except Exception:
        pass
    out = {}
    for name, row in reg.items():
        if row.get("group") != "EXIT" or row.get("role") != "MAIN_SWITCH":
            continue
        if row.get("off_value") is not None:
            out[name] = row["off_value"]
    return out


def _apply_vec_exit_floor(cfg: vec.SweepConfig) -> None:
    """Disable vec-native exit masters absent from the live knob registry.

    SweepConfig intentionally carries experimental defaults that differ from
    Tradier live config (several are True). They are useful for generic sweeps
    but invalid for an all-exits-off floor. This is a test-lane isolation layer,
    never a live configuration recipe.
    """
    tokens = (
        "EXIT", "STOP", "REDUCE", "TAKE_PROFIT", "PROFIT_LOCK", "PPL",
        "SLOWDOWN", "HEDGE", "HOPELESS", "IN_GAIN_TREND",
        "R1_DC_LOW4", "VEL_SLOW_AT_ZERO_GAIN", "ATR_TRAIL",
    )
    for field in dataclasses.fields(cfg):
        name = field.name
        value = getattr(cfg, name)
        if isinstance(value, bool) and any(token in name for token in tokens):
            setattr(cfg, name, False)
    for name in ("EXIT_STRUCT_TF", "LONG_STRUCT_EXIT_TF", "SHORT_STRUCT_EXIT_TF"):
        if hasattr(cfg, name):
            setattr(cfg, name, "None")
    if hasattr(cfg, "E_3_USE_WT_STRUCTURE_EXIT_MODE"):
        cfg.E_3_USE_WT_STRUCTURE_EXIT_MODE = 0
    # The seed represents a fully invested position. Allowing the generic vec
    # engine to keep adding 20 clips changes its weighted entry price and makes
    # the no-exit floor cease to be B&H. Reentries after a real close still OPEN
    # normally; only while-held augmentation is suppressed.
    if hasattr(cfg, "MAX_AUGMENTS_PER_POSITION"):
        cfg.MAX_AUGMENTS_PER_POSITION = 0


def _family_variants(
    base: vec.SweepConfig,
    reg: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Iterable[Tuple[str, str, Any, vec.SweepConfig]]:
    for family in sorted(SAFE_FAMILIES):
        members = sorted(n for n, row in reg.items() if row.get("family") == family)
        masters = [
            n for n in members
            if reg[n].get("role") == "MAIN_SWITCH" and reg[n].get("kind") == "bool"
        ]
        if not masters:
            continue
        family_on = {n: not bool(reg[n].get("off_value", False)) for n in masters}
        # The switch itself is a useful candidate.
        cfg = copy.deepcopy(base)
        if not _apply(cfg, family_on):
            yield family, masters[0], family_on[masters[0]], cfg
        for name in members:
            row = manifest.get(name, {})
            if not row.get("sweepable") or not row.get("test_values"):
                continue
            for value in row["test_values"]:
                values = dict(family_on)
                values[name] = value
                cfg = copy.deepcopy(base)
                if not _apply(cfg, values):
                    yield family, name, value, cfg


def _metrics(events: List[Any], returns: List[float], ts, close, side: str) -> Dict[str, Any]:
    held_from = None
    held_seconds = 0.0
    reasons = Counter()
    real_closes = 0
    for ev in events:
        reasons[ev.reason.split("_px", 1)[0]] += 1
        if ev.type == "OPEN" and held_from is None:
            held_from = float(ev.ts)
        elif ev.type == "CLOSE":
            if held_from is not None:
                held_seconds += max(0.0, float(ev.ts) - held_from)
                held_from = None
            if "MTM_FINAL" not in ev.reason:
                real_closes += 1
    if held_from is not None:
        held_seconds += max(0.0, float(ts[-1]) - held_from)
    span = max(1.0, float(ts[-1]) - float(ts[0]))
    first = float(close[0])
    last = float(close[-1])
    bh_long = ((last - first) / first * 100.0) if first > 0 else 0.0
    return {
        "gain_pct": round(sum(float(x) for x in returns), 8),
        "trades": len(returns),
        "opens": sum(1 for e in events if e.type == "OPEN"),
        "real_closes": real_closes,
        "mtm_count": sum(1 for e in events if "MTM_FINAL" in e.reason),
        "time_in_market_pct": round(100.0 * held_seconds / span, 6),
        "bh_long_pct": round(bh_long, 8),
        "bh_side_pct": round(bh_long if side == "LONG" else -bh_long, 8),
        "reason_counts": dict(reasons.most_common(12)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True, choices=("LONG", "SHORT"))
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--target-min", type=float, default=30.0)
    ap.add_argument("--target-max", type=float, default=50.0)
    ap.add_argument("--output", default=str(REPORT))
    ap.add_argument("--baseline-only", action="store_true",
                    help="run only the seeded all-exits-off parity floor")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    start_ts = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp())
    npz, ts = vec.load_npz(symbol, "tradier", start_ts=start_ts)
    close = np.asarray(npz.get("close"), dtype=float)
    registry = _load_json(REGISTRY).get("tradier", {})
    manifest = _load_json(MANIFEST).get("params", {})

    base = vec.SweepConfig()
    accepted_refused = _apply(base, _accepted(symbol, args.side))
    _apply(base, _exit_off_values(registry))
    _apply_vec_exit_floor(base)

    variants: List[Tuple[str, str, Any, vec.SweepConfig]] = [
        ("BASELINE", "__ALL_EXITS_OFF__", False, copy.deepcopy(base))
    ]
    if not args.baseline_only:
        variants.extend(_family_variants(base, registry, manifest))
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    rows = []
    for family, knob, value, cfg in variants:
        events, returns, n_bars = vec.simulate_one_symbol(
            symbol, args.side, "tradier", cfg,
            _npz_cache=(npz, ts), seed_position_at_start=True,
        )
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tier": "VEC_CANDIDATE",
            "campaign": "stocks_baseline_v2_s4h__ladder_vec_v1",
            "symbol": symbol,
            "side": args.side,
            "family": family,
            "knob": knob,
            "value": value,
            "bars": n_bars,
            "accepted_refused": accepted_refused,
            **_metrics(events, returns, ts, close, args.side),
        }
        row["in_target_exposure"] = (
            args.target_min <= row["time_in_market_pct"] <= args.target_max
        )
        rows.append(row)

    with out_path.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    ranked = sorted(
        rows,
        key=lambda r: (r["in_target_exposure"], r["gain_pct"], -r["trades"]),
        reverse=True,
    )
    print(json.dumps({
        "symbol": symbol,
        "side": args.side,
        "variants": len(rows),
        "seconds": round(time.monotonic() - started, 3),
        "npz_loads": 1,
        "output": str(out_path),
        "top_candidates": ranked[:10],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
