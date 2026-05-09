# PROPOSED EXIT REWRITE — 2026-05-09

User mandate (3 rules + NO_LOSS purge), crypto + tradier in parallel this session.
**No edits applied yet. This doc shows every line change for review.**

---

## 0. Existing-code map (what's already there)

| Rule | Status | Existing location | Gap |
|---|---|---|---|
| **R1** dc_low4_3m emergency close | Partial | `ez_positions_quick.py:3504` (score function `_score_close`, gated `min_since_aug<12`) | Not a hard-close path; not always-on; no alert; no entry-signal naming |
| **R2** WT_15m delta @ ~0 gain | ~90% done | `ez_manage.py:20696-20742` (`WT_15M_VEL_SLOW`, added 2026-05-08, widened today) | Band default 0.10% (user wants 0.50%); no `gain >= +0.01%` lower bound; missing tradier mirror |
| **R3** MTF gate on all other exits | Not done | n/a | XL refactor — separate session |
| **NO_LOSS purge** | 50+ files reference | `UNIVERSAL_NOLOSS_GATE`, `STRICT_NO_LOSS_ACCOUNTS`, `NOLOSS_MIN_PROFIT_PCT` | Surgical neutralization via config flips, not full code deletion (this session) |

**This session ships A+B+C+D+E (below). R3 deferred.**

---

## A. NO_LOSS NEUTRALIZATION — config flips only

Strategy: flip the master gates OFF. Existing code that reads them becomes no-ops without crashing on missing keys. Full code deletion scheduled for separate cleanup pass.

### A.1 `config.py` — 4 line changes

```diff
- STRICT_NO_LOSS_ACCOUNTS = ['ang','flz', 'men', 'fin', 'inf']  # line 328
+ STRICT_NO_LOSS_ACCOUNTS = []  # 2026-05-09: DISBANDED. NO_LOSS liquidated 2 accts -80%. R1/R2 + technicals replace it.
```

```diff
- NOLOSS_DC4H_GATE_ENABLED: bool = True   # line 498
+ NOLOSS_DC4H_GATE_ENABLED: bool = False  # 2026-05-09: NO_LOSS purge — R1 dc_low4_3m + R2 WT_15M_VEL_SLOW supersede
```

```diff
- UNIVERSAL_NOLOSS_GATE: bool = True  # line 952
+ UNIVERSAL_NOLOSS_GATE: bool = False  # 2026-05-09: NO_LOSS purge. R1 + R2 + technicals must fire freely.
```

```diff
- NOLOSS_MIN_PROFIT_PCT: float = 0.5   # (defined elsewhere, search-and-replace)
+ NOLOSS_MIN_PROFIT_PCT: float = 0.0   # 2026-05-09: NO_LOSS purge — all gates that compare `gain >= _noloss_min` pass at 0
```

### A.2 `config_tradier.py` — 3 line changes

```diff
- TRA_NO_LOSS_EXIT: bool = True   # line 89
+ TRA_NO_LOSS_EXIT: bool = False  # 2026-05-09: NO_LOSS purge

- STRICT_NO_LOSS_ACCOUNTS_TRADIER = ('trb', 'trc')  # ~line 2047 region
+ STRICT_NO_LOSS_ACCOUNTS_TRADIER = ()  # 2026-05-09: NO_LOSS purge

- NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 3.0  # (multiple refs)
+ NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 0.0  # 2026-05-09: NO_LOSS purge
```

`UNIVERSAL_NOLOSS_GATE` in config_tradier.py (line 2101) is already `False` — no change.

### A.3 `backtest_v8_engine.py` — already nukes these lists at 1682-1686. Keep as-is (no longer a divergence from live now that live = `[]` too).

### A.4 Files that grep `STRICT_NO_LOSS_BYPASS_REASONS` lookups → safe (lookups return `[]`, no KeyError).

---

## B. R1 — POST-OPEN dc_low4_3m EMERGENCY CLOSE + DESKTOP ALERT

### B.1 New helper module — `ez_alert.py` (NEW FILE, not locked)

```python
"""Desktop alerts for emergency exit fires. Native macOS notification + JSONL log."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone

ALERT_LOG = "data/sweep_alerts/bad_exits.jsonl"


def _osascript_notify(title: str, subtitle: str, body: str) -> None:
    try:
        msg = f'display notification "{body}" with title "{title}" subtitle "{subtitle}"'
        subprocess.Popen(["osascript", "-e", msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def alert_bad_exit(account: str, position_key: str, gain: float, reason: str,
                   entry_signal: str, entry_ts: str, current_price: float) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "position_key": position_key,
        "gain_pct": round(gain, 4),
        "exit_reason": reason,
        "entry_signal": entry_signal,
        "entry_ts": entry_ts,
        "current_price": current_price,
    }
    os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)
    try:
        with open(ALERT_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    _osascript_notify(
        title=f"BAD EXIT — {position_key}",
        subtitle=f"{account} | {reason}",
        body=f"g={gain:.2f}% | entry={entry_signal[:40]} @ {entry_ts[:19]}",
    )
```

### B.2 `ez_manage.py` — insert R1 block at line ~20680, BEFORE `WT_15M_VEL_SLOW` (R2)

Pseudocode of insertion (exact lines computed at edit time):

```python
# ═══════════════════════════════════════════════════════════════════════════
# R1 — DC_LOW4_3M EMERGENCY CLOSE (USER 2026-05-09)
# Always-on while position has NEVER been meaningfully profitable (max_gain <= 0.5%).
# If price breaks the 4-bar 3m channel low (LONG) / high (SHORT) → CLOSE NOW.
# Bypasses NO_LOSS, hedge, MTF. Fires desktop alert naming entry signal.
# Winners exempt: max_gain > 0.5% means trailing/structure exits handle it.
# ═══════════════════════════════════════════════════════════════════════════
if position and abs(safe_float(getattr(position, 'positionAmt', 0))) > 0 and \
   bool(getattr(config, 'R1_DC_LOW4_3M_EMERGENCY_ENABLED', True)):
    try:
        _r1_max_gain = safe_fetch_float(getattr(position, 'max_gain', 0), 0)
        _r1_winner_thresh = float(getattr(config, 'R1_WINNER_EXEMPT_MAX_GAIN_PCT', 0.5))
        if _r1_max_gain <= _r1_winner_thresh:
            _r1_ind = await ii(trade_manager, symbol)
            if _r1_ind:
                _r1_dc_low4_3m = safe_fetch_float(_r1_ind.get('dc_low4_3m'), 0)
                _r1_dc_high4_3m = safe_fetch_float(_r1_ind.get('dc_high4_3m'), 0)
                _r1_is_long = (position_side == 'LONG')
                _r1_breached = (
                    (_r1_is_long and _r1_dc_low4_3m > 0 and current_price <= _r1_dc_low4_3m) or
                    ((not _r1_is_long) and _r1_dc_high4_3m > 0 and current_price >= _r1_dc_high4_3m)
                )
                if _r1_breached:
                    _r1_amt = abs(safe_float(getattr(position, 'positionAmt', 0)))
                    _r1_close_side = 'SELL' if _r1_is_long else 'BUY'
                    _r1_gain = safe_fetch_float(getattr(position, 'gain', 0), 0)
                    _r1_entry_sig = getattr(position, 'last_signal', '?') or getattr(position, 'open_reason', '?')
                    _r1_entry_ts = str(getattr(position, 'opened_at', ''))
                    _r1_level = _r1_dc_low4_3m if _r1_is_long else _r1_dc_high4_3m
                    logger.error(
                        f"⛔ [R1_DC_LOW4_3M_EMERGENCY] {position_key}: g={_r1_gain:.2f}% max_g={_r1_max_gain:.2f}% "
                        f"price={current_price:.6f} {'<=' if _r1_is_long else '>='} dc4_3m={_r1_level:.6f} "
                        f"entry={_r1_entry_sig} → CLOSE (skip NO_LOSS, hedge, MTF)"
                    )
                    try:
                        import ez_alert
                        ez_alert.alert_bad_exit(
                            account=account_key, position_key=position_key, gain=_r1_gain,
                            reason="R1_DC_LOW4_3M_EMERGENCY", entry_signal=str(_r1_entry_sig),
                            entry_ts=_r1_entry_ts, current_price=current_price,
                        )
                    except Exception as _r1_alert_err:
                        logger.warning(f"[R1_ALERT_ERR] {position_key}: {_r1_alert_err}")
                    try:
                        await trade_manager.execute_now(
                            position_key=position_key, account_key=account_key, symbol=symbol,
                            original_positionAmt=_r1_amt, side=_r1_close_side, position_side=position_side,
                            quantity=_r1_amt, old_price=current_price,
                            unique_id=f"R1_DC_LOW4_3M_{int(time.time())}",
                            reason=f"R1_DC_LOW4_3M_EMERGENCY_g{_r1_gain:.2f}_entry_{_r1_entry_sig[:30]}",
                            is_full_close=True, action='CLOSE')
                        trade_manager.processing_keys.discard(position_key)
                        return f"{EvalStatus.ACTION_TAKEN}:R1_DC_LOW4_3M_EMERGENCY_CLOSED"
                    except Exception as _r1_err:
                        logger.error(f"⛔ [R1_DC_LOW4_3M_ERR] {position_key}: {_r1_err}")
    except Exception as _r1_outer:
        logger.debug(f"[R1_DC_LOW4_3M] {position_key} probe err: {_r1_outer}")
```

### B.3 `config.py` — add R1 toggles

```python
# R1 — dc_low4_3m emergency close (USER 2026-05-09)
R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = True
R1_WINNER_EXEMPT_MAX_GAIN_PCT: float = 0.5  # if max_gain ever exceeded this, R1 deactivates (winner protection)
```

### B.4 `tradier_manage.py` — mirror R1 in `evaluate_stop` BEFORE NOLOSS_BB1H_GATE

(Tradier 5m TF — uses `dc_low4_5m` / `dc_high4_5m` instead of 3m. Need to verify field exists in NPZ; if not, fall back to `dc_low_5m` / `dc_high_5m`.)

### B.5 `config_tradier.py` — same toggles

### B.6 Backtest sync: `backtest_v8_engine.py` — auto-fires because `process_position_enter` is the engine's exit pipeline, but we need to ensure `dc_low4_3m` is in the NPZ-driven indicator dict (verified at line 1689-1719 — yes, ind dict comes from `store.build_indicator_dict(idx)`).

---

## C. R2 — WT_15M_VEL_SLOW TUNING (already implemented, just tighten)

### C.1 `config.py` — adjust thresholds

```diff
- WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.10
+ WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.50  # USER 2026-05-09: widened — fires near breakeven, not just <0.10%
+ WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.01  # USER 2026-05-09: only close if gain ≥ +0.01% (positive net of commissions)
```

`WT_15M_VEL_NEAR_ZERO_THRESHOLD: float = 0.1` already correct, no change.

### C.2 `ez_manage.py:20715` — add lower bound

```diff
- if _wzg_gain < _wzg_band:
+ _wzg_floor = float(getattr(config, 'WT_15M_VEL_SLOW_GAIN_FLOOR_PCT', 0.01))
+ if _wzg_floor <= _wzg_gain < _wzg_band:
```

This makes R2 fire ONLY when gain in `[+0.01%, +0.50%]` AND momentum dying — captures "approaching 0 from above, exit at small positive" intent.

### C.3 Tradier mirror — add equivalent R2 block in `tradier_manage.py` (currently absent). Use 5m WT velocity if 15m unavailable; field name is `wt_velocity_15m` either way per indicator dict.

### C.4 Backtest auto-syncs (same code path).

---

## D. TRADIER PARALLEL WORK

| Change | File | Status |
|---|---|---|
| NO_LOSS purge configs | `config_tradier.py` | A.2 above |
| R1 mirror | `tradier_manage.py` | B.4 (5m TF instead of 3m) |
| R2 mirror | `tradier_manage.py` | C.3 |
| Audit trade frequency | `data/history/trb,trc` | Run grep+jq on JSONL |

---

## E. SMOKE TEST PLAN

After all edits + md5-sync to S1:

```bash
# Crypto: 1 sym × 30 days
python3 backtest_v8_engine.py --symbols BTCUSDC --start 2026-04-09 --end 2026-05-09 --account ang
# Tradier: 1 sym × 30 days
python3 backtest_v8_engine.py --mode tradier --symbols AAPL --start 2026-04-09 --end 2026-05-09 --account trb
```

**Pass criteria**:
- Trade count: 60-240 over 30 days = 2-8/day ✓
- No "open bar N → close bar N+1" pattern visible in trade timestamps
- R1 fires logged in alert JSONL
- pool_sharpe > 0 (anything positive given R1+R2 cut bad trades early)

**Abort criteria**:
- Trades = 0 → cooldowns blocking everything → revert
- Trades > 1000 → R1 not gating winners → check `max_gain` field

---

## F. UNLOCK + BACKUP + SYNC PROTOCOL

For each file edited:

1. Backup: `cp <f> backups/before_exit_rewrite_202605091940.<f>`
2. Edit local
3. Compile: `python -c "import py_compile; py_compile.compile('<f>', doraise=True)"`
4. md5 record: `md5 <f>` written to commit log
5. rsync to S1: `rsync -az --existing --update <f> s1-int:/home/niels/binance-sandbox/`
6. Verify md5 match S1

**Files to unlock for this session** (user must acknowledge):
- `ez_manage.py` (R1 block + A.1 noloss neutralization references)
- `ez_positions_quick.py` (A.1 noloss-gate-reading sites — neutralized via config flip, no code changes needed strictly speaking, but worth audit)
- `config.py` (A.1)
- `tradier_manage.py` (R1 + R2 mirror)
- `config_tradier.py` (A.2)

`ez_positions_quick.py` may stay locked if A.1 config flips suffice. We'll skip unless an `is_strict_no_loss_account` lookup actually needs deletion.

---

## G. RISK ACCEPTANCE CHECKLIST

- [ ] User confirms NO_LOSS gates flipping OFF is intended (positions WILL close at loss now).
- [ ] User confirms R1 always-on (winners exempt @ max_gain>0.5%) is correct interpretation.
- [ ] User confirms R2 band 0.50% / floor 0.01% / EXIT_AT current price is correct.
- [ ] User confirms desktop osascript alerts are wanted (will pop notifications continuously if R1 fires often).
- [ ] User confirms tradier mirror this session (vs next).
- [ ] User accepts smoke-test gating before any S1 sync of live-trading scripts.

---

## H. ORDER OF OPERATIONS (when user says "go")

1. `mkdir -p data/sweep_alerts`
2. Write `ez_alert.py` (new, no lock)
3. Edit `config.py` (unlock, backup, A.1, B.3, C.1, lock)
4. Edit `config_tradier.py` (unlock, backup, A.2, lock)
5. Edit `ez_manage.py` (unlock, backup, B.2 + C.2, lock)
6. Edit `tradier_manage.py` (unlock, backup, B.4 + C.3, lock)
7. Compile-check all 4
8. rsync to S1
9. md5 verify all 4 match across MB+S1
10. Smoke-test BTC × 30 days on S1, watch trade count
11. If pass → smoke-test AAPL × 30 days
12. Report trade-count distribution, R1/R2 fire counts, sample alert JSONL records
13. Add memory entry summarizing the rewrite

---

**This is the proposal. No code edited yet. Reply "go" to apply, or redirect.**
