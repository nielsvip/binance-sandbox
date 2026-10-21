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
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tools" / "data_never_miss_probe.py"
MERGE = ROOT / "tools" / "data_never_miss_merge.py"
REPORT = ROOT / "data" / "reports" / "data_never_miss.json"
ALERTS = ROOT / "data" / "alerts" / "data_never_miss.jsonl"
NOTIFY_STATE = ROOT / "data" / "reports" / ".data_never_miss_notify.json"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10"]
HOSTS = {"mac": {"ssh": None, "bases": [str(ROOT)]},
         "s1": {"ssh": os.environ.get("NEVERMISS_S1", "s1-int"), "bases": ["binance", "binance-sandbox"]},
         "gw": {"ssh": os.environ.get("NEVERMISS_GW", "gateway-internal"), "bases": ["binance"]}}
# live stores that consumers read — these get healed; S1 sandbox + gateway/ subdirs are sources only
TARGETS = {("mac", str(ROOT)), ("s1", "binance"), ("gw", "binance")}
TF_MS = {"3m": 180000, "15m": 900000, "1h": 3600000, "4h": 14400000, "D": 86400000}
TOL_BARS = {"3m": 2, "15m": 1, "1h": 1, "4h": 1, "D": 1}
GRACE_MS = 5 * 60000
MARKET_MAX_AGE = {"price_cache_1.json": 60, "price_cache_2.json": 180, "price_cache_3.json": 60,
                  "data/latest_market_data.json": 180, "data/tradier/tradier_indicators_latest.json": 300}
# hosts whose market files are live (gateway price caches are retired; S1 is the failover standby)
MARKET_HOSTS = {"mac": list(MARKET_MAX_AGE), "s1": list(MARKET_MAX_AGE), "gw": ["data/latest_market_data.json"]}
REL = re.compile(r"^klines_cache/(?:(gateway|tradier)/)?([A-Za-z0-9]+)_(3m|15m|1h|4h|D)\.json$")


def log(msg):
    print(f"[NEVERMISS {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def probe(host):
    h = HOSTS[host]
    try:
        if h["ssh"] is None:
            r = subprocess.run([sys.executable, str(PROBE)] + h["bases"], capture_output=True, text=True, timeout=120)
        else:
            r = subprocess.run(SSH + [h["ssh"], "python3 - " + " ".join("~/" + b for b in h["bases"])], input=PROBE.read_text(), capture_output=True, text=True, timeout=120)
        d = json.loads(r.stdout)
        # remote bases come back expanded (/home/niels/binance) — key them by our short names
        d["bases"] = dict(zip(h["bases"], d["bases"].values()))
        return host, d, ""
    except Exception as e:
        return host, None, f"{type(e).__name__}: {e}"[:200]


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


def index(probes, crypto, stocks):
    """{(venue, symbol, tf): {(host, base, sub): last_ms}}"""
    idx = {}
    for host, d in probes.items():
        for base, files in d["bases"].items():
            for rel, (_mt, last, _sz) in files.items():
                m = REL.match(rel)
                if not m or not last:
                    continue
                sub, symbol, tf = m.group(1) or "", m.group(2), m.group(3)
                venue = "stocks" if sub == "tradier" else "crypto"
                if (venue == "crypto" and symbol not in crypto) or (venue == "stocks" and symbol not in stocks):
                    continue
                idx.setdefault((venue, symbol, tf), {})[(host, base, sub)] = last
    return idx


def rel_of(symbol, tf, sub):
    return f"klines_cache/{sub + '/' if sub else ''}{symbol}_{tf}.json"


def fetch_sources(needed):
    """needed = {(host, base): {rel}} -> {(host, base, rel): local path} (remote files pulled with ONE rsync per base)."""
    stage = Path(tempfile.mkdtemp(prefix="nevermiss_src_"))
    out = {}
    for (host, base), rels in needed.items():
        h = HOSTS[host]
        if h["ssh"] is None:
            for rel in rels:
                out[(host, base, rel)] = str(Path(base) / rel)
            continue
        dst = stage / host / base
        dst.mkdir(parents=True, exist_ok=True)
        lst = dst / ".files"
        lst.write_text("".join(r + "\n" for r in sorted(rels)))
        cmd = [rsync_bin(), "-a", "--timeout=60", "-e", " ".join(SSH), f"--files-from={lst}", f"{h['ssh']}:{base}/", str(dst) + "/"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as e:
            log(f"pull {host}:{base} failed: {e}")
        for rel in rels:
            p = dst / rel
            if p.exists():
                out[(host, base, rel)] = str(p)
    return stage, out


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
    return [r for r in data if isinstance(r, dict) and str(r.get("timestamp", "")).replace(" ", "T")[:19] > after]


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
            subprocess.run([rsync_bin(), "-a", "--timeout=60", "-e", " ".join(SSH), local, f"{HOSTS[host]['ssh']}:{remote}"], capture_output=True, timeout=120, check=True)
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
    needed = {}
    for (_host, _base), items in plan.items():
        for rel, _own, srcs in items:
            symbol_tf = rel.rsplit("/", 1)[-1]
            for (sh, sb, ssub) in srcs:
                needed.setdefault((sh, sb), set()).add(f"klines_cache/{ssub + '/' if ssub else ''}{symbol_tf}")
    stage, local = fetch_sources(needed)
    results = {}
    try:
        for (host, base), items in plan.items():
            patches = {}
            for rel, own, srcs in items:
                symbol_tf = rel.rsplit("/", 1)[-1]
                by_ts = {}
                for (sh, sb, ssub) in srcs:
                    p = local.get((sh, sb, f"klines_cache/{ssub + '/' if ssub else ''}{symbol_tf}"))
                    if p:
                        for r in rows_after(p, own):
                            by_ts.setdefault(str(r["timestamp"]).replace(" ", "T")[:19], r)
                if by_ts:
                    patches[rel] = [by_ts[t] for t in sorted(by_ts)]
            res = apply_patch(host, base, patches)
            results[f"{host}:{base}"] = {"files": len(patches), "rows": sum(v for v in res.values() if isinstance(v, int)),
                                         "skipped": {k: v for k, v in res.items() if not isinstance(v, int)}}
    finally:
        subprocess.run(["rm", "-rf", str(stage)])
    return results


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
            expect = (now_ms // step) * step - TOL_BARS[tf] * step - GRACE_MS
        else:
            expect = stock_ref.get(tf, 0)
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
    for host, files in MARKET_HOSTS.items():
        d = probes.get(host)
        if not d:
            continue
        base_files = next(iter(d["bases"].values()))
        for rel in files:
            ent = base_files.get(rel)
            age = None if ent is None else round(d["now"] - ent[0], 1)
            ok = age is not None and age <= MARKET_MAX_AGE[rel]
            out.append({"host": host, "file": rel, "age_s": age, "max_s": MARKET_MAX_AGE[rel], "ok": ok})
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
    idx = index(probes, crypto, stocks)
    healed = {} if a.no_heal else heal(idx, probes)
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
           "kline_breaches": breaches, "unhealable": unhealable, "market": mk}
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
        log(f"BREACH market {m['host']} {m['file']} age {m['age_s']}s > {m['max_s']}s")
    for h, e in unreachable.items():
        log(f"BREACH host {h} unreachable: {e}")
    ok = not (breaches or mk_bad or unreachable)
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
