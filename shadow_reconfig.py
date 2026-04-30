#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""shadow_reconfig.py — daily rotation of active shadow configs.

User directive: "keep changing configs frequently at first signs of worse
results; daily reconfigs."

DAILY (00:05 UTC):
    1. Score each ENABLED shadow on rolling 14-day window of round-trip returns.
       (Falls back to 1d / 7d windows if 14d data not yet accumulated.)
    2. If a shadow has ≥CHURN_MIN_TRIPS trips and est_sharpe < CHURN_THRESHOLD,
       OR negative cum return over the window, mark it for retirement.
    3. Pick the next CANDIDATE from shadows/CANDIDATES.json (a queue of
       sweep-winner overrides waiting for shadow validation).
    4. Disable retired shadow in REGISTRY.json, enable candidate (under same
       account slot to keep concurrency stable).
    5. Append rotation event to shadows/rotation_log.jsonl.
    6. SIGHUP shadow_runner.pid → it reconciles within 30s.

USAGE:
    python3 shadow_reconfig.py                # apply daily rotation
    python3 shadow_reconfig.py --dry-run      # show what would change, no writes
    python3 shadow_reconfig.py --status       # current registry + candidate queue
"""
import argparse
import json
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Reuse round-trip + sharpe helpers from shadow_reporter
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shadow_reporter import _round_trip_returns, _est_sharpe, _read_jsonl, _max_dd_pct

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "shadows" / "REGISTRY.json"
CANDIDATES = BASE / "shadows" / "CANDIDATES.json"
SHADOW_ROOT = BASE / "data" / "shadow_decisions"
ROTATION_LOG = BASE / "shadows" / "rotation_log.jsonl"
RUNNER_PID = BASE / "pids" / "shadow_runner.pid"

# Rotation thresholds (tunable; user said "frequently at first signs of worse results")
CHURN_MIN_TRIPS = 10           # need at least N completed trips to retire
CHURN_SHARPE_THRESHOLD = 0.0   # est_sharpe below this → retire candidate
CHURN_DD_THRESHOLD_PCT = 5.0   # cumulative DD above this → retire
LOOKBACK_DAYS_PRIMARY = 14
LOOKBACK_DAYS_FALLBACK = 7
LOOKBACK_DAYS_MIN = 1

def _load(p: Path, default):
    if not p.exists(): return default
    with open(p) as f: return json.load(f)

def _save(p: Path, obj):
    with open(p, "w") as f: json.dump(obj, f, indent=2)

def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")

def _score_shadow(cfg_id: str, account: str, days_back: int) -> dict:
    """Aggregate a shadow's round-trip returns over the trailing N days."""
    today = datetime.now(timezone.utc).date()
    rows = []
    for i in range(days_back):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        p = SHADOW_ROOT / cfg_id / f"orders_{account}_{d}.jsonl"
        rows.extend(_read_jsonl(p))
    rt = _round_trip_returns(rows)
    sharpe, m, std = _est_sharpe(rt)
    dd = _max_dd_pct(rt)
    cum = sum(rt) if rt else 0.0
    return {
        "cfg_id": cfg_id, "account": account, "days_back": days_back,
        "events": len(rows), "trips": len(rt),
        "sharpe": sharpe, "mean_pct": m, "std_pct": std,
        "max_dd_pct": dd, "cum_pct": cum,
    }

def _decide_retirement(score: dict) -> tuple[bool, str]:
    """Returns (should_retire, reason)."""
    if score["trips"] < CHURN_MIN_TRIPS:
        return (False, f"insufficient trips ({score['trips']}/{CHURN_MIN_TRIPS})")
    if score["sharpe"] < CHURN_SHARPE_THRESHOLD:
        return (True, f"est_sharpe={score['sharpe']:+.2f} < threshold {CHURN_SHARPE_THRESHOLD:+.2f}")
    if score["max_dd_pct"] > CHURN_DD_THRESHOLD_PCT and score["cum_pct"] < 0:
        return (True, f"DD={score['max_dd_pct']:.1f}% > {CHURN_DD_THRESHOLD_PCT}% and cum_ret={score['cum_pct']:+.2f}% < 0")
    return (False, f"keep (sharpe={score['sharpe']:+.2f}, dd={score['max_dd_pct']:.1f}%, cum={score['cum_pct']:+.2f}%)")

def _next_candidate(reg: dict, candidates: dict, retiring_cfg: str) -> dict | None:
    """Pick the next candidate matching the retiring shadow's platform that is
    NOT already enabled and exists as a shadows/<id>.json override file."""
    retiring_meta = reg["shadows"].get(retiring_cfg, {})
    plat = retiring_meta.get("platform")
    enabled_ids = {cid for cid, m in reg["shadows"].items() if m.get("enabled")}
    queue = candidates.get("queue", [])
    for entry in queue:
        if entry.get("platform") != plat: continue
        if entry.get("cfg_id") in enabled_ids: continue
        if entry.get("cfg_id") == retiring_cfg: continue
        # Verify override file exists
        if not (BASE / "shadows" / f"{entry['cfg_id']}.json").exists(): continue
        return entry
    return None

def _sighup_runner() -> bool:
    if not RUNNER_PID.exists(): return False
    try:
        pid = int(RUNNER_PID.read_text().strip())
        os.kill(pid, signal.SIGHUP)
        return True
    except (ProcessLookupError, ValueError, PermissionError) as e:
        print(f"  WARN sighup failed: {e}", file=sys.stderr)
        return False

def cmd_status() -> str:
    reg = _load(REGISTRY, {"shadows": {}})
    candidates = _load(CANDIDATES, {"queue": []})
    out = [f"# Shadow rotation status — {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"]
    out.append("\n## Active shadows\n")
    for cid, meta in reg["shadows"].items():
        if not meta.get("enabled"): continue
        s = _score_shadow(cid, meta.get("account", "?"), LOOKBACK_DAYS_PRIMARY)
        out.append(f"  {cid:30s}  acct={meta.get('account','?'):3s}  "
                   f"trips={s['trips']:3d}  sh={s['sharpe']:+.2f}  "
                   f"dd={s['max_dd_pct']:.1f}%  cum={s['cum_pct']:+.2f}%")
    out.append("\n## Candidate queue\n")
    for entry in candidates.get("queue", [])[:20]:
        out.append(f"  {entry.get('cfg_id', '?'):30s}  platform={entry.get('platform', '?')}  "
                   f"source={entry.get('source', '?')[:60]}")
    out.append("\n## Disabled shadows in registry\n")
    for cid, meta in reg["shadows"].items():
        if meta.get("enabled"): continue
        out.append(f"  {cid:30s}  platform={meta.get('platform', '?')}  acct={meta.get('account', '?')}")
    return "\n".join(out)

def cmd_rotate(dry_run: bool = False) -> str:
    reg = _load(REGISTRY, {"shadows": {}})
    candidates = _load(CANDIDATES, {"queue": []})
    out = [f"# Shadow rotation — {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"]
    if dry_run: out.append("\n*** DRY RUN — no changes will be written ***\n")

    actions = []  # list of (cfg_id, score, decide_reason, candidate_or_None)
    for cid, meta in reg["shadows"].items():
        if not meta.get("enabled"): continue
        # Try primary lookback first; fall back to shorter windows when data sparse
        for days in (LOOKBACK_DAYS_PRIMARY, LOOKBACK_DAYS_FALLBACK, LOOKBACK_DAYS_MIN):
            score = _score_shadow(cid, meta.get("account", "?"), days)
            if score["trips"] >= CHURN_MIN_TRIPS or days == LOOKBACK_DAYS_MIN: break
        retire, reason = _decide_retirement(score)
        cand = _next_candidate(reg, candidates, cid) if retire else None
        actions.append((cid, score, reason, cand, retire))

    out.append("\n## Decisions\n")
    for cid, score, reason, cand, retire in actions:
        marker = "🔄 RETIRE" if retire else "✅ KEEP"
        out.append(f"  {marker:12s}  {cid:30s}  trips={score['trips']:3d} sh={score['sharpe']:+.2f} dd={score['max_dd_pct']:.1f}% cum={score['cum_pct']:+.2f}%  ← {reason}")
        if retire:
            if cand: out.append(f"               → candidate: {cand['cfg_id']} ({cand.get('source','?')[:60]})")
            else:    out.append(f"               → no candidate available; will keep slot empty until queue refills")

    if dry_run:
        return "\n".join(out)

    # Apply rotations
    rotated = 0
    rotation_events = []
    for cid, score, reason, cand, retire in actions:
        if not retire: continue
        # Disable retiring; keep account slot for the candidate
        retiring_acct = reg["shadows"][cid].get("account")
        retiring_plat = reg["shadows"][cid].get("platform")
        reg["shadows"][cid]["enabled"] = False
        reg["shadows"][cid]["_retired_at_"] = datetime.now(timezone.utc).isoformat()
        reg["shadows"][cid]["_retired_reason_"] = reason
        if cand:
            cand_id = cand["cfg_id"]
            if cand_id not in reg["shadows"]:
                reg["shadows"][cand_id] = {
                    "platform": retiring_plat, "account": retiring_acct,
                    "enabled": True, "source": cand.get("source", "?"),
                    "target_metric": cand.get("target_metric", "?"),
                }
            else:
                reg["shadows"][cand_id]["enabled"] = True
                reg["shadows"][cand_id]["account"] = retiring_acct
            # Pop candidate from queue
            candidates["queue"] = [e for e in candidates.get("queue", []) if e.get("cfg_id") != cand_id]
            rotation_events.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "retired": cid, "promoted": cand_id, "account": retiring_acct,
                "score": score, "reason": reason,
            })
            rotated += 1

    if rotated > 0:
        _save(REGISTRY, reg)
        _save(CANDIDATES, candidates)
        with open(ROTATION_LOG, "a") as f:
            for e in rotation_events:
                f.write(json.dumps(e) + "\n")
        out.append(f"\n## Applied: {rotated} rotation(s) — REGISTRY.json + CANDIDATES.json updated")
        if _sighup_runner():
            out.append("- 🔔 SIGHUP sent to shadow_runner — supervisor will reconcile within 30s")
        else:
            out.append("- ⚠️ shadow_runner.pid not found or unreachable — restart manually")
    else:
        out.append("\n## No rotations applied")
    return "\n".join(out)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="show would-be rotations, no writes")
    p.add_argument("--status", action="store_true", help="print current registry + candidate queue")
    args = p.parse_args()
    if args.status: print(cmd_status())
    else: print(cmd_rotate(dry_run=args.dry_run))

if __name__ == "__main__":
    main()
