#!/usr/bin/env python3
"""npz_tail_service — each server owns its cat_side's NPZ rows (USER 2026-10-25).

S1 = crypto SHORT, S2 = crypto LONG, S5 = stock LONG, S6 = stock SHORT.
Every SWEEP_S the service checks each owned symbol: kline tail's last CLOSED
15m bar vs the merged store tip (zip + sidecar tail). Behind -> compute exactly
ONE row with a fresh calculator and tail-append it (milliseconds, zip never
rewritten on the hot path). A tail reaching COMPACT_AT rows folds into the zip
in one rollup so backtest readers (which read the zip) stay current.

No live file is touched. No full rebuilds. Forming bars refused. Dup/stale
idempotent. The wedged in-daemon zip-rewrite updater is bypassed, not edited.
"""

from __future__ import annotations
import argparse
import glob
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from npz_tail_store import (  # noqa: E402
    append_tail_row,
    compact_tail,
    is_closed_bar,
    tail_len,
    tail_rows,
)

logger = logging.getLogger("npz_tail_service")

STABLE = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
CAT_SIDES = (
    "crypto_short",
    "crypto_long",
    "stock_long",
    "stock_short",
    "crypto",
    "stocks",
)
SWEEP_S = 30.0
WORKERS = 8
COMPACT_AT = 4


def is_crypto_symbol(sym):
    return str(sym).upper().endswith(STABLE)


def owned_symbols(universe, cat_side):
    # Rows are side-agnostic: S1 computes ALL crypto rows for S2, S5 ALL stock rows for S6.
    if cat_side in ("crypto", "stocks"):
        want_crypto = cat_side == "crypto"
        return sorted(
            {
                s.rsplit("_", 1)[0]
                for s in universe
                if "_" in s and is_crypto_symbol(s.rsplit("_", 1)[0]) == want_crypto
            }
        )
    side = cat_side.rsplit("_", 1)[-1].upper()
    want_crypto = cat_side.startswith("crypto")
    out = []
    for ss in universe:
        if "_" not in ss:
            continue
        sym, sside = ss.rsplit("_", 1)
        if sside.upper() != side:
            continue
        if is_crypto_symbol(sym) != want_crypto:
            continue
        out.append(ss)
    return sorted(out)


def tip_of(npz_path):
    rows = tail_rows(npz_path)
    if rows:
        try:
            return max(int(r["timestamps"]) for r in rows)
        except Exception:
            pass
    try:
        import numpy as _np

        with _np.load(str(npz_path), allow_pickle=True) as z:
            ts = _np.asarray(z["timestamps"]).astype("int64").reshape(-1)
            return int(ts[-1]) if len(ts) else None
    except Exception:
        return None


def push_to_sibling(base, tail_abspath, sibling, sibling_base):
    """Best-effort sub-second mirror of one tail file. Never blocks the hot path."""
    try:
        import subprocess

        rel = os.path.relpath(str(tail_abspath), str(base))
        if rel.startswith(".."):
            return False
        dest = f"{sibling}:{sibling_base.rstrip('/')}/{rel}"
        subprocess.Popen(
            [
                "scp",
                "-q",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                str(tail_abspath),
                dest,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def sweep_symbol(
    npz_path, symbol, last_close_ts, compute_fn, now_s=None, on_append=None
):
    """One symbol: append exactly one owed row. Returns appended|current|refused|error."""
    try:
        tip = tip_of(npz_path)
    except Exception:
        return "error"
    if tip is not None and int(last_close_ts) <= int(tip):
        return "current"
    if not is_closed_bar(int(last_close_ts), now_s):
        return "refused"
    try:
        row = compute_fn(symbol, int(last_close_ts))
    except Exception:
        return "error"
    if not row:
        return "error"
    final = dict(row, timestamps=int(last_close_ts))
    # Same parent-label rule as _append_row_to_npz: base-TF availability ts = the new bar itself.
    for _tf in ("15m", "5m", "3m", "1m"):
        final[f"timestamp_{_tf}"] = int(last_close_ts)
    try:
        ok = append_tail_row(npz_path, final)
    except Exception:
        return "error"
    if ok and on_append is not None:
        try:
            from npz_tail_store import tail_path_for_npz

            on_append(str(tail_path_for_npz(npz_path)))
        except Exception:
            pass
    return "appended" if ok else "refused"


def load_universe(base):
    base = Path(base)
    uni = []
    try:
        tks = json.loads((base / "tradeable_keys.json").read_text())
        if isinstance(tks, list):
            for k in tks:
                s = str(k).split(":", 1)[-1]
                if "_" in s:
                    uni.append(s)
    except Exception:
        pass
    for f in ("symbols_trb_long.json", "symbols_trb_short.json"):
        side = "LONG" if "long" in f else "SHORT"
        try:
            for s in json.loads((base / f).read_text()):
                s = str(s).strip().upper()
                if s:
                    uni.append(f"{s}_{side}")
        except Exception:
            pass
    if not any(not is_crypto_symbol(s.rsplit("_", 1)[0]) for s in uni if "_" in s):
        # symbols_trb_* erased/empty (tradier_rankings incident): fall back to stock NPZs on disk.
        # Skip dotfiles and orphan *.TMP/BARREFRESH debris (never real symbols).
        try:
            for p in (base / "backtest_v8" / "indicators").glob("*.npz"):
                stem = p.name[:-4]
                if (
                    not stem
                    or stem.startswith(".")
                    or ".TMP" in stem.upper()
                    or "BARREFRESH" in stem.upper()
                ):
                    continue
                sym = stem.upper()
                if not sym.replace(".", "").replace("-", "").isalnum():
                    continue
                if sym and not is_crypto_symbol(sym):
                    uni.append(f"{sym}_LONG")
                    uni.append(f"{sym}_SHORT")
        except Exception:
            pass
    return sorted(set(uni))


def kline_last_close(base, symbol, mode, now_s=None):
    """Last closed 15m bar ts from local kline files, or None."""
    try:
        from npz_live_generator import (
            _drop_forming_bar,
            _load_klines_df,
            _resolve_kline_path,
        )
    except Exception:
        return None
    try:
        f = _resolve_kline_path(symbol, "15m", mode, Path(base))
        if f is None:
            return None
        # These cache files contain long histories; the 1s watcher needs only
        # their final timestamps, not a full pandas parse on every pass.
        with open(f, "rb") as stream:
            stream.seek(max(0, f.stat().st_size - 131072))
            tail = stream.read().decode("utf-8", errors="ignore")
        matches = re.findall(r'"(?:timestamp|timestamp_dt)"\s*:\s*"([^"\\]+)', tail)
        if matches:
            import pandas as pd

            now = pd.Timestamp.now(tz="UTC") if now_s is None else pd.Timestamp(now_s, unit="s", tz="UTC")
            cutoff = now - pd.Timedelta(seconds=900)
            for value in reversed(matches):
                ts = pd.to_datetime(value, utc=True, errors="coerce")
                if not pd.isna(ts) and ts <= cutoff:
                    return int(ts.timestamp())
        df = _load_klines_df(f)
        if df is None or df.empty:
            return None
        df = _drop_forming_bar(df, f.stem.rsplit("_", 1)[-1])
        if df is None or df.empty:
            return None
        return int(df.index[-1].to_pydatetime().timestamp())
    except Exception:
        return None


def make_compute(base, mode):
    import types

    try:
        from npz_live_generator import _compute_row_for_15m_close
    except Exception:
        return None
    try:
        from ez_indicators import IndicatorCalculator as _Calc

        _calc_cls = _Calc
    except Exception:
        _calc_cls = None
    if _calc_cls is None:
        return None
    mgr = types.SimpleNamespace(base_path=Path(base))

    def compute(symbol, close_ts):
        from datetime import datetime, timezone

        calc = _calc_cls()
        return _compute_row_for_15m_close(
            symbol, datetime.fromtimestamp(close_ts, tz=timezone.utc), mgr, calc, mode
        )

    return compute


def _compute_row_process(task):
    symbol, close_ts, base, mode = task
    from datetime import datetime, timezone
    import types

    try:
        from npz_live_generator import _compute_row_for_15m_close

        if mode == "tradier":
            from tradier_indicators import IndicatorCalculator
        else:
            from ez_indicators import IndicatorCalculator
        calculator = IndicatorCalculator()
        manager = types.SimpleNamespace(base_path=Path(base))
        return _compute_row_for_15m_close(
            symbol,
            datetime.fromtimestamp(close_ts, tz=timezone.utc),
            manager,
            calculator,
            mode,
        )
    except Exception:
        return None


def npz_for(base, symbol):
    p = Path(base) / "backtest_v8" / "indicators" / f"{symbol}.npz"
    return str(p) if p.exists() else None


def sweep_once(base, cat_side, workers=WORKERS, sibling=None, sibling_base=None):
    uni = load_universe(base)
    owned = owned_symbols(uni, cat_side)
    mode = "crypto" if cat_side.startswith("crypto") else "tradier"
    compute = make_compute(base, mode)
    if compute is None:
        return {"error": "no-compute"}
    now = time.time()
    def probe(entry):
        if entry.endswith(("_LONG", "_SHORT")):
            sym = entry.rsplit("_", 1)[0]
        else:
            sym = entry
        npz = npz_for(base, sym)
        if npz is None:
            return None
        lc = kline_last_close(base, sym, mode)
        if lc is None:
            return None
        return npz, sym, lc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        jobs = [job for job in pool.map(probe, owned) if job is not None]
    rec = {
        "t": now,
        "cat_side": cat_side,
        "symbols": len(jobs),
        "appended": 0,
        "current": 0,
        "refused": 0,
        "error": 0,
        "compacted": 0,
        "mirrored": 0,
        "error_syms": [],
    }
    if not jobs:
        return rec

    def on_append(tail_abspath):
        if sibling:
            try:
                if push_to_sibling(base, tail_abspath, sibling, sibling_base or base):
                    rec["mirrored"] += 1
            except Exception:
                pass

    ready = []
    for npz, sym, lc in jobs:
        try:
            if (tip_of(npz) is None or int(lc) > int(tip_of(npz))) and is_closed_bar(int(lc), now):
                ready.append((npz, sym, lc))
            else:
                rec["current"] += 1
        except Exception:
            rec["error"] += 1
            rec["error_syms"].append(sym)
    tasks = [(sym, int(lc), str(base), mode) for _, sym, lc in ready]
    with ProcessPoolExecutor(max_workers=min(max(1, workers), 8), mp_context=get_context("fork")) as pool:
        rows = pool.map(_compute_row_process, tasks)
        for (npz, sym, lc), row in zip(ready, rows):
            if not row:
                rec["error"] += 1
                rec["error_syms"].append(sym)
                continue
            try:
                r = sweep_symbol(
                    npz,
                    sym,
                    lc,
                    lambda _symbol, _close_ts, result=row: result,
                    now_s=now,
                    on_append=on_append if sibling else None,
                )
            except Exception:
                r = "error"
            if r == "error":
                rec["error_syms"].append(sym)
            rec[r if r in rec else "error"] += 1
            if r == "appended" and tail_len(npz) >= COMPACT_AT:
                try:
                    rec["compacted"] += compact_tail(npz) or 0
                except Exception:
                    pass
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat-side", default=os.environ.get("CAT_SIDE", ""))
    ap.add_argument("--base", default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--sweep-s", type=float, default=SWEEP_S)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--sibling", default=os.environ.get("TAIL_SIBLING", ""))
    ap.add_argument("--sibling-base", default=os.environ.get("TAIL_SIBLING_BASE", ""))
    a = ap.parse_args()
    if a.cat_side not in CAT_SIDES:
        print(
            json.dumps(
                {"error": f"bad --cat-side {a.cat_side!r}, want one of {CAT_SIDES}"}
            )
        )
        return 2
    try:
        from config import Config as _Cfg

        base = a.base or _Cfg.BASE_PATH
    except Exception:
        base = a.base or str(ROOT)
    while True:
        try:
            rec = sweep_once(
                base,
                a.cat_side,
                workers=a.workers,
                sibling=a.sibling or None,
                sibling_base=a.sibling_base or None,
            )
        except Exception as exc:
            rec = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(rec, sort_keys=True), flush=True)
        if a.once:
            return 0
        time.sleep(a.sweep_s)


if __name__ == "__main__":
    sys.exit(main())
