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

LEGACY_INFLATED_COLUMNS = (
    "sharpe_annual", "sharpe_annualized", "sharpe_yearly", "sharpe_y",
)


def audit_csv(path: Path) -> Dict[str, object]:
    """Inspect a CSV for legacy inflated Sharpe columns. Returns audit dict."""
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False}
    try:
        with p.open("r", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            cols = rd.fieldnames or []
    except Exception as e:
        return {"path": str(p), "exists": True, "error": str(e)}
    inflated = [c for c in cols if c in LEGACY_INFLATED_COLUMNS]
    bare_sharpe = [c for c in cols if c.lower() == "sharpe"]  # ambiguous label
    has_canonical = "pool_sharpe" in cols
    return {
        "path": str(p),
        "exists": True,
        "columns": cols,
        "inflated_columns": inflated,
        "bare_sharpe_columns": bare_sharpe,
        "has_pool_sharpe": has_canonical,
        "verdict": (
            "OK" if has_canonical and not inflated and not bare_sharpe else
            "QUESTIONABLE" if has_canonical else
            "INVALID"
        ),
    }


def audit_directory(directory: Path) -> List[Dict[str, object]]:
    """Audit every CSV in a directory tree. Returns list of audit dicts."""
    out: List[Dict[str, object]] = []
    for p in sorted(Path(directory).rglob("*.csv")):
        out.append(audit_csv(p))
    return out


# ---------- self-test -----------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "audit":
        target = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("data/sweep_results")
        results = audit_directory(target)
        invalid = [r for r in results if r.get("verdict") == "INVALID"]
        questionable = [r for r in results if r.get("verdict") == "QUESTIONABLE"]
        ok = [r for r in results if r.get("verdict") == "OK"]
        print(f"Audited {len(results)} CSVs in {target}")
        print(f"  OK: {len(ok)}")
        print(f"  QUESTIONABLE (no pool_sharpe column): {len(questionable)}")
        print(f"  INVALID (legacy inflated columns): {len(invalid)}")
        for r in invalid:
            print(f"    {r['path']}: inflated={r['inflated_columns']} bare={r['bare_sharpe_columns']}")
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
