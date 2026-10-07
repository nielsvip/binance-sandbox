"""Bulk chart-gen for published finals missing coherent charts (USER 2026-10-03 pos-gain go-live).

For the latest-mtime final per sym_side in SPREADSHEETS/V15_V16_CELL_BY_CELL:
  - skip when a same-basename .html newer than the xlsx already exists (chart current)
  - else load the C-set from {sym}_v14_progress.json cumulative_overrides, HONESTY-BOUND:
    md5(C-set) must equal the manifest overrides_md5 (when a manifest exists) AND
    progress final_gain must match the filename gain within 0.01. Mismatch -> SKIP with reason.
  - generate_hires(symside, C-set, 30, out_name=<xlsx basename>.html), copy charts_1Y -> CELL_BY_CELL.

Usage (S1 only): .venv/bin/python -u tools/v15_bulk_charts.py [--limit N] [--symside X]
"""
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CELL = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
PROG_DIRS = [ROOT / "data" / "reports" / "lifecycle_pilot", Path("/home/niels/v15_run25_20261002/progress"), ROOT / "data" / "reports" / "lifecycle_pilot_s2"]
CHARTS = ROOT / "data" / "reports" / "charts_1Y"

from tools.v15_final_naming import parse_final_matrix_name as _parse  # noqa: E402


def _ov_md5(ov) -> str:
    return hashlib.md5(json.dumps(ov or {}, sort_keys=True, default=str).encode()).hexdigest()


def _latest_finals():
    best = {}
    for p in CELL.glob("*_bh*_30d_matrix.xlsx"):
        d = _parse(p.name)
        if d is None:
            continue
        sym = d.get("symside") or p.name.split("_bh")[0]
        mt = p.stat().st_mtime
        if sym not in best or mt > best[sym][1]:
            best[sym] = (p, mt, d)
    return best


def main(argv):
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    only = argv[argv.index("--symside") + 1] if "--symside" in argv else None
    from tools.opt.hires_chart import generate_hires
    finals = _latest_finals()
    syms = sorted(finals)
    if only:
        syms = [s for s in syms if s == only]
    if limit:
        syms = syms[:limit]
    ok = skip = fail = 0
    t0 = time.time()
    for i, sym in enumerate(syms):
        p, _mt, d = finals[sym]
        html = p.with_suffix(".html")
        tag = f"[{i + 1}/{len(syms)}] {sym}"
        try:
            if html.exists() and html.stat().st_mtime >= p.stat().st_mtime:
                print(f"{tag} SKIP chart-current", flush=True)
                skip += 1
                continue
            man = CELL / p.name.replace(".xlsx", "_manifest.json")
            om = None
            if man.exists():
                try:
                    om = (json.loads(man.read_text()) or {}).get("overrides_md5")
                except Exception:
                    om = None
            ov = None
            _src = None
            _reasons = []
            for _pd in PROG_DIRS:
                pj = _pd / f"{sym}_v14_progress.json"
                if not pj.exists():
                    continue
                try:
                    j = json.loads(pj.read_text())
                except Exception as _je:
                    _reasons.append(f"{_pd.name}:unparseable")
                    continue
                _ov = j.get("cumulative_overrides") or {}
                if not _ov:
                    _reasons.append(f"{_pd.name}:empty-C-set")
                    continue
                fg = j.get("final_gain")
                if fg is None or abs(float(fg) - float(d["gain"])) > 0.01:
                    _reasons.append(f"{_pd.name}:gain-mismatch({fg})")
                    continue
                if om and _ov_md5(_ov) != om:
                    _reasons.append(f"{_pd.name}:cset-md5-mismatch")
                    continue
                ov, _src = _ov, str(pj)
                break
            if ov is None:
                print(f"{tag} SKIP no-honest-C-set {';'.join(_reasons) or 'no-progress-anywhere'}", flush=True)
                skip += 1
                continue
            out = generate_hires(sym, dict(ov), 30, out_name=html.name)
            src = CHARTS / html.name
            if src.exists():
                shutil.copy2(src, html)
            elif Path(out).exists():
                shutil.copy2(out, html)
            print(f"{tag} OK -> {html.name} ({time.time() - t0:.0f}s elapsed)", flush=True)
            ok += 1
        except Exception as e:
            print(f"{tag} FAIL {e}"[:300], flush=True)
            fail += 1
    print(f"[bulk-charts] done ok={ok} skip={skip} fail={fail} syms={len(syms)} elapsed={time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv)
