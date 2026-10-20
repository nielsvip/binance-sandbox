#!/usr/bin/env python3
"""npz_bar_ingest — apply the Mac-computed 15m NPZ bars to S1's NPZs. No rebuild, no recompute.

USER 2026-10-17: "we want to use macbook for the latest npz bar calculations for both tradier and ez -> rsync to
s1 -> s1 adds it immediately to the existing npz's. No compute wasted as macbook already did the computing within
the indicator scripts; s1 never has to rebuild npz's it just uses the updates just like it appends the klines
without fetching them."

The Mac half is `npz_live_generator.export_bar_for_symbol`, reached from the SAME call both indicator scripts
already make every 15 min (`update_npz_for_symbol`): on a non-S1 host it now computes the row with the live
calculator and stages it as JSON instead of returning False on the first line. `tools/npz_bar_push_s1.sh` ships
the staging dir here. This script applies it.

WHY IT IS SAFE TO APPLY A SHIPPED ROW:
  * It is applied through `npz_live_generator._append_row_to_npz`, the SAME function that writes a bar when the
    generator runs on S1 — all-or-nothing per-key alignment, carry-forward for keys the row does not carry, no
    invented keys, and the never-shrink guard in `_save_npz_atomic`. Nothing is reimplemented here.
  * A row is applied ONLY when it is the next bar that actually EXISTS in that symbol's own klines (no bar of
    its own sits between the NPZ's last bar and the row). That makes it venue-agnostic — a stock's overnight or
    weekend gap is not a hole — and it means a real hole is left to tools/npz_bar_refresh.py, which rebuilds the
    tail with the builder. This script never fabricates and never fills a gap.
  * A symbol with no NPZ is SKIPPED (never created): `_append_row_to_npz` would trigger a full precompute, which
    is exactly the compute this path exists to avoid.

usage:
  python3 tools/npz_bar_ingest.py                 # report what would be applied
  python3 tools/npz_bar_ingest.py --apply
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("NPZ_REFRESH_ROOT", os.path.expanduser("~/binance-sandbox")))
if not (ROOT / "backtest_v8").exists():
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IND = ROOT / "backtest_v8" / "indicators"
INBOX = Path(os.environ.get("NPZ_BAR_INBOX", str(ROOT / "data" / "npz_bar_inbox")))
DONE = INBOX / "applied"
REJECTED = INBOX / "rejected"
# 2026-10-20: hole-blocked bars are filed here instead of being re-read every cycle (see HOLE_LEFT_TO_REFRESHER).
DEFERRED = INBOX / "deferred"
REPORT = ROOT / "data" / "reports" / "npz_bar_ingest.json"
BAR = 900


def log(msg):
    print(f"[npz_bar_ingest {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def npz_last_ts(symbol):
    import numpy as np
    p = IND / f"{symbol}.npz"
    if not p.exists():
        return None
    try:
        with np.load(p, allow_pickle=True) as z:
            if "timestamps" not in z.files:
                return None
            t = int(np.asarray(z["timestamps"]).astype(np.int64).reshape(-1)[-1])
            return t // 1000 if t > 1e11 else t
    except Exception:
        return None


def klines_between(symbol, mode, lo, hi):
    """bars of the symbol's OWN 15m klines with lo < ts < hi — a non-empty result means a real hole."""
    subs = ["klines_cache_backtest/tradier", "klines_cache/tradier"] if mode == "tradier" else ["klines_cache_backtest", "klines_cache"]
    import datetime as dt
    for sub in subs:
        p = ROOT / sub / f"{symbol}_15m.json"
        if not p.exists():
            continue
        try:
            sz = p.stat().st_size
            with open(p, "rb") as f:  # tail only: a hole near the head is not our business
                f.seek(max(0, sz - 400_000))
                txt = f.read().decode("utf-8", "ignore")
        except OSError:
            continue
        out = []
        for m in re.findall(r'"timestamp":\s*"([0-9T:\-\.]+)Z?"', txt):
            try:
                t = int(dt.datetime.strptime(m[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())
            except ValueError:
                continue
            if lo < t < hi:
                out.append(t)
        return sorted(set(out))
    return []


BASE_SUFFIXES = ("_15m", "_5m", "_3m", "_1m")
BARE_BASE = ("timestamps", "open", "high", "low", "close", "volume")
TABLE = ROOT / "data" / "npz_label_codes.json"
_table_cache = {}


def label_table():
    """The MEASURED label -> int8 code table (tools/npz_label_code_derive.py). Never a guess, never a default."""
    if "t" not in _table_cache:
        try:
            _table_cache["t"] = json.loads(TABLE.read_text()).get("pinned") or {}
        except Exception:
            _table_cache["t"] = {}
    return _table_cache["t"]


def iso_to_epoch(v):
    import datetime as dt
    try:
        return int(dt.datetime.strptime(str(v)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())
    except ValueError:
        return None


def select_base_tf(row, npz_path):
    """Keep ONLY the base-TF keys the shipped row can move; everything else carries forward.

    MEASURED 2026-10-17 (data/reports/npz_label_code_derive.json, 75 recomputed rows): every 15m categorical maps
    to its stored int8 code unanimously, while EVERY HTF categorical conflicts -- wt_cross_4h BEAR split -1/+1
    64:11, ha_1h green +1/-1 28:13, wt_wave_phase_4h EXPANDING +1/-1 57:10. The cause is not noise: the HTF value
    on a 15m row is the builder's AS-OF BROADCAST of the last closed HTF bar, and a live recompute reads that TF's
    own latest close instead. So an HTF key cannot be moved from a shipped row at all, and it does not need to be --
    it is unchanged until its bucket closes, which is what carry-forward gives, with npz_bar_refresh's builder tail
    the only writer that moves it.

    Three ways a key leaves here, all fail-closed (dropped = carried forward, never fabricated):
      HTF            -- suffix is not a base TF
      UNMAPPED_LABEL -- a string for an integer-dtype key whose label is not in the measured table
      BAD_TIMESTAMP  -- an ISO availability stamp that would not parse
    """
    import numpy as np
    keep, why = {}, {"HTF": [], "UNMAPPED_LABEL": [], "BAD_TIMESTAMP": []}
    dtypes = {}
    try:
        with np.load(npz_path, allow_pickle=True) as z:
            for k in z.files:
                dtypes[k] = np.asarray(z[k]).dtype
    except Exception:
        return row, {"PROBE_FAILED": list(row)[:0]}
    table = label_table()
    for k, v in row.items():
        if not (k in BARE_BASE or k.endswith(BASE_SUFFIXES)):
            why["HTF"].append(k)
            continue
        dt_k = dtypes.get(k)
        if dt_k is not None and dt_k.kind in "iu" and isinstance(v, str):
            if k.startswith("timestamp"):
                e = iso_to_epoch(v)
                if e is None:
                    why["BAD_TIMESTAMP"].append(k)
                    continue
                keep[k] = e
                continue
            # A NUMBER shipped as a string is not a label. MEASURED 2026-10-17: wt_cross_bars_ago_15m arrived as
            # "4"/"7"/"12" and was being dropped as an unmappable label, carrying a stale bar count forward on
            # every trickled bar. Only a genuinely non-numeric string needs the measured table.
            try:
                keep[k] = int(float(v))
                continue
            except ValueError:
                pass
            code = (table.get(k) or {}).get(v)
            if code is None:
                why["UNMAPPED_LABEL"].append(f"{k}={v}")
                continue
            keep[k] = int(code)
            continue
        keep[k] = v
    return keep, {kk: vv for kk, vv in why.items() if vv}


def strip_htf(row):
    """Drop W/M keys from a shipped row. The producing host does not have the history to compute them.

    MEASURED 2026-10-17, and this guard exists because it ALREADY HAPPENED: 3 of 432 staged stock bars
    (ABBV/ACN/ADP) carried 118 W + 118 M keys whose timestamp_W was 2026-03-23 -- the Mac's *_W.json files are 7
    months stale and sit on an 05:00-ET grid, not the builder's Monday-00:00Z W-MON bucket, and its 15m history
    spans 95d (= 14 weekly bars) against the 200 ema_200_W needs. Applying them would have written a March weekly
    high into today's row of every board's input.
    Dropping them is not a loss: _append_row_to_npz carries an absent per-bar key forward, which IS the value until
    the bucket closes, and tools/npz_bar_refresh.py (deep klines + the real builder) is the only writer that moves a
    W/M key. npz_live_generator now drops W/M off S1 at the source; this is the fail-closed half, so a producer
    regression cannot reach an NPZ."""
    bad = [k for k in row if k.endswith(("_W", "_M", "_W_prev", "_M_prev"))]
    for k in bad:
        row.pop(k, None)
    return row, bad


def sanitize_row(npz_path, row):
    """Map JSON null to what the store already uses for "no value".

    The Mac holds no NPZs, so it cannot know the target dtype — it ships JSON null. Unmapped, that aborts the
    whole append: `np.array([None], dtype=float32)` raises inside _append_row_to_npz's all-or-nothing loop, which
    is exactly how the first 6 shipped stock bars failed (wt_divergence_15m / wt_divergence_D came back None for
    "no divergence"). MEASURED: those keys are int8 in the store with 0 for none, so numeric -> 0 is the store's
    own encoding; an object array can hold None as-is."""
    nones = [k for k, v in row.items() if v is None]
    if not nones:
        return row, []
    import numpy as np
    fixed = []
    try:
        with np.load(npz_path, allow_pickle=True) as z:
            for k in nones:
                if k not in z.files:
                    row.pop(k, None)
                    fixed.append(f"{k}: dropped (absent from NPZ)")
                    continue
                dt = np.asarray(z[k]).dtype
                if dt == object:
                    continue
                row[k] = 0
                fixed.append(f"{k}: None -> 0 ({dt})")
    except Exception as e:
        return row, [f"dtype probe failed: {type(e).__name__}"]
    return row, fixed


def claim(f, done_dir):
    """Move a staged bar aside, tolerating a concurrent run that already took it.

    2026-10-17: the installed cron runs this under `flock -n /tmp/npz_bar_ingest.lock` while an operator run does
    not, so two instances globbed the same inbox and the second died with FileNotFoundError mid-apply, abandoning
    the rest of the batch. A vanished file is not an error -- the other instance has it, and the ts<=last check
    already makes a double apply a no-op."""
    try:
        done_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(done_dir / f.name))
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        log(f"could not file {f.name} under applied/: {e}")
        return False


def self_lock():
    """Take the SAME lock the cron takes, so an operator run and the cron can never interleave."""
    import fcntl
    try:
        fh = open(os.environ.get("NPZ_BAR_INGEST_LOCK", "/tmp/npz_bar_ingest.lock"), "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hole-defer-bars", type=int, default=int(os.environ.get("NPZ_BAR_HOLE_DEFER_BARS", "2")),
                    help="file a hole-blocked staged bar into deferred/ when more than this many of its own bars are missing (0 = never defer)")
    ap.add_argument("--max-age-h", type=float, default=float(os.environ.get("NPZ_BAR_MAX_AGE_H", "72")),
                    help="sanity bound only. A STOCK's latest bar is legitimately hours old (the 20:00Z session "
                         "close) and over a weekend ~65 h, so this must not be tight: correctness comes from the "
                         "hole check below (the row must be the next bar that exists in the symbol's own klines), "
                         "not from wall-clock age. 6 h rejected every overnight stock bar when first tried.")
    a = ap.parse_args()
    _lock = None
    if a.apply:
        _lock = self_lock()
        if _lock is None:
            log("another ingest holds the lock — exiting (its batch covers this inbox)")
            return 0
    if not INBOX.is_dir():
        log(f"no inbox at {INBOX} — nothing to do")
        return 0
    files = sorted(INBOX.glob("*.json"))
    if not files:
        log(f"inbox empty ({INBOX})")
        return 0
    import npz_live_generator as gen
    counts, applied, skipped = {}, [], []
    no_npz_syms = set()
    now = time.time()
    # oldest bar first per symbol so a two-bar backlog applies in order
    staged = []
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            counts["UNREADABLE"] = counts.get("UNREADABLE", 0) + 1
            skipped.append({"file": f.name, "why": f"unreadable {type(e).__name__}"})
            continue
        staged.append((int(d.get("ts") or 0), f, d))
    staged.sort()
    # One worker per SYMBOL, symbols in parallel. MEASURED 2026-10-17: a single-threaded batch applied 132 bars in
    # ~6 min (~2.7 s each -- _append_row_to_npz rewrites the whole store through np.savez_compressed), so the
    # symbols late in the batch slipped a second bar and crypto sat at 1-2 bars behind (75 at 1, 76 at 2) instead
    # of uniformly 1. Threads are the right pool here: the cost is zlib + file I/O, both of which drop the GIL.
    # Per-symbol ordering is preserved (oldest staged bar first) because a two-bar backlog must apply in sequence,
    # and each symbol takes npz_bar_refresh's OWN sym_lock so this can never write a store the refresher is tailing.
    by_sym = {}
    for ts, f, d in staged:
        by_sym.setdefault((d.get("symbol") or "?"), []).append((ts, f, d))
    lock = __import__("threading").Lock()

    def bump(key, n=1):
        """counts[k] = counts.get(k,0)+n is a read-modify-write and loses increments across threads."""
        with lock:
            counts[key] = counts.get(key, 0) + n

    def handle(items):
        """Hold npz_bar_refresh's own per-symbol lock for the whole symbol, so this and the refresher's tail pass
        can never write the same store at once (the claim made in the comment above, enforced here)."""
        sym = items[0][2].get("symbol") if items else None
        fd = None
        if a.apply and sym:
            try:
                from tools.npz_bar_refresh import sym_lock
                fd = sym_lock(sym)
                if fd is None:
                    bump("SKIP_REFRESHER_HOLDS_SYMBOL")
                    return
            except Exception:
                fd = None
        try:
            for ts, f, d in items:
                one_bar(ts, f, d)
        finally:
            if fd is not None:
                try:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError:
                    pass

    def one_bar(ts, f, d):
        sym, mode, row = d.get("symbol"), d.get("mode") or "crypto", d.get("row") or {}
        if not sym or not row or not ts:
            bump("MALFORMED")
            return
        age_h = (now - ts) / 3600.0
        last = npz_last_ts(sym)
        if last is None:
            # The producer ships every symbol it tracks; this host has no NPZ for some of them and must NOT create
            # one from a shipped bar (that would trigger a full precompute, the exact compute this path avoids).
            # The file is filed under rejected/ rather than left in place: MEASURED 2026-10-17, 293 of 507 staged
            # bars were NO_NPZ, and leaving them would re-read and re-report the same files every cycle and grow the
            # inbox by ~28k files/day (293 x 96 cycles).
            bump("NO_NPZ")
            with lock:
                no_npz_syms.add(sym)
            if a.apply:
                claim(f, REJECTED)
            return
        if ts <= last:
            bump("ALREADY_HAVE")
            if a.apply:
                claim(f, DONE)
            return
        if age_h > a.max_age_h:
            bump("TOO_OLD")
            skipped.append({"symbol": sym, "ts": ts, "why": f"staged bar {age_h:.1f}h old (> {a.max_age_h}h)"})
            return
        hole = klines_between(sym, mode, last, ts)
        if hole:
            bump("HOLE_LEFT_TO_REFRESHER")
            skipped.append({"symbol": sym, "ts": ts, "why": f"{len(hole)} of its own bars sit between {last} and {ts}"})
            # 2026-10-20 A HOLE-BLOCKED BAR MUST BE FILED ASIDE, exactly as NO_NPZ already is above and for the same
            # reason that comment gives ("leaving them would re-read and re-report the same files every cycle").
            # MEASURED 2026-10-20: 748 hole-blocked bars across 15 symbols had accumulated and every 2-minute cycle
            # re-read all of them, refused all of them, and re-took those symbols' locks to do it — while the only
            # thing that CAN close a hole, tools/npz_bar_refresh.py, needs those same locks. A targeted refresh of the
            # 19 stuck NPZs returned LOCKED_SYMBOL on 8 of them, held by the ingest run that was busy refusing their
            # bars. That is a live-lock: the ingest locks the symbol in order to say "not mine", and the repair can
            # never get in. 12 of the 19 are live-traded, stuck 115-2,023 bars behind.
            # Filing to deferred/ loses nothing: a bar the refresher has since passed becomes ALREADY_HAVE, and the
            # file is still on disk for inspection. Only bars whose hole is WIDER than --hole-defer-bars are filed, so
            # a one-bar pipeline hiccup still gets its normal retry next cycle.
            if a.apply and len(hole) > a.hole_defer_bars:
                claim(f, DEFERRED)
            return
        if not a.apply:
            bump("WOULD_APPLY")
            applied.append({"symbol": sym, "ts": ts, "keys": len(row)})
            return
        n_shipped = len(row)
        row, dropped = select_base_tf(row, IND / f"{sym}.npz")
        if dropped:
            for reason, keys in dropped.items():
                bump(f"DROP_{reason}", len(keys))
            if dropped.get("UNMAPPED_LABEL"):
                log(f"{sym}: {len(dropped['UNMAPPED_LABEL'])} unmapped labels carried forward instead of guessed: {dropped['UNMAPPED_LABEL'][:4]}")
        row, fixes = sanitize_row(IND / f"{sym}.npz", row)
        try:
            ok = gen._append_row_to_npz(IND / f"{sym}.npz", row)
        except Exception as e:
            ok = False
            skipped.append({"symbol": sym, "ts": ts, "why": f"append raised {type(e).__name__}: {e}"[:140]})
        if ok and npz_last_ts(sym) == ts:
            bump("APPLIED")
            applied.append({"symbol": sym, "ts": ts, "keys_applied": len(row), "keys_shipped": n_shipped,
                            "carried_forward": {k: len(v) for k, v in dropped.items()}, "null_fixes": fixes})
            claim(f, DONE)
        else:
            bump("APPEND_FAILED")
            skipped.append({"symbol": sym, "ts": ts, "why": "append returned False or the tail did not move"})

    if a.apply:
        from multiprocessing.pool import ThreadPool
        workers = max(1, min(int(os.environ.get("NPZ_INGEST_WORKERS", "8")), len(by_sym) or 1))
        log(f"applying {len(staged)} staged bars across {len(by_sym)} symbols on {workers} workers")
        pool = ThreadPool(workers)
        try:
            for _ in pool.imap_unordered(handle, list(by_sym.values())):
                pass
        finally:
            pool.close()
            pool.join()
    else:
        for items in by_sym.values():
            handle(items)

    log(f"{'APPLIED' if a.apply else 'DRY-RUN'} inbox={len(files)} {counts}")
    if no_npz_syms:
        log(f"NO_NPZ: {len(no_npz_syms)} symbols this host holds no NPZ for (filed under rejected/, never created): "
            + ",".join(sorted(no_npz_syms)[:8]) + ("..." if len(no_npz_syms) > 8 else ""))
    if skipped:
        log("skipped (first 6): " + json.dumps(skipped[:6])[:420])
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"at": now, "apply": a.apply, "inbox": len(files), "counts": counts,
                                      "applied": applied[:60], "skipped": skipped[:60],
                                      "no_npz_symbols": sorted(no_npz_syms)}, indent=1, sort_keys=True, default=str))
        log(f"report -> {REPORT}")
    except OSError as e:
        log(f"report write failed: {e}")
    # prune applied/ so the dir cannot grow without bound
    try:
        for d, keep_s in ((DONE, 86400), (REJECTED, 21600)):
            for p in d.glob("*.json"):
                if now - p.stat().st_mtime > keep_s:
                    p.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
