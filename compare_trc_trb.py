#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""compare_trc_trb.py — daily trc (agent-driven) vs trb (scripted control) comparison.

Runs daily 20:30 UTC after market close. Reads:
  - data/decisions/decisions_trb_YYYYMMDD.jsonl
  - data/decisions/decisions_trc_YYYYMMDD.jsonl

For each account, aggregates today's CLOSE actions into realized P&L (sum of trade.gain_pct
where qty > 0). Splits trc into agent-influenced (reason starts with TRC_AGENT_) vs scripted.

Outputs:
  ~/binance-agent-handoff/comparison/YYYY-MM-DD.md  (human-readable)
  ~/binance-agent-handoff/comparison_log.jsonl       (cumulative, one row per day)

Then commits + pushes to handoff repo.
"""
import json
import logging
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
DECISIONS_DIR = BASE / "data" / "decisions"
HANDOFF_REPO = Path.home() / "binance-agent-handoff"
COMPARISON_DIR = HANDOFF_REPO / "comparison"
LOG_PATH = Path.home() / "logs" / "compare_trc_trb.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
log = logging.getLogger("compare_trc_trb")


def _today_utc():
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _today_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_jsonl(path):
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _is_close_with_qty(row):
    action = (row.get("action") or "").upper()
    trade = row.get("trade") or {}
    qty = float(trade.get("qty") or 0)
    return ("CLOSE" in action or "💥" in (row.get("action") or "")) and qty > 0


def _is_agent_influenced(row):
    reason = row.get("reason_text") or ""
    return reason.startswith("TRC_AGENT_") or reason.startswith("🤖") or "TRC_AGENT_" in reason


def _aggregate(rows):
    n_close = n_open = n_augment = n_wait = n_agent = 0
    realized = []
    by_symbol = defaultdict(lambda: {"closes": 0, "realized_pct": 0.0, "agent_closes": 0})
    for row in rows:
        action = (row.get("action") or "").upper()
        if "CLOSE" in action or "💥" in (row.get("action") or ""):
            n_close += 1
            if _is_close_with_qty(row):
                gain = float((row.get("trade") or {}).get("gain_pct") or 0)
                realized.append(gain)
                pkey = row.get("position_key", "?")
                sym = pkey.split(":", 1)[-1]
                by_symbol[sym]["closes"] += 1
                by_symbol[sym]["realized_pct"] += gain
                if _is_agent_influenced(row):
                    by_symbol[sym]["agent_closes"] += 1
        elif "OPEN" in action:
            n_open += 1
        elif "AUGMENT" in action:
            n_augment += 1
        elif "WAIT" in action:
            n_wait += 1
        if _is_agent_influenced(row):
            n_agent += 1
    total_pct = sum(realized)
    n_trades = len(realized)
    n_wins = sum(1 for g in realized if g > 0)
    n_losses = sum(1 for g in realized if g < 0)
    win_rate = (n_wins / n_trades) if n_trades else 0.0
    avg_per_trade = (total_pct / n_trades) if n_trades else 0.0
    return {
        "n_decisions": len(rows),
        "n_close": n_close,
        "n_close_with_qty": n_trades,
        "n_open": n_open,
        "n_augment": n_augment,
        "n_wait": n_wait,
        "n_agent_influenced": n_agent,
        "total_realized_pct": round(total_pct, 4),
        "avg_per_trade_pct": round(avg_per_trade, 4),
        "n_wins": n_wins,
        "n_losses": n_losses,
        "win_rate": round(win_rate, 4),
        "by_symbol": {k: dict(v) for k, v in by_symbol.items()},
    }


def _split_trc_agent_vs_scripted(rows):
    agent_rows = [r for r in rows if _is_agent_influenced(r)]
    scripted_rows = [r for r in rows if not _is_agent_influenced(r)]
    return _aggregate(agent_rows), _aggregate(scripted_rows)


def _shared_symbol_diff(trb_agg, trc_agg):
    rows = []
    syms = set(trb_agg["by_symbol"].keys()) | set(trc_agg["by_symbol"].keys())
    for sym in sorted(syms):
        b = trb_agg["by_symbol"].get(sym, {"closes": 0, "realized_pct": 0.0, "agent_closes": 0})
        c = trc_agg["by_symbol"].get(sym, {"closes": 0, "realized_pct": 0.0, "agent_closes": 0})
        rows.append({
            "symbol": sym,
            "trb_closes": b["closes"],
            "trc_closes": c["closes"],
            "trb_pct": round(b["realized_pct"], 3),
            "trc_pct": round(c["realized_pct"], 3),
            "delta_pct": round(c["realized_pct"] - b["realized_pct"], 3),
            "trc_agent_closes": c["agent_closes"],
        })
    rows.sort(key=lambda r: -abs(r["delta_pct"]))
    return rows


def _format_md(date_iso, trb_agg, trc_agg, trc_agent, trc_scripted, shared, cumulative):
    L = [f"# trc-vs-trb daily comparison — {date_iso}", ""]
    L.append("## Aggregate (today, UTC)")
    L.append("")
    L.append("| metric | trb (control) | trc (agent on top of script) |")
    L.append("|---|---:|---:|")
    L.append(f"| decisions | {trb_agg['n_decisions']} | {trc_agg['n_decisions']} |")
    L.append(f"| closed trades (qty>0) | {trb_agg['n_close_with_qty']} | {trc_agg['n_close_with_qty']} |")
    L.append(f"| total realized % | **{trb_agg['total_realized_pct']:+.2f}** | **{trc_agg['total_realized_pct']:+.2f}** |")
    L.append(f"| avg per trade % | {trb_agg['avg_per_trade_pct']:+.3f} | {trc_agg['avg_per_trade_pct']:+.3f} |")
    L.append(f"| wins / losses | {trb_agg['n_wins']} / {trb_agg['n_losses']} | {trc_agg['n_wins']} / {trc_agg['n_losses']} |")
    L.append(f"| win rate | {trb_agg['win_rate']*100:.1f}% | {trc_agg['win_rate']*100:.1f}% |")
    delta = trc_agg["total_realized_pct"] - trb_agg["total_realized_pct"]
    L.append(f"| **Δ trc − trb** | — | **{delta:+.2f} pp** |")
    L.append("")
    L.append("## TRC: agent-influenced vs scripted-only (within trc)")
    L.append("")
    L.append("| metric | scripted | agent |")
    L.append("|---|---:|---:|")
    L.append(f"| closed trades | {trc_scripted['n_close_with_qty']} | {trc_agent['n_close_with_qty']} |")
    L.append(f"| total % | {trc_scripted['total_realized_pct']:+.2f} | {trc_agent['total_realized_pct']:+.2f} |")
    L.append(f"| avg/trade | {trc_scripted['avg_per_trade_pct']:+.3f} | {trc_agent['avg_per_trade_pct']:+.3f} |")
    L.append(f"| win rate | {trc_scripted['win_rate']*100:.1f}% | {trc_agent['win_rate']*100:.1f}% |")
    L.append("")
    L.append("## Top 20 symbols by |trc-trb delta|")
    L.append("")
    L.append("| symbol | trb closes | trc closes | trb % | trc % | Δ pp | agent-influenced |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in shared[:20]:
        L.append(f"| {r['symbol']} | {r['trb_closes']} | {r['trc_closes']} | {r['trb_pct']:+.2f} | {r['trc_pct']:+.2f} | **{r['delta_pct']:+.2f}** | {r['trc_agent_closes']} |")
    L.append("")
    L.append(f"## Cumulative (since first run)")
    L.append("")
    L.append(f"- days_recorded: {cumulative['days']}")
    L.append(f"- cumulative_trb_pct: **{cumulative['cum_trb']:+.2f}**")
    L.append(f"- cumulative_trc_pct: **{cumulative['cum_trc']:+.2f}**")
    L.append(f"- cumulative_delta_pp: **{cumulative['cum_trc'] - cumulative['cum_trb']:+.2f}**")
    L.append("")
    L.append("---")
    L.append(f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `compare_trc_trb.py`_")
    return "\n".join(L)


def _read_cumulative_log():
    log_file = HANDOFF_REPO / "comparison_log.jsonl"
    if not log_file.exists():
        return {"days": 0, "cum_trb": 0.0, "cum_trc": 0.0}
    days = 0
    cum_trb = cum_trc = 0.0
    with log_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                cum_trb += float(row.get("trb_total_pct") or 0)
                cum_trc += float(row.get("trc_total_pct") or 0)
                days += 1
            except json.JSONDecodeError:
                pass
    return {"days": days, "cum_trb": cum_trb, "cum_trc": cum_trc}


def _append_cumulative(date_iso, trb_agg, trc_agg, trc_agent, trc_scripted):
    log_file = HANDOFF_REPO / "comparison_log.jsonl"
    row = {
        "date": date_iso,
        "trb_total_pct": trb_agg["total_realized_pct"],
        "trc_total_pct": trc_agg["total_realized_pct"],
        "trb_trades": trb_agg["n_close_with_qty"],
        "trc_trades": trc_agg["n_close_with_qty"],
        "trb_win_rate": trb_agg["win_rate"],
        "trc_win_rate": trc_agg["win_rate"],
        "trc_agent_trades": trc_agent["n_close_with_qty"],
        "trc_agent_pct": trc_agent["total_realized_pct"],
        "trc_scripted_pct": trc_scripted["total_realized_pct"],
        "delta_pp": round(trc_agg["total_realized_pct"] - trb_agg["total_realized_pct"], 4),
    }
    with log_file.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _git_push():
    git_env = os.environ.copy()
    git_env["GIT_SSH_COMMAND"] = f"ssh -i {Path.home()}/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    git_env["HOME"] = str(Path.home())
    cmds = [
        ["git", "-C", str(HANDOFF_REPO), "add", "comparison/", "comparison_log.jsonl"],
        ["git", "-C", str(HANDOFF_REPO), "commit", "-m", f"comparison {_today_iso()}"],
        ["git", "-C", str(HANDOFF_REPO), "push", "origin", "main"],
    ]
    for cmd in cmds:
        rv = subprocess.run(cmd, env=git_env, capture_output=True, text=True, timeout=60)
        if rv.returncode != 0:
            if "nothing to commit" in (rv.stdout + rv.stderr):
                log.info("nothing to commit")
                return False
            log.error("git failed: %s\n%s\n%s", " ".join(cmd), rv.stdout, rv.stderr)
            return False
    return True


def main():
    ymd = _today_utc()
    iso = _today_iso()
    trb_rows = _load_jsonl(DECISIONS_DIR / f"decisions_trb_{ymd}.jsonl")
    trc_rows = _load_jsonl(DECISIONS_DIR / f"decisions_trc_{ymd}.jsonl")
    if not trb_rows and not trc_rows:
        log.warning("no decisions for %s on either trb or trc — skipping", ymd)
        return
    trb_agg = _aggregate(trb_rows)
    trc_agg = _aggregate(trc_rows)
    trc_agent, trc_scripted = _split_trc_agent_vs_scripted(trc_rows)
    shared = _shared_symbol_diff(trb_agg, trc_agg)
    cumulative = _read_cumulative_log()
    cumulative["cum_trb"] += trb_agg["total_realized_pct"]
    cumulative["cum_trc"] += trc_agg["total_realized_pct"]
    cumulative["days"] += 1
    md = _format_md(iso, trb_agg, trc_agg, trc_agent, trc_scripted, shared, cumulative)
    COMPARISON_DIR.mkdir(parents=True, exist_ok=True)
    out_md = COMPARISON_DIR / f"{iso}.md"
    out_md.write_text(md)
    _append_cumulative(iso, trb_agg, trc_agg, trc_agent, trc_scripted)
    log.info("wrote %s (trb %.2f%% / trc %.2f%% / Δ %+.2fpp)", out_md.name, trb_agg["total_realized_pct"], trc_agg["total_realized_pct"], trc_agg["total_realized_pct"] - trb_agg["total_realized_pct"])
    if _git_push():
        log.info("pushed comparison")


if __name__ == "__main__":
    main()
