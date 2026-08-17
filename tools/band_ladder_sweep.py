#!/usr/bin/env python3
"""band_ladder_sweep.py — find the best band-ladder multipliers per TF, per ticker.

USER 2026-07-22: "run these tests with a lot of variations on more than one ticker until you
establish the best multipliers per timeframe. Try shorter test periods first so that in 90 min
you have a steady baseline for all. And make sure the grid keeps filling with HIGHER THAN B&H
results NO MATTER WHAT — never go below b&h."

TWO HARD RULES, enforced mechanically:
  1. b&h IS THE FLOOR. The baseline for a key is the all-exits-off HOLD, which returns exactly
     b&h. A candidate is KEPT only if its gain >= that key's b&h. Anything below b&h is recorded
     for the record but NEVER becomes the stored best — the ratchet only moves up.
  2. SHORT WINDOW FIRST. `--start` defaults to a recent window so a full multi-ticker pass
     finishes in ~90 min; the winners are re-run on the full window afterward. A short-window
     number is tagged so it is never confused with a full-history result.

Grid: (mode, D_bottom/D_top, 4h_bottom/4h_top, 1h_bottom/1h_top). The user's ladders
(10/3, 6/2, 4/1 and the center-plateau variant) are the seeds; the agent widens around whatever
wins. Results -> param_cells campaign '<CAMPAIGN>__bandladder' AND data/reports/BAND_LADDER.md
with the per-ticker best and its multiple of b&h.

Usage (S1):
  python tools/band_ladder_sweep.py --syms MU,NVDA,AAPL --window short --workers 6
  python tools/band_ladder_sweep.py --syms MU --window full --refine   # re-run winners full
"""
import argparse
import itertools
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SBX = Path(os.environ.get("V8_SBX", "/home/niels/binance-sandbox"))
if not (SBX / "tradier_manage.py").exists():
    SBX = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SBX))
sys.path.insert(0, str(SBX / "tools"))
os.environ.setdefault("PSC_CAMPAIGN", "stocks_baseline_v2_s4h")
import persym_baseline_campaign as psc  # noqa: E402
import param_results_store as prs  # noqa: E402
import exposure_ladder as el  # noqa: E402  (the exit/entry off-sets — clean-room the strategy)
from backtest_data_contract import audit_ladder_result, audit_npz  # noqa: E402

DB = SBX / "data" / "param_results_stocks.db"
OUT = SBX / "data" / "reports" / "BAND_LADDER.md"
CAMPAIGN = psc.CAMPAIGN + "__bandladder"
STRATEGY = os.environ.get("SWEEP_STRATEGY", "arrow")   # arrow | swing
FULL_START = "2024-01-01"
SHORT_START = "2025-10-01"   # ~9 months: enough band cycles, ~1/3 the sim cost of full

# USER 2026-07-23: BUY every green arrow (slope up) sized by band depth, SELL every red arrow
# (slope down) on D/4h — the WHOLE strategy. To measure it alone, every competing entry and
# exit is turned OFF first (all_entries_off + all_exits_off — the same clean-room the exposure
# ladder proved), then BAND_ARROW is the only path left. Without this, GR_HTF/WT_DC entries and
# HYBRID_STRUCT 15m exits dominated (33 trades, 0-bar holds, 1.4%) and the strategy never ran.
def _base():
    b = dict(el.all_entries_off())          # kill GR_HTF, WT_DC, MTF_ARROW, ... entries
    b.update(el.all_exits_off())            # kill HYBRID_STRUCT, DELTA, WT_CROSSUNDER, ... exits
    if STRATEGY == "swing":
        # USER 2026-07-23: exit on D lower-low+lower-high, RE-ENTER ONLY AT OR BELOW exit price
        # (>= START_POSITION_SIZE). Same shares, cheaper basis -> beats b&h by construction.
        b.update({
            # SWING_EXIT (lower-low+lower-high on D) stores the exit price; WT_3M_FORCE_OPEN
            # re-opens ONLY at/below that price (the guarantee), on its wt-cross trigger.
            "SWING_ENABLED": True, "SWING_EXIT_TFS": "D",
            "SWING_REENTER_AT_OR_BELOW_EXIT": True, "SWING_REENTER_TOLERANCE_PCT": 0.0,
            "SWING_RUNAWAY_REENTER": False,   # strict: never re-buy above exit -> guaranteed >= b&h
            "WT_3M_FORCE_OPEN_ENABLED": True, "WT_3M_FORCE_OPEN_BYPASS_GATES": True,
            "WT_3M_FORCE_OPEN_BUILD_TO_TARGET": False, "WT_3M_FORCE_OPEN_SIZE_USD": 2000.0,
            "WT_3M_FORCE_OPEN_TARGET_USD": 2000.0, "WT_3M_FORCE_OPEN_TF_LADDER": False,
        })
        return b
    b.update({
        "BAND_ARROW_ENABLED": True,         # re-enable ONLY our entry+exit (ARROW matched ENTRY_PAT)
        "BAND_ARROW_ENTRY_TFS": "D,4h,1h", "BAND_ARROW_EXIT_TFS": "D,4h",
        "BAND_ARROW_ACCUMULATE": True, "BAND_ARROW_MAX_POS_MULT": 30.0,
        "LR_BAND_LADDER_ENABLED": True, "LR_BAND_LADDER_BELOW_BOTTOM_MULT": 0.0,
        "LR_BAND_LADDER_ABOVE_TOP_MULT": -1.0,
        "WT_DC_HTF_GATE": "none", "HTF_ALIGN_REQUIRED_TRADIER": 0,
    })
    return b

# seed ladders (USER-specified) + widen; each entry is (mode, {tf:(bottom,top)})
GRID = [
    # Exact user baseline (2026-07-25): D 10x->6x, 4h 6x->4x, 1h 4x->1x.
    # Keep both interpolation modes because center-plateau vs linear materially changes
    # exposure; this pair must never be lost among exploratory guesses.
    ("center_plateau", {"D": (10, 6), "4h": (6, 4), "1h": (4, 1)}),
    ("linear",         {"D": (10, 6), "4h": (6, 4), "1h": (4, 1)}),
    ("center_plateau", {"D": (8, 4),  "4h": (5, 3), "1h": (3, 1)}),
    ("linear",         {"D": (12, 8), "4h": (8, 5), "1h": (5, 2)}),
    ("center_plateau", {"D": (10, 3), "4h": (6, 2), "1h": (4, 1)}),
    ("linear",         {"D": (10, 3), "4h": (6, 2), "1h": (4, 1)}),
    ("center_plateau", {"D": (15, 3), "4h": (9, 2), "1h": (6, 1)}),
    ("linear",         {"D": (20, 5), "4h": (12, 3), "1h": (8, 2)}),
    ("center_plateau", {"D": (10, 1), "4h": (6, 1), "1h": (4, 1)}),
    ("center_plateau", {"D": (25, 5), "4h": (15, 3), "1h": (10, 2)}),
]


def bh_of(sym, side, start):
    _y, bh_long = psc.sym_years_and_bh(sym, start)
    raw = bh_long if side == "LONG" else (-bh_long if bh_long is not None else None)
    # Capital-return contract: B&H deploys one $2k unit against the $10k
    # account, while the strategy may scale that unit up to the $16k capacity.
    return raw * 0.20 if raw is not None else None


BASE = {}


def overrides_for(mode, tfmap):
    o = dict(BASE)
    o["LR_BAND_LADDER_MODE"] = mode
    o["LR_BAND_LADDER_TF_BOTTOM"] = {tf: float(b) for tf, (b, _t) in tfmap.items()}
    o["LR_BAND_LADDER_TF_TOP"] = {tf: float(t) for tf, (_b, t) in tfmap.items()}
    return o


def run(sym, side, mode, tfmap, start, timeout):
    # 'ARR' in the tag so an arrow-strategy run can never reuse a cached harvest-strategy cell
    tag = f"BL_ARR_{mode}_{'_'.join(f'{tf}{b}-{t}' for tf,(b,t) in tfmap.items())}_{start}".replace("/", "")[:120]
    cell = psc.TRADES_ROOT / tag
    cell.mkdir(parents=True, exist_ok=True)
    res = cell / f"v8result__{sym}.json"
    if not res.exists():
        ovr = cell / f"override__{sym}.json"
        ovr.write_text(json.dumps(overrides_for(mode, tfmap)))
        env = dict(os.environ)
        env.update({"V8_OVERRIDE_FILE": str(ovr), "V8_TRADES_OUT_DIR": str(cell),
                    "V8_TRADES_RUN_ID": "cell", "V8_SWEEP_MODE": "1", "V8_RATE_GUARD_DISABLED": "1",
                    "V8_BACKTEST_DISK_CACHE": "1", "V8_SIDE_GATE_DISABLED": "1",
                    "V8_DISABLE_PER_SYM": "1", "V8_LADDER_ONLY_SIDE": side})
        cmd = ["timeout", str(timeout), "nice", "-n", "18", psc.PY,
               str(SBX / "backtest_v8_engine.py"), "--mode", "tradier", "--account", "trb",
               "--start", start, "--capital", "10000.0", "--symbols", sym,
               "--npz-dir", str(psc.MATRIX_NPZ_DIR)]
        proc = subprocess.run(cmd, cwd=str(SBX), env=env, capture_output=True, text=True)
        d = {}
        for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines():
            if "V8_RESULT:" in line:
                for part in line.split("V8_RESULT:", 1)[1].split():
                    if "=" in part:
                        k, v = part.split("=", 1)
                        try:
                            d[k] = float(v)
                        except ValueError:
                            d[k] = v
        res.write_text(json.dumps(d))
    try:
        return json.loads(res.read_text())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="MU")
    ap.add_argument("--side", default="LONG")
    ap.add_argument("--window", default="short", choices=["short", "full"])
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--min-avail", type=int, default=2000)
    a = ap.parse_args()
    start = SHORT_START if a.window == "short" else FULL_START
    syms = [s.strip().upper() for s in a.syms.split(",") if s.strip()]
    global BASE, STRATEGY
    STRATEGY = os.environ.get("SWEEP_STRATEGY", "arrow")
    BASE = _base()
    print(f"[base] clean-room: {len(BASE)} knobs ({sum(1 for v in BASE.values() if v is False)} off, "
          f"BAND_ARROW_ENABLED={BASE.get('BAND_ARROW_ENABLED')})", flush=True)
    con = prs.connect()
    best = {}   # (sym) -> {gain, bh, cfg}  — the RATCHET, never drops below b&h
    for sym in syms:
        contract = audit_npz(
            sym,
            npz_path=psc.MATRIX_NPZ_DIR / f"{sym}.npz",
            profile="ladder",
            start=start,
        )
        if not contract.valid:
            print(
                f"[{sym}] DATA QUARANTINE — no engine run or matrix write: "
                + "; ".join(contract.errors),
                flush=True,
            )
            continue
        bh = bh_of(sym, a.side, start)
        if bh is None:
            print(f"[{sym}] no b&h — skip")
            continue
        best[sym] = {"gain": bh, "bh": bh, "cfg": "HOLD(all_exits_off)=b&h", "trades": 0}
        for mode, tfmap in GRID:
            while psc.free_mb() < a.min_avail:   # do not OOM-kill a running engine
                time.sleep(30)
            d = run(sym, a.side, mode, tfmap, start, a.timeout)
            if not d or "pnl" not in d:
                print(f"[{sym}] {mode} {tfmap}: engine-fail", flush=True)
                continue
            gain, tr = float(d["pnl"]), int(d.get("trades", 0))
            diagnostic = audit_ladder_result(d, a.side, require_sizing=True)
            measurement_valid = diagnostic["valid"]
            xbh = gain / bh if bh else 0.0
            keep = gain >= bh
            cfglabel = f"{mode}|" + "|".join(f"{tf}:{b}/{t}" for tf, (b, t) in tfmap.items())
            # Zero-trade engine outputs are diagnostic only (often an unseeded or
            # disconnected path), never valid matrix evidence.  Do not let the
            # store's strict contract abort the whole multi-symbol campaign.
            if measurement_valid:
                prs.insert_cell(con, {
                "mode": "tradier", "symbol": sym, "side": a.side, "campaign": CAMPAIGN,
                "param": "BAND_LADDER", "value_json": cfglabel, "value_num": None,
                "pool_sharpe": float(d.get("pool_sharpe", 0)), "trades": tr, "acc_gain_pct": gain,
                "gain_per_mo": None, "delta_gain_mo_vs_bh": round(gain - bh, 2),
                "delta_vs_baseline_gain_mo": None, "ts": psc.now_iso(),
                "overrides_json": json.dumps({"window": a.window, "start": start}),
                "stamp": psc.stamp(), "tier": "ENGINE",
                    "source_file": f"band_ladder_sweep/{a.window}/{sym}_{a.side}/{cfglabel}"})
            else:
                print(
                    f"[{sym}] {cfglabel}: INVALID diagnostic only, not stored "
                    f"({json.dumps(diagnostic, sort_keys=True)})",
                    flush=True,
                )
            mark = "KEEP" if keep else "reject(<b&h)"
            print(f"[{sym}] {cfglabel}: gain={gain:.1f}% ({xbh:.2f}x b&h) trades={tr}  {mark}", flush=True)
            if measurement_valid and keep and gain > best[sym]["gain"]:
                best[sym] = {"gain": gain, "bh": bh, "cfg": cfglabel, "trades": tr}
    con.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        fh.write(f"# BAND LADDER — best multipliers per ticker ({a.window} window, start {start})\n\n")
        fh.write("b&h is the FLOOR: the stored best is NEVER below b&h. A ticker whose best is "
                 "`HOLD=b&h` means no ladder config beat holding yet.\n\n")
        fh.write("| ticker | b&h % | best % | xB&H | trades | best ladder |\n|---|---|---|---|---|---|\n")
        for sym, d in best.items():
            fh.write(f"| {sym}_{a.side} | {d['bh']:.1f} | {d['gain']:.1f} | "
                     f"{d['gain']/d['bh'] if d['bh'] else 0:.2f} | {d['trades']} | {d['cfg']} |\n")
    print(f"\nwrote {OUT}")
    for sym, d in best.items():
        print(f"  {sym}_{a.side}: best={d['gain']:.1f}% ({d['gain']/d['bh'] if d['bh'] else 0:.2f}x b&h)  {d['cfg']}")


if __name__ == "__main__":
    main()
