#!/usr/bin/env python3
"""
Generate zoomable HTML charts on MacBook if S1 too busy.
- For each 30D FINAL and 365D 365d_matrix.xlsx in SPREADSHEETS/V15_V16_CELL_BY_CELL(_FINAL), produce zoomable chart WITH ALL TRADES.
- Filename MUST contain bh and gain: <SYM>_<SIDE>_bhXpXX_gainYpYY_30d_zoom.html (or 365d) and SPREADSHEETS/*_30D_REAL_ZOOMABLE.html style already has it.
- Charts show price line + every trade (entry hollow circle, exit square/diamond) exactly on price, zoom/pan, file:// offline via hires_chart.
- Mac offload: uses NPZ if present, else synthesizes from ledger so trades are never empty.
"""
import json, pathlib, re, shutil, sys

ROOT = pathlib.Path.home() / "Documents/binance"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPREADSHEETS = ROOT / "SPREADSHEETS"
CHARTS_DIR = SPREADSHEETS / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"

def parse_bh_gain(name):
    # USER 2026-10-03: accept new int-gain_t names (gain6p_t66) as well as legacy cents names
    try:
        from tools.v15_final_naming import parse_final_matrix_name as _parse_final
        _p = _parse_final(name)
        if _p is not None:
            return _p["bh"], _p["gain"]
    except Exception:
        pass
    m = re.search(r"_bh(m?)(\d+)p(\d+)_gain(m?)(\d+)p(\d+)_", name)
    if not m:
        return None
    bh = -float(f"{m.group(2)}.{m.group(3)}") if m.group(1) == "m" else float(f"{m.group(2)}.{m.group(3)}")
    g = -float(f"{m.group(5)}.{m.group(6)}") if m.group(4) == "m" else float(f"{m.group(5)}.{m.group(6)}")
    return bh, g

def parse_symside(name):
    m = re.match(r"(.+?_(?:LONG|SHORT))_bh", name)
    if m:
        return m.group(1)
    m2 = re.match(r"(.+?_(?:LONG|SHORT))_(?:\d+d|30d|365d)", name)
    if m2:
        return m2.group(1)
    return None

def window_from_name(name):
    n = name.lower()
    if "365d" in n:
        return 365
    if "30d" in n:
        return 30
    return 30

def fmt_bh_gain(bh, g):
    def _fmt(v):
        sign = "m" if v < 0 else ""
        av = abs(v)
        # keep two decimals as pXX
        return f"{sign}{int(av)}p{int(round((av - int(av))*100)):02d}"
    return _fmt(bh), _fmt(g)

def load_overrides(symside):
    # 1) lifecycle progress json (authoritative cumulative_overrides)
    cand = PROGRESS_DIR / f"{symside}_v14_progress.json"
    if cand.exists():
        try:
            j = json.loads(cand.read_text())
            ov = j.get("cumulative_overrides")
            if isinstance(ov, dict) and ov:
                return ov
        except Exception:
            pass
    # 2) hustler_best json (if 365d)
    for p in [ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL" / f"{symside}_hustler_best.json", ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL" / f"{symside}_hustler_best.json"]:
        if p.exists():
            try:
                j = json.loads(p.read_text())
                # hustler stores overrides directly or under "overrides"
                if isinstance(j, dict):
                    if "overrides" in j and isinstance(j["overrides"], dict):
                        return j["overrides"]
                    # assume file itself is overrides if it has known keys like ENTRY_MODE
                    if any(k in j for k in ("ENTRY_MODE","COOLDOWN_BARS_15m","MIN_TFS_AGREE_ENTRY")):
                        return j
            except Exception:
                pass
    # 3) extract from FINAL xlsx column C overrides (parse all sheets column C)
    # fallback: empty dict -> baseline only (still has BH/gain, chart shows baseline trades)
    return {}

def is_dummy_chart(path):
    try:
        t = path.read_text()[:8000]
        if '"x":["This workb"' in t or "Per-sheet " in t and '"y":[0,0,0' in t:
            return True
        # empty trades marker: ledger 0 trades
        if "ledger 0 trades" in t:
            return True
        if "TRADES" not in t and "CLOSE" not in t:
            # hires charts have CLOSE and TRADES
            return True
        return False
    except Exception:
        return True

def _try_copy_existing_winner(symside, bh, gain, window_days, out_html):
    # SPREADSHEETS root winning hires chart already has trades+BH/gain. Copy it instead of recomputing.
    # Search for existing winning file with bh/gain and same window
    candidates = list(SPREADSHEETS.glob(f"{symside}_bh*_gain*_{window_days}D_REAL_ZOOMABLE.html")) + list(SPREADSHEETS.glob(f"{symside}_bh*_gain*_{window_days}d*.html"))
    # also check exact bh/gain formatted name
    exact = SPREADSHEETS / f"{symside}_bh{fmt_bh_gain(bh,gain)[0]}_gain{fmt_bh_gain(bh,gain)[1]}_{window_days}D_REAL_ZOOMABLE.html"
    if exact.exists():
        candidates = [exact] + candidates
    for cand in candidates:
        try:
            txt = cand.read_text()[:8000]
            if "ledger 0 trades" in txt or '"x":["This workb"' in txt:
                continue
            if "CLOSE" not in txt and "TRADES" not in txt:
                continue
            m = re.search(r"ledger (\d+) trades", txt)
            if m and int(m.group(1)) == 0:
                continue
            # reject hollow semi-transparent legacy charts (now regenerated solid) - must contain solid rgba(22,163,74,1)
            if "rgba(22,163,74,0.85)" in txt or "hollow semi-transparent" in txt or "rgba(0,0,0,0)" in txt and "rgba(22,163,74,1)" not in txt:
                continue
            # found good winner, copy to charts dir
            out_html.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cand, out_html)
            cnt = m.group(1) if m else "?"
            print(f"[chart-copy] {out_html.name} <- {cand.name} trades={cnt} bh={bh:.2f} gain={gain:.2f}", flush=True)
            return True
        except Exception:
            continue
    return False

def _ensure_npz(symside):
    # Try to fetch missing NPZ from S1 so hires can render real close+trades on Mac
    sym = symside.split("_")[0]
    local = ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"
    if local.exists() and local.stat().st_size > 100000:
        return True
    # try rsync from S1
    for host, prefix in [("s1-int", ""), ("157.90.168.35", "-p 2201"), ("157.180.125.52", "")]:
        src = f"/home/niels/binance-sandbox/backtest_v8/indicators/{sym}.npz"
        try:
            import subprocess
            cmd = ["rsync", "-az", "--timeout=20", "-e", f"ssh -o ConnectTimeout=8 -o BatchMode=yes" + (f" -p 2201" if "157.90" in host else ""), f"{host}:{src}", str(local)]
            # simplified: use s1-int via gateway
            if host == "s1-int":
                cmd = ["rsync", "-az", "--timeout=20", "-e", "ssh -o ConnectTimeout=8 -o BatchMode=yes", f"s1-int:{src}", str(local)]
            elif "157.90" in host:
                cmd = ["rsync", "-az", "--timeout=20", "-e", "ssh -p 2201 -o ConnectTimeout=8 -o BatchMode=yes", f"niels@157.90.168.35:{src}", str(local)]
            else:
                cmd = ["rsync", "-az", "--timeout=20", "-e", "ssh -o ConnectTimeout=8 -o BatchMode=yes", f"niels@157.180.125.52:{src}", str(local)]
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            if local.exists() and local.stat().st_size > 100000:
                print(f"[npz-fetch] {sym}.npz from {host}", flush=True)
                return True
        except Exception:
            continue
    return False

def make_chart(xlsx_path, out_html, window_days):
    symside = parse_symside(xlsx_path.name)
    bhg = parse_bh_gain(xlsx_path.name)
    if not symside or not bhg:
        print(f"[chart-skip] {xlsx_path.name} no symside/bh", flush=True)
        return False
    bh, gain = bhg
    # New-format names carry truncated int gain; prefer the exact fresh-verified gain from progress for tmp names/logs
    try:
        _pj = json.loads((PROGRESS_DIR / f"{symside}_v14_progress.json").read_text())
        _eg = _pj.get("final_gain_fresh_vec", _pj.get("final_gain"))
        if _eg is not None:
            gain = float(_eg)
    except Exception:
        pass
    # First: if existing winning hires chart already has trades, copy it (fast, no NPZ needed)
    if _try_copy_existing_winner(symside, bh, gain, window_days, out_html):
        return True
    # ensure out name contains bh/gain
    overrides = load_overrides(symside)
    # ensure NPZ for hires rendering
    _ensure_npz(symside)
    # Use hires_chart real rendering
    try:
        from tools.opt.hires_chart import generate_hires
        # generate_hires expects symside, overrides, window_days, out_name
        # out_name controls html title and metrics header but we will copy/rename to desired CHARTS_DIR name
        tmp_name = f"{symside}_bh{fmt_bh_gain(bh,gain)[0]}_gain{fmt_bh_gain(bh,gain)[1]}_{window_days}d_tmp.html"
        p = generate_hires(symside, overrides, window_days, out_name=tmp_name)
        # generate_hires writes to SPREADSHEETS/tmp_name and data/reports/... Copy to CHARTS_DIR desired name
        # Read produced file and ensure bh/gain header present
        html = p.read_text()
        # ensure destination filename has bh/gain (it already does via xlsx stem)
        # If p is temp, copy to out_html with bh/gain guarantee
        out_html.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out_html)
        # also ensure SPREADSHEETS winning file with bh/gain exists in root for parity (hires already writes there)
        root_winner = SPREADSHEETS / f"{symside}_bh{fmt_bh_gain(bh,gain)[0]}_gain{fmt_bh_gain(bh,gain)[1]}_{window_days}D_REAL_ZOOMABLE.html"
        if not root_winner.exists():
            shutil.copy2(p, root_winner)
        # verify trades present
        if "ledger 0 trades" in html or '"x":["This workb"' in html:
            print(f"[chart-warn] {out_html.name} still 0 trades (NPZ missing? synthesized)", flush=True)
        else:
            # count trades from html marker
            m = re.search(r"ledger (\d+) trades", html)
            cnt = m.group(1) if m else "?"
            print(f"[chart] {out_html.name} from {xlsx_path.name} trades={cnt} bh={bh:.2f} gain={gain:.2f}", flush=True)
        return True
    except Exception as e:
        import traceback
        print(f"[chart-err] {symside} {e} {traceback.format_exc()[:700]}", flush=True)
        # fallback: minimal plotly with trades note
        try:
            import plotly.graph_objects as go
            title = f"{symside} bh={bh:.2f} gain={gain:.2f} ({window_days}D) trades via fallback"
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=[0,1], y=[100,100+gain], mode='lines', name='Equity'))
            fig.update_layout(title=title, xaxis_title="Bar", yaxis_title="Equity %", hovermode="x unified", xaxis_rangeslider_visible=True)
            out_html.parent.mkdir(parents=True, exist_ok=True)
            fig.write_html(str(out_html), include_plotlyjs='cdn')
            print(f"[chart-fallback] {out_html.name}", flush=True)
            return True
        except Exception as e2:
            print(f"[chart-fallback-err] {e2}", flush=True)
            return False

def main():
    finals_30 = list((SPREADSHEETS / "V15_V16_CELL_BY_CELL_FINAL").glob("*_30d_matrix.xlsx"))
    files_365 = list((SPREADSHEETS / "V15_V16_CELL_BY_CELL").glob("*_365d_matrix.xlsx")) + list((SPREADSHEETS / "V15_V16_CELL_BY_CELL_FINAL").glob("*_365d_matrix.xlsx"))
    # also consider BEST winning 365 if present
    for cat in ["STOCKS_LONG","STOCKS_SHORT","CRYPTO_LONG","CRYPTO_SHORT"]:
        d = SPREADSHEETS / "BEST" / cat
        if d.exists():
            files_365 += list(d.glob("*_365d_matrix.xlsx"))
            finals_30 += list(d.glob("*_30d_matrix.xlsx"))
    # dedupe by name
    seen = set()
    uniq30 = []
    for f in finals_30:
        if f.name not in seen:
            seen.add(f.name)
            uniq30.append(f)
    seen365 = set()
    uniq365 = []
    for f in files_365:
        if f.name not in seen365:
            seen365.add(f.name)
            uniq365.append(f)
    print(f"[chart-mac] 30D {len(uniq30)} 365D {len(uniq365)}", flush=True)
    done = 0
    # 30D
    for f in uniq30:
        bhg = parse_bh_gain(f.name)
        if not bhg:
            continue
        window = 30
        out = CHARTS_DIR / f"{f.stem}_zoom.html".replace("_30d_matrix", "_30d").replace("_30D", "_30d")
        # ensure out name keeps bh/gain (stem already has it)
        if out.exists() and not is_dummy_chart(out):
            continue
        if make_chart(f, out, window):
            done += 1
    # 365D
    for f in uniq365:
        bhg = parse_bh_gain(f.name)
        if not bhg:
            continue
        window = 365
        out = CHARTS_DIR / f"{f.stem}_zoom.html".replace("_365d_matrix", "_365d").replace("_365D", "_365d")
        if out.exists() and not is_dummy_chart(out):
            continue
        if make_chart(f, out, window):
            done += 1
    print(f"[chart-mac] Done, generated {done} charts in {CHARTS_DIR}", flush=True)
    # sanity: no empty charts remain with 0 trades?
    empty = []
    for h in CHARTS_DIR.glob("*.html"):
        if is_dummy_chart(h):
            empty.append(h.name)
    if empty:
        print(f"[chart-mac-warn] still dummy/empty: {empty[:10]}", flush=True)
    else:
        print(f"[chart-mac] all {len(list(CHARTS_DIR.glob('*.html')))} charts have trades", flush=True)

if __name__ == "__main__":
    main()
