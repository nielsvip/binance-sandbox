#!/usr/bin/env python3
"""KINDERGARTEN-FIRST phase: retire EMA_BLANKET by letting KINDERGARTEN win on measured delta.

OPERATOR 2026-10-27, verbatim:
  "We need to ungrey EMA_BLANKET as it is True in most per_sym settings, and make sure it is taken
   over 'naturally' by KINDERGARTEN by doing this: We need to add a step after baseline calculation
   where we switch it off, test every KINDERGARTEN option until we find the best option (hopefully
   not EMA BLANKET), then start testing all switches as usual. This way in one run we will get it to
   turn False everywhere and then we can take it out without any risk. NONE of the kindergarten rows
   can be greyed or skipped in any other way."

Run order inside v15_pilot: baseline -> THIS PHASE -> the normal row sweep. The phase:
  1. forces EMA_BLANKET_FILTER_ENABLED=False on top of the baseline overrides,
  2. evaluates every KINDERGARTEN-family option combination that the template actually offers,
  3. keeps the best one ONLY if it beats the board's own baseline (greedy law, bible §14 -- a
     negative or zero delta promotes nothing and the chain is left exactly as the baseline left it),
  4. returns the winning overrides plus an honest record of every option it measured.

Design notes that matter for correctness:
  * The blanket is NOT inert when EMA_BLANKET_FILTER_FILTER_TF == "OFF": v12_quick_engine.py:10473
    routes OFF to vec_decisions.wave4_families.ema_blanket_entry_gate, a different gate path. So
    "blanket off" means EMA_BLANKET_FILTER_ENABLED=False, never merely TF=OFF.
  * Measured 2026-10-27 in data/per_sym_full_sets.json: EMA_BLANKET_FILTER_ENABLED is True on 18 of
    791 per-symbol sets and False on 773, while KINDERGARTEN_EMA_GATE_ENABLED is True on 547 and
    EMA_9_21_FILTER_ENABLED on 434. So the takeover is mostly done; this phase is what converts the
    remaining 18 safely and proves the rest, instead of deleting a filter that is still live.
  * EMA_9_21_FILTER_TFS and EMA_9_21_TIMEFRAME are CO-EQUAL (a UNION, per
    vec_decisions/kg_entry_gate.py:4 and :21-22), not TFS-first-with-fallback. Both are swept here.
  * The evaluator is injected, so this module imports nothing from v15_pilot and is unit-testable.

The pilot-side hook is one line after the baseline write:
    kg = __import__("tools.v15_kindergarten_first", fromlist=["run"]).run(
             symside=new_symside, overrides=cumulative_overrides, defaults=defaults,
             template=wb_path, evaluate=lambda ov: evaluate_prepared_sanitized(prepared, ov, args.window_days),
             baseline_gain=baseline_gain, progress=progress)
    if kg.get("promoted"): cumulative_overrides.update(kg["overrides"])
"""
import itertools
import json
import os
import pathlib
import time

KG_SWITCHES = (
    "KINDERGARTEN_EMA_GATE_ENABLED",
    "KINDERGARTEN_FILTER_TF",
    "KINDERGARTEN_STRICT_TFS",
    "KINDERGARTEN_CUMULATIVE_MIN_TFS",
    "KINDERGARTEN_CUMULATIVE_MODE",
    "EMA_9_21_FILTER_ENABLED",
    "EMA_9_21_FILTER_TFS",
    "EMA_9_21_TIMEFRAME",
    "EMA_9_21_FILTER_MIN_TFS",
)
BLANKET_OFF = {"EMA_BLANKET_FILTER_ENABLED": False}
GREY_SKIP_RGB = {"FFBFBFBF", "00BFBFBF"}
# col-B values that are labels, not candidates (seen on EMA_9_21_FILTER_MIN_TFS: three "MTF Gate" rows)
NON_CANDIDATE = {"mtf gate", "option value", "sheets applicable", "gates", ""}


def template_options(template, switches=KG_SWITCHES):
    """{switch: [candidate, ...]} from the workbook. Grey rows are REPORTED, never silently dropped:
    the operator requires that no kindergarten row be greyed or skipped, so a grey one is a defect to
    surface, not a row to quietly ignore."""
    import openpyxl
    wb = openpyxl.load_workbook(str(template), data_only=False)
    opts, grey = {}, []
    try:
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            for rr in range(3, ws.max_row + 1):
                cell = ws.cell(row=rr, column=1)
                sw = str(cell.value or "").strip()
                if sw not in switches:
                    continue
                font = cell.font
                rgb = str(getattr(font.color, "rgb", "") or "").upper() if (font is not None and font.color is not None) else ""
                if rgb in GREY_SKIP_RGB:
                    grey.append("%s!%d:%s" % (sheet, rr, sw))
                val = ws.cell(row=rr, column=2).value
                text = "" if val is None else str(val).strip()
                if text.lower() in NON_CANDIDATE:
                    continue
                opts.setdefault(sw, [])
                if val not in opts[sw]:
                    opts[sw].append(val)
    finally:
        wb.close()
    return opts, grey


def _grid(opts, max_combos):
    """Per-switch sweep first (cheap, ALWAYS COMPLETE), then bounded pairwise over the switches that
    have >1 option. Never a full cartesian product: 9 switches x 5 options would be 1.9M evaluations.

    USER 2026-10-28: "are ALL Kindergarten rows now tested on both crypto and stocks AS SOON AS the
    baseline is defined and before any switch gets tested". The cap used to be applied to
    `singles + pairs` as one list, so a `V15_KG_FIRST_MAX` below the number of single options (35 per
    cat_side today) would silently drop whole kindergarten ROWS -- the exact thing the operator forbids.
    The cap now bounds the PAIRWISE part only; every single-option candidate is always returned.
    Returns (candidates, n_singles) so the caller can refuse to let the time budget cut singles short.
    """
    singles = []
    for sw in KG_SWITCHES:
        for val in opts.get(sw, []):
            singles.append({sw: val})
    pairs = []
    multi = [sw for sw in KG_SWITCHES if len(opts.get(sw, [])) > 1]
    for a, b in itertools.combinations(multi, 2):
        for va in opts[a]:
            for vb in opts[b]:
                pairs.append({a: va, b: vb})
    out, seen = [], set()
    for cand in singles:
        key = tuple(sorted((k, str(v)) for k, v in cand.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    n_singles = len(out)
    for cand in pairs:
        if len(out) - n_singles >= max(0, max_combos - n_singles):
            break
        key = tuple(sorted((k, str(v)) for k, v in cand.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out, n_singles


def run(symside, overrides, defaults, template, evaluate, baseline_gain,
        progress=None, budget_s=None, max_combos=None, log=print):
    """Returns {promoted, overrides, best, baseline_gain, blanket_off_gain, measured, grey_rows, ...}.
    `evaluate(ov)` must return the engine result dict for the FULL override set `ov`."""
    t0 = time.time()
    budget_s = float(budget_s if budget_s is not None else os.environ.get("V15_KG_FIRST_S", "600"))
    max_combos = int(max_combos if max_combos is not None else os.environ.get("V15_KG_FIRST_MAX", "120"))
    base_ov = dict(overrides or {})
    out = {"symside": symside, "baseline_gain": float(baseline_gain), "promoted": False,
           "overrides": {}, "best": None, "measured": [], "grey_rows": [], "budget_s": budget_s}

    try:
        opts, grey = template_options(template)
    except Exception as exc:
        out["error"] = "template read failed: %s" % exc
        log("[KG-FIRST] %s template unreadable: %s" % (symside, exc))
        return out
    out["grey_rows"] = grey
    if grey:
        # operator: "NONE of the kindergarten rows can be greyed or skipped in any other way"
        log("[KG-FIRST-DEFECT] %s %d kindergarten rows are GREY and will not be swept by the pilot: %s"
            % (symside, len(grey), ", ".join(grey[:6])))
    if not opts:
        out["error"] = "no kindergarten options in template"
        log("[KG-FIRST] %s no kindergarten rows found in %s" % (symside, template))
        return out

    def _gain(res):
        if not isinstance(res, dict):
            return None
        for k in ("gain_pct", "gain", "final_gain"):
            if isinstance(res.get(k), (int, float)):
                return float(res[k])
        return None

    # step 1 -- blanket OFF, everything else as the baseline left it. This is the reference the
    # kindergarten options must beat, and it is reported separately so the cost of simply switching
    # the blanket off is visible on its own.
    off_ov = {**base_ov, **BLANKET_OFF}
    off_gain = _gain(evaluate(off_ov))
    out["blanket_off_gain"] = off_gain
    log("[KG-FIRST] %s baseline=%.4f blanket_off=%s options=%s"
        % (symside, float(baseline_gain), ("%.4f" % off_gain) if off_gain is not None else "ERR",
           {k: len(v) for k, v in opts.items()}))
    if off_gain is None:
        out["error"] = "blanket-off evaluation failed"
        return out

    # step 2 -- sweep the kindergarten grid on top of blanket-off
    best = {"gain": off_gain, "ov": {}, "label": "blanket_off_only"}
    grid, n_singles = _grid(opts, max_combos)
    out["n_singles"] = n_singles
    out["n_candidates"] = len(grid)
    for i, cand in enumerate(grid):
        # USER 2026-10-28: EVERY kindergarten row must be tested. The time budget may bound the
        # pairwise tail, never the per-row singles — so the budget check does not apply while
        # i < n_singles. A single that still cannot be measured is reported as a DEFECT, not skipped
        # silently, exactly like a grey row.
        if i >= n_singles and time.time() - t0 > budget_s:
            out["budget_hit"] = True
            log("[KG-FIRST] %s budget %.0fs hit after %d evals (all %d per-row singles measured first)"
                % (symside, budget_s, len(out["measured"]), n_singles))
            break
        gain = _gain(evaluate({**off_ov, **cand}))
        label = "+".join("%s=%s" % (k, v) for k, v in sorted(cand.items()))
        out["measured"].append({"label": label, "gain": gain,
                                "delta_vs_baseline": None if gain is None else round(gain - float(baseline_gain), 6)})
        if gain is not None and gain > best["gain"] + 1e-9:
            best = {"gain": gain, "ov": dict(cand), "label": label}
    out["best"] = {"label": best["label"], "gain": best["gain"],
                   "delta_vs_baseline": round(best["gain"] - float(baseline_gain), 6)}
    _unmeasured = [m["label"] for m in out["measured"][:n_singles] if m.get("gain") is None]
    _missing_rows = n_singles - len([m for m in out["measured"][:n_singles]])
    if _unmeasured or _missing_rows > 0:
        log("[KG-FIRST-DEFECT] %s %d of %d per-row kindergarten singles produced NO gain (%d never ran): %s"
            % (symside, len(_unmeasured), n_singles, max(0, _missing_rows), ", ".join(_unmeasured[:6])))
        out["singles_unmeasured"] = _unmeasured
        out["singles_never_ran"] = max(0, _missing_rows)

    # step 3 -- greedy law: promote ONLY a real improvement over the board's own baseline
    if best["gain"] > float(baseline_gain) + 1e-9:
        out["promoted"] = True
        out["overrides"] = {**BLANKET_OFF, **best["ov"]}
        log("[KG-FIRST] %s PROMOTE %s gain=%.4f (+%.4f vs baseline) -- EMA_BLANKET_FILTER_ENABLED=False"
            % (symside, best["label"], best["gain"], best["gain"] - float(baseline_gain)))
    else:
        log("[KG-FIRST] %s NO-PROMOTE best=%s gain=%.4f vs baseline %.4f -- blanket stays as the baseline had it"
            % (symside, best["label"], best["gain"], float(baseline_gain)))

    out["elapsed_s"] = round(time.time() - t0, 2)
    if isinstance(progress, dict):
        progress["kindergarten_first"] = out
    return out


def _selftest():
    """Synthetic evaluator: kindergarten beats the blanket, so the phase must promote and set False."""
    base = {"EMA_BLANKET_FILTER_ENABLED": True}

    def evaluate(ov):
        g = 10.0
        if ov.get("EMA_BLANKET_FILTER_ENABLED") is False:
            g += 1.0
        if ov.get("KINDERGARTEN_EMA_GATE_ENABLED") is True:
            g += 5.0
        if str(ov.get("EMA_9_21_FILTER_TFS") or "") == "1h,D":
            g += 2.0
        return {"gain_pct": g}

    class FakeWB:
        pass

    opts = {"KINDERGARTEN_EMA_GATE_ENABLED": [True, False], "EMA_9_21_FILTER_TFS": ["1h", "1h,D"]}
    global template_options
    real = template_options
    template_options = lambda *_a, **_k: (opts, [])
    try:
        r = run("TEST_LONG", base, {}, "none.xlsx", evaluate, baseline_gain=10.0, log=lambda *_: None)
    finally:
        template_options = real
    assert r["promoted"] is True, r
    assert r["overrides"]["EMA_BLANKET_FILTER_ENABLED"] is False, r
    assert r["best"]["gain"] >= 16.0, r
    # and it must NOT promote when nothing beats the baseline
    r2 = None
    template_options = lambda *_a, **_k: ({"KINDERGARTEN_EMA_GATE_ENABLED": [False]}, [])
    try:
        r2 = run("TEST2_LONG", base, {}, "none.xlsx", lambda ov: {"gain_pct": 9.0}, baseline_gain=10.0, log=lambda *_: None)
    finally:
        template_options = real
    assert r2["promoted"] is False, r2
    print("selftest OK: promote=%s best=%s | no-promote case ok" % (r["overrides"], r["best"]["label"]))


if __name__ == "__main__":
    _selftest()
