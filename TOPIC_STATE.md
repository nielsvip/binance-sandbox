# TOPIC_STATE — Topic & Diagnostic Tracker

This document tracks active diagnostic issues, recent backtest sweeps, and roadmap progress for the trading system.

---

## 🔍 Session Diagnosis — 2026-05-23

We conducted an extensive review of the historical data, previous configurations, and locked files to resolve the **>$80k loss** and identify the path forward.

### 1. Root Cause Breakdown
1. **Annualization Game**: Prior sessions frequency-gamed the Sharpe ratio using `sqrt(bars_per_year)` instead of `sqrt(trades_per_year)` or raw pooled calculations, promoting unstable configurations.
2. **STRICT_NO_LOSS Entrapment**: Holding losing positions forever instead of cutting them via technical indicators caused massive drawdowns.
3. **Parabolic Suicide Buys**: Buying overbought breakouts when K > 90.
4. **Incorrect Stop-Loss TF**: Stocks used a `15m` ATR trail which is too slow (approx. 5 bars on 5m TF) compared to a `5m` ATR trail (1 bar).

---

## 📊 Backtest Sweeps & Findings

### Stocks Stop-Loss Sweep (2026-05-20)
* **Parameters**: 171 symbols × 2.13 years × 5m base TF.
* **Winner**: `ATR_5m_x2.0` (2x ATR trail on the 5m base TF).
* **Pool Sharpe**: `+0.2257` (per-trade unannualized).
* **Worst Trade**: Capped at `-6.32%`.
* **gain_per_yr**: `+5123.58%/yr` (exponentially outperforming the 2000%/yr target).
* **Drawdown**: `-6.32%` (extremely tight control).
* **Verdict**: Stocks base TF (5m) requires 1-bar trail (`5m` TF), whereas crypto base TF (3m) is validated with `15m` (5-bar).

---

## 🗺️ Roadmap Goals

1. **Keep STRICT_NO_LOSS Disabled**: Ensure that all blanket no-loss variables remain disabled in `config.py` and `config_tradier.py` to prevent positions from getting buried.
2. **Secure the 3 Loss-Exit Paths**: Ensure `R1_DC_LOW4_3M_EMERGENCY`, `R2_WT_VEL_SLOW`, and `HEDGE_FAILED` are the *only* paths that can close positions at a loss.
3. **Optimize the S1 Sweep Fleet**: Maintain the 60-70% CPU/MEM utilization floor on S1. Alternate crypto-heavy and stock-heavy sweeps hourly.
4. **Preserve Memory**: Never modify source code without updating `INDEX.md`, `SCRIPT_STATE.md`, and `TOPIC_STATE.md`.
