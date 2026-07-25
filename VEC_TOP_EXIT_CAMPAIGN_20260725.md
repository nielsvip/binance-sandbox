# Vector top-exit and stateful re-entry campaign

**Date:** 2026-07-25  
**Lane:** isolated `VEC_RESEARCH`; no ENGINE matrix or live configuration writes  
**Implementation:** `tools/vec_top_exit_campaign.py` + compiled state scan in `tools/vec_top_exit_scan.c`

## Outcome

The campaign produced a promising full-history MU lead, but it did **not** produce a strategy ready for promotion.

On the complete corrected MU dataset, the best candidate that obeyed both mandatory reclaim and the 70–80% time-in-market target was:

```text
Exit:       E02 4-hour Donchian opposite-channel close, N=30
Lower buy:  E11 after a 2.0 ATR favorable gap and completed 1-hour HH+HL bar
Fallback:   E10 mandatory reclaim, zero ATR buffer
Side:       LONG
```

It returned `+1375.27%` net versus `+665.72%` net side-aware B&H, or `2.066x B&H`, at `75.05%` RTH time in market.

However, the non-overlapping walk-forward check rejected the stronger interpretation:

- The policy winner selected in the discovery window was N=20, not N=30.
- Frozen N=20 returned only `0.373x B&H` in the later validation window.
- Full-sample N=30/G2 returned `1.033x B&H` in validation, but its TIM rose to `84.95%`, outside the requested band.

Therefore the full-history `2.066x` number is a research lead and an exact-engine parity target, **not** evidence of a deployable edge.

## Data contracts

| Key | Contract | Campaign status | Important warning |
|---|---:|---|---|
| `MU_LONG` | PASS | Full campaign + walk-forward | Corrected HTF availability, but `85.67%` of RTH 5-minute rows carry synthetic provenance |
| `VT_LONG` | PASS | Warned diagnostic | History contains a 69.5-day gap |
| `HAO_SHORT` | FAIL | Quarantined; no strategy run | HTF availability fields alias base timestamps, daily stochastic is incomplete, and one bar jumps 65.2% |

No HAO number was manufactured from the invalid file.

## Causal and accounting contract

- All signals use completed bars.
- Higher-timeframe fields come only from their corrected availability timestamp.
- A signal observed at close `i` fills at RTH open `i+1`.
- Long and short accounting use side-aware one-unit capital returns; short return is not incorrectly calculated as inverse price return.
- Costs are 5 bps commission plus 2 bps slippage per side.
- Initial and final benchmark trades use the same cost/fill assumptions.
- Every flat exit stores the side-aware exit/top reclaim level.
- E11 may re-enter at a favorable price only after the configured ATR gap and a completed 1-hour structural continuation bar.
- E10 with zero buffer is mandatory if price invalidates the exit before E11.

The independent Python replay matched the compiled C scan exactly for the queued MU candidate:

```text
gain                  PASS
technical exits       PASS
round trips           PASS
lower reentries       PASS
reclaim reentries     PASS
RTH TIM               PASS
calendar TIM          PASS
next-bar latency      PASS
```

All 29 actionable exit/re-entry fills had exactly one-bar latency.

## Full-history MU results

Net side B&H: `+665.72%`.

| Family | Best policy-compliant configuration | Gain | B&H multiple | RTH TIM | Technical exits | Max DD |
|---|---|---:|---:|---:|---:|---:|
| E01 Chandelier | No candidate met both policies | — | — | — | — | — |
| E02 Donchian | 4h N30 + E11 G2 + E10 RB0 | `+1375.27%` | `2.066x` | `75.05%` | 15 | `31.21%` |
| E04 break/retest | 4h EMA34, B0, rebound 0.25 ATR, wait 12 + E11 G2 + E10 | `+961.72%` | `1.445x` | `73.08%` | 29 | `41.03%` |
| E05 divergence/retest | No candidate met both policies | — | — | — | — | — |

E02 detail:

```text
round trips                    15
lower-price reentries          11
mandatory reclaim reentries     3
mean saved reentry price       +4.55%
positive saved reentries       78.57%
mean flat missed move           8.39%
maximum flat missed move       27.80%
bars flat beyond reclaim            3
```

The three bars beyond reclaim are the three E10 signal bars. Each re-entry filled at the immediately following RTH open. The E10 fills were `-9.79%`, `-11.95%`, and `-17.96%` worse than the preceding exits; this is the explicit cost of the “never miss the resumed move” guarantee. E11’s favorable re-entries more than offset them in the full sample.

## Invalid high number retained only as a diagnostic

E02 N30 plus E11-only G1.5 returned `2.926x B&H`, but it had:

```text
mandatory E10 fallback      disabled
bars flat beyond reclaim       2,632
RTH TIM                       56.01%
```

It violates the user's mandatory reclaim rule and is explicitly `policy_compliant=false`. It remains in the research rows so it will not be unknowingly retested, but it is not queued or promotable.

## Walk-forward result

Windows are non-overlapping:

```text
Discovery:  2024-01-01 <= timestamp < 2025-07-01
Validation: 2025-07-01 <= timestamp
```

### Discovery-selected policy configuration

```text
E02 4h N20 + E11 G2 + E10 RB0
Discovery gain       +70.11%
Discovery B&H         +2.18%
Discovery TIM         76.01%

Frozen validation gain       +242.98%
Validation B&H               +651.32%
Validation multiple            0.373x
Validation TIM                 67.75%
```

This is a clear walk-forward failure.

### Full-sample N30/G2 configuration across the same slices

```text
Discovery gain       +91.47%
Discovery B&H         +2.18%
Discovery TIM         66.67%

Validation gain      +672.49%
Validation B&H       +651.32%
Validation multiple    1.033x
Validation TIM         84.95%
```

It narrowly beats B&H in the later rally but does not maintain the TIM contract in either slice. The full-history `2.066x` is therefore driven by regime interaction and compounding across the boundary, not stable per-window dominance.

The dataset also changes provenance materially across the split: the discovery slice reports 100% synthetic RTH rows, versus 68.74% in validation. This makes exact replay on less synthetic source data especially important.

## VT warned diagnostic

Net VT side B&H: `+32.66%`.

| Family | Best policy-compliant gain | B&H multiple | RTH TIM | Conclusion |
|---|---:|---:|---:|---|
| E01 | `-6.61%` | `-0.202x` | `72.24%` | Reject |
| E02 | `+28.49%` | `0.872x` | `73.54%` | Below B&H |
| E04 | `+30.36%` | `0.930x` | `78.99%` | Below B&H |
| E05 | No policy-compliant candidate | — | — | No result |

The best unconstrained VT result was `1.500x B&H`, but it disabled mandatory reclaim and spent 10,025 bars beyond reclaim. It is invalid under the user's policy.

## S1 artifacts

Final full MU artifact:

```text
/home/niels/binance-sandbox/data/reports/vec_research/
  top_exit_20260725T182601Z_MU_LONG/
```

It contains:

- all 3,664 candidate rows;
- top-100 CSV;
- data-contract report;
- run manifest and NPZ hash;
- compiled-versus-Python accounting parity;
- exact signal/fill event log;
- faithful-engine replay queue;
- immutable Python, C, and data-contract source snapshots.

Important hashes:

```text
MU NPZ
f57ce885f72655026e594ce93fe883f61c20eab647568ee91ee5d7a4e9973fb3

C scanner
194657c76b7ad72d499b87425173ed63eeae0dce6283fb34eb4b49849de6a54d
```

Other artifacts:

```text
VT warned:
/home/niels/binance-sandbox/data/reports/vec_research/
  top_exit_20260725T180924Z_VT_LONG/

HAO quarantine:
/home/niels/binance-sandbox/data/reports/vec_research/
  top_exit_20260725T180959Z_HAO_SHORT_QUARANTINE/

MU discovery:
/home/niels/binance-sandbox/data/reports/vec_research/
  top_exit_20260725T182642Z_MU_LONG/

MU validation:
/home/niels/binance-sandbox/data/reports/vec_research/
  top_exit_20260725T182645Z_MU_LONG/
```

## Faithful-engine replay queue

The full-history N30/G2/E10 candidate is queued for exact-engine reproduction only:

```text
status: PENDING_EXACT_ENGINE_RESEARCH_ADAPTER
promotion_allowed: false
```

The exact engine does not yet expose these isolated research algorithms as switches. A valid replay must:

1. seed one full long unit at the first RTH open;
2. disable competing entries/exits;
3. implement the same completed-4h Donchian N30 event;
4. implement E11 2 ATR plus completed-1h HH+HL;
5. implement zero-buffer E10 mandatory reclaim;
6. reproduce the supplied event timestamps;
7. match gain within 1 basis point and TIM within 0.05 percentage point;
8. remain research-only until walk-forward robustness improves.

## Next research action

Do not widen N30/G2 around the full sample. That would deepen overfitting.

The next useful campaign is a rolling walk-forward/state-adaptive test:

- choose Donchian horizon and E11 gap only from prior data;
- require a minimum number of completed exit/re-entry episodes;
- evaluate multiple rolling validation blocks rather than one rally-dominated block;
- test whether regime features can select N20 versus N30 causally;
- repeat on corrected, low-synthetic symbols before exact-engine integration.

Until then, the correct status is: **mechanically promising, accounting-verified, policy-compliant on the full sample, but not robust out of sample.**

## 2026-07-25 faithful-cost correction and route audit

The earlier artifacts above charged 5 bps independently at each entry and exit.
`backtest_v8_engine.py` instead charges one 10 bps round-trip amount against
entry notional when the position closes. Those methods are close for small
returns but are not identical under compounding. The first fail-closed route
replays exposed the difference:

```text
Discovery N20/G2: engine - vector = +7.116 bp   FAIL
Full N30/G2:      engine - vector = +137.940 bp FAIL
```

The tolerance was not widened. The C scanner, independent Python reference,
and B&H calculation now use the faithful-engine convention. The cost-aligned
full-period frozen N30/G2/E10 artifact is:

```text
/home/niels/binance-sandbox/data/reports/vec_research/
  top_exit_20260725T191201Z_MU_LONG/

strategy gain       +1377.872399%
B&H net              +666.389299%
multiple                 2.067669x
RTH TIM                 75.050635%
```

The cost-aligned frozen walk-forward verdict remains a failure:

```text
N20 discovery      +70.163936% vs +2.181112% B&H, TIM 76.0064%
N20 validation    +243.263911% vs +651.968541% B&H = 0.373122x, TIM 67.7513%

N30 discovery      +91.550098% vs +2.181112% B&H, TIM 66.6680%
N30 validation    +673.491872% vs +651.968541% B&H = 1.033013x, TIM 84.9515%
```

Machine-readable truth is stored at:

```text
data/reports/vec_research/top_exit_walk_forward_summary_MU_LONG.json
```

The faithful-engine adapter is explicitly an execution-route smoke. It checks
the actual loaded NPZ hash, immutable schedule hash, actual loaded bar
open/close, slippage-derived fill, emitted engine fill/quantity, exact
signal-to-next-RTH index, position lifecycle, fees, P&L, and TIM. It sizes each
new round from acknowledged realized equity rather than frozen vector equity.
It deliberately records `signal_parity=false`, because replaying a frozen event
schedule does not independently recompute E02/E10/E11 signals. Therefore even
a passing route smoke cannot promote a matrix cell or a live switch.
