#!/usr/bin/env python3
"""v15_persym_golive — daily chain STEP 3 (BIBLE §68.3): per-sym final sets -> ALL live surfaces on the Mac (director 2026-10-06).

Two modes:
  collect (runs ON a server that holds the NPZ; called over ssh by the Mac mode):
      python tools/v15_persym_golive.py collect --files /tmp/list.txt --out /tmp/out.json [--workers 4]
    per progress file: cumulative_overrides with the 2026-10-06 *_VEC_ONLY_* renames applied (removed keys dropped), n_promoted,
    and a FRESH full-set 30D evaluation of that exact set (tools.opt.v12_pilot.prepare_batch + evaluate_prepared_sanitized — the same
    evaluator v15_pilot._parity_register_at_done feeds the registrar). Pre-screen (no eval): no set, verdict IMPOSSIBLE/NO_TRADES,
    diagnostic_only, n_promoted < 1, stored final gain <= 0.
  golive (default, on the Mac):
      python tools/v15_persym_golive.py [--selection data/avg_delta_selection.json] [--apply]
    selection = the rebuild's ONE-latest-file-per-sym_side map (host + path). Runs collect on every host, then per sym_side applies the
    gates: switch_parity.register_workbook_result (dry_run) = n_promoted >= 1, valid, TIM 20-80, >= 10 trades, DD <= 30 (via valid),
    gain > 0, no secrets, every key type-coerces against the Mac cat_side snapshot, _NEG_BLOCK preserved; plus 365D: a sym_side with a
    365D verdict must pass it (data/confirmed_365d.json entry, else progress final_365d: valid + gain > 0); plus no
    SIMPLE_PRICE_GT0_ENABLED=True. Report data/daily_chain/persym_golive_<date>.json: per sym_side gate result, renamed/dropped keys,
    and what would change vs the current Mac book (old vs new keys/values). Registration is diff_only (director 2026-10-06): only keys
    whose value differs from the effective default (cat_side_defaults_4 > venue global) are pinned per-sym.
    --apply (ONLY after director/user confirmation) calls switch_parity.register_workbook_result(dry_run=False) for each passing
    sym_side: SQLite per_sym_store (primary) + per-sym JSON book (+ trb overlay for stocks) + data/parity_promotions.jsonl, re-read verify.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RENAMES = {"CRYPTO_VEC_ONLY_REENTRY_ENABLED": "CRYPTO_REENTRY_PATHWAYS_ENABLED", "HAIKU_WINNER_VEC_ONLY_ENABLED": "HAIKU_WINNER_AUGMENT_ENABLED", "KEY_LEVEL_CRASH_VEC_ONLY_ENABLED": "KEY_LEVEL_CRASH_EXIT_ENABLED", "KG_STOCKS_LIVE_GATE_VEC_ONLY_ENABLED": "KG_STOCKS_HARD_VETO_ENABLED"}
REMOVED = {"EMA50_15M_ENTRY_FILTER_VEC_ONLY_ENABLED"}
EV_KEYS = ("valid", "invalid_reason", "gain_pct", "trades", "tim_pct", "max_dd_pct", "pool_sharpe")


def migrate(overrides):
    out, renamed, dropped = {}, {}, []
    for k, v in (overrides or {}).items():
        if k in REMOVED or ("VEC_ONLY" in k and k not in RENAMES):
            dropped.append(k)
            continue
        if k in RENAMES:
            renamed[k] = RENAMES[k]
            k = RENAMES[k]
        out[k] = v
    return out, renamed, dropped


ALL_FINISHED = os.environ.get("V15_COLLECT_ALL_FINISHED") == "1"  # USER 2026-10-06: every FINISHED set, any gain (pre-screen = unfinished / no set only)
EVAL_ALL = os.environ.get("V15_COLLECT_EVAL_ALL") == "1"  # tradeable check: no pre-screen; evaluate the latest set (cumulative > initial > defaults)


def _collect_one(path):
    try:
        d = json.loads(Path(path).read_text())
    except Exception as e:
        return None, {"path": path, "skip": f"unreadable: {e}"}
    ss = str(d.get("symside") or Path(path).name.replace("_v14_progress.json", "")).upper()
    rec = {"path": path, "mtime": os.path.getmtime(path), "verdict": d.get("verdict"), "defaults_round": d.get("defaults_round"), "npz_id": d.get("npz_id"), "bh": d.get("bh"), "baseline_gain": d.get("baseline_gain"), "final_365d": d.get("final_365d"), "stored_final_gain": d.get("final_gain_fresh_vec", d.get("cumulative_gain"))}
    co = d.get("cumulative_overrides") or {}
    rec["n_promoted"] = sum(1 for v in (d.get("done") or {}).values() if isinstance(v, dict) and v.get("promoted"))
    if EVAL_ALL:
        rec["set_source"] = "cumulative_overrides" if co else ("initial_overrides" if d.get("initial_overrides") else "defaults")
        co = co or d.get("initial_overrides") or {}
        rec["final_trades_stored"] = d.get("final_trades")
    if not co and not EVAL_ALL:
        rec["skip"] = "no cumulative_overrides"
        return ss, rec
    ov, rec["renamed"], rec["dropped"] = migrate(co)
    rec["overrides"] = ov
    sg = rec["stored_final_gain"]
    finished = bool(d.get("verdict") or d.get("result_done_utc") or d.get("final_gain_fresh_vec") is not None)
    rec["finished"] = finished
    if EVAL_ALL:
        pass
    elif ALL_FINISHED:
        if not finished:
            rec["skip"] = "not finished (no verdict / result_done_utc / final_gain_fresh_vec)"
    elif d.get("verdict") in ("IMPOSSIBLE", "NO_TRADES"):
        rec["skip"] = f"verdict {d.get('verdict')}"
    elif d.get("diagnostic_only"):
        rec["skip"] = f"diagnostic_only {d.get('diagnostic_only')}"
    elif rec["n_promoted"] < 1:
        rec["skip"] = "n_promoted=0"
    elif sg is None or float(sg) <= 0:
        rec["skip"] = f"stored final gain {sg}"
    if rec.get("skip"):
        return ss, rec
    try:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
        prep = prepare_batch(ss, 30)
        ev = evaluate_prepared_sanitized(prep, dict(ov), 30)
        rec["evidence"] = {k: ev.get(k) for k in EV_KEYS}
        rec["evidence"].update(n_promoted=rec["n_promoted"], bh_pct=d.get("bh"), baseline_gain=d.get("baseline_gain"), npz_id=d.get("npz_id") or "", defaults_round=d.get("defaults_round") or "")
        try:
            rec["evidence"]["engine_md5"] = hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()[:8]
        except Exception:
            rec["evidence"]["engine_md5"] = ""
    except Exception as e:
        rec["skip"] = f"fresh eval failed: {e}"
    return ss, rec


def collect(files, out, workers):
    import multiprocessing as mp
    paths = [l.strip() for l in Path(files).read_text().splitlines() if l.strip()]
    res = {}
    with mp.Pool(max(1, workers)) as pool:
        for ss, rec in pool.imap_unordered(_collect_one, paths):
            if ss:
                rec["host"] = os.uname().nodename
                res[ss] = rec
    tmp = Path(out).with_suffix(".tmp")
    tmp.write_text(json.dumps(res, default=str))
    tmp.replace(out)
    print(f"[collect] {len(res)} sym_sides, evaluated {sum(1 for r in res.values() if 'evidence' in r)} -> {out}")


def _ssh(targets, cmd, timeout):
    last = ""
    for t in targets:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", t, cmd], capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return t, r.stdout
        last = (r.stderr or r.stdout)[-300:]
    raise RuntimeError(f"ssh {targets} failed: {last}")


def _scp(src, dst):
    r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", src, dst], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"scp {src} {dst}: {r.stderr[-200:]}")


def gate_365d(ss, rec, conf):
    c = conf.get(ss)
    if c is not None:
        ok = bool(c.get("valid")) and float(c.get("gain_365d") or 0) > 0 and int(c.get("trades") or 0) >= 30
        return ok, f"confirmed_365d valid={c.get('valid')} gain={c.get('gain_365d')} trades={c.get('trades')} at={c.get('confirmed_at')}"
    f = rec.get("final_365d")
    if isinstance(f, dict):
        ok = bool(f.get("valid")) and float(f.get("gain_pct") or 0) > 0
        return ok, f"progress final_365d valid={f.get('valid')} gain={f.get('gain_pct')} trades={f.get('trades')} reason={f.get('invalid_reason')}"
    return True, "no 365D verdict (not required)"


def book_diff(ss, new):
    import switch_parity as sp
    try:
        raw = json.loads(sp._book_path(ss).read_text())
        old_e = raw.get(ss) or {}
    except Exception:
        old_e = {}
    old = old_e.get("overrides") or {}
    ch = {k: [old.get(k), v] for k, v in new.items() if k in old and not sp._same_val(old.get(k), v)}
    add = [k for k in new if k not in old]
    rem = [k for k in old if k not in new]
    return {"old_tag": old_e.get("winning_tag"), "old_n": len(old), "new_n": len(new), "changed_n": len(ch), "added_n": len(add), "removed_n": len(rem), "changed_sample": dict(list(ch.items())[:15]), "added_sample": add[:15], "removed_sample": rem[:15]}


def golive(a):
    import switch_parity as sp
    sel = json.loads(Path(a.selection).read_text())
    hosts = {h["name"]: h for h in json.loads(Path(a.hosts).read_text())["hosts"]}
    per_host = {}
    for ss, r in sel.items():
        per_host.setdefault(r["host"], []).append(r["path"])
    tmpd = Path(tempfile.mkdtemp(prefix="persym_golive_"))
    collected, host_err = {}, {}

    def _run_host(item):
        hn, files = item
        h = hosts.get(hn)
        if not h:
            return hn, None, "host not in hosts file"
        try:
            lf = tmpd / f"{hn}.txt"
            lf.write_text("\n".join(files) + "\n")
            via, _ = _ssh(h["ssh"], "true", 30)
            _scp(str(ROOT / "tools" / "v15_persym_golive.py"), f"{via}:binance-sandbox/tools/v15_persym_golive.py")
            _scp(str(lf), f"{via}:/tmp/v15_persym_golive_files.txt")
            _, out = _ssh([via], f"cd ~/binance-sandbox && {'V15_COLLECT_ALL_FINISHED=1 ' if a.all_finished else ''}timeout {a.collect_timeout} .venv/bin/python -u tools/v15_persym_golive.py collect --files /tmp/v15_persym_golive_files.txt --out /tmp/v15_persym_golive_out.json --workers {a.workers} 2>&1 | grep -v Warning | tail -3", a.collect_timeout + 60)
            print(f"[golive] {hn} via {via}: {out.strip()}", flush=True)
            dst = tmpd / f"{hn}.json"
            _scp(f"{via}:/tmp/v15_persym_golive_out.json", str(dst))
            return hn, json.loads(dst.read_text()), None
        except Exception as e:
            print(f"[golive] host {hn} FAILED: {e}", flush=True)
            return hn, None, str(e)[:300]

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max(1, len(per_host))) as ex:
        for hn, got, err in ex.map(_run_host, sorted(per_host.items())):
            if err:
                host_err[hn] = err
            else:
                collected.update(got)
    conf = {}
    try:
        conf = json.loads((ROOT / "data" / "confirmed_365d.json").read_text())
    except Exception as e:
        print(f"[golive] confirmed_365d.json unreadable: {e}")
    rows, n_pass, n_reg = {}, 0, 0
    for ss in sorted(collected):
        rec = collected[ss]
        row = {"host": rec.get("host"), "path": rec.get("path"), "verdict": rec.get("verdict"), "renamed": rec.get("renamed"), "dropped": rec.get("dropped"), "n_promoted": rec.get("n_promoted"), "final_365d": rec.get("final_365d")}
        if rec.get("skip"):
            row.update(result="SKIP", reason=rec["skip"])
            rows[ss] = row
            continue
        ev, ov = rec["evidence"], rec["overrides"]
        row["evidence"] = ev
        row["registered"] = False
        if ov.get("SIMPLE_PRICE_GT0_ENABLED") is True or str(ov.get("SIMPLE_PRICE_GT0_ENABLED")).lower() == "true":
            row.update(result="REFUSED", reason="SIMPLE_PRICE_GT0_ENABLED=True set")
            rows[ss] = row
            continue
        ir = str(ev.get("invalid_reason") or "")
        if a.all_finished and (ir.startswith(("override", "prepare failed")) or "incompatible" in ir):
            row.update(result="REFUSED", reason=f"engine rejects the set: {ir[:200]}")
            rows[ss] = row
            continue
        if a.all_finished and int(ev.get("trades") or 0) == 0:
            row.update(result="REFUSED", reason="zero trades on the fresh 30D eval (registering would silence a tradeable key)")
            rows[ss] = row
            continue
        ok365, why365 = gate_365d(ss, rec, conf)
        row["gate_365d"] = why365
        if not ok365 and not a.all_finished:
            row.update(result="REFUSED", reason=f"365D verdict fails: {why365}")
            rows[ss] = row
            continue
        dr = sp.register_workbook_result(ss, dict(ov), dict(ev), dry_run=True, diff_only=True, require_qualified=not a.all_finished)
        if "gates passed" not in str(dr.get("reason")):
            row.update(result="NOTHING_TO_REGISTER" if "nothing to register" in str(dr.get("reason")) else "REFUSED", reason=str(dr.get("reason"))[:400], diff_only=dr.get("diff_only"))
            rows[ss] = row
            continue
        n_pass += 1
        row.update(qualified_30d=dr.get("qualified_30d"), unqualified_reasons=dr.get("unqualified_reasons"), negbook_blocked_live=(float(ev.get("gain_pct") or 0) <= 0))
        row.update(result="PASS", would_tag=dr.get("would_tag"), neg_block_preserved=dr.get("neg_block_preserved"), n_keys=dr.get("n_keys"), diff_only=dr.get("diff_only"), would_register=dr.get("would_register"), change=book_diff(ss, dr.get("would_register") or {}))
        if a.apply:
            rr = sp.register_workbook_result(ss, dict(ov), dict(ev), dry_run=False, diff_only=True, require_qualified=not a.all_finished)
            row["applied"] = {k: rr.get(k) for k in ("registered", "reason", "tag", "sqlite", "book", "trb_overlay", "ledger", "backup")}
            row["applied"]["verify_ok"] = (rr.get("verify") or {}).get("ok")
            n_reg += 1 if rr.get("registered") else 0
        row["registered"] = bool((row.get("applied") or {}).get("registered"))
        rows[ss] = row
    from collections import Counter
    summary = {"selected": len(sel), "collected": len(collected), "evaluated": sum(1 for r in collected.values() if "evidence" in r), "results": dict(Counter(r["result"] for r in rows.values())), "pass": n_pass, "registered": n_reg, "host_errors": host_err,
               "refuse_reasons_top": Counter(r["reason"].split(":")[0][:60] for r in rows.values() if r["result"] != "PASS").most_common(12), "renamed_sets": sum(1 for r in rows.values() if r.get("renamed")), "dropped_sets": sum(1 for r in rows.values() if r.get("dropped")),
               "mode_all_finished": bool(a.all_finished), "pass_qualified": sum(1 for r in rows.values() if r["result"] == "PASS" and r.get("qualified_30d")),
               "pass_unqualified": sum(1 for r in rows.values() if r["result"] == "PASS" and not r.get("qualified_30d")),
               "pass_negative_gain_negbook_blocked_live": sum(1 for r in rows.values() if r["result"] == "PASS" and r.get("negbook_blocked_live"))}
    rep = {"date": a.date, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "mode": "APPLY" if a.apply else "DRY_RUN", "selection": a.selection, "renames": RENAMES, "removed": sorted(REMOVED), "summary": summary, "sym_sides": rows}
    out = Path(a.out or ROOT / "data" / "daily_chain" / f"persym_golive_{a.date}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))
    for ss, r in rows.items():
        if r["result"] == "PASS":
            c = r["change"]
            print(f"  PASS {ss} gain={r['evidence'].get('gain_pct'):.2f} trades={r['evidence'].get('trades')} tim={r['evidence'].get('tim_pct')} dd={r['evidence'].get('max_dd_pct')} | book {c['old_tag']} old_n={c['old_n']} -> new_n={c['new_n']} changed={c['changed_n']} added={c['added_n']} removed={c['removed_n']} renamed={r.get('renamed')}")
    print(f"[golive] report {out} mode={rep['mode']}")
    return 1 if host_err else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="golive", choices=("golive", "collect"))
    ap.add_argument("--files")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--eval-all", action="store_true", help="collect: no pre-screen (tradeable check); same as V15_COLLECT_EVAL_ALL=1")
    ap.add_argument("--selection", default=str(ROOT / "data" / "avg_delta_selection.json"))
    ap.add_argument("--hosts", default=str(ROOT / "tools" / "fleet_hosts.json"))
    ap.add_argument("--collect-timeout", type=int, default=900)
    ap.add_argument("--date", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--apply", action="store_true", help="LIVE WRITE: only after director/user confirmation")
    ap.add_argument("--all-finished", action="store_true", help="USER 2026-10-06: register EVERY finished set (any gain), diff-only; still refuses SIMPLE_PRICE_GT0, engine-rejected and zero-trade sets")
    a = ap.parse_args()
    if a.mode == "collect":
        if a.eval_all:
            os.environ["V15_COLLECT_EVAL_ALL"] = "1"
            global EVAL_ALL
            EVAL_ALL = True
        collect(a.files, a.out, a.workers)
        return 0
    return golive(a)


if __name__ == "__main__":
    sys.exit(main())
