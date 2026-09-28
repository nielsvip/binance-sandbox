# PROPOSAL — FILTER_TF Wave 1 live-side twins (2026-09-28)

Status: **AWAITING APPLICATION OUTSIDE THIS SESSION** (live files not touched per wave order).
Vec side landed the identical semantics in `vec_decisions/filter_tf_gates.py` + engine wiring
(WAVE1 block replacing the deleted hash-proxy dispatcher). Everything below is default-neutral:
with the switch at its default (OFF) live behavior is bit-unchanged.

## Config additions/changes (config.py AND config_tradier.py)

```python
MOM3_FILTER_TF: str = "OFF"               # WAVE1: TF-selectable hard MOM3 gate (OFF = today's behavior)
MOMENTUM_BREAKOUT_FILTER_TF: str = "OFF"  # WAVE1: momentum-continuation confirmation gate
FAST_RISER_FILTER_TF: str = "OFF"         # WAVE1: TF for quick-jump double detection (OFF = today's 3m-only path stays sole source)
```
(If the names already exist as unread stubs, change their value to "OFF" and delete the stub reads.)

## 1. MOM3_FILTER_TF — hard entry gate variant of the real MOM3 factor

Insert in the entry-candidate evaluation of BOTH managers, next to the existing MOM3 factor
(ez_manage.py:40976; find the tradier factor twin), BEFORE the score vote:

```python
_m3tf = str(getattr(config, "MOM3_FILTER_TF", "OFF") or "OFF").strip()
if _m3tf.upper() != "OFF":
    _c3tf = float(i.get(f"close_3bar_{_m3tf}", 0) or 0)
    if _c3tf > 0:  # fail-open when data missing, same as the factor
        _m3v = (current_price - _c3tf) / _c3tf * 100
        _m3ok = (_m3v < config.MOM3_LONG_THRESHOLD) if is_long else (_m3v > config.MOM3_SHORT_THRESHOLD)
        if not _m3ok:
            return blocked(f"MOM3_FILTER_TF_{_m3tf}_BLOCK({_m3v:.2f}%)")  # adapt to local block idiom
```

## 2. MOMENTUM_BREAKOUT_FILTER_TF — continuation confirmation gate

Same insertion point; the existing live read (tradier_manage.py:32561 and the ":32149
REAL-WIRED" comment) is a stub farm — replace it with:

```python
_mbtf = str(getattr(config, "MOMENTUM_BREAKOUT_FILTER_TF", "OFF") or "OFF").strip()
if _mbtf.upper() != "OFF":
    _c3b = float(i.get(f"close_3bar_{_mbtf}", 0) or 0)
    if _c3b > 0:
        _mbv = (current_price - _c3b) / _c3b * 100
        if not ((_mbv > 0) if is_long else (_mbv < 0)):
            return blocked(f"MOMENTUM_BREAKOUT_TF_{_mbtf}_BLOCK({_mbv:.2f}%)")
```

## 3. FAST_RISER_FILTER_TF — TF-selectable quick-jump double

Live base: ez_manage.py:52393-52468 (FAST_RISER_DOUBLE, 3m-hardcoded). Change the three
`*_3m` reads to `*_{FAST_RISER_FILTER_TF}` when the switch != OFF, keeping every other
condition identical; OFF keeps today's 3m literals:

```python
_frtf = str(getattr(config, "FAST_RISER_FILTER_TF", "OFF") or "OFF").strip()
_fr_sfx = _frtf if _frtf.upper() != "OFF" else "3m"
# close_3m_prev -> i.get(f"close_{_fr_sfx}_prev"), low_3m/low_3m_prev, ha_3m likewise
```

**UNIT AMBIGUITY TO RESOLVE ON APPLICATION (NO-LIES flag):** ez_manage.py:52400 gates on
`current_gain > 0.8` but logs `gain={current_gain:.2%}` — if `current_gain` is a fraction,
the live threshold is 80 % and the feature effectively never fires; the vec twin uses
0.8 PERCENT (`FAST_RISER_MIN_GAIN_PCT` in vec_decisions/filter_tf_gates.py). Whoever applies
this must check `current_gain`'s producer and align BOTH sides to the same unit.

## Verdicts for the other two wave families (no live diff — DEAD_VEC handover)

- `LIVE_ENTRY_ENGINE_FILTER_TF`: live engine is observability-only
  (ez_positions_quick.py:70 / tradier_manage.py:2032 — size_mult always 1.0). Nothing to
  gate on either side → DEAD_VEC reason "live engine observability-only; no trading semantics".
- `HA_WICK_QUALITY_TF` (+_ENABLED/_SCORE, 240 orange rows): NPZ carries only HA color/streak
  int8 per TF — no HA open/high/low, so a wick-quality score cannot be computed honestly.
  DEAD_VEC reason "NPZ lacks HA wick components; needs backtest_v8_precompute extension first".
  If wanted, the precompute change (emit ha_wick_upper_{tf}/ha_wick_lower_{tf}) is the
  prerequisite ticket.

---

# WAVE 2 APPENDIX — seven generic FILTER_TF live twins (2026-09-28, default-OFF)

Vec side: `vec_decisions/generic_filter_tf.py` (engine cut 58e2bddf). Live twins below are
IDENTICAL semantics, inserted at the entry-candidate / reduce / erosion-exit choke points of
BOTH managers. All default "OFF" — bit-neutral until swept. Generic insertion helper (adapt
the block idiom per site):

```python
def _ftf_tf(name):
    _tf = str(getattr(config, name, "OFF") or "OFF").strip()
    return None if _tf.upper() == "OFF" else _tf
```

1. **BB_PULLBACK_GATE_FILTER_TF** (entry gate): block pullback entry unless
   `bb_pct_b_{tf}` extreme — long `i.get(f"bb_pct_b_{tf}") <= 0.20`, short `>= 0.80`
   (standard %B extreme bands; same constants as vec, documented choice).
2. **BB_RECOVERY_ENTRY_FILTER_TF** (entry gate): long `bb_pct_b_{tf} > 0.5`, short `< 0.5`
   (mid-band reclaim).
3. **BB_RECOVERY_FILTER_TF**: same condition, applied to entry AND reentry paths
   (doc-declared generic variant of #2 — sheet deltas will mirror #2 where reentry ≡ entry).
4. **DC_BREAK_FILTER_TF** (entry gate): long `current_price > dc_high_{tf}_PREV`, short
   `< dc_low_{tf}_prev`. MUST use the prior-bar channel — the current-bar channel contains
   the bar's own extreme and structurally never passes (vec proof: 100% block until fixed).
5. **BT_WT_CROSS_LADDER_FILTER_TF** (entry gate): long `wt1_{tf} > wt2_{tf}`, short `<`.
6. **DC_BREACH_REDUCE_FILTER_TF** (reduce confirm): partial reduces fire only when
   long `current_price <= dc_low_{tf}` / short `>= dc_high_{tf}` (breach against position).
7. **BREAKEVEN_GAIN_EROSION_FILTER_TF** (erosion-exit confirm): the peak-erosion close
   requires momentum against on TF — long `wt1_{tf} < wt2_{tf}`, short `>`.

All seven fail-open when the TF array is missing/zero (same as vec). REMINDER — the Wave-1
**FAST_RISER unit ambiguity** (ez_manage.py:52400 `current_gain > 0.8` vs `:.2%` log) is
still unresolved and must be settled when applying these.

---

# WAVE 4 APPENDIX — VEC_ONLY names needing identical LIVE additions (default-neutral)

These are REAL in the vector engine but absent live (census VEC_ONLY). Per USER order
("or gets added to live identically"), add the identical read to ez_manage/tradier_manage
at the matching decision point; all defaults already behavior-preserving:
LH_HL_FILTER_MODE, LH_HL_FILTER_REQUIRE_BOTH, LH_HL_FILTER_TF_REQ (LH/HL structure entry
filter family), BB_PROFIT_TAKE_TF, BB_EXIT_AT_LOSS_TF (BB-band exit TFs),
WT_DC_STOCH_THRESHOLD_LONG/SHORT, WT_DC_DC_POS_THRESHOLD_SHORT (WT_DC entry thresholds —
live wt_dc adapter exists, thresholds unread), EMA_9_21_FILTER_MIN_TFS (vec kindergarten
block reads it; live EMA 9/21 filter should honor the same min-TF count).
Find each name's vec read (grep vec_decisions/ + v12_quick_engine.py) and mirror the exact
condition; never a stub.

# WAVE 4 LIVE-PARITY DEFAULT NOTE (baseline shift, intentional per USER absolute-parity order)

HTF_DIRECTION_GATE vectorized (ez_positions_quick.py:12092-12140/12658) with LIVE defaults
(ENABLED True, MIN_CONFIRMATIONS 3, D_MANDATORY True, SMA200D True) — vec baselines now
include the live open-gate: XLMUSDT_LONG 30d 4.4168→4.0645; AAPL_SHORT 30d loses ALL opens
(live's gate equally blocks those shorts). Vec applies the gate to all entries; live exempts
SCALP_V3/RZ-bypass/hedge families (vec is family-agnostic — slight over-blocking, disclosed).
MI_EXIT sub-switch defaults aligned to live getattr defaults (True×5, MIN 3, GAIN 0.10);
master MI_EXIT_ENABLED remains False (live). EMA_BLANKET_FILTER_ENABLED forced False —
its True default sat on an unread field; live has no blanket gate (census NEITHER).
