#!/usr/bin/env python3
"""paper_forward_promote — forward evidence -> live, per sym_side, with a paper trail.

USER 2026-10-22: "it should start applying live after 4 days of forward testing
history - any pos delta switch has to be added to live trading ... make another
sql and json for the forward test switch settings so they get inserted into
live automatically" + "a clear record [of] forward test and live behavior of
the switches (whether the pos deltas hold)".

Pipeline (all thresholds flags; defaults below):
  eligible = per-sym mean_delta > 0, scored >= --min-events (3),
             path observed >= --promote-min-days (4.0),
             gate kind bool/inv with a known knob that is actually parity-blocked,
             knob not protective, program armed (ledger age >= --promote-min-days).
  apply    = upsert eligible rows into forward_live_settings.db (table promoted,
             status active) + forward_live_settings.json mirror (the SQL+JSON the
             user asked for), append promotion_ledger.jsonl PROMOTED lines.
             Live picks them up via forward_live_settings.get() within 30 s.
             WITHOUT --apply-live this is a dry run (prints, writes nothing).
  revoke   = an active promotion whose POST-promotion forward delta (events with
             ts after promoted_at) scores <= 0 on >= --min-events is revoked
             (status revoked + REVOKED ledger line) — the "do deltas hold" rule.
  audit    = promotion_audit.md per promotion: predicted pre delta, post delta,
             live fills carrying the path token since promotion (adoption proof),
             verdict HOLDS / FADED (revoked) / NO_LIVE_FILLS / PENDING.

Never: global/cat_side defaults, disabling anything, protective families
(STOP/VIGILANCE/LIQUID/MARGIN/HEDGE/GUARANTEE/FORCE/SUBSTITUTION/RATIO/
BREAK_EVEN/EMERGENCY/KILL/DC_HARD_STOP), tf/unknown/safety gate kinds, or
applying on stale evidence (scores.json older than 30 h unless --allow-stale).
Rollback: --rollback PROMO_ID (or --rollback-all) revokes + ROLLED_BACK lines.

GATE-DISCIPLINE NOTE: this path ships switch changes without
tools/v15_version_gate.py submit on S2, per explicit USER 2026-10-22 order.
Mitigations: per-sym_side only, evidence-gated, protective exclusions above,
kill switches (FORWARD_AUTO_PROMOTE=0 / CONTROL.json), full ledger, auto-revoke.
Every promotion carries its evidence snapshot so it can be reverted.

Usage: python3 tools/paper_forward_promote.py [--apply-live] [--audit]
         [--promote-min-days 4.0] [--min-events 3] [--rollback ID] [--allow-stale]
Cron (daily; scoring already runs 6-hourly):
  10 6 * * * cd /Users/niels/Documents/binance && /opt/anaconda3/envs/binance_env/bin/python tools/paper_forward_promote.py --apply-live --audit >> /tmp/paper_forward_promote.log 2>&1 # PAPER_FORWARD_PROMOTE
"""

from __future__ import annotations

import argparse
import bisect
import glob
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import paper_forward_comprehensive as pfc  # noqa: E402

DIR = ROOT / "data" / "paper_forward_comprehensive"
PDB = DIR / "forward_live_settings.db"
PJS = DIR / "forward_live_settings.json"
LEDGER = DIR / "promotion_ledger.jsonl"

NEVER_AUTO = (
    "STOP",
    "VIGILANCE",
    "LIQUID",
    "MARGIN",
    "HEDGE",
    "GUARANTEE",
    "FORCE_CLOSE",
    "EXIT_FORCE",
    "SUBSTITUTION",
    "RATIO",
    "BREAK_EVEN",
    "EMERGENCY",
    "KILL",
    "DC_HARD_STOP",
    "RECONCIL",
    "BROKER_SYNC",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS promoted (
    promo_id    TEXT PRIMARY KEY,
    sym_side    TEXT,
    path        TEXT,
    venue       TEXT,
    kind        TEXT,
    knob        TEXT,
    value_json  TEXT,
    status      TEXT,
    promoted_at REAL,
    evidence_json TEXT,
    revoked_at  REAL
)"""


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _iso(ts: float = None) -> str:
    return datetime.fromtimestamp(
        ts if ts is not None else _now(), timezone.utc
    ).isoformat()


def load_scores(max_age_h: float, allow_stale: bool) -> dict:
    p = DIR / "scores.json"
    try:
        age_h = (_now() - p.stat().st_mtime) / 3600.0
    except OSError:
        print("no scores.json — run paper_forward_comprehensive.py first")
        sys.exit(2)
    if age_h > max_age_h and not allow_stale:
        print(f"REFUSED: scores.json {age_h:.1f}h old (> {max_age_h}h); evidence stale")
        sys.exit(2)
    d = json.loads(p.read_text())
    d["_age_h"] = age_h
    return d


def load_inventory() -> dict:
    try:
        return json.loads((DIR / "inventory.json").read_text())
    except Exception:
        return {}


def _protective(path: str, knob: str) -> bool:
    u = f"{path} {knob}".upper()
    return any(t in u for t in NEVER_AUTO)


def blocked_knobs() -> set:
    """Knobs actually forced off live today (the only ones a promotion can open)."""
    out = set(pfc._frozen_set_from_py(ROOT / "ez_manage.py", "_NON_VEC_KNOBS_EZ"))
    out |= set(
        pfc._frozen_set_from_py(ROOT / "ez_manage.py", "SUB15M_BLOCKED_SWITCHES")
    )
    out |= set(
        pfc._frozen_set_from_py(ROOT / "tradier_manage.py", "SUB15M_BLOCKED_SWITCHES")
    )
    return out


def gate_map() -> dict:
    """canon path -> (knob, kind) from families.py (first most-specific hit wins)."""
    m: dict = {}
    for r in pfc._families_registry():
        if not r["gate"]:
            continue
        pid = pfc.canon_family(r["family"] or r["token"])
        m.setdefault(pid, (r["gate"], r["kind"]))
    return m


def gate_lookup(gm: dict, path: str):
    """Exact canon hit, else longest registry-token prefix (rows like VEC_DRIVEN_
    only carry a prefix token; the observed path keeps its full name)."""
    if path in gm:
        return gm[path]
    best, bestn = None, -1
    for r in pfc._families_registry():
        if not r["gate"]:
            continue
        tok = pfc.canon_family(r["token"])
        if tok and path.startswith(tok) and len(tok) > bestn:
            best, bestn = (r["gate"], r["kind"]), len(tok)
    return best or (None, None)


def eligible(
    scores: dict, inv: dict, min_days: float, min_events: int, blocked: set = None
):
    """Yield promotion dicts (no writes)."""
    gm = gate_map()
    blocked = blocked if blocked is not None else blocked_knobs()
    out = []
    for r in scores.get("per_sym", []):
        if (
            r.get("mean_delta") is None
            or r["mean_delta"] <= 0
            or r.get("scored", 0) < min_events
        ):
            continue
        knob, gate_kind = gate_lookup(gm, r["path"])
        if not knob or gate_kind not in ("bool", "inv"):
            continue
        if _protective(r["path"], knob):
            continue
        if knob not in blocked:
            continue
        days = next(
            (
                p.get("days", 0)
                for p in scores.get("paths", [])
                if p["path"] == r["path"]
                and p["venue"] == r["venue"]
                and p["kind"] == r["kind"]
            ),
            0,
        )
        if days < min_days:
            continue
        value = True if gate_kind == "bool" else False
        out.append(
            {
                "sym_side": r["sym_side"],
                "path": r["path"],
                "venue": r["venue"],
                "kind": r["kind"],
                "knob": knob,
                "value": value,
                "gate_kind": gate_kind,
                "evidence": {
                    "mean_delta": r["mean_delta"],
                    "scored": r["scored"],
                    "events": r["events"],
                    "days": days,
                },
            }
        )
    return out


# ── store (SQL primary + JSON mirror) ──


def _connect():
    DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(PDB), timeout=30)
    con.execute(SCHEMA)
    return con


def read_promoted() -> dict:
    """promo_id -> row (active only). Never creates the DB (dry runs stay side-effect free)."""
    if PDB.exists():
        try:
            con = _connect()
            try:
                con.row_factory = sqlite3.Row
                return {
                    r["promo_id"]: dict(r)
                    for r in con.execute("SELECT * FROM promoted WHERE status='active'")
                }
            finally:
                con.close()
        except Exception:
            pass
    try:
        d = json.loads(PJS.read_text())
        return {
            r["promo_id"]: r
            for r in (d.get("promoted") or [])
            if r.get("status") == "active"
        }
    except Exception:
        return {}


def write_mirror(all_rows: list) -> None:
    PJS.write_text(
        json.dumps(
            {
                "generated_at": _iso(),
                "promoted": all_rows,
                "note": "mirror of forward_live_settings.db; live reads SQL first, this file second",
            },
            indent=1,
        )
    )


def _ledger(action: str, promo: dict, extra: dict = None) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": _iso(), "action": action, **promo}
    if extra:
        row.update(extra)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")


def apply(cands: list, live: bool) -> tuple:
    """Upsert candidates. Returns (applied, skipped_ids). Dry run unless live."""
    current = read_promoted()
    applied, skipped = [], []
    for c in cands:
        pid = f"{c['sym_side']}:{c['knob']}"
        if pid in current:
            skipped.append(pid)
            continue
        promo = {"promo_id": pid, **c, "promoted_at": _now()}
        if not live:
            applied.append(pid)
            continue
        con = _connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO promoted VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    c["sym_side"],
                    c["path"],
                    c["venue"],
                    c["kind"],
                    c["knob"],
                    json.dumps({"value": c["value"], "promo_id": pid}),
                    "active",
                    promo["promoted_at"],
                    json.dumps(c["evidence"]),
                    None,
                ),
            )
            con.commit()
            con.row_factory = sqlite3.Row
            all_rows = [dict(r) for r in con.execute("SELECT * FROM promoted")]
        finally:
            con.close()
        write_mirror(all_rows)
        _ledger(
            "PROMOTED",
            {
                k: promo[k]
                for k in (
                    "promo_id",
                    "sym_side",
                    "path",
                    "venue",
                    "kind",
                    "knob",
                    "value",
                    "gate_kind",
                    "evidence",
                    "promoted_at",
                )
            },
        )
        applied.append(pid)
    return applied, skipped


def revoke(promo_ids: list, reason: str, live: bool) -> list:
    done = []
    for pid in promo_ids:
        if not live:
            done.append(pid)
            continue
        con = _connect()
        try:
            con.execute(
                "UPDATE promoted SET status='revoked', revoked_at=? WHERE promo_id=? AND status='active'",
                (_now(), pid),
            )
            con.commit()
            con.row_factory = sqlite3.Row
            write_mirror([dict(r) for r in con.execute("SELECT * FROM promoted")])
        finally:
            con.close()
        _ledger(reason, {"promo_id": pid})
        done.append(pid)
    return done


# ── post-promotion evidence: do the deltas hold? ──


def post_delta(promo: dict, min_events: int):
    """Forward delta of promo path/sym_side from events AFTER promoted_at. (mean, scored) or (None, 0)."""
    events = pfc.load_events(days=60)
    vals = []
    for e in events:
        if e["_t"] < promo["promoted_at"]:
            continue
        pk = e.get("position_key", "")
        _, _, ss = pk.partition(":")
        if (
            ss != promo["sym_side"]
            or e["_fam"] != promo["path"]
            or e.get("kind") != promo["kind"]
        ):
            continue
        vals.append(e)
    if not vals:
        return None, 0
    venue = pfc.canon_venue(promo["venue"])
    symbol = promo["sym_side"].rsplit("_", 1)[0]
    is_long = promo["sym_side"].endswith("_LONG")
    scored = []
    if promo["kind"] == "exit":
        acct = promo.get("account") or vals[0].get("position_key", "").split(":")[0]
        rcs = pfc.real_closes(acct, promo["sym_side"])
        for e in vals:
            px, _ = pfc.close_at(venue, symbol, e["_t"])
            i = bisect.bisect_right([t for t, _ in rcs], e["_t"])
            if px and i < len(rcs) and rcs[i][1]:
                real = rcs[i][1]
                scored.append((px - real) / real if is_long else (real - px) / real)
    else:
        for e in vals:
            px, _ = pfc.close_at(venue, symbol, e["_t"])
            ch, _ = pfc.close_at(venue, symbol, e["_t"], offset=16)
            if px and ch:
                cost = pfc.COST.get(venue, 0.10) / 100.0
                s = 1.0 if is_long else -1.0
                scored.append(s * (ch - px) / px - cost)
    if len(scored) < min_events:
        return None, len(scored)
    return sum(scored) / len(scored), len(scored)


def live_fills_since(promo: dict):
    """Live fills carrying the path token in their reason since promoted_at (adoption proof)."""
    token = promo["path"]
    n = 0
    accts = (
        ("ang", "fin", "flz", "inf", "men")
        if promo["venue"] == "crypto"
        else ("tra", "trb", "trc")
    )
    for acct in accts:
        p = ROOT / "data" / "history" / acct / f"{promo['sym_side']}.jsonl"
        try:
            for ln in open(p, errors="replace"):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                try:
                    ts = pfc._ep(r.get("ts", ""))
                except Exception:
                    continue
                if ts >= promo["promoted_at"] and token in str(r.get("reason", "")):
                    n += 1
        except OSError:
            continue
    return n


def audit(min_events: int) -> list:
    rows = []
    for pid, pr in read_promoted().items():
        try:
            promo = {
                "promo_id": pid,
                "sym_side": pr["sym_side"],
                "path": pr["path"],
                "venue": pr["venue"],
                "kind": pr["kind"],
                "knob": pr["knob"],
                "promoted_at": pr["promoted_at"],
                "evidence": json.loads(pr["evidence_json"] or "{}"),
            }
        except Exception:
            continue
        post, n = post_delta(promo, min_events)
        fills = live_fills_since(promo)
        pre = (promo["evidence"] or {}).get("mean_delta")
        if post is None:
            verdict = "PENDING"
        elif post > 0:
            verdict = "HOLDS"
        else:
            verdict = "FADED"
        rows.append(
            {
                "promo_id": pid,
                "sym_side": promo["sym_side"],
                "path": promo["path"],
                "knob": promo["knob"],
                "pre_delta": pre,
                "post_delta": post,
                "post_scored": n,
                "live_fills": fills,
                "verdict": verdict,
            }
        )
    rows.sort(key=lambda r: (r["verdict"] != "FADED", r["promo_id"]))
    lines = [
        f"# Forward promotion audit — {_iso()}",
        "",
        "| promo | sym_side | path | pre Δ | post Δ (n) | live fills | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        pre = f"{r['pre_delta']:+.4f}" if r["pre_delta"] is not None else "n/a"
        post = (
            f"{r['post_delta']:+.4f} (n={r['post_scored']})"
            if r["post_delta"] is not None
            else f"pending (n={r['post_scored']})"
        )
        lines.append(
            f"| {r['promo_id']} | {r['sym_side']} | {r['path']} | {pre} | {post} | {r['live_fills']} | {r['verdict']} |"
        )
    if not rows:
        lines.append("| (no live promotions yet) | | | | | | |")
    (DIR / "promotion_audit.md").write_text("\n".join(lines) + "\n")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-live", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--promote-min-days", type=float, default=4.0)
    ap.add_argument("--min-events", type=int, default=3)
    ap.add_argument("--max-age-h", type=float, default=30.0)
    ap.add_argument("--allow-stale", action="store_true")
    ap.add_argument("--rollback", default=None)
    ap.add_argument("--rollback-all", action="store_true")
    a = ap.parse_args()
    if a.rollback or a.rollback_all:
        ids = [a.rollback] if a.rollback else list(read_promoted())
        done = revoke(ids, "ROLLED_BACK", a.apply_live)
        print(
            f"[promote] rollback {'DRY-RUN ' if not a.apply_live else ''}{len(done)}: {done[:10]}"
        )
        return
    scores = load_scores(a.max_age_h, a.allow_stale)
    inv = load_inventory()
    cands = eligible(scores, inv, a.promote_min_days, a.min_events)
    ledger_age_d = 0.0
    try:
        f0 = sorted(glob.glob(str(pfc.NONVEC_DIR / "events_*.jsonl")))[0]
        first = min(
            pfc._ep(json.loads(ln)["ts"])
            for ln in open(f0, errors="replace")
            if ln.strip().startswith("{")
        )
        ledger_age_d = (_now() - first) / 86400.0
    except Exception:
        pass
    armed = ledger_age_d >= a.promote_min_days
    if not armed:
        print(
            f"[promote] NOT ARMED: forward ledger {ledger_age_d:.1f}d < {a.promote_min_days}d — no live writes (dry list: {len(cands)} candidates)"
        )
        for c in cands[:15]:
            print(
                f"  would promote {c['sym_side']}:{c['knob']}={c['value']} ({c['path']} Δ={c['evidence']['mean_delta']:+.4f} n={c['evidence']['scored']})"
            )
    else:
        applied, skipped = apply(cands, a.apply_live)
        print(
            f"[promote] {'LIVE' if a.apply_live else 'DRY-RUN'} armed ({ledger_age_d:.1f}d): {len(applied)} applied/new, {len(skipped)} already live: {applied[:15]}"
        )
    if a.audit:
        rows = audit(a.min_events)
        faded = [r["promo_id"] for r in rows if r["verdict"] == "FADED"]
        holds = sum(1 for r in rows if r["verdict"] == "HOLDS")
        if faded and armed and a.apply_live:
            revoke(faded, "REVOKED", True)
            print(f"[promote] auto-revoked {len(faded)} faded: {faded[:10]}")
        print(
            f"[promote] audit: {len(rows)} live promotions, {holds} HOLDS, {len(faded)} FADED -> promotion_audit.md"
        )


if __name__ == "__main__":
    main()
