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
import fcntl
import hashlib
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
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


def source_revision(base, symbol, mode, close_ts):
    from closed_bar_feed import is_verified_closed_native, timestamp_epoch
    path = Path(base) / 'data' / 'closed_15m_bars' / mode / f'{symbol}.json'
    try:
        for row in reversed(json.loads(path.read_text())):
            if timestamp_epoch(row['timestamp']) == int(close_ts):
                values = {key: row.get(key) for key in ('open', 'high', 'low', 'close', 'volume')}
                values['verified_native'] = is_verified_closed_native(row, int(close_ts) + 900)
                return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def source_marker(npz_path):
    path = Path(npz_path)
    return path.parent / '.tail_source' / f'{path.stem}.json'


def source_changed(npz_path, close_ts, revision):
    if not revision:
        return False
    try:
        previous = json.loads(source_marker(npz_path).read_text())
        return previous != {'bar_ts': int(close_ts), 'source_sha256': revision}
    except (OSError, ValueError):
        return True


def sweep_symbol(
    npz_path, symbol, last_close_ts, compute_fn, now_s=None, on_append=None, revision=None
):
    """One symbol: append exactly one owed row. Returns appended|current|refused|error."""
    try:
        tip = tip_of(npz_path)
    except Exception:
        return "error"
    correction = tip is not None and int(last_close_ts) == int(tip) and source_changed(npz_path, last_close_ts, revision)
    if tip is not None and int(last_close_ts) <= int(tip) and not correction:
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
        ok = append_tail_row(npz_path, final, now=now_s, replace_latest=correction)
    except Exception:
        return "error"
    if ok and revision:
        from closed_bar_feed import atomic_json
        atomic_json(source_marker(npz_path), {'bar_ts': int(last_close_ts), 'source_sha256': revision})
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
        from closed_bar_feed import is_verified_closed_native, timestamp_epoch
        now_epoch = time.time() if now_s is None else float(now_s)
        current_start = int(now_epoch // 900) * 900 - 900
        overlay = Path(base) / 'data' / 'closed_15m_bars' / mode / f'{symbol}.json'
        overlay_rows = json.loads(overlay.read_text()) if overlay.is_file() else []
        verified_current = any(timestamp_epoch(row['timestamp']) == current_start and is_verified_closed_native(row, current_start + 900) for row in overlay_rows)
        def eligible_start(stamp):
            if stamp + 900 > now_epoch:
                return False
            # 2026-10-27 USER design: the mark-price bar is published at the close and S1 calculates the NPZ bar AS SOON AS IT
            # RECEIVES it; the OHLCV kline that replaces it later is applied as a correction (source_changed -> replace_latest).
            # The 30 s hold for a verified native row here delayed every crypto NPZ bar by 30 s.
            if mode == 'tradier':
                from tools.npz_cutoffs import is_rth_bar_start
                return is_rth_bar_start(stamp)
            return True
        overlay_tip = None
        if overlay_rows:
            now = time.time() if now_s is None else float(now_s)
            eligible = [timestamp_epoch(row['timestamp']) for row in overlay_rows if eligible_start(timestamp_epoch(row['timestamp']))]
            overlay_tip = max(eligible) if eligible else None
            if overlay_tip is not None and overlay_tip >= int(now // 900) * 900 - 900:
                return overlay_tip
        f = _resolve_kline_path(symbol, "15m", mode, Path(base))
        if f is None:
            return overlay_tip
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
                if not pd.isna(ts) and ts <= cutoff and eligible_start(int(ts.timestamp())):
                    return max(int(ts.timestamp()), overlay_tip or 0)
            return overlay_tip
        df = _load_klines_df(f)
        if df is None or df.empty:
            return overlay_tip
        df = _drop_forming_bar(df, f.stem.rsplit("_", 1)[-1])
        if mode == 'tradier':
            df = df.loc[[eligible_start(int(stamp.timestamp())) for stamp in df.index]]
        if df is None or df.empty:
            return overlay_tip
        return max(int(df.index[-1].to_pydatetime().timestamp()), overlay_tip or 0)
    except Exception:
        return None


def make_compute(base, mode):
    import types

    try:
        from npz_live_generator import _compute_row_for_15m_close
    except Exception:
        return None
    try:
        if mode == "tradier":
            from tradier_indicators import IndicatorCalculator as _Calc
        else:
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


def held_first_symbols(base, owned, cat_side, now=None):
    if not cat_side.startswith("crypto"):
        return owned
    try:
        manifest = json.loads((Path(base) / "data" / "vec_live" / "held_sides.json").read_text())
        when = time.time() if now is None else now
        if when - float(manifest.get("generated_ts") or 0) > 180:
            return owned
        held = {str(ss).rsplit("_", 1)[0] for ss in manifest.get("held", []) if str(ss).endswith(("_LONG", "_SHORT"))}
    except (OSError, ValueError, TypeError):
        return owned
    def symbol(entry):
        return entry.rsplit("_", 1)[0] if entry.endswith(("_LONG", "_SHORT")) else entry
    return sorted(owned, key=lambda entry: (symbol(entry) not in held, entry))


def sweep_once(base, cat_side, workers=WORKERS, sibling=None, sibling_base=None):
    uni = load_universe(base)
    owned = held_first_symbols(base, owned_symbols(uni, cat_side), cat_side)
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
        return npz, sym, lc, source_revision(base, sym, mode, lc)

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
    for npz, sym, lc, revision in jobs:
        try:
            tip = tip_of(npz)
            changed = tip is not None and int(lc) == int(tip) and source_changed(npz, lc, revision)
            if (tip is None or int(lc) > int(tip) or changed) and is_closed_bar(int(lc), now):
                ready.append((npz, sym, lc, revision))
            else:
                rec["current"] += 1
        except Exception:
            rec["error"] += 1
            rec["error_syms"].append(sym)
    tasks = [(sym, int(lc), str(base), mode) for _, sym, lc, _ in ready]
    # 2026-10-27: import the compute modules in the PARENT so every forked worker inherits them. Measured on S1: each worker
    # paid 2.5 s (npz_live_generator 1.0 s + ez_indicators 1.5 s) before its first row, on every sweep.
    if tasks:
        try:
            import npz_live_generator  # noqa: F401
            import tradier_indicators  # noqa: F401
            if mode != "tradier":
                import ez_indicators  # noqa: F401
        except Exception:
            pass
    with ProcessPoolExecutor(max_workers=min(max(1, workers), 12), mp_context=get_context("fork")) as pool:
        futures = {pool.submit(_compute_row_process, task): job for task, job in zip(tasks, ready)}
        for future in as_completed(futures):
            npz, sym, lc, revision = futures[future]
            row = future.result()
            if not row:
                rec["error"] += 1
                rec["error_syms"].append(sym)
                continue
            if source_revision(base, sym, mode, lc) != revision:
                rec['error'] += 1
                rec['error_syms'].append(sym)
                continue
            try:
                r = sweep_symbol(
                    npz,
                    sym,
                    lc,
                    lambda _symbol, _close_ts, result=row: result,
                    now_s=now,
                    on_append=on_append if sibling else None,
                    revision=revision,
                )
            except Exception:
                r = "error"
            if r == "error":
                rec["error_syms"].append(sym)
            rec[r if r in rec else "error"] += 1
    return rec


def _vd_held_symbols(cat_side):
    """Held symbols from the server trader's broker read (~/binance/data/vector_direct/held_<venue>.json)."""
    venue = "crypto" if cat_side.startswith("crypto") else "stocks"
    try:
        doc = json.loads((Path.home() / "binance" / "data" / "vector_direct" / f"held_{venue}.json").read_text())
        return {str(ss).rsplit("_", 1)[0].upper() for ss in doc.get("sides") or []}
    except (OSError, ValueError, TypeError):
        return set()


def stream_loop(base, cat_side, workers=WORKERS, sibling=None, sibling_base=None, poll_s=1.0, once=False):
    """2026-10-28 (USER "within seconds of 15m bar completion and kline arrival"): the same per-symbol append as sweep_once,
    without its batch barrier. sweep_once computed every ready symbol of a pass and waited for ALL of them before looking
    again, so a bar arriving at +3 s waited for the +1 s pass to finish (+17 s) and the next pass (+43 s); measured on S1
    10-28 03:45Z: passes at +2.0 (36 rows) and +17.5 (67 rows), last append +42.8 s. Here one persistent fork pool takes
    each symbol the moment its closed bar is visible (held symbols first); a row whose source changed while it computed
    (Mac mark bar -> native kline) is resubmitted at once instead of waiting a whole pass."""
    from concurrent.futures import FIRST_COMPLETED, wait
    mode = "crypto" if cat_side.startswith("crypto") else "tradier"
    import npz_live_generator  # noqa: F401  (imported before the fork so every worker inherits it)
    # 2026-10-28: ez_indicators.compute imports tradier_indicators lazily (WaveTrend divergence); 1.2 s per fresh worker.
    import tradier_indicators  # noqa: F401
    if mode != "tradier":
        import ez_indicators  # noqa: F401
    ctx = get_context("fork")
    pool = ProcessPoolExecutor(max_workers=max(1, workers), mp_context=ctx)
    inflight = {}
    tasks_done = 0
    last_print = 0.0
    rec = None

    def on_append(tail_abspath):
        if sibling:
            try:
                if push_to_sibling(base, tail_abspath, sibling, sibling_base or base):
                    rec["mirrored"] += 1
            except Exception:
                pass

    while True:
        now = time.time()
        rec = {"t": now, "cat_side": cat_side, "stream": True, "symbols": 0, "submitted": 0, "inflight": 0, "appended": 0, "current": 0,
               "refused": 0, "error": 0, "resubmit_source_changed": 0, "compacted": 0, "mirrored": 0, "error_syms": [], "appended_syms": []}
        owned = owned_symbols(load_universe(base), cat_side)
        held = _vd_held_symbols(cat_side)

        def symbol_of(entry):
            return entry.rsplit("_", 1)[0] if entry.endswith(("_LONG", "_SHORT")) else entry
        owned = sorted({symbol_of(e) for e in owned}, key=lambda s: (s not in held, s))

        def probe(sym):
            npz = npz_for(base, sym)
            if npz is None:
                return None
            lc = kline_last_close(base, sym, mode)
            if lc is None:
                return None
            return npz, sym, lc, source_revision(base, sym, mode, lc)

        with ThreadPoolExecutor(max_workers=8) as tp:
            jobs = [j for j in tp.map(probe, [s for s in owned if s not in inflight]) if j is not None]
        rec["symbols"] = len(jobs) + len(inflight)
        for npz, sym, lc, revision in jobs:
            try:
                tip = tip_of(npz)
                changed = tip is not None and int(lc) == int(tip) and source_changed(npz, lc, revision)
                if (tip is None or int(lc) > int(tip) or changed) and is_closed_bar(int(lc), now):
                    fut = pool.submit(_compute_row_process, (sym, int(lc), str(base), mode))
                    inflight[sym] = (fut, npz, lc, revision)
                    rec["submitted"] += 1
                else:
                    rec["current"] += 1
            except Exception:
                rec["error"] += 1
                rec["error_syms"].append(sym)
        done = [s for s, v in inflight.items() if v[0].done()]
        if not done and inflight:
            wait([v[0] for v in inflight.values()], timeout=poll_s, return_when=FIRST_COMPLETED)
            done = [s for s, v in inflight.items() if v[0].done()]
        broken = False
        for sym in done:
            fut, npz, lc, revision = inflight.pop(sym)
            tasks_done += 1
            try:
                row = fut.result()
            except Exception:
                row, broken = None, True
            if not row:
                rec["error"] += 1
                rec["error_syms"].append(sym)
                continue
            if source_revision(base, sym, mode, lc) != revision:
                rec["resubmit_source_changed"] += 1  # next loop sees the new revision and computes it again at once
                continue
            try:
                r = sweep_symbol(npz, sym, lc, lambda _s, _c, result=row: result, now_s=time.time(), on_append=on_append if sibling else None, revision=revision)
            except Exception:
                r = "error"
            if r == "error":
                rec["error_syms"].append(sym)
            if r == "appended":
                rec["appended_syms"].append(sym)
            rec[r if r in rec else "error"] += 1
        rec["inflight"] = len(inflight)
        if broken or (not inflight and tasks_done >= 5000):
            # a worker died (OOM) or the pool served many rows: start clean workers
            pool.shutdown(wait=False, cancel_futures=True)
            inflight.clear()
            pool = ProcessPoolExecutor(max_workers=max(1, workers), mp_context=ctx)
            tasks_done = 0
        if rec["appended"] or rec["error"] or rec["submitted"] or rec["resubmit_source_changed"] or now - last_print >= 30:
            last_print = now
            print(json.dumps(rec, sort_keys=True), flush=True)
        if once and not inflight:
            return rec
        if not inflight:
            time.sleep(min(poll_s, 0.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", action="store_true", help="no batch barrier: compute each symbol as soon as its bar is visible")
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
    lock_path = Path(base) / "data" / f".npz_tail_service_{a.cat_side}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    singleton = lock_path.open("a")
    try:
        fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    if not a.once:
        import subprocess
        log_path = Path(base) / 'logs' / f'npz_tail_compactor_{a.cat_side}.jsonl'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open('a') as stream:
            subprocess.Popen([sys.executable, str(Path(__file__).with_name('npz_tail_compactor.py')), '--base', str(base), '--cat-side', a.cat_side, '--loop'], cwd=base, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    if a.stream:
        return 0 if stream_loop(base, a.cat_side, workers=a.workers, sibling=a.sibling or None, sibling_base=a.sibling_base or None, once=a.once) is not None else 0
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
