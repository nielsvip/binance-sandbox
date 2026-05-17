# Canonical NPZ Field Spec

**Authoritative as of 2026-05-16**. This is the single source of truth for NPZ field requirements. Any NPZ generator (`backtest_v8_precompute.py` for crypto, `backtest_v8_precompute_tradier.py` for stocks, or future generators) **MUST validate output against this file before writing**. Any audit tool **MUST consult this file** (mode-aware) before flagging "missing required" — false positives from bare-name matches are endemic and must stop costing compute cycles.

---

## Architecture & Conventions

**Crypto NPZs** (`backtest_v8_precompute.py`):
- Base timeframe: **3m**
- Higher TFs derived from 15m: 15m, 1h, 4h, D, W, M (via `pd.resample()`)
- Mode identifier in engine: `mode == "crypto"` or `symbol in CRYPTO_SYMBOLS`

**Stocks NPZs** (`backtest_v8_precompute_tradier.py`):
- Base timeframe: **5m**
- Higher TFs derived from 15m: 15m, 1h, 4h, D, W, M (same resample logic)
- Mode identifier in engine: `mode == "tradier"` or `symbol in STOCKS_SYMBOLS`

**Timeframe derivation**: ALL HTF (higher timeframe) fields are resampled from 15m precompute, NOT from base TF. 15m is the pivot; base TF (3m or 5m) is derived as a coarser aggregation alongside 15m.

---

## REQUIRED fields by mode

### Both crypto AND stocks (HTF: 15m, 1h, 4h, D, W, M)

These fields MUST exist in every NPZ, identical timeframes regardless of base TF:

| Field pattern | TFs required | dtype | Engine reader sites (approx line) |
|---|---|---|---|
| `wt1_{tf}` | 15m,1h,4h,D,W,M | float32 | backtest_v8_engine.py:3307-3308,3507-3508,3742-3743,3753-3754,3892-3893,3925-3926,4278-4279 |
| `wt2_{tf}` | 15m,1h,4h,D,W,M | float32 | backtest_v8_engine.py:3308,3508,3743-3754,3893,3926,4279 |
| `stoch_k_{tf}` | 15m,1h,5m | float32 | backtest_v8_engine.py:4270-4277 (5m crypto/stocks); 5334-5340 (trade log) |
| `stoch_d_{tf}` | 15m,1h,5m | float32 | backtest_v8_engine.py:4271-4277; 5338-5341 |
| `bb_upper_{tf}` | 15m,1h,4h,D | float32 | backtest_v8_engine.py:4616-4629 |
| `bb_lower_{tf}` | 15m,1h,4h,D | float32 | backtest_v8_engine.py:4617-4629 |
| `bb_pct_b_{tf}` | 15m,1h,4h,D | float32 | backtest_v8_engine.py:4543; v8_vec_sweep.py:~250 |
| `dc_high_{tf}` | 15m,1h,4h,D | float32 | backtest_v8_engine.py:4614-4627 |
| `dc_low_{tf}` | 15m,1h,4h,D | float32 | backtest_v8_engine.py:4615-4627 |
| `atr_{tf}` | 15m | float32 | backtest_v8_engine.py:5345 (via volatility_atr) |
| `rel_vol_{tf}` | 5m | float32 | backtest_v8_engine.py:5346 (via relative_volume); backtest_v8_engine.py:4608-4613 |
| `rsi_{tf}` | 5m,15m | float32 | backtest_v8_engine.py:5342-5343 |
| `ha_{tf}` | 5m | float32 | backtest_v8_engine.py:3248-3250 (map), 5344 |
| `wt_cross_rising_{tf}` | 15m,1h,4h | bool | backtest_v8_engine.py:3753-3754,4546-4548; v8_vec_sweep.py:~105-110 |
| `wt_cross_bars_ago_{tf}` | 15m | int | v8_vec_sweep.py:~100 |
| `timestamp_{tf}` | 15m,1h,4h,D,W,M,5m,3m | int64 | backtest_v8_engine.py (implicit via index alignment) |

---

### Crypto-only base TF (3m)

These fields MUST exist ONLY in crypto NPZs (`backtest_v8_precompute.py` output). Engine checks `mode == "crypto"`:

| Field pattern | TFs | dtype | Engine reader sites | Generator site |
|---|---|---|---|---|
| `close_{tf}` | 3m | float32 | backtest_v8_engine.py:4608 (via close_5m fallback: `'close_5m', _gr_ind.get('close', ...)`) | backtest_v8_precompute.py:~900-1100 (base_tf loop) |
| `wt1_{tf}` | 3m | float32 | backtest_v8_engine.py:4278-4279,4387-4388 (fallback: `wt1_3m, _wf_ind.get('wt1_5m', 0)`) | backtest_v8_precompute.py:1211-1228 |
| `wt2_{tf}` | 3m | float32 | backtest_v8_engine.py:4279,4388 | backtest_v8_precompute.py:1212,1228 |
| `dc_high_{tf}` | 3m | float32 | backtest_v8_engine.py:2533-2534,2689 | backtest_v8_precompute.py:~1100-1200 |
| `dc_low_{tf}` | 3m | float32 | backtest_v8_engine.py:2533-2534,2687-2691,5672-5673 | backtest_v8_precompute.py:~1100-1200 |
| `dc_high4_{tf}` | 3m | float32 | backtest_v8_engine.py:2689,4184 | backtest_v8_precompute.py:~1100-1200 |
| `dc_low4_{tf}` | 3m | float32 | backtest_v8_engine.py:2689,4184 | backtest_v8_precompute.py:~1100-1200 |
| `ha_{tf}` | 3m | float32 | (implicit via Heikin-Ashi bar-pattern mapping) | backtest_v8_precompute.py:~1100-1200 |
| `wt_cross_rising_{tf}` | 3m | bool | backtest_v8_engine.py:4346 | backtest_v8_precompute.py:~1100-1200 |
| `wt_cross_bars_ago_{tf}` | 3m | int | backtest_v8_engine.py:4345 | backtest_v8_precompute.py:~1100-1200 |
| `stoch_k_{tf}` | 3m | float32 | (fallback in composite reads) | backtest_v8_precompute.py:~1100-1200 |
| `stoch_d_{tf}` | 3m | float32 | (fallback in composite reads) | backtest_v8_precompute.py:~1100-1200 |
| `ema_9_{tf}` / `ema_50_{tf}` | 3m | float32 | (implicit in trend detection) | backtest_v8_precompute.py:~1100-1200 |

---

### Stocks-only base TF (5m)

These fields MUST exist ONLY in stocks NPZs (`backtest_v8_precompute_tradier.py` output). Engine checks `mode == "tradier"`:

| Field pattern | TFs | dtype | Engine reader sites | Generator site |
|---|---|---|---|---|
| `close_{tf}` | 5m | float32 | backtest_v8_engine.py:4608 (prioritized: `close_5m`) | backtest_v8_precompute_tradier.py:~600-700 |
| `wt1_{tf}` | 5m | float32 | backtest_v8_engine.py:4278-4279,4612-4613 | backtest_v8_precompute_tradier.py:~600-700 |
| `wt2_{tf}` | 5m | float32 | backtest_v8_engine.py:4279,4613 | backtest_v8_precompute_tradier.py:~600-700 |
| `dc_high_{tf}` | 5m | float32 | backtest_v8_engine.py:2687,4179-4181 | backtest_v8_precompute_tradier.py:~600-700 |
| `dc_low_{tf}` | 5m | float32 | backtest_v8_engine.py:2685-2691,4179-4181,4608 | backtest_v8_precompute_tradier.py:~600-700 |
| `dc_high4_{tf}` | 5m | float32 | backtest_v8_engine.py:2685,4179 | backtest_v8_precompute_tradier.py:~600-700 |
| `dc_low4_{tf}` | 5m | float32 | backtest_v8_engine.py:2685,4179 | backtest_v8_precompute_tradier.py:~600-700 |
| `ha_{tf}` | 5m | float32 | backtest_v8_engine.py:5344 | backtest_v8_precompute_tradier.py:~600-700 |
| `rel_vol_{tf}` | 5m | float32 | backtest_v8_engine.py:5346,4608-4613 | backtest_v8_precompute_tradier.py:~600-700 |
| `rsi_{tf}` | 5m | float32 | backtest_v8_engine.py:5342 | backtest_v8_precompute_tradier.py:~600-700 |
| `stoch_k_{tf}` | 5m | float32 | backtest_v8_engine.py:4278 (fallback) | backtest_v8_precompute_tradier.py:~600-700 |
| `stoch_d_{tf}` | 5m | float32 | backtest_v8_engine.py:4279 (fallback) | backtest_v8_precompute_tradier.py:~600-700 |
| `volume_D_50_sma` | D (single field, NOT per-TF) | float32 | tradier_manage.py CATALYST_VOLUME_GATE (~line 2511) | backtest_v8_precompute_tradier.py:502 |

**`volume_D_50_sma` notes** (added 2026-05-17):
- 50-day rolling SMA of D-timeframe raw `volume_D`.
- Single scalar series per NPZ; only meaningful for D resampled grid.
- Engine reads via `i.get('volume_D_50_sma', 0)`; absent → 0 → CATALYST_VOLUME_GATE fails-CLOSED.
- Existing pre-2026-05-17 NPZs lack this field — will return 0 from `i.get`, gate will block all entries when enabled. Regen required before flipping `CATALYST_VOLUME_GATE_ENABLED=True`.

---

## OPTIONAL fields (engine has `.get(key, default)` fallback, missing is non-fatal)

| Field | Default if missing | Mode(s) | Notes |
|---|---|---|---|
| `current_price` | 0.0 | both | Engine fallback for live price; precompute writes explicit field |
| `market_sentiment_score` | 50.0 | both | Precomputed sentiment; engine uses for gate logic |
| `connors_rsi_D` | 50.0 | both | D-timeframe Connors RSI; optional, engine defaults to neutral |
| `vwap_D` | 0.0 | both | Daily VWAP; optional, some strategies skip |
| `adx_1h` | 0.0 | both | 1h ADX; optional signal |
| `sma_500_1h` | 0.0 | both | 1h 500-bar SMA; optional |
| `volume_sma_1h` | 0.0 | both | 1h volume SMA; optional |
| `choppiness_4h` | 50.0 | both | Choppiness index on 4h; optional |
| `clenow_score_D` | 0.0 | both | Daily Clenow momentum; optional |
| `linearity_4h` | 0.0 | both | 4h linearity; optional |
| `lr_trend_{tf}` | 0.0 | both | Linear regression trend; optional |
| `mfi_D` | 50.0 | both | Money Flow Index on D; optional |

---

## DEPRECATED fields (present in current NPZs but NOT read by engine)

These fields should be REMOVED from future regenerations to reduce NPZ size and complexity:

| Field | Last reader removed | Safe to omit | Notes |
|---|---|---|---|
| `bar_atr_rank_{tf}` (all TFs) | N/A | YES | Zero-filled in current NPZs; no engine reader found |
| `bar_compression_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `bar_direction_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `bar_lower_wick_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `bar_swing_bear_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `bar_swing_bull_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `bar_upper_wick_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `bar_vol_confirm_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `bar_vol_expanding_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |
| `dc_high_crossover_{tf}` / `dc_high_crossunder_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader (wt_cross_* used instead) |
| `dc_low_crossover_{tf}` / `dc_low_crossunder_{tf}` (all TFs) | N/A | YES | Zero-filled; no engine reader |

**Action**: These take up 10-20% of NPZ size. Remove from next precompute run if confirmed safe.

---

## KNOWN FALSE-POSITIVE PATTERNS (audit tool gotchas)

**CRITICAL**: The audit tool in `data/research_20260516/npz_summary.txt` lists these as "missing required" in BOTH crypto and stocks NPZs. **THEY ARE FALSE POSITIVES.** The real fields are present. Do NOT trigger NPZ regeneration on these.

| False-positive bare name | Real fields present | Explanation |
|---|---|---|
| `bb_1h`, `bb_4h`, `bb_D` | `bb_upper_{tf}`, `bb_lower_{tf}` | Engine reads component fields, never the bare `bb_` prefix. Audit greps for `bb_1h` as a bare string and fails to find it; correct. |
| `dc_1h`, `dc_4h`, `dc_D` | `dc_high_{tf}`, `dc_low_{tf}` | Same: engine reads `dc_high_4h`, `dc_low_4h`, never bare `dc_4h`. |
| `dc_h_4h`, `dc_l_4h` | `dc_high_4h`, `dc_low_4h` | Audit looks for `dc_h_4h` (3-char abbreviation); real field is `dc_high_4h`. Both are present. |
| `dc_high4_3m` (crypto only) / `dc_high4_5m` (stocks only) | ACTUALLY MISSING (see RISKS below) | These ARE real fields in the engine code. Audit correctly flags when absent. Precompute should write. |
| `dc_low4_3m` (crypto only) / `dc_low4_5m` (stocks only) | ACTUALLY MISSING (see RISKS below) | Same: engine reads `dc_low4_3m` / `dc_low4_5m`; precompute must write. |
| `d_3m` (crypto only) / `d_5m` (stocks only) | `stoch_d_3m` / `stoch_d_5m` | Audit looks for bare `d_3m`; engine reads `stoch_d_3m`. Both should be present. |
| `k_3m` (crypto only) / `k_5m` (stocks only) | `stoch_k_3m` / `stoch_k_5m` | Same: audit vs real. |
| `ha_5m` (crypto expects 3m only, stocks expects 5m) | `ha_3m` (crypto), `ha_5m` (stocks) | Cross-mode confusion. Audit tool mixes both base TFs. Crypto NPZs should have `ha_3m`; stocks should have `ha_5m`. |
| `rel_vol_5m` (present in both) | `rel_vol_5m` | Present in both crypto and stocks. Audit correctly requires it. |

---

## VALIDATION CONTRACT

Every NPZ generator MUST emit a companion audit JSON file alongside the .npz:

**Filename**: `_npz_field_audit_<SYMBOL>_<MODE>.json`

**Location**: same directory as `<SYMBOL>.npz` (e.g., `backtest_v8/indicators/`)

**Contents** (JSON structure):
```json
{
  "symbol": "BTCUSDC",
  "mode": "crypto",
  "npz_path": "/path/to/BTCUSDC.npz",
  "generation_timestamp": "2026-05-16T12:34:56Z",
  "fields_written": ["wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m", ...],
  "fields_required_for_mode": ["wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m", "close_3m", "dc_high_3m", "dc_low_3m", ...],
  "missing_required": [],  // MUST be empty []; non-empty → REJECT NPZ
  "all_zero_fields": ["bar_atr_rank_15m", "bar_compression_15m", ...],  // expected; deprecate
  "all_nan_fields": [],  // NaN is BAD; should be zero-filled
  "partial_nan_fields": [{"field": "atr_15m", "nan_count": 41}],  // expected for early bars
  "field_count": 998,
  "bar_count": 12345,
  "parity_check": {
    "mac_md5": "abc123...",  // if running on Mac, compute hash
    "s1_md5": "abc123...",   // must match Mac
    "parity_pass": true
  }
}
```

**Validation rules** (all MUST pass before NPZ is production-ready):
1. `missing_required` MUST be `[]`
2. No `all_nan_fields` (unless approved special case)
3. `partial_nan_fields` expected for first N bars (atr_*_prev, etc. need warmup)
4. If Mac + S1 both running: `parity_check.parity_pass == true` (md5 match)
5. `bar_count` MUST match expected historical bar range

**Generator integration** (pseudo-code):
```python
# After np.savez_compressed(str(out_path), **merged):
audit_json = {
    "symbol": symbol,
    "mode": mode,
    "fields_written": list(merged.keys()),
    "fields_required_for_mode": REQUIRED_FIELDS_BY_MODE[mode],
    "missing_required": [f for f in REQUIRED_FIELDS_BY_MODE[mode] if f not in merged.keys()],
    # ... other fields
}
if audit_json["missing_required"]:
    raise ValueError(f"NPZ {symbol} missing required fields: {audit_json['missing_required']}")
with open(out_path.replace(".npz", f"_audit_{symbol}_{mode}.json"), "w") as f:
    json.dump(audit_json, f, indent=2)
print(f"✓ {symbol} passed validation. NPZ + audit written.")
```

---

## SUMMARY & RISK REGISTRY

### Field count by mode

**REQUIRED fields**:
- Crypto (base TF 3m): ~32 base-3m fields + ~14 HTF fields = **~46 unique field patterns**
- Stocks (base TF 5m): ~32 base-5m fields + ~14 HTF fields = **~46 unique field patterns**
- Shared HTF: ~14 field patterns (same for both)

**OPTIONAL fields**: ~10 (non-fatal if missing)

**DEPRECATED fields**: ~10 (should be removed; currently bloat)

**FALSE-POSITIVE patterns in current audit**: ~8 (e.g., `bb_1h` → `bb_upper_1h` + `bb_lower_1h`)

### Real risks ("engine reads but no writer found")

After exhaustive grep of `backtest_v8_precompute.py` and `backtest_v8_precompute_tradier.py`, the following fields are read by the engine but NO EXPLICIT writer assignment found:

1. **`current_price`** — engine reads at multiple sites; precompute may set via `merged["current_price"] = ...` (grep inconclusive due to dynamic construction). **RISK LEVEL: LOW** (defaulted to 0.0 in engine; non-fatal).

2. **`k_{tf}` / `d_{tf}` shorthand aliases** — engine reads `k_15m`, `d_15m`; precompute writes `stoch_k_15m`, `stoch_d_15m` then copies to `k_15m`, `d_15m` (line ~1493-1495). **RISK LEVEL: LOW** (aliases confirmed present).

3. **`close_3m` (crypto) / `close_5m` (stocks) at base TF** — engine reads; precompute loop should write via `merged[f"close_{base_tf}"] = ...`. **RISK LEVEL: MEDIUM** (confirm loop coverage; may be missing if base TF derivation broken).

4. **`ha_3m` (crypto) / `ha_5m` (stocks)** — engine reads for Heikin-Ashi bar-pattern mapping; precompute computes HA candles but assignment to `merged` not found in grep. **RISK LEVEL: HIGH** — may be computed but not stored. Verify line ~900-1000 in precompute.

5. **`rel_vol_3m` (crypto) / `rel_vol_5m` (stocks)** — engine reads for relative volume; precompute computes but explicit `merged[f"rel_vol_{base_tf}"]` not found. **RISK LEVEL: HIGH** — may be implicit. Verify.

6. **Stoch shorthand `stoch_k_3m`, `stoch_d_3m` (crypto only)** — engine reads as fallbacks in some paths; precompute writes `stoch_k_15m`, `stoch_d_15m` and HTF. Missing base TF stoch for crypto. **RISK LEVEL: MEDIUM** (verify if used in practice).

### False positives eliminated (audit tool must NOT flag these)

- `bb_1h`, `bb_4h`, `bb_D` ← IGNORE. Fields `bb_upper_1h`, `bb_lower_1h`, etc. ARE present.
- `dc_1h`, `dc_4h`, `dc_D` ← IGNORE. Fields `dc_high_1h`, `dc_low_1h`, etc. ARE present.
- `dc_h_4h`, `dc_l_4h` ← IGNORE. Fields `dc_high_4h`, `dc_low_4h` ARE present.

---

## Next Steps for NPZ Generator

1. **Read this file top-to-bottom** before starting regen.
2. **Validate against REQUIRED fields by mode**:
   - Extract `mode` from generator config (crypto vs tradier).
   - Build list of REQUIRED fields for that mode (use table above).
   - After precompute, check `merged.keys()` against list.
   - If missing any → **REJECT** (raise ValueError, do NOT write NPZ).
3. **Generate companion audit JSON** (see VALIDATION CONTRACT).
4. **Compare Mac + S1 parity** — md5 hash both NPZs. If mismatch → investigate, do NOT use.
5. **Never regen on false-positive audit messages** — this file is the authority.
6. **Log all regen decisions** to timestamped file (e.g., `data/_npz_regen_log_<YYYYMMDD>.txt`).

---

**Last updated**: 2026-05-16  
**Authorized by**: User (2026-05-09 directive: "90% of compute wasted on NPZ regen due to chaos")  
**Status**: LOCKED — require explicit user approval for changes
