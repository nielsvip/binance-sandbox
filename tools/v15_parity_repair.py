#!/usr/bin/env python3
"""v15_parity_repair — prune-until-PASS for parity-FAIL sym_sides (USER 2026-10-04: every sym_side gets NEW proven settings; blocking is not the outcome).

For a FAIL set, remove override FAMILIES (greedy, vec-ranked) until a subset passes live-faithful parity. Returns a PROVEN subset + evidence trail, or None (residual for explicit user decision). Never promotes unproven: the winner passed the real parity_check.
Stage 1 (vec-only, seconds): rank family removals by vec gain retained (must stay positive+valid, >=10 trades).
Stage 2 (scalar, minutes each): first PASS wins. v1 sequential per sym; parallelize across syms via scheduler.
"""
import argparse, json, os, re, subprocess, sys, tempfile

FAMILY_PREFIXES = ("HARDCODED_RALLY_REENTRY", "DC_DAYTRADE", "TRADIER_DC_DAYTRADE", "DAYTRADE", "GAP_RISK", "PEAK_GIVEBACK", "WT_DC", "REENTRY2", "REENTRY", "AUGMENT", "REDUCE", "ENTRY", "EXIT", "DC_", "WT_", "BB_", "STDEV")


def family_of(key):
    k = str(key).upper()
    for p in FAMILY_PREFIXES:
        if k == p or k.startswith(p + "_"):
            return p.rstrip("_")
    return k.split("_")[0] if "_" in k else "MISC"


def rank_removals(overrides, vec_eval):
    """[(family, kept_gain, kept_trades, subset)] for removals keeping vec positive+valid, best first."""
    fams = {}
    for k in overrides:
        fams.setdefault(family_of(k), []).append(k)
    out = []
    for fam, keys in sorted(fams.items()):
        drop = set(keys)
        sub = {k: v for k, v in overrides.items() if k not in drop}
        try:
            r = vec_eval(sub)
        except Exception:
            continue
        g = float(r.get("gain_pct") or 0)
        t = int(r.get("trades") or 0)
        if r.get("valid") and g > 0 and t >= 10:
            out.append((fam, g, t, sub))
    out.sort(key=lambda x: -x[1])
    return out


def repair(ss, overrides, vec_eval, scalar_verify, max_scalar_runs=12):
    """(proven_subset_or_None, evidence). First PASS wins; exhaust -> residual."""
    ev = [{"stage": "input", "keys": len(overrides)}]
    cands = rank_removals(overrides, vec_eval)
    ev.append({"stage": "ranked", "candidates": [(f, round(g, 2), t) for f, g, t, _ in cands]})
    for fam, g, t, sub in cands[:max_scalar_runs]:
        try:
            ok, detail = scalar_verify(ss, sub)
        except Exception as e:
            ev.append({"family": fam, "error": str(e)[:120]})
            continue
        ev.append({"family": fam, "vec_gain": round(g, 2), "pass": bool(ok), "detail": str(detail)[:200]})
        if ok:
            return sub, ev
    return None, ev


def _real_vec(ss, window_days=30):
    sys.path.insert(0, os.path.expanduser("~/binance-sandbox") if os.path.exists(os.path.expanduser("~/binance-sandbox")) else "/Users/niels/Documents/binance")
    os.environ.setdefault("V12_NPZ_CACHE", "32")
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
    prep = prepare_batch(ss, window_days)
    return lambda sub: evaluate_prepared_sanitized(prep, dict(sub), window_days)


def _real_scalar(root, timeout_s):
    def run(ss, sub):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"cumulative_overrides": dict(sub)}, f)
            tmp = f.name
        p = subprocess.run([sys.executable, "-u", "tools/v15_parity_check.py", "--sym-side", ss, "--progress", tmp, "--window-days", "30"],
                           capture_output=True, text=True, timeout=timeout_s, cwd=root)
        m = re.search(r"PARITY (\S+) (PASS|FAIL) :: (.*?) ::", p.stdout + p.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if not m:
            return False, "no PARITY line"
        return m.group(2) == "PASS", m.group(3)[:200]
    return run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-runs", type=int, default=12)
    a = ap.parse_args()
    root = os.path.expanduser("~/binance-sandbox") if os.path.exists(os.path.expanduser("~/binance-sandbox")) else "/Users/niels/Documents/binance"
    sys.path.insert(0, root)
    from tools.v15_final_phase import parity_timeout_for
    ov = dict(json.load(open(a.progress)).get("cumulative_overrides") or {})
    sub, ev = repair(a.sym_side, ov, _real_vec(a.sym_side), _real_scalar(root, parity_timeout_for(a.sym_side)), a.max_runs)
    json.dump({"sym_side": a.sym_side, "proven": sub, "evidence": ev}, open(a.out, "w"), default=str)
    print("PROVEN-SET" if sub else "RESIDUAL", a.sym_side, ("keys=" + str(len(sub))) if sub else "")


if __name__ == "__main__":
    main()
