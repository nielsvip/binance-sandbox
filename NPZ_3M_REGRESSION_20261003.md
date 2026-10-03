# INCIDENT 2026-10-03 — NPZ 3m/5m regression + hours-per-sheet (USER: "SINCE WHEN?????")

## TL;DR (no lies, both directions)

- The 3m/5m SERIES are back in the NPZ *format* via `tools/precompute_v15.py`
  (Sep-19 fork of the v8 creator) which writes the SAME `backtest_v8/indicators/`
  path. The v8 creator (documented tool) is 15m+ only. Two writers, one dir.
- Whether S1 *data* has 3m CANNOT be determined from Mac (no shell/S1 here).
  Counter-evidence: the Oct-02 bt-emit note says S1 NPZs are "tradier-math on
  both venues" = v8-shaped (v15 crypto claims ez-math). The 3m threat may be
  forward-looking (someone runs v15) rather than landed.
- PROVEN landed in the last 3 days: v8 creator Oct-01 NPZW changes (W/M buckets
  resampled from 15m base + deeper-history synthesis + coverage guards) grow
  every regenerated NPZ; fleet engine swapped Oct-01 (5fe299c8→ecacb3be);
  throughput fell 451→326 sym_sides/day; means collapsed (UNI -69, XLM -54...).
- Hours-per-sheet is NOT proven to be NPZ-3m (30D sheets slice to 30d; size
  hits prepare/load/RAM, not per-cell evals). Ranked suspects + decisive
  commands below.

## Creator diff (by reading; git unavailable)

| | `backtest_v8_precompute.py` (root, documented) | `tools/precompute_v15.py` (Sep-19 fork) |
|---|---|---|
| Output | `backtest_v8/indicators/*.npz` | SAME PATH (`OUT_DIR`, :379; `--out_dir` override exists) |
| TFs crypto | 15m,1h,4h,D,W,M (15m base) | 3m(fab),15m,1h,4h,D,W,M (15m base) |
| TFs stocks | 15m,1h,4h,D,W,M (15m base) | 5m(fab),15m,1h,4h,D,W,M (15m base) |
| Fab machinery | PRESENT but DEAD (`fabricate_3m` no callers; "no 1/3/5m fabrication" :1824) | LIVE (tfs include 3m/5m; direction-forced WT fix :1813) |
| Indicator math | tradier_indicators both modes | tradier (stocks) + ez_indicators (crypto) — SAME keys, DIFFERENT values |
| Oct-01 changes | YES: NPZW W/M buckets, D-span coverage, lag markers, W/M SMA guards | None found (newest date refs: Sep-19 fork, Sep-03 contract) |
| Base bars | 15m (n) | 15m (n) — NOT 5x; stale "30000 3m bars" save-guard is Aug-13 leftover |

## Timeline ("since when")

- ~Mar-Apr: 3m/5m-base era (crypto 3m base, stocks 5m base).
- Apr-28: v8 structure 15m-base 15m+ (W/M comment); May-12 bug fix references
  the removed fabricated-base switch; May-30 lock row still lists
  `fabricate_3m, base_tf` as PRESERVED machinery. 15m+ regime ≈ May→Oct.
- Sep-19: v15 fork reintroduces 3m/5m arrays + ez-math crypto to the FORMAT.
- Oct-01: v8 NPZW lands (file growth); fleet engine 5fe299c8→ecacb3be; Oct-01→02
  report: n 451→326, crypto means collapse, HTF_GATE default-nonzero flags.
- Oct-02 18:05 bottom/top engine deploy; 23:50 selltop deploy (AFTER Oct-02 report).

## Hours-per-sheet suspects (ranked, honest)

1. NPZ growth × RAM: Oct-01 NPZW longer-history + W/M arrays; IOTXUSDT 288MB/store
   noted in code; `_MAX_CACHED` default 2 (herd sets 8); OOM-kill→retry cycles
   read as "hours". Fits the n-collapse with workers constant.
2. Oct-01 engine swap (5fe→ecac, content undated in visible lock log): new code
   paths per eval (bottom/top? MTF exits? BT family?) — needs S1 timing split.
3. Herd oversubscription / RED-retry / prefetch storms (secondary).
4. Recalc-on-flip (+1 eval/promotion, Oct-03, mine): negligible by construction.
NOT the cause of within-sheet HTF_GATE both-nonzero: prepared NPZ is RAM-pinned
per pilot, so NPZ regen cannot corrupt an in-flight sheet; that flag is a
pilot/eval-path determinism bug (baseline snapshot vs candidate assembly).

## Operator proof commands (S1, needs shell)

```bash
# 1. NPZ census: which files have 3m/5m, sizes, mtimes (THE regression test)
python3 - <<'EOF'
import numpy as np, glob, os, datetime
rows=[]
for p in sorted(glob.glob(os.path.expanduser('~/binance-sandbox/backtest_v8/indicators/*.npz'))):
    try:
        z=np.load(p,allow_pickle=True); keys=set(z.files)
        has3=any(k.endswith('_3m') for k in keys); has5=any(k.endswith('_5m') for k in keys)
        rows.append((os.path.basename(p),round(os.path.getsize(p)/1e6,1),
                     datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime('%m-%d %H:%M'),
                     len(keys),has3,has5))
    except Exception as e: rows.append((os.path.basename(p),'ERR',str(e)[:40],'','',''))
print(len(rows),'files')
print('WITH_3m:',sum(1 for r in rows if r[4]),' WITH_5m:',sum(1 for r in rows if r[5]))
import collections
print(collections.Counter(r[2][:5] for r in rows if isinstance(r[1],float)))
for r in sorted(rows,key=lambda r:r[1] if isinstance(r[1],float) else -1,reverse=True)[:15]: print(r)
EOF
# 2. Timing split: prepare/load vs per-cell eval (convict hours-per-sheet)
grep -h "prepare\|ALL_PREPARED\|0.07s\|per-row\|elapsed" /tmp/v15_*.log | tail -30
# 3. OOM evidence
dmesg | grep -i "killed process" | tail -5; grep -l "STALL\|BadZip\|OOM" /tmp/v15_*.log
# 4. Who wrote NPZs recently (mtime + creator logs)
ls -lat --time-style=full-iso ~/binance-sandbox/backtest_v8/indicators/ | head -15
grep -rl "precompute" ~/binance-sandbox/logs/ 2>/dev/null | head; crontab -l | grep -i "precompute\|npz"
```

## Containment (until census lands)

- DO NOT run either precompute on S1 (would mix generations mid-campaign).
- DO NOT delete/regenerate NPZs (destroys the evidence + in-flight baselines).
- If census shows v15-shaped files: quarantine list, re-freeze S1 from the last
  known-good set, single-writer rule (v8 only) going forward.
