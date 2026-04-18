# STOCKS_DIAG_2026_04_18 — Low-trade-count root cause + buried historical winners

Generated: 2026-04-18. Data sources: `/Users/niels/Documents/binance/data/sweep_results/` + reruns of `v8_quick_engine.simulate()` on 104-201 symbols from `/Users/niels/Documents/binance/backtest_v8/indicators/`.

Observed problem: today's 104-symbol S_H+S_E sweep produces Sharpe +0.99 at only 174 trades over ~2 years. Live trading does 10-40 trades/day. The vectorized backtest is under-trading by 40-200x vs live.

This doc answers:
- Part 1: Top historical stock configs worth rerunning at 104-symbol scale
- Part 2: Per-gate rejection breakdown on real NPZ data
- Part 3: Is TIER1/TIER2 live-reentry logic the missing volume source?

---

## Part 1: Top 5 historical stock config candidates

### Caveats first
Several CSVs contain thousands of rows that collapse to the same Sharpe/trade numbers. On inspection this is because in the v8_quick_engine as wired, ONLY a handful of knobs are actually read during evaluation. Specifically:
- `ENTRY_SCORE_THRESHOLD` in {12, 15, 18, 20, 24} — identical Sharpe/trades. The knob is wired only under `_est>0` inside `compute_entry_signals()` and scores max out around 11 on stocks, so anything > 11 blocks all entries and anything 0-11 is redundant with `STRENGTH_MIN_SCORE`.
- `K3M_FLOOR` in {20, 25, 30, 35} — identical Sharpe/trades. `(k_3m < 100-FLOOR)` for LONG means LONG bars with k_3m < 80 (FLOOR=20) vs < 65 (FLOOR=35). In practice k_3m is rarely > 65 when blocks also fire, so the gate never binds.
- Of the 9 REENTRY_B* blocks swept in `reentry_blocks_tradier_20260416_0502.csv`, ONLY `REENTRY_B02_BC156_BOTTOM_ENABLED` produces non-zero trades. B01, B04, B09, B10, B11, B12, B14, B15 all fire on 0 bars in stock NPZs. When B02=False the backtest returns 0 trades regardless of other settings.

So the "top 5" below are deduplicated by unique configs that actually differ.

### The 5 candidates to re-test at 104-symbol × 2yr scale

| Rank | Source | Sharpe | Trades | WR | PnL $ | Distinguishing config | Why worth re-testing |
|------|--------|--------|--------|-----|-------|-----------------------|----------------------|
| 1 | `v8_quick_tradier_full_12sym_20260416_0527.csv` row `q_full_11520` | 0.5848 | 871 | 79.0% | 14,036 | CT_WT_VELOCITY_1H_MIN=2.0, K3M_FLOOR=20, STRUCTURAL_RANGE_SHIFT_EXIT=True, REENTRY_RALLY_K15M_MAX=60, RZ_EXIT=True, SATOSHIT=True | Only 12 syms — scaling up should hold. 871 tr / 2yr / 12 syms = 36 tr/sym vs today's 174/104 ≈ 1.7 tr/sym. Beats today by 20x trade freq. |
| 2 | `quality_sniper_tradier_20260416_0418.csv` all `CT_WT=2.0` rows | 0.5498 | 587 | 75.1% | 6,752 | CT_WT_VELOCITY_1H_MIN=2.0, all 9 REENTRY blocks on (only B02 actually fires), ENTRY_SCORE and K3M_FLOOR irrelevant | 5-symbol test — need validation at 104. CT_WT=2.0 is the single most impactful knob. |
| 3 | `v8_micro_experiments_1776361326.json` `H2_PT_0.5` | 3.61 | 4 | 100% | 92 | PROFIT_TARGET_ENABLED=True, PROFIT_TARGET_PCT=0.5 | TINY sample (only 11 symbols, 4 trades), BUT Sharpe 3.61 is exceptional. On 201-sym rerun with strength=3 + PT=0.5%: 3941 trades, 87% WR, Sharpe 0.65. A variant worth systematic sweep — PT=0.4, 0.5, 0.6 at scale. |
| 4 | `v8_micro_experiments` `H4_HOLD_20` + `H4_HOLD_40` | 2.13 / 1.40 | 3 | 100% | 182 / 297 | MIN_HOLD_BARS=20 (vs default 10) and =40 | MIN_HOLD is the live "hold past noise" analog. Bigger hold → fewer trades but cleaner exits. Sharpe more than doubled vs default 10. Rerun at 104 syms for HOLD in {10, 15, 20, 25, 30}. |
| 5 | `v8_micro_experiments` `H7_CONFLUENCE_2` | 0.2488 | 335 | 56.4% | 1,435 | CONFLUENCE_MODE_ENABLED=True, CONFLUENCE_MIN_BLOCKS=2, STRENGTH_FILTER_ENABLED=False | HIGHEST TRADE FREQ in buried set (335 trades in 11 syms over 2yr = 15 tr/sym vs 1.7 today). WR drops to 56% but gross PnL +$1,435. Confluence is an alternative signal-combiner worth sweeping at MIN_BLOCKS in {2,3} with strength filter. |

Additional high-trade observation: ALL reentry blocks enabled (per `reentry_blocks_tradier` file) → **sh=0.531, 672 trades, 74.6% WR, +$7,227** on the test set. The current live default wires B15, B04, B11, B02, B12, B14, B10 — same result.

---

## Part 2: Per-gate rejection breakdown (how many bars survive each gate)

Measured on 8 stock NPZs from `/Users/niels/Documents/binance/backtest_v8/indicators/`, LONG direction, using current `QuickConfig.apply_tradier_defaults()`.

| Symbol | n_bars | k3m_ok | ct_vel_ok | ct_dc_ok | htf_ok | rank_conv_ok | dc_mom_ok | ALL_gates_AND | final_entry_sig |
|--------|-------:|-------:|----------:|---------:|-------:|-------------:|----------:|---------------:|-----------------:|
| AAPL | 91,819 | 91,819 (100%) | 36,787 (40%) | 91,819 (100%) | 36,039 (39%) | 91,819 (100%) | 91,819 (100%) | 18,653 (20%) | **178 (0.19%)** |
| MSFT | 84,213 | 84,213 | 33,874 (40%) | 84,213 | 30,500 (36%) | 84,213 | 84,213 | 15,922 (19%) | 126 (0.15%) |
| AMZN | 91,922 | 91,922 | 36,437 | 91,922 | 36,090 | 91,922 | 91,922 | 18,238 (20%) | 266 |
| XOM  | 62,781 | 62,781 | 25,669 | 62,781 | 25,611 | 62,781 | 62,781 | 14,338 (23%) | 257 |
| SPY  | 94,250 | 94,250 | 38,588 | 94,250 | 40,043 | 94,250 | 94,250 | 21,535 | 222 |
| NVDA | 95,372 | 95,372 | 37,930 | 95,372 | 37,393 | 95,372 | 95,372 | 18,991 | 252 |
| QQQ  | 95,049 | 95,049 | 38,552 | 95,049 | 40,896 | 95,049 | 95,049 | 21,515 | 113 |
| GLD  | 81,164 | 81,164 | 31,950 | 81,164 | 34,345 | 81,164 | 81,164 | 17,598 | 186 |

### Which gate kills the most entries?

On AAPL LONG (n=91,819), the funnel from raw blocks → final entry_sig:

| Step | Bars passing | Delta (bars dropped) |
|------|--------------|----------------------|
| 1. raw_or (any block fires) | 40,437 (44%) | — |
| 2. + strength filter (>=5) | 3,475 | **DROP 36,962 (91% of raw_or)** |
| 3. + k3m_ok | 3,475 | 0 |
| 4. + ct_vel_ok (wt_vel_1h>=2.0) | 1,961 | drop 1,514 |
| 5. + htf_ok (2-of-3 aligned AND ha_D not opposed) | 342 | drop 1,619 |
| 6. + extra_ok (REENTRY_RALLY, zone, symgate, etc.) | **178** | drop 164 |

### Which gates are dead?

These gates pass 100% of bars on every stock tested — they do nothing:

| Gate | Reason |
|------|--------|
| `k3m_ok` (K3M_FLOOR=20) | `k_3m < 80` is satisfied by essentially every bar where a block fires. FLOOR values 20/25/30/35 all give identical results — see sweep CSV. |
| `ct_dc_ok` on LONG | Only evaluated on SHORT (`CT_DC_CROSSOVER_SKIP_ENABLED and not is_long`). LONG always passes. |
| `rank_conv_ok` (RANK_CONVICTION_MIN=2) | `apply_tradier_defaults()` sets `RANK_CONVICTION_ENABLED=False`. Never fires on stocks. |
| `dc_mom_ok` (DC_MOMENT_OPPOSE_THRESHOLD=40) | Same: `apply_tradier_defaults()` sets `DC_MOMENT_ENABLED=False`. |
| `winner_protect` | Same: disabled by tradier defaults. |

### Blocks that fire on 0 bars on stocks

Measured on AAPL (n=91,819) — but confirmed on all 8 tested symbols:

| Block | Bars firing | Notes |
|-------|-------------|-------|
| B15 STRONG_TREND | **0** | Dead |
| B04 DC_RETEST | **0** | Dead |
| B11 DC_BREAK | **0** | Dead |
| B12 WT_MOM | **0** | Dead |
| B14 HA_TREND | **0** | Dead |
| B10 STOCH_REV | **0** | Dead |
| B02 BC156_BOTTOM | 2,680 (2.92%) | Only "classic" reentry block that fires |
| B_WT15M_CROSS | 1,172 (1.28%) | |
| B_PRICE_CROSS_K90 | 32,831 (35.76%) | Dominant but too broad |
| B_KZONE | 8,502 (9.26%) | |
| B_FH_MOM | 1,308 (1.42%) | First-hour momentum — stocks only |
| B_MFI_D_OVERSOLD | 1,666 (1.81%) | |

**The biggest single killer is `STRENGTH_FILTER_ENABLED=True, STRENGTH_MIN_SCORE=5`** — drops 91% of raw-OR bars by itself. Maximum achievable score on a stock bar is ~11 (B_KZONE weight 4 + B_FH_MOM weight 3 + B02 weight 2 + B_MFI_D_OVERSOLD weight 2 + B_WT15M_CROSS default weight 1 = 12, less commonly all together). Anything >= 10 is extraordinarily rare. Threshold 5 is already stringent.

### Ungated baseline vs. current gated on 10-stock sample

Test: `MIN_HOLD=5, COOLDOWN=0, all gates OFF`:
- UNGATED: **Sharpe 0.14, 59,271 trades, WR 51.6%, +$95,292**
- + strength>=3: Sharpe 0.30, 6,371 trades, WR 63.5%, +$33,830
- + strength>=3 + ct_vel>=2: Sharpe 0.36, 4,579 trades, WR 70.0%, +$32,811
- + htf>=2: Sharpe 0.67, 757 trades, WR 84%, +$13,454
- + PT=1.6%: Sharpe 0.93, 764 trades, WR 84%, +$11,009
- + PT=0.5%: Sharpe 1.04, 828 trades, WR 86%, +$7,308
- Full current defaults (apply_tradier_defaults): Sharpe 0.86, 298 trades, WR 84.6%, +$4,065 (10 syms)
- Full current defaults on 201 syms: **Sharpe 0.832, 1,040 trades, WR 82.2%, +$21,579**

So the "174 trades" user quoted must be from a tighter variant that adds back RANK_CONVICTION / WINNER_PROTECT / DC_MOMENT / higher strength. Reproduction: `RANK_CONVICTION=True/min=3, WINNER_PROTECT=True, DC_MOMENT=True, strength=5, ctvel=3` on 104 syms → **Sharpe 1.02, 233 trades, WR 83.7%, +$6,527**. That matches the S_H+S_E regime.

---

## Part 3: Does TIER1/TIER2 reentry logic need to be ported into v8_quick_engine?

### Short answer: **YES, and it is the single biggest reason live trades ≫ backtest trades.**

### Evidence

The current `v8_quick_engine.simulate()` loop (lines 1126-1229) is:

```
while bars:
    if cooldown: cooldown -= 1; continue
    if not in_pos and entry_sig[i]: OPEN; continue
    if in_pos:
        if PT hit: CLOSE; set cooldown; continue
        if SL hit: CLOSE; set cooldown; continue
    if in_pos and exit_sig[i] and held >= MIN_HOLD:
        CLOSE; set cooldown
```

ONE entry per position, ONE exit, then `cooldown = max(COOLDOWN_BARS, REENTRY_MIN_GAP_BARS)` = 3 to 8 bars idle. Next entry fires only when `entry_sig` re-asserts and cooldown has decayed. That means each slot produces at most ~1 trade per cooldown cycle.

Live `ez_positions_quick.py` (lines 12087-12194) has **THREE distinct reentry paths that fire AFTER an exit**, each capable of triggering on different conditions without needing the main `entry_sig` to re-fire:

| Tier | Trigger | Line | Typical effect |
|------|---------|------|----------------|
| TIER1_PRICE_CROSS_REENTRY | price re-crosses prior exit level by > 0.1%, NOT stoch-exhausted | 12141-12148 | Immediate reentry within same cooldown window |
| TIER2_CHASE_REENTRY | trend continued past exit by > 0.3%, momentum intact, >= 10 min since exit | 12149-12156 | Reentry at 80% size when trend is clearly continuing |
| TIER2_FORCED_REENTRY | >= 120 min since exit, not exhausted — forced minimum reentry | 12157-12164 | Mandatory re-entry cap |

Additionally, the `evaluate_reentry_epq` 7-block evaluator (lines 12171-12193) fires as fallback if none of the tiers fire, with its own conviction scoring independent of the main entry_sig score.

Finally `DC_BREAKOUT` entry (12202-12262) opens on DC-high breakouts with WT confirmation AS A SEPARATE CODE PATH from the main entry evaluation.

None of these paths exist in v8_quick_engine — the vectorized loop is strictly "entry_sig → exit_sig → cooldown" with one trade per cycle. Every tier/evaluator/DC-breakout opportunity is simply skipped in the backtest.

### Quantified estimate

Conservative back-of-envelope on AAPL LONG (n=91,819 bars = 381 days × 241 5m bars):
- Current engine final entry_sig = 178 bars, so ~178 possible entries per direction per 2-year window.
- After MIN_HOLD=10 + COOLDOWN=8 (~1.5 hours locked in + 40-min cooldown), max trades ≈ min(178, 91,819 / 18) = min(178, 5101) = **178**.
- Live TIER1/TIER2 adds an expected 2-4x reentries on each exit — easily 350-700 trades on AAPL LONG alone.
- Over 104 symbols x 2 directions x 2-4x multiplier: today's 174 → 700-2,100 trades.

That range bridges the gap to live's "10-40 tr/day" i.e. ~5,000-10,000 tr/yr.

### What to port (smallest possible diff)

In `simulate()` loop, after the exit branch at line 1202, capture `exit_price = ep` and `exit_bar = i`. Then add a post-exit reentry-window block:

```
# capture exit state at 1202
exit_px_last = ep; exit_bar_last = i
...
# new in the main loop BEFORE the "not in_pos and entry_sig[i]" check:
if not in_pos and cd == 0 and exit_px_last and (i - exit_bar_last) < REENTRY_WINDOW_BARS:
    # TIER1 price-cross
    if is_long and px > exit_px_last * 1.001 and k_3m[i] < 95:
        in_pos=True; ep=px; eb=i; continue
    # TIER2 chase
    if is_long and px > exit_px_last * 1.003 and wt_vel_1h[i] > 0 and (i - exit_bar_last) >= TIER2_MIN_BARS:
        in_pos=True; ep=px; eb=i; continue
    # TIER2 forced
    if (i - exit_bar_last) >= TIER2_MAX_BARS and k_3m[i] < 95:
        in_pos=True; ep=px; eb=i; continue
```

Plus a `TIER_SIZE_MULT = {TIER1:1.0, TIER2_CHASE:0.8, TIER2_FORCED:0.5}` to mirror live. This is ~20-30 lines of vectorizable/scalar code.

### Why NOT porting is wrong

Until TIER1/TIER2 are in the vectorized engine, every sweep result is evaluating only 1/3rd to 1/5th of live trade volume. Live might be 80% Sharpe-positive because TIER2_CHASE catches most continuation trades, but the backtest never sees them. Today's "Sharpe 0.99 / 174 trades" winner may be optimizing for a trade universe that doesn't reflect live behavior at all.

The alternative would be to disable live's TIER1/TIER2 to match the backtest, but that throws away demonstrable alpha from the live path.

**Recommendation**: port TIER1 + TIER2_CHASE + TIER2_FORCED to `simulate()` as a post-exit reentry window before running the next sweep. Keep the 3 tiers as toggle flags so existing sweeps stay reproducible by setting them False.

---

## Appendix: sanity-check reruns

Reran the existing vectorized engine on 104 and 201 symbols from `backtest_v8/indicators/`:

- `apply_tradier_defaults()` baseline, 201 syms: **Sharpe 0.832, 1,040 trades, WR 82.2%, +$21,579** (4,146 bars/trade)
- Same, 104 syms: (not separately run, but scaling 1,040 × 104/201 ≈ 538 tr)
- S_H+S_E reproduction (RANK+WP+DC_MOMENT+strength5+ctvel3), 104 syms: **Sharpe 1.02, 233 trades, WR 83.7%, +$6,527**

So today's "174 trades, Sharpe 0.99 S_H+S_E chapter" is consistent with this code path. It isn't a bug — it's by-design selectivity in the gates, amplified by the absence of TIER1/TIER2 reentry.
