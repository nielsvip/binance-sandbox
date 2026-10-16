#!/usr/bin/env python3
"""v15_chart_title_repair — make the FILENAME and the MANIFEST say what the chart's own trades say.

USER 2026-10-16: *"Make sure both the header and the title are corrected with actual results from the chart"*.

The header half is done (`tools/opt/hires_chart.py` fail-closed at generation, `tools/v15_chart_header_repair.py`
for what was already on disk). This is the title half. A published board is a TRIPLET that must agree:

    {SS}_bh{bh}_gain{gain}_t{trades}_30d_matrix.xlsx      <- the board
                                        .html             <- the chart (header already repaired)
                                        _manifest.json    <- the provenance the go-live path reads

The filename gain/trades and `manifest.metrics` are written by the VECTOR engine
(`evaluate_prepared_sanitized`), which returns no per-trade returns; the chart is the LEDGER
(`lifecycle_pilot.evaluate_month`). Measured 2026-10-16 after the header repair: 72 charts carry a filename gain
their own trades do not produce and 91 a manifest gain likewise. This renames the triplet to the ledger truth and
rewrites `manifest.metrics`, keeping the superseded values under `metrics_before_title_repair`.

WHY THIS IS CONSEQUENTIAL — READ BEFORE --apply
`v15_pilot._standing_best` / `_apply_best_publish` rank boards by the gain PARSED OUT OF THE FILENAME, and
`tools/v15_persym_golive.py --published` picks per sym_side from the manifests. Correcting a title can therefore
change which board is standing-best for a sym_side, and so which switch set reaches live. That is the CORRECT
outcome — ranking on a measured number instead of one no trade list supports — but it is a live change, so the
tool prints every sym_side whose standing-best flips and `--apply` is off by default.

IT INVENTS NOTHING: every number comes from the chart's own `const CLOSE/TRADES/EVENTS` via
`tools/opt/chart_truth.recompute`. Files are renamed, never rewritten (except the manifest JSON), and every
touched manifest is backed up first.

USAGE
    python3 tools/v15_chart_title_repair.py                    # dry run + standing-best impact
    python3 tools/v15_chart_title_repair.py --apply
    python3 tools/v15_chart_title_repair.py --symside ZROUSDT_SHORT
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SHEET_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
OUT_DIR = ROOT / "data" / "reports"
BACKUP_ROOT = ROOT / "backups"


def log(msg):
    print(msg, flush=True)


def _ledger_metrics(t):
    return {"gain_pct": t["gain_pct"], "trades": t["trades"], "tim_pct": t["tim_pct"], "max_dd_pct": t["max_dd_pct"], "valid": True, "invalid_reason": "", "bh": t["bh_pct"], "gain_vs_bh": t["delta_vs_bh"]}


def _reconcile_manifest(path, t, args, backup_dir, filename_is_stale):
    """The manifest is what go-live reads, so it carries the ledger truth whether or not the file could be renamed.

    Runs for EVERY board, not only renamed ones: the filename gain is an integer (`gain9p`), so a manifest can be
    0.9pp off its own chart while the title still looks right.
    """
    if not path.exists():
        return "no manifest"
    try:
        m = json.loads(path.read_text())
    except Exception as exc:
        return f"unreadable: {type(exc).__name__}"
    cur = (m.get("metrics") or {}).get("gain_pct")
    if cur is not None and abs(float(cur) - float(t["gain_pct"])) <= args.tol and not filename_is_stale:
        return "already agrees"
    if not args.apply:
        return f"would rewrite {cur} -> {round(t['gain_pct'], 4)}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / path.name)
    m.setdefault("metrics_before_title_repair", m.get("metrics"))
    m["metrics"] = _ledger_metrics(t)
    m["metrics_source"] = "ledger — recomputed from the chart's own TRADES/EVENTS by v15_chart_title_repair"
    m["title_repaired_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if filename_is_stale:
        m["filename_is_stale"] = "the correct name is taken by another board; metrics here are the truth, the filename is not"
    path.write_text(json.dumps(m, indent=1, default=str))
    return f"rewritten {cur} -> {round(t['gain_pct'], 4)}"


def truth_of(html):
    from tools.opt.chart_truth import parse_js_const, recompute

    text = html.read_text(errors="replace")
    trades, events, close = parse_js_const(text, "TRADES"), parse_js_const(text, "EVENTS") or [], parse_js_const(text, "CLOSE")
    if trades is None or close is None:
        return None
    return recompute(trades, events, close, html.name.split("_bh")[0].endswith("_LONG"))


def main():
    ap = argparse.ArgumentParser(description="rename published boards so the title matches the chart's own trades")
    ap.add_argument("--dir", default=str(SHEET_DIR))
    ap.add_argument("--glob", default="*_30d_matrix.html")
    ap.add_argument("--symside", default="")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    from tools.v15_final_naming import final_matrix_name, parse_final_matrix_name

    d = Path(args.dir)
    charts = sorted(d.glob(args.glob))
    if args.symside:
        want = {s.strip().upper() for s in args.symside.split(",") if s.strip()}
        charts = [c for c in charts if c.name.split("_bh")[0].upper() in want]
    tag = time.strftime("%Y%m%d%H%M", time.gmtime())
    backup_dir = BACKUP_ROOT / f"manifests_before_title_repair_{tag}"
    recs, renamed, skipped = [], 0, 0
    # standing-best per sym_side, by the gain in the filename, before and after
    before, after = {}, {}
    for c in charts:
        parsed = parse_final_matrix_name(c.name)
        if not parsed:
            continue
        ss = parsed["symside"]
        t = truth_of(c)
        rec = {"chart": c.name, "symside": ss}
        if t is None:
            rec["skip"] = "no embedded arrays"
            recs.append(rec)
            skipped += 1
            continue
        old_gain = parsed["gain"]
        if old_gain is not None:
            cur = before.get(ss)
            if cur is None or old_gain > cur[1]:
                before[ss] = (c.name, old_gain)
        want_name = final_matrix_name(ss, t["bh_pct"], t["gain_pct"], t["trades"], parsed["window_days"], ext="html")
        rec.update(title_gain=old_gain, title_trades=parsed["trades"], true_gain=round(t["gain_pct"], 4), true_trades=t["trades"], true_bh=round(t["bh_pct"], 4) if t["bh_pct"] is not None else None, want=want_name)
        new_gain = t["gain_pct"]
        cur = after.get(ss)
        if cur is None or new_gain > cur[1]:
            after[ss] = (c.name, new_gain, want_name)
        if want_name == c.name:
            rec["skip"] = "title already matches"
            rec["manifest"] = _reconcile_manifest(d / f"{c.name[:-len('.html')]}_manifest.json", t, args, backup_dir, False)
            recs.append(rec)
            skipped += 1
            continue
        stem_old, stem_new = c.name[: -len(".html")], want_name[: -len(".html")]
        trip = [(c, d / want_name), (d / f"{stem_old}.xlsx", d / f"{stem_new}.xlsx"), (d / f"{stem_old}_manifest.json", d / f"{stem_new}_manifest.json")]
        collide = [str(dst.name) for src, dst in trip if dst.exists() and src.exists() and src != dst]
        if collide:
            # The filename cannot change, but the manifest is what tools/v15_persym_golive.py reads, so it must
            # still tell the truth. It gets the ledger metrics plus a flag saying its own name is stale.
            rec["skip"] = f"target name already taken: {collide}"
            rec["manifest"] = _reconcile_manifest(d / f"{c.name[:-len('.html')]}_manifest.json", t, args, backup_dir, True)
            recs.append(rec)
            skipped += 1
            continue
        rec["renames"] = [[src.name, dst.name] for src, dst in trip if src.exists()]
        if args.apply:
            backup_dir.mkdir(parents=True, exist_ok=True)
            man_src = d / f"{stem_old}_manifest.json"
            if man_src.exists():
                shutil.copy2(man_src, backup_dir / man_src.name)
            for src, dst in trip:
                if src.exists():
                    src.rename(dst)
            man_dst = d / f"{stem_new}_manifest.json"
            if man_dst.exists():
                try:
                    m = json.loads(man_dst.read_text())
                    m.setdefault("metrics_before_title_repair", m.get("metrics"))
                    m["metrics"] = _ledger_metrics(t)
                    m["final_name"] = f"{stem_new}.xlsx"
                    m["metrics_source"] = "ledger — recomputed from the chart's own TRADES/EVENTS by v15_chart_title_repair"
                    m["title_repaired_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    man_dst.write_text(json.dumps(m, indent=1, default=str))
                except Exception as exc:
                    rec["manifest_warn"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        renamed += 1
        recs.append(rec)
        log(f"  {c.name}\n    -> {want_name}   (title said gain {old_gain}/t{parsed['trades']}, trades say {t['gain_pct']:+.2f}/t{t['trades']})")
    # A flip is only real when a DIFFERENT BOARD wins the sym_side. Comparing numbers would flag every board,
    # because the filename gain is truncated to an integer (`gain34p`) while the ledger value is exact (34.13).
    flips = {}
    for ss in sorted(set(before) | set(after)):
        b, a = before.get(ss), after.get(ss)
        if not b or not a or b[0] == a[0]:
            continue
        flips[ss] = {"was_best": b[0], "was_best_title_gain": b[1], "now_best": a[0], "now_best_true_gain": round(float(a[1]), 4), "renamed_to": a[2]}
    out = Path(args.out) if args.out else OUT_DIR / f"v15_chart_title_repair_{tag}.json"
    out.write_text(json.dumps({"tag": tag, "apply": args.apply, "renamed": renamed, "skipped": skipped, "standing_best_flips": flips, "charts": recs}, indent=1, default=str))
    log("")
    log(f"[summary] {len(charts)} charts · {renamed} title(s) need the ledger number · {skipped} already correct/skipped")
    log(f"[summary] sym_sides whose standing-best (filename ranking) changes: {len(flips)}")
    for ss, v in list(flips.items())[:20]:
        log(f"[summary]   {ss:22s} was {v['was_best'][:46]:46s} (title {v['was_best_title_gain']:+.2f})")
        log(f"[summary]   {'':22s} now {v['now_best'][:46]:46s} (trades {v['now_best_true_gain']:+.2f})")
    log(f"[summary] evidence {out}")
    if not args.apply:
        log("[summary] DRY RUN — nothing renamed. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
