#!/usr/bin/env python
"""compare_old_vs_v15.py — recurring report comparing the OLD greedy system vs the NEW v15 system.

OLD: SPREADSHEETS/DC64_GREEDY/{SYM_SIDE}_bh..._gain{FINAL}_delta..._matrix.xlsx  (final greedy gain).
V15: SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM_SIDE}_*matrix.xlsx  (final cumulative E on last filled row)
     — read best-effort; v15 sheets may not exist yet while the mega sweep runs.
Writes data/reports/OLD_vs_V15_report.md: per cat_side summary + per sym_side old vs v15 gain +
which system wins, and flags overfit status from data/reports/dc64_promotions.json if present.

Usage: python tools/compare_old_vs_v15.py
"""
import os, re, glob, json, datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = os.path.join(ROOT, "SPREADSHEETS", "DC64_GREEDY")
V15 = os.path.join(ROOT, "SPREADSHEETS", "V15_V16_CELL_BY_CELL")
PROMO = os.path.join(ROOT, "data", "reports", "dc64_promotions.json")
OUT = os.path.join(ROOT, "data", "reports", "OLD_vs_V15_report.md")


def _num(s):
    m = re.match(r'(m?)([0-9p]+)$', s)
    if not m:
        return None
    v = float(m.group(2).replace("p", "."))
    return -v if m.group(1) == "m" else v


def _cat(ss):
    base = ss[:-5] if ss.endswith("_LONG") else ss[:-6]
    isc = base.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD"))
    return ("CRYPTO" if isc else "STOCKS") + ("_LONG" if ss.endswith("_LONG") else "_SHORT")


def old_results():
    out = {}
    for f in glob.glob(os.path.join(OLD, "*_matrix.xlsx")):
        b = os.path.basename(f); ss = b.split("_bh")[0]
        mb = re.search(r'_bh(m?[0-9p]+)_', b); mg = re.search(r'_gain(m?[0-9p]+)_', b); md = re.search(r'_delta(m?[0-9p]+)_', b)
        out[ss] = {"bh": _num(mb.group(1)) if mb else None, "gain": _num(mg.group(1)) if mg else None,
                   "delta": _num(md.group(1)) if md else None}
    return out


def v15_results():
    out = {}
    if not os.path.isdir(V15):
        return out
    try:
        import openpyxl
    except Exception:
        return out
    for f in glob.glob(os.path.join(V15, "*_matrix.xlsx")):
        ss = os.path.basename(f).split("_bh")[0].replace("_30d_matrix.xlsx", "").split("_30d")[0]
        try:
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            # final cumulative gain = last numeric E across switch sheets
            best = None
            for sh in wb.sheetnames:
                if sh in ("LEGEND_FILTERS", "INSTRUCTIONS", "FILTERS_EXPLAINED", "INSTRUCTIONS_V2",
                          "Results_Deltas", "FILTER_DICTIONARY_V2", "Results_30d_Deltas") or "BASELINE" in sh:
                    continue
                ws = wb[sh]
                for r in range(3, min(ws.max_row, 400) + 1):
                    v = ws.cell(r, 5).value
                    if isinstance(v, (int, float)):
                        best = v if best is None else max(best, v)
            out[ss] = {"gain": best}
            wb.close()
        except Exception:
            pass
    return out


def main():
    old = old_results(); v15 = v15_results()
    promo = {}
    if os.path.exists(PROMO):
        try:
            promo = {r["sym_side"]: r for r in json.load(open(PROMO)).get("results", [])}
        except Exception:
            pass
    cats = {c: [] for c in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")}
    for ss, o in old.items():
        cats.setdefault(_cat(ss), []).append(ss)
    lines = [f"# OLD (dc64_greedy) vs NEW (v15) — {datetime.datetime.utcnow().isoformat()}Z",
             f"\nOLD sheets: {len(old)} | V15 sheets: {len(v15)} | verified(promotions.json): {sum(1 for r in promo.values() if r.get('pass'))} pass / {len(promo)}\n"]
    for c, syms in cats.items():
        if not syms:
            continue
        og = [old[s]["gain"] for s in syms if old[s].get("gain") is not None]
        od = [old[s]["delta"] for s in syms if old[s].get("delta") is not None]
        lines.append(f"\n## {c} — {len(syms)} sym_sides")
        if og:
            lines.append(f"- OLD: avg final gain {sum(og)/len(og):.2f}%, avg Δ vs baseline {sum(od)/len(od):+.2f}pp, best {max(og):.2f}%")
        v15c = [v15[s]["gain"] for s in syms if s in v15 and v15[s].get("gain") is not None]
        lines.append(f"- V15: {len(v15c)} sheets ready" + (f", avg final gain {sum(v15c)/len(v15c):.2f}%" if v15c else " (mega sweep still running / not pulled)"))
        # per-sym table (top 10 by old delta)
        rows = sorted(syms, key=lambda s: -(old[s].get("delta") or -1e9))[:10]
        lines.append("\n| sym_side | OLD gain | OLD Δ | V15 gain | winner | 365D verified |")
        lines.append("|---|---|---|---|---|---|")
        for s in rows:
            og1 = old[s].get("gain"); v1 = v15.get(s, {}).get("gain")
            win = "—"
            if og1 is not None and v1 is not None:
                win = "OLD" if og1 > v1 else ("V15" if v1 > og1 else "tie")
            pv = promo.get(s, {})
            ver = "PASS" if pv.get("pass") else (pv.get("reason", "—")[:24] if pv else "not verified")
            lines.append(f"| {s} | {og1 if og1 is None else round(og1,2)} | {old[s].get('delta')} | {v1 if v1 is None else round(v1,2)} | {win} | {ver} |")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write("\n".join(lines) + "\n")
    print(f"[compare] wrote {OUT} — OLD {len(old)} / V15 {len(v15)} sheets")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
