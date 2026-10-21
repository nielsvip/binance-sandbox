#!/usr/bin/env python3
"""data_never_miss — one watcher for prices, market data and klines on Mac + S1 + gateway, crypto + stocks.

USER 2026-10-21: "apply and monitor use macbook s1 and gateway as sources the system can never miss a beat not for prices
not for klines not for market data on crypto nor on stock". Replaces nothing; tools/klines_freshness_watch.py (6 probes,
Mac only, 15m only, detect only) keeps running.

Every run:
  1. PROBE all three hosts in parallel (tools/data_never_miss_probe.py, tail-read, read-only).
  2. KLINES — per (venue, symbol, tf) in the live universes (symbols.json / symbols_tradier.json; 1m excluded: live 1m is
     built from mark prices and the WS file only upgrades it when a full read arrives):
       best = newest last bar held by ANY store on ANY host (klines_cache, klines_cache/gateway, S1 sandbox, gateway).
       Every live target store (Mac, S1 ~/binance, gateway ~/binance) behind `best` gets the missing bars APPENDED from the
       stores that have them (tools/data_never_miss_merge.py: append-only, never rewrites/drops a row). No exchange API.
  3. VERDICT after healing — crypto vs the clock (closed bars may lag ≤ TOL_BARS + grace), stocks vs the stock universe's
     own newest bar for that tf (calendar-free: holidays/half days need no table). Bars NO host has = UNHEALABLE.
  4. MARKET DATA / PRICES — file age per host (price_cache_*, latest_market_data, tradier_indicators_latest).
  5. REPORT data/reports/data_never_miss.json, BREACH lines to stdout (cron log), data/alerts/data_never_miss.jsonl,
     macOS notification on Mac breaches (throttled 15 min per key).

  python3 tools/data_never_miss.py            # probe + heal + report
  python3 tools/data_never_miss.py --no-heal  # report only
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tools" / "data_never_miss_probe.py"
MERGE = ROOT / "tools" / "data_never_miss_merge.py"
TAIL = ROOT / "tools" / "data_never_miss_tail.py"
REPORT = ROOT / "data" / "reports" / "data_never_miss.json"
ALERTS = ROOT / "data" / "alerts" / "data_never_miss.jsonl"
NOTIFY_STATE = ROOT / "data" / "reports" / ".data_never_miss_notify.json"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10", "-o", "ControlPath=none"]
HOSTS = {"mac": {"ssh": None, "bases": [str(ROOT)]},
         "s1": {"ssh": os.environ.get("NEVERMISS_S1", "s1-int"), "bases": ["binance", "binance-sandbox"]},
         "gw": {"ssh": os.environ.get("NEVERMISS_GW", "gateway-internal"), "bases": ["binance"]}}
# live stores that consumers read — these get healed; S1 sandbox, klines_cache/gateway/ subdirs and the gateway host are
# sources only (USER 2026-10-21: "gateway does not want macbook or s1 klines")
TARGETS = {("mac", str(ROOT)), ("s1", "binance")}
TF_MS = {"3m": 180000, "15m": 900000, "1h": 3600000, "4h": 14400000, "D": 86400000}
TOL_BARS = {"3m": 2, "15m": 1, "1h": 1, "4h": 1, "D": 1}
GRACE_MS = 5 * 60000
PRICE_FILES = ("price_cache_1.json", "price_cache_2.json", "price_cache_3.json")
# (host, label, files, max_age_s) — a group is OK when its FRESHEST file is within max_age (that is how the consumers read
# them: ez_manage blocks only when ALL in-mem price caches are >60s, config.py:6117). Mac limits = the failover trigger
# (live_failover_common SNAPSHOT_STALE_S 90). S1 is the standby: its crypto stack runs 24/7 (checked); its stock stack is
# started by live_failover_s1 only when the Mac goes stale in session (stock files there are idle by design, not checked).
MARKET_RULES = [("mac", "crypto prices", PRICE_FILES, 60), ("mac", "crypto snapshot (market_data_*)", ("data/market_data_NEWEST",), 90),
                ("mac", "crypto latest_market_data", ("data/latest_market_data.json",), 90),
                ("mac", "stock indicators", ("data/tradier/tradier_indicators_latest.json",), 90),
                # USER 2026-10-21: S1 starts its own stack ONLY when the Mac's push stops (cron EZ_S1_STACK_REAP stops it
                # while the Mac is NORMAL) — so S1 is fine when EITHER source is fresh: never judge the idle stack alone
                ("s1", "crypto snapshot on S1 (Mac push or own stack)", ("data/market_data_NEWEST", "ez_s1_stack/data/latest_market_data.json"), 180),
                ("s1", "crypto prices on S1 (Mac push or own stack)", PRICE_FILES + tuple("ez_s1_stack/" + f for f in PRICE_FILES), 300),
                ("gw", "crypto market_data", ("data/latest_market_data.json",), 300)]
DISK_MIN_FREE = 3 * 1024 ** 3  # gateway hit 100 % on 2026-10-21 (221 MB) — ENOSPC is what truncated klines on 10-16
REL = re.compile(r"^klines_cache/(?:(gateway|tradier)/)?([A-Za-z0-9]+)_(3m|15m|1h|4h|D)\.json$")


def canon(ts):
    """one key for every stamp style seen in the stores: "2026-10-20", "2026-10-20T19:30", "2026-10-20 19:30:00",
    "2026-10-20T19:30:00.000000Z" -> "2026-10-20T19:30:00" (2026-10-21: three styles coexist in stock files)."""
    s = str(ts).replace(" ", "T")
    if len(s) >= 19 and s[16] == ":":
        return s[:19]
    if len(s) >= 16 and s[13] == ":":
        return s[:16] + ":00"
    return s[:10] + "T00:00:00"


def log(msg):
    print(f"[NEVERMISS {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def probe(host):
    h = HOSTS[host]
    err = ""
    for attempt in range(3):  # 2026-10-21: the S1 link drops single sessions under load (empty stdout) — retry
        try:
            if h["ssh"] is None:
                return host, probe_local(h["bases"]), ""
            else:
                r = subprocess.run(SSH + [h["ssh"], "python3 - " + " ".join("~/" + b for b in h["bases"])], input=PROBE.read_text(), capture_output=True, text=True, timeout=120)
            d = json.loads(r.stdout)
            # remote bases come back expanded (/home/niels/binance) — key them by our short names
            d["bases"] = dict(zip(h["bases"], d["bases"].values()))
            return host, d, ""
        except Exception as e:
            err = f"{type(e).__name__}: {e} {(r.stderr if 'r' in dir() else '')[-120:]}"[:240]
            time.sleep(3)
    return host, None, err


PROBE_CACHE = ROOT / "data" / "reports" / ".data_never_miss_probe_cache.json"


def probe_local(bases):
    """in-process (no interpreter start) with an mtime+size cache of last bars across runs."""
    sys.path.insert(0, str(ROOT / "tools"))
    import data_never_miss_probe as P
    try:
        cache = json.loads(PROBE_CACHE.read_text())
    except (OSError, ValueError):
        cache = {}
    d = {"now": time.time(), "host": "mac", "bases": {b: P.probe(b, cache) for b in bases}}
    try:
        tmp = PROBE_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, PROBE_CACHE)
    except OSError:
        pass
    return d


def universes():
    try:
        crypto = set(json.loads((ROOT / "symbols.json").read_text()))
    except (OSError, ValueError):
        crypto = set()
    try:
        stocks = set(json.loads((ROOT / "symbols_tradier.json").read_text()))
    except (OSError, ValueError):
        stocks = set()
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from excluded_tokenized_stocks import is_denied
        crypto = {s for s in crypto if not is_denied(s)}
    except Exception:
        pass
    return crypto, stocks


UNPARSED = []
MIXED = []


def index(probes, crypto, stocks):
    """{(venue, symbol, tf): {(host, base, sub): last_ms}}; D bars floored to their DATE (stock D files mix
    "2026-10-20" and "2026-10-20T20:00:00" — one session, one date). Live files with no readable last bar go to UNPARSED."""
    idx = {}
    UNPARSED.clear()
    MIXED.clear()
    for host, d in probes.items():
        for base, files in d["bases"].items():
            for rel, ent in files.items():
                _mt, last, _sz = ent[:3]
                m = REL.match(rel)
                if not m:
                    continue
                sub, symbol, tf = m.group(1) or "", m.group(2), m.group(3)
                venue = "stocks" if sub == "tradier" else "crypto"
                if (venue == "crypto" and symbol not in crypto) or (venue == "stocks" and symbol not in stocks):
                    continue
                if len(ent) > 3 and ent[3] == "mixed":
                    if (host, base) in TARGETS:
                        MIXED.append(f"{host}:{rel}")
                    continue
                if not last:
                    if (host, base) in TARGETS:
                        UNPARSED.append(f"{host}:{base}/{rel}")
                    continue
                if tf == "D":
                    last = last // TF_MS["D"] * TF_MS["D"]
                idx.setdefault((venue, symbol, tf), {})[(host, base, sub)] = last
    return idx


def rel_of(symbol, tf, sub):
    return f"klines_cache/{sub + '/' if sub else ''}{symbol}_{tf}.json"


def rsync_bin():
    for p in ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"):
        if os.path.exists(p):
            return p
    return "rsync"


def rows_after(path, after_ms):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    after = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(after_ms / 1000))
    n = 10 if str(path).endswith("_D.json") else 19
    return [r for r in data if isinstance(r, dict) and canon(r.get("timestamp", ""))[:n] > after[:n]]


def apply_patch(host, base, patches):
    if not patches:
        return {}
    payload = {"base": base if HOSTS[host]["ssh"] is None else "~/" + base, "patches": patches}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        local = fh.name
    try:
        if HOSTS[host]["ssh"] is None:
            r = subprocess.run([sys.executable, str(MERGE), local], capture_output=True, text=True, timeout=300)
        else:
            remote = f"/tmp/nevermiss_patch_{os.getpid()}.json"
            # 2026-10-21 measured: rsync of the patch over the shared ControlMaster died with "Broken pipe (255)" under
            # load; upload through plain ssh stdin, retried
            for attempt in range(3):
                up = subprocess.run(SSH + [HOSTS[host]["ssh"], f"cat > {remote}"], stdin=open(local, "rb"), capture_output=True, timeout=120)
                if up.returncode == 0:
                    break
                time.sleep(3)
            else:
                return {"_error": f"patch upload failed rc={up.returncode} {up.stderr.decode(errors='replace')[-120:]}"}
            r = subprocess.run(SSH + [HOSTS[host]["ssh"], f"python3 - {remote}; rm -f {remote}"], input=MERGE.read_text(), capture_output=True, text=True, timeout=300)
        return json.loads(r.stdout or "{}")
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"[:200]}
    finally:
        os.unlink(local)


def heal(idx, probes):
    """append missing tail bars into every live target that is behind the best copy anywhere."""
    plan = {}
    for (venue, symbol, tf), stores in idx.items():
        best = max(stores.values())
        sub_t = "tradier" if venue == "stocks" else ""
        for host, base in TARGETS:
            if host not in probes:
                continue
            own = stores.get((host, base, sub_t))
            if own is None or own >= best:
                continue  # no live file there (never create one) or already current
            srcs = [k for k, v in stores.items() if v > own and k != (host, base, sub_t)]
            plan.setdefault((host, base), []).append((rel_of(symbol, tf, sub_t), own, srcs))
    if not plan:
        return {}
    # per source store: the rows after the OLDEST target bar that needs it (one request per source base)
    want = {}
    for (_host, _base), items in plan.items():
        for rel, own, srcs in items:
            symbol_tf = rel.rsplit("/", 1)[-1]
            for (sh, sb, ssub) in srcs:
                src_rel = f"klines_cache/{ssub + '/' if ssub else ''}{symbol_tf}"
                cur = want.setdefault((sh, sb), {}).get(src_rel)
                want[(sh, sb)][src_rel] = own if cur is None else min(cur, own)
    with ThreadPoolExecutor(4) as ex:
        got = dict(zip(want, ex.map(lambda k: fetch_tails(k[0], k[1], want[k]), want)))
    results = {}
    for (host, base), items in plan.items():
        patches = {}
        for rel, own, srcs in items:
            symbol_tf = rel.rsplit("/", 1)[-1]
            by_ts = {}
            n = 10 if symbol_tf.endswith("_D.json") else 19  # D: one row per date
            cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(own / 1000))[:n]
            for (sh, sb, ssub) in srcs:
                for r in got.get((sh, sb), {}).get(f"klines_cache/{ssub + '/' if ssub else ''}{symbol_tf}", []):
                    t = canon(r.get("timestamp", ""))[:n]
                    if t > cut:
                        by_ts.setdefault(t, r)
            if by_ts:
                patches[rel] = [by_ts[t] for t in sorted(by_ts)]
        res = apply_patch(host, base, patches)
        results[f"{host}:{base}"] = {"files": len(patches), "rows": sum(v for v in res.values() if isinstance(v, int)),
                                     "skipped": {k: v for k, v in res.items() if not isinstance(v, int)}}
    return results


def fetch_tails(host, base, req):
    """{rel: after_ms} -> {rel: rows newer than after_ms}; remote hosts extract server-side so only new bars travel."""
    if HOSTS[host]["ssh"] is None:
        return {rel: rows for rel, after in req.items() if (rows := rows_after(str(Path(base) / rel), after))}
    try:
        r = subprocess.run(SSH + [HOSTS[host]["ssh"], f"python3 - ~/{base} {shlex.quote(json.dumps(req))}"], input=TAIL.read_text(), capture_output=True, text=True, timeout=150)
        return json.loads(r.stdout or "{}")
    except Exception as e:
        log(f"tail fetch {host}:{base} failed: {type(e).__name__}: {e}"[:200])
        return {}


def verdict(idx, probes, now_ms):
    breaches, unhealable = [], []
    stock_ref = {}
    for (venue, symbol, tf), stores in idx.items():
        if venue == "stocks":
            stock_ref.setdefault(tf, []).append(max(stores.values()))
    stock_ref = {tf: sorted(v)[int(len(v) * 0.9)] for tf, v in stock_ref.items() if v}
    for (venue, symbol, tf), stores in idx.items():
        step = TF_MS[tf]
        if venue == "crypto":
            # bar opened at T closes at T+step; writers get GRACE_MS after the close before a missing bar counts
            expect = ((now_ms - GRACE_MS) // step) * step - TOL_BARS[tf] * step
        else:
            # calendar-free: the universe's own newest bar for this tf; 1 bar of slack absorbs the two 1h/4h grids
            # (13:30 vs 14:00 anchors), half a day for D so a missing session still shows
            expect = stock_ref.get(tf, 0) - (step // 2 if tf == "D" else step)
        best = max(stores.values())
        if best < expect:
            unhealable.append({"venue": venue, "symbol": symbol, "tf": tf, "best": iso(best), "expect": iso(expect)})
        sub_t = "tradier" if venue == "stocks" else ""
        for host, base in TARGETS:
            own = stores.get((host, base, sub_t))
            if own is not None and own < expect:
                breaches.append({"host": host, "venue": venue, "symbol": symbol, "tf": tf, "last": iso(own), "expect": iso(expect), "bars_behind": round((expect - own) / step, 1)})
    return breaches, unhealable


def market(probes):
    out = []
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from live_failover_common import stocks_applicable
        in_session = stocks_applicable()
    except Exception:
        in_session = False
    for host, label, files, max_s in MARKET_RULES:
        d = probes.get(host)
        if not d:
            continue
        if label.startswith("stock") and not in_session:
            continue  # BIBLE §123 (USER 2026-10-21): stock indicators/prices are idle outside the NYSE session by design
        base_files = next(iter(d["bases"].values()))
        ages = {rel: round(d["now"] - base_files[rel][0], 1) for rel in files if rel in base_files}
        age = min(ages.values()) if ages else None
        out.append({"host": host, "file": label, "age_s": age, "max_s": max_s, "ok": age is not None and age <= max_s, "files": ages})
    for host, d in probes.items():
        for base, files in d["bases"].items():
            free = (files.get("_disk_free") or [0, None, None])[2]
            if free is not None:
                out.append({"host": host, "file": f"disk free {base}", "age_s": None, "free_gb": round(free / 1024 ** 3, 1), "ok": free >= DISK_MIN_FREE})
    return out


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M", time.gmtime(ms / 1000)) if ms else None


def notify(key, msg):
    if sys.platform != "darwin":
        return
    try:
        st = json.loads(NOTIFY_STATE.read_text()) if NOTIFY_STATE.exists() else {}
    except (OSError, ValueError):
        st = {}
    if time.time() - st.get(key, 0) < 900:
        return
    st[key] = time.time()
    try:
        NOTIFY_STATE.write_text(json.dumps(st))
        safe = msg.replace('"', "'").replace("\\", "")[:200]
        subprocess.Popen(["osascript", "-e", f'display notification "{safe}" with title "DATA NEVER MISS" sound name "Basso"'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-heal", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    crypto, stocks = universes()
    with ThreadPoolExecutor(3) as ex:
        got = list(ex.map(probe, HOSTS))
    probes = {h: d for h, d, _e in got if d}
    unreachable = {h: e for h, d, e in got if not d}
    log(f"probed {sorted(probes)} in {time.time() - t0:.0f}s")
    idx = index(probes, crypto, stocks)
    healed = {} if a.no_heal else heal(idx, probes)
    log(f"heal done at {time.time() - t0:.0f}s: " + ", ".join(f"{k} +{v['rows']}" for k, v in healed.items()))
    if healed:
        # re-probe the targets that were written so the verdict is measured, not assumed
        with ThreadPoolExecutor(3) as ex:
            for h, d, _e in ex.map(probe, [k.split(":")[0] for k in healed]):
                if d:
                    probes[h] = d
        idx = index(probes, crypto, stocks)
    now_ms = int(time.time() * 1000)
    breaches, unhealable = verdict(idx, probes, now_ms)
    mk = market(probes)
    mk_bad = [m for m in mk if not m["ok"]]
    rep = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "secs": round(time.time() - t0, 1),
           "universe": {"crypto": len(crypto), "stocks": len(stocks)}, "unreachable": unreachable, "healed": healed,
           "kline_breaches": breaches, "unhealable": unhealable, "unparsed_live_files": list(UNPARSED), "mixed_label_files": list(MIXED), "market": mk}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, indent=1))
    os.replace(tmp, REPORT)
    for k, v in healed.items():
        log(f"HEALED {k}: {v['rows']} bars appended into {v['files']} files" + (f" skipped={list(v['skipped'].items())[:3]}" if v["skipped"] else ""))
    by = {}
    for b in breaches:
        by.setdefault((b["host"], b["venue"]), []).append(b)
    for (host, venue), bs in sorted(by.items()):
        worst = max(bs, key=lambda b: b["bars_behind"])
        log(f"BREACH klines {host} {venue}: {len(bs)} files behind — worst {worst['symbol']} {worst['tf']} last {worst['last']} expect {worst['expect']}")
    if unhealable:
        log(f"UNHEALABLE {len(unhealable)} files: no host has the bars — e.g. {[(u['symbol'], u['tf'], u['best']) for u in unhealable[:6]]}")
    for m in mk_bad:
        log(f"BREACH market {m['host']} {m['file']} " + (f"{m['free_gb']} GB free < 3 GB" if "free_gb" in m else f"age {m['age_s']}s > {m['max_s']}s"))
    for h, e in unreachable.items():
        log(f"BREACH host {h} unreachable: {e}")
    if MIXED:
        log(f"MIXED-LABEL {len(MIXED)} stock files hold New-York and UTC stamped rows — not auto-healed: {MIXED[:6]}")
    if UNPARSED:
        log(f"BREACH {len(UNPARSED)} live kline files have no readable last bar — e.g. {UNPARSED[:4]}")
    ok = not (breaches or mk_bad or unreachable or UNPARSED)
    log(f"{'OK' if ok else 'BREACH'} probed={sorted(probes)} keys={len(idx)} secs={rep['secs']}")
    if not ok:
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS, "a") as fh:
            fh.write(json.dumps({"ts": rep["generated_utc"], "kline_breaches": len(breaches), "unhealable": len(unhealable), "market_bad": mk_bad, "unreachable": unreachable}) + "\n")
        mac_bad = [b for b in breaches if b["host"] == "mac"] + [m for m in mk_bad if m["host"] == "mac"]
        if mac_bad or "s1" in unreachable or "gw" in unreachable:
            notify("mac", f"{len(mac_bad)} Mac data breaches, unreachable={sorted(unreachable)} — see data/reports/data_never_miss.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
