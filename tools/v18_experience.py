#!/usr/bin/env python3
"""v18_experience — the V18 Experience Store (doc 17 §3.1, doc 18 §3.3; work item E2, 2026-10-20).

WHAT IT IS. One append-only SQLite shard per cat_side per month (`experience_<CAT_SIDE>_<YYYYMM>.db`) plus a tiny
`experience_index.db` (watermarks, ingest runs, shard registry, proofs, cold copies). The four cat_sides
(CRYPTO_LONG / CRYPTO_SHORT / STOCKS_LONG / STOCKS_SHORT) are NEVER mixed in one file (doc 17 principle 13): every row is
routed by its own sym_side and a row whose cat_side cannot be read is refused and counted, never guessed.

TABLES (per shard)
  sets          set_id = sha256(canonical JSON of the set) -> gzip JSON, stored ONCE per shard. `kind` says what the set
                is: `full` (a native cache row's materialised dict), `replayed_overrides` (an eval_cache_rebuild base),
                `board_start` / `board_final` (progress JSON), `board_col_c` (xlsx column C), `log_chain_base`.
  evals         one ~300-700 B row per measured eval. provenance native|rebuilt|log, era, cat_side, the native 40-hex
                cache key as a column (traceable to the cold zst), set_id + base_set_id + delta. The full set of an eval
                is ALWAYS `sets[base_set_id]` (+) delta, where delta is either explicit JSON (`delta_rule=explicit`,
                {"set": {...}, "del": [...]}) or the documented replay rule of its source (`rebuild_v1` / `log_replay_v1`:
                switch=cand then filter_key=filter_val, coerced exactly as tools/eval_cache_rebuild.py does), or unknown
                (`delta_rule=none`, set_id NULL — never invented).
  settlements   INERT identity records of a delta log collapsed to ONE row per board-row visit: lines that are
                `cached: true`, delta exactly 0, and whose behaviour fingerprint AND gain equal the running chain's.
                Every collapsed line is listed ([line, label, ts]) so no (switch=cand, filter=opt) fact is lost.
  row_done      the pilot's ROW_DONE lines (promotions, row_secs, cum_after) — the greedy chain itself.
  boards        one row per board: from BOARD_CTX lines, progress JSONs (start/final set + promoted chain) and the
                published manifests/xlsx. `ts_kind` states what the timestamp IS (published_utc, board_ctx_ts,
                file_mtime ...) — a file mtime is never presented as the date a board was computed.
  live_events   /history events verbatim (broker echoes dropped and counted), era-tagged at the hermetic cuts
                (crypto 2026-10-14T13:00Z, stocks 2026-10-17T13:00Z), `prov` block kept verbatim when present.
  trades        schema only today: no source carries ledgers (native `ledger` is None); the ledger replay (doc 17 §3.1)
                fills it later.
  log_other     any parseable delta-log line that is none of the above, kept raw.

LAWS. Append-only, INSERT OR IGNORE on natural keys, a watermark per source (path + offset/rowid + last ts) written only
after the rows it covers are committed. Never a fabricated key: native rows keep their real 40-hex key, rebuilt rows their
`rebuilt1:` key, log rows are keyed by host|path|line|ts|... . Dry-run is the default; `--apply` writes.

usage
  v18_experience.py where
  v18_experience.py ingest --kind native  --from X.db  [--apply] [--store DIR] [--host mac]
  v18_experience.py ingest --kind rebuilt --from rebuilt_v1.db [--apply]
  v18_experience.py ingest --kind shard   --from 'DIR/experience_*.db' [--apply]          (merge shards made elsewhere)
  v18_experience.py ingest --kind log     --from 'GLOB' --host s1 [--orig-prefix LOCAL=REMOTE] [--progress-glob G] [--apply]
  v18_experience.py ingest --kind history [--history-root data] [--apply]
  v18_experience.py ingest --kind boards  [--progress-glob G] [--xlsx-dir D] [--apply]
  v18_experience.py prove  --kind K --from X [--against STORE] [--report R.json]
  v18_experience.py cold   --source X.db --out X.db.zst [--apply]
  v18_experience.py reclaim --source X --cold X.zst [--proof R.json ...] [--remote-host s2] [--apply]
  v18_experience.py digest [--apply]
  v18_experience.py audit  [--out data/reports/experience_audit.json]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile
from bisect import bisect_left
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
EXT_DRIVES = ("/Volumes/TOSHIBA_EXT", "/Volumes/SSD2T", "/Volumes/E3T", "/Volumes/WD25")
EXT_SUBDIR = "binance_archive/experience"
MAC_STORE = ROOT / "data" / "experience"
MIN_FREE_GB = 50
INDEX_NAME = "experience_index.db"
SHARD_KIND = "v18_experience_shard"
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
STOCKS_ACCOUNTS = ("tra", "trb", "trc")
CRYPTO_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")
BULK_KEYS = ("overrides", "equity_curve", "exposure_series", "ledger")
NATIVE_MAIN = ("symside", "window_days", "offset_days", "bh_pct", "bars", "gain_pct", "trades", "tim_pct", "max_dd_pct",
               "pool_sharpe", "wr_pct", "valid", "invalid_reason", "behavior_fingerprint")
MAX_DELTA_KEYS = 48
BASES_PER_SYM = 4
MAX_SYMS_IN_MEMORY = 400
FLUSH_EVERY = 20000
REPLAY_LABELS_ROW = ("naked", "hustle", "SACRED_REPASS")


def _iso_epoch(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).strip().replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", txt)
    if m and m.group(2):
        frac = (m.group(2) + "000000")[:7]
        txt = m.group(1) + frac + m.group(3)
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


HERMETIC_CUT = {"CRYPTO": _iso_epoch("2026-10-14T13:00:00Z"), "STOCKS": _iso_epoch("2026-10-17T13:00:00Z")}


def _utc(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch if epoch is not None else time.time()))


def month_of(epoch):
    return time.strftime("%Y%m", time.gmtime(epoch)) if epoch else "undated"


def cat_side_of(sym_side, venue=None):
    s = str(sym_side or "")
    if "_" not in s:
        return None
    symbol, side = s.rsplit("_", 1)
    side = side.upper()
    if side not in ("LONG", "SHORT") or not symbol:
        return None
    if venue is None:
        venue = "CRYPTO" if symbol.upper().endswith(CRYPTO_QUOTES) else "STOCKS"
    return f"{venue}_{side}"


def era_of(cat_side, epoch):
    if epoch is None or not cat_side:
        return "undated"
    return "hermetic" if epoch >= HERMETIC_CUT[cat_side.split("_")[0]] else "pre_hermetic"


def _dumps(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def canon(d):
    return _dumps(d)


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def sha1_text(text):
    return hashlib.sha1(text.encode()).hexdigest()


def frags_of(d):
    return {k: json.dumps(k) + ":" + _dumps(d[k]) for k in d}


def text_of_frags(fr):
    return "{" + ",".join(fr[k] for k in sorted(fr)) + "}"


def coerce(v):
    """Exactly tools/eval_cache_rebuild.coerce, so a replayed set hashes the way the rebuild materialises it."""
    s = str(v)
    if s in ("True", "False"):
        return s == "True"
    try:
        return int(s) if s.lstrip("-").isdigit() else float(s)
    except Exception:
        return v


def parse_filter_label(label):
    s = str(label or "")
    if "=" not in s:
        return None, None
    k, v = s.split("=", 1)
    return k.strip(), v.strip()


def replay_mods(switch, cand, filter_key, filter_val):
    mods = {}
    if switch and switch != "BEST_START" and cand not in (None, ""):
        mods[str(switch)] = coerce(cand)
    if filter_key:
        mods[filter_key] = coerce(filter_val)
    return mods


class Canon:
    """Canonical JSON of a base set as per-key fragments, so base+overlay hashes without re-serialising 800 keys."""

    def __init__(self, d):
        self.keys = sorted(d)
        self.frag = [json.dumps(k) + ":" + _dumps(d[k]) for k in self.keys]
        self.text = "{" + ",".join(self.frag) + "}"
        self.set_id = sha256_text(self.text)
        self.n = len(self.keys)

    def overlay(self, mods):
        if not mods:
            return self.text
        keys, frag = list(self.keys), list(self.frag)
        for k, v in mods.items():
            f = json.dumps(k) + ":" + _dumps(v)
            i = bisect_left(keys, k)
            if i < len(keys) and keys[i] == k:
                frag[i] = f
            else:
                keys.insert(i, k)
                frag.insert(i, f)
        return "{" + ",".join(frag) + "}"


def apply_delta(base, delta):
    out = dict(base)
    for k, v in (delta or {}).get("set", {}).items():
        out[k] = v
    for k in (delta or {}).get("del", []):
        out.pop(k, None)
    return out


# ------------------------------------------------------------------------------------------------ schema

TABLES = OrderedDict([
    ("sets", (("set_id", "TEXT PRIMARY KEY"), ("kind", "TEXT"), ("n_keys", "INT"), ("gz", "BLOB"), ("first_ts", "REAL"),
              ("source", "TEXT"))),
    ("evals", (("eval_id", "TEXT PRIMARY KEY"), ("provenance", "TEXT NOT NULL"), ("sym_side", "TEXT"), ("cat_side", "TEXT"),
               ("era", "TEXT"), ("ts", "REAL"), ("native_key", "TEXT"), ("src_key", "TEXT"), ("set_id", "TEXT"),
               ("base_set_id", "TEXT"), ("delta_rule", "TEXT"), ("delta", "TEXT"), ("switch", "TEXT"), ("cand", "TEXT"),
               ("filter_key", "TEXT"), ("filter_val", "TEXT"), ("label", "TEXT"), ("window_days", "REAL"),
               ("offset_days", "REAL"), ("bh_pct", "REAL"), ("bars", "INT"), ("npz_md5", "TEXT"), ("gain", "REAL"),
               ("trades", "INT"), ("tim", "REAL"), ("dd", "REAL"), ("sharpe", "REAL"), ("wr", "REAL"), ("valid", "INT"),
               ("invalid_reason", "TEXT"), ("fp", "TEXT"), ("secs", "REAL"), ("cum_before", "REAL"), ("run", "TEXT"),
               ("phase", "TEXT"), ("row", "INT"), ("source", "TEXT"), ("extra", "TEXT"))),
    ("settlements", (("settle_id", "TEXT PRIMARY KEY"), ("sym_side", "TEXT"), ("cat_side", "TEXT"), ("era", "TEXT"),
                     ("run", "TEXT"), ("source", "TEXT"), ("sheet", "TEXT"), ("row", "INT"), ("switch", "TEXT"),
                     ("cand", "TEXT"), ("chain_fp", "TEXT"), ("chain_gain", "REAL"), ("trades", "INT"), ("n_lines", "INT"),
                     ("set_identity", "INT"), ("ts_first", "REAL"), ("ts_last", "REAL"), ("lines", "TEXT"))),
    ("row_done", (("row_id", "TEXT PRIMARY KEY"), ("sym_side", "TEXT"), ("cat_side", "TEXT"), ("era", "TEXT"),
                  ("run", "TEXT"), ("source", "TEXT"), ("line", "INT"), ("ts", "REAL"), ("sheet", "TEXT"), ("row", "INT"),
                  ("switch", "TEXT"), ("cand", "TEXT"), ("n_evals", "INT"), ("delta", "REAL"), ("promoted", "INT"),
                  ("cum_after", "REAL"), ("row_secs", "REAL"), ("extra", "TEXT"))),
    ("boards", (("board_id", "TEXT PRIMARY KEY"), ("sym_side", "TEXT"), ("cat_side", "TEXT"), ("era", "TEXT"),
                ("run", "TEXT"), ("publish_ts", "REAL"), ("ts_kind", "TEXT"), ("source", "TEXT"), ("source_path", "TEXT"),
                ("start_set_id", "TEXT"), ("final_set_id", "TEXT"), ("bh", "REAL"), ("baseline", "REAL"),
                ("final_gain", "REAL"), ("trades", "INT"), ("tim", "REAL"), ("dd", "REAL"), ("tier", "TEXT"),
                ("verdict", "TEXT"), ("npz_md5", "TEXT"), ("window_days", "REAL"), ("file_md5", "TEXT"),
                ("promoted_chain", "TEXT"), ("metrics", "TEXT"))),
    ("live_events", (("event_id", "TEXT PRIMARY KEY"), ("account", "TEXT"), ("venue", "TEXT"), ("sym_side", "TEXT"),
                     ("cat_side", "TEXT"), ("era", "TEXT"), ("lifecycle", "TEXT"), ("ts", "TEXT"), ("epoch", "REAL"),
                     ("type", "TEXT"), ("qty", "REAL"), ("price", "REAL"), ("value", "REAL"), ("reason", "TEXT"),
                     ("family", "TEXT"), ("prov_tag", "TEXT"), ("prov", "TEXT"), ("technicals", "TEXT"),
                     ("source", "TEXT"), ("line", "INT"), ("extra", "TEXT"))),
    ("trades", (("eval_id", "TEXT"), ("trade_no", "INT"), ("bar_entry", "INT"), ("bar_exit", "INT"), ("bars_held", "INT"),
                ("entry_family", "TEXT"), ("exit_family", "TEXT"), ("entry_reason", "TEXT"), ("exit_reason", "TEXT"),
                ("pnl_pct", "REAL"), ("pnl_usd", "REAL"), ("deployed", "REAL"), ("mfe_pct", "REAL"), ("mae_pct", "REAL"),
                ("post_exit_32b_pct", "REAL"), ("unrealised_mark", "INT"))),
    ("log_other", (("other_id", "TEXT PRIMARY KEY"), ("sym_side", "TEXT"), ("source", "TEXT"), ("line", "INT"),
                   ("ts", "REAL"), ("label", "TEXT"), ("raw", "TEXT"))),
])
TABLE_PK = {t: cols[0][0] for t, cols in TABLES.items() if t != "trades"}


def shard_ddl():
    out = []
    for t, cols in TABLES.items():
        body = ", ".join(f"{c} {ty}" for c, ty in cols)
        if t == "trades":
            body += ", PRIMARY KEY (eval_id, trade_no)"
        out.append(f"CREATE TABLE IF NOT EXISTS {t} ({body})")
    out.append("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return out


SHARD_INDEXES = ("CREATE INDEX IF NOT EXISTS ix_evals_sym ON evals (sym_side)",
                 "CREATE INDEX IF NOT EXISTS ix_evals_switch ON evals (switch, cand)",
                 "CREATE INDEX IF NOT EXISTS ix_settle_source ON settlements (source)",
                 "CREATE INDEX IF NOT EXISTS ix_live_sym ON live_events (sym_side, epoch)")

INDEX_DDL = (
    "CREATE TABLE IF NOT EXISTS watermarks (source TEXT PRIMARY KEY, kind TEXT, host TEXT, offset INTEGER, line INTEGER,"
    " last_ts TEXT, rows INTEGER, updated TEXT, extra TEXT)",
    "CREATE TABLE IF NOT EXISTS ingests (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, kind TEXT, host TEXT, started TEXT,"
    " finished TEXT, apply INT, counters TEXT)",
    "CREATE TABLE IF NOT EXISTS shards (name TEXT PRIMARY KEY, cat_side TEXT, month TEXT, created TEXT, updated TEXT)",
    "CREATE TABLE IF NOT EXISTS proofs (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, kind TEXT, against TEXT, at TEXT,"
    " checked INTEGER, missing INTEGER, mismatched INTEGER, report TEXT, source_bytes INTEGER, source_mtime REAL)",
    "CREATE TABLE IF NOT EXISTS colds (zst TEXT PRIMARY KEY, source TEXT, sha256 TEXT, bytes INTEGER, manifest TEXT, at TEXT)",
    "CREATE TABLE IF NOT EXISTS chain_bases (set_id TEXT PRIMARY KEY, gz BLOB)",
)


def _connect(path, readonly=False):
    if readonly:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=600.0, isolation_level=None)
    else:
        c = sqlite3.connect(str(path), timeout=600.0, isolation_level=None, uri=True)
        for p in ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL", "PRAGMA cache_size=-200000",
                  "PRAGMA temp_store=MEMORY"):
            try:
                c.execute(p)
            except sqlite3.OperationalError as exc:
                print(f"[store] {p} skipped ({exc})", flush=True)
    return c


# ------------------------------------------------------------------------------------------------ location

def _real_mount(p):
    try:
        return os.path.ismount(p) and os.stat(p).st_dev != os.stat("/").st_dev
    except OSError:
        return False


def _free_gb(p):
    try:
        st = os.statvfs(p)
        return st.f_bavail * st.f_frsize / 1e9
    except OSError:
        return None


def store_candidates():
    rows = []
    for d in EXT_DRIVES:
        rows.append({"path": os.path.join(d, EXT_SUBDIR), "drive": d, "real_mount": _real_mount(d), "free_gb": _free_gb(d)})
    rows.append({"path": str(MAC_STORE), "drive": "/", "real_mount": False, "free_gb": _free_gb(str(ROOT))})
    return rows


def choose_store(explicit=None):
    """Sticky first: a store that already exists is used where it is (an existing master is never silently forked).
    Otherwise the first real ext mount with >= 50 GB free, else the Mac boot disk under data/experience/."""
    if explicit:
        return Path(os.path.expanduser(explicit)), "explicit --store"
    env = os.environ.get("V18_EXPERIENCE_ROOT")
    if env:
        return Path(env), "$V18_EXPERIENCE_ROOT"
    cands = store_candidates()
    for c in cands:
        if os.path.exists(os.path.join(c["path"], INDEX_NAME)) and (c["drive"] == "/" or c["real_mount"]):
            return Path(c["path"]), "existing store (sticky)"
    for c in cands[:-1]:
        if c["real_mount"] and (c["free_gb"] or 0) >= MIN_FREE_GB:
            return Path(c["path"]), f"first real ext mount with >= {MIN_FREE_GB} GB free"
    if not ROOT.joinpath("data").is_dir():
        raise SystemExit("no ext mount with room and no repo data/ dir here: pass --store explicitly")
    return Path(cands[-1]["path"]), f"no real ext mount with >= {MIN_FREE_GB} GB free -> Mac boot disk"


# ------------------------------------------------------------------------------------------------ store

class Store:
    def __init__(self, root, apply=False):
        self.root = Path(root)
        self.apply = apply
        self.cons = {}
        self.sets_seen = {}
        self.pending = {}
        self.inserted = Counter()
        self.offered = Counter()
        self._idx = None
        if apply:
            self.root.mkdir(parents=True, exist_ok=True)

    # -- index
    def idx(self):
        if self._idx is None:
            p = self.root / INDEX_NAME
            if not self.apply and not p.exists():
                return None
            self._idx = _connect(p, readonly=not self.apply)
            if self.apply:
                for s in INDEX_DDL:
                    self._idx.execute(s)
        return self._idx

    def get_wm(self, source):
        ix = self.idx()
        if ix is None:
            return None
        try:
            row = ix.execute("SELECT offset, line, last_ts, rows, extra FROM watermarks WHERE source=?", (source,)).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        return {"offset": row[0] or 0, "line": row[1] or 0, "last_ts": row[2], "rows": row[3] or 0,
                "extra": json.loads(row[4]) if row[4] else {}}

    def set_wm(self, source, kind, host, offset, line, last_ts, rows, extra=None):
        if not self.apply:
            return
        self.flush()
        self.idx().execute("INSERT OR REPLACE INTO watermarks (source, kind, host, offset, line, last_ts, rows, updated, extra)"
                           " VALUES (?,?,?,?,?,?,?,?,?)", (source, kind, host, int(offset), int(line), last_ts, int(rows),
                                                           _utc(), json.dumps(extra or {}, default=str)))

    # -- shards
    @staticmethod
    def shard_name(cat_side, month):
        return f"experience_{cat_side}_{month}.db"

    def shard_paths(self, cat_side=None):
        pat = f"experience_{cat_side}_*.db" if cat_side else "experience_*_*.db"
        return sorted(p for p in self.root.glob(pat) if p.name != INDEX_NAME)

    def shard(self, cat_side, month, create=True):
        if cat_side not in CAT_SIDES:
            raise ValueError(f"refusing unknown cat_side {cat_side!r}")
        name = self.shard_name(cat_side, month)
        if name in self.cons:
            return self.cons[name]
        p = self.root / name
        if not self.apply:
            c = _connect(p, readonly=True) if p.exists() else None
        else:
            new = not p.exists()
            c = _connect(p)
            for s in shard_ddl():
                c.execute(s)
            if new:
                c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('kind', ?)", (SHARD_KIND,))
                c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('cat_side', ?)", (cat_side,))
                c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('month', ?)", (month,))
                self.idx().execute("INSERT OR IGNORE INTO shards (name, cat_side, month, created, updated) VALUES (?,?,?,?,?)",
                                   (name, cat_side, month, _utc(), _utc()))
        self.cons[name] = c
        return c

    def exists(self, table, cat_side, month, key):
        name = self.shard_name(cat_side, month)
        pend = self.pending.get(name, {}).get(table)
        if pend and key in pend:
            return True
        c = self.shard(cat_side, month)
        if c is None:
            return False
        try:
            return c.execute(f"SELECT 1 FROM {table} WHERE {TABLE_PK[table]}=?", (key,)).fetchone() is not None
        except sqlite3.OperationalError:
            return False

    def put(self, cat_side, month, table, row):
        if cat_side not in CAT_SIDES:
            raise ValueError(f"refusing unknown cat_side {cat_side!r}")
        name = self.shard_name(cat_side, month)
        self.offered[f"{table}:{cat_side}"] += 1
        bucket = self.pending.setdefault(name, {}).setdefault(table, OrderedDict())
        key = row[0] if table != "trades" else (row[0], row[1])
        bucket.setdefault(key, (cat_side, month, row))
        if sum(len(b) for b in self.pending[name].values()) >= FLUSH_EVERY:
            self.flush(name)

    def set_known(self, cat_side, month, set_id):
        return set_id in self.sets_seen.get(self.shard_name(cat_side, month), ())

    def put_set(self, cat_side, month, set_id, kind, n_keys, text, ts, source):
        name = self.shard_name(cat_side, month)
        seen = self.sets_seen.setdefault(name, set())
        if set_id in seen:
            return
        seen.add(set_id)
        self.put(cat_side, month, "sets", (set_id, kind, n_keys, gzip.compress(text.encode(), 6, mtime=0), ts, source))

    def flush(self, only=None):
        for name in list(self.pending):
            if only and name != only:
                continue
            tables = self.pending.pop(name)
            if not self.apply:
                for t, b in tables.items():
                    for _k, (cs, _m, _r) in b.items():
                        self.inserted[f"{t}:{cs}:dry"] += 1
                continue
            first = next(iter(next(iter(tables.values())).values()))
            c = self.shard(first[0], first[1])
            c.execute("BEGIN")
            for t, b in tables.items():
                cols = [col for col, _ty in TABLES[t]]
                before = c.total_changes
                c.executemany(f"INSERT OR IGNORE INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              [r for (_cs, _m, r) in b.values()])
                self.inserted[f"{t}:{first[0]}"] += c.total_changes - before
            c.execute("COMMIT")

    def log_ingest(self, source, kind, host, started, counters):
        if not self.apply:
            return
        self.flush()
        self.idx().execute("INSERT INTO ingests (source, kind, host, started, finished, apply, counters) VALUES (?,?,?,?,?,?,?)",
                           (source, kind, host, started, _utc(), 1, json.dumps(counters, default=str)))
        for name in self.cons:
            self.idx().execute("UPDATE shards SET updated=? WHERE name=?", (_utc(), name))

    def finalize(self, build_indexes=False):
        self.flush()
        if self.apply and build_indexes:
            for c in self.cons.values():
                for s in SHARD_INDEXES:
                    c.execute(s)

    def close(self):
        self.flush()
        for c in self.cons.values():
            if c is not None:
                c.close()
        if self._idx is not None:
            self._idx.close()


# ------------------------------------------------------------------------------------------------ native

_MISSING = object()


def _eq(a, b):
    """Type-strict equality (True != 1, 1 != 1.0) so a delta never drops a key whose JSON form differs."""
    if type(a) is not type(b):
        return False
    if isinstance(a, (dict, list)):
        return _dumps(a) == _dumps(b)
    return a == b


class BasePicker:
    """Per sym_side LRU of base sets; an eval's set is stored as base + explicit delta (exact and type-strict)."""

    def __init__(self):
        self.syms = OrderedDict()

    def pick(self, sym, ov, set_id):
        lru = self.syms.get(sym)
        if lru is None:
            lru = self.syms[sym] = OrderedDict()
            while len(self.syms) > MAX_SYMS_IN_MEMORY:
                self.syms.popitem(last=False)
        else:
            self.syms.move_to_end(sym)
        if set_id in lru:
            lru.move_to_end(set_id)
            return set_id, None
        best = None
        for bid, base in lru.items():
            n = 0
            for k, v in ov.items():
                if not _eq(base.get(k, _MISSING), v):
                    n += 1
                    if n > MAX_DELTA_KEYS:
                        break
            if n <= MAX_DELTA_KEYS:
                n += sum(1 for k in base if k not in ov)
            if n <= MAX_DELTA_KEYS and (best is None or n < best[1]):
                best = (bid, n)
        if best is not None:
            base = lru[best[0]]
            lru.move_to_end(best[0])
            return best[0], ([k for k, v in ov.items() if not _eq(base.get(k, _MISSING), v)], [k for k in base if k not in ov])
        lru[set_id] = ov
        while len(lru) > BASES_PER_SYM:
            lru.popitem(last=False)
        return set_id, None

    def base(self, sym, bid):
        return self.syms[sym][bid]


def _native_source_id(host, path):
    return f"{host}:{os.path.abspath(path)}"


def _native_row(key, result_txt, created, source_id, picker, counters):
    """-> (cat_side, month, eval_row, base_ref) or None. base_ref = (sym, base_set_id) for the caller to store once."""
    try:
        r = json.loads(result_txt)
    except Exception:
        counters["bad_json"] += 1
        return None
    if str(key).startswith("rebuilt1:"):
        counters["skip_rebuilt_materialized"] += 1
        return None
    sym = r.get("symside")
    cs = cat_side_of(sym)
    if cs is None:
        counters["no_cat_side"] += 1
        return None
    ts = float(created) if created is not None else None
    month, era = month_of(ts), era_of(cs, ts)
    ov = r.get("overrides")
    set_id = base_id = delta = None
    rule = "none"
    base_needed = None
    if isinstance(ov, dict):
        set_id = sha256_text(canon(ov))
        base_id, diff = picker.pick(sym, ov, set_id)
        rule = "explicit"
        if diff is not None:
            delta = _dumps({"set": {k: ov[k] for k in diff[0]}, "del": sorted(diff[1])})
        base_needed = (sym, base_id)
    npz = r.get("npz_provenance")
    npz_md5 = npz.get("md5") if isinstance(npz, dict) else (npz if isinstance(npz, str) else None)
    extra = {k: v for k, v in r.items() if k not in BULK_KEYS and k not in NATIVE_MAIN}
    extra["_bulk_present"] = {k: r.get(k) is not None for k in BULK_KEYS}
    row = ("n:" + str(key), "native", sym, cs, era, ts, str(key), None, set_id, base_id, rule, delta, None, None, None, None,
           None, r.get("window_days"), r.get("offset_days"), r.get("bh_pct"), r.get("bars"), npz_md5, r.get("gain_pct"),
           r.get("trades"), r.get("tim_pct"), r.get("max_dd_pct"), r.get("pool_sharpe"), r.get("wr_pct"),
           None if r.get("valid") is None else int(bool(r.get("valid"))), r.get("invalid_reason"),
           r.get("behavior_fingerprint"), None, None, None, None, None, source_id, _dumps(extra))
    return cs, month, row, base_needed


def ingest_native(a, store):
    path = a.source
    source_id = _native_source_id(a.host, path)
    src = _connect(path, readonly=True)
    wm = store.get_wm(source_id) or {"offset": 0, "rows": 0}
    counters = Counter()
    picker = BasePicker()
    last_rowid, last_ts, t0 = wm["offset"], None, time.time()
    q = "SELECT rowid, key, result, created FROM evals WHERE rowid > ? ORDER BY rowid"
    for rowid, key, result, created in src.execute(q, (wm["offset"],)):
        counters["source_rows"] += 1
        got = _native_row(key, result, created, source_id, picker, counters)
        last_rowid = rowid
        if got is None:
            continue
        cs, month, row, base = got
        if base is not None and not store.set_known(cs, month, base[1]):
            bd = picker.base(base[0], base[1])
            store.put_set(cs, month, base[1], "full", len(bd), canon(bd), row[5], source_id)
        store.put(cs, month, "evals", row)
        counters[f"evals:{cs}"] += 1
        last_ts = row[5]
        if counters["source_rows"] % FLUSH_EVERY == 0:
            store.set_wm(source_id, "native", a.host, last_rowid, 0, _utc(last_ts) if last_ts else None,
                         wm["rows"] + counters["source_rows"])
            if counters["source_rows"] % 200000 == 0:
                print(f"[native] {counters['source_rows']:,} rows · {time.time() - t0:.0f}s", flush=True)
        if a.limit and counters["source_rows"] >= a.limit:
            break
    store.set_wm(source_id, "native", a.host, last_rowid, 0, _utc(last_ts) if last_ts else None,
                 wm["rows"] + counters["source_rows"])
    src.close()
    return counters


# ------------------------------------------------------------------------------------------------ rebuilt

def ingest_rebuilt(a, store):
    path = a.source
    source_id = _native_source_id(a.host, path)
    src = _connect(path, readonly=True)
    wm = store.get_wm(source_id) or {"offset": 0, "rows": 0}
    counters = Counter()
    bases_raw = {}
    for bid, sym, nk, bsrc, ov in src.execute("SELECT base_id, symside, n_keys, source, overrides FROM bases"):
        bases_raw[bid] = (sym, bsrc, ov)
    counters["source_bases"] = len(bases_raw)
    canons = OrderedDict()
    t0 = time.time()
    last_rowid, last_ts = wm["offset"], None
    q = ("SELECT rowid, ek, base_id, symside, created, switch, cand, filter_key, filter_val, ctx, metrics, prov"
         " FROM evals_compact WHERE rowid > ? ORDER BY rowid")
    for rowid, ek, bid, sym, created, sw, cand, fk, fv, ctxj, metj, provj in src.execute(q, (wm["offset"],)):
        counters["source_rows"] += 1
        last_rowid = rowid
        try:
            ctx, met, prov = json.loads(ctxj or "{}"), json.loads(metj or "{}"), json.loads(provj or "{}")
        except Exception:
            counters["bad_json"] += 1
            continue
        cs = cat_side_of(sym)
        if cs is None:
            counters["no_cat_side"] += 1
            continue
        if ctx.get("cat_side") and ctx.get("cat_side") != cs:
            counters["ctx_cat_side_disagrees"] += 1
        ts = float(created) if created is not None else None
        month, era = month_of(ts), era_of(cs, ts)
        cn = canons.get(bid)
        if cn is None:
            raw = bases_raw.get(bid)
            if raw is None:
                counters["base_missing_in_source"] += 1
                cn = None
            else:
                cn = Canon(json.loads(raw[2]))
                canons[bid] = cn
                while len(canons) > 600:
                    canons.popitem(last=False)
        else:
            canons.move_to_end(bid)
        set_id = base_set_id = None
        rule = "none"
        if cn is not None:
            store.put_set(cs, month, cn.set_id, "replayed_overrides", cn.n, cn.text, ts, f"{source_id}#base:{bid}")
            set_id = sha256_text(cn.overlay(replay_mods(sw, cand, fk, fv)))
            base_set_id, rule = cn.set_id, "rebuild_v1"
        label = prov.get("label")
        if label not in ("naked",) and not fk and label not in REPLAY_LABELS_ROW:
            counters["replay_rule_non_row_label"] += 1
        extra = {"src": prov.get("src"), "line": prov.get("line"), "nav": prov.get("nav"), "tier": ctx.get("tier"),
                 "base_source": prov.get("base_source"), "cached_when_logged": prov.get("cached_when_logged"),
                 "src_base_id": bid, "delta": prov.get("delta"), "fn": prov.get("fn")}
        row = ("r:" + str(ek)[len("rebuilt1:"):], "rebuilt", sym, cs, era, ts, None, ek, set_id, base_set_id, rule, None,
               sw, cand, fk, fv, label, ctx.get("window_days"), ctx.get("offset_days"), ctx.get("bh_pct"), ctx.get("bars"),
               None, met.get("gain_pct"), met.get("trades"), met.get("tim_pct"), met.get("max_dd_pct"),
               met.get("pool_sharpe"), met.get("wr_pct"), None if met.get("valid") is None else int(bool(met.get("valid"))),
               met.get("invalid_reason"), met.get("behavior_fingerprint"), prov.get("secs"), prov.get("cum_before"),
               ctx.get("round"), prov.get("sheet"), prov.get("row") if isinstance(prov.get("row"), int) else None,
               source_id, _dumps(extra))
        store.put(cs, month, "evals", row)
        counters[f"evals:{cs}"] += 1
        last_ts = ts
        if counters["source_rows"] % FLUSH_EVERY == 0:
            store.set_wm(source_id, "rebuilt", a.host, last_rowid, 0, _utc(last_ts) if last_ts else None,
                         wm["rows"] + counters["source_rows"])
            if counters["source_rows"] % 500000 == 0:
                print(f"[rebuilt] {counters['source_rows']:,} rows · {time.time() - t0:.0f}s", flush=True)
        if a.limit and counters["source_rows"] >= a.limit:
            break
    store.set_wm(source_id, "rebuilt", a.host, last_rowid, 0, _utc(last_ts) if last_ts else None,
                 wm["rows"] + counters["source_rows"])
    src.close()
    return counters


# ------------------------------------------------------------------------------------------------ shard merge

def ingest_shard(a, store):
    counters = Counter()
    for p in sorted(glob.glob(os.path.expanduser(a.source))):
        if os.path.basename(p) == INDEX_NAME:
            continue
        s = _connect(p, readonly=True)
        meta = dict(s.execute("SELECT k, v FROM meta").fetchall())
        s.close()
        if meta.get("kind") != SHARD_KIND or meta.get("cat_side") not in CAT_SIDES:
            counters["skipped_not_a_shard"] += 1
            continue
        cs, month = meta["cat_side"], meta["month"]
        if not store.apply:
            s = _connect(p, readonly=True)
            for t in TABLES:
                n = s.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                counters[f"{t}:{cs}:offered"] += n
            s.close()
            continue
        c = store.shard(cs, month)
        c.execute("ATTACH DATABASE ? AS s", (f"file:{os.path.abspath(p)}?mode=ro",))
        try:
            for t in TABLES:
                n_src = c.execute(f"SELECT count(*) FROM s.{t}").fetchone()[0]
                before = c.total_changes
                c.execute(f"INSERT OR IGNORE INTO main.{t} SELECT * FROM s.{t}")
                counters[f"{t}:{cs}:offered"] += n_src
                counters[f"{t}:{cs}:inserted"] += c.total_changes - before
        finally:
            c.execute("DETACH DATABASE s")
        counters["shards_merged"] += 1
        print(f"[shard] merged {p} -> {store.shard_name(cs, month)}", flush=True)
    return counters


# ------------------------------------------------------------------------------------------------ delta logs

def _orig_path(path, prefixes):
    ap = os.path.abspath(path)
    for local, remote in prefixes:
        if ap.startswith(local):
            return remote + ap[len(local):]
    return ap


def _load_progress_bases(globs):
    """sym_side -> (round, n_keys, initial_overrides) from progress JSONs (the board's own START set)."""
    out = {}
    for g in globs or []:
        for f in glob.glob(os.path.expanduser(g)):
            if not f.endswith("_v14_progress.json"):
                continue
            try:
                d = json.loads(Path(f).read_text())
            except Exception:
                continue
            ov = d.get("initial_overrides")
            sym = d.get("symside")
            if isinstance(ov, dict) and ov and sym:
                out[sym] = (d.get("defaults_round"), len(ov), ov)
    return out


def _log_eval_ids(host, orig, ln, d):
    tail = f"{orig}|{ln}|{d.get('ts')}|{d.get('sym_side')}|{d.get('sheet')}|{d.get('row')}|{d.get('switch')}|{d.get('cand')}|{d.get('label')}"
    return "l:" + sha1_text(f"{host}|{tail}"), "r:" + sha1_text(tail)


class LogState:
    def __init__(self, sym_hint):
        self.sym_hint = sym_hint
        self.ctx = {}
        self.chain_fp = None
        self.chain_gain = None
        self.chain_trades = None
        self.base_set_id = None
        self.promoted = {}
        self.row_evals = []
        self.settle = None

    def dump(self):
        return {"ctx": self.ctx, "chain_fp": self.chain_fp, "chain_gain": self.chain_gain, "chain_trades": self.chain_trades,
                "base_set_id": self.base_set_id, "promoted": self.promoted}

    @classmethod
    def load(cls, sym_hint, d):
        s = cls(sym_hint)
        for k, v in (d or {}).items():
            setattr(s, k, v)
        return s


def _flush_settlement(st, store):
    s = st.settle
    st.settle = None
    if not s:
        return
    store.put(s["cs"], month_of(s["ts_first"]), "settlements",
              (s["id"], s["sym"], s["cs"], era_of(s["cs"], s["ts_first"]), s["run"], s["source"], s["sheet"], s["row"],
               s["switch"], s["cand"], s["chain_fp"], s["chain_gain"], s["trades"], len(s["lines"]), s["set_identity"],
               s["ts_first"], s["ts_last"], json.dumps(s["lines"], separators=(",", ":"))))


def _chain_canon(st, store, cache):
    if not st.base_set_id:
        return None
    key = (st.base_set_id, _dumps(st.promoted))
    if key in cache:
        return cache[key]
    base = cache.get(("base", st.base_set_id))
    ix = store.idx()
    if base is None and ix is not None:
        try:
            row = ix.execute("SELECT gz FROM chain_bases WHERE set_id=?", (st.base_set_id,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        base = json.loads(gzip.decompress(row[0])) if row else None
    if base is None:
        return None
    d = dict(base)
    d.update({k: coerce(v) for k, v in st.promoted.items()})
    cache.clear() if len(cache) > 64 else None
    cache[("base", st.base_set_id)] = base
    cache[key] = Canon(d)
    return cache[key]


def ingest_log_file(path, host, prefixes, prog_bases, store, counters, limit=0):
    orig = _orig_path(path, prefixes)
    source_id = f"{host}:{orig}"
    wm = store.get_wm(source_id) or {"offset": 0, "line": 0, "rows": 0, "extra": {}}
    sym_hint = Path(path).name.split("_jump")[0].replace(".jsonl", "")
    st = LogState.load(sym_hint, wm["extra"].get("state"))
    canon_cache = {}
    offset, ln, last_ts = wm["offset"], wm["line"], wm.get("last_ts")
    n_new = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                counters["partial_tail_line_skipped"] += 1
                break
            offset += len(raw)
            ln += 1
            n_new += 1
            try:
                d = json.loads(raw)
            except Exception:
                counters["bad_json"] += 1
                continue
            if not isinstance(d, dict):
                counters["bad_json"] += 1
                continue
            counters["lines"] += 1
            sym = d.get("sym_side") or sym_hint
            cs = cat_side_of(sym)
            if cs is None:
                counters["no_cat_side"] += 1
                continue
            ts = _iso_epoch(d.get("ts"))
            last_ts = d.get("ts") or last_ts
            label = d.get("label")
            if label == "BOARD_CTX":
                _flush_settlement(st, store)
                st.ctx = {k: d.get(k) for k in ("cat_side", "tier", "bh", "baseline_gain", "baseline_trades", "n_overrides",
                                                 "start_fp", "window_days", "round")}
                st.chain_fp, st.chain_gain, st.chain_trades, st.promoted, st.base_set_id = None, None, None, {}, None
                pb = prog_bases.get(sym)
                if pb and pb[0] == d.get("round") and pb[1] == d.get("n_overrides"):
                    cn = Canon(pb[2])
                    st.base_set_id = cn.set_id
                    store.put_set(cs, month_of(ts), cn.set_id, "log_chain_base", cn.n, cn.text, ts, source_id)
                    if store.apply:
                        store.idx().execute("INSERT OR IGNORE INTO chain_bases (set_id, gz) VALUES (?,?)",
                                            (cn.set_id, gzip.compress(cn.text.encode(), 6, mtime=0)))
                    canon_cache[("base", cn.set_id)] = pb[2]
                    counters["board_base_from_progress"] += 1
                else:
                    counters["board_base_unknown"] += 1
                bid = "b:" + sha1_text(f"{host}|{orig}|{ln}|{d.get('ts')}")
                store.put(cs, month_of(ts), "boards",
                          (bid, sym, cs, era_of(cs, ts), d.get("round"), ts, "board_ctx_ts", "log_board_ctx", source_id,
                           st.base_set_id, None, d.get("bh"), d.get("baseline_gain"), None, d.get("baseline_trades"), None,
                           None, d.get("tier"), None, None, d.get("window_days"), None, None, _dumps(dict(st.ctx, line=ln))))
                counters[f"boards:{cs}"] += 1
                continue
            if label == "ROW_DONE":
                key = (d.get("sheet"), d.get("row"), d.get("switch"), d.get("cand"))
                if st.settle and st.settle["rowkey"] == key:
                    _flush_settlement(st, store)
                if d.get("promoted") and d.get("switch"):
                    st.promoted[str(d["switch"])] = d.get("cand")
                    st.chain_gain = d.get("cum_after")
                    hit = [e for e in st.row_evals if e[0] == d.get("cum_after")]
                    st.chain_fp = hit[0][1] if hit else None
                    st.chain_trades = hit[0][2] if hit else None
                    counters["promotions"] += 1
                st.row_evals = []
                rid = "d:" + sha1_text(f"{host}|{orig}|{ln}|{d.get('ts')}")
                store.put(cs, month_of(ts), "row_done",
                          (rid, sym, cs, era_of(cs, ts), st.ctx.get("round"), source_id, ln, ts, d.get("sheet"),
                           d.get("row") if isinstance(d.get("row"), int) else None, d.get("switch"), d.get("cand"),
                           d.get("n_evals"), d.get("delta"), int(bool(d.get("promoted"))), d.get("cum_after"),
                           d.get("row_secs"), _dumps({k: d.get(k) for k in ("nav",)})))
                counters[f"row_done:{cs}"] += 1
                continue
            if d.get("gain_pct") is None:
                oid = "o:" + sha1_text(f"{host}|{orig}|{ln}")
                store.put(cs, month_of(ts), "log_other", (oid, sym, source_id, ln, ts, str(label), raw.decode(errors="replace")))
                counters[f"log_other:{cs}"] += 1
                continue
            if label == "BEST_START_SET" or (st.chain_fp is None and d.get("sheet") == "START"):
                st.chain_fp, st.chain_gain, st.chain_trades = d.get("fp"), d.get("gain_pct"), d.get("trades")
            st.row_evals.append((d.get("gain_pct"), d.get("fp"), d.get("trades")))
            lid, rid = _log_eval_ids(host, orig, ln, d)
            month, era = month_of(ts), era_of(cs, ts)
            delta = d.get("delta")
            fk, fv = parse_filter_label(label)
            identity = (d.get("cached") is True and delta is not None and float(delta) == 0.0 and st.chain_fp is not None
                        and d.get("fp") == st.chain_fp and d.get("gain_pct") == st.chain_gain)
            if host == "s2" and store.exists("evals", cs, month, rid):
                counters["already_in_store_as_rebuilt"] += 1
                continue
            if identity:
                rowkey = (d.get("sheet"), d.get("row"), d.get("switch"), d.get("cand"))
                if st.settle and st.settle["rowkey"] != rowkey:
                    _flush_settlement(st, store)
                if not st.settle:
                    cn = _chain_canon(st, store, canon_cache)
                    st.settle = {"id": "s:" + sha1_text(f"{host}|{orig}|{ln}"), "rowkey": rowkey, "sym": sym, "cs": cs,
                                 "run": st.ctx.get("round"), "source": source_id, "sheet": d.get("sheet"),
                                 "row": d.get("row") if isinstance(d.get("row"), int) else None, "switch": d.get("switch"),
                                 "cand": d.get("cand"), "chain_fp": st.chain_fp, "chain_gain": st.chain_gain,
                                 "trades": d.get("trades"), "ts_first": ts, "ts_last": ts, "lines": [],
                                 "set_identity": None, "_cn": cn}
                s = st.settle
                cn = s.get("_cn")
                if cn is not None:
                    same = cn.overlay(replay_mods(d.get("switch"), d.get("cand"), fk, fv)) == cn.text
                    s["set_identity"] = int(same) if s["set_identity"] in (None, 1) else 0
                s["lines"].append([ln, label, d.get("ts")])
                s["ts_last"] = ts
                counters[f"identity_lines:{cs}"] += 1
                continue
            set_id, rule = None, "none"
            cn = _chain_canon(st, store, canon_cache)
            regular = label in REPLAY_LABELS_ROW or fk is not None
            if cn is not None and regular:
                set_id, rule = sha256_text(cn.overlay(replay_mods(d.get("switch"), d.get("cand"), fk, fv))), "log_replay_v1"
            extra = {"line": ln, "nav": d.get("nav"), "cached": d.get("cached"), "err": d.get("err"), "delta": delta,
                     "fn": d.get("fn"), "tier": st.ctx.get("tier"), "chain_fp": st.chain_fp,
                     "chain_promoted_n": len(st.promoted)}
            row = (lid, "log", sym, cs, era, ts, None, f"{orig}:{ln}", set_id, cn.set_id if (cn is not None and regular) else None,
                   rule, _dumps({"promoted": st.promoted}) if rule == "log_replay_v1" else None, d.get("switch"), d.get("cand"),
                   fk, fv, label, d.get("window_days"), d.get("offset_days"), st.ctx.get("bh"), d.get("bars"), None,
                   d.get("gain_pct"), d.get("trades"), d.get("tim"), d.get("max_dd_pct"), d.get("pool_sharpe"),
                   d.get("wr_pct"), None if d.get("valid") is None else int(bool(d.get("valid"))), d.get("invalid_reason"),
                   d.get("fp"), d.get("secs"), d.get("cum_before"), st.ctx.get("round"), d.get("sheet"),
                   d.get("row") if isinstance(d.get("row"), int) else None, source_id, _dumps(extra))
            store.put(cs, month, "evals", row)
            counters[f"evals:{cs}"] += 1
            if limit and counters["lines"] >= limit:
                break
    if st.settle:
        st.settle.pop("_cn", None)
    _flush_settlement(st, store)
    st.row_evals = []
    store.set_wm(source_id, "log", host, offset, ln, last_ts, wm["rows"] + n_new, {"state": st.dump()})
    return n_new


def ingest_log(a, store):
    counters = Counter()
    prefixes = []
    for p in a.orig_prefix or []:
        local, remote = p.split("=", 1)
        prefixes.append((os.path.abspath(os.path.expanduser(local)), remote))
    prog = _load_progress_bases(a.progress_glob)
    counters["progress_bases_loaded"] = len(prog)
    files = sorted(set(glob.glob(os.path.expanduser(a.source))))
    t0 = time.time()
    for i, f in enumerate(files, 1):
        ingest_log_file(f, a.host, prefixes, prog, store, counters, a.limit)
        counters["files"] += 1
        if i % 10 == 0:
            print(f"[log] {i}/{len(files)} files · {counters['lines']:,} lines · {time.time() - t0:.0f}s", flush=True)
    return counters


# ------------------------------------------------------------------------------------------------ /history

def _family(reason):
    s = str(reason or "").strip()
    if not s:
        return None
    tok = re.split(r"[\s|(]", s, 1)[0]
    return tok or None


def _prov_tag(reason):
    s = str(reason or "")
    if "VEC_EXACT" in s:
        return "VEC_EXACT"
    if "FIXED_STOP" in s:
        return "FIXED_STOP"
    return "untagged"


def is_broker_echo(reason):
    s = str(reason or "")
    return "BROKER_RECONCILED" in s or "broker sync" in s.lower()


def history_files(root):
    root = Path(root)
    out = []
    for acct in CRYPTO_ACCOUNTS:
        out += [("CRYPTO", acct, p) for p in sorted((root / "history" / acct).glob("*.jsonl"))]
    for acct in STOCKS_ACCOUNTS:
        out += [("STOCKS", acct, p) for p in sorted((root / "tradier" / "history" / acct).glob("*.jsonl"))]
    return out


def ingest_history(a, store):
    counters = Counter()
    for venue, acct, p in history_files(a.history_root):
        sym = p.name[:-len(".jsonl")]
        cs = cat_side_of(sym, venue)
        if cs is None:
            counters["no_cat_side_files"] += 1
            continue
        source_id = f"{a.host}:{os.path.abspath(p)}"
        wm = store.get_wm(source_id) or {"offset": 0, "line": 0, "rows": 0, "extra": {}}
        lifecycle = wm["extra"].get("lifecycle")
        offset, ln, last_ts, n_new = wm["offset"], wm["line"], wm.get("last_ts"), 0
        if os.path.getsize(p) < offset:
            counters["file_shrank_since_watermark"] += 1
            print(f"[history] WARNING {p} is shorter than its watermark ({offset}); not re-read", flush=True)
            continue
        with open(p, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    counters["partial_tail_line_skipped"] += 1
                    break
                offset += len(raw)
                ln += 1
                n_new += 1
                try:
                    d = json.loads(raw)
                except Exception:
                    counters["bad_json"] += 1
                    continue
                counters["lines"] += 1
                if is_broker_echo(d.get("reason")):
                    counters[f"dropped_broker_echo:{cs}"] += 1
                    continue
                ep = _iso_epoch(d.get("ts"))
                typ = d.get("type")
                if typ == "OPEN":
                    lifecycle = f"{sym}@{d.get('ts')}"
                era = era_of(cs, ep)
                eid = "e:" + sha1_text(f"{acct}|{sym}|{d.get('ts')}|{typ}|{d.get('qty')}|{d.get('price')}|{d.get('reason')}")
                extra = {k: v for k, v in d.items() if k not in ("ts", "type", "qty", "price", "value", "reason", "indicators", "prov")}
                store.put(cs, month_of(ep), "live_events",
                          (eid, acct, venue, sym, cs, era, lifecycle, d.get("ts"), ep, typ, d.get("qty"), d.get("price"),
                           d.get("value"), d.get("reason"), _family(d.get("reason")), _prov_tag(d.get("reason")),
                           _dumps(d["prov"]) if d.get("prov") is not None else None,
                           _dumps(d.get("indicators")) if d.get("indicators") else None, source_id, ln,
                           _dumps(extra) if extra else None))
                counters[f"live_events:{cs}:{era}"] += 1
                last_ts = d.get("ts") or last_ts
        counters["files"] += 1
        store.set_wm(source_id, "history", a.host, offset, ln, last_ts, wm["rows"] + n_new, {"lifecycle": lifecycle})
    return counters


# ------------------------------------------------------------------------------------------------ boards

_NUM = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


def xlsx_col_c(path):
    """Stdlib twin of eval_cache_rebuild._xlsx_col_c: column C of every switch sheet (rows >= 3), first value wins."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    out = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels}
        for sh in wb.find("m:sheets", ns):
            title = sh.get("name") or ""
            if "BASELINE_METRICS" in title.upper():
                continue
            t = target.get(sh.get(rel_ns), "")
            sp = t.lstrip("/") if t.startswith("/") else "xl/" + t
            if sp not in names:
                continue
            with z.open(sp) as fh:
                for _ev, el in ET.iterparse(fh):
                    if el.tag != "{%s}row" % ns["m"]:
                        continue
                    r = int(el.get("r") or 0)
                    if r >= 3:
                        vals = {}
                        for c in el.findall("m:c", ns):
                            ref = c.get("r") or ""
                            col = re.sub(r"\d", "", ref)
                            if col not in ("A", "C"):
                                continue
                            ty = c.get("t")
                            v = c.find("m:v", ns)
                            txt = v.text if v is not None else None
                            if ty == "s" and txt is not None:
                                val = shared[int(txt)]
                            elif ty == "b":
                                val = txt == "1"
                            elif ty == "inlineStr":
                                val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
                            elif ty in ("str", "e"):
                                val = txt
                            elif txt is not None and _NUM.match(txt):
                                f = float(txt)
                                val = int(f) if f.is_integer() and "." not in txt and "e" not in txt.lower() else f
                            else:
                                val = txt
                            vals[col] = val
                        sw, cv = vals.get("A"), vals.get("C")
                        if sw and cv not in (None, ""):
                            out.setdefault(str(sw).strip(), cv)
                    el.clear()
    return out


def _md5_file(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _put_board_set(store, cs, month, d, kind, ts, source):
    if not isinstance(d, dict) or not d:
        return None
    cn = Canon(d)
    store.put_set(cs, month, cn.set_id, kind, cn.n, cn.text, ts, source)
    return cn.set_id


def ingest_boards(a, store):
    counters = Counter()
    for g in a.progress_glob or []:
        for f in sorted(glob.glob(os.path.expanduser(g))):
            if not f.endswith("_v14_progress.json"):
                continue
            try:
                d = json.loads(Path(f).read_text())
            except Exception:
                counters["progress_bad_json"] += 1
                continue
            sym = d.get("symside") or os.path.basename(f).split("_v14_progress")[0]
            cs = cat_side_of(sym)
            if cs is None:
                counters["no_cat_side"] += 1
                continue
            ts = os.path.getmtime(f)
            month = month_of(ts)
            src = f"{a.host}:{os.path.abspath(f)}"
            start = _put_board_set(store, cs, month, d.get("initial_overrides"), "board_start", ts, src)
            final = _put_board_set(store, cs, month, d.get("cumulative_overrides"), "board_final", ts, src)
            chain = []
            for k, v in (d.get("done") or {}).items():
                if isinstance(v, dict) and v.get("promoted"):
                    chain.append({"key": k, "delta": v.get("delta"), "cumulative_after": v.get("cumulative_after"),
                                  "promoted_how": v.get("promoted_how"), "promoted_filters": v.get("promoted_filters"),
                                  "vec_gain": v.get("vec_gain"), "trades": v.get("trades")})
            tier = d.get("tier")
            met = d.get("impossible_metrics") or {}
            metrics = {k: d.get(k) for k in ("npz_id", "best_start", "adaptive_baseline", "final_gain_fresh_vec",
                                             "not_compliant", "impossible_reasons", "focus_done")}
            metrics["impossible_metrics"] = met
            metrics["done_n"] = len(d.get("done") or {})
            run = d.get("defaults_round")
            bid = "p:" + sha1_text(f"{sym}|{run}|{final}|{start}|{d.get('cumulative_gain')}|{ts}")
            store.put(cs, month, "boards",
                      (bid, sym, cs, era_of(cs, ts), run, ts, "progress_file_mtime", "progress_json", src, start, final,
                       d.get("bh"), d.get("initial_baseline_gain", d.get("baseline_gain")), d.get("cumulative_gain"),
                       met.get("trades"), met.get("tim_pct"), met.get("max_dd_pct"),
                       tier.get("tier") if isinstance(tier, dict) else tier, d.get("verdict"), None, d.get("window_days"),
                       None, _dumps(chain), _dumps(metrics)))
            counters[f"boards_progress:{cs}"] += 1
            counters["promoted_steps"] += len(chain)
    if a.xlsx_dir:
        xdir = Path(os.path.expanduser(a.xlsx_dir))
        manifests = {}
        for m in sorted(xdir.glob("*_matrix_manifest.json")):
            try:
                manifests[m.name[:-len("_manifest.json")] + ".xlsx"] = (m, json.loads(m.read_text()))
            except Exception:
                counters["manifest_bad_json"] += 1
        xlsx = sorted(xdir.glob("*_matrix.xlsx"))
        names = {p.name for p in xlsx}
        t0 = time.time()
        for i, p in enumerate(xlsx, 1):
            sym_m = re.match(r"^(.+?_(LONG|SHORT))_", p.name)
            sym = sym_m.group(1) if sym_m else None
            cs = cat_side_of(sym)
            if cs is None:
                counters["xlsx_no_cat_side"] += 1
                continue
            man = manifests.get(p.name)
            try:
                md5 = _md5_file(p)
                colc = xlsx_col_c(p) if not a.no_col_c else {}
            except Exception as exc:
                counters["xlsx_unreadable"] += 1
                print(f"[boards] unreadable {p.name}: {type(exc).__name__}: {exc}", flush=True)
                continue
            if man:
                mj = man[1]
                ts, ts_kind = _iso_epoch(mj.get("published_utc")), "published_utc"
                if ts is None:
                    ts, ts_kind = os.path.getmtime(p), "file_mtime"
                met = mj.get("metrics") or {}
            else:
                mj, met = {}, {}
                ts, ts_kind = os.path.getmtime(p), "file_mtime"
            month = month_of(ts)
            src = f"{a.host}:{os.path.abspath(p)}"
            final = _put_board_set(store, cs, month, colc, "board_col_c", ts, src)
            claims = {"filename": p.name, "manifest": str(man[0]) if man else None,
                      "manifest_fields": {k: mj.get(k) for k in ("host", "template", "publish_class", "overrides_md5",
                                                                  "pilot_md5", "span_days", "counts", "name_truth",
                                                                  "nolies_correction", "final_name_before_name_truth")},
                      "metrics": met, "col_c_keys": len(colc), "gain_source": "manifest.metrics" if met else "none"}
            bid = "x:" + sha1_text(f"{sym}|{p.name}|{md5}")
            store.put(cs, month, "boards",
                      (bid, sym, cs, era_of(cs, ts), None, ts, ts_kind, "xlsx_manifest" if man else "xlsx", src, None, final,
                       met.get("bh"), None, met.get("gain_pct"), met.get("trades"), met.get("tim_pct"), met.get("max_dd_pct"),
                       None, None, mj.get("npz_md5"), None, md5, None, _dumps(claims)))
            counters[f"boards_xlsx:{cs}"] += 1
            if i % 200 == 0:
                print(f"[boards] {i}/{len(xlsx)} xlsx · {time.time() - t0:.0f}s", flush=True)
        counters["manifests_without_xlsx"] = sum(1 for n in manifests if n not in names)
    return counters


# ------------------------------------------------------------------------------------------------ trades (ledger replay)

TRADE_COLS = ("trade_no", "bar_entry", "bar_exit", "bars_held", "entry_family", "exit_family", "entry_reason", "exit_reason",
              "pnl_pct", "pnl_dollars", "deployed", "mfe_pct", "mae_pct", "post_exit_32b_pct", "unrealised_mark")


def ingest_trades(a, store):
    """data/learn/trade_ledgers/<CAT_SIDE>/<SYM_SIDE>.jsonl from tools/v18_ledger_replay.py: one `meta` line (-> one
    evals row, provenance `ledger_replay`, eval_id `t:<replay eval_id>`) + `trade` lines (-> trades table). Idempotent."""
    counters = Counter()
    for f in sorted(set(glob.glob(os.path.expanduser(a.source)))):
        source_id = f"{a.host}:{os.path.abspath(f)}"
        meta, trades = None, []
        with open(f, "rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    counters["partial_tail_line_skipped"] += 1
                    break
                try:
                    d = json.loads(raw)
                except Exception:
                    counters["bad_json"] += 1
                    continue
                if d.get("kind") == "meta":
                    meta = d
                elif d.get("kind") == "trade":
                    trades.append(d)
        counters["files"] += 1
        if not meta:
            counters["no_meta"] += 1
            continue
        cs = cat_side_of(meta.get("sym_side"))
        if cs is None or (meta.get("cat_side") and meta["cat_side"] != cs):
            counters["cat_side_unreadable_or_disagrees"] += 1
            continue
        if meta.get("skip") or not meta.get("eval_id"):
            counters["skipped_replay:" + str(meta.get("skip") or "no_eval_id")] += 1
            continue
        ts = _iso_epoch(meta.get("replayed_at"))
        month = month_of(ts)
        eid = "t:" + str(meta["eval_id"])
        extra = {k: v for k, v in meta.items() if k not in ("kind", "sym_side")}
        store.put(cs, month, "evals",
                  (eid, "ledger_replay", meta["sym_side"], cs, era_of(cs, ts), ts, None, None, None, None, "none", None, None,
                   None, None, None, "ledger_replay", meta.get("window_days"), None, None, meta.get("bars"), meta.get("npz_md5"),
                   meta.get("gain_pct"), meta.get("trades"), meta.get("tim_pct"), meta.get("max_dd_pct"), meta.get("pool_sharpe"),
                   meta.get("wr_pct"), None if meta.get("valid") is None else int(bool(meta.get("valid"))),
                   meta.get("invalid_reason"), None, None, None, None, None, None, source_id, _dumps(extra)))
        counters[f"evals:{cs}"] += 1
        for t in trades:
            if t.get("eval_id") != meta["eval_id"]:
                counters["trade_eval_id_disagrees_with_meta"] += 1
                continue
            store.put(cs, month, "trades", (eid, t.get("trade_no"), t.get("bar_entry"), t.get("bar_exit"), t.get("bars_held"),
                                           t.get("entry_family"), t.get("exit_family"), t.get("entry_reason"),
                                           t.get("exit_reason"), t.get("pnl_pct"), t.get("pnl_dollars"), t.get("deployed"),
                                           t.get("mfe_pct"), t.get("mae_pct"), t.get("post_exit_32b_pct"),
                                           None if t.get("unrealised_mark") is None else int(bool(t["unrealised_mark"]))))
            counters[f"trades:{cs}"] += 1
    return counters


# ------------------------------------------------------------------------------------------------ prove

class StoreReader:
    def __init__(self, root):
        self.root = Path(root)
        self.cons = {}
        self.set_ids = None

    def con(self, cat_side, month):
        name = Store.shard_name(cat_side, month)
        if name not in self.cons:
            p = self.root / name
            self.cons[name] = _connect(p, readonly=True) if p.exists() else None
        return self.cons[name]

    def get(self, table, cat_side, month, key, cols):
        c = self.con(cat_side, month)
        if c is None:
            return None
        return c.execute(f"SELECT {cols} FROM {table} WHERE {TABLE_PK[table]}=?", (key,)).fetchone()

    def has_set(self, cat_side, month, set_id):
        c = self.con(cat_side, month)
        return c is not None and c.execute("SELECT 1 FROM sets WHERE set_id=?", (set_id,)).fetchone() is not None

    def all_set_ids(self):
        if self.set_ids is None:
            self.set_ids = set()
            for p in sorted(self.root.glob("experience_*_*.db")):
                c = _connect(p, readonly=True)
                self.set_ids.update(r[0] for r in c.execute("SELECT set_id FROM sets"))
                c.close()
        return self.set_ids


def _same(a, b):
    if a is None and b is None:
        return True
    if isinstance(a, float) or isinstance(b, float):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    return a == b


def prove(a):
    against = Path(os.path.expanduser(a.against)) if a.against else choose_store(a.store)[0]
    rd = StoreReader(against)
    res = Counter()
    samples = []

    def miss(kind, info):
        res[kind] += 1
        if len(samples) < 25:
            samples.append(dict(info, why=kind))

    src_path = os.path.expanduser(a.source)
    if a.kind in ("native",):
        src = _connect(src_path, readonly=True)
        picker = BasePicker()
        sid = _native_source_id(a.host, src_path)
        for _rowid, key, result, created in src.execute("SELECT rowid, key, result, created FROM evals ORDER BY rowid"):
            res["checked"] += 1
            c2 = Counter()
            got = _native_row(key, result, created, sid, picker, c2)
            if got is None:
                miss("missing_not_ingestable:" + ",".join(c2), {"key": key})
                continue
            cs, month, row, _b = got
            r = rd.get("evals", cs, month, row[0], "sym_side, set_id, ts, gain, trades, base_set_id")
            if r is None:
                miss("missing", {"key": key, "shard": Store.shard_name(cs, month)})
                continue
            if not (r[0] == row[2] and r[1] == row[8] and _same(r[3], row[22]) and _same(r[4], row[23])):
                miss("mismatched", {"key": key, "store": list(r), "source": [row[2], row[8], row[5], row[22], row[23]]})
                continue
            if not _same(r[2], row[5]):
                res["same_eval_other_created_ts_first_wins"] += 1
            if r[5] and not rd.has_set(cs, month, r[5]):
                miss("missing_base_set", {"key": key, "base_set_id": r[5]})
        src.close()
    elif a.kind == "rebuilt":
        src = _connect(src_path, readonly=True)
        ids = rd.all_set_ids()
        base_set = {}
        for bid, ov in src.execute("SELECT base_id, overrides FROM bases"):
            res["checked_bases"] += 1
            s = Canon(json.loads(ov)).set_id
            base_set[bid] = s
            if s not in ids:
                miss("missing_base_set", {"base_id": bid})
        for ek, bid, sym, created, metj in src.execute("SELECT ek, base_id, symside, created, metrics FROM evals_compact"):
            res["checked"] += 1
            cs = cat_side_of(sym)
            if cs is None:
                miss("missing_not_ingestable", {"ek": ek})
                continue
            ts = float(created) if created is not None else None
            met = json.loads(metj or "{}")
            r = rd.get("evals", cs, month_of(ts), "r:" + ek[len("rebuilt1:"):], "sym_side, ts, gain, trades, base_set_id")
            if r is None:
                miss("missing", {"ek": ek})
            elif not (r[0] == sym and _same(r[1], ts) and _same(r[2], met.get("gain_pct")) and _same(r[3], met.get("trades"))
                      and r[4] == base_set.get(bid)):
                miss("mismatched", {"ek": ek, "store": list(r)})
        src.close()
    elif a.kind in ("shard",):
        for p in sorted(glob.glob(src_path)):
            s = _connect(p, readonly=True)
            meta = dict(s.execute("SELECT k, v FROM meta").fetchall())
            cs, month = meta.get("cat_side"), meta.get("month")
            dst = rd.con(cs, month) if cs in CAT_SIDES else None
            for t in TABLES:
                if t == "trades":
                    continue
                pk = TABLE_PK[t]
                n_src = s.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                res[f"checked:{t}"] += n_src
                res["checked"] += n_src
                if not n_src:
                    continue
                for (k,) in s.execute(f"SELECT {pk} FROM {t}"):
                    if dst is None or not dst.execute(f"SELECT 1 FROM {t} WHERE {pk}=?", (k,)).fetchone():
                        miss("missing", {"shard": os.path.basename(p), "table": t, "key": k})
            s.close()
    elif a.kind == "log":
        prefixes = []
        for pp in a.orig_prefix or []:
            local, remote = pp.split("=", 1)
            prefixes.append((os.path.abspath(os.path.expanduser(local)), remote))
        for f in sorted(set(glob.glob(src_path))):
            orig = _orig_path(f, prefixes)
            source_id = f"{a.host}:{orig}"
            settled = set()
            for p in rd.root.glob("experience_*_*.db"):
                c = rd.con(*_shard_meta_from_name(p.name))
                if c is None:
                    continue
                for (lines,) in c.execute("SELECT lines FROM settlements WHERE source=?", (source_id,)):
                    settled.update(int(x[0]) for x in json.loads(lines))
            sym_hint = Path(f).name.split("_jump")[0].replace(".jsonl", "")
            with open(f, "rb") as fh:
                ln = 0
                for raw in fh:
                    if not raw.endswith(b"\n"):
                        res["partial_tail_line_not_checked"] += 1
                        break
                    ln += 1
                    try:
                        d = json.loads(raw)
                    except Exception:
                        res["unparseable_line_not_evidence"] += 1
                        continue
                    if not isinstance(d, dict):
                        res["unparseable_line_not_evidence"] += 1
                        continue
                    res["checked"] += 1
                    sym = d.get("sym_side") or sym_hint
                    cs = cat_side_of(sym)
                    if cs is None:
                        miss("missing_not_ingestable", {"src": f, "line": ln})
                        continue
                    ts = _iso_epoch(d.get("ts"))
                    month = month_of(ts)
                    label = d.get("label")
                    if label == "BOARD_CTX":
                        ok = rd.get("boards", cs, month, "b:" + sha1_text(f"{a.host}|{orig}|{ln}|{d.get('ts')}"), "1")
                    elif label == "ROW_DONE":
                        ok = rd.get("row_done", cs, month, "d:" + sha1_text(f"{a.host}|{orig}|{ln}|{d.get('ts')}"), "1")
                    elif d.get("gain_pct") is None:
                        ok = rd.get("log_other", cs, month, "o:" + sha1_text(f"{a.host}|{orig}|{ln}"), "1")
                    else:
                        lid, rid = _log_eval_ids(a.host, orig, ln, d)
                        ok = ln in settled
                        if not ok:
                            r = rd.get("evals", cs, month, lid, "gain, trades")
                            if r is None and a.host == "s2":
                                r = rd.get("evals", cs, month, rid, "gain, trades")
                            if r is not None and not (_same(r[0], d.get("gain_pct")) and _same(r[1], d.get("trades"))):
                                miss("mismatched", {"src": f, "line": ln})
                                continue
                            ok = r is not None
                    if not ok:
                        miss("missing", {"src": f, "line": ln, "label": label})
    elif a.kind == "history":
        for venue, acct, p in history_files(src_path):
            sym = p.name[:-len(".jsonl")]
            cs = cat_side_of(sym, venue)
            with open(p, "rb") as fh:
                for raw in fh:
                    if not raw.endswith(b"\n"):
                        res["partial_tail_line_not_checked"] += 1
                        break
                    try:
                        d = json.loads(raw)
                    except Exception:
                        res["unparseable_line_not_evidence"] += 1
                        continue
                    res["checked"] += 1
                    if is_broker_echo(d.get("reason")):
                        res["broker_echo_dropped_by_rule"] += 1
                        continue
                    eid = "e:" + sha1_text(f"{acct}|{sym}|{d.get('ts')}|{d.get('type')}|{d.get('qty')}|{d.get('price')}|{d.get('reason')}")
                    if rd.get("live_events", cs, month_of(_iso_epoch(d.get("ts"))), eid, "1") is None:
                        miss("missing", {"file": str(p), "ts": d.get("ts")})
    else:
        raise SystemExit(f"prove: unknown --kind {a.kind}")
    missing = sum(v for k, v in res.items() if k.startswith("missing"))
    mism = res.get("mismatched", 0)
    try:
        st = os.stat(src_path)
        sbytes, smtime = st.st_size, st.st_mtime
    except OSError:
        sbytes = smtime = None
    rep = {"tool": "tools/v18_experience.py prove", "at": _utc(), "host": socket.gethostname(), "source": src_path,
           "source_host": a.host, "kind": a.kind, "against": str(against), "counts": dict(res), "missing": missing,
           "mismatched": mism, "reclaimable": missing == 0 and mism == 0 and res.get("checked", 0) > 0,
           "source_bytes": sbytes, "source_mtime": smtime, "samples": samples}
    out = a.report or str(ROOT / "data" / "reports" / f"experience_prove_{a.kind}_{sha1_text(src_path)[:10]}.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(rep, indent=1, default=str))
    ixp = against / INDEX_NAME
    if ixp.exists():
        try:
            ix = _connect(ixp)
            ix.execute("INSERT INTO proofs (source, kind, against, at, checked, missing, mismatched, report, source_bytes, source_mtime)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?)", (src_path, a.kind, str(against), rep["at"], res.get("checked", 0),
                                                       missing, mism, out, sbytes, smtime))
            ix.close()
        except sqlite3.OperationalError as exc:
            print(f"[prove] could not record proof in index ({exc})", flush=True)
    print(json.dumps({k: rep[k] for k in ("source", "kind", "counts", "missing", "mismatched", "reclaimable")}, default=str))
    print(f"[prove] report -> {out}")
    return 0 if rep["reclaimable"] else 3


def _shard_meta_from_name(name):
    m = re.match(r"^experience_((?:CRYPTO|STOCKS)_(?:LONG|SHORT))_(\w+)\.db$", name)
    return (m.group(1), m.group(2)) if m else (None, None)


# ------------------------------------------------------------------------------------------------ cold / reclaim

def _sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def _db_census(p):
    out = {}
    try:
        c = _connect(p, readonly=True)
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            out[t] = {"rows": c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]}
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            for col in ("created", "ts", "epoch"):
                if col in cols:
                    mn, mx = c.execute(f"SELECT min({col}), max({col}) FROM {t}").fetchone()
                    out[t]["range"] = [col, _utc(mn) if mn else None, _utc(mx) if mx else None]
                    break
        c.close()
    except sqlite3.DatabaseError as exc:
        out["_error"] = f"{type(exc).__name__}: {exc}"
    return out


def cold(a):
    src = os.path.expanduser(a.source)
    out = os.path.expanduser(a.out)
    plan = {"source": src, "out": out, "cmd": ["zstd", "-19", "--long=27", "-T0", "-q", "-o", out, src]}
    if not a.apply:
        print(json.dumps(dict(plan, dry_run=True), indent=1))
        return 0
    if os.path.exists(out):
        raise SystemExit(f"[cold] refusing to overwrite {out}")
    census = _db_census(src) if src.endswith(".db") else {}
    src_sha = _sha256_file(src) if a.hash_source else None
    t0 = time.time()
    subprocess.run(plan["cmd"], check=True)
    subprocess.run(["zstd", "-t", "-q", "--long=27", out], check=True)
    man = {"tool": "tools/v18_experience.py cold", "at": _utc(), "host": socket.gethostname(), "source": src,
           "source_bytes": os.path.getsize(src), "source_sha256": src_sha, "zst": out, "zst_bytes": os.path.getsize(out),
           "zst_sha256": _sha256_file(out), "tables": census, "zstd_test": "ok", "secs": round(time.time() - t0, 1),
           "decompress": f"zstd -d --long=27 {out} -o <dest>"}
    Path(out + ".manifest.json").write_text(json.dumps(man, indent=1))
    store = Path(a.against) if a.against else None
    if store and (store / INDEX_NAME).exists():
        ix = _connect(store / INDEX_NAME)
        ix.execute("INSERT OR REPLACE INTO colds (zst, source, sha256, bytes, manifest, at) VALUES (?,?,?,?,?,?)",
                   (out, src, man["zst_sha256"], man["zst_bytes"], out + ".manifest.json", man["at"]))
        ix.close()
    print(json.dumps(man, indent=1))
    return 0


def reclaim(a):
    """Refuses unless (1) every proof report names this source, missing 0, mismatched 0, and the source still has the
    proven size; (2) the cold copy's manifest sha256 equals the sha256 of the zst as it exists now; (3) the zst lies on
    a REAL mount (os.path.ismount of its drive and st_dev != the root fs). Dry-run unless --apply."""
    src = os.path.expanduser(a.source)
    zst = os.path.expanduser(a.cold)
    why = []
    reports = list(a.proof or [])
    if not reports:
        why.append("no --proof report given")
    for r in reports:
        try:
            rep = json.loads(Path(r).read_text())
        except Exception as exc:
            why.append(f"proof {r} unreadable ({exc})")
            continue
        if os.path.abspath(os.path.expanduser(rep.get("source", ""))) != os.path.abspath(src):
            why.append(f"proof {r} is for {rep.get('source')}, not {src}")
        if rep.get("missing") != 0 or rep.get("mismatched") != 0 or not rep.get("reclaimable"):
            why.append(f"proof {r}: missing={rep.get('missing')} mismatched={rep.get('mismatched')}")
        if not a.remote_host and rep.get("source_bytes") is not None and os.path.exists(src) and os.path.getsize(src) != rep["source_bytes"]:
            why.append(f"proof {r}: source size changed since the proof ({rep['source_bytes']} -> {os.path.getsize(src)})")
    man_p = zst + ".manifest.json"
    if not os.path.exists(zst) or not os.path.exists(man_p):
        why.append(f"cold copy or manifest missing ({zst})")
    else:
        man = json.loads(Path(man_p).read_text())
        actual = _sha256_file(zst)
        if man.get("zst_sha256") != actual:
            why.append(f"cold sha256 mismatch (manifest {man.get('zst_sha256')} vs file {actual})")
        drive = zst
        while drive not in ("/", "") and not os.path.ismount(drive):
            drive = os.path.dirname(drive)
        if not _real_mount(drive):
            why.append(f"cold copy is not on a real mount (mount point {drive!r}, st_dev equals the root fs or not a mount)")
    verdict = {"source": src, "remote_host": a.remote_host, "cold": zst, "refused": bool(why), "why": why,
               "apply": bool(a.apply)}
    print(json.dumps(verdict, indent=1))
    if why:
        return 4
    if not a.apply:
        print("[reclaim] all checks pass; dry-run: nothing deleted (re-run with --apply)")
        return 0
    if a.remote_host:
        subprocess.run(["ssh", "-o", "BatchMode=yes", a.remote_host, "rm", "--", src], check=True)
    else:
        os.remove(src)
    print(f"[reclaim] removed {src}")
    return 0


# ------------------------------------------------------------------------------------------------ digest / audit

def digest(a):
    root = choose_store(a.store)[0]
    out_dir = Path(a.out_dir or (ROOT / "data" / "learn" / "store"))
    written = {}
    for cs in CAT_SIDES:
        pairs = {}
        live = Counter()
        n_evals = Counter()
        for p in sorted(root.glob(f"experience_{cs}_*.db")):
            c = _connect(p, readonly=True)
            for sw, cand, fk, fv, prov, n, nsym, npos, sdelta, ndelta in c.execute(
                    "SELECT switch, cand, filter_key, filter_val, provenance, count(*), count(DISTINCT sym_side),"
                    " sum(CASE WHEN cum_before IS NOT NULL AND gain > cum_before THEN 1 ELSE 0 END),"
                    " sum(CASE WHEN cum_before IS NOT NULL THEN gain - cum_before END),"
                    " sum(CASE WHEN cum_before IS NOT NULL THEN 1 ELSE 0 END)"
                    " FROM evals WHERE switch IS NOT NULL GROUP BY switch, cand, filter_key, filter_val, provenance"):
                k = f"{sw}={cand}|{fk}={fv}" if fk else f"{sw}={cand}"
                e = pairs.setdefault(k, {"n": 0, "n_syms_max_shard": 0, "n_with_delta": 0, "n_pos": 0, "sum_delta": 0.0,
                                         "by_provenance": {}})
                e["n"] += n
                e["n_syms_max_shard"] = max(e["n_syms_max_shard"], nsym)
                e["n_with_delta"] += ndelta or 0
                e["n_pos"] += npos or 0
                e["sum_delta"] += sdelta or 0.0
                e["by_provenance"][prov] = e["by_provenance"].get(prov, 0) + n
                n_evals[prov] += n
            for sw, cand, nl in c.execute("SELECT switch, cand, sum(n_lines) FROM settlements GROUP BY switch, cand"):
                e = pairs.setdefault(f"{sw}={cand}", {"n": 0, "n_syms_max_shard": 0, "n_with_delta": 0, "n_pos": 0,
                                                      "sum_delta": 0.0, "by_provenance": {}})
                e["inert_identity_lines"] = e.get("inert_identity_lines", 0) + (nl or 0)
            for fam, typ, n in c.execute("SELECT family, type, count(*) FROM live_events WHERE era='hermetic'"
                                         " AND type IN ('CLOSE','REDUCE') GROUP BY family, type"):
                live[f"{fam}|{typ}"] += n
            c.close()
        doc = {"cat_side": cs, "at": _utc(), "store": str(root),
               "warning": "IN-SAMPLE board evidence counts (doc 17 principle 10): not forward weights; never averaged across cat_sides",
               "n_evals_by_provenance": dict(n_evals), "pairs": pairs, "live_hermetic_close_reduce_by_family": dict(live)}
        blob = json.dumps(doc, separators=(",", ":"), default=str)
        written[cs] = {"pairs": len(pairs), "bytes": len(blob)}
        if a.apply:
            out_dir.mkdir(parents=True, exist_ok=True)
            tmp = out_dir / f".store_digest_{cs}.json.tmp"
            tmp.write_text(blob)
            os.replace(tmp, out_dir / f"store_digest_{cs}.json")
    print(json.dumps({"store": str(root), "apply": bool(a.apply), "digests": written}, indent=1))
    return 0


def audit(a):
    root = choose_store(a.store)[0]
    rep = {"tool": "tools/v18_experience.py audit", "at": _utc(), "host": socket.gethostname(), "store": str(root),
           "shards": {}, "totals_by_cat_side": {}, "problems": []}
    tot = {cs: Counter() for cs in CAT_SIDES}
    for p in sorted(root.glob("experience_*_*.db")):
        cs, month = _shard_meta_from_name(p.name)
        if cs is None:
            rep["problems"].append(f"unexpected file name {p.name}")
            continue
        c = _connect(p, readonly=True)
        meta = dict(c.execute("SELECT k, v FROM meta").fetchall())
        if meta.get("cat_side") != cs:
            rep["problems"].append(f"{p.name}: meta cat_side {meta.get('cat_side')} != file name {cs}")
        s = {"bytes": p.stat().st_size, "tables": {}}
        for t in TABLES:
            n = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            s["tables"][t] = n
            tot[cs][t] += n
        foreign = c.execute("SELECT count(*) FROM evals WHERE cat_side != ?", (cs,)).fetchone()[0]
        foreign += c.execute("SELECT count(*) FROM live_events WHERE cat_side != ?", (cs,)).fetchone()[0]
        if foreign:
            rep["problems"].append(f"{p.name}: {foreign} rows of another cat_side")
        s["evals_by_provenance_era"] = {f"{pv}|{er}": n for pv, er, n in
                                        c.execute("SELECT provenance, era, count(*) FROM evals GROUP BY provenance, era")}
        s["evals_by_delta_rule"] = dict(c.execute("SELECT delta_rule, count(*) FROM evals GROUP BY delta_rule").fetchall())
        s["live_by_era"] = dict(c.execute("SELECT era, count(*) FROM live_events GROUP BY era").fetchall())
        orph = c.execute("SELECT count(*) FROM evals e WHERE e.base_set_id IS NOT NULL AND NOT EXISTS"
                         " (SELECT 1 FROM sets s WHERE s.set_id = e.base_set_id)").fetchone()[0]
        s["orphan_evals_without_base_set"] = orph
        borph = c.execute("SELECT count(*) FROM boards b WHERE (b.final_set_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sets s"
                          " WHERE s.set_id=b.final_set_id)) OR (b.start_set_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sets s"
                          " WHERE s.set_id=b.start_set_id))").fetchone()[0]
        s["orphan_boards_without_set"] = borph
        torph = c.execute("SELECT count(*) FROM trades t WHERE NOT EXISTS (SELECT 1 FROM evals e WHERE e.eval_id=t.eval_id)").fetchone()[0]
        s["orphan_trades_without_eval"] = torph
        for k in ("orphan_evals_without_base_set", "orphan_boards_without_set", "orphan_trades_without_eval"):
            if s[k]:
                rep["problems"].append(f"{p.name}: {k}={s[k]}")
                tot[cs][k] += s[k]
        if a.quick_check:
            s["quick_check"] = c.execute("PRAGMA quick_check").fetchone()[0]
            if s["quick_check"] != "ok":
                rep["problems"].append(f"{p.name}: quick_check {s['quick_check']}")
        c.close()
        rep["shards"][p.name] = s
    rep["totals_by_cat_side"] = {cs: dict(v) for cs, v in tot.items()}
    ixp = root / INDEX_NAME
    if ixp.exists():
        ix = _connect(ixp, readonly=True)
        rep["watermarks"] = {"n": ix.execute("SELECT count(*) FROM watermarks").fetchone()[0],
                             "by_kind": dict(ix.execute("SELECT kind, count(*) FROM watermarks GROUP BY kind").fetchall()),
                             "rows_by_kind": dict(ix.execute("SELECT kind, sum(rows) FROM watermarks GROUP BY kind").fetchall())}
        rep["ingests"] = [dict(zip(("source", "kind", "host", "started", "finished", "counters"), r)) for r in
                          ix.execute("SELECT source, kind, host, started, finished, counters FROM ingests ORDER BY id")]
        rep["proofs"] = [dict(zip(("source", "kind", "at", "checked", "missing", "mismatched", "report"), r)) for r in
                         ix.execute("SELECT source, kind, at, checked, missing, mismatched, report FROM proofs ORDER BY id")]
        rep["colds"] = [dict(zip(("zst", "source", "sha256", "bytes"), r)) for r in
                        ix.execute("SELECT zst, source, sha256, bytes FROM colds")]
        ix.close()
    else:
        rep["problems"].append(f"no {INDEX_NAME} in {root}")
    out = a.out or str(ROOT / "data" / "reports" / "experience_audit.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(rep, indent=1, default=str))
    if rep["problems"]:
        Path(out + ".ALERT").write_text("\n".join(rep["problems"]) + "\n")
    print(json.dumps({"store": str(root), "shards": len(rep["shards"]), "totals_by_cat_side": rep["totals_by_cat_side"],
                      "problems": rep["problems"][:20]}, indent=1))
    print(f"[audit] report -> {out}")
    return 1 if rep["problems"] else 0


def where(a):
    root, why = choose_store(a.store)
    print(json.dumps({"store": str(root), "why": why, "candidates": store_candidates(), "min_free_gb": MIN_FREE_GB}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------ main

def do_ingest(a):
    root, why = choose_store(a.store)
    store = Store(root, apply=a.apply)
    started = _utc()
    print(f"[ingest] kind={a.kind} source={a.source} store={root} ({why}) apply={bool(a.apply)}", flush=True)
    fn = {"native": ingest_native, "rebuilt": ingest_rebuilt, "shard": ingest_shard, "log": ingest_log,
          "history": ingest_history, "boards": ingest_boards, "trades": ingest_trades}[a.kind]
    t0 = time.time()
    counters = fn(a, store)
    store.finalize(build_indexes=a.build_indexes)
    counters = dict(counters)
    counters["inserted"] = dict(store.inserted)
    counters["offered"] = dict(store.offered)
    counters["secs"] = round(time.time() - t0, 1)
    store.log_ingest(a.source or a.kind, a.kind, a.host, started, counters)
    store.close()
    print(json.dumps(counters, indent=1, sort_keys=True, default=str))
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps({"kind": a.kind, "source": a.source, "store": str(root), "apply": bool(a.apply),
                                              "started": started, "finished": _utc(), "host": socket.gethostname(),
                                              "counters": counters}, indent=1, sort_keys=True, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--store", help="experience store dir (default: location rule, see `where`)")
        p.add_argument("--host", default="mac", help="host the SOURCE lives on / was read from (mac|s1|s2)")
        p.add_argument("--report")

    p = sub.add_parser("ingest")
    common(p)
    p.add_argument("--kind", required=True, choices=("native", "rebuilt", "shard", "log", "history", "boards", "trades"))
    p.add_argument("--from", dest="source", default="")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--orig-prefix", action="append", help="LOCAL=REMOTE path prefix map for pulled delta logs")
    p.add_argument("--progress-glob", action="append")
    p.add_argument("--xlsx-dir")
    p.add_argument("--no-col-c", action="store_true")
    p.add_argument("--history-root", default=str(ROOT / "data"))
    p.add_argument("--build-indexes", action="store_true")
    p = sub.add_parser("prove")
    common(p)
    p.add_argument("--kind", required=True, choices=("native", "rebuilt", "shard", "log", "history"))
    p.add_argument("--from", dest="source", required=True)
    p.add_argument("--against")
    p.add_argument("--orig-prefix", action="append")
    p = sub.add_parser("cold")
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--against", help="store whose index records the cold copy")
    p.add_argument("--hash-source", action="store_true")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("reclaim")
    p.add_argument("--source", required=True)
    p.add_argument("--cold", required=True)
    p.add_argument("--proof", action="append")
    p.add_argument("--remote-host")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("digest")
    p.add_argument("--store")
    p.add_argument("--out-dir")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("audit")
    p.add_argument("--store")
    p.add_argument("--out")
    p.add_argument("--quick-check", action="store_true")
    p = sub.add_parser("where")
    p.add_argument("--store")
    a = ap.parse_args(argv)
    if a.cmd == "ingest":
        if a.kind in ("native", "rebuilt", "shard", "log", "trades") and not a.source:
            ap.error("--from is required for this kind")
        return do_ingest(a)
    return {"prove": prove, "cold": cold, "reclaim": reclaim, "digest": digest, "audit": audit, "where": where}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
