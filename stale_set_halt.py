"""STALE-SET 2-LOSS HALT (USER 2026-10-13/14, BACKTEST_BIBLE §100).

USER (verbatim): "NO blocking loss closes if the vector tells you to" + "but if the vector has not been refreshed for over
10 days and 2 consecutive trades close at a loss, trading needs to be halted for the sym_side until recalculated with
latest npz."

Per sym_side (crypto venue "ez", stocks venue "tradier"):
  vector age  = now - recalc_epoch(active per_sym set)            (see recalc_anchor(); no set = cat_side defaults = fresh)
  last 2      = the last 2 FULL closes of the sym_side since the active set went live (all accounts of the venue)
  HALT        = age > STALE_SET_HALT_DAYS and both last STALE_SET_HALT_LOSSES closes net < 0
A halt refuses ONLY position increases (OPEN / AUGMENT / REENTRY / ENTRY / BUY-to-open) in execute_now. CLOSE / REDUCE /
hedge-close are never touched. The halt persists in data/stale_set_halt/halts_{venue}.json (survives restarts), is logged +
appended to data/alerts/stale_set_halt_{venue}.jsonl, and the sym_side is pinned on S1's scheduler operator queue
(data/v15_sched_pinned_sides.json "sides"[0]) so its board is recalculated on the latest JIT NPZ. Release is automatic once a
NEW board published after the halt has been applied live (per_sym_active campaign_ts > halted_at AND the set fingerprint
changed AND the board publish time > halted_at); the loss window then restarts at the new apply (counter reset).

Pure functions (evaluate / trailing_losses / recalc_anchor) take explicit inputs so tests run on synthetic ledgers.
Every IO error fails OPEN (no halt) — the halt is an additional guard, never a reason for an exception in execute_now.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
VENUE_ACCOUNTS = {"ez": ("ang", "fin", "men", "flz", "inf"), "tradier": ("tra", "trb", "trc")}
DEFAULT_FEE_PCT = {"ez": 0.04, "tradier": 0.0}  # same fees_pct the per_trade_logger ledger records per venue
ENTRY_TOKENS = ("OPEN", "AUGMENT", "REENTRY", "ENTRY", "REVERSE")
EXIT_TOKENS = ("CLOSE", "REDUCE", "EXIT", "STOP", "PROFIT_TAKE")
PARTIAL_TOKENS = ("TIERED", "PARTIAL", "SCALE_OUT")
_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}


def _cfg(cfg: Any, key: str, default: Any) -> Any:
    try:
        v = getattr(cfg, key) if cfg is not None and hasattr(cfg, key) else default
    except Exception:
        v = default
    return default if v is None else v


def settings(cfg: Any = None) -> Dict[str, Any]:
    return {"enabled": bool(_cfg(cfg, "STALE_SET_HALT_ENABLED", True)), "days": float(_cfg(cfg, "STALE_SET_HALT_DAYS", 10.0)), "losses": int(_cfg(cfg, "STALE_SET_HALT_LOSSES", 2))}


def base_path(cfg: Any = None) -> Path:
    try:
        b = getattr(cfg, "BASE_PATH", None) if cfg is not None else None
        return Path(b) if b else ROOT
    except Exception:
        return ROOT


def iso_epoch(ts: Any) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts) if ts > 0 else None
    try:
        t = str(ts).strip().replace("Z", "+00:00")
        if not t:
            return None
        d = _dt.datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def _iso(epoch: Optional[float]) -> str:
    if not epoch:
        return ""
    return _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sym_side(symbol: str, position_side: str) -> str:
    return f"{str(symbol or '').upper()}_{str(position_side or '').upper()}"


def is_entry_action(action: Any) -> bool:
    a = str(action or "").upper()
    if not a:
        return False
    if any(t in a for t in ("CLOSE", "REDUCE")):
        return False
    return a == "BUY" or any(t in a for t in ENTRY_TOKENS)


# ─────────────────────────────── vector age (recalculation anchor) ───────────────────────────────
_TAG_DATE = re.compile(r"(20\d{2})(\d{2})(\d{2})(?!\d)")
_TAG_EPOCH = re.compile(r"continuous_(1\d{9})")


def tag_epoch(tag: Any) -> Optional[float]:
    """Board date encoded in a winning_tag: v15_30D_20261013 / pilot30d_20261012 / live70_..._20260814 -> 00:00Z of that day;
    continuous_<epoch> -> epoch. Undated tags (backfill, clean_ONLY_CROSSES_v33, '') -> None."""
    t = str(tag or "")
    m = _TAG_EPOCH.search(t)
    if m:
        return float(m.group(1))
    m = _TAG_DATE.search(t)
    if m:
        try:
            return _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=_dt.timezone.utc).timestamp()
        except ValueError:
            return None
    return None


def recalc_anchor(row: Optional[dict], published: Optional[dict] = None, history: Optional[List[dict]] = None) -> Dict[str, Any]:
    """When was the ACTIVE set last recalculated by a board on a fresh NPZ? Pure.

    row       = per_sym_active row {updated_at, campaign_ts, winning_tag, meta_json(dict|str), overrides_md5}
    published = data/published_selection.json entry {published_at, overrides_md5} (board publish = progress mtime ~ result_done_utc)
    history   = per_sym_history rows for the sym_side [{epoch, winning_tag}] (any order)
    Order of evidence (most precise first):
      1. dated board tag AND the latest published board was published at/before this apply -> published_at (board finish on a
         JIT NPZ, §91 — the NPZ is rebuilt ~15 min before the board loads it, so publish time bounds the NPZ age within hours);
      2. dated board tag -> the tag's date (00:00Z of the apply day: golive applies finished boards; conservative = older);
      3. undated tag (mandate strip / artefact strip / backfill rewrites of a board set) -> the latest DATED history row at or
         before this apply (the board the rewrite descends from);
      4. nothing dated -> earliest of campaign_ts / meta.updated_at / meta.promoted_at (provenance unknown: never newer than
         the moment the set was written).
    No row -> cat_side defaults (the daily template) -> fresh (age 0)."""
    if not row:
        return {"source": "cat_side_defaults", "recalc_epoch": time.time(), "apply_epoch": None}
    meta = row.get("meta_json") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    apply_ep = iso_epoch(row.get("campaign_ts")) or iso_epoch(row.get("updated_at"))
    tag = str(row.get("winning_tag") or "")
    te = tag_epoch(tag)
    if te is not None:
        pa = iso_epoch((published or {}).get("published_at"))
        if pa is not None and apply_ep is not None and pa <= apply_ep + 300:
            return {"source": "published_board", "recalc_epoch": pa, "apply_epoch": apply_ep, "tag": tag}
        return {"source": "board_tag_date", "recalc_epoch": min(te, apply_ep or te), "apply_epoch": apply_ep, "tag": tag}
    best = None
    for h in history or []:
        he = iso_epoch(h.get("epoch")) or iso_epoch(h.get("updated_at"))
        hte = tag_epoch(h.get("winning_tag"))
        if hte is None or he is None or (apply_ep is not None and he > apply_ep + 1):
            continue
        cand = min(hte, he)
        if best is None or cand > best:
            best = cand
    if best is not None:
        return {"source": "history_board_tag", "recalc_epoch": best, "apply_epoch": apply_ep, "tag": tag}
    cands = [x for x in (apply_ep, iso_epoch(row.get("updated_at")), iso_epoch(meta.get("updated_at")), iso_epoch(meta.get("promoted_at")), iso_epoch(meta.get("campaign_ts"))) if x]
    if not cands:
        return {"source": "unknown_no_timestamp", "recalc_epoch": 0.0, "apply_epoch": apply_ep, "tag": tag}
    return {"source": "earliest_write_stamp", "recalc_epoch": min(cands), "apply_epoch": apply_ep, "tag": tag}


def _overrides_md5(ov_json: Any) -> str:
    try:
        ov = json.loads(ov_json) if isinstance(ov_json, str) else (ov_json or {})
        return hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()
    except Exception:
        return ""


def _store_db(cfg: Any = None) -> Path:
    return Path(os.environ.get("PER_SYM_STORE_DB") or (base_path(cfg) / "data" / "hourly_reconfig" / "per_sym_store.db"))


def load_active(cfg: Any = None, ttl: float = 60.0) -> Dict[str, dict]:
    """{sym_side: row} from per_sym_active (light columns only). Cached ttl seconds. {} on any error (fail-open)."""
    key = f"active:{_store_db(cfg)}"
    c = _CACHE.get(key)
    if c and time.time() - c[0] < ttl:
        return c[1]
    out: Dict[str, dict] = {}
    try:
        con = sqlite3.connect(f"file:{_store_db(cfg)}?mode=ro", uri=True, timeout=5.0)
        for ss, ua, cts, tag, meta, ov in con.execute("SELECT sym_side, updated_at, campaign_ts, winning_tag, meta_json, overrides_json FROM per_sym_active"):
            out[str(ss).upper()] = {"updated_at": ua, "campaign_ts": cts, "winning_tag": tag, "meta_json": meta, "overrides_md5": _overrides_md5(ov)}
        con.close()
    except Exception:
        out = {}
    _CACHE[key] = (time.time(), out)
    return out


def load_history(ss: str, cfg: Any = None) -> List[dict]:
    try:
        con = sqlite3.connect(f"file:{_store_db(cfg)}?mode=ro", uri=True, timeout=5.0)
        rows = [{"epoch": e, "updated_at": u, "winning_tag": t} for e, u, t in con.execute("SELECT epoch, updated_at, winning_tag FROM per_sym_history WHERE sym_side=? ORDER BY id DESC LIMIT 200", (ss,))]
        con.close()
        return rows
    except Exception:
        return []


def load_published(cfg: Any = None, ttl: float = 600.0) -> Dict[str, dict]:
    """{sym_side: {published_at, overrides_md5}} from data/published_selection.json (chain step 5b copy from S1)."""
    p = base_path(cfg) / "data" / "published_selection.json"
    key = f"pub:{p}"
    c = _CACHE.get(key)
    try:
        mt = p.stat().st_mtime
    except Exception:
        return {}
    if c and c[2] == mt and time.time() - c[0] < ttl:
        return c[1]
    out: Dict[str, dict] = {}
    try:
        d = json.loads(p.read_text())
        for ss, e in (d.get("sym_sides") or {}).items():
            if isinstance(e, dict):
                out[str(ss).upper()] = {"published_at": e.get("published_at"), "overrides_md5": e.get("overrides_md5")}
    except Exception:
        out = {}
    _CACHE[key] = (time.time(), out, mt)
    return out


# ─────────────────────────────── closed trades ───────────────────────────────
def _closes_file(venue: str, cfg: Any = None) -> Path:
    return base_path(cfg) / "data" / "stale_set_halt" / f"closes_{venue}.jsonl"


def _hooks_marker(venue: str, cfg: Any = None) -> Path:
    return base_path(cfg) / "data" / "stale_set_halt" / f"hooks_started_{venue}.txt"


def hooks_started_epoch(venue: str, cfg: Any = None) -> Optional[float]:
    try:
        return iso_epoch(_hooks_marker(venue, cfg).read_text().strip())
    except Exception:
        return None


def mark_hooks_started(venue: str, cfg: Any = None) -> None:
    """Called once by a live process at import: from this moment the venue's full closes are recorded by the execute_now hook,
    so ledger rows are only used for the period before it (no double counting)."""
    try:
        m = _hooks_marker(venue, cfg)
        if not m.exists():
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_text(_iso(time.time()))
    except Exception:
        pass


def record_close(venue: str, account: str, symbol: str, position_side: str, gain_pct: float, reason: str = "", cfg: Any = None, ts: Optional[float] = None, fee_pct: Optional[float] = None) -> Optional[dict]:
    """execute_now full-close hook. Appends {ts, account, sym_side, gain_pct, net_pct, reason} (O_APPEND, multi-process safe),
    then re-evaluates the sym_side so a halt (and its S1 pin) fires right after the 2nd loss instead of at the next entry."""
    try:
        f = _closes_file(venue, cfg)
        f.parent.mkdir(parents=True, exist_ok=True)
        if hooks_started_epoch(venue, cfg) is None:
            try:
                _hooks_marker(venue, cfg).write_text(_iso(time.time() - 2.0))  # ledger rows of this very close (logged ms earlier) are excluded
            except Exception:
                pass
        fee =DEFAULT_FEE_PCT.get(venue, 0.0) if fee_pct is None else float(fee_pct)
        g = float(gain_pct or 0.0)
        rec = {"ts": _iso(ts or time.time()), "account": str(account or ""), "sym_side": sym_side(symbol, position_side), "gain_pct": round(g, 4), "net_pct": round(g - fee, 4), "reason": str(reason or "")[:120], "src": "hook"}
        with open(f, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        _CACHE.pop(f"hook:{venue}", None)
        if not settings(cfg)["enabled"]:
            return None
        return check(venue, symbol, position_side, cfg=cfg, trigger="close", now=ts)  # ts given only by backfills/tests: evaluate as of the close
    except Exception:
        return None


def _hook_closes(venue: str, cfg: Any = None) -> Dict[str, List[dict]]:
    f = _closes_file(venue, cfg)
    key = f"hook:{venue}"
    try:
        mt = f.stat().st_mtime
    except Exception:
        return {}
    c = _CACHE.get(key)
    if c and c[0] == mt:
        return c[1]
    out: Dict[str, List[dict]] = {}
    try:
        with open(f) as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                except Exception:
                    continue
                out.setdefault(str(e.get("sym_side", "")).upper(), []).append(e)
    except Exception:
        out = {}
    _CACHE[key] = (mt, out)
    return out


_G_IN_REASON = re.compile(r"(?:^|[_ \-(])g(-?\d+(?:\.\d+)?)%?")


def ledger_row_to_close(e: dict, venue: str) -> Optional[dict]:
    """per_trade_returns row -> close record, or None when it is not a full close. Pure.
    Full close = no partial marker (TIERED/PARTIAL/SCALE_OUT) and no bare REDUCE (REDUCE_TO_FLAT is a full close).
    Gain: stocks -> pnl_pct (entry/exit prices are real). Crypto -> the g<x>% the exit logic stamped in the reason (decision-time
    gain from the real entry price) when present, else pnl_pct (tracker gain; §98: the crypto ledger has entry_price 0.0 and is
    not Binance truth — flagged src=ledger_pnl so a reviewer sees which closes rest on it)."""
    try:
        r = str(e.get("reason") or "")
        ru = r.upper()
        if any(t in ru for t in PARTIAL_TOKENS):
            return None
        if "REDUCE" in ru and "REDUCE_TO_FLAT" not in ru:
            return None
        sym, side = str(e.get("symbol") or "").upper(), str(e.get("side") or "").upper()
        if not sym or side not in ("LONG", "SHORT"):
            return None
        fee = float(e.get("fees_pct") or 0.0)
        src = "ledger_pnl"
        g = float(e.get("pnl_pct") or 0.0)
        if venue == "ez":
            m = _G_IN_REASON.search(r)
            if m:
                g, src = float(m.group(1)), "ledger_reason_g"
            fee = max(fee, DEFAULT_FEE_PCT["ez"])
        return {"ts": e.get("ts"), "account": e.get("account"), "sym_side": f"{sym}_{side}", "gain_pct": round(g, 4), "net_pct": round(g - fee, 4), "reason": r[:120], "src": src}
    except Exception:
        return None


def _ledger_closes(venue: str, cfg: Any = None, days: int = 45) -> Dict[str, List[dict]]:
    key = f"ledger:{venue}:{days}"
    c = _CACHE.get(key)
    if c and time.time() - c[0] < 120:
        return c[1]
    out: Dict[str, List[dict]] = {}
    d = base_path(cfg) / "data" / "per_trade_returns"
    today = _dt.datetime.now(_dt.timezone.utc)
    for i in range(int(days) + 1):
        day = (today - _dt.timedelta(days=i)).strftime("%Y%m%d")
        for acct in VENUE_ACCOUNTS.get(venue, ()):
            p = d / f"{acct}_{day}.jsonl"
            if not p.exists():
                continue
            try:
                with open(p) as fh:
                    for ln in fh:
                        try:
                            e = json.loads(ln)
                        except Exception:
                            continue
                        e.setdefault("account", acct)
                        rec = ledger_row_to_close(e, venue)
                        if rec:
                            out.setdefault(rec["sym_side"], []).append(rec)
            except Exception:
                continue
    _CACHE[key] = (time.time(), out)
    return out


def closes_for(venue: str, ss: str, cfg: Any = None) -> List[dict]:
    """Hook-recorded full closes (from hooks_started) + ledger closes before that moment, oldest first."""
    hs = hooks_started_epoch(venue, cfg)
    hook = list(_hook_closes(venue, cfg).get(ss, []))
    led = [r for r in _ledger_closes(venue, cfg).get(ss, []) if hs is None or (iso_epoch(r.get("ts")) or 0) < hs]
    rows = [r for r in hook + led if iso_epoch(r.get("ts")) is not None]
    rows.sort(key=lambda r: iso_epoch(r.get("ts")))
    return rows


def trailing_losses(closes: Iterable[dict], since_epoch: Optional[float], n: int) -> Tuple[bool, List[dict]]:
    """Pure: the last n closes newer than since_epoch are ALL net losses? -> (hit, those closes)."""
    rows = sorted([c for c in closes if (iso_epoch(c.get("ts")) or 0) > (since_epoch or 0)], key=lambda c: iso_epoch(c.get("ts")))
    last = rows[-n:] if n > 0 else []
    hit = n > 0 and len(last) == n and all(float(c.get("net_pct", c.get("gain_pct", 0.0)) or 0.0) < 0 for c in last)
    return hit, last


# ─────────────────────────────── pure decision ───────────────────────────────
def evaluate(ss: str, row: Optional[dict], published: Optional[dict], history: Optional[List[dict]], closes: List[dict], halt: Optional[dict], now: float, days: float = 10.0, losses: int = 2) -> Dict[str, Any]:
    """Pure decision for one sym_side. Returns {action: NONE|HALT|KEEP|RELEASE, ...}.
    KEEP/RELEASE only when a halt exists. RELEASE requires: active set applied after halted_at, fingerprint changed since the
    halt, and the board behind it published after halted_at (published_at, or a dated board tag on a day after the halt day)."""
    anc = recalc_anchor(row, published, history)
    age_days = max(0.0, (now - float(anc.get("recalc_epoch") or 0.0)) / 86400.0)
    since = anc.get("apply_epoch")
    if halt:
        h_at = iso_epoch(halt.get("halted_at")) or 0.0
        if halt.get("reset_at"):
            since = max(since or 0.0, iso_epoch(halt.get("reset_at")) or 0.0)
        apply_ep = anc.get("apply_epoch") or 0.0
        fp_now = (row or {}).get("overrides_md5", "")
        fp_changed = bool(row) and fp_now != halt.get("fingerprint", "")
        pub_after = False
        if anc.get("source") == "published_board":
            pub_after = float(anc["recalc_epoch"]) > h_at
        elif anc.get("source") == "board_tag_date":
            pub_after = float(anc["recalc_epoch"]) >= h_at - (h_at % 86400) + 86400  # tag day strictly after the halt day
        if row and apply_ep > h_at and fp_changed and pub_after:
            return {"action": "RELEASE", "sym_side": ss, "age_days": round(age_days, 2), "anchor": anc, "why": f"new board ({anc.get('source')} {_iso(anc.get('recalc_epoch'))}) applied {_iso(apply_ep)} after halt {halt.get('halted_at')}"}
        return {"action": "KEEP", "sym_side": ss, "age_days": round(age_days, 2), "anchor": anc, "why": halt.get("reason", "STALE_SET_HALT")}
    hit, last = trailing_losses(closes, since, losses)
    if age_days > days and hit:
        why = f"STALE_SET_HALT vector_age={age_days:.1f}d>{days:g}d ({anc.get('source')} {_iso(anc.get('recalc_epoch'))}) AND last {losses} closes net " + ",".join(f"{float(c.get('net_pct', 0)):+.2f}%" for c in last)
        return {"action": "HALT", "sym_side": ss, "age_days": round(age_days, 2), "anchor": anc, "losers": last, "why": why}
    return {"action": "NONE", "sym_side": ss, "age_days": round(age_days, 2), "anchor": anc, "trailing_loss_hit": hit, "n_closes_since_apply": len([c for c in closes if (iso_epoch(c.get("ts")) or 0) > (since or 0)])}


# ─────────────────────────────── persistence ───────────────────────────────
def _halts_file(venue: str, cfg: Any = None) -> Path:
    return base_path(cfg) / "data" / "stale_set_halt" / f"halts_{venue}.json"


def load_halts(venue: str, cfg: Any = None) -> Dict[str, dict]:
    try:
        return json.loads(_halts_file(venue, cfg).read_text()).get("halts", {}) or {}
    except Exception:
        return {}


def _save_halts(venue: str, halts: Dict[str, dict], cfg: Any = None) -> None:
    p = _halts_file(venue, cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"halts": halts, "_meta": {"saved_at": _iso(time.time()), "rule": "BACKTEST_BIBLE §100 STALE-SET 2-LOSS HALT"}}, indent=1, default=str))
    os.replace(tmp, p)


def _alert(venue: str, rec: dict, cfg: Any = None) -> None:
    try:
        p = base_path(cfg) / "data" / "alerts" / f"stale_set_halt_{venue}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def _log(logger: Any, level: str, msg: str) -> None:
    try:
        if logger is not None:
            getattr(logger, level)(msg)
    except Exception:
        pass


def check(venue: str, symbol: str, position_side: str, cfg: Any = None, logger: Any = None, trigger: str = "entry", now: Optional[float] = None, pin: bool = True) -> Optional[dict]:
    """Evaluate + persist one sym_side. Returns the active halt record (dict) or None. Fail-open."""
    st = settings(cfg)
    if not st["enabled"]:
        return None
    ss = sym_side(symbol, position_side)
    now = now or time.time()
    try:
        with _LOCK:
            halts = load_halts(venue, cfg)
            halt = halts.get(ss)
            row = load_active(cfg).get(ss)
            if not halt:
                anc = recalc_anchor(row, load_published(cfg).get(ss), None if (row and tag_epoch(row.get("winning_tag")) is not None) else load_history(ss, cfg))
                if (now - float(anc.get("recalc_epoch") or 0.0)) / 86400.0 <= st["days"]:
                    return None  # fresh vector: the loss count is irrelevant (cheap exit, no ledger IO)
            hist = load_history(ss, cfg) if (row and tag_epoch(row.get("winning_tag")) is None) else None
            dec = evaluate(ss, row, load_published(cfg).get(ss), hist, closes_for(venue, ss, cfg), halt, now, st["days"], st["losses"])
            if dec["action"] == "HALT":
                rec = {"sym_side": ss, "venue": venue, "halted_at": _iso(now), "reason": dec["why"], "age_days": dec["age_days"], "anchor": dec["anchor"], "losers": dec.get("losers"), "fingerprint": (row or {}).get("overrides_md5", ""), "trigger": trigger, "pin": {"state": "PENDING"}}
                halts[ss] = rec
                _save_halts(venue, halts, cfg)
                _alert(venue, {"event": "HALT", **rec}, cfg)
                _log(logger, "critical", f"🛑 [STALE_SET_HALT] {venue}:{ss} HALTED — {dec['why']} — opens/augments/reentries refused until a new board (latest NPZ) is published+applied; closes unaffected")
                if pin:
                    pin_async(venue, ss, "add", cfg, logger)
                return rec
            if dec["action"] == "RELEASE":
                rel = dict(halt)
                halts.pop(ss, None)
                _save_halts(venue, halts, cfg)
                _alert(venue, {"event": "RELEASE", "sym_side": ss, "venue": venue, "released_at": _iso(now), "why": dec["why"], "halt": rel}, cfg)
                _log(logger, "critical", f"✅ [STALE_SET_HALT_RELEASE] {venue}:{ss} — {dec['why']} — loss window restarts at the new apply")
                if pin and (rel.get("pin") or {}).get("state") in ("PINNED", "PENDING"):
                    pin_async(venue, ss, "remove", cfg, logger)
                return None
            return halt if dec["action"] == "KEEP" else None
    except Exception as e:
        _log(logger, "warning", f"[STALE_SET_HALT] check error {venue}:{ss} (fail-open): {e}")
        return None


def entry_gate(venue: str, symbol: str, position_side: str, action: Any, cfg: Any = None, logger: Any = None, is_entry: Optional[bool] = None) -> Tuple[bool, str]:
    """execute_now hook: (blocked, why). Only position-increasing actions can be blocked (is_entry overrides the action-token
    test when the caller already classified the order, e.g. tradier's _is_exit_or_reduce/is_reduce). Fail-open."""
    try:
        _entry = is_entry_action(action) if is_entry is None else bool(is_entry)
        if not symbol or not _entry:
            return False, ""
        h = check(venue, symbol, position_side, cfg=cfg, logger=logger, trigger="entry")
        if h:
            return True, str(h.get("reason", "STALE_SET_HALT"))[:200]
    except Exception:
        return False, ""
    return False, ""


# ─────────────────────────────── S1 scheduler pin ───────────────────────────────
PIN_REMOTE = "binance-sandbox/data/v15_sched_pinned_sides.json"
_PIN_SCRIPT = r'''
import json, os, sys, hashlib, time
p = os.path.expanduser(sys.argv[1]); ss = sys.argv[2]; op = sys.argv[3]
d = json.load(open(p)) if os.path.exists(p) else {"sides": [], "hosts": []}
sides = [s for s in (d.get("sides") or []) if s != ss]
if op == "add":
    sides.insert(0, ss)
d["sides"] = sides
d.setdefault("hosts", [])
log = d.setdefault("_stale_set_halt", {})
log[ss] = {"op": op, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
tmp = p + ".tmp_ssh%d" % os.getpid()
open(tmp, "w").write(json.dumps(d, indent=1))
json.load(open(tmp))
os.replace(tmp, p)
print(json.dumps({"ok": True, "op": op, "ss": ss, "top": sides[:1], "n": len(sides), "md5": hashlib.md5(open(p, "rb").read()).hexdigest()}))
'''


def _on_s1() -> bool:
    try:
        return socket.gethostname() in ("niels",) and Path(os.path.expanduser("~/" + PIN_REMOTE)).exists()
    except Exception:
        return False


def pin_now(ss: str, op: str = "add", host: Optional[str] = None, timeout: float = 30.0) -> Dict[str, Any]:
    """Insert ss at sides[0] (op=add) or remove it (op=remove) in S1's operator queue. JSON-validated + md5 returned."""
    try:
        if _on_s1():
            cmd = ["python3", "-c", _PIN_SCRIPT, "~/" + PIN_REMOTE, ss, op]
        else:
            h = host or os.environ.get("STALE_SET_HALT_PIN_HOST", "s1-int")
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", h, "python3", "-c", _shq(_PIN_SCRIPT), "~/" + PIN_REMOTE, ss, op]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip().splitlines()
        res = json.loads(out[-1]) if out else {"ok": False}
        if r.returncode != 0:
            res = {"ok": False, "err": (r.stderr or "")[-300:]}
        return res
    except Exception as e:
        return {"ok": False, "err": str(e)[:300]}


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _set_pin_state(venue: str, ss: str, state: Dict[str, Any], cfg: Any = None) -> None:
    with _LOCK:
        halts = load_halts(venue, cfg)
        if ss in halts:
            halts[ss]["pin"] = state
            _save_halts(venue, halts, cfg)


def pin_async(venue: str, ss: str, op: str, cfg: Any = None, logger: Any = None) -> None:
    """Never blocks the trading loop: runs the ssh edit in a daemon thread and records the result in the halt record."""
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("STALE_SET_HALT_NO_PIN"):
        return

    def _run():
        res = pin_now(ss, op)
        st = {"state": ("PINNED" if op == "add" else "UNPINNED") if res.get("ok") else "PENDING", "op": op, "at": _iso(time.time()), "result": res}
        if op == "add":
            _set_pin_state(venue, ss, st, cfg)
        _alert(venue, {"event": f"PIN_{op.upper()}", "sym_side": ss, **st}, cfg)
        _log(logger, "critical" if res.get("ok") else "error", f"[STALE_SET_HALT_PIN] {op} {ss} on S1 operator queue -> {res}")

    threading.Thread(target=_run, name=f"stale_pin_{ss}", daemon=True).start()


def status_all(venue: str, cfg: Any = None, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Read-only dry-run: evaluate every sym_side that has a set or closes, without writing anything."""
    now = now or time.time()
    st = settings(cfg)
    act = load_active(cfg)
    pub = load_published(cfg)
    halts = load_halts(venue, cfg)
    hook = _hook_closes(venue, cfg)
    led = _ledger_closes(venue, cfg)
    sides = set(hook) | set(led) | set(halts)
    out = []
    for ss in sorted(sides):
        row = act.get(ss)
        hist = load_history(ss, cfg) if (row and tag_epoch(row.get("winning_tag")) is None) else None
        out.append(evaluate(ss, row, pub.get(ss), hist, closes_for(venue, ss, cfg), halts.get(ss), now, st["days"], st["losses"]))
    return out
