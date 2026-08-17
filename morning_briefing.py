#!/usr/bin/env python3
"""morning_briefing.py — fires at 11:30 UTC pre-market.

Reads the S1 v8_test_queue.py sweep results + V3 shadow leaderboard +
the 01:23 6h forward-test report + live state, and produces a recommended-
settings shortlist for the user to review before market open at 13:30 UTC.

Output: /Users/niels/logs/morning_briefing_<utc>.txt
"""
import json, re, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

REPO = Path("/Users/niels/Documents/binance")
LOGDIR = Path("/Users/niels/logs")
SHADOW_DIR = REPO / "data" / "scalp_v3_shadow"
SWEEP_RESULTS_DIR = REPO / "data" / "test_queue_results"
MATRIX_GUARD_TIMEOUT_SECONDS = 180
AI_DECISIONS_BASE = REPO / "data" / "ai_premarket"

def run(cmd, timeout=30) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception as e:
        return f"<err: {e}>"

def s1_status() -> dict:
    out = {}
    out["sweep_log_tail"] = run("ssh -o ConnectTimeout=10 -o BatchMode=yes s1-int 'tail -50 $(ls -t ~/logs/v8_test_queue_phase2_*.log 2>/dev/null | head -1) 2>&1'")
    out["sweep_proc"] = run("ssh -o ConnectTimeout=10 -o BatchMode=yes s1-int 'ps auxww | grep v8_test_queue | grep -v grep'")
    return out

def s1_results() -> list:
    raw = run("ssh -o ConnectTimeout=10 -o BatchMode=yes s1-int 'ls -t ~/binance-sandbox/data/test_queue_results/abtest_*.json 2>/dev/null | head -25'")
    files = [l.strip() for l in raw.splitlines() if l.strip()]
    pulled = []
    for f in files[:18]:
        content = run(f"ssh -o ConnectTimeout=10 -o BatchMode=yes s1-int 'cat {f}'")
        try: pulled.append(json.loads(content))
        except Exception: pass
    return pulled

def shadow_leaderboard() -> list:
    rows = []
    for state_file in sorted(SHADOW_DIR.glob("*_state.json")):
        try:
            d = json.loads(state_file.read_text())
            variant = state_file.stem.replace("_state", "")
            pnl = d.get("total_pnl_pct", d.get("pnl_pct", d.get("realized_pnl_pct", 0)))
            trades = d.get("total_trades", d.get("n_trades", d.get("trades", 0)))
            wr = d.get("win_rate_pct", d.get("wr", 0))
            rows.append({"variant": variant, "pnl_pct": float(pnl) if pnl else 0, "trades": int(trades) if trades else 0, "wr_pct": float(wr) if wr else 0})
        except Exception: pass
    rows.sort(key=lambda x: -x["pnl_pct"])
    return rows

def six_hour_report() -> str:
    files = sorted(LOGDIR.glob("loss_close_fix_comparison_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files: return "<no 6h comparison report found>"
    return files[0].read_text()

def live_state_summary() -> dict:
    out = {}
    out["crypto_workers"] = len(run("pgrep -f 'python.*ez_manage.py --account'").splitlines())
    out["v3_shadows"] = len(run("pgrep -f 'scalp_v3_shadow.py'").splitlines())
    out["ob_keys"] = run("redis-cli -p 6379 KEYS 'orderbook:*' | wc -l").strip()
    return out

def matrix_progress() -> str:
    """Use the canonical guard, including its exact/amber split."""
    guard = REPO / "tools" / "matrix_guard.py"
    try:
        p = subprocess.run(
            [sys.executable, str(guard)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=MATRIX_GUARD_TIMEOUT_SECONDS,
        )
        body = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return body.strip() or f"matrix_guard.py produced no output (exit={p.returncode})"
    except Exception as exc:
        return f"matrix_guard.py unavailable: {exc}"


def ai_premarket_section() -> str:
    """Read todays AI premarket decisions (TRC paper A/B) and render for briefing."""
    try:
        # Prefer today UTC, fallback to latest
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        candidates = [
            AI_DECISIONS_BASE / today / "decisions.json",
            AI_DECISIONS_BASE / "latest.json",
        ]
        # also check sorted latest under base
        if not any(p.exists() for p in candidates):
            try:
                all_files = sorted(AI_DECISIONS_BASE.glob("*/decisions.json"))
                if all_files:
                    candidates.append(all_files[-1])
            except Exception:
                pass
        src = None
        data = None
        for p in candidates:
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                    src = p
                    break
                except Exception:
                    continue
        if not data or not src:
            return "AI PREMARKET (TRC paper A/B, 12:00 UTC)\n  (no decisions yet — run: python tradier_ai_premarket.py)"
        decisions = data.get("decisions", []) if isinstance(data, dict) else []
        all_scored = data.get("all_decisions", decisions)
        gen = data.get("generated_at_utc", "?")
        model = data.get("model", "?")
        tv = data.get("tradingview_enabled", False)
        lines = []
        lines.append(f"AI PREMARKET — TRC PAPER A/B (12:00 UTC, TRB control untouched)")
        lines.append(f"  source: {src}  generated: {gen}  model: {model}  TV: {'enriched' if tv else 'local-only'}")
        if not decisions:
            lines.append(f"  Scored {len(all_scored)} symbols, 0 actionable (conviction < threshold or weekend flat)")
            # show top scored neutral for visibility
            top_neutral = sorted(all_scored, key=lambda d: d.get("conviction", 0), reverse=True)[:5] if all_scored else []
            if top_neutral:
                lines.append("  Top scored (neutral):")
                for d in top_neutral:
                    lines.append(f"    {d.get('symbol','?'):6} {d.get('bias','?'):7} conv={d.get('conviction',0):.2f} | {d.get('reason','')[:90]}")
            return "\n".join(lines)
        longs = [d for d in decisions if d.get("side") == "LONG"]
        shorts = [d for d in decisions if d.get("side") == "SHORT"]
        lines.append(f"  Actionable: {len(decisions)} ({len(longs)} LONG / {len(shorts)} SHORT) from {data.get('universe_count','?')} universe, scored {len(all_scored)}")
        lines.append(f"  {'SYMBOL':<8} {'SIDE':<6} {'CONV':<5} {'SIZE':<5} REASON")
        for d in decisions:
            sym = d.get("symbol", "?")
            side = d.get("side", "?")
            conv = d.get("conviction", 0)
            size = d.get("size_mult", 1.0)
            reason = d.get("reason", "")[:80]
            lines.append(f"    {sym:<8} {side:<6} {conv:<5.2f} {size:<5.2f} {reason}")
        lines.append(f"  → TRC will inject these via tradier_rankings (TRC = TRB + AI); TRB stays control.")
        lines.append(f"  → Live enforcement via ~/binance-agent-handoff/trc_advisories.json (expires 20:00 ET)")
        return "\n".join(lines)
    except Exception as e:
        return f"AI PREMARKET — error reading decisions: {e}"


def ai_evening_attribution() -> str:
    """Evening hook — same data, but also show trades that fired from AI picks (TRC vs TRB)."""
    try:
        # Reuse ai_premarket_section for recommendation part, then add trade fills if history exists
        base = ai_premarket_section()
        # Try to attribute fills from data/history/trc vs trb
        hist_trc = REPO / "data" / "history" / "trc"
        hist_trb = REPO / "data" / "history" / "trb"
        # Quick count of today's jsonl lines if present
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trc_trades = 0
        trb_trades = 0
        try:
            if hist_trc.exists():
                for p in hist_trc.glob("*.jsonl"):
                    try:
                        txt = p.read_text()
                        trc_trades += txt.count(today)
                    except Exception:
                        pass
            if hist_trb.exists():
                for p in hist_trb.glob("*.jsonl"):
                    try:
                        txt = p.read_text()
                        trb_trades += txt.count(today)
                    except Exception:
                        pass
        except Exception:
            pass
        if trc_trades or trb_trades:
            base += f"\n  Today's ledger hits (approx, by date string): TRC={trc_trades} TRB={trb_trades}"
            if trc_trades != trb_trades:
                base += f"  delta={trc_trades - trb_trades:+d} (AI lift signal)"
        else:
            base += "\n  Ledger: no history files yet for today (market closed or not yet traded)"
        return base
    except Exception as e:
        return f"AI EVENING — error: {e}"

def main():
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_path = LOGDIR / f"morning_briefing_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.txt"

    sweep_info = s1_status()
    sweep_results = s1_results()
    leaderboard = shadow_leaderboard()
    forward_6h = six_hour_report()
    live = live_state_summary()
    matrix = matrix_progress()

    lines = []
    lines.append("="*80)
    lines.append(f"MORNING BRIEFING — pre-market 13:30 UTC")
    lines.append(f"generated: {now_iso}")
    lines.append("="*80)
    lines.append("")

    lines.append("SWITCH_MATRIX_TRB PROGRESS (canonical exact + provisional amber)")
    lines.extend(f"  {line}" for line in matrix.splitlines())
    lines.append("  Historical BASELINE_V2_S4H evidence is provenance-bound and not current exact credit.")
    lines.append("")

    lines.append("LIVE STATE")
    lines.append(f"  crypto workers alive: {live['crypto_workers']}/5")
    lines.append(f"  V3 shadow variants:   {live['v3_shadows']}")
    lines.append(f"  ez_orderbook keys:    {live['ob_keys']}")
    lines.append("")

    lines.append("S1 SWEEP STATUS (Phase 2 A/B tests)")
    if "v8_test_queue.py" in sweep_info["sweep_proc"]:
        lines.append("  ⏳ STILL RUNNING — incomplete results below")
    else:
        lines.append("  ✅ DONE")
    lines.append("  log tail:")
    for line in sweep_info["sweep_log_tail"].splitlines()[-30:]:
        lines.append(f"    {line}")
    lines.append("")

    if sweep_results:
        lines.append("S1 SWEEP RESULTS (completed A/B tests)")
        winners_a, winners_b, ties = [], [], []
        for r in sweep_results:
            param = r.get("param", "?")
            sa = r.get("sharpe_a") or r.get("sharpe_per_trade_a") or r.get("pool_sharpe_a")
            sb = r.get("sharpe_b") or r.get("sharpe_per_trade_b") or r.get("pool_sharpe_b")
            ga = r.get("acc_gain_pct_a") or r.get("total_gain_a")
            gb = r.get("acc_gain_pct_b") or r.get("total_gain_b")
            va, vb = r.get("value_a","?"), r.get("value_b","?")
            try:
                sa_f = float(sa) if sa is not None else None
                sb_f = float(sb) if sb is not None else None
                if sa_f is not None and sb_f is not None:
                    delta = sa_f - sb_f
                    if abs(delta) < 0.05:
                        ties.append((param, va, vb, sa_f, sb_f, ga, gb))
                    elif delta > 0:
                        winners_a.append((param, va, vb, sa_f, sb_f, ga, gb))
                    else:
                        winners_b.append((param, va, vb, sa_f, sb_f, ga, gb))
            except Exception: pass
        lines.append(f"  A wins (today's setting): {len(winners_a)}")
        lines.append(f"  B wins (alt setting):    {len(winners_b)}")
        lines.append(f"  Ties (Δ<0.05):           {len(ties)}")
        lines.append("")
        lines.append("  RECOMMENDED CHANGES (where B beat A by Δ Sharpe ≥0.10):")
        rec = [w for w in winners_b if abs(w[3] - w[4]) >= 0.10]
        if rec:
            for param, va, vb, sa_f, sb_f, ga, gb in sorted(rec, key=lambda x: x[4]-x[3], reverse=True):
                lines.append(f"    🔁 {param}: change {va} → {vb}  (Sharpe {sa_f:.3f} → {sb_f:.3f}, +{sb_f-sa_f:.3f})")
        else:
            lines.append(f"    ✅ Today's defaults hold — no Δ≥0.10 challengers.")
        lines.append("")

    lines.append("V3 SHADOW LEADERBOARD (top 8 by PnL)")
    lines.append(f"  {'variant':<22} {'pnl%':>8} {'trades':>8} {'wr%':>6}")
    for r in leaderboard[:8]:
        lines.append(f"  {r['variant']:<22} {r['pnl_pct']:>7.2f}% {r['trades']:>8d} {r['wr_pct']:>5.1f}%")
    lines.append("")
    lines.append("V3 SHADOW LEADERBOARD (bottom 4)")
    for r in leaderboard[-4:]:
        lines.append(f"  {r['variant']:<22} {r['pnl_pct']:>7.2f}% {r['trades']:>8d} {r['wr_pct']:>5.1f}%")
    lines.append("")

    lines.append("6-HOUR FORWARD-TEST COMPARISON")
    for line in forward_6h.splitlines()[:60]:
        lines.append(f"  {line}")
    lines.append("")
    # AI PREMARKET — 12:00 UTC daily, TRC paper A/B (TRB control)
    ai_section = ai_premarket_section()
    lines.append(ai_section)
    lines.append("")
    # Evening attribution hook (for the 12:00 UTC digest second run / manual evening)
    # Show the same AI picks plus ledger delta if history exists — useful when briefing is run post-close
    is_evening = datetime.now(timezone.utc).hour >= 20
    if is_evening or "--evening" in sys.argv:
        lines.append("EVENING AI ATTRIBUTION (TRC vs TRB)")
        lines.append(ai_evening_attribution())
        lines.append("")
    lines.append("="*80)
    lines.append("SUMMARY")
    lines.append("  - Live state above (workers/shadows/OB keys)")
    lines.append("  - S1 sweep verdict above (recommended changes if any)")
    lines.append("  - Shadow leaderboard reveals best Phase 2 variant")
    lines.append("  - Apply recommended changes BEFORE market open at 13:30 UTC if confidence is high")
    lines.append("  - tradier_indicators+rankings will auto-relaunch at 12:00 UTC")
    lines.append("="*80)

    out_path.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\n[written to {out_path}]")

if __name__ == "__main__":
    sys.exit(main() or 0)
