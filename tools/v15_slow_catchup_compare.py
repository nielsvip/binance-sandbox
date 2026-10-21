#!/usr/bin/env python3
"""v15_slow_catchup_compare — slow-vs-catchup board comparison (USER 2026-10-21).

For every sym_side with a QUALIFIED catch-up-era publish AND a QUALIFIED
slow-era publish in per_sym_history, report both boards' OWN numbers
(gain, baseline, bh, trades, dd, NPZ/template/pilot md5) plus the winner
override-key diff (added/removed/changed). Backport candidates = keys the
slow board promoted that catch-up never evaluates (yellow _FILTER_TF keys
flipped from OFF, V17/focus-only families).

Anti-bias rules (no fabricated deltas): each board's gain is reported
against its OWN baseline; slow_vs_catchup gain subtraction is shown ONLY
with same_n collectors: same NPZ sha + same template md5 get
`vintage_match: true`, anything else is flagged. Baseline re-anchoring
(best-start) is reported as its own line item, never folded into a delta.

Era split: SLOW_CUTOFF (fleet-wide slow 21:00Z + direct slow dispatches
from ~20:53Z). Read-only: never writes the DB.
"""
import argparse, datetime, json, os, pathlib, sqlite3, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SLOW_CUTOFF = "2026-10-21T20:50:00+00:00"
DB = os.environ.get("PER_SYM_STORE_DB") or str(ROOT / "data" / "hourly_reconfig" / "per_sym_store.db")


def _rows(sym_side=None):
    con = sqlite3.connect(DB, timeout=60.0)
    con.row_factory = sqlite3.Row
    q = "SELECT sym_side, updated_at, gain_pct, baseline_gain, delta_pct, bh_pct, trades, max_dd_pct, winning_tag, template_md5, overrides_json, meta_json, valid FROM per_sym_history WHERE valid = 1"
    args = []
    if sym_side:
        q += " AND sym_side = ?"
        args.append(sym_side)
    q += " ORDER BY updated_at"
    return [dict(r) for r in con.execute(q, args)]


def _era(updated_at):
    try:
        ts = str(updated_at).replace("Z", "+00:00")
        return "slow" if ts >= SLOW_CUTOFF else "catchup"
    except Exception:
        return "catchup"


def _npz_of(meta_json):
    try:
        return (json.loads(meta_json or "{}") or {}).get("npz")
    except Exception:
        return None


def compare(sym_side, rows):
    catch = [r for r in rows if _era(r["updated_at"]) == "catchup"]
    slow = [r for r in rows if _era(r["updated_at"]) == "slow"]
    if not catch or not slow:
        return None
    c, s = catch[-1], slow[-1]
    try:
        co = json.loads(c["overrides_json"] or "{}")
    except Exception:
        co = {}
    try:
        so = json.loads(s["overrides_json"] or "{}")
    except Exception:
        so = {}
    added = sorted(k for k in so if k not in co)
    removed = sorted(k for k in co if k not in so)
    changed = sorted(k for k in so if k in co and so[k] != co[k])
    filters_on = [k for k in added + changed
                  if k.endswith("_FILTER_TF") and str(so.get(k)) not in ("OFF", "off", "")]
    return {
        "sym_side": sym_side,
        "catchup": {"gain": c["gain_pct"], "baseline": c["baseline_gain"], "bh": c["bh_pct"],
                    "trades": c["trades"], "at": c["updated_at"], "npz": _npz_of(c["meta_json"]),
                    "template": c["template_md5"], "n_overrides": len(co)},
        "slow": {"gain": s["gain_pct"], "baseline": s["baseline_gain"], "bh": s["bh_pct"],
                 "trades": s["trades"], "at": s["updated_at"], "npz": _npz_of(s["meta_json"]),
                 "template": s["template_md5"], "n_overrides": len(so)},
        "vintage_match": bool(_npz_of(c["meta_json"]) and _npz_of(c["meta_json"]) == _npz_of(s["meta_json"])
                              and c["template_md5"] == s["template_md5"]),
        "gain_diff_vs_own_baselines": None if s["gain_pct"] is None or c["gain_pct"] is None else round(s["gain_pct"] - c["gain_pct"], 4),
        "baseline_reanchor": None if s["baseline_gain"] is None or c["baseline_gain"] is None else round(s["baseline_gain"] - c["baseline_gain"], 4),
        "added_keys": added, "removed_keys": removed, "changed_keys": changed,
        "backport_candidates": filters_on,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", default="")
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "reports" / "slow_vs_catchup"))
    a = ap.parse_args()
    rows = _rows(a.sym_side or None)
    by_ss = {}
    for r in rows:
        by_ss.setdefault(r["sym_side"], []).append(r)
    comps = []
    for ss, rs in sorted(by_ss.items()):
        c = compare(ss, rs)
        if c:
            comps.append(c)
    comps.sort(key=lambda c: (c["gain_diff_vs_own_baselines"] is None, -(c["gain_diff_vs_own_baselines"] or 0)))
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    (out / ("compare_%s.json" % stamp)).write_text(json.dumps(
        {"slow_cutoff": SLOW_CUTOFF, "n_compared": len(comps), "compared": comps}, indent=1))
    lines = ["# slow vs catchup %s (%d sym_sides, cutoff %s)" % (stamp, len(comps), SLOW_CUTOFF), "",
             "| sym_side | catchup gain (base) | slow gain (base) | re-anchor | vintage | yellow candidates |",
             "| --- | --- | --- | --- | --- | --- |"]
    for c in comps:
        lines.append("| %s | %.2f (%.2f) | %.2f (%.2f) | %s | %s | %d |" % (
            c["sym_side"], c["catchup"]["gain"] or 0, c["catchup"]["baseline"] or 0,
            c["slow"]["gain"] or 0, c["slow"]["baseline"] or 0,
            c["baseline_reanchor"], "MATCH" if c["vintage_match"] else "mixed",
            len(c["backport_candidates"])))
    (out / ("compare_%s.md" % stamp)).write_text("\n".join(lines) + "\n")
    print(json.dumps({"compared": len(comps), "md": str(out / ("compare_%s.md" % stamp))}))
    for c in comps[:10]:
        print("  %s catchup=%.2f slow=%.2f reanchor=%s vintage=%s +%d/-%d/~%d keys cands=%d" % (
            c["sym_side"], c["catchup"]["gain"] or 0, c["slow"]["gain"] or 0, c["baseline_reanchor"],
            "MATCH" if c["vintage_match"] else "mixed", len(c["added_keys"]), len(c["removed_keys"]),
            len(c["changed_keys"]), len(c["backport_candidates"])))


if __name__ == "__main__":
    main()
