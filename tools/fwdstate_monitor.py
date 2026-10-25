#!/usr/bin/env python3
"""fwdstate_monitor — USER 2026-10-25 FINAL: MONITOR the S1/S5 dictate chain.

Every run verifies, per sym_side STATE file in data/forward_state/:
  1. KLINE->ROW: file npz_tail vs last closed 15m bar (compute/keeper stall?).
  2. PULL: file mtime age (S1->Mac pull stall?).
  3. LIVE AGREEMENT: STATE open flag vs every account's live position files
     (ang/inf/flz/men/fin crypto + tra/trb/trc stocks). A divergence the
     in-engine VEC backstop has not closed within DIVERGE_ALERT_S means the
     enforcement path itself is broken -> CRITICAL.

Observe-only: never places orders, never writes state files. Enforcement
stays in execute_now (VEC backstop). Snapshot -> data/fwdstate_monitor.json,
heartbeat -> /tmp/fwdstate_monitor.log. Launchd StartInterval 15.
"""
import glob
import json
import os
import re
import sys
import time

ROOT = Path = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE_DIR = ROOT / "data" / "forward_state"
SNAP = ROOT / "data" / "fwdstate_monitor.json"
SEEN = ROOT / "data" / "fwdstate_monitor_seen.json"
LOG = "/tmp/fwdstate_monitor.log"
BAR_S = 900.0
COMPUTE_ALERT_S = 1800.0
PULL_ALERT_S = 600.0
DIVERGE_ALERT_S = 300.0
ACCTS = ("ang", "inf", "flz", "men", "fin", "tra", "trb", "trc")


def wall_bar(now):
    return int(now // BAR_S) * int(BAR_S)


def live_open(acct, sym, side):
    for fname in ("long_positions.json", "short_positions.json"):
        p = ROOT / acct / fname
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            try:
                if abs(float((v or {}).get("positionAmt") or 0)) <= 1e-9:
                    continue
            except (TypeError, ValueError):
                continue
            s = str((v or {}).get("symbol") or "")
            if not s:
                parts = str(k).split(":")
                s = parts[1] if len(parts) > 1 else parts[0]
                s = re.sub(r"_(LONG|SHORT)$", "", s.upper())
            if s.upper() != sym:
                continue
            sd = "LONG" if "long" in fname else "SHORT"
            if sd == side:
                return True
    return False


def is_crypto(sym):
    s = str(sym or "").upper()
    return s.endswith("USDT") or s.endswith("USDC")


def main():
    now = time.time()
    wb = wall_bar(now)
    try:
        import datetime as _dt
        weekend = _dt.datetime.now(_dt.timezone.utc).weekday() >= 5
    except Exception:
        weekend = False
    try:
        seen = json.loads(SEEN.read_text())
        seen = seen if isinstance(seen, dict) else {}
    except Exception:
        seen = {}
    stalls, pulls, divs = [], [], []
    n = 0
    max_compute_lag = 0.0
    for f in glob.glob(str(STATE_DIR / "*.json")):
        m = re.match(r"(.+)_(LONG|SHORT)\.json$", os.path.basename(f))
        if not m:
            continue
        sym, side = m.group(1).upper(), m.group(2)
        n += 1
        try:
            d = json.loads(open(f).read())
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("ok"):
            continue
        try:
            mt = os.stat(f).st_mtime
        except Exception:
            continue
        tail = d.get("npz_tail") or 0
        try:
            bar_lag = (wb - float(tail)) if tail else 0.0
        except (TypeError, ValueError):
            bar_lag = 0.0
        if bar_lag > COMPUTE_ALERT_S and (is_crypto(sym) or not weekend):
            stalls.append({"symside": f"{sym}_{side}", "bar_lag_s": round(bar_lag)})
            max_compute_lag = max(max_compute_lag, bar_lag)
        if now - mt > PULL_ALERT_S and (is_crypto(sym) or not weekend):
            pulls.append({"symside": f"{sym}_{side}", "file_age_s": round(now - mt)})
        want = d.get("open")
        if not isinstance(want, bool):
            continue
        for acct in ACCTS:
            lo = live_open(acct, sym, side)
            if lo == want:
                seen.pop(f"{acct}:{sym}_{side}", None)
                continue
            key = f"{acct}:{sym}_{side}"
            first = seen.get(key) or now
            seen[key] = first
            age = now - first
            if age > DIVERGE_ALERT_S:
                divs.append({"key": key, "live": lo, "want": want,
                             "diverge_s": round(age),
                             "tail_bar": time.strftime("%H:%M", time.gmtime(float(tail))) if tail else "?"})
    try:
        SEEN.write_text(json.dumps(seen))
    except Exception:
        pass
    snap = {"checked_ts": now, "checked": time.strftime("%FT%TZ", time.gmtime(now)),
            "n_syms": n, "max_compute_lag_s": round(max_compute_lag),
            "compute_stalls": stalls[:20], "pull_stalls": pulls[:20],
            "divergences": divs[:40]}
    try:
        SNAP.write_text(json.dumps(snap, indent=1))
    except Exception:
        pass
    line = (f"[{snap['checked']}] n={n} maxlag={snap['max_compute_lag_s']}s "
            f"compute_stalls={len(stalls)} pull_stalls={len(pulls)} divergences={len(divs)}")
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
            for dv in divs[:10]:
                fh.write(f"  CRITICAL DIVERGENCE {dv}\n")
            for st in (stalls[:5] + pulls[:5]):
                fh.write(f"  WARN STALL {st}\n")
    except Exception:
        pass
    print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
