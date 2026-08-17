# Entry Path Audit — 2026-04-26

**Scope**: crypto only (`config.py` + `ez_manage.py` + `ez_positions_quick.py`). 8-account live system. Goal: identify dormant entry-trigger paths that could lift trade rate toward >500/day per account *without* breaking CLAUDE.md absolute rules. Read-only audit; no code edits.

**Inputs verified**:
- 266 `*_ENABLED`/`ENABLE_*` flags in `config.py` (143 ON / 107 OFF / 16 module-level).
- 51 canonical switches in `data/sweep_alerts/canonical_switches.json` — all wired (`yes` or `partial`/`DISABLED_BY_DESIGN`). **No DEATH-PENALTY red alert.**
- Live decisions JSONL today (UTC): `data/TRADE_FREQUENCY_AUDIT_20260426.md` shows `inf` 362 OPENs (mostly hedge), `ang` 265, `men` 103, `fin` 100, `flz` 0, `trb/trc` 0 directional. Floor for "500/day per account" is presently met only by inf hedge churn.

---

## 1. Entry-trigger function inventory

### `ez_manage.py`

| function (line) | role | gate(s) | live state |
|---|---|---|---|
| `check_entry_alignment` (149) | LTF/HTF TF-alignment gate | TF_ALIGN_STRICT, ENTRY_SCORE | ON |
| `check_entry_trigger` (411) | per-bar trigger | WT cross + stoch + score | ON |
| `check_entry_vetting` (481) | final pre-fire vet | DELTA_ENTRY, ENTRY_SYMGATE_ENABLED | partial (SYMGATE OFF) |
| `check_reentry_eligible` (605) | reentry gate | REENTRY_2_ENABLED, AGE_GATE | ON |
| `check_reentry_delta_tolerant` (70) | tolerant reentry | DELTA_REENTRY_FILTER_ENABLED | OFF |
| `evaluate_reentry` (16497) | per-symbol reentry scan | nine `REENTRY_B*` switches | mostly ON |
| `evaluate_leaderboard_entry` (16703) | leaderboard scan | RANK_CONVICTION_ENABLED | OFF |
| `evaluate_reversal_entry` (16975) | reversal entry | always | ON |
| `evaluate_reentry_2` (18425) | second-pass reentry | REENTRY_2_ENABLED | ON |
| `process_single_reentry_evaluation` (18016) | per-key apply | — | ON |
| `outlier_scan_loop` (22009) | outlier scanner | LEGITIMATE | ON |
| `periodic_direct_high_gain_reopen` (19541) | direct reopen | — | ON |
| `_price_level_reentry_monitor*` (22446/22548) | price-level retest | REENTRY_POST_CONSOL_ENABLED | ON |
| `periodic_evaluate_reentry_loop` (22561) | reentry orchestrator | — | ON |

### `ez_positions_quick.py`

| function (line) | role | live state |
|---|---|---|
| `check_entry_candidates_for_account` (13272) | **MAIN entry pipeline** | ON |
| `bulk_entry_scan_loop` (15503) | bulk per-account scan | ON |
| `quick_entry_monitor_loop` (15456) | fast entry monitor | ON |
| `priority_exit_scan_loop` (15390) | priority exits | ON |
| `aggressive_hedge_scanner` (15198) | hedge candidate scan | ON (HEDGE_MODE) |
| `reentry_enforcement_loop_epq` (14312) | EPQ reentry chain | ON |
| `evaluate_reentry_epq` / `_2_epq` / `_2_periodic_loop_epq` (14569/15063/15141) | EPQ reentry sub-funcs | ON |
| `scalp_v3_scan_loop` + `_scalp_v3_scan_once` + `_scalp_v3_attempt_open` (15829/15881/16321) | SCALP_V3 (inf only) | ON, $10 cap |

No prohibited autonomous `scan_and_open_*` loop found. All scanners route through `execute_now()` per CLAUDE.md.

---

## 2. Dormant entry-score paths (currently OFF, already wired)

All are score *bonuses* added inside the existing pipeline — they cannot bypass `execute_now()`, cannot expand `tradeable_keys`, and cannot place orders directly. They lift trade *rate* by raising borderline candidates over `ENTRY_SCORE` rather than spawning new order paths.

| flag (config.py line) | wiring | claimed historical edge | risk class |
|---|---|---|---|
| `EMA_PULLBACK_ENABLED` (1423) | `ez_positions_quick.py:3765` score +35 | BC_128 "academia + copy traders" — no per-trade Sharpe on file | low |
| `BB_RSI_STOCH_SCALP_ENABLED` (1432) | `:3777` score +25 | BC_134 "73-77% WR" (no Sharpe — VIOLATES Sharpe>2 floor for direct claim) | low |
| `MACD_ZERO_CROSS_ENABLED` (1434) | `:3786` score +15 | BC_131 "MACD 20% WR standalone" — DO NOT enable | high (poor edge) |
| `RSI2_MEAN_REVERSION_ENABLED` (1437) | `:3797` score +20 | BC_126 "91% WR daily" — daily TF, NOT 3m. Likely zero-trade on 3m base. | medium |
| `HA_WICK_QUALITY_ENABLED` (1441) | `:3804` score +15 | BC_144 "62% WR with EMA filter" — no Sharpe | medium |
| `BB_BREAKOUT_ENABLED` (1444) | `:3812` score +20 | BC_132 — trending regime only | medium |
| `TRIPLE_CONF_ENABLED` (1447) | `:3823` score +30 | BC_125 "MACD+RSI+Stoch" — no Sharpe | medium |
| `EMA200_STOCHRSI_ENABLED` (1487) | `:3834` | BC_127 — no Sharpe | medium |
| `RSI_MACD_EMA_ENABLED` (1493) | `:3847` | BC_129 — no Sharpe | medium |
| `MARKET_QUALITY_SCORE_ENABLED` (1542) | `:3880` | BC_146 "Sharpe +430%, 60% syms improved" — claim, unverified by 48-sym pool sweep | **high upside if verified** |
| `MI_ENTRY_ENABLED` (564) | wired exit path; entry bonus `:3xxx` | "OFF until sweep-proven" | medium |
| `RANK_CONVICTION_ENABLED` (945) | leaderboard score | "tested on broken B15/B11 data, re-sweep pending" | medium |
| `DC_MOMENT_ENABLED` (952) | DC moment score | same — re-sweep pending | medium |
| `ENTRY_SYMGATE_ENABLED` (443) | per-symbol speed gate | currently OFF; flipping ON would *reduce* trades | n/a |
| `REENTRY_SYMGATE_ENABLED` (441) | reentry per-sym gate | same — reducer | n/a |
| `STDEV_BREAKOUT_ENABLED` (1118) | `ez_positions_quick.py:10764/10843/12780/13981/14055` — full entry+exit path with retest sub-entries | "Kill switch OFF — backtest sweep first" | **high upside** (multi-entry per breakout via retest mechanic) |
| `BREAKOUT_MULTI_LUNG_ENABLED` (262) | external module ref `breakout_multi_lung.py` — **NOT FOUND in ez_manage/ez_positions_quick** | unknown | unknown — wiring gap |
| `DD_BOUNCE_ENABLED` (1406) | `ez_manage.py:16624,21061` | augment-on-bounce, OFF until sweep validates | medium (augment path, not new entry) |
| `SQUEEZE_FIRE_ENABLED` (1357) | only in v8 sweep engine | "default OFF — sweep on c08_crypto_winner baseline 2.6365" | not wired to live |
| `SBA_ENABLED` (1530) | underwater-add | BC_145 | medium |
| `OI_DIVERGENCE_ENABLED` (1512) | BC_143 | unverified | medium |

### TIER_A/B/C ablation labels (config.py 393–421)

`ASYMMETRIC_STOPS_ENABLED`, `PROGRESSIVE_LOCK_ENABLED`, `REGIME_GATE_ENABLED`, `PER_SYMBOL_CONFIG_ENABLED`, `VOLUME_CONFIRMATION_ENABLED`, `HOUR_OF_DAY_GATE_ENABLED`, `CIRCUIT_BREAKER_ENABLED`, `PYRAMID_ENABLED` — all carry **estimated** Sharpe deltas in comments (+0.1 to +0.5) but **none verified by full 48-sym pool sweep**. Per CLAUDE.md rule 4b ("48+ sym × >1yr × pool avg") and the Sharpe>2 floor: these claims are not actionable. Sweep first.

---

## 3. Per-account allowlist imbalance

`tradeable_keys.json` (371 entries):

| acct | total | LONG | SHORT | imbalance |
|---|---:|---:|---:|---|
| inf | 168 | 27 | 141 | severe SHORT-skew |
| ang | 84 | 39 | 45 | balanced |
| men | 55 | 27 | 28 | balanced |
| fin | 49 | 24 | 25 | balanced |
| **flz** | **16** | 10 | 6 | **starved** |

Symbol-list files (`symbols_*_long/short.json`):
- `flz` (10), `inf_long` (9), `ang_long`/`ang_short` (20), `inf_short` (29), `men` (80), `fin` (91).

**flz is the explicit choke point** — 16 tradeable_keys vs men's 55. The user's "USDC universe expanded by 19 pairs" has **not** been propagated to flz/ang/inf_long. Tradier (`trb/trc/tra`) logged 0 directional opens today, but those are stocks (out of scope for this audit).

**Proposed rebalance** (conservative, per CLAUDE.md "Tradeable Keys Sacred — hand-picked per account"): user-approved batch only. Suggest:
- Add the 19 new USDC pairs to `inf_long` (currently 9) and `flz` long-side (currently 10), bringing each toward ~25.
- Do NOT auto-add via scanner. Per "Tradeable Keys Sacred" rule, this is a manual user gate.

---

## 4. Top-3 dormant paths ranked by (entry-rate × edge × safety)

Scoring rubric: **entry-rate** = score-bonus magnitude × hit-frequency in indicator universe; **edge** = published Sharpe (must clear >2 floor); **safety** = does NOT bypass `execute_now`, NOT in 13-banned list, NOT a % stop, NOT an autonomous opener.

### #1 — `STDEV_BREAKOUT_ENABLED`
- **Mechanism**: full breakout entry (score 25) + up to 3 retest sub-entries per breakout cycle (score 22, size mult 1.5×). Only dormant flag with built-in *multi-fire* per setup, which directly lifts trade rate.
- **Wired**: `ez_positions_quick.py:10764, 10843, 12780, 13981, 14055` — entry, exit, retest, cooldown all present.
- **Sweep status**: `# Kill switch OFF — backtest sweep first` — **NEVER swept on 48-sym pool**. UNPROVEN.
- **Expected entries/day if enabled**: 50-150/account/day estimate (BB %B>1.125 firings on D+4h with 1h+15m retests across 50-sym universe at 3m base).
- **Ship?** **NO — sweep first**, per CLAUDE.md NEW STRATEGY PROHIBITION + Sharpe>2 floor. Build sweep config: `STDEV_BREAKOUT_ENABLED={True,False}` × baseline c08_crypto_winner (pool_sharpe 2.6365). 48-sym 4yr × Tier-2 `backtest_v8_engine.py`.

### #2 — `MARKET_QUALITY_SCORE_ENABLED`
- **Mechanism**: BC_146 entry filter, score bonus at `ez_positions_quick.py:3880`. Comment claims "Sharpe +430%, 60% syms improved".
- **Sweep status**: claim **not verified by 48-sym pool sweep on file**. Per Sharpe rule 4b, claim is INADMISSIBLE for live decision until verified.
- **Expected entries/day if enabled**: rate-neutral or slightly negative (it's a *filter*, not a trigger). Listed because the +430% claim, if real, dominates the edge term.
- **Ship?** **NO — verify claim with Tier-2 sweep first**. If claim survives 48-sym, ship.

### #3 — `EMA_PULLBACK_ENABLED` (BC_128)
- **Mechanism**: score +35 (highest of any BC bonus) when price pulls back to EMA9/14 above EMA20 with stoch_k<20 — classic "retest-and-launch" matching the user's documented edge ("SMA200 retest-and-launch" per memory).
- **Wired**: `ez_positions_quick.py:3765-3776`. Self-contained.
- **Sweep status**: "Validated by academia + copy traders" — anecdotal, no per-trade Sharpe on file. UNPROVEN by CLAUDE.md standard.
- **Expected entries/day if enabled**: ~20-60/account/day (15m TF pullback firings).
- **Ship?** **NO — sweep first**. Sweep proposal: `EMA_PULLBACK_ENABLED={True,False}` × `EMA_PULLBACK_SCORE_BONUS={20,35,50}` on c08_crypto_winner baseline.

---

## 5. Sweep proposals (config diffs)

### Sweep A — STDEV_BREAKOUT
```python
# Baseline: c08_crypto_winner (pool_sharpe 2.6365 anchor, per memory)
# Variant grid:
STDEV_BREAKOUT_ENABLED = [False, True]
STDEV_BREAKOUT_MAX_RETESTS = [1, 3, 5]
STDEV_BREAKOUT_RETEST_SIZE_MULT = [1.0, 1.5, 2.0]
# = 18 configs × 48 syms × 4yr Tier-2 (backtest_v8_engine.py)
# Pass criterion: pool_sharpe ≥ 2.6365 AND avg_gain_trade not worse than baseline AND max_dd_pct not worse.
# Reject otherwise. Per CLAUDE.md "Sharpe < 2 = trash" — additional 2.0 floor.
```

### Sweep B — MARKET_QUALITY_SCORE
```python
MARKET_QUALITY_SCORE_ENABLED = [False, True]
# Plus underlying threshold knobs (grep config.py:1542+ for tunables).
# 6-12 configs × 48 syms × 4yr Tier-2.
# Pass criterion: claimed +430% must show as ≥+50% pool_sharpe lift on full sample, else discard.
```

### Sweep C — EMA_PULLBACK
```python
EMA_PULLBACK_ENABLED = [False, True]
EMA_PULLBACK_SCORE_BONUS = [20, 35, 50]
EMA_PULLBACK_TF = ["15m", "1h"]
# 12 configs × 48 syms × 4yr Tier-2.
# Pass criterion: pool_sharpe ≥ baseline AND ≥10% trade-rate lift.
```

All three sweeps **must run on S1** (crypto, `config.py`, `python3 v8_test_queue.py --mode crypto`). Tier-1 vec engine = "feel" only per CLAUDE.md 2c — winners must clear Tier-2.

---

## 6. Verdict per CLAUDE.md ship rules

**No dormant entry path may be enabled live in this session.** Every candidate fails one or more of:
- Sharpe>2 floor on 48-sym × >1yr pool sweep (rule 4b, "feedback_sharpe_2_baseline")
- "30-day paper proof + user approval" (NEW STRATEGY PROHIBITION)
- Sweep proof on Tier-2 `backtest_v8_engine.py` (rule 2c)

The only **ungated near-term lever** is **per-account allowlist breadth** (rebalance flz/inf_long with the 19 new USDC pairs) — and even that requires user-approved manual `tradeable_keys.json` edit, not auto-expansion.

**500+ trades/day/account is not reachable by flipping switches.** It requires either (a) `STDEV_BREAKOUT` retest multi-fire (sweep-pending) plus broader `tradeable_keys`, or (b) loosening upstream gates (`GATE_STRICT` 2071 blocks/day on inf, `BLOCKED` 5667/day) — which is risk-on without sweep coverage.

---

*End of audit. No live edits made. No prohibited strategies proposed.*
