#!/usr/bin/env python3
"""365D-verify every go-live candidate set (vector engine, 365D slice prepared once per sym_side) and stamp q365 into
data/golive/candidates_{date}.json (BIBLE §58/§62 gate: valid, gain>0, >= 80 trades or pro-rata on short history)."""
import concurrent.futures as cf, datetime, json, multiprocessing as mp, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)


def one(args):
    ss, ov = args
    import v15_pilot as P
    from tools.opt import v12_pilot as VP
    d = P.get_defaults_for_symside(ss)
    r = VP.evaluate_prepared_sanitized(VP.prepare_batch(ss, 365), P.sanitize_overrides(ov, d)[0], 365)
    span = P._npz_span_days(ss)
    ok, why = P._qualifies_365d(r, span)
    return ss, {"gain": r.get("gain_pct"), "trades": r.get("trades"), "tim": r.get("tim_pct"), "dd": r.get("max_dd_pct"), "valid": r.get("valid"), "reason": r.get("invalid_reason")}, ok, why


def main():
    date = datetime.date.today().strftime("%Y%m%d")
    p = ROOT / "data" / "golive" / f"candidates_{date}.json"
    c = json.loads(p.read_text())
    todo = [(ss, v["overrides"]) for ss, v in c.items() if v.get("q365") is not True]
    with cf.ProcessPoolExecutor(max_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 4, mp_context=mp.get_context("spawn")) as ex:
        for ss, m, ok, why in ex.map(one, todo):
            c[ss]["m365"], c[ss]["q365"], c[ss]["why365"] = m, ok, why
            c[ss]["needs"] = [n for n in c[ss]["needs"] if n != "365D_verify"] + ([] if ok else ["365D_FAIL"])
            print(f"{ss:<20} 365D gain={m['gain']} tr={m['trades']} DD={m['dd']} -> {'PASS' if ok else '; '.join(why)}", flush=True)
    p.write_text(json.dumps(c, indent=1, default=str))


if __name__ == "__main__":
    main()
