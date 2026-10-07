"""Fast xlsx source-of-truth auditor (USER 2026-10-03: xlsx files are the source of truth).

Raw-XML counting (no openpyxl slowness/flakes): C/E/F/Y per switch tab.
--verify: re-check a published xlsx against its _manifest.json sidecar
(file md5, F/C/E recounts, filename gain/bh vs manifest metrics).
--chart: verify a *_30d_matrix.html header gain/BH/trades vs its filename.

Usage:
  sheet_audit.py SPREADSHEETS/V15_V16_CELL_BY_CELL/<file>.xlsx [--verify]
  sheet_audit.py SPREADSHEETS/V15_V16_CELL_BY_CELL/<file>.html --chart
"""
import json
import re
import sys
import zipfile
from pathlib import Path

SWITCH = ('STDEV_SLOPE_SIZING', 'ENTRY_REVERSAL_BOUNCE', 'ENTRY_BREAKOUT_CHANNEL', 'ENTRY_CONFIRMATION_GATES', 'EXIT_STRUCTURAL', 'EXIT_VELOCITY', 'REENTRY_WINDOWED', 'REENTRY_ADAPTIVE', 'AUGMENT_TREND', 'AUGMENT_RISK_SIZING', 'REDUCE_PROFIT_LOCK', 'REDUCE_SIGNAL_RATER', 'GLOBAL_RISK_GATES')


def _col_idx(col):
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch) - 64)
    return idx


def audit_counts(xlsx_path):
    """{per_tab: {tab: {C,E,F,Y}}, total: {C,E,F,Y}} — numerics only (shared-string text excluded except C strings)."""
    z = zipfile.ZipFile(str(xlsx_path))
    wb = z.read('xl/workbook.xml').decode()
    pairs = re.findall(r'<sheet[^>]*?name="([^"]+)"[^>]*?r:id="(rId\d+)"', wb)
    rels = z.read('xl/_rels/workbook.xml.rels').decode()
    rid2t = {rid: tgt for tgt, rid in re.findall(r'Target="/xl/(worksheets/sheet\d+.xml)" Id="(rId\d+)"', rels)}
    per_tab, tot = {}, {"C": 0, "E": 0, "F": 0, "Y": 0}
    for name, rid in pairs:
        if name not in SWITCH:
            continue
        tgt = rid2t.get(rid)
        if not tgt:
            continue
        x = z.read('xl/' + tgt).decode()
        d = {"C": 0, "E": 0, "F": 0, "Y": 0}
        for m in re.finditer(r'<c r="([A-Z]+)(\d+)"[^>]*><v>([^<]*)</v>', x):
            col, row, v = m.group(1), int(m.group(2)), m.group(3)
            if row < 3:
                continue
            try:
                float(v)
                is_num = True
            except Exception:
                is_num = False
            if col == 'C':
                if v:
                    d["C"] += 1
            elif col == 'E' and is_num:
                d["E"] += 1
            elif col == 'F' and is_num:
                d["F"] += 1
            elif is_num and 12 <= _col_idx(col) <= 61:
                d["Y"] += 1
        for m in re.finditer(r'<c r="C(\d+)"[^>]*t="s"[^>]*><v>(\d+)</v>', x):
            if int(m.group(1)) >= 3:
                d["C"] += 1
        for m in re.finditer(r'<c r="C(\d+)"[^>]*><is><t>([^<]*)</t>', x):
            if int(m.group(1)) >= 3 and m.group(2):
                d["C"] += 1
        per_tab[name] = d
        for k in tot:
            tot[k] += d[k]
    return {"per_tab": per_tab, "total": tot}


def _parse_bh_gain(name):
    m = re.search(r"_bh(m?)([\d]+)p([\d]+)_gain(m?)([\d]+)p([\d]+)", name)
    if not m:
        return None, None
    bh = (int(m.group(2)) + int(m.group(3)) / 100) * (-1 if m.group(1) == "m" else 1)
    g = (int(m.group(5)) + int(m.group(6)) / 100) * (-1 if m.group(4) == "m" else 1)
    return bh, g


def verify(xlsx_path):
    """(ok, reasons, detail) — xlsx vs its _manifest.json sidecar + filename coherence."""
    reasons, p = [], Path(xlsx_path)
    man_p = p.parent / (p.name.replace(".xlsx", "_manifest.json"))
    if not man_p.exists():
        return False, ["no manifest sidecar (pre-manifest publish or revoked)"], {}
    man = json.loads(man_p.read_text())
    import hashlib
    h = hashlib.md5()
    with open(str(p), "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    if h.hexdigest() != man.get("file_md5"):
        reasons.append(f"file md5 {h.hexdigest()[:12]} != manifest {str(man.get('file_md5'))[:12]} — FILE CHANGED SINCE PUBLISH")
    try:
        counts = audit_counts(p)["total"]
    except Exception as e:
        return False, [f"unreadable xlsx: {e}"], {}
    for k in ("F", "C", "E"):
        if counts[k] != int((man.get("counts") or {}).get(k, -1)):
            reasons.append(f"count {k} {counts[k]} != manifest {(man.get('counts') or {}).get(k)}")
    bh_n, g_n = _parse_bh_gain(p.name)
    m = man.get("metrics") or {}
    if bh_n is not None and m.get("bh") is not None and abs(bh_n - float(m["bh"])) > 0.015:
        reasons.append(f"filename BH {bh_n} != manifest {m['bh']}")
    if g_n is not None and m.get("gain_pct") is not None and abs(g_n - float(m["gain_pct"])) > 0.015:
        reasons.append(f"filename gain {g_n} != manifest {m['gain_pct']}")
    return (len(reasons) == 0), reasons, {"counts": counts, "manifest": man.get("counts"), "file_md5": h.hexdigest()[:12]}


def verify_chart(html_path):
    """(ok, reasons) — chart header gain/BH/trades vs filename (catches rename-without-regenerate lies)."""
    reasons, p = [], Path(html_path)
    h = p.read_text()
    mg = re.search(r"gain (-?[\d.]+)%", h)
    mb = re.search(r"BH (-?[\d.]+)%", h)
    mt = re.search(r"trades ([\d]+)", h)
    if not mg:
        return False, ["no gain in header"], {}
    bh_n, g_n = _parse_bh_gain(p.name)
    det = {"header_gain": float(mg.group(1)), "header_bh": float(mb.group(1)) if mb else None, "header_trades": int(mt.group(1)) if mt else None}
    if g_n is not None and abs(det["header_gain"] - g_n) > 0.02:
        reasons.append(f"header gain {det['header_gain']} != filename {g_n}")
    if bh_n is not None and det["header_bh"] is not None and abs(det["header_bh"] - bh_n) > 0.02:
        reasons.append(f"header BH {det['header_bh']} != filename {bh_n}")
    return (len(reasons) == 0), reasons, det


def main(argv):
    if len(argv) < 2:
        print("usage: sheet_audit.py <xlsx|html> [--verify|--chart] [--json]")
        return 2
    p = Path(argv[1])
    as_json = "--json" in argv
    if p.suffix == ".html" or "--chart" in argv:
        ok, reasons, det = verify_chart(p)
        out = {"file": p.name, "ok": ok, "reasons": reasons, **det}
    elif "--verify" in argv:
        ok, reasons, det = verify(p)
        out = {"file": p.name, "ok": ok, "reasons": reasons, **det}
    else:
        out = {"file": p.name, **audit_counts(p)}
    if as_json:
        print(json.dumps(out, indent=1, default=str))
    elif p.suffix == ".html" or "--chart" in argv or "--verify" in argv:
        print(("PASS " if out["ok"] else "FAIL ") + out["file"])
        for r in out["reasons"]:
            print("  -", r)
        print("  detail:", json.dumps({k: v for k, v in out.items() if k not in ("file", "ok", "reasons")}, default=str)[:300])
    else:
        for tab, d in out["per_tab"].items():
            print(f"  {tab[:26]:26s} C={d['C']:5d} E={d['E']:5d} F={d['F']:5d} Y={d['Y']:6d}")
        print("TOTAL", out["total"])
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
