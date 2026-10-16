#!/usr/bin/env python3
"""klines_unify — ONE growing gap-free kline store, and ONE backup of it.

USER 2026-10-16: "make sure klines get joined if necessary but always grow without any gaps,
not multiple klines backups that consume terabytes".

The box accumulated a dozen partial copies of the same bars (staging mirrors, restore work dirs,
install leftovers, a raw .guard_snap, the compressed vault, two canonical trees). Each one holds a
few timestamps the canonical store lacks, which is why nothing could ever be thrown away and the
disk kept filling. So: JOIN every timestamp a copy still holds into the canonical store, PROVE the
copy then holds nothing unique, and only then reclaim it.

  --audit            rows/span/holes per file + what each other copy holds that the store LACKS
  --join [--apply]   union those timestamps in (klines_multi_restore: gated, grow-only, guarded_write)
  --dedupe [--apply] delete ONLY roots proven to hold nothing the store lacks

S1 only (the Mac keeps ~1800 bars per file by design). Dry-run unless --apply.
"""
import argparse
import datetime as dt
import gzip
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
HOME = Path.home()
KC = ROOT / "klines_cache"
PERIOD = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "D": 86400, "1d": 86400}
# every copy of the same bars found on S1 2026-10-16, with whether it may ever be reclaimed.
# keep=True is a live landing zone or a protection mechanism — it is read for a join, never deleted.
MIRRORS = [
    ("macbook_feed", ROOT / "klines_cache_macbook", True),
    ("gateway_in_store", KC / "gateway", True),
    ("gateway_live", HOME / "binance" / "klines_cache_gateway", False),
    ("live_store", HOME / "binance" / "klines_cache", True),
    ("ez_s1_stack", HOME / "binance" / "ez_s1_stack" / "klines_cache", False),
    ("consolidated", KC / "consolidated_klines", False),
    ("store_backups", KC / "backups", False),
    ("stkt_20261002", HOME / "klines_backup_stkt_20261002", False),
    ("stkt_20261013", HOME / "klines_backup_stkt_20261013", False),
    ("restore_work", HOME / "klines_restore_work", False),
    ("wm_install", HOME / "klines_wm_install", False),
    ("vault", ROOT / "klines_vault", True),
]
SCHEMA = ("open", "high", "low", "close", "volume")


def is_crypto(rel):
    """Venue follows the SYMBOL, never the directory: the store keeps Binance perps such as tradier/TRBUSDT_15m.json
    and tradier/CHZUSDT_15m.json under tradier/, and measuring those 24/7 files against an RTH session grid invented
    1324 missing sessions for CHZUSDT alone."""
    sym = Path(rel).name.split(".")[0].rsplit("_", 1)[0].upper()
    return sym.endswith(("USDT", "USDC", "BUSD", "FDUSD"))


def tf_of(name):
    stem = name.split(".")[0]
    tf = stem.rsplit("_", 1)[-1] if "_" in stem else ""
    return tf if tf in PERIOD else None


def load_rows(p):
    raw = Path(p).read_bytes()
    if str(p).endswith(".gz"):
        raw = gzip.decompress(raw)
    rows = json.loads(raw)
    return rows if isinstance(rows, list) else []


def stamps(p):
    """set of second-resolution timestamps in a kline file; empty set on anything unreadable."""
    try:
        return {str(r.get("timestamp"))[:19] for r in load_rows(p) if isinstance(r, dict) and r.get("timestamp")}
    except Exception:
        return set()


def epoch(k):
    try:
        return dt.datetime.fromisoformat(k[:19]).replace(tzinfo=dt.timezone.utc).timestamp()
    except Exception:
        return None


def rth_epochs(ks):
    """09:30-15:45 ET bar starts only — measured 2026-10-16: 70% of the "holes" in the Massive-backfilled
    stock files sit at 12:30-13:15 UTC, i.e. pre-market 15m windows in which nothing traded. Those bars do
    not exist and never will, so counting them as gaps makes a clean file unfixable forever. Regular-hours
    absences are the real loss, and they are what the NPZ builder's gap guard trips on."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    out = []
    for k in ks:
        e = epoch(k)
        if e is None:
            continue
        t = dt.datetime.fromtimestamp(e, et)
        if t.weekday() < 5 and dt.time(9, 30) <= t.time() <= dt.time(15, 45):
            out.append(e)
    return sorted(out)


def session_days(ks):
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    return {dt.datetime.fromtimestamp(e, et).strftime("%Y-%m-%d") for e in rth_epochs(ks)}


def recent_window(ks, expect_days=None, days=30, now=None):
    """USER 2026-10-16 ("at least have complete 30D trading worthy data"): the board's fit window is the only one that
    must be perfect, and it drowns in two-year-old history when everything is tallied together. Reported apart.

    A session missing bars is not automatically a gap: measured 2026-10-16, Tradier returns 13 bars for LEXX on
    2026-10-12 while the cache already holds 17 and has nothing Tradier can add — a micro-cap does not print every
    15 minutes. So a day with SOME bars is "thin", and only a trading day with NO bars at all is a hole.
    """
    cut = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days)
    # the same 26 bar-STARTS rth_epochs keeps (09:30..15:45). The 16:00 ET closing print is an extra bar some feeds
    # emit and some do not, so requiring it would mark every complete session thin by one.
    grid = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 15, 30, 45) if (h, m) >= (9, 30)]
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    per_day = {}
    for e in rth_epochs(ks):
        if e < cut.timestamp():
            continue
        t = dt.datetime.fromtimestamp(e, et)
        per_day.setdefault(t.strftime("%Y-%m-%d"), set()).add(t.strftime("%H:%M"))
    thin = {d: len(grid) - len(v & set(grid)) for d, v in per_day.items() if len(v & set(grid)) < len(grid)}
    want = {d for d in (expect_days or set()) if d >= cut.strftime("%Y-%m-%d")}
    missing = sorted(want - set(per_day))
    return {"recent_sessions": len(per_day), "recent_thin_sessions": len(thin), "recent_thin_bars": sum(thin.values()),
            "recent_missing_sessions": len(missing), "recent_worst": sorted(thin.items(), key=lambda kv: -kv[1])[:3],
            "recent_missing_days": missing[:5]}


def holes(ks, tf, is_stock, expect_days=None):
    """Runs of absent bars on the file's own grid. Crypto trades 24/7 so any gap above one period is one;
    stocks are measured inside regular hours only, and a whole trading day with no bars is counted apart."""
    per = PERIOD[tf]
    es = rth_epochs(ks) if (is_stock and per < 86400) else sorted(e for e in (epoch(k) for k in ks) if e is not None)
    rec = {"missing_bars": 0, "holes": 0, "biggest_hole": 0, "missing_sessions": 0}
    if len(es) < 2:
        return rec
    for a, b in zip(es, es[1:]):
        step = int(round((b - a) / per)) - 1
        if step <= 0 or (is_stock and (b - a) > 4 * 3600):
            continue
        rec["missing_bars"] += step
        rec["holes"] += 1
        rec["biggest_hole"] = max(rec["biggest_hole"], step)
    if expect_days:
        have = session_days(ks)
        rec["missing_sessions"] = len([d for d in expect_days if d not in have])
    return rec


def expected_days(p, first, last):
    """Trading days the symbol's own sibling D file proves existed inside the intraday file's span.

    Weekends are dropped: some D files carry Saturday/Sunday rows (built from a 24/7 source), and trusting them
    reported COIN as missing 2026-09-20, 09-27, 10-04 and 10-11 — every one a Sunday.
    """
    d = p.parent / (p.name.rsplit("_", 1)[0] + "_D.json")
    try:
        rows = load_rows(d)
    except Exception:
        return set()
    out = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        day = str(r.get("timestamp"))[:10]
        if not (first[:10] <= day <= last[:10]):
            continue
        try:
            if dt.date.fromisoformat(day).weekday() < 5:
                out.add(day)
        except ValueError:
            continue
    return out


def store_files():
    out = []
    for p in KC.rglob("*.json"):
        parts = set(p.relative_to(KC).parts[:-1])
        if parts & {".guard_snap", "gateway", "macbook", "backups", "consolidated_klines"} or ".tmp" in p.name:
            continue
        if tf_of(p.name):
            out.append(p)
    return sorted(out)


def mirror_rel(root, p):
    rel = str(p.relative_to(root))
    return rel[:-3] if rel.endswith(".gz") else rel


def alt_rels(rel):
    """An older copy may use an older layout: stocks used to sit at the cache root, they now sit under tradier/."""
    name = Path(rel).name
    return [rel, name, f"tradier/{name}"]


def pick(idx, rel):
    for k in alt_rels(rel):
        if k in idx:
            return idx[k]
    return None


def index_mirror(root):
    """rel -> path for every kline file in a copy (nested dirs flattened onto the store's own layout)."""
    idx = {}
    if not root.is_dir():
        return idx
    for p in list(root.rglob("*.json")) + list(root.rglob("*.json.gz")):
        if ".tmp" in p.name or ".guard_snap" in p.parts:
            continue
        if not tf_of(p.name):
            continue
        rel = mirror_rel(root, p)
        if rel not in idx or p.stat().st_size > idx[rel].stat().st_size:
            idx[rel] = p
    return idx


def audit(only=None, with_mirrors=True, gap_tfs=("15m", "1h", "4h", "D"), window_days=30):
    mirrors = [(n, r, k, index_mirror(r)) for n, r, k in MIRRORS] if with_mirrors else []
    rows_out, tally = [], {"files": 0, "with_holes": 0, "missing_bars": 0, "missing_sessions": 0, "mirror_unique": 0}
    for p in store_files():
        rel = str(p.relative_to(KC))
        if only and not any(o in rel for o in only):
            continue
        tf = tf_of(p.name)
        ks = stamps(p)
        if not ks:
            rows_out.append({"rel": rel, "rows": 0, "action": "unreadable"})
            continue
        is_stock = not is_crypto(rel)
        a, b = min(ks), max(ks)
        # only the 15m file carries a per-session expectation: an HTF stock file legitimately covers fewer days than
        # its own D sibling, which read as 98633 "missing sessions" across the 4h files.
        exp = expected_days(p, a, b) if (is_stock and tf == "15m") else None
        h = holes(ks, tf, is_stock, exp)
        rec = {"rel": rel, "rows": len(ks), "head": a[:10], "tail": b[:10], "span_d": round((epoch(b) - epoch(a)) / 86400, 1), **h}
        if is_stock and tf == "15m":
            rec.update(recent_window(ks, exp, days=window_days))
            tally["recent_missing_sessions"] = tally.get("recent_missing_sessions", 0) + rec["recent_missing_sessions"]
            tally["symbols_with_30d_holes"] = tally.get("symbols_with_30d_holes", 0) + bool(rec["recent_missing_sessions"])
        uniq = {}
        for name, root, keep, idx in mirrors:
            q = pick(idx, rel)
            if not q:
                continue
            extra = stamps(q) - ks
            if extra:
                uniq[name] = len(extra)
        if uniq:
            rec["mirror_unique"] = uniq
            tally["mirror_unique"] += 1
        tally["files"] += 1
        if tf in gap_tfs:
            tally["history_files"] = tally.get("history_files", 0) + 1
            tally["with_holes"] += bool(h["missing_bars"] or h["missing_sessions"])
            tally["missing_bars"] += h["missing_bars"]
            tally["missing_sessions"] += h["missing_sessions"]
        else:
            rec["rolling_tf"] = True
        rows_out.append(rec)
    return rows_out, tally


JOIN_TFS = ("15m", "1h", "4h", "D")


def build_plan(records, join_tfs=JOIN_TFS):
    """One klines_multi_restore item per store file that some copy can still deepen.

    History timeframes only, and deliberately so: the 1m/3m/5m files in klines_cache are the LIVE rolling caches that
    ez_prices/ez_indicators read every tick, so pouring two years of 3m history into them would slow the live loop for
    data that has its own home in data/klines_{3m,5m}_hist_w2 (now covered by the external backup). klines_multi_restore
    only defines periods for these four for the same reason.
    """
    idx = {n: index_mirror(r) for n, r, _ in MIRRORS}
    plan = []
    for rec in records:
        if not rec.get("mirror_unique") or tf_of(Path(rec["rel"]).name) not in join_tfs:
            continue
        srcs = [{"kind": "json", "path": str(pick(idx[n], rec["rel"]))} for n in rec["mirror_unique"] if pick(idx.get(n, {}), rec["rel"])]
        if srcs:
            plan.append({"target": rec["rel"], "tf": tf_of(Path(rec["rel"]).name), "sources": srcs})
    return plan


def prune_run_backups(keep=3):
    """klines_multi_restore gzips every file it is about to grow. Nightly that is a fresh copy of each changed file,
    i.e. the same accumulation this tool exists to stop — keep the last few runs and drop the rest."""
    try:
        runs = sorted((ROOT / "backups").glob("before_klines_unify_*"), key=lambda p: p.name)
        for old in runs[:-keep] if keep else runs:
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


def join(records, apply, min_free_gb, join_tfs=JOIN_TFS):
    import klines_multi_restore as MR

    plan = build_plan(records, join_tfs)
    bk = ROOT / "backups" / f"before_klines_unify_{time.strftime('%Y%m%d%H%M', time.gmtime())}"
    prune_run_backups()
    tally = {}
    for item in plan:
        try:
            r = MR.restore(item, apply, min_free_gb, bk)
        except Exception as e:
            r = {"target": item["target"], "action": "error", "why": f"{type(e).__name__}: {e}"[:160]}
        k = str(r.get("action", "?")).split(" ")[0]
        tally[k] = tally.get(k, 0) + 1
        print(json.dumps(r), flush=True)
        if k == "refused_disk":
            break
    return {"planned": len(plan), "tally": tally, "backup_dir": str(bk)}


def dedupe(apply):
    """A copy may only go once the store holds every timestamp it has — that is the whole point of the join."""
    store = {str(p.relative_to(KC)): p for p in store_files()}
    cache = {}
    out = []
    for name, root, keep in MIRRORS:
        if keep or not root.is_dir():
            out.append({"root": str(root), "name": name, "verdict": "keep" if keep else "absent"})
            continue
        idx = index_mirror(root)
        unique_files, unique_stamps, orphans = 0, 0, 0
        for rel, p in idx.items():
            tgt = pick(store, rel)
            if tgt is None:
                orphans += 1
                continue
            if tgt not in cache:
                cache[tgt] = stamps(tgt)
            extra = stamps(p) - cache[tgt]
            if extra:
                unique_files += 1
                unique_stamps += len(extra)
        size_gb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) / 1e9
        rec = {"root": str(root), "name": name, "files": len(idx), "gb": round(size_gb, 2), "unique_files": unique_files, "unique_stamps": unique_stamps, "orphan_files": orphans}
        if unique_stamps or orphans:
            rec["verdict"] = "HOLD — join it first"
        elif not apply:
            rec["verdict"] = f"would reclaim {size_gb:.2f} GB"
        else:
            shutil.rmtree(root, ignore_errors=True)
            rec["verdict"] = f"reclaimed {size_gb:.2f} GB"
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--join", action="store_true")
    ap.add_argument("--dedupe", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None, help="comma substrings of rel to limit the pass")
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    ap.add_argument("--window-days", type=int, default=30, help="the fit window whose completeness is reported apart from old history")
    ap.add_argument("--join-tfs", default="15m,1h,4h,D", help="timeframes joined into the store; the fast caches roll by design and are read live every tick")
    ap.add_argument("--gap-tfs", default="15m,1h,4h,D", help="timeframes a GAP is reported for; 1m/3m/5m caches roll by design and are still joined")
    ap.add_argument("--report", default=None, help="write the audit records to this JSON file")
    ap.add_argument("--from-report", default=None, help="join from an existing report instead of re-auditing (the audit is a ~30 min full-store parse)")
    ap.add_argument("--store", default=None, help="measure against this store instead of the repo's klines_cache (for the external-drive backups)")
    ap.add_argument("--mirror", action="append", default=[], help="NAME=PATH extra copy to compare, repeatable; replaces the built-in S1 list")
    a = ap.parse_args()
    if a.store:
        globals()["KC"] = Path(os.path.expanduser(a.store))
    if a.mirror:
        globals()["MIRRORS"] = [(m.split("=", 1)[0], Path(os.path.expanduser(m.split("=", 1)[1])), False) for m in a.mirror]
    elif platform.system() == "Darwin":
        sys.exit("S1 only unless --store/--mirror name the copies: the Mac's own klines_cache keeps ~1800 bars per file by design")
    only = [x for x in (a.only or "").split(",") if x]
    jtfs = tuple(x for x in (a.join_tfs or "").split(",") if x)
    if a.from_report and a.join and not a.audit:
        recs = json.loads(Path(a.from_report).read_text())["records"]
        if only:
            recs = [r for r in recs if any(o in r["rel"] for o in only)]
        print(json.dumps({"join": join(recs, a.apply, a.min_free_gb, jtfs), "from_report": a.from_report}), flush=True)
        return 0
    if a.audit or a.join:
        recs, tally = audit(only=only, with_mirrors=True, gap_tfs=tuple(x for x in (a.gap_tfs or "").split(",") if x), window_days=a.window_days)
        if a.audit:
            for r in recs:
                if (not r.get("rolling_tf") and (r.get("missing_bars") or r.get("missing_sessions"))) or r.get("mirror_unique") or r.get("action"):
                    print(json.dumps(r), flush=True)
        if a.report:
            Path(a.report).write_text(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tally": tally, "records": recs}))
        print(json.dumps({"audit": tally}), flush=True)
        if a.join:
            print(json.dumps({"join": join(recs, a.apply, a.min_free_gb, jtfs)}), flush=True)
    if a.dedupe:
        for r in dedupe(a.apply):
            print(json.dumps(r), flush=True)
    if not (a.audit or a.join or a.dedupe):
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
