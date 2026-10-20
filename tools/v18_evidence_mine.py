#!/usr/bin/env python3
"""v18_evidence_mine — mine the hot v15 delta logs (bible §20, every real engine eval) into per-cat_side evidence for the
V18 learners: winners pack (doc 17 §3.5), repair-menu companions (§3.2), filter-veto map (§5 ABL-0) and coverage (§3.6).

Runs ON THE HOST THAT HOLDS THE LOGS (S2: its own run dirs + a read-only copy of S1's run33 logs in ~/v18_scratch), stdlib
only, nice 10, a bounded number of worker processes. Output is small aggregate JSON per cat_side; the Mac learners read it.

WHAT IS COUNTED (NO-LIES: every count is a logged engine answer; nothing is estimated)
  naked   key `SWITCH=cand`               main-pass `naked` records, closed by their ROW_DONE (exactly v15_brain.iter_rows)
  pair    key `SWITCH=cand|FILTER=opt`     the yellow cell of that row: d = gain - cum_before (the path's value vs the chain),
                                          m = d - naked d (the filter's marginal on that row)
  abl     (SWITCH=cand, FILTER)           V17 ablation records of a board: ABL_DISCOVER d on the strip base, ABL_RESTORE1 gain
                                          of strip+F, ABL_ATTR gain of strip+F+switch -> eff = ATTR - RESTORE1; killer when
                                          eff < 0.5 * d (the pilot's own v17_killers rule); ABL_RESTORE / ABL_EXEMPT / ABL_PROXY
                                          deltas vs the full chain are the measured remedy outcomes
  forward FORWARD / HOLDOUT records (none exist in the 2026-10-19 logs: holdout is OFF in production; counted when present)
Unit of evidence = (cat_side, key, sym_side), LAST measurement wins (tools/v15_switch_causal_profile convention), so a
sym_side re-tested by several boards counts once; `n` keeps the raw number of evals (fresh + cached) and `nf` the fresh ones.
Cached records are kept: a cached record is the engine's answer for an identical materialised set (an inert switch reads
delta 0 = "not positive on this sym_side"), which is exactly what P(pos) must see. Window: records with ts >= --since.

Copies are not double counted: a file whose basename + first line equal another file's is the same log (kept: the longer).
The S2 rebuilt corpus (~/eval_archive/rebuilt_v1.db) is replayed from these same S2 log files (its `prov.src`); with
--rebuilt, rows are used ONLY when their src log no longer exists on this host (else the log is the source).

usage (S2)
  nohup nice -n 10 python3 tools/v18_evidence_mine.py --src s2='~/v15_run3[23]_*/progress/v15_delta_log/*.jsonl' \
      --src s1='~/v18_scratch/s1_run33_delta_log/*.jsonl' --rebuilt ~/eval_archive/rebuilt_v1.db --workers 3 \
      --out-dir ~/v18_learn/evidence --scratch ~/v18_scratch/mine > ~/logs/v18_evidence_mine.log 2>&1 < /dev/null &
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

EPS = 1e-9
CRYPTO_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD")
DENY = ("MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED")
KILL_FRAC = float(os.environ.get("V15_V17_ABL_KILL_FRAC", "0.5"))
SKIP_PREFIX = ("FINAL_", "DIAG_", "REPAIR365", "SACRED", "COARSE", "JOINT:", "STANDALONE:", "REF_BASELINE", "RECALC", "ENDGAME",
               "FOCUS", "ABL_", "V17_", "SURGEON", "COMBO", "REGIME", "BEST_START")


def utc(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def cat_side_of(sym_side):
    s = str(sym_side or "")
    if "_" not in s:
        return None
    sym, side = s.rsplit("_", 1)
    if side not in ("LONG", "SHORT") or not sym:
        return None
    return ("CRYPTO" if sym.upper().endswith(CRYPTO_QUOTES) else "STOCKS") + "_" + side


def sym_side_of_path(path):
    name = os.path.basename(path).replace(".jsonl", "")
    ss = name.split("_jump")[0].split("_sequential")[0]
    if not ss.endswith(("_LONG", "_SHORT")):
        ss = "_".join(name.split("_")[:2])
    return ss


def _f(x):
    try:
        return None if x is None or isinstance(x, bool) else float(x)
    except (TypeError, ValueError):
        return None


DDL = (
    "CREATE TABLE IF NOT EXISTS obs (cs TEXT, key TEXT, sym TEXT, ts TEXT, d REAL, m REAL, valid INT, prom INT, n INT, nf INT, nb INT,"
    " PRIMARY KEY (cs, key, sym)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS abl (cs TEXT, sw TEXT, f TEXT, sym TEXT, ts TEXT, grp TEXT, disc_d REAL, eff REAL, killer INT,"
    " restore_d REAL, exempt_d REAL, proxy_d REAL, proxy_has_f INT, n INT, PRIMARY KEY (cs, sw, f, sym)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS disc (cs TEXT, sw TEXT, sym TEXT, ts TEXT, grp TEXT, disc_d REAL, n_attr INT, restore_d REAL,"
    " exempt_d REAL, proxy_d REAL, n INT, PRIMARY KEY (cs, sw, sym)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS fwd (cs TEXT, key TEXT, sym TEXT, ts TEXT, d REAL, kind TEXT, PRIMARY KEY (cs, key, sym, ts, kind)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS boards (path TEXT, board_no INT, cs TEXT, sym TEXT, ts TEXT, tier TEXT, bh REAL, baseline REAL,"
    " round TEXT, PRIMARY KEY (path, board_no))",
    "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, host TEXT, sym TEXT, cs TEXT, bytes INT, lines INT, used INT,"
    " first_ts TEXT, last_ts TEXT, counters TEXT)",
)
UPSERT_OBS = ("INSERT INTO obs (cs, key, sym, ts, d, m, valid, prom, n, nf, nb) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
              "ON CONFLICT (cs, key, sym) DO UPDATE SET n = n + excluded.n, nf = nf + excluded.nf, nb = nb + excluded.nb, "
              "prom = MAX(prom, excluded.prom), "
              "d = CASE WHEN excluded.ts >= ts THEN excluded.d ELSE d END, m = CASE WHEN excluded.ts >= ts THEN excluded.m ELSE m END, "
              "valid = CASE WHEN excluded.ts >= ts THEN excluded.valid ELSE valid END, ts = MAX(ts, excluded.ts)")
UPSERT_ABL = ("INSERT INTO abl (cs, sw, f, sym, ts, grp, disc_d, eff, killer, restore_d, exempt_d, proxy_d, proxy_has_f, n) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (cs, sw, f, sym) DO UPDATE SET n = n + excluded.n, "
              "grp = CASE WHEN excluded.ts >= ts THEN excluded.grp ELSE grp END, "
              "disc_d = CASE WHEN excluded.ts >= ts THEN excluded.disc_d ELSE disc_d END, "
              "eff = CASE WHEN excluded.ts >= ts THEN excluded.eff ELSE eff END, "
              "killer = CASE WHEN excluded.ts >= ts THEN excluded.killer ELSE killer END, "
              "restore_d = CASE WHEN excluded.ts >= ts THEN excluded.restore_d ELSE restore_d END, "
              "exempt_d = CASE WHEN excluded.ts >= ts THEN excluded.exempt_d ELSE exempt_d END, "
              "proxy_d = CASE WHEN excluded.ts >= ts THEN excluded.proxy_d ELSE proxy_d END, "
              "proxy_has_f = CASE WHEN excluded.ts >= ts THEN excluded.proxy_has_f ELSE proxy_has_f END, ts = MAX(ts, excluded.ts)")
UPSERT_DISC = ("INSERT INTO disc (cs, sw, sym, ts, grp, disc_d, n_attr, restore_d, exempt_d, proxy_d, n) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
               "ON CONFLICT (cs, sw, sym) DO UPDATE SET n = n + excluded.n, "
               "grp = CASE WHEN excluded.ts >= ts THEN excluded.grp ELSE grp END, "
               "disc_d = CASE WHEN excluded.ts >= ts THEN excluded.disc_d ELSE disc_d END, "
               "n_attr = CASE WHEN excluded.ts >= ts THEN excluded.n_attr ELSE n_attr END, "
               "restore_d = CASE WHEN excluded.ts >= ts THEN excluded.restore_d ELSE restore_d END, "
               "exempt_d = CASE WHEN excluded.ts >= ts THEN excluded.exempt_d ELSE exempt_d END, "
               "proxy_d = CASE WHEN excluded.ts >= ts THEN excluded.proxy_d ELSE proxy_d END, ts = MAX(ts, excluded.ts)")


def connect(path):
    c = sqlite3.connect(path, timeout=600.0, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=OFF")
    for s in DDL:
        c.execute(s)
    return c


class Board:
    """ABL bookkeeping of one board (BOARD_CTX to the next BOARD_CTX / EOF)."""

    def __init__(self):
        self.g_s = {}
        self.disc = {}
        self.g_f = {}
        self.attr = {}
        self.rest = {}
        self.exempt = {}
        self.proxy = {}

    def rows(self, cs, sym, ts):
        abl, disc = [], []
        for (grp, swc), (d, valid) in self.disc.items():
            if not valid or d is None or d <= EPS:
                continue
            n_attr = 0
            rd = self.rest.get((grp, swc))
            ed = self.exempt.get((grp, swc))
            pv = self.proxy.get((grp, swc))
            pd, pfs = (pv if pv else (None, ()))
            for (g2, f), gf in self.g_f.items():
                if g2 != grp:
                    continue
                ga = self.attr.get((grp, swc, f))
                if ga is None or gf is None:
                    continue
                n_attr += 1
                eff = ga - gf
                abl.append((cs, swc, f, sym, ts, grp, d, eff, 1 if eff < d * KILL_FRAC else 0, rd, ed, pd, 1 if f in pfs else 0, 1))
            disc.append((cs, swc, sym, ts, grp, d, n_attr, rd, ed, pd, 1))
        return abl, disc


def mine_file(path, host, since, counters):
    """-> (obs dict, abl rows, disc rows, fwd rows, board rows, meta). One pass, bounded memory (one file in RAM)."""
    sym = sym_side_of_path(path)
    cs = cat_side_of(sym)
    obs = {}
    abl_rows, disc_rows, fwd_rows, board_rows = [], [], [], []
    buf = {}
    board = Board()
    board_no = 0
    first_ts = last_ts = None
    lines = 0

    def put(key, ts, d, m, valid, prom, cached):
        o = obs.get(key)
        if o is None:
            obs[key] = [ts, d, m, valid, prom, 1, 0 if cached else 1]
        else:
            o[5] += 1
            o[6] += 0 if cached else 1
            o[4] = max(o[4], prom)
            if ts >= o[0]:
                o[0], o[1], o[2], o[3] = ts, d, m, valid

    def close_board(ts):
        a, d = board.rows(cs, sym, ts or "")
        abl_rows.extend(a)
        disc_rows.extend(d)

    with open(path, "r", errors="replace") as fh:
        for line in fh:
            lines += 1
            if '"label"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                counters["bad_json"] += 1
                continue
            ts = str(r.get("ts") or "")
            if ts < since:
                counters["before_since"] += 1
                continue
            first_ts = first_ts or ts
            last_ts = ts
            label = str(r.get("label") or "")
            if label == "BOARD_CTX":
                close_board(last_ts)
                board = Board()
                board_no += 1
                board_rows.append((path, board_no, cs, sym, ts, r.get("tier"), _f(r.get("bh")), _f(r.get("baseline_gain")), r.get("round")))
                counters["board_ctx"] += 1
                continue
            if label in ("FORWARD", "HOLDOUT"):
                key = str(r.get("key") or ("BOARD" if label == "HOLDOUT" else ""))
                dv = _f(r.get("d_gain")) if label == "FORWARD" else _f(r.get("final_minus_start"))
                if key and dv is not None:
                    fwd_rows.append((cs, key, sym, ts, dv, label))
                counters[label.lower()] += 1
                continue
            if label.startswith("ABL_"):
                counters["abl_records"] += 1
                head, _, rest = label.partition(":")
                grp, _, tail = rest.partition(":")
                g = _f(r.get("gain_pct"))
                d = _f(r.get("delta"))
                valid = bool(r.get("valid"))
                swc = f"{r.get('switch')}={r.get('cand')}" if r.get("switch") else None
                if head == "ABL_STRIP":
                    board.g_s[grp] = g
                elif head == "ABL_DISCOVER" and swc:
                    board.disc[(grp, swc)] = (d, valid and int(r.get("trades") or 0) > 0)
                elif head == "ABL_RESTORE1":
                    board.g_f[(grp, tail)] = g if valid and int(r.get("trades") or 0) > 0 else None
                elif head == "ABL_ATTR":
                    sw_part, _, f = tail.rpartition("+")
                    if sw_part and f and valid and int(r.get("trades") or 0) > 0:
                        board.attr[(grp, sw_part, f)] = g
                elif head == "ABL_RESTORE" and swc:
                    board.rest[(grp, swc)] = d
                elif head == "ABL_EXEMPT" and swc:
                    board.exempt[(grp, swc)] = d
                elif head == "ABL_PROXY" and swc:
                    pre = swc + "-"
                    fs = tuple(x for x in tail[len(pre):].split("-") if x) if tail.startswith(pre) else ()
                    board.proxy[(grp, swc)] = (d, fs)
                continue
            key = (r.get("sheet"), r.get("row"))
            if label == "ROW_DONE":
                b = buf.pop(key, None)
                sw = r.get("switch")
                if not b or not sw or sw in DENY:
                    continue
                swc = f"{sw}={r.get('cand')}"
                prom = 1 if r.get("promoted") else 0
                nk = b.get("naked")
                if nk is not None:
                    put("N|" + swc, nk[0], nk[1], None, nk[2], prom, nk[3])
                    counters["naked"] += 1
                for hdr, y in b["y"].items():
                    m = (y[1] - nk[1]) if (nk is not None and nk[1] is not None and y[1] is not None) else None
                    put("P|" + swc + "|" + hdr, y[0], y[1], m, y[2], 0, y[3])
                    counters["pair"] += 1
                continue
            if label == "hustle" or label.startswith(SKIP_PREFIX):
                counters["other_labels"] += 1
                continue
            if label.startswith("RED_RETRY:"):
                label = label[len("RED_RETRY:"):]
            if label != "naked" and ("=" not in label or ":" in label):
                counters["unparsed_labels"] += 1
                continue
            rec = (ts, _f(r.get("delta")), 1 if r.get("valid") else 0, bool(r.get("cached")))
            b = buf.setdefault(key, {"y": {}})
            if label == "naked":
                b["naked"] = rec
            else:
                b["y"][label.strip()] = rec
    close_board(last_ts)
    meta = {"lines": lines, "first_ts": first_ts, "last_ts": last_ts, "boards": board_no, "sym": sym, "cs": cs}
    return obs, abl_rows, disc_rows, fwd_rows, board_rows, meta


def denied_symbols():
    """data/excluded_tokenized_stocks.json (tools/excluded_tokenized_stocks.py's file): tokenized stocks never enter evidence."""
    for base in (os.environ.get("V18_ROOT"), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        if not base:
            continue
        p = os.path.join(base, "data", "excluded_tokenized_stocks.json")
        if os.path.exists(p):
            try:
                return frozenset(str(x).strip().upper() for x in json.load(open(p)).get("symbols", []))
            except (OSError, ValueError):
                pass
    return frozenset()


def worker(args):
    wid, files, db_path, since = args
    c = connect(db_path)
    counters = Counter()
    deny = denied_symbols()
    for host, path, size in files:
        if sym_side_of_path(path).rsplit("_", 1)[0].upper() in deny:
            counters["tokenized_file_skipped"] += 1
            continue
        t0 = time.time()
        fc = Counter()
        try:
            obs, abl, disc, fwd, boards, meta = mine_file(path, host, since, fc)
        except Exception as e:
            counters["file_errors"] += 1
            print(f"[mine w{wid}] ERROR {path}: {e}", flush=True)
            continue
        cs, sym = meta["cs"], meta["sym"]
        c.execute("BEGIN")
        if cs:
            c.executemany(UPSERT_OBS, ((cs, k, sym, v[0], v[1], v[2], v[3], v[4], v[5], v[6], 1) for k, v in obs.items()))
            c.executemany(UPSERT_ABL, abl)
            c.executemany(UPSERT_DISC, disc)
            c.executemany("INSERT OR IGNORE INTO fwd VALUES (?,?,?,?,?,?)", fwd)
        else:
            fc["no_cat_side_file"] += 1
        c.executemany("INSERT OR REPLACE INTO boards VALUES (?,?,?,?,?,?,?,?,?)", boards)
        c.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (path, host, sym, cs, size, meta["lines"], 1, meta["first_ts"], meta["last_ts"], json.dumps(dict(fc))))
        c.execute("COMMIT")
        counters.update(fc)
        counters["files"] += 1
        print(f"[mine w{wid}] {host} {os.path.basename(path)} {size/1e6:.0f}MB obs={len(obs)} abl={len(abl)} disc={len(disc)} "
              f"{time.time()-t0:.1f}s", flush=True)
    c.close()
    return dict(counters)


def file_sig(path):
    with open(path, "rb") as fh:
        first = fh.readline()
    return os.path.basename(path) + "|" + hashlib.sha1(first).hexdigest()


def discover(srcs):
    seen = {}
    dups = []
    for spec in srcs:
        host, _, pat = spec.partition("=")
        for p in sorted(glob.glob(os.path.expanduser(pat))):
            if not p.endswith(".jsonl"):
                continue
            try:
                size = os.path.getsize(p)
                sig = file_sig(p)
            except OSError:
                continue
            if sig in seen:
                keep = seen[sig] if seen[sig][2] >= size else (host, p, size)
                drop = (host, p, size) if keep is seen[sig] else seen[sig]
                seen[sig] = keep
                dups.append({"kept": keep[1], "dropped": drop[1]})
            else:
                seen[sig] = (host, p, size)
    return sorted(seen.values(), key=lambda t: -t[2]), dups


def merge(main, parts):
    for p in parts:
        main.execute(f"ATTACH DATABASE '{p}' AS w")
        main.execute("BEGIN")
        main.execute("INSERT INTO obs SELECT cs, key, sym, ts, d, m, valid, prom, n, nf, nb FROM w.obs WHERE 1 " + UPSERT_OBS.split(") VALUES (?,?,?,?,?,?,?,?,?,?,?) ", 1)[1])
        main.execute("INSERT INTO abl SELECT * FROM w.abl WHERE 1 " + UPSERT_ABL.split("VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ", 1)[1])
        main.execute("INSERT INTO disc SELECT * FROM w.disc WHERE 1 " + UPSERT_DISC.split("VALUES (?,?,?,?,?,?,?,?,?,?,?) ", 1)[1])
        main.execute("INSERT OR IGNORE INTO fwd SELECT * FROM w.fwd")
        main.execute("INSERT OR REPLACE INTO boards SELECT * FROM w.boards")
        main.execute("INSERT OR REPLACE INTO files SELECT * FROM w.files")
        main.execute("COMMIT")
        main.execute("DETACH DATABASE w")


def rebuilt_pass(db_path, main, since_epoch, counters):
    """Rows of the S2 rebuilt corpus whose source log is gone from this host (else the log already counted them)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=600.0)
    src_exists = {}
    maxrow = con.execute("SELECT max(rowid) FROM evals_compact").fetchone()[0] or 0
    used = Counter()
    step = 200000
    rows_by_src = defaultdict(int)
    for lo in range(0, maxrow + 1, step):
        for src, created in con.execute("SELECT json_extract(prov,'$.src'), created FROM evals_compact WHERE rowid >= ? AND rowid < ?",
                                        (lo, lo + step)):
            if src not in src_exists:
                src_exists[src] = bool(src) and os.path.exists(src)
            if src_exists[src]:
                used["superseded_by_source_log"] += 1
            elif created is not None and created >= since_epoch:
                rows_by_src[src] += 1
            else:
                used["orphan_before_since"] += 1
    con.close()
    counters["rebuilt"] = {"rows": maxrow, **dict(used), "orphan_in_window_rows": sum(rows_by_src.values()),
                           "orphan_in_window_srcs": len(rows_by_src), "srcs_total": len(src_exists),
                           "srcs_present": sum(1 for v in src_exists.values() if v)}
    if rows_by_src:
        counters["rebuilt"]["note"] = ("orphan in-window rows exist; this version does NOT ingest them (their chain replay is "
                                       "base-dependent) — listed so they are not silently ignored")
        counters["rebuilt"]["orphan_srcs_sample"] = sorted(rows_by_src)[:20]


def export(main, out_dir, meta):
    os.makedirs(out_dir, exist_ok=True)
    written = {}
    for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        naked = {}
        for key, syms, valid, pos, neg, sum_pos, prom, n, nf, last in main.execute(
                "SELECT key, count(*), sum(valid), sum(CASE WHEN valid=1 AND d > ? THEN 1 ELSE 0 END), "
                "sum(CASE WHEN valid=1 AND d < ? THEN 1 ELSE 0 END), sum(CASE WHEN valid=1 AND d > ? THEN d ELSE 0 END), "
                "sum(prom), sum(n), sum(nf), max(ts), sum(nb) FROM obs WHERE cs=? AND key LIKE 'N|%' GROUP BY key", (EPS, -EPS, EPS, cs)):
            naked[key[2:]] = [syms, valid or 0, pos or 0, neg or 0, round(sum_pos or 0.0, 6), prom or 0, n, nf, last, nb]
        pairs = {}
        for key, syms, valid, pos, sum_pos, comp, comp_m, n, nf, nb in main.execute(
                "SELECT key, count(*), sum(valid), sum(CASE WHEN valid=1 AND d > ? THEN 1 ELSE 0 END), "
                "sum(CASE WHEN valid=1 AND d > ? THEN d ELSE 0 END), "
                "sum(CASE WHEN valid=1 AND d > ? AND m > ? THEN 1 ELSE 0 END), "
                "sum(CASE WHEN valid=1 AND d > ? AND m > ? THEN m ELSE 0 END), sum(n), sum(nf), sum(nb) "
                "FROM obs WHERE cs=? AND key LIKE 'P|%' GROUP BY key", (EPS, EPS, EPS, EPS, EPS, EPS, cs)):
            pairs[key[2:]] = [syms, valid or 0, pos or 0, round(sum_pos or 0.0, 6), comp or 0, round(comp_m or 0.0, 6), n, nf, nb]
        pos_syms = defaultdict(set)
        for key, sym in main.execute("SELECT key, sym FROM obs WHERE cs=? AND key LIKE 'P|%' AND valid=1 AND d > ?", (cs, EPS)):
            pos_syms[key[2:]].add(sym)
        abl = {}
        for sw, f, syms, killers, sum_eff, sum_d, rd_pos, rd_n, ed_pos, ed_n, pd_pos, pd_n, killer_syms in main.execute(
                "SELECT sw, f, count(*), sum(killer), sum(eff), sum(disc_d), "
                "sum(CASE WHEN restore_d > ? THEN 1 ELSE 0 END), sum(CASE WHEN restore_d IS NOT NULL THEN 1 ELSE 0 END), "
                "sum(CASE WHEN exempt_d > ? THEN 1 ELSE 0 END), sum(CASE WHEN exempt_d IS NOT NULL THEN 1 ELSE 0 END), "
                "sum(CASE WHEN proxy_has_f=1 AND proxy_d > ? THEN 1 ELSE 0 END), sum(CASE WHEN proxy_has_f=1 AND proxy_d IS NOT NULL THEN 1 ELSE 0 END), "
                "group_concat(CASE WHEN killer=1 THEN sym END) FROM abl WHERE cs=? GROUP BY sw, f", (EPS, EPS, EPS, cs)):
            abl[f"{sw}|{f}"] = {"n_syms": syms, "killer_syms": killers or 0, "sum_eff": round(sum_eff or 0.0, 6), "sum_disc_d": round(sum_d or 0.0, 6),
                                "restore_pos": rd_pos or 0, "restore_n": rd_n or 0, "exempt_pos": ed_pos or 0, "exempt_n": ed_n or 0,
                                "proxy_pos": pd_pos or 0, "proxy_n": pd_n or 0,
                                "killer_sym_list": sorted(set((killer_syms or "").split(",")) - {""})}
        disc = {}
        for sw, syms, sum_d, n_attr, rd_pos, rd_n in main.execute(
                "SELECT sw, count(*), sum(disc_d), sum(n_attr), sum(CASE WHEN restore_d > ? THEN 1 ELSE 0 END), "
                "sum(CASE WHEN restore_d IS NOT NULL THEN 1 ELSE 0 END) FROM disc WHERE cs=? GROUP BY sw", (EPS, cs)):
            disc[sw] = {"n_syms": syms, "sum_disc_d": round(sum_d or 0.0, 6), "n_attr": n_attr or 0, "restore_pos": rd_pos or 0, "restore_n": rd_n or 0}
        fwd = {}
        for key, kind, n, ok in main.execute("SELECT key, kind, count(*), sum(CASE WHEN d >= 0 THEN 1 ELSE 0 END) FROM fwd WHERE cs=? GROUP BY key, kind", (cs,)):
            fwd[f"{kind}|{key}"] = [n, ok or 0]
        boards = main.execute("SELECT count(*), count(DISTINCT sym), min(ts), max(ts) FROM boards WHERE cs=?", (cs,)).fetchone()
        rounds = Counter(r[0] for r in main.execute("SELECT round FROM boards WHERE cs=?", (cs,)))
        syms_obs = main.execute("SELECT count(DISTINCT sym) FROM obs WHERE cs=?", (cs,)).fetchone()[0]
        doc = {"meta": dict(meta, cat_side=cs, n_board_ctx=boards[0], n_sym_sides_board_ctx=boards[1], first_board_ts=boards[2],
                            last_board_ts=boards[3], n_sym_sides_evidence=syms_obs,
                            engine_rounds=dict(Counter((r or "").rsplit("-eng", 1)[-1] if r else "none" for r in rounds.elements())),
                            columns={"naked": ["n_syms", "valid_syms", "pos_syms", "neg_syms", "sum_pos_d", "promoted_syms", "n_evals", "n_fresh", "last_ts", "n_boards"],
                                     "pairs": ["n_syms", "valid_syms", "pos_syms", "sum_pos_d", "companion_syms", "companion_sum_m", "n_evals", "n_fresh", "n_boards"]},
                            tokenized_files_skipped=meta.get("counters", {}).get("tokenized_file_skipped", 0)),
               "naked": naked, "pairs": pairs, "pair_pos_syms": {k: sorted(v) for k, v in pos_syms.items()}, "abl": abl, "abl_discoveries": disc,
               "forward": fwd}
        p = os.path.join(out_dir, f"evidence_{cs}.json.gz")
        tmp = p + ".tmp"
        with gzip.open(tmp, "wt") as fh:
            json.dump(doc, fh, separators=(",", ":"), default=str)
        os.replace(tmp, p)
        written[cs] = {"naked": len(naked), "pairs": len(pairs), "abl_pairs": len(abl), "discoveries": len(disc), "bytes": os.path.getsize(p)}
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", action="append", required=True, help="HOST=GLOB of delta-log jsonl files")
    ap.add_argument("--since", default=None, help="ISO ts (default: now - 14 d)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--scratch", default="~/v18_scratch/mine")
    ap.add_argument("--out-dir", default="~/v18_learn/evidence")
    ap.add_argument("--rebuilt", default="")
    ap.add_argument("--limit-files", type=int, default=0)
    a = ap.parse_args(argv)
    t0 = time.time()
    since = a.since or utc(time.time() - 14 * 86400)
    since_epoch = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
    scratch = os.path.expanduser(a.scratch)
    os.makedirs(scratch, exist_ok=True)
    files, dups = discover(a.src)
    if a.limit_files:
        files = files[: a.limit_files]
    print(f"[mine] {len(files)} files ({sum(f[2] for f in files)/1e9:.2f} GB), {len(dups)} duplicate copies dropped, since {since}", flush=True)
    nw = max(1, min(a.workers, 4))
    buckets = [[] for _ in range(nw)]
    loads = [0] * nw
    for f in files:
        i = loads.index(min(loads))
        buckets[i].append(f)
        loads[i] += f[2]
    parts = []
    jobs = []
    for i, b in enumerate(buckets):
        p = os.path.join(scratch, f"part_{i}.db")
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(p + ext):
                os.remove(p + ext)
        parts.append(p)
        jobs.append((i, b, p, since))
    counters = Counter()
    if nw > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(nw) as pool:
            for c in pool.imap_unordered(worker, jobs):
                counters.update(c)
    else:
        counters.update(worker(jobs[0]))
    main_p = os.path.join(scratch, "evidence.db")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(main_p + ext):
            os.remove(main_p + ext)
    main = connect(main_p)
    merge(main, parts)
    print(f"[mine] merged {len(parts)} parts {time.time()-t0:.0f}s", flush=True)
    extra = {}
    if a.rebuilt and os.path.exists(os.path.expanduser(a.rebuilt)):
        rebuilt_pass(os.path.expanduser(a.rebuilt), main, since_epoch, extra)
        print(f"[mine] rebuilt pass: {json.dumps(extra.get('rebuilt'))}", flush=True)
    meta = {"tool": "tools/v18_evidence_mine.py", "host": os.uname().nodename, "built_at": utc(), "since": since,
            "sources": a.src, "files": len(files), "bytes": sum(f[2] for f in files), "duplicates_dropped": dups[:50],
            "n_duplicates": len(dups), "counters": dict(counters), "kill_frac": KILL_FRAC, **extra,
            "unit": "(cat_side, key, sym_side), last measurement wins; n = raw evals, nf = fresh evals"}
    written = export(main, os.path.expanduser(a.out_dir), meta)
    main.close()
    print(json.dumps({"wall_s": round(time.time() - t0, 1), "written": written, "counters": dict(counters)}, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
