#!/usr/bin/env python3
"""vec_live_executor — X1 of the VEC-DRIVEN LIVE architecture (USER 2026-10-06).

"PARITY = THE SECOND A VECTORIZED TRADE WOULD OCCUR A LIVE TRADE OCCURS."

Runs on S1 (~/binance-sandbox). Each cycle, for every sym_side listed in
data/vec_live/vec_driven.json (mode/account registry, pure_board-owned) whose
proven set resolves from data/vec_live/vec_driven_sets.json (--build-driven owned)
and whose NPZ gained a new completed 15m bar, it runs
the SAME vectorized engine that fills the sheets (v12_quick_engine.simulate_one
via tools/opt/v12_pilot.evaluate_prepared_sanitized, include_ledger=True) with
that sym_side's PROVEN override set, takes the ledger events whose ts == the
just-closed bar's ts (FINAL_MTM dropped: end-of-run artefact) and the engine's
position state at that bar, and writes intents + target state. It places NO
orders — the live consumer (ez_manage execute_now) reconciles target state.

Window: rolling 30D per sym_side, recalculated from the newest completed NPZ
row every cycle (30 calendar days crypto, 30 latest trading sessions stocks).
The merged NPZ plus hot sidecar is loaded, causally aligned, and compacted to
completed 15m rows before the current settings are replayed.

Usage:
  .venv/bin/python tools/vec_live_executor.py --once            # cron (polls ~50 s)
  .venv/bin/python tools/vec_live_executor.py --once --no-poll  # single pass
  .venv/bin/python tools/vec_live_executor.py --init-example    # write example vec_driven.json
"""

from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCOPE = os.environ.get("VEC_LIVE_SCOPE", "30d").lower()
if SCOPE not in ("30d", "7d"):
    raise ValueError(f"Invalid VEC_LIVE_SCOPE={SCOPE!r}")
LIVE_DIR_NAME = "vec_live_7d" if SCOPE == "7d" else "vec_live"
VL = ROOT / "data" / LIVE_DIR_NAME
INTENTS = VL / "intents"
STATE = VL / "state"
ANCHORS = VL / "anchors"
EMITTED = VL / "emitted_ids.txt"
SHADOW_LEDGER = VL / "shadow_ledger.jsonl"
INTENT_LEDGER = VL / "intent_ledger.jsonl"
CYCLE_LOG = VL / "cycle_log.jsonl"
VEC_DRIVEN = VL / "vec_driven.json"
SETS = VL / "vec_driven_sets.json"
CANDIDATES = VL / "candidates.json"
PUBLISH_DIR = Path(os.path.expanduser(f"~/binance/data/{LIVE_DIR_NAME}"))
STALE_SEC = 20 * 60
BAR_SEC = int(float(os.environ.get("VEC_LANE_BAR_SECONDS") or 900))


def is_bar_stale(bar_ts, now_s):
    """True when the bar CLOSED more than STALE_SEC ago.

    Bars are labeled by OPEN; a 15m bar is current until bar_ts + BAR_SEC.
    (2026-10-25: ageing from the label marked every state STALE ~5 min after
    its NPZ tail landed, actionable=False, and the Mac consumer skipped it.)
    """
    try:
        return (float(now_s) - (float(bar_ts) + BAR_SEC)) > STALE_SEC
    except (TypeError, ValueError):
        return True


MIN_BARS = 100
ENGINE_FILES = [
    "tools/vec_live_executor.py",
    "v12_quick_engine.py",
    "tools/opt/evaluate_v12.py",
    "tools/opt/v12_pilot.py",
    "tools/opt/lifecycle_pilot.py",
    "min_decision_tf_guard.py",
    "cat_side_defaults.py",
    "data/per_sym_settings.json",
    "data/sweep_cat_overrides.json",
    "data/switch_dependencies.json",
]


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _sec(x) -> float:
    x = float(x)
    return x / 1000.0 if x > 1e11 else x


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    os.replace(tmp, path)


def _append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(obj, sort_keys=True, default=str) + "\n")


def engine_md5() -> dict:
    files = list(ENGINE_FILES) + sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "vec_decisions").glob("*.py")
    )
    per, h = {}, hashlib.md5()
    for rel in files:
        p = ROOT / rel
        d = hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else "MISSING"
        per[rel] = d
        h.update(f"{rel}:{d}\n".encode())
    return {
        "combined": h.hexdigest(),
        "v12_quick_engine.py": per.get("v12_quick_engine.py"),
        "files": per,
    }


def set_md5(overrides: dict) -> str:
    return hashlib.md5(
        json.dumps(overrides, sort_keys=True, default=str).encode()
    ).hexdigest()


def split_ss(ss: str):
    sym, side = ss.rsplit("_", 1)
    return sym, side.upper()


def npz_path(sym: str) -> Path:
    overlay_dir = ROOT / "data" / "vec_live" / "forward_npz"
    try:
        manifest_path = overlay_dir / "manifest.json"
        if time.time() - manifest_path.stat().st_mtime <= 300:
            manifest = json.loads(manifest_path.read_text())
            if time.time() - float(manifest["generated_ts"]) <= 300 and sym in manifest["symbols"]:
                overlay = overlay_dir / f"{sym}.npz"
                if overlay.is_file():
                    return overlay
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for prefix in ("backtest_v8", "backtest_v7"):
        p = ROOT / prefix / "indicators" / f"{sym}.npz"
        if p.exists():
            return p
    return ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"


def _load_store(sym: str):
    """Merged store (zip + sidecar tail) so hot rows are visible before rollup; zip-only fallback."""
    try:
        from npz_tail_store import load_merged

        return dict(load_merged(str(npz_path(sym))))
    except Exception:
        import numpy as np

        with np.load(str(npz_path(sym)), allow_pickle=True) as z:
            return {k: np.asarray(z[k]) for k in z.files}


def _load_timestamps(sym: str):
    import numpy as np

    path = npz_path(sym)
    with np.load(str(path), allow_pickle=True) as z:
        base = np.asarray(z["timestamps"], dtype="float64")
        parent = np.asarray(z["timestamp_15m"], dtype="float64") if "timestamp_15m" in z.files else base
    try:
        from npz_tail_store import tail_rows

        rows = tail_rows(str(path))
    except Exception:
        rows = []
    if rows:
        zip_last = _sec(base[-1])
        rows = sorted((r for r in rows if _sec(r.get("timestamps") or 0) > zip_last), key=lambda r: _sec(r["timestamps"]))
        rows = [r for i, r in enumerate(rows) if i == 0 or _sec(r["timestamps"]) > _sec(rows[i - 1]["timestamps"])]
        if rows:
            base = np.concatenate((base, np.asarray([r["timestamps"] for r in rows], dtype="float64")))
            parent = np.concatenate((parent, np.asarray([r.get("timestamp_15m", r["timestamps"]) for r in rows], dtype="float64")))
    return {"timestamps": base, "timestamp_15m": parent}


def npz_last_parent(sym: str):
    """Cheap new-bar probe: last completed-15m parent ts + last base ts (lazy member read)."""
    import numpy as np

    z = _load_timestamps(sym)
    par = np.asarray(z["timestamp_15m"], dtype="float64")
    base = np.asarray(z["timestamps"], dtype="float64")
    ok = np.flatnonzero(np.isfinite(par) & (par > 0))
    return (_sec(par[ok[-1]]) if ok.size else 0.0), _sec(base[-1])


def load_raw(sym: str, load_start_ts: float):
    """Raw NPZ arrays from load_start_ts (pre-alignment), sliced as v12_quick_engine.load_npz does."""
    import numpy as np

    z = _load_store(sym)
    keys = list(z.keys())
    ts = np.asarray(z["timestamps"])
    scale = 1000.0 if float(ts[-1]) > 1e11 else 1.0
    start_idx = int(np.searchsorted(ts, load_start_ts * scale))
    raw = {}
    for k in keys:
        v = np.asarray(z[k])
        if getattr(v, "ndim", 0) > 0 and len(v) > start_idx:
            raw[k] = v[start_idx:].copy() if start_idx else v
        else:
            raw[k] = v
    return raw


def truncate_raw(raw: dict, end_ts: float) -> dict:
    """Simulate 'the NPZ as it landed when bar end_ts closed': drop every base row after end_ts."""
    import numpy as np

    ts = np.asarray(raw["timestamps"], dtype="float64")
    scale = 1000.0 if float(ts[-1]) > 1e11 else 1.0
    right = int(np.searchsorted(ts, end_ts * scale, side="right"))
    n = len(ts)
    return {
        k: (v[:right] if isinstance(v, np.ndarray) and v.ndim and len(v) == n else v)
        for k, v in raw.items()
    }


def build_npz(raw: dict, sym: str, mode: str, anchor_ts: float):
    """Align (causal HTF) -> slice from rolling anchor -> compact to completed 15m."""
    import numpy as np
    from vec_decisions.htf_causal_align import align_store
    from tools.opt import evaluate_v12 as E

    st = align_store(dict(raw), sym, mode)
    ts = np.asarray(st["timestamps"], dtype="float64")
    scale = 1000.0 if float(ts[-1]) > 1e11 else 1.0
    left = int(np.searchsorted(ts, anchor_ts * scale, side="left"))
    n = len(ts)
    sliced = {
        k: (v[left:] if isinstance(v, np.ndarray) and v.ndim and len(v) == n else v)
        for k, v in st.items()
    }
    return E._compact_to_15m(sliced)


_BASE_PREP = {}
_BASE_PREP_FINGERPRINT = {}
_WINDOW_NPZ_CACHE = OrderedDict()
_WINDOW_NPZ_CACHE_MAX = 128
_WINDOW_NPZ_CACHE_LOCK = threading.Lock()
_WINDOW_NPZ_BUILDING = {}


def _store_fingerprint(sym: str):
    path = npz_path(sym)
    parts = []
    for candidate in (path, path.parent / ".tail" / f"{sym}.jsonl"):
        try:
            st = candidate.stat()
            parts.append((str(candidate), st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((str(candidate), 0, 0))
    return tuple(parts)


def _tail_prefix_md5(rows, end_ts):
    prefix = [row for row in rows if _sec(row.get("timestamps") or 0) <= end_ts]
    return hashlib.md5(json.dumps(prefix, sort_keys=True, default=str).encode()).hexdigest()



def _prep_source_fingerprint(sym: str):
    """Fingerprint ZIP and sidecar content so a stale prepare can be retried after repair."""
    import hashlib

    path = npz_path(sym)
    digest = hashlib.blake2b(digest_size=16)
    for candidate in (path, path.parent / ".tail" / f"{sym}.jsonl"):
        try:
            stat = candidate.stat()
            digest.update(f"{candidate.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
            with candidate.open("rb") as source:
                digest.update(source.read(4096))
                source.seek(max(0, stat.st_size - 65536))
                digest.update(source.read())
        except OSError:
            digest.update(f"{candidate.name}:MISSING".encode())
    return digest.hexdigest()


def _cache_window(key, entry):
    with _WINDOW_NPZ_CACHE_LOCK:
        for previous in list(_WINDOW_NPZ_CACHE):
            if previous[0] == key[0] and previous != key:
                _WINDOW_NPZ_CACHE.pop(previous)
        _WINDOW_NPZ_CACHE[key] = entry
        _WINDOW_NPZ_CACHE.move_to_end(key)
        while len(_WINDOW_NPZ_CACHE) > _WINDOW_NPZ_CACHE_MAX:
            _WINDOW_NPZ_CACHE.popitem(last=False)


def _window_npz(sym: str, anchor: dict, end_ts: float = None):
    target_end = float(anchor.get("npz_end_ts") or end_ts or 0.0)
    fingerprint = _store_fingerprint(sym)
    key = (
        sym,
        fingerprint,
        float(anchor["anchor_ts"]),
        float(anchor["load_start_ts"]),
        None if end_ts is None else float(end_ts),
    )
    while True:
        with _WINDOW_NPZ_CACHE_LOCK:
            cached = _WINDOW_NPZ_CACHE.get(key)
            if cached is not None:
                _WINDOW_NPZ_CACHE.move_to_end(key)
                return dict(cached["npz"])
            ready = _WINDOW_NPZ_BUILDING.get(key)
            if ready is None:
                ready = threading.Event()
                _WINDOW_NPZ_BUILDING[key] = ready
                build_here = True
            else:
                build_here = False
        if not build_here:
            ready.wait()
            continue
        try:
            extension = None
            if target_end:
                with _WINDOW_NPZ_CACHE_LOCK:
                    for old_key, old in reversed(_WINDOW_NPZ_CACHE.items()):
                        if old_key[0] != sym or old.get("mode") != anchor["mode"] or old_key[1][0] != fingerprint[0]:
                            continue
                        delta = target_end - float(old.get("end_ts") or 0.0)
                        if 0 < delta <= 4 * 900 and int(old.get("steps") or 0) < 4:
                            extension = (old_key, old, delta)
                            break
            if extension is not None:
                from npz_tail_store import _apply_row_in_memory, tail_rows

                _, old, delta = extension
                expected = int(round(delta / 900.0))
                current_tail = tail_rows(str(npz_path(sym)))
                unchanged_prefix = old.get("tail_prefix_md5") == _tail_prefix_md5(current_tail, float(old["end_ts"]))
                corrected_cached_bar = any(row.get('_replace_latest') and float(row.get('timestamps') or 0) <= float(old['end_ts']) for row in current_tail)
                rows = [
                    row
                    for row in current_tail
                    if float(row.get("timestamps") or 0.0) > float(old["end_ts"])
                    and float(row.get("timestamps") or 0.0) <= target_end
                ]
                rows.sort(key=lambda row: float(row["timestamps"]))
                if unchanged_prefix and not corrected_cached_bar and len(rows) == expected and all(
                    abs(float(row["timestamps"]) - (float(old["end_ts"]) + 900 * (i + 1))) < 1
                    for i, row in enumerate(rows)
                ):
                    built = old["npz"]
                    for row in rows:
                        built = _apply_row_in_memory(built, row)
                        if built is None:
                            break
                    if built is not None and abs(float(built["timestamps"][-1]) - target_end) < 1:
                        entry = {
                            "npz": built,
                            "mode": anchor["mode"],
                            "end_ts": target_end,
                            "steps": int(old.get("steps") or 0) + expected,
                            "tail_prefix_md5": _tail_prefix_md5(current_tail, target_end),
                        }
                        _cache_window(key, entry)
                        return dict(built)
            raw = load_raw(sym, anchor["load_start_ts"])
            if end_ts is not None:
                raw = truncate_raw(raw, end_ts)
            built = build_npz(raw, sym, anchor["mode"], anchor["anchor_ts"])
            if built is None:
                return None
            entry = {
                "npz": built,
                "mode": anchor["mode"],
                "end_ts": target_end or float(built["timestamps"][-1]),
                "steps": 0,
            }
            from npz_tail_store import tail_rows
            entry["tail_prefix_md5"] = _tail_prefix_md5(tail_rows(str(npz_path(sym))), float(entry["end_ts"]))
            _cache_window(key, entry)
            return dict(built)
        finally:
            with _WINDOW_NPZ_CACHE_LOCK:
                ready = _WINDOW_NPZ_BUILDING.pop(key, None)
                if ready is not None:
                    ready.set()


def base_prepared(ss: str):
    """Canonical prepare() (base cfg incl. tradier/cat_side/sweep-cat layers, account, MIN_POSITION_SIZE)."""
    sym, _ = split_ss(ss)
    fingerprint = _prep_source_fingerprint(sym)
    cached = _BASE_PREP.get(ss)
    if cached is not None and _BASE_PREP_FINGERPRINT.get(ss) == fingerprint:
        return cached
    import pickle

    emd5 = os.environ.get("VEC_LIVE_ENGINE_MD5", "")
    cp = VL / "cache" / f"{ss}_{emd5}.pkl"
    if emd5 and cp.exists():
        payload = pickle.loads(cp.read_bytes())
        if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == fingerprint:
            _BASE_PREP[ss], _BASE_PREP_FINGERPRINT[ss] = payload[1], fingerprint
            return payload[1]
    from tools.opt import evaluate_v12 as E
    prep = E.prepare(ss, 30)
    if prep is not None:
        prep = {k: v for k, v in prep.items() if k not in ("npz_prepared", "bh")}
        if emd5:
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = cp.with_suffix(".tmp%d" % os.getpid())
            tmp.write_bytes(pickle.dumps((fingerprint, prep)))
            os.replace(tmp, cp)
    _BASE_PREP[ss], _BASE_PREP_FINGERPRINT[ss] = prep, fingerprint
    return prep


def canonical_anchor(ss: str) -> dict:
    """Return the rolling 30D window start from the latest merged NPZ row."""
    import numpy as np
    from tools.opt import evaluate_v12 as E

    sym, side = split_ss(ss)
    mode = "crypto" if E.is_crypto(sym) else "tradier"
    z = _load_timestamps(sym)
    ts = np.asarray(z["timestamps"], dtype="float64")
    fin = ts[np.isfinite(ts) & (ts > 0)]
    sec = fin / (1000.0 if float(fin[-1]) > 1e11 else 1.0)
    end_s = float(sec[-1])
    load_days = 30 if mode == "crypto" else 90
    ls = end_s - load_days * 86400.0
    load_start = (
        dt.datetime.fromtimestamp(ls, tz=dt.timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    if mode == "crypto":
        anchor = end_s - 30 * 86400.0
    else:
        days = sec.astype("datetime64[s]").astype("datetime64[D]")
        uniq = np.unique(days)
        anchor = (
            float(uniq[-30].astype("datetime64[s]").astype("int64"))
            if len(uniq) >= 30
            else float(sec[0])
        )
    return {
        "anchor_ts": float(anchor),
        "load_start_ts": float(min(load_start, anchor)),
        "mode": mode,
        "npz_end_ts": end_s,
        "window_policy": "30_calendar_days" if mode == "crypto" else "30_trading_sessions",
    }


def run_engine(
    ss: str, overrides: dict, anchor: dict, end_ts: float = None, raw: dict = None
):
    """Replay the rolling window through end_ts (None = latest merged row)."""
    from tools.opt import v12_pilot as P
    from tools.opt import evaluate_v12 as E

    sym, side = split_ss(ss)
    mode = anchor["mode"]
    base = base_prepared(ss)
    if base is None:
        return {"valid": False, "invalid_reason": "prepare failed"}, None
    if isinstance(base, dict) and base.get("_stale"):
        return {"valid": False, "invalid_reason": str(base["_stale"])}, None
    if raw is None:
        npz = _window_npz(sym, anchor, end_ts)
    else:
        if end_ts is not None:
            raw = truncate_raw(raw, end_ts)
        npz = build_npz(raw, sym, mode, anchor["anchor_ts"])
    if npz is None:
        return {"valid": False, "invalid_reason": "NPZ window build failed"}, None
    prep = dict(base)
    prep["npz_prepared"] = npz
    prep["bh"] = E._bh(npz, side)
    res = P.evaluate_prepared_sanitized(prep, dict(overrides), 30, include_ledger=True)
    return res, npz


def replay_state(ledger, upto_ts: float = None):
    """Position qty/side/deployed after every ledger row with ts <= upto_ts (FINAL_MTM ignored)."""
    qty, dep, entry_ts, entry_px, entry_reason = 0.0, 0.0, None, None, None
    for e in ledger or []:
        if not isinstance(e, dict):
            continue
        t = _sec(e.get("ts") or 0.0)
        if upto_ts is not None and t > upto_ts + 1e-6:
            break
        typ, reason = e.get("type"), str(e.get("reason") or "")
        if reason == "FINAL_MTM":
            continue
        q = float(e.get("qty") or 0.0)
        if typ == "OPEN":
            qty, dep, entry_ts, entry_px, entry_reason = (
                q,
                float(e.get("pos_deployed") or 0.0),
                t,
                float(e.get("price") or 0.0),
                reason,
            )
        elif typ == "AUGMENT":
            qty += q
            dep = float(e.get("pos_deployed") or dep)
        elif typ == "REDUCE" and "pnl_dollars" not in e:
            qty = max(0.0, qty - q)
        elif typ == "CLOSE" or (typ == "REDUCE" and "pnl_dollars" in e):
            qty, dep, entry_ts, entry_px, entry_reason = 0.0, 0.0, None, None, None
    return {
        "qty": qty,
        "deployed": dep,
        "entry_ts": entry_ts,
        "entry_price": entry_px,
        "entry_reason": entry_reason,
    }


def replay_computed(res: dict) -> bool:
    """Allow completed ledger state despite promotion-only metric-floor failures."""
    if not isinstance(res, dict) or res.get("execution_ledger") is None:
        return False
    if res.get("valid") is True:
        return True
    reason = str(res.get("invalid_reason") or "")
    return reason.startswith(("trades ", "TIM ", "DD "))


def events_at(ledger, bar_ts: float):
    """Ledger rows that execute ON bar_ts (FINAL_MTM dropped; REDUCE trade-row deduped against its event row)."""
    rows = [
        e
        for e in (ledger or [])
        if isinstance(e, dict)
        and abs(_sec(e.get("ts") or 0.0) - bar_ts) < 1e-6
        and str(e.get("reason") or "") != "FINAL_MTM"
    ]
    red_ev = any(e.get("type") == "REDUCE" and "pnl_dollars" not in e for e in rows)
    out = []
    for e in rows:
        if e.get("type") == "REDUCE" and "pnl_dollars" in e and red_ev:
            continue
        out.append(e)
    return out


def decide(ss: str, res: dict, npz, bar_ts: float, side: str, base_size: float):
    """Turn the engine result into (intents_raw, target_state) for bar_ts."""
    import numpy as np

    led = res.get("execution_ledger") or res.get("ledger") or []
    before = replay_state([e for e in led if _sec(e.get("ts") or 0) < bar_ts - 1e-6])
    after = replay_state(led, bar_ts)
    ts = np.asarray(npz["timestamps"], dtype="float64")
    close = np.asarray(npz["close"], dtype="float64")
    idx = int(
        np.searchsorted(ts / (1000.0 if float(ts[-1]) > 1e11 else 1.0), bar_ts - 1e-6)
    )
    px_last = float(close[min(idx, len(close) - 1)])
    raw_events = []
    q_run = before["qty"]
    for seq, e in enumerate(events_at(led, bar_ts)):
        typ = e.get("type")
        q = float(e.get("qty") or 0.0)
        if typ == "OPEN":
            frac, q_run = 1.0, q
        elif typ == "AUGMENT":
            frac = (q / q_run) if q_run > 0 else None
            q_run += q
        elif typ == "REDUCE" and "pnl_dollars" not in e:
            frac = (q / q_run) if q_run > 0 else None
            q_run = max(0.0, q_run - q)
        else:
            frac, q_run = 1.0, 0.0
        raw_events.append(
            {
                "seq": seq,
                "type": "CLOSE" if typ == "REDUCE" and "pnl_dollars" in e else typ,
                "engine_type": typ,
                "reason": str(e.get("reason") or ""),
                "price_ref": float(e.get("price") or px_last),
                "qty_units": q,
                "qty_frac": frac,
                "pos_deployed": e.get("pos_deployed"),
                "pnl_pct": e.get("pnl_pct"),
            }
        )
    final = [e for e in led if str(e.get("reason") or "") == "FINAL_MTM"]
    side_s = ("long" if side == "LONG" else "short") if after["qty"] > 1e-12 else "flat"
    tgt = {
        "side": side_s,
        "qty_units": after["qty"],
        "notional_ref": after["qty"] * px_last,
        "base_size": base_size,
        "size_rel": (after["qty"] * px_last / base_size) if base_size else None,
        "deployed_cum": after["deployed"],
        "entry_ts": after["entry_ts"],
        "entry_price": after["entry_price"],
        "entry_reason": after["entry_reason"],
        "price_ref": px_last,
    }
    consistent = True
    why = ""
    if final:
        fq = float(final[-1].get("qty") or 0.0)
        if abs(fq - after["qty"]) > 1e-6 * max(1.0, fq):
            consistent, why = False, f"replay qty {after['qty']} != FINAL_MTM qty {fq}"
    elif after["qty"] > 1e-12:
        consistent, why = (
            False,
            f"replay says open qty {after['qty']} but engine has no FINAL_MTM (spike-filtered row?)",
        )
    return raw_events, tgt, consistent, why


def intent_id(ss: str, bar_ts: float, typ: str, seq: int) -> str:
    return hashlib.sha256(
        f"{ss}|{int(round(bar_ts))}|{typ}|{seq}".encode()
    ).hexdigest()[:24]


def load_emitted() -> set:
    try:
        return set(EMITTED.read_text().split())
    except OSError:
        return set()


def get_anchor(ss: str) -> dict:
    p = ANCHORS / f"{ss}.json"
    if p.exists():
        return json.loads(p.read_text())
    a = canonical_anchor(ss)
    a.update(
        {
            "set_at": _now(),
            "set_at_iso": _iso(_now()),
            "reason": "INITIAL (canonical 30D window start at first cycle)",
            "history": [],
        }
    )
    _atomic_json(p, a)
    return a


def maybe_reanchor(
    ss: str, overrides: dict, anchor: dict, tgt: dict, n_events: int, bar_ts: float
):
    """Daily re-anchor at a flat moment; accepted only if the new-anchor run is also flat with no event on the bar."""
    if (
        _now() - float(anchor.get("set_at") or 0) < REANCHOR_AGE_SEC
        or tgt["side"] != "flat"
        or n_events
    ):
        return anchor, None
    new = canonical_anchor(ss)
    if new["anchor_ts"] <= anchor["anchor_ts"] + 3600:
        return anchor, None
    res2, npz2 = run_engine(ss, overrides, new)
    if (
        not res2.get("execution_ledger")
        and res2.get("invalid_reason")
        and "eval" in str(res2.get("invalid_reason"))
    ):
        return anchor, f"reanchor skipped: {res2.get('invalid_reason')}"
    ev2, tgt2, ok2, _ = decide(
        ss, res2, npz2, bar_ts, split_ss(ss)[1], tgt["base_size"]
    )
    if tgt2["side"] != "flat" or ev2 or not ok2:
        return anchor, "reanchor deferred: new-anchor run not flat/quiet on this bar"
    hist = list(anchor.get("history") or [])
    hist.append(
        {
            k: anchor.get(k)
            for k in ("anchor_ts", "load_start_ts", "set_at_iso", "reason")
        }
    )
    new.update(
        {
            "set_at": _now(),
            "set_at_iso": _iso(_now()),
            "reason": f"DAILY_FLAT_REANCHOR at bar {_iso(bar_ts)}",
            "history": hist[-30:],
        }
    )
    _atomic_json(ANCHORS / f"{ss}.json", new)
    return new, "reanchored"


def process(
    ss: str, entry: dict, overrides: dict, emd5: str, force: bool = False, engine_source_md5: str = None
) -> dict:
    t0 = _now()
    sym, side = split_ss(ss)
    mode_flag = str(entry.get("mode") or "shadow").lower()
    st_path = STATE / f"{ss}.json"
    prev = json.loads(st_path.read_text()) if st_path.exists() else {}
    rec = {"ss": ss, "mode": mode_flag, "t_start": t0}
    if entry.get("set_source") == "missing_per_sym_active":
        state = {"ss": ss, "mode": mode_flag, "account": entry.get("account"), "status": "NO_ACTIVE_SET", "actionable": False, "why": "no selected per_sym_active full configuration", "target_state": {"side": "unknown"}, "engine_md5": emd5, "set_md5": set_md5(overrides), "written_at": t0, "written_iso": _iso(t0)}
        if prev.get("status") != "NO_ACTIVE_SET" or prev.get("engine_md5") != emd5:
            _atomic_json(st_path, state)
        rec.update({"status": "NO_ACTIVE_SET", "why": state["why"]})
        return rec
    fab = _fabricated(overrides)
    if fab:
        if prev.get("status") != "FABRICATION_SET":
            _atomic_json(
                st_path,
                {
                    "ss": ss,
                    "mode": mode_flag,
                    "status": "FABRICATION_SET",
                    "actionable": False,
                    "why": f"set carries fabrication-class test switch {fab} (BIBLE §19) — never driven",
                    "set_md5": set_md5(overrides),
                    "written_iso": _iso(_now()),
                },
            )
        rec.update({"status": "FABRICATION_SET", "why": fab})
        return rec
    try:
        par_ts, base_last = npz_last_parent(sym)
    except Exception as exc:
        rec.update({"status": "NO_NPZ", "error": f"{type(exc).__name__}: {exc}"})
        return rec
    try:
        npz_mtime = npz_path(sym).stat().st_mtime
    except OSError:
        npz_mtime = None
    rec.update(
        {
            "npz_parent_last": par_ts,
            "npz_ts_last": base_last,
            "npz_mtime": npz_mtime,
            "staleness_sec": round(t0 - base_last, 1),
        }
    )
    from tools.npz_cutoffs import is_crypto_symbol, is_rth_bar_start
    if not is_crypto_symbol(sym) and not is_rth_bar_start(par_ts):
        reason = f"stock bar {_iso(par_ts)} starts outside the NYSE regular session"
        _atomic_json(st_path, {"ss": ss, "mode": mode_flag, "account": entry.get("account"), "status": "OFF_SESSION_BAR", "actionable": False, "why": reason, "bar_ts": par_ts, "bar_iso": _iso(par_ts), "target_state": {"side": "unknown"}, "engine_md5": emd5, "v12_quick_engine_md5": engine_source_md5 or hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest(), "set_md5": set_md5(overrides), "npz_parent_last": par_ts, "npz_ts_last": base_last, "engine_valid": False, "written_at": _now(), "written_iso": _iso(_now())})
        rec.update({"status": "OFF_SESSION_BAR", "why": reason})
        return rec
    fingerprint = [list(part) for part in _store_fingerprint(sym)]
    if (
        not force
        and prev.get("npz_store_fingerprint") == fingerprint
        and prev.get("npz_parent_last") == par_ts
        and prev.get("set_md5") == set_md5(overrides)
        and prev.get("engine_md5") == emd5
    ):
        rec["status"] = "NO_NEW_BAR"
        return rec
    anchor = canonical_anchor(ss)
    t1 = _now()
    res, npz = run_engine(ss, overrides, anchor)
    t2 = _now()
    pre_err = (
        None if npz is not None else str(res.get("invalid_reason") or "prepare failed")
    )
    import numpy as np

    if npz is not None:
        ts = np.asarray(npz["timestamps"], dtype="float64")
        bar_ts = _sec(ts[-1])
        n_bars = len(ts)
    else:
        bar_ts, n_bars = base_last, 0
    # USER 2026-10-25: bars are labeled by OPEN; a bar is complete at bar_ts+BAR_SEC.
    # Ageing from the label stole 15 min from every bar: with a ~5 min NPZ tail the
    # state was born STALE (open-age >20 while close-age ~5), actionable=False, and
    # the Mac consumer skipped it — closes enforced, opens refused, book bled vs boards.
    stale = is_bar_stale(bar_ts, t0)
    _base = base_prepared(ss) or {}
    base_size = float(
        ((_base.get("base_cfg_dict") or {}).get("START_POSITION_SIZE")) or 0.0
    )
    status = "OK"
    why = ""
    if not is_crypto_symbol(sym) and not is_rth_bar_start(bar_ts):
        status, why = "OFF_SESSION_BAR", f"replay ends on stock bar {_iso(bar_ts)} outside the NYSE regular session"
    elif pre_err:
        status, why = "ENGINE_ERROR", pre_err
    elif res.get("execution_ledger") is None:
        status, why = "ENGINE_ERROR", str(res.get("invalid_reason") or "execution ledger missing")
    elif n_bars < MIN_BARS:
        status, why = "INSUFFICIENT_BARS", f"{n_bars} < {MIN_BARS} bars from anchor"
    elif not replay_computed(res):
        status, why = "INVALID_30D_REPLAY", str(res.get("invalid_reason") or "unverified 30D replay")
    raw_events, tgt, consistent, cwhy = (
        ([], {"side": "unknown"}, False, "")
        if status != "OK"
        else decide(ss, res, npz, bar_ts, side, base_size)
    )
    if status == "OK" and not consistent:
        status, why = "INCONSISTENT", cwhy
    if status == "OK" and stale:
        status, why = (
            "STALE",
            f"last bar {_iso(bar_ts)} closed {round((t0 - (bar_ts + BAR_SEC)) / 60.0, 1)} min ago (> {STALE_SEC // 60})",
        )
    actionable = status == "OK"
    smd5 = set_md5(overrides)
    emitted = []
    if actionable and raw_events:
        seen = load_emitted()
        stamp = dt.datetime.fromtimestamp(bar_ts, tz=dt.timezone.utc).strftime(
            "%Y%m%dT%H%M"
        )
        intents = []
        for ev in raw_events:
            iid = intent_id(ss, bar_ts, ev["type"], ev["seq"])
            if iid in seen:
                continue
            it = {
                "intent_id": iid,
                "ss": ss,
                "bar_ts": bar_ts,
                "bar_iso": _iso(bar_ts),
                "type": ev["type"],
                "engine_type": ev["engine_type"],
                "reason": ev["reason"],
                "target_state": tgt,
                "price_ref": ev["price_ref"],
                "qty_frac": ev["qty_frac"],
                "qty_units": ev["qty_units"],
                "pos_deployed": ev["pos_deployed"],
                "engine_md5": emd5,
                "set_md5": smd5,
                "npz_ts_last": base_last,
                "mode": mode_flag,
                "account": entry.get("account"),
                "emitted_at": _now(),
            }
            intents.append(it)
        if intents:
            doc = {
                "ss": ss,
                "bar_ts": bar_ts,
                "bar_iso": _iso(bar_ts),
                "mode": mode_flag,
                "intents": intents,
                "target_state": tgt,
            }
            fp = INTENTS / f"{stamp}_{ss}.json"
            _atomic_json(fp, doc)
            with open(EMITTED, "a") as fh:
                for it in intents:
                    fh.write(it["intent_id"] + "\n")
            for it in intents:
                _append_jsonl(INTENT_LEDGER, it)
                if mode_flag == "shadow":
                    _append_jsonl(SHADOW_LEDGER, it)
            emitted = [it["intent_id"] for it in intents]
            rec["intent_file"] = str(fp)
    extended_window = bool(npz is not None and _sec(npz["timestamps"][0]) < float(anchor["anchor_ts"]))
    if extended_window:
        anchor = dict(anchor, window_policy=anchor["window_policy"] + "_plus_up_to_4_appended_bars")
    reanchor_note = "cached window extended; rolling rebase after four appended bars" if extended_window else "rolling window refreshed from latest NPZ row"
    t3 = _now()
    state = {
        "effective_window_first_ts": _sec(npz["timestamps"][0]) if npz is not None else None,
        "npz_store_fingerprint": fingerprint,
        "ss": ss,
        "mode": mode_flag,
        "account": entry.get("account"),
        "status": status,
        "actionable": actionable,
        "why": why,
        "bar_ts": bar_ts,
        "bar_iso": _iso(bar_ts),
        "npz_parent_last": par_ts,
        "npz_ts_last": base_last,
        "staleness_sec": round(t0 - bar_ts, 1),
        "target_state": tgt,
        "events_this_bar": raw_events,
        "emitted": emitted,
        "engine_md5": emd5,
        "v12_quick_engine_md5": engine_source_md5 or hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest(),
        "set_md5": smd5,
        "anchor_ts": anchor["anchor_ts"],
        "anchor_iso": _iso(anchor["anchor_ts"]),
        "window_policy": anchor.get("window_policy"),
        "window_end_ts": anchor.get("npz_end_ts"),
        "bars_from_anchor": n_bars,
        "engine_valid": res.get("valid"),
        "performance_valid": res.get("valid") is True,
        "performance_invalid_reason": res.get("invalid_reason") if res.get("valid") is not True else None,
        "engine_invalid_reason": res.get("invalid_reason"),
        "run_gain_pct": res.get("gain_pct"),
        "run_trades": res.get("trades"),
        "justified_now": bool((res.get("gain_pct") or 0.0) > 0.0),
        "set_source": entry.get("set_source"),
        "set_tier": entry.get("set_tier"),
        "set_gain_30d_claimed": entry.get("set_gain_30d"),
        "spike_trades_dropped": res.get("spike_trades_dropped"),
        "reanchor": reanchor_note,
        "written_at": _now(),
        "written_iso": _iso(_now()),
        "timings_sec": {
            "probe": round(t1 - t0, 3),
            "engine": round(t2 - t1, 3),
            "decide_emit": round(t3 - t2, 3),
            "total": round(t3 - t0, 3),
        },
        "decision_latency_sec_vs_npz_mtime": (
            round(t3 - npz_mtime, 1) if npz_mtime else None
        ),
    }
    _atomic_json(st_path, state)
    rec.update(
        {
            "status": status,
            "why": why,
            "bar_iso": _iso(bar_ts),
            "events": len(raw_events),
            "emitted": len(emitted),
            "target": tgt.get("side"),
            "timings_sec": state["timings_sec"],
            "latency_vs_npz_mtime": state["decision_latency_sec_vs_npz_mtime"],
        }
    )
    return rec


def load_sets():
    vd = json.loads(VEC_DRIVEN.read_text()) if VEC_DRIVEN.exists() else {}
    cands = json.loads(CANDIDATES.read_text()) if CANDIDATES.exists() else {}
    if SCOPE == "30d":
        tradeable = ROOT / "tradeable_keys.json"
        account_sides = {}
        if tradeable.is_file():
            for key in json.loads(tradeable.read_text()):
                if ":" not in str(key):
                    continue
                account, symbol_side = str(key).split(":", 1)
                if symbol_side.endswith(("_LONG", "_SHORT")):
                    account_sides.setdefault(symbol_side, set()).add(account)
            for symbol_side, accounts in account_sides.items():
                vd.setdefault(symbol_side, {"mode": "live", "account": sorted(accounts)})
        held_path = VL / "held_sides.json"
        try:
            held_doc = json.loads(held_path.read_text())
            if _now() - float(held_doc["generated_ts"]) > 180:
                raise ValueError("held sides stale")
            eligible_crypto = (set(account_sides) | set(held_doc["held"])) if account_sides else None
        except (OSError, ValueError, KeyError, TypeError):
            eligible_crypto = None
        stock_sides = Path("/home/niels/fwd_stocks_syms.txt")
        if stock_sides.is_file():
            for symbol_side in stock_sides.read_text().split():
                if symbol_side.endswith(("_LONG", "_SHORT")):
                    vd.setdefault(symbol_side, {"mode": "live", "account": "trb"})
        canonical = Path("/home/niels/binance/data/hourly_reconfig/per_sym_store.db")
        replica = ROOT / "data" / "hourly_reconfig" / "per_sym_forward_master.db"
        local = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
        db = Path(os.environ["PER_SYM_STORE_DB"]) if os.environ.get("PER_SYM_STORE_DB") else (canonical if canonical.is_file() else replica if replica.is_file() else local)
        if not db.is_file():
            raise RuntimeError(f"current per-sym SQL store missing: {db}")
        from per_sym_live_set import _apply_caps
        from tools.npz_cutoffs import is_crypto_symbol
        result = {}
        cat_doc = {}
        try:
            cat_doc = json.loads((VL / "cat_side_full_defaults.json").read_text())
            if _now() - float((cat_doc.get("_meta") or {}).get("generated_ts") or 0) > 1800:
                cat_doc = {}
        except (OSError, ValueError, TypeError):
            cat_doc = {}
        connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=5)
        try:
            for symbol_side, entry in vd.items():
                if symbol_side.startswith("_") or not isinstance(entry, dict) or entry.get("mode") != "live":
                    continue
                symbol = symbol_side.rsplit("_", 1)[0]
                crypto = is_crypto_symbol(symbol)
                if crypto and eligible_crypto is not None and symbol_side not in eligible_crypto:
                    continue
                row = connection.execute("SELECT updated_at,full_config_json FROM per_sym_active WHERE sym_side=?", (symbol_side,)).fetchone()
                if row and row[1]:
                    full = json.loads(row[1])
                    source = "per_sym_store.active"
                    updated = row[0]
                else:
                    if crypto:
                        result[symbol_side] = ({"mode": "live", "account": entry.get("account") or "ez", "set_source": "missing_per_sym_active"}, {})
                        continue
                    side = symbol_side.rsplit("_", 1)[-1]
                    full = cat_doc.get(f"CRYPTO_{side}") if symbol_side.endswith(("USDT_LONG", "USDT_SHORT", "USDC_LONG", "USDC_SHORT")) else None
                    source = "cat_side_defaults.live"
                    updated = (cat_doc.get("_meta") or {}).get("generated_utc")
                if not isinstance(full, dict) or not full or _fabricated(full):
                    continue
                result[symbol_side] = ({"mode": "live", "account": entry.get("account") or "ez", "set_source": source, "settings_updated_at": updated}, _apply_caps(symbol_side, full))
        finally:
            connection.close()
        return result
    # USER 2026-10-25 forward-data speed: proven override sets live in vec_driven_sets.json, owned by
    # --build-driven. data/vec_live/vec_driven.json is owned by pure_board_trader (mode/account/expiry
    # registry — its merge strips everything else by 10-23 design), so sets kept there are stripped within
    # minutes and the lane starves (measured 23:33Z: 165/165 NO_SET, 0 acted). Sets-file first, legacy
    # sources as fallback. Mode/account still resolve from the registry (pure_board's authority).
    try:
        _sets_doc = json.loads(SETS.read_text())
        _sets = (
            {k: v for k, v in _sets_doc.items() if isinstance(v, dict)}
            if isinstance(_sets_doc, dict)
            else {}
        )
    except Exception:
        _sets = {}
    if SCOPE == "7d":
        # An expired or partially copied premarket stage must stop producing
        # actionable 7D state, even if a supervisor keeps this process alive.
        from tools.v15_preopen_7d_runtime import assert_vector_stage_ready
        from tools.account_comparison_books import side_allowed
        assert_vector_stage_ready(VL)
        # The 7D lane has no fallback to 30D candidates, progress, or the
        # ordinary registry. Every set must carry its own verified provenance.
        return {
            ss: ({"mode": "live", "account": se["account"]}, se["overrides"])
            for ss, se in _sets.items()
            if not ss.startswith("_")
            and se.get("mode") == "live"
            and se.get("verified") is True
            and se.get("window_days") == 7 and se.get("source") == "7d_optimized"
            and se.get("account") in ("fin", "trc")
            and "_" in ss
            and side_allowed(ROOT, se["account"], *ss.rsplit("_", 1))
            and isinstance(se.get("overrides"), dict)
            and isinstance(vd.get(ss), dict)
            and vd[ss].get("mode") == "live"
            and vd[ss].get("account") == se["account"]
        }
    out = {}
    for ss, entry in vd.items():
        if ss.startswith("_") or not isinstance(entry, dict):
            continue
        _se = _sets.get(ss)
        if (
            isinstance(_se, dict)
            and isinstance(_se.get("overrides"), dict)
            and _se.get("mode") == "live"
        ):
            out[ss] = (
                {
                    "mode": entry.get("mode") or "live",
                    "account": entry.get("account") or [],
                },
                _se.get("overrides"),
            )
            continue
        src = str(entry.get("overrides_src") or "candidates.json")
        if src == "candidates.json":
            ov = (cands.get(ss) or {}).get("overrides")
        elif src == "inline":
            ov = entry.get("overrides")
        else:
            p = Path(src) if src.startswith("/") else ROOT / src
            d = json.loads(p.read_text()) if p.exists() else {}
            ov = (d.get(ss) or {}).get("overrides") if ss in d else d.get("overrides")
        out[ss] = (entry, ov)
    # USER 2026-10-25 stock shards: keys proven live in the sets file but absent from the
    # registry (pure_board publishes crypto only) resolve from the sets entry itself —
    # mode/account/overrides all rule-derived from real boards, registry file untouched.
    for ss, se in _sets.items():
        if ss.startswith("_") or ss in out or not isinstance(se, dict):
            continue
        if se.get("mode") == "live" and isinstance(se.get("overrides"), dict):
            out[ss] = (
                {"mode": "live", "account": se.get("account") or "trb"},
                se.get("overrides"),
            )
    return out


def _worker(args):
    if len(args) == 5:
        ss, entry, ov, emd5, force = args
        engine_source_md5 = hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()
    else:
        ss, entry, ov, emd5, force, engine_source_md5 = args
    try:
        return process(ss, entry, ov, emd5, force, engine_source_md5)
    except Exception as exc:
        import traceback

        return {
            "ss": ss,
            "status": "EXCEPTION",
            "error": f"{type(exc).__name__}: {exc}",
            "tb": traceback.format_exc()[-1500:],
        }


def publish_side(ss: str, intent_file: str = None) -> None:
    for source, destination in ((STATE / f"{ss}.json", PUBLISH_DIR / "state" / f"{ss}.json"),):
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    if intent_file:
        source = Path(intent_file)
        if source.is_file():
            destination = PUBLISH_DIR / "intents" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def publish() -> None:
    try:
        PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        for sub in ("intents", "state"):
            src, dst = VL / sub, PUBLISH_DIR / sub
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.glob("*.json"):
                d = dst / f.name
                if not d.exists() or d.stat().st_mtime < f.stat().st_mtime:
                    shutil.copy2(f, d)
        for f in (
            "vec_driven.json",
            "intent_ledger.jsonl",
            "shadow_ledger.jsonl",
            "cycle_log.jsonl",
            "DESIGN.md",
        ):
            if (VL / f).exists():
                shutil.copy2(VL / f, PUBLISH_DIR / f)
    except OSError as exc:
        print(f"[vec_live] publish failed: {exc}", file=sys.stderr)


def cycle(workers: int, force: bool = False, only_sss=None) -> list:
    t0 = _now()
    for d in (INTENTS, STATE, ANCHORS):
        d.mkdir(parents=True, exist_ok=True)
    em = engine_md5()
    os.environ["VEC_LIVE_ENGINE_MD5"] = em["combined"]
    sets = load_sets()
    try:
        from tools import vec_shard as _shard

        _shard_name = _shard.active_shard()
    except Exception:
        _shard, _shard_name = None, "all"
    jobs, recs = [], []
    _skipped = 0
    for ss, (entry, ov) in sorted(sets.items()):
        if only_sss is not None and ss not in only_sss:
            continue
        if _shard is not None and not _shard.serves(ss, _shard_name):
            _skipped += 1
            continue
        if ov is None:
            recs.append({"ss": ss, "status": "NO_SET", "why": "no overrides resolved"})
            continue
        jobs.append((ss, entry, ov, em["combined"], force))
    if _skipped:
        print(
            "[vec_live] shard=%s served=%d skipped=%d"
            % (_shard_name, len(jobs), _skipped)
        )
    if workers > 1 and len(jobs) > 1:
        import multiprocessing as mp

        with mp.get_context("fork").Pool(min(workers, len(jobs))) as pool:
            recs += pool.map(_worker, jobs)
    else:
        recs += [_worker(j) for j in jobs]
    acted = [r for r in recs if r.get("status") != "NO_NEW_BAR"]
    if acted:
        _append_jsonl(
            CYCLE_LOG,
            {
                "t": t0,
                "iso": _iso(t0),
                "engine_md5": em["combined"],
                "v12_md5": em["v12_quick_engine.py"],
                "wall_sec": round(_now() - t0, 2),
                "recs": acted,
            },
        )
        publish()
    return recs


def _watch_stamp(path: Path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return 0, 0


def watch(workers: int, poll_sec: float = 1.0) -> int:
    """Recompute changed symbols as soon as merged NPZ tails or settings change."""
    import signal
    from npz_tail_store import tail_path_for_npz

    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    stamps = {}
    all_pending = True
    code = engine_md5()["combined"]
    while not stopped:
        if engine_md5()["combined"] != code:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        sets = load_sets()
        current_sets = {
            ss: hashlib.md5(json.dumps([entry, ov], sort_keys=True, default=str).encode()).hexdigest()
            for ss, (entry, ov) in sets.items()
        }
        changed = {
            ss for ss, digest in current_sets.items()
            if all_pending or stamps.get(("set", ss)) != digest
        }
        for ss, digest in current_sets.items():
            stamps[("set", ss)] = digest
        all_pending = False
        symbol_sides = {}
        for ss in sets:
            sym, _ = split_ss(ss)
            symbol_sides.setdefault(sym, []).append(ss)
        for sym, sides in symbol_sides.items():
            npz = npz_path(sym)
            tail = tail_path_for_npz(str(npz))
            stamp = (_watch_stamp(npz), _watch_stamp(Path(tail)))
            key = ("npz", sym)
            if stamps.get(key) != stamp:
                if key in stamps:
                    changed.update(sides)
                stamps[key] = stamp
        if changed:
            recs = cycle(workers, only_sss=changed)
            for rec in recs:
                if rec.get("status") != "NO_NEW_BAR":
                    print(json.dumps({k: rec.get(k) for k in ("ss", "status", "bar_iso", "events", "emitted", "target", "timings_sec", "error")}, default=str), flush=True)
        time.sleep(max(0.2, float(poll_sec)))
    return 0


REPAIR_DUAL_DIR = Path(os.path.expanduser("~/v15_repair_dual_20261006"))
PROGRESS_GLOBS = [
    str(ROOT / "data" / "reports" / "lifecycle_pilot" / "*_v14_progress.json"),
    os.path.expanduser("~/v15_run2*/progress/*_v14_progress.json"),
]


def _universe() -> tuple:
    # USER 2026-10-25 forward-data speed: tradeable_keys.json is the live authority both lanes share
    # (mirrors pure_board_trader.universe crypto filter). The books snapshot (Oct 6) missed the majors
    # (BTC/ETH/SOL/BLUR), so the lane evaluated a stale subset. Books kept as fallback only.
    try:
        tks = json.loads((ROOT / "tradeable_keys.json").read_text())
        if isinstance(tks, list) and tks:
            bases = sorted(
                {
                    str(k).split(":", 1)[-1].rsplit("_", 1)[0]
                    for k in tks
                    if ":" in str(k)
                    and str(k)
                    .split(":", 1)[-1]
                    .rsplit("_", 1)[0]
                    .endswith(
                        ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
                    )
                }
            )
            if bases:
                return (
                    bases,
                    "tradeable_keys.json",
                    hashlib.md5(
                        (ROOT / "tradeable_keys.json").read_bytes()
                    ).hexdigest(),
                )
    except Exception:
        pass
    for p in (
        VL / "books" / "symbols_active.json",
        Path(os.path.expanduser("~/binance/symbols_active.json")),
        ROOT / "symbols_active.json",
    ):
        if p.exists():
            d = json.loads(p.read_text())
            syms = d if isinstance(d, list) else list(d)
            return (
                [s for s in syms if str(s).endswith(("USDT", "USDC"))],
                str(p),
                hashlib.md5(p.read_bytes()).hexdigest(),
            )
    return [], None, None


FABRICATION_TRUE_SWITCHES = ("SIMPLE_PRICE_GT0_ENABLED",)


def _fabricated(overrides: dict):
    """Fabrication-class test switches (BIBLE §19) a driven set must never carry ON; returns the first hit or None."""
    for k in FABRICATION_TRUE_SWITCHES:
        v = (overrides or {}).get(k)
        if (
            v is True
            or (isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0)
            or (isinstance(v, str) and v.strip().lower() in ("true", "1", "on", "yes"))
        ):
            return f"{k}={v}"
    return None


def build_driven(write: bool = True) -> dict:
    """USER 2026-10-06: every tradeable crypto key with a proven vectorized set is mode=live.
    USER 2026-10-25 stock shards: proven STOCK keys join under the SAME tier rules
    (gain > 0, fabrication screen) — universe = symbols_trb_long/short; set account = trb.

    Per key, preference: both-window (30D + 365D) qualified set > 30D-best set; within a tier
    repair_dual (engine-verified, accepted) > go-live candidates > latest progress cumulative_overrides.
    A 30D set is used only when its proven gain > 0. Reads the universe/books, never edits them.
    """
    import glob

    syms, upath, umd5 = _universe()
    try:
        _tsyms = set()
        for _tf in (ROOT / "symbols_trb_long.json", ROOT / "symbols_trb_short.json"):
            try:
                _d = json.loads(_tf.read_text())
                _tsyms.update(
                    str(s).strip().upper()
                    for s in (_d if isinstance(_d, list) else list(_d))
                )
            except Exception:
                pass
        _tsyms = {
            s
            for s in _tsyms
            if s
            and not s.endswith(
                ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
            )
        }
        if _tsyms:
            syms = sorted(set(syms) | _tsyms)
    except Exception:
        pass
    cands = json.loads(CANDIDATES.read_text()) if CANDIDATES.exists() else {}
    repair = {}
    for f in REPAIR_DUAL_DIR.glob("*_*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        ss = f.stem
        if d.get("accepted") and isinstance(d.get("best_overrides"), dict):
            after = d.get("after") or {}
            q365 = None
            try:
                for line in (
                    (REPAIR_DUAL_DIR / "summary.jsonl").read_text().splitlines()
                ):
                    r = json.loads(line)
                    if r.get("symside") == ss:
                        q365 = bool(r.get("q365_after"))
            except Exception:
                pass
            repair[ss] = {
                "overrides": d["best_overrides"],
                "gain_30d": after.get("gain"),
                "trades": after.get("trades"),
                "valid": after.get("valid"),
                "q365": q365,
                "src": str(f),
            }
    prog = {}
    for f in sorted(set(sum((glob.glob(g) for g in PROGRESS_GLOBS), []))):
        try:
            d = json.loads(Path(f).read_text())
        except Exception:
            continue
        ss = d.get("symside") or Path(f).name.replace("_v14_progress.json", "")
        ov = d.get("cumulative_overrides")
        if not isinstance(ov, dict) or not ov:
            continue
        g = d.get("final_gain", d.get("cumulative_gain"))
        when = (
            str(d.get("result_done_utc") or "")
            or dt.datetime.fromtimestamp(
                os.path.getmtime(f), tz=dt.timezone.utc
            ).isoformat()
        )
        if ss not in prog or when > prog[ss]["when"]:
            prog[ss] = {
                "overrides": ov,
                "gain_30d": g,
                "trades": d.get("final_trades"),
                "when": when,
                "src": f,
                "engine_md5": d.get("result_engine_md5"),
            }
    prev = json.loads(VEC_DRIVEN.read_text()) if VEC_DRIVEN.exists() else {}
    out = {
        "_schema": {
            "SYMBOL_SIDE": {
                "mode": "live|shadow",
                "account": "ez",
                "overrides_src": "inline",
                "set_source": "repair_dual|candidates|progress",
                "set_tier": "BOTH_WINDOW|30D_BEST",
                "set_md5": "md5 of overrides",
            }
        },
        "_built_at": _iso(_now()),
        "_universe": {"path": upath, "md5": umd5, "n_symbols": len(syms)},
        "_rule": "USER 2026-10-06: ALL TRADEABLE KEYS NEED POSITIONS WHENEVER JUSTIFIED BY THE VECTORIZED BACKTEST — tier BOTH_WINDOW > 30D_BEST; repair_dual > candidates > progress; 30D set needs proven gain > 0",
    }
    # USER 2026-10-25 stock shards: a proven board must never need universe membership —
    # the tier rules (gain > 0, fabrication screen) are the gate. Include bases seen in
    # repair/candidates/progress. Variant studies (AAPL_BF_LONG) excluded: real bases
    # never contain an underscore.
    try:
        _extra = set()
        for _src in (repair, cands, prog):
            for _ss in _src:
                if not isinstance(_ss, str) or "_" not in _ss:
                    continue
                _side = _ss.rsplit("_", 1)[-1]
                _base = _ss.rsplit("_", 1)[0]
                if _side in ("LONG", "SHORT") and _base and "_" not in _base:
                    _extra.add(_base)
        if _extra:
            syms = sorted(set(syms) | _extra)
    except Exception:
        pass
    no_set, fab_log = [], []
    for sym in syms:
        for side in ("LONG", "SHORT"):
            ss = f"{sym}_{side}"
            opts = []
            r, c, p = repair.get(ss), cands.get(ss), prog.get(ss)
            if r and r.get("q365"):
                opts.append(("BOTH_WINDOW", "repair_dual", r))
            if c and c.get("q365") and isinstance(c.get("overrides"), dict):
                opts.append(
                    (
                        "BOTH_WINDOW",
                        "candidates",
                        {
                            "overrides": c["overrides"],
                            "gain_30d": c.get("gain_30d"),
                            "trades": c.get("trades"),
                            "src": "data/vec_live/candidates.json (s5 data/golive/candidates_20261006.json) <- "
                            + str(c.get("path")),
                        },
                    )
                )
            if r and (r.get("gain_30d") or 0) > 0:
                opts.append(("30D_BEST", "repair_dual", r))
            if (
                c
                and isinstance(c.get("overrides"), dict)
                and (c.get("gain_30d") or 0) > 0
            ):
                opts.append(
                    (
                        "30D_BEST",
                        "candidates",
                        {
                            "overrides": c["overrides"],
                            "gain_30d": c.get("gain_30d"),
                            "trades": c.get("trades"),
                            "src": "data/vec_live/candidates.json <- "
                            + str(c.get("path")),
                        },
                    )
                )
            if p and (p.get("gain_30d") or 0) > 0:
                opts.append(("30D_BEST", "progress", p))
            if not opts:
                no_set.append(ss)
                continue
            clean = [o for o in opts if not _fabricated(o[2]["overrides"])]
            rejected = [
                f"{t}:{n}:{_fabricated(x['overrides'])}"
                for t, n, x in opts
                if _fabricated(x["overrides"])
            ]
            if rejected:
                fab_log.append(
                    {
                        "ss": ss,
                        "rejected": rejected,
                        "fallback": (
                            f"{clean[0][0]}:{clean[0][1]}"
                            if clean
                            else "NONE -> shadow"
                        ),
                    }
                )
            tier, src, s = clean[0] if clean else opts[0]
            mode = "live" if clean else "shadow"
            _pacct = (prev.get(ss) or {}).get("account")
            if not _pacct:
                try:
                    from tools import vec_shard as _vsh

                    _pacct = "trb" if not _vsh.is_crypto_sym(sym) else "ez"
                except Exception:
                    _pacct = "ez"
            out[ss] = {
                "mode": mode,
                "account": _pacct,
                "overrides_src": "inline",
                "overrides": s["overrides"],
                "set_source": src,
                "set_tier": tier,
                "set_src_path": s.get("src"),
                "set_gain_30d": s.get("gain_30d"),
                "set_trades": s.get("trades"),
                "set_md5": set_md5(s["overrides"]),
                "alternatives": [
                    f"{t}:{n}:{(x.get('gain_30d'))}"
                    for t, n, x in (clean[1:] if clean else opts[1:])
                ],
                "rejected_fabrication_sets": rejected,
                "shadow_reason": (
                    None
                    if clean
                    else "every proven set contains a fabrication-class test switch (BIBLE §19)"
                ),
            }
    out["_no_set"] = no_set
    out["_fabrication_rejections"] = fab_log
    for r in fab_log:
        _append_jsonl(VL / "fabrication_rejections.jsonl", dict(r, t=_iso(_now())))
        print(f"[vec_live] FABRICATION-CLASS set rejected {r}", file=sys.stderr)
    if write:
        # USER 2026-10-25 forward-data speed: sets go to vec_driven_sets.json, NEVER vec_driven.json —
        # pure_board_trader owns that file and its merge strips everything but mode/account/expiry.
        if SETS.exists():
            shutil.copy2(
                SETS,
                VL
                / f"vec_driven_sets.before_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M')}.json",
            )
        _atomic_json(SETS, out)
    return out


def init_example() -> None:
    VL.mkdir(parents=True, exist_ok=True)
    ex = {
        "_schema": {
            "SYMBOL_SIDE": {
                "mode": "shadow|live (live = consumer may execute via execute_now; shadow = consumer only logs)",
                "account": "binance account key the consumer uses (e.g. ez)",
                "overrides_src": "candidates.json (data/vec_live/candidates.json[ss].overrides) | inline (uses 'overrides') | path to json {ss:{overrides}} or {overrides}",
            }
        },
        "ATOMUSDT_LONG": {
            "mode": "shadow",
            "account": "ez",
            "overrides_src": "candidates.json",
        },
    }
    p = VL / "vec_driven.example.json"
    _atomic_json(p, ex)
    print(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument(
        "--no-poll", action="store_true", help="single pass instead of polling ~50 s"
    )
    ap.add_argument("--poll-sec", type=float, default=50.0)
    ap.add_argument("--watch", action="store_true", help="continuously watch NPZ tails and settings")
    ap.add_argument("--watch-sec", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-run even without a new bar (idempotent: intent_ids never re-emitted)",
    )
    ap.add_argument("--init-example", action="store_true")
    ap.add_argument(
        "--build-driven",
        action="store_true",
        help="rebuild vec_driven_sets.json: every tradeable crypto key with a proven set -> live",
    )
    a = ap.parse_args()
    if a.init_example:
        init_example()
        return 0
    if a.build_driven:
        if SCOPE == "7d":
            raise SystemExit("7D sets require verified 7D pilot results; --build-driven reads 30D boards")
        d = build_driven()
        keys = [k for k in d if not k.startswith("_")]
        tiers = {}
        for k in keys:
            t = f"{d[k]['set_tier']}:{d[k]['set_source']}"
            tiers[t] = tiers.get(t, 0) + 1
        print(
            json.dumps(
                {
                    "keys": len(keys),
                    "live": sum(1 for k in keys if d[k]["mode"] == "live"),
                    "shadow": sum(1 for k in keys if d[k]["mode"] == "shadow"),
                    "by_tier_source": tiers,
                    "no_set": len(d["_no_set"]),
                    "fabrication_rejections": d["_fabrication_rejections"],
                    "universe": d["_universe"],
                },
                indent=1,
            )
        )
        return 0
    if a.watch:
        return watch(a.workers, a.watch_sec)
    import v12_quick_engine  # noqa: F401  (import once before fork)
    from tools.opt import v12_pilot  # noqa: F401

    deadline = _now() + (0 if a.no_poll else a.poll_sec)
    first = True
    while True:
        recs = cycle(a.workers, force=a.force and first)
        first = False
        for r in recs:
            if r.get("status") != "NO_NEW_BAR":
                print(
                    json.dumps(
                        {
                            k: r.get(k)
                            for k in (
                                "ss",
                                "status",
                                "why",
                                "bar_iso",
                                "events",
                                "emitted",
                                "target",
                                "timings_sec",
                                "latency_vs_npz_mtime",
                                "error",
                            )
                        },
                        default=str,
                    ),
                    flush=True,
                )
        if _now() + 5 > deadline:
            break
        time.sleep(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
