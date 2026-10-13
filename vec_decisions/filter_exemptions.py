"""FILTER_EXEMPTIONS — per-switch filter exemption (V17, USER 2026-10-13).

USER: "the filter ablation is there so you can find the entry and augment and reentry switches that should NOT BE
affected by a certain filter ... then you put the filters back on and have the pos delta over the previous best gain".

Config key (QuickConfig / config.py / config_tradier.py): FILTER_EXEMPTIONS = {path_id: [filter_key, ...]}.
Default {} = zero reads beyond the emptiness check = current behaviour exactly.

path_id kinds (classify()):
  * ENTRY  — an entry family alias of v12_quick_engine._ENTRY_FAMILY_ALIASES (e.g. "MOM3", "DC_BREAKOUT") or a raw
             compute_reentry_blocks() block name (e.g. "B_WT_15M_CROSS_ENTRY"). Honoured by a second pre-loop pass of the
             SAME simulate_one code with the listed filters forced off and ENTRY_ISOLATE_FAMILY=<path>; the extra open
             bars are restricted to the bars where that family's raw block fires in the main pass (R = raw_A & entry_B),
             and on those bars every open choke gate takes its filters-off state. A family whose master is off in the
             main pass has no raw bars -> the exemption can never enable a disabled family.
  * REENTRY — every reentry fire (post-close open that is not a fresh entry_sig bar). Honoured by a second pre-loop
             pass with the filters forced off (no isolation); reentry fires read the open-gate arrays of that pass.
  * AUGMENT / AUGMENT_<SRC> — in-loop augment gates. SRC in AUGMENT_SOURCES. A gate is bypassed on a bar when any
             active augment source of that bar is exempt from it.

Filter keys: an ENTRY/REENTRY filter key must be a member of a filter-ablation group (vec_decisions/filter_ablation_groups
GROUPS — curated: permits/bypasses/signal-TFs excluded) and is forced to its off value (bool False / TF 'OFF') in the
second pass. Numeric thresholds have no off value and are ignored (reported by validate()). AUGMENT keys must be in
AUGMENT_GATE_KEYS (the gates the augment choke point evaluates).

Live: crypto live runs PARITY_VEC_EXACT_MODE (families ENTRY,EXIT,AUGMENT) — the live decision is this simulate_one run
with the sym_side's promoted overrides, so a promoted FILTER_EXEMPTIONS is honoured live by construction. Stocks honour
it only under config_tradier.PARITY_VEC_EXACT_MODE=True (tradier_vec_exact); the native stock path does not.
"""
from __future__ import annotations

import copy

AUGMENT_SOURCES = ("LADDER", "SIGNAL", "FAST_RISER", "AT_LOSS", "HAIKU", "DD_BOUNCE", "STOCK_LIVE")
AUGMENT_GATE_KEYS = (
    "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED",
    "AUG_GAIN_GATE_LIVE_TWIN_ENABLED",
    "HTF_GATE_APPLY_TO_AUGMENT",
    "HTF_DIRECTION_GATE_ENABLED",
    "COUNTER_TREND_ADD_BLOCK_ENABLED",
    "STOCKS_OPENING_BUFFER_ENTRY_ENABLED",
    "AUGMENT_ONLY_WHEN_PROFITABLE",
    "AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER",
)
# open-gate arrays captured at the end of the simulate_one prelude (None = gate inactive). neutral = value of an inactive gate.
GATE_ARRAYS = {
    "_strict_open_block": False,
    "_ec_choke_block": False,
    "_atf_entry_block": False,
    "_qta_ct_block": False,
    "_w2_gr_block": False,
    "_w2_dc4_block": False,
    "_bt_veto": False,
    "_dg_block": False,
    "_rag_pass": True,
}


def _ablation_members():
    try:
        import vec_decisions.filter_ablation_groups as _g

        s = set()
        for v in _g.GROUPS.values():
            s.update(v)
        return s
    except Exception:
        return set()


FILTER_KEYS = frozenset(_ablation_members())


def classify(path_id: str, aliases=None) -> str:
    p = str(path_id or "").strip().upper()
    if not p:
        return ""
    if p == "REENTRY":
        return "REENTRY"
    if p == "AUGMENT" or p.startswith("AUGMENT_"):
        return "AUGMENT"
    return "ENTRY"


def parse(raw) -> dict:
    """{'ENTRY': {path: [filters]}, 'REENTRY': [filters], 'AUGMENT': {src|'*': set(filters)}}; empty dict when nothing applies."""
    if not raw or not isinstance(raw, dict):
        return {}
    out = {"ENTRY": {}, "REENTRY": [], "AUGMENT": {}}
    for path, flts in raw.items():
        if isinstance(flts, str):
            flts = [x.strip() for x in flts.split(",") if x.strip()]
        try:
            flts = [str(f).strip() for f in (flts or []) if str(f).strip()]
        except Exception:
            continue
        kind = classify(path)
        pu = str(path).strip()
        if kind == "ENTRY":
            fl = [f for f in flts if f in FILTER_KEYS]
            if fl:
                out["ENTRY"].setdefault(pu, [])
                out["ENTRY"][pu] += [f for f in fl if f not in out["ENTRY"][pu]]
        elif kind == "REENTRY":
            out["REENTRY"] += [f for f in flts if f in FILTER_KEYS and f not in out["REENTRY"]]
        elif kind == "AUGMENT":
            src = pu.upper()[len("AUGMENT_"):] if pu.upper().startswith("AUGMENT_") else "*"
            fl = {f for f in flts if f in AUGMENT_GATE_KEYS}
            if fl:
                out["AUGMENT"].setdefault(src, set()).update(fl)
    if not out["ENTRY"] and not out["REENTRY"] and not out["AUGMENT"]:
        return {}
    return out


def validate(raw) -> list:
    """Human-readable list of ignored (path, filter) pairs — pilot/report use; the engine silently ignores them."""
    bad = []
    if not isinstance(raw, dict):
        return bad
    for path, flts in raw.items():
        kind = classify(path)
        for f in (flts if isinstance(flts, (list, tuple, set)) else str(flts).split(",")):
            f = str(f).strip()
            if not f:
                continue
            if kind in ("ENTRY", "REENTRY") and f not in FILTER_KEYS:
                bad.append((path, f, "not a filter-ablation group member (no off value / permit / bypass)"))
            if kind == "AUGMENT" and f not in AUGMENT_GATE_KEYS:
                bad.append((path, f, "not an augment choke-point gate key"))
    return bad


def off_value(current):
    if isinstance(current, bool):
        return False
    if isinstance(current, str):
        return "OFF"
    return None


def pass_b_cfg(cfg, filters, isolate: str = ""):
    """Shallow copy of cfg with `filters` forced off, exemptions cleared and the probe sink armed."""
    c = copy.copy(cfg)
    for f in filters:
        try:
            cur = getattr(c, f, None)
        except Exception:
            continue
        off = off_value(cur)
        if off is None or cur == off:
            continue
        try:
            setattr(c, f, off)
        except Exception:
            pass
    try:
        c.FILTER_EXEMPTIONS = {}
        c.ENTRY_ISOLATE_FAMILY = str(isolate or "")
        c._FX_PROBE_SINK = {}
    except Exception:
        return None
    return c


def merge(a, b, sel, n, neutral):
    """np.where(sel, b, a) with None = inactive gate (neutral everywhere)."""
    import numpy as np

    if a is None and b is None:
        return None
    A = np.full(n, neutral, dtype=bool) if a is None else np.asarray(a, dtype=bool)
    B = np.full(n, neutral, dtype=bool) if b is None else np.asarray(b, dtype=bool)
    return np.where(sel, B, A)


def aug_exempt(fx_aug: dict, active_sources, gate_key: str) -> bool:
    """True when any active augment source (or the '*' wildcard) is exempt from gate_key."""
    if not fx_aug:
        return False
    if gate_key in fx_aug.get("*", ()):
        return bool(active_sources)
    for s in active_sources:
        if gate_key in fx_aug.get(s, ()):
            return True
    return False
