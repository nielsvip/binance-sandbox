"""Extract pilot-identical template bold defaults for the 4 cat_sides (Mac).

Uses v15_pilot.template_bold_defaults with pilot-identical arguments so the
S5 vec leg replicates the pilot base (bolds under cumulative_overrides).
Writes data/reports/s5_verify_20261003/bolds.json. Read-only over inputs.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUTDIR = os.path.join(ROOT, "data", "reports", "s5_verify_20261003")
CATS = {"CRYPTO_LONG": ("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx", "XTZUSDT_LONG"), "CRYPTO_SHORT": ("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx", "ADAUSDC_SHORT"), "STOCKS_LONG": ("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx", "INTC_LONG"), "STOCKS_SHORT": ("SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx", "AXTI_SHORT")}


def main():
    import v15_pilot as P
    from tools.build_cat_side_defaults_4 import venue_values as _venue_values
    out = {"cats": {}, "violations": {}, "untrusted": {}, "non_json": {}}
    for cat, (tpl, sym) in CATS.items():
        defaults = P.get_defaults_for_symside(sym)
        try:
            _truth = _venue_values(P.map_key_for_symside(sym).startswith("STOCKS"))[0]
            try:
                import per_sym_store as _pss
                _promos = _pss.get_cat_side_promotions()
                _promoted = set((_promos.get(P.map_key_for_symside(sym)) or {}).keys()) if _promos else set()
            except Exception:
                _promoted = set((json.loads(open(os.path.join(ROOT, "data", "cat_side_promotions.json")).read()).get(P.map_key_for_symside(sym)) or {}).keys())
        except Exception as e:
            print(f"[warn] {cat} truth load failed ({e}) — all bolds trusted")
            _truth, _promoted = None, set()
        tpl_defaults, tpl_bad = P.template_bold_defaults(os.path.join(ROOT, tpl), defaults, _truth, _promoted)
        out["violations"][cat] = tpl_bad
        out["untrusted"][cat] = [[t, s, str(v), str(tv)] for (t, s, v, tv) in list(P.UNTRUSTED_BOLD)]
        nj = []
        clean = {}
        for k, v in tpl_defaults.items():
            try:
                json.dumps(v)
                clean[k] = v
            except (TypeError, ValueError):
                clean[k] = str(v)
                nj.append(k)
        out["non_json"][cat] = nj
        out["cats"][cat] = clean
        print(f"{cat}: bolds={len(clean)} violations={len(tpl_bad)} untrusted={len(out['untrusted'][cat])} non_json={len(nj)}")
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, "bolds.json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f)
    os.replace(tmp, p)
    print("->", p)


if __name__ == "__main__":
    sys.exit(main())
