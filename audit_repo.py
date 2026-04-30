#!/usr/bin/env python3
"""audit_repo — CI gate for the NO-LIES MANDATE.

Runs metrics_guard.scan_repo_for_emitters and decides whether the repo state
is safe to commit / push / sweep with.

Always-blocking findings (no exceptions):
  - SQRT_ANNUAL anywhere in active code

Ratchet-blocking findings (block when the baseline grows):
  - any cockroach in one of the ACTIVE_EMITTERS scripts beyond what's in
    audit_repo_baseline.json

Reportable but non-blocking (unless --strict):
  - BARE_SHARPE_KEY / FSTRING_SHARPE in non-active scripts (tracked for
    the retrofit phase)

Use cases:
  python audit_repo.py                # ratchet mode: SQRT_ANNUAL + new active hits block
  python audit_repo.py --strict       # zero-tolerance (use after retrofit complete)
  python audit_repo.py --update-baseline  # save current state as new baseline
  python audit_repo.py --report       # never fail; print summary
  python audit_repo.py --json         # machine-readable

Exit codes:
  0 — clean (no blocking findings)
  1 — blocking findings present
  2 — usage error
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BASELINE_PATH = REPO_ROOT / "audit_repo_baseline.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import metrics_guard  # noqa: E402


def _finding_key(f) -> str:
    """Stable key for set-comparison against baseline. Pattern + file +
    code-snippet so renaming line numbers doesn't break the baseline."""
    return f"{f['pattern']}|{f['file']}|{f['code'].strip()[:120]}"


def _load_baseline():
    if not BASELINE_PATH.exists():
        return None
    try:
        return json.loads(BASELINE_PATH.read_text())
    except Exception:
        return None


def _save_baseline(findings):
    payload = {
        "generated_utc": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "n_findings": len(findings),
        "keys": sorted({_finding_key(f) for f in findings}),
        "details": [
            {
                "file": f["file"],
                "line": f["line"],
                "pattern": f["pattern"],
                "code": f["code"][:200],
            }
            for f in sorted(findings, key=lambda x: (x["file"], x["line"]))
        ],
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def _classify(findings):
    sqrt_hits = [f for f in findings if f["pattern"] == "SQRT_ANNUAL"]
    active_hits = [
        f for f in findings
        if Path(f["file"]).name in metrics_guard.ACTIVE_EMITTERS
        and "snapshots" not in f["file"]
        and "backups" not in f["file"]
    ]
    other = [f for f in findings if f not in sqrt_hits and f not in active_hits]
    return sqrt_hits, active_hits, other


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    args = set(argv)
    strict = "--strict" in args
    report_only = "--report" in args
    as_json = "--json" in args
    update_baseline = "--update-baseline" in args

    findings = metrics_guard.scan_repo_for_emitters(REPO_ROOT)
    sqrt_hits, active_hits, other = _classify(findings)

    if update_baseline:
        payload = _save_baseline(sqrt_hits + active_hits)
        print(f"Baseline updated: {BASELINE_PATH}")
        print(f"  {payload['n_findings']} active-emitter / SQRT findings recorded")
        return 0

    baseline = _load_baseline()
    baseline_keys = set(baseline.get("keys", [])) if baseline else set()
    current_keys = {_finding_key(f) for f in (sqrt_hits + active_hits)}
    new_findings_keys = current_keys - baseline_keys
    new_findings = [
        f for f in (sqrt_hits + active_hits)
        if _finding_key(f) in new_findings_keys
    ]

    if as_json:
        print(json.dumps({
            "repo": str(REPO_ROOT),
            "baseline_present": baseline is not None,
            "baseline_size": len(baseline_keys),
            "n_findings_total": len(findings),
            "n_sqrt_annual": len(sqrt_hits),
            "n_active_emitter": len(active_hits),
            "n_other_legacy": len(other),
            "n_new_vs_baseline": len(new_findings),
            "new_findings": [
                {"file": f["file"], "line": f["line"], "pattern": f["pattern"], "code": f["code"][:120]}
                for f in new_findings
            ],
            "strict": strict,
            "report_only": report_only,
        }, indent=2))
        if report_only:
            return 0
        if sqrt_hits:
            return 1
        if strict and findings:
            return 1
        if new_findings:
            return 1
        return 0

    print(f"audit_repo: {len(findings)} cockroach hits in {REPO_ROOT}")
    print(f"  SQRT_ANNUAL:        {len(sqrt_hits)}  (ALWAYS BLOCKING)")
    print(f"  active-emitter:     {len(active_hits)}  (baseline: {len(baseline_keys)})")
    print(f"  other (legacy):     {len(other)}  (tracked; --strict to block)")
    print(f"  NEW vs baseline:    {len(new_findings)}  (BLOCKING — ratchet)")

    if sqrt_hits and not report_only:
        print("\n=== SQRT_ANNUAL (BLOCKING — original cockroach) ===")
        for f in sqrt_hits:
            print(f"  {f['file']}:{f['line']} [{f['pattern']}] {f['code'][:140]}")
        return 1

    if new_findings and not report_only:
        print("\n=== NEW active-emitter findings vs baseline (BLOCKING) ===")
        for f in new_findings[:50]:
            print(f"  {f['file']}:{f['line']} [{f['pattern']}] {f['code'][:140]}")
        if len(new_findings) > 50:
            print(f"  ...and {len(new_findings) - 50} more")
        print("\nFix the new findings, OR if you intentionally added them and the")
        print("baseline is genuinely growing, run: python audit_repo.py --update-baseline")
        return 1

    if strict and findings and not report_only:
        print("\n=== --strict: ANY finding blocks ===")
        for f in findings[:50]:
            print(f"  {f['file']}:{f['line']} [{f['pattern']}] {f['code'][:140]}")
        if len(findings) > 50:
            print(f"  ...and {len(findings) - 50} more")
        return 1

    if not baseline:
        print("\nNote: no baseline yet — run 'python audit_repo.py --update-baseline' "
              "to capture current state. Until baseline exists, only SQRT_ANNUAL blocks.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
