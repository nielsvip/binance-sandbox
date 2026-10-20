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
    """sym_side -> its publish rows, NEWEST FIRST (the latest publish is [0])."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute("select id, sym_side, updated_at, cat_side, winning_tag, gain_pct, baseline_gain, bh_pct, trades, pool_sharpe, max_dd_pct, tim_pct, valid, defaults_round, overrides_json, meta_json from per_sym_history where json_extract(meta_json, '$.source') = 'fleet_orchestrator_publish' order by updated_at, id").fetchall()  # 2026-10-14: latest = latest PUBLISH time (an after-the-fact insert must never win by id)
    c.close()
    latest = {}
    for r in rows:
        latest.setdefault(str(r[1]).upper(), []).insert(0, r)
    return latest


def build(db: Path, sheet_dir: Path, strict: bool = False) -> dict:
    """2026-10-16 FALLBACK TO THE NEWEST APPLICABLE PUBLISH: a refused latest row used to be published as the sym_side's
    selection anyway, which left live in a deadlock — the go-live applier SKIPs a refused board, so the live row stays on
    an older set, and live_twins.vec_exact.latest_board_ok then refuses every ENTRY for the sym_side because live != the
    selected board (measured: XRPUSDC_LONG SET_DRIFTED, live on its 10-11 board since, 45 keys off the 10-13 selection,
    entries refused the whole time; GOOGL_SHORT the same via MANIFEST_MD5_MISMATCH). A board that cannot be applied must
    not be the one live is measured against: we now walk that sym_side's publishes newest-first and select the first one
    that passes, recording in `fallback_from` what the newer refused row was and why. Nothing is loosened — a refused row
    is still never applied, and the age ceiling in latest_board_ok still refuses a fallback older than a week. Only when
    EVERY publish is refused does the newest refused row stay selected (unchanged behaviour, nothing applicable exists).
    V15_SEL_FALLBACK=0 restores selecting the latest row regardless."""
    import os

    out = {}
    for ss, cands in sorted(latest_published(db).items()):
        recs = []
        for r in cands if os.environ.get("V15_SEL_FALLBACK", "1") != "0" else cands[:1]:
            recs.append(_evaluate(ss, r, sheet_dir, strict))
            if not recs[-1].get("refuse"):
                break
        rec = next((x for x in recs if not x.get("refuse")), recs[0])
        if rec is not recs[0]:
            rec["fallback_from"] = {"published_at": recs[0].get("published_at"), "history_id": recs[0].get("history_id"), "refuse": recs[0].get("refuse"), "n_skipped": recs.index(rec)}
        out[ss] = rec
    return out


def _evaluate(ss: str, r, sheet_dir: Path, strict: bool) -> dict:
    """One publish row -> its selection record (verify / refuse / overrides / evidence)."""
    (hid, _sym, upd, cat, tag, gain, base, bh, trades, psh, dd, tim, valid, dround, ovj, metaj) = r
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
    # 2026-10-20 CARRY THE CELL DATE, not just the publish stamp. The gate (live_twins/vec_exact.latest_board_ok)
    # had only `published_at` (write-recency) and the run label in `progress`, so it took the OLDER of the two and
    # dated every board under v15_run32_20261011 to Oct 11 — the day that RUN STARTED. The new pilot keeps producing
    # fresh cells inside that same run directory, so 332 of 474 published sym_sides were refused BOARD_TOO_OLD at
    # 230 h while their cells were computed Oct 16-19 (distinct gain/trades per publish, campaign_ts == updated_at).
    # campaign_ts/epoch is stamped when the cells are COMPUTED and cannot move without recomputing, so it is the
    # honest vintage: it still catches a republish of old cells (the thing the correction exists for) without
    # punishing a long-lived run. CLAUDE.md: the board's date is the date its CELLS were computed.
    # Two publishers record the cell date under different names, measured 2026-10-20 on S1's per_sym_history:
    #   fleet_orchestrator_publish (the NEW pilot)  -> "result_done_utc": "2026-10-20T13:52:33.796017+00:00"
    #   the older publisher                          -> "campaign_ts": 1792425485.477219 (epoch)
    # Accept either; fall back to "recorded_at". NOT updated_at/epoch — those are write-recency and move on a
    # republish, which is the whole thing the vintage correction exists to catch.
    _cell_ts = None
    for _k in ("result_done_utc", "campaign_ts", "recorded_at"):
        _v = meta.get(_k)
        if _v in (None, ""):
            continue
        try:
            if isinstance(_v, (int, float)):
                _cell_ts = float(_v)
            else:
                import datetime as _dt
                _t = _dt.datetime.fromisoformat(str(_v).replace("Z", "+00:00"))
                _cell_ts = (_t if _t.tzinfo else _t.replace(tzinfo=_dt.timezone.utc)).timestamp()
            if _cell_ts and _cell_ts > 0:
                break
            _cell_ts = None
        except Exception:
            _cell_ts = None
    rec = {
        "history_id": hid,
        "published_at": upd,
        "cell_ts": _cell_ts,
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
    return rec


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
    doc = {"_meta": {"built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "by": "tools/v15_published_selection.py", "n": len(sel), "manifest_md5_ok": sum(1 for r in sel.values() if r.get("verify") == "MANIFEST_MD5_OK"), "history_row_only": sum(1 for r in sel.values() if str(r.get("verify", "")).startswith("HISTORY_PUBLISH_ROW")), "strict": bool(a.strict_manifest), "refused": sum(1 for r in sel.values() if r.get("refuse")), "fell_back_to_applicable": sum(1 for r in sel.values() if r.get("fallback_from"))}, "sym_sides": sel}
    p = Path(a.out)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, default=str))
    json.loads(tmp.read_text())
    tmp.replace(p)
    print(f"[published-selection] {doc['_meta']} -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
