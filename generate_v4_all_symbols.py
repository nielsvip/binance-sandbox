"""generate_v4_all_symbols.py — Per-symbol 15m + D trade review for ALL symbols.

Shows every symbol with 2 panels (D and 15m) and all trade entries/exits.
Sorted by xBH worst→best. Click symbol name to jump. Summary table at top.
Supports both LONG and SHORT sides.
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


def load_events(acct: str, sym: str, side: str):
    f = HISTORY_DIR / acct / f"{sym}_{side}.jsonl"
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
            reason = ev.get("reason", "")
            if cur_open_ts is not None:
                periods.append({
                    "open_ts": cur_open_ts, "close_ts": ets,
                    "entry_px": cur_entry_px, "exit_px": epx,
                    "pnl_pct": pnl, "pyramids": cur_pyramids,
                    "reason": reason,
                })
            cur_open_ts = None
    return periods


def _plot_ohlc_panel(ax, ohlc, trade_periods, tf_label, side, max_bars=600):
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
            src_s = i * step
            src_e = min(src_s + step, n)
            h_new[i] = ohlc["h"][src_s:src_e].max()
            l_new[i] = ohlc["l"][src_s:src_e].min()
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

    entry_color = "#58a6ff" if side == "LONG" else "#da7b26"
    for tp in trade_periods:
        open_dt = datetime.datetime.fromtimestamp(tp["open_ts"], tz=datetime.timezone.utc)
        close_dt = datetime.datetime.fromtimestamp(tp["close_ts"], tz=datetime.timezone.utc)
        pnl = tp["pnl_pct"]
        shade_color = "#0d4420" if pnl > 0 else "#4d1114"
        ax.axvspan(open_dt, close_dt, alpha=0.12, color=shade_color, zorder=0)
        ax.axvline(open_dt, color=entry_color, linewidth=0.6, alpha=0.5, linestyle="--", zorder=3)
        exit_color = "#3fb950" if pnl > 0 else "#f85149"
        ax.axvline(close_dt, color=exit_color, linewidth=0.6, alpha=0.5, linestyle="--", zorder=3)

    ax.set_ylabel(tf_label, fontsize=9, fontweight="bold", color="#8b949e")
    ax.tick_params(labelsize=7, colors="#8b949e")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.set_facecolor("#0d1117")
    ax.grid(True, alpha=0.1, color="#30363d")
    for spine in ax.spines.values():
        spine.set_color("#30363d")


def make_chart(sym, mode, events, start_ts, side):
    try:
        npz, ts = load_npz(sym, mode, start_ts=start_ts)
    except Exception:
        return ""
    close = np.asarray(npz["close"], dtype=np.float64)
    n = len(close)
    if n < 200:
        return ""
    trade_periods = _build_trade_periods(events)
    # D = step 78 for 5m base (stocks), step 480 for 3m base (crypto)
    # 15m = step 3 for 5m base, step 5 for 3m base
    base_mins = 3 if mode == "crypto" else 5
    step_D = int(24 * 60 / base_mins)
    step_15m = int(15 / base_mins)
    tfs = [("Daily", step_D, 500), ("15m", step_15m, 600)]

    fig, axes = plt.subplots(2, 1, figsize=(16, 5), dpi=100,
                             gridspec_kw={"hspace": 0.25, "height_ratios": [1, 1.2]})
    fig.patch.set_facecolor("#0d1117")
    for idx, (tf_label, step, max_bars) in enumerate(tfs):
        ohlc = _resample_ohlc(close, ts, step)
        if ohlc is None:
            axes[idx].text(0.5, 0.5, f"No data for {tf_label}", transform=axes[idx].transAxes,
                           ha="center", color="#8b949e")
            continue
        _plot_ohlc_panel(axes[idx], ohlc, trade_periods, tf_label, side, max_bars=max_bars)

    # Build title with per-trade breakdown
    n_trades = len(trade_periods)
    wins = sum(1 for tp in trade_periods if tp["pnl_pct"] > 0)
    losses = n_trades - wins
    total_pnl = sum(tp["pnl_pct"] for tp in trade_periods)
    total_pyrs = sum(tp["pyramids"] for tp in trade_periods)
    wr = wins / max(n_trades, 1) * 100
    # Exit path breakdown
    exit_counts = {}
    for tp in trade_periods:
        r = tp["reason"]
        exit_counts[r] = exit_counts.get(r, 0) + 1
    exit_str = " | ".join(f"{k}={v}" for k, v in sorted(exit_counts.items(), key=lambda x: -x[1]))

    fig.suptitle(f"{sym} {side}  |  {wins}W/{losses}L ({wr:.0f}%)  |  "
                 f"PnL {total_pnl:+.1f}%  |  {total_pyrs} pyr  |  {exit_str}",
                 fontsize=10, fontweight="bold", color="#c9d1d9", y=0.99)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def compute_metrics(events):
    closes = [e for e in events if e["type"] == "CLOSE"]
    augments = [e for e in events if e["type"] == "AUGMENT"]
    if not closes:
        return {"trades": 0, "wr": 0, "total_pnl": 0, "avg_pnl": 0,
                "best": 0, "worst": 0, "pyramids": 0, "compound": 1.0,
                "exits": {}}
    pnls = [c.get("indicators", {}).get("pnl_pct", 0) for c in closes]
    wins = [p for p in pnls if p > 0]
    cap = 10000.0
    for p in pnls:
        cap += cap * (p / 100)
    exits = {}
    for c in closes:
        r = c.get("reason", "?")
        exits[r] = exits.get(r, 0) + 1
    return {
        "trades": len(closes),
        "wr": len(wins) / len(closes) * 100 if closes else 0,
        "total_pnl": sum(pnls),
        "avg_pnl": float(np.mean(pnls)) if pnls else 0,
        "best": max(pnls) if pnls else 0,
        "worst": min(pnls) if pnls else 0,
        "pyramids": len(augments),
        "compound": cap / 10000.0,
        "exits": exits,
    }


def generate_html(acct, mode, start_date, side, out_path):
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    hist_dir = HISTORY_DIR / acct
    if not hist_dir.exists():
        print(f"ERROR: {hist_dir} not found")
        return
    suffix = f"_{side}.jsonl"
    syms = sorted([f.stem.replace(f"_{side}", "") for f in hist_dir.glob(f"*{suffix}")])
    print(f"Processing {len(syms)} {side} symbols from {acct}...")

    rows = []
    for sym in syms:
        events = load_events(acct, sym, side)
        m = compute_metrics(events)
        try:
            npz, _ = load_npz(sym, mode, start_ts=start_ts)
            close = np.asarray(npz["close"], dtype=np.float64)
            if side == "SHORT":
                bh_mult = (2.0 - float(close[-1]) / float(close[0])) if close[0] > 0 else 1.0
            else:
                bh_mult = float(close[-1]) / float(close[0]) if close[0] > 0 else 1.0
        except Exception:
            bh_mult = 1.0
        ratio = m["compound"] / max(bh_mult, 0.01) if bh_mult > 0 else 0
        rows.append({"sym": sym, "events": events, "bh_mult": bh_mult,
                      "ratio": ratio, **m})

    rows.sort(key=lambda r: r["ratio"])

    all_returns = []
    for r in rows:
        for ev in r["events"]:
            if ev["type"] == "CLOSE":
                all_returns.append(ev.get("indicators", {}).get("pnl_pct", 0))
    pool_sharpe = float(np.mean(all_returns) / np.std(all_returns)) if len(all_returns) > 1 and np.std(all_returns) > 0 else 0

    # Global exit breakdown
    global_exits = {}
    for r in rows:
        for k, v in r["exits"].items():
            global_exits[k] = global_exits.get(k, 0) + v
    exit_str = " | ".join(f"{k}: {v} ({v/max(len(all_returns),1)*100:.0f}%)"
                          for k, v in sorted(global_exits.items(), key=lambda x: -x[1]))

    side_color = "#58a6ff" if side == "LONG" else "#da7b26"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>V24 {side} All Symbols — {acct}</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }}
table {{ border-collapse: collapse; margin-bottom: 20px; background: #161b22; width: 100%; }}
th, td {{ padding: 4px 10px; border: 1px solid #30363d; font-size: 11px; text-align: right; }}
th {{ background: #21262d; color: {side_color}; text-align: left; position: sticky; top: 0; z-index: 10; }}
td:first-child {{ font-weight: bold; text-align: left; }}
td:first-child a {{ color: {side_color}; text-decoration: none; }}
td:first-child a:hover {{ text-decoration: underline; }}
tr.catastrophic td {{ background: #3d1114; color: #f85149; }}
tr.loser td {{ background: #2d1b1b; color: #ffa198; }}
tr.warn td {{ background: #2d2400; color: #d29922; }}
tr.good td {{ background: #0d2818; color: #56d364; }}
tr.great td {{ background: #0a4420; color: #3fb950; font-weight: bold; }}
.chart {{ margin-bottom: 15px; background: #161b22; padding: 8px; border-radius: 6px; border: 1px solid #30363d; }}
.chart img {{ max-width: 100%; height: auto; }}
h1 {{ color: {side_color}; }}
h2 {{ color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
.summary {{ background: #161b22; padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #30363d; }}
.summary .big {{ font-size: 22px; color: {side_color}; font-weight: bold; }}
.summary .stat {{ display: inline-block; margin-right: 25px; }}
.summary .label {{ font-size: 10px; color: #8b949e; }}
.exits {{ font-size: 12px; color: #8b949e; margin: 10px 0; }}
</style></head><body>
<h1>V24 {side} — {acct} — All {len(rows)} Symbols (D + 15m)</h1>
<div class="summary">
  <div class="stat"><div class="big">{pool_sharpe:+.4f}</div><div class="label">Pool Sharpe</div></div>
  <div class="stat"><div class="big">{len(all_returns):,}</div><div class="label">Trades</div></div>
  <div class="stat"><div class="big">{len(rows)}</div><div class="label">Symbols</div></div>
  <div class="stat"><div class="big">{sum(1 for r in rows if r['ratio']>=4)}</div><div class="label">4xBH</div></div>
  <div class="stat"><div class="big">{sum(1 for r in rows if r['compound']<1.0)}</div><div class="label">Losers</div></div>
  <div class="stat"><div class="big">{sum(1 for r in all_returns if r > 0)/max(len(all_returns),1)*100:.1f}%</div><div class="label">WR</div></div>
  <div class="stat"><div class="big">{np.median([r['ratio'] for r in rows if np.isfinite(r['ratio'])]):.1f}x</div><div class="label">Med xBH</div></div>
</div>
<div class="exits"><b>Exit paths:</b> {exit_str}</div>
<h2>All symbols — sorted by xBH (worst → best)</h2>
<table>
<tr><th>Symbol</th><th>BHx</th><th>Comp</th><th>xBH</th><th>Trades</th><th>Pyr</th><th>WR%</th><th>Avg%</th><th>Best%</th><th>Worst%</th><th>Exits</th></tr>
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
        exit_brief = ", ".join(f"{k}:{v}" for k, v in sorted(r["exits"].items(), key=lambda x: -x[1])[:3])
        html += (f"<tr class='{cls}'><td><a href='#sym-{r['sym']}'>{r['sym']}</a></td>"
                 f"<td>{r['bh_mult']:.2f}</td><td>{r['compound']:.2f}</td>"
                 f"<td>{ratio:.1f}</td><td>{r['trades']}</td><td>{r['pyramids']}</td>"
                 f"<td>{r['wr']:.0f}</td><td>{r['avg_pnl']:+.2f}</td>"
                 f"<td>{r['best']:+.1f}</td><td>{r['worst']:+.1f}</td>"
                 f"<td style='font-size:10px;color:#8b949e'>{exit_brief}</td></tr>\n")
    html += "</table>\n<h2>Per-Symbol Charts (D + 15m)</h2>\n"

    for idx, r in enumerate(rows):
        print(f"  [{idx+1}/{len(rows)}] {r['sym']} {side}...")
        b64 = make_chart(r["sym"], mode, r["events"], start_ts, side)
        if not b64:
            html += f'<div class="chart" id="sym-{r["sym"]}"><p>{r["sym"]} — no data</p></div>\n'
            continue
        html += (f'<div class="chart" id="sym-{r["sym"]}">'
                 f'<img src="data:image/png;base64,{b64}"></div>\n')

    html += "</body></html>"
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"\nDone! {out} ({len(html)//1024}KB)")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--acct", required=True)
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--side", default="LONG", choices=["LONG", "SHORT"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    generate_html(args.acct, args.mode, args.start, args.side, args.out)
