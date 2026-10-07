#!/usr/bin/env python3
"""v15_switch_healer — persistent daemon (s1 only) that keeps every switch honestly connected.

USER 2026-10-03: constantly fix switches that are wrongly connected, not vectorized,
not present live, or stuck at pos_sym 0 — so the next symbol gets values in each row.

Each cycle (default 30 min):
  1. CODE INDEX (cached by mtime): AST reachability per config name in vec code
     (v12_quick_engine + vec_decisions) and live code (ez_manage + tradier_manage).
  2. BOARD CENSUS: per-switch tested/nonzero/pos_sym + skip reasons from progress JSONs.
  3. CLASSIFY every switch; PLAN safe repairs; APPLY within budget; JOURNAL all.
  4. SYNC changed data files to s2/s5 (s1 is source of truth for healer-owned files).

SAFE (applied): vec_unwired.json add/remove, template row repair/delete + FINAL_NORM regen.
NEVER: locked files (engine/config/live code), bold DEFAULT values, pilots/herd/scheduler,
finished boards, promotions, reverts of own actions within 7d, >budget changes per cycle.
ENGINE-BACKLOG (detected, reported, never edited): live-functional but vec-absent switches.

  V15_HEALER_DRYRUN=1  audit-only (default 0). V15_HEALER_INTERVAL_S (default 1800).
  V15_HEALER_ONCE=1    single cycle then exit (for cron/debug).
"""
import collections
import copy
import fcntl
import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

LEDGER = ROOT / "data" / "vec_unwired.json"
TEMPLATES = [ROOT / "SPREADSHEETS" / n for n in ("TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx", "TEMPLATE_STOCKS_LONG.xlsx", "TEMPLATE_STOCKS_SHORT.xlsx")]
NORM_DIR = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"
PROGRESS_DIRS = [ROOT / "data" / "reports" / "lifecycle_pilot", Path("/home/niels/v15_run25_20261002/progress")]
JOURNAL = ROOT / "data" / "reports" / "healer" / "journal.jsonl"
STATE = ROOT / "data" / "reports" / "healer" / "state.json"
LOCKFILE = Path("/tmp/v15_switch_healer.lock")
LOGFILE = Path("/tmp/v15_switch_healer.log")
BACKUP_DIR = ROOT / "backups"
INDEX_CACHE = ROOT / "data" / "reports" / "healer" / "code_index.json"
FLEET = tuple(h.strip() for h in os.environ.get("V15_HEALER_FLEET", "s2,s5").split(",") if h.strip())

INTERVAL = int(os.environ.get("V15_HEALER_INTERVAL_S", "1800"))
DRYRUN = os.environ.get("V15_HEALER_DRYRUN", "0") == "1"
ONCE = os.environ.get("V15_HEALER_ONCE", "0") == "1"
BUDGET_LEDGER = int(os.environ.get("V15_HEALER_BUDGET_LEDGER", "25"))
BUDGET_TPL_ROWS = int(os.environ.get("V15_HEALER_BUDGET_TPL_ROWS", "40"))
POS_SYM_MIN_SYMS = 10
NEVER_MOVE_MIN_ZERO = 50
ANOMALY_FRAC = 0.15
NO_REVERT_DAYS = 7

VEC_FILES = ["v12_quick_engine.py"]
LIVE_FILES = ["ez_manage.py", "tradier_manage.py"]

log = logging.getLogger("healer")


def _setup_logging():
    fmt = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    if sys.stdout.isatty():
        h1 = logging.StreamHandler(sys.stdout)
        h1.setFormatter(fmt)
        log.addHandler(h1)
    h2 = logging.FileHandler(str(LOGFILE))
    h2.setFormatter(fmt)
    log.addHandler(h2)
    log.setLevel(logging.INFO)


def _md5(p):
    try:
        return hashlib.md5(Path(p).read_bytes()).hexdigest()[:12]
    except Exception:
        return "missing"


def _journal(action, detail):
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "action": action, "detail": detail}) + "\n")
    except Exception as e:
        log.warning("journal failed: %s", e)


def _recent_own_actions(days=NO_REVERT_DAYS):
    out = collections.Counter()
    try:
        if not JOURNAL.exists():
            return out
        cutoff = time.time() - days * 86400
        for line in open(JOURNAL):
            try:
                r = json.loads(line)
            except Exception:
                continue
            try:
                ts = time.mktime(time.strptime(r.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ"))
            except Exception:
                continue
            if ts >= cutoff and r.get("action") in ("ledger_add", "ledger_remove", "tpl_delete", "tpl_set"):
                out[(r["action"], str((r.get("detail") or {}).get("key") or (r.get("detail") or {}).get("switch") or ""))] += 1
    except Exception:
        pass
    return out


_pilot_fns = {}


def _pilot():
    if not _pilot_fns:
        import v15_pilot as P
        from tools.opt.evaluate_v12 import _coerce_override
        import v12_quick_engine as V
        _pilot_fns.update(parse=P._parse_opt_value, compat=P._cand_compatible, defaults=P.get_defaults_for_symside, sheets=P.SWITCH_SHEETS, coerce=_coerce_override, cfg=V.QuickConfig)
    return _pilot_fns


def build_code_index():
    """{name: {vec_func:[...], vec_stub:bool, vec_mod:bool, live:bool, cfg:bool}} cached by mtime."""
    import glob as _g
    from tools import _zero_ast as Z
    vec_paths = [str(ROOT / VEC_FILES[0])] + sorted(_g.glob(str(ROOT / "vec_decisions" / "*.py")))
    live_paths = [str(ROOT / f) for f in LIVE_FILES]
    sig = {p: (os.path.getmtime(p), os.path.getsize(p)) for p in vec_paths + live_paths if os.path.exists(p)}
    try:
        if INDEX_CACHE.exists():
            c = json.load(open(INDEX_CACHE))
            if c.get("sig") == {k: list(v) for k, v in sig.items()}:
                return c["index"]
    except Exception:
        pass
    vec = Z.scan(vec_paths)
    live = Z.scan(live_paths)
    names = set(vec) | set(live)
    idx = {}
    for k in names:
        vh = (vec.get(k) or {}).get("live") or []
        idx[k] = {"vec_func": sorted({f for (_, _, f) in vh if f != "<module>"})[:8], "vec_stub": bool((vec.get(k) or {}).get("stub")), "vec_mod": bool(vh) and not any(f != "<module>" for (_, _, f) in vh), "live": bool((live.get(k) or {}).get("live"))}
    try:
        cfg_names = set(_pilot()["cfg"]().__dict__.keys())
    except Exception:
        cfg_names = set()
    for k in cfg_names:
        idx.setdefault(k, {"vec_func": [], "vec_stub": False, "vec_mod": False, "live": False})["cfg"] = True
    try:
        INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"sig": {k: list(v) for k, v in sig.items()}, "index": idx}, open(INDEX_CACHE, "w"))
    except Exception as e:
        log.warning("index cache write failed: %s", e)
    return idx


PEER_NZ_CACHE = ROOT / "data" / "reports" / "healer" / "peer_nonzero.json"
PEER_NZ_TTL = 6 * 3600


def _fetch_peer_nonzero():
    """Union of nonzero-move switches from s2/s5 (ssh scan, 6h cache). A switch that moved
    anywhere in the fleet must never be ledgered as dead — s1's view alone is not enough."""
    try:
        if PEER_NZ_CACHE.exists():
            c = json.load(open(PEER_NZ_CACHE))
            if time.time() - c.get("ts", 0) < PEER_NZ_TTL:
                return set(c.get("keys", []))
    except Exception:
        pass
    helper = ("import json,glob\nnz=set()\nfor pat in ['data/reports/lifecycle_pilot/*_v14_progress.json','/home/niels/v15_run25_20261002/progress/*_v14_progress.json']:\n"
              " for p in glob.glob(pat):\n  try:\n   J=json.load(open(p))\n  except Exception:\n   continue\n"
              "  for k,v in (J.get('done') or {}).items():\n   if not isinstance(v,dict):\n    continue\n"
              "   d=v.get('delta')\n   if d is None:\n    continue\n   try:\n    f=float(d)\n   except (TypeError,ValueError):\n    continue\n"
              "   if abs(f)>=1e-9 and ':' in k and '=' in k.split(':',1)[1]:\n    nz.add(k.split(':',1)[1].split('=',1)[0])\n"
              "print(len(nz))\n")
    keys = set()
    for h in FLEET:
        try:
            subprocess.run(["ssh", h, "cat > /tmp/healer_nz.py"], input=helper, capture_output=True, text=True, timeout=60)
            r = subprocess.run(["ssh", h, "cd ~/binance-sandbox && python3 /tmp/healer_nz.py"], capture_output=True, text=True, timeout=600)
            n = int((r.stdout or "0").strip().split()[0])
            log.info("peer %s nonzero-keys: %d", h, n)
            r2 = subprocess.run(["ssh", h, "cd ~/binance-sandbox && python3 -c \"exec(open('/tmp/healer_nz.py').read().replace('print(len(nz))','import json;print(json.dumps(sorted(nz)))'))\""], capture_output=True, text=True, timeout=600)
            keys |= set(json.loads(r2.stdout or "[]"))
        except Exception as e:
            log.warning("peer %s scan failed: %s", h, e)
    try:
        PEER_NZ_CACHE.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"ts": time.time(), "keys": sorted(keys)}, open(PEER_NZ_CACHE, "w"))
    except Exception:
        pass
    return keys


def census_boards():
    """Per-switch {tested, zero, nonzero, syms_pos, syms_tested, reasons} from progress JSONs."""
    import glob as _g
    agg = collections.defaultdict(lambda: {"tested": 0, "zero": 0, "nonzero": 0, "syms_pos": set(), "syms_tested": set(), "reasons": collections.Counter()})
    nfiles = 0
    for d in PROGRESS_DIRS:
        if not d.exists():
            continue
        for p in _g.glob(str(d / "*_v14_progress.json")):
            try:
                J = json.load(open(p))
            except Exception:
                continue
            nfiles += 1
            ss = str(J.get("symside", Path(p).name))
            for k, v in (J.get("done") or {}).items():
                if not isinstance(v, dict) or ":" not in k or "=" not in k.split(":", 1)[1]:
                    continue
                sw = k.split(":", 1)[1].split("=", 1)[0]
                a = agg[sw]
                a["syms_tested"].add(ss)
                dlt = v.get("delta")
                if dlt is None:
                    a["reasons"][str(v.get("reason") or "NO_REASON")[:40]] += 1
                    continue
                try:
                    f = float(dlt)
                except (TypeError, ValueError):
                    a["reasons"]["NONNUMERIC"] += 1
                    continue
                a["tested"] += 1
                if abs(f) < 1e-9:
                    a["zero"] += 1
                else:
                    a["nonzero"] += 1
                    if f > 1e-9:
                        a["syms_pos"].add(ss)
    out = {}
    for sw, a in agg.items():
        out[sw] = {"tested": a["tested"], "zero": a["zero"], "nonzero": a["nonzero"], "pos_sym": len(a["syms_pos"]), "syms_tested": len(a["syms_tested"]), "reasons": dict(a["reasons"])}
    return out, nfiles


def classify_switch(sw, cen, idx, ledger):
    """(verdict, action, why). Verdicts: OK, PLATEAU, LEDGER_ADD, LEDGER_RESCUE, TPL_FIX, BACKLOG, WATCH."""
    info = idx.get(sw, {})
    vec_func = info.get("vec_func") or []
    in_ledger = sw in ledger
    pos_sym = cen.get("pos_sym", 0)
    syms_tested = cen.get("syms_tested", 0)
    never_moves = cen.get("zero", 0) >= NEVER_MOVE_MIN_ZERO and cen.get("nonzero", 0) == 0
    if in_ledger:
        if vec_func:
            return ("LEDGER_RESCUE", "remove", "ledgered but func-level vec reads %s — must evaluate" % vec_func[:3])
        return ("OK", "none", "ledgered, no vec reads")
    if never_moves and not vec_func:
        if info.get("live"):
            return ("BACKLOG", "ledger_add", "never moves anywhere + no vec read but LIVE-functional — honest vec skip + engine-unlock backlog")
        return ("LEDGER_ADD", "add", "fleet never-mover + no func-level vec read (stub=%s mod=%s live=%s)" % (info.get("vec_stub"), info.get("vec_mod"), info.get("live")))
    if never_moves and vec_func:
        return ("PLATEAU", "none", "vec-read but never moves — plateau/interaction, keep evaluating")
    if pos_sym == 0 and syms_tested >= POS_SYM_MIN_SYMS and cen.get("nonzero", 0) == 0 and cen.get("zero", 0) < NEVER_MOVE_MIN_ZERO:
        return ("WATCH", "none", "pos_sym 0 on %d syms but <%d zeros — thin evidence, keep evaluating" % (syms_tested, NEVER_MOVE_MIN_ZERO))
    return ("OK", "none", "moves or positive somewhere")


def audit_templates(ledger):
    """Full compat audit of source templates. Returns (fails, none_rows) with row numbers."""
    import openpyxl
    P = _pilot()
    fails, nones = [], []
    for f in TEMPLATES:
        if not f.exists():
            continue
        is_crypto = "CRYPTO" in f.name
        ss = ("XUSDT" if is_crypto else "X") + ("_LONG" if "LONG" in f.name else "_SHORT")
        defaults = P["defaults"](ss)
        cfg = P["cfg"]()
        wb = openpyxl.load_workbook(str(f), data_only=True, read_only=True)
        for ws in wb.worksheets:
            if ws.title not in P["sheets"]:
                continue
            rn = 2
            for row in ws.iter_rows(min_row=3, max_col=2, values_only=True):
                rn += 1
                a, b = row[0], row[1]
                sa = str(a or "").strip()
                if not sa:
                    continue
                if b is None or (isinstance(b, str) and b.strip() == ""):
                    fails.append({"f": str(f), "sheet": ws.title, "row": rn, "sw": sa, "b": "", "why": "EMPTY_B"})
                    continue
                if isinstance(b, str) and b.strip() in ("none", "None"):
                    nones.append({"f": str(f), "sheet": ws.title, "row": rn, "sw": sa, "b": b})
                    continue
                if sa in ledger:
                    continue
                try:
                    parsed = P["parse"](b, defaults.get(sa))
                    ok, why = P["compat"](sa, parsed, defaults)
                    ok2 = P["coerce"](sa, parsed, getattr(cfg, sa))[0] if hasattr(cfg, sa) else ok
                except Exception as e:
                    ok, ok2, why = False, False, "EXC:%s" % e
                if not ok or ok2 is False:
                    fails.append({"f": str(f), "sheet": ws.title, "row": rn, "sw": sa, "b": repr(b)[:40], "why": str(why)[:100], "dflt": repr(defaults.get(sa))[:60]})
        wb.close()
    return fails, nones


def plan_template_repairs(fails, nones, ledger):
    """(deletes, sets, notes). Rules: never delete a switch's last valid row; dict/list groups
    may vanish entirely (inexpressible); bold-invalid bools are restored to the config default
    (purity, never a flip); one bad row per bool group is repurposed to the missing value."""
    import openpyxl
    P = _pilot()
    deletes, sets, notes = [], [], []
    by_group = collections.defaultdict(list)
    failkeys = {(r["f"], r["sheet"], r["row"]) for r in fails}
    for f in TEMPLATES:
        if not f.exists():
            continue
        is_crypto = "CRYPTO" in f.name
        ss = ("XUSDT" if is_crypto else "X") + ("_LONG" if "LONG" in f.name else "_SHORT")
        defaults = P["defaults"](ss)
        cfg = P["cfg"]()
        wb = openpyxl.load_workbook(str(f), data_only=False, read_only=True)
        for ws in wb.worksheets:
            if ws.title not in P["sheets"]:
                continue
            rn = 2
            for row in ws.iter_rows(min_row=3, max_col=2):
                rn += 1
                a = row[0].value
                sa = str(a or "").strip()
                if not sa or sa in ledger:
                    continue
                b = row[1].value
                bold = bool(getattr(row[1], "font", None) and row[1].font.bold)
                by_group[(str(f), ws.title, sa)].append({"row": rn, "b": b, "bold": bold, "bad": (str(f), ws.title, rn) in failkeys})
        wb.close()
    for (f, sheet, sa), rows in sorted(by_group.items()):
        bad = [r for r in rows if r["bad"]]
        if not bad:
            continue
        is_crypto = "CRYPTO" in f
        ss = ("XUSDT" if is_crypto else "X") + ("_LONG" if "LONG" in f else "_SHORT")
        defaults = P["defaults"](ss)
        cfg = P["cfg"]()
        dflt = defaults.get(sa)
        cur = getattr(cfg, sa, None) if hasattr(cfg, sa) else None
        isbool = isinstance(cur, bool) or isinstance(dflt, bool)
        iscomplex = isinstance(cur, (dict, list, tuple, set)) or isinstance(dflt, (dict, list, tuple, set))
        have = set()
        for r in rows:
            if r["bad"] or r["b"] is None or (isinstance(r["b"], str) and r["b"].strip() == ""):
                continue
            try:
                p = P["parse"](r["b"], dflt)
                ok, _ = P["compat"](sa, p, defaults)
                ok2 = P["coerce"](sa, p, cur)[0] if hasattr(cfg, sa) else ok
                if ok and ok2 is not False:
                    have.add(p)
            except Exception:
                pass
        for r in bad:
            if r["bold"] and isbool:
                v = bool(dflt if isinstance(dflt, bool) else cur)
                sets.append({"f": f, "sheet": sheet, "row": r["row"], "sw": sa, "v": v, "why": "bold-invalid restored to config default"})
                have.add(v)
            elif r["bold"] and iscomplex:
                deletes.append({"f": f, "sheet": sheet, "row": r["row"], "sw": sa, "why": "bold-inexpressible dict/list row"})
            elif isbool and True not in have:
                sets.append({"f": f, "sheet": sheet, "row": r["row"], "sw": sa, "v": True, "why": "repurposed to missing True"})
                have.add(True)
            elif isbool and False not in have:
                sets.append({"f": f, "sheet": sheet, "row": r["row"], "sw": sa, "v": False, "why": "repurposed to missing False"})
                have.add(False)
            else:
                deletes.append({"f": f, "sheet": sheet, "row": r["row"], "sw": sa, "why": "invalid cand, group keeps valid rows" if have else "invalid cand, no valid row expressible"})
        if not have and not iscomplex and not isbool:
            notes.append("no-valid-row:%s:%s:%s — left for manual review" % (Path(f).name, sheet, sa))
            deletes[:] = [d for d in deletes if not (d["f"] == f and d["sheet"] == sheet and d["sw"] == sa)]
    for n in nones:
        sets.append({"f": n["f"], "sheet": n["sheet"], "row": n["row"], "sw": n["sw"], "v": "OFF", "why": "none→OFF (parity-correct disable spelling)"})
    return deletes, sets, notes


def _backup(path):
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dst = BACKUP_DIR / ("healer_%s_%s" % (time.strftime("%Y%m%d%H%M%S", time.gmtime()), Path(path).name))
        import shutil
        shutil.copy2(str(path), str(dst))
        return str(dst)
    except Exception as e:
        log.warning("backup failed for %s: %s", path, e)
        return ""


def apply_ledger_ops(adds, removes):
    if DRYRUN:
        log.info("[dryrun] ledger +%d -%d", len(adds), len(removes))
        return True
    J = json.load(open(LEDGER))
    sw = set(J.get("switches", []))
    _backup(LEDGER)
    for k in sorted(adds):
        sw.add(k)
        _journal("ledger_add", {"key": k})
    for k in sorted(removes):
        sw.discard(k)
        _journal("ledger_remove", {"key": k})
    J["switches"] = sorted(sw)
    J["_healer"] = {"ts": time.strftime("%Y%m%d%H%M%S", time.gmtime()), "added": sorted(adds), "removed": sorted(removes)}
    J["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    json.dump(J, open(LEDGER, "w"), indent=1)
    return True


def apply_template_ops(deletes, sets):
    if DRYRUN:
        log.info("[dryrun] templates +%d sets %d deletes", len(sets), len(deletes))
        return True
    import openpyxl
    ops = collections.defaultdict(lambda: {"set": [], "del": []})
    for s in sets:
        ops[(s["f"], s["sheet"])]["set"].append(s)
    for d in deletes:
        ops[(d["f"], d["sheet"])]["del"].append(d)
    for (f, sheet), o in sorted(ops.items()):
        _backup(f)
        wb = openpyxl.load_workbook(f)
        ws = wb[sheet]
        for s in o["set"]:
            ws.cell(row=s["row"], column=2).value = s["v"]
            _journal("tpl_set", {"key": "%s:%s!%d %s=%r" % (Path(f).name, sheet, s["row"], s["sw"], s["v"]), "why": s["why"]})
        for d in sorted(o["del"], key=lambda x: -x["row"]):
            if str(ws.cell(row=d["row"], column=1).value or "").strip() == "":
                log.warning("skip delete of empty-A row %s!%d", sheet, d["row"])
                continue
            ws.delete_rows(d["row"])
            _journal("tpl_delete", {"key": "%s:%s!%d %s" % (Path(f).name, sheet, d["row"], d["sw"]), "why": d["why"]})
        wb.save(f)
    rc = subprocess.run([sys.executable, "tools/v15_template_normalize_defaults.py", "--src", "SPREADSHEETS", "--out", "SPREADSHEETS/TEMPLATE_FINAL_NORM"], cwd=str(ROOT), capture_output=True, text=True, timeout=1500)
    log.info("norm-regen rc=%d %s", rc.returncode, (rc.stdout or "")[-300:])
    _journal("norm_regen", {"rc": rc.returncode})
    return rc.returncode == 0


def sync_fleet(paths):
    ok = True
    for h in FLEET:
        for p in paths:
            r = subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=accept-new", str(ROOT / p), "%s:~/binance-sandbox/%s" % (h, p)], capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                log.warning("sync %s→%s failed: %s", p, h, r.stderr[:200])
                ok = False
    for h in FLEET:
        r = subprocess.run(["ssh", h, "md5sum " + " ".join("~/binance-sandbox/%s" % p for p in paths)], capture_output=True, text=True, timeout=120)
        for line in (r.stdout or "").splitlines():
            hh, _, fp = line.partition("  ")
            for p in paths:
                if fp.strip().endswith(p) and hh.strip() != _md5(ROOT / p):
                    log.warning("hash mismatch %s on %s", p, h)
                    ok = False
    _journal("fleet_sync", {"paths": paths, "ok": ok})
    return ok


def run_cycle():
    t0 = time.time()
    idx = build_code_index()
    cen, nfiles = census_boards()
    ledger = set(json.load(open(LEDGER)).get("switches", []))
    verdicts = collections.Counter()
    adds, removes, backlog = [], [], []
    for sw, c in cen.items():
        v, act, why = classify_switch(sw, c, idx, ledger)
        verdicts[v] += 1
        if act == "add":
            adds.append(sw)
        elif act == "remove":
            removes.append(sw)
        elif act == "ledger_add":
            adds.append(sw)
            backlog.append(sw)
    own = _recent_own_actions()
    adds = [k for k in adds if own.get(("ledger_remove", k), 0) == 0]
    removes = [k for k in removes if own.get(("ledger_add", k), 0) == 0]
    if adds:
        peer_nz = _fetch_peer_nonzero()
        adds = [k for k in adds if k not in peer_nz]
        backlog = [k for k in backlog if k in adds]
    fails, nones = audit_templates(set(json.load(open(LEDGER)).get("switches", [])))
    deletes, sets, notes = plan_template_repairs(fails, nones, ledger)
    flagged = len(adds) + len(removes) + len(deletes) + len(sets)
    tested = max(1, len(cen))
    log.info("cycle: boards=%d switches=%d verdicts=%s ledger_add=%d ledger_rm=%d tpl_set=%d tpl_del=%d notes=%d", nfiles, len(cen), dict(verdicts), len(adds), len(removes), len(sets), len(deletes), len(notes))
    if flagged > ANOMALY_FRAC * tested:
        log.warning("ANOMALY: %d flagged of %d tested — alert-only cycle", flagged, tested)
        _journal("anomaly", {"flagged": flagged, "tested": tested})
        return {"anomaly": True}
    adds, removes = adds[:BUDGET_LEDGER], removes[:BUDGET_LEDGER]
    row_ops = deletes + [{"f": s["f"], "sheet": s["sheet"], "row": s["row"], "sw": s["sw"]} for s in sets]
    if len(row_ops) > BUDGET_TPL_ROWS:
        deletes = [d for d in deletes][: max(0, BUDGET_TPL_ROWS - len(sets))]
    changed = []
    if adds or removes:
        apply_ledger_ops(adds, removes)
        changed.append("data/vec_unwired.json")
    if deletes or sets:
        if apply_template_ops(deletes, sets):
            changed += ["SPREADSHEETS/%s" % Path(f).name for f in TEMPLATES] + ["SPREADSHEETS/TEMPLATE_FINAL_NORM/%s" % Path(f).name for f in TEMPLATES]
    if changed and not DRYRUN:
        sync_fleet(sorted(set(changed)))
    for sw in backlog:
        _journal("engine_backlog", {"switch": sw, "why": "live-functional, vec-absent — needs engine unlock"})
    for n in notes:
        _journal("manual_review", {"note": n})
    state = {"last_cycle": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "boards": nfiles, "verdicts": dict(verdicts), "adds": adds, "removes": removes, "tpl_sets": len(sets), "tpl_deletes": len(deletes), "backlog": backlog, "dryrun": DRYRUN, "elapsed_s": round(time.time() - t0, 1)}
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        json.dump(state, open(STATE, "w"), indent=1)
    except Exception:
        pass
    _journal("cycle", state)
    return state


def main():
    _setup_logging()
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    lf = open(LOCKFILE, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("healer already running — exiting (never a second healer)")
        return 1
    lf.write(str(os.getpid()))
    lf.flush()
    log.info("healer start pid=%d dryrun=%s interval=%ds budget=%d/%d", os.getpid(), DRYRUN, INTERVAL, BUDGET_LEDGER, BUDGET_TPL_ROWS)
    while True:
        try:
            st = run_cycle()
            log.info("cycle done: %s", {k: st.get(k) for k in ("boards", "adds", "removes", "tpl_sets", "tpl_deletes", "elapsed_s")} if isinstance(st, dict) else st)
        except Exception:
            log.exception("cycle crashed — sleeping, never dying silent")
        if ONCE:
            return 0
        time.sleep(INTERVAL + random.randint(-120, 120))


if __name__ == "__main__":
    sys.exit(main())
