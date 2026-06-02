import json
import os
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
CACHE_FILE = BASE / "data" / "tradier_exchange_cache.json"
LONG_FILE = BASE / "symbols_trb_long.json"
SHORT_FILE = BASE / "symbols_trb_short.json"
BRIEF_FILE = BASE / "data" / "tv_morning_brief.json"
OUT_FILE = Path("/Users/niels/Desktop/TRB_Charts_Dashboard.html")

def build_wlt_list(symbols, cache):
    wlt = []
    for s in symbols:
        exch = cache.get(s, "")
        wlt.append(f"{exch}:{s}" if exch else s)
    return wlt

def get_per_sym_html(sym, side):
    cand_path = BASE / "data" / "hourly_reconfig" / "trb" / "_candidates" / f"{sym}_{side.upper()}.json"
    if not cand_path.exists():
        return '<div class="settings-content"><div class="settings-empty">No per_sym overrides found</div></div>'
    try:
        data = json.loads(cand_path.read_text())
        metrics = data.get("metrics", {})
        overrides = data.get("recommended_overrides", {})
        ovr_list = [f"{k}={v}" for k, v in overrides.items() if k not in ("LONG_ENABLED", "SHORT_ENABLED")]
        ovr_str = ", ".join(ovr_list) if ovr_list else "None (default)"
        sharpe = metrics.get("recommended_oos_sharpe", 0.0)
        trades = metrics.get("recommended_oos_trades", 0)
        wr = metrics.get("recommended_oos_win_rate_pct", 0.0)
        dd = metrics.get("max_dd_pct", 0.0)
        return f'<div class="settings-content"><div class="settings-metrics"><span>OOS Sharpe: <strong>{sharpe:+.2f}</strong></span><span>Trades: <strong>{trades}</strong></span><span>WR: <strong>{wr:.1f}%</strong></span><span>MaxDD: <strong>{dd:.1f}%</strong></span></div><div class="settings-overrides"><span>Overrides: <code>{ovr_str}</code></span></div></div>'
    except Exception as e:
        return f'<div class="settings-content"><div class="settings-empty">Error: {e}</div></div>'

def render_grid(symbols, grid_id, side):
    if not symbols:
        return '<div class="empty-state">No symbols loaded for this category today.</div>'
    cards = []
    for full_sym in symbols:
        exch, sym = full_sym.split(":") if ":" in full_sym else ("NASDAQ", full_sym)
        settings_html = get_per_sym_html(sym, side)
        card = f"""
        <div class="chart-card">
            <div class="chart-header">
                <span class="chart-symbol">{sym}</span>
                <span class="chart-exch">{exch}</span>
            </div>
            {settings_html}
            <div class="chart-container">
                <div id="tv-widget-{grid_id}-{sym}" style="width: 100%; height: 100%;"></div>
                <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
                <script type="text/javascript">
                new TradingView.widget({{
                  "width": "100%",
                  "height": "100%",
                  "symbol": "{exch}:{sym}",
                  "interval": "15",
                  "timezone": "exchange",
                  "theme": "dark",
                  "style": "1",
                  "locale": "en",
                  "enable_publishing": false,
                  "hide_side_toolbar": false,
                  "allow_symbol_change": true,
                  "container_id": "tv-widget-{grid_id}-{sym}",
                  "hide_top_toolbar": false,
                  "withdateranges": true,
                  "save_image": false,
                  "studies": []
                }});
                </script>
            </div>
        </div>"""
        cards.append(card)
    return f'<div id="{grid_id}" class="dashboard-grid">' + "\n".join(cards) + '</div>'

def main():
    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    long_syms = json.loads(LONG_FILE.read_text())
    short_syms = json.loads(SHORT_FILE.read_text())
    brief = {}
    if BRIEF_FILE.exists():
        try:
            brief = json.loads(BRIEF_FILE.read_text())
        except Exception:
            pass
    buys = [s["symbol"] for s in brief.get("top_buys", [])]
    shorts = [s["symbol"] for s in brief.get("top_shorts", [])]
    long_all = build_wlt_list(long_syms, cache)
    short_all = build_wlt_list(short_syms, cache)
    buy_list = build_wlt_list(buys, cache)
    short_list = build_wlt_list(shorts, cache)
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TRB Charts Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: #0a0b0d;
            --bg-secondary: #131722;
            --border-color: #2a2e39;
            --text-primary: #f2f5f8;
            --text-secondary: #848e9c;
            --accent-green: #0cf;
            --accent-red: #f12c56;
            --gradient: linear-gradient(135deg, #131722, #0d0f14);
            --glow-green: 0 0 15px rgba(0, 204, 255, 0.2);
            --glow-red: 0 0 15px rgba(241, 44, 86, 0.2);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            padding: 24px;
            overflow-x: hidden;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 24px;
        }}
        .header-title {{
            font-size: 24px;
            font-weight: 800;
            background: linear-gradient(90deg, #fff, var(--text-secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .nav-tabs {{
            display: flex;
            gap: 12px;
            overflow-x: auto;
            padding-bottom: 4px;
        }}
        .tab-btn {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 10px 18px;
            border-radius: 30px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        .tab-btn:hover {{
            color: var(--text-primary);
            border-color: var(--text-secondary);
        }}
        .tab-btn.active {{
            background: #fff;
            color: var(--bg-primary);
            border-color: #fff;
            box-shadow: 0 4px 15px rgba(255, 255, 255, 0.25);
        }}
        .controls {{
            display: flex;
            gap: 16px;
            align-items: center;
        }}
        .grid-selector {{
            display: flex;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            padding: 4px;
            border-radius: 8px;
        }}
        .grid-btn {{
            background: transparent;
            border: none;
            color: var(--text-secondary);
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.2s;
        }}
        .grid-btn.active {{
            background: var(--border-color);
            color: var(--text-primary);
        }}
        .dashboard-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
            transition: grid-template-columns 0.3s ease;
        }}
        .chart-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            height: 650px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
            transition: transform 0.25s ease, border-color 0.25s ease;
        }}
        .chart-card:hover {{
            border-color: var(--text-secondary);
        }}
        .chart-header {{
            background: rgba(255, 255, 255, 0.02);
            padding: 10px 16px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
            font-weight: 600;
        }}
        .chart-symbol {{ color: var(--text-primary); font-size: 15px; font-weight: 800; }}
        .chart-exch {{ color: var(--text-secondary); font-size: 12px; }}
        .settings-content {{
            background: rgba(255, 255, 255, 0.03);
            border-bottom: 1px solid var(--border-color);
            padding: 8px 16px;
            font-size: 12px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .settings-metrics {{
            display: flex;
            gap: 16px;
            color: var(--text-secondary);
        }}
        .settings-metrics strong {{
            color: var(--text-primary);
        }}
        .settings-overrides {{
            color: var(--text-secondary);
            text-overflow: ellipsis;
            white-space: nowrap;
            overflow: hidden;
        }}
        .settings-overrides code {{
            font-family: monospace;
            background: rgba(0,0,0,0.2);
            padding: 2px 4px;
            border-radius: 4px;
            color: #0cf;
        }}
        .settings-empty {{
            color: var(--text-secondary);
            font-style: italic;
        }}
        .chart-container {{
            flex-grow: 1;
            position: relative;
            background: #131722;
        }}
        .chart-iframe-wrapper {{
            width: 100%;
            height: 100%;
            border: none;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
        .empty-state {{
            text-align: center;
            padding: 100px 0;
            color: var(--text-secondary);
            font-size: 16px;
        }}
    </style>
</head>
<body>

    <div class="header">
        <div class="header-title">TRB CHARTS DASHBOARD</div>
        <div class="controls">
            <div class="grid-selector">
                <button class="grid-btn" onclick="setColumns(1)">1 Col</button>
                <button class="grid-btn active" onclick="setColumns(2)">2 Col</button>
                <button class="grid-btn" onclick="setColumns(3)">3 Col</button>
                <button class="grid-btn" onclick="setColumns(4)">4 Col</button>
            </div>
        </div>
    </div>

    <div style="margin-bottom: 24px;">
        <div class="nav-tabs">
            <button class="tab-btn active" onclick="switchTab('long-buys')">DAILY LONG BUYS ({len(buy_list)})</button>
            <button class="tab-btn" onclick="switchTab('short-shorts')">DAILY SHORT SHORTS ({len(short_list)})</button>
            <button class="tab-btn" onclick="switchTab('all-longs')">ALL TRB LONGS ({len(long_all)})</button>
            <button class="tab-btn" onclick="switchTab('all-shorts')">ALL TRB SHORTS ({len(short_all)})</button>
        </div>
    </div>

    <!-- DAILY LONG BUYS -->
    <div id="long-buys" class="tab-content active">
        {render_grid(buy_list, "long-buys-grid", "LONG")}
    </div>

    <!-- DAILY SHORT SHORTS -->
    <div id="short-shorts" class="tab-content">
        {render_grid(short_list, "short-shorts-grid", "SHORT")}
    </div>

    <!-- ALL TRB LONGS -->
    <div id="all-longs" class="tab-content">
        {render_grid(long_all, "all-longs-grid", "LONG")}
    </div>

    <!-- ALL TRB SHORTS -->
    <div id="all-shorts" class="tab-content">
        {render_grid(short_all, "all-shorts-grid", "SHORT")}
    </div>

    <script>
        function switchTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            event.target.classList.add('active');
            window.dispatchEvent(new Event('resize'));
        }}

        function setColumns(cols) {{
            document.querySelectorAll('.dashboard-grid').forEach(grid => {{
                grid.style.gridTemplateColumns = `repeat(${{cols}}, 1fr)`;
            }});
            document.querySelectorAll('.grid-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            window.dispatchEvent(new Event('resize'));
        }}
    </script>
</body>
</html>
"""
    OUT_FILE.write_text(html_content)
    print(f"Generated dynamic HTML dashboard at: {OUT_FILE}")

if __name__ == "__main__":
    main()
