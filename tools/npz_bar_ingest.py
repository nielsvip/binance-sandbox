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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-age-h", type=float, default=float(os.environ.get("NPZ_BAR_MAX_AGE_H", "72")),
                    help="sanity bound only. A STOCK's latest bar is legitimately hours old (the 20:00Z session "
                         "close) and over a weekend ~65 h, so this must not be tight: correctness comes from the "
                         "hole check below (the row must be the next bar that exists in the symbol's own klines), "
                         "not from wall-clock age. 6 h rejected every overnight stock bar when first tried.")
    a = ap.parse_args()
    if not INBOX.is_dir():
        log(f"no inbox at {INBOX} — nothing to do")
        return 0
    files = sorted(INBOX.glob("*.json"))
    if not files:
        log(f"inbox empty ({INBOX})")
        return 0
    import npz_live_generator as gen
    counts, applied, skipped = {}, [], []
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
    for ts, f, d in staged:
        sym, mode, row = d.get("symbol"), d.get("mode") or "crypto", d.get("row") or {}
        if not sym or not row or not ts:
            counts["MALFORMED"] = counts.get("MALFORMED", 0) + 1
            continue
        age_h = (now - ts) / 3600.0
        last = npz_last_ts(sym)
        if last is None:
            counts["NO_NPZ"] = counts.get("NO_NPZ", 0) + 1
            skipped.append({"symbol": sym, "why": "no NPZ on this host — never created from a shipped bar"})
            continue
        if ts <= last:
            counts["ALREADY_HAVE"] = counts.get("ALREADY_HAVE", 0) + 1
            if a.apply:
                DONE.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(DONE / f.name))
            continue
        if age_h > a.max_age_h:
            counts["TOO_OLD"] = counts.get("TOO_OLD", 0) + 1
            skipped.append({"symbol": sym, "ts": ts, "why": f"staged bar {age_h:.1f}h old (> {a.max_age_h}h)"})
            continue
        hole = klines_between(sym, mode, last, ts)
        if hole:
            counts["HOLE_LEFT_TO_REFRESHER"] = counts.get("HOLE_LEFT_TO_REFRESHER", 0) + 1
            skipped.append({"symbol": sym, "ts": ts, "why": f"{len(hole)} of its own bars sit between {last} and {ts}"})
            continue
        if not a.apply:
            counts["WOULD_APPLY"] = counts.get("WOULD_APPLY", 0) + 1
            applied.append({"symbol": sym, "ts": ts, "keys": len(row)})
            continue
        n_shipped = len(row)
        row, dropped = select_base_tf(row, IND / f"{sym}.npz")
        if dropped:
            for reason, keys in dropped.items():
                counts[f"DROP_{reason}"] = counts.get(f"DROP_{reason}", 0) + len(keys)
            if dropped.get("UNMAPPED_LABEL"):
                log(f"{sym}: {len(dropped['UNMAPPED_LABEL'])} unmapped labels carried forward instead of guessed: {dropped['UNMAPPED_LABEL'][:4]}")
        row, fixes = sanitize_row(IND / f"{sym}.npz", row)
        try:
            ok = gen._append_row_to_npz(IND / f"{sym}.npz", row)
        except Exception as e:
            ok = False
            skipped.append({"symbol": sym, "ts": ts, "why": f"append raised {type(e).__name__}: {e}"[:140]})
        if ok and npz_last_ts(sym) == ts:
            counts["APPLIED"] = counts.get("APPLIED", 0) + 1
            applied.append({"symbol": sym, "ts": ts, "keys_applied": len(row), "keys_shipped": n_shipped,
                            "carried_forward": {k: len(v) for k, v in dropped.items()}, "null_fixes": fixes})
            DONE.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(DONE / f.name))
        else:
            counts["APPEND_FAILED"] = counts.get("APPEND_FAILED", 0) + 1
            skipped.append({"symbol": sym, "ts": ts, "why": "append returned False or the tail did not move"})
    log(f"{'APPLIED' if a.apply else 'DRY-RUN'} inbox={len(files)} {counts}")
    if skipped:
        log("skipped (first 6): " + json.dumps(skipped[:6])[:420])
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"at": now, "apply": a.apply, "inbox": len(files), "counts": counts,
                                      "applied": applied[:60], "skipped": skipped[:60]}, indent=1, sort_keys=True, default=str))
        log(f"report -> {REPORT}")
    except OSError as e:
        log(f"report write failed: {e}")
    # prune applied/ so the dir cannot grow without bound
    try:
        for p in DONE.glob("*.json"):
            if now - p.stat().st_mtime > 86400:
                p.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
