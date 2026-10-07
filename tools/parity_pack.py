#!/usr/bin/env python3
"""parity_pack — run a parity smoke pack over sym_sides and summarize (manual ops tool for cut#4 re-verify and Monday pre-verify; runs ON a host with NPZs; the Monday chain has its own launch logic in final_orch.drive).

Launch: host-parity per sym_side (staggered). Poll: host-collect until all terminal or timeout. Print: PASS/FAIL/UNAVAILABLE counts + lists.
"""
import argparse, json, sys, time

TERMINAL = ("PASS", "FAIL", "UNAVAILABLE")


def run_pack(syms, progress_dir, final_dir, launch, collect, stagger_s=10, timeout_s=4200, poll_s=60):
    launched, errors = [], {}
    for ss in syms:
        try:
            launch(ss, final_dir, f"{progress_dir}/{ss}_v14_progress.json")
            launched.append(ss)
        except Exception as e:
            errors[ss] = str(e)[:150]
        time.sleep(stagger_s)
    if not launched:
        return {"PASS": [], "FAIL": [], "UNAVAILABLE": [], "RUNNING": [], "ERROR": errors}
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        try:
            last = collect(final_dir).get("parity", {})
        except Exception as e:
            last = {"_collect_error": str(e)[:150]}
        if launched and all((last.get(ss) or {}).get("status") in TERMINAL for ss in launched):
            break
        time.sleep(poll_s)
    summary = {"PASS": [], "FAIL": [], "UNAVAILABLE": [], "RUNNING": [], "ERROR": errors}
    for ss in launched:
        st = (last.get(ss) or {}).get("status", "UNAVAILABLE")
        summary[st if st in summary else "UNAVAILABLE"].append(ss)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-sides", required=True)
    ap.add_argument("--progress-dir", required=True)
    ap.add_argument("--final-dir", required=True)
    ap.add_argument("--timeout-s", type=int, default=4200)
    a = ap.parse_args()
    import v15_final_phase as FP
    syms = [s.strip() for s in a.sym_sides.split(",") if s.strip()]

    def collect(fd):
        import io, contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            FP.host_collect(fd)
        return json.loads(buf.getvalue().splitlines()[-1])

    print(json.dumps(run_pack(syms, a.progress_dir, a.final_dir, FP.host_parity, collect, timeout_s=a.timeout_s), indent=1))


if __name__ == "__main__":
    sys.path.insert(0, "tools")
    main()
