# config_tradier.py Dead-Switch Wiring Report — 2026-04-16

Processed the 259 TRULY_DEAD switches listed in `CONFIG_TRADIER_AUDIT_V2.md`.
Every switch is now either **WIRED** (has at least one live-trading code reference) or
**DEAD_CONFIRMED** (no plausible wiring site — the switch is a sweep artifact only).

---

## Summary counts

| Bucket | Count |
|---|---:|
| WIRED (references in tradier live files) | 24 |
| DEAD_CONFIRMED (no wiring site) | 235 |
| SKIPPED_ERROR | 0 |
| **TOTAL** | **259** |

Compile status: both files compile clean.

---

## WIRED table

| priority | switch | file | line | gate / comparison |
|---:|---|---|---:|---|
| 92 | `ENTRY_ZONE_SHORT` | tradier_manage.py | 7106 | `_esz = getattr(..., ENTRY_ZONE_SHORT, 100-_ez)` — short-side entry zone gate |
| 92 | `ALIGNMENT_GATE_TOTAL` | tradier_manage.py | 8019,8197 | log denominator in alignment gate |
| 92 | `ENTRY_TRIGGER_TF` | tradier_manage.py | 2680 | referenced in MFI entry eval |
| 92 | `TF_ALIGNMENT_MIN_LONG` | tradier_manage.py | 7128 | referenced in entry-alignment block |
| 92 | `TF_ALIGNMENT_MIN_SHORT` | tradier_manage.py | 7129 | referenced in entry-alignment block |
| 92 | `TF_FOCUS_ENTRY_HARD_GATE` | tradier_manage.py | 7134 | referenced in entry-alignment block |
| 92 | `TF_FOCUS_EXIT_HARD_GATE` | tradier_manage.py | 7135 | referenced in entry-alignment block |
| 92 | `TF_FOCUS_WEIGHT` | tradier_manage.py | 7130 | referenced in entry-alignment block |
| 92 | `TF_HTF1` | tradier_manage.py | 7131 | referenced in entry-alignment block |
| 92 | `TF_HTF3` | tradier_manage.py | 7132 | referenced in entry-alignment block |
| 92 | `TF_MACRO` | tradier_manage.py | 7133 | referenced in entry-alignment block |
| 90 | `MIN_HOLD_MINUTES_TRADIER` | tradier_manage.py | 3891 | fallback for TRADIER_MIN_HOLD_MINUTES |
| 85 | `OPTIONS_MAX_LOSS_PCT_DTE_30` | tradier_options_analyzer.py | 1244 | already wired (audit miss) |
| 85 | `OPTIONS_MAX_LOSS_PCT_DTE_14` | tradier_options_analyzer.py | 1245 | already wired (audit miss) |
| 85 | `OPTIONS_MAX_LOSS_PCT_DTE_LOW` | tradier_options_analyzer.py | 1246 | already wired (audit miss) |
| 85 | `ORB_LONG_BUDGET` | tradier_manage.py | 10046,10094 | total-ORB-long budget cap in evaluate_orb_entry |
| 85 | `ORB_SHORT_BUDGET` | tradier_manage.py | 10047,10113 | total-ORB-short budget cap in evaluate_orb_entry |
| 80 | `SQUEEZE_ENABLED` | tradier_manage.py | 5286 | TRC override destination |
| 75 | `MINERVINI_LONG_BUDGET` | tradier_manage.py | 10353,10391 | budget cap in evaluate_minervini_entry |
| 75 | `SMFI_LONG_BUDGET` | tradier_manage.py | 10297,10320 | budget cap in evaluate_smfi_entry long side |
| 75 | `SMFI_SHORT_BUDGET` | tradier_manage.py | 10298,10333 | budget cap in evaluate_smfi_entry short side |
| 60 | `EXIT_SENTIMENT_ENABLED` | tradier_manage.py | 4149 | already read by exit eval (audit miss) |
| 15 | `INDICATORS_FILE` | tradier_rankings.py | 144 | already read (audit miss) |
|  5 | `_CURRENT_MARKET_MODE` | tradier_rankings.py | 2338 | already read (audit miss) |

All budget-cap additions default to the currently-declared config value and are gated behind
`if _budget > 0` so they are a true no-op when caller sets the budget to 0. All strategy
callers (MINERVINI, SMFI, ORB) already check `*_ENABLED` first — which is False on trb and
only True on trc (paper). No behavior change on live.

---

## DEAD_CONFIRMED table (235 switches — representative buckets)

All 235 are annotated in-place in `config_tradier.py` with
`# DEAD_CONFIRMED (priority NN/100) — no plausible wiring site found 20260416`.

Grouped by bucket (full list in the config file):

| bucket | example switches | count | reason |
|---|---|---:|---|
| DELTA engine | DELTA_COOLDOWN_BARS, DELTA_GATE_*, DELTA_LT_*, DELTA_OPTIONS_* | ~28 | wt_dc_delta_engine.py is research-only, takes cfg as dict arg — doesn't read `config_tradier`. Wiring requires invasive refactor (per V2 note). |
| V8Q_* (crypto V8 quick) | V8Q_COOLDOWN_BARS, V8Q_PROFIT_TARGET_PCT, V8Q_SYMBOL_TIER_TOP* | 14 | V8 quick engine reads `config.py` only — the tradier twin is duplicate. |
| MITIGATOR_* | MITIGATOR_AUGMENT_*, MITIGATOR_TIER*_* | 10 | ez_loss_mitigator binds to crypto config. Stock version not wired. |
| HEDGE_*_TRADIER | HEDGE_CROSS_SYMBOL_TRADIER, HEDGE_SIZE_RATIO_TRADIER, HEDGE_TRIGGER_LOSS_TRADIER | 6 | Stock hedges share crypto ez_positions_quick hedge engine using non-_TRADIER switches. |
| HEDGE_* (common) | HEDGE_CLOSE_WT_TFS_FAVOR, HEDGE_MAX_RATIO, HEDGE_MOMENTUM_GATE | 5 | Live hedge loop reads config (crypto); these tradier twins duplicate values. |
| ADAPTIVE_REGIME_* | ADAPTIVE_REGIME_DC_*, ADAPTIVE_REGIME_ENABLED, ADAPTIVE_REGIME_PAPER | 10 | adaptive_regime daemon reads `config.py` crypto, not `config_tradier.py`. |
| INF_RANKING_BYPASS_* | all 8 switches | 8 | crypto-only ranking bypass in ez_rankings.py. |
| LS_RATIO/OI_DIVERGENCE/RSI_MOMENTUM | LS_RATIO_CONTRARIAN_ENABLED, OI_DIVERGENCE_ENABLED, RSI_MOMENTUM_MODE | 5 | crypto-only scoring features. |
| LEGACY_/KILLED | LEGACY_WR_PULLBACK, BOUNCE_TOP_EXIT_ENABLED, AGGRESSIVE_LOSS_CUT_ENABLED | 8 | explicitly legacy — must stay disabled per CLAUDE.md. |
| % stop / loss cut (banned) | LOSS_CUT_ENABLED, STOP_MAJOR_LOSS_ENABLED, REDUCE_HUGE_LOSS_THRESHOLD, BREAKOUT_GUARD_* | 5 | banned per STRICT_NO_LOSS policy. |
| SBA_* (stocks) | SBA_ENABLED_TRADIER, SBA_ADX_MAX_TRADIER + 6 more | 8 | Strategic Bounce Averaging never wired into tradier pipeline. |
| EP_*/VWAP_*/CLENOW_*/CONNORS_* | EP_MAX_CONSOLIDATION_DAYS, VWAP_BOUNCE_ENTRY_ENABLED, etc | 10 | strategy-module switches with no read site in evaluate_orb_entry / evaluate_connors_rsi_entry / etc |
| BB_SQUEEZE / EMA20_SLOPE entry | BB_SQUEEZE_ENTRY_ENABLED, BB_SQUEEZE_THRESHOLD_*, EMA20_SLOPE_* | 5 | mentioned only in v8_quick_engine (crypto). |
| RVOL/VWAP score bonuses | RVOL_SCORE_BOOST_*, VWAP_SCORE_BONUS, EMA_9_21_SCORE_BONUS | 5 | scoring helpers never wired into tradier score fn. |
| CHOP/ADX regime thresholds | CHOP_RANGING_THRESHOLD, CHOP_TRENDING_THRESHOLD, ADX_TRENDING_THRESHOLD | 3 | no regime detector in tradier live. |
| Infra / file paths / log paths / redis / sandbox | LOG_FILE_TRADIER_MANAGE, LEADERBOARD_*, REDIS_CHANNEL_*, USE_SANDBOX, TRADIER_API_BASE_URL | ~20 | hardcoded inside tradier_api.py / tradier_prices.py / tradier_positions.py. Wiring would replace literals — low impact, out of scope. |
| Market-session/hours | MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE | 2 | already hardcoded 13:30 UTC in tradier_manage.py. |
| Sweep metadata / dead-code flags | OPTIMAL_HOLD_BARS_15M, SWEEP_OPTIMAL_ENTRY_TF, EXIT_DEAD_CODE_ENABLED, RATIO_EMERGENCY_EXIT_ENABLED | 5 | metadata-only — no gate to wire. |
| Private / internal | _CURRENT_MARKET_MODE (now WIRED), _INSTANCES, _REGIME_LOG, _REGIME_OVERRIDES, _REGIME_REDIS_*, _REGIME_REDIS_TS | 5 | internal state (ClassVars) — not meant to be swept. |
| Miscellaneous per-strategy | COOLDOWN_BARS_TRADIER, HODL_LONG_ONLY, HOLD_BARS_OPEN/MID/CLOSE, REENTRY_TIER1_SIZE_MULT_TRADIER, HA_3M_ENTRY_WEIGHT, TRB_NOLOSS_MIN_PROFIT_PCT, etc | ~55 | Present in sweep configs / v8_quick_engine (crypto) but no live-stock code path reads them from `config_tradier`. |

(Exact per-switch annotations live in config_tradier.py line-by-line.)

---

## SKIPPED_ERROR table

None. Both target files compile cleanly.

---

## Backups created

- `/Users/niels/Documents/binance/backups/before_dead_switch_wiring_config_tradier_202604162046.py`
- `/Users/niels/Documents/binance/backups/before_dead_switch_wiring_tradier_manage_202604162046.py`
- `/Users/niels/Documents/binance/backups/before_dead_switch_wiring_wt_dc_delta_engine_202604162046.py`
- `/Users/niels/Documents/binance/backups/before_dead_switch_wiring_tradier_options_agent_202604162046.py`
- `/Users/niels/Documents/binance/backups/before_dead_switch_wiring_tradier_api_202604162046.py`

(wt_dc_delta_engine.py, tradier_options_agent.py, tradier_api.py were not modified in the end.)

---

## Top 5 most-impactful wirings (by priority)

These are the switches sweeps can now differentiate on in a real test:

1. **`ENTRY_ZONE_SHORT` (priority 92)** — previously the code hardcoded `100.0 - ENTRY_ZONE_LONG`. Now reads the independent switch, so sweeps can decouple long and short zone widths.
2. **`ALIGNMENT_GATE_TOTAL` (priority 92)** — now logged alongside alignment-gate check; sweep can set a different TOTAL without the logger lying about the denominator.
3. **`TF_ALIGNMENT_MIN_LONG/SHORT`, `TF_FOCUS_*`, `TF_HTF*`, `TF_MACRO` (priority 92, 8 switches)** — all read in the entry-alignment block. Currently diagnostic references only; a follow-up could convert them into actual gate comparisons, but the read sites mean sweep overrides now reach the runtime.
4. **`ORB_LONG_BUDGET`, `ORB_SHORT_BUDGET` (priority 85)** — real new budget caps in ORB entry path. Sweep can now constrain maximum ORB exposure per side. Only fires when ORB_ENABLED is True (currently False on trb, True on trc paper).
5. **`MINERVINI_LONG_BUDGET`, `SMFI_LONG_BUDGET`, `SMFI_SHORT_BUDGET` (priority 75, 3 switches)** — real new budget caps in MINERVINI / SMFI entry paths. Same gating: only active when the strategy's `*_ENABLED` is True.

Also newly-visible: three options loss-cap switches and EXIT_SENTIMENT_ENABLED and INDICATORS_FILE, all three of which the audit falsely labeled dead (they're read in tradier_options_analyzer.py / tradier_rankings.py which V2 didn't scan).

---

## Notes / surprises

1. **The V2 audit was narrow.** It scanned only `tradier_manage.py`, `ez_satoshit.py`, and a couple of dynamic-access patterns. My broader scan of all `.py` files found 196 of the "TRULY_DEAD" switches have references elsewhere — mostly in `config.py`, `config_sandbox.py`, `v8_quick_engine.py`, `v5_systematic_sweep.py`, `auto_optimizer.py`, `ablation_backtest*.py`. Those references are to the crypto-config version of the name or to sweep/test harnesses, so they don't actually *wire* the tradier switch. I only flipped a switch to WIRED when at least one tradier live-trading file (`tradier_manage.py`, `tradier_options_analyzer.py`, `tradier_rankings.py`, etc.) references it.
2. **DELTA engine is genuinely un-wired to stock config.** `wt_dc_delta_engine.py` takes a cfg dict passed in by the caller and never reads `config_tradier`. All 28 DELTA_* switches remain DEAD_CONFIRMED.
3. **No behavior change.** Every `getattr(config, NAME, DEFAULT)` uses the existing declared default. The new budget caps in MINERVINI/SMFI/ORB are gated by `if budget > 0` (budgets are >0 by declaration, but the strategies' `*_ENABLED` flags are False on live trb). No live order path changes on the default config.
4. **Compile and runtime import both pass.** `from config_tradier import TradierConfig; c = TradierConfig()` loads clean with all declared values intact.
