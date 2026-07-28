# START HERE — next session. 2026-07-28

Paste this whole file into a fresh session. It is written to be read cold.

---

## 0. THE ONE THING THAT WENT WRONG, SO YOU DON'T REPEAT IT

The user's instruction on 2026-07-27 was: **"analyse what Codex did to fix the
Opus disaster and take it from there."** Codex had produced a working method.
Instead of building on it, the previous session re-ran its own band-ladder
campaign for ~2 days, corrected its own metric three times, and reported on the
wrong artifact twice. **None of that is usable. Do not continue it.**

**Start from the Codex work.** Read, in order:
1. `BACKTEST_BIBLE.md` §15 through §15.38 — the Codex recovery record.
2. `STOCK_BACKTEST_RECOVERY_20260724.md`
3. `MATRIX_FILL_HANDOFF_20260728.md` — the procedure (still valid).

**Discarded, do not read for results**: everything in
`data/reports/vec_research/band_ladder_walkforward_*`, all of
`data/handle_priority/*.json`, and Bible §15.39–§15.44 (they document the
previous session's own mistakes; useful as warnings, worthless as findings).

---

## 1. THE USER'S NON-NEGOTIABLE PHYSICS — this is the key insight

> *"IT IS IMPOSSIBLE TO DO WORSE THAN B&H BECAUSE JUST ENTERING AND DOING NOTHING
> IS B&H."*

This is correct and it is a **diagnostic**, not an opinion. Any baseline or cell
scoring below buy-and-hold means the config is exiting or failing to enter — it is
**not** a valid stage-0 baseline. If your floor is negative, your floor is broken.
Stop and fix the floor before measuring anything.

**Therefore the current campaign is baselined on the WRONG THING.**
`stocks_repaired_20260725_c2`'s `key_baseline` shows:

| key | trades | capture_vs_bh |
|---|---:|---:|
| MU_LONG | 3,180 | +0.297 |
| NVDA_LONG | 215 | **−0.082** |
| VT_LONG | 497 | **−1.028** |

497 trades is the **live config trading and losing** — not stage 0. A true stage-0
baseline is **1 trade, ~100% time-in-market, return == B&H**. Fixing this is job #1;
every cell measured against a losing baseline is meaningless.

### The test order (USER 2026-07-28) — subtractive, never additive

| stage | config | gate |
|---|---|---|
| **0** | dynamic-quantity ladder ON, **every exit OFF** | ~100% TIM, return **is** B&H. **Hard gate.** |
| **1** | exits back on **one at a time**, sweeping values | must **raise gain** vs stage 0 |
| **2** | entry paths, one at a time | same |
| **3** | filters/gates, one at a time | same |

**Green = higher gain than the stage it was added to.** Nothing else is green.

If stage 0 does not land on B&H, an exit is not behind a switch. Three switch
shapes, none inferable from the name: plain `X_ENABLED`; boolean **without**
`_ENABLED` (`STRUCTURAL_RANGE_SHIFT_EXIT` alone produced 100% of 1,310 closes);
string TF knobs disabled with `"None"` (`LONG_STRUCT_EXIT_TF='D'` → 69 of 149
closes). Read `_cfg_bools()` / `_cfg_strs()`. Bible §13.7, §13.8.

User's reference point: **"if you just trade the green and red arrows on the plot
you have 6x b&h."** Codex's fixes reached **2–20× b&h**. Anything below 1× is a bug.

---

## 1b. DEFECTS FOUND **IN THE CODEX SYSTEM** — fix these to get back to 2–20× b&h

The Codex work is the right foundation and is intact (immutable archives in
`data/npz_recovery/`; pinned `matrix_npz/.../MU.npz` 2026-07-25, `VT.npz`
2026-07-26 — both original and read-only). But it has defects that explain why the
2–20× results never generalised. **Verify each independently before acting — the
previous session's own metrics were wrong three times, so treat this list as
leads with evidence, not settled fact.**

**A. THE BASELINE IS NOT B&H — this is the big one.** `stocks_repaired_20260725_c2`'s
`key_baseline` is the *live config trading*, not stage 0: VT_LONG **497 trades,
capture −1.028**; NVDA_LONG 215 trades, capture −0.082. A stage-0 floor is 1 trade,
~100% TIM, return == B&H. Every cell in that campaign is measured against a losing
baseline. **Re-baseline to the true B&H floor before filling another cell.**
Confidence: high — the trade counts prove it directly.

**B. The campaign could only ever cover 2 keys.**
`data/matrix_npz/stocks_repaired_20260725_c2/` contained exactly `MU.npz` and
`VT.npz`. The daemon reads NPZs only from there, so every other key fails with
`indicator NPZ does not exist`. That is why the repaired campaign shows 4 keys
against the older campaign's 199. Pin an NPZ per key (copy from
`backtest_v8/indicators/`, `chmod 444`). Confidence: high — verified by listing.

**C. 91.9% of repaired-campaign cells are inert** (vs 20.3% in the older one).
Baseline was fixed; switch discrimination was not. Ties directly to the 1,339
`DEGENERATE` rows. Confidence: high — from `param_cells`.

**D. Short-side stage-0 seeding fails systematically.** Every SHORT pilot dies with
`no intended-side trade/MTM record` (TTD_SHORT, ACN_SHORT — different symbols, same
failure). The seed must bypass entry gates, fill, and be verified by
`entry_reason=V8_LADDER_INITIAL_BH_SEED` (§15.1). Until fixed, **no short can be
baselined at all**, which alone would cap the system well below its potential.
Confidence: high — reproduced on two symbols.

**E. The "exact replay" never calls the real engine.**
`tools/v8_research_ladder_adapter.py` docstring says it replays "through
`backtest_v8_engine`". It contains no import of and no subprocess call to it, and
neither do `vec_band_ladder_walkforward.py`, `run_mu_ladder_*.py` or
`v8_research_short_guard_adapter.py`. So a passing "exact replay" proves Tier-1
self-consistency, **not** live-path parity. Confidence: high — verified by grep.
(That file is now in `old/discarded_band_ladder_lane_20260728/`.)

**F. `strategy_bh_multiple` compares a levered leg to an unlevered benchmark.**
The ladder divides strategy P&L by a $2,000 unit while holding up to $16,000 (the
cap clips entry fills only, never appreciation — peak mark-to-market measured
$57,425 on a $10,000 ledger), and the B&H leg is always 1.0× $2,000. Normalising
both to capital actually deployed gave MU_LONG **1.19×**, not 6.55×.
**This means some of the headline 2–20× figures may be leverage, not alpha — check
this before trusting any of them.** Confidence: medium-high on the arithmetic;
**verify independently**, because the previous session drew two wrong conclusions
from this same metric before landing here.

**G. Four different B&H bases are in circulation.** `bh_pct` in
`stocks_repaired_*` is account-based (instrument ÷ 5, because $2,000 is deployed
on a $10,000 ledger) — MU 133.06 vs a true 665.35. `stocks_baseline_v2_s4h`,
stage-0 Tier-2, and the ladder folds each use a different base. Ratios *within* a
campaign are valid; comparing `bh_pct` *across* campaigns is meaningless. Add an
explicit `bh_base` column; never silently rescale stored rows. Confidence: high —
the 5× ratio reproduces exactly on two keys.

**H. HAO was kept as a pilot though it is structurally unusable** — 3.5 months of
history, ~75 daily bars, an unadjusted 137.2% reverse split (2026-06-08), and
`lrL_pct_b_4h`/`_D` that cannot be computed (4h needs 400 bars, D needs 200). It
can never satisfy a D/4h contract. Half the original pilot set was therefore dead.

**I. `lrL_pct_b` is computed only for 1h/4h/D** (`backtest_v8_precompute.py:1658`),
which is why no lower timeframe could ever be principal — a data limitation, not a
design choice. 15m/5m were added 2026-07-28; `--tfs` accepts any three slots.
`bb_pct_b` exists on all 7 TFs but is **not** a substitute (corr 0.45–0.60).

**J. Artifact directories collide.** Two runs of the same symbol/side in the same
second shared a directory; the second died on `FileExistsError` and would have
overwritten the first's `result.json`. Fixed 2026-07-28.

---

## 2. WHICH MATRIX — this cost two days, get it right

| campaign | exports to | baseline | keys | inert |
|---|---|---|---:|---:|
| `stocks_baseline_v2_s4h` | `SWITCH_MATRIX_TRB_ENGINE_HIST_STOCKS_BASELINE_V2_S4H.csv.gz` | **broken** — MU 19 trades / 0.13% TIM, VT 0 trades | 199 | 20.3% |
| `stocks_repaired_20260725_c2` | `SWITCH_MATRIX_TRB.csv.gz` | trades, but is the live config, not stage 0 | **4** | **91.9%** |

**Both are inadequate.** The HIST file is broad but built on a baseline that never
trades (so its grid is inert by construction, §13.5). The repaired file has a
trading baseline but only 4 keys and 92% inert cells.

**The job**: fill `stocks_repaired_20260725_c2` across all 129 keys **after**
re-baselining it to the true B&H floor, and drive the 92% inert down by forcing
every path to a different outcome.

Run `python3 tools/matrix_guard.py` and `./matrix_progress.sh` before and during
any matrix work. The guard names the canonical file and three decoys that have
each been mistaken for it. `matrix_progress.sh` reads the LIVE store
(`param_cells`), because the CSV is a periodic export and lags.

Matrix scale: 3,654 switch×value rows · 803 switches · 129 keys · **471,366 cells,
11.32% filled**. 1,842 rows `NOT_ENGINE_TESTED`, 1,339 `DEGENERATE`.

### Runner
```bash
PSC_CAMPAIGN=stocks_repaired_20260725_c2 python tools/param_matrix_daemon.py \
  --tag <tag> --only <SYM> --side <SIDE> --all-tiers --safe-contract --min-avail 5000
```
On S1 (`ssh s1-int`, cwd `/home/niels/binance-sandbox`). NPZs must be pinned in
`data/matrix_npz/stocks_repaired_20260725_c2/` (copy from `backtest_v8/indicators/`,
`chmod 444`) or the daemon dies with `indicator NPZ does not exist`.

---

## 3. PILOTS

`MU_LONG`, `NVDA_LONG`, `VT_LONG`, `TTD_SHORT`, `ACN_SHORT`. HAO retired as a pilot
(3.5 months history, ~75 daily bars, unadjusted 137.2% reverse split, `lrL_pct_b_4h/_D`
structurally uncomputable).

**BLOCKER — both SHORT pilots fail identically:**
```
[matrix-contract-fail] __BASELINE_SAFE__SHORT/TTD_SHORT: no intended-side trade/MTM record
[matrix-contract-fail] STOP_PACK__RIDE_REGIME_L/ACN_SHORT: no intended-side trade/MTM record
```
This is a **systematic short-side stage-0 seeding failure**, not per-symbol. The
seed must bypass entry gates, fill, and be verified by
`entry_reason=V8_LADDER_INITIAL_BH_SEED` — not by `closes=0` (§15.1). Standing user
mandate: **trades=0 is ALWAYS a bug.** Fix this before anything else on shorts.
Alternates if needed: LAC_SHORT, ADBE_SHORT, OLED_SHORT, STZ_SHORT (all
contract-valid, 2.33yr).

---

## 4. LIVE — done and outstanding

**DONE 2026-07-28** (user unlocked `config_tradier.py`):
`LS_RATIO_ENFORCE_TRADIER: False -> True` (`config_tradier.py:1126`, backup
`backups/before_hao_short_stack_fix_202607282230.py`). With the L/S band off at
`tradier_manage.py:4423`, `RATIO_BOOST_S` could spam shorts at 25:1 — HAO_SHORT
stacked **8 OPENs / ~$20k with ZERO closes**. The 2026-07-11 note said to disable
only until shorts could enter, then re-enable; shorts demonstrably enter now.
**NOT yet deployed or restarted — verify before relying on it.**

**STILL OUTSTANDING (needs `unlock tradier_manage.py`):**
1. `BROKER_PREFLIGHT_MAX_SAME_SIDE_QTY = 50.0` is a **share** count
   (`config_tradier.py:2568`). Meaningless for penny stocks. Must be notional-based.
2. **No penny-stock SHORT guard** at all.
3. **Five exit paths run despite config saying `False`** —
   `EXIT_IBS_EXHAUSTION_ENABLED`, `EXIT_SENTIMENT_ENABLED`, `EXIT_K5M_BOUNCE_ENABLED`,
   `EXIT_STRUCT_BREAK_5M_ENABLED`, `EXIT_BOUNCE_TOP_ENABLED`. The flags appear only
   in a fingerprint list and a comment block claiming "all gated by config flags" —
   **they are not gates**. They fire on `gain >= 0.01%` = premature profit-taking,
   the documented churn culprit (§9.1). Strong candidate for the daily bleed.
4. **Live↔backtest parity is broken** for the band-ladder path: live
   (`tradier_manage.py:3457-3477`) triggers on a 5m rebound off a running low +
   MTF-arrow score, sizing on ONE timeframe. Confirm parity before anything goes live.

---

## 5. HOUSEKEEPING

- **Kill the 5-day-old rate-limited agent** "Fill SWITCH_MATRIX_TRB with complete
  MU_LONG backtest data". It has been dead since ~2026-07-23 and still shows
  "Needs input". Its MU numbers are not to be trusted.
- Agents are not visible to the user while running. **Prefer doing the work in
  the main session**, or write progress to a file the user can open. If you must
  delegate, have the agent write status to disk every few minutes.
- **Nothing is live from any backtest.** No config was promoted.
- `symbol_heartbeat_guard` (launchd, 15s) protects `symbols.json` and
  `symbols_tradier.json` — the latter was being deleted ~12×/week by an
  unidentified process. Forensics land in `data/symbol_guard_incidents.jsonl`.

## 6. REPORTING RULES (honesty, not lane-specific)

- Rank on **alpha in percentage points**, never a ratio — a ratio explodes when
  B&H → 0 and once ranked a **negative-alpha** key #2. Require |B&H| ≥ 20pp.
- Never compare a **levered** leg to an **unlevered** benchmark.
- Publish `avg_deployed_usd` and `implied_leverage_x` beside any return.
- Dynamic quantity = size varying with conditions. A constant larger than the
  benchmark unit is **not** dynamic sizing.
- Every digest sources from the matrix + `data/reports/`. Nothing else.
