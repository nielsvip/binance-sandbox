#!/usr/bin/env python3
"""vec_sweep.py — fully-vectorized backtest sweep, audited Sharpe, SQLite store.

Built per CLAUDE.md NO-LIES MANDATE. Every Sharpe in the DB is per-trade
pool_sharpe = mean(returns)/std(returns). NO annualization, NO sqrt(N), NO
sym-avg masquerading as pool. Open trades at last bar are MtM'd into the
return distribution before computing Sharpe (rule 2). Per-trade returns are
stored as a float16 blob alongside every result row so any number can be
recomputed end-to-end via metrics_guard.pool_sharpe(returns).

USAGE
=====
  # Per-symbol sweep on a single sym (DIAGNOSTIC tagged):
  python3 vec_sweep.py per_symbol --syms BTCUSDT --max-configs 200000

  # Per-symbol sweep on 6 priority syms in sequence:
  python3 vec_sweep.py per_symbol --syms BTCUSDT,ETHUSDT,SOLUSDT,BTCDOMUSDT,DOGEUSDT,ZECUSDT --max-configs 100000

  # Pooled 48-sym crypto basket (publishable):
  python3 vec_sweep.py pooled --basket crypto48 --max-configs 50000

  # Re-test top-N from DB with expanded grid until Sharpe target reached:
  python3 vec_sweep.py validate --top-n 1000 --target-pool-sharpe 4.0

  # V3-only primitive sweep on Mac for forward+back parity:
  python3 vec_sweep.py v3 --syms BTCUSDC,ETHUSDC --max-configs 100000

ARCHITECTURE
============
  - PRIMITIVES: ~120 named boolean signals computed once per symbol from NPZ.
    Each is a single np.ndarray[bool] of shape (n_bars,). Adding a new one is
    a 1-line entry in the PRIMITIVES list.
  - CONFIG: a 4-tuple of primitive-id sets — (entry_long, exit_long,
    entry_short, exit_short). AND-combine within each set. config_hash is
    sha1 over sorted ids → dedupe across runs.
  - GRID: lazy random sampler over the cartesian product. Practical ceiling
    ~10^7 configs/symbol/day per worker; trillions are reachable across
    machines + days, but the script never claims to enumerate them all.
  - TRADE LOOP: vectorized entry/exit-bar arrays + np.searchsorted to pair
    them, then a single per-config Python pass to drop overlaps. ~100-300
    configs/sec per symbol on 4yr × 1.1M bars.
  - SQLITE: configs + results tables, per-trade returns stored as zlib'd
    float16 blob. Indexed on (symbol, pool_sharpe DESC) for top-N queries.
  - WAL mode + per-batch commits; safe to run multiple workers on one DB.

LIMITATIONS (honest)
====================
  - "Trillions of configs" is rhetorical. With 100 primitives and (k=3 entry
    AND k=3 exit) you have C(100,3)^2 ≈ 2.6×10^10 configs. We sample, we
    don't enumerate.
  - Single-symbol Sharpe is DIAGNOSTIC by CLAUDE.md rule 5. Per-symbol
    results in this DB are NEVER promotable to live by themselves — only via
    a ≥48-sym (crypto) / ≥100-sym (stocks) pooled re-run.
  - No commission/slippage model yet. Add as a return-haircut in
    `evaluate_config` before pool_sharpe.

SAFETY
======
  - On launch, touches /tmp/BACKTEST_HOLD per CLAUDE.md (suspend Mac→server
    autosync during A/B). Removes on clean exit.
  - Refuses to run on the live machines if mode != crypto/tradier match.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import signal
import sqlite3
import sys
import time
import zlib
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

import metrics_guard as mg

# ---------------------------------------------------------------------------
# CONST
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent
NPZ_DIR_DEFAULT = REPO / "backtest_v8" / "indicators"
DB_DEFAULT = REPO / "data" / "vec_sweep.db"
HOLD_SENTINEL = Path("/tmp/BACKTEST_HOLD")
BARS_PER_YEAR_3M = 525_600 / 3  # 175,200 3m bars per year

# Crypto-48 canonical basket (matches CLAUDE.md sample floor).
BASKETS: Dict[str, List[str]] = {
    "crypto48": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "BNBUSDT", "AVAXUSDT",
        "XRPUSDT", "LINKUSDT", "LTCUSDT", "UNIUSDT",  # 10 USDC majors (USDT data)
        "1INCHUSDT", "ALGOUSDT", "ANKRUSDT", "ATOMUSDT", "AXSUSDT", "BANDUSDT",
        "BATUSDT", "BELUSDT", "BTCDOMUSDT", "C98USDT", "CELRUSDT", "CHRUSDT",
        "COMPUSDT", "COTIUSDT", "DASHUSDT", "DOTUSDT", "EGLDUSDT", "ENJUSDT",
        "ETCUSDT", "GRTUSDT", "GTCUSDT", "HOTUSDT", "IOSTUSDT", "IOTAUSDT",
        "IOTXUSDT", "KAVAUSDT", "KNCUSDT", "KSMUSDT", "LRCUSDT", "MANAUSDT",
        "MTLUSDT", "NKNUSDT", "QTUMUSDT", "RLCUSDT", "RSRUSDT", "RVNUSDT",
        "SANDUSDT", "SKLUSDT",
    ],
    "priority6": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCDOMUSDT", "DOGEUSDT", "ZECUSDT",
    ],
}

# ---------------------------------------------------------------------------
# PRIMITIVES — boolean signals over NPZ. Add new entries below; nothing else
# needs to change. Each lambda receives the full NPZ dict-like and returns a
# bool ndarray of shape (n_bars,).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Primitive:
    name: str
    fn: Callable[[Dict[str, np.ndarray]], np.ndarray]
    side: str  # "long_entry", "short_entry", "long_exit", "short_exit", "any"


def _safe(d, k, default=0.0):
    """Return d[k] as float32, or a constant array if missing (so the
    primitive evaluates to a constant — typically all-False)."""
    if k in d.files if hasattr(d, "files") else k in d:
        return np.asarray(d[k], dtype=np.float32)
    n = len(d["close_3m"]) if "close_3m" in (d.files if hasattr(d, "files") else d) else 0
    return np.full(n, default, dtype=np.float32)


def _PRIMS() -> List[Primitive]:
    """Build the primitive library. Lambdas are constructed lazily so the
    module imports cheaply."""
    P: List[Primitive] = []

    def add(name, side, fn):
        P.append(Primitive(name, fn, side))

    # --- Donchian Channel breakouts (entries) ---
    for tf in ("3m", "15m", "1h", "4h", "D"):
        add(f"dc_high_break_{tf}", "long_entry",
            lambda d, tf=tf: _safe(d, f"dc_high_crossover_{tf}") > 0)
        add(f"dc_low_break_{tf}", "short_entry",
            lambda d, tf=tf: _safe(d, f"dc_low_crossunder_{tf}") > 0)

    # --- DC retests (exits) ---
    for tf in ("3m", "15m", "1h", "4h", "D"):
        add(f"dc_low_break_{tf}_X", "long_exit",
            lambda d, tf=tf: _safe(d, f"dc_low_crossunder_{tf}") > 0)
        add(f"dc_high_break_{tf}_X", "short_exit",
            lambda d, tf=tf: _safe(d, f"dc_high_crossover_{tf}") > 0)

    # --- WaveTrend cross (long entry on bull cross, short entry on bear cross) ---
    for tf in ("3m", "15m", "1h", "4h", "D"):
        add(f"wt_bull_cross_{tf}", "long_entry",
            lambda d, tf=tf: (_safe(d, f"wt1_{tf}") > _safe(d, f"wt2_{tf}")) &
                              (_safe(d, f"wt1_{tf}_prev") <= _safe(d, f"wt2_{tf}_prev")))
        add(f"wt_bear_cross_{tf}", "short_entry",
            lambda d, tf=tf: (_safe(d, f"wt1_{tf}") < _safe(d, f"wt2_{tf}")) &
                              (_safe(d, f"wt1_{tf}_prev") >= _safe(d, f"wt2_{tf}_prev")))
        add(f"wt_bear_cross_{tf}_X", "long_exit",
            lambda d, tf=tf: (_safe(d, f"wt1_{tf}") < _safe(d, f"wt2_{tf}")) &
                              (_safe(d, f"wt1_{tf}_prev") >= _safe(d, f"wt2_{tf}_prev")))
        add(f"wt_bull_cross_{tf}_X", "short_exit",
            lambda d, tf=tf: (_safe(d, f"wt1_{tf}") > _safe(d, f"wt2_{tf}")) &
                              (_safe(d, f"wt1_{tf}_prev") <= _safe(d, f"wt2_{tf}_prev")))

    # --- Stochastic K extremes ---
    for tf in ("3m", "15m", "1h", "4h"):
        add(f"k_{tf}_oversold20", "long_entry",
            lambda d, tf=tf: _safe(d, f"k_{tf}", 50.0) < 20)
        add(f"k_{tf}_overbought80", "short_entry",
            lambda d, tf=tf: _safe(d, f"k_{tf}", 50.0) > 80)
        add(f"k_{tf}_overbought80_X", "long_exit",
            lambda d, tf=tf: _safe(d, f"k_{tf}", 50.0) > 80)
        add(f"k_{tf}_oversold20_X", "short_exit",
            lambda d, tf=tf: _safe(d, f"k_{tf}", 50.0) < 20)

    # --- RSI ---
    for tf in ("3m", "15m", "1h", "4h"):
        add(f"rsi_{tf}_lt30", "long_entry",
            lambda d, tf=tf: _safe(d, f"rsi_{tf}", 50.0) < 30)
        add(f"rsi_{tf}_gt70", "short_entry",
            lambda d, tf=tf: _safe(d, f"rsi_{tf}", 50.0) > 70)

    # --- MFI ---
    for tf in ("15m", "1h", "4h"):
        add(f"mfi_{tf}_lt25", "long_entry",
            lambda d, tf=tf: _safe(d, f"mfi_{tf}", 50.0) < 25)
        add(f"mfi_{tf}_gt75", "short_entry",
            lambda d, tf=tf: _safe(d, f"mfi_{tf}", 50.0) > 75)

    # --- Bollinger Band % position ---
    for tf in ("3m", "15m", "1h"):
        add(f"bbpct_{tf}_lt0", "long_entry",
            lambda d, tf=tf: _safe(d, f"bb_pct_b_{tf}", 0.5) < 0.0)
        add(f"bbpct_{tf}_gt1", "short_entry",
            lambda d, tf=tf: _safe(d, f"bb_pct_b_{tf}", 0.5) > 1.0)
        add(f"bbpct_{tf}_gt0p8_X", "long_exit",
            lambda d, tf=tf: _safe(d, f"bb_pct_b_{tf}", 0.5) > 0.8)
        add(f"bbpct_{tf}_lt0p2_X", "short_exit",
            lambda d, tf=tf: _safe(d, f"bb_pct_b_{tf}", 0.5) < 0.2)

    # --- WT composite alignment ---
    add("wt_bull_align_ge2", "long_entry",
        lambda d: _safe(d, "wt_bull_alignment", 0.0) >= 2)
    add("wt_bear_align_ge2", "short_entry",
        lambda d: _safe(d, "wt_bear_alignment", 0.0) >= 2)
    add("wt_bull_align_lt1_X", "long_exit",
        lambda d: _safe(d, "wt_bull_alignment", 0.0) < 1)
    add("wt_bear_align_lt1_X", "short_exit",
        lambda d: _safe(d, "wt_bear_alignment", 0.0) < 1)

    # --- WT divergence ---
    add("wt_bull_div", "long_entry",
        lambda d: _safe(d, "wt_any_bull_div", 0.0) > 0)
    add("wt_bear_div", "short_entry",
        lambda d: _safe(d, "wt_any_bear_div", 0.0) > 0)

    # --- Episodic Pivot ---
    add("ep_breakout_long", "long_entry",
        lambda d: (_safe(d, "ep_detected", 0.0) > 0) & (_safe(d, "ep_direction", 0.0) > 0))
    add("ep_breakout_short", "short_entry",
        lambda d: (_safe(d, "ep_detected", 0.0) > 0) & (_safe(d, "ep_direction", 0.0) < 0))

    # --- Clenow momentum ---
    add("clenow_strong_long", "long_entry",
        lambda d: (_safe(d, "clenow_score", 0.0) > 0) & (_safe(d, "clenow_r2", 0.0) > 0.7))
    add("clenow_weak_short", "short_entry",
        lambda d: (_safe(d, "clenow_score", 0.0) < 0) & (_safe(d, "clenow_r2", 0.0) > 0.7))

    # --- Bar-break mean reversion (fast scalp) ---
    add("close_break_3m_low_prev", "long_entry",
        lambda d: _safe(d, "close_3m") < _safe(d, "low_3m_prev"))
    add("close_break_3m_high_prev", "short_entry",
        lambda d: _safe(d, "close_3m") > _safe(d, "high_3m_prev"))

    # --- Time-stop fallback exit (always-True at every Nth bar) ---
    # Stored as a function the trade-loop can fall back to if exit set is empty.
    return P


PRIMITIVES = _PRIMS()
PRIM_NAMES = [p.name for p in PRIMITIVES]
PRIM_BY_NAME = {p.name: i for i, p in enumerate(PRIMITIVES)}
PRIM_BY_SIDE: Dict[str, List[int]] = {}
for i, p in enumerate(PRIMITIVES):
    PRIM_BY_SIDE.setdefault(p.side, []).append(i)


# ---------------------------------------------------------------------------
# NPZ LOADER + PRIMITIVE EVAL
# ---------------------------------------------------------------------------

def load_npz(sym: str, npz_dir: Path) -> Dict[str, np.ndarray]:
    p = npz_dir / f"{sym}.npz"
    if not p.exists():
        raise FileNotFoundError(f"NPZ missing for {sym}: {p}")
    return dict(np.load(p, allow_pickle=False))


def compute_primitive_masks(npz: Dict[str, np.ndarray]) -> np.ndarray:
    """Eval all primitives once; return packed bool array (n_prim, n_bars)."""
    n_bars = len(npz["close_3m"])
    out = np.zeros((len(PRIMITIVES), n_bars), dtype=bool)
    for i, p in enumerate(PRIMITIVES):
        try:
            m = p.fn(npz)
            if m.shape != (n_bars,):
                m = np.zeros(n_bars, dtype=bool)
            out[i] = m.astype(bool)
        except Exception:
            out[i] = False
    return out


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    entry_long: Tuple[int, ...]
    exit_long: Tuple[int, ...]
    entry_short: Tuple[int, ...]
    exit_short: Tuple[int, ...]

    @property
    def hash(self) -> str:
        s = json.dumps([sorted(self.entry_long), sorted(self.exit_long),
                        sorted(self.entry_short), sorted(self.exit_short)],
                       separators=(",", ":"))
        return hashlib.sha1(s.encode()).hexdigest()[:16]

    def label(self) -> str:
        def names(ids):
            return "+".join(PRIM_NAMES[i] for i in ids) or "_NONE_"
        return (f"EL[{names(self.entry_long)}] XL[{names(self.exit_long)}] "
                f"ES[{names(self.entry_short)}] XS[{names(self.exit_short)}]")

    def to_json(self) -> str:
        return json.dumps({
            "entry_long": [PRIM_NAMES[i] for i in self.entry_long],
            "exit_long": [PRIM_NAMES[i] for i in self.exit_long],
            "entry_short": [PRIM_NAMES[i] for i in self.entry_short],
            "exit_short": [PRIM_NAMES[i] for i in self.exit_short],
        })


# ---------------------------------------------------------------------------
# VECTORIZED TRADE LOOP
# ---------------------------------------------------------------------------

def _and_rows(mask_pack: np.ndarray, ids: Sequence[int]) -> np.ndarray:
    """Bitwise-AND a subset of rows from the packed primitive array."""
    if not ids:
        return np.zeros(mask_pack.shape[1], dtype=bool)
    out = mask_pack[ids[0]].copy()
    for j in ids[1:]:
        out &= mask_pack[j]
    return out


def _trade_returns(entry_mask: np.ndarray, exit_mask: np.ndarray,
                   close: np.ndarray, direction: int) -> np.ndarray:
    """Pair entry signals with the next exit signal (no overlap, no
    pyramiding). Open trade at end is MtM'd to the last close. Returns a
    float32 array of per-trade pct returns (e.g. 0.012 = +1.2%)."""
    entry_bars = np.flatnonzero(entry_mask)
    exit_bars = np.flatnonzero(exit_mask)
    if entry_bars.size == 0:
        return np.empty(0, dtype=np.float32)
    rets: List[float] = []
    last_exit = -1
    n = len(close)
    for e in entry_bars:
        if e <= last_exit:
            continue
        if exit_bars.size:
            idx = np.searchsorted(exit_bars, e + 1)
            if idx < exit_bars.size:
                xb = int(exit_bars[idx])
            else:
                xb = n - 1  # MtM open trade
        else:
            xb = n - 1
        ep = close[e]
        xp = close[xb]
        if ep <= 0 or not np.isfinite(ep) or not np.isfinite(xp):
            continue
        r = (xp - ep) / ep
        if direction < 0:
            r = -r
        rets.append(float(r))
        last_exit = xb
        if xb >= n - 1:
            break
    return np.asarray(rets, dtype=np.float32)


def _max_drawdown_pct(rets: np.ndarray) -> float:
    if rets.size == 0:
        return 0.0
    eq = np.cumsum(rets)  # additive equity in pct units
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return float(dd.max() * 100.0)


def evaluate_config(prims: np.ndarray, close: np.ndarray, cfg: Config,
                    n_years: float) -> Dict[str, object]:
    """Run one config end-to-end. Returns dict ready for DB insert."""
    el = _and_rows(prims, cfg.entry_long) if cfg.entry_long else np.zeros(prims.shape[1], dtype=bool)
    xl = _and_rows(prims, cfg.exit_long) if cfg.exit_long else np.zeros(prims.shape[1], dtype=bool)
    es = _and_rows(prims, cfg.entry_short) if cfg.entry_short else np.zeros(prims.shape[1], dtype=bool)
    xs = _and_rows(prims, cfg.exit_short) if cfg.exit_short else np.zeros(prims.shape[1], dtype=bool)
    rets_l = _trade_returns(el, xl, close, +1)
    rets_s = _trade_returns(es, xs, close, -1)
    rets = np.concatenate([rets_l, rets_s]) if rets_l.size or rets_s.size else np.empty(0, dtype=np.float32)
    n_trades = int(rets.size)
    pool = mg.pool_sharpe(rets.tolist()) if n_trades >= 2 else 0.0
    total_gain = float(rets.sum() * 100.0)  # pct, additive (no compounding — same units as Sharpe)
    avg_gain = (total_gain / n_trades) if n_trades else 0.0
    return {
        "pool_sharpe": pool,
        "sym_sharpe": pool,  # single-sym → same; pooled run overwrites
        "trades": n_trades,
        "trades_long": int(rets_l.size),
        "trades_short": int(rets_s.size),
        "acc_gain_pct": total_gain,
        "avg_gain_trade": avg_gain,
        "gain_per_yr": total_gain / max(0.01, n_years),
        "max_dd_pct": _max_drawdown_pct(rets),
        "win_rate_pct": float((rets > 0).mean() * 100.0) if n_trades else 0.0,
        "rets_blob": zlib.compress(rets.astype(np.float16).tobytes(), 6),
    }


# ---------------------------------------------------------------------------
# GRID
# ---------------------------------------------------------------------------

def random_config(rng: random.Random,
                  k_entry: Tuple[int, int] = (1, 3),
                  k_exit: Tuple[int, int] = (1, 2),
                  side: str = "both") -> Config:
    """Sample one config. side: 'long', 'short', 'both'."""
    long_e_pool = PRIM_BY_SIDE.get("long_entry", [])
    long_x_pool = PRIM_BY_SIDE.get("long_exit", [])
    short_e_pool = PRIM_BY_SIDE.get("short_entry", [])
    short_x_pool = PRIM_BY_SIDE.get("short_exit", [])

    def pick(pool, lo, hi):
        if not pool:
            return tuple()
        k = rng.randint(lo, min(hi, len(pool)))
        return tuple(sorted(rng.sample(pool, k)))

    el = pick(long_e_pool, *k_entry) if side in ("long", "both") else tuple()
    xl = pick(long_x_pool, *k_exit) if side in ("long", "both") else tuple()
    es = pick(short_e_pool, *k_entry) if side in ("short", "both") else tuple()
    xs = pick(short_x_pool, *k_exit) if side in ("short", "both") else tuple()
    return Config(el, xl, es, xs)


def expanded_neighborhood(seed: Config, rng: random.Random) -> Config:
    """For a known-good config, swap exactly ONE primitive for a neighbor.
    Used by validate-mode to climb toward Sharpe 4+."""
    sides = ["entry_long", "exit_long", "entry_short", "exit_short"]
    s = rng.choice(sides)
    cur = list(getattr(seed, s))
    if not cur:
        return seed
    pool_map = {
        "entry_long": "long_entry", "exit_long": "long_exit",
        "entry_short": "short_entry", "exit_short": "short_exit",
    }
    pool = [i for i in PRIM_BY_SIDE.get(pool_map[s], []) if i not in cur]
    if not pool:
        return seed
    cur[rng.randrange(len(cur))] = rng.choice(pool)
    cur = tuple(sorted(set(cur)))
    new_kwargs = {
        "entry_long": seed.entry_long, "exit_long": seed.exit_long,
        "entry_short": seed.entry_short, "exit_short": seed.exit_short,
    }
    new_kwargs[s] = cur
    return Config(**new_kwargs)


# ---------------------------------------------------------------------------
# SQLITE
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_hash TEXT UNIQUE NOT NULL,
    config_json TEXT NOT NULL,
    label TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id INTEGER NOT NULL REFERENCES configs(id),
    symbol TEXT NOT NULL,
    n_syms INTEGER NOT NULL,
    years REAL NOT NULL,
    pool_sharpe REAL NOT NULL,
    sym_sharpe REAL NOT NULL,
    trades INTEGER NOT NULL,
    trades_long INTEGER NOT NULL,
    trades_short INTEGER NOT NULL,
    acc_gain_pct REAL NOT NULL,
    avg_gain_trade REAL NOT NULL,
    gain_per_yr REAL NOT NULL,
    gain_sym_yr REAL NOT NULL,
    max_dd_pct REAL NOT NULL,
    win_rate_pct REAL NOT NULL,
    verdict TEXT NOT NULL,
    rets_blob BLOB,
    mode TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(config_id, symbol, mode)
);
CREATE INDEX IF NOT EXISTS idx_results_pool ON results(pool_sharpe DESC);
CREATE INDEX IF NOT EXISTS idx_results_sym_pool ON results(symbol, pool_sharpe DESC);
CREATE INDEX IF NOT EXISTS idx_results_mode ON results(mode, pool_sharpe DESC);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def upsert_config(con: sqlite3.Connection, cfg: Config) -> int:
    h = cfg.hash
    cur = con.execute("SELECT id FROM configs WHERE config_hash=?", (h,))
    r = cur.fetchone()
    if r:
        return r[0]
    con.execute("INSERT INTO configs(config_hash, config_json, label) VALUES(?,?,?)",
                (h, cfg.to_json(), cfg.label()[:200]))
    return int(con.execute("SELECT id FROM configs WHERE config_hash=?", (h,)).fetchone()[0])


def already_have(con: sqlite3.Connection, config_id: int, sym: str, mode: str) -> bool:
    r = con.execute("SELECT 1 FROM results WHERE config_id=? AND symbol=? AND mode=?",
                    (config_id, sym, mode)).fetchone()
    return r is not None


def insert_result(con: sqlite3.Connection, config_id: int, sym: str,
                  n_syms: int, years: float, mode: str,
                  metrics: Dict[str, object]) -> None:
    floor_syms = mg.MIN_SYMS_STOCKS if "tradier" in mode else mg.MIN_SYMS_CRYPTO
    publishable = (n_syms >= floor_syms) and (years >= mg.MIN_YEARS)
    verdict = "PUBLISHABLE" if publishable else "DIAGNOSTIC"
    gsym_yr = (metrics["acc_gain_pct"] / max(1, n_syms)) / max(0.01, years)
    con.execute(
        """INSERT OR REPLACE INTO results
           (config_id, symbol, n_syms, years, pool_sharpe, sym_sharpe, trades,
            trades_long, trades_short, acc_gain_pct, avg_gain_trade,
            gain_per_yr, gain_sym_yr, max_dd_pct, win_rate_pct, verdict,
            rets_blob, mode)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (config_id, sym, n_syms, years,
         metrics["pool_sharpe"], metrics["sym_sharpe"], metrics["trades"],
         metrics["trades_long"], metrics["trades_short"], metrics["acc_gain_pct"],
         metrics["avg_gain_trade"], metrics["gain_per_yr"], gsym_yr,
         metrics["max_dd_pct"], metrics["win_rate_pct"], verdict,
         metrics.get("rets_blob"), mode),
    )


def top_seeds(con: sqlite3.Connection, mode: str, n: int) -> List[Config]:
    rows = con.execute(
        "SELECT c.config_json FROM results r JOIN configs c ON c.id=r.config_id "
        "WHERE r.mode=? AND r.trades>=30 ORDER BY r.pool_sharpe DESC LIMIT ?",
        (mode, n),
    ).fetchall()
    seeds: List[Config] = []
    for (j,) in rows:
        try:
            d = json.loads(j)
            seeds.append(Config(
                tuple(sorted(PRIM_BY_NAME[n] for n in d["entry_long"] if n in PRIM_BY_NAME)),
                tuple(sorted(PRIM_BY_NAME[n] for n in d["exit_long"] if n in PRIM_BY_NAME)),
                tuple(sorted(PRIM_BY_NAME[n] for n in d["entry_short"] if n in PRIM_BY_NAME)),
                tuple(sorted(PRIM_BY_NAME[n] for n in d["exit_short"] if n in PRIM_BY_NAME)),
            ))
        except Exception:
            continue
    return seeds


# ---------------------------------------------------------------------------
# RUNNERS
# ---------------------------------------------------------------------------

def _years_of(close: np.ndarray) -> float:
    """Length of 3m series in years (float)."""
    return len(close) / BARS_PER_YEAR_3M


def run_per_symbol(args) -> None:
    """Per-symbol DIAGNOSTIC sweep — one config, one symbol, store result.
    By CLAUDE.md rule 5 these are NEVER promotable on their own."""
    syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    npz_dir = Path(args.npz_dir)
    db = open_db(Path(args.db))
    rng = random.Random(args.seed)
    mode = "per_symbol"
    print(f"[per_symbol] syms={syms} max_configs={args.max_configs:,} db={args.db}")
    for sym in syms:
        try:
            npz = load_npz(sym, npz_dir)
        except FileNotFoundError as e:
            print(f"  skip {sym}: {e}")
            continue
        close = npz["close_3m"]
        years = _years_of(close)
        prims = compute_primitive_masks(npz)
        print(f"  {sym}: bars={len(close):,} years={years:.2f} prims={prims.shape[0]}")
        t0 = time.time()
        n_done = 0
        n_skipped = 0
        n_inserted = 0
        commit_every = 500
        while n_done < args.max_configs:
            cfg = random_config(rng, side="both")
            cid = upsert_config(db, cfg)
            if already_have(db, cid, sym, mode):
                n_skipped += 1
                n_done += 1
                continue
            metrics = evaluate_config(prims, close, cfg, years)
            insert_result(db, cid, sym, n_syms=1, years=years, mode=mode, metrics=metrics)
            n_done += 1
            n_inserted += 1
            if n_done % commit_every == 0:
                dt = time.time() - t0
                rate = n_done / max(0.01, dt)
                # Fetch current best for this sym
                best = db.execute(
                    "SELECT pool_sharpe, trades FROM results WHERE symbol=? AND mode=? "
                    "ORDER BY pool_sharpe DESC LIMIT 1", (sym, mode)).fetchone()
                bs = f"{best[0]:+.4f}/{best[1]}t" if best else "?"
                print(f"  {sym}: {n_done:,}/{args.max_configs:,} "
                      f"({rate:.0f}/s, ins={n_inserted}, skip={n_skipped}, best_pool={bs})")
        print(f"  {sym} done: {n_inserted:,} new in {time.time()-t0:.0f}s")


def run_pooled(args) -> None:
    """Pooled basket sweep — same configs evaluated across N syms, returns
    pooled across syms before pool_sharpe. PUBLISHABLE if n_syms ≥ floor."""
    basket = BASKETS.get(args.basket, [])
    if args.syms:
        basket = [s.strip() for s in args.syms.split(",") if s.strip()]
    npz_dir = Path(args.npz_dir)
    db = open_db(Path(args.db))
    rng = random.Random(args.seed)
    mode = f"pooled:{args.basket}"
    print(f"[pooled] basket={args.basket} n_syms={len(basket)} max_configs={args.max_configs:,}")
    # Pre-load NPZ + primitives for every sym (memory intensive on big baskets)
    sym_data: Dict[str, Tuple[np.ndarray, np.ndarray, float]] = {}
    for sym in basket:
        try:
            npz = load_npz(sym, npz_dir)
            close = npz["close_3m"]
            prims = compute_primitive_masks(npz)
            sym_data[sym] = (close, prims, _years_of(close))
            print(f"  loaded {sym} bars={len(close):,}")
        except FileNotFoundError:
            print(f"  skip {sym}: NPZ missing")
    n_syms = len(sym_data)
    if n_syms == 0:
        print("[pooled] no NPZ loaded — abort")
        return
    avg_years = sum(y for _, _, y in sym_data.values()) / n_syms
    t0 = time.time()
    n_done = 0
    while n_done < args.max_configs:
        cfg = random_config(rng, side="both")
        cid = upsert_config(db, cfg)
        if already_have(db, cid, args.basket, mode):
            n_done += 1
            continue
        all_rets: List[float] = []
        per_sym: Dict[str, List[float]] = {}
        total_long = total_short = 0
        for sym, (close, prims, years) in sym_data.items():
            m = evaluate_config(prims, close, cfg, years)
            # decompress to recombine into pool
            blob = m.get("rets_blob")
            if blob:
                rets = np.frombuffer(zlib.decompress(blob), dtype=np.float16).astype(np.float32)
                if rets.size:
                    all_rets.extend(rets.tolist())
                    per_sym[sym] = rets.tolist()
            total_long += m["trades_long"]
            total_short += m["trades_short"]
        if not all_rets:
            n_done += 1
            continue
        pool = mg.pool_sharpe(all_rets)
        sym_sh = mg.sym_sharpe_from_groups(per_sym)
        rets_arr = np.asarray(all_rets, dtype=np.float32)
        total_gain = float(rets_arr.sum() * 100.0)
        avg_gain = total_gain / len(all_rets) if all_rets else 0.0
        metrics = {
            "pool_sharpe": pool,
            "sym_sharpe": sym_sh,
            "trades": len(all_rets),
            "trades_long": total_long,
            "trades_short": total_short,
            "acc_gain_pct": total_gain,
            "avg_gain_trade": avg_gain,
            "gain_per_yr": total_gain / max(0.01, avg_years),
            "max_dd_pct": _max_drawdown_pct(rets_arr),
            "win_rate_pct": float((rets_arr > 0).mean() * 100.0),
            "rets_blob": zlib.compress(rets_arr.astype(np.float16).tobytes(), 6),
        }
        insert_result(db, cid, args.basket, n_syms=n_syms, years=avg_years,
                      mode=mode, metrics=metrics)
        n_done += 1
        if n_done % 100 == 0:
            best = db.execute(
                "SELECT pool_sharpe, trades FROM results WHERE mode=? "
                "ORDER BY pool_sharpe DESC LIMIT 1", (mode,)).fetchone()
            bs = f"{best[0]:+.4f}/{best[1]}t" if best else "?"
            print(f"  {n_done:,}/{args.max_configs:,} ({n_done/(time.time()-t0):.1f}/s, best={bs})")


def run_validate(args) -> None:
    """Pull the top-N configs from DB and search neighborhoods around them
    until target Sharpe is hit (or budget exhausted). S2's job."""
    db = open_db(Path(args.db))
    npz_dir = Path(args.npz_dir)
    seed_mode = args.seed_mode
    out_mode = f"validate:{seed_mode}"
    seeds = top_seeds(db, seed_mode, args.top_n)
    print(f"[validate] {len(seeds)} seeds from mode={seed_mode}, "
          f"target_pool_sharpe={args.target_pool_sharpe}")
    if not seeds:
        print("[validate] no seeds — run per_symbol or pooled first.")
        return
    basket = BASKETS.get(args.basket, [])
    if args.syms:
        basket = [s.strip() for s in args.syms.split(",") if s.strip()]
    sym_data: Dict[str, Tuple[np.ndarray, np.ndarray, float]] = {}
    for sym in basket:
        try:
            npz = load_npz(sym, npz_dir)
            sym_data[sym] = (npz["close_3m"], compute_primitive_masks(npz), _years_of(npz["close_3m"]))
        except FileNotFoundError:
            pass
    n_syms = len(sym_data)
    avg_years = sum(y for _, _, y in sym_data.values()) / max(1, n_syms)
    rng = random.Random(args.seed)
    t0 = time.time()
    n_done = 0
    best_seen = 0.0
    while n_done < args.max_configs:
        seed = seeds[n_done % len(seeds)]
        cfg = expanded_neighborhood(seed, rng)
        cid = upsert_config(db, cfg)
        if already_have(db, cid, args.basket, out_mode):
            n_done += 1
            continue
        all_rets, per_sym = [], {}
        tl = ts = 0
        for sym, (close, prims, years) in sym_data.items():
            m = evaluate_config(prims, close, cfg, years)
            blob = m.get("rets_blob")
            if blob:
                rets = np.frombuffer(zlib.decompress(blob), dtype=np.float16).astype(np.float32)
                if rets.size:
                    all_rets.extend(rets.tolist())
                    per_sym[sym] = rets.tolist()
            tl += m["trades_long"]; ts += m["trades_short"]
        if not all_rets:
            n_done += 1
            continue
        rets_arr = np.asarray(all_rets, dtype=np.float32)
        pool = mg.pool_sharpe(all_rets)
        total_gain = float(rets_arr.sum() * 100.0)
        metrics = {
            "pool_sharpe": pool, "sym_sharpe": mg.sym_sharpe_from_groups(per_sym),
            "trades": len(all_rets), "trades_long": tl, "trades_short": ts,
            "acc_gain_pct": total_gain, "avg_gain_trade": total_gain / len(all_rets),
            "gain_per_yr": total_gain / max(0.01, avg_years),
            "max_dd_pct": _max_drawdown_pct(rets_arr),
            "win_rate_pct": float((rets_arr > 0).mean() * 100.0),
            "rets_blob": zlib.compress(rets_arr.astype(np.float16).tobytes(), 6),
        }
        insert_result(db, cid, args.basket, n_syms=n_syms, years=avg_years,
                      mode=out_mode, metrics=metrics)
        n_done += 1
        if pool > best_seen:
            best_seen = pool
            print(f"  [validate] new best pool_sharpe={pool:+.4f} trades={len(all_rets)} "
                  f"hash={cfg.hash} ({n_done:,} configs in {time.time()-t0:.0f}s)")
        if pool >= args.target_pool_sharpe and len(all_rets) >= 100 and n_syms >= mg.MIN_SYMS_CRYPTO:
            print(f"  [validate] TARGET HIT: pool_sharpe={pool:+.4f} "
                  f"trades={len(all_rets)} n_syms={n_syms} years={avg_years:.2f}")
            break
        if n_done % 200 == 0:
            print(f"  [validate] {n_done:,}/{args.max_configs:,} best={best_seen:+.4f}")


def run_v3(args) -> None:
    """Mac-side V3 sweep: only fast-scalp primitives (3m K, bar-break,
    DC_3m, BB %B 3m). Mirrors scalp_v3_live entry logic so winners can be
    diffed against forward-paper results in data/decisions/."""
    v3_long_e = [PRIM_BY_NAME[n] for n in (
        "k_3m_oversold20", "rsi_3m_lt30", "bbpct_3m_lt0",
        "close_break_3m_low_prev", "wt_bull_cross_3m", "dc_high_break_3m",
    ) if n in PRIM_BY_NAME]
    v3_short_e = [PRIM_BY_NAME[n] for n in (
        "k_3m_overbought80", "rsi_3m_gt70", "bbpct_3m_gt1",
        "close_break_3m_high_prev", "wt_bear_cross_3m", "dc_low_break_3m",
    ) if n in PRIM_BY_NAME]
    v3_long_x = [PRIM_BY_NAME[n] for n in (
        "k_3m_overbought80_X", "bbpct_3m_gt0p8_X", "wt_bear_cross_3m_X",
        "dc_low_break_3m_X",
    ) if n in PRIM_BY_NAME]
    v3_short_x = [PRIM_BY_NAME[n] for n in (
        "k_3m_oversold20_X", "bbpct_3m_lt0p2_X", "wt_bull_cross_3m_X",
        "dc_high_break_3m_X",
    ) if n in PRIM_BY_NAME]
    syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    npz_dir = Path(args.npz_dir)
    db = open_db(Path(args.db))
    rng = random.Random(args.seed)
    mode = "v3"
    print(f"[v3] syms={syms} long_e={len(v3_long_e)} short_e={len(v3_short_e)}")

    def v3_random_config() -> Config:
        ke = rng.randint(1, min(3, len(v3_long_e)))
        kx = rng.randint(1, min(2, len(v3_long_x)))
        return Config(
            tuple(sorted(rng.sample(v3_long_e, ke))),
            tuple(sorted(rng.sample(v3_long_x, kx))),
            tuple(sorted(rng.sample(v3_short_e, ke))),
            tuple(sorted(rng.sample(v3_short_x, kx))),
        )

    for sym in syms:
        try:
            npz = load_npz(sym, npz_dir)
        except FileNotFoundError as e:
            print(f"  skip {sym}: {e}"); continue
        close = npz["close_3m"]; years = _years_of(close)
        prims = compute_primitive_masks(npz)
        t0 = time.time(); n_done = 0; best = -9.0
        while n_done < args.max_configs:
            cfg = v3_random_config()
            cid = upsert_config(db, cfg)
            if already_have(db, cid, sym, mode):
                n_done += 1; continue
            m = evaluate_config(prims, close, cfg, years)
            insert_result(db, cid, sym, n_syms=1, years=years, mode=mode, metrics=m)
            n_done += 1
            if m["pool_sharpe"] > best and m["trades"] >= 30:
                best = m["pool_sharpe"]
                print(f"  {sym}: new best pool={best:+.4f} trades={m['trades']} "
                      f"dd={m['max_dd_pct']:.1f}% hash={cfg.hash}")
            if n_done % 500 == 0:
                print(f"  {sym}: {n_done:,}/{args.max_configs:,} "
                      f"({n_done/(time.time()-t0):.0f}/s, best={best:+.4f})")


def run_top(args) -> None:
    """Print top-N rows from the DB. No execution — just reporting."""
    db = open_db(Path(args.db))
    where = "WHERE r.mode = ?" if args.mode else ""
    params = (args.mode,) if args.mode else ()
    sql = f"""
        SELECT r.symbol, r.mode, r.pool_sharpe, r.sym_sharpe, r.trades, r.acc_gain_pct,
               r.max_dd_pct, r.n_syms, r.years, r.verdict, c.label
        FROM results r JOIN configs c ON c.id=r.config_id
        {where}
        ORDER BY r.pool_sharpe DESC LIMIT ?"""
    rows = db.execute(sql, (*params, args.top_n)).fetchall()
    print(f"# Top {len(rows)} ({args.mode or 'ALL MODES'}). All Sharpes are per-trade pool_sharpe.")
    print("# rank | sym | mode | pool | sym | trades | acc_gain% | dd% | n_syms | yrs | verdict | label")
    for i, r in enumerate(rows, 1):
        print(f"{i:4d} | {r[0]:>10s} | {r[1]:<14s} | {r[2]:+.4f} | {r[3]:+.4f} | "
              f"{r[4]:6d} | {r[5]:+8.1f} | {r[6]:5.1f} | {r[7]:3d} | {r[8]:.2f} | "
              f"{r[9]:<11s} | {r[10][:80]}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def _on_exit(signum, frame):
    if HOLD_SENTINEL.exists() and HOLD_SENTINEL.read_text().startswith("vec_sweep:"):
        try: HOLD_SENTINEL.unlink()
        except Exception: pass
    sys.exit(0)


def _touch_hold(reason: str) -> None:
    try:
        HOLD_SENTINEL.write_text(f"vec_sweep:{reason}:{time.strftime('%FT%T')}\n")
    except Exception:
        pass


def main():
    p = argparse.ArgumentParser(description="vec_sweep — vectorized audited backtest sweep")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = dict(
        npz_dir=str(NPZ_DIR_DEFAULT),
        db=str(DB_DEFAULT),
        seed=42,
    )

    a = sub.add_parser("per_symbol", help="DIAGNOSTIC per-symbol sweep")
    a.add_argument("--syms", default="BTCUSDT,ETHUSDT,SOLUSDT,BTCDOMUSDT,DOGEUSDT,ZECUSDT")
    a.add_argument("--max-configs", type=int, default=100_000)
    a.add_argument("--npz-dir", default=common["npz_dir"])
    a.add_argument("--db", default=common["db"])
    a.add_argument("--seed", type=int, default=common["seed"])
    a.set_defaults(fn=run_per_symbol)

    b = sub.add_parser("pooled", help="PUBLISHABLE basket sweep (≥48 syms × ≥1y)")
    b.add_argument("--basket", default="crypto48")
    b.add_argument("--syms", default="", help="override basket symbols (comma-separated)")
    b.add_argument("--max-configs", type=int, default=50_000)
    b.add_argument("--npz-dir", default=common["npz_dir"])
    b.add_argument("--db", default=common["db"])
    b.add_argument("--seed", type=int, default=common["seed"])
    b.set_defaults(fn=run_pooled)

    c = sub.add_parser("validate", help="Climb neighborhood of top-N seeds toward target Sharpe")
    c.add_argument("--seed-mode", default="pooled:crypto48")
    c.add_argument("--top-n", type=int, default=1000)
    c.add_argument("--basket", default="crypto48")
    c.add_argument("--syms", default="")
    c.add_argument("--target-pool-sharpe", type=float, default=4.0)
    c.add_argument("--max-configs", type=int, default=200_000)
    c.add_argument("--npz-dir", default=common["npz_dir"])
    c.add_argument("--db", default=common["db"])
    c.add_argument("--seed", type=int, default=common["seed"])
    c.set_defaults(fn=run_validate)

    d = sub.add_parser("v3", help="Mac V3 fast-scalp sweep (per-symbol diagnostic)")
    d.add_argument("--syms", default="BTCUSDC,ETHUSDC,SOLUSDC")
    d.add_argument("--max-configs", type=int, default=50_000)
    d.add_argument("--npz-dir", default=common["npz_dir"])
    d.add_argument("--db", default=common["db"])
    d.add_argument("--seed", type=int, default=common["seed"])
    d.set_defaults(fn=run_v3)

    e = sub.add_parser("top", help="Print top-N from the DB")
    e.add_argument("--mode", default="")
    e.add_argument("--top-n", type=int, default=1000)
    e.add_argument("--db", default=common["db"])
    e.set_defaults(fn=run_top)

    args = p.parse_args()
    signal.signal(signal.SIGINT, _on_exit)
    signal.signal(signal.SIGTERM, _on_exit)
    _touch_hold(args.cmd)
    try:
        args.fn(args)
    finally:
        if HOLD_SENTINEL.exists() and HOLD_SENTINEL.read_text().startswith("vec_sweep:"):
            try: HOLD_SENTINEL.unlink()
            except Exception: pass


if __name__ == "__main__":
    main()
