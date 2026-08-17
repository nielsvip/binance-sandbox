# Entry/exit combination rundown — current verified evidence

Updated: 2026-08-02 UTC

This report is generated from current causal receipts. It does not use
`stocks_baseline_v2_s4h`, does not copy one combination result into many scalar
cells, and does not overwrite exact V8 evidence.

## Best complete lifecycle recipes

These results use the corrected strictly-later exit/re-entry engine. The recipe
contains ENTRY, AUGMENT/REDUCE, EXIT and mandatory stored-level REENTER paths;
the exact JSON and hashes are in the Lifecycle Combinations sheet and port
5077.

| key | gain/mo | B&H/mo | multiple | max DD | TIM | real closes | recipe |
|---|---:|---:|---:|---:|---:|---:|---|
| MU_LONG | 178.1961% | 20.3937% | 8.738x | 87.90% | 99.39% | 10 | `lcb-1a89b0f01e7e0c1caa03` |
| NVDA_LONG | 38.2744% | 4.2384% | 9.030x | 40.56% | 99.84% | 15 | `lcb-16ab6f6503314886634a` |
| VT_LONG | 13.9232% | 1.4901% | 9.344x | 19.90% | 99.74% | 7 | signed causal-v2 lifecycle receipt |
| TTD_SHORT | 30.2929% | 2.8390% | 10.670x | 70.23% | 63.44% | 148 | `lcb-62735c3111cdee4cc8a2` |
| ACN_SHORT | 19.9646% | 1.8392% | 10.855x | 46.99% | 98.78% | 98 | `lcb-01488e0e84e7d4a6e2f5` |
| LAC_SHORT | 20.6019% | 2.1067% | 9.779x | 40.02% | 37.69% | 95 | `lcb-0729e11d79456d2ab530` |

High return alone does not hide risk or activity. MU's drawdown is excessive,
and VT has only seven closes. Those remain visible warnings while their
receipts remain valid research evidence.

## Ladder multiplier x WT-exit grid

This is the requested numeric ladder comparison, not a default-setting claim.
The campaign tested 55 recipes per key, retained 344 behavior-unique outcomes,
quarantined 41 duplicate recipes, observed zero future-HTF reads and zero
capacity clamps, and charged 5 bps one-way stock slippage with zero commission.

All rows use a `$2,000` base unit, `$16,000` strategy capacity, D `10/6` unless
shown otherwise, mandatory zero-buffer reclaim of the stored exit/signal
extreme, and the disclosed EMA200 breakout-sizing companion layer.

| key | D lower/upper | 4h lower/upper | 1h lower/upper | WT exit TF | gain/mo | B&H/mo | multiple | DD | TIM | closes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MU_LONG | 10/6 | 1/1 | 4/1 | D | 79.3052% | 20.6074% | 3.848x | 61.57% | 95.82% | 44 |
| NVDA_LONG | 10/6 | 10/1 | 4/1 | D | 35.1971% | 3.8608% | 9.117x | 36.75% | 94.22% | 47 |
| VT_LONG | 10/6 | 6/4 | 8/1 | 4h | 7.4863% | 1.3839% | 5.410x | 34.81% | 87.36% | 92 |
| TTD_SHORT | 10/6 | 6/4 | 10/1 | 4h | 34.7917% | 2.8256% | 12.313x | 73.36% | 89.41% | 134 |
| ACN_SHORT | 10/6 | 6/4 | 8/1 | 4h | 9.2626% | 1.7832% | 5.194x | 46.40% | 89.16% | 126 |
| LAC_SHORT | 10/6 | 4/1 | 4/1 | 4h | 13.3360% | 1.9963% | 6.680x | 97.28% | 90.25% | 128 |
| MSTR_SHORT | 10/6 | 3/1 | 4/1 | D | 44.7990% | 7.6711% | 5.840x | 63.96% | 96.54% | 6 |

The complete 55-row-per-key tables, including losing settings and exact
recipes, are under
`data/reports/vector_ladder_wt_grid/canonical_v7_20260801_exact_recipe_v2/`.
LAC's ladder winner has a near-insolvency drawdown and MSTR has only six
closes; neither warning is suppressed by its B&H multiple.

## What the successful paths currently say

1. The ordinary multi-timeframe ladder is useful as an exposure target, not as
   a standalone buy-and-hold surrogate.
2. A causal WT opposite-cross exit plus mandatory stored-level reclaim is the
   most repeatable tested exit/re-entry pair. Daily WT exits won for MU, NVDA
   and the short-history MSTR sample; 4h WT exits won for VT, TTD, ACN and LAC.
3. The multiplier is symbol- and timeframe-dependent. The same D `10/6`
   anchor survived this grid, while the useful 4h/1h lower-band targets ranged
   from `1` through `10`; one global guessed ladder is not supported.
4. Faster exits improve activity but can create destructive churn. A lower
   Donchian break remains primarily an entry-quality/recovery flag; it is not
   treated here as a blind sell-at-the-bottom instruction.
5. Every exit remains coupled to a later re-entry obligation. No result is
   accepted when price crosses the stored permitted level while flat.

## Scalar matrix status

The current strict union has 14 key-scoped ledgers and 1,889 raw scalar rows:
89 behavior-unique cells pass, 1,800 duplicate/inert cells are quarantined,
and 97 collision groups are queued for causal adapter repair or exact V8.
Only one two-cell saturation plateau is allowed within a five-or-more-value
axis, and only when all other values are unique. The current union contains no
such accepted plateau.

Combination results live in the Lifecycle Combinations surface and may be
used as provisional vector candidates under the Bible. Scalar cells remain
separate OFAT wiring/sensitivity evidence. Existing receipt-valid V8 cells
always have precedence over both.

## Next ten active longs

The first post-pilot long cohort is complete. Nine of ten causal lifecycle
champions beat sided B&H above 2%/month; `QRVO_LONG` remains explicitly
unresolved rather than being promoted with a fabricated or repeated value.

| key | gain/mo | B&H/mo | multiple | DD | TIM | closes |
|---|---:|---:|---:|---:|---:|---:|
| CIBR_LONG | 79.3192% | 9.4023% | 8.436x | 18.66% | 99.38% | 2 |
| PSX_LONG | 16.2579% | 1.0761% | 15.108x | 42.54% | 98.64% | 423 |
| USAR_LONG | 145.4748% | 1.8218% | 79.850x | 35.02% | 95.75% | 104 |
| EOG_LONG | 6.6061% | 0.5682% | 11.627x | 37.65% | 99.99% | 42 |
| QRVO_LONG | -29.5290% | -1.0506% | unresolved | 35.83% | 70.06% | 238 |
| GOOGL_LONG | 34.8942% | 4.7000% | 7.424x | 44.20% | 99.97% | 4 |
| A_LONG | 44.5197% | 5.3397% | 8.337x | 18.32% | 98.83% | 39 |
| BG_LONG | 2.7370% | 0.1222% | 22.397x | 61.35% | 98.46% | 510 |
| DAR_LONG | 15.4567% | 1.1268% | 13.718x | 85.68% | 96.21% | 220 |
| TRGP_LONG | 38.4681% | 5.0416% | 7.630x | 27.36% | 100.00% | 1 |

For activity/TIM, the preserved alternatives are `USAR_LONG` at
93.1192%/month, 59.63% TIM and 1,381 closes, and `TRGP_LONG` at
22.2748%/month, 63.56% TIM and 149 closes. Full path recipes and NPZ hashes
are in `COHORT3_VECTOR_LIFECYCLE_20260802.md`.

## Evidence boundaries

- Causal V7 NPZs only; higher-timeframe source timestamps may never exceed the
  observation timestamp.
- Historical 5m gaps may use disclosed 15m bridging; they are never relabeled
  as authentic 5m bars.
- Stock costs are 5 bps one way and zero commission. Crypto's separate cost
  contract is not used in this report.
- B&H uses `$2,000`; the strategy may use up to `$16,000`. Both raw account
  return and deployed-capital-normalized return stay in the raw receipts.
- Vector lifecycle evidence is labeled as such. Exact V8 replay supersedes it
  but may never be overwritten by it.
