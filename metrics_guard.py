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
import datetime
import json
import math
import re
import shutil
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


def tier_name(pool_sharpe_value: float) -> str:
    """Map pool_sharpe to a tier name per user 2026-04-30 'Real floors not lies'
    directive. Replaces the old 'trash' label — sub-floor results still get the
    [DIAGNOSTIC] tag separately, but tier names are quality positions, not slurs.
        <0       = Discard
        0–0.3    = Noise
        0.3–0.6  = Directional
        ≥0.6     = Best-of-current  (current anchor: tradier pool=0.6516)
        ≥1.0     = Strong
        ≥1.5     = Aspirational
    """
    v = float(pool_sharpe_value)
    if v < 0:    return "Discard"
    if v < 0.3:  return "Noise"
    if v < 0.6:  return "Directional"
    if v < 1.0:  return "Best-of-current"
    if v < 1.5:  return "Strong"
    return "Aspirational"


def format_standard_set(metrics: Mapping[str, float], *, mode: str = "crypto") -> str:
    """Format the full STANDARD METRIC SET as a single line per CLAUDE.md.

    Required fields: pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr,
    gain_sym_yr, trades, dd (or max_dd_pct), n_syms, years.
    Refuses if any are missing. Includes tier name per Real-floors directive.
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
    tier = tier_name(metrics["pool_sharpe"])
    return (
        f"pool_sharpe={metrics['pool_sharpe']:+.4f} ({tier}) | "
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


# ---------- deep CSV row audit -------------------------------------------

def _safe_float(x):
    try:
        return float(x) if x not in (None, "", "None", "nan") else None
    except (ValueError, TypeError):
        return None


def _safe_int(x):
    try:
        return int(float(x)) if x not in (None, "", "None", "nan") else None
    except (ValueError, TypeError):
        return None


def _find_trade_jsonl_pair(csv_path):
    """Heuristic: locate a sibling per-trade JSONL by common naming conventions.
    Returns Path on hit, None otherwise."""
    p = Path(csv_path)
    candidates = [
        p.with_suffix(".trades.jsonl"),
        p.with_suffix(".jsonl"),
        p.parent / (p.stem + "_trades.jsonl"),
        p.parent / (p.stem + ".trades.jsonl"),
        p.parent / "trades" / (p.stem + ".jsonl"),
        p.parent / (p.stem.replace("_results", "_trades") + ".jsonl"),
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    return None


def audit_csv_deep(path, mode: str = "crypto") -> Dict[str, object]:
    """Augments audit_csv with row-level value checks. Catches inflated rows
    (|pool_sharpe| > 5 with trades < 5000) AND sub-floor rows (n_syms < floor
    or years < 1).

    verdict_deep:
      OK            — canonical column, no banned, no inflated rows, ≥1 publishable row
      DIAGNOSTIC_ONLY — canonical column, all rows below sample floor (sub-floor)
      RECOMPUTABLE  — banned/inflated/bare-sharpe BUT a sibling trade JSONL exists
      UNVERIFIABLE  — banned/inflated/bare-sharpe AND no recompute source
      INVALID       — column-level failure with no recovery path
      QUESTIONABLE  — canonical present + suspicious tells (mixed signals)
      READ_ERROR    — could not parse
      NO_SHARPE     — CSV doesn't claim a Sharpe at all (fine, untouched)
    """
    base = audit_csv(path)
    if not base.get("exists"):
        return {**base, "verdict_deep": base.get("verdict", "MISSING")}
    if base.get("verdict") == "NO_SHARPE":
        return {**base, "verdict_deep": "NO_SHARPE", "n_rows": 0}
    p = Path(path)
    cols = base.get("columns", [])
    canonical = base.get("canonical_columns", [])
    sharpe_col = canonical[0] if canonical else (
        "sharpe" if "sharpe" in cols else (
            base.get("bare_sharpe_columns", [None])[0]
            if base.get("bare_sharpe_columns") else None
        )
    )
    if sharpe_col is None:
        # has_any_sharpe_col but neither canonical nor bare; e.g. only banned cols
        for c in base.get("banned_columns", []):
            sharpe_col = c
            break
    floor_syms = MIN_SYMS_STOCKS if mode == "stocks" else MIN_SYMS_CRYPTO
    n_rows = 0
    inflated_rows = 0
    subfloor_rows = 0
    publishable_rows = 0
    syms_seen: set = set()
    years_seen: set = set()
    sharpe_values: List[float] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                n_rows += 1
                ps = _safe_float(r.get(sharpe_col)) if sharpe_col else None
                tr = _safe_int(r.get("trades"))
                ns = _safe_int(r.get("n_syms"))
                yr = _safe_float(r.get("years"))
                if ps is not None:
                    sharpe_values.append(ps)
                if ns is not None:
                    syms_seen.add(ns)
                if yr is not None:
                    years_seen.add(round(yr, 2))
                if ps is not None and abs(ps) > PER_SYM_SHARPE_CAP and (tr or 0) < 5000:
                    inflated_rows += 1
                publishable = (ns is not None and ns >= floor_syms) and (yr is not None and yr >= MIN_YEARS)
                if publishable:
                    publishable_rows += 1
                else:
                    subfloor_rows += 1
    except Exception as e:
        return {**base, "verdict_deep": "READ_ERROR", "error": str(e)}
    has_jsonl_pair = _find_trade_jsonl_pair(p) is not None
    base_verdict = base.get("verdict")
    if base_verdict == "INVALID" or inflated_rows > 0 or base.get("bare_sharpe_columns"):
        verdict_deep = "RECOMPUTABLE" if has_jsonl_pair else "UNVERIFIABLE"
    elif base.get("banned_columns") and not canonical:
        verdict_deep = "RECOMPUTABLE" if has_jsonl_pair else "UNVERIFIABLE"
    elif publishable_rows == 0 and n_rows > 0:
        verdict_deep = "DIAGNOSTIC_ONLY"
    elif base_verdict == "OK" and inflated_rows == 0:
        verdict_deep = "OK"
    elif base_verdict == "QUESTIONABLE":
        verdict_deep = "QUESTIONABLE"
    else:
        verdict_deep = "QUESTIONABLE"
    return {
        **base,
        "n_rows": n_rows,
        "inflated_rows": inflated_rows,
        "subfloor_rows": subfloor_rows,
        "publishable_rows": publishable_rows,
        "max_n_syms": max(syms_seen) if syms_seen else None,
        "min_n_syms": min(syms_seen) if syms_seen else None,
        "max_years": max(years_seen) if years_seen else None,
        "min_years": min(years_seen) if years_seen else None,
        "max_sharpe": max(sharpe_values) if sharpe_values else None,
        "min_sharpe": min(sharpe_values) if sharpe_values else None,
        "has_jsonl_pair": has_jsonl_pair,
        "sharpe_col_used": sharpe_col,
        "verdict_deep": verdict_deep,
    }


# ---------- quarantine ----------------------------------------------------

def quarantine_csv(path, dest_root, reason: str) -> Path:
    """Move a CSV (and any sibling .jsonl trade list) to dest_root preserving
    a useful relative path. Writes a sidecar .quarantine.json with reason +
    timestamp. Idempotent: appends timestamp suffix if dest already exists.
    """
    p = Path(path).resolve()
    dest_root = Path(dest_root).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    rel = p.name
    s = str(p)
    for anchor in ("data/sweep_results", "data/autonomous", "data/_legacy_unverified", "data"):
        if anchor in s:
            idx = s.index(anchor)
            rel = s[idx:]
            break
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        dest = dest.parent / f"{dest.stem}_{ts}{dest.suffix}"
    shutil.move(str(p), str(dest))
    sidecar = dest.with_suffix(dest.suffix + ".quarantine.json")
    sidecar.write_text(json.dumps({
        "original_path": str(p),
        "quarantined_to": str(dest),
        "reason": reason,
        "quarantined_utc": datetime.datetime.utcnow().isoformat() + "Z",
    }, indent=2))
    pair = _find_trade_jsonl_pair(p)
    if pair and pair.exists():
        pair_dest = dest.parent / pair.name
        if pair_dest.exists():
            ts2 = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            pair_dest = dest.parent / f"{pair.stem}_{ts2}{pair.suffix}"
        try:
            shutil.move(str(pair), str(pair_dest))
        except Exception:
            pass
    return dest


def audit_and_quarantine_directory(directory, dest_root, mode: str = "crypto",
                                    dry_run: bool = False) -> Dict[str, object]:
    """Audit every CSV under directory; quarantine UNVERIFIABLE/INVALID/READ_ERROR
    to dest_root. RECOMPUTABLE files are reported but NOT quarantined here —
    they need a separate recompute pass first. Returns summary dict."""
    quarantined: List[Dict[str, object]] = []
    kept_ok: List[Dict[str, object]] = []
    diagnostic_only: List[Dict[str, object]] = []
    recomputable: List[Dict[str, object]] = []
    questionable: List[Dict[str, object]] = []
    read_error: List[Dict[str, object]] = []
    no_sharpe: List[Dict[str, object]] = []
    for p in sorted(Path(directory).rglob("*.csv")):
        if "_legacy_unverified" in str(p) or "_NOLIES_HOLD_" in str(p):
            continue
        a = audit_csv_deep(p, mode=mode)
        v = a.get("verdict_deep")
        if v in ("INVALID", "UNVERIFIABLE", "READ_ERROR"):
            if not dry_run:
                try:
                    dest = quarantine_csv(p, dest_root, reason=f"verdict_deep={v} | {a.get('reason', '')}")
                    a["quarantined_to"] = str(dest)
                except Exception as e:
                    a["quarantine_error"] = str(e)
            quarantined.append(a)
        elif v == "OK":
            kept_ok.append(a)
        elif v == "DIAGNOSTIC_ONLY":
            diagnostic_only.append(a)
        elif v == "RECOMPUTABLE":
            recomputable.append(a)
        elif v == "QUESTIONABLE":
            questionable.append(a)
        elif v == "NO_SHARPE":
            no_sharpe.append(a)
    return {
        "directory": str(directory),
        "dest_root": str(dest_root),
        "mode": mode,
        "dry_run": dry_run,
        "n_total": (len(quarantined) + len(kept_ok) + len(diagnostic_only) +
                    len(recomputable) + len(questionable) + len(no_sharpe)),
        "n_ok": len(kept_ok),
        "n_diagnostic_only": len(diagnostic_only),
        "n_recomputable": len(recomputable),
        "n_quarantined": len(quarantined),
        "n_questionable": len(questionable),
        "n_no_sharpe": len(no_sharpe),
        "quarantined": quarantined,
        "recomputable": recomputable,
        "questionable_paths": [r["path"] for r in questionable],
    }


# ---------- repo-wide emitter scan ----------------------------------------

_PATTERN_SQRT_ANNUAL = re.compile(
    r"\*\s*(?:np\.|math\.)?sqrt\s*\(\s*(?:252|252\.|365|n_trades|len\(|trades_per_yr|trades_per_year)\b"
)
_PATTERN_BARE_SHARPE_KEY = re.compile(
    r"""['"](?:sharpe|sharpe_w|sharpe_annual|sharpe_yearly|sharpe_y|sharpe_ann|"""
    r"""pool_sharpe_proxy|sharpe_rough|sharpe_estimate|sharpe_proxy|sharpe_weighted)['"]"""
)
_PATTERN_FSTRING_SHARPE = re.compile(
    r"""f['"][^'"]*\b[Ss]harpe\b[^'"]*\{[^}]*\}"""
)
_PATTERN_BARE_LABEL = re.compile(
    r"""['"][Ss]harpe['"]?\s*[=:]\s*\{?(?!sharpe_per|pool_sharpe|sym_sharpe)"""
)
_DEFAULT_EXCLUDE_DIRS = (
    "old", ".git", "__pycache__", "backups", "klines_cache",
    "klines_cache_backtest", "klines_cache_gateway", "data",
    "_NOLIES_HOLD_20260430", "_legacy_unverified", "node_modules",
    ".venv", "venv", "env",
    "snapshots_local", "snapshots", ".history",
)


def scan_repo_for_emitters(repo_root,
                           exclude_dirs: Iterable[str] = _DEFAULT_EXCLUDE_DIRS,
                           include_old: bool = False) -> List[Dict[str, object]]:
    """Scan all .py files in repo_root for cockroach Sharpe-emission patterns
    OUTSIDE metrics_guard imports. Returns list of findings dicts."""
    excludes = set(exclude_dirs)
    if include_old:
        excludes.discard("old")
    findings: List[Dict[str, object]] = []
    root = Path(repo_root)
    for py in root.rglob("*.py"):
        if any(part in excludes for part in py.parts):
            continue
        if py.name == "metrics_guard.py":
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "sharpe" not in text.lower():
            continue
        imports_guard = ("import metrics_guard" in text) or ("from metrics_guard" in text)
        for ln_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            for label, pat in (
                ("SQRT_ANNUAL", _PATTERN_SQRT_ANNUAL),
                ("BARE_SHARPE_KEY", _PATTERN_BARE_SHARPE_KEY),
                ("FSTRING_SHARPE", _PATTERN_FSTRING_SHARPE),
            ):
                if pat.search(line):
                    findings.append({
                        "file": str(py.relative_to(root)) if py.is_relative_to(root) else str(py),
                        "line": ln_no,
                        "pattern": label,
                        "code": line.strip()[:240],
                        "imports_metrics_guard": imports_guard,
                    })
    return findings


# ---------- audit_repo CI gate -------------------------------------------

ACTIVE_EMITTERS = {
    "v8_quick_sweep.py", "v8_quick_engine.py", "autonomous_search.py",
    "backtest_v8_engine.py", "v8_test_queue.py",
}


def audit_repo_strict(repo_root, fail_on_active_emitters_only: bool = False
                      ) -> 'tuple[int, list]':
    """Returns (exit_code, findings). exit_code=0 only when no cockroaches
    found. fail_on_active_emitters_only narrows to the 5 known live scripts.
    """
    findings = scan_repo_for_emitters(repo_root)
    if fail_on_active_emitters_only:
        findings = [f for f in findings if Path(f["file"]).name in ACTIVE_EMITTERS]
    return (1 if findings else 0), findings


# ---------- self-test -----------------------------------------------------

def _arg_value(argv, flag, default=None):
    """Read --flag VALUE or --flag=VALUE from argv; default if absent."""
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) >= 2 else "smoke"

    if cmd == "audit":
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

    elif cmd == "audit-deep":
        target = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("data/sweep_results")
        mode = _arg_value(sys.argv, "--mode")
        if mode is None:
            mode = "stocks" if any(x in str(target).lower() for x in ("tradier", "stocks")) else "crypto"
        results = [audit_csv_deep(p, mode=mode) for p in sorted(Path(target).rglob("*.csv"))
                   if "_legacy_unverified" not in str(p) and "_NOLIES_HOLD_" not in str(p)]
        by_v: Dict[str, List[Dict[str, object]]] = {}
        for r in results:
            by_v.setdefault(str(r.get("verdict_deep", "?")), []).append(r)
        print(f"Deep-audited {len(results)} CSVs in {target} (mode={mode})")
        for v in ("OK", "DIAGNOSTIC_ONLY", "RECOMPUTABLE", "UNVERIFIABLE",
                  "INVALID", "QUESTIONABLE", "READ_ERROR", "NO_SHARPE"):
            n = len(by_v.get(v, []))
            if n:
                print(f"  {v}: {n}")
        if "--show-bad" in sys.argv:
            for v in ("INVALID", "UNVERIFIABLE", "QUESTIONABLE", "READ_ERROR"):
                for r in by_v.get(v, [])[:30]:
                    print(f"  [{v}] {r['path']}  rows={r.get('n_rows')} "
                          f"infl={r.get('inflated_rows')} subfloor={r.get('subfloor_rows')} "
                          f"max_syms={r.get('max_n_syms')} max_yr={r.get('max_years')} "
                          f"max_S={r.get('max_sharpe')}")

    elif cmd == "quarantine":
        if len(sys.argv) < 4:
            print("usage: metrics_guard.py quarantine SOURCE_DIR DEST_ROOT [--mode crypto|stocks] [--dry-run]")
            sys.exit(2)
        src = sys.argv[2]
        dst = sys.argv[3]
        mode = _arg_value(sys.argv, "--mode") or (
            "stocks" if any(x in src.lower() for x in ("tradier", "stocks")) else "crypto"
        )
        dry_run = "--dry-run" in sys.argv
        summary = audit_and_quarantine_directory(src, dst, mode=mode, dry_run=dry_run)
        out = {k: v for k, v in summary.items() if k not in ("quarantined", "recomputable")}
        print(json.dumps(out, indent=2))
        print(f"\nQuarantined paths ({len(summary['quarantined'])}):")
        for r in summary["quarantined"][:50]:
            print(f"  {r['path']}  → {r.get('quarantined_to', '(dry-run)')}")
        if summary["recomputable"]:
            print(f"\nRECOMPUTABLE (left in place — needs separate JSONL recompute pass) ({len(summary['recomputable'])}):")
            for r in summary["recomputable"][:30]:
                print(f"  {r['path']}  has_jsonl={r.get('has_jsonl_pair')}")

    elif cmd == "scan-repo":
        root = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path(".")
        include_old = "--include-old" in sys.argv
        findings = scan_repo_for_emitters(root, include_old=include_old)
        by_pattern: Dict[str, List[Dict[str, object]]] = {}
        for f in findings:
            by_pattern.setdefault(str(f["pattern"]), []).append(f)
        print(f"Scanned {root} → {len(findings)} cockroach patterns "
              f"(include_old={include_old})")
        for pat, items in sorted(by_pattern.items()):
            print(f"  {pat}: {len(items)} hits")
        if "--show" in sys.argv:
            for f in findings[:200]:
                print(f"  {f['file']}:{f['line']} [{f['pattern']}] {f['code']}")
        if findings and "--exit-on-findings" in sys.argv:
            sys.exit(1)

    elif cmd == "enforce":
        root = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path(".")
        active_only = "--active-only" in sys.argv
        rc, findings = audit_repo_strict(root, fail_on_active_emitters_only=active_only)
        scope = "active emitters only" if active_only else "full repo"
        print(f"audit_repo enforce ({scope}): {len(findings)} cockroach findings")
        for f in findings[:50]:
            print(f"  {f['file']}:{f['line']} [{f['pattern']}] {f['code']}")
        if len(findings) > 50:
            print(f"  ...and {len(findings) - 50} more")
        sys.exit(rc)

    elif cmd in ("smoke", "test", "selftest"):
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

    else:
        print(__doc__ or "metrics_guard CLI")
        print()
        print("Subcommands:")
        print("  audit DIR              — shallow column audit")
        print("  audit-deep DIR         — deep row + column audit (--mode, --show-bad)")
        print("  quarantine SRC DST     — audit-deep + move INVALID/UNVERIFIABLE to DST")
        print("  scan-repo ROOT         — grep .py files for cockroach patterns")
        print("  enforce ROOT           — CI gate: exit non-zero on findings")
        print("                           (--active-only narrows to 5 live emitters)")
        print("  smoke                  — self-test")
        sys.exit(2)
