# Implementation Suggestions: Integrating WaveTrend + HTF Structure Hybrid Exits

To maximize the performance of your baseline configurations (`per_sym`) and the rolling 7-day reconfig agent (`7D`), we suggest integrating the WaveTrend + Higher Timeframe Structure hybrid exit (OR Gate) directly into the backtest engine and optimization sweeps.

---

## 🛠️ Suggestion 1: Port Hybrid Exits to `per_sym_engine_crypto.py`

### Current Gap
The `per_sym_engine_crypto.py` backtest engine does not load the candlestick structure variables (`high_D_prev`, `low_D_prev`, `high_4h_prev`, `low_4h_prev`, etc.) and lacks the hybrid OR-gate logic. Thus, the sweep profiles cannot optimize these parameters.

### Action Plan
1. **Load structure variables**: Modify `load_3m_base` to retrieve `open_{tf}`, `high_{tf}_prev`, and `low_{tf}_prev` for higher timeframes (`15m`, `1h`, `4h`, `D`).
2. **Expand `SymParams` class**: Add two configuration parameters:
   * `EXIT_STRUCT_TF: str = 'None'` (e.g. `'15m'`, `'1h'`, `'4h'`, `'D'`, or `'None'`)
   * `EXIT_HYBRID_OR: bool = True`
3. **Update Exit Signal Logic**: Inside the engine's `compute_signals` loop, generate the structural breakdown mask on `EXIT_STRUCT_TF` and combine it using logical OR with the WaveTrend crossover signal:
   ```python
   # Inside LONG exit logic:
   struct_exit = transition_exit_mask(open_tf, high_tf_prev, low_tf_prev, is_long=True)
   exit_ = wt_bear_strong | struct_exit
   ```

---

## 🔍 Suggestion 2: Expand `per_sym_crypto_profiles` Mutation Grid

### Current Gap
The 4-year baseline configurator sweeps exit filters only on entry thresholds, hold bars, and WaveTrend TFs, but does not search for the optimal structural exit timeframe per symbol.

### Action Plan
In `per_sym_crypto_profiles.py` -> `mutation_grid()`, add the new parameters to the sweep space:
* Test `EXIT_STRUCT_TF` across `['15m', '1h', '4h', 'D']` under `EXIT_HYBRID_OR = True`.
* Sweeping this allows the optimizer to find the optimal exit timeframe per symbol (e.g. highly volatile spot tokens might choose `4h` or `1h` exits, while major tokens like BTC/ETH might select `D` or `None`).

---

## 🏃 Suggestion 3: Enable 7-Day Rolling Adaptive Exits in `per_sym_7d_agent`

### Current Gap
The rolling 7-day tuning agent runs candidate variations in the neighborhood of the 4-year baseline. If the baseline doesn't include the hybrid TF overrides, the 7D agent cannot adapt them to the current weekly regime.

### Action Plan
In `per_sym_7d_agent.py` -> `neighborhood_variants()`, add candidate neighborhood changes for the exit timeframe:
* **Tighter variant**: Shift structural exit timeframe down (e.g., if baseline is `4h`, test `1h` or `15m` to cut losses faster in volatile regimes).
* **Looser variant**: Shift structural exit timeframe up (e.g., test `D` or `None` to let profits run during a steady trend).
* **Benefit**: The 7D agent will automatically downshift the structural exit TF during choppy market environments to limit drawdowns, and upshift it during clean macro trends to maximize gains.

---

## 🛡️ Suggestion 4: Define Side-Specific Defaults in `config.py` & `config_tradier.py`

### Current Gap
LONG and SHORT sides are distinct demographics, and Stocks and Crypto have different participants. We need separate default parameter fallbacks.

### Action Plan
In `config.py` and `config_tradier.py`, set distinct defaults based on the 4-year sweep winners:

```python
# Crypto Exits (config.py)
LONG_STRUCT_EXIT_TF = 'D'       # Macro trend alignment
SHORT_STRUCT_EXIT_TF = '4h'     # Fast escape from short-squeeze runaways

# Stock Exits (config_tradier.py)
LONG_STRUCT_EXIT_TF = 'D'       # Institutional gap protection
SHORT_STRUCT_EXIT_TF = '15m'    # Intraday risk protection
```

---

## ⚡ Suggestion 5: Wire the OR-Gate exits in `ez_manage.py` and `tradier_manage.py`

### Action Plan
In the exit check loops of `ez_manage.py` and `tradier_manage.py`, implement the OR-gate check:
1. Fetch the configured `STRUCT_EXIT_TF` for the active symbol.
2. If `STRUCT_EXIT_TF` is active, check if `high_TF < high_TF_prev and low_TF < low_TF_prev` (for LONG position).
3. Trigger the exit immediately if either this structure break **OR** the primary WaveTrend crossover occurs.
