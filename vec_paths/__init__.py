# vec_paths — live entry path modules for vec_engine_v1
# Each module exposes one check_*_entry() function that mirrors the live
# trading logic exactly (tradier_manage._check_dc_break,
# ez_manage/ez_reentry_pullback reentry paths).
#
# ALL paths are ADDITIVE after the existing WT_DC_ENTRY gate — they do NOT
# replace or bypass existing gates.  See vec_engine_v1.py for integration.
#
# ════════════════════════════════════════════════════════════════════════════════
# DEAD-KNOB REWIRE 2026-05-18 — VEC_ENGINE_ONLY refusal list.
# ════════════════════════════════════════════════════════════════════════════════
# Flags wired in backtest_v8_engine.py / tradier_manage.py that have NO
# semantic equivalent in v8_vec_sweep.py. Any sweep run with V8_USE_VEC_ALL=1
# (the standard path) that toggles these knobs will produce baseline-identical
# results, because the vec path doesn't simulate the live-only state machine
# they gate. Listing them here so:
#   1. sweep_coordinator can pre-flight refuse the variant rather than burn
#      hours on a guaranteed DUPLICATE_OF_<baseline>, and
#   2. future wiring sessions know exactly what infra is missing.
#
# Each entry: KNOB_NAME → (REQUIRED_VEC_INFRA_TO_WIRE, COMMENT).
VEC_ENGINE_ONLY_KNOBS = {
    # BT_PRESERVE_DEBOUNCE_ACROSS_BARS — backtest_v8_engine.py:3208 wipes
    # ez_manage._recent_reduces / redis debounce / tracker_manager.last_check_times
    # every bar by default. Flag=True suspends the wipe so live-style cooldowns
    # apply. v8_vec_sweep.py uses its own state machine (state.last_augment_ts,
    # state.last_reduce_ts, gr_last_fire_ts, augmented_count) which is preserved
    # across bars by construction; there is no debounce wipe to suppress. To
    # actually honour this flag in vec, we would have to re-introduce the live
    # debounce_exec Redis-key model — which the vec engine deliberately removed
    # for determinism. KEEP AS ENGINE-ONLY.
    "BT_PRESERVE_DEBOUNCE_ACROSS_BARS": (
        "Live debounce_exec Redis model would need to be ported back into vec; "
        "currently vec preserves cooldowns by design.",
        "Engine-only; vec already behaves like BT_PRESERVE_DEBOUNCE=True.",
    ),
    # DT_TARGET_ATR_ENABLED + DT_TARGET_ATR_MULT + TRADIER_DC_DAYTRADE_TARGET_PCT
    # — tradier_manage.py:15667 inside daytrade flow. vec_sweep has no separate
    # daytrade vs swing wing; every position is treated as a single state with
    # one exit cascade (R1/R2/WT_CROSSUNDER_FINAL/PEAK_GIVEBACK/PPL).
    # To wire: would need (a) trade_wing per-position state, (b) a parallel
    # daytrade exit pipeline gated by `wing == 'daytrade'`, (c) DC_DAYTRADE
    # entry path that tags newborn positions. Substantial refactor; deferred.
    "DT_TARGET_ATR_ENABLED": (
        "trade_wing state + parallel DT exit cascade required.",
        "Engine-only; vec has no daytrade wing.",
    ),
    "DT_TARGET_ATR_MULT": (
        "trade_wing state + parallel DT exit cascade required.",
        "Engine-only; vec has no daytrade wing.",
    ),
    "TRADIER_DC_DAYTRADE_TARGET_PCT": (
        "trade_wing state + parallel DT exit cascade required.",
        "Engine-only; vec has no daytrade wing.",
    ),
    # DC_TIER4_BAR_MATURITY_BLOCK_ENABLED / DC_TIER4_BAR_MATURITY_BLOCK +
    # WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED / WT_DC_ENTRY_BAR_MATURITY_BLOCK
    # — tradier_manage.py:7127 / :2588. Both check the DC-tier multi-resolution
    # augment-sizing model (Tier 1=5m, 2=15m, 3=1h, 4=4h) and refuse the augment
    # when the current daily bar has consumed >70% of ATR_D in the direction of
    # the augment. v8_vec_sweep doesn't compute DC-tier per bar; AUGMENT sizing
    # is a single GR multiplier. To wire: would need (a) per-bar active_tier
    # array (4 levels of dc_high/low compare), (b) qty_mult that scales by tier,
    # (c) bar_maturity computation (price vs open_D vs atr_D), (d) gate on
    # AUGMENT path. Substantial; deferred.
    "DC_TIER4_BAR_MATURITY_BLOCK_ENABLED": (
        "per-bar active_tier (5m/15m/1h/4h DC breakout) + tier-aware aug sizing required.",
        "Engine-only; vec uses GR-multiplier augment sizing, no DC-tier model.",
    ),
    "DC_TIER4_BAR_MATURITY_BLOCK": (
        "per-bar active_tier + bar_maturity gate required.",
        "Engine-only; vec uses GR-multiplier augment sizing, no DC-tier model.",
    ),
    "WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED": (
        "WT_DC_ENTRY scorer + bar_maturity gate required in vec OPEN path.",
        "Engine-only; vec uses precomputed WT_3M + GR + reentry triggers, no WT_DC_ENTRY scorer.",
    ),
    "WT_DC_ENTRY_BAR_MATURITY_BLOCK": (
        "WT_DC_ENTRY scorer + bar_maturity gate required in vec OPEN path.",
        "Engine-only; vec uses precomputed WT_3M + GR + reentry triggers, no WT_DC_ENTRY scorer.",
    ),
    # GR v5 BREAKOUT-CONFIRM → BOUNCE-ENTRY state machine (2026-05-18, SKELETON).
    # vec_paths/gr_v5_state.py:evaluate_gr_v5_vec currently returns zero masks
    # regardless of inputs. Full implementation (per-(sym,side) state arrays,
    # arm tracking, ARM→FIRE/DISARM transitions) lands in the followup
    # session. Sweep coordinator should pre-flight refuse GR_V5_* arms with
    # this reason UNTIL state arrays + arm tracking land — at which point
    # remove the entries below and parity-validate via tools/test_gr_v5_parity.py.
    "GR_V5_ENABLED": (
        "state arrays + arm tracking (see vec_paths/gr_v5_state.py TODOs 1-9)",
        "Skeleton-only; full state machine + LTF bounce detection pending.",
    ),
    "GR_V5_HTF_TFS": (
        "state arrays + arm tracking",
        "Skeleton-only; HTF break detection pending.",
    ),
    "GR_V5_HTF_MIN_ALIGN": (
        "state arrays + arm tracking",
        "Skeleton-only; HTF alignment counter pending.",
    ),
    "GR_V5_BREAKOUT_REQUIRE_VOLUME": (
        "state arrays + arm tracking",
        "Skeleton-only; volume gate pending.",
    ),
    "GR_V5_BREAKOUT_VOL_MULT": (
        "state arrays + arm tracking",
        "Skeleton-only; volume gate pending.",
    ),
    "GR_V5_LTF_TFS": (
        "state arrays + arm tracking",
        "Skeleton-only; LTF bounce detection pending.",
    ),
    "GR_V5_LTF_MIN_ALIGN": (
        "state arrays + arm tracking",
        "Skeleton-only; LTF alignment counter pending.",
    ),
    "GR_V5_BOUNCE_STOCH_LONG": (
        "state arrays + arm tracking",
        "Skeleton-only; oversold gate pending.",
    ),
    "GR_V5_BOUNCE_STOCH_SHORT": (
        "state arrays + arm tracking",
        "Skeleton-only; overbought gate pending.",
    ),
    "GR_V5_BOUNCE_WT_CROSS_REQUIRED": (
        "state arrays + arm tracking",
        "Skeleton-only; fresh-cross detector pending.",
    ),
    "GR_V5_ARM_WINDOW_BARS": (
        "state arrays + arm tracking",
        "Skeleton-only; arm-timeout counter pending.",
    ),
    "GR_V5_RETEST_BAND_PCT": (
        "state arrays + arm tracking",
        "Skeleton-only; retest band gate pending.",
    ),
    "GR_V5_INVALIDATE_PCT": (
        "state arrays + arm tracking",
        "Skeleton-only; invalidation gate pending.",
    ),
    # BREAKOUT_RETEST_ARMED persistent variant (2026-05-18, B1 caveat). The
    # stateless form (BREAKOUT_RETEST_ARMED_ENABLED) is wired by Agent B2 at
    # vec_paths/breakout_retest.py. The PERSISTENT form uses a per-(symbol,side)
    # state dict (arm on dc_high_D[prev_D] cross + volume confirm, fire on
    # retest within 7d) and lives only in ez_manage.py MultiAccountTradeManager.
    # vec engine has no equivalent state-dict path; refuse arms toggling it.
    "BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED": (
        "per-(symbol,side) breakout_retest_armed state dict",
        "Engine-only; vec uses stateless dc_basis_D anchor variant (vec_paths/breakout_retest.py).",
    ),
}


def vec_refuses_knob(knob_name: str) -> bool:
    """Return True when the knob is wired only in the live/slow engine and
    will produce DUPLICATE_OF_<baseline> results if toggled in vec mode.

    sweep_coordinator / v8_vec_sweep callers should consult this to refuse
    variants up-front rather than burn compute on guaranteed-baseline runs.
    """
    return str(knob_name).strip() in VEC_ENGINE_ONLY_KNOBS


def vec_engine_only_reason(knob_name: str) -> str:
    """Return human-readable refusal reason or empty string when not in list."""
    entry = VEC_ENGINE_ONLY_KNOBS.get(str(knob_name).strip())
    return entry[1] if entry else ""
