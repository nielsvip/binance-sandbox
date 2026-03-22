# Position Bleed Analysis: ang:BTCUSDC_LONG
## Why a +$6 Gain Bled to Near-Loss Without Auto-Exit

**Date of incident:** 2026-03-03 ~18:36–21:18 UTC
**Account:** ang | **Symbol:** BTCUSDC | **Side:** LONG
**Scripts involved:** ez_manage.py · ez_positions_service.py · ez_positions_quick.py

---

## 1. What Actually Happened (Log-Verified Timeline)

```
~18:36 UTC  Position opened (time_since_entry=156min at 21:12 → opened ~18:36)
20:25–20:36 Series of QUICK_AUGMENT orders pile into BTCUSDC_LONG
            @ $68263–$68385, building to 0.012 BTC (~$820)
            Simultaneously: SUBSTITUTION kills other losers to free margin
            Parallel BTCUSDC_SHORT attempts: 0.000081 BTC ($5.51) — effectively nothing
20:27:41    pac1 WS update: positionAmt=0.007, unrealizedPnl=+$2.47
20:33:26    pac1 WS update: positionAmt=0.010, unrealizedPnl=+$3.45
            (Peak ~$6 gain was ~20:35–20:45 based on augment momentum)
20:53:57    *** STOP_FUNCTIONS_KILL allowed (k_3m=60.5 < d_3m=82.7) ***
            *** HEDGE_MODE_BLOCK: gain=-0.28% < 0.17% threshold — KILL BLOCKED ***
20:54:16    0.009 BTC SHORT finally attempted via maker order
20:54:40    MAKER_FALLBACK: 0.008 BTC sent to webhook (maker timeout)
20:56:39    IMMEDIATE_REDUCE BEARISH (conviction=75.0) — queued, never confirmed executed
21:03:32    NUKE: stale active SHORT order (Age > 60s) cancelled
21:05:30    *** STOP_FUNCTIONS_KILL allowed (k_3m=19.1 < d_3m=25.5) ***
            *** HEDGE_MODE_BLOCK: gain=-0.31% < 0.17% — KILL BLOCKED ***
21:05:56    $91.91 SHORT via webhook (QUICK_HEDGE_ACTUAL_OPEN_BTCUSDC_SHORT_TIMEOUT)
21:07–21:15 ez_positions_quick: NUKE_KEY loop — hedge repeatedly "banished" as failed
            HEDGE_SCANNER triggers every ~90s → HEDGE_FALLBACK → BTCUSDC_SHORT
            SHORT AUGMENT blocked: "Gain 0.37% < 0.5% threshold"
            STRICT_NO_LOSS_BYPASS fires → REDUCE 0.0007 BTC @ gain=-0.31%
            (4–5 small REDUCES executed, each 0.0007 BTC)
21:18:03    NUKE: stuck 'placing' state for BTCUSDC_SHORT cleaned
            USER MANUALLY KILLS position
```

---

## 2. Complete Decision Flow Diagram

```
Price falling from peak (gain drops from +$6 → 0 → -$0.50)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       ez_manage.py                                      │
│  STOP_FUNCTIONS_KILL evaluator (every ~26 seconds)                      │
│                                                                         │
│  Check 1: time_since_entry >= 5min  → OK (156 min)                     │
│  Check 2: k_3m < d_3m (stoch bearish on 3m)                            │
│           → BLOCKED until 20:53 (k_3m=71.5 > d_3m=56.4 stays bullish) │
│           → ALLOWED at 20:53 (k_3m=60.5 < d_3m=82.7)                  │
│           → ALLOWED at 21:05 (k_3m=19.1 < d_3m=25.5)                  │
│                                                                         │
│  Check 3 (HEDGE_MODE_BLOCK): gain >= 0.17% required                    │
│           → gain=-0.28%  → *** BLOCKED *** (20:53)                     │
│           → gain=-0.31%  → *** BLOCKED *** (21:05)                     │
│           → gain=-0.39%  → *** BLOCKED *** (21:18)                     │
└─────────────────────────────────────────────────────────────────────────┘
         │ BLOCKED by HEDGE_MODE_BLOCK every time stoch finally turns
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   ez_positions_quick.py                                 │
│  check_exit_candidates_for_account() — 5–6 second loop                 │
│                                                                         │
│  Hard exit check: max_gain=6% > 0.15 AND current_gain ≤ 0.10%         │
│  → BREAK_EVEN_GUARD_CLOSE fires                                         │
│  → is_hedge_account? YES                                                │
│  → Try dual hedge instead of reducing (lines 6374–6380)                │
│     → hedge_engine.execute_dual_hedge() called                          │
│     → SHORT augment blocked: "Gain 0.37% < 0.5%"                       │
│     *** HEDGE FAILS / GETS NUKED ***                                    │
│                                                                         │
│  HEDGE_SCANNER (90s interval):                                          │
│  ang:BTCUSDC_LONG losing → Triggering HEDGE_FALLBACK                   │
│  → SHORT block: AUGMENT rejected (0.37% < 0.5%)                        │
│  → STRICT_NO_LOSS_BYPASS: HEDGE_FAILED_FALLBACK_REDUCE fires           │
│  → REDUCE 0.0007 BTC @ gain=-0.31% (about $47 out of $136)             │
│  (Repeated ~every 2 min from 21:07 to 21:15)                           │
└─────────────────────────────────────────────────────────────────────────┘
         │ Only tiny partial REDUCEs getting through
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   ez_positions_service.py                               │
│  check_position_reductions() — called from monitor loop                │
│                                                                         │
│  _check_immediate_reduction_triggers():                                 │
│    max_gain=6%, erosion>15bp → should fire PROFIT_TAKE                 │
│    BUT: hedge account + gain < -0.05% → returns None (line 10758)      │
│                                                                         │
│  _check_trailing_stops():                                               │
│    max_gain=6% >= 3%, gain < 2.5% → REDUCE should fire               │
│    BUT: position marked is_hedged=True → DC break rule skipped         │
│    Trailing stop (max_gain - 1.0% threshold): gain still above floor? │
│                                                                         │
│  evaluate_reversal_exit():                                              │
│    DISABLED for ang (only enabled for fin, line 11080)                 │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. The 9 Blocking Layers — What Each Does and Why It Fires

### BLOCKER 1 — 3m Stochastic Bullish During Price Fall
**Script:** ez_manage.py
**Function:** `STOP_FUNCTIONS_KILL` evaluator
**Condition:** Requires `k_3m < d_3m` (bearish crossover on 3m stoch)
**Log evidence:**
```
21:12:19  STOP_FUNCTIONS_KILL not allowed: time_since_entry=156.1min | k_3m=71.5 > d_3m=56.4
21:13:09  STOP_FUNCTIONS_KILL not allowed: time_since_entry=157.0min | k_3m=71.5 > d_3m=56.4
```
**Why:** The 3m stochastic was still trending up (fast MA above slow MA) while BTC price was actually falling at the same time. This is a lagging indicator — it didn't flip bearish until the price had already dropped significantly. **The KILL cannot fire until the indicator lags behind the damage.**

---

### BLOCKER 2 — HEDGE_MODE_BLOCK (Critical)
**Script:** ez_manage.py
**Function:** `execute_now()` (lines ~11894–11900) and `execute_trade_action()` (lines ~10126–10132)
**Condition:** If `is_hedge_account AND is_reduce AND gain < 0.17%` → BLOCKED
**Log evidence:**
```
20:53:57  STOP_FUNCTIONS_KILL allowed (stoch finally bearish)
20:53:57  [HEDGE_MODE_BLOCK] ang:BTCUSDC_LONG: Blocking STOP_FUNCTIONS_KILL
          - position gain < 0.17% (gain=-0.28%) when HEDGE_MODE=True
21:05:30  [HEDGE_MODE_BLOCK] ang:BTCUSDC_LONG: gain=-0.31% — BLOCKED
21:18:31  [HEDGE_MODE_BLOCK] ang:BTCUSDC_LONG: gain=-0.39% — BLOCKED
```
**Why:** The design intent is "don't reduce if already in loss because the hedge is covering you." But the hedge in this case is a $91 short against a $680 long — completely inadequate to cover. The block fires without checking hedge SIZE vs position size. A 13% coverage hedge is treated identically to a 100% coverage hedge.

---

### BLOCKER 3 — STRICT_NO_LOSS in execute_trade_wrapper
**Script:** ez_positions_quick.py
**Function:** `execute_trade_wrapper()` (lines 5825–5853)
**Condition:** `is_strict_no_loss_account('ang') AND gain < -0.01% AND has_active_hedge` → BLOCKED
**Log evidence:**
```
21:07:52  [HEDGE_FALLBACK] Forcing SAME-SYMBOL hedge for BTCUSDC (Ratio 1.0)
21:07:56  [EXECUTE_BLOCKED] ang:BTCUSDC_SHORT: AUGMENT rejected. Gain 0.37% < 0.5%
21:07:58  [STRICT_NO_LOSS_BYPASS][ang] Emergency Override (HEDGE_FAILED_FALLBACK_REDUCE)
21:07:58  REDUCE | ReqQty=0.000700 | RealAmt=0.002000 | Gain=-0.31%
```
**Why:** The strict no-loss block correctly identifies that a hedge exists and prevents closing the long at a loss. But the same-symbol hedge SHORT can't be augmented either (separate rule: gain must be ≥ 0.5% to augment a hedged position). The bypass path is only reached after each ~90-second HEDGE_SCANNER cycle — resulting in tiny 0.0007 BTC REDUCEs every 2 minutes instead of a clean exit.

---

### BLOCKER 4 — SHORT AUGMENT Gain Threshold
**Script:** ez_positions_quick.py
**Function:** `execute_trade_wrapper()` (line 5918)
**Condition:** `AUGMENT rejected if real_gain < 0.5%`
**Log evidence:**
```
21:07:56  [EXECUTE_BLOCKED] ang:BTCUSDC_SHORT: AUGMENT rejected. Gain 0.37% < 0.5%
21:10:42  [EXECUTE_BLOCKED] ang:BTCUSDC_SHORT: AUGMENT rejected. Gain 0.37% < 0.5%
21:12:13  [EXECUTE_BLOCKED] ang:BTCUSDC_SHORT: AUGMENT rejected. Gain 0.37% < 0.5%
21:13:12  [EXECUTE_BLOCKED] ang:BTCUSDC_SHORT: AUGMENT rejected. Gain 0.37% < 0.5%
21:14:35  [EXECUTE_BLOCKED] ang:BTCUSDC_SHORT: AUGMENT rejected. Gain 0.37% < 0.5%
```
**Why:** The BTCUSDC_SHORT hedge WAS gaining (0.37% — price going down = short profits), but the 0.5% threshold for augmenting hedges prevents scaling it up to match the long's losses. This is the exact scenario where you WANT to augment the short — the hedge is working but can't be increased to full coverage.

---

### BLOCKER 5 — Hedge Logic "Hedged Instead of Reducing" Loop
**Script:** ez_positions_quick.py
**Function:** `check_exit_candidates_for_account()` (lines 6374–6380 and 6427–6440)
**Condition:** When hard exit triggers AND position is unhedged AND value ≥ $50 → try hedge instead of reduce
**Why it fails:** System tries to hedge instead of reducing. Hedge attempt fails (SHORT augment blocked at 0.37%). No fallback reduce on hedge failure. Returns early without reducing. Next cycle in 5–6 seconds, same path again. **The exit loop burns through 5-second iterations attempting hedge instead of reducing.**

---

### BLOCKER 6 — NUKE_KEY Loop Poison
**Script:** ez_positions_quick.py
**Function:** `hedge_engine` (internal)
**Log evidence:**
```
21:07:07–21:08:47  ☢️ [NUKE_KEY] Permanently banishing failed hedge: ang:BTCUSDC_SHORT
                   (repeats every 5–10 seconds for ~11 minutes straight)
```
**Why:** When a hedge attempt fails, NUKE_KEY "permanently banishes" the hedge key from the active list. Then HEDGE_SCANNER immediately triggers a new one. The rapid NUKE → retry cycle means no single hedge survives long enough to build real coverage. Each attempt starts from zero.

---

### BLOCKER 7 — Stale Indicator Data (50.0 Defaults)
**Script:** All three (data consumer)
**Log evidence:**
```
21:14:23  k_15m=50.0 d_15m=50.0 k_15m_prev=50.0 k_1h=50.0 d_1h=50.0 k_4h=50.0 d_4h=50.0
          sma_200_1m=0.00  t15:None  (STALE)
21:14:34  k_15m=25.7 d_15m=23.5 k_1h=89.5 d_1h=95.4 ...sma_200_1m=67209.76 (FRESH)
21:14:47  k_15m=50.0 d_15m=50.0 k_1h=50.0 d_1h=50.0  sma_200_1m=0.00  (STALE again)
```
**Why:** Two data sources alternate — one fresh (klines from server), one stale (t15:None, sma=0). Every other evaluation cycle uses completely default indicator values. Any logic that reads k_15m, k_1h, k_4h, or sma_200 for half the evaluations gets neutral 50.0 instead of actual bearish values. Exit decisions based on those indicators **see a neutral market instead of a bear trend.**

---

### BLOCKER 8 — Reversal Exit Disabled for 'ang'
**Script:** ez_positions_service.py
**Function:** `evaluate_reversal_exit()` (line 11080)
**Condition:** `if not (account_key == 'fin' and config.REV_MODE): return None`
**Why:** The most sophisticated exit logic (detect gain erosion from peak, trigger REVERSE before full loss) is gated to the `fin` account only. For `ang`, this entire function returns None immediately on every call.

---

### BLOCKER 9 — IMMEDIATE_REDUCE Not Confirmed Executed
**Script:** ez_manage.py
**Log evidence:**
```
20:56:39  [🚀 IMMEDIATE_REDUCE] ang:BTCUSDC_LONG: BEARISH signal → REDUCE (conviction=75.0)
          (no subsequent EXECUTE log for this specific reduce)
```
**Why:** The IMMEDIATE_REDUCE signal fires, but gets queued via `queue_trade_action()`. Queue has an 8-second limbo lock. Between queue and execution the position may have changed state, or the maker lock was busy (multiple [MAKER_BLOCK] entries around this time), or the post-fill cooldown from the recent augments blocked it.

---

## 4. Gain Tracking: What 'max_gain' and 'gain' Actually Represent

From the VERBOSE logs, there are **two "gain" fields** with confusing naming:

| Field | Log label | Meaning | In this incident |
|-------|-----------|---------|-----------------|
| `position.gain` | `gain=` in STOP_FUNCTIONS log | Real-time unrealized PnL % | -0.25% to -0.42% (current loss) |
| `position.max_gain` | `gain=` in VERBOSE INDICATOR_VALUES | Historical peak gain stored in position dict | -28.58% → This is the deteriorated/stale stored value |

The VERBOSE log shows `gain=-28.58%` and `gain=-29.89%` and `gain=-40.82%` in the `INDICATOR_VALUES` line. This is NOT the current P&L — it is the stored `max_gain` field that has decayed/gone stale. The real current gain (from the STOP_FUNCTIONS line) is only -0.25% to -0.42%.

**Consequence:** If any exit logic reads the wrong `gain` field from the position dict, it sees -28% instead of -0.3% and makes completely wrong decisions.

---

## 5. Reentry Logic: How the Position Gets Back In When the Tide Turns

### Path A: ez_positions_service.py `_check_reentry_triggers()` (lines 7417–7454)
1. Background loop runs every 10 seconds
2. Checks `service.reentry_data[position_key]` for each reduced position
3. If price crosses back through `reentry_level` (set at reduction price + small buffer):
4. Checks stochastic confirmation: `k_3m > d_3m` for LONG (bullish crossover required)
5. Executes BUY at `reentry_amount` from the stored data
6. **Problem:** 6-minute reduction cooldown applies. After each REDUCE, minimum 360 seconds before re-entry check fires.

### Path B: ez_positions_quick.py `check_entry_candidates_for_account()` (lines 6525–6696)
1. AdvancedSignalRater scores each position candidate
2. REENTRY score boosted if price is at 1.3x–1.8x above exit price
3. Requires k_3m < 25 for LONG reentry (oversold confirmation)
4. "QUICK_REENTRY" recommendation → execute AUGMENT/OPEN
5. **Problem:** Requires momentum alignment that may lag hours after exit.

### Path C: `create_ladder_levels_for_reentry()` called from `handle_reduction()`
- On each reduction, ladder levels are set for reentry
- These levels persist in `{account}/long_ladder.json`
- The ladder manager (in ez_manage.py) monitors price hitting ladder levels
- **Problem:** If max_gain was stale (-28%), ladder levels may be set wrong.

---

## 6. Root Cause Matrix

| Root Cause | Severity | Scripts | Fix Direction |
|-----------|----------|---------|---------------|
| HEDGE_MODE_BLOCK ignores hedge coverage ratio | CRITICAL | ez_manage.py L11894 | Add hedge_coverage_ratio check; only block if hedge >= 80% of position value |
| BTCUSDC_SHORT AUGMENT blocked at 0.37% (< 0.5%) | CRITICAL | ez_positions_quick.py L5918 | Lower augment-gain threshold for hedges from 0.5% to 0.1% |
| 3m stochastic lags price — KILL delayed until damage done | HIGH | ez_manage.py | Add HA color or 1m stoch override to STOP_FUNCTIONS_KILL check |
| "Hedge instead of reduce" loop burns without fallback | HIGH | ez_positions_quick.py L6374–6440 | After 2 failed hedge attempts in <30s, fall through to REDUCE |
| NUKE_KEY clears hedges too aggressively | HIGH | ez_positions_quick.py | Add minimum 30s cooldown before nuking a newly placed hedge |
| Stale 50.0 indicator data alternating with fresh | HIGH | All (data pipeline) | Detect t15:None / sma=0 as stale; refuse exit decisions on stale data |
| evaluate_reversal_exit disabled for 'ang' | MEDIUM | ez_positions_service.py L11080 | Remove account_key == 'fin' restriction |
| Stale/decayed max_gain carried as 'gain' field | MEDIUM | ez_positions_service.py | Rename stored field to avoid confusion; reset max_gain on reentry |
| IMMEDIATE_REDUCE queued, not guaranteed executed | MEDIUM | ez_manage.py | Log queue execution result; add bypass for high-conviction reduces |

---

## 7. The Specific Fix for BTCUSDC-Type Bleed

**The single change that would have exited this position at $4–5 gain:**

In `ez_manage.py` (HEDGE_MODE_BLOCK logic, ~line 11894), the current condition:
```python
if HEDGE_MODE and is_reduce and gain < 0.17%:
    return "BLOCKED_BY_HEDGE_MODE"
```
Should become:
```python
hedge_coverage = hedge_position_value / long_position_value  # 0.0 to 1.0
if HEDGE_MODE and is_reduce and gain < 0.17% and hedge_coverage >= 0.80:
    return "BLOCKED_BY_HEDGE_MODE"
# else: allow the kill — inadequate hedge is same as no hedge
```

And in `ez_positions_quick.py` (SHORT augment threshold, ~line 5918), the current:
```python
if real_gain < 0.5%:
    return "AUGMENT rejected"
```
Should for HEDGE positions specifically be:
```python
if real_gain < 0.1%:  # Hedges can be augmented earlier — they're defensive
    return "AUGMENT rejected"
```

**Why these two changes fix the bleed loop:**
1. BTCUSDC_SHORT at 0.37% gain would be allowed to augment → hedge grows to proper coverage
2. With proper coverage, HEDGE_MODE_BLOCK would only fire when coverage is adequate
3. With inadequate coverage, STOP_FUNCTIONS_KILL goes through → position exits near peak

---

## 8. Scalp "In-and-Out" While Long Holds: What's Missing

The expected behavior during an upward rally with pullbacks:
```
Expected:
  LONG position at $6 gain
  Indicators show pullback starting
  → EXIT the LONG (take the $6)
  → Open a small SHORT to profit on the pullback
  → When pullback exhausted (k_3m oversold), re-enter LONG
  → Repeat

Actual:
  LONG holds indefinitely because HEDGE_MODE_BLOCK prevents clean exit
  SHORT is opened but too small and gets nuked repeatedly
  No profit captured on the pullback
  By the time hedge is functional, the gain is gone
```

**What's missing:** A `SCALP_PROFIT_TAKE` action type that bypasses HEDGE_MODE_BLOCK when:
- `max_gain > 2%` AND `current_gain > 1%` AND `k_3m just crossed bearish`
- This would allow locking in $4+ profits at the first momentum flip
- The position can be re-opened via reentry logic when k_3m oversold

Currently, HEDGE_MODE_BLOCK treats all reductions identically regardless of the intention (defensive exit vs profit capture). A `SCALP_PROFIT_TAKE` reason string needs to be added to the bypass keyword list in `execute_now()` and `execute_trade_action()`.

---

## 9. Where to Look for the Same Problem on Other Accounts

Search all manage logs:
```bash
grep "HEDGE_MODE_BLOCK" /home/niels/logs/ez_manage_*.log | grep "gain < 0.17"
grep "EXECUTE_BLOCKED.*Gain.*< 0.5" /home/niels/logs/ez_positions_quick_general_*.log
grep "NUKE_KEY.*Permanently banishing" /home/niels/logs/ez_positions_quick_general_*.log | wc -l
```

If the NUKE_KEY count is in the hundreds and HEDGE_MODE_BLOCK appears for positions that had prior peak gains > 2%, the same bleed pattern is occurring on other symbols/accounts.
