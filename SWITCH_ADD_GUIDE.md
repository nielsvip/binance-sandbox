# SWITCH_ADD_GUIDE — adding a switch end to end (any agent can follow this)

**Rule position:** the template one-script rule ([LOCKED_FILES.md](LOCKED_FILES.md)) has exactly ONE
exception — adding a switch, and only through `tools/v15_switch_add.py` below. No hand edits to
`*_TEMPLATE_*.xlsx`, no other writer. A genuinely NEW STRATEGY still needs explicit user approval
first (see NEW STRATEGY PROHIBITION in [CLAUDE.md](CLAUDE.md)); a switch is a knob on an approved path.

**Contract (from [BACKTEST_BIBLE.md](BACKTEST_BIBLE.md) §§4.1b+7.2, enforced by tooling, not memory):**
every switch/state name has a wiring-index entry and reads in BOTH engines, a config default the
template bold matches exactly, and its live-trading function applied to the live-trading columns.
New wiring goes in a `vec_decisions/<area>.py` predicate + a single call in `v12_quick_engine.py`.

## Step 0 — Design (5 lines, in your reply before you code)

Entry logic, exit logic, data pipeline, trade frequency, risk. One sentence each. If you cannot
write these, stop — you are adding a strategy, not a switch.

## Step 1 — Wire the code FIRST (rows come last)

1. `config.py` (`class Config`) and/or `config_tradier.py` (`class TradierConfig`): add the field with
   its live default. Crypto-only → config.py; stocks-only → config_tradier.py; both → both.
2. `v12_quick_engine.py` `QuickConfig`: same field, same default. If the stock default differs, add it
   to `apply_tradier_defaults`.
3. Live function: implement in `ez_manage.py` and/or `tradier_manage.py` (or a module they call) on the
   `process_position()` / `check_entry/exit_candidates()` path. Code reachable ONLY from daemons or
   crons the backtest never runs does NOT count as wired.
4. Vector predicate: new logic in a `vec_decisions/<area>.py` predicate + a SINGLE call site in
   `v12_quick_engine.py` (§7.2: never scatter call sites across the file).
5. `data/sweep_defaults/cat_side_defaults_4.json`: rebuild via `tools/build_cat_side_defaults_4.py
   --stage-only`, then promote + verify.
6. Behavioral test: add/extend a test that flips the switch and asserts the ledger/behavior flips with
   everything else fixed (see §7.3 zero_audit protocol; `tests/test_v15_g_vector_yellow.py` pattern).

## Step 2 — Registry + wiring index

1. Add the switch to `data/wiring/vec_function_registry.json` → `registry`:
   `{"live_counterpart": "...", "suggested_home_tab": "<TAB>", "valid_options": [default, alt, ...]}`
   with **≥2 options**.
2. `python3 tools/build_switch_bible.py` then `python3 tools/verify_switch_bible.py` — zero new
   violations for your switch (no TEMPLATE_NO_CONFIG / LINK_LOST / VEC_MODULE_NOT_CALLED /
   DEFAULT_MISMATCH). Fix code until green; never proceed red.

## Step 3 — Pick the tab (exact right tab, in authority order)

1. Registry `suggested_home_tab` (the adder enforces this — a mismatch refuses).
2. Else `MANUAL_HOME` in `tools/v15_template_home_rules.py` (evidence-based placement).
3. Else `default_tab_for(lifecycle, name)`: EXIT→EXIT_VELOCITY/EXIT_STRUCTURAL by token,
   REENTRY→REENTRY_WINDOWED/REENTRY_ADAPTIVE, AUGMENT→AUGMENT_RISK_SIZING/AUGMENT_TREND,
   REDUCE→REDUCE_PROFIT_LOCK/REDUCE_SIGNAL_RATER, else ENTRY_CONFIRMATION_GATES; sizing knobs →
   STDEV_SLOPE_SIZING; account-level gates → GLOBAL_RISK_GATES. Lifecycle = the bible `kind`.
4. Still unsure → ask the user. A wrong tab is worse than a slow answer. One switch lives in
   exactly ONE tab per template (verified).

## Step 4 — Stage the rows (dry-run, review, apply)

```bash
python3 tools/v15_switch_add.py --switch MY_SWITCH --tab ENTRY_CONFIRMATION_GATES \
  --candidates True,False --default True --venues both
```

- `--venues crypto|stocks|both`, `--sides long|short|both` (a LONG/SHORT token in the name pins the
  side). BOOL→True/False pair, TF→OFF/15m/1h/4h/D domain, INT/FLOAT/ENUM→≥2 explicit values.
- Default dry-run prints the plan (file/tab/insert row). Review it, then re-run with `--apply`.
- The tool inserts whole rows after the LAST WHITE row of the tab (before the orange block), mirrors
  style, puts the default row first (bold B + `is_default` YES), backs up each file to
  `backups/before_switch_add_*`, then proves row-guard integrity (zero original rows changed) and the
  one-bold-default gate. Any failure refuses before saving.
- Refusal catalog: unknown tab / <2 candidates / default not in candidates / not a config field /
  config-default mismatch / not a QuickConfig field / no bible entry / dead status / no vec reads /
  no live reads / registry tab mismatch / duplicate rows. Each names the step to fix — do that step,
  do not bypass. There is no bypass flag on purpose.

## Step 5 — Prove it end to end (no proof, no ship)

1. `backtest_v12_engine.py --mode crypto|tradier --symbols ONE_SYM` with the switch flipped vs
   default: the ledger must flip with everything else fixed (live-path proof).
2. Pilot smoke: one sym_side short run (or the unit test from Step 1.6) shows the new rows calculate.
3. `python3 tools/verify_switch_bible.py` green; `SWITCH_BIBLE.json` rebuilt if presence changed
   (`SWITCH_BIBLE_TEMPLATE_DIR=SPREADSHEETS/TEMPLATE_FINAL_NORM` for the fleet set).
4. Full `staged_verify` currently reports thousands of PRE-EXISTING str-typed-option violations in
   live templates — your add must not INCREASE the count (compare before/after). Do not try to fix
   that backlog inside a switch-add; it is a separate approved migration.

## Step 6 — Fleet sync

Re-run the adder with `--fleet`, or by hand per file:

```bash
rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new" \
  SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx \
  s1-via-gateway:~/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/
# + s2, s5, then md5sum all three vs local
```

New pilot launches clone the new templates automatically. Never restart running pilots for this.

## What lives where (do not improvise parallel tooling)

- Writer: `tools/v15_switch_add.py` (this exception). Stager/verifier/applier for bulk restructures:
  `tools/v15_template_add_missing.py` → `tools/v15_template_staged_verify.py` →
  `tools/v15_template_staged_apply.py` (targets the generic `SPREADSHEETS/TEMPLATE_*.xlsx` set, NOT
  the fleet set; currently gated by the pre-existing violation backlog — see Step 5.4).
- Refused legacy: `tools/v15_add_switches.py`, `tools/v15_add_orange_rows.py` (USER 2026-09-30
  class refusal; superseded by this guide — do not un-refuse them, use the adder above).
- The ONLY templates are `SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_*.xlsx` (4 independent files,
  ~3393 rows each, diverging per cat_side). The legacy generic `SPREADSHEETS/TEMPLATE_*.xlsx` set was
  archived on 2026-10-07 (`backups/archive_generic_templates_20261007/`, never restored over FINAL_NORM).
