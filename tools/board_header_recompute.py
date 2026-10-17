#!/usr/bin/env python3
"""board_header_recompute — rewrite a board page's header from the page's OWN trades when its settings cannot be re-rendered.

USER 2026-10-17: "recalculate all headers of the html files ... so all are calculated exactly the same so we can compare and
trust them again". Pages whose set can be obtained are RE-RENDERED (tools.opt.hires_chart.generate_hires); this tool is the
fallback for pages whose set cannot be obtained (html-only copies, unreproducible sets, stale NPZ, 0 trades today).

Same rules as engine 4532fd52 + evaluator df0d3fa2 / tools/board_name_truth_guard.py page_truth (TRUTH_VERSION >= 6):
  G = (sum net pnl_dollars of TRADES incl. the end-of-window mark + realised reduce P&L filed in EVENTS) / cost-basis peak x 100
  DD over the same base, WR over the same closes, sizing line = tools.opt.evaluate_v12.capital_metrics semantics.
Only the first header line is replaced (BH and window are kept from the old line), the old sizing line is replaced, and a visible
note "header recomputed from this page's trades (settings not reproducible)" is added. A page whose header already equals the
recomputation (within 0.01 gain, $1 peak) is left untouched. The companion manifest (same dir, same stem) gets
"header_recomputed_utc" + "header_recomputed". Backup: <root>/backups/board_header_recompute/<UTC>.tar. --dry-run plans only.
"""
from __future__ import annotations

import argparse
import html as _html
import importlib.util
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = importlib.util.spec_from_file_location("bntg_hdr", str(HERE / "board_name_truth_guard.py"))
GU = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(GU)
NOTE = "header recomputed from this page's trades (settings not reproducible)"
CAPM_SPAN_RE = re.compile(r'<br><span style="color:#93c5fd">.*?</span>', re.S)
NOTE_RE = re.compile(r'<br><span class="hdr-recomputed".*?</span>', re.S)
WINDOW_RE = re.compile(r"window\s+(\d+)\s+bars")


def capital_metrics(rows, gain_pct):
    """tools.opt.evaluate_v12.capital_metrics when importable (S1/S2 sandboxes), else None (the line is then omitted)."""
    try:
        sys.path.insert(0, str(HERE.parent))
        from tools.opt import evaluate_v12 as E  # noqa: E402

        return E.capital_metrics(rows, gain_pct)
    except Exception:
        return None


def recompute(page: Path) -> dict:
    t = GU.page_truth(page, force_peak_method="cost")
    if t.get("status") in ("NO_LEDGER", "UNREADABLE", "NO_PEAK"):
        return {"status": "IMPOSSIBLE", "why": t.get("why") or t.get("status"), "truth": t}
    hdr_ok = t.get("header_gain") is not None and abs(round(t["G"], 2) - t["header_gain"]) <= 0.01 + 1e-9 and t.get("peak_header") is not None and abs(round(t["peak"]) - t["peak_header"]) <= 1.0
    return {"status": "CONSISTENT" if hdr_ok else "RECOMPUTE", "truth": t}


def rewrite(page: Path, ss: str, t: dict) -> str:
    text = page.read_text(errors="replace")
    i = text.find('<div class="metrics">')
    j = text.find("</div>", i)
    if i < 0 or j < 0:
        raise ValueError("no metrics div")
    seg = text[i + len('<div class="metrics">'): j]
    k = seg.find("<br>")
    first, rest = (seg, "") if k < 0 else (seg[:k], seg[k:])
    trades = GU.js_const(text, "TRADES") or []
    events = GU.js_const(text, "EVENTS") or []
    closed = [x for x in trades if isinstance(x, dict) and x.get("pnl_dollars") is not None and not GU.is_unrealised(x)]
    countable = [e for e in events if isinstance(e, dict) and (GU.COUNT_MTM or not e.get("unrealised"))]
    realised_ev = [e for e in countable if e.get("pnl_dollars") is not None and not e.get("seeded_prewindow") and not GU.is_unrealised(e)]
    outcomes = [float(x.get("pnl_dollars") or 0.0) for x in closed + realised_ev]
    wr = 100.0 * sum(1 for v in outcomes if v > 0) / len(outcomes) if outcomes else 0.0
    bh = t.get("header_bh")
    wm = WINDOW_RE.search(first)
    g = t["G"]
    line = (f"<b>{_html.escape(ss)} — FINAL</b> &nbsp;|&nbsp; gain {g:.2f}% &nbsp; BH {'—' if bh is None else f'{bh:.2f}'}% &nbsp; Δ {'—' if bh is None else f'{g - bh:.2f}'}% &nbsp;|&nbsp; "
            f"trades {len(closed)} &nbsp; DD {t.get('max_dd_pct', 0.0):.2f}% &nbsp; WR {wr:.2f}% &nbsp; | "
            + (f"window {wm.group(1)} bars &nbsp; " if wm else "") + f"peak ${t['peak']:.0f}")
    rest = NOTE_RE.sub("", CAPM_SPAN_RE.sub("", rest))
    cm = capital_metrics(closed + countable, g)
    capm = ""
    if cm:
        capm = (f'<br><span style="color:#93c5fd">$ gain ${cm["net_gain_dollars"]:.2f} (net of fees) &nbsp; max $ invested ${cm["max_invested_dollars"]:.0f}'
                f' &nbsp; avg $ invested ${cm["avg_invested_dollars"]:.0f} (while in a trade, info only) &nbsp;|&nbsp; flat-size gain {cm["flat_gain_pct"]:.2f}%'
                f' &nbsp; sizing lift {cm["sizing_lift_pct"]:.2f}% &nbsp; size↔return corr {cm["size_return_corr"]:.2f} ({cm["lifecycles"]} trades)</span>')
    note = f'<br><span class="hdr-recomputed" style="color:#f59e0b">⚑ {NOTE} — {time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())}</span>'
    return text[: i + len('<div class="metrics">')] + line + capm + note + rest + text[j:], cm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", action="append")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-age", type=int, default=600)
    ap.add_argument("--skip-list", default=None, help="file of page paths that a re-render pass will handle (left alone)")
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    roots = [Path(r).expanduser().resolve() for r in (a.root or [str(HERE.parent)])]
    skip = set(Path(a.skip_list).read_text().split()) if a.skip_list else set()
    g = GU.Guard(roots, True, 0, roots[0] / "data" / "reports" / "_hdr_recompute_scan_state.json")
    files = g.scan()
    tars = {}
    res = {"consistent": 0, "recomputed": 0, "impossible": [], "young": 0, "skipped_rerender": 0, "errors": []}
    now = time.time()
    for root, p, m in files:
        if not (p.name.endswith("_30d_matrix.html") or p.name.endswith("_30d_matrix.html.superseded")):
            continue
        if str(p) in skip:
            res["skipped_rerender"] += 1
            continue
        try:
            if now - p.stat().st_mtime < a.min_age:
                res["young"] += 1
                continue
            r = recompute(p)
            if r["status"] == "IMPOSSIBLE":
                res["impossible"].append({"page": str(p), "why": r["why"]})
                continue
            if r["status"] == "CONSISTENT":
                res["consistent"] += 1
                continue
            res["recomputed"] += 1
            if a.dry_run:
                continue
            new_text, cm = rewrite(p, m.group("ss"), r["truth"])
            if root not in tars:
                bd = root / "backups" / "board_header_recompute"
                bd.mkdir(parents=True, exist_ok=True)
                tars[root] = tarfile.open(bd / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{os.getpid()}.tar", "a")
            tars[root].add(str(p), arcname=str(p.relative_to(root)), recursive=False)
            tmp = p.with_name(p.name + ".hdr.tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, p)
            stem = p.name[: p.name.index("_30d_matrix")]
            suf = ".superseded" if p.name.endswith(".superseded") else ""
            mp = p.with_name(f"{stem}_30d_matrix_manifest.json{suf}")
            if mp.exists():
                try:
                    man = json.loads(mp.read_text())
                    tars[root].add(str(mp), arcname=str(mp.relative_to(root)), recursive=False)
                    t = r["truth"]
                    man["header_recomputed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    man["header_recomputed"] = {"note": NOTE, "G": t["G"], "sum_pnl_dollars": t["sum_pnl_dollars"], "peak": t["peak"], "max_dd_pct": t.get("max_dd_pct"), "trades": t.get("closed_trades"), "header_gain_before": t.get("header_gain"), "rules": "engine 4532fd52 + evaluator df0d3fa2 semantics (cost-basis peak, end-of-window mark counted, realised reduce P&L)", **({k: cm.get(k) for k in ("net_gain_dollars", "max_invested_dollars", "avg_invested_dollars", "flat_gain_pct", "sizing_lift_pct", "size_return_corr")} if cm else {})}
                    tmpm = mp.with_name(mp.name + ".hdr.tmp")
                    tmpm.write_text(json.dumps(man, indent=1, default=str))
                    os.replace(tmpm, mp)
                except (OSError, ValueError) as exc:
                    res["errors"].append(f"{mp}: {exc}")
        except Exception as exc:
            res["errors"].append(f"{p}: {type(exc).__name__}: {str(exc)[:150]}")
    for tf in tars.values():
        tf.close()
    summary = {k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}
    print(json.dumps({"host": os.uname().nodename, "dry_run": a.dry_run, **summary}), flush=True)
    if a.report:
        Path(a.report).write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
