# Comparison Log: workingset_20260304_151215 (GOOD) vs Current Live

**Date**: 2026-03-05
**Baseline**: `workingset_20260303_151950` (before improvements)
**Good state**: `workingset_20260304_151215` (profitable improvements)
**Current state**: Live on server as of 2026-03-05 02:10 UTC

## Summary

- **30 files** changed between Good→Current
- **~80+ bare `except:` → `except Exception:`** conversions (cosmetic/safe)
- **~15 substantive logic changes** that could affect profitability

---

## FILE-BY-FILE COMPARISON

---

### 1. config.py (5 lines changed)
**Category**: NEW FEATURE (sandbox)

| Good Version | Current Version | Assessment |
|---|---|---|
| No sandbox fields | Added `SANDBOX_MODE: bool = False`, `SANDBOX_ACCOUNTS: List[str] = ['sbx']` | NEUTRAL — new feature, disabled by default |
| No sandbox init | `__post_init__` appends sandbox accounts to `ACCOUNT_KEYS` when enabled | NEUTRAL — only activates if SANDBOX_MODE=True |

**Verdict**: SAFE — sandbox is off by default, no impact on live trading

---

### 2. config_tradier.py (16 lines changed)
**Category**: NEW FEATURE (scalp/swing budgets)

Added 16 new config fields:
- `SWING_LONG_BUDGET`, `SWING_SHORT_BUDGET` (2000.0 each)
- `SWING_MAX_POSITION_SIZE`, `SWING_START_SIZE`
- `SCALP_LONG_BUDGET`, `SCALP_SHORT_BUDGET` (1000.0 each)
- `SCALP_MAX_POSITION_SIZE`, `SCALP_START_SIZE`
- `SCALP_MAX_HOLD_MINUTES` (450), `SCALP_STOP_PCT` (1.5%), `SCALP_TARGET_PCT` (2%)
- `SCALP_MAX_POSITIONS_PER_SIDE` (8), `SCALP_TOP_MOVERS_N` (14)
- `SCALP_MIN_REL_VOL` (1.1), `SCALP_MIN_MOVE_PCT` (0.003)

**Verdict**: SAFE — just field definitions, need code that uses them to matter

---

### 3. utils.py (49 lines changed)
**Category**: BUG FIX + NEW FEATURE

| Change | Good | Current | Assessment |
|---|---|---|---|
| `is_sandbox_account()` added | N/A | New helper function | NEUTRAL |
| `clean_position_key` side matching | `"SHORT" in side` (substring) | `side in ("SELL", "SHORT_SELL")` (exact) | GOOD FIX — prevents substring false matches |
| `construct_position_key` matching | `"LONG" in normalized_side.upper()` | `_ns in ("BUY", "LONG_BUY")` | GOOD FIX — exact matching |
| Error logging uncommented | `#logger.error(...)` | `logger.error(...)` in construct_position_key | GOOD — visibility |
| 10+ bare except fixes | `except:` | `except Exception:` | SAFE |

**Verdict**: ALL GOOD — bug fixes + safety improvements

---

### 4. ez_manage.py (269 lines changed) **CRITICAL FILE**
**Category**: MIXED — bug fixes + strategy changes

#### Bug Fixes (GOOD - keep):
| Line | Change | Assessment |
|---|---|---|
| ~1275 | Added `if not position_key:` null guard | GOOD — prevents crash |
| ~8577 | Fixed side determination: was always "SELL" for SHORT augments | CRITICAL FIX |
| ~12128 | `action == 'CLOSE'` → `action = 'CLOSE'` (was no-op comparison) | CRITICAL FIX |
| ~12267 | `abs(posAmt - origAmt)` → `abs(abs(posAmt) - abs(origAmt))` | GOOD FIX |
| ~3524 | Added `_aug_webhook_sent` tracking dict | GOOD — prevents duplicates |

#### Strategy Changes (REVIEW CAREFULLY):
| Line | Change | Assessment |
|---|---|---|
| ~1299,1308 | TradeVerifier timestamp tolerance: `start_time - 1.0` → `start_time - 3.0` | RISKY — wider verification window may accept stale fills |ok verifier needs to wait until serious confirmation
| ~11624 | Sandbox: `place_maker_order` returns immediately for sandbox accounts | NEUTRAL (only if sandbox enabled) |good
| ~11673 | Block augment fallback webhook on account error | POTENTIALLY HARMFUL — augments may silently fail |verify needs to verify
| ~11768 | Block augment fallback webhook on timeout | POTENTIALLY HARMFUL — same concern |
| ~12023 | Block duplicate augments within 15 minutes | COULD LIMIT PROFITS — prevents rapid augmentation |good
| ~12040 | Augment gate: requires `real_notional > 0` | GOOD — prevents blocking new opens |good but positions need to have been updated before another order so multiple opens will finally be ea thing of the past
| ~12114 | Uncommented `quantity=max(pos_min_qty, 0.35 * quantity)` | REDUCES AUGMENT SIZE to 35% | good as long as the system sucks 
| ~12155-12184 | Sandbox fill simulation | NEUTRAL (sandbox only) |good
| ~12184-12200 | Reduce verification + retry | GOOD — catches unconfirmed fills |good
| ~12267 | Augment unverified: blocks fallback webhook with FAILED_AUG_UNVERIFIED | POTENTIALLY HARMFUL — augments stuck |no fallbacks until verified and open orders cancelled
| ~12462-12506 | send_webhook: cancels ALL open orders same side before sending | AGGRESSIVE — could cancel wanted orders | ONLY the orders placed by place_maker_order it has the id's not other orders on server
| **~17630** | **Profit harvest threshold: 0.08% → 0.35%** | **MONEY CRITICAL — holds positions 4x longer before harvesting** | change to 0.1 however NEVER exit without an exit signal unless it is about to go into loss
| **~17632** | **DC basis profit exit: 0.05% → 0.20%** | **MONEY CRITICAL — waits 4x more profit before exit** |change to 0.2 however NEVER exit without an exit signal unless it is about to go into loss

**Verdict**: MIXED. Bug fixes are essential. But the profit threshold changes (0.08→0.35%, 0.05→0.20%) fundamentally change exit behavior — positions are held much longer before taking profit. In a choppy/down market this means giving back gains. The augment blocking could also prevent profitable position building.

---

### 5. ez_positions_quick.py (322 lines changed) **CRITICAL FILE**
**Category**: MIXED — bug fixes + major strategy changes

#### Bug Fixes (GOOD - keep):
| Line | Change | Assessment |
|---|---|---|
| ~3112-3120 | Wrapped `asyncio.create_task()` in try/get_running_loop guard | GOOD FIX |
| ~3185 | `.get['BTCUSDC']` → `.get('BTCUSDC')` (subscript on method) | CRITICAL FIX |
| ~6158 | `"LONG" in position_key` → `.endswith("_LONG")` | CRITICAL FIX |

#### Strategy Changes (REVIEW CAREFULLY):
| Line | Change | Assessment |
|---|---|---|
| **~794-819** | **Stale indicators gate: blocks ALL entries when indicators >15min old** | **POTENTIALLY HARMFUL — can block entries during normal operation** | We have been vERY clear about this - 1m indicators should not be taken into account if >90s old, 3m if >5min old, 5m if >9min old, 15m if >25min old, 1h if >2h old, 4h if >46 old, 1d if >30h old, 1w if >8d old,
| **~807** | **BASIS_CONDITION: removed `last_exit_timestamp is None`** | **CHANGES REENTRY BEHAVIOR — now always applies, not just first entry** |good
| **~809-817** | **SMA200 boycott: LONG blocked below SMA200, SHORT blocked above** | **MAJOR STRATEGY CHANGE — eliminates contrarian/mean-reversion entries** |good
| **~1211,1221** | **Scalp exit timer: 4min → 12min after augment** | **HOLDS SCALPS 3x LONGER — riskier in choppy markets** |  good unless < dc_low4_3m/5m etc clear startegy in tradier_manage should be followed by ez_manage
| **~1865-1882** | **SMA200 + DC basis filters on rank universe** | **REDUCES ENTRY UNIVERSE — fewer candidates** |good if BASIS_CONDITION is True
| **~1906-1951** | **Hedge candidates filtered by SMA200/basis** | **RESTRICTS HEDGING — may not find hedge when needed** |good if BASIS_CONDITION is True and if losing position is closed immediately if not finding hedge
| **~2627-2681** | **No more same-symbol hedge fallback → emergency reduce 40%** | **MAJOR CHANGE — stops hedging, reduces instead** |NOT GOOD we have a RATIO in DUAL HEDGE between same symbol and the other symbol foundthat NEED TO BE RESPECTED ALWAYS. Same symbol hedges are always found so they also mean only partial close if the rateengine has not been repaired (REPAIR IT)! and no hedge can be found
| **~2681** | **ALL hedges fail → CLOSE (was REDUCE 35%)** | **MORE AGGRESSIVE RISK — closes entire position** |ONLY hte part of the other symbol hedge
| **~2681-2819** | **New _emergency_reduce_losing()** | NEW — reduces losers by 40% |if > START_POSIITON_SIZE
| **~2819-2884** | **New check_pullback_reexpansion_ez()** | NEW — pullback detection with position sizing boost |absolutely!
| **~2983-3014** | **Pullback-reexpansion scoring: score 4=3x, score 3=2x position** | **AGGRESSIVE — up to 3x normal position on "setup"** |good
| **~5820** | **BASIS_CONDITION: removed pnl_pct > 0.05 fallback** | STRICTER — no bypass even with small profit |good
| **~5962** | **BASIS_CONDITION: removed FORCE_DC bypass** | **BLOCKS DC BREAKOUT ENTRIES — major strategy limitation** |NOT GOOD
| **~5979-5991** | **SMA200 boycott in execute_trade_wrapper** | Mirrors rate() changes |NOT GOOD
| **~6081-6098** | **Augment webhook suppressed on failed verification** | Same augment-blocking pattern |Good CONFIRMATION FIRST 
| **~6098** | Trade cooldown after close | REASONABLE |good
| **~6415-6419** | **CYCLE_TP exit: exits at 15m stoch peaks when profit >0.15%** | **PREMATURE EXIT — 0.15% is very small, exits on noise** |GOOD however WHY THE FUCK ARE WE GETTING IN 0.15% below the k_15 top that is SUICIDE. You get in on the BOTTOM of k1m/k3m/k5m and k_15m unless k_1h and k_4h are still not too overheated and we are breaking dc_high_15/ or 1h or 4h (vv short) 
| **~6752-6870** | **FORCE_DC: staleness + sanity guards** | GOOD — prevents phantom breakouts |

**Verdict**: VERY MIXED. The SMA200 boycott, BASIS_CONDITION changes, and CYCLE_TP exit at 0.15% could be catastrophic together. The SMA200 filter eliminates entries below SMA200 for longs — in a correction this blocks ALL long entries. CYCLE_TP at 0.15% exits positions on tiny gains at stochastic peaks. Hedge restrictions mean losses can't be offset. The pullback-reexpansion 3x sizing is aggressive and untested.

---

### 6. ez_positions_service.py (121 lines changed)
**Category**: BUG FIX + SANDBOX

| Change | Assessment |
|---|---|
| Div-by-zero guard (line ~2772): `if current_price <= 0: return existing levels` | GOOD FIX |
| Div-by-zero guard (line ~2966): `/ current_price` guarded | GOOD FIX |
| Sandbox: `fetch_positions()` returns `{}` for sandbox accounts | NEUTRAL |
| ~40 bare except fixes | SAFE |

**Verdict**: ALL GOOD

---

### 7. ez_rankings.py (35 lines changed)
**Category**: CRITICAL BUG FIX

| Change | Assessment |
|---|---|
| **Line ~4180: `symbols_inf_long_list` → `symbols_inf_short_list`** | **CRITICAL FIX — was overwriting longs with short symbols** | THX for finding stupid errors -- trb has not taken out shorts in days there msut be a bug in the short side pls fix
| Lines ~4917-4930: Open positions always tracked for indicators | GOOD — prevents indicator starvation |
| ~8 bare except fixes | SAFE |

**Verdict**: ALL GOOD — essential fixes

---

### 8. ez_market_data.py (8 lines changed)
**Category**: COSMETIC

Only 3 bare `except:` → `except Exception:` fixes. No logic changes.

**Verdict**: SAFE

---

### 9. ez_double.py (32 lines changed)
**Category**: CRITICAL BUG FIXES

| Line | Change | Assessment |
|---|---|---|
| 1175 | `abs(p - (ref/ref))` → `abs(p - ref) / ref` | **CRITICAL FIX — outlier detection was always ~1.0** |
| 1770 | `abs(cur - (pos/pos))` → `abs(cur - pos) / pos` | **CRITICAL FIX — price sanity always wrong** |
| 1778 | `max_val/pos if pos else max_val/pos` → `else quantity` | **CRITICAL FIX — both branches identical, crashes on 0** |
| 1767 | Added `current_price > 0` guard | GOOD |
| 2026 | `60/current_price if current_price > 0 else 0` guard | GOOD |
| 1935-1936 | `last_reduction_*` keys → `last_augmentation_*` | **CRITICAL FIX — copy-paste bug** |
| 2079, 2100 | `==` → `=` for augmented_positions | **CRITICAL FIX — no-op comparisons** |

**Verdict**: ALL CRITICAL FIXES — must keep

---

### 10. ez_crosses.py (12 lines changed)
**Category**: BUG FIX

| Change | Assessment |
|---|---|
| Line 1611: `time.sleep(SLEEP_INTERVAL)` → `await asyncio.sleep(SLEEP_INTERVAL)` | **CRITICAL FIX — was blocking async main loop** |
| 5 bare except fixes | SAFE |

**Verdict**: ALL GOOD

---

### 11. ez_indicators.py (71 lines changed)
**Category**: BUG FIX + DATA RETENTION

| Change | Assessment |
|---|---|
| Line 583: Added try/except around `json.loads(raw)` | GOOD FIX |
| Lines 795-821: Cache validity extended with 5-min TTL | GOOD — prevents stale cache |
| Lines 2605-2692: Market data cleanup bins extended (24h/7d tiers) | GOOD — more data retained |
| ~20 bare except fixes | SAFE |

**Verdict**: ALL GOOD

---

### 12. ez_positions.py (46 lines changed)
**Category**: SANDBOX + COSMETIC

Import reorganization. Sandbox account support (skip WebSocket/API, create directories).

**Verdict**: SAFE — sandbox is off by default

---

### 13. ez_mark_prices.py (8 lines changed)
**Category**: CRITICAL BUG FIX

| Change | Assessment |
|---|---|
| Line 1841: `keep='first'` → `keep='last'` | **CRITICAL FIX — fresh data overwrites stale** |
| 3 bare except fixes | SAFE |

**Verdict**: MUST KEEP

---

### 14. ez_positions_watchdog.py (112 lines changed)
**Category**: IMPROVEMENT

| Change | Assessment |
|---|---|
| New `_reap_zombies()` method | GOOD — prevents defunct process buildup |
| Called every watchdog iteration | GOOD |
| ~25 bare except fixes | SAFE |

**Verdict**: ALL GOOD

---

### 15. ez_prices.py (11 lines changed)
**Category**: BUG FIX

Line 248: `asyncio.create_task()` in sync context → wrapped with `get_event_loop()` guard.

**Verdict**: GOOD FIX

---

### 16. ez_prices_ws.py (29 lines changed)
**Category**: BUG FIX

Lines 1165-1177: Kline `float()` conversions wrapped in try/except so bad message doesn't force reconnect.

**Verdict**: GOOD FIX

---

### 17. ez_share_ind.py (6 lines changed)
**Category**: OBSERVABILITY

Added daemon heartbeat thread that logs every 60s to confirm shared memory server is alive.

**Verdict**: HARMLESS

---

### 18. ez_disk_cleanup.py (28 lines changed)
**Category**: DATA RETENTION

| Change | Assessment |
|---|---|
| Stopped nuking `tradier/*` data (kept for backtesting) | GOOD |
| Extended retention bins: hourly→24h, 4-hourly→1 week, daily→30 days | GOOD |
| Bare except fixes | SAFE |

**Verdict**: ALL GOOD

---

### 19. ez_indicators_merger.py (2 lines changed)
Single bare `except:` fix. **SAFE.**

---

### 20. ez_klines.py (12 lines changed)
7 bare `except:` fixes. **SAFE.**

---

### 21. ez_positions_realtime.py (10 lines changed)
6 bare `except:` fixes. **SAFE.**

---

### 22. ez_loss_mitigator.py (2 lines changed)
**Category**: BUG FIX

Line 679: Added `if candidate.exit_price <= 0: continue` div-by-zero guard.

**Verdict**: GOOD FIX

---

### 23. tradier_manage.py (580 lines changed) **CRITICAL FILE**
**Category**: MIXED — DST fix + major strategy changes

#### Bug Fixes / Improvements (GOOD):
| Change | Assessment |
|---|---|
| DST-aware `is_regular_trading_hours()` using `ZoneInfo('America/New_York')` | IMPORTANT FIX |
| Fixed `current_price > 0 and current_price <= 0` (always false) → `current_price <= 0` | CRITICAL FIX |
| Bare except fixes (~30) | SAFE |

#### Strategy Changes (REVIEW CAREFULLY):
| Change | Assessment |
|---|---|
| **Reopen cooldown: 30 min after full close** | **PREVENTS REENTRY — may miss bounces** |
| **Pullback-reexpansion heavy artillery: 2-3x qty boost** | **AGGRESSIVE — untested position sizing** |
| **Swing budget enforcement** (`is_swing_entry_allowed`) | **LIMITS POSITIONS — could block needed entries** |
| **Time-based structural stops widened: 10min→20min, 30min→60min** | **HOLDS POSITIONS LONGER through drawdown** |
| **Stop buffers widened: 0.1% → 0.4% for first 20 min** | **MUCH WIDER STOPS — allows 4x more drawdown** |
| **DC stop upgraded from dc_low_15m to dc_low_1h at 60min+** | **WIDER STOPS — 1h DC channel vs 15m** |
| **Added 5m candle close confirmation for early stops** | May delay needed exits |
| **Weak technical exit shield: 15min→20min, deadzone cap 40min** | **HOLDS LOSERS LONGER** |
| **CYCLE_TP exit: 15m stoch peak/trough at gain >0.15%** | **PREMATURE EXITS at tiny gains** |
| **Entry DC basis filter: blocks longs above basis_1h, shorts below** | **LIMITS ENTRIES** |
| **Stochastic triggers tightened: k<50→k<35 for longs, k>50→k>65 for shorts** | **FEWER ENTRIES** |
| **Reentry momentum: 0.1%→0.3% pullback required, min 10min wait** | **FEWER REENTRIES** |
| **Rebalance cooldown: skip if reduced/augmented within 30min** | May delay needed rebalancing |
| Wing exposure tracking + budget gating | New risk management |

**Verdict**: VERY MIXED. The DST fix is essential. The wider stops + longer hold times + CYCLE_TP exits create a contradictory pattern: hold losers longer (wide stops) but exit winners early (CYCLE_TP at 0.15%). This is likely the main profit killer.

---

### 24. tradier_positions.py (53 lines changed)
**Category**: BUG FIX + FEATURE

| Change | Assessment |
|---|---|
| `safe_json_loads`: `orjson.loads` → `json.loads` (avoids ImportError) | GOOD FIX |
| `Pass` → `pass` (NameError) | CRITICAL FIX |
| `if symbol in pk` → `parse_position_key(pk)` for exact match | GOOD FIX |
| Added `trade_wing: str = "swing"` field to TradierPosition | NEUTRAL (new field) |
| ~15 bare except fixes | SAFE |

**Verdict**: ALL GOOD

---

### 25. tradier_rankings.py (88 lines changed)
**Category**: BUG FIX + STRATEGY

| Change | Assessment |
|---|---|
| `orjson.loads` → `json.loads` fallback fix | GOOD |
| `safe_json_loads` rewrite | GOOD |
| **HTF/LTF R-value separation** (trend quality gate) | NEW ANALYSIS |
| **Dedup: keep highest-scoring entry per symbol** | GOOD FIX |
| **Fixed: bottom15_15m was sourcing from top_100_set** → now from `top_100_losers_set` | CRITICAL FIX |
| **Symbol parity enforcement: pad shorts to match longs count** | NEW — ensures balanced universe |
| **Non-shortable + options filtered from shorts** | GOOD — prevents invalid shorts |
| ~8 bare except fixes | SAFE |

**Verdict**: MOSTLY GOOD. The losers sourcing fix and dedup are important. Parity enforcement is reasonable.

---

### 26. tradier_indicators.py (72 lines changed)
**Category**: BUG FIX + IMPROVEMENT

| Change | Assessment |
|---|---|
| New `is_intraday_data()` function — detects daily bars in intraday caches | GOOD — data quality |
| Purges intraday caches that contain daily bars | GOOD — prevents bad indicators |
| **Start time timestamps: UTC→ET conversion for Tradier API** | IMPORTANT FIX — API expects ET |
| **Rejected timesales that return daily bars** | GOOD |
| **1h/4h: removed `pad_with_higher()`** → merge intraday resampled only | POTENTIALLY RISKY — less data padding |
| Extended indicator file retention (24h hourly, 7d per-4h, 30d daily) | GOOD |
| `get_timesales` interval fix propagated from tradier_api.py | GOOD |
| ~15 bare except fixes | SAFE |

**Verdict**: MOSTLY GOOD. The ET conversion fix is important. Removing pad_with_higher could mean less historical depth for 1h/4h, but avoids fake bars.

---

### 27. tradier_api.py (13 lines changed)
**Category**: BUG FIX

| Change | Assessment |
|---|---|
| Removed duplicate 429 handler (dead code after first return) | GOOD FIX |
| `interval: "1m"` → `"1min"` for `get_timesales` | IMPORTANT FIX — Tradier API format |
| 4 bare except fixes | SAFE |

**Verdict**: ALL GOOD

---

### 28. tradier_prices.py (11 lines changed)
**Category**: IMPROVEMENT

| Change | Assessment |
|---|---|
| Price: `last` → `(bid+ask)/2` (mid price) with last as fallback | GOOD — better price accuracy |
| 3 bare except fixes | SAFE |

**Verdict**: GOOD

---

### 29. tradier_webhook_bridge.py (2 lines changed)
Single bare `except:` fix. **SAFE.**

---

### 30. symbols_tradier.json
Not compared in detail (likely just symbol list changes).

---

## CRITICAL CHANGES LIKELY CAUSING LOSSES

### 1. CYCLE_TP Exit at 0.15% (ez_positions_quick.py + tradier_manage.py)-fine
Exits ANY position that's only 0.15% in profit when 15m stochastic crosses.
- 0.15% is barely above fees
- Stochastic crosses happen frequently
- **Effect**: Cuts winners extremely short

### 2. SMA200 Boycott (ez_positions_quick.py)-ONLY if BASIS_CONDITION!
Blocks ALL long entries below SMA200, ALL short entries above.
- In a correction, price is below SMA200 for extended periods
- **Effect**: No new longs during pullbacks (when they're often cheapest)

### 3. Profit Harvest Threshold Raised 4x (ez_manage.py:17630) TAKE PROFIT IF the 15m is turning against or 15m is hig and 1mor 3m turns against
`current_gain > 0.08%` → `current_gain > 0.35%`
- Positions must gain 4x more before harvesting
- In choppy markets, gains reverse before reaching 0.35%
- **Effect**: Profits that would have been captured at 0.08% evaporate

### 4. DC Basis Exit Threshold Raised 4x (ez_manage.py:17632) dc basis is WAY TOO LATE to exit you exit when high < dc high  after breaking it and get back in crossing over dc basis or dc high

`current_gain > 0.05%` → `current_gain > 0.20%`
- Same effect as above for DC basis exits

### 5. Hedge Restrictions (ez_positions_quick.py)
- No more same-symbol hedge fallback STUPID
- Hedge candidates filtered by SMA200/basis (fewer qualify) take it out
- When ALL hedges fail → CLOSE (was REDUCE 35%) part same symbol (defined in init)  rest close
- **Effect**: Can't hedge effectively, forced to close at losses (STUPID)

### 6. Scalp Timer Tripled (ez_positions_quick.py)
`min_since_aug > 4` → `> 12` minutes
- Scalps held 3x longer after augmentation
- **Effect**: More exposure to reversals UNLESS entries are LESS STUPID and we keep the dc_low4_5m/3m for the first 15m

### 7. Wider Stops + Longer Hold (tradier_manage.py)
- First 20 min buffer: 0.1% → 0.4% (4x wider) fine
- Time brackets widened: 10→20 and 30→60 minutes fine
- DC stop upgraded from 15m to 1h after an hour of having an open trade
- **Effect**: Holds losing positions much longer before stopping STUPID we are only talking about GAINING positions losing positions are DEAD by then leaving their hedges OPEN as normal positions if they do well

### 8. Augment Blocking (ez_manage.py)
- Block augment fallback webhooks on error/timeout
- Block duplicate augments within 15 min of course unless ANOTHER good gain is established over the augmented position
- Reduce augment size to 35% of calculated quantity (ok while we are throttling everyting to 35% as long as this trading disaster is not fixed)
- **Effect**: Profitable positions can't grow as fast

### 9. BASIS_CONDITION Changes (ez_positions_quick.py)
- Removed last_exit_timestamp exception (always applies now) what does that mean??
- Removed pnl_pct > 0.05 fallback ?? you can never augment on a shit posiiton
- Removed FORCE_DC bypass BYPASS IS GOOD WITH A SMALL LEASH AS IT WAS DESIGNED AND THEN GET BACK IN HUGE AFTER A PULLBACK BUT DONT LET IT EXPLDE AND NOT BE IN A TRADE THAT IS FKN STUPID
- **Effect**: More entries blocked by basis condition OVERRIDE/BYPASS if a symbol takes off like crazy you can not afford to not be in!!!!

### 10. Entry Filters Tightened (tradier_manage.py)
- DC basis_1h proximity filter
- Stochastic triggers narrowed (k<35/k>65 vs k<50/k>50) fine if any stoch is 50/50 it should be IGNORED it means it ahs not been calculated 
- 30min reopen cooldown after close not on scalps of course
- **Effect**: Fewer entries overall

---

## CHANGES THAT ARE CLEARLY GOOD (KEEP REGARDLESS)

1. All `except:` → `except Exception:` fixes (~80+ instances)
2. ez_double.py: outlier detection formula fix, price sanity fix, div-by-zero fixes, copy-paste fix, no-op comparison fixes
3. ez_rankings.py: `symbols_inf_long_list` → `symbols_inf_short_list` fix
4. ez_mark_prices.py: `keep='first'` → `keep='last'`
5. ez_crosses.py: `time.sleep` → `await asyncio.sleep`
6. ez_prices.py: asyncio.create_task guard
7. ez_prices_ws.py: kline parsing try/except
8. ez_positions_quick.py: `.get['BTCUSDC']` fix, `.endswith("_LONG")` fix
9. ez_manage.py: side determination fix (line 8577), no-op `==` → `=` fix (line 12128), null guard
10. tradier_api.py: duplicate 429 handler removal, `"1m"` → `"1min"` fix
11. tradier_positions.py: `Pass` → `pass`, `symbol in pk` → exact match, `orjson` → `json` fallback
12. tradier_rankings.py: losers sourcing fix, dedup
13. tradier_manage.py: DST-aware trading hours, `price > 0 and price <= 0` fix
14. tradier_indicators.py: intraday data validation, ET timestamp conversion
15. tradier_prices.py: mid-price (bid+ask)/2
16. utils.py: exact side matching in clean_position_key/construct_position_key
17. ez_indicators.py: json.loads guard, cache TTL, retention bins
18. ez_positions_service.py: div-by-zero guards
19. ez_loss_mitigator.py: exit_price div-by-zero guard
20. ez_positions_watchdog.py: zombie process reaper
21. ez_share_ind.py: heartbeat logging
22. ez_disk_cleanup.py: extended data retention
23. Sandbox infrastructure (config, utils, ez_positions) — no impact when disabled

---

## CHANGES APPLIED (2026-03-05 05:00 UTC) — workingset_20260305_050020

### ez_manage.py
1. Profit harvest threshold: 0.35% -> **0.10%** (with exit signal requirement: long_stop/short_stop still required)
2. DC basis exit: kept at 0.20% (user confirmed)

### ez_positions_quick.py
1. **Per-timeframe stale indicators**: Replaced blanket 15min gate with: 1m>90s, 3m>5min, 5m>9min, 15m>25min, 1h>2h, 4h>46h, D>30h, 1w>8d. Only blocks on critical TFs (1m/3m/5m/15m).
2. **Restored FORCE_DC bypass**: `and not is_force_dc` added back to BASIS_CONDITION check in execute_trade_wrapper
3. **Removed SMA200 boycott from execute_trade_wrapper**: SMA200 check stays in rate() but no longer hard-blocks in execute_trade_wrapper
4. **Restored same-symbol hedge fallback**: When elected hedge fails, now tries same-symbol hedge with actual_symbol_ratio before emergency reducing
5. **All hedges fail = partial REDUCE**: Changed from CLOSE entire position to REDUCE by actual_symbol_ratio only
6. **Emergency reduce guard**: Only applies if position > START_POSITION_SIZE
7. **Hedge zombie killing**: Changed from `gain < prev_gain - 0.04` to `gain < prev_gain` (any drop from positive kills hedge immediately)

### config_tradier.py
- SWING budgets: 20000 -> 2000
- SCALP budgets: 10000 -> 1000
- SCALP_MAX_HOLD_MINUTES: 45 -> 450
- SCALP_MIN_REL_VOL: 1.3 -> 1.1

### tradier_manage.py
1. **Reopen cooldown**: No cooldown for scalps; 15min for losing close; 30min for winning close
2. **Stoch triggers**: Added `k5 != 50` guard (50 = uncalculated, ignore)

### Saved as workingset_20260305_050020

---

## REMAINING TODO

### URGENT — 98% losing trades on ang
- Logs show entries at BAD levels (near k_15m tops, stoch 50/50 uncalculated)
- Need to fix entry quality: enter at BOTTOM of k1m/k3m/k5m/k15m cycles
- Only break DC high/1h/4h if k_1h and k_4h not overheated
- stoch_k=50 / stoch_d=50 should be treated as UNCALCULATED everywhere (not just tradier)

### Hedge zombie cleanup needed
- HEDGE_MODE_BLOCK prevents closing losing hedged positions
- Hedges that go negative should be killed faster
- Applied: `gain < prev_gain` kills hedge (was `gain < prev_gain - 0.04`)

### trb short-side investigation
- User reports trb hasn't taken shorts in days
- Need to investigate tradier_rankings.py and tradier_manage.py short-side logic

---

## APPENDIX: Files with NO changes between Good→Current

These files are identical between `workingset_20260304_151215` and current:
- bridge.py
- ez_gain_protector.py (not in workingset — separate file)
- ez_gap_filler.py (not in workingset — separate file)
- nuke_everything_LINUX.sh
- run_with_watchdog_LINUX.sh
- sh.sh
- symbols_fin.json
- symbols.json
- symbols_men.json
- ez_positions_backup.py
- ez_positions_backup_account.py
- ez_positions_realtime_ang.py through _men.py (account variants)
