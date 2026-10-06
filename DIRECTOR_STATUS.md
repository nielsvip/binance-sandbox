# DIRECTOR STATUS — 2026-10-06 (updated by the director session; newest at top of each row)

| # | Workstream | Owner | State | Blocker / next |
|---|---|---|---|---|
| 1 | **Fresh crypto data** — NPZ bar refresh on S1 (old `~/npzb` loop was DEAD → NPZs 2–5 days stale) | X2 agent (user-approved install) | building; proof → install every-minute cron | 45/48 live keys STALE until it runs |
| 2 | **Emergency bridge** (≤3 h, monitored): S1 executor runs v12 per proven set → Mac ez_manage executes via queue_trade_action → execute_trade_action → execute_now | X1 ✅ (cron live, equivalence 7/7 sym_sides 0 mismatches) · X3 ✅ (consumer, 25 tests) | ON: `VEC_DRIVEN_ENABLED=True`, registry 37 sym_sides on real accounts, expiry +3 h, KILL = `data/vec_live/KILL`, pull + monitor crons on Mac | first intent emitted (1000BONKUSDC_SHORT 03:30Z); needs #1 for the rest |
| 3 | **Live config = vec baseline** | director ✅ | all `ABLATION_DISABLE_*` = False (config.py + config_tradier.py), reentry enforce loop on; crypto accounts restarted | Muse (`--yolo`) also restarts live accounts concurrently — risk |
| 4 | **Indicator speed + 2nd source** | director ✅ (`ii()` JSON parse once per file, redis 30 s breaker) · infra agent (S1 ez_indicators + Mac pull) | ii() fix live after restart; S1 stack in progress | — |
| 5 | **Real parity, crypto** (live and vec call the SAME functions in the real scripts, 15m cadence) | PARITY LOOP CRYPTO | shared-function twins behind `PARITY_VEC_EXACT_MODE` | harness H3 (real execute_now) from lane A |
| 6 | **Real parity, stocks** | PARITY LOOP STOCKS | decision layer PROVEN identical (AAPL_LONG 70/70); user unlocked tradier_manage/config_tradier → applying exact-mode hook; vx2 trip parity running | MSTR_LONG/PYPL_SHORT sets poisoned (SIMPLE_PRICE_GT0) → excluded |
| 7 | **Replay harness** (backtest_v12_engine) | lane A | fixed: record_decision crash, path pinning, injected fake producers removed, set precedence, cadence; real execute_now opt-in | account-state stub so real execute_now sizing works |
| 8 | **Forward tests** (live vs vector continuously; LIVE_ONLY 1m/3m/5m + non-vectorizable listed) | forward-test agent | repairing: crypto run timed out every hour, dh_parity gaps unattended, daily-parity-test failing, 1378 unmapped exits | → `data/reports/forward_parity_latest.md` |
| 9 | **The original job**: encyclopedia + v15_pilot DIAGNOSE+REPAIR + trade autopsy (which TEMPLATE row fixes which bad trade) | director | built & tested (10 tests); dual-window (30D+365D) search; repair queues s5 crypto / s2 stocks (RTH-correct engine) / S1 dual re-run | deploy to fleet after parity (#5–#7) |
| 10 | **Fleet relaunch** (S1 coordinator, all 3 servers, newest scripts) | director | paused for parity; `tools/parity_cut_deploy.sh` ready (restores every paused cron + fleet-healer) | after parity proof |

Hard facts recorded: stocks trade only in RTH (unconditional in the engine; all earlier stock sheets invalid); ablation never True outside a test; sizing multipliers held at 1.0× live until user approval; SIMPLE_PRICE_GT0 sets never driven.
