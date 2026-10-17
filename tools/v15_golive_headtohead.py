#!/usr/bin/env python3
"""Head-to-head on ONE NPZ: the live set vs the latest published set, and only a real winner goes live.

USER 2026-10-16: "make sure no more exaggeration of technicals and gains can ever take place".

THE LEAK, MEASURED. `tools/v15_persym_golive.py` re-evaluates a candidate set on THE BOARD'S OWN NPZ
(`V12_ALLOW_STALE_NPZ=1`, npz_id from the evidence), so every board's claim is confirmed against the window it
was measured in — and then the newest/biggest claim is applied. Two boards for the same sym_side are therefore
ranked against each other across DIFFERENT windows. Measured 2026-10-16 18:4xZ, both sets on the same current
NPZ, realised closes only:

    sym_side            live set now    its stored claim   today's board set now
    AAVEUSDC_LONG             -4.97            +43.02             +20.92
    CHRUSDT_LONG              -3.01            +81.52             +20.67
    DOGEUSDC_SHORT            -0.14            +38.60             +34.65
    BBUSDT_LONG               +1.84           +100.87             +60.82
    1000PEPEUSDC_LONG         +1.50            +68.55             +20.29
    EDUUSDT_LONG              +1.07            +45.00             +21.70
    ONDOUSDT_LONG            +28.44            +58.83             +36.71
    COTIUSDT_SHORT           +74.18            +67.92             +50.21   <- live genuinely better, kept

Seven of eight live sets were running on a claim that does not survive re-measurement, while a set measured
this morning was worth 20-60 points more on the same data. No arithmetic on stored numbers can see this; only
measuring both on one NPZ can.

THE RULE. Candidate and incumbent are measured in the SAME process, on the SAME NPZ, with the SAME window, and
through metrics_guard.realised_closes (an unrealised window-edge mark is never gain). The candidate must win by
more than the board-nondeterminism tolerance (BIBLE: <=0.48 gain between identical runs) or nothing changes.
A tie keeps the incumbent — churning live settings for noise is its own cost.

  worker:  python3 tools/v15_golive_headtohead.py measure --sym-sides A_LONG,B_SHORT --out /tmp/h2h.json
  mac:     python3 tools/v15_golive_headtohead.py apply --in /tmp/h2h.json [--apply]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOL = float(os.environ.get("V15_H2H_TOL", "0.48"))  # BIBLE board nondeterminism; a smaller win is noise


def _gain_and_trades(sym_side, ov, window_days):
    """-> (gain_pct, trades, dd_pct, tim_pct, wr_pct, why). Realised closes only, current NPZ, no stale escape."""
    import metrics_guard as MG
    from tools.opt.lifecycle_pilot import evaluate_month
    from tools.opt.evaluate_v12 import _honest_gain_pct as _g, _honest_max_dd_pct as _dd
    try:
        r = evaluate_month(sym_side, dict(ov), include_ledger=True, window_days=int(window_days))
    except Exception as e:
        return None, None, None, None, None, f"{type(e).__name__}: {str(e)[:90]}"
    if not r.get("valid"):
        return None, None, None, None, None, f"invalid: {str(r.get('invalid_reason'))[:80]}"
    led = r.get("execution_ledger") or []
    real = MG.realised_closes(led)
    if not real:
        return None, None, None, None, None, f"no realised close ({len(led)} ledger rows)"
    countable = {"ledger": [t for t in led if not MG.is_unrealised_mark(t)], "mean_deployed": r.get("mean_deployed")}
    wins = sum(1 for t in real if float(t.get("pnl_dollars") or 0.0) > 0)
    return (round(_g(countable), 4), len(real), round(_dd(countable), 4),
            r.get("tim_pct"), round(100.0 * wins / len(real), 2), "")


def _candidate_set(sym_side):
    """The latest PUBLISHED set for a sym_side, from per_sym_history publish rows. -> (set, meta) or (None, why)."""
    import sqlite3
    import per_sym_store as PS
    try:
        c = sqlite3.connect(f"file:{PS.DB_PATH}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "select updated_at, winning_tag, gain_pct, overrides_json, meta_json from per_sym_history "
            "where sym_side = ? and json_extract(meta_json, '$.source') = 'fleet_orchestrator_publish' "
            "order by updated_at desc, id desc limit 1", (sym_side,)).fetchall()
        c.close()
    except Exception as e:
        return None, f"history unreadable: {type(e).__name__}: {str(e)[:60]}"
    if not rows:
        return None, "no publish row"
    r = rows[0]
    try:
        ov = json.loads(r["overrides_json"] or "{}")
    except Exception:
        return None, "publish row overrides_json unparseable"
    if not isinstance(ov, dict) or len(ov) < 20:
        return None, f"publish row carries {len(ov) if isinstance(ov, dict) else 0} keys — nothing usable"
    return ov, {"published_at": r["updated_at"], "winning_tag": r["winning_tag"], "claimed_gain": r["gain_pct"],
                "publish_row_keys": len(ov)}


def _candidate_catside(sym_side):
    """The CURRENT cat_side defaults for this sym_side. -> (set, meta) or (None, why).

    USER 2026-10-17: "nothing trades old backtest settings, >14 days needs to verify whether old settings give
    better results than latest defaults for cat_sym and use the best scoring one". A per-sym set proven weeks ago
    is not self-evidently better than today's cat_side defaults, and nothing was re-checking that. This makes the
    cat_side layer a first-class CANDIDATE so the same one-NPZ head-to-head decides it: the old set only survives
    if it still wins by more than the nondeterminism tolerance. No new measurement path — same engine, same rule."""
    try:
        import cat_side_defaults as _csd
    except Exception as e:
        return None, f"cat_side_defaults unimportable: {type(e).__name__}: {str(e)[:60]}"
    sym, _, side = sym_side.rpartition("_")
    crypto = sym.upper().endswith(("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI"))
    cat_side = f"{'CRYPTO' if crypto else 'STOCKS'}_{side.upper()}"
    try:
        ov = dict(_csd.defaults(cat_side) or {})
    except Exception as e:
        return None, f"cat_side defaults unreadable: {type(e).__name__}: {str(e)[:60]}"
    if len(ov) < 20:
        return None, f"cat_side {cat_side} carries {len(ov)} keys — nothing usable"
    return ov, {"cat_side": cat_side, "cat_side_keys": len(ov), "source": "cat_side_defaults"}


def _stale_sym_sides(days):
    """SYM_SIDEs whose live per_sym row was last written more than `days` ago, oldest first."""
    import datetime
    import sqlite3

    import per_sym_store as PS
    out = []
    try:
        c = sqlite3.connect(f"file:{PS.DB_PATH}?mode=ro", uri=True)
        rows = c.execute("select sym_side, updated_at from per_sym_active").fetchall()
        c.close()
    except Exception:
        return out
    now = time.time()
    for sym_side, updated_at in rows:
        try:
            t = datetime.datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            continue
        age_d = (now - t.timestamp()) / 86400.0
        if age_d > float(days):
            out.append((age_d, sym_side))
    out.sort(reverse=True)
    return [ss for _, ss in out]


def _candidate_full(incumbent, cand):
    """The candidate as a COMPLETE set: the incumbent full set with the publish row's keys written over it.

    USER 2026-10-15 law, re-learned the hard way 2026-10-16 18:24Z: per_sym is a COMPLETE set, `overrides` is a
    TEMPLATE-only concept. `per_sym_history.overrides_json` is a DIFF on many rows (measured: ETHFIUSDC_LONG 56
    keys, TRBUSDT_LONG 41, ZECUSDC_LONG 76, against ~3572 live). A first version of this tool applied that diff AS
    the full set, so 24 live sym_sides dropped from ~3572 keys to 41-856 and would have resolved ~3500 switches
    from cat_side/global defaults instead of their proven values. All 24 were restored from the pre-apply export
    within minutes. Both the MEASUREMENT and the WRITE now use this merged complete set, so the number measured is
    the number that goes live. Pure."""
    full = dict(incumbent or {})
    full.update(cand or {})
    return full


def measure(a):
    import per_sym_store as PS
    # THE INCUMBENT MUST BE THE SET THAT TRADES. Measured 2026-10-16 19:0xZ: a worker's per_sym_store.db is NOT
    # the Mac's — AAVEUSDC_LONG 3572 keys on the Mac vs 3531 on s1 (different md5), and DOGEUSDC_SHORT and
    # RLCUSDT_SHORT shared ONE md5 on s1 (a cat_side fallback, i.e. no per-sym set there at all). A first run
    # that took the worker's own db as "live" produced a +2670 pp "recovery" that was an artefact of comparing
    # against sets nothing trades. --live-sets ships the Mac's sets in, so only a real incumbent is ever beaten.
    live_in = {}
    if a.live_sets:
        live_in = json.load(open(a.live_sets))
        if not isinstance(live_in, dict) or not live_in:
            raise SystemExit(f"--live-sets {a.live_sets}: expected a non-empty {{sym_side: set}} map")
        print(f"[h2h] incumbent sets from {a.live_sets}: {len(live_in)} sym_sides (the Mac's live per_sym, not this host's db)", flush=True)
    elif not a.allow_local_live:
        raise SystemExit("refusing to run: --live-sets is required (a worker's per_sym_store.db is not what trades); "
                         "pass --allow-local-live only on the host that owns the live sets")
    sides = [s.strip() for s in (a.sym_sides or "").replace("\n", ",").split(",") if s.strip()]
    if a.sym_sides_file:
        sides += [l.strip() for l in open(a.sym_sides_file) if l.strip() and not l.startswith("#")]
    if getattr(a, "stale_days", 0):
        stale = _stale_sym_sides(a.stale_days)
        sides = [s for s in stale if (not live_in or s in live_in)] if not sides else [s for s in sides if s in set(stale)]
        print(f"[h2h] --stale-days {a.stale_days}: {len(sides)} sym_side(s) whose live set is older than that", flush=True)
    if not sides and live_in:
        sides = sorted(live_in)
    sides = sorted(dict.fromkeys(sides))
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename,
           "window_days": a.window_days, "tol": TOL,
           "incumbent_source": a.live_sets or f"local per_sym_store ({PS.DB_PATH})", "decisions": []}
    for i, ss in enumerate(sides, 1):
        d = {"sym_side": ss}
        live = live_in.get(ss) if live_in else (PS.get_full_config(ss) or {})
        live = live if isinstance(live, dict) else {}
        cand, meta = _candidate_catside(ss) if getattr(a, "candidate", "published") == "catside" else _candidate_set(ss)
        d["candidate_meta"] = meta if isinstance(meta, dict) else {"why": meta}
        if not live:
            d["verdict"], d["why"] = "NO_INCUMBENT", "no live per_sym set"
        if not isinstance(cand, dict):
            d["verdict"], d["why"] = "NO_CANDIDATE", str(meta)
            out["decisions"].append(d)
            print(f"[{i}/{len(sides)}] {ss}: NO_CANDIDATE ({meta})", flush=True)
            continue
        cand_full = _candidate_full(live, cand)
        d["set_keys"] = {"live": len(live), "publish_row": len(cand), "candidate_complete": len(cand_full)}
        if live and len(cand_full) < len(live):
            d["verdict"], d["why"] = "CANDIDATE_INCOMPLETE", f"merged candidate {len(cand_full)} keys < live {len(live)} — refusing to shrink a live set"
            out["decisions"].append(d)
            print(f"[{i}/{len(sides)}] {ss}: CANDIDATE_INCOMPLETE ({d['why']})", flush=True)
            continue
        lg, lt, ldd, ltim, lwr, lwhy = _gain_and_trades(ss, live, a.window_days) if live else (None, None, None, None, None, "no live set")
        cg, ct, cdd, ctim, cwr, cwhy = _gain_and_trades(ss, cand_full, a.window_days)
        d["live"] = {"gain_pct": lg, "trades": lt, "max_dd_pct": ldd, "tim_pct": ltim, "wr_pct": lwr, "why": lwhy}
        d["candidate"] = {"gain_pct": cg, "trades": ct, "max_dd_pct": cdd, "tim_pct": ctim, "wr_pct": cwr, "why": cwhy}
        d["claimed_gain"] = (meta or {}).get("claimed_gain")
        if cg is None:
            d["verdict"], d["why"] = "CANDIDATE_UNMEASURABLE", cwhy
        elif lg is None:
            d["verdict"], d["why"] = "APPLY_CANDIDATE", f"no measurable incumbent ({lwhy})"
            d["overrides"] = cand_full
        elif cg > lg + TOL:
            d["verdict"], d["why"] = "APPLY_CANDIDATE", f"candidate {cg} beats live {lg} by {cg - lg:.3f} > tol {TOL} on the same NPZ"
            d["overrides"] = cand_full
        else:
            d["verdict"], d["why"] = "KEEP_LIVE", f"candidate {cg} does not beat live {lg} by more than {TOL}"
        _cl = d.get("claimed_gain")
        if cg is not None and isinstance(_cl, (int, float)) and abs(float(_cl) - cg) > 1.0:
            d["claim_not_reproducible_pp"] = round(float(_cl) - cg, 3)
        print(f"[{i}/{len(sides)}] {ss}: {d['verdict']} live={lg} cand={cg} claimed={_cl} — {d['why']}", flush=True)
        out["decisions"].append(d)
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    v = {}
    for d in out["decisions"]:
        v[d.get("verdict", "?")] = v.get(d.get("verdict", "?"), 0) + 1
    print(f"[h2h] {len(sides)} sym_sides -> {v} -> {a.out}", flush=True)
    return 0


def export_live(a):
    """Dump the live per_sym sets of the host that TRADES, so a worker measures the real incumbent (see measure)."""
    import per_sym_store as PS
    sides = [s.strip() for s in (a.sym_sides or "").replace("\n", ",").split(",") if s.strip()]
    if a.sym_sides_file:
        sides += [l.strip() for l in open(a.sym_sides_file) if l.strip() and not l.startswith("#")]
    sides = sorted(dict.fromkeys(sides))
    out, thin = {}, []
    for ss in sides:
        c = PS.get_full_config(ss) or {}
        if len(c) < 20:
            thin.append(f"{ss}({len(c)})")
            continue
        out[ss] = c
    Path(a.out).write_text(json.dumps(out, default=str))
    print(f"[export-live] {len(out)} of {len(sides)} sym_sides from {PS.DB_PATH} -> {a.out}"
          + (f"; {len(thin)} with no usable set: {thin[:8]}" if thin else ""))
    return 0


def apply_(a):
    import per_sym_store as PS
    d = json.load(open(a.inp))
    todo = [x for x in d.get("decisions", []) if x.get("verdict") == "APPLY_CANDIDATE" and isinstance(x.get("overrides"), dict)]
    print(f"[h2h-apply] {len(todo)} of {len(d.get('decisions', []))} decision(s) would change the live set (tol {d.get('tol')})")
    for x in todo:
        print(f"   {x['sym_side']:22s} live {x['live'].get('gain_pct')} -> candidate {x['candidate'].get('gain_pct')}  ({x['why']})")
    if not a.apply:
        print("[h2h-apply] DRY RUN — pass --apply to write")
        return 0
    ok, fail = 0, []
    for x in todo:
        ss = x["sym_side"]
        # Same write contract as tools/v15_suspect_row_rerun.apply_one (USER 2026-10-15: per_sym is a COMPLETE
        # set, never a diff) — the candidate set measured above becomes both overrides and full_config, and
        # per_sym_active_config*.json is written by the store, never by hand.
        cand = dict(x["overrides"])
        # FAIL-CLOSED on set size. A per_sym set may gain keys, never lose them: 2026-10-16 18:24Z a publish-row
        # DIFF was written as the full set and 24 live sym_sides fell from ~3572 keys to 41-856. Checked again
        # here against the CURRENT live set, not only against the export the measurement used.
        now_live = PS.get_full_config(ss) or {}
        if now_live and len(cand) < len(now_live):
            fail.append(f"{ss}: candidate has {len(cand)} keys, live has {len(now_live)} — refusing to shrink the live set")
            continue
        missing = [k for k in now_live if k not in cand]
        if missing:
            fail.append(f"{ss}: candidate omits {len(missing)} live key(s) (e.g. {missing[:3]}) — a per_sym set is complete or it is not written")
            continue
        e = PS.get(ss) or {}
        snap = dict(e.get("defaults_snapshot") or {})
        venue = "crypto" if ss.rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1")) else "stocks"
        jp = ROOT / "data" / "hourly_reconfig" / ("per_sym_active_config.json" if venue == "crypto" else "trb/active_config.json")
        meta = {"source": "v15_golive_headtohead", "why": x["why"], "measured_at": d.get("ts"), "host": d.get("host"),
                "window_days": d.get("window_days"), "tol": d.get("tol"),
                "gain_pct": (x.get("candidate") or {}).get("gain_pct"), "trades": (x.get("candidate") or {}).get("trades"),
                "max_dd_pct": (x.get("candidate") or {}).get("max_dd_pct"), "tim_pct": (x.get("candidate") or {}).get("tim_pct"),
                "replaced_live_gain_pct": (x.get("live") or {}).get("gain_pct"),
                "claimed_gain_pct": x.get("claimed_gain"), "claim_not_reproducible_pp": x.get("claim_not_reproducible_pp"),
                "evidence": str(a.inp)}
        try:
            PS.upsert(ss, cand, snap, cand, meta, json_path=jp if jp.exists() else None)
            back = PS.get_full_config(ss) or {}
            miss = [k for k, v in cand.items() if back.get(k) != v]
            if miss:
                fail.append(f"{ss}: {len(miss)} key(s) did not read back (e.g. {miss[:3]})")
                continue
            ok += 1
        except Exception as e:
            fail.append(f"{ss}: {type(e).__name__}: {str(e)[:70]}")
    print(f"[h2h-apply] applied {ok}, failed {len(fail)}")
    for f in fail:
        print(f"   FAIL {f}")
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--sym-sides", default="")
    m.add_argument("--sym-sides-file", default="")
    m.add_argument("--live-sets", default="", help='JSON {"SYM_SIDE": {full set}} exported from the host that TRADES')
    m.add_argument("--allow-local-live", action="store_true", help="use this host's own per_sym_store as the incumbent (only where live runs)")
    m.add_argument("--window-days", type=int, default=30)
    m.add_argument("--candidate", choices=("published", "catside"), default="published", help="what to pit against the live set: the latest published board (default) or the CURRENT cat_side defaults (USER 2026-10-17)")
    m.add_argument("--stale-days", type=float, default=0.0, help="only sym_sides whose live set was last written more than N days ago (0 = no age filter)")
    m.add_argument("--out", default="/tmp/v15_h2h.json")
    e = sub.add_parser("export-live", help="dump THIS host's live per_sym sets for a worker to measure against")
    e.add_argument("--sym-sides", default="")
    e.add_argument("--sym-sides-file", default="")
    e.add_argument("--out", required=True)
    p = sub.add_parser("apply")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.mode == "export-live":
        return export_live(a)
    return measure(a) if a.mode == "measure" else apply_(a)


if __name__ == "__main__":
    sys.exit(main())
