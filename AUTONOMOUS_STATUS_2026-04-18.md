# Autonomous Run — 2026-04-18 → Monday 2026-04-20

Snapshot at 2026-04-18 01:20 UTC. All three machines active. No user intervention required until Monday.

## What's running

### MacBook (source of truth, live trading)
| Service | Mechanism | Purpose |
|---|---|---|
| `com.niels.binance-supervisor` | launchd `KeepAlive=true` | Probes S1/S2 via `s1-int`/`s2-int` every 120s. 3-tick hysteresis. At 600s SSH-dead → Hetzner hw reset via Cloud API. 900s → second reset. Creds at `~/.config/hetzner.env`. |
| `com.niels.s1-s2-autosync` | launchd `KeepAlive=true`, **2s tick** | Compares MacBook vs S1/S2 md5 for ALL `ez_*.py`, `tradier_*.py`, `wt_*.py`, `v8_*.py`, `backtest_v8_*.py`, `utils.py`, `symbols.json`, `breakout_multi_lung.py`. Any drift → rsync from MacBook (MacBook always wins). **Excludes** `config.py`, `config_tradier.py` (allowed to differ per 2026-04-18 user rule). |
| `com.niels.autosave-15min` | launchd | Copies critical files to `backups/autosave/<ts>/` every 15 min. |
| `ez_manage.py --account {ang,inf,flz,men,fin}` | `run_with_watchdog.sh` | Live crypto trading. Restarted 2026-04-18 ~00:15 to pick up today's fixes. |
| `tradier_manage.py --accounts {trb,trc}` | `run_with_watchdog.sh` | Live stocks. |
| `ez_{klines,prices,rankings,indicators,market_data,news_scanner,crosses,mark_prices,share_ind,copilot,positions_watchdog,backup}.py` | `run_with_watchdog.sh` | Data + support services. |

### S1 (crypto backtests)
| Component | State |
|---|---|
| `autonomous_sweep_orchestrator.sh` | Running (PID 3206869). Auto-chains sweeps. |
| Current sweep | `backtest_v8_sweep.py --mode crypto --tier reentry_optimize` BTC/ETH/SOL/LINK. |
| `/home/niels/SWEEP_RUNNING` marker | `REENTRY_OPTIMIZE` (set 2026-04-17 23:32). |

### S2 (stocks backtests)
| Component | State |
|---|---|
| Current sweep | `_ab_sector_sweep.py --sector mix_12 --workers 8` (9 processes PID 16537+). |
| Orchestrator | **Not visible.** When `mix_12` completes, no auto-chain detected. Flag for Monday if sweep ran dry. |
| `SWEEP_RUNNING` marker | Missing. |

## Today's fixes (on disk + synced to S1/S2)

All md5-verified identical across MacBook + S1 + S2:

1. **Webhook bypass kills** (ez_positions_quick.py: 5 call sites tagged `WEBHOOK_BYPASS_KILLED`) — no more `tracker_manager.send_webhook()` fallbacks routing around `execute_now`. Triggered by inf:CELRUSDT_LONG triple-open incident.
2. **Hedge gate NameError** fixed (ez_positions_quick.py:4918) — success-return no longer references deleted vars. Paired with the 2026-04-17 `wt_3m AND wt_1h ONLY` rule.
3. **WT_COMPOSITE_SCORING_ENABLED=True** in config.py — unlocks dormant bonus block.
4. **zone_action + pyramid** wired into rate()'s `_delta_entry_ok`/`_delta_exit_ok`.
5. **delta pos_state fix** (rate() line 1694) — now passes `n_entries:1, last_entry_price` matching check_entry_candidates; fixes DeltaTracker computing on weak state.
6. **Reentry monitor union** (bulk_entry_scan_loop) — iterates `tradeable_position_keys ∪ reentry_data ∪ entry_candidates ∪ global tradeable_keys` so manually-closed positions stay monitored.
7. **Supervisor flap fix** — 3-tick SSH success hysteresis before clearing `ssh_fail_since`.
8. **WT_15M_SAME_HEDGE rewire** (ez_manage.py:19569) — now routes through `hedge_engine.execute_same_symbol_hedge` which does tradeable_keys temp-add + nuke_hedge_key on close. Ends `NON_TRADEABLE_HARD_BLOCK` loop.

## Double-open defense in depth (audited 2026-04-18 00:52)

| Layer | Location | Status |
|---|---|---|
| `OPEN_ON_OPEN_RECLASSIFY` → AUGMENT | ez_manage.py:10580 | Intact |
| `BLOCKED_DUPLICATE_OPEN_{pk}` | ez_manage.py:10605 | Intact |
| `BLOCK_YOUFUCKINGPIECEOFSHIT_OPEN IS FOR ZERO` | ez_manage.py:10971 | Intact |
| `OPEN_ON_OPEN_BLOCK` | ez_manage.py:12968 | Intact |
| `ABSOLUTE_WEBHOOK_LOCK` (30s TTL, open-direction only) | ez_manage.py:14046-14074 + ez_positions_quick.py:6957+ | Intact |
| `tracker_manager.send_webhook()` direct bypasses | ez_positions_quick.py | **ZERO** (all 5 killed) |
| `futures_create_order` outside execute_now | `place_maker_order` (ez_manage.py:12601, inside execute_now) + STOP_MARKET (reduce-only) | Only legitimate paths |
| `ez_double.py`, `import argparse.py` | Not imported anywhere | Dead code |

## How to monitor Monday

```bash
# All 3 md5-match?
md5 -q ez_positions_quick.py ez_manage.py
ssh s1-int "md5sum /home/niels/binance-sandbox/ez_positions_quick.py /home/niels/binance-sandbox/ez_manage.py"
ssh s2-int "md5sum /home/niels/binance-sandbox/ez_positions_quick.py /home/niels/binance-sandbox/ez_manage.py"

# Autosync healthy?
tail -20 /tmp/s1_s2_autosync.log       # silent = in parity; look for PUSHED_OK on any drift
launchctl list | grep s1-s2-autosync

# Supervisor healthy? (Hetzner hw reset ready?)
tail -30 ~/logs/supervisor.log | grep -E "streak=|FIRED|RESET"

# ez_manage per account still alive?
ps -ef | grep "ez_manage.py --account" | grep -v grep

# Any webhook bypass attempts (should all be logger.critical no-ops)?
grep -c "WEBHOOK_BYPASS_KILLED" ~/logs/ez_manage_inf.log

# Hedges firing post-fix?
grep -cE "HEDGE_SAME_CALL|WT_15M_SAME_HEDGE_OK|OK_wt3m=" ~/logs/ez_manage_inf.log

# Sweep alive on S1?
ssh s1-int "pgrep -fa autonomous_sweep_orchestrator; cat /home/niels/SWEEP_RUNNING"

# Sweep alive on S2? (watch for stale after mix_12 completes)
ssh s2-int "pgrep -fa _ab_sector_sweep"
```

## Known caveat for Monday

- **S2 orchestrator**: the `_ab_sector_sweep mix_12` job was the only sweep running on S2 when you logged off. No auto-chain detected. If it completes before Monday, S2 goes idle until a new sweep is queued. Not destructive, just a gap in stocks backtest data.
- **Claude session monitor** (task `b8mrdpwzt`): persistent within this session only. Dies on logout. Intended — the real autonomy is the launchd watchers + run_with_watchdog + orchestrators above.

## Added 2026-04-18 01:35 UTC

### Dashboards (now under launchd)

| Port | Service | Plist | Reads from |
|---|---|---|---|
| 5050 | Trade Analytics | `com.niels.trade-analytics` | `data/history/<acct>/*.jsonl` + `data/tradier/history/<acct>/*.jsonl` |
| 5051 | V8 Sweep Cockpit | `com.niels.sweep-cockpit` | `data/sweep_results/*.json`, cross-machine dupe DB |

Both `KeepAlive=true`. Check: `curl -sf http://127.0.0.1:5050/; curl -sf http://127.0.0.1:5051/`

### ⚠️ Sweep results look broken (Monday priority)

Cockpit top 10 all `Sharpe 14.75 / 2 trades / $0.01` (clear sampling noise — tests barely generate entries). Bottom rows also only 2 trades. Most rows are `0 trades`. Either:
1. Entry gates in V8 engine too strict post-recent-changes (check WT_COMPOSITE_SCORING_ENABLED=True impact on crypto), OR
2. Data pipeline issue (NPZ symbol coverage), OR
3. Sweep test definitions (which switches are flipped) aren't actually affecting entry rate.

First thing Monday: pick one config from the cockpit leaderboard, re-run it standalone with `backtest_v8_sweep.py`, verify trade count > 100.

## Added 2026-04-18 01:59 UTC — inf needs a second restart

Timeline mismatch: user restarted all ez_manage processes at ~00:15, but the WT_15M_SAME_HEDGE → `hedge_engine.execute_same_symbol_hedge` rewire landed at **00:26** (11 min later). Inf is still running the pre-fix WT_15M code in memory. Evidence: at 01:59:50 UTC inf logged `[NON_TRADEABLE_HARD_BLOCK] ... reason=WT_15M_SAME_HEDGE_FOR_inf:1000FLOKIUSDT_SHORT_wt1-6.8` — that reason string only comes from the OLD code path (the new path routes through hedge_engine and uses a different reason format).

**All other fixes from today ARE live** (webhook-bypass kills, reentry-monitor union, zone_action wiring, delta pos_state, hedge gate NameError, WT_COMPOSITE=True) — those landed before the 00:15 restart.

**Action Monday**: restart `ez_manage.py --account inf` (watchdog will respawn) to pick up WT_15M fix. After restart, `[NON_TRADEABLE_HARD_BLOCK] ... WT_15M_SAME_HEDGE_FOR` should stop appearing; `[HEDGE_SAME_CALL]` + `OK_wt3m=...` should appear instead.

One-liner to force the restart:
```bash
pkill -f "ez_manage.py --account inf" && sleep 2 && ps -ef | grep "ez_manage.*inf" | grep -v grep
# run_with_watchdog.sh respawns it within ~5s
```

Other accounts (ang, flz, men, fin) likely need same treatment — they were restarted at same 00:15 timestamp so also missed the 00:26 fix. Do a rolling restart one-at-a-time to avoid a gap with no account live.

## Added 2026-04-18 03:27 UTC — EMERGENCY_OVERSIZE stuck in loop

4 consecutive fires (33s apart — matches 30s cooldown) on `inf:BATUSDT_LONG` ($43.63) and `inf:ATOMUSDT_LONG` ($34.72) — both 2x+ over `max_usd=$20`. Notional unchanged each fire → reduce is being silently rejected.

Root cause (best guess, unverified): reducer at ez_manage.py:20588 passes reason `FORCE_REDUCE_OVERSIZE_...` which is NOT in `LOSS_EXIT_TECHNICAL_BYPASS` tokens (`LIQUIDATION`, `EMERGENCY_DC1H_BREACH`, `PARABOLIC_EXIT`). If these positions are underwater (likely — CRASH regime, LONGs losing), the NOLOSS gate at ez_manage.py:10720 blocks the reduce. `STRICT_NO_LOSS_ACCOUNTS=[]` in current config *should* disable this path, but something in the chain is still rejecting.

**Monday fix (one-liner):** add `'EMERGENCY_OVERSIZE'` or `'FORCE_REDUCE_OVERSIZE'` to `config.LOSS_EXIT_TECHNICAL_BYPASS`, OR instrument the reducer at line 20588 to log execute_now's actual return string so we know the reject reason.

**Manual workaround now (if wanted):** user reduces BATUSDT_LONG and ATOMUSDT_LONG manually on the exchange UI. These are safe to size down — EMERGENCY_OVERSIZE is system-defined "too big", not strategy-defined.

**Blast radius so far:** none. Positions are just oversized. No capital loss. The loop will keep firing until either price moves them to profit or a restart / manual close.
