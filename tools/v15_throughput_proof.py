#!/usr/bin/env python3
"""V15 THROUGHPUT PROOF — isolated N-arm A/B of v15_pilot code trees on ONE sym_side, ONE host, ONE NPZ.

USER 2026-10-15 ("one improved sym_side every 5 minutes per server"): every pilot speed change must be
proven to be a PURE I/O / scheduling optimisation — same promoted set, same final gain, less wall time.
This harness runs the SAME sym_side once per arm, by default CONCURRENTLY so every arm sees identical
host load, each in its own materialised root (code tree copy + the SAME frozen NPZ bytes + templates +
data), and reports per arm:

  wall_s, result_cpu_s, wchar_bytes, write_bytes, peak_pss_kb, rows_done, final_gain,
  md5(cumulative_overrides), trades, max_dd_pct, tim_pct, pool_sharpe, quality-gate verdict.

THE BAR (NO-LIES, BACKTEST_BIBLE §108): md5(cumulative_overrides) identical to arm 1 AND final_gain
identical. A pure I/O change that moves either one is WRONG and this tool says FAIL — it never
"accepts a small difference". Nothing is published: V15_PROGRESS_DIR / V15_OUT_DIR / V15_ISOLATED_PROOF
/ V15_EVAL_CACHE_DB point every write into the arm dir, and the eval cache starts empty per arm so no
arm warms another.

  run     --sym-side SYM_SIDE --arm champion=DIR --arm candidate=DIR [--arm-set LABEL:K=V] ...
  report  --run RUNDIR

Stdlib only (+ rsync). Runs on a worker with the NPZs + .venv (s1 / s2). Never on the Mac (no NPZ set).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HOME = Path.home()
SANDBOX = Path(os.environ.get("V15_PROOF_SRC", str(HOME / "binance-sandbox")))
DEFAULT_ROOT = Path(os.environ.get("V15_PROOF_DIR", str(HOME / "v15_throughput_proof")))
CLK = os.sysconf("SC_CLK_TCK")
SAMPLE_S = 2.0
# a materialised root carries the CODE only; the heavy inputs are wired in explicitly so both arms
# provably read the same bytes (a symlinked tree would resolve ROOT back to the original and read live data)
CODE_EXCLUDE = ["data", "backtest_v8", "backtest_v7", "SPREADSHEETS", "klines_cache", "klines_cache_macbook",
                "klines_vault", "klines_canonical", "archive", "backups", ".venv", "backtest_framework",
                ".git", "logs", "from_scratch_archive_*", "*.log", "*.db", "*.sqlite", "*.npz", "*.xlsx",
                "__pycache__", ".history", ".elastic"]
# Production launch env (S1/S2 crontab 2026-10-15) minus the per-run isolation keys the harness owns.
PROD_ENV = {
    "V15_FRESH_RUN": "1",
    "V15_TEMPLATE_DEFAULTS": "1",
    "V15_ADAPT_BASELINE": "0",
    "V15_SKIP_LIVE_AT_DONE": "1",
    "V15_POSSYM_SAMPLING": "0",
    "V15_INERT_SAMPLE_EVERY": "100",
    "V15_POSSYM_MIN_N": "1",
    "V15_DIAG_REPAIR_S": "150",
    "V15_ENDGAME_S": "120",
    "V15_FOCUS_S_WINNER": "600",
    "V15_FOCUS_S_MEDIOCRE": "150",
    "V15_FOCUS_S_LOSER": "60",
    "V15_TIER_BIG_MOVER": "8",
    "V15_FOCUS_S_BHCHASE": "600",
    "V15_FOCUS_TOP_K_BHCHASE": "48",
    "V15_POOL_BATCH_N": "8",
    "V15_UNWIRED_SKIP": "0",
    "V12_NPZ_CACHE": "8",
    "V12_ALLOW_STALE_NPZ": "1",
    "V15_V17_ROUNDS": "1",
    "V15_V17_COMBO_SHARE": "0.35",
    "V15_V17_COMBO_SHARE_WINNER": "0.4",
    "V15_V17_P3_MAX_S_LOSER": "300.0",
    "V15_V17_P3_MAX_S_LOST_CAUSE": "120.0",
    "V15_V17_P3_MAX_S_MEDIOCRE": "648.0",
    "V15_V17_P3_MAX_S_WINNER": "1500.0",
    "V15_V17_P_MIN": "0.006",
    "V15_V17_SHARE_ABLATION": "0.48",
    "V15_V17_SHARE_FOCUS": "0.6",
    "V15_V17_SHARE_RETEST": "0.84",
    "V15_V17_SHARE_SURGEON": "0.6",
    "V15_V17_SHARE_SURGEON_WINNER": "0.9",
    # harness-owned: one process computes ONE side end to end, no slice exit, no partner side
    "V15_FILL_SLICE_S": "0",
    "V15_PAIR_MODE": "0",
    # 1d/7d are script-testing windows only (USER 2026-10-15) — a proof arm never spends CPU on them
    "V15_HOLDOUT_DAYS": "0",
    "V15_FORWARD_SCORE": "0",
}
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
QUAL_MIN_TRADES = 10
QUAL_DD_MAX = 30.0
QUAL_TIM_LO, QUAL_TIM_HI = 20.0, 80.0


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def md5_file(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def md5_overrides(ov):
    """Canonical md5 of a promoted-override set: sorted keys, stringified values, no float drift."""
    if not isinstance(ov, dict):
        return ""
    canon = json.dumps({str(k): str(v) for k, v in sorted(ov.items())}, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canon.encode()).hexdigest()


def sym_of(symside):
    s = str(symside).upper()
    return s[:-5] if s.endswith("_LONG") else s[:-6] if s.endswith("_SHORT") else s


def is_crypto(symside):
    return sym_of(symside).endswith(CRYPTO_SUFFIX)


def cat_side(symside):
    s = str(symside).upper()
    return ("CRYPTO" if is_crypto(s) else "STOCKS", "LONG" if s.endswith("_LONG") else "SHORT")


def read_proc(pid, name):
    try:
        with open(f"/proc/{pid}/{name}") as fh:
            return fh.read()
    except Exception:
        return ""


def tree_pids(root_pid):
    """Every live descendant of root_pid, root included (one pass over /proc, no pgrep)."""
    kids = {}
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        st = read_proc(e, "stat")
        if not st:
            continue
        tail = st[st.rfind(")") + 2:].split()
        if len(tail) < 2:
            continue
        kids.setdefault(int(tail[1]), []).append(int(e))
    out, stack = [], [int(root_pid)]
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(kids.get(p, []))
    return out


def sample_tree(root_pid):
    """cpu_s, wchar, write_bytes, pss_kb summed over the live process tree."""
    cpu = wchar = wb = pss = 0
    n = 0
    for pid in tree_pids(root_pid):
        st = read_proc(pid, "stat")
        if not st:
            continue
        tail = st[st.rfind(")") + 2:].split()
        if len(tail) < 13:
            continue
        cpu += (int(tail[11]) + int(tail[12])) / CLK
        n += 1
        for line in read_proc(pid, "io").splitlines():
            if line.startswith("wchar: "):
                wchar += int(line.split()[1])
            elif line.startswith("write_bytes: "):
                wb += int(line.split()[1])
        m = re.search(r"^Pss:\s+(\d+) kB", read_proc(pid, "smaps_rollup"), re.M)
        if m:
            pss += int(m.group(1))
    return {"cpu_s": cpu, "wchar": wchar, "write_bytes": wb, "pss_kb": pss, "procs": n}


class ArmSampler(threading.Thread):
    """/proc counters are monotonic per process, so the LAST live sample of the tree is the arm's total
    (the pool is forked once and lives to the end). PSS is tracked as a peak."""

    def __init__(self, pid):
        super().__init__(daemon=True)
        self.pid = pid
        self.stop = threading.Event()
        self.last = {}
        self.peak = {"cpu_s": 0.0, "wchar": 0, "write_bytes": 0, "pss_kb": 0, "procs": 0}

    def run(self):
        while not self.stop.is_set():
            try:
                s = sample_tree(self.pid)
            except Exception:
                s = {}
            if s.get("procs"):
                self.last = s
                for k in self.peak:
                    if s.get(k, 0) > self.peak[k]:
                        self.peak[k] = s[k]
            self.stop.wait(SAMPLE_S)


def build_root(arm_dir, code_dir, symside, npz_src, data_src, tpl_src, extra_syms):
    """Materialise an arm root: code copy + the SAME frozen NPZ bytes + templates + data. Returns (root, npz_md5)."""
    root = arm_dir / "root"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    ex = []
    for e in CODE_EXCLUDE:
        ex += ["--exclude", e]
    subprocess.run(["rsync", "-a"] + ex + [f"{Path(code_dir).resolve()}/", f"{root}/"], check=True)
    venv = SANDBOX / ".venv"
    if venv.exists():
        os.symlink(venv, root / ".venv")
    if data_src and Path(data_src).exists():
        subprocess.run(["cp", "-a", str(data_src), str(root / "data")], check=True)
        for junk in ("eval_cache_v1.db", "causal_learner.db"):
            p = root / "data" / junk
            if p.exists():
                p.unlink()
    ind = root / "backtest_v8" / "indicators"
    ind.mkdir(parents=True, exist_ok=True)
    now = time.time()
    md5s = {}
    for sym in [sym_of(symside)] + list(extra_syms or []):
        src = Path(npz_src) / f"{sym}.npz"
        if not src.exists():
            raise SystemExit(f"REFUSED: no NPZ {src} — a proof arm never runs on a missing/other symbol's NPZ")
        shutil.copy2(src, ind / src.name)
        os.utime(ind / src.name, (now, now))  # age gates read mtime; the bytes stay the frozen ones
        md5s[sym] = md5_file(ind / src.name)
    cat, side = cat_side(symside)
    tdir = root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"
    tdir.mkdir(parents=True, exist_ok=True)
    found = False
    for f in sorted(Path(tpl_src).glob("TEMPLATE_*.xlsx")):
        shutil.copy2(f, tdir / f.name)
        shutil.copy2(f, root / "SPREADSHEETS" / f.name)
        if f.name == f"TEMPLATE_{cat}_{side}.xlsx":
            found = True
    if not found:
        raise SystemExit(f"REFUSED: no TEMPLATE_{cat}_{side}.xlsx in {tpl_src} — never substitute another side's template")
    return root, md5s


def build_env(root, arm_dir, workers, overrides):
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("V15_", "V12_")):
            env.pop(k, None)
    env.update(PROD_ENV)
    env["V15_PROGRESS_DIR"] = str(arm_dir / "progress")
    env["V15_OUT_DIR"] = str(arm_dir / "out")
    env["V15_ISOLATED_PROOF"] = "1"
    env["V15_EVAL_CACHE_DB"] = str(arm_dir / "eval_cache.db")
    env["V15_V17_CARRY_DIR"] = str(arm_dir / "carry")
    env["V15_SCHED_30D_ONLY"] = "1"
    env["V15_RESULT_WORKERS"] = str(workers)
    env["PYTHONPATH"] = str(root)
    env.update(overrides)
    return env


def prepare_arm(label, code_dir, symside, args, root_dir, overrides):
    """Materialise the arm root BEFORE any arm starts, so a concurrent proof starts every arm at once."""
    arm = root_dir / f"arm_{label}"
    for sub in ("progress", "out", "carry"):
        (arm / sub).mkdir(parents=True, exist_ok=True)
    root, npz_md5 = build_root(arm, code_dir, symside, args.npz_src, args.data_src, args.tpl_src, args.extra_npz)
    return {"label": label, "code_dir": code_dir, "arm": arm, "root": root, "npz_md5": npz_md5, "overrides": overrides}


def launch_arm(prep, symside, args):
    label, arm, root, npz_md5, overrides = prep["label"], prep["arm"], prep["root"], prep["npz_md5"], prep["overrides"]
    code_dir = prep["code_dir"]
    pilot = root / "v15_pilot.py"
    if not pilot.exists():
        raise SystemExit(f"REFUSED: no v15_pilot.py in {code_dir}")
    py = root / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(os.environ.get("V15_PROOF_PYTHON", str(SANDBOX / ".venv" / "bin" / "python")))
    if not py.exists():
        py = Path(sys.executable)
    cat, side = cat_side(symside)
    cmd = [str(py), "-u", str(pilot), "--sym-side", symside,
           "--template", str(root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{cat}_{side}.xlsx"),
           "--seq-mode", "worst2best", "--window-days", str(args.window_days),
           "--vector-only", "--workers", str(args.workers)]
    if args.nice:
        cmd = ["nice", "-n", str(args.nice)] + cmd
    log = open(arm / "pilot.log", "w")
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(root), env=build_env(root, arm, args.workers, overrides),
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    sampler = ArmSampler(proc.pid)
    sampler.start()
    meta = {"label": label, "code_dir": str(Path(code_dir).resolve()), "pilot_md5": md5_file(pilot),
            "npz_md5": npz_md5, "cmd": cmd, "sym_side": symside, "workers": args.workers,
            "window_days": args.window_days, "started_utc": utcnow(), "pid": proc.pid,
            "env_overrides": overrides}
    (arm / "launch.json").write_text(json.dumps(meta, indent=1))
    return {"label": label, "arm": arm, "proc": proc, "sampler": sampler, "t0": t0, "log": log, "meta": meta}


def harvest(run, symside):
    arm, sampler = run["arm"], run["sampler"]
    sampler.stop.set()
    sampler.join(timeout=5)
    try:
        run["log"].close()
    except Exception:
        pass
    prog = arm / "progress" / f"{symside}_v14_progress.json"
    j = {}
    if prog.exists():
        try:
            j = json.loads(prog.read_text())
        except Exception as e:
            j = {"_read_error": str(e)}
    ov = j.get("cumulative_overrides") or {}
    final = j.get("final_gain")
    if final is None:
        final = j.get("cumulative_gain")
    # The published manifest is where the pilot records the board's OWN metrics and its OWN hash of the
    # published set (metrics.tim_pct / max_dd_pct, overrides_md5). The progress JSON carries neither, so a
    # harvest that only read the progress JSON reported dd/tim as None and the quality gates read FAIL by
    # omission — never let a missing field look like a failed gate.
    man = {}
    try:
        mans = sorted((arm / "out").glob("*_manifest.json"), key=lambda p: p.stat().st_mtime)
        if mans:
            man = json.loads(mans[-1].read_text())
    except Exception:
        man = {}
    metrics = man.get("metrics") or j.get("final_metrics") or j.get("final") or {}
    pilot_set_hash = man.get("overrides_md5") or ((j.get("provenance") or {}).get("published_set_hash"))

    def pick(*names):
        for n in names:
            for src in (metrics, j):
                if isinstance(src, dict) and src.get(n) is not None:
                    return src[n]
        return None

    trades = pick("trades", "final_trades")
    dd = pick("max_dd_pct", "final_max_dd_pct", "dd")
    tim = pick("tim_pct", "final_tim", "tim")
    sharpe = pick("pool_sharpe", "final_pool_sharpe")
    best_start = j.get("initial_baseline_gain", j.get("baseline_gain"))
    gates = {
        "trades>=10": trades is not None and float(trades) >= QUAL_MIN_TRADES,
        "dd<=30": dd is not None and float(dd) <= QUAL_DD_MAX,
        "tim 20-80": tim is not None and QUAL_TIM_LO <= float(tim) <= QUAL_TIM_HI,
        "gain>0": final is not None and float(final) > 0,
        "final>=best_start": final is not None and best_start is not None and float(final) >= float(best_start) - 1e-9,
    }
    last = sampler.last or {}
    done = j.get("done") or {}
    return {
        "label": run["label"], "pilot_md5": run["meta"]["pilot_md5"], "code_dir": run["meta"]["code_dir"],
        "npz_md5": run["meta"]["npz_md5"], "env_overrides": run["meta"]["env_overrides"],
        "rc": run["proc"].returncode, "wall_s": round(run["t1"] - run["t0"], 1),
        "result_cpu_s": j.get("result_cpu_s"), "sampled_cpu_s": round(last.get("cpu_s", 0.0), 1),
        # /proc/<pid>/io vanishes with the process, so the LAST live sample can read 0 if it landed after the
        # tree exited (measured 2026-10-15: both arms reported wchar 0). The running peak is the honest floor.
        "wchar_bytes": max(int(last.get("wchar") or 0), int(sampler.peak.get("wchar") or 0)) or None,
        "write_bytes": max(int(last.get("write_bytes") or 0), int(sampler.peak.get("write_bytes") or 0)) or None,
        "peak_pss_kb": sampler.peak.get("pss_kb"), "peak_procs": sampler.peak.get("procs"),
        "rows_done": len(done),
        "rows_promoted": sum(1 for v in done.values() if isinstance(v, dict) and v.get("promoted")),
        "final_gain": final, "best_start": best_start,
        "overrides_md5": md5_overrides(ov), "overrides_n": len(ov),
        "pilot_set_hash": pilot_set_hash, "published_name": man.get("final_name"),
        "trades": trades, "max_dd_pct": dd, "tim_pct": tim, "pool_sharpe": sharpe,
        "result_start_utc": j.get("result_start_utc"), "result_done_utc": j.get("result_done_utc"),
        "gates": gates, "gate_fails": [k for k, v in gates.items() if not v],
        "progress_json": str(prog), "progress_json_bytes": prog.stat().st_size if prog.exists() else 0,
        "log": str(arm / "pilot.log"),
    }


ROWS = ["pilot_md5", "wall_s", "result_cpu_s", "sampled_cpu_s", "wchar_bytes", "write_bytes",
        "peak_pss_kb", "peak_procs", "rows_done", "rows_promoted", "final_gain", "best_start",
        "overrides_n", "overrides_md5", "pilot_set_hash", "trades", "max_dd_pct", "tim_pct",
        "pool_sharpe", "progress_json_bytes", "rc"]


def fmt_table(arms):
    def cell(v):
        if isinstance(v, float):
            return f"{v:.4f}" if abs(v) < 1e6 else f"{v:.0f}"
        if v is None:
            return "None"
        s = str(v)
        return s[:10] + ".." if len(s) > 12 else s

    w = max(len(r) for r in ROWS) + 1
    cw = 14
    out = [" " * w + " " + " ".join(a["label"][:cw].ljust(cw) for a in arms)]
    out.append("-" * (w + 1 + (cw + 1) * len(arms)))
    for r in ROWS:
        out.append(r.ljust(w) + " " + " ".join(cell(a.get(r)).ljust(cw) for a in arms))
    ref = arms[0]
    if ref.get("wall_s"):
        out.append("")
        for a in arms[1:]:
            d = a["wall_s"] - ref["wall_s"]
            out.append(f"vs {ref['label']}: {a['label']} wall {d:+.0f}s ({100.0 * d / ref['wall_s']:+.1f}%)"
                       + (f", write_bytes {100.0 * ((a.get('write_bytes') or 0) - (ref.get('write_bytes') or 0)) / (ref.get('write_bytes') or 1):+.1f}%" if ref.get("write_bytes") else ""))
    return "\n".join(out)


SIGNAL_RCS = {-15, -9, 143, 137}


def control_groups(arms):
    """Arms that are the SAME code with the SAME env are a control group: anything they differ by is the board's
    own run-to-run noise, not an effect of a change. Keyed on (pilot_md5, env)."""
    g = {}
    for a in arms:
        k = (a.get("pilot_md5"), json.dumps(a.get("env_overrides") or {}, sort_keys=True))
        g.setdefault(k, []).append(a)
    return [v for v in g.values() if len(v) > 1]


def noise_floor(arms):
    """-> (floor_gain, floor_rows, same_md5, note). None floor = no control arm, so nothing can be attributed.

    MEASURED 2026-10-15 (s2, DOTUSDT_LONG, champion tree 1aef0e69 twice, same frozen NPZ, workers 16):
    champA 3374 rows / 8 promotions / gain 8.4366 / set 89b899837d, champB 3376 / 10 / 8.9128 / b04b8e6c41.
    IDENTICAL CODE, DIFFERENT RESULT — the board's late phases are budgeted in wall-clock seconds
    (V15_V17_P3_MAX_S_*, V15_FOCUS_S_*, V15_ENDGAME_S), so what gets explored depends on timing. A
    byte-identical promoted set is therefore NOT achievable by any change, including a no-op, and a
    single-run difference can never be attributed to the change under test."""
    floors = []
    for grp in control_groups(arms):
        gains = [float(a["final_gain"]) for a in grp if a.get("final_gain") is not None]
        rows = [int(a["rows_done"] or 0) for a in grp]
        md5s = {a.get("overrides_md5") for a in grp}
        floors.append((max(gains) - min(gains) if len(gains) > 1 else 0.0,
                       max(rows) - min(rows) if len(rows) > 1 else 0,
                       len(md5s) == 1,
                       f"{grp[0]['label']}+{grp[1]['label']} on pilot {str(grp[0].get('pilot_md5'))[:10]}"))
    if not floors:
        return None, None, None, ""
    fg = max(f[0] for f in floors)
    fr = max(f[1] for f in floors)
    same = all(f[2] for f in floors)
    return fg, fr, same, "; ".join(f[3] for f in floors)


def verdict(arms):
    ref = arms[0]
    lines, ok = [], True
    truncated = [a["label"] for a in arms if a.get("rc") in SIGNAL_RCS]
    floor_gain, floor_rows, ctrl_same, ctrl_note = noise_floor(arms)
    if floor_gain is None:
        lines.append("NO CONTROL ARM: this run has no two arms with the same pilot_md5 and env, so there is no")
        lines.append("  measured noise floor and a difference CANNOT be attributed to the change under test.")
        lines.append("  Measured 2026-10-15: the champion tree run twice on the same NPZ gave 8.4366 vs 8.9128")
        lines.append("  and different promoted sets, so add a duplicate arm before judging anything.")
    elif not ctrl_same:
        lines.append(f"BOARD IS NONDETERMINISTIC (control {ctrl_note}): same code + same inputs produced DIFFERENT")
        lines.append(f"  promoted sets. Measured noise floor: |d_gain| <= {floor_gain:.4f}, |d_rows| <= {floor_rows}. A byte-identical")
        lines.append("  promoted set is not achievable by any change, including a no-op — the late phases are")
        lines.append("  wall-clock budgeted, so timing decides what gets explored. Judge below against this floor.")
    else:
        lines.append(f"control {ctrl_note}: same code reproduced the same promoted set (floor |d_gain| <= {floor_gain:.4f})")
    if truncated:
        # measured 2026-10-15: the fleet reaper SIGTERMed two arms of a DOTUSDT_LONG A/B as "orphaned workers
        # (parent gone)" because it grouped every pilot of a sym_side together. A signalled arm's wall time is a
        # lower bound and its late phases are cut, so no wall-time claim may be made from this run.
        lines.append(f"TRUNCATED ARMS (killed by a signal, rc in {sorted(SIGNAL_RCS)}): {truncated} — wall times are")
        lines.append("  lower bounds and late phases are cut. No speedup/slowdown may be reported from this run.")
    for a in arms:
        lines.append(f"quality gates {a['label']}: " + ("PASS" if not a["gate_fails"] else "FAIL " + ",".join(a["gate_fails"])))
    for a in arms[1:]:
        same_set = bool(ref["overrides_md5"]) and ref["overrides_md5"] == a["overrides_md5"]
        if ref.get("pilot_set_hash") and a.get("pilot_set_hash"):
            same_set = same_set and ref["pilot_set_hash"] == a["pilot_set_hash"]
        g_ref, g_arm = ref.get("final_gain"), a.get("final_gain")
        same_gain = g_ref is not None and g_arm is not None and abs(float(g_ref) - float(g_arm)) <= 1e-9
        lines.append(f"{a['label']} vs {ref['label']}: promoted-set md5 {'SAME' if same_set else 'DIFFER'}, "
                     f"final_gain {'SAME' if same_gain else 'DIFFER'}")
        if not (same_set and same_gain):
            if a["label"] in truncated or ref["label"] in truncated:
                ok = False
                lines.append(f"  -> INCONCLUSIVE: one of the two arms was truncated by a signal, so a different")
                lines.append("     promoted set is expected (the wall-clock-budgeted late phases ran for different")
                lines.append("     lengths). Re-run with no external killer before judging the candidate.")
            elif floor_gain is None:
                ok = False
                lines.append("  -> UNATTRIBUTABLE: results differ, but with no control arm this run cannot say")
                lines.append("     whether the change or the board's own nondeterminism caused it. Add a duplicate arm.")
            elif (a.get("final_gain") is not None and ref.get("final_gain") is not None
                  and abs(float(a["final_gain"]) - float(ref["final_gain"])) <= floor_gain
                  and abs(int(a["rows_done"] or 0) - int(ref["rows_done"] or 0)) <= max(floor_rows, 0)):
                lines.append(f"  -> WITHIN NOISE: d_gain {float(a['final_gain']) - float(ref['final_gain']):+.4f} and "
                             f"d_rows {int(a['rows_done'] or 0) - int(ref['rows_done'] or 0):+d} are inside the measured")
                lines.append(f"     control floor (|d_gain| <= {floor_gain:.4f}, |d_rows| <= {floor_rows}). The change is NOT shown to alter")
                lines.append("     what the board computes — and is NOT shown to preserve it either. Repeat both arms.")
            else:
                ok = False
                lines.append(f"  -> EXCEEDS NOISE FLOOR: d_gain {float(a['final_gain']) - float(ref['final_gain']):+.4f} vs "
                             f"floor {floor_gain:.4f}. {a['label']} changes what the board computes. Do not ship it.")
        elif truncated:
            lines.append(f"  -> RESULT-IDENTICAL up to the truncation point, wall comparison WITHHELD "
                         f"({ref['wall_s']:.0f}s vs {a['wall_s']:.0f}s are both lower bounds).")
        elif a["wall_s"] >= ref["wall_s"]:
            lines.append(f"  -> NEUTRAL: identical results, no wall-time win ({ref['wall_s']:.0f}s -> {a['wall_s']:.0f}s).")
            lines.append("     Report it as a no-op, never as a speedup.")
        else:
            lines.append(f"  -> PASS: identical results, wall {ref['wall_s']:.0f}s -> {a['wall_s']:.0f}s.")
    return "\n".join(lines), ok


def cmd_run(args):
    symside = args.sym_side.upper()
    arms_spec = []
    for a in args.arm:
        label, _, code = a.partition("=")
        if not label or not code:
            raise SystemExit(f"REFUSED: --arm wants LABEL=CODE_DIR, got {a!r}")
        arms_spec.append((label, code))
    if len(arms_spec) < 2:
        raise SystemExit("REFUSED: a proof needs at least 2 arms")
    per_arm = {label: {} for label, _ in arms_spec}
    common = {}
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        if not _:
            raise SystemExit(f"REFUSED: --set wants K=V, got {kv!r}")
        common[k] = v
    for spec in args.arm_set or []:
        label, _, kv = spec.partition(":")
        k, _2, v = kv.partition("=")
        if label not in per_arm or not _ or not _2:
            raise SystemExit(f"REFUSED: --arm-set wants LABEL:K=V with a known LABEL, got {spec!r}")
        per_arm[label][k] = v
    root_dir = Path(args.root) if args.root else DEFAULT_ROOT / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{symside}"
    root_dir.mkdir(parents=True, exist_ok=True)
    if int(args.workers) < 16:
        print(f"[proof] WARNING: --workers {args.workers} < 16. The production contract is 16 "
              f"(USER: never reduce worker counts); a proof below it does not transfer.", flush=True)
    print(f"[proof] {symside} root={root_dir} arms={[l for l, _ in arms_spec]} workers={args.workers} "
          f"window={args.window_days} mode={'sequential' if args.sequential else 'concurrent'}", flush=True)
    runs = []
    preps = []
    for label, code in arms_spec:
        ov = dict(common)
        ov.update(per_arm[label])
        t0 = time.time()
        preps.append(prepare_arm(label, code, symside, args, root_dir, ov))
        print(f"[proof] arm {label} root materialised in {time.time() - t0:.0f}s npz={preps[-1]['npz_md5']} env={ov or '-'}", flush=True)
    if args.sequential:
        for prep in preps:
            r = launch_arm(prep, symside, args)
            print(f"[proof] arm {r['label']} pid={r['proc'].pid} md5={r['meta']['pilot_md5']}", flush=True)
            r["proc"].wait()
            r["t1"] = time.time()
            print(f"[proof] arm {r['label']} rc={r['proc'].returncode} wall={r['t1'] - r['t0']:.0f}s", flush=True)
            runs.append(r)
    else:
        for prep in preps:
            r = launch_arm(prep, symside, args)
            print(f"[proof] arm {r['label']} pid={r['proc'].pid} md5={r['meta']['pilot_md5']}", flush=True)
            runs.append(r)
        deadline = time.time() + args.timeout_s
        pending = list(runs)
        while pending and time.time() < deadline:
            time.sleep(5)
            for r in list(pending):
                if r["proc"].poll() is not None:
                    r["t1"] = time.time()
                    print(f"[proof] arm {r['label']} rc={r['proc'].returncode} wall={r['t1'] - r['t0']:.0f}s", flush=True)
                    pending.remove(r)
        for r in pending:
            print(f"[proof] arm {r['label']} TIMEOUT after {args.timeout_s}s — terminating tree", flush=True)
            try:
                os.killpg(os.getpgid(r["proc"].pid), signal.SIGTERM)
            except Exception:
                pass
            try:
                r["proc"].wait(timeout=120)
            except Exception:
                pass
            r["t1"] = time.time()
    res = [harvest(r, symside) for r in runs]
    npz_sets = {json.dumps(a["npz_md5"], sort_keys=True) for a in res}
    table = fmt_table(res)
    v, ok = verdict(res)
    if len(npz_sets) != 1:
        v = "REFUSED VERDICT: arms did not read identical NPZ bytes — " + str(npz_sets) + "\n" + v
        ok = False
    out = {"at": utcnow(), "host": os.uname().nodename, "sym_side": symside, "root": str(root_dir),
           "workers": args.workers, "window_days": args.window_days,
           "mode": "sequential" if args.sequential else "concurrent",
           "common_env": common, "arms": res, "verdict": v, "pass": ok}
    (root_dir / "proof.json").write_text(json.dumps(out, indent=1, default=str))
    (root_dir / "proof.txt").write_text(table + "\n\n" + v + "\n")
    print("\n" + table + "\n\n" + v, flush=True)
    print(f"\n[proof] wrote {root_dir / 'proof.json'} and {root_dir / 'proof.txt'}", flush=True)
    return 0 if ok else 1


def cmd_report(args):
    p = Path(args.run)
    f = p / "proof.json" if p.is_dir() else p
    d = json.loads(f.read_text())
    print(fmt_table(d["arms"]))
    print()
    # RECOMPUTE, never replay: a verdict stored by an older harness can be wrong (the 2026-10-15 run was stored
    # with "FAIL: changes what the board computes" before the control arm proved the board is nondeterministic).
    v, ok = verdict(d["arms"])
    print(v)
    stored = (d.get("verdict") or "").strip()
    if stored and stored != v.strip():
        print("\n[note] the verdict stored at run time differed and has been superseded by this recomputation:")
        print("  " + stored.replace("\n", "\n  "))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Isolated N-arm throughput proof for v15_pilot code trees")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run every arm on one sym_side and emit the proof table")
    r.add_argument("--sym-side", required=True)
    r.add_argument("--arm", action="append", required=True, help="LABEL=CODE_DIR (repeat; arm 1 is the reference)")
    r.add_argument("--arm-set", action="append", help="LABEL:K=V env for one arm only")
    r.add_argument("--set", action="append", help="K=V env applied to every arm")
    r.add_argument("--workers", type=int, default=16)
    r.add_argument("--window-days", type=int, default=30, choices=[20, 30, 365],
                   help="1d/7d are script-testing windows only (USER 2026-10-15) and are not offered here")
    r.add_argument("--npz-src", default=str(SANDBOX / "backtest_v8" / "indicators"))
    r.add_argument("--data-src", default=str(SANDBOX / "data"))
    r.add_argument("--tpl-src", default=str(SANDBOX / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    r.add_argument("--extra-npz", action="append", help="extra SYMBOL npz to stage (pair/peer reads)")
    r.add_argument("--root", default=None)
    r.add_argument("--sequential", action="store_true", help="run arms one after another (default: concurrent = identical host load)")
    r.add_argument("--nice", type=int, default=0)
    r.add_argument("--timeout-s", type=int, default=9000)
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="re-print a finished proof")
    p.add_argument("--run", required=True)
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
