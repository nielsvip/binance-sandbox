# Vector versus faithful-engine ledger parity — 2026-07-25

## Verdict

The cost-aligned scanner result is correct:

- Vector scanner: **+1377.8723989410419%**
- Hardened faithful-engine route: **+1377.8723989410414%**
- Difference: effectively zero, **-4.55e-11 basis points**
- Engine dollar P&L: **$27,557.44797882083** on the $2,000 deployed unit
- Final equity multiple: **14.7787239894104**

No scanner accounting fix is required.

The earlier faithful-engine result of **+1376.653993412358%** was not an
authoritative replay of the new accounting semantics. Its event times and fill
prices were valid, but its entry quantities were frozen from the old vector
schedule's cached equity fields. The hardened adapter now sizes each entry from
realized, acknowledged engine equity.

## Artifacts

New vector artifact:

```text
/home/niels/binance-sandbox/data/reports/vec_research/
top_exit_20260725T191201Z_MU_LONG
```

Hardened exact-engine PASS:

```text
/home/niels/binance-sandbox/data/reports/vec_research/
v8_exact_replay_20260725T191214Z_MU_LONG
```

Old pre-hardening engine ledger:

```text
/home/niels/binance-sandbox/data/reports/vec_research/
v8_exact_replay_20260725T184018Z_MU_LONG
```

Old vector source schedule:

```text
/home/niels/binance-sandbox/data/reports/vec_research/
top_exit_20260725T182601Z_MU_LONG
```

All paths are `VEC_RESEARCH`; no matrix, live config, or live state was written.

## What was identical

The old and regenerated schedules both contain:

- 30 actions;
- 1 seed entry;
- 15 E02 exits;
- 14 re-entries;
- 11 E11 lower-price re-entries;
- 3 E10 mandatory reclaim re-entries;
- no final open position.

After removing only `equity_after_fill`, their canonical event JSON has the
same SHA-256:

```text
4982487e1f0b4f47e364886518ffd45067d5be7c4d32fc34dbdf13fe25158dc5
```

The type/timestamp/fill-price/reason stream also has the same SHA-256:

```text
018562bddf5c1396fe6b80572608ab65286ce516272d4b0db38c8180cbd9fc1e
```

Therefore the 121.8406 bp discrepancy was not caused by:

- changed E02 signals;
- changed E10/E11 signals;
- changed timestamps;
- changed next-RTH-open fills;
- changed slippage;
- changed trade count.

## Root cause: old frozen quantities

The old adapter precomputed each re-entry quantity as:

```text
quantity = $2,000 * old_vector_equity_after_previous_exit / entry_fill
```

Those cached vector equity values came from the old two-debit commission
model. The faithful engine then executed those frozen quantities while charging
its own 0.10% round-trip fee at close. That mixed two accounting regimes.

The hardened adapter instead uses:

```text
quantity = realized_acknowledged_engine_equity / actual_entry_fill
```

and updates realized equity after every acknowledged close:

```text
gross_pnl = (exit_fill - entry_fill) * quantity
fee       = 0.001 * entry_fill * quantity
equity   += gross_pnl - fee
```

This is the same dollar ledger represented by the scanner:

```text
close_equity =
    entry_equity * (1 + price_return)
    - entry_equity * 0.001
```

The first seed quantity was identical. Re-entry quantities then drifted because
the old schedule was no longer sizing from engine-realized equity. Examples:

| Re-entry fill | Old frozen qty | Ack-driven qty | Difference |
|---:|---:|---:|---:|
| $108.7417 | 17.870776 | 17.870268 | -0.0028% |
| $109.1318 | 33.116285 | 33.129813 | +0.0409% |
| $210.4571 | 30.991461 | 31.017272 | +0.0833% |
| $451.7003 | 26.246365 | 26.280061 | +0.1284% |
| $843.1136 | 34.641142 | 34.701764 | +0.1750% |

Across the complete ledger:

```text
old frozen-quantity P&L       $27,533.07986824716
ack-driven realized P&L       $27,557.44797882083
difference                        $24.36811057367
gain difference                    1.21840552868 percentage points
gain difference                  121.8405528683 basis points
```

The size difference is exactly the previously unexplained gap.

## Hardened engine proof

The exact-engine replay passed every route audit:

- return code: 0;
- scheduled actions: 30;
- executed actions: 30;
- refused actions: 0;
- missing actions: 0;
- price mismatches: 0;
- quantity mismatches: 0;
- runtime flat at end: yes;
- real closes: 15 expected, 15 actual;
- time in market: **75.05063484112638%** expected and actual;
- accounting expected: **+1377.8723989410419%**;
- accounting actual: **+1377.8723989410414%**;
- accounting status: PASS.

Fingerprints from the passing audit:

```text
NPZ
f57ce885f72655026e594ce93fe883f61c20eab647568ee91ee5d7a4e9973fb3

event schedule
30a9cac27e3fd3cc7526178331c1a1e777e979505db7ab3965d40620d133a518

backtest_v8_engine.py
077ae8763dca2b220742cb8a46bc4a6c795deddf86b204f6f0e06d9be46d7a8a

tradier_manage.py
228a1bbce07358f227c667538e27ff867b73371ca3dedd801c9b1ae78e398c7e
```

This proves execution and accounting parity of the frozen schedule. It still
does not independently prove signal parity, and the artifact remains
non-promotable until E02/E10/E11 signals are independently recomputed by the
faithful route.

## Regression coverage

The focused suite now includes an oracle that does not call the Python vector
reference replay. It manually reproduces the engine's dollar ledger across an
exit and re-entry, including:

- full-equity acknowledged sizing;
- next-bar reclaim fill;
- gross price P&L;
- 0.10% fee on entry notional at close.

Focused result:

```text
19 passed
```

The scanner, Python campaign, adapter, and engine now agree on the authoritative
ack-driven accounting contract.
