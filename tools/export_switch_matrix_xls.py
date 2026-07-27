#!/usr/bin/env python3
"""export_switch_matrix_xls.py — USER 2026-07-20 spreadsheet spec.

ONE ROW PER (switch, value). ONE COLUMN PER KEY — every symbol in
symbols_trb_long.json (as <SYM>_LONG) and symbols_trb_short.json (as <SYM>_SHORT).
Cell = delta gain/mo of that switch-value vs that key's baseline (blank = not tested yet).

Rows are emitted for the FULL manifest (every switch x every swept value), so the sheet is
the complete work-list: blanks show what still has to run, values fill in as the OFAT
progresses. Sheets:
  Matrix       switch | value | n_tested | mean_delta | best_key | worst_key | <one col per key>
  Coverage     per switch: how many keys tested, how many helped (delta>0), how many hurt
  Baselines    per key: gain/mo, b&h/mo, delta, trades, campaign

Source: data/param_results_stocks.db (param_cells + key_baseline) — the campaign store.
Output: data/reports/SWITCH_MATRIX_TRB.xlsx  (+ .csv.gz for the full width)

Usage (S1 or Mac):  python tools/export_switch_matrix_xls.py [--campaign stocks_baseline_v2_s4h]
"""
import argparse
import csv
import gzip
import json
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "param_results_stocks.db"
FLEET_DB = BASE / "data" / "reports" / "path_fleet" / "queue.db"
CURRENT_ENGINE_CAMPAIGN = "stocks_repaired_20260725_c2"
CURRENT_ENGINE_CUTOFF = "2026-07-26T04:15:00Z"


def key_columns(account):
    # trc temporarily runs trb symbols with the 7D overlay (USER 2026-07-20) — same universe, own matrix file
    prefix = "trb" if account == "trc" else account
    cols = []
    for fname, side in ((f"symbols_{prefix}_long.json", "LONG"), (f"symbols_{prefix}_short.json", "SHORT")):
        p = BASE / fname
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        syms = d if isinstance(d, list) else list(d)
        cols += [f"{str(s).upper()}_{side}" for s in syms]
    seen, out = set(), []
    for c in cols:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def norm_val(v):
    s = str(v).strip()
    low = s.lower()
    if low in ("true", "yes", "on"): return "true"
    if low in ("false", "no", "off"): return "false"
    if low in ("none", "null", ""): return "none"
    try:
        f = float(s)
        if f != f or f in (float("inf"), float("-inf")): return low
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError, OverflowError):
        return low


def manifest_rows(mode="tradier", actionable=True):
    p = BASE / f"data/param_sweep_manifest_{mode}.json"
    if not p.exists(): return []
    man = json.loads(p.read_text()).get("params", {})
    rows = []
    for name, spec in man.items():
        if actionable and isinstance(spec, dict) and not bool(spec.get("sweepable")):
            continue
        vals = None
        if isinstance(spec, dict):
            for k in ("test_values", "values", "sweep_values", "range"):
                if isinstance(spec.get(k), (list, tuple)) and spec[k]:
                    vals = list(spec[k])
                    break
            if vals is None: vals = [spec.get("default")]
        elif isinstance(spec, (list, tuple)): vals = list(spec)
        else: vals = [spec]
        for v in vals: rows.append((name, norm_val(v)))
    return rows


def manifest_inventory(mode="tradier"):
    """Non-actionable settings retained for audit, not shown as blank work."""
    p = BASE / f"data/param_sweep_manifest_{mode}.json"
    if not p.exists():
        return []
    man = json.loads(p.read_text()).get("params", {})
    out = []
    for name, spec in man.items():
        if not isinstance(spec, dict) or bool(spec.get("sweepable")):
            continue
        tier = spec.get("sweep_tier") or ""
        consumed = spec.get("consumed_by") or {}
        if tier == "DEAD" or not any(consumed.values()):
            reason = "DEAD / no live or engine consumer; reconnect before testing"
        elif tier == "LIVE_ONLY":
            reason = "LIVE_ONLY / no faithful backtest consumer"
        else:
            reason = "explicitly sweepable:false in the manifest"
        out.append((name, tier, reason, json.dumps(consumed, sort_keys=True)))
    return sorted(out)


def load_cells(campaign, tier="ENGINE"):
    """Cells for ONE tier only.

    2026-07-21 NO-LIES FIX: this used to read every row of param_cells into one grid. 86% of
    those rows are Tier-1 vec screens whose deltas were subtracted from a Tier-2 ENGINE
    baseline — a tier gap, not a knob effect (MU_LONG: 495 cells all showing a phantom
    +1.3917 %/mo). Tier-1 and Tier-2 numbers are not comparable and MUST NOT share a grid:
    the engine sheet is the truth, the vec sheet is a [DIAGNOSTIC] screen, and a cell whose
    override provably changed nothing (inert=1) reads INERT, never a number.
    Returns (cells, inert_flags, cell_meta, base). `cells` and the visible matrix values are
    delta gain/mo versus B&H; `cell_meta` retains the result fields needed for coloring and
    zero-trade/fingerprint checks."""
    if not DB.exists(): return {}, {}, {}, {}
    con = sqlite3.connect(str(DB))
    cells, inert, cell_meta = {}, {}, {}
    q = ("SELECT symbol, side, param, value_json, delta_vs_baseline_gain_mo, gain_per_mo, inert "
         "FROM param_cells WHERE mode='tradier' AND COALESCE(tier,'ENGINE')=?")
    # The matrix used to display delta_vs_baseline_gain_mo. That baseline is not the user's
    # floor. Keep the legacy delta only as a fallback, but make the visible cell and color logic
    # use the stored, same-key delta_gain_mo_vs_bh from the faithful engine.
    q = ("SELECT symbol, side, param, value_json, delta_gain_mo_vs_bh, delta_vs_baseline_gain_mo, "
         "gain_per_mo, trades, inert, trades_fingerprint, validation_status, "
         "contract_fingerprint, real_closes, reentry_violations "
         "FROM param_cells WHERE mode='tradier' AND COALESCE(tier,'ENGINE')=?")
    args = [tier]
    if campaign:
        q += " AND campaign=?"
        args.append(campaign)
    # More than one campaign can write the same logical cell.  The unrestricted matrix is
    # explicitly a "latest evidence" view, so make overwrite order deterministic instead of
    # relying on SQLite's unspecified scan order.
    if campaign == CURRENT_ENGINE_CAMPAIGN:
        q += (
            " AND ts>=? AND validation_status IN "
            "('PASS','PASS_WITH_CAPACITY_CLAMPS','INCOMPLETE_NO_REAL_CLOSE')"
        )
        args.append(CURRENT_ENGINE_CUTOFF)
    q += " ORDER BY ts"
    expected = {}
    if campaign == CURRENT_ENGINE_CAMPAIGN:
        try:
            try:
                from tools import persym_baseline_campaign as psc
            except ModuleNotFoundError:
                import persym_baseline_campaign as psc
            expected = {
                f"{sym}_{side}": psc.matrix_contract_fingerprint(sym, side)
                for sym, side in con.execute(
                    "SELECT symbol,side FROM param_cells WHERE campaign=? "
                    "UNION SELECT symbol,side FROM key_baseline WHERE campaign=?",
                    (campaign, campaign),
                )
            }
        except Exception:
            expected = {}
    for (sym, side, param, val, bh_delta, legacy_delta, gpm, trades, inrt, fp,
         validation, contract_fp, real_closes, reentry_violations) in con.execute(q, args).fetchall():
        key = f"{sym}_{side}"
        if campaign == CURRENT_ENGINE_CAMPAIGN and contract_fp != expected.get(key):
            continue
        ck = (param, norm_val(val), f"{sym}_{side}")
        delta = bh_delta if bh_delta is not None else legacy_delta
        cells[ck] = delta if delta is not None else gpm
        inert[ck] = bool(inrt)
        cell_meta[ck] = {"bh_delta": bh_delta, "gain_per_mo": gpm, "trades": trades,
                         "fingerprint": fp, "inert": bool(inrt),
                         "validation_status": validation, "real_closes": real_closes,
                         "reentry_violations": reentry_violations}
    if tier == "ENGINE" and campaign != CURRENT_ENGINE_CAMPAIGN:
        try:
            srows = con.execute(
                "SELECT symbol, side, param, value_json, delta_gain_mo, overrides_json "
                "FROM stage_results ORDER BY ts"
            ).fetchall()
        except sqlite3.OperationalError:
            srows = []
        for sym, side, param, val, delta, ojson in srows:
            if delta is None: continue
            key = f"{sym}_{side}"
            ck = (param, norm_val(val), key)
            cells.setdefault(ck, delta)
            cell_meta.setdefault(ck, {"bh_delta": None, "gain_per_mo": None,
                                      "trades": None, "fingerprint": None, "inert": False})
            if ojson:
                try:
                    overrides = json.loads(ojson)
                except Exception:
                    overrides = {}
                # A PACK delta belongs to the pack, not to each knob inside it. Crediting the
                # same number to every constituent is how one result became many identical
                # "winners". Kept only where the pack changed exactly one knob.
                if isinstance(overrides, dict) and len(overrides) == 1:
                    for oname, oval in overrides.items(): cells.setdefault((oname, norm_val(oval), key), delta)
    base = {}
    bq = ("SELECT symbol, side, gain_per_mo, bh_per_mo, delta_gain_mo_vs_bh, trades, campaign, "
          "trades_fingerprint, validation_status, contract_fingerprint "
          "FROM key_baseline WHERE mode='tradier'")
    bargs = []
    if campaign:
        bq += " AND campaign=?"
        bargs.append(campaign)
    if campaign == CURRENT_ENGINE_CAMPAIGN:
        bq += (
            " AND ts>=? AND validation_status IN "
            "('PASS','PASS_WITH_CAPACITY_CLAMPS','INCOMPLETE_NO_REAL_CLOSE')"
        )
        bargs.append(CURRENT_ENGINE_CUTOFF)
    bq += " ORDER BY ts"
    for sym, side, g, bh, d, tr, camp, fp, validation, contract_fp in con.execute(
        bq, bargs
    ).fetchall():
        key = f"{sym}_{side}"
        if campaign == CURRENT_ENGINE_CAMPAIGN and contract_fp != expected.get(key):
            continue
        base[f"{sym}_{side}"] = {"gain_per_mo": g, "bh_per_mo": bh, "delta_vs_bh": d,
                                  "trades": tr, "campaign": camp, "fingerprint": fp,
                                  "validation_status": validation}
    con.close()
    # Equal fingerprints across two values of the same switch are a wiring failure, even when
    # rounding makes the gain deltas look slightly different. Mark all such values red later.
    fps = {}
    for (param, val, key), meta in cell_meta.items():
        fp = meta.get("fingerprint")
        if fp:
            fps.setdefault((param, key, fp), set()).add(val)
    for (param, key, fp), values in fps.items():
        if len(values) > 1:
            for val in values:
                cell_meta[(param, val, key)]["same_value_fingerprint"] = True
    return cells, inert, cell_meta, base


def load_engine_coverage(campaign):
    """Explain why an ENGINE cell is blank without importing VEC evidence.

    The canonical matrix deliberately exposes only contract-matched ``param_cells``.
    This companion index is diagnostic metadata:

    * ``stale`` means an exact engine row exists for the same key/knob/value, but its
      code+NPZ+side fingerprint is no longer current;
    * ``vec_only`` means a vector screen exists, but there is no current exact-engine
      measurement.  It remains blank and is explicitly labelled EXACT_QUEUE_EMPTY;
    * ``fleet_exact`` lists V8 exact replays from the path-fleet ledger.  They are
      informational unless their payload explicitly names one attributable matrix
      knob/value.  Return-% is never silently converted to gain/month.

    Keeping this outside ``load_cells`` is important: no VEC or research-only value can
    enter the ENGINE numeric cell dictionary by accident.
    """
    out = {
        "stale": set(),
        "invalid": set(),
        "vec_only": set(),
        "raw_engine_rows": 0,
        "current_engine_rows": 0,
        "stale_engine_rows": 0,
        "invalid_engine_rows": 0,
        "fleet_exact": [],
        "fleet_exact_attributable": 0,
    }
    if not DB.exists():
        return out
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    valid_statuses = {
        "PASS", "PASS_WITH_CAPACITY_CLAMPS", "INCOMPLETE_NO_REAL_CLOSE",
    }
    rows = con.execute(
        "SELECT symbol,side,param,value_json,validation_status,contract_fingerprint,"
        "ts,source_file FROM param_cells WHERE mode='tradier' "
        "AND COALESCE(tier,'ENGINE')='ENGINE' AND campaign=? AND ts>=? ORDER BY ts",
        (campaign, CURRENT_ENGINE_CUTOFF),
    ).fetchall()
    out["raw_engine_rows"] = len(rows)
    expected = {}
    if rows:
        try:
            try:
                from tools import persym_baseline_campaign as psc
            except ModuleNotFoundError:
                import persym_baseline_campaign as psc
            expected = {
                f"{row['symbol']}_{row['side']}": psc.matrix_contract_fingerprint(
                    row["symbol"], row["side"]
                )
                for row in rows
            }
        except Exception:
            expected = {}
    for row in rows:
        key = f"{row['symbol']}_{row['side']}"
        logical = (row["param"], norm_val(row["value_json"]), key)
        if row["validation_status"] not in valid_statuses:
            out["invalid"].add(logical)
            out["invalid_engine_rows"] += 1
        elif row["contract_fingerprint"] != expected.get(key):
            out["stale"].add(logical)
            out["stale_engine_rows"] += 1
        else:
            out["current_engine_rows"] += 1
    # This query is presence-only.  No VEC metric is selected or returned.
    try:
        for row in con.execute(
            "SELECT DISTINCT symbol,side,param,value_json FROM param_cells "
            "WHERE mode='tradier' AND tier='VEC'"
        ):
            out["vec_only"].add(
                (row["param"], norm_val(row["value_json"]),
                 f"{row['symbol']}_{row['side']}")
            )
    except sqlite3.OperationalError:
        pass
    con.close()

    fleet_db = FLEET_DB
    if fleet_db.exists():
        try:
            fleet = sqlite3.connect(f"file:{fleet_db}?mode=ro", uri=True, timeout=30)
            fleet.row_factory = sqlite3.Row
            for row in fleet.execute(
                "SELECT j.path_id,r.symbol,r.side,r.stage,r.status,"
                "r.strategy_return_pct,r.bh_return_pct,r.same_entry_control_return_pct,"
                "r.payload_json,r.artifact,r.created_at "
                "FROM results r JOIN jobs j ON j.id=r.job_id "
                "WHERE r.exact_replay=1 OR UPPER(r.stage) LIKE '%EXACT%' "
                "ORDER BY r.created_at"
            ):
                payload = {}
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    pass
                param = payload.get("matrix_param") or payload.get("param")
                value = payload.get("matrix_value", payload.get("value_json"))
                attributable = bool(
                    payload.get("attributable")
                    and param is not None
                    and value is not None
                )
                if attributable:
                    out["fleet_exact_attributable"] += 1
                out["fleet_exact"].append({
                    "path_id": row["path_id"], "key": f"{row['symbol']}_{row['side']}",
                    "stage": row["stage"], "status": row["status"],
                    "strategy_return_pct": row["strategy_return_pct"],
                    "bh_return_pct": row["bh_return_pct"],
                    "same_entry_control_return_pct": row["same_entry_control_return_pct"],
                    "matrix_param": param, "matrix_value": value,
                    "attributable": attributable, "artifact": row["artifact"],
                    "created_at": row["created_at"],
                })
            fleet.close()
        except sqlite3.OperationalError:
            pass
    return out


_REG = None


def registry():
    """tools/knob_registry.py output: {mode: {knob: {family, group, role, ...}}}"""
    global _REG
    if _REG is None:
        p = BASE / "data" / "knob_registry.json"
        if not p.exists():
            raise RuntimeError(
                f"required knob registry missing: {p}; refusing to silently destroy family/role grouping"
            )
        _REG = json.loads(p.read_text()).get("tradier", {})
    return _REG


_VERIFIED_DESCRIPTIONS = {
    "WT_3M_FORCE_OPEN_ENABLED": (
        "ENTRY — on stocks, opens/adds when price is on the correct 200-SMA/EMA side and "
        "5m WT is favorable; generally more fills and time-in-market; off=False; per-symbol."
    ),
    "WT_3M_FORCE_OPEN_BUILD_TO_TARGET": (
        "ENTRY/SIZING — permits repeated adds until target notional is reached; generally "
        "more add events and exposure; off=False."
    ),
    "WT_3M_FORCE_OPEN_BYPASS_GATES": (
        "ENTRY BYPASS — skips several execution/risk gates; generally more actual fills; "
        "off=False; currently global-only despite the per-symbol master."
    ),
    "WT_3M_FORCE_OPEN_DIST_PCT": (
        "ENTRY FILTER — minimum distance beyond the 200-SMA required by WT force-open; "
        "higher values generally mean fewer qualifying opens."
    ),
    "MTF_DC_REJECT_EXIT_ENABLED": (
        "EXIT — after a Donchian breakout, closes on rejection back inside the selected band; "
        "generally more closes/lower exposure; off=False; requires MTF_EXIT_USE_COMPOUND=True."
    ),
    "MTF_DC_REJECT_EXIT_LOOKBACK": (
        "RECONNECT — intended Donchian rejection lookback, but no live or engine read site "
        "exists; it cannot affect trades until wired."
    ),
    "DC_LOW4_STOP_ENABLED": (
        "ENTRY-QUALITY FAILURE DIAGNOSTIC / LOSING-CHURN EXIT EVIDENCE — closes a LONG below "
        "dc_low4_5m (SHORT above dc_high4_5m). This is a tight loss/emergency exit used to "
        "expose entries that fail almost immediately; it is NOT a recommended top or "
        "profit-taking exit. Below-B&H measurements remain gray discard evidence; off=False."
    ),
    "R1_DC_LOW4_3M_EMERGENCY_ENABLED": (
        "ENTRY-QUALITY FAILURE DIAGNOSTIC / LOSING-CHURN EXIT EVIDENCE — despite the legacy "
        "'3M' config name, stocks use frozen dc_low4_5m/dc_high4_5m. A breach closes the failed "
        "entry at a loss; it is NOT a top/profit-taking path. Use its results to diagnose entry "
        "quality and churn, never to recommend profit capture; off=False; per-symbol."
    ),
    "PARTIAL_PROFIT_LOCK_ENABLED": (
        "EXIT — reduces part of a winner, installs a breakeven-buffer stop, then may close the "
        "remainder; more order events, but completed-trade/exposure effect is empirical; off=False."
    ),
    "STRUCTURAL_RANGE_SHIFT_EXIT": (
        "EXIT — closes near a selected BB/DC range after overbought/oversold reversal; generally "
        "more closes/lower exposure; off=False; currently global-only and engine parity needs review."
    ),
    "LONG_STRUCT_EXIT_TF": (
        "CURRENT DIRECT EXIT — selects the timeframe whose lower-high+lower-low break closes a "
        "LONG immediately. The armed structural-break → WT1 rebound-top → subsequent lower-price "
        "sequence is a VEC-REJECTED BASELINE (structural_wt_rebound_20260726T062654Z; median "
        "alpha -4.70pp, 0/6 valid folds beat B&H), with NO Tier-2 result. Keep it out of matrix "
        "promotion. A frozen profit-gated grid made 9/9 exits winners but beat B&H in only 1/4 "
        "validation folds (median alpha -1.04pp) because delayed E10 reclaim chased price. "
        "Resting-reclaim execution remains research-only and the reentry obligation must remain "
        "latched until reopened. A MU structural probe made 2.0006x B&H but is rejected because "
        "the comparable frozen ladder + 4h N30 E02 control made 6.4117x B&H. off='None'; "
        "per-symbol."
    ),
    "SHORT_STRUCT_EXIT_TF": (
        "CURRENT DIRECT EXIT — selects the timeframe whose higher-high+higher-low break closes a "
        "SHORT immediately. The mirrored armed-break → WT1 bottom → subsequent higher-price "
        "sequence has NO Tier-2 result; the tested LONG baseline was VEC-REJECTED "
        "(structural_wt_rebound_20260726T062654Z). Do not infer SHORT performance from LONG; "
        "profit/MFE-gated variants remain research-only and reentry must stay latched until "
        "reopened. off='None'; per-symbol."
    ),
    "GOLDEN_RULE_REQUIRE_ACTIVATION": (
        "ENTRY CONDITION — requires the Golden Rule activation state before the qualifying entry; "
        "False loosens that requirement and may permit more entries; signal-dependent; Tier-2 "
        "fingerprint currently unchanged between True/False, so reconnect before promotion."
    ),
    "MTF_ARMED_ENTRY_ENABLED": (
        "ENTRY — permits entries only after the multi-timeframe armed state is established; "
        "generally increases qualifying entries when enabled; off=False; requires MTF arm state."
    ),
    "BB_PULLBACK_GATE_ENABLED": (
        "ENTRY FILTER — requires a Bollinger pullback condition before entry; enabling can reduce "
        "entries, while disabling admits more signals; off=False; signal-dependent."
    ),
}

_DC_LOW4_DIAGNOSTIC_PARAMS = {
    "DC_LOW4_STOP_ENABLED",
    "R1_DC_LOW4_3M_EMERGENCY_ENABLED",
}


def describe_knob(param):
    """Conservative human description; numeric polarity is never guessed."""
    if param in _VERIFIED_DESCRIPTIONS:
        return _VERIFIED_DESCRIPTIONS[param]
    info = registry().get(param, {})
    group = str(info.get("group") or "OTHER").upper()
    role = str(info.get("role") or "SETTING").replace("_", " ").lower()
    scope = "per-symbol" if info.get("per_sym") else "global-only"
    off = info.get("off_value")
    words = param.replace("_", " ").lower()
    if "BYPASS" in param:
        effect = "bypasses a condition; enabling usually permits more actions"
    elif "COOLDOWN" in param or "MIN_HOLD" in param:
        effect = "timing constraint; increasing it usually reduces action frequency"
    elif group == "ENTRY" and info.get("kind") == "bool":
        effect = "entry path/filter; enabling a path usually increases opens, while enabling a filter can reduce them"
    elif group == "EXIT" and info.get("kind") == "bool":
        effect = "exit path/filter; enabling a path usually increases closes and lowers exposure"
    elif group == "SIZING" or any(x in param for x in ("SIZE", "MULT", "CAPITAL", "BUDGET")):
        effect = "primarily changes position quantity, not signal count"
    elif any(x in param for x in ("THRESHOLD", "_MIN", "_MAX", "LOOKBACK", "_TF")):
        effect = "selector/threshold; trade-count direction is signal-dependent and must be measured"
    else:
        effect = f"controls {words}; trade-count direction must be measured"
    off_text = f"; off={off!r}" if off is not None else ""
    return f"{group} {role.upper()} — {effect}{off_text}; {scope}."


def matrix_cell_state(param, meta):
    """Color one measured cell without losing diagnostic evidence."""
    if meta is None:
        return None
    bh_delta = meta.get("bh_delta")
    gain = meta.get("gain_per_mo")
    trades = meta.get("trades")
    if (
        (trades is not None and trades < 1)
        or meta.get("same_value_fingerprint")
        or meta.get("validation_status") != "PASS"
        or (meta.get("real_closes") or 0) < 1
        or (meta.get("reentry_violations") or 0) > 0
    ):
        return "red"
    if bh_delta is None:
        return "white"
    if abs(float(bh_delta)) <= 0.00005:
        return "red"
    if float(bh_delta) > 0:
        return "green"
    if param in _DC_LOW4_DIAGNOSTIC_PARAMS:
        return "gray"
    if gain is not None and float(gain) > 0:
        return "white"
    return "gray"


def _split_main_sub(row):
    """[param, value, ...] -> [main_switch, sub_setting, value, ...].

    A knob whose registry role is MAIN_SWITCH owns column A and leaves B blank; every other
    knob in the family sits in column B under its family's main switch, so the whole feature
    reads as one block: the on/off, then its settings and their value ladders."""
    param, val, rest = row[0], row[1], row[2:]
    d = registry().get(param, {})
    role = d.get("role", "")
    fam = d.get("family") or param
    if role == "MAIN_SWITCH":
        return [param, "", val] + rest
    mains = sorted(
        k for k, v in registry().items()
        if v.get("family") == fam and v.get("role") == "MAIN_SWITCH"
    )
    main = next((k for k in mains if k == f"{fam}_ENABLED"), None)
    if main is None:
        main = next((k for k in mains if k.endswith("_ENABLED")), None)
    if main is None and len(mains) == 1:
        main = mains[0]
    if not main or main == param:
        # no master switch for this family (packs, TF selectors, orphan knobs): it heads its own
        # block rather than being listed as a sub-setting of itself
        return [param, "", val] + rest
    return [main, param, val] + rest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default=None, help="restrict to one campaign (default: all)")
    ap.add_argument("--only-tested", action="store_true", help="skip switch-values with no data yet")
    ap.add_argument("--account", default="trb", choices=["trb", "trc"], help="stock account universe for key columns")
    ap.add_argument("--tier", default="ENGINE", choices=["ENGINE", "VEC"],
                    help="ENGINE = faithful Tier-2 (the only promotable truth); "
                         "VEC = Tier-1 screen, exported to its own [DIAGNOSTIC] file. NEVER mixed.")
    a = ap.parse_args()
    if a.tier == "ENGINE" and a.campaign is None:
        # The unrestricted "latest evidence" view silently resurrected pre-fix cells whenever
        # a repaired cell was still blank.  ENGINE now defaults to the exact repaired campaign;
        # historical campaigns remain queryable only by naming one explicitly.
        a.campaign = CURRENT_ENGINE_CAMPAIGN
    suffix = "" if a.tier == "ENGINE" else "_VEC_DIAGNOSTIC"
    OUT_XLSX = BASE / "data" / "reports" / f"SWITCH_MATRIX_{a.account.upper()}{suffix}.xlsx"
    OUT_CSV = BASE / "data" / "reports" / f"SWITCH_MATRIX_{a.account.upper()}{suffix}.csv.gz"
    cols = key_columns(a.account)
    rows = manifest_rows()
    inventory = manifest_inventory()
    cells, inert, cell_meta, base = load_cells(a.campaign, a.tier)
    engine_coverage = (
        load_engine_coverage(a.campaign)
        if a.tier == "ENGINE" and a.campaign == CURRENT_ENGINE_CAMPAIGN
        else None
    )
    known = set(rows)
    actionable_names = {p for p, _v in rows}
    rows += sorted({
        (p, v) for (p, v, _k) in cells
        if p in actionable_names and (p, v) not in known
    })
    # USER 2026-07-21: options not traded — params neither tested nor listed in the work-list
    rows = [pv for pv in rows if "OPTION" not in pv[0]]
    # USER 2026-07-21: WT1-cross trigger rows FIRST in every matrix
    rows.sort(key=lambda pv: (not pv[0].startswith("WT_3M_FORCE_OPEN"), 0))
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    # USER 2026-07-22 layout: read the sheet as a DIAGRAM — column A is the MAIN SWITCH (the
    # True/False that moves a whole group), column B is the SUB-SETTING under it, column C its
    # value ladder (20/30/40...). Family + role come from tools/knob_registry.py so entry and
    # exit read identically in stocks and crypto.
    header = ["main_switch", "sub_setting", "value", "description", "status", "n_tested", "n_inert", "mean_delta",
              "best_key", "best_delta", "worst_key", "worst_delta"] + cols
    META_COLS = header.index(cols[0]) if cols else len(header)
    matrix, coverage, matrix_states = [], [], []
    # USER 2026-07-21: "If 2 fields produce the same results for different settings the function
    # is broken and needs to be fixed." Two mechanically distinct ways that happens, both fatal:
    #   RECONNECT  — every value left the sim bit-identical to baseline: the code never read the
    #                knob (disconnected wiring).
    #   DEGENERATE — the values differ from baseline but not from EACH OTHER: the knob is read
    #                but saturates, so the grid carries no information. This is the class the
    #                WT_DC_ENTRY_THRESHOLD 35/43/55/75 example belongs to — v8_vec_sweep defaults
    #                that knob to 0.0 while live config_tradier runs 45, so every override >=35
    #                shuts the gate and returns 0 trades at every value.
    by_switch = {}
    for param, val in rows:
        by_switch.setdefault(param, []).append(val)
    dead, degenerate = {}, {}
    for p, vs in by_switch.items():
        tested = [(v, c) for v in vs for c in cols if (p, v, c) in cells]
        dead[p] = bool(tested) and all(inert.get((p, v, c), False) for v, c in tested)
        per_value = {}
        for v, c in tested:
            per_value.setdefault(v, {})[c] = cells[(p, v, c)]
        shared = set.intersection(*(set(d) for d in per_value.values())) if len(per_value) > 1 else set()
        degenerate[p] = bool(shared) and len(per_value) > 1 and all(
            len({round(per_value[v][c], 6) for v in per_value}) == 1 for c in shared)
    for param, val in rows:
        vals = {c: cells.get((param, val, c)) for c in cols}
        got = {c: v for c, v in vals.items() if v is not None}
        n_inert = sum(1 for c in got if inert.get((param, val, c)))
        stale_keys = (
            [c for c in cols if (param, val, c) in engine_coverage["stale"]]
            if engine_coverage else []
        )
        invalid_keys = (
            [c for c in cols if (param, val, c) in engine_coverage["invalid"]]
            if engine_coverage else []
        )
        vec_only_keys = (
            [
                c for c in cols
                if (param, val, c) in engine_coverage["vec_only"]
                and (param, val, c) not in engine_coverage["stale"]
            ]
            if engine_coverage else []
        )
        if a.only_tested and not got: continue
        if not got:
            if stale_keys:
                status = "STALE_ENGINE_CONTRACT"
            elif invalid_keys:
                status = "ENGINE_VALIDATION_FAILED"
            elif vec_only_keys:
                status = "EXACT_QUEUE_EMPTY"
            else:
                status = "NOT_ENGINE_TESTED"
        elif dead.get(param):
            status = "RECONNECT"
        elif degenerate.get(param):
            status = "DEGENERATE"
        elif n_inert == len(got):
            status = "INERT_AT_VALUE"
        else:
            status = "OK"
        if got:
            mean_d = round(sum(got.values()) / len(got), 4)
            bk = max(got, key=got.get)
            wk = min(got, key=got.get)
            row = [param, val, describe_knob(param), status, len(got), n_inert, mean_d,
                   bk, round(got[bk], 4), wk, round(got[wk], 4)]
        else:
            row = [param, val, describe_knob(param), status, 0, 0, None, None, None, None, None]
        row = _split_main_sub(row)
        row += [(round(vals[c], 4) if vals[c] is not None else None) for c in cols]
        matrix.append(row)
        states = []
        for c in cols:
            meta = cell_meta.get((param, val, c))
            if vals[c] is None:
                states.append(None)
                continue
            # Red = equal to B&H / not wired. Zero-trade rows are also red: they are a bug,
            # never a safe result. Fingerprint-identical values are red even if rounded deltas
            # differ. Green = beats the floor. White = below B&H but still positive/viable.
            # Gray = shittier/non-viable and should be discarded. dc_low4_5m
            # below-B&H diagnostics are gray even when nominal gain stays >0.
            states.append(matrix_cell_state(param, meta))
        matrix_states.append(states)
        coverage.append([
            param, val, status, len(got), n_inert,
            sum(1 for v in got.values() if v > 0),
            sum(1 for v in got.values() if v < 0),
            len(stale_keys), len(invalid_keys), len(vec_only_keys),
        ])
    # USER 2026-07-21: stocks trigger runs on wt1_5m (WT_FORCE_OPEN_TRIGGER_TF='5m',
    # tradier_manage ~2698) — display 5m; config knob keeps the legacy WT_3M_ name.
    for r in matrix:
        if isinstance(r[0], str) and r[0].startswith("WT_3M_FORCE_OPEN"):
            r[0] = r[0].replace("WT_3M_FORCE_OPEN", "WT1_5M_FORCE_OPEN[cfg:WT_3M]")
        if isinstance(r[1], str) and r[1].startswith("WT_3M_FORCE_OPEN"):
            r[1] = r[1].replace("WT_3M_FORCE_OPEN", "WT1_5M_FORCE_OPEN[cfg:WT_3M]")
    color_map = {(r[0], r[1], str(r[2])): states for r, states in zip(matrix, matrix_states)}
    tier_note = ("# TIER=ENGINE — repaired-contract faithful Tier-2. Historical rows excluded; "
                 "only green, non-inert, real-close rows are promotion candidates.\n"
                 if a.tier == "ENGINE" else
                 "# [DIAGNOSTIC ONLY] TIER=VEC — Tier-1 vectorized screen (v8_vec_sweep, ~85% live parity).\n"
                 "# Deltas are vs the Tier-1 baseline. NEVER comparable to the ENGINE file, never promotion evidence.\n")
    with gzip.open(OUT_CSV, "wt") as fh:
        fh.write(tier_note)
        fh.write("# cell values: delta gain/mo versus the same-key B&H floor (not versus the old trading baseline)\n")
        fh.write("# cell colors: GREEN=beats B&H | RED=equal/zero-trade/fingerprint-identical (wiring bug) | "
                 "WHITE=below B&H but positive/viable | GRAY=shittier/non-viable discard\n")
        fh.write("# status: OK=value moved the sim | INERT_AT_VALUE=this value changed nothing | "
                 "RECONNECT=no value changed anything (knob unread by the code) | "
                 "DEGENERATE=values differ from baseline but not from each other (knob saturates "
                 "or vec/live default mismatch) | STALE_ENGINE_CONTRACT=exact row preserved but "
                 "invalidated by current code+NPZ+side fingerprint | "
                 "ENGINE_VALIDATION_FAILED=exact run exists but failed the repaired contract | "
                 "EXACT_QUEUE_EMPTY=VEC evidence exists but no attributable current exact result | "
                 "NOT_ENGINE_TESTED=no exact ENGINE evidence for this switch/value\n")
        fh.write("# stocks: WT trigger TF = 5m (wt1_5m); rows named WT1_5M_FORCE_OPEN[cfg:WT_3M] map to config knobs WT_3M_FORCE_OPEN_*\n")
        # 2026-07-21: was a naive ",".join — list-valued switches (R2_TF_LIST=['d','15m'], TF
        # whitelists, symbol lists) embed commas, which shifted every key column right for those
        # rows and mis-attributed each cell to the wrong symbol. Same defect class as the crypto
        # column-shift found 2026-07-20. Quote properly; never hand-roll CSV.
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in matrix: w.writerow(["" if x is None else x for x in r])
    # USER 2026-07-21: separate ENTRY vs EXIT switches, group rows per switch, and grey
    # every value-row that is NOT that switch's best (mutually exclusive: only one value
    # of a switch can be live) — numbers stay visible, styling marks the losers.
    import re as _re
    _EXIT_PAT = _re.compile(r"EXIT|STOP|TRAIL|CLOSE|NOLOSS|R1_|R2_|R3_|GIVEBACK|BREAKEVEN|HARVEST|SLOPE_FLIP|PROFIT_LOCK|CROSSUNDER|TF_EXCLUDE|REDUCE", _re.I)
    _SIZE_PAT = _re.compile(r"SIZE|SIZING|CONVICTION|RATIO_MULT|CAPITAL|BUDGET", _re.I)
    _ENTRY_PAT = _re.compile(r"ENTRY|OPEN|REENTRY|FORCE_OPEN|BREAKOUT|SCORE|GATE|ALIGN|CONFIRM|THRESHOLD|K_ZONE|MOMENTUM|ARROW|STOP_PACK", _re.I)

    def group_of(name):
        if _SIZE_PAT.search(name): return "Sizing"
        if _EXIT_PAT.search(name): return "Exit"
        if _ENTRY_PAT.search(name): return "Entry"
        return "Other"
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        grey_font = Font(color="999999")
        grey_fill = PatternFill("solid", fgColor="EEEEEE")
        best_fill = PatternFill("solid", fgColor="E2EFDA")
        red_font = Font(color="9C0006")
        inert_fill = PatternFill("solid", fgColor="FFC7CE")
        wb = openpyxl.Workbook()
        by_group = {"Entry": [], "Exit": [], "Sizing": [], "Other": []}
        for r in matrix:
            by_group[group_of(str(r[0]))].append(r)
        first = True
        for gname in ("Entry", "Exit", "Sizing", "Other"):
            rows_g = by_group[gname]
            ws = wb.active if first else wb.create_sheet()
            first = False
            ws.title = gname
            ws.append(header)
            # group by switch, best (max mean_delta among tested values) first inside group
            from collections import defaultdict as _dd
            sw_rows = _dd(list)
            for r in rows_g: sw_rows[r[0]].append(r)   # r[0] is now the MAIN SWITCH = one block per feature
            def _best_of(rs):
                # only OK rows may win a switch — an INERT/RECONNECT row's "delta" is the
                # absence of an effect, and greening it would promote a disconnected knob
                vals = [x[7] for x in rs if x[7] is not None and x[4] == "OK"]
                return max(vals) if vals else None
            for sw in sorted(sw_rows, key=lambda s: (not str(s).startswith("WT1_5M"), -(_best_of(sw_rows[s]) if _best_of(sw_rows[s]) is not None else -9e9), str(s))):
                rs = sw_rows[sw]
                best = _best_of(rs)
                # Read as a DIAGRAM: the MAIN SWITCH is written ONCE, bold, heading its block;
                # its sub-settings sit underneath with column A blank and their own value ladder
                # in column C. A blank spacer row separates features. Sub-settings are grouped
                # so all values of one setting stay together (20/30/40...), not interleaved.
                first = True
                for r in sorted(rs, key=lambda x: (str(x[1]) != "", str(x[1]),
                                                   -(x[7] if x[7] is not None else -9e9))):
                    r = list(r)
                    if first:
                        first = False
                    else:
                        r[0] = ""          # column A only on the block's first row
                    ws.append(r)
                    row_cells = ws[ws.max_row]
                    if str(r[1]) == "":
                        for c in row_cells[:3]:
                            c.font = Font(bold=True)   # the MAIN SWITCH row heads its block
                    else:
                        row_cells[1].alignment = Alignment(indent=1)   # sub-settings indented
                    if r[4] in ("RECONNECT", "DEGENERATE", "INERT_AT_VALUE"):
                        for c in row_cells[:META_COLS]:
                            c.font = red_font
                            c.fill = inert_fill
                    elif r[4] in (
                        "STALE_ENGINE_CONTRACT", "ENGINE_VALIDATION_FAILED",
                        "EXACT_QUEUE_EMPTY", "NOT_ENGINE_TESTED",
                    ):
                        for c in row_cells[:META_COLS]:
                            c.font = grey_font
                            c.fill = grey_fill
                    states = color_map.get((r[0] if r[0] else sw, r[1], str(r[2])), [])
                    for offset, state in enumerate(states, start=META_COLS):
                        if offset >= len(row_cells) or state is None:
                            continue
                        cell = row_cells[offset]
                        if state == "green":
                            cell.fill = best_fill
                            cell.font = Font(color="006100", bold=True)
                        elif state == "red":
                            cell.fill = inert_fill
                            cell.font = red_font
                        elif state == "gray":
                            cell.fill = grey_fill
                            cell.font = grey_font
                        else:  # white = measured, viable, but below B&H
                            cell.fill = PatternFill("solid", fgColor="FFFFFF")
                            cell.font = Font(color="000000")
                ws.append([])   # spacer between features
            ws.column_dimensions["A"].width = 42
            ws.column_dimensions["B"].width = 42
            ws.column_dimensions["C"].width = 14
            ws.column_dimensions["D"].width = 95
            ws.freeze_panes = "E2"
        # LADDER sheet (USER 2026-07-22): follow the exposure ladder LIVE — start with NO exits
        # (~1 trade = b&h, the floor), then every exit added/adjusted, keeping a row for each
        # config and flagging the ones that IMPROVED on the running best. Then the same for
        # entries. Ordered by time so the progression reads top-to-bottom as it happens.
        try:
            lcon = sqlite3.connect(str(DB))
            lcon.execute("PRAGMA busy_timeout=60000")
            lrows = lcon.execute(
                "SELECT symbol, side, param, value_json, time_in_mkt_pct, acc_gain_pct, gain_per_mo, "
                "pool_sharpe, delta_gain_mo_vs_bh, trades, source_file, ts FROM param_cells "
                "WHERE campaign LIKE '%ladder%' ORDER BY symbol, side, ts").fetchall()
            lbase = {r[0] + "_" + r[1]: r[2] for r in lcon.execute(
                "SELECT symbol, side, bh_pct FROM key_baseline WHERE mode='tradier'")}
            lcon.close()
        except sqlite3.OperationalError as _lexc:
            print(f"[Ladder] query failed: {_lexc}")  # never fail silently into an empty sheet
            lrows, lbase = [], {}
        wsl = wb.create_sheet("Ladder")
        wsl.append(["key", "stage", "switch", "value", "time_in_mkt_%", "acc_gain_%", "b&h_%",
                    "vs_b&h", "gain/mo", "pool_sharpe", "d_gain_mo_vs_bh", "trades", "IMPROVED?", "when"])
        best = {}
        for sym, side, param, val, tim, gain, gmo, ps, dd, tr, sf, ts in lrows:
            key = f"{sym}_{side}"
            stage = (str(sf).split("/")[1] if sf and "/" in str(sf) else "")
            bh = lbase.get(key)
            vs = round((gain or 0) - bh, 2) if bh is not None else None
            prev = best.get(key)
            improved = prev is None or (gain or 0) > prev
            if improved:
                best[key] = gain or 0
            r = [key, stage, param, val, round(tim or 0, 2), round(gain or 0, 2),
                 round(bh, 2) if bh is not None else None, vs,
                 round(gmo or 0, 4), round(ps or 0, 4), round(dd or 0, 2), tr or 0,
                 "IMPROVED" if improved else "", str(ts)[:19]]
            wsl.append(r)
            if improved:
                for c in wsl[wsl.max_row]:
                    c.fill = best_fill
            if bh is not None and (gain or 0) > bh:
                wsl.cell(row=wsl.max_row, column=8).font = Font(bold=True, color="006100")
        wsl.freeze_panes = "C2"
        # BandLadder sheet (USER 2026-07-22): per-ticker best ladder multipliers, editable.
        # These are the knobs you vary per ticker — D/4h/1h bottom & top, mode — with the best
        # found by band_ladder_sweep and its multiple of b&h alongside.
        try:
            blcon = sqlite3.connect(str(DB)); blcon.execute("PRAGMA busy_timeout=60000")
            blrows = blcon.execute(
                "SELECT symbol, side, value_json, acc_gain_pct, delta_gain_mo_vs_bh, trades, "
                "pool_sharpe, source_file, ts FROM param_cells WHERE campaign LIKE '%bandladder' "
                "ORDER BY symbol, side, acc_gain_pct DESC").fetchall()
            blbh = {r[0]+"_"+r[1]: (r[3]-(r[4] or 0)) for r in blrows}  # gain - delta = b&h
            blcon.close()
        except sqlite3.OperationalError:
            blrows = []
        wsb = wb.create_sheet("BandLadder")
        wsb.append(["key", "rank", "ladder (mode|D:b/t|4h:b/t|1h:b/t)", "gain %", "b&h %",
                    "xB&H", "trades", "sharpe", "beats_bh", "when"])
        seen = {}
        for sym, side, cfg, gain, dbh, tr, ps, sf, ts in blrows:
            k = f"{sym}_{side}"; seen[k] = seen.get(k, 0) + 1
            bh = (gain or 0) - (dbh or 0)
            x = (gain or 0) / bh if bh else 0
            wsb.append([k, seen[k], cfg, round(gain or 0, 1), round(bh, 1), round(x, 2),
                        tr or 0, round(ps or 0, 3), "YES" if (gain or 0) >= bh else "", str(ts)[:19]])
            if seen[k] == 1 and (gain or 0) >= bh:
                for c in wsb[wsb.max_row]:
                    c.fill = best_fill
        wsb.freeze_panes = "B2"
        wsb.column_dimensions["C"].width = 40
        # Preserve pre-repair dc_low4 measurements as gray, quarantined evidence instead of
        # silently resurrecting them into the repaired-contract matrix or deleting them. These
        # rows explain why the switch is diagnostic-only and prevent repeated blind tuning.
        wsd = wb.create_sheet("DC4 Diagnostics")
        wsd.append([
            "campaign", "key", "value", "gain/mo", "time_in_market_%",
            "trades", "delta_gain/mo_vs_B&H", "validation", "classification", "source",
        ])
        try:
            dcon = sqlite3.connect(str(DB))
            dcon.execute("PRAGMA busy_timeout=60000")
            drows = dcon.execute(
                "SELECT campaign,symbol,side,value_json,gain_per_mo,time_in_mkt_pct,trades,"
                "delta_gain_mo_vs_bh,validation_status,source_file "
                "FROM param_cells WHERE mode='tradier' "
                "AND campaign='stocks_baseline_v2_s4h' "
                "AND param='DC_LOW4_STOP_ENABLED' "
                "ORDER BY symbol,side,value_json"
            ).fetchall()
            dcon.close()
        except sqlite3.OperationalError as _dexc:
            print(f"[DC4 Diagnostics] query failed: {_dexc}")
            drows = []
        for campaign, sym, side, val, gain, tim, trades, delta, validation, source in drows:
            wsd.append([
                campaign,
                f"{sym}_{side}",
                val,
                gain,
                tim,
                trades,
                delta,
                validation or "NULL / PRE-REPAIR",
                "QUARANTINED ENTRY-QUALITY / LOSING-CHURN EVIDENCE; DO NOT PROMOTE",
                source,
            ])
            for cell in wsd[wsd.max_row]:
                cell.fill = grey_fill
                cell.font = grey_font
        wsd.freeze_panes = "B2"
        wsd.auto_filter.ref = wsd.dimensions
        for col, width in {
            "A": 28, "B": 18, "C": 12, "D": 14, "E": 20, "F": 10,
            "G": 23, "H": 22, "I": 72, "J": 85,
        }.items():
            wsd.column_dimensions[col].width = width
        for row in wsd.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws2 = wb.create_sheet("Coverage")
        ws2.append([
            "switch", "value", "status", "keys_current_engine", "keys_inert",
            "keys_helped", "keys_hurt", "keys_stale_contract",
            "keys_engine_validation_failed", "keys_vec_only_exact_queue_empty",
        ])
        for r in coverage: ws2.append(r)
        if engine_coverage:
            wse = wb.create_sheet("Engine Coverage", 0)
            wse.append(["metric", "count", "meaning"])
            wse.append([
                "raw repaired-campaign ENGINE rows",
                engine_coverage["raw_engine_rows"],
                "Preserved exact-engine rows since the repaired cutoff, before current-contract filtering.",
            ])
            wse.append([
                "current contract ENGINE rows",
                engine_coverage["current_engine_rows"],
                "Rows whose code+NPZ+side fingerprint matches this export. Only these may fill numeric matrix cells.",
            ])
            wse.append([
                "stale contract ENGINE rows",
                engine_coverage["stale_engine_rows"],
                "Exact results from an older contract. Preserved, explicitly labelled, never copied into current cells.",
            ])
            wse.append([
                "ENGINE validation failures",
                engine_coverage["invalid_engine_rows"],
                "Exact runs that failed the repaired validation contract.",
            ])
            wse.append([
                "path-fleet V8 exact replay rows",
                len(engine_coverage["fleet_exact"]),
                "Separate exact replays. They do not fill an OFAT switch cell unless the payload explicitly identifies one attributable knob/value.",
            ])
            wse.append([
                "path-fleet exact rows attributable to switch/value",
                engine_coverage["fleet_exact_attributable"],
                "Explicit attribution only; return-% is not silently converted to gain/month.",
            ])
            wse.append([])
            wse.append([
                "status", "definition",
                "Numeric ENGINE cell remains blank unless a current contract-matched exact result exists.",
            ])
            wse.append([
                "STALE_ENGINE_CONTRACT",
                "An exact row exists for this switch/value/key, but its fingerprint is obsolete.",
            ])
            wse.append([
                "ENGINE_VALIDATION_FAILED",
                "An exact row exists, but validation failed.",
            ])
            wse.append([
                "EXACT_QUEUE_EMPTY",
                "A VEC diagnostic exists, but there is no attributable current exact-engine result.",
            ])
            wse.append([
                "NOT_ENGINE_TESTED",
                "No exact ENGINE evidence exists for this switch/value.",
            ])
            wse.freeze_panes = "A2"
            wse.column_dimensions["A"].width = 48
            wse.column_dimensions["B"].width = 18
            wse.column_dimensions["C"].width = 110
            for row in wse.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)

            wsx = wb.create_sheet("Exact Engine Evidence")
            wsx.append([
                "path_id", "key", "stage", "status", "strategy_return_%",
                "B&H_return_%", "same_entry_control_return_%",
                "matrix_param", "matrix_value", "attributable_to_switch_value",
                "artifact", "created_at",
            ])
            for exact in engine_coverage["fleet_exact"]:
                wsx.append([
                    exact["path_id"], exact["key"], exact["stage"], exact["status"],
                    exact["strategy_return_pct"], exact["bh_return_pct"],
                    exact["same_entry_control_return_pct"], exact["matrix_param"],
                    exact["matrix_value"], "YES" if exact["attributable"] else "NO",
                    exact["artifact"], exact["created_at"],
                ])
                if not exact["attributable"]:
                    for cell in wsx[wsx.max_row]:
                        cell.fill = grey_fill
                        cell.font = grey_font
            wsx.freeze_panes = "A2"
            wsx.auto_filter.ref = wsx.dimensions
            for col, width in {
                "A": 28, "B": 18, "C": 24, "D": 18, "E": 18, "F": 18,
                "G": 28, "H": 36, "I": 18, "J": 28, "K": 90, "L": 20,
            }.items():
                wsx.column_dimensions[col].width = width
        ws3 = wb.create_sheet("Baselines")
        ws3.append(["key", "gain_per_mo", "bh_per_mo", "delta_vs_bh", "trades", "campaign"])
        for k in cols:
            b = base.get(k, {})
            ws3.append([k, b.get("gain_per_mo"), b.get("bh_per_mo"), b.get("delta_vs_bh"), b.get("trades"), b.get("campaign")])
        wsi = wb.create_sheet("Inventory")
        wsi.append(["setting", "tier", "exclusion_reason", "consumed_by", "description"])
        for name, tier, reason, consumed in inventory:
            wsi.append([name, tier, reason, consumed, describe_knob(name)])
        wsi.freeze_panes = "A2"
        wsi.column_dimensions["A"].width = 48
        wsi.column_dimensions["C"].width = 58
        wsi.column_dimensions["E"].width = 95
        wb.save(OUT_XLSX)
        xls_note = str(OUT_XLSX)
    except Exception as e:
        xls_note = f"(xlsx skipped: {e})"
    tested = sum(1 for r in matrix if r[5])
    from collections import Counter as _Counter
    by_status = _Counter(r[4] for r in matrix)
    mu = sum(1 for r in matrix if r[header.index("MU_LONG")] is not None) if "MU_LONG" in cols else 0
    print(f"tier={a.tier}  rows={len(matrix)} (switch-values)  key_columns={len(cols)}  rows_with_data={tested}")
    print(f"status: " + "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print(f"MU_LONG filled: {mu}/{len(matrix)}")
    print(f"csv={OUT_CSV}")
    print(f"xlsx={xls_note}")


if __name__ == "__main__":
    main()
