#!/usr/bin/env python3
"""metrics_guard — single chokepoint for displaying Sharpe + canonical metrics.

This module exists because inflated/misleading Sharpe numbers caused a 30%
net-worth loss in a week (2026-04-29). Every script that emits a Sharpe to a
human (UI, log, CSV, status update, memory) imports this module and routes
through `validate_and_format_sharpe()`.

The function:
  - REFUSES to format a Sharpe that violates CLAUDE.md rules 1, 3, 4, 8
    (sqrt-annualization, bare "Sharpe X" without qualifier, missing trade list)
  - DOWNGRADES to "[DIAGNOSTIC]" tag for sub-minimum-sample results
  - Computes pool_sharpe + sym_sharpe + STANDARD METRIC SET from a trade list
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

# CLAUDE.md floors
MIN_SYMS_CRYPTO = 48
MIN_SYMS_STOCKS = 100
MIN_YEARS = 1.0
MIN_TRADES_PER_SYM_FOR_SYM_SHARPE = 30
PER_SYM_SHARPE_CAP = 5.0
TRASH_FLOOR_POOL_SHARPE = 1.0


class FakeMetricRefused(ValueError):
    """Raised when a metric label/value violates CLAUDE.md rules."""


def pool_sharpe(returns: Sequence[float]) -> float:
    """Pool Sharpe = mean(all_returns) / std(all_returns). Per CLAUDE.md the
    canonical metric. Returns 0 when std is 0 (constant returns)."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((x - mean) ** 2 for x in returns) / n
    sd = math.sqrt(var) if var > 0 else 0.0
    return (mean / sd) if sd > 0 else 0.0


def sym_sharpe_from_groups(returns_by_sym: Mapping[str, Sequence[float]]) -> float:
    """sym_sharpe = mean(per-symbol pool_sharpe), capped at ±5.0,
    excluding syms with < 30 trades."""
    caps: List[float] = []
    for sym, rets in returns_by_sym.items():
        if len(rets) < MIN_TRADES_PER_SYM_FOR_SYM_SHARPE:
            continue
        ps = pool_sharpe(rets)
        capped = max(-PER_SYM_SHARPE_CAP, min(PER_SYM_SHARPE_CAP, ps))
        caps.append(capped)
    return (sum(caps) / len(caps)) if caps else 0.0


def standard_metric_set(returns_by_sym: Mapping[str, Sequence[float]],
                        years: float) -> Dict[str, float]:
    """Compute the CLAUDE.md STANDARD METRIC SET from per-symbol return lists.

    Returns dict with: pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr,
    gain_sym_yr, trades, n_syms, years. All numbers are honest — no annualization.
    """
    all_rets: List[float] = []
    for rets in returns_by_sym.values():
        all_rets.extend(rets)
    n_trades = len(all_rets)
    n_syms = len(returns_by_sym)
    total_gain = sum(all_rets)
    avg_gain = (total_gain / n_trades) if n_trades else 0.0
    yrs = max(0.01, years)
    return {
        "pool_sharpe": pool_sharpe(all_rets),
        "sym_sharpe": sym_sharpe_from_groups(returns_by_sym),
        "avg_gain_trade": avg_gain,
        "gain_per_yr": total_gain / yrs,
        "gain_sym_yr": (total_gain / max(1, n_syms)) / yrs,
        "trades": n_trades,
        "n_syms": n_syms,
        "years": yrs,
        "total_gain_pct": total_gain,
    }


def validate_and_format_sharpe(value: float, *, label: str, n_syms: int,
                               years: float, trades: int,
                               mode: str = "crypto") -> str:
    """Format a Sharpe value with the proper qualifier label and a diagnostic
    tag if below the publishable-sample floor.

    Refuses to format if:
      - `label` is missing or just "sharpe" / "Sharpe" without a qualifier (rule 8)
      - `value` looks sqrt-annualized (heuristic: |value| > 5.0 with trades<5000 reeks
        of inflation; a per-trade Sharpe in real strategies sits in [-2, 3])
      - n_syms or years arguments are missing
    """
    label_norm = (label or "").strip().lower()
    if not label_norm:
        raise FakeMetricRefused("Sharpe must have a qualifier label (got empty)")
    valid_labels = ("pool_sharpe", "sym_sharpe", "sharpe_per_trade", "sharpe_pt", "per_trade_sharpe")
    if label_norm not in valid_labels and not label_norm.startswith("pool_sharpe"):
        raise FakeMetricRefused(
            f"Bare/unqualified Sharpe label {label!r} is forbidden per CLAUDE.md rule 8. "
            f"Use one of {valid_labels}."
        )
    if abs(value) > PER_SYM_SHARPE_CAP and trades < 5000:
        # Looks like sqrt-annualization or a tiny-sample outlier
        raise FakeMetricRefused(
            f"Sharpe {value:.4f} on {trades} trades looks inflated "
            f"(|value| > {PER_SYM_SHARPE_CAP} with sample below floor). "
            f"Per CLAUDE.md rule 3 sqrt-annualization is banned and rule 6 caps "
            f"per-symbol Sharpe. Recompute as per-trade pool_sharpe."
        )
    floor_syms = MIN_SYMS_STOCKS if mode == "stocks" else MIN_SYMS_CRYPTO
    publishable = (n_syms >= floor_syms) and (years >= MIN_YEARS)
    val_str = f"{value:+.4f}"
    if publishable:
        return f"{label_norm}={val_str}"
    return f"{label_norm}={val_str} [DIAGNOSTIC ONLY · n_syms={n_syms} · years={years:.2f}]"


def format_standard_set(metrics: Mapping[str, float], *, mode: str = "crypto") -> str:
    """Format the full STANDARD METRIC SET as a single line per CLAUDE.md.

    Required fields: pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr,
    gain_sym_yr, trades, dd (or max_dd_pct), n_syms, years.
    Refuses if any are missing.
    """
    required = ("pool_sharpe", "sym_sharpe", "avg_gain_trade",
                "gain_per_yr", "gain_sym_yr", "trades", "n_syms", "years")
    missing = [k for k in required if k not in metrics]
    if missing:
        raise FakeMetricRefused(
            f"STANDARD METRIC SET incomplete — missing {missing}. "
            f"All 9 fields required per CLAUDE.md."
        )
    dd = metrics.get("max_dd_pct", metrics.get("dd"))
    dd_str = f"{dd:.1f}%" if isinstance(dd, (int, float)) else "?"
    n_syms = int(metrics["n_syms"])
    years = float(metrics["years"])
    floor_syms = MIN_SYMS_STOCKS if mode == "stocks" else MIN_SYMS_CRYPTO
    diag = "" if (n_syms >= floor_syms and years >= MIN_YEARS) else \
           f"  [DIAGNOSTIC · {n_syms}/{floor_syms} syms · {years:.2f}y]"
    return (
        f"pool_sharpe={metrics['pool_sharpe']:+.4f} | "
        f"sym_sharpe={metrics['sym_sharpe']:+.4f} | "
        f"avg_gain_trade={metrics['avg_gain_trade']:+.4f}%/trade | "
        f"gain_per_yr={metrics['gain_per_yr']:+.1f}%/yr | "
        f"gain_sym_yr={metrics['gain_sym_yr']:+.4f}%/sym/yr | "
        f"trades={int(metrics['trades']):,} | "
        f"dd={dd_str} | "
        f"n_syms={n_syms} | "
        f"years={years:.2f}{diag}"
    )


# ---------- legacy CSV audit / convert -----------------------------------

# Banned column names per CLAUDE.md NO-LIES MANDATE.
# Anything that smells of annualization, weighting, proxy, or rough estimation.
LEGACY_INFLATED_COLUMNS = (
    "sharpe_annual", "sharpe_annualized", "sharpe_yearly", "sharpe_y",
    "sharpe_ann", "sharpe_w", "sharpe_weighted",
    "pool_sharpe_proxy", "sharpe_proxy",
    "sharpe_rough", "sharpe_estimate", "sharpe_est",
)
# These are present-but-marked columns; we tolerate them if a canonical column also exists.
TOLERATED_DIAGNOSTIC_COLUMNS = (
    "sharpe_ann_INVALID",  # already explicitly named INVALID — author flagged it
    "deflated_sharpe", "psr",  # Bailey/PSR — diagnostic, OK as long as canonical present
    "sym_sharpe", "sym_sharpe_avg", "per_sym_sharpe",  # diagnostic per CLAUDE.md
    "sharpe_pt", "sharpe_per_trade",  # canonical synonyms
    "sharpe_max", "sharpe_med", "sharpe_min", "sharpe_p25", "sharpe_p75",  # distribution
    "stage1_sharpe", "stage2_sharpe", "worker_pool_sharpe",  # sub-pool variants
    "sym_sharpe_old", "long_old_sharpe", "short_old_sharpe",  # old labels, retained
    "long_strict_sharpe", "short_strict_sharpe",  # OK
    "sharpe_net", "sharpe_48", "sharpe_100",  # OK
    "syms_with_sharpe",  # count, not value
    "cfg_EARLY_ABORT_SHARPE_FLOOR",  # config knob, not a result
)


def _is_canonical_sharpe_col(col: str) -> bool:
    return col in ("pool_sharpe", "sharpe_per_trade", "sharpe_pt")


def _is_banned_sharpe_col(col: str) -> bool:
    if col in TOLERATED_DIAGNOSTIC_COLUMNS:
        return False
    if col in LEGACY_INFLATED_COLUMNS:
        return True
    cl = col.lower()
    # Catch sneaky variants
    if cl in ("sharpe", "_sharpe"):
        return True
    if "annual" in cl and "sharpe" in cl:
        return True
    if cl == "sharpe_w":
        return True
    return False


def audit_csv(path: Path) -> Dict[str, object]:
    """Inspect a CSV for banned/inflated Sharpe columns. Returns audit dict."""
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False}
    try:
        with p.open("r", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            cols = rd.fieldnames or []
    except Exception as e:
        return {"path": str(p), "exists": True, "error": str(e)}
    banned = [c for c in cols if _is_banned_sharpe_col(c)]
    bare_sharpe = [c for c in cols if c.lower() in ("sharpe", "_sharpe")]
    canonical = [c for c in cols if _is_canonical_sharpe_col(c)]
    has_any_sharpe_col = any("sharpe" in c.lower() for c in cols)
    if has_any_sharpe_col and not canonical:
        verdict = "INVALID"  # Sharpe-bearing CSV without a canonical column = lying
    elif banned and not canonical:
        verdict = "INVALID"
    elif banned and canonical:
        verdict = "QUESTIONABLE"  # canonical present but banned column also present
    elif bare_sharpe:
        verdict = "INVALID"
    elif canonical:
        verdict = "OK"
    elif not has_any_sharpe_col:
        verdict = "NO_SHARPE"  # CSV doesn't claim a Sharpe at all — fine
    else:
        verdict = "QUESTIONABLE"
    return {
        "path": str(p),
        "exists": True,
        "columns": cols,
        "banned_columns": banned,
        "bare_sharpe_columns": bare_sharpe,
        "canonical_columns": canonical,
        "has_any_sharpe_col": has_any_sharpe_col,
        "verdict": verdict,
    }


def audit_directory(directory: Path) -> List[Dict[str, object]]:
    """Audit every CSV in a directory tree. Returns list of audit dicts."""
    out: List[Dict[str, object]] = []
    for p in sorted(Path(directory).rglob("*.csv")):
        out.append(audit_csv(p))
    return out


# ---------- write-time chokepoint -----------------------------------------

CANONICAL_REQUIRED_COLS = (
    "pool_sharpe", "sym_sharpe", "avg_gain_trade",
    "gain_per_yr", "gain_sym_yr", "trades", "max_dd_pct",
    "n_syms", "years",
)


def write_sharpe_row(csv_path: Path, row: Mapping[str, object],
                     mode: str = "crypto", append: bool = True) -> None:
    """Write a single row to a Sharpe-bearing CSV. REFUSES if the row violates
    the NO-LIES MANDATE. This is the ONLY sanctioned way to add a Sharpe row
    to data/sweep_results/ or data/autonomous/.

    Validation:
      - All CANONICAL_REQUIRED_COLS present.
      - No banned column names in row keys.
      - pool_sharpe value within [-5, 5] OR sample is huge (≥5000 trades).
      - n_syms / years sane.
      - On below-floor sample, row gets a `verdict` column = `[DIAGNOSTIC]`.
    """
    row_keys = set(row.keys())
    missing = [c for c in CANONICAL_REQUIRED_COLS if c not in row_keys]
    if missing:
        raise FakeMetricRefused(
            f"write_sharpe_row REFUSED: missing canonical columns {missing} in row for {csv_path}. "
            f"NO-LIES MANDATE requires all 9 canonical columns."
        )
    banned_in_row = [c for c in row_keys if _is_banned_sharpe_col(c)]
    if banned_in_row:
        raise FakeMetricRefused(
            f"write_sharpe_row REFUSED: banned columns {banned_in_row} in row for {csv_path}. "
            f"Per CLAUDE.md NO-LIES MANDATE, drop these or rename to a canonical synonym."
        )
    ps = float(row.get("pool_sharpe", 0) or 0)
    n_syms = int(row.get("n_syms", 0) or 0)
    years = float(row.get("years", 0) or 0)
    trades = int(row.get("trades", 0) or 0)
    if abs(ps) > PER_SYM_SHARPE_CAP and trades < 5000:
        raise FakeMetricRefused(
            f"write_sharpe_row REFUSED: pool_sharpe={ps:.4f} on {trades} trades looks inflated. "
            f"Per CLAUDE.md rule 3, sqrt-annualization banned and rule 6 caps Sharpe at ±{PER_SYM_SHARPE_CAP}."
        )
    floor_syms = MIN_SYMS_STOCKS if mode == "stocks" else MIN_SYMS_CRYPTO
    publishable = (n_syms >= floor_syms) and (years >= MIN_YEARS)
    row = dict(row)
    row.setdefault("verdict", "PUBLISHABLE" if publishable else "DIAGNOSTIC")
    p = Path(csv_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_header = (not p.exists()) or (not append)
    cols = list(CANONICAL_REQUIRED_COLS) + [k for k in row.keys() if k not in CANONICAL_REQUIRED_COLS]
    mode_str = "a" if append and p.exists() else "w"
    with p.open(mode_str, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in cols})


# ---------- recompute from trade list -------------------------------------

def recompute_pool_sharpe_from_jsonl(jsonl_path: Path,
                                     gain_field: str = "pnl_pct") -> Dict[str, object]:
    """Read a per-trade JSONL (one trade per line, with a gain_field), recompute
    pool_sharpe + standard_metric_set. Used to repair lying CSVs that have a
    sibling JSONL with the underlying trades.
    """
    p = Path(jsonl_path)
    if not p.exists():
        return {"recovered": False, "reason": "JSONL missing", "path": str(p)}
    by_sym: Dict[str, List[float]] = {}
    n_lines = 0
    bad_lines = 0
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n_lines += 1
            try:
                rec = json.loads(line)
            except Exception:
                bad_lines += 1
                continue
            sym = rec.get("symbol") or rec.get("sym") or rec.get("ticker") or "?"
            g = rec.get(gain_field)
            if g is None:
                # try common fallbacks
                g = rec.get("gain_pct") or rec.get("pnl") or rec.get("return_pct")
            if g is None:
                continue
            try:
                by_sym.setdefault(sym, []).append(float(g))
            except Exception:
                continue
    return {
        "recovered": bool(by_sym),
        "n_lines": n_lines,
        "bad_lines": bad_lines,
        "n_syms": len(by_sym),
        "n_trades": sum(len(v) for v in by_sym.values()),
        "pool_sharpe": pool_sharpe([x for v in by_sym.values() for x in v]),
        "sym_sharpe": sym_sharpe_from_groups(by_sym),
        "by_sym_count": {k: len(v) for k, v in by_sym.items()},
    }


# ---------- self-test -----------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "audit":
        target = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("data/sweep_results")
        results = audit_directory(target)
        invalid = [r for r in results if r.get("verdict") == "INVALID"]
        questionable = [r for r in results if r.get("verdict") == "QUESTIONABLE"]
        ok = [r for r in results if r.get("verdict") == "OK"]
        no_sharpe = [r for r in results if r.get("verdict") == "NO_SHARPE"]
        print(f"Audited {len(results)} CSVs in {target}")
        print(f"  OK (canonical, no banned): {len(ok)}")
        print(f"  NO_SHARPE (CSV doesn't claim Sharpe): {len(no_sharpe)}")
        print(f"  QUESTIONABLE (canonical present + something off): {len(questionable)}")
        print(f"  INVALID (Sharpe claim without canonical / banned-only): {len(invalid)}")
        if "--show-invalid" in sys.argv:
            for r in invalid[:50]:
                print(f"    {r['path']}: banned={r.get('banned_columns')} bare={r.get('bare_sharpe_columns')} canonical={r.get('canonical_columns')}")
    else:
        # Smoke test
        rets_btc = [0.5, -0.3, 0.8, 0.2, -0.1] * 10
        rets_eth = [0.4, -0.2, 0.6, 0.1, 0.0] * 10
        m = standard_metric_set({"BTCUSDT": rets_btc, "ETHUSDT": rets_eth}, years=1.5)
        print("Smoke test STANDARD METRIC SET:")
        print(format_standard_set({**m, "max_dd_pct": 4.2}, mode="crypto"))
        print()
        print("Validate sharpe 0.45 from 2 syms / 1.5yr / 50 trades:")
        print(validate_and_format_sharpe(0.45, label="pool_sharpe", n_syms=2, years=1.5, trades=50))
        print()
        print("Refused (sharpe 9.3 on 100 trades — looks inflated):")
        try:
            print(validate_and_format_sharpe(9.3, label="pool_sharpe", n_syms=2, years=1.0, trades=100))
        except FakeMetricRefused as e:
            print(f"  FakeMetricRefused: {e}")
        print()
        print("Refused (bare 'sharpe' label):")
        try:
            print(validate_and_format_sharpe(0.5, label="sharpe", n_syms=50, years=2.0, trades=10000))
        except FakeMetricRefused as e:
            print(f"  FakeMetricRefused: {e}")
