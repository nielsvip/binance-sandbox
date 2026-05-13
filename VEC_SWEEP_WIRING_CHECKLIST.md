# v8_vec_sweep.py — Vectorization Wiring Checklist
Generated: 2026-05-13 | Interrupt-safe — resume from ✅/🔲 state

---

## Context
v8_vec_sweep.py is the fast pure-vec sweep engine. It currently only models 8 paths out of 30+
that fire in live trading. This checklist tracks wiring ALL relevant live paths into the sweep
so sweep results actually predict live performance.

**Key architectural note**: All vec_paths `check_*` functions use `store.f(key, idx)` /
`pos_state.side` etc. v8_vec_sweep uses a plain npz dict + SymState. Two thin adapters bridge
the gap: `_NPZStoreAdapter` and `_PosStateAdapter` (see Agent A task below).

---

## ██ ACTIVE RIGHT NOW — Wait 60 min ██

- [ ] **S1 sweep result check** — GR_EXIT variants (mult 9/12/15) ± DC_LOW4_STOP_ENABLED  
  Command: `ssh s1-int 'tail -50 ~/logs/bt_sweep_*.log | grep -E "pool_sharpe|GR_EXIT|DC_LOW4" | tail -30'`  
  After finding winner: apply to live via `config.py` knobs:  
  `GR_EXIT_ENABLED=True/False` + `GR_EXIT_MULT_THRESHOLD=9/12/15` + `DC_LOW4_STOP_ENABLED=True/False`  
  **NOTE**: DC_LOW4_STOP in sweep ≠ R1_DC in live — see Architecture Gaps below.

---

## TIER 1 — Already-coded vec modules, just need wiring into v8_vec_sweep

### T1-A: R1 + R2 Exits (replace DC_STOP proxy) — `vec_paths/exit_r1_r2.py`
Status: ✅ DONE 2026-05-13

**What**: Replace the sweep's `DC_LOW4_STOP_ENABLED` fixed-stop-at-entry block (lines 449–486)
with proper `check_r1_emergency_exit` (monitoring-based, matches live).  
Add `check_r2_wt_vel_slow_exit` to the exit cascade.

- [ ] Add `_NPZStoreAdapter` and `_PosStateAdapter` classes to v8_vec_sweep.py (before simulate_one_symbol)
- [ ] Import `check_r1_emergency_exit, check_r2_wt_vel_slow_exit` from vec_paths.exit_r1_r2 (try/except)
- [ ] Add SweepConfig fields: `R1_DC_LOW4_3M_EMERGENCY_ENABLED=True`, `R1_NEWBORN_WINDOW_MIN=15`, `R1_USE_DC_4BAR=True`, `R1_TF=""`, `WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED=True`, `WT_15M_VEL_SLOW_GAIN_FLOOR_PCT=0.01`, `WT_15M_VEL_SLOW_GAIN_BAND_PCT=0.10`, `WT_VEL_DECEL_RATIO=0.5`, `WT_VEL_USE_DECEL_RATIO_ONLY=True`, `R2_PEAK_MIN_PCT=0.5`, `R2_TF_LIST=None`
- [ ] Create `_store = _NPZStoreAdapter(npz, close, ts)` before hot loop
- [ ] Create `_pos = _PosStateAdapter(state)` before hot loop
- [ ] Each bar: update `_pos.gain_pct = gain` after computing gain
- [ ] In holding branch: call check_r1_emergency_exit FIRST (before GR_EXIT, before all other exits); if fires → close + bypass noloss
- [ ] In exit cascade after E3_STRUCTURE: call check_r2_wt_vel_slow_exit; if fires → close + bypass noloss
- [ ] Remove / gate-off `DC_LOW4_STOP_ENABLED` / `DC_LOW_STOP_ENABLED` old block (or keep as separate sweep feature with rename `DC_FIXED_STOP_ENABLED` to make it clear it's sweep-only)
- [ ] Compile check: `python -c "import py_compile; py_compile.compile('v8_vec_sweep.py', doraise=True)"`
- [ ] Smoke test: `python v8_vec_sweep.py --mode crypto --symbols BTC --start 2026-04-01 --max-bars 5000 --no-history`

### T1-B: Partial Profit Lock (PPL) — `vec_paths/partial_profit_lock_v2.py`
Status: ✅ DONE 2026-05-13

**What**: Add 3-step PPL as REDUCE events in holding branch. Already in backtest_v8_engine
(lines 3435–3479) but missing from v8_vec_sweep entirely.

- [ ] Import `check_ppl_step1, check_ppl_step2, check_ppl_step3` from vec_paths.partial_profit_lock_v2 (try/except)
- [ ] Add SweepConfig fields: `PARTIAL_PROFIT_LOCK_ENABLED=True`, `PARTIAL_PROFIT_LOCK_GAIN_PCT=0.5`, `PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT=0.75`, `PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT=0.02` (PARTIAL_PROFIT_LOCK_FRAC already there)
- [ ] Add PPL fields to _PosStateAdapter: `ppl_fired`, `ppl_first_exit_price`, `ppl_stop_level`, `ppl_stop_upgraded` (mutable on adapter, not on SymState)
- [ ] In holding branch (top, before exits): call check_ppl_step1 → if fires: add REDUCE event, update _pos.ppl_fired + _pos.ppl_first_exit_price + _pos.ppl_stop_level; update state.qty -= partial
- [ ] call check_ppl_step2 → if fires: update _pos.ppl_stop_level + _pos.ppl_stop_upgraded
- [ ] call check_ppl_step3 → if fires: add CLOSE event for remainder; record trade_return
- [ ] Reset PPL state on OPEN: `_pos.ppl_fired = False; _pos.ppl_first_exit_price = 0.0; ...`
- [ ] Compile + smoke test

### T1-C: GOLDEN_RULE Entries — `vec_paths/golden_rule_enforce.py`
Status: ✅ DONE 2026-05-13 — 133 fires in 3-sym smoke test (123×1.0x + 10×1.5x)

**What**: GOLDEN_RULE_*_mult*_INTERVENTION fires 20+ times/account/day in live. The
`check_golden_rule_enforce` function is a stateless per-bar approximation. Add it to the
FLAT branch alongside the existing reentry block check.

- [ ] Import `check_golden_rule_enforce` from vec_paths.golden_rule_enforce (try/except)
- [ ] Add SweepConfig fields: `GOLDEN_RULE_ENABLED=True`, `GOLDEN_RULE_BASE_USD=5.0`, `GOLDEN_RULE_DC_15M_ENABLED=True`, `GOLDEN_RULE_BB_15M_ENABLED=True`, `GOLDEN_RULE_DC_1H_ENABLED=True`, `GOLDEN_RULE_BB_1H_ENABLED=True`, `GOLDEN_RULE_DC_4H_ENABLED=True`, `GOLDEN_RULE_BB_4H_ENABLED=True`, `GOLDEN_RULE_MULT_15M=1.0`, `GOLDEN_RULE_MULT_1H=1.5`, `GOLDEN_RULE_MULT_4H=2.0`, `GOLDEN_RULE_MULT_D=3.0`, `GOLDEN_RULE_HTF_VETO_ENABLED=False`, `GOLDEN_RULE_MIN_IND=1`, `GOLDEN_RULE_HTF_MIN_TFS=0`
- [ ] In FLAT branch: after `fire_block or wt_open_ok` check, ALSO call `check_golden_rule_enforce(_store, i, _pos, is_long, config)` (even when qty > 0 it can AUGMENT — use is_flat flag to distinguish)
- [ ] GOLDEN_RULE result action == "OPEN" → treat as OPEN; action == "AUGMENT" → treat as AUGMENT if already holding
- [ ] The reason string from result["reason"] is already in GOLDEN_RULE_*_mult*_INTERVENTION format
- [ ] Compile + smoke test

---

## TIER 2 — Existing modules, medium wiring effort

### T2-A: Peak Giveback + BE Erosion — `vec_paths/peak_giveback_be_erosion.py`
Status: ✅ DONE 2026-05-13 (default OFF; enable via PEAK_GIVEBACK_DROP_TRIGGER_ENABLED / BE_EROSION_EXIT_ENABLED)

**What**: `QUICK_BANDAID_OFF` + `BREAK_EVEN_GUARD_EXIT` patterns. 16+ fires/account.

- [ ] Import `check_peak_giveback_exit, check_be_erosion_exit` (try/except)
- [ ] Add SweepConfig fields: `PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=False`, `PEAK_GIVEBACK_DROP_FROM_PEAK_PCT=2.0`, `PEAK_GIVEBACK_MIN_PEAK=0.5`, `BE_EROSION_ENABLED=False`, `BE_EROSION_MIN_GAIN=0.0`
- [ ] In exit cascade (after E3_STRUCTURE, before R2): call both; if fires → close via noloss gate
- [ ] Compile + smoke test

### T2-B: Reduce Paths (partial TP) — `vec_paths/reduce_paths.py`
Status: ✅ DONE 2026-05-13 (default OFF; enable via PROFIT_TAKE_REDUCE_ENABLED / STRONG_REDUCE_K_ENABLED)

**What**: `QUICK_CYCLE_TP_STOCH_AGAINST` (16+/acct), `QUICK_REDUCE_NO_PROFIT` (31/acct),
`QUICK_PROFIT_PROTECT` (5+/acct). Currently sweep models no REDUCE events at all.

- [ ] Import `check_profit_take_reduce, check_strong_reduce_k` (try/except)
- [ ] Add SweepConfig fields: `PROFIT_TAKE_REDUCE_ENABLED=False`, `PROFIT_TAKE_REDUCE_GAIN_PCT=3.0`, `STRONG_REDUCE_K_ENABLED=False`
- [ ] In holding branch (after PPL): add REDUCE event on fire; update state.qty -= reduce_qty; re-blend entry_price
- [ ] Compile + smoke test

### T2-C: WT Crossunder Final — `vec_paths/wt_crossunder_final.py`
Status: ✅ DONE 2026-05-13 — 6 fires in smoke test (WT_CROSSOVER_FINAL_3m_*)

**What**: `WT_CROSSUNDER_FINAL` exits — fires on decisive crossunder against position.

- [ ] Import `check_wt_crossunder_final_exit` (try/except)
- [ ] Add SweepConfig fields: `WT_CROSSUNDER_FINAL_ENABLED=True`
- [ ] In exit cascade (after E3_STRUCTURE): call; if fires → close via noloss gate
- [ ] Compile + smoke test

---

## TIER 3 — New vectorization needed (no current vec module)

### T3-A: IN_GAIN_TREND_EXIT
Status: ✅ DONE 2026-05-13 — exit_id=94, uses stoch_k/d_1h + ha_1h(int8) BIG tier, stoch_k/d_15m + ha_15m(int8) MED tier. 0 fires in short smoke (expected, max gain only 4.7% in window).

**What**: No vec module exists. Need to build `vec_paths/in_gain_trend.py`.  
Live source: grep `IN_GAIN_TREND_EXIT` in ez_manage.py to find the logic.

- [ ] Find IN_GAIN_TREND_EXIT logic in ez_manage.py: `grep -n "IN_GAIN_TREND" ez_manage.py`
- [ ] Write `vec_paths/in_gain_trend.py` with `check_in_gain_trend_exit(store, bar_idx, pos_state, mode, cfg)`
- [ ] Write parity test: `tools/test_in_gain_trend_parity.py`
- [ ] Wire into v8_vec_sweep exit cascade
- [ ] Compile + smoke test

### T3-B: Hedge Engine + GR trigger
Status: ✅ DONE 2026-05-13 — 824 HEDGE_OPEN/CLOSE pairs in smoke test, all balanced + in pool_sharpe
**GR trigger**: wt1_3m against AND (15m/1h OR GR_against_score >= GR_HEDGE_SCORE_FLOOR=6). Reason includes gr-score tag e.g. `HEDGE_PROTECT_SHORT_LOSS_g-0.65_GR_15m1h_gr16`

**What**: 60-70% of live AUGMENT/REDUCE actions are hedge-related. Currently 0% modeled.  
`vec_paths/hedge_engine.py` + `vec_paths/hedge_scan_gates.py` have the scaffolding.

- [ ] Decide: model hedge as a SECOND simultaneous position per symbol (complex) OR model hedge PnL impact as a separate overlay?
- [ ] Design doc: how does hedge affect reported pool_sharpe? (hedges are pairs — need both legs)
- [ ] Implement hedge engine into SymState (add hedge_qty, hedge_entry_price, hedge_side)
- [ ] Wire check_scan_hedge_losers + evaluate_hedge_scan_gates_vec into holding branch
- [ ] Handle hedge CLOSE on WT flip
- [ ] parity test vs backtest_v8_engine hedge outputs
- [ ] Wire into v8_vec_sweep

### T3-C: DELTA_ENGINE entries/exits
Status: ✅ DONE 2026-05-13 — wired as 4th entry trigger in FLAT branch, default OFF (DELTA_ENGINE_ENABLED=False). check_delta_entry takes (store, bar_idx, side, mode, cfg).

- [ ] Read vec_paths/delta_engine.py: check_delta_entry, compute_delta_speeds interfaces
- [ ] Add SweepConfig DELTA_ENGINE fields
- [ ] Wire check_delta_entry into FLAT branch (alongside reentry + GOLDEN_RULE)
- [ ] Wire DELTA_EXIT into exit cascade
- [ ] Compile + smoke test

---

## Architecture Gaps (must resolve before claiming sweep == live)

### AG-1: DC_STOP ≠ R1_DC_EMERGENCY
- **Sweep**: fixed stop price at entry; closes any time price breaks it
- **Live**: R1 monitoring within first 15 min of open only (R1_NEWBORN_WINDOW_MIN)
- **Resolution**: T1-A above — replace sweep's fixed-stop with check_r1_emergency_exit
- [ ] Old `DC_LOW4_STOP_ENABLED` renamed `DC_FIXED_STOP_ENABLED` with docstring: "SWEEP-ONLY approximation — not in live"
- [ ] Any sweep results citing DC_STOP as driver are NOT replicable in live until T1-A is done

### AG-2: GOLDEN_RULE cooldown not modeled in sweep
- **Live**: GOLDEN_RULE_COOLDOWN_S=600s — max 1 entry per 10 min per symbol
- **Sweep approximation**: fires every bar that qualifies (overcounts entries)
- **Resolution**: add simple `_gr_last_fire_ts` tracking in simulate_one_symbol SymState
- [ ] Add `gr_last_fire_ts: float = 0.0` to SymState
- [ ] After GOLDEN_RULE OPEN/AUGMENT: set `state.gr_last_fire_ts = bar_ts`
- [ ] Gate GOLDEN_RULE: only fire if `bar_ts - state.gr_last_fire_ts >= config.GOLDEN_RULE_COOLDOWN_S`

### AG-3: Hedge engine absent from sweep
- Pool Sharpe in sweep is computed on direct positions only
- Live: hedges can absorb losses, changing the effective PnL distribution significantly
- Sweep Sharpe will SYSTEMATICALLY DIFFER from live until T3-B is done
- [ ] Flag sweep outputs with `[HEDGE_NOT_MODELED]` tag in canonical_line until T3-B complete

### AG-4: Sweep SweepConfig fields NOT in live (sweep-only features)
- `GR_EXIT_ENABLED` / `GR_EXIT_MULT_THRESHOLD` — live has no GR-score-based exit
- `E_1_WT_EXIT_USE_DELTA_ENABLED` / `E_1_EXIT_DELTA_THR` — live E1 has no delta gate
- `E_3_USE_WT_STRUCTURE_EXIT_MODE` (int modes) — live uses single mode
- [ ] If sweep winner uses these → must add corresponding knobs + code to ez_manage.py before applying to live
- [ ] Document in sweep result headers which knobs are sweep-only

---

## Config Sync
Status: 🔄 Agent running (a9cc282c85a1f1abd) — comparing all SweepConfig defaults vs live config.py

---

## Post-Integration Tasks (after each tier complete)

- [ ] Smoke test every tier on BTC LONG 2026-01-01 5000 bars → no crash, reasonable trades
- [ ] Run full crypto smoke: 8 syms × 2026-01-01 → check canonical line plausible
- [ ] rsync to S1: `rsync -az --existing --update v8_vec_sweep.py niels@157.180.125.52:/home/niels/binance-sandbox/`
- [ ] Verify md5 parity Mac+S1
- [ ] Run S1 sweep with new engine on standard 8-sym pool; compare pool_sharpe vs previous run (regression check)
- [ ] If new paths significantly change Sharpe → re-run GR_EXIT sweep with new engine (old sweep used old engine)

---

## Quick Reference — Adapter Classes (paste into v8_vec_sweep.py)

```python
class _NPZStoreAdapter:
    """Wraps v8_vec_sweep npz dict to satisfy vec_paths store.f(key, idx) interface."""
    __slots__ = ("_npz", "_close", "_ts")
    def __init__(self, npz: dict, close: "np.ndarray", ts: "np.ndarray"):
        self._npz = npz; self._close = close; self._ts = ts
    def f(self, key: str, idx: int, default: float = 0.0) -> float:
        arr = self._npz.get(key)
        if arr is None or idx >= len(arr): return default
        try: return float(arr[idx])
        except Exception: return default
    def price(self, idx: int) -> float:
        return float(self._close[idx])
    @property
    def timestamps(self) -> "np.ndarray": return self._ts
    def arrays(self): return self._npz  # fallback for store.arrays.get() pattern

class _PosStateAdapter:
    """Wraps SymState to satisfy vec_paths pos_state.side / .gain_pct interface.
    Mutable: update gain_pct + PPL fields each bar."""
    __slots__ = ("_state", "gain_pct", "ppl_fired", "ppl_first_exit_price",
                 "ppl_stop_level", "ppl_stop_upgraded")
    def __init__(self, state: "SymState"):
        self._state = state
        self.gain_pct = 0.0
        self.ppl_fired = False; self.ppl_first_exit_price = 0.0
        self.ppl_stop_level = 0.0; self.ppl_stop_upgraded = False
    @property
    def open(self) -> bool: return self._state.qty > 0.0001
    @property
    def side(self) -> str: return "LONG" if self._state.is_long else "SHORT"
    @property
    def max_gain_pct(self) -> float: return self._state.max_gain
    @property
    def entry_price(self) -> float: return self._state.entry_price
    @property
    def entry_ts(self) -> float: return self._state.opened_at
    @property
    def qty(self) -> float:
        return self._state.qty if self._state.is_long else -self._state.qty
    def reset_ppl(self):
        self.ppl_fired = False; self.ppl_first_exit_price = 0.0
        self.ppl_stop_level = 0.0; self.ppl_stop_upgraded = False
```

---

## S1 Sweep Results Tracker

| Run | GR_EXIT | DC_LOW4_STOP | pool_sharpe | trades | winner? |
|-----|---------|--------------|-------------|--------|---------|
| — | — | — | — | — | — |

Fill in after 60-min check.

---

## Interrupt Resume Guide

If session is interrupted:
1. Read this file to see which ✅/🔲 items remain
2. Check `git log --oneline -5` to see what was committed
3. Check `python -c "import v8_vec_sweep; print('OK')"` to verify current state compiles
4. Resume from first 🔲 item in the lowest tier number
