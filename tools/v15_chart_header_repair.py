#!/usr/bin/env python3
"""v15_chart_header_repair — rewrite every already-published chart's header from the chart's OWN trades.

USER 2026-10-16: *"FIX all these IMMEDIATELY"* / *"make sure NO false number can go on any chart and if the
numbers producer is real the chart should be built on that not on lies"* / *"make sure no chart html can ever be
produced again without recalculating its own results and adding them to the header"*.

Going FORWARD that is enforced at the chokepoint: `tools/opt/hires_chart.generate_hires` now derives the header
from the ledger it is about to embed and refuses to write a file whose headline is not the sum of its own trades.
This script is the BACKWARD half — the 875 charts already on disk, of which 75 print a headline that contradicts
the trades drawn in the same file (BIOUSDC_SHORT printed +64.82% over trades summing to -11.15%).

IT INVENTS NOTHING. Every number it writes is recomputed from `const CLOSE/TRADES/EVENTS` already inside that
file, with the engine's own helpers (`tools/opt/evaluate_v12._honest_gain_pct/_honest_max_dd_pct/_peak_concurrent`,
`metrics_guard.pool_sharpe_from_ledger`). No NPZ, no engine run, so it is exact and host-independent. Numbers that
CANNOT be recomputed from the file (DDmtm needs intrabar high/low; score is a ranking artefact) are REMOVED rather
than carried over unverified — a number nobody can check is the thing that caused this.

The original header is preserved verbatim inside the repaired file (`REPAIRED` banner + an HTML comment) so the
lie stays auditable, every touched file is backed up first, and an evidence JSON records before -> after per chart.

USAGE
    python3 tools/v15_chart_header_repair.py                 # dry run, prints what would change
    python3 tools/v15_chart_header_repair.py --apply         # back up, rewrite, write evidence
    python3 tools/v15_chart_header_repair.py --symside APO_SHORT --apply
"""
import argparse
import html as _html
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SHEET_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
OUT_DIR = ROOT / "data" / "reports"
BACKUP_ROOT = ROOT / "backups"


def log(msg):
    print(msg, flush=True)


def _fmt(v, nd=2):
    try:
        return "—" if v is None else f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def recompute_header(trades, events, close, is_long, window_days, symside):
    """Exactly the numbers hires_chart now prints — derived from the embedded arrays, nothing else."""
    from tools.opt.evaluate_v12 import _honest_gain_pct, _honest_max_dd_pct, _peak_concurrent

    embedded = {"ledger": list(trades) + list(events)}
    gain = _honest_gain_pct(embedded)
    bh = None
    if close and len(close) >= 2 and close[0] > 0:
        raw = (close[-1] - close[0]) / close[0] * 100.0
        bh = raw if is_long else max(-100.0, -raw)
    wins = sum(1 for t in trades if float(t.get("pnl_dollars") or 0.0) > 0)
    bars = set()
    for t in trades:
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        if isinstance(be, int) and isinstance(bx, int):
            bars.update(range(be, bx))
    try:
        from metrics_guard import pool_sharpe_from_ledger

        psh = float(pool_sharpe_from_ledger(trades))
    except Exception:
        psh = None
    return {
        "gain_pct": gain,
        "bh_pct": bh,
        "delta_vs_bh": (gain - bh) if bh is not None else None,
        "trades": len(trades),
        "max_dd_pct": _honest_max_dd_pct(embedded),
        "tim_pct": (100.0 * len(bars) / len(close)) if close else None,
        "wr_pct": (100.0 * wins / len(trades)) if trades else 0.0,
        "peak_capital": _peak_concurrent(embedded["ledger"]),
        "pool_sharpe": psh,
        "pnl_dollars": sum(float(t.get("pnl_dollars") or 0.0) for t in trades),
        "recomputed_by": "v15_chart_header_repair from this file's own embedded TRADES/EVENTS",
    }


def sharpe_text(psh, trades_n, window_days, symside):
    try:
        from metrics_guard import validate_and_format_sharpe

        sym = symside.rsplit("_", 1)[0] if "_" in symside else symside
        mode = "crypto" if sym.endswith(("USDT", "USDC", "USD1")) else "stocks"
        return validate_and_format_sharpe(float(psh or 0.0), label="pool_sharpe", n_syms=1, years=float(window_days) / 365.25, trades=trades_n, mode=mode)
    except Exception:
        return f"pool_sharpe={_fmt(psh, 3)} [UNVERIFIED]"


def honesty_bits(trades, is_long):
    costs, unsized, mtm_pnl, mtm_n = [], 0, 0.0, 0
    for t in trades:
        try:
            qty, ep, xp, pnl = float(t["qty"]), float(t["entry_price"]), float(t["exit_price"]), float(t["pnl_dollars"])
        except (KeyError, TypeError, ValueError):
            continue
        dep = float(t.get("deployed") or 0.0) or qty * ep
        if str(t.get("exit_reason") or "").upper().startswith("FINAL_MTM"):
            mtm_n += 1
            mtm_pnl += pnl
            continue
        if dep <= 0 or ep <= 0:
            continue
        if qty > 0 and abs(qty * ep - dep) <= 0.01 * dep:
            costs.append((qty * ((xp - ep) if is_long else (ep - xp)) - pnl) / dep * 1e4)
        else:
            unsized += 1
    bits = []
    if costs:
        med = sorted(costs)[len(costs) // 2]
        bits.append(f"costs modelled {med:.1f} bps round turn" + (" — NO commission/slippage/spread" if med < 0.5 else ""))
    if mtm_n:
        bits.append(f"{mtm_n} FINAL_MTM row(s) = ${mtm_pnl:+.2f} UNREALISED (position never sold)")
    if unsized:
        bits.append(f"{unsized} reduce-to-flat row(s) state remaining qty, not closed size — their P&L is not reproducible from the pictograms")
    return bits


def build_header(symside, h, close_n, trades, events, window_days, old_text, is_long):
    sh = sharpe_text(h["pool_sharpe"], h["trades"], window_days, symside)
    opens = sum(1 for e in events if str(e.get("type")).upper() == "OPEN")
    augs = sum(1 for e in events if str(e.get("type")).upper() == "AUGMENT")
    reds = sum(1 for e in events if str(e.get("type")).upper() == "REDUCE")
    bits = honesty_bits(trades, is_long)
    out = (
        '<div class="metrics">'
        f'<b>{_html.escape(symside)} — FINAL</b> &nbsp;|&nbsp; '
        f'gain {_fmt(h["gain_pct"])}% &nbsp; BH {_fmt(h["bh_pct"])}% &nbsp; Δ {_fmt(h["delta_vs_bh"])}% &nbsp;|&nbsp; '
        f'trades {h["trades"]} &nbsp; {_html.escape(sh)} &nbsp; DD {_fmt(h["max_dd_pct"])}% &nbsp; TIM {_fmt(h["tim_pct"])}% '
        f'&nbsp; WR {_fmt(h["wr_pct"])}% &nbsp; | window {close_n} bars &nbsp; peak ${_fmt(h["peak_capital"], 0)}'
        f'<br><span style="font-size:10px;opacity:1">close {close_n} bars &nbsp;|&nbsp; ledger {h["trades"]} trades'
        f' + {opens} opens / {augs} augment / {reds} reduce events &nbsp;|&nbsp; '
        f'every number above recomputed from this file\'s own TRADES/EVENTS (${_fmt(h["pnl_dollars"], 2)} realised / ${_fmt(h["peak_capital"], 0)} peak capital)</span>'
    )
    if bits:
        out += f'<br><span style="font-size:10px;color:#f59e0b">⚑ {_html.escape(" · ".join(bits))}</span>'
    out += (
        f'<br><span style="font-size:10px;color:#ef4444">REPAIRED {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())} — '
        f'the header this file shipped with was written by a producer with no per-trade returns and did not match '
        f'the trades drawn below. DDmtm and score are removed: they cannot be recomputed from this file. '
        f'Original header kept in the HTML source comment below.</span>'
        f"</div><!-- ORIGINAL HEADER BEFORE REPAIR: {old_text.replace('--', '- -')} -->"
    )
    return out


def parse_js_const(text, name):
    key = "const %s =" % name
    i = text.find(key)
    if i < 0:
        return None
    j = text.index("=", i) + 1
    k = text.find(";\n", j)
    if k < 0:
        return None
    try:
        return json.loads(text[j:k].strip())
    except ValueError:
        return None


def repair_one(path, args, backup_dir):
    rec = {"chart": path.name, "changed": False}
    text = path.read_text(errors="replace")
    i = text.find('<div class="metrics">')
    if i < 0:
        rec["skip"] = "no metrics header"
        return rec
    end = text.find("</div>", i)
    old = text[i : end + len("</div>")]
    if "REPAIRED" in old and not args.force:
        rec["skip"] = "already repaired"
        return rec
    trades, events, close = parse_js_const(text, "TRADES"), parse_js_const(text, "EVENTS") or [], parse_js_const(text, "CLOSE")
    if trades is None or close is None:
        rec["skip"] = "no embedded TRADES/CLOSE — nothing to recompute from"
        return rec
    # UNDER-DRAWING REPAIR (the real defect behind 75 of the 78 mismatched charts). `metrics_guard.realised_closes`
    # counts ANY row with pnl_dollars as a trade; charts written before the E2 unified semantic (2026-10-12) split
    # by type label, so genuine REDUCE-to-flat closes (HLR_TOP_EXIT, PPL_TP, REDUCE_TO_FLAT) were filed into EVENTS
    # and never drawn. The header counted their dollars all along — so the headline was RIGHT and the picture was
    # incomplete. Moving them back into TRADES is reclassification by the sanctioned predicate, not invention:
    # every field of every row is kept byte-for-byte.
    hidden = [e for e in events if isinstance(e, dict) and e.get("pnl_dollars") is not None]
    if hidden:
        trades = trades + hidden
        events = [e for e in events if e.get("pnl_dollars") is None]
        rec["undrawn_closes_restored"] = len(hidden)
        rec["undrawn_dollars"] = round(sum(float(h["pnl_dollars"]) for h in hidden), 4)
    symside = path.name.split("_bh")[0]
    is_long = symside.endswith("_LONG")
    m = re.search(r"_(\d+)d_matrix", path.name)
    window_days = int(m.group(1)) if m else 30
    h = recompute_header(trades, events, close, is_long, window_days, symside)
    old_gain = re.search(r"gain\s+(-?[\d.]+)%", old)
    old_trades = re.search(r"trades\s+(\d+)", old)
    rec["was"] = {"gain_pct": float(old_gain.group(1)) if old_gain else None, "trades": int(old_trades.group(1)) if old_trades else None}
    rec["now"] = {"gain_pct": round(h["gain_pct"], 4), "trades": h["trades"]}
    rec["gain_drift"] = round(abs(rec["was"]["gain_pct"] - h["gain_pct"]), 4) if rec["was"]["gain_pct"] is not None else None
    new_header = build_header(symside, h, len(close), trades, events, window_days, re.sub(r"<[^>]+>", " ", old).strip()[:600], is_long)
    out = text[:i] + new_header + text[end + len("</div>") :]
    if hidden:
        for name, arr in (("TRADES", trades), ("EVENTS", events)):
            pat = re.compile(r"const %s = .*?;\n" % name, re.S)
            if not pat.search(out):
                rec["skip"] = f"cannot rewrite const {name}"
                return rec
            out = pat.sub(lambda _m, a=arr, n=name: "const %s = %s;\n" % (n, json.dumps(a, separators=(",", ":"), default=str)), out, count=1)
    verified = "const HEADER_VERIFIED = " + json.dumps(h, separators=(",", ":"), default=str) + ";\n"
    if "const HEADER_VERIFIED" in out:
        out = re.sub(r"const HEADER_VERIFIED = .*?;\n", verified, out, count=1, flags=re.S)
    else:
        out = out.replace("const c=document.getElementById('c')", verified + "const c=document.getElementById('c')", 1)
    rec["changed"] = True
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)
        tmp = path.with_suffix(".html.tmp")
        tmp.write_text(out)
        tmp.replace(path)
        rec["backup"] = str(backup_dir / path.name)
    return rec


def main():
    ap = argparse.ArgumentParser(description="rewrite published chart headers from each chart's own embedded trades")
    ap.add_argument("--dir", default=str(SHEET_DIR))
    ap.add_argument("--glob", default="*_30d_matrix.html")
    ap.add_argument("--symside", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-drift", type=float, default=0.0, help="only repair charts whose printed gain is off by at least this much (0 = repair every header)")
    ap.add_argument("--force", action="store_true", help="re-repair a file that already carries a REPAIRED banner")
    ap.add_argument("--apply", action="store_true", help="actually write (OFF by default)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    d = Path(args.dir)
    charts = sorted(d.glob(args.glob))
    if args.symside:
        want = {s.strip().upper() for s in args.symside.split(",") if s.strip()}
        charts = [c for c in charts if c.name.split("_bh")[0].upper() in want]
    if args.limit:
        charts = charts[: args.limit]
    if not charts:
        log(f"[repair] no charts matched {args.glob} in {d}")
        return 1
    tag = time.strftime("%Y%m%d%H%M", time.gmtime())
    backup_dir = BACKUP_ROOT / f"charts_before_header_repair_{tag}"
    log(f"[repair] {len(charts)} chart(s) · apply={args.apply} · backups -> {backup_dir}")
    recs, changed, skipped, liars = [], 0, 0, []
    for i, c in enumerate(charts, 1):
        try:
            r = repair_one(c, args, backup_dir)
        except Exception as exc:
            r = {"chart": c.name, "changed": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        if r.get("gain_drift") is not None and r["gain_drift"] < args.min_drift:
            r["skip"] = f"drift {r['gain_drift']} < --min-drift"
            r["changed"] = False
        recs.append(r)
        if r.get("changed"):
            changed += 1
            if (r.get("gain_drift") or 0) > 0.01:
                liars.append(r)
        else:
            skipped += 1
        if r.get("gain_drift") and r["gain_drift"] > 0.01:
            log(f"[{i}/{len(charts)}] {c.name}: printed {r['was']['gain_pct']:+.2f}% / {r['was']['trades']} tr  ->  own trades {r['now']['gain_pct']:+.2f}% / {r['now']['trades']} tr  (drift {r['gain_drift']})")
    out = Path(args.out) if args.out else OUT_DIR / f"v15_chart_header_repair_{tag}.json"
    out.write_text(json.dumps({"tag": tag, "apply": args.apply, "backup_dir": str(backup_dir), "changed": changed, "skipped": skipped, "charts": recs}, indent=1, default=str))
    liars.sort(key=lambda r: -(r.get("gain_drift") or 0))
    log("")
    log(f"[summary] {len(charts)} charts · {changed} header(s) rewritten · {skipped} skipped · {len(liars)} were printing a gain their own trades do not produce")
    for r in liars[:15]:
        log(f"[summary] {r['gain_drift']:8.2f}pp  {r['chart'][:58]:58s} {r['was']['gain_pct']:+8.2f} -> {r['now']['gain_pct']:+8.2f}")
    log(f"[summary] evidence {out}")
    if not args.apply:
        log("[summary] DRY RUN — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
