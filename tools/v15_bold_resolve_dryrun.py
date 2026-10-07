#!/usr/bin/env python3
"""v15_bold_resolve_dryrun — DRY RUN: resolve one-bold-per-switch violations by the operator's tiebreak.

For every MULTI_BOLD switch (from data/cat_side_defaults.json produced by sync_catside_defaults.py):
  - primary: pick the bold candidate value with the best gated winsorized avg_delta (previous-round winner);
  - if NONE of the bold candidates has a positive delta but ANOTHER value does → promotion would move the
    default to that other value (noted);
  - if the switch has NO deltas at all → basis = NEEDS_LOG (fall back to (b) most-recent-promotion via the
    promotion log — not present yet at bootstrap, so FLAG for one-time resolution).
Read-only: writes data/reports/bold_resolution_<date>.{json,md}; touches NO template/config.
"""
import argparse, datetime, json, pathlib
import v15_promote_dryrun as P  # reuse collect/gated/is_sizing_excluded (module-level defs only)

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]


def best_value(deltas_by_val, candidates, min_pos, min_frac, cap):
    scored = []
    for v in candidates:
        deltas = deltas_by_val.get(v)
        if deltas is None:  # lenient numeric match (e.g. "10" vs "10.0")
            for k in deltas_by_val:
                try:
                    if float(k) == float(v):
                        deltas = deltas_by_val[k]; break
                except Exception:
                    pass
        g = P.gated(deltas, min_pos, min_frac, cap) if deltas else None
        if g:
            scored.append((v, g["score"], g["pos_sym"], g["n"]))
    scored.sort(key=lambda x: -x[1])
    return scored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pos", type=int, default=2)
    ap.add_argument("--min-frac", type=float, default=0.05)
    ap.add_argument("--cap", type=float, default=10.0)
    ap.add_argument("--date", default=datetime.datetime.utcnow().strftime("%Y%m%d"))
    args = ap.parse_args()
    cdefs = json.loads((ROOT / "data" / "cat_side_defaults.json").read_text())
    violations = cdefs.get("violations", {})
    sw, _fl = P.collect()  # sw[cs][switch] = {value: [deltas]}
    out = {}
    for cs in CAT_SIDES:
        resolved = []
        for tab, name, kind, vals in violations.get(cs, []):
            if not kind.startswith("MULTI_BOLD"):
                continue
            sizing = P.is_sizing_excluded(name)
            byval = sw.get(cs, {}).get(name, {})
            scored = best_value(byval, vals, args.min_pos, args.min_frac, args.cap)
            if scored:
                basis = "avg_delta_winner"
                pick = scored[0][0]
                detail = f"score={scored[0][1]:+.4f} pos_sym={scored[0][2]}/{scored[0][3]}"
            elif byval:
                basis = "no_positive_delta_keep_prior"
                pick = vals[0]
                detail = "no bold candidate had a positive gated delta"
            else:
                basis = "NEEDS_LOG_(b)"
                pick = vals[0]
                detail = "no deltas at all -> most-recent-promotion (needs promotion log); FLAG"
            resolved.append({"tab": tab, "switch": name, "bold_candidates": vals, "resolved_default": pick,
                             "basis": basis, "detail": detail, "sizing_excluded": sizing})
        out[cs] = resolved
        by_basis = {}
        for r in resolved:
            by_basis[r["basis"]] = by_basis.get(r["basis"], 0) + 1
        print(f"[{cs}] multi_bold_resolved={len(resolved)} {by_basis}")
        for r in resolved[:5]:
            print(f"    {r['switch']}: {r['bold_candidates']} -> {r['resolved_default']} ({r['basis']}; {r['detail']})")
    outdir = ROOT / "data" / "reports"
    (outdir / f"bold_resolution_{args.date}.json").write_text(json.dumps(out, indent=2, default=str))
    lines = [f"# One-bold resolution {args.date} (DRY RUN — nothing applied)", ""]
    for cs in CAT_SIDES:
        need_log = sum(1 for r in out[cs] if r["basis"] == "NEEDS_LOG_(b)")
        lines.append(f"## {cs} — {len(out[cs])} multi-bolds ({need_log} need (b)/log)")
        lines.append("| switch | bold_candidates | resolved | basis |")
        lines.append("|---|---|---|---|")
        for r in out[cs]:
            lines.append(f"| {r['switch']} | {r['bold_candidates']} | {r['resolved_default']} | {r['basis']} |")
        lines.append("")
    (outdir / f"bold_resolution_{args.date}.md").write_text("\n".join(lines))
    print(f"[written] {outdir}/bold_resolution_{args.date}.{{json,md}}")


if __name__ == "__main__":
    main()
