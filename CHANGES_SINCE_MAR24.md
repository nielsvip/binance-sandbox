# Changes Since 2026-03-24 — Complete Inventory

## Status Summary
- 55 files MODIFIED (vs git baseline)
- 40 files DELETED
- 227 files NEWLY ADDED (untracked)

## Core Trading Files (MODIFIED)
- `CLAUDE.md`
- `LOCKED_FILES.md`
- `config.py`
- `config_tradier.py`
- `ez_backup.py`
- `ez_disk_cleanup.py`
- `ez_indicators.py`
- `ez_klines.py`
- `ez_launcher.py`
- `ez_manage.py`
- `ez_market_data.py`
- `ez_positions.py`
- `ez_positions_active.py`
- `ez_positions_quick.py`
- `ez_positions_realtime.py`
- `ez_positions_service.py`
- `ez_positions_watchdog.py`
- `ez_prices.py`
- `ez_rankings.py`
- `gateway_data_broadcaster.py`
- `nightly_lab.py`
- `pa.py`
- `paper_tournament.py`
- `push.py`
- `rsync_from_gateway_continuous.sh`
- `rsync_gateway_positions.sh`
- `rsync_gateway_to_others.sh`
- `rsync_indicators_to_server.sh`
- `rsync_klines_continuous.sh`
- `rsync_klines_from_server.sh`
- `rsync_market_data_from_server_continuous.sh`
- `rsync_market_data_to_server_continuous.sh`
- `rsync_poly_arb.sh`
- `rsync_positions_to_server_continuous.sh`
- `rsync_positions_to_server_enhanced.sh`
- `rsync_sweep_results.sh`
- `rsync_to_server_continuous.sh`
- `run_with_watchdog.sh`
- `run_with_watchdog_LINUX.sh`
- `server.py`
- `setup_ssh_tunnels.py`
- `sh.sh`
- `start_everything_1.command`
- `start_everything_3.command`
- `start_terminals.sh`
- `trade_analytics.py`
- `trade_monitor_ai.py`
- `tradier_api.py`
- `tradier_indicators.py`
- `tradier_manage.py`
- `tradier_positions.py`
- `tradier_rankings.py`
- `tradier_watchdog_cron.sh`
- `tradier_webhook_bridge.py`
- `utils.py`

## Deleted Files
- `backtest_ablation.py`
- `backtest_ablation_tradier.py`
- `backtest_candle_patterns.py`
- `backtest_continuous_sweep.py`
- `backtest_deep_crypto.py`
- `backtest_deep_tradier.py`
- `backtest_deep_tradier_p2.py`
- `backtest_factory.py`
- `backtest_full_matrix.py`
- `backtest_hedge_vs_ratio.py`
- `backtest_marathon.py`
- `backtest_mover_noloss.py`
- `backtest_no_loss.py`
- `backtest_sentiment_monitor.py`
- `backtest_tradier_candle.py`
- `backtest_tradier_noloss.py`
- `continuous_param_optimizer.py`
- `ez_backtest_tf_compare.py`
- `ez_backtest_tiered.py`
- `ez_breakout_agent.py`
- `ez_eval_agent.py`
- `ez_haiku_agent.py`
- `ez_haiku_bridge.py`
- `ez_klines_htf.py`
- `ez_logs_analyzer.py`
- `ez_manage_sandbox.py`
- `ez_manipulation_detector.py`
- `ez_mem_watchdog.py`
- `ez_metric_sweep.py`
- `"ez_positions_`
- `ez_positions_new.py`
- `ez_positions_quick_sandbox.py`
- `ez_positions_service_sandbox.py`
- `ez_practical_sizing_backtest.py`
- `ez_sentiment_monitor.py`
- `ez_status.py`
- `ez_symbol_performance.py`
- `ez_test_run.py`
- `ez_tournament.py`
- `tradier_hedge_engine.py`

## New Files (Recent Work)
- `100.md`
- `ABLATION_REPORT.md`
- `BACKTEST_INVENTORY.md`
- `BACKTEST_V3_ARCHITECTURE.md`
- `CHANGES_SINCE_MAR24.md`
- `COPILOT_STATUS.md`
- `HEDGE_BANDAID_BACKTEST.md`
- `SERVER_LOCKS.md`
- `SWEEP_ORDER.md`
- `TESTING_SCHEDULE_20260324.md`
- `ablation_backtest.py`
- `ablation_backtest_v3.py`
- `ablation_v2_dropone.py`
- `adaptive_inf.py`
- `adaptive_optimizer.py`
- `adaptive_regime.py`
- `analyze_dc5m_losers.py`
- `analyze_master_revalidation.py`
- `analyze_tournament_results.py`
- `analyze_v5_trades.py`
- `analyze_v8_pnl.py`
- `analyze_wt_dc_entries.py`
- `analyze_wt_dc_exits.py`
- `auto_apply_winners.py`
- `backfill_klines.py`
- `backtest_btc_trio_spread.py`
- `backtest_combined_system.py`
- `backtest_congress_trades.py`
- `backtest_derived_strategies.py`
- `backtest_entry_exit_combined.py`
- `backtest_fill_kline_gaps.py`
- `backtest_gold_spread.py`
- `backtest_mstr_ibit_options_spread.py`
- `backtest_mstr_ibit_spread.py`
- `backtest_queue_server.sh`
- `backtest_redzone_perpetual.py`
- `backtest_redzone_stock_sweep.py`
- `backtest_redzone_sweep.py`
- `backtest_scorer_5cond.py`
- `backtest_status_report.sh`
- `backtest_uso_bno_options_spread.py`
- `backtest_uso_bno_spread.py`
- `backtest_v5_analyze.py`
- `backtest_v5_engine.py`
- `backtest_v5_full_tradier.py`
- `backtest_v5_harness.py`
- `backtest_v5_sweep.py`
- `backtest_v8_engine.py`
- `backtest_v8_harness.py`
- `backtest_v8_interpolate_3m.py`

---

## FEATURE CHANGES IN LOCKED FILES (verified against current state)

### ez_manage.py (lines: $(wc -l < ez_manage.py))
**Locked features present:**
- ✅ UNIVERSAL_NOLOSS_GATE (5 refs)
- ✅ EXIT_HARD_MAX_LOSS_CAP_ENABLED (1 refs)
- ✅ EXIT_MARKET_SPIKE_REDUCE_ENABLED (2 refs)
- ✅ EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED (1 refs)
- ✅ EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED (1 refs)
- ✅ HEDGE_TRADEABLE_ADD_ETA (1 refs)
- ✅ execute_dual_hedge (5 refs)
- ✅ MOMENTUM_RIDER (36 refs)
- ❌ TRIPLE_SIREN (MISSING!)
- ✅ tradeable_keys (70 refs)

### ez_positions_quick.py
- ✅ EXIT_PREEMPTIVE_BREAKEVEN_ENABLED (1 refs)
- ✅ WT_GATE_BYPASS_RZ (1 refs)
- ✅ wt_3m (7 refs)
- ✅ HEDGE_PROFIT_PROTECT (2 refs)
- ❌ PARABOLIC_EXHAUSTION_EXIT (MISSING!)
- ✅ _hedge_entry_is_valid (6 refs)
- ✅ GAIN_EROSION (6 refs)
- ✅ STRUCTURAL_RANGE_SHIFT_EXIT (1 refs)

### config_tradier.py (ablation winners applied)
- Line 219:     ENTRY_ZONE_LONG
- Line 221:     ENTRY_MIN_ALIGNMENT
- Line 634:     WT_DC_ENTRY_THRESHOLD
- Line 697:     STRUCTURAL_RANGE_SHIFT_EXIT

### tradier_manage.py
- ✅ STRUCTURAL_RANGE_SHIFT_EXIT (1 refs)
- ✅ WT_CROSSUNDER_FINAL (2 refs)
- ✅ _ltf_down (4 refs)
- ✅ _15m_confirm (4 refs)
- ✅ _htf_against (4 refs)
- ✅ TRADIER_MIN_HOLD_MINUTES (1 refs)
- ✅ SATOSHIT_ENABLED_TRADIER (7 refs)
- ✅ DELTA_ENTRY_ENABLED (1 refs)

### V8 Backtest System
- backtest_v8_engine.py: _use_real_eta = True (1 refs)
- backtest_v8_engine.py: V8_BYPASS_SHOULD_ENTER (2 refs)
- backtest_v8_engine.py: Using REAL execute_trade_action (1 refs)
- backtest_v8_engine.py: tradeable_long (1 refs)
- backtest_v8_engine.py: tradeable_short (1 refs)

