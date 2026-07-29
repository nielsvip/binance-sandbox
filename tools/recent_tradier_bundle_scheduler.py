#!/usr/bin/env python3
"""Canonical-matrix companion: resumable 90/10 coherent-bundle vector screen.

This does *not* restart the discarded band-ladder grind and does not write live
configuration.  It uses the actual recent Tradier symbol/side cohort, screens
whole entry/exit/reentry bundles on frozen chronological folds, and appends
research evidence to the existing path-fleet ledger.  Exact replay is never
started here: only a bundle passing both discovery folds and the untouched
validation fold is put in ``EXACT_PENDING``.

The deterministic claim sequence is nine productive/promising handles followed
by one exploration handle whenever both lanes contain runnable work.  A handle
with three consecutive non-positive/inert attempts is demoted to exploration;
it can therefore consume at most the one-in-ten exploration share.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_ROOT = ROOT / "data/reports/recent_tradier_90_10"
DEFAULT_NPZ = ROOT / "backtest_v8/indicators"
DEFAULT_FLEET = ROOT / "data/reports/path_fleet/queue.db"
PYTHON = Path(
    os.environ.get(
        "BINANCE_PYTHON",
        "/home/niels/.conda/envs/binance_env/bin/python",
    )
)
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

TERMINAL = {
    "VECTOR_REJECTED",
    "VALIDATION_REJECTED",
    "EXACT_PENDING",
    "EXACT_PASS",
    "EXACT_FAIL",
    "DATA_QUARANTINED",
    "BUDGET_EXHAUSTED",
}


@dataclass(frozen=True)
class Bundle:
    bundle_id: str
    description: str
    overrides: dict[str, Any]
    component_paths: tuple[str, ...]
    prior_promising: bool = True
    sides: tuple[str, ...] = ("LONG", "SHORT")


BUNDLES: tuple[Bundle, ...] = (
    Bundle(
        "WTDC_RECLAIM_PEAK_FAST",
        "WT/DC entry with completed-1h confirmation, persistent exit-price "
        "reclaim, and a fast profitable-peak giveback exit.",
        {
            "WT_DC_ENTRY_THRESHOLD": 35,
            "WT_DC_HTF_GATE": "1h",
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": True,
            "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": True,
            "PEAK_GIVEBACK_DROP_PCT": 0.5,
            "PEAK_GIVEBACK_MIN_PEAK_PCT": 0.5,
            "MTF_ATR_TRAIL_ENABLED": False,
        },
        ("ENTRY_WT_DC", "EXIT_PEAK_GIVEBACK"),
    ),
    Bundle(
        "WTDC_RECLAIM_MTFATR",
        "Stricter WT/DC entry plus persistent reclaim and a completed-1h "
        "ATR trail; all settings change together as one strategy.",
        {
            "WT_DC_ENTRY_THRESHOLD": 50,
            "WT_DC_HTF_GATE": "4h",
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": True,
            "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": False,
            "MTF_ATR_TRAIL_ENABLED": True,
            "MTF_ATR_TRAIL_MULT": 2.5,
            "MTF_ATR_TRAIL_TF": "1h",
        },
        ("ENTRY_WT_DC", "EXIT_MTF_ATR_TRAIL"),
    ),
    Bundle(
        "GR_WTDC_RECLAIM_PEAK",
        "Golden-Rule confirmation filters WT/DC entries; a persistent reclaim "
        "obligation follows the profitable-peak giveback exit.",
        {
            "WT_DC_ENTRY_THRESHOLD": 35,
            "WT_DC_HTF_GATE": "1h",
            "GOLDEN_RULE_ENABLED": True,
            "GOLDEN_RULE_HTF_MIN_TFS": 2,
            "GOLDEN_RULE_MIN_IND": 2,
            "GR_FILTER_ALL_ENTRIES": True,
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": True,
            "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": True,
            "PEAK_GIVEBACK_DROP_PCT": 0.75,
            "PEAK_GIVEBACK_MIN_PEAK_PCT": 0.75,
        },
        ("ENTRY_GOLDEN_RULE", "ENTRY_WT_DC", "EXIT_PEAK_GIVEBACK"),
    ),
    Bundle(
        "GR_DIRECT_RECLAIM_MTFATR",
        "Golden-Rule direct entries with a higher WT/DC floor, persistent "
        "reclaim, and a patient completed-1h ATR exit.",
        {
            "WT_DC_ENTRY_THRESHOLD": 65,
            "WT_DC_HTF_GATE": "4h_D",
            "GOLDEN_RULE_ENABLED": True,
            "GOLDEN_RULE_HTF_MIN_TFS": 2,
            "GOLDEN_RULE_MIN_IND": 3,
            "GR_FILTER_ALL_ENTRIES": False,
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": True,
            "MTF_ATR_TRAIL_ENABLED": True,
            "MTF_ATR_TRAIL_MULT": 3.0,
            "MTF_ATR_TRAIL_TF": "1h",
        },
        ("ENTRY_GOLDEN_RULE", "ENTRY_WT_DC", "EXIT_MTF_ATR_TRAIL"),
    ),
    Bundle(
        "SHORT_CORRECTION_ELEVATOR",
        "SHORT-only fast-correction book: permissive WT/DC trigger, no HTF "
        "trend veto, fast peak giveback, and mandatory post-cover reclaim.",
        {
            "WT_DC_ENTRY_THRESHOLD": 24,
            "WT_DC_HTF_GATE": "none",
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": True,
            "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": True,
            "PEAK_GIVEBACK_DROP_PCT": 0.35,
            "PEAK_GIVEBACK_MIN_PEAK_PCT": 0.4,
            "MTF_ATR_TRAIL_ENABLED": True,
            "MTF_ATR_TRAIL_MULT": 1.5,
            "MTF_ATR_TRAIL_TF": "15m",
        },
        ("ENTRY_WT_DC", "EXIT_PEAK_GIVEBACK", "EXIT_MTF_ATR_TRAIL"),
        sides=("SHORT",),
    ),
    Bundle(
        "SHORT_BEAR_CONTINUATION",
        "SHORT-only patient bear book: completed 4h/D WT confirmation, wider "
        "ATR exit, and persistent reclaim rather than an inverted LONG recipe.",
        {
            "WT_DC_ENTRY_THRESHOLD": 50,
            "WT_DC_HTF_GATE": "4h_D",
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": True,
            "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": True,
            "PEAK_GIVEBACK_DROP_PCT": 1.0,
            "PEAK_GIVEBACK_MIN_PEAK_PCT": 1.0,
            "MTF_ATR_TRAIL_ENABLED": True,
            "MTF_ATR_TRAIL_MULT": 3.0,
            "MTF_ATR_TRAIL_TF": "1h",
        },
        ("ENTRY_WT_DC", "EXIT_PEAK_GIVEBACK", "EXIT_MTF_ATR_TRAIL"),
        sides=("SHORT",),
    ),
    Bundle(
        "EXPLORE_CHANNEL_REENTRY",
        "Exploration retry: channel re-entry exit combined with WT/DC and "
        "persistent reclaim. Kept to a deterministic one-in-ten budget.",
        {
            "WT_DC_ENTRY_THRESHOLD": 35,
            "WT_DC_HTF_GATE": "1h",
            "CHANNEL_REENTRY_STOP_ENABLED": True,
            "CHANNEL_REENTRY_STOP_TF": "1h",
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": True,
            "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": False,
            "MTF_ATR_TRAIL_ENABLED": False,
        },
        ("ENTRY_WT_DC", "EXIT_E02_DONCHIAN"),
        prior_promising=False,
    ),
    Bundle(
        "EXPLORE_BBLOCK_REENTRY",
        "Exploration retry: prior-exit-gated B-block reentries with the direct "
        "exit-price reclaim disabled. This cannot consume the productive lane.",
        {
            "WT_DC_ENTRY_THRESHOLD": 35,
            "WT_DC_HTF_GATE": "1h",
            "VEC_REENTRY_REQUIRE_PRIOR_EXIT": True,
            "VEC_REENTRY_DC4_EXITPRICE_ENABLED": False,
            "REENTRY_B15_STRONG_TREND_ENABLED": True,
            "REENTRY_B04_DC_RETEST_ENABLED": True,
            "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED": True,
            "PEAK_GIVEBACK_DROP_PCT": 0.5,
            "PEAK_GIVEBACK_MIN_PEAK_PCT": 0.5,
        },
        ("ENTRY_REENTRY_TREND_ENABLED", "ENTRY_REENTRY_PULLBACK_ENABLED", "EXIT_PEAK_GIVEBACK"),
        prior_promising=False,
    ),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    temp.replace(path)


def connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "queue.db", timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta(
          key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS handles(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          campaign_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          side TEXT NOT NULL,
          cohort_bucket TEXT NOT NULL,
          bundle_id TEXT NOT NULL,
          prior_promising INTEGER NOT NULL,
          historical_positive INTEGER NOT NULL DEFAULT 0,
          historical_negative INTEGER NOT NULL DEFAULT 0,
          historical_inert INTEGER NOT NULL DEFAULT 0,
          fail_streak INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'PENDING',
          time_budget_s INTEGER NOT NULL,
          lease_owner TEXT,
          lease_at REAL,
          receipt_path TEXT,
          updated_at TEXT NOT NULL,
          UNIQUE(campaign_id,symbol,side,bundle_id));
        CREATE TABLE IF NOT EXISTS attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          handle_id INTEGER NOT NULL,
          attempt_number INTEGER NOT NULL,
          lane TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          elapsed_s REAL,
          receipt_path TEXT,
          receipt_sha256 TEXT,
          payload_json TEXT NOT NULL,
          fleet_ingested INTEGER NOT NULL DEFAULT 0,
          UNIQUE(handle_id,attempt_number));
        CREATE TABLE IF NOT EXISTS controls(
          campaign_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          side TEXT NOT NULL,
          fold TEXT NOT NULL,
          npz_sha256 TEXT NOT NULL,
          metrics_json TEXT NOT NULL,
          receipt_path TEXT NOT NULL,
          PRIMARY KEY(campaign_id,symbol,side,fold,npz_sha256));
        """
    )
    con.commit()
    return con


def meta_get(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def meta_set(con: sqlite3.Connection, key: str, value: Any) -> None:
    con.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        (key, str(value)),
    )


def fleet_history(
    fleet_db: Path,
    symbol: str,
    side: str,
    components: Iterable[str],
) -> tuple[int, int, int]:
    if not fleet_db.exists() or fleet_db.stat().st_size == 0:
        return 0, 0, 0
    try:
        con = sqlite3.connect(f"file:{fleet_db}?mode=ro", uri=True, timeout=20)
        placeholders = ",".join("?" for _ in components)
        rows = con.execute(
            f"""SELECT r.status,r.strategy_return_pct,r.bh_return_pct,
                       r.same_entry_control_return_pct,r.trades
                FROM results r JOIN jobs j ON j.id=r.job_id
                WHERE r.symbol=? AND r.side=? AND j.path_id IN ({placeholders})""",
            (symbol, side, *components),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return 0, 0, 0
    positive = negative = inert = 0
    for status, strategy, bh, control, trades in rows:
        if int(trades or 0) <= 0 or "INERT" in str(status).upper():
            inert += 1
            continue
        strategy = float(strategy or 0)
        benchmark = max(0.0, float(bh or 0))
        control_value = float(control) if control is not None else -1e300
        if strategy > benchmark and strategy > control_value:
            positive += 1
        else:
            negative += 1
    return positive, negative, inert


def validate_catalog() -> None:
    from v8_vec_sweep import SweepConfig
    from vec_paths import vec_refuses_knob

    cfg = SweepConfig()
    errors = []
    for bundle in BUNDLES:
        for name in bundle.overrides:
            if not hasattr(cfg, name):
                errors.append(f"{bundle.bundle_id}: unknown {name}")
            elif vec_refuses_knob(name):
                errors.append(f"{bundle.bundle_id}: vec-refused {name}")
    if errors:
        raise RuntimeError("; ".join(errors))


def init_campaign(
    *,
    root: Path,
    cohort_path: Path,
    fleet_db: Path,
    pilot_exceptions: Iterable[str],
    priority_budget_s: int,
    explore_budget_s: int,
) -> dict[str, Any]:
    validate_catalog()
    cohort = json.loads(cohort_path.read_text())
    campaign_seed = {
        "scheduler_sha256": sha256(Path(__file__).resolve()),
        "cohort_sha256": sha256(cohort_path),
        "window_start": cohort["window_start"],
        "window_end_exclusive": cohort["window_end_exclusive"],
        "bundles": [dataclasses.asdict(row) for row in BUNDLES],
        "pilot_exceptions": sorted(pilot_exceptions),
        "metric_contract": (
            "single-fold net PnL / pre-cost committed fill-notional "
            "integrated over all fold bars; never marked notional"
        ),
    }
    campaign_id = "recent30_bundle_" + hashlib.sha256(
        json.dumps(campaign_seed, sort_keys=True).encode()
    ).hexdigest()[:12]
    keys = [
        (row["symbol"].upper(), row["side"].upper(), "BROKER_RECENT_30D")
        for row in cohort["cohort"]
    ]
    for raw in pilot_exceptions:
        symbol, side = raw.upper().rsplit("_", 1)
        if symbol == "ACH":
            raise ValueError("ACH is unresolved/absent; never invent it")
        if symbol == "HAO":
            raise ValueError("HAO is data-quarantined")
        keys.append((symbol, side, "USER_PILOT_EXCEPTION_NOT_RECENT"))
    keys = sorted(set(keys))
    con = connect(root)
    meta_set(con, "campaign_id", campaign_id)
    meta_set(con, "campaign_contract_json", json.dumps(campaign_seed, sort_keys=True))
    meta_set(con, "claim_sequence", meta_get(con, "claim_sequence", "0"))
    meta_set(con, "cohort_path", str(cohort_path.resolve()))
    meta_set(con, "cohort_sha256", sha256(cohort_path))
    for symbol, side, bucket in keys:
        for bundle in BUNDLES:
            if side not in bundle.sides:
                continue
            positive, negative, inert = fleet_history(
                fleet_db, symbol, side, bundle.component_paths
            )
            con.execute(
                """INSERT OR IGNORE INTO handles
                   (campaign_id,symbol,side,cohort_bucket,bundle_id,
                    prior_promising,historical_positive,historical_negative,
                    historical_inert,time_budget_s,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    campaign_id,
                    symbol,
                    side,
                    bucket,
                    bundle.bundle_id,
                    int(bundle.prior_promising),
                    positive,
                    negative,
                    inert,
                    priority_budget_s if bundle.prior_promising else explore_budget_s,
                    now_iso(),
                ),
            )
    con.commit()
    result = status_payload(con)
    con.close()
    write_progress(root, result)
    return result


def effective_lane(row: sqlite3.Row) -> str:
    if int(row["fail_streak"]) >= 3:
        return "EXPLORE"
    if int(row["historical_positive"]) > 0:
        return "PRIORITY"
    if (
        int(row["historical_negative"]) + int(row["historical_inert"]) >= 3
        and int(row["historical_positive"]) == 0
    ):
        return "EXPLORE"
    return "PRIORITY" if int(row["prior_promising"]) else "EXPLORE"


def deterministic_rank(campaign: str, row: sqlite3.Row) -> str:
    return hashlib.sha256(
        (
            f"{campaign}|{row['symbol']}|{row['side']}|"
            f"{row['bundle_id']}|{row['attempts']}"
        ).encode()
    ).hexdigest()


def claim(
    con: sqlite3.Connection,
    owner: str,
    *,
    only_keys: set[str] | None = None,
    lease_s: int = 1800,
) -> tuple[sqlite3.Row, str] | None:
    now = time.time()
    con.execute("BEGIN IMMEDIATE")
    con.execute(
        """UPDATE handles SET status='PENDING',lease_owner=NULL,lease_at=NULL
           WHERE status='RUNNING' AND lease_at<?""",
        (now - lease_s,),
    )
    campaign = meta_get(con, "campaign_id")
    rows = con.execute(
        """SELECT * FROM handles
           WHERE campaign_id=? AND
                 (status='PENDING' OR
                  (status IN ('VECTOR_REJECTED','BUDGET_EXHAUSTED')
                   AND attempts<3))""",
        (campaign,),
    ).fetchall()
    if only_keys:
        rows = [
            row
            for row in rows
            if f"{row['symbol']}_{row['side']}" in only_keys
        ]
    if not rows:
        con.commit()
        return None
    sequence = int(meta_get(con, "claim_sequence", "0"))
    requested_lane = "EXPLORE" if sequence % 10 == 9 else "PRIORITY"
    pools = {
        lane: [row for row in rows if effective_lane(row) == lane]
        for lane in ("PRIORITY", "EXPLORE")
    }
    lane = requested_lane if pools[requested_lane] else (
        "EXPLORE" if requested_lane == "PRIORITY" else "PRIORITY"
    )
    if not pools[lane]:
        con.commit()
        return None
    row = min(pools[lane], key=lambda item: deterministic_rank(campaign, item))
    con.execute(
        """UPDATE handles SET status='RUNNING',lease_owner=?,lease_at=?,
                              attempts=attempts+1,updated_at=?
           WHERE id=?""",
        (owner, now, now_iso(), row["id"]),
    )
    attempt_number = int(row["attempts"]) + 1
    con.execute(
        """INSERT INTO attempts
           (handle_id,attempt_number,lane,status,started_at,payload_json)
           VALUES(?,?,?,?,?,?)""",
        (
            row["id"],
            attempt_number,
            lane,
            "RUNNING",
            now_iso(),
            json.dumps({"claim_sequence": sequence}, sort_keys=True),
        ),
    )
    meta_set(con, "claim_sequence", sequence + 1)
    con.commit()
    return con.execute("SELECT * FROM handles WHERE id=?", (row["id"],)).fetchone(), lane


def fold_manifest(npz_path: Path, start: str = "2024-01-01") -> dict[str, Any]:
    with np.load(npz_path, allow_pickle=True) as z:
        ts = np.asarray(z["timestamps"], dtype=np.int64)
        close = np.asarray(z["close"], dtype=np.float64)
    start_epoch = int(
        datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()
    )
    i0 = int(np.searchsorted(ts, start_epoch, side="left"))
    ts = ts[i0:]
    close = close[i0:]
    if len(ts) < 900:
        raise ValueError(f"insufficient rows after {start}: {len(ts)}")
    cuts = [0, int(len(ts) * 0.35), int(len(ts) * 0.70), len(ts)]
    folds = []
    for name, begin, finish in zip(
        ("DISCOVERY_1", "DISCOVERY_2", "VALIDATION"),
        cuts[:-1],
        cuts[1:],
    ):
        folds.append(
            {
                "name": name,
                "start_ts": int(ts[begin]),
                "end_ts_inclusive": int(ts[finish - 1]),
                "bars": finish - begin,
                "start_close": float(close[begin]),
                "end_close": float(close[finish - 1]),
            }
        )
    payload = {
        "npz_path": str(npz_path),
        "npz_sha256": sha256(npz_path),
        "source_start": start,
        "rows": len(ts),
        "folds": folds,
        "selection_contract": (
            "both discovery folds rank/fail first; validation is invoked only "
            "after the bundle is frozen"
        ),
    }
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    return payload


def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _artifact_paths(output: str) -> tuple[Path, Path]:
    summary = re.search(r"summary=(\S+)", output)
    trades = re.search(r"trades\s*=(\S+)", output)
    if not summary or not trades:
        raise RuntimeError("vec runner did not print artifact paths")
    return Path(summary.group(1)), Path(trades.group(1))


def _binary_tim(events: list[dict[str, Any]], timestamps: np.ndarray) -> float:
    by_ts: dict[int, list[str]] = {}
    for row in events:
        try:
            event_ts = int(datetime.fromisoformat(row["ts"]).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        by_ts.setdefault(event_ts, []).append(str(row.get("type", "")).upper())
    active = False
    active_rows = 0
    for value in timestamps:
        for event_type in by_ts.get(int(value), []):
            if event_type in {"OPEN", "AUGMENT", "REENTRY"}:
                active = True
            elif event_type in {"CLOSE", "MTM_FINAL"}:
                active = False
        active_rows += int(active)
    return 100.0 * active_rows / max(1, len(timestamps))


def _committed_fill_ledger(
    events: list[dict[str, Any]],
    timestamps: np.ndarray,
    closes: np.ndarray,
    *,
    side: str,
    round_trip_cost_pct: float,
) -> dict[str, Any]:
    """Return an honest fold ledger on pre-cost committed fill dollar-time.

    The denominator is requested/filled entry notional carried while the
    position is open. It is never revalued with the mark, which would
    mechanically favor winning SHORTs and penalize winning LONGs.
    """
    by_ts: dict[int, list[dict[str, Any]]] = {}
    for row in events:
        try:
            event_ts = int(datetime.fromisoformat(str(row["ts"])).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        by_ts.setdefault(event_ts, []).append(row)
    side_sign = 1.0 if side == "LONG" else -1.0
    qty = entry_cost = realized = 0.0
    committed_sum = 0.0
    peak_committed = 0.0
    close_count = hedge_events = 0
    errors: list[str] = []
    pnl_curve: list[float] = []
    for ts_value, mark_value in zip(timestamps, closes):
        mark = float(mark_value)
        for row in by_ts.get(int(ts_value), []):
            event_type = str(row.get("type") or "").upper()
            try:
                event_qty = abs(float(row.get("qty") or 0.0))
                event_price = float(row.get("price") or mark)
            except (TypeError, ValueError):
                errors.append(f"bad_event_numeric:{event_type}")
                continue
            if event_type.startswith("HEDGE"):
                hedge_events += 1
                continue
            if event_type in {"OPEN", "AUGMENT", "REENTRY"}:
                if event_qty <= 0 or event_price <= 0:
                    errors.append(f"bad_entry:{event_type}")
                    continue
                if event_type in {"OPEN", "REENTRY"} and qty > 1e-9:
                    errors.append("open_while_position_active")
                qty += event_qty
                entry_cost += event_qty * event_price
            elif event_type in {"REDUCE", "CLOSE", "MTM_FINAL"}:
                if qty <= 1e-9:
                    errors.append(f"{event_type.lower()}_while_flat")
                    continue
                close_qty = min(event_qty, qty)
                if close_qty <= 0:
                    errors.append(f"bad_close:{event_type}")
                    continue
                average_entry = entry_cost / qty
                gross = side_sign * (event_price - average_entry) * close_qty
                cost = (
                    round_trip_cost_pct / 100.0 * average_entry * close_qty
                )
                realized += gross - cost
                qty -= close_qty
                entry_cost -= average_entry * close_qty
                close_count += 1
                if qty <= 1e-9:
                    qty = entry_cost = 0.0
        committed = entry_cost
        committed_sum += committed
        peak_committed = max(peak_committed, committed)
        unrealized = (
            side_sign * (mark - entry_cost / qty) * qty if qty > 1e-9 else 0.0
        )
        pnl_curve.append(realized + unrealized)
    if qty > 1e-8:
        errors.append("position_open_after_final_mtm")
    average_committed = committed_sum / max(1, len(timestamps))
    return_on_deployed = (
        100.0 * realized / average_committed
        if average_committed > 0
        else 0.0
    )
    peak_pnl = 0.0
    max_drawdown_usd = 0.0
    for pnl in pnl_curve:
        peak_pnl = max(peak_pnl, pnl)
        max_drawdown_usd = max(max_drawdown_usd, peak_pnl - pnl)
    return {
        "strategy_return_pct": return_on_deployed,
        "realized_net_pnl_usd": realized,
        "average_committed_fill_notional_usd": average_committed,
        "peak_committed_fill_notional_usd": peak_committed,
        "max_dd_pct": (
            100.0 * max_drawdown_usd / average_committed
            if average_committed > 0
            else 0.0
        ),
        "trades": close_count,
        "hedge_event_count": hedge_events,
        "ledger_errors": errors,
        "metric_scope": "FROZEN_FOLD_RETURN_ON_AVG_COMMITTED_FILL_NOTIONAL",
        "return_unit": "PCT_OF_PRE_COST_COMMITTED_FILL_DOLLAR_TIME",
        "return_aggregation": "SINGLE_CHRONOLOGICAL_FOLD",
        "tim_unit": "BINARY_POSITION_BAR_PCT",
        "tim_aggregation": "SINGLE_CHRONOLOGICAL_FOLD",
    }


def run_vec_fold(
    *,
    symbol: str,
    side: str,
    fold: dict[str, Any],
    npz_path: Path,
    overrides: dict[str, Any],
    timeout_s: int,
    log_path: Path,
) -> dict[str, Any]:
    cmd = [
        "nice",
        "-n",
        "19",
        str(PYTHON),
        str(ROOT / "v8_vec_sweep.py"),
        "--mode",
        "tradier",
        "--account",
        "trb_bundle_research",
        "--symbols",
        symbol,
        "--sides",
        side,
        "--start",
        datetime.fromtimestamp(
            int(fold["start_ts"]), tz=timezone.utc
        ).isoformat(),
        "--max-bars",
        str(int(fold["bars"])),
        "--workers",
        "1",
        "--no-history",
    ]
    for name, value in sorted(overrides.items()):
        cmd += ["--override", f"{name}={_value_text(value)}"]
    env = dict(os.environ)
    env["V8_VEC_ALLOW_DIAGNOSTIC"] = "1"
    env["V8_VEC_SWEEP_BYPASS_ACCT_FILTER"] = "1"
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=max(1, timeout_s),
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "$ " + " ".join(shlex.quote(part) for part in cmd) + "\n" + output
    )
    if proc.returncode:
        raise RuntimeError(
            f"vec rc={proc.returncode}: {' | '.join(output.splitlines()[-4:])}"
        )
    summary_path, trades_path = _artifact_paths(output)
    summary_rows = [
        json.loads(line)
        for line in summary_path.read_text().splitlines()
        if line.strip()
    ]
    summary = next(
        (
            row
            for row in summary_rows
            if row.get("symbol") == symbol and row.get("side") == side
        ),
        None,
    )
    if summary is None:
        raise RuntimeError("missing per-key vec summary")
    events = [
        json.loads(line)
        for line in trades_path.read_text().splitlines()
        if line.strip()
    ]
    with np.load(npz_path, allow_pickle=True) as z:
        all_ts = np.asarray(z["timestamps"], dtype=np.int64)
        all_close = np.asarray(z["close"], dtype=np.float64)
    begin = int(np.searchsorted(all_ts, int(fold["start_ts"]), side="left"))
    fold_ts = all_ts[begin : begin + int(fold["bars"])]
    fold_close = all_close[begin : begin + int(fold["bars"])]
    try:
        from v8_vec_sweep import _vec_round_trip_cost_for_sym

        round_trip_cost_pct = float(_vec_round_trip_cost_for_sym(symbol))
    except (ImportError, TypeError, ValueError):
        round_trip_cost_pct = 0.05
    ledger = _committed_fill_ledger(
        events,
        fold_ts,
        fold_close,
        side=side,
        round_trip_cost_pct=round_trip_cost_pct,
    )
    bh_long = (
        (float(fold["end_close"]) / float(fold["start_close"])) - 1.0
    ) * 100.0
    bh = (bh_long if side == "LONG" else -bh_long) - round_trip_cost_pct
    metrics = {
        **ledger,
        "bh_return_pct": bh,
        "side_benchmark_pct": max(0.0, bh),
        "pool_sharpe": float(summary["pool_sharpe"]),
        "vec_acc_gain_pct_diagnostic_only": float(summary["acc_gain_pct"]),
        "vec_max_dd_pct_diagnostic_only": float(summary["max_dd_pct"]),
        "tim_pct": _binary_tim(events, fold_ts),
        "bars": int(summary["n_bars"]),
        "elapsed_s": time.time() - started,
        "summary_sha256": sha256(summary_path),
        "trades_sha256": sha256(trades_path),
        "engine": "v8_vec_sweep",
        "tier": "VEC_DIAGNOSTIC",
        "round_trip_cost_pct": round_trip_cost_pct,
    }
    try:
        summary_path.unlink()
        trades_path.unlink()
    except OSError:
        pass
    return metrics


def fold_strict(
    metrics: dict[str, Any],
    control: dict[str, Any],
    *,
    tim_min: float,
    tim_max: float,
) -> tuple[bool, list[str]]:
    failures = []
    if int(metrics["trades"]) < 3:
        failures.append("trades<3")
    if float(metrics["strategy_return_pct"]) <= float(
        metrics["side_benchmark_pct"]
    ):
        failures.append("did_not_beat_side_bh_or_cash")
    if float(metrics["strategy_return_pct"]) <= float(
        control["strategy_return_pct"]
    ):
        failures.append("did_not_beat_same_fold_vec_control")
    if not tim_min <= float(metrics["tim_pct"]) <= tim_max:
        failures.append(f"tim_not_{tim_min:g}_{tim_max:g}")
    if float(metrics["max_dd_pct"]) > 40.0:
        failures.append("drawdown>40")
    if int(metrics.get("hedge_event_count", 0)) > 0:
        failures.append("hedge_events_out_of_scope")
    if metrics.get("ledger_errors"):
        failures.append("committed_fill_ledger_invalid")
    return not failures, failures


def control_for_fold(
    *,
    con: sqlite3.Connection,
    root: Path,
    campaign: str,
    symbol: str,
    side: str,
    fold: dict[str, Any],
    npz_path: Path,
    npz_sha: str,
    timeout_s: int,
) -> dict[str, Any]:
    row = con.execute(
        """SELECT metrics_json FROM controls
           WHERE campaign_id=? AND symbol=? AND side=? AND fold=?
             AND npz_sha256=?""",
        (campaign, symbol, side, fold["name"], npz_sha),
    ).fetchone()
    if row:
        return json.loads(row[0])
    receipt_path = (
        root / "controls" / f"{symbol}_{side}" / f"{fold['name']}.json"
    )
    metrics = run_vec_fold(
        symbol=symbol,
        side=side,
        fold=fold,
        npz_path=npz_path,
        overrides={},
        timeout_s=timeout_s,
        log_path=receipt_path.with_suffix(".log"),
    )
    atomic_json(receipt_path, metrics)
    con.execute(
        """INSERT OR REPLACE INTO controls
           VALUES(?,?,?,?,?,?,?)""",
        (
            campaign,
            symbol,
            side,
            fold["name"],
            npz_sha,
            json.dumps(metrics, sort_keys=True),
            str(receipt_path),
        ),
    )
    con.commit()
    return metrics


def run_handle(
    *,
    con: sqlite3.Connection,
    root: Path,
    handle: sqlite3.Row,
    lane: str,
    npz_dir: Path,
    tim_min: float,
    tim_max: float,
) -> dict[str, Any]:
    bundle = next(row for row in BUNDLES if row.bundle_id == handle["bundle_id"])
    symbol, side = handle["symbol"], handle["side"]
    npz_path = npz_dir / f"{symbol}.npz"
    started = time.time()
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": handle["campaign_id"],
        "handle_id": handle["id"],
        "attempt_number": handle["attempts"],
        "symbol": symbol,
        "side": side,
        "position_key": f"{symbol}_{side}",
        "cohort_bucket": handle["cohort_bucket"],
        "bundle": dataclasses.asdict(bundle),
        "lane": lane,
        "time_budget_s": handle["time_budget_s"],
        "started_at": now_iso(),
        "folds": [],
        "tim_gate_pct": [tim_min, tim_max],
        "live_config_written": False,
        "matrix_cell_written": False,
        "exact_invoked": False,
    }
    if not npz_path.exists():
        receipt.update(
            status="DATA_QUARANTINED",
            failures=["NPZ_MISSING"],
            elapsed_s=time.time() - started,
        )
        return receipt
    try:
        manifest = fold_manifest(npz_path)
    except Exception as exc:
        receipt.update(
            status="DATA_QUARANTINED",
            failures=[f"FOLD_MANIFEST:{type(exc).__name__}:{exc}"],
            elapsed_s=time.time() - started,
        )
        return receipt
    receipt["fold_manifest"] = manifest
    try:
        from tools.backtest_data_contract import audit_npz
        audit = audit_npz(symbol, npz_path, "core", "2024-01-01")
        receipt["data_contract"] = dataclasses.asdict(audit)
        if not audit.valid:
            receipt.update(
                status="DATA_QUARANTINED",
                failures=audit.errors,
                elapsed_s=time.time() - started,
            )
            return receipt
    except Exception as exc:
        receipt.update(
            status="DATA_QUARANTINED",
            failures=[f"DATA_AUDIT:{type(exc).__name__}:{exc}"],
            elapsed_s=time.time() - started,
        )
        return receipt
    campaign = handle["campaign_id"]
    budget = int(handle["time_budget_s"])
    try:
        for fold in manifest["folds"]:
            elapsed = time.time() - started
            remaining = budget - elapsed
            if remaining < 5:
                receipt.update(
                    status="BUDGET_EXHAUSTED",
                    failures=["PER_SYMBOL_TIME_BUDGET"],
                )
                return receipt
            control = control_for_fold(
                con=con,
                root=root,
                campaign=campaign,
                symbol=symbol,
                side=side,
                fold=fold,
                npz_path=npz_path,
                npz_sha=manifest["npz_sha256"],
                timeout_s=int(max(5, remaining / 2)),
            )
            remaining = budget - (time.time() - started)
            metrics = run_vec_fold(
                symbol=symbol,
                side=side,
                fold=fold,
                npz_path=npz_path,
                overrides=bundle.overrides,
                timeout_s=int(max(5, remaining)),
                log_path=(
                    root
                    / "logs"
                    / f"{symbol}_{side}"
                    / bundle.bundle_id
                    / f"a{handle['attempts']}_{fold['name']}.log"
                ),
            )
            strict, failures = fold_strict(
                metrics, control, tim_min=tim_min, tim_max=tim_max
            )
            receipt["folds"].append(
                {
                    "fold": fold,
                    "metrics": metrics,
                    "same_fold_vec_control": control,
                    "alpha_vs_bh_or_cash_pp": (
                        metrics["strategy_return_pct"]
                        - metrics["side_benchmark_pct"]
                    ),
                    "alpha_vs_control_pp": (
                        metrics["strategy_return_pct"]
                        - control["strategy_return_pct"]
                    ),
                    "strict": strict,
                    "failures": failures,
                }
            )
            if fold["name"].startswith("DISCOVERY") and not strict:
                receipt.update(
                    status="VECTOR_REJECTED",
                    failures=[
                        f"{fold['name']}:{failure}" for failure in failures
                    ],
                    early_stop=fold["name"],
                )
                return receipt
            if fold["name"] == "VALIDATION" and not strict:
                receipt.update(
                    status="VALIDATION_REJECTED",
                    failures=[
                        f"VALIDATION:{failure}" for failure in failures
                    ],
                )
                return receipt
        receipt.update(
            status="EXACT_PENDING",
            failures=[],
            exact_queue_contract=(
                "strict vector discovery+validation survivor; exact engine "
                "must independently prove fills/accounting/causality before promotion"
            ),
        )
        return receipt
    except subprocess.TimeoutExpired:
        receipt.update(
            status="BUDGET_EXHAUSTED",
            failures=["VEC_SUBPROCESS_TIMEOUT"],
        )
        return receipt
    except Exception as exc:
        receipt.update(
            status="VECTOR_REJECTED",
            failures=[f"RUNNER:{type(exc).__name__}:{exc}"],
        )
        return receipt
    finally:
        receipt["elapsed_s"] = time.time() - started


def finish_attempt(
    con: sqlite3.Connection,
    root: Path,
    handle: sqlite3.Row,
    receipt: dict[str, Any],
) -> Path:
    receipt["finished_at"] = now_iso()
    receipt_path = (
        root
        / "receipts"
        / f"{handle['symbol']}_{handle['side']}"
        / handle["bundle_id"]
        / f"attempt_{handle['attempts']:02d}.json"
    )
    atomic_json(receipt_path, receipt)
    receipt_sha = sha256(receipt_path)
    status = receipt["status"]
    fail_streak = (
        0
        if status in {"EXACT_PENDING", "EXACT_PASS"}
        else int(handle["fail_streak"]) + 1
    )
    con.execute(
        """UPDATE handles SET status=?,fail_streak=?,lease_owner=NULL,
                              lease_at=NULL,receipt_path=?,updated_at=?
           WHERE id=?""",
        (status, fail_streak, str(receipt_path), now_iso(), handle["id"]),
    )
    con.execute(
        """UPDATE attempts SET status=?,finished_at=?,elapsed_s=?,
                               receipt_path=?,receipt_sha256=?,payload_json=?
           WHERE handle_id=? AND attempt_number=?""",
        (
            status,
            now_iso(),
            float(receipt.get("elapsed_s", 0)),
            str(receipt_path),
            receipt_sha,
            json.dumps(
                {
                    "failures": receipt.get("failures", []),
                    "fold_count": len(receipt.get("folds", [])),
                },
                sort_keys=True,
            ),
            handle["id"],
            handle["attempts"],
        ),
    )
    con.commit()
    return receipt_path


def status_payload(con: sqlite3.Connection) -> dict[str, Any]:
    campaign = meta_get(con, "campaign_id")
    handles = con.execute(
        "SELECT * FROM handles WHERE campaign_id=?", (campaign,)
    ).fetchall()
    states: dict[str, int] = {}
    lanes: dict[str, int] = {"PRIORITY": 0, "EXPLORE": 0}
    buckets: dict[str, int] = {}
    for row in handles:
        states[row["status"]] = states.get(row["status"], 0) + 1
        lane = effective_lane(row)
        lanes[lane] += 1
        buckets[row["cohort_bucket"]] = (
            buckets.get(row["cohort_bucket"], 0) + 1
        )
    attempts = con.execute(
        """SELECT a.lane,a.status,COUNT(*)
           FROM attempts a JOIN handles h ON h.id=a.handle_id
           WHERE h.campaign_id=?
           GROUP BY a.lane,a.status""",
        (campaign,),
    ).fetchall()
    attempt_counts: dict[str, dict[str, int]] = {}
    for lane, status, count in attempts:
        attempt_counts.setdefault(lane, {})[status] = count
    lane_totals = {
        lane: sum(states.values())
        for lane, states in attempt_counts.items()
    }
    total_claims = int(meta_get(con, "claim_sequence", "0"))
    latest_receipts = []
    for row in con.execute(
        """SELECT a.*,h.symbol,h.side,h.bundle_id
           FROM attempts a JOIN handles h ON h.id=a.handle_id
           WHERE h.campaign_id=? AND a.status!='RUNNING'
             AND a.receipt_path IS NOT NULL
           ORDER BY a.id DESC LIMIT 30""",
        (campaign,),
    ):
        try:
            receipt = json.loads(Path(row["receipt_path"]).read_text())
        except (OSError, json.JSONDecodeError):
            receipt = {}
        final_fold = (receipt.get("folds") or [{}])[-1]
        metrics = final_fold.get("metrics") or {}
        latest_receipts.append(
            {
                "position_key": f"{row['symbol']}_{row['side']}",
                "bundle_id": row["bundle_id"],
                "lane": row["lane"],
                "status": row["status"],
                "elapsed_s": row["elapsed_s"],
                "final_fold": (final_fold.get("fold") or {}).get("name"),
                "strategy_return_pct": metrics.get("strategy_return_pct"),
                "side_benchmark_pct": metrics.get("side_benchmark_pct"),
                "alpha_vs_benchmark_pp": (
                    metrics.get("strategy_return_pct", 0)
                    - metrics.get("side_benchmark_pct", 0)
                    if metrics
                    else None
                ),
                "tim_pct": metrics.get("tim_pct"),
                "trades": metrics.get("trades"),
                "receipt_path": row["receipt_path"],
            }
        )
    return {
        "schema_version": 1,
        "campaign_id": campaign,
        "generated_at": now_iso(),
        "handles": len(handles),
        "distinct_keys": len(
            {(row["symbol"], row["side"]) for row in handles}
        ),
        "states": dict(sorted(states.items())),
        "effective_lanes": lanes,
        "cohort_buckets": buckets,
        "attempts": attempt_counts,
        "claim_sequence": total_claims,
        "realized_explore_share": (
            sum(attempt_counts.get("EXPLORE", {}).values()) / total_claims
            if total_claims
            else 0.0
        ),
        "exact_pending": [
            f"{row['symbol']}_{row['side']}:{row['bundle_id']}"
            for row in handles
            if row["status"] == "EXACT_PENDING"
        ],
        "latest_receipts": latest_receipts,
        "contract": {
            "allocation": "9 PRIORITY : 1 EXPLORE per complete block",
            "demotion": "3 consecutive non-positive/inert attempts",
            "tim_gate_pct": [65.0, 80.0],
            "folds": "35% discovery-1 / 35% discovery-2 / 30% untouched validation",
            "exact": "strict vector survivors only",
            "live_writes": False,
        },
    }


def write_progress(root: Path, payload: dict[str, Any]) -> None:
    atomic_json(root / "status.json", payload)
    attempts = payload["attempts"]
    priority_n = sum(attempts.get("PRIORITY", {}).values())
    explore_n = sum(attempts.get("EXPLORE", {}).values())
    lines = [
        f"# Recent Tradier 90/10 bundle progress — {payload['generated_at']}",
        "",
        f"- Campaign: `{payload['campaign_id']}`",
        f"- Universe: {payload['distinct_keys']} symbol-side keys; "
        f"{payload['cohort_buckets']}",
        f"- Handles: {payload['handles']}; states: {payload['states']}",
        f"- Claims: {payload['claim_sequence']} "
        f"(priority {priority_n}, exploration {explore_n}, "
        f"exploration share {payload['realized_explore_share']:.1%})",
        "- Contract: coherent multi-knob bundles, vector-first, frozen "
        "35/35/30 folds, 65–80% TIM, exact only every-fold strict survivors.",
        "- HAO is excluded; TTD_SHORT and ACN_SHORT are labeled pilot "
        "exceptions rather than falsely described as recent trades; ACH is unresolved.",
        "- No live configuration or canonical ENGINE cell is written by this screen.",
        "",
        "## Exact queue",
        "",
    ]
    lines.extend(
        f"- `{row}`" for row in payload["exact_pending"]
    )
    if not payload["exact_pending"]:
        lines.append("- Empty.")
    lines += [
        "",
        "## Latest per-key receipts",
        "",
        "| key | bundle | lane | state | fold | return | benchmark | alpha | TIM | trades |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload.get("latest_receipts") or []:
        def value(name: str, suffix: str = "") -> str:
            raw = row.get(name)
            return "—" if raw is None else f"{float(raw):.3f}{suffix}"

        lines.append(
            f"| {row['position_key']} | `{row['bundle_id']}` | "
            f"{row['lane']} | {row['status']} | {row.get('final_fold') or '—'} | "
            f"{value('strategy_return_pct', '%')} | "
            f"{value('side_benchmark_pct', '%')} | "
            f"{value('alpha_vs_benchmark_pp', 'pp')} | "
            f"{value('tim_pct', '%')} | {row.get('trades') if row.get('trades') is not None else '—'} |"
        )
    if not payload.get("latest_receipts"):
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    (root / "PROGRESS.md").write_text("\n".join(lines) + "\n")


def ingest_fleet(root: Path, fleet_db: Path) -> dict[str, int]:
    if not fleet_db.exists() or fleet_db.stat().st_size == 0:
        raise FileNotFoundError(fleet_db)
    queue = connect(root)
    fleet = sqlite3.connect(fleet_db, timeout=120)
    fleet.execute("PRAGMA journal_mode=WAL")
    campaign = meta_get(queue, "campaign_id")
    jobs_added = rows_added = 0
    for bundle in BUNDLES:
        path_id = f"BUNDLE_{bundle.bundle_id}"
        universe = {
            "campaign": campaign,
            "source": "recent Tradier 30d broker cohort + labeled pilot exceptions",
        }
        contract = {
            "description": bundle.description,
            "overrides": bundle.overrides,
            "component_paths": bundle.component_paths,
            "coherent_bundle": True,
            "field_by_field_attribution": False,
            "tim_gate_pct": [65, 80],
            "exact_only_strict": True,
        }
        before = fleet.total_changes
        fleet.execute(
            """INSERT OR IGNORE INTO jobs
               (path_id,kind,priority,status,universe_json,contract_json)
               VALUES(?,?,?,?,?,?)""",
            (
                path_id,
                "BUNDLE",
                30 if bundle.prior_promising else 90,
                "SCREENED",
                json.dumps(universe, sort_keys=True),
                json.dumps(contract, sort_keys=True),
            ),
        )
        jobs_added += int(fleet.total_changes > before)
    fleet.commit()
    attempts = queue.execute(
        """SELECT a.*,h.symbol,h.side,h.bundle_id
           FROM attempts a JOIN handles h ON h.id=a.handle_id
           WHERE h.campaign_id=? AND a.status!='RUNNING'
             AND a.fleet_ingested=0
           ORDER BY a.id""",
        (campaign,),
    ).fetchall()
    for row in attempts:
        receipt = json.loads(Path(row["receipt_path"]).read_text())
        last = (receipt.get("folds") or [{}])[-1]
        metrics = last.get("metrics") or {}
        control = last.get("same_fold_vec_control") or {}
        job = fleet.execute(
            "SELECT id FROM jobs WHERE path_id=?",
            (f"BUNDLE_{row['bundle_id']}",),
        ).fetchone()
        if not job:
            continue
        payload = {
            "scheduler_receipt_sha256": row["receipt_sha256"],
            "scheduler_receipt": row["receipt_path"],
            "campaign_id": campaign,
            "lane": row["lane"],
            "bundle_id": row["bundle_id"],
            "failures": receipt.get("failures", []),
            "folds": receipt.get("folds", []),
            "coherent_bundle": True,
            "matrix_cell_written": False,
            "live_config_written": False,
            "metric_scope": metrics.get("metric_scope"),
            "return_unit": metrics.get("return_unit"),
            "return_aggregation": metrics.get("return_aggregation"),
            "tim_unit": metrics.get("tim_unit"),
            "tim_aggregation": metrics.get("tim_aggregation"),
            "tim_metric": "binary_position_bar_pct",
            "tim_binary_pct": metrics.get("tim_pct"),
            "tim_weighted_pct": None,
            "capital_base_usd": metrics.get(
                "average_committed_fill_notional_usd"
            ),
        }
        duplicate = fleet.execute(
            "SELECT 1 FROM results WHERE payload_json LIKE ? LIMIT 1",
            (f'%{row["receipt_sha256"]}%',),
        ).fetchone()
        if duplicate:
            queue.execute(
                "UPDATE attempts SET fleet_ingested=1 WHERE id=?", (row["id"],)
            )
            continue
        fleet.execute(
            """INSERT INTO results
               (job_id,symbol,side,stage,status,strategy_return_pct,
                bh_return_pct,same_entry_control_return_pct,
                alpha_vs_bh_pp,alpha_vs_control_pp,tim_pct,trades,
                untouched_oos,exact_replay,future_htf_count,artifact,
                payload_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job[0],
                row["symbol"],
                row["side"],
                "RECENT30_90_10_BUNDLE_VEC",
                (
                    "VECTOR_STRICT_EXACT_PENDING"
                    if row["status"] == "EXACT_PENDING"
                    else f"GRAY_{row['status']}"
                ),
                metrics.get("strategy_return_pct"),
                metrics.get("bh_return_pct"),
                control.get("strategy_return_pct"),
                (
                    metrics.get("strategy_return_pct", 0)
                    - metrics.get("side_benchmark_pct", 0)
                    if metrics
                    else None
                ),
                (
                    metrics.get("strategy_return_pct", 0)
                    - control.get("strategy_return_pct", 0)
                    if metrics and control
                    else None
                ),
                metrics.get("tim_pct"),
                metrics.get("trades"),
                int(
                    row["status"] in {"VALIDATION_REJECTED", "EXACT_PENDING"}
                ),
                0,
                0,
                str(Path(row["receipt_path"]).parent),
                json.dumps(payload, sort_keys=True),
                time.time(),
            ),
        )
        queue.execute(
            "UPDATE attempts SET fleet_ingested=1 WHERE id=?", (row["id"],)
        )
        rows_added += 1
    fleet.commit()
    queue.commit()
    fleet.close()
    queue.close()
    return {"jobs_added": jobs_added, "rows_added": rows_added}


def run_loop(args: argparse.Namespace) -> int:
    con = connect(args.root)
    owner = f"{args.tag}:{os.getpid()}"
    only_keys = {
        key.strip().upper()
        for key in args.keys.split(",")
        if key.strip()
    } or None
    completed = 0
    while args.max_handles <= 0 or completed < args.max_handles:
        claimed = claim(con, owner, only_keys=only_keys)
        if claimed is None:
            break
        handle, lane = claimed
        receipt = run_handle(
            con=con,
            root=args.root,
            handle=handle,
            lane=lane,
            npz_dir=args.npz_dir,
            tim_min=args.tim_min,
            tim_max=args.tim_max,
        )
        path = finish_attempt(con, args.root, handle, receipt)
        completed += 1
        print(
            json.dumps(
                {
                    "key": f"{handle['symbol']}_{handle['side']}",
                    "bundle": handle["bundle_id"],
                    "lane": lane,
                    "status": receipt["status"],
                    "elapsed_s": round(float(receipt.get("elapsed_s", 0)), 2),
                    "receipt": str(path),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        payload = status_payload(con)
        write_progress(args.root, payload)
    payload = status_payload(con)
    con.close()
    write_progress(args.root, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--cohort", type=Path, required=True)
    init.add_argument("--fleet-db", type=Path, default=DEFAULT_FLEET)
    init.add_argument(
        "--pilot-exceptions", default="TTD_SHORT,ACN_SHORT"
    )
    init.add_argument("--priority-budget-s", type=int, default=180)
    init.add_argument("--explore-budget-s", type=int, default=90)
    run = sub.add_parser("run")
    run.add_argument("--npz-dir", type=Path, default=DEFAULT_NPZ)
    run.add_argument("--tag", default="worker")
    run.add_argument("--keys", default="")
    run.add_argument("--max-handles", type=int, default=1)
    run.add_argument("--tim-min", type=float, default=65.0)
    run.add_argument("--tim-max", type=float, default=80.0)
    sub.add_parser("status")
    ingest = sub.add_parser("ingest-fleet")
    ingest.add_argument("--fleet-db", type=Path, default=DEFAULT_FLEET)
    args = parser.parse_args()
    if args.action == "init":
        payload = init_campaign(
            root=args.root,
            cohort_path=args.cohort,
            fleet_db=args.fleet_db,
            pilot_exceptions=[
                row.strip()
                for row in args.pilot_exceptions.split(",")
                if row.strip()
            ],
            priority_budget_s=args.priority_budget_s,
            explore_budget_s=args.explore_budget_s,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.action == "run":
        return run_loop(args)
    if args.action == "status":
        con = connect(args.root)
        payload = status_payload(con)
        con.close()
        write_progress(args.root, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.action == "ingest-fleet":
        print(json.dumps(ingest_fleet(args.root, args.fleet_db), sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
