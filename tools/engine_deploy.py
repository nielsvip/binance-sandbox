#!/usr/bin/env python3
"""engine_deploy (Agent M, permanent integrator): atomic deploy of a staged tree (mirrors repo paths: v12_quick_engine.py, vec_decisions/*.py,
tools/opt/evaluate_v12.py, data/switch_dependencies.json, data/vec_unwired.json, backtest_v12_engine.py ...) to Mac + s1/s2/s5 (~/binance-sandbox and ~/binance).
Order: every non-engine file FIRST, v12_quick_engine.py LAST; each file is copied to <name>.new.<ts> then mv'd (atomic rename) so a starting pilot never imports a half-written file.
Backups: backups/before_engine_<ts>/ (Mac) and ~/binance-sandbox/backups/before_engine_<ts>/ (hosts). Then md5 verify + import check; writes data/engine_deploy/<ts>.json + CURRENT.json.
  python tools/engine_deploy.py --stage DIR [--label TEXT] [--no-hosts]"""
import argparse, datetime, hashlib, json, os, shutil, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTS = ["s1-pub", "s2", "s5"]
DIRS = ["binance-sandbox", "binance"]
SSH = ["ssh", "-S", "none", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
def md5(p): return hashlib.md5(open(p, "rb").read()).hexdigest()
def sh(cmd, **k): return subprocess.run(cmd, capture_output=True, text=True, **k)
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--stage", required=True); ap.add_argument("--label", default=""); ap.add_argument("--no-hosts", action="store_true"); ap.add_argument("--changed", default="", help="comma list of FILTER=opt / SWITCH keys whose behaviour changed (union of the queue MANIFESTs changed_keys)"); ap.add_argument("--wired-now", default="", help="comma list of keys newly wired (removed from vec_unwired)")
    a = ap.parse_args(); stage = os.path.abspath(a.stage)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    files = []
    for dp, _, fn in os.walk(stage):
        for f in fn:
            if f.endswith((".pyc", ".bak")) or "__pycache__" in dp: continue
            files.append(os.path.relpath(os.path.join(dp, f), stage))
    files.sort(key=lambda r: (r == "v12_quick_engine.py", r))   # engine LAST
    for r in files:
        if r.endswith(".py"):
            import py_compile; py_compile.compile(os.path.join(stage, r), doraise=True)
    # JSN2 guard: data/cat_side_defaults_4.json is hot-read by live; never deploy it from a stale copy: union-merge keys from the current Mac copy (staged values win on conflicts) and refuse a staged file that has no cat_side sections.
    _cj = "data/cat_side_defaults_4.json"
    if _cj in files:
        _sp, _mp = os.path.join(stage, _cj), os.path.join(ROOT, _cj)
        _st = json.load(open(_sp)); _cur = json.load(open(_mp)) if os.path.exists(_mp) else {}
        if not all(c in _st for c in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")): sys.exit("REFUSED: staged cat_side_defaults_4.json lacks cat_side sections")
        _added = 0
        for _c in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
            for _k, _v in (_cur.get(_c) or {}).items():
                if _k not in _st[_c]: _st[_c][_k] = _v; _added += 1
        if _added: json.dump(_st, open(_sp, "w"), indent=1); print(f"[JSN2 guard] cat_side_defaults_4.json: merged {_added} keys from the current copy into the staged file")
    bk = os.path.join(ROOT, "backups", f"before_engine_{ts}"); 
    for r in files:
        src = os.path.join(ROOT, r)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(bk, r)), exist_ok=True); shutil.copy2(src, os.path.join(bk, r))
    # Mac atomic install
    for r in files:
        dst = os.path.join(ROOT, r); os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + f".new.{ts}"; shutil.copy2(os.path.join(stage, r), tmp); os.replace(tmp, dst)
    res = {"ts": ts, "label": a.label, "files": {r: md5(os.path.join(stage, r)) for r in files}, "hosts": {}}
    if not a.no_hosts:
        for h in HOSTS:
            for d in DIRS:
                base = f"~/{d}"
                sh(SSH + [h, f"mkdir -p ~/binance-sandbox/backups/before_engine_{ts}"])
                if d == "binance-sandbox":
                    cp = " ".join(f"--relative {r}" for r in files)
                    sh(SSH + [h, "cd ~/binance-sandbox && tar cf - " + " ".join(r for r in files) + f" 2>/dev/null | (cd backups/before_engine_{ts} && tar xf - 2>/dev/null); true"])
                # upload all to temp names first
                for r in files:
                    sh(["rsync", "-az", "-e", " ".join(SSH), "--relative", f"./{r}", f"{h}:{base}/.stage_{ts}/"], cwd=stage)
                mv = " && ".join(f"mkdir -p $(dirname {r}) && mv -f .stage_{ts}/{r} {r}" for r in files)
                o = sh(SSH + [h, f"cd {base} && ({mv}) && rm -rf .stage_{ts} && echo moved"])
                chk = sh(SSH + [h, f"cd {base} && md5sum " + " ".join(files)])
                got = {l.split()[1]: l.split()[0] for l in chk.stdout.splitlines() if len(l.split()) == 2}
                bad = [r for r in files if got.get(r) != res["files"][r]]
                imp = sh(SSH + [h, f"cd {base} && .venv/bin/python -c 'import v12_quick_engine, evaluate_v12; print(\"ok\")' 2>&1 | tail -1"]).stdout.strip()
                res["hosts"][f"{h}:{d}"] = {"moved": "moved" in o.stdout, "md5_bad": bad, "import": imp}
    os.makedirs(os.path.join(ROOT, "data/engine_deploy"), exist_ok=True)
    cur = {}
    cp_ = os.path.join(ROOT, "data/engine_deploy/CURRENT.json")
    if os.path.exists(cp_): cur = json.load(open(cp_)).get("md5", {})
    cur.update(res["files"])
    json.dump(res, open(os.path.join(ROOT, f"data/engine_deploy/{ts}.json"), "w"), indent=1)
    _split = lambda x: [k.strip() for k in x.split(",") if k.strip()]
    _eng = res["files"].get("v12_quick_engine.py") or cur.get("v12_quick_engine.py") or ""
    _cf = {"engine_md5": _eng, "at": datetime.datetime.utcnow().isoformat() + "Z", "deploy": ts, "label": a.label, "changed": _split(a.changed), "wired_now": _split(a.wired_now)}
    if not _cf["changed"] and not _cf["wired_now"] and any(r.startswith(("vec_decisions/", "v12_quick_engine", "tools/opt/evaluate_v12", "data/switch_dependencies", "data/vec_unwired")) for r in files):
        _cf["changed"] = ["__UNSPECIFIED__"]   # Agent Y: unknown scope -> recompute everything (queue MANIFEST must list changed_keys)
    json.dump(_cf, open(os.path.join(ROOT, "data/engine_deploy/changed_filters.json"), "w"), indent=1)
    json.dump({"updated_utc": datetime.datetime.utcnow().isoformat() + "Z", "last_deploy": ts, "engine_md5": _eng, "changed_filters_file": "data/engine_deploy/changed_filters.json", "md5": cur}, open(cp_, "w"), indent=1)
    bad_any = [k for k, v in res["hosts"].items() if v["md5_bad"] or not v["moved"]]
    line = f"{datetime.datetime.utcnow().strftime('%H:%MZ')} [M deploy {ts}] {a.label} files={len(files)} engine={res['files'].get('v12_quick_engine.py','-')[:8]} hosts_bad={bad_any or 'none'} imports={sorted({v['import'] for v in res['hosts'].values()})}"
    open(os.path.join(ROOT, "data/wiring/LOG.md"), "a").write("- " + line + "\n"); open(os.path.join(ROOT, "DAILY_AVG_DELTA_CHECKLIST.md"), "a").write("- " + line + "\n")
    print(line)
if __name__ == "__main__": main()
