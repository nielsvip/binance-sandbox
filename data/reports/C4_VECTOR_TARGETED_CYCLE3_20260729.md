# c4 Vector Targeted Cycle 3 — preregistered MU/NVDA/LAC catalog

Date: 2026-07-29 UTC

Status: local catalog and validation only. No S1, exact-engine, matrix DB, or
live-configuration writes were made by this work.

## Why a third cycle is needed

The completed safe and adaptive cycles show that continuing to move only the
exit thresholds cannot satisfy both the return and exposure objectives:

| key | strongest performance arm | best observed 65–80% TIM arm | implication |
|---|---|---|---|
| MU_LONG | `C4_WTDC55_PPL_PATIENT`: +7.424%, control -8.620%, 41.94% TIM | none; adaptive maximum was about 54.15% TIM and the slower locks lost money | the entry/control population itself has an exposure ceiling below 65%; add a causal entry/replenishment path while retaining the profitable patient lock |
| NVDA_LONG | `C4_WTDC60_R3_D4H`: +9.968% vs +4.977% B&H (2.003x), control +6.034%, 23.68% TIM | none; daily-only/lower-threshold adaptive R3 reached 30.23% TIM but only 1.621x B&H | preserve R3 and fill post-exit gaps; lowering the WT/DC threshold alone has plateaued |
| LAC_SHORT | `C4_WTDC60_R3_D`: +28.171% vs +11.084% B&H (2.542x), control +16.925%, 43.27% TIM | `C4_WTDC35_PPL_PATIENT`: +15.976% (1.441x), control +15.922%, 72.57% TIM | replenish the high-alpha R3 arm; separately test whether the already-in-band patient arm gains enough from the denser entry population |

The second-cycle LAC slow-lock arms demonstrate why simply waiting longer is
not a solution: they stayed around 65–73% TIM but failed both 2x B&H and, in
most cases, their same-entry control. MU showed the same loss of exit alpha as
the partial-lock trigger moved from 2.5% toward 3.5–7%.

## Preregistered intervention

Cycle 3 adds only the existing `WT_3M_FORCE_OPEN` family to the promising
exit identities. This is a with-trend entry/add path based on the appropriate
side of the 200-period anchor and favorable short-timeframe WaveTrend. Two
forms are paired:

- `GATED`: `WT_3M_FORCE_OPEN_ENABLED=True`,
  `WT_3M_FORCE_OPEN_BYPASS_GATES=False`.
- `BYPASS`: the same entry source with its registered bypass enabled. This is
  a deliberately bounded diagnostic arm; exact replay still has to prove the
  action fingerprint, capacity, and risk gates.

Every same-entry vector control retains the WT/DC threshold and both
force-open fields while switching off the candidate exit. Therefore a result
cannot pass merely because the extra entry path makes more money; the chosen
exit still has to add alpha over that denser control.

The catalog contains 38 unique arms:

- MU_LONG: 12 partial-lock arms. Thresholds 35/55, gated/bypass, and a narrow
  interpolation from the profitable 2.5/3.0/0.5 lock through
  2.75/3.25/0.4375 and 3.0/3.5/0.375. The destructive 5–7% region is not
  repeated.
- NVDA_LONG: 10 R3 arms. Daily and D+4h variants around the proven threshold
  60 arm and the adaptive daily/lower-threshold arm, each gated/bypass.
- LAC_SHORT: 8 daily-R3 replenishment arms across the observed 15–60
  threshold plateau, plus 8 patient/midpoint partial-lock arms at thresholds
  35/55.

## Acceptance contract

The screen keeps the existing c4 criteria: three chronological folds, stock
round-trip cost 0.05%, strategy capacity $16,000, side-specific B&H using
$2,000, positive cash return when side B&H is negative, at least 2x positive
side B&H, 65–80% binary TIM, maximum drawdown no greater than 40%, and a
strict improvement over the same-entry/same-fold control.

Vector success is only an exact-replay candidate. It does not fill the ENGINE
matrix and does not authorize live settings. Exact replay must additionally
prove route attribution, at least three real closes, side isolation, no
unopposed reentry overshoot, legal capacity behavior, and the 0.05% stock cost.

## Artifacts

- Catalog/screen: `tools/c4_vector_targeted_cycle3.py`
- Contract tests: `tools/test_c4_vector_targeted_cycle3.py`
- Default output (when intentionally run on a host with c4 NPZs):
  `data/reports/c4_vector_bundle_screen_targeted_cycle3/`

The existing safe/adaptive runner and their receipt trees are not modified, so
their runner SHA and completed evidence remain stable.
