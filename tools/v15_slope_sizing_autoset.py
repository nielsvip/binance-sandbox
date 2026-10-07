#!/usr/bin/env python3
"""v15_slope_sizing_autoset — derive the slope-sizing start position (twin ON/OFF, mode, D cap, optional gate) per sym_side from DATA,
apply it only when a rule is validated out-of-sample.  Idempotent, dry-run by default, every change logged (SLP, 2026-10-01).

stages
  collect  run tools/slp_worker_rules.py on a worker (scratch overlay of the DEPLOYED engine) for the venue's universe, pull jsonl to data/slp/autoset/<stamp>/
  decide   aggregate worker results, validate every candidate rule, write data/slp/slope_sizing_autoset.json (+ autoset_log.csv); NO_VALIDATED_RULE => no overrides
  apply    write per-sym start overrides data/slp/start_overrides/<SYMBOL_SIDE>.json (flat {key: value}, the format of V15_START_OVERRIDES) from the
           last decision; with --live also merge them into the live per-sym layer (data/hourly_reconfig/per_sym_active_config.json, merge-only, backup,
           atomic, hot-reloaded by ez_manage/tradier_manage _cfg) -- only keys with a live consumer: see LIVE_KEYS.
  run      collect+decide+apply   (cron / daily update: tools/v15_daily_template_update.py calls `run --venue both` when V15_SLP_AUTOSET=1)

GUARDS (all must pass per cat_side): >= MIN_SYM_SIDES[venue] (stocks 60, crypto 40) valid sym_sides; mean delta vs the current vector default >= MIN_EFFECT gain points;
5th percentile of the bootstrap mean > 0; >= CV_POS_FRAC of random symbol-half splits positive with the rule chosen on one half; when temporal
windows exist (offset>0) the mean delta there must be > 0 and same sign.  Crypto only on an engine that carries htf_causal_align (AUDIT/001).
"""
import argparse, csv, datetime, glob, json, os, random, shutil, subprocess, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "slp"
LOG = ROOT / "data" / "wiring" / "slp" / "autoset_log.csv"
RESULT = OUT / "slope_sizing_autoset.json"
LIVE_PERSYM = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
MIN_SYM_SIDES = {"STOCKS": 60, "CRYPTO": 40}  # per cat_side (crypto universe is only ~54 symbols)
MIN_EFFECT, CV_POS_FRAC, N_SPLITS, N_BOOT = 0.15, 0.80, 300, 1000
CAT = {"stocks": ("STOCKS_LONG", "STOCKS_SHORT"), "crypto": ("CRYPTO_LONG", "CRYPTO_SHORT")}
LIVE_KEYS = ("SLOPE_SIZING_LIVE_TWIN_ENABLED", "STDEV_SLOPE_SIZING_MODE", "STDEV_SLOPE_SIZING_D_MAX", "STDEV_SLOPE_SIZING_ENABLED", "BAND_SLOPE_SIZING_V2_ENABLED", "BAND_SLOPE_SIZING_V2_TF")
TW = {"SLOPE_SIZING_LIVE_TWIN_ENABLED": True}
LIB = {"OFF": {}, "NONE": {"STDEV_SLOPE_SIZING_ENABLED": False, "BAND_SLOPE_SIZING_V2_ENABLED": False}, "ON": dict(TW),
       "ON_depth": {**TW, "STDEV_SLOPE_SIZING_MODE": "depth"}, "ON_btt": {**TW, "STDEV_SLOPE_SIZING_MODE": "bottom_to_top"},
       "ON_D4": {**TW, "STDEV_SLOPE_SIZING_D_MAX": 4.0}, "ON_D2": {**TW, "STDEV_SLOPE_SIZING_D_MAX": 2.0},
       "ON_4h": {**TW, "BAND_SLOPE_SIZING_V2_TF": "4h"}, "ON_bandonly": {**TW, "STDEV_SLOPE_SIZING_ENABLED": False}}
for _g in ("LOCFAV", "SLOPEFAV", "SLOPEFAV_LOCFAV", "VOLTOP", "VOLHI", "VOLLO", "HALF"):
    LIB["ON_" + _g] = {**TW, "SLOPE_SIZING_GATE": _g}
FEATS = ("vol15m_pct", "atr1h_pct", "abs_slope_D", "range_pct")


def now():
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def log(rows):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "stage", "cat_side", "item", "old", "new", "evidence"])
        for r in rows:
            w.writerow([now(), *r])


def cat_of(ss):
    sym, side = ss.rsplit("_", 1)
    return f"{'CRYPTO' if sym.endswith(('USDT', 'USDC', 'USD1', 'USDS', 'BUSD', 'FDUSD', 'TUSD', 'DAI')) else 'STOCKS'}_{side}"


def universe(venue):
    f = sorted(glob.glob(str(ROOT / "data" / "daily_universe" / "*.json")))[-1]
    u = json.load(open(f))
    return [f"{s}_{d}" for s in u["stocks" if venue == "stocks" else "crypto"] for d in ("LONG", "SHORT")]


def engine_ok_for_crypto():
    try:
        return "htf_causal_align" in (ROOT / "v12_quick_engine.py").read_text()
    except OSError:
        return False


def collect(a):
    if a.venue == "crypto" and not engine_ok_for_crypto():
        print("REFUSED: crypto needs the AUDIT/001 engine (htf_causal_align); not collecting"); return None
    ss = universe(a.venue); stamp = now(); d = OUT / "autoset" / stamp; d.mkdir(parents=True, exist_ok=True)
    nproc = a.procs
    subprocess.run(["bash", str(ROOT / "tools" / "flt2_overlay.sh"), a.host, "/tmp/slp_empty_stage", "slp_auto"], check=False)
    subprocess.run(["scp", "-q", str(ROOT / "tools" / "slp_worker_rules.py"), f"{a.host}:/tmp/slp_worker_rules.py"], check=True)
    cmd = f"rm -rf /tmp/slp_auto_out; mkdir -p /tmp/slp_auto_out; "
    for i in range(nproc):
        part = ",".join(ss[i::nproc])
        cmd += f"nohup sh -c 'cd /tmp && ~/binance-sandbox/.venv/bin/python /tmp/slp_worker_rules.py slp_auto {part} /tmp/slp_auto_out/part{i}.jsonl {a.offsets}' > /tmp/slp_auto_out/log{i}.txt 2>&1 & "
    subprocess.run(["ssh", a.host, cmd + "sleep 1; echo started"], check=True)
    print(f"collect started on {a.host}; poll `ssh {a.host} 'pgrep -fc [s]lp_worker_rules'` then `pull --dir {d}`")
    (d / "WHERE").write_text(a.host)
    return d


def pull(a):
    d = Path(a.dir); host = a.host
    subprocess.run(["scp", "-q", f"{host}:/tmp/slp_auto_out/part*.jsonl", str(d)], check=True)
    print("pulled", len(list(d.glob("part*.jsonl"))), "files to", d)


def load(d):
    rows = []
    for f in sorted(Path(d).glob("part*.jsonl")):
        for l in f.read_text().splitlines():
            x = json.loads(l)
            if not x.get("err"):
                rows.append(x)
    return rows


def delta_table(rows, cs):
    """per (sym, side, off) delta of every rule vs OFF (current vector default); invalid -> None."""
    out = []
    for r in rows:
        ss = r["ss"]; side = ss.rsplit("_", 1)[1]; sym = ss.rsplit("_", 1)[0]
        if cat_of(ss) != cs:
            continue
        off = r["res"].get("OFF")
        if not off or not off[2] or off[0] is None:
            continue
        d = {k: ((v[0] - off[0]) if (v and v[2] and v[0] is not None) else None) for k, v in r["res"].items() if k != "OFF"}
        out.append({"ss": ss, "sym": sym, "off": r.get("off", 0), "feat": r.get("feat", {}), "d": d})
    return out


def boot_p5(xs):
    xs = np.asarray(xs, float)
    return float(np.percentile([np.mean(np.random.choice(xs, len(xs))) for _ in range(N_BOOT)], 5))


def validate_cat(table, cs):
    cur = [t for t in table if t["off"] == 0]; tmp = [t for t in table if t["off"] > 0]
    ev = {"n": len(cur), "n_temporal": len(tmp), "candidates": {}}
    minn = MIN_SYM_SIDES[cs.split("_")[0]]
    if len(cur) < minn:
        ev["verdict"] = f"NO_VALIDATED_RULE: only {len(cur)} valid sym_sides (< {minn})"; return None, ev
    rules = sorted({k for t in cur for k in t["d"]})
    syms = sorted({t["sym"] for t in cur}); random.seed(11); np.random.seed(11)
    def score(sub, k):
        return float(np.mean([(t["d"].get(k) if t["d"].get(k) is not None else -2.0) for t in sub])) if k != "OFF" else 0.0
    best = None
    for k in rules:
        xs = [t["d"][k] for t in cur if t["d"].get(k) is not None]
        if len(xs) < minn:
            continue
        m = float(np.mean(xs)); p5 = boot_p5(xs)
        pos = 0
        for _ in range(N_SPLITS):
            random.shuffle(syms); A = set(syms[: len(syms) // 2])
            sa = [t for t in cur if t["sym"] in A]; sb = [t for t in cur if t["sym"] not in A]
            ch = max(["OFF"] + rules, key=lambda r_: score(sa, r_))
            pos += score(sb, ch) > 0 if ch != "OFF" else 0
        cvf = pos / N_SPLITS
        tm = [t["d"][k] for t in tmp if t["d"].get(k) is not None]
        ok_t = (not tm) or (np.mean(tm) > 0 and (m > 0))
        ev["candidates"][k] = {"mean": round(m, 3), "p5": round(p5, 3), "cv_pos_frac": round(cvf, 2), "temporal_mean": (round(float(np.mean(tm)), 3) if tm else None)}
        if m >= MIN_EFFECT and p5 > 0 and cvf >= CV_POS_FRAC and ok_t and (best is None or m > best[1]):
            best = (k, m)
    if best is None:
        ev["verdict"] = "NO_VALIDATED_RULE: no candidate passes effect/p5/CV/temporal guards"; return None, ev
    ev["verdict"] = f"VALIDATED {best[0]}"
    return best[0], ev


def decide(a):
    d = Path(a.dir) if a.dir else sorted((OUT / "autoset").glob("*"))[-1]
    rows = load(d); res = {"ts": now(), "source": str(d), "venue": a.venue, "cat_sides": {}, "overrides": {}}
    log_rows = []
    for cs in CAT[a.venue]:
        rule, ev = validate_cat(delta_table(rows, cs), cs)
        res["cat_sides"][cs] = {"rule": rule, **ev}
        log_rows.append(["decide", cs, "rule", "", rule or "NONE", ev["verdict"]])
        if rule:
            for r in rows:
                ss = r["ss"]
                if cat_of(ss) == cs and r.get("off", 0) == 0:
                    res["overrides"][ss] = dict(LIB[rule])
    res["n_sym_sides_with_overrides"] = len(res["overrides"])
    OUT.mkdir(parents=True, exist_ok=True)
    prev = json.loads(RESULT.read_text()) if RESULT.exists() else {}
    if not a.dry_run:
        RESULT.write_text(json.dumps(res, indent=1))
    log(log_rows)
    print(json.dumps({cs: v["verdict"] for cs, v in res["cat_sides"].items()}, indent=1), "| overrides:", len(res["overrides"]), "| dry-run" if a.dry_run else "| written")
    return res


def apply(a):
    res = json.loads(RESULT.read_text()) if RESULT.exists() else {"overrides": {}}
    ov = res.get("overrides", {}); sd = OUT / "start_overrides"; sd.mkdir(parents=True, exist_ok=True); rows = []
    for f in sd.glob("*.json"):
        if f.stem not in ov:
            rows.append(["apply", "", f.stem, "start_override", "REMOVED", "no longer validated"])
            if not a.dry_run: f.unlink()
    for ss, o in ov.items():
        p = sd / f"{ss}.json"; old = json.loads(p.read_text()) if p.exists() else {}
        if old != o:
            rows.append(["apply", "", ss, json.dumps(old), json.dumps(o), "start_override"])
            if not a.dry_run:
                tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(o, indent=1)); os.replace(tmp, p)
    if a.live and ov:
        if not LIVE_PERSYM.exists(): print("live per-sym file missing"); return
        cur = json.loads(LIVE_PERSYM.read_text()); changed = 0
        bk = ROOT / "backups" / f"before_slp_autoset_{now()}_per_sym_active_config.json"
        for ss, o in ov.items():
            if not cat_of(ss).startswith("CRYPTO"):
                rows.append(["apply_live", "", ss, "", "", "SKIPPED stocks: per_sym_active_config.json is crypto-only (its purpose line); stocks use the validated full-recipe overlay"]); continue
            e = cur.setdefault(ss, {})
            for k, v in o.items():
                if k in LIVE_KEYS and e.get(k) != v:
                    rows.append(["apply_live", "", ss, f"{k}={e.get(k)}", f"{k}={v}", "per_sym_active_config merge"]); e[k] = v; changed += 1
        if changed and not a.dry_run:
            shutil.copy2(LIVE_PERSYM, bk); tmp = LIVE_PERSYM.with_suffix(".tmp"); tmp.write_text(json.dumps(cur)); os.replace(tmp, LIVE_PERSYM)
    log(rows); print(f"apply: {len(rows)} changes{' (dry-run)' if a.dry_run else ''}")
    for r in rows[:20]: print("  ", r)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("stage", choices=["collect", "pull", "decide", "apply", "run"])
    ap.add_argument("--venue", default="stocks", choices=["stocks", "crypto"]); ap.add_argument("--host", default="s2"); ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--offsets", default="0,30"); ap.add_argument("--dir"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.stage == "collect": collect(a)
    elif a.stage == "pull": pull(a)
    elif a.stage == "decide": decide(a)
    elif a.stage == "apply": apply(a)
    else:
        d = collect(a)
        print("run: collect started; schedule `pull` + `decide` + `apply` after it finishes (cron: tools/v15_slope_sizing_autoset.py pull/decide/apply)")


if __name__ == "__main__":
    main()
