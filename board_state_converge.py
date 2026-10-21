"""USER 2026-10-21 BOARD-STATE CONVERGE (shared, import-light: stdlib only).

The forward NPZ paper test (pure-board lane) publishes per-bar state verdicts:
CONVERGE_OPEN (vector holds, live flat) / CONVERGE_CLOSE (vector flat, live
holds). This module is the read/filter/record half consumed by the _manage
loops. It places no orders; the manage loop executes through execute_now and
records, per forced bar, the reason live was not trading it plus the vector
leg live was ignoring (lane row `vec_leg`).

Ledger in : <BASE>/data/pure_board/intents_<YYYY-MM-DD>.jsonl
Records out: <BASE>/data/converge/converge_<acct>.jsonl
"""
from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path

CONVERGE_OPEN = "CONVERGE_OPEN"
CONVERGE_CLOSE = "CONVERGE_CLOSE"
SUPPORTED = (CONVERGE_OPEN, CONVERGE_CLOSE)
MAX_VERDICT_AGE_S = 1200.0
MAX_SNAPSHOT_AGE_S = 300.0
BAR_S = 900.0
REFUSAL_MARKERS = ("REFUS", "BLOCKED", "DENIED", "SKIPPED", "TIMEOUT", "STALE",
                   "INSUFFICIENT", "MINIMUM", "COOLDOWN", "GATED", "VETO", "REJECT", "ERROR")


class StaleInput(Exception):
    pass


def _read_jsonl(path):
    rows = []
    try:
        with open(path) as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return rows


def read_live_snapshots(root, account, max_age_s=MAX_SNAPSHOT_AGE_S, now=None):
    """What the account holds, from the position writer's own snapshots.

    Returns ({(acct, sym_side): {qty, entry, mark, opened_at}}, newest_mtime).
    Raises StaleInput when any side file is missing or older than max_age_s —
    the loop must refuse to converge on rotted state, never guess.
    """
    now = float(now if now is not None else time.time())
    out, mtimes = {}, []
    for side in ("long", "short"):
        fp = Path(root) / account / f"{side}_positions.json"
        try:
            mtime = os.path.getmtime(fp)
        except OSError:
            raise StaleInput(f"snapshot missing: {fp}")
        if now - mtime > max_age_s:
            raise StaleInput(f"snapshot rotted: {fp} age={now - mtime:.0f}s")
        mtimes.append(mtime)
        try:
            doc = json.load(open(fp)) or {}
        except Exception:
            raise StaleInput(f"snapshot unreadable: {fp}")
        for key, pos in doc.items():
            try:
                amt = abs(float((pos or {}).get("positionAmt") or 0.0))
            except Exception:
                continue
            if amt <= 0:
                continue
            ss = key.split(":", 1)[1] if ":" in key else key
            out[(account, ss)] = {"qty": amt,
                                  "entry": float((pos or {}).get("entry_price") or 0.0),
                                  "mark": float((pos or {}).get("mark_price") or 0.0),
                                  "opened_at": (pos or {}).get("opened_at")}
    return out, (max(mtimes) if mtimes else 0.0)


def fresh_verdicts(pure_board_dir, account, now=None, max_age_s=MAX_VERDICT_AGE_S):
    """Fresh CONVERGE rows for one account: decided recently AND on a recent bar."""
    now = float(now if now is not None else time.time())
    rows = []
    for fp in glob.glob(str(Path(pure_board_dir) / "intents_*.jsonl")):
        rows.extend(_read_jsonl(fp))
    out = []
    for r in rows:
        try:
            if r.get("acct") != account or r.get("action") not in SUPPORTED:
                continue
            if now - float(r.get("ts") or 0.0) > max_age_s:
                continue
            if now - (float(r.get("bar_ts") or 0.0) + BAR_S) > max_age_s:
                continue
            out.append(r)
        except Exception:
            continue
    out.sort(key=lambda r: (r.get("sym_side") or "", r.get("bar_ts") or 0.0))
    return out


def already_forced(record_path, account, sym_side, bar_ts):
    """True when this exact (key, bar) was already forced — no re-fire spam."""
    for r in _read_jsonl(record_path):
        try:
            if (r.get("acct") == account and r.get("sym_side") == sym_side
                    and abs(float(r.get("bar_ts") or -1) - float(bar_ts)) < 1.0):
                return True
        except Exception:
            continue
    return False


def diagnose(history_dir, account, sym_side, action, live_qty, vec_leg, now=None, hours=24):
    """Best-supported reason live is not trading it — observed facts only.

    Scans the key's own history for fills and refusal markers; never invents a
    cause. Returns one sentence citing the facts.
    """
    fills, refusals, last = [], [], ""
    try:
        fp = Path(history_dir) / account / f"{sym_side}.jsonl"
        cutoff = float(now if now is not None else time.time()) - hours * 3600.0
        for e in _read_jsonl(str(fp)):
            try:
                ts = e.get("ts")
                ets = float(ts) if isinstance(ts, (int, float)) else None
                if ets is None:
                    continue
                if ets < cutoff:
                    continue
                typ = str(e.get("type") or "").upper()
                reason = str(e.get("reason") or "")
                fills.append((ets, typ, reason))
                if any(m in (typ + " " + reason).upper() for m in REFUSAL_MARKERS):
                    refusals.append(reason[:100])
            except Exception:
                continue
    except Exception:
        pass
    fills.sort()
    nf = len(fills)
    last = f"{fills[-1][1]} {fills[-1][2][:60]}" if fills else "none"
    if refusals:
        why = f"live refused with {refusals[0]}"
    elif nf == 0:
        why = "no live fills/signals for this key in 24h (live strategy silent)"
    else:
        why = f"{nf} live fills/24h, last {last} (no live leg matching vector now)"
    if action == CONVERGE_OPEN:
        return (f"live flat qty={live_qty:.6f}; {why}; "
                f"vector HOLD leg ignored: {vec_leg or 'unlabeled'}")
    return (f"live holds qty={live_qty:.6f}; {why}; "
            f"vector FLAT leg ignored: {vec_leg or 'unlabeled'}")


def build_record(now, bar_ts, account, sym_side, action, vec_leg, board_md5,
                 engine_trades, engine_gain, live_qty, live_refusal, exec_result):
    return {"ts": now, "bar_ts": bar_ts, "acct": account, "sym_side": sym_side,
            "action": action, "vector_function": "v12 board-state HOLD/CLOSE via pure_board lane",
            "vec_leg": vec_leg or "", "board_md5": board_md5 or "",
            "engine_trades": engine_trades, "engine_gain": engine_gain,
            "live_qty_before": live_qty, "live_refusal": live_refusal,
            "exec_result": str(exec_result)[:160]}


def record_path(base_path, account):
    return str(Path(base_path) / "data" / "converge" / f"converge_{account}.jsonl")


def append_record(path, rec):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
    return path
