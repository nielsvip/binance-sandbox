#!/usr/bin/env python3
"""
derive_param_ranges.py — Build a data-driven sweep search-space from all historical backtests.

Reads the consolidated results DB (results_db.sqlite: v8_sweep_runs = real-engine,
v8_quick_runs = vec) and, for EVERY config parameter that has ever been swept, derives
the value range worth re-testing for a fresh baseline:

  * bool         -> test {True, False}, ranked by mean pool_sharpe
  * numeric      -> a curated set of values bracketing the historically best band
  * categorical  -> distinct values ranked by mean pool_sharpe

It then cross-references the full config_tradier.TradierConfig (or config.Config) dataclass
so untested and dead (engine-ignored) parameters are surfaced too. Output is a spec JSON the
baseline sweep + per_sym agent can consume, plus a human-readable report.

NO-LIES: the `sharpe` column is the engine-emitted pool_sharpe stored in results_db. This
script ranks/derives a SEARCH SPACE only — it makes no promotion claim. Sample floors are
applied (min trades, min n_syms) and support counts are reported for every value so a thin
sample cannot masquerade as a winner. Dead knobs (metrics_guard.is_knob_consumed_by_engine)
are flagged and excluded from the active search space.

Usage:
  python3 derive_param_ranges.py --mode tradier
  python3 derive_param_ranges.py --mode tradier --db archive_from_s2/results_db.sqlite \
      --min-trades 30 --min-nsyms 100 --top-pct 0.25 --max-values 9 \
      --out data/param_baseline_spec_tradier.json

Then (separately, on S1) the spec drives:
  1. a fresh 4yr baseline sweep over the active search space, and
  2. per_sym optimisation seeded from the new baseline.
"""
import argparse
import dataclasses
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import metrics_guard
except Exception:
    metrics_guard = None

DEFAULT_DBS = [
    Path("data/results_db.sqlite"),
    Path("archive_from_s2/results_db.sqlite"),
    Path("/home/niels/binance-sandbox/data/results_db.sqlite"),
]
BOOL_TRUE = {"true", "1", "yes", "on"}
BOOL_FALSE = {"false", "0", "no", "off"}


def _find_db(explicit):
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        sys.exit(f"DB not found: {p}")
    for p in DEFAULT_DBS:
        if p.exists():
            return p
    sys.exit(f"No results DB found in any of: {[str(x) for x in DEFAULT_DBS]}")


def _coerce(value):
    s = str(value).strip()
    low = s.lower()
    if low in BOOL_TRUE:
        return ("bool", True)
    if low in BOOL_FALSE:
        return ("bool", False)
    try:
        if any(c in s for c in (".", "e", "E")) or s in ("inf", "-inf"):
            return ("num", float(s))
        return ("num", int(s))
    except ValueError:
        return ("cat", s)


def _pull(conn, table, mode, has_nsyms):
    cols = "sharpe, trades, cfg_json" + (", n_syms" if has_nsyms else "")
    q = f"SELECT {cols} FROM {table} WHERE mode=? AND cfg_json IS NOT NULL AND cfg_json NOT IN ('','{{}}')"
    out = []
    for row in conn.execute(q, (mode,)):
        sharpe, trades, cfg_json = row[0], row[1], row[2]
        n_syms = row[3] if has_nsyms else None
        if sharpe is None:
            continue
        try:
            sharpe = float(sharpe)
        except (TypeError, ValueError):
            continue
        if sharpe != sharpe or abs(sharpe) > 1e6:
            continue
        try:
            cfg = json.loads(cfg_json)
        except (TypeError, ValueError):
            continue
        out.append((sharpe, trades, n_syms, cfg))
    return out


def _collect(rows, min_trades, min_nsyms, real_weight):
    obs = {}
    kept = 0
    for sharpe, trades, n_syms, cfg, is_real in rows:
        try:
            t = int(trades or 0)
        except (TypeError, ValueError):
            t = 0
        if t and t < min_trades:
            continue
        if n_syms is not None and n_syms and n_syms < min_nsyms:
            continue
        kept += 1
        w = real_weight if is_real else 1.0
        for k, v in cfg.items():
            param = k[4:] if k.startswith("cfg_") else k
            kind, val = _coerce(v)
            obs.setdefault(param, []).append((kind, val, sharpe, w))
    return obs, kept


def _weighted_mean(pairs):
    num = sum(s * w for s, w in pairs)
    den = sum(w for _, w in pairs)
    return num / den if den else 0.0


def _numeric_range(value_stats, max_values):
    vals = sorted(value_stats.keys())
    best = max(value_stats, key=lambda v: value_stats[v]["mean_sharpe"])
    if len(vals) == 1:
        v = vals[0]
        if v == 0:
            grid = [-1.0, 0.0, 1.0]
        else:
            grid = sorted({round(v * m, 6) for m in (0.5, 0.75, 1.0, 1.25, 1.5)})
        return grid, best, "single_value_expanded"
    good = [v for v in vals if value_stats[v]["mean_sharpe"] >= value_stats[best]["mean_sharpe"] * 0.8]
    if not good:
        good = [best]
    lo, hi = min(good), max(good)
    span = [v for v in vals if lo <= v <= hi]
    extra = [v for v in vals if v < lo or v > hi]
    extra.sort(key=lambda v: abs(v - best))
    pick = sorted(set(span + extra[: max(0, max_values - len(span))] + [best]))
    if len(pick) > max_values:
        idx = [round(i * (len(pick) - 1) / (max_values - 1)) for i in range(max_values)]
        pick = sorted({pick[i] for i in idx} | {best})
    return pick, best, "data_band"


def derive(conn, mode, args):
    has = {t: True for t in ("v8_quick_runs",)}
    real_rows = [(s, tr, ns, c, True) for (s, tr, ns, c) in _pull(conn, "v8_sweep_runs", mode, False)]
    vec_rows = [(s, tr, ns, c, False) for (s, tr, ns, c) in _pull(conn, "v8_quick_runs", mode, True)]
    obs, kept = _collect(real_rows + vec_rows, args.min_trades, args.min_nsyms, args.real_weight)
    spec = {}
    for param, entries in obs.items():
        kinds = {k for k, _, _, _ in entries}
        dominant = "bool" if "bool" in kinds and len(kinds) == 1 else (
            "num" if kinds <= {"num"} else ("bool" if "bool" in kinds else "cat"))
        by_val = {}
        for kind, val, sharpe, w in entries:
            if dominant == "num" and kind != "num":
                continue
            by_val.setdefault(val, []).append((sharpe, w))
        value_stats = {}
        for val, pairs in by_val.items():
            value_stats[val] = {
                "mean_sharpe": round(_weighted_mean(pairs), 4),
                "max_sharpe": round(max(s for s, _ in pairs), 4),
                "support_obs": len(pairs),
            }
        if not value_stats:
            continue
        best_val = max(value_stats, key=lambda v: value_stats[v]["mean_sharpe"])
        dead = (metrics_guard is not None
                and not metrics_guard.is_knob_consumed_by_engine(param, "tradier" if mode == "tradier" else "crypto"))
        if dominant == "bool":
            test_values = [True, False]
            method = "bool_toggle"
        elif dominant == "num":
            test_values, best_val, method = _numeric_range(value_stats, args.max_values)
        else:
            test_values = sorted(value_stats, key=lambda v: value_stats[v]["mean_sharpe"], reverse=True)
            method = "categorical_ranked"
        spec[param] = {
            "type": dominant,
            "method": method,
            "test_values": test_values,
            "best_value_seen": best_val,
            "best_mean_pool_sharpe": value_stats[best_val]["mean_sharpe"],
            "best_max_pool_sharpe": value_stats[best_val]["max_sharpe"],
            "support_obs_total": sum(v["support_obs"] for v in value_stats.values()),
            "distinct_values_seen": sorted(value_stats.keys()) if dominant != "bool" else list(value_stats.keys()),
            "per_value": {str(k): value_stats[k] for k in sorted(value_stats, key=str)},
            "dead_in_engine": bool(dead),
            "swept_in_history": True,
        }
    return spec, kept, len(real_rows), len(vec_rows)


STRATEGY_KW = ("ENABLED", "STOP", "EXIT", "ENTRY", "GATE", "HOLD", "THRESHOLD",
               "_MIN", "_MAX", "_PCT", "_TF", "HEDGE", "_DC", "DC_", "_WT", "WT_",
               "_BB", "BB_", "NOLOSS", "REENTRY", "SCORE", "GIVEBACK", "BRAKE",
               "ALIGN", "VETO", "RATIO", "SIZING", "AUGMENT", "PARTIAL", "TRAIL",
               "BYPASS", "CONFIRM", "REQUIRE", "FLOOR", "BAND", "DECEL", "FROZEN",
               "PEAK", "VELOCITY", "MOMENTUM", "BREAKOUT", "FORCE_OPEN", "K3M", "K5M")
INFRA_KW = ("REFRESH", "INTERVAL", "SLEEP", "TIMEOUT", "_PATH", "_DIR", "URL",
            "EMAIL", "_LOG", "SECONDS", "_PORT", "HOST", "WEBHOOK", "TOKEN",
            "API_", "_KEY", "POLL", "CACHE", "RETRY_DELAY", "HEARTBEAT", "DEBUG",
            "VERBOSE", "DUMP", "SHADOW_LOG", "_MS")


def _is_strategy_knob(name):
    up = name.upper()
    if any(k in up for k in INFRA_KW):
        return False
    return any(k in up for k in STRATEGY_KW)


def _propose_untested(name, meta, max_values):
    d = meta["default"]
    if isinstance(d, bool):
        return {"type": "bool", "method": "untested_toggle", "test_values": [True, False],
                "current_default": d}
    if isinstance(d, (int, float)) and not isinstance(d, bool):
        v = float(d)
        if v == 0:
            grid = [0, 1, 2] if "MIN" in name.upper() or "TFS" in name.upper() else [0.0, 0.5, 1.0]
        else:
            grid = sorted({round(v * m, 6) for m in (0.5, 0.75, 1.0, 1.25, 1.5)})
        grid = [int(x) if isinstance(d, int) and float(x).is_integer() else x for x in grid]
        return {"type": "num", "method": "untested_default_band", "test_values": grid[:max_values],
                "current_default": d}
    return None


def _config_fields(mode):
    try:
        if mode == "tradier":
            from config_tradier import TradierConfig as C
        else:
            from config import Config as C
    except Exception as e:
        return {}, f"config introspection failed: {e}"
    fields = {}
    if dataclasses.is_dataclass(C):
        for f in dataclasses.fields(C):
            fields[f.name] = {"type": getattr(f.type, "__name__", str(f.type)),
                              "default": f.default if f.default is not dataclasses.MISSING else None}
    else:
        for k, v in vars(C).items():
            if k.isupper():
                fields[k] = {"type": type(v).__name__, "default": v}
    return fields, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["tradier", "crypto"], default="tradier")
    ap.add_argument("--db", default="")
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--min-nsyms", type=int, default=100)
    ap.add_argument("--top-pct", type=float, default=0.25)
    ap.add_argument("--max-values", type=int, default=9)
    ap.add_argument("--real-weight", type=float, default=3.0)
    ap.add_argument("--sharpe-plausible-cap", type=float, default=5.0,
                    help="best_mean_pool_sharpe above this is flagged ranking_suspect (legacy/unaudited)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.mode == "crypto" and args.min_nsyms == 100:
        args.min_nsyms = 48
    db = _find_db(args.db)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    spec, kept, n_real, n_vec = derive(conn, args.mode, args)
    fields, ferr = _config_fields(args.mode)
    for name, v in spec.items():
        v["ranking_suspect"] = bool(v.get("best_mean_pool_sharpe", 0) > args.sharpe_plausible_cap)
    swept = set(spec.keys())
    untested = []
    proposed = {}
    for name, meta in fields.items():
        if name in swept:
            continue
        dead = (metrics_guard is not None
                and not metrics_guard.is_knob_consumed_by_engine(name, args.mode))
        untested.append({"param": name, "type": meta["type"], "current_default": _jsonable(meta["default"]),
                         "dead_in_engine": bool(dead)})
        if not dead and _is_strategy_knob(name):
            p = _propose_untested(name, meta, args.max_values)
            if p:
                proposed[name] = p
    active = {k: v for k, v in spec.items() if not v["dead_in_engine"]}
    dead_swept = {k: v for k, v in spec.items() if v["dead_in_engine"]}
    out = {
        "mode": args.mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(db),
        "runs_real_engine": n_real,
        "runs_vec": n_vec,
        "runs_kept_after_floor": kept,
        "floors": {"min_trades": args.min_trades, "min_nsyms": args.min_nsyms},
        "note": "sharpe = engine-emitted pool_sharpe from results_db; search-space derivation only, not a promotion claim. ranking_suspect=True means best_mean_pool_sharpe exceeds the plausibility cap (likely unaudited/legacy value) — trust the RANGE, not the rank.",
        "sharpe_plausible_cap": args.sharpe_plausible_cap,
        "active_search_space": active,
        "proposed_untested_strategy_space": proposed,
        "dead_knobs_swept_in_history": sorted(dead_swept.keys()),
        "config_params_never_swept": sorted(untested, key=lambda x: x["param"]),
        "config_introspection_error": ferr,
    }
    out_path = Path(args.out) if args.out else Path(f"data/param_baseline_spec_{args.mode}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=_jsonable))
    _report(out, out_path)


def _jsonable(x):
    if isinstance(x, bool) or x is None:
        return x
    try:
        if x == int(x):
            return int(x)
        return float(x)
    except (TypeError, ValueError):
        return str(x)


def _report(out, out_path):
    a = out["active_search_space"]
    print(f"\n=== PARAM BASELINE SPEC · mode={out['mode']} ===")
    print(f"source: {out['source_db']}  real-engine runs={out['runs_real_engine']}  vec runs={out['runs_vec']}  kept(floor)={out['runs_kept_after_floor']}")
    prop = out["proposed_untested_strategy_space"]
    print(f"active sweepable(history)={len(a)}  proposed untested-strategy={len(prop)}  never-swept(all)={len(out['config_params_never_swept'])}")
    print(f"\n--- A. DATA-DERIVED ranges (swept in history; ⚠=ranking_suspect/legacy sharpe) ---")
    print(f"{'PARAM':40s} {'TYPE':5s} {'BEST':>10s} {'pool_sh':>8s} {'obs':>6s}  TEST_VALUES")
    for name in sorted(a, key=lambda k: -a[k]["best_mean_pool_sharpe"]):
        d = a[name]
        tv = d["test_values"]
        tvs = ",".join(str(x) for x in tv) if len(tv) <= 9 else f"{len(tv)} values [{tv[0]}..{tv[-1]}]"
        flag = "⚠" if d.get("ranking_suspect") else " "
        print(f"{flag}{name[:39]:39s} {d['type']:5s} {str(d['best_value_seen'])[:10]:>10s} {d['best_mean_pool_sharpe']:8.3f} {d['support_obs_total']:6d}  {tvs}")
    print(f"\n--- B. PROPOSED ranges for NEVER-SWEPT strategy/exit knobs (default-centered; NO history) ---")
    print(f"{'PARAM':40s} {'TYPE':5s} {'DEFAULT':>10s}  TEST_VALUES")
    for name in sorted(prop):
        d = prop[name]
        print(f"{name[:40]:40s} {d['type']:5s} {str(d['current_default'])[:10]:>10s}  {','.join(str(x) for x in d['test_values'])}")
    print(f"\nspec written -> {out_path}")
    if out["dead_knobs_swept_in_history"]:
        print(f"\n⚠️  {len(out['dead_knobs_swept_in_history'])} knobs were swept historically but are DEAD in engine (results irrelevant): {', '.join(out['dead_knobs_swept_in_history'][:12])}{' ...' if len(out['dead_knobs_swept_in_history'])>12 else ''}")


if __name__ == "__main__":
    main()
