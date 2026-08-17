#!/usr/bin/env python3
"""param_results_store.py — per-(symbol, side, param) test-result store for the
stocks baseline campaign (crypto later; schema is mode-tagged from day one).

Answers the questions the current stores cannot (they keep only the single BEST
config per key):
  * which params/values were tested for each (symbol, side) to reach its baseline
  * the RANGE of inputs tested and the RANGE of (gain/mo - b&h/mo) outcomes
  * whether there is still HOPE outside the tested range (best value sits at an
    extreme, or the two extremes disagree materially) -> suggested next values
  * which params are INERT (no value moved the outcome for ANY symbol) ->
    wiring suspects that must be fixed before pruning (Bible §1: dead knob ≠ useless)

DB: data/param_results_stocks.db (sqlite, S1 is authority; Mac gets rsync copies).
Tables:
  key_baseline(mode, account, symbol, side, campaign, ts, window_start, years,
      pool_sharpe, trades, acc_gain_pct, gain_per_mo, bh_pct, bh_per_mo,
      delta_gain_mo_vs_bh, overrides_json, stamp, source_file)
  param_cells(mode, symbol, side, campaign, param, value_json, value_num,
      pool_sharpe, trades, acc_gain_pct, gain_per_mo, delta_gain_mo_vs_bh,
      delta_vs_baseline_gain_mo, ts, overrides_json, stamp, source_file)
      UNIQUE(mode, symbol, side, campaign, param, value_json)
All rows carry the FULL overrides_json + 4-file stamp (Bible §12.5 — a result
without its complete recipe is a lie by omission). NEVER deleted, only appended
(user: preserve all data, stop chasing tails).

Analytics (read-side, recomputed on demand):
  param_ranges(mode, campaign)  -> per (symbol, side, param): n_values, vmin, vmax,
      best_value, best_delta, worst_delta, spread, edge_flag, suggested_next (JSON)
  inert_params(mode, campaign)  -> params where max |delta| < INERT_EPS across ALL keys
  export_xlsx(path)             -> workbook: Baselines / ParamRanges / Suggestions / Inert

Was-it-tested pre-flight (Bible §4, user: read all reports before retesting):
  already_tested(mode, symbol, side, param, value) -> bool  (checks this store AND
      test_results_central.db symbol_results/switch_settings when present)
"""
import hashlib
import json
import os
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _set_local_immutable(path, enabled):
    """Close the macOS report-puller overwrite race around atomic exports."""
    import stat
    import sys

    path = Path(path)
    if sys.platform != "darwin" or not path.exists():
        return
    flag = getattr(stat, "UF_IMMUTABLE", 0)
    if flag:
        current = path.stat().st_flags
        os.chflags(path, (current | flag) if enabled else (current & ~flag))
DB_PATH = BASE / "data" / "param_results_stocks.db"
CENTRAL_DB = BASE / "data" / "test_results_central.db"
INERT_EPS = 0.05  # gain/mo pp below which a delta is noise
REPAIRED_CAMPAIGN = "stocks_repaired_20260730_c5"
CAPITAL_ACCOUNTING_VERSION = "avg-trade-deployed-2000-v1"
REPAIRED_CUTOFF = "2026-07-30T03:30:10Z"


def trades_fingerprint_c4(trades):
    # USER 2026-07-21: "If 2 fields produce the same results for different settings the
    # function is broken." A fingerprint over the realised trade list makes that mechanical:
    # identical fingerprint to the same-tier baseline => the override changed NOTHING, so the
    # knob is unconsumed (RECONNECT), and its delta MUST be reported as 0, never as a gain.
    h = hashlib.md5()
    for t in sorted(trades, key=lambda x: (x.get("entry_ts") or 0, x.get("exit_ts") or 0)):
        h.update(f"{t.get('entry_ts')}|{t.get('exit_ts')}|{round(float(t.get('pnl_pct') or 0.0), 6)};".encode())
    return f"{len(trades)}:{h.hexdigest()[:16]}"


def trades_fingerprint(trades, contract_version=None):
    """Return the version-appropriate exact schedule/action fingerprint.

    C4 remains byte-for-byte unchanged so every existing row stays historical
    evidence.  C5 callers must pass their contract version explicitly; this
    prevents a reporting process from silently relabelling c4 JSONL.
    """
    if str(contract_version or "").startswith("tradier-matrix-exec-c5"):
        from c5_action_fingerprint import exact_action_fingerprint

        return exact_action_fingerprint(trades)
    return trades_fingerprint_c4(trades)

SCHEMA = """
CREATE TABLE IF NOT EXISTS key_baseline (
  mode TEXT, account TEXT, symbol TEXT, side TEXT, campaign TEXT, ts TEXT,
  window_start TEXT, years REAL, pool_sharpe REAL, trades INTEGER,
  acc_gain_pct REAL, gain_per_mo REAL, bh_pct REAL, bh_per_mo REAL,
  delta_gain_mo_vs_bh REAL, overrides_json TEXT, stamp TEXT, source_file TEXT,
  UNIQUE(mode, symbol, side, campaign)
);
CREATE TABLE IF NOT EXISTS param_cells (
  mode TEXT, symbol TEXT, side TEXT, campaign TEXT, param TEXT,
  value_json TEXT, value_num REAL, pool_sharpe REAL, trades INTEGER,
  acc_gain_pct REAL, gain_per_mo REAL, delta_gain_mo_vs_bh REAL,
  delta_vs_baseline_gain_mo REAL, ts TEXT, overrides_json TEXT, stamp TEXT,
  source_file TEXT,
  UNIQUE(mode, symbol, side, campaign, param, value_json)
);
CREATE INDEX IF NOT EXISTS idx_cells_param ON param_cells(mode, campaign, param);
CREATE INDEX IF NOT EXISTS idx_cells_key ON param_cells(mode, campaign, symbol, side);
CREATE TABLE IF NOT EXISTS param_cell_history (
  mode TEXT, symbol TEXT, side TEXT, campaign TEXT, param TEXT,
  value_json TEXT, archived_ts TEXT, replacement_contract_fingerprint TEXT,
  archive_reason TEXT, row_json TEXT,
  UNIQUE(mode, symbol, side, campaign, param, value_json, archived_ts)
);
CREATE INDEX IF NOT EXISTS idx_cell_history_key
  ON param_cell_history(mode, campaign, symbol, side, param);
CREATE TABLE IF NOT EXISTS key_baseline_history (
  mode TEXT, symbol TEXT, side TEXT, campaign TEXT, archived_ts TEXT,
  replacement_capital_accounting_version TEXT, archive_reason TEXT,
  row_json TEXT,
  UNIQUE(mode, symbol, side, campaign, archived_ts)
);
CREATE INDEX IF NOT EXISTS idx_baseline_history_key
  ON key_baseline_history(mode, campaign, symbol, side);
"""

MIGRATIONS = (
    "ALTER TABLE key_baseline ADD COLUMN time_in_mkt_pct REAL",
    "ALTER TABLE key_baseline ADD COLUMN capture_vs_bh REAL",
    "ALTER TABLE param_cells ADD COLUMN time_in_mkt_pct REAL",
    # 2026-07-21 TIER-HONESTY COLUMNS (NO-LIES MANDATE). 21,937 of 25,600 tradier cells were
    # Tier-1 vec screens stored indistinguishably from engine truth, and 14,073 of them carried
    # a delta subtracted from a Tier-2 ENGINE baseline — a cross-tier difference that is pure
    # tier gap, not knob effect (MU_LONG: 495 cells all reporting a phantom +1.3917 gain/mo).
    "ALTER TABLE param_cells ADD COLUMN tier TEXT",
    "ALTER TABLE param_cells ADD COLUMN baseline_stamp TEXT",
    "ALTER TABLE param_cells ADD COLUMN trades_fingerprint TEXT",
    "ALTER TABLE param_cells ADD COLUMN inert INTEGER",
    "ALTER TABLE param_cells ADD COLUMN delta_legacy_crosstier REAL",
    "ALTER TABLE key_baseline ADD COLUMN tier TEXT",
    "ALTER TABLE key_baseline ADD COLUMN trades_fingerprint TEXT",
    # Repaired-matrix contract (2026-07-25).  A timestamp/campaign label alone cannot prove
    # that a row used the side-isolated, B&H-seeded, closed-HTF engine and the exact NPZ that
    # is current now.  These fields make that proof queryable and let reports quarantine all
    # older rows without deleting the historical evidence.
    "ALTER TABLE param_cells ADD COLUMN validation_status TEXT",
    "ALTER TABLE param_cells ADD COLUMN contract_fingerprint TEXT",
    "ALTER TABLE param_cells ADD COLUMN real_closes INTEGER",
    "ALTER TABLE param_cells ADD COLUMN mtm_count INTEGER",
    "ALTER TABLE param_cells ADD COLUMN opens_long INTEGER",
    "ALTER TABLE param_cells ADD COLUMN opens_short INTEGER",
    "ALTER TABLE param_cells ADD COLUMN requested_fill_ratio REAL",
    "ALTER TABLE param_cells ADD COLUMN size_clamp_count INTEGER",
    "ALTER TABLE param_cells ADD COLUMN reentry_pending INTEGER",
    "ALTER TABLE param_cells ADD COLUMN reentry_violations INTEGER",
    "ALTER TABLE param_cells ADD COLUMN result_audit_json TEXT",
    "ALTER TABLE key_baseline ADD COLUMN validation_status TEXT",
    "ALTER TABLE key_baseline ADD COLUMN contract_fingerprint TEXT",
    "ALTER TABLE key_baseline ADD COLUMN real_closes INTEGER",
    "ALTER TABLE key_baseline ADD COLUMN mtm_count INTEGER",
    "ALTER TABLE key_baseline ADD COLUMN opens_long INTEGER",
    "ALTER TABLE key_baseline ADD COLUMN opens_short INTEGER",
    "ALTER TABLE key_baseline ADD COLUMN requested_fill_ratio REAL",
    "ALTER TABLE key_baseline ADD COLUMN size_clamp_count INTEGER",
    "ALTER TABLE key_baseline ADD COLUMN reentry_pending INTEGER",
    "ALTER TABLE key_baseline ADD COLUMN reentry_violations INTEGER",
    "ALTER TABLE key_baseline ADD COLUMN result_audit_json TEXT",
    # Capital-accounting identity. Exact schedules can remain contract-identical
    # while their reported performance changes when leverage normalization is
    # repaired, so this must be queryable independently of engine fingerprint.
    "ALTER TABLE param_cells ADD COLUMN capital_accounting_version TEXT",
    "ALTER TABLE param_cells ADD COLUMN benchmark_deployed_usd REAL",
    "ALTER TABLE param_cells ADD COLUMN average_deployed_usd REAL",
    "ALTER TABLE param_cells ADD COLUMN capital_normalization_factor REAL",
    "ALTER TABLE param_cells ADD COLUMN raw_pnl_usd REAL",
    "ALTER TABLE param_cells ADD COLUMN normalized_pnl_usd REAL",
    "ALTER TABLE param_cells ADD COLUMN max_dd_pct REAL",
    "ALTER TABLE key_baseline ADD COLUMN capital_accounting_version TEXT",
    "ALTER TABLE key_baseline ADD COLUMN benchmark_deployed_usd REAL",
    "ALTER TABLE key_baseline ADD COLUMN average_deployed_usd REAL",
    "ALTER TABLE key_baseline ADD COLUMN capital_normalization_factor REAL",
    "ALTER TABLE key_baseline ADD COLUMN raw_pnl_usd REAL",
    "ALTER TABLE key_baseline ADD COLUMN normalized_pnl_usd REAL",
    "ALTER TABLE key_baseline ADD COLUMN max_dd_pct REAL",
)


def busy_retry(fn, tries=40, delay=5):
    # 2026-07-21: `database is locked` raised out of insert_cell killed the whole Tier-2
    # fleet (param_matrix_daemon.py:204 traceback, 31 crashes/worker log, 0 engine cells
    # produced for hours). busy_timeout alone does not cover every BUSY class, so every
    # write goes through here and the caller NEVER sees an OperationalError.
    last = None
    for _ in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
            last = exc
            time.sleep(delay)
    raise sqlite3.OperationalError(f"busy after {tries} tries: {last}")


def _schema_current(con):
    """True when the tables and every migrated column already exist (read-only check)."""
    try:
        cols = {t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                for t in ("param_cells", "key_baseline")}
    except sqlite3.OperationalError:
        return False
    if not cols["param_cells"] or not cols["key_baseline"]:
        return False
    if con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='param_cell_history'"
    ).fetchone() is None:
        return False
    if con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='key_baseline_history'"
    ).fetchone() is None:
        return False
    for mig in MIGRATIONS:
        parts = mig.split()
        if parts[2] in cols and parts[5] not in cols[parts[2]]:
            return False
    return True


def connect(db_path=None):
    # 2026-07-21: WAL + long busy_timeout — 6 engine workers + 2 combo hunters + vec lane +
    # cron rebuilds share this DB; default journal mode was crash-looping every writer.
    con = sqlite3.connect(str(db_path or DB_PATH), timeout=600)
    try:
        con.execute("PRAGMA busy_timeout=600000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    # DDL takes a WRITE lock. Running it on EVERY connect meant a 14-worker fleet spent its
    # entire startup starving itself for the lock — workers sat at 1s of CPU for 20+ minutes,
    # never reaching the work loop (2026-07-21). Check first; only write when actually behind.
    if _schema_current(con):
        return con
    busy_retry(lambda: con.executescript(SCHEMA))
    for mig in MIGRATIONS:
        # 2026-07-21: this used to swallow EVERY OperationalError. Under an 11-writer fleet the
        # ALTER TABLEs lost the write lock and were skipped silently — then insert_cell wrote to
        # columns that did not exist. "duplicate column" is the only benign outcome; a lock must
        # be outwaited, never ignored, or the schema and the writers drift apart in silence.
        try:
            busy_retry(lambda m=mig: con.execute(m))
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc):
                raise
    con.commit()
    return con


def upsert_baseline(con, row):
    cols = ("mode", "account", "symbol", "side", "campaign", "ts", "window_start", "years",
            "pool_sharpe", "trades", "acc_gain_pct", "gain_per_mo", "bh_pct", "bh_per_mo",
            "delta_gain_mo_vs_bh", "overrides_json", "stamp", "source_file",
            "time_in_mkt_pct", "capture_vs_bh", "tier", "trades_fingerprint",
            "validation_status", "contract_fingerprint", "real_closes", "mtm_count",
            "opens_long", "opens_short", "requested_fill_ratio", "size_clamp_count",
            "reentry_pending", "reentry_violations", "result_audit_json",
            "capital_accounting_version", "benchmark_deployed_usd",
            "average_deployed_usd", "capital_normalization_factor",
            "raw_pnl_usd", "normalized_pnl_usd", "max_dd_pct")
    if (row.get("trades") or 0) <= 0 and os.environ.get("V8_ALLOW_ZERO_TRADE") != "1":
        raise ZeroTradeBug(
            f"ZERO_TRADE_BASELINE {row.get('symbol')}_{row.get('side')} tier={row.get('tier')} "
            f"campaign={row.get('campaign')} — a baseline that never traded makes EVERY cell "
            f"diffed against it meaningless (VT 2026-07-23: 532 ENGINE cells vs a 0-trade base)")
    busy_retry(lambda: con.execute(
        f"INSERT INTO key_baseline ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        "ON CONFLICT(mode, symbol, side, campaign) DO UPDATE SET "
        + ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("mode", "symbol", "side", "campaign")),
        [row.get(c) for c in cols]))


class ZeroTradeBug(ValueError):
    """trades==0 is ALWAYS a harness bug, never a result (USER 2026-07-23)."""


def _canonical_json(value):
    """Normalize stored JSON receipts before deciding they describe a new run."""
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return value
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), default=str)


def assert_traded(row, strict=True):
    """USER MANDATE 2026-07-23: 'EVERY 0 TRADE FIELD IS A BUG BECAUSE WE START WITH B&H
    ALWAYS IN A TRADE.' Every cell begins holding the b&h position from bar 0, so even a
    config that blocks every new entry still carries that seed position, marked-to-market at
    the final bar. Minimum trades is therefore 1. trades==0 does NOT mean 'this knob kills
    trading' — it means the seed never opened, so the row reports 0.00% where it should
    report the b&h return. That is the FALSE FLOOR: it reads as the safest cell in the
    matrix while actually being the only one that never traded. Reject at the write
    chokepoint; do not let it reach the DB and get diffed against."""
    t = row.get("trades")
    if t is None or int(t) <= 0:
        msg = (f"ZERO_TRADE_BUG {row.get('symbol')}_{row.get('side')} "
               f"param={row.get('param')} tier={row.get('tier')} campaign={row.get('campaign')} "
               f"— unseeded run, NOT a result (b&h floor missing)")
        if strict:
            raise ZeroTradeBug(msg)
        print(f"[REJECT] {msg}")
        return False
    return True


def insert_cell(con, row):
    if not assert_traded(row, strict=os.environ.get("V8_ALLOW_ZERO_TRADE") != "1"):
        return
    cols = ("mode", "symbol", "side", "campaign", "param", "value_json", "value_num",
            "pool_sharpe", "trades", "acc_gain_pct", "gain_per_mo", "delta_gain_mo_vs_bh",
            "delta_vs_baseline_gain_mo", "ts", "overrides_json", "stamp", "source_file",
            "time_in_mkt_pct", "tier", "baseline_stamp", "trades_fingerprint", "inert",
            "validation_status", "contract_fingerprint", "real_closes", "mtm_count",
            "opens_long", "opens_short", "requested_fill_ratio", "size_clamp_count",
            "reentry_pending", "reentry_violations", "result_audit_json",
            "capital_accounting_version", "benchmark_deployed_usd",
            "average_deployed_usd", "capital_normalization_factor",
            "raw_pnl_usd", "normalized_pnl_usd", "max_dd_pct")
    key_cols = ("mode", "symbol", "side", "campaign", "param", "value_json")
    key = [row.get(c) for c in key_cols]

    def write_cell():
        existing = con.execute(
            f"SELECT {','.join(cols)} FROM param_cells WHERE "
            + " AND ".join(f"{c}=?" for c in key_cols),
            key,
        ).fetchone()
        if existing is None:
            con.execute(
                f"INSERT INTO param_cells ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [row.get(c) for c in cols],
            )
            return

        incoming_engine = str(row.get("tier", "")).upper() == "ENGINE"
        old = dict(zip(cols, existing))
        old_engine = str(old.get("tier") or "").upper() == "ENGINE"
        contract_changed = (
            incoming_engine
            and old.get("contract_fingerprint") != row.get("contract_fingerprint")
        )
        tier_upgrade = incoming_engine and not old_engine
        same_contract_receipt_refresh = (
            incoming_engine
            and old_engine
            and bool(row.get("replace_same_contract_receipt"))
            and old.get("contract_fingerprint")
            == row.get("contract_fingerprint")
            and _canonical_json(old.get("overrides_json"))
            != _canonical_json(row.get("overrides_json"))
        )
        valid_statuses = (
            {"PASS"}
            if row.get("campaign") == REPAIRED_CAMPAIGN
            else {
                "PASS",
                "PASS_WITH_CAPACITY_CLAMPS",
                "INCOMPLETE_NO_REAL_CLOSE",
            }
        )
        same_contract_validation_refresh = (
            incoming_engine
            and old_engine
            and bool(row.get("replace_same_contract_invalid"))
            and old.get("contract_fingerprint")
            == row.get("contract_fingerprint")
            and old.get("validation_status") not in valid_statuses
            and row.get("validation_status") in valid_statuses
        )
        capital_accounting_refresh = (
            incoming_engine
            and old_engine
            and bool(row.get("capital_accounting_version"))
            and old.get("contract_fingerprint")
            == row.get("contract_fingerprint")
            and old.get("capital_accounting_version")
            != row.get("capital_accounting_version")
        )
        if not (
            contract_changed
            or tier_upgrade
            or same_contract_receipt_refresh
            or same_contract_validation_refresh
            or capital_accounting_refresh
        ):
            return

        # A repaired contract can invalidate an existing logical cell while the UNIQUE key
        # remains identical. INSERT OR IGNORE used to discard every valid rerun forever, so
        # six 24/7 workers repeatedly recomputed cells without advancing the matrix. Preserve
        # the displaced measurement verbatim, then atomically promote the new ENGINE result.
        con.execute(
            "INSERT INTO param_cell_history "
            "(mode,symbol,side,campaign,param,value_json,archived_ts,"
            "replacement_contract_fingerprint,archive_reason,row_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                *key,
                str(time.time_ns()),
                row.get("contract_fingerprint"),
                (
                    "TIER_UPGRADE"
                    if tier_upgrade
                    else (
                        "SAME_CONTRACT_RECEIPT_REFRESH"
                        if same_contract_receipt_refresh
                        else (
                            "SAME_CONTRACT_VALIDATION_REFRESH"
                            if same_contract_validation_refresh
                            else (
                                "CAPITAL_ACCOUNTING_REFRESH"
                                if capital_accounting_refresh
                                else "CONTRACT_REFRESH"
                            )
                        )
                    )
                ),
                json.dumps(old, sort_keys=True, default=str),
            ],
        )
        con.execute(
            f"UPDATE param_cells SET {','.join(f'{c}=?' for c in cols)} WHERE "
            + " AND ".join(f"{c}=?" for c in key_cols),
            [row.get(c) for c in cols] + key,
        )

    busy_retry(write_cell)
    # COMMIT HERE, ALWAYS (2026-07-21). SQLite allows one writer at a time, and a writer holds
    # the lock from its first statement until it commits. combo_search.py inserted inside a loop
    # whose body runs a FULL 9-15min engine (key_gain_mo) and only committed after the loop — so
    # it held the write lock across several engine runs and every other writer blocked behind it.
    # That is the mechanism behind the 5h06m DB starvation and the fleet stalling in insert_cell.
    # A cell is a few hundred bytes; holding a transaction open across compute is never correct.
    busy_retry(con.commit)


def already_tested(con, mode, symbol, side, param, value):
    # 2026-07-21: rows store value_json as the RAW string (insert_cell) while this
    # compared json.dumps(value) ('"X"' vs 'X') — string-valued cells never matched,
    # so every pass re-verified pack/TF cells forever. Match both spellings.
    vj = json.dumps(value)
    cur = con.execute(
        "SELECT 1 FROM param_cells WHERE mode=? AND symbol=? AND side=? AND param=? AND value_json IN (?,?) LIMIT 1",
        (mode, symbol, side, param, vj, str(value)))
    if cur.fetchone():
        return True
    if CENTRAL_DB.exists():
        try:
            ccon = sqlite3.connect(str(CENTRAL_DB))
            cur = ccon.execute(
                "SELECT 1 FROM symbol_results sr JOIN switch_settings ss "
                "ON ss.ref_id = sr.sr_id AND ss.scope='symbol_results' "
                "WHERE sr.mode=? AND sr.symbol=? AND sr.side=? AND ss.switch=? AND ss.value=? LIMIT 1",
                (mode, symbol, side, param, str(value)))
            hit = cur.fetchone() is not None
            ccon.close()
            return hit
        except Exception:
            pass
    return False


def _suggest_next(values, deltas):
    """values: sorted numeric tested values; deltas: delta gain/mo-vs-baseline per value.
    HOPE rule (user 2026-07-18): if the extreme tested values do not both flatten to
    ~equal outcomes, the optimum may lie OUTSIDE the tested range -> suggest extending
    past the better extreme by one range-step. Also suggest midpoint refinement around
    the best value when the grid is coarse."""
    if len(values) < 2:
        return {"edge_flag": "INSUFFICIENT", "suggested_next": []}
    lo, hi = values[0], values[-1]
    d_lo, d_hi = deltas[0], deltas[-1]
    best_i = max(range(len(values)), key=lambda i: deltas[i])
    step = (hi - lo) / max(1, len(values) - 1)
    sug = []
    flag = "CONVERGED"
    if best_i == 0:
        flag = "HOPE_BELOW"
        sug.append(lo - step)
    elif best_i == len(values) - 1:
        flag = "HOPE_ABOVE"
        sug.append(hi + step)
    elif abs(d_lo - d_hi) > INERT_EPS:
        flag = "EDGES_DIVERGE"
        sug.append((lo - step) if d_lo > d_hi else (hi + step))
    if 0 < best_i < len(values) - 1 and step > 0:
        left, right = values[best_i - 1], values[best_i + 1]
        if right - left > step * 1.5:
            sug.extend([(left + values[best_i]) / 2, (values[best_i] + right) / 2])
    return {"edge_flag": flag, "suggested_next": [round(s, 6) for s in sug]}


def param_ranges(con, mode="tradier", campaign=None):
    q = ("SELECT symbol, side, param, value_json, value_num, delta_vs_baseline_gain_mo, "
         "delta_gain_mo_vs_bh, pool_sharpe, trades FROM param_cells WHERE mode=?")
    args = [mode]
    if campaign:
        q += " AND campaign=?"
        args.append(campaign)
    rows = con.execute(q, args).fetchall()
    grouped = {}
    for sym, side, param, vj, vn, dvb, dbh, ps, tr in rows:
        grouped.setdefault((sym, side, param), []).append((vj, vn, dvb or 0.0, dbh, ps, tr))
    out = []
    for (sym, side, param), cells in sorted(grouped.items()):
        numeric = [c for c in cells if c[1] is not None]
        best = max(cells, key=lambda c: c[2])
        deltas = [c[2] for c in cells] + [0.0]
        rec = {"symbol": sym, "side": side, "param": param, "n_values": len(cells),
               "best_value": best[0], "best_delta_gain_mo": round(best[2], 4),
               "worst_delta_gain_mo": round(min(c[2] for c in cells), 4),
               "spread": round(max(deltas) - min(deltas), 4),
               "best_pool_sharpe": best[4], "best_delta_gain_mo_vs_bh": best[3]}
        if len(numeric) >= 2:
            numeric.sort(key=lambda c: c[1])
            rec.update(_suggest_next([c[1] for c in numeric], [c[2] for c in numeric]))
            rec.update({"vmin": numeric[0][1], "vmax": numeric[-1][1]})
        else:
            rec.update({"edge_flag": "CATEGORICAL", "suggested_next": [], "vmin": None, "vmax": None})
        out.append(rec)
    return out


def inert_params(con, mode="tradier", campaign=None):
    ranges = param_ranges(con, mode, campaign)
    by_param = {}
    for r in ranges:
        by_param.setdefault(r["param"], []).append(r["spread"])
    return sorted(p for p, spreads in by_param.items()
                  if len(spreads) >= 3 and max(spreads) < INERT_EPS)


def relevance_ranking(con, mode="tradier", campaign=None):
    """Params sorted by max |delta gain/mo| across keys — the pruning order.
    A param is only prunable AFTER proof it moves outcomes for no symbol (inert ->
    wiring check first, per user mandate)."""
    ranges = param_ranges(con, mode, campaign)
    by_param = {}
    for r in ranges:
        d = by_param.setdefault(r["param"], {"param": r["param"], "n_keys": 0, "max_spread": 0.0,
                                             "n_keys_moved": 0})
        d["n_keys"] += 1
        d["max_spread"] = max(d["max_spread"], r["spread"])
        if r["spread"] >= INERT_EPS:
            d["n_keys_moved"] += 1
    return sorted(by_param.values(), key=lambda d: -d["max_spread"])


def _table_columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _tradeable_stock_keys(base=BASE):
    """Return the pinned-live plus historical TRB symbol-side union."""
    try:
        from tools.audit_stock_matrix_workbooks import configured_keys
    except ModuleNotFoundError:
        from audit_stock_matrix_workbooks import configured_keys  # type: ignore
    return configured_keys(Path(base))


def _active_stock_keys(base=BASE):
    """Return only the pinned current live directional universe."""
    try:
        from tools.audit_stock_matrix_workbooks import active_keys
    except ModuleNotFoundError:
        from audit_stock_matrix_workbooks import active_keys  # type: ignore
    return active_keys(Path(base))


def _preserved_v8_evidence(base=BASE):
    """Load immutable historical V8 cells for workbook display only."""
    import hashlib

    archive = Path(base) / "data/reports/PRESERVED_V8_CELLS.jsonl"
    receipt_path = Path(base) / "data/reports/PRESERVED_V8_CELLS_RECEIPT.json"
    if not archive.exists() or not receipt_path.exists():
        return {}
    receipt = json.loads(receipt_path.read_text())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != receipt.get("archive_sha256"):
        raise RuntimeError("PRESERVED_V8_CELLS archive hash mismatch")
    out = {}
    with archive.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if not item.get("immutable_preservation") or item.get("promotion_allowed") is not False:
                raise RuntimeError("invalid PRESERVED_V8_CELLS evidence row")
            logical = (item["key"], item["param"], str(item["value_json"]))
            out[logical] = {
                "gain_per_mo": item.get("gain_per_mo"),
                "delta_vs_bh": item.get("delta_gain_mo_vs_bh"),
                "trades": item.get("trades"),
                "pool_sharpe": item.get("pool_sharpe"),
                "validation": "PRESERVED_V8_REPLAY_REQUIRED",
                "performance_verdict": "HISTORICAL_EXACT_NOT_PROMOTABLE",
                "promotable": False,
                "inert": False,
                "real_closes": None,
                "reentry_violations": None,
                "campaign": item.get("campaign"),
                "ts": item.get("ts"),
                "source_file": item.get("source_file"),
                "evidence_class": "PRESERVED_V8_WORKBOOK_ROW",
                "preservation_display_status": item.get("preservation_display_status"),
                "preservation_quarantine_reasons": item.get("preservation_quarantine_reasons") or [],
            }
    return out


def _load_path_inventory(base=BASE):
    """Map Tradier config knobs to the current live reason-path catalog.

    The inventory is the human-audited source for reason prefixes, functions and
    descriptions.  The knob registry remains the exhaustive source for settings,
    because one live reason can depend on several knobs and not every knob has
    fired in live history yet.
    """
    candidates = (
        Path(base) / "reports" / "path_inventory.xlsx",
        Path(base) / "data" / "reports" / "path_inventory.xlsx",
    )
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return {}
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return {}
    by_knob = {}
    for sheet in ("Tradier — Entries", "Tradier — Exits"):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        headers = [str(c.value or "").strip() for c in ws[1]]
        idx = {name: i for i, name in enumerate(headers)}
        for values in ws.iter_rows(min_row=2, values_only=True):
            raw_keys = values[idx.get("Config Key", -1)] if "Config Key" in idx else None
            if not raw_keys or str(raw_keys).strip() in ("—", "-", "N/A"):
                continue
            record = {
                "reason": values[idx.get("Reason Prefix", -1)] if "Reason Prefix" in idx else "",
                "description": values[idx.get("Description", -1)] if "Description" in idx else "",
                "source": values[idx.get("Source File", -1)] if "Source File" in idx else "",
                "function": values[idx.get("Function", -1)] if "Function" in idx else "",
                "default": values[idx.get("Default", -1)] if "Default" in idx else "",
                "status": values[idx.get("Status", -1)] if "Status" in idx else "",
            }
            # Inventory keys use "A / B"; reject prose/em-dash tokens.
            for knob in (x.strip() for x in str(raw_keys).split("/")):
                if knob and knob.replace("_", "").isalnum():
                    by_knob.setdefault(knob, []).append(record)
    try:
        wb.close()
    except Exception:
        pass
    return by_knob


def _describe_setting(param, info, inventory):
    records = inventory.get(param, [])
    live = []
    for record in records[:3]:
        reason = str(record.get("reason") or "")
        desc = str(record.get("description") or "")
        if reason or desc:
            live.append(f"{reason}: {desc}".strip(": "))
    group = str(info.get("group") or "OTHER").upper()
    role = str(info.get("role") or "SETTING").replace("_", " ")
    scope = "per-symbol" if info.get("per_sym") else "global"
    fallback = f"{group} {role}; {scope}; config knob {param}."
    return " | ".join(live) if live else fallback


def _path_definitions(base=BASE):
    """Exhaustive ENTRY/EXIT path families with descriptions and setting grids."""
    reg_path = Path(base) / "data" / "knob_registry.json"
    manifest_path = Path(base) / "data" / "param_sweep_manifest_tradier.json"
    missing = [str(path) for path in (reg_path, manifest_path) if not path.exists()]
    if missing:
        # Missing registry data used to produce a formally valid workbook whose
        # Entry Paths and Exit Paths sheets contained zero path columns.  That
        # destroys the human work-list on the next recurring refresh.  Fail
        # before touching the canonical artifact instead.
        raise FileNotFoundError(
            "cannot build PARAM_BASELINE_STOCKS path sheets; missing authority: "
            + ", ".join(missing)
            + ". Rebuild with: V8_SBX=$PWD python3 tools/knob_registry.py build"
        )
    registry = json.loads(reg_path.read_text()).get("tradier", {})
    manifest = json.loads(manifest_path.read_text()).get("params", {})
    if manifest and not registry:
        raise RuntimeError(
            f"empty Tradier registry in {reg_path}; refusing to emit empty path sheets"
        )
    inventory = _load_path_inventory(base)
    grouped = {"ENTRY": {}, "EXIT": {}}
    for param, info in registry.items():
        group = str(info.get("group") or "").upper()
        if group not in grouped:
            continue
        family = str(info.get("family") or param)
        grouped[group].setdefault(family, []).append((param, info))
    result = {"ENTRY": [], "EXIT": []}
    for group, families in grouped.items():
        for family, settings in sorted(families.items()):
            settings.sort(key=lambda item: (
                item[1].get("role") != "MAIN_SWITCH", item[0]
            ))
            detail, descriptions, reasons = [], [], []
            for param, info in settings:
                spec = manifest.get(param, {}) if isinstance(manifest.get(param, {}), dict) else {}
                values = spec.get("test_values") or spec.get("values") or []
                default = spec.get("default", info.get("default"))
                sweepable = bool(spec.get("sweepable"))
                value_text = ",".join(json.dumps(v, separators=(",", ":")) for v in values)
                detail.append(
                    f"{param} [default={json.dumps(default, separators=(',', ':'))}; "
                    f"grid={value_text or 'none'}; sweepable={'yes' if sweepable else 'no'}]"
                )
                descriptions.append(_describe_setting(param, info, inventory))
                reasons.extend(str(r.get("reason") or "") for r in inventory.get(param, []))
            result[group].append({
                "path": family,
                "params": [p for p, _ in settings],
                "settings": " | ".join(detail),
                "description": " | ".join(dict.fromkeys(x for x in descriptions if x)),
                "live_reasons": " | ".join(dict.fromkeys(x for x in reasons if x)),
            })
    return result


def _repaired_evidence(con, mode="tradier", campaign=REPAIRED_CAMPAIGN):
    """Load only contract-matched repaired ENGINE evidence.

    Historical and VEC rows deliberately never enter these structures. A current
    cell is retained for diagnosis when it fails promotion gates, but it must
    match that key's repaired baseline contract.
    """
    bcols, ccols = _table_columns(con, "key_baseline"), _table_columns(con, "param_cells")
    required_base = {
        "validation_status", "contract_fingerprint", "real_closes",
        "reentry_violations", "requested_fill_ratio", "size_clamp_count",
        "tier", "time_in_mkt_pct", "capture_vs_bh",
        "capital_accounting_version",
    }
    required_cells = {
        "validation_status", "contract_fingerprint", "real_closes",
        "reentry_violations", "requested_fill_ratio", "size_clamp_count",
        "tier", "time_in_mkt_pct", "inert",
        "capital_accounting_version",
    }
    if not required_base.issubset(bcols) or not required_cells.issubset(ccols):
        return {}, {}
    baselines = {}
    bq = (
        "SELECT symbol,side,gain_per_mo,bh_per_mo,delta_gain_mo_vs_bh,trades,"
        "pool_sharpe,time_in_mkt_pct,capture_vs_bh,validation_status,"
        "contract_fingerprint,real_closes,reentry_violations,requested_fill_ratio,"
        "size_clamp_count,ts,overrides_json "
        "FROM key_baseline WHERE mode=? AND campaign=? AND COALESCE(tier,'ENGINE')='ENGINE' "
        "AND ts>=? AND validation_status='PASS' "
        "AND capital_accounting_version=? ORDER BY ts"
    )
    for row in con.execute(
        bq, (mode, campaign, REPAIRED_CUTOFF, CAPITAL_ACCOUNTING_VERSION)
    ):
        (sym, side, gain, bh, delta, trades, sharpe, tim, capture, validation,
         contract, real_closes, reentry_violations, fill_ratio, clamps, ts, overrides) = row
        baselines[f"{sym}_{side}"] = {
            "gain_per_mo": gain, "bh_per_mo": bh, "delta_vs_bh": delta,
            "trades": trades, "pool_sharpe": sharpe, "time_in_mkt_pct": tim,
            "capture_vs_bh": capture, "validation": validation, "contract": contract,
            # Structural validation answers whether the exact replay is usable
            # evidence.  It must never be presented as strategy acceptance.
            "performance_verdict": (
                "MEETS 2X B&H RESEARCH BAR"
                if (
                    delta is not None
                    and float(delta) > 0
                    and capture is not None
                    and float(capture) >= 2.0
                )
                else (
                    "ABOVE B&H BUT BELOW 2X — GRAY/NONPROMOTABLE"
                    if delta is not None and float(delta) > 0
                    else "BELOW B&H — DISCARD EVIDENCE"
                )
            ),
            "real_closes": real_closes, "reentry_violations": reentry_violations,
            "requested_fill_ratio": fill_ratio, "size_clamp_count": clamps,
            "ts": ts, "overrides_json": overrides,
        }
    cells = {}
    cq = (
        "SELECT symbol,side,param,value_json,gain_per_mo,delta_gain_mo_vs_bh,trades,"
        "pool_sharpe,time_in_mkt_pct,validation_status,contract_fingerprint,real_closes,"
        "reentry_violations,requested_fill_ratio,size_clamp_count,ts,overrides_json,inert "
        "FROM param_cells WHERE mode=? AND campaign=? AND COALESCE(tier,'ENGINE')='ENGINE' "
        "AND ts>=? AND validation_status='PASS' "
        "AND capital_accounting_version=? ORDER BY ts"
    )
    for row in con.execute(
        cq, (mode, campaign, REPAIRED_CUTOFF, CAPITAL_ACCOUNTING_VERSION)
    ):
        (sym, side, param, value, gain, delta, trades, sharpe, tim, validation,
         contract, real_closes, reentry_violations, fill_ratio, clamps, ts,
         overrides, inert) = row
        key = f"{sym}_{side}"
        base = baselines.get(key)
        if not base or contract != base.get("contract"):
            continue
        promotable = (
            validation == "PASS"
            and (real_closes or 0) >= 1
            and (reentry_violations or 0) == 0
            and not bool(inert)
            and delta is not None
            and float(delta) > 0
            and base.get("bh_per_mo") is not None
            and float(base["bh_per_mo"]) > 0
            and gain is not None
            and float(gain) / float(base["bh_per_mo"]) >= 2.0
        )
        cells[(key, param, str(value))] = {
            "gain_per_mo": gain, "delta_vs_bh": delta, "trades": trades,
            "pool_sharpe": sharpe, "time_in_mkt_pct": tim,
            "validation": validation, "contract": contract,
            "real_closes": real_closes, "reentry_violations": reentry_violations,
            "requested_fill_ratio": fill_ratio, "size_clamp_count": clamps,
            "ts": ts, "overrides_json": overrides, "inert": bool(inert),
            "promotable": promotable,
        }
    return baselines, cells


def _path_fleet_evidence(base=BASE):
    """Load the isolated vector/exact path-fleet ledger for workbook reporting.

    The fleet is intentionally *not* merged into repaired ENGINE cells.  It is
    a separate research tier that makes vector-first progress visible while
    preserving the matrix promotion boundary.
    """
    root = Path(base) / "data" / "reports" / "path_fleet"
    db_path = root / "queue.db"
    registry_path = root / "PATH_FLEET_REGISTRY.json"
    if not db_path.exists():
        return [], []
    try:
        registry_raw = json.loads(registry_path.read_text())
    except (OSError, json.JSONDecodeError):
        registry_raw = []
    registry = {
        str(row.get("path_id")): row
        for row in registry_raw
        if isinstance(row, dict) and row.get("path_id")
    }
    try:
        fleet = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=30
        )
        fleet.row_factory = sqlite3.Row
        jobs = [dict(row) for row in fleet.execute(
            """SELECT id,path_id,kind,priority,status,claimed_by,message
               FROM jobs ORDER BY priority,path_id"""
        )]
        results = [dict(row) for row in fleet.execute(
            """SELECT j.path_id,j.kind,j.priority,j.status job_status,
                      r.symbol,r.side,r.stage,r.status result_status,
                      r.strategy_return_pct,r.bh_return_pct,
                      r.same_entry_control_return_pct,r.alpha_vs_bh_pp,
                      r.alpha_vs_control_pp,r.tim_pct,r.trades,
                      r.untouched_oos,r.exact_replay,r.future_htf_count,
                      r.artifact,r.payload_json,r.created_at
               FROM results r JOIN jobs j ON j.id=r.job_id
               ORDER BY r.created_at DESC,r.id DESC"""
        )]
        fleet.close()
    except (OSError, sqlite3.Error):
        return [], []
    for job in jobs:
        job["registry"] = registry.get(job["path_id"], {})
    for result in results:
        result["registry"] = registry.get(result["path_id"], {})
    return jobs, results


def _write_restored_workbook_sheets(wb, con, mode="tradier", base=BASE):
    """Restore the durable per-symbol and ENTRY/EXIT matrix views.

    These sheets are generated every time PARAM_BASELINE_STOCKS is refreshed,
    so a recurring report run can no longer delete them.
    """
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    keys = _tradeable_stock_keys(base)
    paths = _path_definitions(base)
    baselines, cells = _repaired_evidence(con, mode)
    preserved_cells = _preserved_v8_evidence(base)
    display_cells = dict(preserved_cells)
    # Receipt-valid current ENGINE wins the same logical cell.  Vector rows
    # never enter either mapping.
    display_cells.update(cells)
    # Include repaired evidence keys even if the live list changed after the run.
    for key in sorted(baselines):
        if key not in keys:
            keys.append(key)

    navy = PatternFill("solid", fgColor="1F4E78")
    blue = PatternFill("solid", fgColor="D9EAF7")
    green = PatternFill("solid", fgColor="E2F0D9")
    red = PatternFill("solid", fgColor="F4CCCC")
    gray = PatternFill("solid", fgColor="E7E6E6")
    amber = PatternFill("solid", fgColor="FFF2CC")
    white_bold = Font(color="FFFFFF", bold=True)

    guide = wb.create_sheet("Workbook Guide", 0)
    guide.column_dimensions["A"].width = 34
    guide.column_dimensions["B"].width = 115
    guide_rows = [
        ("PARAM_BASELINE_STOCKS", "Durable stock parameter workbook; restored layout."),
        ("Generated UTC", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        (
            "Configured universe",
            f"{len(_active_stock_keys(base))} ACTIVE plus "
            f"{len(_tradeable_stock_keys(base)) - len(_active_stock_keys(base))} "
            "HISTORICAL directional symbol-sides. Source paths and membership hashes are "
            "audited separately; JSON order-only rewrites do not alter the "
            "membership contract. Blank result cells are allowed; a missing "
            "key row is an export failure.",
        ),
        ("PerSym Results", "One row per current TRB tradeable symbol-side. Only repaired ENGINE "
         f"campaign {REPAIRED_CAMPAIGN} with contract-matched rows is allowed in result cells."),
        ("Entry Paths / Exit Paths", "Every registry path is a column group. Row 2 contains the "
         "human description and live reason paths; row 3 contains per-key subcolumns for tested "
         "settings, best setting, best delta vs B&H, best gain/mo, and result status."),
        ("Green", "Contract-valid PASS cell with real close(s), no reentry violation, non-inert, and >B&H."),
        ("Amber", "Repaired evidence exists but is not promotable (capacity clamp/incomplete gate)."),
        ("Blue", "Immutable historical V8 result recovered from the prior workbook. It cannot "
         "be overwritten by vector output; exact replay/receipt recovery is required to promote."),
        ("Gray", "Below B&H or pending. Retained to prevent blind retesting; never promoted."),
        ("Red", "Invalid/wiring/reentry failure evidence. Diagnose; never promote."),
        ("Legacy sheets", "Baselines, ParamRanges, Suggestions, AllCells, Relevance and Matrix_Top "
         "are preserved for historical research. They may contain pre-repair or VEC evidence and "
         "MUST NOT be copied into repaired/promotable cells."),
        ("Coverage", f"{len(baselines)}/{len(keys)} current keys have repaired baselines; "
         f"{len(cells)} contract-matched repaired cells plus "
         f"{len(preserved_cells)} immutable historical V8 cells loaded."),
    ]
    for row in guide_rows:
        guide.append(row)
    for cell in guide[1]:
        cell.fill, cell.font = navy, white_bold
    for row in guide.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    ws = wb.create_sheet("PerSym Results", 1)
    headers = [
        "key", "symbol", "side", "evidence_tier", "campaign", "baseline_gain/mo",
        "B&H_gain/mo", "baseline_delta_vs_B&H", "baseline_capture_xB&H", "trades",
        "time_in_market_%", "pool_sharpe", "structural_validation",
        "baseline_performance_verdict", "real_closes",
        "reentry_violations", "requested_fill_ratio", "size_clamps", "contract",
        "latest_result_UTC", "paths_tested", "promotable_>B&H_paths", "best_path",
        "best_setting", "best_gain/mo", "best_delta_vs_B&H", "best_status",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = navy, white_bold
    by_key = {}
    for (key, param, value), meta in display_cells.items():
        by_key.setdefault(key, []).append((param, value, meta))
    param_to_path = {}
    for group in ("ENTRY", "EXIT"):
        for path in paths[group]:
            for param in path["params"]:
                param_to_path[param] = f"{group}:{path['path']}"
    for key in keys:
        symbol, side = key.rsplit("_", 1)
        base_row = baselines.get(key, {})
        candidates = by_key.get(key, [])
        best = max(candidates, key=lambda item: (
            item[2].get("delta_vs_bh") if item[2].get("delta_vs_bh") is not None else -1e99
        ), default=None)
        best_meta = best[2] if best else {}
        best_status = (
            "PROMOTABLE > B&H" if best_meta.get("promotable")
            else (
                "PRESERVED V8 / REPLAY REQUIRED"
                if best_meta.get("evidence_class") == "PRESERVED_V8_WORKBOOK_ROW"
                else ("REPAIRED / NOT PROMOTABLE" if best else "PENDING REPAIRED TEST")
            )
        )
        ws.append([
            key, symbol, side,
            (best_meta.get("evidence_class") or "REPAIRED ENGINE ONLY"),
            (best_meta.get("campaign") or REPAIRED_CAMPAIGN),
            base_row.get("gain_per_mo"), base_row.get("bh_per_mo"),
            base_row.get("delta_vs_bh"), base_row.get("capture_vs_bh"),
            base_row.get("trades"), base_row.get("time_in_mkt_pct"),
            base_row.get("pool_sharpe"), base_row.get("validation"),
            base_row.get("performance_verdict"),
            base_row.get("real_closes"), base_row.get("reentry_violations"),
            base_row.get("requested_fill_ratio"), base_row.get("size_clamp_count"),
            base_row.get("contract"), base_row.get("ts"),
            len({param_to_path.get(p, p) for p, _v, _m in candidates}),
            sum(1 for _p, _v, meta in candidates if meta.get("promotable")),
            param_to_path.get(best[0], best[0]) if best else None,
            f"{best[0]}={best[1]}" if best else None,
            (
                None
                if best_meta.get("evidence_class") == "PRESERVED_V8_WORKBOOK_ROW"
                and best_meta.get("preservation_display_status") != "PASS"
                else best_meta.get("gain_per_mo")
            ),
            (
                None
                if best_meta.get("evidence_class") == "PRESERVED_V8_WORKBOOK_ROW"
                and best_meta.get("preservation_display_status") != "PASS"
                else best_meta.get("delta_vs_bh")
            ),
            best_status,
        ])
        fill = (
            green if best_meta.get("promotable")
            else blue if best_meta.get("evidence_class") == "PRESERVED_V8_WORKBOOK_ROW"
            else amber if best
            else gray
        )
        ws.cell(ws.max_row, 27).fill = fill
    ws.freeze_panes = "F2"
    ws.auto_filter.ref = ws.dimensions
    for i, width in enumerate((20, 12, 9, 23, 31, 16, 14, 20, 20, 10, 17, 13,
                               25, 31, 12, 19, 19, 12, 26, 21, 12, 22, 34, 44,
                               14, 20, 26), 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    def write_path_matrix(sheet_name, group):
        ws_path = wb.create_sheet(sheet_name, 2 if group == "ENTRY" else 3)
        fixed = [
            "key", "symbol", "side", "repaired baseline status",
            "repaired B&H gain/mo",
        ]
        for i, value in enumerate(fixed, 1):
            ws_path.cell(1, i, value)
            ws_path.cell(1, i).fill, ws_path.cell(1, i).font = navy, white_bold
            ws_path.merge_cells(start_row=1, start_column=i, end_row=3, end_column=i)
            ws_path.cell(1, i).alignment = Alignment(vertical="center", wrap_text=True)
        subheaders = ["tested settings", "best setting", "best Δgain/mo vs B&H",
                      "best gain/mo", "status"]
        path_columns = {}
        col = len(fixed) + 1
        for path in paths[group]:
            start, end = col, col + len(subheaders) - 1
            ws_path.merge_cells(start_row=1, start_column=start, end_row=1, end_column=end)
            head = ws_path.cell(1, start, path["path"])
            head.fill, head.font = navy, white_bold
            head.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws_path.merge_cells(start_row=2, start_column=start, end_row=2, end_column=end)
            desc = path["description"]
            if path["live_reasons"]:
                desc += f" | LIVE REASONS: {path['live_reasons']}"
            desc += f" | SETTINGS: {path['settings']}"
            dcell = ws_path.cell(2, start, desc)
            dcell.fill = blue
            dcell.alignment = Alignment(vertical="top", wrap_text=True)
            for offset, sub in enumerate(subheaders):
                cell = ws_path.cell(3, start + offset, sub)
                cell.fill, cell.font = blue, Font(bold=True)
                cell.alignment = Alignment(wrap_text=True)
            path_columns[path["path"]] = (start, path)
            col = end + 1
        for key in keys:
            symbol, side = key.rsplit("_", 1)
            base_row = baselines.get(key, {})
            ws_path.append([
                key, symbol, side,
                (
                    f"{base_row.get('validation')} / "
                    f"{base_row.get('performance_verdict')}"
                    if base_row
                    else "PENDING"
                ),
                base_row.get("bh_per_mo"),
            ])
            row_index = ws_path.max_row
            for path_name, (start, path) in path_columns.items():
                path_cells = [
                    (param, value, meta)
                    for (cell_key, param, value), meta in display_cells.items()
                    if cell_key == key and param in path["params"]
                ]
                tested = " | ".join(
                    f"{param}={value}" for param, value, _meta in sorted(path_cells)
                )
                best = max(path_cells, key=lambda item: (
                    item[2].get("delta_vs_bh")
                    if item[2].get("delta_vs_bh") is not None else -1e99
                ), default=None)
                if best:
                    param, value, meta = best
                    if meta.get("evidence_class") == "PRESERVED_V8_WORKBOOK_ROW":
                        if meta.get("preservation_display_status") == "PASS":
                            status, fill = "PRESERVED V8 — EXACT REPLAY REQUIRED", blue
                        else:
                            reasons = ",".join(
                                meta.get("preservation_quarantine_reasons") or []
                            )
                            status, fill = f"PRESERVED V8 QUARANTINED: {reasons}", red
                    elif meta.get("promotable"):
                        status, fill = "PROMOTABLE > B&H", green
                    elif meta.get("validation") != "PASS":
                        status, fill = f"NOT PROMOTABLE: {meta.get('validation')}", amber
                    elif (meta.get("reentry_violations") or 0) > 0 or (meta.get("real_closes") or 0) < 1:
                        status, fill = "INVALID: close/reentry contract", red
                    elif meta.get("inert"):
                        status, fill = "INVALID: INERT / wiring", red
                    else:
                        status, fill = "BELOW B&H — DISCARD EVIDENCE", gray
                    quarantined_preserved = (
                        meta.get("evidence_class") == "PRESERVED_V8_WORKBOOK_ROW"
                        and meta.get("preservation_display_status") != "PASS"
                    )
                    values = [
                        tested, f"{param}={value}",
                        None if quarantined_preserved else meta.get("delta_vs_bh"),
                        None if quarantined_preserved else meta.get("gain_per_mo"),
                        status,
                    ]
                else:
                    values, fill = ["", "", None, None, "PENDING"], gray
                for offset, value in enumerate(values):
                    ws_path.cell(row_index, start + offset, value)
                ws_path.cell(row_index, start + 4).fill = fill
        ws_path.freeze_panes = "F4"
        ws_path.sheet_view.showGridLines = False
        ws_path.row_dimensions[2].height = 75
        for i, width in enumerate((20, 12, 9, 24, 17), 1):
            ws_path.column_dimensions[get_column_letter(i)].width = width
        for start, _path in path_columns.values():
            for offset, width in enumerate((38, 34, 18, 14, 30)):
                ws_path.column_dimensions[get_column_letter(start + offset)].width = width
        return len(path_columns)

    entry_count = write_path_matrix("Entry Paths", "ENTRY")
    exit_count = write_path_matrix("Exit Paths", "EXIT")
    fleet_jobs, fleet_results = _path_fleet_evidence(base)
    fleet_sheet = wb.create_sheet("Path Fleet Results", 4)
    fleet_headers = [
        "path_id", "kind", "priority", "job_status", "description", "settings",
        "fixed_entry_control", "fixed_exit_control", "symbol", "side", "stage",
        "result_status", "strategy_return_%", "B&H_return_%",
        "strategy_x_B&H", "alpha_vs_B&H_pp", "same_entry_control_return_%",
        "alpha_vs_control_pp", "time_in_market_%", "trades", "untouched_OOS",
        "exact_replay", "future_HTF_count", "promotion_allowed",
        "matrix_written", "result_UTC", "artifact", "metric_scope",
        "return_unit", "return_aggregation", "TIM_unit", "TIM_aggregation",
        "trades_unit", "trades_aggregation",
    ]
    fleet_sheet.append(fleet_headers)
    for cell in fleet_sheet[1]:
        cell.fill, cell.font = navy, white_bold

    latest_results, seen_results = [], set()
    for result in fleet_results:
        identity = (
            result.get("path_id"), result.get("symbol"), result.get("side"),
            result.get("stage"), result.get("result_status"),
        )
        if identity in seen_results:
            continue
        seen_results.add(identity)
        latest_results.append(result)
    paths_with_results = {row.get("path_id") for row in latest_results}
    for job in fleet_jobs:
        if job.get("path_id") not in paths_with_results:
            latest_results.append({
                "path_id": job.get("path_id"), "kind": job.get("kind"),
                "priority": job.get("priority"), "job_status": job.get("status"),
                "registry": job.get("registry") or {},
            })

    for result in latest_results:
        registry_row = result.get("registry") or {}
        try:
            payload = json.loads(result.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        strategy = result.get("strategy_return_pct")
        bh = result.get("bh_return_pct")
        multiple = (
            float(strategy) / float(bh)
            if strategy is not None and bh is not None and float(bh) > 0
            else None
        )
        promotion_allowed = bool(
            payload.get("promotion_allowed")
            or (payload.get("manifest") or {}).get("promotion_allowed")
        )
        matrix_written = bool(
            payload.get("matrix_written")
            or (payload.get("manifest") or {}).get("matrix_written")
        )
        created = result.get("created_at")
        created_iso = (
            datetime.fromtimestamp(float(created), timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if created is not None else None
        )
        fleet_sheet.append([
            result.get("path_id"), result.get("kind"), result.get("priority"),
            result.get("job_status"),
            registry_row.get("description"),
            json.dumps(registry_row.get("settings") or {}, separators=(",", ":")),
            registry_row.get("fixed_entry_control"),
            registry_row.get("fixed_exit_control"),
            result.get("symbol"), result.get("side"), result.get("stage"),
            result.get("result_status"), strategy, bh, multiple,
            result.get("alpha_vs_bh_pp"),
            result.get("same_entry_control_return_pct"),
            result.get("alpha_vs_control_pp"), result.get("tim_pct"),
            result.get("trades"), bool(result.get("untouched_oos")),
            bool(result.get("exact_replay")), result.get("future_htf_count"),
            promotion_allowed, matrix_written, created_iso,
            result.get("artifact"),
            payload.get("metric_scope") or "LEGACY_UNSCOPED",
            payload.get("return_unit") or "LEGACY_UNSCOPED",
            payload.get("return_aggregation") or "LEGACY_UNSCOPED",
            payload.get("tim_unit") or "LEGACY_UNSCOPED",
            payload.get("tim_aggregation") or "LEGACY_UNSCOPED",
            payload.get("trades_unit") or "LEGACY_UNSCOPED",
            payload.get("trades_aggregation") or "LEGACY_UNSCOPED",
        ])
        row = fleet_sheet.max_row
        status = str(result.get("result_status") or result.get("job_status") or "")
        if (
            result.get("exact_replay") and promotion_allowed and matrix_written
            and status in {"ACCEPTED", "PROMOTION_ELIGIBLE"}
        ):
            fill = green
        elif status in {"CONTROL_FAILURE", "GRAY_REJECTED", "REJECTED"}:
            fill = gray
        elif (
            status.startswith("RED_")
            or status in {
                "QUARANTINED", "ERROR", "FAILED", "INERT",
                "WIRING_FAILURE",
            }
        ):
            fill = red
        elif result.get("strategy_return_pct") is not None:
            fill = amber
        else:
            fill = blue if status in {"SCREENED", "DELEGATED"} else gray
        fleet_sheet.cell(row, 12).fill = fill
    fleet_sheet.freeze_panes = "I2"
    fleet_sheet.auto_filter.ref = fleet_sheet.dimensions
    fleet_sheet.sheet_view.showGridLines = False
    for column in (5, 6, 7, 8, 27, 28, 29, 30, 31, 32, 33, 34):
        for cell in fleet_sheet[get_column_letter(column)]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    widths = (
        38, 9, 8, 20, 70, 54, 44, 44, 12, 9, 24, 24, 17, 15, 16, 18,
        24, 20, 18, 9, 14, 12, 18, 18, 14, 21, 70, 40, 32, 42, 18, 42,
        20, 42,
    )
    for column, width in enumerate(widths, 1):
        fleet_sheet.column_dimensions[get_column_letter(column)].width = width

    guide.append(("Restored sheet coverage",
                  f"PerSym keys={len(keys)}; Entry paths={entry_count}; Exit paths={exit_count}; "
                  f"Path-fleet jobs={len(fleet_jobs)}; latest stage/status rows={len(latest_results)}."))
    guide.append((
        "Path Fleet Results",
        "Separate vector/exact research ledger for the frozen top/bottom cohorts. "
        "It includes every logical path job, descriptions, parameter ranges, fixed "
        "entry/exit controls and latest per-stage/status rows. Superseded red wiring "
        "diagnostics remain visible beside later repaired gray results. Amber is research evidence, "
        "gray is retained rejection, and only an exact promotion-allowed/matrix-written "
        "row may be green. These rows never overwrite repaired ENGINE cells.",
    ))
    return {
        "keys": len(keys), "repaired_baselines": len(baselines),
        "repaired_cells": len(cells), "entry_paths": entry_count, "exit_paths": exit_count,
        "fleet_jobs": len(fleet_jobs), "fleet_rows": len(latest_results),
    }


def _preserve_matrix_top(wb, path):
    """Carry the hourly param_matrix.py Matrix_Top sheet across baseline refreshes.

    param_results_store and param_matrix are independent recurring exporters. The
    former used to recreate the workbook from scratch and silently delete the
    latter's useful 603-column per-symbol matrix until the next hourly run.
    Preserve its values deterministically; param_matrix.py remains responsible
    for refreshing the sheet's content.
    """
    path = Path(path)
    if not path.exists():
        return False
    from openpyxl import load_workbook
    try:
        old = load_workbook(path, read_only=True, data_only=False)
    except Exception:
        return False
    try:
        if "Matrix_Top" not in old.sheetnames:
            return False
        source = old["Matrix_Top"]
        if "Matrix_Top" in wb.sheetnames:
            del wb["Matrix_Top"]
        target = wb.create_sheet("Matrix_Top")
        for row in source.iter_rows(values_only=True):
            target.append(list(row))
        target.freeze_panes = getattr(source, "freeze_panes", None) or "D2"
        target.auto_filter.ref = target.dimensions
        return True
    finally:
        old.close()


def export_xlsx(con, path, mode="tradier", campaign=None):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Baselines"
    bcols = ("symbol", "side", "pool_sharpe", "trades", "acc_gain_pct", "gain_per_mo",
             "bh_pct", "bh_per_mo", "delta_gain_mo_vs_bh", "years", "window_start",
             "campaign", "ts", "stamp", "overrides_json")
    ws.append(list(bcols))
    q = "SELECT " + ",".join(bcols) + " FROM key_baseline WHERE mode=?"
    args = [mode]
    if campaign:
        q += " AND campaign=?"
        args.append(campaign)
    for row in con.execute(q + " ORDER BY delta_gain_mo_vs_bh", args):
        ws.append(list(row))
    ws2 = wb.create_sheet("ParamRanges")
    rcols = ("symbol", "side", "param", "n_values", "vmin", "vmax", "best_value",
             "best_delta_gain_mo", "worst_delta_gain_mo", "spread", "edge_flag",
             "suggested_next", "best_pool_sharpe", "best_delta_gain_mo_vs_bh")
    ws2.append(list(rcols))
    ranges = param_ranges(con, mode, campaign)
    for r in ranges:
        ws2.append([json.dumps(r[c]) if isinstance(r.get(c), list) else r.get(c) for c in rcols])
    ws3 = wb.create_sheet("Suggestions")
    ws3.append(["symbol", "side", "param", "edge_flag", "suggested_next"])
    for r in ranges:
        if r.get("suggested_next"):
            ws3.append([r["symbol"], r["side"], r["param"], r["edge_flag"], json.dumps(r["suggested_next"])])
    ws5 = wb.create_sheet("AllCells")
    ccols = ("symbol", "side", "param", "value_json", "pool_sharpe", "trades", "acc_gain_pct",
             "gain_per_mo", "delta_gain_mo_vs_bh", "delta_vs_baseline_gain_mo", "ts",
             "campaign", "source_file", "stamp", "overrides_json")
    ws5.append(list(ccols))
    q5 = "SELECT " + ",".join(ccols) + " FROM param_cells WHERE mode=?"
    args5 = [mode]
    if campaign:
        q5 += " AND campaign=?"
        args5.append(campaign)
    for row in con.execute(q5 + " ORDER BY symbol, side, param, value_num", args5):
        ws5.append(list(row))
    # The recurring exporter previously destroyed historical daemon evidence
    # whenever the local DB was sparse.  Re-append the hash-bound immutable V8
    # archive on every refresh.  This is a ledger view, not promotion credit.
    for (key, param, value), meta in sorted(_preserved_v8_evidence(BASE).items()):
        symbol, side = key.rsplit("_", 1)
        ws5.append([
            symbol, side, param, value, meta.get("pool_sharpe"),
            meta.get("trades"), None, meta.get("gain_per_mo"),
            meta.get("delta_vs_bh"), None, meta.get("ts"),
            meta.get("campaign"), meta.get("source_file"),
            "PRESERVED_V8_IMMUTABLE_REPLAY_REQUIRED", None,
        ])
    ws4 = wb.create_sheet("Relevance")
    ws4.append(["param", "max_spread_gain_mo", "n_keys_tested", "n_keys_moved", "verdict"])
    for d in relevance_ranking(con, mode, campaign):
        verdict = "INERT_CHECK_WIRING" if (d["n_keys"] >= 3 and d["max_spread"] < INERT_EPS) else (
            "RELEVANT" if d["n_keys_moved"] else "LOW")
        ws4.append([d["param"], round(d["max_spread"], 4), d["n_keys"], d["n_keys_moved"], verdict])
    # Durable restoration: these sheets used to live in separate one-off workbooks and
    # disappeared whenever this recurring exporter rebuilt PARAM_BASELINE_STOCKS. Generate
    # them here, on every refresh, while keeping all legacy sheets above untouched.
    restored = _write_restored_workbook_sheets(wb, con, mode, BASE)
    try:
        from tools.lifecycle_workbook import write_lifecycle_sheet
    except ModuleNotFoundError:
        from lifecycle_workbook import write_lifecycle_sheet  # type: ignore
    lifecycle_count = write_lifecycle_sheet(wb, BASE)
    matrix_top_preserved = _preserve_matrix_top(wb, path)
    wb["Workbook Guide"].append((
        "Legacy evidence counts",
        f"Baselines={ws.max_row - 1}; AllCells={ws5.max_row - 1}; "
        "kept in their original sheets, not blended into repaired result cells.",
    ))
    wb["Workbook Guide"].append((
        "Vector Lifecycle",
        f"{lifecycle_count} hash/provenance checked combination result(s), kept in a "
        "separate non-exact sheet and never blended into scalar/ENGINE cells.",
    ))
    wb["Workbook Guide"].append((
        "Matrix_Top",
        "Preserved from the most recent tools/param_matrix.py export."
        if matrix_top_preserved else
        "Not present in the previous workbook; tools/param_matrix.py export will create it.",
    ))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _set_local_immutable(path, False)
    # A full workbook can take more than a minute to serialize on a loaded S1.
    # Never expose that partial ZIP to the hourly puller or digest process.
    tmp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp.xlsx"
    )
    try:
        wb.save(str(tmp_path))
        with zipfile.ZipFile(tmp_path, "r") as archive:
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required.issubset(archive.namelist()):
                raise RuntimeError(
                    f"incomplete workbook archive: {tmp_path}"
                )
        try:
            from tools.audit_stock_matrix_workbooks import (
                assert_audit,
                audit_param_workbook,
            )
        except ModuleNotFoundError:
            from audit_stock_matrix_workbooks import (  # type: ignore
                assert_audit,
                audit_param_workbook,
            )
        report = audit_param_workbook(
            tmp_path,
            BASE,
            require_fresh=False,
            require_matrix_top=matrix_top_preserved,
        )
        assert_audit(report)
        os.replace(tmp_path, path)
        _set_local_immutable(path, True)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        _set_local_immutable(path, True)
    print(
        "[xlsx restored] "
        + " ".join(f"{key}={value}" for key, value in restored.items()),
        f"matrix_top_preserved={matrix_top_preserved}",
        flush=True,
    )
    return path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init", "ranges", "inert", "relevance", "xlsx"])
    ap.add_argument("--mode", default="tradier")
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--out", default=str(BASE / "data" / "reports" / "PARAM_BASELINE_STOCKS.xlsx"))
    a = ap.parse_args()
    con = connect()
    if a.cmd == "init":
        print(f"ok {DB_PATH}")
    elif a.cmd == "ranges":
        print(json.dumps(param_ranges(con, a.mode, a.campaign), indent=1)[:5000])
    elif a.cmd == "inert":
        print(json.dumps(inert_params(con, a.mode, a.campaign)))
    elif a.cmd == "relevance":
        print(json.dumps(relevance_ranking(con, a.mode, a.campaign)[:50], indent=1))
    elif a.cmd == "xlsx":
        print(export_xlsx(con, a.out, a.mode, a.campaign))
    con.commit()
    con.close()
