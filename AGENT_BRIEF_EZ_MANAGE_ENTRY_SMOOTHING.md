# AGENT BRIEF — smoothen (NOT lighten) ez_manage's conditional entry functions

**Written 2026-08-21. Read this whole file before editing anything.**

## The one-sentence job

`backtest_v12_engine` drives the real `ez_manage` entry functions, and they are
too slow to verify candidates at any useful rate. Make them **faster without
changing a single decision they make**.

## SMOOTHEN ≠ LIGHTEN — this is the whole brief

You are optimising **execution**, never **logic**.

| ALLOWED (smoothing) | FORBIDDEN (lightening) |
|---|---|
| hoist an invariant out of a loop | remove a condition |
| cache a value recomputed per bar | change a threshold or default |
| short-circuit ordering: cheap checks first | reorder in a way that changes the outcome |
| replace repeated `getattr(config, X)` with one lookup | drop a filter "because it rarely fires" |
| precompute an index instead of rescanning | merge two gates into one |
| avoid rebuilding the same dict each call | "simplify" a branch you think is redundant |

**A condition that fires on 0.1% of bars is not dead code — it is the 0.1% that
someone spent weeks getting right.** If you believe a branch is truly unreachable,
do not delete it: prove it with a counter over a full year on 20 symbols, report
it, and leave it in place.

## Targets

Entry path in `ez_manage.py`:

```
check_entry_alignment                 :320
check_entry_trigger                   :806
check_entry_vetting                   :913
evaluate_technical_indicator_signals  :36930
evaluate_leaderboard_entry            :35480
evaluate_reversal_entry               :36107
evaluate_ranking_momentum_trade       :35878
calculate_final_order_quantity        :37360
```

`ez_positions_quick` is on the same hot path and is fair game for the same
treatment — `process_single_exit`, `execute_trade_wrapper`,
`calculate_dynamic_quantity`.

## Method — do it in this order

**1. Measure first. Do not guess.**
```bash
python3 -c "
import cProfile,pstats,io,sys; sys.path.insert(0,'.')
# profile ONE symbol through backtest_v12_engine's live path
"
```
Profile a real run and rank by `tottime`. Optimise only what the profile names.

Precedent from 2026-08-21: profiling one evaluation showed **77,084 scalar
`np.searchsorted` calls costing 2.95 s of 7.45 s — 40% of every run** — from two
lines in `vec_paths/emergency_brake.py:420-421` doing a rolling-window count per
bar. Replacing them with `bisect` over a cached list and prefix sums was
**numerically identical (0 mismatches / 36 checks)** and removed the 40%. That is
the shape of a good fix: enormous win, zero behaviour change.

**2. Prove equivalence on EVERY change.**
```
BEFORE any edit:  capture the full trade ledger for >= 20 symbols, both sides
AFTER  each edit: ledgers must be BYTE-IDENTICAL — same trades, same timestamps,
                  same reasons, same quantities
```
`tools/opt/parity_trace.py` aligns two ledgers and reports the first divergence.
Not "same P&L" — same *trades*. Two different strategies can produce the same
P&L by accident.

**3. One change at a time, each committed separately.** A batch of five
optimisations where the ledger changed tells you nothing about which one did it.

## Hard rules

- **`ez_manage.py`, `ez_positions_quick.py`, `tradier_manage.py` are in
  `LOCKED_FILES.md`.** Do not edit without an explicit unlock in the same message.
- **Back up before every edit**: `cp <file> backups/before_<desc>_<YYYYMMDDHHMM>.py`
- **Never revert live code.** If something looks wrong, ask — do not roll back.
- Do not touch `execute_now()` guards. It is the single order gate; every caller
  depends on those checks applying uniformly.
- Banned families stay banned: `NOLOSS`, `NO_LOSS`, `HEDGE`, `STRICT_NO_LOSS`,
  `GHOST_CLOSE` (user mandate 2026-05-29).

## Four traps that have already bitten in this codebase

1. **The config copy.** `_apply_per_task_overrides` RETURNS a modified copy; it
   does not mutate in place. Discard the return value and every variant scores
   identically — this silently invalidated an entire optimisation campaign.
2. **Declaration lists are not reads.** A switch inside
   `_FULL_COVERAGE_PARAMS_EZ = [...]` is a string literal. ~39 switches were
   "confirmed wired" this way and were read by nobody. Grep for `.NAME` or
   `getattr(x, "NAME")`, never the bare name.
3. **Parent gates.** A child switch behind `PARENT_ENABLED=False` is never
   reached, so it profiles as dead while being perfectly wired. Probe with
   parents forced on.
4. **Generated stubs.** `if bool(getattr(cfg,"X",False)) if "X".endswith("_ENABLED") else ...`
   is a runtime string test on the switch's own name — always constant. There are
   1,981 such lines in `backtest_v12_engine`. They are inert; removing them with a
   line filter breaks indentation (they sit inside conditional blocks). Use an AST
   pass or leave them.

## Definition of done

- Profile shows a measurable wall-clock reduction on the live path
- Trade ledgers byte-identical across >= 20 symbols, both sides, full year
- Every edit has a backup and a separate commit
- A short report: what was slow, what changed, the measured before/after, and the
  parity proof for each change

## Context you will want

- `BACKTEST_BIBLE.md` — `HOW TO ADD A SWITCH` and the optimisation-method section
- `tools/opt/parity_trace.py` — ledger alignment, first-divergence report
- `tools/opt/probe_reads.py` — which switches are read AT RUNTIME
- `backtest_v12_engine.py` — the live-faithful harness (has a no-vectorisation
  guard; do not defeat it)
- `v12_quick_engine.py` — the vectorised engine. **Do not "smoothen" this one by
  copying logic from it into live, or vice versa.** They are deliberately
  independent so that agreement between them is evidence.
