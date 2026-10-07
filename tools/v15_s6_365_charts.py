#!/usr/bin/env python3
"""Zoomable hires charts for s6 365D+GS finals (USER 2026-10-07).

For every data/s6_365/<SS>_gs.json with best_overrides, render the GS-winner
config (the improved 30D + its 365D) via tools/opt/hires_chart.generate_hires:
  <SS>_bh<X>_gain<Y>_30D_S6GS_REAL_ZOOMABLE.html    (improved 30D)
  <SS>_bh<X>_gain<Y>_365D_S6GS_REAL_ZOOMABLE.html   (winner on 365D)
into data/s6_365/charts/ (S1->Mac via tools/sync_s1_to_mac.sh S6_365 section).

Honesty: filenames come from the chart's OWN recomputed header (single eval per
window), never copied from gs.json. 30D header gain is cross-checked against
gs.json after.gain (S6 streamed the same NPZ bytes from S1) and drift >0.5pp is
logged. Incremental: skips a window when its chart is newer than the gs.json.

Usage (S1 only): .venv/bin/python -u tools/v15_s6_365_charts.py [--limit N] [--symside X] [--windows 30,365]
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
S6DIR = ROOT / "data" / "s6_365"
CHARTDIR = S6DIR / "charts"
HEADER_RE = re.compile(r"gain ([\-\d\.]+)% &nbsp; BH ([\-\d\.]+)%")

from tools.v15_final_naming import fmt_pct2  # noqa: E402


def _hires_targets(out_name):
    return [ROOT / "data" / "reports" / "charts_1Y" / out_name, ROOT / "data" / "reports" / "charts_1M" / out_name, ROOT / "SPREADSHEETS" / out_name]


def _existing(ss, window):
    return sorted(CHARTDIR.glob(f"{ss}_bh*_gain*_{window}D_S6GS_REAL_ZOOMABLE.html"))


def main(argv):
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    only = argv[argv.index("--symside") + 1] if "--symside" in argv else None
    windows = [int(x) for x in (argv[argv.index("--windows") + 1].split(",") if "--windows" in argv else ["30", "365"])]
    from tools.opt.hires_chart import generate_hires
    CHARTDIR.mkdir(parents=True, exist_ok=True)
    sides = sorted(p.stem[:-3] for p in S6DIR.glob("*_gs.json"))
    if only:
        sides = [s for s in sides if s == only]
    if limit:
        sides = sides[:limit]
    ok = skip = fail = 0
    t0 = time.time()
    for i, ss in enumerate(sides):
        gp = S6DIR / f"{ss}_gs.json"
        tag = f"[{i + 1}/{len(sides)}] {ss}"
        try:
            gs = json.loads(gp.read_text())
        except Exception as e:
            print(f"{tag} SKIP unreadable-gs {str(e)[:80]}", flush=True)
            skip += 1
            continue
        ov = gs.get("best_overrides")
        if not isinstance(ov, dict) or not ov:
            print(f"{tag} SKIP no-best-overrides", flush=True)
            skip += 1
            continue
        after = gs.get("after") or {}
        gsm = gp.stat().st_mtime
        for w in windows:
            wtag = f"{tag} {w}D"
            cur = _existing(ss, w)
            if cur and max(p.stat().st_mtime for p in cur) >= gsm:
                print(f"{wtag} SKIP chart-current {cur[-1].name}", flush=True)
                skip += 1
                continue
            tmp = f"S6GS_TMP_{ss}_{w}d.html"
            try:
                out = generate_hires(ss, dict(ov), w, out_name=tmp)
                html = Path(out).read_text()
            except Exception as e:
                print(f"{wtag} FAIL hires-raised {type(e).__name__}: {str(e)[:120]}", flush=True)
                fail += 1
                continue
            m = HEADER_RE.search(html)
            if not m:
                print(f"{wtag} FAIL header-unparsed (0-trade or degenerate eval)", flush=True)
                for t in _hires_targets(tmp):
                    try:
                        t.unlink()
                    except Exception:
                        pass
                fail += 1
                continue
            gain, bh = float(m.group(1)), float(m.group(2))
            final = f"{ss}_bh{fmt_pct2(bh)}_gain{fmt_pct2(gain)}_{w}D_S6GS_REAL_ZOOMABLE.html"
            for t in _hires_targets(tmp):
                try:
                    if t.exists():
                        t.replace(t.with_name(final))
                except Exception:
                    pass
            try:
                src = ROOT / "data" / "reports" / "charts_1Y" / final
                if not src.exists():
                    src = ROOT / "data" / "reports" / "charts_1M" / final
                shutil.copy2(src, CHARTDIR / final)
            except Exception as e:
                print(f"{wtag} FAIL copy {str(e)[:100]}", flush=True)
                fail += 1
                continue
            for stale in cur:
                if stale.name != final:
                    try:
                        stale.unlink()
                    except Exception:
                        pass
            note = ""
            if w == 30 and after.get("gain") is not None:
                try:
                    drift = abs(float(gain) - float(after["gain"]))
                    note = f" s6drift={drift:.2f}pp"
                    if drift > 0.5:
                        note += " WARN>0.5"
                except Exception:
                    pass
            print(f"{wtag} OK -> {final}{note} ({time.time() - t0:.0f}s elapsed)", flush=True)
            ok += 1
    print(f"[s6-charts] done ok={ok} skip={skip} fail={fail} sides={len(sides)} elapsed={time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv)
