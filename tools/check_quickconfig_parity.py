#!/usr/bin/env python3
"""check_quickconfig_parity — BACKTEST_BIBLE §17 guard (2026-09-29).

§17.2 instance audit: QuickConfig() vs Config() (crypto) and
QuickConfig()+apply_tradier_defaults() vs TradierConfig() (stocks), simple-typed
shared fields, floats to 9dp. Exclusions (§17.3, curated + verified) load from
data/quickconfig_sync_exclusions.json. Exit 1 + loud list on any non-excluded
divergence. Run: python3 tools/check_quickconfig_parity.py [--json]
"""
from __future__ import annotations
import dataclasses as dc
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import v12_quick_engine as V
    from config import Config as CC
    import config_tradier as CTM
    ex = json.loads((ROOT / "data" / "quickconfig_sync_exclusions.json").read_text())
    infra = re.compile(ex["infra_regex"])
    # 2026-09-29: the tightened exclusions file dropped ftf_regex (FILTER_TF fields are now synced) and documents
    # capital/cost normalization keys under capital_cost_flags — honour the file as written
    ftf = re.compile(ex["ftf_regex"]) if ex.get("ftf_regex") else None
    names = set(ex["names"]) | set(ex.get("capital_cost_flags", {}))
    prefixes = tuple(ex["prefix"])

    def excluded(k):
        return bool(infra.search(k) or (ftf is not None and ftf.search(k)) or k in names or k.startswith(prefixes))

    def simple(x):
        return isinstance(x, (bool, int, float, str))

    def same(a, b):
        if isinstance(a, bool) or isinstance(b, bool):
            return bool(a) == bool(b)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
        return str(a) == str(b)

    qc = V.QuickConfig()
    qt = V.QuickConfig(); qt.apply_tradier_defaults()
    cf = CC()
    ct = CTM.TradierConfig()
    pairs = [("crypto", qc, cf), ("tradier", qt, ct)]
    bad = []
    n_cmp = 0
    for tag, q, c in pairs:
        qv = {f.name: getattr(q, f.name) for f in dc.fields(q)}
        cv = {f.name: getattr(c, f.name) for f in dc.fields(c)}
        for k in qv:
            if k not in cv or excluded(k) or not (simple(qv[k]) and simple(cv[k])):
                continue
            n_cmp += 1
            if not same(qv[k], cv[k]):
                bad.append({"venue": tag, "name": k, "quickconfig": repr(qv[k]), "live": repr(cv[k])})
    if "--json" in sys.argv:
        print(json.dumps({"comparable": n_cmp, "divergent": bad}, indent=1))
    if bad:
        print(f"QUICKCONFIG PARITY FAIL (Bible §17): {len(bad)} divergent of {n_cmp} comparable:", file=sys.stderr)
        for b in bad[:50]:
            print(f"  [{b['venue']}] {b['name']}: vec {b['quickconfig']} != live {b['live']}", file=sys.stderr)
        if len(bad) > 50:
            print(f"  ... +{len(bad)-50} more", file=sys.stderr)
        sys.exit(1)
    print(f"quickconfig parity OK (Bible §17.2 instance audit; {n_cmp} comparisons; exclusions per data/quickconfig_sync_exclusions.json)")
    sys.exit(0)


if __name__ == "__main__":
    main()
