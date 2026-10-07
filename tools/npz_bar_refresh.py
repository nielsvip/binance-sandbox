#!/usr/bin/env python3
"""npz_bar_refresh — append the just-closed 15m bar(s) to every live NPZ, crypto AND stocks (runs ON s1, X2 of the VEC-DRIVEN LIVE architecture).

v2 (2026-10-06, USER "latest 15m bar ALWAYS in results, both venues, 24/7"): the v1 crypto core below is UNCHANGED (same Builder harness,
same splice/validate/guard/save bytes); v2 adds a Tradier leg for stocks, same-cycle sentiment, F&G history, and a --loop daemon + 24/7
wiring (systemd unit binance-npz-refresh.service + @reboot + */5 watchdog cron; flock singleton so they never double-run).

Per symbol (only when the NPZ's last bar is older than the last CLOSED 15m bar):
  1. existing NPZ L (crypto must carry htf_align=causal_v3; a legacy store is refused, the engine would shift appended causal rows a second time).
  2. input frames = the builder's own source union (backtest_v8_precompute.compute_symbol -> load_klines over its sources) with FRESH
     closed klines overlaid on the primary 15m source, unclosed rows dropped. Crypto: Binance USD-M futures (fapi /fapi/v1/klines, 15m
     only — no HTF REST, s1 shares its IP with the ez stack). Stocks: Tradier 15min time&sales (04:00-19:45 ET grid, STKT filter), one
     call per symbol per cycle, prefetched in the parent with ONE shared client (sem 2, paced; workers never touch the API).
  3. capture pass: compute_symbol runs on those frames and is stopped at its first compute_tf_arrays call -> the builder's own
     final per-TF frames (15m + its 1h/4h/D/W/M selection, causal W/M frame / D-derived W/M for tradier) — no selection logic is
     re-implemented. Tradier HTFs resample from the fresh 15m (builder's own code), so the EOD close flows into D/W/M tails.
  4. tail pass: compute_symbol runs again with the 15m frame cut to the trailing gap+--w15 bars (--w15 0 = full-length, proven exact)
     and every HTF frame = the captured full frame; stopped right before its save (all indicator passes done) -> builder arrays for
     the trailing rows. Indicator math is 100 % the builder's (compute_tf_arrays, causal _broadcast_asof_indices, cross-TF passes,
     funding/OI/F&G injection).
  5. post-processors exactly like the live pipeline: tools/npz_fundoi (asof/lagpct on data/fundoi_stage) for stores that carry
     cov_funding, tools/v15_npz_augment add_* functions + classic_formations on the spliced arrays (each fail-open wrapped; success
     path byte-identical), dc_width_*_prev roll.
  6. APPEND ONLY: rows <= last existing bar are never changed; only rows with ts > last bar are appended. Every row key of L must be
     extended (else refuse), non-row keys kept. Overlap check: builder keys recomputed on the last --overlap existing rows are compared
     with L (max abs/rel diff recorded) — a non-zero overlap diff flags builder non-causality or provisional history rows.
  7. tools/npz_guard.should_allow_overwrite + tmp (same dir) + os.replace; stamp data/vec_live/npz_fresh.json {sym: ts_last, built_at, md5,...}.
  8. SENTIMENT wave (same cycle, unless --no-sentiment): breadth over ALL active NPZs at each newly-appended ts,
     score = 50+(bull-bear)/tot*50 from wt_composite_bias (identical formula to _inject_market_sentiment / npz_sentiment_force),
     patched into the installed files (one atomic rewrite each; files missing the key get it). Crypto+stocks share the 15m grid so
     breadth mixes per exact ts exactly like the legacy full injects. A Sunday full npz_sentiment_force cron reconciles stragglers.
  9. F&G wave (once/day, unless --no-fng): real history from api.alternative.me (limit=0) -> data/fear_greed_cache/fng.json, the file
     the builder's _inject_fear_greed asof-fills. Never fabricated; on fetch failure the old file stays and new rows get zeros (= the
     documented "no F&G" signal).
usage: npz_bar_refresh.py [--mode both] [--symbols A,B] [--ind-dir DIR] [--workers 6] [--w15 0] [--dry-run] [--force] [--loop]
"""
import argparse, asyncio, datetime as dt, fcntl, hashlib, json, os, sys, time, urllib.request, zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("NPZ_REFRESH_ROOT", os.path.expanduser("~/binance-sandbox")))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
BAR = 900
FAPI = "https://fapi.binance.com/fapi/v1/klines"
FAPI_TF = {"15m": ("15m", 900), "1h": ("1h", 3600), "4h": ("4h", 14400), "D": ("1d", 86400)}
STAMP = ROOT / "data" / "vec_live" / "npz_fresh.json"
LOOPSTAMP = ROOT / "data" / "vec_live" / "npz_loop.json"
FUNDOI_KEYS = ("funding_rate_15m", "funding_rate", "oi_15m", "oi_value_15m", "oi_change_15m_15m", "oi_change_1h_15m", "oi_change_1h_pct", "cov_funding", "cov_oi")
ET = ZoneInfo("America/New_York")
STOCK_GRID_MIN, STOCK_GRID_MAX = 240, 1185  # 04:00-19:45 ET (STKT grid; matches the live stock NPZ grid)
STOCK_CLOSE_GRACE = 30  # s after a 15m bar closes before trusting the Tradier bar (API propagation)
STOCK_PREFETCH_SEM = 2  # Tradier prefetch concurrency (gentle: s1 shares the token with the live stack)
FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"
FNG_CACHE = ROOT / "data" / "fear_greed_cache" / "fng.json"
SENT_BACKFILL_S = 20 * 3600  # unknown sent_through -> (re)patch rows newer than now-20h (one-time heal, then tracked exactly)
SENT_BACKLOG_CAP = 96  # max backlog rows patched per file per cycle (older heals over cycles + Sunday full)


def is_crypto_symbol(symbol):
    s = str(symbol).upper()
    return s.endswith("USDT") or s.endswith("USDC")


class _Captured(Exception):
    def __init__(self, payload):
        super().__init__("captured")
        self.payload = payload


def log(msg):
    print(f"[npz_bar_refresh {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def last_closed_bar(now=None):
    now = int(now if now is not None else time.time())
    return (now // BAR) * BAR - BAR


def fetch_fapi(symbol, tf, limit, end_open):
    """closed Binance futures klines for tf with open time <= end_open (limit <= 1500), as a builder-format DataFrame (UTC index)."""
    import pandas as pd
    iv, per = FAPI_TF[tf]
    url = f"{FAPI}?symbol={symbol}&interval={iv}&limit={max(2, min(1500, int(limit)))}"
    with urllib.request.urlopen(url, timeout=15) as r:
        rows = json.loads(r.read().decode())
    now_ms = int(time.time() * 1000)
    rows = [k for k in rows if int(k[6]) < now_ms and int(k[0]) // 1000 <= end_open]
    if not rows:
        return None
    idx = pd.to_datetime([int(k[0]) for k in rows], unit="ms", utc=True)
    df = pd.DataFrame({"timestamp": [t.strftime("%Y-%m-%dT%H:%M:%S.000000Z") for t in idx], "open": [float(k[1]) for k in rows], "high": [float(k[2]) for k in rows],
                       "low": [float(k[3]) for k in rows], "close": [float(k[4]) for k in rows], "volume": [float(k[5]) for k in rows]}, index=idx)
    df.index.name = "timestamp_dt"
    return df


def fetch_fresh(symbol, last_ts, cutoff):
    """closed 15m bars from Binance futures REST (weight 1-2 at steady state). HTF frames come from the builder's own sources
    (s1 klines_cache written by ez_klines) — no HTF REST calls (s1 shares its IP with the ez stack; Binance -1003 bans hit everyone)."""
    gap = max(1, (cutoff - last_ts) // BAR)
    try:
        return {"15m": fetch_fapi(symbol, "15m", gap + 3, cutoff)}
    except Exception as e:
        return {"_rest_error": f"{type(e).__name__}: {e}"[:160]}


def last_closed_stock_bar(now=None):
    """latest 15m bar open (UTC grid — the stock grid is ts%900==0) that is CLOSED incl. Tradier propagation grace."""
    now = int(now if now is not None else time.time())
    return ((now - STOCK_CLOSE_GRACE) // BAR) * BAR - BAR


def stocks_gate(now=None):
    """whether a stocks Tradier prefetch is worthwhile now. Returns (attempt, reason). Out-of-gate cycles skip stocks
    entirely (crypto still runs); the loop re-attempts deeply stale stocks (missed >=1 session) at most once/hour."""
    now = now if now is not None else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return False, "weekend"
    mm = et.hour * 60 + et.minute
    if 210 <= mm <= 1230:
        return True, "session"
    return False, "night"


def last_session_final_bar(now=None):
    """most recent fully-closed weekday 19:45 ET bar (ext close), epoch; 0 when none in range (clock sanity).
    Out-of-gate catch-up uses this (not an hour threshold): any stock older than the last closed session gets fetched."""
    now = now if now is not None else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    ts_now = now.timestamp()
    et_now = now.astimezone(ET)
    for back in range(10):
        d = (et_now - dt.timedelta(days=back)).date()
        if d.weekday() >= 5:
            continue
        cand = int(dt.datetime(d.year, d.month, d.day, 19, 45, tzinfo=ET).timestamp())
        if cand + BAR + STOCK_CLOSE_GRACE <= ts_now:
            return cand
    return 0


def backlog_window(ts_last, sent_through_prev, now_s):
    """(lo_excl, hi_incl) ts window needing sentiment (re)patch, or None. Unknown sent_through -> the last
    SENT_BACKFILL_S window (one-time heal for pre-tracking rows, e.g. restart-interrupted placeholders)."""
    if ts_last is None:
        return None
    if sent_through_prev is None:
        return (now_s - SENT_BACKFILL_S, ts_last)
    if ts_last > sent_through_prev:
        return (sent_through_prev, ts_last)
    return None


def stock_prefetch_action(s, co, err, last):
    """pure routing for a prefetched stock: ("job", None) = refresh in wave 1; ("result", r) = terminal FRESH/NO_NPZ;
    ("fail", r) = terminal PREFETCH_FAILED (caller logs). No-NPZ symbols never reach the API (no wasted calls)."""
    if co is not None:
        return ("job", None)
    if last is None:
        return ("result", {"symbol": s, "mode": "tradier", "status": "NO_NPZ"})
    if not err:
        return ("result", {"symbol": s, "mode": "tradier", "status": "FRESH", "via": "prefetch", "ts_before": last})
    return ("fail", {"symbol": s, "mode": "tradier", "status": "PREFETCH_FAILED", "error": err, "ts_before": last})


def tradier_rows_to_df(rows, cutoff):
    """Tradier 15min time&sales rows -> builder-format DataFrame (UTC index), STKT grid filter (04:00-19:45 ET),
    epoch 'timestamp' as true UTC, bars with ts > cutoff or still forming dropped. Returns None when empty."""
    import math
    import pandas as pd
    out = {}
    for r in rows or []:
        try:
            e = int(float(r.get("timestamp")))
        except (TypeError, ValueError):
            continue
        if e > cutoff:
            continue
        t = pd.Timestamp(e, unit="s", tz="UTC")
        loc = t.tz_convert("America/New_York")
        mm = loc.hour * 60 + loc.minute
        if not (STOCK_GRID_MIN <= mm <= STOCK_GRID_MAX):
            continue
        try:
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            v = float(r.get("volume", 0) or 0)
        except (TypeError, ValueError, KeyError):
            continue
        if not (math.isfinite(c) and c > 0):
            continue
        out[e] = (o, h, l, c, v)
    if not out:
        return None
    idx = pd.to_datetime(sorted(out), unit="s", utc=True)
    df = pd.DataFrame({"timestamp": [t.strftime("%Y-%m-%dT%H:%M:%S.000000Z") for t in idx], "open": [out[int(t.timestamp())][0] for t in idx],
                       "high": [out[int(t.timestamp())][1] for t in idx], "low": [out[int(t.timestamp())][2] for t in idx],
                       "close": [out[int(t.timestamp())][3] for t in idx], "volume": [out[int(t.timestamp())][4] for t in idx]}, index=idx)
    df.index.name = "timestamp_dt"
    return df


async def prefetch_tradier_15m(symbols, last_ts_map, cutoff):
    """ONE shared Tradier client, sem-paced: {sym: (df|None, cutoff|None, err|None)}. Workers never touch the API.
    Per-symbol cutoff = min(global cutoff, max fresh closed bar); a symbol with no fresh bar newer than its NPZ keeps cutoff=None (FRESH)."""
    from utils import load_environment_from_gpg
    load_environment_from_gpg(None)
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    client = TradierAPIClient(TradierConfig(), account_key="tra")
    await client.connect()
    sem = asyncio.Semaphore(STOCK_PREFETCH_SEM)
    out = {}

    async def one(symbol):
        async with sem:
            try:
                last = int(last_ts_map.get(symbol, 0) or 0)
                start = dt.datetime.fromtimestamp(max(last - 2 * 86400, 0), dt.timezone.utc).strftime("%Y-%m-%d")
                rows = await asyncio.wait_for(client.get_timesales(symbol, interval="15min", start=start), timeout=90)
                df = tradier_rows_to_df(rows, cutoff)
                if df is None or not len(df):
                    return symbol, (None, None, "no Tradier bars")
                import pandas as pd
                mx = int(df.index[-1].timestamp())
                if mx <= last:
                    return symbol, (None, None, None)
                return symbol, (df, min(int(cutoff), mx), None)
            except Exception as e:
                return symbol, (None, None, f"{type(e).__name__}: {e}"[:160])
            finally:
                await asyncio.sleep(0.15)

    try:
        for fut in asyncio.as_completed([one(s) for s in symbols]):
            sym, res = await fut
            out[sym] = res
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return out


def refresh_fng_cache(max_age_h=20.0, dry_run=False):
    """once/day real F&G history (api.alternative.me ?limit=0) -> data/fear_greed_cache/fng.json (builder's asof source).
    Validates shape before install; on ANY failure the old file stays (new rows then get zeros = documented 'no F&G')."""
    try:
        age_h = (time.time() - FNG_CACHE.stat().st_mtime) / 3600.0
        if age_h < max_age_h:
            return {"status": "FRESH", "age_h": round(age_h, 1)}
    except OSError:
        pass
    if dry_run:
        return {"status": "SKIPPED_DRY_RUN"}
    try:
        req = urllib.request.Request(FNG_URL, headers={"User-Agent": "npz_bar_refresh/2.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode())
        recs = []
        for e in payload.get("data", []):
            v, t = int(e["value"]), int(e["timestamp"])
            if 0 <= v <= 100 and t > 0:
                recs.append({"timestamp": t, "value": v})
        if len(recs) < 30:
            return {"status": "REFUSED_THIN", "n": len(recs)}
        recs.sort(key=lambda e: e["timestamp"])
        FNG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FNG_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(recs))
        os.replace(tmp, FNG_CACHE)
        return {"status": "INSTALLED", "n": len(recs), "last": recs[-1]}
    except Exception as e:
        return {"status": "ERROR", "error": f"{type(e).__name__}: {e}"[:160]}


def breadth_scores(ind_dir, symbols, ts_set):
    """read-only scan of actives: {t: (score, n)} with score = 50+(bull-bear)/tot*50 from wt_composite_bias at t
    (identical formula to _inject_market_sentiment / npz_sentiment_force; crypto+stocks mix per exact ts like legacy)."""
    import numpy as np
    from collections import defaultdict
    want = sorted(ts_set)
    bull, bear, tot = defaultdict(int), defaultdict(int), defaultdict(int)
    n_files = 0
    for sym in symbols:
        p = Path(ind_dir) / f"{sym}.npz"
        if not p.exists():
            continue
        try:
            with np.load(p, allow_pickle=True) as z:
                if "timestamps" not in z.files or "wt_composite_bias" not in z.files:
                    continue
                ts = np.asarray(z["timestamps"]).astype(np.int64)
                if ts[-1] > 1e11:
                    ts = ts // 1000
                bias = np.asarray(z["wt_composite_bias"]).astype(np.int8)
                if len(ts) != len(bias) or not len(ts):
                    continue
                if ts[-1] < want[0] or ts[0] > want[-1]:
                    continue
                n_files += 1
                pos = np.searchsorted(ts, np.asarray(want, dtype=np.int64))
                for j, t in enumerate(want):
                    i = int(pos[j])
                    if i < len(ts) and int(ts[i]) == t:
                        b = int(bias[i])
                        tot[t] += 1
                        if b == 1:
                            bull[t] += 1
                        elif b == -1:
                            bear[t] += 1
        except Exception:
            continue
    return {t: (float(50.0 + (bull[t] - bear[t]) / max(tot[t], 1) * 50.0), tot[t]) for t in want if tot[t] > 0}, n_files


def patch_sentiment_file(path, new_ts, scores):
    """atomic full-rewrite patch of market_sentiment_score at new_ts (adds the key when missing); shape otherwise identical."""
    import numpy as np
    path = Path(path)
    z = np.load(path, allow_pickle=True)
    L = {k: z[k] for k in z.files}
    ts = np.asarray(L["timestamps"]).astype(np.int64)
    if ts[-1] > 1e11:
        ts = ts // 1000
    ts_raw = np.asarray(L["timestamps"])
    pos = np.searchsorted(ts, np.asarray(sorted(new_ts), dtype=np.int64))
    pairs = [(int(pos[j]), scores[t]) for j, t in enumerate(sorted(new_ts)) if t in scores and int(pos[j]) < len(ts) and int(ts[int(pos[j])]) == t]
    if not pairs:
        return {"status": "NO_MATCHING_TS"}
    if "market_sentiment_score" in L:
        mss = np.asarray(L["market_sentiment_score"], dtype=np.float64).reshape(-1).copy()
        if len(mss) != len(ts):
            return {"status": "REFUSE_LEN_MISMATCH"}
    else:
        mss = np.full(len(ts), 50.0, dtype=np.float64)
    for i, (score, _n) in pairs:
        mss[i] = score
    D = dict(L)
    D["market_sentiment_score"] = mss.astype(np.float32)
    if set(D) - set(L) - {"market_sentiment_score"}:
        return {"status": "REFUSE_KEY_DRIFT"}
    for k in L:
        if len(np.asarray(D[k]).reshape(-1)) != len(np.asarray(L[k]).reshape(-1)) and np.ndim(L[k]) >= 1:
            return {"status": "REFUSE_LEN_DRIFT", "key": k}
    if not np.all(np.isfinite(mss[[i for i, _ in pairs]])):
        return {"status": "REFUSE_NON_FINITE"}
    tmp = save_npz(path, D)
    chk = np.load(tmp, allow_pickle=True)
    ok = len(chk.files) == len(D) and int(np.asarray(chk["timestamps"]).astype(np.int64).reshape(-1)[-1] // (1000 if ts_raw[-1] > 1e11 else 1)) == int(ts[-1])
    if not ok:
        tmp.unlink()
        return {"status": "TMP_VERIFY_FAILED"}
    try:
        os.chmod(tmp, path.stat().st_mode & 0o777)
    except Exception:
        pass
    os.replace(tmp, path)
    return {"status": "PATCHED", "n_ts": len(pairs)}


def _epoch_s(idx):
    """epoch seconds of a DatetimeIndex regardless of its unit (pandas 3 parses ISO strings to datetime64[us], not ns)."""
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    return idx.as_unit("s").asi8


def cache_bar_final(symbol, cutoff):
    """REST fallback: the cutoff bar in the builder's primary 15m cache is final only if a NEWER row exists (the writer fetched after it closed)."""
    import backtest_v8_precompute as bvp
    df = bvp.load_klines(bvp.CRYPTO_KLINES / f"{symbol}_15m.json")
    if df is None or not len(df):
        return False
    ts = _epoch_s(df.index)
    return bool((ts == cutoff).any() and (ts > cutoff).any())


def cache_bar_final_stocks(symbol, cutoff):
    """stocks REST fallback: the cutoff bar is final only if a NEWER row exists in a tradier 15m cache (builder source order)."""
    import backtest_v8_precompute as bvp
    for d in (ROOT / "klines_cache_backtest" / "tradier", ROOT / "klines_cache" / "tradier", ROOT / "klines_cache_gateway" / "tradier",
              ROOT / "klines_cache_macbook" / "tradier"):
        try:
            df = bvp.load_klines(d / f"{symbol}_15m.json")
        except Exception:
            continue
        if df is None or not len(df):
            continue
        ts = _epoch_s(df.index)
        if bool((ts == cutoff).any() and (ts > cutoff).any()):
            return True
    return False


def _overlay(df, fr, cutoff, tf=None):
    import pandas as pd
    if fr is not None and len(fr):
        if df is None:
            df = fr.copy()
        else:
            if getattr(df.index, "tz", None) is None:
                fr = fr.copy(); fr.index = fr.index.tz_localize(None)
            df = pd.concat([df[~df.index.isin(fr.index)], fr]).sort_index()
    if df is not None:
        lim = pd.Timestamp(cutoff, unit="s", tz="UTC")
        if getattr(df.index, "tz", None) is None:
            lim = lim.tz_localize(None)
        df = df[df.index <= lim]
        if tf in FAPI_TF and tf != "15m" and len(df):
            # HTF: only buckets CLOSED by the time the cutoff 15m bar closed (a still-forming provisional row never enters the frame)
            per = pd.Timedelta(seconds=FAPI_TF[tf][1])
            df = df[df.index + per <= lim + pd.Timedelta(seconds=BAR)]
    return df


class Builder:
    """thin harness around backtest_v8_precompute.compute_symbol (module functions are swapped only for the duration of one call)."""

    def __init__(self):
        import backtest_v8_precompute as bvp
        # closed-bar refresh always uses the builder's classic path: identical day/night math. The live forming-frames path exists only for the still-forming bar the cutoff excludes (and costs n x 4 full indicator runs per symbol).
        os.environ["NPZ_STOCK_FRAMES_LEGACY"] = "1"
        self.bvp = bvp
        self.orig = {k: getattr(bvp, k) for k in ("load_klines", "compute_tf_arrays", "_frame_span_seconds", "_crypto_wm_frame", "resample_tf")}

    def _restore(self):
        for k, v in self.orig.items():
            setattr(self.bvp, k, v)

    def source_dfs(self, symbol, mode, fresh, cutoff):
        """builder-selected per-TF frames for the given input data (compute_symbol stopped at its first compute_tf_arrays call)."""
        bvp, orig_load = self.bvp, self.orig["load_klines"]
        primary_done = set()

        def load(path):
            df = orig_load(path)
            tf = path.name[:-5].rsplit("_", 1)[-1]
            if tf in FAPI_TF and tf not in primary_done and (df is not None and len(df) >= 30 or (fresh.get(tf) is not None and len(fresh[tf]) >= 30)):
                primary_done.add(tf)
                return _overlay(df, fresh.get(tf), cutoff, tf)
            return _overlay(df, None, cutoff, tf)

        def cta(df, tf):
            f = sys._getframe(1)
            while f is not None and f.f_code.co_name != "compute_symbol":
                f = f.f_back
            if f is None or "dfs" not in f.f_locals:
                return self.orig["compute_tf_arrays"](df, tf)
            raise _Captured({k: v.copy() for k, v in f.f_locals["dfs"].items()})

        bvp.load_klines, bvp.compute_tf_arrays = load, cta
        try:
            bvp.compute_symbol(symbol, mode)
        except _Captured as c:
            return c.payload
        finally:
            self._restore()
        return None

    def arrays(self, symbol, mode, dfs, w15=None):
        """builder arrays (dict) for frames dfs; the 15m frame is cut to its trailing w15 rows, HTF frames are used as captured."""
        bvp = self.bvp
        frames = dict(dfs)
        if w15:
            frames["15m"] = frames["15m"].iloc[-int(w15):]
        first = {}

        def load(path):
            tf = path.name[:-5].rsplit("_", 1)[-1]
            if tf in first:
                return None
            first[tf] = True
            return frames.get(tf)

        def span(df):
            f = sys._getframe(1)
            if f.f_code.co_name == "compute_symbol":
                raise _Captured(dict(f.f_locals["merged"]))
            return self.orig["_frame_span_seconds"](df)

        def wm(df15, authentic, tf):
            return frames.get(tf, authentic)

        def rs(df, tf):
            if tf in frames and tf != "15m":
                return frames[tf]
            return self.orig["resample_tf"](df, tf)

        bvp.load_klines, bvp._frame_span_seconds, bvp._crypto_wm_frame, bvp.resample_tf = load, span, wm, rs
        try:
            bvp.compute_symbol(symbol, mode)
        except _Captured as c:
            merged = c.payload
            if mode == "crypto" and os.environ.get("NPZ_HTF_LEGACY_LAG1") != "1":
                import numpy as np
                merged["htf_align"] = np.array(["causal_v3" if os.environ.get("NPZ_WM_LEGACY") != "1" else "causal_v2"])
            return merged
        finally:
            self._restore()
        raise RuntimeError("compute_symbol returned without reaching the save stage")


def post_process(D, symbol, n_old, have_cov, mode="crypto", need=None):
    """live post-processor chain on the spliced dict D (full length): fundoi -> v15_npz_augment add_* (+formations) -> dc_width_*_prev.
    Returns {key: full-length array}; caller appends only rows >= n_old. Each aug call is fail-open wrapped (a crashing post-processor
    logs once and is skipped; success path byte-identical to v1). vwap session days follow the venue (ET for stocks). need (the keys
    splice will actually consume from post) gates each group: groups whose outputs are all builder-provided or absent from L are
    skipped — identical outputs, no wasted compute (add_wicks alone costs ~5s/symbol)."""
    import numpy as np
    out = {}
    n = len(D["timestamps"])
    if have_cov:
        from npz_fundoi import build_fundoi_npz as fb
        cache = fb.STAGE_CACHE / f"{symbol}.json"
        ts = np.asarray(D["timestamps"]).astype(np.int64)
        if cache.exists():
            rec = json.loads(cache.read_text())
            f, o = rec["funding"], rec["oi"]
            if f:
                fts = np.array([int(r["fundingTime"]) // 1000 for r in f], dtype=np.int64)
                fv = np.array([float(r["fundingRate"]) for r in f], dtype=np.float64)
                fr, fok = fb.asof(ts, fts, fv, fb.FUND_STALE_S)
            else:
                fr, fok = np.full(n, np.nan), np.zeros(n, bool)
            if o:
                ots = np.array([int(r["timestamp"]) // 1000 for r in o], dtype=np.int64)
                oi_, ook = fb.asof(ts, ots, np.array([float(r["sumOpenInterest"]) for r in o]), fb.OI_STALE_S)
                oiv, _ = fb.asof(ts, ots, np.array([float(r["sumOpenInterestValue"]) for r in o]), fb.OI_STALE_S)
            else:
                oi_, oiv, ook = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, bool)
        else:
            fr, fok = np.full(n, np.nan), np.zeros(n, bool)
            oi_, oiv, ook = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, bool)
        ch15, ch1h = fb.lagpct(oi_, 1), fb.lagpct(oi_, 4)
        for k, v in {"funding_rate_15m": fr, "funding_rate": fr, "oi_15m": oi_, "oi_value_15m": oiv, "oi_change_15m_15m": ch15, "oi_change_1h_15m": ch1h, "oi_change_1h_pct": ch1h}.items():
            out[k] = v.astype(np.float32)
        out["cov_funding"] = fok.astype(np.int8)
        out["cov_oi"] = ook.astype(np.int8)
        for k, v in out.items():
            D[k] = v
    import v15_npz_augment as aug
    base = {k: v for k, v in D.items() if not k.startswith("formation_")}
    a = {}

    def _run(names, prefixes=()):
        if need is None:
            return True
        for k in need:
            if k in names:
                return True
            for p in prefixes:
                if k.startswith(p):
                    return True
        return False

    _stocks = (mode == "tradier")
    _failed = []
    _groups = (("funding_z", aug.add_funding_z, {"funding_zscore", "funding_extreme"}, ()), ("oi_velocity", aug.add_oi_velocity, {"oi_vel_1h", "oi_vel_4h", "oi_vel_regime"}, ()),
               ("orb", aug.add_orb, {"orb_high", "orb_low", "orb_position"}, ()), ("smfi", aug.add_smfi, set(), ("smfi_",)),
               ("vwap", None, {"vwap", "vwap_upper1", "vwap_lower1", "vwap_upper2", "vwap_lower2", "vwap_distance_pct"}, ()),
               ("rsi2", aug.add_rsi2, set(), ("rsi_2_",)), ("kc_position", aug.add_kc_position, set(), ("kc_position_",)),
               ("wicks", aug.add_wicks, set(), ("bar_upper_wick_", "bar_lower_wick_", "bar_body_ratio_", "bar_vol_ratio_", "bar_atr_rank_", "bar_streak_", "bar_swing_")),
               ("formations", aug.add_formations, set(), ("formation_",)))
    for _name, _fn, _names, _prefixes in _groups:
        if not _run(_names, _prefixes):
            continue
        try:
            if _name == "vwap":
                aug.add_vwap(base, a, _stocks)
            else:
                _fn(base, a)
        except Exception as e:
            _failed.append(f"{_name}:{type(e).__name__}")
    if _failed:
        log(f"{symbol} post_process skipped: {','.join(_failed)}")
    for k, v in a.items():
        out.setdefault(k, v)
    if need is None or any(k.startswith("dc_width_") and k.endswith("_prev") for k in need):
        for w in sorted(k for k in D if k.startswith("dc_width_") and not k.endswith("_prev") and k != "dc_width"):
            arr = np.asarray(D[w], dtype=np.float32)
            pr = np.roll(arr, 1).astype(np.float32)
            pr[0] = arr[0]
            out.setdefault(w + "_prev", pr)
    return out


def _cast(a, like):
    import numpy as np
    a = np.asarray(a)
    return a.astype(like.dtype) if a.dtype != like.dtype else a


def backfill_legacy_keys(full, L, keys, symbol):
    """verified replication of retired-builder keys. Each candidate is computed from the spliced arrays and VERIFIED
    against the frozen old rows (<=1e-5 rel on up to the last 8 nonzero rows); unverified keys are returned missing so the
    caller refuses loudly. Returns {key: full-length array} for verified keys only. Registry:
      volume_D_50_sma: rolling(50, min_periods=1).mean over NATIVE D volume (old/backtest_v8_precompute_tradier.py:561),
        native series recovered as first row per timestamp_D group (exact: broadcast repeats the native value)."""
    import numpy as np
    out = {}
    if "volume_D_50_sma" in keys and "volume_D" in full and "timestamp_D" in full and "volume_D_50_sma" in L:
        try:
            v = np.asarray(full["volume_D"], dtype=np.float64).reshape(-1)
            td = np.asarray(full["timestamp_D"]).reshape(-1)
            old = np.asarray(L["volume_D_50_sma"], dtype=np.float64).reshape(-1)
            if len(v) >= len(old) and len(old) >= 2:
                chg = np.ones(len(td), bool)
                chg[1:] = td[1:] != td[:-1]
                firsts = np.flatnonzero(chg)
                gid = np.cumsum(chg) - 1
                import pandas as pd
                sma = pd.Series(v[firsts]).rolling(50, min_periods=1).mean().values
                rep = sma[gid].astype(np.float32)
                j = slice(max(0, len(old) - 8), len(old))
                ok = np.isfinite(old[j]) & (old[j] != 0)
                if bool(ok.any()):
                    err = np.abs(rep[j][ok].astype(np.float64) - old[j][ok]) / np.maximum(np.abs(old[j][ok]), 1e-9)
                    if float(err.max()) <= 1e-5:
                        out["volume_D_50_sma"] = rep
                    else:
                        log(f"{symbol} backfill volume_D_50_sma UNVERIFIED (worst rel {float(err.max()):.2g})")
                else:
                    log(f"{symbol} backfill volume_D_50_sma UNVERIFIED (no nonzero old rows)")
        except Exception as e:
            log(f"{symbol} backfill volume_D_50_sma failed: {type(e).__name__}")
    return out


def splice(L, tail, symbol, overlap=8, mode="crypto"):
    """append rows of tail with ts > last ts of L; every row key of L must be extended. returns (new_dict, info)."""
    import numpy as np
    ts_old = np.asarray(L["timestamps"]).astype(np.int64)
    n_old = len(ts_old)
    ts_t = np.asarray(tail["timestamps"]).astype(np.int64)
    new_mask = ts_t > ts_old[-1]
    k_new = int(new_mask.sum())
    info = {"n_old": n_old, "appended": k_new, "new_ts": [int(t) for t in ts_t[new_mask].tolist()]}
    if k_new == 0:
        return None, info
    if (ts_t[new_mask][0] - ts_old[-1]) <= 0 or not np.all(np.diff(ts_t[new_mask]) > 0):
        raise ValueError("non-monotonic tail timestamps")
    row_keys = [k for k in L if np.ndim(L[k]) >= 1 and len(L[k]) == n_old and k != "htf_align"]
    D = {}
    builder_keys = set()
    for k in row_keys:
        if k in tail and np.ndim(tail[k]) >= 1 and len(tail[k]) == len(ts_t):
            D[k] = np.concatenate([L[k], _cast(np.asarray(tail[k])[new_mask], L[k])])
            builder_keys.add(k)
    # overlap check: builder arrays recomputed on the last `overlap` existing rows vs L
    ov = np.isin(ts_t, ts_old[-overlap:])
    j_old = np.searchsorted(ts_old, ts_t[ov])
    worst, worst_key, n_bad = 0.0, None, 0
    for k in builder_keys:
        if k in FUNDOI_KEYS and "cov_funding" in L:
            continue
        a = np.asarray(L[k])[j_old]
        b = np.asarray(tail[k])[ov]
        if a.dtype.kind in "fc" or b.dtype.kind in "fc":
            a64, b64 = a.astype(np.float64), b.astype(np.float64)
            both_nan = np.isnan(a64) & np.isnan(b64)
            d = np.where(both_nan, 0.0, np.abs(a64 - b64) / np.maximum(1.0, np.abs(a64)))
            d = np.where(np.isnan(d), np.inf, d)
            m = float(d.max()) if d.size else 0.0
        elif a.dtype.kind in "biu" and b.dtype.kind in "biu":
            m = float(np.abs(a.astype(np.int64) - b.astype(np.int64)).max()) if a.size else 0.0
        else:
            m = 0.0 if np.array_equal(a.astype(str), b.astype(str)) else 1.0
        if m > 1e-6:
            n_bad += 1
        if m > worst:
            worst, worst_key = m, k
    info.update({"overlap_rows": int(ov.sum()), "overlap_keys_diff": n_bad, "overlap_worst": worst, "overlap_worst_key": worst_key})
    # spliced view for post processors: builder keys extended, others padded so the functions see full arrays
    full = dict(D)
    for k in row_keys:
        if k not in full:
            pad = np.zeros(k_new, dtype=np.asarray(L[k]).dtype) if np.asarray(L[k]).dtype.kind in "biufc" else np.full(k_new, np.asarray(L[k])[-1])
            full[k] = np.concatenate([L[k], pad])
    need = {k for k in row_keys if k not in builder_keys or k in FUNDOI_KEYS}
    post = post_process(full, symbol, n_old, "cov_funding" in L, mode=mode, need=need)
    _bf = [k for k in need if k not in post]
    if _bf:
        post.update(backfill_legacy_keys(full, L, _bf, symbol))
    missing = []
    for k in row_keys:
        if k in builder_keys and k not in FUNDOI_KEYS:
            continue
        if k in post and len(post[k]) == n_old + k_new:
            D[k] = np.concatenate([L[k], _cast(np.asarray(post[k])[n_old:], L[k])])
        elif k in builder_keys:
            continue
        else:
            missing.append(k)
    if missing:
        raise ValueError(f"cannot extend {len(missing)} row keys: {missing[:8]}")
    for k in L:
        if k not in D:
            D[k] = L[k]
    return D, info


def save_npz(path, D, level=1):
    """np.savez_compressed-equivalent writer (npz = zip of .npy members), deflate level 1 for speed; tmp in the same dir + os.replace."""
    import numpy as np
    tmp = path.with_name(f".{path.stem}.{os.getpid()}.barrefresh.tmp.npz")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level, allowZip64=True) as zf:
        for k, v in D.items():
            with zf.open(k + ".npy", "w", force_zip64=True) as f:
                np.lib.format.write_array(f, np.asanyarray(v), allow_pickle=True)
    return tmp


def validate(L, D, path):
    import numpy as np
    from npz_guard import should_allow_overwrite
    missing = sorted(set(L) - set(D))
    if missing:
        return False, f"lost keys {missing[:5]}"
    n0, n1 = len(L["timestamps"]), len(D["timestamps"])
    if n1 <= n0:
        return False, "not longer"
    for k in L:
        if np.ndim(L[k]) >= 1 and len(L[k]) == n0 and k != "htf_align" and len(D[k]) != n1:
            return False, f"{k} len {len(D[k])} != {n1}"
    ts = np.asarray(D["timestamps"]).astype(np.int64)
    if not np.all(np.diff(ts) > 0):
        return False, "timestamps not increasing"
    for k in ("open_15m", "high_15m", "low_15m", "close_15m", "close"):
        if k in D and not np.all(np.isfinite(np.asarray(D[k])[n0:].astype(np.float64))):
            return False, f"non-finite {k} in appended rows"
    span = float(ts[-1] - ts[0]) / 86400.0
    dtm = float(np.median(np.diff(ts.astype(float))))
    ok, reason = should_allow_overwrite(path, span, dtm, len(D), candidate_n=n1, candidate_ts_span_days=span)
    return ok, reason


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def refresh_symbol(symbol, ind_dir, w15=0, dry_run=False, force=False, cutoff=None, fresh=None, mode="crypto", overlap=8, builder=None):
    import numpy as np
    t0 = time.time()
    if cutoff is None:
        cutoff = last_closed_bar() if mode == "crypto" else last_closed_stock_bar()
    cutoff = int(cutoff)
    path = Path(ind_dir) / f"{symbol}.npz"
    res = {"symbol": symbol, "cutoff": cutoff, "mode": mode}
    if not path.exists():
        return {**res, "status": "NO_NPZ"}
    z = np.load(path, allow_pickle=True)
    _ts_raw = np.asarray(z["timestamps"]).astype(np.int64)
    if int(_ts_raw[-1]) > 1e11:
        return {**res, "status": "REFUSE_MS_TIMESTAMPS"}
    ts_last = int(_ts_raw[-1])
    res["ts_before"] = ts_last
    if ts_last >= cutoff and not force:
        return {**res, "status": "FRESH", "secs": round(time.time() - t0, 2)}
    if mode == "crypto" and ("htf_align" not in z.files or str(np.asarray(z["htf_align"]).ravel()[0]) not in ("causal_v3",)):
        return {**res, "status": "REFUSE_LEGACY_HTF_ALIGN"}
    L = {k: z[k] for k in z.files}
    n_old = len(L["timestamps"])
    t1 = time.time()
    if fresh is None:
        fresh = fetch_fresh(symbol, ts_last, cutoff) if mode == "crypto" else {"15m": None}
    else:
        fresh = dict(fresh)
    res["t_fetch"] = round(time.time() - t1, 2)
    if fresh.get("_rest_error"):
        res["rest_error"] = fresh.pop("_rest_error")
    if fresh.get("15m") is None or int(fresh["15m"].index[-1].timestamp()) < cutoff:
        fresh.pop("15m", None)
        _cbf = cache_bar_final_stocks if mode == "tradier" else cache_bar_final
        if not _cbf(symbol, cutoff):
            return {**res, "status": "EXCHANGE_BAR_NOT_READY"}
        res["source_15m"] = "klines_cache(final-by-successor)"
    else:
        res["source_15m"] = "fapi" if mode == "crypto" else "tradier"
    if mode == "tradier" and fresh.get("15m") is not None:
        _fts = _epoch_s(fresh["15m"].index)
        gap = int((((_fts > ts_last) & (_fts <= cutoff)).sum()))
    else:
        gap = (cutoff - ts_last) // BAR
    if gap > 1400:
        return {**res, "status": "GAP_TOO_LARGE", "gap_bars": int(gap)}
    b = builder or Builder()
    t2 = time.time()
    dfs = b.source_dfs(symbol, mode, fresh, cutoff)
    if dfs is None or "15m" not in dfs:
        return {**res, "status": "NO_SOURCE"}
    res["t_capture"] = round(time.time() - t2, 2)
    t3 = time.time()
    tail = b.arrays(symbol, mode, dfs, w15=(int(gap) + int(w15)) if int(w15) > 0 else None)
    res["t_tail"] = round(time.time() - t3, 2)
    t4 = time.time()
    D, info = splice(L, tail, symbol, overlap=overlap, mode=mode)
    res.update(info)
    res["t_post"] = round(time.time() - t4, 2)
    if D is None:
        return {**res, "status": "NO_NEW_ROWS"}
    ok, reason = validate(L, D, path)
    res["guard"] = reason
    if not ok:
        return {**res, "status": "GUARD_REFUSED"}
    res["ts_after"] = int(np.asarray(D["timestamps"])[-1])
    if dry_run:
        return {**res, "status": "DRY_RUN_OK", "secs": round(time.time() - t0, 2)}
    t5 = time.time()
    tmp = save_npz(path, D)
    chk = np.load(tmp, allow_pickle=True)
    if len(chk.files) != len(D) or int(np.asarray(chk["timestamps"])[-1]) != res["ts_after"]:
        tmp.unlink()
        return {**res, "status": "TMP_VERIFY_FAILED"}
    try:
        os.chmod(tmp, path.stat().st_mode & 0o777)
    except Exception:
        pass
    os.replace(tmp, path)
    res["t_save"] = round(time.time() - t5, 2)
    res["md5"] = md5(path)
    res["installed_at"] = time.time()
    res["latency_from_bar_close_s"] = round(res["installed_at"] - (res["ts_after"] + BAR), 1)
    res["secs"] = round(time.time() - t0, 2)
    res["status"] = "INSTALLED"
    return res


_BUILDER = None


def warm_imports():
    import logging
    import backtest_v8_precompute
    logging.getLogger("v7_precompute").setLevel(logging.WARNING)
    for m in ("tradier_indicators", "ez_indicators", "classic_formations", "v15_npz_augment", "npz_guard", "vec_paths.stdev_macro_vec", "npz_fundoi.build_fundoi_npz"):
        try:
            __import__(m)
        except Exception as e:
            log(f"warm import {m} failed: {e}")


_LOCK_FD = None
_TERM = {"n": 0}


class _Shutdown(Exception):
    pass


def _pool_init():
    import signal as _sig
    _sig.signal(_sig.SIGTERM, _sig.SIG_DFL)
    if _LOCK_FD is not None:
        try:
            os.close(_LOCK_FD)
        except OSError:
            pass


def _drain_pool(pool, jobs):
    """consume pool.imap results with 5s SIGTERM polling; raises _Shutdown on SIGTERM or pool stall (caller stamps partials)."""
    out = []
    it = pool.imap(_worker, jobs)
    from multiprocessing.context import TimeoutError as _MpTimeoutError
    silent = 0
    while True:
        if _TERM["n"]:
            raise _Shutdown()
        try:
            r = it.next(timeout=5)
        except StopIteration:
            return out
        except (_MpTimeoutError, TimeoutError):
            silent += 1
            if silent >= (60 if out else 120):
                log(f"pool stall: {silent}x5s silence with {len(out)} results drained; treating pool as dead, restart recovers")
                raise _Shutdown()
            continue
        silent = 0
        out.append(r)


def _worker(args):
    global _BUILDER
    symbol, ind_dir, w15, dry, force, cutoff, mode, fresh = args
    try:
        if _BUILDER is None:
            _BUILDER = Builder()
        return refresh_symbol(symbol, ind_dir, w15=w15, dry_run=dry, force=force, cutoff=cutoff, fresh=fresh, mode=mode, builder=_BUILDER)
    except Exception as e:
        return {"symbol": symbol, "status": "ERROR", "mode": mode, "error": f"{type(e).__name__}: {e}"[:300]}


def npz_last_ts(ind_dir, symbol):
    """last bar ts (seconds) of an NPZ, or None. Lazy: reads the timestamps member only."""
    import numpy as np
    p = Path(ind_dir) / f"{symbol}.npz"
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


def universe_stocks():
    """stock candidates from symbols_tradier.json (Tradier universe); refresh_symbol reports NO_NPZ for missing files."""
    try:
        syms = json.loads((ROOT / "symbols_tradier.json").read_text())
        return [str(s).upper() for s in syms if str(s).strip()]
    except Exception as e:
        log(f"universe_stocks failed: {e}")
        return []


def run_cycle(a, ind_dir, crypto_syms, stock_syms, stock_prefetch, cutoff_crypto, w15, last_ts_map=None, stamp_path=None):
    """one refresh cycle. Wave 1: fork-pool refresh (crypto fapi in workers; stocks from parent-prefetched frames).
    Wave 2 (unless --no-sentiment): breadth scan over actives at new ts + patch of installed files AND backlog files
    (FRESH files with ts_last > sent_through: restart-interrupted placeholders, self-healing). Returns (results, wave2)."""
    import concurrent.futures
    t0 = time.time()
    last_ts_map = last_ts_map or {}
    results = []
    for s in stock_syms:
        df, co, err = stock_prefetch.get(s, (None, None, "no prefetch"))
        act, r = stock_prefetch_action(s, co, err, last_ts_map.get(s))
        if act == "result":
            results.append(r)
    warm_imports()
    jobs = [(s, ind_dir, w15, a.dry_run, a.force, cutoff_crypto, "crypto", None) for s in crypto_syms]
    for s in stock_syms:
        df, co, err = stock_prefetch.get(s, (None, None, "no prefetch"))
        act, r = stock_prefetch_action(s, co, err, last_ts_map.get(s))
        if act == "fail":
            results.append(r)
            log(json.dumps(r, default=str))
            continue
        if act == "result":
            continue
        fr = {"15m": df}
        if err:
            fr["_rest_error"] = err
        jobs.append((s, ind_dir, w15, a.dry_run, a.force, co, "tradier", fr))
    from multiprocessing import get_context
    pool = get_context("fork").Pool(max(1, a.workers), initializer=_pool_init)
    try:
        for r in _drain_pool(pool, jobs):
            results.append(r)
            if r.get("status") not in ("FRESH",):
                slim = {k: v for k, v in r.items() if k != "new_ts"}
                log(json.dumps(slim, default=str))
    finally:
        pool.terminate()
        pool.join()
        if _TERM["n"] and not a.no_stamp and not a.dry_run:
            stamp(results, {}, stamp_path)
    t_wave1 = round(time.time() - t0, 1)
    wave2 = {"status": "SKIPPED"}
    patched_map = {}
    if not a.no_sentiment and not a.dry_run and not _TERM["n"]:
        t2 = time.time()
        import numpy as _np
        targets = {}
        for r in results:
            if r.get("status") == "INSTALLED" and r.get("new_ts"):
                targets.setdefault(r["symbol"], set()).update(r["new_ts"])
        ledger = stamp_sent_through(stamp_path)
        now_s = time.time()
        n_backlog = 0
        for r in results:
            if r.get("status") not in ("INSTALLED", "FRESH"):
                continue
            if r.get("status") == "INSTALLED" and not r.get("new_ts"):
                continue
            s, last = r.get("symbol"), r.get("ts_before")
            w = backlog_window(last, ledger.get(s), now_s)
            if not w:
                continue
            try:
                with _np.load(Path(ind_dir) / f"{s}.npz", allow_pickle=True) as z:
                    ts = _np.asarray(z["timestamps"]).astype(_np.int64).reshape(-1)
                    if ts[-1] > 1e11:
                        ts = ts // 1000
                cands = [int(t) for t in ts.tolist() if w[0] < t <= w[1]][-SENT_BACKLOG_CAP:]
                if cands:
                    targets.setdefault(s, set()).update(cands)
                    n_backlog += 1
            except Exception:
                continue
        ts_set = sorted({t for v in targets.values() for t in v})
        if targets and ts_set:
            actives = sorted({s for s in crypto_syms + stock_syms})
            scores, n_files = breadth_scores(ind_dir, actives, ts_set)
            patched, failed = 0, 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(2, min(8, a.workers))) as ex:
                futs = {ex.submit(patch_sentiment_file, str(Path(ind_dir) / f"{s}.npz"), sorted(ts), scores): (s, sorted(ts)) for s, ts in targets.items()}
                for f in concurrent.futures.as_completed(futs):
                    s, ts = futs[f]
                    try:
                        pr = f.result()
                    except Exception as e:
                        pr = {"status": "ERROR", "error": f"{type(e).__name__}: {e}"[:120]}
                    if pr.get("status") == "PATCHED":
                        patched += 1
                        patched_map[s] = max(ts)
                    else:
                        failed += 1
                        log(json.dumps({"symbol": s, "sentiment": pr}, default=str))
            wave2 = {"status": "DONE", "ts": len(ts_set), "breadth_files": n_files, "patched": patched, "failed": failed,
                     "backlog_files": n_backlog, "secs": round(time.time() - t2, 1)}
        else:
            wave2 = {"status": "NOTHING_NEW", "secs": round(time.time() - t2, 1)}
    if not a.no_stamp and not a.dry_run:
        stamp(results, patched_map, stamp_path)
    return results, wave2, t_wave1


def loop_stamp(payload, loop_path=None):
    lp = Path(loop_path) if loop_path else LOOPSTAMP
    lp.parent.mkdir(parents=True, exist_ok=True)
    try:
        cur = json.loads(lp.read_text())
        hist = cur.get("history", []) if isinstance(cur, dict) else []
    except Exception:
        hist = []
    hist = (hist + [payload])[-48:]
    tmp = lp.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": dt.datetime.now(dt.timezone.utc).isoformat(), "history": hist}, indent=1, sort_keys=True, default=str))
    os.replace(tmp, lp)


def next_cycle_wait(now=None):
    """seconds until next :00/:15/:30/:45 UTC boundary + 75s settle (fapi close_time strictness + Tradier propagation)."""
    now = now if now is not None else time.time()
    nxt = ((int(now) // BAR) + 1) * BAR + 75
    return max(5.0, float(nxt - now))


def universe():
    def jl(p, d=None):
        try:
            return json.loads(Path(p).read_text())
        except Exception:
            return d
    pri, rest = [], []
    vd = jl(ROOT / "data" / "vec_live" / "vec_driven.json", {}) or {}
    for k, v in (vd.items() if isinstance(vd, dict) else []):
        if isinstance(v, dict) and v.get("account", "ez") != "ez":
            continue
        pri.append(k.rsplit("_", 1)[0])
    for p in sorted((ROOT / "data" / "golive").glob("candidates_*.json"))[-1:]:
        c = jl(p, {}) or {}
        pri += [k.rsplit("_", 1)[0] for k in (c if isinstance(c, dict) else [])]
    act = jl(ROOT / "symbols_active.json", []) or []
    rest += [s for s in (act if isinstance(act, list) else list(act))]
    seen, out = set(), []
    for s in pri + rest:
        s = str(s).upper()
        if s and s not in seen and (s.endswith("USDT") or s.endswith("USDC")):
            seen.add(s)
            out.append(s)
    return out


def stamp(results, patched=None, stamp_path=None):
    """per-symbol freshness stamp. patched={sym: max_ts} (wave-2 PATCHED files) drives sent_through (max ts with verified-real
    sentiment); INSTALLED-but-unpatched files keep sent_through=ts_before (backlog, healed next cycle)."""
    sp = Path(stamp_path) if stamp_path else STAMP
    patched = patched or {}
    sp.parent.mkdir(parents=True, exist_ok=True)
    with open(str(sp) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            cur = json.loads(sp.read_text())
        except Exception:
            cur = {}
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for r in results:
            s = r.get("symbol")
            if not s:
                continue
            e = cur.get(s, {})
            if r.get("status") == "INSTALLED":
                e.update({"ts_last": r["ts_after"], "ts_last_iso": dt.datetime.fromtimestamp(r["ts_after"], dt.timezone.utc).isoformat(), "built_at": now, "md5": r.get("md5"),
                          "bars": r.get("n_old", 0) + r.get("appended", 0), "appended": r.get("appended"), "latency_s": r.get("latency_from_bar_close_s"),
                          "overlap_worst": r.get("overlap_worst"), "overlap_worst_key": r.get("overlap_worst_key")})
                e["sent_through"] = patched.get(s, r.get("ts_before"))
                e.pop("error", None)
            elif r.get("status") == "FRESH":
                e.setdefault("ts_last", r.get("ts_before"))
                if s in patched:
                    e["sent_through"] = patched[s]
                e["checked_at"] = now
            else:
                e.update({"last_status": r.get("status"), "error": r.get("error") or r.get("guard"), "checked_at": now})
            e["status"] = r.get("status")
            cur[s] = e
        tmp = sp.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1, sort_keys=True))
        os.replace(tmp, sp)


def stamp_sent_through(stamp_path=None):
    """{sym: sent_through} ledger for backlog detection (read-only snapshot; atomic-replace-safe)."""
    sp = Path(stamp_path) if stamp_path else STAMP
    try:
        cur = json.loads(sp.read_text())
    except Exception:
        return {}
    return {s: e.get("sent_through") for s, e in cur.items() if isinstance(e, dict) and e.get("sent_through") is not None}


def split_universe(a):
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
        crypto, stocks = [s for s in syms if is_crypto_symbol(s)], [s for s in syms if not is_crypto_symbol(s)]
    else:
        crypto, stocks = universe(), universe_stocks()
    if a.mode == "crypto":
        stocks = []
    elif a.mode == "stocks":
        crypto = []
    return crypto, stocks


def one_cycle(a, w15, state, in_loop):
    """single cycle: F&G daily -> crypto cutoff -> stocks prefetch (gated in loop; forced one-shot) -> run_cycle. Returns summary."""
    t0 = time.time()
    ind_dir = a.ind_dir
    crypto_syms, stock_syms_all = split_universe(a)
    cutoff_crypto = last_closed_bar()
    fng = {"status": "SKIPPED_FLAG"} if a.no_fng else refresh_fng_cache(dry_run=a.dry_run)
    if fng.get("status") not in ("FRESH", "SKIPPED_FLAG", "SKIPPED_DRY_RUN"):
        log(f"F&G cache: {json.dumps(fng, default=str)}")
    stock_prefetch, last_ts_map, gate_note = {}, {}, ""
    stock_syms = list(stock_syms_all)
    cutoff_stocks = None
    if stock_syms_all:
        for s in stock_syms_all:
            last_ts_map[s] = npz_last_ts(ind_dir, s)
        cutoff_stocks = last_closed_stock_bar()
        attempt, reason = stocks_gate()
        has_npz = [s for s in stock_syms_all if last_ts_map.get(s) is not None]
        fetch_syms = list(has_npz)
        if in_loop and not attempt:
            _final = last_session_final_bar()
            stale = [s for s in has_npz if last_ts_map.get(s) < _final]
            if stale and (time.time() - state.get("last_catchup", 0)) > 3600:
                fetch_syms = stale
                state["last_catchup"] = time.time()
                gate_note = f"night-catchup({len(stale)})"
            else:
                fetch_syms = []
                gate_note = f"gate-skip({reason})"
                stock_syms = []
        t_pf = time.time()
        if fetch_syms:
            try:
                stock_prefetch = asyncio.run(prefetch_tradier_15m(fetch_syms, last_ts_map, cutoff_stocks))
            except Exception as e:
                log(f"stocks prefetch fatal: {type(e).__name__}: {e}"[:200])
                stock_prefetch = {}
                stock_syms = []
                gate_note = (gate_note + " prefetch-fatal").strip()
            else:
                stock_syms = list(fetch_syms if (in_loop and not attempt) else stock_syms_all)
        else:
            stock_prefetch = {}
        gate_note = (gate_note + f" prefetch_s={round(time.time() - t_pf, 1)}").strip()
    results, wave2, t_wave1 = run_cycle(a, ind_dir, crypto_syms, stock_syms, stock_prefetch, cutoff_crypto, w15, last_ts_map)
    cnt = {}
    for r in results:
        cnt[r.get("status")] = cnt.get(r.get("status"), 0) + 1
    summ = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "cutoff_crypto": cutoff_crypto, "cutoff_stocks": cutoff_stocks,
            "n_crypto": len(crypto_syms), "n_stocks": len(stock_syms), "gate": gate_note or stocks_gate()[1],
            "counts": cnt, "wave1_s": t_wave1, "wave2": wave2, "fng": fng.get("status"), "cycle_s": round(time.time() - t0, 1)}
    log(f"cycle done crypto={len(crypto_syms)} stocks={len(stock_syms)} {gate_note} {cnt} wave1={t_wave1}s wave2={wave2.get('status')}/{wave2.get('secs', 0)}s fng={fng.get('status')} total={summ['cycle_s']}s")
    return summ


def run_loop(a, w15):
    import faulthandler
    faulthandler.enable()
    state, n = {"last_catchup": 0.0}, 0
    _alive = {"n": 0}
    import atexit
    def _bye():
        try:
            log(f"process exiting (atexit): completed {_alive['n']} cycles")
        except Exception:
            pass
    atexit.register(_bye)
    import signal as _signal
    def _on_term(signum, frame):
        _TERM["n"] += 1
        try:
            log(f"SIGTERM #{_TERM['n']} (shutdown: pool drain aborts, partial cycle stamped, systemd restarts)")
        except Exception:
            pass
        if _TERM["n"] == 1:
            try:
                import subprocess
                ps = subprocess.run(["ps", "-eo", "pid,ppid,lstart,args"], capture_output=True, text=True, timeout=10).stdout
                Path(f"/tmp/npz_kill_ps_{int(time.time())}.log").write_text(ps)
            except Exception:
                pass
    _signal.signal(_signal.SIGTERM, _on_term)
    log(f"loop start mode={a.mode} workers={a.workers} w15={w15} ind={a.ind_dir}")
    while True:
        try:
            summ = one_cycle(a, w15, state, True)
            loop_stamp(summ)
        except _Shutdown:
            log(f"loop exit on SIGTERM mid-cycle (partial stamped, {n} full cycles done)")
            return
        except (KeyboardInterrupt, SystemExit) as e:
            import traceback
            log(f"CYCLE-BASEEXC swallowed (no TTY here; real stops arrive as SIGTERM, never this): {type(e).__name__} {e}\n{traceback.format_exc()[-2000:]}")
            try:
                loop_stamp({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "baseexc": f"{type(e).__name__}: {e}"[:200]})
            except Exception:
                pass
        except Exception as e:
            import traceback
            log(f"cycle ERROR (continuing): {type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}")
            try:
                loop_stamp({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "cycle_error": f"{type(e).__name__}: {e}"[:200]})
            except Exception:
                pass
        n += 1
        _alive["n"] = n
        if a.max_cycles and n >= a.max_cycles:
            log(f"loop exit after {n} cycles (--max-cycles)")
            return
        if _TERM["n"]:
            log(f"loop exit after SIGTERM x{_TERM['n']} ({n} cycles done)")
            return
        _deadline = time.time() + next_cycle_wait()
        while time.time() < _deadline:
            if _TERM["n"]:
                log(f"loop exit on idle SIGTERM ({n} cycles done)")
                return
            time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols")
    ap.add_argument("--mode", default="both", choices=("both", "crypto", "stocks"))
    ap.add_argument("--ind-dir", default=str(ROOT / "backtest_v8" / "indicators"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--w15", type=int, default=None, help="None = 0 one-shot (proven exact), 2500 in --loop; >0 = trailing window (fast, ~1e-7 ewm tails, quantified per cycle in overlap stats)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-stamp", action="store_true")
    ap.add_argument("--no-sentiment", action="store_true")
    ap.add_argument("--no-fng", action="store_true")
    ap.add_argument("--loop", action="store_true", help="daemon: run a cycle right after every :00/:15/:30/:45 UTC close, forever (systemd + cron keep it alive)")
    ap.add_argument("--max-cycles", type=int, default=0, help="testing only: exit the loop after N cycles (never set in the 24/7 unit)")
    ap.add_argument("--lock", default="/tmp/npz_bar_refresh.lock")
    ap.add_argument("--allow-mac", action="store_true", help="override the macOS refusal (debugging only; live NPZs exist on S1 only)")
    a = ap.parse_args()
    if sys.platform == "darwin" and not a.allow_mac and (a.loop or not a.dry_run):
        ap.error("macOS refused: the refresh loop and live installs run on S1 only (Mac holds no live NPZs); pass --allow-mac to override")
    lk = open(a.lock, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("locked (previous run active)")
        return
    global _LOCK_FD
    _LOCK_FD = lk.fileno()
    w15 = a.w15 if a.w15 is not None else (2500 if a.loop else 0)
    if a.loop:
        run_loop(a, w15)
        return
    try:
        summ = one_cycle(a, w15, {"last_catchup": 0.0}, False)
    except _Shutdown:
        log("shutdown on SIGTERM (partial one-shot stamped)")
        return
    if a.dry_run:
        log(f"dry-run summary: {json.dumps(summ, default=str)}")


if __name__ == "__main__":
    main()
