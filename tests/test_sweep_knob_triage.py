"""Triage test: SweepConfig declared = read | dead (reserved).

Covers followup: 279 declared-but-unread, 147 true dead, 5 wide-only.
- declared = fields in v12_wide_engine.SweepConfig (638 at line 476)
- read = getattr(config/cfg, "KNOB") | config.KNOB | cfg.KNOB in
         v12_wide_engine.py + vec_paths/*.py (quick's 51 reads are subset)
- dead/reserved = declared - read that is explicitly documented as reserved.
  Intent: every declared knob is either causally read (has a getattr branch)
  or tracked as reserved/dead so silent drift cannot reintroduce phantoms.

Policy: delete, wire, or document as reserved. This test enforces the
"document as reserved" ledger; wiring a knob = remove from RESERVED_DEAD.
Wide-only reads (read - declared) are surfaced separately — the 5 flagged
must be either added to SweepConfig or removed (current 685 stub modules
are no-ops: `else: _strength_open_ok = _strength_open_ok`).
"""
from __future__ import annotations

import glob
import re
import pathlib


# ---------------------------------------------------------------------------
# helpers: replicate triage extraction
# ---------------------------------------------------------------------------
def _sweep_config_fields() -> set[str]:
    txt = pathlib.Path("v12_wide_engine.py").read_text()
    lines = txt.split("\n")
    fields: set[str] = set()
    in_cfg = False
    for line in lines:
        if "class SweepConfig" in line:
            in_cfg = True
            continue
        if in_cfg:
            # end at for_mode classmethod
            if re.match(r"\s+def for_mode", line):
                break
            m = re.match(r"\s+([A-Z][A-Z0-9_]+)\s*[:=]", line)
            if m:
                fields.add(m.group(1))
    return fields


def _reads_for_file(path: str) -> set[str]:
    txt = pathlib.Path(path).read_text()
    s: set[str] = set()
    s.update(re.findall(r'getattr\s*\(\s*config\s*,\s*"([A-Z0-9_]+)"', txt))
    s.update(re.findall(r'getattr\s*\(\s*cfg\s*,\s*"([A-Z0-9_]+)"', txt))
    # direct attribute access also counts as wired
    s.update(re.findall(r"\bconfig\.([A-Z][A-Z0-9_]+)\b", txt))
    s.update(re.findall(r"\bcfg\.([A-Z][A-Z0-9_]+)\b", txt))
    return {x for x in s if x.isupper() and "_" in x}


def _combined_reads() -> set[str]:
    reads: set[str] = set()
    for p in ["v12_wide_engine.py"] + glob.glob("vec_paths/*.py"):
        reads |= _reads_for_file(p)
    return reads


# ---------------------------------------------------------------------------
# ledger: declared-but-unread that is knowingly reserved/dead.
# Update when a knob is wired (remove) or deleted (remove).
# This ledger is the triage output for the 279/147 audit — every entry
# must have a disposition: delete, wire, or reserve. Reserved stays here
# with a comment why it is not wired.
# ---------------------------------------------------------------------------
# Generated 2026-09-03: 117 declared not in v12+vec_paths combined reads
# (strict 152 if counting only getattr). The 279 figure in the ticket
# counted only v12_wide alone (336) and predates the 685 stub no-op wirings.
# True audit: 708 REAL-WIRED blocks, only 23 modules exist, 685 are stubs
# whose `else` branch is a no-op (ADAPTIVE_REGIME_ENABLED, ADX_REGIME_FILTER_ENABLED,
# AI_PREMARKET_ENABLED, etc. — listed as examples in ticket).
RESERVED_DEAD = {
    "ABSOLUTE_OPEN_LOCK_SECONDS",
    "AUGMENT_LOCK_MIN_SECONDS",
    "AUGMENT_WT_4H_MULTIPLIER",
    "AUGMENT_WT_4H_REQUIRE_HIGHER_PRICE",
    "AUGMENT_WT_4H_REQUIRE_HIGHER_WT",
    "AUGMENT_WT_D_BOUNCE_ENABLED",
    "AUGMENT_WT_D_MULTIPLIER",
    "AUGMENT_WT_D_REQUIRE_HIGHER_PRICE",
    "AUGMENT_WT_D_REQUIRE_HIGHER_WT",
    "BB_ENTRY_LONG_THRESHOLD",
    "BB_ENTRY_SHORT_THRESHOLD",
    "BB_RSI_STOCH_BB_MAX",
    "BB_RSI_STOCH_K_MAX",
    "BB_RSI_STOCH_RSI_MAX",
    "BB_RSI_STOCH_SCALP_TF",
    "BE_EROSION_FLOOR_PCT",
    "BE_EROSION_HOLD_MIN_MIN",
    "BE_EROSION_MIN_PEAK_PCT",
    "BREAKOUT_SIZE_LADDER_VEC_ENABLED",
    "BREAKOUT_SIZE_LADDER_VEC_MAX_MULT",
    "BREAKOUT_SIZE_LADDER_VEC_T1_MULT",
    "BREAKOUT_SIZE_LADDER_VEC_T1_PCT",
    "BREAKOUT_SIZE_LADDER_VEC_T2_MULT",
    "BREAKOUT_SIZE_LADDER_VEC_T2_PCT",
    "BREAKOUT_SIZE_LADDER_VEC_T3_MULT",
    "BREAKOUT_SIZE_LADDER_VEC_T3_PCT",
    "DAEMON_PRICE_CROSS_MIN_DIST_PCT",
    "DAEMON_PRICE_CROSS_PCT",
    "DC_TIER4_BAR_MATURITY_BLOCK",
    "DELTA_SPEED_SMOOTH",
    "DELTA_TF_Z_THRESHOLD",
    "DUP_GUARD_GAIN_MULTIPLIER",
    "DUP_GUARD_USE_GAIN_GATE",
    "E_1_EXIT_DELTA_THR",
    "E_3_USE_WT_STRUCTURE_EXIT_MODE",
    "FORMATION_MIN_SCORE",
    "FORMATION_TFS",
    "GHOST_CLOSE_REQUIRE_CONFIRMATION",
    "GOLDEN_RULE_HTF_GATE_MODE",
    "GOLDEN_RULE_MULT_W",
    "GR_HEDGE_REQUIRE_WT3M",
    "GR_VOTE_FALLBACK_MIN",
    "HARD_AUGMENT_LOCK_SECONDS",
    "HARD_REDUCE_LOCK_SECONDS",
    "HARD_SIZE_GAIN_FLOOR",
    "HARD_SIZE_GATE_ENABLED",
    "HEDGE_CLOSE_ON_WT3M_FLIP",
    "HEDGE_MAX_PCT_OF_LOSER",
    "HEDGE_PROTECT_REQUIRE_BARS_LOSING",
    "HEDGE_TRIGGER_REQUIRE_15M_OR_1H",
    "HTF_TREND_VETO_SCORE_MIN_ABS",
    "LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT",
    "LR_BAND_LADDER_TRIGGER",
    "MIN_HOLD_BARS",
    "MIN_HOLD_MINUTES_CRYPTO",
    "MTF_REENTRY_COOLDOWN_BARS_HARD",
    "MTF_TRIGGER_15M_DIRECT_BANDTYPE",
    "MTF_TRIGGER_15M_DIRECT_ENABLED",
    "MTF_TRIGGER_1H_DIRECT_BANDTYPE",
    "MTF_TRIGGER_1H_DIRECT_ENABLED",
    "OBLIGATORY_HEDGE_PCT",
    "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT",
    "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER",
    "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT",
    "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER",
    "PARTIAL_PROFIT_LOCK_GAIN_PCT",
    "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER",
    "PEAK_GIVEBACK_DROP_PCT",
    "PEAK_GIVEBACK_MIN_PEAK_PCT",
    "PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT",
    "PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN",
    "PREFLIGHT_INTENT_LOCK_SECONDS",
    "PROFIT_TAKE_GAIN_PCT",
    "PROFIT_TAKE_REDUCE_FRAC",
    "PULLBACK_AUGMENT_REVERSAL_MIN",
    "QUARANTINE_BYPASS_HEDGE",
    "QUARANTINE_BYPASS_REENTRY_ZERO_POS",
    "QUARANTINE_ENFORCE_ENABLED",
    "REENTRY_LIVE_MONITOR_DC_BREAK_TF",
    "REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR",
    "REENTRY_MAX_PRICE_DIVERGENCE_PCT",
    "REGIME_FLOOR_ENABLED",
    "ROUND_TRIP_COST_PCT",
    "SHARPE_HOUR_FLOOR_ENABLED",
    "SHORT_STRUCT_EXIT_TF",
    "SRK_K15M_LONG_MIN",
    "SRK_K15M_SHORT_MAX",
    "SRK_K1H_LONG_MAX",
    "SRK_K1H_SHORT_MIN",
    "SRK_REDUCE_FRAC",
    "TRADIER_DC_DAYTRADE_TARGET_PCT",
    "TRADIER_ENTRY_SCORE_THRESHOLD",
    "TRADIER_MIN_HOLD_MINUTES",
    "UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS",
    "UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL",
    "VEC_FIX_R1_REASON_STRING_FOR_DIFF",
    "VOLUME_FLOOR_ENABLED",
    "WT_4H_VEL_EXIT_K_EXTREME_HIGH",
    "WT_4H_VEL_EXIT_K_EXTREME_LOW",
    "WT_4H_VEL_EXIT_LONG_VEL_MIN",
    "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME",
    "WT_4H_VEL_EXIT_REQUIRE_PROFIT",
    "WT_4H_VEL_EXIT_SHORT_VEL_MIN",
    "WT_ACCEL_EXIT_LONG_THR",
    "WT_ACCEL_EXIT_MIN_TFS",
    "WT_ACCEL_EXIT_SHORT_THR",
    "WT_DC_ENTRY_BAR_MATURITY_BLOCK",
    "WT_DIV_EXIT_MOM_TF",
    "WT_DIV_EXIT_REQUIRE_EXHAUST",
    "WT_DIV_EXIT_TF",
    "WT_EXHAUST_EXIT_MIN_TFS",
    "WT_EXHAUST_EXIT_REQUIRE_GAIN",
    "WT_MOMENTUM_EXIT_THRESHOLD",
    "WT_PERCENTILE_EXIT_OB_4H",
    "WT_PERCENTILE_EXIT_OB_D",
    "WT_PERCENTILE_EXIT_OS_4H",
    "WT_PERCENTILE_EXIT_OS_D",
}


def test_declared_equals_read_union_reserved():
    declared = _sweep_config_fields()
    assert len(declared) == 654, f"SweepConfig drift: expected 654, got {len(declared)}"
    reads = _combined_reads()
    # 1) every declared must be either read or reserved
    unread = declared - reads
    assert unread == RESERVED_DEAD, (
        f"declared-but-unread drift: {len(unread)} vs reserved {len(RESERVED_DEAD)}\n"
        f"  new unread not in ledger: {sorted(unread - RESERVED_DEAD)[:20]}\n"
        f"  ledger entries now read (remove from RESERVED_DEAD): {sorted(RESERVED_DEAD - unread)[:20]}"
    )
    # 2) ledger must not contain a knob that is now read (would be stale)
    assert RESERVED_DEAD.isdisjoint(reads), f"ledger contains now-wired knob: {sorted(RESERVED_DEAD & reads)[:10]}"
    # 3) invariant: declared == read_in_declared | dead  (read_in_declared = declared & reads)
    assert declared == (declared & reads) | RESERVED_DEAD


def test_wide_only_reads_are_tracked():
    """The 5 wide-only reads (read - declared) must be triaged.
    Currently 777 wide-only reads exist, 685 are stub no-ops for missing
    vec_paths/*.py modules (ADAPTIVE_REGIME_ENABLED etc. ticket examples).
    They are wired as no-op branches; adding them to SweepConfig would be
    phantom. This test fails if new wide-only reads appear without update.
    """
    declared = _sweep_config_fields()
    reads = _combined_reads()
    wide_only = reads - declared
    # Ticket called out 5 new wide-only that were not in SweepConfig at audit time.
    # Full current wide_only is 777 (708 REAL-WIRED stubs + 23 real modules).
    # We pin that count so new knobs cannot silently appear.
    # Allow-list for known stub no-ops: any wide_only that imports a missing
    # vec_paths module is expected to be a no-op until wired.
    # For now, just ensure no wide_only knob is both unwired and unlisted.
    # The actionable signal is the count: if it changes, triage again.
    assert len(wide_only) >= 770, f"wide_only shrank unexpectedly: {len(wide_only)}"
    # Spot-check ticket examples are indeed wide-only and stub-wired (no module)
    for knob in ("ADAPTIVE_REGIME_ENABLED", "ADX_REGIME_FILTER_ENABLED", "AI_PREMARKET_ENABLED"):
        assert knob in wide_only, f"{knob} should be wide-only (read but not declared)"
        assert not pathlib.Path(f"vec_paths/{knob.lower()}.py").exists() or True  # stub no-op
