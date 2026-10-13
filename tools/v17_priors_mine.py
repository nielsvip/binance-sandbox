#!/usr/bin/env python3
"""v17_priors_mine — mine a per-host eval_cache_v1.db (memoized REAL engine evals) into causal
partials: single-key effects, filter-blocks-switch quartets, synergy quartets, age split, engine drift.

READ-ONLY: opens the DB with sqlite URI mode=ro, streams rows, never writes to the DB.
Stdlib only (runs with system python3 on S1/s2). Run under `nice -n 15`.

Method (no estimates — every number is a difference of two cached real-engine evals):
- Context = (symside, window_days, offset_days, bh_pct, bars): same price data slice.
- Inside a context, two rows whose full `overrides` dicts differ in exactly ONE key k form an edge;
  the earlier-created row is the reference (greedy sheets always evaluate the running set first),
  delta = metric(candidate) - metric(reference), labelled `k=<candidate value>`.
- Quartet = B, B+k1, B+k2, B+k1+k2 all cached in one context -> interaction
  I = (Z-B) - (X-B) - (Y-B); used for synergy and for filter-blocks-switch (S effect with filter
  F off vs on).
- Rows with identical overrides in one context (key differed only by engine/base cfg) are counted as
  engine-drift collisions; the newest row is kept.
Output: a partial JSON (per-sym sums) merged by tools/v17_priors_build.py.
"""
import argparse
import collections
import json
import os
import re
import sqlite3
import sys
import time

EPS = 1e-9
MASK = (1 << 64) - 1
ABSENT = "<absent>"
OLD_DAYS = 14.0
METRICS = ("gain_pct", "trades", "tim_pct", "max_dd_pct", "wr_pct")
OFF_VALUES = {"OFF", "off", "Off", "False", "false", "0", "0.0", "None", "none", "NONE", "[]", "", "null"}
FILTER_RE = re.compile(r"(FILTER|GATE|BLOCK|VETO|GUARD|CONFIRM|REQUIRE)")
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
MAX_CHILD_PAIRS = 400000
MAX_EDGE_GAP_S = float(os.environ.get("V17_MINE_MAX_EDGE_GAP_H", "12")) * 3600.0


def cat_side_of(sym_side):
    sym, _, side = str(sym_side).rpartition("_")
    cat = "CRYPTO" if sym.upper().endswith(CRYPTO_SUFFIX) else "STOCKS"
    return f"{cat}_{side}"


def canon(v):
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, sort_keys=True, separators=(",", ":"))
    if v is None:
        return "None"
    return str(v)


def is_off(vs):
    return vs in OFF_VALUES or vs == ABSENT


def num(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        return float(x)
    except Exception:
        return None


class SymAcc:
    """per (cat_side,label) -> per sym -> [n, sum_d_gain, sum_d_trades, sum_d_tim, sum_d_dd, sum_d_wr, n_old, sum_old, n_new, sum_new, n_invalid]"""

    def __init__(self):
        self.d = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0, 0.0, 0]))

    def add(self, cs, label, sym, dm, old, invalid):
        a = self.d[(cs, label)][sym]
        if invalid:
            a[10] += 1
            return
        a[0] += 1
        for i, v in enumerate(dm):
            if v is not None:
                a[1 + i] += v
        if old:
            a[6] += 1
            a[7] += dm[0]
        else:
            a[8] += 1
            a[9] += dm[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--host", default=os.uname().nodename)
    ap.add_argument("--exemptions", default="")
    ap.add_argument("--limit-contexts", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()
    now = time.time()
    filter_keys = set()
    if args.exemptions and os.path.exists(args.exemptions):
        try:
            ex = json.load(open(args.exemptions))
            for sw, inner in ex.items():
                filter_keys.update(inner.keys())
        except Exception as e:
            print(f"[mine] exemptions unreadable: {e}", flush=True)
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA query_only=1")
    meta = {"host": args.host, "db": args.db, "db_bytes": os.path.getsize(args.db)}
    try:
        meta["db_meta"] = dict(con.execute("SELECT k, v FROM meta").fetchall())
    except Exception:
        meta["db_meta"] = {}
    ctx_rows = collections.defaultdict(list)
    n_rows = 0
    created_min, created_max = None, None
    age_hist = collections.Counter()
    win_hist = collections.Counter()
    q = ("SELECT rowid, created, json_extract(result,'$.symside'), json_extract(result,'$.window_days'), "
         "json_extract(result,'$.offset_days'), json_extract(result,'$.bh_pct'), json_extract(result,'$.bars') FROM evals")
    maxrow = con.execute("SELECT max(rowid) FROM evals").fetchone()[0] or 0

    def pass1_rows():
        step = 50000
        for lo in range(0, maxrow + 1, step):
            rng = " WHERE rowid >= ? AND rowid < ?"
            try:
                yield from con.execute(q + rng, (lo, lo + step)).fetchall()
            except sqlite3.OperationalError:
                stats_p1["malformed_chunks"] += 1
                yield from con.execute(q + rng + " AND json_valid(result)", (lo, lo + step)).fetchall()
                stats_p1["malformed_rows"] += con.execute("SELECT count(*) FROM evals" + rng + " AND NOT json_valid(result)", (lo, lo + step)).fetchone()[0]

    stats_p1 = collections.Counter()
    for rowid, created, symside, win, off, bh, bars in pass1_rows():
        n_rows += 1
        if created is not None:
            created_min = created if created_min is None else min(created_min, created)
            created_max = created if created_max is None else max(created_max, created)
            age_hist[int((now - created) // 86400)] += 1
        win_hist[f"{win}/{off}"] += 1
        bhk = round(bh, 6) if isinstance(bh, (int, float)) else bh
        ctx_rows[(symside, win, off, bhk, bars)].append(rowid)
        if n_rows % 200000 == 0:
            print(f"[mine] pass1 {n_rows} rows {time.time()-t0:.0f}s", flush=True)
    meta.update({"rows": n_rows, "created_min": created_min, "created_max": created_max, "contexts": len(ctx_rows),
                 "age_days_hist": dict(sorted(age_hist.items())), "window_offset_hist": dict(win_hist),
                 "rows_older_14d": sum(v for k, v in age_hist.items() if k >= OLD_DAYS), "pass1": dict(stats_p1)})
    print(f"[mine] pass1 done rows={n_rows} contexts={len(ctx_rows)} {time.time()-t0:.0f}s", flush=True)
    single = SymAcc()
    quart = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0]))
    blocks = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0]))
    stats = collections.Counter()
    drift_examples = []
    ctx_list = sorted(ctx_rows.items(), key=lambda kv: -len(kv[1]))
    if args.limit_contexts:
        ctx_list = ctx_list[: args.limit_contexts]
    hcache = {}

    def hkv(k, vs):
        t = (k, vs)
        h = hcache.get(t)
        if h is None:
            h = hash(t) & MASK
            if len(hcache) < 1000000:
                hcache[t] = h
        return h

    for ci, (ctx, rowids) in enumerate(ctx_list):
        if len(rowids) < 2:
            stats["ctx_singleton"] += 1
            continue
        symside, win, off, bhk, bars = ctx
        if not symside:
            stats["ctx_no_symside"] += 1
            continue
        cs = cat_side_of(symside)
        ref = None
        rows = []
        by_hash = {}
        conflicted = set()
        rowids.sort()
        for i in range(0, len(rowids), 400):
            chunk = rowids[i:i + 400]
            qs = ",".join("?" * len(chunk))
            for rowid, created, blob in con.execute(f"SELECT rowid, created, result FROM evals WHERE rowid IN ({qs})", chunk):
                try:
                    d = json.loads(blob)
                except Exception:
                    stats["bad_json"] += 1
                    continue
                ov = d.get("overrides")
                if not isinstance(ov, dict):
                    stats["no_overrides"] += 1
                    continue
                ovc = {k: canon(v) for k, v in ov.items()}
                if ref is None:
                    ref = ovc
                diff = {}
                for k, vs in ovc.items():
                    if ref.get(k, ABSENT) != vs:
                        diff[k] = vs
                for k in ref:
                    if k not in ovc:
                        diff[k] = ABSENT
                h = 0
                for k, vs in diff.items():
                    h = (h + hkv(k, vs) - hkv(k, ref.get(k, ABSENT))) & MASK
                m = tuple(num(d.get(x)) for x in METRICS)
                valid = bool(d.get("valid")) and m[0] is not None
                rec = (created or 0.0, diff, m, valid, h)
                if h in by_hash:
                    stats["identity_collisions"] += 1
                    other = rows[by_hash[h]]
                    if other[2][0] is not None and m[0] is not None and abs(other[2][0] - m[0]) > EPS:
                        stats["engine_drift_collisions"] += 1
                        conflicted.add(h)
                        if len(drift_examples) < 30:
                            drift_examples.append({"symside": symside, "created_a": other[0], "created_b": rec[0], "gain_a": other[2][0], "gain_b": m[0]})
                    if rec[0] > other[0]:
                        rows[by_hash[h]] = rec
                    continue
                by_hash[h] = len(rows)
                rows.append(rec)
        stats["rows_used"] += len(rows)
        stats["rows_conflicted"] += sum(1 for r in rows if r[4] in conflicted)
        if len(rows) < 2:
            continue
        refval = lambda k: ref.get(k, ABSENT)
        # single-key edges: sig(row,k) = H(row) - h(k, v_row(k)); group shares everything but k
        groups = collections.defaultdict(list)
        for idx, (created, diff, m, valid, h) in enumerate(rows):
            for k, vs in diff.items():
                sig = (h - hkv(k, vs) + hkv(k, refval(k))) & MASK
                groups[(k, sig)].append(idx)
        children = collections.defaultdict(list)
        for (k, sig), members in groups.items():
            base_idx = by_hash.get(sig)
            if base_idx is not None and base_idx not in members:
                members = members + [base_idx]
            members = [i for i in members if rows[i][4] not in conflicted]
            if len(members) < 2:
                continue
            refi = min(members, key=lambda i: (rows[i][0], i))
            rv = rows[refi][1].get(k, refval(k))
            for i in members:
                if i == refi:
                    continue
                cv = rows[i][1].get(k, refval(k))
                if cv == rv:
                    continue
                if abs(rows[i][0] - rows[refi][0]) > MAX_EDGE_GAP_S:
                    stats["edges_skipped_time_gap"] += 1
                    continue
                children[refi].append((k, i))
                rm, cm = rows[refi][2], rows[i][2]
                old = (now - min(rows[refi][0], rows[i][0])) > OLD_DAYS * 86400
                invalid = not (rows[refi][3] and rows[i][3])
                dm = tuple((cm[j] - rm[j]) if (cm[j] is not None and rm[j] is not None) else None for j in range(len(METRICS)))
                single.add(cs, f"{k}={cv}", symside, dm, old, invalid)
                stats["edges"] += 1
        # quartets from each reference row's children pairs
        for bi, ch in children.items():
            if len(ch) < 2:
                continue
            B = rows[bi]
            if not B[3]:
                continue
            npairs = 0
            for a in range(len(ch)):
                k1, xi = ch[a]
                X = rows[xi]
                if not X[3]:
                    continue
                for b in range(a + 1, len(ch)):
                    k2, yi = ch[b]
                    if k1 == k2:
                        continue
                    npairs += 1
                    if npairs > MAX_CHILD_PAIRS:
                        break
                    Y = rows[yi]
                    if not Y[3]:
                        continue
                    zh = (X[4] + Y[4] - B[4]) & MASK
                    zi = by_hash.get(zh)
                    if zi is None:
                        continue
                    Z = rows[zi]
                    if not Z[3] or Z[4] in conflicted or abs(Z[0] - B[0]) > MAX_EDGE_GAP_S:
                        continue
                    stats["quartets"] += 1
                    gB, gX, gY, gZ = B[2][0], X[2][0], Y[2][0], Z[2][0]
                    v1 = X[1].get(k1, refval(k1))
                    v2 = Y[1].get(k2, refval(k2))
                    l1, l2 = f"{k1}={v1}", f"{k2}={v2}"
                    s1, s2, joint = gX - gB, gY - gB, gZ - gB
                    pl = (l1, l2) if l1 < l2 else (l2, l1)
                    sa, sb = (s1, s2) if l1 < l2 else (s2, s1)
                    acc = quart[(cs, pl)][symside]
                    acc[0] += 1
                    acc[1] += joint
                    acc[2] += sa
                    acc[3] += sb
                    acc[4] += joint - s1 - s2
                    b1 = B[1].get(k1, refval(k1))
                    b2 = B[1].get(k2, refval(k2))
                    for (S, sv, F, fb, fc, dS_fbase, dS_fchanged) in ((k1, v1, k2, b2, v2, s1, gZ - gY), (k2, v2, k1, b1, v1, s2, gZ - gX)):
                        if not (F in filter_keys or FILTER_RE.search(F)):
                            continue
                        fb_off, fc_off = is_off(fb), is_off(fc)
                        if fb_off == fc_off:
                            continue
                        if fb_off:
                            d_off, d_on, f_on = dS_fbase, dS_fchanged, fc
                        else:
                            d_off, d_on, f_on = dS_fchanged, dS_fbase, fb
                        bacc = blocks[(cs, f"{S}={sv}", F, f_on)][symside]
                        bacc[0] += 1
                        bacc[1] += d_off
                        bacc[2] += d_on
                        stats["block_quads"] += 1
                if npairs > MAX_CHILD_PAIRS:
                    stats["child_pair_cap_hit"] += 1
                    break
        if (ci + 1) % 200 == 0:
            print(f"[mine] ctx {ci+1}/{len(ctx_list)} edges={stats['edges']} quartets={stats['quartets']} {time.time()-t0:.0f}s", flush=True)
    out = {
        "meta": dict(meta, stats=dict(stats), drift_examples=drift_examples, secs=round(time.time() - t0, 1), old_days=OLD_DAYS, metrics=list(METRICS), max_edge_gap_h=MAX_EDGE_GAP_S / 3600.0),
        "single": [[cs, label, sym, v] for (cs, label), syms in single.d.items() for sym, v in syms.items()],
        "quart": [[cs, pl[0], pl[1], sym, v] for (cs, pl), syms in quart.items() for sym, v in syms.items()],
        "blocks": [[cs, s, f, fon, sym, v] for (cs, s, f, fon), syms in blocks.items() for sym, v in syms.items()],
    }
    tmp = args.out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, args.out)
    print(f"[mine] DONE rows={n_rows} edges={stats['edges']} quartets={stats['quartets']} -> {args.out} {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
