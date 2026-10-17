# OPEN ITEMS — session 2026-10-16 → 2026-10-17 (per_sym parity work)

Everything from this session's prompts that is **not resolved**. Items the session finished are listed
once at the bottom so nobody redoes them. Every number here was measured during the session; where
something is inferred rather than measured it says so.

**Global precondition for all live items:** none of the code/config changes below reach the running
daemons until they restart — they hold the old modules. The autosave committer is SIGSTOPped (see D1),
so `backups/` is the only history for this work.

---

## A. BLOCKED ON A USER DECISION

### A1. Sizing: confirm 15 / 80 / 120 is intended
Effective per-board sizing now resolves to `START_POSITION_SIZE 15.0`, `MAX_ORDER_VALUE 80.0`,
`MAX_POSITION_SIZE 120.0`. Earlier in the session the light table was restored to its real values
(45 / 180 / 360) from the `# was` comments; **another session then changed those entries to 15 / 120**
(my later edits came back flagged "file had been modified on disk"). It may be deliberate caution by a
teammate or by you — this session did not make that change and is not assuming it was intended.
→ **Confirm 15/80/120 is the number you want live**, or restore 45/180/360.
Evidence: `config.py:5823-5824`, `per_sym_live_set.full_set('GALAUSDT_LONG')`.

### A2. "NOTHING open on exchange that per_sym/charts/xls would not have open"
Not started beyond diagnosis. This means **closing live positions**, so no action was taken.
The deliverable is the list first, not the action:
- bucket every open position across `ang/fin/flz/inf/men` + `trb/trc` as
  (a) has a current board **and** the vector holds it, (b) has a board but the vector is **flat**,
  (c) **no board at all**;
- source: `./<acct>/long_positions.json` + short equivalents + `./<acct>/tracker.json` (confirmed
  updating live, 03:06) against `per_sym_active`;
- buckets (b) and (c) are what must close under the mandate.
Existing machinery to route it through: `VEC_STALE_FLATTEN` / `VEC_CONVERGE_OPEN` in
`live_twins/vec_exact.py`. Do **not** write a bespoke close path.
Note the interaction with A3: the per-position set pin is what currently *prevents* convergence
mid-trade, so A3 and A2 are the same problem seen from two ends.

### A3. Pin promotion — proposed, not approved, not implemented
A held position keeps the set it was opened under until flat (`vec_exact.pin_activate`, docstring ~line
119), so a better board cannot reach an open position. Proposal as put to you:
1. on a set change for a pinned sym_side, simulate the current bar under the **new** set; if it also says
   "holding", promote the pin to the new set — no orphan risk by construction, and on the docstring's own
   figure (~28% of changed sym_sides flip state) that adopts ~72% of set changes immediately;
2. if the new set says **flat** while live holds, route it as `PIN_CONVERGE_EXIT` through the normal exit
   path instead of discarding the verdict;
3. gate the whole thing behind `VEC_EXACT_PIN_PROMOTE_ENABLED`.
Bullet 1 is shippable on the compatibility proof alone. **Bullet 2 should be measured on the frozen NPZ
set before it ships** — "a better set wants out" must be shown to beat "the opening set's exit".
`live_twins/vec_exact.py` is binance-4b's file → coordinate.

### A4. Restart decision for the live daemons
Every parity fix from this session is inert until `ez_manage` / `ez_positions_quick` /
`tradier_manage` restart. Direction of the change is exposure-reducing and board-aligning. Not done
unilaterally: 5 crypto accounts with open positions. Options: staggered restart now, or let the
keepalive cycle pick it up.

### A5. Autosave committer is SIGSTOPped — resume or not
`autosave_15min.py` pid 22008 is in state `TN` (stopped) since its last log line
`[2026-10-14 21:03:32] Backup dir at 14.9GB — pruning by stability score`; last commit 2026-10-14 20:58.
launchd `KeepAlive` sees the pid alive so it never restarts it (same wedged-pid liveness failure
`LOCKED_FILES.md` records for the ez_manage watchdogs). ~49 CPU-hours over 9 days on a 14 GB
`backups/autosave` dir, so it may have been stopped deliberately.
**Not resumed**, because `kill -CONT` immediately backs up 275 files, prunes 14 GB, runs `git add -A` +
commit and **pushes to origin and s1-backup** — capturing several agents' half-finished edits to locked
live files in one outward push. Also note `git push origin-ssh` has been failing on a denied deploy key
since before it froze (`origin` and `s1-backup` were fine).
Nothing in this repo has been committed for ~2 days — this affects every agent's work, not just this
session's.

---

## B. UNFINISHED WORK WITH A PLAN

### B1. Per-function attribution for the non-vectorizable functions (your core ask)
**Step 1 of 3 is DONE** (see E7 — harness now produces 645 trades instead of 0). Remaining:

- **Step 2 — one switch per path.** `tools/forward_live_vs_vector/paths.yaml` header already states the
  principle: *"One boolean/level switch = one attributable delta."* Its entries violate it — 11 paths
  covering 37 switches, with `P1_entry_engines` enabling 6 at once, `P3_micro_3m5m` 4, `P10_fixed_qty` 10.
  Split into ~33 single-knob entries, one per `_NON_VEC_KNOBS_EZ` member (`ez_manage.py:7849`), and
  **keep the bundles alongside** — *bundle − Σ(singles)* is the interaction term.
- **Step 3 — real per-function metrics, through `metrics_guard`.** Per function: n, per-trade mean, win
  rate, positive-day ratio, and `sharpe_per_trade` on the trade-return list, plus the **same-population
  `vector_only` baseline** so each function is scored against parity rather than a global total.
  `[DIAGNOSTIC ONLY]` below the sample floor, and it must not promote.
- **Step 3b — a promotion gate for `NO_TWIN`, which has none.** `data/reports/ltf_flip_latest.json`
  carries four explicit checks for the LTF leg (`ltf_exits>=20`, `ltf_total>0`, `ltf_per_trade>0`,
  `ltf_pos_days>=60%`) and **zero** for `NO_TWIN`, so that leg sits in `HOLD` forever regardless of what
  it shows. Without this, "turn on only the good functions" can never happen.

**Do not build on the descriptive legs.** `ltf_flip.py`'s own docstring: *"The legs are DESCRIPTIVE
(where PnL realized), not causal"*, and protective legs look negative by construction. The `NO_TWIN
+101.4%` figure is **not** evidence of contribution. Causal attribution = the `paths.yaml` knockout
diff (`live_full.pnl − knocked_out.pnl` on the same window), nothing else.

### B2. Minimum-mode as a true one-switch overlay (Proposal A, partially done)
Done: all 29 active in-place sizing edits restored across the class globals and the light/base/extreme
tables; `CRYPTO_MINIMUM_TRADING_MODE=False`; light mode applied.
**Not done** (the part that makes the switch actually round-trip):
1. `CRYPTO_REAL_SIZES` dict holding the real value of all 39 fields, so the real numbers live in data
   rather than only in `# was` comments;
2. one source for the cap table — move `CRYPTO_MIN_SIZE_CAPS` into `config.py` beside the flag, which
   retires the literal duplicated in `per_sym_live_set.py` and its `ast` parity test;
3. `tests/test_minimum_mode_roundtrip.py`: flag ON → every sizing read ≤ cap and ≥ the exchange floor;
   flag OFF → every read **==** the real value, across all 39 fields and all three market modes.
Until (3) exists, "flip one switch and the real values are back" is a hope, not a property.
Note 10 further `# was` comments remain on **commented-out** legacy lines in `_apply_mode` — inert,
left alone deliberately.

### B3. Enforce parity on EXITS properly
`STRICT_VEC_PARITY_GATE_EXITS` is now `True` on both venues (changed by another session on your
"we need total parity" order; `config.py:6921` documents a 3-day census of **0 of 1096** crypto closes
blocked, and the allowlist extended with FIXED_STOP / ULTIMATE_DC / VIGILANCE_DC4 / DAYTRADE_* /
TECHNICAL_TARGET / UNCOND_DC / LH_LL_TOP_EXIT). That census is **theirs, not re-verified here.**
Open: watch for `BLOCKED_VEC_PARITY_*` in live logs after the next restart. The 2026-09-28 failure mode
(allowlist missing a live loss-exit token → stranded losers, ADAUSDC −3.97% → −4.66%) is the thing to
catch early.

### B4. `VEC_EXACT` is missing from `UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS`
`UNIVERSAL_NOLOSS_GATE` is `False` on both venues, so this is **inert today**. But the 35-entry bypass
list contains no VEC entry, so if that gate is ever re-enabled every `|VEC_EXACT` close would be blocked
by it — exactly the failure that stranded losers before, and now directly against the
"always in tandem, no exception" mandate. One line in `config.py` / `config_tradier.py` makes the gate
safe-by-construction. Recommended, not done (outside what was asked).

### B5. Conviction sizing needs board-sourced multipliers before it can return
`CONVICTION_SIZING_ENABLED=False` per your instruction, *until the 5 accounts book a positive day*.
Note **why** it had to go off regardless: it read `size_mult` from `data/persym_final_book.json`, dated
2026-09-01, which is now inert (`PERSYM_FINAL_BOOK_ENABLED=False`). So re-enabling it as-is would resume
sizing off a 6.5-week-old file. To come back it needs its multipliers rebuilt from the **daily boards**.

### B6. The 5m phantom-leg class of bug is not closed in general
`DC_BREAK_HIGH_5M` is fixed (gated on `LTF_SUB15M_TRADING_ENABLED` on both the live and vec sides).
The underlying hazard remains: the NPZ has no `dc_high_5m` / `dc_high_crossover_5m`, and
`_NPZStore.b()/f()` **return a default instead of raising**, so a twin whose code looks correct silently
never fires. Same shape as the documented `k_3m`/`d_3m` phantom at the 15m-floor fix.
→ Make the vec store raise (or hard-log) on a missing series. That would surface every other phantom leg
at once, which is valuable and wants its own measured pass.
Also: if `LTF_SUB15M_TRADING_ENABLED` is ever turned on for the paper lane, the 5m asymmetry returns —
live would trade 5m, the vector still would not, because the key is *missing*, not switched off.

---

## C. VERIFICATION DEBT / SUSPECTED DEFECTS

### C1. `W=1 L=628` — check before anything is built on the harness
First successful harness run (`PUMPUSDT_LONG`, 7d, sandbox + live boards):
`[V8_FINAL_PNL] B n=629 sum=+0.35% W=1 L=628`, `VOL n=4 W=0 L=4`, total `n=645 sum=+0.35%`.
A 0.16% win rate netting +0.35% is not a plausible trade distribution. Likely either a family firing off
a default rather than real data (the B6 class) or an exit-attribution artefact booking losses per bar.
**Per your own rule an implausible result is a broken run, not a result.** Every one of the ~33
per-function ablations in B1 would inherit this error, so this is the gate before B1 step 2.
Also: the stocks half (`VALE_LONG`) of that probe had not finished.

### C2. `men:DOTUSDT_LONG` — AUGMENT carrying an exit reason, unexplained
2026-10-15T18:57:54, `type=AUGMENT`, `reason=BREAKOUT_LEASH_EXIT_LH_LL`, with
`BREAKOUT_LEASH_ENABLED=False` at board, cat_side and global. An augment labelled with an exit reason is
an anomaly in itself, and the per-position pin cannot excuse it (pins only excuse exits, and AUGMENT is
already refused while pinned ≠ latest). Yesterday's process config state is unreconstructable, so **no
cause was attributed**. Worth reading the leash call site.
The only other candidate violation, `inf:ETHFIUSDC_LONG MTF_GR_WT_EXIT`, **was** explained — the
per-position pin, verified against `pin_activate`/`pin_release` — and the audit now reports it as
`exit_pin_unresolved` rather than a violation.

### C3. Two stale parity tests left failing on purpose
`tests/test_parity_live_twin_psym_cs_get.py::test_precedence` and
`::test_old_psym_get_returns_snapshot_for_contrast`. They assert the **pre-mandate** precedence —
`KNOB_J` resolving from the JSON `overrides` half (removed by the 2026-10-06 single-source change) and
`_psym_get` returning the snapshot via a separate `get_full_config` call (collapsed into `get_overrides`
by the 2026-10-15 full-set mandate). **Proven not a regression:** they fail identically against the
pre-edit `ez_manage.py` backup. They need rewriting to the current contract or deleting — that is a call
on your mandates, so they were left failing and visible rather than quietly adjusted to match the code.

### C4. Other pre-existing test failures (none caused by this session)
- `test_switch_parity.py::test_live_order_switches_zero_hard_after_catside_conversion` — 6 HARDs:
  `MAX_POSITION_SIZE` + `MAX_ORDER_VALUE_FIN` (your deliberate exposure cuts) and four **non-sizing**
  switch divergences that predate this session: `DC_POS_SIZE_LIVE_ENABLED`, `NEWBORN_LOSS_KILL_ENABLED`,
  `VIGILANCE_GUARD_ENABLED`, `GUARANTEED_REENTRY_K_STOCH_GATE_ENABLED` (your own 2026-10-16 change).
  Those four can move which bar a trade lands on — worth closing for parity.
- `test_v12_overlay_twins.py::test_no_value_identical_overlay_twins` — fails identically with
  `PER_SYM_FULL_SET_READER=0`, i.e. pre-existing. It is the twin-parity test, so it matters.
- 3 collection errors, all in files this session never touched:
  `test_parity_live_twin_stocks_EMA50_15M_ENTRY_FILTER_ENABLED.py`,
  `test_parity_live_twin_stocks_NEWBORN_LOSS_KILL_FILTER_TF.py` (both `StopIteration` from
  `tests/_laneC_parity_helpers.v12_block` not finding its marker in `v12_quick_engine.py`),
  `test_v15_herd_host_specific.py`.
- `test_switch_parity.py::test_startup_gate_real_repo` — one `GHOST_SWITCH` hard; fails isolated in both
  reader modes, so pre-existing (it briefly looked like a regression due to test-order flakiness).

### C5. `_RecordingClient.futures_position_information` missing
`PHANTOM_KILL` fails and is skipped on every bar in the scalar harness — 10,484 of 19,177 log lines in
one run. Two costs: wasted runtime, and **a live code path the "live-faithful" harness never exercises**,
which is a parity hole in the measurement tool itself.
`_RecordingClient` is in `backtest_v12_engine.py`, and CLAUDE.md requires reading `BACKTEST_BIBLE.md`
before touching that file — **not done this session**, so the fix was not attempted.

### C6. 32 unmapped trade reasons
`tools/per_sym_trade_reason_audit.py` leaves 32 post-settings events unclassified (mostly singletons:
`REENTRY_MONITOR` 13, `STOP_LOSS` 2, `GAP_MOC_EXIT`, `DC_4h_HARD_STOP`,
`MANDATORY_REENTRY_PRICE_CROSS` …). Reported with counts, never asserted as parity-clean. Mapping them
needs reading each emitter — and per the session's own hard lesson, **never infer a reason's gate from
its name**.

---

## D. INFRASTRUCTURE / HYGIENE

### D1. See A5 (autosave SIGSTOPped) — listed as a decision because resuming it pushes outward.

### D2. `/Volumes/E3T` unmounted → the other archive cron has been dead since 2026-10-15T23:41Z
`tools/v15_history_archive.sh` (per_sym_history + manifests, "kept forever") logs
`FAILED: drive not mounted`. It resumes on reconnect since `LAST_ID` lives on the drive, so nothing is
permanently lost — **but it is not a backup while the drive is unplugged.** Different drive and different
payload from the new daily archive (E4), so the two are complementary, not redundant.

### D3. `WUSDT` is still in `symbols_active.json` with no board
Entry-gated today (absent from `published_selection.json` → `NO_LATEST_BOARD`; hermetic mode `enforce`;
guardian `KEY_OPEN_BLOCK` on `WUSDT_LONG` for ang/inf/men), and binance-4b confirmed only exits and
broker reconciliations appear in its history. Removing it from the live universe was **not** done — that
is a live-universe change nobody sanctioned.

### D4. 181 of 417 live stock sym_sides now lose entries
Consequence of `TRADIER_NO_BOARD_ENTRY_BLOCK=True` (your explicit call): they are absent from
`published_selection.json`. 236 keep trading, so the book does not halt. Flagged so the number is known,
not as a problem to undo.

### D5. Mac holds ~21 NPZ
Per your architecture note the Mac should hold none except for quick tests. Pre-existing, not touched.
`FORWARD_NPZ_DIR` was added for exactly the quick-test case and documented as "must not be used to grow
a second NPZ home".

### D6. 61 sym_sides still not board-exact
binance-4b's count (gate-OK 373 of 434 published boards after their fix), 22 of them stocks. Their lane,
their measurement, not re-verified here.

---

## E. DONE THIS SESSION — do not redo

1. **Per-sym reader single-source.** New `per_sym_live_set.py` (`full_set()` = `per_sym_store` full
   frozen set, JSON `full_config` fail-soft); `ez_manage._ezm_per_sym_layer` on both `_PerSymProxy`
   paths; `ez_positions_quick._per_sym_full_set` on `_get_per_sym_overrides` and `_get_btc_sym_cfg`.
   Reader drift **142 absent + 67 drifted → 0 and 0** of 328 crypto rows; `reader_off_board=0` on all six
   accounts. Kill switch `PER_SYM_FULL_SET_READER=0` restores the old behaviour with no code change.
2. **Sizing-cap bypass closed.** 269 capped-knob instances were already being served to live uncapped
   through the JSON half (`GALAUSDT_LONG` resolved `START_POSITION_SIZE 28.0`, `MAX_ORDER_VALUE 360.0`
   against 6.0 caps). `per_sym_live_set._apply_caps` clamps for every reader, crypto only, fail-safe.
3. **Mode tables restored + light mode applied + light mode made sizing-only.** 29 active values
   restored; the three timing knobs (`REDUCTION_COOLDOWN_SECONDS` 15, `AUGMENTATION_COOLDOWN_SECONDS` 90,
   `HIGH_GAIN_AUGMENTATION_MIN_SIZE` 50) now vector-exact; `MAX_ORDER_VALUE_MEN` 440→280;
   `MAX_POSITION_SIZE_MEN/FIN` 360→280 (vector-exact and lower).
4. **Daily ext-drive archive.** `tools/per_sym_daily_archive.py` + cron 23:50/23:58 UTC →
   `/Volumes/SSD2T/binance_archive/per_sym_daily/`. Content-addressed sets (an unchanged sym_side costs
   ~100 bytes/day, not 120 KB), that day's `/history` events **with their indicators**, consistent whole-db
   snapshots pruned to 14 dailies + first-of-month forever. 7 days loaded, 2823 distinct sets, verify
   clean. `--show SYM_SIDE --day D` is the matchback. Past days are stamped
   `source=history_reconstructed` and never passed off as observed.
5. **`QTY_LIVE_ORDER_CAP_MULT` / `QTY_LIVE_POSITION_CAP_MULT` wired live** (were QuickConfig-only, so the
   backtest clipped at 8×/10× START while live took the legacy absolute-cap branch).
6. **`DC_BREAK_HIGH_5M` gated** on `LTF_SUB15M_TRADING_ENABLED`, live **and** vec twin, fail-safe both
   sides; `LTF_SUB15M_TRADING_ENABLED` declared explicitly in `config.py` (was working only off a
   `getattr` default, invisible to the parity tooling).
7. **Ablation harness produces trades for the first time: 645 vs 0.** Cause was never throughput — the
   live boards and the NPZ live in different S1 trees. Correct invocation: run **in
   `~/binance-sandbox`** (backtest tree, the one NPZ home) with
   `PER_SYM_STORE_DB=/home/niels/binance/data/hourly_reconfig/per_sym_store.db`. `NO_LATEST_BOARD`
   refusals went from one per bar to **0**. `FORWARD_NPZ_DIR` added for Mac quick-tests only.
8. **Overrides above the daily board removed.** `PERSYM_FINAL_BOOK_ENABLED` False (2026-09-01 file was
   overriding 248 live rows — force-enabling 194, force-disabling 54); `CONVICTION_SIZING_ENABLED` False;
   neg-sharpe injection measured inert (0 of 186 rows). `CRYPTO_MODE_SIZE_CEILING_ENABLED` stays True as
   the one sanctioned deviation, until the 5 accounts book a positive day.
9. **No-loss prohibition overruled in the docs** (`CLAUDE.md`, `ABSOLUTE_PROHIBITIONS.md`) with your
   words verbatim and the superseded text kept for provenance. Still in force and stated there: no exit
   may ever be skipped; exits are never gated by a parity/board/entry check.
10. **Paper lane verified** — `tools/forward_parity/ltf_flip`, cron `35 0,6,12,18`, **both** universes
    (`ang/fin/flz/inf/men` + `trb/trc`), ran 00:49:21Z, 896 keys / 463 syms. 7d: `VEC15` +0.1063/trade,
    `LTF_135` **−0.3221/trade** over 52 exits, `NO_TWIN` +0.0635 over 1597. Verdict `HOLD`. So switching
    1/3/5m off is what the measurement supports. 3/5m NPZ history also confirmed (precompute machinery
    intact; `CDW.npz` still carries 145 `_5m` series).
11. **Two audit tools** — `tools/per_sym_live_parity_audit.py` (BOARD/READER/TRADED, `--legacy-reader`
    keeps the old drift reproducible, capped knobs reported in a separate "deliberate" block) and
    `tools/per_sym_trade_reason_audit.py` (now models the vec_exact per-position pin). **Mac only** — S1's
    db is the publisher db with no `per_sym_active` rows, so running them there audits nothing.
12. **One error made and reverted:** `TRADIER_HERMETIC_ENFORCE` was set True at 00:46 and reverted at
    ~03:05. `tradier_manage.py` ~47709 states the prerequisite in its own comment — the gate is
    `vec_provenance.gate()`, stock forward parity is **1 PASS of 35**, so enforcing "would refuse most
    legitimate opens and halt the book", and the flip is allowed only after a full-session reconciliation
    that has not been done. No `tradier_manage` process was running (US market closed), so it was never
    loaded. The prerequisite is now written into `config_tradier.py` so it cannot be repeated blind.

---

## Backups from this session (the only history while A5 holds)

```
backups/before_persym_single_source_202610162245.py          ez_manage.py
backups/before_persym_single_source_epq_202610162245.py      ez_positions_quick.py
backups/before_untracked_loss_*_202610162335                 the 5 new files
backups/before_mode_sizes_restore_lightmode_202610170009.py  config.py
backups/before_qty_live_cap_mults_202610170009.py            config_tradier.py
backups/before_lightmode_sizing_only_202610170019.py         config.py
backups/before_parity_enforce_sub15m_202610170046.py         config.py
backups/before_parity_enforce_stocks_gate_202610170046.py    config_tradier.py
backups/before_dc_break_5m_sub15m_gate_202610170051.py       tradier_manage.py
backups/before_dc_break_5m_sub15m_gate_vec_202610170051.py   vec_paths/dc_break.py
backups/before_npz_dir_override_202610170305.py              forward_harness.py
backups/before_hermetic_revert_202610170305.py               config_tradier.py
backups/before_noloss_prohibition_removal_202610170312.md    CLAUDE.md
backups/before_noloss_prohibition_removal_AP_202610170312.md ABSOLUTE_PROHIBITIONS.md
backups/before_final_book_off_202610170327.py                config.py
backups/before_conviction_off_202610170331.py                config.py
```

Kill switches that revert behaviour without a code change: `PER_SYM_FULL_SET_READER=0`,
`CRYPTO_MINIMUM_TRADING_MODE`, `CRYPTO_MODE_SIZE_CEILING_ENABLED`, `LTF_SUB15M_TRADING_ENABLED`,
`PERSYM_FINAL_BOOK_ENABLED`, `CONVICTION_SIZING_ENABLED`, `V8_DISABLE_PER_SYM`,
`PER_SYM_STORE_SQLITE_DISABLED`, `PER_SYM_APPLY_FULL_SET`, `LIVE_BOARD_DEAD_KEY_SKIP`,
`V15_GOLIVE_NOOP_BOARD_CHECK`, `VEC_EXACT_PIN_SET_PER_POSITION`.
