# BTC-Dedicated Loop Design — `flz:BTCUSDC` (and BTC-config-only `inf:BTCUSDC_SHORT`)

**Status:** DESIGN DOC, awaiting user approval. No code has been written.
**Owner:** flz primarily. Spec applies to ANY account that trades BTC (only inf today).
**Stakes:** 20x leverage. 2.5% loss = 50% account wipe. Will run 1000× capital of other crypto loops once proven.
**Anchor metric:** Phase 1 baseline B (NOLOSS=False) on BTCUSDT 4yr = pool_sharpe **+0.0899** / +30% PnL / 1390 trades / 50.4% WR. Anything we ship must clearly beat this.
**Anchor levels:** see `btc_levels_snapshot_20260427.json` — currently densest red-zone is $69k–$73k cluster; cycle high $126k untested.

---

## 1. Architecture overview

```
                     ┌──────────────────────────────────────────────┐
                     │  ez_positions_quick.process_positions_account│
                     │  per-account loop                            │
                     └─────┬─────────────────────────────────────────┘
                           │
            ┌──────────────┴──────────────┐
            │ symbol routing / per-key    │
            │ tradeable + hard-block gate │
            └──────┬─────────────────┬────┘
                   │                 │
   symbol ∉ BTC*   │                 │  symbol ∈ {BTCUSDC, BTCUSDT}
                   ↓                 ↓
   existing rate() ─→ entries     ┌─────────────────────────────────┐
                                  │  btc_dedicated_loop()           │  NEW
                                  │  uses BTC_* config exclusively  │
                                  │  ignores generic crypto knobs   │
                                  └────────┬────────────────────────┘
                                           │
            ┌──────────────────────────────┼──────────────────────────────┐
            ↓                              ↓                              ↓
   BTC entry pipeline           BTC exit pipeline               BTC risk gate
   (existing + new)             (existing + new)                (20x-aware)
```

- New code is gated behind `BTC_DEDICATED_ENABLED` (default **False**). When False, BTC trades fall through to the standard rate() pipeline (current behavior).
- `BTC_DEDICATED_ENABLED=False` lets us deploy the code dormant on MacBook + S1 + S2, run the sweep on the new code path via env-flag, then flip live ON only after user approval.

---

## 2. `BTC_*` config namespace (proposed)

To live in `config.py` with mirror in `v8_quick_engine.py:QuickConfig` for sweepability. Every flag default-OFF or default-conservative.

### Master switches
```python
BTC_DEDICATED_ENABLED: bool = False
BTC_DEDICATED_ACCOUNTS: list = ["flz", "inf"]   # who routes through this loop
BTC_HARD_BLOCK_OTHER_ACCOUNTS: bool = True       # block ang/men/fin from BTC at is_tradeable
```

### Red-zone composition
```python
BTC_RZ_USE_WT_DC: bool = True       # existing wt_dc_delta zones
BTC_RZ_USE_FIB: bool = True         # NEW — fib levels
BTC_RZ_USE_ROUND: bool = True       # NEW — round-number bands
BTC_RZ_FIB_TFS: list = ["4h","D","W","M","Y"]
BTC_RZ_FIB_LOOKBACK = {"4h":200,"D":180,"W":104,"M":24,"Y":5}
BTC_RZ_ROUND_INC_USD: list = [5000, 1000]      # primary + secondary grid
BTC_RZ_PROXIMITY_PCT: float = 0.5              # within 0.5% of any level = "active red zone"
```

### Accelerating WT delta gate (the primary BTC entry trigger)
```python
BTC_ACCEL_RAMP_ENABLED: bool = True
BTC_ACCEL_RAMP_TFS: list = ["3m","15m","1h","4h","D"]   # all five must accelerate
BTC_ACCEL_RAMP_MIN_TFS: int = 5                          # default strict (all)
BTC_ACCEL_RAMP_REQUIRE_POSITIVE: bool = True             # Δwt > Δwt_prev > 0 (true ramp)
BTC_ACCEL_RAMP_PRICE_BOUNCE_TF: str = "3m"               # price-bounce confirmation TF
BTC_ACCEL_RAMP_PRICE_BOUNCE_BARS: int = 3                # within last N bars
```

### Divergence (continuous monitoring, BTC-clockwork)
```python
BTC_DIVERGENCE_ENABLED: bool = True
BTC_DIVERGENCE_INDICATORS: list = ["WT","RSI","MFI","OBV","CVD"]
BTC_DIVERGENCE_TFS: list = ["3m","15m","1h","4h","D"]
BTC_DIVERGENCE_BULL_MIN_INDS: int = 2          # 2-of-5 indicators must show bull div
BTC_DIVERGENCE_BEAR_MIN_INDS: int = 2
BTC_DIVERGENCE_LOOKBACK_BARS: int = 5
BTC_DIVERGENCE_BLOCK_AGAINST: bool = True      # bear div blocks LONG entries; bull div blocks SHORT
BTC_DIVERGENCE_EXIT_AGAINST: bool = True       # bear div exits LONG positions
```

### Entry triggers (combined logic)
```python
# Primary entry: 3m bounce off active red zone + accel-ramp on all TFs + no opposing div.
BTC_ENTRY_PRIMARY_REQUIRE_RZ: bool = True
BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP: bool = True
BTC_ENTRY_PRIMARY_BLOCK_OPPOSING_DIV: bool = True

# Secondary entry: divergence-only (when accel ramp not aligned).
BTC_ENTRY_DIV_ONLY_ENABLED: bool = False       # opt-in via sweep
BTC_ENTRY_DIV_ONLY_MIN_INDS: int = 3           # tighter than monitoring threshold
```

### Risk path A — HEDGE (sweep all settings)
```python
BTC_RISK_PATH: str = "hedge"                   # "hedge" | "technical" — set per-sweep variant
BTC_HEDGE_ENABLED: bool = True
BTC_HEDGE_TRIGGER_LOSS_PCT: float = -1.0       # tighter than crypto -2.0 due to 20x
BTC_HEDGE_SAME_SYMBOL_PCT: float = 1.0
BTC_HEDGE_MIN_HOLD_BARS: int = 10
BTC_HEDGE_WT_KILL_CONFIRM_TF: str = "1h"
BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = True
BTC_HEDGE_WT_VEL_GATE_ENABLED: bool = True
BTC_HEDGE_REQUIRE_4OF5_WT_TFS: bool = True     # per existing strict rule
BTC_HEDGE_NEVER_CLOSE_AT_LOSS: bool = True     # per memory feedback_hedge_no_close_at_loss
```

### Risk path B — TECHNICAL EXIT + GUARANTEED REENTRY
```python
# Sell at WT/DC technical exit at any P/L (including loss). Re-enter on next valid setup.
BTC_TECH_EXIT_ENABLED: bool = False            # mutually exclusive with BTC_RISK_PATH=hedge
BTC_TECH_EXIT_WT_MIN_TFS: int = 3              # 3+ TFs against = exit
BTC_TECH_EXIT_DC_BREACH_TF: str = "15m"        # dc_low breach on 15m = exit LONG
BTC_TECH_EXIT_AT_ANY_PNL: bool = True          # bypass NOLOSS / strict-no-loss

# Reentry guarantee: track "force_reentry" flag set on technical exit.
BTC_GUARANTEED_REENTRY_ENABLED: bool = True
BTC_GUARANTEED_REENTRY_MAX_AGE_BARS: int = 480 # 480 × 3m = 24h max persistence
BTC_GUARANTEED_REENTRY_MIN_GAP_BARS: int = 5   # min 15min between exit and reentry
BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE: bool = True  # must still see bounce off red zone
BTC_GUARANTEED_REENTRY_SIZE_MULT: float = 1.0  # full size reentry
```

### 20x leverage risk caps (HARD)
```python
BTC_LEVERAGE: float = 20.0
BTC_PER_TRADE_NOTIONAL_USD_MAX: float = 100.0  # per-trade notional ceiling (will scale with proven track)
BTC_TOTAL_NOTIONAL_USD_MAX: float = 300.0      # max concurrent notional across all BTC positions
BTC_DAILY_LOSS_PCT_FLOOR: float = -0.5         # halt new entries if day PnL < -0.5%
BTC_WEEKLY_LOSS_PCT_FLOOR: float = -1.5        # halt all BTC trading for 24h if week PnL < -1.5%
BTC_PYRAMID_DISABLED: bool = True              # no augmenting at 20x
BTC_INTRABAR_REVERSAL_EXIT: bool = True        # exit on accel sign-flip without TF confirm
BTC_REGIME_PAUSE_ENABLED: bool = True          # pause new entries during BTC funding-rate spike or extreme OI
```

### Sweep-validated knobs (set after Phase 7)
Will be filled in once Phase 7 sweep produces winners. Placeholder for now.

---

## 3. Hard-block other accounts from BTC

Two layers of defense:

**Layer 1 — `tradeable_keys.json` data:**
- Keep `flz:BTCUSDC_LONG`, `flz:BTCUSDC_SHORT`
- Keep `inf:BTCUSDC_SHORT` (per user — inf can trade BTC, but only with BTC config)
- DELETE `ang:BTCUSDC_LONG` (line 31)

**Layer 2 — `ez_positions_quick.py` is_tradeable gate (~line 1787):**
```python
# BTC dedicated-loop config gate (defense in depth)
_BTC_SYMS = ('BTCUSDC', 'BTCUSDT')
if symbol in _BTC_SYMS and getattr(config, 'BTC_HARD_BLOCK_OTHER_ACCOUNTS', True):
    if account_key not in getattr(config, 'BTC_DEDICATED_ACCOUNTS', ['flz','inf']):
        return 0, "BTC_HARD_BLOCKED", f"BTC trading restricted to BTC_DEDICATED_ACCOUNTS"
```

---

## 4. Entry pipeline (BTC dedicated loop)

```
EVERY 3m bar for flz:BTCUSDC (and inf:BTCUSDC_SHORT):

1. Compute key/fib/round levels (cached, refresh on new local H/L per TF)
2. Compute red-zone proximity (within 0.5% of ANY level)
3. Compute multi-indicator divergences (WT, RSI, MFI, OBV, CVD on 3m+15m+1h+4h+D)
4. Compute accel-ramp boolean (Δwt > Δwt_prev > 0 on all 5 TFs)

5. Veto checks (any True → SKIP entry):
   a. opposing divergence ≥ MIN_INDS (e.g. bear div on 2+ inds blocks LONG)
   b. opposing HTF trend (already-existing HTF_DIRECTION_GATE)
   c. funding rate against side (existing FUNDING_GATE)
   d. daily-loss floor breached (BTC_DAILY_LOSS_PCT_FLOOR)
   e. notional cap breached (BTC_TOTAL_NOTIONAL_USD_MAX)

6. Trigger composition (any composite True → ENTRY):
   PRIMARY: red_zone_active AND accel_ramp_aligned AND price_bounce_3m
   SECONDARY (opt-in): divergence_strong (≥3 inds) AND red_zone_active
   TERTIARY (opt-in): existing rate() winner ≥ ENTRY_SCORE AND red_zone_active

7. Sizing:
   - Base: BTC_PER_TRADE_NOTIONAL_USD_MAX × CONVICTION_MULT (1.0 default; sweep)
   - Cap by BTC_TOTAL_NOTIONAL_USD_MAX − current_open_notional
   - HARD ABORT if computed size > BTC_PER_TRADE_NOTIONAL_USD_MAX

8. Order via execute_now() (per CLAUDE.md ONLY GATE rule)
```

---

## 5. Exit pipeline (BTC dedicated loop)

Two mutually-exclusive risk paths sweepable per variant:

### Risk path A — HEDGE (default)
```
1. Existing exits (RZ_EXIT, WT crossunder min-TFs, structural range shift) — keep
2. New BTC_DIVERGENCE_EXIT_AGAINST — exit on opposing divergence
3. New BTC_INTRABAR_REVERSAL_EXIT — exit on accel sign-flip without TF confirm
4. NO closure at loss; instead: open hedge per BTC_HEDGE_* knobs
5. Hedge close ONLY on WT 3m+1h flip + non-negative gain (per memory)
```

### Risk path B — TECHNICAL EXIT + GUARANTEED REENTRY
```
1. Existing technical exits + new BTC ones — fire at any P/L
2. On exit, set force_reentry flag + record exit_price + exit_reason
3. Every subsequent bar: if force_reentry active AND red_zone_bounce confirmed AND age<MAX_AGE → REENTER
4. NO HEDGE in this path
5. Reentry persists across crypto-worker restarts (lives in tracker.json)
```

---

## 6. Sweep validation criteria (Phase 7)

Before flipping `BTC_DEDICATED_ENABLED=True`:

| Metric | Floor | Notes |
|---|---|---|
| pool_sharpe (4yr BTCUSDT) | ≥ 1.5 | per CLAUDE.md "below 2 = trash" relaxed for single-sym BTC |
| accumulated_gain_pct | ≥ 200% | beats Phase 1 baseline B (149%) by >35% |
| max_dd_pct | ≤ 8% | absolute critical at 20x — 2.5%=50% wipe |
| trades / 4yr | ≥ 200 | enough sample to be meaningful (≥50/yr) |
| WR | 40-65% | reject overfit-narrow patterns |
| deflated_sharpe | ≥ 0 (positive) | escapes random-noise ceiling |
| gain_vs_bh | ≥ 1.5× | clearly beats buy-and-hold |
| Sweep iterations | ≥ 2000 | for DSR to be meaningful |
| Tier 2 replay | within 20% of Tier 1 | backtest_v8_engine.py confirms |

---

## 7. Files to edit (when user approves)

| File | What | Approx Lines |
|---|---|---|
| `config.py` | Add BTC_* namespace (~50 keys) | new section ~line 285 |
| `v8_quick_engine.py:QuickConfig` | Mirror BTC_* keys for sweepability | ~line 200 |
| `ez_indicators.py` | Add `compute_fib_levels`, `compute_round_levels`, RSI/MFI/OBV/CVD divergence detection, multi-TF accel-ramp boolean | ~lines 1600-1700 |
| `wt_dc_delta.py` | Optionally consume fib + round levels in `_run_redzone()` | ~line 800 |
| `ez_positions_quick.py` | Add `btc_dedicated_loop()` async function; route flz:BTCUSDC + inf:BTCUSDC_SHORT through it; hard-block other accts | ~lines 1787 (gate), ~lines 3500-3700 (loop body) |
| `tradeable_keys.json` | Delete line 31 (`ang:BTCUSDC_LONG`) | one-line edit |

After every edit: `cp file backups/before_<desc>_<TS>.py` BEFORE editing, then rsync MB→S1→S2 + md5 verify (per CLAUDE.md sandbox parity).

---

## 8. Phased rollout (post-design-approval)

1. **Phase 5** (this approval gates) — implement indicators (fib/round/multi-divergence/accel-ramp) and ship to NPZ via precompute. Add BTC_* config. Rsync MB+S1+S2.
2. **Phase 6** — wire `btc_dedicated_loop()` in ez_positions_quick.py with `BTC_DEDICATED_ENABLED=False`. Rsync. No live behavior change.
3. **Phase 7** — sweep BTC_* knobs in v8_quick_engine, two variants (Path A hedge, Path B technical+reentry). Must hit Section 6 floors.
4. **Phase 8** — flip `BTC_DEDICATED_ENABLED=True` on **paper-mode flz** clone account (no real $) for ≥3 trading days; verify decisions JSONL shows trades fire on accel-ramp + red-zone + divergence as designed.
5. **Phase 9** — user explicit approval → flip live config flag. Start with `BTC_PER_TRADE_NOTIONAL_USD_MAX=100` (small) and only scale up after live validation.

---

## 9. Open questions for user

1. **Risk path preference for first sweep batch:** lead with (A) hedge or (B) technical-exit-with-reentry, or sweep both equally?
2. **`inf:BTCUSDC_SHORT` semantics:** when this loop is live, does inf's existing position migrate to BTC config immediately, or stay frozen until close? (User said use BTC config; assume "from now on use BTC config" unless told otherwise.)
3. **`ang:BTCUSDC_LONG` line 31 removal:** OK to delete now? No live ang BTC position exists.
4. **Notional cap starting value:** $100/trade × max 3 concurrent = $300 total notional. Times 20x = $6000 effective exposure. Sane for "1000× capital later" runway? Or want different starting point?
5. **Paper-mode flz clone:** do we have a paper flz account configured, or do I need to set one up?
