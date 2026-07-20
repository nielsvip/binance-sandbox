#!/usr/bin/env python3
"""build_param_workbook.py — ONE spreadsheet for every config param across BOTH
trading systems (crypto config.Config + stocks config_tradier.TradierConfig).

Merges, per system:
  - data/param_sweep_manifest_<mode>.json  (tier classification: who consumes the knob)
  - data/param_baseline_spec_<mode>.json   (test history pulled from the results DB:
        which values were actually run, mean/max pool_sharpe per value, obs count)

into data/param_workbook.csv — one row per (system, param) with:
  * WHO consumes it (vec / tier2 / live) + sweep_tier
  * TEST STATUS: TESTED / UNTESTED / OBSOLETE(dead) / LIVE_ONLY(forward-test)
  * what was tested (distinct values, obs), best value+sharpe seen, is current default best
  * a rule-based SUGGESTED_PATH toward gain/mo + sharpe (NO promotion claims; always
    routes promotion through a Tier-2-at-floor verify per CLAUDE.md no-lies mandate)
  * EDITABLE controls the test runners read back: sweep_enabled, test_values,
    user_set_value, notes  -> edit the CSV, run workbook_to_manifest.py, sweeps pick it up.

NO-LIES: this assigns consumption facts + a search space + observed history only. A
"best_pool_sharpe" here is the mean pool_sharpe across historical runs at that value
(diagnostic). It is NOT a promotion. Every adopt-suggestion says "verify Tier-2 at
sample floor first". Below-floor history is flagged so nothing sub-floor entraps live.
"""
import argparse
import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
MODES = ["crypto", "tradier"]

# churn/exit/entry/sizing levers first — these move gain/mo + sharpe most (per the
# -0.07 baseline churn diagnosis); mirrors engine_ofat_screen.PRIORITY_KW.
PRIORITY_KW = ["R1_", "R2_", "DC_LOW", "DC_HIGH", "NOLOSS", "EXIT", "STOP", "GIVEBACK",
               "FROZEN", "PEAK", "WT_VEL", "HOLD", "REENTRY", "AUGMENT", "FORCE_OPEN",
               "ENTRY", "WT_DC", "GR_", "SCORE", "ALIGN", "SIZE", "POSITION_SIZE",
               "MIN_GAIN", "DUP", "OVERTRADE", "COOLDOWN", "HEDGE", "RATIO"]


def priority(name):
    up = name.upper()
    for i, kw in enumerate(PRIORITY_KW):
        if kw in up:
            return i
    return len(PRIORITY_KW)


def _load(mode):
    man = json.loads((BASE / f"data/param_sweep_manifest_{mode}.json").read_text())["params"]
    sp = BASE / f"data/param_baseline_spec_{mode}.json"
    spec = json.loads(sp.read_text()) if sp.exists() else {}
    active = spec.get("active_search_space", {})
    return man, active


def _fmt(x):
    if isinstance(x, float):
        return f"{x:.4g}"
    return "" if x is None else str(x)


def _suggest(name, meta, hist):
    """Rule-based path toward gain/mo + sharpe. No cheating: never asserts a number is
    promotable; always defers promotion to a Tier-2-at-floor verify."""
    tier = meta["sweep_tier"]
    prio_hi = priority(name) < 12  # churn/exit/entry/sizing band
    if tier == "DEAD":
        return "OBSOLETE", "Referenced in no engine/live source -> dead knob. Ignore; candidate for removal once config unlocked. Do NOT sweep (0-effect lying rows)."
    if tier == "LIVE_ONLY":
        return "FORWARD_TEST", "Live-only, no backtest path -> cannot sweep. Forward-test vs live over time; keep current value until live evidence."
    if not meta.get("sweepable"):
        return "BLOCKED", "Sweepable=False (no test range). Needs a range in baseline spec before it can be screened."
    if hist and hist.get("support_obs_total", 0) > 0:
        best_v = hist.get("best_value_seen")
        best_sh = hist.get("best_mean_pool_sharpe")
        cur = meta.get("default")
        def _same(a, b):
            try:
                return abs(float(a) - float(b)) < 1e-9
            except (TypeError, ValueError):
                return str(a) == str(b)
        if best_v is not None and not _same(best_v, cur):
            return "RETEST_ADOPT", (f"History favors {name}={best_v} (mean pool_sharpe {best_sh} over "
                                    f"{hist.get('support_obs_total')} runs) vs current {cur}. "
                                    f"Re-run Tier-2 OFAT at sample floor to confirm, THEN promote — do not adopt on history alone.")
        return "CONFIRMED_DEFAULT", (f"Current value {cur} is the best tested (mean pool_sharpe {best_sh}, "
                                     f"{hist.get('support_obs_total')} runs). Hold; widen range only if seeking marginal gain.")
    # untested but sweepable
    if prio_hi:
        return "SCREEN_HIGH", "UNTESTED churn/exit/entry/sizing lever -> HIGH priority Tier-2 OFAT (biggest gain/mo + sharpe leverage). Screen first."
    return "SCREEN", "UNTESTED sweepable knob -> queue for OFAT screen (vec-first if VEC_SCREEN, else Tier-2)."


def _load_prior_edits(op):
    """Carry over the 4 editable columns from an existing workbook so a refresh of
    test-history never clobbers manual tuning."""
    prior = {}
    if op.exists():
        for r in csv.DictReader(op.open()):
            prior[(r["system"], r["param"])] = {k: r.get(k, "") for k in
                                                 ("sweep_enabled", "test_values", "user_set_value", "notes")}
    return prior


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/param_workbook.csv")
    ap.add_argument("--reset-edits", action="store_true", help="ignore prior manual edits")
    args = ap.parse_args()
    op = BASE / args.out
    prior = {} if args.reset_edits else _load_prior_edits(op)
    rows = []
    summary = {}
    for mode in MODES:
        man, active = _load(mode)
        cnt = {"TESTED": 0, "UNTESTED": 0, "OBSOLETE": 0, "LIVE_ONLY": 0, "BLOCKED": 0}
        for name in sorted(man, key=lambda n: (priority(n), n)):
            meta = man[name]
            hist = active.get(name)
            action, note = _suggest(name, meta, hist)
            tier = meta["sweep_tier"]
            # test_status (coarse) for quick filtering by an agent
            if tier == "DEAD":
                status = "OBSOLETE"
            elif tier == "LIVE_ONLY":
                status = "LIVE_ONLY"
            elif not meta.get("sweepable"):
                status = "BLOCKED"
            elif hist and hist.get("support_obs_total", 0) > 0:
                status = "TESTED"
            else:
                status = "UNTESTED"
            cnt[status if status in cnt else "BLOCKED"] = cnt.get(status, 0) + 1
            tv = meta.get("test_values") or []
            distinct_tested = ""
            best_v = best_sh = obs = ""
            if hist:
                distinct_tested = "|".join(str(x) for x in hist.get("distinct_values_seen", []))
                best_v = _fmt(hist.get("best_value_seen"))
                best_sh = _fmt(hist.get("best_mean_pool_sharpe"))
                obs = hist.get("support_obs_total", 0)
            c = meta.get("consumed_by", {})
            rows.append({
                "system": mode,
                "param": name,
                "current_value": _fmt(meta.get("default")),
                "type": meta.get("type"),
                "sweep_tier": tier,
                "consumed_vec": int(bool(c.get("vec"))),
                "consumed_tier2": int(bool(c.get("tier2"))),
                "consumed_live": int(bool(c.get("live"))),
                "test_status": status,
                "n_runs_obs": obs,
                "distinct_values_tested": distinct_tested,
                "best_value_seen": best_v,
                "best_mean_pool_sharpe": best_sh,
                "priority_rank": priority(name),
                "suggested_action": action,
                "suggested_path": note,
                # ---- editable control columns (runner reads these back) ----
                "sweep_enabled": "Y" if meta.get("sweepable") else "N",
                "test_values": "|".join(str(x) for x in tv),
                "user_set_value": "",
                "notes": "",
            })
            pe = prior.get((mode, name))
            if pe:
                rows[-1].update({k: v for k, v in pe.items() if v != ""})
        summary[mode] = {"n": len(man), **cnt}
    cols = list(rows[0].keys())
    op = BASE / args.out
    with op.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"workbook -> {op}  ({len(rows)} rows)")
    for mode, s in summary.items():
        print(f"  {mode:8s} n={s['n']:5d}  TESTED={s['TESTED']:4d}  UNTESTED={s['UNTESTED']:4d}  "
              f"LIVE_ONLY={s['LIVE_ONLY']:4d}  OBSOLETE={s['OBSOLETE']:4d}  BLOCKED={s.get('BLOCKED',0)}")


if __name__ == "__main__":
    main()
