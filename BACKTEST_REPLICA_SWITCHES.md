# Backtest Replica Switches — Live-Only Features Inventory

**Purpose**: Set these to replicate `backtest_v8_engine.py` behavior in live code.
Any feature in this list is **NOT** modeled in `v8_quick_engine.py` or `backtest_v8_engine.py`.

---

## GROUP KILL (set all when running A/B vs backtest)

```python
# config.py overrides for backtest-replica mode
SCALP_V3_ENABLED = False                     # orderbook scalper — no backtest analog
LEGACY_GUARANTEED_REENTRY = False            # enforcement loop — no backtest analog
MANDATORY_REENTRY_MIN_WT_AGREE = 99          # effectively disables MANDATORY_REENTRY
PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED = True  # keep (reduces bad trades)
B_MAIN_ENTRY_GATE_ENABLED = False            # tradier B-gate — not in v8 engine (tradier only)
LOCAL_EXTREMES_SCORER_ENABLED = False        # v8_quick has it but default OFF; tradier only
WT_DC_ENTRY_FILTER_ENABLED = True            # keep — this IS the v8 baseline entry
LINEARITY_LR_LONG_ENABLED = False            # not in backtest engine
LINEARITY_LR_SHORT_ENABLED = False           # not in backtest engine
SQUEEZE_FIRE_ENABLED = False                 # not yet in backtest engine
# Advisory system has no config kill switch — must stop local_advisory_generator cron
```

---

## Feature-by-Feature Inventory

### 1. MTF_SR_FRESH_SETUP Advisory System
- **File**: `local_advisory_generator.py` → `fin_advisory_consumer.py` → `ez_manage.py:20052`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: Stop the cron/loop that runs `local_advisory_generator.py`. No config switch exists.
- **Fix needed**: Add `FIN_ADVISORY_CONSUMER_ENABLED` switch in config.py + ez_manage.py gate.
- **What it does**: Reads `tradeable_refresh.json` every minute; emits `force_open`/`force_close`/
  `force_hedge`/`hold`/`block_entry` advisories for all 5 crypto accounts.
- **Dumb-trade fix (2026-05-04)**: `local_advisory_generator.py` now requires BOTH `1h` AND `15m`
  to be in the aligned set (not just any 4/5), plus blocks if wt1_15m or wt1_1h > ±53 (extreme).

### 2. SCALP_V3 Orderbook Scanner
- **Files**: `ez_positions_quick.py` (157 refs), `ez_manage.py`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: `SCALP_V3_ENABLED = False` (config.py:122)
- **What it does**: Ultra-short $10-$30 scalps on orderbook divergence; fires `SCALP_V3_OPEN_*` entries.

### 3. GUARANTEED_REENTRY Enforcement Loop
- **Files**: `ez_manage.py:15700` (main loop), `ez_positions_quick.py:15398` (epq loop)
- **In v8 backtest**: ❌ NOT PRESENT (v8_quick has the config flag but no enforcement)
- **Kill**: `LEGACY_GUARANTEED_REENTRY = False`
- **What it does**: Tracks every exited position; forces reentry when price recrosses exit level +
  sufficient WT agreement + favorable K. Has tight stop for GUARANTEED positions.
- **Current guards**: k_3m extremes (80/20), WT 3m+15m check. Missing: wt_1h direct check.

### 4. MANDATORY_REENTRY (PRICE_CROSS path)
- **File**: `ez_positions_quick.py:2909` (score +30 on price cross)
- **In v8 backtest**: ❌ NOT PRESENT as enforcement; reason string only in backtest_v8_engine
- **Kill**: `MANDATORY_REENTRY_MIN_WT_AGREE = 99` (effectively requires all TFs — never fires)
- **Current guards**: k_3m block (80/20), min 2/3 WT TFs. Missing: wt_1h explicit check.

### 5. MANDATORY_PRICE_CROSS_EPQ ("NO QUESTIONS ASKED")
- **File**: `ez_positions_quick.py:16068`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: Only blocked when ALL THREE of 15m/1h/4h are against (via `PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED`).
  **Gap**: fires when 1h is against but 15m is neutral (not caught by current veto).
- **Fix needed** (requires `ez_positions_quick.py` unlock): Change veto from "all three against" to
  "ANY of 1h/15m against" → block.

### 6. DIRECTION_FAVORABLE_REENTRY path
- **File**: `ez_positions_quick.py:15973`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: No dedicated switch — wrapped inside guaranteed reentry flow.

### 7. B10_STOCH_REV_LONG/SHORT Reentry Signals
- **File**: `ez_positions_quick.py:15842,15845`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: Part of EPQ reentry loop — disabled with `LEGACY_GUARANTEED_REENTRY = False`.

### 8. WT_4H_VEL_EXIT with MANDATORY_REENTRY flag
- **File**: `ez_manage.py:20578`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: No dedicated switch currently.

### 9. DELTA_EXIT with MANDATORY_REENTRY routing
- **File**: `ez_manage.py:20924`, `ez_positions_quick.py:3246`
- **In v8 backtest**: ❌ NOT PRESENT (v8 has DELTA_EXIT but no reentry routing)
- **Kill**: DELTA_EXIT itself has a switch but the MANDATORY_REENTRY flag is always appended.

### 10. LOCAL_EXTREMES Entry Gate (Tradier)
- **Files**: `tradier_manage.py:1790`, `v8_quick_engine.py:753`
- **In v8 backtest**: ⚠️ PARTIAL — v8_quick has it but disabled by default; NOT in backtest_v8_engine
- **Kill**: `LOCAL_EXTREMES_SCORER_ENABLED = False` (already default in v8_quick)

### 11. B_MAIN_ENTRY_GATE (Tradier)
- **File**: `tradier_manage.py:1633`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: `B_MAIN_ENTRY_GATE_ENABLED = False`

### 12. LINEARITY_LR Entry Filter (Tradier)
- **File**: `tradier_manage.py`, `config_tradier.py`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: `LINEARITY_LR_LONG_ENABLED = False`, `LINEARITY_LR_SHORT_ENABLED = False` (already defaults)

### 13. Leaderboard Entry Path
- **File**: `ez_manage.py:17030`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: No dedicated switch. Rarely fires in practice.

### 14. SRS NOLOSS Bypass (Structural Range Shift)
- **Files**: `tradier_manage.py`, `ez_manage.py`
- **In v8 backtest**: ⚠️ PARTIAL — SRS logic present but can't exit at loss
- **Kill**: N/A — difference is that live allows controlled-loss exit; backtest doesn't.

### 15. Hedge Engine (same-symbol hedging)
- **Files**: `ez_positions_quick.py` extensive, `ez_manage.py`
- **In v8 backtest**: ❌ NOT PRESENT
- **Kill**: `HEDGE_MODE = False` per account config

---

## Missing Config Switch TODO

These features need a NEW kill switch added to config.py to be replica-safe:

| Feature | Proposed Switch | Files to edit |
|---------|----------------|---------------|
| Advisory consumer | `FIN_ADVISORY_CONSUMER_ENABLED = True` | `config.py`, `ez_manage.py:20051` |
| WT_4H_VEL_EXIT MANDATORY_REENTRY | `WT_4H_VEL_MANDATORY_REENTRY_ENABLED = True` | `config.py`, `ez_manage.py:20578` |
| DELTA_EXIT MANDATORY_REENTRY routing | `DELTA_EXIT_MANDATORY_REENTRY_ENABLED = True` | `config.py`, `ez_manage.py:20924` |
| MANDATORY_PRICE_CROSS EPQ | `MANDATORY_PRICE_CROSS_EPQ_ENABLED = True` | `config.py`, `ez_positions_quick.py:16068` |

---

## Sanity Gap — Needs Unlock

The user directive: *"ANY reentry or hedge — even GUARANTEED or MANDATORY — MUST pass the 'don't be stupid' filter: wt_1h AND wt_15m must agree with the trade, and k must not be at extreme values."*

**Current gap in locked files:**

| Path | File | Current check | Missing |
|------|------|--------------|---------|
| GUARANTEED_REENTRY | `ez_manage.py:15870` | k_3m extremes (80/20), WT 3m+15m | wt_1h direct agreement |
| MANDATORY_REENTRY PRICE_CROSS | `ez_positions_quick.py:2909` | k_3m extremes, min 2/3 WT agree | wt_1h when exactly 2/3 agree |
| MANDATORY_PRICE_CROSS_EPQ | `ez_positions_quick.py:16046` | Block only if ALL 3 (15m/1h/4h) against | Block if EITHER 1h OR 15m disagrees |

**To fix these, unlock needed**: `ez_manage.py` + `ez_positions_quick.py`

Proposed changes (ready to apply after unlock):
1. `ez_manage.py` GUARANTEED_REENTRY block: add `_wt_1h_ok = (wt1_1h > wt2_1h if is_long else wt1_1h < wt2_1h)` check alongside existing WT 3m+15m checks; require wt_1h + (wt_3m OR wt_15m) = 2/3 including 1h mandatory.
2. `ez_positions_quick.py` MANDATORY_PRICE_CROSS: change PRICE_CROSSED_HTF_AGAINST_VETO from "all three against" to "1h OR 15m against" (flip veto from `_htf_against_long = bear_15m AND bear_1h AND bear_4h` to `bear_15m OR bear_1h`).
3. `ez_positions_quick.py` GUARANTEED_REENTRY block (line 15584): same wt_1h mandatory addition.
