#!/usr/bin/env python3
"""v15_consistency_check — continuous NO-LIES integrity test over the PRODUCED sweep numbers (USER 2026-09-30:
"run constantly on the produced numbers to flag inconsistencies immediately").

The headline invariant it enforces: a boolean switch's baseline config is either True OR False, so when the
sweep evaluates BOTH values against the same baseline, exactly ONE must score delta 0 (the value that equals
the running default). If NEITHER is ~0, the default value scored non-zero against itself — impossible unless
the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic, or
the delta was fabricated. Any such case is unsafe to promote (NO-LIES).

It reuses the morning-report collector (tools/v15_morning_report.py) so the logic is identical to what the
daily report shows. Reads the Mac's synced lifecycle_pilot by default; --remote also polls s1/s2/s5.

Cron (Mac, every 10 min, local-only = cheap):
  */10 * * * * cd ~/Documents/binance && .venv/bin/python tools/v15_consistency_check.py >> /tmp/v15_consistency.log 2>&1
Exit code: 0 = clean, 2 = inconsistencies over --fail-on, 1 = collection error. So it can gate a promotion step.
"""
import argparse
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_morning_report as MR  # noqa: E402

OUT_DIR = ROOT / "data" / "reports"


def gather(window_h, use_remote):
    sources = {}
    lr, lerr = MR.run_collector_local(window_h)
    sources["mac"] = lr
    if lerr:
        print(f"[warn] local collector: {lerr}", file=sys.stderr)
    if use_remote:
        for host, aliases in MR.HOSTS.items():
            rows, alias, err = MR.run_collector_remote(aliases, window_h)
            sources[host] = rows
            if err:
                print(f"[warn] {host}: {err}", file=sys.stderr)
    merged = {}
    for host, rws in sources.items():
        for r in rws:
            r = dict(r, host=host)
            ss = r["symside"]
            if ss not in merged or r["mtime"] > merged[ss]["mtime"]:
                merged[ss] = r
    return list(merged.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=float, default=48.0)
    ap.add_argument("--remote", action="store_true", help="also poll s1/s2/s5 (default: Mac-local only)")
    ap.add_argument("--fail-on", type=int, default=0, help="exit 2 when offender sym_sides exceed this")
    ap.add_argument("--quiet", action="store_true", help="only write the report, minimal stdout")
    args = ap.parse_args()

    rows = gather(args.window_hours, args.remote)
    if not rows:
        print("[error] no rows collected", file=sys.stderr)
        return 1
    offenders, swc, total = MR.consistency_summary(rows)
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
    day = datetime.date.today().strftime("%Y%m%d")

    lines = [f"\n## {stamp} · window {args.window_hours}h · {len(rows)} sym_sides "
             f"({'local+remote' if args.remote else 'local'})"]
    if not offenders:
        lines.append("- ✅ CLEAN — every boolean switch has a baseline-matching (delta 0) value in every "
                     "sym_side. No default value scored non-zero against itself.")
    else:
        lines.append(f"- ⚠️ **{len(offenders)} sym_sides / {total} switch×baseline cases** where a bool "
                     f"switch scored non-zero for BOTH True and False vs the same baseline (default-nonzero).")
        lines.append("- Worst switches (by # sym_sides): " +
                     ", ".join(f"`{sw}`×{n}" for sw, n in swc.most_common(20)))
        for r in sorted(offenders, key=lambda r: -r["bool_incons_n"])[:40]:
            ex = r.get("bool_incons_ex") or []
            exs = "; ".join(f"{e[0]} T={e[1]:+.3f}/F={e[2]:+.3f}" for e in ex[:3])
            lines.append(f"    - `{r['symside']}` [{r['host']}] {r['bool_incons_n']} cases — {exs}")
        lines.append("- **Fix:** the engine must return the frozen-baseline gain (delta 0) for the value that "
                     "equals the running config. Trace `evaluate_prepared_sanitized` baseline handling; a "
                     "`0.0` baseline_gain with identical non-zero True/False deltas = a DATA_ERROR baseline "
                     "(dead NPZ) that must not be promoted. See memory v12_quick_engine_synthetic_distinctness.")

    out = OUT_DIR / f"v15_consistency_{day}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        f.write("\n".join(lines) + "\n")
    if not args.quiet:
        print("\n".join(lines))
    print(f"[written] {out}", file=sys.stderr)
    if len(offenders) > args.fail_on:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
