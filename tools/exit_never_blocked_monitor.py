#!/usr/bin/env python3
"""EXIT NEVER BLOCKED monitor (USER 2026-10-14 "make sure at least EXITS will never be compromised again").

Runs every minute (launchd com.niels.exit_never_blocked_monitor). Read-only on trading state; writes only alerts/status.
Two inputs, both scanned incrementally (byte offsets in the state file):
  1. data/safety/exit_events/{venue}_{YYYYMMDD}.jsonl   (exit_guarantee wrapper, active after the next manager restart)
     episode opens at a RESULT event with cls REFUSED/VERIFY (or ESCALATE/WATCH/ALERT); closes at CONFIRMED / WATCH_DONE /
     FORCE_RESULT FORCE_NOTHING_OPEN / a later RESULT cls OK|NOOP|POLICY for the key.
  2. ~/logs/ez_manage_{acct}.log  (works today, before the restart)
     decision  = "[VEC_EXACT_EXEC] <pk> action=CLOSE" or "action=CLOSE reason=FIXED_STOP" lines
     confirmed = "[POSITIONS_CONFIRMED] <pk>: SUCCESS kind=reduce" / "[EXIT_FORCE_CONFIRMED] <pk>" / "[EXIT_FORCE] <pk>: broker FLAT"
An episode open longer than --minutes (default 5) -> one alert per episode: data/alerts/exit_never_blocked_monitor.jsonl +
macOS notification. Status snapshot: data/safety/exit_monitor_status.json.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = Path(os.path.expanduser("~/logs"))
ACCOUNTS = ("ang", "fin", "men", "flz", "inf")
OPEN_CLS = {"REFUSED", "VERIFY"}
CLOSE_EVENTS = {"CONFIRMED", "WATCH_DONE"}
RE_TS = re.compile(r"^(\d{2}) (\d{2}):(\d{2}):(\d{2})")
RE_DECISION = re.compile(r"\[VEC_EXACT_EXEC\] (\S+) action=CLOSE|\] (\S+): .*action=CLOSE reason=(FIXED_STOP\S*)")
RE_CONFIRMED = re.compile(r"\[POSITIONS_CONFIRMED\] (\S+): SUCCESS kind=reduce.* post=0(?:\.0+)? |\[EXIT_FORCE_CONFIRMED\] (\S+):|\[EXIT_FORCE\] (\S+): broker FLAT")


def _now() -> float:
    return time.time()


def _load(p: Path, d):
    try:
        return json.loads(p.read_text())
    except Exception:
        return d


def _save(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, p)


def _read_new(path: str, offsets: dict):
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    off = int(offsets.get(path, 0))
    if off > size:
        off = 0
    if path not in offsets and size > 2_000_000:
        off = size - 2_000_000  # first run on a big log: last ~2 MB only
    with open(path, "rb") as fh:
        fh.seek(off)
        data = fh.read()
    offsets[path] = off + len(data)
    return data.decode("utf-8", "ignore").splitlines()


def _log_ts(line: str, now: float):
    m = RE_TS.match(line)
    if not m:
        return None
    dd, hh, mi, ss = (int(x) for x in m.groups())
    t = datetime.fromtimestamp(now, timezone.utc)
    try:
        d = t.replace(day=dd, hour=hh, minute=mi, second=ss, microsecond=0)
    except ValueError:
        return None
    e = d.timestamp()
    return e - 86400 * 28 if e > now + 3600 else e  # day-of-month from last month


def scan_events(state: dict, root: Path, now: float) -> None:
    eps = state.setdefault("episodes", {})
    offs = state.setdefault("offsets", {})
    for p in sorted(glob.glob(str(root / "data" / "safety" / "exit_events" / "*_*.jsonl")))[-6:]:
        for ln in _read_new(p, offs):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            key = f"{e.get('venue')}:{e.get('key')}"
            ev = e.get("event")
            ts = _iso(e.get("ts")) or now
            if (ev == "RESULT" and e.get("cls") in OPEN_CLS) or ev in ("ESCALATE", "WATCH", "ALERT"):
                ep = eps.setdefault(key, {"since": ts, "src": "events", "alerted": False})
                ep["last"] = str(e.get("result") or e.get("why") or e.get("msg") or ev)[:160]
                ep["reason"] = str(e.get("reason") or ep.get("reason") or "")[:120]
            elif ev in CLOSE_EVENTS or (ev == "RESULT" and e.get("cls") in ("OK", "NOOP", "POLICY")) or (ev == "FORCE_RESULT" and str(e.get("result", "")).startswith(("SUCCESS", "FORCE_NOTHING_OPEN"))):
                if key in eps and eps[key].get("src") == "events":
                    eps.pop(key, None)


def scan_logs(state: dict, log_dir: Path, now: float) -> None:
    eps = state.setdefault("episodes", {})
    offs = state.setdefault("offsets", {})
    for acct in ACCOUNTS:
        p = log_dir / f"ez_manage_{acct}.log"
        for ln in _read_new(str(p), offs):
            m = RE_DECISION.search(ln)
            if m:
                pk = m.group(1) or m.group(2)
                key = f"log:{pk}"
                if key not in eps:
                    eps[key] = {"since": _log_ts(ln, now) or now, "src": "log", "alerted": False, "last": ln[-160:]}
                continue
            m = RE_CONFIRMED.search(ln)
            if m:
                pk = m.group(1) or m.group(2) or m.group(3)
                eps.pop(f"log:{pk}", None)


def _iso(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except Exception:
        return None


def _notify(msg: str) -> None:
    if sys.platform != "darwin" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        safe = msg.replace('"', "'")[:200]
        subprocess.Popen(["osascript", "-e", f'display notification "{safe}" with title "EXIT NOT FILLED" sound name "Basso"'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def evaluate(state: dict, root: Path, now: float, minutes: float, max_age_h: float = 24.0, broker_flat=None) -> list:
    out = []
    eps = state.setdefault("episodes", {})
    for key, ep in list(eps.items()):
        age = now - float(ep.get("since") or now)
        if age > max_age_h * 3600:
            eps.pop(key, None)
            continue
        if age >= minutes * 60 and not ep.get("alerted"):
            if broker_flat is not None:
                try:
                    if broker_flat(key, float(ep.get("since") or 0.0)):
                        eps.pop(key, None)
                        continue
                except Exception:
                    pass
            ep["alerted"] = True
            rec = {"ts": datetime.now(timezone.utc).isoformat(), "key": key, "open_minutes": round(age / 60, 1), "src": ep.get("src"), "last": ep.get("last"), "reason": ep.get("reason", "")}
            out.append(rec)
    if out:
        p = root / "data" / "alerts" / "exit_never_blocked_monitor.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as fh:
            for r in out:
                fh.write(json.dumps(r, default=str) + "\n")
        _notify(f"{len(out)} close(s) without confirmed fill > {minutes:g} min: " + ", ".join(r["key"] for r in out[:4]))
    return out


def positions_file_flat(root: Path, key: str, since: float = 0.0) -> bool:
    """True when {acct}/{long|short}_positions.json (broker-reflected, written by the positions service after fills) shows
    the key flat or absent — the close happened by a path that printed no confirmation line (exchange stop, WS fill)."""
    pk = key.split(":", 1)[1] if key.startswith(("log:", "ez:", "tradier:")) else key
    if ":" not in pk:
        return False
    acct, rest = pk.split(":", 1)
    side = "long" if rest.endswith("_LONG") else "short"
    p = root / acct / f"{side}_positions.json"
    try:
        if time.time() - p.stat().st_mtime > 300:
            return False  # stale file proves nothing
        d = json.loads(p.read_text())
    except Exception:
        return False
    row = d.get(pk) if isinstance(d, dict) else None
    try:
        if row is None or abs(float(row.get("positionAmt") or 0.0)) <= 0.0:
            return True
        oa = _iso(row.get("opened_at"))
        return bool(oa and since and oa > since + 5)  # a NEW lifecycle opened after the close decision: that close happened
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=float(os.environ.get("EXIT_MONITOR_MINUTES", "5")))
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--log-dir", default=str(LOG_DIR))
    ap.add_argument("--no-logs", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root)
    sp = root / "data" / "safety" / "exit_monitor_state.json"
    state = _load(sp, {})
    now = _now()
    scan_events(state, root, now)
    if not a.no_logs:
        scan_logs(state, Path(a.log_dir), now)
    alerts = evaluate(state, root, now, a.minutes, broker_flat=lambda k, since: positions_file_flat(root, k, since))
    _save(sp, state)
    status = {"ts": datetime.now(timezone.utc).isoformat(), "open_episodes": len(state.get("episodes", {})), "alerted_now": alerts, "episodes": state.get("episodes", {})}
    _save(root / "data" / "safety" / "exit_monitor_status.json", status)
    print(json.dumps({"open": status["open_episodes"], "alerts": len(alerts)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
