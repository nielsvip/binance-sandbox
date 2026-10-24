#!/usr/bin/env python3
"""npz_freshness_tripwire — ALERT-ONLY staleness tripwire for the two NPZ sources.

OPERATOR 2026-10-24 (2-minute rule): follow whichever source reports first, intervene
immediately if any source lags >2min behind kline arrival. This script checks BOTH
sources against the 120s threshold and reports; it changes nothing, restarts nothing,
places nothing, and never edits a pipeline.

Sources checked:
  s1_npz   prebuilt backtest_v8/indicators/*.npz on S1 (one ssh stat call; mtime lag
           vs latest closed 15m bar, plus a 5-symbol tail spot-check over ssh).
  mac_klines  the Mac compute path's own input (klines_cache/<SYM>_15m.json last-bar
           close vs latest closed 15m bar, all tradeable crypto syms, data-domain).

Sinks: stdout (cron log) + data/pure_board/npz_freshness_alerts.jsonl (one summary
row plus one row per breach per run). Exit code is always 0; an alert is a row, not
a failure. A source that cannot be reached reports status UNKNOWN (never STALE —
a failed check must not masquerade as a measured lag).

CRON (NOT INSTALLED — install by hand, see task report):
  */5 * * * * /usr/bin/python3 /Users/niels/Documents/binance/tools/npz_freshness_tripwire.py >> /Users/niels/logs/npz_freshness_tripwire.log 2>&1  # NPZ_FRESHNESS_TRIPWIRE
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAR_S = 900.0
FRESH_S = 120.0
OUT = ROOT / "data" / "pure_board" / "npz_freshness_alerts.jsonl"
S1_HOST = "s1-int"
S1_NPZ_DIR = "/home/niels/binance-sandbox/backtest_v8/indicators"
TAIL_SAMPLE = 5


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}Z] {msg}", flush=True)


def tradeable_crypto_syms():
    keys = [k for k in json.load(open(ROOT / "tradeable_keys.json")) if ":" in k]
    syms = set()
    for k in keys:
        ss = k.split(":", 1)[1]
        sym = ss.rsplit("_", 1)[0]
        if sym.endswith(("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")):
            syms.add(sym)
    return sorted(syms)


def s1_mtime_lags(syms):
    """{sym: mtime} via ONE ssh call; raises on any transport failure (UNKNOWN, not stale)."""
    want = " ".join(f"{S1_NPZ_DIR}/{s}.npz" for s in syms)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", S1_HOST,
                        f"stat -c '%Y %n' {want}"],
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"s1 stat failed rc={r.returncode} err={(r.stderr or '')[:160]}")
    out = {}
    for line in r.stdout.splitlines():
        mt, path = line.split(None, 1)
        out[Path(path).stem] = float(mt)
    return out


def s1_tail_sample(syms, close_ts):
    """Data-domain spot check: tail 15m bar of a few S1 NPZs via one remote python."""
    sample = syms[:TAIL_SAMPLE]
    lst = " ".join(f"{S1_NPZ_DIR}/{s}.npz" for s in sample)
    py = ("import numpy as np,glob;"
          f"[(print(f.split('/')[-1][:-4], float(np.asarray(np.load(f,allow_pickle=True)['timestamps'],dtype=float)[-1])))"
          f" for f in '{lst}'.split()]")
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", S1_HOST,
                            f"python3 -c \"{py}\""],
                           capture_output=True, text=True, timeout=90)
        tails = {}
        for line in (r.stdout or "").splitlines():
            s, t = line.split()
            tails[s] = float(t)
        return {s: close_ts - tails[s] for s in sample if s in tails}
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}"}


def mac_kline_tail(sym):
    """Last closed 15m bar close-time in the Mac compute path's own input file."""
    p = ROOT / "klines_cache" / f"{sym}_15m.json"
    d = json.load(open(p))
    rows = d if isinstance(d, list) else d.get("klines") or d.get("bars") or []
    last = rows[-1]
    if isinstance(last, dict):
        for k in ("close_time", "close_ts", "t", "ts", "time", "open_time"):
            if k in last:
                return float(last[k]) / 1000.0 if float(last[k]) > 1e12 else float(last[k])
        for k in ("timestamp", "open_ts", "datetime"):
            if k in last:
                v = last[k]
                if isinstance(v, (int, float)):
                    return float(v) / 1000.0 if float(v) > 1e12 else float(v)
                return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        raise ValueError("no ts key")
    return float(last[0]) / 1000.0 if float(last[0]) > 1e12 else float(last[0])


def main() -> None:
    now = time.time()
    close_ts = (now // BAR_S) * BAR_S - BAR_S
    syms = tradeable_crypto_syms()
    rows = []
    breaches = []
    try:
        mts = s1_mtime_lags(syms)
        for s in syms:
            if s not in mts:
                rows.append({"sym": s, "source": "s1_npz", "status": "MISSING"})
                breaches.append((s, "s1_npz", None))
                continue
            lag = close_ts - mts[s]
            ok = lag <= FRESH_S
            rows.append({"sym": s, "source": "s1_npz", "status": "FRESH" if ok else "STALE",
                         "lag_s": round(lag, 1)})
            if not ok:
                breaches.append((s, "s1_npz", round(lag, 1)))
        tails = s1_tail_sample(syms, close_ts)
        if "_error" in tails or not tails:
            tail_status = "UNKNOWN"
        else:
            tail_status = "FRESH" if all(v <= FRESH_S for v in tails.values()) else "STALE"
        rows.append({"source": "s1_npz_tail_sample", "status": tail_status, "tails": tails})
    except Exception as exc:
        rows.append({"source": "s1_npz", "status": "UNKNOWN", "err": f"{type(exc).__name__}: {exc}"[:200]})
        log(f"ALERT s1_npz source UNKNOWN ({exc})".rstrip()[:200])
    mac_missing, mac_stale = 0, 0
    for s in syms:
        try:
            lag = close_ts - mac_kline_tail(s)
        except Exception:
            mac_missing += 1
            rows.append({"sym": s, "source": "mac_klines", "status": "UNKNOWN"})
            continue
        ok = lag <= FRESH_S
        if not ok:
            mac_stale += 1
            breaches.append((s, "mac_klines", round(lag, 1)))
            rows.append({"sym": s, "source": "mac_klines", "status": "STALE", "lag_s": round(lag, 1)})
    s1_stale = sum(1 for _, src, _ in breaches if src == "s1_npz")
    log(f"tripwire close={close_ts:.0f} syms={len(syms)} s1_npz_stale={s1_stale} "
        f"mac_klines_stale={mac_stale} mac_klines_unknown={mac_missing}")
    for s, src, lag in breaches[:25]:
        log(f"ALERT {src} {s} {lag}s behind kline arrival (>120s)")
    if len(breaches) > 25:
        log(f"ALERT ... and {len(breaches) - 25} more (see jsonl)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    run_ts = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(OUT, "a") as fh:
        fh.write(json.dumps({"ts": run_ts, "close_ts": close_ts, "kind": "summary",
                             "syms": len(syms), "s1_npz_stale": s1_stale,
                             "mac_klines_stale": mac_stale,
                             "mac_klines_unknown": mac_missing}) + "\n")
        for s, src, lag in breaches:
            fh.write(json.dumps({"ts": run_ts, "close_ts": close_ts, "kind": "breach",
                                 "sym": s, "source": src, "lag_s": lag,
                                 "threshold_s": FRESH_S}) + "\n")


if __name__ == "__main__":
    main()
