# inf Loss-Pattern Analysis (2026-04-20 → 2026-04-26)

Read-only forensic analysis of `data/decisions/decisions_inf_2026042[0-6].jsonl`
(5,853 events) and `~/logs/ez_manage_inf.log*` (NOLOSS-block events, current-day only).

## TL;DR — Hypothesis confirmed, but the bleeder is HEDGES, not V3

| Metric | Value |
|---|---|
| Non-hedge close-pair gains (n=357) | sum **+147.15%**, mean **+0.412%/trade**, 28% close at loss |
| **Hedge close-pair gains (n=3,398)** | **sum −405.62%**, mean **−0.119%/trade**, **56.5% close at loss** |
| Hedge sum-of-losses | **−1,025%** (sum-of-wins +619%) |
| NOLOSS-blocked positions recovering to gain≥0% within 1h | **0.9%** (3 / 334) |
| Median NOLOSS-block "current loss" | **−1.65%** (mean −3.60%, max −24.56%, n=1,702 in last ~24h) |
| V3 closes that would have been more profitable held +5min | **75.5%** (40/53), median Δ +0.075% |
| V3 closes that would have been more profitable held +15min | **84.2%** (16/19), median Δ +0.118% |
| V3 closes followed by same-direction reentry (any latency) | **0 / 53** |

The user's "loss/loss" framing is correct: closing too early **and** failing to exit
losers are both happening, but they're not the same dollar bucket.

## Q1 — V3 closes are firing too early

53 V3 closes in 7 days, all `ATR_TP` or `PEAK_GIVEBACK` exits at gains 0.18–0.61%.

| Hold longer by | n with future data | would-have-been-more-profitable | median Δ vs exit price |
|---|---|---|---|
| +1 min | 53 | 23 (43.4%) | +0.000% |
| +5 min | 53 | **40 (75.5%)** | **+0.075%** |
| +15 min | 19 | **16 (84.2%)** | **+0.118%** |

V3 takes ~0.2% scalp profits on average. Ride to +0.3% (median 5-min continuation):
~75% of trades leave 0.07–0.12% on the table per close. With 53 closes/week that's
~6% of weekly P&L given up by V3 alone — small in dollars but signals the ATR_TP
target is mis-tuned to the actual continuation distribution.

## Q2 — NOLOSS-hold recovery is statistically near-zero

1,702 `UNIVERSAL_NOLOSS_GATE` blocks in the available log window (current-day rotations
only — the older `.1`–`.12` files are also same-day). After matching block-time prices
to JSONL price marks for entry-implication:

| Window | n | recovered ≥0% | median future-gain |
|---|---|---|---|
| +1 h | 334 | **3 (0.9%)** | **−1.811%** |
| +4 h | 3 | 0 | −1.902% |

Holding losers in NOLOSS purgatory is **not** "waiting for recovery" — it's
"watching the loss deepen". 99% of blocks do NOT recover to flat within 60 min;
median position is ~1.8% deeper red.

## Q3 — Reentry latency is effectively infinity

Of 53 V3 closes, **zero** were followed by a same-symbol same-side reentry from any
strategy. The reentry pipeline isn't firing after V3 exits.

## Q4 — Loss-then-missed-bounce: 50% of loss-closes were "early" within 5 min

100 non-hedge loss closes paired to opens. Asking "would holding instead of closing
have helped":

| Window | n | would-be-green | would-be-less-red |
|---|---|---|---|
| +5 min | 100 | 2 (2.0%) | **50 (50.0%)** |
| +15 min | 100 | 1 (1.0%) | **50 (50.0%)** |
| +60 min | 11 | 2 (18.2%) | **9 (81.8%)** |

50% of loss closes happen at a local mini-bottom — not enough to flip green, but the
position improves over the next 15 min in half the cases. Combined with V3 over-eager
exits, this confirms the "closed too early" half.

The "stuck loser" half is in the worst-trade tail:
- APEUSDT_SHORT: −46.92% × 2 (held 4.4 days, force-closed by HLR_TOP_EXIT)
- INXUSDT_SHORT: −15.04% / −14.94% (held 4.3 days, GAIN_EROSION_STOP)
- BBUSDT_SHORT: −10.86% (held 4.9 days, WT_4H_VEL_EXIT MANDATORY_REENTRY)

These five trades alone = **−134.7% gain-pct**, dwarfing the +147% won by all 257
other winners.

## Verdict — hypothesis SUPPORTED

Both ends bleed: V3 leaves continuation on the table, NOLOSS-held losers don't
recover. The dollar-weight is on the **hedge side**: 3,398 hedge closes net **−405%**
over 7 days, with 1,921 (56.5%) closing at a loss. Hedges are the systemic bleeder
that the user feels as "$14k options-style losses" on the spot side.

## Concrete config recommendations

1. **`SCALP_V3_ATR_TP_MULT` raise / replace with bar-confirmation exit.**
   75% of `ATR_TP` exits leave continuation. Replace `g >= ATR*N` with
   `g >= ATR*N AND (wt1_3m crosses wt2_3m against OR k_1m reverses 2 bars)` —
   AND-not-OR, so target alone doesn't trigger.
2. **`SCALP_V3_MAX_HOLD_MIN` raise to ≥15 min** with mandatory wt-flip exit at the cap.
   Current effective hold ≈ 1–3 min based on ATR firing.
3. **`UNIVERSAL_NOLOSS_GATE` add an absolute stop at ≤−5%.** Only 0.9% recovery rate
   means holding past −5% has zero EV. The −46% APE/-15% INX cases would have been
   capped at −5%. (Pair with `WRONG_SIDE_ABS_KILL` already-active rule.)
4. **NOLOSS technical-exit bypass**: when 5/5 WT TFs against, allow loss exit
   regardless of NOLOSS state — already on file as `NOLOSS_BYPASS_WT_5OF5_ENABLED`,
   currently `False`. Sweep this ON.
5. **Hedge gating tightening**: 56.5% hedge-close loss rate + only +0.19%/win mean
   on `HEDGE_CLEANUP_R6_main_recovered` cases means hedge selection is near-random.
   Validate the new `HEDGE_ZONE_GUARD` (DC + WT velocity at selection) added 2026-04-26
   and consider raising the open gain-floor for hedges (currently `gain<0` is enough).
6. **Reentry pipeline diagnosis**: 0/53 V3 closes followed by reentry suggests
   `PRICE_CROSSED_MANDATORY` or score-floor is blocking 3m reentries. Trace one
   logged V3 close end-to-end for "why didn't I reopen?" telemetry.
