#!/usr/bin/env python3
"""s1_bar_guarantee — per-bar, per-symbol completeness ledger + escalating self-repair for S1's live crypto chain.

USER 2026-10-28 (verbatim): "make sure s1 never misses a beat in the sense that is produces or receives a 15m kline for every
tradeable symbol, calculates its nps and state for each side and executes it immediately".

Chain watched per closed 15m bar B (close C = B + 900) for every crypto side in ~/binance/tradeable_keys.json plus every held
side (~/binance/data/vector_direct/held_crypto.json):
  BAR     data/closed_15m_receipts.jsonl first receipt of (symbol, B)          source mac | fallback (row _producer)
  NPZ     backtest_v8/indicators/.tail/<SYM>.jsonl tip >= B (or zip tip)        tools/npz_tail_service.py
  STATE   data/forward_state/<SYM_SIDE>.json npz_tail >= B and ok               tools/fwdstate_npz_watch.py
  EXEC    ~/binance/data/vector_direct/consumed_crypto.json: every active account holding/keyed on the side planned against
          that state (tools/vector_direct_trader.py writes it per pass)
Repairs, escalating by deadline after C:
  +35 s  bar missing          -> fetch exactly those symbols from Binance (closed kline) and land them via receive_closed_15m
  +45 s  bar not in the NPZ   -> append exactly those symbols (subprocess, same functions as npz_tail_service)
  +90 s  no ok state          -> data/forward_state_priority/<SYM_SIDE> (fwdstate_npz_watch recomputes at class -1)
  ok state not consumed 10 s  -> ~/binance/data/vector_direct/PLAN_NOW_crypto (trader wakes and plans every active account)
Every stage missing at its deadline -> data/bar_guarantee/ALERTS.jsonl + the trader's data/vector_direct/ALERTS.jsonl.
Ledger: ~/binance/data/bar_guarantee/<day>.jsonl (one row per side per bar + one bar summary); status status.json.
Test mode: data/bar_guarantee/SIMULATE_MISSING.json {"symbols": [...], "until_epoch": t} — receipts of those symbols are
ignored (the Mac bar is treated as missing), so the +35 s repair fetches and lands the exchange bar for real. Never orders.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
BAR_S = 900
SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
RECV_DEADLINE_S = 35.0
APPEND_DEADLINE_S = 45.0
STATE_DEADLINE_S = 90.0
EXEC_AFTER_STATE_S = 10.0
FINAL_S = 600.0


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


def universe(live_root: Path):
    keys = {}
    for k in read_json(live_root / "tradeable_keys.json", []) or []:
        if ":" not in str(k):
            continue
        acct, ss = str(k).split(":", 1)
        ss = ss.upper()
        if ss.rsplit("_", 1)[0].endswith(SUFFIXES) and ss.endswith(("_LONG", "_SHORT")):
            keys.setdefault(acct, set()).add(ss)
    held = read_json(live_root / "data" / "vector_direct" / "held_crypto.json", {}) or {}
    held_by = {a: {str(s).upper() for s in v} for a, v in (held.get("by_account") or {}).items()}
    sides = set().union(*keys.values(), *held_by.values()) if (keys or held_by) else set()
    return keys, held_by, sides


def repair_append(base: Path, symbols, bar):
    """Subprocess body: append the closed bar `bar` for exactly these symbols (npz_tail_service functions)."""
    import npz_tail_service as T
    out = {}
    for symbol in symbols:
        try:
            npz = T.npz_for(base, symbol)
            if npz is None:
                out[symbol] = "no npz"
                continue
            lc = T.kline_last_close(base, symbol, "crypto")
            if lc is None or int(lc) < bar:
                out[symbol] = f"source has no bar {bar} (last {lc})"
                continue
            revision = T.source_revision(base, symbol, "crypto", lc)
            row = T._compute_row_process((symbol, int(lc), str(base), "crypto"))
            if not row:
                out[symbol] = "compute returned nothing"
                continue
            out[symbol] = T.sweep_symbol(npz, symbol, lc, lambda _s, _c, r=row: r, revision=revision)
        except Exception as exc:
            out[symbol] = f"{type(exc).__name__}: {str(exc)[:120]}"
    print(json.dumps(out), flush=True)
    return 0


class Guarantee:
    def __init__(self, a):
        self.a = a
        self.base = Path(a.base).expanduser()
        self.live = Path(a.live_root).expanduser()
        self.state_dir = Path(a.state_dir).expanduser() if a.state_dir else self.base / "data" / "forward_state"
        self.vd = self.live / "data" / "vector_direct"
        self.out = self.live / "data" / "bar_guarantee"
        self.out.mkdir(parents=True, exist_ok=True)
        self.prio = self.base / "data" / "forward_state_priority"
        self.receipts = self.base / "data" / "closed_15m_receipts.jsonl"
        self.ind = self.base / "backtest_v8" / "indicators"
        self.rc_pos = None
        self.recv = {}  # (symbol, bar) -> received_at (first)
        self.bar = None
        self.history = []
        self.state_cache = {}
        self.consumed = ({}, None)
        self.zip_cache = {}
        self.session = None
        self.procs = []
        self.last_poke = 0.0
        self.last_status = 0.0

    # ── inputs ──
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
            if r.get("mode") != "crypto":
                continue
            k = (r.get("symbol"), int(r.get("bar_start_ts") or 0))
            if k not in self.recv:
                self.recv[k] = float(r.get("received_at") or 0)
        cutoff = time.time() - 3 * 3600
        if len(self.recv) > 20000:
            self.recv = {k: v for k, v in self.recv.items() if k[1] > cutoff}

    def row_source(self, symbol, bar):
        from closed_bar_feed import timestamp_epoch
        for r in reversed(read_json(self.base / "data" / "closed_15m_bars" / "crypto" / f"{symbol}.json", []) or []):
            try:
                if timestamp_epoch(r.get("timestamp")) == bar:
                    return "fallback" if r.get("_producer") == "s1_closed_bar_fallback" else "mac", r.get("_source")
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
        p = self.vd / "consumed_crypto.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return {}
        if self.consumed[1] != mt:
            self.consumed = ((read_json(p, {}) or {}).get("sides") or {}, mt)
        return self.consumed[0]

    def simulate(self, now):
        d = read_json(self.out / "SIMULATE_MISSING.json", {}) or {}
        if float(d.get("until_epoch") or 0) < now:
            return set()
        return {str(s).upper() for s in d.get("symbols") or []}

    # ── bar lifecycle ──
    def new_bar(self, bar, now):
        keys, held_by, sides = universe(self.live)
        status = read_json(self.vd / "status_crypto.json", {}) or {}
        active = list(status.get("active_accounts") or [])
        expect = {}
        for ss in sides:
            expect[ss] = sorted(a for a in active if ss in keys.get(a, ()) or ss in held_by.get(a, ()))
        syms = sorted({ss.rsplit("_", 1)[0] for ss in sides})
        sim = self.simulate(now)
        self.bar = {"bar": bar, "close": bar + BAR_S, "started": now, "active": active, "sim": sorted(sim & set(syms)),
                    "syms": {s: {"recv_at": None, "src": None, "src_detail": None, "append_at": None, "repairs": []} for s in syms},
                    "sides": {ss: {"accts": expect[ss], "held": any(ss in v for v in held_by.values()), "state_at": None,
                                   "state_ok_at": None, "reason": None, "exec_at": None, "orders": [], "repairs": []} for ss in sorted(sides)},
                    "done_stage": set(), "alerts": [], "repairs_n": 0}
        log(f"bar {self.iso(bar)} tracking {len(syms)} symbols / {len(sides)} sides, active={active} sim={self.bar['sim']}")

    @staticmethod
    def iso(ts):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    def alert(self, stage, items, repair):
        b = self.bar
        row = {"iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "epoch": time.time(), "host": socket.gethostname(),
               "venue": "crypto", "kind": f"BAR_GUARANTEE_{stage}", "bar": b["bar"], "bar_iso": self.iso(b["bar"]),
               "after_close_s": round(time.time() - b["close"], 1), "n": len(items), "items": sorted(items)[:200], "repair": repair}
        for path in (self.out / "ALERTS.jsonl", self.vd / "ALERTS.jsonl"):
            try:
                with open(path, "a") as fh:
                    fh.write(json.dumps(row) + "\n")
            except OSError:
                pass
        b["alerts"].append({"stage": stage, "n": len(items), "after_close_s": row["after_close_s"]})
        log(f"ALERT {stage} {len(items)}: {sorted(items)[:12]} repair={repair}")

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
                        r["orders"] = sorted(set(r["orders"]) | {f"{acct}:{x}" for x in c.get("orders") or []})
                if len(ats) == len(r["accts"]):
                    r["exec_at"] = max(ats)

    def repair(self, now):
        b = self.bar
        age = now - b["close"]
        sim = set(b["sim"])
        # BAR
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
                    self.alert("BAR_MISSING", missing, f"fallback fetch at +{age:.0f}s: " + json.dumps({k: v for k, v in res.items()})[:400])
        # NPZ
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
        # STATE
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
        # EXEC
        late = [ss for ss, r in b["sides"].items() if r["state_ok_at"] and r["exec_at"] is None and now - r["state_ok_at"] >= EXEC_AFTER_STATE_S]
        if late and now - self.last_poke >= 5.0:
            self.last_poke = now
            try:
                (self.vd / "PLAN_NOW_crypto").write_text(f"s1_bar_guarantee bar {b['bar']} {len(late)} sides e.g. {late[:3]}")
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

    def fetch_bars(self, symbols, bar):
        import concurrent.futures as cf
        import s1_closed_bar_fallback as F
        from closed_bar_feed import receive_closed_15m
        if self.session is None:
            self.session = F.session(self.a.local_ip)
        sim = set(self.bar["sim"])

        def one(symbol):
            try:
                if symbol not in sim and F.has_bar(self.base, symbol, bar):
                    return symbol, "arrived_meanwhile"
                row, why = F.fetch_bar(self.session, symbol, bar)
                if row is None:
                    return symbol, why
                row["_producer"] = "s1_closed_bar_fallback"
                row["_repair"] = "s1_bar_guarantee" + ("_simulated_missing" if symbol in sim else "")
                ev = {"symbol": symbol, "mode": "crypto", "bar_start_ts": bar, "bar_close_ts": bar + BAR_S, "published_at": time.time(), "row": row}
                rc = receive_closed_15m(str(self.base), ev)
                r = self.bar["syms"][symbol]
                if r["recv_at"] is None:
                    r["recv_at"], r["src"], r["src_detail"] = rc["received_at"], "fallback", "s1_bar_guarantee"
                return symbol, "landed"
            except Exception as exc:
                return symbol, f"{type(exc).__name__}: {str(exc)[:100]}"
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            return dict(ex.map(one, symbols))

    def spawn_append(self, symbols, bar):
        logf = open(self.out / "repair_append.log", "a")
        p = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--repair-append", ",".join(symbols), "--bar", str(bar),
                              "--base", str(self.base)], cwd=str(self.base), stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append((p, logf))
        log(f"REPAIR append pid {p.pid} for {symbols[:10]}")

    def complete(self):
        b = self.bar
        return all(r["append_at"] for r in b["syms"].values()) and all(r["state_ok_at"] and r["exec_at"] for r in b["sides"].values())

    def summary(self, final=False):
        b = self.bar
        c = b["close"]
        sy, sd = b["syms"].values(), b["sides"].values()
        rel = lambda t: None if t in (None, "n/a") else round(t - c, 2)
        return {"bar": b["bar"], "bar_iso": self.iso(b["bar"]), "close_iso": self.iso(c), "final": final, "active_accounts": b["active"],
                "symbols_expected": len(b["syms"]), "sides_expected": len(b["sides"]),
                "received": sum(1 for r in sy if r["recv_at"]), "received_mac": sum(1 for r in sy if r["src"] == "mac"),
                "received_fallback": sum(1 for r in sy if r["src"] == "fallback"), "appended": sum(1 for r in sy if r["append_at"]),
                "states_ok": sum(1 for r in sd if r["state_ok_at"]), "states_any": sum(1 for r in sd if r["state_at"]),
                "executed": sum(1 for r in sd if r["exec_at"] not in (None, "n/a")), "exec_na": sum(1 for r in sd if r["exec_at"] == "n/a"),
                "latency_s": {"recv": lat_stats([rel(r["recv_at"]) for r in sy]), "append": lat_stats([rel(r["append_at"]) for r in sy]),
                              "state_ok": lat_stats([rel(r["state_ok_at"]) for r in sd]),
                              "exec": lat_stats([rel(r["exec_at"]) for r in sd]),
                              "exec_after_state": lat_stats([None if r["exec_at"] in (None, "n/a") or not r["state_ok_at"] else round(r["exec_at"] - r["state_ok_at"], 2) for r in sd])},
                "missing": {"recv": sorted(s for s, r in b["syms"].items() if not r["recv_at"]), "append": sorted(s for s, r in b["syms"].items() if not r["append_at"]),
                            "state_ok": sorted(s for s, r in b["sides"].items() if not r["state_ok_at"]),
                            "exec": sorted(s for s, r in b["sides"].items() if r["state_ok_at"] and not r["exec_at"])},
                "repairs": b["repairs_n"], "repaired": {"recv": sorted(s for s, r in b["syms"].items() if any(x["stage"] == "recv" for x in r["repairs"])),
                                                        "append": sorted(s for s, r in b["syms"].items() if any(x["stage"] == "append" for x in r["repairs"])),
                                                        "state": sorted(s for s, r in b["sides"].items() if any(x["stage"] == "state" for x in r["repairs"])),
                                                        "exec": sorted(s for s, r in b["sides"].items() if any(x["stage"] == "exec" for x in r["repairs"]))},
                "alerts": b["alerts"], "simulated_missing": b["sim"], "orders": sum(len(r["orders"]) for r in sd)}

    def finalize(self, now):
        b = self.bar
        c = b["close"]
        rel = lambda t: None if t in (None, "n/a") else round(t - c, 2)
        path = self.out / f"{dt.datetime.fromtimestamp(b['bar'], dt.timezone.utc):%Y-%m-%d}.jsonl"
        s = self.summary(final=True)
        with open(path, "a") as fh:
            for ss, r in b["sides"].items():
                sym = b["syms"][ss.rsplit("_", 1)[0]]
                fh.write(json.dumps({"kind": "side", "bar": b["bar"], "bar_iso": s["bar_iso"], "sym_side": ss, "accounts": r["accts"], "held": r["held"],
                                     "bar_received_s": rel(sym["recv_at"]), "source": sym["src"], "source_detail": sym["src_detail"],
                                     "npz_appended_s": rel(sym["append_at"]), "state_written_s": rel(r["state_at"]), "state_ok_s": rel(r["state_ok_at"]),
                                     "state_reason": r["reason"], "executed_s": rel(r["exec_at"]) if r["exec_at"] != "n/a" else "n/a",
                                     "orders": r["orders"], "repairs": sym["repairs"] + r["repairs"]}, default=str) + "\n")
            fh.write(json.dumps({"kind": "bar", **s}, default=str) + "\n")
        self.history = (self.history + [s])[-8:]
        log(f"FINAL bar {s['bar_iso']}: syms {s['received']}/{s['symbols_expected']} recv (mac {s['received_mac']} fb {s['received_fallback']}) "
            f"append {s['appended']} states_ok {s['states_ok']}/{s['sides_expected']} exec {s['executed']}(+{s['exec_na']} n/a) repairs {s['repairs']} "
            f"lat {json.dumps(s['latency_s'])}")
        self.bar = None

    def write_status(self, now):
        cur = self.summary() if self.bar else None
        bad = [h for h in self.history if any(h["missing"][k] for k in h["missing"])]
        atomic_json(self.out / "status.json", {"epoch": now, "iso": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                "host": socket.gethostname(), "pid": os.getpid(), "current": cur, "last_bars": self.history,
                                                "ok": not bad, "bars_with_gaps": [h["bar_iso"] for h in bad]})

    def loop(self):
        log(f"s1_bar_guarantee start base={self.base} live={self.live} states={self.state_dir}")
        self.follow_receipts()
        while True:
            now = time.time()
            try:
                bar = int(now // BAR_S) * BAR_S - BAR_S
                if self.bar and (self.bar["bar"] != bar and (self.complete() or now - self.bar["close"] >= FINAL_S)):
                    self.finalize(now)
                elif self.bar and now - self.bar["close"] >= FINAL_S:
                    self.finalize(now)
                if self.bar is None and (not self.history or self.history[-1]["bar"] < bar):
                    self.new_bar(bar, now)
                if self.bar:
                    self.observe(now)
                    self.repair(now)
                    if self.complete() and "complete" not in self.bar["done_stage"]:
                        self.bar["done_stage"].add("complete")
                        log(f"bar {self.iso(self.bar['bar'])} COMPLETE at +{now - self.bar['close']:.1f}s")
                self.procs = [(p, f) for p, f in self.procs if p.poll() is None or f.close()]
                if now - self.last_status >= 2.0:
                    self.last_status = now
                    self.write_status(now)
            except Exception as exc:
                log(f"loop error {type(exc).__name__}: {str(exc)[:300]}")
            time.sleep(max(0.05, 1.0 - (time.time() - now)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT))
    ap.add_argument("--live-root", default=str(Path.home() / "binance"))
    ap.add_argument("--state-dir", default="")
    ap.add_argument("--local-ip", default=os.environ.get("VD_LOCAL_IP", "5.75.211.216"))
    ap.add_argument("--repair-append", default="")
    ap.add_argument("--bar", type=int, default=0)
    a = ap.parse_args()
    if a.repair_append:
        return repair_append(Path(a.base).expanduser(), [s for s in a.repair_append.split(",") if s], a.bar)
    import fcntl
    out = Path(a.live_root).expanduser() / "data" / "bar_guarantee"
    out.mkdir(parents=True, exist_ok=True)
    lock = open(out / ".lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another s1_bar_guarantee holds the lock; exiting")
        return 0
    Guarantee(a).loop()


if __name__ == "__main__":
    sys.exit(main())
