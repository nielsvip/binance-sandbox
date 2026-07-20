# TRADIER DEEP AUDIT — FULL REPORT & RESULTS (2026-07-03)

31-agent investigation, every critical finding adversarially verified (15 CONFIRMED / 8 PARTIAL-corrected / 1 sub-claim refuted+corrected). All fixes below are LIVE and verified. Market closed today (July-4 observance) — live-behavior confirmation lands Monday 2026-07-06 13:30 UTC.

## A. RESULTS — what changed today (with proof)

| # | Action | Proof |
|---|--------|-------|
| 1 | **Churn engine OFF in live** — trb/trc restarted; procs had run `WT_3M_FORCE_OPEN=True` from memory since before the Jul-2 16:33 config flip | New PIDs 18485/19296; **0** `WT_3M_FORCE_OPEN` log lines post-restart (was ~34/min); `GOLDEN_RULE_MIN_IND=5` now in effect |
| 2 | **Short universes rebuilt from positive per_sym keys** (user mandate) | `symbols_trb_short.json` 50→**70**, `symbols_trc_short.json` 52→**89**; every added key wsharpe>0, gain>0, trades≥30; NON_SHORTABLE excluded; backups in `backups/before_short_universe_persym_*` |
| 3 | **Systematic test matrix running 24/7 on S1** — replaces the no-op-riddled queue (19/29 tradier + 5/8 crypto params tested before had ZERO engine reference = 2/3 of compute wasted) | Banners: `sweepable=749` (tradier) / `sweepable=629` (crypto); manifests `s1:binance-sandbox/data/ofat_manifest_*.json`; per-trade per-symbol sheets accumulate at `s1:~/logs/ofat_{mode}_trades/<PARAM>__<VALUE>/cell__<SYM>.jsonl` |
| 4 | **Matrix restarted CLEAN on the corrected config** — old baseline/cells were computed under force-open=True (poisoned); mixing would fake every delta | Old outputs archived to `s1:~/logs/ofat_archive_prechurn/`; fresh `__BASELINE__` recomputing now |
| 5 | **RAM unblocked** — killed `vec_top_combo_validator` (6.2GB; leaderboard byte-static 7+ days) + disabled its `*/30` cron; worker gate 8000→6000MB | avail 3.3→7.6GB; runners spawn workers again (was stalled since Jul-2 17:40Z) |
| 6 | **trb_review baseline overlay fixed** — page simulated with a Jun-26 config (122 keys) instead of the current baseline | Current Jul-2 93-key `active_config.json` copied to `s1:/home/niels/binance/data/hourly_reconfig/trb/` |
| 7 | **Coverage ledger created** — ends re-testing the same ~20 celebrity switches | `data/_knob_audit/knob_coverage_20260703.csv` (3,798 rows, every knob classified) |
| 8 | Engine-reachability CORRECTION: Tier-2 imports live modules (`ez_manage`:171, `ez_reentry`:176, `tradier_manage`:5588; overrides via `setattr(config,…)`) — so live-module knobs ARE sweepable | Only **357 tradier + 592 crypto** knobs truly unreachable (ez_positions_quick / btc_loop / daemon-process reads) — not "1,900" |

## B. FINDINGS (verified)

### B1. Ledger /history/ tra/trb/trc — polluted + self-destructing [CONFIRMED]
- **97% of trb's 9,564 OPENs are phantom** `SYNC_DETECTION` heartbeats every ~47s (`tradier_manage.py:14116/:14177`, fallback `:11913`). CAT: 1,101 identical qty=2 @1060.90 opens = impossible $2.3M on $70k. trc 85%, tra 90%.
- **Rotation destroys real history**: 200KB cap + prior `.bak` unlinked (`:11923-11929`) → flooded symbols rotate ~every 19h; 14 trb symbols already lost older real fills permanently.
- Ledger has **no fee field**; FIFO PnL uncomputable (phantom opens). Only 551 `BROKER_CLOSE` rows authoritative. SNDK real realized: **−$267.14**.
- **tra NOT parked**: real `WT_3M_FORCE_OPEN` buys Jun 8/16/22 (GFV-limited account — guard scope review needed).

### B2. Churn / SNDK [CONFIRMED — it really traded that way]
- "336 trades" = exit FILLS (235 trb + 101 trc); actual round-trips **17**. Jun-30: 13 one-share round-trips, PnL $0–$31 each, median close→reopen **2.9 min**, REENTRY 2m24s after exit at same price.
- Loop 1: standing-state force-open (`:2589-2657`, fires every eval) → razor 4-bar 5m DC stop → `R1_DC_LOW4_EMERGENCY` closes at ANY age (`_r1_age_min=0.0 # kept for log only`, `:2226`); reason bypasses 7/10 gates incl. 8/day cap (`_ot_emerg :3715`).
- Loop 2: CLOSE-refire — unconfirmed closes resubmitted ~2min & each logged as a trade (TXN: 241 "closes" of the same 2 shares).
- Cost: commission $0 but re-buys median **+$4.61 (~0.21%)** above prior sale.
- R1 knob truth: `R1_DC_LOW4_3M_EMERGENCY_ENABLED=True` since 2026-05-29 (the "_3M_" is a crypto-borrowed label; stocks fire on 5m). Crypto moved to wide 20-bar `dc_low_3m` on 2026-07-01; **stocks still on tight 4-bar `dc_low4_5m` — the pending A/B**. OFAT evidence: R1 OFF → pool_sharpe **−0.0547 → +0.0101** (Δ+0.0649, trades 19,413→14,512, n_syms=16, DIAGNOSTIC).

### B3. trb_review accuracy [CONFIRMED]
- **Live layer trustworthy**: API == ledger exactly (SNDK 1092/1092, NVDA 1658/1658, GLD 359/359, XOM 376/376); round-trip reconstruction reproduced. Stacked "+$0" labels = faithful churn, not a render bug.
- Card tag counts fills not round-trips (rename to "N exit fills" or compute rounds).
- BT overlay was simulating a stale Jun-26 config → **fixed today** (see A6). Overlay uses the vec per_sym engine — parity caveat applies to yellow markers.
- Reconstruction ignores BROKER_CLOSE/GHOST_CLOSE (218 SNDK exit fills dropped, mostly early-ledger dupes); SNDK's 2 biggest losers (Mar 24/25) predate first chart bar.

### B4. Shorts — dead since 2026-05-13 [CONFIRMED; partly FIXED today]
Zero `sell_short` since 5/13 while signals fire 4,774/day. Kill chain:
1. **DG_10 data bug (the big one)**: needs D or 4h actively bearish, but live indicator snapshot has **ZERO `open_D/close_D/open_4h/close_4h` keys and NO `sma_200_15m`** → bias always 0, bypass can never fire → unconditional short-block. *(needs unlock — see C)*
2. OVERTRADE counts attempts not fills (`:3729`) — blocked shorts burn their own 8/day cap. *(needs unlock)*
3. **Universe mismatch — FIXED today**: 26/32 trb + 25/33 trc positive-wsharpe _SHORT keys weren't in `symbols_{acct}_short.json` (the SOLE permission, `:10392-10406`). Now they are.
4. COUNTER_TREND + GR consensus mop up the rest (these are backtested quality gates — leave until matrix data says otherwise).

### B5. per_sym mandate — massively violated [CONFIRMED]
- Live configs content-frozen since **2026-06-15**: daily updater fails every symbol (`allow_pickle=False`, 2,489 errors) while mtimes refresh — staleness masked. *(needs unlock)*
- Jun-29 sweeps: trb 121/122 positive, trc 143/168 → **ZERO promoted** (gate `pool_sharpe>0.5` at `per_sym_sweep_100.py:507`, not >0). *(one-line edit + user ruling on sample floor vs mandate)*
- 69/121 trb + 87/142 trc positive keys not tradeable live (universe) — short side fixed today; LONG-side gaps remain (63 trb keys e.g. CDE_LONG ws=0.198 +500.8%/1159tr are only in the *short* file).
- `persym_final_book.json` (2026-05-31) is first-priority and stale — force-disables ASTS_SHORT (+286%, 1258tr) and MP_SHORT (+357%, 1502tr) against fresher data.
- Negative-key violation: trc COP_LONG (7D wsharpe −0.012) still tradeable.

### B6. Sandbox test results — stagnation, now rebooted [CONFIRMED]
- Real sweep pipeline dead since Jun-27 (100% OOM errors; guardian cron is a pgrep self-match no-op). S1 hard-reboots daily 13:05 UTC (root crontab). Central DB rebuilt (807K runs, was 1.96M) with 8× dup rows.
- Old OFAT: 2/3 of tested params were engine no-ops; best real find was the R1 row above. Historical stocks baseline **0.5823 unbeaten**; faithful baseline was **negative** because it included the force-open churn the live system was running.
- **Now**: clean matrix (749+629 real params) grinding on the corrected config with fresh baseline. Every future row is a real test.

## C. SUGGESTED EDITS (need "unlock <file>"; backtest-first where marked)
1. `tradier_manage.py` — SYNC_DETECTION dedupe (skip history write if identical OPEN within sync interval) + archive rotation instead of `.bak` unlink + fix `_resolve_entry_reason` so real reasons reach the ledger. *Ledger must become truthful before any live-vs-backtest validation.*
2. `tradier_indicators.py` (or adapter) — emit `open_D/close_D/open_4h/close_4h` + `sma_200_15m` → makes DG_10 the designed gate instead of a short-killer.
3. `tradier_manage.py:3729` — OVERTRADE counter on confirmed submission, not attempt.
4. `tradier_manage.py` — CLOSE-refire: check prior order_id status before resubmit; append to history only on confirmed fill.
5. `config_tradier.py` — after matrix data: `R1_USE_DC_4BAR` False (20-bar, crypto-proven) [backtest-first: matrix covers it]; `DG_REPEAT_OPEN_PER_DAY_MAX` 60→8; drop `WT_3M_FORCE_OPEN` from `_ot_emerg`.
6. `tools/per_sym_sweep_100.py:507` — promote gate >0.5 → >0 (keep ≥30-trade floor) + delivery merge into live configs; fix `allow_pickle` in `tradier_hourly_reconfig.py`; rebuild final book from Jun-29 results.
7. Force-open REWRITE (user spec): fire only when 5m WT cross price > previous cross price (long; mirrored short) + retest of cross price confirms. Knobs: `WT_FORCE_OPEN_HH_CROSS_ONLY/_RETEST_ENABLED/_RETEST_BAND_PCT/_RETEST_MAX_BARS`. Prereq: repair engine force-open parity (fires ~0 in sim vs thousands live), then Tier-2 A/B OFF vs old vs rewrite.
8. Ops decisions: keep/remove daily 13:05 S1 reboot (root crontab); guardian cron pgrep self-match fix; DB ingest dedupe.

## D. WHAT TO WATCH
- **Monday 13:30 UTC**: shorts finally attempt entries on the 57 new keys (DG_10 will still block most until fix C2 — count `DG_10` log lines to size the remaining blocker); churn stays dead (no `WT_3M_FORCE_OPEN` lines, SNDK fills normal).
- **Daily**: `ssh s1-int 'wc -l ~/logs/ofat_*_screen.csv'` — rows must grow; trades sheets in `ofat_*_trades/` accumulate the per-symbol × per-setting evidence.
- First fresh `__BASELINE__` (corrected config, no churn) lands within hours — that number replaces −0.0547 as the honest starting line.
