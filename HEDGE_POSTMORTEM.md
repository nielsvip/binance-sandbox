# HEDGE ENGINE POST-MORTEM: March 6, 2026

## TL;DR
The hedge engine ran for ~4.5 hours (17:56 - 22:20 UTC) and created a **deadlock loop** across 3 accounts: it opened positions it could never close because STRICT_NO_LOSS_BLOCK prevented closing at ANY loss. The system then looped **21,358 times** trying to close positions, failing every time, while positions bled. When STRICT_NO_LOSS_BYPASS finally kicked in, some positions had lost up to **-7.20%**.

---

## Timeline
| Time | Event |
|------|-------|
| 17:56 | Hedge engine activates on men, ang, flz |
| 17:57-18:00 | First hedge opens: TRXUSDT_SHORT, LRCUSDT_SHORT (men), STGUSDT_SHORT (ang) |
| 17:57 | First EMERGENCY_REDUCE fires — LRCUSDT_SHORT reduced at -$0.18 loss (men) |
| 18:06 | TUSDT_LONG churning begins (men) — opened/closed 28+ times |
| 18:12 | SKYAIUSDT_LONG opened (ang) — closed 47 sec later, reopened, closed, reopened... |
| 18:38 | STRICT_NO_LOSS_BLOCK starts blocking hedge kills (ADAUSDC_SHORT -0.07%, men) |
| 18:44-22:12 | **4-hour deadlock**: ~21,358 close attempts blocked |
| 19:49-19:52 | ANG MORPHOUSDT_SHORT blocked at -0.27% to -0.47% — retried every 15-20 sec |
| 22:01 | STRICT_NO_LOSS_BYPASS starts allowing closes on worst positions |
| 22:20 | FLOWUSDT_SHORT finally closed at -7.20% (ang) — worst single loss |

---

## Damage Summary

### STRICT_NO_LOSS_BLOCK Events (Close Attempts Blocked)
| Account | Hedge-related blocks | Non-hedge blocks | Total blocked |
|---------|---------------------|-------------------|---------------|
| **ang** | 12,900 | 12,474 | **13,298** |
| **men** | 3,580 | 3,717 | **3,985** |
| **flz** | 2,973 | 3,786 | **4,075** |
| **Total** | | | **21,358** |

### Symbols Stuck in ang (Top 10 — each number = a blocked close attempt)
| Symbol | Blocked attempts |
|--------|-----------------|
| C98USDT_SHORT | 523 |
| CELOUSDT_SHORT | 436 |
| BELUSDT_LONG | 371 |
| PAXGUSDT_SHORT | 297 |
| ACHUSDT_LONG | 291 |
| UMAUSDT_LONG | 287 |
| LRCUSDT_SHORT | 283 |
| TRXUSDT_LONG | 283 |
| MORPHOUSDT_SHORT | 275 |
| EGLDUSDT_SHORT | 264 |

### Symbols Stuck in men (Top 10)
| Symbol | Blocked attempts |
|--------|-----------------|
| ETHWUSDT_SHORT | 146 |
| 1000BONKUSDC_SHORT | 128 |
| EGLDUSDT_SHORT | 124 |
| GTCUSDT_SHORT | 122 |
| 1000FLOKIUSDT_SHORT | 122 |
| WLDUSDC_SHORT | 119 |
| CRVUSDC_SHORT | 115 |
| FETUSDT_SHORT | 113 |
| OPUSDT_SHORT | 112 |
| DOGEUSDC_SHORT | 109 |

### Hedge Execution Stats
| Account | Hedge opens | Hedge kill attempts | Kills blocked | Actually killed |
|---------|-------------|--------------------|--------------|-----------------|
| **ang** | 26 | 1,136+ | 12,900 | ~78 (instant kill + bypass) |
| **men** | 54 | 42+ | 3,580 | ~23 (success) + 14 (instant) |
| **flz** | 2 | 30+ | 2,973 | ~5 (kill) + 5 (instant) |

### Worst Bypass Losses (when STRICT_NO_LOSS_BYPASS finally allowed close)
| Loss % | Symbol | Account |
|--------|--------|---------|
| -7.20% | FLOWUSDT_SHORT | ang |
| -6.09% | (unknown) | ang |
| -6.03% | (unknown) | ang |
| -4.84% | (unknown) | ang |
| -3.90% | (multiple) | ang |

### EMERGENCY_REDUCE / FALLBACK_REDUCE Disaster (FLZ)
FLZ had **1,364 EMERGENCY_REDUCE** attempts and **1,269 FALLBACK_REDUCE** attempts. Results:
- `EXECUTION_FAILED`: **615** (97.6%)
- `SUCCESS`: **7** (1.1%)
- `BLOCKED_LOW_GAIN_DRAIN_PROTECTION`: **7** (1.1%)

FLZ was in a continuous loop trying to reduce positions across 12 symbols (BTCDOMUSDT 429x, GTCUSDT 350x, ENJUSDT 314x...) and failing almost every time.

### TUSDT Churning (men account)
TUSDT_LONG was opened **28 times** from scratch and closed/reduced **22 times** in 4 hours. Every cycle: open $6-$27 → immediately lose → try to close → blocked or closed at $0 gain → reopen. Pure fee destruction.

### SKYAIUSDT Churning (ang account)
SKYAIUSDT_LONG was opened 6 times and closed 6 times. Example cycle:
1. 18:12 — Open $13.87
2. 18:31 — Partial reduce (gain +$0.48)
3. 18:37 — Full close (gain -$0.02)
4. 19:13 — Reopen $10.27
5. 19:14 — Close 47 seconds later (gain $0.00)
6. 19:35 — Reopen $6.62
7. 19:39 — Close 4 minutes later (gain $0.00)

Net result: fees paid on ~$60 of churn for $0.46 total gain.

---

## Root Cause Chain

### 1. THE DEADLOCK (Primary cause of damage)
```
Position losing → Hedge scanner triggers →
  Opens counter-position → Counter-position also loses →
  Try to kill hedge → STRICT_NO_LOSS_BLOCK says NO →
  Both positions bleed → Loop forever every 5-15 seconds
```

The hedge engine was designed to be **disposable** (open fast, kill fast if wrong). But STRICT_NO_LOSS_BLOCK treats ALL positions equally — it doesn't know or care that a position is a hedge. So the core mechanic of the hedge engine (quick kill) was **impossible** on STRICT_NO_LOSS accounts.

**STRICT_NO_LOSS_ACCOUNTS = ['ang','inf','men','fin','flz']**
**HEDGE_ACCOUNTS = ['inf','fin','men','flz']**

Every hedge account except inf is also a strict-no-loss account. The hedge engine was deployed into accounts where its core kill mechanism is forbidden.

### 2. EMERGENCY_REDUCE: The "Safety Net" That Failed
When no hedge candidate exists, the system falls back to EMERGENCY_REDUCE (force-close the losing position). On FLZ this fired 1,364 times but **97.6% failed** (EXECUTION_FAILED: FAILED_REDUCE_UNVERIFIED). The maker order system couldn't complete reduces because of lock contention from the rapid-fire loop.

### 3. Candidate Churning
The scoring system kept electing the same symbols over and over:
- men: LRCUSDT_SHORT elected 189 times, TRUMPUSDC_SHORT 69 times
- The engine would elect a candidate, attempt execution, fail or succeed, then immediately re-elect the same symbol

### 4. No Position Size Cap
TUSDT_LONG accumulated up to $33 (6 + 27) before being closed, then immediately reopened. No mechanism limited total hedge exposure per account.

### 5. Code Changes That Made Execution Work (Irony)
Earlier in this session, 5 fixes were applied to `ez_manage.py` to make hedge execution actually succeed (ta logic, timeout, webhook bypass). Before these fixes, hedges were silently failing. The fixes made them execute — into the deadlock.

---

## Design Flaws That Must Be Fixed Before Hedge Can Be Re-Enabled

### CRITICAL: Must Fix ALL Before Re-enabling

1. **STRICT_NO_LOSS exemption for hedges** — Hedge positions MUST be closable at a loss. The entire point of a hedge is to accept a small loss to prevent a larger one. If you can't close a losing hedge, you have TWO losing positions instead of one.

2. **Maximum hedge exposure per account** — Hard cap on total USD in hedge positions (e.g., 5% of account equity). TUSDT churned $300+ in volume for $0 gain.

3. **Hedge kill must bypass ALL safety blocks** — STRICT_NO_LOSS_BLOCK, LOW_GAIN_DRAIN_PROTECTION, and any other close-prevention mechanism must yield to hedge kills. A hedge that can't be killed is worse than no hedge.

4. **Cooldown after hedge close** — After killing a hedge, wait at least 5 minutes before opening another on the same symbol. Prevents the TUSDT/SKYAIUSDT churn pattern.

5. **EMERGENCY_REDUCE must actually work** — 97.6% failure rate means there is no safety net. If the fallback fails, the system is fully exposed with no exit.

### IMPORTANT: Should Fix

6. **Don't hedge in STRICT_NO_LOSS accounts** — Or: create a separate "hedge sub-account" that is NOT subject to STRICT_NO_LOSS. The current config overlap is a fundamental contradiction.

7. **Log throttling** — The system produced 21,358 BLOCK log entries in 4 hours. This is a sign of a tight retry loop with no backoff. If a close is blocked, wait at least 60 seconds before retrying.

8. **Candidate diversity** — LRCUSDT_SHORT was elected 189 times on men. After 3 failed attempts on the same symbol, skip it for at least 30 minutes.

9. **Kill on open, not on loss** — Instead of waiting for a hedge to lose and then trying to close it at a loss, set a hard time limit: if the hedge hasn't gained within N minutes, close it at market regardless of P&L.

---

## The Correct Philosophy

**The long/short ratio IS the hedge.** The system maintains balanced long and short exposure across all positions. That ratio protects the portfolio. You don't hedge individual positions — the portfolio structure IS the hedge.

STRICT_NO_LOSS is **correct behavior**. The hedge engine violated the core trading philosophy by trying to close individual losers. Losers are part of the ratio. Closing them destroys the balance.

## What NOT To Do — EVER

- **NEVER** re-enable HEDGE_MODE — individual position hedging is fundamentally wrong for this system
- **NEVER** try to close losing positions to "protect" against loss — the L/S ratio handles it
- **NEVER** build systems that try to outsmart STRICT_NO_LOSS — it exists for a reason
- **NEVER** test hedge-like systems on live accounts
- Do NOT add more hedge scoring/candidate logic — the entire approach is wrong

---

## Who/What Caused It

### The engine design was fundamentally flawed from day one:
- HEDGE_ACCOUNTS and STRICT_NO_LOSS_ACCOUNTS overlap completely
- No exemption for hedge positions in the no-loss logic
- No fallback when the kill mechanism fails

### This session's changes made it worse by:
- Making hedge execution actually work (5 fixes to ez_manage.py maker orders)
- Relaxing candidate filters so more hedges could be opened
- Adding dynamic fallback candidates that increased hedge volume

### But the core problem existed before any changes:
- STRICT_NO_LOSS_BLOCK predates the hedge engine
- The hedge kill logic always sent closes through the same path as normal closes
- No one tested what happens when a hedge position immediately goes negative on a no-loss account

The hedge engine was a gun that couldn't shoot. This session loaded the gun. The design aimed it at our own foot.

---

## Current State (as of 2026-03-06 22:20 UTC)
- `HEDGE_MODE = False` (config.py line 58) — **DISABLED**
- 5 code fixes to ez_manage.py are live but harmless with HEDGE_MODE off
- All hedge scoring/candidate changes in ez_positions_quick.py are live but inactive
- Positions that were stuck have been force-closed via STRICT_NO_LOSS_BYPASS
- No hedge code should be modified until items 1-5 above are addressed
