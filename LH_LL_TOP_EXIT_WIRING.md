# LH_LL_TOP_EXIT — experimental structure-triggered top/bottom exit (wiring spec)

Status: WIRED 2026-10-04 (USER "unlock all") — all sections below APPLIED on Mac,
verified, files RELOCKED (see LOCKED_FILES.md log). FLEET 2026-10-04: engine e467
+ vec module + config knobs + all 8 templates confirmed on S1/S2/S5 (via fleet
sync + peer parity_go cut); live-twin hunks + test file pushed by this session
(server backups before_lhll_twins_202610040300, uniform md5s, compile-verified).
No v12/config/pilot/template cuts by this session (Monday freeze respected; FREEZE LIFTED 2026-10-04 by USER — cuts resume, work straight through).
Maintained suite green Mac/S1/S2/S5. FLEET DATA NOTE (not LHLL, under dispute):
S1 GALA/MOG/PEPE/BTC/ETH/SOL yield 0-1 trades under raw/sweep/template-bold
baselines while S1 CHR/DOT/AAPL/MSTR trade 100+ and S2/S5 GALA trade 128 —
symbol-selective gate binding on S1's rebuilt NPZs (1165 keys vs S5 945, gains
diverge at same counts). Peer reports S1 GALA 127tr (unreproduced; exact repro
requested). LHLL tests green wherever trades exist. NPZ-md5-per-result
recording recommended for Monday sheets (offered to implement).

## 1. What it does

LONG: when the previous COMPLETED 4h and/or D bar prints a lower high (LH)
and/or lower low (LL), the position is exited near the top of the bounce that
forms inside the next 4h bar — via 15m WaveTrend rollover (the forming 4h
(lower) high measured on wt 15m), via a touch of `dc_high_1h`, or both —
instead of riding the bounce down into the `dc_low_4h − 0.25%` stop.

SHORT mirrors (vv): previous bar prints a higher high (HH) and/or higher low
(HL) → exit near the forming bottom via 15m WT roll-up / `dc_low_1h` touch
instead of the `dc_high_4h + 0.25%` stop.

The DC stop is NOT modified and stays the worst-case backstop. When no bounce
forms, the stop fires exactly as before — first-fire-wins, no suppression.

## 2. Verified module (no lock needed, already on disk)

- `vec_decisions/lh_ll_top_exit.py` — pure scalar core (`struct_armed`,
  `wt_leg_fires`, `dc_leg_fires`, `near_edge`, `check_lh_ll_top_exit`) +
  vectorized `build_exit_mask` + `resolve_lh_ll_top_exit` + `parse_struct_tf`.
- `vec_decisions/test_lh_ll_top_exit.py` — run:
  `python3 vec_decisions/test_lh_ll_top_exit.py`

Proof (2026-10-04, Mac, `1000000MOGUSDT.npz` n=38446, post-`align_store`):

- 768,000 scalar-vs-vector checks, **0 mismatches** (both sides × TF {4h,D,4h+D}
  × struct {LH,LL,LH_LL,LH_AND_LL} × leg {WT15M,DC1H,EITHER,BOTH} × price-confirm).
- Causality: truncated-store prefix identical (no lookahead). Deterministic.
  Default (`OFF`/False) → all-False mask (idempotency-safe).
- Mask build 1.5 ms / 38k bars (vector budget is 0.07 s/eval — negligible).
- Fire density sane, e.g. LONG 4h EITHER 7.41%, BOTH 0.04% (cross-into-
  resistance is rare by design). Per-bar trigger bars, not trades — real exit
  counts come from the engine A/B after wiring.

## 3. Knobs (add to all 3 configs, defaults = inert)

```python
LH_LL_TOP_EXIT_ENABLED: bool = False
LH_LL_TOP_EXIT_STRUCT_TF: str = "OFF"            # "4h" | "D" | "4h,D"
LH_LL_TOP_EXIT_STRUCT_MODE: str = "LH_LL"        # "LH" | "LL" | "LH_LL" | "LH_AND_LL"
LH_LL_TOP_EXIT_MODE: str = "EITHER"              # "WT15M" | "DC1H" | "EITHER" | "BOTH"
LH_LL_TOP_EXIT_DC1H_BUFFER_PCT: float = 0.10
LH_LL_TOP_EXIT_BOTH_TOL_PCT: float = 0.30
LH_LL_TOP_EXIT_REQUIRE_PRICE_CONFIRM: bool = False
```

- `config.py` (Config, live crypto), `config_tradier.py` (TradierConfig, live
  stocks), `v12_quick_engine.py` (QuickConfig backtest both + `apply_tradier_defaults`
  overlay where the stock value differs — here identical, so no overlay line).
- Short semantics mirror automatically inside the predicate (LH→HH, LL→HL);
  no separate short knobs.
- After wiring: `data/cat_side_defaults_4.json` rebuild will pick up the OFF
  defaults (no live change).

## 4. Vector engine call site (v12_quick_engine.py, in-loop, scalar)

Prep (next to `_dd_stop_specs` resolution, ~L12217):

```python
import vec_decisions.lh_ll_top_exit  # top imports
_lhll_spec = vec_decisions.lh_ll_top_exit.resolve_lh_ll_top_exit(lambda _k, _d: getattr(cfg, _k, _d))
```

Fire (per-bar position loop, BEFORE the `elif daytrade_on and ...` DC block at
~L13314 so the reason string shows the top exit when both trigger same bar;
economics identical either way — first-fire-wins):

```python
elif _lhll_spec.get("enabled") and pos is not None:
    try:
        _lhll_ind = {"high_4h": float(_safe(npz, "high_4h", n, 0.0)[i]), "high_4h_prev": float(_safe(npz, "high_4h_prev", n, 0.0)[i]),
            "low_4h": float(_safe(npz, "low_4h", n, 0.0)[i]), "low_4h_prev": float(_safe(npz, "low_4h_prev", n, 0.0)[i]),
            "high_D": float(_safe(npz, "high_D", n, 0.0)[i]), "high_D_prev": float(_safe(npz, "high_D_prev", n, 0.0)[i]),
            "low_D": float(_safe(npz, "low_D", n, 0.0)[i]), "low_D_prev": float(_safe(npz, "low_D_prev", n, 0.0)[i]),
            "wt1_15m": float(w1arr[i]), "wt2_15m": float(w2arr[i]),
            "dc_high_1h": float(_safe(npz, "dc_high_1h", n, 0.0)[i]), "dc_low_1h": float(_safe(npz, "dc_low_1h", n, 0.0)[i])}
        _lhll_fire, _lhll_reason = vec_decisions.lh_ll_top_exit.check_lh_ll_top_exit(
            _lhll_spec, _lhll_ind, float(px), float(close[i-1]) if i > 0 else 0.0,
            float(w1arr[i-1]) if i > 0 else 0.0, float(w2arr[i-1]) if i > 0 else 0.0, is_long)
        if _lhll_fire:
            closed, reason = True, _lhll_reason
    except Exception:
        pass
```

Hoist the `_safe(...)` arrays to prep (one fetch per array, not per bar).
Honour the global `MIN_HOLD_BARS_BEFORE_EXIT` gate the same way the DC block
does (place inside the same hold-guard).

Alternative (mask OR into `exit_sig` in `compute_exit_signals`): one line
`exit_sig |= build_exit_mask(npz, n, is_long, spec, _safe)` — cheaper, but the
in-loop placement gives reason attribution and matches the DC-block pattern.
Pick ONE, never both.

## 5. Live twins (same predicate, completed-bar inputs only)

- Crypto `ez_manage.process_position`: indicator snapshot already carries
  `high_4h/high_4h_prev/low_4h/low_4h_prev/high_D/low_D` (see ez_manage ~L835);
  add `high_D_prev/low_D_prev` read (or fall back: no D-arm when missing).
  WT prev = previous 15m snapshot values; `dc_high/low_1h` from snapshot.
- Stocks `tradier_manage.process_position`: same keys via indicator bundle;
  if `_prev` keys are absent, pass 0 → that TF honestly does not arm (never
  substitute forming-bar values).
- Full close via the normal exit path (`execute_now` gate for crypto).
- CAUTION: live must use COMPLETED HTF bars. Forming-bar H/L would arm/exit on
  unstable intra-bar extremes (live equivalent of lookahead).

## 6. Template rows (ONE script only — v15_avg_delta_apply pipeline)

Tab `EXIT_VELOCITY` (velocity/WT exits; DC1H leg is a channel touch but the
family is a top-exit, matching HLR_TOP precedent in REDUCE — either tab is
defensible; EXIT_VELOCITY keeps it next to WT_LOWER_CROSS_EXIT_TF):

- `LH_LL_TOP_EXIT_ENABLED` False (YES) / True
- `LH_LL_TOP_EXIT_STRUCT_TF` OFF (YES) / 4h / D / 4h,D
- `LH_LL_TOP_EXIT_STRUCT_MODE` LH_LL (YES) / LH / LL / LH_AND_LL
- `LH_LL_TOP_EXIT_MODE` EITHER (YES) / WT15M / DC1H / BOTH
- `LH_LL_TOP_EXIT_DC1H_BUFFER_PCT` 0.10 (YES) / 0.05 / 0.25 / 0.50
- `LH_LL_TOP_EXIT_BOTH_TOL_PCT` 0.30 (YES) / 0.10 / 0.50 / 1.00
- `LH_LL_TOP_EXIT_REQUIRE_PRICE_CONFIRM` False (YES) / True

Per §63 order law: the ENABLED sanction-style rows precede the tightening rows
so confirmation/mode variants are always evaluated with the exit alive.
`FILTER_DICTIONARY_V2` SPECIFIC rows for the yellow cross-product
(struct × leg) so each STRUCT_TF row gets WT15M/DC1H/BOTH yellows.

## 7. Proof plan after wiring (before ANY promotion thought)

1. Idempotency: every default flip reads exactly 0 (§21).
2. Ledger proof: `include_ledger=True` A/B — non-zero deltas must show changed
   CLOSE bars/reasons (`LH_LL_TOP_EXIT ...`), never identical ledgers (§19).
3. Stress (backtest-expert): buffer/tol at 50/100/150%, STRUCT_MODE variants,
   price-confirm on/off — need a plateau, not a spike; 1.5–2× slippage;
   year-by-year / 365D (§44) — 365D < 50% of 30D = overfit, abandon.
4. Sample floor: ≥48 crypto / ≥100 stocks, ≥30 trades/sym or [DIAGNOSTIC ONLY].
5. Scalar parity via `backtest_v12_engine` on the winning set (§43) before live.
6. Live: per-sym paper first; the DC stop stays untouched throughout.

## 8. What was NOT changed (locks honoured)

No edit to `v12_quick_engine.py`, `config.py`, `config_tradier.py`,
`ez_manage.py`, `tradier_manage.py`, `TEMPLATE_*.xlsx`, or any live config.
New files only: `vec_decisions/lh_ll_top_exit.py`,
`vec_decisions/test_lh_ll_top_exit.py`, this doc.
