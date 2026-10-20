#!/usr/bin/env python3
"""maxaug_purge — USER 2026-10-20 "MAX_AUGMENTS should have been deleted a long time ago from all templates".

MAX_AUGMENTS_PER_POSITION (and any MAX_AUGMENTS* key) was deleted from the engine 2026-10-15; nothing reads it. This tool drops
the DEAD KEY from the data surfaces, as a pure key removal (no value is changed, updated_at/campaign_ts are NOT touched, so no
set's age is re-stamped):
  * live per_sym store (per_sym_active: overrides_json / full_config_json / defaults_snapshot_json) + the two JSON views
  * kv_json rows per_sym_settings (cat_side defaults) and cat_side_promotions via per_sym_store.kv_put
  * data/per_sym_settings.json, data/cat_side_defaults_4.json, data/cat_side_promotions.json, data/per_sym_full_sets.json
  * --json-only FILE...  scrub arbitrary JSON files in place (fleet copies)
History rows (per_sym_history, avg_delta ledger, probes) are report-only: they record what was true when written.
Ledger tag maxaug_purge_20261020 -> data/reports/maxaug_purge_20261020_ledger.json.
Default is dry-run. --apply backs everything up to backups/before_maxaug_purge_<ts>_* first.
"""
import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TAG = "maxaug_purge_20261020"
PREFIX = "MAX_AUGMENTS"
EXACT = {"REENTRY_TIER2_MAX_MINUTES_TRADIER", "REENTRY_TIER2_MAX_MINUTES"}
LEDGER = ROOT / "data" / "reports" / "maxaug_purge_20261020_ledger.json"
TEXT_FILES = ["data/engine_only_knobs.txt", "data/vec_aware_knobs.txt"]
EXTRA_JSON = ["data/SWITCH_BIBLE.json", "data/switch_dependencies.json", "data/knob_registry.json", "data/v15_wired_switches.json", "data/opportune_filter_map.json"]
DATA_FILES = ["data/per_sym_settings.json", "data/cat_side_defaults_4.json", "data/cat_side_promotions.json", "data/per_sym_full_sets.json"]


def bad(k):
    return isinstance(k, str) and (k.startswith(PREFIX) or k in EXACT)


def scrub(o, lists=False):
    """remove every dict key starting MAX_AUGMENTS (any depth); returns the number removed"""
    n = 0
    if isinstance(o, dict):
        for k in [k for k in o if bad(k)]:
            del o[k]
            n += 1
        for v in o.values():
            n += scrub(v, lists)
    elif isinstance(o, list):
        if lists:
            keep = [v for v in o if not bad(v)]
            n += len(o) - len(keep)
            o[:] = keep
        for v in o:
            n += scrub(v, lists)
    return n


def count_keys(o, lists=False):
    n = 0
    if isinstance(o, dict):
        n += sum(1 for k in o if bad(k))
        n += sum(count_keys(v, lists) for v in o.values())
    elif isinstance(o, list):
        if lists:
            n += sum(1 for v in o if bad(v))
        n += sum(count_keys(v, lists) for v in o)
    return n


def write_json_atomic(path, doc, indent, sort_keys):
    tmp = path.with_name(path.name + ".maxaug_tmp")
    if indent == "lines":
        tmp.write_text("{\n" + ",\n".join(f"{json.dumps(k)}: {json.dumps(v, default=str)}" for k, v in doc.items()) + "\n}\n")
    else:
        tmp.write_text(json.dumps(doc, indent=indent, default=str, sort_keys=sort_keys))
    json.loads(tmp.read_text())
    tmp.replace(path)


def purge_json_file(path, apply, ts, ledger):
    path = Path(path)
    if not path.exists():
        ledger["files"][str(path)] = "absent"
        return
    text = path.read_text()
    if path.suffix == ".txt":
        lines = text.split("\n")
        before = sum(1 for l in lines if bad(l.strip()))
        if apply and before:
            shutil.copy2(path, ROOT / "backups" / f"before_maxaug_purge_{ts}_{path.name}")
            path.write_text("\n".join(l for l in lines if not bad(l.strip())))
        after = sum(1 for l in path.read_text().split("\n") if bad(l.strip())) if apply else "dry-run"
        ledger["files"][str(path)] = {"keys_before": before, "keys_after": after}
        print(f"[txt] {path} lines {before} -> {after}", flush=True)
        return
    doc = json.loads(text)
    lists = any(str(path).endswith(e) for e in EXTRA_JSON)
    before = count_keys(doc, lists)
    ind = "lines" if text.startswith('{\n"') else 1 if text.startswith("{\n ") and not text.startswith("{\n  ") else (2 if text.startswith("{\n  ") else None)
    if before:
        probe = json.loads(text)
        if ind == "lines":
            fmt_ok = True
        else:
            fmt_ok = any(json.dumps(probe, indent=ind, default=str, sort_keys=sk) == text.rstrip("\n") for sk in (False, True))
        if not fmt_ok:
            print(f"[file] {path} format does not round-trip (indent={ind}) - SKIPPED, handle by its builder", flush=True)
            ledger["files"][str(path)] = {"keys_before": before, "keys_after": before, "skipped": "format"}
            return
    if apply and before:
        shutil.copy2(path, ROOT / "backups" / f"before_maxaug_purge_{ts}_{path.name}")
        scrub(doc, lists)
        write_json_atomic(path, doc, ind, False)
    ledger["files"][str(path)] = {"keys_before": before, "keys_after": count_keys(json.loads(path.read_text()), lists) if apply else "dry-run"}
    print(f"[file] {path} keys {before} -> {ledger['files'][str(path)]['keys_after']}", flush=True)


def purge_store(apply, ts, ledger):
    import per_sym_store as pss
    con = sqlite3.connect(str(pss.DB_PATH), timeout=60.0)
    con.row_factory = sqlite3.Row
    cols = ("overrides_json", "full_config_json", "defaults_snapshot_json", "meta_json")
    per_col = {c: 0 for c in cols}
    rows_hit = []
    try:
        todo = []
        for r in con.execute("SELECT sym_side, " + ",".join(cols) + " FROM per_sym_active"):
            upd, hit = {}, False
            for c in cols:
                raw = r[c]
                if not raw:
                    continue
                txt = pss._maybe_decompress(raw)
                if PREFIX not in (txt or "") and not any(k in (txt or "") for k in EXACT):
                    continue
                doc = json.loads(txt)
                n = scrub(doc)
                if n:
                    hit = True
                    per_col[c] += 1
                    upd[c] = pss._maybe_compress(json.dumps(doc, sort_keys=True)) if c in ("full_config_json", "defaults_snapshot_json") and raw != txt else json.dumps(doc, sort_keys=True)
            if hit:
                rows_hit.append(r["sym_side"])
                todo.append((r["sym_side"], upd))
        print(f"[store] per_sym_active rows with key: {len(rows_hit)}  per-column {per_col}", flush=True)
        ledger["store_rows"] = len(rows_hit)
        ledger["store_per_column"] = per_col
        ledger["store_sym_sides"] = rows_hit
        if apply:
            shutil.copy2(pss.DB_PATH, ROOT / "backups" / f"before_maxaug_purge_{ts}_per_sym_store.db")
            for ss, upd in todo:
                sets = ",".join(f"{c}=?" for c in upd)
                con.execute(f"UPDATE per_sym_active SET {sets} WHERE sym_side=?", (*upd.values(), ss))
            con.commit()
    finally:
        con.close()
    for jp in (pss.CRYPTO_JSON, pss.STOCKS_JSON):
        purge_json_file(jp, apply, ts, ledger)
    for key in (pss.KV_CAT_SIDE_DEFAULTS_4, pss.KV_CAT_SIDE_PROMOTIONS):
        con = sqlite3.connect(str(pss.DB_PATH), timeout=60.0)
        try:
            row = con.execute("SELECT value_json FROM kv_json WHERE key=?", (key,)).fetchone()
        finally:
            con.close()
        raw = row[0] if row else None
        txt = pss._maybe_decompress(raw) if raw else None
        doc = json.loads(txt) if txt else None
        n = count_keys(doc) if doc is not None else 0
        ledger["kv"][key] = {"keys_before": n}
        print(f"[kv] {key} keys {n}", flush=True)
        if apply and n:
            scrub(doc)
            pss.kv_put(key, doc, compress=(raw != txt))
            again = pss.kv_get(key)
            ledger["kv"][key]["keys_after"] = count_keys(again)


SEED_TOP_KEYS = ("cumulative_overrides", "hustler_overrides")


def purge_progress(dirs, apply, ts, ledger):
    """Targeted scrub of the top-level seeding dicts in pilot progress JSONs.

    Only cumulative_overrides / hustler_overrides are touched (the sets a new
    board ingests as prior-best). Per-row done cells are evidence and stay.
    """
    n_files, n_keys = 0, 0
    for d in dirs:
        for path in sorted(Path(d).expanduser().glob("*_v14_progress.json")):
            try:
                doc = json.loads(path.read_text())
            except Exception:
                continue
            removed = 0
            for sk in SEED_TOP_KEYS:
                sub = doc.get(sk)
                if isinstance(sub, dict):
                    for k in [k for k in sub if bad(k)]:
                        del sub[k]
                        removed += 1
            if removed:
                n_files += 1
                n_keys += removed
                if apply:
                    shutil.copy2(path, ROOT / "backups" / f"before_maxaug_purge_{ts}_{path.name}")
                    text = path.read_text()
                    ind = 2 if text.startswith("{\n  ") else 1
                    write_json_atomic(path, doc, ind, False)
                print(f"[progress] {path} seed keys removed: {removed}", flush=True)
    ledger["progress_dirs"] = dirs
    ledger["progress_files"] = n_files
    ledger["progress_keys"] = n_keys if apply else "dry-run"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json-only", nargs="*", default=None)
    ap.add_argument("--progress", nargs="*", default=None,
                    help="scrub top-level seeding dicts in progress dirs (default: none)")
    a = ap.parse_args()
    ts = time.strftime("%Y%m%d%H%M")
    (ROOT / "backups").mkdir(exist_ok=True)
    ledger = {"tag": TAG, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "apply": a.apply, "files": {}, "kv": {}}
    if a.json_only is not None:
        for f in a.json_only:
            purge_json_file(f, a.apply, ts, ledger)
        return 0
    if a.progress is not None:
        purge_progress(a.progress, a.apply, ts, ledger)
        if a.apply:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            (LEDGER.parent / f"maxaug_purge_progress_{ts}.json").write_text(json.dumps(ledger, indent=1))
        return 0
    purge_store(a.apply, ts, ledger)
    for f in DATA_FILES + EXTRA_JSON + TEXT_FILES:
        purge_json_file(ROOT / f, a.apply, ts, ledger)
    if a.apply:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(ledger, indent=1))
        print(f"ledger -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
