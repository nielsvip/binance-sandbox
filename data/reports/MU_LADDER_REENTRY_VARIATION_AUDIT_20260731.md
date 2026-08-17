# MU ladder re-entry variation audit

Sources: the exact ladder-parity replay and the frozen walk-forward/retune
receipts under `data/reports/vec_research/`. Results below are reproduced from
those artifacts; no new values are inferred.

## Exact engine replay

Replay: `v8_exact_ladder_replay_20260726T145112Z_MU_LONG`.

| ladder | trigger | source TFs | fills / exits / lower re-entries | gain | B&H | delta | B&H multiple | DD | weighted TIM | clamps | status |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BLOCK153 | center-plateau target, close-confirm | D / 4h / 1h | 23 / 10 / 10 | 1316.021% | 205.252% | +1110.769 pp | 6.412x | 37.621% | 77.092% | 0 | exact parity PASS; promotion OFF |

Exact audit: 34 scheduled/executed actions, 11 closed lifecycles, one-bar
signal-to-fill latency, zero future HTF sources, capacity ceiling $16,000,
zero clamps, and accounting parity PASS. The replay used the ladder lower-first
re-entry plus stored exit/top reclaim. It tested D/4h/1h; no 15m ladder leg was
present in this exact schedule.

## Three-fold frozen walk-forward selections

The broader campaign evaluated 244 candidate curves. These are the selected
outer-fold variants and their untouched validation results:

| fold | selected curve | trigger / semantics | gain | B&H | delta | B&H multiple | DD | exits / lower re-entries | fill ratio | clamps | verdict |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | ARC5 | green / linear target | 226.673% | 44.088% | +182.585 pp | 5.141x | 12.368% | 7 / 7 | 1.000 | 0 | pass for this fold |
| 2 | BLOCK101 | union / linear add | 909.147% | 132.957% | +776.190 pp | 6.838x | 25.757% | 6 / 6 | 0.231 | 150 | capacity/fill failure |
| 3 | BLOCK153 | union / center-plateau target | 1316.021% | 205.252% | +1110.769 pp | 6.412x | 37.621% | 10 / 10 | 1.000 | 0 | pass for this fold |

## Exposure retune selections

This retune tested ladder curves against the same frozen E02 control. It is
not exact promotion evidence because the aggregate failed the non-degradation
and capacity rules.

| fold | selected curve | trigger / semantics | gain | B&H | delta | B&H multiple | DD | fill ratio | clamps | verdict |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | LOCAL_F1_lower_heavy_S1.15 | green / linear add | 563.388% | 44.088% | +519.300 pp | 12.779x | 38.506% | 0.345 | 76 | failed capacity |
| 2 | LOCAL_F2_same_S0.7 | structure / linear target | 161.154% | 132.957% | +28.197 pp | 1.212x | 7.445% | 1.000 | 0 | valid fold, weak edge |
| 3 | ARC1 | union / linear target | 1337.779% | 204.905% | +1132.874 pp | 6.529x | 32.324% | 1.000 | 0 | valid fold, high DD |

Aggregate retune: 2,062.320% capital return versus 381.950% B&H, 5.399x
B&H multiple, 38.506% maximum DD, 76 clamps, 54.188% fill ratio, and
−440.761 percentage points versus the same frozen control. `vector_survivor`
and `exposure_target_pass` were false; promotion is prohibited.

## Stability grid

The stability fleet evaluated 320 registered ladder candidates across folds.
Strict discovery survivors: **0**. The leading BLOCK153-like candidate passed
fold 1 but failed fold 2 because it was not above the source E02 ladder control
(fold 2: +583.249% capital return, +450.292 pp versus B&H, 18.092% DD, but
−325.898 pp versus source control). Therefore the 10x-looking rows are
high-return fold/sample results, not a stable winning path.

## Interpretation

The ladder mechanism is intact in the exact replay and can generate large
sample-period B&H multiples, but the current evidence does **not** support
turning on a 10x ladder: fold instability, 37–39% drawdown, and capacity/fill
failures are material. The exact schedule currently uses D/4h/1h; 15m has only
appeared in separate vector/approximate research and needs a dedicated exact
replay before it can be credited.

## DC-low break/rebound/rollover exit-variant registration (2026-08-01)

The research lane now registers the missing variants explicitly rather than
folding them into an unlabeled technical exit:

| registered family | SHORT rule | LONG mirror | confirmation clocks | current evidence |
|---|---|---|---|---|
| `ENTRY_REVERSAL_DC_BREAK_BOUNCE_ROLLOVER` | completed `dc_low_N` break → rebound → rollover | completed `dc_high_N` break → pullback → turn-up | parent 5m/15m/1h/4h/D; next 5m execution | vector campaign contract; no local result artifact |
| `EXIT_REVERSAL_HHHL` | completed higher-high **and** higher-low | completed lower-low **and** lower-high | selectable; default 5m | registered, exact adapter pending |
| `EXIT_REVERSAL_WT_CROSS` | opposing WT cross | opposing WT cross | selectable 5m/15m/1h/4h/D | registered, exact adapter pending |

`tools/mu_reversal_ladder_campaign.py` accepts
`--exit-timeframes 5m 15m 1h 4h D` for the full same/cross-clock sweep. The
default remains 5m for compatibility, so a run that omits this option must not
be described as having tested every exit clock. Combined `WT_OR_STRUCTURE`
and `WT_AND_STRUCTURE` rows remain diagnostics and cannot be reported as
isolated HHHL or WT-cross results. No matrix or live credit is claimed until a
valid NPZ, exact V8 adapter, 30 real closes, and applicable risk/activity gates
are present.
