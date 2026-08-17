# Important Params — Consolidated from Already-Collected Sweep Results

Built 2026-07-07. Read-only consolidation — no new sweeps were run. Sources gathered: `data/pilot_ranges_{crypto,tradier}_pool8.json`, `data/pilot_ranges_{crypto,tradier}_nk8.json`, `data/pilot_ranges_BTCUSDC_SHORT.json`, `data/pilot_ranges_MU_LONG.json`, S1 `data/ofat_progress/{crypto,tradier}_screen.csv` (in-progress OFAT), `data/sweep_tiers.json` (hand-curated, 2026-04-16), `data/ofat_manifest_{crypto,tradier}_wide.json` (the 627/748 sweepable-param universe definitions), and `data/test_results_central.db` (`runs` table, 247,219 rows with usable `overrides_json` + `pool_sharpe`).

Full per-param detail: `data/important_params_crypto.json`, `data/important_params_tradier.json`. Ranked, highlighted spreadsheet: `SPREADSHEETS/IMPORTANT_PARAMS.xlsx` (sheets: README, Crypto, Tradier).

**Denominator note**: the raw `Config`/`TradierConfig` dataclasses actually have ~2,045 / ~1,790 total attributes — far above "~800." The 627 crypto / 748 tradier figures used throughout (and in this consolidation) are the harness's own "sweepable" manifest (`ofat_manifest_*_wide.json`) — numeric/bool/threshold-style knobs judged worth testing. That manifest is the correct denominator for "which of the ~800 keys are important" as the user meant it; the other ~1,400/1,000 raw attributes are paths, strings, structural/internal fields never intended for sweeping.

## Honest coverage — the real number

| | Crypto (627 sweepable) | Tradier (748 sweepable) |
|---|---|---|
| **CONFIRMED_MOVER** (\|Δpool_sharpe\| > 0.02, clean single-param-at-a-time evidence) | **1** (0.2%) | **19** (2.5%) |
| TESTED_FLAT (tested, no meaningful movement) | 144 (23%) | 417 (56%) |
| ENGINE_BLOCKED_ZERO_TRADES (tested, but 0 trades under every value — sensitivity unmeasurable) | 7 (1%) | 0 |
| UNWIRED_OR_DEAD_UNCONFIRMED (override mechanism reports "not a live config attr" or "config attr but engine doesn't read it") | 132 (21%) | 312 (42%) |
| UNTESTED_NO_DATA (appears nowhere in any gathered source) | 343 (55%) | 0 |

**Bottom line: of 627 crypto params, only 1 has confirmed-mover evidence and 343 (55%) have never been touched by any test in the gathered corpus. Of 748 tradier params, 19 have confirmed-mover evidence, 417 are tested-and-flat, and every param has at least some data point somewhere (0 fully untested) — but "some data point" is frequently a single n=2-arm single-symbol diagnostic, not real coverage.**

This is much thinner than "days of collected sweep work" suggests. Two structural reasons, both worth fixing before the per-key phase runs:

1. **The crypto pool8 sweep (the only pooled, multi-symbol, clean-OFAT crypto source) produced ZERO trades on every one of the 35 params it touched** (`_meta.DIAGNOSTIC: "25-sym POOLED subset"`, window start 2026-05-15). Every switch in that file has `zero_delta_reason: ZERO_TRADES_ALL`. The crypto pool8 window's entry path never fired — this sweep is not informative for importance ranking, only for confirming the engine was dead-blocked in that window. Crypto's only real evidence is the `BTCUSDC_SHORT` single-symbol diagnostic (n_syms=1 — far below the 48-symbol sample floor, and even there 213 of 240 tested params came back `NOT_A_CONFIG_ATTR` — override never applied by that harness).
2. **Single-symbol pilot files (`BTCUSDC_SHORT`, `MU_LONG`) report the large majority of params as `NOT_A_CONFIG_ATTR`** (213/240 crypto, 743/749 tradier). Per memory (`project_dead_knob_audit_20260701.md`), roughly half of prior "disconnected knob" claims were false positives caused by the sweep harness's override mechanism, not real dead code — so these are flagged `UNWIRED_OR_DEAD_UNCONFIRMED`, not asserted dead. They need a code-level grep against `live_refs` before being trusted either way.

**Central DB (`test_results_central.db`, `runs` table) was mined but is NOT usable for marginal per-param importance.** It contains 247,219 rows with non-empty `overrides_json` + `pool_sharpe`, but each row changes 20-70 params simultaneously (random/genetic-search style, not one-factor-at-a-time) — confirmed by inspecting sample `overrides_json` blobs. A naive per-param delta computed from this corpus produces implausibly huge, near-uniform deltas (~1.7-3.6 pool_sharpe) for almost every param, which is a confounding artifact, not a real effect — reporting those numbers would violate the no-invented-importance rule. The DB is used only for **coverage** (has this param ever appeared in any recorded run — yes for 752/627 crypto, 614/748 tradier) and is tagged `CAVEAT: CONFOUNDED` everywhere it appears in the JSON output; it is never used to rank or promote a param.

**Every number in this consolidation is `[DIAGNOSTIC ONLY]`.** The best available crypto/tradier evidence pools 8 symbols (pool8) or 1 symbol (single-sym diagnostics) — both far below CLAUDE.md's sample floor (≥48 crypto / ≥100 stocks, >1yr, ≥30 trades/sym). None of this is promotion-grade. Its only sanctioned use is telling the per-key baseline phase which ~20-30 params to check first instead of grinding through all 627/748.

## Top movers — Crypto (1 confirmed; thin data, use cautiously)

| Rank | Param | \|Δpool_sharpe\| | Source | Best value | Live default | live_refs |
|---|---|---|---|---|---|---|
| 1 | `GR_HTF_DIRECT_EXIT_SCORE` | 0.0435 | single-sym BTCUSDC_SHORT (n=1) | 7.5 | 15.0 | — |

Next-best candidates below the 0.02 threshold (still worth an early per-key check, since crypto pool8 gave near-zero signal on anything): `MAX_POSITION_SIZE`, `START_POSITION_SIZE`, `MIN_GAIN`, `MIN_POSITION_SIZE`, `PARTIAL_PROFIT_LOCK_FRAC`, `GOLDEN_RULE_MIN_IND`, `GOLDEN_RULE_HTF_MIN_TFS` (deltas 0.005-0.008, all from single n=2-arm BTCUSDC_SHORT tests — noise-level, listed only because nothing else cleared even that bar).

**Recommendation for crypto**: before trusting any importance ranking, re-run the pool8 harness on a window where the entry path actually fires (the 2026-05-15 window was dead for all 8 symbols) and widen beyond 1 single-symbol diagnostic. Until then, crypto param importance is effectively unknown.

## Top movers — Tradier (19 confirmed, sorted by |Δpool_sharpe|)

| Rank | Param | \|Δpool_sharpe\| | Source | Best value | Useful range tested | Live default |
|---|---|---|---|---|---|---|
| 1 | `GR_HTF_DIRECT_ENTRY_SCORE_MIN` | 0.7088 | single-sym MU_LONG (corroborated by pool8 Δ=0.1401) | 3.0 (pool8) / 24 (MU_LONG) | 3, 6, 9, 12, 18, 24, 36 | 12.0 |
| 2 | `TRADIER_ENTRY_SCORE_THRESHOLD` | 0.5239 | single-sym MU_LONG (corroborated by pool8 Δ=0.2568) | 90 (pool8) / 60 (MU_LONG) | 8, 15, 22, 30, 45, 60, 90 | 30 |
| 3 | `GOLDEN_RULE_MIN_IND` | 0.4622 | single-sym MU_LONG (corroborated by pool8 Δ=0.242) | 8 (pool8) / 2 (MU_LONG) | 1, 2, 4, 5, 8 | 5 |
| 4 | `K_ZONE_LONG_THRESHOLD_TRADIER` / `TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER` (aliases) | 0.4572 | pool8 (n_syms=8) | 35 (= current default) | 9, 18, 26, 35, 52, 70, 105 | 35 |
| 5 | `WT_DC_ENTRY_THRESHOLD` | 0.4338 | pool8 (n_syms=8) | 90 | 11, 22, 34, 45, 68, 90 | 45 |
| 6 | `ENTRY_ZONE_LONG` | 0.3246 | single-sym MU_LONG only (no pool8 data) | 160 | 160 (only nonzero-trade value tested) | 80.0 |
| 7 | `DC_POSITION_ENTRY_THRESHOLD` / `TRADIER_DC_POSITION_ENTRY_THRESHOLD` (aliases) | 0.3134 | pool8 (n_syms=8) | 0.375 | 0.1875, 0.25, 0.375, 0.5, 0.75 | 0.25 |
| 8 | `MFI_LONG_THRESHOLD_D` | 0.2212 | pool8 (n_syms=8) | 80.0 (= current default) | 40, 60, 80, 120, 160, 240 | 80.0 |
| 9 | `GOLDEN_RULE_HTF_MIN_TFS` | 0.1477 | pool8 (n_syms=8) | 1 | 1, 2, 3 | 3 |
| 10 | `WT_EXIT_MIN_TFS_TRADIER` / `TRADIER_WT_EXIT_MIN_TFS_TRADIER` (aliases) | 0.0633 | pool8 (n_syms=8) | 2 | 1, 2, 4, 5, 8, 10, 15 | 5 |
| 11 | `ENTRY_MIN_ALIGNMENT` | 0.0592 | pool8 (n_syms=8) | 10 | 1, 2, 4, 5, 8, 10, 15 | 5 |
| 12 | `DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD` | 0.0373 | pool8 (n_syms=8) | 41.25 | 13.75-165 (7 pts) | 55.0 |
| 13 | `K_ZONE_SHORT_THRESHOLD_TRADIER` / `TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER` (aliases) | 0.0305 | pool8, corroborated directionally by in-progress `tradier_screen.csv` (same param moved there too) | 16 | 16-195 (7 pts) | 65 |
| 14 | `RZ_TOP_BB_THRESHOLD` | 0.0277 | pool8 (n_syms=8) | 0.2125 | 0.2125-2.55 (7 pts) | 0.85 |
| 15 | `MTF_GR_EXIT_MIN_TFS` | 0.0277 | pool8 (n_syms=8) | 1 | 1, 2, 3, 4, 6, 9 | 3 |

(Aliases collapse to 11 distinct real params. Only 19 rows total clear the 0.02 threshold — there is no top-30 to show honestly; the rest of the list below is `TESTED_FLAT`.)

**Cross-validation note**: 4 of these (`GR_HTF_DIRECT_ENTRY_SCORE_MIN`, `TRADIER_ENTRY_SCORE_THRESHOLD`, `GOLDEN_RULE_MIN_IND`, `K_ZONE_LONG_THRESHOLD_TRADIER`) show up as movers in *both* the pool8 pooled sweep and the independent MU_LONG single-symbol sweep, though best-value disagrees between the two (expected — different sample). That agreement is the strongest evidence in this whole consolidation; everything else is single-source.

**Best-value disagreement flagged, not resolved**: where pool8 and MU_LONG disagree on best value (rows 1-3 above), this consolidation does not pick a winner — that's exactly what the per-key baseline phase should resolve with a proper multi-symbol test.

## What this means for the per-key baseline phase

- **Tradier**: sweep the 19 confirmed movers first (11 distinct params after alias collapse), in the order above. Then the 417 `TESTED_FLAT` are lower priority (already known to not move the needle in pool8/MU_LONG — still worth periodic re-check since flat-under-8-symbols ≠ flat-under-100). The 312 `UNWIRED_OR_DEAD_UNCONFIRMED` need a `grep`/code read against `live_refs` before spending sweep budget — if genuinely dead, sweeping them is wasted compute.
- **Crypto**: there is effectively no reliable priority list yet. Re-running a pool8-style sweep on a live window (not the dead 2026-05-15 window) is a prerequisite — until then, treat the 7 `GOLDEN_RULE`/hedge-related `ENGINE_BLOCKED_ZERO_TRADES` params as high-suspicion candidates to unblock first, since they gate the entry path itself.
- **343 crypto params (55%) and effectively most of the 312 tradier "unwired" params have never had a real value-vs-value test** — that gap should be closed by the per-key phase rather than assumed the manifest denominator (`ofat_manifest_*_wide.json`) implies coverage; the manifest only means "the harness knows this param exists," not "it was tested."
