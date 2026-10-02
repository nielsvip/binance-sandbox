#!/usr/bin/env python3
"""per_sym_store — SQLite primary + JSON backup for per-symbol full config.

Every per_sym entry now stores the *full resolved config* (defaults snapshot
at promotion time  ~3300-3500 keys  +  override delta)  so that a later
TEMPLATE change cannot silently drift the live config.  About ~5000 keys
per sym_side when counting both venues is the operator unit (switches +
filter TFs + all venue fields).

Storage:
  SQLite (primary):  data/hourly_reconfig/per_sym_store.db
    table per_sym_active  (sym_side PK, …)
    table per_sym_history (id PK, sym_side, …) — ≥30d retention, compressed
    table kv_json         (key PK, value_json TEXT, updated_at, epoch) — generic
                          JSON view for cat_side_defaults_4, cat_side_promotions,
                          avg_delta_round_ledger, gain_pusher universes.
                          Lets cutover fail-soft: loaders try SQL first,
                          JSON fallback, so vanishing JSONs don't silently
                          fall back to global defaults.

  JSON (backup, always kept as generated views until cutover):
    per_sym_active_config.json (crypto), _stocks.json, trb/…, trc/…,
    cat_side_defaults_4.json, cat_side_promotions.json,
    avg_delta_round_ledger.json, gain_pusher/universe_*.json
  Writers dual-write: JSON file (view) + kv_json (primary).
  Readers SQL-primary → JSON fallback.

Usage:
  from per_sym_store import get as ps_get, get_full_config, upsert
  from per_sym_store import kv_get, kv_put  # generic JSONs

  # promotion (v15_pilot):
  per_sym_store.upsert(sym_side, overrides, defaults_snapshot, meta)

  # live (ez_manage / tradier_manage):
  val = per_sym_store.get_knob(symbol, side, knob, default)
  # or
  cfg = per_sym_store.get_full_config(sym_side)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PER_SYM_STORE_DB") or (ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"))
CRYPTO_JSON = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
STOCKS_JSON = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json"
TRB_JSON = ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json"
TRC_JSON = ROOT / "data" / "hourly_reconfig" / "trc" / "active_config.json"

CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")

_HISTORY_RETENTION_DAYS = 30
_HISTORY_PRUNE_AFTER_DAYS = 35  # keep ≥30d, prune >35d to avoid churn
_HISTORY_MAX_PER_SYM = 45  # safety cap without growing DB

# Generic KV keys for JSONs that must survive cutover (writers dual-write, loaders SQL-primary)
KV_CAT_SIDE_DEFAULTS_4 = "cat_side_defaults_4"
KV_CAT_SIDE_PROMOTIONS = "cat_side_promotions"
KV_AVG_DELTA_ROUND_LEDGER = "avg_delta_round_ledger"
KV_GAIN_PUSHER_PRIORITY = "gain_pusher/PRIORITY_SWITCHES"
# universe keys are per cat_side: f"gain_pusher/universe_{cat_side}"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS per_sym_active (
    sym_side            TEXT PRIMARY KEY,
    cat_side            TEXT,
    updated_at          TEXT,
    winning_tag         TEXT,
    wsharpe             REAL,
    pool_sharpe         REAL,
    trades              INTEGER,
    max_dd_pct          REAL,
    acc_gain_pct        REAL,
    gain_vs_bh          REAL,
    bh_pct              REAL,
    delta_365           REAL,
    delta_30d           REAL,
    gain_30d            REAL,
    baseline_30d        REAL,
    campaign_ts         REAL,
    overrides_json      TEXT,
    defaults_snapshot_json TEXT,
    full_config_json    TEXT,
    template_md5        TEXT,
    defaults_round      TEXT,
    meta_json           TEXT
);
CREATE TABLE IF NOT EXISTS per_sym_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sym_side            TEXT,
    updated_at          TEXT,
    epoch               REAL,
    cat_side            TEXT,
    template_md5        TEXT,
    defaults_round      TEXT,
    winning_tag         TEXT,
    gain_pct            REAL,
    baseline_gain       REAL,
    delta_pct           REAL,
    bh_pct              REAL,
    trades              INTEGER,
    pool_sharpe         REAL,
    max_dd_pct          REAL,
    tim_pct             REAL,
    valid               INTEGER,
    overrides_json      TEXT,
    full_config_json    TEXT,
    defaults_snapshot_json TEXT,
    meta_json           TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_sym ON per_sym_history(sym_side, updated_at);
CREATE TABLE IF NOT EXISTS kv_json (
    key                 TEXT PRIMARY KEY,
    value_json          TEXT,
    updated_at          TEXT,
    epoch               REAL
);
"""

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=5.0)
    try:
        con.executescript(_SCHEMA)
        # migrate existing history table: add missing columns for 30-day retention + results
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(per_sym_history)").fetchall()}
            _migrate = {
                "epoch": "REAL",
                "cat_side": "TEXT",
                "template_md5": "TEXT",
                "defaults_round": "TEXT",
                "winning_tag": "TEXT",
                "gain_pct": "REAL",
                "baseline_gain": "REAL",
                "delta_pct": "REAL",
                "bh_pct": "REAL",
                "trades": "INTEGER",
                "pool_sharpe": "REAL",
                "max_dd_pct": "REAL",
                "tim_pct": "REAL",
                "valid": "INTEGER",
                "overrides_json": "TEXT",
                "defaults_snapshot_json": "TEXT",
            }
            for col, typ in _migrate.items():
                if col not in cols:
                    con.execute(f"ALTER TABLE per_sym_history ADD COLUMN {col} {typ}")
            # ensure indexes exist
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_sym ON per_sym_history(sym_side, updated_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_epoch ON per_sym_history(epoch)")
        except Exception:
            pass
        try:
            con.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        con.commit()
    finally:
        con.close()


def _connect() -> sqlite3.Connection:
    _ensure_db()
    con = sqlite3.connect(str(DB_PATH), timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def _cat_side_of(symbol: str, side: str) -> str:
    s = str(side or "").upper()
    s = "LONG" if s.startswith("L") else "SHORT"
    cat = "CRYPTO" if str(symbol or "").upper().endswith(CRYPTO_SUFFIXES) else "STOCKS"
    return f"{cat}_{s}"


# ---------------------------------------------------------------------------
# Read path  (SQLite primary, JSON fallback)
# ---------------------------------------------------------------------------
def _maybe_decompress(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    # history full_config_json may be zlib+base64 compressed to keep DB small
    # compressed blobs start with 'z:'
    if text.startswith("z:"):
        try:
            import base64, zlib
            return zlib.decompress(base64.b64decode(text[2:].encode("ascii"))).decode("utf-8")
        except Exception:
            return text
    return text


def _maybe_compress(text: str) -> str:
    # compress only if it saves ≥15% (avoid overhead for tiny payloads)
    if not text or len(text) < 500:
        return text
    try:
        import base64, zlib
        comp = base64.b64encode(zlib.compress(text.encode("utf-8"), 6)).decode("ascii")
        if len(comp) + 2 < len(text) * 0.85:
            return "z:" + comp
    except Exception:
        pass
    return text


def _row_to_entry(row: sqlite3.Row) -> dict:
    try:
        overrides = json.loads(row["overrides_json"]) if row["overrides_json"] else {}
    except Exception:
        overrides = {}
    try:
        defaults_snapshot = json.loads(_maybe_decompress(row["defaults_snapshot_json"]) if row["defaults_snapshot_json"] else "") if row["defaults_snapshot_json"] else {}
    except Exception:
        defaults_snapshot = {}
    try:
        full_config = json.loads(_maybe_decompress(row["full_config_json"]) if row["full_config_json"] else "") if row["full_config_json"] else {}
    except Exception:
        full_config = {}
    try:
        meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    except Exception:
        meta = {}
    base = dict(meta) if meta else {}
    # merge identifying fields
    base.update(
        {
            "overrides": overrides,
            "defaults_snapshot": defaults_snapshot,
            "full_config": full_config,
            "cat_side": row["cat_side"],
            "updated_at": row["updated_at"],
            "template_md5": row["template_md5"],
            "defaults_round": row["defaults_round"],
            "_source": "sqlite",
        }
    )
    # keep top-level metrics for is_live gate compat
    for k in ("winning_tag", "wsharpe", "pool_sharpe", "trades", "max_dd_pct", "acc_gain_pct", "gain_vs_bh", "bh_pct", "delta_365", "delta_30d", "gain_30d", "baseline_30d", "campaign_ts"):
        if row[k] is not None:
            base.setdefault(k, row[k])
            # also provide legacy aliases
            if k == "acc_gain_pct":
                base.setdefault("gain_pct", row[k])
                base.setdefault("total_gain_pct", row[k])
            if k == "wsharpe":
                base.setdefault("pool_sharpe", row[k])
    return base


def _json_load_fallback(sym_side: str) -> Optional[dict]:
    """Try each JSON file for the sym_side. Returns raw entry or None."""
    # choose file set based on suffix heuristic, but try all
    for p in (CRYPTO_JSON, STOCKS_JSON, TRB_JSON, TRC_JSON):
        try:
            if not p.exists():
                continue
            raw = json.loads(p.read_text())
            if sym_side in raw and isinstance(raw[sym_side], dict):
                e = dict(raw[sym_side])
                e["_source"] = f"json:{p.name}"
                # ensure full_config present: fallback to overrides if missing
                if "full_config" not in e and "overrides" in e:
                    # legacy: full_config not stored, caller will merge with cat_side defaults
                    pass
                return e
        except Exception:
            continue
    return None


def get(sym_side: str) -> Optional[dict]:
    """Return the per_sym entry for sym_side. SQLite primary, JSON fallback.
    When PER_SYM_STORE_SQLITE_DISABLED=1, always use JSON (parity audit)."""
    sym_side = str(sym_side).strip()
    if os.environ.get("PER_SYM_STORE_SQLITE_DISABLED") != "1":
        try:
            con = _connect()
            try:
                cur = con.execute("SELECT * FROM per_sym_active WHERE sym_side=?", (sym_side,))
                row = cur.fetchone()
                if row is not None:
                    e = _row_to_entry(row)
                    # if full_config is empty but json has it, prefer json
                    if not e.get("full_config"):
                        jf = _json_load_fallback(sym_side)
                        if jf and jf.get("full_config"):
                            return jf
                    return e
            finally:
                con.close()
        except Exception:
            pass
    return _json_load_fallback(sym_side)


def get_full_config(sym_side: str) -> Optional[dict]:
    """Return the full resolved config dict for sym_side, or None."""
    e = get(sym_side)
    if e is None:
        return None
    fc = e.get("full_config")
    if isinstance(fc, dict) and fc:
        return dict(fc)
    # fallback: try to reconstruct from overrides + cat_side defaults
    overrides = e.get("overrides") or {}
    if not overrides:
        return None
    # try cat_side defaults reconstruction
    try:
        import cat_side_defaults as _csd
        cat = e.get("cat_side") or _cat_side_of(sym_side.rsplit("_", 1)[0], sym_side.rsplit("_", 1)[-1])
        base = _csd.defaults(cat)
        merged = dict(base)
        merged.update(overrides)
        return merged
    except Exception:
        return dict(overrides) if overrides else None


def get_overrides(sym_side: str) -> Optional[dict]:
    e = get(sym_side)
    if e is None:
        return None
    return dict(e.get("overrides") or {})


def get_knob(symbol: str, side: str, knob: str, default=None, venue: str = None):
    """Resolved knob value: full_config (sqlite/json) > cat_side default > default.

    Does NOT consult config.Config / TradierConfig — the caller (_psym_get/_cfg)
    still does that fallback after this.  This helper only resolves the per_sym
    layer via the store.
    """
    sym_side = f"{symbol}_{side}"
    fc = get_full_config(sym_side)
    if fc is not None and knob in fc:
        return fc[knob]
    # also try overrides directly (covers legacy rows without full_config)
    e = get(sym_side)
    if e is not None and isinstance(e.get("overrides"), dict) and knob in e["overrides"]:
        return e["overrides"][knob]
    # cat_side defaults fallback is handled by the caller; we still try for
    # callers that want one-stop lookup (use venue hint if given)
    try:
        import cat_side_defaults as _csd
        v = _csd.get_for(knob, symbol, side, None, venue=venue)
        if v is not None:
            return v
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# Write path  (upsert SQLite + JSON backup)
# ---------------------------------------------------------------------------
def _template_md5_for(cat_side: str) -> str:
    try:
        name_map = {
            "CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx",
            "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx",
            "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx",
            "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx",
        }
        p = ROOT / "SPREADSHEETS" / name_map.get(cat_side, "TEMPLATE_CRYPTO_LONG.xlsx")
        if p.exists():
            return hashlib.md5(p.read_bytes()).hexdigest()
    except Exception:
        pass
    return ""


def _prune_history(con: sqlite3.Connection) -> int:
    """Keep ≥30d of history; prune >35d and cap 45 per sym_side. Returns deleted count."""
    try:
        now = time.time()
        cutoff = now - _HISTORY_PRUNE_AFTER_DAYS * 86400
        # 1) time-based: delete anything older than 35d
        cur = con.execute("DELETE FROM per_sym_history WHERE epoch IS NOT NULL AND epoch < ?", (cutoff,))
        n_time = cur.rowcount or 0
        # 2) count-based safety cap: keep newest 45 per sym_side
        for (sym,) in con.execute("SELECT DISTINCT sym_side FROM per_sym_history").fetchall():
            cnt = con.execute("SELECT COUNT(*) FROM per_sym_history WHERE sym_side=?", (sym,)).fetchone()[0]
            if cnt > _HISTORY_MAX_PER_SYM:
                # delete oldest beyond cap, but never delete anything within 30d
                keep_cutoff = now - _HISTORY_RETENTION_DAYS * 86400
                con.execute(
                    """
                    DELETE FROM per_sym_history WHERE id IN (
                        SELECT id FROM per_sym_history
                        WHERE sym_side=? AND (epoch IS NULL OR epoch < ?)
                        ORDER BY epoch ASC, id ASC
                        LIMIT ?
                    )
                    """,
                    (sym, keep_cutoff, cnt - _HISTORY_MAX_PER_SYM),
                )
        con.commit()
        return n_time
    except Exception:
        return 0


def get_history(sym_side: str, days: int = 30, limit: int = 60) -> list[dict]:
    """Return history entries for sym_side within last *days* (≥30d guaranteed on disk)."""
    sym_side = str(sym_side).strip()
    try:
        con = _connect()
        try:
            cutoff = time.time() - max(days, _HISTORY_RETENTION_DAYS) * 86400
            rows = con.execute(
                "SELECT * FROM per_sym_history WHERE sym_side=? AND (epoch IS NULL OR epoch >= ?) ORDER BY epoch DESC, id DESC LIMIT ?",
                (sym_side, cutoff, limit),
            ).fetchall()
            out = []
            for r in rows:
                try:
                    d = dict(r)
                    # decompress payloads
                    for k in ("full_config_json", "defaults_snapshot_json"):
                        if d.get(k):
                            d[k] = _maybe_decompress(d[k])
                    # parse compact results for caller
                    try:
                        d["overrides"] = json.loads(d["overrides_json"]) if d.get("overrides_json") else {}
                    except Exception:
                        d["overrides"] = {}
                    try:
                        d["full_config"] = json.loads(d["full_config_json"]) if d.get("full_config_json") else {}
                    except Exception:
                        d["full_config"] = {}
                    out.append(d)
                except Exception:
                    continue
            return out
        finally:
            con.close()
    except Exception:
        return []


def upsert(
    sym_side: str,
    overrides: dict,
    defaults_snapshot: dict = None,
    full_config: dict = None,
    meta: dict = None,
    template_md5: str = None,
    defaults_round: str = None,
    json_path: Path = None,
) -> dict:
    """Upsert a per_sym entry.

    *defaults_snapshot* should be the full defaults at promotion time (the
    ~3300-3500 keys for that cat_side).  If omitted it is taken from
    cat_side_defaults.defaults(cat_side).  *full_config* is defaults merged
    with overrides; if omitted it is computed.

    Dynamic switches/filters: any key present in defaults_snapshot or overrides
    is stored verbatim as JSON — new switches added to TEMPLATE appear
    automatically, removed switches stay in historical snapshots for audit but
    are ignored by live readers (no schema migration needed).

    History: every upsert appends a compressed row to per_sym_history with
    compact results (gain/trades/sharpe/dd/tim/valid/delta) so the 30-day
    adjustment trail is queryable without parsing 117KB blobs. History is
    pruned to keep ≥30d (delete >35d) and ≤45 rows/sym_side, so DB growth
    stays bounded (~501*30 compressed rows vs 501 current).

    Writes to SQLite (primary) and to the appropriate JSON file (backup).
    Returns the entry dict that was persisted.
    """
    sym_side = str(sym_side).strip()
    parts = sym_side.rsplit("_", 1)
    symbol = parts[0] if len(parts) == 2 else sym_side
    side = parts[1] if len(parts) == 2 else "LONG"
    cat_side = _cat_side_of(symbol, side)
    # defaults snapshot
    if defaults_snapshot is None:
        try:
            import cat_side_defaults as _csd
            defaults_snapshot = _csd.defaults(cat_side)
        except Exception:
            defaults_snapshot = {}
    else:
        defaults_snapshot = dict(defaults_snapshot)
    overrides = dict(overrides or {})
    if full_config is None:
        full_config = dict(defaults_snapshot)
        full_config.update(overrides)
    else:
        full_config = dict(full_config)
    meta = dict(meta or {})
    # ensure required meta fields
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # template md5 / round
    if template_md5 is None:
        template_md5 = meta.get("template_md5") or _template_md5_for(cat_side)
    if defaults_round is None:
        defaults_round = meta.get("defaults_round") or os.environ.get("V15_DEFAULTS_ROUND") or ""
    # derive metrics if not in meta
    # keep meta flat for DB columns too
    def _f(v, d=0):
        try:
            return float(v)
        except Exception:
            return d

    # DB columns
    wsharpe = meta.get("wsharpe", meta.get("pool_sharpe"))
    pool_sharpe = meta.get("pool_sharpe", wsharpe)
    trades = meta.get("trades")
    max_dd = meta.get("max_dd_pct")
    acc_gain = meta.get("acc_gain_pct", meta.get("gain_pct", meta.get("gain_30d")))
    gain_vs_bh = meta.get("gain_vs_bh")
    bh_pct = meta.get("bh_pct")
    delta_365 = meta.get("delta_365")
    delta_30d = meta.get("delta_30d")
    gain_30d = meta.get("gain_30d", acc_gain)
    baseline_30d = meta.get("baseline_30d", meta.get("baseline_gain"))
    campaign_ts = meta.get("campaign_ts", time.time())
    winning_tag = str(meta.get("winning_tag", meta.get("vec_baseline_tag", "")) or "")

    # Write SQLite
    _ensure_db()
    con = _connect()
    try:
        con.execute(
            """
            INSERT INTO per_sym_active
                (sym_side, cat_side, updated_at, winning_tag, wsharpe, pool_sharpe,
                 trades, max_dd_pct, acc_gain_pct, gain_vs_bh, bh_pct, delta_365,
                 delta_30d, gain_30d, baseline_30d, campaign_ts,
                 overrides_json, defaults_snapshot_json, full_config_json,
                 template_md5, defaults_round, meta_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sym_side) DO UPDATE SET
                cat_side=excluded.cat_side,
                updated_at=excluded.updated_at,
                winning_tag=excluded.winning_tag,
                wsharpe=excluded.wsharpe,
                pool_sharpe=excluded.pool_sharpe,
                trades=excluded.trades,
                max_dd_pct=excluded.max_dd_pct,
                acc_gain_pct=excluded.acc_gain_pct,
                gain_vs_bh=excluded.gain_vs_bh,
                bh_pct=excluded.bh_pct,
                delta_365=excluded.delta_365,
                delta_30d=excluded.delta_30d,
                gain_30d=excluded.gain_30d,
                baseline_30d=excluded.baseline_30d,
                campaign_ts=excluded.campaign_ts,
                overrides_json=excluded.overrides_json,
                defaults_snapshot_json=excluded.defaults_snapshot_json,
                full_config_json=excluded.full_config_json,
                template_md5=excluded.template_md5,
                defaults_round=excluded.defaults_round,
                meta_json=excluded.meta_json
            """,
            (
                sym_side,
                cat_side,
                now_iso,
                winning_tag,
                float(wsharpe) if wsharpe is not None else None,
                float(pool_sharpe) if pool_sharpe is not None else None,
                int(trades) if trades is not None else None,
                float(max_dd) if max_dd is not None else None,
                float(acc_gain) if acc_gain is not None else None,
                float(gain_vs_bh) if gain_vs_bh is not None else None,
                float(bh_pct) if bh_pct is not None else None,
                float(delta_365) if delta_365 is not None else None,
                float(delta_30d) if delta_30d is not None else None,
                float(gain_30d) if gain_30d is not None else None,
                float(baseline_30d) if baseline_30d is not None else None,
                float(campaign_ts) if campaign_ts is not None else time.time(),
                json.dumps(overrides, sort_keys=True),
                json.dumps(defaults_snapshot, sort_keys=True),
                json.dumps(full_config, sort_keys=True),
                str(template_md5 or ""),
                str(defaults_round or ""),
                json.dumps(meta, sort_keys=True, default=str),
            ),
        )
        con.commit()
        # history — compact results + compressed full/snapshot so 30d trail stays small
        try:
            now_epoch = time.time()
            # compact result metrics extracted from meta/vec (no need to parse 117KB)
            gain_pct = meta.get("gain_pct", meta.get("acc_gain_pct", meta.get("gain_30d")))
            baseline_gain = meta.get("baseline_gain", meta.get("baseline_30d"))
            delta_pct = None
            try:
                if gain_pct is not None and baseline_gain is not None:
                    delta_pct = float(gain_pct) - float(baseline_gain)
                elif meta.get("delta_30d") is not None:
                    delta_pct = float(meta.get("delta_30d"))
                elif meta.get("delta_pct") is not None:
                    delta_pct = float(meta.get("delta_pct"))
            except Exception:
                pass
            bh = meta.get("bh_pct")
            tim = meta.get("tim_pct", meta.get("tim"))
            valid_int = 1 if meta.get("valid") else (1 if (trades or 0) >= 10 and (gain_pct if gain_pct is not None else -999) >= -5 else 0)
            # compress heavy JSON blobs for history (per_sym_active keeps uncompressed for fast live reads)
            full_json = _maybe_compress(json.dumps(full_config, sort_keys=True))
            snap_json = _maybe_compress(json.dumps(defaults_snapshot, sort_keys=True))
            ov_json = json.dumps(overrides, sort_keys=True)
            con.execute(
                """
                INSERT INTO per_sym_history
                    (sym_side, updated_at, epoch, cat_side, template_md5, defaults_round,
                     winning_tag, gain_pct, baseline_gain, delta_pct, bh_pct, trades,
                     pool_sharpe, max_dd_pct, tim_pct, valid,
                     overrides_json, full_config_json, defaults_snapshot_json, meta_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sym_side,
                    now_iso,
                    now_epoch,
                    cat_side,
                    str(template_md5 or ""),
                    str(defaults_round or ""),
                    winning_tag,
                    float(gain_pct) if gain_pct is not None else None,
                    float(baseline_gain) if baseline_gain is not None else None,
                    float(delta_pct) if delta_pct is not None else None,
                    float(bh) if bh is not None else None,
                    int(trades) if trades is not None else None,
                    float(pool_sharpe) if pool_sharpe is not None else None,
                    float(max_dd) if max_dd is not None else None,
                    float(tim) if tim is not None else None,
                    int(valid_int),
                    ov_json,
                    full_json,
                    snap_json,
                    json.dumps(meta, sort_keys=True, default=str),
                ),
            )
            con.commit()
            # retain ≥30d, prune >35d and cap 45/sym
            _prune_history(con)
        except Exception as _he:
            try:
                print(f"[per_sym_store] history write failed {sym_side}: {_he}", flush=True)
            except Exception:
                pass
    finally:
        con.close()

    # Write JSON backup (preserve existing _meta)
    # Caller may pass explicit json_path (e.g. trb/active_config.json for stocks)
    if json_path is None:
        sym_for_check = sym_side.rsplit("_", 1)[0] if "_" in sym_side else sym_side
        is_crypto = sym_for_check.upper().endswith(CRYPTO_SUFFIXES)
        json_path = CRYPTO_JSON if is_crypto else STOCKS_JSON
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(json_path.read_text()) if json_path.exists() else {}
        except Exception:
            raw = {}
        _meta = raw.pop("_meta", None) if isinstance(raw, dict) else None
        entry = dict(meta)
        entry["overrides"] = overrides
        # NEW: store snapshot + full for backup parity
        entry["defaults_snapshot"] = defaults_snapshot
        entry["full_config"] = full_config
        entry["template_md5"] = str(template_md5 or "")
        entry["defaults_round"] = str(defaults_round or "")
        entry.setdefault("updated_at", now_iso)
        entry.setdefault("cat_side", cat_side)
        # ensure metric aliases for legacy readers that still read wsharpe/pool_sharpe/gain_pct
        if "wsharpe" not in entry and pool_sharpe is not None:
            entry["wsharpe"] = float(pool_sharpe)
        if "pool_sharpe" not in entry and wsharpe is not None:
            entry["pool_sharpe"] = float(wsharpe)
        raw[sym_side] = entry
        if _meta is not None:
            raw["_meta"] = _meta
        tmp = json_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2, default=str))
        tmp.replace(json_path)
    except Exception as _e:
        # DB is primary; json failure is logged but not fatal
        try:
            print(f"[per_sym_store] json backup write {json_path.name} failed: {_e}", flush=True)
        except Exception:
            pass

    return {"sym_side": sym_side, "full_config": full_config, "overrides": overrides, "defaults_snapshot": defaults_snapshot, "meta": meta}


def count() -> int:
    try:
        con = _connect()
        try:
            cur = con.execute("SELECT COUNT(*) AS c FROM per_sym_active")
            return int(cur.fetchone()["c"])
        finally:
            con.close()
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Generic KV store — mirrors JSON files that must survive cutover
# ---------------------------------------------------------------------------
def kv_put(key: str, value: Any, compress: bool = False) -> None:
    """Dual-write helper: store JSON-serialisable *value* under *key*.
    Called by build_cat_side_defaults_4 / v15_daily_template_update / pusher
    right after they write the JSON file (JSON stays as generated view).
    """
    try:
        _ensure_db()
        con = _connect()
        try:
            js = json.dumps(value, sort_keys=True, default=str)
            if compress:
                js = _maybe_compress(js)
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            con.execute(
                "INSERT INTO kv_json(key, value_json, updated_at, epoch) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at, epoch=excluded.epoch",
                (str(key), js, now_iso, time.time()),
            )
            con.commit()
        finally:
            con.close()
    except Exception as _e:
        try:
            print(f"[per_sym_store] kv_put {key} failed: {_e}", flush=True)
        except Exception:
            pass


def kv_get(key: str) -> Optional[Any]:
    """SQL-primary read for a KV JSON. Returns parsed value or None.
    Respects PER_SYM_STORE_SQLITE_DISABLED=1 (audit mode) → always None so caller falls back to file.
    """
    if os.environ.get("PER_SYM_STORE_SQLITE_DISABLED") == "1":
        return None
    try:
        con = _connect()
        try:
            row = con.execute("SELECT value_json FROM kv_json WHERE key=?", (str(key),)).fetchone()
            if row is None or row["value_json"] is None:
                return None
            raw = _maybe_decompress(row["value_json"])
            return json.loads(raw) if raw else None
        finally:
            con.close()
    except Exception:
        return None


def kv_health() -> dict:
    try:
        con = _connect()
        try:
            n = int(con.execute("SELECT COUNT(*) FROM kv_json").fetchone()[0])
            keys = [r[0] for r in con.execute("SELECT key FROM kv_json ORDER BY key").fetchall()]
            return {"kv_n": n, "kv_keys": keys}
        finally:
            con.close()
    except Exception:
        return {"kv_n": -1, "kv_keys": []}


def get_cat_side_promotions() -> Optional[dict]:
    """SQL-primary → JSON fallback for cat_side_promotions.json. Prevents silent loss of 172 promotions."""
    v = kv_get(KV_CAT_SIDE_PROMOTIONS)
    if isinstance(v, dict):
        return v
    try:
        p = ROOT / "data" / "cat_side_promotions.json"
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def get_avg_delta_round_ledger() -> Optional[dict]:
    """SQL-primary → JSON fallback for avg_delta_round_ledger.json."""
    v = kv_get(KV_AVG_DELTA_ROUND_LEDGER)
    if isinstance(v, dict):
        return v
    try:
        p = ROOT / "data" / "avg_delta_round_ledger.json"
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def health() -> dict:
    db_exists = DB_PATH.exists()
    db_count = count() if db_exists else 0
    try:
        crypto_n = len([k for k in json.loads(CRYPTO_JSON.read_text()).keys() if not k.startswith("_")]) if CRYPTO_JSON.exists() else 0
    except Exception:
        crypto_n = -1
    try:
        stocks_n = len([k for k in json.loads(STOCKS_JSON.read_text()).keys() if not k.startswith("_")]) if STOCKS_JSON.exists() else 0
    except Exception:
        stocks_n = -1
    # history retention stats (30d guarantee)
    hist_n = hist_30d_n = hist_oldest_days = -1
    try:
        if db_exists:
            con = _connect()
            try:
                hist_n = int(con.execute("SELECT COUNT(*) FROM per_sym_history").fetchone()[0])
                cutoff = time.time() - _HISTORY_RETENTION_DAYS * 86400
                hist_30d_n = int(con.execute("SELECT COUNT(*) FROM per_sym_history WHERE epoch >= ?", (cutoff,)).fetchone()[0])
                row = con.execute("SELECT MIN(epoch) AS mn FROM per_sym_history").fetchone()
                mn = row["mn"] if row and row["mn"] is not None else None
                hist_oldest_days = round((time.time() - float(mn)) / 86400, 1) if mn else 0
            finally:
                con.close()
    except Exception:
        pass
    kh = kv_health()
    out = {
        "db_path": str(DB_PATH),
        "db_exists": db_exists,
        "db_count": db_count,
        "json_crypto_n": crypto_n,
        "json_stocks_n": stocks_n,
        "history_n": hist_n,
        "history_30d_n": hist_30d_n,
        "history_oldest_days": hist_oldest_days,
        "history_retention_days": _HISTORY_RETENTION_DAYS,
    }
    out.update(kh)
    return out
