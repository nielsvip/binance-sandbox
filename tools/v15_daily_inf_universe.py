#!/usr/bin/env python3
"""v15_daily_inf_universe — daily refresh of the live 'inf' crypto account books (BACKTEST_BIBLE §68.3 step 3b, USER 2026-10-06).

symbols_inf_long.json / symbols_inf_short.json (repo root on the Mac, plain JSON list of symbols) are rewritten with the
15 best LONG and 15 best SHORT crypto sym_sides of that day's backtest (USER 2026-10-08), ranked on the FRESH 30D result of the final
per-sym set. Results are fetched from the servers (S1/s2/s5): the pointer ~/v15_current_progress_dir.txt PLUS every
~/v15_* campaign dir (top level and progress/ subdir) and the lifecycle_pilot dir — campaign dirs rotate fleet-wide
(2026-10-08: pointer-only fetch saw 19 files while 124 fresh ones sat in the previous campaign), so the day boundary is
the per-record 36h evaluated filter, not the directory. Nothing is recomputed here.

Metrics source (fail-closed): progress["diagnose_repair"]["after"] — the only fresh full-set record that carries gain,
trades, TIM and DD. It is used only when its gain equals the progress final set's gain (cumulative_gain /
final_gain_fresh_vec), i.e. it describes the set that is actually final. Anything else -> ineligible "no fresh metrics".

Gates (same as step 3 / switch_parity.qualifies_30d + v15_pilot._qualifies_365d):
  tradeable crypto sym_side (tools/v15_universe.build()['allowed_sym_sides'] = tradeable_keys.json minus
  data/universe_exclude.json), fresh (evaluated inside --max-age-hours), valid, gain > 0, TIM 20-80, DD <= 30,
  trades >= 10, verdict not IMPOSSIBLE/NO_TRADES, no diagnostic_only / needs_redo, 365D passes when a final_365d verdict
  exists, final set has no SIMPLE_PRICE_GT0_ENABLED=True and no *_VEC_ONLY_* key (BIBLE §68.2.1; the registrar refuses
  such sets, so they can never be live per-sym).

Safety:
  * symbols with an OPEN inf position (inf/{long,short}_positions.json positionAmt != 0, or an inf active hedge in
    inf/tracker.json) stay in that side's book until closed (no stranding);
  * a symbol eligible on BOTH sides goes only to its better-gain side by default (--both-sides resolve):
    ez_positions_service.cleanup_positions HEDGE PAIR CLEANUP drops both inf keys of a symbol listed on both sides with
    no open position unless the symbol is in the winners/losers lists, so listing both would silently trade neither;
  * never pads with ineligible symbols (fewer than 15 eligible -> only the eligible ones);
  * --apply refuses when a host fetch failed (--allow-partial-hosts overrides), when positions files are stale or
    unreadable, when fewer than --min-records fresh sym_sides were fetched (--allow-thin-fetch overrides; a collapsed
    fleet view must keep yesterday's books, never write a retention-only skeleton), or when a side would become empty
    (--allow-empty-side overrides);
  * dry-run by default; --apply = backup to backups/ + atomic write (tmp + fsync + os.replace) + re-read verify.
Report: data/inf_universe/<YYYYMMDD>.json + .md (ranked table, kept-for-open-position, diff vs previous books) and the
fetched records data/inf_universe/<YYYYMMDD>_records.json. All numbers are single-sym_side 30D vector backtests
([DIAGNOSTIC ONLY] under the CLAUDE.md sample floor); no Sharpe is emitted.

usage: v15_daily_inf_universe.py [--apply] [--hosts s1-pub,s2,s5] [--top 15] [--max-age-hours 36] [--min-records 20]
                                 [--records PATH] [--both-sides resolve|keep] [--allow-partial-hosts] [--allow-empty-side]
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("INF_UNIVERSE_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT_DIR = ROOT / "data" / "inf_universe"
BOOKS = {"LONG": ROOT / "symbols_inf_long.json", "SHORT": ROOT / "symbols_inf_short.json"}
POSITIONS = {"LONG": ROOT / "inf" / "long_positions.json", "SHORT": ROOT / "inf" / "short_positions.json"}
TRACKER = ROOT / "inf" / "tracker.json"
EXCLUDE = ROOT / "data" / "universe_exclude.json"
CONFIRMED_365D = ROOT / "data" / "confirmed_365d.json"
DEFAULT_HOSTS = ("s1-pub", "s2", "s5")
CRYPTO_QUOTES = ("USDT", "USDC")
TIM_MIN, TIM_MAX = 20.0, 80.0
MAX_DD_PCT = 30.0
MIN_TRADES_30D = 10
QUAL_365D_FLOOR_TRADES = 80
GAIN_MATCH_TOL = 1e-4
POSITIONS_MAX_AGE_S = 900
BAD_VERDICTS = ("IMPOSSIBLE", "NO_TRADES")
KEEP_FIELDS = (
    "symside", "baseline_gain", "bh", "window_days", "cumulative_gain", "final_gain_fresh_vec", "final_gain", "final_trades",
    "verdict", "verdict_reasons", "publish_class", "run_class", "diagnostic_only", "not_compliant", "last_run_failed",
    "final_365d", "engine_mixed_chain", "result_start_utc", "result_done_utc", "defaults_round", "host", "impossible_reasons",
)
REMOTE_EXTRACT = r'''
import glob, hashlib, json, os, sys, time
pdir, max_age_s = sys.argv[1], float(sys.argv[2])
keep = json.loads(sys.argv[3])
home = os.path.expanduser("~")
patterns = [
    os.path.join(pdir, "*_v14_progress.json"),
    os.path.join(home, "v15_*", "*_v14_progress.json"),
    os.path.join(home, "v15_*", "progress", "*_v14_progress.json"),
    os.path.join(home, "binance-sandbox", "data", "reports", "lifecycle_pilot", "*_v14_progress.json"),
]
paths = sorted({p for pat in patterns for p in glob.glob(pat)})
out = []
for path in paths:
    sym_side = os.path.basename(path)[: -len("_v14_progress.json")]
    symbol = sym_side.rsplit("_", 1)[0]
    if not symbol.endswith(("USDT", "USDC")) or not sym_side.endswith(("_LONG", "_SHORT")):
        continue
    st = os.stat(path)
    if st.st_mtime < time.time() - max_age_s:
        continue
    rec = {"sym_side": sym_side, "file": path, "mtime": st.st_mtime, "size": st.st_size}
    try:
        with open(path) as fh:
            d = json.load(fh)
    except Exception as e:
        rec["error"] = f"unreadable: {e}"[:160]
        out.append(rec)
        continue
    for k in keep:
        if k in d:
            rec[k] = d[k]
    dr = d.get("diagnose_repair") or {}
    rec["diagnose_repair"] = {k: dr.get(k) for k in ("accepted", "accept_reason", "before", "after", "skipped")} if isinstance(dr, dict) else {}
    rec["needs_redo"] = bool(d.get("needs_redo"))
    rec["has_repair_365d"] = bool(d.get("repair_365d"))
    co = d.get("cumulative_overrides") or {}
    rec["has_cumulative_overrides"] = bool(co)
    rec["simple_price_gt0"] = co.get("SIMPLE_PRICE_GT0_ENABLED")
    # 2026-10-06 USER: old switch names are a relic -> migrate with the ONE alias map (tools/v15_persym_golive.py), the same migration go-live applies
    # 2026-10-08 FIX: remote runs as `python3 -` with cwd=$HOME -> the tools dir is not on sys.path (s2/s5 ModuleNotFoundError on 2026-10-07)
    for _cand in (os.path.expanduser("~/binance-sandbox/tools"), os.path.expanduser("~/binance/tools")):
        if os.path.isdir(_cand) and _cand not in sys.path:
            sys.path.insert(0, _cand)
    import v15_persym_golive as _golive
    co, _ren, _drop = _golive.migrate(co)
    rec["legacy_renamed"] = _ren
    rec["legacy_dropped"] = _drop
    rec["n_overrides"] = len(co)
    rec["overrides_md5"] = hashlib.md5(json.dumps(co, sort_keys=True, default=str).encode()).hexdigest()[:12]
    npz = d.get("npz_id") or {}
    rec["npz_md5"] = (npz.get("md5") or "")[:12] if isinstance(npz, dict) else ""
    rec["npz_ts_last"] = npz.get("ts_last") if isinstance(npz, dict) else None
    out.append(rec)
print(json.dumps(out, default=str))
'''


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def _f(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _parse_ts(x):
    try:
        t = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def jload(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def fetch_host(host, max_age_s, timeout=600):
    """(records, error) for one host: reads ~/v15_current_progress_dir.txt, extracts crypto progress summaries remotely."""
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host]
    try:
        r = subprocess.run(ssh + ["cat ~/v15_current_progress_dir.txt"], capture_output=True, text=True, timeout=60)
    except Exception as e:
        return [], f"{host}: progress dir read failed: {e}"
    pdir = (r.stdout or "").strip().splitlines()[0].strip() if r.returncode == 0 and r.stdout.strip() else ""
    if not pdir:
        return [], f"{host}: no ~/v15_current_progress_dir.txt ({(r.stderr or '').strip()[:120]})"
    cmd = "python3 - {} {} '{}'".format(pdir, int(max_age_s), json.dumps(list(KEEP_FIELDS)))
    try:
        r = subprocess.run(ssh + [cmd], input=REMOTE_EXTRACT, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return [], f"{host}: extract failed: {e}"
    if r.returncode != 0:
        return [], f"{host}: extract rc={r.returncode} {(r.stderr or '').strip()[-200:]}"
    try:
        recs = json.loads(r.stdout)
    except Exception as e:
        return [], f"{host}: bad extract output: {e}"
    for rec in recs:
        rec["fetched_from"] = host
        rec["progress_dir"] = pdir
    return recs, None


def record_evaluated_at(rec):
    """latest of result_start_utc / result_done_utc (stale carried done stamps exist), else file mtime."""
    stamps = [t for t in (_parse_ts(rec.get("result_start_utc")), _parse_ts(rec.get("result_done_utc"))) if t is not None]
    if stamps:
        return max(stamps)
    try:
        return dt.datetime.fromtimestamp(float(rec.get("mtime")), dt.timezone.utc)
    except Exception:
        return None


def _record_complete(rec):
    """a copy counts as complete when it carries a final per-sym set plus fresh full-set metrics (2026-10-08: a fresh
    campaign dir holds newer-but-partial copies that must not shadow the complete evaluation from the previous one)."""
    dr = rec.get("diagnose_repair") or {}
    after = dr.get("after") if isinstance(dr, dict) else None
    return bool(rec.get("has_cumulative_overrides")) and isinstance(after, dict) and after.get("gain") is not None


def dedupe_records(records):
    """one record per sym_side across hosts and campaign dirs (pilots fan progress copies out to peers; rsync keeps
    mtimes): readable beats unreadable, complete beats partial, then newest file mtime (the authoring host keeps
    writing after the copy was pushed), then biggest file."""
    best = {}
    for rec in records:
        ss = rec.get("sym_side")
        if not ss:
            continue
        key = (0 if rec.get("error") else 1, 1 if _record_complete(rec) else 0, float(rec.get("mtime") or 0), int(rec.get("size") or 0))
        if ss not in best or key > best[ss][0]:
            best[ss] = (key, rec)
    return {ss: v[1] for ss, v in best.items()}


def final_metrics(rec):
    """(metrics dict, reason) — fresh full-set 30D metrics of the FINAL set, or (None, why)."""
    dr = rec.get("diagnose_repair") or {}
    after = dr.get("after") if isinstance(dr, dict) else None
    if not isinstance(after, dict) or after.get("gain") is None:
        why = dr.get("skipped") if isinstance(dr, dict) and dr.get("skipped") else "no diagnose_repair.after"
        return None, f"no fresh full-set metrics ({why})"
    final_gain = _f(rec.get("cumulative_gain"))
    if final_gain is None:
        final_gain = _f(rec.get("final_gain_fresh_vec"))
    if final_gain is None:
        return None, "no final-set gain to match the fresh metrics against"
    if abs(_f(after.get("gain")) - final_gain) > GAIN_MATCH_TOL:
        return None, f"fresh metrics describe another set (diagnose_repair.after gain {_f(after.get('gain')):.4f} != final {final_gain:.4f})"
    return {
        "gain_pct": _f(after.get("gain")),
        "trades": int(after.get("trades") or 0),
        "tim_pct": _f(after.get("tim")),
        "max_dd_pct": _f(after.get("dd")),
        "win_rate_pct": _f(after.get("wr")),
        "valid": bool(after.get("valid")),
        "invalid_reason": after.get("reason") or "",
        "bh_pct": _f(after.get("bh")),
    }, ""


def qualifies_365d(v):
    """(ok, reasons) mirrors v15_pilot._qualifies_365d (valid, gain > 0, trades >= 80 or pro-rata on < 330d history)."""
    reasons = []
    v = v or {}
    if not v.get("valid"):
        reasons.append(f"365D invalid:{v.get('invalid_reason') or '?'}")
    trades = int(v.get("trades") or 0)
    span = _f(v.get("span_days"))
    floor = QUAL_365D_FLOOR_TRADES
    if span is not None and span < 330:
        floor = max(10, round(QUAL_365D_FLOOR_TRADES * span / 365))
    if trades < floor:
        reasons.append(f"365D trades {trades}<{floor}")
    g = _f(v.get("gain_pct"))
    if g is None or g <= 0:
        reasons.append(f"365D gain {g}")
    return (not reasons, reasons)


def evaluate(rec, allowed, now, max_age_h, vec_only_false_ok=False):
    """(eligible, reasons, metrics, flags) for one deduped record. vec_only_false_ok: deprecated no-op (old switch names are
    migrated by the go-live alias map, never a selection reason)."""
    reasons, flags = [], []
    ss = str(rec.get("sym_side") or "")
    symbol, _, side = ss.rpartition("_")
    if side not in ("LONG", "SHORT") or not symbol.endswith(CRYPTO_QUOTES):
        return False, ["not a crypto sym_side"], None, flags
    if rec.get("error"):
        return False, [rec["error"]], None, flags
    if ss not in allowed:
        reasons.append("not a tradeable key (tradeable_keys.json / universe_exclude.json)")
    ev = record_evaluated_at(rec)
    if ev is None or (now - ev).total_seconds() > max_age_h * 3600:
        reasons.append(f"stale: evaluated {ev.isoformat() if ev else '?'} > {max_age_h:.0f}h ago")
    if str(rec.get("verdict") or "").upper() in BAD_VERDICTS:
        reasons.append(f"verdict {rec.get('verdict')}")
    if rec.get("diagnostic_only"):
        reasons.append(f"diagnostic_only: {rec.get('diagnostic_only')}")
    if rec.get("needs_redo"):
        reasons.append("needs_redo pending (365D-driven re-fill)")
    if not rec.get("has_cumulative_overrides"):
        reasons.append("no final per-sym set (cumulative_overrides empty)")
    if rec.get("simple_price_gt0") is True or str(rec.get("simple_price_gt0")).lower() == "true":
        reasons.append("SIMPLE_PRICE_GT0_ENABLED=True set")
    if rec.get("legacy_renamed") or rec.get("legacy_dropped"):
        flags.append(f"old switch names migrated by the go-live alias map: renamed {sorted(rec.get('legacy_renamed') or {})[:4]} dropped {(rec.get('legacy_dropped') or [])[:4]}")
    m, why = final_metrics(rec)
    if m is None:
        reasons.append(why)
    else:
        if not m["valid"]:
            reasons.append(f"invalid:{m['invalid_reason'] or '?'}")
        if m["gain_pct"] is None or m["gain_pct"] <= 0:
            reasons.append(f"gain {m['gain_pct']}")
        if m["tim_pct"] is None or not (TIM_MIN <= m["tim_pct"] <= TIM_MAX):
            reasons.append(f"TIM {m['tim_pct']} outside [{TIM_MIN:.0f},{TIM_MAX:.0f}]")
        if m["max_dd_pct"] is None or m["max_dd_pct"] > MAX_DD_PCT:
            reasons.append(f"DD {m['max_dd_pct']} > {MAX_DD_PCT:.0f}")
        if m["trades"] < MIN_TRADES_30D:
            reasons.append(f"trades {m['trades']}<{MIN_TRADES_30D}")
    v365 = rec.get("final_365d")
    if isinstance(v365, dict) and v365:
        ok365, r365 = qualifies_365d(v365)
        if not ok365:
            reasons.extend(r365)
    else:
        flags.append("365D: no verdict")
    if rec.get("not_compliant"):
        flags.append(f"not_compliant: {str(rec.get('not_compliant'))[:90]}")
    if rec.get("engine_mixed_chain"):
        flags.append("engine_mixed_chain (fresh eval authoritative)")
    return (not reasons, reasons, m, flags)


def open_inf_positions(positions=None, tracker=None, max_age_s=POSITIONS_MAX_AGE_S, now_ts=None):
    """({'LONG': set(symbols), 'SHORT': set(symbols)}, problems). Read-only."""
    positions = positions or POSITIONS
    tracker = tracker or TRACKER
    now_ts = now_ts or time.time()
    out, problems = {"LONG": set(), "SHORT": set()}, []
    for side, path in positions.items():
        path = Path(path)
        if not path.exists():
            problems.append(f"{path.name} missing")
            continue
        age = now_ts - path.stat().st_mtime
        if age > max_age_s:
            problems.append(f"{path.name} stale ({age / 60:.0f} min old)")
        data = jload(path)
        if not isinstance(data, dict):
            problems.append(f"{path.name} unreadable")
            continue
        for key, pos in data.items():
            if not str(key).startswith("inf:") or not isinstance(pos, dict):
                continue
            amt = _f(pos.get("positionAmt")) or 0.0
            if abs(amt) > 0:
                ss = str(key).split(":", 1)[1]
                symbol, _, s = ss.rpartition("_")
                out[s if s in out else side].add(symbol)
    t = jload(tracker, {}) if Path(tracker).exists() else {}
    for h in (t.get("active_hedges") or []) if isinstance(t, dict) else []:
        k = h.get("position_key") if isinstance(h, dict) else None
        if k and str(k).startswith("inf:"):
            symbol, _, s = str(k).split(":", 1)[1].rpartition("_")
            if s in out:
                out[s].add(symbol)
    return out, problems


def build_books(evaluated, open_pos, top=15, both_sides="resolve"):
    """evaluated: {sym_side: (eligible, reasons, metrics, flags)}. Returns dict with ranked lists per side, the final books
    (top-N eligible + open-position retention), both-side conflicts."""
    ranked = {"LONG": [], "SHORT": []}
    for ss, (ok, _r, m, _fl) in evaluated.items():
        if ok:
            symbol, _, side = ss.rpartition("_")
            ranked[side].append((m["gain_pct"], symbol))
    for side in ranked:
        ranked[side].sort(key=lambda x: (-x[0], x[1]))
    conflicts = []
    if both_sides == "resolve":
        gl = {s: g for g, s in ranked["LONG"]}
        gs = {s: g for g, s in ranked["SHORT"]}
        for symbol in sorted(set(gl) & set(gs)):
            if symbol in open_pos["LONG"] and symbol in open_pos["SHORT"]:
                continue
            keep = "LONG" if (gl[symbol], 1) >= (gs[symbol], 0) else "SHORT"
            if symbol in open_pos["LONG"]:
                keep = "LONG"
            elif symbol in open_pos["SHORT"]:
                keep = "SHORT"
            drop = "SHORT" if keep == "LONG" else "LONG"
            ranked[drop] = [(g, s) for g, s in ranked[drop] if s != symbol]
            conflicts.append({"symbol": symbol, "kept_side": keep, "dropped_side": drop, "gain_long": gl[symbol], "gain_short": gs[symbol]})
    books, kept_open = {}, {}
    for side in ("LONG", "SHORT"):
        chosen = [s for _g, s in ranked[side][:top]]
        kept_open[side] = sorted(s for s in open_pos[side] if s not in chosen)
        books[side] = chosen + kept_open[side]
    return {"ranked": ranked, "books": books, "kept_open": kept_open, "conflicts": conflicts}


def diff_books(prev, new):
    out = {}
    for side in ("LONG", "SHORT"):
        p, n = list(prev.get(side) or []), list(new.get(side) or [])
        out[side] = {"added": sorted(set(n) - set(p)), "removed": sorted(set(p) - set(n)), "kept": sorted(set(p) & set(n))}
    return out


def atomic_write_json(path, obj):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with open(tmp, "w") as fh:
        fh.write(json.dumps(obj, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    if json.loads(path.read_text()) != obj:
        raise RuntimeError(f"post-write verify failed for {path}")


def allowed_sym_sides(root=ROOT):
    import v15_universe as U
    allowed = {str(s).upper() for s in U.build(root)["allowed_sym_sides"]}
    excl = jload(Path(root) / "data" / "universe_exclude.json", {}) or {}
    allowed -= {str(s).upper() for s in excl.get("exclude_sym_sides", [])}
    return allowed


def write_report(rep, stamp_date):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jp = OUT_DIR / f"{stamp_date}.json"
    mp = OUT_DIR / f"{stamp_date}.md"
    for cur, prev in ((jp, OUT_DIR / f"{stamp_date}.prev.json"), (mp, OUT_DIR / f"{stamp_date}.prev.md")):
        try:
            if cur.exists():
                shutil.copy2(cur, prev)
        except Exception:
            pass
    jp.write_text(json.dumps(rep, indent=1, default=str))
    L = [f"# inf universe {stamp_date} ({'APPLIED' if rep['applied'] else 'DRY-RUN'})", ""]
    L.append(f"- generated {rep['generated_utc']}; hosts {rep['hosts']}; host errors {rep['host_errors'] or 'none'}")
    L.append(f"- crypto sym_side records fetched {rep['n_records']} (deduped {rep['n_deduped']}); eligible LONG {rep['n_eligible']['LONG']} / SHORT {rep['n_eligible']['SHORT']}")
    L.append(f"- window: evaluated within {rep['max_age_hours']}h; top {rep['top']} per side; both-sides policy {rep['both_sides']}; vec_only_false_ok {rep['vec_only_false_ok']}")
    L.append("- numbers: single sym_side 30D vector backtest of the final per-sym set (diagnose_repair.after) — [DIAGNOSTIC ONLY] under the CLAUDE.md sample floor; no Sharpe emitted")
    if rep.get("apply_refused"):
        L.append(f"- **APPLY REFUSED**: {rep['apply_refused']}")
    if rep.get("position_problems"):
        L.append(f"- positions problems: {rep['position_problems']}")
    L.append(f"- 365D live certificates (data/confirmed_365d.json) present for: {rep.get('confirmed_365d_in_books') or 'none'} — live crypto opens are fail-closed on that file")
    for side in ("LONG", "SHORT"):
        L += ["", f"## {side} book ({len(rep['books'][side])} symbols)", "", "| # | sym_side | gain% | trades | TIM% | DD% | 365D gain% | 365D trades | host | flags |", "|---|---|---|---|---|---|---|---|---|---|"]
        for i, row in enumerate(rep["ranked"][side], 1):
            L.append(f"| {i} | {row['sym_side']} | {row['gain_pct']:.2f} | {row['trades']} | {row['tim_pct']:.2f} | {row['max_dd_pct']:.2f} | {row.get('gain_365d') if row.get('gain_365d') is None else round(row['gain_365d'], 2)} | {row.get('trades_365d')} | {row['host']} | {'; '.join(row['flags'])} |")
        if rep["kept_open"][side]:
            L.append(f"\nkept for OPEN inf position: {rep['kept_open'][side]}")
        d = rep["diff"][side]
        L.append(f"\ndiff vs previous book: added {d['added']} | removed {d['removed']} | kept {d['kept']}")
    if rep["conflicts"]:
        L += ["", "## both-side conflicts (resolved to the better-gain side)", ""] + [f"- {c['symbol']}: kept {c['kept_side']} ({c['gain_long']:.2f} L / {c['gain_short']:.2f} S)" for c in rep["conflicts"]]
    L += ["", "## ineligible", "", "| sym_side | reasons |", "|---|---|"]
    for ss, rs in sorted(rep["ineligible"].items()):
        L.append(f"| {ss} | {'; '.join(rs)[:300]} |")
    mp.write_text("\n".join(L) + "\n")
    return jp, mp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hosts", default=",".join(DEFAULT_HOSTS))
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--max-age-hours", type=float, default=36.0)
    ap.add_argument("--min-records", type=int, default=20)
    ap.add_argument("--allow-thin-fetch", action="store_true")
    ap.add_argument("--records", help="re-use a fetched records JSON instead of fetching")
    ap.add_argument("--report-tag", default="", help="suffix for the report file names (e.g. a variant dry-run)")
    ap.add_argument("--both-sides", choices=("resolve", "keep"), default="resolve")
    ap.add_argument("--allow-partial-hosts", action="store_true")
    ap.add_argument("--allow-empty-side", action="store_true")
    ap.add_argument("--vec-only-false-ok", action="store_true", help="accept sets whose *_VEC_ONLY_* keys are all False (flagged)")
    a = ap.parse_args(argv)
    now = now_utc()
    stamp_date = now.strftime("%Y%m%d")
    hosts = [h for h in a.hosts.split(",") if h]
    host_errors = []
    if a.records:
        records = jload(a.records, [])
    else:
        records = []
        for h in hosts:
            recs, err = fetch_host(h, max(a.max_age_hours, 24) * 3600 * 2)
            if err:
                host_errors.append(err)
                print(f"[inf-universe] {err}", flush=True)
            records.extend(recs)
            print(f"[inf-universe] {h}: {len(recs)} crypto progress records", flush=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{stamp_date}_records.json").write_text(json.dumps(records, default=str))
    allowed = allowed_sym_sides()
    deduped = dedupe_records(records)
    evaluated = {ss: evaluate(rec, allowed, now, a.max_age_hours, a.vec_only_false_ok) for ss, rec in deduped.items()}
    open_pos, pos_problems = open_inf_positions()
    res = build_books(evaluated, open_pos, a.top, a.both_sides)
    prev = {side: jload(p, []) or [] for side, p in BOOKS.items()}
    conf = jload(CONFIRMED_365D, {}) or {}
    ranked_rows = {}
    for side in ("LONG", "SHORT"):
        rows = []
        for g, symbol in res["ranked"][side][: a.top]:
            ss = f"{symbol}_{side}"
            ok, _r, m, fl = evaluated[ss]
            rec = deduped[ss]
            v365 = rec.get("final_365d") or {}
            rows.append({"sym_side": ss, **m, "gain_365d": _f(v365.get("gain_pct")), "trades_365d": v365.get("trades"), "host": rec.get("fetched_from"),
                         "evaluated_at": str(record_evaluated_at(rec)), "overrides_md5": rec.get("overrides_md5"), "defaults_round": rec.get("defaults_round"),
                         "verdict": rec.get("verdict"), "flags": fl + ([] if ss in conf else ["no live 365D certificate"])})
        ranked_rows[side] = rows
    refused = []
    if host_errors and not a.allow_partial_hosts:
        refused.append(f"host fetch errors: {host_errors}")
    if len(deduped) < a.min_records and not a.allow_thin_fetch:
        refused.append(f"thin fleet view: {len(deduped)} fresh sym_sides < --min-records {a.min_records} (campaign rotation or fleet outage? keeping yesterday's books)")
    if pos_problems:
        refused.append(f"open-position state not trustworthy: {pos_problems}")
    for side in ("LONG", "SHORT"):
        if not res["books"][side] and not a.allow_empty_side:
            refused.append(f"{side} book would be empty (0 eligible, 0 open) — pass --allow-empty-side to write an empty book")
    rep = {
        "generated_utc": now.isoformat(), "applied": False, "hosts": hosts, "host_errors": host_errors, "top": a.top,
        "max_age_hours": a.max_age_hours, "min_records": a.min_records, "both_sides": a.both_sides, "vec_only_false_ok": a.vec_only_false_ok, "n_records": len(records), "n_deduped": len(deduped),
        "n_eligible": {s: sum(1 for ss, v in evaluated.items() if v[0] and ss.endswith("_" + s)) for s in ("LONG", "SHORT")},
        "ranked": ranked_rows, "books": res["books"], "kept_open": res["kept_open"], "open_positions": {k: sorted(v) for k, v in open_pos.items()},
        "position_problems": pos_problems, "conflicts": res["conflicts"], "previous_books": prev, "diff": diff_books(prev, res["books"]),
        "ineligible": {ss: v[1] for ss, v in evaluated.items() if not v[0]},
        "eligible_beyond_top": {s: [f"{sym}_{s}" for _g, sym in res["ranked"][s][a.top:]] for s in ("LONG", "SHORT")},
        "confirmed_365d_in_books": sorted(f"{s}_{side}" for side in ("LONG", "SHORT") for s in res["books"][side] if f"{s}_{side}" in conf),
        "apply_refused": None, "backups": {},
    }
    if a.apply:
        if refused:
            rep["apply_refused"] = " | ".join(refused)
            print(f"[inf-universe] APPLY REFUSED: {rep['apply_refused']}", flush=True)
        else:
            stamp = now.strftime("%Y%m%d%H%M")
            (ROOT / "backups").mkdir(exist_ok=True)
            for side, path in BOOKS.items():
                if path.exists():
                    bp = ROOT / "backups" / f"before_inf_universe_{stamp}_{path.name}"
                    shutil.copy2(path, bp)
                    rep["backups"][side] = str(bp)
            for side, path in BOOKS.items():
                atomic_write_json(path, res["books"][side])
            rep["applied"] = True
            print(f"[inf-universe] APPLIED LONG={len(res['books']['LONG'])} SHORT={len(res['books']['SHORT'])}", flush=True)
    elif refused:
        rep["apply_refused"] = "(dry-run) an --apply now would be refused: " + " | ".join(refused)
    jp, mp = write_report(rep, stamp_date + (f"_{a.report_tag}" if a.report_tag else ""))
    print(f"[inf-universe] eligible LONG={rep['n_eligible']['LONG']} SHORT={rep['n_eligible']['SHORT']} books LONG={len(res['books']['LONG'])} SHORT={len(res['books']['SHORT'])} report {jp} {mp}", flush=True)
    return 0 if (rep["applied"] or not a.apply) else 2


if __name__ == "__main__":
    sys.exit(main())
