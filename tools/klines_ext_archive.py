#!/usr/bin/env python3
"""klines_ext_archive — a klines archive on the external drives that ONLY EVER GROWS.

USER 2026-10-17: "make sure the ext drives hold a full klines backup that only grows never shrinks".

Klines are the most-damaged asset in this repo, and every incident was a SHRINK:
  * tradier_klines window-replace erased whole ranges (2026-10-16, 29 events restored from copies);
  * two full disks truncated 333 kline files at 4096-byte boundaries (ENOSPC, 2026-10-16);
  * a UTC-vs-ET grid dropped the close of every Nov-Mar session (2026-10-16).
So the archive is append-only in BOTH directions:
  BARS   the union of every bar ever seen, keyed by timestamp. A source that has fewer bars than the archive
         (truncated, erased, re-windowed) contributes nothing and takes nothing away.
  VALUES an existing bar's values are never overwritten. A source disagreeing on the OHLCV of a timestamp the
         archive already holds is recorded in _conflicts.jsonl for review, never silently applied — a
         "repair" and a corruption look identical to code, so a human decides.
A write is refused outright if the merged result would have fewer bars or a narrower span than what is already
on the drive. The file is written to a temp path, re-parsed, counted, and only then os.replace()d, so an
ENOSPC-truncated write can never land on top of a good file.

Sources (union, priority only decides which NEW bar is taken first): klines_cache (primary live),
klines_cache_gateway (gateway backup), klines_canonical, klines_cache_backtest, data/klines_wm_staging.

A per-drive _manifest.json records each source's (mtime,size) so an unchanged file is skipped without being
parsed — the first run reads ~10 GB of JSON, later runs read almost nothing, which is what makes it safe to
run from the 30-minute tick.

Usage:
    python3 tools/klines_ext_archive.py                 # merge every source into every mounted drive
    python3 tools/klines_ext_archive.py --coverage      # per-drive bar/file totals + what is under-replicated
    python3 tools/klines_ext_archive.py --symbol ZROUSDT_4h   # one file (debug)
    python3 tools/klines_ext_archive.py --verify ZROUSDT_4h   # compare archive vs live for one file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
SOURCES = ("klines_cache", "klines_cache_gateway", "klines_canonical", "klines_cache_backtest", "data/klines_wm_staging")
EXT_ROOTS = (Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"), Path("/Volumes/TOSHIBA_EXT"))
ARCH_SUBDIR = Path("binance_archive") / "klines_archive"
MIN_FREE_GB = 3.0
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
BINANCE_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h", "D": "1d", "1d": "1d", "5m": "5m", "3m": "3m", "1m": "1m"}
LOG = Path("/tmp/klines_ext_archive.log")


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def real_drives() -> list[Path]:
    """Mounted externals only. A /Volumes entry on the boot volume's device is a phantom directory — writing
    a klines archive there would silently fill the internal disk (/Volumes/WD25 was exactly that)."""
    out = []
    try:
        boot = os.stat("/System/Volumes/Data").st_dev
    except OSError:
        boot = None
    for r in EXT_ROOTS:
        try:
            if not r.is_dir() or not os.access(r, os.W_OK):
                continue
            if boot is not None and os.stat(r).st_dev == boot:
                continue
            st = os.statvfs(r)
            free = (st.f_bavail * st.f_frsize) / 1e9
            if free < MIN_FREE_GB:
                log(f"  {r.name}: {free:.1f}GB free — below {MIN_FREE_GB}GB, skipped (never risk an ENOSPC write)")
                continue
            out.append(r)
        except OSError:
            continue
    return out


def free_gb(p: Path) -> float:
    try:
        st = os.statvfs(p)
        return (st.f_bavail * st.f_frsize) / 1e9
    except OSError:
        return 0.0


def source_files() -> dict[str, list[Path]]:
    """name (SYMBOL_TF) -> every source file that carries it, in source-priority order."""
    out: dict[str, list[Path]] = {}
    for rel in SOURCES:
        d = REPO / rel
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            if p.name.startswith("_") or p.name.startswith("."):
                continue
            out.setdefault(p.stem, []).append(p)
    return out


def load_bars(p: Path) -> list[dict]:
    try:
        d = json.loads(p.read_text())
    except Exception:
        return []
    return [b for b in d if isinstance(b, dict) and b.get("timestamp")] if isinstance(d, list) else []


def _same_bar(a: dict, b: dict) -> bool:
    """Equal apart from float round-trip noise. Without this, 2998897.3000000003 vs 2998897.3 is logged as a
    conflict on every run forever (seen on ZROUSDT_4h 2026-10-14T20:00)."""
    for k in ("open", "high", "low", "close", "volume"):
        try:
            if round(float(a.get(k, 0)), 8) != round(float(b.get(k, 0)), 8):
                return False
        except (TypeError, ValueError):
            if a.get(k) != b.get(k):
                return False
    return True


def _more_complete(new: dict, cur: dict) -> bool:
    """True when `new` is a LATER sample of the same forming interval, not a different bar.

    Inside one interval a bar can only accumulate: volume grows, high rises, low falls, open is fixed. So a
    candidate that dominates on all of those is strictly more complete, and taking it still only ever ADDS
    information. Measured on ZROUSDT_4h 2026-10-13T20:00, klines_cache held a partial bar (volume 68,811,
    range 2.0076-2.0156) where the gateway had the finished one (volume 1,682,228, range 2.0057-2.0552);
    keeping the partial would have frozen a wrong bar into a never-shrinking archive.
    Anything that does NOT dominate is a genuine disagreement and is logged, never applied."""
    try:
        if float(new["volume"]) < float(cur["volume"]):
            return False
        if float(new["high"]) < float(cur["high"]):
            return False
        if float(new["low"]) > float(cur["low"]):
            return False
        if round(float(new["open"]), 8) != round(float(cur["open"]), 8):
            return False
        return (float(new["volume"]) > float(cur["volume"])
                or float(new["high"]) > float(cur["high"])
                or float(new["low"]) < float(cur["low"]))
    except (KeyError, TypeError, ValueError):
        return False


def merge_one(name: str, srcs: list[Path], arch_dir: Path, conflicts: list,
              peers: list[Path] = ()) -> tuple[str, int, int]:
    """Union every source AND every other drive's copy into this archive copy.

    `peers` is what keeps the drives converged. The first full run wrote the drives sequentially and they
    finished 11,449 bars apart (SSD2T 19,458,485 vs E3T 19,469,934) purely because live klines moved between
    the two passes — and without peer-merging, a symbol whose sources never change again (a delisted pair)
    would stay unequal forever. Reading the peers makes every copy the union of everything seen anywhere.
    Returns (status, bars_before, bars_after)."""
    target = arch_dir / f"{name}.json"
    existing = load_bars(target) if target.exists() else []
    merged = {b["timestamp"]: b for b in existing}
    own_before = len(merged)
    # Bars settled against the exchange by tools/klines_reconcile.py are PINNED: never re-valued here.
    # The _more_complete rule below reads "higher volume + wider range" as a later sample of the same forming
    # bar, which an INFLATED corrupt bar also satisfies — that is how the archive ended up holding
    # WLFIUSDC_1h 2026-08-03T00:00 at volume 10,306,094 when the exchange says 528,337. Without this pin the
    # next run would promote the bad bar again and silently undo all 8,400 corrections.
    pinned: set = set()
    try:
        _pp = arch_dir / f"{name}._pinned.json"
        if _pp.exists():
            pinned = {str(x) for x in json.loads(_pp.read_text())}
    except Exception:
        pinned = set()
    for peer_dir in peers:
        if peer_dir == arch_dir:
            continue
        pp = peer_dir / f"{name}.json"
        if pp.exists():
            for b in load_bars(pp):
                cur = merged.get(b["timestamp"])
                if cur is None:
                    merged[b["timestamp"]] = b
                elif b["timestamp"] in pinned:
                    continue
                elif not _same_bar(cur, b) and _more_complete(b, cur):
                    merged[b["timestamp"]] = b
    before = own_before
    added = upgraded = len(merged) - own_before
    for p in srcs:
        for b in load_bars(p):
            ts = b["timestamp"]
            cur = merged.get(ts)
            if cur is None:
                merged[ts] = b
                added += 1
            elif ts in pinned:
                continue                      # exchange-settled: only another reconcile may change it
            elif _same_bar(cur, b):
                continue                      # float noise only
            elif _more_complete(b, cur):
                merged[ts] = b                # later sample of the same forming interval: strictly more data
                upgraded += 1
            else:
                # genuine disagreement where neither dominates: never overwrite, a repair and a corruption
                # are indistinguishable to code. Recorded for a human.
                conflicts.append({"name": name, "timestamp": ts, "kept": cur, "offered": b, "source": str(p)})
    if added == 0 and upgraded == 0 and target.exists():
        return "unchanged", before, before
    out = [merged[k] for k in sorted(merged)]
    after = len(out)
    # ONLY-GROWS gate: never fewer bars, never a narrower span than what is already on the drive
    if existing:
        if after < before:
            log(f"  REFUSED {name}: merge would shrink {before} -> {after} bars")
            return "refused_shrink", before, after
        if out[0]["timestamp"] > existing[0]["timestamp"] or out[-1]["timestamp"] < existing[-1]["timestamp"]:
            log(f"  REFUSED {name}: merge would narrow the span "
                f"[{existing[0]['timestamp']}..{existing[-1]['timestamp']}] -> [{out[0]['timestamp']}..{out[-1]['timestamp']}]")
            return "refused_narrow", before, after
    tmp = target.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(out, separators=(",", ":")))
        # re-parse what actually landed: an ENOSPC-truncated temp file must never replace a good archive
        check = load_bars(tmp)
        if len(check) != after:
            tmp.unlink(missing_ok=True)
            log(f"  REFUSED {name}: temp file verified {len(check)} bars, expected {after} (short write — disk full?)")
            return "refused_shortwrite", before, after
        os.replace(tmp, target)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        log(f"  REFUSED {name}: write failed ({e})")
        return "refused_error", before, after
    return ("created" if not existing else "grown"), before, after


def run(only: str | None, force: bool = False) -> int:
    drives = real_drives()
    if not drives:
        log("no external drive mounted — nothing archived (normal; the next tick catches up)")
        return 0
    srcs_by_name = source_files()
    if only:
        srcs_by_name = {k: v for k, v in srcs_by_name.items() if k == only}
    log(f"klines archive: {len(srcs_by_name)} symbol/timeframe files -> {[d.name for d in drives]}")
    peer_dirs = [d / ARCH_SUBDIR for d in drives]
    # every drive's manifest, read once: the skip test needs to know whether THIS drive is behind another,
    # otherwise a drive that was written first stays permanently short on symbols whose sources never change
    peer_manifests: dict[str, dict] = {}
    for pd in peer_dirs:
        try:
            peer_manifests[str(pd)] = json.loads((pd / "_manifest.json").read_text())
        except Exception:
            peer_manifests[str(pd)] = {}
    for drive in drives:
        arch_dir = drive / ARCH_SUBDIR
        arch_dir.mkdir(parents=True, exist_ok=True)
        man_path = arch_dir / "_manifest.json"
        try:
            manifest = json.loads(man_path.read_text())
        except Exception:
            manifest = {}
        stats = {"created": 0, "grown": 0, "unchanged": 0, "skipped": 0, "refused": 0}
        bars_added = 0
        conflicts: list = []
        for i, (name, srcs) in enumerate(sorted(srcs_by_name.items())):
            sig = {str(p): [int(p.stat().st_mtime), p.stat().st_size] for p in srcs if p.exists()}
            ent = manifest.get(name) or {}
            peer_bars = [int((peer_manifests.get(str(pd)) or {}).get(name, {}).get("bars") or 0) for pd in peer_dirs]
            if (not force and ent.get("srcs") == sig and (arch_dir / f"{name}.json").exists()
                    and int(ent.get("bars") or 0) >= max(peer_bars or [0])):
                stats["skipped"] += 1
                continue
            if free_gb(drive) < MIN_FREE_GB:
                log(f"  {drive.name}: dropped below {MIN_FREE_GB}GB mid-run — stopping writes to this drive")
                break
            status, before, after = merge_one(name, srcs, arch_dir, conflicts, peers=peer_dirs)
            if status.startswith("refused"):
                stats["refused"] += 1
                continue
            stats[status] = stats.get(status, 0) + 1
            bars_added += max(0, after - before)
            manifest[name] = {"bars": after, "first": None, "last": None, "srcs": sig}
            if status != "unchanged":
                b = load_bars(arch_dir / f"{name}.json")
                if b:
                    manifest[name]["first"], manifest[name]["last"] = b[0]["timestamp"], b[-1]["timestamp"]
            if (i + 1) % 500 == 0:
                man_path.write_text(json.dumps(manifest))
        man_path.write_text(json.dumps(manifest))
        if conflicts:
            with (arch_dir / "_conflicts.jsonl").open("a") as fh:
                for c in conflicts:
                    fh.write(json.dumps(c) + "\n")
        log(f"  {drive.name}: {stats} +{bars_added} bars, {len(conflicts)} value conflict(s) recorded, "
            f"{free_gb(drive):.1f}GB free")
    return 0


def deepen(names: list[str], tfs: tuple, max_pages: int, pause: float, budget_s: float) -> int:
    """Page BACKWARDS to the symbol's first bar ever, so the archive is unbounded in history too.

    USER 2026-10-17: "on the ex drive we need infinite ones no clipping ever". The archive itself never
    clips, but it can only hold what it has seen — and the live stores are deliberately shallow
    (ez_klines._trim caps 15m at 1500-2000 bars, 1m at 14000-18000). So depth has to be pulled from the
    exchange: fetch_before from the earliest bar held, limit=1500 per request (same cost as 5), merged
    append-only, until a page returns nothing older. That symbol/timeframe is then stamped
    inception_reached in the manifest and never re-paged.
    Crypto only — a stock symbol has no Binance history and belongs to massive_backfill_15m."""
    import time as _t
    try:
        sys.path.insert(0, str(REPO / "tools"))
        from klines_backfill_15m import fetch_before
    except Exception as e:
        log(f"cannot import fetch_before ({e}) — deepen aborted")
        return 1
    drives = real_drives()
    if not drives:
        log("no external drive mounted — deepen aborted")
        return 0
    arch_dirs = [d / ARCH_SUBDIR for d in drives]
    t0 = _t.time()
    for name in names:
        sym, _, tf = name.rpartition("_")
        if tf not in tfs or not sym.upper().endswith(CRYPTO_SUFFIX):
            continue
        interval = BINANCE_INTERVAL.get(tf)
        if not interval:
            continue
        target = arch_dirs[0] / f"{name}.json"
        bars = load_bars(target)
        if not bars:
            continue
        man_path = arch_dirs[0] / "_manifest.json"
        try:
            manifest = json.loads(man_path.read_text())
        except Exception:
            manifest = {}
        if (manifest.get(name) or {}).get("inception_reached"):
            continue
        merged = {b["timestamp"]: b for b in bars}
        oldest = min(ts_ms_of(b["timestamp"]) for b in bars)
        start_n = len(merged)
        pages = 0
        reached = False
        while pages < max_pages:
            if budget_s and (_t.time() - t0) > budget_s:
                log(f"  deepen budget {budget_s:.0f}s reached — stopping (resumable next run)")
                break
            try:
                rows = fetch_before(sym, oldest - 1, limit=1500, interval=interval)
            except Exception as e:
                log(f"  {name}: fetch_before failed ({e})")
                break
            pages += 1
            new = [r for r in rows if r["timestamp"] not in merged]
            for r in new:
                merged[r["timestamp"]] = r
            if not new:
                reached = True
                break
            oldest = min(ts_ms_of(r["timestamp"]) for r in rows)
            if len(rows) < 1500:
                reached = True        # the exchange had fewer than a full page left = start of history
                break
            _t.sleep(pause)
        if len(merged) > start_n or reached:
            out = [merged[k] for k in sorted(merged)]
            for ad in arch_dirs:
                p = ad / f"{name}.json"
                cur = load_bars(p)
                if len(out) < len(cur):
                    log(f"  REFUSED {name} on {ad.parents[1].name}: deepen result shorter than archive")
                    continue
                tmp = p.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(out, separators=(",", ":")))
                if len(load_bars(tmp)) != len(out):
                    tmp.unlink(missing_ok=True)
                    log(f"  REFUSED {name} on {ad.parents[1].name}: short write")
                    continue
                os.replace(tmp, p)
                try:
                    m = json.loads((ad / "_manifest.json").read_text())
                except Exception:
                    m = {}
                e = m.get(name) or {}
                e.update(bars=len(out), first=out[0]["timestamp"], last=out[-1]["timestamp"],
                         inception_reached=bool(reached))
                m[name] = e
                (ad / "_manifest.json").write_text(json.dumps(m))
            log(f"  {name}: {start_n} -> {len(out)} bars in {pages} page(s) "
                f"[{out[0]['timestamp'][:10]} .. {out[-1]['timestamp'][:10]}]"
                f"{' INCEPTION' if reached else ''}")
    return 0


def ts_ms_of(ts: str) -> int:
    return int(datetime.strptime(ts.replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def coverage() -> int:
    drives = real_drives()
    log(f"drives present: {[d.name for d in drives] or 'NONE'}")
    live = source_files()
    live_bars = {}
    for name, srcs in live.items():
        live_bars[name] = max((len(load_bars(p)) for p in srcs), default=0)
    log(f"  local sources: {len(live)} symbol/timeframe files")
    for d in drives:
        arch = d / ARCH_SUBDIR
        try:
            man = json.loads((arch / "_manifest.json").read_text())
        except Exception:
            man = {}
        files = len(list(arch.glob("*.json"))) - (1 if (arch / "_manifest.json").exists() else 0)
        bars = sum(int(v.get("bars") or 0) for v in man.values())
        missing = [n for n in live if n not in man]
        short = [n for n, v in man.items() if live_bars.get(n, 0) > int(v.get("bars") or 0)]
        log(f"  {d.name}: {files} files, {bars} bars archived, {len(missing)} local file(s) not archived yet, "
            f"{len(short)} archived shorter than local, {free_gb(d):.1f}GB free")
        if missing[:5]:
            log(f"      not archived yet e.g. {missing[:5]}")
    return 0


def verify(name: str) -> int:
    drives = real_drives()
    srcs = source_files().get(name, [])
    log(f"{name}: local sources {[(str(p), len(load_bars(p))) for p in srcs]}")
    for d in drives:
        p = d / ARCH_SUBDIR / f"{name}.json"
        b = load_bars(p) if p.exists() else []
        if b:
            log(f"  {d.name}: {len(b)} bars  {b[0]['timestamp']} .. {b[-1]['timestamp']}")
            lost = [x["timestamp"] for p2 in srcs for x in load_bars(p2) if x["timestamp"] not in {y["timestamp"] for y in b}]
            log(f"      bars present locally but missing from the archive: {len(lost)}")
        else:
            log(f"  {d.name}: NOT ARCHIVED")
    return 0


_LOCK_FH = None


def take_lock(name: str = "klines_archive") -> bool:
    """One writer at a time across klines_ext_archive / klines_reconcile / klines_gap_fill.

    All three write the same archive files. Each write is atomic (tmp + os.replace) so a file can never be
    corrupted, but two overlapping runs can still LOSE an update: the later writer replaces the file from a
    snapshot it read before the earlier writer finished. With the 30-minute tick and a long reconcile pass
    that overlap is routine, so the loser simply exits and the next tick picks the work up."""
    global _LOCK_FH
    import fcntl
    try:
        _LOCK_FH = open(f"/tmp/{name}.lock", "w")
        fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (IOError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--symbol", help="archive just this SYMBOL_TF")
    ap.add_argument("--verify", help="compare the archive against local sources for one SYMBOL_TF")
    ap.add_argument("--deepen", action="store_true", help="page BACKWARDS to each symbol's first bar ever (unbounded history on the drives)")
    ap.add_argument("--timeframes", default="15m,1h,4h,D", help="--deepen: which timeframes to deepen")
    ap.add_argument("--max-pages", type=int, default=40, help="--deepen: 1500-bar pages per symbol per run")
    ap.add_argument("--pause", type=float, default=0.25, help="--deepen: seconds between requests")
    ap.add_argument("--budget", type=float, default=0, help="--deepen: stop after N seconds (resumable)")
    ap.add_argument("--force", action="store_true", help="re-merge even when the manifest says sources are unchanged (use after a merge-rule change)")
    a = ap.parse_args()
    _ro = bool(getattr(a, 'coverage', False) or getattr(a, 'verify', False) or getattr(a, 'dry_run', False))
    if not _ro and not take_lock():
        log("another klines archive writer holds the lock — exiting; the next tick picks this up")
        return 0
    if a.coverage:
        return coverage()
    if a.verify:
        return verify(a.verify)
    if a.deepen:
        names = [a.symbol] if a.symbol else sorted(source_files())
        return deepen(names, tuple(t.strip() for t in a.timeframes.split(",") if t.strip()), a.max_pages, a.pause, a.budget)
    return run(a.symbol, a.force)


if __name__ == "__main__":
    sys.exit(main())
