"""v15_final_sync_guard — ONE place that decides which CELL_BY_CELL files are immutable finals.

USER 2026-10-05: a finished bh/gain matrix (old cents format OR new int+trades
format) is pulled/pushed ONCE and never again — re-pulling the same name with a
fresh mtime ("recycling") makes it impossible to see progress. Its same-stem
zoomable chart (.html) + manifest (_manifest.json) travel with it, also immutable.
Live in-progress sheets ({SYM}_{SIDE}_30d_matrix.xlsx, no bh/gain) keep updating.

Shell pull/push scripts hardcode FINAL_GLOBS below as rsync include/exclude
patterns (two-pass: live files update normally, finals with --ignore-existing);
tests/test_v15_final_sync_guard.py enforces the shell literals stay in sync.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FINAL_XLSX_GLOB = "*_bh*_gain*_30d_matrix.xlsx"
FINAL_HTML_GLOB = "*_bh*_gain*_30d_matrix.html"
FINAL_MANIFEST_GLOB = "*_bh*_gain*_manifest.json"
FINAL_GLOBS = (FINAL_XLSX_GLOB, FINAL_HTML_GLOB, FINAL_MANIFEST_GLOB)

try:
    from tools.v15_final_naming import parse_final_matrix_name as _parse
except Exception:
    _parse = None


def is_final_xlsx(name: str) -> bool:
    if _parse is None:
        return False
    p = _parse(str(name).strip())
    return p is not None and p.get("ext") == "xlsx"


def is_final_chart(name: str) -> bool:
    if _parse is None:
        return False
    p = _parse(str(name).strip())
    return p is not None and p.get("ext") == "html"


def is_final_manifest(name: str) -> bool:
    n = str(name).strip()
    if not n.endswith("_manifest.json") or _parse is None:
        return False
    return _parse(n[: -len("_manifest.json")] + ".xlsx") is not None


def is_final(name: str) -> bool:
    return is_final_xlsx(name) or is_final_chart(name) or is_final_manifest(name)


def chart_name_for_xlsx(xlsx_name: str) -> str:
    return str(xlsx_name).strip()[:-len(".xlsx")] + ".html"


def manifest_name_for_xlsx(xlsx_name: str) -> str:
    return str(xlsx_name).strip()[:-len(".xlsx")] + "_manifest.json"


def missing_charts(cell_dir: str | Path) -> list[str]:
    d = Path(cell_dir)
    if not d.is_dir():
        return []
    missing = []
    for p in sorted(d.glob("*_bh*_gain*_30d_matrix.xlsx")):
        if not is_final_xlsx(p.name):
            continue
        if not (d / chart_name_for_xlsx(p.name)).exists():
            missing.append(p.name)
    return missing


def audit(cell_dir: str | Path) -> dict:
    d = Path(cell_dir)
    finals = sorted(p.name for p in d.glob("*_bh*_gain*_30d_matrix.xlsx")) if d.is_dir() else []
    finals = [n for n in finals if is_final_xlsx(n)]
    charts = sum(1 for n in finals if (d / chart_name_for_xlsx(n)).exists())
    mans = sum(1 for n in finals if (d / manifest_name_for_xlsx(n)).exists())
    return {"finals": len(finals), "charts": charts, "manifests": mans, "missing_charts": len(finals) - charts}


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--audit":
        target = Path(argv[2]) if len(argv) >= 3 else ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
        a = audit(target)
        print(f"[final-guard] {target}: finals={a['finals']} charts={a['charts']} manifests={a['manifests']} missing_charts={a['missing_charts']}")
        for n in missing_charts(target)[:20]:
            print(f"[final-guard] missing-chart {n}")
        if a["missing_charts"] > 20:
            print(f"[final-guard] ... and {a['missing_charts'] - 20} more")
        return 0
    if len(argv) >= 2 and argv[1] == "--globs":
        for g in FINAL_GLOBS:
            print(g)
        return 0
    print("usage: v15_final_sync_guard.py --audit [dir] | --globs")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
