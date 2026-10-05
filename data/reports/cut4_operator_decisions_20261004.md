# cut#4 NEEDS-OPERATOR-DECISION log — 2026-10-04 (Mac, wire-push)

Switches/filters triaged as unwireable-inert by wave-2 children: no mappable live
parent exists, so emitting a hook would INVENT behavior (BIBLE §60 violation).
Held rows stay KEEP-ROW in TEMPLATEs (never deleted); wire_status_paint keeps
their col-A font GREY until an operator defines the parent.

## w2-orange-ports-B (48 orange filters, crypto; spec EMPTY, 16/16 twin tests green)
- ALREADY-WIRED 9 / NO-CRYPTO-GAP 6 (no action)
- NEEDS-OPERATOR-DECISION 33 (no mappable ez crypto parent):
SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER
SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER
SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER
SMA200_DIST_LONG_THRESHOLD
STRENGTH_FILTER_ENABLED
STRENGTH_MIN_SCORE
TF_FOCUS_ENTRY_HARD_GATE
TF_FOCUS_WEIGHT
TF_HTF1
TF_HTF3
TRADIER_FH_MOMENTUM_DC_MAX_LONG
TRADIER_FH_MOMENTUM_MFI_MIN
TRADIER_FH_MOMENTUM_MIN_MOVE_PCT
TRADIER_FH_MOMENTUM_WINDOW_MINUTES
VWAP_FILTER_ENABLED
WIN_TRAIL_EROSION_PCT
WT_15M_BOUNCE_FILTER_HH_ENABLED
WT_15M_BOUNCE_FILTER_HL_ENABLED
WT_15M_BOUNCE_FILTER_MODE
WT_15M_BOUNCE_REQUIRE_BOTH_HTF
WT_15M_BOUNCE_VOLUME_FILTER_ENABLED
WT_15M_BOUNCE_VOLUME_THRESHOLD
WT_DC_DC_POS_THRESHOLD_LONG
WT_DC_DC_POS_THRESHOLD_SHORT
WT_DC_DETAILED_ENTRY_THRESHOLD
WT_DC_DIRECT_THRESHOLD
WT_DC_ENTRY_THRESHOLD
WT_DC_HTF_GATE
WT_DC_HTF_GATE_MODE
WT_DC_K5M_MIN_SHORT_HARD
WT_DC_STOCH_THRESHOLD_LONG
WT_DC_STOCH_THRESHOLD_SHORT
WT_VEL_DECAY_THRESHOLD
Evidence: /tmp/twin_orange_ports_b.py (STATUS map), /tmp/test_orange_ports_b_twin.py,
/tmp/hook_spec_orange_ports_b.json (empty = no hooks).

## wire_status_paint procedure (user order 2026-10-04: grey disconnected, black when rewired)
- Tool: tools/wire_status_paint.py --dry-run | --apply (ledger: data/reports/wire_paint_ledger.json)
- Rule: col-A font GREY (FFBFBFBF = pilot GREY_SKIP) iff venue status NOT WIRED_BOTH_*.
  Wired-both rows this tool painted are restored to their ORIGINAL font color.
- NEVER touched: pre-existing grey (other reasons), orange rows (rollup), yellow
  headers (phase 2, needs filter->status map), values/bold/fills/row order.
- Re-run --apply after EVERY merge + bible rebuild. Templates on Mac only; S1
  herds unaffected until gatekeeper syncs.

## P0 pivot 2026-10-04 ~23:53 UTC (operator: "vectorized but not yet connected live")
- Priority = VEC_ONLY template switches -> wire LIVE side -> WIRED_BOTH.
- Census: crypto template VEC_ONLY 106 (105 PROMOTED), stocks 17 (16 PROMOTED).
  Lists: /tmp/p0_vec_only_crypto.txt, /tmp/p0_vec_only_stocks.txt,
  promoted intersects /tmp/p0_vec_only_promoted_{crypto,stocks}.txt.
- Earlier "live freeze" idea REJECTED by operator (and its redirect messages were
  never delivered — bad IDs). Live wiring RESUMES, targeted VEC_ONLY->BOTH with
  inert defaults; vector stays the reference (mirror existing vector predicates).
- Spawned: p0-crypto-A (53), p0-crypto-B (53), p0-stocks (17). Merge on landing:
  hook_spec apply + twin test green + bible rebuild + paint re-run.
- Paint re-run with vector rule: 110 blackened, 1883 grey (vector-blind only).

## w2/p0-stocks NEEDS-OPERATOR-DECISION 5 (no tradier live parent):
CRYPTO_SPIKE_FADE_THRESHOLD_PCT (crypto-only, no tradier equivalent)
FORCE_MIN_ONE_TRADE (harness-only, not a live switch)
GUARANTEED_REENTRY_TIGHT_STOP_ENABLED (no tradier GR pathway)
FROZEN_STOP_FILTER_TF (no tradier frozen-stop path; see twin header)
WT_SIMPLE_GUARANTEE_ENABLED (no tradier WT-guarantee path; see twin header)

## p0-crypto-A NEEDS-OPERATOR-DECISION 43:
ABLATION_DISABLE_RATIO_REBALANCE: Vector effectively inert: inline v12:12485-12486 is `pass # no ratio model`; the killing _apply_625_ablation_gates (v12:11522) is never called. Live HAS a ratio loop (ez_manage:37166) but hooking a ki
ABLATION_DISABLE_SPIKE_FADE_EXIT: Vector effectively inert: inline v12:12496-12498 `pass`; _apply_625 killer (v12:11530) never called. No live spike-fade exit parent either.
ADX_TRENDING_THRESHOLD: Vector uses it for WT-velocity regime-reduce branching + REGIME_GATE sizing (v12:10412-10449); live crypto has neither parent (only hardcoded ADX cutoffs in quality scorer). Threshold has no inert val
BACKTEST_VALIDATED_GATES_TRADIER: Vector gated to MODE==tradier (v12:9257). Zero crypto effect; a crypto live hook would create unmodeled behavior. Stocks-lane item.
CHOP_RANGING_THRESHOLD: Read-but-unused in vector formula (v12:9048; only trending_thr feeds is_ranging). Live CT_CHOP gate uses a different single-threshold formula (CT_CHOP_4H_MAX, ez_manage:662). No exact parent.
CHOP_TRENDING_THRESHOLD: Live CT_CHOP_4H gate formula differs (no ADX<25 / BB-width<0.5 legs); threshold has no inert value under the live master. Needs live formula decision.
COOLDOWN_BARS: Unit mismatch: vector is bar-based post-close cd (v12:12505); live crypto is seconds-based REENTRY_COOLDOWN_S (default 0). Bar->time conversion needs operator decision.
COOLDOWN_BARS_TRADIER: Tradier branch of v12:12505; never affects crypto vector. Stocks-lane item.
DC_BREAK_LOW_HTF_ALIGN_MIN: No live DC-break-low hard-short parent in ez lane (vector v12:9414).
DC_DAYTRADE_STOP_PCT: Dead in vector: computed v12:12531, never consumed (DC-list path replaced fixed %). Live daytrade is DC-list-only. A fixed-% exit hook would introduce divergence.
DC_DAYTRADE_TARGET_PCT: Dead in vector: computed v12:12532, never consumed. Same as STOP_PCT.
DC_HARD_STOP_REENTRY_COOLDOWN_HOURS: Vector gated is_tradier (v12:13531). Stocks-lane item.
DC_POSITION_ENTRY_THRESHOLD: Vector B_DAYTRADE *entry* block (v12:8673); live crypto daytrade is exit-only with no entry parent, and the master is shared with live exits (a new entry block would fire under live's enabled master).
DELTA_ATR_ENTRY_FILTER: No live delta-entry ATR move-filter parent (wt_dc_delta.py has no B_DELTAENTRY / atr*0.3 concept; vector v12:8729).
DELTA_EXIT_DC_FLOOR: Vector requires tradier + STOCKS_LIVE_TWINS_ENABLED (v12:9898). Stocks-lane item.
DELTA_MAX_HOLD_BARS: No live delta max-hold/age exit parent (wt_dc_delta.py has exit_min_hold only; vector v12:12520).
DYN_STRUCT_TRAIL_ENABLED: Vector gated tradier (v12:11958). Stocks-lane item.
FORCE_MIN_ONE_TRADE: Backtest-only construct (force first-valid-bar entry, v12:9870); no live meaning.
GAP_CLOSE_MOC_EXIT_ENABLED: Stocks/MOC-only; crypto has no market close; vector requires tradier (only_stocks default True, v12:11833). No ez parent.
GAP_CLOSE_MOC_FORCE_MOC_AT_CLOSE: Same close-gap family: no ez parent.
GAP_CLOSE_MOC_ONLY_FOR_SHORTS: Same close-gap family: no ez parent.
GAP_CLOSE_MOC_ONLY_STOCKS: Same close-gap family: no ez parent.
GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT: Same close-gap family: no ez parent.
GAP_CLOSE_PER_SYMBOL_LOOKBACK_DAYS: Same close-gap family: no ez parent.
GAP_INVENTORY_LOOKBACK_DAYS: Fallback input to the tradier-only GAP sentinel (v12:11758). No ez parent.
GAP_MOC_DC_PROXIMITY_PCT: Same GAP-MOC family (VV proximity, v12:11798). No ez parent.
GAP_MOC_EXIT_ENABLED: Master of the tradier-only GAP sentinel (v12:11695); crypto has no MOC. No ez parent.
GAP_MOC_FORCE_MOC_AT_CLOSE: Same GAP-MOC family. No ez parent.
GAP_MOC_HOLD_POSITIVE_BIAS_PCT: Same GAP-MOC family (threshold fallback, v12:11775). No ez parent.
GAP_PER_SYMBOL_AVG_THRESH_PCT: Same GAP-MOC family. No ez parent.
GAP_PER_SYMBOL_LOOKBACK_DAYS: Same GAP-MOC family. No ez parent.
GAP_RISK_EXIT_ENABLED: Vector gated tradier (v12:11905); live parent is tradier_manage, not ez; vector module absent in this checkout. Stocks-lane item.
GAP_RISK_EXIT_COND_A_ENABLED: Same GAP_RISK family (A-enable OR-leg). Stocks-lane item.
GAP_RISK_EXIT_COND_B_ENABLED: Same GAP_RISK family (B-enable OR-leg). Stocks-lane item.
GAP_RISK_EXIT_LONG_ENABLED: Same GAP_RISK family (side gate). Stocks-lane item.
GAP_RISK_EXIT_SHORT_ENABLED: Same GAP_RISK family (side gate). Stocks-lane item.
GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED: Same GAP_RISK family (A-enable OR-leg). Stocks-lane item.
GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED: Same GAP_RISK family (B-enable OR-leg). Stocks-lane item.
GAP_RISK_REENTRY_ENABLED: Config-only: no vector predicate anywhere (field v12:6805, zero functional reads). Nothing to mirror; needs operator spec.
GAP_RISK_REENTRY_MAX_DAYS: Config-only: no vector predicate anywhere. Needs operator spec.
GAP_RISK_REENTRY_ON_FILL: Config-only: no vector predicate anywhere. Needs operator spec.
GAP_RISK_REENTRY_REQUIRE_TREND: Config-only: no vector predicate anywhere. Needs operator spec.
GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN: Vector dead-read (v12:8624 read, v12:8630 discarded, zero effect); a live hook with the vector default True would NEWLY block reentries. Operator must choose the live default.
## p0-crypto-B NEEDS-OPERATOR-DECISION:
['TF_FOCUS_ENTRY_HARD_GATE', 'TF_FOCUS_WEIGHT']: DEAD in vector: v12:8760-8766 computes focus_mult but no read site exists (grep focus_mult = defs only). No live parent; promoting cannot move the ledger. Twin tf_focus_ok always passes + documents de (vec: v12_quick_engine.py:8755-8766)
['STRENGTH_FILTER_ENABLED', 'STRENGTH_MIN_SCORE']: Vec scores OR-block fires with V8Q weights (v12:9144-9162); live has no block-fire accumulator, so no mappable live parent. Twin strength_ok + STRENGTH_WEIGHTS delivered; operator must decide block-fi (vec: v12_quick_engine.py:9144-9162)
MIN_HOLD_BARS: Vec wraps the whole exit loop (held_bars>=min_hold at v12:13919,13932,14000,14017,14055); insertion-only cannot wrap the live exit section, so veto placement needs the operator. Twin min_hold_bars/min (vec: v12_quick_engine.py:12507)
MIN_HOLD_MINUTES_TRADIER: Tradier-only in vector (v12:12518 is_tradier gate); no crypto live effect. Twin encodes the MODE gate (crypto inert). (vec: v12_quick_engine.py:12518-12519)
MTF_ATR_TRAIL_TF_TRADIER: Tradier-only in vector (v12:12655 selects it only when is_tradier; crypto uses MTF_ATR_TRAIL_TF). Twin mtf_atr_trail_tf encodes the selection. (vec: v12_quick_engine.py:12654-12660)
['PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER', 'PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER', 'PARTIAL_PROFIT_LOCK_FRAC_TRADIER', 'PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER']: No functional vector predicate in this checkout: v12 delegates to vec_decisions.reduce_profit_lock.ppl_step (v12:13758) whose module is absent here, so blind mirroring is refused. Twin mirrors the sto (vec: v12_quick_engine.py:13754-13759 + tradier_manage.py:19973-20027)
['REENTRY_ENTRY_FILTER_ENABLED', 'REENTRY_FILTER_MIN_PASS']: Vec counts passes over live entry-filter masks (v12:12469-12470,13179-13180); live reentry path has no mask-vector parent to graft onto insertion-only. Twin reentry_fire_allowed/reentry_filter_need de (vec: v12_quick_engine.py:12469-12470,13177-13181)
REENTRY_TIER1_SIZE_MULT_TRADIER: Tradier-only in vector (v12:13268 has_closed_before AND is_tradier). Twin reentry_tier1_mult encodes the gate (crypto 1.0). (vec: v12_quick_engine.py:13267-13275)
SIMPLE_PRICE_GT0_ENABLED: Test switch (v12:12509-12513): entry EVERY bar, exit every 2nd bar, bypasses all holds/cooldown. No safe live parent; auto-hooking would trade recklessly. Twin simple_gt0 mirrors exactly for lab use o (vec: v12_quick_engine.py:12508-12513)
TF_ALIGNMENT_MIN_TOTAL: Tradier-only in vector (v12:9257 MODE+BACKTEST_VALIDATED gates; comment notes crypto regression). Twin tf_alignment_ok encodes the gates (crypto inert). (vec: v12_quick_engine.py:9256-9266)
TRADIER_ENTRY_SCORE_THRESHOLD: Tradier-only in vector (v12:9285 MODE gate + >=24 arm). Twin tradier_entry_score_ok encodes it (crypto inert). (vec: v12_quick_engine.py:9283-9288)
['TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER', 'TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER']: Tradier-branch-only in vector (v12:8430-8431 select them only when is_tradier; crypto uses K_ZONE_LONG/SHORT_THRESHOLD). Twin kzone_entry_fire encodes the selection. (vec: v12_quick_engine.py:8428-8432)
TRADIER_RSI_ENTRY_LONG_TRADIER: Tradier-only in vector (v12:8791 MODE gate). Twin tradier_rsi_entry_fire encodes the gate (crypto never fires). (vec: v12_quick_engine.py:8791-8798)
['TRADIER_STOCH_ENTRY_LONG_TRADIER', 'TRADIER_STOCH_ENTRY_SHORT_TRADIER', 'TRADIER_STOCH_EXTREME_LONG_TRADIER', 'TRADIER_STOCH_EXTREME_SHORT_TRADIER']: Tradier-only in vector (v12:8799-8804 inside the MODE==tradier block). Twin tradier_stoch_entry_fire encodes the gate. (STOCH_CROSS_ENTRY_TRADIER itself IS hooked for its all-mode veto slice v12:9248. (vec: v12_quick_engine.py:8799-8804)
TRADIER_DC_POSITION_ENTRY_THRESHOLD: Tradier-branch-only in vector (v12:8675; crypto uses DC_POSITION_ENTRY_THRESHOLD, sibling-A owned). Twin daytrade_entry_fire encodes the selection; the daytrade elif hook reads it on tradier MODE only (vec: v12_quick_engine.py:8673-8679)
['TRADIER_DC_DAYTRADE_STOP_PCT', 'TRADIER_DC_DAYTRADE_TARGET_PCT']: DEAD in vector: assigned v12:12527-12528 but the fixed-% branches were DELETED (v12:13886-13887 USER SPEC — only DC-channel/ATR daytrade exits remain). Twin daytrade_pct_resolve returns the assigned v (vec: v12_quick_engine.py:12525-12532,13886-13887)
['TRADIER_DC_DAYTRADE_STOP_USE_DC_15M', 'TRADIER_DC_DAYTRADE_STOP_USE_DC4_15M', 'TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M', 'TRADIER_DC_DAYTRADE_TARGET_USE_DC4_15M']: DEAD in vector: field defs only (v12:4724-4727), zero functional reads repo-wide outside tradier_manage stocks-live (33345-33348). Twin daytrade_use_dc_flags returns the flags with no effect. Operator (vec: v12_quick_engine.py:4719-4727 + tradier_manage.py:33345-33348)
LR_BAND_LADDER_STOCH_EXTREME: No functional vector read (v12 defaults only: 4606/6118); reference is stocks-live tradier_manage:1543-1569. No ez parent (ez:59587 is a no-op stub; v15 pilots list it in _PRUNED_ORANGE). Twin lr_ladd (vec: tradier_manage.py:1501-1569 (no v12 predicate))
WIN_TRAIL_EROSION_PCT: Vec exit trigger (v12:13890: peak erosion >= peak*pct); live has no peak-trail parent (ez hits are allowlist-only). Twin win_trail_erosion_fire delivered; operator decides exit-flow placement. NOTE ve (vec: v12_quick_engine.py:12616,13890)
WT_D_BOUNCE_DD_STOP_ENABLED: No functional vector read in this checkout (v12 delegates to absent reduce_profit_lock.dd_bounce_stop_fires, v12:13810). Twin mirrors stocks-live tradier_manage:12091 (qty>=0.5, aug_px>0, price back t (vec: v12_quick_engine.py:13808-13811 + tradier_manage.py:12091)