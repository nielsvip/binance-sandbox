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

## P0-stocks merge 2026-10-05 ~00:15 UTC (12 hooks, tradier_manage.py, 16/16 twin green)
- 8 INERT at defaults: DD_BOUNCE_ENABLED, FG_FEAR_THRESHOLD (active iff val!=25),
  FG_GREED_THRESHOLD (iff !=75), HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN,
  MIN_HOLD_BARS, MTF_DC_REJECT_USE_DC4 (fail-open), STOCH_CROSS_ENTRY_TRADIER,
  TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION (fail-open: dc_width_1h_prev missing).
- 4 BEHAVIOR-CHANGING (P0 mission: live adopts backtest-measured thresholds):
  TRADIER_STOCH_ENTRY_LONG 35->30 (promoted 15/30 now honored),
  TRADIER_STOCH_EXTREME_LONG 20->15 (promoted 15), ENTRY_SHORT 65->70 (prom 52),
  EXTREME_SHORT 80->85 (prom 85). Old STOCH_* keys have ZERO promotions (fossils).
  CAVEAT: threshold parity only — live zone-OR-extreme-OR-cross formula differs
  from vector band (k<entry & k>extreme); full formula unification is open work.
- Merge repairs by parent: MTF_DC insert relocated above tuple-unpack (child
  anchor split the statement -> SyntaxError, fixed); 4 stoch twin-indirections
  replaced with direct _cfg reads (scan-blind lambda form -> scan-visible).

## P0-crypto-A merge 2026-10-05 ~00:25 UTC (10 hooks, ez_manage.py + ez_positions_quick.py, 32/32 twin green)
- ALL 10 INERT at defaults (verified per-hook: arity + scope + gate default).
  Promoted-active values exist (ABLATION_*=True, AUGMENT_ONLY...=True,
  BB_RECOVERY=True, CSG=60.0) -> live now honors them = measured behavior.
- Scan fix: _sw_get added to CALL_READ_FUNCS (genuine per-sym getter, same
  precedence as _psym_get) -> 13 promotions incl BB_RECOVERY + AUGMENTED_DC_BREAK,
  KEY_LEVEL_CRASH, PARABOLIC_EXIT, REENTRY_GRACE_MINUTES, TREND_REGIME_VETO,
  WT_CROSS_EXIT_3M_VETO_MAX_AGE (crypto+stocks); 2 DEAD->LIVE_ONLY; 0 demotions.
- 43 needs-decision filed (tradier-gated vector reads, unit mismatch COOLDOWN_BARS,
  dead-reads, no ez parent; several marked stocks-lane items for follow-up).

## P0-crypto-B merge 2026-10-05 ~01:00 UTC (11 hooks ez_manage.py + 5 config.py fields, 45/45 twin green)
- INERT at defaults (4): K_ZONE_VETO_ENABLED_TRADIER, STOCH_CROSS_ENTRY_TRADIER,
  VWAP_FILTER_ENABLED, WT_DC_K5M pair (master False). SATOSHIT_EXIT x4 inert via
  twin master SATOSHIT_EXIT_ENABLED=False (config False, QuickConfig False).
- BAKED VETOES now live (4, verified unconditional in vec entry mask v12:9392/9600):
  WT_DC_STOCH thresholds, WT_DC_HTF (forced 4h_d), WT_DC_DC_POS_MIN 0.20,
  WT_DC_FINAL_SCORE_MAX 0.40. Live entries now match measured strictness.
- NEW LIVE OPEN PATHS (2 elifs, ACTIVE at merge — maximum flag):
  WT_DC entry leg (config True = template bold/QuickConfig/tradier unanimous;
  mirrors v12:9425 scorer block) and DC_DAYTRADE entry leg (masters True
  everywhere already; live had exit leg only). Both carry r1-stop +
  duplicate-open-cooldown guards, grafted on the entry-port elif chain.
  NOTE v12:4470: DC Daytrade sits on the banned-without-approval list — masters
  were already True; this wires the missing live entry leg, no master flipped.
- Config.py adds (vec/tradier-unanimous values, CORRECTED from child 0.0/inf which
  would have neutered the baked vetoes): WT_DC_ENABLED True, DCPOS_MIN 0.20,
  FINAL_MAX 0.40, K5M False/20.0. cat_side MISS for these (defaults lane owns).
- HELD: K3M_FLOOR (live already has real-3m floor; hook would AND a 15m proxy =
  stricter than measured -> operator decision), _IMPORT (dead scaffolding).
- 40 needs-decision filed (dedupe applied).

## Market-open proof loop 2026-10-05 ~02:05 UTC (operator: all tests cover all new switches)
- Repaired 2 stale twin tests (fleet drift, not wire-push breakage):
  reentry_staged slice anchor 8261->8266 (+5, block intact); vec_special SIMPLE
  premise updated (lane B3 wired it live, pinned inert + provenance).
- Closed vec_special remainder: DELTA_PYRAMID_MAX LIVE_ONLY->WIRED_BOTH via
  twin_vec_special.delta_pyramid_adds_allowed (consumed cap read, default 8 =
  config/tradier/QuickConfig unanimous; mirrors DeltaTracker pyramid_max).
- New test_wire_push_contract_20261005.py (151 tests): spec-driven hook-presence
  pins for every merged switch (direct + twin-mediated), 5 P0 config pins,
  22 firing tests for previously untested twin functions. Full twin suite:
  507/507 green across 19 files.
- Watch: tools/twin_watch.sh re-runs the 19-file suite every 10 min until
  2026-10-05 13:00 UTC (log data/reports/twin_watch_20261005.log, fails only
  to monitor) + one-shot cron final proof 12:30 UTC. P0 specs persisted to
  repo root (hook_spec_p0_*.json) as durable merge evidence.

## NPZ width_prev fleet patch 2026-10-05 ~14:00 UTC (operator: zeros in sheets)
- Root causes found for sheet zeros: (1) matrices predate cut#5 vec wiring
  (IBIT 01:32 vs engine 01:37; VLO Oct 2; AXON Oct 4) -> stale measured zeros,
  re-sweep fixes; (2) dc_width_1h_prev missing from all NPZs -> daytrade
  1h-expansion gate fail-open (now fixed); (3) final_score/trend_val LT keys
  missing -> hard-short LT gate vetoes on 0.5 default (OPEN, see below).
- Servers cleared: S1/S4/S5 all on cut#5 v12 92feb8f3 (12 twins); S1 config
  has P0 fields. Zero-count NOT a version problem.
- Fix shipped: backtest_v8_precompute.py emits dc_width_{tf}_prev (roll-first,
  Mac-side, needs deploy for future recomputes); tools/npz_add_width_prev.py
  backfilled all 668 S1 NPZs (atomic, idempotent, verified exact); S1 sync
  pushed to .4/.5/.6, verified True/946 keys on S4+S5. test_npz_width_prev.py
  green (convention pins).
- LT SCORES UNFILLABLE historically: no ranking history exists (only current
  snapshot, Aug 20). trend_val_raw = price-derived (weighted TF slopes) but
  normalized against cross-symbol globals per ranking run -> per-bar
  reconstruction needs a 2-pass design (per-bar slopes + global history).
  final_score = composite incl sentiment -> no history at all. OPERATOR
  DECISION needed: (a) reconstruction project, (b) forward-record only,
  (c) leave gate strict-veto (current, parity-consistent both sides).
- Infra flags (not this lane): S1 disk 97% (10G free), mem 1G avail; gateway
  jump path down mid-run (used s1-pub + S1-hop); S4 default python3 lacks
  numpy (herd env may differ).

## Sentiment-from-klines rebuild 2026-10-06 ~00:45 UTC (operator order)
- Found: market_sentiment_score existed but 597/668 files were stale flat-50
  (bias healthy, grids overlap -> old bad write). Formula is kline-derived
  (cross-sym WT-bias ratio) = honest reconstruction, no external feed needed.
- tools/npz_sentiment_force.py: same formula, OOM-proof (sqlite score table,
  zip-surgery writes, --only-flat finisher). Survived 4 killed attempts
  (S1 mem pressure); final: 667/667 varied (IBIT std 25.19, 19k uniques).
  1 holdout ETH.npz (orphan, not swept, partial regen without bias) left
  for the normal regen cycle.
- Synced S1->.4/.5/.6 via sync_indicators.sh; IBIT md5 identical on all 3.
- Note: this is MARKET sentiment. Ranking LT scores (final_score_norm_lt /
  trend_val_norm_lt) remain unfilled (no ranking history) per prior entry.
