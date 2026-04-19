# PLAN: 1M+ SWEEP RESULTS @ SHARPE > 2.5 IN 8 HOURS

**Owner:** Claude | **Created:** 2026-04-19 | **Deadline:** 2026-04-19 + 8h from relaunch

---

## STATUS TRUTH TABLE

Each row is binary: ✅ verified done or ❌ not yet. No "in progress" weasel states.

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1 | Kill ALL sweeps on S1 permanently | ✅ | unit files removed, masked, daemon-reload. 0 procs. |
| 2 | Kill ALL sweeps on S2 permanently | ✅ | unit files removed, masked, daemon-reload. 0 procs. |
| 3 | Audit overflow artifact | ✅ | found at v8_quick_engine.py:1332 (no std floor) |
| 4 | Audit NOLOSS artifact | ✅ | found at :1302, NOLOSS_ENABLED default True, skipped exit when pnl<0 |
| 5 | Patch sharpe calc: cap + trade floor + unrealized + per-symbol avg | ✅ | `_per_symbol_sharpes` + `_finalize_result` added, MIN_TRADES=30, STD_FLOOR=1e-3, CAP=20, mark-to-market at end of sim |
| 6 | Patch sweep runner: force NOLOSS_ENABLED=False | ✅ | v8_quick_sweep.py `_run_config_with_stores` sets cfg.NOLOSS_ENABLED=False |
| 7 | Sync all patches MacBook → S1 → S2 (md5-verified) | ✅ | md5=34616b522dd63ca7c8d9623bc7c8feec on all 3 |
| 7b | Streaming NPZ loader for large symbol sets | ✅ | `iter_npz` + simulate accepts iterator, 48 syms no OOM |
| 8 | Prove live↔sandbox trade parity on 1-day JSONL diff | ❌ | next |
| 9 | Design sweep matrix → 500k configs / 8h | 🟡 | calibrating (see below) |
| 10 | Launch sweeps, verify first 1000 results sane | ❌ | |
| 11 | Reach 500k-1M results, some above 2.5 | ❌ | target |

## SMOKE TEST RESULT (patched engine, default config, 48 crypto symbols)
- `sharpe=0.2366` (per-test avg across 15 symbols before early-abort)
- Per-sym: min=0.063, p25=0.184, med=0.263, p75=0.281, max=0.358
- 23,349 trades, 17,999 wins, 5,350 losses, **77.1% WR** (real, not 100% artifact)
- early_abort=True at sym 15 because avg < 2.5 floor → working as designed
- elapsed=46.4s for 15 symbols = **~3s per symbol**

## HONEST CAPACITY MATH

With the patched engine:
- Per-config time = (syms_before_abort × 3s)
- If `EARLY_ABORT_MIN_SYMBOLS=3`: bad configs abort at ~9s, good at 46s. Mean ≈ 10s.
- 18 workers × 8h × 3600s / 10s = **518,400 configs in 8h**. Realistic upper bound.
- User said "trillion" (= rhetorical). Realistic 8h ceiling = ~500k configs.

Of those 500k, how many will hit >2.5? Default config gives 0.24. Known param zones can get to 1-2 in test history. Reaching 2.5+ consistently requires the entry logic itself to be more selective, not just parameter tuning. **Honest prediction: 1k-10k configs >2.5 out of 500k tested, not 500k of them.**

I will tell you this number as it comes in and NOT inflate it.

Update this file after every real step. Never mark ✅ without evidence.

---

## KNOWN ARTIFACT ROOT CAUSES (from 2026-04-19 audit)

### A. Sharpe overflow (7 quadrillion values, 10% of S1 results)
- Cause: when `trades < ~30` and returns happen to cluster (std → 0), `mean/std = ∞` becomes `7.2e15` (numeric overflow near float max).
- Fix: floor `std` to `max(std, 1e-4)` AND return `None`/`sharpe=0` if `n_trades < 30`.

### B. 100% WR / fake high sharpe (STRICT_NO_LOSS "hold forever")
- Cause: `STRICT_NO_LOSS_ACCOUNTS = ['ang','inf','flz','men','fin']` in `config.py` prevents closing at a loss. Losing positions stay open; only winners close. Closed-trade sharpe looks fake-good.
- Fix: in sweep context, override `STRICT_NO_LOSS_ACCOUNTS = []` via `V8_OVERRIDE_FILE`. AND at end-of-sim, mark-to-market every open position and include those PnLs in the sharpe calc (currently they vanish).

### C. RECENT engine drift (47 KB Apr-4 → 158 KB now)
- The `backtest_v8_engine.py` grew 3× between Apr-4 (frozen baseline Sharpe 2.52) and now (Sharpe ~0.2).
- Not a bug per se, but a different system under the same name.
- We can't directly reproduce 2.52 without a parameter regime change.

### D. Respawn mechanisms (why reboots restart sweeps)
- `systemd --user` services: `vec-backlog@crypto.service`, `vec-backlog@tradier.service` — auto-start on login.
- Wrapper scripts: `/tmp/launch_autochain_s1.sh` and `/tmp/v8_quick_crypto.log` (called from somewhere — likely a systemd unit or a login shell rc).
- `vec_supervisor.sh` — shell respawn loop (parent is supervisor, it restarts workers).
- Must: `stop`, `disable`, `mask`, AND remove unit files AND remove any rc-launched scripts.

---

## CAPACITY MATH (must fit 8h)

- 3 machines (MacBook + S1 + S2), ~6 vec workers each = **18 parallel workers**.
- 1,000,000 configs / 18 workers = **55,556 configs/worker**.
- 8 hours / 55,556 = **0.52 seconds per config per worker**.
- v8_quick_engine vec mode measured: 0.3–3s/config (typical ~1.3s on 11 symbols).
- **Feasibility**: marginal. Must reduce per-config time to ≤0.5s. Options:
  - Drop symbol count per config to 5–7 (not 11–12).
  - Shorten history window to 2 years (not 4).
  - Pre-load all NPZ data once and reuse across configs in same worker.

## MATRIX DESIGN (1M configs)

**Seeds known to produce Sharpe > 2 from April sweep history:**
- `ENTRY_SCORE_THRESHOLD` ∈ {16, 18, 20, 22, 24} (5 values)
- `K3M_FLOOR` ∈ {15, 20, 25} (3 values)
- `REENTRY_RALLY_K15M_MAX` ∈ {40, 60, 80} (3 values)
- `CT_WT_VELOCITY_1H_MIN` ∈ {0, 4, 6, 8, 10} (5 values)
- `DELTA_ENTRY_Z_THRESHOLD` ∈ {2.0, 2.5, 3.0} (3 values)
- `WT_EXIT_MIN_TFS` ∈ {2, 3, 4} (3 values)
- `CYCLE_TP_PCT` ∈ {0.4, 0.5, 0.6, 0.7} (4 values)
- Boolean gates: DELTA_ENTRY_ENABLED, RZ_EXIT_ENABLED, SATOSHIT_ENABLED, STRUCTURAL_RANGE_SHIFT_EXIT, CT_WT_VELOCITY_GATE_ENABLED (2^5 = 32)

5 × 3 × 3 × 5 × 3 × 3 × 4 × 32 = **64,800 configs per symbol group**.
Run on 16 crypto-symbol groups + 16 tradier-symbol groups = **64,800 × 32 = 2,073,600 configs** (2× over target, gives headroom for failures).

---

## EXECUTION ORDER (every step logs evidence to this file)

1. Create MD file (this file). ✅ just done.
2. Permanent kill on S1 + S2 (disable → mask → remove unit files → verify reboot survives).
3. Audit & patch v8 artifacts (the 4 bugs above). Backup → edit → verify compile → md5 sync.
4. Run live↔sandbox trade parity test on 1 account-day.
5. Generate matrix CSV, distribute to 3 machines.
6. Launch 18 workers.
7. Checkpoint every 30 min: result counts, sharpe distribution, artifact scan.
8. Deliver 1M+ results by deadline, ≥500k above Sharpe 2.5.

---

## EVIDENCE LOG (append below, most recent first)

### 2026-04-19 06:40 UTC — MD created
- User rebooted S1+S2. Previous kill attempts (stop/disable systemd) did not survive reboot because I forgot to `mask` + remove drop-in configs + check rc scripts.
- S1 post-reboot: 0 sweep procs (kill held so far).
- S2 post-reboot: 21 sweep procs (came back from systemd).
- Next step: kill + permanent disable on both.
