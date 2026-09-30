# SWITCH WIRING GUIDE — every missing config / config_tradier switch → an identical, vectorized numpy function

> Generated 2026-09-30. Authoritative worklist + recipe for wiring **every template switch that is not yet REAL in the vector engine** (`v12_quick_engine.py`) to a numpy function that is **byte-for-byte identical in behavior** to its live `ez_manage.py` (crypto) / `tradier_manage.py` (stocks) counterpart. No proxies, no fabrication, no hardcoded defaults.

This document is a companion to `BACKTEST_BIBLE.md` (§14.3, §16, §17, §18, §19, §30). Where the two disagree, the BIBLE wins.

---

## 0. THE LAW (read before touching anything)

1. **IDENTICAL, not approximate.** The numpy function must reproduce the *exact* decision the live `ez_manage` / `tradier_manage` function makes on the same bar. Same thresholds, same comparison operators, same TF, same guard conditions. If live says `px > ema*(1+pct)` you write `close > ema*(1+pct)` — never `rsi>55` as a 'balanced' stand-in.
2. **NO FABRICATION (NO-LIES §19).** Forbidden forever: `mask[0] ^= True`, `np.arange(n)%k`, `hash(param)`-seeded bit flips, `RandomState`, any 'distinctness'/'guarantee'/'balanced' proxy, and read-only `_ = getattr(cfg, X)` scaffolding. A non-zero delta is credible **only** when the trade ledger actually changed. An honest 0 (non-binding / no-op candidate) must stay 0.
3. **NEVER a hardcoded default.** Every switch must exist in `config.py` (crypto) AND `config_tradier.py` (stocks) so `_cfg` / `getattr(cfg, ...)` resolves the real per-venue value — never a literal fallback baked into the call. The default value in each config MUST equal the **bold** value of that switch's row in the matching `TEMPLATE_{venue}_{side}.xlsx`.
4. **15m is the lowest timeframe.** 3m and 5m are ignored for now. Any live logic that reads `*_3m` / `*_5m` is ported to read `*_15m` (the NPZ has **no** 3m/5m arrays — only 15m/1h/4h/D/W). See §4.
5. **Prove it with the ledger.** A wiring is DONE only when flipping the switch changes `simulate_one`'s trade ledger AND the scalar `backtest_v12_engine` produces the same change (parity). See §8.
6. **Backup → edit → compile → verify → deploy.** Never revert/restore a whole file; only diff and re-add what is missing (§9).

---

## 1. THE FOUR SURFACES AND HOW THEY RESOLVE

Every switch lives on four surfaces. All four must agree.

| Surface | File | Role | Reads |
|---|---|---|---|
| Crypto live | `ez_manage.py` | real-money crypto decisions | `config.py` (crypto) |
| Stocks live | `tradier_manage.py` | real-money stock decisions | `config_tradier.py` ONLY (`config = TradierConfig()` @ line 5730) |
| Vector sweep | `v12_quick_engine.py` (`simulate_one` + `compute_*_signals` + `vec_decisions/`) | fills TEMPLATE sheets | `QuickConfig` seeded from config/config_tradier per mode |
| Defaults | `config.py`, `config_tradier.py`, `data/cat_side_defaults_4.json` | bold template values, 4 sides | consumed by all three |

**Resolution order (both venues):** per-symbol override → `cat_side_defaults_4.json` (CRYPTO_LONG/CRYPTO_SHORT/STOCKS_LONG/STOCKS_SHORT, sourced from template bold rows) → global (`config.py` / `config_tradier.py`). `tradier_manage._cfg(param, default, account, sym, side)` walks: per-sym recipe → final book → account active_config → regime → `getattr(config=TradierConfig, param, default)`. **It never reads crypto `config.py`.** The only way `_cfg` ever returned its literal `default` was a switch missing from `config_tradier` — which is why every switch must be present there (all 468 template switches now are).

**Why identical-but-vectorized:** the sweep baseline must equal what live would do, or the deltas are lies (§17). The vector twin is the same predicate evaluated across all bars at once instead of one bar per tick.

---

## 2. THE CANONICAL WIRING RECIPE (12 steps)

Do this for **one switch at a time**. Never batch-wire blindly.

```
STEP 1  Pick the switch from the worklist (§10). Note its lifecycle tab + venue(s).
STEP 2  Read the LIVE function:
           grep -n '<SWITCH>' ez_manage.py tradier_manage.py
        Open the FIRST real decision site (not a dataclass decl, not a registry list,
        not a `_ = 1` stub). Read the whole enclosing block: the guard, the indicators
        it reads, the comparison, and what it does (block entry / add exit / size mult).
STEP 3  List the indicators the live block reads (ind.get('...'), safe_fetch_float(...)).
        Map every 3m/5m key -> 15m (see §4). Confirm each maps to an NPZ key (see §5).
STEP 4  cp v12_quick_engine.py backups/before_wire_<SWITCH>_$(date +%Y%m%d%H%M).py
STEP 5  Create the numpy twin in vec_decisions/ (see §6 for module conventions):
           vec_decisions/<family>.py  ->  def <switch>_mask_vec(npz, n, cfg, is_long, _safe): -> np.ndarray[bool]
        Translate the live predicate 1:1 using the patterns in §7.
STEP 6  Add ONE call site in v12_quick_engine.simulate_one (or compute_entry/exit_signals)
        at the correct lifecycle point (see §6). Gate it on the switch differing from its
        effective default; apply the mask (entry_sig &= ~block, exit_sig |= fire, etc.).
STEP 7  Ensure the QuickConfig field exists (v12_quick_engine.py, class QuickConfig) with
        a fallback default; the real value flows from config/config_tradier per mode.
STEP 8  Confirm config.py (crypto) AND config_tradier.py (stocks) both define the switch,
        value == the bold value in TEMPLATE_{venue}_{side}.xlsx. (All 468 already do; verify.)
STEP 9  Compile: python -c "import py_compile; py_compile.compile('v12_quick_engine.py', doraise=True)"
STEP 10 Prove ledger change (see §8): flip the switch on 2-3 symbols where it should bind;
        trades/gain MUST change. If it changes NOTHING on any symbol AND live also changes
        nothing -> it is an honest 0, leave it (do NOT fabricate).
STEP 11 Prove parity: scalar backtest_v12_engine flip == vector flip (same ledger delta).
STEP 12 Deploy (see §9): rsync engine to s1/s2/s5, md5 verify, import-check. Log in tracker.
```

### 2.1 Worked example — `EMA50_15M_ENTRY_FILTER_ENABLED` (done 2026-09-30, use as the template)

**Live (`ez_manage.py:36213-36220`)** — the exact predicate:
```python
if bool(getattr(config, 'EMA50_15M_ENTRY_FILTER_ENABLED', False)) and _ema50_15m > 0 and _px > 0:
    _ema_filter_pct = float(getattr(config, 'EMA50_15M_ENTRY_FILTER_PCT', 0.0)) / 100.0
    if _is_long and _px <= _ema50_15m * (1.0 + _ema_filter_pct):   # BLOCK long
        continue
    if (not _is_long) and _px >= _ema50_15m * (1.0 - _ema_filter_pct):  # BLOCK short
        continue
```
**Vector twin (`v12_quick_engine.simulate_one`, entry-filter section ~line 22163)** — identical, vectorized:
```python
if bool(getattr(cfg, 'EMA50_15M_ENTRY_FILTER_ENABLED', False)):
    _ema50_15m = _safe(npz, 'ema_50_15m', n, 0.0)
    _ema_filter_pct = float(getattr(cfg, 'EMA50_15M_ENTRY_FILTER_PCT', 0.0) or 0.0) / 100.0
    _ema_valid = (_ema50_15m > 0) & (close > 0)
    if is_long:
        _ema_block = _ema_valid & (close <= _ema50_15m * (1.0 + _ema_filter_pct))
    else:
        _ema_block = _ema_valid & (close >= _ema50_15m * (1.0 - _ema_filter_pct))
    entry_sig = entry_sig & ~_ema_block
    _entry_filter_masks.append(~_ema_block)
```
**Proof it is wired (not fabricated):** `simulate_one` on RVNUSDT_LONG with `EMA50_15M_ENTRY_FILTER_PCT=2.0` moved the ledger **121 -> 75 trades, -14.52% -> +0.18%**. At pct=0 it was honestly non-binding on RVN (entries already above EMA50) — a real 0, kept.

Note the 1:1 mapping: `_px`->`close`, `continue`(skip entry)->`entry_sig &= ~block`, same operators, same guard `ema>0 & px>0`, same `/100.0`. That is the standard you replicate for every switch below.

---

## 3. LIFECYCLE -> WHERE THE CALL SITE GOES

`simulate_one` builds four boolean signals then walks bars. Put your mask at the matching stage:

| Lifecycle | Signal to modify | How | Typical call site |
|---|---|---|---|
| ENTRY gate/filter | `entry_sig` | `entry_sig &= ~block_mask` (and append to `_entry_filter_masks`) | entry-filter section of `simulate_one` after `compute_entry_signals` |
| ENTRY trigger (adds entries) | `entry_sig` | `entry_sig |= fire_mask` | inside/after `compute_entry_signals` |
| EXIT | `exit_sig` | `exit_sig |= fire_mask` | `compute_exit_signals` or the exit assembly in `simulate_one` |
| AUGMENT | `augment_sig` | `augment_sig |= fire_mask` (gain-gated in the bar walk) | augment assembly |
| REDUCE | `reduce_sig` | `reduce_sig |= fire_mask` | reduce assembly |
| REENTRY | `entry_sig` + reentry filter masks | gate via `_entry_filter_masks` + `REENTRY_ENTRY_FILTER_*` | reentry-filter block ~line 22163+ |
| SIZING/RISK | size multiplier / position cap | `mult *= factor` in the sizing chokepoint | `_size_qty` / sizing section |

Never add a call inside a disabled passthrough farm (§10.0). Those are dead by design.

---

## 4. TIMEFRAME RULE — 15m is the floor

The frozen NPZ has arrays for **15m, 1h, 4h, D, W only** (verified: 153/153/151/147/136 keys respectively). There is **no 3m or 5m** array. Therefore:

- Live reads `*_3m` or `*_5m` -> port to `*_15m`.
- Live reads `wt1_3m` / `k_3m` / `close_5m` etc. -> `wt1_15m` / `stoch_k_15m` / `close_15m` (or `close`).
- A switch whose *entire* meaning is a sub-15m micro-trigger (e.g. `K3M_FLOOR`) is ported at 15m and documented as a 15m approximation in the call-site comment. Never invent a 3m array.
- `_parse_tf_list` already aliases `5m->3m`; for the vector twin collapse both to `15m`.

---

## 5. NPZ INDICATOR CATALOG (what the vector twin may read)

802 keys / 213 indicator bases, each usually per-TF (`<base>_<tf>` and often `<base>_<tf>_prev`). Read with `_safe(npz, key, n, default)` (float array) or `_safeb(npz, key, n)` (bool). Families you will use most:

- **Price/OHLC:** `close`, `open_<tf>`, `high_<tf>`, `low_<tf>`, `close_<tf>`, `close_<tf>_prev`, `close_3bar`, `close_5bar`.
- **WaveTrend:** `wt1_<tf>`, `wt2_<tf>`, `wt_velocity_<tf>`, `wt_peak_<tf>`, `wt_trough_<tf>`, `wt_peak_prev_<tf>`, `wt_peak_structure_<tf>`, `wt_cross*`, `div_reg_bull_wt`, `div_reg_bear_wt`, `div_hid_*`.
- **Donchian:** `dc_high_<tf>`, `dc_low_<tf>`, `dc_basis_<tf>`, `dc_position_<tf>`, `dc_high4`, `dc_low4`, `dc_width_<tf>`, `dc_*_crossover`, `dc_*_crossunder`, `dc_*_ant`.
- **Bollinger:** `bb_upper_<tf>`, `bb_lower_<tf>`, `bb_middle_<tf>`, `bb_pct_b_<tf>`, `bb_width_<tf>`, `bb_width_pct_<tf>`, `bb_touches_<tf>`.
- **Momentum/oscillators:** `rsi_<tf>`, `connors_rsi_<tf>`, `stoch_k_<tf>`, `stoch_d_<tf>`, `k_<tf>`, `d_<tf>`, `mfi_<tf>`, `adx_<tf>`, `choppiness_<tf>`, `clenow_slope_<tf>`, `clenow_r2_<tf>`.
- **Moving averages:** `ema_9_<tf>`, `ema_14_<tf>`, `ema_20_<tf>`, `ema_50_<tf>`, `ema_200_<tf>`, `sma_50_<tf>`, `sma_200_<tf>`, `ema_9_above_21_<tf>`, `ema_50_above_200_<tf>`.
- **Volatility/vol:** `atr_<tf>`, `relative_volume_<tf>`, `bar_vol_*`, `bar_atr_rank_<tf>`.
- **Bar structure / Heikin-Ashi:** `ha_<tf>` (via `_ha_int`), `bar_pattern*`, `bar_body_ratio`, `bar_upper_wick`, `bar_lower_wick`, `bar_streak`, `bar_swing_bull/bear`.
- **Regression bands (grey band):** `lrL_*`, `lrL_r2_<tf>`, `band_*`.

If the live function needs a value that is **not** in the NPZ (e.g. Fear&Greed index, funding rate, order-book walls, live leaderboard), the switch is **not vectorizable from the frozen NPZ**. Do NOT proxy it. Either (a) add the series to `backtest_v8_precompute.py` first, or (b) mark it `VEC_UNSUPPORTED` with a reason and leave it honest-0. Funding-rate is the only officially crypto-only family.

---

## 6. vec_decisions/ MODULE CONVENTIONS

There are already 162 real modules in `vec_decisions/` (38 are wired into `simulate_one`). Follow their shape:

```python
# vec_decisions/<family>.py
import numpy as np
def <switch_lower>_mask_vec(npz, n, cfg, is_long, _safe):
    """Faithful vector twin of ez_manage.py:<LINES> / tradier_manage.py:<LINES>.
    Returns a bool ndarray[n]: True where the live predicate fires (block or exit)."""
    v = _safe(npz, '<indicator>_15m', n, <default>)
    if is_long:
        return <numpy boolean expression matching live LONG branch>
    return <numpy boolean expression matching live SHORT branch>
```

Call it once from `v12_quick_engine`:
```python
import vec_decisions.<family>
try:
    if bool(getattr(cfg, '<SWITCH>', <default>)):
        _m = vec_decisions.<family>.<switch_lower>_mask_vec(npz, n, cfg, is_long, _safe)
        entry_sig = entry_sig & ~_m   # or exit_sig |= _m , etc. per §3
except Exception:
    pass
```
Keep the module pure (no disk I/O, no globals, no per-bar Python loop). One switch family per file; name it after the live concept.

---

## 7. TRANSLATION PATTERNS — live idiom -> numpy

| Live (`ez_manage`) | Vector (`v12_quick_engine`) |
|---|---|
| `float(ind.get('rsi_1h', 50))` | `_safe(npz, 'rsi_1h', n, 50.0)` |
| `safe_fetch_float(ind.get('ema_50_15m',0),0)` | `_safe(npz, 'ema_50_15m', n, 0.0)` |
| `ind.get('wt1_15m_prev', x)` | `_safe(npz, 'wt1_15m_prev', n, 0.0)` (precomputed prev array) |
| bar-by-bar `if px < ema: continue` | `block = close < ema; entry_sig &= ~block` |
| `wt1 crosses below wt2` | `(wt1 < wt2) & (wt1_prev >= wt2_prev)` using `*_prev` arrays |
| `k_1h > 80 and rising` | `(k1h > 80) & (k1h > _safe(npz,'k_1h_prev',n,k1h))` |
| multi-TF OR list `'15m,1h,4h'` | `m = np.zeros(n,bool)` then `for tf in tfs: m |= cond(tf)` |
| multi-TF AND (N-of-K) | `votes = sum(cond(tf).astype(int) for tf in tfs); m = votes >= min_tfs` |
| scored exit `score += w; exit if score>=thr` | accumulate `score = np.zeros(n); score += w*cond; fire = score >= thr` |
| `getattr(config, X, d) != default` guard | `bool(getattr(cfg, X, d))` / `abs(float(getattr(cfg,X,d))-default) > 1e-9` |
| percent buffer `* (1 + pct/100)` | identical: `* (1.0 + pct/100.0)` |

The scored-exit pattern matters: many exits in `ez_manage` add weighted points across TFs and fire on a threshold (e.g. `WT_DIV_EXIT` adds 6/10/14 per TF). The vector twin must reproduce the **score + threshold**, not OR each component directly, or it will over-fire and break parity.

---

## 8. VERIFICATION (a wiring is not done until these pass)

```python
from tools.opt.v12_pilot import prepare_batch
from tools.opt.evaluate_v12 import evaluate_prepared
p = prepare_batch('RVNUSDT_LONG', 30)
base = evaluate_prepared(p, {}, include_ledger=True)
on   = evaluate_prepared(p, {'<SWITCH>': <non-default value>}, include_ledger=True)
assert (on['trades'], round(on['gain_pct'],6)) != (base['trades'], round(base['gain_pct'],6)), 'switch does not move ledger on this symbol'
```
1. **Determinism:** same overrides twice -> identical `gain_pct` (§21).
2. **Idempotency:** setting the switch to its effective default -> delta 0 (§21).
3. **Ledger change:** flipping to a non-default value changes trades/gain on >=1 symbol where it should bind. If it changes nothing on ANY symbol and LIVE also changes nothing -> honest 0, keep it (do NOT fabricate).
4. **Parity:** scalar `backtest_v12_engine` flip == vector flip. Run the parity harness on the same frozen 30d NPZ. The whole point is that the vector twin equals live.
5. **No timing regression:** cached eval stays ~0.07s (§22). No per-bar Python loop, no per-row disk reload.

---

## 9. DEPLOYMENT

```bash
cp v12_quick_engine.py backups/before_wire_<SWITCH>_$(date +%Y%m%d%H%M).py   # backup FIRST
python -c "import py_compile; py_compile.compile('v12_quick_engine.py', doraise=True)"
# md5 the sandbox first to detect drift; NEVER overwrite newer work — diff + re-add only what is missing
for h in s1-int s2 s5; do rsync -az v12_quick_engine.py $h:~/binance-sandbox/; ssh $h 'md5sum ~/binance-sandbox/v12_quick_engine.py'; done
for h in s1-int s2 s5; do ssh $h 'cd ~/binance-sandbox && .venv/bin/python -c "import v12_quick_engine"'; done  # import check
```
Running pilots keep their loaded engine; new pilots pick up the change. A genuine wiring shifts baselines — that is expected and rides a fresh progress dir, not a mid-sheet injection. Announce engine cuts per the fleet protocol.

---

## 10. THE WORKLIST — every switch not yet REAL in the vector engine

Status legend per switch: `ez=` crypto-live wiring, `td=` stocks-live wiring, `vec=` current vector status (all are STUB/ABSENT here — that is why they are on the list). `ez_line` is the first real decision site to copy from. `inds` are the indicators that site reads (already 3m/5m->15m where noted). ABSENT in a venue means the live function itself is missing there and must be carbon-copied from the other venue first.

### 10.0 DO NOT wire into these (disabled passthrough farms — dead by design, NO-LIES)
`_apply_new_audit_causal`, `_apply_auto_wired_params`, `_apply_universal_distinctness_fallback`, `_wire_07_exit_stops_tranche`, `_apply_batch2_entry_gates`, `_apply_batch2_exit_gates`, `_apply_batch2_augment_gates`, `_apply_625_entry_gates`, `_apply_625_exit_gates`, `_apply_625_sizing_mult`, `_apply_625_generic_gates`, `_apply_PZ_causal`, `_batch1_template_wiring`. These return their inputs unchanged. Any 'wiring' inside them is a fabricated proxy and must stay dead.

### 10.1  ENTRY  (40 switches) — tabs: ENTRY_REVERSAL_BOUNCE / ENTRY_BREAKOUT_CHANNEL / ENTRY_CONFIRMATION_GATES

- **`BTC_BREAKOUT_ENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6754`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'BTC_BREAKOUT_ENTRY_ENABLED', False)):`
  - indicators: close, dc_position_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::btc_breakout_entry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`DC_BREAKOUT_TF_EXPANDED`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dc_breakout_tf_expanded_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`DD_BOUNCE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6895`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'DD_BOUNCE_ENABLED', False)):`
  - indicators: close, k_3m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::dd_bounce_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`EMA_BLANKET_FILTER_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:29925`  |  tradier_manage.py:13971
  - live code: `_eb_cfg = _EbNS(EMA_BLANKET_FILTER_ENABLED=True, EMA_BLANKET_FILTER_MIN_TFS=_psym_get(symbol, position_side, "EMA_BLANKET_FILTER_MIN_TFS", getattr(config, "EMA_BLANKET_FILTER_MIN_T`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::ema_blanket_filter_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`EMA_BLANKET_FILTER_MIN_TFS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:29928`  |  tradier_manage.py:13975
  - live code: `logger.warning(f"🚫 [EMA_BLANKET_FILTER] {position_key}: BLOCKED {action} — ema9>21 agrees on {_eb_agree}/{_eb_found} TFs < {_eb_cfg.EMA_BLANKET_FILTER_MIN_TFS}. reason={(reason or `
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::ema_blanket_filter_min_tfs_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`ENTRY_PRIMARY_TF`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59282`  |  tradier_manage.py:17109
  - live code: `if bool(getattr(config, "ENTRY_PRIMARY_TF", False)):`
  - indicators: dc_position, klines_15m
  - vec twin: create `vec_decisions/<family>.py::entry_primary_tf_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`EXECUTE_NOW_SINGLE_GATE_ENFORCE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59479`  |  tradier_manage.py:33084
  - live code: `if bool(getattr(config, "EXECUTE_NOW_SINGLE_GATE_ENFORCE", False)):`
  - indicators: close
  - vec twin: create `vec_decisions/<family>.py::execute_now_single_gate_enforce_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`FUNDING_GATE_LONG_MAX`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59488`  |  tradier_manage.py:33623
  - live code: `if bool(getattr(config, "FUNDING_GATE_LONG_MAX", False)):`
  - indicators: funding_rate
  - vec twin: create `vec_decisions/<family>.py::funding_gate_long_max_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`FUNDING_GATE_MTF_REQUIRED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59492`  |  tradier_manage.py:33143
  - live code: `if bool(getattr(config, "FUNDING_GATE_MTF_REQUIRED", False)):`
  - indicators: funding_rate
  - vec twin: create `vec_decisions/<family>.py::funding_gate_mtf_required_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`FUNDING_GATE_SHORT_MIN`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59496`  |  tradier_manage.py:33625
  - live code: `if bool(getattr(config, "FUNDING_GATE_SHORT_MIN", False)):`
  - indicators: funding_rate
  - vec twin: create `vec_decisions/<family>.py::funding_gate_short_min_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`GR_FILTER_ALL_ENTRIES`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58630`  |  tradier_manage.py:33158
  - live code: `if getattr(config, "GR_FILTER_ALL_ENTRIES", None) is not None: _ = 1  # GR_FILTER_ALL_ENTRIES — BATCH 5`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::gr_filter_all_entries_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`GR_FILTER_VEC_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58631`  |  tradier_manage.py:33160
  - live code: `if bool(getattr(config, "GR_FILTER_VEC_ENABLED", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::gr_filter_vec_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`GR_FILTER_VEC_MIN_TFS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58639`  |  tradier_manage.py:33164
  - live code: `if bool(getattr(config, "GR_FILTER_VEC_MIN_TFS", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::gr_filter_vec_min_tfs_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`HAIKU_ENTRY_GATE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58647`  |  tradier_manage.py:32845
  - live code: `if getattr(config, "HAIKU_ENTRY_GATE_ENABLED", None) is not None: _ = 1  # HAIKU_ENTRY_GATE_ENABLED — BATCH 5`
  - indicators: adx_15m, klines_15m, klines_1h, max_gain, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::haiku_entry_gate_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`HTF_GATE_APPLY_TO_OPEN`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59564`  |  tradier_manage.py:33230
  - live code: `if bool(getattr(config, "HTF_GATE_APPLY_TO_OPEN", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_gate_apply_to_open_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`HTF_GATE_BYPASS_RZ`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59568`  |  tradier_manage.py:33232
  - live code: `if bool(getattr(config, "HTF_GATE_BYPASS_RZ", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_gate_bypass_rz_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`HTF_GATE_D_MANDATORY`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:37482`  |  tradier_manage.py:33234
  - live code: `getattr(config, "HTF_GATE_D_MANDATORY", True)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::htf_gate_d_mandatory_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`HTF_GATE_MIN_CONFIRMATIONS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59573`  |  tradier_manage.py:33651
  - live code: `if int(getattr(config, "HTF_GATE_MIN_CONFIRMATIONS", 0) or 0) != 0: _ = 1  # HTF_GATE_MIN_CONFIRMATIONS — BATCH2`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_gate_min_confirmations_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`HTF_GATE_SIGNALS_SMA200D`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:37479`  |  tradier_manage.py:33238
  - live code: `getattr(config, "HTF_GATE_SIGNALS_SMA200D", True)`
  - indicators: price
  - vec twin: create `vec_decisions/<family>.py::htf_gate_signals_sma200d_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`HTF_TREND_VETO_BYPASS_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58683`  |  tradier_manage.py:33240
  - live code: `if getattr(config, "HTF_TREND_VETO_BYPASS_ENABLED", None) is not None: _ = 1  # HTF_TREND_VETO_BYPASS_ENABLED — BATCH 5`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_trend_veto_bypass_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`LH_HL_FILTER_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58701`  |  tradier_manage.py:27877
  - live code: `_=getattr(config, "LH_HL_FILTER_ENABLED", False)  # LH_HL_FILTER_ENABLED`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::lh_hl_filter_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`LH_HL_FILTER_TF_REQ`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58707`  |  tradier_manage.py:27537
  - live code: `_=getattr(config, "LH_HL_FILTER_TF_REQ", False)  # LH_HL_FILTER_TF_REQ`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::lh_hl_filter_tf_req_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`MTF_FILTER_STRONG_BUY_QUICK_BYPASS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58853`  |  tradier_manage.py:33304
  - live code: `if getattr(config, "MTF_FILTER_STRONG_BUY_QUICK_BYPASS", None) is not None: _ = 1  # MTF_FILTER_STRONG_BUY_QUICK_BYPASS — BATCH 5`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::mtf_filter_strong_buy_quick_bypass_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`OPEN_RATE_BREAKER_ENABLED`** — vec:`STUB` ez:`REAL` td:`STUB`
  - live: `ez_manage.py:30300`
  - live code: `if _orb_is_open and bool(getattr(config, "OPEN_RATE_BREAKER_ENABLED", True)):`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::open_rate_breaker_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`OPEN_RATE_MAX`** — vec:`STUB` ez:`REAL` td:`STUB`
  - live: `ez_manage.py:30302`
  - live code: `_orb_max = int(getattr(config, "OPEN_RATE_MAX", 15))`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::open_rate_max_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`RZ_BREAKOUT_BAND`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59013`  |  tradier_manage.py:12862
  - live code: `if getattr(config, "RZ_BREAKOUT_BAND", None) is not None: _ = 1  # RZ_BREAKOUT_BAND — BATCH 5`
  - indicators: klines_15m, klines_3m, stoch_k_15m, stoch_k_3m
  - vec twin: create `vec_decisions/<family>.py::rz_breakout_band_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`V8_ENTRY_ENGINE_DC_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59111`  |  tradier_manage.py:33451
  - live code: `if bool(getattr(config, "V8_ENTRY_ENGINE_DC_ENABLED", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::v8_entry_engine_dc_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`V8_ENTRY_ENGINE_WT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59113`  |  tradier_manage.py:33453
  - live code: `if bool(getattr(config, "V8_ENTRY_ENGINE_WT_ENABLED", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::v8_entry_engine_wt_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_15M_CROSS_ENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59128`  |  tradier_manage.py:33580
  - live code: `if bool(getattr(config, "WT_15M_CROSS_ENTRY_ENABLED", False)):`
  - indicators: bb_pct_b_15m, klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_15m_cross_entry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_AGAINST_FILTER_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59145`  |  tradier_manage.py:16420
  - live code: `if bool(getattr(config, "WT_AGAINST_FILTER_ENABLED", False)):`
  - indicators: klines_15m, rsi_1h, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_against_filter_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_COMPOSITE_ENTRY_BLOCK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59149`  |  tradier_manage.py:33493
  - live code: `# WT_COMPOSITE_ENTRY_BLOCK — real live: wt/rsi/bb check`
  - indicators: klines_15m, rsi_1h, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_composite_entry_block_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_COMPOSITE_ENTRY_GOOD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59157`  |  tradier_manage.py:33497
  - live code: `if bool(getattr(config, "WT_COMPOSITE_ENTRY_GOOD", False)):`
  - indicators: klines_15m, rsi_1h, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_composite_entry_good_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_COMPOSITE_ENTRY_OK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59161`  |  tradier_manage.py:33501
  - live code: `if bool(getattr(config, "WT_COMPOSITE_ENTRY_OK", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_composite_entry_ok_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_COMPOSITE_ENTRY_STRONG`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59165`  |  tradier_manage.py:33505
  - live code: `if bool(getattr(config, "WT_COMPOSITE_ENTRY_STRONG", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_composite_entry_strong_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_DC_DIRECT_TF_ENTRY`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::wt_dc_direct_tf_entry_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_DIV_ENTRY_GATE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59182`  |  tradier_manage.py:33521
  - live code: `if bool(getattr(config, "WT_DIV_ENTRY_GATE_ENABLED", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_div_entry_gate_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_EXHAUST_ENTRY_GATE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59194`  |  tradier_manage.py:33525
  - live code: `if bool(getattr(config, "WT_EXHAUST_ENTRY_GATE_ENABLED", False)):`
  - indicators: klines_15m, rsi_1h, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_exhaust_entry_gate_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_ENTRY_GATE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59213`  |  tradier_manage.py:13988
  - live code: `_v = getattr(config, "WT_PERCENTILE_ENTRY_GATE_ENABLED", None)`
  - indicators: klines_15m, rsi_1h, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_entry_gate_enabled_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_ENTRY_OB_D`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59219`  |  tradier_manage.py:33541
  - live code: `if bool(getattr(config, "WT_PERCENTILE_ENTRY_OB_D", False)):`
  - indicators: klines_15m, rsi_1h, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_entry_ob_d_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_ENTRY_OS_D`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59223`  |  tradier_manage.py:33545
  - live code: `if bool(getattr(config, "WT_PERCENTILE_ENTRY_OS_D", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_entry_os_d_mask_vec`, mirror the live predicate 1:1, call once at the ENTRY stage (§3), prove ledger change (§8).

### 10.2  EXIT  (77 switches) — tabs: EXIT_STRUCTURAL / EXIT_VELOCITY

- **`ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6578`  |  tradier_manage.py:5647
  - live code: `_ = getattr(config, 'ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC', 0.0)`
  - indicators: close, wt1_15m, wt1_1h, wt1_3m, wt1_4h, wt1_D, wt2_15m, wt2_1h
  - vec twin: create `vec_decisions/<family>.py::all_tf_against_close_cooldown_sec_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`ALL_TF_AGAINST_CLOSE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6579`  |  tradier_manage.py:5608
  - live code: `if bool(getattr(config, 'ALL_TF_AGAINST_CLOSE_ENABLED', False)):`
  - indicators: close, wt1_15m, wt1_1h, wt1_3m, wt1_4h, wt1_D, wt2_15m, wt2_1h
  - vec twin: create `vec_decisions/<family>.py::all_tf_against_close_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`ALL_TF_AGAINST_CLOSE_MIN_TFS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6586`  |  tradier_manage.py:5614
  - live code: `_min_tfs = int(getattr(config, 'ALL_TF_AGAINST_CLOSE_MIN_TFS', 4))`
  - indicators: wt1_15m, wt1_1h, wt1_4h, wt1_D, wt2_15m, wt2_1h, wt2_4h, wt2_D
  - vec twin: create `vec_decisions/<family>.py::all_tf_against_close_min_tfs_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`ATR_TRAIL_SWEEP_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6613`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'ATR_TRAIL_SWEEP_ENABLED', False)):`
  - indicators: atr_1h, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::atr_trail_sweep_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BB_SQUEEZE_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6451`  |  tradier_manage.py:5384
  - live code: `_ = getattr(config, 'BB_SQUEEZE_EXIT_ENABLED', False)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::bb_squeeze_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BOTTOM_EXIT_HTF_WT_VETO_ENABLED`** — vec:`STUB` ez:`REAL` td:`ABSENT`
  - live: `ez_manage.py:46605`  |  td=ABSENT
  - live code: `if bool(getattr(config, "BOTTOM_EXIT_HTF_WT_VETO_ENABLED", True)):`
  - indicators: dc_high_4h, wt1_15m, wt1_1h, wt1_4h, wt2_15m, wt2_1h, wt2_4h
  - vec twin: create `vec_decisions/<family>.py::bottom_exit_htf_wt_veto_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BREAKEVEN_DC_FIELD_MODE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6708`  |  tradier_manage.py:5647
  - live code: `if is_long and not (_v > _v2): return False, 'BREAKEVEN_DC_FIELD_MODE_TF'`
  - indicators: close, sma_200_1h, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::breakeven_dc_field_mode_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BREAKEVEN_GAIN_EROSION_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6711`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'BREAKEVEN_GAIN_EROSION_ENABLED', False)):`
  - indicators: close, sma_200_1h, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::breakeven_gain_erosion_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BREAKEVEN_GAIN_EROSION_MIN_GAIN`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6723`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BREAKEVEN_GAIN_EROSION_MIN_GAIN', 0.0))`
  - indicators: close, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::breakeven_gain_erosion_min_gain_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6729`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT', 0.0))`
  - indicators: close
  - vec twin: create `vec_decisions/<family>.py::breakeven_gain_erosion_require_profit_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BTC_DIVERGENCE_EXIT_AGAINST`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6766`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BTC_DIVERGENCE_EXIT_AGAINST', 0.0))`
  - indicators: close, sma_200_1h, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::btc_divergence_exit_against_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`BTC_TECH_EXIT_WT_MIN_TFS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6811`  |  tradier_manage.py:5647
  - live code: `if _c <= _thr: return False, 'BTC_TECH_EXIT_WT_MIN_TFS_THR'`
  - indicators: close, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::btc_tech_exit_wt_min_tfs_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`DAYTRADE_DC_STOP_TF`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::daytrade_dc_stop_tf_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`DC_BREAK_WAIT_WT15_CLOSE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:40402`  |  tradier_manage.py:18202
  - live code: `_dc_wait_wt15 = _cfg_auto('DC_BREAK_WAIT_WT15_CLOSE_ENABLED', False)`
  - indicators: dc_high_15m, dc_low_15m, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::dc_break_wait_wt15_close_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`DC_DAYTRADE_STOP_USE_DC4_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32598
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dc_daytrade_stop_use_dc4_15m_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`DC_DAYTRADE_STOP_USE_DC_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32597
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dc_daytrade_stop_use_dc_15m_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`DC_HOPELESS_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6871`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'DC_HOPELESS_EXIT_ENABLED', False)):`
  - indicators: dc_position_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::dc_hopeless_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`DC_HOPELESS_EXIT_MIN_AGE_S`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6880`  |  tradier_manage.py:5647
  - live code: `if _c <= _thr: return False, 'DC_HOPELESS_EXIT_MIN_AGE_S_THR'`
  - indicators: close, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::dc_hopeless_exit_min_age_s_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`DYN_STRUCT_TRAIL_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6545`  |  tradier_manage.py:10766
  - live code: `_ = getattr(config, 'DYN_STRUCT_TRAIL_ENABLED', False)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dyn_struct_trail_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:30118`  |  tradier_manage.py:25784
  - live code: `if (bool(getattr(config, "EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED", False))`
  - indicators: wt_peak_structure_15m, wt_trough_structure_15m
  - vec twin: create `vec_decisions/<family>.py::exit_blocker_require_lh_ll_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`EXIT_VELOCITY_WT_TFS`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::exit_velocity_wt_tfs_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`E_1_EXIT_DELTA_THR`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59484`  |  tradier_manage.py:33111
  - live code: `if float(getattr(config, "E_1_EXIT_DELTA_THR", 0) or 0) != 0: _ = 1  # E_1_EXIT_DELTA_THR — BATCH2`
  - indicators: close, funding_rate
  - vec twin: create `vec_decisions/<family>.py::e_1_exit_delta_thr_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`E_1_WT_EXIT_USE_DELTA_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58596`  |  tradier_manage.py:33113
  - live code: `if getattr(config, "E_1_WT_EXIT_USE_DELTA_ENABLED", None) is not None: _ = 1  # E_1_WT_EXIT_USE_DELTA_ENABLED — BATCH 5`
  - indicators: close, klines_15m, rsi_15m
  - vec twin: create `vec_decisions/<family>.py::e_1_wt_exit_use_delta_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`E_3_USE_WT_STRUCTURE_EXIT_MODE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:50023`  |  tradier_manage.py:33115
  - live code: `_e3_mode = int(getattr(config, "E_3_USE_WT_STRUCTURE_EXIT_MODE", 0))`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::e_3_use_wt_structure_exit_mode_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`GAP_RISK_EXIT_ENABLED`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:19499
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::gap_risk_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`HARD_BREAKEVEN_FLOOR_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58652`  |  tradier_manage.py:33190
  - live code: `if bool(getattr(config, "HARD_BREAKEVEN_FLOOR_ENABLED", False)):`
  - indicators: adx_15m, adx_1h, klines_15m, klines_1h, max_gain
  - vec twin: create `vec_decisions/<family>.py::hard_breakeven_floor_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`HARD_BREAKEVEN_MIN_PEAK_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59508`  |  tradier_manage.py:33634
  - live code: `if bool(getattr(config, "HARD_BREAKEVEN_MIN_PEAK_PCT", False)):`
  - indicators: max_gain
  - vec twin: create `vec_decisions/<family>.py::hard_breakeven_min_peak_pct_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:46791`  |  tradier_manage.py:33218
  - live code: `if _hac_1h_against and _hac_confirm_ok and bool(getattr(config, "HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H", False)):`
  - indicators: wt1_15m, wt1_3m, wt1_4h, wt2_15m, wt2_3m, wt2_4h
  - vec twin: create `vec_decisions/<family>.py::htf_against_force_close_confirm_4h_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`HTF_AGAINST_FORCE_CLOSE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58672`  |  tradier_manage.py:33220
  - live code: `if getattr(config, "HTF_AGAINST_FORCE_CLOSE_ENABLED", None) is not None: _ = 1  # HTF_AGAINST_FORCE_CLOSE_ENABLED — BATCH 5`
  - indicators: dc_position, klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_against_force_close_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`HTF_EXIT_VETO_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58675`  |  tradier_manage.py:33222
  - live code: `if bool(getattr(config, "HTF_EXIT_VETO_ENABLED", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_exit_veto_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`HTF_EXIT_VETO_MAX_LOSS_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59555`  |  tradier_manage.py:33645
  - live code: `if bool(getattr(config, "HTF_EXIT_VETO_MAX_LOSS_PCT", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_exit_veto_max_loss_pct_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`HTF_EXIT_VETO_MIN_ALIGNED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58679`  |  tradier_manage.py:33646
  - live code: `if bool(getattr(config, "HTF_EXIT_VETO_MIN_ALIGNED", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_exit_veto_min_aligned_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`INTRADAY_SESSION_FORCE_EXIT_UTC`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58684`  |  tradier_manage.py:33244
  - live code: `# INTRADAY_SESSION_FORCE_EXIT_UTC — real live: if current UTC hour == threshold, force exit (was placeholder _ =1)`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::intraday_session_force_exit_utc_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58713`  |  tradier_manage.py:33264
  - live code: `if bool(getattr(config, "LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED", False)):`
  - indicators: mfi_1h, wt1_1h
  - vec twin: create `vec_decisions/<family>.py::loss_exit_stale_price_allow_near_be_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58715`  |  tradier_manage.py:33266
  - live code: `if getattr(config, "LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED", None) is not None: _ = 1  # LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED — BATCH 5`
  - indicators: mfi_1h, wt1_1h
  - vec twin: create `vec_decisions/<family>.py::loss_exit_stop_functions_kill_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58716`  |  tradier_manage.py:33268
  - live code: `# LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK — real live: loss technical exit with mfi_1h + wt, no stale block`
  - indicators: mfi_1h, wt1_1h
  - vec twin: create `vec_decisions/<family>.py::loss_technical_exit_no_stale_block_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`MACD_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58730`  |  tradier_manage.py:33270
  - live code: `if bool(getattr(config, "MACD_EXIT_ENABLED", False)):`
  - indicators: klines_15m, macd_hist
  - vec twin: create `vec_decisions/<family>.py::macd_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`MACD_EXIT_MIN_GAIN`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58734`  |  tradier_manage.py:33272
  - live code: `if bool(getattr(config, "MACD_EXIT_MIN_GAIN", False)):`
  - indicators: klines_15m, macd_hist
  - vec twin: create `vec_decisions/<family>.py::macd_exit_min_gain_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`MACD_EXIT_TF`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58738`  |  tradier_manage.py:33274
  - live code: `if bool(getattr(config, "MACD_EXIT_TF", False)):`
  - indicators: klines_15m, macd_hist
  - vec twin: create `vec_decisions/<family>.py::macd_exit_tf_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`MU_CORRECTION_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58878`  |  tradier_manage.py:18727
  - live code: `if bool(getattr(config, "MU_CORRECTION_EXIT_ENABLED", False)):`
  - indicators: rsi_1h
  - vec twin: create `vec_decisions/<family>.py::mu_correction_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`PEAK_GIVEBACK_DROP_TRIGGER_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58958`  |  tradier_manage.py:33360
  - live code: `if bool(getattr(config, "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::peak_giveback_drop_trigger_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`REVERSE_ON_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59010`  |  tradier_manage.py:33414
  - live code: `_=getattr(config, "REVERSE_ON_EXIT_ENABLED", False)  # REVERSE_ON_EXIT_ENABLED`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reverse_on_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`RULE_B_3M_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59012`  |  tradier_manage.py:33416
  - live code: `_=getattr(config, "RULE_B_3M_EXIT_ENABLED", False)  # RULE_B_3M_EXIT_ENABLED`
  - indicators: klines_3m, stoch_k_3m
  - vec twin: create `vec_decisions/<family>.py::rule_b_3m_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_AUG_BE_STOP_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59258`  |  tradier_manage.py:33418
  - live code: `if bool(getattr(config, "SCALP_V3_AUG_BE_STOP_ENABLED", False)):`
  - indicators: bb_pct_b_15m, dc_position, klines_15m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_aug_be_stop_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_AUG_BE_STOP_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59261`  |  tradier_manage.py:33420
  - live code: `if bool(getattr(config, "SCALP_V3_AUG_BE_STOP_PCT", False)):`
  - indicators: bb_pct_b_15m, klines_15m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_aug_be_stop_pct_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_K_OB_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59017`  |  tradier_manage.py:33422
  - live code: `if bool(getattr(config, "SCALP_V3_K_OB_EXIT_ENABLED", False)):`
  - indicators: klines_15m, klines_3m, ob_wall_dist_pct, stoch_k_15m, stoch_k_3m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_k_ob_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_K_OB_EXIT_K15M_HI`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59027`  |  tradier_manage.py:33424
  - live code: `# SCALP_V3_K_OB_EXIT_K15M_HI — causal: stoch_k_15m >= thr + wall proximity`
  - indicators: klines_15m, ob_wall_dist_pct, stoch_k_15m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_k_ob_exit_k15m_hi_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_K_OB_EXIT_K15M_LO`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59037`  |  tradier_manage.py:33426
  - live code: `if bool(getattr(config, "SCALP_V3_K_OB_EXIT_K15M_LO", False)):`
  - indicators: klines_15m, ob_wall_dist_pct, stoch_k_15m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_k_ob_exit_k15m_lo_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_K_OB_EXIT_K3M_HI`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59046`  |  tradier_manage.py:33428
  - live code: `if bool(getattr(config, "SCALP_V3_K_OB_EXIT_K3M_HI", False)):`
  - indicators: klines_15m, klines_3m, ob_wall_dist_pct, stoch_k_15m, stoch_k_3m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_k_ob_exit_k3m_hi_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_K_OB_EXIT_K3M_LO`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59054`  |  tradier_manage.py:33430
  - live code: `if bool(getattr(config, "SCALP_V3_K_OB_EXIT_K3M_LO", False)):`
  - indicators: klines_15m, klines_3m, stoch_k_15m, stoch_k_3m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_k_ob_exit_k3m_lo_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_K_OB_EXIT_WALL_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59021`  |  tradier_manage.py:33432
  - live code: `_wall = float(getattr(config, "SCALP_V3_K_OB_EXIT_WALL_PCT", 0.5) or 0.5)`
  - indicators: klines_15m, klines_3m, ob_wall_dist_pct, stoch_k_15m, stoch_k_3m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_k_ob_exit_wall_pct_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_OB_WALL_TOO_CLOSE_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59070`  |  tradier_manage.py:33434
  - live code: `if bool(getattr(config, "SCALP_V3_OB_WALL_TOO_CLOSE_PCT", False)):`
  - indicators: klines_15m, klines_3m, rsi_15m, stoch_k_3m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_ob_wall_too_close_pct_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SCALP_V3_PROTECTIVE_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59074`  |  tradier_manage.py:33436
  - live code: `if bool(getattr(config, "SCALP_V3_PROTECTIVE_EXIT_ENABLED", False)):`
  - indicators: klines_15m, rsi_15m
  - vec twin: create `vec_decisions/<family>.py::scalp_v3_protective_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`SIMPLE_TP_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59078`  |  tradier_manage.py:33438
  - live code: `if bool(getattr(config, "SIMPLE_TP_EXIT_ENABLED", False)):`
  - indicators: bb_pct_b_15m, klines_15m, rsi_15m
  - vec twin: create `vec_decisions/<family>.py::simple_tp_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`STDEV_REJECT_EXIT_TF`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59092`  |  tradier_manage.py:19983
  - live code: `if bool(getattr(config, "STDEV_REJECT_EXIT_TF", False)):`
  - indicators: bb_pct_b_15m, klines_15m
  - vec twin: create `vec_decisions/<family>.py::stdev_reject_exit_tf_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`STDEV_SUPPRESS_EARLY_EXIT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59096`  |  tradier_manage.py:33444
  - live code: `if bool(getattr(config, "STDEV_SUPPRESS_EARLY_EXIT", False)):`
  - indicators: bb_pct_b_15m, klines_15m
  - vec twin: create `vec_decisions/<family>.py::stdev_suppress_early_exit_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`TRADIER_DC_DAYTRADE_STOP_USE_DC4_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32598
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::tradier_dc_daytrade_stop_use_dc4_15m_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`TRADIER_DC_DAYTRADE_STOP_USE_DC_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32597
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::tradier_dc_daytrade_stop_use_dc_15m_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`TREND_EXIT_SCORE_FLIP`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59107`  |  tradier_manage.py:33446
  - live code: `_=getattr(config, "TREND_EXIT_SCORE_FLIP", False)  # TREND_EXIT_SCORE_FLIP`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::trend_exit_score_flip_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`TREND_MIN_GAIN_EXIT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59108`  |  tradier_manage.py:33447
  - live code: `if bool(getattr(config, "TREND_MIN_GAIN_EXIT", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::trend_min_gain_exit_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_15M_LH_WAIT_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:40501`  |  tradier_manage.py:18315
  - live code: `if _cfg_auto('WT_15M_LH_WAIT_EXIT_ENABLED', False):`
  - indicators: bb_pct_b_15m, dc_position_15m, wt1_15m, wt2_15m, wt_peak_structure_15m, wt_trough_structure_15m
  - vec twin: create `vec_decisions/<family>.py::wt_15m_lh_wait_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_4H_VEL_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:49767`  |  tradier_manage.py:33469
  - live code: `getattr(config, "WT_4H_VEL_EXIT_ENABLED", True)`
  - indicators: wt1_4h, wt_velocity_4h
  - vec twin: create `vec_decisions/<family>.py::wt_4h_vel_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_4H_VEL_EXIT_K_EXTREME_HIGH`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:49807`  |  tradier_manage.py:33473
  - live code: `_wtve_kx_hi = float(getattr(config, "WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0))`
  - indicators: k_15m, k_3m
  - vec twin: create `vec_decisions/<family>.py::wt_4h_vel_exit_k_extreme_high_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_4H_VEL_EXIT_K_EXTREME_LOW`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:49808`  |  tradier_manage.py:33477
  - live code: `_wtve_kx_lo = float(getattr(config, "WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0))`
  - indicators: k_15m, k_3m
  - vec twin: create `vec_decisions/<family>.py::wt_4h_vel_exit_k_extreme_low_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_4H_VEL_EXIT_REQUIRE_K_EXTREME`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:49805`  |  tradier_manage.py:33481
  - live code: `getattr(config, "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True)`
  - indicators: k_3m
  - vec twin: create `vec_decisions/<family>.py::wt_4h_vel_exit_require_k_extreme_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_4H_VEL_EXIT_REQUIRE_PROFIT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59136`  |  tradier_manage.py:33485
  - live code: `if getattr(config, "WT_4H_VEL_EXIT_REQUIRE_PROFIT", None) is not None: _ = 1  # WT_4H_VEL_EXIT_REQUIRE_PROFIT — BATCH 5`
  - indicators: rsi_1h
  - vec twin: create `vec_decisions/<family>.py::wt_4h_vel_exit_require_profit_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_CROSS_EXIT_APPLIES_TO_WINNERS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59169`  |  tradier_manage.py:33509
  - live code: `if bool(getattr(config, "WT_CROSS_EXIT_APPLIES_TO_WINNERS", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_cross_exit_applies_to_winners_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_CROSS_EXIT_MIN_AGE_MINUTES`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59177`  |  tradier_manage.py:33513
  - live code: `if bool(getattr(config, "WT_CROSS_EXIT_MIN_AGE_MINUTES", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_cross_exit_min_age_minutes_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_CROSS_EXIT_REQUIRE_15M_CONFIRM`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:46786`  |  tradier_manage.py:33517
  - live code: `# not 1h noise. Driven by the (previously dead) WT_CROSS_EXIT_REQUIRE_15M_CONFIRM knob.`
  - indicators: wt1_15m, wt1_4h, wt2_15m, wt2_4h
  - vec twin: create `vec_decisions/<family>.py::wt_cross_exit_require_15m_confirm_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:40528`  |  tradier_manage.py:18344
  - live code: `if _cfg_auto('WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED', False):`
  - indicators: bb_pct_b_15m, dc_position_15m, wt1_15m, wt2_15m, wt_divergence_15m
  - vec twin: create `vec_decisions/<family>.py::wt_divergence_vv_short_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_EXHAUST_EXIT_MIN_GAIN_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59198`  |  tradier_manage.py:33529
  - live code: `if getattr(config, "WT_EXHAUST_EXIT_MIN_GAIN_PCT", None) is not None: _ = 1  # WT_EXHAUST_EXIT_MIN_GAIN_PCT — BATCH 5`
  - indicators: klines_15m, klines_1h, wt1_15m, wt1_1h
  - vec twin: create `vec_decisions/<family>.py::wt_exhaust_exit_min_gain_pct_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_EXHAUST_EXIT_REQUIRE_GAIN`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59199`  |  tradier_manage.py:33533
  - live code: `if getattr(config, "WT_EXHAUST_EXIT_REQUIRE_GAIN", None) is not None: _ = 1  # WT_EXHAUST_EXIT_REQUIRE_GAIN — BATCH 5`
  - indicators: klines_15m, klines_1h, klines_4h, wt1_15m, wt1_1h, wt1_4h
  - vec twin: create `vec_decisions/<family>.py::wt_exhaust_exit_require_gain_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:49949`  |  tradier_manage.py:33549
  - live code: `getattr(config, "WT_PERCENTILE_EXIT_ENABLED", True)`
  - indicators: wt_percentile_4h, wt_percentile_D
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_EXIT_OB_4H`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59228`  |  tradier_manage.py:33553
  - live code: `if getattr(config, "WT_PERCENTILE_EXIT_OB_4H", None) is not None: _ = 1  # WT_PERCENTILE_EXIT_OB_4H — BATCH 5`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_exit_ob_4h_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_EXIT_OB_D`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:49962`  |  tradier_manage.py:33557
  - live code: `and _pct_D > getattr(config, "WT_PERCENTILE_EXIT_OB_D", 90)`
  - indicators: wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_exit_ob_d_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_EXIT_OS_4H`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59230`  |  tradier_manage.py:33561
  - live code: `if getattr(config, "WT_PERCENTILE_EXIT_OS_4H", None) is not None: _ = 1  # WT_PERCENTILE_EXIT_OS_4H — BATCH 5`
  - indicators: wt1_1h
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_exit_os_4h_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).
- **`WT_PERCENTILE_EXIT_OS_D`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59231`  |  tradier_manage.py:33565
  - live code: `if getattr(config, "WT_PERCENTILE_EXIT_OS_D", None) is not None: _ = 1  # WT_PERCENTILE_EXIT_OS_D — BATCH 5`
  - indicators: wt1_1h
  - vec twin: create `vec_decisions/<family>.py::wt_percentile_exit_os_d_mask_vec`, mirror the live predicate 1:1, call once at the EXIT stage (§3), prove ledger change (§8).

### 10.3  AUGMENT  (16 switches) — tabs: AUGMENT_TREND / AUGMENT_RISK_SIZING

- **`AUGMENTED_POSITIONS_GUARD_FLOOR_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6618`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'AUGMENTED_POSITIONS_GUARD_FLOOR_MULT', 0.0))`
  - indicators: atr_1h, close, sma_200_1h
  - vec twin: create `vec_decisions/<family>.py::augmented_positions_guard_floor_mult_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_AT_LOSS_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6624`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'AUGMENT_AT_LOSS_ENABLED', False)):`
  - indicators: close, sma_200_1h
  - vec twin: create `vec_decisions/<family>.py::augment_at_loss_enabled_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_BOUNCE_MIN_GAIN_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58557`  |  tradier_manage.py:33029
  - live code: `if bool(getattr(config, "AUGMENT_BOUNCE_MIN_GAIN_PCT", False)):`
  - indicators: gain, position
  - vec twin: create `vec_decisions/<family>.py::augment_bounce_min_gain_pct_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_BREAKOUT_MIN_GAIN_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58561`  |  tradier_manage.py:21269
  - live code: `if bool(getattr(config, "AUGMENT_BREAKOUT_MIN_GAIN_PCT", False)):`
  - indicators: gain, position
  - vec twin: create `vec_decisions/<family>.py::augment_breakout_min_gain_pct_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_FALLBACK_GAIN_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58565`  |  tradier_manage.py:33059
  - live code: `# AUGMENT_FALLBACK_GAIN_PCT — real live: fallback augment gain pct`
  - indicators: gain, position
  - vec twin: create `vec_decisions/<family>.py::augment_fallback_gain_pct_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_FALLBACK_REDUCE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58573`  |  tradier_manage.py:33063
  - live code: `if bool(getattr(config, "AUGMENT_FALLBACK_REDUCE_ENABLED", False)):`
  - indicators: gain, position
  - vec twin: create `vec_decisions/<family>.py::augment_fallback_reduce_enabled_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_FALLBACK_REDUCE_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58577`  |  tradier_manage.py:33067
  - live code: `if bool(getattr(config, "AUGMENT_FALLBACK_REDUCE_PCT", False)):`
  - indicators: close, gain, position
  - vec twin: create `vec_decisions/<family>.py::augment_fallback_reduce_pct_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_MIN_GAIN_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58581`  |  tradier_manage.py:33031
  - live code: `if getattr(config, "AUGMENT_MIN_GAIN_PCT", None) is not None: _ = 1  # AUGMENT_MIN_GAIN_PCT — BATCH 5`
  - indicators: close, gain, position
  - vec twin: create `vec_decisions/<family>.py::augment_min_gain_pct_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`AUGMENT_WT_4H_BOUNCE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6635`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'AUGMENT_WT_4H_BOUNCE_ENABLED', False)):`
  - indicators: close, sma_200_1h
  - vec twin: create `vec_decisions/<family>.py::augment_wt_4h_bounce_enabled_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`COUNTER_TREND_ADD_BLOCK_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59354`  |  tradier_manage.py:33001
  - live code: `_ = "COUNTER_TREND_ADD_BLOCK_ENABLED"`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::counter_trend_add_block_enabled_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`DELTA_PYRAMID_MAX`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6914`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'DELTA_PYRAMID_MAX', 0.0))`
  - indicators: close, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::delta_pyramid_max_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`DELTA_PYRAMID_PRICE_TOL`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6920`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'DELTA_PYRAMID_PRICE_TOL', 0.0))`
  - indicators: close, wt_velocity_1h
  - vec twin: create `vec_decisions/<family>.py::delta_pyramid_price_tol_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`DYNAMIC_SCORE_AUGMENT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6543`  |  tradier_manage.py:5568
  - live code: `_ = getattr(config, 'DYNAMIC_SCORE_AUGMENT_ENABLED', False)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dynamic_score_augment_enabled_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`HTF_GATE_APPLY_TO_AUGMENT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59560`  |  tradier_manage.py:33228
  - live code: `if bool(getattr(config, "HTF_GATE_APPLY_TO_AUGMENT", False)):`
  - indicators: klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::htf_gate_apply_to_augment_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`MAX_AUGMENTS_PER_POSITION`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:24152`  |  tradier_manage.py:33290
  - live code: `_max_augs = getattr(config, "MAX_AUGMENTS_PER_POSITION", 3)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::max_augments_per_position_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).
- **`UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59110`  |  tradier_manage.py:33449
  - live code: `if getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", None) is not None: _ = 1  # UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED — BATCH 5`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::universal_augment_gain_gate_enabled_mask_vec`, mirror the live predicate 1:1, call once at the AUGMENT stage (§3), prove ledger change (§8).

### 10.4  REDUCE  (5 switches) — tabs: REDUCE_PROFIT_LOCK / REDUCE_SIGNAL_RATER

- **`INTRADAY_RATIO_MAX_TRIMS_PER_DAY`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:23675
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::intraday_ratio_max_trims_per_day_mask_vec`, mirror the live predicate 1:1, call once at the REDUCE stage (§3), prove ledger change (§8).
- **`INTRADAY_RATIO_TRIM_FRAC`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:23705
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::intraday_ratio_trim_frac_mask_vec`, mirror the live predicate 1:1, call once at the REDUCE stage (§3), prove ledger change (§8).
- **`PARTIAL_EXIT_FRAC`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58938`  |  tradier_manage.py:33354
  - live code: `# PARTIAL_EXIT_FRAC — real live: partial exit frac`
  - indicators: position
  - vec twin: create `vec_decisions/<family>.py::partial_exit_frac_mask_vec`, mirror the live predicate 1:1, call once at the REDUCE stage (§3), prove ledger change (§8).
- **`QUICK_REDUCE_TECHNICAL_ONLY`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58964`  |  tradier_manage.py:33362
  - live code: `if bool(getattr(config, "QUICK_REDUCE_TECHNICAL_ONLY", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::quick_reduce_technical_only_mask_vec`, mirror the live predicate 1:1, call once at the REDUCE stage (§3), prove ledger change (§8).
- **`WT_REDUCE_FRAC_HIGH`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59234`  |  tradier_manage.py:33569
  - live code: `_wt_reduce = float(getattr(config, "WT_REDUCE_FRAC_HIGH", 0) or 0)`
  - indicators: klines_15m, wt1_15m, wt1_1h
  - vec twin: create `vec_decisions/<family>.py::wt_reduce_frac_high_mask_vec`, mirror the live predicate 1:1, call once at the REDUCE stage (§3), prove ledger change (§8).

### 10.5  REENTRY  (72 switches) — tabs: REENTRY_WINDOWED / REENTRY_ADAPTIVE

- **`BOUNCE_REENTRY_K_RESET_LONG`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6692`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BOUNCE_REENTRY_K_RESET_LONG', 0.0))`
  - indicators: close, k_3m
  - vec twin: create `vec_decisions/<family>.py::bounce_reentry_k_reset_long_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`BOUNCE_REENTRY_K_RESET_SHORT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6702`  |  tradier_manage.py:21815
  - live code: `if _c <= _thr: return False, 'BOUNCE_REENTRY_K_RESET_SHORT_THR'`
  - indicators: close, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::bounce_reentry_k_reset_short_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`BREAKOUT_LEASH_REENTRY_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6739`  |  tradier_manage.py:5647
  - live code: `if _c <= _thr: return False, 'BREAKOUT_LEASH_REENTRY_MULT_THR'`
  - indicators: close, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::breakout_leash_reentry_mult_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`BTC_GUARANTEED_REENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6772`  |  tradier_manage.py:5647
  - live code: `if bool(getattr(config, 'BTC_GUARANTEED_REENTRY_ENABLED', False)):`
  - indicators: close, sma_200_1h
  - vec twin: create `vec_decisions/<family>.py::btc_guaranteed_reentry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`BTC_GUARANTEED_REENTRY_MAX_AGE_BARS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6777`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BTC_GUARANTEED_REENTRY_MAX_AGE_BARS', 0.0))`
  - indicators: close, sma_200_1h
  - vec twin: create `vec_decisions/<family>.py::btc_guaranteed_reentry_max_age_bars_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`BTC_GUARANTEED_REENTRY_MIN_GAP_BARS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6783`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BTC_GUARANTEED_REENTRY_MIN_GAP_BARS', 0.0))`
  - indicators: close
  - vec twin: create `vec_decisions/<family>.py::btc_guaranteed_reentry_min_gap_bars_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`CHANNEL_REENTRY_STOP_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6827`  |  tradier_manage.py:5655
  - live code: `if bool(getattr(config, 'CHANNEL_REENTRY_STOP_ENABLED', False)):`
  - indicators: dc_position_15m, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::channel_reentry_stop_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:55732`  |  tradier_manage.py:5647
  - live code: `if (not _is_long_tier) and getattr(cfg, "DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED", True):`
  - indicators: current_price, k_3m, wt1_3m, wt2_3m
  - vec twin: create `vec_decisions/<family>.py::daemon_reentry_short_wt_xunder_gate_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`DAEMON_REENTRY_STALE_EXIT_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6860`  |  tradier_manage.py:5647
  - live code: `if (not is_long) and not (_w1 < _w2): return False, 'DAEMON_REENTRY_STALE_EXIT_ENABLED_WT'`
  - indicators: wt1_15m, wt1_3m, wt2_15m, wt2_3m
  - vec twin: create `vec_decisions/<family>.py::daemon_reentry_stale_exit_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`DIRECTION_FAVORABLE_REENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42211`  |  tradier_manage.py:32839
  - live code: `and getattr(config, "DIRECTION_FAVORABLE_REENTRY_ENABLED", False)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::direction_favorable_reentry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`FOLLOW_THROUGH_REENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58609`  |  tradier_manage.py:33132
  - live code: `if bool(getattr(config, "FOLLOW_THROUGH_REENTRY_ENABLED", False)):`
  - indicators: close
  - vec twin: create `vec_decisions/<family>.py::follow_through_reentry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`HLR_REENTRY_MAX_AGE_S`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59520`  |  tradier_manage.py:33636
  - live code: `if bool(getattr(config, "HLR_REENTRY_MAX_AGE_S", False)):`
  - indicators: dc_position, klines_1h
  - vec twin: create `vec_decisions/<family>.py::hlr_reentry_max_age_s_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`HTF_WT_CHURN_REENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`ABSENT`
  - live: `ez_manage.py:42423`  |  td=ABSENT
  - live code: `if _guaranteed_reentry and _is_flat_for_guarantee and reentry_level > 0 and bool(getattr(config, "HTF_WT_CHURN_REENTRY_ENABLED", True)):`
  - indicators: wt1_15m, wt1_1h, wt2_15m, wt2_1h
  - vec twin: create `vec_decisions/<family>.py::htf_wt_churn_reentry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`HTF_WT_CHURN_REENTRY_MAX_AGE_MIN`** — vec:`STUB` ez:`REAL` td:`ABSENT`
  - live: `ez_manage.py:42425`  |  td=ABSENT
  - live code: `_churn_max_age = float(getattr(config, "HTF_WT_CHURN_REENTRY_MAX_AGE_MIN", 120.0))`
  - indicators: wt1_15m, wt1_1h, wt1_4h, wt2_15m, wt2_1h, wt2_4h
  - vec twin: create `vec_decisions/<family>.py::htf_wt_churn_reentry_max_age_min_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`LEGACY_PROC_SINGLE_REENTRY`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58694`  |  tradier_manage.py:33250
  - live code: `if getattr(config, "LEGACY_PROC_SINGLE_REENTRY", None) is not None: _ = 1  # LEGACY_PROC_SINGLE_REENTRY — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::legacy_proc_single_reentry_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`LEGACY_REENTRY_PSR_DC_BOUNCE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58695`  |  tradier_manage.py:33252
  - live code: `if getattr(config, "LEGACY_REENTRY_PSR_DC_BOUNCE", None) is not None: _ = 1  # LEGACY_REENTRY_PSR_DC_BOUNCE — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::legacy_reentry_psr_dc_bounce_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`LEGACY_REENTRY_PSR_FULL_DC`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58696`  |  tradier_manage.py:33254
  - live code: `if getattr(config, "LEGACY_REENTRY_PSR_FULL_DC", None) is not None: _ = 1  # LEGACY_REENTRY_PSR_FULL_DC — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::legacy_reentry_psr_full_dc_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`LEGACY_REENTRY_PSR_K_DC_CROSSOVER`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58697`  |  tradier_manage.py:33256
  - live code: `if getattr(config, "LEGACY_REENTRY_PSR_K_DC_CROSSOVER", None) is not None: _ = 1  # LEGACY_REENTRY_PSR_K_DC_CROSSOVER — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::legacy_reentry_psr_k_dc_crossover_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`LEGACY_REENTRY_PSR_QUICK_RECOVERY`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58699`  |  tradier_manage.py:33258
  - live code: `_=getattr(config, "LEGACY_REENTRY_PSR_QUICK_RECOVERY", False)  # LEGACY_REENTRY_PSR_QUICK_RECOVERY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::legacy_reentry_psr_quick_recovery_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58754`  |  tradier_manage.py:33282
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS", False)):`
  - indicators: klines_15m, macd_hist
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_allow_wt0_strong_cross_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_K_HIGH_BLOCK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58756`  |  tradier_manage.py:33284
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_K_HIGH_BLOCK", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_k_high_block_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_K_LOW_BLOCK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58758`  |  tradier_manage.py:33286
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_K_LOW_BLOCK", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_k_low_block_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58760`  |  tradier_manage.py:33288
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_require_k_not_extreme_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_WT_FILTER_MIN_TFS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58762`  |  tradier_manage.py:12065
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_WT_FILTER_MIN_TFS", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_wt_filter_min_tfs_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58766`  |  tradier_manage.py:12068
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_wt_filter_min_velocity_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58770`  |  tradier_manage.py:12066
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_wt_filter_require_flip_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_WT_FILTER_TF_MODE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58774`  |  tradier_manage.py:12064
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_WT_FILTER_TF_MODE", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_wt_filter_tf_mode_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58778`  |  tradier_manage.py:12067
  - live code: `if bool(getattr(config, "MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::mandatory_reentry_wt_filter_velocity_ratio_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`MU_CORRECTION_REENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58881`  |  tradier_manage.py:21521
  - live code: `_=getattr(config, "MU_CORRECTION_REENTRY_ENABLED", False)  # MU_CORRECTION_REENTRY_ENABLED`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::mu_correction_reentry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58896`  |  tradier_manage.py:33324
  - live code: `if bool(getattr(config, "OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT", False)):`
  - indicators: klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_default_size_mult_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58899`  |  tradier_manage.py:33326
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_ENABLED", False)  # OBLIGATORY_REENTRY_ENABLED`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_K15_HIGH_BLOCK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58901`  |  tradier_manage.py:33328
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_K15_HIGH_BLOCK", False)  # OBLIGATORY_REENTRY_K15_HIGH_BLOCK`
  - indicators: wt1_15m
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_k15_high_block_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58902`  |  tradier_manage.py:33330
  - live code: `if bool(getattr(config, "OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC", False)):`
  - indicators: wt1_15m
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_k15_high_size_frac_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_SCORE_TIER1`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58904`  |  tradier_manage.py:33332
  - live code: `# OBLIGATORY_REENTRY_SCORE_TIER1 — real live: obligatory reentry score tier1`
  - indicators: wt1_15m
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_score_tier1_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_SCORE_TIER2`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58912`  |  tradier_manage.py:33334
  - live code: `if bool(getattr(config, "OBLIGATORY_REENTRY_SCORE_TIER2", False)):`
  - indicators: wt1_15m
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_score_tier2_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_SCORE_TIER3`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58915`  |  tradier_manage.py:33336
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_SCORE_TIER3", False)  # OBLIGATORY_REENTRY_SCORE_TIER3 OBLIGATORY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_score_tier3_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58917`  |  tradier_manage.py:33338
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK", False)  # OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK OBLIGATORY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_short_k15_low_block_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58919`  |  tradier_manage.py:33340
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC", False)  # OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC OBLIGATORY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_short_k15_low_size_frac_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_SMA_FIELD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58921`  |  tradier_manage.py:33342
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_SMA_FIELD", False)  # OBLIGATORY_REENTRY_SMA_FIELD OBLIGATORY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_sma_field_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_SMA_TF`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58923`  |  tradier_manage.py:33344
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_SMA_TF", False)  # OBLIGATORY_REENTRY_SMA_TF OBLIGATORY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_sma_tf_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58925`  |  tradier_manage.py:33346
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED", False)  # OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED OBLIGATORY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_tier1_htf_required_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58927`  |  tradier_manage.py:33348
  - live code: `_=getattr(config, "OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED", False)  # OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED OBLIGATORY`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::obligatory_reentry_tier2_htf_required_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`QUICK_REENTRY_60MIN_MIN_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58967`  |  tradier_manage.py:33364
  - live code: `_=getattr(config, "QUICK_REENTRY_60MIN_MIN_PCT", False)  # QUICK_REENTRY_60MIN_MIN_PCT`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::quick_reentry_60min_min_pct_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY2_DC_BREAK_ALLOW_15M`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42260`  |  tradier_manage.py:33365
  - live code: `_dc_reentry_allow_15m = bool(getattr(config, 'REENTRY2_DC_BREAK_ALLOW_15M', True))`
  - indicators: dc_high4_3m, dc_high_3m, dc_low4_3m, dc_low_3m
  - vec twin: create `vec_decisions/<family>.py::reentry2_dc_break_allow_15m_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY2_DC_BREAK_FILTER_TF`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42263`  |  tradier_manage.py:33367
  - live code: `_dc_ftf = str(getattr(config, 'REENTRY2_DC_BREAK_FILTER_TF', '3m'))`
  - indicators: dc_low4_3m
  - vec twin: create `vec_decisions/<family>.py::reentry2_dc_break_filter_tf_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY2_DC_BREAK_REQUIRE_K_FILTER`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42261`  |  tradier_manage.py:33369
  - live code: `_dc_req_k = bool(getattr(config, 'REENTRY2_DC_BREAK_REQUIRE_K_FILTER', True))`
  - indicators: dc_high4_3m, dc_low4_3m, dc_low_3m
  - vec twin: create `vec_decisions/<family>.py::reentry2_dc_break_require_k_filter_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY2_DC_BREAK_REQUIRE_WT_FILTER`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42262`  |  tradier_manage.py:33371
  - live code: `_dc_req_wt = bool(getattr(config, 'REENTRY2_DC_BREAK_REQUIRE_WT_FILTER', False))`
  - indicators: dc_high4_3m, dc_low4_3m
  - vec twin: create `vec_decisions/<family>.py::reentry2_dc_break_require_wt_filter_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY2_DIR_FAV_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58972`  |  tradier_manage.py:33375
  - live code: `if getattr(config, "REENTRY2_DIR_FAV_ENABLED", None) is not None: _ = 1  # REENTRY2_DIR_FAV_ENABLED — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry2_dir_fav_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_B16_SIZE_MULT_STRONG`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58974`  |  tradier_manage.py:33378
  - live code: `_=getattr(config, "REENTRY_B16_SIZE_MULT_STRONG", False)  # REENTRY_B16_SIZE_MULT_STRONG reentry`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_b16_size_mult_strong_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_B16_SIZE_MULT_WEAK`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58976`  |  tradier_manage.py:33380
  - live code: `_=getattr(config, "REENTRY_B16_SIZE_MULT_WEAK", False)  # REENTRY_B16_SIZE_MULT_WEAK reentry`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_b16_size_mult_weak_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_B16_SMA200_PROX_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58978`  |  tradier_manage.py:33382
  - live code: `_=getattr(config, "REENTRY_B16_SMA200_PROX_PCT", False)  # REENTRY_B16_SMA200_PROX_PCT reentry`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_b16_sma200_prox_pct_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_B16_SMA200_PULLBACK_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58980`  |  tradier_manage.py:33384
  - live code: `_=getattr(config, "REENTRY_B16_SMA200_PULLBACK_ENABLED", False)  # REENTRY_B16_SMA200_PULLBACK_ENABLED reentry`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_b16_sma200_pullback_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_BOUNCE_BAR_GR_ENABLED`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_bounce_bar_gr_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_CROSS_FRESHNESS_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58982`  |  tradier_manage.py:33386
  - live code: `_=getattr(config, "REENTRY_CROSS_FRESHNESS_ENABLED", False)  # REENTRY_CROSS_FRESHNESS_ENABLED reentry`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_cross_freshness_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_DC_MID_PULLBACK_ENABLED`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_dc_mid_pullback_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_EXHAUSTED_PARTIAL_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58984`  |  tradier_manage.py:33388
  - live code: `_=getattr(config, "REENTRY_EXHAUSTED_PARTIAL_ENABLED", False)  # REENTRY_EXHAUSTED_PARTIAL_ENABLED reentry`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_exhausted_partial_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_EXIT_RECLAIM_BUFFER_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:38689`  |  tradier_manage.py:33390
  - live code: `_buf = float(getattr(config, "REENTRY_EXIT_RECLAIM_BUFFER_PCT", 0.2)) / 100.0`
  - indicators: sma_200_1m
  - vec twin: create `vec_decisions/<family>.py::reentry_exit_reclaim_buffer_pct_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_EXIT_RECLAIM_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58986`  |  tradier_manage.py:33392
  - live code: `if getattr(config, "REENTRY_EXIT_RECLAIM_ENABLED", None) is not None: _ = 1  # REENTRY_EXIT_RECLAIM_ENABLED — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_exit_reclaim_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_K_RESET_GR_ENABLED`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_k_reset_gr_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_POST_CONSOL_ATR_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42707`  |  tradier_manage.py:33394
  - live code: `getattr(config, "REENTRY_POST_CONSOL_ATR_THRESHOLD", 0.15)`
  - indicators: bar_atr_rank_1h, bar_atr_rank_4h, bar_atr_rank_D
  - vec twin: create `vec_decisions/<family>.py::reentry_post_consol_atr_threshold_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_POST_CONSOL_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42705`  |  tradier_manage.py:33396
  - live code: `if getattr(config, "REENTRY_POST_CONSOL_ENABLED", True):`
  - indicators: bar_atr_rank_1h, bar_atr_rank_4h, bar_atr_rank_D
  - vec twin: create `vec_decisions/<family>.py::reentry_post_consol_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_POST_CONSOL_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58989`  |  tradier_manage.py:33398
  - live code: `if getattr(config, "REENTRY_POST_CONSOL_MULT", None) is not None: _ = 1  # REENTRY_POST_CONSOL_MULT — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_post_consol_mult_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_POST_CONSOL_TFS_REQUIRED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42709`  |  tradier_manage.py:33400
  - live code: `_tfs_req = int(getattr(config, "REENTRY_POST_CONSOL_TFS_REQUIRED", 2))`
  - indicators: bar_atr_rank_1h, bar_atr_rank_4h, bar_atr_rank_D
  - vec twin: create `vec_decisions/<family>.py::reentry_post_consol_tfs_required_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_PRICE_IMPROVE_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58991`  |  tradier_manage.py:33402
  - live code: `if getattr(config, "REENTRY_PRICE_IMPROVE_PCT", None) is not None: _ = 1  # REENTRY_PRICE_IMPROVE_PCT — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_price_improve_pct_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_PULLBACK_GR_SCORE_ENABLED`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_pullback_gr_score_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_SIZE_BREAKOUT_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42026`  |  tradier_manage.py:33404
  - live code: `_re_size_mult = float(getattr(config, "REENTRY_SIZE_EXTENDED_MULT", 0.5)) if _re_extended else (float(getattr(config, "REENTRY_SIZE_DIP_MULT", 1.5)) if _re_dip else float(getattr(c`
  - indicators: k_1h
  - vec twin: create `vec_decisions/<family>.py::reentry_size_breakout_mult_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_SIZE_DIP_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42026`  |  tradier_manage.py:33406
  - live code: `_re_size_mult = float(getattr(config, "REENTRY_SIZE_EXTENDED_MULT", 0.5)) if _re_extended else (float(getattr(config, "REENTRY_SIZE_DIP_MULT", 1.5)) if _re_dip else float(getattr(c`
  - indicators: k_1h
  - vec twin: create `vec_decisions/<family>.py::reentry_size_dip_mult_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_SIZE_EXTENDED_K1H`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42023`  |  tradier_manage.py:33408
  - live code: `_re_ext_thr = float(getattr(config, "REENTRY_SIZE_EXTENDED_K1H", 95.0))`
  - indicators: k_1h
  - vec twin: create `vec_decisions/<family>.py::reentry_size_extended_k1h_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_SIZE_EXTENDED_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42026`  |  tradier_manage.py:33410
  - live code: `_re_size_mult = float(getattr(config, "REENTRY_SIZE_EXTENDED_MULT", 0.5)) if _re_extended else (float(getattr(config, "REENTRY_SIZE_DIP_MULT", 1.5)) if _re_dip else float(getattr(c`
  - indicators: k_1h
  - vec twin: create `vec_decisions/<family>.py::reentry_size_extended_mult_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_SMA200_GR_CONTINUATION_ENABLED`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::reentry_sma200_gr_continuation_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`REENTRY_WT15M_SIZE_MULT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:42726`  |  tradier_manage.py:33412
  - live code: `# Size: REENTRY_WT15M_SIZE_MULT (default 1.5), stacks with E post-consolidation boost`
  - indicators: wt1_1h, wt1_4h, wt2_1h, wt2_4h
  - vec twin: create `vec_decisions/<family>.py::reentry_wt15m_size_mult_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).
- **`VEC_REENTRY_DC4_EXITPRICE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59117`  |  tradier_manage.py:32932
  - live code: `if bool(getattr(config, "VEC_REENTRY_DC4_EXITPRICE_ENABLED", False)):`
  - indicators: bb_pct_b_15m, klines_15m, wt1_15m
  - vec twin: create `vec_decisions/<family>.py::vec_reentry_dc4_exitprice_enabled_mask_vec`, mirror the live predicate 1:1, call once at the REENTRY stage (§3), prove ledger change (§8).

### 10.6  SIZING/RISK  (6 switches) — tabs: STDEV_SLOPE_SIZING / GLOBAL_RISK_GATES

- **`BTC_RZ_WT_DC_MULTIFACTOR`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6801`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BTC_RZ_WT_DC_MULTIFACTOR', 0.0))`
  - indicators: close
  - vec twin: create `vec_decisions/<family>.py::btc_rz_wt_dc_multifactor_mask_vec`, mirror the live predicate 1:1, call once at the SIZING/RISK stage (§3), prove ledger change (§8).
- **`INTRADAY_RATIO_CHECK_INTERVAL_MIN`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:23665
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::intraday_ratio_check_interval_min_mask_vec`, mirror the live predicate 1:1, call once at the SIZING/RISK stage (§3), prove ledger change (§8).
- **`INTRADAY_RATIO_COOLDOWN_MIN`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:23690
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::intraday_ratio_cooldown_min_mask_vec`, mirror the live predicate 1:1, call once at the SIZING/RISK stage (§3), prove ledger change (§8).
- **`INTRADAY_RATIO_DEVIATION_THR`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:23681
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::intraday_ratio_deviation_thr_mask_vec`, mirror the live predicate 1:1, call once at the SIZING/RISK stage (§3), prove ledger change (§8).
- **`INTRADAY_RATIO_REBALANCE_ENABLED`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:23658
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::intraday_ratio_rebalance_enabled_mask_vec`, mirror the live predicate 1:1, call once at the SIZING/RISK stage (§3), prove ledger change (§8).
- **`INTRADAY_RATIO_REQUIRE_TOP`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:23700
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::intraday_ratio_require_top_mask_vec`, mirror the live predicate 1:1, call once at the SIZING/RISK stage (§3), prove ledger change (§8).

### 10.7  GLOBAL/OTHER  (42 switches) — tabs: GLOBAL_RISK_GATES / cross-cutting

- **`BANDAID_OFF_LOSER_RECOVER_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6640`  |  tradier_manage.py:33071
  - live code: `_thr = float(getattr(config, 'BANDAID_OFF_LOSER_RECOVER_PCT', 0.0))`
  - indicators: close, sma_200_1h
  - vec twin: create `vec_decisions/<family>.py::bandaid_off_loser_recover_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`BAND_ARROW_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6646`  |  tradier_manage.py:5650
  - live code: `if bool(getattr(config, 'BAND_ARROW_ENABLED', False)):`
  - indicators: close, sma_200_1h
  - vec twin: create `vec_decisions/<family>.py::band_arrow_enabled_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`BAND_ARROW_SLOPE_DEADBAND`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6655`  |  tradier_manage.py:5647
  - live code: `if _c <= _thr: return False, 'BAND_ARROW_SLOPE_DEADBAND_THR'`
  - indicators: close, wt1_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::band_arrow_slope_deadband_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`BTC_ACCEL_RAMP_REQUIRE_POSITIVE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6748`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BTC_ACCEL_RAMP_REQUIRE_POSITIVE', 0.0))`
  - indicators: close, dc_position_15m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::btc_accel_ramp_require_positive_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`BTC_HARD_BLOCK_OTHER_ACCOUNTS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6789`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BTC_HARD_BLOCK_OTHER_ACCOUNTS', 0.0))`
  - indicators: close
  - vec twin: create `vec_decisions/<family>.py::btc_hard_block_other_accounts_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`BTC_ROUND_BANDS_EACH_SIDE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6795`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'BTC_ROUND_BANDS_EACH_SIDE', 0.0))`
  - indicators: close
  - vec twin: create `vec_decisions/<family>.py::btc_round_bands_each_side_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`CRYPTO_SPIKE_FADE_THRESHOLD_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6846`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'CRYPTO_SPIKE_FADE_THRESHOLD_PCT', 0.0))`
  - indicators: close, wt1_3m, wt2_15m, wt2_3m
  - vec twin: create `vec_decisions/<family>.py::crypto_spike_fade_threshold_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`DAYTRADE_DC_TARGET_TF`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::daytrade_dc_target_tf_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`DC_DAYTRADE_TARGET_DC_BUFFER_PCT`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32601
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dc_daytrade_target_dc_buffer_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`DC_DAYTRADE_TARGET_USE_DC4_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32600
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dc_daytrade_target_use_dc4_15m_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`DC_DAYTRADE_TARGET_USE_DC_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32599
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::dc_daytrade_target_use_dc_15m_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`DC_MOMENT_STRONG_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:6889`  |  tradier_manage.py:5647
  - live code: `_thr = float(getattr(config, 'DC_MOMENT_STRONG_THRESHOLD', 0.0))`
  - indicators: close, k_3m, wt2_15m
  - vec twin: create `vec_decisions/<family>.py::dc_moment_strong_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`EXECUTE_NOW_MAX_MARK_AGE_S`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:30692`  |  tradier_manage.py:25942
  - live code: `getattr(config, "EXECUTE_NOW_MAX_MARK_AGE_S", 3.0)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::execute_now_max_mark_age_s_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`FG_FEAR_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:41470`  |  tradier_manage.py:33121
  - live code: `if _fg_val <= getattr(config, "FG_FEAR_THRESHOLD", 25):`
  - indicators: fear_greed, value
  - vec twin: create `vec_decisions/<family>.py::fg_fear_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`FG_GREED_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59487`  |  tradier_manage.py:33123
  - live code: `if float(getattr(config, "FG_GREED_THRESHOLD", 0) or 0) != 0: _ = 1  # FG_GREED_THRESHOLD — BATCH2`
  - indicators: funding_rate
  - vec twin: create `vec_decisions/<family>.py::fg_greed_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`GOLDEN_RULE_BASE_USD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58621`  |  tradier_manage.py:33152
  - live code: `if getattr(config, "GOLDEN_RULE_BASE_USD", None) is not None: _ = 1  # GOLDEN_RULE_BASE_USD — BATCH 5`
  - indicators: funding_rate, klines_1h, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::golden_rule_base_usd_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`HA_WICK_QUALITY_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58656`  |  tradier_manage.py:33194
  - live code: `if bool(getattr(config, "HA_WICK_QUALITY_ENABLED", False)):`
  - indicators: adx_1h, klines_1h, max_gain
  - vec twin: create `vec_decisions/<family>.py::ha_wick_quality_enabled_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`HA_WICK_QUALITY_SCORE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58660`  |  tradier_manage.py:33196
  - live code: `if bool(getattr(config, "HA_WICK_QUALITY_SCORE", False)):`
  - indicators: adx_1h, klines_1h
  - vec twin: create `vec_decisions/<family>.py::ha_wick_quality_score_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`HA_WICK_QUALITY_TF`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58664`  |  tradier_manage.py:33198
  - live code: `if bool(getattr(config, "HA_WICK_QUALITY_TF", False)):`
  - indicators: adx_1h, dc_position, klines_1h
  - vec twin: create `vec_decisions/<family>.py::ha_wick_quality_tf_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`HLR_SMA_BAND_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59544`  |  tradier_manage.py:33641
  - live code: `if bool(getattr(config, "HLR_SMA_BAND_PCT", False)):`
  - indicators: dc_position, hlr_w, klines_1h
  - vec twin: create `vec_decisions/<family>.py::hlr_sma_band_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`HLR_TOP_MIN_TFS`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59548`  |  tradier_manage.py:33642
  - live code: `if bool(getattr(config, "HLR_TOP_MIN_TFS", False)):`
  - indicators: dc_position, klines_1h
  - vec twin: create `vec_decisions/<family>.py::hlr_top_min_tfs_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`LIVE_VEC_EMERGENCY_BRAKE_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58712`  |  tradier_manage.py:26047
  - live code: `if getattr(config, "LIVE_VEC_EMERGENCY_BRAKE_ENABLED", None) is not None: _ = 1  # LIVE_VEC_EMERGENCY_BRAKE_ENABLED — BATCH 5`
  - indicators: mfi_1h
  - vec twin: create `vec_decisions/<family>.py::live_vec_emergency_brake_enabled_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`MACD_ZERO_CROSS_ENABLED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58742`  |  tradier_manage.py:33276
  - live code: `if bool(getattr(config, "MACD_ZERO_CROSS_ENABLED", False)):`
  - indicators: klines_15m, macd_hist
  - vec twin: create `vec_decisions/<family>.py::macd_zero_cross_enabled_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`MACD_ZERO_CROSS_SCORE`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58746`  |  tradier_manage.py:33278
  - live code: `if bool(getattr(config, "MACD_ZERO_CROSS_SCORE", False)):`
  - indicators: klines_15m, macd_hist
  - vec twin: create `vec_decisions/<family>.py::macd_zero_cross_score_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`MACD_ZERO_CROSS_TF`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58750`  |  tradier_manage.py:33280
  - live code: `if bool(getattr(config, "MACD_ZERO_CROSS_TF", False)):`
  - indicators: klines_15m, macd_hist
  - vec twin: create `vec_decisions/<family>.py::macd_zero_cross_tf_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`MOVER_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58833`  |  tradier_manage.py:33296
  - live code: `if bool(getattr(config, "MOVER_THRESHOLD", False)):`
  - indicators: klines_15m, rsi_15m
  - vec twin: create `vec_decisions/<family>.py::mover_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`MTF_GR_MIN_IND`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58856`  |  tradier_manage.py:33306
  - live code: `_v = getattr(config, "MTF_GR_MIN_IND", None)`
  - indicators: klines_15m, rsi_15m, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::mtf_gr_min_ind_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`MTS_BOTTOM_BONUS_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58862`  |  tradier_manage.py:33308
  - live code: `if bool(getattr(config, "MTS_BOTTOM_BONUS_THRESHOLD", False)):`
  - indicators: klines_15m, rsi_15m, rsi_1h
  - vec twin: create `vec_decisions/<family>.py::mts_bottom_bonus_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`MTS_BOTTOM_STRONG_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58866`  |  tradier_manage.py:33310
  - live code: `if bool(getattr(config, "MTS_BOTTOM_STRONG_THRESHOLD", False)):`
  - indicators: klines_15m, rsi_15m
  - vec twin: create `vec_decisions/<family>.py::mts_bottom_strong_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58884`  |  tradier_manage.py:33314
  - live code: `if getattr(config, "NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT", None) is not None: _ = 1  # NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::newborn_loss_kill_gain_threshold_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58885`  |  tradier_manage.py:33316
  - live code: `if getattr(config, "NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST", None) is not None: _ = 1  # NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST — BATCH 5`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::newborn_loss_kill_require_vel_against_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`NEW_POSITION_MAX_LOSS_THRESHOLD`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:44650`  |  tradier_manage.py:33320
  - live code: `and position.gain > config.NEW_POSITION_MAX_LOSS_THRESHOLD`
  - indicators: d_3m, k_3m, k_3m_prev
  - vec twin: create `vec_decisions/<family>.py::new_position_max_loss_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`OI_CONFIRM_MIN_CHANGE_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:58930`  |  tradier_manage.py:33350
  - live code: `if bool(getattr(config, "OI_CONFIRM_MIN_CHANGE_PCT", False)):`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::oi_confirm_min_change_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`SENTIMENT_REBAL_COOLDOWN_MIN`** — vec:`STUB` ez:`STUB` td:`REAL`
  - live: `ez_manage.py:2444`  |  tradier_manage.py:22860
  - live code: `_ = getattr(config, 'SENTIMENT_REBAL_COOLDOWN_MIN', None)`
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::sentiment_rebal_cooldown_min_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`SIMPLE_TP_PCT`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59080`  |  tradier_manage.py:33440
  - live code: `if bool(getattr(config, "SIMPLE_TP_PCT", False)):`
  - indicators: bb_pct_b_15m, klines_15m
  - vec twin: create `vec_decisions/<family>.py::simple_tp_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`TRADIER_DC_DAYTRADE_TARGET_DC_BUFFER_PCT`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32601
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::tradier_dc_daytrade_target_dc_buffer_pct_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`TRADIER_DC_DAYTRADE_TARGET_USE_DC4_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32600
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::tradier_dc_daytrade_target_use_dc4_15m_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M`** — vec:`STUB` ez:`ABSENT` td:`REAL`
  - live: `ez=ABSENT (copy from tradier first)`  |  tradier_manage.py:32599
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::tradier_dc_daytrade_target_use_dc_15m_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`WRONG_SIDE_WT_TFS_REQUIRED`** — vec:`STUB` ez:`REAL` td:`REAL`
  - live: `ez_manage.py:59119`  |  tradier_manage.py:33457
  - live code: `if getattr(config, "WRONG_SIDE_WT_TFS_REQUIRED", None) is not None: _ = 1  # WRONG_SIDE_WT_TFS_REQUIRED — BATCH 5`
  - indicators: bb_pct_b_15m, klines_15m
  - vec twin: create `vec_decisions/<family>.py::wrong_side_wt_tfs_required_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`WT_DC_DIRECT_DC_TF`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::wt_dc_direct_dc_tf_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`WT_DC_DIRECT_THRESHOLD`** — vec:`STUB` ez:`STUB` td:`REAL`
  - live: `ez: no clean decision site — inspect`  |  tradier_manage.py:1112
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::wt_dc_direct_threshold_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).
- **`WT_DC_TF_COMBO`** — vec:`STUB` ez:`ABSENT` td:`ABSENT`
  - live: `ez=ABSENT (copy from tradier first)`  |  td=ABSENT
  - indicators: (none detected — read the block)
  - vec twin: create `vec_decisions/<family>.py::wt_dc_tf_combo_mask_vec`, mirror the live predicate 1:1, call once at the GLOBAL/OTHER stage (§3), prove ledger change (§8).

---

## 11. BATCH PLAN & TRACKING

Total to wire: **258** switches. Suggested order (highest sweep value first):
1. **ENTRY** gates/filters — they change which trades exist, so every downstream switch depends on them (do these first).
2. **EXIT** families — largest group; many are scored exits (use the score+threshold pattern, §7).
3. **AUGMENT / REDUCE** — gain-gated; verify against the augment/reduce ledger events.
4. **REENTRY** — depend on entry filters; wire after ENTRY.
5. **SIZING/RISK / GLOBAL** — multipliers and portfolio gates; some (execute_now, leaderboard, funding) are structurally un-vectorizable and stay `VEC_UNSUPPORTED`.

Track every switch in a ledger (`data/reports/vec_wiring_progress.json`): `{switch: {status: TODO|WIRED|HONEST_ZERO|VEC_UNSUPPORTED, vec_module, call_site_line, ledger_proof: {sym, base_trades, on_trades}, parity: PASS|FAIL, deployed_md5}}`. A switch is only closed when status is WIRED+parity PASS, HONEST_ZERO (proven no ledger change in live too), or VEC_UNSUPPORTED (with reason).

Re-run `tools/build_cat_side_defaults_4.py` after any template change to keep the 4-side defaults == template bold.

---

## 12. ANTI-PATTERNS (instant reject in review)

- Any proxy indicator standing in for the real one (`rsi` for Fear&Greed, `adx` for 'age', `bb` for 'confirm').
- `mask[0] ^= True`, `np.arange(n)%k`, `hash(...)`, `RandomState`, `_ = getattr(cfg, X)` no-op reads.
- Wiring inside a §10.0 passthrough farm.
- A hardcoded default in the live/vec call instead of a real config/config_tradier field.
- Reading a 3m/5m array (none exist) or an indicator not in the NPZ.
- Reporting a non-zero delta without a corresponding trade-ledger change.
- Overwriting a whole file from an older backup (restore). Only diff and re-add the missing piece.

*Worklist generated from `data/cat_side_defaults_4.json` (468 template switches) x wiring audit on 2026-09-30. 258 switches are not yet REAL in `v12_quick_engine`. Authoritative per-switch status = the ledger-change test (§8), not grep — some entries here may already bind via `vec_decisions` and only need a ledger check to close.*
