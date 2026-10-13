#!/usr/bin/env python3
"""v17_daily_learn_prep — DAILY LEARN LOOP data packet (BIBLE §99, USER 2026-10-13: "a daily cron that uses <5% of tokens ...
learns from all the trades and the backtest results ... how to get out at the top and in at the bottom with bigger amounts if
higher low (vv short)").

PURE PYTHON, ZERO LLM TOKENS, READ-ONLY on every source (ledgers, klines, progress JSONs, combo library, A/B reports). The only
files written are under data/learn/ (or --out). No NPZ builds, no engine evals, no orders, no config.

Legs:
  --leg live           Mac: live trade ledgers (data/history/<acct>/*.jsonl + data/tradier/history/<acct>/*.jsonl, broker echoes
                       de-duplicated) -> per-trade lifecycle metrics on the symbol's 15m klines (klines_cache[/tradier]):
                       entry vs local extreme (% and bars, +-N bar window), exit vs the local top/bottom, MFE giveback, hold bars,
                       exit reason, higher-low (LONG) / lower-high (SHORT) adds and their forward result, size vs first entry,
                       missed augments (in-trade HL pivot + run-up with 0 augments), regime label; aggregates 24h + 7d by
                       cat_side / entry path / exit path / regime; decision-log VEC_EXACT dispatch share (data/decisions).
  --leg backtest-host  one fleet host (stdlib only, python3.10 ok): progress JSONs touched in the last 24h (~/v15_run*/progress,
                       ~/v15_import*/progress) -> compact board rows (v17 per-phase lift, promotions = adopted paths with delta/
                       TIM/DD/trades, best_start choice, final vs bh, surgeon symptoms + exit reasons, regime), plus the host's
                       data/v17_combo_library.sqlite last-24h evidence summary and daily_insight outcomes (closing the loop).
  --leg backtest       S1: backtest-host on S1 + the same leg on each --hosts peer over ssh (s2 = 10.0.0.4), + data/reports/
                       v17_ab_*.md of the last 24h.
  --leg packet         Mac: live JSON + backtest JSON + the four TEMPLATE_* files -> data/learn/daily_packet_<date>.md (HARD CAP
                       30 KB, dense top-N tables) + .json, data/learn/valid_keys_<date>.json (full key -> candidate values per
                       cat_side, the validator's truth) + valid_keys_<date>.txt (compact focused reference for the LLM, <= 20 KB).
  --leg validate       insights JSON (LLM output) -> drop every key/value not in valid_keys / refused by tools/switch_types.coerce;
                       writes the cleaned file (+ insight_id per candidate path).

No metric here is a Sharpe; gains are per-trade % on the ledger's own fills. Samples are small by design (one day) -> every
aggregate is DIAGNOSTIC ONLY and the packet says so.
"""
import argparse
import datetime
import glob
import json
import math
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("V17_LEARN_ROOT") or Path(__file__).resolve().parents[1])
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
LEARN_DIR = Path(os.environ.get("V17_LEARN_DIR") or (ROOT / "data" / "learn"))
PACKET_CAP = int(os.environ.get("V17_LEARN_PACKET_CAP", 30 * 1024))
KEYREF_CAP = int(os.environ.get("V17_LEARN_KEYREF_CAP", 20 * 1024))
STOCK_ACCTS = ("tra", "trb", "trc")
CRYPTO_ACCTS = ("ang", "fin", "flz", "inf", "men")
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CS_ABBR = {"CRYPTO_LONG": "CL", "CRYPTO_SHORT": "CS", "STOCKS_LONG": "SL", "STOCKS_SHORT": "SS"}
ADD_TYPES = ("OPEN", "AUGMENT", "REENTRY")
RED_TYPES = ("REDUCE", "CLOSE", "GHOST_CLOSE", "BROKER_CLOSE")
END_TYPES = ("CLOSE", "GHOST_CLOSE", "BROKER_CLOSE")
ECHO_RE = re.compile(r"^(BROKER_RECONCILED|Broker sync|BROKER_SYNC_OPEN_FROM_FLAT_STUB)", re.I)
NOFILL_RE = re.compile(r"REJECTED|_REJECT_|DRY_?RUN", re.I)  # logged intents that never filled (e.g. BROKER_SYNC_REJECTED_TOTAL_VALUE_CAP_349)
WIN_N = int(os.environ.get("V17_LEARN_WIN_BARS", 16))  # +-16 x 15m = +-4h local-extreme window
BAR_S = 900.0
FOCUS_TABS = ("REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "STDEV_SLOPE_SIZING", "ENTRY_CONFIRMATION_GATES", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES")


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(ts):
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _parse_ts(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s) / (1000.0 if float(s) > 1e11 else 1.0)
    try:
        t = str(s).strip().replace("Z", "+00:00")
        d = datetime.datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def _num(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _mean(xs):
    xs = [float(x) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------------------- live leg
def path_label(reason):
    """normalised entry/exit path name from a ledger reason ('EXIT_VELOCITY_WT against-long g-0.23% |VEC_EXACT' -> EXIT_VELOCITY_WT)."""
    s = str(reason or "").split("|")[0].strip()
    if not s:
        return "UNKNOWN"
    if s.upper().startswith(("CLOSE ", "REDUCE ")):
        s = s.split(" ", 1)[1].strip()
    tok = re.split(r"[\s(:,=]|_(?:px|lvl|qty|g|tgt|close|open|cur|exit|now|k|s)[-+]?\d|_[-+]\d|_\d+\.\d|@", s)[0].strip()
    tok = re.sub(r"_FOR_[a-z]+$", "_FOR", re.sub(r"[-+]?\d+\.\d+%?$|[-+]?\d+%$", "", tok)).rstrip("_") or s[:24]
    if tok in ("DAYTRADE_TARGET", "TECHNICAL_TARGET") and " " in s and not re.match(r"^[sg]?[-+]?[0-9]", s.split(" ", 2)[1]):
        tok = f"{tok} {s.split(' ', 2)[1]}"
    return tok[:40]


def load_ledger(paths):
    """events from one or more jsonl files (same acct+sym_side), de-duplicated across copies, broker echoes dropped when the
    bot's own event of the same direction is within 300 s (echo = BROKER_RECONCILED / Broker sync / FLAT_STUB)."""
    seen, ev = set(), []
    for p in paths:
        try:
            fh = open(p, "r", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                t = _parse_ts(e.get("ts"))
                typ = str(e.get("type") or "").upper()
                q, px = _num(e.get("qty")), _num(e.get("price"))
                if t is None or typ not in ADD_TYPES + RED_TYPES or not q or not px or q <= 0 or px <= 0 or NOFILL_RE.search(str(e.get("reason") or "")):
                    continue
                k = (round(t, 0), typ, round(q, 8), round(px, 6))
                if k in seen:
                    continue
                seen.add(k)
                ev.append({"t": t, "type": typ, "qty": q, "px": px, "reason": str(e.get("reason") or ""), "echo": bool(ECHO_RE.match(str(e.get("reason") or "")))})
    ev.sort(key=lambda e: e["t"])
    out = []
    for i, e in enumerate(ev):
        if e["echo"]:
            d = e["type"] in ADD_TYPES
            twin = any((not o["echo"]) and ((o["type"] in ADD_TYPES) == d) and abs(o["t"] - e["t"]) <= 300 for o in ev[max(0, i - 12): i + 12])
            if twin:
                continue
        out.append(e)
    return out


def lifecycles(events, is_long):
    """replay qty -> list of trades {adds:[ev], reds:[ev], open, ...}. A reduce from flat (history rotated) is ignored."""
    trades, cur, qty, peak = [], None, 0.0, 0.0
    for e in events:
        if e["type"] in ADD_TYPES:
            if cur is None or qty <= 1e-12:
                cur = {"adds": [], "reds": [], "start_kind": e["type"], "cost": 0.0, "realized": 0.0, "peak_cost": 0.0}
                qty, peak = 0.0, 0.0
                trades.append(cur)
            avg = cur["cost"] / qty if qty > 0 else e["px"]
            cur["adds"].append(dict(e, avg_before=avg, qty_before=qty))
            qty += e["qty"]
            cur["cost"] += e["qty"] * e["px"]
            peak = max(peak, qty)
            cur["peak_cost"] = max(cur["peak_cost"], cur["cost"])
            continue
        if cur is None or qty <= 1e-12:
            continue
        q = min(e["qty"], qty) if e["type"] not in END_TYPES else qty
        avg = cur["cost"] / qty
        cur["realized"] += q * ((e["px"] - avg) if is_long else (avg - e["px"]))
        cur["reds"].append(dict(e, qty=q, avg=avg))
        qty -= q
        cur["cost"] = max(0.0, cur["cost"] - q * avg)
        if e["type"] in END_TYPES or qty <= max(1e-12, 0.01 * peak):
            cur["closed"] = True
            qty, cur = 0.0, None
    return trades


_KL = {}


def load_klines(symbol, venue, root=None):
    root = Path(root or ROOT)
    key = (str(root), symbol, venue)
    if key in _KL:
        return _KL[key]
    dirs = ("klines_cache/tradier", "klines_cache_gateway/tradier", "klines_cache", "klines_cache_gateway") if venue == "STOCKS" else ("klines_cache", "klines_cache_gateway", "klines_cache/tradier", "klines_cache_gateway/tradier")
    out = None
    for p in [root / d / f"{symbol}_15m.json" for d in dirs]:  # freshest copy wins (one cache can lag a day)
        try:
            raw = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        rows = raw if isinstance(raw, list) else (raw.get("data") or raw.get("klines") or [])
        ts, hi, lo, cl = [], [], [], []
        for r in rows:
            if isinstance(r, dict):
                t, h, l_, c = _parse_ts(r.get("timestamp") or r.get("t") or r.get("time")), _num(r.get("high")), _num(r.get("low")), _num(r.get("close"))
            elif isinstance(r, (list, tuple)) and len(r) >= 5:
                t, h, l_, c = _parse_ts(r[0]), _num(r[2]), _num(r[3]), _num(r[4])
            else:
                continue
            if t is None or not h or not l_ or not c:
                continue
            ts.append(t)
            hi.append(h)
            lo.append(l_)
            cl.append(c)
        if len(ts) >= 50 and (out is None or max(ts) > out["ts"][-1]):
            order = sorted(range(len(ts)), key=lambda i: ts[i])
            out = {"ts": [ts[i] for i in order], "high": [hi[i] for i in order], "low": [lo[i] for i in order], "close": [cl[i] for i in order], "src": str(p.relative_to(root))}
    _KL[key] = out
    return out


def _bar_idx(kl, t):
    """index of the bar containing t (last bar with open <= t); None outside the kline span."""
    ts = kl["ts"]
    if not ts or t < ts[0] - BAR_S or t > ts[-1] + BAR_S:
        return None
    lo, hi = 0, len(ts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _regime(kl, i, is_long):
    try:
        from tools import v17_combo_engine as CE
    except Exception:
        CE = None
    if CE is None or i is None:
        return "UNKNOWN"
    n7 = 7 * 96
    a = max(0, i - n7)
    now = CE.seg_features(kl["close"][a: i + 1], is_long)
    ref = CE.seg_features(kl["close"][max(0, a - n7): a + 1], is_long) if a > 50 else None
    return CE.classify_regime(now, ref) if now else "UNKNOWN"


def trade_metrics(tr, kl, is_long, n=WIN_N):
    """per-trade top/bottom metrics (None fields when the klines do not cover the trade)."""
    a0 = tr["adds"][0]
    sgn = 1.0 if is_long else -1.0
    m = {"t_entry": a0["t"], "entry_px": a0["px"], "entry_kind": tr["start_kind"], "entry_path": path_label(a0["reason"]), "vec_exact": "VEC_EXACT" in a0["reason"], "n_adds": len(tr["adds"]) - 1, "n_augments": sum(1 for x in tr["adds"][1:] if x["type"] == "AUGMENT"), "closed": bool(tr.get("closed"))}
    first_val = a0["qty"] * a0["px"]
    if first_val < 0.1 * tr["peak_cost"]:  # dust/probe starter fill (e.g. 3 units then 8249): size growth is measured from the first meaningful fill
        m["dust_starter_usd"] = _r(first_val, 4)
        first_val = next((x["qty"] * x["px"] for x in tr["adds"] if x["qty"] * x["px"] >= 0.1 * tr["peak_cost"]), first_val)
    m["size_first_usd"] = _r(first_val)
    m["size_peak_usd"] = _r(tr["peak_cost"])
    m["size_ratio_peak_first"] = _r(tr["peak_cost"] / first_val, 2) if first_val > 0 else None
    if tr["reds"]:
        last = tr["reds"][-1]
        m["t_exit"], m["exit_px"] = last["t"], last["px"]
        m["exit_path"] = path_label(last["reason"])
        m["pnl_pct"] = _r(tr["realized"] / tr["peak_cost"] * 100.0, 3) if tr["peak_cost"] > 0 else None
        m["pnl_usd"] = _r(tr["realized"], 2)
        m["exit_move_pct"] = _r(sgn * (last["px"] / a0["px"] - 1.0) * 100.0, 3)
    if kl is None:
        m["kl"] = False
        return m
    m["kl"] = True
    hi, lo = kl["high"], kl["low"]
    ie = _bar_idx(kl, a0["t"])
    if ie is None:
        return m
    m["regime"] = _regime(kl, ie, is_long)
    w0, w1 = max(0, ie - n), min(len(lo) - 1, ie + n)
    if is_long:
        j = min(range(w0, w1 + 1), key=lambda k: lo[k])
        m["entry_vs_extreme_pct"] = _r((a0["px"] / lo[j] - 1.0) * 100.0, 3)
        jb = min(range(w0, ie + 1), key=lambda k: lo[k])
        m["entry_vs_prior_extreme_pct"] = _r((a0["px"] / lo[jb] - 1.0) * 100.0, 3)
    else:
        j = max(range(w0, w1 + 1), key=lambda k: hi[k])
        m["entry_vs_extreme_pct"] = _r((hi[j] / a0["px"] - 1.0) * 100.0, 3)
        jb = max(range(w0, ie + 1), key=lambda k: hi[k])
        m["entry_vs_prior_extreme_pct"] = _r((hi[jb] / a0["px"] - 1.0) * 100.0, 3)
    m["entry_bars_from_extreme"] = ie - j  # >0 entered after the bottom/top; <0 entered before it (caught the knife)
    m["entry_ref_extreme"] = lo[j] if is_long else hi[j]
    ix = _bar_idx(kl, m["t_exit"]) if m.get("t_exit") else None
    end = ix if ix is not None else len(lo) - 1
    if end >= ie:
        seg = range(ie, end + 1)
        if is_long:
            km = max(seg, key=lambda k: hi[k])
            mfe = (hi[km] / a0["px"] - 1.0) * 100.0
            mae = (min(lo[k] for k in seg) / a0["px"] - 1.0) * 100.0
        else:
            km = min(seg, key=lambda k: lo[k])
            mfe = (1.0 - lo[km] / a0["px"]) * 100.0
            mae = -(max(hi[k] for k in seg) / a0["px"] - 1.0) * 100.0
        m["mfe_pct"], m["mae_pct"] = _r(mfe, 3), _r(mae, 3)
        m["hold_bars"] = end - ie
        if m.get("exit_move_pct") is not None and ix is not None:
            m["giveback_pct"] = _r(mfe - m["exit_move_pct"], 3)
    if ix is not None:
        x0, x1 = max(0, ix - n), min(len(lo) - 1, ix + n)
        if is_long:
            jt = max(range(x0, x1 + 1), key=lambda k: hi[k])
            m["exit_vs_extreme_pct"] = _r((hi[jt] / m["exit_px"] - 1.0) * 100.0, 3)
        else:
            jt = min(range(x0, x1 + 1), key=lambda k: lo[k])
            m["exit_vs_extreme_pct"] = _r((1.0 - lo[jt] / m["exit_px"]) * 100.0, 3)
        m["exit_bars_from_extreme"] = ix - jt  # >0 exited after the top (gave back); <0 exited before it (premature)
    # higher-low (LONG) / lower-high (SHORT) adds inside the trade + missed augments
    adds = []
    ref_ext = m["entry_ref_extreme"]
    for x in tr["adds"][1:]:
        ia = _bar_idx(kl, x["t"])
        if ia is None:
            continue
        lw = range(max(0, ia - n), ia + 1)
        loc = min(lo[k] for k in lw) if is_long else max(hi[k] for k in lw)
        hl = loc > ref_ext * 1.0005 if is_long else loc < ref_ext * 0.9995
        fwd = None if m.get("exit_px") is None else sgn * (m["exit_px"] / x["px"] - 1.0) * 100.0
        adds.append({"type": x["type"], "path": path_label(x["reason"]), "hl": bool(hl), "fwd_pct": _r(fwd, 3), "size_vs_first": _r(x["qty"] * x["px"] / first_val, 2) if first_val > 0 else None, "vs_avg_pct": _r(sgn * (x["px"] / x["avg_before"] - 1.0) * 100.0, 3)})
        ref_ext = loc  # successive higher lows: each add is compared with the previous add's low
    m["adds"] = adds
    if m["n_augments"] == 0 and end - ie >= 6:
        best = None
        for k in range(ie + 2, max(ie + 2, end - 1)):
            if is_long:
                piv = lo[k] <= min(lo[max(ie, k - 2): k + 3]) and lo[k] > m["entry_ref_extreme"] * 1.0005
                if piv:
                    run = (max(hi[k: end + 1]) / lo[k] - 1.0) * 100.0
                    best = run if best is None or run > best else best
            else:
                piv = hi[k] >= max(hi[max(ie, k - 2): k + 3]) and hi[k] < m["entry_ref_extreme"] * 0.9995
                if piv:
                    run = (1.0 - min(lo[k: end + 1]) / hi[k]) * 100.0
                    best = run if best is None or run > best else best
        if best is not None and best >= 1.0:
            m["missed_augment_run_pct"] = _r(best, 3)
    return m


def _cat_side(acct, sym_side):
    side = sym_side.rsplit("_", 1)[-1].upper()
    return ("STOCKS_" if acct in STOCK_ACCTS else "CRYPTO_") + side


def collect_live(root=None, now=None, days=7.0):
    """every lifecycle with an entry or exit inside the last `days` (open trades included, flagged)."""
    root = Path(root or ROOT)
    now = float(now or time.time())
    lo_t = now - days * 86400.0
    files = {}
    for base in (root / "data" / "history", root / "data" / "tradier" / "history"):
        for p in base.glob("*/*.jsonl"):
            acct = p.parent.name
            if acct not in STOCK_ACCTS + CRYPTO_ACCTS or not re.fullmatch(r"[A-Z0-9.]+_(LONG|SHORT)", p.stem):
                continue
            try:
                if p.stat().st_mtime < lo_t:
                    continue
            except OSError:
                continue
            files.setdefault((acct, p.stem), []).append(p)
    trades, n_ev = [], 0
    kl_missing, no_bar = set(), set()
    for (acct, ss), paths in sorted(files.items()):
        is_long = ss.endswith("_LONG")
        ev = load_ledger(paths)
        n_ev += len(ev)
        sym = ss.rsplit("_", 1)[0]
        cs = _cat_side(acct, ss)
        kl = None
        for tr in lifecycles(ev, is_long):
            t_last = tr["reds"][-1]["t"] if tr["reds"] else tr["adds"][-1]["t"]
            if t_last < lo_t:
                continue
            if kl is None:
                kl = load_klines(sym, "STOCKS" if cs.startswith("STOCKS") else "CRYPTO", root) or False
                if kl is False:
                    kl_missing.add(sym)
            m = trade_metrics(tr, kl or None, is_long)
            m.update(acct=acct, sym_side=ss, cat_side=cs)
            if kl and m.get("entry_vs_extreme_pct") is None:
                no_bar.add(sym)
            trades.append(m)
    dec = dispatch_stats(root, now)
    return {"generated_utc": _iso(now), "now": now, "days": days, "n_files": len(files), "n_events": n_ev, "n_trades": len(trades), "klines_missing": sorted(kl_missing)[:40], "n_klines_missing": len(kl_missing), "klines_stale": sorted(no_bar)[:40], "n_klines_stale": len(no_bar), "dispatch_24h": dec, "trades": trades}


def dispatch_stats(root, now):
    out = {}
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    names = {f"decisions_*_{(day - datetime.timedelta(days=k)).strftime('%Y%m%d')}.jsonl" for k in (0, 1)}
    for pat in names:
        for p in (Path(root) / "data" / "decisions").glob(pat):
            acct = p.name.split("_")[1]
            try:
                fh = open(p, "r", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    t = _parse_ts(e.get("timestamp"))
                    if t is None or t < now - 86400:
                        continue
                    d = out.setdefault(acct, {"n": 0, "vec_exact": 0, "by_action": {}})
                    d["n"] += 1
                    d["vec_exact"] += 1 if "VEC_EXACT" in str(e.get("reason") or "") else 0
                    a = str(e.get("action") or "?")
                    d["by_action"][a] = d["by_action"].get(a, 0) + 1
    return out


def _agg(rows):
    cl = [r for r in rows if r.get("closed") and r.get("pnl_pct") is not None]
    return {"n": len(rows), "closed": len(cl), "win_pct": _r(100.0 * sum(1 for r in cl if r["pnl_pct"] > 0) / len(cl), 1) if cl else None, "pnl_pct": _r(_mean(r["pnl_pct"] for r in cl), 3), "pnl_usd": _r(sum(r.get("pnl_usd") or 0 for r in cl), 2), "entry_vs_ext": _r(_mean(r.get("entry_vs_extreme_pct") for r in rows), 3), "entry_bars": _r(_mean(r.get("entry_bars_from_extreme") for r in rows), 1), "exit_vs_ext": _r(_mean(r.get("exit_vs_extreme_pct") for r in cl), 3), "exit_bars": _r(_mean(r.get("exit_bars_from_extreme") for r in cl), 1), "mfe": _r(_mean(r.get("mfe_pct") for r in cl), 3), "giveback": _r(_mean(r.get("giveback_pct") for r in cl), 3), "hold": _r(_mean(r.get("hold_bars") for r in cl), 1), "size_ratio": _r(_mean(r.get("size_ratio_peak_first") for r in rows), 2)}


def aggregate_live(live, hours=(24, 168)):
    now = live["now"]
    out = {}
    for h in hours:
        lo_t = now - h * 3600.0
        rows = [t for t in live["trades"] if (t.get("t_exit") or t["t_entry"]) >= lo_t]
        g = {"all": _agg(rows)}
        for dim, key in (("cat_side", "cat_side"), ("entry_path", "entry_path"), ("exit_path", "exit_path"), ("regime", "regime")):
            buckets = {}
            for r in rows:
                kk = r.get(key) or "UNKNOWN"
                if dim in ("entry_path", "exit_path", "regime"):
                    kk = f"{r['cat_side']}|{kk}"
                buckets.setdefault(kk, []).append(r)
            g[dim] = {k: _agg(v) for k, v in sorted(buckets.items(), key=lambda kv: -len(kv[1]))}
        adds = [dict(a, cat_side=r["cat_side"]) for r in rows for a in (r.get("adds") or [])]
        hl = {}
        for a in adds:
            k = f"{a['cat_side']}|{'HL' if a['hl'] else 'noHL'}|{a['type']}"
            d = hl.setdefault(k, [])
            d.append(a)
        g["adds"] = {k: {"n": len(v), "fwd_pct": _r(_mean(a["fwd_pct"] for a in v), 3), "win_pct": _r(100.0 * sum(1 for a in v if (a["fwd_pct"] or 0) > 0) / max(1, sum(1 for a in v if a["fwd_pct"] is not None)), 1), "size_vs_first": _r(_mean(a["size_vs_first"] for a in v), 2), "vs_avg_pct": _r(_mean(a["vs_avg_pct"] for a in v), 3)} for k, v in sorted(hl.items())}
        ma = [r for r in rows if r.get("missed_augment_run_pct") is not None]
        g["missed_augment"] = {cs: {"n": len([r for r in ma if r["cat_side"] == cs]), "run_pct": _r(_mean(r["missed_augment_run_pct"] for r in ma if r["cat_side"] == cs), 3), "of_trades": len([r for r in rows if r["cat_side"] == cs])} for cs in CAT_SIDES if any(r["cat_side"] == cs for r in rows)}
        out[f"{h}h"] = g
    return out


# ---------------------------------------------------------------------------------------------------------- backtest leg
def _phase_base(p):
    return re.sub(r"_\d+$", "", str(p))


def board_row(d, path, host):
    v = d.get("v17") or {}
    ph = {}
    for p, x in (v.get("phases") or {}).items():
        if not isinstance(x, dict):
            continue
        b = _phase_base(p)
        e = ph.setdefault(b, {"lift": 0.0, "evals": 0, "wall_s": 0.0, "promotions": 0, "n": 0})
        e["lift"] += float(_num(x.get("lift")) or 0.0)
        e["evals"] += int(_num(x.get("evals")) or 0)
        e["wall_s"] += float(_num(x.get("wall_s")) or 0.0)
        e["promotions"] += int(_num(x.get("promotions")) or 0)
        e["n"] += 1
    promos = []
    for pr in v.get("promotions") or []:
        if not isinstance(pr, dict):
            continue
        parts = pr.get("parts") or ([str(pr.get("what") or "").split(":", 1)[-1]] if pr.get("what") else [])
        promos.append({"phase": pr.get("phase"), "parts": [str(x) for x in parts][:6], "delta": _r(_num(pr.get("delta")), 4), "trades": pr.get("trades"), "tim": _r(_num(pr.get("tim")), 1), "dd": _r(_num(pr.get("dd")), 2)})
    surg = []
    for _k, s in (v.get("surgeon") or {}).items():
        for rd in (s or {}).get("rounds") or []:
            surg.append({"symptoms": {k: {"n": (x or {}).get("n"), "size": _r(_num((x or {}).get("size")), 3)} for k, x in (rd.get("symptoms") or {}).items()}, "exits": (rd.get("top_exit_reasons") or [])[:5], "adopted": len(rd.get("adopted") or []), "by_symptom": {k: {"tried": (x or {}).get("tried"), "pos": (x or {}).get("pos")} for k, x in (rd.get("by_symptom") or {}).items()}, "combo_offered": ((rd.get("combo_engine") or {}).get("offered"))})
    reg = v.get("regime") or {}
    dc = d.get("defaults_challenger") or {}
    f365 = d.get("final_365d") if isinstance(d.get("final_365d"), dict) else {}
    final = _num(d.get("final_gain_fresh_vec")) if d.get("final_gain_fresh_vec") is not None else _num(d.get("cumulative_gain"))
    tier = d.get("tier") if isinstance(d.get("tier"), dict) else {}
    return {"sym_side": d.get("symside") or Path(path).name.replace("_v14_progress.json", ""), "host": host, "run": Path(path).parent.parent.name, "done_utc": d.get("result_done_utc"), "v17": bool(v.get("on")), "start": _r(_num(d.get("initial_baseline_gain")), 3), "final": _r(final, 3), "bh": _r(_num(d.get("bh")), 3), "trades": d.get("final_trades"), "tier": tier.get("tier"), "best_start": dc.get("chosen"), "best_start_gain": _r(_num(dc.get("start_gain")), 3), "phases": {k: {"lift": round(e["lift"], 4), "evals": e["evals"], "wall_s": round(e["wall_s"], 1), "promotions": e["promotions"]} for k, e in ph.items()}, "promotions": promos[:30], "surgeon": surg[-2:], "regime_now": reg.get("regime_now"), "regime_then": reg.get("regime_then"), "old_set_better_recent_by": _r(_num((reg.get("previous") or {}).get("old_set_better_recent_by")), 3), "g365": _r(_num(f365.get("gain") if f365 else None), 3), "dd365": _r(_num(f365.get("dd") if f365 else None), 2), "verdict": d.get("verdict") if isinstance(d.get("verdict"), (str, type(None))) else str(d.get("verdict"))[:60]}


def library_summary(lib_path, since_iso, top=40):
    p = Path(lib_path)
    if not p.exists():
        return {"path": str(p), "missing": True}
    try:
        db = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as e:
        return {"path": str(p), "error": str(e)[:120]}
    out = {"path": str(p)}
    try:
        q = "SELECT e.source, e.origin, e.window_days, COUNT(*), SUM(e.best_delta > 1e-9), AVG(CASE WHEN e.best_delta > 1e-9 THEN e.best_delta END) FROM evidence e WHERE e.ts >= ? AND e.source LIKE 'v17_combo_engine%' GROUP BY 1,2,3 ORDER BY 4 DESC"
        out["origins_24h"] = [{"source": r[0], "origin": r[1], "w": r[2], "n": r[3], "pos": r[4] or 0, "mean_pos_delta": _r(r[5], 3)} for r in db.execute(q, (since_iso,)).fetchall()][:40]
        q = "SELECT b.cat_side, c.combo, e.window_days, COUNT(DISTINCT b.sym_side), AVG(e.best_delta), MAX(e.best_delta), AVG(e.tim), AVG(e.dd), AVG(e.trades), GROUP_CONCAT(DISTINCT e.origin) FROM evidence e JOIN combos c ON c.combo_id=e.combo_id JOIN boards b ON b.board_id=e.board_id WHERE e.ts >= ? AND e.best_delta > 1e-9 AND (e.valid IS NULL OR e.valid=1) AND e.source IN ('v17_combo_engine','v17_combo_engine_joint','delta_log') GROUP BY b.cat_side, e.combo_id, e.window_days ORDER BY COUNT(DISTINCT b.sym_side) DESC, AVG(e.best_delta) DESC LIMIT ?"
        out["top_combos_24h"] = [{"cat_side": r[0], "combo": json.loads(r[1]), "w": r[2], "n_boards": r[3], "mean_delta": _r(r[4], 3), "max_delta": _r(r[5], 3), "tim": _r(r[6], 1), "dd": _r(r[7], 2), "trades": _r(r[8], 0), "origins": (r[9] or "")[:60]} for r in db.execute(q, (since_iso, top * 3)).fetchall()]
        has = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='insight_outcomes'").fetchone()
        if has:
            q = "SELECT insight_id, insight_date, cat_side, status, COUNT(*), COUNT(DISTINCT sym_side), AVG(d30), MAX(d30), SUM(d30 > 1e-9 AND (gate IS NULL OR gate='')), AVG(d365), SUM(adopted) FROM insight_outcomes WHERE ts >= ? GROUP BY insight_id, cat_side, status ORDER BY insight_date DESC, insight_id"
            out["insight_outcomes"] = [{"insight_id": r[0], "date": r[1], "cat_side": r[2], "status": r[3], "n": r[4], "boards": r[5], "d30_mean": _r(r[6], 3), "d30_max": _r(r[7], 3), "pos_gated": r[8] or 0, "d365_mean": _r(r[9], 3), "adopted": r[10] or 0} for r in db.execute(q, (since_iso[:10] if len(since_iso) > 10 else since_iso,)).fetchall()]
        else:
            out["insight_outcomes"] = []
    except sqlite3.Error as e:
        out["error"] = str(e)[:160]
    finally:
        db.close()
    return out


def collect_bt_host(home=None, now=None, hours=24.0, lib_path=None, globs=None):
    home = Path(home or Path.home())
    now = float(now or time.time())
    pats = globs or [str(home / "v15_run*" / "progress" / "*_v14_progress.json"), str(home / "v15_import*" / "progress" / "*_v14_progress.json")]
    host = socket.gethostname()
    rows, n_files, errs = [], 0, 0
    for f in sorted({x for p in pats for x in glob.glob(p)}):
        try:
            if os.path.getmtime(f) < now - hours * 3600.0:
                continue
            n_files += 1
            d = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            errs += 1
            continue
        if not isinstance(d, dict):
            continue
        rows.append(board_row(d, f, host))
    since = datetime.datetime.fromtimestamp(now - hours * 3600.0, datetime.timezone.utc).strftime("%Y-%m-%dT%H")
    lib = library_summary(lib_path or (Path(os.environ.get("V17_COMBO_LIB") or (ROOT / "data" / "v17_combo_library.sqlite"))), since)
    return {"host": host, "generated_utc": _iso(now), "n_files": n_files, "read_errors": errs, "boards": rows, "library": lib}


def ab_reports(root=None, now=None, hours=24.0, max_rows=40):
    root = Path(root or ROOT)
    now = float(now or time.time())
    out = []
    for p in sorted((root / "data" / "reports").glob("v17_ab_*.md")):
        try:
            if p.stat().st_mtime < now - hours * 3600.0:
                continue
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        hdr = None
        for ln in lines:
            if not ln.startswith("|") or set(ln.replace("|", "").strip()) <= {"-", " "}:
                continue
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if hdr is None:
                hdr = cells
                continue
            row = dict(zip(hdr, cells))
            out.append({"report": p.name, "sym_side": row.get("sym_side"), "arm": row.get("arm"), "gain": row.get("final gain"), "bh": row.get("bh"), "trades": row.get("trades"), "tim": row.get("TIM"), "dd": row.get("max DD"), "g365": row.get("365D gain"), "evals": row.get("evals")})
    return out[-max_rows:]


def collect_backtest(hosts=("local", "10.0.0.4"), now=None, hours=24.0, ssh_timeout=900):
    now = float(now or time.time())
    out = {"generated_utc": _iso(now), "hosts": {}, "ab": ab_reports(now=now, hours=hours)}
    for h in hosts:
        if h in ("local", "", socket.gethostname()):
            out["hosts"][socket.gethostname()] = collect_bt_host(now=now, hours=hours)
            continue
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", h, f"cd ~/binance-sandbox && nice -n 15 python3 tools/v17_daily_learn_prep.py --leg backtest-host --hours {hours} --now {now} --stdout"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=ssh_timeout)
            out["hosts"][h] = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip().startswith("{") else {"error": f"rc={r.returncode} {r.stderr[-300:]}"}
        except (subprocess.TimeoutExpired, ValueError, OSError) as e:
            out["hosts"][h] = {"error": str(e)[:300]}
    return out


# ------------------------------------------------------------------------------------------------------------ valid keys
def build_valid_keys(root=None):
    """{cat_side: {key: {"tabs": [...], "values": [native...], "filter": bool}}} from the four TEMPLATE_* files (rows + yellow
    filter headers), every value through tools/switch_types.coerce (strict) — refused values never enter the reference."""
    root = Path(root or ROOT)
    sys.path.insert(0, str(root))
    from tools import switch_types as ST
    from tools import v17_combo_engine as CE
    out = {}
    for cs in CAT_SIDES:
        venue = cs.split("_")[0]
        tpl = CE.read_template(cs)
        keys = {}

        def add(k, raw, tab, is_filter):
            ok, val, _why = ST.coerce(k, raw, venue, strict_unknown=True)
            if not ok:
                return
            e = keys.setdefault(k, {"tabs": [], "values": [], "filter": is_filter})
            if tab and tab not in e["tabs"]:
                e["tabs"].append(tab)
            if all(not ST.same_value(val, x) for x in e["values"]):
                e["values"].append(val)
        for tab, sw, raw in tpl["rows"]:
            if raw in ("", None):
                continue
            add(sw, raw, tab, False)
        for tab, hdrs in tpl["headers"].items():
            for h in hdrs:
                f, _, o = h.partition("=")
                add(f.strip(), o.strip(), tab, True)
        for k, vals in CE.AIR_FAMILY.items():
            for v in vals:
                add(k, v, "AUGMENT_TREND", False)
        add("AUGMENT_INTO_REENTRY_ENABLED", True, "AUGMENT_TREND", False)
        add("AUGMENT_INTO_REENTRY_ENABLED", False, "AUGMENT_TREND", False)
        out[cs] = {"template": tpl["template"], "md5": tpl["md5"], "keys": keys}
    return out


def _fmt_v(v):
    if isinstance(v, bool):
        return "T" if v else "F"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e9:
        return str(int(v))
    return str(v)


def keyref_text(vk, priority_keys=(), cap=KEYREF_CAP):
    """compact LLM reference: 'KEY: v1|v2 {CL,CS}' grouped by tab, evidence keys first, then FOCUS_TABS order; capped."""
    allk = {}
    for cs, d in vk.items():
        for k, e in d["keys"].items():
            a = allk.setdefault(k, {"cs": [], "vals": [], "tab": (e["tabs"] or ["FILTER"])[0], "filter": e["filter"]})
            a["cs"].append(CS_ABBR[cs])
            for v in e["values"]:
                if _fmt_v(v) not in a["vals"]:
                    a["vals"].append(_fmt_v(v))
    lines = ["# VALID KEYS (only these keys/values may appear in overrides_delta; {..} = cat_sides where valid, none = all 4; T/F = True/False)"]
    used = set()

    def line(k):
        a = allk[k]
        cs = "" if len(a["cs"]) == 4 else "{" + ",".join(a["cs"]) + "}"
        vals = a["vals"][:10]
        return f"{k}: {'|'.join(vals)}{'|..' if len(a['vals']) > 10 else ''} {cs}".rstrip()
    pk = [k for k in priority_keys if k in allk]
    if pk:
        lines.append("## keys in today's evidence")
        for k in pk:
            if k not in used:
                lines.append(line(k))
                used.add(k)
    for tab in FOCUS_TABS:
        ks = sorted(k for k, a in allk.items() if a["tab"] == tab and not a["filter"] and k not in used)
        if not ks:
            continue
        lines.append(f"## {tab}")
        for k in ks:
            lines.append(line(k))
            used.add(k)
    txt = ""
    for ln in lines:
        if len((txt + ln + "\n").encode()) > cap - 120:
            txt += f"# ... truncated at {cap // 1024} KB; {len(allk) - len(used)} more keys exist (filters, other tabs) — validator still accepts any template key\n"
            break
        txt += ln + "\n"
    return txt


# ---------------------------------------------------------------------------------------------------------------- packet
def _tbl(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def _agg_cells(a):
    return [a["n"], a["closed"], a["win_pct"], a["pnl_pct"], a["entry_vs_ext"], a["entry_bars"], a["exit_vs_ext"], a["exit_bars"], a["mfe"], a["giveback"], a["hold"], a["size_ratio"]]


AGG_HEAD = ["n", "closed", "win%", "pnl%", "entry_vs_ext%", "entry_bars", "exit_vs_ext%", "exit_bars", "mfe%", "giveback%", "hold_bars", "peak/first"]


def summarize_bt(bt):
    boards, seen = [], set()
    for h in (bt.get("hosts") or {}).values():
        for b in (h.get("boards") or []) if isinstance(h, dict) else []:
            k = (b.get("sym_side"), b.get("done_utc"), b.get("final"))
            if k in seen:  # the same board mirrored into several run/import dirs or hosts
                continue
            seen.add(k)
            boards.append(b)
    phases, prom, keys, starts, cs_fin, symp, exits, bysym = {}, {}, {}, {}, {}, {}, {}, {}
    for b in boards:
        cs = "_".join(["STOCKS" if not re.search(r"USD[CT]?_", b["sym_side"] + "_") else "CRYPTO", b["sym_side"].rsplit("_", 1)[-1]])
        b["cat_side"] = cs
        for p, e in (b.get("phases") or {}).items():
            x = phases.setdefault(p, {"boards": 0, "lift": 0.0, "pos": 0, "evals": 0, "wall_s": 0.0})
            x["boards"] += 1
            x["lift"] += e["lift"]
            x["pos"] += 1 if e["lift"] > 1e-9 else 0
            x["evals"] += e["evals"]
            x["wall_s"] += e["wall_s"]
        for pr in b.get("promotions") or []:
            k = (cs, " + ".join(pr["parts"]))
            x = prom.setdefault(k, {"n": 0, "d": [], "tim": [], "dd": [], "tr": [], "phase": set()})
            x["n"] += 1
            x["d"].append(pr["delta"])
            x["tim"].append(pr["tim"])
            x["dd"].append(pr["dd"])
            x["tr"].append(_num(pr["trades"]))
            x["phase"].add(str(pr["phase"]))
            for part in pr["parts"]:
                kk = part.split("=", 1)[0].strip()
                keys[kk] = keys.get(kk, 0) + 1
        s = b.get("best_start") or "?"
        starts[(cs, s)] = starts.get((cs, s), 0) + 1
        if b.get("final") is not None and b.get("bh") is not None:
            x = cs_fin.setdefault(cs, {"n": 0, "above": 0, "edge": [], "final": [], "lift": []})
            x["n"] += 1
            x["above"] += 1 if b["final"] > b["bh"] else 0
            x["edge"].append(b["final"] - b["bh"])
            x["final"].append(b["final"])
            if b.get("start") is not None:
                x["lift"].append(b["final"] - b["start"])
        for sg in b.get("surgeon") or []:
            for k, v in (sg.get("symptoms") or {}).items():
                x = symp.setdefault((cs, k), {"boards": 0, "n": 0, "size": 0.0})
                if (v.get("n") or 0) > 0:
                    x["boards"] += 1
                    x["n"] += int(v.get("n") or 0)
                    x["size"] += float(v.get("size") or 0.0)
            for k, v in (sg.get("by_symptom") or {}).items():
                x = bysym.setdefault((cs, k), {"tried": 0, "pos": 0})
                x["tried"] += int(v.get("tried") or 0)
                x["pos"] += int(v.get("pos") or 0)
            for rsn, n in sg.get("exits") or []:
                kk = (cs, path_label(rsn))
                exits[kk] = exits.get(kk, 0) + int(n or 0)
    return boards, phases, prom, keys, starts, cs_fin, symp, exits, bysym


def build_packet(live, bt, date, top=12):
    """markdown packet (dense tables, top-N) + its JSON twin. Shrinks top-N until <= PACKET_CAP."""
    agg = aggregate_live(live) if live else {}
    boards, phases, prom, keys, starts, cs_fin, symp, exits, bysym = summarize_bt(bt or {})
    libs = {h: (x or {}).get("library") or {} for h, x in ((bt or {}).get("hosts") or {}).items() if isinstance(x, dict)}
    data = {"date": date, "live": {"generated_utc": (live or {}).get("generated_utc"), "n_trades": (live or {}).get("n_trades"), "n_klines_missing": (live or {}).get("n_klines_missing"), "dispatch_24h": (live or {}).get("dispatch_24h"), "agg": agg}, "backtest": {"generated_utc": (bt or {}).get("generated_utc"), "n_boards": len(boards), "hosts": {h: {"n_files": (x or {}).get("n_files"), "error": (x or {}).get("error")} for h, x in ((bt or {}).get("hosts") or {}).items()}, "phases": phases, "final_vs_bh": {cs: {"n": x["n"], "above_bh": x["above"], "mean_edge": _r(_mean(x["edge"]), 3), "mean_final": _r(_mean(x["final"]), 3), "mean_lift_vs_start": _r(_mean(x["lift"]), 3)} for cs, x in cs_fin.items()}, "best_start": {f"{a}|{b}": n for (a, b), n in starts.items()}, "promotion_key_freq": dict(sorted(keys.items(), key=lambda kv: -kv[1])[:80]), "libraries": libs, "ab": (bt or {}).get("ab") or []}}
    for n in (top, 10, 8, 6, 4, 3, 2):
        md = _packet_md(date, live, agg, boards, phases, prom, starts, cs_fin, symp, exits, bysym, libs, bt, n)
        if len(md.encode()) <= PACKET_CAP:
            break
    if len(md.encode()) > PACKET_CAP:
        md = md.encode()[: PACKET_CAP - 80].decode(errors="ignore") + "\n\n[PACKET TRUNCATED AT HARD CAP]\n"
    data["top_n"] = n
    data["packet_bytes"] = len(md.encode())
    return md, data


def _packet_md(date, live, agg, boards, phases, prom, starts, cs_fin, symp, exits, bysym, libs, bt, n):
    L = [f"# DAILY LEARN PACKET {date} (UTC)", "All numbers are real ledger fills / real v12_quick_engine evals copied from progress JSONs + combo library. One-day samples -> DIAGNOSTIC ONLY. No Sharpe here.", "Conventions: entry_vs_ext% = distance of the first fill from the local LOW (LONG) / HIGH (SHORT) within +-16 x 15m bars; entry_bars = bars entry came AFTER that extreme (<0 = before it, i.e. knife). exit_vs_ext% = distance of the last exit from the local HIGH (LONG) / LOW (SHORT); exit_bars >0 = exited after the top (gave back), <0 = before (premature). giveback% = MFE - realised move. peak/first = peak position cost / first meaningful fill cost (>=10% of peak; dust starters skipped). HL = add whose prior-16-bar low was ABOVE the trade's entry low (LONG; SHORT: lower high).", ""]
    if live:
        L.append(f"## A. LIVE TRADES (ledger {live.get('generated_utc')}, {live.get('n_trades')} lifecycles in 7d, klines missing for {live.get('n_klines_missing')} / not covering the entry for {live.get('n_klines_stale')} symbols)")
        for h in ("24h", "168h"):
            g = agg.get(h) or {}
            if not g:
                continue
            L.append(f"### A.{h} by cat_side")
            rows = [[k] + _agg_cells(v) for k, v in (g.get("cat_side") or {}).items()]
            rows.append(["ALL"] + _agg_cells(g["all"]))
            L.append(_tbl(["cat_side"] + AGG_HEAD, rows))
            for dim in ("entry_path", "exit_path", "regime"):
                items = [(k, v) for k, v in (g.get(dim) or {}).items() if v["n"] >= (2 if h == "168h" else 1)][:n]
                if items:
                    L.append(f"### A.{h} by {dim} (top {n} by n)")
                    L.append(_tbl([dim] + AGG_HEAD, [[k] + _agg_cells(v) for k, v in items]))
            if g.get("adds"):
                L.append(f"### A.{h} adds: higher-low (LONG) / lower-high (SHORT) vs not")
                L.append(_tbl(["cat_side|HL|type", "n", "fwd%_to_exit", "win%", "size_vs_first", "px_vs_avg%"], [[k, v["n"], v["fwd_pct"], v["win_pct"], v["size_vs_first"], v["vs_avg_pct"]] for k, v in g["adds"].items()]))
            if g.get("missed_augment"):
                L.append(f"### A.{h} missed augments (0 augments, in-trade HL pivot then >=1% run)")
                L.append(_tbl(["cat_side", "missed", "of_trades", "mean_run%"], [[k, v["n"], v["of_trades"], v["run_pct"]] for k, v in g["missed_augment"].items()]))
        tr7 = [t for t in live.get("trades") or [] if t.get("closed")]
        worst_gb = sorted([t for t in tr7 if t.get("giveback_pct") is not None], key=lambda t: -t["giveback_pct"])[:n]
        if worst_gb:
            L.append(f"### A.worst giveback (7d top {n})")
            L.append(_tbl(["sym_side", "acct", "entry_path", "exit_path", "mfe%", "exit_move%", "giveback%", "exit_bars", "hold", "regime"], [[t["sym_side"], t["acct"], t["entry_path"], t.get("exit_path"), t.get("mfe_pct"), t.get("exit_move_pct"), t["giveback_pct"], t.get("exit_bars_from_extreme"), t.get("hold_bars"), t.get("regime")] for t in worst_gb]))
        worst_en = sorted([t for t in live.get("trades") or [] if t.get("entry_vs_extreme_pct") is not None], key=lambda t: -t["entry_vs_extreme_pct"])[:n]
        if worst_en:
            L.append(f"### A.worst entries vs local extreme (7d top {n})")
            L.append(_tbl(["sym_side", "acct", "entry_path", "entry_vs_ext%", "entry_bars", "pnl%", "mae%", "regime"], [[t["sym_side"], t["acct"], t["entry_path"], t["entry_vs_extreme_pct"], t.get("entry_bars_from_extreme"), t.get("pnl_pct"), t.get("mae_pct"), t.get("regime")] for t in worst_en]))
        best = sorted([t for t in tr7 if t.get("pnl_pct") is not None], key=lambda t: -t["pnl_pct"])[: max(3, n // 2)]
        if best:
            L.append("### A.best trades (7d)")
            L.append(_tbl(["sym_side", "entry_path", "exit_path", "pnl%", "entry_vs_ext%", "exit_vs_ext%", "n_adds", "peak/first", "regime"], [[t["sym_side"], t["entry_path"], t.get("exit_path"), t["pnl_pct"], t.get("entry_vs_extreme_pct"), t.get("exit_vs_extreme_pct"), t["n_adds"], t.get("size_ratio_peak_first"), t.get("regime")] for t in best]))
        dsp = live.get("dispatch_24h") or {}
        if dsp:
            L.append("### A.decision log 24h (VEC_EXACT share)")
            L.append(_tbl(["acct", "decisions", "vec_exact", "top actions"], [[a, d["n"], d["vec_exact"], ", ".join(f"{k}:{v}" for k, v in sorted(d["by_action"].items(), key=lambda kv: -kv[1])[:4])] for a, d in sorted(dsp.items())]))
    L.append("")
    hs = ", ".join("%s:%s" % (h, (x or {}).get("n_files", (x or {}).get("error"))) for h, x in ((bt or {}).get("hosts") or {}).items())
    L.append(f"## B. BACKTESTS (fleet progress JSONs touched in 24h: {len(boards)} boards; hosts {hs})")
    if phases:
        L.append("### B.v17 phase lift (sum over boards; lift = gain points added by the phase)")
        L.append(_tbl(["phase", "boards", "boards_lift>0", "sum_lift", "mean_lift", "evals", "wall_min"], [[p, x["boards"], x["pos"], _r(x["lift"], 2), _r(x["lift"] / max(1, x["boards"]), 3), x["evals"], _r(x["wall_s"] / 60.0, 1)] for p, x in sorted(phases.items(), key=lambda kv: -kv[1]["lift"])]))
    if cs_fin:
        L.append("### B.final vs buy&hold (30D)")
        L.append(_tbl(["cat_side", "boards", "above_bh", "mean final", "mean final-bh", "mean final-start"], [[cs, x["n"], x["above"], _r(_mean(x["final"]), 2), _r(_mean(x["edge"]), 2), _r(_mean(x["lift"]), 2)] for cs, x in sorted(cs_fin.items())]))
    if starts:
        L.append("### B.best_start choice")
        L.append(_tbl(["cat_side", "chosen", "boards"], [[a, b, c] for (a, b), c in sorted(starts.items(), key=lambda kv: -kv[1])[: n + 4]]))
    if prom:
        items = sorted(prom.items(), key=lambda kv: (-kv[1]["n"], -(_mean(kv[1]["d"]) or 0)))[: n * 2]
        L.append(f"### B.adopted paths (v17 promotions; top {n * 2})")
        L.append(_tbl(["cat_side", "path", "boards", "mean_delta", "tim", "dd", "trades", "phase"], [[cs, p[:90], x["n"], _r(_mean(x["d"]), 3), _r(_mean(x["tim"]), 1), _r(_mean(x["dd"]), 2), _r(_mean(x["tr"]), 0), ",".join(sorted(x["phase"]))[:30]] for (cs, p), x in items]))
    top_b = sorted([b for b in boards if b.get("final") is not None], key=lambda b: -(b["final"] - (b.get("bh") or 0)))
    if top_b:
        L.append(f"### B.boards best / worst edge vs bh (top {n // 2 + 1} each)")
        sel = top_b[: n // 2 + 1] + top_b[-(n // 2 + 1):] if len(top_b) > n + 2 else top_b
        L.append(_tbl(["sym_side", "start", "final", "bh", "trades", "tier", "best_start", "regime_now", "g365"], [[b["sym_side"], b.get("start"), b["final"], b.get("bh"), b.get("trades"), b.get("tier"), b.get("best_start"), b.get("regime_now"), b.get("g365")] for b in sel]))
    if symp:
        L.append("### B.surgeon symptoms (boards with symptom, total n, total size) + fixes found")
        L.append(_tbl(["cat_side|symptom", "boards", "n", "size", "tried", "pos"], [[f"{cs}|{k}", x["boards"], x["n"], _r(x["size"], 2), (bysym.get((cs, k)) or {}).get("tried"), (bysym.get((cs, k)) or {}).get("pos")] for (cs, k), x in sorted(symp.items(), key=lambda kv: -kv[1]["size"])[: n + 4]]))
    if exits:
        L.append("### B.backtest top exit reasons (surgeon ledgers)")
        L.append(_tbl(["cat_side|exit", "n"], [[f"{a}|{b}", c] for (a, b), c in sorted(exits.items(), key=lambda kv: -kv[1])[: n + 2]]))
    lib_seen = {}
    for h, lib in libs.items():
        if not lib or lib.get("missing"):
            continue
        sig = json.dumps([lib.get("origins_24h"), lib.get("top_combos_24h")], default=str, sort_keys=True)
        if sig in lib_seen:
            L.append(f"### B.combo library ({h}): identical to {lib_seen[sig]} (merged library) — not repeated")
            continue
        lib_seen[sig] = h
        og = [o for o in lib.get("origins_24h") or [] if o["w"] == 30][: n + 2]
        if og:
            L.append(f"### B.combo engine origins 24h ({h})")
            L.append(_tbl(["source", "origin", "evals", "pos", "pos%", "mean_pos_delta"], [[o["source"], o["origin"], o["n"], o["pos"], _r(100.0 * o["pos"] / max(1, o["n"]), 1), o["mean_pos_delta"]] for o in og]))
        tc = (lib.get("top_combos_24h") or [])[:n]
        if tc:
            L.append(f"### B.top new combo evidence 24h ({h}; positive, valid; by boards then delta)")
            L.append(_tbl(["cat_side", "w", "combo", "boards", "mean_d", "max_d", "tim", "dd", "trades"], [[c["cat_side"], c["w"], json.dumps(c["combo"], default=str, separators=(",", ":"))[:110], c["n_boards"], c["mean_delta"], c["max_delta"], c["tim"], c["dd"], c["trades"]] for c in tc]))
        io = lib.get("insight_outcomes") or []
        if io:
            L.append(f"### B.YESTERDAY'S INSIGHTS — outcome in the real engine ({h})")
            L.append(_tbl(["insight_id", "cat_side", "status", "evals", "boards", "d30_mean", "d30_max", "pos&gated-ok", "d365_mean", "adopted"], [[x["insight_id"], x["cat_side"], x["status"], x["n"], x["boards"], x["d30_mean"], x["d30_max"], x["pos_gated"], x["d365_mean"], x["adopted"]] for x in io[: n * 3]]))
    ab = (bt or {}).get("ab") or []
    if ab:
        L.append("### B.V17 A/B (latest reports, 24h)")
        L.append(_tbl(["sym_side", "arm", "gain", "bh", "trades", "TIM", "DD", "365D"], [[a["sym_side"], a["arm"], a["gain"], a["bh"], a["trades"], a["tim"], a["dd"], a["g365"]] for a in ab[: n * 2]]))
    return "\n".join(L) + "\n"


def priority_keys_from(bt):
    ks = []
    for h in ((bt or {}).get("hosts") or {}).values():
        if not isinstance(h, dict):
            continue
        for b in h.get("boards") or []:
            for pr in b.get("promotions") or []:
                for p in pr.get("parts") or []:
                    ks.append(p.split("=", 1)[0].strip())
        for c in ((h.get("library") or {}).get("top_combos_24h") or []):
            ks.extend(str(k) for k in (c.get("combo") or {}))
    seen, out = set(), []
    for k in ks:
        if k not in seen and k != "FILTER_EXEMPTIONS":
            seen.add(k)
            out.append(k)
    return out[:150]


# ------------------------------------------------------------------------------------------------------------- validator
def _in_span(val, vals):
    nums = [float(x) for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if len(nums) < 2 or not isinstance(val, (int, float)) or isinstance(val, bool):
        return False
    return min(nums) - 1e-12 <= float(val) <= max(nums) + 1e-12


def validate_insights(doc, vk, date=None):
    """-> (clean_doc, report). Every candidate_paths[].overrides_delta key must be a TEMPLATE key of that cat_side and its value
    must coerce (tools/switch_types, strict) AND equal a template candidate value (numeric: inside the template span). Invalid
    keys are DROPPED (listed in dropped_keys); a path left empty is dropped. ABLATION_* = True and MAX_AUGMENTS_PER_POSITION = 0
    are refused. Rules text is kept (strings only, truncated)."""
    sys.path.insert(0, str(ROOT))
    from tools import switch_types as ST
    rep = {"paths_in": 0, "paths_out": 0, "keys_in": 0, "keys_out": 0, "dropped": []}
    clean = {}
    for k in ("entry_timing_rules", "exit_timing_rules", "higher_low_sizing_rules", "surgeon_symptom_rules", "retire"):
        v = (doc or {}).get(k) or []
        if not isinstance(v, list):
            v = [v]
        clean[k] = [(x if isinstance(x, str) else json.dumps(x, default=str))[:600] for x in v][:25]
    paths = (doc or {}).get("candidate_paths") or []
    out = []
    for i, p in enumerate(paths if isinstance(paths, list) else []):
        rep["paths_in"] += 1
        if not isinstance(p, dict):
            rep["dropped"].append({"i": i, "why": "not an object"})
            continue
        cs = str(p.get("cat_side") or "").upper().strip()
        if cs not in vk:
            rep["dropped"].append({"i": i, "why": f"cat_side {cs!r}"})
            continue
        ss = p.get("sym_side")
        if ss not in (None, "", "null"):
            ss = str(ss).upper().strip()
            if not re.fullmatch(r"[A-Z0-9.]+_(LONG|SHORT)", ss) or not ss.endswith(cs.split("_")[1]):
                rep["dropped"].append({"i": i, "why": f"sym_side {ss!r} vs {cs}"})
                ss = None
        else:
            ss = None
        reg = p.get("regime")
        reg = str(reg).strip().upper()[:40] if reg not in (None, "", "null") else None
        delta = p.get("overrides_delta") or {}
        if not isinstance(delta, dict):
            rep["dropped"].append({"i": i, "why": "overrides_delta not a dict"})
            continue
        keys = vk[cs]["keys"]
        venue = cs.split("_")[0]
        good, dropped = {}, []
        for k, v in delta.items():
            rep["keys_in"] += 1
            k = str(k).strip()
            if k not in keys:
                dropped.append({"key": k, "value": v, "why": "not a template key for " + cs})
                continue
            ok, val, why = ST.coerce(k, v, venue, strict_unknown=True)
            if not ok:
                dropped.append({"key": k, "value": v, "why": why[:120]})
                continue
            if k.startswith("ABLATION_") and val is True:
                dropped.append({"key": k, "value": v, "why": "ABLATION_NEVER_ON"})
                continue
            if k == "MAX_AUGMENTS_PER_POSITION" and isinstance(val, (int, float)) and float(val) == 0.0:
                dropped.append({"key": k, "value": v, "why": "MAXAUG_NEVER_ZERO"})
                continue
            cands = keys[k]["values"]
            if not (any(ST.same_value(val, c) for c in cands) or _in_span(val, cands)):
                dropped.append({"key": k, "value": v, "why": f"value not a template candidate ({'|'.join(_fmt_v(c) for c in cands[:8])})"})
                continue
            good[k] = val
        if len(good) > 4:
            for k in list(good)[4:]:
                dropped.append({"key": k, "value": good.pop(k), "why": "more than 4 keys (engine cap)"})
        if dropped:
            rep["dropped"].append({"i": i, "cat_side": cs, "keys": dropped})
        if not good:
            continue
        rep["keys_out"] += len(good)
        out.append({"insight_id": f"{date or 'x'}-{len(out):02d}", "date": date, "cat_side": cs, "sym_side": ss, "regime": reg, "overrides_delta": good, "why": str(p.get("why") or "")[:400], "evidence_refs": [str(x)[:120] for x in (p.get("evidence_refs") or [])][:8] if isinstance(p.get("evidence_refs"), list) else [str(p.get("evidence_refs"))[:120]], "dropped_keys": [d["key"] for d in dropped]})
    rep["paths_out"] = len(out)
    clean["candidate_paths"] = out
    clean["date"] = date
    clean["validated_utc"] = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    clean["validation"] = {k: rep[k] for k in ("paths_in", "paths_out", "keys_in", "keys_out")}
    return clean, rep


def parse_llm_result(text):
    """(raw_insights_dict | None, markdown) from the model's PART 1 json block + PART 2 after ---MD---."""
    text = str(text or "")
    md = text.split("---MD---", 1)[1].strip() if "---MD---" in text else ""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    cand = m.group(1) if m else None
    if cand is None:
        i, j = text.find("{"), text.rfind("}")
        cand = text[i: j + 1] if 0 <= i < j else None
    if cand is None:
        return None, md
    try:
        d = json.loads(cand)
        return (d if isinstance(d, dict) else None), md
    except ValueError:
        return None, md


def ingest(claude_json, date, vk, model=None, budget=None):
    """claude -p --output-format json result -> insights_<date>.raw.json, insights_<date>.json (validated), insights_<date>.md,
    cost_log.jsonl row. Returns the summary dict."""
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        res = json.loads(Path(claude_json).read_text())
    except (OSError, ValueError) as e:
        res = {"is_error": True, "result": "", "error": f"unreadable CLI output: {e}"}
    if isinstance(res, list):  # stream form -> last result object
        res = next((x for x in reversed(res) if isinstance(x, dict) and x.get("type") == "result"), {})
    cost = {"ts": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"), "date": date, "model": model, "budget_usd": budget, "total_cost_usd": res.get("total_cost_usd"), "is_error": res.get("is_error"), "subtype": res.get("subtype"), "num_turns": res.get("num_turns"), "duration_ms": res.get("duration_ms"), "usage": {k: (res.get("usage") or {}).get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}, "model_usage": {k: {kk: v.get(kk) for kk in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "costUSD")} for k, v in (res.get("modelUsage") or {}).items() if isinstance(v, dict)}, "permission_denials": len(res.get("permission_denials") or [])}
    raw, md = parse_llm_result(res.get("result"))
    out = {"date": date, "cost_usd": cost["total_cost_usd"], "is_error": bool(res.get("is_error")), "parsed": raw is not None}
    if raw is not None:
        _write_atomic(LEARN_DIR / f"insights_{date}.raw.json", json.dumps(raw, indent=1, default=str))
        clean, rep = validate_insights(raw, vk, date)
        clean["llm"] = {"model": model, "cost_usd": cost["total_cost_usd"], "session_turns": res.get("num_turns")}
        _write_atomic(LEARN_DIR / f"insights_{date}.json", json.dumps(clean, indent=1, default=str))
        _write_atomic(LEARN_DIR / f"insights_{date}.validation.json", json.dumps(rep, indent=1, default=str))
        out.update({k: rep[k] for k in ("paths_in", "paths_out", "keys_in", "keys_out")})
        cost["paths_in"], cost["paths_out"] = rep["paths_in"], rep["paths_out"]
    hdr = f"# DAILY LEARN INSIGHTS {date}\n\nmodel {model} · cost ${cost['total_cost_usd']} · candidate paths valid {out.get('paths_out')}/{out.get('paths_in')} (validator: tools/v17_daily_learn_prep.py validate_insights + tools/switch_types.coerce). Proposals are TESTS for tools/v17_combo_engine.py (origin daily_insight), never applied live directly.\n\n"
    _write_atomic(LEARN_DIR / f"insights_{date}.md", hdr + (md or "(no markdown part returned)") + "\n")
    with open(LEARN_DIR / "cost_log.jsonl", "a") as fh:
        fh.write(json.dumps(cost, default=str) + "\n")
    return out


# ------------------------------------------------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leg", required=True, choices=("live", "backtest", "backtest-host", "packet", "validate", "valid-keys", "ingest"))
    ap.add_argument("--date", default=_utc_now().strftime("%Y%m%d"))
    ap.add_argument("--now", type=float, default=None)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--hosts", default="local,10.0.0.4")
    ap.add_argument("--out", default=None)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--live-json", default=None)
    ap.add_argument("--bt-json", default=None)
    ap.add_argument("--insights-raw", default=None)
    ap.add_argument("--valid-keys", default=None)
    ap.add_argument("--claude-json", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--budget", type=float, default=None)
    a = ap.parse_args(argv)
    now = a.now or time.time()
    if a.leg == "ingest":
        vk = json.loads(Path(a.valid_keys or LEARN_DIR / f"valid_keys_{a.date}.json").read_text())
        res = ingest(a.claude_json, a.date, vk, a.model, a.budget)
        print(json.dumps(res, default=str))
        return 0 if res.get("paths_out") else 4
    if a.leg == "live":
        res = collect_live(now=now, days=a.days)
        out = Path(a.out or LEARN_DIR / f"live_leg_{a.date}.json")
        _write_atomic(out, json.dumps(res, default=str))
        print(json.dumps({"leg": "live", "out": str(out), "trades": res["n_trades"], "files": res["n_files"], "events": res["n_events"], "klines_missing": res["n_klines_missing"]}))
        return 0
    if a.leg == "backtest-host":
        res = collect_bt_host(now=now, hours=a.hours)
        txt = json.dumps(res, default=str)
        if a.stdout:
            sys.stdout.write(txt)
        else:
            out = Path(a.out or LEARN_DIR / f"bt_host_{socket.gethostname()}_{a.date}.json")
            _write_atomic(out, txt)
            print(json.dumps({"leg": "backtest-host", "out": str(out), "boards": len(res["boards"])}))
        return 0
    if a.leg == "backtest":
        res = collect_backtest(hosts=[h.strip() for h in a.hosts.split(",") if h.strip()], now=now, hours=a.hours)
        out = Path(a.out or LEARN_DIR / f"bt_leg_{a.date}.json")
        _write_atomic(out, json.dumps(res, default=str))
        print(json.dumps({"leg": "backtest", "out": str(out), "hosts": {h: (len(x.get("boards") or []) if isinstance(x, dict) and "boards" in x else x.get("error")) for h, x in res["hosts"].items()}, "ab_rows": len(res["ab"])}))
        return 0
    if a.leg == "valid-keys":
        vk = build_valid_keys()
        out = Path(a.out or LEARN_DIR / f"valid_keys_{a.date}.json")
        _write_atomic(out, json.dumps(vk, default=str))
        print(json.dumps({"out": str(out), "keys": {cs: len(d["keys"]) for cs, d in vk.items()}}))
        return 0
    if a.leg == "packet":
        live = json.loads(Path(a.live_json or LEARN_DIR / f"live_leg_{a.date}.json").read_text()) if Path(a.live_json or LEARN_DIR / f"live_leg_{a.date}.json").exists() else None
        btp = Path(a.bt_json or LEARN_DIR / f"bt_leg_{a.date}.json")
        bt = json.loads(btp.read_text()) if btp.exists() else None
        md, data = build_packet(live, bt, a.date)
        vk = build_valid_keys()
        kref = keyref_text(vk, priority_keys_from(bt))
        LEARN_DIR.mkdir(parents=True, exist_ok=True)
        _write_atomic(LEARN_DIR / f"daily_packet_{a.date}.md", md)
        _write_atomic(LEARN_DIR / f"daily_packet_{a.date}.json", json.dumps(data, default=str))
        _write_atomic(LEARN_DIR / f"valid_keys_{a.date}.json", json.dumps(vk, default=str))
        _write_atomic(LEARN_DIR / f"valid_keys_{a.date}.txt", kref)
        print(json.dumps({"leg": "packet", "packet_md_bytes": len(md.encode()), "top_n": data["top_n"], "valid_keys_txt_bytes": len(kref.encode()), "valid_keys": {cs: len(d["keys"]) for cs, d in vk.items()}, "live_trades": (live or {}).get("n_trades"), "bt_boards": data["backtest"]["n_boards"]}))
        return 0 if len(md.encode()) <= PACKET_CAP else 3
    if a.leg == "validate":
        vk = json.loads(Path(a.valid_keys or LEARN_DIR / f"valid_keys_{a.date}.json").read_text())
        raw = json.loads(Path(a.insights_raw).read_text())
        clean, rep = validate_insights(raw, vk, a.date)
        out = Path(a.out or LEARN_DIR / f"insights_{a.date}.json")
        _write_atomic(out, json.dumps(clean, indent=1, default=str))
        print(json.dumps({"leg": "validate", "out": str(out), **{k: rep[k] for k in ("paths_in", "paths_out", "keys_in", "keys_out")}, "n_dropped_entries": len(rep["dropped"])}))
        return 0 if clean["candidate_paths"] else 4
    return 2


if __name__ == "__main__":
    sys.exit(main())
