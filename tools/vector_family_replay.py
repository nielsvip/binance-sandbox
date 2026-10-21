"""vector_family_replay — twin-vs-vector bar replays for EXIT + TECHNICAL fires.

Standard: same as the 2026-10-21 VEL retrace (real NPZ bars, real board switches,
real engine/twin math). NPZ velocities are completed-bar values, which is exactly
what the live *_closed fields now carry — so NPZ doubles as the closed input.

EXIT: necessary-condition replay (fire_at mask+minTFs). Booking gates are
symmetric-or-stricter by construction (per-sym ENABLED/MIN_TFS/hold/NOLOSS reads
on both sides; HTF veto crypto-inert; daytrade priority identical).
TECHNICAL: cross replay on NPZ dc levels. A fresh cross is necessary for the
vector booking; no-cross bars are conclusive suppression matches. Cross bars
need position age for the hold verdict and report NEEDS-AGE (ambiguous).
Exit_sig composition is a known residual for TECHNICAL (not for EXIT lane-D).
"""

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from live_twins import vector_enforce as ve
from vec_decisions import twin_exits_dead as ted
from vec_decisions import dc_channel_exits as dcc

FULL = json.load(open(ROOT / "data" / "per_sym_full_sets.json"))


def _fire_rows(family):
    rows = []
    try:
        lines = Path(ve.VIOLATIONS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if d.get("family") != family:
            continue
        if "VELOCITY" not in str(d.get("reason", "")) and family == "EXIT":
            continue
        if "TECHNICAL_TARGET" not in str(d.get("reason", "")) and family == "TECHNICAL":
            continue
        rows.append(d)
    rows.sort(key=lambda d: d.get("ts", 0))
    return rows


def _bar(npz_path, fire_ts):
    z = np.load(npz_path, allow_pickle=True)
    ts = np.asarray(z["timestamps"])
    i = int(np.searchsorted(ts, fire_ts, side="right")) - 1
    n = len(ts)
    i = max(0, min(i, n - 1))
    return z, n, i


def _side(key):
    return key.endswith("_SHORT")


def replay_exit_fires():
    """Returns (match, total). Match = twin-closed-fire == vector-fire_at."""
    n_match, n_total = 0, 0
    for d in _fire_rows("EXIT"):
        key = str(d.get("position_key", ""))
        sym = key.split(":")[-1].rsplit("_", 1)[0]
        is_long = not _side(key)
        row = FULL.get(sym + ("_SHORT" if not is_long else "_LONG"), {})
        cfgd = row.get("full_config", row)
        get = lambda k, dd=None, _c=cfgd: _c.get(k, dd)
        try:
            z, n, i = _bar(
                str(ROOT / "backtest_v8" / "indicators" / (sym + ".npz")),
                float(d.get("ts", 0)),
            )
        except OSError:
            continue
        tfs = ted.velocity_wt_tfs(get)
        vals = []
        for tf in tfs:
            a = z.get("wt_velocity_%s" % tf)
            vals.append(
                (
                    tf,
                    (
                        float(np.asarray(a, dtype=float)[i])
                        if a is not None and len(a) == n
                        else 0.0
                    ),
                )
            )
        vec_fires = len(ted.velocity_wt_hits(vals, is_long)) >= ted.velocity_wt_min_tfs(
            get
        )
        ind = {"wt_velocity_%s" % tf: v for tf, v in vals}
        ind.update({"wt_velocity_%s_closed" % tf: v for tf, v in vals})
        twin_fires, twin_reason = ted.velocity_wt_exit_live_fire(ind, is_long, get)
        n_total += 1
        mark = "MATCH" if bool(twin_fires) == bool(vec_fires) else "MISMATCH"
        if mark == "MATCH":
            n_match += 1
        print(
            f"{sym} twin={bool(twin_fires)} vec={bool(vec_fires)} {mark} ({twin_reason[:50]})"
        )
    return n_match, n_total


def replay_technical_fires():
    """Returns (match, total). Match = no-cross suppression agreement, or
    cross bars flagged NEEDS-AGE (counted separately, never as match)."""
    n_match, n_total, n_ambiguous = 0, 0, 0
    for d in _fire_rows("TECHNICAL"):
        key = str(d.get("position_key", ""))
        sym = key.split(":")[-1].rsplit("_", 1)[0]
        is_long = not _side(key)
        row = FULL.get(sym + ("_SHORT" if not is_long else "_LONG"), {})
        cfgd = row.get("full_config", row)
        get = lambda k, dd=None, _c=cfgd: _c.get(k, dd)
        try:
            z, n, i = _bar(
                str(ROOT / "backtest_v8" / "indicators" / (sym + ".npz")),
                float(d.get("ts", 0)),
            )
        except OSError:
            continue
        stop_specs, tgt_specs = dcc.resolve_technical_dc(get)
        if not tgt_specs and not stop_specs:
            continue
        n_total += 1

        def arr(k):
            a = z.get(k)
            return (
                np.asarray(a, dtype=float)
                if a is not None and len(a) == n
                else np.zeros(n)
            )

        px, pxp = float(arr("close_15m")[i]), float(arr("close_15m")[i - 1])
        crossed = False
        for sp in tgt_specs:
            fl = sp["field_long"] if is_long else sp["field_short"]
            lvl, lvl_p = float(arr(fl)[i]), float(arr(fl)[i - 1])
            if dcc.dc_target_cross(px, pxp, lvl, lvl_p, is_long, sp):
                crossed = True
        if not crossed:
            n_match += 1
            print(f"{sym} cross=False twin=False vec=False MATCH (sitting-at-target)")
        else:
            n_ambiguous += 1
            print(f"{sym} cross=True NEEDS-AGE (hold unevaluated)")
    print(f"ambiguous-needs-age: {n_ambiguous}")
    return n_match, n_total
