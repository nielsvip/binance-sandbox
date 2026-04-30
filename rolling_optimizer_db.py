#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""
Rolling Optimizer DB — SQLite backend for per-symbol config sweep results.

Stores ALL config results (not just winners) so after 1 week we can
identify universally-superior parameter values and hardcode them.

Tables:
  config_results         — one row per (symbol, side, run_date, config_hash)
  config_params_expanded — exploded per-param rows for standardization queries
"""
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DB_PATH = Path(__file__).parent / "data" / "rolling_optimizer.db"


def get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS config_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            account TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            config_json TEXT NOT NULL,
            weighted_sharpe REAL DEFAULT 0,
            raw_sharpe REAL DEFAULT 0,
            weighted_pnl_pct REAL DEFAULT 0,
            raw_pnl_pct REAL DEFAULT 0,
            weighted_pnl_dollars REAL DEFAULT 0,
            raw_pnl_dollars REAL DEFAULT 0,
            avg_position_value REAL DEFAULT 0,
            avg_pnl_per_trade REAL DEFAULT 0,
            composite_score REAL DEFAULT 0,
            trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            elapsed REAL DEFAULT 0,
            status TEXT DEFAULT 'ok',
            window_start TEXT,
            window_end TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cr_symbol_date ON config_results(symbol, side, run_date);
        CREATE INDEX IF NOT EXISTS idx_cr_config_hash ON config_results(config_hash);
        CREATE INDEX IF NOT EXISTS idx_cr_composite ON config_results(composite_score DESC);

        CREATE TABLE IF NOT EXISTS config_params_expanded (
            result_id INTEGER NOT NULL,
            param_name TEXT NOT NULL,
            param_value TEXT NOT NULL,
            weighted_sharpe REAL DEFAULT 0,
            composite_score REAL DEFAULT 0,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            run_date TEXT NOT NULL,
            FOREIGN KEY (result_id) REFERENCES config_results(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cpe_param ON config_params_expanded(param_name, param_value);
        CREATE INDEX IF NOT EXISTS idx_cpe_symbol ON config_params_expanded(symbol, side, run_date);
    """)
    conn.commit()


def config_hash(cfg: Dict) -> str:
    """Deterministic hash of config dict (sorted keys)."""
    raw = json.dumps(cfg, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def insert_result(conn: sqlite3.Connection, result: Dict) -> int:
    """Insert one config result. Returns row id."""
    now = datetime.now(timezone.utc).isoformat()
    cfg = result.get("config", {})
    c_hash = config_hash(cfg)
    cur = conn.execute("""
        INSERT INTO config_results (
            run_date, symbol, side, account, config_hash, config_json,
            weighted_sharpe, raw_sharpe, weighted_pnl_pct, raw_pnl_pct,
            weighted_pnl_dollars, raw_pnl_dollars, avg_position_value,
            avg_pnl_per_trade, composite_score,
            trades, wins, losses, elapsed, status,
            window_start, window_end, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.get("run_date", ""),
        result.get("symbol", ""),
        result.get("side", ""),
        result.get("account", ""),
        c_hash,
        json.dumps(cfg, sort_keys=True, default=str),
        result.get("weighted_sharpe", 0),
        result.get("raw_sharpe", 0),
        result.get("weighted_pnl_pct", 0),
        result.get("raw_pnl_pct", 0),
        result.get("weighted_pnl_dollars", 0),
        result.get("raw_pnl_dollars", 0),
        result.get("avg_position_value", 0),
        result.get("avg_pnl_per_trade", 0),
        result.get("composite_score", 0),
        result.get("trades", 0),
        result.get("wins", 0),
        result.get("losses", 0),
        result.get("elapsed", 0),
        result.get("status", "ok"),
        result.get("window_start", ""),
        result.get("window_end", ""),
        now,
    ))
    conn.commit()
    return cur.lastrowid


def expand_config_params(conn: sqlite3.Connection, result_id: int, cfg: Dict,
                         weighted_sharpe: float, composite_score: float,
                         symbol: str, side: str, run_date: str):
    """Explode config dict into per-param rows for standardization analysis."""
    rows = []
    for k, v in cfg.items():
        rows.append((result_id, k, json.dumps(v, default=str),
                      weighted_sharpe, composite_score, symbol, side, run_date))
    conn.executemany(
        "INSERT INTO config_params_expanded VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.commit()


def compute_composite_scores(conn: sqlite3.Connection, symbol: str, side: str, run_date: str):
    """Normalize sharpe and $ pnl across all configs for a symbol, update composite_score."""
    rows = conn.execute("""
        SELECT id, weighted_sharpe, weighted_pnl_dollars
        FROM config_results
        WHERE symbol = ? AND side = ? AND run_date = ? AND status = 'ok' AND trades > 0
    """, (symbol, side, run_date)).fetchall()
    if len(rows) < 2:
        return
    sharpes = [r["weighted_sharpe"] for r in rows]
    dollars = [r["weighted_pnl_dollars"] for r in rows]
    s_min, s_max = min(sharpes), max(sharpes)
    d_min, d_max = min(dollars), max(dollars)
    updates = []
    for r in rows:
        norm_s = (r["weighted_sharpe"] - s_min) / (s_max - s_min) if s_max > s_min else 0.5
        norm_d = (r["weighted_pnl_dollars"] - d_min) / (d_max - d_min) if d_max > d_min else 0.5
        composite = 0.5 * norm_s + 0.5 * norm_d
        updates.append((composite, r["id"]))
    conn.executemany("UPDATE config_results SET composite_score = ? WHERE id = ?", updates)
    conn.commit()


def get_best_config(conn: sqlite3.Connection, symbol: str, side: str,
                    run_date: str, min_trades: int = 2) -> Optional[Dict]:
    """Return the config with highest composite_score for a symbol/side/date."""
    row = conn.execute("""
        SELECT config_json, composite_score, weighted_sharpe, weighted_pnl_dollars,
               trades, wins, losses, avg_pnl_per_trade, avg_position_value
        FROM config_results
        WHERE symbol = ? AND side = ? AND run_date = ? AND status = 'ok' AND trades >= ?
        ORDER BY composite_score DESC
        LIMIT 1
    """, (symbol, side, run_date, min_trades)).fetchone()
    if not row:
        return None
    return {
        "config": json.loads(row["config_json"]),
        "composite_score": row["composite_score"],
        "weighted_sharpe": row["weighted_sharpe"],
        "weighted_pnl_dollars": row["weighted_pnl_dollars"],
        "trades": row["trades"],
        "wins": row["wins"],
        "losses": row["losses"],
        "avg_pnl_per_trade": row["avg_pnl_per_trade"],
        "avg_position_value": row["avg_position_value"],
    }


def standardization_report(conn: sqlite3.Connection, min_symbols: int = 15,
                           days_back: int = 7) -> List[Dict]:
    """Find params where one value consistently wins across symbols.
    Returns list of {param_name, winning_value, n_symbols, avg_composite, margin_pct}."""
    rows = conn.execute("""
        WITH ranked AS (
            SELECT param_name, param_value,
                   COUNT(DISTINCT symbol || '_' || side) as n_keys,
                   AVG(composite_score) as avg_comp,
                   ROW_NUMBER() OVER (PARTITION BY param_name ORDER BY AVG(composite_score) DESC) as rn
            FROM config_params_expanded
            WHERE run_date >= date('now', ? || ' days')
            GROUP BY param_name, param_value
            HAVING n_keys >= ?
        )
        SELECT r1.param_name, r1.param_value as winning_value,
               r1.n_keys, r1.avg_comp,
               r2.avg_comp as runner_up_comp,
               CASE WHEN r2.avg_comp > 0
                    THEN (r1.avg_comp - r2.avg_comp) / r2.avg_comp * 100
                    ELSE 100.0 END as margin_pct
        FROM ranked r1
        LEFT JOIN ranked r2 ON r1.param_name = r2.param_name AND r2.rn = 2
        WHERE r1.rn = 1
        ORDER BY margin_pct DESC
    """, (f"-{days_back}", min_symbols)).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    conn = get_db()
    print(f"DB at {DB_PATH}")
    report = standardization_report(conn)
    if report:
        print(f"\n{'='*70}")
        print(f"  STANDARDIZATION REPORT ({len(report)} params)")
        print(f"  {'Param':<45} {'Winner':<10} {'Symbols':>7} {'Margin':>8}")
        for r in report:
            print(f"  {r['param_name']:<45} {r['winning_value']:<10} {r['n_keys']:>7} {r['margin_pct']:>7.1f}%")
    else:
        print("No data yet — run rolling_config_optimizer.py first")
    conn.close()
