# 🔴🔴🔴 CHURN / LOSS OFFENDERS — FLAGGED FOR URGENT REVISION — 2026-09-28 🔴🔴🔴

> **The stock block is a TEMPORARY triage stop-loss, not a permanent ban.** Each blocked name is a
> fix-queue item in **`data/reports/CHURN_FIX_PRIORITY.md`** — root-cause it, fix, verify, then
> **remove it from `config_tradier.BLACKLIST`**. Do not leave a name blocked once fixed.

Source: 7-day trade-level audit of live ledgers `data/history/{ang,fin,men,inf,flz}` (ez_ crypto)
and `data/tradier/history/{tra,trb,trc}` (tradier_ stocks), round-trip reconstruction identical to
`tools/churn_loss_guard.py` extended to a 7-day window. Full report:
`data/history/live_trade_audit_7d_20260928.md`. Numbers are realized round-trip PnL on an
average-entry basis (REDUCE/CLOSE realize; rejected/veto pseudo-fills excluded).

> **STATUS 2026-09-28: STOCK BLOCK APPLIED. Crypto deliberately NOT applied (would strand opens).
> Nothing closed** — closing at a loss is an Absolute Prohibition (DC/WT exits handle wind-down).
> User authorized `unlock config_tradier.py config.py`.
> - **Stocks (APPLIED):** the 7 names below added to `config_tradier.BLACKLIST` (`config_tradier.py:183`,
>   backup `backups/before_churn_blacklist_202609280805.py`). `is_symbol_tradeable` blocks new
>   entries/augments; exits/reduces bypass (`tradier_manage.py:23571`) so open positions still wind
>   down via DC/WT. **Takes effect on next trb/trc/tra restart** — verify before Monday open.
> - **Crypto (NOT applied):** `config.BLACKLIST_SYMBOLS` skips the symbol at the top of
>   `process_position` (`ez_manage.py:45861`) **before** exit logic → blacklisting a symbol that
>   holds an open position **strands it** (no exit/DC-stop can ever fire). fin/men/inf currently
>   hold open COTIUSDT/IOTXUSDT → blacklisting would trap them. Crypto churn is −$26/7d (noise), so
>   the harm outweighs it. Crypto stays flagged only; use the side-level guard, never config.py, and
>   only on a flat symbol.
> Markets are closed (Sun 2026-09-28); nothing bleeds before Monday open.

## STOCKS — ✅ APPLIED to config_tradier.BLACKLIST (net-losing AND continuously losing)

| account:SYM_SIDE | 7d net $ | exits | win% | loss streak | churn/60min | pattern |
|---|---:|---:|---:|---:|---:|---|
| **SLV_LONG** (trb,tra) | **−1,423.68** | 22 | 0% | 21 | 10 | re-open → `ULTIMATE_DC_4h_HARD_STOP_LONG` → re-open. 606 OPEN attempts in 7d. |
| **COPX_LONG** (trb) | **−497.92** | 19 | 0% | 19 | 8 | every exit a loss |
| **PYPL_SHORT** (trb,trc) | **−399.41** | 35 | 29% | 16 | 8 | high-frequency churn, net bleed |
| **QBTS_SHORT** (trb,trc) | **−351.58** | 41 | 37% | 20 | 4 | 41 exits, ends on 20-loss streak |
| **DINO_LONG** (trb) | **−253.87** | 27 | 7% | 0 | 5 | 7% win rate — structurally wrong side |
| **RBLX_LONG** (trb,trc) | **−176.18** | 28 | 11% | 7 | 5 | 11% win rate |
| **MPC_LONG** (trb,trc) | **−172.93** | 16 | 19% | 6 | 2 | net bleed |

**APPLIED:** `config_tradier.BLACKLIST = ['SLV','COPX','PYPL','QBTS','DINO','RBLX','MPC']`
(`config_tradier.py:183`). `is_symbol_tradeable` checks `self.blacklist` first, immune to the ~10min
`tradier_rankings` regen. Blocks BOTH sides — verified all 7 traded only the losing side in the last 7d,
so no active winner is lost (RBLX_SHORT is in the universe but did not trade this week; re-add it alone
after review if wanted). Re-add any name via `config_tradier.py:183` after the root cause is fixed.

## STOCKS — flag for revision, do NOT auto-block (borderline / mixed)

- `VLO_LONG` −381.53 (33 ex, 39% win) — losing but not one-sided; review exit timing.
- `PSX_LONG` −129.70 (27 ex, 44% win), `RS_SHORT` −99.12 (31 ex, 39% win) — near coin-flip, fee/churn drag.
- `LSCC_SHORT` −98.97 (2 ex), `NEM_LONG` −30.80 (1 ex), `TSM_LONG` −12.69 (2 ex) — **low sample**, one bad trade, not churn.

## CRYPTO (ez_) — micro-size ($25–80 notional); net −$25.68/7d, within noise

Clear churners in `tradeable_keys.json` (flag; block only if pattern persists — $ impact negligible):

- `ang:EGLDUSDT_LONG` — 13-loss streak, churn 9/60min, 7% win (net −0.61)
- `fin+men:COTIUSDT_SHORT` — 20 exits, churn 6, 41% win (net −17.85 combined, worst crypto by $)
- `flz:DASHUSDT_SHORT` — 7-loss streak (net −4.59)
- `inf:ACEUSDT_SHORT` — 35 exits, churn 8 (net +0.10 — pure churn, no edge)
- `ang:MINAUSDT_LONG` (17% win), `inf:APEUSDT_SHORT` (12% win), `fin:SNXUSDT_SHORT` (9% win) — low-edge

Crypto durable block lever = `tools/churn_loss_guard.py --enforce` (edits `tradeable_keys.json`), BUT its
default 24h window found 0 offenders on Sunday — see report improvement #1 (extend window, cover stocks,
verify `ez_positions_service` does not re-add blocked keys).

---
*Written 2026-09-28 by live-trade audit. Blocked keys stay flagged until a human fixes the root cause and
re-enables. Root causes: (1) entries fire on sym_sides with `trades:0` per_sym profile via gate-bypass paths;
(2) DC 4h hard-stop churns re-opened losers. See report §Improvements.*
