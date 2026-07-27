#!/usr/bin/env python3
"""tools/reopt_loop.py — NEVER-IDLE weak-key re-optimizer on the REAL engine.

USER MANDATE: "neg sharpe results need to be recalculated ALL THE TIME changing
tf's and params until they get to decent results — ANY free cpu or memory needs to
be dedicated to that if there is not an urgent test running." Results are proven on
the ACTUAL live logic (backtest_v8_engine_FOTEST.py, which calls the live
ez_manage/tradier_manage entry/exit functions) — vec is a screen/lie for many
params, used ONLY to pre-screen, NEVER as the final live decision.

ENABLE vs OPTIMIZE (USER 2026-07-07 — vec gating was wrong: ETHUSDC vec -0.59 but
real engine +0.39). "May trade" (ENABLE) is separate from the optimization TARGET:
  - ENABLE line: a key is tradeable if its REAL-engine baseline is POSITIVE
    (pool_sharpe>0 AND beats b&h AND trades>=MIN). Do NOT require >0.5 to re-enable.
  - RESCUE target: >0.5 — what the search keeps chasing to lift an enabled key.
Only keep a key gated OFF if the REAL engine confirms it NEGATIVE (or it can't beat
b&h). Real engine is truth; vec was only ever a screen.

WHAT IT DOES
  0. PHASE=correct (TOP PRIORITY): cheap baseline-first fix of the vec-gating mistake.
     For every vec-gated NEGATIVE key (per_sym <SIDE>_ENABLED=False OR vec enabled=
     False), run ONE real-engine baseline sim on its current config; if real-positive
     + beats b&h -> RE-ENABLE immediately (undo the vec lie), else keep gated + queue
     for rescue. Logs key, vec_baseline (lie) -> real_baseline (truth) -> action to
     data/reopt_loop/gating_corrections.jsonl.
  1. PHASE=search queue = every WEAK key from vec_baselines whose sharpe_per_trade is
     NEGATIVE or WEAK (<0.5), plus NO_TRADES. Priority: most-negative first.
  2. For each key, SEARCH on the REAL engine (baseline + TIMEFRAME knobs first, then
     high-impact WIDE-manifest param families, OFAT off the key's current per_sym
     config). RESCUED = a config with pool_sharpe>0.5 that beats b&h.
  3. Apply: back up + merge the winning override into the per_sym config, set
     <SIDE>_ENABLED=True + flip vec_baselines enabled=True, tag _vec_gate
     REAL_ENGINE_CONFIRMED_<action> (RESCUED_GT05 | REENABLED_POSITIVE), log
     before/after through metrics_guard ([DIAGNOSTIC n_syms=1]). Real-negative keys
     that never beat b&h -> REAL_ENGINE_CONFIRMED_HOPELESS (kept OFF, versioned by
     candidate-set+window signature so not retried until TFs/params/window change).
  4. NEVER IDLE + YIELD: saturates spare S1 (parallel real-engine workers scaled by
     free memory), but yields to urgent work — pauses when /home/niels/logs/URGENT_TEST
     exists or a priority sweep proc is running, and holds each launch until free
     memory > MIN_AVAIL (oom floor). Durable/resumable via per-candidate result
     markers; safe to relaunch (cron self-heal).
  5. Crypto (config.py) and stock (config_tradier.py) are STRICTLY separate: separate
     mode/account/manifest/baselines/per_sym config/basket. Never mixed.

HONESTY: real-engine per-trade pool_sharpe only for the re-enable decision; the
symbol's buy&hold is computed from the same NPZ; single-symbol => below the sample
floor => tagged [DIAGNOSTIC n_syms=1] and never written to any override_* live-loader
path. Inert (zero-delta) knobs are flagged, not counted as a fix.
"""
import os
import re
import sys
import json
import time
import shutil
import random
import hashlib
import argparse
import datetime
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

SBX = Path(os.environ.get("REOPT_SBX", "/home/niels/binance-sandbox"))
PY = os.environ.get("V8_PYTHON", "/home/niels/.conda/envs/binance_env/bin/python")
ENGINE = str(SBX / "backtest_v8_engine_FOTEST.py")
NPZ = str(SBX / "backtest_v8" / "indicators")
LOGS = Path(os.environ.get("REOPT_LOGS", "/home/niels/logs"))
URGENT_FLAG = LOGS / "URGENT_TEST"
PAUSE_FLAG = LOGS / "REOPT_PAUSE"
STATE_ROOT = SBX / "data" / "reopt_loop"
import sys as _sys
_sys.path.insert(0, str(SBX / "tools"))
try:
    import provenance_lib as _prov
except Exception:
    _prov = None
# "Urgent" = genuinely higher-priority jobs we must yield to (fresh-key onboarding,
# user-requested sweeps, the test queue). The routine peer grinder (engine_ofat_screen /
# alternating_grinder) is NOT urgent — this loop coexists with it and shares spare
# capacity (nice-18 + the RAM oom floor keep both civil). Flag file forces a yield.
PRIORITY_PROC_PATTERNS = [p for p in os.environ.get("REOPT_URGENT_PROCS", "v8_test_queue.py,per_sym_sweep_100.py,add_new_symbols").split(",") if p]
CAND_SET_VERSION = "v3-tf+param-2026-07-07"
MIN_TRADES = int(os.environ.get("REOPT_MIN_TRADES", "15"))
RESCUE_SHARPE = float(os.environ.get("REOPT_RESCUE_SHARPE", "0.5"))
IMPROVE_SHARPE = float(os.environ.get("REOPT_IMPROVE_SHARPE", "0.3"))
# ENABLE (may-trade) is separate from RESCUE (optimization target). A key is
# tradeable if its REAL-engine baseline is POSITIVE + beats b&h + has samples.
# >0.5 (RESCUE) is the target the search keeps chasing, NOT the trade/no-trade line.
ENABLE_SHARPE = float(os.environ.get("REOPT_ENABLE_SHARPE", "0.0"))
sys.path.insert(0, str(SBX))
try:
    import metrics_guard
except Exception:
    metrics_guard = None


def _start_for(env_key, default_days):
    override = os.environ.get(env_key, "").strip()
    if override:
        return override
    days = int(os.environ.get("REOPT_WINDOW_DAYS", str(default_days)))
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

MODES = {
    "crypto": {
        "config": SBX / "config.py",
        "account": "ang",
        "manifest": SBX / "data" / "ofat_manifest_crypto_wide.json",
        "baselines": SBX / "data" / "vec_baselines_crypto.json",
        "persym": SBX / "data" / "hourly_reconfig" / "per_sym_active_config.json",
        "start": _start_for("REOPT_START_CRYPTO", 120),
        "tf_candidates": {
            "GOLDEN_RULE_ENTRY_TF_LIST": [["4h", "1h", "15m"], ["1d", "4h", "1h"]],
            "REENTRY2_DC_BREAK_FILTER_TF": ["15m", "1h", "4h"],
            "HTF_REGIME_EXIT_TF": ["1h", "4h"],
            "MTF_WT_CROSS_EXIT_TF": ["1h", "4h"],
            "MTF_DC_REJECT_EXIT_TF": ["1h", "4h"],
            "COOLDOWN_BARS": [6, 12, 24],
        },
    },
    "stock": {
        "config": SBX / "config_tradier.py",
        "account": "trb",
        "manifest": SBX / "data" / "ofat_manifest_tradier_wide.json",
        "baselines": SBX / "data" / "vec_baselines_stock.json",
        "persym": SBX / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json",
        "start": _start_for("REOPT_START_STOCK", 365),
        "tf_candidates": {
            "GOLDEN_RULE_ENTRY_TF_LIST": [["4h", "1h"], ["1d", "4h", "1h"], ["1d", "4h"]],
            "HTF_REGIME_EXIT_TF": ["4h", "1d"],
            "STOP_TIMEFRAME": ["1h", "4h", "1d"],
            "COOLDOWN_BARS": [6, 12, 24],
        },
    },
}
PARAM_FAMILY_PRIORITY = ["GOLDEN", "GR_", "_GR_", "MIN_IND", "ENTRY_SCORE", "ENTRY_MIN", "THRESHOLD", "WT_DC", "EMA_FILTER", "R1_", "R2_", "WT_VEL", "EXIT", "SIZE", "POSITION", "HTF", "COOLDOWN"]
MAX_TF_CANDS = int(os.environ.get("REOPT_MAX_TF", "10"))
MAX_PARAM_CANDS = int(os.environ.get("REOPT_MAX_PARAM", "14"))


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    line = f"{now_iso()} {msg}"
    print(line, flush=True)


def free_mb():
    try:
        for ln in subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines():
            if ln.startswith("Mem:"):
                return int(ln.split()[6])
    except Exception:
        pass
    return 0


def urgent_running():
    if URGENT_FLAG.exists() or PAUSE_FLAG.exists():
        return True
    try:
        ps = subprocess.run(["pgrep", "-af", "python"], capture_output=True, text=True).stdout
        for pat in PRIORITY_PROC_PATTERNS:
            if pat in ps:
                return True
    except Exception:
        pass
    return False


def target_par(min_avail):
    # Single-symbol FOTEST sims are light on RAM (mem is not the constraint on S1 —
    # observed 37 engine procs at <20% mem). Scale by memory headroom but cap by
    # PAR_CAP so we saturate spare CPU without over-subscribing. Yields fully (0) to
    # urgent work.
    if urgent_running():
        return 0
    avail = free_mb()
    cap = int(os.environ.get("REOPT_PAR_CAP", "6"))
    per = int(os.environ.get("REOPT_MB_PER_WORKER", "3500"))
    return max(1, min(cap, (avail - min_avail) // per))


def bh_pct(symbol, start):
    try:
        import numpy as np
        s_ep = datetime.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()
        z = np.load(f"{NPZ}/{symbol}.npz", mmap_mode="r", allow_pickle=True)
        close = z["close"]
        ts = z["timestamps"] if "timestamps" in z.files else (z["ts"] if "ts" in z.files else None)
        i0 = 0
        if ts is not None:
            arr = np.asarray(ts, dtype=float)
            arr = arr / 1000.0 if arr.max() > 1e12 else arr
            w = np.where(arr >= s_ep)[0]
            i0 = int(w[0]) if len(w) else 0
        c0 = float(close[i0])
        c1 = float(close[-1])
        return round((c1 / c0 - 1.0) * 100.0, 3) if c0 else None
    except Exception:
        return None


def bench_bh(key, bh):
    """Side-aware buy&hold benchmark. A SHORT key's fair benchmark is shorting-and-
    holding (inverse of long b&h), else every short is unfairly gated in a rally."""
    if bh is None:
        return None
    return -bh if key.endswith("_SHORT") else bh


def eval_vs_bh(key, gain, bh):
    b = bench_bh(key, bh)
    if gain is None or b is None:
        return None, None
    # A ratio to a non-positive benchmark is not economically meaningful.
    # This matters most for SHORT keys during a bull sample: short-and-hold is
    # negative, so a losing strategy could previously be labelled
    # ``beats_bh=True`` merely because it lost less than the perpetual short.
    # Cash (0%) is the opportunity floor in that case.  Keep the raw
    # side-specific benchmark for reporting, but never manufacture a multiple.
    if b <= 0:
        return None, (gain > 0.0)
    return round(gain / b, 3), (gain > b)


def parse_result(out):
    finals = re.findall(r"V8_RESULT:\s*pool_sharpe=([+-]?[0-9.]+)", out)
    sharpe = float(finals[-1]) if finals else None
    mt = re.findall(r"\btrades=([0-9]+)", out)
    mc = re.findall(r"\bcloses=([0-9]+)", out)
    trades = int(mt[-1]) if mt else (int(mc[-1]) if mc else 0)
    mg = re.findall(r"\bgain_pct=([+-]?[0-9.]+)", out)
    mp = re.findall(r"\bpnl=([+-]?[0-9.]+)", out)
    gain = float(mg[-1]) if mg else (float(mp[-1]) if mp else None)
    err = None
    if sharpe is None:
        err = "EARLY_ABORT" if "EARLY_ABORT" in out else ("timeout" if "TIMEOUT" in out.upper() else "no_result")
    return {"sharpe": sharpe, "trades": trades, "gain": gain, "err": err}


def run_engine(symbol, mode, account, start, override, cell_dir, timeout, min_avail):
    marker = cell_dir / "result.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text())
        except Exception:
            pass
    cell_dir.mkdir(parents=True, exist_ok=True)
    ovr = cell_dir / "ovr.json"
    ovr.write_text(json.dumps(override))
    waited = 0
    while free_mb() < min_avail:
        time.sleep(10)
        waited += 10
        if waited > 1800:
            return {"sharpe": None, "trades": 0, "gain": None, "err": "mem_starved"}
    env = dict(os.environ)
    env.update({"V8_OVERRIDE_FILE": str(ovr), "V8_RATE_GUARD_DISABLED": "1", "V8_SWEEP_MODE": "1", "V8_BACKTEST_DISK_CACHE": "1"})
    cmd = ["timeout", str(timeout), "nice", "-n", "18", PY, ENGINE, "--mode", mode, "--account", account, "--start", start, "--symbols", symbol, "--npz-dir", NPZ]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(SBX), env=env, capture_output=True, text=True, timeout=timeout + 90)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception:
        res = {"sharpe": None, "trades": 0, "gain": None, "err": "timeout"}
        marker.write_text(json.dumps(res))
        return res
    res = parse_result(out)
    res["elapsed_s"] = round(time.time() - t0, 1)
    if res.get("err"):
        (cell_dir / "stderr_tail.txt").write_text(out[-4000:])
    marker.write_text(json.dumps(res))
    return res


def fam_rank(name):
    up = name.upper()
    for i, kw in enumerate(PARAM_FAMILY_PRIORITY):
        if kw in up:
            return i
    return len(PARAM_FAMILY_PRIORITY)


def cfg_attrs(config_path):
    txt = Path(config_path).read_text()
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]+)\s*[:=]", txt, re.M))


def load_weak_queue(mode_cfg):
    d = json.loads(Path(mode_cfg["baselines"]).read_text())
    neg, weak, notr = [], [], []
    for k, v in d.items():
        if not isinstance(v, dict):
            continue
        s = v.get("sharpe_per_trade")
        if s is None:
            notr.append((k, None))
        elif s < 0:
            neg.append((k, s))
        elif s < RESCUE_SHARPE:
            weak.append((k, s))
    neg.sort(key=lambda x: x[1])
    weak.sort(key=lambda x: x[1])
    return neg + weak + notr, d


def load_gated_keys(mode_cfg, include_weak=False):
    """Keys gated OFF based on vec — either per_sym <SIDE>_ENABLED=False OR
    vec_baselines enabled=False. Correction priority = the vec-NEGATIVE ones (the
    'lies' vec claimed are losers); most-negative first. include_weak also pulls the
    0..0.5 vec-off keys (secondary batch)."""
    b = json.loads(Path(mode_cfg["baselines"]).read_text())
    try:
        p = json.loads(Path(mode_cfg["persym"]).read_text())
    except Exception:
        p = {}
    out = []
    for k, v in b.items():
        if not isinstance(v, dict):
            continue
        side = "LONG" if k.endswith("_LONG") else ("SHORT" if k.endswith("_SHORT") else None)
        if side is None:
            continue
        vec_off = v.get("enabled") is False
        pe = p.get(k)
        persym_off = isinstance(pe, dict) and pe.get(side + "_ENABLED") is False
        if not (vec_off or persym_off):
            continue
        s = v.get("sharpe_per_trade")
        if s is None:
            continue
        if s < 0 or (include_weak and s < RESCUE_SHARPE):
            out.append((k, s))
    out.sort(key=lambda x: x[1])
    return out, p


def correction_log_append(record):
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with open(STATE_ROOT / "gating_corrections.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")


def build_candidates(mode, key, persym_entry):
    cfg = MODES[mode]
    base_ovr = dict(persym_entry.get("overrides", {})) if persym_entry else {}
    cands = [("__BASELINE__", dict(base_ovr))]
    tf_added = 0
    for knob, values in cfg["tf_candidates"].items():
        for val in values:
            if tf_added >= MAX_TF_CANDS:
                break
            ovr = dict(base_ovr)
            ovr[knob] = val
            tag = f"TF__{knob}__{re.sub(r'[^A-Za-z0-9]', '', str(val))[:24]}"
            cands.append((tag, ovr))
            tf_added += 1
    man = json.loads(Path(cfg["manifest"]).read_text()).get("params", {})
    params = [(n, m) for n, m in man.items() if m.get("sweepable") and m.get("test_values")]
    params.sort(key=lambda kv: (fam_rank(kv[0]), kv[0]))
    p_added = 0
    for n, m in params:
        if p_added >= MAX_PARAM_CANDS:
            break
        cur = str(base_ovr.get(n, m.get("default")))
        for v in m["test_values"]:
            if p_added >= MAX_PARAM_CANDS:
                break
            if str(v) == cur:
                continue
            cv = _coerce(v)
            ovr = dict(base_ovr)
            ovr[n] = cv
            tag = f"P__{n}__{re.sub(r'[^A-Za-z0-9.]', '', str(v))[:20]}"
            cands.append((tag, ovr))
            p_added += 1
    return cands


def _coerce(v):
    v = str(v)
    if v in ("True", "False"):
        return v == "True"
    try:
        return int(v) if ("." not in v and "e" not in v.lower()) else float(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def cand_hash(tag, ovr):
    h = hashlib.md5((tag + json.dumps(ovr, sort_keys=True)).encode()).hexdigest()[:10]
    return f"{re.sub(r'[^A-Za-z0-9_]', '', tag)[:40]}__{h}"


def win_tag(mode):
    return "w" + MODES[mode]["start"].replace("-", "")


def key_signature(mode):
    cfg = MODES[mode]
    sig = CAND_SET_VERSION + "|" + cfg["start"] + "|" + str(sorted(cfg["tf_candidates"].keys()))
    try:
        sig += "|" + str(Path(cfg["manifest"]).stat().st_mtime_ns)
    except Exception:
        pass
    return hashlib.md5(sig.encode()).hexdigest()[:12]


def is_done(state_key_dir, sig):
    verdict = state_key_dir / "verdict.json"
    if not verdict.exists():
        return None
    try:
        v = json.loads(verdict.read_text())
        if v.get("signature") == sig:
            return v
    except Exception:
        pass
    return None


def write_verdict(state_key_dir, verdict):
    if _prov is not None and isinstance(verdict, dict):  # Bible 12.5
        verdict.setdefault("stamp", _prov.stamp(str(SBX)))
    state_key_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_key_dir / "verdict.json.tmp"
    tmp.write_text(json.dumps(verdict, indent=1))
    os.replace(tmp, state_key_dir / "verdict.json")


def _atomic_write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def _flip_vec_baseline_enabled(mode, key, best, baseline_vec, action):
    """The vec_baselines `enabled` flag is the gate for stocks (per_sym has no
    SIDE_ENABLED there). Flip it True + stamp the REAL-engine truth so the gate is
    real-engine-based, not vec."""
    bpath = Path(MODES[mode]["baselines"])
    try:
        b = json.loads(bpath.read_text())
    except Exception:
        return
    v = b.get(key)
    if not isinstance(v, dict):
        return
    bak = bpath.with_name(bpath.name + f".bak.reopt_{int(time.time())}")
    if not bak.exists():
        shutil.copy2(bpath, bak)
    v["enabled"] = True
    v["real_engine_sharpe"] = best["sharpe"]
    v["real_engine_trades"] = best["trades"]
    v["real_engine_gain_vs_bh"] = best.get("gain_vs_bh")
    v["real_engine_action"] = action
    v["real_engine_ts"] = time.time()
    v["vec_lie_note"] = f"vec sharpe={baseline_vec} gated OFF; real engine sharpe={best['sharpe']} -> {action}"
    b[key] = v
    _atomic_write_json(bpath, b)


def apply_enable(mode, key, winning_ovr, best, baseline_vec, report_line, action):
    cfg = MODES[mode]
    persym_path = Path(cfg["persym"])
    data = json.loads(persym_path.read_text())
    bak = persym_path.with_name(persym_path.name + f".bak.reopt_{int(time.time())}")
    if not bak.exists():
        shutil.copy2(persym_path, bak)
    side = "LONG" if key.endswith("_LONG") else "SHORT"
    entry = data.get(key)
    if not isinstance(entry, dict):
        entry = {"side": side, "overrides": {}, "created_by": "reopt_loop"}
    ovr = dict(entry.get("overrides", {}))
    tf_param_only = {k: v for k, v in winning_ovr.items() if k not in entry.get("overrides", {}) or entry["overrides"].get(k) != v}
    ovr.update(winning_ovr)
    entry["overrides"] = ovr
    entry[f"{side}_ENABLED"] = True
    entry["reopt_action"] = action
    entry["reopt_real_sharpe"] = best["sharpe"]
    entry["reopt_real_trades"] = best["trades"]
    entry["reopt_real_gain_pct"] = best["gain"]
    entry["reopt_gain_vs_bh"] = best.get("gain_vs_bh")
    entry["reopt_beats_bh"] = best.get("beats_bh")
    entry["reopt_changed_params"] = tf_param_only
    entry["reopt_vec_baseline_sharpe"] = baseline_vec
    entry["reopt_ts"] = time.time()
    entry["reopt_date"] = now_iso()
    if _prov is not None:  # Bible 12.5 full-settings provenance
        entry["reopt_stamp"] = _prov.stamp(str(SBX))
        entry["reopt_effective_diff"] = _prov.effective_diff(ovr, "tradier" if mode == "stock" else "crypto")
    entry["_vec_gate"] = f"[REAL_ENGINE_CONFIRMED_{action} n_syms=1 DIAGNOSTIC {report_line} | vec_said={baseline_vec}]"
    data[key] = entry
    _atomic_write_json(persym_path, data)
    _flip_vec_baseline_enabled(mode, key, best, baseline_vec, action)


def apply_rescue(mode, key, winning_ovr, best, baseline_vec, report_line):
    apply_enable(mode, key, winning_ovr, best, baseline_vec, report_line, "RESCUED_GT05")


def report_line_for(mode, best):
    metrics = {"pool_sharpe": best["sharpe"], "sym_sharpe": best["sharpe"], "avg_gain_trade": (best["gain"] / best["trades"]) if best.get("gain") and best.get("trades") else 0.0, "gain_per_yr": None, "gain_sym_yr": None, "trades": best["trades"], "max_dd_pct": None, "n_syms": 1, "years": None}
    if metrics_guard is not None:
        try:
            return metrics_guard.format_standard_set(metrics, mode="crypto" if mode == "crypto" else "tradier")
        except Exception:
            pass
    return f"pool_sharpe={best['sharpe']} | trades={best['trades']} | gain_vs_bh={best.get('gain_vs_bh')} | n_syms=1 [DIAGNOSTIC]"


def rescue_log_append(record):
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with open(STATE_ROOT / "rescues.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")


def process_key(mode, key, baseline_vec, persym_data, timeout, min_avail):
    cfg = MODES[mode]
    symbol = key.rsplit("_", 1)[0]
    sig = key_signature(mode)
    state_key_dir = STATE_ROOT / mode / re.sub(r"[^A-Za-z0-9_]", "", key)
    prior = is_done(state_key_dir, sig)
    if prior is not None:
        return {"key": key, "status": prior.get("status"), "cached": True}
    npz = Path(NPZ) / f"{symbol}.npz"
    if not npz.exists():
        write_verdict(state_key_dir, {"status": "NO_NPZ", "signature": sig, "ts": time.time()})
        return {"key": key, "status": "NO_NPZ"}
    entry = persym_data.get(key)
    cands = build_candidates(mode, key, entry)
    bh = bh_pct(symbol, cfg["start"])
    baseline_res = None
    results = []
    for tag, ovr in cands:
        cell = state_key_dir / win_tag(mode) / cand_hash(tag, ovr)
        res = run_engine(symbol, mode, cfg["account"], cfg["start"], ovr, cell, timeout, min_avail)
        res["tag"] = tag
        res["override_delta"] = {k: v for k, v in ovr.items() if not (entry and entry.get("overrides", {}).get(k) == v)}
        gvbh, beats = eval_vs_bh(key, res.get("gain"), bh)
        res["gain_vs_bh"] = gvbh
        res["beats_bh"] = beats
        if tag == "__BASELINE__":
            baseline_res = res
        results.append((tag, ovr, res))
        if res.get("sharpe") is not None and res["sharpe"] > RESCUE_SHARPE and (res.get("trades") or 0) >= MIN_TRADES and res.get("beats_bh"):
            break
    scored = [(tag, ovr, r) for tag, ovr, r in results if r.get("sharpe") is not None and (r.get("trades") or 0) >= MIN_TRADES]
    # 2026-07-08 GAINMO (USER): rank by GAIN among sharpe-positive candidates (churn law:
    # unconstrained gain-max selects fee-bleed churn, so sharpe<=0 never ranks); fall back
    # to sharpe order when nothing is sharpe-positive.
    _pos = [t for t in scored if t[2]["sharpe"] > 0.0]
    if _pos:
        _pos.sort(key=lambda t: ((t[2].get("gain") or 0.0), t[0] == "__BASELINE__"), reverse=True)
        scored = _pos + [t for t in scored if t[2]["sharpe"] <= 0.0]
    else:
        scored.sort(key=lambda t: (t[2]["sharpe"], t[0] == "__BASELINE__"), reverse=True)
    best_entry = scored[0] if scored else None
    base_sharpe = baseline_res.get("sharpe") if baseline_res else None
    base_beats = baseline_res.get("beats_bh") if baseline_res else None
    base_trades = (baseline_res.get("trades") if baseline_res else 0) or 0
    # RESCUED = optimization target reached: a config with pool_sharpe>0.5 that beats b&h.
    if best_entry and best_entry[2]["sharpe"] > RESCUE_SHARPE and best_entry[2].get("beats_bh"):
        tag, ovr, best = best_entry
        rline = report_line_for(mode, best)
        apply_enable(mode, key, ovr, best, baseline_vec, rline, "RESCUED_GT05")
        rec = {"key": key, "mode": mode, "status": "RESCUED", "vec_baseline_sharpe": baseline_vec, "real_baseline_sharpe": base_sharpe, "new_sharpe": best["sharpe"], "new_trades": best["trades"], "new_gain_vs_bh": best.get("gain_vs_bh"), "winning_tag": tag, "changed": best_entry[2]["override_delta"], "report_line": rline, "start": cfg["start"], "ts": time.time(), "date": now_iso()}
        rescue_log_append(rec)
        write_verdict(state_key_dir, {"status": "RESCUED", "signature": sig, "new_sharpe": best["sharpe"], "winning_tag": tag, "ts": time.time()})
        log(f"[RESCUE] {mode} {key}: vec={baseline_vec} real_base={base_sharpe} -> {best['sharpe']:.4f} (trades={best['trades']} gvbh={best.get('gain_vs_bh')}) via {tag}")
        return {"key": key, "status": "RESCUED", "new_sharpe": best["sharpe"]}
    # ENABLED_POSITIVE = may-trade line: current config is real-positive + beats b&h.
    # Re-enable now (undo any vec gate); the search keeps chasing >0.5 on later passes.
    if base_sharpe is not None and base_sharpe > ENABLE_SHARPE and base_trades >= MIN_TRADES:  # USER 2026-07-08: enable on real-positive; beating b&h is only the >0.5 optimization target
        rline = report_line_for(mode, baseline_res)
        apply_enable(mode, key, {}, baseline_res, baseline_vec, rline, "REENABLED_POSITIVE")
        rec = {"key": key, "mode": mode, "status": "ENABLED_POSITIVE", "vec_baseline_sharpe": baseline_vec, "real_baseline_sharpe": base_sharpe, "real_beats_bh": base_beats, "real_trades": base_trades, "best_search_sharpe": best_entry[2]["sharpe"] if best_entry else None, "report_line": rline, "start": cfg["start"], "ts": time.time(), "date": now_iso()}
        rescue_log_append(rec)
        write_verdict(state_key_dir, {"status": "ENABLED_POSITIVE", "signature": sig, "real_baseline_sharpe": base_sharpe, "best_search_sharpe": best_entry[2]["sharpe"] if best_entry else None, "n_cands": len(results), "ts": time.time()})
        log(f"[ENABLE] {mode} {key}: vec={baseline_vec} -> real +{base_sharpe:.4f} beats_bh={base_beats} trades={base_trades} -> RE-ENABLED (searching for >0.5)")
        return {"key": key, "status": "ENABLED_POSITIVE", "real_base": base_sharpe}
    best_sharpe = best_entry[2]["sharpe"] if best_entry else None
    status = "REAL_ENGINE_CONFIRMED_HOPELESS"
    write_verdict(state_key_dir, {"status": status, "signature": sig, "best_sharpe": best_sharpe, "real_baseline_sharpe": base_sharpe, "real_beats_bh": base_beats, "best_tag": best_entry[0] if best_entry else None, "n_cands": len(results), "ts": time.time()})
    log(f"[HOPELESS] {mode} {key}: real_base={base_sharpe} beats_bh={base_beats} best_real={best_sharpe} (vec={baseline_vec}, kept OFF)")
    return {"key": key, "status": status, "best_sharpe": best_sharpe, "real_base": base_sharpe}


def run_mode(mode, timeout, min_avail, max_keys):
    cfg = MODES[mode]
    queue, _ = load_weak_queue(cfg)
    persym_data = json.loads(Path(cfg["persym"]).read_text())
    baselines = json.loads(Path(cfg["baselines"]).read_text())
    if max_keys:
        queue = queue[:max_keys]
    log(f"[QUEUE] mode={mode} weak_keys={len(queue)} start={cfg['start']} timeout={timeout}s")
    done = 0
    for key, vec_s in queue:
        while True:
            par = target_par(min_avail)
            if par >= 1:
                break
            log(f"[YIELD] urgent/oom — pausing mode={mode} (free={free_mb()}MB urgent={urgent_running()})")
            time.sleep(60)
        try:
            r = process_key(mode, key, vec_s, persym_data, timeout, min_avail)
        except Exception as e:
            log(f"[ERR] {mode} {key}: {e}")
            r = {"key": key, "status": "ERROR", "err": str(e)}
        done += 1
        if done % 5 == 0:
            log(f"[PROGRESS] mode={mode} {done}/{len(queue)} last={r.get('status')}")
    log(f"[DONE] mode={mode} processed={done}")


def _wait_out_urgent(mode):
    while urgent_running():
        log(f"[PAUSED] urgent/oom — mode={mode} free={free_mb()}MB sleeping 60s")
        time.sleep(60)


def run_mode_parallel(mode, timeout, min_avail, max_keys):
    cfg = MODES[mode]
    queue, _ = load_weak_queue(cfg)
    persym_data = json.loads(Path(cfg["persym"]).read_text())
    if max_keys:
        queue = queue[:max_keys]
    log(f"[QUEUE] mode={mode} weak_keys={len(queue)} start={cfg['start']} timeout={timeout}s")
    done = {"n": 0, "rescued": 0, "hopeless": 0, "enabled": 0, "error": 0}
    idx = 0
    par = max(1, target_par(min_avail))
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        while idx < len(queue) or futs:
            while len(futs) < max(1, par) and idx < len(queue):
                _wait_out_urgent(mode)
                par = max(1, target_par(min_avail))
                key, vec_s = queue[idx]
                idx += 1
                futs[ex.submit(process_key, mode, key, vec_s, persym_data, timeout, min_avail)] = key
            if not futs:
                continue
            f = next(as_completed(list(futs.keys())))
            key = futs.pop(f)
            try:
                r = f.result()
            except Exception as e:
                r = {"key": key, "status": "ERROR", "err": str(e)}
            done["n"] += 1
            st = r.get("status", "")
            if st == "RESCUED":
                done["rescued"] += 1
            elif st == "ENABLED_POSITIVE":
                done["enabled"] += 1
            elif st == "REAL_ENGINE_CONFIRMED_HOPELESS":
                done["hopeless"] += 1
            elif st == "ERROR":
                done["error"] += 1
            if done["n"] % 5 == 0:
                log(f"[PROGRESS] mode={mode} {done['n']}/{len(queue)} rescued={done['rescued']} enabled={done['enabled']} hopeless={done['hopeless']} par={par}")
    log(f"[DONE] mode={mode} n={done['n']} rescued={done['rescued']} enabled={done['enabled']} hopeless={done['hopeless']} error={done['error']}")
    return done


def correct_one_key(mode, key, vec_s, persym_data, timeout, min_avail):
    cfg = MODES[mode]
    symbol = key.rsplit("_", 1)[0]
    sig = key_signature(mode)
    state_key_dir = STATE_ROOT / mode / re.sub(r"[^A-Za-z0-9_]", "", key)
    marker = state_key_dir / "enable_verdict.json"
    if marker.exists():
        try:
            m = json.loads(marker.read_text())
            if m.get("signature") == sig:
                return {"key": key, "status": m.get("status"), "cached": True}
        except Exception:
            pass
    npz = Path(NPZ) / f"{symbol}.npz"
    if not npz.exists():
        state_key_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(marker, {"status": "NO_NPZ", "signature": sig, "ts": time.time()})
        return {"key": key, "status": "NO_NPZ"}
    entry = persym_data.get(key)
    base_ovr = dict(entry.get("overrides", {})) if isinstance(entry, dict) else {}
    bh = bh_pct(symbol, cfg["start"])
    cell = state_key_dir / win_tag(mode) / cand_hash("__BASELINE__", base_ovr)
    res = run_engine(symbol, mode, cfg["account"], cfg["start"], base_ovr, cell, timeout, min_avail)
    sh = res.get("sharpe")
    tr = res.get("trades") or 0
    gn = res.get("gain")
    gvbh, beats = eval_vs_bh(key, gn, bh)
    beats = bool(beats)
    res["gain_vs_bh"] = gvbh
    res["beats_bh"] = beats
    positive = sh is not None and sh > ENABLE_SHARPE and beats and tr >= MIN_TRADES
    action = "REENABLED_POSITIVE" if positive else "KEPT_GATED_REAL_NEGATIVE"
    if positive:
        rline = report_line_for(mode, res)
        apply_enable(mode, key, {}, res, vec_s, rline, "REENABLED_POSITIVE")
    rec = {"key": key, "mode": mode, "vec_baseline": vec_s, "real_baseline_sharpe": sh, "real_trades": tr, "real_gain_pct": gn, "real_gain_vs_bh": gvbh, "real_beats_bh": beats, "action": action, "start": cfg["start"], "ts": time.time(), "date": now_iso()}
    correction_log_append(rec)
    state_key_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(marker, {"status": action, "signature": sig, "real_baseline_sharpe": sh, "ts": time.time()})
    log(f"[CORRECT] {mode} {key}: vec={vec_s} (lie) -> real={sh} beats_bh={beats} trades={tr} => {action}")
    return {"key": key, "status": action, "real_base": sh}


def correct_gating(mode, timeout, min_avail, max_keys, include_weak=False):
    cfg = MODES[mode]
    queue, persym_data = load_gated_keys(cfg, include_weak=include_weak)
    if max_keys:
        queue = queue[:max_keys]
    log(f"[CORRECT-QUEUE] mode={mode} vec_gated_keys={len(queue)} (include_weak={include_weak}) start={cfg['start']}")
    done = {"n": 0, "reenabled": 0, "kept": 0, "other": 0}
    idx = 0
    par = max(1, target_par(min_avail))
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        while idx < len(queue) or futs:
            while len(futs) < max(1, par) and idx < len(queue):
                _wait_out_urgent(mode)
                par = max(1, target_par(min_avail))
                k, s = queue[idx]
                idx += 1
                futs[ex.submit(correct_one_key, mode, k, s, persym_data, timeout, min_avail)] = k
            if not futs:
                continue
            f = next(as_completed(list(futs.keys())))
            k = futs.pop(f)
            try:
                r = f.result()
            except Exception as e:
                r = {"key": k, "status": "ERROR", "err": str(e)}
            done["n"] += 1
            st = r.get("status", "")
            if st == "REENABLED_POSITIVE":
                done["reenabled"] += 1
            elif st == "KEPT_GATED_REAL_NEGATIVE":
                done["kept"] += 1
            else:
                done["other"] += 1
            if done["n"] % 5 == 0:
                log(f"[CORRECT-PROGRESS] mode={mode} {done['n']}/{len(queue)} REENABLED={done['reenabled']} KEPT_GATED={done['kept']}")
    log(f"[CORRECT-DONE] mode={mode} n={done['n']} REENABLED={done['reenabled']} KEPT_GATED={done['kept']} other={done['other']}")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "stock"], required=True)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--min-avail", type=int, default=1500)
    ap.add_argument("--max-keys", type=int, default=0)
    ap.add_argument("--parallel", action="store_true")
    ap.add_argument("--only-keys", type=str, default="")
    ap.add_argument("--phase", choices=["correct", "search", "both"], default="search", help="correct=cheap baseline-first vec-gating fix (re-enable real-positive keys); search=full TF+param rescue toward >0.5; both=correct then search")
    ap.add_argument("--include-weak", action="store_true", help="correction: also process 0..0.5 vec-off keys, not just the vec-negative ones")
    a = ap.parse_args()
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    if a.only_keys:
        cfg = MODES[a.mode]
        persym_data = json.loads(Path(cfg["persym"]).read_text())
        baselines = json.loads(Path(cfg["baselines"]).read_text())
        for key in [k.strip() for k in a.only_keys.split(",") if k.strip()]:
            vec_s = baselines.get(key, {}).get("sharpe_per_trade")
            r = process_key(a.mode, key, vec_s, persym_data, a.timeout, a.min_avail)
            log(f"[ONLYKEY] {key} -> {r}")
        return
    if a.phase in ("correct", "both"):
        correct_gating(a.mode, a.timeout, a.min_avail, a.max_keys, include_weak=a.include_weak)
    if a.phase in ("search", "both"):
        if a.parallel:
            run_mode_parallel(a.mode, a.timeout, a.min_avail, a.max_keys)
        else:
            run_mode(a.mode, a.timeout, a.min_avail, a.max_keys)


if __name__ == "__main__":
    main()
