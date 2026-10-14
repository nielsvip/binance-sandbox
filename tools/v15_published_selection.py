#!/usr/bin/env python3
"""v15_published_selection — go-live source = the LATEST PUBLISHED board per sym_side (USER 2026-10-13: live trades EXACTLY the
latest vectorized best-outcome set for every sym_side that has results).

Runs ON S1 (owner of per_sym_history + the published sheets):
    python tools/v15_published_selection.py --out data/published_selection.json
Source: per_sym_history rows with meta_json.source == fleet_orchestrator_publish (tools/v15_settings_history.py writes one per
publish, full overrides_json + the board's own metrics). The latest row per sym_side wins. Each set is checked against the sheet
manifest(s) SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM_SIDE}_*_manifest.json: md5(json.dumps(overrides, sort_keys=True)) equal to a
manifest overrides_md5 = MANIFEST_MD5_OK. REFUSED: a manifest at/after this publish with another md5 (history row not the latest
publish), or an older manifest with the SAME gain and another md5 (set drifted since publish — set no longer matches evidence).
No manifest for this publish (workers do not sync manifests back; older manifests = previous publishes) = HISTORY_PUBLISH_ROW,
accepted unless --strict-manifest.
Evidence = the published board's own numbers (no fresh re-eval: the re-eval hit STALE_NPZ_SKIPPED under the NPZ JIT law and
refused 542/571 sets on 2026-10-13). Read-only on every store.
"""

import argparse
import datetime
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def overrides_md5(ov: dict) -> str:
    return hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()


def manifest_md5s(symside: str, sheet_dir: Path) -> dict:
    out = {}
    for p in sheet_dir.glob(f"{symside}_*_manifest.json"):
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        if str(m.get("symside", "")).upper() != symside:
            continue
        if m.get("overrides_md5"):
            out[str(m["overrides_md5"])] = {"manifest": p.name, "published_utc": m.get("published_utc"), "publish_class": m.get("publish_class"), "metrics": m.get("metrics")}
    return out


def latest_published(db: Path) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute("select id, sym_side, updated_at, cat_side, winning_tag, gain_pct, baseline_gain, bh_pct, trades, pool_sharpe, max_dd_pct, tim_pct, valid, defaults_round, overrides_json, meta_json from per_sym_history where json_extract(meta_json, '$.source') = 'fleet_orchestrator_publish' order by updated_at, id").fetchall()  # 2026-10-14: latest = latest PUBLISH time (an after-the-fact insert must never win by id)
    c.close()
    latest = {}
    for r in rows:
        latest[str(r[1]).upper()] = r
    return latest


def build(db: Path, sheet_dir: Path, strict: bool = False) -> dict:
    out = {}
    for ss, r in sorted(latest_published(db).items()):
        (hid, _, upd, cat, tag, gain, base, bh, trades, psh, dd, tim, valid, dround, ovj, metaj) = r
        try:
            ov = json.loads(ovj or "{}")
        except Exception:
            ov = {}
        try:
            meta = json.loads(metaj or "{}")
        except Exception:
            meta = {}
        om = overrides_md5(ov)
        mans = manifest_md5s(ss, sheet_dir)
        hit = mans.get(om)
        rec = {
            "history_id": hid,
            "published_at": upd,
            "cat_side": cat,
            "host": meta.get("host"),
            "progress": meta.get("progress"),
            "overrides_md5": om,
            "n_overrides": len(ov),
            "manifest": (hit or {}).get("manifest"),
            "overrides": ov,
            "evidence": {"valid": bool(valid), "invalid_reason": "" if valid else "published board flagged invalid", "gain_pct": gain, "trades": trades, "tim_pct": tim, "max_dd_pct": dd, "pool_sharpe": psh, "bh_pct": bh, "baseline_gain": base, "defaults_round": dround or "", "npz_id": meta.get("npz") or "", "source": "published_board"},
        }
        rec["verify"] = "MANIFEST_MD5_OK" if hit else ""
        if not ov:
            rec["refuse"] = "published row has no overrides"
        elif not hit:
            # Manifests are written on the PUBLISHING host and are not synced back from workers (s2/s5/s6), and an older manifest
            # belongs to a PREVIOUS publish. Only a manifest at/after this publish can contradict it; an older manifest with the
            # SAME gain but another md5 means this history row re-recorded that old publish after its set drifted -> refused.
            hp = _ts(upd)
            newer = [m for m in mans.values() if _ts(m.get("published_utc")) >= hp - 600]
            same_gain_old = [m for m in mans.values() if _same_gain((m.get("metrics") or {}).get("gain_pct"), gain)]
            if newer:
                rec["refuse"] = f"MANIFEST_MD5_MISMATCH: newer sheet manifest {newer[0].get('manifest')} ({newer[0].get('published_utc')}) has another set (md5 {om} not in {sorted(mans)[:3]}) — history row is not the latest publish"
            elif same_gain_old:
                rec["refuse"] = f"SET_DRIFTED_SINCE_PUBLISH: history set md5 {om} != manifest {same_gain_old[0].get('manifest')} with the SAME gain {gain} — set no longer matches its evidence"
            elif strict:
                rec["refuse"] = f"NO_MANIFEST_FOR_THIS_PUBLISH (strict): {len(mans)} older manifest(s) only" if mans else "NO_SHEET_MANIFEST for this sym_side (strict)"
            else:
                rec["verify"] = "HISTORY_PUBLISH_ROW" + (f" (only {len(mans)} older manifest(s) — previous publishes)" if mans else " (no manifest synced from the publishing host)")
        rec["manifest_ok"] = not rec.get("refuse")
        out[ss] = rec
    return out


def _ts(s) -> float:
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _same_gain(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"))
    ap.add_argument("--sheets", default=str(ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"))
    ap.add_argument("--out", default=str(ROOT / "data" / "published_selection.json"))
    ap.add_argument("--strict-manifest", action="store_true", help="refuse every publish without a same-md5 sheet manifest on this host")
    a = ap.parse_args()
    sel = build(Path(a.db), Path(a.sheets), strict=a.strict_manifest)
    doc = {"_meta": {"built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "by": "tools/v15_published_selection.py", "n": len(sel), "manifest_md5_ok": sum(1 for r in sel.values() if r.get("verify") == "MANIFEST_MD5_OK"), "history_row_only": sum(1 for r in sel.values() if str(r.get("verify", "")).startswith("HISTORY_PUBLISH_ROW")), "strict": bool(a.strict_manifest), "refused": sum(1 for r in sel.values() if r.get("refuse"))}, "sym_sides": sel}
    p = Path(a.out)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, default=str))
    json.loads(tmp.read_text())
    tmp.replace(p)
    print(f"[published-selection] {doc['_meta']} -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
