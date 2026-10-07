#!/usr/bin/env python3
"""v15_tradeable_check — daily chain check (USER ruling 2026-10-06): every tradeable key must trade.

Tradeable = today's universe allowed_sym_sides (tools/v15_universe.py: tradeable_keys.json + trb lists/mandatory, minus
universe_exclude) PLUS every open-position sym_side (data/open_position_sym_sides.json). For each one, the LATEST sheet of this
round (data/avg_delta_selection.json = the rebuild's one-latest-file-per-sym_side map) is re-evaluated FRESH at 30D on the host that
holds its NPZ (tools/v15_persym_golive.py collect --eval-all: cumulative_overrides > initial_overrides > defaults). A row is a
SYSTEM_ERROR (for the repair phase) when: no sheet this round, the evaluation failed, trades < 10 or TIM < 20%.
Writes data/daily_chain/<date>_tradeable_check.json. Runs in tools/v15_daily_chain_s1.sh (non-fatal step) or by hand:
  V15_FLEET_HOSTS=tools/fleet_hosts_final.json python tools/v15_tradeable_check.py [--selection F] [--out F]
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_persym_golive as G  # noqa: E402
import v15_universe as U  # noqa: E402

MIN_TRADES = 10
MIN_TIM = 20.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", default=str(ROOT / "data" / "avg_delta_selection.json"))
    ap.add_argument("--hosts", default=os.environ.get("V15_FLEET_HOSTS") or str(ROOT / "tools" / "fleet_hosts.json"))
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1500)
    a = ap.parse_args()
    hosts_file = a.hosts if os.path.isabs(a.hosts) else str(ROOT / a.hosts)
    uni, how = U.load_or_build(ROOT, write=False)
    openpos = U.open_position_sym_sides(ROOT)
    tradeable = {s.upper() for s in uni.get("allowed_sym_sides", [])} | openpos
    sel = json.loads(Path(a.selection).read_text())
    hosts = {h["name"]: h for h in json.loads(Path(hosts_file).read_text())["hosts"]}
    per_host, rows = {}, {}
    for ss in sorted(tradeable):
        r = sel.get(ss)
        if not r:
            rows[ss] = {"result": "SYSTEM_ERROR", "reason": "no sheet in this round (not in the rebuild selection)", "open_position": ss in openpos}
            continue
        per_host.setdefault(r["host"], []).append(r["path"])
    tmpd = Path(tempfile.mkdtemp(prefix="tradeable_check_"))

    def run_host(item):
        hn, files = item
        h = hosts.get(hn)
        if not h:
            return hn, None, f"host {hn} not in {hosts_file}"
        try:
            lf = tmpd / f"{hn}.txt"
            lf.write_text("\n".join(files) + "\n")
            via, _ = G._ssh(h["ssh"], "true", 30)
            G._scp(str(ROOT / "tools" / "v15_persym_golive.py"), f"{via}:binance-sandbox/tools/v15_persym_golive.py")
            G._scp(str(lf), f"{via}:/tmp/v15_tradeable_check_files.txt")
            _, out = G._ssh([via], f"cd ~/binance-sandbox && timeout {a.timeout} .venv/bin/python -u tools/v15_persym_golive.py collect --eval-all --files /tmp/v15_tradeable_check_files.txt --out /tmp/v15_tradeable_check_out.json --workers {a.workers} 2>&1 | grep -v Warning | tail -2", a.timeout + 60)
            print(f"[tradeable-check] {hn} via {via}: {out.strip()}", flush=True)
            dst = tmpd / f"{hn}.json"
            G._scp(f"{via}:/tmp/v15_tradeable_check_out.json", str(dst))
            return hn, json.loads(dst.read_text()), None
        except Exception as e:
            return hn, None, str(e)[:300]

    host_err, got = {}, {}
    with cf.ThreadPoolExecutor(max(1, len(per_host))) as ex:
        for hn, res, err in ex.map(run_host, sorted(per_host.items())):
            if err:
                host_err[hn] = err
            else:
                got.update(res)
    for ss in sorted(tradeable):
        if ss in rows:
            continue
        rec = got.get(ss)
        base = {"open_position": ss in openpos, "host": sel[ss]["host"], "path": sel[ss]["path"]}
        if rec is None:
            rows[ss] = dict(base, result="SYSTEM_ERROR", reason=f"not evaluated (host error: {host_err.get(sel[ss]['host'], 'missing from collect output')})")
            continue
        ev = rec.get("evidence")
        base.update(set_source=rec.get("set_source"), verdict=rec.get("verdict"))
        if not ev:
            rows[ss] = dict(base, result="SYSTEM_ERROR", reason=f"evaluation failed: {rec.get('skip')}")
            continue
        t = int(ev.get("trades") or 0)
        tim = float(ev.get("tim_pct") or 0.0)
        why = []
        if t < MIN_TRADES:
            why.append(f"trades {t} < {MIN_TRADES}/30D")
        if tim < MIN_TIM:
            why.append(f"TIM {tim:.1f}% < {MIN_TIM:.0f}%")
        base.update(trades=t, tim_pct=tim, gain_pct=ev.get("gain_pct"), valid=ev.get("valid"), invalid_reason=ev.get("invalid_reason"))
        rows[ss] = dict(base, result="SYSTEM_ERROR" if why else "OK", reason="; ".join(why))
    for v in rows.values():
        if v["result"] != "SYSTEM_ERROR":
            continue
        r, ir = v["reason"], str(v.get("invalid_reason") or "")
        v["kind"] = ("no sheet" if "no sheet" in r else "eval failed" if r.startswith(("evaluation failed", "not evaluated")) else "set incompatible with engine" if ir.startswith("override") else
                     "zero trades" if v.get("trades") == 0 else "trades<10+TIM<20" if "trades" in r and "TIM" in r else "trades<10" if "trades" in r else "TIM<20")
    errs = {k: v for k, v in rows.items() if v["result"] == "SYSTEM_ERROR"}
    summary = {"tradeable": len(tradeable), "open_positions": len(openpos), "universe": how, "ok": sum(1 for v in rows.values() if v["result"] == "OK"), "system_error": len(errs),
               "error_kinds": Counter(v["kind"] for v in errs.values()), "open_position_errors": sorted(k for k, v in errs.items() if v.get("open_position")),
               "host_errors": host_err}
    out = Path(a.out or ROOT / "data" / "daily_chain" / f"{a.date}_tradeable_check.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"date": a.date, "at": dt.datetime.now(dt.timezone.utc).isoformat(), "rule": f"tradeable key must trade: >= {MIN_TRADES} trades/30D and TIM >= {MIN_TIM:.0f}% on its latest sheet (USER 2026-10-06)", "summary": summary, "rows": rows}, indent=1, default=str))
    tmp.replace(out)
    print(json.dumps(summary, indent=1, default=str))
    print(f"[tradeable-check] report {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
