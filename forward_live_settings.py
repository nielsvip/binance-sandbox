"""forward_live_settings — live reader for forward-test promotions.

USER 2026-10-22: any disabled/nonvec/low-TF path with a positive forward delta
after --promote-min-days (default 4.0) of paper history is re-enabled live,
per sym_side. The writer is tools/paper_forward_promote.py (SQLite primary +
JSON fallback, the operator's chosen shape). This module is the READ side,
imported by the two live resolution chokes (ez_manage._psym_get,
tradier_manage._cfg) ABOVE their parity blocks, so a live promotion exempts
exactly that (sym_side, knob) and nothing else.

Fail-SAFE in the live direction: any error, any kill switch, any non-active
row returns None and the existing blocks behave exactly as today. This module
never raises, never writes, never trades.
Kill: env FORWARD_AUTO_PROMOTE=0, or data/paper_forward_comprehensive/CONTROL.json
{"auto_promote": false}.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "data" / "paper_forward_comprehensive"
DB = DIR / "forward_live_settings.db"
JS = DIR / "forward_live_settings.json"
CONTROL = DIR / "CONTROL.json"

_MEMO = {"at": 0.0, "rows": {}}
MEMO_SEC = 30.0


def _killed() -> bool:
    try:
        if str(os.environ.get("FORWARD_AUTO_PROMOTE", "1")).strip() in (
            "0",
            "false",
            "False",
            "no",
        ):
            return True
        c = json.loads(CONTROL.read_text())
        if isinstance(c, dict) and c.get("auto_promote") is False:
            return True
    except Exception:
        pass
    try:  # promotions are LIVE-only: backtest/sweep engines resolve the same chokes and must never see them
        import sys as _sys

        if any(
            m in _sys.modules
            for m in ("backtest_v12_engine", "v15_pilot", "v12_quick_engine")
        ):
            return True
    except Exception:
        pass
    return False


def _load_rows() -> dict:
    now = time.time()
    if now - _MEMO["at"] < MEMO_SEC:
        return _MEMO["rows"]
    rows: dict = {}
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        try:
            for sym_side, knob, value_json, status in con.execute(
                "SELECT sym_side, knob, value_json, status FROM promoted"
            ):
                if str(status) == "active":
                    rows[(str(sym_side), str(knob))] = json.loads(value_json)
        finally:
            con.close()
    except Exception:
        try:
            d = json.loads(JS.read_text())
            for r in d.get("promoted") or []:
                if str(r.get("status")) == "active":
                    rows[(str(r.get("sym_side")), str(r.get("knob")))] = r.get("value")
        except Exception:
            rows = {}
    _MEMO["at"] = now
    _MEMO["rows"] = rows
    return rows


def get(symbol, side, knob):
    """Promoted value for this (sym_side, knob), or None (no promotion / killed / error).

    Returns (value, promo_id). Bool knobs promote True, ABLATION_DISABLE_* inv
    knobs promote False — the writer decides; this side only carries it.
    """
    try:
        if not symbol or not side or not knob:
            return None
        if _killed():
            return None
        v = _load_rows().get((f"{symbol}_{side}", str(knob)))
        if v is None:
            return None
        return v.get("value"), v.get("promo_id")
    except Exception:
        return None


def invalidate() -> None:
    _MEMO["at"] = 0.0
    _MEMO["rows"] = {}
