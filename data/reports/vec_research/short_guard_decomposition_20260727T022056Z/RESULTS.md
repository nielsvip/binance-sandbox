# SHORT guard decomposition

Research-only. Every row was selected on D1+D2 before FINAL was read.

| book | key | profile | entries | strategy | short B&H | long B&H | DD | TIM | exits | emergency covers | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BEAR | IBIT_SHORT | G00_ALL_VETOES | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G11_DAY_GAIN | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G12_RSI_15M | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G13_RSI_1H | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G14_BULL_D_CANDLE | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G15_BULL_4H_CANDLE | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G16_NO_BEAR_HTF_CONFIRM | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G17_BULL_D_WT | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G20_OVERBOUGHT_BLOCK | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G21_BULL_STATE_BLOCK | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |
| BEAR | IBIT_SHORT | G99_TOP_REJECTION_PATH_SCOPED | 6 | +0.00% | +28.32% | -28.32% | 0.00% | 0.00% | 0 | 0 | GRAY_REJECTED |

## Data-contract errors

- BEAR MSTR: ['stoch_k_4h unusable: finite=87.9%, nonzero=86.8%, unique=217', 'stoch_k_D unusable: finite=61.0%, nonzero=60.9%, unique=50', 'missing required field lrL_pct_b_4h', 'missing required field lrL_slope_4h', 'missing required field lrL_pct_b_D', 'missing required field lrL_slope_D']
- BEAR COIN: ['timestamp_15m is base timestamp alias; NPZ predates closed-bar fix', 'timestamp_1h is base timestamp alias; NPZ predates closed-bar fix', 'timestamp_4h is base timestamp alias; NPZ predates closed-bar fix', 'timestamp_D is base timestamp alias; NPZ predates closed-bar fix']
