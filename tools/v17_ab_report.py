#!/usr/bin/env python3
"""V17 A/B report (USER 2026-10-13 acceptance protocol): same sym_sides, same frozen NPZ/template/start set/env, old pilot vs
V17 (V15_V17_ROUNDS), isolated progress dirs. Reads <base>/{old,new}/progress/*_v14_progress.json + logs + delta logs and
re-evaluates every final override set with the real engine (evaluate_prepared_sanitized, include_ledger) for trades / TIM /
DD / WR / pool_sharpe (metrics_guard, DIAGNOSTIC label: one sym_side x 30 days is below the publishable floor).
Writes data/reports/v17_ab_<ts>.json + .md. Every number is measured; nothing is estimated.

usage: python3 tools/v17_ab_report.py --base ~/v17_ab_all [--out data/reports]
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _log_stats(log: Path) -> dict:
    out = {"wall_s": None, "max_rss_mb": None, "redo": None, "phases": {}}
    try:
        txt = log.read_text(errors="replace")
    except Exception:
        return out
    m = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([0-9:.]+)", txt)
    if m:
        parts = [float(x) for x in m.group(1).split(":")]
        out["wall_s"] = round(parts[-1] + 60 * parts[-2] + (3600 * parts[-3] if len(parts) > 2 else 0), 1)
    m = re.search(r"Maximum resident set size \(kbytes\): (\d+)", txt)
    if m:
        out["max_rss_mb"] = round(int(m.group(1)) / 1024.0, 1)
    m = re.findall(r"\[REDO\] \S+ (.*scheduled.*)", txt)
    out["redo"] = m[-1][:120] if m else None
    for ph, ev, wall, lift in re.findall(r"\[V17-PHASE\] (\S+) evals=(\d+) wall=([0-9.]+)s lift=([+-][0-9.]+)", txt):
        out["phases"][ph] = {"evals": int(ev), "wall_s": float(wall), "lift": float(lift)}
    for tag, secs in re.findall(r"\[(FOCUS|ENDGAME|DIAG)\] \S+ .*?([0-9.]+)s evals=", txt):
        out["phases"].setdefault(f"OLD_{tag}", {"wall_s": float(secs)})
    return out


def _delta_evals(dl: Path) -> int:
    n = 0
    try:
        with dl.open() as fh:
            for line in fh:
                if '"ROW_DONE"' in line or '"cached": true' in line:
                    continue
                n += 1
    except Exception:
        pass
    return n


def _engine_metrics(symside: str, overrides: dict, prep_cache: dict) -> dict:
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    import metrics_guard as mg
    if symside not in prep_cache:
        prep_cache[symside] = prepare_batch(symside, 30)
    res = evaluate_prepared_sanitized(prep_cache[symside], dict(overrides), 30, include_ledger=True)
    led = res.get("ledger") or res.get("trades_ledger") or []
    rets = []
    try:
        rets = [float(t.get("pnl_pct")) for t in mg.realised_closes(led) if t.get("pnl_pct") is not None]
    except Exception:
        rets = []
    ps = mg.pool_sharpe(rets) if rets else None
    wr = (sum(1 for r in rets if r > 0) / len(rets) * 100.0) if rets else res.get("win_rate")
    try:
        ps_txt = mg.validate_and_format_sharpe(float(ps or 0.0), label="pool_sharpe", n_syms=1, years=30 / 365.0, trades=int(res.get("trades") or 0), mode="crypto" if "USD" in symside else "stocks") if ps is not None else "pool_sharpe=n/a [UNVERIFIED: no per-trade returns]"
    except Exception as e:
        ps_txt = f"pool_sharpe refused: {e}"[:120]
    return {"gain_pct": res.get("gain_pct"), "trades": res.get("trades"), "tim_pct": res.get("tim_pct"), "max_dd_pct": res.get("max_dd_pct"), "wr_pct": None if wr is None else round(float(wr), 2), "pool_sharpe": ps_txt, "valid": res.get("valid"), "invalid_reason": res.get("invalid_reason"), "n_returns": len(rets)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports"))
    a = ap.parse_args()
    base = Path(os.path.expanduser(a.base))
    prep = {}
    rows = []
    for arm in ("old", "new"):
        for p in sorted(glob.glob(str(base / arm / "progress" / "*_v14_progress.json"))):
            ss = Path(p).name.replace("_v14_progress.json", "")
            j = json.loads(Path(p).read_text())
            ls = _log_stats(base / arm / "logs" / f"{ss}.log")
            ov = j.get("cumulative_overrides") or {}
            em = _engine_metrics(ss, ov, prep) if ov else {}
            prom = sum(1 for v in (j.get("done") or {}).values() if isinstance(v, dict) and v.get("promoted"))
            bh = j.get("bh")
            fg = j.get("final_gain") if j.get("final_gain") is not None else j.get("cumulative_gain")
            rows.append({"arm": arm, "sym_side": ss, "host": (j.get("v17") or {}).get("host"), "final_gain": fg, "board_final_gain_field": j.get("final_gain"), "cumulative_gain": j.get("cumulative_gain"), "engine_reeval": em, "bh": bh, "gain_over_bh": (round(float(fg) / float(bh), 3) if fg is not None and bh not in (None, 0) and float(bh) > 0 else None), "promoted_rows": prom, "evals": _delta_evals(base / arm / "progress" / "v15_delta_log" / f"{ss}_jump.jsonl"), "wall_s": ls["wall_s"], "max_rss_mb_parent": ls["max_rss_mb"], "redo_scheduled": ls["redo"], "phases": ls["phases"] or None, "v17_phases": (j.get("v17") or {}).get("phases")})
    ts = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"v17_ab_{ts}.json").write_text(json.dumps({"generated_utc": ts, "base": str(base), "rows": rows}, indent=1, default=str))
    md = [f"# V17 A/B {ts}", "", "Same sym_side, same frozen NPZ / template / start set / env; old = pilot bab5e0ce (V17 off), new = V17.", "Metrics re-evaluated from each final override set with the real engine (evaluate_prepared_sanitized).", "", "| sym_side | arm | final gain | bh | gain/bh | trades | TIM | max DD | WR | pool_sharpe | promoted rows | evals | wall min | RSS MB | REDO |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["sym_side"], r["arm"])):
        e = r["engine_reeval"] or {}
        f = lambda x, n=2: "" if x is None else (f"{float(x):.{n}f}" if isinstance(x, (int, float)) else str(x))
        md.append(f"| {r['sym_side']} | {r['arm']} | {f(r['final_gain'])} | {f(r['bh'])} | {f(r['gain_over_bh'])} | {e.get('trades', '')} | {f(e.get('tim_pct'))} | {f(e.get('max_dd_pct'))} | {f(e.get('wr_pct'))} | {e.get('pool_sharpe', '')} | {r['promoted_rows']} | {r['evals']} | {f((r['wall_s'] or 0) / 60.0, 1) if r['wall_s'] else ''} | {f(r['max_rss_mb_parent'], 0)} | {(r['redo_scheduled'] or '')[:40]} |")
    md += ["", "## V17 phases (new arm)", ""]
    for r in rows:
        if r["arm"] == "new" and r.get("v17_phases"):
            md.append(f"- {r['sym_side']}: " + "; ".join(f"{k} {v.get('wall_s')}s/{v.get('evals')}ev/{v.get('lift'):+.2f}" for k, v in r["v17_phases"].items() if isinstance(v, dict) and v.get("lift") is not None))
    (out / f"v17_ab_{ts}.md").write_text("\n".join(md) + "\n")
    print(out / f"v17_ab_{ts}.md")
    print("\n".join(md))


if __name__ == "__main__":
    main()
