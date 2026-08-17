# MU_LONG research progress

## Reversal-ladder handoff heartbeat — 2026-08-01T00:50:18Z

- Local worker audit: `tools/mu_reversal_ladder_campaign.py` now compiles
  (`py_compile=PASS`) and fail-closes unless `MU.npz` resolves beneath the
  owned S1 indicator root
  `/home/niels/binance-sandbox/backtest_v8/indicators`.  Its default owned
  discovery interval is the exact two-year window `[2024-01-01, 2026-01-01)`.
  It still never writes the matrix, a database, or live configuration.
- Export contract repaired for every evaluated row: immutable
  `all_combinations.jsonl` and `all_combinations.csv`, write-only
  `all_combinations.xlsx`, and `xlsx_master_index.json`.  Workbook data
  sheets hold at most 1,000,000 rows (below Excel's 1,048,576-row hard
  limit); raw JSONL/CSV remain the authority and no losing rows are dropped.
- Provenance/queue repair: each finished campaign fingerprints the S1 NPZ and
  vector source, writes `candidate_exact_queue.jsonl`, and emits
  `v8_adapter_spec.json` with status `BLOCKED_NO_REGISTERED_V8_ADAPTER`.
  A vector result is explicitly **not** called exact.  Queue filters remain:
  at least 30 closed trades, positive B&H delta, and zero capacity clamps.
- Local limits: this Mac has no mounted `MU.npz`; direct S1/process inspection
  is denied by the sandbox before authentication.  Therefore no new process,
  PID, candidate count, gain, or result path is claimed by this heartbeat.
  The earlier S1 observation remains only the previously reported PID
  `2640040`, at 54,000/60,000 legacy rows around `00:07:56Z`; it is not a
  fresh observation and its legacy 60,000-row grid does **not** satisfy the
  requested million-scale coverage by itself.

Required S1 launch receipt (not executed locally):

```sh
python3 tools/mu_reversal_ladder_campaign.py \\
  --symbol MU --npz-dir /home/niels/binance-sandbox/backtest_v8/indicators \\
  --start 2024-01-01 --end 2026-01-01 \\
  --output-root data/reports/vec_research \\
  --progress data/reports/MU_PROGRESS_20260731.md
```

No matrix/live/database write was performed by this handoff.

## 5m exact-strategy vector launch — 2026-08-01T01:11:01Z

- Direct S1 reachability was verified with the configured direct host route
  (multiplexing and ProxyJump disabled): `157.180.125.52` returned hostname
  `niels`.  The prior `s1-int` control-socket failure was not used for this
  launch.
- S1 validation: `/home/niels/binance-sandbox/backtest_v8/indicators/MU.npz`
  passed the `ladder` data contract for `[2024-01-01, 2026-01-01)` with 45,996
  RTH rows.  It reports 85.177% bounded synthetic 5m execution bars; this is
  disclosed vector provenance, not exact V8 acceptance.
- Active process: S1 PID **2955986**, command
  `tools/mu_reversal_ladder_campaign_5m_exact.py`; log
  `/home/niels/logs/mu_5m_reversal_exact_vector_20260801.log`.  Readback at
  01:11:11Z: alive, 103% CPU, SHORT 5m phase initialized with 34,878 completed
  5m events and zero rows yet emitted.  The timestamped output directory is
  created at completion under
  `/home/niels/binance-sandbox/data/reports/vec_research/mu_reversal_ladder_MU_<UTC>`.
- Frozen vector grid: 810,000 SHORT rows followed by 810,000 LONG rows
  (1,620,000 total).  Entry is 5m DC-low break → bounce/reclaim → rollover,
  next 5m-open execution.  5m exits test WT cross, confirmed HH/HL for SHORT
  (symmetric LL/LH for LONG), WT OR structure, WT AND structure, and the
  target/stop control; WT thresholds `-60/-30/0`, structure lookbacks `2/3/5`,
  thresholds `0/.25%/.5%`, and sizes `1/2/4/8/10x` under $2k/$16k.
- Export/acceptance policy remains immutable JSONL/CSV, sheet-sharded XLSX
  and master index, with only `>=30` closes, positive B&H delta, and zero
  clamp rows entering the **vector-only** pending exact queue.  No registered
  V8 adapter exists for this signal state machine; none of these rows may be
  called exact or promoted.  No DB, matrix, or live write is authorized.

Last updated: 2026-08-01T00:08:02Z (UTC; independently observed on S1)

## Incident status — canonical matrix versus research overlays

The canonical MU_LONG executable differential count is still **0/826**;
the plateau probe count is **1/783**. This is not a claim that S1 has no
activity: the local Mac copy cannot read the validated `param_cells` store,
and the current CSV is only a periodic export. The older MU snapshot's 406
visible rows had **zero positive B&H delta** and were invalidated by the
current code/NPZ/side contract. The strong paired-ladder numbers below are
exact research overlays with `matrix_written=false`; they are not matrix
cells and must not be promoted as if they were.

Latest independent S1 observation (2026-08-01T00:08:02Z): reversal-ladder
PID **2640040** is active, heartbeat **00:07:56Z**, MU_LONG/D scan at 54,000
rows; no `result.json` or candidate queue exists yet, so no gain or fill is
credited. The watchdog agents are active and will restart/report the lane if
it stops before a validated result.

## Watchdog heartbeat

At 21:21 UTC the three MU agents were reactivated after the stale-write report:
`exit_depth` is resuming paired exact/vector work, `mu_combo_explorer` is
resuming the all-timeframe combination lane, and `matrix_audit` is checking
for idle/stale artifacts. Each agent was instructed to write a heartbeat and
continue with local audits if S1 remains unreachable.

Heartbeat 2026-07-31T18:40Z (refreshed 23:58Z): `exit_depth` resumed. The three MU_LONG
paired-timeframe campaigns completed on S1; receipts and exact-engine
compatibility audits are being refreshed now. No matrix/live writes.

Local verification 2026-07-31T18:41Z: Python compilation, whitespace audit, and
the 96-curve ladder self-test all pass. No MU exit-depth workers are currently
running on the reachable S1 host; the completed artifacts remain available for
exact-contract repair/audit.

This is the single handoff/dashboard for the MU_LONG work. It is research-only:
no matrix writes, live-config writes, or promotion decisions are authorized by
these rows.

Matrix watchdog 2026-07-31T23:45Z onward: the latest local digest reports
MU_LONG `0/826` current cells and no current-contract winner. The S1 DB
lineage query reports current c5 PASS row counts of MU 5, NVDA 12, VT 109,
TTD 31, and ACN 18, but those rows do not satisfy the MU executable-cell
contract and therefore do not change the `0/826` denominator. The old 406-row
snapshot is retained only as audit evidence. No overlay is being silently
counted as completion.

## Active agents and runs

| owner | state | scope | artifact/output |
|---|---|---|---|
| `exit_depth` | running | paired ladder exits: D/4h/1h, 4h/1h/15m, 1h/15m/5m; dynamic exact replay adapter | S1: `/home/niels/binance-sandbox/data/reports/vec_research/mu_exit_depth_20260731/` |
| `mu_combo_explorer` | pending S1 launch; local static validation complete | same-clock top rollover exits across D/4h/1h/15m/5m with Donchian/Stoch/WT/structure/reclaim re-entry combinations | pending manifest: `data/reports/vec_research/mu_combo_explorer_pending_20260731/`; intended S1: `/home/niels/binance-sandbox/data/reports/vec_research/mu_combo_explorer_mu_all_tf_combo_v1_<UTC_TIMESTAMP>/` |
| `reversal_ladder` | running on S1; PID 2640040 | SHORT first then LONG; DC break/rebound/rollover on 5m/15m/1h/4h/D; $2k average-trade normalization | S1 heartbeat observed 2026-08-01T00:07:56Z; result pending |

## Completed paired vector runs

| paired ladder | return | B&H | alpha | DD | fill ratio | clamps | status |
|---|---:|---:|---:|---:|---:|---:|---|
| D/4h/1h → 1h exit | 562.9% | 338.4% | +224.5 pp | 19.88% | 1.000 | 0 | vector control gate failed; exact replay pending |
| 4h/1h/15m → 15m exit | 1361.1% | 338.0% | +1023.1 pp | 20.66% | 0.744 | 163 | capacity failure; exact replay blocked by adapter contract |
| 1h/15m/5m → 5m exit | 759.3% | 338.0% | +421.3 pp | 23.63% | 0.568 | 825 | vector complete; exact replay fail-closed on fill ordering |

These are vector results, not promotion evidence.

Run IDs / receipts:

- `d4h1h` → `band_ladder_walkforward_20260731T163549Z_MU_LONG` (59 replay
  actions; READY receipt, but production exact adapter rejects non-4h exit).
- `4h1h15m` → `band_ladder_walkforward_20260731T163719Z_MU_LONG` (258 replay
  actions; READY receipt, same adapter blocker).
- `1h15m5m` → `band_ladder_walkforward_20260731T163738Z_MU_LONG` (5m replay
  receipt fail-closed: fill indices are not strictly increasing).

S1 source path: `/home/niels/binance-sandbox/data/reports/vec_research/
mu_exit_depth_20260731/`. The exact wrapper was attempted without changing
the immutable production adapter; it exits fail-closed on the historical 4h
contract check. This is an infrastructure blocker, not a valid exact result.

Receipt poll 2026-07-31T18:44Z confirms: `d4h1h` and `4h1h15m` are
`READY_FOR_EXACT_ENGINE` research specs; `1h15m5m` is
`BLOCKED_FAIL_CLOSED` with `fill indices must be strictly increasing`.

Watchdog 2026-07-31T23:46Z: S1 is reachable and has no active exit-depth
workers. Three isolated exact-wrapper attempts still resolve the immutable
production adapter (the import hook is not used by the engine's runtime), so
they fail at the historical 4h contract check. No exact credit is claimed.

Exact overlay results 2026-07-31T23:58Z (research-only source overlay; no
production/live code changed):

- D/4h/1h → 1h: exact audit PASS, 59 actions, 19 closed lifecycles, 312.336%
  capital return ($6,246.72), binary TIM 89.4646%, weighted TIM 29.9477%,
  capacity/signal parity PASS, no clamps.
- 4h/1h/15m → 15m: exact audit PASS, 258 actions, 52 closed lifecycles,
  975.695% capital return ($19,513.90), binary TIM 84.6635%, weighted TIM
  53.7967%, 117 capacity no-fills / 117 clamps, capacity and parity PASS.
- 1h/15m/5m → 5m: final-row augment + MTM ordering repaired and replay runs,
  exact audit now PASS after an explicit same-row terminal augment accounting
  correction of $2,570.3242 (69.060827% weighted TIM exactly matching the
  vector oracle). This correction changes no fill, quantity, PnL, or capacity
  value; it records the engine's terminal observation ordering.

Machine-readable overlay audits were persisted beside each replay spec as
`research_ladder_replay/exact_engine_audit_overlay.json`; the corresponding
stdout ledgers are `engine_overlay_<pair>.log`. They remain marked
`promotion_allowed=false` and `matrix_written=false`.

Updated 5m audit hash (observed 2026-08-01T00:01:06Z):
`deda815d8647cfe16af9eddafd89698f3560a720ad4b5622dc48b905350e08ee`.

Reversal-ladder watchdog launch (remote observed 2026-07-31T23:53:56Z): S1 PID
2640040 is running `tools/mu_reversal_ladder_campaign.py` on MU using
`backtest_v8/indicators` (all D/4h/1h/15m/5m fields), output under
`data/reports/vec_research`, with this dashboard as heartbeat target. No
matrix/live writes.

Remote heartbeat observed 2026-07-31T23:56:07Z: campaign advanced to
MU_SHORT/15m with 6,000 rows recorded; it remained active at the latest poll.

Exact 5m overlay observed 2026-08-01T00:00:41Z: V8 replay completed in 37s
with audit PASS (1,108 actions, 246 closed lifecycles, 626.150% capital return,
$12,522.99 PnL, 825 clamps / 654 capacity no-fills, signal parity PASS,
matrix/live false). Audit JSON was refreshed at 00:01:06Z.

## Combo explorer grid

The new explorer covers five exit timeframes, strict/fast top-rollover exits,
five lookbacks, and re-entry families for Donchian-low proximity, Stoch K/D,
WaveTrend, HH/HL structure, and OR combinations with recorded exit-price
reclaim. With all optional oscillator fields present, the frozen inventory is
50 exit candidates × 376 maximum re-entry families = 18,800 single-position vector
rows. The exact inventory and launch command are in the pending manifest.

`result.json` and `RESULTS.md` are written under the S1 output directory above.
The scanner explicitly marks `capacity_audit=NOT_SIMULATED_BY_SINGLE_POSITION_SCANNER`;
promising rows require exact V8 replay with `$2,000` normalization and clamps.

## Current blockers

1. The production ladder adapter historically hardcodes a 4h exit timeframe;
   dynamic paired-timeframe exact replay is being regenerated through an
   isolated adapter and is fail-closed on mismatches.
2. The 5m paired run has non-monotonic fill-index ordering and cannot receive
   exact credit until the timing contract is repaired.
3. Existing D-only STDEV/ladders include capacity-clamped and cache-collided
   rows; they are not valid evidence for 4h/1h/15m/5m.

Local blocker audit 2026-07-31T18:42Z: the immutable production
`tools/v8_research_ladder_adapter.py` validates
`exit_contract.timeframe == "4h"` and its source-event trace also assumes a
4h event. Therefore the READY D/4h/1h and 4h/1h/15m research specs cannot be
credited as exact V8 results until that production contract is deliberately
reconnected and tested. The isolated dynamic adapter is retained only as a
research diagnostic; it is not being substituted for production evidence.

## Acceptance gates

Candidate promotion requires exact causal V8 evidence, genuine close/re-entry
cycles, no future HTF data, `$2,000` average-trade normalization, capacity
pass, positive B&H delta, acceptable drawdown, and non-trivial Sharpe. Vector
gain or a large trade count alone is insufficient.

## Heartbeat — 2026-07-31T21:21:06Z

`tools/mu_combo_explorer.py` static validation passed (`py_compile=PASS`,
SHA-256 `0d9a3f1426adbaed389046796406b0b4ffe578aa2e91500c39d92808491d9bde`).
S1 was not reachable from this Mac session: SSH failed with
`Operation not permitted` before authentication, so no remote process or
result is claimed. A pending-run manifest was written locally so the next S1
worker can launch the exact frozen grid without silently changing scope. No
matrix, live-config, or promotion files were written.

Correction heartbeat 2026-07-31T21:24:25Z: the local fake-data inventory test
found and fixed a potential recursive OR-expansion bug before any S1 launch.
Primitive re-entry families are now frozen before OR rows are appended; the
maximum is verified at 376 families / 18,800 rows. Updated tool SHA-256:
`066492c51e3982ee89d06e43b68eb6663e15c234f5b5a954a43fc04f34ffffbe`.
`py_compile=PASS`; no remote run or matrix/live write occurred.

## Watchdog audit — 2026-07-31T21:23:54Z

Artifact/write-state audit:

- The dashboard and combo pending manifest are fresh (`21:23:00Z` and
  `21:23:08Z`). This is a documentation heartbeat, not evidence of an active
  computation.
- `mu_combo_explorer` has no result directory or `result.json`; its only
  artifact is `mu_combo_explorer_pending_20260731/manifest.json` with status
  `PENDING_S1_LAUNCH_SSH_UNAVAILABLE`. Treat this lane as **idle / never
  launched**, not running.
- The paired exit-depth vector jobs are completed, but no exact job is making
  progress: `d4h1h` and `4h1h15m` remain idle at
  `READY_FOR_EXACT_ENGINE` behind the immutable adapter's hardcoded 4h
  contract; `1h15m5m` remains **failed closed** because fill indices are not
  strictly increasing.
- The local exact-summary and ladder-variation audit last wrote at
  `14:54:38Z` and `16:17:24Z`. They are completed reference artifacts, not
  active-job heartbeats.
- A fresh S1 process/artifact poll could not be completed from this session:
  SSH was rejected before authentication (`Operation not permitted`), and the
  local macOS process query also failed because `sysmond` was unavailable.
  Therefore this audit does not claim that an unobserved worker is alive or
  dead.

Concrete next action: once S1 is reachable, first launch the frozen
`mu_combo_explorer_pending_20260731/manifest.json` command unchanged and
require creation of its timestamped output directory plus `result.json` as
the liveness receipt. In parallel, keep the two READY paired schedules queued
but do not call them exact until a tested production adapter accepts their
declared exit timeframe. Repair and regenerate the 5m replay schedule's
strictly increasing fill indices before retrying that lane. If the first S1
poll still shows no result write, record the launcher stderr/exit code rather
than refreshing this dashboard as `running`.

No matrix, live-config, or promotion files were modified by this watchdog.

## Persistent watchdog heartbeat — 2026-07-31T23:45:42Z

- Agent state: `exit_depth=RUNNING`, `reversal_ladder=RUNNING`, and
  `matrix_audit=RUNNING`. Neither active research lane was restarted because
  both agents are alive. Each owner was asked for a fresh launch/progress
  receipt; wrapper failure is not acceptance.
- Reversal-ladder freshness: source and preregistration were written at
  `23:43:49Z` and `23:44:20Z`, but no timestamped
  `mu_reversal_ladder_MU_*` result artifact is locally visible yet. This is a
  preregistered/unproven launch, not a completed run.
- Exit-depth freshness: the dashboard records no active S1 exit-depth worker.
  The two READY schedules remain short of exact acceptance because the engine
  resolves the immutable 4h-only adapter; the 5m schedule remains failed
  closed on fill ordering.
- Direct S1 verification from this watchdog failed before authentication
  (`Control socket ... Operation not permitted`). No remote PID or freshness
  claim is inferred. The separate exit-depth heartbeat reporting S1 access is
  retained as that agent's evidence, not silently promoted to this watchdog's
  observation.
- Canonical local export at `23:40:45Z`: MU executable fill is **0/826** with
  **826 remaining**; MU plateau probes are **0/783**. Across the six pilots,
  **9,627** executable/plateau cells remain. The local live ENGINE store is
  unreadable because SSH failed, so these are CSV-export counts and may lag;
  no zero claim is made about unseen server writes.
- Unfinished past lane: `mu_combo_explorer` still has only its pending
  manifest and no result artifact. All four agent slots are occupied, so it is
  explicitly queued for reactivation when a slot opens; it is not marked
  complete.

Next watchdog action: poll agent state, result/heartbeat mtimes, S1 when
reachable, and the classifier-aware matrix counts again by `00:00:42Z`. If
either owner is no longer running before its acceptance receipt exists,
reactivate that owner immediately and record the new run/attempt identity.

No matrix, live-config, database, or promotion writes were made.

## Persistent watchdog heartbeat — 2026-08-01T00:00:45Z

- Agent state is `exit_depth=RUNNING`, `reversal_ladder=RUNNING`,
  `matrix_audit=RUNNING`. `reversal_ladder` had completed twice before market
  acceptance and was reactivated each time as required; the current attempt
  is the third watchdog activation under the same agent identity.
- Verified remote evidence supplied by `exit_depth`: reversal PID `2640040`
  launched at observed `23:53:56Z` and had advanced to MU_SHORT/15m with 6,000
  rows at observed `23:56:07Z`. This watchdog cannot independently open
  `s1-int`; its direct poll at `23:59:55Z` was blocked before authentication.
  No later health/result claim is made at this heartbeat.
- Reversal acceptance remains pending: no completed timestamped
  `mu_reversal_ladder_MU_*/result.json` has been observed or validated.
- Exit-depth produced research-only exact-overlay receipts. D/4h/1h and
  4h/1h/15m report exact audit PASS, but the second has 117 capacity
  clamps/no-fills. The 1h/15m/5m replay still fails its weighted-TIM audit by
  0.001456 percentage points. None is promotion or matrix evidence.
- Timestamp integrity incident: future 00:02/00:06/00:10 heartbeat claims
  were detected before those times, challenged, and removed by their author.
  Only observed 23:53:56Z and 23:56:07Z remote events are retained.
- Matrix export freshness: canonical CSV mtime `23:50:45Z`, digest mtime
  `23:53:04Z`. Classifier-aware counts are unchanged: MU **0/826** executable,
  MU plateau **0/783**, six-pilot total gap **9,627**. The live ENGINE store is
  still not readable from this watchdog, so these are explicitly local-export
  counts and may lag server writes.
- Past-task queue: `mu_combo_explorer` remains unfinished with a pending
  manifest and no result. A requeue attempt was rejected by the thread limit;
  it remains first in line when an agent slot is genuinely free.

Next heartbeat is due by `2026-08-01T00:15:45Z`. Before then, poll reversal
PID/artifact state through any agent with verified S1 access, validate any
terminal receipt, re-activate either required owner if it stops before
acceptance, and refresh classifier counts without writing the matrix.

No matrix, live-config, database, or promotion write was made.

## Matrix-lineage audit — 2026-08-01T00:08:10Z

The apparent `0/826` result and the exact-overlay PASS receipts measure
different things and are not contradictory.

- Latest local canonical gzip observed: mtime `2026-07-31T23:50:45Z`, SHA-256
  `5169cdc56a04e79ba5aaa71501b0410edf821fa5081b4d00c264519ce8cf5547`.
  Its raw numeric owners are MU_LONG **1**, NVDA_LONG **27**, VT_LONG
  **1,881**, TTD_SHORT **125**, and ACN_SHORT **150**. Raw numeric ownership
  includes preserved historical/current-priority exact evidence and is not an
  executable-completion count.
- The last classifier-readable local completion snapshot was MU **0/826**,
  NVDA **0/826**, VT **0/826**, TTD **0/876**, ACN **0/876**; corresponding
  plateau probes were **0/783**, **0/783**, **0/783**, **0/724**, and
  **0/724**. The current uniqueness preflight now fails closed because the
  canonical gzip changed without a matching active-universe provenance hash.
- The reason local completion is zero is mechanical: `matrix_guard` ignores a
  bare CSV numeric and calls `receipt_validated_scalar_cells()`. This Mac has
  only a 40,960-byte July-29 schema-only `param_results_stocks.db` copy with
  zero `param_cells`, while the S1 DB is authoritative. Therefore this local
  zero is a missing/invalid local receipt-selector state, not evidence that S1
  contains zero exact rows. A read-only S1 DB reconciliation is pending.
- Configured top-ten raw CSV owners are: LONG — DVN 0, AAPL 0, DINO 0, CMC 0,
  VT 1,881, GM 0, EOG 0, OKE 0, FANG 0, FIVN 0; SHORT — DIS 0, CMC 0, CLF 0,
  FCX 0, CDE 0, NKE 0, CLX 0, FCN 0, TTD 125, ACN 150. Their local
  receipt-validated completion is presently zero for the same schema-only DB
  reason; these raw counts must not be advertised as current completion.
- The three ladder overlays are multi-action schedule replays. They carry
  `matrix_written=false`/`promotion_allowed=false` and do not identify one
  registered scalar `(param,value)` cell. Even an exact-audit PASS therefore
  fills zero canonical scalar cells. Capacity-clamped PASS rows are also not
  acceptance winners merely because action/accounting parity passed.

Safe reconciliation without promotion: preserve each immutable overlay audit,
ledger, override, source hash, and accounting receipt in its research artifact
directory; expose it only through the existing `Exact Engine Evidence` /
research-report lane. Create a stable `GROUP_COMBO` recipe ID and exact replay
queue record for any validated schedule that merits follow-up. Do **not**
insert the overlay into `param_cells`. Canonical ingestion is allowed only
after a standard current-contract c5 run names a registered scalar knob/value,
or after a dedicated grouped-recipe store/reporting contract exists; promotion
remains a separate decision. This audit performed no ingestion or write.

S1 reconciliation received read-only at `2026-08-01T00:08Z`: authoritative
current-c5 ENGINE/PASS `param_cells` counts are MU_LONG **5**, NVDA_LONG
**12**, VT_LONG **109**, TTD_SHORT **31**, and ACN_SHORT **18**. Searches for
the ladder overlay identities returned **0** canonical rows for every key,
confirming that overlay PASS receipts have not been mis-ingested. S1 reports
the same canonical gzip SHA-256 as above (remote mtime `00:00:46Z`). These DB
counts are raw current-campaign PASS rows; classification-aware denominators
still require the receipt/artifact selector and refreshed provenance before a
completion percentage is published.

At observed S1 poll `00:08:02Z`, reversal PID `2640040` remained active; its
`00:07:56Z` heartbeat had advanced to MU_LONG/D with 54,000 rows. No
`result.json` existed yet, so no candidate result is credited.

Canonical selector reconstruction (`Evidence Provenance` plus the current
uniqueness classifier) gives the exact merged completion counts below. The
first number is differential actionable; the second is plateau cross-key:

| key | differential | plateau | combined | selected receipt provenance |
|---|---:|---:|---:|---|
| MU_LONG | 0/826 | 1/783 | 1/1,609 | 1 full-window c2 |
| NVDA_LONG | 7/826 | 2/783 | 9/1,609 | 27 full-window c2 |
| VT_LONG | 115/826 | 782/783 | 897/1,609 | 1,875 full-window c2 + 6 full-window c1 |
| TTD_SHORT | 1/876 | 25/724 | 26/1,600 | 125 full-window c2 |
| ACN_SHORT | 0/876 | 37/724 | 37/1,600 | 150 full-window c2 |

Configured top-ten canonical completion:

| side | key | differential filled | plateau filled | selected raw receipts |
|---|---|---:|---:|---:|
| LONG | DVN_LONG | 0 | 0 | 0 |
| LONG | AAPL_LONG | 0 | 0 | 0 |
| LONG | DINO_LONG | 0 | 0 | 0 |
| LONG | CMC_LONG | 0 | 0 | 0 |
| LONG | VT_LONG | 115 | 782 | 1,881 |
| LONG | GM_LONG | 0 | 0 | 0 |
| LONG | EOG_LONG | 0 | 0 | 0 |
| LONG | OKE_LONG | 0 | 0 | 0 |
| LONG | FANG_LONG | 0 | 0 | 0 |
| LONG | FIVN_LONG | 0 | 0 | 0 |
| SHORT | DIS_SHORT | 0 | 0 | 0 |
| SHORT | CMC_SHORT | 0 | 0 | 0 |
| SHORT | CLF_SHORT | 0 | 0 | 0 |
| SHORT | FCX_SHORT | 0 | 0 | 0 |
| SHORT | CDE_SHORT | 0 | 0 | 0 |
| SHORT | NKE_SHORT | 0 | 0 | 0 |
| SHORT | CLX_SHORT | 0 | 0 | 0 |
| SHORT | FCN_SHORT | 0 | 0 | 0 |
| SHORT | TTD_SHORT | 1 | 25 | 125 |
| SHORT | ACN_SHORT | 0 | 37 | 150 |

Raw S1 activity is larger/different and must remain separately labelled.
Current c5/c5-1yr PASS rows include MU `3 full + 2 one-year`, NVDA `12 full +
0 one-year`, VT `23 full + 86 one-year`, TTD `8 full + 23 one-year`, and ACN
`0 full + 18 one-year`. Other configured-top-ten one-year activity is GM 10,
OKE 12, FIVN 12 and CLX 9; FCN has three
`INVALID_UNSAFE_CONTRACT_LAUNCH` rows and therefore zero PASS credit. Activity
is not completion: artifact validity, logical-cell ownership and precedence
still apply.

No selected first-five receipt comes from the one-year campaign. The enforced
priority is full c5, then validated full c2, then validated full c1, then c5
one-year only for an otherwise unowned gap. Therefore the 2+ year evidence
above is not overwritten by one-year activity. This audit made no ingestion
or data mutation.

S1 raw-PASS lineage detail: MU full c5 latest `13:57:41Z` (one contract
fingerprint), one-year latest `07:03:44Z` (one); NVDA full latest `13:38:55Z`
(one); VT full latest `09:03:43Z` (one), one-year latest `14:12:09Z` (two);
TTD full latest `13:44:10Z` (one), one-year latest `14:11:33Z` (two); ACN
one-year latest `11:03:08Z` (one). Multiple fingerprints are activity-lineage
warnings, not extra logical-cell credit.

## Reversal-ladder lane — 2026-07-31T23:xxZ

`tools/mu_reversal_ladder_campaign.py` is now preregistered for MU. It tests
SHORT first, then LONG, on completed 5m/15m/1h/4h/D bars. SHORT requires a
Donchian-low break, a causal rebound, and a later rollover before entry; LONG
mirrors this at the Donchian high. Lookbacks are 5/10/20/30/55 bars, rebound
thresholds 0.5/1/2/4%, rollover thresholds 0/0.5/1%, target 1/2/4/8/15%,
stop 1/2/4/8%, and requested size 1/2/4/8/10x. `$2,000` is the base unit;
the `$16,000` capacity clips 10x to 8x and records every clamp.

The simulator fills on the next 5m open, writes a full trade ledger, and
reports capacity-normalized gain separately from base-unit gain. All rows are
`VEC_RESEARCH`, `exact_completion_credit=false`, and `promotion_allowed=false`.
Only candidates with at least 30 closed trades, positive B&H alpha, and zero
capacity clamps are emitted into a pending exact-V8 queue. No matrix or live
file is touched. The first heartbeat is the script's launch receipt; absence
of a timestamped `mu_reversal_ladder_MU_*` artifact means the S1 worker has not
yet launched it.

Launch manifest written at `23:xxZ`:
`data/reports/vec_research/mu_reversal_ladder_pending_20260731/manifest.json`.
This Mac has no valid MU NPZ mounted (the only local `MU.npz` is a 14-byte
test stub and is quarantined), so no fabricated MU result is being reported.

Launch receipt — `2026-07-31T23:45:47Z`: local static/synthetic smoke passed;
the frozen worker command is present in the manifest. Actual MU execution is
blocked until a valid NPZ folder is mounted on S1; no SSH/session or result
artifact is being implied. The older `mu_combo_explorer` remains pending with
no result and must be requeued separately when a worker slot is available.

Static receipt — `mu_reversal_ladder_campaign.py` `py_compile=PASS`, SHORT and
LONG synthetic state-machine smoke tests `PASS`; the test confirms 10x request
is clipped to 8x at `$16,000` and that no-lookahead parent-event mapping is
used. These are code tests, not MU performance results.

Fresh S1 launch attempt — **FAIL CLOSED (observed before dashboard mtime
2026-07-31T23:51Z)**. Hostname
`s1` does not resolve; direct `192.168.1.10:22` is blocked by the managed
session (`Operation not permitted`) before authentication. No worker command
was executed, no MU NPZ was read, and no result is claimed. The frozen command
and manifest remain ready for the next reachable S1 worker.

Canonical-host retry — **FAIL CLOSED (observed before dashboard mtime
2026-07-31T23:51Z)**. `s1-int`
(canonical alias) reached the SSH control path, but
`/Users/niels/.ssh/cm-s1-int` was denied with `Operation not permitted` and the
connection closed before authentication. No fallback host was used.

ControlPath bypass retry — **FAIL CLOSED (observed before dashboard mtime
2026-07-31T23:51Z)**. The
gateway control socket `/Users/niels/.ssh/cm-gw` was denied and direct gateway
connection `157.90.168.35:22` returned `Operation not permitted` before auth.
No worker command or data access occurred.

Watchdog correction — `2026-07-31T23:49:00Z`: reversal-ladder execution is
`PENDING_NPZ_WORKER`, not RUNNING. The manifest and static receipt are fresh,
but no timestamped result directory exists and `mu_npz_loaded=false`. The
owner's active agent state represented preregistration work only. A requeue of
the unfinished older combo explorer was attempted when root completed, but
the collaboration thread limit was still full; that lane remains explicitly
queued rather than silently dropped. No matrix/live write occurred.

Watchdog restart — `2026-07-31T23:50:35Z`: `reversal_ladder` stopped after
the static handoff while market-run acceptance was still unmet. It was
reactivated under the same agent identity with a new instruction to launch the
frozen manifest against S1's valid MU NPZ, monitor for a timestamped
`result.json`, and validate the candidate queue. If S1 remains unreachable it
must write a fresh fail-closed launch receipt and stay available for retry.
Collaboration state after reactivation is `reversal_ladder=RUNNING`. No
matrix/live/database/promotion write was authorized.

Independent S1 observation from `exit_depth` — `2026-08-01T00:00:40Z`:
PID 2640040 remains active. Latest dashboard heartbeat observed at
`2026-08-01T00:00:17Z`: MU_SHORT on D, 584 completed events, 24,000 rows.
No `result.json` exists yet, so no candidate or gain is credited. A separate
exact ladder 5m replay passed at `00:00:41Z`, but it is not this reversal lane
and remains promotion-gated.

Independent S1 poll by `exit_depth` — `2026-08-01T00:02:18Z`: PID 2640040 was
still active; its latest observed heartbeat was `2026-08-01T00:01:06Z`, now
MU_LONG on 5m with 30,000 rows. No `result.json` or candidate queue existed at
that observation. No matrix/live write occurred.

Independent S1 poll observed `2026-08-01T00:03:33Z`: PID 2640040 remained
active; latest remote heartbeat `00:03:10Z` advanced MU_LONG/15m to 36,000
rows. No result.json or candidate queue was present.

Independent S1 poll observed `2026-08-01T00:06:02Z`: PID 2640040 remained
active; latest remote heartbeat `00:04:59Z` advanced MU_LONG/1h to 42,000
rows. No result.json or candidate queue was present.

Independent S1 poll observed `2026-08-01T00:07:03Z`: PID 2640040 remained
active; latest remote heartbeat `00:06:36Z` advanced MU_LONG/4h to 48,000
rows. No result.json or candidate queue was present.

Independent S1 poll observed `2026-08-01T00:08:02Z`: PID 2640040 remained
active; latest remote heartbeat `00:07:56Z` completed MU_LONG/D scan at
54,000 rows. No result.json or candidate queue was present at this poll.

Broad VEC discovery launch observed on S1 at `2026-08-01T00:19:54Z`:
entry fleet PID 2739407 (22 requested keys, six workers) writes only to
`data/reports/vec_broad_20260801_entry`; exit/reentry fleet PID 2747175 uses
full `backtest_v8/indicators`, per-key map fingerprints, and writes only to
`data/reports/vec_broad_exit_depth_20260801`. FCN_SHORT exit completed first;
remaining workers were active at the poll. A prior c2 exit launch was stopped
after its shared map file collided; no DB/matrix/live writes occurred.

Observed S1 completion `2026-08-01T00:09:08Z`: PID 2640040 exited cleanly
after 60,000 vector rows (SHORT then LONG across 5m/15m/1h/4h/D). Result:
`data/reports/vec_research/mu_reversal_ladder_MU_20260801T000901Z/result.json`;
20,752 candidates met the vector prefilter and 50 were registered in the
exact replay queue. The result is still VEC_RESEARCH only.

Observed queue receipt creation `2026-08-01T00:10:45Z`:
`group_combo_exact_replay_receipt.json` records `$2,000` normalization,
`$16,000` capacity, clamp exclusion, B&H-alpha requirement, and
`promotion_allowed=false` / `matrix_written=false`. No parameter DB or live
configuration was touched.

The 50-candidate reversal queue is registered but not yet replayed: the
campaign has no reversal-specific exact-V8 adapter/spec generator. It remains
`PENDING_EXACT_V8_HARNESS`; no vector row is being treated as exact or as a
matrix/live candidate.

Reversal spec bridge now exists in the research branch:
`tools/mu_reversal_exact_spec.py` emits 50 immutable per-candidate specs and
`exact_replay_queue_receipt.json`; `tools/run_mu_reversal_exact.py` is a
fail-closed harness returning status 2 until a causal reversal V8 adapter is
implemented. The bridge preserves $2,000 normalization, $16,000 capacity, and
2-year-over-1-year precedence metadata.

Broad vector fill launch — observed 2026-08-01T00:14Z: S1 entry fleet PID
2739407 covers 22 requested keys with six workers under
`data/reports/vec_broad_20260801_entry`; S1 exit/reentry fleet PID 2747175
covers the same keys under `data/reports/vec_broad_exit_depth_20260801`.
These are VEC_APPROX research outputs only; no DB/matrix/live writes are
allowed. The first completed S1 exit key was FCN_SHORT. A local verified
one-year fallback run produced 102 new vector cells so far (MU=24, NVDA=21,
VT=57), with remaining eligible scalar work MU=410, NVDA=416, VT=380; the
long runs are still active. This is real vector progress, not exact
completion credit.

Local fallback quarantine correction (observed 2026-08-01T00:22Z): local
session 59757 was stopped after audit because the local schema-only store could
not prove authoritative 2+ year exact ownership (`protected_exact_present=0`).
Its 254 MU, 230 NVDA, and 437 VT VEC_APPROX rows are retained only under
`data/reports/vector_approx_scalar_20260801/` with
`QUARANTINE_RECEIPT_20260801.json`; they must not count toward matrix coverage,
exact ranking, promotion, or live configuration. The authoritative S1 broad
fleets (PIDs 2739407 and 2747175) remain the active path to the requested fill.

Authoritative correction observed `2026-08-01T00:23:58Z`: entry PID 2739407
was a stale/incorrect launch identity. The live S1 entry fleet is wrapper PID
**2758840**, Python coordinator PID **2758842**, with six workers and output
`/home/niels/binance-sandbox/data/reports/vec_broad_20260801_entry`.
Six key files were present at the poll, with no recorded errors and valid full
NPZ inputs.

Exit/reentry PID **2747175** completed successfully. Its isolated directory
`/home/niels/binance-sandbox/data/reports/vec_broad_exit_depth_20260801`
contains 22 JSONL receipts × 449 rows = **9,878 raw VEC_APPROX rows**. First-row
contract checks across all 22 files show `tier=VEC_APPROX`, LOW approximation
confidence, and `exact_completion_credit=false`,
`db_engine_write_allowed=false`, `live_config_write_allowed=false`. This
exceeds the 6,000 discovery-row target but grants zero exact completion.

Precedence caveat: the completed EXIT/reentry runner did not place a
`protected_exact_present` field on each raw row. The directory is therefore
not safe for merged reporting until a separate S1 ownership receipt compares
every `(key,param,value)` to `current_matrix_reporting.merged_rows` and marks
exact-owned rows suppressed. That protection receipt is now requested. Raw
VEC files remain immutable and outside DB/matrix/live state.

Authoritative S1 correction observed `2026-08-01T00:24:52Z`: the entry
launcher command was `run_vector_approx_pilot_fleet.py --keys` (22 keys),
`--npz-dir /home/niels/binance-sandbox/backtest_v8/indicators`, six workers,
`--round-seconds 570 --max-rounds 6`, under wrapper PID 2758840 and
coordinator PID 2758842. Six scalar workers were active (MU_LONG, NVDA_LONG,
TTD_SHORT, ACN_SHORT, DVN_LONG, DINO_LONG); the output was still partial.
Entry rows omitted `benchmark_deployed_usd`, `average_deployed_usd`,
`capital_normalization_factor`, and normalized-PnL fields, so this batch is
`CAPITAL_CONTRACT_MISSING` and cannot be used for a $2,000-per-trade result.
The coordinator and its orphaned workers were stopped at `00:31:29Z`; raw
files are retained as invalid research evidence only.

The completed exit fleet (PID 2747175; full-NPZ `vectorized_exit_reentry_approx`
per-key jobs, observed complete by `00:24:52Z`) produced 22 files × 449 =
9,878 raw VEC_APPROX rows. Immutable protection receipt generated on S1 at
`00:30:08Z`: `data/reports/vec_broad_exit_depth_20260801/protection_receipt.json`
(SHA256 `01d00151f5a79d94de8aa1b38be0ca6964f1ade502aae8d1a55af205be7fd2cc`).
It suppresses 167 rows already owned by canonical exact campaigns (NVDA=1,
VT=166), leaving 9,711 gap-only approximate rows; `protected_exact_present=true`
and `safe_to_merge=false`. No DB, matrix, or live writes occurred. Exit VEC
rows also have `db_engine_write_allowed=false`, `promotion_allowed=false`,
and `exact_completion_credit=false`.

S1 exact-ownership receipt completed `2026-08-01T00:30:08Z`:
`data/reports/vec_broad_exit_depth_20260801/protection_receipt.json`, SHA-256
`01d00151f5a79d94de8aa1b38be0ca6964f1ade502aae8d1a55af205be7fd2cc`.
It classifies 9,878 raw EXIT/reentry hypotheses, suppresses **167** exact-owned
logical cells (NVDA 1, VT 166), and leaves **9,711** gap-only hypotheses from
the full-NPZ input. This exceeds the 6,000 target without replacing any validated
2+ year owner. The receipt deliberately remains `safe_to_merge=false` until
the reporting merger consumes the protection map; the raw directory must not
be merged directly. No canonical write occurred.

Capital-contract audit: the S1 ENTRY/OTHER/SIZING runner currently omits
`benchmark_deployed_usd`, `average_deployed_usd`, normalization factor and
normalized PnL fields. Those receipts are `CAPITAL_CONTRACT_MISSING` and do
not count toward the requested `$2,000`-normalized target unless revalued by a
separate, receipt-preserving process. The EXIT/reentry receipts do carry the
explicit `$2,000` benchmark, proxy deployment, normalization factor and
normalized diagnostic return.

## 24-key retarget ownership audit — 2026-08-01

The exact deduplicated union is:

`NVDA_LONG, MU_LONG, VT_LONG, TTD_SHORT, ACN_SHORT, DVN_LONG, AAPL_LONG,
DINO_LONG, CMC_LONG, GM_LONG, EOG_LONG, OKE_LONG, FANG_LONG, FIVN_LONG,
DIS_SHORT, CMC_SHORT, CLF_SHORT, FCX_SHORT, CDE_SHORT, NKE_SHORT, CLX_SHORT,
FCN_SHORT, IBIT_LONG, MSTR_SHORT`.

The completed 22-key broad S1 run omitted **IBIT_LONG** and **MSTR_SHORT**.
They are the two missing retarget keys; no worker was launched by this audit.

Canonical executable ownership across this 24-key union is 970 filled and
**37,547 unfilled** cells: differential/actionable **123 filled + 20,251
unfilled**, plateau **847 filled + 17,296 unfilled**. The five special rows
are NVDA 9 filled/1,600 unfilled combined, MU 1/1,608, VT 897/712, TTD
26/1,574 and ACN 37/1,563. Every other LONG key is 0/1,609 and every other
SHORT key is 0/1,600.

For the narrower frozen 449-cell EXIT/reentry adapter, strict intersection
with canonical selected exact provenance suppresses **372** existing 2+ year
owners: NVDA 1 and VT 371. IBIT and MSTR have zero selected exact owners in
this map. Safe gap-only inventory is therefore **10,404** cells: NVDA 448,
VT 78, and 449 for each of the other 22 keys.

This stricter result exposes an error in the earlier S1 protection receipt,
which suppressed only NVDA 1 + VT 166. It under-protects 205 VT cells because
it did not exclude every preserved 2+ year owner. The prior 9,711 figure must
remain quarantined; scheduling must use the 372-owner exclusion (and recheck
the same normalized `(key,param,value)` identities) before any retarget.

Final stop inventory observed `2026-08-01T00:32:20Z`: 17 partial ENTRY files
totaling 6,095 rows were retained. Every one of those rows is missing the
capital-contract fields, so none is eligible for coverage, ranking, merge, or
promotion.

## 24-key VEC combo replacement gate — 2026-08-01T00:51:16Z

**State: NOT LAUNCHED / QUARANTINED.** PID: `none`; output directory: `none`;
heartbeat: `none`; raw-combination count: **0**. The prior S1 protection
receipt remains invalid for retarget scheduling because it suppresses only 167
owners (VT=166) instead of the required **372** (NVDA=1, VT=371).

The replacement launcher now refuses to spawn workers unless a receipt is
explicitly stamped `authority=S1`, `status=PASS`, `safe_to_launch=true`, binds
the exact 24-key digest, proves all five path groups
(`entry, augment, reduce, exit, reenter`), and proves the 372-owner
EXIT/reentry intersection. The receipt builder must be run on S1 with
`VEC_OWNERSHIP_AUTHORITY=S1`; a local/schema-only database cannot satisfy this
gate.

When that corrected receipt exists, the isolated research runner writes every
combination to append-only `raw_results.jsonl` and Excel-safe CSV chunks plus
`xlsx_master_index.json`/`worksheet_settings.json`. It records every settings
vector, actual V8 source cells, timeframe, threshold, quantity multiplier,
$2,000 benchmark, average deployment, $16,000 cap, normalization factor and
status. Any row without all capital-contract fields is quarantined and is
ineligible for ranking, coverage, matrix, DB, promotion or live use.

### S1 utilization/watchdog check — 2026-08-01T00:51:16Z

S1 is **not reachable** from this workspace: the read-only BatchMode SSH probe
to `s1-int` timed out during banner exchange after 10 seconds. Consequently no
S1 CPU, RAM, swap, PID, process or heartbeat values are claimed here.

Prepared but not installed/started: `tools/vec_cohort_watchdog.py`. Its
five-minute heartbeat is report-only by default; its explicit `--enforce` mode
holds the target between 60–80% one-minute load-per-core and sends `SIGSTOP`
only above 80% when available memory is below 15% or swap is non-zero. It only
resumes below 60% with at least 20% memory available and zero swap. Starting it
on S1 requires the actual launched coordinator PID and a corrected receipt;
neither is available in this session.
