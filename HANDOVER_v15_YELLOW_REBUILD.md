# v15 Yellow-Cell Rebuild — Code-Grounded Switch↔Filter Map
**Date:** 2026-09-28 | **Author:** niels (nielsvip@gmail.com) | **Status:** DONE (map + 4 templates + pilot rewired, verified). NOT yet synced to S1/S2, no herd launched.

Supersedes the approach in `HANDOVER_v15_YELLOW_FIX.md` (that "smart prefix" fix tested wrong/arbitrary filters — see Root Cause).

---

## ROOT CAUSE (proven)
The per-cell **yellow fill** in the 4 canonical templates was misaligned. Rows are sorted worst2best (col A = switch), but the yellow `PatternFill` stayed pinned to absolute row positions → highlights landed on the wrong switches (e.g. `WT_15M_BOUNCE_OPEN_ENABLED`, an entry switch, was yellow on `EXIT_TOP_FADE`/`EXIT_R1_R2`; candidate rows of one switch disagreed: 22 vs 35 vs 0 yellow cells).

Both fallback mappings were also unreliable:
- `FILTER_DICTIONARY_V2."Switches exactly it gates"` — only 133/441 rows name a real switch; 293 are prose ("BB pullback entries"). `get_opportune_filters` fuzzy-matched that prose via `_token_overlap`.
- `v15_pilot` prefix hack (`switch.split("_")[0]` substring + `all_hdrs[:10]` fallback) — tested arbitrary filters (NO-LIES risk).

The last-working `UNIUSDC_LONG_..._matrix.xlsx` computed a **tight per-switch set** (~3/row), NOT 231/row — so the correct model is per-switch opportune, not "test everything".

## GROUND TRUTH USED (code, not averages)
- `data/knob_registry.json` — every knob's `role` (MAIN_SWITCH/FILTER/SUB_SETTING/TF/CONDITION), `family`, `group` (ENTRY/EXIT/SIZING/REENTRY/OTHER).
- Function-level **read-site co-occurrence** parsed from the live scripts: crypto = `ez_manage.py`, `wt_dc_*`, `local_extremes_scorer.py`; stocks = `tradier_manage.py`, `tradier_*`, scorers. (Reads: `config.NAME`, `getattr(config,"NAME")`, `_cfg`, `_psym_get`.)
- `FILTER_DICTIONARY_V2` "Sheets applicable" + "Live location" for the 43 template filter modules (all `*_FILTER_TF`).

## RULE (user chose "Medium")
For switch S in a sheet, filter F (of the 43 bases) is **opportune** iff `lifecycle-compatible(F, sheet)` AND any of:
1. **family** — F's underlying knob shares S's `knob_registry` family;
2. **co-read** — F's module and S are read in the same live function (functions reading ≤40 knobs, to exclude giant orchestrators);
3. **group** — F's lifecycle == S's lifecycle (ENTRY/EXIT). Management/global switches (AUGMENT/REDUCE/REENTRY/GLOBAL/SIZING) take the GLOBAL gate net; globals never flood ENTRY/EXIT rows (they enter there only via family/co-read).

Result: 5023 switch-rows mapped, **0 zero-opportune**, per-switch median 22 (entry≈10, exit≈8, mgmt/global≈20-23). Signal mix: group 81,624 / co-read 299 / family 225.

## DELIVERABLES / FILES CHANGED
- **NEW** `data/opportune_filter_map.json` — `{CATEGORY: {SHEET: {SWITCH: [filter_bases]}}}`, read by both the template regenerator and `v15_pilot`.
- **NEW** `data/opportune_filter_map_provenance.csv` — every (template,sheet,switch,filter,signal) for audit.
- **REBUILT** `SPREADSHEETS/TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx` — yellow fills re-aligned from the map; stale yellows cleared; CRC-validated (28 entries each); FILTER_DICTIONARY_V2 preserved (441 rows). Backups: `backups/before_yellow_rebuild_*_202609280237.xlsx`.
- **EDITED** `v15_pilot.py` (backup `backups/before_yellow_rebuild_v15_pilot_202609280237.py`):
  - added `_load_opportune_map` / `map_key_for_symside` / `opportune_filter_bases` / `opportune_filter_rows`;
  - main filler (~L1141): replaced prefix hack with `opportune_filter_bases(new_symside, sname, switch)`;
  - two per-row selectors (~L3519, ~L4026): now `opportune_filter_rows(new_symside, sheet, switch)`;
  - `_load_filter_dictionary` candidate order: canonical templates first (legacy `TEMPLATE.xlsx` last; it is CRC-corrupt in `docProps/core.xml`).

Reproduce the map: scripts in the session scratchpad (`build_coread.py`, `build_map.py`, `regen_templates.py`).

## VERIFIED
- v15_pilot compiles; helpers resolve correctly (crypto entry bounce → 10 entry filters incl. WT_15M_BOUNCE_* via family; stocks exit → 8 exit filters).
- Templates re-aligned: entry switches show entry filters; candidate rows of one switch now agree.
- Fills == pilot compute (both read `data/opportune_filter_map.json`).

## NOT DONE (needs your call — costs money / touches running herds)
1. rsync templates + `v15_pilot.py` + `data/opportune_filter_map.json` to S1 (crypto) / S2 (stocks), md5 verify.
2. Launch a single-symbol proof (e.g. `ZECUSDC_SHORT`) and confirm F/G/L:BI fill on the re-aligned yellows.
3. Then resume the herd.

## NOTE ON COST/COMPUTE
Medium ≈ 42 yellow option-cells/entry row (10 bases × ~4-5 TF options). If per-sym runtime is too high on the herd, the same map supports a "tight" tier (drop the group net; keep family+co-read) with no template/pilot changes beyond regenerating the map.
