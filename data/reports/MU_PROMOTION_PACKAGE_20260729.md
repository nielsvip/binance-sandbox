# MU_LONG LIVE-PROMOTION PACKAGE — 2026-07-29 (for 13:30 UTC open)

## ✅ FINAL UPDATE 01:25 UTC — V3 SEALED-FOLD-TIM CONTRACT: FULL PASS (supersedes the fork below)

User chose **option 3** (split bands): sealed/live fold binds to TIM_BAND=(50,75);
discovery folds keep the sanity band DISCOVERY_TIM_BAND=(65,80). Encoded as
`CONTRACT = "MU_DAILY_DEEP_PARETO_HOLDOUT_V3_SEALED_FOLD_TIM"` with the
in-band-repair OR-clause. Result: **candidate 151 PASSES ALL GATES** —
verdict `PARETO_STABLE_BH_SURVIVOR_REQUIRES_EXACT_V3`, contract hash `9231680f…`,
receipt `data/reports/vec_research/MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260729_V3_SEALED.json`.
Holdout TIM 68.62% ∈ [50,75]; the 11.04% return sacrifice vs the (out-of-band,
77.09% TIM) source is accepted by the repair clause. `promotion_allowed` stays
hardcoded False by design — the operator promotes. User unlocked
tradier_manage.py; per-key live wiring (arrow gate, per-TF ladder, cap clip,
short mirror) is coded + compile-verified; live processes booted on it
2026-07-29 15:25 UTC. Remaining step: write the 5 per-key entries into
`data/hourly_reconfig/trb/active_config.json` (hot-reloads, no restart needed).

## ⚠️ UPDATE 01:10 UTC — tiered TIM band (50–75) recomputed; USER DECISION REQUIRED

Under the new tiered policy encoded strictly (band applied to EVERY fold),
candidate 151 is rejected at DISCOVERY: folds 1–2 ran at 77.39%/77.42% weighted
TIM — above the 75 ceiling — so the sealed fold never opens
(`MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260729_V2_TIERED.json`,
contract `V2_TIERED_TIM`, hash `9e66e045…`). The source config is equally out of
band (77.09% on the sealed fold). **Strictly applied, NOTHING MU is promotable today.**

The fork (three band changes have already been made this week — a fourth must be
your call, not an agent's):
- **Option A — band binds every fold** (current encoding): MU stays gray. No
  promotion at 13:30.
- **Option B — band binds the sealed/live fold only** (discovery keeps the prior
  65–80 sanity band): 151's holdout TIM 68.62% is in-band, the in-band-repair
  clause covers the 11% return sacrifice vs the now-out-of-band source, and per
  the TIM65 run's numbers 151 would very likely be promotion-eligible. Requires
  one amendment + a ~3-min rerun once you choose it.

Say "option A" or "option B" (and, for any promotion, "unlock tradier_manage.py").
Everything below reflects the pre-tiered-band analysis and stands as reference.

Prepared by orchestrator session. All numbers recomputed this session from sealed
artifacts — nothing hand-edited, nothing carried over from voided lanes.

## The decision

Two fully-evidenced configs exist for MU_LONG. Both passed every absolute gate and
full exact-replay provenance (33/33 actions, 0 refusals, 0 future-HTF, 0 clamps,
bit-identical signal parity). They differ exactly along the exposure↔gain trade-off:

| | **source control** (BLOCK153 center_plateau, union trigger) | **candidate 151** (DAILY_DEEP center_plateau TARGET_CAP8 close_confirm) |
|---|---:|---:|
| sealed-fold return | **+1316.0214%** | +1170.7005% |
| alpha vs B&H (+205.2519%) | **+1110.77pp** | +965.45pp |
| B&H multiple | 6.41× | 5.70× |
| weighted TIM | 77.09% (above your 50–70 target) | **68.62% (inside your 50–70 target)** |
| max drawdown | 37.62% | **35.85%** |
| minimum equity | **$8,210.77** | $7,912.35 |
| avg_deployed_usd | $12,334.67 | $10,978.45 |
| implied_leverage_x (vs $10k start) | 1.2335× | 1.0978× |
| peak_post_fill_notional_usd | $16,000 (8× cap) | $16,000 (8× cap) |
| exact-replay provenance | PASS (7/7) | PASS |
| Pareto-contract verdict | incumbent — survived | GRAY: `return_sacrifice_compensated` fails |

Receipts: `data/reports/vec_research/MU_SOURCE_EXACT_REPLAY_RECEIPT_20260729.json`,
`data/reports/vec_research/MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260729_TIM65.json`
(+ full artifact dirs on S1, SHA-verified inputs identical to the 2026-07-27 sealed run).

### Why candidate 151 is still gray after the TIM 65 change
The TIM gate now passes (68.62 ∈ [65,80]). The sole remaining failure,
`return_sacrifice_compensated`, is structural: 151 returns 11.04% less than the
source, and the contract only accepts a return sacrifice when it repairs an
out-of-band TIM — but the source's TIM (77.09%) was already in-band, so the required
≥10pp gap improvement is unreachable by construction. The contract did not reject
MU; it rejected replacing the incumbent with a lower-return challenger.

### Routes to live (pick one)
1. **Promote the source config** — no contract change needed; highest return; but
   TIM 77.09% exceeds the stated 50–70% exposure target.
2. **Promote candidate 151** — fits the exposure target; requires a second contract
   amendment (recognize in-band exposure reduction toward the 50–70 target as valid
   compensation). That is a policy decision only the user can make.

## What promotion requires regardless of route

1. **`unlock tradier_manage.py`** — the live wiring cannot be touched without it.
2. **Known parity break (must be acknowledged before live):** live
   (`tradier_manage.py:3457-3477`) triggers on a 5m rebound off a running low +
   MTF-arrow score, sizing on ONE timeframe; the backtest triggers on completed-HTF
   structure with three independent TF ladders. These are different strategies at
   the trigger level.
3. **Evidence-class caveat (defect E):** both configs' "exact replay" is Tier-1
   self-consistency via the research adapter. It does NOT call
   `backtest_v8_engine.py` or the live decision path. It is not proof of live parity.
4. Contract name flag: the TIM-65 receipt still carries the literal
   `MU_DAILY_DEEP_PARETO_HOLDOUT_V1` name; only the contract hash (`3ceafe88…`)
   proves the terms changed. Rename to V2 before citing.

## Honest-numbers block (reporting rules §6)
- Rankings above use alpha in percentage points; |B&H| = 205.25pp ≥ 20pp so the
  multiples are quotable.
- Leverage: both configs peak at $16,000 notional on a $10,000 ledger (1.6× peak);
  average deployed capital and implied leverage are published in the table. The
  denominators are STARTING equity — against compounding equity the effective
  leverage falls over the run. The earlier "1.19×" memory figure used an
  unconfirmed method; the table's numbers come from the tool's own formula.
- These are single-symbol (n_syms=1) research results: `[DIAGNOSTIC ONLY · n_syms=1]`
  under the sample-floor rule. Promotion of a single-key override is a user decision
  explicitly outside auto-promotion paths; nothing here was written to any matrix,
  override, or live-loader path.
