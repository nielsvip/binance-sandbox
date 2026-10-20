#!/usr/bin/env python3
"""augment_family_tables — AUGMENT / REDUCE / REENTRY family tables per cat_side for the self-learning loop (USER 2026-10-20:
"revise the self-learning to include all the augment and partial-reduce and reentry paths, not just enter and exit").

The exit-loss ranker (tools/v18_exit_loss_rank.py) and the precision cards (tools/v18_precision_build.py) rank EXIT families only.
This tool reads the SAME board trade ledgers (data/learn/trade_ledgers/<CAT_SIDE>/*.jsonl, tools/v18_ledger_replay.py: one real
include_ledger eval of each latest published final set) and writes three family tables, plus a repair menu built ONLY from
paired real-engine evals (tools/augment_family_probe.py JSON outputs: board set vs board set + one family change, same frozen NPZ).

AUGMENT   trades, trades with >= 1 augment (n_aug), autopsy MISSED_AUGMENT; per trigger T in 2.5/3/4.5/6: trades whose MFE reached
          T with 0 augments ("missed"), and of those how many closed ABOVE T (an add at T would have closed green) vs at/below T
          (an add at T would have closed red — the add is what turns a winner's giveback into a loss). Counts and the realised
          minus T spread only; NO simulated $ (an add changes the peak cost basis — only the probe's engine runs measure gain).
REDUCE    rows the engine closed through a reduce path (type REDUCE: REDUCE_TO_FLAT, PPL_TP, ...) by exit family: n, sum pnl_pp,
          giveback (MFE - realised) of the winners, post-exit 32-bar continuation. Partial reduces that do not flatten carry no
          pnl row in the replay corpus; their count per family comes from the probe ("n_partial_reduces", "RED:<src>").
REENTRY   reentry trades by entry family (n, WR, sum pnl_pp); continuation after exit: trades whose post-exit 32-bar move
          >= max(1 %, realised) (the autopsy PREMATURE rule), split by whether the board re-entered within 32 bars, and what that
          next trade earned.
REPAIR    data/learn/augment_family_repair_menu_<CS>.json: probe variant -> overrides, boards measured, boards positive, mean / best
          delta (pct-points of board gain, same NPZ, same set). Source "measured_probe" only; nothing is inferred.

usage: python3 tools/augment_family_tables.py [--ledgers data/learn/trade_ledgers] [--probes DIR] [--out-dir data/learn] [--apply]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
TRIGGERS = (2.5, 3.0, 4.5, 6.0)
POST_BARS_NOTE = "post_exit_32b_pct (replay corpus field)"


def cat_side_of(ss: str) -> str:
    sym, side = ss.rsplit("_", 1)
    crypto = any(sym.endswith(x) for x in ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
    return ("CRYPTO_" if crypto else "STOCKS_") + side


def _f(v, d=0.0):
    try:
        x = float(v)
        return x if x == x else d
    except (TypeError, ValueError):
        return d


def load_boards(ledger_root: Path, cs: str):
    boards = []
    for f in sorted(glob.glob(str(ledger_root / cs / "*.jsonl"))):
        meta, rows = None, []
        for line in open(f):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("kind") == "meta":
                meta = d
            elif d.get("kind") == "trade":
                rows.append(d)
        if meta and meta.get("valid") is not False and rows:
            rows.sort(key=lambda r: (int(r.get("bar_entry") or 0), int(r.get("trade_no") or 0)))
            boards.append((meta, rows))
    return boards


def augment_table(boards):
    t = {"boards": len(boards), "trades": 0, "trades_with_augment": 0, "autopsy_missed_augment": 0, "by_trigger": {}}
    trig = {T: {"reached_no_aug": 0, "closed_above_T": 0, "closed_at_or_below_T": 0, "spread_sum": 0.0, "lost_by_exit_family": defaultdict(int), "boards": set()} for T in TRIGGERS}
    for meta, rows in boards:
        for r in rows:
            t["trades"] += 1
            if int(r.get("n_aug") or 0) > 0:
                t["trades_with_augment"] += 1
                continue
            if "MISSED_AUGMENT" in (r.get("autopsy_cls") or []):
                t["autopsy_missed_augment"] += 1
            mfe, real = _f(r.get("mfe_pct")), _f(r.get("pnl_pct"))
            for T in TRIGGERS:
                if mfe >= T:
                    b = trig[T]
                    b["reached_no_aug"] += 1
                    b["boards"].add(meta["sym_side"])
                    b["spread_sum"] += real - T
                    if real > T:
                        b["closed_above_T"] += 1
                    else:
                        b["closed_at_or_below_T"] += 1
                        b["lost_by_exit_family"][str(r.get("exit_family") or "?")] += 1
    for T, b in trig.items():
        n = b["reached_no_aug"]
        t["by_trigger"][str(T)] = {"reached_no_aug": n, "boards": len(b["boards"]), "closed_above_T": b["closed_above_T"], "closed_at_or_below_T": b["closed_at_or_below_T"], "share_add_green": round(b["closed_above_T"] / n, 4) if n else None, "mean_realised_minus_T_pct": round(b["spread_sum"] / n, 4) if n else None, "top_exit_families_after_reaching_T": sorted(b["lost_by_exit_family"].items(), key=lambda x: -x[1])[:6]}
    t["share_with_augment"] = round(t["trades_with_augment"] / t["trades"], 4) if t["trades"] else None
    return t


def reduce_table(boards):
    fam = defaultdict(lambda: {"n": 0, "sum_pnl_pp": 0.0, "winners": 0, "giveback_sum": 0.0, "post_exit_sum": 0.0, "boards": set()})
    for meta, rows in boards:
        for r in rows:
            if str(r.get("type")) != "REDUCE":
                continue
            k = str(r.get("exit_family") or "?")
            k = k.split("_g=")[0]
            f = fam[k]
            f["n"] += 1
            f["boards"].add(meta["sym_side"])
            f["sum_pnl_pp"] += _f(r.get("pnl_pp"))
            f["post_exit_sum"] += _f(r.get("post_exit_32b_pct"))
            if _f(r.get("pnl_pct")) > 0:
                f["winners"] += 1
                f["giveback_sum"] += _f(r.get("mfe_pct")) - _f(r.get("pnl_pct"))
    out = {}
    for k, f in sorted(fam.items(), key=lambda x: -x[1]["n"]):
        out[k] = {"n": f["n"], "boards": len(f["boards"]), "sum_pnl_pp": round(f["sum_pnl_pp"], 3), "wr": round(f["winners"] / f["n"], 4), "mean_giveback_winners_pct": round(f["giveback_sum"] / f["winners"], 4) if f["winners"] else None, "mean_post_exit_32b_pct": round(f["post_exit_sum"] / f["n"], 4)}
    return out


def reentry_table(boards):
    fam = defaultdict(lambda: {"n": 0, "win": 0, "sum_pnl_pp": 0.0, "boards": set()})
    cont = {"premature_exits": 0, "reentered_within_32b": 0, "reentered_pnl_pp": 0.0, "reentered_wins": 0, "not_reentered": 0, "not_reentered_post_exit_sum_pct": 0.0}
    for meta, rows in boards:
        for i, r in enumerate(rows):
            if "REENTR" in str(r.get("entry_reason", "")).upper():
                f = fam[str(r.get("entry_family") or "?")]
                f["n"] += 1
                f["boards"].add(meta["sym_side"])
                f["sum_pnl_pp"] += _f(r.get("pnl_pp"))
                f["win"] += int(_f(r.get("pnl_pct")) > 0)
            post, real = _f(r.get("post_exit_32b_pct")), _f(r.get("pnl_pct"))
            if post >= max(1.0, real):
                cont["premature_exits"] += 1
                nxt = rows[i + 1] if i + 1 < len(rows) else None
                if nxt is not None and 0 <= int(nxt.get("bar_entry") or 0) - int(r.get("bar_exit") or 0) <= 32:
                    cont["reentered_within_32b"] += 1
                    cont["reentered_pnl_pp"] += _f(nxt.get("pnl_pp"))
                    cont["reentered_wins"] += int(_f(nxt.get("pnl_pct")) > 0)
                else:
                    cont["not_reentered"] += 1
                    cont["not_reentered_post_exit_sum_pct"] += post
    out = {k: {"n": f["n"], "boards": len(f["boards"]), "wr": round(f["win"] / f["n"], 4), "sum_pnl_pp": round(f["sum_pnl_pp"], 3)} for k, f in sorted(fam.items(), key=lambda x: -x[1]["n"])}
    c = cont
    c["reentered_pnl_pp"] = round(c["reentered_pnl_pp"], 3)
    c["reentered_wr"] = round(c["reentered_wins"] / c["reentered_within_32b"], 4) if c["reentered_within_32b"] else None
    c["not_reentered_mean_post_exit_pct"] = round(c["not_reentered_post_exit_sum_pct"] / c["not_reentered"], 4) if c["not_reentered"] else None
    c.pop("not_reentered_post_exit_sum_pct")
    return {"by_entry_family": out, "continuation_after_exit": c}


def repair_menu(probe_dir: Path):
    menus = {cs: defaultdict(lambda: {"family": "", "overrides": None, "env": None, "cap": None, "boards": [], "deltas": []}) for cs in CAT_SIDES}
    meta = {cs: [] for cs in CAT_SIDES}
    for f in sorted(glob.glob(str(probe_dir / "*.json"))):
        try:
            j = json.loads(Path(f).read_text())
        except Exception:
            continue
        if "rows" not in j or "sym_side" not in j:
            continue
        cs = cat_side_of(j["sym_side"])
        meta[cs].append({"sym_side": j["sym_side"], "frozen_npz_md5": j.get("frozen_npz_md5"), "published_npz_md5": j.get("published_npz_md5"), "engine_md5": j.get("engine_md5"), "measured_utc": j.get("measured_utc")})
        for r in j["rows"]:
            if r.get("family") == "BASE" or not r.get("valid"):
                continue
            m = menus[cs][r["label"]]
            m.update(family=r["family"], overrides=r.get("overrides"), env=r.get("env"), cap=r.get("cap"))
            m["boards"].append(j["sym_side"])
            m["deltas"].append(round(float(r.get("delta_vs_base") or 0.0), 4))
    out = {}
    for cs, mm in menus.items():
        rows = []
        for label, m in mm.items():
            d = m["deltas"]
            rows.append({"label": label, "family": m["family"], "overrides": m["overrides"], "env": m["env"], "measurement_only_cap": m["cap"], "source": "measured_probe", "support_n": len(d), "support_pos": sum(1 for x in d if x > 1e-9), "support_zero": sum(1 for x in d if abs(x) <= 1e-9), "mean_delta_pp": round(sum(d) / len(d), 4) if d else None, "best_delta_pp": max(d) if d else None, "per_board": dict(zip(m["boards"], d))})
        rows.sort(key=lambda r: (-(r["support_pos"]), -(r["mean_delta_pp"] or -1e9)))
        out[cs] = {"boards": meta[cs], "paths": rows}
    return out


def md(cs, a, rd, rt, menu):
    L = [f"# {cs} — AUGMENT / REDUCE / REENTRY family tables", "", f"built {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} from {a['boards']} board ledgers (data/learn/trade_ledgers/{cs}); tools/augment_family_tables.py", ""]
    L += ["## AUGMENT", "", f"trades {a['trades']}, with >= 1 augment {a['trades_with_augment']} ({(a['share_with_augment'] or 0) * 100:.2f} %), autopsy MISSED_AUGMENT {a['autopsy_missed_augment']}", "", "| trigger T % | trades reached T with 0 augments | boards | closed above T (add green) | closed at/below T (add red) | mean realised - T (pp of price) | where those moves were lost (exit family: n) |", "|---|---|---|---|---|---|---|"]
    for T, b in a["by_trigger"].items():
        L.append(f"| {T} | {b['reached_no_aug']} | {b['boards']} | {b['closed_above_T']} | {b['closed_at_or_below_T']} | {b['mean_realised_minus_T_pct']} | {', '.join(f'{k}: {v}' for k, v in b['top_exit_families_after_reaching_T'])} |")
    L += ["", "## REDUCE (rows closed through a reduce path; partial reduces: see the probe counts)", "", "| exit family | n | boards | sum pnl pp | WR | mean giveback of winners % | mean post-exit 32b % |", "|---|---|---|---|---|---|---|"]
    for k, f in list(rd.items())[:12]:
        L.append(f"| {k} | {f['n']} | {f['boards']} | {f['sum_pnl_pp']} | {f['wr']} | {f['mean_giveback_winners_pct']} | {f['mean_post_exit_32b_pct']} |")
    L += ["", "## REENTRY", "", "| entry family | n | boards | WR | sum pnl pp |", "|---|---|---|---|---|"]
    for k, f in list(rt["by_entry_family"].items())[:12]:
        L.append(f"| {k} | {f['n']} | {f['boards']} | {f['wr']} | {f['sum_pnl_pp']} |")
    c = rt["continuation_after_exit"]
    L += ["", f"continuation after exit (post-exit 32b >= max(1 %, realised)): {c['premature_exits']} exits; re-entered within 32 bars {c['reentered_within_32b']} (next trade WR {c['reentered_wr']}, sum {c['reentered_pnl_pp']} pp); not re-entered {c['not_reentered']} (mean post-exit move {c['not_reentered_mean_post_exit_pct']} %)", ""]
    L += ["## REPAIR MENU (measured_probe only: same set, same frozen NPZ, one family change; Δ in pct-points of board gain)", "", "| family | path | boards | positive | zero | mean Δ | best Δ |", "|---|---|---|---|---|---|---|"]
    for r in (menu or {}).get("paths", [])[:40]:
        L.append(f"| {r['family']} | {r['label']} | {r['support_n']} | {r['support_pos']} | {r['support_zero']} | {r['mean_delta_pp']} | {r['best_delta_pp']} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledgers", default=str(ROOT / "data" / "learn" / "trade_ledgers"))
    ap.add_argument("--probes", default="")
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "learn"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    menus = repair_menu(Path(a.probes).expanduser()) if a.probes else {}
    out_dir = Path(a.out_dir)
    for cs in CAT_SIDES:
        boards = load_boards(Path(a.ledgers), cs)
        at, rd, rt = augment_table(boards), reduce_table(boards), reentry_table(boards)
        doc = {"schema": "augment_family_tables/1", "cat_side": cs, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source_ledgers": str(Path(a.ledgers) / cs), "augment": at, "reduce": rd, "reentry": rt}
        text = md(cs, at, rd, rt, menus.get(cs))
        print(text if not a.apply else f"[{cs}] boards {at['boards']} trades {at['trades']} with_aug {at['trades_with_augment']}")
        if a.apply:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"family_tables_{cs}.json").write_text(json.dumps(doc, indent=1, default=str))
            (out_dir / f"family_tables_{cs}.md").write_text(text)
            if cs in menus:
                (out_dir / f"augment_family_repair_menu_{cs}.json").write_text(json.dumps({"schema": "augment_family_repair_menu/1", "cat_side": cs, "built_at": doc["built_at"], **menus[cs]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
