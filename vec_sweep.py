#!/usr/bin/env python3
"""vec_sweep.py — fully-vectorized backtest sweep, audited Sharpe, SQLite store.

Built per CLAUDE.md NO-LIES MANDATE. Every Sharpe in the DB is per-trade
pool_sharpe = mean(returns)/std(returns). NO annualization, NO sqrt(N), NO
sym-avg masquerading as pool. Open trades at last bar are MtM'd (rule 2).
Per-trade returns are stored as a zlib'd float16 blob alongside every result
so any number can be recomputed via metrics_guard.pool_sharpe(returns).

EVERY NPZ FIELD IS SWITCHABLE WITH SWEEPABLE THRESHOLDS
=======================================================
On startup, vec_sweep reads one sample NPZ and auto-classifies every field
(530 in current backtest_v8/indicators/ files) into a PrimitiveFamily:

  - bool-like  (crossover/crossunder/detected/_pass/_long/_short/state):
        materialized as `field == 1` (or `==-1` for the negative side).
        1 primitive per field per side.
  - oscillator (rsi/k_/d_/mfi/bb_pct_b/wt_*):
        materialized as `field >= T` and `field <= T`, with T sweeping the
        oscillator range. ~10 thresholds per side per field.
  - centered-zero (macd/wt1/wt2/clenow_*/funding_rate/ratio):
        thresholds at percentiles {5,10,25,50,75,90,95} of the sample, both
        sides.
  - unbounded-pos (atr/dc_*/close/volume/etc.):
        thresholds at percentiles {10,25,50,75,90} for `>=` and `<=`. Plus
        cross-field comparisons against `close` (e.g. close>dc_high_4h).

The family + threshold tuple is the primitive id. Cartesian product over
(k entry primitives) AND (k exit primitives) for both long and short =
order of 10^15 unique configs. We don't enumerate; we sample randomly and
let the DB act as the leaderboard.

Switchability: every family has `enabled=True` by default. Pass
`--disable-families "atr_*,kc_*"` (glob) to drop entire families from the
grid. Pass `--ranges-json path` to override default thresholds.

USAGE
=====
  # Catalog of all auto-generated primitive families:
  python3 vec_sweep.py catalog --sample-sym BTCUSDT

  # Per-symbol diagnostic sweep (priority 6 syms):
  python3 vec_sweep.py per_symbol --syms BTCUSDT,ETHUSDT,SOLUSDT,BTCDOMUSDT,DOGEUSDT,ZECUSDT --max-configs 200000

  # Pooled 48-sym crypto basket (publishable):
  python3 vec_sweep.py pooled --basket crypto48 --max-configs 50000

  # S2: validate seeds → climb to Sharpe target:
  python3 vec_sweep.py validate --seed-mode pooled:crypto48 --top-n 1000 --target-pool-sharpe 4.0

  # Mac: V3 fast-scalp (3m primitives only):
  python3 vec_sweep.py v3 --syms BTCUSDC,ETHUSDC,SOLUSDC --max-configs 100000

  # Read DB:
  python3 vec_sweep.py top --mode pooled:crypto48 --top-n 1000

LIMITATIONS (honest)
====================
  * Single-symbol Sharpes are DIAGNOSTIC by CLAUDE.md rule 5. Per-symbol
    rows in this DB cannot promote to live alone — only via a ≥48-sym
    (crypto) / ≥100-sym (stocks) pooled re-run.
  * No commission/slippage model. Subtract a haircut in `_trade_returns`
    if you want to bake one in.
  * "Trillions of configs" is a property of the search space; we sample.
  * Cache is LRU 4000 masks (~5GB for 1.1M-bar series). Bump
    MASK_CACHE_LIMIT if you have RAM.
"""
from __future__ import annotations

import argparse
import collections
import fnmatch
import hashlib
import json
import os
import random
import re
import signal
import sqlite3
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
MASK_CACHE_LIMIT = 4000          # LRU cap; ~137KB packed/1.1MB bool per mask
SAMPLE_SIZE_FOR_PERCENTILES = 200_000  # bars used to derive percentile thresholds

BASKETS: Dict[str, List[str]] = {
    "crypto48": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "BNBUSDT", "AVAXUSDT",
        "XRPUSDT", "LINKUSDT", "LTCUSDT", "UNIUSDT",
        "1INCHUSDT", "ALGOUSDT", "ANKRUSDT", "ATOMUSDT", "AXSUSDT", "BANDUSDT",
        "BATUSDT", "BELUSDT", "BTCDOMUSDT", "C98USDT", "CELRUSDT", "CHRUSDT",
        "COMPUSDT", "COTIUSDT", "DASHUSDT", "DOTUSDT", "EGLDUSDT", "ENJUSDT",
        "ETCUSDT", "GRTUSDT", "GTCUSDT", "HOTUSDT", "IOSTUSDT", "IOTAUSDT",
        "IOTXUSDT", "KAVAUSDT", "KNCUSDT", "KSMUSDT", "LRCUSDT", "MANAUSDT",
        "MTLUSDT", "NKNUSDT", "QTUMUSDT", "RLCUSDT", "RSRUSDT", "RVNUSDT",
        "SANDUSDT", "SKLUSDT",
    ],
    "priority6": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCDOMUSDT", "DOGEUSDT", "ZECUSDT"],
}

# Field-name regex → side hint. Used only for bool-like primitives where the
# name carries direction info; oscillators/centered/unbounded sample both sides.
SIDE_HINTS = [
    (re.compile(r"(crossover|bull|long|breakout|detected|pass|above)"), ("long_entry", "short_exit")),
    (re.compile(r"(crossunder|bear|short|breakdown|below)"),            ("short_entry", "long_exit")),
]

OSCILLATOR_PATTERNS = re.compile(
    r"^(rsi|k_|d_|mfi|stoch|bb_pct_b|wt_overbought|wt_oversold)", re.IGNORECASE
)
CENTERED_PATTERNS = re.compile(
    r"^(macd|wt1_|wt2_|wt_composite|clenow_score|clenow_slope|funding_rate|"
    r"pct_from|wt_momentum_state|wt_composite_delta|wt_falling|wt_rising|"
    r"oi_|delta_|vol_z)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# AUTO PRIMITIVE FAMILIES
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PrimitiveFamily:
    """A field + a sweep specification. Materializes into N primitives, one
    per threshold."""
    field: str
    kind: str                   # "bool", "oscillator", "centered", "unbounded", "signed", "cross"
    sides: Tuple[str, ...]      # which sides this family contributes primitives to
    thresholds: Tuple[float, ...]  # empty for bool kind
    ops: Tuple[str, ...]        # subset of {"ge","le","eq","ne"}; for cross-field "gt_field:<other>"
    enabled: bool = True
    # True when the field's threshold is comparable across symbols. Bool,
    # oscillator, cross, and centered-around-zero are symbol-relative;
    # unbounded-positive (atr, dc_*, bb_lower, etc.) and signed-with-large-
    # magnitude are not. Pooled/validate runners drop non-relative families
    # by default to avoid "atr>=70.7 USD" applied to ETH.
    symbol_relative: bool = True


def _classify_field(name: str, arr: np.ndarray) -> Optional[Tuple[str, Tuple[str, ...], Tuple[float, ...], Tuple[str, ...], bool]]:
    """Classify a single NPZ field. Returns (kind, sides, thresholds, ops) or
    None if field should be excluded (constant, NaN-only, time/string)."""
    if name in ("timestamps", "close"):
        return None
    if arr.dtype.kind not in "fiub":
        return None
    finite = arr[np.isfinite(arr)]
    if finite.size < 1000:
        return None
    uniq_sample = np.unique(finite[: min(50_000, finite.size)])
    is_bool_like = uniq_sample.size <= 4 and np.all(np.isin(uniq_sample, np.array([-1.0, 0.0, 1.0, 2.0])))
    p1, p10, p25, p50, p75, p90, p99 = np.percentile(finite, [1, 10, 25, 50, 75, 90, 99])

    sides_long = ("long_entry", "short_exit")
    sides_short = ("short_entry", "long_exit")
    sides_both = sides_long + sides_short

    if is_bool_like:
        sides = sides_both
        for rx, s in SIDE_HINTS:
            if rx.search(name):
                sides = s
                break
        return ("bool", sides, (1.0,), ("eq",), True)

    is_osc = bool(OSCILLATOR_PATTERNS.search(name)) or (p1 >= -5 and p99 <= 105)
    if is_osc:
        thresholds = tuple(float(t) for t in (10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90))
        return ("oscillator", sides_both, thresholds, ("ge", "le"), True)

    is_centered = bool(CENTERED_PATTERNS.search(name)) or (p10 < 0 and p90 > 0 and abs(p50) < (p90 - p10) * 0.3)
    if is_centered:
        candidates = sorted(set([float(x) for x in (p10, p25, p50, p75, p90, 0.0)]))
        # Centered fields whose magnitudes are price-scaled (macd_*) are
        # NOT symbol-relative; flag accordingly.
        sym_rel = bool(re.match(r"^(wt|funding_rate|pct_from|wt_momentum_state|"
                                r"clenow_score|delta_)", name)) or (abs(p90) < 50 and abs(p10) < 50)
        return ("centered", sides_both, tuple(candidates), ("ge", "le"), sym_rel)

    if p10 < 0 < p90:
        return ("signed", sides_both, (float(p10), float(p25), 0.0, float(p75), float(p90)),
                ("ge", "le"), abs(p10) < 100 and abs(p90) < 100)

    # Unbounded positive — absolute thresholds are NOT comparable across syms.
    return ("unbounded", sides_both,
            (float(p10), float(p25), float(p50), float(p75), float(p90)),
            ("ge", "le"), False)


def auto_families(sample_npz: Dict[str, np.ndarray],
                  enabled_globs: Sequence[str] = ("*",),
                  disabled_globs: Sequence[str] = ()) -> List[PrimitiveFamily]:
    """Build the recipe catalog from one sample NPZ. Idempotent."""
    fams: List[PrimitiveFamily] = []
    for name in sorted(sample_npz.keys()):
        if any(fnmatch.fnmatchcase(name, g) for g in disabled_globs):
            continue
        if not any(fnmatch.fnmatchcase(name, g) for g in enabled_globs):
            continue
        arr = sample_npz[name]
        if arr.ndim != 1:
            continue
        cls = _classify_field(name, arr)
        if cls is None:
            continue
        kind, sides, thresholds, ops, sym_rel = cls
        fams.append(PrimitiveFamily(field=name, kind=kind, sides=sides,
                                    thresholds=thresholds, ops=ops, enabled=True,
                                    symbol_relative=sym_rel))
    # Cross-field price-vs-channel primitives (always symbol-relative).
    for tf in ("3m", "15m", "1h", "4h", "D"):
        for upper, lower in (("dc_high_" + tf, "dc_low_" + tf),
                             ("bb_upper_" + tf, "bb_lower_" + tf),
                             ("kc_upper_" + tf, "kc_lower_" + tf)):
            if upper in sample_npz and lower in sample_npz:
                fams.append(PrimitiveFamily(
                    field=f"close_above_{upper}", kind="cross",
                    sides=("long_entry", "short_exit"), thresholds=(0.0,),
                    ops=("gt_field:" + upper,), symbol_relative=True))
                fams.append(PrimitiveFamily(
                    field=f"close_below_{lower}", kind="cross",
                    sides=("short_entry", "long_exit"), thresholds=(0.0,),
                    ops=("lt_field:" + lower,), symbol_relative=True))
    return fams


# ---------------------------------------------------------------------------
# PRIMITIVE = (family, op, threshold) — hashable id
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Primitive:
    field: str
    op: str
    threshold: float

    @property
    def name(self) -> str:
        if self.op == "eq":
            return f"{self.field}=={self.threshold:g}"
        if self.op.startswith("gt_field:"):
            return f"{self.field}>{self.op.split(':',1)[1]}"
        if self.op.startswith("lt_field:"):
            return f"{self.field}<{self.op.split(':',1)[1]}"
        return f"{self.field}{self.op}{self.threshold:g}"

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.field}|{self.op}|{self.threshold:.10g}".encode()).hexdigest()[:14]


def materialize(fam: PrimitiveFamily, threshold_idx: int, op: str) -> Primitive:
    if fam.kind == "cross":
        return Primitive(field=fam.field, op=fam.ops[0], threshold=0.0)
    return Primitive(field=fam.field, op=op, threshold=fam.thresholds[threshold_idx])


def compute_mask(npz: Dict[str, np.ndarray], prim: Primitive) -> np.ndarray:
    """Materialize a single primitive's bool mask. Pure function over npz.
    Returns all-False if any referenced field is missing in this symbol's
    NPZ — pooled mode runs across symbols with non-identical schemas."""
    n = len(npz["close_3m"])
    op = prim.op
    if op.startswith("gt_field:"):
        other = op.split(":", 1)[1]
        if other not in npz:
            return np.zeros(n, dtype=bool)
        a = npz.get("close", npz["close_3m"])
        return np.asarray(a > npz[other], dtype=bool)
    if op.startswith("lt_field:"):
        other = op.split(":", 1)[1]
        if other not in npz:
            return np.zeros(n, dtype=bool)
        a = npz.get("close", npz["close_3m"])
        return np.asarray(a < npz[other], dtype=bool)
    if prim.field not in npz:
        return np.zeros(n, dtype=bool)
    a = npz[prim.field]
    t = prim.threshold
    if op == "ge":
        return np.asarray(a >= t, dtype=bool)
    if op == "le":
        return np.asarray(a <= t, dtype=bool)
    if op == "eq":
        return np.asarray(a == t, dtype=bool)
    if op == "ne":
        return np.asarray(a != t, dtype=bool)
    return np.zeros(len(npz["close_3m"]), dtype=bool)


class MaskCache:
    """LRU bool-mask cache keyed by primitive.id, scoped to one symbol."""
    def __init__(self, npz: Dict[str, np.ndarray], limit: int = MASK_CACHE_LIMIT):
        self.npz = npz
        self.limit = limit
        self.store: "collections.OrderedDict[str, np.ndarray]" = collections.OrderedDict()

    def get(self, prim: Primitive) -> np.ndarray:
        pid = prim.id
        if pid in self.store:
            self.store.move_to_end(pid)
            return self.store[pid]
        m = compute_mask(self.npz, prim)
        self.store[pid] = m
        if len(self.store) > self.limit:
            self.store.popitem(last=False)
        return m

    def and_many(self, prims: Sequence[Primitive]) -> np.ndarray:
        if not prims:
            return np.zeros(len(self.npz["close_3m"]), dtype=bool)
        out = self.get(prims[0]).copy()
        for p in prims[1:]:
            out &= self.get(p)
        return out


# ---------------------------------------------------------------------------
# CONFIG (4 sets of primitives)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    entry_long: Tuple[Primitive, ...]
    exit_long: Tuple[Primitive, ...]
    entry_short: Tuple[Primitive, ...]
    exit_short: Tuple[Primitive, ...]

    @property
    def hash(self) -> str:
        ids = (tuple(sorted(p.id for p in self.entry_long)),
               tuple(sorted(p.id for p in self.exit_long)),
               tuple(sorted(p.id for p in self.entry_short)),
               tuple(sorted(p.id for p in self.exit_short)))
        return hashlib.sha1(json.dumps(ids).encode()).hexdigest()[:20]

    def label(self) -> str:
        def ns(ps): return "+".join(p.name for p in ps) or "_"
        return f"EL[{ns(self.entry_long)}] XL[{ns(self.exit_long)}] ES[{ns(self.entry_short)}] XS[{ns(self.exit_short)}]"

    def to_json(self) -> str:
        def dump(ps): return [{"f": p.field, "op": p.op, "t": p.threshold} for p in ps]
        return json.dumps({
            "entry_long": dump(self.entry_long), "exit_long": dump(self.exit_long),
            "entry_short": dump(self.entry_short), "exit_short": dump(self.exit_short),
        }, separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "Config":
        d = json.loads(s)
        def load(lst): return tuple(Primitive(x["f"], x["op"], float(x["t"])) for x in lst)
        return Config(load(d["entry_long"]), load(d["exit_long"]),
                      load(d["entry_short"]), load(d["exit_short"]))


def random_config(rng: random.Random, families: List[PrimitiveFamily],
                  k_entry: Tuple[int, int] = (1, 4),
                  k_exit: Tuple[int, int] = (1, 3),
                  side: str = "both") -> Config:
    """Sample one config by drawing K primitives per side from the enabled
    family pool, with one threshold per family."""
    by_side: Dict[str, List[PrimitiveFamily]] = collections.defaultdict(list)
    for f in families:
        if not f.enabled:
            continue
        for s in f.sides:
            by_side[s].append(f)

    def pick(side_key: str, lo: int, hi: int) -> Tuple[Primitive, ...]:
        pool = by_side.get(side_key, [])
        if not pool:
            return tuple()
        k = rng.randint(lo, min(hi, len(pool)))
        chosen_fams = rng.sample(pool, k)
        prims: List[Primitive] = []
        for fam in chosen_fams:
            if fam.kind == "bool" or fam.kind == "cross":
                prims.append(materialize(fam, 0, fam.ops[0]))
            else:
                op = rng.choice(fam.ops)
                ti = rng.randrange(len(fam.thresholds))
                prims.append(materialize(fam, ti, op))
        return tuple(sorted(prims, key=lambda p: (p.field, p.op, p.threshold)))

    el = pick("long_entry", *k_entry) if side in ("long", "both") else tuple()
    xl = pick("long_exit", *k_exit) if side in ("long", "both") else tuple()
    es = pick("short_entry", *k_entry) if side in ("short", "both") else tuple()
    xs = pick("short_exit", *k_exit) if side in ("short", "both") else tuple()
    return Config(el, xl, es, xs)


def neighbor_config(seed: Config, families: List[PrimitiveFamily],
                    rng: random.Random) -> Config:
    """Mutate exactly one primitive — swap threshold OR replace with a new
    family — in one of the 4 sets. Used by validate-mode to climb."""
    sets = ["entry_long", "exit_long", "entry_short", "exit_short"]
    s = rng.choice(sets)
    cur = list(getattr(seed, s))
    if not cur:
        # add one
        side_key = {"entry_long": "long_entry", "exit_long": "long_exit",
                    "entry_short": "short_entry", "exit_short": "short_exit"}[s]
        pool = [f for f in families if f.enabled and side_key in f.sides]
        if not pool:
            return seed
        fam = rng.choice(pool)
        if fam.kind in ("bool", "cross"):
            new = [materialize(fam, 0, fam.ops[0])]
        else:
            new = [materialize(fam, rng.randrange(len(fam.thresholds)), rng.choice(fam.ops))]
        new_kw = {a: getattr(seed, a) for a in sets}
        new_kw[s] = tuple(new)
        return Config(**new_kw)
    # mutate one entry
    idx = rng.randrange(len(cur))
    target = cur[idx]
    fam = next((f for f in families if f.field == target.field), None)
    if fam and fam.kind not in ("bool", "cross"):
        # nudge threshold
        ops = list(fam.ops); ops.append(target.op)
        new_op = rng.choice(ops)
        new_t = rng.choice(fam.thresholds)
        cur[idx] = Primitive(target.field, new_op, float(new_t))
    else:
        # replace family
        side_key = {"entry_long": "long_entry", "exit_long": "long_exit",
                    "entry_short": "short_entry", "exit_short": "short_exit"}[s]
        pool = [f for f in families if f.enabled and side_key in f.sides
                and f.field != target.field]
        if pool:
            f2 = rng.choice(pool)
            if f2.kind in ("bool", "cross"):
                cur[idx] = materialize(f2, 0, f2.ops[0])
            else:
                cur[idx] = materialize(f2, rng.randrange(len(f2.thresholds)), rng.choice(f2.ops))
    cur = tuple(sorted(cur, key=lambda p: (p.field, p.op, p.threshold)))
    new_kw = {a: getattr(seed, a) for a in sets}
    new_kw[s] = cur
    return Config(**new_kw)


# ---------------------------------------------------------------------------
# TRADE LOOP (vectorized)
# ---------------------------------------------------------------------------

def _trade_returns(entry_mask: np.ndarray, exit_mask: np.ndarray,
                   close: np.ndarray, direction: int) -> np.ndarray:
    entry_bars = np.flatnonzero(entry_mask)
    if entry_bars.size == 0:
        return np.empty(0, dtype=np.float32)
    exit_bars = np.flatnonzero(exit_mask)
    rets: List[float] = []
    last_exit = -1
    n = len(close)
    for e in entry_bars:
        if e <= last_exit:
            continue
        if exit_bars.size:
            idx = np.searchsorted(exit_bars, e + 1)
            xb = int(exit_bars[idx]) if idx < exit_bars.size else (n - 1)
        else:
            xb = n - 1
        ep = float(close[e]); xp = float(close[xb])
        if ep <= 0 or not np.isfinite(ep) or not np.isfinite(xp):
            continue
        r = (xp - ep) / ep
        if direction < 0:
            r = -r
        rets.append(r)
        last_exit = xb
        if xb >= n - 1:
            break
    return np.asarray(rets, dtype=np.float32)


def _max_drawdown_pct(rets: np.ndarray) -> float:
    if rets.size == 0:
        return 0.0
    eq = np.cumsum(rets)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq).max()
    return float(dd * 100.0)


def evaluate_config(cache: MaskCache, close: np.ndarray, cfg: Config,
                    n_years: float) -> Dict[str, object]:
    el = cache.and_many(cfg.entry_long) if cfg.entry_long else np.zeros(len(close), dtype=bool)
    xl = cache.and_many(cfg.exit_long) if cfg.exit_long else np.zeros(len(close), dtype=bool)
    es = cache.and_many(cfg.entry_short) if cfg.entry_short else np.zeros(len(close), dtype=bool)
    xs = cache.and_many(cfg.exit_short) if cfg.exit_short else np.zeros(len(close), dtype=bool)
    rets_l = _trade_returns(el, xl, close, +1)
    rets_s = _trade_returns(es, xs, close, -1)
    rets = np.concatenate([rets_l, rets_s]) if rets_l.size or rets_s.size else np.empty(0, dtype=np.float32)
    n = int(rets.size)
    pool = mg.pool_sharpe(rets.tolist()) if n >= 2 else 0.0
    total_gain = float(rets.sum() * 100.0)
    avg_gain = (total_gain / n) if n else 0.0
    return {
        "pool_sharpe": pool, "sym_sharpe": pool,
        "trades": n, "trades_long": int(rets_l.size), "trades_short": int(rets_s.size),
        "acc_gain_pct": total_gain, "avg_gain_trade": avg_gain,
        "gain_per_yr": total_gain / max(0.01, n_years),
        "max_dd_pct": _max_drawdown_pct(rets),
        "win_rate_pct": float((rets > 0).mean() * 100.0) if n else 0.0,
        "rets_blob": zlib.compress(rets.astype(np.float16).tobytes(), 6),
    }


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
CREATE TABLE IF NOT EXISTS family_catalog (
    field TEXT PRIMARY KEY, kind TEXT, sides TEXT,
    thresholds TEXT, ops TEXT, enabled INTEGER
);
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
    r = con.execute("SELECT id FROM configs WHERE config_hash=?", (h,)).fetchone()
    if r:
        return r[0]
    con.execute("INSERT INTO configs(config_hash, config_json, label) VALUES(?,?,?)",
                (h, cfg.to_json(), cfg.label()[:300]))
    return int(con.execute("SELECT id FROM configs WHERE config_hash=?", (h,)).fetchone()[0])


def already_have(con: sqlite3.Connection, config_id: int, sym: str, mode: str) -> bool:
    return con.execute("SELECT 1 FROM results WHERE config_id=? AND symbol=? AND mode=?",
                       (config_id, sym, mode)).fetchone() is not None


def insert_result(con: sqlite3.Connection, config_id: int, sym: str,
                  n_syms: int, years: float, mode: str, metrics: Dict[str, object]) -> None:
    floor = mg.MIN_SYMS_STOCKS if "tradier" in mode else mg.MIN_SYMS_CRYPTO
    publishable = (n_syms >= floor) and (years >= mg.MIN_YEARS) and (metrics["trades"] >= 30 * max(1, n_syms))
    verdict = "PUBLISHABLE" if publishable else "DIAGNOSTIC"
    gsym_yr = (metrics["acc_gain_pct"] / max(1, n_syms)) / max(0.01, years)
    con.execute("""INSERT OR REPLACE INTO results
        (config_id, symbol, n_syms, years, pool_sharpe, sym_sharpe, trades,
         trades_long, trades_short, acc_gain_pct, avg_gain_trade, gain_per_yr,
         gain_sym_yr, max_dd_pct, win_rate_pct, verdict, rets_blob, mode)
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (config_id, sym, n_syms, years,
         metrics["pool_sharpe"], metrics["sym_sharpe"], metrics["trades"],
         metrics["trades_long"], metrics["trades_short"], metrics["acc_gain_pct"],
         metrics["avg_gain_trade"], metrics["gain_per_yr"], gsym_yr,
         metrics["max_dd_pct"], metrics["win_rate_pct"], verdict,
         metrics.get("rets_blob"), mode))


def write_family_catalog(con: sqlite3.Connection, fams: List[PrimitiveFamily]) -> None:
    con.execute("DELETE FROM family_catalog")
    for f in fams:
        con.execute("INSERT INTO family_catalog VALUES(?,?,?,?,?,?)",
                    (f.field, f.kind, json.dumps(list(f.sides)),
                     json.dumps(list(f.thresholds)), json.dumps(list(f.ops)),
                     int(f.enabled)))


def top_seeds(con: sqlite3.Connection, mode: str, n: int,
              allowed_fields: Optional[set] = None) -> List[Config]:
    """Pull top-N configs from a mode. If `allowed_fields` provided, drops
    primitives whose field is not in the allow-set — used by pooled
    validate-mode to strip absolute-price primitives from per-symbol seeds
    before mutating, so seed contamination doesn't produce filter-by-
    coincidence pooled Sharpes."""
    rows = con.execute(
        "SELECT c.config_json FROM results r JOIN configs c ON c.id=r.config_id "
        "WHERE r.mode=? AND r.trades>=30 ORDER BY r.pool_sharpe DESC LIMIT ?",
        (mode, n)).fetchall()
    out: List[Config] = []
    for (j,) in rows:
        try:
            cfg = Config.from_json(j)
            if allowed_fields is not None:
                def filt(prims):
                    return tuple(p for p in prims if p.field in allowed_fields
                                 or p.op.startswith("gt_field:") or p.op.startswith("lt_field:"))
                cfg = Config(filt(cfg.entry_long), filt(cfg.exit_long),
                             filt(cfg.entry_short), filt(cfg.exit_short))
                if not (cfg.entry_long or cfg.exit_long or cfg.entry_short or cfg.exit_short):
                    continue
            out.append(cfg)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# RUNNERS
# ---------------------------------------------------------------------------

def _years_of(close: np.ndarray) -> float:
    return len(close) / BARS_PER_YEAR_3M


def _load_npz(sym: str, npz_dir: Path) -> Dict[str, np.ndarray]:
    p = npz_dir / f"{sym}.npz"
    if not p.exists():
        raise FileNotFoundError(f"{p}")
    return dict(np.load(p, allow_pickle=False))


_HTF_SUFFIX_RE = re.compile(r"_(4h|D|W|M|D_prev|W_prev|M_prev|D_ant|W_ant|M_ant)$|^wt_(composite|bull_alignment|bear_alignment|cross_count|falling|rising|peak|trough)")


def _families_for(args, sample_npz: Dict[str, np.ndarray],
                  symbol_relative_only: bool = False) -> List[PrimitiveFamily]:
    enabled = tuple((args.enable_families or "*").split(","))
    disabled = tuple(g for g in (args.disable_families or "").split(",") if g)
    fams = auto_families(sample_npz, enabled_globs=enabled, disabled_globs=disabled)
    if symbol_relative_only or getattr(args, "symbol_relative_only", False):
        fams = [f for f in fams if f.symbol_relative]
    tf = getattr(args, "tf_filter", "any")
    if tf == "htf_only":
        # Keep only 4h/D/W/M-suffixed fields + multi-TF wt aggregates.
        fams = [f for f in fams if _HTF_SUFFIX_RE.search(f.field)]
    elif tf == "no_3m":
        # Drop 3m-only primitives (often noise-firing); keep 15m up.
        fams = [f for f in fams if not f.field.endswith("_3m") and not f.field.endswith("_3m_prev")]
    return fams


def run_per_symbol(args) -> None:
    syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    db = open_db(Path(args.db))
    rng = random.Random(args.seed)
    npz_dir = Path(args.npz_dir)
    mode = "per_symbol"
    print(f"[per_symbol] syms={syms} max_configs={args.max_configs:,}")
    sample = _load_npz(syms[0], npz_dir)
    fams = _families_for(args, sample)
    write_family_catalog(db, fams)
    n_prims = sum(len(f.thresholds) * len(f.ops) for f in fams)
    print(f"[per_symbol] {len(fams)} families → {n_prims:,} primitives in pool")
    for sym in syms:
        try: npz = _load_npz(sym, npz_dir)
        except FileNotFoundError as e: print(f"  skip {sym}: {e}"); continue
        close = npz["close_3m"]; years = _years_of(close)
        cache = MaskCache(npz)
        print(f"  {sym}: bars={len(close):,} years={years:.2f}")
        t0 = time.time(); n_done = n_skipped = 0; best = -9.0
        while n_done < args.max_configs:
            cfg = random_config(rng, fams, side="both")
            cid = upsert_config(db, cfg)
            if already_have(db, cid, sym, mode):
                n_skipped += 1; n_done += 1; continue
            m = evaluate_config(cache, close, cfg, years)
            insert_result(db, cid, sym, n_syms=1, years=years, mode=mode, metrics=m)
            n_done += 1
            if m["pool_sharpe"] > best and m["trades"] >= 30:
                best = m["pool_sharpe"]
                print(f"    {sym} new best pool_sharpe={best:+.4f} trades={m['trades']} dd={m['max_dd_pct']:.1f}%")
            if n_done % 500 == 0:
                rate = n_done / max(0.01, time.time() - t0)
                print(f"    {sym}: {n_done:,}/{args.max_configs:,} ({rate:.0f}/s, skip={n_skipped}, best={best:+.4f})")
        print(f"  {sym} done in {time.time()-t0:.0f}s")


def run_pooled(args) -> None:
    basket = BASKETS.get(args.basket, [])
    if args.syms:
        basket = [s.strip() for s in args.syms.split(",") if s.strip()]
    db = open_db(Path(args.db))
    rng = random.Random(args.seed)
    npz_dir = Path(args.npz_dir)
    mode = f"pooled:{args.basket}"
    print(f"[pooled] basket={args.basket} n_syms={len(basket)} max_configs={args.max_configs:,}")
    sym_data: Dict[str, Tuple[np.ndarray, MaskCache, float]] = {}
    sample: Optional[Dict[str, np.ndarray]] = None
    for sym in basket:
        try:
            npz = _load_npz(sym, npz_dir)
            close = npz["close_3m"]
            sym_data[sym] = (close, MaskCache(npz, limit=600), _years_of(close))
            if sample is None:
                sample = npz
        except FileNotFoundError:
            print(f"  skip {sym}: NPZ missing")
    if not sym_data: print("[pooled] no data — abort"); return
    fams = _families_for(args, sample, symbol_relative_only=True); write_family_catalog(db, fams)
    n_syms = len(sym_data); avg_years = sum(y for _, _, y in sym_data.values()) / n_syms
    print(f"[pooled] {len(fams)} families loaded; n_syms={n_syms} avg_years={avg_years:.2f}")
    t0 = time.time(); n_done = 0; best = -9.0
    while n_done < args.max_configs:
        cfg = random_config(rng, fams, side="both")
        cid = upsert_config(db, cfg)
        if already_have(db, cid, args.basket, mode):
            n_done += 1; continue
        all_rets: List[float] = []; per_sym: Dict[str, List[float]] = {}; tl = ts = 0
        for sym, (close, cache, years) in sym_data.items():
            m = evaluate_config(cache, close, cfg, years)
            blob = m.get("rets_blob")
            if blob:
                rets = np.frombuffer(zlib.decompress(blob), dtype=np.float16).astype(np.float32)
                if rets.size:
                    all_rets.extend(rets.tolist())
                    per_sym[sym] = rets.tolist()
            tl += m["trades_long"]; ts += m["trades_short"]
        if not all_rets: n_done += 1; continue
        rets_arr = np.asarray(all_rets, dtype=np.float32)
        pool = mg.pool_sharpe(all_rets); ssh = mg.sym_sharpe_from_groups(per_sym)
        total_gain = float(rets_arr.sum() * 100.0)
        metrics = {
            "pool_sharpe": pool, "sym_sharpe": ssh, "trades": len(all_rets),
            "trades_long": tl, "trades_short": ts, "acc_gain_pct": total_gain,
            "avg_gain_trade": total_gain / len(all_rets),
            "gain_per_yr": total_gain / max(0.01, avg_years),
            "max_dd_pct": _max_drawdown_pct(rets_arr),
            "win_rate_pct": float((rets_arr > 0).mean() * 100.0),
            "rets_blob": zlib.compress(rets_arr.astype(np.float16).tobytes(), 6),
        }
        # Trade-frequency gates: drop overtrading and undertrading configs at insert time.
        tps = len(all_rets) / max(1, n_syms) / max(0.01, avg_years)
        max_tps = getattr(args, "max_tps_per_yr", 0) or 0
        min_tps = getattr(args, "min_tps_per_yr", 0) or 0
        if (max_tps > 0 and tps > max_tps) or (min_tps > 0 and tps < min_tps):
            n_done += 1
            continue
        insert_result(db, cid, args.basket, n_syms=n_syms, years=avg_years, mode=mode, metrics=metrics)
        n_done += 1
        # Same sample-floor gate as validate-mode.
        sample_ok = len(all_rets) >= 30 * n_syms and (abs(pool) <= 5.0 or len(all_rets) >= 5000)
        if pool > best and sample_ok:
            best = pool
            print(f"  [pooled] new best pool_sharpe={pool:+.4f} sym_sharpe={ssh:+.4f} "
                  f"trades={len(all_rets)} hash={cfg.hash}")
        if n_done % 50 == 0:
            print(f"  [pooled] {n_done:,}/{args.max_configs:,} ({n_done/(time.time()-t0):.2f}/s, best={best:+.4f})")


def run_validate(args) -> None:
    db = open_db(Path(args.db))
    npz_dir = Path(args.npz_dir)
    basket = [s.strip() for s in (args.syms or ",".join(BASKETS.get(args.basket, []))).split(",") if s.strip()]
    sym_data: Dict[str, Tuple[np.ndarray, MaskCache, float]] = {}
    sample: Optional[Dict[str, np.ndarray]] = None
    for sym in basket:
        try:
            npz = _load_npz(sym, npz_dir); close = npz["close_3m"]
            sym_data[sym] = (close, MaskCache(npz, limit=600), _years_of(close))
            if sample is None: sample = npz
        except FileNotFoundError: pass
    if not sym_data: print("[validate] no data — abort"); return
    # Pooled basket → only symbol-relative primitives. Filter applies to BOTH
    # the random sampler AND the seed configs: per-symbol seeds carry
    # absolute-price primitives that produce filter-by-coincidence Sharpes
    # (e.g. kc_lower_4h≤486 fires only on cheap-coin syms by accident).
    fams = _families_for(args, sample, symbol_relative_only=True)
    allowed = {f.field for f in fams}
    seeds = top_seeds(db, args.seed_mode, args.top_n, allowed_fields=allowed)
    print(f"[validate] {len(seeds)} seeds (after sym-relative filter) from mode={args.seed_mode} target={args.target_pool_sharpe:+.2f}")
    if not seeds: print("[validate] no seeds — run a base sweep first."); return
    rng = random.Random(args.seed)
    n_syms = len(sym_data); avg_years = sum(y for _, _, y in sym_data.values()) / n_syms
    out_mode = f"validate:{args.seed_mode}"
    t0 = time.time(); n_done = 0; best = -9.0
    while n_done < args.max_configs:
        seed = seeds[rng.randrange(len(seeds))]
        cfg = neighbor_config(seed, fams, rng)
        cid = upsert_config(db, cfg)
        if already_have(db, cid, args.basket, out_mode):
            n_done += 1; continue
        all_rets: List[float] = []; per_sym: Dict[str, List[float]] = {}; tl = ts = 0
        for sym, (close, cache, years) in sym_data.items():
            m = evaluate_config(cache, close, cfg, years)
            blob = m.get("rets_blob")
            if blob:
                rets = np.frombuffer(zlib.decompress(blob), dtype=np.float16).astype(np.float32)
                if rets.size:
                    all_rets.extend(rets.tolist()); per_sym[sym] = rets.tolist()
            tl += m["trades_long"]; ts += m["trades_short"]
        if not all_rets: n_done += 1; continue
        rets_arr = np.asarray(all_rets, dtype=np.float32)
        pool = mg.pool_sharpe(all_rets); ssh = mg.sym_sharpe_from_groups(per_sym)
        total_gain = float(rets_arr.sum() * 100.0)
        metrics = {
            "pool_sharpe": pool, "sym_sharpe": ssh, "trades": len(all_rets),
            "trades_long": tl, "trades_short": ts, "acc_gain_pct": total_gain,
            "avg_gain_trade": total_gain / len(all_rets),
            "gain_per_yr": total_gain / max(0.01, avg_years),
            "max_dd_pct": _max_drawdown_pct(rets_arr),
            "win_rate_pct": float((rets_arr > 0).mean() * 100.0),
            "rets_blob": zlib.compress(rets_arr.astype(np.float16).tobytes(), 6),
        }
        # Trade-frequency gates same as pooled.
        tps = len(all_rets) / max(1, n_syms) / max(0.01, avg_years)
        max_tps = getattr(args, "max_tps_per_yr", 0) or 0
        min_tps = getattr(args, "min_tps_per_yr", 0) or 0
        if (max_tps > 0 and tps > max_tps) or (min_tps > 0 and tps < min_tps):
            n_done += 1
            continue
        insert_result(db, cid, args.basket, n_syms=n_syms, years=avg_years, mode=out_mode, metrics=metrics)
        n_done += 1
        sample_ok = len(all_rets) >= 30 * max(1, n_syms) and (abs(pool) <= 5.0 or len(all_rets) >= 5000)
        if pool > best and sample_ok:
            best = pool
            print(f"  [validate] new best pool_sharpe={pool:+.4f} trades={len(all_rets)} hash={cfg.hash}")
        floor_syms = mg.MIN_SYMS_STOCKS if "tradier" in out_mode else mg.MIN_SYMS_CRYPTO
        if pool >= args.target_pool_sharpe and len(all_rets) >= 30 * n_syms and n_syms >= floor_syms:
            print(f"  [validate] TARGET HIT pool={pool:+.4f} n_syms={n_syms} years={avg_years:.2f} hash={cfg.hash}"); break
        if n_done % 100 == 0:
            print(f"  [validate] {n_done:,}/{args.max_configs:,} best={best:+.4f}")


def run_v3(args) -> None:
    """V3 fast-scalp: restrict families to 3m-suffixed fields."""
    syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    db = open_db(Path(args.db))
    rng = random.Random(args.seed)
    npz_dir = Path(args.npz_dir)
    mode = "v3"
    sample = _load_npz(syms[0], npz_dir)
    all_fams = auto_families(sample,
                             enabled_globs=("*_3m", "*_3m_*", "k_*_3m", "rsi_3m", "bb_pct_b_3m",
                                            "dc_*_3m", "wt*_3m", "close_above_*_3m", "close_below_*_3m"),
                             disabled_globs=())
    if not all_fams:
        all_fams = [f for f in auto_families(sample) if "_3m" in f.field]
    write_family_catalog(db, all_fams)
    print(f"[v3] {len(all_fams)} 3m-only families across {len(syms)} syms")
    for sym in syms:
        try: npz = _load_npz(sym, npz_dir)
        except FileNotFoundError as e: print(f"  skip {sym}: {e}"); continue
        close = npz["close_3m"]; years = _years_of(close)
        cache = MaskCache(npz)
        t0 = time.time(); n_done = 0; best = -9.0
        while n_done < args.max_configs:
            cfg = random_config(rng, all_fams, side="both")
            cid = upsert_config(db, cfg)
            if already_have(db, cid, sym, mode):
                n_done += 1; continue
            m = evaluate_config(cache, close, cfg, years)
            insert_result(db, cid, sym, n_syms=1, years=years, mode=mode, metrics=m)
            n_done += 1
            if m["pool_sharpe"] > best and m["trades"] >= 30:
                best = m["pool_sharpe"]
                print(f"  v3 {sym} best pool_sharpe={best:+.4f} trades={m['trades']} dd={m['max_dd_pct']:.1f}%")
            if n_done % 500 == 0:
                print(f"  v3 {sym}: {n_done:,}/{args.max_configs:,} "
                      f"({n_done/(time.time()-t0):.0f}/s best={best:+.4f})")


def run_top(args) -> None:
    db = open_db(Path(args.db))
    where = "WHERE r.mode=?" if args.mode else ""
    params: Tuple = (args.mode,) if args.mode else ()
    rows = db.execute(f"""
        SELECT r.symbol, r.mode, r.pool_sharpe, r.sym_sharpe, r.trades, r.acc_gain_pct,
               r.max_dd_pct, r.n_syms, r.years, r.verdict, c.label
        FROM results r JOIN configs c ON c.id=r.config_id {where}
        ORDER BY r.pool_sharpe DESC LIMIT ?""",
        (*params, args.top_n)).fetchall()
    print(f"# Top {len(rows)} ({args.mode or 'ALL'})  -- per-trade pool_sharpe; DIAGNOSTIC if below floor")
    for i, r in enumerate(rows, 1):
        print(f"{i:5d} | {r[0]:>10s} | {r[1]:<22s} | pool={r[2]:+.4f} | sym={r[3]:+.4f} | "
              f"t={r[4]:6d} | gain={r[5]:+8.1f}% | dd={r[6]:5.1f}% | "
              f"{r[7]:3d}sym | {r[8]:.2f}y | {r[9]:<11s} | {r[10][:80]}")


def run_catalog(args) -> None:
    npz_dir = Path(args.npz_dir)
    sample = _load_npz(args.sample_sym, npz_dir)
    fams = auto_families(sample,
                         enabled_globs=tuple((args.enable_families or "*").split(",")),
                         disabled_globs=tuple(g for g in (args.disable_families or "").split(",") if g))
    print(f"# Auto family catalog from {args.sample_sym} — {len(fams)} families")
    by_kind: Dict[str, int] = {}
    n_prims = 0
    for f in fams:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        n_prims += len(f.thresholds) * len(f.ops)
    print(f"# kind counts: {by_kind}")
    print(f"# total materializable primitives: {n_prims:,}")
    if args.show:
        for f in fams[: args.show]:
            print(f"  {f.field:<32s} kind={f.kind:<10s} sides={'+'.join(f.sides):<24s} "
                  f"ops={list(f.ops)} thresholds={[round(x,3) for x in f.thresholds[:8]]}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def _on_exit(signum, frame):
    if HOLD_SENTINEL.exists() and HOLD_SENTINEL.read_text().startswith("vec_sweep:"):
        try: HOLD_SENTINEL.unlink()
        except Exception: pass
    sys.exit(0)


def _touch_hold(reason: str) -> None:
    try: HOLD_SENTINEL.write_text(f"vec_sweep:{reason}:{time.strftime('%FT%T')}\n")
    except Exception: pass


def _add_common(p):
    p.add_argument("--npz-dir", default=str(NPZ_DIR_DEFAULT))
    p.add_argument("--db", default=str(DB_DEFAULT))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--enable-families", default="*",
                   help="comma globs of family field-names to ENABLE (default: all)")
    p.add_argument("--disable-families", default="",
                   help="comma globs of family field-names to DISABLE")
    p.add_argument("--tf-filter", default="any", choices=("any", "htf_only", "no_3m"),
                   help="restrict primitive timeframes: any (default), htf_only (4h/D/W/M), no_3m")
    p.add_argument("--max-tps-per-yr", type=float, default=0,
                   help="reject configs with > N trades/sym/yr (anti-overtrading; 0=disabled)")
    p.add_argument("--min-tps-per-yr", type=float, default=0,
                   help="reject configs with < N trades/sym/yr (sample floor; 0=disabled)")


def main():
    p = argparse.ArgumentParser(description="vec_sweep — vectorized audited backtest sweep")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("per_symbol"); _add_common(a)
    a.add_argument("--syms", default="BTCUSDT,ETHUSDT,SOLUSDT,BTCDOMUSDT,DOGEUSDT,ZECUSDT")
    a.add_argument("--max-configs", type=int, default=200_000)
    a.set_defaults(fn=run_per_symbol)

    b = sub.add_parser("pooled"); _add_common(b)
    b.add_argument("--basket", default="crypto48")
    b.add_argument("--syms", default="")
    b.add_argument("--max-configs", type=int, default=50_000)
    b.set_defaults(fn=run_pooled)

    c = sub.add_parser("validate"); _add_common(c)
    c.add_argument("--seed-mode", default="pooled:crypto48")
    c.add_argument("--top-n", type=int, default=1000)
    c.add_argument("--basket", default="crypto48")
    c.add_argument("--syms", default="")
    c.add_argument("--target-pool-sharpe", type=float, default=4.0)
    c.add_argument("--max-configs", type=int, default=200_000)
    c.set_defaults(fn=run_validate)

    d = sub.add_parser("v3"); _add_common(d)
    d.add_argument("--syms", default="BTCUSDC,ETHUSDC,SOLUSDC")
    d.add_argument("--max-configs", type=int, default=100_000)
    d.set_defaults(fn=run_v3)

    e = sub.add_parser("top")
    e.add_argument("--mode", default="")
    e.add_argument("--top-n", type=int, default=1000)
    e.add_argument("--db", default=str(DB_DEFAULT))
    e.set_defaults(fn=run_top)

    f = sub.add_parser("catalog"); _add_common(f)
    f.add_argument("--sample-sym", default="BTCUSDT")
    f.add_argument("--show", type=int, default=0,
                   help="print first N families with their thresholds")
    f.set_defaults(fn=run_catalog)

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
