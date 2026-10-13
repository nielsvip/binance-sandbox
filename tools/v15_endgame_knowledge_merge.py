#!/usr/bin/env python3
"""USER 2026-10-13 fleet-wide ENDGAME knowledge (BIBLE §69).

Every pilot appends `endgame_ledger.jsonl` in its own campaign dir (`~/v15_runNN_*/progress/`) on its own host, so the
per-campaign `endgame_knowledge.json` reset at every campaign and was never merged across hosts (EV ordering never matured).
This tool rebuilds ONE knowledge file from every ledger record of every host x campaign:

    data/endgame/endgame_knowledge_fleet.json
      workbooks   {n, sum_lift, pos_lift, target_met, adopted, rejected, sum_cand_lift}      (fleet aggregate)
      keys        {key: {n, sum, pos, dropped}}                                           (same shape the pilot ranks with)
      by_cat_side {CRYPTO_LONG|...: {workbooks, keys}}                                    (per cat_side EV)
      sources     [{host, file, records}] , n_records, built

Run on S1 (scheduler host): `--pull` rsyncs every worker's ledgers to data/endgame/ledgers/<host>/, `--push` copies the
fleet file to every worker's <root>/data/endgame/ and md5-verifies. Records are deduped by (key, ts) (the same file read
twice). Pure stats from real ledger rows — no estimates. `load_ev(cat_side)` -> {key: (n, mean_delta, pos_rate)} is the
consumer API (pinpoint round).
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLEET_PATH = ROOT / "data" / "endgame" / "endgame_knowledge_fleet.json"
LEDGER_DIR = ROOT / "data" / "endgame" / "ledgers"
HOSTS_FILE = ROOT / "tools" / "fleet_hosts_final.json"
CRYPTO_QUOTES = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def cat_side_of(symside):
    s = str(symside or "").upper()
    base = s[:-5] if s.endswith("_LONG") else s[:-6] if s.endswith("_SHORT") else s
    return f"{'CRYPTO' if base.endswith(CRYPTO_QUOTES) else 'STOCKS'}_{'LONG' if s.endswith('_LONG') else 'SHORT'}"


def _new_wb():
    return {"n": 0, "sum_lift": 0.0, "pos_lift": 0, "target_met": 0, "adopted": 0, "rejected": 0, "sum_cand_lift": 0.0, "n_cand": 0}


def _add_key(ks, key, delta, pos_always=False):
    if delta is None:
        return
    try:
        d = float(delta)
    except Exception:
        return
    st = ks.setdefault(key, {"n": 0, "sum": 0.0, "pos": 0})
    st["n"] += 1
    st["sum"] = float(st["sum"]) + d
    st["pos"] += 1 if (pos_always or d > 1e-9) else 0


def _merge_record(bucket, rec):
    # Mirrors v15_pilot._endgame_merge_knowledge (naked switch + switch=value, reapply steps, drops, workbook lift).
    ks = bucket.setdefault("keys", {})
    for nk in rec.get("naked") or []:
        sw, d = nk.get("switch"), nk.get("delta")
        if not sw or d is None:
            continue
        _add_key(ks, sw, d)
        _add_key(ks, f"{sw}={str(nk.get('value'))[:60]}", d)
    for st in rec.get("reapply_seq") or []:
        if st.get("filter"):
            _add_key(ks, st["filter"], st.get("step_delta"), pos_always=True)
    for dk in rec.get("dropped") or []:
        e = ks.setdefault(dk, {"n": 0, "sum": 0.0, "pos": 0})
        e["dropped"] = int(e.get("dropped", 0)) + 1
    wb = bucket.setdefault("workbooks", _new_wb())
    try:
        lift = float(rec.get("lift") or 0.0)
    except Exception:
        lift = 0.0
    wb["n"] += 1
    wb["sum_lift"] += lift
    wb["pos_lift"] += 1 if lift > 1e-9 else 0
    wb["target_met"] += 1 if rec.get("target_met") else 0
    wb["adopted"] += 1 if rec.get("adopted") else 0
    wb["rejected"] += 1 if rec.get("rejected") else 0
    if rec.get("cand_lift") is not None:
        wb["sum_cand_lift"] += float(rec["cand_lift"])
        wb["n_cand"] += 1


def ledger_files(extra_globs=None):
    pats = [str(LEDGER_DIR / "**" / "*.jsonl"), os.path.expanduser("~/v15_run*/progress/endgame_ledger.jsonl")] + list(extra_globs or [])
    seen, out = set(), []
    for p in pats:
        for f in sorted(glob.glob(p, recursive=True)):
            rf = os.path.realpath(f)
            if rf not in seen:
                seen.add(rf)
                out.append(f)
    return out


def build(files):
    know = {"version": 1, "built": datetime.datetime.now(datetime.timezone.utc).isoformat(), "workbooks": _new_wb(), "keys": {}, "by_cat_side": {}, "sources": []}
    dedupe = set()
    bad = 0
    for f in files:
        n_f = 0
        try:
            fh = open(f, errors="replace")
        except Exception:
            continue
        with fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except Exception:
                    bad += 1
                    continue
                if not isinstance(rec, dict) or not rec.get("symside"):
                    bad += 1
                    continue
                dk = (rec.get("key"), rec.get("ts"))
                if dk in dedupe:
                    continue
                dedupe.add(dk)
                _merge_record(know, rec)
                _merge_record(know["by_cat_side"].setdefault(rec.get("cat_side") or cat_side_of(rec["symside"]), {}), rec)
                n_f += 1
        know["sources"].append({"file": f, "records": n_f})
    know["n_records"] = len(dedupe)
    know["bad_lines"] = bad
    return know


def _atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)


def load_ev(cat_side=None, path=None, min_n=1):
    """{key: (n, mean_delta, pos_rate)} from the fleet file — cat_side bucket when given (None/'ALL' = fleet-global)."""
    p = Path(path or os.environ.get("V15_ENDGAME_KNOWLEDGE") or FLEET_PATH)
    try:
        know = json.loads(p.read_text())
    except Exception:
        return {}
    if cat_side in (None, "", "ALL"):
        keys = know.get("keys") or {}
    else:
        keys = ((know.get("by_cat_side") or {}).get(cat_side) or {}).get("keys") or {}
    out = {}
    for k, st in keys.items():
        try:
            n = int(st.get("n", 0))
            if n >= min_n:
                out[k] = (n, float(st.get("sum", 0.0)) / n, float(st.get("pos", 0)) / n)
        except Exception:
            continue
    return out


def _hosts():
    try:
        return [h for h in json.loads(HOSTS_FILE.read_text()).get("hosts", []) if h.get("ssh")]
    except Exception:
        return []


def _is_local(ip):
    return ip in ("127.0.0.1", "localhost")


def pull(timeout=300):
    rep = []
    for h in _hosts():
        ip = h["ssh"][0]
        if _is_local(ip):
            continue
        dst = LEDGER_DIR / h["name"]
        dst.mkdir(parents=True, exist_ok=True)
        cmd = ["rsync", "-az", "-m", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new",
               "--include=v15_run*/", "--include=v15_run*/progress/", "--include=v15_run*/progress/endgame_ledger.jsonl", "--exclude=*",
               f"{ip}:~/", str(dst) + "/"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            rep.append((h["name"], r.returncode, (r.stderr or "")[-200:]))
        except Exception as e:
            rep.append((h["name"], -1, str(e)[:200]))
    return rep


def push(path=FLEET_PATH, timeout=120):
    md5 = hashlib.md5(Path(path).read_bytes()).hexdigest()
    rep = []
    for h in _hosts():
        ip = h["ssh"][0]
        if _is_local(ip):
            continue
        root = h.get("root") or "~/binance-sandbox"
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ip]
        try:
            subprocess.run(ssh + [f"mkdir -p {root}/data/endgame"], capture_output=True, timeout=timeout)
            r = subprocess.run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15", str(path), f"{ip}:{root}/data/endgame/"], capture_output=True, text=True, timeout=timeout)
            m = subprocess.run(ssh + [f"md5sum {root}/data/endgame/{Path(path).name}"], capture_output=True, text=True, timeout=timeout)
            rmd5 = (m.stdout or "").split(" ")[0]
            rep.append((h["name"], r.returncode, "md5 OK" if rmd5 == md5 else f"md5 MISMATCH {rmd5}"))
        except Exception as e:
            rep.append((h["name"], -1, str(e)[:200]))
    return md5, rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pull", action="store_true", help="rsync every worker's campaign ledgers to data/endgame/ledgers/<host>/ first")
    ap.add_argument("--push", action="store_true", help="copy the fleet file to every worker's <root>/data/endgame/ + md5 verify")
    ap.add_argument("--out", default=str(FLEET_PATH))
    ap.add_argument("--ledger-glob", action="append", default=[], help="extra ledger globs (tests)")
    ap.add_argument("--no-default-globs", action="store_true", help="only --ledger-glob files (tests)")
    a = ap.parse_args(argv)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%FT%TZ")
    if a.pull:
        for name, rc, err in pull():
            print(f"[endgame-merge] {ts} pull {name} rc={rc} {err.strip()}", flush=True)
    files = ([f for g in a.ledger_glob for f in sorted(glob.glob(g, recursive=True))] if a.no_default_globs else ledger_files(a.ledger_glob))
    know = build(files)
    _atomic_json(a.out, know)
    wb = know["workbooks"]
    ml = wb["sum_lift"] / wb["n"] if wb["n"] else 0.0
    n3 = sum(1 for st in know["keys"].values() if int(st.get("n", 0)) >= 3)
    print(f"[endgame-merge] {ts} files={len(files)} workbooks={wb['n']} records={know['n_records']} bad={know['bad_lines']} keys={len(know['keys'])} keys_n>=3={n3} mean_lift={ml:+.4f} P(lift>0)={wb['pos_lift']}/{wb['n']} adopted={wb['adopted']} rejected={wb['rejected']} cat_sides={ {k: v['workbooks']['n'] for k, v in know['by_cat_side'].items()} } -> {a.out}", flush=True)
    if a.push:
        md5, rep = push(Path(a.out))
        for name, rc, msg in rep:
            print(f"[endgame-merge] {ts} push {name} rc={rc} {msg} ({md5[:8]})", flush=True)
        if any(rc != 0 or "MISMATCH" in msg for _n, rc, msg in rep):
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
