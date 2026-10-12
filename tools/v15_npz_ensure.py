"""v15_npz_ensure — pilot-start NPZ completeness (USER 2026-10-12).

NO missing bar in any NPZ up to the moment of starting v15_pilot. Runs the
24/7 tail-append (tools/npz_bar_refresh.py --symbols) for the run's symbols,
refresh-and-go: failures are LOUD but never block the pilot (midrun/finalize
guards catch residue; halting compute is worse than measuring on a 1-bar lag).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def base_syms(symsides):
    out = []
    for s in symsides:
        b = str(s).upper()
        for suf in ("_LONG", "_SHORT"):
            if b.endswith(suf):
                b = b[: -len(suf)]
        if b and b not in out:
            out.append(b)
    return out


def ensure(symsides, timeout=600, dry_run=False):
    syms = base_syms(symsides)
    if not syms:
        return True
    cmd = [sys.executable, os.path.join(ROOT, "tools", "npz_bar_refresh.py"),
           "--symbols", ",".join(syms)]
    if dry_run:
        cmd.append("--dry-run")
    print(f"[NPZ-ENSURE] {' '.join(cmd)}", flush=True)
    try:
        r = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-3:]
        for line in tail:
            print(f"[NPZ-ENSURE] {line[:160]}", flush=True)
        if r.returncode != 0:
            print(f"[NPZ-ENSURE-LOUD] refresh rc={r.returncode} for {syms} — proceeding, guards own residue", flush=True)
        return True  # refresh-and-go: never block
    except Exception as e:
        print(f"[NPZ-ENSURE-LOUD] refresh failed ({e}) for {syms} — proceeding, guards own residue", flush=True)
        return True
