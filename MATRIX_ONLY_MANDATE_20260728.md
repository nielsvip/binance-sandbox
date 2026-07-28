# MATRIX-ONLY MANDATE — 2026-07-28 (USER)

**One lane. `SWITCH_MATRIX_TRB` is the system. Everything else is discarded.**

## 0. The decision

USER 2026-07-28: *"All present and future testing and reporting in the mail
digests has to be based on the new 1600+ param SWITCH_MATRIX_TRB and the other
data in the data/reports folder. The rest is discarded as fake numbers and false
data and useless information."*

The band-ladder walk-forward campaign (`tools/vec_band_ladder_walkforward.py`,
`ladder_grind_daemon.py`, `data/reports/vec_research/band_ladder_walkforward_*`)
was run as a **standalone lane outside the matrix**. It is not switch-matrix
evidence and must not be reported as system results. Its numbers were also
wrong three separate ways in one session (§15.41 levered-vs-unlevered, §15.42
the flattened 1x test, §15.44 the ratio singularity that ranked a
negative-alpha key second).

**The stdev/band ladder itself is NOT discarded — it becomes a path inside the
matrix.** Sizing by band depth is the mechanism USER wants; it just has to be
registered, swept and reported like every other path.

## 1. Measured state (2026-07-28 20:2x UTC)

| store | value |
|---|---|
| `param_registry` | **1,799 params** — this is the "1600+" |
| `param_cells` total | 92,681 rows, 711 distinct params ever tested |
| **ENGINE tier** | 8,792 rows · 603 params · 199 keys · last write **2026-07-28T20:24:31Z (LIVE)** |
| VEC tier | 83,604 rows · 158 params · 226 keys · last write 2026-07-23 (STALE) |
| untested | **1,799 − 603 = 1,196 registry params have no ENGINE cell (66%)** |

The ENGINE matrix lane is alive and writing. The ladder grind was stopped
2026-07-28 (daemon killed, `watchdog_ladder_grind` cron removed) so it stops
competing for CPU.

## 2. Pilot keys — 3 LONG + 2 SHORT, FULL matrix each

HAO is **retired as a pilot**. It cannot be fixed: 3.5 months of history
(2026-04-09 → 2026-07-27), ~75 daily bars, an unadjusted 137.2% reverse split on
2026-06-08, and `lrL_pct_b_4h` / `lrL_pct_b_D` structurally uncomputable
(4h needs 400 bars, D needs 200). Adding 15m/5m channels did not and cannot fix a
D/4h contract requirement.

**Replacement SHORT pilots** (from `symbols_trb_short`, contract-valid, 2.33yr):

| key | bars | short-side b&h | why |
|---|---:|---:|---|
| **TTD_SHORT** | 93,922 | +80.44% | largest short-side move with deep data |
| **ACN_SHORT** | 60,206 | +56.09% | large cap, independent sector from TTD |

Alternates if either disappoints: `LAC_SHORT` (98,047 bars, +58.03%),
`ADBE_SHORT`, `OLED_SHORT`, `STZ_SHORT`. 68 valid short candidates exist in total.

**LONG pilots**: `MU_LONG`, `NVDA_LONG`, plus one more to be chosen on the same
criteria (contract-valid, ≥2yr, meaningful b&h).

## 3. Order of work

### 3.1 Register every obsolete-system path into the matrix (BLOCKING)
USER: *"make sure ALL paths from the obsolete system are present in the correct
manner in the switch matrix so we can send the old matrix to an /old folder so
nobody can make that same mistake AGAIN."*

1. Enumerate every path the ladder campaign exercised: band-depth sizing per
   timeframe slot, trigger family (`green` / `structure` / `union`), accumulation
   semantics (`target` / `add`), interpolation (`linear` / `center_plateau`),
   stoch gate, Donchian exit horizon, reclaim obligation, capacity/base-unit.
2. For each, confirm a `param_registry` entry exists with the right type and
   sweep values. Add what is missing.
3. Confirm the ENGINE engine actually *reads* each one (a registry entry whose
   value never changes the trade fingerprint is a dead cell — §13.5).
4. Only when 1–3 pass: move the superseded matrix artifacts to `old/`.

### 3.2 Fill the FULL matrix for the 5 pilots
All 1,799 registry params (or the pruned work-list) at ENGINE tier, per pilot
key. **ENGINE only** — a Tier-1 vec screen does not fill a cell (§13.5).

### 3.3 Filter, then fan out
Once 5 pilots have full matrix coverage: classify every path by whether it
produces positive alpha. Paths that never do are demoted to **1/5 or 1/10 key
sampling** from `symbols_trb_long/short`; productive paths are tested on every
key. Promotion/demotion rules and the fixed stratified sample frame are already
specified in `BACKTEST_BIBLE.md` §15.39 and
`data/handle_priority/sample_frame_20260727.json`.

### 3.4 Parity confirmation (BLOCKING before any live change)
The band ladder is **known non-parity today**: live
(`tradier_manage.py:3457-3477`) triggers on a 5m price rebound off a running low
plus an MTF-arrow score and sizes on ONE timeframe, while the backtest triggers
on completed-HTF `wt_cross_bull`/structure and runs three independent TF ladders.
Integrating the stdev ladder means making live and matrix agree, then proving it.
Several "new" subpaths tested in the sandbox may not be wired live at all — that
has to be confirmed path by path before anything is switched on.

### 3.5 Timeframes
Integrate the stdev ladder **completely for 1h and above** first. `lrL_pct_b_15m`
and `lrL_pct_b_5m` now exist (`backtest_v8_precompute.py:1658`, added
2026-07-28) and `--tfs` accepts any three slots, so **15m and 5m are ready to add
later — explicitly NOT a priority now** (recorded here per USER instruction).

## 4. Objective

**Best gain vs buy-and-hold for every tradeable symbol, using dynamic quantity.**

Dynamic quantity means size varying with conditions — band depth and every other
condition that legitimately defines trade size. It does **not** mean a constant
larger than the benchmark unit; USER 2026-07-28: *"not just higher than the $2k
from b&h that is plain cheating and useless."*

**Reporting rules that survive from the discarded lane** (they are about honesty,
not about the ladder):
- Rank on **alpha in percentage points per dollar deployed**, never on a ratio —
  a ratio explodes when b&h approaches zero and ranked a negative-alpha key #2
  (§15.44). Require |b&h| ≥ 20pp before quoting any multiple.
- Never quote a levered leg against an unlevered benchmark (§15.41).
- Publish `avg_deployed_usd` and `implied_leverage_x` beside any return.

## 5. Digests

Every mail digest (Morning Briefing, Evening Recap, Results Digest, Trader
Research, Paper Options) must source from `SWITCH_MATRIX_TRB` and
`data/reports/`. Any digest section still reading a discarded store must be
cut or explicitly labelled dead. Audit findings for all five are in
`EMAIL_DIGEST_REPAIR_20260728.md` (in progress) — several already proven to be
serving 12–52 day old data from producers that no longer run.

## 6. Status

| item | state |
|---|---|
| ladder grind daemon + watchdog | **STOPPED** 2026-07-28 |
| HAO retired, TTD_SHORT + ACN_SHORT selected | **DONE** |
| register obsolete paths into matrix | NOT STARTED |
| full matrix for 5 pilots | NOT STARTED |
| parity confirmation | NOT STARTED |
| move old matrix to `old/` | BLOCKED on registration |
| anything live | **NOTHING LIVE** |
