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
    per_sym_settings.json, cat_side_promotions.json,
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
KV_CAT_SIDE_DEFAULTS_4 = "per_sym_settings"  # renamed 2026-10-08: SQL primary, data/per_sym_settings.json is the identical backup
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
# 2026-10-07 hourly triage (tradier 14:15Z): every get()/kv_get() ran _ensure_db (connect +
# executescript + PRAGMA/ALTER/INDEX + WAL + commit + close) then a 2nd connect + SELECT * +
# json.loads of ~2x123KB blobs + close, synchronously on the live asyncio thread, hundreds of
# times per symbol via tradier _cfg / cat_side_defaults._load. macOS `sample` of trb pid 59940
# showed ~75% of main-thread time in sqlite connect/close/parse -> event-loop starvation ->
# every order ORDER_STALL_SKIP (0 broker orders 13:30-14:15Z). Fix = memoize schema setup per
# DB file + cache parsed reads keyed on the db/-wal file signature (any writer, any process,
# bumps -wal or db mtime_ns/size -> cache dropped). Values returned are identical; copies are
# handed out so callers cannot mutate the cache.
_ENSURED_DB: set = set()
_READ_CACHE: dict = {"sig": None, "get": {}, "kv": {}}
_READ_MISS = object()


def _db_signature():
    sig = [str(DB_PATH)]
    for p in (DB_PATH, Path(str(DB_PATH) + "-wal")):
        try:
            st = os.stat(p)
            sig.extend((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.extend((0, 0, 0))
    return tuple(sig)


def _read_cache_bucket(name: str) -> dict:
    sig = _db_signature()
    if _READ_CACHE["sig"] != sig:
        _READ_CACHE["sig"] = sig
        _READ_CACHE["get"] = {}
        _READ_CACHE["kv"] = {}
    return _READ_CACHE[name]


def _invalidate_read_cache() -> None:
    _READ_CACHE["sig"] = None
    _READ_CACHE["get"] = {}
    _READ_CACHE["kv"] = {}


def _copy2(v):
    if isinstance(v, dict):
        return {k: (dict(x) if isinstance(x, dict) else list(x) if isinstance(x, list) else x) for k, x in v.items()}
    if isinstance(v, list):
        return [(dict(x) if isinstance(x, dict) else list(x) if isinstance(x, list) else x) for x in v]
    return v


def _ensure_db() -> None:
    try:
        _ens_key = (str(DB_PATH), os.stat(DB_PATH).st_ino)
    except OSError:
        _ens_key = None
    if _ens_key is not None and _ens_key in _ENSURED_DB:
        return
    _ensure_db_impl()
    try:
        _ENSURED_DB.add((str(DB_PATH), os.stat(DB_PATH).st_ino))
    except OSError:
        pass


def _ensure_db_impl() -> None:
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


_JSON_FALLBACK_CACHE: dict = {}


def _json_fallback_entry(p: Path, sym_side: str):
    # 2026-10-06 triage: per-file cache keyed on (mtime_ns, size, ino). Uncached, every call re-parsed ~140MB
    # (crypto+stocks per_sym JSON) synchronously -> 1.5-2.4s per _psym_get for sym_sides absent from SQLite,
    # stalling the ez_manage event loop for minutes (men stalls, ang PAU_TIMEOUT exit 42). Keeps only the key
    # set + entries actually requested (not the whole 70MB dict) to bound RSS. Same data, same result.
    # 2026-10-13 STORM GUARD: one parse per file-signature, serving the whole
    # working set from it. Previously EVERY not-yet-cached sym_side under a
    # fresh sig re-parsed the entire 58-109MB file; the v15 chain rewrites
    # these files ~1/min, so the hot live _cfg path did hundreds of 100MB
    # parses per cycle and froze the tradier event loops for minutes (zero
    # fills, BROKER_UNREACHABLE_ORDERS, 120s+ ORDER_STALL_SKIP — proven via
    # live faulthandler stack: process_position -> ... -> json.loads). Same
    # data, same freshness (any sig change re-reads); only redundant parses
    # are gone. Working set stays bounded to requested sym_sides (RSS).
    try:
        st = p.stat()
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    key = str(p)
    hit = _JSON_FALLBACK_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        if sym_side in hit[2]:
            return hit[2][sym_side]
        if hit[1] is not None and sym_side not in hit[1]:
            _absent_mark(key, (sym_side,))
            return None
    else:
        hit = None
        if _absent_known(key, sym_side):
            return None
    raw = json.loads(p.read_text())
    keys = frozenset(raw.keys()) if isinstance(raw, dict) else frozenset()
    want = set(hit[2].keys()) if hit else set()
    want.add(sym_side)
    # 2026-10-14: keep EVERY entry of the parsed file (was: only the requested working set) — each new sym_side re-parsed the whole
    # JSON inside live tradier_manage's event loop (frozen 10-179 s, exits never reached the broker).
    entries: dict = dict(raw) if isinstance(raw, dict) else {}
    _JSON_FALLBACK_CACHE[key] = (sig, keys, entries)
    if sym_side not in entries:
        _absent_mark(key, (sym_side,))
    return entries.get(sym_side)


def _prime_one_file(p: Path, want: set) -> int:
    """Parse p ONCE if its signature changed; keep entries for want + the
    previously cached working set. Returns 1 if parsed, else 0."""
    try:
        st = p.stat()
    except OSError:
        return 0
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    key = str(p)
    hit = _JSON_FALLBACK_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return 0
    try:
        raw = json.loads(p.read_text())
    except Exception:
        return 0
    keys = frozenset(raw.keys()) if isinstance(raw, dict) else frozenset()
    keep = set(hit[2].keys()) if hit else set()
    keep |= set(want or ())
    entries: dict = {}
    if isinstance(raw, dict):
        for _k in keep:
            if _k in raw:
                entries[_k] = raw[_k]
    _JSON_FALLBACK_CACHE[key] = (sig, keys, entries)
    _absent_mark(key, (set(keep) - set(entries.keys())))
    return 1


def prime_json_working_set(sym_sides) -> int:
    """Live hot-path primer: parse each per_sym JSON view at most ONCE per
    file revision and pre-extract entries for sym_sides (e.g. a scan
    universe). Without this, every uncached sym_side under a fresh revision
    re-parsed the whole 58-109MB file (~1/min rewrites) and froze the live
    loops. Returns number of files (re)parsed."""
    try:
        want = {str(s).strip() for s in (sym_sides or ()) if str(s).strip()}
    except Exception:
        return 0
    if not want:
        return 0
    _paths = (CRYPTO_JSON, STOCKS_JSON, TRB_JSON, TRC_JSON)
    if not any(str(_s).upper().rsplit("_", 1)[0].endswith(CRYPTO_SUFFIXES) for _s in want):
        _paths = (STOCKS_JSON, TRB_JSON, TRC_JSON)
    done = 0
    for _p in _paths:
        try:
            if _p.exists():
                done += _prime_one_file(_p, want)
        except Exception:
            continue
    return done


def _json_file_order(sym_side: str):
    """Venue-aware view order: crypto syms hit the crypto view first, stocks
    syms hit stocks/trb/trc first (CRYPTO_JSON last). The old fixed order
    parsed the 96MB crypto view on EVERY stocks miss before reaching the
    109MB stocks view — ~200MB per miss in the live hot path."""
    _s = str(sym_side or "").upper()
    _base = _s.rsplit("_", 1)[0] if "_" in _s else _s
    if _base.endswith(CRYPTO_SUFFIXES):
        return (CRYPTO_JSON, STOCKS_JSON, TRB_JSON, TRC_JSON)
    return (STOCKS_JSON, TRB_JSON, TRC_JSON, CRYPTO_JSON)


_ABSENT_TTL_S = 300.0
_JSON_ABSENT_CACHE: dict = {}


def _absent_known(path_key: str, sym_side: str) -> bool:
    try:
        _ts = _JSON_ABSENT_CACHE.get(path_key, {}).get(sym_side)
        return _ts is not None and (time.time() - float(_ts)) < _ABSENT_TTL_S
    except Exception:
        return False


def _absent_mark(path_key: str, syms) -> None:
    try:
        _now = time.time()
        _d = _JSON_ABSENT_CACHE.get(path_key)
        if not isinstance(_d, dict):
            _d = _JSON_ABSENT_CACHE[path_key] = {}
        for _s in syms or ():
            _d[str(_s)] = _now
        if len(_d) > 5000:
            _cut = _now - _ABSENT_TTL_S
            for _k in [k for k, v in _d.items() if float(v) < _cut]:
                _d.pop(_k, None)
    except Exception:
        pass


def _json_load_fallback(sym_side: str) -> Optional[dict]:
    """Try each JSON file for the sym_side. Returns raw entry or None."""
    import copy as _copy
    # choose file set based on suffix heuristic, but try all
    for p in _json_file_order(sym_side):
        try:
            if not p.exists():
                continue
            _ent = _json_fallback_entry(p, sym_side)
            if isinstance(_ent, dict):
                e = _copy.deepcopy(_ent)
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
            _bucket = _read_cache_bucket("get")
            _hit = _bucket.get(sym_side)
            if _hit is not None:
                if _hit is _READ_MISS:
                    return _json_load_fallback(sym_side)
                if not _hit.get("full_config"):
                    jf = _json_load_fallback(sym_side)
                    if jf and jf.get("full_config"):
                        return jf
                return _copy2(_hit)
            con = _connect()
            try:
                cur = con.execute("SELECT * FROM per_sym_active WHERE sym_side=?", (sym_side,))
                row = cur.fetchone()
                if row is not None:
                    e = _row_to_entry(row)
                    _bucket[sym_side] = e
                    # if full_config is empty but json has it, prefer json
                    if not e.get("full_config"):
                        jf = _json_load_fallback(sym_side)
                        if jf and jf.get("full_config"):
                            return jf
                    return _copy2(e)
                _bucket[sym_side] = _READ_MISS
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


_OVR_MEMO: dict = {}  # 2026-10-14: sym_side -> (monotonic ts, overrides dict); live _cfg called this thousands of times per cycle and
# every call deep-copied the full entry (full_config) -> tradier_manage event loop frozen up to 179 s -> no exits reached the broker.

_ARTEFACT_KEYS = ("MODE", "SIMPLE_PRICE_GT0_ENABLED", "VENUE", "IS_TRADIER")
# USER 2026-10-17 "we need total parity" / "make sure every sym_side trades the EXACT bars its best settings dict demands":
# parity, exit-guarantee and exposure-mode switches are LIVE AUTHORITY — they say HOW live follows the board, never what
# the board measured (no engine or vec_decisions read any of them). Frozen into every full set at board time, they
# silently overrode config: STRICT_VEC_PARITY_GATE_EXITS=False on 368 stock + 329 crypto sets, PARITY_VEC_EXACT_MODE=False
# on 85 stock sets (snapshots older than the 2026-10-13 switch-on). Stripped here AND pinned to config in
# ez_manage._psym_get / _PerSymProxy and tradier_manage._cfg (the cat_side layer carries frozen values too).
LIVE_AUTHORITY_KNOBS = frozenset({
    "PARITY_VEC_EXACT_MODE", "PARITY_VEC_EXACT_FAMILIES", "PARITY_VEC_EXACT_SOURCE", "PARITY_VEC_EXACT_SET_DIR",
    "PARITY_VEC_EXACT_LIVE_SIZING", "PARITY_VEC_EXACT_SNAPSHOT_FINAL", "STRICT_VEC_PARITY_MODE", "STRICT_VEC_PARITY_SHADOW",
    "STRICT_VEC_PARITY_GATE_ENTRIES", "STRICT_VEC_PARITY_GATE_EXITS", "TRADIER_HERMETIC_ENFORCE", "TRADIER_NO_BOARD_ENTRY_BLOCK",
    "VEC_EXACT_STALE_FALLBACK_BARS", "VEC_EXACT_BAR_HARD_STOP_ENABLED", "EXIT_GUARANTEE_ENABLED", "EXIT_GUARANTEE_FORCE_ENABLED",
    "CRYPTO_MINIMUM_TRADING_MODE", "CRYPTO_MODE_SIZE_CEILING_ENABLED",
})


def _mandate_sanitize(ov: dict) -> dict:
    """The go-live integrity strip, applied at READ time to the frozen full set.

    tools/v15_persym_golive.sanitize() only ever cleaned the overrides half of a row; the defaults_snapshot half was
    never checked. Engine artefacts never reach live (MODE / SIMPLE_PRICE_GT0_ENABLED / VENUE / IS_TRADIER /
    ABLATION_*, USER 2026-10-08), and a MAX_ORDER_VALUE of 0 would block every order for the sym_side (it is still a
    live switch — USER 2026-10-15: "MAX_ORDER_VALUE right now is 6x START_POSITION_SIZE but it is not yet obsolete").
    MAX_AUGMENTS_PER_POSITION needs no clause any more: the switch was deleted from every surface 2026-10-15 and
    purged from the cat_side layer and all 736 live rows. A dropped key falls through to the cat_side default.
    """
    for k in _ARTEFACT_KEYS:
        ov.pop(k, None)
    for k in LIVE_AUTHORITY_KNOBS:
        ov.pop(k, None)
    for k in [k for k in ov if k.startswith("ABLATION_")]:
        ov.pop(k, None)
    if "MAX_ORDER_VALUE" in ov:
        try:
            if float(str(ov["MAX_ORDER_VALUE"]).strip()) <= 0:
                ov.pop("MAX_ORDER_VALUE", None)
        except (TypeError, ValueError):
            pass
    return ov


def get_overrides(sym_side: str) -> Optional[dict]:
    """The per-sym layer every live reader resolves FIRST (ez_manage._psym_get, tradier_manage._cfg,
    ez_positions_service). READ-ONLY: the returned dict is the live cache itself, never copy-on-read (see below).

    USER 2026-10-15: "we do not store overrides anymore, we store and apply the FULL set of switches and filters for
    every symbol and side ... defaults change every morning so overrides are absolutely useless." That is exactly the
    hole this closes. The row keeps three columns: overrides_json (the 842 keys the board's sheet covered),
    defaults_snapshot_json (the 3561-key default layer frozen when the board was written) and full_config_json
    (snapshot + overrides = the complete set the board was MEASURED with). Live read only the 842 and resolved the
    other 2719 from the CURRENT per_sym_settings layer, which tools/build_cat_side_defaults_4.py rebuilds every
    morning -> measured at 02:40Z: 384 of 735 rows were running at least one switch at a value their own board never
    saw (115 keys; STOCH_CROSS_ENTRY_TRADIER True->False on 208 rows, COOLDOWN_BARS_TRADIER 8->0 on 182,
    ADX_REGIME_FILTER_ENABLED True->False on 171, MIN_GAIN_TO_BUY_AGGRESSIVELY 4.5->6.0 on 81). Serving full_config
    makes the per-sym layer complete, so a morning default move can no longer drift live off a qualified board; the
    next go-live re-apply refreshes the snapshot. Kill switch PER_SYM_APPLY_FULL_SET=0 restores the 842-key diff.

    No copy on return: the full set is 3561 keys and live calls this thousands of times per cycle (copying cost
    8.3 us/call at 842 keys, 18.3 us at 3561 -> the same event-loop freeze the memo was added for; no-copy is 0.05 us).
    Every live caller is a membership test plus one read; the two tool callers copy for themselves.
    """
    import time as _time
    _k = str(sym_side).strip()
    _hit = _OVR_MEMO.get(_k)
    _now = _time.monotonic()
    if _hit is not None and _now - _hit[0] < float(os.environ.get("PER_SYM_OVR_MEMO_SEC", "30.0")):
        return _hit[1]
    e = get(sym_side)
    if e is None:
        ov = None
    elif os.environ.get("PER_SYM_APPLY_FULL_SET", "1") != "0":
        _full = e.get("full_config")
        ov = _mandate_sanitize(dict(_full)) if isinstance(_full, dict) and _full else dict(e.get("overrides") or {})
    else:
        ov = dict(e.get("overrides") or {})
    _OVR_MEMO[_k] = (_now, ov)
    return ov


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
    """Keep ≥30d of history; prune >35d and cap 45 per sym_side. Returns deleted count.
    USER 2026-10-14: "hold on to it infinitely … for stats and development and self learning" -> history is NEVER pruned unless
    PER_SYM_HISTORY_PRUNE=1 is set explicitly (archive copies: tools/v15_history_archive.sh on the external drive)."""
    if os.environ.get("PER_SYM_HISTORY_PRUNE") != "1":
        return 0
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
    # 2026-10-15: the 30 s get_overrides memo must not survive our own write. switch_parity.verify_symside read the
    # pre-write cached row right after upsert and reported "post-verify FAILED: override key sets differ / sqlite
    # overrides row missing" for every set, so the 02:23Z go-live apply registered 0 of 57 on paper while the rows had
    # in fact landed (AAVEUSDC_LONG 02:23:24Z, board-identical). Invalidating here keeps every writer honest.
    _OVR_MEMO.pop(sym_side, None)
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
        _invalidate_read_cache()
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
        raw = None
        for _att in range(5):
            try:
                raw = json.loads(json_path.read_text()) if json_path.exists() else {}
                break
            except Exception:
                time.sleep(0.5)
        if raw is None:
            raise RuntimeError(f"unreadable JSON book after retries: {json_path}")
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
        tmp = json_path.with_suffix(f".json.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(raw, indent=2, default=str))
        tmp.replace(json_path)
    except Exception as _e:
        # DB is primary; json failure is logged but not fatal
        try:
            print(f"[per_sym_store] json backup write {json_path.name} failed: {_e}", flush=True)
        except Exception:
            pass

    _OVR_MEMO.pop(sym_side, None)  # 2026-10-15: and again after the write, in case the row was re-read mid-upsert
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
            _invalidate_read_cache()
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
        _bucket = _read_cache_bucket("kv")
        _hit = _bucket.get(str(key), _READ_MISS)
        if _hit is not _READ_MISS:
            return _copy2(_hit)
        con = _connect()
        try:
            row = con.execute("SELECT value_json FROM kv_json WHERE key=?", (str(key),)).fetchone()
            if row is None or row["value_json"] is None:
                _bucket[str(key)] = None
                return None
            raw = _maybe_decompress(row["value_json"])
            _val = json.loads(raw) if raw else None
            _bucket[str(key)] = _val
            return _copy2(_val)
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
