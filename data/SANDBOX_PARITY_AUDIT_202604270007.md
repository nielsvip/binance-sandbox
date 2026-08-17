# Sandbox Parity Audit — 202604270007

Per CLAUDE.md SANDBOX PARITY section. MacBook = source of truth. Sandboxes = S1 (157.180.125.52) + S2 (204.168.181.211).

## Summary

- **Total files audited**: 109
- **MB-tracked files**: 72
- **OK (match across MB+S1+S2)**: 72
- **Pre-fix DRIFT count**: 15 (9 missing on S1, 6 missing on both)
- **Post-fix DRIFT count**: 0
- **Sandbox-only (legitimately differ)**: 37

## Actions taken

- Synced 9 files from MB to S1 (were S1-MISSING, S2-OK):
  ez_breakout_hunter.py, ez_copilot.py, ez_disk_watchdog.py, ez_divergence_monitor.py,
  ez_launcher.py, ez_outlier_hunter.py, ez_positions_active.py, ez_regime.py,
  tradier_premarket_scanner.py

- Synced 6 files from MB to BOTH S1+S2 (were missing on both):
  backtest_v8_verify_daily.py, ez_reentry_vectorized.py, wt_dc_delta_backtest.py,
  wt_dc_delta_engine.py, wt_dc_oos_paper_test.py, wt_dc_research.py

## Sandbox-only files (NOT touched per CLAUDE.md "Files that LEGITIMATELY differ")

- ez_autoresearch.py
- ez_autoresearch_monitor.py
- ez_backtest_tf_compare.py
- ez_backtest_tiered.py
- ez_breakout_agent.py
- ez_double.py
- ez_gain_protector.py
- ez_gap_filler.py
- ez_indicators_merger.py
- ez_key_levels.py
- ez_key_levels_monitor.py
- ez_klines_htf.py
- ez_logs_analyzer.py
- ez_loss_mitigator.py
- ez_manage_sandbox.py
- ez_manipulation_detector.py
- ez_mark_prices.py
- ez_news_scanner.py
- ez_positions_backup.py
- ez_positions_backup_account.py
- ez_positions_realtime_ang.py
- ez_positions_realtime_fin.py
- ez_positions_realtime_flz.py
- ez_positions_realtime_inf.py
- ez_positions_realtime_men.py
- ez_positions_service_sandbox.py
- ez_positions_watchdog.py
- ez_practical_sizing_backtest.py
- ez_prices_ws.py
- ez_sentiment_monitor.py
- ez_tournament.py
- ez_trading_policy.py
- tradier_hedge_engine.py
- tradier_recovery_agent.py
- tradier_reentry_improved.py
- tradier_vectorized_mass_sweep.py
- wt_composite.py

## Full md5 table

| FILE | MB | S1 | S1_STAT | S2 | S2_STAT |
|---|---|---|---|---|---|
| backtest_v8_engine.py | `73651883` | `73651883` | OK | `73651883` | OK |
| backtest_v8_harness.py | `2a3f4c92` | `2a3f4c92` | OK | `2a3f4c92` | OK |
| backtest_v8_interpolate_3m.py | `12bd2f07` | `12bd2f07` | OK | `12bd2f07` | OK |
| backtest_v8_interpolate_5m.py | `38727760` | `38727760` | OK | `38727760` | OK |
| backtest_v8_positions.py | `f0ecd8a6` | `f0ecd8a6` | OK | `f0ecd8a6` | OK |
| backtest_v8_precompute.py | `dc2188c2` | `dc2188c2` | OK | `dc2188c2` | OK |
| backtest_v8_precompute_tradier.py | `8a184456` | `8a184456` | OK | `8a184456` | OK |
| backtest_v8_sweep.py | `8dae17cd` | `8dae17cd` | OK | `8dae17cd` | OK |
| backtest_v8_verify_daily.py | `67854f38` | `67854f38` | OK | `67854f38` | OK |
| breakout_multi_lung.py | `99f79c91` | `99f79c91` | OK | `99f79c91` | OK |
| config.py | `e1f0048a` | `e1f0048a` | OK | `e1f0048a` | OK |
| config_tradier.py | `413a42df` | `413a42df` | OK | `413a42df` | OK |
| ez_autoresearch.py | `-` | `9328d462` | SANDBOX-ONLY | `-` | - |
| ez_autoresearch_monitor.py | `-` | `d7c374ca` | SANDBOX-ONLY | `-` | - |
| ez_backtest_tf_compare.py | `-` | `f96b44dd` | SANDBOX-ONLY | `-` | - |
| ez_backtest_tiered.py | `-` | `4e6a62db` | SANDBOX-ONLY | `-` | - |
| ez_backup.py | `8c86e129` | `8c86e129` | OK | `8c86e129` | OK |
| ez_breakout_agent.py | `-` | `ec6e708f` | SANDBOX-ONLY | `-` | - |
| ez_breakout_hunter.py | `778d4584` | `778d4584` | OK | `778d4584` | OK |
| ez_copilot.py | `5db14e0d` | `5db14e0d` | OK | `5db14e0d` | OK |
| ez_crosses.py | `423fe77a` | `423fe77a` | OK | `423fe77a` | OK |
| ez_disk_cleanup.py | `b5f4bde9` | `b5f4bde9` | OK | `b5f4bde9` | OK |
| ez_disk_watchdog.py | `28e1b213` | `28e1b213` | OK | `28e1b213` | OK |
| ez_divergence_monitor.py | `93c8fd91` | `93c8fd91` | OK | `93c8fd91` | OK |
| ez_double.py | `-` | `6b69e85c` | SANDBOX-ONLY | `6b69e85c` | SANDBOX-ONLY |
| ez_gain_protector.py | `-` | `12d93146` | SANDBOX-ONLY | `12d93146` | SANDBOX-ONLY |
| ez_gap_filler.py | `-` | `adce82e2` | SANDBOX-ONLY | `adce82e2` | SANDBOX-ONLY |
| ez_indicators.py | `522b55b4` | `522b55b4` | OK | `522b55b4` | OK |
| ez_indicators_merger.py | `-` | `7c5b6bc3` | SANDBOX-ONLY | `7c5b6bc3` | SANDBOX-ONLY |
| ez_key_levels.py | `-` | `4d49d71b` | SANDBOX-ONLY | `-` | - |
| ez_key_levels_monitor.py | `-` | `c3dc4031` | SANDBOX-ONLY | `-` | - |
| ez_klines.py | `ae85f310` | `ae85f310` | OK | `ae85f310` | OK |
| ez_klines_htf.py | `-` | `15e7a49f` | SANDBOX-ONLY | `-` | - |
| ez_launcher.py | `d2918222` | `d2918222` | OK | `d2918222` | OK |
| ez_logs_analyzer.py | `-` | `d1007a35` | SANDBOX-ONLY | `-` | - |
| ez_loss_mitigator.py | `-` | `113dca09` | SANDBOX-ONLY | `113dca09` | SANDBOX-ONLY |
| ez_manage.py | `8a221b88` | `8a221b88` | OK | `8a221b88` | OK |
| ez_manage_sandbox.py | `-` | `43af914f` | SANDBOX-ONLY | `-` | - |
| ez_manipulation_detector.py | `-` | `a4858997` | SANDBOX-ONLY | `-` | - |
| ez_mark_prices.py | `-` | `d6451a3a` | SANDBOX-ONLY | `d6451a3a` | SANDBOX-ONLY |
| ez_market_data.py | `982cbecd` | `982cbecd` | OK | `982cbecd` | OK |
| ez_news_scanner.py | `-` | `f5aa0980` | SANDBOX-ONLY | `f5aa0980` | SANDBOX-ONLY |
| ez_orderbook.py | `8e73c6b4` | `8e73c6b4` | OK | `8e73c6b4` | OK |
| ez_outlier_hunter.py | `bfeafbb5` | `bfeafbb5` | OK | `bfeafbb5` | OK |
| ez_positions.py | `8359ac4a` | `8359ac4a` | OK | `8359ac4a` | OK |
| ez_positions_active.py | `ce2db7d9` | `ce2db7d9` | OK | `ce2db7d9` | OK |
| ez_positions_backup.py | `-` | `-` | - | `93eb0e4b` | SANDBOX-ONLY |
| ez_positions_backup_account.py | `-` | `-` | - | `26a1f16a` | SANDBOX-ONLY |
| ez_positions_quick.py | `6694f19f` | `6694f19f` | OK | `6694f19f` | OK |
| ez_positions_realtime.py | `8d0dfc08` | `8d0dfc08` | OK | `8d0dfc08` | OK |
| ez_positions_realtime_ang.py | `-` | `-` | - | `048455e8` | SANDBOX-ONLY |
| ez_positions_realtime_fin.py | `-` | `-` | - | `df886545` | SANDBOX-ONLY |
| ez_positions_realtime_flz.py | `-` | `-` | - | `fec2af09` | SANDBOX-ONLY |
| ez_positions_realtime_inf.py | `-` | `-` | - | `62fb96ea` | SANDBOX-ONLY |
| ez_positions_realtime_men.py | `-` | `-` | - | `7edaf280` | SANDBOX-ONLY |
| ez_positions_service.py | `d1183d55` | `d1183d55` | OK | `d1183d55` | OK |
| ez_positions_service_sandbox.py | `-` | `34d2da62` | SANDBOX-ONLY | `-` | - |
| ez_positions_watchdog.py | `-` | `2d30c38c` | SANDBOX-ONLY | `2d30c38c` | SANDBOX-ONLY |
| ez_practical_sizing_backtest.py | `-` | `1669a4be` | SANDBOX-ONLY | `-` | - |
| ez_prices.py | `15da4c34` | `15da4c34` | OK | `15da4c34` | OK |
| ez_prices_ws.py | `-` | `b147e807` | SANDBOX-ONLY | `b147e807` | SANDBOX-ONLY |
| ez_rankings.py | `96b22692` | `96b22692` | OK | `96b22692` | OK |
| ez_reentry_vectorized.py | `3d947064` | `3d947064` | OK | `3d947064` | OK |
| ez_regime.py | `37920420` | `37920420` | OK | `37920420` | OK |
| ez_satoshit.py | `94ccf8a9` | `94ccf8a9` | OK | `94ccf8a9` | OK |
| ez_sentiment_monitor.py | `-` | `bd6a295b` | SANDBOX-ONLY | `-` | - |
| ez_share_ind.py | `c9b77aeb` | `c9b77aeb` | OK | `c9b77aeb` | OK |
| ez_tournament.py | `-` | `1236cfb1` | SANDBOX-ONLY | `-` | - |
| ez_trading_policy.py | `-` | `e923ebd9` | SANDBOX-ONLY | `-` | - |
| symbols.json | `ea84b911` | `ea84b911` | OK | `ea84b911` | OK |
| tradier_advisory_consumer.py | `208e5a8e` | `208e5a8e` | OK | `208e5a8e` | OK |
| tradier_api.py | `13a343e6` | `13a343e6` | OK | `13a343e6` | OK |
| tradier_emergency_brake.py | `2002386a` | `2002386a` | OK | `2002386a` | OK |
| tradier_hedge_engine.py | `-` | `bec42688` | SANDBOX-ONLY | `-` | - |
| tradier_history_sync.py | `3f7d0f9b` | `3f7d0f9b` | OK | `3f7d0f9b` | OK |
| tradier_indicators.py | `98bf9307` | `98bf9307` | OK | `98bf9307` | OK |
| tradier_manage.py | `05bf623f` | `05bf623f` | OK | `05bf623f` | OK |
| tradier_options_agent.py | `36f31db8` | `36f31db8` | OK | `36f31db8` | OK |
| tradier_options_analyzer.py | `d52fdbc1` | `d52fdbc1` | OK | `d52fdbc1` | OK |
| tradier_options_csp_backtest.py | `82bd03a7` | `82bd03a7` | OK | `82bd03a7` | OK |
| tradier_options_csp_monitor.py | `c06dc312` | `c06dc312` | OK | `c06dc312` | OK |
| tradier_options_hedge.py | `bd1baf2e` | `bd1baf2e` | OK | `bd1baf2e` | OK |
| tradier_options_recommendations.py | `b6d822d9` | `b6d822d9` | OK | `b6d822d9` | OK |
| tradier_options_shadow_compare.py | `8a70117b` | `8a70117b` | OK | `8a70117b` | OK |
| tradier_options_shadow_runner.py | `eef748c9` | `eef748c9` | OK | `eef748c9` | OK |
| tradier_options_shadow_scorer.py | `4980365f` | `4980365f` | OK | `4980365f` | OK |
| tradier_options_state.py | `b5c4650a` | `b5c4650a` | OK | `b5c4650a` | OK |
| tradier_positions.py | `e350c273` | `e350c273` | OK | `e350c273` | OK |
| tradier_premarket_scanner.py | `9dcb6e1b` | `9dcb6e1b` | OK | `9dcb6e1b` | OK |
| tradier_prices.py | `8fe0a3b3` | `8fe0a3b3` | OK | `8fe0a3b3` | OK |
| tradier_rankings.py | `129f2188` | `129f2188` | OK | `129f2188` | OK |
| tradier_recovery_agent.py | `-` | `-` | - | `2b9f2684` | SANDBOX-ONLY |
| tradier_reentry_improved.py | `-` | `559bb492` | SANDBOX-ONLY | `-` | - |
| tradier_sector_ls_ratio.py | `fe6a8844` | `fe6a8844` | OK | `fe6a8844` | OK |
| tradier_vectorized_mass_sweep.py | `-` | `-` | - | `6c051c9a` | SANDBOX-ONLY |
| tradier_vix_regime.py | `98878ce7` | `98878ce7` | OK | `98878ce7` | OK |
| tradier_webhook_bridge.py | `6ec6904e` | `6ec6904e` | OK | `6ec6904e` | OK |
| utils.py | `029eb8d4` | `029eb8d4` | OK | `029eb8d4` | OK |
| v8_quick_engine.py | `32cc37bf` | `32cc37bf` | OK | `32cc37bf` | OK |
| v8_quick_sweep.py | `ec7e423f` | `ec7e423f` | OK | `ec7e423f` | OK |
| wt_composite.py | `-` | `-` | - | `a76ff19c` | SANDBOX-ONLY |
| wt_dc_delta.py | `caf88b0c` | `caf88b0c` | OK | `caf88b0c` | OK |
| wt_dc_delta_backtest.py | `3167b8f1` | `3167b8f1` | OK | `3167b8f1` | OK |
| wt_dc_delta_engine.py | `4f9bdcb0` | `4f9bdcb0` | OK | `4f9bdcb0` | OK |
| wt_dc_entry_scorer.py | `f6a05917` | `f6a05917` | OK | `f6a05917` | OK |
| wt_dc_exit_scorer.py | `54570d29` | `54570d29` | OK | `54570d29` | OK |
| wt_dc_hierarchy.py | `5d15d5e4` | `5d15d5e4` | OK | `5d15d5e4` | OK |
| wt_dc_oos_paper_test.py | `48696799` | `48696799` | OK | `48696799` | OK |
| wt_dc_research.py | `d6474fb5` | `d6474fb5` | OK | `d6474fb5` | OK |
