#!/usr/bin/env python3
"""Shared data-aggregation for the /results dashboard (chart_server.py) and the
12h results digest email (tools/results_digest_email.py).

Reads the Mac-side mirror of S1's reopt/vec-baseline/gating files, kept fresh
by tools/sync_results_dashboard_data.sh via cron (every 5 min) + the existing
per_sym_active_config.json cron pull. Only the S1 CPU/mem/queue liveness probe
is a live SSH call (short timeout, cached in-process) — file contents
themselves are never fetched over SSH from request path.

NO-LIES MANDATE (CLAUDE.md): every real-engine number here is a SINGLE-SYMBOL
backtest (n_syms=1) — below the 48-crypto/100-stock sample floor, so it is
[DIAGNOSTIC] by rule 5. Rule 5 explicitly permits DIAGNOSTIC numbers for
debug/monitoring (never for promotion/deployment/recommendation) — this module
is a monitoring view of the reopt/gating machine, not a promotion decision.
Sharpe values are always capped at +/-5.0 (metrics_guard.PER_SYM_SHARPE_CAP)
and always labeled `real_sharpe_1sym` — never a bare "Sharpe".
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_PATH))
import metrics_guard  # noqa: E402

SHARPE_CAP = metrics_guard.PER_SYM_SHARPE_CAP
MIN_TRADES_DIAGNOSTIC = metrics_guard.MIN_TRADES_PER_SYM_FOR_SYM_SHARPE

VEC_BASELINES = {"crypto": BASE_PATH / "data" / "vec_baselines_crypto.json",
                  "stock": BASE_PATH / "data" / "vec_baselines_stock.json"}
GATING_CORRECTIONS = BASE_PATH / "data" / "reopt_loop" / "gating_corrections.jsonl"
RESCUES = BASE_PATH / "data" / "reopt_loop" / "rescues.jsonl"
ACTIVE_CONFIG = {"crypto": BASE_PATH / "data" / "hourly_reconfig" / "per_sym_active_config.json",
                  "stock": BASE_PATH / "data" / "hourly_reconfig" / "trb" / "active_config.json"}
REMOTE_REOPT_LOG = "/home/niels/logs/reopt_loop.log"


def _is_crypto_sym(sym):
    s = (sym or "").upper()
    return s.endswith("USDT") or s.endswith("USDC")


def mode_for_key(key):
    base = key.rsplit("_", 1)[0] if (key.endswith("_LONG") or key.endswith("_SHORT")) else key
    return "crypto" if _is_crypto_sym(base) else "stock"


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _load_jsonl(path):
    out = []
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return out


def cap_sharpe(v):
    try:
        v = float(v)
    except Exception:
        return None
    if v > SHARPE_CAP:
        return SHARPE_CAP
    if v < -SHARPE_CAP:
        return -SHARPE_CAP
    return v


def _latest_by_key(records, key_field="key", ts_field="ts"):
    latest = {}
    for r in records:
        k = r.get(key_field)
        if not k:
            continue
        ts = r.get(ts_field) or 0
        if k not in latest or ts >= (latest[k].get(ts_field) or 0):
            latest[k] = r
    return latest


def file_mtime_iso(path):
    try:
        ts = Path(path).stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return None


def _active_state(mode):
    """{key: enabled_bool} from active_config overrides (<SIDE>_ENABLED),
    defaulting to True when the field is absent (no explicit gate found =
    not confirmed gated off)."""
    cfg = _load_json(ACTIVE_CONFIG[mode])
    state = {}
    for key, v in cfg.items():
        if key.startswith("_") or not isinstance(v, dict):
            continue
        if mode_for_key(key) != mode:
            continue  # hard separation — never mix crypto/stock keys
        side = "LONG" if key.endswith("_LONG") else ("SHORT" if key.endswith("_SHORT") else None)
        if side is None:
            continue
        ov = v.get("overrides", {}) if isinstance(v.get("overrides"), dict) else {}
        state[key] = bool(ov.get(side + "_ENABLED", True))
    return state


def build_report(mode):
    """mode: 'crypto' or 'stock'. Returns headline counts + winners/gated/
    rescued tables + coverage stats. See module docstring re: DIAGNOSTIC
    labeling — this is a monitoring view, not a promotion decision."""
    vec = {k: v for k, v in _load_json(VEC_BASELINES[mode]).items() if mode_for_key(k) == mode}
    gating_all = [r for r in _load_jsonl(GATING_CORRECTIONS) if r.get("mode") == mode]
    rescues_all = [r for r in _load_jsonl(RESCUES) if r.get("mode") == mode]
    gating_latest = _latest_by_key(gating_all)
    rescues_latest = _latest_by_key(rescues_all)
    active = _active_state(mode)
    real_state = {}
    for k, r in gating_latest.items():
        real_state[k] = {"key": k, "real_sharpe": cap_sharpe(r.get("real_baseline_sharpe")),
                          "trades": r.get("real_trades"), "gain_vs_bh": r.get("real_gain_vs_bh"),
                          "action": r.get("action"), "date": r.get("date"), "ts": r.get("ts") or 0,
                          "source": "gate"}
    for k, r in rescues_latest.items():
        ts = r.get("ts") or 0
        if k in real_state and real_state[k]["ts"] > ts:
            continue
        if r.get("status") == "RESCUED":
            sharpe, trades, gvb = r.get("new_sharpe"), r.get("new_trades"), r.get("new_gain_vs_bh")
        else:
            sharpe, trades, gvb = r.get("real_baseline_sharpe", r.get("best_search_sharpe")), r.get("real_trades"), None
        real_state[k] = {"key": k, "real_sharpe": cap_sharpe(sharpe), "trades": trades, "gain_vs_bh": gvb,
                          "action": r.get("status"), "date": r.get("date"), "ts": ts, "source": "rescue",
                          "winning_tag": r.get("winning_tag"), "changed": r.get("changed"),
                          "before_sharpe": cap_sharpe(r.get("real_baseline_sharpe")) if r.get("status") == "RESCUED" else None}
    total_keys = len(active) if active else len(vec)
    enabled_keys = sum(1 for v in active.values() if v)
    gated = [v for v in real_state.values() if v.get("action") == "KEPT_GATED_REAL_NEGATIVE"]
    winners = [v for v in real_state.values() if (v.get("real_sharpe") if v.get("real_sharpe") is not None else -99) > 0.5]
    mid = [v for v in real_state.values() if 0 <= (v.get("real_sharpe") if v.get("real_sharpe") is not None else -99) <= 0.5]
    rescued_this_run = [v for v in real_state.values() if v.get("source") == "rescue" and v.get("action") == "RESCUED"]
    winners.sort(key=lambda v: v.get("real_sharpe") or -99, reverse=True)
    gated.sort(key=lambda v: v.get("real_sharpe") if v.get("real_sharpe") is not None else -99)
    rescued_sorted = sorted(rescued_this_run, key=lambda v: v.get("ts") or 0, reverse=True)
    n_confirmed = len(real_state)
    return {"mode": mode, "total_keys": total_keys, "enabled_keys": enabled_keys,
            "gated_off_keys": len(gated), "winners_count": len(winners), "mid_count": len(mid),
            "rescued_count": len(rescued_sorted), "vec_screened_keys": len(vec),
            "real_engine_confirmed_keys": n_confirmed, "vec_only_keys": max(0, len(vec) - n_confirmed),
            "winners": winners, "gated": gated, "rescued": rescued_sorted, "mid": mid,
            "all_confirmed": list(real_state.values()),
            "vec_mtime": file_mtime_iso(VEC_BASELINES[mode]), "gating_mtime": file_mtime_iso(GATING_CORRECTIONS),
            "rescues_mtime": file_mtime_iso(RESCUES)}


_LIVENESS_CACHE = {"ts": 0.0, "data": None}


def _parse_reopt_progress(log_tail):
    progress = {}
    for line in log_tail.splitlines():
        m = re.search(r"\[QUEUE\] mode=(\w+) weak_keys=(\d+)", line)
        if m:
            progress.setdefault(m.group(1), {})["queue_total"] = int(m.group(2))
        m = re.search(r"\[PROGRESS\] mode=(\w+) (\d+)/(\d+) rescued=(\d+) enabled=(\d+) hopeless=(\d+)", line)
        if m:
            progress.setdefault(m.group(1), {}).update({"done": int(m.group(2)), "queue_total": int(m.group(3)),
                                                          "rescued": int(m.group(4)), "enabled": int(m.group(5)),
                                                          "hopeless": int(m.group(6))})
        m = re.search(r"\[DONE\] mode=(\w+) n=(\d+) rescued=(\d+) enabled=(\d+) hopeless=(\d+)", line)
        if m:
            progress.setdefault(m.group(1), {}).update({"done": int(m.group(2)), "queue_total": int(m.group(2)),
                                                          "rescued": int(m.group(3)), "enabled": int(m.group(4)),
                                                          "hopeless": int(m.group(5))})
    return progress


def get_s1_liveness(cache_s=60):
    """Best-effort SSH probe of S1 CPU/mem + reopt_loop.log tail. Cached
    in-process for cache_s seconds so page loads/refreshes don't hammer SSH."""
    now = time.time()
    if _LIVENESS_CACHE["data"] is not None and (now - _LIVENESS_CACHE["ts"]) < cache_s:
        return _LIVENESS_CACHE["data"]
    out = {"reachable": False}
    try:
        cmd = ("top -bn1 | sed -n '3p'; free -m | sed -n '2p'; tail -c 4000 %s" % REMOTE_REOPT_LOG)
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=4", "-o", "BatchMode=yes", "s1-int", cmd],
                            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            lines = r.stdout.splitlines()
            out["reachable"] = True
            out["cpu_line"] = lines[0].strip() if lines else None
            out["mem_line"] = lines[1].strip() if len(lines) > 1 else None
            log_tail = "\n".join(lines[2:])
            out["progress"] = _parse_reopt_progress(log_tail)
        else:
            out["error"] = (r.stderr or "").strip()[:300]
    except Exception as e:
        out["error"] = str(e)
    _LIVENESS_CACHE["data"] = out
    _LIVENESS_CACHE["ts"] = now
    return out
