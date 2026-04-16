# Dead Switch Wiring Progress

## Status as of 2026-04-16

### Summary
- TRULY_DEAD switches: 259
- WIRED (has live reference or budget cap added): 23
- DEAD_CONFIRMED (no plausible wiring): 236
- Files edited: config_tradier.py, tradier_manage.py

### Wired switches (priority order)
- P92: ENTRY_ZONE_SHORT, ALIGNMENT_GATE_TOTAL, ENTRY_TRIGGER_TF,
       TF_ALIGNMENT_MIN_LONG, TF_ALIGNMENT_MIN_SHORT, TF_FOCUS_ENTRY_HARD_GATE,
       TF_FOCUS_EXIT_HARD_GATE, TF_FOCUS_WEIGHT, TF_HTF1, TF_HTF3, TF_MACRO
- P90: MIN_HOLD_MINUTES_TRADIER
- P85: OPTIONS_MAX_LOSS_PCT_DTE_14/30/LOW, ORB_LONG_BUDGET, ORB_SHORT_BUDGET
- P80: SQUEEZE_ENABLED
- P75: MINERVINI_LONG_BUDGET, SMFI_LONG_BUDGET, SMFI_SHORT_BUDGET
- P60: EXIT_SENTIMENT_ENABLED
- P15: INDICATORS_FILE
- P5:  _CURRENT_MARKET_MODE

### Remaining (DEAD_CONFIRMED)
All 236 others marked with inline
`# DEAD_CONFIRMED (priority NN/100) — no plausible wiring site found 20260416`
comments in config_tradier.py.

Most fall into these buckets with no live-code target:
- DELTA engine (wt_dc_delta_engine.py is research-only, doesn't read config)
- V8Q_* (crypto V8 quick-engine only; not wired to stock live)
- MITIGATOR_* (ez_loss_mitigator referenced in crypto, not stocks)
- HEDGE_*_TRADIER (stock hedges share crypto hedge engine; tradier-specific knobs unused)
- ADAPTIVE_REGIME_* (paper-mode daemon reads crypto config only)
- INF_RANKING_BYPASS_* (crypto-only ranking bypass)
- LEGACY_* and percentage-stop switches (DEAD by policy, never re-enable)
- Internal/private (_REGIME_*, _INSTANCES, etc.)

### Backups
- backups/before_dead_switch_wiring_config_tradier_202604162046.py
- backups/before_dead_switch_wiring_tradier_manage_202604162046.py
- backups/before_dead_switch_wiring_wt_dc_delta_engine_202604162046.py
- backups/before_dead_switch_wiring_tradier_options_agent_202604162046.py
- backups/before_dead_switch_wiring_tradier_api_202604162046.py

### Compile status
- config_tradier.py: OK
- tradier_manage.py: OK
