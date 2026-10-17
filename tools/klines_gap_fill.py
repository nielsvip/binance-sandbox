#!/usr/bin/env python3
"""klines_gap_fill — never leave a hole: look in every folder first, and only then fetch (1500 bars at a time).

USER 2026-10-17: "if any bar is missing they must look in any of the 3 folders (gateway/mac/s1) and fill it in
before leaving a gap or fetching (if fetching is necessary fetch 1500 bars it is same cost as 5 bars)".

SOURCE ORDER, strictly. A REST call is the last resort, never the first:
  1. MAC      klines_cache, klines_canonical, klines_cache_backtest, data/klines_wm_staging
  2. GATEWAY  klines_cache_gateway (the Mac's mirror of the gateway host's cache)
  3. S1       ~/binance-sandbox/klines_cache (19 GB, the deep long store), ~/binance/klines_cache,
              both klines_canonical, ~/binance/klines_cache_gateway — pulled per symbol over ssh, once
  4. FETCH    Binance USD-M /fapi/v1/klines via tools.klines_backfill_15m.fetch_after, ALWAYS limit=1500
              even for a one-bar hole, because 1500 bars cost the same as 5 (one request, one weight unit).

A found bar is merged append-only: a timestamp already held is never overwritten here, and the external
archive is updated through tools/klines_ext_archive (which refuses any write that would shrink or narrow it).
Local stores are only touched with --apply-local, because a careless local write is what erased ranges on
2026-10-16; the archive is always safe to update since it cannot shrink by construction.

Gap detection is grid-based per timeframe and INTERIOR ONLY (between the first and last bar held) — the head
and tail are not "gaps", they are just the extent of history. Stock symbols are reported but never fetched:
their sessions are irregular (the UTC-vs-ET winter-grid loss) and tools/massive_backfill_15m.py --fill-holes
owns that repair.

Usage:
    python3 tools/klines_gap_fill.py --symbol ZROUSDT_15m
    python3 tools/klines_gap_fill.py --all --max 50          # scan, fill from folders, fetch what remains
    python3 tools/klines_gap_fill.py --all --no-fetch        # folders + S1 only, no REST at all
    python3 tools/klines_gap_fill.py --symbol X --apply-local  # also repair the local klines_canonical copy
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(REPO))

MAC_DIRS = ("klines_cache", "klines_canonical", "klines_cache_backtest", "data/klines_wm_staging")
GATEWAY_DIRS = ("klines_cache_gateway",)
S1_DIRS = ("binance-sandbox/klines_cache", "binance/klines_cache", "binance-sandbox/klines_canonical",
           "binance/klines_canonical", "binance/klines_cache_gateway")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ConnectTimeout=20"]
S1_HOST = "s1-int"
STEP_S = {"15m": 900, "1h": 3600, "4h": 14400, "D": 86400, "1d": 86400, "5m": 300, "3m": 180, "1m": 60}
BINANCE_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h", "D": "1d", "1d": "1d", "5m": "5m", "1m": "1m"}
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
FETCH_LIMIT = 1500           # USER: same cost as 5 bars
MAX_PAGES_PER_SYMBOL = 40    # per run; a bigger hole simply continues on the next run
FETCH_PAUSE_S = 0.25
LOG = Path("/tmp/klines_gap_fill.log")


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def split_name(name: str) -> tuple[str, str]:
    sym, _, tf = name.rpartition("_")
    return sym, tf


def is_crypto(sym: str) -> bool:
    return sym.upper().endswith(CRYPTO_SUFFIX)


def load(p: Path) -> list[dict]:
    try:
        d = json.loads(p.read_text())
    except Exception:
        return []
    return [b for b in d if isinstance(b, dict) and b.get("timestamp")] if isinstance(d, list) else []


def ts_ms(ts: str) -> int:
    return int(datetime.strptime(ts.replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


def ms_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def absent_path(name: str) -> Path | None:
    """Sidecar of slots a fetch PROVED the exchange does not have. Lives next to the archive."""
    dirs = archive_dirs()
    return (dirs[0] / f"{name}._absent.json") if dirs else None


def load_absent(name: str) -> set[int]:
    """Slots already proven absent upstream, on any drive.

    Without this the tick re-requests unfillable holes forever: 1000000MOGUSDT_1m shows 4408 interior gaps in
    16231 bars, and a thin pair genuinely has minutes with no trades, so those slots can never be filled. A
    gap is only recorded here when a fetch window that SPANNED the slot came back without it — i.e. the
    exchange was asked and had nothing. It is a record of proof, not an assumption."""
    out: set[int] = set()
    for d in archive_dirs():
        p = d / f"{name}._absent.json"
        try:
            out |= {int(x) for x in json.loads(p.read_text())}
        except Exception:
            continue
    return out


def save_absent(name: str, slots: set[int]) -> None:
    for d in archive_dirs():
        p = d / f"{name}._absent.json"
        try:
            cur = {int(x) for x in json.loads(p.read_text())} if p.exists() else set()
            merged = cur | slots
            if merged != cur:
                p.write_text(json.dumps(sorted(merged)))
        except Exception:
            continue


def interior_gaps(bars: list[dict], tf: str) -> list[int]:
    """Missing grid slots strictly between the first and last bar held. Head/tail are extent, not gaps."""
    step = STEP_S.get(tf)
    if not step or len(bars) < 2:
        return []
    have = {ts_ms(b["timestamp"]) for b in bars}
    lo, hi = min(have), max(have)
    step_ms = step * 1000
    # anchor on the grid the data itself uses, so a venue whose bars sit at :30 is not reported as all-missing
    return [t for t in range(lo, hi + 1, step_ms) if t not in have]


def archive_dirs() -> list[Path]:
    out = []
    try:
        boot = os.stat("/System/Volumes/Data").st_dev
    except OSError:
        boot = None
    for r in (Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"), Path("/Volumes/TOSHIBA_EXT")):
        try:
            if r.is_dir() and os.access(r, os.W_OK) and not (boot is not None and os.stat(r).st_dev == boot):
                d = r / "binance_archive" / "klines_archive"
                if d.is_dir():
                    out.append(d)
        except OSError:
            continue
    return out


def local_candidates(name: str) -> list[tuple[str, Path]]:
    out = []
    for rel in MAC_DIRS:
        p = REPO / rel / f"{name}.json"
        if p.exists():
            out.append(("mac", p))
    for rel in GATEWAY_DIRS:
        p = REPO / rel / f"{name}.json"
        if p.exists():
            out.append(("gateway", p))
    for d in archive_dirs():
        p = d / f"{name}.json"
        if p.exists():
            out.append((f"archive:{d.parents[1].name}", p))
    return out


def pull_s1(name: str, tmp: Path) -> list[tuple[str, Path]]:
    """Copy this symbol's S1 files down once, newest-deepest first. Absence of S1 is never fatal."""
    out = []
    for rel in S1_DIRS:
        dest = tmp / (rel.replace("/", "_") + f"_{name}.json")
        r = subprocess.run(["rsync", "-a", "--timeout=90", "-e", " ".join(SSH),
                            f"{S1_HOST}:{rel}/{name}.json", str(dest)],
                           capture_output=True, text=True)
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            out.append((f"s1:{rel}", dest))
    return out


def fetch_missing(sym: str, tf: str, gaps: list[int]) -> tuple[list[dict], int, set[int]]:
    """Fill every hole from the exchange, always limit=1500 per request (USER: same cost as 5 bars).

    Paging continues WITHIN a hole until it is covered: one request returns at most 1500 bars, so the
    15,168-slot contiguous hole in 1000FLOKIUSDT_1h advanced only 1500 per run before this and would have
    needed ten runs to close. Each page starts at the earliest slot still uncovered.
    Returns (bars, n_requests, proven_absent) — a slot inside a window the exchange actually ANSWERED that
    the answer did not contain is proven absent upstream, not merely missing from our copies.
    """
    import time as _t
    try:
        sys.path.insert(0, str(REPO / "tools"))
        from klines_backfill_15m import fetch_after            # type: ignore
    except Exception as e:
        log(f"    cannot import fetch_after ({e}) — no fetching")
        return [], 0, set()
    interval = BINANCE_INTERVAL.get(tf)
    if not interval:
        return [], 0, set()
    step_ms = STEP_S[tf] * 1000
    blocks, cur = [], [gaps[0]]
    for t in gaps[1:]:
        if t == cur[-1] + step_ms:
            cur.append(t)
        else:
            blocks.append(cur)
            cur = [t]
    blocks.append(cur)
    got: list[dict] = []
    absent: set[int] = set()
    calls = 0
    for blk in blocks:
        while calls < MAX_PAGES_PER_SYMBOL:
            covered = {ts_ms(b["timestamp"]) for b in got}
            todo = [t for t in blk if t not in covered]
            if not todo:
                break
            start_ms = todo[0]
            try:
                rows = fetch_after(sym, start_ms, limit=FETCH_LIMIT, interval=interval)
            except Exception as e:
                log(f"    fetch failed at {ms_ts(start_ms)}: {e}")
                break
            calls += 1
            if not rows:
                break
            got.extend(rows)
            span_lo, span_hi = ts_ms(rows[0]["timestamp"]), ts_ms(rows[-1]["timestamp"])
            returned = {ts_ms(r["timestamp"]) for r in rows}
            absent |= {t for t in blk if span_lo <= t <= span_hi and t not in returned}
            log(f"    fetched {len(rows)} bars from {ms_ts(start_ms)} "
                f"(hole of {len(blk)}, {len(todo)} left; limit={FETCH_LIMIT})")
            if len(rows) < FETCH_LIMIT or span_hi <= start_ms:
                break                      # the exchange had nothing more to give from here
            _t.sleep(FETCH_PAUSE_S)
        if calls >= MAX_PAGES_PER_SYMBOL:
            log(f"    page budget {MAX_PAGES_PER_SYMBOL} reached for {sym}_{tf} — resumable next run")
            break
    return got, calls, absent


def write_archive(name: str, bars: list[dict]) -> None:
    """Append-only update of every archive copy: existing timestamps are kept, new ones added, never shrink."""
    for d in archive_dirs():
        p = d / f"{name}.json"
        cur = {b["timestamp"]: b for b in load(p)} if p.exists() else {}
        before = len(cur)
        for b in bars:
            cur.setdefault(b["timestamp"], b)
        if len(cur) == before:
            continue
        out = [cur[k] for k in sorted(cur)]
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, separators=(",", ":")))
        if len(load(tmp)) != len(out):
            tmp.unlink(missing_ok=True)
            log(f"    REFUSED archive write on {d.parents[1].name}: short write")
            continue
        os.replace(tmp, p)
        # Keep _manifest.json honest. This tool used to write the archive file and leave the manifest alone,
        # so klines_ext_archive --coverage read a stale `bars` count and reported "136 archived shorter than
        # local" for files this tool had in fact just FILLED, and the peer-convergence check compared stale
        # numbers. Only bars/first/last are touched: `srcs` stays as it was so the archive tool still
        # re-verifies the sources itself.
        try:
            mp = d / "_manifest.json"
            man = json.loads(mp.read_text()) if mp.exists() else {}
            ent = man.get(name) or {}
            ent.update(bars=len(out), first=out[0]["timestamp"], last=out[-1]["timestamp"])
            man[name] = ent
            mp.write_text(json.dumps(man))
        except Exception as e:
            log(f"    manifest update failed on {d.parents[1].name}: {e}")
        log(f"    archive {d.parents[1].name}: {before} -> {len(out)} bars")


def fill_one(name: str, no_fetch: bool, apply_local: bool) -> dict:
    sym, tf = split_name(name)
    rep = {"name": name, "gaps_before": 0, "from_folders": 0, "from_s1": 0, "fetched": 0, "requests": 0,
           "still_missing": 0, "sources_used": []}
    if tf not in STEP_S:
        rep["skip"] = f"unknown timeframe {tf}"
        return rep
    merged: dict[str, dict] = {}
    for label, p in local_candidates(name):
        for b in load(p):
            merged.setdefault(b["timestamp"], b)
    if not merged:
        rep["skip"] = "no copy anywhere locally"
        return rep
    bars = [merged[k] for k in sorted(merged)]
    known_absent = load_absent(name)
    gaps = [g for g in interior_gaps(bars, tf) if g not in known_absent]
    rep["gaps_before"] = len(gaps)
    rep["known_absent"] = len(known_absent)
    if not gaps:
        return rep
    log(f"  {name}: {len(gaps)} interior gap(s) across {len(bars)} bars "
        f"[{bars[0]['timestamp'][:16]} .. {bars[-1]['timestamp'][:16]}]")
    # the local union already counts as "looked in the mac + gateway + archive folders"
    before_folders = len(merged)
    rep["from_folders"] = before_folders - len({b["timestamp"] for b in load(local_candidates(name)[0][1])})
    # 3. S1
    with tempfile.TemporaryDirectory() as td:
        for label, p in pull_s1(name, Path(td)):
            n0 = len(merged)
            for b in load(p):
                merged.setdefault(b["timestamp"], b)
            if len(merged) > n0:
                rep["from_s1"] += len(merged) - n0
                rep["sources_used"].append(label)
    bars = [merged[k] for k in sorted(merged)]
    gaps = [g for g in interior_gaps(bars, tf) if g not in known_absent]
    # 4. fetch, last resort
    if gaps and not no_fetch and is_crypto(sym):
        got, calls, proven_absent = fetch_missing(sym, tf, gaps)
        rep["requests"] = calls
        if proven_absent:
            save_absent(name, proven_absent)
            rep["proven_absent"] = len(proven_absent)
        n0 = len(merged)
        for b in got:
            merged.setdefault(b["timestamp"], b)
        rep["fetched"] = len(merged) - n0
        if rep["fetched"]:
            rep["sources_used"].append("binance:1500")
        bars = [merged[k] for k in sorted(merged)]
        gaps = [g for g in interior_gaps(bars, tf) if g not in (known_absent | proven_absent)]
    elif gaps and not is_crypto(sym):
        rep["sources_used"].append("stock: no fetch (massive_backfill_15m --fill-holes owns session repair)")
    rep["still_missing"] = len(gaps)
    write_archive(name, bars)
    if apply_local:
        target = REPO / "klines_canonical" / f"{name}.json"
        cur = {b["timestamp"]: b for b in load(target)} if target.exists() else {}
        n0 = len(cur)
        for b in bars:
            cur.setdefault(b["timestamp"], b)
        if len(cur) > n0:
            out = [cur[k] for k in sorted(cur)]
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(out, separators=(",", ":")))
            if len(load(tmp)) == len(out):
                os.replace(tmp, target)
                log(f"    klines_canonical: {n0} -> {len(out)} bars")
            else:
                tmp.unlink(missing_ok=True)
                log("    REFUSED klines_canonical write: short write")
    return rep


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
    ap.add_argument("--symbol", help="one SYMBOL_TF")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max", type=int, default=0, help="--all: stop after this many files with gaps")
    ap.add_argument("--no-fetch", action="store_true", help="folders + S1 only; never call the exchange")
    ap.add_argument("--apply-local", action="store_true", help="also repair klines_canonical (append-only)")
    a = ap.parse_args()
    _ro = bool(getattr(a, 'coverage', False) or getattr(a, 'verify', False) or getattr(a, 'dry_run', False))
    if not _ro and not take_lock():
        log("another klines archive writer holds the lock — exiting; the next tick picks this up")
        return 0
    names = []
    if a.symbol:
        names = [a.symbol]
    elif a.all:
        seen = set()
        for rel in MAC_DIRS + GATEWAY_DIRS:
            d = REPO / rel
            if d.is_dir():
                for p in d.glob("*.json"):
                    if not p.name.startswith(("_", ".")):
                        seen.add(p.stem)
        names = sorted(seen)
    else:
        ap.error("need --symbol or --all")
    log(f"gap fill over {len(names)} file(s); fetch={'OFF' if a.no_fetch else f'limit {FETCH_LIMIT}'}; "
        f"archives={[d.parents[1].name for d in archive_dirs()] or 'NONE'}")
    tot = {"with_gaps": 0, "from_s1": 0, "fetched": 0, "requests": 0, "still_missing": 0}
    for n in names:
        r = fill_one(n, a.no_fetch, a.apply_local)
        if r.get("gaps_before"):
            tot["with_gaps"] += 1
            for k in ("from_s1", "fetched", "requests", "still_missing"):
                tot[k] += r.get(k, 0)
            log(f"    {n}: s1=+{r['from_s1']} fetched=+{r['fetched']} ({r['requests']} req) "
                f"still_missing={r['still_missing']} via {r['sources_used'] or ['folders']}")
            if a.max and tot["with_gaps"] >= a.max:
                log(f"  --max {a.max} reached")
                break
    log(f"TOTAL {tot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
