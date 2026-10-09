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
CREATE VIEW IF NOT EXISTS fix_ranking AS SELECT f.id, f.kind, f.scope, f.target, f.status,
 (SELECT COUNT(*) FROM fix_outcomes o WHERE o.fix_id = f.id AND o.verdict = 'helped') AS helps,
 (SELECT COUNT(*) FROM fix_outcomes o WHERE o.fix_id = f.id AND o.verdict = 'hurt') AS hurts,
 (SELECT COUNT(*) FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.switch_key = f.target OR f.target LIKE '%' || c.switch_key || '%') AS evidence_n,
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

        cfg = pss.get_full_config(sym_side) or {}
        ov = cfg.get("overrides") or cfg.get("winning_set") or {}
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


def _parse_cell_key(k):
    try:
        tab, rest = str(k).split("!", 1)
        left, rkey = rest.split(":", 1)
        sw, cand = (left.split("=", 1) + [""])[:2]
        return tab.strip(), sw.strip(), cand.strip(), rkey.strip()
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
            if (
                isinstance(nd, (int, float))
                and not isinstance(nd, bool)
                and float(nd) < -1e-9
            ):
                deltas.append(("", float(nd)))
            for h, dt in (v.get("yellows") or {}).items():
                if (
                    isinstance(dt, (int, float))
                    and not isinstance(dt, bool)
                    and float(dt) < -1e-9
                ):
                    deltas.append((str(h).strip(), float(dt)))
            if not deltas:
                continue
            lid = _add_lesson(
                cx,
                _now(),
                "sweep_cell",
                f"{ss}|{k}",
                ss,
                cat_of(ss),
                {"tab": tab, "cand": cnd, "file": os.path.basename(f)},
            )
            if lid is None:
                continue
            n_les += 1
            for h, dt in deltas:
                _add_cause(cx, lid, sw, cnd, h, dt, dt, "direct_sweep", tab, k)
                n_cau += 1
    cx.commit()
    _propose_fixes(cx)
    _record_outcomes(cx)
    cx.commit()
    cx.close()
    return {"lessons": n_les, "causes": n_cau}


def _propose_fixes(cx):
    rows = cx.execute(
        "SELECT c.switch_key, SUM(c.weight), COUNT(DISTINCT l.sym_side), SUM(CASE WHEN c.attribution='direct_sweep' THEN 1 ELSE 0 END) FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.switch_key != '__sym_context__' GROUP BY c.switch_key"
    ).fetchall()
    for sw, w, nsyms, ndir in rows:
        w = float(w or 0.0)
        helps = cx.execute(
            "SELECT COUNT(*) FROM fixes f JOIN fix_outcomes o ON o.fix_id = f.id WHERE f.target = ? AND o.verdict = 'helped'",
            (sw,),
        ).fetchone()[0]
        if w <= -5.0 and int(nsyms or 0) >= 3 and int(ndir or 0) >= 3 and helps == 0:
            status = "confirmed"
        else:
            status = "proposed"
        cx.execute(
            "INSERT OR IGNORE INTO fixes(kind, scope, target, payload, status, reason, created) VALUES (?,?,?,?,?,?,?)",
            (
                "disable_switch",
                "global",
                sw,
                json.dumps({"net_weight": round(w, 3), "n_syms": nsyms}),
                status,
                f"net causal weight {w:.2f} over {nsyms} syms",
                _now(),
            ),
        )
        if status == "confirmed":
            cx.execute(
                "UPDATE fixes SET status = 'confirmed', reason = ? WHERE kind = 'disable_switch' AND scope = 'global' AND target = ?",
                (f"net causal weight {w:.2f} over {nsyms} syms", sw),
            )


def _record_outcomes(cx):
    for fid, sw in cx.execute(
        "SELECT id, target FROM fixes WHERE kind = 'disable_switch' AND status = 'confirmed'"
    ).fetchall():
        n = cx.execute(
            "SELECT COUNT(*) FROM fix_outcomes WHERE fix_id = ?", (fid,)
        ).fetchone()[0]
        if n:
            continue
        cx.execute(
            "INSERT INTO fix_outcomes(fix_id, ts, sym_side, metric_after, verdict) SELECT ?, MAX(l.ts), '', 0.0, 'neutral' FROM lessons l JOIN causes c ON c.lesson_id = l.id WHERE c.switch_key = ?",
            (fid, sw),
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
        rkey = rest.split(":", 1)[1]
        return f"{tab.strip()}!{rkey.strip()}@{str(header).strip()}"
    except Exception:
        return ""


def export_condemned(db_path=None, out_path=None, merge_path=None):
    cx = connect(db_path)
    sws = {}
    for sw, venue in cx.execute(
        "SELECT f.target, l.venue FROM fixes f JOIN causes c ON c.switch_key = f.target JOIN lessons l ON l.id = c.lesson_id WHERE f.kind = 'disable_switch' AND f.status = 'confirmed' GROUP BY f.target, l.venue"
    ):
        sws.setdefault(venue or "GLOBAL", set()).add(sw)
    switches = {cat: sorted(v) for cat, v in sws.items()}
    cells = {}
    for venue, rk, hdr, n, avg in cx.execute(
        "SELECT l.venue, c.rowkey, c.header, COUNT(*), AVG(c.delta) FROM causes c JOIN lessons l ON l.id = c.lesson_id WHERE c.attribution = 'direct_sweep' AND c.header != '' AND c.rowkey != '' GROUP BY l.venue, c.rowkey, c.header HAVING COUNT(*) >= 3 AND AVG(c.delta) < 0"
    ):
        ck = _cell_key(rk, hdr)
        if not ck:
            continue
        sw = rk.split("!", 1)[1].split("=", 1)[0] if "!" in rk and "=" in rk else ""
        helpt = cx.execute(
            "SELECT COUNT(*) FROM fixes f JOIN fix_outcomes o ON o.fix_id = f.id WHERE f.target = ? AND o.verdict = 'helped'",
            (sw,),
        ).fetchone()[0]
        if helpt:
            continue
        cells.setdefault(venue or "GLOBAL", {})[ck] = {
            "pos_sym": 0,
            "n_sym": n,
            "avg_delta": round(float(avg), 4),
            "causal": True,
        }
    payload = {
        "meta": {"at": _now(), "engine": "v15_causal_learner"},
        "switches": switches,
        "cells": cells,
        "fixes_proposed": [f for f in rank_fixes(cx) if f["status"] == "proposed"][:50],
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
