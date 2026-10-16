#!/usr/bin/env python3
"""v15_live_sync_proof — provably sync live to the latest published backtest boards (Mac).

Per account (ang, fin, flz, inf, men — never tra/trb/trc stocks) x sym_side it compares:
  LIVE:    data/hourly_reconfig/per_sym_store.db per_sym_active.overrides_json, plus any
           per-account override files under data/hourly_reconfig/{fin,flz,inf,men}/
           (ang has no per-account dir: its live settings ARE the global store).
  BOARD:   the latest published set: a live build() from tools/v15_published_selection
           (latest per_sym_history row with meta source fleet_orchestrator_publish) when
           this host's store has publish rows (S1); otherwise the latest
           data/published_selection.json produced by that same tool (the Mac case —
           recorded in _meta.board_source, never hidden). Either way each board set is
           cross-checked against the LOCAL sheet manifests
           SPREADSHEETS/V15_V16_CELL_BY_CELL/*_manifest.json
           via md5(json.dumps(overrides, sort_keys=True)).

Trust tier per sym_side (honesty law — these names are the whole point):
  VERIFIED           live md5 == published md5 AND a LOCAL sheet manifest carries the same
                     md5 (MANIFEST_MD5_OK) AND robustness bars pass.
  APPLIED_UNVERIFIED live md5 == published md5 but the proof is weaker: no local manifest
                     with this md5 (HISTORY_PUBLISH_ROW only), or the robustness bars
                     fail, or the publish row was refused. Applied, not proven.
  DIVERGED           live md5 != published md5, or no live row, or no usable publish.
                     Every DIVERGED sym_side lands in the ALERT list.

Robustness bars (to count as VERIFIED the published evidence must satisfy all three):
  board trades >= 10, |max_dd_pct| <= 30, 20 <= tim_pct <= 80.
Sets with n_promoted == 1 (single promoted key: manifest counts.C on an md5 hit, else the
latest per_sym_history '... n_promoted=N' source for the sym_side) are flagged
SINGLE_KEY_FRAGILE (record-only). Whether fresh forward/OOS evidence exists (a
*7d_progress*, *365*, *oos*, *forward* or *walkforward* file for the sym_side under
data/reports/lifecycle_pilot) is recorded per sym_side but NEVER a blocker.

Read-only on every store, board, and manifest. Its ONLY write is data/live_sync_proof.json.
HONESTY LAW: a VERIFIED row means 'live == latest published board with sane evidence' —
never 'this will win', never 'no losing day'.

Usage:
  python3 tools/v15_live_sync_proof.py [--out data/live_sync_proof.json] [--dry-run]
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_published_selection import build as published_build
from tools.v15_published_selection import overrides_md5

DB = os.environ.get("PER_SYM_STORE_DB") or str(ROOT / "data" / "hourly_reconfig" / "per_sym_store.db")
SHEETS = str(ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL")
PUBLISHED_FALLBACK = str(ROOT / "data" / "published_selection.json")
PILOT_REPORTS = ROOT / "data" / "reports" / "lifecycle_pilot"

CRYPTO_ACCOUNTS = ["ang", "fin", "flz", "inf", "men"]
PER_ACCOUNT_DIRS = {"fin": "fin", "flz": "flz", "inf": "inf", "men": "men"}  # ang: no dir -> global store

MIN_TRADES, MAX_DD, TIM_LO, TIM_HI = 10, 30.0, 20.0, 80.0

# 2026-10-15: live==board must be judged on the keys that CAN be live, not on the board's raw set. The go-live path strips
# engine artefacts (MODE / SIMPLE_PRICE_GT0_ENABLED / VENUE / IS_TRADIER / ABLATION_*, USER 2026-10-08) and the no-cap
# mandate keys (MAX_AUGMENTS_PER_POSITION<1, MAX_ORDER_VALUE<=0, USER 2026-10-10/13) before applying, and switch_parity
# drops any key with no live reference. Comparing raw md5s therefore reported DIVERGED for every correctly applied board
# (401 of 735 sym_sides, 2005 false alerts at 02:36Z) — an honesty tool that cries wolf is worse than none. The comparison
# md5 is now taken over the applicable keys on both sides; the raw overrides_md5 still backs the manifest cross-check.
_ARTEFACT_KEYS = {"MODE", "SIMPLE_PRICE_GT0_ENABLED", "VENUE", "IS_TRADIER"}
_LIVE_KNOWN = None


def _live_known_keys():
    """Keys any decision surface can read: config.Config | TradierConfig | QuickConfig fields."""
    global _LIVE_KNOWN
    if _LIVE_KNOWN is None:
        keys = set()
        try:
            import dataclasses as _dc
            from config import Config as _CC
            from config_tradier import TradierConfig as _CT
            import v12_quick_engine as _V
            keys |= {k for k in dir(_CC()) if not k.startswith("_")}
            keys |= {k for k in dir(_CT()) if not k.startswith("_")}
            keys |= {f.name for f in _dc.fields(_V.QuickConfig)}
        except Exception:
            keys = set()
        _LIVE_KNOWN = keys
    return _LIVE_KNOWN


def _canon(v):
    """Representation-free value: switch_parity coerces every value to the LIVE field's type on registration
    (config_tradier encodes bools as 0.0/1.0 floats), so the live row holds True as 1.0 and 3 as 3.0 while the board's
    raw cell still holds True / 3 / "True". Those are the same setting; only the md5 of their JSON text differs."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 9)
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ("true", "false"):
            return 1.0 if s.lower() == "true" else 0.0
        try:
            return round(float(s), 9)
        except ValueError:
            return s
    return v


def applicable(ov: dict) -> dict:
    """The subset of an override set that the live surface can carry (go-live strip + known-key filter), canonicalised."""
    known = _live_known_keys()
    out = {}
    for k, v in (ov or {}).items():
        if k in _ARTEFACT_KEYS or str(k).startswith("ABLATION_"):
            continue
        if known and k not in known:
            continue
        if k == "MAX_AUGMENTS_PER_POSITION":
            try:
                if float(str(v).strip()) < 1:
                    continue
            except (TypeError, ValueError):
                pass
        if k == "MAX_ORDER_VALUE":
            try:
                if float(str(v).strip()) <= 0:
                    continue
            except (TypeError, ValueError):
                pass
        out[k] = _canon(v)
    return out


def applicable_md5(ov: dict) -> str:
    a = applicable(ov)
    return overrides_md5(a) if a else ""


def live_rows(db_path=DB):
    """{SYM_SIDE: {overrides, md5, updated_at, winning_tag}}. Read-only; {} on any error."""
    out = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20.0)
        rows = con.execute("SELECT sym_side, updated_at, winning_tag, overrides_json FROM per_sym_active").fetchall()
        con.close()
    except Exception:
        return {}
    for ss, ua, tag, ovj in rows:
        try:
            ov = json.loads(ovj or "{}")
        except Exception:
            ov = {}
        if not isinstance(ov, dict):
            ov = {}
        out[str(ss).upper()] = {"overrides": ov, "md5": applicable_md5(ov),
                                "updated_at": str(ua or ""), "winning_tag": str(tag or ""),
                                "n_overrides": len(ov)}
    return out


def per_account_overrides(acct: str):
    """{SYM_SIDE: overrides} from data/hourly_reconfig/<acct>/*.json[.gz]. {} when absent/empty."""
    out = {}
    d = ROOT / "data" / "hourly_reconfig" / PER_ACCOUNT_DIRS.get(acct, f"__none_{acct}")
    if not d.is_dir():
        return out
    for fp in sorted(d.glob("*.json*")):
        try:
            raw = gzip.open(fp, "rt").read() if fp.suffix == ".gz" else fp.read_text()
            doc = json.loads(raw)
        except Exception:
            continue
        items = doc.items() if isinstance(doc, dict) else []
        for k, v in items:
            if isinstance(v, dict) and ("overrides" in v or "overrides_json" in v):
                ov = v.get("overrides")
                if ov is None:
                    try:
                        ov = json.loads(v.get("overrides_json") or "{}")
                    except Exception:
                        ov = {}
                if isinstance(ov, dict) and ov:
                    out[str(k).upper()] = ov
    return out


def manifest_map(sheet_dir=SHEETS):
    """{SYM_SIDE: {md5: {manifest, published_utc, counts, metrics}}}. Read-only."""
    out = {}
    try:
        paths = sorted(Path(sheet_dir).glob("*_manifest.json"))
    except OSError:
        return {}
    for p in paths:
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        ss = str(m.get("symside") or "").upper()
        md5 = str(m.get("overrides_md5") or "")
        if not ss or not md5:
            continue
        out.setdefault(ss, {})[md5] = {"manifest": p.name, "published_utc": m.get("published_utc"),
                                       "counts": m.get("counts") or {}, "metrics": m.get("metrics") or {}}
    return out


def history_npromoted(db_path=DB):
    """{SYM_SIDE: latest n_promoted} from '... n_promoted=N' history sources. Read-only."""
    out = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20.0)
        rows = con.execute("SELECT sym_side, meta_json FROM per_sym_history WHERE meta_json LIKE '%n_promoted=%' ORDER BY updated_at, id").fetchall()
        con.close()
    except Exception:
        return {}
    for ss, meta in rows:
        try:
            nums = re.findall(r"n_promoted=(\d+)", str(meta or ""))
        except Exception:
            continue
        if nums:
            out[str(ss).upper()] = int(nums[-1])
    return out


def load_boards(db_path=DB, sheets=SHEETS, fallback_file=PUBLISHED_FALLBACK):
    """({SYM_SIDE: board}, provenance). Live build() first; file fallback when empty. Read-only."""
    try:
        live = published_build(Path(db_path), Path(sheets), strict=False)
    except Exception:
        live = {}
    live = {k: v for k, v in (live or {}).items() if not str(k).startswith("_") and isinstance(v, dict)}
    if live:
        return live, f"live_build:{db_path}"
    try:
        doc = json.loads(Path(fallback_file).read_text())
        boards = {str(k).upper(): v for k, v in (doc.get("sym_sides") or {}).items() if isinstance(v, dict)}
        meta = doc.get("_meta") or {}
        if boards:
            return boards, f"file:{fallback_file} built_at={meta.get('built_at')} by={meta.get('by')} n={meta.get('n')}"
    except Exception:
        pass
    return {}, f"none (live build empty, fallback {fallback_file} unreadable/empty)"


def forward_evidence_map():
    """{SYM_SIDE: [filenames]} for forward/OOS-ish report files. Record-only, never a blocker."""
    hits = {}
    try:
        names = os.listdir(PILOT_REPORTS)
    except OSError:
        return {}
    tags = ("7d_progress", "365", "oos", "forward", "walkforward")
    for n in names:
        ln = n.lower()
        if not any(t in ln for t in tags):
            continue
        ss = n.split("_7d_")[0].split("_365")[0].upper()
        for t in ("_V14_PROGRESS", "_PROGRESS"):
            ss = ss.split(t)[0]
        hits.setdefault(ss, []).append(n)
    return hits


def bars_ok(ev: dict):
    """Robustness bars on the published board's own evidence. Returns (ok, [failed bars])."""
    fails = []
    try:
        tr = ev.get("trades")
        if tr is None or int(tr) < MIN_TRADES:
            fails.append(f"trades={tr}<{MIN_TRADES}")
    except (TypeError, ValueError):
        fails.append("trades unreadable")
    try:
        dd = ev.get("max_dd_pct")
        if dd is None or abs(float(dd)) > MAX_DD:
            fails.append(f"dd={dd}>|{MAX_DD}|")
    except (TypeError, ValueError):
        fails.append("dd unreadable")
    try:
        tim = ev.get("tim_pct")
        if tim is None or not (TIM_LO <= float(tim) <= TIM_HI):
            fails.append(f"tim={tim} outside {TIM_LO}-{TIM_HI}")
    except (TypeError, ValueError):
        fails.append("tim unreadable")
    return (not fails), fails


def npromoted_for(ss: str, pub: dict, mans: dict, hist_np: dict):
    """n_promoted + its source: manifest counts.C on an md5 hit, else latest history row."""
    hit = (mans.get(ss) or {}).get(pub.get("overrides_md5") or "")
    if hit:
        try:
            return int((hit.get("counts") or {}).get("C")), "manifest"
        except (TypeError, ValueError):
            pass
    if ss in hist_np:
        return hist_np[ss], "history"
    return None, "unknown"


def build_proof(accounts=CRYPTO_ACCOUNTS, db_path=DB, sheets=SHEETS, fallback_file=PUBLISHED_FALLBACK):
    live = live_rows(db_path)
    boards, provenance = load_boards(db_path, sheets, fallback_file)
    mans = manifest_map(sheets)
    hist_np = history_npromoted(db_path)
    fwd = forward_evidence_map()
    acct_ovs = {a: per_account_overrides(a) for a in accounts}
    sym_sides = sorted(set(live) | set(boards))
    proof = {"_meta": {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "by": "tools/v15_live_sync_proof.py",
                       "accounts": list(accounts), "n_live": len(live), "n_published": len(boards),
                       "board_source": provenance,
                       "bars": f"trades>={MIN_TRADES} + |DD|<={MAX_DD} + TIM {TIM_LO}-{TIM_HI}"},
             "accounts": {}, "ALERT": []}
    for acct in accounts:
        rows = {}
        for ss in sym_sides:
            lv = live.get(ss)
            pub = boards.get(ss)
            pub_applicable_md5 = applicable_md5((pub or {}).get("overrides") or {})
            entry = {"tier": "DIVERGED", "reason": "", "live_md5": (lv or {}).get("md5", ""),
                     "pub_md5": pub_applicable_md5, "pub_raw_md5": (pub or {}).get("overrides_md5", ""),
                     "live_source": "global_store",
                     "verify": "", "n_overrides": (pub or {}).get("n_overrides", 0),
                     "n_promoted": None, "npromoted_source": "unknown",
                     "fragile": False, "bars": [], "forward_oos": False, "forward_files": []}
            aov = acct_ovs.get(acct, {}).get(ss)
            if aov:
                entry["live_md5"] = applicable_md5(aov)
                entry["live_source"] = f"per_account_dir:{PER_ACCOUNT_DIRS[acct]}"
                entry["n_overrides"] = len(aov)
            ff = fwd.get(ss, [])
            entry["forward_oos"] = bool(ff)
            entry["forward_files"] = sorted(ff)[:3]
            if lv is None and not aov:
                entry["reason"] = "NO_LIVE_SETTINGS"
            elif pub is None or pub.get("refuse"):
                entry["tier"] = "APPLIED_UNVERIFIED"
                entry["reason"] = f"PUBLISHED_{pub.get('refuse', 'MISSING')}" if pub else "NO_PUBLISHED_BOARD"
            elif entry["live_md5"] != pub_applicable_md5:
                entry["reason"] = "SET_MISMATCH live!=published (applicable keys)"
            else:
                ok, fails = bars_ok(pub.get("evidence") or {})
                entry["bars"] = fails
                npv, nps = npromoted_for(ss, pub, mans, hist_np)
                entry["n_promoted"], entry["npromoted_source"] = npv, nps
                if npv == 1:
                    entry["fragile"] = True
                if (mans.get(ss) or {}).get(pub.get("overrides_md5") or ""):
                    entry["verify"] = "MANIFEST_MD5_OK"
                else:
                    entry["verify"] = str(pub.get("verify") or "HISTORY_PUBLISH_ROW")
                if entry["verify"] == "MANIFEST_MD5_OK" and ok:
                    entry["tier"] = "VERIFIED"
                    entry["reason"] = "live==published board, local manifest md5 match, bars pass"
                else:
                    entry["tier"] = "APPLIED_UNVERIFIED"
                    why = []
                    if entry["verify"] != "MANIFEST_MD5_OK":
                        why.append(f"manifest={entry['verify']}")
                    if not ok:
                        why.append("bars:" + ",".join(fails))
                    entry["reason"] = "live==published but " + "; ".join(why)
                if entry["fragile"]:
                    entry["reason"] += " [SINGLE_KEY_FRAGILE record-only]"
            rows[ss] = entry
            if entry["tier"] == "DIVERGED":
                proof["ALERT"].append({"account": acct, "sym_side": ss, "reason": entry["reason"],
                                      "live_md5": entry["live_md5"], "pub_md5": entry["pub_md5"]})
        proof["accounts"][acct] = rows
    m = proof["_meta"]
    m["n_alert"] = len(proof["ALERT"])
    m["tiers"] = {}
    for acct, rows in proof["accounts"].items():
        c = {}
        for e in rows.values():
            c[e["tier"]] = c.get(e["tier"], 0) + 1
        m["tiers"][acct] = c
    return proof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "live_sync_proof.json"))
    ap.add_argument("--accounts", default=",".join(CRYPTO_ACCOUNTS))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    proof = build_proof([x.strip().lower() for x in a.accounts.split(",") if x.strip()])
    if a.dry_run:
        print(json.dumps({**proof["_meta"], "alert_sample": proof["ALERT"][:10]}, indent=1))
        return 0
    p = Path(a.out)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(proof, sort_keys=True))
    json.loads(tmp.read_text())
    tmp.replace(p)
    print(json.dumps({**proof["_meta"], "out": str(p)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
