#!/usr/bin/env python3
"""
consolidate_results_db.py — Import all backtest/sweep results into a single SQLite DB.

Tables:
  vec_mass_signals          — best signal combos (filtered: sharpe>=1.5, trades>=30)
  v8_quick_runs             — Tier-1 quick sweep configs (cfg stored as JSON blob)
  v8_sweep_runs             — Tier-2 full backtest engine sweep configs
  autonomous_iters          — autonomous_search.py iterations
  backtest_v8_run_summaries — per-JSONL-file summaries (no raw per-trade storage)
  import_log                — crash-resumable file tracking

Usage:
  python3 consolidate_results_db.py [--delete-after] [--skip-jsonl] [--export-csvs] [--dry-run]

After import, source files can be deleted with --delete-after.
JSONL files are summarized (not stored per-trade); use --skip-jsonl to skip them.
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SANDBOX = Path("/home/niels/binance-sandbox")
DB_PATH = SANDBOX / "data" / "results_db.sqlite"
SWEEP_RESULTS = SANDBOX / "data" / "sweep_results"
AUTONOMOUS = SANDBOX / "data" / "autonomous"
V8_LOGS = SANDBOX / "backtest_v8" / "logs"
V8_SWEEPS = SANDBOX / "backtest_v8" / "sweeps"
EXPORT_DIR = SANDBOX / "data" / "results_db_export"

VEC_MASS_SHARPE_MIN = 1.5
VEC_MASS_TRADES_MIN = 30


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")
    return conn


def create_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS import_log (
        id           INTEGER PRIMARY KEY,
        file_path    TEXT UNIQUE,
        file_size    INTEGER,
        file_mtime   REAL,
        table_name   TEXT,
        rows_imported INTEGER,
        status       TEXT,
        imported_at  TEXT
    );

    CREATE TABLE IF NOT EXISTS vec_mass_signals (
        id          INTEGER PRIMARY KEY,
        source_file TEXT,
        mode        TEXT,
        run_ts      INTEGER,
        side        TEXT,
        combo       TEXT,
        horizon     INTEGER,
        n_trades    INTEGER,
        sharpe      REAL,
        wr          REAL,
        mean_ret    REAL,
        std         REAL,
        pf          REAL,
        imported_at TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_vec_mass
        ON vec_mass_signals(mode, side, combo, horizon);

    CREATE TABLE IF NOT EXISTS v8_quick_runs (
        id               INTEGER PRIMARY KEY,
        source_file      TEXT,
        sweep_name       TEXT,
        mode             TEXT,
        n_syms           INTEGER,
        date_label       TEXT,
        run_id           TEXT,
        config_hash      TEXT,
        sharpe           REAL,
        sharpe_min       REAL,
        sharpe_p25       REAL,
        sharpe_med       REAL,
        sharpe_p75       REAL,
        sharpe_max       REAL,
        syms_with_sharpe INTEGER,
        pnl              REAL,
        trades           INTEGER,
        wins             INTEGER,
        losses           INTEGER,
        wr               REAL,
        avg_pnl_pct      REAL,
        elapsed          REAL,
        status           TEXT,
        early_abort      INTEGER,
        symbols_used     INTEGER,
        cfg_json         TEXT,
        imported_at      TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_v8q_hash   ON v8_quick_runs(config_hash);
    CREATE INDEX IF NOT EXISTS ix_v8q_sharpe ON v8_quick_runs(sharpe DESC);
    CREATE INDEX IF NOT EXISTS ix_v8q_mode   ON v8_quick_runs(mode, sweep_name);

    CREATE TABLE IF NOT EXISTS v8_sweep_runs (
        id          INTEGER PRIMARY KEY,
        source_file TEXT,
        sweep_name  TEXT,
        mode        TEXT,
        run_id      TEXT,
        name        TEXT,
        sharpe      REAL,
        pnl         REAL,
        trades      INTEGER,
        wins        INTEGER,
        losses      INTEGER,
        elapsed     REAL,
        status      TEXT,
        cfg_json    TEXT,
        imported_at TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_v8s_sharpe ON v8_sweep_runs(sharpe DESC);
    CREATE INDEX IF NOT EXISTS ix_v8s_mode   ON v8_sweep_runs(mode, sweep_name);

    CREATE TABLE IF NOT EXISTS autonomous_iters (
        id              INTEGER PRIMARY KEY,
        source_dir      TEXT,
        worker          TEXT,
        mode            TEXT,
        iter            INTEGER,
        pool_sharpe     REAL,
        deflated_sharpe REAL,
        psr             REAL,
        sym_sharpe      REAL,
        acc_gain_pct    REAL,
        gain_sym_yr     REAL,
        avg_gain_trade  REAL,
        gain_per_yr     REAL,
        max_dd_pct      REAL,
        trades          INTEGER,
        gain_vs_bh      REAL,
        elapsed_s       REAL,
        overrides_count INTEGER,
        reliable        INTEGER,
        useless         INTEGER,
        overrides_json  TEXT,
        imported_at     TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_auto
        ON autonomous_iters(source_dir, worker, iter);
    CREATE INDEX IF NOT EXISTS ix_auto_sharpe ON autonomous_iters(pool_sharpe DESC);
    CREATE INDEX IF NOT EXISTS ix_auto_dir    ON autonomous_iters(source_dir, mode);

    CREATE TABLE IF NOT EXISTS backtest_v8_run_summaries (
        id               INTEGER PRIMARY KEY,
        source_file      TEXT UNIQUE,
        run_name         TEXT,
        mode             TEXT,
        account          TEXT,
        n_events         INTEGER,
        n_buys           INTEGER,
        n_sells          INTEGER,
        n_full_closes    INTEGER,
        unique_positions INTEGER,
        date_min         TEXT,
        date_max         TEXT,
        imported_at      TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_v8j_mode ON backtest_v8_run_summaries(mode, account);
    """)
    conn.commit()


def already_done(conn, file_path):
    row = conn.execute(
        "SELECT status FROM import_log WHERE file_path=?", (str(file_path),)
    ).fetchone()
    return row is not None and row[0] == "done"


def log_import(conn, file_path, table_name, rows, status):
    try:
        stat = os.stat(file_path)
        size, mtime = stat.st_size, stat.st_mtime
    except FileNotFoundError:
        size, mtime = 0, 0.0
    conn.execute("""
        INSERT OR REPLACE INTO import_log
            (file_path, file_size, file_mtime, table_name, rows_imported, status, imported_at)
        VALUES (?,?,?,?,?,?,?)
    """, (str(file_path), size, mtime, table_name, rows, status, now_iso()))
    conn.commit()


def safe_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def safe_int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# vec_mass_signals
# ---------------------------------------------------------------------------

def import_vec_mass(conn, path: Path, delete_after: bool, dry_run: bool):
    if already_done(conn, path):
        return
    mode = "tradier" if "tradier" in path.name else "crypto"
    m = re.search(r"_(\d{8,})\.csv$", path.name)
    run_ts = int(m.group(1)) if m else 0
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if "sharpe" not in (reader.fieldnames or []):
                log_import(conn, path, "vec_mass_signals", 0, "skip_schema")
                return
            for row in reader:
                try:
                    n_trades = safe_int(row.get("n_trades", "0"), 0)
                    sharpe = safe_float(row.get("sharpe", "0"), 0.0)
                    if n_trades < VEC_MASS_TRADES_MIN or sharpe < VEC_MASS_SHARPE_MIN:
                        continue
                    rows.append((
                        path.name, mode, run_ts,
                        row.get("side", ""),
                        row.get("combo", ""),
                        safe_int(row.get("horizon", "0"), 0),
                        n_trades, sharpe,
                        safe_float(row.get("wr"), 0.0),
                        safe_float(row.get("mean_ret"), 0.0),
                        safe_float(row.get("std"), 0.0),
                        safe_float(row.get("pf"), 0.0),
                        now_iso(),
                    ))
                except Exception:
                    continue
    except Exception as e:
        log_import(conn, path, "vec_mass_signals", 0, f"error:{e}")
        return

    if not dry_run and rows:
        conn.executemany("""
            INSERT INTO vec_mass_signals
                (source_file, mode, run_ts, side, combo, horizon,
                 n_trades, sharpe, wr, mean_ret, std, pf, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mode, side, combo, horizon)
            DO UPDATE SET
                sharpe      = CASE WHEN excluded.sharpe > vec_mass_signals.sharpe
                                   THEN excluded.sharpe ELSE vec_mass_signals.sharpe END,
                n_trades    = CASE WHEN excluded.sharpe > vec_mass_signals.sharpe
                                   THEN excluded.n_trades ELSE vec_mass_signals.n_trades END,
                source_file = CASE WHEN excluded.sharpe > vec_mass_signals.sharpe
                                   THEN excluded.source_file ELSE vec_mass_signals.source_file END
        """, rows)
        conn.commit()

    log_import(conn, path, "vec_mass_signals", len(rows), "done")
    print(f"  vec_mass  {path.name}: {len(rows):,} qualifying rows")
    if delete_after and not dry_run:
        path.unlink()
        print(f"            deleted")


# ---------------------------------------------------------------------------
# v8_quick_runs  (also covers v8_dead_* and misc sweep CSVs)
# ---------------------------------------------------------------------------

def _parse_v8quick_name(name):
    mode = "tradier" if "tradier" in name else "crypto"
    m = re.search(r"_(\d+)sym", name)
    n_syms = int(m.group(1)) if m else 0
    m = re.search(r"_(\d{8})(?:_\d{4})?(?:\.csv)?$", name)
    date_label = m.group(1) if m else ""
    m = re.search(r"(?:v8_quick_|v8_dead_)(?:tradier_|crypto_)?(.+?)_\d+sym", name)
    sweep_name = m.group(1) if m else re.sub(r"\.csv$", "", name)
    return mode, n_syms, date_label, sweep_name


def import_v8quick(conn, path: Path, delete_after: bool, dry_run: bool):
    if already_done(conn, path):
        return
    mode, n_syms, date_label, sweep_name = _parse_v8quick_name(path.name)
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "sharpe" not in reader.fieldnames:
                log_import(conn, path, "v8_quick_runs", 0, "skip_schema")
                return
            cfg_cols = [c for c in reader.fieldnames if c.startswith("cfg_")]
            for row in reader:
                try:
                    cfg = {c: row[c] for c in cfg_cols}
                    rows.append((
                        path.name, sweep_name, mode, n_syms, date_label,
                        row.get("run_id", ""),
                        row.get("config_hash", ""),
                        safe_float(row.get("sharpe")),
                        safe_float(row.get("sharpe_min")),
                        safe_float(row.get("sharpe_p25")),
                        safe_float(row.get("sharpe_med")),
                        safe_float(row.get("sharpe_p75")),
                        safe_float(row.get("sharpe_max")),
                        safe_int(row.get("syms_with_sharpe")),
                        safe_float(row.get("pnl")),
                        safe_int(row.get("trades")),
                        safe_int(row.get("wins")),
                        safe_int(row.get("losses")),
                        safe_float(row.get("wr")),
                        safe_float(row.get("avg_pnl_pct")),
                        safe_float(row.get("elapsed")),
                        row.get("status", ""),
                        safe_int(row.get("early_abort")),
                        safe_int(row.get("symbols_used")),
                        json.dumps(cfg),
                        now_iso(),
                    ))
                except Exception:
                    continue
    except Exception as e:
        log_import(conn, path, "v8_quick_runs", 0, f"error:{e}")
        return

    if not dry_run and rows:
        conn.executemany("""
            INSERT INTO v8_quick_runs
                (source_file, sweep_name, mode, n_syms, date_label, run_id, config_hash,
                 sharpe, sharpe_min, sharpe_p25, sharpe_med, sharpe_p75, sharpe_max,
                 syms_with_sharpe, pnl, trades, wins, losses, wr, avg_pnl_pct,
                 elapsed, status, early_abort, symbols_used, cfg_json, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()

    log_import(conn, path, "v8_quick_runs", len(rows), "done")
    print(f"  v8_quick  {path.name}: {len(rows):,} rows")
    if delete_after and not dry_run:
        path.unlink()


# ---------------------------------------------------------------------------
# v8_sweep_runs  (from backtest_v8/sweeps/*.csv)
# ---------------------------------------------------------------------------

def _parse_v8sweep_name(name):
    mode = "tradier" if "tradier" in name else "crypto"
    m = re.search(r"v8_sweep_(?:tradier_|crypto_)?(.+?)(?:_\d+sym)?(?:_[a-f0-9]{6})?_\d{8}", name)
    sweep_name = m.group(1) if m else re.sub(r"\.csv$", "", name)
    return mode, sweep_name


def import_v8sweep(conn, path: Path, delete_after: bool, dry_run: bool):
    if already_done(conn, path):
        return
    mode, sweep_name = _parse_v8sweep_name(path.name)
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "sharpe" not in reader.fieldnames:
                log_import(conn, path, "v8_sweep_runs", 0, "skip_schema")
                return
            cfg_cols = [c for c in reader.fieldnames if c.startswith("cfg_")]
            for row in reader:
                try:
                    cfg = {c: row[c] for c in cfg_cols}
                    rows.append((
                        path.name, sweep_name, mode,
                        row.get("run_id", ""),
                        row.get("name", ""),
                        safe_float(row.get("sharpe")),
                        safe_float(row.get("pnl")),
                        safe_int(row.get("trades")),
                        safe_int(row.get("wins")),
                        safe_int(row.get("losses")),
                        safe_float(row.get("elapsed")),
                        row.get("status", ""),
                        json.dumps(cfg),
                        now_iso(),
                    ))
                except Exception:
                    continue
    except Exception as e:
        log_import(conn, path, "v8_sweep_runs", 0, f"error:{e}")
        return

    if not dry_run and rows:
        conn.executemany("""
            INSERT INTO v8_sweep_runs
                (source_file, sweep_name, mode, run_id, name, sharpe, pnl,
                 trades, wins, losses, elapsed, status, cfg_json, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()

    log_import(conn, path, "v8_sweep_runs", len(rows), "done")
    print(f"  v8_sweep  {path.name}: {len(rows):,} rows")
    if delete_after and not dry_run:
        path.unlink()


# ---------------------------------------------------------------------------
# autonomous_iters
# ---------------------------------------------------------------------------

def import_autonomous(conn, path: Path, source_dir: str, worker: str, mode: str,
                      delete_after: bool, dry_run: bool):
    if already_done(conn, path):
        return
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                log_import(conn, path, "autonomous_iters", 0, "skip_schema")
                return
            fnames = set(reader.fieldnames)
            if "pool_sharpe" not in fnames and "sharpe" not in fnames:
                log_import(conn, path, "autonomous_iters", 0, "skip_schema")
                return
            for row in reader:
                try:
                    rows.append((
                        source_dir, worker, mode,
                        safe_int(row.get("iter", "0"), 0),
                        safe_float(row.get("pool_sharpe", row.get("sharpe", "0")), 0.0),
                        safe_float(row.get("deflated_sharpe"), 0.0),
                        safe_float(row.get("psr"), 0.0),
                        safe_float(row.get("sym_sharpe"), 0.0),
                        safe_float(row.get("acc_gain_pct"), 0.0),
                        safe_float(row.get("gain_sym_yr"), 0.0),
                        safe_float(row.get("avg_gain_trade"), 0.0),
                        safe_float(row.get("gain_per_yr"), 0.0),
                        safe_float(row.get("max_dd_pct"), 0.0),
                        safe_int(row.get("trades", "0"), 0),
                        safe_float(row.get("gain_vs_bh"), 0.0),
                        safe_float(row.get("elapsed_s"), 0.0),
                        safe_int(row.get("overrides_count", "0"), 0),
                        safe_int(row.get("reliable", "0"), 0),
                        safe_int(row.get("useless", "0"), 0),
                        row.get("overrides_json", "{}"),
                        now_iso(),
                    ))
                except Exception:
                    continue
    except Exception as e:
        log_import(conn, path, "autonomous_iters", 0, f"error:{e}")
        return

    if not dry_run and rows:
        conn.executemany("""
            INSERT OR IGNORE INTO autonomous_iters
                (source_dir, worker, mode, iter, pool_sharpe, deflated_sharpe, psr,
                 sym_sharpe, acc_gain_pct, gain_sym_yr, avg_gain_trade, gain_per_yr,
                 max_dd_pct, trades, gain_vs_bh, elapsed_s, overrides_count,
                 reliable, useless, overrides_json, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()

    log_import(conn, path, "autonomous_iters", len(rows), "done")
    print(f"  autonomous {path.relative_to(AUTONOMOUS)}: {len(rows):,} rows")
    if delete_after and not dry_run:
        path.unlink()


# ---------------------------------------------------------------------------
# backtest_v8_run_summaries  (JSONL — store summaries, not per-trade rows)
# ---------------------------------------------------------------------------

def import_v8_jsonl(conn, path: Path, dry_run: bool):
    if already_done(conn, path):
        return
    name = path.stem
    m = re.match(r"v8_(crypto|tradier)_(\w+?)_\d{8}", name)
    mode = m.group(1) if m else "unknown"
    account = m.group(2) if m else "unknown"

    n_events = n_buys = n_sells = n_full_closes = 0
    positions: set = set()
    ts_min = ts_max = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n_events += 1
                side = obj.get("side", "")
                if side == "BUY":
                    n_buys += 1
                elif side == "SELL":
                    n_sells += 1
                if obj.get("is_full_close"):
                    n_full_closes += 1
                pk = obj.get("position_key")
                if pk:
                    positions.add(pk)
                ts = obj.get("timestamp")
                if ts:
                    if ts_min is None or ts < ts_min:
                        ts_min = ts
                    if ts_max is None or ts > ts_max:
                        ts_max = ts
    except Exception as e:
        log_import(conn, path, "backtest_v8_run_summaries", 0, f"error:{e}")
        return

    date_min = datetime.fromtimestamp(ts_min, timezone.utc).date().isoformat() if ts_min else None
    date_max = datetime.fromtimestamp(ts_max, timezone.utc).date().isoformat() if ts_max else None

    if not dry_run:
        conn.execute("""
            INSERT OR IGNORE INTO backtest_v8_run_summaries
                (source_file, run_name, mode, account, n_events, n_buys, n_sells,
                 n_full_closes, unique_positions, date_min, date_max, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (path.name, name, mode, account, n_events, n_buys, n_sells,
              n_full_closes, len(positions), date_min, date_max, now_iso()))
        conn.commit()

    log_import(conn, path, "backtest_v8_run_summaries", n_events, "done")
    print(f"  jsonl     {path.name}: {n_events:,} events, {len(positions)} positions [{date_min}→{date_max}]")


# ---------------------------------------------------------------------------
# Export summary CSVs
# ---------------------------------------------------------------------------

def export_summary_csvs(conn):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    tables = [
        "vec_mass_signals",
        "v8_quick_runs",
        "v8_sweep_runs",
        "autonomous_iters",
        "backtest_v8_run_summaries",
    ]
    for tbl in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        if count == 0:
            continue
        cols = [d[0] for d in conn.execute(f"SELECT * FROM {tbl} LIMIT 1").description]
        out = EXPORT_DIR / f"{tbl}.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            cursor = conn.execute(f"SELECT * FROM {tbl} ORDER BY rowid")
            while True:
                batch = cursor.fetchmany(10000)
                if not batch:
                    break
                w.writerows(batch)
        print(f"  exported  {tbl}.csv ({count:,} rows → {out})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete-after", action="store_true",
                    help="Delete source CSV files after successful import")
    ap.add_argument("--skip-jsonl", action="store_true",
                    help="Skip JSONL backtest logs (they are large and slow)")
    ap.add_argument("--export-csvs", action="store_true",
                    help="Export one summary CSV per table after import")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse files but do not write to DB or delete anything")
    args = ap.parse_args()

    if args.dry_run:
        print("DRY RUN — no writes, no deletes")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    create_tables(conn)

    # ---- 1. vec_mass CSVs ------------------------------------------------
    print("\n=== vec_mass_signals ===")
    vm_files = sorted(SWEEP_RESULTS.glob("vec_mass_*.csv"))
    empty_deleted = 0
    for p in vm_files:
        if p.stat().st_size == 0:
            if not args.dry_run:
                p.unlink()
            empty_deleted += 1
            continue
        import_vec_mass(conn, p, args.delete_after, args.dry_run)
    if empty_deleted:
        print(f"  deleted {empty_deleted} zero-byte vec_mass files")

    # ---- 2. v8_quick / v8_dead CSVs in sweep_results ---------------------
    print("\n=== v8_quick_runs ===")
    misc_globs = [
        "v8_quick_*.csv", "v8_dead_*.csv",
        "hvc_*.csv", "quality_*.csv", "rapid_*.csv",
        "reentry_*.csv", "sweep_*.csv", "v8_confluence_*.csv",
        "v8_million_*.csv",
    ]
    for pat in misc_globs:
        for p in sorted(SWEEP_RESULTS.glob(pat)):
            if p.stat().st_size > 0:
                import_v8quick(conn, p, args.delete_after, args.dry_run)

    # ---- 3. v8_sweep CSVs from backtest_v8/sweeps/ -----------------------
    print("\n=== v8_sweep_runs ===")
    for p in sorted(V8_SWEEPS.rglob("*.csv")):
        if p.stat().st_size > 0:
            import_v8sweep(conn, p, args.delete_after, args.dry_run)

    # ---- 4. autonomous_iters ---------------------------------------------
    print("\n=== autonomous_iters ===")
    for auto_csv in sorted(AUTONOMOUS.rglob("autonomous_*.csv")):
        if auto_csv.stat().st_size == 0:
            continue
        rel = auto_csv.relative_to(AUTONOMOUS)
        parts = rel.parts
        source_dir = parts[0]
        worker = parts[1] if len(parts) > 2 else "main"
        mode = "tradier" if "tradier" in source_dir else "crypto"
        import_autonomous(conn, auto_csv, source_dir, worker, mode,
                          args.delete_after, args.dry_run)

    # ---- 5. backtest_v8 JSONL summaries -----------------------------------
    if not args.skip_jsonl:
        print("\n=== backtest_v8_run_summaries ===")
        jsonl_files = sorted(V8_LOGS.glob("*.jsonl"))
        print(f"  processing {len(jsonl_files):,} JSONL files...")
        for i, p in enumerate(jsonl_files, 1):
            import_v8_jsonl(conn, p, args.dry_run)
            if i % 500 == 0:
                print(f"  ...{i:,}/{len(jsonl_files):,} done")
    else:
        print("\n=== backtest_v8_run_summaries === (skipped)")

    # ---- 6. Export summary CSVs ------------------------------------------
    if args.export_csvs:
        print("\n=== exporting summary CSVs ===")
        export_summary_csvs(conn)

    # ---- Final stats -------------------------------------------------------
    print("\n=== DB summary ===")
    tables = [
        "vec_mass_signals", "v8_quick_runs", "v8_sweep_runs",
        "autonomous_iters", "backtest_v8_run_summaries", "import_log",
    ]
    for tbl in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl:<35} {n:>10,} rows")
    db_mb = DB_PATH.stat().st_size / 1_048_576
    print(f"\n  DB size: {db_mb:.1f} MB  →  {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
