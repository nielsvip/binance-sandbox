#!/usr/bin/env python3
"""per_sym_report.py — Generate self-contained HTML dashboard of per-symbol backtest results.

Shows:
  - Per-symbol metrics table (from per_sym_active_config.json)
  - OPT_*.png charts embedded as base64 (from plots/)
  - Key override params per symbol

Usage:
  python3 per_sym_report.py            → writes per_sym_report.html and opens in browser
  python3 per_sym_report.py --no-open  → writes HTML only
"""
from __future__ import annotations
import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACTIVE_CFG = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
PLOTS_DIR = ROOT / "plots"
OUT_HTML = ROOT / "per_sym_report.html"

KEY_OVERRIDES = [
    "ENTRY_SCORE_THRESHOLD",
    "BTC_ACCEL_RAMP_MIN_TFS",
    "BTC_BREAKOUT_ENTRY_ENABLED",
    "BTC_MIN_HOLD_BARS",
    "BTC_HARD_LOSS_USD_PER_TRADE",
    "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE",
    "BTC_TECH_EXIT_WT_MIN_TFS",
    "BTC_ENTRY_PRIMARY_REQUIRE_RZ",
    "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP",
    "WA_MIN_GAIN_PCT",
    "PYRAMID_MIN_GAIN_PCT",
    "BB_SQUEEZE_ENTRY_ENABLED",
    "BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE",
]

TIER_COLOR = {
    "PROMOTE": "#3fb950",
    "DIAGNOSTIC": "#e3b341",
    "REJECT_DD": "#f85149",
    "REJECT_WR": "#f85149",
    "REJECT_TRADES": "#f85149",
    "REJECT_PNL": "#f0883e",
}


def img_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def tier(m: dict) -> str:
    ps = float(m.get("wsharpe", 0))
    if ps >= 1.5: return "Aspirational"
    if ps >= 1.0: return "Strong"
    if ps >= 0.6: return "Best-of-current"
    if ps >= 0.3: return "Directional"
    if ps >= 0.0: return "Noise"
    return "Discard"


def generate_html() -> str:
    cfg: dict = {}
    if ACTIVE_CFG.exists():
        try:
            cfg = json.loads(ACTIVE_CFG.read_text())
        except Exception:
            pass

    # Deduplicate: one entry per symbol (LONG side drives the display)
    syms: dict[str, dict] = {}
    for k, v in cfg.items():
        sym = k.rsplit("_", 1)[0]
        if k.endswith("_LONG"):
            syms[sym] = v

    # Collect all OPT charts — keyed by symbol name
    chart_paths: dict[str, Path] = {}
    for p in sorted(PLOTS_DIR.glob("OPT_*.png")):
        sym_name = p.stem[4:]  # strip "OPT_"
        chart_paths[sym_name] = p

    all_chart_syms = sorted(chart_paths.keys())
    profiled_syms = sorted(syms.keys())
    untracked_syms = [s for s in all_chart_syms if s not in profiled_syms]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def metric_row(label: str, val: str, highlight: str = "") -> str:
        style = f" style='color:{highlight}'" if highlight else ""
        return f"<tr><td class='ml'>{label}</td><td class='mv'{style}>{val}</td></tr>"

    def sym_card(sym: str, entry: dict | None, chart_path: Path | None) -> str:
        m = entry or {}
        ovr = m.get("overrides", {})
        ps = float(m.get("wsharpe", 0))
        trades = int(m.get("trades", 0))
        t = tier(m) if m else "—"
        tier_c = "#e3b341" if ps < 0.3 else ("#3fb950" if ps >= 0.6 else "#58a6ff")

        stats_rows = ""
        if m:
            stats_rows += metric_row("pool_sharpe", f"{ps:+.4f}", tier_c)
            stats_rows += metric_row("trades", f"{trades:,}")
            stats_rows += metric_row("tier", t, tier_c)
            stats_rows += metric_row("tag", m.get("winning_tag", "—"))
            stats_rows += metric_row("sample", m.get("sample_tag", "—"))
        else:
            stats_rows = "<tr><td colspan='2' style='color:#888'>No optimized config</td></tr>"

        ovr_rows = ""
        for param in KEY_OVERRIDES:
            if param in ovr:
                v = ovr[param]
                col = ""
                if isinstance(v, bool):
                    col = "#3fb950" if v else "#f85149"
                ovr_rows += metric_row(param, str(v), col)

        img_html = ""
        if chart_path and chart_path.exists():
            b64 = img_b64(chart_path)
            img_html = f"<img src='data:image/png;base64,{b64}' style='width:100%;border-radius:4px;margin-top:8px'>"
        else:
            img_html = "<div style='color:#888;padding:20px;text-align:center'>Chart not yet generated</div>"

        return f"""
<div class='card'>
  <div class='sym-title'>{sym}</div>
  <table class='mt'>
    {stats_rows}
    {'<tr><td colspan=2><hr style="border-color:#3a3f42;margin:4px 0"></td></tr>' if ovr_rows else ''}
    {ovr_rows}
  </table>
  {img_html}
</div>"""

    # Section 1: profiled symbols
    profiled_html = "\n".join(sym_card(s, syms[s], chart_paths.get(s)) for s in profiled_syms)

    # Section 2: charts without config (ang/fin/men profiler output)
    untracked_html = "\n".join(sym_card(s, None, chart_paths.get(s)) for s in untracked_syms)

    css = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #1a1e21; color: #c0c0c0; font-family: 'SF Mono', monospace; font-size: 12px; padding: 16px; }
h1 { color: #9c864e; font-size: 18px; margin-bottom: 4px; }
h2 { color: #58a6ff; font-size: 14px; margin: 20px 0 8px; border-bottom: 1px solid #3a3f42; padding-bottom: 4px; }
.ts { color: #6e7681; font-size: 11px; margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 12px; }
.card { background: #272d30; border: 1px solid #3a3f42; border-radius: 6px; padding: 12px; }
.sym-title { color: #9c864e; font-size: 15px; font-weight: bold; margin-bottom: 8px; }
.mt { width: 100%; border-collapse: collapse; }
.ml { color: #6e7681; padding: 1px 6px 1px 0; white-space: nowrap; }
.mv { color: #c0c0c0; padding: 1px 0; word-break: break-all; }
"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset='utf-8'><title>Per-Symbol Report {now_str}</title>
<style>{css}</style></head>
<body>
<h1>Per-Symbol Backtest Report</h1>
<div class='ts'>Generated {now_str} &nbsp;·&nbsp; {len(profiled_syms)} optimized &nbsp;·&nbsp; {len(all_chart_syms)} charts</div>

<h2>BTC-Dedicated Optimized Symbols ({len(profiled_syms)})</h2>
<div class='grid'>{profiled_html}</div>

<h2>All Other OPT Charts ({len(untracked_syms)})</h2>
<div class='grid'>{untracked_html}</div>

</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    html = generate_html()
    OUT_HTML.write_text(html, encoding="utf-8")
    size_kb = OUT_HTML.stat().st_size // 1024
    print(f"Written: {OUT_HTML}  ({size_kb} KB)")
    if not args.no_open:
        try:
            subprocess.Popen(["open", str(OUT_HTML)])
        except Exception:
            pass


if __name__ == "__main__":
    main()
