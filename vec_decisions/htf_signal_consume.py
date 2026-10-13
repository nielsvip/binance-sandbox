"""HTF signal instance + LTF-confirmed retry with monotone tightening (USER 2026-10-13, V17 TF law, option "b" as corrected).

USER law: "HTF signals stay valid for longer but need LTF confirmations and can't just start repeating orders (D is valid
for 96 15m bars but can not reapply constantly nor be discarded after one hit)". Correction (authoritative): "The signal
remains valid, but ONLY if ANY lower-TF data confirms it ... keep trying if the bottom (top) was almost in but not quite
yet. But if it has to try 12 times it must keep trying with LESS TOLERANCE time after time."

Semantics (one shared implementation; the vector engine calls the array API, live native mirrors call the scalar API):

* HTF PATH = an entry trigger whose signal lives on D / 4h / 1h (docs/v17_tf_semantics.md section 3):
  BB_BREAKOUT_ENTRY_<tf> (state), BB_BOUNCE_ENTRY_<tf> (event), DC_BREAKOUT_TF_EXPANDED_<tf> (state),
  DC_BREAKOUT_ENTRY tier DC4H / DC1H (state), GOLDEN_RULE_ENTRY (1h breakout armed window).
* INSTANCE = the HTF bar that produced the signal (NPZ timestamp_<tf>; live: the completed HTF candle). GOLDEN_RULE's
  instance is its last 1h breakout cross. The instance is VALID for HTF_SIGNAL_VALID_BARS_<TF> 15m bars from its start
  (default = the venue span: crypto D 96 / 4h 16 / 1h 4; stocks RTH D 26 / 4h 14 / 1h 4). A one-bar HTF EVENT
  (BB_BOUNCE) stays armed for that window instead of dying after one bar ("never discarded after one hit").
* An open that ONLY an HTF path produced (no 15m/LTF entry source fires on the bar) is allowed only on a FRESH LTF
  confirmation, never on the mere persistence of the HTF state. Confirmations (side-matching, after the last exit):
    C1  15m WaveTrend: the most recent wt1/wt2 15m cross is on the trade side and at most window_k-1 bars old
    C2   1h WaveTrend: the most recent wt1/wt2 1h cross is on the trade side and at most window_k-1 15m bars old
    C3  15m stoch:    k crosses d on the decision bar itself (k>d now, k<=d on the previous bar; short mirror)
* ATTEMPT k on the same instance (k = opens already made from it + 1, at most HTF_SIGNAL_MAX_ATTEMPTS) tightens
  monotonically:
    window_k = max(1, floor(HTF_RETRY_LTF_WINDOW_BARS * HTF_RETRY_TIGHTEN_WINDOW ** (k-1)))   (fresher confirmation)
    need_k   = min(3, 1 + floor((k-1) * HTF_RETRY_TIGHTEN_CONFIRMS))                        (more confirmations)
    size_k   = max(0.1, HTF_RETRY_TIGHTEN_SIZE ** (k-1))                                    (smaller size)
  Defaults 4 bars / 0.75 / 0.25 / 1.0: attempts 1-4 need 1 confirmation, 5-8 need 2, 9-12 need all 3; the freshness
  window shrinks 4,3,2,1,1... bars. Size tightening defaults to 1.0 (off) because live sizing is live's job (BIBLE
  section 68.3a) - a vec-only size cut would only distort the backtest against live. Every prior attempt on the
  instance tightens the next one (a stopped-out attempt is a prior attempt); the instance is never discarded while valid.
* Any open made while an HTF path's mask is true counts as an attempt on that path's instance.
* HTF_SIGNAL_CONSUME_ENABLED=False restores the old behaviour exactly (no stripping, no gate, BB_BOUNCE one-bar event).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

HTF_TFS = ("1h", "4h", "D")
PERIOD_S = {"1h": 3600, "4h": 14400, "D": 86400}
SPAN_CRYPTO = {"1h": 4, "4h": 16, "D": 96}
SPAN_STOCKS = {"1h": 4, "4h": 14, "D": 26}
VALID_KEY = {"1h": "HTF_SIGNAL_VALID_BARS_1H", "4h": "HTF_SIGNAL_VALID_BARS_4H", "D": "HTF_SIGNAL_VALID_BARS_D"}
KEYS = (
    "HTF_SIGNAL_CONSUME_ENABLED",
    "HTF_SIGNAL_MAX_ATTEMPTS",
    "HTF_SIGNAL_VALID_BARS_D",
    "HTF_SIGNAL_VALID_BARS_4H",
    "HTF_SIGNAL_VALID_BARS_1H",
    "HTF_RETRY_LTF_WINDOW_BARS",
    "HTF_RETRY_TIGHTEN_WINDOW",
    "HTF_RETRY_TIGHTEN_CONFIRMS",
    "HTF_RETRY_TIGHTEN_SIZE",
)


def _g(cfg: Any, k: str, d: Any) -> Any:
    try:
        if callable(cfg) and not hasattr(cfg, k):
            return cfg(k, d)
        v = getattr(cfg, k, d)
        return d if v is None else v
    except Exception:
        return d


def _b(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def enabled(cfg: Any) -> bool:
    return _b(_g(cfg, "HTF_SIGNAL_CONSUME_ENABLED", True))


def norm_tf(tf: Any) -> Optional[str]:
    s = str(tf or "").strip()
    u = s.upper()
    if u in ("1H", "60M"):
        return "1h"
    if u in ("4H", "240M"):
        return "4h"
    if u in ("D", "1D", "DAY"):
        return "D"
    return None


def span_bars(tf: str, venue: str) -> int:
    t = norm_tf(tf)
    if t is None:
        return 1
    return (SPAN_STOCKS if str(venue).lower() == "tradier" else SPAN_CRYPTO)[t]


def valid_bars(cfg: Any, tf: str, venue: str) -> int:
    t = norm_tf(tf)
    if t is None:
        return 1
    try:
        v = int(float(_g(cfg, VALID_KEY[t], 0) or 0))
    except Exception:
        v = 0
    return v if v > 0 else span_bars(t, venue)


def attempt_requirements(cfg: Any, k: int) -> Tuple[int, int, float]:
    """(window_bars, confirmations_needed, size_mult) for attempt k (1-based) on one instance. Monotone in k."""
    k = max(1, int(k))
    try:
        base = float(_g(cfg, "HTF_RETRY_LTF_WINDOW_BARS", 4) or 4)
    except Exception:
        base = 4.0
    try:
        tw = float(_g(cfg, "HTF_RETRY_TIGHTEN_WINDOW", 0.75))
    except Exception:
        tw = 0.75
    try:
        tc = float(_g(cfg, "HTF_RETRY_TIGHTEN_CONFIRMS", 0.25))
    except Exception:
        tc = 0.25
    try:
        ts = float(_g(cfg, "HTF_RETRY_TIGHTEN_SIZE", 1.0))
    except Exception:
        ts = 1.0
    tw = min(max(tw, 0.0), 1.0)
    tc = max(tc, 0.0)
    ts = min(max(ts, 0.0), 1.0)
    window = max(1, int(math.floor(base * (tw ** (k - 1)) + 1e-9)))
    need = min(3, 1 + int(math.floor((k - 1) * tc + 1e-9)))
    size = max(0.1, ts ** (k - 1))
    return window, need, size


def max_attempts(cfg: Any) -> int:
    try:
        return max(1, int(float(_g(cfg, "HTF_SIGNAL_MAX_ATTEMPTS", 12) or 12)))
    except Exception:
        return 12


# ---------------------------------------------------------------- array API (vector engine)


def _tsec(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    return a / 1000.0 if len(a) and np.nanmax(np.abs(a)) > 1e11 else a


def instance_ids(npz: Mapping, ts: np.ndarray, tf: str, n: int) -> np.ndarray:
    """HTF instance id per 15m bar: the NPZ's own timestamp_<tf> (start of the completed HTF bar the arrays hold);
    fallback floor(ts / period) when the NPZ carries no timestamp_<tf>."""
    t = norm_tf(tf)
    a = None
    try:
        a = npz.get(f"timestamp_{t}") if hasattr(npz, "get") else None
    except Exception:
        a = None
    if a is not None:
        a = np.asarray(a)
        if a.shape[0] == n and np.any(a > 0):
            return _tsec(a).astype(np.int64)
    tsv = _tsec(np.asarray(ts)[:n])
    return np.floor(tsv / PERIOD_S.get(t, 3600)).astype(np.int64)


def run_start(ids: np.ndarray) -> np.ndarray:
    """Bar index where the current value of ids started (change points, forward filled)."""
    ids = np.asarray(ids)
    n = ids.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    chg = np.ones(n, dtype=bool)
    chg[1:] = ids[1:] != ids[:-1]
    idx = np.where(chg, np.arange(n), 0)
    return np.maximum.accumulate(idx).astype(np.int64)


def extend_event(event: np.ndarray, ids: np.ndarray, valid: int) -> np.ndarray:
    """An HTF EVENT stays armed for `valid` 15m bars from the event bar, inside the same instance (rule C5)."""
    ev = np.asarray(event, dtype=bool)
    n = ev.shape[0]
    if n == 0 or not ev.any():
        return ev.copy()
    last = np.maximum.accumulate(np.where(ev, np.arange(n), -1))
    ok = last >= 0
    li = np.where(ok, last, 0)
    same = np.asarray(ids)[li] == np.asarray(ids)
    return ok & same & ((np.arange(n) - li) < max(1, int(valid)))


def _last_cross(w1: np.ndarray, w2: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """(last cross bar index or -1, side of the most recent cross +1 bull / -1 bear / 0 none) per bar."""
    w1 = np.asarray(w1, dtype=float)
    w2 = np.asarray(w2, dtype=float)
    n = w1.shape[0]
    d = w1 - w2
    valid = np.isfinite(d) & ~((w1 == 0) & (w2 == 0))
    cb = np.zeros(n, dtype=bool)
    cr = np.zeros(n, dtype=bool)
    if n > 1:
        cb[1:] = valid[1:] & valid[:-1] & (d[1:] > 0) & (d[:-1] <= 0)
        cr[1:] = valid[1:] & valid[:-1] & (d[1:] < 0) & (d[:-1] >= 0)
    ev = cb | cr
    last = np.maximum.accumulate(np.where(ev, np.arange(n), -1))
    side_ev = np.where(cb, 1, np.where(cr, -1, 0))
    side = np.where(last >= 0, side_ev[np.where(last >= 0, last, 0)], 0)
    return last.astype(np.int64), side.astype(np.int8)


def ltf_context(npz: Mapping, n: int, is_long: bool, safe) -> Dict[str, np.ndarray]:
    """Per-bar LTF confirmation inputs (all on the 15m decision grid)."""
    s = 1 if is_long else -1
    l15, s15 = _last_cross(safe(npz, "wt1_15m", n), safe(npz, "wt2_15m", n))
    l1h, s1h = _last_cross(safe(npz, "wt1_1h", n), safe(npz, "wt2_1h", n))
    k = np.asarray(safe(npz, "stoch_k_15m", n, 50), dtype=float)
    d = np.asarray(safe(npz, "stoch_d_15m", n, 50), dtype=float)
    st = np.zeros(n, dtype=bool)
    if n > 1:
        if is_long:
            st[1:] = (k[1:] > d[1:]) & (k[:-1] <= d[:-1])
        else:
            st[1:] = (k[1:] < d[1:]) & (k[:-1] >= d[:-1])
    st &= ~((k == 50) & (d == 50))
    return {"l15": l15, "ok15": s15 == s, "l1h": l1h, "ok1h": s1h == s, "st15": st}


def ltf_confirmations(ctx: Mapping[str, np.ndarray], i: int, window: int, last_exit_bar: int) -> int:
    c = 0
    l15 = int(ctx["l15"][i])
    if bool(ctx["ok15"][i]) and l15 >= 0 and l15 > last_exit_bar and (i - l15) < window:
        c += 1
    l1h = int(ctx["l1h"][i])
    if bool(ctx["ok1h"][i]) and l1h >= 0 and l1h > last_exit_bar and (i - l1h) < window:
        c += 1
    if bool(ctx["st15"][i]) and i > last_exit_bar:
        c += 1
    return c


class RetryBook:
    """Per sym_side attempt book: path -> [instance_id, opens]. Used by the vector loop (bar indices) and the live
    mirrors (same arithmetic; live passes its own bar clock)."""

    def __init__(self):
        self.book: Dict[str, List[int]] = {}

    def opens(self, path: str, inst: int) -> int:
        st = self.book.get(path)
        return int(st[1]) if (st is not None and int(st[0]) == int(inst)) else 0

    def decide(self, cfg: Any, fired: List[Tuple[str, int, int, int]], i: int, last_exit_bar: int, conf_fn) -> Tuple[bool, Dict[str, Any]]:
        """fired = [(path, inst, inst_start_bar, valid_bars)], conf_fn(window, last_exit_bar) -> n confirmations.
        Allowed iff ANY fired path is inside its validity window, below the attempt cap, and has need_k confirmations."""
        cap = max_attempts(cfg)
        best = None
        diag = []
        for path, inst, start, valid in fired:
            k = self.opens(path, inst) + 1
            window, need, size = attempt_requirements(cfg, k)
            in_win = (i - int(start)) < max(1, int(valid))
            got = conf_fn(window, last_exit_bar) if (in_win and k <= cap) else 0
            ok = in_win and k <= cap and got >= need
            diag.append({"path": path, "inst": int(inst), "attempt": k, "window": window, "need": need, "got": got, "in_window": bool(in_win), "ok": bool(ok)})
            if ok and (best is None or size > best[1]):
                best = (path, size)
        return best is not None, {"paths": diag, "size_mult": (best[1] if best else 1.0), "by": (best[0] if best else "")}

    def commit(self, fired_now: List[Tuple[str, int]]) -> None:
        for path, inst in fired_now:
            st = self.book.get(path)
            if st is None or int(st[0]) != int(inst):
                self.book[path] = [int(inst), 1]
            else:
                st[1] = int(st[1]) + 1


def dc_tier_masks(cfg: Any, npz: Mapping, n: int, is_long: bool, close: np.ndarray, safe, base_safe=None) -> Dict[str, np.ndarray]:
    """DC_BREAKOUT_ENTRY tier split (same twin as compute_reentry_blocks, details=True): {'4h': mask&DC4H, '1h': mask&DC1H}."""
    try:
        import vec_decisions.check_entry_candidates_crypto__dc_breakout_tiered as _dc

        if base_safe is not None:
            w13, w23 = base_safe(npz, "wt1", n, cfg), base_safe(npz, "wt2", n, cfg)
        else:
            w13, w23 = safe(npz, "wt1_15m", n), safe(npz, "wt2_15m", n)
        det = _dc.check_dc_breakout_tiered_vec(
            cfg, close, safe(npz, "dc_high_4h", n), safe(npz, "dc_high_1h", n), safe(npz, "dc_high_15m", n), safe(npz, "dc_high_3m", n),
            safe(npz, "dc_low_4h", n), safe(npz, "dc_low_1h", n), safe(npz, "dc_low_15m", n), safe(npz, "dc_low_3m", n),
            safe(npz, "stoch_k_15m", n, 50), w13, w23, safe(npz, "wt1_15m", n), safe(npz, "wt2_15m", n), is_long, details=True,
        )
        m = np.asarray(det["mask"], dtype=bool)
        tier = np.asarray(det["tier"])
        return {"4h": m & (tier == "DC4H"), "1h": m & (tier == "DC1H")}
    except Exception:
        return {}


def strip_spec(cfg: Any, blocks: Mapping[str, np.ndarray], dc_tiers: Mapping[str, np.ndarray]) -> Dict[str, Optional[np.ndarray]]:
    """Block name -> replacement mask (None = zero) used to recompute the NON-HTF entry signal."""
    out: Dict[str, Optional[np.ndarray]] = {}
    for key, blk in (("BB_BREAKOUT_ENTRY_TF", "BB_BREAKOUT_ENTRY"), ("BB_BOUNCE_ENTRY_TF", "BB_BOUNCE_ENTRY")):
        t = norm_tf(_g(cfg, key, "OFF"))
        if t is not None and f"{blk}_{t}" in blocks:
            out[f"{blk}_{t}"] = None
    if "GOLDEN_RULE_ENTRY" in blocks:
        out["GOLDEN_RULE_ENTRY"] = None
    if "DC_BREAKOUT_ENTRY" in blocks and dc_tiers:
        htf = np.zeros(len(np.asarray(blocks["DC_BREAKOUT_ENTRY"])), dtype=bool)
        for m in dc_tiers.values():
            htf |= np.asarray(m, dtype=bool)
        out["DC_BREAKOUT_ENTRY"] = np.asarray(blocks["DC_BREAKOUT_ENTRY"], dtype=bool) & ~htf
    return out


def apply_strip(blocks: Dict[str, np.ndarray], spec: Optional[Mapping[str, Optional[np.ndarray]]]) -> None:
    if not spec:
        return
    for name, rep in spec.items():
        if name in blocks:
            arr = np.asarray(blocks[name], dtype=bool)
            blocks[name] = np.zeros_like(arr) if rep is None else np.asarray(rep, dtype=bool)[: arr.shape[0]]


def path_masks(cfg: Any, npz: Mapping, n: int, is_long: bool, ts: np.ndarray, blocks: Mapping[str, np.ndarray], dc_tiers: Mapping[str, np.ndarray], dcx_mask: Optional[np.ndarray], golden_last_cross_ts: Optional[np.ndarray], venue: str) -> List[Dict[str, Any]]:
    """Every active HTF path: {'path', 'tf', 'mask', 'inst', 'start', 'valid'} (arrays length n)."""
    out = []

    def _add(path, tf, mask, ids, start=None, valid=None):
        m = np.asarray(mask, dtype=bool)[:n]
        if m.shape[0] != n or not m.any():
            return
        out.append({"path": path, "tf": tf, "mask": m, "inst": ids, "start": run_start(ids) if start is None else start, "valid": valid_bars(cfg, tf, venue) if valid is None else valid})

    for key, blk in (("BB_BREAKOUT_ENTRY_TF", "BB_BREAKOUT_ENTRY"), ("BB_BOUNCE_ENTRY_TF", "BB_BOUNCE_ENTRY")):
        t = norm_tf(_g(cfg, key, "OFF"))
        if t is not None and f"{blk}_{t}" in blocks:
            ids = instance_ids(npz, ts, t, n)
            if blk == "BB_BOUNCE_ENTRY":
                # the block is already the armed window (extend_event in compute_reentry_blocks) -> no second window here
                _add(f"{blk}_{t}", t, blocks[f"{blk}_{t}"], ids, valid=10 ** 9)
            else:
                _add(f"{blk}_{t}", t, blocks[f"{blk}_{t}"], ids)
    for t, m in (dc_tiers or {}).items():
        _add(f"DC_BREAKOUT_ENTRY@{t}", t, m, instance_ids(npz, ts, t, n))
    t = norm_tf(_g(cfg, "DC_BREAKOUT_TF_EXPANDED", _g(cfg, "DC_BREAKOUT_TF", "1h")))
    if t is not None and dcx_mask is not None:
        _add(f"DC_BREAKOUT_TF_EXPANDED_{t}", t, dcx_mask, instance_ids(npz, ts, t, n))
    if "GOLDEN_RULE_ENTRY" in blocks and golden_last_cross_ts is not None:
        lc = np.asarray(golden_last_cross_ts, dtype=float)[:n]
        if lc.shape[0] == n:
            ids = np.round(lc).astype(np.int64)
            gs = run_start(ids)
            _add("GOLDEN_RULE_ENTRY", "1h", blocks["GOLDEN_RULE_ENTRY"], ids, start=gs, valid=10 ** 9)
    return out


# ---------------------------------------------------------------- scalar API (live native mirrors)


def live_instance_key(now_ts: float, tf: str, venue: str) -> int:
    """Instance key of the HTF candle whose COMPLETED values a live decision at now_ts reads (equality key only).
    crypto: UTC-aligned period index of the last completed candle; stocks: ET session date (D) / session half (4h) /
    session hour from 09:30 ET (1h)."""
    t = norm_tf(tf) or "1h"
    now = float(now_ts or 0.0)
    if now > 1e11:
        now /= 1000.0
    if str(venue).lower() != "tradier":
        return int(now // PERIOD_S[t]) - 1
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        et = datetime.fromtimestamp(now, ZoneInfo("America/New_York"))
        day = et.year * 10000 + et.month * 100 + et.day
        mins = et.hour * 60 + et.minute - (9 * 60 + 30)
        if t == "D":
            return day
        if t == "4h":
            return day * 10 + (0 if mins < 210 else 1)
        return day * 100 + max(0, mins) // 60
    except Exception:
        return int(now // PERIOD_S[t])


def live_bars_into_instance(now_ts: float, tf: str, venue: str) -> int:
    t = norm_tf(tf) or "1h"
    now = float(now_ts or 0.0)
    if now > 1e11:
        now /= 1000.0
    if str(venue).lower() != "tradier":
        return int((now % PERIOD_S[t]) // 900)
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        et = datetime.fromtimestamp(now, ZoneInfo("America/New_York"))
        mins = max(0, et.hour * 60 + et.minute - (9 * 60 + 30))
        if t == "D":
            return mins // 15
        if t == "4h":
            return (mins if mins < 210 else mins - 210) // 15
        return (mins % 60) // 15
    except Exception:
        return 0


def _f(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except Exception:
        return d


def live_confirmations(ind: Mapping[str, Any], is_long: bool, window: int, bars_since_exit: Optional[int]) -> int:
    """Scalar twin of ltf_confirmations on the live indicator dict (wt_cross_<tf> = side of the most recent cross,
    wt_cross_bars_ago_<tf> in that TF's bars; stoch k/d + *_prev for the decision-bar cross)."""
    side = "BULL" if is_long else "BEAR"
    lim = 10 ** 9 if bars_since_exit is None else int(bars_since_exit)
    c = 0
    if str(ind.get("wt_cross_15m", "")).upper() == side:
        b = _f(ind.get("wt_cross_bars_ago_15m"), 999.0)
        if b < window and b < lim:
            c += 1
    if str(ind.get("wt_cross_1h", "")).upper() == side:
        b = _f(ind.get("wt_cross_bars_ago_1h"), 999.0) * 4.0
        if b < window and b < lim:
            c += 1
    k, d = _f(ind.get("stoch_k_15m"), 50.0), _f(ind.get("stoch_d_15m"), 50.0)
    kp, dp = _f(ind.get("stoch_k_15m_prev"), k), _f(ind.get("stoch_d_15m_prev"), d)
    if not (k == 50.0 and d == 50.0) and lim > 0:
        if (is_long and k > d and kp <= dp) or ((not is_long) and k < d and kp >= dp):
            c += 1
    return c


_LIVE_BOOKS: Dict[str, RetryBook] = {}
_LIVE_EXIT_TS: Dict[str, float] = {}


def live_note_exit(pkey: str, now_ts: float) -> None:
    _LIVE_EXIT_TS[str(pkey)] = float(now_ts or 0.0)


def live_gate(cfg: Any, pkey: str, path: str, tf: str, ind: Mapping[str, Any], is_long: bool, now_ts: float, venue: str) -> Tuple[bool, Dict[str, Any]]:
    """Native live mirror for an HTF producer about to queue an OPEN. Returns (allowed, diag). Disabled -> allowed.
    The caller commits with live_commit(pkey, path, tf, now_ts, venue) once it actually queues the OPEN."""
    if not enabled(cfg):
        return True, {"disabled": True}
    t = norm_tf(tf)
    if t is None:
        return True, {"not_htf": True}
    book = _LIVE_BOOKS.setdefault(str(pkey), RetryBook())
    inst = live_instance_key(now_ts, t, venue)
    start_off = live_bars_into_instance(now_ts, t, venue)
    ex = _LIVE_EXIT_TS.get(str(pkey))
    bse = None if not ex else max(0, int((float(now_ts) - ex) // 900))
    return book.decide(cfg, [(path, inst, -start_off, valid_bars(cfg, t, venue))], 0, -1, lambda w, _le: live_confirmations(ind, is_long, w, bse))


def live_commit(pkey: str, path: str, tf: str, now_ts: float, venue: str) -> None:
    t = norm_tf(tf)
    if t is None:
        return
    _LIVE_BOOKS.setdefault(str(pkey), RetryBook()).commit([(path, live_instance_key(now_ts, t, venue))])


# ---------------------------------------------------------------- vector-engine glue (v12_quick_engine.simulate_one)


def nonhtf_core(cfg: Any, npz: Mapping, n: int, is_long: bool, close: np.ndarray, safe, base_safe, compute_blocks, compute_entry) -> Optional[Dict[str, Any]]:
    """Recompute compute_entry_signals with every HTF trigger block stripped (exact provenance of the core entry
    signal). Returns {'es_nonhtf', 'blocks', 'dc_tiers', 'golden_lc'} or None when disabled / no HTF path is active."""
    if not enabled(cfg) or _b(_g(cfg, "SIMPLE_PRICE_GT0_ENABLED", False)):
        return None
    try:
        import copy as _copy

        blocks = compute_blocks(npz, n, is_long, cfg)
        tiers = dc_tier_masks(cfg, npz, n, is_long, close, safe, base_safe) if "DC_BREAKOUT_ENTRY" in blocks else {}
        golden_lc = None
        if "GOLDEN_RULE_ENTRY" in blocks:
            import vec_decisions.golden_loop as _gl

            _ts = npz.get("timestamps", None)
            if _ts is None or len(np.asarray(_ts)) != n:
                _ts = np.arange(n, dtype=float) * 900.0
            golden_lc = _gl.trigger_masks(npz, np.asarray(_ts, dtype=float), is_long, str(_g(cfg, "MODE", "crypto")), cfg).get("last_cross_ts")
        spec = strip_spec(cfg, blocks, tiers)
        if spec:
            cfg_s = _copy.copy(cfg)
            cfg_s._HTF_STRIP_BLOCKS = spec
            es = np.asarray(compute_entry(npz, n, is_long, cfg_s), dtype=bool)
        else:
            es = None
        return {"es_nonhtf": es, "blocks": blocks, "dc_tiers": tiers, "golden_lc": golden_lc}
    except Exception:
        return None


def build_ctx(cfg: Any, npz: Mapping, n: int, is_long: bool, ts: np.ndarray, entry_sig: np.ndarray, core: Optional[Mapping], or_srcs, safe) -> Optional[Dict[str, Any]]:
    """Per-simulation gate context, or None (disabled / no active HTF path / nothing HTF-only)."""
    if not core:
        return None
    try:
        venue = str(_g(cfg, "MODE", "crypto"))
        dcx = None
        nonhtf_or = np.zeros(n, dtype=bool)
        for lb, m in list(or_srcs or []):
            try:
                mm = np.asarray(m, dtype=bool)[:n]
                if mm.shape[0] != n:
                    continue
                lbu = str(lb).upper()
                if lbu.startswith("DC_BREAKOUT_TF_EXPANDED_") and norm_tf(lbu.rsplit("_", 1)[-1]) is not None:
                    dcx = mm if dcx is None else (dcx | mm)
                else:
                    nonhtf_or |= mm
            except Exception:
                pass
        paths = path_masks(cfg, npz, n, is_long, ts, core["blocks"], core["dc_tiers"], dcx, core.get("golden_lc"), venue)
        if not paths:
            return None
        es_n = core.get("es_nonhtf")
        es = np.asarray(entry_sig, dtype=bool)
        htf_any = np.zeros(n, dtype=bool)
        for p in paths:
            htf_any |= p["mask"]
        htf_only = es & htf_any & ~nonhtf_or
        if es_n is not None:
            htf_only &= ~np.asarray(es_n, dtype=bool)[:n]
        return {"paths": paths, "htf_only": htf_only, "ltf": ltf_context(npz, n, is_long, safe), "book": RetryBook(), "trace": [], "size": 1.0}
    except Exception:
        return None


def _last_exit_bar(trades) -> int:
    for t in reversed(trades or []):
        try:
            if str(t.get("type", "CLOSE")) == "CLOSE" and t.get("bar_exit") is not None:
                return int(t["bar_exit"])
        except Exception:
            pass
    return -1


def gate(ctx: Optional[Dict[str, Any]], cfg: Any, i: int, trades) -> bool:
    """True = the bar's fire may proceed. Only HTF-only fires are gated."""
    if ctx is None:
        return True
    ctx["size"] = 1.0
    if not bool(ctx["htf_only"][i]):
        return True
    fired = [(p["path"], int(p["inst"][i]), int(p["start"][i]), int(p["valid"])) for p in ctx["paths"] if p["mask"][i]]
    if not fired:
        return True
    le = _last_exit_bar(trades)
    ltf = ctx["ltf"]
    ok, diag = ctx["book"].decide(cfg, fired, i, le, lambda w, lx: ltf_confirmations(ltf, i, w, lx))
    if ok:
        ctx["size"] = float(diag["size_mult"])
    ctx["trace"].append({"bar": int(i), "allowed": bool(ok), "last_exit_bar": le, **diag})
    return ok


def commit(ctx: Optional[Dict[str, Any]], i: int) -> None:
    """An open happened at bar i: every HTF path whose mask is true counts the attempt on its instance."""
    if ctx is None:
        return
    now = [(p["path"], int(p["inst"][i])) for p in ctx["paths"] if p["mask"][i]]
    if now:
        ctx["book"].commit(now)
        ctx["trace"].append({"bar": int(i), "open": True, "htf_only": bool(ctx["htf_only"][i]), "attempts": {pth: ctx["book"].opens(pth, inst) for pth, inst in now}, "insts": {pth: inst for pth, inst in now}})


def dump_trace(ctx: Optional[Dict[str, Any]], sym: str, is_long: bool) -> None:
    """V12_HTF_TRACE_FILE=path -> append the gate trace (proof instrumentation; inert when unset)."""
    import os

    p = os.environ.get("V12_HTF_TRACE_FILE")
    if not p or ctx is None:
        return
    try:
        import json

        with open(p, "a") as f:
            f.write(json.dumps({"sym": str(sym), "is_long": bool(is_long), "paths": [{"path": x["path"], "tf": x["tf"], "mask_bars": int(x["mask"].sum())} for x in ctx["paths"]], "htf_only_bars": int(ctx["htf_only"].sum()), "trace": ctx["trace"]}, default=str) + "\n")
    except Exception:
        pass
