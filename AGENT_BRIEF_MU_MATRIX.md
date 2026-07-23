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

**STATE AT HANDOFF (2026-07-23 ~06:15 UTC) — verify, don't assume:**
Run COMPLETE: `~/logs/mu_unblocked.log`, MU / trb / start 2024-04-01, launched with `V8_SWEEP_MODE=1`.

- ✅ **THE SANDBOX TRADES AGAIN: `trades=232`** over `window_days=841.66` (2.3 yr). Old campaign baseline was 19. **Task 1's acceptance bar (trades ≥ 1) is met.**
- ✅ **The unblock mechanism is verified working.** A fresh import under `V8_SWEEP_MODE=1` on S1 yields `TRADIER_ENTRY_SCORE_THRESHOLD=0, GOLDEN_RULE_MIN_IND=0, REQUIRE_ACTIVATION=False`. `config_tradier` has **no module-level `config` singleton** (`C.config` is None), so each instantiation re-applies it.
- ⚠️ `[SWEEP_ENTRY_UNBLOCK]` is absent from the run log even though `/proc/<pid>/environ` confirmed the var was set. Most likely a lost stdout buffer: `backtest_v8_engine.py:50` re-execs the process for `PYTHONHASHSEED`, and `os.exec*` replaces the image **without flushing Python's stdio buffer**, so a `print()` issued before it disappears. Cosmetic — but **prove it, don't trust it**: re-run with `V8_KEEP_ENTRY_GATES=1` and diff `trades` + `trades_fingerprint` against 232. Different ⇒ unblock is live. Identical ⇒ it is NOT taking effect in the engine path and must be fixed before any cell is recorded.
- 🔴 **The engine itself flags this result untrustworthy:**
  `FINAL_BROKEN_RATE: trades=232 projected=0.28/acct/day target>=3/acct/day — result NOT trustworthy`
  232 trades over 2.3 yr is ~100/yr; the engine wants ≥3/day. **Do not record this as a finished baseline.** It clears "is anything trading at all," not "is this a usable baseline." Raising trade frequency toward that target is the real content of Tasks 4–5 (arrow entries on 1h/4h/D + ladder re-entries).
- ❌ **Still unmeasured: gain vs b&h.** No `gain_pct` / `acc_gain` line was emitted to the log, so **MU's b&h floor is still unknown** and nothing has yet been shown to beat it. Task 2 is wide open — that is your first real deliverable.

### TASK 2 — ✅ MEASURED 2026-07-23. THE B&H FLOOR (15m closes, start 2024-04-01)
```
SYM     first      last     b&h_pct     window
MU     119.00    983.28   +726.29%    2.31yr  n=37793   <-- THE MU FLOOR. BEAT THIS.
NVDA    91.38    212.02   +132.02%    2.31yr  n=38120
HAO    136.96      0.19    -99.86%    0.29yr  n=3351    <-- only 3.5 months, unusable
VT     156.23    155.99     -0.15%    0.06yr  n=708     <-- 708 bars, unusable
```
**MU_LONG b&h = +726.29%.** This is the 1-trade baseline: buy at bar 0, hold to the last bar.
A cell is only worth recording if it **beats +726.29%**. Anything below is a regression, not a result.
HAO and VT cannot be baselined at all on current data — say so, do not emit a short-window number as if comparable.

Prior Tier-2 reference points, all FAR below the floor: default config **3.07%**; wt_5m cross **0.27x b&h**;
band-top harvest **0.50x**; band arrow **1.4%/33 trades**. **Nothing has beaten b&h yet.**

### TASK 2b — Run ordering (USER 2026-07-23)
**Fill the matrix in THIS ORDER, and record only cells that beat the b&h floor:**
1. **exits HTF** (D / 4h / 1h exit knobs)
2. **entries HTF**
3. **exits LTF** (15m / 5m)
4. **entries LTF**

Rationale: you cannot be below b&h and cannot be out of a position while price is above the exit price — so exits are what create or destroy the multiple. Get exits right on the high timeframes first; entries only decide re-entry timing once exits are sane.

**Ladder:** being handed to Codex (USER 2026-07-23) — do not sink more time into the multiplier math.
**Entry fallback if the ladder is unavailable:** enter on **`wt_cross_5m`**, or **`wt_cross_15m`**. Both are CONFIRMED present in `backtest_v8/indicators/MU.npz`, alongside `wt1_/wt2_/wt_bullish_/wt_cross_bear_/wt_cross_bars_ago_` for both 5m and 15m. No missing-data excuse here.

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
