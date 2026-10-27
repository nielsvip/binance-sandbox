#!/usr/bin/env python3
"""Re-queue boards that finished but can no longer be published, so a template edit cannot strand them.

2026-10-27 incident: not one board was published for days although the sweeps were running. Three
deadlocked judgements disagreed about the same board:

  * v15_pilot wrote final_gain + final_path + not_compliant=False  -> the pilot calls it finished
  * self_dispatch's progress_state() sets done = (final_gain is not None), ignoring completeness
    -> the dispatcher SKIPS it, so it never resumes
  * tools/v15_harvest_done.py requires v15_progress_board.is_complete() -> it REFUSES to publish it

A board lands in that gap whenever the TEMPLATE changes after the board finished. Measured on s6:
today's TEMPLATE_FINAL_NORM edit replaced candidate REGIME_TRENDING_WT_REDUCE_FRAC_LOW=0.4 with 0.2,
so 11 finished boards were each 1-6 rows short of the new row set -- rows that did not exist when they
ran -- and no component would ever close the gap. They were complete, unpublished, and unreachable.

This tool finds exactly those boards (final_gain set, compliant, but is_complete False) and relaunches
them through the host's own overload_run.sh, which owns the launch lock, the concurrency cap and the
memory gate. The pilot then does a holes-only resume and fills just the new rows. Nothing is deleted
and no result is rewritten: publication still has to earn is_complete().

Dry-run by default; --apply actually relaunches, newest-shortfall-first, at most --max per run.
"""
import argparse
import glob
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _progress_dir():
    env = os.environ.get("V15_PROGRESS_DIR")
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env)
    try:
        p = pathlib.Path((pathlib.Path.home() / "v15_current_progress_dir.txt").read_text().strip())
        if p.is_dir():
            return p
    except Exception:
        pass
    return None


GREY_RGB = {"FFBFBFBF", "00BFBFBF"}
ESSENTIAL_PREFIX = "ESSENTIAL-ROWS"
UNTESTED_PREFIX = "untested (0 rows"


def _best_first_allowed(root):
    for path in ("/tmp/v15_best_first.json", str(root / "data" / "v15_best_first.json")):
        try:
            allowed = json.loads(pathlib.Path(path).read_text()).get("allowed")
            if allowed:
                return set(allowed)
        except Exception:
            continue
    return None


def _tradeable(symside, allowed):
    return allowed is None or symside.upper() in allowed


def _template_path(root, symside):
    is_stock = "USDC" not in symside and "USDT" not in symside
    side = "LONG" if symside.endswith("_LONG") else "SHORT"
    return root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{'STOCKS' if is_stock else 'CRYPTO'}_{side}.xlsx"


def _measurable_switches(template):
    """-> set of switch names with at least one non-grey row in the template (None if it cannot be read)."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(template, read_only=False, data_only=False)
    except Exception:
        return None
    live = set()
    for ws in wb.worksheets:
        for row in range(3, ws.max_row + 1):
            cell = ws.cell(row=row, column=1)
            name = cell.value
            if not isinstance(name, str) or not name or name.startswith(("=", "#")):
                continue
            rgb = getattr(getattr(cell.font, "color", None), "rgb", None)
            if rgb not in GREY_RGB:
                live.add(name)
    return live


def holes_only_refusal(prog, measurable):
    """True when the board was refused ONLY at the ESSENTIAL-ROWS gate, solely because the essential rows were
    untested (0 rows), and every one of those switches now has a non-grey row in the current template."""
    reason = str(prog.get("not_compliant") or "")
    unprotected = prog.get("unprotected")
    if not reason.startswith(ESSENTIAL_PREFIX) or not isinstance(unprotected, dict) or not unprotected:
        return False
    if any(not str(why).startswith(UNTESTED_PREFIX) for why in unprotected.values()):
        return False
    return measurable is not None and all(name in measurable for name in unprotected)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max", type=int, default=2, help="relaunches per run (the lane cap still applies)")
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--max-short", type=int, default=250, help="skip a board missing more rows than this; it needs a full sweep, not a hole fill")
    ap.add_argument("--host-lane", default=None, help="worker_lanes dir name; default = hostname")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    import v15_progress_board as board

    pdir = _progress_dir()
    if pdir is None:
        print(json.dumps({"event": "no_progress_dir"}))
        return 0
    lane = a.host_lane or os.uname().nodename
    root = pathlib.Path(__file__).resolve().parent.parent
    runner = root / "tools" / "worker_lanes" / lane / "overload_run.sh"
    stranded = []
    allowed = _best_first_allowed(root)
    measurable_cache = {}
    skipped_untradeable = []
    requeue_refused = []
    for path in sorted(glob.glob(str(pdir / "*_v14_progress.json"))):
        symside = os.path.basename(path)[: -len("_v14_progress.json")]
        try:
            prog = json.loads(pathlib.Path(path).read_text())
        except Exception:
            continue
        if prog.get("final_gain") is None or prog.get("bh") is None or not prog.get("final_path"):
            continue
        if prog.get("not_compliant"):
            tpl = _template_path(root, symside)
            if tpl not in measurable_cache:
                measurable_cache[tpl] = _measurable_switches(tpl)
            if not holes_only_refusal(prog, measurable_cache[tpl]):
                continue
            requeue_refused.append(symside)
        if not _tradeable(symside, allowed):
            skipped_untradeable.append(symside)
            continue
        try:
            if board.is_complete(symside):
                continue
            status = board.symside_status(symside)
        except Exception:
            continue
        short = int(status.get("rows") or 0) - int(status.get("filled") or 0)
        if short <= 0 or short > a.max_short:
            continue
        stranded.append({"symside": symside, "short_rows": short, "rows": status["rows"], "final_path": prog.get("final_path")})
    stranded.sort(key=lambda r: r["short_rows"])
    out = {"event": "stranded", "n": len(stranded), "progress_dir": str(pdir), "apply": a.apply,
           "essential_refusal_requeued": sorted(set(requeue_refused) & {r["symside"] for r in stranded}),
           "skipped_not_tradeable": sorted(skipped_untradeable),
           "boards": [{k: r[k] for k in ("symside", "short_rows", "rows")} for r in stranded[:20]]}
    if not stranded:
        if not a.quiet:
            print(json.dumps({"event": "none_stranded", "progress_dir": str(pdir)}))
        return 0
    print(json.dumps(out))
    if not a.apply:
        return 0
    if not runner.exists():
        print(json.dumps({"event": "no_runner", "path": str(runner)}))
        return 1
    launched = []
    for rec in stranded[: a.max]:
        symside = rec["symside"]
        is_stock = "USDC" not in symside and "USDT" not in symside
        side = "LONG" if symside.endswith("_LONG") else "SHORT"
        template = root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{'STOCKS' if is_stock else 'CRYPTO'}_{side}.xlsx"
        if not template.exists():
            continue
        try:
            res = subprocess.run(["bash", str(runner), symside, str(template), str(a.workers), "6"],
                                 capture_output=True, text=True, timeout=180, cwd=str(root))
            verdict = (res.stdout or res.stderr or "").strip().splitlines()
            launched.append({"symside": symside, "short_rows": rec["short_rows"], "verdict": verdict[-1] if verdict else ""})
        except Exception as exc:
            launched.append({"symside": symside, "error": str(exc)})
    print(json.dumps({"event": "requeued", "launched": launched}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
