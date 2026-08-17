# Path-productivity redesign and first hotlist

## Outcome

The first bounded vector ENTRY×EXIT campaign finished all usable keys in under
12 minutes each (median 7m41s, maximum 11m22s), versus 4.5–10 minutes for one
exact scalar cell and hundreds of cells per key in the old campaign. The full
22-key cohort finished in 21m50s with eight workers.

The strict priority combinations are:

| Priority | Key | Entry | Exit | Strategy/mo | B&H/mo | Closes/week | TIM | Max DD | Clamps |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | CLF_SHORT | ENTRY_BOUNCE_5M_LOW | BOTTOM_A protective trail, 1h arm/DC/5m trail | 24.291% | -12.909% | 6.206 | 69.133% | 33.383% | 0 |
| 2 | ACN_SHORT | ENTRY_BB_RECOVERY | BOTTOM_A protective trail, 4h arm/immediate/5m trail | 17.698% | 1.663% | 1.142 | 59.160% | 13.458% | 0 |
| 3 | NVDA_LONG | ENTRY_LADDER_GREEN | BOTTOM_A protective trail, 1h arm/immediate/5m trail | 4.420% | 3.202% | 2.094 | 28.785% | 16.651% | 0 |

All three are eligible for exact complete-recipe adapter review and replay, but
were not dispatched. They are vector evidence, not live promotions.
`BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED` is the first displayed exit-family
hotlist item. Entry is not universal: the three winners use three different
entry families, which confirms that scalar global knob selection was the wrong
abstraction.

## What the failures say

Eighteen keys remain `UNRESOLVED_USE_BH`, so their executable baseline is B&H.
Among displayed champions, 14 fail weekly activity, 10 fail their TIM band, six
fail a causal/capacity/reclaim condition, and only one fails positive B&H delta.
The next search should therefore optimize event frequency and exposure jointly
inside the leading path families; it should not spend cycles finding still more
low-frequency high-return exits. TTD short illustrates this: 93.92%/month in
the validation slice versus 8.02%/month B&H, but only 0.305 closes/week.

FCN short is not a strategy loss. Its rebuilt NPZ contains zero RTH rows after
2024-01-01 and is a data-repair item.

## Why the exact matrix became slow

The March `tradier_manage.py` was 6,692 lines; current is 19,650. The central
`process_position` grew from 252 lines/87 calls to 2,164 lines/1,213 calls, and
`evaluate_stop` from 149 lines/66 calls to 1,355 lines/774 calls. The exact
campaign then starts a fresh Python process per scalar cell, reloads the NPZ,
rebuilds a full indicator dictionary and replays roughly 113k bars through the
entire live decision graph. At measured 4.5–10 minutes/cell, MU's 826 cells are
about 138 core-hours before interaction search. OFAT repeats that cost while
measuring a conditional effect that changes when any upstream path changes.

## Next execution order

1. Exact-replay the three full recipes above; do not exact-replay their scalar
   fields separately.
2. Refine BOTTOM_A only around its observed ranges: 5m trail; 1h/4h arm;
   immediate/DC modes; lookback 10–20; distance 1–2; ATR buffer 0–0.5.
3. Cross that exit range with the three entry seeds (5m bounce, BB recovery,
   ordinary ladder), then add Stoch HH/HL as the first alternate because one
   additional eligible top-five row used it.
4. For activity failures, shorten arm/confirmation clocks and introduce the
   existing causal reentry block only after the base lifecycle meets TIM. For
   TIM failures, tune exposure/sizing as a coherent block, not exit thresholds.
5. Repair FCN RTH source data and rerun only FCN.
6. Content-address source snapshots so immediate Mac sync transfers one copy of
   identical code instead of repeating it throughout a 1.1 GB tree.

Canonical artifacts:

- `data/reports/vec_research/path_hotlist_20260801/full_v2/PATH_PRIORITY_HOTLIST.md`
- `data/reports/vec_research/path_hotlist_20260801/full_v2/campaign_receipt.json`
- `data/reports/vec_research/path_hotlist_20260801/full_v2/mac_s1_publication_receipt.json`
