#!/usr/bin/env python3
"""build_sweep_manifest.py — classify EVERY config param by which engine consumes
it, attach a test range + provenance, and emit (a) a sweep manifest the screening
runner consumes and (b) a human-readable annotation for each param (reason for the
current value, with date) ready to paste behind the field in config_tradier.py.

Classification is by DIRECT SOURCE-TOKEN PRESENCE (ground truth) across:
  vec   : v8_vec_sweep.py                       (Tier-1 fast screen)
  tier2 : backtest_v8_engine.py, v8_quick_engine.py (Tier-2 real-code test)
  live  : tradier_manage.py, tradier_indicators.py, config_tradier.py consumers

Sweep tier per param:
  VEC_SCREEN    : read by vec  -> fast OFAT screen across all symbols (cheap)
  ENGINE_SCREEN : read by tier2 but NOT vec -> real-engine OFAT on ~40 syms
  LIVE_ONLY     : read by live but no backtest path -> forward-test, cannot sweep
  DEAD          : referenced nowhere outside its own definition -> do not sweep

Ranges come from data/param_baseline_spec_tradier.json (data-derived for swept
params, default-band for never-swept strategy knobs); else derived here.

NO-LIES: this assigns a SEARCH SPACE + consumption fact only. No promotion claim.
"""
import argparse
import dataclasses
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sweep_value_semantics import executable_values, validate_test_values
from tradier_sweep_grid_contract import grid_contract

BASE = Path(__file__).resolve().parent
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

VEC_SRC = ["v8_vec_sweep.py"]
TIER2_SRC = ["backtest_v8_engine.py", "v8_quick_engine.py", "classic_formations.py"]
# 2026-07-20: tradier list was missing the SHARED decision modules its stack imports
# (utils, wt_composite via tradier_indicators, wt_dc_* scorers + local_extremes via
# tradier_manage) -> params consumed there were falsely classified DEAD for stocks.
LIVE_SRC_TRADIER = ["tradier_manage.py", "tradier_indicators.py", "tradier_positions.py",
                    "tradier_prices.py", "tradier_rankings.py", "tradier_api.py",
                    "tradier_hourly_reconfig.py", "utils.py", "wt_composite.py",
                    "wt_dc_delta.py", "wt_dc_entry_scorer.py", "wt_dc_exit_scorer.py",
                    "local_extremes_scorer.py", "classic_formations.py"]
LIVE_SRC_CRYPTO = ["ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
                   "ez_indicators.py", "ez_prices.py", "ez_rankings.py", "ez_reentry.py",
                   "ez_klines.py", "ez_market_data.py", "wt_composite.py", "utils.py",
                   "wt_dc_delta.py", "classic_formations.py"]
LIVE_SRC = LIVE_SRC_TRADIER

RUNTIME_FENCE_PAT = re.compile(
    r"(?:^|_)(?:MIN_OPEN_TS|START_TS|END_TS)(?:_|$)"
)


def _load_tokens(files):
    """Return set of identifier tokens that appear in the given source files
    (excluding their own config_tradier.py definition line)."""
    tok = set()
    for fn in files:
        p = BASE / fn
        if not p.exists():
            continue
        txt = p.read_text(errors="ignore")
        tok |= set(re.findall(r"[A-Z][A-Z0-9_]{3,}", txt))
    return tok


def _config_fields(mode="tradier"):
    if mode == "crypto":
        from config import Config as C
    else:
        from config_tradier import TradierConfig as C
    out = {}
    if dataclasses.is_dataclass(C):
        for f in dataclasses.fields(C):
            d = f.default if f.default is not dataclasses.MISSING else None
            out[f.name] = {"type": getattr(f.type, "__name__", str(f.type)), "default": d}
    return out


def _jsonable(x):
    if isinstance(x, bool) or x is None:
        return x
    # Preserve the declared numeric type.  Collapsing ``1.0`` to JSON ``1``
    # made the Tier-2 expander treat float grids as integer grids and reject
    # their fractional controls.
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return float(x)
    return str(x)


def _derive_range(name, default):
    d = default
    if isinstance(d, bool):
        return [True, False], "bool_toggle"
    if isinstance(d, (int, float)):
        v = float(d)
        if v == 0:
            grid = [0, 1, 2] if ("MIN" in name.upper() or "TFS" in name.upper()) else [0.0, 0.5, 1.0]
        else:
            grid = sorted({round(v * m, 6) for m in (0.5, 0.75, 1.0, 1.25, 1.5)})
        grid = [int(x) if isinstance(d, int) and float(x).is_integer() else x for x in grid]
        return grid, "default_band"
    return None, "non_numeric_no_range"


def _same_typed_value(a, b):
    """Strict equality for controls (``True`` is never numeric ``1``)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return type(a) is type(b) and a == b


def _include_default_control(default, values):
    """Every executable alpha grid must contain the active typed control."""
    rows = list(values or [])
    if not any(_same_typed_value(default, value) for value in rows):
        rows.append(default)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["tradier", "crypto"], default="tradier")
    ap.add_argument("--spec", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--annot", default=None)
    args = ap.parse_args()
    mode = args.mode
    args.spec = args.spec or f"data/param_baseline_spec_{mode}.json"
    args.out = args.out or f"data/param_sweep_manifest_{mode}.json"
    args.annot = args.annot or f"data/param_annotations_{mode}.md"
    global LIVE_SRC
    LIVE_SRC = LIVE_SRC_CRYPTO if mode == "crypto" else LIVE_SRC_TRADIER

    fields = _config_fields(mode)
    spec = {}
    sp = BASE / args.spec
    if sp.exists():
        s = json.loads(sp.read_text())
        spec = {"active": s.get("active_search_space", {}),
                "proposed": s.get("proposed_untested_strategy_space", {})}

    vec_tok = _load_tokens(VEC_SRC)
    tier2_tok = _load_tokens(TIER2_SRC)
    live_tok = _load_tokens(LIVE_SRC)

    manifest = {}
    counts = {"VEC_SCREEN": 0, "ENGINE_SCREEN": 0, "LIVE_ONLY": 0, "DEAD": 0}
    annot_lines = []
    for name in sorted(fields):
        default = fields[name]["default"]
        in_vec, in_t2, in_live = name in vec_tok, name in tier2_tok, name in live_tok
        runtime_control = bool(RUNTIME_FENCE_PAT.search(name))
        if runtime_control:
            tier = "RUNTIME_CONTROL"
        elif in_vec:
            tier = "VEC_SCREEN"
        elif in_t2:
            tier = "ENGINE_SCREEN"
        elif in_live:
            tier = "LIVE_ONLY"
        else:
            tier = "DEAD"
        counts.setdefault(tier, 0)
        counts[tier] += 1
        # range + provenance
        rng, method, swept, best_val, best_sh, obs = None, None, False, None, None, 0
        if name in spec["active"]:
            a = spec["active"][name]
            rng, method, swept = a["test_values"], a["method"], True
            best_val, best_sh, obs = a.get("best_value_seen"), a.get("best_mean_pool_sharpe"), a.get("support_obs_total", 0)
        elif name in spec["proposed"]:
            pr = spec["proposed"][name]
            rng, method = pr["test_values"], pr["method"]
        else:
            rng, method = _derive_range(name, default)
        contract = grid_contract(name) if mode == "tradier" else None
        if contract:
            rng = contract["values"]
            method = "live_consumer_contract"
            # These grids are explicitly documented against the real Tradier
            # evaluator used by the exact engine.  Source-token scanning cannot
            # see every dynamically imported live consumer, so the contract is
            # the stronger evidence and keeps the grid executable.
            in_t2 = True
            if not in_vec:
                if tier != "ENGINE_SCREEN":
                    counts[tier] -= 1
                    counts["ENGINE_SCREEN"] += 1
                tier = "ENGINE_SCREEN"
        if runtime_control:
            rng = None
            method = "runtime_fence_excluded_from_alpha"
        else:
            rng = _include_default_control(default, rng)
        range_validation = validate_test_values(name, default, rng)
        rng = executable_values(range_validation)
        sweepable = (
            not runtime_control
            and tier in ("VEC_SCREEN", "ENGINE_SCREEN")
            and rng is not None
        )
        manifest[name] = {
            "default": _jsonable(default), "type": fields[name]["type"],
            "consumed_by": {"vec": in_vec, "tier2": in_t2, "live": in_live},
            "sweep_tier": tier, "sweepable": sweepable,
            "test_values": [_jsonable(x) for x in rng] if rng else None, "range_method": method,
            "range_validation": range_validation,
            "grid_contract_evidence": contract["evidence"] if contract else None,
            "runtime_control_not_alpha": runtime_control,
            "swept_in_history": swept, "best_value_seen": _jsonable(best_val) if best_val is not None else None,
            "best_mean_pool_sharpe": best_sh, "support_obs": obs,
        }
        # annotation remark
        if swept:
            prov = f"swept(hist): best={best_val}@pool_sharpe {best_sh} over {obs} obs"
        elif runtime_control:
            prov = "RUNTIME CONTROL; excluded from alpha sweeps"
        elif tier == "DEAD":
            prov = "NEVER swept; referenced in NO engine/live source -> dead knob"
        elif tier == "LIVE_ONLY":
            prov = "NEVER swept; live-only (no backtest path) -> forward-test, un-sweepable"
        else:
            prov = "NEVER swept; range=default-band, pending screen"
        rng_s = ("[" + ",".join(str(x) for x in rng) + "]") if rng else "n/a"
        annot_lines.append(f"`{name}` = `{_jsonable(default)}`  "
                           f"# [{TODAY}] {tier} · {prov} · range={rng_s}")

    out = {
        "mode": mode, "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_params": len(fields), "tier_counts": counts,
        "screen_note": "VEC_SCREEN -> fast Tier-1 OFAT all syms; ENGINE_SCREEN -> Tier-2 OFAT 40 syms; "
                       "LIVE_ONLY/DEAD -> not swept (annotated); RUNTIME_CONTROL -> operational fence, "
                       "never alpha-swept. Classification by source-token presence.",
        "params": manifest,
    }
    op = BASE / args.out
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(out, indent=2, default=_jsonable))

    ap_path = BASE / args.annot
    sweepable_n = sum(1 for v in manifest.values() if v["sweepable"])
    cfg_file = "config" if mode == "crypto" else "config_tradier"
    header = (f"# {cfg_file} param annotations — {TODAY}\n\n"
              f"{len(fields)} params · VEC_SCREEN={counts['VEC_SCREEN']} "
              f"ENGINE_SCREEN={counts['ENGINE_SCREEN']} LIVE_ONLY={counts['LIVE_ONLY']} "
              f"DEAD={counts['DEAD']} · sweepable={sweepable_n}\n\n"
              "Remark format (paste behind each field in config_tradier.py once unlocked):\n"
              "`NAME` = `default`  # [date] TIER · provenance · range=[...]\n\n")
    ap_path.write_text(header + "\n".join(annot_lines) + "\n")

    print(f"manifest -> {op}  ({len(fields)} params)")
    print(f"  VEC_SCREEN={counts['VEC_SCREEN']}  ENGINE_SCREEN={counts['ENGINE_SCREEN']}  "
          f"LIVE_ONLY={counts['LIVE_ONLY']}  DEAD={counts['DEAD']}  sweepable={sweepable_n}")
    print(f"annotations -> {ap_path}")


if __name__ == "__main__":
    main()
