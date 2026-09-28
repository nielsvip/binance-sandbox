# V15 ASSURE — Completion-Assurance Framework for v15_pilot Sheets (2026-09-28)

## 1. The clear picture — two trading systems, one vector engine, four templates

| | CRYPTO | STOCKS |
|---|---|---|
| Live manager | `ez_manage.py` (`process_position`, `check_entry/exit_candidates`, `execute_now` gate) | `tradier_manage.py` (same shape, Tradier venue) |
| Config / switches | `config.py` | `config_tradier.py` |
| Templates | `TEMPLATE_CRYPTO_LONG.xlsx` / `TEMPLATE_CRYPTO_SHORT.xlsx` | `TEMPLATE_STOCKS_LONG.xlsx` / `TEMPLATE_STOCKS_SHORT.xlsx` |
| Worker | s1 (crypto NPZs) | s2 (stock NPZs) |

Vectorized equivalent for BOTH: `v12_quick_engine.py` (`QuickConfig` holds every wired
switch; LOCKED, imports all 79 `vec_decisions` modules). Access path used by the pilot
and by this framework:

```
tools/opt/v12_pilot.prepare_batch(symside, 30)            # NPZ+base_cfg once, ~1s
tools/opt/v12_pilot.evaluate_prepared_sanitized(prep, ov) # one candidate eval
```

Slow live-faithful verifier (H/I columns only, never for F/G/yellows):
`backtest_v12_engine.py` (~30s, calls real `process_position`).

Template anatomy (all 4 templates identical shape): 13 `SWITCH_SHEETS` tabs +
`*_BASELINE_METRICS`. Headers on **row 2**; data rows from 3. Per data row:
A=switch, B=candidate value, C=promoted override string, E=cumulative_before
(greedy chain), F=HUSTLE (best vec gain − baseline), G=VECTOR delta vs E,
H/I=LIVE (backtest_v12_engine only), K=per-row filters, and yellow filter columns
from L outward (header `FILTER=Value`; the cell is yellow-marked FFFF00 where the
filter is opportune for that row's switch). Yellow cell value = real delta of
(chain overrides + switch=cand + that one filter) vs cumulative_before.

## 2. Measured facts (why days of compute produced no filled sheet)

- The engine is NOT the problem: `evaluate_prepared_sanitized` measured
  **p50 0.028s / p95 0.037s on Mac, p99 0.07s on s1** under the live herd. The
  "tenths of a second per cell" target already holds.
- The compute HAS been happening: GDX_LONG progress JSON on s1 holds **3,032 done
  rows and 73,583 numeric yellow deltas** (+36MB delta log).
- The loss is the pilot's 6.5k-line write path: shipped workbooks had **F=0 on every
  row, E2 still the literal string 'BASELINE' on all 13 tabs**, C mostly empty.
  One code path even blanks F by design ("F NOT USED — leave blank", v15_pilot
  ~line 4212) while the spec requires F every row. Results were computed, logged
  to JSON, and dropped on the floor.
- Old JSONs can carry corrupted cums (`cumulative_gain=0.0` seen on AAPL_SHORT vs
  engine −4.08). Engine sign is authoritative; the framework re-anchors.

## 3. The framework — `tools/v15_assure.py`

Single tool, five modes, deployed to Mac + s1 + s2 (md5-verified). JSON progress
files are the source of truth; the workbook is a deterministic render of them.

```
python3 tools/v15_assure.py audit --all              # fill-state truth per sym_side, exit!=0 if any inconsistent
python3 tools/v15_assure.py refill GDX_LONG          # rebuild xlsx from JSON truth — NO evals, ~30s for 3k rows/73k yellows
python3 tools/v15_assure.py complete AAPL_SHORT      # engine-evaluate remaining pending rows (greedy chain per spec)
python3 tools/v15_assure.py bench AAPL_SHORT         # prove p95 <= 0.3s per eval on this host (PASS/FAIL exit code)
python3 tools/v15_assure.py watch --interval 60      # daemon: JSON-ahead-of-xlsx -> auto refill; stale pilot -> STALL report
```

Guarantees:
- **Never stalls**: every eval runs under a hard 10s future timeout; timeout/error
  → RED cell + reason recorded, row continues. Per-row heartbeat
  `/tmp/v15_assure_heartbeat_<SYM_SIDE>.txt`. Rows >5s are logged SLOW.
- **Never races a live pilot**: takes the same per-output-file flock as v15_pilot
  (`data/locks/v15_pilot_{ss}_{md5}.lock`); skips locked workbooks unless `--force`.
- **Never truncates**: single-writer atomic save with zip validation (≥10 entries +
  CRC test) before `os.replace` — same defense as the pilot's `_atomic_save`.
- **Never lies**: every written number is a real engine gain/delta. F is only
  written when a real vec gain exists in the record (vec_gain, joint_gain, or
  best yellow's cum_before+delta); `complete` re-anchors cumulative_gain with a
  fresh engine eval and reports when the stored value disagreed. No Sharpe is
  emitted anywhere by this tool.
- **Self-verifying**: refill re-audits after save; verdict must be CONSISTENT
  (F≈done, yellows≈JSON, E2 numeric on all 13 tabs).
- `watch` is report+refill only by default; it never kills a pilot without `--kill`.

## 4. Operating procedure

1. On each worker: `audit --all` → list of `JSON_AHEAD_OF_XLSX` sym_sides.
2. `refill` them (locks make this safe alongside the running herd — live sym_sides
   are skipped, finish later, and the watch picks them up).
3. `watch --interval 300` under nohup on s1 and s2 keeps every future sheet
   converged and surfaces STALL lines (pilot holds lock but JSON idle >30 min).
4. `bench <SYM_SIDE>` on any host before blaming the engine for slowness.
5. `complete <SYM_SIDE>` only on the host that has the NPZ (s1 crypto, s2 stocks);
   it refuses honestly when `prepare_batch` finds no data.

## 5. Test evidence (2026-09-28, sandboxed copies, then deployed)

- refill GDX_LONG (fresh s1 JSON): 3,032 rows, 73,573 yellow cells, 117 reds,
  F column fully populated, E2 numeric on 13 tabs — 31s, post-verdict CONSISTENT.
- complete AAPL_SHORT: pending rows evaluated at p50 0.031s / p95 0.054s,
  stored cum 0.0 corrected to engine −4.0796.
- bench AAPL_SHORT: n=30 p50 0.028s p95 0.037s → PASS.

## 6. Production state (2026-09-28 ~17:10Z)

- s1 full audit (374 sym_sides before the 30-min cap): **55 JSON_AHEAD_OF_XLSX,
  278 JSON_ONLY, 39 NO_DATA, 0 CONSISTENT** — quantifies "days of compute, zero
  finished sheets".
- watch daemons live on s1 (`python3`) and s2 (`./.venv/bin/python`, bare python3
  there lacks numpy), logs `/tmp/v15_assure_watch*.log`, interval 300s, refill-only
  (no --kill). They converge the backlog and every future sheet automatically.
- Live pilots are respected: GDX_LONG refill on s1 correctly skipped (flock held).
- Row drift handled: AMZN_LONG refill relocated 2,381 rows by (switch,cand)
  identity; 488 keys whose switch was purged from today's templates skipped
  honestly; 56 unmapped yellow headers counted.
- Corrupt chains quarantined: 1000BONKUSDC_SHORT chain baseline 0.0 with real
  initial_baseline_gain −14.11 → F/E2 withheld by refill; `complete` re-anchored
  (stored cum −14.04 vs engine −30.02 — engine wins) and backfills F from raw
  stored vec_gains.
- Old watch caveat: `complete` must run on the host holding the NPZ (s1 crypto,
  s2 stocks); it refuses honestly elsewhere.
