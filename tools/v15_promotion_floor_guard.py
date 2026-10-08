#!/usr/bin/env python3
"""v15_promotion_floor_guard — COMBINED trade-floor check for the daily template promotion (director 2026-10-06).

The template writer promotes each key on its own average delta; combined they can kill trading (2026-10-06 17:18: every crypto
short zeroed). Before the writer saves, this evaluates the combined new defaults on 5 sample TRADEABLE sym_sides of the cat_side
(real engine, 30D: tools.opt.v12_pilot prepare_batch + evaluate_prepared_sanitized). The base config is prepared with
CAT_SIDE_DEFAULTS_SQLITE_DISABLED=1 and CAT_SIDE_DEFAULTS_PATH=<current per_sym_settings.json> (the stale SQL kv must not
leak in); the promoted values are applied as overrides on top (= the candidate defaults).
FAIL when the candidate median (over the samples) has: trades < 50% of the baseline, or trades < 10 while below the baseline,
or TIM < 20% while below the baseline. On FAIL it bisects: keys that fail alone are refused; if the rest still fails, greedy
backward elimination (drop the key whose removal restores the most trades) up to --max-elim steps; still failing -> refuse the
whole cat_side (keep the old bolds).
  check(cat_side, promoted={KEY: value}, baseline={KEY: old value} or None) -> report dict
  CLI replay: python tools/v15_promotion_floor_guard.py --replay data/reports/v15_daily_template_update_<ts>.json [--cat-side X]
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# ROOT must precede tools/: tools/cat_side_defaults.py is a DIFFERENT module that would shadow the engine's cat_side layer
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != (ROOT / "tools").resolve()]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
FLOOR_TRADES = 10
FLOOR_TIM = 20.0
MAX_DROP = 0.5
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD")


def _norm(v, key=None):
    if isinstance(v, str):
        t = v.strip()
        if key and t.upper().startswith(f"{key.upper()}="):  # template cells carry "KEY=VALUE"
            t = t.split("=", 1)[1].strip()
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        try:
            f = float(t)
            return int(f) if f.is_integer() and "." not in t else f
        except Exception:
            return t
    return v


def _tradeable(cat_side):
    import v15_universe as U
    cat, side = cat_side.split("_")
    try:
        uni, _ = U.load_or_build(ROOT, write=False)
        allowed = set(uni.get("allowed_sym_sides", []))
    except Exception:
        allowed = set()
    allowed |= U.open_position_sym_sides(ROOT)
    out = [s for s in allowed if s.endswith("_" + side) and (s.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIX) == (cat == "CRYPTO"))]
    return sorted(out, key=lambda s: hashlib.md5(s.encode()).hexdigest())


def _prepare(cat_side, n, cat_file):
    from tools.opt.v12_pilot import prepare_batch
    old = {k: os.environ.get(k) for k in ("CAT_SIDE_DEFAULTS_SQLITE_DISABLED", "CAT_SIDE_DEFAULTS_PATH")}
    os.environ["CAT_SIDE_DEFAULTS_SQLITE_DISABLED"] = "1"
    os.environ["CAT_SIDE_DEFAULTS_PATH"] = str(cat_file)
    try:
        try:  # 2026-10-08: cat_side_defaults is a compat shim over per_sym_settings; the shim does not re-export _cache/PATH
            import per_sym_settings as csd
        except ImportError:
            import cat_side_defaults as csd
        csd.PATH = Path(cat_file)
        assert Path(csd.__file__).resolve().parent == ROOT, f"wrong per_sym_settings module: {csd.__file__}"
        _c = getattr(csd, "_cache", None)
        if isinstance(_c, dict):
            _c["mtime"] = None
            _c["mtime_sql"] = None
        preps = []
        for ss in _tradeable(cat_side):
            if len(preps) >= n:
                break
            try:
                p = prepare_batch(ss, 30)
            except Exception:
                p = None
            if p is not None:
                preps.append((ss, p))
        return preps
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _stats(preps, overrides):
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    per = []
    for ss, p in preps:
        try:
            r = evaluate_prepared_sanitized(p, dict(overrides), 30)
            per.append((ss, int(r.get("trades") or 0), float(r.get("tim_pct") or 0.0)))  # engine-rejected override -> trades None -> 0 (cannot apply = refuse)
        except Exception as e:
            per.append((ss, 0, 0.0))
    return {"med_trades": statistics.median([x[1] for x in per]) if per else 0, "med_tim": statistics.median([x[2] for x in per]) if per else 0.0, "per": per}


def _fails(c, b):
    why = []
    if b["med_trades"] > 0 and c["med_trades"] < MAX_DROP * b["med_trades"]:
        why.append(f"median trades {c['med_trades']} < {MAX_DROP:.0%} of baseline {b['med_trades']}")
    if c["med_trades"] < FLOOR_TRADES and c["med_trades"] < b["med_trades"]:
        why.append(f"median trades {c['med_trades']} < floor {FLOOR_TRADES} (baseline {b['med_trades']})")
    if c["med_tim"] < FLOOR_TIM and c["med_tim"] < b["med_tim"] - 1e-9:
        why.append(f"median TIM {c['med_tim']:.1f} < {FLOOR_TIM:.0f}% (baseline {b['med_tim']:.1f})")
    return why


def check(cat_side, promoted, baseline=None, n=5, max_elim=12, cat_file=None):
    cat_file = cat_file or os.environ.get("V15_GUARD_CAT_FILE") or str(ROOT / "data" / "per_sym_settings.json")
    promoted = {k: _norm(v, k) for k, v in (promoted or {}).items()}
    base_ov = {k: _norm(v, k) for k, v in (baseline or {}).items() if v is not None}
    rep = {"cat_side": cat_side, "n_promoted": len(promoted), "cat_file": cat_file, "refused_keys": [], "refuse_all": False, "ok": True}
    if not promoted:
        return rep
    preps = _prepare(cat_side, n, cat_file)
    rep["samples"] = [ss for ss, _ in preps]
    if len(preps) < 3:
        rep.update(ok=False, refuse_all=True, reason=f"only {len(preps)} sample sym_sides prepared (need >= 3) — refusing (fail-closed)")
        return rep
    b = _stats(preps, base_ov)
    rep["baseline"] = b
    c = _stats(preps, dict(base_ov, **promoted))
    rep["candidate"] = c
    why = _fails(c, b)
    if not why:
        rep["reason"] = "combined promotions keep the trade floor"
        return rep
    rep["ok"] = False
    rep["candidate_fail"] = why
    culprits = []
    singles = {}
    for k, v in promoted.items():
        s = _stats(preps, dict(base_ov, **{k: v}))
        singles[k] = (s["med_trades"], round(s["med_tim"], 2))
        if _fails(s, b):
            culprits.append(k)
    rep["single_key"] = singles
    keep = {k: v for k, v in promoted.items() if k not in culprits}
    cur = _stats(preps, dict(base_ov, **keep))
    elim = []
    while _fails(cur, b) and keep and len(elim) < max_elim:
        best = None
        for k in list(keep):
            t = dict(keep)
            t.pop(k)
            s = _stats(preps, dict(base_ov, **t))
            if best is None or (s["med_trades"], s["med_tim"]) > (best[1]["med_trades"], best[1]["med_tim"]):
                best = (k, s)
        keep.pop(best[0])
        elim.append(best[0])
        cur = best[1]
    rep["eliminated"] = elim
    rep["final"] = cur
    if _fails(cur, b):
        rep.update(refuse_all=True, refused_keys=sorted(promoted), reason=f"still below the floor after bisect ({'; '.join(_fails(cur, b))}) — whole cat_side refused, old bolds kept")
    else:
        rep.update(refused_keys=sorted(culprits + elim), reason=f"refused {len(culprits) + len(elim)} killer key(s); {len(keep)} promotion(s) kept")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-json", default=None, help="writer mode: {cat_side, promoted} json in, check() result json to --out")
    ap.add_argument("--replay", default=None, help="a v15_daily_template_update report json (promoted_switch/promoted_filter carry old -> new)")
    ap.add_argument("--cat-side", default=None)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cat-file", default=None, help="baseline per_sym_settings.json (the defaults in force before the replayed promotion)")
    ap.add_argument("--old-values", action="store_true", help="also reset promoted keys to the report's old template bolds (default: baseline = --cat-file as is)")
    a = ap.parse_args()
    if a.check_json:
        j = json.loads(Path(a.check_json).read_text())
        g = check(j["cat_side"], j["promoted"], None, n=a.n, cat_file=a.cat_file)
        Path(a.out).write_text(json.dumps(g, indent=1, default=str))
        print(f"[floor-guard] {j['cat_side']} ok={g['ok']} refuse_all={g['refuse_all']} refused={len(g['refused_keys'])} | {g.get('reason')}")
        return 0
    r = json.loads(Path(a.replay).read_text())
    res = {}
    for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        if a.cat_side and cs != a.cat_side or cs not in r:
            continue
        prom, base = {}, {}
        for kind in ("promoted_switch", "promoted_filter"):
            for tab, key, old, new, _avg in r[cs].get(kind, []):
                prom[key] = new
                if old:
                    base[key] = old[0]
        g = check(cs, prom, base if a.old_values else None, n=a.n, cat_file=a.cat_file)
        res[cs] = g
        print(f"[{cs}] promoted={len(prom)} samples={g.get('samples')} base={ {k: g['baseline'][k] for k in ('med_trades', 'med_tim')} if 'baseline' in g else None} cand={ {k: g['candidate'][k] for k in ('med_trades', 'med_tim')} if 'candidate' in g else None} ok={g['ok']} refuse_all={g['refuse_all']} refused={g['refused_keys'][:20]}{' ...' if len(g['refused_keys']) > 20 else ''} | {g.get('reason')}", flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
