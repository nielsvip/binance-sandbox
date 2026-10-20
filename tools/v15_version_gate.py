#!/usr/bin/env python3
"""V15 VERSION GATE (USER 2026-10-14): "make sure the verifier is in place on the fixed npz set on s2 that runs every new version of
v15_pilot.py and subscripts and only allows a modification if results improve — not when skipping switches or filters leads to
deterioration of the system".

Runs on S2 (~/v15_bench). Nothing reaches production pilots unless it PASSES here:
  submit  --files F [F ...] [--set K=V ...] [--note TXT]   new candidate = champion tree + these files (+ gated switch defaults)
  run                                                      (cron */20, flock) bench the oldest pending candidate vs the champion
  status                                                   champion + last decisions
  watch                                                    alert when production code differs from the champion without a PASS
Bench: tools/v15_npz_bench.py on the frozen snapshot (default snap_20261014, 4 sides, one per cat_side), champion and candidate
run CONCURRENTLY (same host load), both with V15_HOLDOUT_DAYS=7 so every board gives its FIT gain (what the board finds) and its
OOS gain on 7 unseen days.
PASS = every side has a result in both runs AND sum(fit candidate) >= sum(fit champion) - TOL_SUM AND no side's fit gain drops more
than TOL_SIDE AND sum(OOS candidate) >= sum(OOS champion) - TOL_SUM. Skipping switches/filters that loses board gain = FAIL.
On PASS: candidate files are deployed (atomic, md5-verified) to S2 + S1 sandbox and live dirs, the gated switch defaults are
written to data/v15_gate_flags.json on both hosts (the pilot reads defaults from it; env still wins), and the candidate becomes the
champion. On FAIL nothing is deployed. Decisions: ~/v15_bench/gate/decisions.jsonl + GATE_STATUS.md. Never reverts production.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
BENCH = Path(os.environ.get("V15_BENCH_DIR", str(HOME / "v15_bench")))
GATE = BENCH / "gate"
CHAMP = GATE / "champion"
CANDS = GATE / "candidates"
DECISIONS = GATE / "decisions.jsonl"
STATUS = GATE / "GATE_STATUS.md"
SNAP = os.environ.get("V15_GATE_SNAP", "20261014")
# 2026-10-16 USER "test every version side by side against the previous version so corner cutting is exposed": default =
# EVERY side of the snapshot (8, two per cat_side) instead of 4 — one side per cat_side let a single noisy board decide.
SIDES = os.environ.get("V15_GATE_SIDES", "")
# Corner-cut rule: a candidate whose boards evaluate less of the template (yellow cells or naked rows) than the champion's
# boards by more than this share must WIN on unseen days by more than the noise floor and may not lose fit gain at all.
COVERAGE_MIN = float(os.environ.get("V15_GATE_COVERAGE_MIN", "0.90"))
# Verdicts are made on chart-truth numbers only (tools/v15_bench_truth.py); V15_GATE_REQUIRE_TRUTH=0 = legacy self-reported.
REQUIRE_TRUTH = os.environ.get("V15_GATE_REQUIRE_TRUTH", "1") != "0"
# Launch-env keys that only route files/caches (never what a board computes) — ignored by env drift + env diffs.
ENV_IGNORE = {"V15_PROGRESS_DIR", "V15_OUT_DIR", "V15_START_OVERRIDES", "V15_ISOLATED_PROOF", "V15_EVAL_CACHE_DB", "V15_FILL_SLICE_S", "V15_V17_CARRY_DIR", "V15_DEFAULTS_ROUND", "V15_FRESH_RUN", "V15_SKIP_LIVE_AT_DONE", "V12_NPZ_CACHE", "V12_ALLOW_STALE_NPZ", "V15_HOLDOUT_DAYS"}
# USER 2026-10-15 "make sure some noise is acceptable we need to move ahead". MEASURED noise floor: two runs of the
# IDENTICAL champion tree on the IDENTICAL frozen NPZ gave 3374 vs 3376 rows, 8 vs 10 promotions, gain 8.4366 vs
# 8.9128 -> |d_gain| <= 0.48 per board, promoted-set hash unstable, because the late phases are budgeted in wall-clock
# seconds (V15_V17_P3_MAX_S_*, V15_FOCUS_S_*, V15_ENDGAME_S) so timing decides what gets explored. A per-side
# tolerance of 2.0 is ~4x that floor, but the SUM of 4 sides carries ~0.48*sqrt(4) = 0.95 of noise on its own, so the
# old TOL_SUM=1.0 sat exactly on the floor and would FAIL correct candidates about half the time. 2.5 keeps a real
# deterioration (a lost switch costs far more than 2.5 summed) detectable while noise alone can no longer block a ship.
NOISE_FLOOR_PER_SIDE = float(os.environ.get("V15_GATE_NOISE_FLOOR", "0.48"))
TOL_SUM = float(os.environ.get("V15_GATE_TOL_SUM", "2.5"))
TOL_SIDE = float(os.environ.get("V15_GATE_TOL_SIDE", "2.0"))
RUN_TIMEOUT_MIN = float(os.environ.get("V15_GATE_RUN_TIMEOUT_MIN", "720"))  # 8 sides x ~55 min per tree, two trees concurrently
MIN_FREE_MB = float(os.environ.get("V15_GATE_MIN_FREE_MB", "20000"))
SRC = HOME / "binance-sandbox"
DEPLOY = [("local", HOME / "binance-sandbox"), ("local", HOME / "binance"), ("10.0.0.3", "/home/niels/binance-sandbox"), ("10.0.0.3", "/home/niels/binance")]
# USER 2026-10-17 ("all of it should be fed into the next v15_pilot calculations then it gets into live trading if
# proven successful"): switch_types.py and switch_parity.py were NOT gated, yet v15_pilot imports both and
# tools/switch_types.coerce IS the pilot's type law (_types_coerce_reason, v15_pilot:2193) — "a candidate the engine
# cannot consume must never be evaluated". A change there can therefore add or remove candidate cells and move board
# output with no bench at all. Widening the allowlist only makes the gate STRICTER (more files must PASS to deploy).
# The 2026-10-17 parse fix that exposed this was measured inert first: 0 of 241,471 template candidate values
# changed accept/reject verdict, so it moved nothing — the hole was in coverage, not in that change.
GATED = ("v15_pilot.py", "switch_parity.py", "switch_types.py", "tools/switch_types.py")
GATED_PREFIX = ("tools/v15_", "tools/v17_", "tools/v18_")
# 2026-10-20: V18 brain code + its data (trained models, digests) are bench inputs too; the bench tree is a full copy of the champion
# tree, so data files copied here are visible to the candidate pilot. Deploy is the same generic path: a destination whose parent dir
# does not exist is skipped (not failed), exactly as the local branch already did.
GATED_DATA_RE = re.compile(r"^data/(brain/v18_brain_[A-Za-z0-9_]+|learn/digest_[A-Za-z0-9_]+)\.json$")
CODE_EXCLUDE = ["data", "backtest_v8", "backtest_v7", "SPREADSHEETS", "klines_*", "archive", "backups", ".venv", "backtest_framework", ".git", "logs", "*.log", "*.db", "*.sqlite", "*.npz", "*.xlsx", "__pycache__", ".history", ".elastic"]


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rsync_tree(src, dst):
    ex = []
    for e in CODE_EXCLUDE:
        ex += ["--exclude", e]
    subprocess.run(["rsync", "-a", "--delete"] + ex + [f"{src}/", f"{dst}/"], check=True)


def ensure_champion():
    if not (CHAMP / "tree" / "v15_pilot.py").exists():
        (CHAMP / "tree").mkdir(parents=True, exist_ok=True)
        rsync_tree(SRC, CHAMP / "tree")
        meta = {"at": now(), "from": "production sandbox at gate install", "pilot_md5": md5(CHAMP / "tree" / "v15_pilot.py"), "flags": {}}
        write_json_atomic(CHAMP / "meta.json", meta)
    return json.loads((CHAMP / "meta.json").read_text())


def gated_path(rel):
    return rel in GATED or rel.startswith(GATED_PREFIX) or bool(GATED_DATA_RE.match(rel))


def snap_manifest():
    try:
        return json.loads((BENCH / f"snap_{SNAP}" / "manifest.json").read_text())
    except Exception:
        return {}


def gate_sides():
    if SIDES:
        return [s for s in SIDES.split(",") if s]
    man = snap_manifest()
    return [s for s in man.get("sides") or [] if any(s.startswith(sym) and s[len(sym):] in ("_LONG", "_SHORT") for sym in (man.get("npz") or {}))]


def result_env(env):
    return {k: str(v) for k, v in (env or {}).items() if k.startswith(("V15_", "V12_")) and k not in ENV_IGNORE}


def champ_env(champ):
    """The launch env the champion is benched (and production must run) with. Bootstrap = the snapshot's captured env."""
    return result_env(champ.get("env") if champ.get("env") is not None else snap_manifest().get("env"))


def env_diff(want, got):
    keys = sorted(set(want) | set(got))
    return {k: {"champion": want.get(k), "running": got.get(k)} for k in keys if want.get(k) != got.get(k)}


PROC_ENV_SH = "for p in $(pgrep -f '^[^ ]*python[^ ]* -u [^ ]*v15_pilot.py --sym-side'); do c=$(readlink /proc/$p/cwd); case \"$c\" in *v15_bench*) continue;; esac; echo \"@@PID $p $c\"; tr '\\0' '\\n' < /proc/$p/environ 2>/dev/null | grep -E '^V1[25]_'; done"


def running_pilot_envs(host):
    """Result-affecting env of every PRODUCTION pilot on a host (bench pilots under ~/v15_bench excluded)."""
    cmd = ["bash", "-c", PROC_ENV_SH] if host == "local" else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, PROC_ENV_SH]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return []
    envs, cur = [], None
    for line in out.splitlines():
        if line.startswith("@@PID "):
            cur = {"pid": line.split()[1], "env": {}}
            envs.append(cur)
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            cur["env"][k] = v
    for e in envs:
        e["env"] = result_env(e["env"])
    return [e for e in envs if e["env"]]  # unreadable environ (process gone / not ours) carries no evidence


def cmd_submit(a):
    champ = ensure_champion()
    cid = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cd = CANDS / cid
    (cd / "tree").mkdir(parents=True)
    rsync_tree(CHAMP / "tree", cd / "tree")
    files = {}
    for spec in a.files or []:
        src, _, rel = spec.partition("=")
        rel = rel or os.path.relpath(os.path.abspath(src), str(SRC))
        if not gated_path(rel):
            print(f"[gate] {rel}: not a gated file (v15_pilot.py, tools/v15_*, tools/v17_*, tools/v18_*, data/brain/v18_brain_*.json, data/learn/digest_*.json) — refused", file=sys.stderr)
            shutil.rmtree(cd)
            return 2
        (cd / "tree" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, cd / "tree" / rel)
        files[rel] = md5(cd / "tree" / rel)
    flags = dict(champ.get("flags") or {})
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        flags[k] = v
    # Launch env (what the scheduler/autotune export at dispatch) is benched like code: candidate env = champion env + changes.
    env = champ_env(champ)
    if a.env_from_production:
        envs = running_pilot_envs(a.env_from_production)
        if not envs:
            print(f"[gate] --env-from-production {a.env_from_production}: no running production pilot found — refused", file=sys.stderr)
            shutil.rmtree(cd)
            return 2
        env = dict(envs[0]["env"])
    for kv in a.env or []:
        k, _, v = kv.partition("=")
        if v == "":
            env.pop(k, None)
        else:
            env[k] = v
    env = result_env(env)
    meta = {"id": cid, "submitted": now(), "note": a.note or "", "priority": int(a.priority or 0), "files": files, "flags": flags, "env": env, "env_changes": env_diff(champ_env(champ), env), "champion_pilot_md5": champ.get("pilot_md5"), "state": "pending"}
    if not files and not a.set and not meta["env_changes"] and not a.allow_identical:
        print("[gate] nothing to bench: no files, no --set, launch env identical to the champion's — refused", file=sys.stderr)
        shutil.rmtree(cd)
        return 2
    write_json_atomic(cd / "meta.json", meta)
    print(json.dumps(meta, indent=1))
    return 0


def bench_env(env):
    """Full env a bench pilot runs with: the snapshot's captured env (routing keys included) with the version's
    result-affecting env laid over it; a result key the version removed is removed. 2026-10-16: passing the filtered env
    alone dropped V12_ALLOW_STALE_NPZ, every frozen-snapshot NPZ read as STALE and both arms aborted in 10-40 s (rc=2)."""
    man_env = dict(snap_manifest().get("env") or {})
    out = {k: v for k, v in man_env.items() if k not in result_env(man_env)}
    out.update(result_env(env))
    out["V12_ALLOW_STALE_NPZ"] = "1"  # the snapshot is frozen by design; staleness vs wall clock is not a verdict
    return out


def bench(label, code, flags, env):
    env_file = GATE / f"env_{label}.json"
    write_json_atomic(env_file, bench_env(env))
    args = [sys.executable, str(BENCH / "tools" / "v15_npz_bench.py"), "run", "--snap", SNAP, "--label", label, "--code", str(code), "--only", ",".join(gate_sides()), "--env-file", str(env_file), "--parallel", "1", "--workers", "2", "--nice", "10", "--min-free-mb", "7000", "--force", "--set", "V15_HOLDOUT_DAYS=7"]
    for k, v in flags.items():
        args += ["--set", f"{k}={v}"]
    for k in ("V15_BRAIN", "V15_BRAIN_NAKED"):
        if k not in flags:
            args += ["--set", f"{k}=0"]
    log = open(GATE / f"bench_{label}.log", "w")
    return subprocess.Popen(args, cwd=str(BENCH), stdout=log, stderr=subprocess.STDOUT)


def results(label):
    p = BENCH / "runs" / f"{SNAP}__{label}" / "results.json"
    return json.loads(p.read_text()).get("results") or {} if p.exists() else {}


def _progress_json(label, side):
    d = BENCH / "runs" / f"{SNAP}__{label}" / side / "progress"
    for p in sorted(d.glob("*_progress.json")) if d.exists() else []:
        if ".npzprev_" not in p.name and ".hollowprev_" not in p.name:
            return p
    return None


def truth_rescore(lc, la, ch, ca):
    """USER 2026-10-16 "numbers never exaggerated and match exactly the html charts": both arms' final sets (and the shared
    start set) re-scored by ONE scorer (tools/v15_bench_truth.py in the champion run root, same frozen NPZ) from the full
    execution ledger, unrealised window-edge marks excluded — the chart's own math. decide() uses these when present."""
    root = BENCH / "runs" / f"{SNAP}__{lc}" / "root"
    py = root / ".venv" / "bin" / "python"
    try:
        fit_days = int(json.loads((BENCH / "runs" / f"{SNAP}__{lc}" / "results.json").read_text()).get("window_days_fit") or 20)
    except Exception:
        fit_days = 20
    for s in set(ch) | set(ca):
        P, Q = _progress_json(lc, s), _progress_json(la, s)
        if not (P and Q and py.exists()):
            continue
        args = [str(py if py.exists() else sys.executable), str(BENCH / "tools" / "v15_bench_truth.py"), "--root", str(root), "--side", s, "--fit-days", str(fit_days), "--holdout-days", "7", "--set", f"champ={P}", "--set", f"cand={Q}", "--set", f"start={P}:initial_overrides"]
        try:
            r = subprocess.run(["nice", "-n", "10"] + args, capture_output=True, text=True, timeout=900)
            t = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except Exception as e:
            t = {"error": f"{type(e).__name__}: {e}"[:200]}
        for res, key in ((ch, "champ"), (ca, "cand")):
            if s in res and isinstance(t.get(key), dict):
                res[s]["truth"] = t[key]
                res[s]["truth_start"] = t.get("start")
            elif s in res:
                res[s]["truth_error"] = t.get("error") or "no truth row"
    return ch, ca


def _f(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None


def _cov(m, k):
    c = m.get("coverage") if isinstance(m.get("coverage"), dict) else {}
    return int(c.get(k) or 0)


def decide(ch, ca, sides=None):
    sides = sides or gate_sides() or sorted(set(ch) | set(ca))
    rows, missing, drops, uninformative, scored = [], [], [], [], []
    sf_ch = sf_ca = so_ch = so_ca = 0.0
    cov = {"cells_champ": 0, "cells_cand": 0, "naked_champ": 0, "naked_cand": 0, "rows_champ": 0, "rows_cand": 0}
    n_truth = 0
    for s in sides:
        a, b = ch.get(s) or {}, ca.get(s) or {}
        fa, fb, oa, ob = _f(a.get("final_gain")), _f(b.get("final_gain")), _f(a.get("oos_final")), _f(b.get("oos_final"))
        ta, tb = a.get("truth") or {}, b.get("truth") or {}
        if all(_f((t.get(ph) or {}).get("gain_pct")) is not None for t in (ta, tb) for ph in ("fit", "oos")):
            # Chart-truth numbers (common scorer, no unrealised marks) replace each arm's self-reported gains.
            fa, fb, oa, ob = _f(ta["fit"]["gain_pct"]), _f(tb["fit"]["gain_pct"]), _f(ta["oos"]["gain_pct"]), _f(tb["oos"]["gain_pct"])
            n_truth += 1
        rows.append({"side": s, "fit_champ": fa, "fit_cand": fb, "oos_champ": oa, "oos_cand": ob, "wall_champ": a.get("wall_s"), "wall_cand": b.get("wall_s"), "evals_champ": a.get("evals_noncached"), "evals_cand": b.get("evals_noncached"),
                     "truth": bool(ta and tb), "engine_fit_champ": a.get("final_gain"), "engine_fit_cand": b.get("final_gain"), "engine_oos_champ": a.get("oos_final"), "engine_oos_cand": b.get("oos_final"),
                     "wr_champ": (ta.get("fit") or {}).get("wr_pct"), "wr_cand": (tb.get("fit") or {}).get("wr_pct"), "marks_champ": sum(int((ta.get(ph) or {}).get("unrealised_marks") or 0) for ph in ("fit", "oos")), "marks_cand": sum(int((tb.get(ph) or {}).get("unrealised_marks") or 0) for ph in ("fit", "oos")),
                     "oos_trades_champ": (ta.get("oos") or {}).get("trades"), "oos_trades_cand": (tb.get("oos") or {}).get("trades"), "oos_start": ((a.get("truth_start") or {}).get("oos") or {}).get("gain_pct"),
                     "trades_champ": (ta.get("fit") or {}).get("trades", a.get("trades")), "trades_cand": (tb.get("fit") or {}).get("trades", b.get("trades")), "dd_champ": (ta.get("fit") or {}).get("max_dd_pct", a.get("dd")), "dd_cand": (tb.get("fit") or {}).get("max_dd_pct", b.get("dd")), "tim_champ": (ta.get("fit") or {}).get("tim_pct", a.get("tim")), "tim_cand": (tb.get("fit") or {}).get("tim_pct", b.get("tim")),
                     "cells_champ": _cov(a, "cells_eval"), "cells_cand": _cov(b, "cells_eval"), "naked_champ": _cov(a, "naked_rows"), "naked_cand": _cov(b, "naked_rows"),
                     "skipped_champ": (a.get("coverage") or {}).get("skipped_by_mechanism") if isinstance(a.get("coverage"), dict) else None,
                     "skipped_cand": (b.get("coverage") or {}).get("skipped_by_mechanism") if isinstance(b.get("coverage"), dict) else None})
        # 2026-10-15: a side that fails IDENTICALLY on both trees (both IMPOSSIBLE / both no-progress) is a harness or
        # snapshot problem — it carries no information about the candidate, so it is excluded instead of auto-FAILing
        # every submission. A side missing on ONE tree only still blocks: that is the candidate losing a board.
        if None in (fa, oa) and None in (fb, ob):
            uninformative.append(s)
            continue
        if None in (fa, fb, oa, ob):
            missing.append(s)
            continue
        scored.append(s)
        sf_ch += fa
        sf_ca += fb
        so_ch += oa
        so_ca += ob
        for k in ("cells", "naked"):
            cov[f"{k}_champ"] += rows[-1][f"{k}_champ"]
            cov[f"{k}_cand"] += rows[-1][f"{k}_cand"]
        cov["rows_champ"] += _cov(a, "rows")
        cov["rows_cand"] += _cov(b, "rows")
        if fb < fa - TOL_SIDE:
            drops.append(s)
    _n = max(1, len(scored))
    _sum_floor = round(NOISE_FLOOR_PER_SIDE * (_n ** 0.5), 3)
    # Corner-cut detection (USER 2026-10-16): coverage = what the boards actually evaluated. Unknown coverage on the champion
    # side (pre-coverage results) = ratio None = no verdict either way; a candidate whose coverage is unreadable while the
    # champion's is known counts as a cut (fail-closed).
    cell_ratio = (cov["cells_cand"] / cov["cells_champ"]) if cov["cells_champ"] else None
    naked_ratio = (cov["naked_cand"] / cov["naked_champ"]) if cov["naked_champ"] else None
    corner_cut = any(r is not None and r < COVERAGE_MIN for r in (cell_ratio, naked_ratio))
    reasons = []
    if not scored:
        reasons.append("no side scored on both versions")
    if missing:
        reasons.append(f"candidate lost boards: {missing}")
    if drops:
        reasons.append(f"side fit drop > {TOL_SIDE}: {drops}")
    if scored and n_truth < len(scored) and REQUIRE_TRUTH:
        reasons.append(f"TRUTH_MISSING: chart-truth rescore available on {n_truth}/{len(scored)} scored sides — self-reported gains are never a PASS basis")
    if corner_cut:
        # Fewer evaluations must EARN their place: strictly better on unseen days by more than noise, never worse in fit.
        if not so_ca > so_ch + _sum_floor:
            reasons.append(f"CORNER_CUT (cells x{cell_ratio if cell_ratio is None else round(cell_ratio, 3)}, naked x{naked_ratio if naked_ratio is None else round(naked_ratio, 3)} < {COVERAGE_MIN}) without an OOS win: OOS {so_ch:.3f} -> {so_ca:.3f} needs > +{_sum_floor}")
        if sf_ca < sf_ch:
            reasons.append(f"CORNER_CUT with lower fit gain: {sf_ch:.3f} -> {sf_ca:.3f}")
    else:
        if sf_ca < sf_ch - TOL_SUM:
            reasons.append(f"fit sum {sf_ch:.3f} -> {sf_ca:.3f} below tolerance {TOL_SUM}")
        if so_ca < so_ch - TOL_SUM:
            reasons.append(f"OOS sum {so_ch:.3f} -> {so_ca:.3f} below tolerance {TOL_SUM}")
    ok = not reasons
    wc = sum(r["wall_champ"] or 0 for r in rows)
    wn = sum(r["wall_cand"] or 0 for r in rows)
    return {"PASS": ok, "reasons": reasons, "corner_cut": corner_cut, "truth_sides": n_truth, "scorer": "chart-truth (tools/v15_bench_truth.py, common scorer, window-edge mark counted)" if n_truth else "self-reported final_gain/oos_final", "coverage": {**cov, "cell_ratio": None if cell_ratio is None else round(cell_ratio, 4), "naked_ratio": None if naked_ratio is None else round(naked_ratio, 4), "min": COVERAGE_MIN},
            "scored": scored, "missing": missing, "uninformative": uninformative, "side_drops": drops,
            "noise_floor_per_side": NOISE_FLOOR_PER_SIDE, "noise_floor_sum": _sum_floor,
            "sum_delta": round(sf_ca - sf_ch, 3), "sum_delta_in_noise": bool(abs(sf_ca - sf_ch) <= _sum_floor),
            "oos_delta": round(so_ca - so_ch, 3), "fit_sum_champ": round(sf_ch, 3), "fit_sum_cand": round(sf_ca, 3), "oos_sum_champ": round(so_ch, 3), "oos_sum_cand": round(so_ca, 3), "speedup": round(wc / wn, 3) if wn else None, "rows": rows, "tol_sum": TOL_SUM, "tol_side": TOL_SIDE}


def deploy(cd, meta):
    out = []
    flags_doc = json.dumps({"at": now(), "candidate": meta["id"], "flags": meta.get("flags") or {}}, indent=1)
    (GATE / "flags.json").write_text(flags_doc)
    # The benched launch env is published next to the flags: data/v15_gate_env.json = the env production must run.
    _env = result_env(meta["env"]) if meta.get("env") is not None else champ_env(json.loads((CHAMP / "meta.json").read_text()) if (CHAMP / "meta.json").exists() else {})
    (GATE / "env.json").write_text(json.dumps({"at": now(), "candidate": meta["id"], "env": _env}, indent=1))
    extra = {"data/v15_gate_flags.json": GATE / "flags.json", "data/v15_gate_env.json": GATE / "env.json"}
    for host, root in DEPLOY:
        for rel in list(meta.get("files") or {}) + list(extra):
            src = extra[rel] if rel in extra else (cd / "tree" / rel)
            want = md5(src)
            if host == "local":
                dst = Path(root) / rel
                if not dst.parent.exists():
                    continue
                tmp = dst.with_name(f".{dst.name}.gate.tmp")
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
                got = md5(dst)
            else:
                tmpr = f"{root}/.{Path(rel).name}.gate.tmp"
                subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(src), f"{host}:{tmpr}"], check=False)
                if GATED_DATA_RE.match(rel) and subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"test -d {root}/{Path(rel).parent}"]).returncode != 0:
                    continue
                r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"test -d {root}/{Path(rel).parent} && mv {tmpr} {root}/{rel} && md5sum {root}/{rel} || rm -f {tmpr}"], capture_output=True, text=True)
                got = (r.stdout.split() or [""])[0]
            out.append({"host": host, "root": str(root), "file": rel, "ok": got == want})
    return out


def cmd_publish_env(a):
    """Publish the CHAMPION's benched launch env as data/v15_gate_env.json on every production root (the scheduler appends it
    to each 30D launch, dispatch drops unbenched autotune exports while it exists). Only the champion env — never a candidate's."""
    champ = ensure_champion()
    env = champ_env(champ)
    (GATE / "env.json").write_text(json.dumps({"at": now(), "candidate": f"champion {champ.get('pilot_md5')}", "env": env}, indent=1))
    want = md5(GATE / "env.json")
    out = []
    for host, root in DEPLOY:
        rel = "data/v15_gate_env.json"
        if host == "local":
            dst = Path(root) / rel
            if not dst.parent.exists():
                continue
            tmp = dst.with_name(f".{dst.name}.gate.tmp")
            shutil.copy2(GATE / "env.json", tmp)
            os.replace(tmp, dst)
            got = md5(dst)
        else:
            tmpr = f"{root}/.v15_gate_env.json.gate.tmp"
            subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(GATE / "env.json"), f"{host}:{tmpr}"], check=False)
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"test -d {root}/data && mv {tmpr} {root}/{rel} && md5sum {root}/{rel} || rm -f {tmpr}"], capture_output=True, text=True)
            got = (r.stdout.split() or [""])[0]
        out.append({"host": host, "root": str(root), "ok": got == want})
    with open(DECISIONS, "a") as fh:
        fh.write(json.dumps({"id": f"publish-env-{now()}", "state": "ENV_PUBLISHED", "env": env, "deploy": out}) + "\n")
    print(json.dumps({"env_keys": len(env), "deploy": out}, indent=1))
    return 0 if out and all(x["ok"] for x in out) else 1


def read_meta(cd):
    """A candidate's meta, or None when it is missing/empty/corrupt. 2026-10-15: one 0-byte meta.json (ENOSPC during
    submit) made the listing comprehension raise JSONDecodeError, so EVERY `run` tick died before benching anything —
    the gate was dead from 00:15Z to 02:20Z and the guard kept falling production back to the champion meanwhile.
    An unreadable meta is quarantined as BROKEN (never silently re-run) and the tick continues."""
    p = Path(cd) / "meta.json"
    try:
        return json.loads(p.read_text())
    except Exception as e:
        try:
            write_json_atomic(p, {"id": Path(cd).name, "state": "BROKEN", "error": f"{type(e).__name__}: {e}"[:200], "quarantined": now()})
        except Exception:
            pass
        print(f"[gate] BROKEN candidate {Path(cd).name}: unreadable meta.json ({type(e).__name__}) — quarantined, not benched", flush=True)
        return None


def write_json_atomic(path, obj):
    p = Path(path)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, p)


def requeue_stale(metas):
    """A candidate whose bench died (ENOSPC, OOM, reboot) stays state=running forever and is never retried, because
    only `pending` is picked up. After RUN_TIMEOUT_MIN with no live bench it goes back to pending (max 2 requeues)."""
    for cd, meta in metas:
        if meta.get("state") != "running":
            continue
        try:
            age_min = (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(meta.get("started") or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)).total_seconds() / 60.0
        except Exception:
            age_min = RUN_TIMEOUT_MIN + 1
        if age_min < RUN_TIMEOUT_MIN:
            continue
        if int(meta.get("requeues") or 0) >= 2:
            meta["state"] = "FAIL_STALLED"
            meta["note"] = f"{meta.get('note') or ''} [stalled {age_min:.0f} min, 2 requeues spent]".strip()
        else:
            meta["state"] = "pending"
            meta["requeues"] = int(meta.get("requeues") or 0) + 1
        meta["requeued_at"] = now()
        write_json_atomic(Path(cd) / "meta.json", meta)
        print(f"[gate] candidate {meta.get('id')} stale {age_min:.0f} min -> {meta['state']}", flush=True)


def cmd_run(a):
    GATE.mkdir(parents=True, exist_ok=True)
    champ = ensure_champion()
    metas = [(p, m) for p in sorted(CANDS.glob("*")) if (p / "meta.json").exists() and (m := read_meta(p)) is not None] if CANDS.exists() else []
    requeue_stale(metas)
    pend = sorted([p for p, _m in metas if (read_meta(p) or {}).get("state") == "pending"], key=lambda p: (-int((read_meta(p) or {}).get("priority") or 0), p.name))
    if not pend:
        write_status(champ)
        return 0
    free_mb = shutil.disk_usage(str(BENCH)).free / (1 << 20)
    if free_mb < MIN_FREE_MB:
        print(f"[gate] HOLD: {free_mb:.0f} MB free on {BENCH} < {MIN_FREE_MB:.0f} MB — two bench runs need ~12 GB; benching now would truncate results.json (2026-10-14 ENOSPC)", flush=True)
        write_status(champ)
        return 0
    cd = pend[0]
    meta = read_meta(cd) or {}
    meta["state"] = "running"
    meta["started"] = now()
    write_json_atomic(cd / "meta.json", meta)
    lc, la = f"gate_{meta['id']}_champ", f"gate_{meta['id']}_cand"
    p1 = bench(lc, CHAMP / "tree", champ.get("flags") or {}, champ_env(champ))
    p2 = bench(la, cd / "tree", meta.get("flags") or {}, result_env(meta["env"]) if meta.get("env") is not None else champ_env(champ))
    p1.wait()
    p2.wait()
    return finalize(cd, meta, decide(*truth_rescore(lc, la, results(lc), results(la))))


def finalize(cd, meta, d):
    """The single PASS/deploy/record path, shared by run and redecide so the criterion can never diverge."""
    meta["decision"] = d
    meta["finished"] = now()
    meta["state"] = "PASS" if d["PASS"] else "FAIL"
    if not d.get("scored"):
        # No side produced a result on both versions = the harness failed; never booked as a verdict on the candidate.
        meta["state"] = "HARNESS_FAIL"
    if d["PASS"]:
        meta["deploy"] = deploy(cd, meta)
        if all(x["ok"] for x in meta["deploy"]):
            rsync_tree(cd / "tree", CHAMP / "tree")
            _prev = json.loads((CHAMP / "meta.json").read_text()) if (CHAMP / "meta.json").exists() else {}
            _env = result_env(meta["env"]) if meta.get("env") is not None else champ_env(_prev)
            write_json_atomic(CHAMP / "meta.json", {"at": now(), "from": f"candidate {meta['id']}", "pilot_md5": md5(CHAMP / "tree" / "v15_pilot.py"), "flags": meta.get("flags") or {}, "env": _env, "previous_pilot_md5": _prev.get("pilot_md5"), "decision": {k: v for k, v in d.items() if k != "rows"}})
        else:
            meta["state"] = "PASS_DEPLOY_INCOMPLETE"
    write_json_atomic(cd / "meta.json", meta)
    with open(DECISIONS, "a") as fh:
        fh.write(json.dumps({"id": meta["id"], "state": meta["state"], "note": meta.get("note"), "files": meta.get("files"), "flags": meta.get("flags"), **{k: v for k, v in d.items() if k != "rows"}}) + "\n")
    write_status(json.loads((CHAMP / "meta.json").read_text()))
    print(json.dumps({"id": meta["id"], "state": meta["state"], **{k: v for k, v in d.items() if k != "rows"}}, indent=1))
    return 0


def cmd_redecide(a):
    """Re-score an ALREADY BENCHED candidate from the artifacts its bench left on disk, with the same decide() and the
    same tolerances — no board is recomputed. For the runs whose sides were mis-read as "no progress json" by the
    pre-2026-10-15 bench glob (tools/v15_npz_bench.py find_progress): the boards really ran (rc=0), their progress
    JSONs are on disk, only the scoring step never saw them."""
    cd = CANDS / a.id
    meta = read_meta(cd)
    if meta is None:
        print(f"[gate] no candidate {a.id}", file=sys.stderr)
        return 2
    lc, la = f"gate_{a.id}_champ", f"gate_{a.id}_cand"
    live = subprocess.run(["pgrep", "-f", f"v15_npz_bench.py run .*--label gate_{a.id}_"], capture_output=True, text=True)
    if live.returncode == 0 and live.stdout.strip():
        print(f"[gate] REFUSED: a bench for {a.id} is still running (pids {live.stdout.split()}) — never score a half-written run", file=sys.stderr)
        return 3
    for lb in (lc, la):
        rp = BENCH / "runs" / f"{SNAP}__{lb}" / "results.json"
        if not rp.exists():
            print(f"[gate] REFUSED: {rp} missing — {a.id} was never benched", file=sys.stderr)
            return 3
        r = subprocess.run([sys.executable, str(BENCH / "tools" / "v15_npz_bench.py"), "rescore", "--snap", SNAP, "--label", lb], cwd=str(BENCH), capture_output=True, text=True)
        print(r.stdout.strip(), flush=True)
        if r.returncode != 0:
            print(f"[gate] rescore {lb} failed: {r.stderr[-400:]}", file=sys.stderr)
            return 3
    d = decide(*truth_rescore(lc, la, results(lc), results(la)))
    if a.dry_run:
        print(json.dumps({"id": a.id, "would_be": "PASS" if d["PASS"] else "FAIL", "dry_run": True, **{k: v for k, v in d.items() if k != "rows"}}, indent=1))
        return 0
    return finalize(cd, meta, d)


def write_status(champ):
    L = [f"# V15 version gate — {now()}", "", f"champion: pilot md5 `{champ.get('pilot_md5')}` from {champ.get('from')} at {champ.get('at')}", f"gated switch defaults: `{json.dumps(champ.get('flags') or {})}`", "", "| candidate | state | note | fit Σ champ→cand | OOS Σ champ→cand | speedup | drops/missing |", "|---|---|---|---|---|---|---|"]
    if DECISIONS.exists():
        for line in DECISIONS.read_text().splitlines()[-20:]:
            r = json.loads(line)
            L.append(f"| {r.get('id','?')} | {r.get('state','?')} | {r.get('note','')[:40]} | {r.get('fit_sum_champ')}→{r.get('fit_sum_cand')} | {r.get('oos_sum_champ')}→{r.get('oos_sum_cand')} | {r.get('speedup')} | {r.get('side_drops')}/{r.get('missing')} |")
    pend = [p.name for p in CANDS.glob("*") if (p / "meta.json").exists() and (read_meta(p) or {}).get("state") in ("pending", "running")] if CANDS.exists() else []
    L += ["", f"pending/running: {pend}"]
    # Side-by-side of the last benched candidate vs the champion it was measured against (USER 2026-10-16).
    last = None
    for p in sorted(CANDS.glob("*")) if CANDS.exists() else []:
        m = read_meta(p) or {}
        if isinstance(m.get("decision"), dict) and m["decision"].get("rows"):
            last = m
    if last:
        d = last["decision"]
        L += ["", f"## Side by side — {last['id']} ({last.get('state')}) vs champion", f"reasons: {d.get('reasons') or '-'}", f"coverage: {d.get('coverage')}", f"env changes: {json.dumps(last.get('env_changes') or {})[:600]}", "",
              "| side | fit champ→cand | OOS champ→cand | trades | DD % | TIM % | cells eval | naked rows | wall s |", "|---|---|---|---|---|---|---|---|---|"]
        for r in d["rows"]:
            L.append(f"| {r['side']} | {r.get('fit_champ')}→{r.get('fit_cand')} | {r.get('oos_champ')}→{r.get('oos_cand')} | {r.get('trades_champ')}→{r.get('trades_cand')} | {r.get('dd_champ')}→{r.get('dd_cand')} | {r.get('tim_champ')}→{r.get('tim_cand')} | {r.get('cells_champ')}→{r.get('cells_cand')} | {r.get('naked_champ')}→{r.get('naked_cand')} | {r.get('wall_champ')}→{r.get('wall_cand')} |")
    for alert in ("ALERT_UNGATED_PILOT.json", "ALERT_UNGATED_ENV.json"):
        if (GATE / alert).exists():
            L += ["", f"**{alert}**: {(GATE / alert).read_text()[:800]}"]
    STATUS.write_text("\n".join(L) + "\n")


def cmd_status(a):
    champ = ensure_champion()
    write_status(champ)
    print(STATUS.read_text())
    return 0


def cmd_watch(a):
    """Alert (never revert) when a production pilot differs from the champion without a PASS."""
    champ = ensure_champion()
    want = champ.get("pilot_md5")
    alerts = []
    for host, root in DEPLOY:
        if host == "local":
            p = Path(root) / "v15_pilot.py"
            got = md5(p) if p.exists() else None
        else:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, f"md5sum {root}/v15_pilot.py"], capture_output=True, text=True)
            got = (r.stdout.split() or [None])[0]
        if got and got != want:
            alerts.append({"host": host, "root": str(root), "md5": got, "champion": want})
    if alerts:
        (GATE / "ALERT_UNGATED_PILOT.json").write_text(json.dumps({"at": now(), "alerts": alerts, "fix": "submit the change: python3 ~/v15_bench/tools/v15_version_gate.py submit --files <path>=v15_pilot.py"}, indent=1))
        print(json.dumps(alerts, indent=1))
    else:
        (GATE / "ALERT_UNGATED_PILOT.json").unlink(missing_ok=True)
    # 2026-10-16: the launch env is part of the version. Scheduler pace overrides (INERT 1/100, V17 forced on, FOCUS cuts)
    # and the daily V17 autotune changed what every board computes without ever being benched.
    want_env = champ_env(champ)
    drift = []
    for host in sorted({h for h, _r in DEPLOY}):
        for e in running_pilot_envs(host):
            d = env_diff(want_env, e["env"])
            if d:
                drift.append({"host": host, "pid": e["pid"], "diff": d})
    if drift:
        keys = sorted({k for x in drift for k in x["diff"]})
        (GATE / "ALERT_UNGATED_ENV.json").write_text(json.dumps({"at": now(), "pilots": len(drift), "keys": keys, "sample": drift[:3], "fix": "bench the running env: python3 ~/v15_bench/tools/v15_version_gate.py submit --env-from-production 10.0.0.3 --note '...'"}, indent=1))
        print(f"[gate] ENV DRIFT: {len(drift)} production pilots run a launch env the gate never benched: {keys}", flush=True)
    else:
        (GATE / "ALERT_UNGATED_ENV.json").unlink(missing_ok=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--files", nargs="*", help="SRC[=REL] (REL defaults to SRC's path relative to ~/binance-sandbox)")
    s.add_argument("--set", action="append", help="gated switch default K=V (e.g. V15_BRAIN=1)")
    s.add_argument("--note")
    s.add_argument("--env", action="append", help="launch-env change K=V benched against the champion env (K= removes K)")
    s.add_argument("--env-from-production", metavar="HOST", help="candidate env = the env a running production pilot on HOST (local or 10.0.0.3) really has")
    s.add_argument("--allow-identical", action="store_true", help="champion-vs-champion run (noise calibration)")
    s.add_argument("--priority", type=int, default=0, help="higher benches first (user-ordered comparisons)")
    sub.add_parser("run")
    rd = sub.add_parser("redecide")
    rd.add_argument("--id", required=True, help="candidate id whose bench artifacts are already on disk")
    rd.add_argument("--dry-run", action="store_true", help="print the verdict without deploying or recording it")
    sub.add_parser("status")
    sub.add_parser("watch")
    sub.add_parser("publish-env")
    a = ap.parse_args(argv)
    return {"submit": cmd_submit, "run": cmd_run, "redecide": cmd_redecide, "status": cmd_status, "watch": cmd_watch, "publish-env": cmd_publish_env}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
