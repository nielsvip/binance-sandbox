#!/usr/bin/env python3
"""zero_stage_run — test STAGED engine files (data/zero_audit/staged/overlay/**) on a host WITHOUT touching its live ~/binance-sandbox.
Builds /tmp/zstage_<tag> = symlinks to every sandbox entry except the overlaid files (overlay files are copied in; vec_decisions is a
per-file symlink dir), then runs tools/zero_stage_eval.py there: for each sym_side evaluate BASE (live engine via sandbox) vs STAGED
(overlay) on default overrides and on an optional flip, printing gain/trades/fingerprint and the exit-reason histogram.
  python tools/zero_stage_run.py --host s2 --tag t1 --symsides UEC_SHORT,AAPL_LONG [--flip KEY=VAL ...]
"""
import argparse, os, subprocess, sys, pathlib, shlex
ROOT = pathlib.Path(__file__).resolve().parents[1]
OV = ROOT / "data" / "zero_audit" / "staged" / "overlay"
REMOTE_EVAL = r'''
import sys, os, json, collections, importlib
stage, sandbox, flips = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
mode = sys.argv[4]; symsides = sys.argv[5].split(",")
root = stage if mode == "staged" else sandbox
os.chdir(root); sys.path.insert(0, root)
import v12_quick_engine as V
assert V.__file__.startswith(root), V.__file__
import pkgutil, vec_decisions
for _m in pkgutil.iter_modules(vec_decisions.__path__):
    if not _m.name.startswith("test_"):
        importlib.import_module("vec_decisions." + _m.name)
assert list(vec_decisions.__path__)[0].startswith(root), vec_decisions.__path__
from tools.opt import v12_pilot as vp
sys.path.insert(0, root)
import vec_decisions.mtf_exit_scorer as _M
assert _M.__file__.startswith(root), _M.__file__
for ss in symsides:
    for tag, ov in [("default", {})] + [(f"flip {k}={v}", {k: v}) for k, v in flips.items()]:
        try:
            r = vp.evaluate_sanitized(ss, ov, window_days=30, include_ledger=True)
        except Exception as e:
            print(json.dumps({"mode": mode, "ss": ss, "tag": tag, "err": str(e)[:120]})); continue
        led = [t for t in (r.get("ledger") or []) if isinstance(t, dict) and "pnl_dollars" in t]
        why = collections.Counter(str(t.get("exit_reason") or t.get("reason"))[:26].split(" g")[0] for t in led)
        print(json.dumps({"mode": mode, "ss": ss, "tag": tag, "gain": round(r.get("gain_pct") or 0, 4), "trades": r.get("trades"), "tim": r.get("tim_pct"), "fp": str(r.get("behavior_fingerprint"))[:10], "exits": dict(why.most_common(6))}), flush=True)
'''
def sh(host, cmd, timeout=900):
    return subprocess.run(["ssh", "-o", "StrictHostKeyChecking=accept-new", host, cmd], capture_output=True, text=True, timeout=timeout)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True); ap.add_argument("--tag", default="t")
    ap.add_argument("--symsides", required=True); ap.add_argument("--flip", action="append", default=[])
    a = ap.parse_args()
    stage = f"/tmp/zstage_{a.tag}"
    files = [str(p.relative_to(OV)) for p in OV.rglob("*.py")]
    build = f"rm -rf {stage}; mkdir -p {stage}; cd ~/binance-sandbox; for f in $(ls -A); do case $f in vec_decisions) ;; *) ln -s \"$PWD/$f\" {stage}/$f 2>/dev/null;; esac; done; mkdir -p {stage}/vec_decisions; for f in vec_decisions/* ; do ln -s \"$PWD/$f\" {stage}/$f 2>/dev/null; done; for f in {' '.join(files)}; do rm -f {stage}/$f; done; echo built"
    print(sh(a.host, build).stdout.strip())
    for f in files:
        subprocess.run(["scp", "-q", str(OV / f), f"{a.host}:{stage}/{f}"], check=True)
    flips = {}
    for x in a.flip:
        k, v = x.split("=", 1); flips[k] = {"True": True, "False": False}.get(v, v)
    import json
    scr = f"{stage}_eval.py"
    subprocess.run(["ssh", a.host, f"cat > {scr}"], input=REMOTE_EVAL, text=True)
    for mode in ("base", "staged"):
        r = sh(a.host, f"cd ~/binance-sandbox && nice -n 10 .venv/bin/python {scr} {stage} $HOME/binance-sandbox {shlex.quote(json.dumps(flips))} {mode} {a.symsides} 2>&1 | grep '^{{'")
        print(r.stdout.strip())
if __name__ == "__main__":
    main()
