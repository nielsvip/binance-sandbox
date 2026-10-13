#!/usr/bin/env python3
"""v17_priors — loader for data/causal/v17_priors.json (mined by tools/v17_priors_mine.py +
tools/v17_priors_build.py from eval_cache_v1.db + causal_learner.db; real engine evals only).

    from tools import v17_priors
    p = v17_priors.load("CRYPTO_LONG")      # {"single", "blocks", "synergy", "anti_synergy", "deprioritise", "learner_conflicts"}
    v17_priors.single("CRYPTO_LONG", "WT_DIV_ENTRY_GATE_ENABLED=False")   # stats dict or None
    v17_priors.p_eval("CRYPTO_LONG", "MOM3_FILTER_TF=D")                 # 1.0 at top pos_sym .. 1/200 at pos_sym 0
    v17_priors.exemptions_for("CRYPTO_LONG", "BB_SQUEEZE_ENTRY_ENABLED")  # block rows carrying a real FILTER_EXEMPTIONS dict

Fail-open: a missing/corrupt file returns empty structures (callers then evaluate everything).
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = Path(os.environ.get("V17_PRIORS_PATH") or (ROOT / "data" / "causal" / "v17_priors.json"))
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
P_MIN = 1.0 / 200.0
EMPTY = {"single": {}, "blocks": [], "synergy": [], "anti_synergy": [], "deprioritise": [], "learner_conflicts": []}
_cache = {}


def _read(path):
    path = Path(path or DEFAULT_PATH)
    try:
        st = path.stat()
        memo = (str(path), st.st_mtime_ns, st.st_size)
        if _cache.get("memo") == memo:
            return _cache["doc"]
        doc = json.loads(path.read_text())
        _cache.update(memo=memo, doc=doc, norm={})
        return doc
    except Exception:
        return {}


def meta(path=None):
    return _read(path).get("meta") or {}


def load(cat_side, path=None):
    doc = _read(path)
    d = doc.get(str(cat_side).upper())
    if not isinstance(d, dict):
        return {k: (v.copy() if hasattr(v, "copy") else v) for k, v in EMPTY.items()}
    out = dict(EMPTY)
    out.update(d)
    return out


def norm_label(label):
    key, _, val = str(label).partition("=")
    v = val.strip()
    if v in ("True", "true"):
        return f"{key}=True"
    if v in ("False", "false"):
        return f"{key}=False"
    try:
        return f"{key}={float(v):g}"
    except Exception:
        return f"{key}={v}"


def single(cat_side, label, path=None):
    s = load(cat_side, path)["single"]
    if label in s:
        return s[label]
    norm = _cache.setdefault("norm", {})
    nk = (str(cat_side).upper(), id(s))
    idx = norm.get(nk)
    if idx is None:
        idx = {}
        for k, v in s.items():
            idx.setdefault(norm_label(k), v)
        norm[nk] = idx
    return idx.get(norm_label(label))


def top_pos(cat_side, path=None):
    s = load(cat_side, path)["single"]
    return max((v.get("pos", 0) for v in s.values()), default=0)


def p_eval(cat_side, label, path=None, p_min=P_MIN, unknown=1.0):
    """Sampling probability for a yellow/switch cell: 1.0 at the cat_side's top pos_sym, log-linear
    down to p_min (1/200) at pos_sym 0. Unknown keys fail-open to `unknown` (1.0)."""
    st = single(cat_side, label, path)
    if not st:
        return unknown
    top = top_pos(cat_side, path)
    if top <= 0:
        return unknown
    frac = min(1.0, max(0.0, st.get("pos", 0) / float(top)))
    return float(p_min ** (1.0 - frac))


def blocks_for(cat_side, switch_key, path=None):
    return [b for b in load(cat_side, path)["blocks"] if b.get("switch_key") == switch_key or b.get("switch") == switch_key]


def exemptions_for(cat_side, switch_key, path=None):
    return [b for b in blocks_for(cat_side, switch_key, path) if b.get("exemption_override")]


def is_deprioritised(cat_side, label, path=None):
    st = single(cat_side, label, path)
    return bool(st) and st.get("verdict") in ("DEAD", "USELESS", "HARMFUL")


if __name__ == "__main__":
    m = meta()
    print(json.dumps({"rows_total": m.get("rows_total"), "generated_utc": m.get("generated_utc")}))
    for cs in CAT_SIDES:
        p = load(cs)
        print(cs, len(p["single"]), "single", len(p["blocks"]), "blocks", len(p["synergy"]), "synergy", len(p["deprioritise"]), "deprioritise")
