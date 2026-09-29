# Stock filter wiring review (unexercised → port candidates)

_Generated 2026-09-29T17:19:02Z · basis: census × unexercised (5 bases/side @180d)_

## Summary
- Unexercised stock filters: **107**
- Already stock-wired (just didn't fire on 5 symbols; nothing to do): **42**
- **Port candidates (crypto-wired via ez_manage, NOT stock-wired): 52**
  - APPLICABLE: 41 · NOT_APPLICABLE (crypto-only): 6 · NEEDS_DATA: 5
- Vec-only (neither live path): 5 · Not in census: 7

> Verdicts are census-wiring + name-semantic, NOT per-filter code traces. Each APPLICABLE filter's exact ez_manage application must be read before wiring. NO filter was dropped. tradier_manage.py LOCKED — no edits; staged plan only.

## Port candidates

| filter | verdict | census class | colors | reason |
|---|---|---|---|---|
| ADX_RANGING_THRESHOLD | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| BANDAID_OFF_LOSER_RECOVER_PCT | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| BREAKEVEN_GAIN_EROSION_FILTER_TF | APPLICABLE | LIVE_ONLY | yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| BTC_ACCEL_RAMP_REQUIRE_POSITIVE | NOT_APPLICABLE | BOTH_WIRED | orange | crypto/futures-specific (BTC lead, open-interest, funding, or perp) — no stock analogue |
| BTC_HARD_BLOCK_OTHER_ACCOUNTS | NOT_APPLICABLE | BOTH_WIRED | orange | crypto/futures-specific (BTC lead, open-interest, funding, or perp) — no stock analogue |
| BTC_ROUND_BANDS_EACH_SIDE | NOT_APPLICABLE | BOTH_WIRED | orange | crypto/futures-specific (BTC lead, open-interest, funding, or perp) — no stock analogue |
| CIRCUIT_SHARPE_GATES_FILTER_TF | APPLICABLE | LIVE_ONLY | yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| COOLDOWN_LOCKS_FILTER_TF | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| CRYPTO_SPIKE_FADE_THRESHOLD_PCT | NOT_APPLICABLE | BOTH_WIRED | orange | crypto/futures-specific (BTC lead, open-interest, funding, or perp) — no stock analogue |
| DC_BREACH_REDUCE_FILTER_TF | APPLICABLE | LIVE_ONLY | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| DC_MOMENTUM_BOTA_SCORER_FILTER_TF | APPLICABLE | LIVE_ONLY | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| DC_MOMENT_STRONG_THRESHOLD | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| DELTA_ENGINE_FILTER_TF | NEEDS_DATA | LIVE_ONLY | orange,yellow | order-flow/price 'delta' input — verify the stock indicator source before wiring |
| DELTA_HTF_GATE | NEEDS_DATA | BOTH_WIRED | orange | order-flow/price 'delta' input — verify the stock indicator source before wiring |
| DELTA_REENTRY_FILTER_ENABLED | NEEDS_DATA | LIVE_ONLY | orange | order-flow/price 'delta' input — verify the stock indicator source before wiring |
| EZ_MANAGE_THROTTLER_RATE | NOT_APPLICABLE | BOTH_WIRED | orange | ez_manage infra knob (crypto engine internal), not a strategy filter for stocks |
| FIRST_OPEN_THROTTLE_FILTER_TF | APPLICABLE | LIVE_ONLY | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GOLDEN_RULE_BASE_USD | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GOLDEN_RULE_ENFORCE_FILTER_TF | APPLICABLE | LIVE_ONLY | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GOLDEN_RULE_HTF_VOTE_FILTER_TF | APPLICABLE | LIVE_ONLY | yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GR_FILTER_ALL_ENTRIES | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GR_FILTER_VEC_ENABLED | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GR_FILTER_VEC_FILTER_TF | APPLICABLE | LIVE_ONLY | yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GR_FILTER_VEC_MIN_TFS | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| GR_V5_STATE_FILTER_TF | APPLICABLE | LIVE_ONLY | yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HAIKU_WINNER_FILTER_TF | APPLICABLE | LIVE_ONLY | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HA_WICK_QUALITY_ENABLED | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HA_WICK_QUALITY_SCORE | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HA_WICK_QUALITY_TF | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HLR_SMA_BAND_PCT | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HLR_TOP_MIN_TFS | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HTF4_CONF | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HTF_DIRECTION_GATE_ENABLED | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HTF_GATE_BYPASS_RZ | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HTF_GATE_D_MANDATORY | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HTF_GATE_MIN_CONFIRMATIONS | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HTF_TREND_VETO_BYPASS_ENABLED | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| HTF_TREND_VETO_BYPASS_REASONS | APPLICABLE | BOTH_WIRED | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| LEADERBOARD_FILTER | NEEDS_DATA | BOTH_WIRED | orange | cross-symbol universe ranking — needs a stock-universe leaderboard/mover feed to exist |
| MOVER_THRESHOLD | NEEDS_DATA | LIVE_ONLY | orange | cross-symbol universe ranking — needs a stock-universe leaderboard/mover feed to exist |
| MTF_ARMED_ENTRIES_FILTER_TF | APPLICABLE | LIVE_ONLY | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| MTF_FILTER_STRONG_BUY_QUICK_BYPASS | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| MTF_GR_MIN_IND | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| MTS_BOTTOM_BONUS_THRESHOLD | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| MTS_BOTTOM_STRONG_THRESHOLD | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| NEW_POSITION_MAX_LOSS_THRESHOLD | APPLICABLE | LIVE_ONLY | orange | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| OI_CONFIRM_MIN_CHANGE_PCT | NOT_APPLICABLE | LIVE_ONLY | orange | crypto/futures-specific (BTC lead, open-interest, funding, or perp) — no stock analogue |
| PEAK_GIVEBACK_BE_EROSION_FILTER_TF | APPLICABLE | LIVE_ONLY | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| WT_15M_BOUNCE_BB_MAX | APPLICABLE | BOTH_WIRED | orange,yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |
| WT_15M_BOUNCE_BB_MIN | APPLICABLE | BOTH_WIRED | yellow | venue-agnostic (price/indicator/position-management logic maps to stocks) |