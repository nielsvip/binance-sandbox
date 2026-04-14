# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""Aggregate top 1000 positively-tested configs from all sweep CSVs across Local/S1/S2.
Outputs data/TOP1000_SWEEP_RESULTS.xlsx.
"""
import csv
import io
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

BASE = Path(__file__).resolve().parent
OUT = BASE / "data" / "TOP1000_SWEEP_RESULTS.xlsx"

MIN_TRADES = 15
MIN_SHARPE = 0.0

MACHINES = [
    {"name": "Local",  "host": None, "sweep_dir": str(BASE / "backtest_v8" / "sweeps")},
    {"name": "S1",     "host": "s1-int", "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
    {"name": "S2",     "host": "s2-int", "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
]


def _ssh(host, cmd, timeout=30):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", host, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def _list_csvs(m):
    if m["host"] is None:
        d = Path(m["sweep_dir"])
        if not d.exists():
            return []
        return sorted([str(p) for p in d.rglob("v8_sweep_*.csv")])
    raw = _ssh(m["host"], f'find {m["sweep_dir"]} -name "v8_sweep_*.csv" 2>/dev/null')
    return sorted([p.strip() for p in raw.splitlines() if p.strip()])


def _read_csv(m, path):
    if m["host"] is None:
        return open(path).read()
    return _ssh(m["host"], f"cat {path}", timeout=60)


def _parse_rows(raw, machine, csv_path):
    rows, header = [], None
    stem = Path(csv_path).stem
    # Extract metadata from filename: v8_sweep_{system}_{tier}_{Nsym}_{date}_{time}
    m = re.match(r'v8_sweep_(\w+)_(\w+)_(?:(\d+)sym_)?(?:[a-f0-9]+_)?(\d{8})_(\d{4})', stem)
    if m:
        system = m.group(1)   # tradier or crypto
        tier = m.group(2)     # t4, t25, etc.
        n_sym = m.group(3) or "?"
        dt_str = m.group(4) + "_" + m.group(5)
        try:
            run_dt = datetime.strptime(dt_str, "%Y%m%d_%H%M")
        except Exception:
            run_dt = None
    else:
        system = tier = n_sym = "?"
        run_dt = None

    # Data period from filename (symbols count → proxy for backtest scope)
    # t4 = 8 symbols 2yr, t25 = 3-20 symbols, etc. — noted in meta
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("run_id,"):
            header = next(csv.reader(io.StringIO(line)))
            continue
        if header is None:
            continue
        try:
            vals = next(csv.reader(io.StringIO(line)))
            if len(vals) != len(header):
                continue
            row = dict(zip(header, vals))
            if row.get("status") != "ok":
                continue
            sharpe = float(row.get("sharpe", 0) or 0)
            trades = int(row.get("trades", 0) or 0)
            wins = int(row.get("wins", 0) or 0)
            losses = int(row.get("losses", 0) or 0)
            pnl = float(row.get("pnl", 0) or 0)
            elapsed = float(row.get("elapsed", 0) or 0)
            if sharpe <= MIN_SHARPE or trades < MIN_TRADES:
                continue
            win_rate = round(100.0 * wins / (wins + losses), 1) if (wins + losses) > 0 else 0.0
            cfg_cols = {k: v for k, v in row.items() if k.startswith("cfg_")}
            rows.append({
                "sharpe": sharpe,
                "pnl": pnl,
                "trades": trades,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "elapsed_s": elapsed,
                "machine": machine,
                "system": system,
                "tier": tier,
                "n_symbols": n_sym,
                "run_dt": run_dt.strftime("%Y-%m-%d %H:%M") if run_dt else "?",
                "csv_file": Path(csv_path).name,
                "run_id": row.get("run_id", ""),
                "name": row.get("name", ""),
                **cfg_cols,
            })
        except Exception:
            continue
    return rows


def _cfg_key(row):
    """Fingerprint a config by its cfg_* values for deduplication."""
    return tuple(sorted((k, v) for k, v in row.items() if k.startswith("cfg_")))


def main():
    print("Collecting sweep CSVs from all machines...")
    all_rows = []
    for m in MACHINES:
        print(f"  {m['name']}: listing CSVs...")
        try:
            csvs = _list_csvs(m)
        except Exception as e:
            print(f"    ERROR listing: {e}")
            continue
        print(f"    {len(csvs)} CSVs found")
        for path in csvs:
            try:
                raw = _read_csv(m, path)
                rows = _parse_rows(raw, m["name"], path)
                all_rows.extend(rows)
                print(f"    {Path(path).name}: {len(rows)} valid rows")
            except Exception as e:
                print(f"    ERROR {path}: {e}")

    print(f"\nTotal valid rows (sharpe>{MIN_SHARPE}, trades>={MIN_TRADES}): {len(all_rows)}")

    # Deduplicate: for same config fingerprint keep highest sharpe
    best_by_cfg = {}
    for row in all_rows:
        key = _cfg_key(row)
        existing = best_by_cfg.get(key)
        if existing is None or row["sharpe"] > existing["sharpe"]:
            best_by_cfg[key] = row

    deduped = sorted(best_by_cfg.values(), key=lambda r: r["sharpe"], reverse=True)
    print(f"After dedup by config fingerprint: {len(deduped)} unique configs")

    top = deduped[:1000]
    print(f"Writing top {len(top)} to {OUT}")

    # Collect all cfg_* column names across all rows
    cfg_cols = []
    seen_cfg = set()
    for row in top:
        for k in row:
            if k.startswith("cfg_") and k not in seen_cfg:
                seen_cfg.add(k)
                cfg_cols.append(k)
    cfg_cols.sort()

    # Clean cfg column display names
    def cfg_label(c):
        n = c[4:]  # strip cfg_
        # strip common prefix/suffix patterns for readability
        n = re.sub(r'_TRADIER$', '', n)
        n = re.sub(r'^TRADIER_', '', n)
        return n

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Top 1000 Configs"

    # Header style
    hdr_fill = PatternFill("solid", fgColor="1F3864")
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Fixed columns
    fixed = [
        ("Rank", 6), ("Sharpe", 9), ("PnL ($)", 10), ("Trades", 8),
        ("Wins", 7), ("Losses", 7), ("Win Rate %", 10), ("Elapsed (s)", 11),
        ("Machine", 9), ("System", 9), ("Tier", 7), ("Symbols", 8),
        ("Run Date", 13), ("Run ID", 22), ("Config Name", 40), ("CSV File", 38),
    ]
    cfg_widths = [(cfg_label(c), max(14, len(cfg_label(c)) + 2)) for c in cfg_cols]

    headers = [h for h, _ in fixed] + [h for h, _ in cfg_widths]
    widths  = [w for _, w in fixed] + [w for _, w in cfg_widths]

    for col_idx, (hdr, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=1, column=col_idx, value=hdr)
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = hdr_align
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[1].height = 36
    ws.freeze_panes = "A2"

    # Data fills — green gradient by sharpe rank
    fills = {
        "top10":   PatternFill("solid", fgColor="C6EFCE"),
        "top50":   PatternFill("solid", fgColor="E2EFDA"),
        "top200":  PatternFill("solid", fgColor="F2F7EE"),
        "rest":    None,
    }

    for rank, row in enumerate(top, start=1):
        excel_row = rank + 1
        fill = (fills["top10"] if rank <= 10 else
                fills["top50"] if rank <= 50 else
                fills["top200"] if rank <= 200 else None)

        values = [
            rank,
            row["sharpe"],
            row["pnl"],
            row["trades"],
            row["wins"],
            row["losses"],
            row["win_rate"],
            row["elapsed_s"],
            row["machine"],
            row["system"],
            row["tier"],
            row["n_symbols"],
            row["run_dt"],
            row["run_id"],
            row["name"],
            row["csv_file"],
        ] + [row.get(c, "") for c in cfg_cols]

        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=excel_row, column=col_idx, value=val)
            if fill:
                cell.fill = fill
            if col_idx in (2, 3, 7):  # Sharpe, PnL, WinRate
                cell.number_format = "0.000" if col_idx == 2 else ("$#,##0.00" if col_idx == 3 else "0.0")
            cell.alignment = Alignment(horizontal="center" if col_idx <= 16 else "left")

    # Add summary sheet
    ws2 = wb.create_sheet("Summary")
    ws2["A1"] = "TOP 1000 SWEEP RESULTS SUMMARY"
    ws2["A1"].font = Font(bold=True, size=14)
    ws2["A3"] = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"
    ws2["A4"] = f"Total unique configs tested: {len(deduped)}"
    ws2["A5"] = f"Filters: status=ok, sharpe>{MIN_SHARPE}, trades>={MIN_TRADES}"
    ws2["A6"] = f"Dedup: highest sharpe per config fingerprint"
    ws2["A8"] = "Machines:"
    for i, m in enumerate(MACHINES):
        ws2[f"A{9+i}"] = f"  {m['name']}: {m['sweep_dir']}"
    ws2["A13"] = "Top 10 configs:"
    ws2["A14"] = "Rank | Sharpe | Trades | Win% | PnL | Config Name"
    for i, row in enumerate(top[:10]):
        ws2[f"A{15+i}"] = f"#{i+1} | {row['sharpe']:.3f} | {row['trades']} | {row['win_rate']:.1f}% | ${row['pnl']:.2f} | {row['name']}"

    for col in ["A"]:
        ws2.column_dimensions[col].width = 90

    wb.save(OUT)
    print(f"\nSaved: {OUT}")
    print(f"\nTop 10:")
    for i, row in enumerate(top[:10], 1):
        print(f"  #{i:3d} sharpe={row['sharpe']:.3f} trades={row['trades']:4d} wr={row['win_rate']:.1f}% pnl=${row['pnl']:.2f}  {row['machine']}/{row['tier']}  {row['name'][:60]}")


if __name__ == "__main__":
    main()
