"""v15_template_row_intake — queue bookkeeping for the persistent template row-add path (BIBLE §71 amendment, §94).

The ONLY template writer for new rows stays tools/v15_switch_add.py (`--queue ... --apply` on the S1 source host,
chain step 2a). This tool never writes a template. It:
  merge-inbox  merge data/template_row_queue_inbox/*.json (from `v15_switch_add.py --enqueue` or `rescue-lost`) into
               data/template_row_queue.json (dedupe by switch; an existing entry wins; processed files -> inbox/merged/)
  rescue-lost  compare the templates a pull is about to replace (BEFORE dir, e.g. backups/daily_chain_mac_<ts>) with
               the pulled ones (AFTER root): every switch present before and absent after is written to an inbox file
               (tab, candidates, bold default from the before rows) and optionally pushed to the source host's inbox, so
               a pull can never silently drop rows again (the 2026-10-09 13:49Z ENTRY_LH_TRIGGER_* loss)
  verify       per queued switch: which target templates carry it; md5 of the 8 template files (json to stdout/--out)

Usage: python3 tools/v15_template_row_intake.py merge-inbox [--queue Q] [--inbox DIR]
       python3 tools/v15_template_row_intake.py rescue-lost --before-dir DIR --after-root ROOT [--push HOST] [--dry-run]
       python3 tools/v15_template_row_intake.py verify [--queue Q] [--out F]
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
CATS = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
TEMPLATE_RELS = [f"SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_{c}.xlsx" for c in CATS] + [f"SPREADSHEETS/TEMPLATE_{c}.xlsx" for c in CATS]
QUEUE = ROOT / "data" / "template_row_queue.json"
INBOX = ROOT / "data" / "template_row_queue_inbox"


def _now():
    return datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")


def _write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    Path(tmp).write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, str(path))


def load_queue(path):
    p = Path(path)
    if not p.exists():
        return {"version": 1, "entries": []}
    q = json.loads(p.read_text())
    if isinstance(q, list):
        q = {"version": 1, "entries": q}
    q.setdefault("entries", [])
    return q


def entry_type_errors(e):
    """BIBLE §96: every candidate + default of a queue entry must pass the ONE validator for each venue (fail-closed)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import switch_types as ST
    sw = str(e.get("switch", "")).strip().upper()
    bad = []
    for venue in [str(v).upper() for v in (e.get("venues") or ["CRYPTO", "STOCKS"])]:
        c = e.get("candidates")
        cands = (c.get(venue) or []) if isinstance(c, dict) else (c or [])
        d = e.get("default")
        dflt = d.get(venue) if isinstance(d, dict) else d
        for v in list(cands) + ([dflt] if dflt is not None else []):
            ok, _cv, why = ST.coerce(sw, v, venue)
            if not ok:
                bad.append(f"{venue}:{v!r}: {why}")
    return bad


def merge_entries(queue, new_entries, source):
    """Append entries whose switch is not queued yet; an existing entry always wins (S1 owns the queue). Returns (added, ignored)."""
    have = {str(e.get("switch", "")).strip().upper() for e in queue["entries"]}
    added, ignored = [], []
    for e in new_entries:
        sw = str(e.get("switch", "")).strip().upper()
        if not sw or not e.get("tab"):
            ignored.append({"switch": sw, "why": "missing switch/tab"})
            continue
        _terr = entry_type_errors(e)
        if _terr:
            ignored.append({"switch": sw, "why": f"switch_types refused (BIBLE §96): {_terr[:4]}"})
            print(f"[intake] REFUSED {sw}: mistyped candidate/default {_terr[:4]}", flush=True)
            continue
        if sw in have:
            ignored.append({"switch": sw, "why": "already queued"})
            continue
        e = dict(e)
        e["switch"] = sw
        e.setdefault("status", "QUEUED")
        e.setdefault("source", source)
        e.setdefault("queued_at", datetime.datetime.utcnow().isoformat() + "Z")
        queue["entries"].append(e)
        have.add(sw)
        added.append(sw)
    return added, ignored


def cmd_merge_inbox(a):
    inbox = Path(a.inbox)
    files = sorted(inbox.glob("*.json")) if inbox.exists() else []
    q = load_queue(a.queue)
    tot_add, tot_ign = [], []
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            print(f"[intake] unreadable inbox file {f.name}: {e} — moved to bad/", flush=True)
            (inbox / "bad").mkdir(exist_ok=True)
            shutil.move(str(f), str(inbox / "bad" / f.name))
            continue
        ents = d.get("entries", []) if isinstance(d, dict) else d
        add, ign = merge_entries(q, ents, f"inbox:{f.name}")
        tot_add += add
        tot_ign += ign
        if not a.dry_run:
            (inbox / "merged").mkdir(exist_ok=True)
            shutil.move(str(f), str(inbox / "merged" / f.name))
    if tot_add and not a.dry_run:
        q.setdefault("history", []).append({"ts": _now(), "merged_from_inbox": tot_add})
        _write_json_atomic(a.queue, q)
    print(f"[intake] merge-inbox files={len(files)} added={len(tot_add)} {tot_add[:20]} ignored={len(tot_ign)}", flush=True)
    return 0


def rows_by_switch(path):
    """{switch: {"tab": tab, "rows": [(B as str, is_default_yes, bold)]}} for one template (read-only)."""
    import openpyxl
    out = {}
    wb = openpyxl.load_workbook(str(path), read_only=False)
    try:
        for t in SWITCH_SHEETS:
            if t not in wb.sheetnames:
                continue
            ws = wb[t]
            hdrs = [ws.cell(2, c).value for c in range(1, min(ws.max_column, 40) + 1)]
            isd = (hdrs.index("is_default") + 1) if "is_default" in hdrs else 12
            for r in range(3, ws.max_row + 1):
                sw = ws.cell(r, 1).value
                if sw in (None, ""):
                    continue
                sw = str(sw).strip()
                b = ws.cell(r, 2)
                ent = out.setdefault(sw, {"tab": t, "rows": []})
                if ent["tab"] == t:
                    ent["rows"].append((None if b.value is None else str(b.value), str(ws.cell(r, isd).value or "").strip().upper() == "YES", bool(b.font and b.font.bold)))
    finally:
        wb.close()
    return out


def _flat(rel):
    return rel.replace("/", "_")


def lost_rows(before_root, after_root, flat_before=True):
    """Switches present in a BEFORE template and absent from the AFTER template of the same path -> inbox entries."""
    found = {}
    for rel in TEMPLATE_RELS:
        bp = Path(before_root) / (_flat(rel) if flat_before else rel)
        ap = Path(after_root) / rel
        if not bp.exists() or not ap.exists():
            continue
        before, after = rows_by_switch(bp), rows_by_switch(ap)
        cs = next(c for c in CATS if f"TEMPLATE_{c}.xlsx" in rel)
        venue, side = cs.split("_")
        for sw, ent in before.items():
            if sw in after:
                continue
            sys.path.insert(0, str(ROOT / "tools"))
            import switch_types as ST  # BIBLE §96: a rescue never re-queues a value the one validator refuses
            vals = [v for v, _, _ in ent["rows"] if v not in (None, "") and ST.coerce(sw, v, venue)[0]]
            dflt = next((v for v, y, b in ent["rows"] if y and b), None) or next((v for v, y, _ in ent["rows"] if y), None) or (vals[0] if vals else None)
            if dflt is not None and not ST.coerce(sw, dflt, venue)[0]:
                continue
            if len(set(vals)) < 2 or dflt is None:
                continue
            e = found.setdefault(sw, {"switch": sw, "tab": ent["tab"], "venues": [], "sides": [], "candidates": {}, "default": {}, "lost_from": []})
            if venue not in e["venues"]:
                e["venues"].append(venue)
            if side not in e["sides"]:
                e["sides"].append(side)
            e["candidates"].setdefault(venue, [])
            for v in [dflt] + vals:
                if v not in e["candidates"][venue]:
                    e["candidates"][venue].append(v)
            e["default"].setdefault(venue, dflt)
            e["lost_from"].append(rel)
    return list(found.values())


def cmd_rescue_lost(a):
    ents = lost_rows(a.before_dir, a.after_root, flat_before=not a.unflat)
    for e in ents:
        e["why"] = f"rows present before the pull and absent after it ({len(e['lost_from'])} files) — rescued by v15_template_row_intake rescue-lost"
    print(f"[intake] rescue-lost before={a.before_dir} after={a.after_root}: {len(ents)} switches would be dropped by the pull: {[e['switch'] for e in ents][:30]}", flush=True)
    if not ents or a.dry_run:
        return 0
    f = Path(a.inbox) / f"rescue_lost_{_now()}.json"
    _write_json_atomic(f, {"source": "rescue-lost", "before": str(a.before_dir), "after": str(a.after_root), "entries": ents})
    print(f"[intake] wrote {f}", flush=True)
    if a.push:
        r = subprocess.run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", str(f), f"{a.push}:binance-sandbox/data/template_row_queue_inbox/"], capture_output=True, text=True)
        print(f"[intake] push {a.push} rc={r.returncode} {r.stderr.strip()[:200]}", flush=True)
        return 0 if r.returncode == 0 else 2
    return 0


def md5_of(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_verify(a):
    q = load_queue(a.queue)
    root = Path(a.root)
    present = {rel: rows_by_switch(root / rel) for rel in TEMPLATE_RELS if (root / rel).exists()}
    rep = {"root": str(root), "md5": {rel: md5_of(root / rel) for rel in present}, "switches": {}}
    for e in q["entries"]:
        sw = str(e.get("switch", "")).upper()
        rep["switches"][sw] = {rel: f"{present[rel][sw]['tab']}:{len(present[rel][sw]['rows'])}" for rel in present if sw in present[rel]}
    rep["n_queued"] = len(q["entries"])
    rep["n_present_somewhere"] = sum(1 for v in rep["switches"].values() if v)
    out = json.dumps(rep, indent=1)
    if a.out:
        Path(a.out).write_text(out)
    print(out if not a.quiet else f"[intake] verify queued={rep['n_queued']} present_somewhere={rep['n_present_somewhere']}", flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge-inbox")
    m.add_argument("--queue", default=str(QUEUE))
    m.add_argument("--inbox", default=str(INBOX))
    m.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("rescue-lost")
    r.add_argument("--before-dir", required=True)
    r.add_argument("--after-root", default=str(ROOT))
    r.add_argument("--unflat", action="store_true", help="BEFORE dir mirrors the repo layout (default: flattened backup names a_b_c.xlsx)")
    r.add_argument("--inbox", default=str(INBOX))
    r.add_argument("--push")
    r.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("--queue", default=str(QUEUE))
    v.add_argument("--root", default=str(ROOT))
    v.add_argument("--out")
    v.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    return {"merge-inbox": cmd_merge_inbox, "rescue-lost": cmd_rescue_lost, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
