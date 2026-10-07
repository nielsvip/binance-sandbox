#!/usr/bin/env python3
"""v15_graph_search — graph-guided, path-aware, dual-window repair of a sym_side set (director 2026-10-06, ENCYCLOPEDIA v2).

Goes beyond "flip one or two switches to crank TIM/trades": it uses the knowledge graph (tools/v15_knowledge_graph.py ->
data/encyclopedia_v2/graph.json: switch/filter -> function family -> lifecycle, yellow filter -> switch edges, exit precedence,
fleet utilities) plus this sym_side's own measured lever map and trade autopsy.

Phases (every number is a real v12_quick_engine evaluation — evaluate_prepared_sanitized on the 30D and 365D slices in RAM):
  0 DIAGNOSE  base 30D (+ledger) and 365D; faults = tools/v15_diagnose_repair.diagnose + trade-autopsy faults (PREMATURE_EXITS,
              MISSED_AUGMENTS, MISSED_MOVES, LOSERS) + 365D faults (FAIL_365D_DD/TRADES/GAIN/TIM).
  1 SCREEN    every TEMPLATE candidate singly on the base (ledger) -> per-sym lever map + per-candidate trade attribution
              (losers fixed, premature exits fixed, augments fixed, captured moves) = this sym_side's utilities.
  2 ABLATE    GROUP ABLATION (investigation, never live): every TAB / LIFECYCLE / code family / name family / FILTER group is
              switched OFF (bool -> False, TF -> OFF) and, separately, reverted to cat_side DEFAULT, on the base; Δgain/Δtrades/
              ΔTIM/ΔDD/B&H gap on 30D (+365D for the coarse groups). Explicit ABLATION_DISABLE_* flags are measured, never applied.
  3 CULPRITS  groups whose removal improves the dual-window key are applied (minus forbidden members), then their members are
              re-added best-first (forward selection) — keeps the good members of a bad group.
  4 REORDER   groups whose ON contribution is weak (|Δ| small) get their settings re-tested best-first from three starting
              points (current state, group OFF, group DEFAULT) — greedy chains are path-dependent; a different start = order.
  5 PATHS     for each fault (worst first): graph utilities -> the function families that address it -> OPEN: the top openers
              of distinct families applied together with the filters the graph links to them relaxed (temporary loss allowed)
              -> RE-TIGHTEN: best-first over the linked filters + gates/exits, dual-window judged. Exits that dominate the
              ledger and close before the top are attacked through the exit-precedence graph (their master/TF switches).
  6 POLISH    dual-window beam over the shortlist (measured winners + reverts).
  7 VERIFY    top distinct finalists + origin on 365D; selection (30D compliant, 365D qualified, score); acceptance rule as
              tools/v15_diagnose_repair (status up, or +0.5pp 30D gain without losing status).
score = adj_gain(30D) [B&H stretch bonus, thin-sample penalty] + 0.1 * gain(365D). B&H ratio is reported, never a gate.

Safety (as DIAGNOSE+REPAIR): never applies promotion-blocked / NEVER_APPLY / SIZING / ABLATION_*=True / safety-gate True->False.

Standalone (S5/S2, NPZ in RAM, fork pool; also runs the current DIAGNOSE+REPAIR on the same base/budget for comparison):
  nice -n 10 .venv/bin/python tools/v15_graph_search.py --symsides ETHUSDC_LONG,AAPL_SHORT --out ~/v15_graph_20261006 \
      --method both --budget 900 --workers 4
In-pilot: v15_pilot._diagnose_repair calls run(ctx) after DIAGNOSE+REPAIR when V15_GRAPH_SEARCH=1 (default off).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v15_diagnose_repair as DR  # noqa: E402
from tools.v15_shutdown import V15Shutdown  # noqa: E402

GRAPH_PATH = ROOT / "data" / "encyclopedia_v2" / "graph.json"
ABLATION_FLAGS = ("ABLATION_DISABLE_REENTRY", "ABLATION_DISABLE_AUGMENTATION", "ABLATION_DISABLE_HIGH_GAIN_AUGMENT", "ABLATION_DISABLE_DC_BREACH_REDUCE",
                  "ABLATION_DISABLE_FILTER_ENTRY", "ABLATION_DISABLE_FILTER_MTF_HTF", "ABLATION_DISABLE_FILTER_REENTRY", "ABLATION_DISABLE_FILTER_EXIT",
                  "ABLATION_DISABLE_FILTER_AUGMENT", "ABLATION_DISABLE_FILTER_REDUCE")
# 2026-10-07 filter-ablation lane (PROPOSAL, default OFF): when True, starved
# sides open via the ordered filter-ablation series (ordered_filter_open) instead
# of deep_open()'s all-gates-at-once blast. Parent/user flips for reruns after
# worst_first. Env override: V15_GS_FILTER_SERIES=1.
FILTER_SERIES_ENABLED = os.environ.get("V15_GS_FILTER_SERIES", "0") == "1"
BUILD = "heal2-20261007"  # USER 2026-10-07: bump on ANY behavior change; stamped into every report (gs_build)
PHASE_PLAN = (("ABLATE", 0.16, 60), ("CULPRITS", 0.16, 60), ("PATHS", 0.30, 90), ("REORDER", 0.09, 25), ("POLISH", 0.06, 15), ("HEAL", 0.23, 90))


class _box:
    """USER 2026-10-07: absolute time box for one GS phase. Slow phases clip (never steal later phases);
    early finishes flow forward automatically (ends are fixed, starts are now). Sets deadline AND budget so every
    existing left()/fraction gate honors the box; driver deadline-honor propagates via the passed deadline."""

    def __init__(self, S, seconds: float, name: str):
        self.S, self.seconds, self.name = S, seconds, name

    def __enter__(self):
        self.saved = (self.S.deadline, self.S.budget)
        self.t0 = time.time()
        self.S.deadline = min(self.S.deadline, self.t0 + self.seconds)
        self.S.budget = self.seconds
        return self

    def __exit__(self, *exc):
        self.S.deadline, self.S.budget = self.saved
        try:
            self.S.rep.setdefault("phase_secs", {})[self.name] = round(time.time() - self.t0, 1)
        except Exception:
            pass
        return False


def _box_enter(S, box_ends: dict, name: str, rep: dict) -> float:
    """seconds to run, or 0 to skip honestly (records skip + macro). Slosh-inclusive: skipped phases gift time forward."""
    end, mn = box_ends[name]
    left = end - time.time()
    if left < mn:
        rep.setdefault("phase_skips", []).append(name)
        S.rep.setdefault("macro", []).append({"phase": name, "status": f"skipped: box {left:.0f}s < min {mn}s"})
        return 0.0
    return left
FLAG_LIFECYCLES = (("HIGH_GAIN_AUGMENT", ("AUGMENT", "AUGMENT_GATE")), ("AUGMENTATION", ("AUGMENT", "AUGMENT_GATE")), ("AUGMENT", ("AUGMENT", "AUGMENT_GATE")),
                   ("REENTRY", ("REENTRY", "REENTRY_GATE")), ("DC_BREACH_REDUCE", ("REDUCE", "EXIT_CLOSE")), ("REDUCE", ("REDUCE",)),
                   ("FILTER_MTF_HTF", ("ENTRY_GATE", "REENTRY_GATE")), ("FILTER_ENTRY", ("ENTRY_GATE", "ENTRY_OPEN")), ("FILTER_REENTRY", ("REENTRY", "REENTRY_GATE")),
                   ("FILTER_EXIT", ("EXIT_CLOSE", "EXIT_VETO")), ("FILTER_AUGMENT", ("AUGMENT", "AUGMENT_GATE")), ("FILTER_REDUCE", ("REDUCE", "EXIT_CLOSE")),
                   ("ENTRY", ("ENTRY_OPEN", "ENTRY_GATE")), ("EXIT", ("EXIT_CLOSE", "EXIT_VETO")), ("HEDGE", ("GLOBAL",)))
COARSE = ("TAB:", "LIFECYCLE:", "CODEFAM:", "FILTERS:")
NO_TOUCH_TOKENS = ("PARITY", "VEC_ONLY", "STRICT_VEC", "SIMPLE_PRICE_GT0", "RTH_ONLY")
NO_TOUCH_EXACT = {"MODE", "BASE_TF", "TF_HTF1", "TF_HTF2", "TF_HTF3", "SIM_ACCOUNT", "SIM_TRADING_ACCOUNT", "VEC_ACCOUNT_KEY"}


def no_touch(k: str) -> bool:
    """parity / infrastructure / test fields: never changed by the search (measured only via explicit ablation flags)."""
    u = str(k).upper()
    return u in NO_TOUCH_EXACT or u.startswith("ABLATION_") or u.endswith(("_PATH", "_DIR", "_FILE")) or any(t in u for t in NO_TOUCH_TOKENS)
WANT = {"TOO_FEW_TRADES": ("trades", 1), "FEW_TRADES": ("trades", 1), "TIM_LOW": ("tim", 1), "TIM_HIGH": ("tim", -1), "DD_HIGH": ("dd", -1), "DD_WARN": ("dd", -1),
        "TOO_MANY_TRADES": ("trades", -1), "LOW_EDGE": ("gain", 1), "GAIN_NEG": ("gain", 1), "BELOW_BH_MATERIAL": ("gain", 1), "LOW_WR": ("wr", 1),
        "PREMATURE_EXITS": ("premature_fixed", 1), "MISSED_AUGMENTS": ("aug_fixed", 1), "LOSERS": ("losers_fixed", 1), "MISSED_MOVES": ("captured", 1),
        "FAIL_365D_DD": ("dd365", -1), "FAIL_365D_TRADES": ("trades365", 1), "FAIL_365D_GAIN": ("gain365", 1), "FAIL_365D_TIM": ("tim365", -1)}
FAULT_LIFECYCLES = {"TOO_FEW_TRADES": ("ENTRY_GATE", "ENTRY_OPEN", "REENTRY", "REENTRY_GATE"), "FEW_TRADES": ("ENTRY_GATE", "ENTRY_OPEN", "REENTRY", "REENTRY_GATE"),
                    "MISSED_MOVES": ("ENTRY_GATE", "ENTRY_OPEN", "REENTRY"), "TIM_LOW": ("EXIT_CLOSE", "EXIT_VETO", "REENTRY", "ENTRY_GATE"),
                    "TIM_HIGH": ("EXIT_CLOSE", "REDUCE", "EXIT_VETO"), "DD_HIGH": ("EXIT_CLOSE", "REDUCE", "ENTRY_GATE", "AUGMENT"), "DD_WARN": ("EXIT_CLOSE", "REDUCE", "AUGMENT"),
                    "FAIL_365D_DD": ("EXIT_CLOSE", "REDUCE", "ENTRY_GATE", "AUGMENT", "EXIT_VETO"), "FAIL_365D_GAIN": ("ENTRY_GATE", "EXIT_CLOSE"), "FAIL_365D_TRADES": ("ENTRY_GATE", "ENTRY_OPEN", "REENTRY"),
                    "FAIL_365D_TIM": ("EXIT_CLOSE", "REDUCE"), "TOO_MANY_TRADES": ("ENTRY_GATE", "REENTRY_GATE", "EXIT_VETO"), "LOW_EDGE": ("ENTRY_GATE", "EXIT_CLOSE"),
                    "GAIN_NEG": ("ENTRY_GATE", "EXIT_CLOSE", "REENTRY_GATE"), "LOW_WR": ("ENTRY_GATE", "REENTRY_GATE"), "BELOW_BH_MATERIAL": ("EXIT_CLOSE", "EXIT_VETO", "AUGMENT", "REENTRY"),
                    "PREMATURE_EXITS": ("EXIT_CLOSE", "EXIT_VETO", "REDUCE"), "MISSED_AUGMENTS": ("AUGMENT", "AUGMENT_GATE"), "LOSERS": ("ENTRY_GATE", "EXIT_CLOSE", "REENTRY_GATE")}


RELAX_FAULTS = ("TOO_FEW_TRADES", "FEW_TRADES", "MISSED_MOVES", "TIM_LOW", "BELOW_BH_MATERIAL", "FAIL_365D_TRADES", "PREMATURE_EXITS", "MISSED_AUGMENTS")


def load_graph(cat_side: str, path: Path = GRAPH_PATH) -> dict:
    """Compact per-cat_side view: switch -> {lifecycle, family, code_family, off_value}, groups, yellow (filter <- switch),
    fault priors, exit precedence."""
    try:
        g = json.loads(Path(path).read_text())
    except Exception:
        return {}
    sw = {}
    for k, v in g.get("nodes", {}).items():
        if v.get("type") in ("switch", "filter"):
            sw[v["name"]] = {"lifecycle": v.get("lifecycle"), "family": v.get("family"), "code_family": v.get("code_family"), "off": v.get("off_value"), "roles": v.get("roles", [])}
    prior = {}
    for fault, lev in (g.get("fault_index", {}).get(cat_side) or {}).items():
        prior[fault] = {x["lever"]: i for i, x in enumerate(lev)}
    return {"switch": sw, "groups": g.get("groups", {}).get(cat_side, {}), "yellow": g.get("templates", {}).get(cat_side, {}).get("yellow", {}),
            "prior": prior, "exit_precedence": g.get("meta", {}).get("exit_precedence", []), "engine_md5": g.get("meta", {}).get("engine_md5")}


def score(m30: dict, m365: dict | None) -> float:
    s = DR.adj_gain(m30)
    if m365 is not None and m365.get("gain") is not None:
        s += 0.1 * m365["gain"]
    return s


def dkey(m30: dict, m365: dict | None, q365: bool, n_changes: int = 0) -> tuple:
    c30 = DR.compliant(m30)
    if m365 is None:
        return (False, False, c30, False, -1e9, round(score(m30, None), 6), -n_changes)
    return (bool(q365) and c30, bool(q365), c30, bool(m365.get("valid")), -round(DR.excess(m365), 3), round(score(m30, m365), 6), -n_changes)


class GraphSearch(DR._Search):
    def __init__(self, ctx: dict):
        super().__init__(ctx)
        self.G = ctx.get("graph") or {}
        self.gsw = self.G.get("switch", {})
        self.m365c: dict = {}
        self.attr: dict = {}  # cand id -> scorecard vs the base trades
        self.log = ctx.get("log") or (lambda m: None)
        self.touch = ctx.get("touch") or (lambda m: None)
        self.rep: dict = {"steps": [], "ablation": [], "macro": []}
        self.t_start = time.time()
        self.by_switch = defaultdict(list)
        for c in self.cands:
            self.by_switch[c["switch"]].append(c)

    # ── 365D, memoised, parallel when the ctx can ──
    def m365_many(self, ovs: list) -> list:
        todo, keys = [], []
        for ov in ovs:
            k = self._k(self.sanitize(ov))
            keys.append(k)
            if k in self.m365c or k in [x[0] for x in todo]:
                continue
            b = self._m365.get(k)  # USER 2026-10-07: bridge to the shared persisted 365D memo (resume replays, never recomputes)
            if b is None:
                rr = self._resume_m365.get(self._mk(k))
                if rr is not None:
                    b = (rr[0], bool(rr[1]))
            if b is not None:
                self.m365c[k] = (b[0], b[1], ["resumed"])
            else:
                todo.append((k, self.sanitize(ov)))
        if todo and self.ctx.get("eval_365") and self.left() > 5:
            if self._shutdown_hit():
                self._flush_ckpt()
                raise V15Shutdown("shutdown in GS 365D batch")
            if self.ctx.get("eval_many_365"):
                res = self.ctx["eval_many_365"]([ov for _k, ov in todo], self.deadline)
            else:
                res = []
                for _k, ov in todo:
                    if self.left() <= 5:
                        res.append((None, None))
                        continue
                    res.append(self.ctx["eval_365"](ov))
            _stored = 0
            for (k, _ov), (r, span) in zip(todo, res):
                if r is None:
                    continue
                ok, why = self.ctx["qualifies_365"](r, span)
                self.m365c[k] = (DR.metrics(r), ok, why)
                self._m365[k] = (self.m365c[k][0], ok)
                _stored += 1
            try:
                self.log(f"[m365] todo={len(todo)} stored={_stored} nones={len(todo) - _stored} left={round(self.left(), 1)}")
            except Exception:
                pass
            self._flush_ckpt()
        else:
            try:
                self.log(f"[m365] SKIP todo={len(todo)} has_eval365={bool(self.ctx.get('eval_365'))} left={round(self.left(), 1)}")
            except Exception:
                pass
        return [self.m365c.get(k) for k in keys]

    def key_of(self, ov: dict, m30: dict) -> tuple:
        r = self.m365c.get(self._k(self.sanitize(ov)))
        if r is None:
            return dkey(m30, None, False, len(self.changes(ov)))
        return dkey(m30, r[0], r[1], len(self.changes(ov)))

    def _step(self, phase: str, label: str, ov: dict, m: dict, extra: dict | None = None):
        r = self.m365c.get(self._k(self.sanitize(ov)))
        st = {"phase": phase, "applied": label, **{k: m[k] for k in ("gain", "trades", "tim", "dd", "wr", "valid")}, "gain_365": (r[0] or {}).get("gain") if r else None,
              "dd_365": (r[0] or {}).get("dd") if r else None, "q365": r[1] if r else None, "evals": self.n_evals, "t": round(time.time() - self.t_start, 1), **(extra or {})}
        self.rep["steps"].append(st)
        self.log(f"[GS-{phase}] {label} -> 30D gain={m['gain']} tr={m['trades']} TIM={m['tim']} DD={m['dd']} | 365D gain={st['gain_365']} DD={st['dd_365']} q={st['q365']}")
        self.touch(f"gs-{phase.lower()}")

    def climb(self, state: dict, state_m: dict, phase: str, pred=None, only: set | None = None, rounds: int = 6, k365: int = 6, frac_stop: float = 0.0, extra_items=None) -> tuple:
        """best-first dual-window hill climb: each round screens the candidate moves on 30D (pool), sends the top k365 by 30D
        quality key to 365D (pool), applies the best by the dual key if it beats the current state."""
        cur_key = self.key_of(state, state_m)
        for rnd in range(rounds):
            if self._shutdown_hit():
                self._flush_ckpt()
                raise V15Shutdown("shutdown in GS climb")
            if self.left() < frac_stop * self.budget:
                break
            scr = self.screen(state, state_m, phase, pred, only=only)
            if extra_items:
                scr += self.evaluate([(lab, {**state, **ov}, c, True) for lab, ov, c in extra_items(state)], phase, state_m["gain"])
            ch = [(DR.quality_key(m, len(self.changes(ov))), ov, m, c) for m, ov, c in scr if m is not None and not DR._forbidden(c, state, self.defaults)]
            if not ch:
                break
            ch.sort(key=lambda x: x[0], reverse=True)
            seen, top = set(), []
            for q, ov, m, c in ch:
                fk = self._k(ov)
                if fk in seen:
                    continue
                seen.add(fk)
                top.append((ov, m, c))
                if len(top) >= k365:
                    break
            self.m365_many([ov for ov, _m, _c in top])
            best = max(top, key=lambda x: self.key_of(x[0], x[1]))
            bk = self.key_of(best[0], best[1])
            if bk <= cur_key:
                break
            state, state_m, cur_key = best[0], best[1], bk
            self._step(phase, f"{best[2]['switch']}={best[2]['cand']}", state, state_m, {"round": rnd})
        return state, state_m

    # ── groups ──
    def _off_value(self, sw: str, cur):
        if no_touch(sw):
            return None
        node = self.gsw.get(sw) or {}
        off = node.get("off")
        if off is not None and str(off).strip().upper() not in ("OFF", "FALSE", "NONE"):
            off = None
        if off is not None:
            try:
                import v15_pilot as P  # noqa
                v = P._parse_opt_value(off, self.defaults.get(sw))
            except Exception:
                v = False if str(off).lower() == "false" else off
            if not self.same(cur, v):
                return v
            return None
        if str(sw).endswith("_ENABLED") and (cur is True or str(cur).strip().lower() == "true"):
            return False
        return None

    def group_ov(self, state: dict, members: list, mode: str) -> tuple:
        """-> (new state, changed keys). OFF: members with an off value set to it. DEFAULT: members differing from the cat default reverted."""
        v, changed = dict(state), []
        for sw in members:
            if no_touch(sw) or sw in DR.STRUCTURAL:
                continue
            cur = state.get(sw, self.defaults.get(sw))
            if mode == "OFF":
                off = self._off_value(sw, cur)
                if off is not None:
                    v[sw] = off
                    changed.append(sw)
            else:
                if sw in state and not self.same(state[sw], self.defaults.get(sw)):
                    v.pop(sw, None)
                    changed.append(sw)
        return v, changed

    def _allowed_change(self, state: dict, k: str, v) -> bool:
        c = {"switch": k, "cand": v, "tab": "", "ov": {k: v}}
        return not DR._forbidden(c, state, self.defaults) and k not in DR.NEVER_APPLY and not no_touch(k)

    def ablate(self, state: dict, state_m: dict, tag: str) -> list:
        groups = self.G.get("groups", {})
        items, meta = [], []
        for fl in ABLATION_FLAGS:  # USER 2026-10-07: FLAGS first (drill-down feeds on them; truncation-safe order)
            if fl in self.defaults and not (state.get(fl, self.defaults.get(fl)) is True):
                v = {**state, fl: True}
                c = {"switch": fl, "cand": True, "tab": "ABLATION", "row": None, "ov": {fl: True}, "group": f"FLAG:{fl}", "mode": "TRUE", "changed": [fl], "measure_only": True}
                items.append((f"ABLATE:{fl}", v, c, False))
                meta.append(c)
        for gname, members in sorted(groups.items(), key=lambda kv: 0 if kv[0].startswith("FAMILY:") else 1):
            for mode in ("OFF", "DEFAULT"):
                v, changed = self.group_ov(state, members, mode)
                if not changed:
                    continue
                c = {"switch": f"GROUP[{gname}]", "cand": mode, "tab": "ABLATION", "row": None, "ov": {k: v.get(k, self.defaults.get(k)) for k in changed}, "group": gname, "mode": mode, "changed": changed}
                items.append((f"ABLATE:{gname}|{mode}", v, c, False))  # investigation only: never a finalist (may hold forbidden members)
                meta.append(c)
        res = self.evaluate(items, f"ABLATE_{tag}", state_m["gain"])
        coarse = [(ov, m, c) for m, ov, c in res if m is not None and c["group"].startswith(COARSE + ("FLAG:",))]
        self.m365_many([ov for ov, _m, _c in coarse])
        out = []
        b365 = self.m365c.get(self._k(self.sanitize(state)))
        for m, ov, c in res:
            if m is None:
                continue
            r = self.m365c.get(self._k(self.sanitize(ov)))
            d365 = None
            if r and b365 and r[0] and b365[0]:
                d365 = {"gain": (r[0]["gain"] or 0) - (b365[0]["gain"] or 0), "dd": (r[0]["dd"] or 0) - (b365[0]["dd"] or 0), "trades": r[0]["trades"] - b365[0]["trades"], "q365": r[1]}
            d = DR._delta(m, state_m)
            bh = m.get("bh")
            out.append({"tag": tag, "group": c["group"], "mode": c["mode"], "n_changed": len(c["changed"]), "changed": c["changed"][:60], "d": d, "valid": m["valid"],
                        "gain": m["gain"], "trades": m["trades"], "bh_gap": (m["gain"] - bh) if (m["gain"] is not None and bh is not None) else None, "d365": d365,
                        "measure_only": bool(c.get("measure_only")), "_ov": ov, "_m": m})
        out.sort(key=lambda a: -((a["d"] or {}).get("gain") or -1e9))
        for a in out:
            self.rep["ablation"].append({k: v for k, v in a.items() if not k.startswith("_")})
        self.log(f"[GS-ABLATE-{tag}] {len(out)} group ablations; removal helps 30D: {[(a['group'] + '|' + a['mode'], round(a['d']['gain'], 2)) for a in out[:5] if a['d']['gain'] and a['d']['gain'] > 0]}")
        return out

    def _flag_lifecycles(self, flag: str) -> tuple:
        """lifecycles under an ablation FLAG (diagnostic -> drill target). Graph node first, name-substring fallback."""
        g = self.gsw.get(flag) or {}
        for cand in (g.get("code_family"), g.get("family"), g.get("lifecycle")):
            if cand and cand != "ABLATION":
                hit = next((lc for key, lc in FLAG_LIFECYCLES if key in str(cand).upper()), None)
                if hit:
                    return hit
        u = str(flag).upper()
        hit = next((lc for key, lc in FLAG_LIFECYCLES if key in u), None)
        return hit or ()

    def _drill_flag(self, state: dict, state_m: dict, cur: tuple, a: dict) -> tuple:
        """USER 2026-10-07: a FLAG ablation hit is a direction, not a verdict — drill switch-by-switch into its lifecycles
        (dual-window climb). The flag itself is NO_TOUCH and never enters state."""
        d30 = abs((a.get("d") or {}).get("gain") or 0.0)
        d365 = abs((a.get("d365") or {}).get("gain") or 0.0)
        if max(d30, d365) < 0.5:
            return state, state_m, cur, 0
        fams = self._flag_lifecycles(a.get("changed", [None])[0])
        if not fams:
            self.rep["macro"].append({"flag": a.get("group"), "status": "drill skipped: family unresolvable"})
            return state, state_m, cur, 0
        fam_sw = [c["switch"] for c in self.cands if (self.gsw.get(c["switch"]) or {}).get("lifecycle") in fams and not c["switch"].startswith("ABLATION_")]
        ids = {c["_id"] for c in self.cands if c["switch"] in set(fam_sw)} | self.linked_filters(fam_sw)
        ids = {i for i in ids if not self.cands[i]["switch"].startswith("ABLATION_")}
        if len(ids) < 2:
            self.rep["macro"].append({"flag": a.get("group"), "status": f"drill skipped: only {len(ids)} cands in {fams}"})
            return state, state_m, cur, 0
        st, stm = self.climb(state, state_m, f"FLAG[{a.get('group')}]", only=ids, rounds=4, k365=4, frac_stop=0.35)
        self.m365_many([st])
        if self.key_of(st, stm) > cur:
            self._step("CULPRIT_FLAG", f"{a.get('group')}: drill {fams} ({len(ids)} cands)", st, stm)
            self.rep["macro"].append({"flag": a.get("group"), "fams": fams, "n_ids": len(ids), "gain": stm["gain"], "trades": stm["trades"], "accepted": True})
            return st, stm, self.key_of(st, stm), 1
        self.rep["macro"].append({"flag": a.get("group"), "fams": fams, "n_ids": len(ids), "accepted": False,
                                  "note": f"family unfixable switch-by-switch; flag-off itself would gain {d30:+.2f}/30D {d365:+.2f}/365D (diagnostic only)"})
        return state, state_m, cur, 1

    def culprits(self, state: dict, state_m: dict, abl: list, max_groups: int = 4) -> tuple:
        cur = self.key_of(state, state_m)
        tried = 0
        for a in sorted(abl, key=lambda a: self.key_of(a["_ov"], a["_m"]), reverse=True):
            if tried >= max_groups or self.left() < 0.45 * self.budget:
                break
            if a["measure_only"]:
                state, state_m, cur, used = self._drill_flag(state, state_m, cur, a)
                tried += used
                continue
            ov = a["_ov"]
            allowed = {k: ov.get(k, self.defaults.get(k)) for k in a["changed"] if self._allowed_change(state, k, ov.get(k, self.defaults.get(k)))}
            if not allowed:
                continue
            v = dict(state)
            for k, val in allowed.items():
                if a["mode"] == "DEFAULT":
                    v.pop(k, None)
                else:
                    v[k] = val
            m = self.evaluate([(f"CULPRIT:{a['group']}|{a['mode']}", v, None, True)], "CULPRIT", state_m["gain"])[0][0]
            if m is None:
                continue
            self.m365_many([v])
            if self.key_of(v, m) <= cur and self.key_of(a["_ov"], a["_m"]) <= cur:
                continue
            tried += 1
            # forward re-add of removed members, best-first (keeps the good members of a bad group)
            removed = list(allowed)
            st, stm = v, m
            for _ in range(min(6, len(removed))):
                items = []
                for k in removed:
                    w = dict(st)
                    if k in state:
                        w[k] = state[k]
                    else:
                        w.pop(k, None)
                    items.append((f"READD:{k}", w, {"switch": k, "cand": "READD", "tab": "CULPRIT", "ov": {k: state.get(k, self.defaults.get(k))}, "revert": True}, True))
                res = [(mm, ov2, c) for mm, ov2, c in self.evaluate(items, "READD", stm["gain"]) if mm is not None]
                if not res:
                    break
                res.sort(key=lambda x: DR.quality_key(x[0]), reverse=True)
                self.m365_many([ov2 for _mm, ov2, _c in res[:4]])
                b = max(res[:4], key=lambda x: self.key_of(x[1], x[0]))
                if self.key_of(b[1], b[0]) <= self.key_of(st, stm):
                    break
                st, stm = b[1], b[0]
                removed.remove(b[2]["switch"])
            if self.key_of(st, stm) > cur:
                state, state_m, cur = st, stm, self.key_of(st, stm)
                self._step("CULPRIT", f"remove {a['group']}|{a['mode']} (kept back {len(allowed) - len(removed)}/{len(allowed)})", state, state_m)
        return state, state_m

    def reorder(self, state: dict, state_m: dict, abl: list, max_groups: int = 4) -> tuple:
        """weak groups: best-first re-test of the group's settings from 3 starts (current, OFF, DEFAULT)."""
        weak = []
        seen = set()
        for a in abl:
            g = a["group"]
            if a["measure_only"] or g in seen or g.startswith(("TAB:", "LIFECYCLE:")):
                continue
            d = a["d"] or {}
            if d.get("gain") is not None and abs(d["gain"]) < 0.5 and abs(d.get("trades") or 0) <= 5:
                members = set(self.G["groups"].get(g, []))
                ids = {c["_id"] for c in self.cands if c["switch"] in members}
                if len(ids) >= 3:
                    weak.append((g, ids))
                    seen.add(g)
        weak.sort(key=lambda x: -len(x[1]))
        cur = self.key_of(state, state_m)
        for g, ids in weak[:max_groups]:
            if self.left() < 0.35 * self.budget:
                break
            best = (cur, state, state_m)
            for mode in ("CUR", "OFF", "DEFAULT"):
                if mode == "CUR":
                    s0, m0 = state, state_m
                else:
                    s0, ch = self.group_ov(state, self.G["groups"][g], mode)
                    if not ch or any(not self._allowed_change(state, k, s0.get(k, self.defaults.get(k))) for k in ch):
                        continue
                    m0 = self.evaluate([(f"REORDER_START:{g}|{mode}", s0, None, True)], "REORDER", state_m["gain"])[0][0]
                    if m0 is None:
                        continue
                st, stm = self.climb(s0, m0, f"REORDER[{g}|{mode}]", only=ids, rounds=4, k365=4, frac_stop=0.35)
                self.m365_many([st])
                k = self.key_of(st, stm)
                if k > best[0]:
                    best = (k, st, stm)
            if best[0] > cur:
                cur, state, state_m = best
                self._step("REORDER", f"group {g} re-tested from 3 starts", state, state_m)
        return state, state_m

    # ── fault paths ──
    def faults(self, m30: dict, mix: dict, r365, card: dict) -> list:
        f = [x[0].split(":", 1)[0] for x in DR.diagnose(m30, mix, self.ctx.get("cat_side"))]
        if card:
            n = max(1, card.get("n_trades", 1))
            if card.get("PREMATURE_EXIT", 0) >= max(3, 0.2 * n):
                f.append("PREMATURE_EXITS")
            if card.get("MISSED_AUGMENT", 0) >= max(2, 0.1 * n):
                f.append("MISSED_AUGMENTS")
            if card.get("LOSER", 0) >= max(3, 0.4 * n):
                f.append("LOSERS")
            if card.get("missed_moves", 0) >= 3:
                f.append("MISSED_MOVES")
        if r365 is not None and not r365[1]:
            m = r365[0] or {}
            why = " ".join(r365[2] or [])
            if "DD" in why or (m.get("dd") or 0) > DR.DD_MAX:
                f.append("FAIL_365D_DD")
            if "TIM" in why:
                f.append("FAIL_365D_TIM")
            if "trades" in why:
                f.append("FAIL_365D_TRADES")
            if "gain" in why:
                f.append("FAIL_365D_GAIN")
        order = ["FAIL_365D_DD", "TOO_FEW_TRADES", "GAIN_NEG", "DD_HIGH", "TIM_HIGH", "FAIL_365D_GAIN", "FAIL_365D_TRADES", "FAIL_365D_TIM", "TIM_LOW", "FEW_TRADES",
                 "TOO_MANY_TRADES", "LOSERS", "BELOW_BH_MATERIAL", "PREMATURE_EXITS", "MISSED_AUGMENTS", "MISSED_MOVES", "LOW_EDGE", "LOW_WR", "DD_WARN"]
        return [x for x in order if x in set(f)]

    def _move(self, fault: str, m: dict, base: dict, c: dict) -> float | None:
        k, sgn = WANT[fault]
        if k in ("premature_fixed", "aug_fixed", "losers_fixed", "captured"):
            a = self.attr.get(c.get("_id"))
            return sgn * a.get(k, 0) if a else None
        if k.endswith("365"):
            return None
        if m.get(k) is None or base.get(k) is None:
            return None
        return sgn * (m[k] - base[k])

    def openers(self, fault: str, scr: list, base_m: dict, k: int = 3, state: dict | None = None) -> list:
        """measured movers of the fault metric, restricted to the graph's lifecycles for that fault, ranked by measured move then
        fleet prior; one per family (distinct function families = distinct paths). 365D faults: the 16 best 30D-valid
        lifecycle candidates are measured on 365D and ranked by the 365D metric."""
        lcs = FAULT_LIFECYCLES.get(fault, ())
        pri = (self.G.get("prior") or {}).get(fault, {})
        rows = []
        if WANT[fault][0].endswith("365"):
            key365, sgn = WANT[fault][0][:-3], WANT[fault][1]
            b = self.m365c.get(self._k(self.sanitize(state or self.origin)))
            if not b or not b[0]:
                return []
            pool = [(m, ov, c) for m, ov, c in scr if m is not None and not c.get("revert") and m["valid"] and not DR._forbidden(c, state or self.origin, self.defaults)
                    and (self.gsw.get(c["switch"]) or {}).get("lifecycle") in lcs]
            pool.sort(key=lambda x: DR.quality_key(x[0]), reverse=True)
            pool = pool[:16]
            got = self.m365_many([ov for _m, ov, _c in pool])
            for (m, ov, c), r in zip(pool, got):
                if not r or not r[0] or r[0].get(key365) is None or b[0].get(key365) is None:
                    continue
                mv = sgn * (r[0][key365] - b[0][key365])
                if mv > 1e-9:
                    rows.append((mv, -pri.get(f"{c['switch']}={c['cand']}", 999), m["gain"] or -1e9, c, (self.gsw.get(c["switch"]) or {}).get("family") or c["switch"]))
            rows.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
            out, fams = [], set()
            for mv, _p, _g, c, fam in rows:
                if fam not in fams:
                    fams.add(fam)
                    out.append((c, mv))
                if len(out) >= k:
                    break
            return out
        for m, ov, c in scr:
            if m is None or c.get("revert") or DR._forbidden(c, self.origin, self.defaults) or DR._is_safety(c) and fault in ("TOO_FEW_TRADES", "FEW_TRADES", "MISSED_MOVES"):
                continue
            node = self.gsw.get(c["switch"]) or {}
            if lcs and node.get("lifecycle") not in lcs:
                continue
            mv = self._move(fault, m, base_m, c)
            if mv is None or mv <= 1e-9:
                continue
            if not m["valid"] and fault not in ("TOO_FEW_TRADES",):
                continue
            rows.append((mv, -pri.get(f"{c['switch']}={c['cand']}", 999), m["gain"] or -1e9, c, node.get("family") or c["switch"]))
        rows.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        out, fams = [], set()
        for mv, _p, _g, c, fam in rows:
            if fam in fams:
                continue
            fams.add(fam)
            out.append((c, mv))
            if len(out) >= k:
                break
        return out

    def linked_filters(self, switches: list) -> set:
        """candidate ids of the filters the graph links (TEMPLATE yellow) to these switches + gates of the same code family."""
        ys = set()
        for s in switches:
            ys |= set((self.G.get("yellow") or {}).get(s, []))
        fams = {(self.gsw.get(s) or {}).get("code_family") for s in switches} - {None}
        ids = {c["_id"] for c in self.cands if c["switch"] in ys}
        ids |= {c["_id"] for c in self.cands if (self.gsw.get(c["switch"]) or {}).get("code_family") in fams and DR._is_filter(c)}
        return ids

    def exit_attack(self, state: dict, mix: dict) -> list:
        """dominant exit families of the ledger -> exit precedence node -> the candidates of its master/TF switch (slow it / turn it off)."""
        out = []
        ex = mix.get("exit") or {}
        prec = {e["exit"].split("(")[0].split("/")[0]: e for e in self.G.get("exit_precedence", [])}
        for reason, a in list(ex.items())[:4]:
            if a["share"] < 0.2:
                continue
            tok = reason.split("_")[0]
            fam = next((e for name, e in prec.items() if name and (name in reason or reason in name or name.split("_")[0] == tok)), None)
            sws = set()
            if fam and fam.get("master"):
                sws.add(fam["master"])
            sws |= {c["switch"] for c in self.cands if tok in c["switch"].split("_") and (self.gsw.get(c["switch"]) or {}).get("lifecycle", "").startswith(("EXIT", "REDUCE"))}
            out += [c["_id"] for s in sws for c in self.by_switch.get(s, [])]
        return out

    def deep_open(self, state: dict, state_m: dict, fault: str, cur: tuple) -> tuple:
        """STARVED sym_side (no single row adds a trade: several gates bind at once, e.g. the CRYPTO_SHORT default FILTER_TF
        stack): open EVERY allowed entry gate / reentry gate / FILTER_TF at once (graph groups), then re-tighten best-first over
        gates + exits + entries with the dual-window judge (holds that never close are exactly what the exits fix)."""
        v = dict(state)
        opened_keys = []
        for g in ("LIFECYCLE:ENTRY_GATE", "LIFECYCLE:REENTRY_GATE", "FILTERS:ALL_FILTER_TF"):
            w, ch = self.group_ov(v, self.G.get("groups", {}).get(g, []), "OFF")
            for k in ch:
                if self._allowed_change(state, k, w.get(k)):
                    v[k] = w[k]
                    opened_keys.append(k)
        if not opened_keys:
            self.rep["macro"].append({"fault": fault, "status": "starved and no gate to open"})
            return state, state_m, cur
        m = self.evaluate([(f"DEEP_OPEN[{fault}]", v, None, True)], "DEEP_OPEN", state_m["gain"])[0][0]
        rec = {"fault": fault, "deep_open": len(opened_keys), "opened_trades": (m or {}).get("trades"), "opened_gain": (m or {}).get("gain")}
        if m is None or m["trades"] <= state_m["trades"]:
            rec["status"] = "deep open added no trades: the block is outside the TEMPLATE (engine hard block / data)"
            self.rep["macro"].append(rec)
            return state, state_m, cur
        self.m365_many([v])
        ids = {c["_id"] for c in self.cands if (self.gsw.get(c["switch"]) or {}).get("lifecycle", "") in ("EXIT_CLOSE", "REDUCE", "EXIT_VETO", "ENTRY_GATE", "REENTRY_GATE", "ENTRY_OPEN", "REENTRY")}
        st, stm = self.climb(v, m, f"DEEP[{fault}]", only=ids, rounds=8, k365=6, frac_stop=0.25)
        self.m365_many([st])
        kk = self.key_of(st, stm)
        rec.update({"final_gain": stm["gain"], "final_trades": stm["trades"], "accepted": kk > cur})
        self.rep["macro"].append(rec)
        if kk > cur:
            self._step("DEEP_OPEN", f"{fault}: {len(opened_keys)} gates opened -> re-tighten", st, stm)
            return st, stm, kk
        return state, state_m, cur

    def ordered_filter_open(self, state: dict, state_m: dict, fault: str, cur: tuple) -> tuple:
        """PROPOSAL (2026-10-07 filter-ablation lane; gated by FILTER_SERIES_ENABLED,
        default OFF — deep_open() default behavior unchanged): starved side opens via
        a CUMULATIVE ordered series of coherent filter-group ablations (ENTRY, then
        +MTF_HTF, then +REENTRY — vec_decisions.filter_ablation_groups.SERIES_ORDER)
        instead of deep_open()'s all-gates-at-once blast. Each step is measured, so
        the report shows WHICH layer starved the side. Flags are measure-only: before
        the re-tighten climb they are translated to individual member-OFF overrides
        (the climb then keeps/drops members like deep_open) and the flags are dropped
        (ABLATION_*=True is never applied — see module safety rule).
        ROUTING (paths() starved-fault branch, parent-granted 2026-10-07): FILTER_SERIES_ENABLED
        selects this method, else deep_open() — default behavior unchanged.
        FUTURE integration point (parent-owned): the HEAL_365 continuation + culprits()
        FLAG-hit drill-down — per-step series deltas + FLAG ablation hits feed the culprit
        branch (apply offending FILTERS:* group minus forbidden members, forward re-add).
        RERUN WAVE (parent 2026-10-07): heal_365() (runs post-polish, 365D-fault rounds)
        is the integration point for the rerun wave, in addition to this deep_open routing."""
        try:
            from vec_decisions import filter_ablation_groups as _fab
        except Exception:
            self.rep["macro"].append({"fault": fault, "status": "filter series unavailable (no mapping module); caller should fall back to deep_open"})
            return state, state_m, cur
        v, steps = dict(state), []
        for flag in _fab.SERIES_ORDER:
            if flag in self.defaults and not (v.get(flag, self.defaults.get(flag)) is True):
                v[flag] = True
            m = self.evaluate([(f"FILTER_SERIES[{fault}][{flag}]", v, None, False)], "FILTER_SERIES", state_m["gain"])[0][0]
            steps.append({"flag": flag, "trades": (m or {}).get("trades"), "gain": (m or {}).get("gain")})
            if m is None:
                break
        rec = {"fault": fault, "series": "ordered_filter_open", "steps": steps}
        last_m = self.evaluate([(f"FILTER_SERIES[{fault}][ALL]", v, None, False)], "FILTER_SERIES", state_m["gain"])[0][0]
        if last_m is None or last_m["trades"] <= state_m["trades"]:
            rec["status"] = "series added no trades: the block is outside the filter groups (engine hard block / data)"
            self.rep["macro"].append(rec)
            return state, state_m, cur
        # translate flags -> individual member-OFF overrides (members stay climbable; flags dropped)
        w = {k: val for k, val in v.items() if k not in _fab.FLAGS}
        opened_keys = []
        for flag in _fab.SERIES_ORDER:
            if v.get(flag) is not True:
                continue
            w2, ch = self.group_ov(w, list(_fab.GROUPS[flag]), "OFF")
            for k in ch:
                if self._allowed_change(state, k, w2.get(k)):
                    w[k] = w2[k]
                    opened_keys.append(k)
        m2 = self.evaluate([(f"FILTER_SERIES[{fault}][TRANSLATED]", w, None, True)], "FILTER_SERIES", state_m["gain"])[0][0]
        if m2 is None or m2["trades"] <= state_m["trades"]:
            rec.update({"translated_trades": (m2 or {}).get("trades"), "status": "flag->member translation lost the opened trades (allowed-change filter); keeping base"})
            self.rep["macro"].append(rec)
            return state, state_m, cur
        self.m365_many([w])
        ids = {c["_id"] for c in self.cands if (self.gsw.get(c["switch"]) or {}).get("lifecycle", "") in ("EXIT_CLOSE", "REDUCE", "EXIT_VETO", "ENTRY_GATE", "REENTRY_GATE", "ENTRY_OPEN", "REENTRY")}
        st, stm = self.climb(w, m2, f"SERIES[{fault}]", only=ids, rounds=8, k365=6, frac_stop=0.25)
        self.m365_many([st])
        kk = self.key_of(st, stm)
        rec.update({"opened_keys": len(opened_keys), "final_gain": stm["gain"], "final_trades": stm["trades"], "accepted": kk > cur})
        self.rep["macro"].append(rec)
        if kk > cur:
            self._step("FILTER_SERIES", f"{fault}: ordered series -> {len(opened_keys)} members opened -> re-tighten", st, stm)
            return st, stm, kk
        return state, state_m, cur

    def paths(self, state: dict, state_m: dict, scr: list, base_m: dict, faults: list, mix: dict) -> tuple:
        cur = self.key_of(state, state_m)
        for fault in faults[:5]:
            if self.left() < 0.3 * self.budget:
                break
            ops = self.openers(fault, scr, base_m, state=state)
            extra_ids = set(self.exit_attack(state, mix)) if fault in ("PREMATURE_EXITS", "BELOW_BH_MATERIAL", "TIM_LOW") else set()
            if not ops and not extra_ids and fault in ("TOO_FEW_TRADES", "FEW_TRADES", "FAIL_365D_TRADES", "MISSED_MOVES"):
                if FILTER_SERIES_ENABLED:  # 2026-10-07 filter-ablation lane (default OFF): ordered series instead of the deep_open blast
                    state, state_m, cur = self.ordered_filter_open(state, state_m, fault, cur)
                else:
                    state, state_m, cur = self.deep_open(state, state_m, fault, cur)
                continue
            if not ops and not extra_ids:
                self.rep["macro"].append({"fault": fault, "status": "no measured opener in the graph lifecycles"})
                continue
            # OPEN: openers jointly + relax their linked filters (OFF), temporary loss allowed
            v = dict(state)
            for c, _mv in ops:
                if self.applies(v, c):
                    v.update(c["ov"])
            relaxed = []
            set_by_openers = {k for c, _ in ops for k in c["ov"]}
            # relax the linked filters only on trade-starved paths (opening entries/reentries/holds); on loser/DD paths the
            # openers ARE the filters/stops and the climb re-tightens
            relax_ok = fault in RELAX_FAULTS
            for fid in (self.linked_filters([c["switch"] for c, _ in ops]) if relax_ok else ()):
                fc = self.cands[fid]
                if fc["switch"] in set_by_openers:
                    continue
                off = self._off_value(fc["switch"], v.get(fc["switch"], self.defaults.get(fc["switch"])))
                if off is not None and self._allowed_change(v, fc["switch"], off) and fc["switch"] not in relaxed:
                    v[fc["switch"]] = off
                    relaxed.append(fc["switch"])
            opened = self.evaluate([(f"OPEN[{fault}]", v, None, True)], "PATH_OPEN", state_m["gain"])[0][0]
            if opened is None:
                continue
            self.m365_many([v])
            # RE-TIGHTEN: best-first over linked filters + gates + exits (+ the exit-attack set), dual-window
            ids = self.linked_filters([c["switch"] for c, _ in ops]) | extra_ids
            ids |= {c["_id"] for c in self.cands if (self.gsw.get(c["switch"]) or {}).get("lifecycle") in FAULT_LIFECYCLES.get(fault, ())}
            ids |= self.shortlist(None, 60)
            st, stm = self.climb(v, opened, f"PATH[{fault}]", only=ids, rounds=5, k365=6, frac_stop=0.3)
            self.m365_many([st])
            kk = self.key_of(st, stm)
            rec = {"fault": fault, "openers": [f"{c['switch']}={c['cand']} (move {mv:+.2f})" for c, mv in ops], "relaxed": relaxed, "opened_gain": opened["gain"], "opened_trades": opened["trades"],
                   "final_gain": stm["gain"], "final_trades": stm["trades"], "accepted": kk > cur}
            self.rep["macro"].append(rec)
            if kk > cur:
                state, state_m, cur = st, stm, kk
                self._step("PATH", f"{fault}: open {len(ops)} + relax {len(relaxed)} -> re-tighten", state, state_m)
            else:
                self.log(f"[GS-PATH] {fault} not better (opened {opened['gain']}/{opened['trades']}tr -> tightened {stm['gain']}/{stm['trades']}tr)")
        return state, state_m


def prune(S: GraphSearch, state: dict, state_m: dict) -> tuple:
    """backward elimination of changes that carry nothing (fewer changes = less live-parity surface, BIBLE §68):
    every single revert is measured on 30D; the NEUTRAL ones (status kept, gain within 0.05pp) are reverted in one batch,
    verified on 30D+365D (status kept, 365D gain within 0.5pp); a failing batch is halved (best half first)."""
    def ok30(m, ref):
        return m is not None and DR.compliant(m) >= DR.compliant(ref) and m["valid"] >= ref["valid"] and (m["gain"] or -1e9) >= (ref["gain"] or -1e9) - 0.05

    def ok365(ov, ref_ov):
        r, r0 = S.m365c.get(S._k(S.sanitize(ov))), S.m365c.get(S._k(S.sanitize(ref_ov)))
        if r0 is None:
            return True
        if r is None:
            return False
        return (r[1], bool((r[0] or {}).get("valid"))) >= (r0[1], bool((r0[0] or {}).get("valid"))) and ((r[0] or {}).get("gain") or -1e9) >= ((r0[0] or {}).get("gain") or -1e9) - 0.5

    def revert(ov, keys):
        v = dict(ov)
        for k in keys:
            if k in S.origin:
                v[k] = S.origin[k]
            else:
                v.pop(k, None)
        return v
    n0 = len(S.changes(state))
    for _round in range(3):
        if S.left() < 10:
            break
        ch = S.changes(state)
        if not ch:
            break
        res = S.evaluate([(f"PRUNE:{k}", revert(state, [k]), {"switch": k, "cand": "PRUNE", "tab": "PRUNE", "ov": {}, "revert": True}, True) for k in ch], "PRUNE", state_m["gain"])
        neutral = [(m, c["switch"]) for m, _ov, c in res if ok30(m, state_m)]
        neutral.sort(key=lambda x: DR.quality_key(x[0]), reverse=True)
        keys = [k for _m, k in neutral]
        moved = False
        while keys and S.left() > 10:
            v = revert(state, keys)
            m = S.evaluate([(f"PRUNE_BATCH:{len(keys)}", v, None, True)], "PRUNE", state_m["gain"])[0][0]
            if ok30(m, state_m):
                S.m365_many([v, state])
                if ok365(v, state):
                    state, state_m, moved = v, m, True
                    break
            keys = keys[:len(keys) // 2]
        if not moved:
            break
    S._step("PRUNE", f"{n0} -> {len(S.changes(state))} changes", state, state_m)
    return state, state_m


def autopsy_card(S: GraphSearch, ctx: dict, origin: dict, base_m: dict, scr_ledger: list):
    """per-candidate trade attribution vs the base (losers/premature/augments fixed, captured moves) + base trade classes."""
    from tools import v15_trade_autopsy as TA
    close = ctx.get("close")
    is_long = bool(ctx.get("is_long", True))
    r0 = ctx["eval_many_ledger"]([("GS_BASE", origin, None)], "GS_BASE", base_m["gain"], S.deadline)[0][0]
    if not r0 or close is None:
        return {}, None
    base = TA.classify(r0["_rows"], close, is_long)
    moves = TA.missed_moves(base, close, is_long)
    for c, r in scr_ledger:
        if not r:
            continue
        a = TA.attribute(base, r["_rows"], moves)
        S.attr[c["_id"]] = TA.scorecard(base, a)
    card = {"n_trades": len(base), "missed_moves": len(moves)}
    for t in base:
        for k in t["cls"]:
            card[k] = card.get(k, 0) + 1
    return card, base


def monthly_table(S, ctx: dict, state: dict) -> dict:
    """USER 2026-10-07: month-by-month 365D attribution (zero extra evals beyond ONE 365D+ledger eval, cached per state).
    Calendar months when npz365 timestamps align, else 12 bar-quantile slices (labeled approx). Worst slice -> mapped faults."""
    from tools import v15_trade_autopsy as TA
    if not hasattr(S, "heal_cache"):
        S.heal_cache = {}
    key = S._k(S.sanitize(state))
    if key in S.heal_cache:
        return S.heal_cache[key]
    ev = ctx.get("eval_365_ledger")
    if ev is None:
        return {"mode": "no-365-ledger", "slices": [], "worst": None}
    try:
        r, _span = ev(state)
        rows = TA.scaled_rows(r)
    except Exception:
        return {"mode": "no-rows", "slices": [], "worst": None}
    if not rows:
        return {"mode": "no-rows", "slices": [], "worst": None}
    ts, mode, nbx = None, "quantile12", max([t.get("bx", 0) for t in rows] + [0])
    try:
        _ts = ((ctx.get("npz365") or {}).get("timestamps")) or []
        _cl = ((ctx.get("npz365") or {}).get("close")) or []
        if _ts and _cl and len(_ts) == len(_cl) and nbx < len(_ts):
            ts, mode = _ts, "calendar"
    except Exception:
        ts, mode = None, "quantile12"
    import datetime as _dt
    slices = {}
    for t in rows:
        bx = t.get("bx", 0)
        if mode == "calendar":
            try:
                _e = float(ts[bx if bx < len(ts) else -1])
                sid = _dt.datetime.fromtimestamp(_e / 1000.0 if _e > 1e11 else _e, tz=_dt.timezone.utc).strftime("%Y-%m")
            except Exception:
                sid = f"Q{min(11, int(12 * bx / max(1, nbx + 1)))}~"
        else:
            sid = f"Q{min(11, int(12 * bx / max(1, nbx + 1)))}~"
        s = slices.setdefault(sid, {"id": sid, "n": 0, "pnl": 0.0, "wins": 0, "holds": [], "exits": {}})
        s["n"] += 1
        s["pnl"] += float(t.get("pnl") or 0.0)
        s["wins"] += 1 if float(t.get("pnl") or 0.0) > 0 else 0
        s["holds"].append(max(0, t.get("bx", 0) - t.get("be", 0)))
        _er = str(t.get("exit_reason") or "?")
        s["exits"][_er] = s["exits"].get(_er, 0) + 1
    out = []
    for sid in sorted(slices):
        s = slices[sid]
        out.append({"id": sid, "n": s["n"], "pnl": round(s["pnl"], 2), "wr": round(100.0 * s["wins"] / max(1, s["n"]), 1),
                    "losers": round(100.0 * (s["n"] - s["wins"]) / max(1, s["n"]), 1),
                    "avg_hold": round(sum(s["holds"]) / max(1, len(s["holds"])), 1),
                    "top_exits": sorted(s["exits"].items(), key=lambda kv: -kv[1])[:3]})
    cand = [s for s in out if s["n"] >= 5] or out
    worst = min(cand, key=lambda s: s["pnl"]) if cand else None
    res = {"mode": mode, "slices": out, "worst": worst}
    S.heal_cache[key] = res
    return res


def monthly_faults(worst: dict | None) -> list:
    """worst slice -> known fault names (openers-compatible). Honest subset: only what the slice proves."""
    if not worst:
        return []
    f = []
    if worst.get("losers", 0) > 60:
        f.append("LOSERS")
    if worst.get("avg_hold", 1e9) < 20 and worst.get("losers", 0) > 50:
        f.append("PREMATURE_EXITS")
    if worst.get("n", 0) < 5:
        f.append("TOO_FEW_TRADES")
    if worst.get("pnl", 0) < -2:
        f.append("GAIN_NEG")
    return f


def heal_365(S, ctx: dict, state: dict, state_m: dict) -> tuple:
    """USER 2026-10-07: a 365D-red side keeps working until 365D gain is positive — focused 365D-fault rounds with a
    hard 30D-compliance floor (heal can never regress live 30D). Bounded by absolute floors so prune+verify keep theirs.
    Requires measured 365D (skips honestly otherwise); monthly table targets the worst slice (cached, ~free)."""
    r = S.m365c.get(S._k(S.sanitize(state)))
    m365 = r[0] if r else None
    if m365 is None or m365.get("gain") is None:
        S.rep["macro"].append({"phase": "HEAL_365", "status": "skipped: 365D unmeasured"})
        return state, state_m
    if (m365.get("gain") or -1e9) >= 0 and r[1]:
        S.rep["macro"].append({"phase": "HEAL_365", "status": "skipped: 365D already green"})
        return state, state_m
    if S.left() < 60:
        S.rep["macro"].append({"phase": "HEAL_365", "status": "skipped: no heal budget (left<60s)"})
        return state, state_m
    cur, g365 = S.key_of(state, state_m), (m365.get("gain") or -1e9)
    for rnd in range(3):
        if S.left() < 45:
            S.rep["macro"].append({"phase": "HEAL_365", "status": "stop: verify floor (left<45s)"})
            break
        mt = monthly_table(S, ctx, state)
        S.rep["monthly"] = mt
        mix = DR.ledger_mix(ctx["eval_ledger"](state)) if ctx.get("eval_ledger") else {}
        faults = S.faults(state_m, mix, S.m365c.get(S._k(S.sanitize(state))), {})
        for mf in monthly_faults(mt.get("worst")):
            if mf not in faults:
                faults.append(mf)
        faults = [f for f in faults if "365" in f] + [f for f in faults if "365" not in f]
        scr = S.screen(state, state_m, f"HEAL_SCREEN{rnd}", only=S.shortlist(None, 300))
        ids = set()
        for fault in faults[:3]:
            try:
                ops = S.openers(fault, scr, state_m, state=state)
            except Exception:
                ops = []
            ids |= {c["_id"] for c, _mv in ops}
            ids |= {c["_id"] for c in S.cands if (S.gsw.get(c["switch"]) or {}).get("lifecycle") in FAULT_LIFECYCLES.get(fault, ())}
        ids |= S.shortlist(None, 60)
        ids = {i for i in ids if not S.cands[i]["switch"].startswith("ABLATION_")}
        if not ids:
            S.rep["macro"].append({"phase": "HEAL_365", "round": rnd, "status": "no heal ids"})
            break
        st, stm = S.climb(state, state_m, f"HEAL[{rnd}]", only=ids, rounds=3, k365=4, frac_stop=0.0)
        S.m365_many([st])
        rh = S.m365c.get(S._k(S.sanitize(st)))
        h365 = rh[0].get("gain") if rh and rh[0] else None
        rec = {"phase": "HEAL_365", "round": rnd, "faults": faults[:4], "monthly": mt.get("mode"),
               "worst": (mt.get("worst") or {}).get("id"), "gain30": stm["gain"], "gain365": h365, "left": round(S.left(), 1)}
        if DR.compliant(stm) and h365 is not None and h365 >= g365 - 1e-9 and S.key_of(st, stm) > cur:
            state, state_m, cur, g365 = st, stm, S.key_of(st, stm), h365
            rec["accepted"] = True
            S._step("HEAL_365", f"round{rnd}: 365D {g365:+.2f} ({mt.get('mode')}/{rec['worst']})", st, stm)
            S.rep.setdefault("heal_rounds", []).append(rec)
            if h365 >= 0 and rh[1]:
                S.rep["macro"].append({"phase": "HEAL_365", "status": "healed: 365D green"})
                break
        else:
            rec["accepted"] = False
            rec["why"] = "30D-noncompliant" if not DR.compliant(stm) else ("365D-unmeasured" if h365 is None else ("365D-worse" if h365 < g365 - 1e-9 else "key-stalled"))
            S.rep.setdefault("heal_rounds", []).append(rec)
            S.rep["macro"].append({"phase": "HEAL_365", "round": rnd, "status": f"no advance ({rec['why']})"})
            break
    return state, state_m


def run(ctx: dict) -> dict:
    """ctx = tools/v15_diagnose_repair.run ctx + graph (load_graph) [+ eval_many_365(ovs, deadline)->[(res, span)]].
    Returns a DIAGNOSE+REPAIR-compatible report (accepted, best_overrides, after, changes, finalists, steps) + ablation/macro."""
    t0 = time.time()
    S = GraphSearch(ctx)
    S.budget = float(ctx["deadline"]) - t0
    log = S.log
    origin = dict(S.sanitize(dict(ctx["base_overrides"])))
    base_m = DR.metrics(ctx["base_res"], S.bh)
    S.memo[S._k(origin)] = base_m
    rep = S.rep
    rep.update({"method": "graph_search", "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "n_candidates": len(S.cands), "before": base_m, "graph_engine_md5": S.G.get("engine_md5")})
    rep["gs_build"] = BUILD
    S.m365_many([origin])
    o365 = S.m365c.get(S._k(origin))
    rep["origin_365"] = {"m365": o365[0], "q365": o365[1], "why365": o365[2]} if o365 else None
    # 1 SCREEN with ledger (per-sym lever map + trade attribution)
    items = []
    for c in S.cands:
        if not S.applies(origin, c):
            continue
        v = dict(origin)
        v.update(c["ov"])
        items.append((f"{c['switch']}={c['cand']}", S.sanitize(v), c))
    scr_ledger = []
    _okey = hashlib.md5(json.dumps(sorted((k, str(v)) for k, v in origin.items())).encode()).hexdigest()
    _rg = ctx.get("resume_gs")
    if _rg and _rg.get("card") is not None and _rg.get("origin_key") == _okey:
        card, _base_trades = _rg["card"], None  # USER 2026-10-07: resume skips the 10-20 min GS ledger re-grind (same rule as autopsy)
        try: S.attr.update({int(k): v for k, v in (_rg.get("attr") or {}).items()})
        except Exception: pass
        log(f"[GS] ledger screen resumed ({len(S.attr)} attributions, no recompute)")
    elif ctx.get("eval_many_ledger"):
        for i in range(0, len(items), S.chunk):
            if S._shutdown_hit():
                S._flush_ckpt()
                raise V15Shutdown("shutdown in GS ledger screen")
            if S.left() < 0.75 * S.budget:
                break
            part = items[i:i + S.chunk]
            outs = ctx["eval_many_ledger"](part, "GS_SCREEN", base_m["gain"], S.deadline)
            for (lab, ov, c), (r, err) in zip(part, outs):
                if not r:
                    continue
                S.n_evals += 1
                m = DR.metrics(r, S.bh)
                S.memo[S._k(ov)] = m
                S.last[c["_id"]] = DR.quality_key(m, 0)
                scr_ledger.append((c, r))
            S._flush_ckpt()
        card, _base_trades = autopsy_card(S, ctx, origin, base_m, scr_ledger) if scr_ledger else ({}, None)
        try:
            _ck = ctx.get("checkpoint")
            if _ck is not None and scr_ledger:
                _ck({"gs": {"card": card, "attr": {str(k): v for k, v in S.attr.items()}, "origin_key": _okey}})
        except V15Shutdown:
            raise
        except Exception:
            pass
    else:
        card, _base_trades = {}, None
    scr0 = S.screen(origin, base_m, "GS_SCREEN")  # memo hits for everything the ledger screen measured
    mix0 = DR.ledger_mix(ctx["eval_ledger"](origin)) if ctx.get("eval_ledger") else {}
    faults0 = S.faults(base_m, mix0, o365, card)
    rep.update({"mix_before": mix0, "autopsy_card": card, "faults_before": faults0, "diagnosis_before": DR.diagnose(base_m, mix0, ctx.get("cat_side"))})
    rep["lever_map"] = [{"tab": c["tab"], "row": c.get("row"), "switch": c["switch"], "cand": str(c["cand"]), **({"d": DR._delta(m, base_m), "valid": m["valid"]} if m else {"d": None}),
                         **({"attr": {k: S.attr[c["_id"]].get(k) for k in ("losers_fixed", "premature_fixed", "aug_fixed", "captured", "net")}} if c.get("_id") in S.attr else {})}
                        for m, _ov, c in scr0 if not c.get("revert")]
    log(f"[GS] base gain={base_m['gain']} bh={base_m['bh']} tr={base_m['trades']} TIM={base_m['tim']} DD={base_m['dd']} 365D={(o365 or [None])[0]} faults={faults0} screened={len(scr0)}")
    reserve = min(max(45.0, 0.15 * S.budget), 0.3 * S.budget)  # PRUNE + 365D VERIFY always get their time
    S.deadline -= reserve
    S.budget -= reserve
    # 2 ABLATE (on the base) -> 3 CULPRITS -> 5 PATHS -> 4 REORDER; then repeat on the new state while it keeps improving
    state, state_m = origin, base_m
    scr, faults, mix = scr0, faults0, mix0
    box_ends = {}  # USER 2026-10-07: absolute phase schedule (it0 guaranteed, slosh flows forward)
    _r2, _te = S.deadline - time.time(), time.time()
    for _nm, _fr, _mn in PHASE_PLAN:
        _te += max(0.0, _r2) * _fr
        box_ends[_nm] = (_te, _mn)
    rep["phase_secs"], rep["phase_skips"] = {}, []
    for it in range(4):
        if S._shutdown_hit():
            S._flush_ckpt()
            raise V15Shutdown("shutdown in GS iterations")
        k_before = S.key_of(state, state_m)
        if it > 0:
            if S.left() < 0.3 * S.budget:
                break
            mix = DR.ledger_mix(ctx["eval_ledger"](state)) if ctx.get("eval_ledger") else {}
            S.m365_many([state])
            faults = S.faults(state_m, mix, S.m365c.get(S._k(S.sanitize(state))), {})
            scr = S.screen(state, state_m, f"GS_SCREEN{it + 1}", only=S.shortlist(None, 500))
            abl = S.ablate(state, state_m, f"IT{it}")
            state, state_m = S.culprits(state, state_m, abl)
            state, state_m = S.paths(state, state_m, scr, state_m, faults, mix)
            state, state_m = S.reorder(state, state_m, abl)
        else:
            abl = []
            _bl = _box_enter(S, box_ends, "ABLATE", rep)
            if _bl:
                with _box(S, _bl, "ABLATE"):
                    abl = S.ablate(state, state_m, "BASE")
            _bl = _box_enter(S, box_ends, "CULPRITS", rep)
            if _bl:
                with _box(S, _bl, "CULPRITS"):
                    state, state_m = S.culprits(state, state_m, abl)
            _bl = _box_enter(S, box_ends, "PATHS", rep)
            if _bl:
                with _box(S, _bl, "PATHS"):
                    state, state_m = S.paths(state, state_m, scr, base_m, faults, mix)
            _bl = _box_enter(S, box_ends, "REORDER", rep)
            if _bl:
                with _box(S, _bl, "REORDER"):
                    state, state_m = S.reorder(state, state_m, abl)
        rep.setdefault("iterations", []).append({"it": it, "faults": faults, "gain": state_m["gain"], "trades": state_m["trades"], "t": round(time.time() - t0, 1)})
        if not S.key_of(state, state_m) > k_before:
            break
    _bl = _box_enter(S, box_ends, "POLISH", rep)
    if _bl:
        with _box(S, _bl, "POLISH"):
            state, state_m = S.climb(state, state_m, "POLISH", only=S.shortlist(None, 200), rounds=6, k365=6, frac_stop=0.05)
    _bl = _box_enter(S, box_ends, "HEAL", rep)
    if _bl:
        with _box(S, _bl, "HEAL"):
            state, state_m = heal_365(S, ctx, state, state_m)  # USER 2026-10-07: 365D-red sides keep healing (bounded, 30D-safe)
    S.deadline += reserve
    if S.changes(state):
        state, state_m = prune(S, state, state_m)
    # 7 VERIFY / select
    hall = sorted(S.hall, key=lambda x: x[0], reverse=True)

    def _safe(ov):  # defensive: a finalist never carries a forbidden change (safety loosen, ablation, never-apply, parity/infra)
        return all(S._allowed_change(origin, k, ov.get(k, S.defaults.get(k))) for k in S.changes(ov) if k in ov)
    pool = [(ov, m) for ov, m in [(state, state_m)] + [(ov, m) for _k, ov, m, ch in hall[:40] if ch and DR.compliant(m)] if _safe(ov)][:9]
    S.m365_many([ov for ov, _m in pool] + [origin])
    verdicts = []
    for ov, m in [(origin, base_m)] + pool:
        r = S.m365c.get(S._k(S.sanitize(ov)))
        _prov = "origin" if ov is origin else ("pruned_state" if ov is state else S.hall_prov.get(S._mk(S._k(S.sanitize(ov))), "?"))
        verdicts.append({"ov": ov, "m": m, "changes": S.changes(ov), "prov": _prov, "m365": r[0] if r else None, "q365": bool(r and r[1]), "why365": r[2] if r else ["not evaluated"]})

    def sel(v):
        return (DR.compliant(v["m"]), v["q365"], round(score(v["m"], v["m365"]), 6), -len(v["changes"]))
    win = max(verdicts, key=sel)
    orig = verdicts[0]
    status_up = (DR.compliant(win["m"]), win["q365"]) > (DR.compliant(orig["m"]), orig["q365"])
    gain_up = (win["m"]["gain"] or -1e9) >= (orig["m"]["gain"] or -1e9) + float(ctx.get("min_improve_pp", 0.5)) and (DR.compliant(win["m"]), win["q365"]) >= (DR.compliant(orig["m"]), orig["q365"])
    rep["finalists"] = [{k: v[k] for k in ("changes", "prov", "m", "m365", "q365", "why365")} for v in verdicts]
    rep["accepted"] = bool(win is not orig and win["changes"] and (status_up or gain_up))
    rep["best_overrides"] = dict(win["ov"]) if rep["accepted"] else dict(origin)
    rep["after"] = win["m"] if rep["accepted"] else base_m
    rep["after_365"] = win["m365"] if rep["accepted"] else (o365[0] if o365 else None)
    rep["q365_after"] = win["q365"] if rep["accepted"] else bool(o365 and o365[1])
    rep["changes"] = win["changes"] if rep["accepted"] else []
    rep["accept_reason"] = ("status_up" if status_up else "gain_up") if rep["accepted"] else "origin best"
    mixa = DR.ledger_mix(ctx["eval_ledger"](rep["best_overrides"])) if (rep["accepted"] and ctx.get("eval_ledger")) else mix0
    rep["mix_after"] = mixa
    rep["diagnosis_after"] = DR.diagnose(rep["after"], mixa, ctx.get("cat_side"))
    rep["n_evals"] = S.n_evals
    rep["n_evals_365"] = len(S.m365c)
    rep["secs"] = round(time.time() - t0, 1)
    log(f"[GS] done {S.n_evals}+{len(S.m365c)}x365 evals {rep['secs']}s accepted={rep['accepted']} gain {base_m['gain']} -> {rep['after']['gain']} q365={rep['q365_after']} changes={len(rep['changes'])}")
    return rep


# ═════════════════════════ standalone driver ═════════════════════════
P30 = None
P365 = None


def _w(ov):
    from tools.opt import v12_pilot as VP
    return VP.evaluate_prepared_sanitized(P30, ov, 30)


def _wl(ov):
    from tools.opt import v12_pilot as VP
    from tools import v15_trade_autopsy as TA
    r = VP.evaluate_prepared_sanitized(P30, ov, 30, True)
    keep = {k: r.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "wr_pct", "valid", "invalid_reason", "bh_pct", "behavior_fingerprint")}
    keep["_rows"] = TA.scaled_rows(r)
    return keep


def _w365(ov):
    from tools.opt import v12_pilot as VP
    return VP.evaluate_prepared_sanitized(P365, ov, 365)


def _oom_first():
    """fleet safety: if RAM runs out, the kernel kills THIS process (and its fork workers) before any fleet pilot."""
    try:
        with open("/proc/self/oom_score_adj", "w") as fh:
            fh.write("1000")
    except Exception:
        pass


def _pdeathsig():
    """worker dies with its parent (no orphaned fork workers holding RAM on fleet hosts)."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except Exception:
        pass
    _oom_first()


def _mem_guard(min_mb: float = float(os.environ.get("GS_MIN_AVAIL_MB", "900")), max_wait_s: float = 1800.0):
    """pause while the host's MemAvailable is below min_mb (the fleet's pilots have priority)."""
    t0 = time.time()
    while time.time() - t0 < max_wait_s:
        try:
            avail = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) / 1024.0
        except Exception:
            return
        if avail >= min_mb:
            return
        time.sleep(15)


def _collect(futs, timeout_s):
    res = []
    for f in futs:
        try:
            res.append((f.result(timeout=max(0.01, timeout_s - time.time())), ""))
        except Exception as e:
            f.cancel()
            res.append((None, str(e)[:80]))
    return res


def run_symside(ss: str, out: Path, workers: int, budget: float, dirs: list, method: str) -> dict:
    global P30, P365
    import concurrent.futures as cf
    import multiprocessing as mp
    import v15_pilot as P
    from tools import v15_repair_driver as RD
    from tools.opt import v12_pilot as VP
    _oom_first()
    _mem_guard()
    t0 = time.time()
    defaults = P.get_defaults_for_symside(ss)
    san = lambda ov: P.sanitize_overrides(ov, defaults)[0]  # noqa: E731
    P30 = VP.prepare_batch(ss, 30)
    P365 = VP.prepare_batch(ss, 365)
    span = P._npz_span_days(ss)
    pool = cf.ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"), initializer=_pdeathsig)
    list(pool.map(abs, range(workers * 2)))
    cat = ("CRYPTO" if RD._is_crypto(ss) else "STOCKS") + "_" + ss.rsplit("_", 1)[1]
    try:
        prev_ov, prev_src, prev_final = RD.latest_best(ss, dirs)
        bases = [("cat_side_defaults", {})] + ([("latest_best", prev_ov)] if prev_ov else [])
        scored = []
        for lab, ov in bases:
            r = VP.evaluate_prepared_sanitized(P30, san(ov), 30)
            scored.append((DR.quality_key(DR.metrics(r)), lab, ov, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        _, base_lab, base_ov, base_res = scored[0]
        cands = RD.candidates_for(ss, defaults, P)

        def eval_many(items, phase, cum_before, deadline):
            _mem_guard()
            futs = [pool.submit(_w, ov) for _l, ov, _c in items]
            return _collect(futs, min(deadline + 60, time.time() + 10.0 * (-(-len(items) // workers) + 2)))

        def eval_many_ledger(items, phase, cum_before, deadline):
            _mem_guard()
            futs = [pool.submit(_wl, ov) for _l, ov, _c in items]
            return _collect(futs, min(deadline + 60, time.time() + 10.0 * (-(-len(items) // workers) + 2)))

        def eval_365(ov):
            return VP.evaluate_prepared_sanitized(P365, san(ov), 365), span

        def eval_many_365(ovs, deadline):
            _mem_guard()
            futs = [pool.submit(_w365, ov) for ov in ovs]
            return [(r, span) for r, _e in _collect(futs, min(deadline + 120, time.time() + 30.0 * (-(-len(ovs) // workers) + 2)))]
        close = [float(x) for x in (P30.get("npz_prepared") or {}).get("close")]
        common = {"defaults": defaults, "sanitize": san, "same_val": RD.same_val, "base_overrides": base_ov, "base_res": base_res, "bh": base_res.get("bh_pct"),
                  "eval_many": eval_many, "eval_ledger": lambda ov: VP.evaluate_prepared_sanitized(P30, san(ov), 30, True), "eval_365": eval_365, "qualifies_365": P._qualifies_365d,
                  "cat_side": cat, "eval_many_ledger": eval_many_ledger, "close": close, "npz": P30.get("npz_prepared") or {}, "is_long": ss.endswith("_LONG"), "touch": lambda m: None}
        import hashlib
        def _md5(path):
            try:
                return hashlib.md5(Path(path).read_bytes()).hexdigest()[:8]
            except Exception:
                return None
        vdh = hashlib.md5()
        for vp in sorted((ROOT / "vec_decisions").glob("*.py")):
            vdh.update(vp.read_bytes())
        prov = {"engine": _md5(ROOT / "v12_quick_engine.py"), "evaluate_v12": _md5(ROOT / "tools/opt/evaluate_v12.py"), "v15_pilot": _md5(ROOT / "v15_pilot.py"),
                "config": _md5(ROOT / "config.py"), "config_tradier": _md5(ROOT / "config_tradier.py"), "vec_decisions": vdh.hexdigest()[:8],
                "graph_search": _md5(ROOT / "tools/v15_graph_search.py"), "diagnose_repair": _md5(ROOT / "tools/v15_diagnose_repair.py"), "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        summ = {"symside": ss, "cat_side": cat, "base": base_lab, "provenance": prov, "prev_src": prev_src, "prev_final_gain": prev_final, "span_days": span, "bh": base_res.get("bh_pct"),
                "bases": [{"label": lab, "gain": (r or {}).get("gain_pct"), "trades": (r or {}).get("trades"), "valid": (r or {}).get("valid")} for _, lab, _, r in scored]}
        reports = {}
        # both = DR then GS from the same base; chain = DR (budget) then GS from DR's choice (budget) — the in-pilot integration;
        # dr2 = DR alone with 2x budget (equal-compute control for chain)
        meths = {"both": ["dr", "gs"], "chain": ["dr2", "chain"]}.get(method, [method])
        for meth in meths:
            tm = time.time()
            ctx = {**common, "candidates": [dict(c) for c in cands], "deadline": time.time() + (2 * budget if meth == "dr2" else budget), "log": (lambda m, meth=meth: print(f"{m} [{ss} {meth}]", flush=True))}
            if meth == "chain":
                rdr = DR.run({**ctx, "candidates": [dict(c) for c in cands]})
                start_ov = dict(rdr.get("best_overrides") or base_ov)
                sres = VP.evaluate_prepared_sanitized(P30, san(start_ov), 30)
                ctx2 = {**ctx, "candidates": [dict(c) for c in cands], "base_overrides": start_ov, "base_res": sres, "deadline": time.time() + budget, "graph": load_graph(cat), "eval_many_365": eval_many_365}
                rep = run(ctx2)
                rep["before"] = DR.metrics(base_res, base_res.get("bh_pct"))  # report vs the common base
                rep["origin_365"] = rdr.get("origin_365")
                rep["dr_stage"] = {"accepted": rdr.get("accepted"), "after": rdr.get("after"), "changes": rdr.get("changes")}
                if rep.get("accepted"):
                    bo = rep.get("best_overrides") or {}
                    rep["changes"] = sorted(k for k in set(bo) | set(base_ov) if not RD.same_val(bo.get(k, defaults.get(k)), base_ov.get(k, defaults.get(k))))
                if not rep.get("accepted"):  # GS kept DR's set: the chain result is DR's result
                    fin = next((f for f in rdr.get("finalists", []) if f["changes"] == rdr.get("changes")), None)
                    rep["after"] = rdr.get("after") if rdr.get("accepted") else rep["before"]
                    rep["after_365"] = (fin or {}).get("m365") if rdr.get("accepted") else ((rdr.get("origin_365") or {}).get("m365"))
                    rep["q365_after"] = (fin or {}).get("q365") if rdr.get("accepted") else ((rdr.get("origin_365") or {}).get("q365"))
                    rep["changes"] = rdr.get("changes") or []
                    rep["accepted"] = rdr.get("accepted")
            elif meth == "gs":
                ctx["graph"] = load_graph(cat)
                ctx["eval_many_365"] = eval_many_365
                rep = run(ctx)
            else:
                rep = DR.run(ctx)
                fin = next((f for f in rep.get("finalists", []) if f["changes"] == rep.get("changes")), None)
                rep["after_365"] = (fin or {}).get("m365") if rep.get("accepted") else ((rep.get("origin_365") or {}).get("m365"))
                rep["q365_after"] = (fin or {}).get("q365") if rep.get("accepted") else ((rep.get("origin_365") or {}).get("q365"))
            secs = round(time.time() - tm, 1)
            b, a, a365 = rep.get("before") or {}, rep.get("after") or {}, rep.get("after_365") or {}
            bh = b.get("bh")
            o365 = (rep.get("origin_365") or {}).get("m365") or {}
            reports[meth] = rep
            summ[meth] = {"gain_before": b.get("gain"), "gain_after": a.get("gain"), "trades_after": a.get("trades"), "tim_after": a.get("tim"), "dd_after": a.get("dd"),
                          "x_bh_before": (b.get("gain") / bh) if (bh and bh > 0 and b.get("gain") is not None) else None, "x_bh_after": (a.get("gain") / bh) if (bh and bh > 0 and a.get("gain") is not None) else None,
                          "gain_minus_bh_after": (a.get("gain") - bh) if (bh is not None and a.get("gain") is not None) else None,
                          "compliant_after": DR.compliant(a) if a else None, "gain365_before": o365.get("gain"), "q365_before": (rep.get("origin_365") or {}).get("q365"),
                          "gain365_after": a365.get("gain"), "dd365_after": a365.get("dd"), "trades365_after": a365.get("trades"), "q365_after": rep.get("q365_after"),
                          "accepted": rep.get("accepted"), "n_changes": len(rep.get("changes") or []), "changes": rep.get("changes"), "n_evals": rep.get("n_evals"), "secs": secs}
            (out / f"{ss}.{meth}.json").write_text(json.dumps({**{k: v for k, v in rep.items()}, "symside": ss, "base_overrides": base_ov, "base_label": base_lab, "provenance": prov}, default=str))
            print(f"[GS-DRIVER] {ss} {meth}: gain {b.get('gain')} -> {a.get('gain')} (bh {bh}) 365D {o365.get('gain')} -> {a365.get('gain')} q365={rep.get('q365_after')} accepted={rep.get('accepted')} {secs}s", flush=True)
        if "gs" in reports:
            abl_dir = out / "ablation"
            abl_dir.mkdir(exist_ok=True)
            (abl_dir / f"{ss}.json").write_text(json.dumps({"symside": ss, "ablation": reports["gs"].get("ablation", [])}, default=str))
        summ["secs"] = round(time.time() - t0, 1)
        with open(out / "summary.jsonl", "a") as fh:
            fh.write(json.dumps(summ, default=str) + "\n")
        return summ
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _child(args):
    ss, out, workers, budget, dirs, method = args
    try:
        return run_symside(ss, Path(out), workers, budget, dirs, method)
    except Exception as e:
        import traceback
        err = {"symside": ss, "error": f"{type(e).__name__}: {e}"[:300], "trace": traceback.format_exc()[-2000:]}
        with open(Path(out) / "summary.jsonl", "a") as fh:
            fh.write(json.dumps(err) + "\n")
        return err


def main():
    import argparse
    import concurrent.futures as cf
    import glob
    import multiprocessing as mp
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--method", choices=["gs", "dr", "both", "chain"], default="both")
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--budget", type=float, default=900.0)
    ap.add_argument("--progress-dirs", default="data/reports/lifecycle_pilot,~/v15_run*/progress,~/v15_run*")
    a = ap.parse_args()
    _oom_first()
    os.chdir(ROOT)
    dirs = []
    for d in a.progress_dirs.split(","):
        dirs += glob.glob(os.path.expanduser(d.strip())) or [os.path.expanduser(d.strip())]
    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    done = set()
    if (out / "summary.jsonl").exists():
        for line in open(out / "summary.jsonl"):
            try:
                j = json.loads(line)
                if not j.get("error"):
                    done.add(j["symside"])
            except Exception:
                pass
    todo = [s for s in a.symsides.split(",") if s and s not in done]
    print(f"[GS-DRIVER] {len(todo)} sym_sides method={a.method} budget={a.budget}s workers={a.workers} parallel={a.parallel} out={out}", flush=True)
    # one fresh spawned process per sym_side (own NPZ + own fork pool); an OOM-killed sym_side (oom_score_adj=1000: the kernel
    # takes ours before any fleet pilot) is retried once, never takes the queue down
    tries = {s: 0 for s in todo}
    queue = list(todo)
    running = {}
    ctx_mp = mp.get_context("spawn")
    while queue or running:
        while queue and len(running) < a.parallel:
            s = queue.pop(0)
            ex = cf.ProcessPoolExecutor(max_workers=1, mp_context=ctx_mp, initializer=_pdeathsig)
            running[ex.submit(_child, (s, str(out), a.workers, a.budget, dirs, a.method))] = (s, ex)
        done_f, _ = cf.wait(list(running), return_when=cf.FIRST_COMPLETED)
        for fut in done_f:
            s, ex = running.pop(fut)
            try:
                r = fut.result()
                print(f"[GS-DRIVER] finished {r.get('symside')} {r.get('error', '')}", flush=True)
            except Exception as e:
                tries[s] += 1
                print(f"[GS-DRIVER] {s} process died ({type(e).__name__}) try {tries[s]}", flush=True)
                if tries[s] < 2:
                    _mem_guard()
                    queue.append(s)
            ex.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
