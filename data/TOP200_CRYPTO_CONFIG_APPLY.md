# TOP200 CRYPTO CONFIG APPLY — 2026-04-15

Source: `data/TOP1000_CRYPTO_SWEEP_RESULTS.xlsx` (sheet `Top Crypto Configs`).
Total rows in file: 54. After filter `trades >= 500 AND sharpe >= 1.5`: **36 robust configs**.
All 36 robust rows come from the **2026-04-10 03:54 crypto_t13** sweep (Local, 4 symbols).
Later runs (04/10 18:48 and 19:18) contributed 18 rows — all failed the robust filter (thin trade counts or low sharpe).

## Chosen Robust #1

| Field | Value |
|-------|-------|
| Rank | 1 |
| Sharpe | 2.045 |
| PnL ($) | 22.08 |
| Trades | 1795 |
| Wins/Losses | 1043 / 752 |
| Win Rate | 58.1% |
| Run ID | `crypto_t13_00006` |
| CSV | `v8_sweep_crypto_t13_4sym_20260410_0354.csv` |
| Config fingerprint | `DELTA_EN=3 / DELTA_EN=1p5 / DELTA_EX=0p9 / DOM_TF=1 / MIN_TF_LOST=1 / DELTA_EX=3m / HT=none / TF=1p0 / NOLOSS=OFF / STRICT=[]` |

## Current config.py vs Robust #1 Diff

| Param | Current | Robust #1 | Action |
|-------|---------|-----------|--------|
| DELTA_ENTRY_MIN_TF | 3 | 3.0 | match |
| DELTA_ENTRY_Z_THRESHOLD | 2.5 | 1.5 | **DIFFER** — current comment: "Keeping 2.5 until larger cross-symbol sweep confirms reversal" |
| DELTA_EXIT_DECAY_RATIO | 0.90 | 0.9 | match |
| DELTA_EXIT_DOM_TF_ENABLED | True | True | match |
| DELTA_EXIT_MIN_TF_LOST | 1 | 1 | match |
| DELTA_EXIT_TF | `3m` | `3m` | match |
| DELTA_HTF_GATE | `4h_D` | `none` | **DIFFER** — current comment: "WINNER: both 4h AND D must confirm" |
| DELTA_TF_Z_THRESHOLD | 1.5 | 1.0 | **DIFFER** — current comment: "WINNER: tz=1.5" |
| NOLOSS_MIN_PROFIT_PCT | 0.0 | -999.0 | functionally equivalent (no-gate, see ez_manage read) |
| STRICT_NO_LOSS_ACCOUNTS | `['ang','inf','flz','men','fin']` | `[]` | **DIFFER** — re-enabled 2026-04-07 per config comment: "Removing this halved account value in 10 minutes" |
| STRUCTURAL_RANGE_SHIFT_EXIT | True | NaN | no change (not in row) |
| STRUCTURAL_RANGE_SHIFT_TF | `dc_4h` | NaN | no change (not in row) |
| WT_EXIT_VEL_THRESHOLD | -6.0 | NaN | no change (not in row) |

## Why NOT Auto-Applied

Every contested diff contradicts a **prior user-approved winner** from the **same 04/10 sweep data**.
Prior session chose DELTA_HTF_GATE="4h_D", DELTA_ENTRY_Z=2.5, DELTA_TF_Z=1.5 as winners (with explicit
comments). There is no newer data; the TOP1000 extract is the same sweep, and top-1 by raw sharpe
differs from what the user previously approved. CLAUDE.md forbids applying without user approval.

STRICT_NO_LOSS_ACCOUNTS=[] is live-money safety critical — re-enabled 2026-04-07 due to actual account
damage. Not applying unilaterally regardless of sweep ranking.

## Top 20 Robust Configs (summary)

| Rank | Sharpe | PnL | Trades | WR% | mtf | ez | decay | dom | min_lost | exit_tf | htf | tz | noloss | strict |
|------|--------|-----|--------|-----|-----|----|----|-----|----------|---------|-----|-----|--------|--------|
| 1 | 2.045 | 22.08 | 1795 | 58.1 | 3 | 1.5 | 0.9 | T | 1 | 3m | none | 1.0 | -999 | [] |
| 2 | 1.978 | 22.13 | 1796 | 57.9 | 2 | 2.5 | 0.9 | T | 1 | 3m | 4h | 1.0 | -999 | [] |
| 3 | 1.961 | 21.98 | 1795 | 57.9 | 2 | 2.5 | 0.9 | T | 1 | 3m | none | 1.0 | -999 | [] |
| 4 | 1.941 | 19.86 | 1794 | 58.0 | 3 | 2.5 | 0.9 | T | 1 | 3m | none | 1.5 | -999 | [] |
| 5 | 1.937 | 22.26 | 1794 | 57.9 | 3 | 2.5 | 0.9 | T | 1 | 3m | none | 1.0 | -999 | [] |
| 6 | 1.929 | 21.49 | 1795 | 57.7 | 3 | 2.5 | 0.9 | T | 1 | 3m | 4h_D | 1.5 | -999 | [] |
| 7 | 1.923 | 21.75 | 1795 | 57.5 | 3 | 1.5 | 0.9 | T | 1 | 3m | 4h | 1.0 | -999 | [] |
| 8 | 1.892 | 22.34 | 1794 | 58.0 | 2 | 2.0 | 0.9 | T | 1 | 3m | 4h | 1.5 | -999 | [] |
| 9 | 1.888 | 22.37 | 1794 | 57.6 | 2 | 2.0 | 0.9 | T | 1 | 3m | 4h | 1.0 | -999 | [] |
| 10 | 1.886 | 22.78 | 1795 | 57.7 | 3 | 2.0 | 0.9 | T | 1 | 3m | 4h | 1.0 | -999 | [] |
| 11 | 1.883 | 22.54 | 1795 | 57.9 | 2 | 2.0 | 0.9 | T | 1 | 3m | none | 1.5 | -999 | [] |
| 12 | 1.878 | 22.88 | 1796 | 57.9 | 2 | 2.0 | 0.9 | T | 1 | 3m | 4h_D | 1.0 | -999 | [] |
| 13 | 1.871 | 23.38 | 1795 | 58.3 | 3 | 2.5 | 0.9 | T | 1 | 3m | 4h_D | 1.0 | -999 | [] |
| 14 | 1.861 | 20.42 | 1795 | 57.2 | 2 | 2.5 | 0.9 | T | 1 | 3m | 4h_D | 1.0 | -999 | [] |
| 15 | 1.855 | 23.20 | 1795 | 57.9 | 3 | 2.0 | 0.9 | T | 1 | 3m | none | 1.0 | -999 | [] |
| 16 | 1.851 | 21.99 | 1793 | 57.5 | 2 | 2.0 | 0.9 | T | 1 | 3m | 4h_D | 1.5 | -999 | [] |
| 17 | 1.849 | 21.89 | 1793 | 57.8 | 2 | 1.5 | 0.9 | T | 1 | 3m | 4h_D | 1.0 | -999 | [] |
| 18 | 1.816 | 20.87 | 1795 | 57.8 | 3 | 2.0 | 0.9 | T | 1 | 3m | 4h_D | 1.5 | -999 | [] |
| 19 | 1.756 | 17.42 | 1795 | 56.7 | 2 | 2.0 | 0.9 | T | 1 | 3m | none | 1.0 | -999 | [] |
| 20 | 1.747 | 21.80 | 1795 | 57.9 | 3 | 1.5 | 0.9 | T | 1 | 3m | 4h | 1.0 | -999 | [] |

Stable knobs across all 36 robust: `decay=0.9`, `dom_tf=True`, `min_tf_lost=1`, `exit_tf=3m`,
`strict=[]`, `noloss=-999`. These are unanimous winners.
Non-stable knobs (winner depends on other settings): `mtf 2 or 3`, `ez 1.5–2.5`, `htf none/4h/4h_D`, `tz 1.0/1.5`.

## Recommendation

Current config.py matches the **stable winners** already (decay, dom_tf, min_tf_lost, exit_tf).
For the non-stable knobs, current values already represent a previously-approved winner row from
the same data. A **fresh cross-symbol sweep on V8-fixed engine** is needed to decide — running one
right now is blocked by existing S1/S2 sweep activity (see report).
