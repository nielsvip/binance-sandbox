#!/usr/bin/env python3
"""Claimable vector-first research queue for stock ENTRY/EXIT path families.

This is an orchestration and evidence-contract tool.  It never writes live
configuration, symbol universes, PARAM_BASELINE_STOCKS, or SWITCH_MATRIX_TRB.

The campaign unit is one logical path family over a frozen universe cohort:

* top-N recent performers are tested as LONG;
* bottom-N recent performers are tested as SHORT;
* LONG and SHORT rows, accounting, controls, and results are never pooled;
* completed HTF bars/no-lookahead and next-RTH fills are mandatory;
* every exit is compared with both B&H and the strongest frozen result using
  the exact same entry schedule;
* vector screens precede exact backtest_v8 replay;
* no path is promotable until an untouched OOS fold and exact replay pass.

Jobs whose current vector runner cannot honor the frozen ladder/same-entry
control are deliberately ADAPTER_REQUIRED, not silently run with an always-in
position or a different entry schedule.
"""
from __future__ import annotations

import argparse
import ast
import csv
import dataclasses
import hashlib
import json
import os
import socket
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data" / "reports" / "path_fleet"
DEFAULT_NPZ = ROOT / "backtest_v8" / "indicators"


@dataclass(frozen=True)
class PathFamily:
    path_id: str
    kind: str
    priority: int
    description: str
    settings: dict[str, list[Any]]
    fixed_entry_control: str
    fixed_exit_control: str
    runner: str | None
    adapter_status: str
    notes: str = ""
    config_keys: tuple[str, ...] = ()
    source_rows: tuple[str, ...] = ()


CORE_PATHS: tuple[PathFamily, ...] = (
    PathFamily(
        "ENTRY_LADDER_GREEN",
        "ENTRY",
        1,
        "Completed-TF bullish/bearish WT arrow requests a regression-band size; "
        "D/4h/1h requests are capacity-clipped and use a mandatory reclaim after exits.",
        {
            "trigger": ["green"],
            "curve_mode": ["linear", "center_plateau"],
            "semantics": ["target", "add"],
            "D_bottom_top": ["3..8 / 1..6"],
            "4h_bottom_top": ["2..6 / 1..4"],
            "1h_bottom_top": ["1..4 / 0.5..2"],
            "hard_capacity_x": [8],
        },
        "B&H $2k plus frozen accepted ladder curve",
        "E02 4h Donchian N=30 + zero-buffer resting reclaim",
        "tools/vec_band_ladder_walkforward.py",
        "READY_BOTH_SIDES",
        "LONG and SHORT run isolated causal ledgers; exact replay remains mandatory before promotion.",
    ),
    PathFamily(
        "ENTRY_STOCH_HHHL",
        "ENTRY",
        2,
        "Completed-bar HH+HL with low and rising StochRSI for LONG; mirror LH+LL "
        "with high and falling StochRSI for SHORT. Uses the band ladder only for sizing.",
        {
            "stoch_threshold": [15, 20, 25, 30, 35, 40],
            "timeframes": ["1h", "4h", "D"],
            "min_confirming_tfs": [1, 2],
            "trigger": ["structure", "union-with-green"],
        },
        "same frozen ladder curve and capacity",
        "E02 N=30 + resting reclaim",
        "tools/vec_band_ladder_walkforward.py",
        "READY_BOTH_SIDES",
        "Causal direct/union-with-green adapter freezes the accepted ladder "
        "control; LONG and SHORT mirrors are screened independently.",
    ),
    PathFamily(
        "ENTRY_WT_DC",
        "ENTRY",
        1,
        "WaveTrend direction/cross combined with Donchian location or break. This path "
        "previously performed well; sweep aliases must reach TRA_WT_DC_ENTRY_THRESHOLD.",
        {
            "threshold": [35, 45, 55, 65, 75, 85],
            "timeframes": ["15m", "1h", "4h", "D"],
            "min_confirming_tfs": [1, 2, 3],
            "dc_zone": [0.10, 0.20, 0.35, 0.50],
            "cross_age_bars": [1, 2, 4, 8],
        },
        "same frozen ladder sizing; no other entry signals",
        "E02 N=30 + resting reclaim",
        "tools/vec_entry_overlay_walkforward.py",
        "READY_BOTH_SIDES",
        "Numeric-cross/router/switch repair complete. Causal overlay is screened "
        "against frozen ladder+E02; exact replay remains mandatory for survivors.",
    ),
    PathFamily(
        "ENTRY_GOLDEN_RULE",
        "ENTRY",
        3,
        "Multi-timeframe DC/BB break/retest vote. Test it as a direct entry and as "
        "an entry filter; timeframe weights must not be collapsed into an opaque sum.",
        {
            "min_tfs": [1, 2, 3],
            "min_indicators_per_tf": [1, 2],
            "weights_15m_1h_4h_D": ["1/1/1/1", "0.5/1/2/3", "0/1/2/4"],
            "score": [4, 6, 8, 10, 12, 15.5],
            "role": ["direct-entry", "entry-filter"],
        },
        "same ladder sizing and capacity",
        "E02 N=30 + resting reclaim",
        "tools/vec_entry_overlay_walkforward.py",
        "READY_BOTH_SIDES",
    ),
    PathFamily(
        "ENTRY_BB_RECOVERY",
        "ENTRY",
        7,
        "Failed Bollinger breakout/breakdown followed by recovery into the band.",
        {
            "timeframes": ["15m", "1h", "4h"],
            "recovery_bars": [1, 2, 4, 8],
            "min_band_excursion_atr": [0.0, 0.25, 0.5, 1.0],
        },
        "same ladder sizing and capacity",
        "E02 N=30 + resting reclaim",
        "tools/vec_entry_overlay_walkforward.py",
        "READY_BOTH_SIDES",
        "Sweep-only path remains default-off in live code. The causal adapter "
        "tests completed 15m/1h/4h failed breaks without changing live config.",
    ),
    PathFamily(
        "ENTRY_DELTA_MTF",
        "ENTRY",
        8,
        "Delta/momentum entry requiring a declared number of favorable completed TFs.",
        {
            "min_favorable_tfs": [1, 2, 3, 4],
            "decay_ratio": [0.25, 0.5, 0.75],
            "structural_gate": [False, True],
        },
        "same ladder sizing and capacity",
        "E02 N=30 + resting reclaim",
        "tools/vec_entry_overlay_walkforward.py",
        "READY_BOTH_SIDES",
        "Actual delta entry is direct-only. Requested decay ratios are reported "
        "as research directional-retention filters because live "
        "DELTA_EXIT_DECAY_RATIO is exit-only.",
    ),
    PathFamily(
        "ENTRY_DC_BREAK_ENTRY_ENABLED",
        "ENTRY",
        20,
        "Disconnected legacy registry path. The named DC_BREAK_ENTRY_ENABLED "
        "switch is absent. A research reconstruction combines the prior-channel "
        "break semantics from the disabled swing branch and the separately "
        "controlled StockDaytradeWing, without changing live configuration.",
        {
            "timeframe": ["5m", "15m", "1h", "4h"],
            "buffer_fraction": [0.0, 0.0005, 0.001, 0.002],
            "require_1h_expansion": [False, True],
            "confirmation": ["none", "not-exhausted", "directional-stoch"],
            "role": ["direct", "union-with-green"],
            "live_switch_status": ["DISCONNECTED"],
        },
        "same frozen ladder sizing and $16k capacity",
        "E02 N=30 + resting reclaim",
        "tools/vec_entry_overlay_walkforward.py",
        "READY_BOTH_SIDES_RESEARCH_RECONSTRUCTION",
        "Never infer a live setting from this screen. evaluate_open is fail-closed "
        "by DC_BREAK_ENTRY_DISABLED=True; StockDaytradeWing instead reads "
        "DC_DAYTRADE_ENABLED/TRADIER_DC_DAYTRADE_ENABLED.",
    ),
    PathFamily(
        "ENTRY_DC_TIER_AUG_ENABLED",
        "ENTRY",
        20,
        "Phantom inventory switch over an active augment block. "
        "DC_TIER_AUG_ENABLED is absent and unread, while evaluate_augment "
        "unconditionally applies profit-gated 5m/15m/1h/4h Donchian tier targets.",
        {
            "min_gain_pct": [1.0, 3.0, 5.0],
            "buffer_fraction": [0.0, 0.001, 0.002],
            "tier_profile": ["1/2/3/5", "1/1.5/2.5/4", "1/2/4/8"],
            "target_fill_ratio": [0.5, 0.75, 0.9],
            "tier4_maturity_atr": ["off", 0.7, 0.5],
            "inventory_switch_status": ["PHANTOM_ABSENT_AND_UNREAD"],
        },
        "same frozen ladder entry schedule, sizing, and $16k capacity",
        "E02 N=30 + resting reclaim",
        "tools/vec_dc_tier_augment_walkforward.py",
        "READY_BOTH_SIDES_ACTIVE_FUNCTION_PHANTOM_SWITCH",
        "The source setting is gain 3%, buffer .1%, targets 1/2/3/5x, "
        "75% fill gate, maturity guard off. Research extensions are labeled; "
        "no switch-enable claim or live config promotion is allowed.",
    ),
    PathFamily(
        "ENTRY_AUGMENT_TREND_RESUME_ENABLED",
        "ENTRY",
        20,
        "Research reconstruction of the removed stock trend-resume augment: "
        "while an existing position is profitable, add on a side-favorable "
        "5m Donchian-basis, Stoch, and RSI continuation state.",
        {
            "min_gain_pct": [0.5, 1.0, 2.0, 3.0],
            "rsi_boundary_long_short": ["60/40", "70/30", "80/20", "100/0"],
            "add_start_position_mult": [0.25, 0.5],
        },
        "same frozen ladder entry schedule, sizing, and capacity",
        "E02 N=30 + resting reclaim",
        "tools/vec_augment_trend_resume_walkforward.py",
        "READY_BOTH_SIDES_RESEARCH_RECONSTRUCTION",
        "Disconnected from active tradier_manage/config. Last real source is "
        "backups/before_desktop_tradier_fixes_20260721.py; no live setting may "
        "be inferred or promoted until the path is deliberately rewired.",
    ),
    PathFamily(
        "EXIT_E02_DONCHIAN",
        "EXIT",
        1,
        "Exit LONG only after completed HTF close below the prior Donchian low; "
        "SHORT mirrors above prior high. Reentry is lower ladder or resting reclaim.",
        {
            "timeframe": ["1h", "4h", "D"],
            "lookback": [10, 15, 20, 30, 40, 55, 80],
            "profit_gate_pct": [0.0, 0.25, 0.5, 1.0],
        },
        "frozen accepted ladder event/fill schedule",
        "E02 4h N=30",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Same-entry adapter freezes the selected ladder request hashes before "
        "sweeping completed 1h/4h/D exits; dc_low4_5m is excluded.",
    ),
    PathFamily(
        "EXIT_STRUCTURAL_WT_LOWER_TOP",
        "EXIT",
        2,
        "After a completed structural lower low (higher high for SHORT), wait for a "
        "lower price top and WT1 rebound/rollover top; do not exit on the first DC4 break.",
        {
            "arm_timeframe": ["1h", "4h"],
            "retest_timeframe": ["15m", "1h"],
            "wt_rebound_min": [0.5, 1.0, 2.0, 4.0],
            "pivot_lookback": [3, 4, 6, 10],
            "max_wait_hours": [12, 20, 30, 48],
            "profit_gate_pct": [0.25, 0.5, 1.0],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Parity-gated compiled adapter freezes the accepted ladder schedule, "
        "uses completed HTF bars, and mirrors structural states by side; "
        "dc_low4_5m is excluded.",
    ),
    PathFamily(
        "BOTTOM_A_PROTECTIVE_TRAIL",
        "EXIT",
        2,
        "Completed adverse structure arms an immediate diagnostic or a later "
        "monotonic ATR, rolling-stdev, or Donchian protective trail.",
        {
            "arm_timeframe": ["1h", "4h"],
            "trail_timeframe": ["5m", "15m", "1h"],
            "mode": ["IMMEDIATE_DIAGNOSTIC", "ATR", "STDEV", "DC"],
            "break_buffer_atr": [0.0, 0.25],
            "atr_mult": [1.5, 2.0, 3.0, 4.0],
            "stdev_mult": [1.5, 2.0, 2.5, 3.0],
            "lookback": [4, 10, 20, 40],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "The immediate break is retained only as a churn diagnostic. 5m "
        "native/interpolated provenance is preserved; losses are not hidden "
        "behind a zero-profit gate.",
    ),
    PathFamily(
        "BOTTOM_B_DELAYED_LOWER_TOP",
        "EXIT",
        2,
        "Arm on a completed lower-low/adverse break, do not sell at the break, "
        "then exit at the next confirmed lower price/WT1 top (SHORT mirrored).",
        {
            "arm_timeframe": ["1h", "4h"],
            "confirm_timeframe": ["5m", "15m", "1h"],
            "confirmation_mode": ["PRICE_ONLY", "WT_ONLY", "AND", "OR"],
            "confirmation_bars": [1, 2],
            "rebound_atr": [0.25, 0.5, 1.0],
            "prebreak_lookback": [4, 6],
            "max_wait_hours": [12, 24, 48],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Compiled exact-contract screen with Python state-machine parity oracle. "
        "No dc_low4_5m profit exit.",
    ),
    PathFamily(
        "BOTTOM_C_DELAYED_EMERGENCY",
        "EXIT",
        2,
        "Use the delayed lower-top state, but add a separately counted rare "
        "emergency close when recovery never arrives.",
        {
            "arm_timeframe": ["1h", "4h"],
            "confirm_timeframe": ["5m", "15m", "1h"],
            "confirmation_mode": ["AND", "OR"],
            "max_wait_hours": [12, 24, 48],
            "emergency_adverse_atr": [2.0, 3.0, 4.0],
            "emergency_adverse_stdev": [2.5, 3.5, 5.0],
            "continued_adverse_bars": [3, 5],
            "max_emergency_exit_share": [0.25],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Normal and emergency fills are reported separately. A routine "
        "emergency path cannot survive the vector gate.",
    ),
    PathFamily(
        "EXIT_WT_MTF",
        "EXIT",
        4,
        "Exit at WaveTrend exhaustion/cross against the held side, optionally requiring "
        "multiple completed timeframes and a price/structure confirmation.",
        {
            "timeframes": ["15m", "1h", "4h", "D", "W"],
            "min_against_tfs": [1, 2, 3, 4],
            "extreme": [45, 55, 65, 75],
            "velocity": [0.0, 0.25, 0.5, 1.0],
            "profit_gate_pct": [0.0, 0.25, 0.5, 1.0],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Completed 15m/1h/4h/D/W WT exhaustion is vectorized and frozen "
        "chronologically; actual exits are mandatory and MTM-only rows remain gray.",
    ),
    PathFamily(
        "EXIT_GR_OPPOSITE",
        "EXIT",
        5,
        "Exit on opposite Golden Rule HTF votes. Timeframe votes and weights are "
        "reported separately so LONG/SHORT direction cannot be inverted or pooled.",
        {
            "min_tfs": [1, 2, 3],
            "min_indicators": [1, 2],
            "score": [4, 6, 8, 10, 12, 15.5],
            "weights_15m_1h_4h_D": ["1/1/1/1", "0.5/1/2/3", "0/1/2/4"],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Completed 15m/1h/4h/D opposite votes are retained per timeframe "
        "with explicit weights and a raw-vote score; actual exits are mandatory.",
    ),
    PathFamily(
        "EXIT_PARTIAL_RUNNER",
        "EXIT",
        3,
        "Take a bounded partial clip at a fast top, retain a runner for the slow "
        "exit, and give every exited clip its own lower/reclaim reentry obligation.",
        {
            "first_clip_fraction": [0.15, 0.25, 0.33, 0.5],
            "second_clip_fraction": [0.0, 0.15, 0.25, 0.33],
            "fast_family": ["E05", "E06", "WT"],
            "slow_family": ["E01", "E02"],
            "regime_switch": [False, True],
        },
        "exact frozen accepted ladder schedule",
        "same-entry full E02 N=30 control",
        "tools/vec_same_entry_partial_adapter.py",
        "READY_BOTH_SIDES",
        "Compiled same-entry adapter consumes the frozen ladder schedule, "
        "reports realized clip P&L, and keeps each clip reclaim obligation "
        "persistent until filled.",
    ),
    PathFamily(
        "EXIT_E01_CHANDELIER",
        "EXIT",
        6,
        "Monotonic ATR Chandelier stop on completed 4h or daily bars.",
        {
            "timeframe": ["4h", "D"],
            "lookback": [10, 20, 30, 55],
            "atr_mult": [1.5, 2.0, 2.5, 3.0, 4.0],
            "profit_gate_pct": [0.0, 0.25, 0.5, 1.0],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Standard from-entry completed-4h/D monotonic Chandelier. The later "
        "structural-arm adaptation remains a separate backlog experiment.",
    ),
    PathFamily(
        "EXIT_E05_DIVERGENCE_RETEST",
        "EXIT",
        7,
        "Confirmed RSI/price divergence, structural break, then rebound/rollover exit.",
        {
            "pivot_radius": [2, 3, 5],
            "divergence_min": [3, 5, 8],
            "break_buffer_atr": [0.0, 0.25],
            "rebound_atr": [0.25, 0.5],
            "max_wait_bars": [8, 12, 20],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_e05_adapter.py",
        "READY_BOTH_SIDES",
        "Research-only; no equivalent named live state machine. The adapter "
        "uses the _e05_candidates grid and requires divergence, then a later "
        "break, rebound, and rollover—never a first-break exit.",
    ),
    PathFamily(
        "EXIT_E06_REGRESSION_RETEST",
        "EXIT",
        8,
        "Regression extreme arms an exit; reversion/retest confirms it before close.",
        {
            "lookback": [40, 60, 100, 150, 250],
            "arm_z": [1.5, 2.0, 2.5, 3.0],
            "exit_z": [0.75, 1.0, 1.5, 2.0],
            "corr_gate": [0.5, 0.7, 0.85],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_e06_adapter.py",
        "READY_BOTH_SIDES",
        "Same-entry research adapter is connected, but the registered "
        "`rebound_atr` name is stale: active code consumes corr_gate and has "
        "no live Tradier config key or separate post-break retest state.",
        "Research-only prior-log-regression path. exit_z arms with exit_z < arm_z.",
    ),
    PathFamily(
        "EXIT_MTF_ATR_TRAIL",
        "EXIT",
        9,
        "Profit-aware ATR trail compounded across completed timeframes.",
        {
            "timeframes": ["1h", "4h", "D"],
            "atr_mult": [1.5, 2.0, 2.5, 3.0, 4.0],
            "min_profit_pct": [0.0, 0.25, 0.5, 1.0],
            "min_confirming_tfs": [1, 2],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        "tools/vec_same_entry_exit_adapter.py",
        "READY_BOTH_SIDES",
        "Research-only completed 1h/4h/D agreement path. It does not replace "
        "the canonical live/v8 single-TF ratchet, which is currently disabled "
        "for stocks after 5m near-entry churn. Compare min_confirming_tfs=2 "
        "against its exact min_confirming_tfs=1 pair and separately against "
        "Bottom-A's adverse-break-armed single-TF trail.",
    ),
    PathFamily(
        "EXIT_PEAK_GIVEBACK",
        "EXIT",
        10,
        "Exit or reduce after giving back a declared fraction of MFE, gated above cost.",
        {
            "arm_gain_pct": [0.5, 1.0, 2.0, 4.0, 8.0],
            "giveback_fraction": [0.20, 0.33, 0.50, 0.67],
            "reduce_fraction": [0.25, 0.5, 1.0],
        },
        "exact frozen accepted ladder schedule",
        "same-entry E02 N=30 control",
        None,
        "ADAPTER_REQUIRED",
    ),
)


def _slug(value: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in value.upper())
    return "_".join(part for part in clean.split("_") if part)[:72]


INVENTORY_CORE_MAP = {
    ("ENTRY", "WT_DC_ENTRY_ENABLED"): "ENTRY_WT_DC",
    ("ENTRY", "BB_RECOVERY_ENABLED"): "ENTRY_BB_RECOVERY",
    ("ENTRY", "DC_BREAK_ENTRY_ENABLED"): "ENTRY_DC_BREAK_ENTRY_ENABLED",
    ("ENTRY", "DC_TIER_AUG_ENABLED"): "ENTRY_DC_TIER_AUG_ENABLED",
    (
        "ENTRY",
        "AUGMENT_TREND_RESUME_ENABLED",
    ): "ENTRY_AUGMENT_TREND_RESUME_ENABLED",
    ("EXIT", "WT_HTF_EXIT_ENABLED"): "EXIT_WT_MTF",
    ("EXIT", "WT_CROSSUNDER_EXIT_ENABLED"): "EXIT_WT_MTF",
    ("EXIT", "PEAK_GIVEBACK_ENABLED"): "EXIT_PEAK_GIVEBACK",
}


def _inventory_paths() -> tuple[PathFamily, ...]:
    """Merge every authoritative Tradier inventory row into logical cohorts.

    Grouping key is the actual config key plus ENTRY/EXIT.  Multiple reason
    strings controlled by one switch are true aliases and share a job.  Rows
    with different switches never disappear into a broad thematic family.
    """
    source = ROOT / "tools" / "generate_path_inventory.py"
    tree = ast.parse(source.read_text(), filename=str(source))
    tables: dict[str, list[tuple[Any, ...]]] = {}
    wanted = {"TRADIER_ENTRY_PATHS", "TRADIER_EXIT_PATHS"}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        name = next(iter(names & wanted), None)
        if name:
            tables[name] = ast.literal_eval(node.value)
    if set(tables) != wanted:
        raise RuntimeError(f"authoritative inventory tables missing from {source}")
    TRADIER_ENTRY_PATHS = tables["TRADIER_ENTRY_PATHS"]
    TRADIER_EXIT_PATHS = tables["TRADIER_EXIT_PATHS"]

    rows_by_key: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
    for kind, rows in (
        ("ENTRY", TRADIER_ENTRY_PATHS),
        ("EXIT", TRADIER_EXIT_PATHS),
    ):
        for row in rows:
            reason, event_type, source, line, function, config, default, desc, category = row
            key = str(config).strip()
            if key in ("", "—"):
                key = f"NO_CONFIG::{reason}"
            rows_by_key.setdefault((kind, key), []).append(row)

    paths: dict[str, PathFamily] = {p.path_id: p for p in CORE_PATHS}
    for (kind, config), rows in rows_by_key.items():
        primary_config = config.split(" / ")[0].strip()
        mapped = INVENTORY_CORE_MAP.get((kind, primary_config))
        source_rows = tuple(
            (
                f"{row[0]} | event={row[1]} | source={row[2]}:{row[3]} | "
                f"function={row[4]} | config={row[5]} | default={row[6]} | "
                f"category={row[8]}"
            )
            for row in rows
        )
        config_keys = tuple(
            dict.fromkeys(
                part.strip()
                for part in config.split(" / ")
                if part.strip() and not part.startswith("NO_CONFIG::")
            )
        )
        if mapped and mapped in paths:
            old = paths[mapped]
            paths[mapped] = dataclasses.replace(
                old,
                config_keys=tuple(dict.fromkeys(old.config_keys + config_keys)),
                source_rows=old.source_rows + source_rows,
            )
            continue

        path_id = f"{kind}_{_slug(config if not config.startswith('NO_CONFIG::') else rows[0][0])}"
        categories = {str(row[8]).upper() for row in rows}
        defaults = {str(row[6]).upper() for row in rows}
        event_types = sorted({str(row[1]) for row in rows})
        disabled = any(
            token in " ".join(categories | defaults)
            for token in ("DEAD", "DISABLED", "BLOCKED")
        )
        non_strategy = any(
            token in " ".join(categories)
            for token in ("BROKER", "GHOST", "PHYSICS")
        )
        adapter = (
            "QUARANTINED_DISABLED"
            if disabled
            else "NON_STRATEGY_OBSERVABILITY"
            if non_strategy
            else "ADAPTER_REQUIRED"
        )
        descriptions = " / ".join(dict.fromkeys(str(row[7]) for row in rows))
        paths[path_id] = PathFamily(
            path_id=path_id,
            kind=kind,
            priority=50 if disabled or non_strategy else 20,
            description=descriptions,
            settings={
                "config_default": list(dict.fromkeys(str(row[6]) for row in rows)),
                "event_types": event_types,
                "range_status": [
                    "EXTRACT_FROM_CONFIG_AND_FUNCTION_BEFORE_CLAIM"
                    if not disabled
                    else "QUARANTINED_NO_SWEEP"
                ],
            },
            fixed_entry_control=(
                "same frozen ladder sizing and capacity"
                if kind == "ENTRY"
                else "exact frozen accepted ladder schedule"
            ),
            fixed_exit_control=(
                "E02 N=30 + resting reclaim"
                if kind == "ENTRY"
                else "same-entry E02 N=30 control"
            ),
            runner=None,
            adapter_status=adapter,
            notes=(
                "Authoritative inventory cohort. A worker must record explicit "
                "numeric ranges from the named function/config before claiming."
            ),
            config_keys=config_keys,
            source_rows=source_rows,
        )

    # Coverage is fail-closed: the generated registry must retain all 64
    # authoritative source rows even when aliases share a switch/job.
    retained = sum(len(p.source_rows) for p in paths.values())
    expected = len(TRADIER_ENTRY_PATHS) + len(TRADIER_EXIT_PATHS)
    if retained != expected:
        raise RuntimeError(
            f"Tradier path inventory coverage mismatch: retained={retained} expected={expected}"
        )
    return tuple(sorted(paths.values(), key=lambda p: (p.priority, p.kind, p.path_id)))


PATHS: tuple[PathFamily, ...] = _inventory_paths()


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL,
    universe_json TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    claimed_by TEXT,
    claimed_at REAL,
    heartbeat_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result_path TEXT,
    message TEXT,
    UNIQUE(path_id)
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    strategy_return_pct REAL,
    bh_return_pct REAL,
    same_entry_control_return_pct REAL,
    alpha_vs_bh_pp REAL,
    alpha_vs_control_pp REAL,
    tim_pct REAL,
    trades INTEGER,
    untouched_oos INTEGER NOT NULL DEFAULT 0,
    exact_replay INTEGER NOT NULL DEFAULT 0,
    future_htf_count INTEGER,
    artifact TEXT,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _close_key(z: Any) -> str:
    for key in ("adj_close", "adjusted_close", "close", "close_5m", "close_3m"):
        if key in z.files:
            return key
    raise KeyError("no close/close_5m/close_3m")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_symbol_file(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return set()
    if isinstance(payload, dict):
        payload = payload.get("symbols", payload.get("items", []))
    return {str(value).upper() for value in payload if isinstance(value, str)}


def _split_artifact(close: np.ndarray, ts: np.ndarray, idx: np.ndarray) -> dict[str, Any]:
    """Fail-closed raw-close corporate-action screen.

    Adjusted-close provenance is preferable.  When absent, a near-integer
    2/3/4/5/10x discontinuity or any adjacent >80% move is quarantined.  This is
    deliberately conservative: a human can clear a real news gap, but an
    unadjusted split may not select the research universe.
    """
    if len(idx) < 2:
        return {"valid": False, "reason": "TOO_FEW_ROWS"}
    values = close[idx]
    ratios = values[1:] / values[:-1]
    finite = np.isfinite(ratios) & (ratios > 0)
    ratios = ratios[finite]
    if not len(ratios):
        return {"valid": False, "reason": "NO_FINITE_RATIOS"}
    max_jump = float(np.max(np.maximum(ratios, 1.0 / ratios)))
    suspects: list[float] = []
    for ratio in ratios:
        magnitude = max(float(ratio), 1.0 / float(ratio))
        near_integer = any(abs(magnitude / split - 1.0) <= 0.03 for split in (2, 3, 4, 5, 10))
        if near_integer or magnitude >= 1.80:
            suspects.append(float(ratio))
    return {
        "valid": not suspects,
        "reason": "PASS" if not suspects else "RAW_CLOSE_SPLIT_OR_EXTREME_GAP",
        "max_adjacent_ratio_magnitude": max_jump,
        "suspect_ratios": suspects[:10],
    }


def rank_universe(
    npz_dir: Path,
    lookback_days: int,
    top_n: int,
    bottom_n: int,
    *,
    max_data_age_days: int = 14,
    min_history_days: int = 60,
    tradeable_long: set[str] | None = None,
    tradeable_short: set[str] | None = None,
    universe_root: Path = ROOT,
) -> dict[str, Any]:
    end_cutoff = int(time.time())
    start_cutoff = end_cutoff - lookback_days * 86400
    long_path = universe_root / "symbols_trb_long.json"
    short_path = universe_root / "symbols_trb_short.json"
    if tradeable_long is None:
        tradeable_long = _load_symbol_file(long_path)
    if tradeable_short is None:
        tradeable_short = _load_symbol_file(short_path)
    if not tradeable_long or not tradeable_short:
        raise RuntimeError("point-in-time symbols_trb_long/short universe is empty")
    allowed = tradeable_long | tradeable_short
    rows: list[dict[str, Any]] = []
    off_universe: list[str] = []
    for path in sorted(npz_dir.glob("*.npz")):
        symbol = path.stem.upper()
        # Stocks have no USDC/USDT suffix. Index/ETF symbols are retained if
        # present because they are valid Tradier controls.
        if symbol.endswith(("USDC", "USDT")):
            continue
        if symbol not in allowed:
            off_universe.append(symbol)
            continue
        try:
            with np.load(path, allow_pickle=False) as z:
                ts = np.asarray(z["timestamps"], dtype=np.int64)
                price_field = _close_key(z)
                close = np.asarray(z[price_field], dtype=np.float64)
                valid = np.isfinite(close) & (close > 0) & (ts <= end_cutoff)
                idx = np.flatnonzero(valid)
                if len(idx) < 2:
                    continue
                right = int(idx[-1])
                age_days = (end_cutoff - int(ts[right])) / 86400.0
                if age_days > max_data_age_days:
                    rows.append(
                        {
                            "symbol": symbol,
                            "error": f"STALE_NPZ:{age_days:.1f}d>{max_data_age_days}d",
                        }
                    )
                    continue
                candidates = idx[ts[idx] >= start_cutoff]
                left = int(candidates[0]) if len(candidates) else int(idx[0])
                if right <= left:
                    continue
                observed_days = (int(ts[right]) - int(ts[left])) / 86400.0
                if observed_days < min_history_days:
                    rows.append(
                        {
                            "symbol": symbol,
                            "error": (
                                f"INSUFFICIENT_HISTORY:{observed_days:.1f}d"
                                f"<{min_history_days}d"
                            ),
                        }
                    )
                    continue
                corporate_action = (
                    {"valid": True, "reason": "ADJUSTED_CLOSE_PROVENANCE"}
                    if price_field in ("adj_close", "adjusted_close")
                    else _split_artifact(close, ts, idx[(idx >= left) & (idx <= right)])
                )
                if not corporate_action["valid"]:
                    rows.append(
                        {
                            "symbol": symbol,
                            "error": corporate_action["reason"],
                            "corporate_action_check": corporate_action,
                        }
                    )
                    continue
                ret = 100.0 * (float(close[right]) / float(close[left]) - 1.0)
                rows.append(
                    {
                        "symbol": symbol,
                        "return_pct": ret,
                        "start_ts": int(ts[left]),
                        "end_ts": int(ts[right]),
                        "rows": right - left + 1,
                        "npz": str(path),
                        "price_field": price_field,
                        "corporate_action_check": corporate_action,
                    }
                )
        except Exception as exc:
            rows.append({"symbol": symbol, "error": f"{type(exc).__name__}:{exc}"})
    ranked = sorted((r for r in rows if "return_pct" in r), key=lambda x: x["return_pct"])
    if not ranked:
        raise RuntimeError(f"no rankable stock NPZs in {npz_dir}")
    top_ranked = [r for r in ranked if r["symbol"] in tradeable_long]
    bottom_ranked = [r for r in ranked if r["symbol"] in tradeable_short]
    top = list(reversed(top_ranked[-top_n:]))
    bottom = bottom_ranked[:bottom_n]
    if len(top) < top_n or len(bottom) < bottom_n:
        raise RuntimeError(
            f"insufficient valid tradeable cohort: top_long={len(top)}/{top_n} "
            f"bottom_short={len(bottom)}/{bottom_n}"
        )
    return {
        "created_utc": utc_now(),
        "method": "recent close-to-close B&H ranking from source NPZ",
        "lookback_days_requested": lookback_days,
        "max_data_age_days": max_data_age_days,
        "min_history_days": min_history_days,
        "tradeable_snapshot": {
            "long_path": str(long_path),
            "short_path": str(short_path),
            "long_sha256": _sha256(long_path) if long_path.exists() else None,
            "short_sha256": _sha256(short_path) if short_path.exists() else None,
            "long_count": len(tradeable_long),
            "short_count": len(tradeable_short),
        },
        "top_long": [{**r, "side": "LONG"} for r in top],
        "bottom_short": [{**r, "side": "SHORT"} for r in bottom],
        "errors": [r for r in rows if "error" in r],
        "off_universe_npz_symbols_diagnostic": off_universe,
    }


def contract_for(path: PathFamily) -> dict[str, Any]:
    return {
        "path_id": path.path_id,
        "kind": path.kind,
        "description": path.description,
        "settings": path.settings,
        "config_keys": path.config_keys,
        "source_rows": path.source_rows,
        "runner": path.runner,
        "adapter_status": path.adapter_status,
        "fixed_entry_control": path.fixed_entry_control,
        "fixed_exit_control": path.fixed_exit_control,
        "side_isolation": "LONG and SHORT are separate jobs/results/accounting; never pooled",
        "data": "source NPZ; native 5m where available, documented 15m interpolation otherwise",
        "causality": "completed HTF source_ts <= observation_ts; zero future HTF bars",
        "execution": "signal at completed close; next RTH fill unless persistent resting order is explicit",
        "costs": "commission and adverse slippage on every fill",
        "benchmarks": [
            "side-specific B&H using $2,000",
            "strongest frozen result with identical entry schedule/capacity",
        ],
        "capacity": "$16,000 hard maximum; 8x of $2,000 B&H unit",
        "reentry": "every full/partial exit owns lower-price ladder and zero-buffer resting reclaim obligation",
        "stages": ["VEC_DISCOVERY", "VEC_UNTOUCHED_OOS", "V8_EXACT_REPLAY"],
        "promotion": "requires positive OOS alpha vs BOTH B&H and same-entry control, no lookahead, exact replay pass",
        "matrix_eligible": False,
    }


def initial_status(path: PathFamily) -> str:
    if path.adapter_status.startswith("READY"):
        return "READY"
    if path.adapter_status.startswith("DELEGATED"):
        return "DELEGATED"
    if path.adapter_status.startswith("QUARANTINED"):
        return "QUARANTINED"
    if path.adapter_status.startswith("NON_STRATEGY"):
        return "OBSERVABILITY_ONLY"
    return "ADAPTER_REQUIRED"


def init_campaign(root: Path, universe: dict[str, Any], replace: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "PATH_FLEET_REGISTRY.json", [asdict(p) for p in PATHS])
    with (root / "PATH_FLEET_REGISTRY.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "path_id",
                "kind",
                "priority",
                "adapter_status",
                "config_keys",
                "description",
                "settings_json",
                "fixed_entry_control",
                "fixed_exit_control",
                "source_rows",
            ]
        )
        for path in PATHS:
            writer.writerow(
                [
                    path.path_id,
                    path.kind,
                    path.priority,
                    path.adapter_status,
                    " | ".join(path.config_keys),
                    path.description,
                    json.dumps(path.settings, sort_keys=True),
                    path.fixed_entry_control,
                    path.fixed_exit_control,
                    "\n".join(path.source_rows),
                ]
            )
    atomic_json(root / "universe.json", universe)
    db_path = root / "queue.db"
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    if replace:
        con.execute("DELETE FROM jobs")
        con.execute("DELETE FROM results")
    cohort = {
        "top_long": universe["top_long"],
        "bottom_short": universe["bottom_short"],
    }
    for path in PATHS:
        con.execute(
            """INSERT OR IGNORE INTO jobs
               (path_id,kind,priority,status,universe_json,contract_json)
               VALUES (?,?,?,?,?,?)""",
            (
                path.path_id,
                path.kind,
                path.priority,
                initial_status(path),
                json.dumps(cohort, sort_keys=True),
                json.dumps(contract_for(path), sort_keys=True),
            ),
        )
    con.commit()
    con.close()
    write_report(root)


def claim(root: Path, worker: str, stale_seconds: int) -> dict[str, Any] | None:
    con = sqlite3.connect(root / "queue.db", timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("BEGIN IMMEDIATE")
    now = time.time()
    con.execute(
        """UPDATE jobs SET status='READY', claimed_by=NULL, claimed_at=NULL
           WHERE status='RUNNING' AND heartbeat_at < ?""",
        (now - stale_seconds,),
    )
    row = con.execute(
        """SELECT * FROM jobs WHERE status='READY'
           ORDER BY priority,id LIMIT 1"""
    ).fetchone()
    if row is None:
        con.execute("COMMIT")
        con.close()
        return None
    changed = con.execute(
        """UPDATE jobs SET status='RUNNING',claimed_by=?,claimed_at=?,
           heartbeat_at=?,attempts=attempts+1 WHERE id=? AND status='READY'""",
        (worker, now, now, row["id"]),
    ).rowcount
    con.execute("COMMIT")
    if not changed:
        con.close()
        return None
    out = dict(con.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
    con.close()
    out["universe"] = json.loads(out.pop("universe_json"))
    out["contract"] = json.loads(out.pop("contract_json"))
    write_report(root)
    return out


def heartbeat(root: Path, job_id: int, worker: str) -> None:
    con = sqlite3.connect(root / "queue.db")
    changed = con.execute(
        """UPDATE jobs SET heartbeat_at=? WHERE id=? AND status='RUNNING'
           AND claimed_by=?""",
        (time.time(), job_id, worker),
    ).rowcount
    con.commit()
    con.close()
    if not changed:
        raise RuntimeError("job is not owned by this worker")


def finish(root: Path, job_id: int, worker: str, result: Path, message: str) -> None:
    con = sqlite3.connect(root / "queue.db")
    changed = con.execute(
        """UPDATE jobs SET status='SCREENED',result_path=?,message=?,heartbeat_at=?
           WHERE id=? AND status='RUNNING' AND claimed_by=?""",
        (str(result), message, time.time(), job_id, worker),
    ).rowcount
    con.commit()
    con.close()
    if not changed:
        raise RuntimeError("job is not owned by this worker")
    write_report(root)


def add_result(root: Path, payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text())
    required = {
        "job_id",
        "symbol",
        "side",
        "stage",
        "status",
        "strategy_return_pct",
        "bh_return_pct",
        "same_entry_control_return_pct",
        "tim_pct",
        "trades",
        "untouched_oos",
        "exact_replay",
        "future_htf_count",
        "artifact",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"missing result fields: {sorted(missing)}")
    if payload["side"] not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    strategy = float(payload["strategy_return_pct"])
    bh = float(payload["bh_return_pct"])
    control = float(payload["same_entry_control_return_pct"])
    payload["alpha_vs_bh_pp"] = strategy - bh
    payload["alpha_vs_control_pp"] = strategy - control
    # Fail closed: a row cannot claim PASS without both comparisons, untouched
    # OOS, exact replay, and zero future HTF observations.
    promotable = (
        payload["stage"] == "V8_EXACT_REPLAY"
        and bool(payload["untouched_oos"])
        and bool(payload["exact_replay"])
        and int(payload["future_htf_count"]) == 0
        and payload["alpha_vs_bh_pp"] > 0
        and payload["alpha_vs_control_pp"] > 0
    )
    payload["promotion_candidate"] = promotable
    if payload["status"] == "PASS" and not promotable:
        payload["status"] = "RESEARCH_ONLY"
    con = sqlite3.connect(root / "queue.db")
    con.execute(
        """INSERT INTO results
           (job_id,symbol,side,stage,status,strategy_return_pct,bh_return_pct,
            same_entry_control_return_pct,alpha_vs_bh_pp,alpha_vs_control_pp,
            tim_pct,trades,untouched_oos,exact_replay,future_htf_count,artifact,
            payload_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            int(payload["job_id"]),
            payload["symbol"].upper(),
            payload["side"],
            payload["stage"],
            payload["status"],
            strategy,
            bh,
            control,
            payload["alpha_vs_bh_pp"],
            payload["alpha_vs_control_pp"],
            float(payload["tim_pct"]),
            int(payload["trades"]),
            int(bool(payload["untouched_oos"])),
            int(bool(payload["exact_replay"])),
            int(payload["future_htf_count"]),
            str(payload["artifact"]),
            json.dumps(payload, sort_keys=True),
            time.time(),
        ),
    )
    con.commit()
    con.close()
    write_report(root)


def _job_rows(con: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM jobs ORDER BY priority,kind,path_id").fetchall()


def write_report(root: Path) -> Path:
    con = sqlite3.connect(root / "queue.db")
    jobs = list(_job_rows(con))
    results = con.execute(
        """SELECT j.path_id,r.symbol,r.side,r.stage,r.status,
           r.strategy_return_pct,r.bh_return_pct,r.same_entry_control_return_pct,
           r.alpha_vs_bh_pp,r.alpha_vs_control_pp,r.tim_pct,r.trades
           FROM results r JOIN jobs j ON j.id=r.job_id
           ORDER BY r.created_at DESC"""
    ).fetchall()
    lines = [
        "# Stock Path Fleet",
        "",
        f"Updated: {utc_now()}",
        "",
        "This is research-only. A positive B&H comparison is insufficient: every "
        "exit must also beat the strongest frozen result using the identical entry "
        "schedule. LONG and SHORT are never pooled.",
        "",
        "## Queue",
        "",
        "| priority | path | kind | state | attempts | owner |",
        "|---:|---|---|---|---:|---|",
    ]
    for row in jobs:
        lines.append(
            f"| {row['priority']} | `{row['path_id']}` | {row['kind']} | "
            f"{row['status']} | {row['attempts']} | {row['claimed_by'] or '—'} |"
        )
    lines.extend(
        [
            "",
            "## Path descriptions and ranges",
            "",
            "| path | source/config | description | settings | frozen comparison |",
            "|---|---|---|---|---|",
        ]
    )
    by_id = {p.path_id: p for p in PATHS}
    for row in jobs:
        p = by_id[row["path_id"]]
        settings = "; ".join(f"{k}={v}" for k, v in p.settings.items())
        source = (
            f"{', '.join(p.config_keys) or 'research-only'}; "
            f"{len(p.source_rows)} authoritative row(s)"
        )
        lines.append(
            f"| `{p.path_id}` | {source} | {p.description} | {settings} | "
            f"entry: {p.fixed_entry_control}; exit: {p.fixed_exit_control} |"
        )
    lines.extend(
        [
            "",
            "## Result ledger",
            "",
            "| path | key | stage | state | strategy | B&H | same-entry control | "
            "alpha B&H | alpha control | TIM | trades |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in results:
        lines.append(
            f"| `{row[0]}` | {row[1]}_{row[2]} | {row[3]} | {row[4]} | "
            f"{row[5]:.2f}% | {row[6]:.2f}% | {row[7]:.2f}% | "
            f"{row[8]:+.2f}pp | {row[9]:+.2f}pp | {row[10]:.1f}% | {row[11]} |"
        )
    if not results:
        lines.append("| — | — | — | NO VALIDATED RESULTS YET | — | — | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Promotion gate",
            "",
            "A result remains research-only until: vector discovery is frozen; an "
            "untouched chronological OOS fold beats both side-specific B&H and the "
            "same-entry control; source HTF future count is zero; every exit owns "
            "a lower/reclaim reentry; and backtest_v8 exact replay agrees.",
            "",
        ]
    )
    con.close()
    out = root / "PROGRESS.md"
    out.write_text("\n".join(lines))
    return out


def status(root: Path) -> dict[str, Any]:
    con = sqlite3.connect(root / "queue.db")
    states = dict(con.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status"))
    result_count = con.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    promotable = con.execute(
        """SELECT COUNT(*) FROM results WHERE untouched_oos=1 AND exact_replay=1
           AND future_htf_count=0 AND alpha_vs_bh_pp>0 AND alpha_vs_control_pp>0"""
    ).fetchone()[0]
    con.close()
    return {
        "root": str(root),
        "states": states,
        "results": result_count,
        "promotion_candidates": promotable,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = ap.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--npz-dir", type=Path, default=DEFAULT_NPZ)
    p_init.add_argument("--lookback-days", type=int, default=180)
    p_init.add_argument("--top", type=int, default=10)
    p_init.add_argument("--bottom", type=int, default=10)
    p_init.add_argument("--max-data-age-days", type=int, default=14)
    p_init.add_argument("--min-history-days", type=int, default=60)
    p_init.add_argument("--replace", action="store_true")
    p_claim = sub.add_parser("claim")
    p_claim.add_argument("--worker", default=f"{socket.gethostname()}:{os.getpid()}")
    p_claim.add_argument("--stale-seconds", type=int, default=3600)
    p_hb = sub.add_parser("heartbeat")
    p_hb.add_argument("--job-id", type=int, required=True)
    p_hb.add_argument("--worker", required=True)
    p_finish = sub.add_parser("finish")
    p_finish.add_argument("--job-id", type=int, required=True)
    p_finish.add_argument("--worker", required=True)
    p_finish.add_argument("--result", type=Path, required=True)
    p_finish.add_argument("--message", default="")
    p_add = sub.add_parser("add-result")
    p_add.add_argument("payload", type=Path)
    sub.add_parser("status")
    sub.add_parser("report")
    args = ap.parse_args()
    root = args.root.resolve()
    if args.command == "init":
        universe = rank_universe(
            args.npz_dir.resolve(),
            args.lookback_days,
            args.top,
            args.bottom,
            max_data_age_days=args.max_data_age_days,
            min_history_days=args.min_history_days,
        )
        init_campaign(root, universe, args.replace)
        print(json.dumps(status(root), sort_keys=True))
    elif args.command == "claim":
        print(json.dumps(claim(root, args.worker, args.stale_seconds), indent=2, sort_keys=True))
    elif args.command == "heartbeat":
        heartbeat(root, args.job_id, args.worker)
    elif args.command == "finish":
        finish(root, args.job_id, args.worker, args.result, args.message)
    elif args.command == "add-result":
        add_result(root, args.payload)
    elif args.command == "status":
        print(json.dumps(status(root), sort_keys=True))
    elif args.command == "report":
        print(write_report(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
