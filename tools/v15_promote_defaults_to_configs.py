#!/usr/bin/env python3
"""v15_promote_defaults_to_configs — pre-open pipeline step 6+7+8 (PROMO, 2026-10-01).

After tools/v15_daily_template_update.py has promoted every positive avg-delta value to the bold default of the four
TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx, this tool pushes the NEW defaults to every consumer:
  (a) data/cat_side_defaults_4.json  — via tools/build_cat_side_defaults_4.py (its one-default rule refuses violations); this file is the
      LIVE effect (cat_side_defaults.py hot-reloads it: per-sym > cat_side > global). Per-sym overrides are never touched here.
  (b) config.py (crypto) / config_tradier.py (stocks) class defaults — ONLY where the venue's LONG and SHORT winners agree (a single global
      value cannot hold two different sides; the cat_side values stay in the JSON). Text edit of exactly one `    KEY: type = value` line.
  (c) a QuickConfig patch queue item (data/wiring/queue/PROMO/<seq>/: apply_hook.py + MANIFEST.json) for the integrator — ONLY where all four
      cat_side values agree (QuickConfig is the single global of both venues). Sweeps apply the JSON on top anyway.
Guards (a key that fails any guard is listed in the diff CSV with its status and NOT changed):
  REFUSED_NONSCALAR_OR_SECRET, REFUSED_NOT_IN_BIBLE, REFUSED_NOT_BOTH_WIRED (SWITCH_BIBLE status[venue] must be WIRED_BOTH* and the key a
  field of the venue config AND QuickConfig), REFUSED_VEC_UNWIRED (data/vec_unwired.json), CONFLICT_HELD (live config and QuickConfig disagree:
  LIVE CONFIG WINS, the old JSON value stays), NO_CHANGE.
Dry-run is the DEFAULT (writes only data/wiring/promo/diff_<ts>.csv + candidate JSON). --apply: backups of every file first, one flock, all edits
validated (py_compile, import, resolver returns the new values) and rolled back on any failure. Idempotent (a second run finds an empty diff).
Locked live files: LOCKED_FILES.md has the immutable flag — the unlock record goes to data/wiring/LOG.md instead (user GO 2026-10-01).

  python tools/v15_promote_defaults_to_configs.py --templates SPREADSHEETS/_work [--report data/reports/v15_daily_template_update_<ts>.json]
         [--agg SPREADSHEETS/v15_vector_delta_latest.xlsx] [--apply] [--tag <round>]
"""
import argparse
import csv
import datetime
import fcntl
import glob
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
VENUE_FILE = {"CRYPTO": "config.py", "STOCKS": "config_tradier.py"}
SECRET_RE = re.compile(r"API_KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|ACCOUNT_ID|PRIVATE", re.I)
JSON_PATH = ROOT / "data" / "cat_side_defaults_4.json"
PROMO_DIR = ROOT / "data" / "wiring" / "promo"
QUEUE_DIR = ROOT / "data" / "wiring" / "queue" / "PROMO"
LOG_MD = ROOT / "data" / "wiring" / "LOG.md"
_MISSING = object()
# PROMO3 gates (real-money safety; every refusal is listed in the diff CSV, never silent)
DENY_KEY_RE = re.compile(r"^AUGMENT_ONLY_WHEN_PROFITABLE")  # CLAUDE.md/BASE RULE: never augment a losing position -> never promoted to False
RANGE_100_RE = re.compile(r"(RSI|STOCH|MFI|WT)[A-Z0-9_]*(THRESHOLD|MAX|MIN|LEVEL)", re.I)  # oscillator thresholds live in [0,100] (RSI) -- >100 can never trigger
GATES = {"min_n_sym": 0, "hold": set()}


def scalar(v):
    return isinstance(v, (bool, int, float, str)) or v is None


def same(a, b):
    if a == b:
        return True
    try:
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool) and float(a) == float(b)
    except Exception:
        return False


def venue_of(cs):
    return "crypto" if cs.startswith("CRYPTO") else "stocks"


def lit(v, annotation):
    """python literal for a class attribute line, typed by the existing annotation."""
    a = (annotation or "").strip().lower()
    if isinstance(v, bool) or a == "bool":
        return "True" if bool(v) else "False"
    if a == "float":
        return repr(float(v))
    if a == "int":
        return str(int(float(v)))
    if isinstance(v, str):
        return json.dumps(v)
    return repr(v)


def attr_re(key):
    return re.compile(r"^(?P<head>    " + re.escape(key) + r"\s*:\s*(?P<ann>[^=\n]+?)\s*=\s*)(?P<val>[^#\n]*?)(?P<tail>\s*(#.*)?)$", re.M)


def class_segment(text, start_pat, end_pat):
    i = text.find(start_pat)
    if i < 0:
        return None
    j = text.find(end_pat, i + len(start_pat))
    return (i, len(text) if j < 0 else j)


def edit_attr(text, key, new_value, tag, seg=None):
    """returns (new_text, status, old_literal). status: EDITED | ALREADY | NOT_FOUND | AMBIGUOUS."""
    lo, hi = (0, len(text)) if seg is None else seg
    body = text[lo:hi]
    ms = list(attr_re(key).finditer(body))
    if not ms:
        return text, "NOT_FOUND", None
    if len(ms) > 1:
        return text, "AMBIGUOUS", None
    m = ms[0]
    newlit = lit(new_value, m.group("ann"))
    old = m.group("val").strip()
    if old == newlit:
        return text, "ALREADY", old
    tail = m.group("tail") or ""
    comment = tail.strip()
    new_line = f"{m.group('head')}{newlit}  # PROMO {tag}: was {old}" + (f" | {comment[1:].strip()}" if comment else "")
    body2 = body[: m.start()] + new_line + body[m.end():]
    return text[:lo] + body2 + text[hi:], "EDITED", old


def load_report(path):
    """promoted keys of the daily template update report: {cs: {key: {value, avg_delta, tab}}}."""
    out = {cs: {} for cs in CAT_SIDES}
    if not path or not Path(path).exists():
        return out
    j = json.loads(Path(path).read_text())
    for cs in CAT_SIDES:
        for kind in ("promoted_switch", "promoted_filter"):
            for e in (j.get(cs) or {}).get(kind) or []:
                tab, name, _old, new, avg = e[0], e[1], e[2], e[3], e[4]
                val = str(new).split("=", 1)[1] if kind == "promoted_filter" and "=" in str(new) else new
                out[cs][name] = {"value": val, "avg_delta": avg, "tab": tab, "kind": kind}
    return out


def load_agg(path):
    """{(cs, 'NAME=cand'): (pos_sym, n)} from the merged aggregate workbook."""
    out = {}
    if not path or not Path(path).exists():
        return out
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True)
    for cs in CAT_SIDES:
        if cs not in wb.sheetnames:
            continue
        for r in wb[cs].iter_rows(min_row=2, values_only=True):
            if r and r[1]:
                k = (cs, str(r[1]))
                if k not in out or (r[6] or 0) > out[k][1]:
                    out[k] = (r[3], r[6])
    wb.close()
    return out


def run_builder(templates, promos_path, out_path):
    env = dict(os.environ, CSD4_TEMPLATE_DIR=str(templates), CSD4_PROMOTIONS=str(promos_path), CSD4_OUT=str(out_path))
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_cat_side_defaults_4.py")], env=env, capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode, (r.stdout + r.stderr)[-3000:]


def build_plan(cand, old, bible, unwired, qc_fields, venue_fields, promos, agg):
    """classify every key whose candidate value differs from the current JSON. Returns rows, approved {cs: {key: new}}."""
    rows, approved = [], {cs: {} for cs in CAT_SIDES}
    conflicts = (cand.get("_meta") or {}).get("conflicts_quick_vs_live") or {}
    for cs in CAT_SIDES:
        cur, new = old.get(cs) or {}, cand.get(cs) or {}
        venue = venue_of(cs)
        for key in sorted(new):
            nv = new[key]
            ov = cur.get(key, _MISSING)
            if ov is not _MISSING and same(ov, nv):
                continue
            pr = promos.get(cs, {}).get(key) or {}
            avg = pr.get("avg_delta")
            nsym = agg.get((cs, f"{key}={pr.get('value')}"), (None, None))[0] if pr else None
            row = {"key": key, "cat_side": cs, "old": "<absent>" if ov is _MISSING else ov, "new": nv, "avg_delta": avg, "n_sym": nsym, "status": "", "reason": ""}
            rows.append(row)
            if SECRET_RE.search(key) or not scalar(nv) or (ov is not _MISSING and not scalar(ov)):
                row["status"], row["reason"] = "REFUSED_NONSCALAR_OR_SECRET", ""
            elif not pr:
                row["status"], row["reason"] = "NOT_PROMOTED_TEMPLATE_DRIFT", "template/JSON difference without an avg-delta promotion for this cat_side: live JSON value kept (PROMO2: only promotions change live)"
            elif DENY_KEY_RE.search(key):
                row["status"], row["reason"] = "REFUSED_PROHIBITION", "promotion would violate a CLAUDE.md absolute rule (augment only on profitable positions)"
            elif "RSI" in key.upper() and isinstance(nv, (int, float)) and not isinstance(nv, bool) and not (0 <= float(nv) <= 100):
                row["status"], row["reason"] = "REFUSED_OUT_OF_RANGE", "RSI threshold outside 0..100 can never trigger"
            elif (cs + ":" + key) in GATES["hold"] or key in GATES["hold"]:
                row["status"], row["reason"] = "HELD_BY_GATE", "--hold-keys: kept at the current live value for review"
            elif GATES["min_n_sym"] and (nsym is None or float(nsym) < GATES["min_n_sym"]):
                row["status"], row["reason"] = "HELD_BREADTH", f"n_sym={nsym} < --min-n-sym {GATES['min_n_sym']}"
            elif key in conflicts.get(cs, []):
                row["status"], row["reason"] = "CONFLICT_HELD", "live config and QuickConfig disagree: live config wins, old value kept"
            elif key not in bible:
                row["status"], row["reason"] = "REFUSED_NOT_IN_BIBLE", "SWITCH_BIBLE has no record: wiring unknown"
            elif key in unwired:
                row["status"], row["reason"] = "REFUSED_VEC_UNWIRED", "data/vec_unwired.json"
            else:
                b = bible[key]
                st = (b.get("status") or {}).get(venue, "")
                inc = b.get("in_config") or {}
                vfile = VENUE_FILE[cs.split("_")[0]]
                if not str(st).startswith("WIRED_BOTH"):
                    row["status"], row["reason"] = "REFUSED_NOT_BOTH_WIRED", f"bible status[{venue}]={st}"
                elif key not in venue_fields[cs.split("_")[0]] or key not in qc_fields or not inc.get(vfile, True):
                    row["status"], row["reason"] = "REFUSED_NOT_BOTH_WIRED", f"not a field of {vfile} and QuickConfig"
                else:
                    row["status"] = "APPROVED"
                    approved[cs][key] = nv
    return rows, approved


def class_fields(path, cls_start, cls_end=None):
    text = Path(path).read_text()
    seg = class_segment(text, cls_start, cls_end) if cls_end else (text.find(cls_start), len(text))
    body = text[seg[0]:seg[1]]
    return set(re.findall(r"^    ([A-Z][A-Z0-9_]+)\s*:", body, re.M)), text, seg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", required=True, help="dir holding the PROMOTED TEMPLATE_{CAT_SIDE}.xlsx (work dir or SPREADSHEETS)")
    ap.add_argument("--report", default=None, help="v15_daily_template_update report json (default: newest)")
    ap.add_argument("--agg", default=str(ROOT / "SPREADSHEETS" / "v15_vector_delta_latest.xlsx"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--min-n-sym", type=int, default=0, help="PROMO3 breadth gate: hold a promotion supported by fewer than N sym_sides in the aggregate (n_sym column)")
    ap.add_argument("--hold-keys", default=None, help="PROMO3: file with one KEY or CAT_SIDE:KEY per line that must NOT change (status HELD_BY_GATE)")
    a = ap.parse_args()
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    tag = a.tag or ts
    PROMO_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(PROMO_DIR / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("REFUSED: another v15_promote_defaults_to_configs run holds the lock")
    GATES["min_n_sym"] = a.min_n_sym
    if a.hold_keys:
        GATES["hold"] = {l.strip() for l in Path(a.hold_keys).read_text().splitlines() if l.strip() and not l.startswith("#")}
    templates = Path(a.templates).resolve()
    report = a.report or (sorted(glob.glob(str(ROOT / "data" / "reports" / "v15_daily_template_update_*.json")) or [None])[-1])
    promos = load_report(report)
    agg = load_agg(a.agg)
    promos_path = PROMO_DIR / f"promotions_{ts}.json"
    existing = ROOT / "data" / "cat_side_promotions.json"
    merged = json.loads(existing.read_text()) if existing.exists() else {}
    for cs in CAT_SIDES:
        merged.setdefault(cs, {}).update(promos[cs])
    promos_path.write_text(json.dumps(merged, indent=1, default=str))
    cand_path = PROMO_DIR / f"candidate_{ts}.json"
    rc, out = run_builder(templates, promos_path, cand_path)
    if rc != 0 or not cand_path.exists():
        sys.exit(f"REFUSED by build_cat_side_defaults_4 (rc={rc}): nothing written\n{out}")
    old = json.loads(JSON_PATH.read_text())
    cand = json.loads(cand_path.read_text())
    bible = json.loads((ROOT / "data" / "SWITCH_BIBLE.json").read_text()).get("switches") or {}
    uw = json.loads((ROOT / "data" / "vec_unwired.json").read_text())
    unwired = set(uw.get("switches") or []) | set(uw.get("filters") or []) | set(uw.get("switches_manual") or []) | set(uw.get("filters_manual") or [])
    qc_fields, qc_text, qc_seg = class_fields(ROOT / "v12_quick_engine.py", "class QuickConfig:", "\n    def ")
    vf = {}
    vtext = {}
    vseg = {}
    for ven, fn in VENUE_FILE.items():
        vtext[ven] = (ROOT / fn).read_text()
        vf[ven] = set(re.findall(r"^    ([A-Z][A-Z0-9_]+)\s*:", vtext[ven], re.M))
    rows, approved = build_plan(cand, old, bible, unwired, qc_fields, vf, merged, agg)
    csv_path = PROMO_DIR / f"diff_{ts}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "cat_side", "old", "new", "avg_delta", "n_sym", "status", "reason"])
        w.writeheader()
        w.writerows(rows)
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(f"[plan] templates={templates} report={report} agg={'yes' if agg else 'no'} candidate={cand_path}")
    print(f"[diff] {len(rows)} changed keys: {by} -> {csv_path}")
    refused = sorted({r['key'] for r in rows if r['status'].startswith('REFUSED') or r['status'] == 'CONFLICT_HELD'})
    print(f"[refused/held keys] {len(refused)}: {refused[:25]}{' ...' if len(refused) > 25 else ''}")
    n_app = sum(len(v) for v in approved.values())
    # final per-cat_side values after approved changes (what config/QuickConfig globals must agree with)
    final = {cs: dict(old.get(cs) or {}) for cs in CAT_SIDES}
    for cs in CAT_SIDES:
        final[cs].update(approved[cs])
    keys = sorted({k for cs in CAT_SIDES for k in approved[cs]})
    cfg_edits = {"CRYPTO": {}, "STOCKS": {}}
    qc_edits = {}
    for k in keys:
        for ven in ("CRYPTO", "STOCKS"):
            if any(k in approved[f"{ven}_{s}"] for s in ("LONG", "SHORT")):
                vl, vs = final[f"{ven}_LONG"].get(k, _MISSING), final[f"{ven}_SHORT"].get(k, _MISSING)
                if vl is not _MISSING and vs is not _MISSING and same(vl, vs):
                    cfg_edits[ven][k] = vl
        vals = [final[cs].get(k, _MISSING) for cs in CAT_SIDES]
        if all(v is not _MISSING for v in vals) and all(same(vals[0], v) for v in vals):
            qc_edits[k] = vals[0]
    print(f"[approved] {n_app} (key,cat_side) changes; global config edits: crypto={len(cfg_edits['CRYPTO'])} stocks={len(cfg_edits['STOCKS'])}; QuickConfig patch keys={len(qc_edits)}")
    if not a.apply:
        print("[dry-run] nothing written to live files (candidate/diff only). Re-run with --apply at the pre-open window.")
        return
    if not n_app:
        print("[apply] empty diff: nothing to do (idempotent)")
        return
    # ---- apply: backups first, all-or-nothing -------------------------------------------------------------------------------------
    bdir = ROOT / "backups"
    bdir.mkdir(exist_ok=True)
    backups = {}

    def backup(p):
        dst = bdir / f"before_promo_{ts}_{Path(p).name}"
        shutil.copy2(p, dst)
        backups[Path(p)] = dst

    try:
        # new text for the config files (exact-one-line edits)
        new_text, edit_log = {}, []
        for ven, fn in VENUE_FILE.items():
            text = vtext[ven]
            for k, v in sorted(cfg_edits[ven].items()):
                text, st, oldlit = edit_attr(text, k, v, tag, seg=None)
                edit_log.append((fn, k, st, oldlit, v))
            if text != vtext[ven]:
                new_text[ROOT / fn] = text
        for fn, k, st, oldlit, v in edit_log:
            print(f"  [config] {fn} {k}: {st} (was {oldlit}) -> {v!r}")
        if JSON_PATH.exists():
            backup(JSON_PATH)
        for p in new_text:
            backup(p)
        # JSON: current + approved changes only (never deletes a key; held/refused keys keep the old value)
        newjson = json.loads(JSON_PATH.read_text())
        for cs in CAT_SIDES:
            newjson.setdefault(cs, {}).update(approved[cs])
        newjson.setdefault("_meta", {})["promo"] = {"at": datetime.datetime.utcnow().isoformat() + "Z", "tag": tag, "changed": {cs: sorted(approved[cs]) for cs in CAT_SIDES}, "diff_csv": str(csv_path.relative_to(ROOT))}
        tmpj = JSON_PATH.with_suffix(".tmp")
        tmpj.write_text(json.dumps(newjson, indent=1, default=str, sort_keys=True))
        json.loads(tmpj.read_text())
        for p, t in new_text.items():
            tmpp = p.with_suffix(".promo_tmp")
            tmpp.write_text(t)
            py_compile.compile(str(tmpp), doraise=True)
            tmpp.replace(p)
        tmpj.replace(JSON_PATH)
        if existing.exists():
            backup(existing)
        if not existing.exists():
            backups[existing] = None
        existing.write_text(json.dumps(merged, indent=1, default=str))
        # validation: import + resolver returns the new values
        chk = subprocess.run([sys.executable, "-c", "import config, config_tradier, cat_side_defaults as c; import sys; sys.exit(0)"], cwd=str(ROOT), capture_output=True, text=True)
        if chk.returncode:
            raise RuntimeError("import check failed: " + chk.stderr[-500:])
        import importlib
        import cat_side_defaults as CSD
        importlib.reload(CSD)
        CSD._cache["mtime"] = None
        bad = []
        for cs in CAT_SIDES:
            for k, v in approved[cs].items():
                got = CSD.get(k, cs, _MISSING)
                if got is _MISSING or not same(got, v):
                    bad.append((cs, k, got, v))
        if bad:
            raise RuntimeError(f"resolver mismatch: {bad[:5]}")
    except Exception as e:
        for p, b in backups.items():
            if b is None:
                p.unlink(missing_ok=True)
            else:
                shutil.copy2(b, p)
        sys.exit(f"[apply] FAILED, all files restored from backups: {e}")
    # QuickConfig patch queue item for the integrator
    seq = max([int(p.name) for p in QUEUE_DIR.glob("[0-9]*") if p.name.isdigit()] + [0]) + 1
    qdir = QUEUE_DIR / f"{seq:03d}"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "patch.json").write_text(json.dumps({"tag": tag, "values": qc_edits}, indent=1, default=str))
    hook = Path(__file__).with_name("v15_promote_quickconfig_hook.py")
    shutil.copy2(hook, qdir / "apply_hook.py")
    (qdir / "MANIFEST.json").write_text(json.dumps({
        "agent": "PROMO", "seq": f"{seq:03d}", "title": f"avg-delta promoted defaults -> QuickConfig class defaults (round {tag}); all four cat_side values agree",
        "files": ["apply_hook.py (reads patch.json; edits QuickConfig fields only; idempotent)", "patch.json"],
        "changed_keys": sorted(qc_edits), "wired_now": [],
        "baseline_shift": "BASELINE-SHIFT: promoted defaults (JSON already carries them for every sweep via apply_cat_side_defaults; this patch only aligns the class default)",
        "evidence": f"diff {csv_path.relative_to(ROOT)}; approved {n_app} (key,cat_side) changes"}, indent=1))
    with LOG_MD.open("a") as f:
        f.write(f"\n- {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%MZ')} PROMO apply round {tag}: cat_side_defaults_4.json +{n_app} changes, config.py edits={len(cfg_edits['CRYPTO'])}, config_tradier.py edits={len(cfg_edits['STOCKS'])}, QuickConfig queue PROMO/{seq:03d} ({len(qc_edits)} keys). LOCKED_FILES.md immutable (uchg): unlock recorded here per user GO 2026-10-01. Backups backups/before_promo_{ts}_*.\n")
    print(f"[apply] done: JSON +{n_app}, config edits crypto={len(cfg_edits['CRYPTO'])} stocks={len(cfg_edits['STOCKS'])}, QuickConfig queue {qdir} ({len(qc_edits)} keys), backups before_promo_{ts}_*")


if __name__ == "__main__":
    main()
