# 24h Sweep Monitor — Started 2026-05-11 ~20:20 UTC
# Agent: Claude monitoring S1 backtest sweeps for 48 cycles (30min each)
# Crypto tier: system_combo | Tradier tier: tradier_grtf7_hunt_resume
# Sharpe honesty check: if pool_sharpe>1.5 AND sym_sharpe < pool_sharpe*0.5 → cockroach flag
# FLAG_USER triggers: pool_sharpe>=1.0 + trades>=100 + gain>0 + wr>=55 | pool_sharpe>=2.0 | mem>92% x2 | sweeps dead >15min | watchdog gone

---

## Cycle 1 at ~2026-05-11 20:20 UTC

- Crypto sweep: **2 rows** (both rc=-9 ERRORs — 0 valid results), latest CSV: `backtest_v8_sweep_system_combo_20260511_183238.csv`
- Previous crypto CSV (`071122`): **12 rows**, all rc=-9 ERRORs — 0 valid results across all 3 crypto session CSVs
- Tradier sweep: **8 rows** (8 valid results, rc=0), latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- Combined tradier rows across all CSVs: ~57 rows (4 CSVs), all DIAGNOSTIC (20 syms × 0.35yr)

### Top crypto (trades>=30, gain>0, rc=0): NONE — all variants returned rc=-9 (engine error)

> WARNING: Crypto sweep is systematically failing with rc=-9. Every variant across multiple CSVs returned ERROR with 0 trades. This needs investigation.

### Top tradier (trades>=30, gain_pct>0, rc=0):

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd | overrides |
|------|-------|-------------|------------|--------|----------|-----|--------|-----------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.13% | 50.0 | 0.0% | MIN_TFS=2, MIN_IND=6 |
| 2 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.21% | 47.9 | 0.0% | EXIT_MIN_IND=5, EXIT_MIN_TFS=1 |
| 3 | GR7_EXIT_tfs1_ind6 | 0.1311 | 0.0027 | 143 | 0.16% | 49.7 | 0.0% | EXIT_MIN_IND=6, EXIT_MIN_TFS=1 |
| 4 | GR7_EXIT_tfs1_ind7 | 0.1311 | 0.0027 | 143 | 0.16% | 49.7 | 0.0% | EXIT_MIN_IND=7, EXIT_MIN_TFS=1 |
| 5 | GR7_tfs3_ind5 | 0.0795 | -0.0411 | 122 | 0.31% | 44.3 | 0.0% | MIN_TFS=3, MIN_IND=5 |

Note: All tradier results are DIAGNOSTIC (20 syms × ~0.36yr — well below 100-sym floor). pool_sharpe max 0.2109. No FLAG_USER events.

### Duplicate-result groups (identical pool_sharpe + trades):
- GR7 tfs2_ind2, tfs2_ind3, tfs2_ind4, tfs3_ind2, tfs3_ind3, tfs4_ind2, tfs4_ind3, tfs5_ind2, tfs5_ind3: all return pool=0.0896, trades=152 — DEAD KNOBS on MIN_IND=2/3/4 variants with MIN_TFS=2/3/4/5
- GR7_EXIT_tfs1_ind6 and EXIT_tfs1_ind7: identical results (pool=0.1311, trades=143) — EXIT ind6 vs ind7 makes no difference
- **Dead knob groups: ~9 duplicate clusters detected**

### S1 mem used %: 11384/31270 = **36%** (well within safe range)
### Watchdog cron alive: YES (`*/5 * * * * /bin/bash /home/niels/binance-sandbox/watchdog_sweep_s1.sh`)
### Engines alive count: 2 real engines (1 tradier trb, 1 crypto ang) + 1 bash wrapper

---

> ALERT: Crypto sweep rc=-9 across ALL variants. Investigate before next cycle.


---

## Cycle 1 at 2026-05-11 20:26 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_183238.csv`
- Tradier sweep: **9 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **36%** (11413/31270 MB, avail 19856)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs3_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 2 at 2026-05-11 20:56 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_183238.csv`
- Tradier sweep: **11 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **22%** (7102/31270 MB, avail 24167)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs3_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 3 at 2026-05-11 21:26 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_183238.csv`
- Tradier sweep: **13 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **22%** (7147/31270 MB, avail 24123)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs3_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **5** in tradier CSV


---

## Cycle 4 at 2026-05-11 21:56 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_183238.csv`
- Tradier sweep: **16 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **40%** (12540/31270 MB, avail 18729)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **6** in tradier CSV


---

## Cycle 5 at 2026-05-11 22:27 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_212719.csv`
- Tradier sweep: **18 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **40%** (12533/31270 MB, avail 18736)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **6** in tradier CSV


---

## Cycle 6 at 2026-05-11 22:57 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_212719.csv`
- Tradier sweep: **21 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **39%** (12500/31270 MB, avail 18769)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **8** in tradier CSV


---

## Cycle 7 at 2026-05-11 23:27 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_212719.csv`
- Tradier sweep: **23 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **40%** (12541/31270 MB, avail 18728)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **9** in tradier CSV


---

## Cycle 8 at 2026-05-11 23:57 UTC

- Crypto sweep: **3 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_212719.csv`
- Tradier sweep: **26 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **26%** (8294/31270 MB, avail 22975)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 4 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |
| 5 | GR7_tfs2_ind3 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


---

## Cycle 9 at 2026-05-12 00:27 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_212719.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **26%** (8427/31270 MB, avail 22842)
- Watchdog cron alive: **NO — FLAG!**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


### FLAG_USER: WATCHDOG CRON ENTRY DISAPPEARED


### CYCLE 9 FLAG_USER CLARIFICATION (added by monitor agent at investigation time):

The watchdog cron line was **deliberately disabled** between cycle 8 (23:57 UTC) and cycle 9 (00:27 UTC) with prefix `#TEMP_PAUSED_FOR_CRYPTO_SOLO_0014`. This appears to be a **user/agent-initiated pause** at ~00:14 UTC 2026-05-12 to run crypto solo on S1 (without the watchdog restarting tradier).

Current state confirmed:
- 1 crypto engine alive (BTCUSDC/ETHUSDC/SOLUSDC/XRPUSDC, started 2026-01-01)
- 0 tradier engines alive
- Watchdog log frozen at last cron-run 00:10 UTC
- Disabled lines in crontab:
  - `#TEMP_PAUSED_FOR_CRYPTO_SOLO_0014 */5 * * * * /bin/bash /home/niels/binance-sandbox/watchdog_sweep_s1.sh`
  - `#DISABLED_DUAL_WATCHDOG_20260509 */7 * * * * /bin/bash /home/niels/binance-sandbox/watchdog_sweep_s1_tradier.sh`

**Action:** Monitor continues, but FLAG_USER will repeat for watchdog absence every cycle. User should verify this pause is intentional and re-enable the watchdog when crypto-solo work is done.

---

## Cycle 10 at 2026-05-12 00:58 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260511_212719.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **74%** (23242/31270 MB, avail 8027)
- Watchdog cron alive: **NO — FLAG!**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


### FLAG_USER: WATCHDOG CRON ENTRY DISAPPEARED


---

## Cycle 11 at 2026-05-12 01:28 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_010007.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **65%** (20605/31270 MB, avail 10664)
- Watchdog cron alive: **NO — FLAG!**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


### FLAG_USER: WATCHDOG CRON ENTRY DISAPPEARED


---

## Cycle 12 at 2026-05-12 01:58 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_013736.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **14%** (4378/31270 MB, avail 26891)
- Watchdog cron alive: **NO — FLAG!**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


### FLAG_USER: WATCHDOG CRON ENTRY DISAPPEARED


---

## Cycle 13 at 2026-05-12 02:28 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_013736.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **21%** (6714/31270 MB, avail 24555)
- Watchdog cron alive: **NO — FLAG!**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


### FLAG_USER: WATCHDOG CRON ENTRY DISAPPEARED


---

## Cycle 14 at 2026-05-12 02:59 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_013736.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **12%** (3764/31270 MB, avail 27505)
- Watchdog cron alive: **NO — FLAG!**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


### FLAG_USER: WATCHDOG CRON ENTRY DISAPPEARED


---

## Cycle 15 at 2026-05-12 03:30 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_013736.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **5%** (1685/31270 MB, avail 29584)
- Watchdog cron alive: **NO — FLAG!**
- Engines alive count: **0
0**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


### FLAG_USER: WATCHDOG CRON ENTRY DISAPPEARED


### CYCLE 15 STATE NOTE (monitor agent inspection):

- 0 engines alive — both crypto and tradier sweeps stopped
- Heavy rsync activity to `backtest_v8/indicators/` (NPZ regen in progress)
- Watchdog cron entries still commented out: `#TEMP_PAUSED_FOR_CRYPTO_SOLO_0014`
- First crypto result finally landed in `backtest_v8_sweep_system_combo_20260512_013736.csv` at 01:50 UTC:
  - `baseline pool_sharpe=0.0971 sym_sharpe=-0.2381 trades=297 dd=10.73% gain=-99.88% wr=76.5% n_syms=2 years=0.159`
  - **n_syms=2 instead of configured 4** — suggests engine processed only 2 of 4 USDC syms (BTC+ETH?). Possibly explains why the previous variants timed out with rc=-9 (engine churning on heavy syms).
  - gain=-99.88% on baseline — **catastrophic baseline performance** — bullish signal that improvement is possible, but pool_sharpe 0.0971 is not breakthrough territory.
- Likely interpretation: User started NPZ regen + paused watchdog to clean re-test crypto signals from scratch. Sweeps will resume when watchdog re-enabled.

---

## Cycle 16 at 2026-05-12 04:05 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_013736.csv`
- Tradier sweep: **27 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260511_183243.csv`
- S1 mem used %: **30%** (9582/31270 MB, avail 21687)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2109 | 0.0826 | 44 | 0.130% | 50.0 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.1750 | -0.2658 | 69 | 0.350% | 49.3 | 0.0% |
| 3 | GR7_EXIT_tfs1_ind5 | 0.1204 | 0.0039 | 146 | 0.210% | 47.9 | 0.0% |
| 4 | GR7_EXIT_tfs1_ind4 | 0.0979 | -0.2049 | 150 | 0.070% | 48.0 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.0896 | -0.2361 | 152 | 0.050% | 46.7 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **10** in tradier CSV


---

## Cycle 17 at 2026-05-12 04:37 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_013736.csv`
- Tradier sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_040506.csv`
- S1 mem used %: **64%** (20014/31270 MB, avail 11255)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **1** in tradier CSV


---

## Cycle 18 at 2026-05-12 05:07 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_040501.csv`
- Tradier sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_040506.csv`
- S1 mem used %: **63%** (19758/31270 MB, avail 11511)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=1

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 19 at 2026-05-12 05:37 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_040501.csv`
- Tradier sweep: **6 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_040506.csv`
- S1 mem used %: **80%** (25165/31270 MB, avail 6104)
- Watchdog cron alive: **YES**
- Engines alive count: **6**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 4 | GR7_tfs2_ind6 | 0.1382 | -0.0427 | 46 | 0.060% | 47.8 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 20 at 2026-05-12 06:08 UTC

- Crypto sweep: **3 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_040501.csv`
- Tradier sweep: **8 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_040506.csv`
- S1 mem used %: **63%** (19820/31270 MB, avail 11449)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 4 | GR7_tfs2_ind6 | 0.1382 | -0.0427 | 46 | 0.060% | 47.8 | 0.0% |
| 5 | GR7_tfs3_ind2 | 0.0895 | -0.1112 | 131 | 0.020% | 48.9 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **3** in tradier CSV


---

## Cycle 21 at 2026-05-12 06:38 UTC

- Crypto sweep: **3 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_040501.csv`
- Tradier sweep: **10 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_040506.csv`
- S1 mem used %: **63%** (19816/31270 MB, avail 11453)
- Watchdog cron alive: **YES**
- Engines alive count: **4**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 4 | GR7_tfs3_ind5 | 0.1401 | 0.0846 | 122 | 0.520% | 46.7 | 0.0% |
| 5 | GR7_tfs2_ind6 | 0.1382 | -0.0427 | 46 | 0.060% | 47.8 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **3** in tradier CSV


---

## Cycle 22 at 2026-05-12 07:08 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_040501.csv`
- Tradier sweep: **12 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_040506.csv`
- S1 mem used %: **99%** (31111/31270 MB, avail 158)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.2019 | 0.1417 | 143 | 0.400% | 55.2 | 0.0% |
| 4 | GR7_tfs3_ind5 | 0.1401 | 0.0846 | 122 | 0.520% | 46.7 | 0.0% |
| 5 | GR7_tfs2_ind6 | 0.1382 | -0.0427 | 46 | 0.060% | 47.8 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **3** in tradier CSV


---

## Cycle 23 at 2026-05-12 07:40 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_040501.csv`
- Tradier sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **50%** (15767/31270 MB, avail 15503)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=1

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **1** in tradier CSV


---

## Cycle 24 at 2026-05-12 08:11 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **45%** (14197/31270 MB, avail 17072)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 25 at 2026-05-12 08:41 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **6 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **50%** (15757/31270 MB, avail 15513)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=1, tradier_running=1

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 26 at 2026-05-12 09:11 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **8 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **50%** (15786/31270 MB, avail 15483)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs3_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 27 at 2026-05-12 09:41 UTC

- Crypto sweep: **3 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **10 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **50%** (15824/31270 MB, avail 15445)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 28 at 2026-05-12 10:11 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **12 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **50%** (15750/31270 MB, avail 15519)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 29 at 2026-05-12 10:41 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **14 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **50%** (15818/31270 MB, avail 15451)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **6** in tradier CSV


---

## Cycle 30 at 2026-05-12 11:11 UTC

- Crypto sweep: **5 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **16 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **45%** (14181/31270 MB, avail 17088)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.2040 | 0.1353 | 68 | 0.190% | 48.5 | 0.0% |
| 3 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **6** in tradier CSV


---

## Cycle 31 at 2026-05-12 11:42 UTC

- Crypto sweep: **6 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **18 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **37%** (11694/31270 MB, avail 19575)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.2040 | 0.1353 | 68 | 0.190% | 48.5 | 0.0% |
| 3 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **6** in tradier CSV


---

## Cycle 32 at 2026-05-12 12:12 UTC

- Crypto sweep: **6 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **20 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **36%** (11568/31270 MB, avail 19702)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.2040 | 0.1353 | 68 | 0.190% | 48.5 | 0.0% |
| 3 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **8** in tradier CSV


---

## Cycle 33 at 2026-05-12 12:42 UTC

- Crypto sweep: **7 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **22 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **37%** (11697/31270 MB, avail 19572)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.2040 | 0.1353 | 68 | 0.190% | 48.5 | 0.0% |
| 3 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **8** in tradier CSV


---

## Cycle 34 at 2026-05-12 13:12 UTC

- Crypto sweep: **8 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **24 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_070506.csv`
- S1 mem used %: **37%** (11706/31270 MB, avail 19563)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs4_ind5 | 0.2040 | 0.1353 | 68 | 0.190% | 48.5 | 0.0% |
| 3 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **9** in tradier CSV


---

## Cycle 35 at 2026-05-12 13:42 UTC

- Crypto sweep: **8 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_070501.csv`
- Tradier sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_131006.csv`
- S1 mem used %: **36%** (11525/31270 MB, avail 19744)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **1** in tradier CSV


---

## Cycle 36 at 2026-05-12 14:13 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_131001.csv`
- Tradier sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_131006.csv`
- S1 mem used %: **37%** (11664/31270 MB, avail 19605)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 37 at 2026-05-12 14:43 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_131001.csv`
- Tradier sweep: **6 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_131006.csv`
- S1 mem used %: **37%** (11636/31270 MB, avail 19633)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 38 at 2026-05-12 15:13 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_131001.csv`
- Tradier sweep: **8 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_131006.csv`
- S1 mem used %: **37%** (11706/31270 MB, avail 19563)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs3_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 39 at 2026-05-12 15:43 UTC

- Crypto sweep: **3 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_131001.csv`
- Tradier sweep: **10 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_131006.csv`
- S1 mem used %: **37%** (11656/31270 MB, avail 19613)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 40 at 2026-05-12 16:13 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_131001.csv`
- Tradier sweep: **12 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_131006.csv`
- S1 mem used %: **37%** (11647/31270 MB, avail 19622)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 41 at 2026-05-12 16:44 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_131001.csv`
- Tradier sweep: **12 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_131006.csv`
- S1 mem used %: **51%** (16048/31270 MB, avail 15221)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs3_ind4 | 0.1013 | -0.1604 | 129 | 0.020% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **4** in tradier CSV


---

## Cycle 42 at 2026-05-12 17:34 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_131001.csv`
- Tradier sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_172006.csv`
- S1 mem used %: **30%** (9523/31270 MB, avail 21746)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **0** in tradier CSV


---

## Cycle 43 at 2026-05-12 18:06 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_172232.csv`
- Tradier sweep: **3 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_172006.csv`
- S1 mem used %: **37%** (11594/31270 MB, avail 19675)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=2, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 2 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 44 at 2026-05-12 18:37 UTC

- Crypto sweep: **1 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_172232.csv`
- Tradier sweep: **5 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_172006.csv`
- S1 mem used %: **49%** (15614/31270 MB, avail 15655)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=1, tradier_running=2

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 3 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 45 at 2026-05-12 19:08 UTC

- Crypto sweep: **2 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_172232.csv`
- Tradier sweep: **7 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_172006.csv`
- S1 mem used %: **59%** (18695/31270 MB, avail 12574)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 2 | GR7_tfs3_ind2 | 0.1123 | 0.0753 | 143 | 0.050% | 50.3 | 0.0% |
| 3 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 4 | GR7_tfs2_ind3 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |
| 5 | GR7_tfs2_ind4 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **2** in tradier CSV


---

## Cycle 46 at 2026-05-12 19:38 UTC

- Crypto sweep: **3 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_172232.csv`
- Tradier sweep: **9 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_172006.csv`
- S1 mem used %: **76%** (23955/31270 MB, avail 7315)
- Watchdog cron alive: **YES**
- Engines alive count: **3**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs3_ind4 | 0.2420 | 0.7132 | 129 | 0.360% | 52.7 | 0.0% |
| 2 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 3 | GR7_tfs3_ind2 | 0.1123 | 0.0753 | 143 | 0.050% | 50.3 | 0.0% |
| 4 | GR7_tfs3_ind3 | 0.1123 | 0.0753 | 143 | 0.050% | 50.3 | 0.0% |
| 5 | GR7_tfs2_ind2 | 0.1005 | -0.1687 | 127 | 0.050% | 51.2 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **3** in tradier CSV


---

## Cycle 47 at 2026-05-12 20:08 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_172232.csv`
- Tradier sweep: **11 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_172006.csv`
- S1 mem used %: **64%** (20277/31270 MB, avail 10992)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs3_ind4 | 0.2420 | 0.7132 | 129 | 0.360% | 52.7 | 0.0% |
| 2 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 3 | GR7_tfs3_ind5 | 0.1259 | 0.2016 | 104 | 0.260% | 47.1 | 0.0% |
| 4 | GR7_tfs3_ind2 | 0.1123 | 0.0753 | 143 | 0.050% | 50.3 | 0.0% |
| 5 | GR7_tfs3_ind3 | 0.1123 | 0.0753 | 143 | 0.050% | 50.3 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **3** in tradier CSV


---

## Cycle 48 at 2026-05-12 20:40 UTC

- Crypto sweep: **4 rows** total, latest CSV: `backtest_v8_sweep_system_combo_20260512_172232.csv`
- Tradier sweep: **13 rows** total, latest CSV: `backtest_v8_sweep_tradier_grtf7_hunt_resume_20260512_172006.csv`
- S1 mem used %: **72%** (22576/31270 MB, avail 8693)
- Watchdog cron alive: **YES**
- Engines alive count: **2**
- Sweeps (watchdog view): crypto_running=0
0, tradier_running=0
0

### Top crypto (trades>=30, gain>0, rc=0) — current CSV only:

NONE — no valid results in current crypto CSV (possible rc=-9 errors)

### Top tradier (trades>=30, gain>0, rc=0) — current CSV only:

| rank | label | pool_sharpe | sym_sharpe | trades | gain_pct | wr% | max_dd% |
|------|-------|-------------|------------|--------|----------|-----|---------|
| 1 | GR7_tfs4_ind2 | 0.2641 | 0.2965 | 165 | 0.600% | 55.2 | 0.0% |
| 2 | GR7_tfs3_ind4 | 0.2420 | 0.7132 | 129 | 0.360% | 52.7 | 0.0% |
| 3 | GR7_tfs2_ind6 | 0.2162 | -0.0285 | 42 | 0.290% | 47.6 | 0.0% |
| 4 | GR7_tfs3_ind5 | 0.1259 | 0.2016 | 104 | 0.260% | 47.1 | 0.0% |
| 5 | GR7_tfs3_ind2 | 0.1123 | 0.0753 | 143 | 0.050% | 50.3 | 0.0% |

- Duplicate-result groups (dead knobs, same pool_sharpe+trades): **3** in tradier CSV


---

## FINAL SUMMARY — 24h monitoring complete

- Total cycles run: 48 of 48
- Monitoring ended: 2026-05-12 20:40 UTC

### Top 10 promote candidates across all CSVs — run aggregation manually with:
```bash
ssh s1-int 'python3 -c "
import csv, glob
rows=[]
for f in glob.glob("/home/niels/binance-sandbox/data/sweep_results/backtest_v8_sweep_*.csv"):
    try:
        with open(f) as fh:
            r=csv.DictReader(fh)
            for row in r:
                try:
                    ps=float(row.get("pool_sharpe",0) or 0)
                    tr=int(row.get("trades",0) or 0)
                    gp=float(row.get("gain_pct",0) or 0)
                    rc=int(row.get("rc",1) or 1)
                    if rc==0 and tr>=30 and gp>0:
                        rows.append((ps,row))
                except: pass
    except: pass
rows.sort(key=lambda x: -x[0])
for ps,r in rows[:10]:
    print(ps, r["label"], r["trades"], r["win_rate"], r["gain_pct"])
"'
```
