#!/usr/bin/env python3
"""generate_symbol_report.py — Symbol settings + paper vs live trade overview.

Outputs data/SYMBOL_REPORT_<date>.xlsx with 4 sheets:
  1. Summary       — one row per (sym, side), all accounts, paper vs live
  2. Overrides     — per-sym active overrides (per_sym_active_config.json mutations)
  3. 7D_Reconfig   — latest hourly-reconfig winners per account
  4. Discrepancies — symbols with mismatches, gaps, or dead config

Usage:
  python3 generate_symbol_report.py [--days N]  (default last 30 days of live data)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import (Alignment, Font, PatternFill, numbers)
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
DECISIONS_DIR = ROOT / "data" / "decisions"
HR_DIR = ROOT / "data" / "hourly_reconfig"
OUT_DIR = ROOT / "data"

GAIN_RE = re.compile(r"gain[=_]([+-]?\d+(?:\.\d+)?)%", re.I)
MUTATIONS_RE = re.compile(r"mutations=\[(.+)\]", re.DOTALL)

CRYPTO_ACCOUNTS = ("flz", "fin", "inf", "ang", "men")
TRADIER_ACCOUNTS = ("trb", "trc", "tra")
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + TRADIER_ACCOUNTS

ACCOUNT_SYM_FILES = {
    "flz": ["symbols_flz.json"],
    "fin": ["symbols_fin.json"],
    "inf": ["symbols_inf_long.json", "symbols_inf_short.json"],
    "ang": ["symbols_ang_long.json", "symbols_ang_short.json"],
    "men": ["symbols_men.json"],
    "trb": ["symbols_trb_long.json", "symbols_trb_short.json"],
    "trc": ["symbols_trb_long.json", "symbols_trb_short.json"],
}

# ── styles ──────────────────────────────────────────────────────────────────
HDR_FILL = PatternFill("solid", fgColor="1F4E79")
HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
SUBHDR_FILL = PatternFill("solid", fgColor="2E75B6")
SUBHDR_FONT = Font(bold=True, color="FFFFFF", size=9)
WARN_FILL = PatternFill("solid", fgColor="FFC000")
ERR_FILL = PatternFill("solid", fgColor="FF0000")
ERR_FONT = Font(bold=True, color="FFFFFF")
OK_FILL = PatternFill("solid", fgColor="70AD47")
DIAG_FILL = PatternFill("solid", fgColor="D9D9D9")


def _hdr(ws, row, col, text, fill=HDR_FILL, font=HDR_FONT):
    c = ws.cell(row=row, column=col, value=text)
    c.fill = fill
    c.font = font
    c.alignment = Alignment(wrap_text=True, vertical="center")
    return c


def _cell(ws, row, col, val, fmt=None, fill=None, font=None, bold=False):
    c = ws.cell(row=row, column=col, value=val)
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if font:
        c.font = font
    elif bold:
        c.font = Font(bold=True)
    return c


# ── data loading ─────────────────────────────────────────────────────────────

def load_sym_accounts() -> Dict[str, List[str]]:
    """Return {sym: [acct, ...]} for all accounts."""
    result: Dict[str, List[str]] = defaultdict(list)
    for acct, files in ACCOUNT_SYM_FILES.items():
        for fname in files:
            p = ROOT / fname
            if not p.exists():
                continue
            try:
                raw = re.sub(r",(\s*[\]}])", r"\1", p.read_text())
                for s in json.loads(raw):
                    if isinstance(s, str) and acct not in result[s]:
                        result[s].append(acct)
            except Exception:
                pass
    return result


def load_per_sym_active() -> Dict[str, Dict]:
    """Load per_sym_active_config.json — long-term 4yr sweep winners."""
    p = HR_DIR / "per_sym_active_config.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def load_account_active(acct: str) -> Dict[str, Dict]:
    """Load data/hourly_reconfig/{acct}/active_config.json — 7D reconfig winners."""
    p = HR_DIR / acct / "active_config.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def load_live_stats(days: int) -> Dict[str, Dict]:
    """
    Return {"{acct}:{sym_side}": {opens, closes, reentries, reduces,
                                   gains, last_ts, close_gains}} for all accounts.
    Only includes decisions from the last `days` days.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats: Dict[str, Dict] = defaultdict(lambda: {
        "opens": 0, "closes": 0, "reentries": 0, "reduces": 0,
        "close_gains": [], "last_ts": None,
    })

    for acct in ALL_ACCOUNTS:
        for fpath in sorted(DECISIONS_DIR.glob(f"decisions_{acct}_*.jsonl")):
            for line in fpath.open(errors="replace"):
                try:
                    d = json.loads(line)
                    ts_str = d.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except Exception:
                        continue
                    if ts < cutoff:
                        continue
                    pk = d.get("position_key", "")
                    # normalize e.g. "flz:BTCUSDC_LONG" → key = "flz:BTCUSDC_LONG"
                    key = pk
                    action = d.get("action", "")
                    s = stats[key]
                    if action == "OPEN":
                        s["opens"] += 1
                    elif action in ("CLOSE", "QUICK_CLOSE"):
                        s["closes"] += 1
                        m = GAIN_RE.search(d.get("reason", ""))
                        if m:
                            s["close_gains"].append(float(m.group(1)))
                    elif action in ("REENTRY", "QUICK_OPEN"):
                        s["reentries"] += 1
                    elif action == "REDUCE":
                        s["reduces"] += 1
                    if s["last_ts"] is None or ts > s["last_ts"]:
                        s["last_ts"] = ts
                except Exception:
                    pass
    return dict(stats)


def parse_mutations(meta: str) -> str:
    """Extract 'key: old→new' list from _meta string."""
    m = MUTATIONS_RE.search(meta or "")
    if not m:
        return ""
    raw = m.group(1)
    parts = [p.strip().strip("'") for p in raw.split("', '")]
    return "; ".join(parts[:6]) + ("…" if len(parts) > 6 else "")


# ── Sheet 1: Summary ─────────────────────────────────────────────────────────

def build_summary(wb, sym_accounts, per_sym, account_actives, live_stats, days):
    ws = wb.create_sheet("Summary")
    ws.freeze_panes = "A3"

    headers = [
        "Symbol", "Side", "Accounts", "Mode",
        # Per-sym 4yr sweep
        "PrSym Winning Tag", "PrSym wsharpe", "PrSym Trades", "PrSym Sample", "Delta Params",
        # 7D reconfig (best account)
        "7D wsharpe", "7D Trades", "7D Sample", "7D Account",
        # Live (last N days)
        f"Live Opens\n({days}d)", f"Live Closes\n({days}d)",
        f"Live Reentries\n({days}d)", f"Live Reduces\n({days}d)",
        "Avg Close Gain%", "Last Live",
        # Flag
        "Discrepancy",
    ]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)
    _hdr(ws, 2, 1, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}", fill=SUBHDR_FILL, font=SUBHDR_FONT)

    # Build all (sym, side) pairs
    all_pairs = set()
    for key in per_sym:
        sym, side = key.rsplit("_", 1)
        all_pairs.add((sym, side))
    for acct, active in account_actives.items():
        for key in active:
            parts = key.rsplit("_", 1)
            if len(parts) == 2:
                all_pairs.add((parts[0], parts[1]))
    # Add from symbols files (all known syms, assume both sides for crypto, per file for tradier)
    for sym, accts in sym_accounts.items():
        for side in ("LONG", "SHORT"):
            all_pairs.add((sym, side))

    row = 3
    for sym, side in sorted(all_pairs):
        accts = sorted(sym_accounts.get(sym, []))
        if not accts:
            continue
        mode = "tradier" if any(a in TRADIER_ACCOUNTS for a in accts) else "crypto"
        ps_key = f"{sym}_{side}"

        # Per-sym 4yr
        ps = per_sym.get(ps_key, {})
        ps_tag = ps.get("winning_tag", "")
        ps_wsh = ps.get("wsharpe", None)
        ps_tr = ps.get("trades") or ps.get("total_trades_combined")
        ps_samp = ps.get("sample_tag", "")
        ps_delta = ps.get("_delta_params", len(ps.get("overrides", {})))

        # 7D reconfig — take best wsharpe across relevant accounts
        best_7d_ws = None
        best_7d_tr = None
        best_7d_samp = None
        best_7d_acct = None
        for acct in accts:
            active = account_actives.get(acct, {})
            entry = active.get(ps_key)
            if entry:
                ws7 = entry.get("wsharpe")
                if ws7 is not None and (best_7d_ws is None or ws7 > best_7d_ws):
                    best_7d_ws = ws7
                    best_7d_tr = entry.get("trades")
                    best_7d_samp = entry.get("sample_tag", "")
                    best_7d_acct = acct

        # Live stats — aggregate across all accounts for this sym+side
        total_opens = total_closes = total_reentries = total_reduces = 0
        all_gains = []
        last_ts = None
        for acct in accts:
            pk = f"{acct}:{ps_key}"
            s = live_stats.get(pk, {})
            total_opens += s.get("opens", 0)
            total_closes += s.get("closes", 0)
            total_reentries += s.get("reentries", 0)
            total_reduces += s.get("reduces", 0)
            all_gains.extend(s.get("close_gains", []))
            lt = s.get("last_ts")
            if lt and (last_ts is None or lt > last_ts):
                last_ts = lt
        avg_gain = round(sum(all_gains) / len(all_gains), 3) if all_gains else None
        last_live_str = last_ts.strftime("%Y-%m-%d") if last_ts else ""

        # Discrepancy analysis
        flags = []
        if not ps and not best_7d_ws:
            flags.append("NO_CONFIG")
        if ps_wsh is not None and ps_wsh < 0.3 and ps_delta > 0:
            flags.append(f"PrSym_NOISE(ws={ps_wsh:.2f})")
        if best_7d_ws is not None and best_7d_ws < 0:
            flags.append(f"7D_NEGATIVE(ws={best_7d_ws:.2f})")
        if total_opens == 0 and total_closes == 0 and total_reentries == 0:
            if ps or best_7d_ws:
                flags.append("NO_LIVE_TRADES")
        elif total_opens > 0 and total_closes == 0:
            flags.append(f"OPENS_NO_CLOSES({total_opens}o)")
        if avg_gain is not None and avg_gain < -0.5:
            flags.append(f"NEG_AVG_GAIN({avg_gain:.2f}%)")
        if ps_wsh and ps_wsh > 0.8 and total_opens == 0:
            flags.append("GOOD_PAPER_NO_LIVE")
        if best_7d_ws and best_7d_ws > 1.0 and total_opens < 3:
            flags.append("STRONG_7D_FEW_LIVE")
        disc = "; ".join(flags)

        col = 1
        _cell(ws, row, col, sym); col += 1
        _cell(ws, row, col, side); col += 1
        _cell(ws, row, col, ",".join(accts)); col += 1
        _cell(ws, row, col, mode); col += 1
        _cell(ws, row, col, ps_tag); col += 1
        if ps_wsh is not None:
            c = _cell(ws, row, col, round(ps_wsh, 4), fmt="0.0000")
            if ps_wsh < 0:
                c.fill = ERR_FILL; c.font = ERR_FONT
            elif ps_wsh < 0.3:
                c.fill = WARN_FILL
            elif ps_wsh >= 0.6:
                c.fill = OK_FILL
        col += 1
        _cell(ws, row, col, ps_tr); col += 1
        _cell(ws, row, col, ps_samp); col += 1
        _cell(ws, row, col, ps_delta if ps_delta else None); col += 1
        if best_7d_ws is not None:
            c = _cell(ws, row, col, round(best_7d_ws, 4), fmt="0.0000")
            if best_7d_ws < 0:
                c.fill = ERR_FILL; c.font = ERR_FONT
            elif best_7d_ws < 0.3:
                c.fill = WARN_FILL
            elif best_7d_ws >= 1.0:
                c.fill = OK_FILL
        col += 1
        _cell(ws, row, col, best_7d_tr); col += 1
        _cell(ws, row, col, best_7d_samp); col += 1
        _cell(ws, row, col, best_7d_acct); col += 1
        _cell(ws, row, col, total_opens or None); col += 1
        _cell(ws, row, col, total_closes or None); col += 1
        _cell(ws, row, col, total_reentries or None); col += 1
        _cell(ws, row, col, total_reduces or None); col += 1
        if avg_gain is not None:
            c = _cell(ws, row, col, avg_gain, fmt='0.000"%"')
            if avg_gain < 0:
                c.fill = WARN_FILL
        col += 1
        _cell(ws, row, col, last_live_str); col += 1
        if disc:
            c = _cell(ws, row, col, disc)
            c.fill = WARN_FILL if "NO_LIVE" not in disc else ERR_FILL
        col += 1

        row += 1

    # Column widths
    widths = [14, 6, 20, 8, 28, 10, 10, 12, 8, 10, 8, 12, 8, 10, 10, 10, 8, 10, 12, 40]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row-1}"
    print(f"  Sheet 'Summary': {row-3} rows")


# ── Sheet 2: Override Details ─────────────────────────────────────────────────

def build_overrides(wb, per_sym):
    ws = wb.create_sheet("Overrides")
    ws.freeze_panes = "A2"
    headers = ["Sym_Side", "Sample", "wsharpe", "Trades", "Param", "Value", "Source"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)
    row = 2
    for key, entry in sorted(per_sym.items()):
        ovr = entry.get("overrides", {})
        meta = entry.get("overrides", {}).get("_meta", "") if "_meta" in entry.get("overrides", {}) else ""
        mutations = parse_mutations(meta)
        if not ovr and not mutations:
            # Baseline — no overrides
            c = ws.cell(row=row, column=1, value=key)
            ws.cell(row=row, column=2, value=entry.get("sample_tag", ""))
            ws.cell(row=row, column=3, value=round(entry.get("wsharpe", 0), 4))
            ws.cell(row=row, column=4, value=entry.get("trades"))
            ws.cell(row=row, column=5, value="(baseline — no overrides)")
            ws.cell(row=row, column=6, value="")
            ws.cell(row=row, column=7, value=entry.get("winning_tag", ""))
            row += 1
        else:
            for param, val in ovr.items():
                if param.startswith("_"):
                    continue
                ws.cell(row=row, column=1, value=key)
                ws.cell(row=row, column=2, value=entry.get("sample_tag", ""))
                ws.cell(row=row, column=3, value=round(entry.get("wsharpe", 0), 4))
                ws.cell(row=row, column=4, value=entry.get("trades"))
                ws.cell(row=row, column=5, value=param)
                ws.cell(row=row, column=6, value=str(val))
                ws.cell(row=row, column=7, value=entry.get("winning_tag", ""))
                row += 1

    for i, w in enumerate([20, 12, 10, 10, 40, 20, 36], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row-1}"
    print(f"  Sheet 'Overrides': {row-2} override rows")


# ── Sheet 3: 7D Reconfig state ────────────────────────────────────────────────

def build_7d_reconfig(wb, account_actives):
    ws = wb.create_sheet("7D_Reconfig")
    ws.freeze_panes = "A2"
    headers = ["Account", "Sym_Side", "Winning Tag", "wsharpe", "Trades", "Sample", "Cycle ID",
               "Key Mutations"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)
    row = 2
    for acct in sorted(account_actives.keys()):
        active = account_actives[acct]
        for key, entry in sorted(active.items()):
            ovr = entry.get("overrides", {})
            meta = ovr.get("_meta", "")
            mutations = parse_mutations(meta) if meta else ""
            ws7 = entry.get("wsharpe")
            c1 = ws.cell(row=row, column=1, value=acct)
            ws.cell(row=row, column=2, value=key)
            ws.cell(row=row, column=3, value=entry.get("winning_tag", ""))
            c4 = ws.cell(row=row, column=4, value=round(ws7, 4) if ws7 is not None else None)
            if ws7 is not None:
                if ws7 < 0:
                    c4.fill = ERR_FILL; c4.font = ERR_FONT
                elif ws7 < 0.3:
                    c4.fill = WARN_FILL
                elif ws7 >= 1.0:
                    c4.fill = OK_FILL
            ws.cell(row=row, column=5, value=entry.get("trades"))
            ws.cell(row=row, column=6, value=entry.get("sample_tag", ""))
            ws.cell(row=row, column=7, value=entry.get("cycle_id", ""))
            ws.cell(row=row, column=8, value=mutations)
            row += 1
    for i, w in enumerate([8, 20, 32, 10, 8, 12, 18, 60], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row-1}"
    print(f"  Sheet '7D_Reconfig': {row-2} entries")


# ── Sheet 4: Discrepancies ────────────────────────────────────────────────────

def build_discrepancies(wb, sym_accounts, per_sym, account_actives, live_stats, days):
    ws = wb.create_sheet("Discrepancies")
    ws.freeze_panes = "A2"
    headers = ["Symbol", "Side", "Accounts", "Flag", "Detail", "Recommendation"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 1, col, h)

    issues: List[Tuple] = []

    all_configured_keys = set(per_sym.keys())
    for active in account_actives.values():
        all_configured_keys |= set(active.keys())

    for sym, accts in sorted(sym_accounts.items()):
        for side in ("LONG", "SHORT"):
            key = f"{sym}_{side}"
            ps = per_sym.get(key, {})
            # Live aggregated
            total_live = 0
            for acct in accts:
                s = live_stats.get(f"{acct}:{key}", {})
                total_live += s.get("opens", 0) + s.get("closes", 0) + s.get("reentries", 0)

            # Compute best 7D wsharpe for this sym+side across all accounts
            best_7d_ws = None
            for acct in accts:
                e = account_actives.get(acct, {}).get(key, {})
                w = e.get("wsharpe")
                if w is not None and (best_7d_ws is None or w > best_7d_ws):
                    best_7d_ws = w

            # Check: configured but no live — only flag if strong signal
            is_crypto = any(a in CRYPTO_ACCOUNTS for a in accts)
            if key in all_configured_keys and total_live == 0:
                ps_wsh = ps.get("wsharpe") if ps else None
                best_wsh = ps_wsh if ps_wsh is not None else best_7d_ws
                # For crypto: always flag (should trade frequently)
                # For stocks: only flag if wsharpe is meaningful (>= 0.4)
                threshold = 0.0 if is_crypto else 0.4
                if best_wsh is None or best_wsh >= threshold:
                    detail = f"ps_ws={ps_wsh:.3f}" if ps_wsh else f"7d_ws={best_7d_ws:.3f}" if best_7d_ws else "no wsharpe"
                    issues.append((sym, side, ",".join(accts),
                        "CONFIGURED_NO_LIVE_TRADES",
                        detail,
                        "Check if symbol is in tradeable_keys; verify NPZ and engine are firing"))

            # Check: negative per-sym wsharpe but still deployed
            if ps and ps.get("wsharpe", 0) < 0:
                issues.append((sym, side, ",".join(accts),
                    "NEGATIVE_PER_SYM_WSHARPE",
                    f"ws={ps['wsharpe']:.4f} tag={ps.get('winning_tag','')}",
                    "Per-sym override is net-negative; rerun per_sym sweep or revert to baseline"))

            # Check: 7D reconfig negative
            for acct in accts:
                entry = account_actives.get(acct, {}).get(key)
                if entry and entry.get("wsharpe", 0) < 0:
                    issues.append((sym, side, acct,
                        "NEGATIVE_7D_WSHARPE",
                        f"acct={acct} ws={entry['wsharpe']:.4f} trades={entry.get('trades')}",
                        "7D reconfig shows negative edge; monitor closely or force baseline"))

            # Check: in symbols file but completely absent from all configs
            if key not in all_configured_keys and total_live == 0:
                issues.append((sym, side, ",".join(accts),
                    "NO_CONFIG_NO_LIVE",
                    "Not in per_sym_active_config and no live history",
                    "Run per_sym sweep for this symbol; confirm in tradeable_keys"))

            # Check: good paper but rare live
            if best_7d_ws and best_7d_ws >= 1.0 and total_live < 3:
                issues.append((sym, side, ",".join(accts),
                    "STRONG_7D_FEW_LIVE",
                    f"7D ws={best_7d_ws:.3f} but only {total_live} live actions in {days}d",
                    "Signal fires in backtest but not live — check entry gates and symbol status"))

    row = 2
    for sym, side, accts, flag, detail, rec in issues:
        ws.cell(row=row, column=1, value=sym)
        ws.cell(row=row, column=2, value=side)
        ws.cell(row=row, column=3, value=accts)
        c = ws.cell(row=row, column=4, value=flag)
        if "NEGATIVE" in flag or "NO_CONFIG" in flag:
            c.fill = ERR_FILL; c.font = ERR_FONT
        else:
            c.fill = WARN_FILL
        ws.cell(row=row, column=5, value=detail)
        ws.cell(row=row, column=6, value=rec)
        row += 1

    for i, w in enumerate([14, 6, 20, 30, 45, 60], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row-1}"
    print(f"  Sheet 'Discrepancies': {row-2} issues found")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="days of live history to include")
    args = ap.parse_args()

    print(f"Loading data (last {args.days} days of live decisions)...")
    sym_accounts = load_sym_accounts()
    per_sym = load_per_sym_active()
    account_actives = {a: load_account_active(a) for a in ALL_ACCOUNTS}
    account_actives = {k: v for k, v in account_actives.items() if v}
    live_stats = load_live_stats(args.days)

    print(f"  {len(sym_accounts)} unique symbols, {len(per_sym)} per_sym entries")
    print(f"  Account active configs: { {k: len(v) for k,v in account_actives.items()} }")
    print(f"  Live decision keys: {len(live_stats)}")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    print("Building sheets...")
    build_summary(wb, sym_accounts, per_sym, account_actives, live_stats, args.days)
    build_overrides(wb, per_sym)
    build_7d_reconfig(wb, account_actives)
    build_discrepancies(wb, sym_accounts, per_sym, account_actives, live_stats, args.days)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = OUT_DIR / f"SYMBOL_REPORT_{ts}.xlsx"
    wb.save(str(out_path))
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
