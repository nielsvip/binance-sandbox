"""v15 causal learner: every losing trade + every negative-delta filter/setting becomes a causal lesson.

Sources (all engine/log-produced, NO-LIES):
  live trades : data/per_trade_returns/{acct}_{day}.jsonl (pnl_pct/fees), data/decisions/decisions_{acct}_{day}.jsonl (exit context),
                DATA_DIR/history/{acct}/{SYM_SIDE}.jsonl (fills, g-tagged reasons)
  sweep cells : progress JSON done{} rows (delta/yellows/naked_delta) — negatives kept, never dropped

Store: SQLite data/causal_learner.db — lessons -> causes -> fixes -> fix_outcomes, ranked by fix_ranking view.
Consumers (existing files, no locked edits): data/causal_condemned.json merges into cell_evidence runtime
cells + disabled_switches_never_pos (chain step 4b distributes to all pilots).

Attribution honesty: sweep causes are direct (measured delta); live causes are contextual (active-set +
exit reason) and only corroborate — a live loss alone never condemns.
Usage:
  python3 tools/v15_causal_learner.py ingest-live [--day YYYYMMDD] [--db PATH]
  python3 tools/v15_causal_learner.py ingest-progress <pdir[,pdir2]> [--db PATH]
  python3 tools/v15_causal_learner.py export [--db PATH] [--out PATH] [--merge PATH]
  python3 tools/v15_causal_learner.py rank [--db PATH] [--sym-side SS] [--limit N]
  python3 tools/v15_causal_learner.py query --sym-side SS [--db PATH]
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = next(
    (
        p
        for p in [__import__("pathlib").Path(__file__).resolve().parent.parent]
        if (p / "config.py").exists()
    ),
    __import__("pathlib").Path("."),
)
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")
SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL UNIQUE, sym_side TEXT NOT NULL, venue TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS causes(id INTEGER PRIMARY KEY, lesson_id INTEGER NOT NULL REFERENCES lessons(id), switch_key TEXT NOT NULL, cand TEXT NOT NULL DEFAULT '', header TEXT NOT NULL DEFAULT '', delta REAL NOT NULL, weight REAL NOT NULL, attribution TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', rowkey TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_causes_sw ON causes(switch_key, attribution);
CREATE TABLE IF NOT EXISTS fixes(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL, target TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'proposed', reason TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, UNIQUE(kind, scope, target));
CREATE TABLE IF NOT EXISTS fix_outcomes(id INTEGER PRIMARY KEY, fix_id INTEGER NOT NULL REFERENCES fixes(id), ts TEXT NOT NULL, sym_side TEXT NOT NULL, metric_after REAL NOT NULL, verdict TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_outcomes_fix ON fix_outcomes(fix_id);
"""
RANK_VIEW = """
DROP VIEW IF EXISTS fix_ranking;
CREATE VIEW fix_ranking AS SELECT f.id, f.kind, f.scope, f.target, f.status,
 (SELECT COUNT(*) FROM fix_outcomes o WHERE o.fix_id = f.id AND o.verdict = 'helped') AS helps,
 (SELECT COUNT(*) FROM fix_outcomes o WHERE o.fix_id = f.id AND o.verdict = 'hurt') AS hurts,
 (SELECT COUNT(*) FROM causes c WHERE c.switch_key = f.target) AS evidence_n,
 (SELECT MAX(o.ts) FROM fix_outcomes o WHERE o.fix_id = f.id) AS last_seen FROM fixes f
"""


def cat_of(symside):
    s = (symside or "").upper()
    side = "SHORT" if s.endswith("_SHORT") else "LONG"
    base = s[: -len("_" + side)] if s.endswith("_" + side) else s
    return ("CRYPTO_" if base.endswith(CRYPTO_SUFFIX) else "STOCKS_") + side


def connect(db_path):
    db_path = str(db_path or (ROOT / "data" / "causal_learner.db"))
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    cx = sqlite3.connect(db_path)
    cx.executescript(SCHEMA)
    cx.executescript(RANK_VIEW)
    return cx


def _now():
    return datetime.now(timezone.utc).isoformat()


def _add_lesson(cx, ts, source, source_id, sym_side, venue, context):
    try:
        cur = cx.execute(
            "INSERT INTO lessons(ts, source, source_id, sym_side, venue, context) VALUES (?,?,?,?,?,?)",
            (ts, source, source_id, sym_side, venue, json.dumps(context, default=str)),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def _add_cause(
    cx,
    lesson_id,
    switch_key,
    cand,
    header,
    delta,
    weight,
    attribution,
    notes="",
    rowkey="",
):
    cx.execute(
        "INSERT INTO causes(lesson_id, switch_key, cand, header, delta, weight, attribution, notes, rowkey) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            lesson_id,
            switch_key or "",
            cand or "",
            header or "",
            float(delta),
            float(weight),
            attribution,
            notes,
            rowkey,
        ),
    )


def _live_set_switches(sym_side):
    try:
        sys.path.insert(0, str(ROOT))
        import per_sym_store as pss

        ov = pss.get_overrides(sym_side) or {}
        return sorted(ov.keys()) if isinstance(ov, dict) else []
    except Exception:
        return []


def ingest_live(db_path=None, day=None, root=None):
    root = __import__("pathlib").Path(root or ROOT)
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    cx = connect(db_path)
    n_les = n_cau = 0
    for f in sorted(
        glob.glob(str(root / "data" / "per_trade_returns" / f"*_{day}.jsonl"))
    ):
        acct = os.path.basename(f).split("_")[0]
        for ln in open(f):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            net = float(e.get("pnl_pct") or 0.0) - float(e.get("fees_pct") or 0.0)
            if net >= 0:
                continue
            sym = f"{e.get('symbol')}_{e.get('side')}"
            lid = _add_lesson(
                cx,
                str(e.get("ts")),
                "live_trade",
                f"{acct}|{e.get('ts')}|{sym}",
                sym,
                acct,
                {
                    "pnl_pct": e.get("pnl_pct"),
                    "fees_pct": e.get("fees_pct"),
                    "hold_minutes": e.get("hold_minutes"),
                    "qty": e.get("qty"),
                    "reason": e.get("reason"),
                },
            )
            if lid is None:
                continue
            n_les += 1
            for sw in _live_set_switches(sym) or ["__sym_context__"]:
                _add_cause(
                    cx,
                    lid,
                    sw,
                    "",
                    "",
                    net,
                    net * 0.2,
                    "live_context",
                    str(e.get("reason") or "")[:120],
                )
                n_cau += 1
    cx.commit()
    _propose_fixes(cx)
    cx.commit()
    cx.close()
    return {"lessons": n_les, "causes": n_cau}


_SW_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _parse_cell_key(k):
    # Real pilot keys: TAB!<rownum>:SWITCH=cand (cand may be empty). Also accepts canonical
    # TAB!SWITCH=cand (row number already dropped). Returns (tab, sw, cand, rkey).
    try:
        tab, rest = str(k).split("!", 1)
        if ":" in rest:
            left, right = rest.split(":", 1)
            if "=" in right:
                sw, cand = right.split("=", 1)
                rkey = left
            elif "=" in left:
                sw, cand = left.split("=", 1)
                rkey = right
            else:
                return "", "", "", ""
        elif "=" in rest:
            sw, cand = rest.split("=", 1)
            rkey = ""
        else:
            return "", "", "", ""
        sw = sw.strip()
        if not _SW_RE.match(sw):
            return "", "", "", ""
        return tab.strip(), sw, cand.strip(), rkey.strip()
    except Exception:
        return "", "", "", ""


def ingest_progress(pdirs, db_path=None, root=None):
    root = __import__("pathlib").Path(root or ROOT)
    pdirs = [p.strip() for p in str(pdirs).split(",") if p.strip()]
    cx = connect(db_path)
    n_les = n_cau = 0
    cand = [
        f
        for p in pdirs
        for f in glob.glob(
            os.path.join(
                str(root / p) if not os.path.isabs(p) else p, "*_progress.json"
            )
        )
    ]
    for f in sorted(cand):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ss = d.get("symside") or os.path.basename(f).replace("_v14_progress.json", "")
        for k, v in (d.get("done") or {}).items():
            if not isinstance(v, dict):
                continue
            tab, sw, cnd, _r = _parse_cell_key(k)
            if not sw:
                continue
            deltas = []
            nd = v.get("naked_delta", v.get("delta"))
            if isinstance(nd, (int, float)) and not isinstance(nd, bool):
                deltas.append(("", float(nd)))
            for h, dt in (v.get("yellows") or {}).items():
                if isinstance(dt, (int, float)) and not isinstance(dt, bool):
                    deltas.append((str(h).strip(), float(dt)))
            if not deltas:
                continue
            # NAME IDENTITY (USER 2026-10-09): row order reshuffles daily, so the lesson
            # key is canonical TAB!SW=cand (row number dropped) + campaign dir as the
            # measurement epoch. Same logical row, any rownum, same campaign = one lesson.
            ckey = f"{tab}!{sw}={cnd}"
            epoch = os.path.basename(os.path.dirname(os.path.abspath(f))) or "root"
            lid = _add_lesson(
                cx,
                _now(),
                "sweep_cell",
                f"{ss}|{ckey}|{epoch}",
                ss,
                cat_of(ss),
                {"tab": tab, "cand": cnd, "file": os.path.basename(f), "epoch": epoch},
            )
            if lid is None:
                continue
            n_les += 1
            for h, dt in deltas:
                _add_cause(cx, lid, sw, cnd, h, dt, dt, "direct_sweep", tab, ckey)
                n_cau += 1
    cx.commit()
    _propose_fixes(cx)
    _record_outcomes(cx)
    cx.commit()
    cx.close()
    return {"lessons": n_les, "causes": n_cau}


def _propose_fixes(cx):
    # Confirm bar (calibrated 2026-10-09 on 877k run-dir causes: 1134 -> 13 condemns):
    # net <= -10 over >=3 syms, >=3 strictly-negative direct, ZERO positive direct,
    # avg direct <= -0.5. ABLATION_* harness flags are never condemned.
    rows = cx.execute(
        "SELECT c.switch_key, SUM(c.weight), COUNT(DISTINCT l.sym_side), SUM(CASE WHEN c.attribution='direct_sweep' AND c.delta < -1e-9 THEN 1 ELSE 0 END), SUM(CASE WHEN c.attribution='direct_sweep' AND c.delta > 1e-9 THEN 1 ELSE 0 END), AVG(CASE WHEN c.attribution='direct_sweep' THEN c.delta END) FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.switch_key != '__sym_context__' GROUP BY c.switch_key"
    ).fetchall()
    hurt = {
        r[0]
        for r in cx.execute(
            "SELECT f.target FROM fixes f JOIN fix_outcomes o ON o.fix_id = f.id WHERE o.verdict = 'hurt'"
        ).fetchall()
    }
    confirmed_now = set()
    for sw, w, nsyms, nneg, npos, davg in rows:
        w = float(w or 0.0)
        nneg = int(nneg or 0)
        npos = int(npos or 0)
        davg = float(davg) if davg is not None else 0.0
        reason = (
            f"net {w:.2f} over {nsyms} syms, {nneg}neg/{npos}pos direct, avg {davg:.3f}"
        )
        if (
            w <= -10.0
            and int(nsyms or 0) >= 3
            and nneg >= 3
            and npos == 0
            and davg <= -0.5
            and not sw.startswith("ABLATION_")
            and sw not in hurt
        ):
            status = "confirmed"
            confirmed_now.add(sw)
        else:
            status = "proposed"
        cx.execute(
            "INSERT OR IGNORE INTO fixes(kind, scope, target, payload, status, reason, created) VALUES (?,?,?,?,?,?,?)",
            (
                "disable_switch",
                "global",
                sw,
                json.dumps(
                    {
                        "net_weight": round(w, 3),
                        "n_syms": nsyms,
                        "n_neg": nneg,
                        "n_pos": npos,
                        "avg_direct": round(davg, 4),
                    }
                ),
                status,
                reason,
                _now(),
            ),
        )
        if status == "confirmed":
            cx.execute(
                "UPDATE fixes SET status = 'confirmed', reason = ? WHERE kind = 'disable_switch' AND scope = 'global' AND target = ?",
                (reason, sw),
            )
    # Demote stale confirms (e.g. bar tightened since confirmation). Guard: skip targets with
    # post-confirm positive evidence — _record_outcomes owns those (hurt -> rejected with note).
    q = "UPDATE fixes SET status = 'proposed', reason = reason || ' | auto-demoted: bar no longer met' WHERE kind = 'disable_switch' AND status = 'confirmed' AND NOT EXISTS (SELECT 1 FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.switch_key = fixes.target AND c.attribution = 'direct_sweep' AND c.delta > 1e-9 AND l.ts > fixes.created)"
    params: tuple = ()
    if confirmed_now:
        q += f" AND target NOT IN ({','.join('?' for _ in confirmed_now)})"
        params = tuple(confirmed_now)
    cx.execute(q, params)


def _record_outcomes(cx):
    for fid, sw, created in cx.execute(
        "SELECT id, target, created FROM fixes WHERE kind = 'disable_switch' AND status = 'confirmed'"
    ).fetchall():
        pos = cx.execute(
            "SELECT COUNT(*), MAX(l.ts) FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.switch_key = ? AND c.attribution = 'direct_sweep' AND c.delta > 1e-9 AND l.ts > ?",
            (sw, created),
        ).fetchone()
        if (pos[0] or 0) > 0:
            cx.execute(
                "INSERT INTO fix_outcomes(fix_id, ts, sym_side, metric_after, verdict) VALUES (?,?,?,?,?)",
                (fid, pos[1], "", float(pos[0]), "hurt"),
            )
            cx.execute(
                "UPDATE fixes SET status = 'rejected', reason = reason || ' | auto-lifted: positive sweep evidence after condemn' WHERE id = ?",
                (fid,),
            )
        else:
            neg = cx.execute(
                "SELECT COUNT(*) FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.switch_key = ? AND c.delta < -1e-9 AND l.ts > ?",
                (sw, created),
            ).fetchone()[0]
            if (neg or 0) > 0 and cx.execute(
                "SELECT COUNT(*) FROM fix_outcomes WHERE fix_id = ? AND verdict = 'helped'",
                (fid,),
            ).fetchone()[0] == 0:
                cx.execute(
                    "INSERT INTO fix_outcomes(fix_id, ts, sym_side, metric_after, verdict) VALUES (?,?,?,?,?)",
                    (fid, _now(), "", float(neg), "helped"),
                )


def rank_fixes(cx, scope=None, limit=50):
    import math
    from datetime import datetime as _dt

    out = []
    for fid, kind, sc, tgt, st, helps, hurts, evn, last in cx.execute(
        "SELECT id, kind, scope, target, status, helps, hurts, evidence_n, last_seen FROM fix_ranking ORDER BY id"
    ).fetchall():
        if scope and sc != scope:
            continue
        rec = 0.0
        if last:
            try:
                age_d = max(
                    0.0,
                    (
                        _dt.now(timezone.utc) - _dt.fromisoformat(str(last))
                    ).total_seconds()
                    / 86400.0,
                )
                rec = 0.5 ** (age_d / 30.0)
            except Exception:
                rec = 0.0
        score = (
            2.0 * (helps or 0)
            - 3.0 * (hurts or 0)
            + math.log1p(evn or 0)
            + rec
            + (0.5 if str(sc).startswith("sym:") else 0.0)
        )
        out.append(
            {
                "id": fid,
                "kind": kind,
                "scope": sc,
                "target": tgt,
                "status": st,
                "helps": helps,
                "hurts": hurts,
                "evidence_n": evn,
                "score": round(score, 3),
            }
        )
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def _cell_key(rowkey, header):
    try:
        tab, rest = str(rowkey).split("!", 1)
        rkey = rest.split(":", 1)[1] if ":" in rest else rest
        return f"{tab.strip()}!{rkey.strip()}@{str(header).strip()}"
    except Exception:
        return ""


def _agg_pos_lookup(out_path):
    try:
        ap = os.path.join(
            os.path.dirname(str(out_path)) if out_path else str(ROOT / "data"),
            "avg_delta_pos_sym_cell.json",
        )
        d = json.load(open(ap)).get("cat_sides") or {}
        return {
            (cat, ck): (v.get("pos_sym") or 0)
            for cat, cells in d.items()
            for ck, v in (cells or {}).items()
        }
    except Exception:
        return {}


def _agg_pos_switches(agg):
    out = set()
    for (cat, ck), n in agg.items():
        if (n or 0) <= 0:
            continue
        try:
            sw = ck.split("!", 1)[1].split("@", 1)[0].split("=", 1)[0].strip()
        except Exception:
            continue
        if _SW_RE.match(sw):
            out.add((cat, sw))
    return out


def export_condemned(db_path=None, out_path=None, merge_path=None):
    cx = connect(db_path)
    out = str(out_path or (ROOT / "data" / "causal_condemned.json"))
    agg = _agg_pos_lookup(out)
    pos_sw = _agg_pos_switches(agg)
    sws = {}
    for sw, venue in cx.execute(
        "SELECT f.target, l.venue FROM fixes f JOIN causes c ON c.switch_key = f.target JOIN lessons l ON l.id = c.lesson_id WHERE f.kind = 'disable_switch' AND f.status = 'confirmed' GROUP BY f.target, l.venue"
    ):
        if (venue or "GLOBAL", sw) in pos_sw:
            continue
        sws.setdefault(venue or "GLOBAL", set()).add(sw)
    switches = {cat: sorted(v) for cat, v in sws.items()}
    helped = {
        r[0]
        for r in cx.execute(
            "SELECT f.target FROM fixes f JOIN fix_outcomes o ON o.fix_id = f.id WHERE o.verdict = 'helped'"
        ).fetchall()
    }
    cells = {}
    for venue, rk, hdr, n, nsyms, avg in cx.execute(
        "SELECT l.venue, c.rowkey, c.header, COUNT(*), COUNT(DISTINCT l.sym_side), AVG(c.delta) FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.attribution = 'direct_sweep' AND c.header != '' AND c.rowkey != '' GROUP BY l.venue, c.rowkey, c.header HAVING COUNT(*) >= 3 AND AVG(c.delta) <= -0.1 AND SUM(CASE WHEN c.delta > 1e-9 THEN 1 ELSE 0 END) = 0"
    ):
        ck = _cell_key(rk, hdr)
        if not ck:
            continue
        if agg.get((venue or "GLOBAL", ck), 0) > 0:
            continue
        sw = _parse_cell_key(rk)[1]
        if sw in helped:
            continue
        cells.setdefault(venue or "GLOBAL", {})[ck] = {
            "pos_sym": 0,
            "n_sym": nsyms,
            "n_evidence": n,
            "avg_delta": round(float(avg), 4),
            "causal": True,
        }
    # RESCUE shortlist (USER 2026-10-09): proposed switches that ALMOST confirm — heavy
    # negative evidence but blocked by 1-2 positives or a thin margin. These are the interesting
    # switches that deserve the ablation rerun (naked + selective filter-offs) instead of a kill.
    rescue = []
    for sw, w, nsyms, nneg, npos, davg in cx.execute(
        "SELECT c.switch_key, SUM(c.weight), COUNT(DISTINCT l.sym_side), SUM(CASE WHEN c.attribution='direct_sweep' AND c.delta < -1e-9 THEN 1 ELSE 0 END), SUM(CASE WHEN c.attribution='direct_sweep' AND c.delta > 1e-9 THEN 1 ELSE 0 END), AVG(CASE WHEN c.attribution='direct_sweep' THEN c.delta END) FROM causes c JOIN lessons l ON l.id = c.lesson_id JOIN fixes f ON f.target = c.switch_key AND f.kind = 'disable_switch' WHERE c.switch_key != '__sym_context__' AND f.status = 'proposed' GROUP BY c.switch_key HAVING SUM(c.weight) <= -10.0 AND COUNT(DISTINCT l.sym_side) >= 3 AND SUM(CASE WHEN c.attribution='direct_sweep' AND c.delta < -1e-9 THEN 1 ELSE 0 END) >= 3 AND SUM(CASE WHEN c.attribution='direct_sweep' AND c.delta > 1e-9 THEN 1 ELSE 0 END) BETWEEN 1 AND 2"
    ).fetchall():
        rescue.append(
            {
                "switch": sw,
                "net": round(float(w or 0.0), 2),
                "n_syms": int(nsyms or 0),
                "n_neg": int(nneg or 0),
                "n_pos": int(npos or 0),
                "avg_direct": round(float(davg or 0.0), 4),
            }
        )
    rescue.sort(key=lambda r: r["net"])
    payload = {
        "meta": {"at": _now(), "engine": "v15_causal_learner"},
        "switches": switches,
        "cells": cells,
        "fixes_proposed": [f for f in rank_fixes(cx) if f["status"] == "proposed"][:50],
        "rescue": rescue[:40],
    }
    if merge_path and os.path.exists(str(merge_path)):
        try:
            m = json.load(open(merge_path))
            ms = m.get("switches") or {}
            ms = ms if isinstance(ms, dict) else {"GLOBAL": ms}
            for cat, lst in ms.items():
                switches[cat] = sorted(set(switches.get(cat, [])) | set(lst or []))
            for cat, obj in (m.get("cells") or {}).items():
                obj = obj if isinstance(obj, dict) else {}
                for ck, cv in obj.items():
                    cells.setdefault(cat, {}).setdefault(ck, cv)
            payload["switches"] = switches
            payload["cells"] = cells
        except Exception:
            pass
    out = str(out_path or (ROOT / "data" / "causal_condemned.json"))
    tmp = out + ".tmp"
    json.dump(payload, open(tmp, "w"))
    os.replace(tmp, out)
    cx.close()
    return {
        "switches": len(payload["switches"]),
        "cells": sum(len(v) for v in payload["cells"].values()),
        "out": out,
    }


def query(sym_side, db_path=None, limit=20):
    cx = connect(db_path)
    mine = [
        r
        for r in rank_fixes(cx, limit=1000)
        if r["scope"] in ("global", f"sym:{sym_side}")
    ]
    cx.close()
    return mine[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "cmd", choices=["ingest-live", "ingest-progress", "export", "rank", "query"]
    )
    ap.add_argument("pdirs", nargs="?", default="")
    ap.add_argument("--db", default=None)
    ap.add_argument("--day", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--merge", default=None)
    ap.add_argument("--sym-side", default=None)
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "ingest-live":
        print(json.dumps(ingest_live(a.db, a.day)))
    elif a.cmd == "ingest-progress":
        print(json.dumps(ingest_progress(a.pdirs, a.db)))
    elif a.cmd == "export":
        print(json.dumps(export_condemned(a.db, a.out, a.merge)))
    elif a.cmd == "rank":
        print(json.dumps(rank_fixes(connect(a.db), limit=a.limit), indent=1))
    elif a.cmd == "query":
        print(json.dumps(query(a.sym_side, a.db, a.limit), indent=1))


if __name__ == "__main__":
    main()
