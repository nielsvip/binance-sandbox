"""Single position writer law (USER 2026-10-14, REAL MONEY).

"ez_manage should NEVER write any position related data. NOR should ez_positions_realtime, which is ONLY
allowed to kick in momentarily if ez_positions_service fails; NO opened_at can ever be rewritten."

Roles (per process, from EZ_POSITION_WRITER_ROLE or the launched script name):
  service  - ez_positions_service_main.py <acct>: the ONLY writer of <acct>/{long,short}_positions.json.
  failover - ez_positions_realtime.py <acct>: writes ONLY while the service heartbeat is stale (> N s).
  manage   - ez_manage.py: NEVER writes position files. Its bookkeeping fields go to a per-side overlay
             (.manage_overlay_<side>.json) that the writer merges; its memory re-syncs identity fields from files.
  other    - tools/tests: unchanged behaviour (opened_at immutability still enforced).

Every allowed write passes enforce_identity(): a held position's non-empty opened_at is never changed.
"""
import contextvars
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("position_writer_guard")
ROLE_ENV = "EZ_POSITION_WRITER_ROLE"
STALE_ENV = "EZ_SERVICE_HEARTBEAT_STALE_S"
DEFAULT_STALE_S = 60.0  # service REST cycles reach 30+ s on slow API (men/fin 2026-10-14) -> no false takeovers
FILE_FRESH_S = 30.0
HEARTBEAT_NAME = ".positions_service_heartbeat.json"
POSITION_FILE_NAMES = ("long_positions.json", "short_positions.json")
OVERLAY_FIELDS = ("fixed_stop_price", "exchange_stop_order_id", "r1_stop_price", "is_hedge", "hedge_for", "last_signal", "sba_add_count", "last_sba_time", "sba_total_added_usd", "entry_price_before_sba")
_ROLE_BY_SCRIPT = {"ez_manage.py": "manage", "ez_positions_realtime.py": "failover", "ez_positions_service_main.py": "service"}
_identity_write_ok = contextvars.ContextVar("pwg_identity_write_ok", default=False)
_last_log = {}


def process_role():
    r = os.environ.get(ROLE_ENV, "").strip().lower()
    if r:
        return r
    try:
        return _ROLE_BY_SCRIPT.get(os.path.basename(sys.argv[0] if sys.argv else ""), "other")
    except Exception:
        return "other"


def stale_after_s():
    try:
        return float(os.environ.get(STALE_ENV, DEFAULT_STALE_S))
    except Exception:
        return DEFAULT_STALE_S


def is_position_file(path):
    try:
        return Path(path).name.lower() in POSITION_FILE_NAMES
    except Exception:
        return False


def _rate_limited(key, every_s=60.0):
    now = time.time()
    if now - _last_log.get(key, 0.0) < every_s:
        return False
    _last_log[key] = now
    return True


def to_epoch(v):
    if v is None or v == "" or v == "None":
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v)
        if hasattr(v, "timestamp"):
            if getattr(v, "tzinfo", None) is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.timestamp()
        s = str(v).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def _amt(row):
    try:
        return abs(float((row or {}).get("positionAmt", 0) or 0))
    except Exception:
        return 0.0


def read_json(path):
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _atomic_json(path, data):
    p = Path(path)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def heartbeat_path(account_dir):
    return Path(account_dir) / HEARTBEAT_NAME


def write_heartbeat(account_dir, extra=None):
    body = {"ts": time.time(), "iso": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), "role": process_role()}
    if extra:
        body.update(extra)
    try:
        _atomic_json(heartbeat_path(account_dir), body)
        return True
    except Exception as e:
        logger.warning(f"[SINGLE_WRITER] heartbeat write failed {account_dir}: {e!r}")
        return False


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def heartbeat_age(account_dir, now=None):
    """Age of the service heartbeat; inf when missing, unreadable or its pid is dead."""
    hb = read_json(heartbeat_path(account_dir))
    ts = hb.get("ts")
    if not ts:
        return float("inf")
    if hb.get("pid") and not _pid_alive(hb.get("pid")):
        return float("inf")
    return (time.time() if now is None else now) - float(ts)


def service_alive(account_dir, now=None):
    return heartbeat_age(account_dir, now) <= stale_after_s()


def may_write(path, role=None, now=None):
    role = role or process_role()
    if role == "manage":
        return False, "ez_manage never writes position files"
    if role == "failover":
        age = heartbeat_age(Path(path).parent, now)
        if age <= stale_after_s():
            return False, f"service alive (heartbeat {age:.0f}s)"
        return True, f"service heartbeat stale ({age:.0f}s) - failover write"
    return True, role


def overlay_path(path):
    p = Path(path)
    side = p.name.lower().split("_")[0]
    return p.with_name(f".manage_overlay_{side}.json")


def write_overlay(path, rows):
    """ez_manage's bookkeeping (stop ids, frozen stop level, hedge flags, max_gain) for held positions — NOT a position file."""
    out = {}
    for k, r in (rows or {}).items():
        if not isinstance(r, dict) or _amt(r) <= 0:
            continue
        o = {f: r.get(f) for f in OVERLAY_FIELDS if f in r}
        o["opened_at"] = r.get("opened_at")
        o["positionAmt"] = r.get("positionAmt")
        o["max_gain"] = r.get("max_gain")
        out[k] = o
    try:
        _atomic_json(overlay_path(path), {"written_at": time.time(), "pid": os.getpid(), "rows": out})
        return True
    except Exception as e:
        logger.warning(f"[SINGLE_WRITER] overlay write failed {path}: {e!r}")
        return False


def _same_lifecycle(a, b):
    ea, eb = to_epoch(a), to_epoch(b)
    return ea is not None and eb is not None and abs(ea - eb) <= 1.0


def apply_overlay(rows, overlay):
    """Merge ez_manage bookkeeping into the writer's rows (same lifecycle only; max_gain only increases)."""
    merged = 0
    ov_rows = (overlay or {}).get("rows") or {}
    for k, r in (rows or {}).items():
        ov = ov_rows.get(k)
        if not isinstance(r, dict) or not isinstance(ov, dict) or _amt(r) <= 0:
            continue
        if not _same_lifecycle(ov.get("opened_at"), r.get("opened_at")):
            continue
        for f in OVERLAY_FIELDS:
            v = ov.get(f)
            if v not in (None, "", 0, 0.0, False) and r.get(f) != v:
                r[f] = v
                merged += 1
        try:
            if float(ov.get("max_gain") or 0) > float(r.get("max_gain") or 0):
                r["max_gain"] = float(ov.get("max_gain"))
                merged += 1
        except Exception:
            pass
    return merged


def enforce_identity(rows, disk_rows):
    """opened_at is immutable while a position is held: disk held + new held + disk opened_at set -> disk value wins.
    Returns the list of rejected changes (key, disk, attempted)."""
    rejected = []
    for k, n in (rows or {}).items():
        o = (disk_rows or {}).get(k)
        if not isinstance(n, dict) or not isinstance(o, dict):
            continue
        if _amt(o) <= 0 or _amt(n) <= 0:
            continue
        o_oa = o.get("opened_at")
        if to_epoch(o_oa) is None:
            continue
        n_oa = n.get("opened_at")
        if to_epoch(n_oa) is None or not _same_lifecycle(o_oa, n_oa):
            n["opened_at"] = o_oa
            rejected.append((k, o_oa, n_oa))
    return rejected


def _alert(kind, detail, base_dir=None):
    try:
        if not base_dir and os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("POSITIONS_BASE_DIR"):
            return
        d = Path(base_dir or os.environ.get("POSITIONS_BASE_DIR") or Path(__file__).resolve().parent)
        a = d / "data" / "alerts"
        a.mkdir(parents=True, exist_ok=True)
        with open(a / "position_writer_guard.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "pid": os.getpid(), "role": process_role(), **detail}, default=str) + "\n")
    except Exception:
        pass


def before_write(path, rows, role=None, log=None, overlay=True):
    """THE hook every positions-file writer calls right before replacing the file. Returns True to proceed.
    overlay=True only where rows are the caller's in-memory positions (ez_positions.atomic_save)."""
    log = log or logger
    if not is_position_file(path) or not isinstance(rows, dict):
        return True
    role = role or process_role()
    if role == "manage":
        if overlay:
            write_overlay(path, rows)
        if _rate_limited(f"manage:{path}"):
            log.warning(f"[SINGLE_WRITER] ez_manage position write to {path} REFUSED (bookkeeping diverted to {overlay_path(path).name})")
        return False
    ok, why = may_write(path, role)
    if not ok:
        if _rate_limited(f"deny:{path}"):
            log.info(f"[SINGLE_WRITER] {role} write to {path} skipped: {why}")
        return False
    apply_overlay(rows, read_json(overlay_path(path)))
    rejected = enforce_identity(rows, read_json(path))
    for k, disk_oa, new_oa in rejected:
        log.critical(f"🛑 [OPENED_AT_CHANGE_REJECTED] {k}: kept {disk_oa}, refused {new_oa} (role={role} pid={os.getpid()})")
        _alert("OPENED_AT_CHANGE_REJECTED", {"key": k, "kept": disk_oa, "refused": new_oa, "file": str(path)}, Path(path).parent.parent)
    return True


@contextmanager
def identity_write_allowed():
    tok = _identity_write_ok.set(True)
    try:
        yield
    finally:
        _identity_write_ok.reset(tok)


def identity_write_is_allowed():
    return bool(_identity_write_ok.get())


def files_fresh_from_writer(account_dir, now=None):
    """True when both side files were last written by an allowed writer (not manage) within FILE_FRESH_S."""
    now = time.time() if now is None else now
    for side in ("long", "short"):
        m = read_json(Path(account_dir) / f".{side}_positions.json.meta.json")
        if not m.get("written_at") or now - float(m["written_at"]) > FILE_FRESH_S:
            return False
        if ":manage:" in str(m.get("writer", "")):
            return False
    return True


def resync_memory_from_files(account_positions, account_dir, log=None, require_fresh=True, fields=("opened_at", "entry_price", "max_gain")):
    """Adopt opened_at / entry_price / max_gain(up only) from the single writer's files into a reader's memory.
    Only for held positions whose quantity matches the file (same state). Returns list of changes."""
    log = log or logger
    if require_fresh and not files_fresh_from_writer(account_dir):
        return []
    rows = {}
    for side in ("long", "short"):
        rows.update(read_json(Path(account_dir) / f"{side}_positions.json"))
    changes = []
    for k, pos in list((account_positions or {}).items()):
        r = rows.get(k)
        if pos is None or not isinstance(r, dict):
            continue
        try:
            m_amt = abs(float(getattr(pos, "positionAmt", 0) or 0))
        except Exception:
            continue
        f_amt = _amt(r)
        if m_amt <= 0 or f_amt <= 0 or abs(m_amt - f_amt) > max(1e-9, f_amt * 1e-6):
            continue
        with identity_write_allowed():
            f_oa = r.get("opened_at")
            if "opened_at" in fields and to_epoch(f_oa) is not None and not _same_lifecycle(getattr(pos, "opened_at", None), f_oa):
                old = getattr(pos, "opened_at", None)
                try:
                    pos.opened_at = datetime.fromtimestamp(to_epoch(f_oa), timezone.utc)
                    changes.append((k, "opened_at", old, f_oa))
                except Exception:
                    pass
            try:
                f_ep = float(r.get("entry_price") or 0)
                m_ep = float(getattr(pos, "entry_price", 0) or 0)
                if "entry_price" in fields and f_ep > 0 and abs(f_ep - m_ep) > f_ep * 1e-9:
                    pos.entry_price = f_ep
                    changes.append((k, "entry_price", m_ep, f_ep))
            except Exception:
                pass
            try:
                f_mg = float(r.get("max_gain") or 0)
                if "max_gain" in fields and _same_lifecycle(getattr(pos, "opened_at", None), f_oa) and f_mg > float(getattr(pos, "max_gain", 0) or 0):
                    old_mg = getattr(pos, "max_gain", 0)
                    pos.max_gain = f_mg
                    changes.append((k, "max_gain", old_mg, f_mg))
            except Exception:
                pass
    for c in changes:
        log.warning(f"[SINGLE_WRITER_RESYNC] {c[0]}.{c[1]}: memory {c[2]} -> file {c[3]}")
    return changes
