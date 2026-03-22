# Investigation: Bad Trading After workingset_20260304_071139

## Status: Root Causes Identified, Fixes Pending

---

## Root Cause #1 — FORCE_DC_HIGH_BREAKOUT bypasses staleness gate (CRITICAL BUG)

**File:** `ez_positions_quick.py` ~line 6758
**Introduced:** workingset_20260304_151215 or later (not in 071139 good version — needs verification)

The staleness gate in `AdvancedSignalRater.rate()` correctly returns `(0, "WAIT", "STALE_INDICATORS_47m")`.
But immediately after, the FORCE logic checks freshness using the WRONG timestamp:

```python
# BUG: uses timestamp_3m from HOT data (always fresh — prices stream constantly)
_ts3m = indicators.get('timestamp_3m') or indicators.get('timestamp', '')
_force_fresh = (datetime.now(timezone.utc) - _ts_dt).total_seconds() < 1800  # TRUE (3s < 30min)

# Then opens trade with STALE dc_high_3m (from cold/Feb27 data) ← WRONG LEVELS
if _force_fresh and current_price >= dc_high_3m:
    should_trade = True
    reason += "_FORCE_DC_HIGH_BREAKOUT"  # Appended to "STALE_INDICATORS_47m"!
```

**Result:** Every position opened against 47-minute-old Donchian channel levels. This ran constantly
while indicators were stale — which happens for 47+ minutes after every service restart.

**Fix:** Change the `_ts3m` check to use `timestamp_15m` (same source as the staleness gate):
```python
_ts3m = indicators.get('timestamp_15m') or indicators.get('timestamp_3m') or indicators.get('timestamp', '')
```

---

## Root Cause #2 — _cold_data loaded from Feb 27 backup, not latest_market_data.json

**File:** `ez_positions_quick.py` — `_maintain_cold_data_sync()`

`_cold_data` is initialized from `latest_market_data.json` at startup via `load_initial_market_data()`.
But `_maintain_cold_data_sync()` prefers `market_data_*.json` files (sorted by name) over
`latest_market_data.json`. Old backup files from Feb 25-27 still exist:

```
/home/niels/binance/data/market_data_20260227_052549.json  ← Feb 27, 05:25 (days old)
/home/niels/binance/data/market_data_20260226_235018.json
...
```

Since `_last_loaded_file = None` at init, the first run of `_maintain_cold_data_sync` loads the
Feb 27 backup (overwrites the fresh initial load), then never changes because the file never changes.

`latest_market_data.json` (updated every few minutes by ez_indicators_merger) is IGNORED because
`market_data_*.json` candidates always exist.

**Consequence:** `timestamp_15m` in `_cold_data` = Feb 27 05:15 UTC (6+ days stale).

**Fix (quick):** Delete or move the old `market_data_*.json` backup files from /data/:
```bash
ssh niels@157.180.125.52 "mv /home/niels/binance/data/market_data_202602*.json /tmp/"
```

**Fix (proper):** Rewrite `_maintain_cold_data_sync` to also monitor `latest_market_data.json`
when no newer `market_data_*.json` exists, or use pubsub to know when new data arrives.

---

## Root Cause #3 — Augment quantity cap removed (35% → uncapped for ang)

**File:** `ez_manage.py` ~line 12117
**Introduced:** workingset_20260304_151215

```python
# GOOD (071139):
quantity = max(pos_min_qty, 0.35 * quantity)   # capped at 35%

# BAD (151215+):
# quantity=max(pos_min_qty, 0.35 * quantity)   # COMMENTED OUT
# → full calculated quantity sent for ang account
```

`inf/fin/flz/men` got a 50% cap added as replacement, but `ang` got NO cap.
Effect: augments for `ang` are ~3x larger than before.

---

## Root Cause #4 — min_since_aug > 4 → 12 for scalp exits

**File:** `ez_positions_quick.py` lines 1214, 1224
**Introduced:** workingset_20260304_151215+

Scalp exit trigger now waits 12 minutes after last augment (was 4). In fast markets, losing
positions hold 3x longer before scalp exit fires.

---

## Root Cause #5 — Ratio rebalance cooldown lost on restarts

**File:** `ez_manage.py` — `ratio_rebalance_loop()`

`_last_rebalance = {acct: 0.0}` is local to the coroutine — resets to zero on every service
restart. Observed rebalancing at 22:36 and 22:40 (4 min apart) because service restarted between.
Result: forced reduces on losing positions at worst possible moments.

Observed in logs:
```
04 02:15 — RATIO_REBALANCE ang: SHORT overweight 77%S — Reducing 14 positions (at a loss!)
04 22:36 — RATIO_REBALANCE ang: LONG overweight 87%L — Reducing 1 position
04 22:40 — RATIO_REBALANCE ang: SHORT overweight 71%S — Reducing 1 position
```

---

## Root Cause #6 — Order flood / runaway loop at 20:00 UTC March 4

Observed in `actions.log` at 20:00-20:01 UTC:
- `C98USDT_LONG`: augmented 3× in 1 second each with `Position Value before: 0.00`
- `1000LUNCUSDT_LONG`: augmented ~10× then reduced ~15× in 2 minutes
- `NEARUSDC_LONG`: opened 6× with same `Position Value before: 0.00` in 2 seconds

Cause: multiple concurrent tasks all reading stale position state (positionAmt=0) and
all simultaneously deciding to open — the new foothold+verify retry flow introduced
a race condition where position state wasn't updated between concurrent webhook calls.

---

## Data Flow: Why Age1m shows 3s but STALE_INDICATORS shows 47m

| Field | Source | Freshness |
|-------|--------|-----------|
| `Age1m` (display) | `metrics._tick_ts` = WebSocket price tick | Always fresh (prices stream) |
| `Age3m` (display) | `metrics.timestamp_3m` = hot_data from Redis | Always fresh (3m candle stream) |
| `STALE_INDICATORS` | `ind.timestamp_15m` = cold_data from `_cold_data` | Stale (Feb 27 backup or ez_indicators lag) |

`hot_metrics:BTCUSDC` in Redis contains: `{_tick_ts, timestamp_1m, timestamp_3m}` only — NO `timestamp_15m`.
`latest_market_data.json` contains: `{timestamp_15m: fresh, timestamp_1m: Feb26 (kline source), ...}`

---

## Files Changed Between Good (071139) and Bad (current)

| File | Good → Bad Key Change |
|------|-----------------------|
| ez_manage.py | 35% augment cap removed; foothold for reduces; action=='CLOSE' bug fixed; side calc fixed |
| ez_positions_quick.py | FORCE_DC_HIGH_BREAKOUT added; 3x/2x multipliers; min_since_aug 4→12 |
| ez_rankings.py | symbols_inf_long_list → symbols_inf_short_list (bug fix); SMA200 filters added |
| config.py | SANDBOX_MODE added |
| ez_indicators.py | Cache TTL added; file retention extended 4h→24h+7d |

**Unchanged:** ez_indicators_merger.py, ez_share_ind.py (identical to Mar 3 workingset)

---

## Fixes Priority Order

1. **IMMEDIATE** — Fix FORCE_DC_HIGH_BREAKOUT freshness check (1 line, ez_positions_quick.py:6758)
2. **IMMEDIATE** — Delete old market_data backup files so latest_market_data.json is used
3. **HIGH** — Restore 35% augment cap for ang in ez_manage.py
4. **HIGH** — Restore min_since_aug > 4 (from 12) in ez_positions_quick.py
5. **MEDIUM** — Fix ratio_rebalance cooldown to survive restarts (persist to Redis)
6. **MEDIUM** — Add position-lock guard to prevent concurrent opens for same key

---

## Current State (March 5)
- Services appear to still be running (logs active at 01:30 UTC)
- Multiple accounts reported bankrupt (4/5)
- `ang` still alive but shut down
- `latest_market_data.json`: timestamp_15m = 01:45 (fresh now, but was stale at 01:47 during user observation)
- Old `market_data_*.json` backup files still in /data/ → will cause cold_data to freeze again on next restart
