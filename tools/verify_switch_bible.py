#!/usr/bin/env python3
"""verify_switch_bible — GUARD against broken links between templates, configs, live code and the vectorized engine (exit 1 = broken).

Builds a FRESH bible in memory (tools/build_switch_bible.py, ~30 s) and checks it (and, when a baseline exists, compares it with the last ACCEPTED bible
data/SWITCH_BIBLE_baseline.json). BROKEN checks:
  TEMPLATE_NO_CONFIG        a template switch/filter that exists in no config (Config / TradierConfig / QuickConfig) and is not grey in every row
  LINK_LOST                 (needs baseline) a switch that had a live read / reachable vec read in the baseline and now has none (name no longer referenced on that surface;
                            a moved file:line is fine, a vanished read is not)
  DEFAULT_MISMATCH          a template switch whose venue-config default and QuickConfig default differ and is not on the documented conflict list
                            (data/per_sym_settings.json _meta.conflicts_quick_vs_live)
  VEC_MODULE_NOT_CALLED     (needs baseline) a vec_decisions module that is no longer reachable from simulate_one (new vs baseline). The legacy never-called set is reported as WARN.
  NOT_IN_CAT_SIDE_DEFAULTS  a template switch present in the config but absent from data/per_sym_settings.json for that template's cat_side
  DUPLICATE_SWITCH_ROWS     a white switch name that appears in more than one tab of the same template
  COVERAGE                  a switch the bible says is wired (live read + reachable vec read) that is in NO template (the coverage rule: every vectorized function >=1x across the 4 templates)
  TAB_FIRST_ROW_NOT_DEFAULT a tab whose first data row is not an is_default=YES row
  TAB_NO_ORANGE_ROWS        a tab (except STDEV_SLOPE_SIZING) without orange filter rows
  python tools/verify_switch_bible.py [--rebaseline] [--since-md5] [--json OUT]
--rebaseline  write the fresh bible as the accepted baseline (also done automatically when no baseline exists yet or all checks pass with --accept-on-pass)
--since-md5   print which scanned files changed and which switches changed status/read counts vs the baseline, then exit 0
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_switch_bible as B  # noqa: E402

BASELINE = ROOT / "data" / "SWITCH_BIBLE_baseline.json"


def same(a, b):
    if a == b or str(a) == str(b):
        return True
    try:
        return float(a) == float(b)  # bool/0.0/0 are the same category (TradierConfig stores bools as floats)
    except Exception:
        return False


def absent(v):
    return isinstance(v, str) and v.startswith("<")


def run_checks(cur, base):
    sw, meta = cur["switches"], cur["meta"]
    csd = B.load_json(B.DATA / "per_sym_settings.json", {})
    conflicts = (csd.get("_meta", {}) or {}).get("conflicts_quick_vs_live", {})
    res = collections.OrderedDict((k, []) for k in ("TEMPLATE_NO_CONFIG", "LINK_LOST", "DEFAULT_MISMATCH", "VEC_MODULE_NOT_CALLED", "NOT_IN_CAT_SIDE_DEFAULTS", "DUPLICATE_SWITCH_ROWS", "COVERAGE", "TAB_FIRST_ROW_NOT_DEFAULT", "TAB_NO_ORANGE_ROWS"))
    warn = collections.OrderedDict()
    for n, s in sw.items():
        tpl = s["template"]
        if not tpl:
            continue
        # TEMPLATE_NO_CONFIG
        if s["status"]["crypto"] == "NOT_IN_CONFIG":
            rows = [r for t in tpl.values() for r in t["rows"]]
            if not rows or not all(r["grey"] for r in rows):
                res["TEMPLATE_NO_CONFIG"].append(f"{n}: in templates {sorted(tpl)} but in no config")
        # DEFAULT_MISMATCH (per venue)
        d = s["defaults"]
        for venue, live_key, quick_key in (("CRYPTO", "config.py", "QuickConfig"), ("STOCKS", "config_tradier.py", "QuickConfig_apply_tradier_defaults")):
            cats = [cs for cs in tpl if cs.startswith(venue)]
            if not cats:
                continue
            lv, qv = d[live_key], d[quick_key]
            if absent(qv) and venue == "STOCKS":
                qv = d["QuickConfig"]
            if absent(lv) or absent(qv) or same(lv, qv):
                continue
            if any(n in (conflicts.get(cs) or []) for cs in cats):
                continue
            res["DEFAULT_MISMATCH"].append(f"{n} [{venue}]: {live_key}={lv!r} vs QuickConfig={qv!r} (not on the documented conflict list)")
        # NOT_IN_CAT_SIDE_DEFAULTS
        for cs, t in tpl.items():
            if any(not r["orange"] for r in t["rows"]) and not all(r["grey"] for r in t["rows"]):
                if (d["cat_side_defaults_4"].get(cs) == "<absent>") and (s["in_config"]["config.py"] or s["in_config"]["config_tradier.py"] or s["in_config"]["QuickConfig"]):
                    res["NOT_IN_CAT_SIDE_DEFAULTS"].append(f"{n} [{cs}]")
        # DUPLICATE_SWITCH_ROWS
        for cs, t in tpl.items():
            tabs = sorted({r["tab"] for r in t["rows"] if not r["orange"]})
            if len(tabs) > 1:
                res["DUPLICATE_SWITCH_ROWS"].append(f"{n} [{cs}] in tabs {tabs}")
    # COVERAGE_EXCLUDED 2026-10-04 cut5: wired but never sweep rows — secrets,
    # file/dir paths, §17.3 infra, and non-scalar STRUCTURE (list/dict/tuple:
    # not expressible as scalar sweep options, v15_template_options STRUCT).
    # Visible warn, never silent; a template row still clears via s["template"].
    cov_excluded = {
        "TRADIER_API_KEY", "GAP_CLOSE_PER_SYMBOL_INVENTORY_FILE",
        "BASE_PATH", "BLACKLIST_SYMBOLS", "CYCLE_TP_TIERED_LEVELS",
        "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS", "ENTRY_STOCH_HHHL_DIRECT_TFS",
        "LR_BAND_LADDER_TF_BOTTOM", "LR_BAND_LADDER_TF_TOP",
        "STDEV_BREAKOUT_HTF_LIST", "STDEV_BREAKOUT_RETEST_TF_LIST",
        "UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS", "WATCHDOG_DC_TFS",
    }
    infra_sfx = ("_PATH", "_PATHS", "_FILE", "_FILES", "_DIR", "_DIRS", "_CACHE", "_TOKEN", "_TOKENS", "_KEY", "_KEYS", "_URL", "_URLS", "_HOST", "_HOSTS", "_PORT", "_PORTS")
    # COVERAGE
    for n, s in sw.items():
        if s["template"]:
            continue
        if n in cov_excluded or n in ("BASE_TF",) or n.endswith(infra_sfx):
            warn.setdefault("COVERAGE_EXCLUDED (infra/secret/non-scalar by design, never sweep rows)", []).append(n)
            continue
        wired = any(st.startswith("WIRED_BOTH") for st in s["status"].values())  # STAGED_VEC suffix = wiring staged, not yet deployed: not wired
        if wired:
            res["COVERAGE"].append(f"{n}: wired live+vec ({s['live_reads']['crypto'][:1] or s['live_reads']['stocks'][:1]} / {s['vec_reads'][:1]}) but in no template")
        elif s["vec_read_count"]["reachable"] and (s["in_config"]["config.py"] or s["in_config"]["config_tradier.py"]):
            warn.setdefault("COVERAGE_VEC_ONLY_NOT_IN_TEMPLATE (needs live wiring first)", []).append(n)
    # tab structure
    for cs, tabs in meta["template_tabs"].items():
        for tab, t in tabs.items():
            if not t["first_row_default"]:
                res["TAB_FIRST_ROW_NOT_DEFAULT"].append(f"{cs}!{tab}")
            if tab != "STDEV_SLOPE_SIZING" and t["orange"] == 0:
                res["TAB_NO_ORANGE_ROWS"].append(f"{cs}!{tab} (white rows {t['white']})")
    # baseline comparisons
    if base:
        bsw = base["switches"]
        for n, bs in bsw.items():
            cs_ = sw.get(n)
            for v in ("crypto", "stocks"):
                if bs["live_read_count"][v] > 0 and (cs_ is None or cs_["live_read_count"][v] == 0):
                    res["LINK_LOST"].append(f"{n}: live {v} read vanished (baseline {bs['live_reads'][v][:1]})")
            if bs["vec_read_count"]["reachable"] > 0 and (cs_ is None or cs_["vec_read_count"]["reachable"] == 0):
                res["LINK_LOST"].append(f"{n}: reachable vector read vanished (baseline {bs['vec_reads'][:1]})")
        old = set(base["meta"].get("vec_modules_never_called_from_simulate_one", []))
        new = set(meta["vec_modules_never_called_from_simulate_one"])
        base_all = set(base["meta"].get("vec_modules_all", []))
        # VEC_MODULE_PREP_SIDE 2026-10-04: applied on the pilot prep path
        # (prepare_batch/load_npz), not simulate_one — architecture, not
        # regression. Each entry needs a prep-path proof (MARK/stamp asserted
        # after prepare_batch); proofs in data/wiring/proofs_cut5.jsonl.
        prep_side = {"vec_decisions/htf_causal_align.py"}
        for m in sorted(new - old):
            if m in prep_side:
                warn.setdefault("VEC_MODULE_PREP_SIDE (prep-path architecture, proof on file)", []).append(m)
                continue
            if base_all and m not in base_all:
                warn.setdefault("VEC_MODULES_NEW_NEVER_CALLED (triage: new module, not a regression)", []).append(m)
            else:
                res["VEC_MODULE_NOT_CALLED"].append(f"{m}: was reachable from simulate_one in the baseline, not any more")
        warn["VEC_MODULES_NEVER_CALLED_LEGACY (known baseline)"] = [f"{len(new & old)} modules (list in meta.vec_modules_never_called_from_simulate_one)"]
    else:
        warn["VEC_MODULES_NEVER_CALLED"] = [f"{len(meta['vec_modules_never_called_from_simulate_one'])}/{meta['vec_modules_total']} (no baseline yet)"]
    return res, warn


def since_md5(cur, base):
    if not base:
        print("no baseline yet")
        return
    chg = [f for f, m in cur["meta"]["files_md5"].items() if base["meta"]["files_md5"].get(f) != m]
    print(f"files changed since baseline ({len(chg)}): " + ", ".join(chg[:40]))
    trans = collections.Counter()
    ex = collections.defaultdict(list)
    for n, s in cur["switches"].items():
        b = base["switches"].get(n)
        for v in ("crypto", "stocks"):
            old = b["status"][v] if b else "<new>"
            if old != s["status"][v]:
                trans[(v, old, s["status"][v])] += 1
                ex[(v, old, s["status"][v])].append(n)
    for k, c in trans.most_common():
        print(f"  {k[0]:7s} {k[1]:24s} -> {k[2]:24s} {c:4d}  e.g. {', '.join(ex[k][:5])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebaseline", action="store_true")
    ap.add_argument("--accept-on-pass", action="store_true")
    ap.add_argument("--since-md5", action="store_true")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    cur = B.build()
    base = B.load_json(BASELINE) if BASELINE.exists() else None
    if a.since_md5:
        since_md5(cur, base)
        return 0
    res, warn = run_checks(cur, base)
    broken = sum(len(v) for v in res.values())
    print(f"[verify] {cur['meta']['n_switches']} names; baseline {'yes' if base else 'NONE'}; BROKEN={broken}")
    for k, v in res.items():
        print(f"  {k:28s} {len(v):5d}")
        for line in v[:12]:
            print(f"      - {line}")
        if len(v) > 12:
            print(f"      … +{len(v) - 12} more")
    for k, v in warn.items():
        print(f"  WARN {k}: {len(v)} {v[:5] if len(v) <= 5 else ''}")
    if a.json:
        Path(a.json).write_text(json.dumps({"broken": broken, "checks": res, "warn": warn}, indent=1))
    if a.rebaseline or base is None or (a.accept_on_pass and broken == 0):
        BASELINE.write_text(json.dumps(cur, indent=1, default=str, sort_keys=True))
        print(f"[baseline] written {BASELINE}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
