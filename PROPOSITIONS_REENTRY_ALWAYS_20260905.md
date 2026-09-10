# PROPOSITION — Make Reentries ALWAYS Respected — 2026-09-05

**Status:** PROPOSAL ONLY — NO FILE EDITED. Requires explicit `unlock ez_manage.py` in same message to apply. All edits are additive bypasses (not restores), with `cp <file> backups/before_reentry_always_<ts>.py` before edit and `py_compile` + `grep` verify after.

**Goal:** User mandate `reentries are ALWAYS respected` — no gate may silently drop a REENTRY. Reentry = flat position (`positionAmt==0`) re-open after close (via `data/reentry_queue` or `REENTRY_MONITOR`).

**Current behavior audit (HEAD `9824875c`, ez_manage.py 54646 lines):**

- 3 gates **currently block REENTRY** and violate ALWAYS:
  1. `PER_SYM_SIDE_DISABLED` (line 26715) — `("OPEN" in _kill_act or "AUGMENT" in _kill_act or "ENTRY" in _kill_act or "REENTRY" in _kill_act)` → blocks REENTRY when `LONG_ENABLED/SHORT_ENABLED=False` (no positive per_sym backtest). Example live: `05 00:52:49 BLOCKED_PER_SYM_SIDE_DISABLED_SHORT flz:BTCDOMUSDT_SHORT` (OPEN today; same path would block REENTRY).
  2. `STRICT_VEC_PARITY` (line 26745) — `_vp_is_entry = ("OPEN" in _kill_act or "AUGMENT" in _kill_act or "ENTRY" in _kill_act or "REENTRY" in _kill_act)` → blocks REENTRY when `STRICT_VEC_PARITY_MODE=True` and reason not vec-achievable. Currently `False` (shadow), but would block if ever enabled.
  3. `OPEN_RATE_BREAKER` (line 26980) — `_orb_is_open = (("OPEN" in _kill_act or "ENTRY" in _kill_act or "REENTRY" in _kill_act) and ...)` → counts REENTRY toward 15/60s flood cap and can return `BLOCKED_OPEN_RATE_BREAKER`.

- Gates **already correctly exempt** REENTRY (no change needed):
  - `BALANCE_FLOOR_HALT` (26600: only OPEN/AUGMENT/ENTRY/BUY)
  - `COUNTER_TREND_ADD_BLOCK` (26621: exempts REENTRY/PRICE_CROSS/OBLIGATORY)
  - `GR_FILTER_ALL_ENTRIES` (26771: exempts REENTRY/AUGMENT)
  - `MTF_ARMED_ENTRY` (26940: only OPEN/AUGMENT/ENTRY — REENTRY not gated; plus `_guar_ra_en` bypass for REENTRY, cold-start suppressed only for non-REENTRY)
  - `OVERTRADE_GUARD` (27083: only OPEN/AUGMENT/ENTRY — REENTRY exempt)
  - `UAGAIN` (28273: only blocks when `positionAmt > min_qty`; flat REENTRY `Amt==0` → pass. Keep as-is but add explicit REENTRY early-return for safety)
  - `INF_DEDICATED_WINNERS` (27003: only OPEN/AUGMENT/ENTRY)

---

## Proposed Changes (3 edits, all in ez_manage.py `execute_now`)

### P1 — PER_SYM_SIDE_DISABLED (26715) — exempt REENTRY

**Current:**
```python
        try:
            if (
                symbol
                and ("OPEN" in _kill_act or "AUGMENT" in _kill_act or "ENTRY" in _kill_act or "REENTRY" in _kill_act)
                and "CLOSE" not in _kill_act
                and "REDUCE" not in _kill_act
                and "HEDGE" not in _kill_act
                and "HEDGE" not in (reason or "").upper()
            ):
```

**Proposed:**
```python
        try:
            if (
                symbol
                and ("OPEN" in _kill_act or "AUGMENT" in _kill_act or "ENTRY" in _kill_act)
                and "REENTRY" not in _kill_act and "REENTRY" not in (reason or "").upper()
                and "CLOSE" not in _kill_act
                and "REDUCE" not in _kill_act
                and "HEDGE" not in _kill_act
                and "HEDGE" not in (reason or "").upper()
            ):
```
*Rationale:* Per-sym book disables unproven sides for fresh entries; a REENTRY is a *continuation* of a proven exit cycle and must not be blocked. Comment update: `# PER_SYM_SIDE_DISABLED — REENTRY exempt per ALWAYS mandate`.

### P2 — STRICT_VEC_PARITY (26745) — exempt REENTRY from entry gate

**Current:**
```python
                _vp_is_entry = ("OPEN" in _kill_act or "AUGMENT" in _kill_act or "ENTRY" in _kill_act or "REENTRY" in _kill_act) and "CLOSE" not in _kill_act and "REDUCE" not in _kill_act
```

**Proposed:**
```python
                _vp_is_entry = ("OPEN" in _kill_act or "AUGMENT" in _kill_act or "ENTRY" in _kill_act) and "REENTRY" not in _kill_act and "REENTRY" not in (reason or "").upper() and "CLOSE" not in _kill_act and "REDUCE" not in _kill_act
```
*Rationale:* Vec parity is for *new* entries; REENTRY is guaranteed and must bypass even if reason not in vec allowlist. Shadow logging remains for observability.

### P3 — OPEN_RATE_BREAKER (26980) — do not count REENTRY toward flood cap

**Current:**
```python
            _orb_is_open = (("OPEN" in _kill_act or "ENTRY" in _kill_act or "REENTRY" in _kill_act)
                            and "REDUCE" not in _kill_act and "CLOSE" not in _kill_act and "HEDGE" not in _kill_act)
```

**Proposed:**
```python
            _orb_is_open = (("OPEN" in _kill_act or "ENTRY" in _kill_act)
                            and "REENTRY" not in _kill_act and "REENTRY" not in (reason or "").upper()
                            and "REDUCE" not in _kill_act and "CLOSE" not in _kill_act and "HEDGE" not in _kill_act)
```
*Rationale:* Flood breaker protects against cold-start OPEN spam (19/account in seconds). REENTRY is vetted by `REENTRY_COOLDOWN_S=300` and `REENTRY_NEVER_SKIP` — must never be throttled by open-rate.

### P4 — UAGAIN safety (28273) — explicit REENTRY early-return (optional, not strictly needed)

**Current:** `if (not _is_reduce and position_key and bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True))):` then checks `if _uag_amt > _uag_min_qty`

**Proposed add at 28274:**
```python
            # REENTRY always exempt — flat re-open has no gain to gate
            if "REENTRY" in (action or "").upper() or "REENTRY" in (reason or "").upper():
                pass  # skip UAGAIN — fall through to no-block
            elif _uag_position is not None:
```
*Rationale:* Flat REENTRY already passes via `Amt==0`, but explicit makes intent clear and covers race where positionAmt not yet synced flat.

---

## Verification Plan (after unlock + edit)

1. `cp ez_manage.py backups/before_reentry_always_20260905.py`
2. Apply P1-P3 (P4 optional) via `muse.edit_file` with exact `find` strings above.
3. `python3 -c "import py_compile; py_compile.compile('ez_manage.py', doraise=True)"`
4. `grep -n "REENTRY.*_kill_act\|REENTRY.*reason" ez_manage.py` — verify 3 sites now exempt.
5. Restart: `pkill -9 -f "python.*ez_manage.py"; sleep 3; bash start_everything_2.command` (watchdog respawn)
6. Tail 10m: `tail -F logs/ez_manage_flz.log | grep -E "REENTRY|BLOCKED_PER_SYM|BLOCKED_OPEN_RATE"` — expect 0 new `BLOCKED_*` for REENTRY, reentry levels continue `🎯 REENTRY LEVELS (80 total)`.

## Apply Command

User: send `unlock ez_manage.py` in same message as approval, then agent will apply P1-P3 (P4 optional) immediately.

No file was modified to create this proposal.
