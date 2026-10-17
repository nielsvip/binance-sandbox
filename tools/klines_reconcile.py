#!/usr/bin/env python3
"""klines_reconcile — settle archive value conflicts against the exchange, with an audit trail.

WHY THIS EXISTS (found 2026-10-17, my own bug):
tools/klines_ext_archive.py is append-only in values as well as bars — an existing bar is never overwritten,
because "a repair and a corruption are indistinguishable to code". That is right as a default and wrong as a
final answer. The first archive run read klines_cache first (highest source priority) and for
ZROUSDT_4h 2026-10-13T20:00 that store was holding a mis-sampled sub-window: open 2.0134, volume 68,811.
The gateway offered open 2.0379, volume 1,682,228 — refused, because a differing OPEN means "not the same
forming bar" so the _more_complete rule cannot apply. Checked against Binance afterwards, the gateway was
right and all three local stores now agree with it. So the archive froze a wrong bar in, and 73,727 conflicts
share that shape (946 symbol/timeframes; 9,505 of them in 15m/1h/4h/D, the data the engines actually read).

The exchange is the only authority, so this tool asks it. For every conflicted timestamp it fetches the
authoritative bars (limit=1500 per request, same cost as 5) and:
  * archive value != exchange value  -> the archive is CORRECTED to the exchange value, and the old value is
    written to _corrections.jsonl first. Nothing is lost: the superseded bar stays on record.
  * bar missing from the archive     -> added (the archive only ever grows in bar count).
  * exchange has no such bar         -> left exactly as it is, and recorded as unresolved.
The bar COUNT can never decrease here, and no timestamp is ever removed — only a value is replaced, and only
by the exchange's own answer.

Usage:
    python3 tools/klines_reconcile.py --timeframes 15m,1h,4h,D          # the engine-relevant set
    python3 tools/klines_reconcile.py --symbol IPUSDC_1h                # one file
    python3 tools/klines_reconcile.py --timeframes 15m --dry-run        # report, change nothing
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
EXT_ROOTS = (Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"), Path("/Volumes/TOSHIBA_EXT"))
ARCH_SUBDIR = Path("binance_archive") / "klines_archive"
BINANCE_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h", "D": "1d", "1d": "1d", "5m": "5m", "3m": "3m", "1m": "1m"}
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
FETCH_LIMIT = 1500
PAUSE_S = 0.25
LOG = Path("/tmp/klines_reconcile.log")
INCLUDE_TOKENIZED = False
_TOK: set = set()


def tokenized() -> set:
    """The tokenized stock proxies (data/excluded_tokenized_stocks.json, 221 symbols). Archived, never traded."""
    global _TOK
    if not _TOK:
        try:
            d = json.loads((REPO / "data" / "excluded_tokenized_stocks.json").read_text())
            _TOK = {str(x).upper() for x in (d.get("symbols") or [])}
        except Exception:
            _TOK = {"-"}
    return _TOK


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def arch_dirs() -> list[Path]:
    out = []
    try:
        boot = os.stat("/System/Volumes/Data").st_dev
    except OSError:
        boot = None
    for r in EXT_ROOTS:
        try:
            if r.is_dir() and os.access(r, os.W_OK) and not (boot is not None and os.stat(r).st_dev == boot):
                d = r / ARCH_SUBDIR
                if d.is_dir():
                    out.append(d)
        except OSError:
            continue
    return out


def load_bars(p: Path) -> list[dict]:
    try:
        d = json.loads(p.read_text())
    except Exception:
        return []
    return [b for b in d if isinstance(b, dict) and b.get("timestamp")] if isinstance(d, list) else []


def ts_ms(ts: str) -> int:
    return int(datetime.strptime(ts.replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def same(a: dict, b: dict) -> bool:
    for k in ("open", "high", "low", "close", "volume"):
        try:
            if round(float(a.get(k, 0)), 8) != round(float(b.get(k, 0)), 8):
                return False
        except (TypeError, ValueError):
            return False
    return True


def conflicted_timestamps(tfs: tuple, only: str | None) -> dict[str, set[str]]:
    """name -> the timestamps any drive logged a value conflict for."""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for d in arch_dirs():
        p = d / "_conflicts.jsonl"
        if not p.exists():
            continue
        for line in p.open():
            try:
                c = json.loads(line)
            except Exception:
                continue
            name = c.get("name") or ""
            if only and name != only:
                continue
            tf = name.rpartition("_")[2]
            if tf not in tfs:
                continue
            sym = name.rpartition("_")[0]
            if not sym.upper().endswith(CRYPTO_SUFFIX):
                continue        # only the exchange can settle this, and it only knows crypto
            # USER 2026-10-17: "keep collecting stocks usdt versions if you want we are not using them for
            # now as round trip fees kill their advantages vs free". So the tokenized stock proxies stay in
            # the ARCHIVE (klines_ext_archive takes everything) but are not worth exchange calls to settle:
            # 61,749 of the 73,727 value conflicts are 1m, and the worst offenders there are GOOGLUSDT /
            # TSLAUSDT / PLTRUSDT-class proxies that nothing trades. --include-tokenized overrides.
            if not INCLUDE_TOKENIZED and sym.upper() in tokenized():
                continue
            out[name].add(c["timestamp"])
    return out


def reconcile_one(name: str, stamps: set[str], dry: bool) -> dict:
    rep = {"name": name, "conflicts": len(stamps), "checked": 0, "corrected": 0, "added": 0,
           "already_right": 0, "unresolved": 0, "requests": 0}
    sym, _, tf = name.rpartition("_")
    interval = BINANCE_INTERVAL.get(tf)
    dirs = arch_dirs()
    if not interval or not dirs:
        return rep
    try:
        sys.path.insert(0, str(REPO / "tools"))
        from klines_backfill_15m import fetch_after            # type: ignore
    except Exception as e:
        log(f"  cannot import fetch_after ({e})")
        return rep
    import time as _t
    want = sorted(ts_ms(t) for t in stamps)
    auth: dict[int, dict] = {}
    i = 0
    while i < len(want):
        start = want[i]
        try:
            rows = fetch_after(sym, start, limit=FETCH_LIMIT, interval=interval)
            rep["requests"] += 1
        except Exception as e:
            log(f"  {name}: fetch failed at {start}: {e}")
            break
        if not rows:
            break
        for r in rows:
            auth[ts_ms(r["timestamp"])] = r
        last = ts_ms(rows[-1]["timestamp"])
        nxt = i
        while nxt < len(want) and want[nxt] <= last:
            nxt += 1
        i = nxt if nxt > i else i + 1
        if len(rows) < FETCH_LIMIT:
            break
        _t.sleep(PAUSE_S)
    corrections = []
    for d in dirs:
        p = d / f"{name}.json"
        bars = load_bars(p)
        if not bars:
            continue
        by_ts = {b["timestamp"]: b for b in bars}
        n_before = len(by_ts)
        changed = 0
        for t in want:
            a = auth.get(t)
            if a is None:
                continue
            cur = by_ts.get(a["timestamp"])
            if cur is None:
                by_ts[a["timestamp"]] = a
                changed += 1
                rep["added"] += 1
            elif same(cur, a):
                rep["already_right"] += 1
            else:
                corrections.append({"name": name, "timestamp": a["timestamp"], "was": cur, "now": a,
                                    "authority": "binance_fapi", "at": datetime.now(timezone.utc).isoformat()})
                by_ts[a["timestamp"]] = a
                changed += 1
                rep["corrected"] += 1
        rep["checked"] = len([t for t in want if t in auth])
        rep["unresolved"] = len([t for t in want if t not in auth])
        if changed and not dry:
            out = [by_ts[k] for k in sorted(by_ts)]
            if len(out) < n_before:
                log(f"  REFUSED {name} on {d.parents[1].name}: result would shrink")
                continue
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(out, separators=(",", ":")))
            if len(load_bars(tmp)) != len(out):
                tmp.unlink(missing_ok=True)
                log(f"  REFUSED {name} on {d.parents[1].name}: short write")
                continue
            os.replace(tmp, p)
    if corrections and not dry:
        for d in dirs:
            with (d / "_corrections.jsonl").open("a") as fh:
                for c in corrections:
                    fh.write(json.dumps(c) + "\n")
    # per-drive loop counts corrections once per drive; report the per-drive figure divided back
    if len(dirs) > 1:
        for k in ("corrected", "added", "already_right"):
            rep[k] = rep[k] // len(dirs)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", default="15m,1h,4h,D")
    ap.add_argument("--symbol")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-tokenized", action="store_true", help="also settle the untraded stock-USDT proxies")
    ap.add_argument("--max", type=int, default=0, help="stop after this many symbol/timeframes")
    a = ap.parse_args()
    global INCLUDE_TOKENIZED
    INCLUDE_TOKENIZED = bool(a.include_tokenized)
    tfs = tuple(t.strip() for t in a.timeframes.split(",") if t.strip())
    work = conflicted_timestamps(tfs, a.symbol)
    log(f"reconcile: {len(work)} symbol/timeframe(s) with value conflicts in {tfs}"
        f"{' (DRY RUN)' if a.dry_run else ''}; drives={[d.parents[1].name for d in arch_dirs()]}")
    tot = collections.Counter()
    for n, (name, stamps) in enumerate(sorted(work.items(), key=lambda kv: -len(kv[1]))):
        r = reconcile_one(name, stamps, a.dry_run)
        for k in ("conflicts", "corrected", "added", "already_right", "unresolved", "requests"):
            tot[k] += r.get(k, 0)
        log(f"  {name}: {r['conflicts']} conflicted, corrected={r['corrected']} added={r['added']} "
            f"already_right={r['already_right']} unresolved={r['unresolved']} ({r['requests']} req)")
        if a.max and n + 1 >= a.max:
            log("  --max reached")
            break
    log(f"TOTAL {dict(tot)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
