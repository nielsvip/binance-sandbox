#!/usr/bin/env python3
"""v15_html_parity_vlines — overlay live-vs-backtest mismatch vlines on a matrix HTML.

USER 2026-10-24: on hires matrix charts (Chart.js, TRADES/EVENTS/LABELS embedded),
draw slightly reddish vertical lines where backtest trades have no live fill and
slightly greenish ones where live fills have no backtest trade. Also populates the
chart's empty LIVE entry/exit datasets from /history/ so live fills show natively.

Matching is event-level, greedy in time order, tolerance --tol-min (default 30 =
v15_trade_parity default). A --tol2-min sensitivity recount is reported, never drawn.

Writes SIBLING {stem}.parity.html — the input HTML is never modified.
"""
import argparse
import bisect
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OPEN_TOKENS = ("OPEN", "AUGMENT", "ADD", "REOPEN", "BUY")
CLOSE_TOKENS = ("REDUCE", "CLOSE", "SELL", "EXIT", "COVER", "TAKE")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("html", help="matrix HTML with embedded LABELS/TRADES")
    p.add_argument("--acct", default="trb")
    p.add_argument("--tol-min", type=float, default=30.0)
    p.add_argument("--tol2-min", type=float, default=60.0)
    p.add_argument("--out", default=None)
    return p.parse_args()


def extract_js_array(text, name):
    m = re.search(r"const %s = (\[.*?\]);" % re.escape(name), text, re.DOTALL)
    if not m:
        raise SystemExit(f"anchor const {name} not found")
    return json.loads(m.group(1))


def to_utc(s):
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_live(acct, sym, side):
    seen = set()
    events = []
    for root in [ROOT / "data" / "tradier" / "history", ROOT / "data" / "history"]:
        fpath = root / acct / f"{sym}_{side}.jsonl"
        if not fpath.is_file():
            continue
        for line in fpath.read_text().splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            try:
                ts = to_utc(rec.get("ts"))
            except Exception:
                continue
            typ = str(rec.get("type") or "").upper()
            key = (ts.isoformat(), typ)
            if key in seen:
                continue
            seen.add(key)
            if any(k in typ for k in OPEN_TOKENS):
                kind = "open"
            elif any(k in typ for k in CLOSE_TOKENS):
                kind = "close"
            else:
                continue
            events.append({"ts": ts, "kind": kind, "type": typ,
                           "reason": str(rec.get("reason") or "")[:80],
                           "price": rec.get("price"), "qty": rec.get("qty")})
    events.sort(key=lambda e: e["ts"])
    return events


def greedy_match(vec, live, tol_s):
    matched_v, matched_l = set(), set()
    for i, vt in enumerate(vec):
        best, best_dt = None, tol_s + 1
        for j, lt in enumerate(live):
            if j in matched_l:
                continue
            dt = abs((lt - vt).total_seconds())
            if dt <= tol_s and dt < best_dt:
                best, best_dt = j, dt
        if best is not None:
            matched_v.add(i)
            matched_l.add(best)
    return matched_v, matched_l


def main():
    args = parse_args()
    html_path = Path(args.html)
    text = html_path.read_text()
    m = re.match(r"(.+)_([A-Z]+)_.*_matrix\.html", html_path.name)
    if not m:
        raise SystemExit(f"cannot parse SYM_SIDE from {html_path.name}")
    sym, side = m.group(1), m.group(2)
    labels = extract_js_array(text, "LABELS")
    trades = extract_js_array(text, "TRADES")
    closes = extract_js_array(text, "CLOSE")
    bar_ts = [to_utc(l) for l in labels]
    ymin, ymax = min(closes), max(closes)
    vec_entries = [(to_utc(t["entry_time"]), int(t["bar_entry"])) for t in trades]
    vec_exits = [(to_utc(t["exit_time"]), int(t["bar_exit"])) for t in trades]
    live = load_live(args.acct, sym, side)
    live_opens = [e["ts"] for e in live if e["kind"] == "open"]
    live_closes = [e["ts"] for e in live if e["kind"] == "close"]

    def nearest_bar(ts):
        i = bisect.bisect_left(bar_ts, ts)
        cands = [c for c in (i - 1, i) if 0 <= c < len(bar_ts)]
        return min(cands, key=lambda c: abs((bar_ts[c] - ts).total_seconds()))

    out_of_range = [e for e in live if abs((e["ts"] - bar_ts[0]).total_seconds()) > 86400
                    and abs((e["ts"] - bar_ts[-1]).total_seconds()) > 86400
                    and (e["ts"] < bar_ts[0] or e["ts"] > bar_ts[-1])]
    _oor = {(e["ts"].isoformat(), e["type"]) for e in out_of_range}

    def report(tol_min):
        tol_s = tol_min * 60.0
        me_v, me_l = greedy_match([t for t, _ in vec_entries], live_opens, tol_s)
        mx_v, mx_l = greedy_match([t for t, _ in vec_exits], live_closes, tol_s)
        return {"me_v": me_v, "me_l": me_l, "mx_v": mx_v, "mx_l": mx_l}

    r1 = report(args.tol_min)
    r2 = report(args.tol2_min)
    vec_only = sorted([[vec_entries[i][1], "entry"] for i in range(len(vec_entries)) if i not in r1["me_v"]]
                      + [[vec_exits[i][1], "exit"] for i in range(len(vec_exits)) if i not in r1["mx_v"]])
    live_only_idx = ([i for i in range(len(live_opens)) if i not in r1["me_l"]],
                     [i for i in range(len(live_closes)) if i not in r1["mx_l"]])
    live_only_ts = [live_opens[i] for i in live_only_idx[0]] + [live_closes[i] for i in live_only_idx[1]]

    live_js = []
    for e in live:
        if (e["ts"].isoformat(), e["type"]) in _oor:
            continue
        b = nearest_bar(e["ts"])
        if e["kind"] == "open":
            live_js.append({"bar_entry": b, "ts": e["ts"].isoformat(), "reason": e["reason"]})
        else:
            live_js.append({"bar_exit": b, "ts": e["ts"].isoformat(), "reason": e["reason"]})

    def seg(bar_list):
        pts = []
        for b in bar_list:
            lab = labels[b] if 0 <= b < len(labels) else b
            pts += [{"x": lab, "y": ymin}, {"x": lab, "y": ymax}, None]
        return pts

    vec_bars = sorted({b for b, _ in vec_only})
    live_bars = sorted({nearest_bar(t) for t in live_only_ts})
    injection = (
        "{label:'vec only (no live fill)',data:%s,type:'line',borderColor:'rgba(248,113,113,0.55)',"
        "backgroundColor:'rgba(248,113,113,0.55)',borderWidth:1.5,pointRadius:0,fill:false,tension:0,order:20},"
        "{label:'live only (no vec trade)',data:%s,type:'line',borderColor:'rgba(74,222,128,0.55)',"
        "backgroundColor:'rgba(74,222,128,0.55)',borderWidth:1.5,pointRadius:0,fill:false,tension:0,order:20},"
        % (json.dumps(seg(vec_bars)), json.dumps(seg(live_bars)))
    )
    banner = (
        "<div class='metrics' style='border-color:#a855f7'>PARITY VS LIVE (%s %s_%s tol %.0fm): "
        "vec entries %d matched %d · vec exits %d matched %d · live opens %d matched %d · "
        "live closes %d matched %d · <span style='color:#f87171'>vec-only %d</span> · "
        "<span style='color:#4ade80'>live-only %d</span> · tol %.0fm recount: vec-only %d live-only %d · "
        "chart models 0 bps costs; live sample n=%d</div>"
        % (args.acct, sym, side, args.tol_min, len(vec_entries), len(r1["me_v"]), len(vec_exits),
           len(r1["mx_v"]), len(live_opens), len(r1["me_l"]), len(live_closes), len(r1["mx_l"]),
           len(vec_only), len(live_only_ts), args.tol2_min,
           len(vec_entries) - len(r2["me_v"]) + len(vec_exits) - len(r2["mx_v"]),
           len(live_opens) - len(r2["me_l"]) + len(live_closes) - len(r2["mx_l"]), len(live))
    )
    for anchor in ["const LIVE = [];", "const OPEN_EV=EVENTS", "<div id='wrap'>"]:
        if anchor not in text:
            raise SystemExit(f"anchor missing: {anchor}")
    text = text.replace("const LIVE = [];", "const LIVE = %s;" % json.dumps(live_js))
    text = text.replace("const OPEN_EV=EVENTS", injection + "const OPEN_EV=EVENTS")
    text = text.replace("<div id='wrap'>", banner + "<div id='wrap'>", 1)
    out = Path(args.out) if args.out else html_path.with_name(html_path.stem + ".parity.html")
    if out.resolve() == html_path.resolve():
        raise SystemExit("refusing to overwrite input")
    out.write_text(text)
    print(f"WROTE {out}")
    print(f"vec entries {len(vec_entries)} matched {len(r1['me_v'])} | exits {len(vec_exits)} matched {len(r1['mx_v'])}")
    print(f"live opens {len(live_opens)} matched {len(r1['me_l'])} | closes {len(live_closes)} matched {len(r1['mx_l'])}")
    print(f"VEC-ONLY {len(vec_only)} bars {vec_bars[:12]}{'...' if len(vec_bars) > 12 else ''}")
    print(f"LIVE-ONLY {len(live_only_ts)} at {[t.strftime('%m-%d %H:%M') for t in live_only_ts]}")
    print(f"LIVE markers {len(live_js)} skipped-out-of-range {len(out_of_range)}")


if __name__ == "__main__":
    main()
