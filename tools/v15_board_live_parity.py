#!/usr/bin/env python3
"""v15_board_live_parity — does LIVE resolve every key of a sym_side's published board to the board's value? (USER 2026-10-13)

Resolution = live's own chain: per-sym SQLite override (per_sym_store.get_overrides) > cat_side default
(cat_side_defaults.get_for, the per_sym_settings layer) > venue global (config.Config / config_tradier.TradierConfig).
    python tools/v15_board_live_parity.py --selection data/published_selection.json                 # live stores now
    python tools/v15_board_live_parity.py --selection SEL --projection data/daily_chain/persym_golive_DATE.json
      projection: the per-sym layer is replaced by the go-live report's would_register (PASS rows only) = state after --apply.
Keys with no live reader (absent from per-sym, cat_side and venue global) are counted separately (not a live divergence).
GLOBAL_ONLY keys (switch_parity) cannot be per-sym: a board value != the global is an irreducible divergence and is listed.
Read-only.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "tools"))
_MISS = object()


def norm(v):
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        if s.lower() in ("none", "off"):
            return "OFF"
        try:
            return round(float(s), 6)
        except Exception:
            return s
    if v is None:
        return "OFF"
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", required=True)
    ap.add_argument("--projection")
    ap.add_argument("--out")
    a = ap.parse_args()
    import cat_side_defaults as csd
    import config
    import config_tradier
    import per_sym_store as pss
    import switch_parity as sp
    import v15_persym_golive as gl

    ccfg, tcfg = config.Config(), config_tradier.TradierConfig()
    doc = json.loads(Path(a.selection).read_text())
    sel = doc.get("sym_sides", doc)
    proj = None
    if a.projection:
        rep = json.loads(Path(a.projection).read_text())
        proj = {ss: (r.get("would_register") or {}) for ss, r in (rep.get("sym_sides") or {}).items() if r.get("result") == "PASS"}
    rows, keyc = {}, collections.Counter()
    for ss, r in sorted(sel.items()):
        if r.get("refuse") or (proj is not None and ss not in proj):
            continue
        sym, side = ss.rsplit("_", 1)
        crypto = sp.is_crypto_sym(sym)
        ov = gl.sanitize(dict(r.get("overrides") or {}), {})
        pinned = proj[ss] if proj is not None else (pss.get_overrides(ss) or {})
        diff, noreader, glob_only = [], 0, []
        for k, v in ov.items():
            if k in pinned:
                lv = pinned[k]
            else:
                lv = csd.get_for(k, sym, side, _MISS, venue="crypto" if crypto else "stocks")
                if lv is _MISS:
                    lv = getattr(ccfg if crypto else tcfg, k, _MISS)
            if lv is _MISS:
                noreader += 1
                continue
            if norm(lv) != norm(v):
                (glob_only if k in sp.GLOBAL_ONLY_KEYS else diff).append(k)
                keyc[k] += 1
        rows[ss] = {"n_board": len(ov), "n_pinned": len(pinned), "n_diff": len(diff), "diff": diff[:12], "global_only_diff": glob_only, "no_live_reader": noreader}
    zero = sum(1 for x in rows.values() if x["n_diff"] == 0)
    summ = {"mode": "projection" if proj is not None else "live", "sym_sides": len(rows), "zero_diff": zero, "with_diff": len(rows) - zero, "top_diff_keys": keyc.most_common(15)}
    print(json.dumps(summ, indent=1))
    for ss, x in sorted(rows.items(), key=lambda kv: -kv[1]["n_diff"])[:10]:
        if x["n_diff"]:
            print(f"  {ss}: {x['n_diff']} diffs {x['diff']}")
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summ, "rows": rows}, indent=1, default=str))
    return 0 if summ["with_diff"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
