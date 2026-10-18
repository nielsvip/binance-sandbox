#!/usr/bin/env python3
"""eval_cache_rebuild — rebuild eval_cache rows from the artifacts of every backtest (USER 2026-10-18).

USER: "eval_cache_v1.db is needed find it back and add it to the existing one on ext drive" / "if you cant restore
the 29.7gb you need to rebuild it from the logs and jsons and xlsx of every backtest".

WHAT WAS LOST AND WHAT WAS FOUND. S1's `data/eval_cache_v1.db` (29.7 GB, 927,777 rows, last written 2026-10-17 12:22)
was deleted on 2026-10-18 while reclaiming disk; no copy survived on that host and no process held it, so those exact
bytes are gone. Native rows that DID survive and are merged by `--merge-native`:
  * ~/eval_archive/eval_cache_v1_20261014.zst on S2 -> 45.58 GB, 1,539,007 rows, window 2026-10-13 02:42..10-14 19:05
  * 14 x ~/v15_bench/runs/*/*/eval_cache.db on S2 -> 172,088 rows (per-side gate/bench caches, same schema)
  * the Mac's own data/eval_cache_v1.db (1,540 rows) and /Volumes/E3T/mac_backup_20261014/.../eval_cache_v1.db (657)
The window the archive does NOT cover (2026-10-14 19:05 onward) is what this tool rebuilds from artifacts.

WHY A REBUILT ROW CAN NEVER MIS-HIT THE LIVE CACHE. `tools/v15_eval_cache.py` keys a row on sha1(sliced-NPZ array
bytes + symside + window + offset + account + mode + full base cfg + overrides + include_ledger + md5 of every engine
file) -- 40 hex chars. None of the artifacts carry the NPZ bytes or the engine md5 set, so a real key CANNOT be
reconstructed, and inventing one would be the exact failure that module was built to prevent (a wrong hit = fabricated
delta = live-money bug). Every row written here is therefore keyed `rebuilt1:<sha1>` (49 chars), which can never equal
a 40-char real key: `get()` computes the real sha1 from live inputs and will never match a rebuilt row. These rows are
a MINING corpus, not a speed cache.

WHY THEY ARE STILL THE RIGHT SHAPE. `tools/v17_priors_mine.py` -- the self-learning consumer -- never looks at `key`.
It reads `created` and json_extract(result, '$.symside' / '$.window_days' / '$.offset_days' / '$.bh_pct' / '$.bars'),
then the full `overrides` dict, and builds an edge from any two rows in one context whose dicts differ in exactly one
key. So a row is minable iff it carries that context, that dict, and the metrics -- which is exactly what is rebuilt.

PROVENANCE, NEVER GUESSWORK. Metrics (gain_pct, trades, tim_pct, max_dd_pct, pool_sharpe, wr_pct, valid) are copied
VERBATIM from the delta-log row that recorded the engine's answer; nothing is interpolated, averaged or inferred. The
override dict is REPLAYED, not measured: the pilot's greedy chain is itself logged (`ROW_DONE` carries promoted/
cum_after), so the set at any eval = board start set + every promoted (switch, cand) before it + this row's candidate
(+ the filter from a `KEY=VALUE` label). Each row records `rebuilt.base_source` ("progress_json" | "xlsx_col_c" |
"none") so a reader can see how complete its base was, and `rebuilt.src`/`line` point at the file and line it came
from. `fp` (the result's behavior_fingerprint) is carried through as recorded -- it fingerprints the RESULT, not the
inputs, so it is evidence of identical behaviour between two rows, never proof of a reconstructed config.

SIZE, AND WHY THE STORE IS SPLIT IN TWO. A real cache row averages 29.6 KB because `result` embeds the whole 851-key
dict (45.58 GB / 1.539 M rows). The delta logs hold 17,074,857 measured evals over 330 boards, so one blob per eval
would be 482 GB -- more than the drive. But the chain state only MOVES when a row is promoted (a few dozen times per
board), so the dict is written once and shared:
  * `bases`         base_id -> the full override dict at that point in the chain (~16 k rows for the whole fleet)
  * `evals_compact` one row per eval: base_id + the candidate (switch, cand) + the filter (key, value) + the metrics
                    + the context + provenance (~400 B), i.e. the complete 17 M corpus in ~8 GB, losing nothing the
                    logs recorded
  * `evals`         cache-schema rows (key/result/created) MATERIALIZED from bases+evals_compact on demand, so
                    `tools/v17_priors_mine.py --db <this>` runs unchanged against as much of the corpus as a drive
                    allows (`--materialize --max-gb`, newest first)
Nothing is thrown away to make this fit: `--materialize` can be re-run with a bigger budget, or on a subset, at any
time, because bases+evals_compact keep every eval.

usage:
  tools/eval_cache_rebuild.py --out /Volumes/TOSHIBA_EXT/binance_archive/eval_cache/eval_cache_rebuilt_v1.db \
      --logs '~/v15_run*/progress/v15_delta_log/*.jsonl' --progress-dir data/reports/lifecycle_pilot --xlsx-dir SPREADSHEETS/V15_V16_CELL_BY_CELL
  tools/eval_cache_rebuild.py --out X.db --since 2026-10-14T19:05:00Z        # only the window the archive misses
  tools/eval_cache_rebuild.py --materialize X.db --max-gb 60                 # fill cache-schema `evals`, newest first
  tools/eval_cache_rebuild.py --merge-native X.db --from A.db --from B.db    # union real rows, INSERT OR IGNORE
  tools/eval_cache_rebuild.py --audit X.db
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_LABELS_SKIP = ("BOARD_CTX", "ROW_DONE")
METRIC_KEYS = ("gain_pct", "trades", "tim", "max_dd_pct", "pool_sharpe", "wr_pct", "valid", "invalid_reason")
SCHEMA = ("CREATE TABLE IF NOT EXISTS evals (key TEXT PRIMARY KEY, result TEXT NOT NULL, created REAL, hits INT DEFAULT 0)",
          "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)",
          "CREATE TABLE IF NOT EXISTS bases (base_id TEXT PRIMARY KEY, symside TEXT, n_keys INT, source TEXT, overrides TEXT NOT NULL)",
          "CREATE TABLE IF NOT EXISTS evals_compact (ek TEXT PRIMARY KEY, base_id TEXT NOT NULL, symside TEXT, created REAL,"
          " switch TEXT, cand TEXT, filter_key TEXT, filter_val TEXT, ctx TEXT, metrics TEXT, prov TEXT)",
          "CREATE INDEX IF NOT EXISTS ix_compact_created ON evals_compact (created)",
          "CREATE INDEX IF NOT EXISTS ix_compact_symside ON evals_compact (symside)")


def connect(path, write=True):
    p = Path(path)
    if write:
        p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p) if write else f"file:{p}?mode=ro", uri=not write, timeout=600.0, isolation_level=None)
    if write:
        # Speed pragmas only: journal_mode needs exclusive access, and a concurrent READER (an audit or a row count
        # over a 45 GB file on USB) must not turn a merge into a crash. 2026-10-18: it did.
        for pragma in ("PRAGMA synchronous=OFF", "PRAGMA journal_mode=MEMORY"):
            try:
                c.execute(pragma)
            except sqlite3.OperationalError as exc:
                print(f"[cache] {pragma} skipped ({exc}) — continuing with the file's current settings", flush=True)
        for s in SCHEMA:
            c.execute(s)
    return c


def epoch_of(ts):
    try:
        return time.mktime(time.strptime(str(ts).replace("Z", ""), "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return None


def start_set_for(symside, progress_dir, xlsx_dir):
    """The board's base set: the pilot's own progress JSON first, then column C of its matrix. ({}, 'none') if neither."""
    for pat in (f"{symside}_v14_progress.json", f"{symside}*_v14_progress.json"):
        for f in sorted(glob.glob(str(Path(os.path.expanduser(progress_dir)) / pat)), key=os.path.getmtime, reverse=True):
            try:
                d = json.loads(Path(f).read_text())
            except Exception:
                continue
            for k in ("cumulative_overrides", "hustler_overrides", "overrides", "full_config"):
                v = d.get(k)
                if isinstance(v, dict) and v:
                    return dict(v), "progress_json"
    for f in sorted(glob.glob(str(Path(os.path.expanduser(xlsx_dir)) / f"{symside}_*matrix.xlsx")), key=os.path.getmtime, reverse=True):
        got = _xlsx_col_c(f)
        if got:
            return got, "xlsx_col_c"
    return {}, "none"


def _xlsx_col_c(path):
    """Column C of every switch sheet = the override the pilot wrote for that switch (BEST-C-FILL)."""
    try:
        import openpyxl
    except Exception:
        return {}
    out = {}
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return {}
    try:
        for ws in wb.worksheets:
            if "BASELINE_METRICS" in str(ws.title).upper():
                continue
            for row in ws.iter_rows(min_row=3, min_col=1, max_col=3, values_only=True):
                sw, _b, c = (row + (None, None, None))[:3]
                if sw and c not in (None, ""):
                    out.setdefault(str(sw).strip(), c)
    except Exception:
        pass
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return out


def parse_filter_label(label):
    """A delta-log label is either a row kind ('naked', 'SACRED_REPASS', ...) or the yellow filter 'KEY=VALUE'."""
    s = str(label or "")
    if "=" not in s:
        return None, None
    k, v = s.split("=", 1)
    return k.strip(), v.strip()


def coerce(v):
    s = str(v)
    if s in ("True", "False"):
        return s == "True"
    try:
        return int(s) if s.lstrip("-").isdigit() else float(s)
    except Exception:
        return v


def base_id_of(symside, d):
    return hashlib.sha1((str(symside) + "|" + json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)).encode()).hexdigest()


def rebuild_file(path, cx, base, base_source, since_epoch, ctx_default, counters, max_bytes, written_bytes):
    """Stream one delta log, replaying the greedy chain, writing one COMPACT row per measured eval + its shared base."""
    cur = dict(base)
    ctx = dict(ctx_default)
    batch, bbatch = [], []
    seen_bases = set()
    symside_hint = Path(path).name.split("_jump")[0].replace(".jsonl", "")
    with open(path, errors="ignore") as fh:
        for ln, line in enumerate(fh, 1):
            if '"label"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                counters["malformed"] += 1
                continue
            label = d.get("label")
            if label == "BOARD_CTX":
                ctx = {"bh_pct": d.get("bh"), "baseline_gain": d.get("baseline_gain"), "round": d.get("round"),
                       "cat_side": d.get("cat_side"), "tier": d.get("tier"), "n_overrides": d.get("n_overrides"),
                       "start_fp": d.get("start_fp"), "window_days": d.get("window_days")}
                continue
            if label == "ROW_DONE":
                if d.get("promoted") and d.get("switch"):
                    cur[str(d["switch"])] = coerce(d.get("cand"))
                    counters["promoted_applied"] += 1
                continue
            if d.get("gain_pct") is None:
                continue
            ts = d.get("ts")
            created = epoch_of(ts)
            if since_epoch and (created or 0) < since_epoch:
                counters["before_since"] += 1
                continue
            symside = d.get("sym_side") or symside_hint
            bid = base_id_of(symside, cur)
            if bid not in seen_bases:
                seen_bases.add(bid)
                bbatch.append((bid, symside, len(cur), base_source, json.dumps(cur, sort_keys=True, separators=(",", ":"), default=str)))
                counters["bases"] += 1
            sw, cand = d.get("switch"), d.get("cand")
            fk, fv = parse_filter_label(label)
            ctxj = json.dumps({"window_days": d.get("window_days") or ctx.get("window_days"), "offset_days": d.get("offset_days", 0),
                               "bh_pct": ctx.get("bh_pct"), "bars": d.get("bars"), "cat_side": ctx.get("cat_side"),
                               "tier": ctx.get("tier"), "round": ctx.get("round")}, separators=(",", ":"), default=str)
            metj = json.dumps({"gain_pct": d.get("gain_pct"), "trades": d.get("trades"), "tim_pct": d.get("tim"),
                               "max_dd_pct": d.get("max_dd_pct"), "pool_sharpe": d.get("pool_sharpe"), "wr_pct": d.get("wr_pct"),
                               "valid": d.get("valid"), "invalid_reason": d.get("invalid_reason"),
                               "behavior_fingerprint": d.get("fp")}, separators=(",", ":"), default=str)
            provj = json.dumps({"src": str(path), "line": ln, "ts": ts, "label": label, "sheet": d.get("sheet"),
                                "row": d.get("row"), "nav": d.get("nav"), "cum_before": d.get("cum_before"),
                                "delta": d.get("delta"), "secs": d.get("secs"), "cached_when_logged": d.get("cached"),
                                "fn": d.get("fn"), "base_source": base_source,
                                "metrics": "verbatim from the delta log", "overrides": "replayed greedy chain",
                                "tool": "tools/eval_cache_rebuild.py"}, separators=(",", ":"), default=str)
            ek = "rebuilt1:" + hashlib.sha1(f"{path}|{ln}|{ts}|{symside}|{d.get('sheet')}|{d.get('row')}|{sw}|{cand}|{label}".encode()).hexdigest()
            batch.append((ek, bid, symside, created, (str(sw) if sw else None), (str(cand) if cand not in (None, "") else None),
                          fk, fv, ctxj, metj, provj))
            written_bytes[0] += len(ctxj) + len(metj) + len(provj) + 120
            counters["rows"] += 1
            if len(batch) >= 5000:
                cx.executemany("INSERT OR IGNORE INTO evals_compact (ek, base_id, symside, created, switch, cand, filter_key, filter_val, ctx, metrics, prov)"
                               " VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
            if bbatch:
                cx.executemany("INSERT OR IGNORE INTO bases (base_id, symside, n_keys, source, overrides) VALUES (?,?,?,?,?)", bbatch)
                bbatch.clear()
            if max_bytes and written_bytes[0] >= max_bytes:
                counters["stopped_on_max_gb"] += 1
                break
    if batch:
        cx.executemany("INSERT OR IGNORE INTO evals_compact (ek, base_id, symside, created, switch, cand, filter_key, filter_val, ctx, metrics, prov)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
    if bbatch:
        cx.executemany("INSERT OR IGNORE INTO bases (base_id, symside, n_keys, source, overrides) VALUES (?,?,?,?,?)", bbatch)


def do_rebuild(a):
    cx = connect(a.out)
    files = []
    for g in a.logs:
        files.extend(glob.glob(os.path.expanduser(g)))
    files = sorted(set(files), key=os.path.getmtime, reverse=True)
    if not files:
        sys.exit(f"[rebuild] no delta logs matched {a.logs}")
    since_epoch = epoch_of(a.since) if a.since else None
    counters = {"rows": 0, "malformed": 0, "promoted_applied": 0, "before_since": 0, "stopped_on_max_gb": 0, "files": 0,
                "bases": 0, "base_progress_json": 0, "base_xlsx_col_c": 0, "base_none": 0}
    written = [0]
    max_bytes = int(float(a.max_gb) * (1 << 30)) if a.max_gb else 0
    t0 = time.time()
    bases = {}
    for f in files:
        symside = Path(f).name.split("_jump")[0].replace(".jsonl", "")
        if symside not in bases:
            bases[symside] = start_set_for(symside, a.progress_dir, a.xlsx_dir)
        base, src = bases[symside]
        counters[f"base_{src}"] += 1
        rebuild_file(f, cx, base, src, since_epoch, {}, counters, max_bytes, written)
        counters["files"] += 1
        if counters["files"] % 20 == 0:
            print(f"[rebuild] {counters['files']}/{len(files)} files · {counters['rows']:,} rows · {written[0]/(1<<30):.2f} GB · {time.time()-t0:.0f}s", flush=True)
        if counters["stopped_on_max_gb"]:
            print(f"[rebuild] STOP: --max-gb {a.max_gb} reached", flush=True)
            break
    cx.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?,?)",
               ("rebuilt", json.dumps({"tool": "tools/eval_cache_rebuild.py", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                       "host": os.uname().nodename, "counters": counters, "since": a.since,
                                       "key_prefix": "rebuilt1: (49 chars) — can never collide with a real 40-hex cache key",
                                       "metrics": "verbatim from v15_delta_log rows", "overrides": "replayed from the logged greedy chain",
                                       "logs": a.logs, "progress_dir": a.progress_dir, "xlsx_dir": a.xlsx_dir})))
    n = cx.execute("SELECT count(*) FROM evals_compact").fetchone()[0]
    nb = cx.execute("SELECT count(*) FROM bases").fetchone()[0]
    print(f"[rebuild] DONE {counters['rows']:,} evals written · {n:,} in db · {nb:,} shared bases · {time.time()-t0:.0f}s")
    print(f"[rebuild] base sources: progress_json {counters['base_progress_json']} · xlsx_col_c {counters['base_xlsx_col_c']} · none {counters['base_none']}")
    print(f"[rebuild] next: --materialize {a.out} --max-gb N   (fills cache-schema `evals` for v17_priors_mine, newest first)")
    cx.close()


def do_materialize(a):
    """Expand compact rows into cache-schema `evals` rows (newest first) until --max-gb, so v17_priors_mine runs unchanged."""
    cx = connect(a.materialize)
    budget = int(float(a.max_gb) * (1 << 30)) if a.max_gb else 0
    if a.fresh:
        cx.execute("DELETE FROM evals WHERE key LIKE 'rebuilt1:%'")
    bases = {}
    written = n = 0
    t0 = time.time()
    batch = []
    q = "SELECT ek, base_id, symside, created, switch, cand, filter_key, filter_val, ctx, metrics, prov FROM evals_compact"
    if a.since:
        q += f" WHERE created >= {epoch_of(a.since) or 0}"
    q += " ORDER BY created DESC"
    for ek, bid, symside, created, sw, cand, fk, fv, ctxj, metj, provj in cx.execute(q):
        if bid not in bases:
            row = cx.execute("SELECT overrides FROM bases WHERE base_id = ?", (bid,)).fetchone()
            bases[bid] = json.loads(row[0]) if row else {}
            if len(bases) > 4000:
                bases.clear()
                bases[bid] = json.loads(row[0]) if row else {}
        ov = dict(bases[bid])
        if sw and sw != "BEST_START" and cand not in (None, ""):
            ov[sw] = coerce(cand)
        if fk:
            ov[fk] = coerce(fv)
        ctx, met, prov = json.loads(ctxj), json.loads(metj), json.loads(provj)
        result = dict(met)
        result.update({"symside": symside, "window_days": ctx.get("window_days"), "offset_days": ctx.get("offset_days", 0),
                       "bh_pct": ctx.get("bh_pct"), "bars": ctx.get("bars"), "overrides": ov,
                       "rebuilt": dict(prov, switch=sw, cand=cand, filter_key=fk, filter_val=fv,
                                       cat_side=ctx.get("cat_side"), tier=ctx.get("tier"), round=ctx.get("round"),
                                       base_id=bid, base_keys=len(bases[bid]))})
        blob = json.dumps(result, separators=(",", ":"), default=str)
        batch.append((ek, blob, created))
        written += len(blob) + len(ek)
        n += 1
        if len(batch) >= 1000:
            cx.executemany("INSERT OR REPLACE INTO evals (key, result, created) VALUES (?,?,?)", batch)
            batch.clear()
            if n % 50000 == 0:
                print(f"[materialize] {n:,} rows · {written/(1<<30):.2f} GB · {time.time()-t0:.0f}s", flush=True)
        if budget and written >= budget:
            print(f"[materialize] STOP at --max-gb {a.max_gb}")
            break
    if batch:
        cx.executemany("INSERT OR REPLACE INTO evals (key, result, created) VALUES (?,?,?)", batch)
    total = cx.execute("SELECT count(*) FROM evals").fetchone()[0]
    print(f"[materialize] DONE {n:,} rows ({written/(1<<30):.2f} GB) · evals table now {total:,} rows · {time.time()-t0:.0f}s")
    cx.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?,?)",
               ("materialized", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": n,
                                            "bytes": written, "since": a.since, "max_gb": a.max_gb,
                                            "order": "created DESC (newest evals first)"})))
    cx.close()


def do_merge(a):
    cx = connect(a.merge_native)
    before = cx.execute("SELECT count(*) FROM evals").fetchone()[0]
    total_added = 0
    for src in a.from_db:
        for p in sorted(glob.glob(os.path.expanduser(src))):
            try:
                cx.execute("ATTACH DATABASE ? AS s", (f"file:{p}?mode=ro",))
            except Exception as exc:
                print(f"[merge] skip {p}: {exc}")
                continue
            try:
                n0 = cx.execute("SELECT count(*) FROM evals").fetchone()[0]
                cx.execute("INSERT OR IGNORE INTO evals (key, result, created, hits) SELECT key, result, created, hits FROM s.evals")
                n1 = cx.execute("SELECT count(*) FROM evals").fetchone()[0]
                total_added += n1 - n0
                print(f"[merge] +{n1-n0:,} from {p}")
            except Exception as exc:
                print(f"[merge] FAILED {p}: {exc}")
            finally:
                cx.execute("DETACH DATABASE s")
    after = cx.execute("SELECT count(*) FROM evals").fetchone()[0]
    print(f"[merge] {before:,} -> {after:,} rows (+{total_added:,})")
    cx.close()


def do_audit(a):
    cx = connect(a.audit, write=False)
    n = cx.execute("SELECT count(*) FROM evals").fetchone()[0]
    reb = cx.execute("SELECT count(*) FROM evals WHERE key LIKE 'rebuilt1:%'").fetchone()[0]
    mn, mx = cx.execute("SELECT min(created), max(created) FROM evals").fetchone()
    f = lambda t: time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) if t else "?"
    print(f"{a.audit}\n  evals rows {n:,} (real {n-reb:,} · rebuilt {reb:,}) · created {f(mn)} -> {f(mx)}")
    for t in ("evals_compact", "bases"):
        try:
            c2 = cx.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            extra = ""
            if t == "evals_compact":
                a2, b2 = cx.execute("SELECT min(created), max(created) FROM evals_compact").fetchone()
                extra = f" · {f(a2)} -> {f(b2)}"
            print(f"  {t}: {c2:,} rows{extra}")
        except Exception:
            print(f"  {t}: absent")
    try:
        print("  meta:", dict(cx.execute("SELECT k, v FROM meta").fetchall()).get("rebuilt", "")[:300])
    except Exception:
        pass
    bad = cx.execute("SELECT count(*) FROM evals WHERE NOT json_valid(result)").fetchone()[0]
    ctx = cx.execute("SELECT count(*) FROM evals WHERE json_extract(result,'$.symside') IS NULL").fetchone()[0]
    print(f"  malformed result json: {bad} · rows with no symside context (unminable): {ctx}")
    cx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--logs", action="append", default=[])
    ap.add_argument("--progress-dir", default=str(ROOT / "data" / "reports" / "lifecycle_pilot"))
    ap.add_argument("--xlsx-dir", default=str(ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"))
    ap.add_argument("--since", default="", help="ISO ts: only evals at/after this (e.g. the end of the native archive)")
    ap.add_argument("--max-gb", type=float, default=60.0, help="stop before the drive fills; 0 = no limit")
    ap.add_argument("--merge-native")
    ap.add_argument("--from", dest="from_db", action="append", default=[])
    ap.add_argument("--materialize", help="expand compact rows into cache-schema `evals` (newest first)")
    ap.add_argument("--fresh", action="store_true", help="with --materialize: drop existing rebuilt1: rows first")
    ap.add_argument("--audit")
    a = ap.parse_args()
    if a.audit:
        return do_audit(a)
    if a.materialize:
        return do_materialize(a)
    if a.merge_native:
        return do_merge(a)
    if not a.out:
        sys.exit("need --out (rebuild), --merge-native, or --audit")
    if not a.logs:
        a.logs = ["~/v15_run*/progress/v15_delta_log/*.jsonl", "~/v15_run*/v15_delta_log/*.jsonl"]
    do_rebuild(a)


if __name__ == "__main__":
    main()
