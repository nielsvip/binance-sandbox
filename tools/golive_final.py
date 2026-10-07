#!/usr/bin/env python3
"""golive_final — Monday go-live of the qualified sym_sides (USER 2026-10-02). Runs ON THE MAC (source of truth for the live books). Dry-run by default.

USER RULE: only sym_sides whose best set is POSITIVE on 365D AND 30D (BIBLE §58 repair loop, §51 gates) go live; negative / unverified sym_sides do not trade AT ALL (not on old
settings either); everything that qualifies is live before the 13:30 UTC open. Input = data/autopilot/final/qualifiers.json produced on s1 by tools/v15_final_orch.py
(every record carries its evidence). Writes ONLY hot-reloaded data files (no code, no config, no restart, nothing through execute_now):
  * data/hourly_reconfig/per_sym_active_config.json (crypto book) / per_sym_active_config_stocks.json (stocks book): qualified -> entry replaced with the verified set
    (same schema as tools/promote_365cycle_winners_20260929.py); not qualified -> winning_tag += '_NEG_BLOCK' (read by ez_negbook_is_blocked / tradier_manage.negbook_is_blocked:
    no opens; an open position exits at the next WT turn against it - the existing 2026-09-28 mandate)
  * data/hourly_reconfig/trb/active_config.json (stocks live overlay, read FIRST by tradier_manage._cfg): qualified entries get the verified overrides (else the stale overlay would win)
  * data/confirmed_365d.json (crypto 365D gate, fail-closed): qualified -> certification record from tools/confirm_365d.confirm_symside (staged on s1); not qualified -> record removed
  * data/full_recipe_live_config.json: entries of qualified sym_sides are removed (the exact-recipe overlay would override the new set)
Fail-closed gates (nothing is written when any fails): finalized flag, file age <= 5 h, engine md5 == data/engine_deploy/CURRENT.json, >= 1 qualified, local re-validation of every qualified record.
Blocks are written only if the chain really ran (qualified + negative >= 40 % of the expected sym_sides); sym_sides that were never evaluated are left untouched.
usage: golive_final.py [--pull] [--apply] [--qualifiers PATH] [--force-time]
"""
import argparse, datetime as dt, json, os, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(os.environ.get("GOLIVE_ROOT") or Path(__file__).resolve().parents[1])  # GOLIVE_ROOT = rehearsal sandbox (never set in production)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v15_final_phase as FP  # noqa: E402

HR = ROOT / "data" / "hourly_reconfig"
BOOKS = {"crypto": HR / "per_sym_active_config.json", "stocks": HR / "per_sym_active_config_stocks.json"}
TRB = HR / "trb" / "active_config.json"
CONFIRM = ROOT / "data" / "confirmed_365d.json"
OVERLAY = ROOT / "data" / "full_recipe_live_config.json"
OUT = ROOT / "data" / "autopilot_final"
TAG = "v15_FINAL_20261005_BOTH_POS"
NOW = dt.datetime.now(dt.timezone.utc)
STAMP = NOW.strftime("%Y%m%d%H%M")


def jl(p, d=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return d


def atomic(p, obj):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + ".gl.tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    json.loads(tmp.read_text())
    os.replace(tmp, p)


def backup(p):
    b = ROOT / "backups" / f"before_golive_final_{STAMP}_{Path(p).name}"
    shutil.copy2(p, b)
    return str(b)


def venue(ss):
    return "crypto" if FP.is_crypto(ss) else "stocks"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--qualifiers")
    ap.add_argument("--force-time", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    qp = Path(a.qualifiers) if a.qualifiers else OUT / "qualifiers_latest.json"
    if a.pull:
        for src in ("s1-pub", "s2"):
            host_cmd = ["rsync", "-az", "-e", "ssh -o ConnectTimeout=15 -o BatchMode=yes", f"{src}:~/binance-sandbox/data/autopilot/final/qualifiers.json", str(qp)]
            if src == "s2":
                host_cmd = ["bash", "-c", f"ssh -o ConnectTimeout=15 s2 'ssh -o ConnectTimeout=15 10.0.0.3 cat ~/binance-sandbox/data/autopilot/final/qualifiers.json' > {qp}.tmp && mv {qp}.tmp {qp}"]
            if subprocess.run(host_cmd, capture_output=True, text=True, timeout=180).returncode == 0:
                break
    q = jl(qp)
    rep = {"at": NOW.isoformat(), "apply": a.apply, "gates": {}, "written": {}, "backups": []}

    def abort(msg):
        rep["ABORT"] = msg
        (OUT / f"golive_report_{STAMP}.json").write_text(json.dumps(rep, indent=1, default=str))
        print("ABORT (nothing written):", msg)
        sys.exit(2)

    if not q:
        abort(f"no qualifiers file at {qp}")
    shutil.copy2(qp, OUT / f"qualifiers_{STAMP}.json")
    if a.apply and not a.force_time:
        lo, hi = dt.datetime(2026, 10, 5, 11, 30, tzinfo=dt.timezone.utc), dt.datetime(2026, 10, 5, 13, 29, tzinfo=dt.timezone.utc)
        if not (lo <= NOW <= hi):
            abort(f"--apply outside the go-live window {lo.time()}..{hi.time()} UTC on 2026-10-05 (use --force-time)")
    if not q.get("finalized"):
        abort("qualifiers.json is not finalized")
    age_h = (NOW - dt.datetime.fromisoformat(q["generated_at"])).total_seconds() / 3600
    rep["gates"]["age_h"] = round(age_h, 2)
    if age_h > 5:
        abort(f"qualifiers.json is {age_h:.1f} h old")
    mac_eng = (jl(ROOT / "data" / "engine_deploy" / "CURRENT.json", {}).get("engine_md5") or "")[:8]
    if q.get("engine_md5") != mac_eng:
        abort(f"engine md5 mismatch: qualifiers {q.get('engine_md5')} vs Mac CURRENT {mac_eng}")
    ev = q["evaluated"]
    qual = {s: r for s, r in ev.items() if r["status"] == "QUALIFIED"}
    neg = {s: r for s, r in ev.items() if r["status"] in ("NEGATIVE", "UNVERIFIED")}
    rep["gates"].update(expected=q.get("expected"), qualified=len(qual), blocked_candidates=len(neg), counts=q.get("counts"))
    if not qual:
        abort("0 qualified sym_sides")
    # local re-validation of every qualified record (never trust the producer blindly)
    bad = []
    for s, r in qual.items():
        ov = r.get("overrides")
        if not isinstance(ov, dict) or not ov or not FP.good_window(r.get("w30"), FP.FLOOR30) or not FP.good_window(r.get("w365"), FP.FLOOR365) or float(r.get("span_365_days") or 0) < 330:
            bad.append(s); continue
        if venue(s) == "crypto":
            c = r.get("confirm") or {}
            if not (c.get("valid") and float(c.get("gain_365d") or 0) > 0 and int(c.get("trades") or 0) >= 30):
                bad.append(s)
        if any(k.upper().find(x) >= 0 for k in ov for x in ("API_KEY", "SECRET", "TOKEN", "PASSWORD")):
            bad.append(s)
    for s in bad:
        qual.pop(s, None)
        neg[s] = {"status": "UNVERIFIED", "reasons": ["failed local re-validation"]}
    rep["gates"]["failed_local_validation"] = sorted(set(bad))
    if not qual:
        abort("no qualified record survived local re-validation")
    coverage = (len(qual) + sum(1 for r in neg.values() if r["status"] == "NEGATIVE")) / max(1, int(q.get("expected") or 1))
    do_blocks = coverage >= 0.40
    rep["gates"].update(chain_coverage=round(coverage, 3), blocks_enabled=do_blocks)
    plan = {"promote": sorted(qual), "block": sorted(neg) if do_blocks else []}
    print(json.dumps({"gates": rep["gates"], "promote": len(plan["promote"]), "block": len(plan["block"])}))
    if not a.apply:
        rep["plan"] = plan
        (OUT / f"golive_report_{STAMP}.json").write_text(json.dumps(rep, indent=1, default=str))
        print("DRY-RUN (no --apply): report", OUT / f"golive_report_{STAMP}.json")
        return
    ts = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    # ---- books
    for v, path in BOOKS.items():
        book = jl(path, None)
        if not isinstance(book, dict):
            abort(f"{path.name} unreadable")
        rep["backups"].append(backup(path))
        n_p = n_b = 0
        for s in plan["promote"]:
            if venue(s) != v:
                continue
            r = qual[s]
            old = book.get(s) if isinstance(book.get(s), dict) else {}
            w30, w365 = r["w30"], r["w365"]
            book[s] = {"overrides": dict(r["overrides"]), "winning_tag": TAG, "trades": int(w30.get("trades") or 0), "acc_gain_pct": round(float(w30["gain_pct"]), 4),
                       "gain_365_pct": round(float(w365["gain_pct"]), 4), "tim_365_pct": w365.get("tim_pct"), "dd_365_pct": w365.get("max_dd_pct"), "trades_365": int(w365.get("trades") or 0),
                       "parity_30d": (r.get("parity") or {}).get("status"), "prev_winning_tag": old.get("winning_tag"), "prev_acc_gain_pct": old.get("acc_gain_pct"),
                       "engine": f"v12_quick {q.get('engine_md5')}", "promoted_at": ts}
            n_p += 1
        for s in plan["block"]:
            if venue(s) != v:
                continue
            old = book.get(s) if isinstance(book.get(s), dict) else {"overrides": {}, "trades": 0}
            t = str(old.get("winning_tag") or "")
            if "_NEG_BLOCK" not in t:
                old["winning_tag"] = (t + "_NEG_BLOCK") if t else "v15_FINAL_20261005_NEG_BLOCK"
            old["neg_block_reason"] = "; ".join(neg[s].get("reasons", []))[:300]
            old["neg_blocked_at"] = ts
            book[s] = old
            n_b += 1
        atomic(path, book)
        rep["written"][path.name] = {"promoted": n_p, "blocked": n_b}
    # ---- stocks live overlay (trb): replace the overrides of qualified sym_sides that exist there
    if TRB.exists():
        trb = jl(TRB, None)
        if isinstance(trb, dict):
            rep["backups"].append(backup(TRB))
            n = 0
            for s in plan["promote"]:
                if venue(s) == "stocks" and isinstance(trb.get(s), dict):
                    r = qual[s]
                    e = trb[s]
                    e["prev_overrides_FINAL_20261005"] = e.get("overrides")
                    e.update(overrides=dict(r["overrides"]), gain_pct=round(float(r["w30"]["gain_pct"]), 4), trades=int(r["w30"].get("trades") or 0),
                             source="v15_FINAL_20261005 365D+30D both positive", promoted_at=ts, live=True)
                    n += 1
            atomic(TRB, trb)
            rep["written"][TRB.name] = {"replaced": n}
    # ---- full-recipe overlay: drop exact recipes the new set replaces
    ov = jl(OVERLAY, None)
    if isinstance(ov, dict) and isinstance(ov.get("entries"), dict):
        drop = [s for s in plan["promote"] if s in ov["entries"]]
        if drop:
            rep["backups"].append(backup(OVERLAY))
            for s in drop:
                ov["entries"].pop(s)
            atomic(OVERLAY, ov)
        rep["written"][OVERLAY.name] = {"removed": drop}
    # ---- crypto 365D gate: certified records for qualifiers, removal for the rest (fail-closed)
    conf = jl(CONFIRM, {}) if CONFIRM.exists() else {}
    if CONFIRM.exists():
        rep["backups"].append(backup(CONFIRM))
    n_c = n_r = 0
    for s in plan["promote"]:
        if venue(s) == "crypto":
            conf[s] = qual[s]["confirm"]; n_c += 1
    for s in plan["block"]:
        if venue(s) == "crypto" and s in conf:
            conf.pop(s); n_r += 1
    atomic(CONFIRM, conf)
    rep["written"][CONFIRM.name] = {"certified": n_c, "removed": n_r}
    # ---- verify by re-reading exactly what the live loaders read
    chk = {}
    for v, path in BOOKS.items():
        b = jl(path, {})
        blocked = {k for k, e in b.items() if isinstance(e, dict) and (("_NEG_BLOCK" in str(e.get("winning_tag", ""))) or (e.get("acc_gain_pct") is not None and float(e["acc_gain_pct"]) <= 0))}
        chk[v] = {"entries": len(b), "blocked_total": len(blocked), "promoted_unblocked": sum(1 for s in plan["promote"] if venue(s) == v and s not in blocked and b.get(s, {}).get("winning_tag") == TAG),
                  "promoted_expected": sum(1 for s in plan["promote"] if venue(s) == v)}
    rep["verify"] = chk
    (OUT / f"golive_report_{STAMP}.json").write_text(json.dumps(rep, indent=1, default=str))
    lines = [f"## {ts} GO-LIVE FINAL applied by tools/golive_final.py: promoted {len(plan['promote'])}, blocked {len(plan['block'])}; verify {chk}; backups {rep['backups']}"]
    for f in (ROOT / "data" / "wiring" / "LOG.md", ROOT / "DAILY_AVG_DELTA_CHECKLIST.md"):
        with open(f, "a") as fh:
            fh.write("- " + lines[0] + "\n")
    print(json.dumps({"applied": rep["written"], "verify": chk}))
    for v, c in chk.items():
        if c["promoted_unblocked"] != c["promoted_expected"]:
            print("WARNING: promoted entries not all unblocked in", v, c)
            sys.exit(3)


if __name__ == "__main__":
    main()
