# AGENT BRIEF — Get the sandbox trading, fill the MU matrix, never drop below b&h, land the ladder

**Owner:** Niels · **Written:** 2026-07-23 · **Box:** S1 (`s1-int`, `/home/niels/binance-sandbox`) ONLY
**Money at risk if you get this wrong:** $70k live stocks (trb/trc). Read §0 before touching anything.

---

## 0. HARD SAFETY RULES — violating any of these is worse than failing the task

1. **NEVER edit `tradier_manage.py` or `ez_manage.py`.** All experimental behavior goes in `config_tradier.py`, gated behind `V8_SWEEP_MODE`. A previous session wrote four experimental blocks into `tradier_manage.evaluate_stop()`, referenced `account_key`/`position_side` (undefined in that scope), and produced a `NameError` on every open position — **a total stock exit blackout** (R1/R2/PPL/EOD all dead) while entries kept firing. `py_compile` passed. Caught by audit an hour before market open.
2. **`py_compile` does NOT catch undefined globals.** After ANY edit, run a `symtable` sweep on every function you touched. This exact bug class has now bitten twice in two days.
3. **NEVER run `push.py`** — it pkills the Mac live stack (STOP-MAC-FIRST). Sync with `scp`/`rsync` to S1 only.
4. **NEVER run sweeps on the Mac.** Mac = live trading. S1 = all backtests. S2 is dead — never SSH `s2-int`.
5. **Market opens 13:30 UTC.** Anything that could alter live behavior must be verified OFF before then.
6. **No annualization, no `sqrt(252)`.** `pool_sharpe = mean(trade_returns)/std(trade_returns)`. Route every emitted Sharpe through `metrics_guard`. Never write a bare "Sharpe X.X".

---

## 1. WHAT IS ALREADY FIXED (do not redo — verify and build on)

### 1a. The all-entries blocker — FIXED 2026-07-23
`config_tradier.py` had a stack of entry gates that compounded to near-zero opens:
```
TRADIER_ENTRY_SCORE_THRESHOLD  = 30    GOLDEN_RULE_MIN_IND            = 5
GOLDEN_RULE_REQUIRE_ACTIVATION = True  HTF_ALIGN_REQUIRED_TRADIER     = 2
GR_HTF_DIRECT_ENTRY_SCORE_MIN  = 12.0  LIVE_ENTRY_ENGINE_MIN_SCORE    = 0.5
```
**This is why the matrix showed `trades=0` and sub-floor counts, and why unrelated knobs produced identical results — the knobs had no trades to bite on.**

Fix: `TradierConfig._apply_sweep_entry_unblock()` (called from `__post_init__`) zeroes all of them **only when `V8_SWEEP_MODE=1`**. Live never sets that var, so live is byte-identical. Verified both directions.

**⚠️ NOTHING SETS `V8_SWEEP_MODE=1` ANYWHERE IN THE REPO.** You MUST export it in every backtest invocation:
```bash
V8_SWEEP_MODE=1 python backtest_v8_engine.py --mode tradier --account trb --symbols MU --start 2024-04-01
```
Confirm the line `[SWEEP_ENTRY_UNBLOCK] entry gates opened` appears in the log. **No line = gates still shut = your run is garbage.**
Escape hatch `V8_KEEP_ENTRY_GATES=1` restores gated defaults inside a sweep (for A/B).

### 1b. trades=0 is ALWAYS a bug — guard landed
USER, verbatim: *"EVERY 0 TRADE FIELD IS A BUG BECAUSE WE START WITH B&H ALWAYS IN A TRADE."*
The floor is buy-and-hold: at bar 0 the only condition to be in the market is `current_price > 0`. A config that blocks every *new* entry still holds the b&h position, MtM'd at the final bar. **Minimum trades = 1, always.** `trades=0` means the run never opened ⇒ reports `0.00%` where it should report b&h ⇒ **FALSE FLOOR** (looks like the safest cell in the matrix; is the only one that never traded).

`tools/param_results_store.py` now raises `ZeroTradeBug` from `insert_cell()` and `upsert_baseline()`. Override only with `V8_ALLOW_ZERO_TRADE=1`, and only if you can justify it in writing.
**Never classify a zero-trade cell as a result.** I did this twice and was wrong twice.

### 1c. USE 15m, NOT 5m — data coverage
Stocks nominally run a 5m base TF, but 5m klines are ~4 months deep and some are missing entirely:
```
MU_5m   2026-03-23+ (4mo)   MU_15m   2024-03-26 → 2026-07-22 (2.3yr)  ← USE THIS
NVDA_5m 2026-03-23+         NVDA_15m 2024-03-26 → 2026-07-22
VT_5m   2024-07-11+ (2yr)   VT_15m   708 bars, 2026-06-30+  ← why VT baseline = 0 trades
MNTS_5m MISSING             DELL_5m  MISSING
```
Tradier's timesales won't backfill intraday history (probe returned only 553 bars from "400 days back"). **Run the matrix on 15m.** Start date `2024-04-01`. Symbols whose 15m is also short (VT, MNTS, DELL, CIEN) cannot produce a 2-year result — say so explicitly rather than emitting a short-window number as if it were comparable.

---

## 2. YOUR TASKS, IN ORDER

### TASK 1 — Get the sandbox trading again (BLOCKING; nothing else counts until this is green)
```bash
ssh s1-int
cd /home/niels/binance-sandbox
V8_SWEEP_MODE=1 /home/niels/.conda/envs/binance_env/bin/python backtest_v8_engine.py \
    --mode tradier --account trb --symbols MU --start 2024-04-01 2>&1 | tee ~/logs/mu_base.log
```
Acceptance, ALL of:
- `[SWEEP_ENTRY_UNBLOCK]` present in log
- `V8_RESULT` line emitted with **`trades >= 1`**
- `gain_pct` is a real number, not `0.00`

If trades is still 0, **do not proceed and do not paper over it.** Trace *which* gate rejected the open — instrument the entry path in the engine (read-only logging) or bisect by flipping gates one at a time via env. The answer is a specific gate name, not a guess.

**STATE AT HANDOFF (2026-07-23 ~06:00 UTC) — verify, don't assume:**
A run is IN FLIGHT: `~/logs/mu_unblocked.log`, MU / trb / start 2024-04-01, launched with `V8_SWEEP_MODE=1`.
- ✅ **It is trading: 99 closes at step 70800/113139 (~62%).** Compare to the old campaign baseline of 19 trades. So the sandbox is no longer silent.
- ⚠️ **UNRESOLVED:** `[SWEEP_ENTRY_UNBLOCK]` does **not** appear in that log, yet `/proc/<pid>/environ` confirms `V8_SWEEP_MODE=1` is set and `config_tradier.py:2854` on S1 contains the method. So either (a) the banner is being swallowed (the engine re-execs itself for `PYTHONHASHSEED` at `backtest_v8_engine.py:50`, and config may be instantiated in a worker whose stdout isn't captured), or (b) the unblock never ran and those 99 closes are the *gated* baseline.
  **These two possibilities give completely different numbers. Resolve it before recording a single cell.** Cheapest check: run the same command twice, once with `V8_KEEP_ENTRY_GATES=1`, and diff `trades` + `trades_fingerprint`. Identical fingerprint ⇒ the unblock is not taking effect ⇒ fix that first.

### TASK 2 — Establish the MU b&h floor (the number every cell must beat)
Compute MU_LONG buy-and-hold over the exact same window (2024-04-01 → last 15m bar), same bars the engine used. Record it in the campaign store as the baseline. **Every subsequent cell is judged against this number.**
Prior measured reference points (Tier-2, faithful): default config **3.07%** vs b&h **426%**; wt_5m cross **0.27× b&h**; band-top harvest **0.50×**; band arrow **1.4%/33 trades**. **Nothing has beaten b&h yet.** That is the whole problem.

### TASK 3 — Fill SWITCH_MATRIX_TRB for MU_LONG
Target: every field populated with a real 2-year Tier-2 number, ~3000 distinct results.
- Store: `tools/param_results_store.py` → `data/param_results_stocks.db` on S1 (authority; Mac copy is a 0-byte stub).
- **Tier discipline:** never diff a Tier-1 `VEC` cell against a Tier-2 `ENGINE` baseline. The `tier` column exists precisely because 16,880 cross-tier deltas were once written as if comparable. Same-tier baselines only.
- Export: `tools/export_switch_matrix_xls.py` → `data/reports/SWITCH_MATRIX_TRB.xlsx`.
- Layout the user asked for: main True/False switch columns, sub-setting columns beneath them, readable as a diagram, entry and exit sections.
- **If two settings of a knob produce an identical `trades_fingerprint`, the knob is INERT — flag it, don't record it as a finding.** (`trades_fingerprint` = md5 over the sorted trade list.)

### TASK 4 — Keep every recorded gain ABOVE b&h
USER, verbatim: *"DO NOT STUPIDLY GO BACK TO <B&H EVER EVER EVER EVER"* and *"b&h is the FLOOR for every key."*
Structural reasoning that makes this achievable: if you are **always in the market while price is above your last exit price**, you cannot structurally lose to b&h. Note the direction carefully — USER corrected this twice:

> **"EVERY POSITION MUST BE OPEN WHEN PRICE > EXIT PRICE — it is an OPEN rule NOT A FILTER ON ENTRIES."**

Seed the initial exit price at **0.0** so the first bar always opens. Then: exit on the configured signal, and **re-open the moment price exceeds the exit price**. Do not implement this as a veto on entries — that inverts it and was already built wrong once.

### TASK 5 — Land the ladder (band/grey-zone position sizing)
Multiplier varies by where price sits in the regression channel, per TF. USER spec, verbatim intent:
```
D  : 10x at center and below  (also test 10x at low)  →  3x at or above top band
4h :  6x                                              →  2x
1h :  4x                                              →  1x
below lower band: 0x
```
It is a **continuous ladder, not a threshold gate** — a previous attempt built it as a gate and the user called it *"structurally wRONG"*.
Combine with: **buy every green arrow on 1h/4h/D, sell every red arrow on D/4h** (1h sell needs testing; ignore 15m for now — that's for the BB/wt_dc work later). Expected ≈70% time in market and ≥5× b&h.
Data note: regression bands `lrL_pct_b_{tf}` / `lrL_slope_{tf}` exist for **1h/4h/D only, no 15m**. `high_{tf}`/`low_{tf}` and `_prev` exist on all TFs.
Vary multipliers across **more than one ticker** until the best per-TF set is established; use short windows first so a steady baseline exists within ~90 min. Add the ladder params as matrix columns.

### TASK 6 — Ladder into live, DISABLED
USER: *"the ladder work needs to be in live trading as soon as it has a decent gain — but disabled until proven in the matrix like everything else."*
Standard pattern: wire into the live path with a config kill switch **defaulting to `False`**; prove in the matrix; only then flip per-symbol via `data/hourly_reconfig/per_sym_active_config.json`. **Do not wire it until it has a Tier-2 number that beats b&h.** When you do wire it, obey §0 rules 1–2 without exception.

---

## 3. REPORTING FORMAT (mandatory, all 9 fields — drop one and it's invalid)
```
pool_sharpe=X.XXXX | sym_sharpe=X.XXXX | avg_gain_trade=X.XX%/trade | gain_per_yr=XX.X%/yr |
gain_sym_yr=X.XXXX%/sym/yr | trades=N | dd=X.X% | n_syms=N | years=Y.Y
```
Single-symbol work is `[DIAGNOSTIC ONLY · n_syms=1]` and can never promote anything to live.

## 4. HOW TO REPORT BACK
State plainly: what trades now, what the MU b&h floor is, how many matrix cells are filled with real numbers, which configs beat b&h and by how much, and which knobs are inert. **If something is still broken, say so with the evidence — do not round a failure up to a success.** A zero-trade run is a bug report, not a result. Numbers you did not measure do not go in the report.
