#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""
Sweep Database — SQLite backend for the self-improving tournament engine.

Single source of truth for:
  - Every config ever tested (hashed, never re-run)
  - Results per config per machine
  - Tournament generations (winners breed winners)
  - Auto-apply history (what went live, when, effect)
  - Machine coordination (claim/release, no manual locks)

Usage:
    from sweep_db import SweepDB
    db = SweepDB()  # auto-detects path based on platform
    db.save_result({...})
    winners = db.get_top_configs(mode="tradier", min_trades=50, limit=10)
"""

import hashlib
import json
import logging
import os
import platform
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("sweep_db")

IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    DEFAULT_DB_PATH = Path("/home/niels/binance-sandbox/sweep_tournament.db")
else:
    DEFAULT_DB_PATH = Path("/Users/niels/Documents/binance/sweep_tournament.db")


def config_hash(params: Dict) -> str:
    """Deterministic hash of a config dict. Sorted keys, canonical JSON."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class SweepDB:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS configs (
                    config_hash TEXT PRIMARY KEY,
                    params_json TEXT NOT NULL,
                    mode TEXT NOT NULL,          -- 'crypto' or 'tradier'
                    tier TEXT DEFAULT '',        -- original tier (T1, T2, ...) or 'bred_gen3'
                    generation INTEGER DEFAULT 0,
                    parent_hashes TEXT DEFAULT '',  -- comma-sep hashes of parents (for bred configs)
                    created_at TEXT NOT NULL,
                    claimed_by TEXT DEFAULT NULL,   -- machine name
                    claimed_at TEXT DEFAULT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_hash TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    sharpe REAL DEFAULT 0.0,
                    pnl REAL DEFAULT 0.0,
                    trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0.0,
                    max_drawdown REAL DEFAULT 0.0,
                    profit_factor REAL DEFAULT 0.0,
                    elapsed REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'ok',
                    error TEXT DEFAULT '',
                    machine TEXT DEFAULT '',
                    symbols TEXT DEFAULT '',     -- which symbols were tested
                    start_date TEXT DEFAULT '',
                    capital REAL DEFAULT 10000.0,
                    run_at TEXT NOT NULL,
                    FOREIGN KEY (config_hash) REFERENCES configs(config_hash)
                );

                CREATE TABLE IF NOT EXISTS generations (
                    gen_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    parent_config_hashes TEXT DEFAULT '',
                    child_config_hashes TEXT DEFAULT '',
                    breed_method TEXT DEFAULT '',  -- 'crossover', 'mutate', 'midpoint'
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS applied (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_hash TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    baseline_sharpe REAL DEFAULT 0.0,
                    new_sharpe REAL DEFAULT 0.0,
                    improvement_pct REAL DEFAULT 0.0,
                    applied_at TEXT NOT NULL,
                    reverted_at TEXT DEFAULT NULL,
                    notes TEXT DEFAULT '',
                    FOREIGN KEY (config_hash) REFERENCES configs(config_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_config ON runs(config_hash);
                CREATE INDEX IF NOT EXISTS idx_runs_mode ON runs(mode);
                CREATE INDEX IF NOT EXISTS idx_runs_sharpe ON runs(sharpe DESC);
                CREATE INDEX IF NOT EXISTS idx_configs_mode ON configs(mode);
                CREATE INDEX IF NOT EXISTS idx_configs_gen ON configs(generation);
                CREATE INDEX IF NOT EXISTS idx_configs_claimed ON configs(claimed_by);
            """)

    # ═══════════════════════════════════════════════════════════════
    # CONFIG MANAGEMENT
    # ═══════════════════════════════════════════════════════════════

    def add_config(self, params: Dict, mode: str, tier: str = "", generation: int = 0, parent_hashes: str = "") -> str:
        """Add a config if not already present. Returns config_hash."""
        h = config_hash(params)
        with self._conn() as conn:
            existing = conn.execute("SELECT config_hash FROM configs WHERE config_hash = ?", (h,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO configs (config_hash, params_json, mode, tier, generation, parent_hashes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (h, json.dumps(params, sort_keys=True), mode, tier, generation, parent_hashes, _now()),
                )
        return h

    def add_configs_batch(self, configs: List[Dict], mode: str, tier: str = "", generation: int = 0) -> List[str]:
        """Add multiple configs, skip duplicates. Returns list of hashes."""
        hashes = []
        with self._conn() as conn:
            for params in configs:
                h = config_hash(params)
                hashes.append(h)
                existing = conn.execute("SELECT config_hash FROM configs WHERE config_hash = ?", (h,)).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO configs (config_hash, params_json, mode, tier, generation, parent_hashes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (h, json.dumps(params, sort_keys=True), mode, tier, generation, "", _now()),
                    )
        return hashes

    def config_exists(self, params: Dict) -> bool:
        h = config_hash(params)
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM configs WHERE config_hash = ?", (h,)).fetchone() is not None

    def has_result(self, params: Dict) -> bool:
        """Check if this config has been run (has at least one result)."""
        h = config_hash(params)
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM runs WHERE config_hash = ?", (h,)).fetchone() is not None

    def get_config(self, config_hash: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM configs WHERE config_hash = ?", (config_hash,)).fetchone()
            return dict(row) if row else None

    # ═══════════════════════════════════════════════════════════════
    # MACHINE COORDINATION (claim/release)
    # ═══════════════════════════════════════════════════════════════

    def claim_configs(self, machine: str, mode: str, limit: int = 10, prefer_generation: Optional[int] = None) -> List[Dict]:
        """Claim untested, unclaimed configs for a machine. Returns list of {config_hash, params}."""
        with self._conn() as conn:
            # Release stale claims (>3h old)
            conn.execute(
                "UPDATE configs SET claimed_by = NULL, claimed_at = NULL WHERE claimed_by IS NOT NULL AND claimed_at < datetime('now', '-3 hours')"
            )
            # Find untested, unclaimed configs
            order = "generation DESC, created_at ASC" if prefer_generation is None else "ABS(generation - ?) ASC, created_at ASC"
            query = """
                SELECT c.config_hash, c.params_json, c.tier, c.generation
                FROM configs c
                LEFT JOIN runs r ON c.config_hash = r.config_hash
                WHERE r.id IS NULL
                  AND c.mode = ?
                  AND (c.claimed_by IS NULL OR c.claimed_by = ?)
                ORDER BY {} LIMIT ?
            """.format(order)
            params = [mode, machine]
            if prefer_generation is not None:
                params = [prefer_generation, mode, machine]
                query = """
                    SELECT c.config_hash, c.params_json, c.tier, c.generation
                    FROM configs c
                    LEFT JOIN runs r ON c.config_hash = r.config_hash
                    WHERE r.id IS NULL
                      AND c.mode = ?
                      AND (c.claimed_by IS NULL OR c.claimed_by = ?)
                    ORDER BY ABS(c.generation - ?) DESC, c.created_at ASC LIMIT ?
                """
                params = [mode, machine, prefer_generation, limit]
            else:
                params = [mode, machine, limit]
            rows = conn.execute(query, params).fetchall()
            claimed = []
            for row in rows:
                conn.execute(
                    "UPDATE configs SET claimed_by = ?, claimed_at = ? WHERE config_hash = ?",
                    (machine, _now(), row["config_hash"]),
                )
                claimed.append({"config_hash": row["config_hash"], "params": json.loads(row["params_json"]), "tier": row["tier"], "generation": row["generation"]})
            return claimed

    def release_claim(self, config_hash: str):
        with self._conn() as conn:
            conn.execute("UPDATE configs SET claimed_by = NULL, claimed_at = NULL WHERE config_hash = ?", (config_hash,))

    def get_pending_count(self, mode: str) -> int:
        """How many configs have no result yet?"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as cnt FROM configs c
                LEFT JOIN runs r ON c.config_hash = r.config_hash
                WHERE r.id IS NULL AND c.mode = ?
            """, (mode,)).fetchone()
            return row["cnt"]

    # ═══════════════════════════════════════════════════════════════
    # RESULTS
    # ═══════════════════════════════════════════════════════════════

    def save_result(self, config_hash: str, mode: str, sharpe: float, pnl: float, trades: int, wins: int, losses: int, elapsed: float, status: str = "ok", error: str = "", machine: str = "", symbols: str = "", start_date: str = "", capital: float = 10000.0, max_drawdown: float = 0.0, profit_factor: float = 0.0):
        wr = wins / max(1, wins + losses) * 100
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO runs (config_hash, mode, sharpe, pnl, trades, wins, losses, win_rate, max_drawdown, profit_factor, elapsed, status, error, machine, symbols, start_date, capital, run_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (config_hash, mode, sharpe, pnl, trades, wins, losses, wr, max_drawdown, profit_factor, elapsed, status, error, machine, symbols, start_date, capital, _now()),
            )
            # Release claim after result saved
            conn.execute("UPDATE configs SET claimed_by = NULL, claimed_at = NULL WHERE config_hash = ?", (config_hash,))

    def save_result_dict(self, result: Dict, mode: str, machine: str = ""):
        """Save from a V8 engine result dict (same format as backtest_v8_sweep.py)."""
        params = result.get("config", {})
        h = config_hash(params)
        # Ensure config exists
        self.add_config(params, mode)
        self.save_result(
            config_hash=h, mode=mode,
            sharpe=result.get("sharpe", 0.0), pnl=result.get("pnl", 0.0),
            trades=result.get("trades", 0), wins=result.get("wins", 0), losses=result.get("losses", 0),
            elapsed=result.get("elapsed", 0.0), status=result.get("status", "ok"),
            error=result.get("error", ""), machine=machine,
            symbols=result.get("symbols", ""), start_date=result.get("start_date", ""),
        )

    def get_top_configs(self, mode: str, min_trades: int = 20, min_sharpe: float = 0.0, limit: int = 10) -> List[Dict]:
        """Get top configs by Sharpe. Returns list of {config_hash, params, sharpe, pnl, trades, ...}."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT r.config_hash, c.params_json, c.tier, c.generation,
                       r.sharpe, r.pnl, r.trades, r.wins, r.losses, r.win_rate,
                       r.max_drawdown, r.profit_factor, r.elapsed, r.run_at
                FROM runs r
                JOIN configs c ON r.config_hash = c.config_hash
                WHERE r.mode = ? AND r.trades >= ? AND r.sharpe >= ? AND r.status = 'ok'
                ORDER BY r.sharpe DESC
                LIMIT ?
            """, (mode, min_trades, min_sharpe, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_baseline_sharpe(self, mode: str) -> float:
        """Get the Sharpe of the most recently applied config, or 0."""
        with self._conn() as conn:
            row = conn.execute("SELECT new_sharpe FROM applied WHERE mode = ? ORDER BY applied_at DESC LIMIT 1", (mode,)).fetchone()
            return row["new_sharpe"] if row else 0.0

    def get_all_tested_hashes(self, mode: str) -> set:
        with self._conn() as conn:
            rows = conn.execute("SELECT DISTINCT config_hash FROM runs WHERE mode = ?", (mode,)).fetchall()
            return {r["config_hash"] for r in rows}

    def get_generation_stats(self, mode: str) -> List[Dict]:
        """Stats per generation: count, avg/max sharpe, avg trades."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT c.generation,
                       COUNT(DISTINCT r.config_hash) as tested,
                       COUNT(DISTINCT c.config_hash) as total,
                       ROUND(AVG(r.sharpe), 3) as avg_sharpe,
                       ROUND(MAX(r.sharpe), 3) as max_sharpe,
                       ROUND(AVG(r.pnl), 2) as avg_pnl,
                       ROUND(AVG(r.trades), 0) as avg_trades
                FROM configs c
                LEFT JOIN runs r ON c.config_hash = r.config_hash AND r.status = 'ok'
                WHERE c.mode = ?
                GROUP BY c.generation
                ORDER BY c.generation
            """, (mode,)).fetchall()
            return [dict(r) for r in rows]

    def get_run_count(self, mode: str) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM runs WHERE mode = ? AND status = 'ok'", (mode,)).fetchone()
            return row["cnt"]

    # ═══════════════════════════════════════════════════════════════
    # TOURNAMENT BREEDING
    # ═══════════════════════════════════════════════════════════════

    def get_max_generation(self, mode: str) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(generation) as mg FROM configs WHERE mode = ?", (mode,)).fetchone()
            return row["mg"] or 0

    def record_generation(self, mode: str, generation: int, parent_hashes: List[str], child_hashes: List[str], method: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO generations (mode, generation, parent_config_hashes, child_config_hashes, breed_method, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (mode, generation, ",".join(parent_hashes), ",".join(child_hashes), method, _now()),
            )

    # ═══════════════════════════════════════════════════════════════
    # AUTO-APPLY TRACKING
    # ═══════════════════════════════════════════════════════════════

    def record_apply(self, config_hash: str, mode: str, params: Dict, baseline_sharpe: float, new_sharpe: float, notes: str = ""):
        improvement = ((new_sharpe - baseline_sharpe) / max(0.001, abs(baseline_sharpe))) * 100
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO applied (config_hash, mode, params_json, baseline_sharpe, new_sharpe, improvement_pct, applied_at, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (config_hash, mode, json.dumps(params, sort_keys=True), baseline_sharpe, new_sharpe, improvement, _now(), notes),
            )

    def get_apply_history(self, mode: str, limit: int = 20) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM applied WHERE mode = ? ORDER BY applied_at DESC LIMIT ?", (mode, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_last_apply(self, mode: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM applied WHERE mode = ? ORDER BY applied_at DESC LIMIT 1", (mode,)).fetchone()
            return dict(row) if row else None

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY / STATUS
    # ═══════════════════════════════════════════════════════════════

    def status_summary(self) -> str:
        lines = ["SWEEP TOURNAMENT STATUS", "=" * 60]
        for mode in ("tradier", "crypto"):
            with self._conn() as conn:
                total = conn.execute("SELECT COUNT(*) as c FROM configs WHERE mode = ?", (mode,)).fetchone()["c"]
                tested = conn.execute("SELECT COUNT(DISTINCT config_hash) as c FROM runs WHERE mode = ? AND status = 'ok'", (mode,)).fetchone()["c"]
                pending = total - tested
                claimed = conn.execute("SELECT COUNT(*) as c FROM configs WHERE mode = ? AND claimed_by IS NOT NULL", (mode,)).fetchone()["c"]
                max_gen = self.get_max_generation(mode)
                best = self.get_top_configs(mode, min_trades=20, limit=1)
                best_sharpe = best[0]["sharpe"] if best else 0.0
                last_apply = self.get_last_apply(mode)
            lines.append(f"\n  {mode.upper()}")
            lines.append(f"    Configs: {total} total, {tested} tested, {pending} pending, {claimed} claimed")
            lines.append(f"    Generations: {max_gen}")
            lines.append(f"    Best Sharpe: {best_sharpe:.3f}")
            if last_apply:
                lines.append(f"    Last applied: {last_apply['applied_at']} (Sharpe {last_apply['new_sharpe']:.3f})")
            else:
                lines.append(f"    Last applied: never")
        gen_stats = self.get_generation_stats("tradier")
        if gen_stats:
            lines.append(f"\n  TRADIER GENERATIONS:")
            lines.append(f"    {'Gen':>4} {'Tested':>7} {'Total':>7} {'AvgSharpe':>10} {'MaxSharpe':>10} {'AvgPnL':>8} {'AvgTrades':>10}")
            for g in gen_stats:
                lines.append(f"    {g['generation']:>4} {g['tested']:>7} {g['total']:>7} {g['avg_sharpe'] or 0:>10.3f} {g['max_sharpe'] or 0:>10.3f} {g['avg_pnl'] or 0:>8.2f} {g['avg_trades'] or 0:>10.0f}")
        gen_stats_c = self.get_generation_stats("crypto")
        if gen_stats_c:
            lines.append(f"\n  CRYPTO GENERATIONS:")
            lines.append(f"    {'Gen':>4} {'Tested':>7} {'Total':>7} {'AvgSharpe':>10} {'MaxSharpe':>10} {'AvgPnL':>8} {'AvgTrades':>10}")
            for g in gen_stats_c:
                lines.append(f"    {g['generation']:>4} {g['tested']:>7} {g['total']:>7} {g['avg_sharpe'] or 0:>10.3f} {g['max_sharpe'] or 0:>10.3f} {g['avg_pnl'] or 0:>8.2f} {g['avg_trades'] or 0:>10.0f}")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    import sys
    db = SweepDB()
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        print(db.status_summary())
    else:
        print(f"DB path: {db.db_path}")
        print(db.status_summary())
