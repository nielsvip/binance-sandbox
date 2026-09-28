# PROPOSAL — Surface Hidden Tradier Reduce Paths as Switches (2026-09-28)

Status: **AWAITING USER APPROVAL.** The edits below touch live trading code
(`config_tradier.py` — currently LOCKED per LOCKED_FILES.md row of 2026-09-28 17:30 —
and `tradier_manage.py`). The permission classifier declined letting an agent apply
them; apply by hand or hand this file to a session with permission after saying
"unlock config_tradier.py".

Already done WITHOUT touching live code (this session):
- TEMPLATE_STOCKS_LONG/SHORT.xlsx: +16 rows REDUCE_PROFIT_LOCK (PARTIAL_PROFIT_LOCK_*
  ×5 switches, WT_D_BOUNCE_DD_STOP_ENABLED), +28 rows REDUCE_SIGNAL_RATER
  (INTRADAY_RATIO_* ×7, SENTIMENT_REBAL_* ×2, EOD_SLIM_RATIO_ENABLED) — first row of
  each group = live default (bold).
- v15_pilot.py: `LIVE_ONLY_SWITCHES` frozenset — the portfolio-level ones
  (INTRADAY_RATIO_*, EOD_SLIM, SENTIMENT_REBAL_*) are never evaluated in sheets
  (reason "LIVE_ONLY: portfolio-level state, not single-symbol vectorizable").
- Vectorizable and handed to the engine-parity agent: PARTIAL_PROFIT_LOCK_* and
  WT_D_BOUNCE_DD_STOP_ENABLED (pure position math), plus the two new families below
  once switches exist (Overbought/Oversold TP needs `k_5m`/`d_5m`/`rsi_5m` — present
  in NPZs; Market_Against needs a market-bias series — check NPZ before claiming).

## Section 1 — new config_tradier.py switches (behavior-preserving defaults)

Exact literals transcribed from `tradier_manage.py` ~14495–14558 on 2026-09-28:

```python
# --- Market_Against_Position bias reduce (currently hardcoded ~tradier_manage.py:14506-14538) ---
MARKET_AGAINST_REDUCE_ENABLED: bool = True          # today: always on (no gate)
MARKET_AGAINST_REDUCE_BIAS_THR: float = 0.3         # long: bias < -0.3 / short: bias > 0.3
MARKET_AGAINST_REDUCE_GAIN_LOW_PCT: float = 0.0     # gain > 0
MARKET_AGAINST_REDUCE_GAIN_HIGH_PCT: float = 0.08   # gain < 0.08
MARKET_AGAINST_REDUCE_MIN_SINCE_AUG_MIN: float = 15.0  # min_since_aug > 15
MARKET_AGAINST_REDUCE_FRACTION: float = 0.30        # int(position_amt * 0.3)

# --- Overbought / Oversold take-profit reduce (hardcoded ~14540-14558) ---
OVERBOUGHT_TP_REDUCE_ENABLED: bool = True           # today: always on (no gate)
OVERBOUGHT_TP_STOCH_K_THR: float = 85.0             # k_5m > 85
OVERBOUGHT_TP_RSI_THR: float = 75.0                 # rsi_5m > 75
OVERBOUGHT_TP_REQUIRE_K_BELOW_D: bool = True        # k_5m < d_5m
OVERBOUGHT_TP_MIN_GAIN_PCT: float = 2.0             # gain > 2.0
OVERBOUGHT_TP_REDUCE_FRACTION: float = 0.25         # int(position_amt * 0.25)
OVERSOLD_TP_STOCH_K_THR: float = 15.0               # short mirror: k_5m < 15
OVERSOLD_TP_RSI_THR: float = 25.0                   # rsi_5m < 25 (k_5m > d_5m mirror)
```

Both paths remain gated by `TRADIER_MIN_HOLD_MINUTES` (fallback
`config.MIN_HOLD_MINUTES_TRADIER` 240.0) — unchanged.

## Section 2 — tradier_manage.py literal→config swaps (diff-style)

```
@@ ~14506 (Market_Against trigger) @@
-                    if (is_long and market_bias < -0.3) or (not is_long and market_bias > 0.3):
+                    _mar_thr = float(getattr(config, 'MARKET_AGAINST_REDUCE_BIAS_THR', 0.3))
+                    if getattr(config, 'MARKET_AGAINST_REDUCE_ENABLED', True) and ((is_long and market_bias < -_mar_thr) or (not is_long and market_bias > _mar_thr)):
@@ ~14509 @@
-                        if gain > 0 and gain < 0.08 and min_since_aug > 15:
+                        if gain > float(getattr(config, 'MARKET_AGAINST_REDUCE_GAIN_LOW_PCT', 0.0)) and gain < float(getattr(config, 'MARKET_AGAINST_REDUCE_GAIN_HIGH_PCT', 0.08)) and min_since_aug > float(getattr(config, 'MARKET_AGAINST_REDUCE_MIN_SINCE_AUG_MIN', 15)):
@@ ~14514 @@
-                                _raw_qty = int(position_amt * 0.3)
+                                _raw_qty = int(position_amt * float(getattr(config, 'MARKET_AGAINST_REDUCE_FRACTION', 0.3)))
@@ ~14540 (Overbought long) @@
-                    if is_long and i.get('k_5m', 50) > 85 and i.get('rsi_5m', 50) > 75 and i.get('k_5m', 50) < i.get('d_5m', 50) :
-                        if gain > 2.0:
+                    if is_long and getattr(config, 'OVERBOUGHT_TP_REDUCE_ENABLED', True) and i.get('k_5m', 50) > float(getattr(config, 'OVERBOUGHT_TP_STOCH_K_THR', 85)) and i.get('rsi_5m', 50) > float(getattr(config, 'OVERBOUGHT_TP_RSI_THR', 75)) and ((not getattr(config, 'OVERBOUGHT_TP_REQUIRE_K_BELOW_D', True)) or i.get('k_5m', 50) < i.get('d_5m', 50)):
+                        if gain > float(getattr(config, 'OVERBOUGHT_TP_MIN_GAIN_PCT', 2.0)):
@@ ~14545 @@
-                                reduce_qty = max(1, int(position_amt * 0.25))
+                                reduce_qty = max(1, int(position_amt * float(getattr(config, 'OVERBOUGHT_TP_REDUCE_FRACTION', 0.25))))
@@ ~14552 (Oversold short mirror) @@
-                    elif not is_long and i.get('k_5m', 50) < 15 and i.get('rsi_5m', 50) < 25 and i.get('k_5m', 50) > i.get('d_5m', 50) :
-                        if gain > 2.0 and _ovr_min_hold_ok:
-                            reduce_qty = max(1, int(position_amt * 0.25))
+                    elif not is_long and getattr(config, 'OVERBOUGHT_TP_REDUCE_ENABLED', True) and i.get('k_5m', 50) < float(getattr(config, 'OVERSOLD_TP_STOCH_K_THR', 15)) and i.get('rsi_5m', 50) < float(getattr(config, 'OVERSOLD_TP_RSI_THR', 25)) and i.get('k_5m', 50) > i.get('d_5m', 50):
+                        if gain > float(getattr(config, 'OVERBOUGHT_TP_MIN_GAIN_PCT', 2.0)) and _ovr_min_hold_ok:
+                            reduce_qty = max(1, int(position_amt * float(getattr(config, 'OVERBOUGHT_TP_REDUCE_FRACTION', 0.25))))
```

(`config` here = the module object tradier_manage reads its knobs from — verify the
file's exact config-access idiom (`_cfg_auto` is used nearby) and prefer that idiom
when applying. All defaults equal the removed literals → zero live behavior change.)

## Section 3 — USER DECISION: INTRADAY_RATIO loop blindness (H1 root cause)

`INTRADAY_RATIO_TRIM` (~23085–23128) fires `execute_trade_action` and never checks
the result; the "[BROKER_SYNC_DEMAND] … will demand fresh sync thereafter" promise is
never fulfilled, so tracked qty never decrements (AGI 21/72.0 ×3, NEM 3/10.0 ×6) and
the same trim re-fires every cooldown — blind on accounts where orders fail
(trc: 87× ORDER_API_ERROR today, including failed HARD-STOP closes).

Proposed minimal fix (NOT applied):
1. After each ratio trim, record a pending-reduce shadow qty; subtract it from the
   deviation input until a broker sync confirms or refutes the fill.
2. On ORDER_API_ERROR from a trim (or any REDUCE/CLOSE), exponential backoff for that
   symbol + a desktop alert — never silent re-fire on the next cooldown.
3. Policy question for the user: ratio trims currently cut positions at −3…−7.9%
   (UNIVERSAL_NOLOSS_GATE=False). If trims must respect NOLOSS, gate
   REDUCE-at-loss reasons or add a ratio-trim-specific loss floor.
4. Separately and URGENTLY: trc's "Invalid API response" storm (87×, includes failed
   hard stops AGI/AU/CVX/LSCC) is under investigation by a dedicated agent — do not
   consider trc protected until resolved.
