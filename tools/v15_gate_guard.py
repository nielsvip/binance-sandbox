#!/usr/bin/env python3
"""V15 GATE GUARD (USER 2026-10-14, roadmap docs/v15_pending/11_roadmap.md section 5): automatic fallback to last known good.

"Good" = the gate champion: md5-pinned tree + its PASS report; the last 3
champions are kept under gate/champions/ with reports. A champion redeploy is
the gate's own verified pointer — explicitly user-authorized, not a revert.

Watch signals (all already logged): ALERT_UNGATED_PILOT, pilot traceback
bursts, DATA_ERROR / 0-trade spikes, boards/h collapse vs Phase-0 baseline.
Actor: S2 cron gate_guard */10 (flock): if any production pilot md5 differs
from the champion AND (ungated-alert age > 30 min OR health red) then
atomically redeploy champion files, md5-verify, append AUTO_FALLBACK to
decisions.jsonl, and write the GATE_STATUS.md banner. The gate `watch` stays
alert-only; this guard acts.

Never touches: NPZ/klines data, live-trading configs, candidate trees,
scheduler state, brains/models. Acts only on v15_pilot.py +
data/v15_gate_flags.json (the gate watch's own signal); other gated-file
diffs (tools/v15_*, tools/v17_*, live-dir skew) are counted report-only and
never restored — those reach production via gate submit.

Kill switch: V15_GATE_GUARD=0 forces observe-only (never restores).
Tonight's test: --dry-run (prints the would-restore plan, changes nothing) +
a simulated ungated file against a sandbox copy only (V15_BENCH_DIR +
V15_GUARD_ROOTS overrides).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
BENCH = Path(os.environ.get("V15_BENCH_DIR", str(HOME / "v15_bench")))
GATE = BENCH / "gate"
CHAMP = GATE / "champion"
CHAMPS = GATE / "champions"
STATE = GATE / "guard_state.json"
DECISIONS = GATE / "decisions.jsonl"
STATUS = GATE / "GATE_STATUS.md"
ALERT = GATE / "ALERT_UNGATED_PILOT.json"
GATED = ("v15_pilot.py",)
GATED_PREFIX = ("tools/v15_", "tools/v17_")
FLAGS_REL = "data/v15_gate_flags.json"
STALE_MIN = float(os.environ.get("V15_GUARD_STALE_MIN", "30"))
TB_BURST = int(os.environ.get("V15_GUARD_TB_BURST", "20"))
ENABLED = os.environ.get("V15_GATE_GUARD", "1") == "1"


def _roots():
    ov = os.environ.get("V15_GUARD_ROOTS")
    if ov:
        out = []
        for item in ov.split(","):
            host, _, root = item.partition(":")
            out.append((host.strip() or "local", (root.strip())))
        return out
    return [("local", str(HOME / "binance-sandbox")), ("local", str(HOME / "binance")), ("10.0.0.3", "/home/niels/binance-sandbox"), ("10.0.0.3", "/home/niels/binance")]


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd, timeout=50):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def gated_files(tree):
    out = {}
    cands = [tree / GATED[0]]
    for pat in ("tools/v15_*", "tools/v17_*"):
        cands += [p for p in tree.glob(pat) if p.is_file() and p.suffix in (".py", ".sh")]
    for p in cands:
        out[str(p.relative_to(tree))] = md5(p)
    return out


def batch_remote_md5s(host, root, rels, chunk=100):
    out = {}
    rels = list(rels)
    for i in range(0, len(rels), chunk):
        paths = " ".join(f"{root}/{r}" for r in rels[i:i + chunk])
        r = sh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, f"md5sum {paths} 2>/dev/null"], timeout=55)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].startswith(root + "/"):
                out[parts[1][len(root) + 1:]] = parts[0]
    return out


def remote_read(host, path):
    r = sh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, f"cat {path}"])
    return r.stdout if r.returncode == 0 else None


def prod_flags(host, root):
    if host == "local":
        p = Path(root) / FLAGS_REL
        return p.read_text() if p.is_file() else None
    return remote_read(host, f"{root}/{FLAGS_REL}")


def flags_match(champ_flags, raw):
    if raw is None:
        return not champ_flags
    try:
        doc = json.loads(raw)
    except Exception:
        return False
    return (doc.get("flags") or {}) == (champ_flags or {})


def health():
    probes, red = {}, False
    tb = 0
    cutoff = time.time() - 3600
    logs = [HOME / "logs" / "v15_version_gate.log"] + sorted(GATE.glob("bench_*.log"))
    for lp in logs:
        try:
            if lp.is_file() and lp.stat().st_mtime > cutoff - 86400:
                out = sh(["grep", "-c", "Traceback", str(lp)]).stdout.strip()
                tb += int(out or 0)
        except Exception:
            continue
    probes["traceback_1h"] = tb
    if tb > TB_BURST:
        red = True
    base = os.environ.get("V15_GUARD_BASELINE_BOARDS_H")
    probes["boards_h"] = "unknown (Phase-0 baseline pending)" if not base else f"baseline {base}/h (auto-compare lands with lane C KPI)"
    probes["data_error_spike"] = "unknown (Phase-0 baseline pending)"
    return probes, red


def alert_age_min():
    if ALERT.is_file():
        try:
            at = json.loads(ALERT.read_text()).get("at")
            return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() / 60.0
        except Exception:
            return (time.time() - ALERT.stat().st_mtime) / 60.0
    return None


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"first_seen": {}}


def snapshot_champion(meta, dry):
    keep = 3
    snap = CHAMPS / (meta.get("pilot_md5") or "unknown")[:12]
    if not dry:
        (CHAMPS).mkdir(parents=True, exist_ok=True)
        if not (snap / "v15_pilot.py").exists() and (CHAMP / "tree" / "v15_pilot.py").exists():
            snap.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CHAMP / "tree" / "v15_pilot.py", snap / "v15_pilot.py")
            (snap / "meta.json").write_text(json.dumps(meta, indent=1))
        snaps = sorted(CHAMPS.iterdir(), key=lambda p: p.stat().st_mtime)
        for old in snaps[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
    return sorted([p.name for p in CHAMPS.iterdir()]) if CHAMPS.exists() else []


def restore_file(host, root, rel, src, dry):
    want = md5(src)
    if dry:
        return {"host": host, "root": root, "file": rel, "ok": None, "dry": True}
    if host == "local":
        dst = Path(root) / rel
        tmp = dst.with_name(f".{dst.name}.guard.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        return {"host": host, "root": root, "file": rel, "ok": md5(dst) == want}
    tmpr = f"{root}/.{Path(rel).name}.guard.tmp"
    sh(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(src), f"{host}:{tmpr}"])
    r = sh(["ssh", "-o", "BatchMode=yes", host, f"test -d {root}/{Path(rel).parent} && mv {tmpr} {root}/{rel} && md5sum {root}/{rel} || rm -f {tmpr}"])
    return {"host": host, "root": root, "file": rel, "ok": (r.stdout.split() or [""])[0] == want}


def restore_flags(host, root, champ_flags, dry):
    src = GATE / "flags.guard.tmp.json"
    if dry:
        return {"host": host, "root": root, "file": FLAGS_REL, "ok": None, "dry": True}
    src.write_text(json.dumps({"at": now(), "candidate": "guard-champion-restore", "flags": champ_flags or {}}, indent=1))
    return restore_file(host, root, FLAGS_REL, src, False)


def main(argv=None):
    ap = argparse.ArgumentParser(description="V15 gate guard: auto-fallback to the gate champion.")
    ap.add_argument("--dry-run", action="store_true", help="print the would-restore plan, change nothing")
    a = ap.parse_args(argv)
    dry = a.dry_run or not ENABLED
    meta = json.loads((CHAMP / "meta.json").read_text())
    champ_flags = meta.get("flags") or {}
    champ_tree = CHAMP / "tree"
    champ_gated = gated_files(champ_tree)
    acted_rels = [GATED[0], FLAGS_REL]
    mism, plan, other_diffs = [], [], 0
    roots = _roots()
    remote_maps = {}
    for host, root in roots:
        if host != "local" and host not in remote_maps:
            remote_maps[host] = batch_remote_md5s(host, root, champ_gated)
    for host, root in roots:
        for rel, want in champ_gated.items():
            if host == "local":
                p = Path(root) / rel
                got = md5(p) if p.is_file() else None
            else:
                got = remote_maps[host].get(rel)
            if got == want:
                continue
            if rel in acted_rels:
                mism.append({"host": host, "root": root, "file": rel, "got": got, "want": want})
            else:
                other_diffs += 1
        if not flags_match(champ_flags, prod_flags(host, root)):
            mism.append({"host": host, "root": root, "file": FLAGS_REL, "got": "flags-differ", "want": json.dumps(champ_flags)})
    probes, health_red = health()
    state = load_state()
    t = time.time()
    for m in mism:
        k = f"{m['host']}:{m['root']}:{m['file']}"
        state["first_seen"].setdefault(k, t)
    for k in list(state["first_seen"]):
        if not any(f"{m['host']}:{m['root']}:{m['file']}" == k for m in mism):
            del state["first_seen"][k]
    ages = [(t - state["first_seen"][f"{m['host']}:{m['root']}:{m['file']}"]) / 60.0 for m in mism]
    max_age = max(ages) if ages else 0.0
    aage = alert_age_min()
    stale = max_age >= STALE_MIN or (aage is not None and aage >= STALE_MIN)
    if mism and not a.dry_run:
        STATE.write_text(json.dumps(state, indent=1))
    act = bool(mism) and (stale or health_red)
    for m in mism:
        k = f"{m['host']}:{m['root']}:{m['file']}"
        plan.append(f"would-restore {k} got={m['got']} want={m['want']} age={((t - state['first_seen'][k]) / 60.0):.1f}min")
    print(json.dumps({"at": now(), "champion": meta.get("pilot_md5"), "mismatches": len(mism), "other_gated_diffs_report_only": other_diffs, "max_age_min": round(max_age, 1), "alert_age_min": round(aage, 1) if aage is not None else None, "health": probes, "health_red": health_red, "stale": stale, "would_act": act, "dry": dry, "plan": plan}, indent=1))
    if not mism:
        print("[guard] GREEN — production matches the champion")
        return 0
    if not act:
        print(f"[guard] GREEN-WAIT — {len(mism)} mismatch(es) younger than {STALE_MIN}min and health ok; no restore")
        return 0
    if dry:
        print("[guard] DRY-RUN — plan above, changed nothing")
        return 0
    results = []
    for m in mism:
        if m["file"] == FLAGS_REL:
            results.append(restore_flags(m["host"], m["root"], champ_flags, False))
        else:
            results.append(restore_file(m["host"], m["root"], m["file"], champ_tree / m["file"], False))
    ok = all(r.get("ok") for r in results)
    with open(DECISIONS, "a") as fh:
        fh.write(json.dumps({"id": "guard-fallback-" + now().replace(":", "").replace("-", ""), "at": now(), "state": "AUTO_FALLBACK", "champion": meta.get("pilot_md5"), "restored": results, "ok": ok, "reason": {"max_age_min": round(max_age, 1), "health_red": health_red}}) + "\n")
    banner = [f"# V15 version gate — {now()}", "", f"champion: pilot md5 `{meta.get('pilot_md5')}` from {meta.get('from')} at {meta.get('at')}", f"gated switch defaults: `{json.dumps(champ_flags)}`", "", f"> AUTO_FALLBACK at {now()}: restored {sum(1 for r in results if r.get('ok'))}/{len(results)} files (verify: md5==champion).", ""]
    if DECISIONS.exists():
        for line in DECISIONS.read_text().splitlines()[-20:]:
            r = json.loads(line)
            banner.append(f"| {r.get('at', r.get('id', '?'))} | {r.get('state')} | {str(r.get('restored', r.get('note', '')))[:40]} |")
    STATUS.write_text("\n".join(banner) + "\n")
    snaps = snapshot_champion(meta, False)
    state = {"first_seen": {}}
    STATE.write_text(json.dumps(state, indent=1))
    print(json.dumps({"AUTO_FALLBACK": ok, "restored": results, "champion_snaps": snaps}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
