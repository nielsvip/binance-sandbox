# START HERE — next session. Restored Codex checkpoint, 2026-07-28

Paste this whole file into a fresh session. Written to be read cold.

---

## 0. WHERE YOU ARE

You are continuing the **Codex** line of work, branch
`origin/backtest-recovery-20260725`, last Codex commit **`ad0257ea`**
("Surface HAO short phase 3 in progress digest"). That work is intact and is the
foundation. Build on it.

**The immediately preceding session (Opus) went wrong and you must not repeat it.**
Given "analyse what Codex did and take it from there", it instead re-ran Codex's
ladder tool as its own mass campaign across 383 keys, corrected its own metric
three times, reported on the wrong artifact twice, and briefly moved Codex's
working tools into `old/`. All restored. **Its conclusions are void; Codex's are not.**

### Valid vs void — the distinction matters

| | verdict |
|---|---|
| Codex campaign artifacts: `vec_research/parent_clock_v3_20260727/`, `MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260727.json`, `HAO_SHORT_*_RECEIPT_20260727.json`, `hao_recovery_20260727T0010Z/` | **VALID — use these** |
| Codex tools: `tools/run_mu_ladder_pareto_holdout.py`, `tools/vec_band_ladder_walkforward.py`, `tools/v8_research_ladder_adapter.py`, `tools/switch_matrix_digest.py` | **VALID — restored to `tools/`** |
| The Opus mass-grind: 383-key `band_ladder_walkforward_*` sweeps from 2026-07-28, `data/handle_priority/*.json`, `HONEST_RANK_*`, `LADDER_SETTINGS_AND_RESULTS_*` | **VOID — ignore** |
| Bible §15.39–§15.44 | Opus's own errors. Warnings only, not findings. |

---

## 1. DONE THIS SESSION — MU is now promotion-eligible

**TIM floor lowered 70.0 → 65.0** (`tools/run_mu_ladder_pareto_holdout.py:49`,
backup `backups/before_tim_floor_65_202607282340.py`). Field
`weighted_tim_70_80` renamed `weighted_tim_in_band` so the name stops lying.

| MU_LONG DAILY_DEEP holdout | |
|---|---|
| return | **+1,170.7005%** |
| B&H | +205.2519% |
| **multiple** | **5.704×** |
| weighted TIM | 68.6153% — **inside the new 65–80 band** |
| exact v3 replay | 33/33 actions, 0 refusals, 0 future-HTF sources, 0 clamps |
| max DD / min equity | 35.8534% / $7,912.35 |

It previously failed by **1.38 points**. Codex correctly refused to move the gate
after seeing the result; the justification for moving it now is independent — the
user set exposure targets of **50–70% weighted TIM for top-tier keys**, and 68.62%
sits inside that band, so the old 70.0 floor contradicted the user's own target.
That reasoning is recorded in the code.

**Also fixed:** Codex's last command failed because the two receipt JSONs were on
the Mac but not S1, so the new digest sections would render `NO RECEIPT`. Both are
now on S1 at `data/reports/vec_research/`.

---

## 2. DO THIS NEXT — the user's standing instruction

> *"lower time in market from 70 to 65 so we can make MU live and hopefully have the
> other 3 live as well by market open at 13:30 UTC. Continue with all the tickers we
> have been actively trading in the last month, taking less time per symbol as the
> faults have hopefully been ironed out, and we can start skipping (or only retry in
> 1 out of 10) the handles that so far have not produced positive results. Also keep
> spawning agents to iron out the cracks so we can start backtesting way faster."*

1. **Floor lowered — done.** Re-run the other three pilots against the 65 band and
   see which now qualify.
2. **Fleet the actively-traded tickers.** Already extracted:
   `data/handle_priority/traded_universe_20260727.json` — **83 keys traded in 60d,
   17 in 7d** (from `data/history/{trb,trc}/*.jsonl`).
3. **Skip / 1-in-10 the unproductive handles.** A frozen stratified sample frame
   exists: `data/handle_priority/sample_frame_20260727.json` — 129 keys, 16 strata
   (side × trend sign × volatility quartile), proportional allocation, pilots
   force-included. **Do not re-draw it** — that turns sampling into cherry-picking.
4. **Keep spawning agents.** Caveat learned the hard way: **agents are invisible to
   the user while running.** Have every agent write status to a file the user can
   open, or do the work in the main session.

### Test order (USER) — subtractive, never additive

| stage | config | gate |
|---|---|---|
| **0** | dynamic-quantity ladder ON, **every exit OFF** | ~100% TIM, return **is** B&H. **Hard gate.** |
| **1** | exits back on **one at a time** | must **raise gain** vs stage 0 |
| **2** | entry paths, one at a time | same |
| **3** | filters/gates, one at a time | same |

**Green = higher gain than the stage it was added to.**

**USER's physics, treat as a diagnostic:** *"It is impossible to do worse than B&H
because just entering and doing nothing is B&H."* Correct. Any baseline below B&H
is a **broken floor**, not a bad strategy. Fix the floor before measuring anything.

If stage 0 does not reach B&H, an exit is not behind a switch. Three shapes, none
inferable from the name: plain `X_ENABLED`; boolean **without** `_ENABLED`
(`STRUCTURAL_RANGE_SHIFT_EXIT` alone produced 100% of 1,310 closes); string TF
knobs disabled with `"None"` (`LONG_STRUCT_EXIT_TF='D'` → 69 of 149 closes). Read
`_cfg_bools()` / `_cfg_strs()`. Bible §13.7, §13.8.

---

## 3. DEFECTS IN THE CODEX SYSTEM — verify each, they cap the results

Leads with evidence, not settled fact. The prior session's metrics were wrong three
times; check before acting.

**A. The repaired-campaign baseline is NOT B&H — biggest one.**
`stocks_repaired_20260725_c2` `key_baseline` is the *live config trading*:
VT_LONG **497 trades, capture −1.028**; NVDA_LONG 215 trades, −0.082. Stage 0 is
1 trade, ~100% TIM, return == B&H. Every cell measured against that is meaningless.

**B. That campaign could only ever cover 2 keys.**
`data/matrix_npz/stocks_repaired_20260725_c2/` held exactly `MU.npz` + `VT.npz`;
the daemon reads NPZs only from there, everything else dies with `indicator NPZ
does not exist`. NVDA/TTD/ACN/LAC pinned 2026-07-28. Pin one per key
(copy from `backtest_v8/indicators/`, `chmod 444`).

**C. 91.9% of its cells are inert** (vs 20.3% in `stocks_baseline_v2_s4h`).
Baseline fixed, switch discrimination not. Ties to 1,339 `DEGENERATE` rows.

**D. Short-side stage-0 seeding fails systematically.** Every SHORT pilot dies with
`no intended-side trade/MTM record` (TTD_SHORT, ACN_SHORT — same failure, different
symbols). Seed must bypass entry gates, fill, and be verified by
`entry_reason=V8_LADDER_INITIAL_BH_SEED` (§15.1). **No short can be baselined until
fixed.** Standing mandate: trades=0 is ALWAYS a bug.

**E. "Exact replay" never calls the real engine.** `v8_research_ladder_adapter.py`
docstring claims it replays "through `backtest_v8_engine`"; it has no import of and
no subprocess call to it. So exact-replay PASS = Tier-1 self-consistency, **not**
live-path parity. Verified by grep.

**F. Check before going live: `strategy_bh_multiple` compares a levered leg to an
unlevered benchmark.** Strategy P&L ÷ $2,000 unit while holding up to $16,000 (cap
clips entry fills only, never appreciation — peak MTM $57,425 on a $10,000 ledger);
B&H is always 1.0× $2,000. Normalising both to deployed capital gave MU **1.19×**,
not 6.55×. Does not block MU — but know which number you are trading on.

**G. Four B&H bases in circulation.** `bh_pct` in `stocks_repaired_*` is
account-based (instrument ÷ 5): MU 133.06 vs true 665.35. Ratios *within* a campaign
are valid; across campaigns meaningless. Add a `bh_base` column; never rescale
stored rows.

**H. HAO is structurally unusable as a pilot** — 3.5 months history, ~75 daily bars,
unadjusted 137.2% reverse split (2026-06-08), `lrL_pct_b_4h/_D` uncomputable.
Replaced by TTD_SHORT + ACN_SHORT (68 valid shorts exist; alternates LAC, ADBE,
OLED, STZ).

**I. `lrL_pct_b` computed only for 1h/4h/D** (`backtest_v8_precompute.py:1658`) —
why no lower TF could be principal. 15m/5m added 2026-07-28; `--tfs` takes any three
slots. `bb_pct_b` exists on all 7 TFs but is **not** a substitute (corr 0.45–0.60).
**Not a priority** per user — 1h and above first.

**J. Artifact dirs collided** — same-second runs shared a directory, second died on
`FileExistsError` and would have overwritten the first's `result.json`. Fixed.

---

## 4. LIVE

**DONE:** `LS_RATIO_ENFORCE_TRADIER: False → True` (`config_tradier.py:1126`,
backup `backups/before_hao_short_stack_fix_202607282230.py`). With the L/S band off
at `tradier_manage.py:4423`, `RATIO_BOOST_S` spammed shorts at 25:1 — HAO_SHORT
stacked **8 OPENs / ~$20k with ZERO closes**. Its own 2026-07-11 note said re-enable
once shorts could enter; they do. **Not deployed or restarted — verify.**

**NEEDS `unlock tradier_manage.py`:**
1. `BROKER_PREFLIGHT_MAX_SAME_SIDE_QTY = 50.0` is a **share** count — meaningless at
   $0.16. Must be notional.
2. No penny-stock SHORT guard exists.
3. **Five exit paths run despite config saying `False`** —
   `EXIT_IBS_EXHAUSTION_ENABLED`, `EXIT_SENTIMENT_ENABLED`, `EXIT_K5M_BOUNCE_ENABLED`,
   `EXIT_STRUCT_BREAK_5M_ENABLED`, `EXIT_BOUNCE_TOP_ENABLED`. The flags appear only in
   a fingerprint list and a comment block claiming "all gated by config flags" — they
   are **not gates**. They fire at `gain >= 0.01%` = premature profit-taking, the
   documented churn culprit (§9.1). Prime suspect for the daily bleed.
4. **Live↔backtest parity break**: live (`tradier_manage.py:3457-3477`) triggers on a
   5m rebound off a running low + MTF-arrow score, sizing on ONE timeframe; the
   backtest uses completed-HTF `wt_cross_bull`/structure with three independent TF
   ladders. Prove parity before live.

---

## 5. HOUSEKEEPING

- **Kill the 5-day-old rate-limited agent** "Fill SWITCH_MATRIX_TRB with complete
  MU_LONG backtest data" — dead since ~2026-07-23, still shows "Needs input", its MU
  numbers are untrustworthy.
- `python3 tools/matrix_guard.py` and `./matrix_progress.sh` — canonical-artifact
  check and live progress (reads `param_cells`; the CSV export lags).
- `symbol_heartbeat_guard` (launchd, 15s) protects `symbols.json` /
  `symbols_tradier.json` — the latter was deleted ~12×/week by an unidentified
  process; forensics land in `data/symbol_guard_incidents.jsonl`.
- Nothing from any backtest is live. No config promoted.

## 6. REPORTING RULES

- Rank on **alpha in percentage points**, never a ratio — a ratio explodes as B&H → 0
  and once ranked a **negative-alpha** key #2. Require |B&H| ≥ 20pp for any multiple.
- Never compare a levered leg to an unlevered benchmark.
- Publish `avg_deployed_usd` and `implied_leverage_x` beside any return.
- Dynamic quantity = size varying with conditions; a constant above the benchmark
  unit is not dynamic sizing.
- Digests source from the matrix + `data/reports/`. Nothing else.
