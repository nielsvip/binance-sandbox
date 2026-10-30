# Forward state vs. recomputed chart: gain discrepancy investigation

Date of investigation: 2026-10-29 (evidence timestamps are UTC, 2026-10-29 unless noted).
Status: **OPEN. Root cause not found.** The discrepancy is not explained by sizing.

## 1. The question

The daily 30D charts (`tools/daily_forward_charts.py`) are meant to show the trades that the
forward vector state on S1 (crypto) and S5 (stocks) produces for each sym_side, over the 30D
window ending at the 20:00Z close. The first verification pass showed that the recomputed trades
and gain do not match the published forward state (`data/forward_state/<SYM_SIDE>.json`).

The requirement is: the same switches on the same data must give the same trades and gain.
The only permitted difference is sizing.

## 2. Reproduction (the numbers)

| sym_side | host | published state (ts, tail) | published trades / gain | recomputed now (same tail) |
|---|---|---|---|---|
| AAPL_LONG | S5 | 20:04:23Z, tail 1793303100 (bar open 19:45Z) | 39 / **3.8061%** | 39 / **0.9372%** |
| BTCUSDC_LONG | S1 | 20:33:53Z, tail 1793304900 (bar open 20:15Z) | 16 / **8.0666%** | 19 round trips (38 ledger rows) / **5.6288%** |

- AAPL: trade count and the open position agree. The engine says the position was entered at bar
  1298 (open 13:30Z) at 349.18, which matches the state's `engine_open_px` 349.18.
- BTCUSDC: trade count differs (16 vs 19), gain differs.
- Chart header for AAPL (`HEADER_VERIFIED`): gain 0.937229, trades 39, pnl $31.14, peak $3322.2.

Note on counts: the forward path returns `trades` (round trips) and a `ledger` that also includes
REDUCE/AUGMENT events. Earlier comparisons used `len(ledger)` by mistake (102 for AAPL, 38 for
BTC). That is fixed; the table above uses round trips.

## 3. How the forward state is computed (the code path)

`tools/v15_forward_state.py::state_for(symside)`:

1. `_load_overrides(symside)` reads the ACTIVE published set from
   `data/hourly_reconfig/per_sym_store.db` (`per_sym_active.full_config_json`), applies
   `per_sym_live_set._apply_caps`.
2. `_drop_retired(ov)` pops retired keys (in place).
3. `_stale_anchor(symside)` adds `STALE_SET_RECALC_EPOCH` / `STALE_SET_APPLY_EPOCH`.
4. Reads NPZ under `merged_npz_reads(paths)` (zip plus `.tail` sidecar), after `_E._STORES.clear()`.
5. `prepare_batch` (tools/opt/v12_pilot) -> `evaluate_v12.prepare` -> `_store` -> `_exact_30d_slice`
   -> `_compact_to_15m`.
6. `evaluate_prepared_sanitized(prep, ov, 30, include_ledger=True)`.

Process context: the stock states are written by `s5-fwdstate-watch.service`
(`tools/fwdstate_npz_watch.py`, long-lived, `Restart=always`, PER_SYM_STORE_DB set). Crypto states
on S1 run from the same watcher family. The watcher restarts its process pool when "forward code
changed" (`tools/fwdstate_npz_watch.py:175`); last S5 restart before the 20:04 state was 17:56:48Z.

## 4. What was tested and ruled out

All results below come from `tools/daily_forward_charts.py` or ad-hoc probes calling the same
functions, run on S5 for AAPL_LONG with the tail fixed at 1793303100 unless noted.

| hypothesis | test | result |
|---|---|---|
| Sizing via START_POSITION_SIZE | override 250 / 500 / 600 / 900 | gain 0.9372 for all; **no effect** |
| Sizing via market-mode caps | override LIGHT / NORMAL / EXTREME / as-is cap tables (START, MAX_ORDER_VALUE, MAX_POSITION_SIZE) | gain 0.9372 for all; **no effect** |
| Market mode class default | `TradierConfig._CURRENT_MARKET_MODE` = NORMAL / EXTREME / LIGHT | gain 0.9372 for all |
| Eval cache (`V15_EVAL_CACHE`) | code read | bypassed when `include_ledger=True`; both paths use include_ledger=True. Ruled out. |
| Setup memo (`V12_SETUP_MEMO`) | `V12_SETUP_MEMO=0` vs default | 0.9372 both |
| Stale anchors / retired keys | with vs without | 0.9372 both |
| Settings hash | `settings_sha256` now vs state | identical (25cc5845...) |
| Template md5 | state vs now | identical (9d856f...) |
| Engine AST hash | state vs now | identical (1d961758...) |
| defaults_round | state vs now | identical (run35-...) |
| Stale anchor values | state vs now | identical |
| NPZ load start date | `_frozen_npz_start_date` | same calendar date for cut and uncut end; no effect |
| Cut position (last 25 bars) | scan tail k=0..25 | trades 39 at every cut; gain 0.83–1.15%. **No cut gives 3.8%.** |
| Calling state_for itself under the cut | `state_for("AAPL_LONG")` with `_store` cut at 19:45 | 39 / 0.9372. Same as chart path. |
| NPZ modified after state | mtime of `AAPL.npz` = 20:02:20Z; state 20:04:23Z | file is older than the state; not a post-state rewrite |
| BTC NPZ modified after state | mtime 20:19:32Z; state 20:33:53Z | same; not a post-state rewrite |
| Template is stale in watcher pool | last pool restart 17:56:48Z; template rewrite 11:16:50Z (TEMPLATE_FINAL_NORM) | pool started after rewrite; ruled out for the 20:04 state |

## 5. Open lead (not yet verified)

**The AAPL NPZ was rewritten at 20:02:20Z, between the watcher's 20:00:08Z and 20:04:23Z AAPL_LONG
computations.** Evidence:

- `/tmp/fwdstate_npz_watch.log`: `[2026-10-29T20:00:08Z] AAPL_LONG ok=True tail=1793303100 ... class=0`
  and `[2026-10-29T20:04:23Z] AAPL_LONG ok=True tail=1793303100 ... class=3`. Both tail 19:45 bar,
  so the 20:00 and 20:04 computations saw the same tail but possibly different file bytes.
- `backtest_v8/indicators/AAPL.npz` mtime 2026-10-29T20:02:20Z. Written via atomic `.tmp` rename
  (`.AAPL.npz.<pid>.tmp.npz` pattern), i.e. by a writer, not a read.
- `/tmp/npz_quarter.log` `npz_bar_refresh 20:01:37/38Z` for AAPL: `SOURCE_NO_NEW_BAR`, `rows_before`
  42359 / 42410. These lines do not show what the 20:02:20 write was.

Implication: the published 20:04 state may have been computed on bytes that differ from the bytes I
read today in ways that do not change the tail stamp (e.g. earlier bars or indicator columns
rewritten). That would explain why the tail matches and the trade count matches but gain does not.
This is **not yet verified**: no pre-20:02 copy of `AAPL.npz` exists on S5.

Also unverified: whether a `.tail` sidecar existed at 20:04 (the merged view reads it). None exists
for AAPL now (`AAPL.npz.tail` absent).

## 6. What this does and does not establish

- Established: with the same code, settings, template, engine hash and tail, today's read of the
  NPZ gives 0.9372%, not 3.806%. Sizing keys do not change the gain on this path.
- Established: `state_for()` is reproducible today on the current bytes, so the chart path is
  faithful to the code.
- Not established: whether the forward trader (vector_direct on S1/S5) acted on 3.806% or 0.937%
  data. `data/vector_direct/` does not exist on either host, so there are no broker fills to
  compare against.
- Consequence: a forward state cannot be rebuilt from storage after the fact. Any claim that a
  chart equals "the trades executed" needs the state-time NPZ bytes preserved, or the state's own
  per-trade ledger persisted.

## 7. Suggestions (in priority order)

1. **Persist the ledger with every forward state.** `state_for` already computes `res["ledger"]`
   but only writes aggregates (`gain_pct`, `trades`) and the last acts to
   `data/forward_state/<SYM_SIDE>.json`. Write the full round-trip ledger and the NPZ identity
   (path, mtime, size, sha256 of the timestamps array and of the close array at the tail) into the
   state file (or a per-bar `data/forward_state_history/` file). Without this the discrepancy cannot
   be measured after the fact.
2. **Record the NPZ identity used by each state.** Add `npz_sha256` (or the file mtime + size) and
   the tail bar index to the state. Then "same data" becomes checkable.
3. **Find the 20:02:20Z writer of `AAPL.npz`.** Identify the process that renamed
   `.AAPL.npz.*.tmp.npz` into place at 20:02:20 and what it changed (diff the timestamps and
   indicator columns against a copy, if one exists, or re-derive). Check whether the same writer
   runs for other stock sym_sides and crypto (`BTCUSDC.npz` was written 20:19:32).
4. **Make the verify reproducible from logs.** Add per-sym_side logging of
   `(tail, file sha256, gain, trades)` on every state computation, so two computations at the same
   tail with different results are visible immediately.
5. **Re-run `--verify` across all sym_sides** once (1) is in place, to see whether AAPL is a
   one-off or systematic. I have only checked AAPL_LONG and BTCUSDC_LONG.
6. **Do not change the chart until the state-time data is known.** The chart should be generated
   from the persisted ledger (suggestion 1), not re-simulated from today's NPZ, if the goal is
   "the trades the forward scripts executed".

## 8. Changes made during the investigation (for the record)

- `tools/opt/hires_chart.py`: added optional `heading` and `out_dir` parameters to
  `generate_hires` (defaults unchanged). Backups:
  `backups/before_daily_forward_charts_202610292016.py` (Mac) and
  `backups/before_daily_forward_charts_*.py` on S1 and S5. Deployed to S1 and S5 with matching md5
  (`23996c26307f8385ebc9b4d5dff37536`).
- `tools/daily_forward_charts.py`: new. Deployed to S1 and S5 (md5
  `720c285eefa0df382050b2cd35864aa9` before the last small fix; S1/S5 copies were re-streamed after
  that fix, latest Mac md5 printed in the session). Do not treat that md5 as final without
  re-checking.
- Outputs (not forward truth; recomputed from today's NPZ):
  - S5: `data/reports/charts_daily_30d/` 295 stock charts + `manifest.json`, cut 2026-10-29T20:00Z.
  - S1: `data/reports/charts_daily_30d/` 240 crypto charts + `manifest.json`, cut 2026-10-29T20:00Z.
  - The 6 stock and 11 crypto failures are variant names with no base NPZ (listed in the manifests).
- Probes: temporary files on S5 and S1 under `/tmp` were removed. No forward state, NPZ, template
  or per_sym store was modified.
- No locked files were unlocked. `v12_quick_engine.py` (uchg) was not touched.

## 9. Reproduction commands

Mac repo: `/Users/niels/Documents/binance`. Hosts: `ssh -J gateway s5` (stocks), `ssh s1-int` (crypto).

```bash
# S5, verify one stock against its forward state (tail = state's npz_tail)
cd ~/binance-sandbox && .venv/bin/python -u tools/daily_forward_charts.py --venue stock --verify --only AAPL_LONG --out /tmp/verify_aapl

# S1, verify one crypto
cd ~/binance-sandbox && python3 -u tools/daily_forward_charts.py --venue crypto --verify --only BTCUSDC_LONG --out /tmp/verify_btc

# state_for itself under the 19:45 cut (the decisive probe; see section 4)
# (probe script: scratchpad aapl_probe.py; installs tools.daily_forward_charts._install_cut then calls state_for)
```

The probe script is in the session scratchpad and is not in the repo. Move it into `tools/` if this
investigation continues, so the test is repeatable.
