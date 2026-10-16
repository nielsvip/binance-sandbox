#!/usr/bin/env python3
"""npz_fresh_sync — audit NPZ freshness per symbol, alarm on it, and correct peer copies that drifted BEHIND.

USER 2026-10-16: "make sure they are always up to date from now on" + "never overwritten with smaller versions".

Why a corrective pass exists at all: tools/sync_indicators.sh pushes S1 -> worker with `rsync -azu`, and `-u`
decides by MTIME. `-u` is there for a good reason (a worker that refreshed a stock NPZ locally must not be
overwritten by S1's older copy), but it also means a peer file whose mtime is newer while its CONTENT is older
can never be corrected. MEASURED 2026-10-16 21:05Z, S1 vs s2 over the 332 shared symbols:
  63 symbols were STALER on s2 than on S1 — BNBUSDC S1 3 bars behind / s2 361, DUSKUSDT 4 / 361, BBUSDT 3 / 352,
  AGCO 6 / 317, ZECUSDC 1 / 260, 1000FLOKIUSDT 3 / 256 — i.e. exactly the 3.7-day class in the operator's report,
  and the boards on that host were reading them.
  1 symbol was SHORTER on s2 (AEM 366 rows vs 25083 after repair) — a shrink in the other direction.

What it does: fingerprints every NPZ on this host and on each peer (rows, last bar), then copies a file ONLY in
the direction where the source is strictly better (more rows, or equal rows with a newer last bar) and never
when it would reduce rows on the target. A peer copy that is DEEPER than the local one is reported, not pulled —
tools/npz_repair.py owns adoption, so there is exactly one code path that replaces a local store.

usage:
  python3 tools/npz_fresh_sync.py                        # audit this host only, write the report + alert
  python3 tools/npz_fresh_sync.py --peers 10.0.0.4       # audit + report the drift
  python3 tools/npz_fresh_sync.py --peers 10.0.0.4 --push  # correct the drifted peer copies
"""
import argparse
import datetime as dt
import json
import re
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("NPZ_REFRESH_ROOT", os.path.expanduser("~/binance-sandbox")))
if not (ROOT / "backtest_v8").exists():
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
IND = ROOT / "backtest_v8" / "indicators"
REPORT = ROOT / "data" / "reports" / "npz_freshness.json"
ALERT = ROOT / "data" / "alerts" / "npz_stale_live_lane.json"
SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]
PROBE = r"""
import glob, json, os, sys, time
import numpy as np
out = {}
for p in glob.glob(os.path.expanduser(sys.argv[1] + "/*.npz")):
    s = os.path.basename(p)[:-4]
    try:
        with np.load(p, allow_pickle=True) as z:
            if "timestamps" not in z.files:
                out[s] = {"err": "no timestamps"}
                continue
            ts = np.asarray(z["timestamps"]).astype(np.int64).reshape(-1)
            last = int(ts[-1])
            out[s] = {"rows": int(len(ts)), "last_ts": (last // 1000 if last > 1e11 else last),
                      "keys": int(len(z.files)), "mtime": int(os.path.getmtime(p))}
    except Exception as e:
        out[s] = {"err": type(e).__name__}
print(json.dumps(out))
"""


def log(msg):
    print(f"[npz_fresh_sync {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def is_crypto(s):
    return str(s).upper().endswith(("USDT", "USDC", "BUSD"))


def probe_local():
    py = str(ROOT / ".venv" / "bin" / "python")
    py = py if Path(py).exists() else sys.executable
    r = subprocess.run([py, "-c", PROBE, str(IND)], capture_output=True, text=True, timeout=900)
    return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {}


def probe_peer(host):
    cmd = SSH + [host, "cd ~/binance-sandbox && .venv/bin/python -c " + "'" + PROBE.replace("'", "'\"'\"'") + "'" + " ~/binance-sandbox/backtest_v8/indicators"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        log(f"{host} probe failed rc={r.returncode}: {(r.stderr or '')[-200:]}")
        return {}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        log(f"{host} probe unparsable: {type(e).__name__}")
        return {}


def bars_behind(entry, cut_crypto, cut_stock, symbol):
    if not entry or entry.get("err"):
        return None
    cut = cut_crypto if is_crypto(symbol) else cut_stock
    return int((int(cut) - int(entry["last_ts"])) // 900)


def klines_last(symbols):
    """{sym: last bar ts in its own 15m klines store} by tail-reading each file (never a full JSON parse).

    This is what separates "the refresh is late" from "the vendor/appender did not deliver". MEASURED
    2026-10-16 21:19Z: ~60 stock NPZs sat 95 bars behind the clock while their klines ENDED at
    2026-10-15T20:00Z (USO 30 h, TTD/T/STZ/TECK 25 h) and WMT/AAPL had 2026-10-16T20:00Z — the NPZ was exactly
    as fresh as its source, so alarming on it would point at the wrong layer."""
    out = {}
    for s in symbols:
        p = (ROOT / "klines_cache" / f"{s}_15m.json") if is_crypto(s) else (ROOT / "klines_cache" / "tradier" / f"{s}_15m.json")
        try:
            sz = p.stat().st_size
            with open(p, "rb") as f:
                f.seek(max(0, sz - 4096))
                tailtxt = f.read().decode("utf-8", "ignore")
        except OSError:
            out[s] = None
            continue
        m = re.findall(r'"timestamp":\s*"([0-9T:\-\.]+)Z?"', tailtxt)
        try:
            out[s] = int(dt.datetime.strptime(m[-1][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()) if m else None
        except ValueError:
            out[s] = None
    return out


def layer_split(local, kl, cut_crypto, cut_stock):
    """per symbol: is the NPZ behind its OWN klines (ours to fix) or is the klines store itself behind (upstream)?"""
    refresh_gap, upstream_gap = [], []
    for s, e in local.items():
        if not e or e.get("err") or kl.get(s) is None:
            continue
        npz_b = bars_behind(e, cut_crypto, cut_stock, s)
        k = int(kl[s])
        own = int((k - int(e["last_ts"])) // 900)
        clock = int(((cut_crypto if is_crypto(s) else cut_stock) - k) // 900)
        if own > 1:
            refresh_gap.append({"symbol": s, "npz_behind_klines_bars": own, "npz_behind_clock_bars": npz_b})
        elif clock > 2:
            upstream_gap.append({"symbol": s, "klines_behind_clock_bars": clock, "npz_behind_clock_bars": npz_b})
    refresh_gap.sort(key=lambda d: -d["npz_behind_klines_bars"])
    upstream_gap.sort(key=lambda d: -d["klines_behind_clock_bars"])
    return refresh_gap, upstream_gap


def dead_syms(symbols):
    """symbols whose own klines stopped arriving — their NPZ lag is permanent and is not a pipeline failure.

    Same convention v15_pipeline_health already uses for the merge-lag metric: BK's klines were 74 h old on
    2026-10-16, CALM's 448 h, HL's 1225 h. Averaging a 175-day "lag" into the health of the live set hides a
    real regression behind a dead ticker."""
    max_h = float(os.environ.get("NPZ_DEAD_KLINES_H", "168"))
    out = {}
    now = time.time()
    for s in symbols:
        p = (ROOT / "klines_cache" / f"{s}_15m.json") if is_crypto(s) else (ROOT / "klines_cache" / "tradier" / f"{s}_15m.json")
        try:
            # the LAST BAR, not the mtime: the appender rewrites a file every pass even when the vendor sent
            # nothing new, so mtime says "fresh" for a ticker that stopped trading (it caught only 2 of them).
            with open(p, "rb") as f:
                f.seek(max(0, p.stat().st_size - 4096))
                tailtxt = f.read().decode("utf-8", "ignore")
        except OSError:
            out[s] = None
            continue
        m = re.findall(r'"timestamp":\s*"([0-9T:\-\.]+)Z?"', tailtxt)
        if not m:
            out[s] = None
            continue
        try:
            last = dt.datetime.strptime(m[-1][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            out[s] = None
            continue
        age_h = (now - last) / 3600.0
        if age_h > max_h:
            out[s] = round(age_h, 1)
    return out


def summarize(d, cut_crypto, cut_stock, label, dead=None):
    dead = dead or {}
    out = {}
    for lab, pick in (("crypto", is_crypto), ("stocks", lambda s: not is_crypto(s))):
        v = [bars_behind(e, cut_crypto, cut_stock, s) for s, e in d.items() if pick(s) and s not in dead]
        v = [x for x in v if x is not None]
        if not v:
            continue
        out[lab] = {"n": len(v), "median_bars": statistics.median(v), "within_1": sum(1 for x in v if x <= 1),
                    "within_2": sum(1 for x in v if x <= 2), "over_8": sum(1 for x in v if x > 8), "worst_bars": max(v),
                    "unreadable": sum(1 for s, e in d.items() if pick(s) and (e or {}).get("err")),
                    "dead_excluded": sum(1 for s in d if pick(s) and s in dead)}
    log(f"{label}: " + " | ".join(f"{k} n={x['n']} median={x['median_bars']} within2={x['within_2']} >8bars={x['over_8']} dead={x['dead_excluded']}" for k, x in out.items()))
    return out


def drift(local, peer, cut_crypto, cut_stock):
    """symbols where the PEER copy is behind the local one (push candidates) or deeper than it (adoption candidates)."""
    push, adopt = [], []
    for s, le in local.items():
        pe = peer.get(s)
        if not pe or le.get("err") or pe.get("err"):
            continue
        lb, pb = bars_behind(le, cut_crypto, cut_stock, s), bars_behind(pe, cut_crypto, cut_stock, s)
        if int(pe["rows"]) > int(le["rows"]):
            adopt.append({"symbol": s, "local_rows": le["rows"], "peer_rows": pe["rows"], "local_bars": lb, "peer_bars": pb})
            continue
        if int(pe["rows"]) < int(le["rows"]) or int(pe["last_ts"]) < int(le["last_ts"]):
            push.append({"symbol": s, "local_rows": le["rows"], "peer_rows": pe["rows"], "local_bars": lb, "peer_bars": pb,
                         "gap_bars": (pb - lb) if (lb is not None and pb is not None) else None,
                         "peer_mtime_newer": int(pe.get("mtime") or 0) > int(le.get("mtime") or 0)})
    push.sort(key=lambda d: -(d["gap_bars"] or 0))
    adopt.sort(key=lambda d: -(d["peer_rows"] - d["local_rows"]))
    return push, adopt


def push_files(host, syms, frozen):
    """copy the local (better) copies over the peer's, mtime-independent; frozen boards are never touched."""
    sent, held = [], []
    batch = []
    for s in syms:
        if s in frozen:
            held.append(s)
            continue
        batch.append(str(IND / f"{s}.npz"))
    for i in range(0, len(batch), 40):
        chunk = batch[i: i + 40]
        r = subprocess.run(["rsync", "-az", "--timeout=120", "-e", " ".join(SSH)] + chunk + [f"{host}:~/binance-sandbox/backtest_v8/indicators/"], capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            log(f"{host} push chunk failed rc={r.returncode}: {(r.stderr or '')[-200:]}")
            continue
        sent += [Path(p).stem for p in chunk]
    return sent, held


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peers", default=os.environ.get("NPZ_FRESH_PEERS", ""))
    ap.add_argument("--push", action="store_true", help="correct peer copies that are behind/shorter than this host's")
    ap.add_argument("--max-bars", type=int, default=int(os.environ.get("NPZ_FRESH_MAX_BARS", "2")), help="live-lane alarm threshold in bars")
    a = ap.parse_args()
    now = time.time()
    cut_crypto = (int(now) // 900) * 900 - 900
    try:
        from tools.npz_cutoffs import last_closed_stock_bar
        cut_stock = last_closed_stock_bar(now)
    except Exception:
        cut_stock = cut_crypto
    local = probe_local()
    if not local:
        log("local probe produced nothing — refusing to report (fail-closed)")
        return 2
    # stocks are judged against the session frontier, never the wall clock: outside RTH no stock NPZ can hold the
    # bar the clock asks for, and measuring against it would report all 193 of them "stale" every single night.
    # The frontier (the newest bar any stock store on this host carries) makes the number mean "behind its peers",
    # and the wall-clock distance to that frontier is reported separately so a REAL session-wide stall is visible.
    frontier = max([int(e["last_ts"]) for s, e in local.items() if not is_crypto(s) and not (e or {}).get("err")] or [cut_stock])
    frontier_lag = int((int(cut_stock) - frontier) // 900)
    cut_stock = min(int(cut_stock), frontier)
    dead = dead_syms(sorted(local))
    rep = {"at": now, "host": os.uname().nodename, "cutoff_crypto": cut_crypto, "cutoff_stocks": cut_stock,
           "stock_frontier_ts": frontier, "stock_frontier_bars_behind_clock": frontier_lag,
           "dead_klines": dict(sorted(dead.items())),
           "local": summarize(local, cut_crypto, cut_stock, "local", dead)}
    try:
        from tools.npz_bar_refresh import priority_syms, frozen_syms
        pri, frozen = priority_syms(), frozen_syms()
    except Exception:
        pri, frozen = set(), set()
    kl = klines_last(sorted(local))
    refresh_gap, upstream_gap = layer_split(local, kl, cut_crypto, cut_stock)
    rep["refresh_gap"] = {"n": len(refresh_gap), "worst": refresh_gap[:25], "note": "NPZ behind its OWN klines — this pipeline's responsibility"}
    rep["upstream_gap"] = {"n": len(upstream_gap), "worst": upstream_gap[:25], "note": "klines store itself behind the clock — appender/vendor chain, the NPZ is as fresh as its source"}
    log(f"layers: {len(refresh_gap)} symbol(s) behind their own klines (ours), {len(upstream_gap)} with klines behind the clock (upstream)")
    late = sorted(({"symbol": s, "bars_behind": bars_behind(e, cut_crypto, cut_stock, s), "rows": e.get("rows")}
                   for s, e in local.items() if s in pri and (bars_behind(e, cut_crypto, cut_stock, s) or 0) > a.max_bars and s not in frozen and s not in dead),
                  key=lambda d: -(d["bars_behind"] or 0))
    rep["live_lane_late"] = late
    rep["frozen"] = sorted(frozen)
    for h in [p.strip() for p in a.peers.split(",") if p.strip()]:
        peer = probe_peer(h)
        if not peer:
            continue
        rep.setdefault("peers", {})[h] = {"summary": summarize(peer, cut_crypto, cut_stock, h, dead)}
        push, adopt = drift(local, peer, cut_crypto, cut_stock)
        rep["peers"][h].update({"behind_on_peer": push[:40], "n_behind_on_peer": len(push), "deeper_on_peer": adopt[:20], "n_deeper_on_peer": len(adopt)})
        log(f"{h}: {len(push)} symbol(s) staler/shorter on the peer than here, {len(adopt)} deeper on the peer (adoption is npz_repair's job)")
        if a.push and push:
            sent, held = push_files(h, [d["symbol"] for d in push], frozen)
            rep["peers"][h].update({"pushed": len(sent), "held_frozen": held})
            log(f"{h}: pushed {len(sent)} corrected copies, held {len(held)} frozen board symbol(s)")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    if late:
        ALERT.parent.mkdir(parents=True, exist_ok=True)
        ALERT.write_text(json.dumps({"at": now, "max_bars": a.max_bars, "late": late}, indent=1, sort_keys=True, default=str))
        log(f"ALERT {len(late)} live-lane symbol(s) more than {a.max_bars} bars behind: " + ",".join(d["symbol"] for d in late[:10]))
    else:
        ALERT.unlink(missing_ok=True)
    log(f"report -> {REPORT}")
    return 1 if late else 0


if __name__ == "__main__":
    sys.exit(main())
