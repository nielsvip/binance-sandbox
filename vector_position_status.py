"""vector_position_status — ONE host-aware answer to "what does the vector hold?".

USER 2026-10-22: parity enforcers must run flawlessly on Mac-offline/S1-takeover,
and every script must know where to look for each sym_side's vector position
status. This module is that lookup. Rules:
  * local-first, no ssh in the hot path (on S1-takeover there is no Mac to ask);
  * host layout auto-detected (Mac repo vs S1 ~/binance live vs ~/binance-sandbox);
  * sources in priority order per sym_side: fresh pure-board CONVERGE verdicts
    (incl. S1-synced mirror) -> vec_live forward-hold state -> per_sym board set
    (authorized, not a position) -> nothing held;
  * returns dicts, never raises; stale/unknown reported as such, never invented.
"""
from __future__ import annotations

import glob
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

VERDICT_MAX_AGE_S = 1200.0
BAR_S = 900.0


def host_root() -> Path:
    """Repo root on either host: $BINANCE_ROOT wins, else cwd-of-module heuristics."""
    env = os.environ.get("BINANCE_ROOT")
    if env and Path(env).is_dir():
        return Path(env)
    here = Path(__file__).resolve()
    for cand in (here.parent, Path.cwd()):
        if (cand / "config.py").exists() or (cand / "config_tradier.py").exists():
            return cand
    return here.parent


def host_kind(root: Path = None) -> str:
    """mac | s1-live (~/binance) | s1-sandbox | unknown. Never raises."""
    try:
        r = Path(root) if root else host_root()
        parts = r.parts
        if "binance-sandbox" in parts:
            return "s1-sandbox"
        if r.name == "binance" and str(r).startswith("/home/niels"):
            return "s1-live"
        if "Documents" in parts or str(r).startswith("/Users/"):
            return "mac"
    except Exception:
        pass
    return "unknown"


def logs_dir(root: Path = None) -> Path:
    r = Path(root) if root else host_root()
    if (r / ".." / "logs").is_dir():
        pass
    home_logs = Path.home() / "logs"
    if home_logs.is_dir():
        return home_logs
    return r / ".." / "logs"


def _read_jsonl(fp):
    rows = []
    try:
        with open(fp, errors="replace") as fh:
            for ln in fh:
                if ln.strip().startswith("{"):
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        continue
    except OSError:
        pass
    return rows


def converge_verdicts(root: Path = None, account: str = None, now: float = None) -> dict:
    """(acct, sym_side) -> newest fresh CONVERGE verdict. Mac+synced-S1 rows."""
    r = Path(root) if root else host_root()
    now = float(now if now is not None else time.time())
    out: dict = {}
    for fp in glob.glob(str(r / "data" / "pure_board" / "intents_*.jsonl")):
        for row in _read_jsonl(fp):
            try:
                if row.get("action") not in ("CONVERGE_OPEN", "CONVERGE_CLOSE"):
                    continue
                if account and row.get("acct") != account:
                    continue
                if now - float(row.get("ts") or 0.0) > VERDICT_MAX_AGE_S:
                    continue
                if now - (float(row.get("bar_ts") or 0.0) + BAR_S) > VERDICT_MAX_AGE_S:
                    continue
                key = (row.get("acct"), row.get("sym_side"))
                prev = out.get(key)
                if prev is None or float(row.get("ts") or 0) > float(prev.get("ts") or 0):
                    out[key] = row
            except Exception:
                continue
    return out


def forward_holds(root: Path = None) -> dict:
    """sym_side -> forward-hold record (crypto vec_live state). {} when absent."""
    r = Path(root) if root else host_root()
    out: dict = {}
    for fp in glob.glob(str(r / "data" / "vec_live" / "state" / "*.json")):
        try:
            d = json.loads(Path(fp).read_text())
            out[Path(fp).stem] = d
        except Exception:
            continue
    return out


def board_set(root: Path = None, sym_side: str = "") -> dict:
    """Live-authorized per_sym set row, or {} (authorized config, NOT a position)."""
    try:
        import sqlite3

        con = sqlite3.connect(str(Path(root or host_root()) / "data" / "hourly_reconfig" / "per_sym_store.db"), timeout=10)
        try:
            con.row_factory = sqlite3.Row
            r = con.execute("select * from per_sym_active where sym_side=?", (sym_side,)).fetchone()
            return dict(r) if r else {}
        finally:
            con.close()
    except Exception:
        return {}


def status(sym_side: str, account: str = "", root: Path = None, now: float = None) -> dict:
    """One sym_side's vector position status: {state, reason, asof, source}.

    state: OPEN | FLAT | AUTHORIZED (board set only) | UNKNOWN. Never raises.
    """
    r = Path(root) if root else host_root()
    now = float(now if now is not None else time.time())
    try:
        for (acct, ss), v in converge_verdicts(r, account or None, now).items():
            if ss == sym_side and (not account or acct == account):
                return {"state": "OPEN" if v.get("action") == "CONVERGE_OPEN" else "FLAT",
                        "reason": str(v.get("detail") or v.get("action") or ""),
                        "asof": v.get("ts"), "source": "pure_board_verdict"}
        holds = forward_holds(r)
        if sym_side in holds:
            h = holds[sym_side] or {}
            return {"state": "OPEN" if h.get("pos") or h.get("qty") else "FLAT",
                    "reason": str(h.get("reason") or "forward_hold"),
                    "asof": h.get("saved_utc") or h.get("ts"), "source": "vec_live_state"}
        b = board_set(r, sym_side)
        if b:
            return {"state": "AUTHORIZED", "reason": f"board gain={b.get('acc_gain_pct')}",
                    "asof": b.get("updated_at"), "source": "per_sym_store"}
    except Exception as exc:
        return {"state": "UNKNOWN", "reason": f"lookup error {exc}", "asof": None, "source": "error"}
    return {"state": "UNKNOWN", "reason": "no vector source covers it", "asof": None, "source": "none"}


def iso(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except Exception:
        return "n/a"
