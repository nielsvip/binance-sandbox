"""USER 2026-10-23 v15_pending sweep: pv3 C3 (docs/v15_pending/22_SWITCH_PROPOSALS_V3_20261022.md §3), wired.

REENTRY_MASK_ARM_<FAM>_ENABLED (default False each): apply ONE entry-mask family to reentry fires, independent of
REENTRY_ENTRY_FILTER_ENABLED (the lenient all-mask count) and REENTRY_APPLY_ENTRY_GATES_ENABLED (the whole gate stack).
An armed family whose <FAM>_FILTER_TF is OFF in the running set does nothing (same as for fresh entries); master-gated
families follow their master. No switch True -> None -> today's behaviour byte for byte.
"""
from vec_decisions import filter_tf_gates as _ftg
from vec_decisions import generic_filter_tf as _gft

FAMILIES = {
    "CIRCUIT_SHARPE_GATES": "CIRCUIT_SHARPE_GATES_FILTER_TF",
    "DC_BREAK": "DC_BREAK_FILTER_TF",
    "BREAKOUT_RETEST": "BREAKOUT_RETEST_FILTER_TF",
    "DC_MOMENTUM_BOTA_SCORER": "DC_MOMENTUM_BOTA_SCORER_FILTER_TF",
    "DELTA_ENGINE": "DELTA_ENGINE_FILTER_TF",
    "MOM3": "MOM3_FILTER_TF",
    "MOMENTUM_BREAKOUT": "MOMENTUM_BREAKOUT_FILTER_TF",
}
MASTERS = {"BREAKOUT_RETEST_FILTER_TF": "BREAKOUT_RETEST_ARMED_ENABLED"}


def armed(cfg) -> list:
    return [b for b in FAMILIES if bool(getattr(cfg, f"REENTRY_MASK_ARM_{b}_ENABLED", False))]


def armed_mask(npz, n, is_long, cfg, close, safe):
    m = None
    for base in armed(cfg):
        name = FAMILIES[base]
        tf = str(getattr(cfg, name, "OFF") or "OFF").strip()
        if tf.upper() == "OFF" or (name in MASTERS and not bool(getattr(cfg, MASTERS[name], False))):
            continue
        if name in _gft.FILTER_TF_MAP:
            mk = _gft._cond(_gft.FILTER_TF_MAP[name][1], npz, n, tf, is_long, close, safe)
        elif name == "MOM3_FILTER_TF":
            mk = _ftg.mom3_entry_gate(npz, n, is_long, cfg, close, safe)
        elif name == "MOMENTUM_BREAKOUT_FILTER_TF":
            mk = _ftg.momentum_breakout_gate(npz, n, is_long, cfg, close, safe)
        else:
            mk = None
        if mk is None:
            continue
        m = mk if m is None else (m & mk)
    return m
