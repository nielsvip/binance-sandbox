# SCRIPT_STATE — Core Script Status Tracker

This document tracks the active features, MD5 checksums, and sandbox status of the primary trading orchestrators.

---

## 📁 Critical Files Inventory

### 1. `ez_manage.py` (MacBook + S1 Sandbox)
* **Status**: `Active / Live Trading`
* **Role**: Primary orchestrator for crypto accounts (`ang`, `inf`, `fin`, `flz`, `men`).
* **Active Features**:
  * `execute_now()`: Sole entry gate for all Binance orders.
  * `R1_DC_LOW4_3M_EMERGENCY`: Newborn emergency close on DC breach or 3x ATR_3m breach.
  * `R2_WT_VEL_SLOW`: WT velocity deceleration exit near breakeven.
  * `UNIVERSAL_NOLOSS_GATE`: Hard disabled (`False`).
* **MD5 Parity**: Verified bit-identical on MacBook and S1.

### 2. `tradier_manage.py` (MacBook + S1 Sandbox)
* **Status**: `Active / Live Trading`
* **Role**: Primary orchestrator for stock accounts (`trb`, `trc`).
* **Active Features**:
  * `R1_DC_LOW4_5M_EMERGENCY`: Newborn emergency close on 5m TF.
  * `R2_WT_VEL_SLOW`: WT velocity deceleration exit.
  * `MTF_EXIT_USE_COMPOUND`: Enabled (`True`).
  * `MTF_ATR_TRAIL_ENABLED`: Enabled (`True`), mult=2.0, TF=5m.
  * `UNIVERSAL_NOLOSS_GATE`: Hard disabled (`False`).
* **MD5 Parity**: Verified bit-identical on MacBook and S1.

### 3. `config.py` (MacBook + S1 Sandbox)
* **Status**: `Active / Live Config`
* **Role**: Crypto-mode system parameters.
* **Important Settings**:
  * `STRICT_NO_LOSS_ACCOUNTS = []` (disabled).
  * `UNIVERSAL_NOLOSS_GATE = False` (disabled).
  * `WT_3M_FORCE_OPEN_ENABLED = False` (disabled).
* **MD5 Parity**: Verified bit-identical on MacBook and S1.

### 4. `config_tradier.py` (MacBook + S1 Sandbox)
* **Status**: `Active / Live Config`
* **Role**: Stocks-mode system parameters.
* **Important Settings**:
  * `STRICT_NO_LOSS_ACCOUNTS_TRADIER = []` (disabled).
  * `UNIVERSAL_NOLOSS_GATE = False` (disabled).
  * `MTF_EXIT_USE_COMPOUND = True` (enabled).
  * `MTF_ATR_TRAIL_TF_TRADIER = '5m'` and `MTF_ATR_TRAIL_MULT = 2.0` (optimal stop).
* **MD5 Parity**: Verified bit-identical on MacBook and S1.

---

## ☠️ Banned Systems & Safety Guards

* **Percentage Stops**: `HARD_STOP_LOSS_MAX_PAIN` and `% stops` must remain **DISABLED** in all stock scripts. Exits must be strictly technical.
* **Blanket No-Loss**: `UNIVERSAL_NOLOSS_GATE` and `STRICT_NO_LOSS` must remain **DISABLED**. Exits must use the 3 loss-exit paths (R1, R2, and HEDGE_FAILED).
* **Suicide Reentries**: `WT_3M_FORCE_OPEN` and extreme-K reentries are strictly **BANNED** until swept.
