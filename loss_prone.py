"""loss_prone — entry functions that end up having to close at a loss (USER 2026-10-17: "these are to be excluded from newborn
protection and should only be accepted with additional filters in future application (unless backtest deltas say otherwise)").

The flag list data/loss_prone_entries.json is built by tools/loss_close_db.py from the boards' own ledgers (latest 30-day charts)
and the live ledger (confirmed full closes joined to the opening function in the decision logs). Consumers:
  * ez_manage newborn protection: a position opened by a loss-prone function gets NO newborn protection
    (NEWBORN_PROTECT_LOSS_PRONE_EXEMPT, config-only) — it can close before the loss grows.
  * v12_quick_engine LOSS_PRONE_ENTRY_FILTER_ENABLED: an OPEN from a loss-prone function must also pass
    LOSS_PRONE_ENTRY_MIN_FILTERS of {WT 1h with the side, WT 4h with the side, close vs ema_50_1h with the side}. A template switch,
    so each board decides by its delta ("unless backtest deltas say otherwise"); live follows through the VEC_EXACT twin.
fn_family() is the ONE name normaliser (tools/loss_close_db.py imports it) so DB, live and vector classify identically.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FLAGS = ROOT / "data" / "loss_prone_entries.json"
_STRIP = re.compile(r"_(px|lvl|g|exit|cur|k3m|k15m|qty|mult|arm|leg|tgt|act|dev|pct|gain|age|m)[-+]?[\d.].*$", re.I)
_CACHE = {"mtime": None, "flags": {"crypto": frozenset(), "stocks": frozenset()}}
_DEC = {}  # (account, file) -> (mtime, {position_key: [(epoch, reason)]})


def fn_family(reason) -> str:
    r = str(reason or "").strip()
    r = re.sub(r"^VEC_EXACT_(OPEN|AUGMENT|REDUCE|CLOSE)_", "", r)
    r = re.split(r"[ |:(]", r)[0] if r else ""
    r = _STRIP.sub("", r)
    r = re.sub(r"_(close|above|below|exit|from|at)([a-z]|[-+]?\d).*$", "", r)  # lower-case tail = a parameter
    r = re.sub(r"_(LONG|SHORT)$", "", r)
    r = re.sub(r"_[-+]?\d+(\.\d+)?%?$", "", r).rstrip("_")  # only a separated number is a parameter (B12 stays B12)
    return r or "UNKNOWN"


def flagged(venue: str) -> frozenset:
    try:
        mt = FLAGS.stat().st_mtime
    except OSError:
        return frozenset()
    if _CACHE["mtime"] != mt:
        try:
            d = json.loads(FLAGS.read_text())
            _CACHE["flags"] = {"crypto": frozenset(d.get("crypto") or []), "stocks": frozenset(d.get("stocks") or [])}
            _CACHE["mtime"] = mt
        except Exception:
            pass
    return _CACHE["flags"].get("stocks" if str(venue).lower() in ("stocks", "tradier") else "crypto", frozenset())


def is_loss_prone_reason(reason, venue: str) -> bool:
    return fn_family(reason) in flagged(venue)


def _decisions(account: str, day: str):
    f = ROOT / "data" / "decisions" / f"decisions_{account}_{day}.jsonl"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return {}
    hit = _DEC.get((account, day))
    if hit and hit[0] == mt:
        return hit[1]
    out = {}
    try:
        for ln in open(f, errors="replace"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if str(d.get("action") or "").upper() not in ("OPEN", "REENTRY", "QUICK_OPEN"):
                continue
            ts = datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00")).timestamp()
            out.setdefault(str(d.get("position_key") or ""), []).append((ts, str(d.get("reason") or d.get("reason_text") or "")))
    except Exception:
        return {}
    _DEC[(account, day)] = (mt, out)
    return out


def entry_family_for(account: str, position_key: str, opened_epoch=None) -> str:
    """Opening function of the live position: the last OPEN/REENTRY decision for this key at or before the open (today + yesterday)."""
    now = datetime.now(timezone.utc)
    cands = []
    for d in (now - timedelta(days=1), now):
        cands += _decisions(str(account), d.strftime("%Y%m%d")).get(str(position_key), [])
    lim = (float(opened_epoch) + 120.0) if opened_epoch else time.time()
    cands = sorted(c for c in cands if c[0] <= lim)
    return fn_family(cands[-1][1]) if cands else "UNKNOWN"


def position_is_loss_prone(account: str, position_key: str, venue: str, opened_epoch=None) -> tuple:
    fam = entry_family_for(account, position_key, opened_epoch)
    return (fam in flagged(venue), fam)


def native_entry_ok(get, reason, venue: str, is_long: bool, ind, price) -> tuple:
    """Live twin of the vector's LOSS_PRONE_ENTRY_FILTER for NATIVE exposure increases (no vec provenance): a loss-prone opening
    function must pass LOSS_PRONE_ENTRY_MIN_FILTERS of the same three filters. get(knob, default) is the venue's per-sym reader.
    Returns (ok, family)."""
    fam = fn_family(reason)
    if fam not in flagged(venue) or not bool(get("LOSS_PRONE_ENTRY_FILTER_ENABLED", True)):
        return True, fam
    import vec_decisions.breakeven_lock as _bel

    ind = ind or {}
    return _bel.entry_filters_pass(get, is_long, price, ind.get("wt1_1h"), ind.get("wt2_1h"), ind.get("wt1_4h"), ind.get("wt2_4h"), ind.get("ema_50_1h")), fam
