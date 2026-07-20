#!/usr/bin/env python3
"""build_results_ledger.py — ONE clear, HONEST ledger of every backtest result in
data/sweep_results/ (the last ~6 months of sweeps), with gain/mo + pool_sharpe and an
honest verdict computed from the CLAUDE.md no-lies rules on EVERY row — not the stored
label. Exposes the sub-floor single-symbol "PROMOTE" imposters that lost real money.

Honest verdict per row (overrides whatever the stored `verdict` says):
  MALFORMED            — sharpe/n_syms unparseable (column-misaligned CSV) -> untrustable
  SUB_FLOOR            — n_syms < floor (48 crypto / 100 stocks). CANNOT promote, ever.
                         A single-symbol "BEST" is the exact imposter CLAUDE.md forbids.
  FEW_TRADES           — trades < 30 -> sharpe is noise
  BELOW_EDGE:<tier>    — n_syms>=floor but pool_sharpe < 1.0 -> not a promotion candidate
  FLOOR_OK_NEEDS_TIER2 — n_syms>=floor AND pool_sharpe>=1.0 AND trades>=30. The ONLY honest
                         candidates; still must be Tier-2-reconfirmed before going live.
imposter_flag = stored verdict==PROMOTE but honest verdict is not FLOOR_OK_NEEDS_TIER2.

gain_per_mo = gain_per_yr / 12 (gain_per_yr is canonical acc_gain/n_years).

NO-LIES: nothing here promotes anything. pool_sharpe is reported as-stored (canonical
column); rows whose CSV carries a banned/annualized sharpe column are flagged. The point
is to show, honestly, how little of 6 months of results is actually trustworthy.
"""
import csv
import glob
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    import metrics_guard as mg
    def tier(s):
        try:
            return mg.tier_name(float(s))
        except Exception:
            return "?"
except Exception:
    def tier(s):
        return "?"

FLOOR_CRYPTO, FLOOR_STOCKS = 48, 100
BANNED_COLS = {"sharpe_annual", "sharpe_y", "sharpe_yearly", "sharpe_w",
               "pool_sharpe_proxy", "sharpe_rough"}
CRYPTO_HINT = re.compile(r"USD[CT]|BTC|ETH|SOL|crypto", re.I)
STOCK_HINT = re.compile(r"tradier|trb|trc|stock", re.I)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _mode_floor(row, fname):
    blob = f"{fname} {row.get('tag','')} {row.get('override_path','')} {row.get('mode','')}".lower()
    if STOCK_HINT.search(blob):
        return "stocks", FLOOR_STOCKS
    if CRYPTO_HINT.search(blob):
        return "crypto", FLOOR_CRYPTO
    return "unknown", FLOOR_CRYPTO


def _ts_from_name(fname):
    m = re.search(r"(\d{10})", fname)
    if m:
        return int(m.group(1))
    return None


def honest_verdict(ps, ns, tr, floor):
    if ps is None or ns is None:
        return "MALFORMED"
    if ns < floor:
        return "SUB_FLOOR"
    if tr is not None and tr < 30:
        return "FEW_TRADES"
    if ps < 1.0:
        return f"BELOW_EDGE:{tier(ps)}"
    return "FLOOR_OK_NEEDS_TIER2"


def main():
    files = sorted(glob.glob(str(BASE / "data/sweep_results/*.csv")))
    rows = []
    counts = {}
    imposters = 0
    floor_ok = []
    for fp in files:
        fname = Path(fp).name
        ts = _ts_from_name(fname)
        try:
            with open(fp) as f:
                rdr = csv.DictReader(f)
                cols = set(c.lower() for c in (rdr.fieldnames or []))
                banned = bool(cols & BANNED_COLS)
                for r in rdr:
                    ps = _f(r.get("pool_sharpe"))
                    ns = _f(r.get("n_syms"))
                    tr = _f(r.get("trades"))
                    gpy = _f(r.get("gain_per_yr"))
                    mode, floor = _mode_floor(r, fname)
                    hv = honest_verdict(ps, ns, tr, floor)
                    if banned and hv != "MALFORMED":
                        hv = "UNVERIFIED_BANNED_SHARPE"
                    stored = (r.get("verdict") or "").strip()
                    is_imp = stored.upper() == "PROMOTE" and hv != "FLOOR_OK_NEEDS_TIER2"
                    if is_imp:
                        imposters += 1
                    counts[hv] = counts.get(hv, 0) + 1
                    rec = {
                        "source_file": fname,
                        "run_ts": ts or "",
                        "mode": mode,
                        "floor": floor,
                        "n_syms": int(ns) if ns is not None else "",
                        "pool_sharpe": f"{ps:.4f}" if ps is not None else "",
                        "gain_per_mo_pct": f"{gpy/12:.3f}" if gpy is not None else "",
                        "gain_per_yr_pct": f"{gpy:.2f}" if gpy is not None else "",
                        "trades": int(tr) if tr is not None else "",
                        "years": r.get("years", ""),
                        "max_dd_pct": r.get("max_dd_pct", ""),
                        "wr_pct": r.get("wr_pct", ""),
                        "tag": r.get("tag", ""),
                        "stored_verdict": stored,
                        "HONEST_VERDICT": hv,
                        "imposter_flag": "IMPOSTER" if is_imp else "",
                        "override_path": r.get("override_path", ""),
                    }
                    rows.append(rec)
                    if hv == "FLOOR_OK_NEEDS_TIER2":
                        floor_ok.append((ps, gpy, ns, mode, fname, r.get("tag", "")))
        except Exception:
            continue
    # sort: honest candidates first (by pool_sharpe desc), then everything by gain/mo desc
    def keyf(r):
        ok = 0 if r["HONEST_VERDICT"] == "FLOOR_OK_NEEDS_TIER2" else 1
        ps = _f(r["pool_sharpe"]) or -99
        gm = _f(r["gain_per_mo_pct"]) or -99
        return (ok, -ps, -gm)
    rows.sort(key=keyf)
    out = BASE / "data/results_ledger_6mo.csv"
    cols = list(rows[0].keys())
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    # summary
    total = len(rows)
    promote_stored = sum(1 for r in rows if r["stored_verdict"].upper() == "PROMOTE")
    sub = counts.get("SUB_FLOOR", 0)
    s = []
    s.append(f"# Results ledger — last 6 months, HONEST verdicts  ({total} rows, {len(files)} files)\n")
    s.append("**The point: how much of 6 months of results is actually trustworthy.**\n")
    s.append(f"- rows tagged `PROMOTE` by the producers: **{promote_stored}**")
    s.append(f"- of those, honest verdict != promotable (sub-floor / below-edge imposters): **{imposters}**")
    s.append(f"- rows with n_syms < sample floor (CANNOT promote, ever): **{sub}** ({100.0*sub/total:.1f}%)")
    s.append(f"- rows that are honestly promotable candidates (n_syms>=floor, pool_sharpe>=1.0, trades>=30, still need Tier-2 reconfirm): **{len(floor_ok)}**")
    s.append("")
    s.append("## Honest verdict distribution")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        s.append(f"- {k}: {v}")
    s.append("")
    if floor_ok:
        s.append("## The ONLY rows above the sample floor (the real candidates)")
        floor_ok.sort(key=lambda t: -(t[0] or -99))
        for ps, gpy, ns, mode, fname, tag in floor_ok[:40]:
            s.append(f"- pool_sharpe={ps:.4f} gain/mo={gpy/12:.2f}% n_syms={int(ns)} {mode} `{tag}` ({fname})")
    else:
        s.append("## The ONLY rows above the sample floor: **NONE.**")
        s.append("Not a single result in 6 months met the publishable sample floor. Every 'PROMOTE' was sub-floor.")
    (BASE / "data/results_ledger_SUMMARY.md").write_text("\n".join(s) + "\n")
    print(f"ledger -> {out}  ({total} rows)")
    print(f"PROMOTE-tagged: {promote_stored} | imposters (sub-floor/below-edge but tagged PROMOTE): {imposters}")
    print(f"n_syms<floor: {sub} ({100.0*sub/total:.1f}%) | honestly-promotable candidates: {len(floor_ok)}")
    print("summary -> data/results_ledger_SUMMARY.md")


if __name__ == "__main__":
    main()
