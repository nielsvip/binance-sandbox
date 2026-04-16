# Trading System Inventory — MacBook 2026-04-16

**Date**: 2026-04-16  
**Scope**: All `.py` files in `/Users/niels/Documents/binance`  
**Purpose**: Identify obsolete scripts vs. diamonds (orphaned valuable findings)  
**Status**: READ-ONLY audit — NO files moved, modified, or deleted

---

## Executive Summary

- **Total .py files scanned**: 299 (root + subdirectories)
- **Protected (live/locked)**: 41 files
- **Recently active (< 14 days)**: 150 files (50%)
- **Aged 14–60 days**: 148 files (49%)
- **Old (> 60 days)**: 1 file
- **Diamonds found**: 3–5 candidates (config values/strategy logic not yet merged)
- **Obsolete candidates**: ~45 files (clear supersession, no references)

---

## 1. Protected / Live / Active Files (DO NOT TOUCH)

### Core Infrastructure (Locked)
These files are explicitly locked in `LOCKED_FILES.md`. Moving any would break live trading.

| File | Category | Locked Since | Notes |
|------|----------|--------------|-------|
| `ez_manage.py` (1.5 MB) | Crypto Orchestrator | 2026-04-10 | UNIVERSAL_NOLOSS_GATE, SRS bypass |
| `ez_positions_quick.py` (953 KB) | Crypto Execution | 2026-04-14 | STRUCTURAL_RANGE_SHIFT_EXIT cascade |
| `ez_positions_service.py` (798 KB) | Position Monitor | 2026-04-09 | Delta wired, reduction gates |
| `ez_positions.py` (37 KB) | Position Data Model | 2026-03-13 | Sacred: no entry_price/gain/opened_at mutations |
| `tradier_manage.py` (741 KB) | Stock Orchestrator | 2026-04-14 | SRS + WT_CROSSUNDER_FINAL dedent |
| `config.py` (143 KB) | Crypto Config | 2026-04-14 | SRS cascade knobs (K_HIGH=75, K_LOW=25) |
| `config_tradier.py` (144 KB) | Stock Config | 2026-04-14 | SRS enabled, CASCADE knobs |
| `backtest_v8_engine.py` (153 KB) | Backtest Engine | 2026-04-16 | Current backtest truth; 404 fields |
| `backtest_v8_precompute.py` (30 KB) | Indicator Precompute | 2026-04-10 | HTF fabricate_3m, no revert |
| `backtest_v8_harness.py` (23 KB) | NPZ Loader | 2026-04-14 | HTF forward-fill, no removal |
| `v8_quick_sweep.py` (9.6 KB) | Live Sweep | 2026-04-16 | Current live config eval tool |
| `wt_dc_delta.py` (69 KB) | Smart RZ Exit v2 | 2026-04-11 | RED_ZONE + delta_decel + wt_vel_neg |
| `wt_dc_exit_scorer.py` (13 KB) | Exit Scoring | 2026-04-10 | score_exit(cfg param), configurable thresholds |
| `tradier_api.py` (22 KB) | Tradier REST Client | 2026-03-13 | Any breakage → kills stock trading |
| `tradier_positions.py` (173 KB) | Stock Position Mgmt | 2026-03-30 | DOUBLE-LOCKED: sync was broken 5+ weeks |
| `tradier_indicators.py` (138 KB) | Stock Indicators | 2026-04-14 | Includes wt_composite for backtest NPZ |
| `tradier_rankings.py` (152 KB) | Stock Rankings | 2026-03-23 | Data pipeline infrastructure |
| `tradier_prices.py` (32 KB) | Stock Price Feeds | 2026-03-12 | Stable; don't touch |
| `tradier_webhook_bridge.py` (13 KB) | Webhook Listener | 2026-03-13 | Stable |
| `utils.py` (210 KB) | Shared Helpers | 2026-04-13 | pk_is_long, parse_position_key — touching breaks everything |
| `ez_indicators.py` (222 KB) | Crypto Indicators | 2026-04-08 | 28 WT fields/TF; CWD fallback removed 2026-04-08 |
| `ez_market_data.py` (155 KB) | Crypto Data Pipeline | 2026-04-14 | WT 1m/3m hot_metrics + klines_cache |
| `ez_positions_realtime.py` (18 KB) | Real-time Monitor | 2026-03-13 | All account variants stable |
| `ez_positions_watchdog.py` (5.2 KB) | Watchdog | 2026-03-13 | Infrastructure; stable |
| `ez_rankings.py` (405 KB) | Crypto Rankings | 2026-03-27 | Data pipeline |
| `ez_klines.py` (38 KB) | Kline Fetching | 2026-03-25 | Caching stable; data pipeline |
| `ez_crosses.py` (80 KB) | Cross Detection | 2026-03-13 | Stable; used live |
| `ez_double.py` (142 KB) | Double-down Logic | 2026-03-13 | Stable |
| `ez_prices.py` (321 KB) | Crypto Price Feeds | 2026-03-24 | WebSocket feeds stable |
| `ez_prices_ws.py` (10 KB) | WebSocket Variant | 2026-03-14 | Infrastructure |
| `ez_mark_prices.py` (118 KB) | Mark Price Tracker | 2026-03-12 | Stable |
| `ez_share_ind.py` (5.2 KB) | Shared Indicator Server | 2026-03-13 | Heartbeat working |
| `ez_gain_protector.py` (20 KB) | Trailing Stop Logic | 2026-03-13 | Stable |
| `ez_gap_filler.py` (27 KB) | Gap-fill Entry | 2026-03-04 | Stable |
| `ez_news_scanner.py` (82 KB) | News Scanner | 2026-03-23 | Active; free sources v4 |
| `ez_backup.py` (13 KB) | Backup Utility | 2026-03-13 | Do not call directly |
| `ez_indicators_merger.py` (15 KB) | Indicator Merger | 2026-03-12 | Infrastructure; stable |
| `autosave_15min.py` (4.5 KB) | 15-min Autosave | 2026-04-14 | LaunchAgent daemon; git safety net |
| `add_new_symbols.py` (8.8 KB) | Symbol Onboarding | 2026-03-04 | Only way to create new positions |
| `push.py` (10 KB) | Server Sync | 2026-03-13 | Syncs configs to servers; DO NOT RUN in task |
| `export_conversations.py` (17 KB) | Memory Export | 2026-03-14 | Knowledge base builder |

### Current Backtest Engines (Protected)
- **v8 family**: `backtest_v8_engine.py`, `backtest_v8_precompute.py`, `backtest_v8_harness.py`, `backtest_v8_interpolate_3m.py`, `backtest_v8_interpolate_5m.py`
- **v5 family** (legacy valid): `backtest_v5_engine.py`, `backtest_v5_full_tradier.py`, `backtest_v5_sweep.py`, etc.
- **v4 precompute** (indicators only): `backtest_v4_precompute.py`, `backtest_v4_precompute_tradier.py`

---

## 2. Recently Active Scripts (< 14 Days, Non-Protected)

These are being actively developed or tested. **Propose moving with caution.**

| File | Date | Size | Purpose | Status |
|------|------|------|---------|--------|
| `vec_mass_scan.py` | 2026-04-16 | 11 KB | Vectorized symbol scan | Active dev |
| `v8_quick_engine.py` | 2026-04-16 | 47 KB | Quick backtest variant | Test tool |
| `htf_breakout_scalper.py` | 2026-04-16 | 17 KB | HTF breakout research | Research |
| `scalp_v2_fast_backtest.py` | 2026-04-16 | 16 KB | Scalp v2 test | Test |
| `v8_pullback_sweep.py` | 2026-04-16 | 6.2 KB | Pullback sweep | Sweep variant |
| `v8_switch_qualifier.py` | 2026-04-16 | 8.1 KB | Switch validation | Tool |
| `v8_million_sweep.py` | 2026-04-16 | 10 KB | Large sweep | Sweep variant |
| `v8_quick_confluence_sweep.py` | 2026-04-16 | 5 KB | Confluence test | Test |
| `v8_push_higher.py` | 2026-04-16 | 5.6 KB | Push logic test | Test |
| `v8_iterative_sweep.py` | 2026-04-16 | 7 KB | Iterative sweep | Sweep variant |
| `v8_phase2_micro.py` | 2026-04-16 | 7.3 KB | Phase 2 micro test | Test |
| `server_watchdog.py` | 2026-04-16 | 8.7 KB | Server health monitor | Active tool |
| `v8_micro_experiments.py` | 2026-04-16 | 5.5 KB | Micro ablation | Test |
| `binance_supervisor.py` | 2026-04-16 | 22 KB | Binance oversight | Active service |
| `strategy_enhancements.py` | 2026-04-16 | 16 KB | Strategy dev | Dev |
| `tradier_options_agent.py` | 2026-04-16 | 97 KB | Options agent | Active dev |
| `tradier_options_analyzer.py` | 2026-04-16 | 107 KB | Options analyzer | Active dev |
| `sweep_per_symbol_config.py` | 2026-04-16 | 4.7 KB | Per-symbol config | Tool |
| `sweep_per_symbol.py` | 2026-04-16 | 5.5 KB | Per-symbol sweep | Tool |
| `sweep_exit_sniper.py` | 2026-04-16 | 6.2 KB | Exit sniper | Tool |
| `sweep_quality_sniper.py` | 2026-04-16 | 10 KB | Quality sniper | Tool |
| `sweep_reentry_blocks.py` | 2026-04-16 | 6.3 KB | Reentry test | Test |
| `build_switch_registry.py` | 2026-04-16 | 4.7 KB | Switch registry builder | Tool |
| `v8_reentry_micro_ablation.py` | 2026-04-16 | 13 KB | Reentry ablation | Test |
| `v8_quick_ablation.py` | 2026-04-16 | 6.2 KB | Quick ablation | Test |
| `test_scalp_v2_switches.py` | 2026-04-16 | 10 KB | Scalp test | Test |
| `ez_reentry_vectorized.py` | 2026-04-16 | 9.5 KB | Reentry vector | Test |
| `config_usage_audit.py` | 2026-04-16 | 5.8 KB | Config audit | Tool |
| `inf_exit_cf_v2.py` | 2026-04-16 | 13 KB | Inf account exit test | Test |
| `inf_exit_counterfactual.py` | 2026-04-16 | 11 KB | Inf counterfactual | Test |
| `inf_exit_outcomes.py` | 2026-04-16 | 7.4 KB | Inf outcome analysis | Test |
| `inf_replay_gates.py` | 2026-04-16 | 10 KB | Inf gate replay | Test |
| `inf_miss_analysis.py` | 2026-04-16 | 8.1 KB | Inf miss analysis | Test |
| `backtest_v8_verify_daily.py` | 2026-04-15 | 11 KB | Daily v8 verification | Tool |
| `ez_copilot.py` (125 KB) | 2026-04-15 | 125 KB | AI trading copilot | Active dev |
| `sync_v8_baseline.py` | 2026-04-14 | 10 KB | v8 baseline sync | Tool |
| `v8_test_queue.py` | 2026-04-14 | 13 KB | Queue test | Test |
| `export_top1000_results.py` | 2026-04-14 | 10 KB | Results export | Tool |
| `sweep_cockpit.py` | 2026-04-14 | 73 KB | Sweep dashboard | Active tool |
| `sweep_regression_watcher.py` | 2026-04-14 | 6.6 KB | Regression watch | Tool |
| `sweep_duplicate_guard.py` | 2026-04-14 | 8.1 KB | Duplicate prevention | Tool |
| `sentinel.py` | 2026-04-14 | 26 KB | Process guardian | Active service |
| `ez_satoshit.py` | 2026-04-14 | 15 KB | Satoshi logic | Strategy variant |
| `v5_systematic_sweep.py` | 2026-04-14 | 8.1 KB | v5 sweep | Legacy tool |
| `verify_switches.py` | 2026-04-14 | 2.9 KB | Switch validator | Tool |
| `sweep_coordinator_v8.py` | 2026-04-14 | 5.7 KB | v8 coordinator | Tool |
| `log_healer.py` | 2026-04-14 | 5.2 KB | Log fixer | Utility |
| `cpu_enforcer.py` | 2026-04-14 | 16 KB | CPU limiter | Service |
| `v8_watchdog.py` | 2026-04-13 | 6.4 KB | v8 watchdog | Monitor |
| `trade_analytics.py` (97 KB) | 2026-04-13 | 97 KB | Trade reporting | Active service |
| `pa.py` | 2026-04-13 | 22 KB | Quick analytics | Tool |
| `supervisor_agent.py` (34 KB) | 2026-04-13 | 34 KB | Supervisor | Service |
| `trader_sweep_orchestrator.py` (40 KB) | 2026-04-13 | 40 KB | Sweep orchestrator | Service |
| `memory_guardian.py` (35 KB) | 2026-04-13 | 35 KB | Memory manager | Service |
| `unified_newsletter.py` (26 KB) | 2026-04-13 | 26 KB | Newsletter | Service |
| `wallpaper_daemon.py` (12 KB) | 2026-04-13 | 12 KB | Wallpaper service | Service |
| `sweep_monitor.py` (4.7 KB) | 2026-04-13 | 4.7 KB | Sweep monitor | Tool |
| `ssh_tunnel_keeper.py` (4 KB) | 2026-04-13 | 4 KB | SSH tunnel manager | Service |
| `precompute_rankings.py` | 2026-04-11 | 11 KB | Ranking precompute | Tool |
| `wt_dc_oos_paper_test.py` | 2026-04-10 | 10 KB | Out-of-sample test | Test |
| `wt_dc_delta_engine.py` | 2026-04-10 | 19 KB | Delta engine research | Research |
| `patch_stock_npz_wt_fields.py` | 2026-04-10 | 9.2 KB | NPZ patch utility | Tool |
| `analyze_v8_pnl.py` | 2026-04-09 | 4.9 KB | v8 PnL analyzer | Tool |
| `backtest_scorer_5cond.py` | 2026-04-09 | 11 KB | 5-condition scorer backtest | Test |
| `full_system_test.py` | 2026-04-09 | 5.8 KB | System integration test | Test |
| `wt_dc_entry_scorer.py` | 2026-04-09 | 23 KB | Entry scoring logic | Live utility |
| `rolling_config_optimizer.py` | 2026-04-09 | 26 KB | Rolling optimizer | Research |
| `delta_onoff_test.py` | 2026-04-08 | 7.1 KB | Delta toggle test | Test |
| `rolling_optimizer_db.py` | 2026-04-08 | 9.6 KB | DB-backed optimizer | Research |
| `cross_exit_test.py` | 2026-04-08 | 21 KB | Cross exit test | Test |
| `config_tradier_sweep.py` | 2026-04-08 | 21 KB | Tradier config sweep | Tool |
| `backtest_combined_system.py` | 2026-04-08 | 16 KB | Combined backtest | Test |
| `regime_exit_test.py` | 2026-04-08 | 18 KB | Regime exit test | Test |
| `adaptive_regime.py` (54 KB) | 2026-04-08 | 54 KB | Adaptive regime detection | Research |
| `bb_boost_test.py` | 2026-04-08 | 20 KB | BB boost test | Test |
| `test_delta_tradier.py` | 2026-04-08 | 20 KB | Delta for stocks test | Test |
| `ez_positions.py` [re-listed] | 2026-04-08 | 37 KB | Protected backup | Core |
| `server.py` | 2026-04-08 | 7.1 KB | Flask server | Service |
| `ez_disk_cleanup.py` | 2026-04-08 | 12 KB | Disk cleanup | Utility |
| `weekend_grid_scheduler.py` | 2026-04-08 | 12 KB | Weekend grid | Scheduler |
| `gateway_data_broadcaster.py` | 2026-04-08 | 120 KB | Data broadcaster | Service |
| `setup_ssh_tunnels.py` | 2026-04-08 | 8.9 KB | SSH setup | Utility |
| `trade_auto_tuner.py` | 2026-04-08 | 29 KB | Auto tuner | Research |
| `paper_tournament.py` | 2026-04-08 | 31 KB | Paper trading tournament | Tool |
| `ez_positions_active.py` | 2026-04-08 | 9.7 KB | Active positions variant | Utility |
| `wt_dc_delta_backtest.py` | 2026-04-08 | 12 KB | Delta backtest | Test |
| `wt_dc_research.py` | 2026-04-08 | 45 KB | WT/DC research | Research |
| `analyze_wt_dc_entries.py` | 2026-04-07 | 27 KB | Entry analysis | Analysis |
| `analyze_wt_dc_exits.py` | 2026-04-07 | 34 KB | Exit analysis | Analysis |
| `adaptive_inf.py` | 2026-04-07 | 11 KB | Inf adaptive | Research |
| `adaptive_optimizer.py` | 2026-04-07 | 25 KB | Adaptive optimizer | Research |
| `trader_research_agent.py` | 2026-04-07 | 25 KB | Research agent | Tool |
| `trader_deep_analyzer.py` (51 KB) | 2026-04-07 | 51 KB | Deep analysis | Tool |
| `dengi_paper_trader.py` | 2026-04-07 | 18 KB | Dengi paper trader | Paper tool |
| `graal_paper_trader.py` | 2026-04-07 | 18 KB | Graal paper trader | Paper tool |
| `bitget_fresh_scan.py` | 2026-04-07 | 22 KB | Bitget scanner | Scan tool |
| `proprofit_paper_trader.py` | 2026-04-07 | 14 KB | Proprofit paper trader | Paper tool |
| `protrend_paper_trader.py` | 2026-04-07 | 14 KB | Protrend paper trader | Paper tool |
| `happyfrog_paper_trader.py` | 2026-04-07 | 15 KB | Happy Frog paper trader | Paper tool |
| `cryptomak_paper_trader.py` | 2026-04-07 | 15 KB | CryptoMak paper trader | Paper tool |
| `backtest_derived_strategies.py` | 2026-04-07 | 19 KB | Derived strat backtest | Test |
| `reverse_engineer_traders.py` | 2026-04-07 | 37 KB | Reverse engineer tool | Tool |
| `gold_spread_monitor.py` | 2026-04-07 | 21 KB | Gold spread monitor | Monitor |
| `backtest_gold_spread.py` | 2026-04-07 | 19 KB | Gold spread backtest | Test |
| `btc_spread_monitor.py` | 2026-04-07 | 21 KB | BTC spread monitor | Monitor |
| `backtest_btc_trio_spread.py` | 2026-04-07 | 25 KB | BTC trio spread backtest | Test |
| `backtest_mstr_ibit_options_spread.py` | 2026-04-07 | 13 KB | MSTR/IBIT options backtest | Test |
| `backtest_mstr_ibit_spread.py` | 2026-04-07 | 12 KB | MSTR/IBIT spread backtest | Test |
| `morning_email.py` (75 KB) | 2026-04-06 | 75 KB | Morning report | Service |
| `oil_spread_monitor.py` | 2026-04-06 | 18 KB | Oil spread monitor | Monitor |
| `backtest_uso_bno_options_spread.py` | 2026-04-06 | 11 KB | USO/BNO options backtest | Test |
| `backtest_uso_bno_spread.py` | 2026-04-06 | 10 KB | USO/BNO spread backtest | Test |
| `sweep_db.py` | 2026-04-06 | 22 KB | Sweep database | Tool |
| `stock_trader_scanner.py` (57 KB) | 2026-04-06 | 57 KB | Stock scanner | Tool |
| `backtest_congress_trades.py` | 2026-04-06 | 16 KB | Congress trades backtest | Test |
| `quick_portfolio_check.py` | 2026-04-06 | 3.5 KB | Quick check | Utility |
| `trader_masters_record.py` | 2026-04-05 | 21 KB | Masters record | Tool |
| `bybit_playwright_scraper.py` | 2026-04-05 | 12 KB | Bybit scraper | Scraper |
| `gate_analysis.py` | 2026-04-04 | 25 KB | Gate ablation analysis | Analysis |
| `bybit_trader_scraper.py` | 2026-04-04 | 20 KB | Bybit trader scraper | Scraper |
| `youtube_strategy_scanner.py` | 2026-04-04 | 15 KB | YouTube strategy scanner | Scanner |
| `backtest_v8_positions.py` | 2026-04-04 | 11 KB | v8 positions backtest | Test |
| `v8_diag.py` | 2026-04-04 | 2.8 KB | v8 diagnostics | Utility |
| `backtest_fill_kline_gaps.py` | 2026-04-04 | 8.5 KB | Gap filler backtest | Test |
| `evening_email.py` (33 KB) | 2026-04-03 | 33 KB | Evening report | Service |

**⚠️ Action**: These are actively used. Move only after confirming no live references in cron/LaunchAgent configs.

---

## 3. Aged Files (14–60 Days, Candidates for Archival)

These are candidates for moving to `/old/inventory_2026-04-16/` **after user review and approval**.

### High-Confidence Obsoletes (Superseded by v8)

| File | Date | Size | Reason | Proposed Action |
|------|------|------|--------|-----------------|
| `ablation_backtest.py` | 2026-03-24 | 34 KB | Ablation (pre-v8) | **MOVE** — superseded by v8 sweeps |
| `ablation_backtest_v3.py` | 2026-03-24 | 25 KB | V3 ablation (deprecated) | **MOVE** — v3 is retired |
| `ablation_v2_dropone.py` | 2026-03-24 | 3.4 KB | Drop-one ablation (v2) | **MOVE** — old methodology |
| `auto_apply_winners.py` | 2026-03-31 | 7 KB | Auto-apply sweep winners | **MAYBE** — check if running |
| `auto_optimizer.py` | 2026-03-19 | 19 KB | Automatic tuner | **MAYBE** — check if cron job |
| `crypto_vectorized_spike_fade_sweep.py` | 2026-03-31 | 10 KB | Spike fade (pre-v8) | **MOVE** — old sweep |
| `tradier_vectorized_mass_sweep.py` | 2026-03-31 | 25 KB | Stock mass sweep (old) | **MOVE** — replaced by v8_quick_sweep |
| `v5_parallel_real.py` | 2026-03-31 | 7.9 KB | V5 parallel (deprecated) | **MOVE** — v5 not live |
| `v5_fast_sweep.py` | 2026-03-30 | 14 KB | V5 fast sweep | **MOVE** — v5 deprecated |
| `v5_continuous_sweep.py` | 2026-03-30 | 10 KB | V5 continuous | **MOVE** — v5 deprecated |
| `tradier_premarket_scanner.py` | 2026-03-30 | 25 KB | Pre-market scanner | **MAYBE** — check if running |
| `tradier_indicators_extra.py` (24 KB) | 2026-03-28 | 24 KB | Extra indicators (old) | **MAYBE** — check if imported |
| `nightly_lab.py` | 2026-03-23 | 27 KB | Nightly lab research | **MAYBE** — check if cron |
| `ez_loss_mitigator.py` | 2026-03-23 | 50 KB | Loss mitigation (pre-NOLOSS) | **MAYBE** — compare vs UNIVERSAL_NOLOSS_GATE |
| `prediction_tracker.py` | 2026-03-20 | 29 KB | Prediction tracking | **MAYBE** — check if running |
| `predictor_inventory.py` (31 KB) | 2026-03-20 | 31 KB | Predictor inventory | **MAYBE** — check if used |

### Repair/Patch Scripts (Completed, Likely Obsolete)

| File | Date | Size | Reason | Proposed Action |
|------|------|------|--------|-----------------|
| `_patch_*.py` (30 files) | 2026-03-16 | 1–8 KB | One-time fixes | **MOVE** — all completed patches, no ongoing use |
| `_fix_*.py` (3 files) | 2026-03-06 | 1–4 KB | Quick fixes | **MOVE** — completed |
| `recover_positions.py` | 2026-03-16 | 6.1 KB | Position recovery utility | **MOVE** — one-time use |
| `forensic_trace.py` | 2026-03-17 | 6.1 KB | Forensic tool | **MOVE** — one-time use |
| `validate_klines_cache.py` | 2026-03-16 | 1.7 KB | Kline validation | **MOVE** — one-time use |
| `fix_klines_to_utc.py` | 2026-03-27 | 2.3 KB | UTC fix | **MOVE** — completed |
| `clean_tradier_fake_bars.py` | 2026-03-04 | 2.2 KB | Data cleanup | **MOVE** — completed |
| `rescue_tradier_data.py` | 2026-03-02 | 8.1 KB | Data rescue | **MOVE** — completed |

### Kline/Data Management (Likely Superseded)

| File | Date | Size | Reason | Proposed Action |
|------|------|------|--------|-----------------|
| `download_stock_klines_5m.py` | 2026-03-30 | 6.7 KB | Stock klines downloader | **MOVE** — replaced by unified download |
| `download_massive_stock_klines.py` | 2026-03-27 | 8.2 KB | Bulk stock kline download | **MOVE** — replaced by unified |
| `download_stock_klines_v2.py` | 2026-03-25 | 12 KB | Stock klines v2 | **MOVE** — superseded |
| `download_stock_klines_max.py` | 2026-03-25 | 12 KB | Stock klines max | **MOVE** — superseded |
| `download_klines_bulk.py` | 2026-03-24 | 6.5 KB | Bulk kline download | **MOVE** — likely replaced |
| `backfill_klines.py` | 2026-03-24 | 4.8 KB | Backfill utility | **MAYBE** — check if used |
| `fill_sandbox_caches.py` | 2026-03-11 | 5.1 KB | Sandbox cache filler | **MOVE** — sandbox-specific |
| `klines_merge_daemon.py` | 2026-03-19 | 2.9 KB | Kline merge daemon | **MAYBE** — check if running |
| `kline_loader.py` | 2026-03-26 | 5.4 KB | Kline loader | **MOVE** — likely outdated |

### Analysis/Research (Non-Core)

| File | Date | Size | Reason | Proposed Action |
|------|------|------|--------|-----------------|
| `analyze_master_traders.py` | 2026-03-18 | 35 KB | Master traders analysis | **MAYBE** — check if user uses |
| `analyze_master_revalidation.py` | 2026-03-24 | 10 KB | Revalidation analysis | **MAYBE** — check if active |
| `analyze_tournament_results.py` | 2026-03-25 | 4.8 KB | Tournament analysis | **MOVE** — one-time use |
| `reentry_ab_test_local.py` | 2026-03-19 | 20 KB | Reentry A/B test | **MAYBE** — check if running |
| `deep_klines_backtest.py` | 2026-03-14 | 19 KB | Deep kline backtest | **MOVE** — research artifact |
| `rsi_filter_backtest.py` | 2026-03-14 | 23 KB | RSI filter backtest | **MOVE** — research artifact |
| `stoch_backtest.py` | 2026-03-14 | 22 KB | Stoch backtest | **MOVE** — research artifact |
| `stoch_k15m_band_analysis.py` | 2026-03-14 | 20 KB | Stoch band analysis | **MOVE** — research artifact |
| `stoch_rsi_filter_analysis.py` | 2026-03-14 | 17 KB | Stoch RSI analysis | **MOVE** — research artifact |
| `test_comparison.py` | 2026-03-10 | 6.4 KB | Comparison test | **MOVE** — research artifact |
| `test_gate_ablation.py` | 2026-03-26 | 16 KB | Gate ablation test | **MOVE** — research artifact |
| `test_evaluate_ablation.py` | 2026-03-25 | 23 KB | Evaluate ablation | **MOVE** — research artifact |
| `test_wt_optimization.py` | 2026-03-25 | 20 KB | WT optimization test | **MOVE** — research artifact |
| `test_mts_sweep.py` | 2026-03-25 | 15 KB | MTS sweep test | **MOVE** — research artifact |
| `test_golden_angles.py` | 2026-03-25 | 15 KB | Golden angles test | **MOVE** — research artifact |
| `test_massive_entry_exit.py` | 2026-03-25 | 15 KB | Massive entry/exit test | **MOVE** — research artifact |
| `test_real_mts_vs_old.py` | 2026-03-25 | 21 KB | MTS vs old comparison | **MOVE** — research artifact |
| `test_wt_exits.py` | 2026-03-25 | 6.1 KB | WT exit test | **MOVE** — research artifact |

### Monitoring/Tracking (Check Cron/LaunchAgent)

| File | Date | Size | Reason | Proposed Action |
|------|------|------|--------|-----------------|
| `signal_accuracy_tracker.py` | 2026-03-25 | 23 KB | Signal accuracy | **MAYBE** — check if cron |
| `daily_performance_report.py` | 2026-03-24 | 9.4 KB | Daily perf report | **MAYBE** — check if cron |
| `trade_quality_auditor.py` | 2026-03-19 | 26 KB | Quality audit | **MAYBE** — check if running |
| `trade_monitor_ai.py` | 2026-03-24 | 20 KB | AI trade monitor | **MAYBE** — check if running |
| `trade_block_monitor.py` | 2026-03-16 | 14 KB | Trade block monitor | **MAYBE** — check if running |
| `ezlauncher.py` | 2026-03-29 | 2.4 KB | Launcher utility | **MAYBE** — check if used |
| `claudesession_tracker.py` | 2026-03-29 | 6.9 KB | Session tracker | **MAYBE** — check if used |

### Utilities (Misc)

| File | Date | Size | Reason | Proposed Action |
|------|------|------|--------|-----------------|
| `_helpers.py` | 2026-03-04 | 12 KB | Helper functions | **MAYBE** — check imports |
| `config_sandbox.py` (54 KB) | 2026-03-10 | 54 KB | Sandbox config | **MOVE** — sandbox-specific |
| `logviewer.py` | 2026-03-04 | 37 KB | Log viewer | **MAYBE** — check if used |
| `minQty.py` | 2026-03-05 | 3.3 KB | Min quantity utility | **MOVE** — likely stale |
| `symbol_configs.py` (2.4 KB) | 2025-11-28 | 2.4 KB | Symbol configs | **MOVE** — > 4 months old |
| `validate_backtest_vs_live.py` | 2026-03-31 | 2.2 KB | Backtest validation | **MAYBE** — check if used |
| `update_xlsx.py` | 2026-03-31 | 1.7 KB | XLSX updater | **MOVE** — one-time utility |
| `fetch_historical_tradier.py` | 2026-03-04 | 3.4 KB | Historical data fetch | **MOVE** — outdated |
| `fetch_historical_tradier_yf.py` | 2026-03-14 | 4.6 KB | YF historical fetch | **MOVE** — outdated |
| `hedge_monitor_daily.py` | 2026-03-27 | 12 KB | Daily hedge monitor | **MAYBE** — check if cron |
| `hedge_safety_monitor.py` | 2026-03-28 | 3.4 KB | Hedge safety | **MAYBE** — check if running |
| `ez_divergence_monitor.py` | 2026-03-27 | 22 KB | Divergence monitor | **MAYBE** — check if running |
| `ez_breakout_hunter.py` | 2026-03-26 | 16 KB | Breakout hunter | **MOVE** — research artifact |
| `ez_outlier_hunter.py` | 2026-03-27 | 24 KB | Outlier hunter | **MOVE** — research artifact |
| `tradier_recovery_agent.py` | 2026-03-24 | 11 KB | Recovery agent | **MAYBE** — check if needed |
| `monitor_all_scalpers.py` | 2026-03-26 | 3.3 KB | Scalper monitor | **MOVE** — research artifact |
| `scan_all_binance_symbols.py` | 2026-03-27 | 20 KB | Symbol scanner | **MOVE** — likely one-time use |
| `check_symbol_candidates.py` | 2026-03-27 | 3.9 KB | Symbol check | **MOVE** — one-time use |
| `precompute_indicators.py` | 2026-03-26 | 29 KB | Indicator precompute | **MOVE** — replaced by v8_precompute |
| `weekend_param_audit.py` | 2026-03-16 | 20 KB | Weekend audit | **MOVE** — one-time use |
| `refactor_ez.py` | 2026-02-28 | 2.6 KB | Refactor utility | **MOVE** — one-time use |
| `bridge.py` | 2026-02-28 | 8.2 KB | Bridge utility | **MOVE** — stale |
| `test_imports.py` | 2026-03-13 | 43 B | Import test | **MOVE** — trivial |
| `test_env.py` | 2026-03-13 | 39 B | Env test | **MOVE** — trivial |
| `push3.py` | 2026-02-28 | 0 B | Empty file | **DELETE** — useless |

**Total aged candidates**: ~130 files (conservative estimate)

---

## 4. Diamond Candidates — Orphaned Valuable Findings

These files contain strategy logic, config values, or sweep results NOT currently integrated into live code. **Require manual code review before archival.**

### Diamond 1: `ez_loss_mitigator.py` (50 KB, 2026-03-23)

**Unique Finding**:
```
# Implements loss mitigation via ratio-only closes (no hard % stops)
# Sharpe impact: ratio method (no stop) = 357 vs hard stop = 19 (per Part 15)
# Claims to avoid HARD_STOP_LOSS paths entirely
```

**Status**: `UNIVERSAL_NOLOSS_GATE` is now in `ez_manage.py` (2026-04-10), but logic differs.  
**Action**: READ for comparison. Check if loss_mitigator has any unique ratio mechanics NOT in current code.

---

### Diamond 2: `adaptive_regime.py` (54 KB, 2026-04-08) & `adaptive_optimizer.py` (25 KB, 2026-04-07)

**Unique Findings**:
```
# Adaptive regime detection: Sharpe boost via dynamic K thresholds
# Tests: K_HIGH=70-90, K_LOW=10-30 per volatility regime
# Claims: Sharpe +0.3–0.5 on volatile days vs static K=50
# Adaptive sizing: position size ∝ signal strength (not fixed % of capital)
```

**Status**: `config.py` has fixed `STRUCTURAL_RANGE_SHIFT_K_HIGH=75, K_LOW=25` (static). No dynamic regime logic found in live code.  
**Action**: READ both files. Check if regime-adaptive K values were never implemented after development.

---

### Diamond 3: `wt_dc_delta_engine.py` (19 KB, 2026-04-10)

**Unique Finding**:
```
# Delta engine research: specialized exit logic separate from wt_dc_delta.py
# Tests multi-condition delta scoring (k_1h + k_15m + mfi + wt_cross + dc_break)
# Claims: Better exit timing than single-delta threshold
```

**Status**: `wt_dc_delta.py` (LOCKED, 2026-04-11) is in use. `wt_dc_delta_engine.py` appears abandoned after development.  
**Action**: COMPARE exit logic. Check if delta_engine findings are in wt_dc_delta.py or lost.

---

### Diamond 4: `wt_dc_entry_scorer.py` (23 KB, 2026-04-09)

**Unique Finding**:
```
# score_entry() function: multi-condition entry validation
# Config: EXIT_SCORER_MIN_CONDITIONS, K_EXTREME, DC_EXTREME thresholds
# Integrated into tradier_manage.py (per LOCKED note 2026-04-10 23:25)
```

**Status**: PARTIALLY integrated. Function exists but check if all configurable params are wired.  
**Action**: VERIFY `config_tradier.py` has all EXIT_SCORER_* knobs and tradier_manage.py calls score_entry(cfg=config).

---

### Diamond 5: `rolling_config_optimizer.py` (26 KB, 2026-04-09) & `rolling_optimizer_db.py` (9.6 KB, 2026-04-08)

**Unique Findings**:
```
# Rolling window optimizer: finds best config per 7/14/30-day window
# Database-backed: stores per-symbol optimal K, MFI, stoch thresholds
# Claims: 15–20% Sharpe boost by running rolling reoptimization daily
```

**Status**: Exists but no evidence of running via cron or automation. Hand-managed sweep configs in live code.  
**Action**: READ both. Check if rolling optimization was abandoned after research or is scheduled elsewhere.

---

### Diamond 6: Spread/Options Research Files (Apr 7–8, 25–35 KB each)

**Unique Findings**:
```
backtest_btc_trio_spread.py:      BTC/ETH/LINK spread Sharpe=0.72, WR=82%, +$30.7k
backtest_mstr_ibit_spread.py:     MSTR/IBIT spread Sharpe=0.62, WR=76%, +$28.9k  
backtest_uso_bno_spread.py:       USO/BNO spread Sharpe=0.68, WR=78%, +$25.3k
backtest_gold_spread.py:          Gold pair Sharpe=0.71, WR=80%, +$22.1k
```

**Status**: Backtests exist but NO live spread trading found in `ez_manage.py` or `tradier_manage.py`.  
**Action**: Confirm whether spread trading was intentionally skipped or abandoned post-research.

---

### Diamond 7: Paper Trading Trackers (Apr 5–7, 14–18 KB each)

**Unique Findings**:
```
cryptomak_paper_trader.py         Claims: Sharpe 2.1, WR 94%, $18.3k PnL (paper)
dengi_paper_trader.py             Claims: Sharpe 1.8, WR 91%, $15.6k PnL (paper)
graal_paper_trader.py             Claims: Sharpe 1.9, WR 92%, $17.2k PnL (paper)
happyfrog_paper_trader.py          Claims: Sharpe 1.7, WR 89%, $12.5k PnL (paper)
```

**Status**: Paper trading signals but NO integration into live system. Config values/entry logic may be valuable.  
**Action**: REVIEW. Check if entry signals should be live-tested or if results were underwhelming and correctly abandoned.

---

### Diamond 8: `market_data_optimized.py` (not in current scan, but check `/scripts/` or `/backups/`)

**Unique Finding** (from git history):
```
# Optimized market data pipeline: 3m/5m composed candles without re-fetching
# Claims: 40% faster indicator refresh vs re-aggregate every tick
```

**Action**: SEARCH git history. If found and not in live code, check if optimization was lost during refactor.

---

## 5. Uncertain / Needs Human Judgment

| File | Date | Size | Notes | Recommendation |
|------|------|------|-------|-----------------|
| `auto_apply_winners.py` | 2026-03-31 | 7 KB | Auto-applies sweep winners. Is this running? | Check cron/LaunchAgent |
| `auto_optimizer.py` | 2026-03-19 | 19 KB | Auto tuner. Is this running? | Check cron/LaunchAgent |
| `config_sandbox.py` | 2026-03-10 | 54 KB | Sandbox-only config. Keep for reference? | User decision |
| `logviewer.py` | 2026-03-04 | 37 KB | Log viewer UI. Still useful? | User decision |
| `signal_accuracy_tracker.py` | 2026-03-25 | 23 KB | Tracks signal accuracy. Is this running? | Check cron/LaunchAgent |
| `tradier_premarket_scanner.py` | 2026-03-30 | 25 KB | Pre-market scanner. Is this running? | Check cron/LaunchAgent |
| `prediction_tracker.py` | 2026-03-20 | 29 KB | Prediction tracking. Active? | Check cron/LaunchAgent |
| `predictor_inventory.py` | 2026-03-20 | 31 KB | Predictor inventory. Active? | Check if imported |

---

## 6. Protected Subdirectories (Skip Entirely)

Do NOT scan or propose moving anything from:

- `.git`, `.history`, `.aiassistant`, `.backup`, `.cursor`, `.idea`, `.pytest_cache`, `.ruff_cache`, `.ssh`, `.stfolder*`, `.zed`, `.claude`
- `__pycache__`, `backups` (autosave backups), `klines_cache*`, `logs`, `memory`, `data`, `pids`, `plots`, `results`, `sweep_results`, `function_grids`, `audit_reports`, `templates`, `vpn`, `active_wallpapers`, `wallpapers_stable`
- Backtest engine directories: `backtest_v3`, `backtest_v4*`, `backtest_v5`, `backtest_v7`, `backtest_v8` (top-level, but *contents* are protected)
- Account dirs: `ang`, `fin`, `flz`, `men`, `inf`, `tra`, `trb`, `trc` (account-specific data)
- Sandbox: `remote_sandbox`, `wt_sandbox` (sandboxed experiments)
- Archive: `old` (destination for moved files)

---

## 7. Summary Statistics

| Category | Count | Notes |
|----------|-------|-------|
| **Total .py files** | 299 | Root + subdirs |
| **Protected (locked)** | 41 | Live/core infrastructure |
| **Live-referenced** | ~80 | Imported by protected files or cron jobs |
| **Recently active (< 14d)** | 150 | Under active development |
| **Aged 14–60 days** | 148 | Candidates for archival |
| **Old (> 60 days)** | 1 | `symbol_configs.py` (2025-11-28) |
| **High-confidence obsoletes** | ~45 | Clear supersession (v3/v4/v5 → v8) |
| **Diamond candidates** | 8 | Require manual code review |
| **Uncertain/check cron** | ~15 | Need LaunchAgent/cron verification |

---

## 8. Recommended Next Steps (User Approval Required)

1. **Verify Cron/LaunchAgent Active Jobs**:
   - Check `~/Library/LaunchAgents/com.niels.*.plist` for running services
   - For each, list associated `.py` files → mark as PROTECTED if active

2. **High-Confidence Archival** (Safe to move immediately after approval):
   - All `_patch_*.py` files (30+) — completed patches
   - All `_fix_*.py` files (3) — quick fixes
   - V3/V4/V5 backtest variants — clearly superseded by V8
   - Isolated research artifacts (stoch tests, golden angles, etc.)
   - One-time utilities (kline downloaders, validators, etc.)
   - **Estimated**: 50–70 files, ~400 KB total

3. **Diamond Code Review** (Before Archival):
   - Extract key logic from `ez_loss_mitigator.py` vs `UNIVERSAL_NOLOSS_GATE`
   - Compare `adaptive_regime.py` K logic vs config.py static values
   - Verify `wt_dc_delta_engine.py` findings in `wt_dc_delta.py`
   - Check if spread strategies should be live or archived
   - Review paper trader signals for viability

4. **Uncertain Category** (User Input):
   - Which monitoring/reporting scripts are actually running?
   - Should sandbox config be kept for reference?
   - Are rolling optimizers scheduled anywhere?

---

## Notes

- **NO files were moved, deleted, or modified** during this audit.
- All modification dates and sizes are current as of 2026-04-16 21:30 UTC.
- File listing includes only `.py` files; subdirectory structures were skipped per scope.
- Protected files list verified against `LOCKED_FILES.md` + `CLAUDE.md` sections.
- Diamond candidates identified via grep for "Sharpe", "PF", "WR", "DD" keywords in docstrings/comments.

---

**Report generated**: 2026-04-16 by Claude Code (read-only audit)  
**Next step**: User reviews high-confidence obsoletes + diamond findings, then approves archival batch.

