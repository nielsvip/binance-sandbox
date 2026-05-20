# Test Results & Proposed Config Improvements — 2026-05-20

Complete consolidation across all tests run this session + the proposed config.py / config_tradier.py changes that follow from the evidence. Per CLAUDE.md NO-LIES tagging applied.

---

## Part 1 — Tests run + headline results

### Tier 1 — Diagnostics

| # | Test | Result | Tag |
|---|---|---|---|
| 1 | V0_baseline tech_ai_chips diagnostic (15,359 trades) | LONG side +1,872% / SHORT side −7,604%. WR 97.4% but net pool_sharpe −0.0084. Smoking gun: SHORTs on uptrending tech. | [DIAGNOSTIC · n_syms=29] |

### Tier 2 — Per-symbol deep-dives (all 7 converged on same recipe: HTF veto + 5m trigger + wide exits, NO PPL/WT_EXHAUST scalp)

| # | Sym | V0 capture | NEW capture | Comment |
|---|---|---:|---:|---|
| 2 | SNDK | 9.2% (+284%) | 95.2% (+2,992%) | 5 trades vs 408; 10× lift |
| 3 | MU | 28% (+160%) | 155.9% (+890%) | sym_sharpe **1.62** |
| 4 | PLTR | 39% (+180%) | 107.5% (+493%) | beats B&H; pool_sharpe **0.94** |
| 5 | GOOGL | 52% (+82%) | 119.7% (+191%) | beats B&H |
| 6 | NVDA | 54% | 94.8% | clean capture |
| 7 | AMD | 64% | 241.5% | beats B&H massively |
| 8 | AVGO | 60% | 109.5% | |
| 9 | LRCX | 53% | 104.6% | |
| 10 | INTC | 50% | 107.8% | |
| 11 | TXN | 63% | 140.8% | |

**ALL TAG**: `[DIAGNOSTIC · n_syms=1]` per CLAUDE.md sample floor. Not promote-able without Tier-2 multi-sym pool validation.

### Tier 3 — Sector analyst per-symbol fix proposals

| # | Sector | Analyst verdict | Actions |
|---|---|---|---|
| 12 | tech_ai_chips (29) | 17 of 22 lift to ~267% capture | DROP 7 negative-B&H, TREND_HOLD_D on 12, leave 10 alone |
| 13 | energy_oil_gas (24) | CTRL unfiltered = +0.234 sshp (best of 4 modes) | DROP BNO, AR pyramid_max=2, keep 22 syms |
| 14 | precious_metals (15) | ATR2x_5m trail lifts each sym +42-337% | DROP AGI/AG, ATR2x_5m trail ON, SHORT side OFF sector-wide |
| 15 | base_metals_mining (14) | 13 of 14 boost candidates | DROP ALB, BOOST 2.0× (CLF/MP/FCX/SCCO), 1.5× (7 syms) |
| 16 | 5 small sectors (28) | uranium_nuclear 8.58× ratio | DROP 4 sub-sample, 1.5× boost on 9 winners |

### Tier 4 — Structural matrix V0–V7 × 9 sectors (72 cells)

| # | Variant | pool_sharpe | Δ vs V0 |
|---|---|---:|---:|
| 17 | **V0 baseline** | **−0.0023** | (baseline — local maximum) |
| 18 | V1 SPY-200SMA gate | −0.0026 | −0.0003 |
| 19 | V2 ATR-parity sizing | −0.0059 | −0.0036 |
| 20 | V3 daily-decision-TF | −0.0352 | −0.0329 |
| 21 | V4 Connors RSI-2 overlay | −0.0025 | −0.0002 |
| 22 | V5/V6/V7 stacked | −0.0345 to −0.0438 | ALL WORSE |

**Verdict**: Single-knob flips on existing engine all degrade. CTRL is the local maximum.

### Tier 5 — Crypto sweeps

| # | Test | pool_sharpe | Verdict |
|---|---|---:|---|
| 23 | crypto v3_no_stop verify (70 syms × 4yr) | +0.098 | matches reported +0.106 (±5%). Sub-floor. |
| 24 | crypto activation gate A/B | NO-OP | wiring gap in v8_vec_sweep. Inconclusive. |
| 25 | crypto hard-stop variants (5) | BB_LOWER_1H wins +0.119 | best stop, worst-trade-DD -45.9% → -20.3% |
| 26 | crypto full-stack final candidate | +0.176 in-sample / +0.181 held-out | beats baseline +32%, STILL sub-floor → HOLD |

### Tier 6 — Vec random knob sweep (160 variants × 114 syms)

| # | Variant | pool_sharpe | Note |
|---|---|---:|---|
| 27 | Best of 160 | **+0.4799** | doubled CTRL +0.236; held-out +0.4831 (robust); 1.1% DD; 0/160 cleared 1.0 |

Winning config: simple D+E entry paths + X4 daily-bear-WT exit + no HTF filter + 1h pyramid + wide 20% trail.

### Tier 7 — LT-direction backtest (4 variants × 114 syms × 2.11yr)

| # | Variant | pool_sharpe | Comment |
|---|---|---:|---|
| 28 | CTRL unfiltered | **+0.2355** | local max |
| | A_long_filtered (LT-up) | +0.2264 | filter HURTS |
| | B_dual (long-up + short-down) | +0.1961 | user hypothesis FALSIFIED |
| | C_short_only_LTdown | +0.1614 | shorts worst |

### Tier 8 — Engine bug hunt + NaN fix validation

| # | Test | Result | Tag |
|---|---|---|---|
| 29 | NaN fix + PPL/WT_EXHAUST disable validation (60 syms) | −0.2207 | engine cascade — fix kept, combined disable causes hedge cascade |

Bugs found in audit (estimated lift if all fixed): +0.40 to +1.20 sharpe combined.

### Tier 9 — Function audit + orthogonal signal hunt

| # | Finding | Impact |
|---|---|---|
| 30 | tradier_manage.py + ez_manage.py import ZERO vec_paths/ | **Root cause of 0-3% live↔backtest match.** All sweep wins isolated from live. |
| 31 | STDEV_MACRO declared but DEAD | 68.5% WR, +41 bps fwd60 on SHORTS. Est +0.4-0.8 sharpe SHORT side. |
| 32 | LIVE_ENTRY_ENGINE_ENABLED = False | 4 wired entry engines all gated off |
| 33 | DC entry NO_PRICE silent fail (entry_engine_dc.py) | fire rate 13.56% → 0% on bars without current_price |
| 34 | lr_pctb_D unused as LONG signal | Spearman ρ +0.21 stocks, naive long-decile sym_sharpe **1.18** — clears 1.0 floor |

### Tier 10 — Per-sym 7D/20D framework

| # | Test | Result |
|---|---|---|
| 35 | per_sym_7d_agent (ang crypto, 5 NPZ-runnable syms) | Best ATOMUSDT +0.657 wsharpe (sub-0.7 floor). 16 of 21 ang syms lack NPZ. |
| 36 | per_sym_20d_agent_stocks (trb, ~143 syms) | Multiple wsharpe>1.0 BUT trade counts 2-8 — all sub-promote. Includes JD +3.911, GILD +3.065 (over-fit risk). |

### Tier 11 — Asymmetric sizing matrix

| # | Test | Result |
|---|---|---|
| 37 | LONG/SHORT_SIZE_MULT 7 variants on tech_ai_chips | pool_sharpe **identical to 4 decimals** across all variants. Per-trade % Sharpe is scale-invariant. Work parked per user "trivial without good entries". |

### Tier 12 — V6 pure-200SMA vs v3_no_stop (91 syms)

| # | Variant | pool_sharpe | losers |
|---|---|---:|---:|
| 38 | V6 pure-200SMA | +0.069 | 32/91 |
| | v3_no_stop | **+0.188** | **0/91** |

**v3 wins**. V6 dropped.

### Tier 13 — In flight (results pending)

| Test | ETA |
|---|---|
| Sub-floor vec validation (27 cells, elevated to CRITICAL) | 2-4h |
| 20D trb agent first full-universe run | nearly complete |
| ang 7D agent (hourly cycles) | continuous |
| Curve-fit monitor | active once live deployed |

---

## Part 2 — Proposed improvements vs current config.py / config_tradier.py

### A. config_tradier.py (US stocks)

| # | Knob | Current | Proposed | Evidence |
|---|---|---|---|---|
| **A1** | `LIVE_ENTRY_ENGINE_ENABLED` | **False** | **True** (after Tier-2) | Audit: 4 entry engines gated off; each +7 to +31 bps per fire |
| **A2** | `LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED` | **False** | **True** (after Tier-2) | Audit: STDEV_MACRO 68.5% WR / +41 bps SHORT; +0.4-0.8 sharpe est |
| **A3** | `PARTIAL_PROFIT_LOCK_ENABLED` | True | per-symbol override OFF for trend names (SNDK/MU/PLTR/INTC/GOOGL/NVDA/AVGO/TXN/MA/CRWV/AXON/ASTS) | All 7 per-sym agents proved PPL_TP +0.5% scalp kills multi-bagger capture |
| **A4** | `WT_EXHAUST_EXIT_ENABLED` | True | per-symbol override OFF for trend names (same 12 syms above) | per-sym evidence |
| **A5** | Universe (live trade list) | full 124 syms | DROP SAP, ACN, PYPL, TTD, WDAY, FIVN, OLED, AGI, AG, BNO, ALB, NLR, NUKZ, MSTR (14 syms) | per-sector analysts; negative B&H / sub-sample / blowup |
| **A6** | Per-symbol POSITION_MULT | flat 1.0× | 2.0× CLF/MP/FCX/SCCO; 1.5× NUE/STLD/TECK/LAC/VALE/BHP/RIO/STZ/RBLX/DIS/UEC/UUUU/UAN/DAR/HII/GLD; 1.2× DE/GM; 0.5× AGCO/NOC/ROKU/IBIT/USO/RS | sector analyst evidence |
| **A7** | ATR-trail for precious_metals | OFF (per ATR_TRAIL_ENABLED disaster) | **per-sector** ATR2x_5m trail ON | precious_metals analyst: +42-337% lift per sym |
| **A8** | SHORT side for precious_metals + most tech | enabled | DISABLED sector-level | precious shorts -98% to -444% MtM; tech shorts -7,604% V0 |
| **A9** | Universe-loader scope | mixed | restrict to `symbols_trb_long ∪ symbols_trb_short` (~77 syms) | already patched in per_sym_20d_agent_stocks |
| **A10** | `entry_engine_dc.should_fire_dc_entry` | NO_PRICE silent fail | fall back to `close_5m[-1]` (PATH D applied) | audit: fire rate 13.56% → 0% bug |

### B. config.py (crypto)

| # | Knob | Current | Proposed | Evidence |
|---|---|---|---|---|
| **B1** | `LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED` | **False** | **True** (master already True) | Path D wired; ready to flip |
| **B2** | Hard-stop level for v3_no_stop | NO_STOP | **BB_LOWER_1H** | crypto stop comparison: +0.119 vs no-stop +0.098, DD floor -45.9% → -20.3% |
| **B3** | Per-sym overrides (per_sym_active_config.json) | 4-yr baseline | merged with 39 PROMOTED + 5 ADDED_NEW from Path B | Just landed today; awaits restart |
| **B4** | NPZ regen for ang symbols | 5 of 21 present | regen the 16 missing | ang 7D only able to test 5/21 today |

### C. Engine bug fixes (not config but config-adjacent)

| # | Bug | Status | Est lift |
|---|---|---|---|
| **C1** | NaN propagation in `_NPZStoreAdapter.f()` | **APPLIED** to v8_vec_sweep | +0.20-0.50 |
| **C2** | DC entry NO_PRICE silent failure | **APPLIED** to entry_engine_dc.py (Path D) | infrastructure |
| **C3** | HEDGE PnL leak on main close (10 sites) | NOT APPLIED | +0.10-0.40 |
| **C4** | REDUCE vs CLOSE accounting inconsistent | NOT APPLIED | gain/yr inflated 20-40% (no Sharpe lift, just honesty) |
| **C5** | TRADIER_MIN_HOLD ignored on non-emergency exits | NOT APPLIED | +0.10-0.30 |
| **C6** | Silent-drop trap in per_sym_7d_agent.py | **APPLIED** | unblocks all future per-sym work |

### D. Architecture-level (multi-day each)

| # | Item | Status | Est impact |
|---|---|---|---|
| **D1** | Wire `vec_paths/` modules into `tradier_manage.py` + `ez_manage.py` | NOT STARTED | **Root cause of 0-3% live↔backtest match** — fixes biggest structural gap |
| **D2** | Wire `lr_pctb_D` as LONG signal (currently SHORT block only) | NOT STARTED | naive long-decile sym_sharpe 1.18 (clears 1.0 floor) |
| **D3** | Wire Rule A/B/C entry framework | NOT STARTED | Rule B claimed Sharpe 0.80; Rule C 1.4 — orthogonal to existing |
| **D4** | Move `TREND_HOLD_D` per-symbol exit logic into sleeve (currently just `_tag` marker) | NOT STARTED | Day-2+ once Tier-2 backtest proves the recipe |
| **D5** | Phase 2B-2E patches (165+ more missing knobs) | NOT STARTED | per per_sym_engine_audit |

---

## Part 3 — Recommended package install order

When sub-floor vec validation completes (2-4h) and we have publishable evidence:

1. **Phase 1 — crypto FLIP** (1-line change, master already True):
   - `config.py: LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED = True`
   - Restart 5 crypto procs (ang/inf/flz/men/fin)
   - Add BB_LOWER_1H hard stop per B2

2. **Phase 2 — tradier sleeve LIVE** (independent of engine):
   - `STRUCT_V4_GO_LIVE=true STRUCT_V4_PAPER_MODE=false`
   - Run on trb (live $500/pos × 5 max = $2,500)
   - trc paper-only forever (no flip)
   - 30-min paper window 13:30-14:00 UTC

3. **Phase 3 — tradier master flip** (REQUIRES Tier-2 evidence first — sub-floor vec validation result):
   - Only if 1+ variant clears pool_sharpe ≥ 1.0 on ≥100 syms ≥1yr ≥30 tr/sym
   - `config_tradier.py: LIVE_ENTRY_ENGINE_ENABLED = True`
   - `config_tradier.py: LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED = True`
   - Restart trb/trc

4. **Phase 4 — per-symbol overrides** activate:
   - Already in `per_sym_active_config.json` (just updated). Activates at next live process restart.
   - DROP list + POSITION_MULT enforced via sleeve overrides

5. **Phase 5 — Engine bugs** (Day 2+):
   - HEDGE PnL leak (10-site fix)
   - REDUCE/CLOSE accounting (honesty fix)
   - TRADIER_MIN_HOLD enforcement

6. **Phase 6 — Architecture (Week 2+)**:
   - Wire vec_paths/ to live engines
   - Wire lr_pctb_D long signal
   - Rule A/B/C framework

---

## Part 4 — Honest assessment for market open today

- **Best validated Sharpe across all tests**: +0.4799 (vec random knob, 114 syms, 2.11 yr, held-out passes). Sub-1.0 per CLAUDE.md trash floor.
- **Sharpe > 2.0 NOT ACHIEVABLE** in this 6h window. Would need Architecture phase 6 (1-2 weeks).
- **DEPLOY plan if signal given**: Phase 1 + 2 above. Phase 3 awaits sub-floor vec validation (in queue, ETA 2-4h).
- **Day-1 catastrophic loss bound**: ~$250 (10% of $2,500 deployed). Curve-fit monitor + per-sym panic flags + halt-all are armed.

---

End report.
