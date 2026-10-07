# -*- coding: utf-8 -*-
"""ported_reentry — faithful numpy twins of ez_manage/tradier_manage reentry-lifecycle switches.
Owned by the reentry wiring agent (SWITCH_WIRING_GUIDE.md). Rules: IDENTICAL to live (same thresholds,
operators, TF; 15m floor, 3m/5m->15m), NO proxies, NO fabrication (BIBLE §19). Each switch block is
gated on its cfg value differing from the effective default and applies a REAL mask to the signal.
apply() is a pure passthrough when no switch is active (returns sig unchanged).

===========================================================================================
WIRING VERDICT (2026-09-30) — 0 WIRED, 67 VEC_UNSUPPORTED, 5 SKIPPED  (72 total)
===========================================================================================
Every switch in worklist_REENTRY.json is a REENTRY-lifecycle knob. A REENTRY decision is, by
definition, made only after a prior exit/reduction: live keys it on reentry_timestamp /
min_since_exit / reentry_level / position.last_reduction_price / position.max_quantity /
positionAmt-notional-vs-max / per-position cooldown dicts / a file-based reentry command queue.
A pure per-bar numpy mask over the frozen NPZ has NONE of that state, so it CANNOT reproduce the
live decision. Per the task rules and BIBLE §19 NO-LIES, a switch whose live decision depends on
that state is marked VEC_UNSUPPORTED and left unwired — never proxied.

Two audit-defeating farms in ez_manage.py were also found to be the auto-detected `ez_line` for
many of these switches; they are NOT the real decision and MUST NOT be ported (SWITCH_WIRING_GUIDE
§0.2, §10.0):
  * ez_manage.py ~6570-6931 (`_batch1...` -> `return True,'BATCH1_OK'`): nonsense proxies such as
    `if close <= _thr` where `_thr` is a K-reset / bar-age / size-multiplier config value (price
    compared to a stochastic/bar count), plus a copy-paste `dc_position_15m>=0.4` template reused
    verbatim across unrelated ENABLED switches. Followed by `_full_coverage_read_ez` which documents
    itself as "read ... so it counts as wired ... values are read but not used ... hash fallback".
  * ez_manage.py ~58590-59130: scaffolding stubs `_=getattr(config,X,..)` / `_=_wt` / `_ = 1 # BATCH 5`.
Both farms have no live effect; the real reentry logic (when it exists) lives in the stateful
reentry engine (process_single_reentry_evaluation ~42000-42730; reentry-queue daemon ~55700+;
exit-reclaim ~38686) and is state-dependent as described above.

Per-switch ledger (SWITCH -> status | reason):
  # --- real logic exists but is STATE-DEPENDENT (reentry/position state absent from per-bar NPZ) ---
  BREAKOUT_LEASH_REENTRY_MULT            VEC_UNSUPPORTED reentry sizing mult keyed on exit reason/gain (ez ~42033/42043)
  BTC_GUARANTEED_REENTRY_ENABLED         VEC_UNSUPPORTED guaranteed-reentry flat-after-exit state
  BTC_GUARANTEED_REENTRY_MAX_AGE_BARS    VEC_UNSUPPORTED time-since-exit (bar age) state
  BTC_GUARANTEED_REENTRY_MIN_GAP_BARS    VEC_UNSUPPORTED min gap-since-exit (bar count) state
  BOUNCE_REENTRY_K_RESET_LONG            VEC_UNSUPPORTED per-position K-reset latch state (ez_line is BATCH1 farm proxy)
  BOUNCE_REENTRY_K_RESET_SHORT           VEC_UNSUPPORTED per-position K-reset latch state (ez_line is BATCH1 farm proxy)
  CHANNEL_REENTRY_STOP_ENABLED           VEC_UNSUPPORTED reentry-stop state (ez_line is BATCH1 farm copy-paste dc template)
  DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED VEC_UNSUPPORTED gates a queued reentry cmd in the file-based daemon (ez ~55732)
  DAEMON_REENTRY_STALE_EXIT_ENABLED      VEC_UNSUPPORTED daemon stale-reentry state (ez_line is BATCH1 farm proxy)
  DIRECTION_FAVORABLE_REENTRY_ENABLED    VEC_UNSUPPORTED gated on min_since_exit + reentry delta/grandfather (ez ~42207)
  HTF_WT_CHURN_REENTRY_ENABLED           VEC_UNSUPPORTED flat_for_guarantee + reentry_level + min_since_exit (ez ~42423); tradier ABSENT
  HTF_WT_CHURN_REENTRY_MAX_AGE_MIN       VEC_UNSUPPORTED time-since-exit window (ez ~42425); tradier ABSENT
  HLR_REENTRY_MAX_AGE_S                  VEC_UNSUPPORTED time-since-exit (seconds) window
  REENTRY2_DC_BREAK_ALLOW_15M            VEC_UNSUPPORTED reentry-eval only: cooldown dict + max-size cap + reentry_level (ez ~42248-42314)
  REENTRY2_DC_BREAK_FILTER_TF            VEC_UNSUPPORTED sub-knob of the state-gated REENTRY2_DC_BREAK path (ez ~42263)
  REENTRY2_DC_BREAK_REQUIRE_K_FILTER     VEC_UNSUPPORTED sub-knob of the state-gated REENTRY2_DC_BREAK path (ez ~42261)
  REENTRY2_DC_BREAK_REQUIRE_WT_FILTER    VEC_UNSUPPORTED sub-knob of the state-gated REENTRY2_DC_BREAK path (ez ~42262)
  REENTRY_EXIT_RECLAIM_BUFFER_PCT        VEC_UNSUPPORTED keyed on position.last_reduction_price (prior exit px) (ez ~38687)
  REENTRY_POST_CONSOL_ENABLED            VEC_UNSUPPORTED reentry SIZE multiplier composition, reentry path only (ez ~42705)
  REENTRY_POST_CONSOL_ATR_THRESHOLD      VEC_UNSUPPORTED param of reentry SIZE mult (ez ~42707)
  REENTRY_POST_CONSOL_TFS_REQUIRED       VEC_UNSUPPORTED param of reentry SIZE mult (ez ~42709)
  REENTRY_SIZE_BREAKOUT_MULT             VEC_UNSUPPORTED reentry_amount sizing, keyed on reentry_level (ez ~42026)
  REENTRY_SIZE_DIP_MULT                  VEC_UNSUPPORTED reentry_amount sizing, keyed on reentry_level (ez ~42026)
  REENTRY_SIZE_EXTENDED_K1H              VEC_UNSUPPORTED reentry_amount sizing threshold (ez ~42023)
  REENTRY_SIZE_EXTENDED_MULT             VEC_UNSUPPORTED reentry_amount sizing (ez ~42026)
  REENTRY_WT15M_SIZE_MULT                VEC_UNSUPPORTED reentry SIZE mult, gated on price_ready vs reentry_level (ez ~42726)
  # --- ez_line resolves to a no-op scaffolding/BATCH5 farm; no live decision to port ---
  FOLLOW_THROUGH_REENTRY_ENABLED         VEC_UNSUPPORTED live site is a scaffolding stub (ez ~58609); real path is stateful reentry follow-through
  LEGACY_PROC_SINGLE_REENTRY             VEC_UNSUPPORTED BATCH5 no-op stub (ez ~58694)
  LEGACY_REENTRY_PSR_DC_BOUNCE           VEC_UNSUPPORTED BATCH5 no-op stub (ez ~58695)
  LEGACY_REENTRY_PSR_FULL_DC             VEC_UNSUPPORTED BATCH5 no-op stub (ez ~58696)
  LEGACY_REENTRY_PSR_K_DC_CROSSOVER      VEC_UNSUPPORTED BATCH5 no-op stub (ez ~58697)
  LEGACY_REENTRY_PSR_QUICK_RECOVERY      VEC_UNSUPPORTED scaffolding stub (ez ~58698); quick-recovery = time-since-exit state
  MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS VEC_UNSUPPORTED scaffolding stub (ez ~58754); mandatory-reentry path is stateful
  MANDATORY_REENTRY_K_HIGH_BLOCK         VEC_UNSUPPORTED scaffolding stub (ez ~58756); mandatory-reentry path is stateful
  MANDATORY_REENTRY_K_LOW_BLOCK          VEC_UNSUPPORTED scaffolding stub (ez ~58758); mandatory-reentry path is stateful
  MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME VEC_UNSUPPORTED scaffolding stub (ez ~58760); mandatory-reentry path is stateful
  MANDATORY_REENTRY_WT_FILTER_MIN_TFS    VEC_UNSUPPORTED scaffolding stub (ez ~58762); mandatory-reentry path is stateful
  MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY VEC_UNSUPPORTED scaffolding stub (ez ~58766); mandatory-reentry path is stateful
  MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP VEC_UNSUPPORTED scaffolding stub (ez ~58770); mandatory-reentry path is stateful
  MANDATORY_REENTRY_WT_FILTER_TF_MODE    VEC_UNSUPPORTED scaffolding stub (ez ~58774); mandatory-reentry path is stateful
  MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO VEC_UNSUPPORTED scaffolding stub (ez ~58778); mandatory-reentry path is stateful
  MU_CORRECTION_REENTRY_ENABLED          VEC_UNSUPPORTED scaffolding stub (ez ~58881); reentry-after-correction state
  OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT   VEC_UNSUPPORTED scaffolding stub (ez ~58896); reentry sizing/state
  OBLIGATORY_REENTRY_ENABLED             VEC_UNSUPPORTED scaffolding stub (ez ~58899); obligatory-reentry path is stateful
  OBLIGATORY_REENTRY_K15_HIGH_BLOCK      VEC_UNSUPPORTED scaffolding stub (ez ~58901); obligatory-reentry path is stateful
  OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC  VEC_UNSUPPORTED scaffolding stub (ez ~58902); reentry sizing/state
  OBLIGATORY_REENTRY_SCORE_TIER1         VEC_UNSUPPORTED scaffolding stub (ez ~58904); obligatory-reentry scoring/state
  OBLIGATORY_REENTRY_SCORE_TIER2         VEC_UNSUPPORTED scaffolding stub (ez ~58912); obligatory-reentry scoring/state
  OBLIGATORY_REENTRY_SCORE_TIER3         VEC_UNSUPPORTED scaffolding stub (ez ~58915); obligatory-reentry scoring/state
  OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK VEC_UNSUPPORTED scaffolding stub (ez ~58917); obligatory-reentry path is stateful
  OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC VEC_UNSUPPORTED scaffolding stub (ez ~58919); reentry sizing/state
  OBLIGATORY_REENTRY_SMA_FIELD           VEC_UNSUPPORTED scaffolding stub (ez ~58921); selector for the stateful path
  OBLIGATORY_REENTRY_SMA_TF              VEC_UNSUPPORTED scaffolding stub (ez ~58923); selector for the stateful path
  OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED  VEC_UNSUPPORTED scaffolding stub (ez ~58925); obligatory-reentry path is stateful
  OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED  VEC_UNSUPPORTED scaffolding stub (ez ~58927); obligatory-reentry path is stateful
  QUICK_REENTRY_60MIN_MIN_PCT            VEC_UNSUPPORTED scaffolding stub (ez ~58967); 60-min-since-exit window state
  REENTRY2_DIR_FAV_ENABLED               VEC_UNSUPPORTED scaffolding stub (ez ~58972); real path min_since_exit-gated (ez ~42212)
  REENTRY_B16_SIZE_MULT_STRONG           VEC_UNSUPPORTED scaffolding stub (ez ~58974); reentry sizing/state
  REENTRY_B16_SIZE_MULT_WEAK             VEC_UNSUPPORTED scaffolding stub (ez ~58976); reentry sizing/state
  REENTRY_B16_SMA200_PROX_PCT            VEC_UNSUPPORTED scaffolding stub (ez ~58978); reentry sizing/state
  REENTRY_B16_SMA200_PULLBACK_ENABLED    VEC_UNSUPPORTED scaffolding stub (ez ~58980); reentry pullback path is stateful
  REENTRY_CROSS_FRESHNESS_ENABLED        VEC_UNSUPPORTED scaffolding stub (ez ~58982); cross-freshness = time-since-cross state
  REENTRY_EXHAUSTED_PARTIAL_ENABLED      VEC_UNSUPPORTED scaffolding stub (ez ~58984); partial-position reentry state
  REENTRY_EXIT_RECLAIM_ENABLED           VEC_UNSUPPORTED scaffolding stub (ez ~58986); real path keyed on last_reduction_price (ez ~38686)
  REENTRY_POST_CONSOL_MULT               VEC_UNSUPPORTED scaffolding stub (ez ~58989); reentry SIZE mult value (ez ~42719)
  REENTRY_PRICE_IMPROVE_PCT              VEC_UNSUPPORTED scaffolding stub (ez ~58991); needs prior-exit price to compare
  VEC_REENTRY_DC4_EXITPRICE_ENABLED      VEC_UNSUPPORTED scaffolding stub (ez ~59117); reentry-vs-exit-price state
  # --- absent in BOTH venues: nothing to carbon-copy ---
  REENTRY_BOUNCE_BAR_GR_ENABLED          SKIPPED ez ABSENT, tradier ABSENT
  REENTRY_DC_MID_PULLBACK_ENABLED        SKIPPED ez ABSENT, tradier ABSENT
  REENTRY_K_RESET_GR_ENABLED             SKIPPED ez ABSENT, tradier ABSENT
  REENTRY_PULLBACK_GR_SCORE_ENABLED      SKIPPED ez ABSENT, tradier ABSENT
  REENTRY_SMA200_GR_CONTINUATION_ENABLED SKIPPED ez ABSENT, tradier ABSENT

No REENTRY switch is a pure per-bar function of NPZ indicators, so apply() is an honest passthrough.
If/when the vector engine gains a per-bar reentry-state model (bars-since-exit, last-exit-price,
reentry cooldown arrays), the state-dependent switches above become wireable against those arrays.
==========================================================================================="""
import numpy as np


def apply(npz, n, is_long, cfg, sig, _safe, close, _entry_filter_masks=None):
    # === REENTRY PORTED SWITCHES ===
    # All 72 worklist switches are VEC_UNSUPPORTED (reentry/position state) or SKIPPED (absent in both
    # venues) — see the module docstring ledger for the per-switch reason. None is a pure per-bar
    # function of the frozen NPZ, so there is nothing to wire without fabricating (BIBLE §19 NO-LIES).
    # apply() therefore returns the entry signal unchanged. Do NOT add a proxy here to "make a delta".
    return sig
