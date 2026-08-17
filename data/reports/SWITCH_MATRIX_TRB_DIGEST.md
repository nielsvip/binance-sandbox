# SWITCH_MATRIX_TRB progress digest — 2026-08-03 10:06:40Z

> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.

## Freshness

- Current repaired matrix latest row: `2026-07-31T01:18:45Z` (3.4d old).
- Generic DB activity (includes historical/stage tables): `2026-08-02T01:13:01Z` (1.4d old); it is not matrix freshness.
- Current repaired-contract ENGINE rows: **2,072**; new current rows in 24h: **0**.
- Raw repaired-campaign pilot rows since cutoff: **89**; **89** are preserved but invalidated by the newer code+NPZ+side fingerprint, and **0** fail validation status. Blank current cells must be regenerated; they are not silently backfilled from old code.
- Historical/pre-fix ENGINE rows quarantined from current rankings: **10,064/10,064**. They remain preserved as evidence.
- Current contract: campaign `stocks_repaired_20260730_c5`, cutoff `2026-07-30T03:30:10Z`, exact code+NPZ+side fingerprint required.
- Current stock cost contract: ordinary exact ENGINE **0.05% round trip**; new vector/research manifests **0 bps commission + 2.5 bps adverse slippage one way** (also 0.05% round trip). Historical 5+2 bps research receipts remain quarantined under their declared 14 bps cost.
- Crypto cost remains **0.08% round trip** in the engine; it was not changed to 0.8% because repository/live history does not support that tenfold value.
- VEC diagnostic rows: **84,740**; provisional amber overlay rows: **1,505**. Amber rows are valid provisional results: they count toward provisional fill and vector combination ranking; exact ENGINE credit and live promotion remain separate gates.
- Separate vector scalar-gap receipt: **UNAVAILABLE** — AAPL_LONG detail/coverage mismatch: {'screened_cells': (449, 0), 'moved': (376, 0), 'zero_trade': (73, 0), 'vec_approx_cells': (449, 0), 'vec_approx_moved': (376, 0), 'exact_replay_priority_cells': (376, 0), 'approximation_confidence': ({'LOW': 429, 'MEDIUM': 20}, {}), 'approximation_mismatch_classes': ({'BOOLEAN_FAMILY_EVENT_PROXY': 140, 'GENERIC_CAUSAL_EXIT_PROXY': 61, 'GENERIC_CAUSAL_REENTER_RECLAIM_PROXY': 41, 'INLINE_EXACT_DUPLICATE': 20, 'NORMALIZED_RANK_PRICE_PROXY': 69, 'NORMALIZED_RANK_SIZE_PROXY': 43, 'NORMALIZED_RANK_TIME_PROXY': 75}, {})}
- `SWITCH_MATRIX_TRB.xlsx`: 2026-08-03 10:04:15Z (2m old, 1,403,891 bytes)
- `SWITCH_MATRIX_TRB.csv.gz`: 2026-08-03 10:00:59Z (5m old, 50,701 bytes)
- `SWITCH_MATRIX_INTERDEPENDENCY_20260729.json`: 2026-08-03 08:40:57Z (1.4h old, 2,575,706 bytes)
- `SWITCH_MATRIX_INTERDEPENDENCY_20260729.csv`: 2026-07-29 21:24:17Z (4.5d old, 867,032 bytes)
- Description coverage in current CSV: **3,963/3,963** rows.
- Workbook axis audit: **SWITCH_MATRIX_TRB=PASS** (115 active + 20 historical = 135 visible keys; fresh=yes; missing axes=0; blank descriptions=0; formula errors=0); **PARAM_BASELINE_STOCKS=PASS** (115 active + 20 historical = 135 visible keys; fresh=yes; missing axes=0; blank descriptions=0; formula errors=0)
- Coverage contract: every current `symbols_trb_long/short` key must remain visible on every relevant path sheet. Blank/white result cells are valid; a missing row or column is an export failure.

## Stocks 5m execution provenance

Historical native 5m availability is provider-limited. Older rows use the disclosed containing-15m interpolation; native bars replace it permanently as they are collected.
Retention report scope: **230 symbols**, **229 native archives**, **8,438,120 native rows**, **1 availability warnings**, **0 hard errors**.

| key | native rows | native coverage | native range | interpolated rows | interpolated coverage | retention |
|---|---:|---:|---|---:|---:|---|
## Historical BASELINE_V2_S4H integration

Sidecar not generated.

| MU_LONG | 16726 | — | 2026-03-23 → 2026-07-31 | — | — | PASS |
| NVDA_LONG | 18924 | — | 2026-03-23 → 2026-07-31 | — | — | PASS |
| VT_LONG | 41741 | — | 2024-07-11 → 2026-07-31 | — | — | PASS |
| TTD_SHORT | 12973 | — | 2026-03-23 → 2026-07-31 | — | — | PASS |
| ACN_SHORT | 45356 | — | 2024-08-02 → 2026-07-31 | — | — | PASS |
| LAC_SHORT | 6032 | — | 2026-05-18 → 2026-07-31 | — | — | PASS |

The append/merge ledger blocks any refresh that shrinks native row count, advances the first timestamp, regresses the last timestamp, or introduces duplicate/out-of-order timestamps. `synthetic_5m_parent_close_ts` records the bounded 0/5/10-minute parent lag.

## Ordinary baseline actionability

| key | gain/mo | side B&H/mo | TIM | real closes | clamps | scheduler state |
|---|---:|---:|---:|---:|---:|---|
| MU_LONG | — | — | — | — | — | MISSING CURRENT CONTRACT |
| NVDA_LONG | — | — | — | — | — | MISSING CURRENT CONTRACT |
| VT_LONG | — | — | — | — | — | MISSING CURRENT CONTRACT |
| TTD_SHORT | — | — | — | — | — | MISSING CURRENT CONTRACT |
| ACN_SHORT | — | — | — | — | — | MISSING CURRENT CONTRACT |
| LAC_SHORT | — | — | — | — | — | MISSING CURRENT CONTRACT |

An all-exits-off ladder floor is expected to have zero real closes; high exposure makes it actionable because the exit family under test must create the closes. Only a floor under 1% TIM is entry-inert. Its exact worker is dependency-gated to source-backed ENTRY masters and ENTRY_SOURCE binding probes; helper packs never count as matrix coverage.

## Pilot-key matrix coverage (exact + provisional amber)

| key | exact differential | amber provisional | provisional completion | plateau cross-key | latest Tier-2 row | age | strategy tests |
|---|---:|---:|---:|---:|---|---:|---:|
| MU_LONG | 0/346 (0.0%) | 345 | 345/346 (99.7%) | 0/170 | 2026-07-31T01:18:22Z | 3.4d | 1 |
| NVDA_LONG | 5/346 (1.4%) | 227 | 232/346 (67.1%) | 2/170 | 2026-07-31T01:18:22Z | 3.4d | 13 |
| VT_LONG | 41/346 (11.8%) | 205 | 246/346 (71.1%) | 167/170 | 2026-07-31T01:18:45Z | 3.4d | 1,861 |
| TTD_SHORT | 0/355 (0.0%) | 243 | 243/355 (68.5%) | 3/150 | 2026-07-31T01:18:22Z | 3.4d | 81 |
| ACN_SHORT | 0/355 (0.0%) | 243 | 243/355 (68.5%) | 6/150 | 2026-07-31T01:18:22Z | 3.4d | 110 |
| LAC_SHORT | 1/355 (0.3%) | 242 | 243/355 (68.5%) | 2/150 | 2026-07-31T01:18:22Z | 3.4d | 6 |

Coverage uses the same disjoint classifier as `tools/matrix_guard.py`: only active-key, side-applicable, exact-executable differential rows enter the denominator. Plateau cross-key probes are shown separately. Historical axes, no-live-reader rows, wrong-account rows, helpers, intentional controls, and opposite-side cells are not actionable empties.
Amber VEC rows fill provisional blanks and are valid vector-combination ranking evidence; they never become exact ENGINE rows or exact promotion evidence. Duplicate campaigns do not inflate coverage.

## Vector scalar-gap diagnostic (separate amber layer)

> `VEC_DIAGNOSTIC` / `VEC_APPROX` amber rows are valid provisional fills and may be ranked for vector combination search. Exact completion credit, ENGINE/database writes, and live promotion remain separate gates. Candidates above the configured escalation threshold queue for exact V8 replay.

| key | executable scalar | parity-eligible | screened | moved | inert | zero | VEC_NATIVE | VEC_PARITY | VEC_APPROX | approx H/M/L | exact replay priority | exact credit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| UNAVAILABLE | — | — | — | — | — | — | — | — | — | — | — | 0 |

Fail-closed reason: `AAPL_LONG detail/coverage mismatch: {'screened_cells': (449, 0), 'moved': (376, 0), 'zero_trade': (73, 0), 'vec_approx_cells': (449, 0), 'vec_approx_moved': (376, 0), 'exact_replay_priority_cells': (376, 0), 'approximation_confidence': ({'LOW': 429, 'MEDIUM': 20}, {}), 'approximation_mismatch_classes': ({'BOOLEAN_FAMILY_EVENT_PROXY': 140, 'GENERIC_CAUSAL_EXIT_PROXY': 61, 'GENERIC_CAUSAL_REENTER_RECLAIM_PROXY': 41, 'INLINE_EXACT_DUPLICATE': 20, 'NORMALIZED_RANK_PRICE_PROXY': 69, 'NORMALIZED_RANK_SIZE_PROXY': 43, 'NORMALIZED_RANK_TIME_PROXY': 75}, {})}`.

## Current c5 vector-first bundle cycle

This is the fast discovery lane, not matrix coverage or promotion. It uses a $10,000 stock account denominator, $2,000 side-specific B&H capital, the $16,000/8× strategy ceiling and 0.05% round-trip cost. Each row must beat both 2× B&H/cash and its same-entry/all-exits-off control on every frozen fold before exact replay.
Current runner `026ecaa14684`; latest receipt `2026-08-02T01:37:46.665196+00:00`; exact queue **0**; historical/unbound TIM receipts quarantined **0/0**.

| key | current bundles | top performance probe | best probe inside key TIM band |
|---|---:|---|---|
| MU_LONG | 30 | `C4_WTDC55_PPL_PATIENT` 7.405% vs -4.828% (positive cash floor required); control -8.639%; TIM 41.87%; trades 32; capture_below_2x_side_bh | `C4_WTDC55_PPL_PATIENT` 7.405% vs -4.828% (positive cash floor required); control -8.639%; TIM 41.87%; trades 32; capture_below_2x_side_bh |
| NVDA_LONG | 30 | `C4_WTDC60_R3_D4H` 9.968% vs 4.977% (2.003× B&H); control 6.034%; TIM 23.68%; trades 18; capture_below_2x_side_bh; did_not_beat_same_fold_control | `C4_WTDC60_R3_D4H` 9.968% vs 4.977% (2.003× B&H); control 6.034%; TIM 23.68%; trades 18; capture_below_2x_side_bh; did_not_beat_same_fold_control |
| VT_LONG | 30 | `C4_WTDC60_R3_D4H` 0.077% vs 0.462% (0.166× B&H); control -0.665%; TIM 13.36%; trades 8; capture_below_2x_side_bh; tim_not_20_60 | `C4_WTDC55_R2_PEAK0P35` -0.415% vs 0.462% (-0.898× B&H); control -0.983%; TIM 44.03%; trades 8; capture_below_2x_side_bh |
| TTD_SHORT | 31 | `C4_ORDINARY_LADDER_DC_REJECT_1H_LB5` 4.802% vs 7.648% (0.628× B&H); control 1.816%; TIM 21.22%; trades 38; capture_below_2x_side_bh | `C4_ORDINARY_LADDER_DC_REJECT_1H_LB5` 4.802% vs 7.648% (0.628× B&H); control 1.816%; TIM 21.22%; trades 38; capture_below_2x_side_bh |
| ACN_SHORT | 31 | `C4_WTDC55_R2_PEAK0P35` -0.610% vs -3.220% (positive cash floor required); control -0.610%; TIM 75.55%; trades 18; nonpositive_return_against_cash_floor; did_not_beat_same_fold_control; tim_not_20_60 | `C4_WTDC55_R2P5_R3_D4H` -1.601% vs -3.220% (positive cash floor required); control -0.610%; TIM 27.80%; trades 270; nonpositive_return_against_cash_floor; did_not_beat_same_fold_control |
| LAC_SHORT | 31 | `C4_WTDC60_R3_D` 28.226% vs 11.084% (2.547× B&H); control 16.925%; TIM 43.24%; trades 249; nonpositive_return_against_cash_floor; did_not_beat_same_fold_control | `C4_WTDC60_R3_D` 28.226% vs 11.084% (2.547× B&H); control 16.925%; TIM 43.24%; trades 249; nonpositive_return_against_cash_floor; did_not_beat_same_fold_control |

All rejected receipts remain gray so identical code/data/bundle identities are not blindly retested. `EXACT_PENDING` is still only a queue entry; the exact same-entry control, route attribution, reentry and capacity audits decide whether a result can enter the ENGINE matrix.

### Key-scoped adaptive vector cycle

This second lane tests slower/smaller profit locks and daily-only selective R3 settings only for MU_LONG, NVDA_LONG and LAC_SHORT. VT, TTD and ACN are intentionally excluded from this generic catalog.
Current runner `026ecaa14684`; latest receipt `2026-08-02T01:17:34.987743+00:00`; exact queue **0**; historical/unbound TIM receipts quarantined **0/0**.

| key | adaptive bundles | top performance probe | best probe inside key TIM band |
|---|---:|---|---|
| MU_LONG | 15 | `C4A_MU_LONG_WTDC55_PPL_SLOW5` 0.207% vs -4.828% (positive cash floor required); control -8.639%; TIM 49.06%; trades 25; capture_below_2x_side_bh | `C4A_MU_LONG_WTDC55_PPL_SLOW5` 0.207% vs -4.828% (positive cash floor required); control -8.639%; TIM 49.06%; trades 25; capture_below_2x_side_bh |
| NVDA_LONG | 13 | `C4A_NVDA_LONG_WTDC27P5_R3_D` 8.065% vs 4.977% (1.621× B&H); control 3.356%; TIM 30.23%; trades 20; capture_below_2x_side_bh | `C4A_NVDA_LONG_WTDC27P5_R3_D` 8.065% vs 4.977% (1.621× B&H); control 3.356%; TIM 30.23%; trades 20; capture_below_2x_side_bh |
| LAC_SHORT | 16 | `C4A_LAC_SHORT_WTDC27P5_R3_D` 26.880% vs 11.084% (2.425× B&H); control 15.922%; TIM 44.20%; trades 485; nonpositive_return_against_cash_floor; did_not_beat_same_fold_control | `C4A_LAC_SHORT_WTDC27P5_R3_D` 26.880% vs 11.084% (2.425× B&H); control 15.922%; TIM 44.20%; trades 485; nonpositive_return_against_cash_floor; did_not_beat_same_fold_control |

### Targeted entry-replenishment cycle 3

This isolated 38-arm lane added the registered WT force-open entry source to the strongest MU/NVDA/LAC exits. It is diagnostic only; identical returns expose an inert or absent entry condition rather than exit alpha.
Latest receipt `2026-07-30T01:21:21.388181+00:00`; strict vector survivors **0**; historical/unbound TIM-contract receipts quarantined **0/30**. Quarantined receipts are retained as gray history and never enter either current-ranked column.

| key | targeted bundles | top performance probe | best probe inside key TIM band |
|---|---:|---|---|
| MU_LONG | 0 | — | — |
| NVDA_LONG | 0 | — | — |
| LAC_SHORT | 8 | `C4T3_LAC_SHORT_WTDC35_WF_GATED_PPL_PATIENT` 15.976% vs 11.084% (1.441× B&H); control 15.922%; TIM 72.57%; trades 111; capture_below_2x_side_bh; tim_not_20_60 | — |

### SHORT-native Donchian recovery cycle

This 31-arm TTD/ACN lane covers only after a causally completed lower Donchian downside extension and upward re-cross, with optional side-aware partial banking or winner-velocity decay. It does not invert LONG stops.
Latest receipt `2026-07-30T01:23:04.127298+00:00`; strict vector survivors **0**; historical/unbound TIM-contract receipts quarantined **0/26**. Quarantined receipts are retained as gray history and never enter either current-ranked column.

| key | short-native bundles | top performance probe | best probe inside key TIM band |
|---|---:|---|---|
| TTD_SHORT | 2 | `C4S_TTD_SHORT_DC_15M_LB3` 2.402% vs 7.648% (0.314× B&H); control 1.816%; TIM 15.77%; trades 40; capture_below_2x_side_bh; tim_not_20_60 | — |
| ACN_SHORT | 3 | `C4S_ACN_SHORT_DC_15M_LB5` 1.562% vs -3.220% (positive cash floor required); control -3.125%; TIM 15.50%; trades 27; tim_not_20_60 | — |

### BB breakout entry-replenishment cycle 4

This sealed two-arm lane adds the registered BB pullback entry to the strongest prior MU and NVDA exit bundles. It is vector research only and does not fill ENGINE matrix cells.
Latest receipt `none`; strict vector survivors **0**; historical/unbound TIM-contract receipts quarantined **0/0**.

| key | cycle-4 bundles | top performance probe | best probe inside key TIM band |
|---|---:|---|---|
| MU_LONG | 0 | — | — |
| NVDA_LONG | 0 | — | — |

## Path interpretation guardrails

- `DC_LOW4_STOP_ENABLED` and stock `R1_DC_LOW4_3M_EMERGENCY_ENABLED` (legacy name; actual stock level is `dc_low4_5m`/`dc_high4_5m`) are **ENTRY-QUALITY FAILURE DIAGNOSTICS / LOSING-CHURN EXIT EVIDENCE**. They close a recently failed entry at a tight loss; they are not top/profit-taking exits. Below-B&H observations stay gray and preserved so they are not blindly retested.
- The current `LONG_STRUCT_EXIT_TF` / `SHORT_STRUCT_EXIT_TF` path closes immediately on its selected structural break. The first armed-break → lower price/WT1 rebound-top baseline is **VEC-REJECTED / NO Tier-2 result**: `data/reports/vec_research/structural_wt_rebound_20260726T062654Z` scored 0/6 contract-valid LONG folds above side-and-hold, median alpha -4.70pp, mean TIM 77.9%, and 50 exits / 30 losing. HAO_SHORT's +20.18pp is quarantined because its NPZ contract is invalid. Profit/MFE-gated variants remain research candidates and do not fill matrix cells.
- The nested profit-gated grid `structural_wt_profit_grid_20260726T063646Z` selected one universal setting on MU+VT 2025Q4, then froze it. Validation had 9/9 winning exits but only 1/4 folds above B&H (median alpha -1.04pp). The remaining loss is reentry execution: delayed E10 reclaim filled 10.01% worse on recent MU and 1.46% worse on recent VT. A persistent resting-reclaim model is now the priority; this grid remains VEC-only.
- Frozen exit settings with causal resting reclaim (`resting_reclaim_compare_20260726T064428Z`) improved median validation alpha to +1.94pp and cut mean reclaim overshoot from 0.80% to 0.02%. Recent MU returned +13.59% versus B&H +2.60%, but VT still trailed; this is evidence for the execution fix, not a universal promotion.
- MU ladder discovery `struct_wt_resting_reclaim_probe_20260726T070000Z_MU_LONG` reached +409.93% versus B&H +204.90% (2.0006×) at 52.3% weighted exposure, but is **REJECTED versus the same-entry research control**. The source frozen 2026 ladder + 4h N=30 E02 fold returned +1,316.02% versus B&H +205.25% (6.4117×) at 77.1% exposure. The structural candidate discarded 906.09pp of return; beating B&H alone is not sufficient. Both paths remain VEC-only pending exact replay.
- Nested/frozen same-ladder validation (`struct_wt_nested_frozen_20260726T073000Z`) selected only on MU Jan-Mar, then scored MU Apr-Jul without reselection. Structural returned +359.24% (2.191× B&H) but the same ladder returned +1,380.56% (8.420× B&H): -1,021.32pp and only 0.260× of ladder return. The universal VT score improved a losing ladder (-8.90% versus -61.85%) but remained below B&H (+1.57%). Verdict **REJECT / NO MATRIX**; every exit candidate must beat both B&H and the strongest same-entry/same-window causal control.
- The complete registered `EXIT_STRUCTURAL_WT_LOWER_TOP` screen now supersedes the exploratory structural grids: 20 frozen top/bottom symbol-sides × 768 settings, with completed HTF bars, side isolation, resting reclaim and a compiled-to-Python parity gate. All 20 selected winners reproduced exactly, but **0/20 passed** both B&H + same-entry E02 and the 70–80% discovery/validation TIM gates. MU validated +1,127.00pp over B&H but -44.324pp versus E02 at 65.21% TIM; SNDK's headline used 98.70% TIM. Path-fleet job 37 preserves all rows gray and correctly queues no exact replay.
- `EXIT_PARTIAL_RUNNER` completed the corrected 192-setting registry grid on 20 frozen keys (the listed 4×4×3×2×2 fields cannot equal 128; all clip sums are valid). It produced **0 strict survivors**. Five selected validation winners had zero fast partial fills; E05/E06 filled in only 25%/40% of their validation settings versus WT 100%. The winners created 919 per-exit reclaim obligations, filled 633 and left 286 open. MU made one partial (-$107.98 net) versus ten slow full exits and lost 122.07pp to E02. Job 39 preserves all 20 rows gray and queues no exact replay.
- `EXIT_E06_REGRESSION_RETEST` completed 3,840 same-entry candidates with **0 strict survivors**. The registry is stale: `rebound_atr` is actually passed to `corr_gate`, active code has no ATR rebound/later retest state, and there is no live Tradier E06 config key. Correlation gate 1.0 was 960/960 zero-signal/zero-fill (red); all 20 frozen winners had actual signals/exits. MU was in-band at 73.46% but lost 303.33pp to E02 and left one reclaim open. Job 46 preserves 20 gray rows and queues no exact replay.
- Reentry invariant for structural research: after an exit, the stored exit/top level and reopen obligation remain latched. WT/stochastic vetoes may postpone reopening but must never erase it or allow price to outrun the stored level without reopening. This is a design requirement, not a measured performance claim.

### Preserved dc_low4 diagnostic evidence (pre-repair; gray/quarantined)

| key | switch | gain/mo | TIM | trades | status |
|---|---|---:|---:|---:|---|
| MU_LONG | False | 0.0275 | 0.13% | 17 | PRE-REPAIR / validation NULL |
| MU_LONG | True | -0.1045 | 0.21% | 103 | LOSING-CHURN SIGNATURE; validation NULL |
| MU_SHORT | False | -0.4670 | 6.89% | 48 | PRE-REPAIR / validation NULL |
| MU_SHORT | True | -0.1061 | 0.15% | 155 | NEGATIVE + HIGH-CHURN; validation NULL |

These historical rows are retained for diagnosis and excluded from the automatic matrix-fill queues. They are not valid proof because the repaired contract fields were absent.

## New ladder / entry / exit / interaction strategy results

### MU_LONG

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `STRUCTURAL_RANGE_SHIFT_TF="dc_4h"` | 55.0843 | 23.8051 | 31.2792 | 2.314× | 99.97% | 2 | B&H FLOOR ONLY |

Best trading candidate: **none with at least two trades and complete return metrics**.

### NVDA_LONG

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `BB_PULLBACK_GATE_LONG_MAX=0.45` | -0.2638 | 4.1628 | -4.4266 | -0.063× | 0.92% | 216 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `CT_MFI_15M_LONG_MIN=67.5` | -0.2638 | 4.1628 | -4.4266 | -0.063× | 0.92% | 216 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `BB_PULLBACK_GATE_LONG_MAX=0.225` | -0.2638 | 4.1628 | -4.4266 | -0.063× | 0.92% | 216 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `CT_MFI_15M_LONG_MIN=33.75` | -0.2638 | 4.1628 | -4.4266 | -0.063× | 0.92% | 216 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `ENTRY_ZONE_LONG=20.0` | -0.1612 | 4.1628 | -4.3240 | -0.039× | 0.51% | 48 | REJECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `LR_PCTB_D_LONG_ENTRY_ENABLED=true` | -0.1041 | 4.1628 | -4.2669 | -0.025× | 0.02% | 7 | REJECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `PEAK_GIVEBACK_PROTECTION_ENABLED=true` | 5.7141 | 4.1632 | 1.5509 | 1.373× | 99.94% | 3 | B&H FLOOR ONLY |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `STRUCTURAL_RANGE_SHIFT_EXIT=true` | 9.1966 | 4.1631 | 5.0335 | 2.209× | 99.97% | 2 | B&H FLOOR ONLY |

Best trading candidate: **none with at least two trades and complete return metrics**.

### VT_LONG

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FH_MOMENTUM_POSITION_SIZE=900` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FH_MOMENTUM_POSITION_SIZE=750` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FUNDING_GATE_ENABLED_TRADIER=True` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FUNDING_GATE_ENABLED_TRADIER=False` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED=False` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED=True` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER=False` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-31T01:18:45Z | stocks_repaired_20260725_c2 | `FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER=True` | -1.4421 | 1.3719 | -2.8140 | -1.051× | 2.97% | 497 | INERT / RECONNECT |

Best trading candidate: **none with at least two trades and complete return metrics**.

### TTD_SHORT

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_ENABLED=True` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_ENABLED=False` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER=True` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER=False` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=0.5` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=0.75` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=1` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=1.25` | 0.0520 | 2.8762 | -2.8242 | 0.018× | 0.01% | 1 | INERT / RECONNECT |

Best trading candidate: **none with at least two trades and complete return metrics**.

### ACN_SHORT

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_ENABLED=True` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_ENABLED=False` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER=True` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER=False` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=0.5` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=0.75` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=1` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_TF_LADDER_MULT=1.25` | -0.0054 | 2.0052 | -2.0106 | -0.003× | 0.00% | 1 | INERT / RECONNECT |

Best trading candidate: **none with at least two trades and complete return metrics**.

### LAC_SHORT

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_3M_FORCE_OPEN_ENABLED=true` | -0.0351 | 2.0766 | -2.1117 | -0.017× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `ENTRY_ZONE_SHORT=30.0` | -0.0351 | 2.0766 | -2.1117 | -0.017× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `ENTRY_ZONE_SHORT=80.0` | -0.0351 | 2.0766 | -2.1117 | -0.017× | 0.00% | 1 | INERT / RECONNECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `WT_DC_EXIT_ENABLED=true` | -2.4553 | 2.0770 | -4.5323 | -1.182× | 95.21% | 49 | REJECT |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `DELTA_EXIT_ENABLED=true` | 2.4248 | 2.0770 | 0.3478 | 1.167× | 95.20% | 57 | GRAY: BEATS B&H OUTSIDE 20-60% TIM |
| 2026-07-31T01:18:22Z | stocks_repaired_20260725_c2 | `MTF_WT_CROSS_EXIT_ENABLED=true` | 2.2003 | 2.0771 | 0.1232 | 1.059× | 78.92% | 77 | GRAY: BEATS B&H OUTSIDE 20-60% TIM |

Best trading candidate: **none with at least two trades and complete return metrics**.

## Frozen walk-forward verdict

| key | policy | discovery | frozen validation | validation TIM | verdict |
|---|---|---:|---:|---:|---|
| MU_LONG | `E02_4h_N20 + E11_G2+E10_RB0` | 32.169× B&H | 0.373× B&H | 67.75% | FAIL / NO PROMOTION |
| NVDA_LONG | — | — | — | — | PENDING |
| VT_LONG | — | — | — | — | PENDING |
| TTD_SHORT | — | — | — | — | PENDING |
| ACN_SHORT | — | — | — | — | PENDING |
| LAC_SHORT | — | — | — | — | PENDING |

This table freezes the discovery choice before reading validation. It takes precedence over each window's separately re-optimized best row.

## New causal-exit nested walk-forward

| key | families | strict frozen result | strict TIM | clean frozen result | stability | verdict |
|---|---|---:|---:|---:|---:|---|
| MU_LONG | E03/E06/E08/E09 | 47.407% vs 75.225% B&H (0.630×) | 73.14% | 575.341% vs 971.953% B&H (0.592×) | 16.7% exact repeat | REJECT / NO PROMOTION |
| NVDA_LONG | — | — | — | — | — | PENDING |
| VT_LONG | E03/E06/E08/E09 | NO STRICT FOLDS | — | 3.965% vs 9.922% B&H (0.400×) | 20.0% exact repeat | REJECT / NO PROMOTION |
| TTD_SHORT | — | — | — | — | — | PENDING |
| ACN_SHORT | — | — | — | — | — | PENDING |
| LAC_SHORT | — | — | — | — | — | PENDING |

This lane uses nested 12-month discovery with frozen three-month validation, faithful-engine cost semantics, and excludes VT folds overlapping its known source gap.

## Partial-runner and regime nested walk-forward

| key | family | frozen strategy vs B&H | weighted TIM | partial P&L / exits | stability | verdict |
|---|---|---:|---:|---:|---:|---|
| MU_LONG | E12 PARTIAL THEN RUNNER | 562.105% vs 971.953% (0.618× equity) | 81.69% | 0.8782 / 47 | UNSTABLE | REJECT / NO PROMOTION |
| MU_LONG | E13 REGIME SWITCHED | 525.663% vs 971.953% (0.584× equity) | 70.21% | 0.0000 / 0 | UNSTABLE | REJECT / NO PROMOTION |
| NVDA_LONG | — | — | — | — | — | PENDING |
| VT_LONG | E12 PARTIAL THEN RUNNER | 6.015% vs 9.922% (0.964× equity) | 83.40% | 0.0103 / 10 | STABLE | REJECT / NO PROMOTION |
| VT_LONG | E13 REGIME SWITCHED | 3.047% vs 9.922% (0.937× equity) | 79.26% | 0.0000 / 0 | STABLE | REJECT / NO PROMOTION |
| TTD_SHORT | — | — | — | — | — | PENDING |
| ACN_SHORT | — | — | — | — | — | PENDING |
| LAC_SHORT | — | — | — | — | — | PENDING |

E12 reports net realized partial P&L separately. Positive partial clips do not constitute edge when lost runner exposure and re-add timing leave compounded equity below B&H.

## Causal exit-family factorial — parity-corrected vector research

| key | best isolated pack | 3-fold strategy sum | side B&H sum | multiple | vs all-exits-off floor | TIM | fills | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|
| MU_LONG | `hybrid=OFF, DC=OFF, SRS=OFF, WT=not screened, hold=100m` | 2776.595% | 382.529% | 7.259× | 0.000pp | 74.73% | 0 | GRAY: EXIT DOES NOT BEAT FLOOR |
| TTD_SHORT | `hybrid=ON, DC=ON, SRS=OFF, WT=OFF, hold=4320m` | 1136.148% | 141.767% | 8.014× | 3.152pp | 74.03% | 60 | VECTOR LEAD; EXACT c4 RUNNING/REQUIRED |
| ACN_SHORT | `hybrid=OFF, DC=OFF, SRS=OFF, WT=ON, hold=240m` | 577.617% | 70.114% | 8.238× | 105.440pp | 66.17% | 83 | VECTOR LEAD; EXACT c4 RUNNING/REQUIRED |

TTD/ACN rows above come only from `MTF_DC_EXACT_VECTOR_PARITY_V1`: execution-row decisions against the latest causally completed current Donchian channel. The older completed-1h/prior-channel receipts are superseded and cannot be used as a digest fallback.

### Superseded pre-parity DC packs — retained gray, never headline

| key | old pack | corrected strategy sum | same-entry floor | corrected alpha | verdict |
|---|---|---:|---:|---:|---|
| TTD_SHORT | `DC ON, hybrid OFF, WT OFF, SRS OFF, hold 1440m` | 1103.584% | 1132.996% | -29.412pp | SUPERSEDED / GRAY |
| ACN_SHORT | `DC ON, WT ON, hybrid OFF, SRS OFF, hold 240m` | 467.021% | 472.177% | -5.156pp | SUPERSEDED / GRAY |

These returns are sums over the same three frozen OOS folds, not one compounded holdout. Every arm uses the 0.05% stock round trip and completed HTF bars. The vector adapter does not model stateful DELTA/RZ bottom-bounce, so even an 8× row is research-only until its isolated exact c4 replay passes route attribution, B&H, same-entry control, reentry, drawdown and fingerprint gates.

### Exact c4 finalist replays

Exact TTD is reported independently from the corrected vector table: its 1h DC result includes the exact exit/reentry lifecycle and does not resurrect the superseded prior-channel vector alpha claim.

| key/variant | strategy | side B&H | capture | TIM | real closes | cost contract | exact verdict |
|---|---:|---:|---:|---:|---:|---|---|
| TTD_SHORT/dc_only_exact | 117.2107% | 16.0786% | 7.2898× | 73.60% | 237 | 0.05% stock RT | GRAY DISCARD / STRUCTURAL OR PERFORMANCE FAIL |
| ACN_SHORT/superseded_dc_plus_wt | -28.4376% | 11.2079% | -2.5373× | 20.53% | 553 | 0.05% stock RT | SUPERSEDED PRE-PARITY PACK / GRAY |
| ACN_SHORT/dc_only | -15.8893% | 11.2079% | -1.4177× | 78.49% | 227 | 0.05% stock RT | GRAY DISCARD / STRUCTURAL OR PERFORMANCE FAIL |
| ACN_SHORT/wt_only | -22.6371% | 11.2079% | -2.0197× | 24.89% | 453 | 0.05% stock RT | GRAY DISCARD / STRUCTURAL OR PERFORMANCE FAIL |

A large dollar cost in these rows is turnover at 0.05%, not a restored crypto/legacy rate. TTD's 237 DC closes cost $1,169.80 and still netted $11,721.07. ACN's rejected DC+WT pack cost $2,357.64 across 553 closes; 405 real WT-final closes accounted for -$9,505.41 net while DC itself contributed +$6,661.65.

## Causal ladder multiplier walk-forward

| key | frozen OOS strategy | B&H | multiple | weighted TIM | exact-engine parity | verdict |
|---|---:|---:|---:|---:|---|---|
| MU_LONG | 1093.418% | 381.950% | 2.863× | 84.49% | PASS | RESEARCH EDGE; PROMOTION BLOCKED |
| NVDA_LONG | 158.074% | 43.736% | 3.614× | 74.45% | PENDING | REJECT / NO PROMOTION |
| VT_LONG | -13.381% | 13.786% | -0.971× | 90.98% | PENDING | REJECT / NO PROMOTION |
| TTD_SHORT | 681.495% | 141.483% | 4.817× | 66.42% | PASS | RESEARCH EDGE; PROMOTION BLOCKED |
| ACN_SHORT | 388.764% | 69.837% | 5.567× | 57.73% | PASS | RESEARCH EDGE; PROMOTION BLOCKED |
| LAC_SHORT | -380.752% | -12.131% | —× | 36.02% | PASS | REJECT / NO PROMOTION |

The MU exact replay covers the latest frozen fold: 34/34 actions, +1,316.021% return, 77.09% weighted TIM, zero future HTF sources, zero clamps, and exact signal/fill/accounting parity. It remains research-only because the campaign explicitly sets `promotion_allowed=false`. VT fails frozen OOS; HAO remains data-quarantined.

## MU stable-ladder Pareto holdout

| candidate | discovery | untouched holdout | B&H | multiple | TIM | exact | verdict |
|---|---|---:|---:|---:|---:|---|---|
| C151 `DAILY_DEEP_center_plateau_green_TARGET_CAP8_close_confirm_next_availability` | PASS | 1170.700% | 205.252% | 5.704× | 68.62% | PASS | GRAY / HOLDOUT REJECT (weighted_tim_70_80, return_sacrifice_compensated) |

This lane used a preregistered Pareto contract before opening the final fold. A strong return cannot repair a missed exposure gate after the result is known; the row therefore remains gray and is not written to the promotion matrix.

## HAO SHORT-native phase 3

| contract | candidates | E02 beat side benchmark | E05 beat side benchmark | TIM-valid | final | exact | verdict |
|---|---:|---:|---:|---:|---|---|---|
| `HAO_SHORT_NATIVE_PHASE3_V4` | 48 | 12 | 12 | 0 | SEALED_NOT_EVALUATED | NOT_RUN_DISCOVERY_GATE_FAILED | GRAY_REJECTED_QUARANTINED |

V1–V3 were explicitly invalidated. V4 fixes the persistent-reentry state contract, uses the shared completed-parent clock, and keeps the final fold sealed because no discovery candidate met every exposure/control gate.

## HAO SHORT exposure/persistence phase 4

| contract | discovery strict | D1 strategy / B&H / TIM | D2 strategy / B&H / TIM | final strategy / B&H / TIM | exact | verdict |
|---|---:|---|---|---|---|---|
| `HAO_SHORT_EXPOSURE_PHASE4_V3_RECLAIM_CONFIRM` | 3 | 536.61% / 67.39% / 77.95% | 174.14% / 20.64% / 71.28% | 1746.50% / 99.78% / 36.05% | NOT_RUN_FINAL_GATE_FAILED | GRAY_DISCOVERY_PASS_FINAL_TIM_FAIL |

The frozen phase-4 E02 policy achieved 70–80% weighted exposure and beat side-aware B&H plus the phase-3 same-exit control in both discovery folds. Its untouched final return remained strong but weighted TIM fell to 36.05%; the row is gray, exact did not run, and no matrix/live state changed.

## Non-MU pilot deployed-alpha rescreen

> Selection uses return per pre-cost committed dollar-time against the better of side-aware B&H and cash. Ratios require positive B&H >=20pp. Fold 3 stays sealed unless both discovery folds pass; private schedule replay is not ordinary-engine/live parity.

| key | source | profile | fold 1 deployed alpha / TIM | fold 2 deployed alpha / TIM | fills / clamps | final | verdict |
|---|---|---|---|---|---|---|---|
| TTD_SHORT | PASS | `$8000/N30` | 15.246pp / 70.22% | -25.852pp / 77.61% | 52/0 | SEALED | NO_DISCOVERY_SURVIVOR_GRAY |
| ACN_SHORT | PASS | `$8000/N20` | -14.417pp / 66.71% | -7.373pp / 78.22% | 70/0 | SEALED | NO_DISCOVERY_SURVIVOR_GRAY |
| NVDA_LONG | PASS | `$4000/N20` | 4.087pp / 82.50% | -1.441pp / 77.74% | 44/0 | SEALED | NO_DISCOVERY_SURVIVOR_GRAY |
| VT_LONG | BLOCKED: SYNTHETIC_PARENT_CLOCK_MISSING | `—` | — | — | 0/0 | SEALED | SOURCE_BLOCKED_GRAY |
| LAC_SHORT | PASS | `$8000/N20` | 0.315pp / 84.24% | -84.328pp / 75.72% | 50/0 | SEALED | NO_DISCOVERY_SURVIVOR_GRAY |

No key may advance from this section without stable discovery, a real ordinary `backtest_v8_engine` decision-path run, and live state-machine parity. A vector/private-schedule pass remains gray.

## Top-10 ladder exposure retune

> Frozen nested-OOS research. Aggregate exposure can hide unstable folds; a row is not promotable unless every fold also passes the fixed-control, causality, capacity, reclaim, and exposure gates.

| key | strategy | B&H | multiple | identical control | alpha control | TIM | fold TIM | fills | clamps | exact | verdict |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---|
| AMD_LONG | 1815.895% | 204.780% | 8.868× | 1683.799% | 132.095pp | 89.18% | 85.08% / 94.05% / 88.33% | 50.9% | 86 | — | REJECT / NO PROMOTION |
| ARM_LONG | 1227.431% | 123.020% | 9.977× | 1150.824% | 76.608pp | 73.68% | 79.94% / 53.85% / 86.19% | 89.6% | 18 | — | REJECT / NO PROMOTION |
| DINO_LONG | 726.483% | 118.221% | 6.145× | 531.313% | 195.170pp | 72.21% | 90.61% / 66.77% / 61.07% | 42.6% | 146 | PASS | RESEARCH EDGE; FOLD/CONTROL BLOCKED |
| INTC_LONG | 942.703% | 214.032% | 4.405× | 1466.298% | -523.595pp | 61.14% | 76.39% / 21.57% / 83.70% | 36.3% | 168 | — | REJECT / NO PROMOTION |
| MPC_LONG | 291.563% | 103.592% | 2.815× | 163.185% | 128.378pp | 61.95% | 87.54% / 77.21% / 25.52% | 42.2% | 97 | — | REJECT / NO PROMOTION |
| MRVL_LONG | 1354.261% | 104.106% | 13.008× | 1283.089% | 71.172pp | 74.83% | 81.65% / 69.44% / 73.70% | 75.2% | 54 | — | REJECT / NO PROMOTION |
| MU_LONG | 2062.320% | 381.950% | 5.399× | 2503.082% | -440.761pp | 58.53% | 85.41% / 21.08% / 69.04% | 54.2% | 76 | — | REJECT / NO PROMOTION |
| PBF_LONG | 672.250% | 127.673% | 5.265× | 642.127% | 30.123pp | 39.52% | 18.76% / 10.71% / 84.08% | 100.0% | 0 | — | REJECT / NO PROMOTION |
| SNDK_LONG | 3068.497% | 888.467% | 3.454× | 5414.039% | -2345.543pp | 80.44% | 70.82% / 89.16% | 100.0% | 0 | — | REJECT / NO PROMOTION |
| VLO_LONG | 442.428% | 113.267% | 3.906× | 411.173% | 31.255pp | 53.11% | 10.75% / 71.17% / 73.74% | 100.0% | 0 | — | REJECT / NO PROMOTION |

The retune keeps exits fixed at completed-4h E02 N=30. It changes only the bounded ladder curve/trigger semantics, so alpha against the identical control does not come from a different exit or a different B&H budget.

## Isolated vector research — not matrix evidence

> Fast causal screens only. These rows never fill or color Tier-2 cells. Promotion requires an independent audit, a faithful-engine replay, stable out-of-sample behavior, real closes, and a changed trade fingerprint.

| key | window | best visible candidate | gain | B&H | multiple | TIM | data/policy status |
|---|---|---|---:|---:|---:|---:|---|
| MU_LONG | 2024-01-01 → 2025-07-01 | `E02_4h_N20 + E11_G2+E10_RB0` | 70.164% | 2.181% | 32.169× | 76.01% | EXPOSURE/RECLAIM PASS; FAITHFUL REPLAY PENDING |
| MU_LONG | 2024-01-01 → present | `E02_4h_N30 + E11_G2+E10_RB0` | 1377.872% | 666.389% | 2.068× | 75.05% | EXACT EXECUTION REPLAY PASS; ROBUSTNESS/PROMOTION BLOCKED |
| MU_LONG | 2025-07-01 → present | `E02_4h_N20 + E11_G0.5+E10_RB0` | 458.076% | 651.969% | 0.703× | 76.28% | BELOW B&H; REJECT |
| NVDA_LONG | — | — | — | — | — | — | NO VEC_RESEARCH ARTIFACT |
| VT_LONG | 2024-01-01 → present | `E04_4h_EMA20_B0_R0.25_W12 + E11_G1+E10_RB0` | 30.364% | 32.656% | 0.930× | 78.99% | BELOW B&H; REJECT |
| TTD_SHORT | — | — | — | — | — | — | NO VEC_RESEARCH ARTIFACT |
| ACN_SHORT | — | — | — | — | — | — | NO VEC_RESEARCH ARTIFACT |
| LAC_SHORT | — | — | — | — | — | — | NO VEC_RESEARCH ARTIFACT |

The full-period MU multiple is an optimization-screen headline, not a robust claim. Read the holdout row beside it: exposure drift or sub-B&H holdout performance blocks promotion even when the full-period row is above B&H.

## Recent-trade coherent-bundle screen

> Finite vector research companion, not another daemon. It cannot write live configuration or canonical ENGINE cells; only strict frozen-fold survivors can become exact-replay candidates.

- Campaign: `recent30_bundle_b8f5b6d9b28f`; freshness 4.7d.
- Cohort: **67 symbol-side keys**; buckets: `{'BROKER_RECENT_30D': 454, 'USER_PILOT_EXCEPTION_NOT_RECENT': 16}`.
- Handles: **470**; states: `{'DATA_QUARANTINED': 1, 'PENDING': 460, 'VECTOR_REJECTED': 9}`.
- Claims: **10** (priority 9, exploration 1; realized exploration 10.0%).
- Historical scheduler contract: deterministic 9:1 allocation and 35/35/30 frozen folds. Current repaired TRB handoff instead uses ranked key bands (top 10 per side 50–80%; remainder 20–60%).
- Exact-pending vector survivors: `[]`.

| key | bundle | lane | state | fold | return | benchmark | alpha | TIM | trades |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| LSCC_LONG | `EXPLORE_CHANNEL_REENTRY` | EXPLORE | DATA_QUARANTINED | — | — | — | — | — | — |
| NEM_LONG | `GR_DIRECT_RECLAIM_MTFATR` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | 23.226% | 35.165% | -11.940pp | 23.25% | 49 |
| SCCO_SHORT | `GR_WTDC_RECLAIM_PEAK` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | 171.500% | 4.420% | 167.081pp | 24.02% | 127 |
| ASTS_SHORT | `GR_DIRECT_RECLAIM_MTFATR` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | -391.840% | 0.000% | -391.840pp | 25.06% | 217 |
| CHRD_SHORT | `GR_WTDC_RECLAIM_PEAK` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | 54.820% | 32.340% | 22.480pp | 25.32% | 79 |
| NEM_SHORT | `WTDC_RECLAIM_PEAK_FAST` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | 19.787% | 0.000% | 19.787pp | 28.41% | 140 |
| BG_LONG | `WTDC_RECLAIM_PEAK_FAST` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | -151.557% | 0.000% | -151.557pp | 7.61% | 13 |
| RGLD_SHORT | `WTDC_RECLAIM_PEAK_FAST` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | 46.245% | 0.000% | 46.245pp | 21.75% | 87 |
| CRWV_SHORT | `SHORT_CORRECTION_ELEVATOR` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | -27.412% | 0.000% | -27.412pp | 38.16% | 208 |
| GOOGL_SHORT | `SHORT_CORRECTION_ELEVATOR` | PRIORITY | VECTOR_REJECTED | DISCOVERY_1 | -18.943% | 0.000% | -18.943pp | 41.97% | 216 |

## Top/bottom-10 entry/exit path fleet

> Claimable vector-first research queue. Control rows establish the frozen benchmark that later paths must beat; they are not exact-engine promotion evidence.

- Jobs: **80**; states: ADAPTER_REQUIRED=40, OBSERVABILITY_ONLY=2, QUARANTINED=8, SCREENED=30.
- Frozen tradeable hashes: LONG `e0acfe4c1139dd01f6138445c53071eb307fa4ad0600fcd8dddcd7e794a78955`; SHORT `00c085e4069a105826f040cf9ff95373afaa4684f9ebc02f1a72edff4a0aa361`.
- Top LONG cohort: SNDK, MRVL, ARM, MU, AMD, INTC, PBF, MPC, DINO, VLO.
- Bottom SHORT cohort: LAC, UUUU, UEC, ASTS, ACN, HL, TTD, EGO, CDE, ALB.

| path | key | stage | metric scope / units | state | strategy | B&H | multiple | alpha B&H | same-entry alpha | TIM | trades |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `ENTRY_WT_DC` | IBIT_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -1.697% | 8.890% | -0.191× | -10.587pp | 0.000pp | 2.62% | 12 |
| `ENTRY_DELTA_MTF` | ARM_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 14.638% | 15.974% | 0.916× | -1.335pp | 0.000pp | 2.78% | 13 |
| `ENTRY_DELTA_MTF` | MRVL_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_FINAL_REJECTED | 8.452% | 0.000% | —× | 8.452pp | 0.000pp | 1.30% | 5 |
| `ENTRY_DELTA_MTF` | SNDK_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -3.460% | 5.765% | -0.600× | -9.225pp | 0.000pp | 0.97% | 6 |
| `ENTRY_DELTA_MTF` | MU_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 10.510% | 0.000% | —× | 10.510pp | 0.000pp | 1.12% | 14 |
| `ENTRY_DELTA_MTF` | NVDA_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -0.772% | 0.000% | —× | -0.772pp | 0.000pp | 0.30% | 1 |
| `ENTRY_WT_DC` | IBIT_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -1.697% | 8.890% | -0.191× | -10.587pp | 0.000pp | 2.62% | 12 |
| `ENTRY_DELTA_MTF` | ARM_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 14.638% | 15.974% | 0.916× | -1.335pp | 0.000pp | 2.78% | 13 |
| `ENTRY_DELTA_MTF` | MRVL_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_FINAL_REJECTED | 8.452% | 0.000% | —× | 8.452pp | 0.000pp | 1.30% | 5 |
| `ENTRY_DELTA_MTF` | SNDK_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -3.460% | 5.765% | -0.600× | -9.225pp | 0.000pp | 0.97% | 6 |
| `ENTRY_DELTA_MTF` | MU_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 10.510% | 0.000% | —× | 10.510pp | 0.000pp | 1.12% | 14 |
| `ENTRY_DELTA_MTF` | NVDA_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -0.772% | 0.000% | —× | -0.772pp | 0.000pp | 0.30% | 1 |
| `ENTRY_STOCH_HHHL` | DINO_LONG | VEC_DINO_PHASE3_JOINT_STABILITY_DISCOVERY_ONLY | SUM_OF_TWO_DISCOVERY_VALIDATION_FOLDS; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=UNWEIGHTED_MEAN_OF_DISCOVERY_FOLD_WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | 249.630% | 27.520% | 9.071× | 222.110pp | -10.611pp | 76.14% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 846.161% | 177.045% | 4.779× | 669.116pp | -306.970pp | 71.52% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 846.071% | 177.045% | 4.779× | 669.026pp | -307.061pp | 71.47% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 1072.092% | 177.045% | 6.055× | 895.047pp | -81.039pp | 77.62% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 642.377% | 177.045% | 3.628× | 465.332pp | -510.755pp | 62.70% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 723.534% | 177.045% | 4.087× | 546.489pp | -429.598pp | 58.14% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 723.534% | 177.045% | 4.087× | 546.489pp | -429.598pp | 58.14% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 861.399% | 177.045% | 4.865× | 684.354pp | -295.238pp | 73.65% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 861.426% | 177.045% | 4.866× | 684.381pp | -295.211pp | 73.57% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 1098.579% | 177.045% | 6.205× | 921.534pp | -58.058pp | 80.52% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 692.519% | 177.045% | 3.912× | 515.474pp | -464.118pp | 66.86% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 762.524% | 177.045% | 4.307× | 585.479pp | -394.113pp | 63.75% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 762.524% | 177.045% | 4.307× | 585.479pp | -394.113pp | 63.75% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 858.519% | 177.045% | 4.849× | 681.474pp | -320.420pp | 72.04% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 858.307% | 177.045% | 4.848× | 681.263pp | -320.632pp | 71.99% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 1146.008% | 177.045% | 6.473× | 968.964pp | -32.931pp | 81.22% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 695.993% | 177.045% | 3.931× | 518.948pp | -482.946pp | 65.20% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 772.444% | 177.045% | 4.363× | 595.399pp | -406.495pp | 62.54% | 4 |

Metric guardrail: `VEC_NESTED_FOLD_AGGREGATE` returns are sums of outer-validation-fold capital-return percentages and are not a single holdout return. Only `FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD` rows use single-fold capital-return percentages; `LEGACY_UNSCOPED` rows are historical evidence and must not drive promotion.

SHORT vector controls are present but remain research-only until exact replay and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim gates pass. No LONG result is inverted or pooled.

## Campaign activity

| tier | campaign | rows | latest | age |
|---|---|---:|---|---:|
| ENGINE | stocks_repaired_20260730_c5 | 89 | 2026-08-02T01:13:01Z | 1.4d |

## Reading the matrix

- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.
- White: B&H comparison unavailable; measured evidence only, never promotion.
- Gray: every below-B&H or intentionally discarded result; retain so it is not blindly retested.
- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.
- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, not an optimized strategy.
