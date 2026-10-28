#!/usr/bin/env python3
"""s1_bar_guarantee — per-bar completeness ledger, escalating self-repair and EXACTNESS AUDIT of the server live chain.

USER 2026-10-28 (verbatim): "make sure s1 never misses a beat in the sense that is produces or receives a 15m kline for every
tradeable symbol, calculates its nps and state for each side and executes it immediately" and "make ABSOLUTELY 100% certain
they trade EXACTLY what the rolling forward per_sym vector settings dictate within seconds of 15m bar completion and kline
arrival". Runs on S1 (--venue crypto) and S5 (--venue stocks, RTH bars only).

Chain watched per closed 15m bar B (close C = B + 900), for every side of the trader's universe plus every held side:
  BAR     data/closed_15m_receipts.jsonl first receipt of (symbol, B)          source mac | fallback (row _producer)
  NPZ     backtest_v8/indicators/.tail/<SYM>.jsonl tip >= B (or zip tip)        tools/npz_tail_service.py
  STATE   data/forward_state/<SYM_SIDE>.json npz_tail >= B and ok               tools/fwdstate_npz_watch.py
          + settings_sha256 == sha256(per_sym_active.full_config_json) now, engine_ast_sha256 == this host's engine
  EXEC    ~/binance/data/vector_direct/consumed_<venue>.json: every active account keyed on / holding the side was planned
          against that state (tools/vector_direct_trader.py)
Repairs, escalating by deadline after C:
  +35 s  bar missing          -> fetch exactly those symbols (Binance closed kline / Tradier 15min timesales), land them
                                 through closed_bar_feed.receive_closed_15m (same path as the Mac lane)
  +45 s  bar not in the NPZ   -> append exactly those symbols (subprocess, npz_tail_service functions)
  +90 s  no ok state          -> data/forward_state_priority/<SYM_SIDE> (fwdstate_npz_watch recomputes at class -1)
  ok state not consumed 10 s  -> data/vector_direct/PLAN_NOW_<venue> (trader wakes and plans every active account)
AUDIT at finalize, per account x side: what the state dictated (board open/flat vs broker qty at plan time, AUGMENT/REDUCE
acts at B) vs what the trader ledger shows SENT/filled -> match | match_late | noop | missed | extra | wrong_direction |
wrong_size | blocked:<why> | not_in_books | stale_state | settings_mismatch.
Output: ~/binance/data/bar_guarantee/<venue>_<day>.jsonl (side rows, audit rows, bar summary), status_<venue>.json,
ALERTS.jsonl (+ the trader's data/vector_direct/ALERTS.jsonl). Test: data/bar_guarantee/SIMULATE_MISSING_<venue>.json
{"symbols": [...], "until_epoch": t} ignores those receipts so the +35 s repair fetches and lands the real bar. Never orders.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
BAR_S = 900
RECV_DEADLINE_S = 35.0
APPEND_DEADLINE_S = 45.0
STATE_DEADLINE_S = 90.0
EXEC_AFTER_STATE_S = 10.0
LATE_S = float(os.environ.get("BAR_GUARANTEE_LATE_S", "90"))
FINAL_S = 600.0
FALLBACK_PRODUCERS = ("s1_closed_bar_fallback", "bar_guarantee_fallback")


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def atomic_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    os.replace(tmp, path)


def pct(values, q):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))], 2)


def lat_stats(values):
    v = [x for x in values if x is not None]
    return {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": round(max(v), 2) if v else None}


def engine_ast_sha(path: Path):
    import ast
    return hashlib.sha256(ast.dump(ast.parse(path.read_text()), include_attributes=False).encode()).hexdigest()


def repair_append(base: Path, symbols, bar, mode):
    """Subprocess body: append the closed bar `bar` for exactly these symbols (npz_tail_service functions)."""
    import npz_tail_service as T
    out = {}
    for symbol in symbols:
        try:
            npz = T.npz_for(base, symbol)
            if npz is None:
                out[symbol] = "no npz"
                continue
            lc = T.kline_last_close(base, symbol, mode)
            if lc is None or int(lc) < bar:
                out[symbol] = f"source has no bar {bar} (last {lc})"
                continue
            revision = T.source_revision(base, symbol, mode, lc)
            row = T._compute_row_process((symbol, int(lc), str(base), mode))
            if not row:
                out[symbol] = "compute returned nothing"
                continue
            out[symbol] = T.sweep_symbol(npz, symbol, lc, lambda _s, _c, r=row: r, revision=revision)
        except Exception as exc:
            out[symbol] = f"{type(exc).__name__}: {str(exc)[:120]}"
    print(json.dumps(out), flush=True)
    return 0


def expected_size_ok(act, row, cons, st, start_size):
    """(ok, detail) — size of a SENT order vs what the state dictates at START_POSITION_SIZE scale (exchange rounding)."""
    qty, price, held = float(row.get("qty") or 0), float(row.get("price") or 0), float(cons.get("held") or 0)
    if act == "CLOSE":
        return abs(qty - held) <= max(1e-9, 0.02 * held), f"qty {qty} vs held {held}"
    if act == "OPEN":
        eq, oq = float(st.get("engine_qty") or 0), float(st.get("engine_open_qty") or 0)
        ratio = min(4.0, max(0.25, eq / oq)) if eq > 0 and oq > 0 else 1.0
        want = start_size * ratio
        got = qty * price
        return (got > 0 and 0.5 * want <= got <= 2.0 * want) or (got > 0 and got <= 2.0 * want and want < 6.0), f"notional {got:.2f} vs {want:.2f}"
    acts = [a for a in st.get("acts_last_bar") or [] if str(a.get("type") or "").upper() == act]
    if not acts or held <= 0:
        return True, "no engine act to size against"
    a = acts[0]
    if act == "AUGMENT":
        q, before = float(a.get("qty") or 0), float(a.get("qty_before") or 0)
        want = held * q / before if before > 0 else 0
    else:
        want = min(held, held * float(a.get("qty_frac") or 0))
    if want <= 0:
        return True, "engine act without size"
    return 0.5 * want <= qty <= 1.6 * want or abs(qty - want) * price < 6.0, f"qty {qty} vs {want:.6g}"


class Guarantee:
    def __init__(self, a):
        self.a = a
        self.venue = a.venue
        self.mode = "crypto" if self.venue == "crypto" else "tradier"
        self.base = Path(a.base).expanduser()
        self.live = Path(a.live_root).expanduser()
        self.state_dir = Path(a.state_dir).expanduser() if a.state_dir else self.base / "data" / "forward_state"
        self.vd = self.live / "data" / "vector_direct"
        self.out = self.live / "data" / "bar_guarantee"
        self.out.mkdir(parents=True, exist_ok=True)
        self.prio = self.base / "data" / "forward_state_priority"
        self.receipts = self.base / "data" / "closed_15m_receipts.jsonl"
        self.ind = self.base / "backtest_v8" / "indicators"
        self.store = Path(a.store_db).expanduser() if a.store_db else None
        sys.path.insert(0, str(self.live / "tools"))
        import vector_direct_trader as VDT
        self.VDT = VDT
        self.accounts = list(VDT.CRYPTO_ACCOUNTS if self.venue == "crypto" else VDT.STOCK_ACCOUNTS)
        env = VDT.load_env_files([self.live / ".env", Path.home() / ".env", Path.home() / "binance-sandbox" / ".env", self.vd / f"{self.venue}.env"])
        self.start_size = float((getattr(VDT, "MANDATED_START", {}) or {}).get(self.venue) or VDT.START_POSITION_SIZE or env.get("VD_START_POSITION_SIZE") or 0)
        self.env = env
        self.rc_pos = None
        self.recv = {}
        self.bar = None
        self.history = []
        self.state_cache = {}
        self.consumed = ({}, None)
        self.zip_cache = {}
        self.engine = (None, None)
        self.session = None
        self.procs = []
        self.last_poke = 0.0
        self.last_status = 0.0

    # ── inputs ──
    def host_engine(self):
        p = self.base / "v12_quick_engine.py"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return None
        if self.engine[0] != mt:
            self.engine = (mt, engine_ast_sha(p))
        return self.engine[1]

    def store_sha(self, ss):
        if not self.store or not self.store.is_file():
            return None, None
        con = sqlite3.connect(f"file:{self.store}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT updated_at,full_config_json FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
        finally:
            con.close()
        if not row or not row[1]:
            return None, None
        full = json.loads(row[1])
        return row[0], hashlib.sha256(json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def universe(self):
        keys = {a: set(v) for a, v in self.VDT.universe(self.live, self.venue, self.accounts).items()}
        held = read_json(self.vd / f"held_{self.venue}.json", {}) or {}
        held_by = {a: {str(s).upper() for s in v} for a, v in (held.get("by_account") or {}).items()}
        sides = set().union(*keys.values(), *held_by.values()) if (keys or held_by) else set()
        return keys, held_by, sides

    def follow_receipts(self):
        try:
            size = self.receipts.stat().st_size
        except OSError:
            return
        if self.rc_pos is None or size < self.rc_pos:
            self.rc_pos = max(0, size - 4_000_000)
        if size == self.rc_pos:
            return
        with open(self.receipts, "rb") as fh:
            fh.seek(self.rc_pos)
            chunk = fh.read(size - self.rc_pos)
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.rc_pos += end + 1
        for line in chunk[: end + 1].splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("mode") != self.mode:
                continue
            k = (r.get("symbol"), int(r.get("bar_start_ts") or 0))
            if k not in self.recv:
                self.recv[k] = float(r.get("received_at") or 0)
        if len(self.recv) > 40000:
            cutoff = time.time() - 3 * 3600
            self.recv = {k: v for k, v in self.recv.items() if k[1] > cutoff}

    def row_source(self, symbol, bar):
        from closed_bar_feed import timestamp_epoch
        for r in reversed(read_json(self.base / "data" / "closed_15m_bars" / self.mode / f"{symbol}.json", []) or []):
            try:
                if timestamp_epoch(r.get("timestamp")) == bar:
                    return ("fallback" if r.get("_producer") in FALLBACK_PRODUCERS else "mac"), r.get("_source")
            except Exception:
                continue
        return None, None

    def appended_tip(self, symbol):
        from npz_tail_store import _tail_tip_ts, _base_tip_ts
        sidecar = self.ind / ".tail" / f"{symbol}.jsonl"
        tip = _tail_tip_ts(sidecar)
        zp = self.ind / f"{symbol}.npz"
        try:
            mt = zp.stat().st_mtime_ns
        except OSError:
            mt = None
        hit = self.zip_cache.get(symbol)
        if mt and (not hit or hit[0] != mt):
            self.zip_cache[symbol] = hit = (mt, _base_tip_ts(zp))
        ztip = hit[1] if hit else None
        tips = [t for t in (tip, ztip) if t]
        try:
            smt = sidecar.stat().st_mtime
        except OSError:
            smt = None
        return (max(tips) if tips else None), (tip or 0) >= (ztip or 0), smt

    def read_state(self, ss):
        p = self.state_dir / f"{ss}.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return None, None
        hit = self.state_cache.get(ss)
        if hit and hit[0] == mt:
            return hit[1], mt
        d = read_json(p)
        self.state_cache[ss] = (mt, d)
        return d, mt

    def read_consumed(self):
        p = self.vd / f"consumed_{self.venue}.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return {}
        if self.consumed[1] != mt:
            self.consumed = ((read_json(p, {}) or {}).get("sides") or {}, mt)
        return self.consumed[0]

    def simulate(self, now):
        d = read_json(self.out / f"SIMULATE_MISSING_{self.venue}.json", {}) or {}
        if float(d.get("until_epoch") or 0) < now:
            return set()
        return {str(s).upper() for s in d.get("symbols") or []}

    def tracked_bar(self, bar):
        if self.venue == "crypto":
            return True
        from tools.npz_cutoffs import is_rth_bar_start
        return is_rth_bar_start(bar)

    # ── bar lifecycle ──
    def new_bar(self, bar, now):
        keys, held_by, sides = self.universe()
        status = read_json(self.vd / f"status_{self.venue}.json", {}) or {}
        active = list(status.get("active_accounts") or [])
        syms = sorted({ss.rsplit("_", 1)[0] for ss in sides})
        sim = self.simulate(now)
        self.bar = {"bar": bar, "close": bar + BAR_S, "started": now, "active": active, "sim": sorted(sim & set(syms)), "engine": self.host_engine(),
                    "syms": {s: {"recv_at": None, "src": None, "src_detail": None, "append_at": None, "repairs": []} for s in syms},
                    "sides": {ss: {"accts": sorted(a for a in active if ss in keys.get(a, ()) or ss in held_by.get(a, ())),
                                   "keyed": sorted(a for a in active if ss in keys.get(a, ())), "held": sorted(a for a, v in held_by.items() if ss in v),
                                   "state_at": None, "state_ok_at": None, "reason": None, "exec_at": None, "orders": [], "repairs": [],
                                   "state": None, "consumed": {}, "verify": None} for ss in sorted(sides)},
                    "done_stage": set(), "alerts": [], "repairs_n": 0}
        log(f"{self.venue} bar {self.iso(bar)} tracking {len(syms)} symbols / {len(sides)} sides, active={active} sim={self.bar['sim']}")

    @staticmethod
    def iso(ts):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    def alert(self, stage, items, repair):
        b = self.bar
        row = {"iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "epoch": time.time(), "host": socket.gethostname(),
               "venue": self.venue, "kind": f"BAR_GUARANTEE_{stage}", "bar": b["bar"], "bar_iso": self.iso(b["bar"]),
               "after_close_s": round(time.time() - b["close"], 1), "n": len(items), "items": sorted(items)[:200], "repair": repair}
        for path in (self.out / "ALERTS.jsonl", self.vd / "ALERTS.jsonl"):
            try:
                with open(path, "a") as fh:
                    fh.write(json.dumps(row) + "\n")
            except OSError:
                pass
        b["alerts"].append({"stage": stage, "n": len(items), "after_close_s": row["after_close_s"]})
        log(f"ALERT {stage} {len(items)}: {sorted(items)[:12]} repair={repair}")

    def verify_state(self, ss, st):
        out = {"src": st.get("src"), "settings_sha256": st.get("settings_sha256"), "engine_ok": st.get("engine_ast_sha256") == self.bar["engine"]}
        try:
            upd, sha = self.store_sha(ss)
        except Exception as exc:
            upd, sha = None, f"error {exc}"
        out["store_updated_at"], out["store_sha256"] = upd, sha
        if st.get("src") == "per_sym_store.active":
            out["settings_ok"] = sha == st.get("settings_sha256") or (upd is not None and str(upd) > str(st.get("ts")))
        else:
            out["settings_ok"] = sha is None  # fallback settings are legitimate only when the store has no active row
        return out

    def observe(self, now):
        b = self.bar
        self.follow_receipts()
        sim = set(b["sim"])
        for s, r in b["syms"].items():
            if r["recv_at"] is None and s not in sim:
                t = self.recv.get((s, b["bar"]))
                if t:
                    r["recv_at"] = t
                    r["src"], r["src_detail"] = self.row_source(s, b["bar"])
            if r["append_at"] is None:
                tip, from_sidecar, smt = self.appended_tip(s)
                if tip is not None and tip >= b["bar"]:
                    r["append_at"] = smt if (from_sidecar and smt and b["close"] <= smt <= now) else now
        consumed = self.read_consumed()
        for ss, r in b["sides"].items():
            if r["state_ok_at"] is None:
                st, mt = self.read_state(ss)
                if isinstance(st, dict) and int(st.get("npz_tail") or 0) >= b["bar"]:
                    r["state_at"] = r["state_at"] or mt
                    r["reason"] = st.get("reason")
                    if st.get("ok"):
                        r["state_ok_at"] = mt
                        r["state"] = {k: st.get(k) for k in ("ts", "npz_tail", "open", "acts_bar_ts", "acts_last_bar", "engine_qty", "engine_open_qty",
                                                             "engine_open_bar_ts", "settings_sha256", "settings_updated_at", "src", "engine_ast_sha256")}
                        r["verify"] = self.verify_state(ss, st)
            if r["state_ok_at"] is not None and r["exec_at"] is None:
                if not r["accts"]:
                    r["exec_at"] = "n/a"
                    continue
                rows = consumed.get(ss) or {}
                ats = []
                for acct in r["accts"]:
                    c = rows.get(acct) or {}
                    if int(c.get("npz_tail") or 0) >= b["bar"] and c.get("ok"):
                        ats.append(float(c.get("at") or 0))
                        r["consumed"][acct] = dict(c)
                        r["orders"] = sorted(set(r["orders"]) | {f"{acct}:{x}" for x in c.get("orders") or []})
                if len(ats) == len(r["accts"]):
                    r["exec_at"] = max(ats)

    def repair(self, now):
        b = self.bar
        age = now - b["close"]
        for at in (RECV_DEADLINE_S, 60.0, 120.0, 240.0):
            tag = f"recv@{at:.0f}"
            if age >= at and tag not in b["done_stage"]:
                b["done_stage"].add(tag)
                missing = [s for s, r in b["syms"].items() if r["recv_at"] is None]
                if missing:
                    res = self.fetch_bars(missing, b["bar"])
                    for s in missing:
                        b["syms"][s]["repairs"].append({"stage": "recv", "at": round(age, 1), "result": res.get(s)})
                    b["repairs_n"] += len(missing)
                    self.alert("BAR_MISSING", missing, f"fallback fetch at +{age:.0f}s: " + json.dumps(res)[:400])
        for at in (APPEND_DEADLINE_S, 75.0, 120.0, 240.0):
            tag = f"append@{at:.0f}"
            if age >= at and tag not in b["done_stage"]:
                b["done_stage"].add(tag)
                missing = [s for s, r in b["syms"].items() if r["append_at"] is None]
                if missing:
                    have_bar = [s for s in missing if b["syms"][s]["recv_at"] is not None]
                    if have_bar:
                        self.spawn_append(have_bar, b["bar"])
                    for s in missing:
                        b["syms"][s]["repairs"].append({"stage": "append", "at": round(age, 1), "spawned": s in have_bar})
                    b["repairs_n"] += len(have_bar)
                    self.alert("NPZ_NOT_APPENDED", missing, f"append subprocess for {len(have_bar)} (others still lack the bar)")
        for at in (STATE_DEADLINE_S, 150.0, 300.0):
            tag = f"state@{at:.0f}"
            if age >= at and tag not in b["done_stage"]:
                b["done_stage"].add(tag)
                missing = [ss for ss, r in b["sides"].items() if r["state_ok_at"] is None]
                if missing:
                    self.prio.mkdir(parents=True, exist_ok=True)
                    for ss in missing:
                        try:
                            (self.prio / ss).write_text(f"{now} bar {b['bar']}")
                        except OSError:
                            pass
                        b["sides"][ss]["repairs"].append({"stage": "state", "at": round(age, 1), "reason": b["sides"][ss]["reason"]})
                    b["repairs_n"] += len(missing)
                    self.alert("STATE_NOT_OK", [f"{ss}({b['sides'][ss]['reason']})" for ss in missing], "priority recompute (forward_state_priority)")
        late = [ss for ss, r in b["sides"].items() if r["state_ok_at"] and r["exec_at"] is None and now - r["state_ok_at"] >= EXEC_AFTER_STATE_S]
        if late and now - self.last_poke >= 5.0:
            self.last_poke = now
            try:
                (self.vd / f"PLAN_NOW_{self.venue}").write_text(f"s1_bar_guarantee bar {b['bar']} {len(late)} sides e.g. {late[:3]}")
            except OSError:
                pass
            for ss in late:
                b["sides"][ss]["repairs"].append({"stage": "exec", "at": round(age, 1)})
            b["repairs_n"] += 1
            log(f"POKE trader: {len(late)} ok states not consumed >= {EXEC_AFTER_STATE_S:.0f}s: {late[:6]}")
        overdue = [ss for ss, r in b["sides"].items() if r["state_ok_at"] and r["exec_at"] is None and now - r["state_ok_at"] >= 30.0
                   and f"exec_alert:{ss}" not in b["done_stage"]]
        if overdue:
            for ss in overdue:
                b["done_stage"].add(f"exec_alert:{ss}")
            self.alert("NOT_EXECUTED", overdue, "PLAN_NOW poke sent; trader has not planned these 30 s after the ok state")
        bad = [ss for ss, r in b["sides"].items() if r["verify"] and not (r["verify"]["settings_ok"] and r["verify"]["engine_ok"]) and f"verify:{ss}" not in b["done_stage"]]
        if bad:
            for ss in bad:
                b["done_stage"].add(f"verify:{ss}")
                self.prio.mkdir(parents=True, exist_ok=True)
                try:
                    (self.prio / ss).write_text(f"{now} settings/engine mismatch")
                except OSError:
                    pass
            self.alert("STATE_SETTINGS_OR_ENGINE_MISMATCH", [f"{ss}(settings_ok={b['sides'][ss]['verify']['settings_ok']},engine_ok={b['sides'][ss]['verify']['engine_ok']})" for ss in bad], "priority recompute")

    def fetch_bars(self, symbols, bar):
        import concurrent.futures as cf
        from closed_bar_feed import receive_closed_15m
        sim = set(self.bar["sim"])
        fetch = self.fetch_binance if self.venue == "crypto" else self.fetch_tradier

        def one(symbol):
            try:
                if symbol not in sim and self.recv.get((symbol, bar)):
                    return symbol, "arrived_meanwhile"
                row, why = fetch(symbol, bar)
                if row is None:
                    return symbol, why
                row["_producer"] = "bar_guarantee_fallback"
                row["_repair"] = "s1_bar_guarantee" + ("_simulated_missing" if symbol in sim else "")
                ev = {"symbol": symbol, "mode": self.mode, "bar_start_ts": bar, "bar_close_ts": bar + BAR_S, "published_at": time.time(), "row": row}
                rc = receive_closed_15m(str(self.base), ev)
                r = self.bar["syms"][symbol]
                if r["recv_at"] is None:
                    r["recv_at"], r["src"], r["src_detail"] = rc["received_at"], "fallback", row.get("_source")
                return symbol, "landed"
            except Exception as exc:
                return symbol, f"{type(exc).__name__}: {str(exc)[:100]}"
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            return dict(ex.map(one, symbols))

    def fetch_binance(self, symbol, bar):
        import s1_closed_bar_fallback as F
        if self.session is None:
            self.session = F.session(self.a.local_ip)
        return F.fetch_bar(self.session, symbol, bar)

    def fetch_tradier(self, symbol, bar):
        """Tradier 15min timesales bar starting at `bar` (exchange session data), only once the bar has closed."""
        import requests
        from zoneinfo import ZoneInfo
        key = self.env.get("TRADIER_API_KEY_TRA") or self.env.get("TRADIER_API_KEY")
        if not key:
            return None, "no Tradier key"
        et = dt.datetime.fromtimestamp(bar, dt.timezone.utc).astimezone(ZoneInfo("America/New_York"))
        r = requests.get("https://api.tradier.com/v1/markets/timesales", timeout=10,
                         params={"symbol": symbol, "interval": "15min", "start": et.strftime("%Y-%m-%d %H:%M"),
                                 "end": (et + dt.timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M"), "session_filter": "open"},
                         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        if r.status_code != 200:
            return None, f"HTTP {r.status_code} {r.text[:80]}"
        data = ((r.json() or {}).get("series") or {}).get("data") or []
        data = data if isinstance(data, list) else [data]
        for d in data:
            if int(d.get("timestamp") or 0) == bar:
                now = time.time()
                if now < bar + BAR_S:
                    return None, "bar not closed"
                return {"timestamp": dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                        "open": float(d["open"]), "high": float(d["high"]), "low": float(d["low"]), "close": float(d["close"]),
                        "volume": float(d.get("volume") or 0), "_closed": True, "_provisional": False, "_closed_verified_at": now,
                        "_source": "tradier_timesales_15min_s5_fallback"}, ""
        return None, "bar not returned"

    def spawn_append(self, symbols, bar):
        logf = open(self.out / f"repair_append_{self.venue}.log", "a")
        p = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--venue", self.venue, "--repair-append", ",".join(symbols),
                              "--bar", str(bar), "--base", str(self.base)], cwd=str(self.base), stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append((p, logf))
        log(f"REPAIR append pid {p.pid} for {symbols[:10]}")

    def complete(self):
        b = self.bar
        return all(r["append_at"] for r in b["syms"].values()) and all(r["state_ok_at"] and r["exec_at"] for r in b["sides"].values())

    # ── exactness audit ──
    def ledger_rows(self, b):
        rows = []
        for day in sorted({dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d") for t in (b["close"], b["close"] + FINAL_S)}):
            p = self.vd / f"ledger_{self.venue}_{day}.jsonl"
            try:
                with open(p, "rb") as fh:
                    fh.seek(max(0, p.stat().st_size - 3_000_000))
                    for line in fh.read().splitlines():
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get("event") == "ORDER" and b["close"] <= float(r.get("ts") or 0) < b["close"] + BAR_S and not r.get("dry_run"):
                            rows.append(r)
            except OSError:
                continue
        return rows

    def audit(self):
        b = self.bar
        orders = {}
        for r in self.ledger_rows(b):
            orders.setdefault((r.get("acct"), r.get("ss")), []).append(r)
        out = []
        for ss, side in b["sides"].items():
            st = side["state"] or {}
            at_bar = int(st.get("acts_bar_ts") or -1) == b["bar"] == int(st.get("npz_tail") or -2)
            board_acts = [str(a.get("type") or "").upper() for a in (st.get("acts_last_bar") or [])] if at_bar else []
            for acct in sorted(set(side["accts"]) | {a for (a, s) in orders if s == ss}):
                cons = side["consumed"].get(acct) or {}
                rows = orders.get((acct, ss), [])
                held = float(cons.get("held") or 0)
                if cons and "held" not in cons:
                    held = float(next((r.get("held") for r in rows if r.get("held") is not None), 0) or 0)
                    if not rows:
                        want = ["?held_unknown"]
                if cons and "held" not in cons and not rows:
                    pass
                elif not side["state_ok_at"]:
                    want = ["?stale"]
                elif not st.get("open"):
                    want = ["CLOSE"] if held > 0 else []
                elif held <= 0:
                    want = ["OPEN"] if cons.get("allowed") is not False else ["NOT_IN_BOOKS"]
                else:
                    want = [x for x in board_acts if x in ("AUGMENT", "REDUCE")]
                sent = [r for r in rows if r.get("status") == "SENT"]
                failed = [r for r in rows if r.get("status") not in ("SENT",)]
                sent_acts = [r.get("act") for r in sent]
                if want == ["?held_unknown"]:
                    verdict = "unverifiable_old_trader"
                elif want == ["?stale"]:
                    verdict = "stale_state" if not sent else "extra"
                elif want == ["NOT_IN_BOOKS"]:
                    verdict = "not_in_books" if not sent else "extra"
                elif not want and not rows:
                    verdict = "noop"
                elif not want:
                    verdict = "extra" if sent else "noop"
                elif any(w in sent_acts for w in want):
                    r = next(r for r in sent if r.get("act") in want)
                    ok, detail = expected_size_ok(r.get("act"), r, dict(cons, held=held), st, self.start_size)
                    lag = float(r.get("ts") or 0) - b["close"]
                    verdict = ("match" if lag <= LATE_S else "match_late") if ok else "wrong_size"
                elif sent:
                    verdict = "wrong_direction"
                elif failed:
                    verdict = "blocked:" + str(failed[-1].get("detail") or failed[-1].get("status"))[:90]
                else:
                    verdict = "missed"
                verify = side["verify"] or {}
                if side["state_ok_at"] and not (verify.get("settings_ok") and verify.get("engine_ok")):
                    verdict = f"settings_mismatch|{verdict}"
                fill = None
                for r in sent:
                    resp = r.get("resp") or {}
                    fill = {"order_ts": r.get("ts"), "status": resp.get("status"), "executedQty": resp.get("executedQty"), "qty": r.get("qty"), "price": r.get("price")}
                out.append({"acct": acct, "ss": ss, "want": want, "board_acts": board_acts, "held_at_plan": held, "sent": sent_acts,
                            "failed": [f"{r.get('act')}:{r.get('status')}:{str(r.get('detail') or '')[:60]}" for r in failed], "verdict": verdict,
                            "order_sent_s": round(float(sent[0]["ts"]) - b["close"], 2) if sent else None, "fill": fill,
                            "planned_s": round(float(cons["at"]) - b["close"], 2) if cons.get("at") else None})
        return out

    def summary(self, final=False, audit=None):
        b = self.bar
        c = b["close"]
        sy, sd = b["syms"].values(), b["sides"].values()
        rel = lambda t: None if t in (None, "n/a") else round(t - c, 2)
        s = {"venue": self.venue, "bar": b["bar"], "bar_iso": self.iso(b["bar"]), "close_iso": self.iso(c), "final": final, "active_accounts": b["active"],
             "symbols_expected": len(b["syms"]), "sides_expected": len(b["sides"]),
             "received": sum(1 for r in sy if r["recv_at"]), "received_mac": sum(1 for r in sy if r["src"] == "mac"),
             "received_fallback": sum(1 for r in sy if r["src"] == "fallback"), "appended": sum(1 for r in sy if r["append_at"]),
             "states_ok": sum(1 for r in sd if r["state_ok_at"]), "states_any": sum(1 for r in sd if r["state_at"]),
             "states_verified": sum(1 for r in sd if r["verify"] and r["verify"]["settings_ok"] and r["verify"]["engine_ok"]),
             "executed": sum(1 for r in sd if r["exec_at"] not in (None, "n/a")), "exec_na": sum(1 for r in sd if r["exec_at"] == "n/a"),
             "latency_s": {"recv": lat_stats([rel(r["recv_at"]) for r in sy]), "append": lat_stats([rel(r["append_at"]) for r in sy]),
                           "state_ok": lat_stats([rel(r["state_ok_at"]) for r in sd]), "exec": lat_stats([rel(r["exec_at"]) for r in sd]),
                           "exec_after_state": lat_stats([None if r["exec_at"] in (None, "n/a") or not r["state_ok_at"] else round(r["exec_at"] - r["state_ok_at"], 2) for r in sd])},
             "missing": {"recv": sorted(s for s, r in b["syms"].items() if not r["recv_at"]), "append": sorted(s for s, r in b["syms"].items() if not r["append_at"]),
                         "state_ok": sorted(s for s, r in b["sides"].items() if not r["state_ok_at"]),
                         "exec": sorted(s for s, r in b["sides"].items() if r["state_ok_at"] and not r["exec_at"]),
                         "verified": sorted(s for s, r in b["sides"].items() if r["verify"] and not (r["verify"]["settings_ok"] and r["verify"]["engine_ok"]))},
             "repairs": b["repairs_n"], "repaired": {"recv": sorted(s for s, r in b["syms"].items() if any(x["stage"] == "recv" for x in r["repairs"])),
                                                     "append": sorted(s for s, r in b["syms"].items() if any(x["stage"] == "append" for x in r["repairs"])),
                                                     "state": sorted(s for s, r in b["sides"].items() if any(x["stage"] == "state" for x in r["repairs"])),
                                                     "exec": sorted(s for s, r in b["sides"].items() if any(x["stage"] == "exec" for x in r["repairs"]))},
             "alerts": b["alerts"], "simulated_missing": b["sim"]}
        if audit is not None:
            counts = {}
            for a in audit:
                k = a["verdict"].split(":", 1)[0]
                counts[k] = counts.get(k, 0) + 1
            acted = [a for a in audit if a["want"] and a["want"] not in (["NOT_IN_BOOKS"],)]
            exact = [a for a in audit if a["verdict"] in ("match", "noop", "not_in_books")]
            s["audit"] = {"pairs": len(audit), "verdicts": counts, "exact_match_rate": round(len(exact) / len(audit), 4) if audit else None,
                          "dictated_actions": len(acted), "dictated_matched": sum(1 for a in acted if a["verdict"] == "match"),
                          "order_sent_s": lat_stats([a["order_sent_s"] for a in audit]),
                          "not_exact": [f"{a['acct']}:{a['ss']}:{a['verdict']}:want={a['want']}:sent={a['sent']}" for a in audit if a["verdict"] not in ("match", "noop", "not_in_books")][:60]}
        return s

    def finalize(self, now):
        b = self.bar
        c = b["close"]
        rel = lambda t: None if t in (None, "n/a") else round(t - c, 2)
        try:
            audit = self.audit()
        except Exception as exc:
            log(f"audit failed {type(exc).__name__}: {exc}")
            audit = []
        s = self.summary(final=True, audit=audit)
        path = self.out / f"{self.venue}_{dt.datetime.fromtimestamp(b['bar'], dt.timezone.utc):%Y-%m-%d}.jsonl"
        with open(path, "a") as fh:
            for ss, r in b["sides"].items():
                sym = b["syms"][ss.rsplit("_", 1)[0]]
                fh.write(json.dumps({"kind": "side", "bar": b["bar"], "bar_iso": s["bar_iso"], "sym_side": ss, "accounts": r["accts"], "held_by": r["held"],
                                     "bar_received_s": rel(sym["recv_at"]), "source": sym["src"], "source_detail": sym["src_detail"],
                                     "npz_appended_s": rel(sym["append_at"]), "state_written_s": rel(r["state_at"]), "state_ok_s": rel(r["state_ok_at"]),
                                     "state_reason": r["reason"], "verify": r["verify"], "executed_s": rel(r["exec_at"]) if r["exec_at"] != "n/a" else "n/a",
                                     "orders": r["orders"], "repairs": sym["repairs"] + r["repairs"]}, default=str) + "\n")
            for a in audit:
                fh.write(json.dumps({"kind": "audit", "bar": b["bar"], "bar_iso": s["bar_iso"], **a}, default=str) + "\n")
            fh.write(json.dumps({"kind": "bar", **s}, default=str) + "\n")
        self.history = (self.history + [s])[-8:]
        au = s.get("audit") or {}
        log(f"FINAL {self.venue} bar {s['bar_iso']}: syms {s['received']}/{s['symbols_expected']} recv (mac {s['received_mac']} fb {s['received_fallback']}) "
            f"append {s['appended']} states_ok {s['states_ok']}/{s['sides_expected']} verified {s['states_verified']} exec {s['executed']}(+{s['exec_na']} n/a) "
            f"repairs {s['repairs']} audit {au.get('verdicts')} exact {au.get('exact_match_rate')} lat {json.dumps(s['latency_s'])}")
        self.bar = None

    def write_status(self, now):
        cur = self.summary() if self.bar else None
        bad = [h for h in self.history if any(h["missing"][k] for k in h["missing"]) or (h.get("audit") or {}).get("not_exact")]
        atomic_json(self.out / f"status_{self.venue}.json", {"epoch": now, "iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                            "host": socket.gethostname(), "pid": os.getpid(), "venue": self.venue, "current": cur,
                                                            "last_bars": self.history, "ok": not bad, "bars_with_gaps": [h["bar_iso"] for h in bad]})

    def loop(self):
        log(f"s1_bar_guarantee {self.venue} start base={self.base} live={self.live} states={self.state_dir} store={self.store} start_size={self.start_size}")
        self.follow_receipts()
        while True:
            now = time.time()
            try:
                bar = int(now // BAR_S) * BAR_S - BAR_S
                if self.bar and (now - self.bar["close"] >= FINAL_S or (self.bar["bar"] != bar and self.complete())):
                    self.finalize(now)
                if self.bar is None and (not self.history or self.history[-1]["bar"] < bar) and self.tracked_bar(bar) and now - (bar + BAR_S) < FINAL_S:
                    if not self.history or self.history[-1]["bar"] != bar:
                        self.new_bar(bar, now)
                if self.bar:
                    self.observe(now)
                    self.repair(now)
                    if self.complete() and "complete" not in self.bar["done_stage"]:
                        self.bar["done_stage"].add("complete")
                        log(f"{self.venue} bar {self.iso(self.bar['bar'])} COMPLETE at +{now - self.bar['close']:.1f}s")
                self.procs = [(p, f) for p, f in self.procs if p.poll() is None or f.close()]
                if now - self.last_status >= 2.0:
                    self.last_status = now
                    self.write_status(now)
            except Exception as exc:
                log(f"loop error {type(exc).__name__}: {str(exc)[:300]}")
            time.sleep(max(0.05, 1.0 - (time.time() - now)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=("crypto", "stocks"), default="crypto")
    ap.add_argument("--base", default=str(ROOT))
    ap.add_argument("--live-root", default=str(Path.home() / "binance"))
    ap.add_argument("--state-dir", default="")
    ap.add_argument("--store-db", default=os.environ.get("PER_SYM_STORE_DB", ""))
    ap.add_argument("--local-ip", default=os.environ.get("VD_LOCAL_IP", "5.75.211.216"))
    ap.add_argument("--repair-append", default="")
    ap.add_argument("--bar", type=int, default=0)
    a = ap.parse_args()
    if a.repair_append:
        return repair_append(Path(a.base).expanduser(), [s for s in a.repair_append.split(",") if s], a.bar, "crypto" if a.venue == "crypto" else "tradier")
    import fcntl
    out = Path(a.live_root).expanduser() / "data" / "bar_guarantee"
    out.mkdir(parents=True, exist_ok=True)
    lock = open(out / f".lock_{a.venue}", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another s1_bar_guarantee holds the lock; exiting")
        return 0
    Guarantee(a).loop()


if __name__ == "__main__":
    sys.exit(main())
