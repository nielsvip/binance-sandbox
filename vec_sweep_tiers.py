#!/usr/bin/env python3
"""
vec_sweep_tiers.py — Tier registry for vec_sweep.py.

Each tier is a function returning a list of (label, overrides_dict) tuples.
The overrides_dict maps VecConfig field names to values.

RULES (CLAUDE.md IMPOSTER BLOCK):
  - NO per-symbol-only BEST promotion tiers.
  - NO custom "Score" composites without canonical row.
  - All tiers produce pool results only (multi-symbol run via vec_sweep.py).

Standalone module — does NOT import vec_engine_v1 (avoids circular dep at load time).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Type alias
Tier = List[Tuple[str, Dict[str, Any]]]


# ───────────────────────────────────────────────────────────────────────
# TIER: live_default_baseline
# Single config — live defaults as they stand. Used to validate the vec
# engine produces a finite, non-NaN pool_sharpe on the target universe.
# ───────────────────────────────────────────────────────────────────────
def live_default_baseline() -> Tier:
    """One config: all VecConfig defaults. Validates the pipeline end-to-end."""
    return [("live_default", {})]


# ───────────────────────────────────────────────────────────────────────
# TIER: wt_dc_threshold_sweep
# 11 WT_DC_ENTRY_THRESHOLD steps from 0 to 100.
# Tests sensitivity of the primary entry scorer.
# ───────────────────────────────────────────────────────────────────────
def wt_dc_threshold_sweep() -> Tier:
    """Sweep WT_DC_ENTRY_THRESHOLD across 0–100 in steps of 10."""
    combos: Tier = []
    for thr in range(0, 110, 10):
        label = f"wt_dc_thr_{thr}"
        combos.append((label, {"WT_DC_ENTRY_THRESHOLD": float(thr)}))
    return combos


# ───────────────────────────────────────────────────────────────────────
# TIER: gr_entry_consensus_grid
# GR_HTF_REQUIRE_BULL × GR_HTF_REQUIRE_BEAR grid (6 × 7 = 42 configs).
# Tests the GR HTF gate on entry.
# ───────────────────────────────────────────────────────────────────────
def gr_entry_consensus_grid() -> Tier:
    """6×7 grid: GR_HTF_REQUIRE_BULL ∈ {1,2,3} × GR_HTF_REQUIRE_BEAR ∈ {1,2,3,4,5,6,7}.
    Also includes GR_HTF_GATE_ENABLED=False baseline.
    Total: 1 + 3×7 = 22 configs (per side sweep).
    Full cross: 1 baseline + 3 bull × 7 bear = 22 configs.
    """
    combos: Tier = [("gr_htf_gate_off", {"GR_HTF_GATE_ENABLED": False})]
    for require_bull in [1, 2, 3]:
        for require_bear in [1, 2, 3, 4, 5, 6, 7]:
            label = f"gr_b{require_bull}_r{require_bear}"
            combos.append((label, {
                "GR_HTF_GATE_ENABLED": True,
                "GR_HTF_REQUIRE_BULL": require_bull,
                "GR_HTF_REQUIRE_BEAR": require_bear,
            }))
    return combos


# ───────────────────────────────────────────────────────────────────────
# TIER: entry_path_ablation
# Each entry path boolean flipped on/off relative to the live default.
# Tests which entry paths add or remove value.
# ───────────────────────────────────────────────────────────────────────
def entry_path_ablation() -> Tier:
    """Ablation: one boolean entry gate flipped per variant. Baseline = live defaults."""
    gates = [
        "WT_DC_ENTRY_GATE_ENABLED",
        "GOLDEN_RULE_ENABLED",
        "GR_HTF_GATE_ENABLED",
        "LTF_ALIGN_GATE_ENABLED",
        "ENTRY_SIGNAL_GATE_ENABLED",
        "STDEV_BREAKOUT_ENABLED",
        "STDEV_BOUNCE_ENABLED",
        "TRADIER_DC_DAYTRADE_ENABLED",
        "TRADIER_RSI2_ENABLED",
        "PARTIAL_PROFIT_LOCK_ENABLED",
        "VOL_TARGET_ENABLED",
        "DD_KELLY_ENABLED",
    ]
    combos: Tier = [("baseline", {})]
    for gate in gates:
        # Determine current default from VecConfig defaults
        # We flip: if default is False → test True, if default is True → test False
        _defaults_false = {
            "GOLDEN_RULE_ENABLED",
            "GR_HTF_GATE_ENABLED",
            "LTF_ALIGN_GATE_ENABLED",
            "STDEV_BREAKOUT_ENABLED",
            "STDEV_BOUNCE_ENABLED",
            "TRADIER_DC_DAYTRADE_ENABLED",
            "TRADIER_RSI2_ENABLED",
            "PARTIAL_PROFIT_LOCK_ENABLED",
            "VOL_TARGET_ENABLED",
            "DD_KELLY_ENABLED",
        }
        if gate in _defaults_false:
            combos.append((f"ON_{gate}", {gate: True}))
        else:
            combos.append((f"OFF_{gate}", {gate: False}))
    return combos


# ───────────────────────────────────────────────────────────────────────
# TIER: exit_path_ablation
# Each exit path boolean flipped on/off relative to the live default.
# Tests which exit paths add or remove value.
# ───────────────────────────────────────────────────────────────────────
def exit_path_ablation() -> Tier:
    """Ablation: one boolean exit gate flipped per variant. Baseline = live defaults."""
    combos: Tier = [("baseline", {})]
    exit_gates = [
        ("R1_DC_LOW4_3M_EMERGENCY_ENABLED", True),   # default ON
        ("WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED", True),  # default ON
        ("WT_CROSSUNDER_FINAL_ENABLED", False),       # default OFF
        ("STRUCTURAL_RANGE_SHIFT_EXIT", False),       # default OFF
        ("TRADIER_RSI2_ENABLED", False),              # default OFF
        ("PARTIAL_PROFIT_LOCK_ENABLED", False),       # default OFF
        ("HEDGE_ENGINE_ENABLED", False),              # default OFF
    ]
    for gate, default_val in exit_gates:
        flip_to = not default_val
        prefix = "ON" if flip_to else "OFF"
        combos.append((f"{prefix}_{gate}", {gate: flip_to}))
    # Also sweep R2_TF_LIST
    for tfs in [["15m"], ["1h"], ["4h"], ["15m", "1h"], ["1h", "4h"]]:
        label = "R2_tfs_" + "_".join(tfs)
        combos.append((label, {"R2_TF_LIST": tfs}))
    return combos


# ───────────────────────────────────────────────────────────────────────
# TIER: mega_combo_v1
# 1000+ configs across multiple knob axes. Stress test for speed + OOM.
# Covers: WT_DC_ENTRY_THRESHOLD × HTF_ALIGN × COMBINED_STOCH × GR_HTF.
# ───────────────────────────────────────────────────────────────────────
def mega_combo_v1() -> Tier:
    """1000+ config stress test across 4 axes.

    Axes:
      WT_DC_ENTRY_THRESHOLD:  [30, 45, 55, 65, 75] (5 levels)
      HTF_ALIGN_REQUIRED_TRADIER: [1, 2, 3] (3 levels)
      COMBINED_STOCH_GATE_TRADIER: [40, 50, 60, 70, 80] (5 levels)
      GR_HTF: [(OFF), (ON, 1 bull, 1 bear), (ON, 2 bull, 2 bear)] (3 levels)

    5 × 3 × 5 × 3 = 225 core configs (fast, all combos)
    Then add 800 variants of WT_DC_ENTRY_THRESHOLD x TRADIER_ENTRY_SCORE_THRESHOLD
    (20 × 40 = 800)
    Total: 1026 configs.
    """
    combos: Tier = [("mega_baseline", {})]

    # Core 4-axis grid (225 configs)
    wt_dc_thrs = [30.0, 45.0, 55.0, 65.0, 75.0]
    htf_aligns = [1, 2, 3]
    stoch_gates = [40.0, 50.0, 60.0, 70.0, 80.0]
    gr_htf_modes = [
        ("grOFF", {"GR_HTF_GATE_ENABLED": False}),
        ("gr1", {"GR_HTF_GATE_ENABLED": True, "GR_HTF_REQUIRE_BULL": 1, "GR_HTF_REQUIRE_BEAR": 1}),
        ("gr2", {"GR_HTF_GATE_ENABLED": True, "GR_HTF_REQUIRE_BULL": 2, "GR_HTF_REQUIRE_BEAR": 2}),
    ]

    for thr in wt_dc_thrs:
        for htf in htf_aligns:
            for stoch in stoch_gates:
                for gr_label, gr_ovr in gr_htf_modes:
                    label = f"dc{int(thr)}_h{htf}_s{int(stoch)}_{gr_label}"
                    overrides: Dict[str, Any] = {
                        "WT_DC_ENTRY_THRESHOLD": thr,
                        "HTF_ALIGN_REQUIRED_TRADIER": htf,
                        "COMBINED_STOCH_GATE_TRADIER": stoch,
                    }
                    overrides.update(gr_ovr)
                    combos.append((label, overrides))

    # 2D WT_DC × TRADIER_ENTRY_SCORE grid
    # 19 × 41 = 779 configs to bring total above 1000
    dc_thrs_2d = [float(x) for x in range(20, 96, 4)]    # 19 values (20,24,...,92)
    score_thrs_2d = [float(x) for x in range(16, 57, 1)]  # 41 values (16..56)
    for dc_thr in dc_thrs_2d:
        for score_thr in score_thrs_2d:
            label = f"2d_dc{int(dc_thr)}_sc{int(score_thr)}"
            combos.append((label, {
                "WT_DC_ENTRY_THRESHOLD": dc_thr,
                "TRADIER_ENTRY_SCORE_THRESHOLD": score_thr,
            }))

    return combos


# ───────────────────────────────────────────────────────────────────────
# Tier registry
# ───────────────────────────────────────────────────────────────────────
TIER_REGISTRY: Dict[str, Any] = {
    "live_default_baseline": live_default_baseline,
    "wt_dc_threshold_sweep": wt_dc_threshold_sweep,
    "gr_entry_consensus_grid": gr_entry_consensus_grid,
    "entry_path_ablation": entry_path_ablation,
    "exit_path_ablation": exit_path_ablation,
    "mega_combo_v1": mega_combo_v1,
    "grtf7_full": lambda: grtf7_full(),
}


# ───────────────────────────────────────────────────────────────────────
# TIER: grtf7_full
# Full 5 × 7 = 35-config GR_HTF entry grid: GOLDEN_RULE_HTF_MIN_TFS × MIN_IND.
# Mirrors backtest_v8_sweep.grid_tradier_grtf7_hunt but runs on vec_sweep
# (550× faster, no OOM). Plus baseline + GR7_off = 37 total.
# Per user 2026-05-12: this is the GR_HTF DIRECT signal threshold sweep.
# ───────────────────────────────────────────────────────────────────────
def grtf7_full() -> Tier:
    combos: Tier = [
        ("baseline", {}),
        ("GR7_off", {"GOLDEN_RULE_HTF_MIN_TFS": 0, "GOLDEN_RULE_MIN_IND": 2}),
    ]
    for tfs in [1, 2, 3, 4, 5]:
        for ind in [1, 2, 3, 4, 5, 6, 7]:
            combos.append((f"GR7_tfs{tfs}_ind{ind}", {
                "GOLDEN_RULE_HTF_MIN_TFS": tfs,
                "GOLDEN_RULE_MIN_IND": ind,
            }))
    return combos


def get_tier(name: str) -> Tier:
    """Return the config list for a named tier. Raises KeyError on unknown name."""
    if name not in TIER_REGISTRY:
        raise KeyError(
            f"Unknown tier {name!r}. Available: {sorted(TIER_REGISTRY)}"
        )
    return TIER_REGISTRY[name]()


def list_tiers() -> List[str]:
    """Return sorted list of all available tier names."""
    return sorted(TIER_REGISTRY)


if __name__ == "__main__":
    import json
    for tier_name in list_tiers():
        configs = get_tier(tier_name)
        print(f"{tier_name}: {len(configs)} configs")
        if configs:
            print(f"  first: {configs[0][0]} overrides={json.dumps(configs[0][1])}")
            print(f"  last:  {configs[-1][0]} overrides={json.dumps(configs[-1][1])}")
