# Results ledger — last 6 months, HONEST verdicts  (36921 rows, 3594 files)

**The point: how much of 6 months of results is actually trustworthy.**

- rows tagged `PROMOTE` by the producers: **28571**
- of those, honest verdict != promotable (sub-floor / below-edge imposters): **28571**
- rows with n_syms < sample floor (CANNOT promote, ever): **36911** (100.0%)
- rows that are honestly promotable candidates (n_syms>=floor, pool_sharpe>=1.0, trades>=30, still need Tier-2 reconfirm): **0**

## Honest verdict distribution
- SUB_FLOOR: 36911
- BELOW_EDGE:Noise: 6
- MALFORMED: 2
- BELOW_EDGE:Directional: 1
- BELOW_EDGE:Best-of-current: 1

## The ONLY rows above the sample floor: **NONE.**
Not a single result in 6 months met the publishable sample floor. Every 'PROMOTE' was sub-floor.
