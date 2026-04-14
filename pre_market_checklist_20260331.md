# Pre-Market Checklist — 2026-03-31

## By 12:00 UTC (pre-market prep):

### 1. Apply V5 backtest findings to live config
- [ ] NOLOSS_MIN_PROFIT_PCT_TRADIER: decide 3.0% vs 1.0% based on 121-sym test
- [ ] TRB_NOLOSS_MIN_PROFIT_PCT: match above
- [x] WT exit gain gate: APPLIED (tradier_manage.py line 3160)
- [x] IBS exit: ALREADY IN CODE (line 3376-3393), uses 5m prev bar data from live indicators

### 2. Compile check all scripts
- [ ] python -c "import py_compile; py_compile.compile('tradier_manage.py', doraise=True)"
- [ ] python -c "import py_compile; py_compile.compile('config_tradier.py', doraise=True)"

### 3. Restart tradier services
- [ ] tradier_manage.py --accounts trb (REAL money)
- [ ] tradier_manage.py --accounts trc (paper)
- [ ] tradier_indicators.py
- [ ] tradier_rankings.py

### 4. Verify position sync
- [ ] Check tradier_positions matches API
- [ ] Verify no phantom positions

### 5. Monitor first 30 min
- [ ] Watch logs for IBS_EXHAUSTION exits
- [ ] Watch logs for WT_2of3_DC_EXIT (should only fire on winners now)
- [ ] Check no errors/crashes
