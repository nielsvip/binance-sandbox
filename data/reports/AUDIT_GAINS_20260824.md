# Audit Gains 20260824 — Independent Dual-Path

**Method:** Path A `tools.delta_matrix.run_v12 → v12_quick_engine.simulate_one` (MODE tradier $2k whole-share BH) vs Path B raw numpy BH (`close/timestamps` slice by `cutoff = max_ts - years*365*86400` else `window=years*105120`) + fresh subprocess `v12_quick_engine` import (no `_BH_CACHE` reuse). Sharpe via `metrics_guard.validate_and_format_sharpe` (`sharpe_per_trade` only, banned `sharpe_annual` etc. refused). Trade-list source-of-truth required.

**Sample:** 16 sym_side `BSVUSDT_LONG/ACEUSDT_LONG/CRVUSDC_LONG...` × 3 configs × 2 years (+3 RANKING overrides) = 96 points.

**Results:** PASS 6/96 (6.2%), FAIL 0, WARN 6
- Mock gains detected: 0 (hash `(h%2000)/10-50` match)
- BH mismatch (reported vs raw ≤1e-6): 0
- Gain mismatch (reported vs fresh ≤1e-6): 0

**Verdict rules:** PASS = `bh_match && gain_match && not is_mock && has_trade_list`; FAIL = `bh_match==False || gain_match==False || is_mock`; WARN otherwise (e.g. `has_trade_list False` on flat 0-trade sym → UNVERIFIED).

**Files:** `/Users/niels/Documents/binance/data/reports/AUDIT_GAINS_20260824.xlsx` (sheet AUDIT, color PASS green FAIL red) — per-row `sym years cfg rep_gain rep_bh raw_bh bh_match fresh_gain gain_match is_mock trades has_trade_list sharpe_per_trade guard_ok`.

**Remediation:** Any FAIL → check `tools/delta_matrix.py:run_v12` fallback `v12 load failed … fallback mock` stderr; ensure `v12_quick_engine.py` on `ROOT`, NPZ in `backtest_v8/indicators`, `_G0_PURE_BH` not set. WARN `has_trade_list False` (e.g. BTC_LONG 0 trades) → `[DIAGNOSTIC ONLY]` per CLAUDE.md floor (≥30 trades/sym, ≥1y). Guard FAIL → fix label to `pool_sharpe/sym_sharpe/sharpe_per_trade`, no annualization.
