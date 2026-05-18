"""generate_v4_review_mtf_short.py — Multi-timeframe SHORT trade charts.

Same as generate_v4_review_mtf.py but reads *_SHORT.jsonl and labels appropriately.
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
    f = HISTORY_DIR / acct / f"{sym}_SHORT.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text().strip().split("\n") if l.strip()]


def _resample_ohlc(close, timestamps, step):
    n = len(close)
    n_bars = n // step
    if n_bars < 2:
        return None
    o = np.array([close[i * step] for i in range(n_bars)])
    h = np.array([close[i * step:(i + 1) * step].max() for i in range(n_bars)])
    l = np.array([close[i * step:(i + 1) * step].min() for i in range(n_bars)])
    c = np.array([close[(i + 1) * step - 1] for i in range(n_bars)])
    t = np.array([timestamps[i * step] for i in range(n_bars)])
    return {"o": o, "h": h, "l": l, "c": c, "ts": t}


def _parse_event_ts(ev):
    raw = ev.get("ts", 0)
    if isinstance(raw, str):
        return datetime.datetime.fromisoformat(raw).timestamp()
    return float(raw)


def _build_trade_periods(events):
    periods = []
    cur_open_ts = None
    cur_entry_px = None
    cur_pyramids = 0
    for ev in events:
        etype = ev.get("type", "")
        ets = _parse_event_ts(ev)
        epx = ev.get("price", 0)
        if etype == "OPEN":
            cur_open_ts = ets
            cur_entry_px = epx
            cur_pyramids = 0
        elif etype == "AUGMENT":
            cur_pyramids += 1
        elif etype == "CLOSE":
            pnl = ev.get("indicators", {}).get("pnl_pct", 0)
            if cur_open_ts is not None:
                periods.append({
                    "open_ts": cur_open_ts, "close_ts": ets,
                    "entry_px": cur_entry_px, "exit_px": epx,
                    "pnl_pct": pnl, "pyramids": cur_pyramids,
                })
            cur_open_ts = None
    return periods


def _plot_ohlc_panel(ax, ohlc, trade_periods, tf_label, max_bars=600):
    o, h, l, c, ts = ohlc["o"], ohlc["h"], ohlc["l"], ohlc["c"], ohlc["ts"]
    n = len(o)
    if n > max_bars:
        step = n // max_bars
        o_new = o[::step].copy()
        h_new = h[::step].copy()
        l_new = l[::step].copy()
        c_new = c[::step].copy()
        ts_new = ts[::step]
        n_new = len(o_new)
        for i in range(n_new):
            src_start = i * step
            src_end = min(src_start + step, n)
            h_new[i] = ohlc["h"][src_start:src_end].max()
            l_new[i] = ohlc["l"][src_start:src_end].min()
        o, h, l, c, ts = o_new, h_new, l_new, c_new, ts_new
        n = n_new

    dates = [datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc) for t in ts]

    for i in range(n):
        color = "#3fb950" if c[i] >= o[i] else "#f85149"
        ax.plot([dates[i], dates[i]], [l[i], h[i]], color=color, linewidth=0.5, alpha=0.6)
        body_lo = min(o[i], c[i])
        body_hi = max(o[i], c[i])
        if body_hi - body_lo < (h[i] - l[i]) * 0.01:
            body_hi = body_lo + (h[i] - l[i]) * 0.01
        ax.plot([dates[i], dates[i]], [body_lo, body_hi], color=color, linewidth=1.5, alpha=0.8, solid_capstyle="butt")

    # Trade period shading — SHORT: profit when price drops (red shade = loss = price going up)
    for tp in trade_periods:
        open_dt = datetime.datetime.fromtimestamp(tp["open_ts"], tz=datetime.timezone.utc)
        close_dt = datetime.datetime.fromtimestamp(tp["close_ts"], tz=datetime.timezone.utc)
        pnl = tp["pnl_pct"]
        shade_color = "#0d4420" if pnl > 0 else "#4d1114"
        ax.axvspan(open_dt, close_dt, alpha=0.15, color=shade_color, zorder=0)
        ax.axvline(open_dt, color="#da7b26", linewidth=0.8, alpha=0.7, linestyle="--", zorder=3)
        exit_color = "#3fb950" if pnl > 0 else "#f85149"
        ax.axvline(close_dt, color=exit_color, linewidth=0.8, alpha=0.7, linestyle="--", zorder=3)

    ax.set_ylabel(tf_label, fontsize=9, fontweight="bold", color="#8b949e")
    ax.tick_params(labelsize=7, colors="#8b949e")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.set_facecolor("#0d1117")
    ax.grid(True, alpha=0.1, color="#30363d")
    for spine in ax.spines.values():
        spine.set_color("#30363d")


def make_mtf_chart(sym: str, mode: str, events: list, start_ts: int) -> str:
    try:
        npz, ts = load_npz(sym, mode, start_ts=start_ts)
    except Exception:
        return ""
    close = np.asarray(npz["close"], dtype=np.float64)
    n = len(close)
    if n < 200:
        return ""

    trade_periods = _build_trade_periods(events)

    tfs = [
        ("4h", 48, 400),
        ("1h", 12, 500),
        ("15m", 3, 500),
    ]

    fig, axes = plt.subplots(len(tfs), 1, figsize=(14, 3 * len(tfs)), dpi=100,
                             gridspec_kw={"hspace": 0.3})
    fig.patch.set_facecolor("#0d1117")

    for idx, (tf_label, step, max_bars) in enumerate(tfs):
        ax = axes[idx] if len(tfs) > 1 else axes
        ohlc = _resample_ohlc(close, ts, step)
        if ohlc is None:
            ax.text(0.5, 0.5, f"No data for {tf_label}", transform=ax.transAxes,
                    ha="center", color="#8b949e")
            continue
        _plot_ohlc_panel(ax, ohlc, trade_periods, tf_label, max_bars=max_bars)

    n_trades = len(trade_periods)
    total_pnl = sum(tp["pnl_pct"] for tp in trade_periods)
    total_pyrs = sum(tp["pyramids"] for tp in trade_periods)
    wins = sum(1 for tp in trade_periods if tp["pnl_pct"] > 0)
    wr = wins / max(n_trades, 1) * 100
    fig.suptitle(f"{sym} SHORT  |  {n_trades} trades  |  WR {wr:.0f}%  |  "
                 f"total PnL {total_pnl:+.1f}%  |  {total_pyrs} pyramids",
                 fontsize=11, fontweight="bold", color="#c9d1d9", y=0.98)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def compute_metrics(events: list) -> dict:
    closes = [e for e in events if e["type"] == "CLOSE"]
    augments = [e for e in events if e["type"] == "AUGMENT"]
    if not closes:
        return {"trades": 0, "wr": 0, "total_pnl": 0, "avg_pnl": 0,
                "best": 0, "worst": 0, "pyramids": 0, "compound": 1.0}
    pnls = [c.get("indicators", {}).get("pnl_pct", 0) for c in closes]
    wins = [p for p in pnls if p > 0]
    cap = 10000.0
    for p in pnls:
        cap += cap * (p / 100)
    return {
        "trades": len(closes),
        "wr": len(wins) / len(closes) * 100 if closes else 0,
        "total_pnl": sum(pnls),
        "avg_pnl": float(np.mean(pnls)) if pnls else 0,
        "best": max(pnls) if pnls else 0,
        "worst": min(pnls) if pnls else 0,
        "pyramids": len(augments),
        "compound": cap / 10000.0,
    }


def generate_html(acct: str, mode: str, start_date: str, out_path: str):
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    hist_dir = HISTORY_DIR / acct
    if not hist_dir.exists():
        print(f"ERROR: {hist_dir} not found")
        return
    syms = sorted([f.stem.replace("_SHORT", "") for f in hist_dir.glob("*_SHORT.jsonl")])
    print(f"Processing {len(syms)} SHORT symbols from {acct}...")

    rows = []
    for sym in syms:
        events = load_events(acct, sym)
        m = compute_metrics(events)
        try:
            npz, _ = load_npz(sym, mode, start_ts=start_ts)
            close = np.asarray(npz["close"], dtype=np.float64)
            bh_short_mult = (2.0 - float(close[-1]) / float(close[0])) if close[0] > 0 else 1.0
        except Exception:
            bh_short_mult = 1.0
        ratio = m["compound"] / max(bh_short_mult, 0.01) if bh_short_mult > 0 else 0
        rows.append({"sym": sym, "events": events, "bh_mult": bh_short_mult,
                      "ratio": ratio, **m})

    rows.sort(key=lambda r: r["ratio"])
    n = len(rows)
    worst_10 = rows[:10]
    best_10 = rows[-10:]
    mid_start = max(10, n // 2 - 5)
    mid_10 = rows[mid_start:mid_start + 10]
    chart_rows = worst_10 + mid_10 + best_10

    all_returns = []
    for r in rows:
        for ev in r["events"]:
            if ev["type"] == "CLOSE":
                all_returns.append(ev.get("indicators", {}).get("pnl_pct", 0))
    pool_sharpe = float(np.mean(all_returns) / np.std(all_returns)) if len(all_returns) > 1 and np.std(all_returns) > 0 else 0
    total_trades = len(all_returns)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Struct V4 SHORT MTF Review - {acct}</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }}
table {{ border-collapse: collapse; margin-bottom: 30px; background: #161b22; width: 100%; }}
th, td {{ padding: 6px 12px; border: 1px solid #30363d; font-size: 12px; text-align: right; }}
th {{ background: #21262d; color: #da7b26; text-align: left; }}
td:first-child {{ font-weight: bold; text-align: left; }}
tr.catastrophic td {{ background: #3d1114; color: #f85149; }}
tr.loser td {{ background: #2d1b1b; color: #ffa198; }}
tr.warn td {{ background: #2d2400; color: #d29922; }}
tr.good td {{ background: #0d2818; color: #56d364; }}
tr.great td {{ background: #0a4420; color: #3fb950; font-weight: bold; }}
.section {{ margin: 40px 0; }}
.chart {{ margin-bottom: 25px; background: #161b22; padding: 12px; border-radius: 8px; border: 1px solid #30363d; }}
.chart img {{ max-width: 100%; height: auto; border-radius: 4px; }}
.chart .meta {{ font-size: 11px; color: #8b949e; margin-bottom: 8px; display: flex; gap: 20px; flex-wrap: wrap; }}
.chart .meta span {{ white-space: nowrap; }}
h1 {{ color: #da7b26; }}
h2 {{ color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
.summary {{ background: #161b22; padding: 20px; border-radius: 8px; margin-bottom: 30px; border: 1px solid #30363d; }}
.summary .big {{ font-size: 24px; color: #da7b26; font-weight: bold; }}
.summary .stat {{ display: inline-block; margin-right: 30px; }}
.summary .label {{ font-size: 11px; color: #8b949e; }}
.legend {{ display: flex; gap: 20px; margin: 15px 0; font-size: 12px; }}
.legend span {{ display: flex; align-items: center; gap: 5px; }}
.legend .dot {{ width: 12px; height: 12px; border-radius: 2px; display: inline-block; }}
</style></head><body>
<h1>Struct V4 SHORT "{acct}" - Multi-TF Trade Review</h1>
<div class="summary">
  <div class="stat"><div class="big">{pool_sharpe:+.4f}</div><div class="label">Pool Sharpe</div></div>
  <div class="stat"><div class="big">{total_trades:,}</div><div class="label">Total Trades</div></div>
  <div class="stat"><div class="big">{len(rows)}</div><div class="label">Symbols</div></div>
  <div class="stat"><div class="big">{sum(1 for r in rows if r['ratio']>=4)}</div><div class="label">Cleared 4xBH</div></div>
  <div class="stat"><div class="big">{sum(1 for r in rows if r['compound']<1.0)}</div><div class="label">Losers</div></div>
  <div class="stat"><div class="big">{np.median([r['ratio'] for r in rows if np.isfinite(r['ratio'])]):.2f}x</div><div class="label">Median xBH</div></div>
</div>
<div class="legend">
  <span><span class="dot" style="background:#da7b26"></span> Short entry (orange dashed)</span>
  <span><span class="dot" style="background:#3fb950"></span> Profitable cover (green dashed)</span>
  <span><span class="dot" style="background:#f85149"></span> Loss cover (red dashed)</span>
  <span><span class="dot" style="background:#0d4420"></span> In-position (green shade = short profit)</span>
  <span><span class="dot" style="background:#4d1114"></span> In-position (red shade = short loss)</span>
</div>
<h2>Full table - sorted by xBH (worst first)</h2>
<table>
<tr><th>Symbol</th><th>ShortBHx</th><th>Compx</th><th>xBH</th><th>Trades</th><th>Pyramids</th><th>WR%</th><th>Avg%/tr</th><th>Best%</th><th>Worst%</th></tr>
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

    html += '<div class="section"><h2>Charts - 10 Worst + 10 Mid + 10 Best (4h / 1h / 15m)</h2>\n'
    for i, r in enumerate(chart_rows):
        section_label = ""
        if i == 0:
            section_label = "<h2 style='color:#f85149'>WORST SHORT PERFORMERS</h2>"
        elif i == 10:
            section_label = "<h2 style='color:#d29922'>MIDDLE OF PACK</h2>"
        elif i == 20:
            section_label = "<h2 style='color:#3fb950'>BEST SHORT PERFORMERS</h2>"
        html += section_label
        print(f"  Charting {r['sym']} SHORT ({i+1}/{len(chart_rows)})...")
        b64 = make_mtf_chart(r["sym"], mode, r["events"], start_ts)
        if not b64:
            html += f'<div class="chart"><p>{r["sym"]} - no chart data</p></div>\n'
            continue
        meta = (f"<span>Trades={r['trades']}</span>"
                f"<span>Pyramids={r['pyramids']}</span>"
                f"<span>WR={r['wr']:.0f}%</span>"
                f"<span>Avg={r['avg_pnl']:+.2f}%/tr</span>"
                f"<span>Compound={r['compound']:.2f}x</span>"
                f"<span>ShortB&H={r['bh_mult']:.2f}x</span>"
                f"<span><b>xBH={r['ratio']:.2f}</b></span>")
        html += (f'<div class="chart">'
                 f'<div class="meta">{meta}</div>'
                 f'<img src="data:image/png;base64,{b64}"></div>\n')
    html += "</div>\n</body></html>"

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"\nDone! {out} ({len(html)//1024}KB)")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--acct", default="v24_short_crypto")
    ap.add_argument("--mode", default="crypto")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--out", default="charts/struct_v4_review/v24_short_crypto.html")
    args = ap.parse_args()
    generate_html(args.acct, args.mode, args.start, args.out)
