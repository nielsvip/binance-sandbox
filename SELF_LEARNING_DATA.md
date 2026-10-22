# SELF_LEARNING_DATA.md — where v15_pilot's self-learning finds its corpora

**USER 2026-10-18:** *"you can leave older backtest results on external drive but document clearly where v15_pilot's
self learning should find it"* and *"eval_cache_v1.db is needed find it back and add it to the existing one on ext
drive"* / *"if you cant restore the 29.7gb you need to rebuild it from the logs and jsons and xlsx of every backtest"*.

Machine-readable form: `python3 tools/data_locations.py --where <kind> --host <host>` and `--audit --kind <kind>`.
Every path below is a declared kind in that registry, so nothing here has to be guessed or grepped for again.

---

## 1. The six things v15_pilot learns from

`v15_pilot.py` imports exactly six learning consumers. Each one, its corpus, and the env var that moves that corpus:

| consumer (in `v15_pilot.py`) | corpus it reads | registry kind | env override |
|---|---|---|---|
| `tools.v15_brain.PilotBrain` (line 4690) | `~/v15_run*/[progress/]v15_delta_log/*.jsonl` | `brain_corpus_delta_logs` | **none — see §2** |
| ↳ its built models | `data/brain/brain_<CAT_SIDE>.json` | `brain_models` | none |
| `tools.v17_priors.load` (4130) | `data/causal/v17_priors.json` | `priors_causal` | `V17_PRIORS_PATH` |
| ↳ mined from | `data/eval_cache_v1.db` by `tools/v17_priors_mine.py` | `eval_cache` | `V15_EVAL_CACHE_DB` |
| `tools.v17_combo_engine` (7748) | `data/v17_combo_library.sqlite` | `combo_library` | `V17_COMBO_LIB` |
| `tools.v15_graph_search` (5467) | `data/encyclopedia_v2/graph.json` | `knowledge_graph` | none |
| `tools.v15_endgame_knowledge_merge.load_ev` (4038) | `data/endgame/endgame_knowledge_fleet.json` | `endgame_knowledge` | `V15_ENDGAME_KNOWLEDGE` |
| `tools.v15_ranking_priority` (3942) | `data/rankings_qualified_{crypto,stocks}.json` | (pushed by `ez_rankings_push_s1.sh`) | none |
| `tools/v15_causal_learner.py` | `data/per_trade_returns/*_<day>.jsonl` → `data/causal_learner.db` | `per_trade_returns`, `causal_learner_db` | none |

---

## 2. The one corpus archiving can silently starve: the brain's delta logs

`tools/v15_brain.py` has `DEFAULT_GLOBS = ["~/v15_run*/progress/v15_delta_log/*.jsonl", "~/v15_run*/v15_delta_log/*.jsonl"]`
— **hardcoded, with no env override**, and `tools/v15_brain_export.sh` (cron `20 3 * * *` on **S2**, models then rsynced
to S1) calls it with no `--glob`. So a run dir that moves to an external drive simply stops being learned from, with no
error anywhere.

**It filters by ROW timestamp, not file mtime**, over the last `V15_BRAIN_SINCE_D` days (default **14**). Measured on S2
2026-10-18: the corpus is **9.7 GB in 372 files across 330 boards**, of which only **0.1 GB is outside the 14-day
window** — the active run dir `v15_run32_20261011` holds **9.61 GB** and every byte of it is in scope.

**Retention contract:**

* **Never archive the active run dir.** File mtimes there are fresh but the rows inside span the whole run; an
  mtime-based or directory-based sweep would delete rows the brain still reads.
* **Archive a run dir only when** it is finished **and** no row in it is younger than `V15_BRAIN_SINCE_D` + a margin.
  On S2 today that is 0.1 GB — archiving the learning corpus buys almost nothing. Archive the **boards** (xlsx/html)
  and per-row progress JSON instead; the brain never reads those.
* **To retrain over a longer history than the hosts keep**, point the brain at the archive explicitly — it cannot find
  it by itself:
  ```bash
  python3 tools/v15_brain.py export --since 2026-08-01 --out-dir data/brain \
      --glob '/Volumes/TOSHIBA_EXT/fleet_archive/*/v15_run*/progress/v15_delta_log/*.jsonl' \
      --glob '~/v15_run*/progress/v15_delta_log/*.jsonl'
  ```
  The external drives are **Mac-mounted only** — S1 and S2 cannot see them, so a longer retrain runs on the Mac (or the
  archive is pulled back to a host first).

---

## 3. eval_cache_v1.db — what was lost, what was found, what was rebuilt

S1's `~/binance-sandbox/data/eval_cache_v1.db` (**29.7 GB, 927,777 rows**, last written 2026-10-17 12:22, cold 33 h,
champion env `V15_EVAL_CACHE=0`) was **deleted on 2026-10-18** while reclaiming disk on a host that was at 97% with
11 GB free. No copy remained on S1 and no process held the file open, so **those exact bytes are not recoverable**.

### 3a. Native rows recovered (real 40-hex keys) → the ext-drive master

`/Volumes/TOSHIBA_EXT/binance_archive/eval_cache/eval_cache_v1_master.db`

| source | rows | note |
|---|---|---|
| `s2:~/eval_archive/eval_cache_v1_20261014.zst` | **1,539,007** | 481 MB zstd → 45.58 GB; window 2026-10-13 02:42 → 10-14 19:05 |
| `s2:~/v15_bench/runs/*/*/eval_cache.db` (14 dbs) | **166,110** unique | per-side gate/bench caches, identical schema |
| Mac `data/eval_cache_v1.db` | 1,540 | |
| `/Volumes/E3T/mac_backup_20261014/data/eval_cache_v1.db` | 657 | the pre-existing "one on ext drive" |

Compressed copies kept beside it: `eval_cache_v1_20261014.zst`, `bench_merge.db.zst`.
Merge more at any time: `tools/eval_cache_rebuild.py --merge-native <master> --from <db> [--from ...]` (INSERT OR IGNORE;
identical keys are identical results by construction).

### 3b. The gap, rebuilt from the artifacts → `eval_cache_rebuilt_v1.db`

The archive stops at 2026-10-14 19:05. Everything after it was rebuilt by **`tools/eval_cache_rebuild.py`** from the
**logs, JSONs and xlsx of every backtest**: metrics copied **verbatim** from `v15_delta_log` rows, override dicts
**replayed** from the greedy chain the pilot itself logs (`ROW_DONE.promoted` / `cum_after`), with the board's base set
taken from `*_v14_progress.json` or, failing that, **column C of its `*_matrix.xlsx`**. Census of what is available:
**17,074,857 measured evals** over 330 boards (19,852,827 log lines).

Store layout (one 851-key dict is ~29.6 KB, so 17 M separate blobs would be 482 GB — the dict is written once and shared):

* `bases` — `base_id` → the full override dict at that point in the chain (~16 k rows fleet-wide)
* `evals_compact` — one ~400 B row per eval: `base_id` + candidate + filter + metrics + context + provenance.
  **The complete 17 M corpus, losing nothing the logs recorded.**
* `evals` — cache-schema rows materialized on demand so **`tools/v17_priors_mine.py --db <this>` runs unchanged**:
  `tools/eval_cache_rebuild.py --materialize <db> --max-gb 60` (newest first; re-runnable with any budget).

**A rebuilt row can never mis-hit the live cache.** `tools/v15_eval_cache.py` keys on
`sha1(sliced-NPZ bytes + symside + window + offset + account + mode + full base cfg + overrides + include_ledger +
md5 of every engine file)` = 40 hex chars. The artifacts carry neither the NPZ bytes nor the engine md5 set, so a real
key cannot be reconstructed and is never faked: every rebuilt key is `rebuilt1:<sha1>` (**49 chars**), which cannot
equal a 40-char key. `get()` computes the real sha1 from live inputs and will never match one. These rows are a
**mining corpus, not a speed cache**.

Validated end-to-end on one real board (`XLE_LONG`, 32,725 evals, base recovered from its xlsx): `v17_priors_mine`
produced **5,993 edges, 833 quartets, 668 single-key effects, 261 filter-blocks-switch entries**, and carried the
`rebuilt` provenance into its own output so anything learned from a rebuild is self-labelled.

### 3c. The experience store (work item E2, 2026-10-20) -> `data/experience/`

`tools/v18_experience.py` condenses the evidence above into one append-only SQLite shard per cat_side per month
(`experience_<CRYPTO_LONG|CRYPTO_SHORT|STOCKS_LONG|STOCKS_SHORT>_<YYYYMM>.db`) plus `experience_index.db` (watermarks, ingest
runs, proofs, cold copies). A set is stored once (`sets`), every eval is a ~0.3-0.7 KB row pointing at `base_set_id` + delta; the
cat_sides are never mixed in a file. Registered kinds in `tools/data_locations.py`: `experience_store` (Mac `data/experience`,
env `V18_EXPERIENCE_ROOT`) and `eval_cache_cold` (zst + `.manifest.json` with sha256). Sources ingested: S2 `rebuilt_v1.db`, the 14 staged
bench caches, a snapshot of the S2 live cache, the 2026-10-14 zst archive (1,539,007 native rows), S1/S2 `v15_run33` delta logs
(identity lines `cached: true`, delta 0 collapsed to one `settlements` row per board row, every collapsed line listed),
`/history` with the era cut (crypto >= 2026-10-14T13:00Z, stocks >= 2026-10-17T13:00Z; broker echoes dropped and counted),
boards (xlsx + progress JSON; `ts_kind` says what each date is) and ledger-replay trades. `prove` re-reads a source and compares it
row by row with the store; `reclaim` refuses unless a proof shows 0 missing AND a sha256-verified cold copy sits on a real ext mount.
Numbers per source and per shard: `docs/v15_pending/19_E2_EXPERIENCE_STORE_HANDOVER_20261020.md`, `data/reports/experience_audit_20261020.json`.

---

## 4. Where older backtest results may live

External drives are **Mac-mounted only** (`EXT_DRIVES` in `tools/data_locations.py`). Free space 2026-10-18:
**TOSHIBA_EXT 183 GB**, E3T 19 GB, **SSD2T 0 GB (full)** — so new archives go to **TOSHIBA_EXT**.

| path | holds |
|---|---|
| `{ext}/fleet_archive/<HOST>_offload_<stamp>/` | finished run trees, with `archive.log` |
| `{ext}/binance_archive/eval_cache/` | the eval-cache master, the rebuild, the compressed snapshots |
| `{ext}/binance_archive/klines_archive/` | append-only kline backup |
| `{ext}/v15_mega_progress_mirror/` | `MEGA_{crypto,stocks}_jump` boards |
| `{ext}/s1_daily_reports_20261013/` | per-day S1 report trees |

**Rules.** (1) Nothing in §2's 14-day window leaves a host. (2) An archived run is reachable by the brain only through
an explicit `--glob`. (3) `ez_disk_cleanup.py` is scoped to `~/binance` (BASE_PATH) and **cannot see
`~/binance-sandbox`**, where the fleet writes — it freed 94 MB on a host that was 6 GB from idling the whole fleet;
`tools/s2_disk_guard.sh` is the sandbox-aware guard and now runs on **S1 too** (cron `*/15`, `S2_GUARD_GATE_HOST=10.0.0.4`
so its eval-cache rule can read the champion env off-host).

See also: [`BACKTEST_BIBLE.md`](BACKTEST_BIBLE.md), [`INFRASTRUCTURE.md`](INFRASTRUCTURE.md),
`tools/data_locations.py --audit`.

- 2026-10-22 (USER: S5/S6 are storage until deleted after the 2026-10-24 weekend): scratch roots `rebuild_scratch_s5` (S2 `v15_run33_20261019` + `eval_archive`, both proven in the experience store) and `rebuild_scratch_s6` (S1 `_archive` + backups >14 d) are registered in `tools/data_locations.py` (`--where rebuild_scratch_s5 --host s5`). Everything there must reach an ext drive or be proven duplicate before S5/S6 are deleted. Report: `docs/v15_pending/22_DISK_RECLAIM_20261022.md`.
- 2026-10-22 Hetzner snapshots (v20-absorb): s5 s6 — ids in data/reports/hetzner_snapshots.jsonl (USER 2026-10-22: snapshot before condensing; keep until a later upgrade no longer needs the raw corpus)
