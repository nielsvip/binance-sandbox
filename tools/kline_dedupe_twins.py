#!/usr/bin/env python3
"""kline_dedupe_twins - remove duplicate-instant twins from legacy kline stores (2026-10-29 plan, operator rule).

A twin group = rows sharing one canonical stamp (a legacy "+00:00" row and its "Z" row from another writer). Keep exactly one row:
  1. crypto symbol and klines_canonical/<name> holds that instant: keep the twin whose OHLCV equals the exchange-native row;
     if NEITHER matches, keep the Z writer's row and LOG the pair (kind "neither_matches_canonical").
  2. otherwise keep the Z writer's row (the twin whose ORIGINAL stamp was already canonical, found in the first-pass gzip backup of kline_stamp_normalize);
     no such row -> keep the first.
Kept rows are original rows (nothing is edited). Dry run by default. --apply: gzip backup, kept-subsequence check, atomic write, skips a file whose mtime moved.
This SHRINKS stores: run only per host with klines_guard / klines_vault --seal,--enforce / klines_append paused and re-baselined afterwards (report FOLLOW-UP 7/8 procedure).
usage: kline_dedupe_twins.py --dir DIR --orig-backups DIR [--canonical-dir DIR] [--apply] [--shard i/n] [--log FILE]
"""
import argparse, glob, gzip, json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kline_stamp as K

CRYPTO = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


def ohlcv(r):
    return tuple(r.get(k) for k in ("open", "high", "low", "close", "volume"))


def same(a, b):
    try:
        return all(abs(float(x) - float(y)) <= 1e-9 * max(1.0, abs(float(x))) for x, y in zip(a, b))
    except (TypeError, ValueError):
        return a == b


def z_flags(orig_rows):
    """(instant, ohlcv) -> True when an original row with that content had an already canonical stamp"""
    flags = {}
    for r in orig_rows:
        try:
            c = K.canonical_stamp(r["timestamp"])
        except Exception:
            continue
        flags[(c, ohlcv(r))] = flags.get((c, ohlcv(r)), False) or K.is_canonical(r["timestamp"])
    return flags


def choose(group, canon_row, flags):
    """group = list of (index,row). returns (keep_index, kind)"""
    if canon_row is not None:
        cv = ohlcv(canon_row)
        m = [(i, r) for i, r in group if same(ohlcv(r), cv)]
        if m:
            return m[0][0], "matches_canonical"
    zs = [(i, r) for i, r in group if flags.get((K.canonical_stamp(r["timestamp"]), ohlcv(r)), False)]
    if zs:
        return zs[0][0], "neither_matches_canonical" if canon_row is not None else "z_writer"
    return group[0][0], "first_no_z_found" if canon_row is None else "neither_matches_canonical"


def dedupe(rows, canon_by_stamp, flags):
    groups, order = {}, []
    for i, r in enumerate(rows):
        try:
            c = K.canonical_stamp(r["timestamp"])
        except Exception:
            order.append(None)
            continue
        order.append(c)
        groups.setdefault(c, []).append((i, r))
    drop, kinds, mism = set(), {}, []
    for c, g in groups.items():
        if len(g) < 2:
            continue
        keep, kind = choose(g, canon_by_stamp.get(c), flags)
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "neither_matches_canonical":
            mism.append({"stamp": c, "rows": [ohlcv(r) for _, r in g], "canonical": ohlcv(canon_by_stamp[c]) if c in canon_by_stamp else None})
        for i, _ in g:
            if i != keep:
                drop.add(i)
    return [r for i, r in enumerate(rows) if i not in drop], len(drop), kinds, mism


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True); ap.add_argument("--orig-backups", required=True); ap.add_argument("--canonical-dir", default=None)
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--shard", default="0/1")
    ap.add_argument("--log", default=None); ap.add_argument("--backup-dir", default=os.path.expanduser("~/stamp_norm_backup/twins_pre"))
    a = ap.parse_args()
    i_, n_ = (int(x) for x in a.shard.split("/"))
    tot = {"files": 0, "files_with_twins": 0, "rows_dropped": 0, "neither_matches_canonical": 0, "matches_canonical": 0, "z_writer": 0, "first_no_z_found": 0, "skipped": 0}
    logf = open(a.log, "a") if a.log else None
    for f in sorted(glob.glob(os.path.join(a.dir, "*.json"))):
        name = os.path.basename(f)
        if n_ > 1 and (sum(name.encode()) % n_) != i_:
            continue
        st0 = os.stat(f)
        try:
            rows = json.load(open(f))
        except Exception:
            continue
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            continue
        tot["files"] += 1
        stamps = [r.get("timestamp") for r in rows]
        if len(stamps) == len(set(stamps)):
            continue
        sym = name.rsplit("_", 1)[0]
        canon = {}
        if a.canonical_dir and sym.upper().endswith(CRYPTO):
            cf = os.path.join(a.canonical_dir, name)
            if os.path.exists(cf):
                try:
                    canon = {K.canonical_stamp(r["timestamp"]): r for r in json.load(open(cf))}
                except Exception:
                    canon = {}
        bks = sorted(glob.glob(os.path.join(a.orig_backups, name + ".*.bak.gz")))
        flags = z_flags(json.load(gzip.open(bks[0]))) if bks else {}
        out, dropped, kinds, mism = dedupe(rows, canon, flags)
        if not dropped:
            continue
        tot["files_with_twins"] += 1; tot["rows_dropped"] += dropped
        for k, v in kinds.items():
            tot[k] = tot.get(k, 0) + v
        if logf:
            logf.write(json.dumps({"file": name, "rows": len(rows), "kept": len(out), "dropped": dropped, "kinds": kinds, "mismatches": mism[:50], "mismatch_total": len(mism), "applied": a.apply}) + "\n")
        if a.apply:
            os.makedirs(a.backup_dir, exist_ok=True)
            with open(f, "rb") as s, gzip.open(os.path.join(a.backup_dir, f"{name}.{int(time.time())}.twins.gz"), "wb", compresslevel=1) as d:
                shutil.copyfileobj(s, d)
            if os.stat(f).st_mtime_ns != st0.st_mtime_ns:
                tot["skipped"] += 1
                continue
            it = iter(rows)
            assert all(any(r is o for o in it) for r in out), "kept rows are not an ordered subset"
            tmp = f + f".tw{os.getpid()}.tmp"
            with open(tmp, "w") as fh:
                json.dump(out, fh, separators=(",", ":")); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp, f)
    print(("APPLIED " if a.apply else "DRY ") + json.dumps(tot), flush=True)


if __name__ == "__main__":
    main()
