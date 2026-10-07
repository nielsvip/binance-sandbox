#!/usr/bin/env python3
"""live_parity_inventory — Agent D (2026-10-01): which indicator keys / NPZ arrays / switches exist on the VECTOR side but have no producer
or consumer in LIVE, and the reverse. Read-only static analysis (ast). Output: data/live_parity/inventory.json (+ per-class lists).

Key normalisation: f-string holes {..} and timeframe tokens (3m,5m,15m,1h,4h,D,...) -> '*' so wt1_{tf} == wt1_15m == wt1_*.
Classes per key:
  LIVE_MISSING_PRODUCER  live reads it (or vec reads it and the NPZ has it) but no live code writes that key into the indicator dict
  VEC_KEY_NOT_IN_NPZ     vec reads a key the NPZ precompute never writes (vec side dead; cross-reference for Agent C)
  OK                     produced by live AND present in NPZ (value semantics still need SEMANTIC check — see report)
Switch level (config fields): vec reads cfg.X / getattr(cfg,'X') but live never mentions X (LIVE_MISSING_CONSUMER); live mentions X but vec never does
(VEC_MISSING_TWIN, Agent C's list)."""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "live_parity"
LIVE_PRODUCERS = ["ez_indicators.py", "tradier_indicators.py", "ez_prices.py", "ez_mark_prices.py", "ez_prices_ws.py", "ez_regime.py", "tradier_klines.py", "wt_dc_hierarchy.py", "ez_manage.py", "tradier_manage.py", "ez_positions_quick.py"]
LIVE_CONSUMERS = ["ez_manage.py", "tradier_manage.py", "ez_positions_quick.py", "wt_dc_delta.py", "hedge_decisions.py", "mtf_exit_timing.py"] + [p.name for p in ROOT.glob("mtf_*.py")]
VEC = ["v12_quick_engine.py", "tools/opt/evaluate_v12.py"] + [str(p.relative_to(ROOT)) for p in (ROOT / "vec_decisions").glob("*.py")]
NPZ_PRODUCER = ["backtest_v8_precompute.py"]
TF = r"(?:1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|D|W|1w)"
KEYRE = re.compile(r"^[a-z][a-z0-9_*]{3,}$")


def norm(s: str) -> str:
    s = re.sub(r"\{[^}]*\}", "*", s)
    s = re.sub(rf"(?<=_)({TF})(?=_|$)", "*", s)
    s = re.sub(r"\*(_\*)+", "*", s)
    if s.endswith("_"):
        s += "*"  # "bb_upper_" + tf concatenation
    return s


def fstr_to_pat(node):
    parts = []
    for v in node.values:
        parts.append(v.value if isinstance(v, ast.Constant) else "{}")
    return "".join(parts)


def literal_keys(tree, store_only=False):
    keys = {}
    for n in ast.walk(tree):
        cand = []
        if store_only:
            if isinstance(n, ast.Subscript) and isinstance(getattr(n, "ctx", None), ast.Store):
                cand.append(n.slice)
            elif isinstance(n, ast.Dict):
                cand.extend(k for k in n.keys if k is not None)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("setdefault",) and n.args:
                cand.append(n.args[0])
        else:
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                cand.append(n)
            elif isinstance(n, ast.JoinedStr):
                cand.append(n)
        for c in cand:
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                s = c.value
            elif isinstance(c, ast.JoinedStr):
                s = fstr_to_pat(c)
            else:
                continue
            k = norm(s)
            if KEYRE.match(k):
                keys.setdefault(k, getattr(c, "lineno", 0))
    return keys


def scan(files, store_only=False):
    out = {}
    for f in files:
        p = ROOT / f
        if not p.exists():
            continue
        try:
            tree = ast.parse(p.read_text(errors="ignore"))
        except Exception:
            continue
        for k, ln in literal_keys(tree, store_only).items():
            out.setdefault(k, []).append(f"{f}:{ln}")
    return out


def cfg_names(files):
    """UPPER_CASE config field mentions (strings / attribute access) per file set."""
    names = {}
    for f in files:
        p = ROOT / f
        if not p.exists():
            continue
        txt = p.read_text(errors="ignore")
        for m in re.finditer(r"\b([A-Z][A-Z0-9_]{5,})\b", txt):
            names.setdefault(m.group(1), set()).add(f)
    return names


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    npz = scan(NPZ_PRODUCER, store_only=True)
    live_prod = scan(LIVE_PRODUCERS, store_only=True)
    live_cons = scan(LIVE_CONSUMERS, store_only=False)
    vec_ref = scan(VEC, store_only=False)
    namespace = set(npz) | set(live_prod)
    # keys that look like indicator keys: must appear in NPZ or live producers somewhere, else they are config/other strings
    live_cons_ind = {k: v for k, v in live_cons.items() if k in namespace or any(k.startswith(x) for x in ("wt_", "ha_", "dc_", "bb_", "k_", "d_", "close_", "high_", "low_"))}
    vec_ref_ind = {k: v for k, v in vec_ref.items() if k in namespace}
    res = {"counts": {}, "LIVE_CONSUMED_NOT_PRODUCED": {}, "VEC_READS_NPZ_KEY_LIVE_DOES_NOT_PRODUCE": {}, "VEC_KEY_NOT_IN_NPZ": {}}
    for k, locs in sorted(live_cons_ind.items()):
        if k not in live_prod and k not in ("wt1_*",):
            res["LIVE_CONSUMED_NOT_PRODUCED"][k] = {"consumers": locs[:3], "in_npz": k in npz}
    for k, locs in sorted(vec_ref_ind.items()):
        if k in npz and k not in live_prod:
            res["VEC_READS_NPZ_KEY_LIVE_DOES_NOT_PRODUCE"][k] = {"vec": locs[:3], "live_consumers": live_cons.get(k, [])[:2]}
    for k, locs in sorted(vec_ref.items()):
        if k not in npz and k in live_prod:
            res["VEC_KEY_NOT_IN_NPZ"][k] = {"vec": locs[:3], "live_producers": live_prod[k][:2]}
    # switch level
    live_cfg = cfg_names(["ez_manage.py", "tradier_manage.py", "ez_positions_quick.py", "wt_dc_delta.py", "hedge_decisions.py"] + [p.name for p in ROOT.glob("mtf_*.py")])
    vec_cfg = cfg_names(VEC)
    qc_fields = set(re.findall(r"^\s+([A-Z][A-Z0-9_]{5,})\s*[:=]", (ROOT / "v12_quick_engine.py").read_text(errors="ignore"), re.M))
    res["VEC_SWITCH_NO_LIVE_MENTION"] = sorted(k for k in vec_cfg if k in qc_fields and k not in live_cfg)
    res["LIVE_SWITCH_NO_VEC_MENTION_count"] = len([k for k in live_cfg if k not in vec_cfg])
    res["counts"] = {k: (len(v) if hasattr(v, "__len__") else v) for k, v in res.items() if k != "counts"}
    (OUT / "inventory.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res["counts"], indent=1))


if __name__ == "__main__":
    main()
