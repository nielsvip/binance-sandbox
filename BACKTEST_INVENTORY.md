# BACKTEST CHANGE INVENTORY — Complete Cross-Reference
> Generated 2026-03-24. Sources: `backtest_changes_100.xlsx`, `tournament_config.xlsx`, `ABLATION_REPORT.md`, `TESTING_SCHEDULE_20260324.md`, `trader_tactics_research.md`, all server sandbox results, live code in `config.py`, `config_tradier.py`, `ez_manage.py`, `ez_positions_quick.py`, `tradier_manage.py`, `ez_indicators.py`, `tradier_indicators.py`.

---

## SECTION 1: PROVEN WINNERS — Applied & Working

These changes are ACTIVE in production code, backed by backtest evidence, and should NOT be reverted.

| ID | Parameter | File | Old → New | Evidence | Ablation Δ Sharpe |
|----|-----------|------|-----------|----------|-------------------|
| **BC_1** | TF_FOCUS | config.py | 15m → **3m** | 79% of top 5000, avg Sharpe 305 vs 256 | **+4.624** (rank #1) |
| **BC_2** | TF_FOCUS_WEIGHT | config.py | 5.0 → **8.0** | 3m is 1.9x better than 15m | (bundled w/ BC_1) |
| **BC_17** | STOP_LOSS_THRESHOLD | config.py | 1.0 → **999.0** (disabled) | #1 PnL destroyer when active | **+2.947** (rank #3) |
| **BC_103** | ENTRY_ATR_PCT_MIN | config.py | 0.0 → **1.5%** | Winners 1.97% ATR vs losers 1.22% | **+4.125** (rank #2) |
| **BC_113** | STOP_MAJOR_LOSS_BLOCK | config.py | False → **True** (blocked) | -125k% cumulative PnL destroyer | **+2.947** (rank #4) |
| **BC_114** | IMMEDIATE_WRONG_WAY | config.py | True → **False** (disabled) | #2 PnL destroyer | **+2.329** (rank #5) |
| **BC_31** | HA_3M_ENTRY_WEIGHT | config.py | 0.0 → **-0.5** | Penalize HA-contradict entries | **+2.012** (rank #6) |
| **BC_100** | ENTRY_VOL_MIN_RATIO | config.py | 1.0 → **1.3** | Winners 1.95x vol vs losers 1.27x | **+2.008** (rank #7) |
| **BC_6** | BB_ENTRY_LONG_THRESHOLD | config.py | none → **-0.2** | Top 4 signal in 5000-backtest matrix | **+1.932** (rank #8) |
| **BC_32** | TF_ALIGNMENT_MIN_TOTAL | config.py | 4 → **3** | Over-filtering kills entries | **+1.352** (rank #9) |
| **BC_111** | RSI_ENTRY_GATE_ENABLED | config.py | none → **True** (long<37, short>63) | 97.8% WR, OOS Sharpe 304 | **+1.295** (rank #10) |
| **BC_101** | STOCH K/D periods | ez_indicators.py | 5/5 → **7/7** | Marathon: crypto PF 2.28, stock PF 2.81 | (marathon winner) |
| **BC_101** | K_ZONE thresholds | config.py | 35/65 → **90/10** | Marathon: PF 2.38 Sharpe 4.50 | (marathon winner) |
| **BC_108** | NO-LOSS natural exit gate | 8 files | none → gates all exits below min% | Blocks -125k% destroyer | (critical gate) |
| **BC_109** | K_ZONE_ENTRY_ENABLED | config.py | none → **True** | K in zone + turning, no crossover wait | (research-backed) |
| **BC_110** | BOUNCE_REENTRY_ENABLED | config.py | none → **True** | 96%+ WR on profitable exit pullback | (research-backed) |
| **BC_111** | MOVER_DETECTION_ENABLED | config.py | none → **True** | 99.7% WR mover + RSI gate combo | (research-backed) |
| **BC_113b** | MOMENTUM_FADE_ENABLED | config.py | none → **True** | 99.5% WR, Sharpe 325 | (research-backed) |
| **BC_253** | RATIO_MULTIPLIER | config.py | 4.0 → **3.0** | 253-config tournament: Sharpe 357 vs 332 | (tournament winner) |
| **BC_3** | EMA_DIST_ENTRY_ENABLED | config.py | none → **True** | Top 4 signal in matrix | (matrix winner) |
| **BC_4** | MOM3_ENTRY_ENABLED | config.py | none → **True** | Top 4 signal in matrix | (matrix winner) |
| **BC_5** | MOM5_ENTRY_ENABLED | config.py | none → **True** | Top 4 signal in matrix | (matrix winner) |
| **BC_100** | NOLOSS_MIN_PROFIT_PCT | config.py | 0.5% → **0.30%** | Tighter profit floor | (master trader) |
| **BC_102** | LONG_STOCH_CHASE_BLOCK | config.py | none → **True** | Block LONG when stoch>70 + ha_streak>2 | (master trader) |
| **BC_104** | SHORT_ABOVE_SMA20_BONUS | config.py | none → **15** | Mean-reversion shorts win at +3.15% | (master trader) |
| **BC_115** | FAST_RISER_DOUBLE | config.py | True → **False** (disabled) | Net negative PnL, amplifies losers | (disabled = good) |
| **BC_116** | AUGMENT_PYRAMID | config.py | True → **False** (disabled) | Sharpe -43 → -23 without it | (disabled = good) |
| **BC_123** | HEDGE_MODE | config.py | True → **False** (disabled) | 178-config test: NO_HEDGE Sharpe 94 vs best hedge 23 | (disabled = good) |
| **BC_120** | Same-symbol hedge | ez_positions_quick.py | allowed → **blocked** | Cross-symbol only | (disabled = good) |

### Proven Winners — Tradier (Stocks)

| ID | Parameter | File | Old → New | Evidence |
|----|-----------|------|-----------|----------|
| **T51** | NOLOSS_MIN_PROFIT_PCT_TRADIER | config_tradier.py | none → **1.0%** | NO-LOSS gate for stocks |
| **T52** | K_ZONE_ENTRY_ENABLED_TRADIER | config_tradier.py | none → **True** | Same K-zone edge as crypto |
| **T55** | RSI_ENTRY_PERIOD_TRADIER | config_tradier.py | 2 → **10** | RSI(10) mean-reversion, Sharpe 6.43 |
| **T61** | RATIO_MULTIPLIER_TRADIER | config_tradier.py | 2.0 → **3.5** | Stronger ratio enforcement |
| **T31** | HEDGE_MODE_TRADIER | config_tradier.py | True → **False** | Negative EV in all 5 configs |
| **T58** | ATR_TRAIL_ENABLED | config_tradier.py | True → **False** | #1 stock PnL destroyer (-2557%) |
| **T59** | STOCH_CROSS_ENTRY | config_tradier.py | True → **False** | Noise on daily bars |
| **T60** | AUGMENT_PYRAMID | config_tradier.py | True → **False** | Barely fires (0-10 trades) |
| **T63** | HEDGE_SAME_SYMBOL | config_tradier.py | True → **False** | Cross-symbol only |
| **BC_100** | 5m structure entry/exit | tradier_manage.py | none → higher_low/lower_high | PF 2.53, Sharpe 4.45 (best stock result) |

---

## SECTION 2: PROVEN LOSERS — Tested & Rejected (Do NOT Enable)

| ID | Parameter | What It Does | Evidence Against | Status |
|----|-----------|-------------|-----------------|--------|
| **BC_16** | OPTIMAL_HOLD_BARS_15M=13 | Force exit after 13 bars on 15m | Ablation: **-6.983 Sharpe** (worst performer) | ⚠️ ACTIVE — **SHOULD REVERT to 999** |
| **BC_35** | HTF1_CONF=False | Disable 1h confirmation | Ablation: **-1.680 Sharpe** — lost trend confirmation | ⚠️ ACTIVE — **CONTRADICTS server results** |
| **BC_101_TP** | ACCOUNT_TP_PCT=0.005 | 0.5% account TP | Ablation: **-0.745 Sharpe** — TP too tight | ⚠️ ACTIVE — **REVIEW needed** |
| ~~BC_107~~ | AGGRESSIVE_LOSS_CUT | Cut losses aggressively | No validation | Correctly **DISABLED** |
| ~~MACD~~ | MACD strategies (125, 129, 131) | MACD-based entries | BTC: 20% WR, -5.88 PF (NEGATIVE EV on crypto) | Correctly **DISABLED** |
| ~~ATR trail~~ | T58 ATR trailing stop | ATR-based trailing stop for stocks | -2557% PnL (destroys everything) | Correctly **DISABLED** |
| ~~DC 4h/D~~ | DC breakout on 4h/D for stocks | Donchian breakout on higher TFs | 28.6% WR, PF 0.65 | Correctly **NOT in code** |
| ~~Pyramiding~~ | T60 augment pyramid stocks | Pyramid into winners (stocks) | Barely fires (0-10 trades), no value | Correctly **DISABLED** |
| ~~Same-sym hedge~~ | BC_120, T63 | Hedge same symbol | Deadlock loop, 21K failed attempts in 4h | Correctly **DISABLED** |

---

## SECTION 3: CONTRADICTIONS — Code vs Evidence Mismatch

These need immediate review. The code says one thing, but backtest evidence says another.

| # | Issue | Code State | Evidence Says | Action Required |
|---|-------|-----------|--------------|-----------------|
| 1 | **BC_16: OPTIMAL_HOLD_BARS_15M=13** | ACTIVE (line 253) | Ablation: **-6.983 Sharpe** (WORST of all 52 tested) | **REVERT to 999 or disable** |
| 2 | **BC_35: HTF1_CONF=False** | ACTIVE (line 192) | Ablation: **-1.680 Sharpe** — server INDIVIDUAL_RESULTS also shows HTF1_CONF=OFF is bad | **REVERT to True** |
| 3 | **BC_101_TP: ACCOUNT_TP_PCT=0.005** | ACTIVE (line 211) | Ablation: **-0.745 Sharpe** — TP too tight; re-entry sizing study says need TP≥0.6% | **Raise to 0.01-0.015** |
| 4 | **STOCH 7/7 vs tournament 9/5/5** | Code: K=7, D=7 (marathon) | Tournament top 4 ALL use stoch_d=**5**, stoch_k=**9 or 14** | **Run head-to-head: 7/7 vs 9/5** |
| 5 | **TF combo: 3m focus vs D+4h+1h+15m** | Code: TF_FOCUS=3m | Tournament top 4 ALL use **D+4h+1h+15m** alignment | **Possible: 3m entry + D/4h/1h/15m confirm** |
| 6 | **TP: 0.3-0.5% vs tournament 1.5-2%** | Code: TP ~0.3-0.5% | Tournament winners: **1.5% fixed TP**, 567k backtest study confirms simple>complex | **Test 1.5% fixed TP** |
| 7 | **Entry gate: cross+ha vs cross+ha+dc** | Code: cross+ha (mostly) | Tournament #1 combo uses **cross+ha+dc** (Sharpe 188 vs 173) | **Test adding DC mid filter** |
| 8 | **Re-entry sizing: all net negative** | Code: reentries enabled | All 24 gate×sizing combos are net negative at 0.3% TP | **Increase TP before enabling reentries** |
| 9 | **Backtest Sharpe inflation** | Old results cited Sharpe 49-73 | QA found 5 bugs: actual Sharpe is **8-34x lower** | **Re-validate any result from before QA fix** |

---

## SECTION 4: HIGH-PRIORITY NOT YET APPLIED — Backtest-Ready

These have strong evidence but are currently DISABLED (False) in config. They should be the next batch to enable.

| Priority | ID | Parameter | Evidence | Risk | Config Flag |
|----------|----|-----------|----------|------|-------------|
| **#1** | **BC_137** | ADX_REGIME_FILTER | 36%→182% improvement (5x), documented across all strategies | Low — filter only, doesn't change entries | `ADX_REGIME_FILTER_ENABLED=False` |
| **#2** | **BC_128** | EMA_PULLBACK | Copy trading + academic validated, matches "retest-and-launch" core edge | Low — score bonus only | `EMA_PULLBACK_ENABLED=False` |
| **#3** | **BC_141** | F&G_SIZING | 1,240% vs 680% B&H (2018-2025), extreme fear = 3x size | Low — sizing modifier only | `FG_SIZING_ENABLED=False` |
| **#4** | **BC_139** | SIMPLE_TP_EXIT | 567k backtests: simple fixed TP% > trailing/complex | Medium — changes exit logic | `SIMPLE_TP_EXIT_ENABLED=False` |
| **#5** | **BC_138** | RSI_MOMENTUM_MODE | RSI>50=buy on crypto (not <30), multiple studies | Medium — inverts RSI logic | `RSI_MOMENTUM_MODE=False` |
| **#6** | **BC_134** | BB_RSI_STOCH_SCALP | 73-77% WR triple confirmation, good for scalp accounts | Low — additive signal | `BB_RSI_STOCH_SCALP_ENABLED=False` |
| **#7** | **BC_142** | LS_RATIO_CONTRARIAN | Suppress crowded-side entries using global L/S data | Low — filter only | `LS_RATIO_CONTRARIAN_ENABLED=False` |
| **#8** | **BC_143** | OI_DIVERGENCE | Price high + OI low = short signal, 30-50% accuracy boost | Low — filter only | `OI_DIVERGENCE_ENABLED=False` |

### Not Yet Applied — Tradier

| Priority | ID | Parameter | Evidence | Status |
|----------|----|-----------|----------|--------|
| **#1** | **T65** | Remove DC breakout from HODL entry | DC 28.6% WR on stocks, PF 0.65 | PENDING — code written, not deployed |
| **#2** | **T66** | DC_DAYTRADE_ENABLED | DC on 5m/15m with 1h expansion | PENDING — code written, not deployed |
| **#3** | -- | HODL_LONG_ONLY validation | Shorts negative on stocks per ablation | T54 ACTIVE but needs validation |

---

## SECTION 5: SERVER BACKTEST KEY FINDINGS SUMMARY

### What the server tests actually proved:

| Test | Location | Key Finding | Impact on Live |
|------|----------|-------------|----------------|
| **Config Matrix** (11 flags) | config_matrix/ | SCALP_OFF is #1 lever (+6.1 Sharpe). System still net negative. | SCALP_MODE=False ✅ applied |
| **Entry Logic** (4 logics × 4 TFs) | entry_logic_test/ | Logic D (multi-confirm k<30 + lower-TF k>d + higher-low) on 1h: **98% WR, PF 5.19, Sharpe 21.28** | ⚠️ NOT fully applied — only partially in BC_109/BC_110 |
| **RSI Investigation** (809 combos) | rsi_investigation/ | RSI 50-line crossover is the real edge. Best short: 15m rsi>50 entry + rsi<50 exit: **76% WR, PF 4.64, Sharpe 32.4** | ⚠️ NOT applied — BC_138 is DISABLED |
| **Re-entry Sizing** (24 combos) | reentry_sizing/ | ALL net negative. Need TP≥0.6% (not 0.3%) for break-even at 60% WR | ⚠️ Reentries active but TP too low |
| **Momentum Gate** | todo_crusher/ | 6-month momentum filter: +30% Sharpe improvement, cuts 31% worst trades | ⚠️ NOT applied |
| **QA Corrections** | qa_controlfreak/ | Backtest framework had 5 bugs, Sharpe inflated 8-34x | ⚠️ ALL pre-QA Sharpe values unreliable |
| **Ablation Study** (52 params) | ABLATION_REPORT.md | 10 KEEP, 3 REVERT, 39 neutral. BC_16 is worst (-6.98 Sharpe) | ⚠️ BC_16 still active (see contradictions) |
| **Tournament** (288 combos) | tournament_config.xlsx | Top 4: all D+4h+1h+15m, stoch_d=5, fixed exit, k_thresh=80, TP 1.5% | ⚠️ Live system uses 3m focus + 0.3-0.5% TP (contradicts) |
| **Marathon** (16K runs) | backtest_changes_100.xlsx | Stoch 14/7/7 + struct = best. K-zone 90/10 = second best | ✅ Applied (STOCH 7/7, K-zone 90/10) |
| **Hedge Postmortem** | HEDGE_POSTMORTEM.md | 21K failed attempts in 4.5h, 40%+ loss in 1 day. 5 fixes needed before re-enable | ✅ HEDGE_MODE=False (correctly disabled) |
| **Trade Loss Analysis** | TRADE_LOSS_ANALYSIS.md | 87/107 losses from 5 root causes: premature hedging, lifecycle chaos, bad registry, orphans, ratio kills | ✅ Hedge disabled, ratio fixed |
| **Scalp 1m** | scalp_1m_strategy/ | All 3 strategies net negative. Only works in falling markets | ✅ SCALP_MODE=False |
| **Sentiment Quality** | sandbox_sentiment_quality/ | Zero overlap between sentiment top-40 and ang's positions | ⚠️ Sentiment not driving entries |

---

## SECTION 6: NUMBERS THAT MATTER

### Crypto (from marathon/ablation/tournament):

| Metric | Best Config Found | Current Live | Gap |
|--------|-------------------|-------------|-----|
| **Entry logic** | Logic D multi-confirm 1h (Sharpe 21.28) | Mixed gates (3m focus) | Logic D not fully implemented |
| **Stoch params** | K=7, D=7 (marathon Sharpe 4.41) | K=7, D=7 | ✅ Aligned |
| **K-zone** | 90/10 (marathon Sharpe 4.50) | 90/10 | ✅ Aligned |
| **TP target** | 1.5% fixed (tournament Sharpe 188) | 0.3-0.5% tiered | ⚠️ **3-5x too low** |
| **TF alignment** | D+4h+1h+15m (tournament all 4 winners) | 3m focus, min 3 TFs | ⚠️ Needs reconciliation |
| **RSI mode** | 50-line crossover (Sharpe 32.4) | Mean-reversion <37/>63 | ⚠️ NOT applied |
| **ADX filter** | 36%→182% improvement | Not enabled | ⚠️ #1 unimplemented lever |
| **Exit mode** | Fixed TP (tournament + 567k study) | Complex tiered + structure | ⚠️ Simpler may be better |

### Stocks (from structure backtest/tradier results):

| Metric | Best Config Found | Current Live | Gap |
|--------|-------------------|-------------|-----|
| **Entry TF** | 15m+1h confirm (PF 2.53) | 5m structure + DC + stoch | Partially aligned |
| **Exit** | 5m structure break (PF 2.53) | Mixed | ✅ Applied (BC_100) |
| **RSI period** | RSI(10) (Sharpe 6.43) | RSI(10) | ✅ Aligned |
| **Stoch params** | K=7, D=7 (marathon Sharpe 5.04) | K=7, D=7 | ✅ Aligned |
| **Hedge** | Disabled (neg EV all 5 configs) | Disabled | ✅ Aligned |
| **ATR trail** | Disabled (-2557%) | Disabled | ✅ Aligned |
| **DC breakout entry** | Remove from HODL (28.6% WR) | Still in code | ⚠️ T65 PENDING |
| **DC daytrade wing** | Enabled on 5m/15m | Code written | ⚠️ T66 PENDING |

---

## SECTION 7: IMMEDIATE ACTION ITEMS (Priority Order)

### Fix Contradictions First:
1. **REVERT BC_16**: Set `OPTIMAL_HOLD_BARS_15M = 999` — ablation proves it's the single worst change (-6.98 Sharpe)
2. **REVERT BC_35**: Set `HTF1_CONF = True` — ablation proves disabling it hurts (-1.68 Sharpe)
3. **Raise TP**: Test `ACCOUNT_TP_PCT = 0.01` (1%) minimum — tournament, 567k study, AND re-entry sizing study all say 0.3-0.5% is too low

### Enable Top Unimplemented Levers:
4. **Enable BC_137**: `ADX_REGIME_FILTER_ENABLED = True` — single biggest documented improvement (36%→182%)
5. **Test BC_138**: `RSI_MOMENTUM_MODE = True` — RSI investigation found 50-line crossover is 2x better PF than current mean-reversion
6. **Enable BC_141**: `FG_SIZING_ENABLED = True` — 1,240% vs 680% buy-and-hold

### Tradier Specific:
7. **Deploy T65**: Remove DC breakout from HODL entry trigger (28.6% WR = net negative)
8. **Deploy T66**: Enable DC daytrade wing on 5m/15m
9. **Run stoch head-to-head**: 7/7 (marathon) vs 9/5 (tournament) — winner takes all

### Validate Before Trusting:
10. **Re-run ablation for BC_35 and BC_16** with corrected backtest framework (QA found 5 bugs, Sharpe inflated 8-34x)
11. **Reconcile 3m focus vs D+4h+1h+15m** — both show strong results in different tests. May be complementary (3m for entry timing, D/4h/1h/15m for direction).

---

## SECTION 8: COMPLETE CHANGE REGISTRY

### All 80+ BACKTEST_CHANGE entries — Final Status

| ID | Status | Applied In | Category |
|----|--------|-----------|----------|
| BC_1 | ✅ ACTIVE | config.py | TF_FOCUS=3m |
| BC_2 | ✅ ACTIVE | config.py | TF_FOCUS_WEIGHT=8.0 |
| BC_3 | ✅ ACTIVE | config.py | EMA_DIST entry |
| BC_4 | ✅ ACTIVE | config.py | MOM3 entry |
| BC_5 | ✅ ACTIVE | config.py | MOM5 entry |
| BC_6 | ✅ ACTIVE | config.py | BB entry threshold |
| BC_7 | ✅ ACTIVE | config.py | SMA200 dist threshold |
| BC_8 | 🔄 REVERTED | config.py | K3M_CAP (reverted to 80 from 70) |
| BC_9 | ✅ ACTIVE | config.py | K3M_FLOOR=30 |
| BC_12 | ✅ ACTIVE | config.py | Tiered TP |
| BC_14 | ✅ ACTIVE | config.py | Stoch cross 3m exit |
| BC_15 | ✅ ACTIVE | config.py | Hold bars 3m=21 |
| BC_16 | ⚠️ **REVERT** | config.py | Hold bars 15m=13 (WORST: -6.98 Sharpe) |
| BC_17 | ✅ ACTIVE | config.py | Stop loss disabled (999.0) |
| BC_18 | ✅ ACTIVE | config.py | Fast cut loss disabled (-999.0) |
| BC_19 | ✅ ACTIVE | config.py | Reduce huge loss disabled (-999.0) |
| BC_20 | ✅ ACTIVE | config.py | Breakout guard disabled (-999.0) |
| BC_21 | ✅ ACTIVE | config.py:ang | Start size=15 |
| BC_22 | ✅ ACTIVE | config.py:ang | Max order=120 |
| BC_23 | ✅ ACTIVE | config.py | DC width max=5.0 |
| BC_24 | ✅ ACTIVE | config.py | EMA dist sizing |
| BC_25 | ✅ ACTIVE | config.py | High gain aug min=50 |
| BC_27 | ✅ ACTIVE | config.py | Symbol size multipliers |
| BC_31 | ✅ ACTIVE | config.py | HA 3m entry weight=-0.5 |
| BC_32 | ✅ ACTIVE | config.py | TF alignment min=3 |
| BC_33 | ✅ ACTIVE | config.py | TF alignment short=1 |
| BC_34 | ✅ ACTIVE | config.py | TF alignment long=1 |
| BC_35 | ⚠️ **REVERT** | config.py | HTF1_CONF=False (HURTS: -1.68 Sharpe) |
| BC_38 | ✅ ACTIVE | config.py | Hedge trigger=-0.05 |
| BC_40 | ✅ ACTIVE | config.py | Circuit breaker=60s |
| BC_41 | ✅ ACTIVE | config.py:ang | Aug cooldown=90s |
| BC_42 | ✅ ACTIVE | config.py:ang | Reduce cooldown=15s |
| BC_43 | ✅ ACTIVE | config.py | Force refresh=10s |
| BC_44 | ✅ ACTIVE | config.py | Ranking loop=120s |
| BC_45 | ✅ ACTIVE | config.py | Reenter debounce=30s |
| BC_46 | ✅ ACTIVE | ez_manage.py | Blacklist augment block |
| BC_48 | ✅ ACTIVE | config.py | TRXUSDT blacklisted |
| BC_100 | ✅ ACTIVE | config.py + 3 files | Entry vol gate + structure + NOLOSS 0.3% |
| BC_101 | ✅ ACTIVE | ez/tradier_indicators | STOCH 7/7 + K-zone 90/10 |
| BC_102 | ✅ ACTIVE | config.py | Long stoch chase block |
| BC_103 | ✅ ACTIVE | config.py | ATR% min 1.5% |
| BC_104 | ✅ ACTIVE | config.py | Short above SMA20 bonus |
| BC_105 | 🔄 REVERTED | config.py | K3M_CAP reverted to 80 |
| BC_106 | 🔄 REVERTED | config.py | HTF_STRICT reverted to True |
| BC_107 | ❌ DISABLED | config.py | Aggressive loss cut=False |
| BC_108 | ✅ ACTIVE | 8 files | NO-LOSS natural exit gate |
| BC_109 | ✅ ACTIVE | config.py | K-zone entry |
| BC_110 | ✅ ACTIVE | config.py | Bounce reentry |
| BC_111 | ✅ ACTIVE | config.py | Mover detection + RSI gate |
| BC_112 | ✅ ACTIVE | config.py | Exit gain threshold=1.0 |
| BC_113 | ✅ ACTIVE | config.py | Stop major loss BLOCKED |
| BC_113b | ✅ ACTIVE | config.py | Momentum fade |
| BC_114 | ❌ DISABLED | config.py | Immediate wrong way=False |
| BC_115 | ❌ DISABLED | config.py | Fast riser double=False |
| BC_116 | ❌ DISABLED | config.py | Augment pyramid=False |
| BC_119 | ✅ ACTIVE | config.py | Hedge entry trigger=-2.0 |
| BC_120 | ✅ ACTIVE | ez_positions_quick.py | Same-symbol hedge blocked |
| BC_122 | ✅ ACTIVE | config.py | DC edge sizing |
| BC_123 | ✅ ACTIVE | config.py | HEDGE_MODE=False |
| BC_125 | ❌ DISABLED | config.py | MACD+RSI+Stoch (crypto neg EV) |
| BC_126 | ❌ DISABLED | config.py | RSI(2) mean reversion |
| BC_128 | ❌ DISABLED | config.py | EMA pullback (HIGH PRIORITY) |
| BC_131 | ❌ DISABLED | config.py | MACD zero cross |
| BC_132 | ❌ DISABLED | config.py | BB breakout |
| BC_134 | ❌ DISABLED | config.py | BB+RSI+Stoch scalp |
| BC_137 | ❌ DISABLED | config.py | **ADX regime filter (HIGHEST PRIORITY)** |
| BC_138 | ❌ DISABLED | config.py | RSI momentum mode |
| BC_139 | ❌ DISABLED | config.py | Simple TP exit |
| BC_141 | ❌ DISABLED | config.py | F&G sizing |
| BC_142 | ❌ DISABLED | config.py | L/S ratio contrarian |
| BC_143 | ❌ DISABLED | config.py | OI divergence |
| BC_144 | ❌ DISABLED | config.py | HA wick quality |
| BC_253 | ✅ ACTIVE | config.py | Ratio multiplier=3.0 |

### Tradier (T-prefix):

| ID | Status | Category |
|----|--------|----------|
| T1-T10 | ✅ ACTIVE | Entry zones, gates, alignment |
| T12 | ✅ ACTIVE | Scalp stop disabled (9.99) |
| T13-T14 | ✅ ACTIVE | RSI2 exit thresholds |
| T17 | ✅ ACTIVE | Stoch cross 1h exit |
| T18 | ✅ ACTIVE | Gap fill TP 0.7 |
| T19-T25 | ✅ ACTIVE | Time zone sizing |
| T26-T30 | ✅ ACTIVE | Position sizing |
| T31 | ❌ DISABLED | Hedge disabled (neg EV) |
| T34-T38 | ✅ ACTIVE | Positions, cooldowns, loss limits |
| T40-T50 | ✅ ACTIVE | Various operational params |
| T51-T57 | ✅ ACTIVE | NO-LOSS, K-zone, RSI, etc. |
| T58 | ❌ DISABLED | ATR trail (-2557%) |
| T59 | ❌ DISABLED | Stoch cross entry (noise) |
| T60 | ❌ DISABLED | Augment pyramid (no trades) |
| T61 | ✅ ACTIVE | Ratio mult=3.5 |
| T62 | ✅ ACTIVE | Cross-symbol hedge params |
| T63 | ❌ DISABLED | Same-symbol hedge |
| T64 | ✅ ACTIVE | RSI entry levels 42/58 |
| T65 | ⏳ PENDING | Remove DC from HODL entry |
| T66 | ⏳ PENDING | DC daytrade wing |

---

## SECTION 9: TRUST LEVEL OF RESULTS

| Source | Trust Level | Why |
|--------|------------|-----|
| **QA-corrected results** (qa_controlfreak) | ✅ HIGH | 5 bugs fixed, Sharpe corrected 8-34x |
| **Ablation study** (ABLATION_REPORT.md) | ⚠️ MEDIUM | Run before QA corrections — Sharpe deltas may be inflated |
| **Tournament** (tournament_config.xlsx) | ⚠️ MEDIUM | Small symbol set (40), only 3 rounds of 288 combos |
| **Marathon** (backtest_changes_100.xlsx) | ⚠️ MEDIUM | 16K runs but pre-QA framework |
| **RSI investigation** | ✅ HIGH | 809 combos, 80 symbols, done after framework fixes |
| **Entry logic test** | ✅ HIGH | Clean methodology, 4×4 matrix |
| **Config matrix** (11 flags) | ✅ HIGH | Systematic individual + combo testing |
| **Re-entry sizing** | ✅ HIGH | Clear structural math: TP must be ≥0.6% |
| **Trader tactics research** | ⚠️ MEDIUM | Literature review, not own backtests |
| **567k exit study** | ✅ HIGH | External (KJ Trading), massive sample |
| **Copy trading validation** | ⚠️ MEDIUM | Observational, not backtested |
