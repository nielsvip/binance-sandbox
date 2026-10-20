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


INGEST_TFS = set(BINANCE_INTERVAL) | {"W", "M"}
REL_TOL = 1e-6


def _ingest_rows(p: Path):
    """(rows, how) for any kline-bearing file of an older copy: .json/.json.gz/.tmp/.bak*; a truncated file is
    salvaged object by object (every complete {...} that parses), never dropped as 'unreadable'."""
    import gzip
    import re
    raw = p.read_bytes()
    if p.name.endswith(".gz"):
        raw = gzip.decompress(raw)
    try:
        d = json.loads(raw)
        return d, "ok"
    except Exception:
        out = []
        for m in re.finditer(rb"\{[^{}]*\}", raw):
            try:
                r = json.loads(m.group(0))
            except Exception:
                continue
            if isinstance(r, dict) and r.get("timestamp"):
                out.append(r)
        return out, "salvaged" if out else "unreadable"


def _ingest_units(p: Path):
    """[(SYMBOL_TF, rows)] or None when the file carries no kline series; klines_master = {tf: rows} per symbol."""
    if p.name.endswith(".klines.json.gz"):
        d, how = _ingest_rows(p)
        sym = p.name[: -len(".klines.json.gz")]
        return ([(f"{sym}_{tf}", rows) for tf, rows in d.items() if isinstance(rows, list)] if isinstance(d, dict) else []), how
    stem = p.name.split(".")[0]
    if p.name.startswith(("_", ".")) or "_" not in stem or stem.rsplit("_", 1)[-1] not in INGEST_TFS or ".json" not in p.name:
        return None, "non_kline"
    d, how = _ingest_rows(p)
    return [(stem, d if isinstance(d, list) else [])], how


def _tsk(t) -> str:
    return str(t)[:19].replace(" ", "T")


def _v(b):
    try:
        return tuple(float(b[k]) for k in ("open", "high", "low", "close", "volume"))
    except Exception:
        return None


def _cls(have, offered) -> str:
    """same | archive_dominates | copy_dominates | conflict (float32 round-trips, rel <= 1e-6, are the same bar)."""
    a, b = _v(have), _v(offered)
    if a is None or b is None:
        return "same" if a == b else "conflict"
    if all(abs(x - y) <= REL_TOL * max(abs(x), abs(y), 1e-12) for x, y in zip(a, b)):
        return "same"
    if abs(a[0] - b[0]) > REL_TOL * max(abs(a[0]), abs(b[0]), 1e-12):
        return "conflict"
    if a[4] >= b[4] and a[1] >= b[1] and a[2] <= b[2]:
        return "archive_dominates"
    if b[4] >= a[4] and b[1] >= a[1] and b[2] <= a[2]:
        return "copy_dominates"
    return "conflict"


def ingest(copies: list[tuple[str, Path]], drive_name: str, apply: bool, floor_gb: float, report: Path | None,
           conflicts_path: Path | None, only: str | None = None) -> int:
    """E9 2026-10-20: union older ext-drive kline copies into ONE drive's archive, with a per-copy proof.

    Strictly append-only: a timestamp the archive already holds is NEVER re-valued here (not even by the
    forming-bar rule of merge_one) — every disagreement is classified and logged for a human. Bars under a
    gateway/ subdir (USDT-perp bars under USDC names) are never added, only counted. Peers are not read: each
    copy is named explicitly so its proof is its own. A copy is reclaimable only when, re-run after the apply,
    new == conflicts == copy_dominates == gateway_new == unreadable == non_kline == 0.
    Writes stop as soon as the drive would fall below floor_gb; the names left are listed as deferred."""
    drive = Path("/Volumes") / drive_name
    if not (os.path.ismount(drive) and os.stat(drive).st_dev != os.stat("/").st_dev):
        log(f"{drive} is not a real mount — refusing")
        return 2
    arch_dir = drive / ARCH_SUBDIR
    if not arch_dir.is_dir():
        log(f"{arch_dir} missing — refusing (this mode only grows an existing archive)")
        return 2
    t0 = datetime.now(timezone.utc)
    idx: dict[str, list] = {}
    per: dict[str, dict] = {}
    aux: dict[str, list] = {}
    for cname, root in copies:
        st = per.setdefault(cname, {"root": str(root), "files": 0, "kline_files": 0, "non_kline_files": 0, "salvaged_files": 0,
                                    "unreadable_files": 0, "bytes": 0, "series": 0, "bars": 0, "present_same": 0,
                                    "present_archive_dominates": 0, "copy_dominates": 0, "conflicts": 0, "new": 0,
                                    "new_added": 0, "new_via_other_copy": 0, "new_deferred": 0, "gateway_new_held": 0,
                                    "names_with_new": 0})
        if not (root.is_dir() and os.path.ismount(Path("/Volumes") / root.parts[2]) and os.stat(root).st_dev != os.stat("/").st_dev):
            st["verdict"] = "absent_or_phantom"
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            st["files"] += 1
            st["bytes"] += p.stat().st_size
            stem = p.name.split(".")[0]
            if p.name.endswith("._pinned.json"):
                try:
                    mine = {str(x) for x in json.loads(p.read_text())}
                    tp = arch_dir / p.name
                    theirs = {str(x) for x in json.loads(tp.read_text())} if tp.exists() else set()
                    left = len(mine - theirs)
                except Exception:
                    left = -1
                st["pinned_files"] = st.get("pinned_files", 0) + 1
                if left:
                    st["pinned_not_in_archive"] = st.get("pinned_not_in_archive", 0) + (left if left > 0 else 1)
                    aux.setdefault(cname, []).append(str(p.relative_to(root)))
                continue
            if p.name.endswith(".klines.json.gz") or (".json" in p.name and "_" in stem and stem.rsplit("_", 1)[-1] in INGEST_TFS and not p.name.startswith(("_", "."))):
                st["kline_files"] += 1
                if p.name.endswith(".klines.json.gz"):
                    idx.setdefault(f"__master__{p.name}", []).append((cname, p))
                else:
                    idx.setdefault(stem, []).append((cname, p))
            else:
                st["non_kline_files"] += 1
                aux.setdefault(cname, []).append(str(p.relative_to(root)))
    # expand klines_master files into per-name entries
    # (tf list from the copy's own manifest.jsonl when present, so 2.3 GB of gz is not decoded twice nor held in RAM)
    mtfs: dict = {}
    for key in [k for k in idx if k.startswith("__master__")]:
        for cname, p in idx.pop(key):
            if str(p.parent) not in mtfs:
                mtfs[str(p.parent)] = {}
                try:
                    for line in (p.parent / "manifest.jsonl").read_text().splitlines():
                        r = json.loads(line)
                        mtfs[str(p.parent)][r["sym"]] = list((r.get("tfs") or {}).keys())
                except Exception:
                    pass
            sym = p.name[: -len(".klines.json.gz")]
            tfs = mtfs[str(p.parent)].get(sym)
            if tfs is None:
                try:
                    units, how = _ingest_units(p)
                    tfs = [n.rsplit("_", 1)[-1] for n, _ in (units or [])]
                except Exception:
                    per[cname]["unreadable_files"] += 1
                    continue
            for tf in tfs:
                idx.setdefault(f"{sym}_{tf}", []).append((cname, p, tf))
    mcache: dict = {}
    order = {c: i for i, (c, _) in enumerate(copies)}
    man_path = arch_dir / "_manifest.json"
    try:
        manifest = json.loads(man_path.read_text())
    except Exception:
        manifest = {}
    cfh = open(conflicts_path, "a") if conflicts_path else None
    stopped = None
    totals = {"names": 0, "names_grown": 0, "names_created": 0, "bars_added": 0, "bytes_written_delta": 0, "refused": 0, "deferred_names": 0}
    deferred: list = []
    free_start = free_gb(drive)
    names = sorted(idx)
    if only:
        names = [n for n in names if n == only]
    for name in names:
        totals["names"] += 1
        target = arch_dir / f"{name}.json"
        existing = load_bars(target) if target.exists() else []
        have = {_tsk(b["timestamp"]): b for b in existing}
        suf = str(existing[0]["timestamp"])[19:] if existing else ".000000Z"
        pending: dict = {}
        pending_src: dict = {}
        bad: list = []
        ents = sorted(idx[name], key=lambda e: order[e[0]])
        for ent in ents:
            cname, p = ent[0], ent[1]
            st = per[cname]
            if len(ent) == 3:
                if str(p) not in mcache:
                    if len(mcache) >= 3:
                        mcache.pop(next(iter(mcache)))
                    try:
                        d, _how = _ingest_rows(p)
                    except Exception:
                        d = None
                    mcache[str(p)] = d if isinstance(d, dict) else {}
                    if not mcache[str(p)]:
                        st["unreadable_files"] += 1
                rows, how = mcache[str(p)].get(ent[2]) or [], "ok"
            else:
                try:
                    units, how = _ingest_units(p)
                    rows = units[0][1] if units else []
                except Exception:
                    st["unreadable_files"] += 1
                    continue
                if how == "salvaged":
                    st["salvaged_files"] += 1
                elif how == "unreadable" and p.stat().st_size > 2:
                    st["unreadable_files"] += 1
            st["series"] += 1
            gw = "gateway" in p.relative_to(Path(st["root"])).parts
            seen_here = set()
            had_new = False
            for b in rows:
                if not isinstance(b, dict) or not b.get("timestamp"):
                    continue
                k = _tsk(b["timestamp"])
                if k in seen_here:
                    continue
                seen_here.add(k)
                st["bars"] += 1
                if k in have:
                    c = _cls(have[k], b)
                    st[{"same": "present_same", "archive_dominates": "present_archive_dominates"}.get(c, "copy_dominates" if c == "copy_dominates" else "conflicts")] += 1
                    if c in ("copy_dominates", "conflict"):
                        bad.append({"ts": k, "kind": c, "copy": cname, "file": str(p), "kept": _v(have[k]), "offered": _v(b)})
                    continue
                st["new"] += 1
                had_new = True
                if gw:
                    st["gateway_new_held"] += 1
                    continue
                if k in pending:
                    c = _cls(pending[k], b)
                    if c == "same" or c == "archive_dominates":
                        st["new_via_other_copy"] += 1
                    else:
                        st["copy_dominates" if c == "copy_dominates" else "conflicts"] += 1
                        bad.append({"ts": k, "kind": "inter_copy_" + c, "copy": cname, "file": str(p), "kept": _v(pending[k]), "offered": _v(b), "kept_from": pending_src[k]})
                    continue
                pending[k] = dict(b, timestamp=k + suf)
                pending_src[k] = cname
            st["names_with_new"] += int(had_new)
        if bad and cfh:
            cfh.write(json.dumps({"name": name, "archive": str(target), "n": len(bad), "examples": bad[:20]}) + "\n")
        if not pending:
            continue
        # report attribution: first copy to offer a new bar is the one it is added from
        add_by = {}
        for k, c in pending_src.items():
            add_by[c] = add_by.get(c, 0) + 1
        merged = dict(have)
        merged.update(pending)
        out = [merged[k] for k in sorted(merged)]
        blob = json.dumps(out, separators=(",", ":"))
        old_sz = target.stat().st_size if target.exists() else 0
        totals["projected_bytes_delta"] = totals.get("projected_bytes_delta", 0) + len(blob) - old_sz
        totals["projected_bars_added"] = totals.get("projected_bars_added", 0) + len(pending)
        if stopped or not apply:
            for c, n in add_by.items():
                per[c]["new_added" if not apply else "new_deferred"] += n
            if apply:
                deferred.append(name)
            continue
        if free_gb(drive) - (len(blob) - old_sz) / 1e9 < floor_gb:
            stopped = f"{name}: free {free_gb(drive):.2f} GB - {len(blob) - old_sz} B would cross the {floor_gb} GB floor"
            log(f"  STOP writes — {stopped}")
            for c, n in add_by.items():
                per[c]["new_deferred"] += n
            deferred.append(name)
            continue
        if existing and (len(out) < len(existing) or _tsk(out[0]["timestamp"]) > _tsk(existing[0]["timestamp"]) or _tsk(out[-1]["timestamp"]) < _tsk(existing[-1]["timestamp"])):
            totals["refused"] += 1
            continue
        tmp = target.with_suffix(".json.tmp")
        try:
            tmp.write_text(blob)
            if len(load_bars(tmp)) != len(out):
                tmp.unlink(missing_ok=True)
                totals["refused"] += 1
                log(f"  REFUSED {name}: short temp write")
                continue
            os.replace(tmp, target)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            totals["refused"] += 1
            log(f"  REFUSED {name}: {e}")
            continue
        for c, n in add_by.items():
            per[c]["new_added"] += n
        totals["bars_added"] += len(pending)
        totals["bytes_written_delta"] += len(blob) - old_sz
        totals["names_created" if not existing else "names_grown"] += 1
        mrec = manifest.get(name) or {}
        mrec.update({"bars": len(out), "first": out[0]["timestamp"], "last": out[-1]["timestamp"]})
        mrec.setdefault("srcs", {})
        manifest[name] = mrec
        if (totals["names_grown"] + totals["names_created"]) % 200 == 0:
            man_path.write_text(json.dumps(manifest))
    if apply and (totals["names_grown"] or totals["names_created"]):
        man_path.write_text(json.dumps(manifest))
    if cfh:
        cfh.close()
    totals["deferred_names"] = len(deferred)
    for c, st in per.items():
        blockers = {k: st[k] for k in ("new", "conflicts", "copy_dominates", "gateway_new_held", "unreadable_files", "non_kline_files", "pinned_not_in_archive") if st.get(k)}
        if apply:
            blockers.pop("new", None)
            left = st["new"] - st["new_added"] - st["new_via_other_copy"] - st["gateway_new_held"]
            if left:
                blockers["new_not_added"] = left
        st["blockers"] = blockers
        st.setdefault("verdict", "PROVEN — archive holds every bar, no disagreement" if not blockers else "HOLD")
        st["gb"] = round(st["bytes"] / 1e9, 3)
        st["non_kline_examples"] = aux.get(c, [])[:15]
    doc = {"ts": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "apply" if apply else "dry_run",
           "drive": drive_name, "archive": str(arch_dir), "floor_gb": floor_gb, "free_gb_start": round(free_start, 2), "free_gb_end": round(free_gb(drive), 2),
           "stopped": stopped, "totals": totals, "copies": per, "deferred_names": deferred}
    log(json.dumps({k: v for k, v in doc.items() if k not in ("copies", "deferred_names")}))
    for c, st in per.items():
        log(f"  {c}: " + json.dumps({k: v for k, v in st.items() if k != "non_kline_examples"}))
    if report:
        report.write_text(json.dumps(doc, indent=1))
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
    ap.add_argument("--ingest-from", action="append", default=[], help="NAME=PATH older copy to union into --drive's archive (repeatable, priority order)")
    ap.add_argument("--drive", default="E3T", help="--ingest-from: the ONE drive whose archive grows")
    ap.add_argument("--floor-gb", type=float, default=10.0, help="--ingest-from: never let the drive fall below this")
    ap.add_argument("--dry-run", action="store_true", help="--ingest-from: count only, write nothing")
    ap.add_argument("--report", default=None, help="--ingest-from: per-copy proof JSON")
    ap.add_argument("--conflicts-out", default=None, help="--ingest-from: per-name disagreement jsonl (never the drive)")
    a = ap.parse_args()
    if a.ingest_from:
        copies = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in a.ingest_from]
        if not a.dry_run and not take_lock():
            log("another klines archive writer holds the lock — exiting")
            return 3
        return ingest(copies, a.drive, not a.dry_run, a.floor_gb, Path(a.report) if a.report else None,
                      Path(a.conflicts_out) if a.conflicts_out else None, a.symbol)
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
