| switch | cause | live vs vec | proposed fix |
|---|---|---|---|
| ABLATION_DISABLE_CHECK_NOLOSS | D | DIFFERENT (vec VEC_STUB-equivalent; stoc | Add a vec twin of ez_manage.check_no_loss_exit (ez_manage.py:3312) as an exit source in simulate_one, skipped when ABLATION_DISABLE_CHECK_NOLOSS; make the templ |
| ABLATION_DISABLE_ENTRY_RANKING | D | VEC_STUB (+LIVE_STUB stocks) | Needs vec twin of evaluate_ranking_momentum_trade as an OR entry source; could not determine whether NPZ carries the ranking inputs (Redis leaderboard) - if not |
| ABLATION_DISABLE_ENTRY_TECHNICAL | D | VEC_STUB (+LIVE_STUB stocks) | Add vec twin of evaluate_technical_indicator_signals as an OR entry source (default off=baseline), or declare crypto-live-only. |
| AUGMENTED_POSITIONS_GUARD_FLOOR_MULT | D | VEC_STUB (stocks: switch not live) | In simulate_one augment choke point (v12 ~14040) skip augment when pos n_augments>0 and live_pnl_pct < MULT*MIN_GAIN via lane_vec_gates2.augmented_guard_block_m |
| AUGMENT_FALLBACK_REDUCE_ENABLED | D | VEC_STUB; live also inert (field gap) | Vec: in simulate_one track peak pnl + last augment qty, call twin_reentry_staged.augment_fallback_fires/_reduce_frac and emit REDUCE. Live: populate position.la |
| AUGMENT_FALLBACK_REDUCE_PCT | D | VEC_STUB; also dependent on a default-of | Same as AUGMENT_FALLBACK_REDUCE_ENABLED; sweep PCT only with ENABLED=True. |
| BACKTEST_VALIDATED_GATES_TRADIER | D | PARTIAL (vec hard-gate body OFF by maste | Decide parity: run the live BV block in vec (entry_hard_gates.tradier_block) whenever BACKTEST_VALIDATED_GATES_TRADIER is True (needs per-symbol live overlays); |
| BB_BREAKOUT_ENABLED | D | VEC_STUB | Call alt_entries.check_bb_breakout_vec next to check_rz_breakout_vec (v12:9970) with bb_pct_b_{BB_BREAKOUT_TF}; decide how a live score boost maps to a boolean  |
| BB_FROZEN_STOP_ENABLED | D | VEC_STUB (stocks: DIFFERENT semantics, n | At position open in simulate_one store pos['ty_frozen_bb']=_ty_frozen_bb_arr[entry_bar] (array already built v12:12639); stocks rows should be dropped or a sepa |
| BB_RSI_STOCH_SCALP_ENABLED | D | VEC_STUB (and crypto/stocks live formula | Wire via alt_entries.check_bb_rsi_stoch_scalp_vec next to v12:9970 with 15m/1h key remap and separate crypto vs stocks thresholds; again needs a score->entry ma |
| CONFLUENCE_MODE_ENABLED | D | DIFFERENT | Replace v12:9222-9228 by a vectorized twin_gates_sizing_a.confluence_blocks (3 real votes) applied to the FINAL entry_sig (same pattern as b7 ENTRY_CHOKE_GATES_ |
| CYCLE_TP_TIERED_ENABLED | D | VEC_STUB | Implement tier reduce in simulate_one reduce loop using vec_decisions.twin_sizing_reduce.cycle_tp_tier (pure fn exists, per-position fired-set); will change eve |
| DELTA_ENGINE_ENABLED | D | DIFFERENT (live master gates exits/gate/ | Wire a DeltaTracker exit twin: call vec_decisions.delta_exit_top (already imported, v12:124) / process_position_crypto__e1_wt_delta_exit inside compute_exit_sig |
| DELTA_GATE_AUGMENT | D | DIFFERENT for stocks (live honours the g | twin_p0_crypto_a._delta_honor(cfg): return True when MODE=='tradier' (or flip VEC_HONOR default to True in tradier defaults); crypto stays dead to match live. |
| DELTA_GATE_OPEN | D | DIFFERENT (stocks live honours; vec iner | Honour flag on for MODE=='tradier' (as for DELTA_GATE_AUGMENT) and apply delta_gate_open_allows on the FINAL entry_sig next to the KG b6 mask (v12:~12455) and o |
| DELTA_GATE_REENTRY | D | DIFFERENT for stocks; SAME-dead for cryp | Honour flag on for MODE=='tradier' (twin_p0_crypto_a._delta_honor). |
| DELTA_HTF_GATE | D | DIFFERENT (vec veto exists only pre-OR / | Apply lane_vec_gates2.delta_htf_block_mask to the FINAL entry_sig and _wd_open (crypto OPEN/REENTRY exempt list as live) in simulate_one next to the other choke |
| DELTA_PYRAMID_MAX | D | VEC_STUB | Wire twin_vec_special.delta_pyramid_adds_allowed as the adds cap in the augment loop of simulate_one AND add a pyramid_long/short trigger twin (DeltaTracker.pyr |
| DYNAMIC_SCORE_AUGMENT_ENABLED | D | SAME (dead on both sides) | Needs spec: define 'dynamic score augment' (score-driven augment gate), implement once in vec_decisions + live augment path with kill switch. Until then templat |
| EMA_50_200_FILTER_ENABLED | D | SAME (both sides data-starved: indicator | Vec: in live_kindergarten_stocks.pass_mask (and twin_gates_sizing_a.kg_entry_allowed) derive ema_50_above_200_{tf} = ema_50_{tf} > ema_200_{tf} from existing NP |
| EMA_50_200_TFS | D | SAME (both sides data-starved: indicator | Vec: in live_kindergarten_stocks.pass_mask (and twin_gates_sizing_a.kg_entry_allowed) derive ema_50_above_200_{tf} = ema_50_{tf} > ema_200_{tf} from existing NP |
| EMA_50_200_TIMEFRAME | D | SAME (both sides data-starved: indicator | Vec: in live_kindergarten_stocks.pass_mask (and twin_gates_sizing_a.kg_entry_allowed) derive ema_50_above_200_{tf} = ema_50_{tf} > ema_200_{tf} from existing NP |
| EMA_9_21_FILTER_FILTER_TF | D | DIFFERENT (vec ignores the knob on both  | Add vec_decisions.twin_yellow_filters.kg_stocks_tfs_restrict(family_tfs, FILTER_TF) inside live_kindergarten_stocks.pass_mask and kg_entry_gate.ema921_pass_vec; |
| EMA_DIST_ENTRY_ENABLED | D | DIFFERENT (live crypto = sizing factor;  | Add the live sizing factor to vec sizing (compute_regime/size multiplier path: +30 factor to the sizing_score twin) instead of/in addition to the entry block; k |
| EMA_DIST_SHORT_THRESHOLD | D | DIFFERENT (sizing vs entry) | Same as EMA_DIST_ENTRY_ENABLED (sizing twin). |
| EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED | D | VEC_STUB (crypto) / LIVE_STUB (stocks) | In simulate_one reentry fire block (the HARDCODED_RALLY_REENTRY site, v12 ~13300) gate/replace with vec_decisions.guaranteed_price_cross_reentry.check_guarantee |
| EZ_REENTRY_PRICE_CROSS_PCT | D | VEC_STUB | Same call site as GUARANTEE_ENABLED: apply `crossed = px > exit*(1+EZ_REENTRY_PRICE_CROSS_PCT)` to the reentry fire. |
| FH_MOMENTUM_ENABLED | D | DIFFERENT (stocks: wrong flag read; cryp | vec: in compute_reentry_blocks read FH_MOMENTUM_ENABLED (not the TRADIER_ alias) for MODE tradier (or make the template row TRADIER_FH_MOMENTUM_ENABLED); remove |
| FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO | D | VEC_STUB (dead module) | In simulate_one per-bar position loop next to the DC stop: `if FROZEN_ACTIVATION_STOP_ENABLED and (gain_pct <= FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO or (frozen_dc_le |
| FROZEN_ACTIVATION_STOP_ENABLED | D | VEC_STUB | Same fix as FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO (one call site covers both). |
| GAP_CLOSE_MOC_FORCE_MOC_AT_CLOSE | D | DIFFERENT (window by bar index modulo 26 | Compute the window from timestamps (ET 14:30-16:00 -> last 6 RTH bars) instead of `_i % _bars_per_day` in the GAP sentinel block (v12 ~12010-12020) and derive _ |
| GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT | D | PARTIAL/DIFFERENT (avg from rolling bars | Same window fix; also restrict template to SHORT sheets. |
| GAP_RISK_REENTRY_ENABLED | D | VEC_STUB | Call gap_risk_reentry_vec(npz,n,cfg,is_long) in simulate_one's reentry fire block (flat, has_closed_before, last exit_reason GAP_RISK_EXIT*) like the target_dc_ |
| GAP_RISK_REENTRY_REQUIRE_TREND | D | VEC_STUB | Same single call site as GAP_RISK_REENTRY_ENABLED. |
| K_ZONE_VETO_ENABLED_TRADIER | D | VEC_STUB | Apply live_stocks_entry.live_veto (or kzone_veto_ok_vec) as an AND on blocks['B_WT_DC_LIVE'] inside the tradier _wl_ok stack (v12_quick_engine ~L9591-9609) so t |
| LONG_STOCH_CHASE_BLOCK | D | VEC_STUB | Same as K_ZONE_VETO: AND live_veto's chase-block term onto B_WT_DC_LIVE in the tradier _wl_ok stack (v12 ~L9591-9609); then re-measure on a sym with k1h>70 stre |
| LR_PCTB_D_LONG_ENTRY_ENABLED | D | VEC_STUB | Add lr_pct_b_D (and lrL_pct_b_D) to backtest_v8_precompute; add blocks['B_LR_PCTB_D'] = check_lr_pctb_d_long_vec(...) in compute_reentry_blocks for both venues  |
| LR_PCTB_D_LONG_ENTRY_THRESHOLD | D | VEC_STUB | Same fix as LR_PCTB_D_LONG_ENTRY_ENABLED (NPZ field + entry block). |
| NOLOSS_BYPASS_WT_5OF5_ENABLED | D | PARTIAL | Parity: derive vec NOLOSS hold from live NOLOSS_MIN_PROFIT_PCT_TRADIER (stocks strict accounts) / NOLOSS_DC4H gate (crypto) and give the exit_sig close a specif |
| PEAK_GIVEBACK_BE_EROSION_FILTER_TF | D | DIFFERENT | Decide the real semantics: either implement live crypto erosion-confirm at the WIN_TRAIL_EROSION/PEAK_GIVEBACK exit (ez_manage ~L60914 -> real gate) and keep th |
| R3_HTF_FLIP_EXIT_ENABLED | D | VEC_STUB | Give R3 its own close in the walk (like MTF_ATR_TRAIL / WT_FINAL_EXIT_INFO): when r3 mask[i] and held_bars>=min_hold, append CLOSE reason 'R3_HTF_FLIP' (with th |
| RECENT_REDUCTION_GUARD_WINDOW_S | D | PARTIAL | Set QuickConfig RECENT_REDUCTION_GUARD_ENABLED=True (live parity, cat_side/QuickConfig accordance) and extend the twin in vec_decisions/wirec_execnow_guards.py  |
| REENTRY_BAR_TURN_ENABLED | D | VEC_STUB | In the v12 loop reentry `fire` block (~v12:13405-13440) call reentry_confirmation_gate (or its _vec) for price-cross pathways with a 15m high/low bar-turn proxy |
| REENTRY_BAR_TURN_REQUIRE_BOTH | D | VEC_STUB | Same call site as REENTRY_BAR_TURN_ENABLED; pass require_both into the 15m bar-turn proxy. |
| REENTRY_CONFIRMATION_GATES_ENABLED | D | VEC_STUB | Call reentry_confirmation_gate (15m proxies for 3m legs) in the v12 reentry fire block for non-rally pathways and, for stocks, model only the RECOVERY_AUG k_5m  |
| REENTRY_SMA200_BACKUP_ENABLED | D | LIVE_STUB | Decide first: either wire live (call check_guaranteed_price_cross_reentry in ez_reentry.enforce_price_cross_reentry) + vec (call _vec in the reentry fire path)  |
| REENTRY_STOCH_K_MAX_LONG | D | VEC_STUB | Apply guaranteed_price_cross_reentry.reentry_confirmation_gate (vectorised: k_15m/k_1h/WT-cross legs) to the crypto reentry fire path in simulate_one (replace b |
| REENTRY_STOCH_K_MIN_SHORT | D | VEC_STUB | Same as REENTRY_STOCH_K_MAX_LONG. |
| REGIME_EXIT_TRENDING_THRESHOLD | D | DIFFERENT | Wire live classify_regime hysteresis into the vec (stateful per-bar mode with dwell REGIME_MIN_DWELL_BARS and ex-threshold) under REGIME_DETECTION_ENABLED, or r |
| RSI2_ENABLED | D | VEC_STUB | Fix key: read rsi_2_1h (or mirror live fallback chain rsi2_1h -> rsi_1h exactly as twin_gates_sizing_a.rsi2_fires:49) at v12:8503; AND relocate route per ENTRY_ |
| RSI2_ENTRY_THRESHOLD | D | VEC_STUB | Same fix as RSI2_ENABLED (key + route placement). |
| RSI_ENTRY_LONG_TRADIER | D | VEC_STUB | Stocks: add vec twin of calculate_signal_score rsi_long/rsi_short = (mfi_4h<thr / >thr) OR-trigger in the stocks entry stack (after confirming runtime activity) |
| RSI_ENTRY_MAX_LONG | D | PARTIAL | Apply the RSI_ENTRY_GATE mask on the FINAL entry_sig (after the B_WT_DC_LIVE OR, as the live gate is applied to every open); add data/switch_dependencies.json m |
| RSI_ENTRY_MIN_SHORT | D | PARTIAL | Same as RSI_ENTRY_MAX_LONG; also clamp template candidates to <=100. |
| RSI_ENTRY_SHORT_TRADIER | D | VEC_STUB | Same as RSI_ENTRY_LONG_TRADIER. |
| SATOSHIT_LONG_HA_STREAK_MAX | D | VEC_STUB | Add vec twin of ez_satoshit.satoshit_entry_signal gated by SATOSHIT_ENABLED (15m indicators) as final OR into entry_sig for crypto accounts in SATOSHIT_ACCOUNTS |
| SATOSHIT_MIN_VOTES | D | VEC_STUB | Add vec twin of ez_satoshit.satoshit_entry_signal gated by SATOSHIT_ENABLED (15m indicators) as final OR into entry_sig for crypto accounts in SATOSHIT_ACCOUNTS |
| SATOSHIT_SHORT_BB_PCTB_MIN | D | VEC_STUB | Add vec twin of ez_satoshit.satoshit_entry_signal gated by SATOSHIT_ENABLED (15m indicators) as final OR into entry_sig for crypto accounts in SATOSHIT_ACCOUNTS |
| SBA_ADX_MAX | D | DIFFERENT | sba_bounce.sba_bounce_score_vec: use bb_width_pct_{tf}*100 (or (bb_upper-bb_lower)/mid*100) instead of absolute bb_width_{tf}; move the `_base_entry &= _sba_bou |
| SBA_MIN_SCORE | D | DIFFERENT | Same fixes as SBA_ADX_MAX (units + final-stage AND). |
| STOCH_CROSS_3M_EXIT_ENABLED | D | PARTIAL (same predicate; base TF clamped | Give the stoch-cross mask its OWN reason tag in the _tech_reason hook (near v12:14504 WT_FINAL_EXIT_INFO pattern) so it is exempt from the VEC_EXIT_SIG_IS_TRIGG |
| STOCH_CROSS_ENTRY_TRADIER | D | DIFFERENT (vec: global AND over all entr | Move the cross into the tradier stoch leg only (B_TRADIERSTOCH := cross event when ON, MODE==tradier), delete the global extra_ok AND (v12:9368-9376) and the cr |
| TF_ALIGNMENT_MIN_TOTAL | D | LIVE_STUB (stocks) / VEC no-op (crypto) | Stocks: implement a real live read (tradier_manage fresh-open veto) + add to live_veto; crypto: enable vec twin for MODE crypto behind TF_ALIGNMENT_MIN_TOTAL_LI |
| TF_FOCUS_ENTRY_HARD_GATE | D | VEC_STUB (crypto live has a real active  | Add predicate in vec_decisions (e.g. twin_p0_crypto_b) returning (wt1_1h>wt2_1h)&(wt1_4h>wt2_4h) long / mirrored short, call once in compute_entry_signals extra |
| TF_FOCUS_WEIGHT | D | VEC_STUB and live cosmetic | Either remove from template (no function) or define a real score bonus consumed by a real score; do not wire as pure gate (weight irrelevant). |
| TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION | D | DIFFERENT (live gate fail-open + bonus o | Decide live semantics first (add dc_width_1h_prev indicator or drop switch); then add daytrade-DC entry as a real open source (live DaytradeWing candidate scan) |
| TRADIER_DC_DAYTRADE_TARGET_PCT | D | VEC_STUB (deliberate) | Template should drop this switch OR user must decide to restore a fixed-% daytrade target exit (contradicts DC-channel-only spec, BIBLE §15); if restored, apply |
| TRADIER_DC_POSITION_ENTRY_THRESHOLD | D | DIFFERENT (vec entry trigger vs live bon | Decide whether daytrade DC entry is a live open source (DaytradeWing); if yes model it as OR open-source in vec/live_stocks_entry; else mark switch bonus-only. |
| TRADIER_ENTRY_SCORE_THRESHOLD | D | DIFFERENT (key/semantic mismatch) | In vec map TRADIER_ENTRY_SCORE_THRESHOLD onto the WT_DC score floor (v12:9573) like live alias :5915, keep mfi_D arm separate; then prove WT_DC entries drop. |
| TRADIER_FH_MOMENTUM_DC_CONFIRM | D | PARTIAL (same predicate; vec only reache | Add B_FHMOMENTUM (and fh_momentum_fires predicate from twin_gates_sizing_a) to live_stocks_entry.SOURCE_BLOCKS as an OR open-source; enable STOCKS_LIVE_ENTRY_ST |
| TRADIER_FH_MOMENTUM_DC_MAX_LONG | D | PARTIAL (same predicate; vec only reache | Add B_FHMOMENTUM (and fh_momentum_fires predicate from twin_gates_sizing_a) to live_stocks_entry.SOURCE_BLOCKS as an OR open-source; enable STOCKS_LIVE_ENTRY_ST |
| TRADIER_FH_MOMENTUM_ENABLED | D | PARTIAL (same predicate; vec only reache | Add B_FHMOMENTUM (and fh_momentum_fires predicate from twin_gates_sizing_a) to live_stocks_entry.SOURCE_BLOCKS as an OR open-source; enable STOCKS_LIVE_ENTRY_ST |
| TRADIER_FH_MOMENTUM_MFI_CONFIRM | D | PARTIAL (same predicate; vec only reache | Add B_FHMOMENTUM (and fh_momentum_fires predicate from twin_gates_sizing_a) to live_stocks_entry.SOURCE_BLOCKS as an OR open-source; enable STOCKS_LIVE_ENTRY_ST |
| TRADIER_FH_MOMENTUM_WINDOW_MINUTES | D | PARTIAL (same predicate; vec only reache | Add B_FHMOMENTUM (and fh_momentum_fires predicate from twin_gates_sizing_a) to live_stocks_entry.SOURCE_BLOCKS as an OR open-source; enable STOCKS_LIVE_ENTRY_ST |
| TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER | D | DIFFERENT (TF 15m vs live 4h, no turn co | Make vec leg mirror live (stoch_k_4h<thr & >stoch_d_4h) as B_KZONE_TRADIER reading TRADIER_K_ZONE_*; include in SOURCE_BLOCKS. |
| TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER | D | DIFFERENT | As long leg; additionally fix hard_short_ok data-absent default (v12:~9470 _final_h) so absent field is fail-open not all-False. |
| TRADIER_MFI_ENTRY_LONG_ENABLED | D | SAME predicate (veto); not present in li | Add tradier_mfi_long_blocks equivalent to live_stocks_entry.live_veto; template: restrict switch to STOCKS_LONG. |
| TRADIER_MI_ENTRY_ENABLED_TRADIER | D | PARTIAL (5m vs 15m k) | Add B_MI_ENTRY to SOURCE_BLOCKS as OR source; enable live stack in stocks baseline. |
| TRADIER_RSI_ENTRY_SHORT_TRADIER | D | PARTIAL (twin default rel-vol min 2.4 vs | Add B_TRADIERRSI (short leg) to SOURCE_BLOCKS as OR source; align rel-vol default; fix hard_short_ok absent-field default. |
| TRADIER_RSI_SHORT_REL_VOLUME_MIN | D | PARTIAL (default mismatch) | As RSI_ENTRY_SHORT; reconcile default 1.2 vs 2.4 first (check config_tradier value). |
| VWAP_FILTER_ENABLED | D | LIVE_STUB (no live consumer at all) | Decide: either delete the row (no live behaviour exists) or wire a real live VWAP veto in the active entry path (tradier process_position / ez execute path) AND |
| WT_CROSS_EXIT_REQUIRE_15M_CONFIRM | D | SAME for crypto; stocks LIVE_STUB | Remove STOCKS_LONG/SHORT rows (no stocks live consumer) or implement a tradier WT_CROSS_EXIT leg live+vec; crypto rows are fine. |
| WT_DC_DC_TF | D | LIVE_STUB (assigned-only) / vec only in  | Either drop row (no live consumer) or wire live read first; vec would then need a post-OR dc_position-TF mask on final entry_sig. |
| WT_DC_FINAL_SCORE_MAX | D | DIFFERENT (field absent from npz; two ve | Precompute final_score_norm_lt (LT trend score) into the NPZ (backtest_v8_precompute) and apply it in B_WT_DC_LIVE _wl_ok for shorts; no proxy allowed (NO-LIES) |
| WT_DC_LONG_ENABLED | D | DIFFERENT (live crypto boycotts all LONG | vec_decisions predicate wtdc_side_boycott_mask(cfg,is_long) returning zeros when the side switch is False (live BOYCOTT), applied to the final entry_sig for cry |
| WT_DC_SHORT_ENABLED | D | DIFFERENT for crypto; PARTIAL for stocks | Same as WT_DC_LONG_ENABLED (side boycott mask on final entry_sig for crypto). |
| WT_DC_STOCH_THRESHOLD_LONG | D | LIVE_STUB (stocks) / flag-off (crypto);  | Live first: either wire stocks gate or set crypto *_LIVE_ENABLED default True; vec: post-OR stoch mask on final entry_sig (vec_decisions predicate) gated by the |
| WT_DC_STOCH_THRESHOLD_SHORT | D | LIVE_STUB (stocks) / flag-off (crypto) | As LONG. |
| WT_DC_TF_HTF2 | D | DIFFERENT (vec key-case bug vs live lowe | In v12_quick_engine ~9495 build key as f'wt1_{label}' for non-D/W labels (npz key names wt1_15m/wt1_1h/wt1_4h; D and W upper-case) and mirror in twin_p0_crypto_ |
| WT_EXIT_MIN_TFS_TRADIER | D | DIFFERENT (vec ignores master WT_EXIT_VE | Gate the lane mask by WT_EXIT_VETO_ENABLED_TRADIER (register it as master in switch_dependencies.json) and give the close a specific reason (WT_EXIT_TFS_VARFIX) |
| WT_VEL_DECAY_EXIT_ENABLED | D | LIVE_STUB for crypto; PARTIAL for stocks | Remove crypto row (no live reader) or add a crypto live read in the ez exit chain AND give vec a specific-reason close. |
| ABLATION_DISABLE_ENTRY_REVERSAL | E | VEC_STUB (moot: live path inactive) | No vec fix needed unless REV_MODE gets simulated; remove row from sweep or gate it on REV_MODE. |
| BANDAID_OFF_LOSER_RECOVER_PCT | E | VEC_STUB; live path inactive | None (live-only hedge management); drop from sweep or re-add only with hedge simulation. |
| DELTA_EXIT_MANDATORY_REENTRY_ENABLED | E | VEC_STUB (live effect cosmetic/wall-cloc | None cheap. If desired model the stocks cooldown re-gate bypass in vec reentry cooldown_bars; crypto effect cosmetic. |
| GUARANTEED_REENTRY_DELTA_GATE_ENABLED | E | VEC_STUB | Delete the `_ =` stub; mark cell not-sweepable (grey) or implement check_reentry_delta_tolerant twin on 15m delta if owner accepts the approximation (no 3m/1m i |
| GUARANTEED_REENTRY_K_FAVORABLE_HIGH | E | VEC_STUB | Same as DELTA_GATE: delete stub, grey the cell; a 15m-K twin would be an approximation (needs owner decision). |
| GUARANTEED_REENTRY_K_HIGH_BLOCK | E | VEC_STUB | Same as DELTA_GATE. |
| GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN | E | VEC_STUB (hedge not simulated) / LIVE_ST | Not fixable in a single-symbol vec; grey the cell on all four sheets or move to the portfolio-level backtest. |
| GUARANTEED_REENTRY_STRICT_CONFIRMATION | E | VEC_STUB | Same as DELTA_GATE. |
| GUARANTEED_REENTRY_TIGHT_STOP_ENABLED | E | VEC_STUB | Only after vec has a faithful GUARANTEED_REENTRY source: add per-position tag + gain/age stop in the position loop; otherwise grey the cell. |
| GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S | E | VEC_STUB | Same as TIGHT_STOP_ENABLED. |
| GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S | E | VEC_STUB | Same as TIGHT_STOP_ENABLED. |
| GUARANTEED_REENTRY_TIGHT_STOP_PCT | E | VEC_STUB | Same as TIGHT_STOP_ENABLED. |
| HTF_EXIT_VETO_ENABLED | E | PARTIAL (crypto predicate identical, but | Needs 3m data in the NPZ (w2-data3m sidecar) so dc4_lvl is real; until then grey the three HTF_EXIT_VETO_* rows (crypto) and remove from STOCKS sheets (no live  |
| HTF_EXIT_VETO_MAX_LOSS_PCT | E | PARTIAL (see HTF_EXIT_VETO_ENABLED) | Same as HTF_EXIT_VETO_ENABLED. |
| HTF_EXIT_VETO_MIN_ALIGNED | E | PARTIAL (see HTF_EXIT_VETO_ENABLED) | Same as HTF_EXIT_VETO_ENABLED. |
| MIN_POSITION_SIZE | E | VEC_STUB | Model min-qty dust remainder: replace quick_reduce_sources.DUST_FRAC with MIN_POSITION_SIZE/px in the reduce hooks (v12 ~L14140-14160) and floor open/augment no |
| PARITY_DISABLE_NON_VECTORIZABLE | E | DIFFERENT | Remove from the swept template (master parity switch) or define its vec meaning as 'enable each non-vectorizable family twin' - not recommended. |
| REENTRY_BOUNCE_BAR_GR_ENABLED | E | VEC_STUB | Not wireable faithfully at 15m floor; either drop from vec sweep/mark 'live-only', or add a 15m-bar-turn proxy twin in reentry_bounce_after_correction + call fr |
| REENTRY_BOUNCE_BAR_GR_MIN_TFS | E | VEC_STUB | Same as REENTRY_BOUNCE_BAR_GR_ENABLED. |
| REENTRY_CHURN_GUARD_ENABLED | E | VEC_STUB | Live-only daemon behaviour; if it must be swept, add a 15m twin (breakout vs dc_high_15m within N bars of last exit) at the v12 reentry fire site and document i |
| REENTRY_CHURN_GUARD_USE_4BAR | E | VEC_STUB | Same as REENTRY_CHURN_GUARD_ENABLED. |
| REENTRY_DC_MID_PULLBACK_ENABLED | E | VEC_STUB | Live-only at 3m; mark as such or add 15m/4h-channel twin (dc_pos_15m, width_4h) in reentry_bounce_after_correction + call in obligatory path. |
| REENTRY_DC_MID_PULLBACK_WIDTH_MAX | E | VEC_STUB | Same as REENTRY_DC_MID_PULLBACK_ENABLED. |
| REENTRY_K_RESET_GR_ENABLED | E | VEC_STUB | Could be proxied at 15m (K at 15m/1h/4h/D exists in NPZ; replace wt_3m by wt_15m) in reentry_bounce_after_correction + obligatory path; parity-test before sweep |
| REENTRY_K_RESET_GR_MIN_TFS | E | VEC_STUB | Same as REENTRY_K_RESET_GR_ENABLED. |
| REENTRY_K_RESET_TF | E | VEC_STUB | Same as REENTRY_K_RESET_GR_ENABLED. |
| REENTRY_PULLBACK_GR_SCORE_ENABLED | E | VEC_STUB | Not worth wiring for the 15m vec: live predicate uses wt1_3m/wt2_3m (3m bars), unavailable in the 15m-compacted NPZ. Drop the stocks rows from TEMPLATE_STOCKS_S |
| REENTRY_PULLBACK_GR_SCORE_MIN | E | VEC_STUB | As _ENABLED: needs 3m bars; keep LIVE_ONLY or implement 15m proxy in obligatory_reentry_vec.fires with parity proof. |
| REENTRY_SMA200_GR_CONTINUATION_ENABLED | E | VEC_STUB | Same as PULLBACK: LIVE_ONLY (needs 3m); remove stocks row. |
| TREND_EXIT_SCORE_FLIP | E | DIFFERENT (live dormant via TREND_ACCOUN | Only if a trend-account mode is wanted: gate lane trend_flip_mask by account in TREND_ACCOUNTS and attach a specific TREND_REVERSAL_EXIT reason (so exit_sig clo |
| TREND_MIN_GAIN_EXIT | E | VEC_STUB (result discarded) and LIVE dor | Drop the discarded read; consume trend_min_gain_mask as the AND-leg of the TREND flip exit (gain_arr from the loop) only for TREND_ACCOUNTS mode; otherwise mark |
| V12_PARITY_DISABLE_NON_VECTORIZABLE | E | DIFFERENT by design (vec assigns a dead  | No vec predicate possible (it is a live-side mask over non-simulated knobs). Remove from sweep templates or label NOT_SWEEPABLE. |
| WT_DC_K5M_HARD_ENABLED | E | PARTIAL/DIFFERENT (live 5m vs vec 15m pr | Not wireable without 5m data in the vector system (user order 2026-10-01: no 3m/5m). Mark template row N/A. |
| WT_DC_K5M_MIN_SHORT_HARD | E | PARTIAL/DIFFERENT (as K5M_HARD_ENABLED) | N/A until 5m data exists; mark row N/A. |