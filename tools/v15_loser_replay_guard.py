"""v15_loser_replay_guard — would the NEW per-sym set have avoided today's losses?

READ-ONLY. For every losing lifecycle of a day (and every OPEN position currently giving back its peak), this tool
answers one question per trade: does the set that live resolves RIGHT NOW differ, in the protective direction, from the
set that was live when the trade lost?

It is a SETTINGS comparison against MEASURED failure modes. It never replays or simulates P&L and never reports a
counterfactual gain (NO-LIES: a replayed number would need v12_quick_engine.prepare_batch on frozen NPZ, and Mac NPZs
end 2026-10-12/13).

Sources (all read-only):
  trades      per_trade_ledger.distinct_rows / final_closes over data/per_trade_returns/{acct}_{day}.jsonl
              (IMPORTED, never reimplemented — one raw row per close ATTEMPT collapses to one lifecycle)
  peak        {acct}/{long,short}_positions.json  -> max_gain / gain  (the only place peak is recorded; the ledger
              schema has no max_gain, so a CLOSED lifecycle's peak is NOT knowable -> PEAK_UNKNOWN, never guessed)
  OLD set     data/hourly_reconfig/per_sym_store.db  per_sym_history  (latest row with epoch <= trade epoch)
  NOW set     data/hourly_reconfig/per_sym_store.db  per_sym_active   (what live resolves right now)
  PILOT set   data/reports/lifecycle_pilot/{SYM_SIDE}_v14_progress.json  cumulative_overrides (in-flight, NOT live)
  defaults    config.Config / config_tradier.TradierConfig dataclass defaults, for keys absent from full_config
  R2 legs     vec_decisions.twin_yellow_filters.r2_eff_tfs (IMPORTED — the effective leg set is never hand-derived)

CLI:
    python3 tools/v15_loser_replay_guard.py                      # today UTC
    python3 tools/v15_loser_replay_guard.py --date 20261015 --top 40
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
import re
import sqlite3
import sys
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import per_trade_ledger as PTL
from vec_decisions.twin_yellow_filters import r2_eff_tfs

DB_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
PILOT_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
TRIAGE_PATH = ROOT / "data" / "reports" / "v15_recalc_triage.json"
OUT_JSON = ROOT / "data" / "reports" / "v15_loser_replay_guard.json"
OUT_TXT = ROOT / "data" / "reports" / "v15_loser_replay_guard.txt"
STOCK_ACCOUNTS = set(PTL.VENUE_ACCOUNTS["tradier"])
CRYPTO_ACCOUNTS = set(PTL.VENUE_ACCOUNTS["ez"])
MISSING = "<ABSENT>"
# r2_eff_tfs family_list defaults are hardcoded at the LIVE call sites, not in full_config:
#   stocks  tradier_manage.py:16471  _cfg("R2_TF_LIST", ("1h","4h","D"))
#   crypto  ez_manage.py:52406       _psym_get("R2_TF_LIST", ("15m",))
R2_FAMILY_CALLSITE = {"stocks": ("1h", "4h", "D"), "crypto": ("15m",)}
R2_FAMILY_SOURCE = {"stocks": "tradier_manage.py:16471", "crypto": "ez_manage.py:52406"}
RTH_OPEN_MIN = 13 * 60 + 30
PEAK_MIN_PCT = 2.0
GIVEBACK_MAX_PCT = 0.75
STOPPED_THROUGH_PCT = 0.5
CHURN_MIN_LIFECYCLES = 3
SUB5_MIN = 5.0
SLOW_EXIT_MIN_HOLD = 240.0

# direction model — how a value change is scored. Anything not listed here is reported old->new with
# direction "ambiguous" and never counted as FIXED.
#   off_is_unarmed    : "OFF"/"" means the protection is not armed; OFF -> a TF is protective
#   bool_true_prot    : True arms a protection / a gate
#   bool_false_prot   : False disarms an entry strategy (fewer entries)
#   lower_is_prot     : a smaller number is tighter / smaller risk
#   higher_is_prot    : a larger number is more cooldown / more distance
#   r2_legs           : scored by the effective leg set from r2_eff_tfs()
SWITCH_DIRECTION: Dict[str, str] = {
    "EXIT_R1_R2_FILTER_TF": "r2_legs",
    "FROZEN_STOP_FILTER_TF": "off_is_unarmed",
    "MTF_ATR_TRAIL_FILTER_TF": "off_is_unarmed",
    "TECHNICAL_DC_STOP_TF": "off_is_unarmed",
    "DAYTRADE_DC_STOP_TF": "off_is_unarmed",
    "WT_LOWER_CROSS_EXIT_TF": "off_is_unarmed",
    "MTF_WT_CROSS_EXIT_TF": "off_is_unarmed",
    "GAP_MOC_EXIT_ENABLED": "bool_true_prot",
    "GAP_MOC_FORCE_MOC_AT_CLOSE": "bool_true_prot",
    "GAP_MOC_REQUIRE_TOP": "ambiguous",
    "DC_HARD_STOP_15M_ENABLED": "bool_true_prot",
    "DC_HARD_STOP_1H_ENABLED": "bool_true_prot",
    "ATR_ADAPTIVE_STOP_ENABLED": "bool_true_prot",
    "ATR_ADAPTIVE_STOP_MULT": "lower_is_prot",
    "ATR_ADAPTIVE_STOP_TF": "ambiguous",
    "MTF_ATR_TRAIL_ENABLED": "bool_true_prot",
    "MTF_ATR_TRAIL_ENABLED_TRADIER": "bool_true_prot",
    "MTF_ATR_TRAIL_MULT": "lower_is_prot",
    "DC_LOW_FROZEN_STOP_ENABLED": "bool_true_prot",
    "BB_FROZEN_STOP_ENABLED": "bool_true_prot",
    "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED": "bool_true_prot",
    "R2_PEAK_MIN_PCT": "lower_is_prot",
    "WT_CROSS_EXIT_ENABLED": "bool_true_prot",
    "WT_CROSS_EXIT_APPLIES_TO_WINNERS": "bool_true_prot",
    "WT_CROSS_EXIT_APPLIES_TO_LOSERS": "bool_true_prot",
    "WT_CROSS_EXIT_MIN_AGE_MINUTES": "lower_is_prot",
    "COOLDOWN_BARS_TRADIER": "higher_is_prot",
    "COOLDOWN_BARS": "higher_is_prot",
    "TRADIER_POST_CLOSE_COOLDOWN_MIN": "higher_is_prot",
    "REENTRY_COOLDOWN_S": "higher_is_prot",
    "MAX_ORDER_VALUE": "lower_is_prot",
    "WT_15M_BOUNCE_OPEN_ENABLED": "bool_false_prot",
    "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": "bool_true_prot",
    "WT_15M_BOUNCE_FILTER_HH_ENABLED": "bool_true_prot",
    "WT_15M_BOUNCE_FILTER_HL_ENABLED": "bool_true_prot",
    "WT_15M_BOUNCE_HIGH_1H_GT_PREV": "bool_true_prot",
    "WT_15M_BOUNCE_LOW_1H_GT_PREV": "bool_true_prot",
    "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED": "bool_true_prot",
    "WT_15M_BOUNCE_REL_VOL_GT_1": "bool_true_prot",
    "WT_15M_BOUNCE_VOLUME_THRESHOLD": "higher_is_prot",
    "WT_15M_BOUNCE_BB_MAX": "lower_is_prot",
    "WT_15M_BOUNCE_BB_MIN": "higher_is_prot",
    "WT_15M_BOUNCE_MAX_BARS_AGO": "lower_is_prot",
}
PEAK_SWITCHES = [
    "EXIT_R1_R2_FILTER_TF",
    "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED",
    "R2_PEAK_MIN_PCT",
    "FROZEN_STOP_FILTER_TF",
    "MTF_ATR_TRAIL_FILTER_TF",
    "MTF_ATR_TRAIL_ENABLED",
    "MTF_ATR_TRAIL_ENABLED_TRADIER",
    "MTF_ATR_TRAIL_MULT",
    "WT_CROSS_EXIT_APPLIES_TO_WINNERS",
    "WT_LOWER_CROSS_EXIT_TF",
]
GAP_SWITCHES = [
    "GAP_MOC_EXIT_ENABLED",
    "GAP_MOC_FORCE_MOC_AT_CLOSE",
    "GAP_MOC_WINDOW_MINUTES",
    "GAP_MOC_REQUIRE_TOP",
    "GAP_MOC_HOLD_POSITIVE_BIAS_PCT",
]
STOP_SWITCHES = [
    "DC_HARD_STOP_15M_ENABLED",
    "DC_HARD_STOP_1H_ENABLED",
    "DC_HARD_STOP_TF",
    "ATR_ADAPTIVE_STOP_ENABLED",
    "ATR_ADAPTIVE_STOP_TF",
    "ATR_ADAPTIVE_STOP_MULT",
    "TECHNICAL_DC_STOP_TF",
    "DAYTRADE_DC_STOP_TF",
    "DC_LOW_FROZEN_STOP_ENABLED",
    "DC_LOW_FROZEN_STOP_TF",
]
CHURN_SWITCHES = [
    "COOLDOWN_BARS_TRADIER",
    "COOLDOWN_BARS",
    "TRADIER_POST_CLOSE_COOLDOWN_MIN",
    "REENTRY_COOLDOWN_S",
    "MAX_ORDER_VALUE",
]
ENTRY_SWITCHES = [
    "WT_15M_BOUNCE_OPEN_ENABLED",
    "WT_15M_BOUNCE_REQUIRE_BOTH_HTF",
    "WT_15M_BOUNCE_FILTER_HH_ENABLED",
    "WT_15M_BOUNCE_FILTER_HL_ENABLED",
    "WT_15M_BOUNCE_HIGH_1H_GT_PREV",
    "WT_15M_BOUNCE_LOW_1H_GT_PREV",
    "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED",
    "WT_15M_BOUNCE_REL_VOL_GT_1",
    "WT_15M_BOUNCE_VOLUME_THRESHOLD",
    "WT_15M_BOUNCE_BB_MIN",
    "WT_15M_BOUNCE_BB_MAX",
    "WT_15M_BOUNCE_MAX_BARS_AGO",
]
VELOCITY_SWITCHES = [
    "WT_CROSS_EXIT_ENABLED",
    "WT_CROSS_EXIT_MIN_AGE_MINUTES",
    "WT_CROSS_EXIT_APPLIES_TO_WINNERS",
    "WT_CROSS_EXIT_APPLIES_TO_LOSERS",
    "MTF_WT_CROSS_EXIT_ENABLED",
    "MTF_WT_CROSS_EXIT_TF",
]
MODE_SWITCHES = {
    "GAVE_BACK_PEAK": PEAK_SWITCHES,
    "OVERNIGHT_GAP": GAP_SWITCHES + STOP_SWITCHES,
    "STOPPED_THROUGH": STOP_SWITCHES + GAP_SWITCHES,
    "CHURN": CHURN_SWITCHES + ENTRY_SWITCHES,
    "SLOW_EXIT": PEAK_SWITCHES + VELOCITY_SWITCHES,
    "VELOCITY_EXIT": VELOCITY_SWITCHES,
    "HEDGE_FALLBACK": [],
    "OTHER": PEAK_SWITCHES + STOP_SWITCHES,
}
HEDGE_RE = re.compile(r"HEDGE_FAILED_FALLBACK|HEDGE_UNWIND|SECTORHEAL", re.I)
STOP_RE = re.compile(r"HARD_STOP|FIXED_STOP|_STOP_|DAYTRADE_STOP|FROZEN_STOP|STOP_LOSS", re.I)
VEL_RE = re.compile(r"EXIT_VELOCITY|WT_CROSS|WT_LOWER_CROSS|MTF_GR_WT|WT_CROSSOVER|WT_CROSSUNDER", re.I)
PX_RE = re.compile(r"px([0-9]*\.?[0-9]+)")
LVL_RE = re.compile(r"lvl([0-9]*\.?[0-9]+)")


def _dec(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    if str(text).startswith("z:"):
        try:
            return zlib.decompress(base64.b64decode(str(text)[2:].encode("ascii"))).decode("utf-8")
        except Exception:
            return text
    return text


def _loadj(text: Optional[str]) -> dict:
    try:
        d = json.loads(_dec(text) or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _epoch(ts) -> Optional[float]:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def _venue(account: str) -> str:
    return "stocks" if str(account) in STOCK_ACCOUNTS else "crypto"


def _dataclass_defaults(cls) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        for f in dataclasses.fields(cls):
            if f.default is not dataclasses.MISSING:
                out[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                try:
                    out[f.name] = f.default_factory()  # type: ignore[misc]
                except Exception:
                    continue
    except Exception:
        return {}
    return out


def load_defaults() -> Dict[str, Dict[str, Any]]:
    out = {"stocks": {}, "crypto": {}}
    try:
        import config_tradier
        out["stocks"] = _dataclass_defaults(config_tradier.TradierConfig)
    except Exception as exc:
        print("WARN  config_tradier defaults unavailable: %s" % exc)
    try:
        import config as _config
        out["crypto"] = _dataclass_defaults(_config.Config)
    except Exception as exc:
        print("WARN  config defaults unavailable: %s" % exc)
    return out


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        self.con.row_factory = sqlite3.Row
        self._hist: Dict[str, List[Tuple[float, str, str]]] = {}
        self._active: Dict[str, Tuple[float, str, dict, str]] = {}

    def history(self, sym_side: str) -> List[Tuple[float, str, str]]:
        if sym_side in self._hist:
            return self._hist[sym_side]
        rows = []
        try:
            cur = self.con.execute(
                "SELECT epoch, updated_at, full_config_json, winning_tag FROM per_sym_history "
                "WHERE sym_side=? ORDER BY epoch",
                (sym_side,),
            )
            for r in cur:
                e = r["epoch"] if r["epoch"] is not None else _epoch(r["updated_at"])
                if e is None:
                    continue
                rows.append((float(e), str(r["updated_at"] or ""), r["full_config_json"], str(r["winning_tag"] or "")))
        except Exception:
            rows = []
        self._hist[sym_side] = rows
        return rows

    def history_at(self, sym_side: str, epoch: float) -> Optional[dict]:
        pick = None
        for e, updated, blob, tag in self.history(sym_side):
            if e <= epoch:
                pick = (e, updated, blob, tag)
            else:
                break
        if pick is None:
            return None
        return {"epoch": pick[0], "updated_at": pick[1], "winning_tag": pick[3], "full_config": _loadj(pick[2]), "source": "per_sym_store.db:per_sym_history"}

    def history_latest(self, sym_side: str) -> Optional[dict]:
        rows = self.history(sym_side)
        if not rows:
            return None
        e, updated, blob, tag = rows[-1]
        return {"epoch": e, "updated_at": updated, "winning_tag": tag, "full_config": _loadj(blob), "source": "per_sym_store.db:per_sym_history[latest]"}

    def active(self, sym_side: str) -> Optional[dict]:
        if sym_side in self._active:
            e, updated, fc, tag = self._active[sym_side]
            return {"epoch": e, "updated_at": updated, "winning_tag": tag, "full_config": fc, "source": "per_sym_store.db:per_sym_active"}
        try:
            r = self.con.execute(
                "SELECT updated_at, full_config_json, winning_tag FROM per_sym_active WHERE sym_side=?",
                (sym_side,),
            ).fetchone()
        except Exception:
            r = None
        if r is None:
            return None
        e = _epoch(r["updated_at"]) or 0.0
        fc = _loadj(r["full_config_json"])
        self._active[sym_side] = (e, str(r["updated_at"] or ""), fc, str(r["winning_tag"] or ""))
        return {"epoch": e, "updated_at": str(r["updated_at"] or ""), "winning_tag": str(r["winning_tag"] or ""), "full_config": fc, "source": "per_sym_store.db:per_sym_active"}


CENSUS_SWITCHES = ["EXIT_R1_R2_FILTER_TF", "FROZEN_STOP_FILTER_TF", "MTF_ATR_TRAIL_FILTER_TF", "GAP_MOC_EXIT_ENABLED", "DC_HARD_STOP_15M_ENABLED", "WT_LOWER_CROSS_EXIT_TF"]
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD", "BUSD", "FDUSD")


def fleet_census(store: "Store", defaults: Dict[str, Dict[str, Any]]) -> dict:
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    totals: Dict[str, int] = defaultdict(int)
    try:
        rows = store.con.execute("SELECT sym_side, full_config_json FROM per_sym_active").fetchall()
    except Exception:
        return {}
    for r in rows:
        ss = str(r["sym_side"] or "")
        sym = ss.rsplit("_", 1)[0]
        venue = "crypto" if sym.upper().endswith(CRYPTO_SUFFIXES) else "stocks"
        totals[venue] += 1
        fc = _loadj(r["full_config_json"])
        cfg = {"full_config": fc, "source": "per_sym_active"}
        for key in CENSUS_SWITCHES:
            v, _ = resolve(cfg, key, venue, defaults)
            d = defaults.get(venue, {}).get(key, MISSING)
            tag = "%r%s" % (v, "" if v == d else "  (default %r)" % (d,))
            out.setdefault(key, {}).setdefault(venue, defaultdict(int))[tag] += 1
        legs, _fs, _raw, fam = r2_legs(cfg, venue, defaults)
        if not set(fam).issubset(set(legs)):
            out.setdefault("_R2_LEGS_COLLAPSED", {}).setdefault(venue, defaultdict(int))["eff %s of family %s" % (list(legs), list(fam))] += 1
    return {"totals": dict(totals), "by_switch": {k: {v: dict(c) for v, c in d.items()} for k, d in out.items()}}


def pilot_set(sym_side: str) -> Optional[dict]:
    p = PILOT_DIR / ("%s_v14_progress.json" % sym_side)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    ov = d.get("cumulative_overrides") or {}
    return {
        "epoch": p.stat().st_mtime,
        "updated_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
        "full_config": ov if isinstance(ov, dict) else {},
        "cumulative_gain": d.get("cumulative_gain"),
        "baseline_gain": d.get("baseline_gain"),
        "final_gain_fresh_vec": d.get("final_gain_fresh_vec"),
        "done_cells": len(d.get("done") or {}),
        "source": "data/reports/lifecycle_pilot/%s_v14_progress.json:cumulative_overrides" % sym_side,
    }


def resolve(cfg: Optional[dict], key: str, venue: str, defaults: Dict[str, Dict[str, Any]]) -> Tuple[Any, str]:
    fc = (cfg or {}).get("full_config") or {}
    if key in fc:
        return fc[key], (cfg or {}).get("source", "full_config")
    dv = defaults.get(venue, {})
    if key in dv:
        return dv[key], "config_default(%s)" % ("config_tradier.TradierConfig" if venue == "stocks" else "config.Config")
    return MISSING, "absent"


def r2_legs(cfg: Optional[dict], venue: str, defaults: Dict[str, Dict[str, Any]]) -> Tuple[tuple, str, Any, tuple]:
    raw, _src = resolve(cfg, "EXIT_R1_R2_FILTER_TF", venue, defaults)
    fam, fam_src = resolve(cfg, "R2_TF_LIST", venue, defaults)
    if fam is MISSING or not fam:
        fam = R2_FAMILY_CALLSITE[venue]
        fam_src = "live_callsite(%s)" % R2_FAMILY_SOURCE[venue]
    try:
        fam_t = tuple(fam)
    except Exception:
        fam_t = R2_FAMILY_CALLSITE[venue]
    if raw is MISSING:
        raw = "15m"
    try:
        legs = r2_eff_tfs(raw, fam_t)
    except Exception:
        legs = ()
    return tuple(legs), fam_src, raw, fam_t


def _num(x) -> Optional[float]:
    try:
        if isinstance(x, bool):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _is_off(x) -> bool:
    return str(x).strip().upper() in ("OFF", "", "NONE", "NONE()")


def score(key: str, old: Any, new: Any) -> str:
    if old == new:
        return "same"
    kind = SWITCH_DIRECTION.get(key, "ambiguous")
    if old is MISSING or new is MISSING:
        return "ambiguous"
    if kind == "off_is_unarmed":
        if _is_off(old) and not _is_off(new):
            return "protective"
        if not _is_off(old) and _is_off(new):
            return "regressed"
        return "ambiguous"
    if kind == "bool_true_prot":
        return "protective" if bool(new) and not bool(old) else ("regressed" if bool(old) and not bool(new) else "ambiguous")
    if kind == "bool_false_prot":
        return "protective" if bool(old) and not bool(new) else ("regressed" if bool(new) and not bool(old) else "ambiguous")
    if kind in ("lower_is_prot", "higher_is_prot"):
        a, b = _num(old), _num(new)
        if a is None or b is None:
            return "ambiguous"
        if a == b:
            return "same"
        up = b > a
        good = up if kind == "higher_is_prot" else (not up)
        return "protective" if good else "regressed"
    return "ambiguous"


def score_r2(old_legs: tuple, new_legs: tuple) -> str:
    if tuple(old_legs) == tuple(new_legs):
        return "same"
    o, n = set(old_legs), set(new_legs)
    if o and o < n:
        return "protective"
    if n and n < o:
        return "regressed"
    if len(n) > len(o):
        return "protective"
    if len(n) < len(o):
        return "regressed"
    return "ambiguous"


def diff_switches(keys: List[str], old_cfg, new_cfg, venue, defaults) -> List[dict]:
    out = []
    for key in keys:
        ov, osrc = resolve(old_cfg, key, venue, defaults)
        nv, nsrc = resolve(new_cfg, key, venue, defaults)
        dflt = defaults.get(venue, {}).get(key, MISSING)
        if key == "EXIT_R1_R2_FILTER_TF":
            ol, ofam, oraw, ofamlegs = r2_legs(old_cfg, venue, defaults)
            nl, nfam, nraw, nfamlegs = r2_legs(new_cfg, venue, defaults)
            direction = score_r2(ol, nl)
            out.append(
                {
                    "switch": key,
                    "old": oraw,
                    "new": nraw,
                    "old_source": osrc,
                    "new_source": nsrc,
                    "old_eff_legs": list(ol),
                    "new_eff_legs": list(nl),
                    "r2_family_legs": list(nfamlegs),
                    "r2_collapsed_now": not set(nfamlegs).issubset(set(nl)),
                    "r2_family_source": nfam,
                    "default": dflt,
                    "direction": direction,
                    "changed": oraw != nraw or tuple(ol) != tuple(nl),
                    "resolver": "vec_decisions.twin_yellow_filters.r2_eff_tfs",
                }
            )
            continue
        direction = score(key, ov, nv)
        out.append(
            {
                "switch": key,
                "old": ov,
                "new": nv,
                "old_source": osrc,
                "new_source": nsrc,
                "default": dflt,
                "direction": direction,
                "changed": ov != nv,
            }
        )
    return out


def unprotective_now(diff: dict) -> Optional[str]:
    """Is the value live RIGHT NOW in the unarmed / unprotective state? None = no honest claim.

    Two severities, because BACKTEST_BIBLE §15 says MTF trails / gap-MOC / technical-DC-stop are OFF in the DC-channel
    BASELINE by design: a value that merely equals its config default is tagged BASELINE_OFF (never armed, not a
    regression), while a value a board MOVED off a protective default is tagged BOARD_MOVED (today's regression, live).
    """
    key, val = diff["switch"], diff["new"]
    if val is MISSING:
        return None
    dflt = diff.get("default", MISSING)
    moved = dflt is not MISSING and dflt != val
    sev = "BOARD_MOVED off default %r" % (dflt,) if moved else "BASELINE_OFF (= config default, BIBLE §15)"
    kind = SWITCH_DIRECTION.get(key, "ambiguous")
    if key == "EXIT_R1_R2_FILTER_TF":
        if diff.get("r2_collapsed_now"):
            return "[%s] %s=%r -> eff legs %s of family %s (peak-then-collapse legs REMOVED)" % (sev, key, val, diff.get("new_eff_legs"), diff.get("r2_family_legs"))
        return None
    if kind == "off_is_unarmed" and _is_off(val):
        return "[%s] %s=%r (protection not armed)" % (sev, key, val)
    if kind == "bool_true_prot" and not bool(val):
        return "[%s] %s=False (protection off)" % (sev, key)
    if kind == "bool_false_prot" and bool(val):
        return "[%s] %s=True (entry strategy on)" % (sev, key)
    return None


def classify(row: dict, n_life_today: int, venue: str) -> Tuple[str, List[str], dict]:
    reason = str(row.get("reason") or "")
    hold = PTL._f(row.get("hold_minutes"), 0.0)
    exit_dt = datetime.fromtimestamp(row["_epoch"], timezone.utc)
    minute = exit_dt.hour * 60 + exit_dt.minute
    modes: List[str] = []
    ev: dict = {}
    px = PX_RE.search(reason)
    lvl = LVL_RE.search(reason)
    through_pct = None
    if px and lvl and STOP_RE.search(reason):
        p, l = float(px.group(1)), float(lvl.group(1))
        if l > 0:
            is_long = str(row.get("side") or "").upper().startswith("L")
            through_pct = ((l - p) / l * 100.0) if is_long else ((p - l) / l * 100.0)
            ev["stop_level"] = l
            ev["stop_decision_px"] = p
            ev["through_pct"] = round(through_pct, 3)
    if HEDGE_RE.search(reason):
        modes.append("HEDGE_FALLBACK")
    if through_pct is not None and through_pct > STOPPED_THROUGH_PCT:
        modes.append("STOPPED_THROUGH")
    if venue == "stocks" and hold > 360.0 and RTH_OPEN_MIN <= minute <= RTH_OPEN_MIN + 40:
        modes.append("OVERNIGHT_GAP")
        ev["exit_minutes_after_open"] = minute - RTH_OPEN_MIN
    if n_life_today >= CHURN_MIN_LIFECYCLES:
        modes.append("CHURN")
        ev["sym_side_lifecycles_today"] = n_life_today
    if hold <= SUB5_MIN:
        modes.append("SUB5MIN")
    if VEL_RE.search(reason):
        modes.append("VELOCITY_EXIT")
    if hold >= SLOW_EXIT_MIN_HOLD and "OVERNIGHT_GAP" not in modes:
        modes.append("SLOW_EXIT")
    peak = row.get("_max_gain")
    if peak is not None:
        ev["max_gain_pct"] = round(float(peak), 3)
        ev["max_gain_source"] = row.get("_max_gain_source")
        if float(peak) >= PEAK_MIN_PCT and PTL._f(row.get("pnl_pct"), 0.0) <= GIVEBACK_MAX_PCT:
            modes.insert(0, "GAVE_BACK_PEAK")
    else:
        ev["max_gain_pct"] = None
        ev["max_gain_source"] = "PEAK_UNKNOWN: data/per_trade_returns rows carry no max_gain"
    order = ["GAVE_BACK_PEAK", "STOPPED_THROUGH", "OVERNIGHT_GAP", "HEDGE_FALLBACK", "VELOCITY_EXIT", "CHURN", "SLOW_EXIT", "SUB5MIN"]
    primary = next((m for m in order if m in modes), "OTHER")
    return primary, modes, ev


def load_open_positions() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for acct in sorted(STOCK_ACCOUNTS | CRYPTO_ACCOUNTS):
        for side_file in ("long_positions.json", "short_positions.json"):
            p = ROOT / acct / side_file
            if not p.exists():
                continue
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            for key, pos in d.items():
                if not isinstance(pos, dict):
                    continue
                sym = str(pos.get("symbol") or "").upper()
                side = str(pos.get("position_side") or "").upper()
                if not sym or not side:
                    continue
                if abs(PTL._f(pos.get("positionAmt"), 0.0)) <= 0:
                    continue
                ss = "%s_%s" % (sym, side)
                out["%s:%s" % (acct, ss)] = {
                    "account": acct,
                    "sym_side": ss,
                    "venue": _venue(acct),
                    "gain_pct": PTL._f(pos.get("gain"), 0.0),
                    "max_gain_pct": PTL._f(pos.get("max_gain"), 0.0),
                    "entry_price": PTL._f(pos.get("entry_price"), 0.0),
                    "mark_price": PTL._f(pos.get("mark_price"), 0.0),
                    "opened_at": pos.get("opened_at"),
                    "notional_usd": round(abs(PTL._f(pos.get("positionAmt"), 0.0)) * abs(PTL._f(pos.get("mark_price"), 0.0) or PTL._f(pos.get("entry_price"), 0.0)), 2),
                    "source": "%s/%s" % (acct, side_file),
                }
    return out


def verdict_for(diffs: List[dict], new_cfg, close_epoch: float) -> Tuple[str, str]:
    landed = new_cfg is not None and float(new_cfg.get("epoch") or 0.0) > close_epoch
    if not landed:
        return "NO_NEW_SET", "live set (%s) is not newer than this close — nothing has landed since" % ((new_cfg or {}).get("updated_at") or "none")
    prot = [d for d in diffs if d.get("direction") == "protective"]
    reg = [d for d in diffs if d.get("direction") == "regressed"]
    chg = [d for d in diffs if d.get("changed")]
    if prot and not reg:
        return "FIXED", "protective: " + ", ".join("%s %r->%r" % (d["switch"], d["old"], d["new"]) for d in prot[:4])
    if prot and reg:
        return "MIXED", "protective: %s | regressed: %s" % (
            ", ".join(d["switch"] for d in prot[:3]),
            ", ".join(d["switch"] for d in reg[:3]),
        )
    if reg:
        return "REGRESSED", "regressed: " + ", ".join("%s %r->%r" % (d["switch"], d["old"], d["new"]) for d in reg[:4])
    if chg:
        return "CHANGED_AMBIGUOUS", "changed but direction not scorable: " + ", ".join(d["switch"] for d in chg[:4])
    return "UNCHANGED", "every switch that decides this failure mode is byte-identical — it WILL repeat"


def run(date_str: str, top: int) -> dict:
    defaults = load_defaults()
    store = Store()
    raw = PTL.load_raw(ROOT, None, days=3)
    dist = PTL.distinct_rows(raw)
    finals = PTL.final_closes(dist)
    day = [r for r in finals if datetime.fromtimestamp(r["_epoch"], timezone.utc).strftime("%Y%m%d") == date_str]
    life_count: Dict[str, int] = defaultdict(int)
    for r in day:
        life_count["%s_%s" % (str(r.get("symbol") or "").upper(), str(r.get("side") or "").upper())] += 1
    open_pos = load_open_positions()
    losers = [r for r in day if PTL._f(r.get("pnl_pct"), 0.0) < 0]
    trades: List[dict] = []
    for r in losers:
        sym = str(r.get("symbol") or "").upper()
        side = str(r.get("side") or "").upper()
        ss = "%s_%s" % (sym, side)
        venue = _venue(str(r.get("account")))
        usd = round(PTL._f(r.get("notional_usd"), 0.0) * PTL._f(r.get("pnl_pct"), 0.0) / 100.0, 2)
        close_epoch = float(r["_epoch"])
        open_epoch = close_epoch - PTL._f(r.get("hold_minutes"), 0.0) * 60.0
        r["_max_gain"] = None
        r["_max_gain_source"] = None
        primary, modes, ev = classify(r, life_count[ss], venue)
        old_cfg = store.history_at(ss, close_epoch)
        entry_cfg = store.history_at(ss, open_epoch)
        new_cfg = store.active(ss)
        if new_cfg is None:
            new_cfg = store.history_latest(ss)
        keys = list(dict.fromkeys(MODE_SWITCHES.get(primary, []) + PEAK_SWITCHES))
        if old_cfg is None:
            diffs = []
            verdict, why = "NO_OLD_SET", "no per_sym_history row at or before this close — OLD set unknown, no honest diff"
        else:
            diffs = diff_switches(keys, old_cfg, new_cfg, venue, defaults)
            verdict, why = verdict_for(diffs, new_cfg, close_epoch)
        entry_diffs = diff_switches(ENTRY_SWITCHES, entry_cfg, new_cfg, venue, defaults) if entry_cfg else []
        entry_gates_armed = [d["switch"] for d in entry_diffs if d["switch"] != "WT_15M_BOUNCE_OPEN_ENABLED" and d["direction"] == "protective"]
        trades.append(
            {
                "sym_side": ss,
                "account": r.get("account"),
                "venue": venue,
                "lifecycle": r.get("lifecycle"),
                "entry_ts": datetime.fromtimestamp(open_epoch, timezone.utc).isoformat(),
                "exit_ts": r.get("ts"),
                "hold_minutes": round(PTL._f(r.get("hold_minutes"), 0.0), 2),
                "pnl_pct": round(PTL._f(r.get("pnl_pct"), 0.0), 4),
                "notional_usd": PTL._f(r.get("notional_usd"), 0.0),
                "usd": usd,
                "reason": r.get("reason"),
                "n_raw_attempts": r.get("n_raw"),
                "primary_mode": primary,
                "modes": modes,
                "evidence": ev,
                "old_set": {"updated_at": (old_cfg or {}).get("updated_at"), "winning_tag": (old_cfg or {}).get("winning_tag"), "source": (old_cfg or {}).get("source")},
                "new_set": {"updated_at": (new_cfg or {}).get("updated_at"), "winning_tag": (new_cfg or {}).get("winning_tag"), "source": (new_cfg or {}).get("source")},
                "new_set_landed_after_close": bool(new_cfg and float(new_cfg.get("epoch") or 0.0) > close_epoch),
                "verdict": verdict,
                "why": why,
                "switch_diffs": diffs,
                "entry_gates_now_armed": entry_gates_armed,
            }
        )
    # OPEN positions currently handing back a peak — tomorrow's losses if nothing changed.
    open_rows: List[dict] = []
    for key, pos in sorted(open_pos.items()):
        if pos["max_gain_pct"] < PEAK_MIN_PCT:
            continue
        if pos["gain_pct"] > GIVEBACK_MAX_PCT:
            continue
        ss = pos["sym_side"]
        venue = pos["venue"]
        new_cfg = store.active(ss) or store.history_latest(ss)
        keys = PEAK_SWITCHES + (GAP_SWITCHES if venue == "stocks" else [])
        diffs = diff_switches(keys, new_cfg, new_cfg, venue, defaults)
        legs, fam_src, raw_tf, fam_legs = r2_legs(new_cfg, venue, defaults)
        unprotected = [u for u in (unprotective_now(d) for d in diffs) if u]
        open_rows.append(
            {
                **pos,
                "given_back_pct": round(pos["max_gain_pct"] - pos["gain_pct"], 3),
                "live_set_updated_at": (new_cfg or {}).get("updated_at"),
                "live_set_source": (new_cfg or {}).get("source"),
                "eff_r2_legs": list(legs),
                "r2_family_legs": list(fam_legs),
                "r2_family_source": fam_src,
                "unprotected": unprotected,
                "status": "UNPROTECTED_NOW" if unprotected else "PROTECTIONS_ARMED",
            }
        )
    open_rows.sort(key=lambda x: -x["given_back_pct"])
    # roll-ups
    buckets: Dict[str, dict] = defaultdict(lambda: {"trades": 0, "usd": 0.0, "sym_sides": set()})
    modes_roll: Dict[str, dict] = defaultdict(lambda: {"trades": 0, "usd": 0.0})
    for t in trades:
        b = buckets[t["verdict"]]
        b["trades"] += 1
        b["usd"] += t["usd"]
        b["sym_sides"].add(t["sym_side"])
        m = modes_roll[t["primary_mode"]]
        m["trades"] += 1
        m["usd"] += t["usd"]
    by_venue: Dict[str, dict] = defaultdict(lambda: {"trades": 0, "usd": 0.0})
    for t in trades:
        by_venue[t["venue"]]["trades"] += 1
        by_venue[t["venue"]]["usd"] += t["usd"]
    # per sym_side worklist — still carrying the exact config that lost
    ss_roll: Dict[str, dict] = {}
    for t in trades:
        s = ss_roll.setdefault(
            t["sym_side"],
            {"sym_side": t["sym_side"], "venue": t["venue"], "trades": 0, "usd": 0.0, "verdicts": defaultdict(int), "modes": defaultdict(int), "new_set_updated_at": t["new_set"]["updated_at"], "old_set_updated_at": t["old_set"]["updated_at"], "unchanged_switches": [], "still_unprotective": []},
        )
        s["trades"] += 1
        s["usd"] += t["usd"]
        s["verdicts"][t["verdict"]] += 1
        s["modes"][t["primary_mode"]] += 1
        if t["verdict"] in ("UNCHANGED", "NO_NEW_SET", "REGRESSED"):
            for d in t["switch_diffs"]:
                if not d["changed"]:
                    tag = "%s=%r" % (d["switch"], d["new"])
                    if d["switch"] == "EXIT_R1_R2_FILTER_TF":
                        tag = "%s=%r(eff %s of family %s)" % (d["switch"], d["new"], d.get("new_eff_legs"), d.get("r2_family_legs"))
                    if tag not in s["unchanged_switches"]:
                        s["unchanged_switches"].append(tag)
                    u = unprotective_now(d)
                    if u and u not in s["still_unprotective"]:
                        s["still_unprotective"].append(u)
    for s in ss_roll.values():
        s["still_unprotective"].sort(key=lambda x: (0 if x.startswith("[BOARD_MOVED") else 1, x))
    board_moved: Dict[str, dict] = defaultdict(lambda: {"trades": 0, "usd": 0.0, "sym_sides": set(), "detail": ""})
    for t in trades:
        if t["verdict"] not in ("UNCHANGED", "NO_NEW_SET", "REGRESSED"):
            continue
        for d in t["switch_diffs"]:
            if d["changed"]:
                continue
            u = unprotective_now(d)
            if not u or not u.startswith("[BOARD_MOVED"):
                continue
            b = board_moved[d["switch"]]
            b["trades"] += 1
            b["usd"] += t["usd"]
            b["sym_sides"].add(t["sym_side"])
            b["detail"] = u
    worklist = []
    for s in ss_roll.values():
        v = dict(s["verdicts"])
        if not (v.get("UNCHANGED") or v.get("NO_NEW_SET") or v.get("REGRESSED")):
            continue
        pilot = pilot_set(s["sym_side"])
        worklist.append(
            {
                "sym_side": s["sym_side"],
                "venue": s["venue"],
                "trades": s["trades"],
                "usd": round(s["usd"], 2),
                "verdicts": v,
                "modes": dict(s["modes"]),
                "old_set_updated_at": s["old_set_updated_at"],
                "live_set_updated_at": s["new_set_updated_at"],
                "still_unprotective": s["still_unprotective"],
                "unchanged_switches": s["unchanged_switches"][:12],
                "pilot_inflight": None
                if pilot is None
                else {
                    "progress_json_mtime": pilot["updated_at"],
                    "cumulative_gain": pilot.get("cumulative_gain"),
                    "baseline_gain": pilot.get("baseline_gain"),
                    "done_cells": pilot.get("done_cells"),
                    "source": pilot["source"],
                },
            }
        )
    worklist.sort(key=lambda x: x["usd"])
    triage = None
    if TRIAGE_PATH.exists():
        try:
            td = json.loads(TRIAGE_PATH.read_text())
            neg = [x for x in td if PTL._f(x.get("today_usd"), 0.0) < 0]
            triage = {
                "path": str(TRIAGE_PATH.relative_to(ROOT)),
                "mtime": datetime.fromtimestamp(TRIAGE_PATH.stat().st_mtime, timezone.utc).isoformat(),
                "rows": len(td),
                "negative_sym_sides": len(neg),
                "sum_today_usd_negative": round(sum(PTL._f(x.get("today_usd"), 0.0) for x in neg), 2),
                "sum_today_usd_all": round(sum(PTL._f(x.get("today_usd"), 0.0) for x in td), 2),
            }
        except Exception:
            triage = None
    report = {
        "tool": "tools/v15_loser_replay_guard.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "disclaimer": "SETTINGS comparison against measured failure modes. No trade was re-simulated; no replayed or counterfactual P&L appears anywhere in this report.",
        "ledger_rule": PTL.distinct_rows.__module__ + ".distinct_rows (imported)",
        "r2_resolver": "vec_decisions.twin_yellow_filters.r2_eff_tfs (imported)",
        "counts": {
            "day_final_non_dust_closes": len(day),
            "losing_lifecycles": len(trades),
            "losing_sym_sides": len(ss_roll),
            "net_usd_losers_only": round(sum(t["usd"] for t in trades), 2),
            "by_venue": {k: {"trades": v["trades"], "usd": round(v["usd"], 2)} for k, v in by_venue.items()},
        },
        "triage_cross_check": triage,
        "fleet_census_per_sym_active": fleet_census(store, defaults),
        "verdict_rollup": {k: {"trades": v["trades"], "usd": round(v["usd"], 2), "sym_sides": len(v["sym_sides"])} for k, v in sorted(buckets.items(), key=lambda kv: kv[1]["usd"])},
        "mode_rollup": {k: {"trades": v["trades"], "usd": round(v["usd"], 2)} for k, v in sorted(modes_roll.items(), key=lambda kv: kv[1]["usd"])},
        "board_moved_still_live_rollup": {
            k: {"trades": v["trades"], "usd": round(v["usd"], 2), "sym_sides": len(v["sym_sides"]), "detail": v["detail"]}
            for k, v in sorted(board_moved.items(), key=lambda kv: kv[1]["usd"])
        },
        "preopen_worklist": worklist,
        "open_peak_giveback": open_rows,
        "trades": sorted(trades, key=lambda t: t["usd"])[:top] if top else sorted(trades, key=lambda t: t["usd"]),
        "trades_truncated_to": top,
    }
    return report


def render(rep: dict) -> str:
    L: List[str] = []
    L.append("=" * 118)
    L.append("v15_loser_replay_guard  %s   generated %s" % (rep["date"], rep["generated_at"]))
    L.append(rep["disclaimer"])
    L.append("=" * 118)
    c = rep["counts"]
    L.append("losing lifecycles %d over %d sym_sides   net $%.2f   (day final non-dust closes %d)" % (c["losing_lifecycles"], c["losing_sym_sides"], c["net_usd_losers_only"], c["day_final_non_dust_closes"]))
    for v, d in sorted(c["by_venue"].items()):
        L.append("    %-7s trades %-4d $%.2f" % (v, d["trades"], d["usd"]))
    if rep.get("triage_cross_check"):
        t = rep["triage_cross_check"]
        L.append("triage cross-check %s (%s): %d negative sym_sides, sum today_usd %s (all rows %s)" % (t["path"], t["mtime"], t["negative_sym_sides"], t["sum_today_usd_negative"], t["sum_today_usd_all"]))
    L.append("")
    L.append("VERDICT ROLL-UP  (dollars of today's losing lifecycles)")
    L.append("-" * 118)
    for k, v in rep["verdict_rollup"].items():
        L.append("  %-18s trades %-4d sym_sides %-4d $%.2f" % (k, v["trades"], v["sym_sides"], v["usd"]))
    L.append("")
    L.append("PRIMARY FAILURE MODE ROLL-UP")
    L.append("-" * 118)
    for k, v in rep["mode_rollup"].items():
        L.append("  %-18s trades %-4d $%.2f" % (k, v["trades"], v["usd"]))
    fc = rep.get("fleet_census_per_sym_active") or {}
    if fc:
        L.append("")
        L.append("FLEET CENSUS — every sym_side in per_sym_active (%s), not only today's losers" % fc.get("totals"))
        L.append("-" * 118)
        for key, byv in (fc.get("by_switch") or {}).items():
            L.append("  %s" % key)
            for venue, counts in sorted(byv.items()):
                for val, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                    L.append("        %-7s %-5d %s" % (venue, n, val))
    L.append("")
    L.append("BOARD-MOVED SWITCHES STILL LIVE — a board moved these OFF a protective default and nothing has changed since")
    L.append("-" * 118)
    for k, v in rep["board_moved_still_live_rollup"].items():
        L.append("  %-38s trades %-4d sym_sides %-4d $%.2f" % (k, v["trades"], v["sym_sides"], v["usd"]))
        L.append("        %s" % v["detail"])
    L.append("")
    L.append("PRE-OPEN WORKLIST — sym_sides still carrying the configuration that lost today")
    L.append("-" * 118)
    for w in rep["preopen_worklist"]:
        L.append("  %-16s %-7s $%-9.2f trades %-3d verdicts %s  modes %s" % (w["sym_side"], w["venue"], w["usd"], w["trades"], dict(w["verdicts"]), dict(w["modes"])))
        L.append("        old_set %s -> live_set %s (identical set: per_sym_store.db:per_sym_active)" % (w["old_set_updated_at"], w["live_set_updated_at"]))
        for s in w["still_unprotective"]:
            L.append("        !! STILL UNPROTECTIVE  %s" % s)
        for s in w["unchanged_switches"][:6]:
            L.append("        unchanged  %s" % s)
        if w["pilot_inflight"]:
            p = w["pilot_inflight"]
            L.append("        pilot in-flight (NOT live): cum_gain %s baseline %s cells %s  %s" % (p["cumulative_gain"], p["baseline_gain"], p["done_cells"], p["progress_json_mtime"]))
    L.append("")
    L.append("OPEN POSITIONS GIVING BACK A PEAK RIGHT NOW (tomorrow's losses if nothing changes)")
    L.append("-" * 118)
    for o in rep["open_peak_giveback"]:
        L.append("  %-16s %-4s peak %+7.2f%% -> now %+7.2f%%  gave back %6.2f%%  $%-9.2f  %s" % (o["sym_side"], o["account"], o["max_gain_pct"], o["gain_pct"], o["given_back_pct"], o["notional_usd"], o["status"]))
        for u in o["unprotected"]:
            L.append("        UNPROTECTED %s" % u)
    L.append("")
    L.append("WORST LOSING LIFECYCLES (per-trade verdict, every value traceable to its source)")
    L.append("-" * 118)
    for t in rep["trades"]:
        L.append("  %-16s %-4s $%-9.2f %+7.3f%% hold %8.1fm  %-16s  %s" % (t["sym_side"], t["account"], t["usd"], t["pnl_pct"], t["hold_minutes"], t["primary_mode"], t["verdict"]))
        L.append("        exit %s  reason %s" % (t["exit_ts"], str(t["reason"])[:96]))
        L.append("        old_set %s (%s)  ->  live_set %s (%s)" % (t["old_set"]["updated_at"], t["old_set"]["source"], t["new_set"]["updated_at"], t["new_set"]["source"]))
        L.append("        modes %s  evidence %s" % (t["modes"], {k: v for k, v in t["evidence"].items() if k != "max_gain_source"}))
        L.append("        %s" % t["why"])
        if t["verdict"] in ("UNCHANGED", "NO_NEW_SET", "REGRESSED", "MIXED"):
            for d in t["switch_diffs"]:
                if d["changed"]:
                    continue
                u = unprotective_now(d)
                if u:
                    L.append("          !! STILL %s" % u)
        for d in t["switch_diffs"]:
            if not d["changed"]:
                continue
            if d["switch"] == "EXIT_R1_R2_FILTER_TF":
                L.append("          %-38s %r -> %r   eff legs %s -> %s   [%s]" % (d["switch"], d["old"], d["new"], d.get("old_eff_legs"), d.get("new_eff_legs"), d["direction"]))
            else:
                L.append("          %-38s %r -> %r   [%s]  src %s -> %s" % (d["switch"], d["old"], d["new"], d["direction"], d["old_source"], d["new_source"]))
    L.append("=" * 118)
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare today's losing trades against the NEW per-sym sets (read-only).")
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"), help="UTC day YYYYMMDD")
    ap.add_argument("--top", type=int, default=40, help="per-trade detail rows in the report (0 = all)")
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--out-txt", default=str(OUT_TXT))
    a = ap.parse_args(argv)
    rep = run(a.date, a.top)
    txt = render(rep)
    Path(a.out_json).write_text(json.dumps(rep, indent=1, default=str))
    Path(a.out_txt).write_text(txt)
    print(txt)
    print("\nwrote %s" % a.out_json)
    print("wrote %s" % a.out_txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
