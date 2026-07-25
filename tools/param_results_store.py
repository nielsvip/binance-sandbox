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
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "param_results_stocks.db"
CENTRAL_DB = BASE / "data" / "test_results_central.db"
INERT_EPS = 0.05  # gain/mo pp below which a delta is noise


def trades_fingerprint(trades):
    # USER 2026-07-21: "If 2 fields produce the same results for different settings the
    # function is broken." A fingerprint over the realised trade list makes that mechanical:
    # identical fingerprint to the same-tier baseline => the override changed NOTHING, so the
    # knob is unconsumed (RECONNECT), and its delta MUST be reported as 0, never as a gain.
    h = hashlib.md5()
    for t in sorted(trades, key=lambda x: (x.get("entry_ts") or 0, x.get("exit_ts") or 0)):
        h.update(f"{t.get('entry_ts')}|{t.get('exit_ts')}|{round(float(t.get('pnl_pct') or 0.0), 6)};".encode())
    return f"{len(trades)}:{h.hexdigest()[:16]}"

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
            "reentry_pending", "reentry_violations", "result_audit_json")
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
            "reentry_pending", "reentry_violations", "result_audit_json")
    busy_retry(lambda: con.execute(
        f"INSERT OR IGNORE INTO param_cells ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [row.get(c) for c in cols]))
    # TIER UPGRADE (2026-07-21): a Tier-1 vec SCREEN cell must never keep a slot that the
    # faithful Tier-2 engine has since measured. Engine overwrites VEC/LAB; never the reverse.
    if str(row.get("tier", "")).upper() == "ENGINE":
        busy_retry(lambda: con.execute(
            f"UPDATE param_cells SET {','.join(f'{c}=?' for c in cols)} "
            "WHERE mode=? AND symbol=? AND side=? AND campaign=? AND param=? AND value_json=? "
            "AND (tier IS NULL OR tier<>'ENGINE')",
            [row.get(c) for c in cols]
            + [row.get(k) for k in ("mode", "symbol", "side", "campaign", "param", "value_json")]))
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
    ws4 = wb.create_sheet("Relevance")
    ws4.append(["param", "max_spread_gain_mo", "n_keys_tested", "n_keys_moved", "verdict"])
    for d in relevance_ranking(con, mode, campaign):
        verdict = "INERT_CHECK_WIRING" if (d["n_keys"] >= 3 and d["max_spread"] < INERT_EPS) else (
            "RELEVANT" if d["n_keys_moved"] else "LOW")
        ws4.append([d["param"], round(d["max_spread"], 4), d["n_keys"], d["n_keys_moved"], verdict])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
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
