"""Build the S5 live-verification manifest from Mac lifecycle progress JSONs.

Source of truth: data/reports/lifecycle_pilot/*progress.json (final sets =
cumulative_overrides + recorded gains). Emits one entry per symside plus run
provenance. Read-only over inputs; writes only the new manifest dir.
"""
import glob
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(ROOT, "data", "reports", "lifecycle_pilot")
OUTDIR = os.path.join(ROOT, "data", "reports", "s5_verify_20261003")


def md5_of(path):
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(PROG, "*progress.json")))
    entries = []
    skipped = []
    for path in files:
        try:
            with open(path) as f:
                d = json.load(f)
        except Exception as e:
            skipped.append({"file": os.path.basename(path), "reason": f"unreadable: {e}"[:120]})
            continue
        symside = d.get("symside") or os.path.basename(path).replace("_v14_progress.json", "").replace("_30d_progress.json", "").replace("_7d_progress.json", "")
        done = d.get("done", [])
        try:
            done_n = len(done)
        except TypeError:
            done_n = 0
        ov = d.get("cumulative_overrides") or {}
        if not isinstance(ov, dict):
            skipped.append({"file": os.path.basename(path), "reason": "cumulative_overrides not a dict"})
            continue
        try:
            json.dumps(ov)
        except (TypeError, ValueError):
            skipped.append({"file": os.path.basename(path), "reason": "overrides not JSON-serializable"})
            continue
        lv = d.get("live_verified")
        entries.append({
            "symside": symside,
            "file": os.path.basename(path),
            "window_days": int(d.get("window_days") or 30),
            "bh": d.get("bh"),
            "baseline_gain": d.get("baseline_gain"),
            "initial_baseline_gain": d.get("initial_baseline_gain"),
            "cumulative_gain": d.get("cumulative_gain"),
            "final_gain_fresh_vec": d.get("final_gain_fresh_vec"),
            "overrides": ov,
            "overrides_n": len(ov),
            "done_n": done_n,
            "has_live_gain": bool(isinstance(lv, dict) and lv.get("gain_pct") is not None),
            "live_reason": (lv.get("reason") if isinstance(lv, dict) else None),
            "engine_mixed_chain": d.get("engine_mixed_chain"),
            "needs_redo": bool(d.get("needs_redo")),
            "diagnostic_only": d.get("diagnostic_only"),
            "final_365d": d.get("final_365d"),
        })
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        git_head = None
    pref = {"v14": 0, "30d": 1, "7d": 2}
    def rank(e):
        tag = "v14" if "_v14_" in e["file"] else ("30d" if "_30d_" in e["file"] else ("7d" if "_7d_" in e["file"] else "zzz"))
        return (-e["done_n"], pref.get(tag, 9), e["file"])
    best = {}
    dropped = []
    for e in sorted(entries, key=rank):
        if e["symside"] not in best:
            best[e["symside"]] = e
        else:
            dropped.append({"symside": e["symside"], "file": e["file"], "done_n": e["done_n"], "overrides_n": e["overrides_n"]})
    entries = sorted(best.values(), key=lambda e: e["symside"])
    manifest = {
        "run": "s5_verify_20261003",
        "source": "mac data/reports/lifecycle_pilot",
        "n_entries": len(entries),
        "n_skipped": len(skipped),
        "skipped": skipped,
        "n_dedup_dropped": len(dropped),
        "dedup_dropped": dropped,
        "mac_md5": {n: md5_of(os.path.join(ROOT, n)) for n in ("backtest_v12_engine.py", "v12_quick_engine.py", "v15_pilot.py", "ez_manage.py", "tradier_manage.py", "config.py", "config_tradier.py")},
        "mac_git_head": git_head,
        "entries": entries,
    }
    out = os.path.join(OUTDIR, "manifest.json")
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f)
    os.replace(tmp, out)
    with_live = sum(1 for e in entries if e["has_live_gain"])
    with_done = sum(1 for e in entries if e["done_n"] > 0)
    print(f"entries={len(entries)} skipped={len(skipped)} with_done={with_done} already_live={with_live} -> {out}")


if __name__ == "__main__":
    sys.exit(main())
