"""
build_advanced_exit_audit.py
Comprehensive Excel audit of live history + decisions + backtest sweep CSVs.
Produces history_audit_advanced_20260514.xlsx.

Key design:
- Uses reason_bucket (normalized, numbers stripped) for stats grouping
- Also produces raw-reason breakdown (top unique reasons)
- Sources: data/history/ (OPEN/CLOSE/AUGMENT events), data/decisions/ (CLOSE/REDUCE with gain in reason),
  data/sweep_results/*_trades.jsonl (backtest trades with pnl_pct)
- P&L extracted from: pnl_pct field (backtest) or embedded gain in reason string (decisions)
"""
import os
import re
import json
import glob
from datetime import datetime

import pandas as pd
import numpy as np
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

BASE_PATH = "/Users/niels/Documents/binance"
HISTORY_PATH = os.path.join(BASE_PATH, "data", "history")
DECISIONS_PATH = os.path.join(BASE_PATH, "data", "decisions")
SWEEP_PATH = os.path.join(BASE_PATH, "data", "sweep_results")
OUTPUT_PATH = os.path.join(BASE_PATH, "history_audit_advanced_20260514.xlsx")

CRYPTO_ACCOUNTS = {"ang", "inf", "flz", "men", "fin"}
TRADIER_ACCOUNTS = {"trb", "trc"}
ALL_ACCOUNTS = CRYPTO_ACCOUNTS | TRADIER_ACCOUNTS

GAIN_PATTERNS = [
    re.compile(r'gain\+([+-]?\d+\.?\d*)'),
    re.compile(r'gain([+-]\d+\.?\d*)'),
    re.compile(r'gain=([+-]?\d+\.?\d*)'),
    re.compile(r'_g([+-]?\d+\.?\d*)[%_]'),
    re.compile(r'_g([+-]?\d+\.?\d*)$'),
    re.compile(r'hgain([+-]?\d+\.?\d*)%'),
    re.compile(r'g=([+-]?\d+\.?\d*)'),
    re.compile(r'g(\d+\.?\d*)%'),
]


def extract_gain_from_reason(reason: str):
    if not reason:
        return None
    for pat in GAIN_PATTERNS:
        m = pat.search(reason)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def normalize_reason(reason: str) -> str:
    """Strip numeric tokens to get a canonical bucket, preserving structure."""
    if not reason:
        return "UNKNOWN"
    r = re.sub(r'[+-]?\d+\.\d+', 'N', reason)
    r = re.sub(r'[+-]?\d+', 'N', r)
    r = r.strip('_ ')
    return r[:120]


def parse_history_files():
    """Parse all JSONL files from the history folder."""
    rows = []
    errors = 0
    total_lines = 0

    for acct in sorted(os.listdir(HISTORY_PATH)):
        acct_dir = os.path.join(HISTORY_PATH, acct)
        if not os.path.isdir(acct_dir) or acct not in ALL_ACCOUNTS:
            continue
        asset_class = "crypto" if acct in CRYPTO_ACCOUNTS else "tradier"

        for fn in sorted(os.listdir(acct_dir)):
            if not fn.endswith(".jsonl"):
                continue
            base = fn[:-6]
            if "_LONG" in base:
                symbol = base[: base.rfind("_LONG")]
                side = "LONG"
            elif "_SHORT" in base:
                symbol = base[: base.rfind("_SHORT")]
                side = "SHORT"
            else:
                symbol = base
                side = "UNKNOWN"

            fp = os.path.join(acct_dir, fn)
            with open(fp, "r", errors="replace") as fh:
                for line in fh:
                    total_lines += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        errors += 1
                        continue
                    if not isinstance(d, dict):
                        errors += 1
                        continue

                    action = d.get("type") or d.get("action") or ""
                    reason = d.get("reason") or ""
                    ts_raw = d.get("ts") or d.get("timestamp") or ""
                    price = d.get("price")
                    qty = d.get("qty")
                    value = d.get("value")

                    pnl_pct = (
                        d.get("pnl_pct")
                        or d.get("gain_pct")
                        or d.get("pct_gain")
                        or d.get("profit_pct")
                    )
                    if pnl_pct is None and action in ("CLOSE", "REDUCE"):
                        pnl_pct = extract_gain_from_reason(reason)

                    rows.append({
                        "account": acct,
                        "asset_class": asset_class,
                        "symbol": symbol,
                        "side": side,
                        "action": action,
                        "reason": reason,
                        "reason_bucket": normalize_reason(reason),
                        "pnl_pct": pnl_pct,
                        "price": price,
                        "qty": qty,
                        "value": value,
                        "ts": ts_raw,
                        "source": "history",
                    })

    print(f"[history] Parsed {total_lines:,} lines → {len(rows):,} events, {errors} errors")
    return pd.DataFrame(rows), total_lines, errors


def parse_decisions_files():
    """Parse decisions JSONL files - focus on CLOSE/REDUCE events."""
    rows = []
    errors = 0
    total_lines = 0

    for fn in sorted(os.listdir(DECISIONS_PATH)):
        if not fn.endswith(".jsonl") or not fn.startswith("decisions_"):
            continue
        parts = fn[:-6].split("_")
        if len(parts) < 3:
            continue
        acct = parts[1]
        if acct not in ALL_ACCOUNTS:
            continue
        asset_class = "crypto" if acct in CRYPTO_ACCOUNTS else "tradier"

        fp = os.path.join(DECISIONS_PATH, fn)
        with open(fp, "r", errors="replace") as fh:
            for line in fh:
                total_lines += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    continue
                if not isinstance(d, dict):
                    errors += 1
                    continue

                action = d.get("action", "")
                if action not in ("CLOSE", "REDUCE", "OPEN", "QUICK_OPEN",
                                  "AUGMENT", "HEDGE_OPEN", "HEDGE_CLOSE"):
                    continue

                reason = d.get("reason", "")
                ts_raw = d.get("timestamp", "")
                pk = d.get("position_key", "")
                account_from_pk = pk.split(":")[0] if ":" in pk else acct
                sym_side = pk.split(":")[1] if ":" in pk else ""

                if "_LONG" in sym_side:
                    symbol = sym_side[: sym_side.rfind("_LONG")]
                    side = "LONG"
                elif "_SHORT" in sym_side:
                    symbol = sym_side[: sym_side.rfind("_SHORT")]
                    side = "SHORT"
                else:
                    symbol = sym_side
                    side = "UNKNOWN"

                snap = d.get("snapshot", {}) or {}
                price = snap.get("price") if isinstance(snap, dict) else None

                pnl_pct = extract_gain_from_reason(reason)

                rows.append({
                    "account": account_from_pk or acct,
                    "asset_class": asset_class,
                    "symbol": symbol,
                    "side": side,
                    "action": action,
                    "reason": reason,
                    "reason_bucket": normalize_reason(reason),
                    "pnl_pct": pnl_pct,
                    "price": price,
                    "ts": ts_raw,
                    "source": "decisions",
                })

    print(f"[decisions] Parsed {total_lines:,} lines → {len(rows):,} relevant events, {errors} errors")
    return pd.DataFrame(rows), total_lines, errors


def parse_backtest_trades():
    """Parse recent vec_sweep trades JSONL files."""
    rows = []
    trade_files = glob.glob(os.path.join(SWEEP_PATH, "*_trades.jsonl"))
    trade_files.sort(key=os.path.getmtime, reverse=True)
    trade_files = trade_files[:5]

    for fp in trade_files:
        fn = os.path.basename(fp)
        with open(fp, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):
                    continue
                action = d.get("type", "")
                reason = d.get("reason", "")
                rows.append({
                    "symbol": d.get("symbol", ""),
                    "side": d.get("side", ""),
                    "action": action,
                    "reason": reason,
                    "reason_bucket": normalize_reason(reason),
                    "pnl_pct": d.get("pnl_pct"),
                    "source_file": fn,
                })

    print(f"[backtest_trades] {len(rows):,} events from {len(trade_files)} files")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def parse_sweep_csvs():
    """Parse last 10 sweep CSVs."""
    csv_files = glob.glob(os.path.join(SWEEP_PATH, "*.csv"))
    csv_files.sort(key=os.path.getmtime, reverse=True)
    csv_files = csv_files[:10]
    dfs = []
    for fp in csv_files:
        try:
            df = pd.read_csv(fp, nrows=500)
            df["source_file"] = os.path.basename(fp)
            dfs.append(df)
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True, sort=False)
    print(f"[sweep_csvs] {len(combined):,} rows from {len(dfs)} CSV files")
    return combined


def compute_exit_stats(df: pd.DataFrame, group_col="reason_bucket"):
    """Compute per-reason_bucket exit stats from a DataFrame of CLOSE/REDUCE events."""
    closes = df[df["action"].isin(("CLOSE", "REDUCE"))].copy()
    if closes.empty:
        return pd.DataFrame()

    closes["pnl_pct"] = pd.to_numeric(closes["pnl_pct"], errors="coerce")
    closes["is_win"] = closes["pnl_pct"] > 0

    agg = closes.groupby(group_col).agg(
        count=(group_col, "count"),
        win_count=("is_win", "sum"),
        loss_count=("is_win", lambda x: (~x).sum()),
        avg_pnl_pct=("pnl_pct", "mean"),
        median_pnl_pct=("pnl_pct", "median"),
        total_pnl_pct=("pnl_pct", "sum"),
        p25_pnl=("pnl_pct", lambda x: x.quantile(0.25)),
        p75_pnl=("pnl_pct", lambda x: x.quantile(0.75)),
        pnl_available=("pnl_pct", lambda x: x.notna().sum()),
    ).reset_index()

    agg.rename(columns={group_col: "reason_bucket"}, inplace=True)
    agg["win_rate"] = (agg["win_count"] / agg["count"] * 100).round(1)
    agg["pnl_coverage_pct"] = (agg["pnl_available"] / agg["count"] * 100).round(1)
    agg = agg.sort_values("count", ascending=False).reset_index(drop=True)

    for col in ["avg_pnl_pct", "median_pnl_pct", "total_pnl_pct", "p25_pnl", "p75_pnl"]:
        agg[col] = agg[col].round(4)

    # Column order
    cols = ["reason_bucket", "count", "win_count", "loss_count", "win_rate",
            "avg_pnl_pct", "median_pnl_pct", "total_pnl_pct", "p25_pnl", "p75_pnl",
            "pnl_available", "pnl_coverage_pct"]
    return agg[cols]


def compute_raw_exit_stats(df: pd.DataFrame, top_n=200):
    """Per raw-reason stats (top N by count)."""
    closes = df[df["action"].isin(("CLOSE", "REDUCE"))].copy()
    if closes.empty:
        return pd.DataFrame()

    closes["pnl_pct"] = pd.to_numeric(closes["pnl_pct"], errors="coerce")
    closes["is_win"] = closes["pnl_pct"] > 0

    agg = closes.groupby("reason").agg(
        count=("reason", "count"),
        win_count=("is_win", "sum"),
        avg_pnl_pct=("pnl_pct", "mean"),
        total_pnl_pct=("pnl_pct", "sum"),
        pnl_available=("pnl_pct", lambda x: x.notna().sum()),
    ).reset_index()

    agg["win_rate"] = (agg["win_count"] / agg["count"] * 100).round(1)
    agg["pnl_coverage_pct"] = (agg["pnl_available"] / agg["count"] * 100).round(1)
    agg["avg_pnl_pct"] = agg["avg_pnl_pct"].round(4)
    agg["total_pnl_pct"] = agg["total_pnl_pct"].round(4)
    agg = agg.sort_values("count", ascending=False).head(top_n).reset_index(drop=True)
    return agg


def compute_entry_stats(df: pd.DataFrame):
    """Compute per-reason entry stats."""
    opens = df[df["action"].isin(("OPEN", "AUGMENT", "QUICK_OPEN",
                                   "HEDGE_OPEN", "HEDGE_CLOSE"))].copy()
    if opens.empty:
        return pd.DataFrame()

    agg = opens.groupby(["action", "reason_bucket"]).agg(
        count=("reason_bucket", "count"),
        symbols=("symbol", lambda x: x.nunique()),
        accounts=("account", lambda x: ", ".join(sorted(x.unique())[:5])),
    ).reset_index()
    agg = agg.sort_values("count", ascending=False).reset_index(drop=True)
    return agg


def compute_per_account_reason(df: pd.DataFrame):
    """Breakdown by account × reason_bucket for CLOSE events."""
    closes = df[df["action"].isin(("CLOSE", "REDUCE"))].copy()
    if closes.empty:
        return pd.DataFrame()

    closes["pnl_pct"] = pd.to_numeric(closes["pnl_pct"], errors="coerce")
    closes["is_win"] = closes["pnl_pct"] > 0

    agg = closes.groupby(["account", "asset_class", "reason_bucket"]).agg(
        count=("reason_bucket", "count"),
        win_count=("is_win", "sum"),
        avg_pnl_pct=("pnl_pct", "mean"),
        total_pnl_pct=("pnl_pct", "sum"),
        pnl_available=("pnl_pct", lambda x: x.notna().sum()),
    ).reset_index()
    agg["win_rate"] = (agg["win_count"] / agg["count"] * 100).round(1)
    agg["avg_pnl_pct"] = agg["avg_pnl_pct"].round(4)
    agg["total_pnl_pct"] = agg["total_pnl_pct"].round(4)
    agg = agg.sort_values(["account", "count"], ascending=[True, False]).reset_index(drop=True)
    return agg


def compute_parity_gaps(live_df: pd.DataFrame, bt_df: pd.DataFrame):
    """Compare normalized exit reason buckets between live and backtest."""
    live_close = live_df[live_df["action"].isin(("CLOSE", "REDUCE"))]
    live_buckets = set(live_close["reason_bucket"].dropna().unique())

    if not bt_df.empty:
        bt_close = bt_df[bt_df["action"].isin(("CLOSE", "REDUCE"))]
        bt_buckets = set(bt_close["reason_bucket"].dropna().unique())
    else:
        bt_buckets = set()

    live_only = sorted(live_buckets - bt_buckets)
    bt_only = sorted(bt_buckets - live_buckets)
    common = sorted(live_buckets & bt_buckets)

    print(f"[parity] live_only={len(live_only)}, bt_only={len(bt_only)}, common={len(common)}")

    max_len = max(len(live_only), len(bt_only), len(common), 1)
    rows = []
    for i in range(max_len):
        rows.append({
            "live_only_reasons (not in backtest)": live_only[i] if i < len(live_only) else "",
            "backtest_only_reasons (not in live)": bt_only[i] if i < len(bt_only) else "",
            "common_reasons (both live+backtest)": common[i] if i < len(common) else "",
        })
    return pd.DataFrame(rows)


# ── Excel helpers ──────────────────────────────────────────────────────────────

GREEN_FILL = PatternFill("solid", fgColor="C6EFCE")
YELLOW_FILL = PatternFill("solid", fgColor="FFEB9C")
RED_FILL = PatternFill("solid", fgColor="FFC7CE")
NEUTRAL_FILL = PatternFill("solid", fgColor="F2F2F2")
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
BOLD_FONT = Font(bold=True)


def style_sheet(ws, headers, color_col_win_rate=None, color_col_avg_pnl=None):
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    ws.freeze_panes = "A2"

    wr_idx = (headers.index(color_col_win_rate) + 1
               if color_col_win_rate and color_col_win_rate in headers else None)
    pnl_idx = (headers.index(color_col_avg_pnl) + 1
                if color_col_avg_pnl and color_col_avg_pnl in headers else None)

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, max_row=ws.max_row), start=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=False)
        if wr_idx:
            wr_cell = ws.cell(row=row_idx, column=wr_idx)
            try:
                val = float(wr_cell.value)
                if val > 70:
                    wr_cell.fill = GREEN_FILL
                elif val >= 50:
                    wr_cell.fill = YELLOW_FILL
                else:
                    wr_cell.fill = RED_FILL
            except (TypeError, ValueError):
                pass
        if pnl_idx:
            pnl_cell = ws.cell(row=row_idx, column=pnl_idx)
            try:
                val = float(pnl_cell.value)
                pnl_cell.fill = GREEN_FILL if val > 0 else RED_FILL
            except (TypeError, ValueError):
                pass

    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            if cell.value is not None:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 8), 70)


def df_to_sheet(wb, sheet_name: str, df: pd.DataFrame,
                color_col_win_rate=None, color_col_avg_pnl=None):
    ws = wb.create_sheet(title=sheet_name[:31])
    if df.empty:
        ws.append(["No data"])
        return ws

    display_df = df.fillna("").copy()
    headers = list(display_df.columns)
    ws.append(headers)
    for row in display_df.itertuples(index=False):
        ws.append(list(row))

    style_sheet(ws, headers,
                color_col_win_rate=color_col_win_rate,
                color_col_avg_pnl=color_col_avg_pnl)
    return ws


def main():
    print("=" * 60)
    print("Advanced Exit/Entry Audit — building Excel report")
    print(f"Output: {OUTPUT_PATH}")
    print("=" * 60)

    # ── 1. Parse sources ──────────────────────────────────────────
    hist_df, hist_lines, hist_errors = parse_history_files()
    dec_df, dec_lines, dec_errors = parse_decisions_files()
    bt_df = parse_backtest_trades()
    sweep_csv_df = parse_sweep_csvs()

    # ── 2. Combine history + decisions ───────────────────────────
    combined_df = pd.concat([hist_df, dec_df], ignore_index=True, sort=False)
    combined_df["pnl_pct"] = pd.to_numeric(combined_df["pnl_pct"], errors="coerce")

    # Deduplicate: history and decisions may overlap on CLOSE events
    # Keep history rows preferentially; for decisions, only keep ones without a
    # matching history row (same account+symbol+side+action+ts prefix)
    # Simple approach: keep all but mark source clearly

    crypto_df = combined_df[combined_df["asset_class"] == "crypto"]
    tradier_df = combined_df[combined_df["asset_class"] == "tradier"]

    # ── 3. Compute stats ──────────────────────────────────────────
    # Normalized bucket stats (main analysis)
    crypto_exit = compute_exit_stats(crypto_df)
    tradier_exit = compute_exit_stats(tradier_df)

    # Raw reason stats (top 200)
    crypto_exit_raw = compute_raw_exit_stats(crypto_df, top_n=200)
    tradier_exit_raw = compute_raw_exit_stats(tradier_df, top_n=200)

    # Entry stats
    crypto_entry = compute_entry_stats(crypto_df)
    tradier_entry = compute_entry_stats(tradier_df)

    # Per-account breakdown
    per_account = compute_per_account_reason(combined_df)

    # Parity gaps
    parity = compute_parity_gaps(combined_df, bt_df)

    # Backtest exit stats
    bt_exit_stats = compute_exit_stats(bt_df) if not bt_df.empty else pd.DataFrame()

    # ── 4. Source breakdown ───────────────────────────────────────
    # Per-source per-asset_class action count
    source_summary_rows = []
    for (src, ac, act), grp in combined_df.groupby(["source", "asset_class", "action"]):
        source_summary_rows.append({
            "source": src,
            "asset_class": ac,
            "action": act,
            "count": len(grp),
            "with_pnl": grp["pnl_pct"].notna().sum(),
        })
    source_summary = pd.DataFrame(source_summary_rows).sort_values(
        ["source", "asset_class", "count"], ascending=[True, True, False]
    ).reset_index(drop=True)

    # ── 5. Raw data sample ────────────────────────────────────────
    raw_sample = combined_df.head(1000).copy()
    raw_cols = ["account", "asset_class", "symbol", "side", "action",
                "reason", "reason_bucket", "pnl_pct", "ts", "source"]
    raw_sample = raw_sample[[c for c in raw_cols if c in raw_sample.columns]]

    # ── 6. Build Excel ────────────────────────────────────────────
    print("\nBuilding Excel workbook...")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Metadata sheet (first)
    ws_meta = wb.create_sheet(title="Metadata", index=0)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    meta_rows = [
        ["Field", "Value"],
        ["Generated", now],
        ["", ""],
        ["=== DATA SOURCES ===", ""],
        ["History lines parsed", hist_lines],
        ["History events", len(hist_df)],
        ["History errors", hist_errors],
        ["Decisions events (CLOSE/REDUCE/OPEN/AUGMENT)", len(dec_df)],
        ["Decisions errors", dec_errors],
        ["Combined events total", len(combined_df)],
        ["", ""],
        ["=== SPLIT BY ASSET CLASS ===", ""],
        ["Crypto events", len(crypto_df)],
        ["Tradier events", len(tradier_df)],
        ["", ""],
        ["=== BACKTEST ===", ""],
        ["Backtest trade events", len(bt_df) if not bt_df.empty else 0],
        ["Sweep CSV rows loaded", len(sweep_csv_df) if not sweep_csv_df.empty else 0],
        ["", ""],
        ["=== UNIQUE REASON BUCKETS ===", ""],
        ["Crypto exit reason buckets", len(crypto_exit)],
        ["Tradier exit reason buckets", len(tradier_exit)],
        ["Crypto entry reason buckets", len(crypto_entry)],
        ["Tradier entry reason buckets", len(tradier_entry)],
        ["", ""],
        ["=== NOTE ON P&L ===", ""],
        ["P&L source", "Extracted from reason string (gain/g= patterns)"],
        ["History CLOSE events w/o P&L field", "P&L extracted from reason string where possible"],
        ["Backtest pnl_pct", "Direct field from *_trades.jsonl files"],
    ]
    for row in meta_rows:
        ws_meta.append(row)

    ws_meta["A1"].fill = HEADER_FILL
    ws_meta["A1"].font = HEADER_FONT
    ws_meta["B1"].fill = HEADER_FILL
    ws_meta["B1"].font = HEADER_FONT
    ws_meta.column_dimensions["A"].width = 45
    ws_meta.column_dimensions["B"].width = 35
    ws_meta.freeze_panes = "A2"

    # Main sheets
    df_to_sheet(wb, "Crypto Exit Paths (Buckets)", crypto_exit,
                color_col_win_rate="win_rate",
                color_col_avg_pnl="avg_pnl_pct")

    df_to_sheet(wb, "Tradier Exit Paths (Buckets)", tradier_exit,
                color_col_win_rate="win_rate",
                color_col_avg_pnl="avg_pnl_pct")

    df_to_sheet(wb, "Crypto Exit Paths (Raw)", crypto_exit_raw,
                color_col_win_rate="win_rate",
                color_col_avg_pnl="avg_pnl_pct")

    df_to_sheet(wb, "Tradier Exit Paths (Raw)", tradier_exit_raw,
                color_col_win_rate="win_rate",
                color_col_avg_pnl="avg_pnl_pct")

    df_to_sheet(wb, "Crypto Entry Paths", crypto_entry)
    df_to_sheet(wb, "Tradier Entry Paths", tradier_entry)

    df_to_sheet(wb, "Per-Account Summary", per_account,
                color_col_win_rate="win_rate",
                color_col_avg_pnl="avg_pnl_pct")

    df_to_sheet(wb, "Parity Gaps", parity)

    if not bt_exit_stats.empty:
        df_to_sheet(wb, "Backtest Exit Buckets", bt_exit_stats,
                    color_col_win_rate="win_rate",
                    color_col_avg_pnl="avg_pnl_pct")

    if not sweep_csv_df.empty:
        df_to_sheet(wb, "Sweep CSV Summary", sweep_csv_df.head(500))

    df_to_sheet(wb, "Source Breakdown", source_summary)
    df_to_sheet(wb, "Raw Data Sample", raw_sample)

    wb.save(OUTPUT_PATH)
    print(f"\nSaved: {OUTPUT_PATH}")

    # ── Summary printout ──────────────────────────────────────────
    close_events = combined_df[combined_df["action"].isin(("CLOSE", "REDUCE"))]

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total events parsed (history + decisions): {len(combined_df):,}")
    print(f"  History events:    {len(hist_df):,} (from {hist_lines:,} lines)")
    print(f"  Decisions events:  {len(dec_df):,}")
    print(f"  CLOSE/REDUCE total:{len(close_events):,}")
    print(f"  Crypto events:     {len(crypto_df):,}")
    print(f"  Tradier events:    {len(tradier_df):,}")

    if not crypto_exit.empty:
        print("\nTop 5 crypto exit REASON BUCKETS by count:")
        for _, row in crypto_exit.head(5).iterrows():
            wr = f"{row['win_rate']:.1f}%" if pd.notna(row['win_rate']) and row['win_rate'] else "n/a"
            avg = f"{row['avg_pnl_pct']:.3f}%" if pd.notna(row['avg_pnl_pct']) and row['avg_pnl_pct'] else "n/a"
            cov = f"(P&L cov {row['pnl_coverage_pct']:.0f}%)"
            print(f"  [{row['count']:6d}] WR={wr:6s} avg={avg:8s} {cov} | {row['reason_bucket'][:70]}")

    if not tradier_exit.empty:
        print("\nTop 5 tradier exit REASON BUCKETS by count:")
        for _, row in tradier_exit.head(5).iterrows():
            wr = f"{row['win_rate']:.1f}%" if pd.notna(row['win_rate']) and row['win_rate'] else "n/a"
            avg = f"{row['avg_pnl_pct']:.3f}%" if pd.notna(row['avg_pnl_pct']) and row['avg_pnl_pct'] else "n/a"
            cov = f"(P&L cov {row['pnl_coverage_pct']:.0f}%)"
            print(f"  [{row['count']:6d}] WR={wr:6s} avg={avg:8s} {cov} | {row['reason_bucket'][:70]}")

    if not bt_exit_stats.empty:
        print("\nTop 5 BACKTEST exit reason buckets:")
        for _, row in bt_exit_stats.head(5).iterrows():
            wr = f"{row['win_rate']:.1f}%" if pd.notna(row['win_rate']) else "n/a"
            avg = f"{row['avg_pnl_pct']:.3f}%" if pd.notna(row['avg_pnl_pct']) else "n/a"
            print(f"  [{row['count']:6d}] WR={wr:6s} avg={avg:8s} | {row['reason_bucket'][:70]}")

    if not parity.empty:
        n_live_only = (parity.iloc[:, 0] != "").sum()
        n_bt_only = (parity.iloc[:, 1] != "").sum()
        n_common = (parity.iloc[:, 2] != "").sum()
        print(f"\nParity gaps (normalized buckets):")
        print(f"  live-only={n_live_only}, backtest-only={n_bt_only}, common={n_common}")

    if hist_errors > 0:
        print(f"\nWarnings: {hist_errors} malformed JSON lines skipped in history")
    if dec_errors > 0:
        print(f"          {dec_errors} malformed JSON lines skipped in decisions")

    print("\nDone.")


if __name__ == "__main__":
    main()
