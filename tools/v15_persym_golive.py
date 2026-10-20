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
    gain > 0, no secrets, every key type-coerces against the Mac cat_side snapshot (no _NEG_BLOCK, USER 2026-10-13); plus 365D: a sym_side with a
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
RENAMES = {
    "CRYPTO_VEC_ONLY_REENTRY_ENABLED": "CRYPTO_REENTRY_PATHWAYS_ENABLED",
    "HAIKU_WINNER_VEC_ONLY_ENABLED": "HAIKU_WINNER_AUGMENT_ENABLED",
    "KEY_LEVEL_CRASH_VEC_ONLY_ENABLED": "KEY_LEVEL_CRASH_EXIT_ENABLED",
    "KG_STOCKS_LIVE_GATE_VEC_ONLY_ENABLED": "KG_STOCKS_HARD_VETO_ENABLED",
}
REMOVED = {"EMA50_15M_ENTRY_FILTER_VEC_ONLY_ENABLED"}
EV_KEYS = (
    "valid",
    "invalid_reason",
    "gain_pct",
    "trades",
    "tim_pct",
    "max_dd_pct",
    "pool_sharpe",
)


# MAX_AUGMENTS_PER_POSITION joined the artefact list 2026-10-15 (USER "delete the switch completely"): it is gone
# from config/config_tradier/QuickConfig and from the cat_side layer, but boards published BEFORE the deletion
# still carry it, so every go-live would write a dead key back into the live set (ACTUSDT_LONG 04:57:19Z did).
ARTEFACT_KEYS = {"MODE", "SIMPLE_PRICE_GT0_ENABLED", "VENUE", "IS_TRADIER", "MAX_AUGMENTS_PER_POSITION",
                 "REENTRY_TIER2_MAX_MINUTES_TRADIER", "REENTRY_TIER2_MAX_MINUTES"}


def _ts_epoch(v):
    """Epoch seconds from an ISO-8601 'Z' stamp or a number; None when unparseable."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _measured_decision_newer(sym_side, published_at):
    """Reason to skip when per_sym_active already holds a MEASURED h2h decision newer than this publish, else ''.

    v15_golive_headtohead re-measures incumbent vs candidate on ONE NPZ and is the only writer whose number was
    observed rather than claimed. Fail-safe in BOTH directions: an unreadable store, a non-h2h row or an h2h row
    older than the publish all return '' so the normal refresh proceeds untouched. When the publish stamp is
    missing we keep the measured row, because an unknown age cannot be shown to be newer than a measurement.
    """
    try:
        import per_sym_store as PS

        e = PS.get(sym_side) or {}
    except Exception:
        return ""
    if not str(e.get("source") or "").startswith("v15_golive_headtohead"):
        return ""
    m_at = _ts_epoch(e.get("measured_at") or e.get("updated_at"))
    if m_at is None:
        return ""
    p_at = _ts_epoch(published_at)
    if p_at is not None and p_at >= m_at:
        return ""
    return (f"live row is a MEASURED h2h decision ({e.get('measured_at') or e.get('updated_at')}, "
            f"gain_30d={e.get('gain_30d')}) newer than this board's publish ({published_at or 'unknown'}) — "
            f"measured beats claimed; h2h re-checks it hourly")


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


ALL_FINISHED = (
    os.environ.get("V15_COLLECT_ALL_FINISHED") == "1"
)  # USER 2026-10-06: every FINISHED set, any gain (pre-screen = unfinished / no set only)
EVAL_ALL = (
    os.environ.get("V15_COLLECT_EVAL_ALL") == "1"
)  # tradeable check: no pre-screen; evaluate the latest set (cumulative > initial > defaults)


def _collect_one(path):
    try:
        d = json.loads(Path(path).read_text())
    except Exception as e:
        return None, {"path": path, "skip": f"unreadable: {e}"}
    ss = str(
        d.get("symside") or Path(path).name.replace("_v14_progress.json", "")
    ).upper()
    rec = {
        "path": path,
        "mtime": os.path.getmtime(path),
        "verdict": d.get("verdict"),
        "defaults_round": d.get("defaults_round"),
        "npz_id": d.get("npz_id"),
        "bh": d.get("bh"),
        "baseline_gain": d.get("baseline_gain"),
        "final_365d": d.get("final_365d"),
        "stored_final_gain": d.get("final_gain_fresh_vec", d.get("cumulative_gain")),
    }
    co = d.get("cumulative_overrides") or {}
    rec["n_promoted"] = sum(
        1
        for v in (d.get("done") or {}).values()
        if isinstance(v, dict) and v.get("promoted")
    )
    if EVAL_ALL:
        rec["set_source"] = (
            "cumulative_overrides"
            if co
            else ("initial_overrides" if d.get("initial_overrides") else "defaults")
        )
        co = co or d.get("initial_overrides") or {}
        rec["final_trades_stored"] = d.get("final_trades")
    if not co and not EVAL_ALL:
        rec["skip"] = "no cumulative_overrides"
        return ss, rec
    ov = sanitize(co, rec)
    sg = rec["stored_final_gain"]
    finished = bool(
        d.get("verdict")
        or d.get("result_done_utc")
        or d.get("final_gain_fresh_vec") is not None
    )
    rec["finished"] = finished
    if EVAL_ALL:
        pass
    elif ALL_FINISHED:
        if not finished:
            rec["skip"] = (
                "not finished (no verdict / result_done_utc / final_gain_fresh_vec)"
            )
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
        rec["evidence"].update(
            n_promoted=rec["n_promoted"],
            bh_pct=d.get("bh"),
            baseline_gain=d.get("baseline_gain"),
            npz_id=d.get("npz_id") or "",
            defaults_round=d.get("defaults_round") or "",
        )
        try:
            rec["evidence"]["engine_md5"] = hashlib.md5(
                (ROOT / "v12_quick_engine.py").read_bytes()
            ).hexdigest()[:8]
        except Exception:
            rec["evidence"]["engine_md5"] = ""
    except Exception as e:
        rec["skip"] = f"fresh eval failed: {e}"
    return ss, rec


def sanitize(co, rec):
    """Integrity stripping shared by the progress-file collect and the published-board source: VEC_ONLY renames/removals,
    engine artefacts (MODE/SIMPLE_PRICE_GT0/VENUE/IS_TRADIER/MAX_AUGMENTS_PER_POSITION/ABLATION_*), MAX_ORDER_VALUE<=0. Fills rec."""
    ov, rec["renamed"], rec["dropped"] = migrate(co)
    # 2026-10-08 CONTAMINATION FIX: engine-level artefacts never go live — MODE (venue engine mode; 71 stocks sets were "rescued"
    # by MODE=crypto), SIMPLE_PRICE_GT0_ENABLED (pseudo-gate), ABLATION_* (engine kills). Stripped BEFORE the fresh eval below, so
    # the evidence that qualifies the set is measured without them (never the contaminated number).
    rec["artefact_stripped"] = [
        k for k in ov if k in ARTEFACT_KEYS or k.startswith("ABLATION_")
    ]
    for k in rec["artefact_stripped"]:
        ov.pop(k, None)
    # MAXAUG_NEVER_ZERO retired 2026-10-15: the switch itself is deleted, so ARTEFACT_KEYS drops it outright.
    rec["mandate_stripped"] = []
    # 2026-10-13 fishnet: a per-sym MAX_ORDER_VALUE of 0 blocks every order (FCN_LONG/SHORT poison this turn) — never live.
    if "MAX_ORDER_VALUE" in ov:
        try:
            _mov = float(str(ov["MAX_ORDER_VALUE"]).strip())
        except Exception:
            _mov = None
        if _mov is not None and _mov <= 0:
            rec["mandate_stripped"].append(f"MAX_ORDER_VALUE={ov['MAX_ORDER_VALUE']}")
            ov.pop("MAX_ORDER_VALUE", None)
    rec["overrides"] = ov
    return ov


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
    print(
        f"[collect] {len(res)} sym_sides, evaluated {sum(1 for r in res.values() if 'evidence' in r)} -> {out}"
    )


def _ssh(targets, cmd, timeout):
    last = ""
    for t in targets:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", t, cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0:
            return t, r.stdout
        last = (r.stderr or r.stdout)[-300:]
    raise RuntimeError(f"ssh {targets} failed: {last}")


def _scp(src, dst):
    r = subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", src, dst],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RuntimeError(f"scp {src} {dst}: {r.stderr[-200:]}")


def gate_365d(ss, rec, conf):
    c = conf.get(ss)
    if c is not None:
        ok = (
            bool(c.get("valid"))
            and float(c.get("gain_365d") or 0) > 0
            and int(c.get("trades") or 0) >= 30
        )
        return (
            ok,
            f"confirmed_365d valid={c.get('valid')} gain={c.get('gain_365d')} trades={c.get('trades')} at={c.get('confirmed_at')}",
        )
    f = rec.get("final_365d")
    if isinstance(f, dict):
        ok = bool(f.get("valid")) and float(f.get("gain_pct") or 0) > 0
        return (
            ok,
            f"progress final_365d valid={f.get('valid')} gain={f.get('gain_pct')} trades={f.get('trades')} reason={f.get('invalid_reason')}",
        )
    return True, "no 365D verdict (not required)"


def load_blocklist():
    try:
        d = json.loads((ROOT / "data" / "golive_blocklist.json").read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def book_diff(ss, new):
    import switch_parity as sp

    try:
        raw = json.loads(sp._book_path(ss).read_text())
        old_e = raw.get(ss) or {}
    except Exception:
        old_e = {}
    old = old_e.get("overrides") or {}
    ch = {
        k: [old.get(k), v]
        for k, v in new.items()
        if k in old and not sp._same_val(old.get(k), v)
    }
    add = [k for k in new if k not in old]
    rem = [k for k in old if k not in new]
    return {
        "old_tag": old_e.get("winning_tag"),
        "old_n": len(old),
        "new_n": len(new),
        "changed_n": len(ch),
        "added_n": len(add),
        "removed_n": len(rem),
        "changed_sample": dict(list(ch.items())[:15]),
        "added_sample": add[:15],
        "removed_sample": rem[:15],
    }


def _trades_from_published_sheet(symside, gain=None, tol=0.01):
    """Recover a null trades count from the board's OWN published filename (`..._t<N>_30d_matrix.xlsx`) without
    re-evaluating anything. Matched on the evidence's gain, never on mtime: a sym_side usually has several published
    sheets and the newest is often a DIFFERENT board (ACEUSDT_LONG's newest is an older `gain0p00_t0` run, while the
    winner is `bh41p09_gain65p15`), so mtime would attribute another board's trade count. Returns None when the
    winning sheet predates the `_t<N>_` naming (the pre-2026-10 format carries no count) — the caller then records
    trades_unknown rather than inventing a number."""
    if gain is None:
        return None
    try:
        from tools.v15_final_naming import parse_final_matrix_name as _pfm
    except Exception:
        return None
    try:
        cands = list((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL").glob(f"{symside}_bh*gain*_30d_matrix.xlsx"))
    except Exception:
        return None
    for p in cands:
        d = _pfm(p.name) or {}
        if d.get("trades") is None or d.get("gain") is None:
            continue
        try:
            if abs(float(d["gain"]) - float(gain)) <= tol:
                return int(d["trades"])
        except Exception:
            continue
    return None


def published_collected(path):
    """USER 2026-10-13: go-live source = the latest PUBLISHED board per sym_side (tools/v15_published_selection.py on S1). The set and
    its evidence are the board's own (md5-verified against the sheet manifest); integrity stripping identical to the collect path."""
    doc = json.loads(Path(path).read_text())
    src = doc.get("sym_sides", doc) if isinstance(doc, dict) else {}
    out = {}
    for ss, r in (src or {}).items():
        if not isinstance(r, dict) or str(ss).startswith("_"):
            continue
        rec = {"host": r.get("host"), "path": r.get("progress"), "verdict": None, "n_promoted": None, "final_365d": None, "published_at": r.get("published_at"), "overrides_md5": r.get("overrides_md5"), "manifest": r.get("manifest")}
        if r.get("refuse"):
            rec["skip"] = f"published: {r['refuse']}"
        elif not r.get("manifest_ok"):
            rec["skip"] = "published: manifest md5 not verified"
        else:
            sanitize(r.get("overrides") or {}, rec)
            rec["evidence"] = dict(r.get("evidence") or {})
        out[str(ss).upper()] = rec
    return doc.get("_meta", {}), out


def golive(a):
    import switch_parity as sp

    if a.published:
        a.all_finished = True  # published boards are finished by definition; risk carried by size (require_qualified=False)
        _pmeta, collected = published_collected(a.published)
        print(f"[golive] PUBLISHED source {a.published}: {_pmeta}", flush=True)
        return golive_rows(a, sp, collected, {}, {"source": "published", "path": a.published, "meta": _pmeta})
    sel = json.loads(Path(a.selection).read_text())
    hosts = {h["name"]: h for h in json.loads(Path(a.hosts).read_text())["hosts"]}
    # USER 2026-10-13 "are all calculated sym_sides live?": under the NPZ JIT law worker NPZs lag by hours, so the fresh re-eval
    # hit STALE_NPZ_SKIPPED -> 0 trades -> 542/571 sets refused (0 applied at 14:09Z). The re-eval runs the set on the board's own
    # NPZ (npz_id recorded in evidence), like the pilot launches (V12_ALLOW_STALE_NPZ=1). Kill switch V15_GOLIVE_ALLOW_STALE=0.
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
            _scp(
                str(ROOT / "tools" / "v15_persym_golive.py"),
                f"{via}:binance-sandbox/tools/v15_persym_golive.py",
            )
            _scp(str(lf), f"{via}:/tmp/v15_persym_golive_files.txt")
            _, out = _ssh(
                [via],
                f"cd ~/binance-sandbox && {'V15_COLLECT_ALL_FINISHED=1 ' if a.all_finished else ''}{'V12_ALLOW_STALE_NPZ=1 ' if os.environ.get('V15_GOLIVE_ALLOW_STALE', '1') == '1' else ''}timeout {a.collect_timeout} .venv/bin/python -u tools/v15_persym_golive.py collect --files /tmp/v15_persym_golive_files.txt --out /tmp/v15_persym_golive_out.json --workers {a.workers} 2>&1 | grep -v Warning | tail -3",
                a.collect_timeout + 60,
            )
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
    return golive_rows(a, sp, collected, host_err, {"source": "selection", "path": a.selection, "selected": len(sel)})


def golive_rows(a, sp, collected, host_err, src):
    conf = {}
    try:
        conf = json.loads((ROOT / "data" / "confirmed_365d.json").read_text())
    except Exception as e:
        print(f"[golive] confirmed_365d.json unreadable: {e}")
    blocklist = load_blocklist()
    if blocklist:
        print(f"[golive] blocklist active: {sorted(blocklist)}", flush=True)
    rows, n_pass, n_reg = {}, 0, 0
    for ss in sorted(collected):
        rec = collected[ss]
        row = {
            "host": rec.get("host"),
            "path": rec.get("path"),
            "verdict": rec.get("verdict"),
            "renamed": rec.get("renamed"),
            "dropped": rec.get("dropped"),
            "n_promoted": rec.get("n_promoted"),
            "final_365d": rec.get("final_365d"),
        }
        if ss in blocklist:
            b = (
                blocklist[ss]
                if isinstance(blocklist[ss], dict)
                else {"reason": str(blocklist[ss])}
            )
            row.update(
                result="REFUSED",
                reason=f"golive-blocklisted: {b.get('reason', 'no reason')} (added {b.get('added', '?')}, until {b.get('until', '?')})",
            )
            rows[ss] = row
            continue
        if rec.get("skip"):
            row.update(result="SKIP", reason=rec["skip"])
            rows[ss] = row
            continue
        ev, ov = rec["evidence"], rec["overrides"]
        row["evidence"] = ev
        row["registered"] = False
        # MEASURED BEATS CLAIMED (2026-10-17). The hourly h2h cycle re-measures the incumbent against this exact
        # published board on ONE NPZ and writes the winner with its MEASURED gain. This refresh then re-registered
        # the SAME board through switch_parity minutes later and overwrote that decision with the cat_side snapshot
        # + coerced keys, recording the board's CLAIMED gain. Measured today: PBF_LONG (h2h measured 14.9874, row
        # became 12.348) and PLTR_SHORT (measured 4.0914, row became the 4.6633 claim with 2 keys off the approved
        # set) — 2 of the 10 sets h2h had just applied, silently, because both writers "succeeded".
        # A publish older than the last measurement has nothing new to say, so it must not overwrite it. This is
        # deliberately narrow: it only skips when the live row IS an h2h decision AND that decision is newer than
        # this board's publish stamp. Every other registration path is untouched — n_promoted=0 is NOT the trigger
        # (418 of 551 live rows are legitimately n_promoted=0; refusing those would break most of go-live).
        _skip_why = _measured_decision_newer(ss, rec.get("published_at"))
        if _skip_why:
            row.update(result="SKIP", reason=_skip_why)
            rows[ss] = row
            continue
        if (
            ov.get("SIMPLE_PRICE_GT0_ENABLED") is True
            or str(ov.get("SIMPLE_PRICE_GT0_ENABLED")).lower() == "true"
        ):
            row.update(result="REFUSED", reason="SIMPLE_PRICE_GT0_ENABLED=True set")
            rows[ss] = row
            continue
        ir = str(ev.get("invalid_reason") or "")
        if a.all_finished and (
            ir.startswith(("override", "prepare failed")) or "incompatible" in ir
        ):
            row.update(result="REFUSED", reason=f"engine rejects the set: {ir[:200]}")
            rows[ss] = row
            continue
        # USER 2026-10-15: "the eval can not move on until it has at least 10 trades ... all tradeable_keys should be
        # actually making trades by now". Correct — and that is why UNKNOWN must never be read as ZERO. `int(None or 0)`
        # refused 37 published boards (gain 15-109 %, 24-478 trades in their own progress) as "zero trades" and kept
        # live on an OLDER set, because their trades field arrived null. A board cannot publish below the 10-trade
        # floor, so only a MEASURED 0 is a silencing risk; unknown is a bookkeeping hole, recorded and followed.
        _ev_tr = ev.get("trades")
        if _ev_tr is None:
            _ev_tr = _trades_from_published_sheet(ss, ev.get("gain_pct"))
            if _ev_tr is not None:
                ev["trades"] = _ev_tr
                ev["trades_source"] = "published_sheet_name"
            else:
                row["trades_unknown"] = True
        if a.all_finished and _ev_tr is not None and int(_ev_tr) == 0:
            row.update(
                result="REFUSED",
                reason="zero trades MEASURED on the board (registering would silence a tradeable key)",
            )
            rows[ss] = row
            continue
        ok365, why365 = gate_365d(ss, rec, conf)
        row["gate_365d"] = why365
        # USER 2026-10-13 NEW RULE: "we now apply live BEFORE 365D and backtest_v12_live verification, which does not
        # make them obsolete — they need to run also, but meanwhile the newest settings trade immediately." The 365D
        # verdict is therefore RECORDED, never a blocker. Verification keeps running in the fleet chain, and a
        # 2-consecutive-losing-trade streak on the new settings escalates that sym_side to prioritized 365D
        # optimization (tools/v15_loss_streak_watch.py). Restore the old hard gate with V15_GOLIVE_REQUIRE_365D=1.
        # Every INTEGRITY refusal above/below stays: blocklist, artefact keys, zero-trade sets, engine-rejected sets,
        # secret/type gates. Those protect against junk, not against being unverified.
        if not ok365:
            if os.environ.get("V15_GOLIVE_REQUIRE_365D", "0") == "1" and not a.all_finished:
                row.update(result="REFUSED", reason=f"365D verdict fails: {why365} (V15_GOLIVE_REQUIRE_365D=1)")
                rows[ss] = row
                continue
            row["applied_before_365d"] = True
            print(f"[golive] {ss} applying BEFORE 365D verification (USER 2026-10-13 rule): {why365}", flush=True)
        dr = sp.register_workbook_result(
            ss,
            dict(ov),
            dict(ev),
            dry_run=True,
            diff_only=True,
            require_qualified=not a.all_finished,
            pin_all=bool(getattr(a, "pin_all", False)),
        )
        if "gates passed" not in str(dr.get("reason")):
            row.update(
                result=(
                    "NOTHING_TO_REGISTER"
                    if "nothing to register" in str(dr.get("reason"))
                    else "REFUSED"
                ),
                reason=str(dr.get("reason"))[:400],
                diff_only=dr.get("diff_only"),
            )
            rows[ss] = row
            continue
        n_pass += 1
        row.update(
            qualified_30d=dr.get("qualified_30d"),
            unqualified_reasons=dr.get("unqualified_reasons"),
            negative_gain_minimal_tier=(float(ev.get("gain_pct") or 0) <= 0),  # USER 2026-10-13: never blocked, minimal perf-tier size
        )
        row.update(
            result="PASS",
            would_tag=dr.get("would_tag"),
            neg_block_preserved=dr.get("neg_block_preserved"),
            n_keys=dr.get("n_keys"),
            diff_only=dr.get("diff_only"),
            would_register=dr.get("would_register"),
            change=book_diff(ss, dr.get("would_register") or {}),
        )
        # 2026-10-15 CONVERGENCE: a set already byte-identical on the live surface is a no-op write that still costs ~35 s
        # under live load (two 56/59 MB JSON books rewritten + a ~300 KB history row into the 1.12 GB SQLite + per-key verify).
        # The 19:20Z run spent its whole 5400 s timeout that way and died after 148 of 394 sets, so every sym_side past the
        # cut stayed on an older board. Skipping the no-ops leaves only the sets that actually differ, so the run finishes
        # and live converges on the latest published boards. Exactness proved: dry-run would_register == the live row on
        # every already-applied set (40/40 sampled). Kill switch V15_GOLIVE_SKIP_NOOP=0.
        if a.apply and os.environ.get("V15_GOLIVE_SKIP_NOOP", "1") == "1":
            _wr = dr.get("would_register")
            if isinstance(_wr, dict) and _wr:
                try:
                    # the overrides COLUMN, not get_overrides() — that now serves the full measured set
                    # (per_sym_store PER_SYM_APPLY_FULL_SET), while would_register is the sheet-key diff.
                    import per_sym_store as _pss_live
                    _row = _pss_live.get(ss)
                    _cur = dict((_row or {}).get("overrides") or {}) if _row else None
                except Exception:
                    _cur = None
                # 2026-10-16: the diff half matching is NOT proof the set is live. Live resolves the FULL frozen set
                # (per_sym_store.get_overrides, USER 2026-10-15), so a row whose overrides COLUMN equals this run's
                # diff-only key set can still carry an older board's defaults_snapshot underneath. Measured on
                # refresh_1238: 139 sym_sides were skipped as already-live while the live entry gate read them as
                # LIVE_SET_NE_BOARD (XRPUSDC_LONG 45 keys, ZCSH_SHORT 22, TIAUSDC_SHORT 850) — on crypto that verdict
                # also refuses every ENTRY, so those sym_sides could neither update nor open. The no-op skip now has to
                # agree with the gate itself: same comparator, live_twins.vec_exact.board_live_diff.
                # Fail-CLOSED: if the check cannot run, the set is registered (a wasted write beats a stale board).
                # V15_GOLIVE_NOOP_BOARD_CHECK=0 restores the pre-2026-10-16 diff-only skip.
                _offboard = None
                if isinstance(_cur, dict) and _cur == _wr:
                    if os.environ.get("V15_GOLIVE_NOOP_BOARD_CHECK", "1") == "0":
                        _offboard = []
                    else:
                        try:
                            from live_twins import vec_exact as _vx_live
                            _offboard = _vx_live.board_live_diff(ss.rsplit("_", 1)[0], ss.rsplit("_", 1)[1], dict(ov))
                        except Exception as _obe:
                            _offboard = [f"<board check failed: {_obe}>"]
                if _offboard == []:
                    row["already_live"] = True
                    row["registered"] = True
                    rows[ss] = row
                    continue
                if _offboard:
                    row["noop_but_offboard"] = _offboard[:8]
                    row["noop_but_offboard_n"] = len(_offboard)
        if a.apply:
            rr = sp.register_workbook_result(
                ss,
                dict(ov),
                dict(ev),
                dry_run=False,
                diff_only=True,
                require_qualified=not a.all_finished,
                pin_all=bool(getattr(a, "pin_all", False)),
            )
            row["applied"] = {
                k: rr.get(k)
                for k in (
                    "registered",
                    "reason",
                    "tag",
                    "sqlite",
                    "book",
                    "trb_overlay",
                    "ledger",
                    "backup",
                )
            }
            row["applied"]["verify_ok"] = (rr.get("verify") or {}).get("ok")
            n_reg += 1 if rr.get("registered") else 0
        row["registered"] = bool((row.get("applied") or {}).get("registered"))
        rows[ss] = row
    from collections import Counter

    summary = {
        "selected": src.get("selected", len(collected)),
        "source": src.get("source"),
        "pin_all": bool(getattr(a, "pin_all", False)),
        "collected": len(collected),
        "evaluated": sum(1 for r in collected.values() if "evidence" in r),
        "results": dict(Counter(r["result"] for r in rows.values())),
        "pass": n_pass,
        "registered": n_reg,
        "already_live_skipped": sum(1 for r in rows.values() if r.get("already_live")),
        "host_errors": host_err,
        "refuse_reasons_top": Counter(
            r["reason"].split(":")[0][:60]
            for r in rows.values()
            if r["result"] != "PASS"
        ).most_common(12),
        "renamed_sets": sum(1 for r in rows.values() if r.get("renamed")),
        "dropped_sets": sum(1 for r in rows.values() if r.get("dropped")),
        "mode_all_finished": bool(a.all_finished),
        "pass_qualified": sum(
            1 for r in rows.values() if r["result"] == "PASS" and r.get("qualified_30d")
        ),
        "pass_unqualified": sum(
            1
            for r in rows.values()
            if r["result"] == "PASS" and not r.get("qualified_30d")
        ),
        "pass_negative_gain_minimal_tier": sum(
            1
            for r in rows.values()
            if r["result"] == "PASS" and r.get("negative_gain_minimal_tier")
        ),
    }
    summary["blocklisted"] = sorted(
        ss
        for ss, r in rows.items()
        if str(r.get("reason", "")).startswith("golive-blocklisted")
    )
    rep = {
        "date": a.date,
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "APPLY" if a.apply else "DRY_RUN",
        "selection": src.get("path"),
        "renames": RENAMES,
        "removed": sorted(REMOVED),
        "blocklist": blocklist,
        "summary": summary,
        "sym_sides": rows,
    }
    out = Path(a.out or ROOT / "data" / "daily_chain" / f"persym_golive_{a.date}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))
    for ss, r in rows.items():
        if r["result"] == "PASS":
            c = r["change"]
            print(
                f"  PASS {ss} gain={r['evidence'].get('gain_pct'):.2f} trades={r['evidence'].get('trades')} tim={r['evidence'].get('tim_pct')} dd={r['evidence'].get('max_dd_pct')} | book {c['old_tag']} old_n={c['old_n']} -> new_n={c['new_n']} changed={c['changed_n']} added={c['added_n']} removed={c['removed_n']} renamed={r.get('renamed')}"
            )
    print(f"[golive] report {out} mode={rep['mode']}")
    return 1 if host_err else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="golive", choices=("golive", "collect"))
    ap.add_argument("--files")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--eval-all",
        action="store_true",
        help="collect: no pre-screen (tradeable check); same as V15_COLLECT_EVAL_ALL=1",
    )
    ap.add_argument(
        "--selection", default=str(ROOT / "data" / "avg_delta_selection.json")
    )
    ap.add_argument("--hosts", default=str(ROOT / "tools" / "fleet_hosts.json"))
    ap.add_argument("--collect-timeout", type=int, default=900)
    ap.add_argument(
        "--date",
        default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d"),
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="LIVE WRITE: only after director/user confirmation",
    )
    ap.add_argument(
        "--published",
        help="USER 2026-10-13: go-live source = data/published_selection.json from tools/v15_published_selection.py (latest published board per sym_side, md5-verified vs sheet manifest, board evidence, no re-eval); implies --all-finished",
    )
    ap.add_argument(
        "--pin-all",
        action="store_true",
        help="USER 2026-10-13 exact-board parity: pin EVERY board key per-sym (also keys equal to today's default) so later default moves cannot drift live off the board",
    )
    ap.add_argument(
        "--all-finished",
        action="store_true",
        help="USER 2026-10-06: register EVERY finished set (any gain), diff-only; still refuses SIMPLE_PRICE_GT0, engine-rejected and zero-trade sets",
    )
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
