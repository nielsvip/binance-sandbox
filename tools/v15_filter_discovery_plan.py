#!/usr/bin/env python3
"""v15_filter_discovery_plan — ONE deduped source manifest for the filter discovery (Agent Y, 2026-10-01).

--emit OUT      (runs on each host, stdlib only) best existing result per sym_side on THIS host:
                  json = newest *_v14_progress.json (~/v15_*/progress, lifecycle_pilot) with a complete final set (>=50 cumulative_overrides + final_gain)
                  xlsx = newest valid *_matrix*.xlsx under ~/binance-sandbox/SPREADSHEETS/** (archive_* and TEMPLATE* excluded) for sym_sides WITHOUT a json
--plan          (runs on the Mac) collects the three host manifests, dedupes by sym_side (newest json wins; xlsx only if no json anywhere), assigns every
                  sym_side to ONE host (balanced per venue; owner host preferred when its load is not above the average), ships the source file to the assigned
                  host (~/binance-sandbox/data/yellow_discovery/sources/<ss>/...) and writes data/yellow_discovery/manifest_<host>.json (copied to the host)
"""
import argparse, glob, json, os, re, subprocess, sys, zipfile, collections, tempfile, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {"s1": "s1-pub", "s2": "s2", "s5": "s5"}
NO_RELAY = "--no-relay" in sys.argv  # every sym_side is processed on the host that already holds its source (no file shipping)
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def cat_of(ss):
    s = ss.upper()
    side = "LONG" if s.endswith("_LONG") else "SHORT"
    base = s[: -(len(side) + 1)]
    return ("CRYPTO" if base.endswith(CRYPTO_SUFFIX) else "STOCKS") + "_" + side


def emit(out):
    home = os.path.expanduser("~")
    best = {}
    for g in ["~/v15_*/progress/*_v14_progress.json", "~/binance-sandbox/data/reports/lifecycle_pilot/*_v14_progress.json"]:
        for p in glob.glob(os.path.expanduser(g)):
            ss = os.path.basename(p)[: -len("_v14_progress.json")]
            m = os.path.getmtime(p)
            if ss not in best or m > best[ss]["mtime"]:
                best[ss] = {"symside": ss, "kind": "json", "path": p, "mtime": m}
    keep = {}
    for ss, e in best.items():
        try:
            d = json.load(open(e["path"]))
        except Exception:
            continue
        if len(d.get("cumulative_overrides") or {}) >= 50 and d.get("final_gain", d.get("cumulative_gain")) is not None:
            e["fixed"] = any(isinstance(x, dict) and "naked_binding" in x for x in (d.get("done") or {}).values())
            e["has365"] = bool(glob.glob(os.path.expanduser(f"~/v15_*/chain/**/{ss}_365_cycle.json"), recursive=True))
            keep[ss] = e
    xl = {}
    for p in glob.glob(os.path.expanduser("~/binance-sandbox/SPREADSHEETS/**/*.xlsx"), recursive=True):
        b = os.path.basename(p)
        if "archive_2025" in p or "TEMPLATE" in b or b.endswith(".tmp.xlsx") or ".tmp" in b:
            continue
        m = re.match(r"([A-Z0-9]+_(?:LONG|SHORT))_", b)
        if not m:
            continue
        ss = m.group(1)
        if ss in keep:
            continue
        mt = os.path.getmtime(p)
        if ss not in xl or mt > xl[ss]["mtime"]:
            if os.path.getsize(p) < 300000:
                continue
            try:
                with zipfile.ZipFile(p) as z:
                    if len(z.namelist()) < 10:  # central directory only (fast); full CRC test happens when the sheet is opened
                        continue
            except Exception:
                continue
            xl[ss] = {"symside": ss, "kind": "xlsx", "path": p, "mtime": mt}
    rows = list(keep.values()) + list(xl.values())
    for r in rows:
        r["cat"] = cat_of(r["symside"])
        r["size"] = os.path.getsize(r["path"])
    json.dump(rows, open(out, "w"))
    print(f"[emit] {os.uname()[1]}: json {len(keep)} xlsx-only {len(xl)} -> {out}")


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def plan():
    inv = {}
    for name, tgt in HOSTS.items():
        r = sh(["scp", "-q", "-o", "StrictHostKeyChecking=accept-new", f"{ROOT}/tools/v15_filter_discovery_plan.py", f"{tgt}:/tmp/v15_fdp.py"])
        r = sh(["ssh", tgt, "python3 /tmp/v15_fdp.py --emit /tmp/v15_fdp_inv.json"])
        print(name, r.stdout.strip(), r.stderr.strip()[:100])
        r = sh(["scp", "-q", f"{tgt}:/tmp/v15_fdp_inv.json", f"/tmp/fdp_inv_{name}.json"])
        inv[name] = json.load(open(f"/tmp/fdp_inv_{name}.json"))
    best = {}
    for name, rows in inv.items():
        for r in rows:
            r["host"] = name
            cur = best.get(r["symside"])
            rank = (1 if r["kind"] == "json" else 0, r["mtime"])
            if cur is None or rank > (1 if cur["kind"] == "json" else 0, cur["mtime"]):
                best[r["symside"]] = r
    print("deduped sym_sides:", collections.Counter(r["cat"] for r in best.values()), "kinds:", collections.Counter(r["kind"] for r in best.values()))
    # assignment: weights ~ cost: crypto 4.0, stocks 1.0 per sym_side; owner preferred while its load <= 1.1 x average, else least-loaded host (bulk tar relay)
    total_w = sum(4.0 if r["cat"].startswith("CRYPTO") else 1.0 for r in best.values())
    cap = 1.1 * total_w / 3
    load = {h: 0.0 for h in HOSTS}
    order = sorted(best.values(), key=lambda r: (r["cat"].startswith("STOCKS"), r["symside"]))
    assign = {h: [] for h in HOSTS}
    for r in order:
        w = 4.0 if r["cat"].startswith("CRYPTO") else 1.0
        host = r["host"] if (NO_RELAY or load[r["host"]] + w <= cap) else min(load, key=load.get)
        load[host] += w
        r["assigned"] = host
        assign[host].append(r)
    for h, rows in assign.items():
        print(h, len(rows), dict(collections.Counter(r["cat"] for r in rows)), "load", load[h])
    out = ROOT / "data" / "yellow_discovery"
    out.mkdir(parents=True, exist_ok=True)
    DEST = "/home/niels/binance-sandbox/data/yellow_discovery/sources"
    moves = collections.defaultdict(list)
    for r in best.values():
        if r["assigned"] != r["host"]:
            moves[(r["host"], r["assigned"])].append(r["path"])
    for (src, dst), paths in moves.items():  # one tar stream per (owner -> assigned) pair, piped through the Mac
        lst = f"/tmp/fdp_move_{src}_{dst}.txt"
        open(lst, "w").write("\n".join(paths) + "\n")
        sh(["scp", "-q", lst, f"{HOSTS[src]}:{lst}"])
        cmd = f"ssh {HOSTS[src]} 'tar -cf - -T {lst} 2>/dev/null' | ssh {HOSTS[dst]} 'mkdir -p {DEST} && tar -xf - -C {DEST}'"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        print(f"[relay] {src}->{dst}: {len(paths)} files rc={r.returncode} {r.stderr.strip()[:100]}")
    for h, rows in assign.items():
        man = []
        for r in rows:
            path = r["path"] if r["assigned"] == r["host"] else DEST + r["path"]
            man.append({"symside": r["symside"], "kind": r["kind"], "path": path, "cat": r["cat"], "mtime": r["mtime"], "has365": r.get("has365", False), "fixed": r.get("fixed", False), "owner": r["host"]})
        mf = out / f"manifest_{h}.json"
        mf.write_text(json.dumps(man))
        sh(["scp", "-q", str(mf), f"{HOSTS[h]}:/home/niels/binance-sandbox/data/yellow_discovery/manifest.json"])
        print(f"[plan] {h}: {len(man)} sym_sides manifest shipped")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--no-relay", action="store_true")
    a = ap.parse_args()
    if a.emit:
        emit(a.emit)
    elif a.plan:
        plan()
