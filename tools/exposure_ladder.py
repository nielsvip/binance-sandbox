#!/usr/bin/env python3
"""exposure_ladder.py — test switches DOWN from b&h, not UP from nothing (USER 2026-07-22).

THE CORRECTION THIS IMPLEMENTS
------------------------------
The OFAT grid measured every knob against a baseline that is out of the market 99.87% of the
time (MU_LONG: 19 trades / 2.3yr / 0.13% time-in-market vs b&h +633%). In that regime almost
nothing binds, which is why ~80% of cells came back `inert`. Testing "works backwards and
starts with 0.1% time in the market, which is absolutely useless" (user).

The right direction is subtractive:

  STAGE 0  ALL EXIT KNOBS OFF  -> you never leave a position -> ~100% time-in-market, and the
           return IS buy-and-hold. This is the FLOOR, and it is the number every later config
           must beat. If stage 0 does NOT reproduce ~b&h, some exit path is not behind a flag
           and must be found before any tuning is meaningful — that alone is worth the run.
  STAGE 1  Turn exits back on ONE AT A TIME, sweeping each one's parameter values. An exit
           earns its place only if it keeps time-in-market in the target band (default 70-80%)
           AND beats b&h. Anything that cuts exposure without beating b&h is destroying money
           you would have made by doing nothing.
  STAGE 2  Then the same for entries: all entry paths OFF, on one at a time, sweep each.

The wt_5m cross alone sits at ~50% time-in-market — the right ballpark; it is lagging and
churning, and the remaining ~1,600 switches exist to find the RIGHT 50-80%, not to claw up
from zero.

Rows land under campaign '<CAMPAIGN>__ladder' so they never mix with the OFAT grid, and carry
tier='ENGINE' + the full overrides_json like every other faithful cell.

Usage (S1):
  python tools/exposure_ladder.py stage0 --symbol MU --side LONG
  python tools/exposure_ladder.py stage1 --symbol MU --side LONG
  python tools/exposure_ladder.py stage2 --symbol MU --side LONG
  python tools/exposure_ladder.py report --symbol MU --side LONG
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

SBX = Path(os.environ.get("V8_SBX", "/home/niels/binance-sandbox"))
if not SBX.exists():
    SBX = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SBX))
sys.path.insert(0, str(SBX / "tools"))
os.environ.setdefault("PSC_CAMPAIGN", "stocks_baseline_v2_s4h")
import persym_baseline_campaign as psc  # noqa: E402
import param_results_store as prs  # noqa: E402

MANIFEST = SBX / "data" / "param_sweep_manifest_tradier.json"
LADDER_CAMPAIGN = psc.CAMPAIGN + "__ladder_v2"
EXIT_PAT = re.compile(r"EXIT|STOP|TRAIL|CLOSE|NOLOSS|R1_|R2_|R3_|GIVEBACK|BREAKEVEN|HARVEST"
                      r"|SLOPE_FLIP|PROFIT_LOCK|CROSSUNDER|REDUCE|TP_|TAKE_PROFIT", re.I)
ENTRY_PAT = re.compile(
    r"ENTRY|OPEN|REENTRY|FORCE_OPEN|BREAKOUT|K_ZONE|ARROW|SCALP|ACTIVATION",
    re.I,
)
# The entry under test. wt_5m cross alone = ~50% time-in-market (lab, MU_LONG) — the ballpark
# the whole exercise is about. config knob keeps the legacy WT_3M_ name; code reads wt1_5m.
ENTRY_ON = {"WT_3M_FORCE_OPEN_ENABLED": True, "WT_3M_FORCE_OPEN_BYPASS_GATES": True}

# USER 2026-07-22: open with the LADDER instead of the wt_5m cross — quantity varies by where
# price sits in the "grey zones" (the regression band, calculate_regression_band in
# tradier_rankings: +/- N*stdev around calculate_regression_slope_line) and by how steep the HTF
# slope is. Deeper in the channel + steeper slope = bigger clip. This is the band-capture system
# (LR_BAND_* + BAND_SLOPE_SIZING_V2_*), user-reported as proven to beat b&h when exited well.
#
# R2_MIN=0.0 on purpose: at the 0.7 default, lrL_r2_D >= 0.5 co-occurs with lower-band + rising
# slope on ZERO bars, which makes the D band entry structurally unfireable (2026-07-20 measurement).
# ENTRY_PRIORITY=True on purpose: evaluated last in the cascade it fired 0 times on 3,189
# eligible ARM bars.
ENTRY_BAND = {
    "LR_BAND_ENTRY_ENABLED": True, "LR_BAND_ENTRY_TF": "D", "LR_BAND_ENTRY_R2_MIN": 0.0,
    "LR_BAND_ENTRY_PRIORITY": True, "LR_BAND_ENTRY_LO": 0.6,
    "LR_BAND_REGIME_ENABLED": True, "LR_BAND_REGIME_MAX_PB": 0.6,
    "LR_BAND_SIZE_DEPTH_GAIN": 1.0, "LR_BAND_SIZE_SLOPE_GAIN": 1.0,
    "BAND_SLOPE_SIZING_V2_ENABLED": True, "BAND_SLOPE_SIZING_V2_TF": "D",
    # the green arrow: multi-TF band-depth+slope score gates re-entry after a harvest
    "MTF_ARROW_ENTRY_ENABLED": True, "MTF_ARROW_THETA": 0.05,
    "MTF_ARROW_SIZE_GAIN": 1.0, "MTF_ARROW_SIZE_MAX": 4.0,
    # HTF gate off: the band/arrow score IS the gate; the momentum cascade otherwise vetoes it
    "WT_DC_HTF_GATE": "none", "HTF_ALIGN_REQUIRED_TRADIER": 0,
}

# The ONE exit allowed in the base config: harvest at the TOP of the 4h/D regression channel,
# then let the arrow re-enter on the next green arrow at the same or a lower TF. Everything
# else stays off, so this measures the band system alone against b&h.
EXIT_BAND_TOP = {
    "LR_BAND_HARVEST_ENABLED": True, "LR_BAND_HARVEST_HI": 0.7, "LR_BAND_HARVEST_FRAC": 1.0,
}


def manifest():
    return json.loads(MANIFEST.read_text())["params"]


def accepted_overrides(symbol, side):
    """Return the latest live-accepted per-key recipe, if one exists."""
    path = SBX / "data" / "hourly_reconfig" / psc.ACCOUNT / "active_config.json"
    try:
        row = json.loads(path.read_text()).get(f"{symbol}_{side}") or {}
        values = row.get("overrides") or {}
        return dict(values) if isinstance(values, dict) else {}
    except Exception:
        return {}


# Polarity is NOT inferable from the name. Measured 2026-07-22: of 144 boolean exit knobs only
# 92 end in _ENABLED — the other 52 break the convention, and the very first stage-0 run leaked
# because STRUCTURAL_RANGE_SHIFT_EXIT (no _ENABLED suffix) stayed on and produced 100% of the
# 1,310 closes. Read the real config types, and respect the three polarities:
#   normal switch  (X_ENABLED / X_EXIT / X_STOP)  -> False disables
#   ABLATION_DISABLE_X                            -> TRUE disables
#   REQUIRE_* / *_REQUIRES_*                      -> a CONDITION on an exit, not a switch;
#                                                    forcing it would loosen, not disable. Skip.
# FOURTH SHAPE: numeric THRESHOLDS. They disable by being pushed OUT OF REACH, and the
# direction is not inferable from the name or type — it depends on the comparison in the code.
# Each must be recorded explicitly. Found by stage-0 runs that still leaked after the boolean
# and string sweeps: WT_DC_EXIT fires on `score >= WT_DC_EXIT_THRESHOLD` (default 30), and the
# DELTA_EXIT family fires whenever `gain >= NOLOSS_MIN_PROFIT_PCT_TRADIER` (default 0.01, i.e.
# essentially always) — together ~77 of the surviving closes on MU_LONG.
THRESHOLD_OFF = {
    "WT_DC_EXIT_THRESHOLD": 9999.0,            # score >= thr  -> unreachable high
    "NOLOSS_MIN_PROFIT_PCT_TRADIER": 9999.0,   # gain  >= thr  -> unreachable high
    "NOLOSS_MIN_PROFIT_PCT": 9999.0,
    "EXIT_ALGO_SCORE_MIN": 9999.0,
    "WT_DC_EXIT_STALE_MAX_S": 0.0,             # staleness window -> never stale-exits
}
ENTRY_THRESHOLD_OFF = {
    # WT_DC_ENTRY fires when score >= threshold. Both aliases are consumed by the
    # Tradier engine/live path; disabling only the unprefixed name leaves WT_DC_ENTRY
    # producing trades during supposedly isolated entry replays.
    "WT_DC_ENTRY_THRESHOLD": 9999.0,
    "TRA_WT_DC_ENTRY_THRESHOLD": 9999.0,
}

ABLATION = re.compile(r"^ABLATION_DISABLE_", re.I)
CONDITION = re.compile(r"REQUIRE", re.I)


def _cfg_bools():
    try:
        import config_tradier
        cfg = config_tradier.TradierConfig
        return {n: getattr(cfg, n) for n in dir(cfg) if isinstance(getattr(cfg, n, None), bool)}
    except Exception:
        return {}


def _cfg_strs():
    try:
        import config_tradier
        cfg = config_tradier.TradierConfig
        return {n: getattr(cfg, n) for n in dir(cfg)
                if isinstance(getattr(cfg, n, None), str) and not n.startswith("_")}
    except Exception:
        return {}


def off_switches(pat, anti=None):
    """{knob: value_that_disables_it} — the COMPLETE off-set for a family.

    Three switch shapes, none of them inferable from the name alone (all three were found the
    hard way by stage-0 runs that failed to reach b&h):
      boolean            -> False disables
      ABLATION_DISABLE_* -> TRUE disables (inverted)
      string TF/TYPE     -> "None" disables. `LONG_STRUCT_EXIT_TF='D'` drove HYBRID_STRUCT_EXIT_D
                            (69 of 149 closes) and `MTF_DC_REJECT_EXIT_TF='1h'` is the exit
                            churning MU flat LIVE — a boolean-only sweep can never switch
                            either of them off.
    REQUIRE* knobs are CONDITIONS on an exit, not switches: forcing them loosens rather than
    disables, so they are skipped."""
    out = {}
    for n in self_pat_filter(_cfg_bools(), pat, anti):
        out[n] = True if ABLATION.search(n) else False
    for n in self_pat_filter(_cfg_strs(), pat, anti):
        out[n] = "None"
    if pat is EXIT_PAT:
        out.update(THRESHOLD_OFF)
    elif pat is ENTRY_PAT:
        out.update(ENTRY_THRESHOLD_OFF)
    return out


def self_pat_filter(d, pat, anti):
    for n in d:
        if not pat.search(n) or (anti and anti.search(n)) or "OPTION" in n.upper():
            continue
        if CONDITION.search(n):
            continue
        yield n


def flags(pat, anti=None):
    """Knob names to re-enable one at a time (the switches, not the conditions)."""
    return sorted(off_switches(pat, anti))


def registry_role(knob):
    """Role from tools/knob_registry.py — one vocabulary for stocks and crypto."""
    global _REG
    try:
        if _REG is None:
            _REG = json.loads((SBX / "data" / "knob_registry.json").read_text()).get("tradier", {})
    except Exception:
        _REG = {}
    return (_REG or {}).get(knob, {}).get("role", "MAIN_SWITCH")


_REG = None


def params_of(prefix):
    """Sweepable (param, values) in the registry family containing `prefix`.

    String-prefix grouping silently mixed unrelated controls and omitted valid
    companions (for example WT families whose names do not share the same
    textual stem). The generated registry is now the sole family authority.
    """
    registry_role(prefix)  # lazy-load _REG
    family = (_REG or {}).get(prefix, {}).get("family")
    if not family:
        return []
    out = []
    for n, v in manifest().items():
        if not isinstance(v, dict) or not v.get("sweepable") or not v.get("test_values"):
            continue
        if (_REG or {}).get(n, {}).get("family") == family:
            out.append((n, list(v["test_values"])))
    return out


def all_exits_off():
    return off_switches(EXIT_PAT)


def all_entries_off():
    return off_switches(ENTRY_PAT, anti=EXIT_PAT)


V8_RESULT_RE = re.compile(r"V8_RESULT:\s*(.*)")


def run_engine_capture(symbol, tag):
    """Parse the V8_RESULT emitted by the same engine invocation as run_symbol.

    The engine marks still-open positions to market at the final bar
    (backtest_v8_engine.py:5470-5490) and folds them into V8_RESULT — but it never writes them
    into the trades JSONL. So a config that HOLDS (which is the whole point of the ladder) shows
    zero closed trades and reads as 0.00% gain if you only sum the trade list. That is exactly
    how stage 1 produced a table of zeros. Read the engine's number instead of re-deriving it;
    it is also the one that obeys the CLAUDE.md rule that open positions must be MtM'd."""
    result_path = psc.TRADES_ROOT / tag / f"v8result__{symbol}.txt"
    if not result_path.exists():
        return None
    match = V8_RESULT_RE.search(result_path.read_text())
    if not match:
        return None
    d = {}
    for part in match.group(1).split():
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                d[k] = float(v)
            except ValueError:
                d[k] = v
    return d


def run_cell(symbol, side, tag, overrides, args):
    """One faithful Tier-2 run; returns the metric dict (or None if the engine failed).

    V8_DISABLE_PER_SYM=1 is MANDATORY here. `tradier_manage._cfg()` resolves the per-symbol
    overlay (data/hourly_reconfig/<acct>/active_config.json) FIRST and falls through to
    getattr(config, ...) LAST — which is exactly where a V8 sweep override lands. So for any key
    with a per-sym entry the overlay silently WINS over the override, and the run measures the
    overlay instead of the config under test. Found 2026-07-22: MU_SHORT carries
    PARTIAL_PROFIT_LOCK_ENABLED=True in its overlay, so PPL kept firing while the exit inventory
    had it False. 127 of 129 keys have overlay entries — without this env var the whole ladder
    would be measuring the wrong config (tradier_manage.py:666,695)."""
    os.environ["V8_DISABLE_PER_SYM"] = "1"
    os.environ["V8_LADDER_ONLY_SIDE"] = side
    if tag.startswith(("LADDER0__", "LADDER1__")):
        os.environ["V8_LADDER_FORCE_INITIAL_SIDE"] = side
    else:
        os.environ.pop("V8_LADDER_FORCE_INITIAL_SIDE", None)
    engine_tag = f"L2__{tag}"
    by_side = psc.run_symbol(
        symbol, overrides, engine_tag, timeout=args.timeout, min_avail=args.min_avail
    )
    if by_side is None:
        return None
    trades = by_side.get(side, [])
    years, bh_long = psc.sym_years_and_bh(symbol, psc.START)
    years = years or psc.years_since(psc.START)
    rets = [float(t["pnl_pct"]) for t in trades]
    m = psc.hold_metrics(trades, years, psc.key_metrics(rets, years, bh_long, side))
    m.pop("capture_vs_bh", None)
    # Raw NPZ B&H starts at the first premarket bar. Tier-2 may only seed a
    # Tradier position at the first RTH bar, so compare the floor against the
    # independently reconstructed first-tradable-entry -> final-mark return.
    # Keep raw NPZ B&H in the report as context; do not fail a valid 99.9% RTH
    # hold because the benchmark bought hours before trading was allowed.
    if trades:
        entry_px = float(trades[0].get("entry_price") or 0.0)
        exit_px = float(trades[-1].get("exit_price") or 0.0)
        if entry_px > 0.0 and exit_px > 0.0:
            gross = (exit_px - entry_px) / entry_px * 100.0
            m["_tradable_bh_pct"] = gross if side == "LONG" else -gross
    # Overlay the engine's own MtM-inclusive totals: a held position contributes nothing to the
    # closed-trade list, so without this every holding config reports 0.00%.
    v8 = run_engine_capture(symbol, engine_tag)
    if v8:
        side_l = side.lower()
        m["time_in_mkt_pct"] = float(v8.get(f"time_in_mkt_{side_l}_pct", 0.0))
        m["_entry_events"] = int(v8.get(f"opens_{side_l}", 0))
        m["_real_closes"] = int(v8.get("real_closes", 0))
        m["_mtm_count"] = int(v8.get("mtm_count", 0))
    return m


def store(con, symbol, side, param, value, m, overrides, tag, stage):
    # Invalid/unseeded zero-trade outputs are diagnostics, not matrix evidence.
    # Skip them so one disconnected path cannot abort a multi-symbol campaign.
    if int(m.get("trades", 0) or 0) <= 0 and int(m.get("_mtm_count", 0) or 0) <= 0:
        print(f"[store] skip zero-trade diagnostic {symbol}_{side} {param}={value}", flush=True)
        return
    prs.insert_cell(con, {
        "mode": psc.MODE, "symbol": symbol, "side": side, "campaign": LADDER_CAMPAIGN,
        "param": param, "value_json": str(value), "value_num": None, **m,
        "delta_vs_baseline_gain_mo": None, "ts": psc.now_iso(),
        "overrides_json": json.dumps(overrides), "stamp": psc.stamp(), "tier": "ENGINE",
        "source_file": f"exposure_ladder/{stage}/{symbol}_{side}/{tag}"})


def verdict(m, bh_pct, lo, hi):
    """Did this config keep exposure in band AND beat buy-and-hold?"""
    tim = m.get("time_in_mkt_pct") or 0.0
    gain = m.get("acc_gain_pct") or 0.0
    return {"time_in_mkt_pct": round(tim, 2), "acc_gain_pct": round(gain, 2),
            "bh_pct": round(bh_pct or 0.0, 2),
            "beats_bh": bool(bh_pct is not None and gain > bh_pct),
            "in_band": bool(lo <= tim <= hi)}


def main():
    ap = argparse.ArgumentParser()
    # USER 2026-07-22 six-step protocol, per ticker:
    #   stage0  100% in the market (all exits off) — the floor, and the inventory completeness test
    #   stage1  switch ON each EXIT main category one at a time, tune its sub-settings to max vs b&h
    #   stage2  switch OFF all ENTRY paths, run each ONE back on to max gain vs b&h
    #   stage3  switch EVERYTHING on, then flip each main category on/off to max vs b&h  => BASELINE
    #   report  the ladder table;  verify  names exits still escaping the inventory
    ap.add_argument("stage", choices=["stage0", "stage1", "stage2", "stage3", "report", "verify"])
    ap.add_argument("--symbol", default="MU")
    ap.add_argument("--side", default="LONG", choices=["LONG", "SHORT"])
    ap.add_argument("--band-lo", type=float, default=70.0)
    ap.add_argument("--band-hi", type=float, default=80.0)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--min-avail", type=int, default=3000)
    ap.add_argument("--limit", type=int, default=0, help="max knobs this pass (0 = all)")
    ap.add_argument("--only-knob", default="",
                    help="run one exact stage1/stage2 main switch (Tier-1 shortlist replay)")
    ap.add_argument("--knob-value", default=None,
                    help="explicit value for --only-knob (true/false, number or string)")
    ap.add_argument("--combo", default="",
                    help="comma-separated companion overrides KNOB=VALUE for interaction replay")
    ap.add_argument("--shard", default="", help="i/N — run only this slice of the knob list so "
                                                "N workers can share one stage across the box")
    ap.add_argument("--entry", default="accepted", choices=["accepted", "wt5m", "band"],
                    help="accepted = latest live per-key recipe (fallback wt5m); "
                         "wt5m = WT_3M_FORCE_OPEN cross; band = LR_BAND ladder sized by "
                         "grey-zone depth + HTF slope, with MTF arrow re-entry")
    ap.add_argument("--ladder-bottom", type=float, default=None, help="size mult at the LOWER band")
    ap.add_argument("--ladder-top", type=float, default=None, help="size mult at the UPPER band")
    ap.add_argument("--ladder-mode", default=None, choices=["linear", "center_plateau"])
    ap.add_argument("--tag-suffix", default="", help="distinguish variant cells/cell-dirs")
    ap.add_argument("--base-exit", default="none", choices=["none", "band_top"],
                    help="band_top = harvest at the 4h/D regression-channel top, the only exit "
                         "allowed in the base config")
    a = ap.parse_args()
    sym, side = a.symbol.upper(), a.side
    con = prs.connect()
    _y, bh_long = psc.sym_years_and_bh(sym, psc.START)
    bh = bh_long if side == "LONG" else (-bh_long if bh_long is not None else None)

    def parse_combo(raw):
        out = {}
        for item in (raw or "").split(","):
            if not item.strip() or "=" not in item:
                continue
            key, value = item.split("=", 1)
            value = value.strip()
            if value.lower() in ("true", "false"):
                parsed = value.lower() == "true"
            else:
                try:
                    parsed = float(value)
                except ValueError:
                    parsed = value
            out[key.strip()] = parsed
        return out

    combo_overrides = parse_combo(a.combo)

    if a.stage == "verify":
        # Read the last stage-0 trade file and list the exit families that STILL fired. Each one
        # is an exit missing from the inventory: find its switch, and it belongs on the Exit sheet.
        import glob
        from collections import Counter
        pat = str(psc.TRADES_ROOT / f"LADDER0__{sym}_{side}" / f"cell__{sym}.jsonl")
        files = glob.glob(pat)
        if not files:
            print(f"no stage0 trades at {pat} — run stage0 first")
            return
        rows = [json.loads(l) for l in open(files[0]) if l.strip()]
        pn = [r for r in rows if r.get("pnl_pct") is not None
              and str(r.get("side", "")).upper() == side]
        fams = Counter(re.split(r"[_ ]g?-?[0-9]", str(r.get("exit_reason")))[0] for r in pn)
        off = all_exits_off()
        print(f"{sym}_{side}: {len(pn)} closes survived ALL {len(off)} exit switches being off.")
        print("Each family below is an exit NOT yet in the inventory — find its switch:")
        for fam, n in fams.most_common():
            print(f"   {n:5d}  {fam}")
        if not pn:
            print("   (none — inventory is COMPLETE, stage 0 is the true floor)")
        return

    if a.stage == "report":
        rows = con.execute(
            "SELECT param, value_json, time_in_mkt_pct, acc_gain_pct, trades, source_file "
            "FROM param_cells WHERE campaign=? AND symbol=? AND side=? ORDER BY acc_gain_pct DESC",
            (LADDER_CAMPAIGN, sym, side)).fetchall()
        print(f"{sym}_{side}  b&h={bh}%   band={a.band_lo}-{a.band_hi}% time-in-market")
        print(f"{'param':46s} {'value':10s} {'TIM%':>7s} {'gain%':>10s} {'trades':>7s}  verdict")
        for p, v, tim, g, tr, _sf in rows:
            ok = "KEEP" if (bh is not None and (g or 0) > bh and a.band_lo <= (tim or 0) <= a.band_hi) else ""
            print(f"{str(p)[:46]:46s} {str(v)[:10]:10s} {tim or 0:7.2f} {g or 0:10.2f} {tr or 0:7d}  {ok}")
        return

    if a.entry == "band":
        base = dict(ENTRY_BAND)
    elif a.entry == "accepted":
        base = accepted_overrides(sym, side) or dict(ENTRY_ON)
    else:
        base = dict(ENTRY_ON)
    # variant knobs must land in the OVERRIDE, not an env var — the engine only sees the JSON
    if a.ladder_bottom is not None:
        base["LR_BAND_LADDER_BOTTOM_MULT"] = a.ladder_bottom
    if a.ladder_top is not None:
        base["LR_BAND_LADDER_TOP_MULT"] = a.ladder_top
    if a.ladder_mode:
        base["LR_BAND_LADDER_MODE"] = a.ladder_mode
    if a.stage in ("stage0", "stage1"):
        base.update(all_exits_off())
    if a.base_exit == "band_top":
        base.update(EXIT_BAND_TOP)   # re-enable exactly one exit on top of the all-off floor
    if a.stage == "stage2":
        base.update(all_entries_off())

    if a.stage == "stage0":
        # THE FLOOR + the completeness test for the exit inventory.
        #
        # The PASS condition is CLOSES == 0 on the focus side, not a gain figure. A position that
        # never exits emits no completed-trade record at all (pnl_pct only exists on closes), so
        # "held to the end" reads as trades=0 / 0% time-in-market through the trade list — which
        # is exactly how an earlier version of this function reported a SUCCESSFUL hold as a
        # total failure. Counting closes sidesteps that entirely: zero closes means nothing
        # exited, which means the return IS buy-and-hold by construction.
        m = run_cell(sym, side, f"LADDER0__{sym}_{side}{a.tag_suffix}", base, a)
        if m is None:
            print("[stage0] engine failed — no cell written, rerun")
            return
        closes = int(m.get("_real_closes") or 0)
        opens = int(m.get("_entry_events") or 0)
        tim = float(m.get("time_in_mkt_pct") or 0.0)
        gain = float(m.get("acc_gain_pct") or 0.0)
        store(con, sym, side, "LADDER_STAGE0", "all_exits_off" + (a.tag_suffix or ""), m, base, "stage0", "stage0")
        tradable_bh = m.get("_tradable_bh_pct")
        print(
            f"[stage0] {sym}_{side} ALL EXITS OFF ({len(base)} switches) -> "
            f"opens={opens} real_closes={closes} TIM={tim:.2f}% gain={gain:.2f}% "
            f"tradable_b&h={tradable_bh}% raw_npz_b&h={bh}%"
        )
        floor_ok = (
            opens >= 1 and closes == 0 and tim >= 99.0
            and tradable_bh is not None
            and abs(gain - tradable_bh) <= max(1.0, abs(tradable_bh) * 0.01)
        )
        if floor_ok:
            print("         FLOOR REACHED: opened, held, exposure and return reconcile to b&h. "
                  "Exit inventory is COMPLETE for this key; stage 1 may begin.")
        else:
            print("         *** FLOOR FAILED. Refusing the old closes==0 shortcut: require an "
                  "actual open, zero real closes, >=99% TIM, and gain ~= b&h.")
        return

    if a.stage == "stage3":
        # STEP 5: everything ON, then flip each MAIN CATEGORY off/on and keep whichever wins.
        # This is where interactions show up — a category that helped alone can hurt in company.
        base = accepted_overrides(sym, side) or dict(ENTRY_ON)
        mains = [k for k in flags(EXIT_PAT) + flags(ENTRY_PAT, anti=EXIT_PAT)
                 if registry_role(k) == "MAIN_SWITCH"]
        print(f"[stage3] {sym}_{side}: ALL ON, flipping {len(mains)} main categories one at a time")
        for i, knob in enumerate(mains, 1):
            for val in (False, True):
                ovr = dict(base)
                ovr[knob] = (not val) if ABLATION.search(knob) else val
                tag = f"LADDER3__{knob}__{val}".replace("/", "_")[:120]
                m = run_cell(sym, side, tag, ovr, a)
                if m is None:
                    continue
                store(con, sym, side, knob, val, m, ovr, tag, "stage3")
                v = verdict(m, bh, a.band_lo, a.band_hi)
                mark = "KEEP" if v["beats_bh"] else ""
                print(f"  [{i}/{len(mains)}] {knob}={val}  TIM={v['time_in_mkt_pct']}%  "
                      f"gain={v['acc_gain_pct']}%  bh={v['bh_pct']}%  {mark}", flush=True)
        con.close()
        return

    knobs = flags(EXIT_PAT) if a.stage == "stage1" else flags(ENTRY_PAT, anti=EXIT_PAT)
    if a.only_knob:
        # Explicit shortlist replays may name a condition/filter that is not captured by
        # the broad path regex (for example GOLDEN_RULE_REQUIRE_ACTIVATION). The registry and
        # manifest are the authority for an explicit request; do not reject a real knob merely
        # because its spelling lacks ENTRY/OPEN.
        knobs = [k for k in knobs if k == a.only_knob]
        if not knobs:
            # An explicit matrix replay can target a cell that exists only in historical
            # param_results (or a newly discovered path not yet in the manifest). Let the
            # engine prove or disprove it; a typo will produce a measured inert result rather
            # than silently skipping the requested work.
            knobs = [a.only_knob]
        if not knobs:
            raise SystemExit(f"--only-knob {a.only_knob!r} is not an eligible {a.stage} main switch")
    if a.limit:
        knobs = knobs[:a.limit]
    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        knobs = [k for j, k in enumerate(knobs) if j % n == i]
        print(f"[shard {i}/{n}] {len(knobs)} knobs")
    print(f"[{a.stage}] {sym}_{side}: {len(knobs)} knobs to re-enable one at a time, "
          f"band {a.band_lo}-{a.band_hi}% TIM, must beat b&h={bh}%")
    for i, knob in enumerate(knobs, 1):
        family_params = params_of(knob) or [(knob, [True])]
        if a.only_knob:
            # An explicit replay is attributable to exactly one field. Do not silently
            # sweep sibling settings from the registry family under an --only-knob label.
            family_params = [(knob, [True, False])]
            if a.knob_value is None:
                family_params = [(knob, [True])]
        for pname, values in family_params:
            for val in values:
                if a.only_knob and a.knob_value is not None and pname == a.only_knob:
                    raw = str(a.knob_value)
                    if raw.lower() in ("true", "false"):
                        values = [raw.lower() == "true"]
                    else:
                        try:
                            values = [float(raw)]
                        except ValueError:
                            values = [raw]
                    if val != values[0]:
                        continue
                ovr = dict(base)
                ovr.update(combo_overrides)
                ovr[knob] = False if ABLATION.search(knob) else True   # this ONE feature back on...
                ovr[pname] = val          # ...at this parameter setting
                tag = (
                    f"LADDER{1 if a.stage == 'stage1' else 2}__{knob}__{pname}__{val}{a.tag_suffix}"
                ).replace("/", "_")[:120]
                if (not a.only_knob) and con.execute("SELECT 1 FROM param_cells WHERE campaign=? AND symbol=? and side=? "
                               "AND param=? AND value_json=? LIMIT 1",
                               (LADDER_CAMPAIGN, sym, side, pname, str(val))).fetchone():
                    continue          # already measured — shards and restarts never redo work
                m = run_cell(sym, side, tag, ovr, a)
                if m is None:
                    continue
                store(con, sym, side, pname, val, m, ovr, tag, a.stage)
                v = verdict(m, bh, a.band_lo, a.band_hi)
                mark = "KEEP" if v["beats_bh"] and v["in_band"] else ""
                print(f"  [{i}/{len(knobs)}] {pname}={val}  TIM={v['time_in_mkt_pct']}%  "
                      f"gain={v['acc_gain_pct']}%  bh={v['bh_pct']}%  {mark}", flush=True)
    con.close()


if __name__ == "__main__":
    main()
