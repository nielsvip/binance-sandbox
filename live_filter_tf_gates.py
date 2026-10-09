"""FILTER_TF WT-ALIGN live twin (USER 2026-10-08 ruling: LIVE IMITATES VEC).

Shared predicate mirroring vec build_masks()['entry'] wt_cross_side legs
(vec_decisions/generic_filter_tf.py, ANDed v12_quick_engine:12650): each mapped
entry FILTER_TF with a real TF requires wt1_TF > wt2_TF (long) / < (short),
strict; both-zero (missing data) passes; OFF/unknown TF inert via missing-key
fail-open. SCOPE: wt_cross_side kind only — other FILTER_TF_MAP kinds need
dedicated twins (all OFF at current defaults). Kill switch: any leg OFF.
"""

def check_filter_tf_wt_align(get, ind, is_long, action):
    """Returns (blocked, why). get(k, default) resolves the switch (_cfg in live)."""
    act = (action or "").upper()
    if ("OPEN" not in act and act != "BUY") or "CLOSE" in act or "REDUCE" in act:
        return False, ""
    from vec_decisions.generic_filter_tf import FILTER_TF_MAP
    m = ind or {}
    for name, (target, kind) in FILTER_TF_MAP.items():
        if target != "entry" or kind != "wt_cross_side":
            continue
        tf = str(get(name, "OFF") or "OFF").strip()
        if tf.upper() == "OFF":
            continue
        w1 = m.get("wt1_%s" % tf)
        w2 = m.get("wt2_%s" % tf)
        w1 = float(w1) if w1 is not None else 0.0
        w2 = float(w2) if w2 is not None else 0.0
        if w1 == 0.0 and w2 == 0.0:
            continue
        ok = (w1 > w2) if is_long else (w1 < w2)
        if not ok:
            return True, "%s_%s_WT_AGAINST" % (name, tf)
    return False, ""
