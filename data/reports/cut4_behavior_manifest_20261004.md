# Cut#4 behavior-change manifest — wiring push wave 1 (2026-10-04)

Source: user mandate "ALL switches hooked up TEMPLATE->config->vector AND live".
Status: Mac-only. Nothing below is live until cut#4 deploys.
Proof bundle: 136/136 twin tests green (`pytest test_*_twin.py`, 10 suites).
Rule: every ACTIVE item fires on synthetic enabled-input AND stays inert at
defaults/off AND agrees vec-vs-live formula. Anything without firing proof is
HELD (see bottom).

## ACTIVE behavior changes (intended deltas, gate per-item)

| Switch | Venues | Intended delta | Firing proof |
|---|---|---|---|
| WT_CROSSUNDER_FINAL_ENABLED (True/True) | crypto+stocks exits | NEW exit: wt1 crossunder + k>=70 (long, 3m crypto / 5m stocks) | test_entry_ports_b_twin.py::test_crossunder_exit_both_bases + test_vec_formula_agreement_grid |
| EMA20_SLOPE_ENTRY_ENABLED (True/True) | crypto entries | NEW entry proposals on ema20-slope signal | test_dist_slope_vwap_boundaries (fire + boundary) |
| SMA200_DIST_ENTRY_ENABLED (True crypto; False stocks=inert) | crypto entries | NEW entry proposals on sma200-distance | test_dist_slope_vwap_boundaries (96.9 fires / 97.0 not) |
| VWAP_BOUNCE_ENTRY_ENABLED (True crypto; False stocks=inert) | crypto entries | NEW entry proposals on vwap-bounce + DIST_PCT | test_dist_slope_vwap_boundaries |
| MFI_ENTRY_ENABLED (True crypto; False stocks=inert) | crypto base entries | NEW AND-veto in check_entry_alignment (mfi_1h LONG<60 / SHORT>40) | test_simple_entries_boundaries (59.9 pass / 60.0 block both sides) |
| RSI2_ENABLED (True/True) | crypto entries (stocks pre-wired) | NEW crypto entry proposals on RSI2 extremes | test_gates_sizing_a_twin.py::test_rsi2_connors_boundaries |
| VEL_EXIT_ENABLED (True/True) | crypto exits (stocks pre-wired) | NEW crypto velocity exit | test_vel_and_decay_boundaries |
| FH_MOMENTUM_ENABLED (True/True) | crypto (stocks pre-wired) | NEW crypto momentum gate/proposals | test_fh_boundaries |
| SBA_BOUNCE_ENABLED (True/True) | crypto (stocks pre-wired) | NEW crypto bounce gate on base entries | test_sba_hand_computed + test_sba_score_agreement_spot |
| KINDERGARTEN_CUMULATIVE_MODE (True, added to config.py) | crypto KG gate | cumulate-all vs single-pick in KG filter | test_kg_modes |

## Inert wiring (zero behavior change at defaults, 104 names)

TECHNICAL_DC x4, NOLOSS/STOP_LOSS/DC_RECOVERY/BYPASS x6, BB x2,
STOCH_CROSS_3M, entry-ports-A x12, entries-dead-A/B x24, exits-dead x11,
entry-ports-B OFF-switches x15 (WT_15M_BOUNCE x7, WT/STOCH/SATOSHIT/BB_PCTB/
BAND_ARROW/WT_DC_DETAILED_SCORER/BB_BOUNCE), gates-sizing-A OFF-switches x9,
yellow x15. Each: OFF-master or empty-TF-list at defaults; twin suites assert
inert-default + boundaries + formula agreement.

## HELD (not in cut#4, needs operator call)

- EXIT_VELOCITY_WT_TFS: no ENABLED master, TFS default 1h,4h,D = always-on
  exit. Hook drafted then REMOVED. Needs H9 decision (add master vs OFF default).
- Yellow hot-path rewrites: profit-target reorder, reduce-reason rewrite,
  pos-dict ty_* fields (unneeded — .get defaults cover; reorder changes exit
  priority; rewrite risks reason-parity).
- COOLDOWN_BARS: vec 3 bars (~45min) vs live REENTRY_COOLDOWN_S 300s — wiring
  changes live reentry timing. Needs unit decision.
- SIMPLE_PRICE_GT0_ENABLED: vec debug switch. Recommend TEMPLATE row deletion.
- INTRADAY_RATIO_* x4 + EOD_SLIM_RATIO_ENABLED: portfolio-level, single-position
  engine cannot model. Recommend greying rows.
- 19 orphan yellow filters (kind=none, no parent behavior anywhere):
  BREAKEVEN_GAIN_EROSION, CIRCUIT_SHARPE, DC_MOMENTUM_BOTA, DELTA_ENGINE,
  EMERGENCY_BRAKE, EXIT_TIGHT_BREAKOUT_SCORER, EXIT_TO_REDUCE_ADAPTER,
  FIRST_OPEN_THROTTLE, GOLDEN_RULE_ENFORCE, GOLDEN_RULE_HTF_VOTE,
  GR_FILTER_VEC, GR_V5_STATE, HAIKU_WINNER, LIVE_ENTRY_ENGINE,
  LIVE_ONLY_SIGNALS_BATCH5, MTF_ARMED_ENTRIES, NOLOSS_BYPASS_WT5OF5,
  NEWBORN_PROTECT, OPEN_INTENT_SIZE_GATES, PARTIAL_PROFIT_LOCK_V2
  (all *_FILTER_TF). Recommend yellow-header removal (dead yellows waste
  compute; G is d_all so no promotion inflation — verified v15_pilot:7201).
- WT_DC_DETAILED_TF: in NO config, no reads anywhere. Needs full spec or row
  deletion.
- WT_DC_DIRECT_DC_TF / WT_DC_DIRECT_TF_ENTRY: DEAD/DEAD, no parent found yet
  (vec-special child investigating).
- MTF_ATR_TRAIL / BREAKOUT_RETEST / PEAK_GIVEBACK / EXIT_TOP_FADE yellows:
  staged vec, unstaging in wave 2.
- EZ_MANAGE_THROTTLER_RATE (white row, infra knob): recommend row deletion.
- TRADIER_MFI_ENTRY_LONG_ENABLED in crypto templates: venue-wrong row,
  recommend removal from CRYPTO sheets (stocks twin wired).

## Companions newly wired-but-unswept (updater queue, COVERAGE +10 wave 1)

MU_CORRECTION_SYMBOLS, MU_CORRECTION_HTF_* / REQUIRE_* etc. — read by merged
twins, no template rows. Harmless (defaults = today's behavior).
