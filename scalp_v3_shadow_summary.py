"""scalp_v3_shadow_summary.py — print a PnL leaderboard across shadow variants."""
from __future__ import annotations
import glob, json, os, sys, datetime
from collections import defaultdict
from pathlib import Path

SHADOW_DIR = Path("data/scalp_v3_shadow")


def load_jsonl(p):
    if not p.exists():
        return []
    out = []
    with open(p) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def summarize_variant(variant: str):
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    decisions = load_jsonl(SHADOW_DIR / f"{variant}_decisions_{today}.jsonl")
    state_p = SHADOW_DIR / f"{variant}_state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    realized = float(state.get("realized_pct", 0.0))
    cycle_n = int(state.get("cycle_n", 0))
    open_pos = len(state.get("positions", {}))

    entries = [d for d in decisions if d.get("event") == "V3_ENTRY"]
    exits   = [d for d in decisions if d.get("event") == "V3_EXIT"]
    hclose  = [d for d in decisions if d.get("event") == "HEDGE_WT_CLOSE"]
    hholds  = [d for d in decisions if d.get("event") == "HEDGE_NOLOSS_HOLD"]

    closed_pnls = [d.get("gain_pct", 0) for d in exits + hclose]
    wins   = sum(1 for p in closed_pnls if p > 0)
    losses = sum(1 for p in closed_pnls if p < 0)
    n_closed = len(closed_pnls)
    wr = (100.0 * wins / n_closed) if n_closed else 0.0
    avg_per_trade = (sum(closed_pnls) / n_closed) if n_closed else 0.0

    # Group exits by reason family
    reason_fams = defaultdict(int)
    for e in exits:
        r = e.get("reason", "")
        fam = r.split("_LONG")[0].split("_SHORT")[0] if "_LONG" in r or "_SHORT" in r else r[:30]
        reason_fams[fam] += 1
    top_reason = max(reason_fams.items(), key=lambda x: x[1]) if reason_fams else ("-", 0)

    return {
        "variant": variant,
        "cycle_n": cycle_n,
        "realized_pct": realized,
        "open": open_pos,
        "entries": len(entries),
        "exits": len(exits),
        "hedge_closes": len(hclose),
        "noloss_holds": len(hholds),
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "avg_per_trade": avg_per_trade,
        "top_exit_reason": f"{top_reason[0]}({top_reason[1]})",
    }


def main():
    variants = sorted({Path(p).stem.split("_state")[0] for p in glob.glob(str(SHADOW_DIR / "*_state.json"))})
    if not variants:
        # Fallback: derive from run.log files
        variants = sorted({Path(p).stem.replace("_run", "") for p in glob.glob(str(SHADOW_DIR / "*_run.log"))})
    rows = [summarize_variant(v) for v in variants]
    rows.sort(key=lambda r: -r["realized_pct"])
    print(f"\n=== SHADOW VARIANT LEADERBOARD ({datetime.datetime.now().strftime('%H:%M:%S')}) ===")
    print(f"{'variant':<12} {'cyc':>4s} {'open':>4s} {'ent':>4s} {'exit':>5s} {'hedge':>5s} {'hold':>5s} {'wr':>5s} {'avgT':>7s} {'realiz':>8s}  {'top_exit'}")
    for r in rows:
        print(f"{r['variant']:<12} {r['cycle_n']:>4d} {r['open']:>4d} {r['entries']:>4d} {r['exits']:>5d} {r['hedge_closes']:>5d} {r['noloss_holds']:>5d} {r['wr']:>4.0f}% {r['avg_per_trade']:>+6.3f}% {r['realized_pct']:>+7.2f}%  {r['top_exit_reason']}")
    # Save JSON snapshot for trending
    snap = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "rows": rows}
    out = SHADOW_DIR / f"_leaderboard_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(snap, indent=2))
    print(f"\nsnapshot → {out}")


if __name__ == "__main__":
    main()
