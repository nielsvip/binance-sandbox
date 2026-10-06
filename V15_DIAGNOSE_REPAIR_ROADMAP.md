# V15 DIAGNOSE + REPAIR — the original job, its state, and the steps to put it in motion

> Owner: director session binance-e4 (2026-10-06). **Gate: nothing below goes live until the PARITY EMERGENCY (§0) is
> closed** — a repaired sheet is worthless if its trades do not happen in live.

---

## 0. EMERGENCY FIRST — live ↔ vectorized parity (blocks everything below)

**Why it blocks:** the census (ENCYCLOPEDIA.md §1, §4) and the first trade-level runs show the sheets and live trade
differently: vec-only paths (augments, MULTI_TF_EXIT, reentry machinery, exit chains live has ablated), live-only gates
(≈74 execute_now gates, NOLOSS holds, disaster guard…), and "wired" switches whose live read is a stub. Strict
aggregate parity already failed on CRWD/SMCI/ALGO (BIBLE §66.9). Any gain the repair phase finds on vec numbers is not
real money until the same trades happen live.

**Rule (USER 2026-10-06):** live is king. Vector "test" functions that were ever positive (pos_sym > 0 in
v15_avg_delta) are wired INTO live; every other vec default behaviour must equal live; live gates/exits get vec twins
("filtered correctly"). Sym_sides whose sheet trades do not replicate are blocked from live (`_NEG_BLOCK`).

| Lane | Owns | Job | Output |
|---|---|---|---|
| A harness + evidence | `backtest_v12_engine.py`, `tools/v15_trade_parity.py`, `tools/v15_parity_check.py` | fix live-leg crash (`'float' object has no attribute 'get'`), trade-level parity on a cross-venue sample, gap register | `data/reports/trade_parity/*`, `data/parity/gap_register_20261006.md` |
| B crypto live wiring | `ez_manage.py`, `ez_positions_quick.py`, `mtf_live_evaluator.py`, `config.py` | wire pos_sym>0 vec-only/dead switches live; expose fake live twins (FILTER_TF stub farms, ablation-inert paths) | `data/parity/lane_B_crypto_ledger.md`, `tests/test_parity_live_twin_*` |
| C stocks live wiring | `tradier_manage.py`, `tradier_*.py`, `config_tradier.py` | same for stocks | `data/parity/lane_C_stocks_ledger.md` |
| D vec baseline = live | `v12_quick_engine.py`, `vec_decisions/` | default-on vec behaviour live lacks → behind a switch defaulting to live; live gates → vec twins | `data/parity/lane_D_baseline_gap_list.md` |

Already in place (2026-10-06): `execution_ledger` from the scalar replay; `tools/v15_trade_parity.py` (round-trip
matching, gap register); `tools/v15_parity_check.py` fails PARITY when < 80 % of trips match both ways
(`V15_TRADE_PARITY_GATE=0` disables) → `v15_final_phase.qualify` → NEGATIVE → `golive_final` `_NEG_BLOCK`.

**Exit criteria for the emergency:**
1. Live leg of the harness runs for every venue/side (no harness crashes; artefacts like single-side portfolio gates,
   3m-vs-15m cadence and the execute_now stub documented and either removed or explicitly accounted for).
2. Gap register: every gap family with ≥ 2 sym_sides is closed (wired live, vec twin, or default aligned) or has a
   written, user-visible reason.
3. Trade-level parity PASS (≥ 80 % trips matched both ways, exit agreement reported) on a cross-venue sample
   (≥ 4 crypto long, 3 crypto short, 4 stocks long, 3 stocks short), with the *engine and live files that will deploy*.
4. Unit tests for every new twin green; compile clean; LOCKED_FILES change-log rows written.
5. Deploy (director): engine cut announced, archive pre-cut progress (BIBLE §24), md5 on Mac/S1/S2/S5, live processes
   restarted and log-verified (entries ≠ 0, no crash loop) — USER authorised full deploy incl. restart.
6. Every currently live sym_side re-verified with trade-level parity; failures `_NEG_BLOCK`ed before the next open.

---

## 1. The original job (USER 2026-10-05, verbatim intent)

1. **Encyclopedia** of every function in the ez_ (crypto) and tradier_ (stocks) systems and their vectorized equivalents,
   so any agent can look at a sym_side's results in `/SPREADSHEETS` and say *what is wrong* (too many trades, TIM too
   long, DD too high, gain too low, below B&H, …).
2. **v15_pilot re-runs the tabs of the sheet with that diagnosis in mind — NOT sequentially** (the sequential greedy can
   never get further than where we are): find the faults, **first soften the filters** (so every entry, reentry and
   augment switch starts producing deltas, even negative), **then add entries and exits**, **then tighten the filters
   again** to reach high gain and > B&H on each sym_side.
3. It runs **immediately after all cells are calculated, while the NPZ is in RAM** (long and short): slower tests,
   higher quality.
4. Limited to existing switches for now; once good it **points out which switches/filters are missing**.

User decisions (2026-10-05): search on 30D + verify top finalists on 365D · replace the final set when the repaired
set is better and compliant · ≤ 15 min per sym_side · B&H is a stretch goal, never a gate.

---

## 2. What is built (state 2026-10-06)

| Piece | Where | State |
|---|---|---|
| Encyclopedia hub + 9 chapters + evidence data | `ENCYCLOPEDIA.md`, `docs/encyclopedia/01–09`, `data/encyclopedia/{lever_evidence,fleet_diagnosis}.csv` | done (read-only census; line numbers drift — grep) |
| Repair logic (diagnose → SOFTEN → ADD → TIGHTEN → POLISH → 365D VERIFY, gap report) | `tools/v15_diagnose_repair.py` | done, 5 unit tests green (`tests/test_v15_diagnose_repair.py`) |
| Pilot hook (after `_final_filter_recheck`, pool + NPZ hot, delta-log per eval, `DIAGNOSE_REPAIR` tab, `v15_diag_repair/{SS}.json`, C rewrite on adoption, 365D slice prepared once in RAM) | `v15_pilot.py` `_diagnose_repair()` | done on Mac, compiled; **NOT deployed to the fleet** |
| Proofs on real NPZ (S1, isolated driver, report-only) | `/tmp/diag_proof/` on S1 | run 1: BNBUSDC_LONG 13.23 → 14.30 (1 change); APO_LONG kept. Exposed: throughput, `MODE` candidate, 365D starvation — all fixed; run 2 in progress |

Safety built in: never applies promotion-blocked switches, `ABLATION_DISABLE_*=True`, bool safety gates True→False,
structural fields (`MODE`, `BASE_TF`, sizing bases), `NEVER_APPLY` fabrication-class switches (`SIMPLE_PRICE_GT0_ENABLED`).
Every eval is a real engine eval logged to the delta log (sentinel stays alive); adoption needs a fresh compliant eval.

---

## 3. Steps to put it in motion once §0 is closed

1. **Re-baseline on the parity engine.** The parity fixes change engine and live behaviour = engine cut. Archive
   pre-cut progress dirs, start a new defaults round (BIBLE §60.6), never average deltas across the cut.
2. **Re-check the repair candidate pool against parity status.** Only switches that are wired live (lane B/C ledgers:
   WIRED_LIVE / ALREADY_REAL) may be APPLIED by the repair; everything else measured only. Implementation: load the
   lane ledgers (or a regenerated `data/SWITCH_BIBLE.json` with `ledger_flip_proven`/parity flags) into
   `promotion_block_reason`/`NEVER_APPLY` — one small change in `tools/v15_diagnose_repair._forbidden`.
3. **Second proof round (isolated, report mode).** On S1 with the cut engine: 2 crypto long, 2 crypto short, 2 stocks
   long, 2 stocks short, `V15_DIAG_REPAIR_MODE=report`, `V15_PROGRESS_DIR=/tmp/proof_diag_*`, `--workers 14`.
   Acceptance: phase finishes within budget, no RED storm, every adopted set fresh-verified, 365D verdicts present,
   DIAGNOSE_REPAIR tab complete.
4. **Trade-level parity on the repaired sets** (`tools/v15_parity_check.py`, now trade-level): a repaired set is only
   adoptable for live if it passes. Wire this as the acceptance condition (finalist pool → parity → adopt).
5. **Fleet deploy of the pilot** (`v15_pilot.py`, `tools/v15_diagnose_repair.py`): rsync S1/S2/S5, md5 verify, herds
   pick it up at the next launch (in-flight pilots unaffected). Start in `report` mode for one round, then `publish`.
6. **Budget check against the herd hardcap** (90 min/pilot): the phase clips itself to leave 15 min for DONE; watch
   `[DIAG] skipped: only …s left` lines; if frequent, raise herd cap or lower `V15_DIAG_REPAIR_S`.
7. **Daily loop:** repaired sets flow into the morning go-live exactly like today's finals (365D + trade-level parity +
   `golive_final`), so the improvements reach live before the open.
8. **Missing-switch worklist:** aggregate `gaps` (MISSING_LEVER / LEVER_EXISTS_BUT_COSTLY) from every
   `v15_diag_repair/*.json` per cat_side → `data/parity/missing_levers_{date}.md` — the input for new switches/filters,
   each built by the 4-surface wiring rule (vec + ez + tradier + config) so parity holds from day one.
9. **Encyclopedia upkeep:** after the parity cut, refresh the chapters' read-first facts (ENCYCLOPEDIA §1, §7) — several
   items there (label-only exits, vec-only augments, inert live paths) are exactly what the parity lanes change.

---

## 4. Open decisions for the user (when the emergency is closed)

- Coherence: on adoption the published set differs from the set the sequential rows measured (the DIAGNOSE_REPAIR tab
  holds the measurements). Keep direct publish (current) or schedule a re-fill (REDO) like COMPLIANCE does?
- Trade-parity threshold: 80 % of trips matched both ways (current default) — tighten after the first parity round?
