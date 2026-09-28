# CHURN FIX PRIORITY QUEUE — not a permanent blacklist — 2026-09-28

## ⚠️ VERIFICATION 2026-09-28 08:4x (binance-a4) — fixes #1/#2 applied by a peer session; fix #1 has a WHOLE-BOOK-FREEZE regression

A peer session implemented fixes #1 (per_sym `_opens_exposure` gate), #2 (DC hard-stop cooldown,
`config_tradier.py:72`), plus a new per-side `VIGILANCE_GUARD` (`data/vigilance_blocks_tradier.json`,
auto-blocks a sym_side after `VIGILANCE_CONSEC_LOSSES=2` losing closes). Both files **compile OK**.
VIGILANCE is the right durable loss-circuit-breaker and largely supersedes the manual BLACKLIST below.

**BUT fix #1 + current per_sym data = new-entry freeze.** Verified empirically: every one of the
**248/248** stock per_sym entries is bare `{overrides, winning_tag:'clean_ONLY_CROSSES_v33', trades:0}`
— no `wsharpe`/`pool_sharpe`/gain. `PER_SYM_LIVE_GATE`'s block condition `(trades==0 and w is None)`
is therefore TRUE for **all 248**, so gating flat-opens (fix #1's `_opens_exposure`) blocks **every
fresh stock open Monday**. The BEST-matrix fallback does NOT save it: it only runs when the per_sym
entry is missing (all are present, just bare), the filename matcher `"{base}_{side}_bh"` won't match
strategy-infix names (`AAPL_SEQ_LONG_bh…`, `AAPL_WF_LONG_…`), and most matrices show gain<bh
(MSFT gain4.13<bh6.14, NVDA/AMZN negative) so "beat buy-hold" blocks them anyway.

**Decision needed (strategy, not a mechanical patch):**
1. Populate `per_sym_active_config_stocks.json` with real `trades`/`pool_sharpe`/`acc_gain_pct`/`bh_pct`
   from BEST/pilot so the gate can discriminate good vs bad — the proper fix; OR
2. Make `PER_SYM_LIVE_GATE` treat a *bare/unjudgeable* per_sym entry as "no entry" → fall through to
   the BEST-matrix check, AND fix the BEST matcher to handle strategy infixes (`_SEQ_`/`_WF_`); OR
3. Short-term un-freeze: `PER_SYM_GATE_FLAT_OPEN_ENFORCE=False` (`config_tradier.py:76`) reverts fix
   #1's broadening, and rely on VIGILANCE + DC-cooldown + BLACKLIST for churn control until (1)/(2).

Not edited by binance-a4 — peers are actively editing `tradier_manage.py`/`ez_manage.py`; avoiding a
collision. Whoever owns that edit: apply (1) or (2), or ship with the kill switch at (3).

## LEVER 3 done + LEVER 1 provenance/blockers (binance-a4 2026-09-28 08:5x)

- **Lever 3 APPLIED:** `config_tradier.PER_SYM_GATE_FLAT_OPEN_ENFORCE = False` (`config_tradier.py:76`,
  backup `backups/before_flatopen_killswitch_202609280848.py`, compiles). Un-freezes stock flat-opens
  (reverts fix #1's broadening only); VIGILANCE + DC-cooldown + BLACKLIST still gate churn.
- **METHODOLOGY (USER 2026-09-28):** the ONLY valid promotion/validation window is **30D minimum,
  confirmed by 365D**. **7D is preliminary Mac-only (`--dry-run`/`--allow-mac`) scratch and must NEVER
  consume S1/worker CPU.** So `*_7d_progress.json` are NOT a BEST source — ignore them, and any 7D job
  on S1 is CPU waste to stop.
- **Lever 1 (repopulate per_sym from LATEST 30D BEST) — source is being generated NOW:**
  - Promoted BEST xlsx `SPREADSHEETS/BEST/STOCKS_*` are **Sep 14–19** (stale prior set).
  - The live 30D sweep (`v15_pilot … --window-days 30 --vector-only`, herd `tools/v15_local_herd.py`,
    supervisor `tools/v15_mega_supervisor.sh`) is writing fresh 30D matrices to
    `SPREADSHEETS/V15_V16_CELL_BY_CELL/*_30d_matrix.xlsx` **today** (AAPL_SHORT 08:58, SPY_LONG 08:51,
    NVDA_L/S 08:46 …). These, after 365D confirmation, are the correct BEST source.
  - **Blocker still standing:** the promotion pipeline that writes `per_sym_active_config_stocks.json`
    (`tools/dc64_apply_promotions.py`, `tradier_hourly_reconfig.py`) emits **only `overrides` +
    `winning_tag`** — never `gain`/`bh`/`trades`/`pool_sharpe`. That omission is *why* every entry is
    bare and the gate blocks all 248.
  - **To finish lever 1 (in order):** (1) let the 30D stock sweep finish the universe + 365D confirm;
    (2) have the promotion pipeline copy 30D `gain`/`bh` + 365D-confirmed `trades`+`pool_sharpe`
    (`metrics_guard` on per-trade returns) into per_sym; (3) then set
    `PER_SYM_GATE_FLAT_OPEN_ENFORCE=True`. Hand-populating metrics outside this pipeline = NO-LIES
    violation — NOT done.
  - **CPU-waste to clear:** ~15 duplicate hung `v15_pilot … UUUU_LONG … --window-days 30` procs on S1
    (0% CPU, ~15min) — stragglers from the herd-init bottleneck; reap so slots free for real 30D work.

---


These 7 stock sym_sides are **temporarily** in `config_tradier.BLACKLIST` (`config_tradier.py:183`)
**only to stop the bleed** while each root cause is fixed. This is a **work queue**: fix → verify →
**remove the name from `config_tradier.BLACKLIST`** → re-observe. A name must not sit blocked once
fixed. Blocking is a stop-loss, not a verdict on the symbol.

- Block behavior: `is_symbol_tradeable` refuses new entries/augments; exits/reduces bypass
  (`tradier_manage.py:23571`) so any open position still winds down via DC/WT (never force-closed).
- Effect on trade P&L if fixed: the top-7 losers = **≈ −$3,375 / 7d**; the stock book netted only
  +$710, so fixing these ≈ **5× weekly net** on the same winners.
- Shared root cause (see report §5): entries fire on a side whose per_sym profile is `trades:0`
  (never validated) via reclaim / ladder-parity / REENTRY paths that skip `PER_SYM_LIVE_GATE`
  (`tradier_manage.py:24578`); the position then gets DC-4h-hard-stopped at a loss and re-opened.

| # | sym_side | 7d net $ | exits | win% | streak | root cause to fix | unblock criteria (remove from BLACKLIST when ALL true) |
|--:|---|--:|--:|--:|--:|---|---|
| 1 | **SLV_LONG** | −1424 | 22 | 0% | 21 | 606 open-attempts → `ULTIMATE_DC_4h_HARD_STOP` loop; per_sym `trades:0`, re-opens via bypass path | per_sym SLV_LONG has validated profile (trades≥30, pool_sharpe>0.2) **or** entry-leak fix lands; then 5-trade paper check net≥0 |
| 2 | **COPX_LONG** | −498 | 19 | 0% | 19 | same open→DC-stop loop; 0% win | per_sym validated profile **or** entry-leak fix; paper 5-trade net≥0 |
| 3 | **PYPL_SHORT** | −399 | 35 | 29% | 16 | high-freq churn, net bleed; per_sym `trades:0` | entry-leak fix + DC re-entry cooldown; paper 5-trade net≥0 |
| 4 | **QBTS_SHORT** | −352 | 41 | 37% | 20 | 41 exits, ends 20-loss streak; per_sym `trades:0` | entry-leak fix + DC cooldown; paper 5-trade net≥0 |
| 5 | **DINO_LONG** | −254 | 27 | 7% | 0 | 7% win — structurally wrong side/timing | confirm which side/tf per_sym validates; only re-add the validated side |
| 6 | **RBLX_LONG** | −176 | 28 | 11% | 7 | 11% win; RBLX_SHORT untraded (not blocked-intent, collateral only) | validated per_sym profile for RBLX_LONG; re-add RBLX_SHORT alone now if wanted |
| 7 | **MPC_LONG** | −173 | 16 | 19% | 6 | net bleed | entry-leak fix; paper 5-trade net≥0 |

## The two code fixes that clear most of this queue at once (need `unlock tradier_manage.py ez_manage.py`)
1. **Close the per_sym entry-leak** — make reclaim / ladder-parity / REENTRY branches re-check
   `PER_SYM_LIVE_GATE`, so a `trades:0` side cannot be re-opened by any path. Fixes #1–4,7 at source.
2. **DC-4h-hard-stop re-entry cooldown** — after `ULTIMATE_DC_4h_HARD_STOP` closes a name, gate the
   same-side re-open for N hours / until DC regime flips. Kills the open→stop→open loop.

After (1)+(2) are live and verified, remove each name from `config_tradier.BLACKLIST` and watch its
ledger for one session; if churn recurs, re-block and re-open the queue item.

## Crypto (NOT blacklisted — would strand open positions)
`config.BLACKLIST_SYMBOLS` skips the symbol at the top of `process_position` (`ez_manage.py:45861`)
before exits, so it strands open positions — never use it for crypto churn. Crypto churn is
−$26/7d (noise). Track side-level via `tools/churn_loss_guard.py` (see report improvement #3) — flag
only: `COTIUSDT_{L,S}`, `IOTXUSDT_{L,S}`, `EGLDUSDT_LONG`, `DASHUSDT_SHORT`, `SNXUSDT_SHORT`.
