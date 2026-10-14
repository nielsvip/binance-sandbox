#!/usr/bin/env python3
"""V17 KPI + REGRESSION GUARD + BOUNDED AUTO-TUNER (BIBLE "V17 KPI + AUTO-TUNE (2026-10-14)").

USER 2026-10-14: "make sure the results keep improving and processing times per sym_side keep reducing by the self learning
system". Goals: 10x bh for every sym_side; >=12 then >30 boards/hour/server on S1 + s2.

Stdlib only (S1 python 3.12, s2 python 3.10). Every number is copied/derived from progress JSONs (result_* stamps, v17 phase
book, best_start, final_365d, best_seen) and delta logs (per-eval `secs`, `cached`) — nothing estimated except the flagged
`core_min_est` = wall x workers (the pilot does not record CPU; `eval_core_min` = sum of the delta log's non-cached eval
seconds is the MEASURED lower bound). No Sharpe anywhere (not a Sharpe surface; DIAGNOSTIC aggregates).

Modes:
  (default) collect   S1 cron every 30 min: extract finished boards from S1 (local) + s2 (ssh, this file piped to python3 -)
                      -> data/kpi/v17_kpi.sqlite (upsert, key host|sym_side|result_done_utc) -> rollups -> regression guard
                      -> data/kpi/v17_kpi_latest.md/.json (+ data/alerts/V17_KPI_REGRESSION.json on a regression).
  --extract           print board records (JSON) for this host's progress dirs (used remotely via ssh stdin).
  --autotune          daily 14:30Z (after the 13:40Z daily learn): lift per core-minute per phase per tier over the last
                      2-3 days -> data/kpi/v17_autotune.json (bounded +-20%/day, hard min/max per knob) + history.
  --rollback          restore the previous day's autotune snapshot (one command).
  --disable           write apply=false (dispatch exports nothing; pilot defaults).
Reader hook: tools/v15_dispatch_job.py `_autotune_exports()` prefixes the remote launch command with `export K=V;` for the
whitelisted knobs of an apply=true file <= 3 days old (kill switch V17_AUTOTUNE_APPLY=0 in the scheduler env)."""
import argparse, fcntl, glob, json, math, os, re, socket, sqlite3, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KPI_DIR = Path(os.environ.get("V17_KPI_DIR") or ROOT / "data" / "kpi")
ALERT_DIR = Path(os.environ.get("V17_KPI_ALERT_DIR") or ROOT / "data" / "alerts")
DB_PATH = KPI_DIR / "v17_kpi.sqlite"
AUTOTUNE = KPI_DIR / "v17_autotune.json"
AT_HIST_DIR = KPI_DIR / "autotune_history"
AT_HIST_LOG = KPI_DIR / "v17_autotune_history.jsonl"
DISPATCH_LOG = Path(os.environ.get("V15_DISPATCH_LOG", "/tmp/v15_dispatch.log"))
DEFAULT_GLOBS = "~/v15_run*/progress"
DEFAULT_HOSTS = "s1=local,s2=10.0.0.4"
TIERS = ("WINNER", "MEDIOCRE", "LOSER", "LOST_CAUSE")
P3_SHARE_FAMS = ("combo", "surgeon", "focus", "ablation", "retest")
P3_FAMS = P3_SHARE_FAMS + ("probe_p3",)
MIN_LIFT_PM = 0.3  # the "0.3 rule" (V15_V17_MIN_LIFT_PM) — READ ONLY, the tuner never writes it
STEP = 0.20  # max relative change per knob per day
# knob -> default (pilot 404a3881 v15_pilot.py), hard bounds, family, tier group. Laws are NOT knobs: V15_V17_MIN_LIFT_PM,
# V15_V17_P3_MAX_S (absolute cap), best-start, 365D record-only, typed values, never-lower are never written.
KNOBS = {
    "V15_V17_COMBO_SHARE_WINNER": {"default": 0.5, "lo": 0.15, "hi": 0.7, "kind": "share", "fam": "combo", "tiers": ("WINNER",)},
    "V15_V17_COMBO_SHARE": {"default": 0.35, "lo": 0.1, "hi": 0.6, "kind": "share", "fam": "combo", "tiers": ("MEDIOCRE", "LOSER", "LOST_CAUSE")},
    "V15_V17_SHARE_SURGEON_WINNER": {"default": 0.75, "lo": 0.3, "hi": 0.9, "kind": "share", "fam": "surgeon", "tiers": ("WINNER",)},
    "V15_V17_SHARE_SURGEON": {"default": 0.6, "lo": 0.2, "hi": 0.85, "kind": "share", "fam": "surgeon", "tiers": ("MEDIOCRE", "LOSER", "LOST_CAUSE")},
    "V15_V17_SHARE_FOCUS": {"default": 0.5, "lo": 0.1, "hi": 0.8, "kind": "share", "fam": "focus", "tiers": TIERS},
    "V15_V17_SHARE_ABLATION": {"default": 0.6, "lo": 0.15, "hi": 0.85, "kind": "share", "fam": "ablation", "tiers": TIERS},
    "V15_V17_SHARE_RETEST": {"default": 0.7, "lo": 0.2, "hi": 0.9, "kind": "share", "fam": "retest", "tiers": TIERS},
    "V15_V17_P3_MAX_S_WINNER": {"default": 1500.0, "lo": 600.0, "hi": 3600.0, "kind": "budget", "tiers": ("WINNER",)},
    "V15_V17_P3_MAX_S_MEDIOCRE": {"default": 540.0, "lo": 180.0, "hi": 1500.0, "kind": "budget", "tiers": ("MEDIOCRE",)},
    "V15_V17_P3_MAX_S_LOSER": {"default": 300.0, "lo": 90.0, "hi": 900.0, "kind": "budget", "tiers": ("LOSER",)},
    "V15_V17_P3_MAX_S_LOST_CAUSE": {"default": 120.0, "lo": 30.0, "hi": 300.0, "kind": "budget", "tiers": ("LOST_CAUSE",)},
    "V15_V17_P_MIN": {"default": 0.005, "lo": 0.001, "hi": 0.02, "kind": "pmin", "tiers": TIERS},
}
MIN_BOARDS_KNOB = int(os.environ.get("V17_AT_MIN_BOARDS", "3"))
MIN_CORE_MIN_KNOB = float(os.environ.get("V17_AT_MIN_CORE_MIN", "10"))
MIN_BOARDS_WINDOW = int(os.environ.get("V17_AT_MIN_WINDOW_BOARDS", "8"))


def _now():
    return time.time()


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def parse_ts(s):
    """ISO (with Z / +00:00 / microseconds) -> epoch seconds UTC; None on failure. Python 3.10-safe."""
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?", s.strip())
    if not m:
        return None
    import calendar
    y, mo, d, h, mi, se = (int(x) for x in m.groups()[:6])
    frac = float(m.group(7)) if m.group(7) else 0.0
    return calendar.timegm((y, mo, d, h, mi, se, 0, 0, 0)) + frac


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def cat_side_of(ss):
    sym, _, side = str(ss).rpartition("_")
    return ("CRYPTO" if sym.endswith(("USDT", "USDC")) else "STOCKS") + "_" + side


def phase_family(name):
    n = str(name).upper()
    if n.startswith("P2"):
        return "p2"
    if n.startswith("P1_PROBE"):
        return "probe"
    if n.startswith("P3_PROBE"):
        return "probe_p3"
    for k, fam in (("P3_COMBO", "combo"), ("P3_SURGEON", "surgeon"), ("P3_REGIME365", "regime365"), ("P3_ABLATION", "ablation"), ("P3_RETEST", "retest"), ("P3_FOCUS", "focus"), ("P4_FFR", "ffr"), ("P4_POST_ENDGAME_DIAG", "endgame_diag")):
        if n.startswith(k):
            return fam
    return re.sub(r"_\d+$", "", n).lower()


# ------------------------------------------------------------------------------------------------------------ extraction
def delta_log_stats(dl_files, start_ts, done_ts, final_gain):
    """non-cached evals + their measured seconds inside [start, done]; TIM of the last row equal to the final gain."""
    out = {"dl_rows": 0, "evals": 0, "evals_cached": 0, "eval_secs": 0.0, "tim_at_final": None, "dl_found": bool(dl_files)}
    if not dl_files or start_ts is None or done_ts is None:
        return out
    lo, hi = _iso(start_ts - 1), _iso(done_ts + 1)
    for f in dl_files:
        try:
            fh = open(f, "r", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if line.startswith('{"ts": "'):
                    ts = line[8:28]
                    if ts < lo or ts > hi:
                        continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ts = str(r.get("ts") or "")
                if ts < lo or ts > hi or r.get("label") == "ROW_DONE":
                    continue
                out["dl_rows"] += 1
                if r.get("cached"):
                    out["evals_cached"] += 1
                elif r.get("secs") is not None and r.get("gain_pct") is not None:
                    out["evals"] += 1
                    out["eval_secs"] += float(_num(r.get("secs")) or 0.0)
                g = _num(r.get("gain_pct"))
                if final_gain is not None and g is not None and abs(g - final_gain) < 1e-6 and r.get("tim") is not None:
                    out["tim_at_final"] = _num(r.get("tim"))
    out["eval_secs"] = round(out["eval_secs"], 2)
    return out


def board_record(d, path, host, workers, workers_src):
    """one finished board -> flat KPI record (None when the board has no result stamps)."""
    ss = d.get("symside") or Path(path).name.split("_v14_progress.json")[0]
    t0, t1 = parse_ts(d.get("result_start_utc")), parse_ts(d.get("result_done_utc"))
    if t0 is None or t1 is None or t1 < t0:
        return None
    v = d.get("v17") if isinstance(d.get("v17"), dict) else {}
    tier = (d.get("tier") or {}).get("tier") if isinstance(d.get("tier"), dict) else None
    final = _num(d.get("final_gain_fresh_vec"))
    if final is None:
        final = _num(d.get("final_gain"))
    if final is None:
        final = _num(d.get("cumulative_gain"))
    bh = _num(d.get("bh"))
    bs = d.get("best_start") if isinstance(d.get("best_start"), dict) else {}
    bs_gain = _num(bs.get("gain"))
    if bs_gain is None:
        bs_gain = _num(d.get("initial_baseline_gain"))
    seen = d.get("best_seen") if isinstance(d.get("best_seen"), dict) else {}
    tim = dd = None
    if final is not None and _num(seen.get("gain")) is not None and abs(_num(seen.get("gain")) - final) < 1e-6:
        tim, dd = _num(seen.get("tim")), _num(seen.get("dd"))
    if dd is None:
        for pr in reversed(v.get("promotions") or []):
            if isinstance(pr, dict) and final is not None and _num(pr.get("after")) is not None and abs(_num(pr["after"]) - final) < 1e-6:
                tim = tim if tim is not None else _num(pr.get("tim"))
                dd = _num(pr.get("dd"))
                break
    pdir = Path(path).parent
    dl = delta_log_stats(sorted(glob.glob(str(pdir / "v15_delta_log" / f"{ss}_*.jsonl"))), t0, t1, final)
    if tim is None:
        tim = dl["tim_at_final"]
    phases, flags = {}, []
    p3_starts = []
    first_p3_entry = None
    for name, x in (v.get("phases") or {}).items():
        if not isinstance(x, dict):
            continue
        fam = phase_family(name)
        e = phases.setdefault(fam, {"wall_s": 0.0, "lift": 0.0, "evals": 0, "promotions": 0, "n": 0})
        w = float(_num(x.get("wall_s")) or 0.0)
        e["wall_s"] += w
        e["lift"] += float(_num(x.get("lift")) or 0.0)
        e["evals"] += int(_num(x.get("evals")) or 0)
        e["promotions"] += int(_num(x.get("promotions")) or 0)
        e["n"] += 1
        if str(name).upper().startswith("P3_"):
            at = parse_ts(x.get("at"))
            if at is not None:
                p3_starts.append((at - w, _num(x.get("entry_gain"))))
    if p3_starts:
        p3_starts.sort()
        first_p3_entry = p3_starts[0][1]
    p0 = float(_num(v.get("p0_wall_s")) or 0.0)
    if p0 > 0:
        phases["p0"] = {"wall_s": p0, "lift": 0.0, "evals": 0, "promotions": 0, "n": 1}
    v_start = parse_ts(v.get("started_utc"))
    if v.get("on") and "p2" not in phases and v_start is not None:
        if p3_starts:
            p2_wall = max(0.0, p3_starts[0][0] - v_start)
            p2_lift = (first_p3_entry - bs_gain) if (first_p3_entry is not None and bs_gain is not None) else 0.0
        else:
            p2_wall = max(0.0, (t1 - t0) - sum(e["wall_s"] for e in phases.values()))
            p2_lift = (final - bs_gain) if (final is not None and bs_gain is not None) else 0.0
        phases["p2"] = {"wall_s": round(p2_wall, 1), "lift": round(p2_lift, 4), "evals": 0, "promotions": 0, "n": 1, "derived": True}
        flags.append("p2_derived")
    wall = t1 - t0
    accounted = sum(e["wall_s"] for e in phases.values())
    if v.get("on"):
        phases["other"] = {"wall_s": round(max(0.0, wall - accounted), 1), "lift": 0.0, "evals": 0, "promotions": 0, "n": 1, "derived": True}
    for e in phases.values():
        e["wall_s"] = round(e["wall_s"], 1)
        e["lift"] = round(e["lift"], 4)
    flags.append("cpu_est_wall_x_workers")
    if workers_src != "dispatch_log":
        flags.append("workers_assumed")
    if not dl["dl_found"]:
        flags.append("no_delta_log")
    f365 = d.get("final_365d") if isinstance(d.get("final_365d"), dict) else {}
    below = bool(final is not None and bs_gain is not None and final < bs_gain - 1e-6)
    return {
        "key": f"{host}|{ss}|{d.get('result_done_utc')}", "sym_side": ss, "cat_side": cat_side_of(ss), "tier": tier, "host": host,
        "run": pdir.parent.name, "pilot_md5": d.get("result_pilot_md5"), "engine_md5": d.get("result_engine_md5"), "config_md5": d.get("result_config_md5"),
        "start_utc": _iso(t0), "done_utc": _iso(t1), "start_ts": t0, "done_ts": t1, "wall_s": round(wall, 1), "workers": workers, "workers_src": workers_src,
        "core_min_est": round(wall * workers / 60.0, 2), "eval_core_min": round(dl["eval_secs"] / 60.0, 2), "evals": dl["evals"], "evals_cached": dl["evals_cached"],
        "final_gain": final, "bh": bh, "gain_bh": (final / bh) if (final is not None and bh is not None and bh > 0) else None,
        "trades": d.get("final_trades") if d.get("final_trades") is not None else d.get("run_trades"), "tim": tim, "dd": dd,
        "g365": _num(f365.get("gain_pct")), "dd365": _num(f365.get("max_dd_pct")), "tim365": _num(f365.get("tim_pct")), "trades365": f365.get("trades"), "valid365": f365.get("valid"),
        "best_start": bs.get("chosen"), "best_start_gain": bs_gain, "final_below_best_start": below, "v17_on": bool(v.get("on")),
        "phases": phases, "flags": flags, "run_class": d.get("run_class"),
    }


def dispatch_workers(log_path=DISPATCH_LOG):
    """{(host_label, SYMBOL): [(ts, workers)]} from the dispatch log's launched rows that carry `workers`."""
    out = {}
    try:
        lines = Path(log_path).read_text(errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("event") != "launched" or not r.get("workers"):
            continue
        tag = str(r.get("tag") or "")
        hl = tag.split(":", 1)[0] if ":" in tag else ""
        ts = parse_ts(r.get("ts"))
        if ts is not None:
            out.setdefault((hl, str(r.get("symbol") or "").upper()), []).append((ts, int(r["workers"])))
    return out


def _workers_for(wmap, host, ss, start_ts, default):
    sym = ss.rpartition("_")[0].upper()
    best = None
    for ts, w in wmap.get((host, sym), []):
        if ts <= start_ts + 600 and (best is None or ts > best[0]):
            best = (ts, w)
    return (best[1], "dispatch_log") if best else (default, "assumed")


def extract(globs, host, since_ts, workers_default, wmap=None):
    """board records for every finished progress JSON (incl. .npzprev_ archives) under the globs, done >= since_ts."""
    wmap = wmap or {}
    recs, seen, errs = [], set(), 0
    files = set()
    for g in globs:
        base = os.path.expanduser(g)
        files.update(glob.glob(os.path.join(base, "*_v14_progress.json")))
        files.update(glob.glob(os.path.join(base, "*_v14_progress.json.npzprev_*.json")))
    for f in sorted(files):
        try:
            if os.path.getmtime(f) < since_ts:
                continue
            d = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            errs += 1
            continue
        if not isinstance(d, dict) or not d.get("result_done_utc"):
            continue
        t1 = parse_ts(d.get("result_done_utc"))
        if t1 is None or t1 < since_ts:
            continue
        ss = d.get("symside") or Path(f).name.split("_v14_progress.json")[0]
        w, src = _workers_for(wmap, host, ss, parse_ts(d.get("result_start_utc")) or t1, workers_default)
        r = board_record(d, f, host, w, src)
        if r and r["key"] not in seen:
            seen.add(r["key"])
            recs.append(r)
    return {"host": host, "hostname": socket.gethostname(), "boards": recs, "read_errors": errs, "n_files": len(files)}


def extract_remote(ssh_target, host, globs, since_ts, workers_default, wmap, timeout=900):
    """pipe THIS file to `python3 -` on the target (no deployment skew), parse its JSON."""
    args = f"--extract --host-label {host} --since {since_ts} --workers-default {workers_default} --globs '{','.join(globs)}'"
    wm = {f"{k[0]}|{k[1]}": v for k, v in wmap.items() if k[0] == host}
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_target, f"V17_KPI_WMAP='{json.dumps(wm)}' nice -n 15 python3 - {args}"]
    try:
        r = subprocess.run(cmd, input=Path(__file__).read_text(), capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            return json.loads(r.stdout)
        return {"host": host, "error": f"rc={r.returncode} {r.stderr[-300:]}", "boards": []}
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        return {"host": host, "error": str(e)[:300], "boards": []}


# --------------------------------------------------------------------------------------------------------------- sqlite
COLS = ["key", "sym_side", "cat_side", "tier", "host", "run", "pilot_md5", "engine_md5", "config_md5", "start_utc", "done_utc", "start_ts", "done_ts", "wall_s", "workers", "workers_src", "core_min_est", "eval_core_min", "evals", "evals_cached", "final_gain", "bh", "gain_bh", "trades", "tim", "dd", "g365", "dd365", "tim365", "trades365", "valid365", "best_start", "best_start_gain", "final_below_best_start", "v17_on", "phases_json", "flags", "run_class", "ingested_utc"]


def db_open(path=None):
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=60)
    con.execute(f"CREATE TABLE IF NOT EXISTS boards ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'key' else '') for c in COLS)})")
    con.execute("CREATE INDEX IF NOT EXISTS boards_done ON boards(done_ts)")
    con.execute("CREATE TABLE IF NOT EXISTS runs (ts TEXT, hosts_json TEXT, n_new INTEGER, n_seen INTEGER)")
    return con


def db_upsert(con, recs):
    n_new = 0
    for r in recs:
        row = dict(r)
        row["phases_json"] = json.dumps(row.pop("phases", {}), sort_keys=True)
        row["flags"] = ",".join(row.get("flags") or [])
        row["ingested_utc"] = _iso(_now())
        for b in ("final_below_best_start", "v17_on", "valid365"):
            row[b] = None if row.get(b) is None else int(bool(row[b]))
        exists = con.execute("SELECT 1 FROM boards WHERE key=?", (row["key"],)).fetchone()
        n_new += 0 if exists else 1
        con.execute(f"INSERT OR REPLACE INTO boards ({', '.join(COLS)}) VALUES ({', '.join('?' for _ in COLS)})", [row.get(c) for c in COLS])
    con.commit()
    return n_new


def db_boards(con, since_ts, until_ts=None):
    q = f"SELECT {', '.join(COLS)} FROM boards WHERE done_ts >= ?" + (" AND done_ts < ?" if until_ts is not None else "") + " ORDER BY done_ts"
    rows = con.execute(q, (since_ts,) if until_ts is None else (since_ts, until_ts)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(COLS, r))
        d["phases"] = json.loads(d.pop("phases_json") or "{}")
        d["flags"] = [x for x in (d.get("flags") or "").split(",") if x]
        out.append(d)
    return out


# -------------------------------------------------------------------------------------------------------------- rollups
def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def pctl(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int(math.ceil(q * len(xs))) - 1))]


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def phase_table(boards):
    """{family: {boards, pos, lift, wall_min, core_min, lift_per_core_min, lift_per_wall_min}} (core-min = phase wall x workers)."""
    out = {}
    for b in boards:
        w = b.get("workers") or 1
        for fam, e in (b.get("phases") or {}).items():
            if not e.get("wall_s") and not e.get("lift"):
                continue
            x = out.setdefault(fam, {"boards": 0, "pos": 0, "lift": 0.0, "wall_min": 0.0, "core_min": 0.0, "derived": False})
            x["boards"] += 1
            x["pos"] += 1 if (e.get("lift") or 0) > 1e-9 else 0
            x["lift"] += float(e.get("lift") or 0.0)
            x["wall_min"] += float(e.get("wall_s") or 0.0) / 60.0
            x["core_min"] += float(e.get("wall_s") or 0.0) * w / 60.0
            x["derived"] = x["derived"] or bool(e.get("derived"))
    for x in out.values():
        x["lift_per_core_min"] = _r(x["lift"] / x["core_min"], 4) if x["core_min"] > 0 else None
        x["lift_per_wall_min"] = _r(x["lift"] / x["wall_min"], 4) if x["wall_min"] > 0 else None
        x["lift"], x["wall_min"], x["core_min"] = _r(x["lift"], 3), _r(x["wall_min"], 1), _r(x["core_min"], 1)
    return out


def summarize(boards, hours):
    hosts = sorted({b["host"] for b in boards})
    gb = [b["gain_bh"] for b in boards if b.get("gain_bh") is not None]
    out = {"n": len(boards), "hours": _r(hours, 2), "hosts": {}, "median_wall_min": _r(median([b["wall_s"] / 60.0 for b in boards]), 1), "median_core_min_est": _r(median([b["core_min_est"] for b in boards]), 1), "median_eval_core_min": _r(median([b["eval_core_min"] for b in boards if "no_delta_log" not in b["flags"]]), 2), "median_gain_bh": _r(median(gb), 3), "p90_gain_bh": _r(pctl(gb, 0.9), 3), "n_bh_pos": len(gb), "n_bh_nonpos": len(boards) - len(gb), "median_final_minus_best_start": _r(median([b["final_gain"] - b["best_start_gain"] for b in boards if b.get("final_gain") is not None and b.get("best_start_gain") is not None]), 3), "n_below_best_start": sum(1 for b in boards if b.get("final_below_best_start")), "median_final": _r(median([b.get("final_gain") for b in boards]), 2), "median_g365": _r(median([b.get("g365") for b in boards]), 2), "median_dd365": _r(median([b.get("dd365") for b in boards]), 2)}
    for k in (1, 2, 5, 10):
        out[f"share_ge_{k}x_bh"] = _r(sum(1 for x in gb if x >= k) / len(gb), 3) if gb else None
    for h in hosts:
        hb = [b for b in boards if b["host"] == h]
        out["hosts"][h] = {"boards": len(hb), "boards_per_h": _r(len(hb) / hours, 2) if hours > 0 else None, "median_wall_min": _r(median([b["wall_s"] / 60.0 for b in hb]), 1), "median_core_min_est": _r(median([b["core_min_est"] for b in hb]), 1), "median_gain_bh": _r(median([b["gain_bh"] for b in hb]), 3)}
    out["tiers"] = {t: sum(1 for b in boards if b.get("tier") == t) for t in sorted({str(b.get("tier")) for b in boards})}
    out["pilot_md5"] = sorted({str(b.get("pilot_md5"))[:8] for b in boards})
    out["phases"] = phase_table([b for b in boards if b.get("v17_on")])
    out["phases_by_tier"] = {t: phase_table([b for b in boards if b.get("v17_on") and b.get("tier") == t]) for t in TIERS if any(b.get("tier") == t for b in boards)}
    return out


def rollups(con, now):
    day0 = now - (now % 86400)
    win = {"6h": (now - 6 * 3600, 6.0), "24h": (now - 86400, 24.0), "today": (day0, max(0.25, (now - day0) / 3600.0)), "3d": (now - 3 * 86400, 72.0)}
    out = {"generated_utc": _iso(now), "windows": {}}
    for k, (since, hrs) in win.items():
        out["windows"][k] = summarize(db_boards(con, since), hrs)
    hourly = []
    for i in range(47, -1, -1):
        h0 = now - (now % 3600) - i * 3600
        bs = db_boards(con, h0, h0 + 3600)
        for h in sorted({b["host"] for b in bs}):
            hb = [b for b in bs if b["host"] == h]
            hourly.append({"hour": _iso(h0)[:13] + "Z", "host": h, "boards": len(hb), "median_wall_min": _r(median([b["wall_s"] / 60.0 for b in hb]), 1), "median_core_min_est": _r(median([b["core_min_est"] for b in hb]), 1), "median_gain_bh": _r(median([b["gain_bh"] for b in hb]), 3)})
    out["hourly"] = hourly
    daily = []
    for i in range(6, -1, -1):
        d0 = day0 - i * 86400
        bs = db_boards(con, d0, d0 + 86400)
        if bs:
            s = summarize(bs, min(24.0, max(0.25, (now - d0) / 3600.0)))
            daily.append({"day": _iso(d0)[:10], **{k: s[k] for k in ("n", "median_wall_min", "median_core_min_est", "median_eval_core_min", "median_gain_bh", "p90_gain_bh", "share_ge_1x_bh", "share_ge_10x_bh", "n_below_best_start", "pilot_md5")}, "boards_per_h": {h: x["boards_per_h"] for h, x in s["hosts"].items()}})
    out["daily"] = daily
    out["n_db"] = con.execute("SELECT COUNT(*) FROM boards").fetchone()[0]
    return out


# ---------------------------------------------------------------------------------------------------- regression guard
def _cmp(cur, base, metric, rise_pct=None, fall_pct=None):
    c, b = cur.get(metric), base.get(metric)
    if c is None or b is None or b == 0:
        return None
    ch = (c - b) / abs(b)
    hit = (rise_pct is not None and ch > rise_pct) or (fall_pct is not None and ch < -fall_pct)
    return {"metric": metric, "current": c, "baseline": b, "change_pct": round(100.0 * ch, 1), "regression": bool(hit)}


def regression_check(con, now, min_n=5):
    """last 24h and last 6h vs the 3 days before each window. wall / core-min rise > 15 %, gain/bh fall > 10 %, finals below
    best-start. Evidence carries the pilot/engine md5 sets so a coinciding code change is named."""
    res = {"checked_utc": _iso(now), "windows": {}, "regression": False}
    for name, hrs in (("24h", 24.0), ("6h", 6.0)):
        cur_b = db_boards(con, now - hrs * 3600)
        base_b = db_boards(con, now - hrs * 3600 - 72 * 3600, now - hrs * 3600)
        cur, base = summarize(cur_b, hrs), summarize(base_b, 72.0)
        w = {"n_current": len(cur_b), "n_baseline": len(base_b), "checks": [], "insufficient": len(cur_b) < min_n or len(base_b) < min_n}
        below = [{"sym_side": b["sym_side"], "host": b["host"], "final": _r(b["final_gain"], 3), "best_start_gain": _r(b["best_start_gain"], 3), "pilot_md5": b.get("pilot_md5"), "done_utc": b["done_utc"]} for b in cur_b if b.get("final_below_best_start")]
        if not w["insufficient"]:
            for c in (_cmp(cur, base, "median_wall_min", rise_pct=0.15), _cmp(cur, base, "median_core_min_est", rise_pct=0.15), _cmp(cur, base, "median_eval_core_min", rise_pct=0.15), _cmp(cur, base, "median_gain_bh", fall_pct=0.10)):
                if c:
                    w["checks"].append(c)
        w["finals_below_best_start"] = below
        cur_md5 = {}
        for b in cur_b:
            cur_md5.setdefault(str(b.get("pilot_md5")), 0)
            cur_md5[str(b.get("pilot_md5"))] += 1
        base_md5 = {str(b.get("pilot_md5")) for b in base_b}
        w["pilot_md5_current"] = cur_md5
        w["pilot_md5_new_vs_baseline"] = sorted(m for m in cur_md5 if m not in base_md5)
        w["engine_md5_new_vs_baseline"] = sorted({str(b.get("engine_md5")) for b in cur_b} - {str(b.get("engine_md5")) for b in base_b})
        w["tier_mix_current"], w["tier_mix_baseline"] = cur["tiers"], base["tiers"]
        w["regression"] = any(c["regression"] for c in w["checks"]) or bool(below)
        res["windows"][name] = w
        res["regression"] = res["regression"] or w["regression"]
    return res


def write_alert(reg, alert_dir=None):
    ad = Path(alert_dir or ALERT_DIR)
    ad.mkdir(parents=True, exist_ok=True)
    p = ad / "V17_KPI_REGRESSION.json"
    if reg.get("regression"):
        _write_atomic(p, json.dumps(reg, indent=1, default=str))
        return str(p)
    if p.exists():  # cleared: keep the evidence, never silently delete it
        p.replace(ad / f"V17_KPI_REGRESSION.resolved_{time.strftime('%Y%m%dT%H%MZ', time.gmtime())}.json")
    return None


def _write_atomic(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, p)


# ------------------------------------------------------------------------------------------------------------ auto-tuner
def load_autotune(path=None):
    try:
        d = json.loads(Path(path or AUTOTUNE).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def autotune(boards, prev_doc=None, now=None, days=3.0):
    """bounded reallocation toward phases with higher lift per core-minute. Returns the autotune doc (not written)."""
    now = now or _now()
    prev_knobs = ((prev_doc or {}).get("knobs") or {}) if (prev_doc or {}).get("apply", True) else {}
    v17 = [b for b in boards if b.get("v17_on")]
    doc = {"generated_utc": _iso(now), "apply": True, "window_days": days, "n_boards": len(v17), "step_max_pct": int(STEP * 100), "min_lift_pm_rule": MIN_LIFT_PM, "laws_untouched": ["V15_V17_MIN_LIFT_PM", "V15_V17_P3_MAX_S", "best-start", "365D record-only", "typed values", "never-lower"], "knobs": {}, "env": {}}
    enough = len(v17) >= MIN_BOARDS_WINDOW
    if not enough:
        doc["note"] = f"only {len(v17)} v17 boards in {days} d (< {MIN_BOARDS_WINDOW}) — every knob held"
    tables = {}

    def tbl(tiers):
        k = tuple(tiers)
        if k not in tables:
            tables[k] = phase_table([b for b in v17 if b.get("tier") in tiers])
        return tables[k]

    def nboards(tiers, fams):
        return sum(1 for b in v17 if b.get("tier") in tiers and any((b["phases"].get(f) or {}).get("wall_s") for f in fams))

    for knob, spec in KNOBS.items():
        prev = _num((prev_knobs.get(knob) or {}).get("value")) if isinstance(prev_knobs.get(knob), dict) else None
        prev = spec["default"] if prev is None else prev
        new, why, ev = prev, "hold", {}
        if enough and spec["kind"] == "share":
            t = tbl(spec["tiers"])
            f = t.get(spec["fam"]) or {}
            pool_l = sum((t.get(x) or {}).get("lift") or 0.0 for x in P3_SHARE_FAMS)
            pool_c = sum((t.get(x) or {}).get("core_min") or 0.0 for x in P3_SHARE_FAMS)
            nb = nboards(spec["tiers"], (spec["fam"],))
            ev = {"tiers": list(spec["tiers"]), "boards_with_phase": nb, "phase_lift": f.get("lift"), "phase_core_min": f.get("core_min"), "phase_lift_per_core_min": f.get("lift_per_core_min"), "p3_pool_lift": _r(pool_l, 3), "p3_pool_core_min": _r(pool_c, 1), "p3_pool_lift_per_core_min": _r(pool_l / pool_c, 4) if pool_c > 0 else None}
            if nb < MIN_BOARDS_KNOB or (f.get("core_min") or 0) < MIN_CORE_MIN_KNOB:
                why = f"hold: evidence {nb} boards / {f.get('core_min') or 0} core-min < {MIN_BOARDS_KNOB} / {MIN_CORE_MIN_KNOB}"
            elif pool_l <= 1e-9 or pool_c <= 0:
                why = "hold: no P3 lift in the window (nothing to reallocate toward)"
            else:
                ratio = ((f.get("lift") or 0.0) / f["core_min"]) / (pool_l / pool_c)
                ev["ratio_vs_pool"] = _r(ratio, 3)
                if 0.8 <= ratio <= 1.25:
                    why = f"hold: phase lift/core-min {ratio:.2f}x the P3 pool (deadband 0.8-1.25)"
                else:
                    fac = _clamp(math.sqrt(ratio) if ratio > 0 else 1.0 - STEP, 1.0 - STEP, 1.0 + STEP)
                    new = _clamp(round(prev * fac, 4), spec["lo"], spec["hi"])
                    why = f"{'raise' if new > prev else 'cut'}: phase lift/core-min {ratio:.2f}x the P3 pool -> x{fac:.2f} (bounded +-{int(STEP * 100)}%, [{spec['lo']}, {spec['hi']}])"
        elif enough and spec["kind"] == "budget":
            tier = spec["tiers"][0]
            t = tbl((tier,))
            l = sum((t.get(x) or {}).get("lift") or 0.0 for x in P3_FAMS)
            wm = sum((t.get(x) or {}).get("wall_min") or 0.0 for x in P3_FAMS)
            cm = sum((t.get(x) or {}).get("core_min") or 0.0 for x in P3_FAMS)
            nb = nboards((tier,), P3_FAMS)
            rate = (l / wm) if wm > 0 else None
            ev = {"tier": tier, "boards_with_p3": nb, "p3_lift": _r(l, 3), "p3_wall_min": _r(wm, 1), "p3_core_min": _r(cm, 1), "p3_lift_per_wall_min": _r(rate, 4), "p3_lift_per_core_min": _r(l / cm, 4) if cm > 0 else None}
            if nb < MIN_BOARDS_KNOB or cm < MIN_CORE_MIN_KNOB:
                why = f"hold: evidence {nb} boards / {_r(cm, 1)} core-min < {MIN_BOARDS_KNOB} / {MIN_CORE_MIN_KNOB}"
            elif rate >= 2 * MIN_LIFT_PM:
                new = _clamp(round(prev * (1 + STEP), 1), spec["lo"], spec["hi"])
                why = f"raise: tier P3 lift {rate:.3f} pp/wall-min >= 2x the {MIN_LIFT_PM} rule -> +{int(STEP * 100)}%"
            elif rate < MIN_LIFT_PM:
                new = _clamp(round(prev * (1 - STEP), 1), spec["lo"], spec["hi"])
                why = f"cut: tier P3 lift {rate:.3f} pp/wall-min < the {MIN_LIFT_PM} rule -> -{int(STEP * 100)}% initial budget (the 0.3 extension still extends any lifting window)"
            else:
                why = f"hold: tier P3 lift {rate:.3f} pp/wall-min within [{MIN_LIFT_PM}, {2 * MIN_LIFT_PM})"
        elif enough and spec["kind"] == "pmin":
            t = tbl(TIERS)
            p2 = t.get("p2") or {}
            pool_l = sum((t.get(x) or {}).get("lift") or 0.0 for x in P3_FAMS)
            pool_c = sum((t.get(x) or {}).get("core_min") or 0.0 for x in P3_FAMS)
            nb = nboards(TIERS, ("p2",))
            ev = {"boards_with_p2": nb, "p2_lift": p2.get("lift"), "p2_core_min": p2.get("core_min"), "p2_lift_per_core_min": p2.get("lift_per_core_min"), "p2_derived": p2.get("derived"), "p3_lift_per_core_min": _r(pool_l / pool_c, 4) if pool_c > 0 else None}
            if nb < max(5, MIN_BOARDS_KNOB) or (p2.get("core_min") or 0) < MIN_CORE_MIN_KNOB or pool_c <= 0:
                why = "hold: evidence too thin for the yellow p_eval floor"
            else:
                p2r, p3r = (p2.get("lift") or 0.0) / p2["core_min"], pool_l / pool_c
                if p3r > 0 and p2r < 0.5 * p3r:
                    new = _clamp(round(prev * (1 - STEP), 5), spec["lo"], spec["hi"])
                    why = f"cut: P2 lift/core-min {p2r:.4f} < 0.5x P3 {p3r:.4f} -> fewer sampled yellows (-{int(STEP * 100)}%)"
                elif p2r > 1.5 * max(p3r, 1e-9):
                    new = _clamp(round(prev * (1 + STEP), 5), spec["lo"], spec["hi"])
                    why = f"raise: P2 lift/core-min {p2r:.4f} > 1.5x P3 {p3r:.4f} -> more sampled yellows (+{int(STEP * 100)}%)"
                else:
                    why = f"hold: P2 {p2r:.4f} vs P3 {p3r:.4f} lift/core-min within [0.5x, 1.5x]"
        # hard guard: never more than STEP away from prev, never outside bounds
        new = _clamp(_clamp(new, prev * (1 - STEP) - 1e-12, prev * (1 + STEP) + 1e-12), spec["lo"], spec["hi"])
        doc["knobs"][knob] = {"value": new, "prev": prev, "default": spec["default"], "bounds": [spec["lo"], spec["hi"]], "why": why, "evidence": ev}
        doc["env"][knob] = new
    return doc


def save_autotune(doc, path=None, hist_dir=None, hist_log=None, event="autotune"):
    path, hist_dir, hist_log = Path(path or AUTOTUNE), Path(hist_dir or AT_HIST_DIR), Path(hist_log or AT_HIST_LOG)
    hist_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(doc, indent=1, default=str))
    snap = hist_dir / f"v17_autotune_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    _write_atomic(snap, json.dumps(doc, indent=1, default=str))
    with open(hist_log, "a") as fh:
        fh.write(json.dumps({"ts": _iso(_now()), "event": event, "snapshot": snap.name, "apply": doc.get("apply"), "env": doc.get("env")}, default=str) + "\n")
    return str(snap)


def rollback(path=None, hist_dir=None, hist_log=None):
    """restore the snapshot BEFORE the one currently in effect (one command: python3 tools/v17_kpi.py --rollback)."""
    path, hist_dir = Path(path or AUTOTUNE), Path(hist_dir or AT_HIST_DIR)
    cur = load_autotune(path)
    snaps = sorted(hist_dir.glob("v17_autotune_*.json"))
    prior = [s for s in snaps if str(load_autotune(s).get("generated_utc", "")) < str(cur.get("generated_utc", "~")) and not load_autotune(s).get("rolled_back_from")]
    if not prior:
        doc = {"generated_utc": _iso(_now()), "apply": False, "knobs": {}, "env": {}, "rolled_back_from": cur.get("generated_utc"), "note": "no earlier snapshot — apply=false (pilot defaults)"}
    else:
        doc = load_autotune(prior[-1])
        doc["rolled_back_from"] = cur.get("generated_utc")
        doc["rollback_utc"] = _iso(_now())
    save_autotune(doc, path, hist_dir, hist_log, event="rollback")
    return doc


# --------------------------------------------------------------------------------------------------------------- report
def _tbl(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


PH_ORDER = ["p0", "probe", "p2", "combo", "surgeon", "focus", "ablation", "retest", "probe_p3", "regime365", "ffr", "endgame_diag", "other"]


def report_md(roll, reg, at_doc, hosts_status):
    L = [f"# V17 KPI — {roll['generated_utc']}", "", "Source: progress JSONs (result_* stamps, v17 phase book, best_start, final_365d) + delta logs of S1 + s2. DIAGNOSTIC aggregates, no Sharpe. "
         "`core_min_est` = wall x pilot --workers (the pilot records no CPU; flagged `cpu_est_wall_x_workers`); `eval_core_min` = measured sum of non-cached eval seconds from the delta log (lower bound). "
         "gain/bh only for bh > 0. Phase lift = gain points added inside the phase; `p2` on legacy-P2 boards = first P3 entry gain - best_start gain (derived); `other` = wall not covered by a recorded phase.", "",
         "Hosts: " + ", ".join(f"{h}: {s}" for h, s in hosts_status.items()) + f" · boards in DB: {roll['n_db']}", "", "## 1. Throughput + quality by window (targets: >=12 then >30 boards/h/server; 10x bh every sym_side)"]
    rows = []
    for w, s in roll["windows"].items():
        bph = ", ".join(f"{h} {x['boards_per_h']}" for h, x in s["hosts"].items()) or "-"
        rows.append([w, s["n"], bph, s["median_wall_min"], s["median_core_min_est"], s["median_eval_core_min"], s["median_gain_bh"], s["p90_gain_bh"], f"{s['n_bh_pos']}/{s['n']}", s["share_ge_1x_bh"], s["share_ge_2x_bh"], s["share_ge_5x_bh"], s["share_ge_10x_bh"], s["median_final_minus_best_start"], s["n_below_best_start"], s["median_g365"], s["median_dd365"]])
    L.append(_tbl(["window", "boards", "boards/h/server", "med wall min", "med core-min est", "med eval core-min", "med gain/bh", "p90 gain/bh", "bh>0", ">=1x", ">=2x", ">=5x", ">=10x", "med final-best_start", "final<best_start", "med 365D gain", "med 365D DD"], rows))
    for w in ("24h", "3d"):
        s = roll["windows"].get(w) or {}
        ph = s.get("phases") or {}
        if not ph:
            continue
        L.append(f"\n## 2.{w} lift per phase (v17 boards; core-min = phase wall x workers)")
        L.append(_tbl(["phase", "boards", "lift>0", "sum lift pp", "mean lift", "wall min", "core-min", "lift/core-min", "lift/wall-min", "derived"], [[p, x["boards"], x["pos"], x["lift"], _r((x["lift"] or 0) / max(1, x["boards"]), 3), x["wall_min"], x["core_min"], x["lift_per_core_min"], x["lift_per_wall_min"], "yes" if x["derived"] else ""] for p, x in sorted(ph.items(), key=lambda kv: PH_ORDER.index(kv[0]) if kv[0] in PH_ORDER else 99)]))
    s3 = roll["windows"].get("3d") or {}
    if s3.get("phases_by_tier"):
        fams = [p for p in PH_ORDER if any(p in t for t in s3["phases_by_tier"].values())]
        L.append("\n## 3. lift/core-min per phase per tier (3d)")
        L.append(_tbl(["tier", "boards"] + fams, [[t, s3["tiers"].get(t)] + [(tb.get(p) or {}).get("lift_per_core_min") for p in fams] for t, tb in s3["phases_by_tier"].items()]))
    if roll.get("hourly"):
        L.append("\n## 4. hourly (last 24 h)")
        L.append(_tbl(["hour", "host", "boards", "med wall min", "med core-min est", "med gain/bh"], [[h["hour"], h["host"], h["boards"], h["median_wall_min"], h["median_core_min_est"], h["median_gain_bh"]] for h in roll["hourly"][-48:] if h["hour"] >= _iso(parse_ts(roll["generated_utc"]) - 86400)[:13]]))
    if roll.get("daily"):
        L.append("\n## 5. daily")
        L.append(_tbl(["day", "boards", "boards/h/server", "med wall min", "med core-min est", "med eval core-min", "med gain/bh", "p90", ">=1x", ">=10x", "final<best_start", "pilot md5"], [[d["day"], d["n"], ", ".join(f"{h} {v}" for h, v in d["boards_per_h"].items()), d["median_wall_min"], d["median_core_min_est"], d["median_eval_core_min"], d["median_gain_bh"], d["p90_gain_bh"], d["share_ge_1x_bh"], d["share_ge_10x_bh"], d["n_below_best_start"], " ".join(d["pilot_md5"])] for d in roll["daily"]]))
    L.append(f"\n## 6. regression guard: {'REGRESSION' if reg.get('regression') else 'ok'}")
    for w, x in (reg.get("windows") or {}).items():
        hits = [f"{c['metric']} {c['baseline']}->{c['current']} ({c['change_pct']:+}%)" + (" **REGRESSION**" if c["regression"] else "") for c in x["checks"]]
        L.append(f"- {w}: n {x['n_current']} vs 3d-before {x['n_baseline']}{' (insufficient sample, no verdict)' if x['insufficient'] else ''}; " + ("; ".join(hits) or "no comparable metrics") + f"; finals<best_start {len(x['finals_below_best_start'])}; new pilot md5 {', '.join(m[:8] for m in x['pilot_md5_new_vs_baseline']) or '-'}")
    if at_doc:
        L.append(f"\n## 7. autotune in effect ({at_doc.get('generated_utc')}, apply={at_doc.get('apply')})")
        L.append(_tbl(["knob", "value", "prev", "why"], [[k, v.get("value"), v.get("prev"), str(v.get("why"))[:150]] for k, v in (at_doc.get("knobs") or {}).items()]))
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------------------------------ CLI
def _lock(name):
    f = open(f"/tmp/{name}.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def collect(hosts_spec, globs, days, workers_default, now=None, db=None):
    now = now or _now()
    since = now - days * 86400
    wmap = dispatch_workers()
    results, status = [], {}
    for part in [p.strip() for p in hosts_spec.split(",") if p.strip()]:
        label, _, target = part.partition("=")
        if target in ("", "local"):
            r = extract(globs, label, since, workers_default, wmap)
        else:
            r = extract_remote(target, label, globs, since, workers_default, wmap)
        status[label] = r.get("error") or f"{len(r.get('boards') or [])} boards/{r.get('n_files')} files"
        results.extend(r.get("boards") or [])
    con = db_open(db)
    n_new = db_upsert(con, results)
    con.execute("INSERT INTO runs VALUES (?,?,?,?)", (_iso(now), json.dumps(status), n_new, len(results)))
    con.commit()
    return con, status, n_new, len(results)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--autotune", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--disable", action="store_true")
    ap.add_argument("--force", action="store_true", help="autotune: rerun a day that already has a snapshot")
    ap.add_argument("--host-label", default=socket.gethostname())
    ap.add_argument("--hosts", default=os.environ.get("V17_KPI_HOSTS", DEFAULT_HOSTS))
    ap.add_argument("--globs", default=os.environ.get("V17_KPI_GLOBS", DEFAULT_GLOBS))
    ap.add_argument("--since", type=float, default=None)
    ap.add_argument("--days", type=float, default=float(os.environ.get("V17_KPI_DAYS", "4")))
    ap.add_argument("--at-days", type=float, default=float(os.environ.get("V17_AT_DAYS", "3")))
    ap.add_argument("--workers-default", type=int, default=int(os.environ.get("V17_KPI_WORKERS", "2")))
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    now = a.now or _now()
    globs = [g for g in a.globs.split(",") if g]
    if a.extract:
        wm = {}
        try:
            for k, v in json.loads(os.environ.get("V17_KPI_WMAP") or "{}").items():
                h, _, s = k.partition("|")
                wm[(h, s)] = [tuple(x) for x in v]
        except ValueError:
            pass
        r = extract(globs, a.host_label, a.since if a.since is not None else now - a.days * 86400, a.workers_default, wm)
        sys.stdout.write(json.dumps(r, default=str))
        return 0
    if a.rollback:
        d = rollback()
        print(json.dumps({"rolled_back_to": d.get("generated_utc"), "apply": d.get("apply"), "env": d.get("env")}, default=str))
        return 0
    if a.disable:
        cur = load_autotune()
        cur.update({"apply": False, "disabled_utc": _iso(now)})
        save_autotune(cur, event="disable")
        print(json.dumps({"apply": False}))
        return 0
    lk = _lock("v17_kpi")
    if lk is None:
        print(json.dumps({"skip": "another v17_kpi run holds /tmp/v17_kpi.lock"}))
        return 0
    con, status, n_new, n_seen = collect(a.hosts, globs, a.days, a.workers_default, now)
    roll = rollups(con, now)
    reg = regression_check(con, now)
    alert = write_alert(reg)
    at_out = None
    if a.autotune:
        today = time.strftime("%Y%m%d", time.gmtime(now))
        done_today = sorted(AT_HIST_DIR.glob(f"v17_autotune_{today}T*.json")) if AT_HIST_DIR.exists() else []
        if done_today and not a.force:
            at_out = {"skip": f"autotune already ran today ({done_today[-1].name}); --force to rerun"}
        else:
            doc = autotune(db_boards(con, now - a.at_days * 86400), load_autotune(), now, a.at_days)
            doc["kpi_regression_at_tune"] = reg.get("regression")
            at_out = {"snapshot": save_autotune(doc), "changed": {k: (v["prev"], v["value"]) for k, v in doc["knobs"].items() if v["value"] != v["prev"]}}
    at_doc = load_autotune()
    roll["regression"] = reg
    roll["autotune"] = at_doc
    roll["hosts_status"] = status
    _write_atomic(KPI_DIR / "v17_kpi_latest.json", json.dumps(roll, default=str))
    _write_atomic(KPI_DIR / "v17_kpi_latest.md", report_md(roll, reg, at_doc, status))
    print(json.dumps({"ts": _iso(now), "hosts": status, "new": n_new, "seen": n_seen, "db": roll["n_db"], "regression": reg.get("regression"), "alert": alert, "autotune": at_out}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
