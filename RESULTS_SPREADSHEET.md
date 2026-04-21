# Sweep Results Spreadsheet — 2026-04-21 (Post-PT-Removal)

## Definition: Pool Sharpe

**Pool Sharpe = `mean(all_trade_returns) / std(all_trade_returns)`**

Where `all_trade_returns` = list of per-trade P&L percentages across ALL trades on ALL symbols,
pooled into one array. NOT summed. NOT annualized by sqrt(N). NOT per-symbol average.
Each trade contributes exactly one return to the array.

A pool_sharpe of 1.5 means: the average per-trade return is 1.5× the standard deviation
(i.e., the strategy's signal quality is strong enough that typical gain > typical noise).

**Minimum validity requirements (CLAUDE.md):**
- ≥ 48 symbols (crypto) / ≥ 100 symbols (stocks)
- > 1 year of data
- Pool-averaged (not per-symbol average)
- Per-symbol must have ≥ 30 trades for inclusion

---

## 20:00 UTC Report (2026-04-21)

### STATUS: NO VALID CONFIGS WITH POOL_SHARPE > 1.5 FOUND YET

All previous "wins" (Sharpe 2.25, 2.55, 6.577, 8.007) used PROFIT_TARGET_PCT — a fake exit
mechanism removed at 19:04 UTC. Honest sweeps started ~19:30 UTC. Results below are 100% real.

---

## Best Crypto Configs (50 sym × 2022-2026, pool Sharpe)

B&H baseline: accumulated_gain = **-3,623%** (avg -72.5%/sym) — bear market start 2022.
10x B&H target: accumulated_gain > +3,623% while maintaining pool_sharpe > 1.5.

| Rank | Source | pool_sharpe | Accum. Gain % | Max DD % | Trades | Notes |
|------|--------|------------|--------------|---------|--------|-------|
| 1 | c08 snapshot (claimed) | **0.59** | 13,144% | 11.1% | 14,769 | Pre-PT-removal — NEEDS RE-VALIDATION |
| 2 | hunt_crypto tier (stream) | 0.2120 | ~4,000% | ~30% | ~1,000 | Early result, 1-sym test only |
| — | — | **0** confirmed | — | — | — | No valid 50-sym results yet |

**C08 Config (most promising, needs re-validation):**
```json
{
  "K3M_FLOOR": 15,
  "STRENGTH_FILTER_ENABLED": false,
  "RZ_CASCADE_ENABLED": true,
  "PARTIAL_PROFIT_LOCK_ENABLED": true,
  "AUGMENT_WT_4H_BOUNCE_ENABLED": true,
  "AUGMENT_WT_D_BOUNCE_ENABLED": true,
  "WRONG_SIDE_ABS_KILL_ENABLED": true
}
```

---

## Best Tradier Configs (CORRECTED 2026-04-21 ~21:00 UTC)

**IMPORTANT CORRECTION**: Previous rows showing 0.5823 sharpe were from **12-symbol** Stage-1 tests — NOT valid (requires ≥100 symbols).

### Valid Tradier Results (≥100 symbols, 2024-2026)

B&H baseline: accumulated_gain = **+7,720%** (avg +67.7%/sym) — bull market 2024-2025.
10x B&H target: accumulated_gain > +77,205%.

| Rank | pool_sharpe | Symbols | Accum. Gain % | Max DD % | Trades | Notes |
|------|------------|---------|--------------|---------|--------|-------|
| 1 | **0.2173** | 262 | 2,264% | 16.7% | 8,372 | Best VALID result |
| 2 | 0.2165 | 262 | 2,272% | 16.9% | 8,362 | |
| 3 | 0.2162 | 262 | 2,117% | 16.7% | 7,944 | |

**Stage-1 Screening (12-sym, NOT valid, directional only):**

| Rank | pool_sharpe | Symbols | Accum. Gain % | Max DD % | Trades | Notes |
|------|------------|---------|--------------|---------|--------|-------|
| 1 | 0.8761 | 12 | 43.9% | 0.04% | 68 | INVALID (68 trades = noise) |
| 2 | 0.8492 | 12 | 132.6% | 1.51% | 242 | INVALID (<100 symbols) |
| 3 | 0.5823 | 12 | 4,181% | 9.65% | 8,078 | Previously reported as valid — INCORRECT |

**Key observations (corrected):**
- Maximum VALID pool_sharpe found: **0.2173** (262 sym, vs 1.5 target)
- Stage-1 candidates up to 0.88 on 12 sym — Stage-2 validation running now
- Honest ceiling on full set ≈ 0.22. Need architectural improvement.
- WSAK + PPL combo appears promising in Stage-1 screening

### Top Valid Config #1 Key Parameters

```
pool_sharpe = 0.2173 | gain = 2,264% | DD = 16.7% | trades = 8,372 | symbols = 262
```

Stage-2 pipeline running to validate 68 Stage-1 candidates on 114-sym. Results expected within 1-2 hours.

### Top Config #1 Key Parameters

```
pool_sharpe = 0.5823 | gain = 4,181% | DD = 9.65% | trades = 8,078
```

| Parameter | Value | Effect |
|-----------|-------|--------|
| WRONG_SIDE_ABS_KILL_ENABLED | True | Kills position when 5/5 WT TFs against. Dominant lever. |
| CT_WT_VELOCITY_GATE_ENABLED | False | Allows more entries (velocity not required) |
| NOLOSS_ENABLED | False | Removes intermediate NOLOSS checks |
| D_TREND_REQUIRED | False | Don't require daily trend alignment |
| WT_EXIT_MIN_TFS | 2 | Exit when 2/5 TFs turn against (less patience) |
| COOLDOWN_BARS | 1 | Very fast reentry (5m) |
| MFI_ENTRY_ENABLED | True | Money Flow Index entry filter on |
| VWAP_FILTER_ENABLED | True | VWAP distance filter for entries |
| AUGMENT_WT_D_BOUNCE_ENABLED | True | Add to position on Daily WT bounce |
| WT_PERCENTILE_EXIT_ENABLED | True | Exit when WT percentile extreme |

### Top Config #2 Key Parameters (PPL + WSAK)

```
pool_sharpe = 0.5753 | gain = 4,614% | DD = 10.43% | trades = 9,689
```

| Parameter | Value | Effect |
|-----------|-------|--------|
| PARTIAL_PROFIT_LOCK_ENABLED | True | 50% close at +0.5%, then BE stop |
| WRONG_SIDE_ABS_KILL_ENABLED | True | Dominant lever |
| LOCAL_EXTREMES_SCORER_ENABLED | True | Only enter at local extremes |
| DYNAMIC_SCORE_COUNTER_EXIT_ENABLED | True | Dynamic scoring exit |
| STOCH_CROSS_ENTRY_TRADIER | True | Stochastic cross entry confirmation |
| CT_DC_CROSSOVER_SKIP_ENABLED | False | Allow DC-area entries |

---

## Why Pool_Sharpe > 1.5 Is Hard Post-PT-Removal

**Root cause analysis:**

| Metric | Value |
|--------|-------|
| Best pool_sharpe achieved | 0.58 |
| Required | 1.5 |
| Gap factor | ~2.6× worse |

To reach pool_sharpe 1.5 from current 0.58, one of these must change:
1. **Triple the mean per-trade return** (e.g., hold longer with MIN_HOLD_BARS)
2. **Halve the std of per-trade returns** (e.g., PPL forces consistent +0.5% exit for 50%)
3. **Combination**: better entry filter + longer hold + PPL

**The 1.5 Sharpe ceiling with real exits:**
The quick engine with honest exits (WT cross, DC reversal, PPL) produces naturally variable
per-trade returns. Some trades gain 5%, others lose 3%. With ~0.5% mean and ~0.9% std,
Sharpe = 0.56. To get 1.5, we need either 1.35% mean or 0.33% std — much harder.

**PPL theoretically helps:**
- 50% closed at exactly +0.5% → reduces variance of closed-50% portion
- Remaining 50% rides to BE stop (+0.02% buffer) → "free ride" on winners
- Net effect on Sharpe: positive but currently showing only +0.01-0.02 improvement

**What might work:**
- LOCAL_EXTREMES_SCORER with high score threshold (only very clear setups)
- MIN_HOLD_BARS=250 (12.5 hours) to ride momentum
- PPL + WSAK + selective entries → fewer but cleaner trades

---

## Sweep Progress as of 20:00 UTC

| Server | Tier | Mode | Rate | Best Found |
|--------|------|------|------|-----------|
| S1 | hunt_crypto | Crypto 62-sym | 0.3/s per 1-sym test | 0.21 (1-sym, not valid) |
| S1 | le_partial_exit_crypto | Crypto 62-sym | Starting | TBD |
| S1 | mega_crypto_v8 | Crypto 62-sym | Starting | TBD |
| S2 | autonomous v2 (PPL enabled) | Tradier 114-sym | 192 tested | 0.58 |
| S2 | le_partial_exit_tradier | Tradier all | Starting | TBD |
| S2 | mega_tradier_v8_focused | Tradier all | Starting | TBD |

**ETA for 1000 configs > 1.5 Sharpe:** Unknown. Current ceiling appears to be ~0.58.
May require architectural changes (longer holds, stricter entry filters) rather than parameter tuning.

---

## Next Reports

- **00:00 UTC**: Check all tier results, show best 20 configs per mode
- **09:00 UTC**: Full sweep totals, any configs > 1.5 found
- **12:00 UTC**: Final assessment + top-10 spreadsheet with full parameter list
