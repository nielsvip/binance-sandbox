"""generate_v4_review.py — Build HTML review page for struct_v4 backtest results.

Generates per-symbol price charts with trade markers (OPEN/CLOSE/AUGMENT),
sorted by performance. Shows best winners AND worst losers prominently.
Outputs a self-contained HTML file (charts as inline base64 PNGs).
"""
from __future__ import annotations

import base64, datetime, io, json, sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_vec_structure_sweep import load_npz

BASE = Path(__file__).resolve().parent
HISTORY_DIR = BASE / "data" / "history"


def load_events(acct: str, sym: str):
    f = HISTORY_DIR / acct / f"{sym}_LONG.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text().strip().split("\n") if l.strip()]


def make_chart(sym: str, mode: str, events: list, start_ts: int) -> str:
    """Generate a PNG chart as base64 string."""
    try:
        npz, ts = load_npz(sym, mode, start_ts=start_ts)
    except Exception:
        return ""
    close = np.asarray(npz["close"], dtype=np.float64)
    dates = [datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc) for t in ts]
    # Downsample for readability (show daily closes)
    step = max(1, len(close) // 500)
    ds_dates = dates[::step]
    ds_close = close[::step]
    fig, ax = plt.subplots(figsize=(10, 3), dpi=80)
    ax.plot(ds_dates, ds_close, color="#333", linewidth=0.7, alpha=0.8)
    # Plot trade events
    for ev in events:
        evt_ts_raw = ev.get("ts", 0)
        if isinstance(evt_ts_raw, str):
            evt_dt = datetime.datetime.fromisoformat(evt_ts_raw)
        else:
            evt_dt = datetime.datetime.fromtimestamp(float(evt_ts_raw), tz=datetime.timezone.utc)
        evt_px = ev.get("price", 0)
        etype = ev.get("type", "")
        if etype == "OPEN":
            ax.scatter([evt_dt], [evt_px], marker="^", color="#2196F3", s=30, zorder=5)
        elif etype == "CLOSE":
            pnl = ev.get("indicators", {}).get("pnl_pct", 0)
            color = "#4CAF50" if pnl > 0 else "#F44336"
            ax.scatter([evt_dt], [evt_px], marker="v", color=color, s=30, zorder=5)
        elif etype == "AUGMENT":
            ax.scatter([evt_dt], [evt_px], marker="D", color="#FF9800", s=20, zorder=4)
    ax.set_title(sym, fontsize=10, fontweight="bold", loc="left")
    ax.tick_params(labelsize=7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(True, alpha=0.2)
    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def compute_metrics(events: list) -> dict:
    opens = [e for e in events if e["type"] == "OPEN"]
    closes = [e for e in events if e["type"] == "CLOSE"]
    augments = [e for e in events if e["type"] == "AUGMENT"]
    if not closes:
        return {"trades": 0, "wr": 0, "total_pnl": 0, "avg_pnl": 0, "best": 0, "worst": 0,
                "pyramids": 0, "compound": 1.0}
    pnls = [c.get("indicators", {}).get("pnl_pct", 0) for c in closes]
    wins = [p for p in pnls if p > 0]
    cap = 10000.0
    for p in pnls:
        cap += cap * (p / 100)
    return {
        "trades": len(closes),
        "wr": len(wins) / len(closes) * 100 if closes else 0,
        "total_pnl": sum(pnls),
        "avg_pnl": np.mean(pnls) if pnls else 0,
        "best": max(pnls) if pnls else 0,
        "worst": min(pnls) if pnls else 0,
        "pyramids": len(augments),
        "compound": cap / 10000.0,
    }


def generate_html(acct: str, mode: str, start_date: str, out_path: str,
                  max_charts: int = 30):
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    hist_dir = HISTORY_DIR / acct
    if not hist_dir.exists():
        print(f"ERROR: {hist_dir} not found")
        return
    syms = sorted([f.stem.replace("_LONG", "") for f in hist_dir.glob("*_LONG.jsonl")])
    print(f"Processing {len(syms)} symbols from {acct}...")
    # Compute all metrics
    rows = []
    for sym in syms:
        events = load_events(acct, sym)
        m = compute_metrics(events)
        # Get B&H
        try:
            npz, ts = load_npz(sym, mode, start_ts=start_ts)
            close = np.asarray(npz["close"], dtype=np.float64)
            bh_mult = float(close[-1]) / float(close[0]) if close[0] > 0 else 1.0
        except Exception:
            bh_mult = 1.0
        ratio = m["compound"] / bh_mult if bh_mult > 0 else 0
        rows.append({
            "sym": sym, "events": events, "bh_mult": bh_mult,
            "ratio": ratio, **m,
        })
    # Sort: worst first, best last (so losers are at top)
    rows.sort(key=lambda r: r["ratio"])
    # Select charts: 10 worst + 10 middle + 10 best
    n = len(rows)
    worst_10 = rows[:10]
    best_10 = rows[-10:]
    mid_start = max(10, n // 2 - 5)
    mid_10 = rows[mid_start:mid_start + 10]
    chart_rows = worst_10 + mid_10 + best_10
    # Pool sharpe
    all_returns = []
    for r in rows:
        for ev in r["events"]:
            if ev["type"] == "CLOSE":
                all_returns.append(ev.get("indicators", {}).get("pnl_pct", 0))
    pool_sharpe = float(np.mean(all_returns) / np.std(all_returns)) if len(all_returns) > 1 and np.std(all_returns) > 0 else 0
    total_trades = len(all_returns)
    # Build HTML
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Struct V4 Review — {acct}</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }}
table {{ border-collapse: collapse; margin-bottom: 30px; background: #161b22; box-shadow: 0 1px 3px rgba(0,0,0,0.3); width: 100%; }}
th, td {{ padding: 6px 12px; border: 1px solid #30363d; font-size: 12px; text-align: right; }}
th {{ background: #21262d; color: #58a6ff; text-align: left; }}
td:first-child {{ font-weight: bold; text-align: left; }}
tr.catastrophic td {{ background: #3d1114; color: #f85149; }}
tr.loser td {{ background: #2d1b1b; color: #ffa198; }}
tr.warn td {{ background: #2d2400; color: #d29922; }}
tr.good td {{ background: #0d2818; color: #56d364; }}
tr.great td {{ background: #0a4420; color: #3fb950; font-weight: bold; }}
.section {{ margin: 40px 0; }}
.chart {{ margin-bottom: 20px; background: #161b22; padding: 10px; border-radius: 6px; }}
.chart img {{ max-width: 100%; height: auto; }}
.chart h3 {{ margin: 0 0 8px 0; font-size: 13px; color: #8b949e; }}
.chart .meta {{ font-size: 11px; color: #8b949e; margin-bottom: 5px; }}
h1 {{ color: #58a6ff; }}
h2 {{ color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
.summary {{ background: #161b22; padding: 20px; border-radius: 8px; margin-bottom: 30px; }}
.summary .big {{ font-size: 24px; color: #58a6ff; font-weight: bold; }}
.summary .stat {{ display: inline-block; margin-right: 30px; }}
.summary .label {{ font-size: 11px; color: #8b949e; }}
</style></head><body>
<h1>Struct V4 "{acct}" — Trade Review</h1>
<div class="summary">
  <div class="stat"><div class="big">{pool_sharpe:+.4f}</div><div class="label">Pool Sharpe</div></div>
  <div class="stat"><div class="big">{total_trades:,}</div><div class="label">Total Trades</div></div>
  <div class="stat"><div class="big">{len(rows)}</div><div class="label">Symbols</div></div>
  <div class="stat"><div class="big">{sum(1 for r in rows if r['ratio']>=4)}</div><div class="label">Cleared 4×B&H</div></div>
  <div class="stat"><div class="big">{sum(1 for r in rows if r['compound']<1.0)}</div><div class="label">Losers</div></div>
  <div class="stat"><div class="big">{np.median([r['ratio'] for r in rows]):.2f}×</div><div class="label">Median ×B&H</div></div>
</div>
<h2>Full table — sorted by ×B&H (worst first)</h2>
<table>
<tr><th>Symbol</th><th>B&H×</th><th>Compound×</th><th>×B&H</th><th>Trades</th><th>Pyramids</th><th>WR%</th><th>Avg%/tr</th><th>Best%</th><th>Worst%</th></tr>
"""
    for r in rows:
        ratio = r["ratio"]
        if r["compound"] < 0.5:
            cls = "catastrophic"
        elif r["compound"] < 1.0:
            cls = "loser"
        elif ratio < 2.0:
            cls = "warn"
        elif ratio < 4.0:
            cls = "good"
        else:
            cls = "great"
        html += (f"<tr class='{cls}'><td>{r['sym']}</td>"
                 f"<td>{r['bh_mult']:.2f}</td><td>{r['compound']:.2f}</td>"
                 f"<td>{ratio:.2f}</td><td>{r['trades']}</td><td>{r['pyramids']}</td>"
                 f"<td>{r['wr']:.0f}</td><td>{r['avg_pnl']:+.2f}</td>"
                 f"<td>{r['best']:+.1f}</td><td>{r['worst']:+.1f}</td></tr>\n")
    html += "</table>\n"
    # Charts section
    html += '<div class="section"><h2>Charts — 10 Worst + 10 Mid + 10 Best</h2>\n'
    for i, r in enumerate(chart_rows):
        section_label = ""
        if i == 0:
            section_label = "<h2 style='color:#f85149'>WORST LOSERS</h2>"
        elif i == 10:
            section_label = "<h2 style='color:#d29922'>MIDDLE OF PACK</h2>"
        elif i == 20:
            section_label = "<h2 style='color:#3fb950'>BEST WINNERS</h2>"
        html += section_label
        print(f"  Charting {r['sym']} ({i+1}/{len(chart_rows)})...")
        b64 = make_chart(r["sym"], mode, r["events"], start_ts)
        if not b64:
            html += f'<div class="chart"><h3>{r["sym"]} — ×B&H={r["ratio"]:.2f} (no chart data)</h3></div>\n'
            continue
        meta = (f"Trades={r['trades']} | Pyramids={r['pyramids']} | WR={r['wr']:.0f}% | "
                f"Avg={r['avg_pnl']:+.2f}%/tr | Compound={r['compound']:.2f}× | "
                f"B&H={r['bh_mult']:.2f}× | <b>×B&H={r['ratio']:.2f}</b>")
        html += (f'<div class="chart"><h3>{r["sym"]} — ×B&H={r["ratio"]:.2f}</h3>'
                 f'<div class="meta">{meta}</div>'
                 f'<img src="data:image/png;base64,{b64}"></div>\n')
    html += "</div>\n</body></html>"
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"\nDone! {out} ({len(html)//1024}KB)")
    print(f"  Pool Sharpe: {pool_sharpe:+.4f}")
    print(f"  Total trades: {total_trades}")
    print(f"  Cleared 4×: {sum(1 for r in rows if r['ratio']>=4)}/{len(rows)}")
    print(f"  Losers: {sum(1 for r in rows if r['compound']<1.0)}/{len(rows)}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--acct", default="struct_v4_safer")
    ap.add_argument("--mode", default="tradier")
    ap.add_argument("--start", default="2024-04-01")
    ap.add_argument("--out", default="charts/struct_v4_review/index.html")
    ap.add_argument("--max-charts", type=int, default=30)
    args = ap.parse_args()
    generate_html(args.acct, args.mode, args.start, args.out, args.max_charts)
