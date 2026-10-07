#!/usr/bin/env python3
"""v15_final_phase — Monday go-live qualification (USER 2026-10-02). Orchestrated on s1 by tools/v15_autopilot.py; also has host-side modes.

USER RULE: Mon 06:00 UTC every sym_side's BEST found settings run 365D; negative -> fix the 30D sheet (BIBLE §58 repair loop, existing scheduler chain +
tools/v15_365_cycle.py) until both windows are positive; ONLY positive sym_sides go live; negative / unverified ones do not trade at all (not on old settings
either); the live-faithful backtest_v12_engine parity test runs the 30D settings; everything that qualifies is live before the 13:30 UTC open.

Qualification (ALL must hold; evidence is copied into data/autopilot/final/qualifiers.json, nothing is inferred):
  1. chain verdict final_both_ok (30D AND 365D valid + positive, trade floors >=10 / >=80, TIM<=80, DD<=30, 365D span >= 330 d) from tools/v15_365_cycle.py
  2. the winning set is the one in the verdict's final_progress (cumulative_overrides) - that exact set is what parity / certification / the book get
  3. crypto: tools/confirm_365d.py certification code (confirm_symside, only this recipe) wrote a record (valid, gain>0, trades>=30)
  4. parity: live-faithful backtest_v12_engine 30D run of the same set. Status PASS / FAIL / UNAVAILABLE. STRICT since PAR/001 2026-10-04: QUALIFIED
     requires PASS; FAIL -> NEGATIVE; UNAVAILABLE/RUNNING/missing -> UNVERIFIED (all non-PASS blocked from trading).
Modes: host-scan | host-collect | host-parity SS | host-confirm SS   (run on a host, print JSON)   and, on s1, the functions imported by v15_autopilot.
"""
import argparse, datetime as dt, glob, json, os, re, shlex, subprocess, sys, time
from pathlib import Path

HOME = os.path.expanduser("~")
ROOT = Path(HOME) / "binance-sandbox"
FINAL_TAG = "20261005"
FINAL_DIRNAME = f"v15_final_{FINAL_TAG}"
FLOOR30, FLOOR365 = 10, 80
MIN_RUN = 18


# ----------------------------------------------------------------------------------------------- host side
def host_scan():
    """newest-round-first: {ss: {path, round, final_gain, mtime, defaults_round}} for every sym_side with a finished 30D sheet. Herd-era boards (data/reports/lifecycle_pilot, mtime >= 2026-10-03) join the newest round; older boards stay ranked by their run dir only."""
    runs = []
    for d in glob.glob(os.path.join(HOME, "v15_run*_2026*")):
        m = re.match(r"v15_run(\d+)_2026\d+$", os.path.basename(d))
        if m and int(m.group(1)) >= MIN_RUN and os.path.isdir(os.path.join(d, "progress")):
            runs.append((int(m.group(1)), os.path.join(d, "progress")))
    runs.sort(reverse=True)
    out = {}
    for rn, pdir in runs:
        for f in glob.glob(os.path.join(pdir, "*_v14_progress.json")):
            ss = os.path.basename(f)[: -len("_v14_progress.json")]
            if ss in out:
                continue
            try:
                d = json.load(open(f))
                fg = d.get("final_gain")
                co = d.get("cumulative_overrides")
                if fg is None or not isinstance(co, dict):
                    continue
                out[ss] = {"path": f, "round": rn, "final_gain": float(fg), "mtime": os.path.getmtime(f), "defaults_round": d.get("defaults_round")}
            except Exception:
                continue
    lc_round = runs[0][0] if runs else MIN_RUN
    lc_cut = dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc).timestamp()
    for f in glob.glob(os.path.join(str(ROOT), "data", "reports", "lifecycle_pilot", "*_v14_progress.json")):
        ss = os.path.basename(f)[: -len("_v14_progress.json")]
        try:
            mt = os.path.getmtime(f)
            if mt < lc_cut:
                continue
            d = json.load(open(f))
            fg = d.get("final_gain")
            co = d.get("cumulative_overrides")
            if fg is None or not isinstance(co, dict):
                continue
            cur = out.get(ss)
            if cur is None or (lc_round, mt) > (cur["round"], cur["mtime"]):
                out[ss] = {"path": f, "round": lc_round, "final_gain": float(fg), "mtime": mt, "defaults_round": d.get("defaults_round")}
        except Exception:
            continue
    print(json.dumps(out))


def _verdicts(final_dir):
    chain = os.path.join(final_dir, "chain")
    v = {}
    for f in glob.glob(os.path.join(chain, "v365", "*_365_cycle.json")):
        ss = os.path.basename(f)[: -len("_365_cycle.json")]
        v.setdefault(ss, {})["v365"] = f
    for f in glob.glob(os.path.join(chain, "repair_a*", "*_365_cycle.json")):
        ss = os.path.basename(f)[: -len("_365_cycle.json")]
        a = int(re.search(r"repair_a(\d+)", f).group(1))
        cur = v.setdefault(ss, {}).get("repair")
        if cur is None or a > cur[0]:
            v[ss]["repair"] = (a, f)
    res = {}
    for ss, d in v.items():
        f = d["repair"][1] if "repair" in d else d["v365"]
        try:
            j = json.load(open(f))
        except Exception:
            continue
        last = (j.get("rounds") or [{}])[-1]
        ov = None
        if j.get("final_both_ok") and j.get("final_progress"):
            try:
                ov = json.load(open(j["final_progress"])).get("cumulative_overrides")
            except Exception:
                ov = None
        res[ss] = {"file": f, "attempt": d["repair"][0] if "repair" in d else 0, "both_ok": bool(j.get("final_both_ok")), "unverifiable": bool(j.get("unverifiable")),
                   "final_progress": j.get("final_progress"), "overrides": ov, "span_365_days": j.get("span_365_days"), "w30": last.get("w30"), "w365": last.get("w365"), "ts": j.get("ts")}
    return res


def host_collect(final_dir):
    pdir = os.path.join(final_dir, "parity")
    par = {}
    for f in glob.glob(os.path.join(pdir, "*.out")):
        ss = os.path.basename(f)[:-4]
        txt = open(f).read()
        m = re.search(r"PARITY (\S+) (PASS|FAIL) :: (.*?) :: vec_gain=(\S+) vec_trades=(\S+) live_gain=(\S+) live_trades=(\S+)", txt)
        running = subprocess.run(["pgrep", "-f", f"v15_parity_check.py --sym-side {ss} "], capture_output=True, text=True).stdout.strip() != ""
        if m:
            par[ss] = {"status": m.group(2), "reason": m.group(3)[:200], "vec_gain": m.group(4), "vec_trades": m.group(5), "live_gain": m.group(6), "live_trades": m.group(7)}
        else:
            par[ss] = {"status": "RUNNING" if running else "UNAVAILABLE", "reason": ("running" if running else "no PARITY line (timeout/crash)"), "age_s": int(time.time() - os.path.getmtime(f))}
    conf = {}
    for cf in glob.glob(os.path.join(final_dir, "confirm", "*.json")):
        try:
            conf.update(json.load(open(cf)))
        except Exception:
            pass
    cstate = {}
    for f in glob.glob(os.path.join(final_dir, "confirm", "*.out")):
        cstate[os.path.basename(f)[:-4]] = int(time.time() - os.path.getmtime(f))
    try:
        _cur = json.load(open(os.path.join(ROOT, "data", "engine_deploy", "CURRENT.json")))
        cur = {"engine_md5": _cur.get("engine_md5"), "updated_utc": _cur.get("updated_utc"), "last_deploy": _cur.get("last_deploy")}
    except Exception:
        cur = {}
    print(json.dumps({"verdicts": _verdicts(final_dir), "parity": par, "confirmed": conf, "confirm_started": cstate, "current": cur}))


def parity_timeout_for(ss):
    """Venue-aware parity wall-clock: crypto scalar needs ~2400s (ALGO 63%@1458s), stocks ~1100s max (SMCI 83%@884s). 2026-10-04. Strips the _LONG/_SHORT side first (suffix check on the raw symside never matches)."""
    sym = str(ss).upper().rsplit("_", 1)[0]
    return 3600 if sym.endswith(("USDT", "USDC", "USD1")) else 1500


def host_parity(ss, final_dir, progress):
    os.makedirs(os.path.join(final_dir, "parity"), exist_ok=True)
    out = os.path.join(final_dir, "parity", f"{ss}.out")
    _to = parity_timeout_for(ss)
    cmd = f"cd {ROOT} && nice -n 12 timeout {_to} .venv/bin/python -u tools/v15_parity_check.py --sym-side {ss} --progress {shlex.quote(progress)} --window-days 30 > {shlex.quote(out)} 2>&1"
    subprocess.Popen(["bash", "-c", cmd], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("launched")


def host_confirm(ss, final_dir, progress):
    """tools/confirm_365d.confirm_symside restricted to the ONE final recipe, writing into the staging file (never the live one)."""
    os.makedirs(os.path.join(final_dir, "confirm"), exist_ok=True)
    out = os.path.join(final_dir, "confirm", f"{ss}.out")
    stage = os.path.join(final_dir, "confirm", f"{ss}.json")
    code = (
        "import json,sys;sys.path.insert(0,'.');import tools.confirm_365d as C;from pathlib import Path\n"
        f"ov=json.load(open({progress!r})).get('cumulative_overrides') or {{}}\n"
        f"C.CONFIRM_PATH=Path({stage!r})\n"
        "C._candidates_for=lambda s:[('final_20261005_both_positive',dict(ov))]\n"
        f"print(json.dumps(C.confirm_symside({ss!r},keep_existing=True)))\n"
    )
    cmd = f"cd {ROOT} && nice -n 12 timeout 1500 .venv/bin/python -u -c {shlex.quote(code)} > {shlex.quote(out)} 2>&1"  # 1500s: same heavy-sym rationale as parity leg (SMCI-class syms exceed 900s)
    subprocess.Popen(["bash", "-c", cmd], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("launched")


# ----------------------------------------------------------------------------------------------- qualification (pure function, unit-testable)
def is_crypto(ss):
    return ss.rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))


def good_window(w, floor):
    return bool(w) and bool(w.get("valid")) and (w.get("gain_pct") or 0) > 0 and int(w.get("trades") or 0) >= floor and (w.get("tim_pct") is None or float(w["tim_pct"]) <= 80) and (w.get("max_dd_pct") is None or float(w["max_dd_pct"]) <= 30)


def qualify(ss, verdict, parity, confirmed):
    """-> (status, reasons). status in QUALIFIED | NEGATIVE | UNVERIFIED | UNAVAILABLE_PARITY_NEG. Deterministic, evidence-only."""
    if not verdict:
        return "UNVERIFIED", ["no 365D chain verdict (compute did not reach it)"]
    if verdict.get("unverifiable"):
        return "UNVERIFIED", ["365D unverifiable (NPZ span < 330d)"]
    if not verdict.get("both_ok"):
        return "NEGATIVE", [f"chain not both-positive after attempt {verdict.get('attempt')}: 30D {fmt(verdict.get('w30'))} | 365D {fmt(verdict.get('w365'))}"]
    w30, w365 = verdict.get("w30") or {}, verdict.get("w365") or {}
    if not verdict.get("overrides"):
        return "UNVERIFIED", ["final overrides missing from the winning progress file"]
    why = []
    if not good_window(w30, FLOOR30):
        why.append(f"30D not good {fmt(w30)}")
    if not good_window(w365, FLOOR365):
        why.append(f"365D not good {fmt(w365)}")
    if float(verdict.get("span_365_days") or 0) < 330:
        why.append(f"365D span {verdict.get('span_365_days')}d < 330")
    if why:
        return "NEGATIVE", why
    if is_crypto(ss):
        rec = confirmed.get(ss)
        if not rec or not rec.get("valid") or float(rec.get("gain_365d") or 0) <= 0 or int(rec.get("trades") or 0) < 30:
            return "UNVERIFIED", ["crypto 365D certification record missing/invalid (confirm_365d code path)"]
    p = parity.get(ss) or {}
    try:
        lg = float(p.get("live_gain"))
    except (TypeError, ValueError):
        lg = None
    if lg is not None and lg < 0:
        return "NEGATIVE", [f"live-faithful 30D parity run is NEGATIVE ({lg}%): {p.get('reason')}"]
    if p.get("status") != "PASS":  # STRICT since PAR/001 2026-10-04 (user-ordered immediate fix): only a PASS proves vec/scalar agreement; everything else is blocked from trading
        if p.get("status") == "FAIL":
            return "NEGATIVE", [f"parity FAIL (vec/scalar diverge, unproven): {p.get('reason')}"]
        return "UNVERIFIED", [f"parity {p.get('status', 'NOT_RUN')} (no live-faithful evidence): {p.get('reason', '')}"]
    return "QUALIFIED", [f"30D {fmt(w30)} | 365D {fmt(w365)} | parity PASS {p.get('reason', '')}"[:300]]


def fmt(w):
    w = w or {}
    g = w.get("gain_pct")
    return f"{g:+.2f}%/{w.get('trades')}t/TIM{w.get('tim_pct')}/DD{w.get('max_dd_pct')}/{'valid' if w.get('valid') else 'INVALID'}" if g is not None else "n/a"


# ----------------------------------------------------------------------------------------------- s1 orchestration helpers
def rhost(h, args, timeout=900, stdin=None):
    cmd = f"cd {h['root']} && .venv/bin/python tools/v15_final_phase.py {args}"
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", h["ssh"][0], cmd], capture_output=True, text=True, timeout=timeout, input=stdin)
    return r.returncode, r.stdout.strip(), r.stderr.strip()[-300:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["host-scan", "host-collect", "host-parity", "host-confirm"])
    ap.add_argument("--ss")
    ap.add_argument("--final-dir", default=os.path.join(HOME, FINAL_DIRNAME))
    ap.add_argument("--progress")
    a = ap.parse_args()
    if a.mode == "host-scan":
        host_scan()
    elif a.mode == "host-collect":
        host_collect(a.final_dir)
    elif a.mode == "host-parity":
        host_parity(a.ss, a.final_dir, a.progress)
    elif a.mode == "host-confirm":
        host_confirm(a.ss, a.final_dir, a.progress)


if __name__ == "__main__":
    main()
