"""discrepancy_ledger — one SQL row for EVERY place the real trading scripts and the vectorized script disagree.

USER 2026-10-18: "keep a ledger/sql of EVERY discrepancy where the REAL trading scripts WOULD have traded something else than
the vectorized script so we can make a daily evaluation of what is wrong in vectorized or in live and what would have been
gained if we would have ignored vectorized."

SQLite at data/discrepancy/discrepancy.db (ext-drive archive by tools/v15_history_archive.sh conventions). ONE table, one row
per (class, account, sym_side, bar, fingerprint), idempotent — re-ingesting the same logs never duplicates.

CLASSES (each is a different fault, so the daily report can say what is wrong WHERE):
  GATE_REFUSED           live decided an order the vector never decided; the parity gate refused it (paper_nonvec).
                         -> "what live WOULD have traded". Counterfactual: forward return from the refusal price.
  LIVE_ONLY_FILL         a live fill whose reason carries no |VEC_EXACT tag: live traded without the vector.
                         -> realised P&L of that trip IS the answer to "what did ignoring the vector gain".
  PROMISE_LATE           a vec act delivered after its own bar: same decision, worse price.
                         -> cost = (price live got - price the vector booked) x side.
  PROMISE_NOT_DELIVERED  a vec act with no delivery at all (and live state still needed it).
  EXEC_SKIPPED_INFLIGHT  a vec act refused by the single-flight dispatch guard (SKIPPED_VX_INFLIGHT).
  SWEEP_TIMEOUT          the key was never evaluated in that sweep (vec_dispatch BLOCKED_DISPATCH_TIMEOUT): the vector had no
                         chance to decide at all. No price -> counted, never priced.
  BAR_MISMATCH           matched trips whose entry/exit bar differs (from tools/v15_daily_retrace.py).

Each row carries: when, which bar, account, sym_side, action/side, the live reason and the vec reason, prices, qty, and the
counterfactual fields the report fills in (`cf_pct`, `cf_usd`, `cf_horizon_bars`, `cf_basis`) so every daily number can be
traced back to the row that produced it. Writes are additive; `scored_at` marks a row whose counterfactual is computed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DISCREPANCY_DB") or (ROOT / "data" / "discrepancy" / "discrepancy.db"))
CLASSES = ("GATE_REFUSED", "LIVE_ONLY_FILL", "PROMISE_LATE", "PROMISE_NOT_DELIVERED", "EXEC_SKIPPED_INFLIGHT", "SWEEP_TIMEOUT", "BAR_MISMATCH")
SCHEMA = """
CREATE TABLE IF NOT EXISTS discrepancy (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fp            TEXT UNIQUE,
    class         TEXT NOT NULL,
    ts            REAL NOT NULL,
    day           TEXT NOT NULL,
    bar_ts        REAL,
    account       TEXT,
    sym_side      TEXT,
    venue         TEXT,
    action        TEXT,
    side          TEXT,
    who_wanted    TEXT,
    live_reason   TEXT,
    vec_reason    TEXT,
    live_price    REAL,
    vec_price     REAL,
    qty           REAL,
    notional      REAL,
    live_usd      REAL,
    lag_s         REAL,
    bars_off      INTEGER,
    cf_basis      TEXT,
    cf_horizon    INTEGER,
    cf_pct        REAL,
    cf_usd        REAL,
    verdict       TEXT,
    scored_at     REAL,
    source        TEXT,
    detail_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_disc_day ON discrepancy(day, class);
CREATE INDEX IF NOT EXISTS idx_disc_key ON discrepancy(sym_side, account, class);
CREATE INDEX IF NOT EXISTS idx_disc_unscored ON discrepancy(scored_at);
"""


def connect(path=None, read_only: bool = False):
    p = Path(path or DB_PATH)
    if read_only:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def fingerprint(cls, account, sym_side, bar_ts, extra="") -> str:
    raw = f"{cls}|{account}|{sym_side}|{int(float(bar_ts or 0))}|{extra}"
    return hashlib.md5(raw.encode()).hexdigest()


def add(con, cls: str, ts: float, account: str, sym_side: str, *, bar_ts=None, venue="crypto", action="", side="",
        who_wanted="", live_reason="", vec_reason="", live_price=None, vec_price=None, qty=None, notional=None,
        live_usd=None, lag_s=None, bars_off=None, source="", detail=None, fp_extra="") -> bool:
    """Insert one discrepancy. Returns True when it was new. Never raises (a ledger error may not cost a trade)."""
    try:
        if cls not in CLASSES:
            raise ValueError(f"unknown class {cls}")
        day = time.strftime("%Y-%m-%d", time.gmtime(float(ts)))
        fp = fingerprint(cls, account, sym_side, bar_ts if bar_ts else ts, fp_extra or action or live_reason[:40])
        cur = con.execute(
            "INSERT OR IGNORE INTO discrepancy (fp,class,ts,day,bar_ts,account,sym_side,venue,action,side,who_wanted,"
            "live_reason,vec_reason,live_price,vec_price,qty,notional,live_usd,lag_s,bars_off,source,detail_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fp, cls, float(ts), day, (float(bar_ts) if bar_ts else None), account, sym_side, venue, action, side, who_wanted,
             str(live_reason or "")[:200], str(vec_reason or "")[:200], live_price, vec_price, qty, notional, live_usd, lag_s,
             bars_off, source, json.dumps(detail or {}, default=str)[:2000]),
        )
        return cur.rowcount > 0
    except Exception:
        return False


def score(con, row_id: int, *, cf_basis: str, cf_pct=None, cf_usd=None, cf_horizon=None, verdict="") -> None:
    try:
        con.execute("UPDATE discrepancy SET cf_basis=?, cf_pct=?, cf_usd=?, cf_horizon=?, verdict=?, scored_at=? WHERE id=?",
                    (cf_basis, cf_pct, cf_usd, cf_horizon, verdict, time.time(), int(row_id)))
    except Exception:
        pass


def counts(con, day=None):
    q = "SELECT class, COUNT(*), SUM(COALESCE(live_usd,0)), SUM(COALESCE(cf_usd,0)) FROM discrepancy"
    args = ()
    if day:
        q += " WHERE day=?"
        args = (day,)
    return list(con.execute(q + " GROUP BY class ORDER BY 2 DESC", args))
