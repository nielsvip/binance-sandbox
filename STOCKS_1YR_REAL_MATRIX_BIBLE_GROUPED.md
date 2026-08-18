# STOCKS 1YR Real Matrix — Grouped Switch Inventory (Bible)

Source: config_tradier.py 3,264 lines · registry 664 total · 664 _ENABLED · build 2026-08-18

Color rule: **Bottom/Top mean-reversion = TEAL #0f766e** vs **Breakout/Breakdown momentum = AMBER #b45309** (SHORT is mirror: bottom↔top, breakout↔breakdown). Exits Top/TP = EMERALD #047857 vs Breakdown/Stop = ROSE #be123c vs Trailing = AMBER. Filters = SKY #0369a1.

Groups replace the >900 entries × >900 exits × >120 reentries × >20 filters random cartesian — each bundle P1-P9 fixes one entry bucket, pairs its natural 1-2 exits, gates only with its own filter family.

## Entries — Bottom / Mean-Reversion (LONG bottom = SHORT top)

> Color `#0f766e` · chip `teal` — Buys the dip (long) / sells the rip (short). Fails-open, side-aware LONG: price < dc_low*(1-buf) vs SHORT: price > dc_high*(1+buf). All gated by STRENGTH/Zone/K-Zone/Red-Zone only.

### A1 BOUNCE — `ENTRY_BOUNCE_|DONCHIAN_DIRECT|BOUNCE_REENTRY|K_RESET` — 3 switches

Price bounced off Donchian/BB lower band and turns up. Ex: ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_* (8 keys, TF 5m, DIST 0.015, DEEP 50, TURN 40, CONF2)

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| BOUNCE_REENTRY_ENABLED | True config.py:2432 | WEAK | Y | Y | - | BACKTEST_CHANGE_110: After profitable exit, require K to pull back to  |
| ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED | False config.py:4351 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |
| ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED | False config.py:4357 | WEAK | Y | Y | Y | PORTED from TradierConfig 2026-08-17 |

### A2 STOCH / K-ZONE — `STOCH_ENTRY|K_ZONE_|COMBINED_STOCH` — 2 switches

Oversold stochastic. Ex: STOCH_ENTRY_ENABLED (K<30) + K_ZONE_ENTRY_ENABLED_TRADIER (k<thr & k>d), COMBINED_STOCH 60

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| K_ZONE_ENTRY_ENABLED | True config.py:2428 | WEAK | Y | Y | - | BACKTEST_CHANGE_109: K-zone entry — enter when K in zone (<35 LONG / > |
| STOCH_ENTRY_ENABLED | False config.py:39 | WEAK | Y | Y | - | parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks (bypass re |

### A3 WT dip — `WT_ENTRY|WT_DC_ENTRY|WT_3M|WT_CELSIUS` — 6 switches

WaveTrend trough. Ex: WT_ENTRY_ENABLED (cross up from < -60) + WT_DC_ENTRY 45 + HTF 4h_D

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| WT_3M_FORCE_OPEN_GR_GATE_ENABLED | False config.py:1411 | WIRED | Y | Y | Y | 2026-06-03 USER MANDATE — PERMANENTLY OFF. The GR-vote gate (20 votes/ |
| WT_3M_FORCE_OPEN_ENABLED | True config.py:1403 | WEAK | Y | Y | Y | USER 2026-06-01: Enabled with >1% SMA200_15m distance, WaveTrend veloc |
| WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED | False config.py:5113 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |
| WT_DC_ENTRY_ENABLED | False config.py:1163 | WEAK | - | Y | - | REVERTED 2026-08-11 |
| WT_3M_OPEN_GATE_ENABLED | False config.py:1151 | WEAK | Y | Y | - |  |
| WT_ENTRY_ENABLED | False config.py:40 | WEAK | Y | Y | - | parity 2026-08-17: SWITCH tested per_sym + 7D crypto+stocks (bypass re |

### A4 BB / Band — `BB_PCTB|LR_BAND|BB_ENTRY|BAND_SLOPE` — 9 switches

Band touch. %B<0.30 + LR band distance, channel slope filter; LR_BAND_REGIME uses slope UP to allow entry below top band

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| BAND_SLOPE_SIZING_V2_ENABLED | True config.py:1066 | WIRED | Y | Y | - | 2026-07-15 v2-v5 campaign: grad sizing uplift positive on 176-sym 6.5y |
| LR_BAND_ENTRY_ENABLED | False config.py:1075 | WIRED | Y | Y | - |  |
| LR_BAND_REGIME_ENABLED | False config.py:1083 | WIRED | Y | Y | - | 2026-07-20 USER: catch EVERY upswing — long anywhere below REGIME_MAX_ |
| BB_PCTB_ENTRY_ENABLED | False config.py:34 | WEAK | Y | Y | - | parity 2026-08-17: vector->live (was vector-only) |
| LR_BAND_HARVEST_ENABLED | False config.py:1082 | WEAK | Y | Y | - | 2026-07-19 USER band mandate: upper-band exit wired in ez_manage (was  |
| LR_BAND_SLOPE_FLIP_EXIT_ENABLED | False config.py:1085 | WEAK | Y | Y | - | 2026-07-20 USER: channel slope flip → full PROFIT exit |
| LR_BAND_E02_EXIT_ENABLED | False config.py:4467 | WEAK | Y | Y | Y | PORTED from TradierConfig 2026-08-17 |
| LR_BAND_LADDER_ENABLED | False config.py:4476 | WEAK | Y | Y | Y | PORTED from TradierConfig 2026-08-17 |
| LR_BAND_LADDER_ORDINARY_PARITY_ENABLED | False config.py:4478 | WEAK | Y | Y | Y | PORTED from TradierConfig 2026-08-17 |

### A5 RSI2 / Connors — `RSI2|RSI_ENTRY|CONNORS` — 7 switches

Extreme RSI. RSI(10)<40, RSI2<3, Connors cumulative

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| CONNORS_RSI_ENABLED | False config.py:4255 | WEAK | Y | Y | - | DISABLED 2026-03-30: augmented MRVL at -6.74% on real money. Needs V5  |
| RSI2_ENABLED | True config.py:4727 | WEAK | Y | Y | Y | PORTED from TradierConfig 2026-08-17 |
| RSI2_MEAN_REVERSION_ENABLED | False config.py:2675 | WEAK | Y | Y | - | BACKTEST_CHANGE_126: RSI(2) ultra-oversold (91% WR daily, tiny gains) |
| RSI_ENTRY_GATE_ENABLED | False config.py:2583 | WEAK | Y | Y | - | BC_154: DISABLED — 67-config ablation (48sym/4yr): stoch_gate_50 does  |
| CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED | False config.py:4251 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |
| TRADIER_RSI2_ENABLED | True config.py:4970 | WEAK | Y | Y | Y | PORTED from TradierConfig 2026-08-17 |
| TRC_CONNORS_RSI_ENABLED | False config.py:5030 | WEAK | Y | Y | - | 2026-06-02 V8-VALIDATED → LOSER, turned OFF per user parity policy. Fa |

### A6 MFI / STOCH scalps — `MFI_ENTRY|SCALP` — 31 switches

Fast mean-reversion scalps gated by volume

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| SCALP_V3_ENABLED | False config.py:143 | WIRED | Y | Y | - | USER 2026-05-30: SCALP_V3 PROHIBITED (counter-trend churn). Stays off. |
| SCALP_REDUCE_ENABLED | False config.py:118 | WIRED | Y | Y | - | 2026-05-30 USER: OFF. The AdvancedSignalRater.rate "SCALP_REDUCE" prof |
| BB_RSI_STOCH_SCALP_ENABLED | False config.py:2670 | WEAK | Y | Y | - | BACKTEST_CHANGE_134: BB+RSI+Stoch triple confirmation scalp (73-77% WR |
| MFI_ENTRY_ENABLED | True config.py:4512 | WEAK | Y | Y | Y | FIXED 2026-05-18: semantics inverted from "oversold-required" to "over |
| MICRO_SCALP_USDC_MAKER_ENABLED | False config.py:654 | WEAK | Y | Y | - |  |
| SCALP_V3_REENTRY_STICKY_ENABLED | True config.py:1187 | WEAK | Y | Y | - |  |
| MICRO_SCALP_STOCKS_MAKER_ENABLED | False config.py:4521 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |
| SCALP_V3_AUG_BE_STOP_ENABLED | True config.py:298 | WEAK | Y | Y | - |  |
| SCALP_V3_AUG_ENABLED | True config.py:291 | WEAK | Y | Y | - |  |
| SCALP_V3_K_OB_EXIT_ENABLED | True config.py:221 | WEAK | Y | Y | - |  |
| SCALP_V3_OB_FLOW_AGREE_ENABLED | True config.py:355 | WEAK | Y | Y | - | require live momentum to agree with OB side |
| SCALP_V3_PROTECTIVE_EXIT_ENABLED | True config.py:302 | WEAK | Y | Y | - |  |
| … +19 more | | | | | | |

### A7 SATOSHIT / LIVE_ENGINE — `SATOSHIT|LIVE_ENGINE` — 3 switches

Vote system +8 boost aggregation of micro-signals

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| SATOSHIT_ENABLED | True config.py:2446 | WIRED | Y | Y | Y |  |
| SATOSHIT_ENTRY_ENABLED | False config.py:45 | WEAK | Y | Y | - | parity 2026-08-17: vector->live (was vector-only) |
| SATOSHIT_EXIT_ENABLED | False config.py:2448 | WEAK | Y | Y | - | DISABLED 2026-04-21 — replaced by PARTIAL_PROFIT_LOCK mechanism (50% a |

---

## Entries — Breakout / Momentum (LONG breakout = SHORT breakdown)

> Color `#b45309` · chip `amber` — Buys strength on Donchian/Delta break with TF sizing 0.5x-4x. Opposite filters: Volume + Regime/LH_HL only; RED_ZONE not applied.

### B1 DC break — `DC_ENTRY|DC_BREAK|BREAKOUT_TF_SIZE` — 12 switches

Donchian channel breakout veto + timeframe position sizing

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| DC_BREAK_GR_MULT_ENABLED | False config.py:4264 | WIRED | Y | Y | - | P2-C: route DC_BREAK entries through GR Phase 1/2 sizing  # PORTED fro |
| REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED | False config.py:3170 | WIRED | Y | Y | - | 2026-05-29 NEUTRALIZED to default-OFF: an agent set this True (live de |
| BREAKOUT_TF_SIZE_ENABLED | True config.py:2077 | WEAK | Y | Y | - |  |
| EXIT_STRUCT_DC_BREAK_ENABLED | True config.py:4394 | WEAK | Y | Y | - | DC structural break (multi-TF). KEEP — catches real breakdowns.  # POR |
| HTF_DC_BREAKOUT_TRADIER_ENABLED | False config.py:4436 | WEAK | Y | Y | - | F2: additive entry — close > dc_high_4h * (1+thr) AND W WT on side  #  |
| DC_BREAKOUT_ENTRY_ENABLED | True config.py:2747 | WEAK | Y | Y | - | BACKTEST_CHANGE_133: Donchian breakout entry (trend-following) |
| DC_BREAK_LOW_REQUIRE_HTF_ENABLED | False config.py:4267 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |
| REENTRY_B11_DC_BREAK_ENABLED | True config.py:1613 | WEAK | Y | Y | - | ABLATION: Sharpe 0.34/0.31, 94-97% WR. Top quality. |
| WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED | False config.py:5113 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |
| WT_DC_ENTRY_ENABLED | False config.py:1163 | WEAK | - | Y | - | REVERTED 2026-08-11 |
| REENTRY2_DC_BREAK_ENABLED | True config.py:1629 | WEAK | Y | Y | - | DC breakout fast-path reentry — USER 2026-05-29 LOCKED ON: the live ga |
| SCALP_V3_ENTRY_DC_BREAK_ENABLED | False config.py:1222 | WEAK | Y | Y | - | 2026-04-27 OFF — same |

### B2 DELTA — `DELTA_ENTRY` — 1 switches

Delta z>=2.5 + accel 0.3 + score +15/-25, velocity proxy

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| DELTA_ENTRY_ENABLED | True config.py:1678 | WIRED | Y | Y | Y |  |

### B3 Arrow / GR HTF — `ARROW|GR_HTF|MTF_ARROW` — 7 switches

Multi-timeframe arrow → GR HTF 12→27 confirmation

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| GR_HTF_DIRECT_EXIT_ENABLED | True config.py:2641 | WIRED | Y | Y | Y | 🚩 Master exit switch. ROLLBACK: False |
| GR_HTF_GATE_ENABLED | False config.py:4421 | WEAK | Y | Y | Y | NEW. Adds GR HTF alignment gate (uses wt_bull_alignment/wt_bear_alignm |
| GR_HTF_DIRECT_ENTRY_ENABLED | True config.py:2638 | WEAK | Y | Y | - | 🚩 Master entry switch. ROLLBACK: False |
| MTF_ARROW_ENTRY_ENABLED | False config.py:4537 | WEAK | Y | Y | - | 2026-07-20 USER multi-TF arrow system (lab-proven ARM 5.81x b&h sized) |
| MTF_ARROW_TRAIL_EXIT_ENABLED | False config.py:4544 | WEAK | Y | Y | - | lab-faithful exit: close when price retraces CONFIRM_PCT off running h |
| BAND_ARROW_ENABLED | False config.py:4178 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |
| MTF_ARROW_SHORT_ENTRY_ENABLED | False config.py:4538 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |

---

## Reentries — Grouped separately (not 120 random)

> Color `#475569` · chip `slate` — Second entry on live position. Must be isolated from entry filters; gated only by Cooldown/Anti-churn.

### R1 PULL1-4 dumb buckets — `REENTRY_PULL` — 4 switches

PULL1-4 all False, 0 ablation win — dead buckets, VEC_UNSUPPORTED

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| REENTRY_PULL1_ENABLED | False config.py:41 | WEAK | Y | Y | - | parity 2026-08-17: vector->live (was vector-only) |
| REENTRY_PULL2_ENABLED | False config.py:42 | WEAK | Y | Y | - | parity 2026-08-17: vector->live (was vector-only) |
| REENTRY_PULL3_ENABLED | False config.py:43 | WEAK | Y | Y | - | parity 2026-08-17: vector->live (was vector-only) |
| REENTRY_PULL4_ENABLED | False config.py:44 | WEAK | Y | Y | - | parity 2026-08-17: vector->live (was vector-only) |

### R2 B-blocks smart — `REENTRY_B0|REENTRY_B1|BOUNCE_REENTRY` — 12 switches

KEEP B02/B04/B10/B11/B12/B14; CUT B09 0.02 Sharpe; B09-12 etc mixed getattr parity gap

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| BOUNCE_REENTRY_ENABLED | True config.py:2432 | WEAK | Y | Y | - | BACKTEST_CHANGE_110: After profitable exit, require K to pull back to  |
| REENTRY_B01_WT_2of3_ENABLED | False config.py:1608 | WEAK | Y | Y | - | ABLATION: Sharpe 0.033/0.036 = noise. 256K/177K trades. CUT. |
| REENTRY_B02_BC156_BOTTOM_ENABLED | True config.py:1609 | WEAK | Y | Y | - | ABLATION: Sharpe 0.31/0.32, 22K/14K trades, 62.5% WR. Best balance. |
| REENTRY_B04_DC_RETEST_ENABLED | True config.py:1610 | WEAK | Y | Y | - | ABLATION: Sharpe 0.39/0.31, 579/335 trades. High quality. |
| REENTRY_B09_SNAPBACK_ENABLED | False config.py:1611 | WEAK | Y | Y | - | ABLATION: Sharpe 0.022/0.024 = weak. CUT. |
| REENTRY_B10_STOCH_REV_ENABLED | True config.py:1612 | WEAK | Y | Y | - | ABLATION: Sharpe 0.07/0.12, 69-75% WR. Keep for WR. |
| REENTRY_B11_DC_BREAK_ENABLED | True config.py:1613 | WEAK | Y | Y | - | ABLATION: Sharpe 0.34/0.31, 94-97% WR. Top quality. |
| REENTRY_B12_WT_MOM_ENABLED | True config.py:1614 | WEAK | Y | Y | - | ABLATION: Sharpe 0.15/0.17, 112K/73K trades. Volume king. |
| REENTRY_B14_HA_TREND_ENABLED | True config.py:1615 | WEAK | Y | Y | - | ABLATION: Sharpe 0.11/0.13. Moderate. |
| REENTRY_B15_STRONG_TREND_ENABLED | True config.py:1616 | WEAK | Y | Y | - | ABLATION: Sharpe 0.89/0.72, 94-97% WR. Sniper. |
| REENTRY_B16_SMA200_PULLBACK_ENABLED | True config.py:3157 | WEAK | Y | Y | - |  |
| REENTRY_B16_MIDRANGE_ENABLED | True config.py:1617 | WEAK | Y | Y | - | DC midrange reclaim + 15m WT cross — fires when trend resumes after re |

### R3 Mandatory / Guaranteed — `GUARANTEED|MANDATORY|BTC_GUARANTEED|K_RESET` — 9 switches

Now gated by 5m DC break + WT 15m anti-churn (wt_dc_delta)

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| GUARANTEED_REENTRY_AUGMENT_ENABLED | True config.py:1865 | WIRED | Y | Y | - |  |
| TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED | True config.py:2087 | WEAK | Y | Y | - | 2026-04-16: re-enabled. The HTF_TREND_VETO block is fixed separately v |
| GUARANTEED_REENTRY_DELTA_GATE_ENABLED | False config.py:2472 | WEAK | Y | Y | - | 2026-05-10 USER MANDATE: DELTA_REENTRY_BLOCKED_tf/_z/_4h_against gates |
| GUARANTEED_REENTRY_TIGHT_STOP_ENABLED | True config.py:2478 | WEAK | Y | Y | - |  |
| MANDATORY_HEDGE_ON_NEGATIVE_ENABLED | True config.py:1989 | WEAK | Y | Y | - | ⚠️ DEATH PENALTY — DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION |
| MANDATORY_REENTRY_WT_FILTER_ENABLED | True config.py:4498 | WEAK | Y | Y | - | 2026-08-03 EMERGENCY: price-cross reentry requires 15m WT confirmation |
| BTC_GUARANTEED_REENTRY_ENABLED | True config.py:3941 | WEAK | Y | Y | - |  |
| GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED | False config.py:4087 | WEAK | Y | Y | - |  |
| GUARANTEED_REENTRY_HTF_VETO_ENABLED | False config.py:1016 | WEAK | Y | Y | - | USER 2026-05-11: same rationale — REENTRY must fire on bounce regardle |

### R4 Tier sizing — `TIER.*REENTRY|REENTRY_TIER` — 0 switches

Tier sizing 1.5/0.8/2.0/1.5/1.0, capital-aware

_No registry match for this pattern — pattern may be stocks-only / not in cross-config registry; check config_tradier directly._

---

## Exits — Top / Take-Profit (at the top → rose is not this)

> Color `#047857` · chip `emerald` — Scorer fade take-profits at overbought top. All via DeltaTracker with structural veto.

### X1 scorer fade — `MFI_FLIP|RSI_EXIT|STOCH_EXIT|WT_DC_EXIT|MTF_GR` — 4 switches

MFI>70, RSI>85, WT_DC 30, MTF_GR 3x5

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| MFI_FLIP_EXIT_ENABLED | True config.py:4515 | WEAK | Y | Y | - | BACKTEST_CHANGE_148: Exit when MFI exhausts (+3.91% avg vs +1.09% fixe |
| MTF_GR_EXIT_GATE_ENABLED | True config.py:1588 | WEAK | Y | Y | - | 2026-05-20 ON (Phase I) |
| MTF_GR_FILTER_ENABLED | True config.py:1497 | WEAK | Y | Y | - |  |
| WT_DC_EXIT_ENABLED | True config.py:5117 | WEAK | Y | Y | - | path-scoped master; False skips only the WT_DC scorer exit  # PORTED f |

### X1 regime — `REGIME_EXIT|EXIT_REGIME` — 0 switches

Regime 2%/0.15% take-profit

_No registry match for this pattern — pattern may be stocks-only / not in cross-config registry; check config_tradier directly._

### X1 formation — `FORMATION|EXIT_TOP` — 14 switches

Formation exits, harvest logic

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| FORMATION_CUP_HANDLE_ENTRY_ENABLED | False config.py:1911 | WEAK | Y | Y | Y |  |
| FORMATION_CUP_HANDLE_EXIT_ENABLED | False config.py:1912 | WEAK | Y | Y | - |  |
| FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED | False config.py:1903 | WEAK | Y | Y | Y |  |
| FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED | False config.py:1904 | WEAK | Y | Y | - |  |
| FORMATION_FLAG_PENNANT_ENTRY_ENABLED | False config.py:1909 | WEAK | Y | Y | Y |  |
| FORMATION_FLAG_PENNANT_EXIT_ENABLED | False config.py:1910 | WEAK | Y | Y | - |  |
| FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED | False config.py:1901 | WEAK | Y | Y | Y |  |
| FORMATION_HEAD_SHOULDERS_EXIT_ENABLED | False config.py:1902 | WEAK | Y | Y | - |  |
| FORMATION_TREND_STRUCTURE_ENTRY_ENABLED | False config.py:1913 | WEAK | Y | Y | Y |  |
| FORMATION_TREND_STRUCTURE_EXIT_ENABLED | False config.py:1914 | WEAK | Y | Y | - |  |
| FORMATION_TRIANGLE_ENTRY_ENABLED | False config.py:1907 | WEAK | Y | Y | Y |  |
| FORMATION_TRIANGLE_EXIT_ENABLED | False config.py:1908 | WEAK | Y | Y | - |  |
| … +2 more | | | | | | |

---

## Exits — Breakdown / Stop / Structure (the breakdown → not the top)

> Color `#be123c` · chip `rose` — Technical breakdown exits. All require structural_exit_permitted() = NOT rising AND (LTF collapse OR 1h/4h LH+LL). DC multi-TF close_5m_prev confirm.

### X2 DC/BB reject — `EXIT_STRUCT_DC_BREAK|STRUCTURAL_RANGE_SHIFT|STDEV|BB_REJECT|DC_REJECT|MTF_DC|MTF_BB` — 17 switches

DC break (14616 close_5m_prev), bb_1h structural shift (bb_4h = Apr-13 disaster), STDEV/BB wall

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| STDEV_BREAKOUT_ENABLED | False config.py:2278 | WIRED | Y | Y | Y | Kill switch OFF — backtest sweep first |
| STDEV_BOUNCE_ENABLED | False config.py:2298 | WIRED | Y | Y | Y |  |
| STDEV_REJECT_EXIT_ENABLED | False config.py:2303 | WIRED | Y | Y | - |  |
| EXIT_STRUCT_DC_BREAK_ENABLED | True config.py:4394 | WEAK | Y | Y | - | DC structural break (multi-TF). KEEP — catches real breakdowns.  # POR |
| EXIT_STDEV_BREAKOUT_FAIL_ENABLED | True config.py:1300 | WEAK | Y | Y | - | BB breakout failure (price back inside bands) |
| STDEV_BB_RZ_EXIT_ENABLED | False config.py:2280 | WEAK | Y | Y | - | Exit when price exits daily BB band (rejection) |
| STDEV_BREAKOUT_EXIT_WT_ENABLED | True config.py:2307 | WEAK | Y | Y | - | Also exit on WT turn against on 1h |
| STDEV_MACRO_HEDGE_BOOST_ENABLED | False config.py:1545 | WEAK | Y | Y | Y | extra OBLIGATORY_HEDGE trigger when origin held against macro extreme. |
| STDEV_MACRO_R4_EXIT_ENABLED | False config.py:1543 | WEAK | Y | Y | Y | fire CLOSE on STRONG_TOP (LONG) / STRONG_BOT (SHORT) + LTF flip (wt_4h |
| LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED | False config.py:321 | WEAK | Y | Y | - | 2026-05-19 PATH D: STDEV_D200_HIGH+WT_D_BEAR composite (68.5% WR, +41  |
| MTF_BB_REJECT_EXIT_ENABLED | True config.py:1585 | WEAK | Y | Y | - | 2026-05-20 ON (Phase I) |
| MTF_DC_REJECT_EXIT_ENABLED | True config.py:1582 | WEAK | Y | Y | - | 2026-05-20 ON (Phase I) |
| … +5 more | | | | | | |

### X2 SRS — `SRS` — 0 switches

DC/BB hybrid SRS exit

_No registry match for this pattern — pattern may be stocks-only / not in cross-config registry; check config_tradier directly._

### X2 Delta speed-decay — `DELTA_EXIT` — 3 switches

DELTA speed_decay 30% on 15m 0.71 Sharpe, ACCEL, DOM_TF, TF_LOSS

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| DELTA_EXIT_DOM_TF_ENABLED | True config.py:1719 | WEAK | Y | Y | - | 2026-04-10 04:15 APPLIED — winner per user "yes apply" (was False) |
| DELTA_EXIT_ENABLED | True config.py:1679 | WEAK | Y | Y | Y |  |
| DELTA_EXIT_SPEED_DECAY_VEC_ENABLED | False config.py:4102 | WEAK | Y | Y | - |  |

### X2 floors — `FLOOR|DYNAMIC_SCORE` — 3 switches

MT floors, dynamic score counter, HTF W reversal

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| DYNAMIC_SCORE_COUNTER_EXIT_ENABLED | True config.py:4331 | WEAK | Y | Y | - | 2026-04-20 le_dynamic winner: exit when opposite-direction LE score >= |
| HARD_BREAKEVEN_FLOOR_ENABLED | True config.py:1981 | WEAK | Y | Y | - | ⚠️ DO NOT DISABLE WITHOUT EXPLICIT USER PERMISSION |
| MAKER_CLOSE_COMMISSION_FLOOR_ENABLED | False config.py:2468 | WEAK | Y | Y | - | 2026-05-12 USER MANDATE: DISABLED. Was clamping SELL limits ABOVE mark |

---

## Exits — Trailing (amber, distinct from breakdown)

> Color `#b45309` · chip `amber` — Trailing protects giveback. Mostly UNWIRED — ATR_TRAIL #1 PnL destroyer OFF.

### X3 ATR trail — `ATR_TRAIL` — 3 switches

ATR_TRAIL (#1 destroyer) OFF, 2x mode OFF

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| ATR_TRAIL_2X_EXIT_ENABLED | False config.py:4172 | WEAK | Y | Y | - | BACKTEST_CHANGE_T58: was True. ATR trail = #1 stock PnL destroyer (-25 |
| MTF_ATR_TRAIL_ENABLED | True config.py:1565 | WEAK | Y | Y | Y | 2026-05-20 ON (Phase I) |
| ATR_TRAIL_SWEEP_ENABLED | None NOT_FOUND | WEAK | - | Y | Y |  |

### X3 chandelier — `CHAND|BOTTOM_A|PROTECTIVE_TRAIL` — 1 switches

Bottom A chandelier 4h/5m STDEV

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| BOTTOM_A_PROTECTIVE_TRAIL_ENABLED | False config.py:4201 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |

---

## Filters / Gates — mapped to the bucket they gate

> Color `#0369a1` · chip `sky` — Never global. Each filter gates exactly one bucket family (prevents 900x900x120x20 cartesian).

### F1 HTF Alignment → both — `HTF_ALIGN|ALIGNMENT_GATE|MTF_ALIGN` — 1 switches

HTF trend Alignment gates both Bottom and Breakout

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| HTF_ALIGNMENT_ENABLED | True config.py:4434 | WEAK | Y | Y | - | vector 1140: htf_cnt >= HTF_MIN_ALIGNED  # PORTED from TradierConfig 2 |

### F2 STRENGTH 5 → Bottom only — `STRENGTH` — 2 switches

Strength 5 kills 91% of raw Bottom fires; not applied to Breakout

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| STRENGTH_FILTER_ENABLED | True config.py:4898 | WEAK | Y | Y | - | vector 1181: score >= STRENGTH_MIN_SCORE  # PORTED from TradierConfig  |
| V8Q_STRENGTH_FILTER_ENABLED | False config.py:1643 | WEAK | Y | Y | - |  |

### F3 Zone / Time → Bottom — `ZONE_ENTRY|TIME_ZONE` — 2 switches

Zone/Time gates Bottom only

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| TIME_ZONE_ENABLED | True config.py:4917 | WIRED | Y | Y | - | BACKTEST_CHANGE_T19 enable time-of-day zone sizing  # PORTED from Trad |
| K_ZONE_ENTRY_ENABLED | True config.py:2428 | WEAK | Y | Y | - | BACKTEST_CHANGE_109: K-zone entry — enter when K in zone (<35 LONG / > |

### F4 K-Zone → Bottom — `K_ZONE` — 1 switches

K-Zone gating Bottom

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| K_ZONE_ENTRY_ENABLED | True config.py:2428 | WEAK | Y | Y | - | BACKTEST_CHANGE_109: K-zone entry — enter when K in zone (<35 LONG / > |

### F5 Volume → Breakout only — `VOLUME_GATE|VOLUME_FILTER` — 1 switches

Volume gates Breakout only

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| CATALYST_VOLUME_GATE_ENABLED | False config.py:4232 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |

### F6 OI / Red-Zone 0.5% wall → Bottom — `OI_GATE|RED_ZONE|FUNDING` — 11 switches

Red-Zone 0.5% wall, funding/OI gates Bottom

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| FUNDING_GATE_ENABLED | True config.py:677 | WEAK | Y | Y | - | 2026-04-27 default ON in live per user directive (was OFF in pre-exist |
| FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED | False config.py:4408 | WEAK | Y | Y | - | apply gate to hedge entries too (default OFF)  # PORTED from TradierCo |
| FUNDING_HEDGE_GATE_ENABLED | True config.py:680 | WEAK | Y | Y | - | apply funding gate to hedge entries too (helps "wrong moment" hedge op |
| RED_ZONE_AUGMENT_GATE_ENABLED | False config.py:717 | WEAK | Y | Y | - | apply to AUGMENT actions (don't add into resistance) |
| RED_ZONE_GATE_ENABLED | False config.py:713 | WEAK | Y | Y | - | 2026-04-28 restored — probe one-by-one to find which gate actually reg |
| RED_ZONE_GATE_FALLBACK_ENABLED | True config.py:2961 | WEAK | Y | Y | - |  |
| RED_ZONE_HEDGE_GATE_ENABLED | False config.py:716 | WEAK | Y | Y | - | apply red-zone gate to hedge entries too (stops hedging into hard wall |
| RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED | True config_tradier.py:207 | WEAK | Y | Y | - | apply to AUGMENT actions (don't add into resistance) |
| RED_ZONE_TRADIER_GATE_ENABLED | True config_tradier.py:204 | WEAK | Y | Y | - | 2026-04-28 restored — probe one-by-one |
| FUNDING_OI_INJECT_ENABLED | True config.py:693 | WEAK | Y | Y | - |  |
| RULE_C_FUNDING_EXTREME_ENABLED | False config.py:1441 | WEAK | Y | Y | - | Rule C (funding-extreme mean-reversion). Default OFF until funding gat |

### F7 Cooldown → Reentry only — `COOLDOWN|ANTI_CHURN|REENTRY_ANTI` — 2 switches

Cooldown/ART anti-churn gates Reentry only

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| TRADIER_REENTRY_ANTI_CHURN_ENABLED | False config.py:4963 | WEAK | Y | Y | - | 2026-05-10 USER MANDATE: REENTRY guaranteed — ANTI_CHURN_exit_score ga |
| TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED | True config.py:4927 | WEAK | Y | Y | - | PORTED from TradierConfig 2026-08-17 |

### F8 No-Loss → Breakdown/Trailing only — `NO_LOSS|LOSS_REQUIRES_HEDGE` — 0 switches

Universal No-Loss Gate True — blocks ALL loss closes except technical exits + hedge; LOSS_REQUIRES_HEDGE True

_No registry match for this pattern — pattern may be stocks-only / not in cross-config registry; check config_tradier directly._

### F9 Regime / LH_HL → Breakout — `REGIME_FILTER|LH_HL|ADX_REGIME` — 5 switches

Regime/LH_HL gates Breakout

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| LH_HL_FILTER_ENABLED | False config.py:744 | WIRED | Y | Y | - |  |
| ADX_REGIME_FILTER_ENABLED | False config.py:2592 | WIRED | Y | Y | - | BACKTEST_CHANGE_137: ADX<20 = sizing penalty + entry deduction. |
| VIX_REGIME_FILTER_ENABLED | True config.py:5084 | WEAK | Y | Y | - | Block entries when SPY < SMA200  # PORTED from TradierConfig 2026-08-1 |
| LH_HL_FILTER_AUGMENT_GATE_ENABLED | False config.py:749 | WEAK | Y | Y | - | apply to AUGMENT actions (don't add into reversing trend) |
| LH_HL_FILTER_HEDGE_GATE_ENABLED | False config.py:750 | WEAK | Y | Y | - | apply to hedge entries (default OFF — hedges are intentional counter-t |

---

## Sizing / Augment / Hedge (non-entry/exit)

> Color `#7c3aed` · chip `slate` — Capital and risk, not signal. VEC_UNSUPPORTED for single-position engine where noted.

### S1 sizing — `POSITION_SIZE|SIZING|TIER_SIZE` — 8 switches

Regime/EMA_DIST/ATR/DC_EDGE sizing, tier sizing

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| BAND_SLOPE_SIZING_V2_ENABLED | True config.py:1066 | WIRED | Y | Y | - | 2026-07-15 v2-v5 campaign: grad sizing uplift positive on 176-sym 6.5y |
| CONVICTION_SIZING_ENABLED | True config.py:1876 | WEAK | Y | Y | - | 2026-06-02 USER: scale base entry size by per-sym conviction (size_mul |
| ATR_ADAPTIVE_SIZING_ENABLED | False config.py:2752 | WEAK | Y | Y | - | BACKTEST_CHANGE_135: Inverse ATR sizing (high vol = smaller) |
| DC_WIDTH_SIZING_ENABLED | True config.py:2399 | WEAK | Y | Y | - |  |
| EMA_DIST_SIZING_ENABLED | True config.py:2394 | WEAK | Y | Y | - | BACKTEST_CHANGE_24: scale size by ema_dist strength |
| FG_SIZING_ENABLED | False config.py:2664 | WEAK | Y | Y | - | BACKTEST_CHANGE_141: F&G sizing multiplier (1,240% vs 680% B&H). Fear= |
| R_Z5_DC_PULLBACK_SIZING_ENABLED | False config.py:3102 | WEAK | Y | Y | - |  |
| DC_EDGE_SIZING_ENABLED | True config.py:2403 | WEAK | Y | Y | - | BACKTEST_CHANGE_122: Scale position size by DC channel position. Edge= |

### S2 augment — `AUGMENT` — 16 switches

Augment-only when profitable, pyramid

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| GUARANTEED_REENTRY_AUGMENT_ENABLED | True config.py:1865 | WIRED | Y | Y | - |  |
| PULLBACK_AUGMENT_ENABLED | None NOT_FOUND | WIRED | - | - | - |  |
| UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED | True config.py:820 | WEAK | Y | Y | Y |  |
| AUGMENT_BLOWPAST_ENABLED | True config.py:1622 | WEAK | Y | Y | - | gain >= 3×MIN_GAIN, conviction 90. Highest conviction. |
| AUGMENT_HTF_TREND_ENABLED | True config.py:1625 | WEAK | Y | Y | - | HTF trend only, conviction 65. Most frequent. |
| AUGMENT_WT_3TF_ENABLED | True config.py:1624 | WEAK | Y | Y | - | 3/3 LTF aligned + smaller gain, conviction 70. |
| AUGMENT_WT_CROSS_ENABLED | True config.py:1623 | WEAK | Y | Y | - | WT cross + aligned 2/3 TFs + gain >= MIN_GAIN, conviction 80. |
| BOUNCE_AUGMENT_ENABLED | False config.py:2040 | WEAK | Y | Y | - | 2026-08-10 USER MANDATE: NEVER augment losing positions — prohibited a |
| LH_HL_FILTER_AUGMENT_GATE_ENABLED | False config.py:749 | WEAK | Y | Y | - | apply to AUGMENT actions (don't add into reversing trend) |
| RECOVERY_AUGMENT_ENABLED | False config.py:584 | WEAK | Y | Y | - |  |
| RED_ZONE_AUGMENT_GATE_ENABLED | False config.py:717 | WEAK | Y | Y | - | apply to AUGMENT actions (don't add into resistance) |
| RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED | True config_tradier.py:207 | WEAK | Y | Y | - | apply to AUGMENT actions (don't add into resistance) |
| … +4 more | | | | | | |

### S3 hedge/ratio — `HEDGE|RATIO|REBALANC` — 58 switches

Hedge-account/ratio concepts — VEC_UNSUPPORTED, no single-position meaning

| Switch | Default | Badge | Live | Vec | Engine | Comment |
|---|---|---|---|---|---|---|
| HEDGE_BANDAID_OFF_ENABLED | False config.py:855 | WIRED | Y | Y | - | 2026-05-29 USER: ZERO hedging anywhere. Only path emitting HEDGE-tagge |
| DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED | False config.py:1097 | WEAK | Y | Y | Y |  |
| HEDGE_DC_RESISTANCE_GATE_ENABLED | False config.py:623 | WEAK | Y | Y | - | 2026-04-26: OFF — was blocking hedges precisely when needed (V3 SHORT  |
| HEDGE_DECAY_NUKE_ENABLED | True config.py:1155 | WEAK | Y | Y | - |  |
| HEDGE_FAILED_FALLBACK_CLOSE_ENABLED | True config.py:1845 | WEAK | Y | Y | - | on hedge failure → close (HEDGE_FAILED bypass already in NOLOSS list) |
| HEDGE_TRIGGER_GR_SCORE_ENABLED | True config.py:1843 | WEAK | Y | Y | - | USER 2026-05-13: add GR HTF vote score as 3rd confirmation path. Hedge |
| HEDGE_WT_VEL_GATE_ENABLED | False config.py:626 | WEAK | Y | Y | - | 2026-04-26: OFF — same reason as DC gate above. Hedge-the-bleeder must |
| OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED | False config.py:1839 | WEAK | Y | Y | - | master switch for the periodic loop |
| OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED | False config.py:3423 | WEAK | Y | Y | - |  |
| SENTIMENT_REBALANCER_ENABLED | False config.py:4860 | WEAK | Y | Y | Y | 2026-05-26 USER MANDATE — KILLED after trc/IBIT_LONG -$80k/mo bleed. p |
| UNDERWATER_HEDGE_OR_CLOSE_ENABLED | True config.py:860 | WEAK | Y | Y | Y |  |
| EOD_SLIM_RATIO_ENABLED | False config.py:4374 | WEAK | Y | Y | - | 2026-06-02 USER MANDATE: OFF. last_hour_balancing_loop EOD_SLIM_RATIO  |
| … +46 more | | | | | | |

---

## Parity & Wiring Gaps (measured, not estimated)

- tradier_manage distinct getattr _ENABLED: 165

- backtest_v8_engine distinct: 62

- vec_paths distinct: 154

- live-only (tradier minus engine/vec): 131 — parity gap, vector/engine silently ignore today

- vector-only: 131 — live never enforces; promoting vector receipt would be live-unfaithful

- uncovered in any surface: 382 — ghosts/DEAD_CONFIRMED (224 DEAD in comments)

- WIRED ≥3 usage: 41 · UNWIRED 0: 242 · WEAK 1-2: 381


## Grouped Backtest Plan P1–P9 (replaces cartesian)

| Bundle | Fixes | Pairs with | Gates only with | Promotion gate |
|---|---|---|---|---|

| P1 Bottom Bounce | A1 BOUNCE DONCHIAN | X2 breakdown (DC confirm) | F2 STRENGTH5 + F3 Zone + F6 Red-Zone + F1 HTF | 1yr × ≥100 stocks × ≥30 trades/sym · pool_sharpe ≥1.0 |

| P2 WT Dip | A3 WT | X1 Top scorer + X2 structure | F1 HTF + F7 Cooldown | same |

| P3 Band | A4 BB/Band LR_BAND | X2 STDEV/BB reject | F2 STRENGTH + F4 K-Zone | same |

| P4 DC/Delta Breakdown | B1 DC + B2 DELTA | X2 DC/BB + X2 Delta decay | F5 Volume + F9 Regime/LH_HL + F1 HTF | same |

| P5 Trend+Trailing | B3 Arrow/GR HTF | X3 ATR/chandelier | F9 Regime | same |

| P6 RSI2 Extreme | A5 RSI2 | X1 regime + X2 floors | F2 STRENGTH + F1 HTF | same |

| P7 Reentry stress | R1-R4 PULL/B-blocks/Tiers | — | F7 Cooldown only | same |

| P8 Filter ablation | — | — | F1-F9 leave-one-out per bucket | same |

| P9 No-Loss guardrails | — | X2/X3 loss exits ONLY | F8 No-Loss technical bypass | same |


## What wiring would touch (NO FILE EDITED THIS TURN — report only)

- Per LOCKED_FILES.md all 8 engines are RELOCKED 2026-07-21 structural-exit veto. A real wiring run would `cp backups/before_<desc>_<ts>.py` → edit tradier_manage.py (+ wt_dc_delta.py) live function + its vector twin (vec_paths/* scalar + precompute + v8_quick_engine compute_entry/exit_signals) + backtest_v8_engine.py (_cfg/getattr(tm_mod.config,…) + V8_OVERRIDE delta map) per family, fails-open when _ENABLED False, side-aware, gated behind its group's filter family only. BACKTEST_BIBLE §1/§13/§16.50 VECTOR_LIFECYCLE + metrics_guard + causal next-RTH fill is admission gate; DEAD_CONFIRMED handled as VEC_UNSUPPORTED + owner note, not silent pass. S1 sync is hash-verified sandbox only.

- To proceed: issue explicit `unlock <file>` per LOCKED_FILES.md Step 0b for each bundle you want wired (e.g. `unlock tradier_manage.py config_tradier.py backtest_v8_engine.py v8_quick_engine.py` plus specific vec_paths/*). Then family agents can be respawned shared-checkout (worktree_isolation:false) with crash-isolated re-queue.
