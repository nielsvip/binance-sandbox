#!/usr/bin/env python3
"""6-hour loss-close fix comparison.
Runs at 01:23 UTC 2026-04-29. Reads pre-fix baseline + computes post-fix metrics.
Writes /Users/niels/logs/loss_close_fix_comparison_<utc>.txt.
"""
import json, re, subprocess, sys
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone

REPO = Path("/Users/niels/Documents/binance")
LOGDIR = Path("/Users/niels/logs")
ACCOUNTS = ["ang","fin","flz","inf","men"]
CUTOFF = "2026-04-28T19:14:31"  # restart marker (UTC)
BASELINE = Path("/tmp/loss_close_fix_baseline_20260428_1921.json")
g_re = re.compile(r"g[a-zA-Z_=]*?(-\d+\.\d+)")

def scan_window(start_iso: str, end_iso: str | None):
    """Return per-account + global stats for closes in [start, end)."""
    per = {}
    g_paths = Counter(); g_lsum=0.0; g_lcnt=0; g_total=0
    for acct in ACCOUNTS:
        a_paths = Counter(); a_lsum=0.0; a_lcnt=0; a_total=0
        for d in ("20260428","20260429"):
            p = REPO / f"data/decisions/decisions_{acct}_{d}.jsonl"
            if not p.exists(): continue
            for line in p.open():
                try: row = json.loads(line)
                except: continue
                ts = row.get("timestamp","")
                if ts < start_iso: continue
                if end_iso is not None and ts >= end_iso: continue
                act = row.get("action","")
                if act not in ("CLOSE","QUICK_CLOSE","REDUCE","STRONG_REDUCE"): continue
                a_total += 1; g_total += 1
                rsn = row.get("reason","")
                m = g_re.search(rsn)
                if m:
                    g = float(m.group(1))
                    if g < -0.05:
                        a_lcnt += 1; g_lcnt += 1
                        a_lsum += g; g_lsum += g
                        prefix = "_".join(rsn.split("_")[:3])[:35]
                        a_paths[prefix] += 1; g_paths[prefix] += 1
        per[acct] = {
            "total": a_total, "loss": a_lcnt, "loss_sum": round(a_lsum,2),
            "top_paths": dict(a_paths.most_common(5)),
        }
    return {"per_account": per, "global": {"total": g_total, "loss": g_lcnt, "loss_sum": round(g_lsum,2), "top_paths": dict(g_paths.most_common(5))}}

def hours_between(start_iso: str, end_iso: str) -> float:
    s = datetime.fromisoformat(start_iso.replace("Z","+00:00"))
    e = datetime.fromisoformat(end_iso.replace("Z","+00:00"))
    if s.tzinfo is None: s = s.replace(tzinfo=timezone.utc)
    if e.tzinfo is None: e = e.replace(tzinfo=timezone.utc)
    return (e - s).total_seconds() / 3600.0

def count_gate_blocks(acct: str) -> dict:
    """Count BLOCKED log lines per gate per account since fix-restart."""
    out = {}
    pattern = "WT_4H_VEL_EXIT_BLOCKED|MANDATORY_REENTRY_BLOCKED|BREAKEVEN_BLOCKED_LOSS|HEDGE_MAX_AGE_KILL_BLOCKED|HEDGE_CLOSE_SCALP_C_BLOCKED|RED_ZONE_GATE_FB"
    for fname in (f"ez_manage_{acct}.log", f"ez_positions_quick_{acct}.log", f"ez_positions_quick_general_{acct}.log"):
        p = LOGDIR / fname
        if not p.exists(): continue
        try:
            r = subprocess.run(["grep","-cE",pattern,str(p)], capture_output=True, text=True, timeout=30)
            n = int(r.stdout.strip()) if r.returncode == 0 else 0
        except Exception: n = 0
        out[fname] = n
    out["total"] = sum(v for k,v in out.items() if k != "total")
    return out

def gate_breakdown(acct: str) -> dict:
    """Break down block hits by individual gate name."""
    breakdown = {}
    for gate in ["WT_4H_VEL_EXIT_BLOCKED","MANDATORY_REENTRY_BLOCKED","BREAKEVEN_BLOCKED_LOSS","HEDGE_MAX_AGE_KILL_BLOCKED","HEDGE_CLOSE_SCALP_C_BLOCKED","RED_ZONE_GATE_FB"]:
        total = 0
        for fname in (f"ez_manage_{acct}.log", f"ez_positions_quick_{acct}.log", f"ez_positions_quick_general_{acct}.log"):
            p = LOGDIR / fname
            if not p.exists(): continue
            try:
                r = subprocess.run(["grep","-c",gate,str(p)], capture_output=True, text=True, timeout=30)
                total += int(r.stdout.strip()) if r.returncode == 0 else 0
            except Exception: pass
        breakdown[gate] = total
    return breakdown

def ob_health() -> dict:
    try:
        r = subprocess.run(["redis-cli","-p","6379","KEYS","orderbook:*"], capture_output=True, text=True, timeout=10)
        keys = [l for l in r.stdout.splitlines() if l.startswith("orderbook:")]
        hb_r = subprocess.run(["redis-cli","-p","6379","GET","orderbook:_heartbeat"], capture_output=True, text=True, timeout=10)
        hb_raw = hb_r.stdout.strip()
        try: hb = json.loads(hb_raw) if hb_raw else {}
        except Exception: hb = {"raw": hb_raw[:120]}
        return {"keys_live": len(keys), "heartbeat": hb}
    except Exception as e:
        return {"error": str(e)}

def shadow_status() -> dict:
    try:
        r = subprocess.run(["pgrep","-f","scalp_v3_shadow.py"], capture_output=True, text=True, timeout=10)
        pids = [p for p in r.stdout.splitlines() if p.strip()]
        return {"alive": len(pids)}
    except Exception as e:
        return {"error": str(e)}

def main():
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_path = LOGDIR / f"loss_close_fix_comparison_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.txt"

    # PRE-FIX
    if BASELINE.exists():
        pre = json.loads(BASELINE.read_text())
        pre_window_h = hours_between("2026-04-28T00:00:00", CUTOFF)
        pre_global = pre["global"]
    else:
        pre_data = scan_window("2026-04-28T00:00:00", CUTOFF)
        pre_window_h = hours_between("2026-04-28T00:00:00", CUTOFF)
        pre_global = {"total_closes_pre_fix": pre_data["global"]["total"],
                      "loss_closes_pre_fix": pre_data["global"]["loss"],
                      "loss_sum_pct_pre_fix": pre_data["global"]["loss_sum"],
                      "top_paths_by_count": pre_data["global"]["top_paths"]}
        pre = {"global": pre_global, "per_account": {a: {
            "total_closes_pre_fix": v["total"], "loss_closes_pre_fix": v["loss"],
            "loss_sum_pct_pre_fix": v["loss_sum"], "by_path": v["top_paths"]
        } for a,v in pre_data["per_account"].items()}}

    # POST-FIX
    post = scan_window(CUTOFF, None)
    post_window_h = hours_between(CUTOFF, now_iso)

    # Gate blocks + OB + shadow
    blocks = {a: gate_breakdown(a) for a in ACCOUNTS}
    blocks_sum = {gate: sum(blocks[a].get(gate,0) for a in ACCOUNTS) for gate in ["WT_4H_VEL_EXIT_BLOCKED","MANDATORY_REENTRY_BLOCKED","BREAKEVEN_BLOCKED_LOSS","HEDGE_MAX_AGE_KILL_BLOCKED","HEDGE_CLOSE_SCALP_C_BLOCKED","RED_ZONE_GATE_FB"]}
    ob = ob_health()
    sh = shadow_status()

    # New paths in post-fix not in pre-fix top-list (potential leaks)
    pre_paths = set(pre["global"].get("top_paths_by_count", {}).keys())
    post_paths = set(post["global"]["top_paths"].keys())
    anomalies = post_paths - pre_paths

    # === RENDER ===
    lines = []
    lines.append("="*80)
    lines.append(f"LOSS-CLOSE FIX 6-HOUR COMPARISON")
    lines.append(f"generated: {now_iso}")
    lines.append(f"fix restart: {CUTOFF}")
    lines.append("="*80)
    lines.append("")

    pre_total = pre["global"].get("total_closes_pre_fix", pre["global"].get("total",0))
    pre_loss = pre["global"].get("loss_closes_pre_fix", pre["global"].get("loss",0))
    pre_lsum = pre["global"].get("loss_sum_pct_pre_fix", pre["global"].get("loss_sum",0.0))
    post_total = post["global"]["total"]
    post_loss = post["global"]["loss"]
    post_lsum = post["global"]["loss_sum"]

    pre_lh = pre_loss / pre_window_h if pre_window_h else 0
    post_lh = post_loss / post_window_h if post_window_h else 0
    pre_lsh = pre_lsum / pre_window_h if pre_window_h else 0
    post_lsh = post_lsum / post_window_h if post_window_h else 0
    reduction = (1 - post_lh / pre_lh) * 100 if pre_lh else 0
    bleed_reduction = (1 - post_lsh / pre_lsh) * 100 if pre_lsh else 0

    lines.append(f"HEADLINE")
    lines.append(f"  loss-closes:  {pre_loss}  in {pre_window_h:.2f}h = {pre_lh:.1f}/h  →  {post_loss}  in {post_window_h:.2f}h = {post_lh:.1f}/h    ({reduction:+.1f}%)")
    lines.append(f"  loss-bleed%:  {pre_lsum:+.1f}% = {pre_lsh:+.2f}%/h  →  {post_lsum:+.1f}% = {post_lsh:+.2f}%/h    ({bleed_reduction:+.1f}%)")
    lines.append("")

    lines.append("PER-ACCOUNT")
    lines.append(f"  {'acct':<6} {'pre_total':>10} {'pre_loss':>9} {'pre_sum%':>10} {'pre_loss/h':>11} | {'post_total':>11} {'post_loss':>10} {'post_sum%':>10} {'post_loss/h':>12}")
    for a in ACCOUNTS:
        p = pre.get("per_account",{}).get(a, {})
        q = post["per_account"].get(a, {})
        pt = p.get("total_closes_pre_fix", p.get("total",0)); pl = p.get("loss_closes_pre_fix", p.get("loss",0)); ps = p.get("loss_sum_pct_pre_fix", p.get("loss_sum",0.0))
        qt = q.get("total",0); ql = q.get("loss",0); qs = q.get("loss_sum",0.0)
        plh = pl/pre_window_h if pre_window_h else 0
        qlh = ql/post_window_h if post_window_h else 0
        lines.append(f"  {a:<6} {pt:>10d} {pl:>9d} {ps:>9.1f}% {plh:>10.1f} | {qt:>11d} {ql:>10d} {qs:>9.1f}% {qlh:>11.1f}")
    lines.append("")

    lines.append("TOP CLOSE-REASON PATHS (pre-fix)")
    for k,v in list(pre["global"].get("top_paths_by_count",{}).items())[:5]:
        lines.append(f"  {v:>5d}  {k}")
    lines.append("")

    lines.append("TOP CLOSE-REASON PATHS (post-fix)")
    for k,v in list(post["global"]["top_paths"].items())[:5]:
        lines.append(f"  {v:>5d}  {k}")
    lines.append("")

    lines.append("GATE BLOCK TALLY (post-fix)")
    for gate, total in sorted(blocks_sum.items(), key=lambda x: -x[1]):
        lines.append(f"  {total:>6d}  {gate}")
    lines.append("")

    lines.append("PER-ACCOUNT GATE BLOCKS")
    for a in ACCOUNTS:
        b = blocks[a]
        gate_list = ", ".join(f"{g.replace('_BLOCKED','').replace('_LOSS','')}={b.get(g,0)}" for g in ["WT_4H_VEL_EXIT_BLOCKED","MANDATORY_REENTRY_BLOCKED","BREAKEVEN_BLOCKED_LOSS","HEDGE_MAX_AGE_KILL_BLOCKED","HEDGE_CLOSE_SCALP_C_BLOCKED","RED_ZONE_GATE_FB"] if b.get(g,0)>0)
        lines.append(f"  {a}: {gate_list or 'no blocks'}")
    lines.append("")

    lines.append("EZ_ORDERBOOK HEALTH")
    lines.append(f"  keys_live: {ob.get('keys_live','?')}")
    if isinstance(ob.get("heartbeat"), dict):
        hb = ob["heartbeat"]
        lines.append(f"  heartbeat: tracked={hb.get('tracked','?')} seeded={hb.get('seeded','?')} writes_total={hb.get('writes_total','?')}")
    lines.append("")

    lines.append("V3 SHADOW")
    lines.append(f"  alive: {sh.get('alive','?')}/8")
    lines.append("")

    if anomalies:
        lines.append("⚠️ ANOMALIES — paths in post-fix top-5 NOT in pre-fix top-5 (possible new leak)")
        for a in anomalies:
            cnt = post["global"]["top_paths"].get(a,0)
            lines.append(f"  {cnt:>5d}  {a}")
        lines.append("")

    lines.append("VERDICT")
    if reduction >= 80:
        lines.append(f"  ✅ Fix WORKED: loss-close rate dropped {reduction:.0f}% per hour. Loss-% bleed cut {bleed_reduction:.0f}%/h.")
    elif reduction >= 50:
        lines.append(f"  🟡 Partial: loss-close rate down {reduction:.0f}% but still leaking. Investigate top post-fix paths.")
    elif reduction > 0:
        lines.append(f"  🟠 Modest: loss-close rate down only {reduction:.0f}%. Likely un-gated channel still firing.")
    else:
        lines.append(f"  🔴 NOT working: loss-close rate {reduction:+.0f}%. Roll back or find the bypass.")
    if anomalies:
        lines.append(f"  ⚠️ {len(anomalies)} new path(s) in post-fix top-5 — review anomaly section.")

    lines.append("")
    lines.append("="*80)

    out_path.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\n[written to {out_path}]")

if __name__ == "__main__":
    sys.exit(main() or 0)
