#!/usr/bin/env python3
"""v15_universe — the daily symbol universe for the fleet scheduler (USER 2026-09-30).

STOCKS = symbols with >=1 executed trade event (OPEN/AUGMENT/REDUCE/CLOSE) in the last 30 days in the REAL trade ledger
         data/history/{tra,trb,trc,inf}/SYM_SIDE.jsonl (the same ledger tools/ledger_truth.py reconstructs; the Tradier accounts).
         Either side traded -> BOTH sides are run (pair).
CRYPTO = union of the account symbol lists symbols_{flz,men,ang}_{long,short}.json (NOT inf/fin/trb/tra). Always pairs.
Written to data/daily_universe/<YYYYMMDD>.json.   --extend-universe (scheduler flag, off by default) adds the legacy order files' rest.
  python tools/v15_universe.py [--write] [--push]     # --push rsyncs the file to every host in tools/fleet_hosts*.json
"""
import argparse, datetime as dt, glob, json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "daily_universe"
LAST_GOOD = OUT / "last_good.json"


def _quorum_check(allowed, root):
    """USER 2026-10-10: no single bad read may EVER flip the fleet. A fresh build that collapses
    to <50% of the last-good allowlist (or to empty while last-good exists) is corruption, not
    signal — the tradeable universe moves a handful of keys per day, never halves. Returns
    (ok, reason)."""
    lgp = root / "data" / "daily_universe" / "last_good.json"
    try:
        lg = json.loads(lgp.read_text()) if lgp.exists() else {}
    except Exception:
        lg = {}
    lg_allowed = set(lg.get("allowed_sym_sides") or [])
    n, m = len(allowed), len(lg_allowed)
    if n == 0 and m > 0:
        return False, f"empty build vs last_good {m}"
    if m > 0 and n < 0.5 * m:
        return False, f"collapse {m}->{n} (<50% of last_good)"
    return True, ""


def _persist_last_good(allowed, root):
    try:
        out = root / "data" / "daily_universe"
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / "last_good.tmp"
        tmp.write_text(json.dumps({"allowed_sym_sides": sorted(allowed)}))
        os.replace(tmp, out / "last_good.json")
        return True
    except Exception as e:
        print(f"[universe] WARN last_good persist failed: {e}", file=sys.stderr)
        return False
STOCK_ACCOUNTS = ("tra", "trb", "trc", "inf")
CRYPTO_ACCOUNTS = ("flz", "men", "ang")
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
EVENTS = ("OPEN", "AUGMENT", "REDUCE", "CLOSE")


def stocks_traded(root, days=30, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cut = now - dt.timedelta(days=days)
    sides, newest = {}, None
    for acc in STOCK_ACCOUNTS:
        for f in glob.glob(str(root / "data" / "history" / acc / "*.jsonl")):
            ss = os.path.basename(f)[:-6]
            if not ss.endswith(("_LONG", "_SHORT")):
                continue
            sym = ss.rsplit("_", 1)[0]
            if sym.endswith(CRYPTO_SUFFIX):
                continue
            n = 0
            for l in open(f):
                try:
                    e = json.loads(l)
                    t = dt.datetime.fromisoformat(str(e["ts"]).replace("Z", "+00:00"))
                except Exception:
                    continue
                if newest is None or t > newest:
                    newest = t
                if t >= cut and str(e.get("type", "")).upper() in EVENTS:
                    n += 1
            if n:
                sides.setdefault(sym, {})[ss] = sides.get(sym, {}).get(ss, 0) + n
    return sides, newest


def crypto_symbols(root):
    syms, per = set(), {}
    for acc in CRYPTO_ACCOUNTS:
        for side in ("long", "short"):
            p = root / f"symbols_{acc}_{side}.json"
            try:
                lst = json.loads(p.read_text())
            except Exception:
                continue
            for s in lst:
                s = str(s).strip().upper()
                if s:
                    syms.add(s); per.setdefault(s, set()).add(f"{acc}_{side}")
    return sorted(syms), {k: sorted(v) for k, v in per.items()}


def tradeable_sym_sides(root):
    """USER 2026-10-06: calculations ONLY for tradeable keys — crypto = tradeable_keys.json sym_sides,
    stocks = symbols_trb_long/short.json + TRADIER_MANDATORY_LONG/SHORT_TRB; minus data/universe_exclude.json;
    PLUS every sym_side with an open position (data/open_position_sym_sides.json), even if excluded."""
    import re
    allowed = set()
    try:
        _tk_raw = (root / "tradeable_keys.json").read_text()
        try:
            _tk_data = json.loads(_tk_raw)
        except Exception:
            # USER 2026-10-10: the file is rewritten live and can be caught mid-write (concatenated/
            # truncated tail). raw_decode salvages the first complete array instead of zeroing the
            # whole crypto allowlist (which either idles crypto or fail-opens the entire fleet).
            _tk_data, _ = json.JSONDecoder().raw_decode(_tk_raw.strip())
        allowed |= {str(k).split(":", 1)[-1].upper() for k in _tk_data}
    except Exception as e:
        print(f"[universe] WARN tradeable_keys.json unreadable even robustly: {e} — crypto allowlist empty", file=sys.stderr)
    src = (root / "config_tradier.py").read_text() if (root / "config_tradier.py").exists() else ""
    for side in ("long", "short"):
        syms = set()
        try:
            syms |= {str(s).upper() for s in json.loads((root / f"symbols_trb_{side}.json").read_text())}
        except Exception:
            pass
        m = re.search(rf"TRADIER_MANDATORY_{side.upper()}_TRB\s*=\s*(\[[^\]]*\])", src)
        if m:
            txt = re.sub(r",\s*\]", "]", m.group(1).replace("'", '"'))
            try:
                syms |= {str(s).upper() for s in json.loads(txt)}
            except Exception:
                syms |= {s.upper() for s in re.findall(r'"([A-Za-z0-9._]+)"', m.group(1))}
        allowed |= {f"{s}_{side.upper()}" for s in syms}
    try:
        allowed -= {str(s).upper() for s in json.loads((root / "data" / "universe_exclude.json").read_text()).get("exclude_sym_sides", [])}
    except Exception:
        pass
    allowed |= open_position_sym_sides(root)  # USER 2026-10-06: an OPEN position is always computed, even when excluded / not tradeable
    return allowed


def open_position_sym_sides(root):
    """sym_sides with an open position, from data/open_position_sym_sides.json (Mac tools/v15_open_positions_push.py, every 5 min)."""
    p = root / "data" / "open_position_sym_sides.json"
    try:
        d = json.loads(p.read_text())
    except Exception as e:
        print(f"[universe] WARN open positions unreadable ({p}): {e}", file=sys.stderr)
        return set()
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(d["at"])).total_seconds() / 60
        if age > 60:
            print(f"[universe] WARN open positions file is {age:.0f} min old (Mac push stalled?) — still used", file=sys.stderr)
    except Exception:
        pass
    return {str(s).upper() for s in d.get("sym_sides", [])}


def build(root=ROOT, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    allowed = tradeable_sym_sides(root)
    openpos = open_position_sym_sides(root)
    st, cr_src = {}, {}
    for ss in allowed:
        sym, side = ss.rsplit("_", 1)
        if sym.endswith(("USDT", "USDC")):
            cr_src.setdefault(sym, []).append(side)
        else:
            st.setdefault(sym, {})[ss] = 1
    cr, newest = sorted(cr_src), None
    return {
        "allowed_sym_sides": sorted(allowed),
        "date": now.strftime("%Y%m%d"), "built_utc": now.isoformat(),
        "sources": {"stocks": "symbols_trb_long/short.json + TRADIER_MANDATORY_LONG/SHORT_TRB (USER 2026-10-06 tradeable only)",
                    "crypto": "tradeable_keys.json sym_sides (USER 2026-10-06 tradeable only)", "exclude": "data/universe_exclude.json",
                    "open_positions": "data/open_position_sym_sides.json (always added, overrides exclude)"},
        "open_position_sym_sides": sorted(openpos),
        "stocks": sorted(st), "crypto": cr,
        "stock_events": {s: sum(v.values()) for s, v in st.items()}, "stock_sides_traded": {s: sorted(v) for s, v in st.items()},
        "crypto_lists": cr_src,
        "counts": {"stocks_symbols": len(st), "stocks_pairs": len(st), "stocks_sym_sides": sum(len(v) for v in st.values()),
                   "crypto_symbols": len(cr), "crypto_pairs": len(cr), "crypto_sym_sides": sum(len(v) for v in cr_src.values())},
    }


def load_or_build(root=ROOT, now=None, write=True):
    """latest file <= today if it is from today, else (re)build locally. Returns (universe, how)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = OUT / f"{now.strftime('%Y%m%d')}.json"
    if today.exists():
        try:
            return json.loads(today.read_text()), "file"
        except Exception:
            pass
    # the stock ledger lives on the Mac (servers' copies are stale): prefer the newest pushed file of the last 3 days over a local rebuild
    recent = sorted(OUT.glob("20??????.json"))
    for f in reversed(recent):
        try:
            age = (now.date() - dt.datetime.strptime(f.stem, "%Y%m%d").date()).days
            if 0 <= age <= 3:
                return json.loads(f.read_text()), f"file-{age}d-old"
        except Exception:
            continue
    u = build(root, now)
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        today.write_text(json.dumps(u, indent=1))
    return u, "built-local"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    u = build()
    print(json.dumps(u["counts"]), u["sources"]["stocks"])
    f = OUT / f"{u['date']}.json"
    if a.write or a.push:
        OUT.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(u, indent=1))
        print("[written]", f)
    if a.push:
        hosts = json.load(open(ROOT / "tools" / "fleet_hosts.json"))["hosts"]
        for h in hosts:
            tgt = h["ssh"][-1] if h["name"] == "s1" else h["ssh"][0]
            r = subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=10 -o BatchMode=yes", "--rsync-path=mkdir -p ~/binance-sandbox/data/daily_universe && rsync", str(f), f"{tgt}:{h['root']}/data/daily_universe/"], capture_output=True, text=True)
            print("[push]", h["name"], tgt, r.returncode, r.stderr.strip()[:120])


if __name__ == "__main__":
    main()
