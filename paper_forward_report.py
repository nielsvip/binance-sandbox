"""paper_forward_report.py — Comparison report for paper_forward_runner.

Reads each arm's trades.jsonl + equity.jsonl, computes the 5-metric standard set
(per CLAUDE.md), writes data/paper_forward/REPORT.md, appends one-line summary
to data/paper_forward/DAILY.log.

This is a forward paper test, not a published baseline. Diagnostic Sharpe only.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List

BASE = Path(__file__).resolve().parent
ROOT_DIR = BASE / "data" / "paper_forward"
DATA_DIR = ROOT_DIR / sys.argv[1] if len(sys.argv) > 1 else ROOT_DIR / "v01_baseline"
REPORT_FILE = DATA_DIR / "REPORT.md"
DAILY_LOG = DATA_DIR / "DAILY.log"
LEADERBOARD = ROOT_DIR / "LEADERBOARD.md"
STARTING_EQUITY = 1000.0


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_jsonl(path: Path) -> List[dict]:
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
            except Exception:
                continue
    return out


def _stat_pool_sharpe(net_pcts: List[float]) -> float:
    if len(net_pcts) < 2:
        return 0.0
    n = len(net_pcts)
    mean = sum(net_pcts) / n
    var = sum((x - mean) ** 2 for x in net_pcts) / n
    sd = math.sqrt(var)
    return mean / sd if sd > 0 else 0.0


def _stat_sym_sharpe(closes_by_sym: Dict[str, List[float]]) -> float:
    per_sym = []
    for sym, returns in closes_by_sym.items():
        if len(returns) < 2:
            continue
        mean = sum(returns) / len(returns)
        var = sum((x - mean) ** 2 for x in returns) / len(returns)
        sd = math.sqrt(var)
        if sd > 0:
            per_sym.append(mean / sd)
    if not per_sym:
        return 0.0
    return sum(per_sym) / len(per_sym)


def _stat_max_dd(equity_series: List[float]) -> float:
    if not equity_series:
        return 0.0
    peak = equity_series[0]
    worst = 0.0
    for e in equity_series:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak * 100.0
            if dd > worst:
                worst = dd
    return worst


def _arm_metrics(arm_id: str) -> dict:
    arm_dir = DATA_DIR / f"arm_{arm_id}"
    trades = _read_jsonl(arm_dir / "trades.jsonl")
    equity = _read_jsonl(arm_dir / "equity.jsonl")

    closes = [t for t in trades if t.get("action") == "CLOSE"]
    opens = [t for t in trades if t.get("action") == "OPEN"]

    net_pcts = [float(t.get("net_pct", 0.0)) for t in closes]
    pnls_usd = [float(t.get("pnl_usd", 0.0)) for t in closes]

    closes_by_sym: Dict[str, List[float]] = {}
    for t in closes:
        s = t.get("symbol", "?")
        closes_by_sym.setdefault(s, []).append(float(t.get("net_pct", 0.0)))

    n_close = len(closes)
    n_open = len(opens)
    n_unique_sym = len(closes_by_sym)

    win = sum(1 for x in net_pcts if x > 0)
    win_rate = (win / n_close * 100.0) if n_close else 0.0
    acc_gain_pct = sum(net_pcts)
    avg_gain_trade = (acc_gain_pct / n_close) if n_close else 0.0

    pool_sharpe = _stat_pool_sharpe(net_pcts)
    sym_sharpe = _stat_sym_sharpe(closes_by_sym)

    if equity:
        first_ts = equity[0].get("ts", 0.0)
        last_ts = equity[-1].get("ts", first_ts)
        elapsed_sec = max(1.0, last_ts - first_ts)
        elapsed_yr = elapsed_sec / (365.25 * 86400.0)
        eq_series = [float(e.get("equity", STARTING_EQUITY)) for e in equity]
        last_equity = eq_series[-1]
        max_dd_pct = _stat_max_dd(eq_series)
    else:
        elapsed_sec = 0.0
        elapsed_yr = 0.0
        last_equity = STARTING_EQUITY
        max_dd_pct = 0.0

    gain_per_yr = (acc_gain_pct / elapsed_yr) if elapsed_yr > 0 else 0.0
    gain_sym_yr = (acc_gain_pct / max(1, n_unique_sym) / elapsed_yr) if elapsed_yr > 0 else 0.0

    pnl_total_usd = sum(pnls_usd)

    return {
        "arm": arm_id,
        "n_open": n_open,
        "n_close": n_close,
        "n_unique_sym": n_unique_sym,
        "win_rate_pct": win_rate,
        "acc_gain_pct": acc_gain_pct,
        "avg_gain_trade": avg_gain_trade,
        "pool_sharpe": pool_sharpe,
        "sym_sharpe": sym_sharpe,
        "gain_per_yr": gain_per_yr,
        "gain_sym_yr": gain_sym_yr,
        "max_dd_pct": max_dd_pct,
        "pnl_usd": pnl_total_usd,
        "last_equity": last_equity,
        "elapsed_hours": elapsed_sec / 3600.0,
    }


def _fmt_row(label: str, m: dict) -> str:
    return (
        f"| {label} | {m['n_close']:>6} | {m['n_unique_sym']:>4} | "
        f"{m['win_rate_pct']:>5.1f}% | {m['acc_gain_pct']:>+7.2f}% | "
        f"{m['avg_gain_trade']:>+5.3f}% | {m['pool_sharpe']:>+5.3f} | "
        f"{m['sym_sharpe']:>+5.3f} | {m['gain_per_yr']:>+8.1f}% | "
        f"{m['gain_sym_yr']:>+5.3f}% | {m['max_dd_pct']:>5.2f}% | "
        f"${m['pnl_usd']:>+7.2f} | {m['elapsed_hours']:>5.1f}h |"
    )


def _arm_label(arm_id: str) -> str:
    return {
        "A": "A — full symbols.json",
        "B": "B — symbols_inf only ",
        "C": "C — universe DC-20  ",
    }.get(arm_id, arm_id)


def _build_leaderboard() -> None:
    """Scan every variant subdir under data/paper_forward and rank arm B (V3 +
    inf-filter) by avg gain per trade and pool sharpe. This is the spotlight
    metric for the multi-variant paper farm."""
    if not ROOT_DIR.exists():
        return
    rows = []
    for variant_dir in sorted(ROOT_DIR.iterdir()):
        if not variant_dir.is_dir():
            continue
        # Skip if no arm dirs.
        if not any((variant_dir / f"arm_{a}").exists() for a in ("A", "B", "C")):
            continue
        prev_data, prev_report = DATA_DIR, REPORT_FILE
        try:
            globals()["DATA_DIR"] = variant_dir
            for arm_id in ("A", "B", "C"):
                m = _arm_metrics(arm_id)
                m["variant"] = variant_dir.name
                rows.append(m)
        finally:
            globals()["DATA_DIR"] = prev_data
    if not rows:
        return
    rows.sort(key=lambda m: (m["arm"], -m["avg_gain_trade"]))
    header = (
        "# Paper Forward Leaderboard\n\n"
        f"Generated: {_utc_iso()}\n\n"
        "Sorted within each arm by avg gain per trade (descending).\n\n"
        "| Variant | Arm | Trades | AvgGain/Tr | PoolShp | AccGain | MaxDD | PnL | Hours |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    body = []
    for m in rows:
        body.append(
            f"| {m['variant']} | {m['arm']} | {m['n_close']:>5} | "
            f"{m['avg_gain_trade']:>+5.3f}% | {m['pool_sharpe']:>+5.3f} | "
            f"{m['acc_gain_pct']:>+7.2f}% | {m['max_dd_pct']:>5.2f}% | "
            f"${m['pnl_usd']:>+7.2f} | {m['elapsed_hours']:>5.1f}h |"
        )
    LEADERBOARD.write_text(header + "\n".join(body) + "\n")
    print(f"Wrote {LEADERBOARD}")


def main() -> int:
    metrics = [_arm_metrics(a) for a in ("A", "B", "C")]

    header = (
        "# Paper Forward Test Report\n\n"
        f"Generated: {_utc_iso()}\n\n"
        "**Forward paper test — diagnostic only.** Per CLAUDE.md the published-Sharpe floor "
        "is 48+ symbols × >1yr × pool-averaged. This is a 2-week forward test on a curated "
        "live universe; treat the numbers below as observations, not as published baselines.\n\n"
        "Position sizing: $20/trade, max 8 concurrent, 0.04% taker fees per side modeled. "
        "Starting equity $1000 per arm.\n\n"
        "## Comparison\n\n"
        "| Arm | Trades | #Sym | Win% | AccGain | AvgGain/Tr | PoolShp | SymShp | Gain/yr | Gain/sym/yr | MaxDD | PnL | Elapsed |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )

    rows = "\n".join(_fmt_row(_arm_label(m["arm"]), m) for m in metrics)

    a, b, c = metrics
    delta_b_a = b["acc_gain_pct"] - a["acc_gain_pct"]
    delta_c_a = c["acc_gain_pct"] - a["acc_gain_pct"]
    sharpe_b_a = b["pool_sharpe"] - a["pool_sharpe"]

    summary = (
        "\n\n## Spotlight (Arm B vs Arm A)\n\n"
        "_Does the symbols_inf_long/short filter improve V3 PnL, or does it cost trades on "
        "symbols.json members it didn't pick?_\n\n"
        f"- **AccGain delta (B − A):** {delta_b_a:+.2f}%\n"
        f"- **PoolSharpe delta (B − A):** {sharpe_b_a:+.3f}\n"
        f"- **Trade count B / A:** {b['n_close']} / {a['n_close']}"
        + (f" (B = {b['n_close']/a['n_close']*100:.0f}% of A)" if a['n_close'] else "")
        + "\n\n"
        "## Sanity baseline (Arm C — universe DC-20)\n\n"
        "_Is the curated universe doing anything vs raw market with a primitive strategy?_\n\n"
        f"- **AccGain delta (C − A):** {delta_c_a:+.2f}%\n"
        f"- **Arm C trades:** {c['n_close']} on {c['n_unique_sym']} unique symbols\n"
    )

    REPORT_FILE.write_text(header + rows + summary)

    daily_line = (
        f"{_utc_iso()}  "
        f"A:trades={a['n_close']} pool={a['pool_sharpe']:+.3f} acc={a['acc_gain_pct']:+.2f}% pnl=${a['pnl_usd']:+.2f}  "
        f"B:trades={b['n_close']} pool={b['pool_sharpe']:+.3f} acc={b['acc_gain_pct']:+.2f}% pnl=${b['pnl_usd']:+.2f}  "
        f"C:trades={c['n_close']} pool={c['pool_sharpe']:+.3f} acc={c['acc_gain_pct']:+.2f}% pnl=${c['pnl_usd']:+.2f}  "
        f"Δ(B-A)={delta_b_a:+.2f}% Δ(C-A)={delta_c_a:+.2f}%\n"
    )
    with DAILY_LOG.open("a") as f:
        f.write(daily_line)

    print(f"Wrote {REPORT_FILE}")
    print(f"Appended {DAILY_LOG}")
    print()
    print(daily_line.rstrip())
    try:
        _build_leaderboard()
    except Exception as e:
        print(f"leaderboard error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
