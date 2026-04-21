# SESSION 2026-04-21 — Claude Responsibilities (live, must be applied before every action)

**Purpose**: single sheet of everything the user has told me in this conversation.
If I forget any of these rules the user loses time and money. Reread before every step.

Path: `/Users/niels/Documents/binance/SESSION_2026-04-21_RESPONSIBILITIES.md`

---

## 0. Today's non-negotiable GATES

- **Sharpe > 1.5** (per-trade pool) is the ONLY Sharpe allowed to justify a Big Test.
- **Accumulated gain must be ≥ 10× Buy-and-Hold** for the same symbol universe + window, OR the config is trash — the effort/compute/$ cannot be justified otherwise.
- **Minimum sample**: ≥48 crypto syms × >1yr OR ≥100 stock syms × >1yr; **≥30 trades per symbol** (~1500 trades at 48 syms).
- **Pool Sharpe ONLY** — `mean(all_trade_pnl) / std(all_trade_pnl)` across ALL trades of ALL syms. NEVER per-symbol Sharpe. NEVER single-symbol. NEVER annualize by sqrt(N).
- **Open losers must be mark-to-market** in the Sharpe denominator (NOLOSS blocks mid-sim exits; it does NOT filter negatives out of stats).

## 1. B&H is the reference line — print it on EVERY report

- Every test result MUST include the B&H benchmark (gain + dd) for the same corpus + window.
- A strategy that doesn't beat B&H is TRASH. A strategy that gets <10× B&H gain is NOT WORTH THE EFFORT.
- Reference B&H numbers I have as of today:
  - **Tradier 128sym × 2yr (2024-01-01 start)**: B&H acc_gain = +9077%, avg +70.9%/sym, basket DD 2.56%. → Target = **+90,770%**.
  - **Tradier 24sym × 1yr (2024-06-01)**: B&H acc_gain = +1620%, avg +67.5%/sym, basket DD 8.64%. → Target = **+16,200%**.
  - **Crypto 50sym × 4yr (2022-01-01)**: B&H acc_gain = **−3771%** (basket of dying alts). Since BH is negative, target for crypto is **absolute gain > 10,000% AND Sharpe > 1.5** on ≥48 syms.

## 2. FORBIDDEN config flips (they create fake 2-trade Sharpe 14.7 frauds)

These flags cap PnL at ~0.5% and generate near-100% WR artifacts NOT present in real live code (`ez_manage`, `tradier_manage`). Any sweep that flips them is lying.

- `AUGMENT_PT_ENABLED`, `AUGMENT_PT_PCT`
- `PARTIAL_EXIT_ENABLED`, `PARTIAL_EXIT_PCT`, `PARTIAL_EXIT_FRAC`, `PARTIAL_TRAIL_ARM_PCT`, `PARTIAL_TRAIL_FLOOR_PCT`
- `PARTIAL_PROFIT_LOCK_ENABLED` (+`_GAIN_PCT`, `_ARM_GAIN_PCT`, `_FRAC`, `_GAIN_PCT_TRADIER`, `_ARM_GAIN_PCT_TRADIER`, `_FRAC_TRADIER`)
- `CYCLE_TP_TIERED_ENABLED`, `CYCLE_TP_PCT`, `CYCLE_TP_TIERED_FRAC`, `CYCLE_TP_CONDITIONAL_EXIT`
- `ACCOUNT_TP_PCT`
- `AUGMENT_WT_D_AUTO_CLOSE_ENABLED`, `AUGMENT_WT_4H_AUTO_CLOSE_ENABLED`

Implementation: `FORBIDDEN_FLIPS` list is active in `autonomous_search.py`. v8_quick_sweep.py / tiers config must ALSO enforce. Any sweep framework that fails to reject these is BROKEN.

## 3. MIN-TRADES floor — reject 2-trade fluke winners BEFORE they enter result files

- **MIN_TRADES_PER_SYMBOL = 30** (user rule, repeated 13+ times)
- For 48-sym sweep → reject results with total trades < 48 × 30 = **1,440 trades**
- For 24-sym sweep → reject < 720 trades
- For 12-sym sweep → reject < 360 trades
- For 6-sym sweep → reject < 180 trades
- Anything below floor is INVISIBLE — don't rank, don't print, don't save.
- Applies to CSV output, dashboards, top-N lists, winner JSONLs. Everywhere.

## 4. MONITOR WHILE TESTING (user rule added 2026-04-21)

Every test/sweep/search I launch must self-monitor:
- After N configs (N=50 for short runs, 500 for long), sanity-check the output distribution.
- If >X% of results have trade-count < floor → STOP sweep, fix sampler or filter.
- If top-Sharpe configs have suspiciously tight outlier (e.g. Sharpe > 3 on < floor trades) → STOP, those are flukes.
- Dashboards must always apply the MIN_TRADES floor before showing rank tables.
- Reject-count and pass-count must be logged alongside total configs.

## 5. Never warn the user about failures — produce or shut up

- User directive: "do not warn me until you have a series of settings both for stocks and crypto that reach the goal".
- Status updates ONLY when explicitly pulled (scheduled wakeup, user asks).
- Failure to reach Sharpe > 1.5 after N iterations is NOT a result — it means try a DIFFERENT approach (make new scripts, rewrite engine functions, change signal primitives).

## 6. Engine-tier architecture (CLAUDE.md 2c, recap)

- **Tier 1 — v8_quick_engine.py (vectorized)**: 24/7 swarm feeder. Output is shortlists ONLY. NEVER a decision input.
- **Tier 2 — backtest_v8_engine.py**: real code path (`check_entry_candidates_for_account`, `check_exit_candidates_for_account`, `execute_trade_action`, `MultiAccountTradeManager`). Top Tier-1 candidates run here on 12→48→128 syms.
- **Tier 3 — Full production**: only configs that clear Tier 2 with Sharpe > 1.5 + gain > 10× B&H on ≥48/128 syms + ≥30 trades/sym.

## 7. Server roles (CLAUDE.md sandbox-parity)

- MacBook = live + source of truth. All edits here first.
- S1 (157.180.125.52) = CRYPTO backtest only.
- S2 (204.168.181.211) = TRADIER backtest only.
- After ANY edit to a file in the 6-critical list: rsync --existing --update to S1 + S2 + md5 verify.
- NEVER kill protected sweeps (`/home/niels/SWEEP_RUNNING` lock / SCREEN session `sweep48h`/`stock_v2_tradier`).
- NEVER run live trading on servers. NEVER run push.py.

## 8. Strategy work not to revert

- PPL v2 wired in tradier_manage.py but config default flipped FALSE today (2026-04-21) after tradier sweep showed −10% gain vs PPL OFF baseline.
- `wt_dc_hierarchy.py` rewritten 2026-04-21 as true state-machine cascade (not parallel alignment). Smoke shows cascade-exit bit-identical to dumb wt1_15m; cascade-entry destructive. Ceiling ~0.98 Sharpe on 50sym × 4yr crypto.
- `SIMPLE_WT15M_EXIT_ONLY_ENABLED` is the "dumb exit" placeholder while the full rewrite is done.
- REENTRIES still the main loser — the `reentry_if_momentum` flag from wt_dc_delta.py's `_run_redzone` is in scope but not yet wired into the cooldown bypass path for crypto.

## 9. Reporting format — strict

Every result I report to the user must include, minimum:
```
  strategy: <label>
  mode: crypto|tradier
  corpus: <N syms × <T> years (start=<date>)
  pool_sharpe: <x.xxx>          (floor: ≥1.5 for acceptance)
  accumulated_gain_pct: <xx>%   (vs BH: <yx>× ratio)
  max_dd_pct: <xx>%
  trades: <N>                   (floor: ≥30/symbol)
  BH_reference: gain=<y>% dd=<z>% sharpe_per_bar=<s>
```

## 10. Things to NEVER do again

- Never cite Sharpe 3.54 on 143 trades (tiny-sample lie).
- Never call sharpe_annual ("2.52 baseline") a Sharpe — it's sharpe × sqrt(N).
- Never run a sweep that flips any `FORBIDDEN_FLIPS` flag.
- Never report per-symbol Sharpes without the pool-Sharpe companion.
- Never restart or create orders outside execute_now().
- Never edit on servers. MacBook first, then rsync.
- Never auto-revert or drop strategy wirings.

---

**If I skip any of the above, flag it loudly to the user next message and correct before proceeding.**
