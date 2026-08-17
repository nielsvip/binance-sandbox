# Stock matrix red-cell repair — 2026-08-01

## Scope and rule

This repair changes wiring only. It does not create, infer, overwrite, or
promote any P&L cell. A setting is eligible for a numerical matrix result only
after a new causal run has a finite nonzero side-aware delta, a distinct action
fingerprint, and real activity. Equal or inactive observations remain
quarantined.

## Reconnected paths

| Matrix field | Repair | Safe semantics |
|---|---|---|
| `BB_PULLBACK_GATE_LONG_MAX` | Per-key `_cfg` values now reach the shared raw-indicator BB entry gate. | LONG blocks above its configured %B boundary; SHORT remains the mirrored `SHORT_MIN` rule; absent data fails open. |
| `DELTA_EXIT_DC_FLOOR` | Per-key switch now gates the existing Delta decision. | Donchian 15m break **confirms** an already-valid Delta exit; it never triggers a standalone floor/bottom stop. Missing DC data fails open. |
| `EXIT_GAIN_EROSION_ENABLED` | Explicit path switch now gates the pre-existing peak-giveback implementation. | Turning it off genuinely disables that path; it does not alter the independent master. |
| `EXIT_BOUNCE_TOP_ENABLED` | Replaced hardcoded `False` with the per-key switch. | Default remains off. The retained path is technical bounce/turn plus mandatory reentry, not a raw DC floor stop. |
| `EXIT_TREND_REVERSAL_ENABLED` | Added a pure confirmed-reversal predicate. | Profit-only; requires opposing 1h/4h WT, a 1h structural break, then a 5m recovery and renewed adverse turn—so it exits at the recovery/lower-top, not the first break. |

`tools/build_matrix_interdependency_manual.py` and
`tools/audit_switch_matrix_uniqueness.py` now report all five as direct,
per-symbol live decision reads. This changes their status from red/reconnect
to `NOT_CURRENTLY_RED`; it is not proof that every value has fired on every
symbol.

## MSTR_SHORT

`MSTR_SHORT` is configured in `symbols_trb_short.json`, but its required
historical data is absent locally: no `backtest_v8/indicators/MSTR.npz`, no
current matrix NPZ, and no 5m/15m/1h/4h/D raw-klines cache. Therefore there is
no valid vector or V8 result to add. Missing-data cells must remain blank/red,
not receive a proxy number.

The reproducible recovery sequence after raw klines are restored is:

```bash
python3 backtest_v8_precompute_tradier.py --only MSTR --output-dir backtest_v8 --start 2024-01-01 --workers 1
EZ_LOG_DIR="$PWD/data/_scratch_v8_logs" TRADIER_API_LOG_DIR="$PWD/data/_scratch_v8_logs" \
  python3 backtest_v8_sweep.py --mode tradier --account trb --symbols MSTR \
  --start 2024-01-01 --capital 16000 --tier configs_from_file \
  --configs-file <MSTR-config.json> --workers 1
```

`MTF_ARMED_ENTRY_ENABLED` is not a standalone MSTR short test while
`MTF_ARMED_ENTRY_SKIP_SHORT=True`; its cell is an N/A/control until a paired
test toggles `MTF_ARMED_ENTRY_SKIP_SHORT=False`.

## Verification

```text
python3 -m py_compile tradier_matrix_gates.py tradier_manage.py
pytest -q test_tradier_matrix_gates.py
# 3 passed
```

The broad authority audit still reports 32 pre-existing missing manifest/path
rows unrelated to these five fields; that is a separate contract-inventory
repair and must not be disguised as a successful matrix run.
