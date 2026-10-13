#!/usr/bin/env python3
"""v15_causal_rescue — the CONSUMER of the causal learner's `rescue` shortlist (bible §85/§94, USER 2026-10-13).

The learner exports `rescue` (proposed switches with heavy negative sweep evidence but 1-2 positives) for an
"ablation rerun instead of a kill". Nothing ran it. This tool does, with the REAL engine only
(tools.opt.evaluate_v12.prepare + tools.opt.v12_pilot.evaluate_prepared_sanitized — the same path as
tools/v15_switch_flip_study.py; nothing reimplemented, nothing estimated):

  per rescue switch, per cat_side, on a few deterministic autopsy bases (~/v15_autopsy_first/*_autopsy_base.json):
    LOO   : base set  vs  base with the switch neutralised (QuickConfig default if the base differs from it,
            else OFF for a *_TF* switch / False for an active bool; numeric-at-default = NOT_ACTIVE, no eval)
    NAKED : base set  vs  base + switch=cand for the cat_side's most-sampled profile cands (up to --naked-cands)
  d_x = ablated/naked − base for gain_pct, trades, tim_pct, max_dd_pct, wr_pct (+ validity).

Verdict per (switch, cat_side) from the LOO deltas (d_gain = what neutralising the switch does):
  DISABLE_CANDIDATE  ablation helps on >= 3 sym_sides and never hurts
  KEEP               ablation hurts on >= 1 and never helps
  INSUFFICIENT       ablation helps on 1-2, never hurts
  MIXED              helps and hurts
  NOT_ACTIVE         switch never active / identity on every base
Every verdict carries `[DIAGNOSTIC ONLY]` below the sample floor (>=48 crypto / >=100 stocks sym_sides) — a
handful of bases never promotes anything; it ranks the next sweep. No Sharpe is computed or emitted.

DB: outcomes land in the learner DB as fixes(kind='rescue_ablation', scope='cat:<CS>', target=<switch>) +
one fix_outcomes row per evaluated sym_side (metric_after = LOO d_gain, verdict ablation_helped / ablation_hurt /
ablation_neutral). Distinct verdict names on purpose: the learner's disable_switch confirm/auto-lift logic
keys on 'helped'/'hurt' and is NOT altered by this tool.

Cap: `cap` prunes the OLDEST causes rows (lessons rows stay as tombstones so the content-hash idempotency of
ingest-progress never re-adds them; causes of confirmed/rejected fix targets are protected), then VACUUM.
Refuses unless PRAGMA integrity_check is ok, or while a v15_causal_learner process runs. Dry-run by default.

Usage:
  python3 tools/v15_causal_rescue.py run [--workers 4] [--max-switches N] [--bases-per-cat 4] [--budget-s S]
                                         [--out data/causal/rescue_outcomes.json] [--db PATH --ingest] [--dry-run]
  python3 tools/v15_causal_rescue.py ingest-outcomes data/causal/rescue_outcomes.json [--db PATH]
  python3 tools/v15_causal_rescue.py cap [--max-gb 2.0] [--apply] [--db PATH]
"""
import argparse
import collections
import concurrent.futures as cf
import glob
import hashlib
import json
import multiprocessing as mp
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(ROOT, "tools"))  # append: tools/ must never shadow root engine modules (tools/cat_side_defaults.py)
CATS = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
DENY = {"MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED"}
METRICS = ("gain_pct", "tim_pct", "max_dd_pct", "wr_pct", "trades", "valid", "invalid_reason", "bh_pct")
FLOOR = {"CRYPTO": 48, "STOCKS": 100}
EPS = 1e-9


def _now():
    return datetime.now(timezone.utc).isoformat()


def cat_side_of(ss):
    sym, _, side = str(ss).upper().rpartition("_")
    return f"{'CRYPTO' if sym.endswith(CRYPTO_SUFFIX) else 'STOCKS'}_{side}"


def load_rescue(path, max_switches=None, include_proposed=0):
    """rescue shortlist (+ optional top-N fixes_proposed targets) -> ordered unique switch list."""
    d = json.load(open(path))
    out = []
    for r in d.get("rescue") or []:
        sw = str((r or {}).get("switch") or "").strip()
        if sw and not sw.startswith("ABLATION_") and sw not in DENY and sw not in out:
            out.append(sw)
    if include_proposed:
        for f in (d.get("fixes_proposed") or [])[: int(include_proposed)]:
            sw = str((f or {}).get("target") or "").strip()
            if sw and not sw.startswith("ABLATION_") and sw not in DENY and sw not in out:
                out.append(sw)
    return out[: max_switches] if max_switches else out


def _rot_key(path, salt):
    return hashlib.sha1(f"{salt}|{os.path.basename(path)}".encode()).hexdigest()


def load_bases(bases_dir, per_cat, cats=CATS, min_trades=10, salt=None):
    """deterministic per ISO week (salt): sym_sides ordered by sha1(salt|file), first `per_cat` per cat_side with
    >= min_trades and an override set. The weekly salt rotates which bases are re-measured; same week = same bases."""
    if salt is None:
        y, w, _ = datetime.now(timezone.utc).isocalendar()
        salt = f"{y}-W{w:02d}"
    pool = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(bases_dir, "*_autopsy_base.json")), key=lambda q: _rot_key(q, salt)):
        ss = os.path.basename(p)[: -len("_autopsy_base.json")]
        cs = cat_side_of(ss)
        if cs not in cats or len(pool[cs]) >= per_cat:
            continue
        try:
            d = json.load(open(p))
            ov = ((d.get("final") or {}).get("overrides")) or {}
            a = d.get("autopsy") or {}
            if not ov or int(a.get("combo_trades") or a.get("base_trades") or 0) < min_trades:
                continue
            pool[cs].append((ss, ov))
        except Exception:
            continue
    return dict(pool)


def profile_cands(profile, cs, sw, k, bible=None):
    """most-sampled `sw=cand` keys of the cat_side's causal profile (real fleet evidence), up to k;
    fallback: the SWITCH_BIBLE registry valid_options of the switch."""
    sc = (((profile or {}).get("cat_sides") or {}).get(cs) or {}).get("switch_cand") or {}
    rows = [(key.split("=", 1)[1], v.get("n_sym") or 0) for key, v in sc.items() if key.split("=", 1)[0] == sw and "=" in key]
    rows.sort(key=lambda t: (-t[1], str(t[0])))
    out = [c for c, _ in rows[:k]]
    if not out and bible:
        vo = (((bible.get(sw) or {}).get("agent_c_registry") or {}).get("valid_options")) or []
        out = [str(v) for v in vo][:k]
    return out


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).strip().lower() == str(b).strip().lower()
    try:
        return abs(float(a) - float(b)) < 1e-12
    except Exception:
        return str(a).strip() == str(b).strip()


def ablation_value(sw, cur, default):
    """neutral value for LOO, or None when the switch is not active on this base (identity -> no eval)."""
    if not _same(cur, default):
        return default
    if isinstance(cur, bool):
        return False if cur else None
    if "_TF" in sw and isinstance(cur, str) and cur.strip().upper() not in ("OFF", "NONE", ""):
        return "OFF"
    return None


def _m(r):
    return {k: (r or {}).get(k) for k in METRICS}


def _delta(a, b):
    out = {}
    for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "wr_pct"):
        try:
            out["d_" + k.replace("_pct", "")] = round(float(a.get(k)) - float(b.get(k)), 4)
        except Exception:
            out["d_" + k.replace("_pct", "")] = None
    out["valid"] = bool(a.get("valid"))
    out["invalid_reason"] = a.get("invalid_reason")
    return out


def _w_task(task):
    """fork worker: one sym_side base -> prepare once -> base + LOO + naked evals (real engine)."""
    ss, base_ov, plan, deadline = task
    try:
        import dataclasses
        import v12_quick_engine as VQ
        import v15_pilot as P
        from tools.opt import evaluate_v12 as E
        from tools.opt import v12_pilot as VP
        _qi = VQ.QuickConfig()
        qd = {f.name: getattr(_qi, f.name, None) for f in dataclasses.fields(VQ.QuickConfig)}
        prep = E.prepare(ss, 30)
        if prep is None:
            return ss, None, "prepare failed (no npz)"
        base = {k: v for k, v in dict(base_ov).items() if k not in DENY and not str(k).startswith("ABLATION_")}
        base = P.sanitize_overrides(base, qd)[0]
        b = _m(VP.evaluate_prepared_sanitized(prep, base, 30))
        out = {"base": b, "evals": {}, "errors": 0}

        def _run(sw, val):
            pv = P._parse_opt_value(val, qd.get(sw))
            sw_ov = P._switch_overrides(sw, pv)
            if all(_same(base.get(k, qd.get(k)), v) for k, v in sw_ov.items()):
                return {"identity": True}
            ov = dict(base)
            ov.update(sw_ov)
            ov = P.sanitize_overrides(ov, qd)[0]
            r = _m(VP.evaluate_prepared_sanitized(prep, ov, 30))
            return dict(_delta(r, b), value=str(val))

        for sw, cands in plan:
            if deadline and time.time() > deadline:
                out["evals"][sw] = {"skipped": "budget"}
                continue
            rec = {}
            if sw not in qd:
                out["evals"][sw] = {"skipped": "not a QuickConfig field"}
                continue
            cur = base.get(sw, qd.get(sw))
            if isinstance(qd.get(sw), (dict, list, set, tuple)):
                out["evals"][sw] = {"loo": {"not_ablatable": type(qd.get(sw)).__name__}, "naked": {}}
                continue
            av = ablation_value(sw, cur, qd.get(sw))
            try:
                rec["loo"] = {"not_active": True, "current": str(cur)} if av is None else dict(_run(sw, av), current=str(cur))
            except Exception as e:
                rec["loo"] = {"err": f"{type(e).__name__}: {e}"[:120]}
                out["errors"] += 1
            rec["naked"] = {}
            for c in cands:
                try:
                    rec["naked"][str(c)] = _run(sw, c)
                except Exception as e:
                    rec["naked"][str(c)] = {"err": f"{type(e).__name__}: {e}"[:120]}
                    out["errors"] += 1
            out["evals"][sw] = rec
        return ss, out, ""
    except Exception as e:
        return ss, None, f"{type(e).__name__}: {e}"[:160]


def verdict_of(loo_rows):
    """loo_rows: [{'d_gain':x,'valid':bool}, ...] (identity/not-active/errors already removed)."""
    helps = sum(1 for r in loo_rows if r.get("d_gain") is not None and r["d_gain"] > EPS and r.get("valid"))
    hurts = sum(1 for r in loo_rows if r.get("d_gain") is not None and r["d_gain"] < -EPS)
    if not loo_rows:
        return "NOT_ACTIVE", helps, hurts
    if helps >= 3 and hurts == 0:
        return "DISABLE_CANDIDATE", helps, hurts
    if hurts >= 1 and helps == 0:
        return "KEEP", helps, hurts
    if helps and hurts:
        return "MIXED", helps, hurts
    if helps:
        return "INSUFFICIENT", helps, hurts
    return "NEUTRAL", helps, hurts


def naked_verdict(rows):
    """rows: naked cand evals (any cand). Used when LOO is NOT_ACTIVE (the switch sits at default on the bases,
    which is exactly where the sweep's negative evidence came from: trying its candidates)."""
    pos = sum(1 for r in rows if r.get("d_gain") is not None and r["d_gain"] > EPS and r.get("valid"))
    neg = sum(1 for r in rows if r.get("d_gain") is not None and r["d_gain"] < -EPS)
    if not rows:
        return "NO_EVIDENCE", pos, neg
    if pos >= 3 and neg == 0:
        return "RESCUE_CANDIDATE", pos, neg
    if pos == 0 and neg >= 1:
        return "CONFIRM_NEGATIVE", pos, neg
    if pos and neg:
        return "MIXED", pos, neg
    if pos:
        return "INSUFFICIENT", pos, neg
    return "INERT", pos, neg


def summarise(results, plan_by_cs, switches):
    summ = {}
    for cs, sss in plan_by_cs.items():
        venue = cs.split("_")[0]
        for sw in switches:
            loo, naked = [], collections.defaultdict(list)
            n_base_invalid = 0
            for ss in sss:
                if (results.get(ss) or {}).get("base", {}).get("valid") is False:
                    n_base_invalid += 1
                    continue
                r = (results.get(ss) or {}).get("evals", {}).get(sw) or {}
                lo = r.get("loo") or {}
                if lo and not lo.get("identity") and not lo.get("not_active") and not lo.get("err") and "d_gain" in lo:
                    loo.append(dict(lo, ss=ss))
                for c, nr in (r.get("naked") or {}).items():
                    if nr and "d_gain" in nr and not nr.get("identity"):
                        naked[c].append(dict(nr, ss=ss))
            v, helps, hurts = verdict_of(loo)
            all_naked = [x for rows in naked.values() for x in rows]
            nv, npos, nneg = naked_verdict(all_naked)
            if v == "NOT_ACTIVE":
                v = "NAKED_" + nv

            def _mean(rows, k):
                xs = [x[k] for x in rows if x.get(k) is not None]
                return round(sum(xs) / len(xs), 4) if xs else None
            n_eval = len(loo)
            summ.setdefault(sw, {})[cs] = {
                "verdict": v, "label": "[DIAGNOSTIC ONLY]" if n_eval < FLOOR[venue] else "", "n_loo": n_eval, "n_base_invalid": n_base_invalid,
                "ablation_helps": helps, "ablation_hurts": hurts, "naked_verdict": nv, "naked_pos": npos, "naked_neg": nneg, "n_naked": len(all_naked),
                "mean_d_gain": _mean(loo, "d_gain"), "mean_d_trades": _mean(loo, "d_trades"), "mean_d_tim": _mean(loo, "d_tim"),
                "mean_d_max_dd": _mean(loo, "d_max_dd"), "mean_d_wr": _mean(loo, "d_wr"),
                "naked": {c: {"n": len(rows), "pos": sum(1 for x in rows if x["d_gain"] is not None and x["d_gain"] > EPS and x.get("valid")), "mean_d_gain": _mean(rows, "d_gain")} for c, rows in naked.items()},
                "per_sym": [{k: x.get(k) for k in ("ss", "d_gain", "d_trades", "d_tim", "d_max_dd", "d_wr", "valid", "value", "current")} for x in loo],
                "per_sym_naked": [dict({k: x.get(k) for k in ("ss", "d_gain", "d_trades", "d_tim", "d_max_dd", "d_wr", "valid")}, cand=c) for c, rows in naked.items() for x in rows],
            }
    return summ


def write_md(out, path):
    m = out["_meta"]
    L = [f"# Causal RESCUE ablation — {m['at']} on {m['host']}", "",
         f"Engine: {m['engine']}. {m['ok']}/{m['tasks']} bases evaluated in {m['secs']}s, errors {m['n_errors']}. "
         "LOO = neutralise the switch on a real autopsy base (d = ablated − base). When the switch already sits at its "
         "default/OFF on every base (NOT_ACTIVE) the verdict comes from the NAKED cand evals (NAKED_RESCUE_CANDIDATE = cand "
         "positive on >=3 bases and never negative; NAKED_CONFIRM_NEGATIVE = never positive; NAKED_INERT = zero everywhere). Below the sample floor every row is "
         "[DIAGNOSTIC ONLY] — ranking for the next sweep, never a promotion. No Sharpe emitted.", "",
         "| switch | cat_side | verdict | n LOO | helps | hurts | d_gain | d_trades | d_tim | d_dd | d_wr | naked (cand: pos/n mean) | label |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for sw, per in out["switches"].items():
        for cs, e in per.items():
            nk = "; ".join(f"{c}: {v['pos']}/{v['n']} {v['mean_d_gain']}" for c, v in e["naked"].items())
            L.append(f"| `{sw}` | {cs} | {e['verdict']} | {e['n_loo']} | {e['ablation_helps']} | {e['ablation_hurts']} | {e['mean_d_gain']} | {e['mean_d_trades']} | {e['mean_d_tim']} | {e['mean_d_max_dd']} | {e['mean_d_wr']} | {nk} | {e['label']} |")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")


def run(a):
    switches = load_rescue(a.condemned, a.max_switches, a.include_proposed)
    profile = {}
    try:
        profile = json.load(open(a.profile))
    except Exception:
        pass
    bible = {}
    try:
        bible = json.load(open(os.path.join(ROOT, "data", "SWITCH_BIBLE.json"))).get("switches") or {}
    except Exception:
        pass
    bases = load_bases(a.bases, a.bases_per_cat, [c for c in a.cats.split(",") if c], salt=a.salt or None)
    deadline = (time.time() + a.budget_s) if a.budget_s else 0
    tasks, plan_by_cs = [], {}
    for cs, rows in bases.items():
        plan = [(sw, profile_cands(profile, cs, sw, a.naked_cands, bible)) for sw in switches]
        plan_by_cs[cs] = [ss for ss, _ in rows]
        for ss, ov in rows:
            tasks.append((ss, ov, plan, deadline))
    print(f"[rescue] switches={len(switches)} bases={ {c: len(v) for c, v in plan_by_cs.items()} } tasks={len(tasks)} workers={a.workers}", flush=True)
    if a.dry_run:
        print(json.dumps({"switches": switches, "bases": plan_by_cs}, indent=1))
        return 0
    results, errors, t0 = {}, {}, time.time()
    with cf.ProcessPoolExecutor(max_workers=a.workers, mp_context=mp.get_context("fork")) as ex:
        for i, (ss, out, err) in enumerate(ex.map(_w_task, tasks)):
            if out is None:
                errors[ss] = err
            else:
                results[ss] = out
            print(f"[rescue] {i + 1}/{len(tasks)} {ss} {'ERR ' + err if err else 'ok'} {time.time() - t0:.0f}s", flush=True)
    n_err = len(errors) + sum(r.get("errors", 0) for r in results.values())
    out = {"_meta": {"at": _now(), "host": os.uname().nodename, "secs": round(time.time() - t0, 1), "tasks": len(tasks), "ok": len(results),
                     "n_errors": n_err, "errors": errors, "switches": switches, "bases": plan_by_cs,
                     "engine": "tools.opt.v12_pilot.evaluate_prepared_sanitized (real v12_quick_engine)",
                     "run_id": hashlib.sha1(f"{_now()}|{os.uname().nodename}".encode()).hexdigest()[:12]},
           "switches": summarise(results, plan_by_cs, switches), "raw": results}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out + ".tmp", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=str)
    os.replace(a.out + ".tmp", a.out)
    write_md(out, a.out[:-5] + ".md" if a.out.endswith(".json") else a.out + ".md")
    for sw, per in out["switches"].items():
        print(f"[rescue] {sw}: " + ", ".join((f"{cs}={e['verdict']}(naked {e['naked_pos']}+/{e['naked_neg']}- of {e['n_naked']})" if e['verdict'].startswith("NAKED_") else f"{cs}={e['verdict']}(loo {e['ablation_helps']}+/{e['ablation_hurts']}- of {e['n_loo']}, d_gain {e['mean_d_gain']})") + e['label'] for cs, e in per.items()), flush=True)
    print(json.dumps({k: out["_meta"][k] for k in ("at", "host", "secs", "tasks", "ok", "n_errors", "run_id")}))
    if a.ingest:
        print(json.dumps(ingest_outcomes(a.out, a.db)))
    return 0


def _connect(db):
    import v15_causal_learner as CL  # noqa: E402 (tools/ on path below)
    return CL.connect(db)


def ingest_outcomes(path, db=None):
    """outcomes json -> fixes(kind=rescue_ablation) + fix_outcomes. Idempotent per (fix, run ts, sym_side)."""
    d = json.load(open(path))
    ts = (d.get("_meta") or {}).get("at") or _now()
    run_id = (d.get("_meta") or {}).get("run_id") or ""
    cx = _connect(db)
    n_fix = n_out = n_skip = 0
    try:
        for sw, per in (d.get("switches") or {}).items():
            for cs, e in (per or {}).items():
                pl = {k: e.get(k) for k in ("verdict", "label", "n_loo", "ablation_helps", "ablation_hurts", "mean_d_gain", "mean_d_trades", "mean_d_tim", "mean_d_max_dd", "mean_d_wr")}
                pl.update(run_id=run_id, at=ts)
                payload = json.dumps(pl)
                reason = f"rescue ablation {e.get('verdict')} {e.get('ablation_helps')}+/{e.get('ablation_hurts')}- n={e.get('n_loo')} {e.get('label') or ''}".strip()
                cur = cx.execute("INSERT OR IGNORE INTO fixes(kind, scope, target, payload, status, reason, created) VALUES (?,?,?,?,?,?,?)",
                                 ("rescue_ablation", f"cat:{cs}", sw, payload, "proposed", reason, ts))
                n_fix += cur.rowcount or 0
                cx.execute("UPDATE fixes SET payload = ?, reason = ? WHERE kind = 'rescue_ablation' AND scope = ? AND target = ?", (payload, reason, f"cat:{cs}", sw))
                fid = cx.execute("SELECT id FROM fixes WHERE kind = 'rescue_ablation' AND scope = ? AND target = ?", (f"cat:{cs}", sw)).fetchone()[0]
                for r in e.get("per_sym") or []:
                    dg = r.get("d_gain")
                    if dg is None:
                        continue
                    vd = "ablation_helped" if (dg > EPS and r.get("valid")) else ("ablation_hurt" if dg < -EPS else "ablation_neutral")
                    if cx.execute("SELECT 1 FROM fix_outcomes WHERE fix_id = ? AND ts = ? AND sym_side = ?", (fid, ts, r.get("ss") or "")).fetchone():
                        n_skip += 1
                        continue
                    cx.execute("INSERT INTO fix_outcomes(fix_id, ts, sym_side, metric_after, verdict) VALUES (?,?,?,?,?)", (fid, ts, r.get("ss") or "", float(dg), vd))
                    n_out += 1
                for r in e.get("per_sym_naked") or []:
                    dg = r.get("d_gain")
                    if dg is None:
                        continue
                    vd = "naked_pos" if (dg > EPS and r.get("valid")) else ("naked_neg" if dg < -EPS else "naked_zero")
                    key = f"{r.get('ss') or ''}|{sw}={r.get('cand')}"
                    if cx.execute("SELECT 1 FROM fix_outcomes WHERE fix_id = ? AND ts = ? AND sym_side = ?", (fid, ts, key)).fetchone():
                        n_skip += 1
                        continue
                    cx.execute("INSERT INTO fix_outcomes(fix_id, ts, sym_side, metric_after, verdict) VALUES (?,?,?,?,?)", (fid, ts, key, float(dg), vd))
                    n_out += 1
        cx.commit()
    finally:
        cx.close()
    return {"fixes_new": n_fix, "outcomes": n_out, "skipped_existing": n_skip, "db": str(db or os.path.join(ROOT, "data", "causal_learner.db"))}


def _learner_running():
    try:
        r = subprocess.run(["pgrep", "-f", "v15_causal_learner.py"], capture_output=True, text=True)
        return [p for p in r.stdout.split() if p.strip() and int(p) != os.getpid()]
    except Exception:
        return []


def cap_plan(cx, db_bytes, max_bytes):
    """-> dict(cutoff_cause_id, n_delete_est, ...) or {'action':'none'}; protects causes of confirmed/rejected targets."""
    n_causes, max_id = cx.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM causes").fetchone()
    if db_bytes <= max_bytes or not n_causes:
        return {"action": "none", "db_bytes": db_bytes, "max_bytes": max_bytes, "causes": n_causes}
    bpc = db_bytes / float(n_causes)
    keep = int((max_bytes * 0.9) / bpc)
    n_drop = max(0, n_causes - keep)
    cutoff = cx.execute("SELECT id FROM causes ORDER BY id LIMIT 1 OFFSET ?", (max(0, n_drop - 1),)).fetchone()
    protected = sorted(r[0] for r in cx.execute("SELECT DISTINCT target FROM fixes WHERE status IN ('confirmed','rejected')"))
    return {"action": "prune", "db_bytes": db_bytes, "max_bytes": max_bytes, "causes": n_causes, "bytes_per_cause": round(bpc, 1),
            "keep_target": keep, "drop_target": n_drop, "cutoff_cause_id": cutoff[0] if cutoff else 0, "protected_targets": protected}


def cap(db=None, max_gb=None, apply=False, vacuum=True):
    db = str(db or os.path.join(ROOT, "data", "causal_learner.db"))
    max_gb = float(max_gb if max_gb is not None else os.environ.get("V15_CAUSAL_DB_MAX_GB", "2.0"))
    if not os.path.exists(db):
        return {"error": f"no db {db}"}
    if apply and _learner_running():
        return {"refused": "v15_causal_learner is running (chain 4c?) — retry later"}
    cx = sqlite3.connect(db)
    try:
        ic = cx.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            return {"refused": f"integrity_check={ic}"}
        size = os.path.getsize(db)
        plan = cap_plan(cx, size, max_gb * 1024 ** 3)
        plan["apply"] = bool(apply)
        if plan["action"] == "none" or not apply:
            return plan
        prot = plan["protected_targets"]
        q = "DELETE FROM causes WHERE id <= ?"
        params = [plan["cutoff_cause_id"]]
        if prot:
            q += f" AND switch_key NOT IN ({','.join('?' for _ in prot)})"
            params += prot
        cur = cx.execute(q, params)
        plan["deleted_causes"] = cur.rowcount
        # tombstone (keep row + source_id so ingest-progress never re-adds the lesson), shrink context
        cur = cx.execute("UPDATE lessons SET context = '{\"pruned\":1}' WHERE id NOT IN (SELECT DISTINCT lesson_id FROM causes) AND context != '{\"pruned\":1}'")
        plan["tombstoned_lessons"] = cur.rowcount
        cx.commit()
        if vacuum:
            cx.execute("VACUUM")
        plan["db_bytes_after"] = os.path.getsize(db)
        plan["integrity_after"] = cx.execute("PRAGMA integrity_check").fetchone()[0]
        return plan
    finally:
        cx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "ingest-outcomes", "cap"])
    ap.add_argument("path", nargs="?", default="")
    ap.add_argument("--condemned", default=os.path.join(ROOT, "data", "causal_condemned.json"))
    ap.add_argument("--profile", default=os.path.join(ROOT, "data", "causal", "switch_causal_profile.json"))
    ap.add_argument("--bases", default=os.path.expanduser("~/v15_autopsy_first"))
    ap.add_argument("--bases-per-cat", type=int, default=4)
    ap.add_argument("--cats", default=",".join(CATS))
    ap.add_argument("--salt", default="", help="base rotation salt (default: ISO week)")
    ap.add_argument("--max-switches", type=int, default=0)
    ap.add_argument("--include-proposed", type=int, default=0, help="also ablate the top-N fixes_proposed targets")
    ap.add_argument("--naked-cands", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget-s", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "causal", "rescue_outcomes.json"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--ingest", action="store_true", help="after run, write outcomes into the learner DB")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-gb", type=float, default=None)
    ap.add_argument("--apply", action="store_true", help="cap: actually prune + VACUUM (default dry-run)")
    a = ap.parse_args()
    if a.cmd == "run":
        return run(a)
    if a.cmd == "ingest-outcomes":
        print(json.dumps(ingest_outcomes(a.path or a.out, a.db)))
        return 0
    print(json.dumps(cap(a.db, a.max_gb, a.apply), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
