#!/usr/bin/env python3
"""EVERY switch and filter in any TEMPLATE_* file must resolve PER-SYM in live. Audited, and enforceable.

USER 2026-10-20: "make sure EACH switch and filter that is in any TEMPLATE_* file is ALWAYS psym and can never be
global" / "if not parity is impossible".

That is the logical foundation of parity, not a style preference: the pilot measures a candidate value FOR ONE
sym_side and the board stores the winner per sym_side. If any live read of that knob takes the GLOBAL value, live
cannot reproduce the board for that sym_side no matter what else is fixed — the measured delta was earned against
a number live never applies. Proven case 2026-10-20: GOLDEN_RULE_HTF_MIN_TFS was per-sym on the crypto ENTRY path
(ez_manage.py:18529/:44689) and GLOBAL on the EXIT path (:53673), so one rule ran on two different numbers.

WHY AST AND NOT GREP. `_psym_get(sym, side, "K", getattr(config, "K", 1))` contains BOTH a per-sym call and a
getattr — the getattr is only the DEFAULT. A regex scores that as a global read and would report every correctly
fixed site as broken. So this walks the AST and attributes each key reference to its ENCLOSING call:
  PSYM      a per-sym resolver call (_psym_get/_sw_get/_psym_cs_get/_cfg with a symbol argument)
  GLOBAL    getattr(config|config_tradier|cfg, "KEY", ...) or config.KEY, NOT nested inside a PSYM call
  DISCARDED `_ = <read>` — the "wire N missing switches" stub pattern; reads the value and throws it away
  NAMELIST  the key only appears as a bare string in a list/set/dict (an allowlist, not a read)

Verdicts per key: OK (per-sym only) | GLOBAL_LEAK (>=1 real global read) | INERT (only discarded/namelist) |
ABSENT (no reference in any live module).

GLOBAL_LEAK rows carry `symbol_bound_in_func`: whether a symbol/side name is bound ANYWHERE in the enclosing
function. It is a TRIAGE HINT, not proof the site is fixable: this does no flow analysis, so a symbol bound 200
lines BELOW the read still counts. Measured example — ez_manage.py:18285 (GOLDEN_RULE_ENABLED) reports True, yet
that read is the master check at the top of `_golden_rule_loop(self)` where no symbol exists yet; the loop binds
one later per candidate. Such sites need a per-sym check ADDED at the candidate level, which CHANGES BEHAVIOUR
(it starts vetoing trades) and is not a resolution fix. Always read the site before editing it.

Exit 0 = no global leaks, 1 = leaks found, 2 = could not audit. Read-only.
"""
from __future__ import annotations

import argparse
import ast
import collections
import glob
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(os.environ.get("BASE_PATH") or pathlib.Path(__file__).resolve().parents[1])

PSYM_FUNCS = {"_psym_get", "_sw_get", "_psym_cs_get", "_cfg", "_cfg_auto", "_psym_cs", "get_knob", "_ezm_per_sym_layer"}
# ONLY the module-level GLOBAL config objects. `cfg` is deliberately NOT here: in the vector path and in
# vec_decisions/* `cfg` is the ALREADY-RESOLVED per-sym set handed to the engine, so `getattr(cfg, KEY)` is a
# per-sym read and counting it as global produced a false positive on vec_paths/gr_filter_vec.py:136 in the first
# run. `config` / `config_tradier` are the one-value-for-every-sym_side module objects — those are the real risk.
GLOBAL_OBJS = {"config", "config_tradier", "_ezm_base_config", "_lc"}
# live decision modules only: the engines (v12_*, v8_*, v11_*) and vec_paths/* are the VECTOR, which is HANDED a
# per-sym cfg and legitimately reads it flat. Auditing them would measure the wrong thing.
LIVE_MODULES = ("ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py", "tradier_manage.py",
                "tradier_vec_exact.py", "positions_truth.py", "ez_positions_service_main.py")
LIVE_GLOBS = ("live_twins/*.py",)
# SHARED CORES. `vec_decisions/*.py` is not simply "the vector": the live modules IMPORT 38 of them and call into
# them with a PER-SYM cfg — fixed_stop, bb_stoch_exits, dc_channel_exits, breakeven_gain_erosion, qty_quantizers,
# stop_reclaim ... — which is this repo's twin pattern (one core, read by both the engine and live). Excluding
# them made the audit report 94 keys as ABSENT that live really does resolve, and it also meant a correct fix
# routed through a shared core (the STDEV_TWIN_SIZING patch applied 2026-10-20 19:4xZ) still read as ABSENT, so
# the acceptance test could never pass. Only cores a live module actually imports are scanned; reads there are
# PROXY_PSYM because the cfg handed in is the resolved per-sym set.
# CAVEAT, stated because the tool cannot prove it: IMPORTED IS NOT CALLED. Modules named *_dead
# (twin_entries_dead_a/_b, twin_exits_dead) are imported while their reading functions may have no live caller, so
# a key credited here can still be inert. Proving the call path is a human/agent read, not a grep.
def live_imported_cores(root: pathlib.Path) -> list:
    """Every shared core a live module imports: vec_decisions/* AND root-level modules.

    2026-10-20: the first version scanned only `vec_decisions/`, which missed ROOT-LEVEL cores that live imports
    and that do read template switches — `loss_prone.py` (LOSS_PRONE_ENTRY_FILTER_ENABLED /
    LOSS_PRONE_ENTRY_MIN_FILTERS, reached from ez_manage.py:32339) and `golden_rule_htf.py`. Both were therefore
    reported ABSENT while live genuinely resolves them. Discovering them from the imports means a core added
    later is covered without editing this list."""
    out, names = [], set()
    for m in LIVE_MODULES:
        f = root / m
        if not f.exists():
            continue
        src = f.read_text(errors="ignore")
        names |= set(re.findall(r"vec_decisions[._]([a-z_0-9]+)", src))
        for n in set(re.findall(r"^\s*(?:import|from)\s+([a-z_][a-z_0-9]*)", src, re.M)):
            cand = root / f"{n}.py"
            if cand.exists() and cand.name not in LIVE_MODULES and cand not in out:
                out.append(cand)
    out += [root / "vec_decisions" / f"{n}.py" for n in sorted(names) if (root / "vec_decisions" / f"{n}.py").exists()]
    return out

# DECLARED PROBE FARMS: functions whose entire purpose is to REFERENCE switches so they count as "wired".
# Read and verified 2026-10-20 — every body is reference-only, e.g. ez_manage.py:64448
#   if bool(getattr(config, "AUGMENT_BOUNCE_MIN_GAIN_PCT", False)):
#       try: _a=float(locals().get("position", {}).get("gain",0) if 'position' in locals() else 0)
#       except: _a=0
#       _=_a  # AUGMENT_BOUNCE_MIN_GAIN_PCT
# `locals()` in a function whose only parameter is `config` can never hold a position, so these compute nothing
# and assign to `_`. Their varied body shapes defeat a purely structural test, and counting them as live reads
# overstated the defect by 203 sites, so they are named here on the evidence of their source.
PROBE_FUNCS = {"_ensure_ez_all", "_ensure_tradier_all"}
SYM_NAMES = {"symbol", "position_side", "is_long", "sym", "side", "_sym", "_side", "position_key", "target_symbol"}
NON_SWITCH_HDRS = {"Switch", "default", "override", "Family", "BASELINE", "HUSTLE_DELTA", "VECTOR_DELTA",
                   "LIVE_DELTA", "LIVE_SHARPE", "REAL_COMPLETE", "PER_ROW_FILTERS", "is_default", "AVG_DELTA", "POS_SYM"}
SKIP_TABS = {"PROTOCOL", "LEGEND_FILTERS", "INSTRUCTIONS", "INSTRUCTIONS_V2", "FILTERS_EXPLAINED",
             "FILTER_DICTIONARY_V2", "Results_Deltas", "Results_30d_Deltas"}
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")


def template_keys(paths: list) -> dict:
    """{KEY: {"switch_tabs": set, "filter_tabs": set, "templates": set}} from col A (switches) and the
    row-2 `KEY=VALUE` headers from col O onward (per-row yellow filters)."""
    import openpyxl
    out: dict = collections.defaultdict(lambda: {"switch_tabs": set(), "filter_tabs": set(), "templates": set()})
    for p in paths:
        name = pathlib.Path(p).name
        try:
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        except Exception as exc:
            print(f"[warn] {name}: {exc}", file=sys.stderr)
            continue
        for ws in wb.worksheets:
            if ws.title in SKIP_TABS or "BASELINE" in ws.title:
                continue
            rows = ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 1200), max_col=min(ws.max_column or 1, 260), values_only=True)
            for i, r in enumerate(rows, 1):
                if not r:
                    continue
                if i == 2:  # header row: filter columns are `KEY=VALUE`
                    for cell in r[14:]:
                        if not cell:
                            continue
                        k = str(cell).split("=", 1)[0].strip().upper()
                        if KEY_RE.match(k) and k not in NON_SWITCH_HDRS:
                            out[k]["filter_tabs"].add(ws.title)
                            out[k]["templates"].add(name)
                    continue
                if i >= 3 and r[0]:
                    k = str(r[0]).strip().upper()
                    if KEY_RE.match(k) and k not in NON_SWITCH_HDRS:
                        out[k]["switch_tabs"].add(ws.title)
                        out[k]["templates"].add(name)
        wb.close()
    return out


def proxy_backed(path: pathlib.Path) -> bool:
    """True when this module's module-level `config` is a PER-SYM PROXY, so `getattr(config, "KNOB")` inside it
    already resolves per-sym and is NOT a global read.

    This distinction is the whole audit. Measured 2026-10-20 — the LAST module-level binding wins:
      ez_manage.py:7879            config = _ezm_base_config            (placeholder)
      ez_manage.py:8874            config = _PerSymProxy(_ezm_base_config)   <- WINS, so ez_manage is per-sym
      ez_positions_quick.py:304    config = Config()                    plain, never wrapped
      tradier_manage.py:6764       config = TradierConfig()             plain, never wrapped
      ez_positions_service.py:435  config = Config()                    plain, never wrapped
    `_PerSymProxy.__getattribute__` (ez_manage.py:8848) intercepts every UPPERCASE name, reads
    `_psym_ctx_var` (set per position via ez_manage.py:7842) and returns `_ezm_per_sym_layer(sym, side)[name]`
    when the board carries it. So an ez_manage `getattr(config, KNOB)` is per-sym WHENEVER THE CONTEXT IS SET —
    a first pass that counted those as global overstated the defect by 420 sites. The residual ez_manage risk is
    a read on a path where the context var is None, which falls through to the base: that is a
    context-coverage question, not a call-site question, and needs runtime instrumentation, not grep.
    """
    try:
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
    except Exception:
        return False

    # Walk MODULE-LEVEL statements, descending into module-level If/Try bodies: a rebind there is still a module
    # global. The first version of this used `re.match(r"^config\\s*=", line)`, which missed
    # ez_positions_quick.py's `if os.environ.get("PQ_PER_SYM_PROXY","1") != "0": config = _PQPerSymProxy(config)`
    # because that line is INDENTED — and so reported a module as unprotected after it had been fixed.
    # Function bodies are deliberately NOT descended into: a local named `config` is not the module global.
    found = [False]

    def scan(body):
        for st in body:
            if isinstance(st, ast.Assign):
                for tgt in st.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "config":
                        v = st.value
                        nm = ""
                        if isinstance(v, ast.Call):
                            f = v.func
                            nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                        found[0] = "Proxy" in nm
            elif isinstance(st, (ast.If, ast.Try)):
                scan(st.body)
                scan(getattr(st, "orelse", []) or [])
                scan(getattr(st, "finalbody", []) or [])
    scan(tree.body)
    return found[0]


def _fname(node) -> str:
    f = getattr(node, "func", None)
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def scan_module(path: pathlib.Path, keys: set, proxy: bool = False) -> list:
    """[{key, kind, line, func, fixable_in_place}] for every reference to a template key."""
    try:
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
    except Exception as exc:
        print(f"[warn] cannot parse {path}: {exc}", file=sys.stderr)
        return []
    parent, enclosing_fn, fn_syms, dead_from = {}, {}, {}, {}
    fn_stack = []

    class Walk(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            self._fn(node)

        def visit_AsyncFunctionDef(self, node):
            self._fn(node)

        def _fn(self, node):
            names = {a.arg for a in list(node.args.args) + list(node.args.kwonlyargs)}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    names.add(sub.id)
                elif isinstance(sub, ast.arg):
                    names.add(sub.arg)
            # UNREACHABLE-CODE BOUNDARY. A function whose body hits an unconditional return/raise at TOP level
            # has dead code after it, and a global read in dead code is not a live read at all. Measured
            # 2026-10-20: ez_manage._batch1_template_live_gate begins `return True, ''` (line 7269) and then
            # carries 59 `getattr(config, "<template switch>")` reads that can never execute — reporting those as
            # parity defects would overstate the problem by a third.
            for stmt in node.body:
                if isinstance(stmt, (ast.Return, ast.Raise)):
                    dead_from[node.name] = min(dead_from.get(node.name, 10**9), getattr(stmt, "lineno", 10**9))
                    break
            fn_stack.append((node.name, names))
            self.generic_visit(node)
            fn_stack.pop()

        def generic_visit(self, node):
            for child in ast.iter_child_nodes(node):
                parent[child] = node
                if fn_stack:
                    enclosing_fn[child] = fn_stack[-1][0]
                    fn_syms[child] = fn_stack[-1][1]
            super().generic_visit(node)

    Walk().visit(tree)

    def ancestors(node, limit=14):
        out, cur = [], node
        for _ in range(limit):
            cur = parent.get(cur)
            if cur is None:
                break
            out.append(cur)
        return out

    hits = []
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip().upper() in keys:
            key = node.value.strip().upper()
        elif isinstance(node, ast.Attribute) and node.attr.upper() in keys and isinstance(node.value, ast.Name) and node.value.id in GLOBAL_OBJS:
            key = node.attr.upper()
        if key is None:
            continue
        anc = ancestors(node)
        calls = [a for a in anc if isinstance(a, ast.Call)]
        names = [_fname(c) for c in calls]
        kind = "NAMELIST"
        if any(n in PSYM_FUNCS for n in names):
            kind = "PSYM"
        elif isinstance(node, ast.Attribute):
            kind = "GLOBAL"
        elif names and names[0] == "getattr":
            a0 = calls[0].args[0] if calls[0].args else None
            if isinstance(a0, ast.Name) and a0.id in GLOBAL_OBJS:
                kind = "GLOBAL"
            elif isinstance(a0, ast.Attribute) and a0.attr in GLOBAL_OBJS:
                kind = "GLOBAL"
        if kind == "GLOBAL" and proxy:
            kind = "PROXY_PSYM"   # per-sym via _PerSymProxy whenever _psym_ctx_var is set
        if kind == "GLOBAL":
            assign = next((a for a in anc if isinstance(a, ast.Assign)), None)
            if assign is not None and len(assign.targets) == 1 and isinstance(assign.targets[0], ast.Name) and assign.targets[0].id == "_":
                kind = "DISCARDED"
            # PROBE FARM: `if <read>: _ = 1`. The read happens and the result is thrown away, so it gates nothing.
            # This is the "wire N missing config.py switches" pattern; ez_manage._ensure_ez_all (377 sites) and
            # tradier_manage._ensure_tradier_all (290) are entirely built of it — 667 of 1787 raw sites.
            if kind == "GLOBAL":
                iff = next((a for a in anc if isinstance(a, ast.If)), None)
                if iff is not None and all(
                    isinstance(b, ast.Pass) or (isinstance(b, ast.Assign) and len(b.targets) == 1
                                                and isinstance(b.targets[0], ast.Name) and b.targets[0].id == "_")
                    for b in iff.body):
                    kind = "DISCARDED"
            if kind == "GLOBAL" and enclosing_fn.get(node) in PROBE_FUNCS:
                kind = "DISCARDED"
            # DEAD: after an unconditional top-level return/raise in the enclosing function.
            if kind == "GLOBAL":
                _fn = enclosing_fn.get(node)
                _cut = dead_from.get(_fn)
                if _cut is not None and getattr(node, "lineno", 0) > _cut:
                    kind = "DEAD"
        syms = fn_syms.get(node, set())
        hits.append({"key": key, "kind": kind, "line": getattr(node, "lineno", 0),
                     "func": enclosing_fn.get(node, "<module>"),
                     "symbol_bound_in_func": bool(syms & SYM_NAMES)})
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_*.xlsx"))
    ap.add_argument("--json", default=str(ROOT / "data" / "reports" / "template_switch_psym_audit.json"))
    ap.add_argument("--max-leaks", type=int, default=0)
    ap.add_argument("--only-key")
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()

    paths = sorted(glob.glob(a.templates))
    if not paths:
        print(f"no templates matched {a.templates}")
        return 2
    tkeys = template_keys(paths)
    if a.only_key:
        tkeys = {k: v for k, v in tkeys.items() if k == a.only_key.upper()}
    keys = set(tkeys)
    print(f"templates: {len(paths)}  distinct switch+filter keys: {len(keys)}")

    mods = [ROOT / m for m in LIVE_MODULES if (ROOT / m).exists()]
    for g in LIVE_GLOBS:
        mods += [pathlib.Path(p) for p in sorted(glob.glob(str(ROOT / g))) if not pathlib.Path(p).name.startswith("test_")]
    cores = live_imported_cores(ROOT)
    mods += cores
    print(f"shared cores imported by live: {len(cores)}")
    per_key = collections.defaultdict(list)
    for m in mods:
        _is_core = m.parent.name == "vec_decisions" or (m.parent == ROOT and m.name not in LIVE_MODULES)
        for h in scan_module(m, keys, proxy=(proxy_backed(m) or _is_core)):
            h["module"] = m.name if m.parent == ROOT else f"{m.parent.name}/{m.name}"
            per_key[h["key"]].append(h)
    print(f"live modules scanned: {len(mods)}")

    rows, verdicts = [], collections.Counter()
    for k in sorted(keys):
        hs = per_key.get(k, [])
        kinds = collections.Counter(h["kind"] for h in hs)
        globals_ = [h for h in hs if h["kind"] == "GLOBAL"]
        if globals_:
            v = "GLOBAL_LEAK"
        elif kinds.get("PSYM") or kinds.get("PROXY_PSYM"):
            v = "OK"
        elif hs:
            v = "INERT"   # only probe-farm / dead-code / namelist references: live reads it nowhere that acts
        else:
            v = "ABSENT"
        verdicts[v] += 1
        rows.append({"key": k, "verdict": v, "n_psym": kinds.get("PSYM", 0), "n_global": len(globals_),
                     "n_discarded": kinds.get("DISCARDED", 0), "n_namelist": kinds.get("NAMELIST", 0),
                     "n_dead": kinds.get("DEAD", 0), "n_proxy_psym": kinds.get("PROXY_PSYM", 0),
                     "templates": sorted(tkeys[k]["templates"]), "switch_tabs": sorted(tkeys[k]["switch_tabs"]),
                     "filter_tabs": sorted(tkeys[k]["filter_tabs"]),
                     "global_sites": [{"module": g["module"], "line": g["line"], "func": g["func"],
                                       "symbol_bound_in_func": g["symbol_bound_in_func"]} for g in globals_[:12]]})
    leaks = [r for r in rows if r["verdict"] == "GLOBAL_LEAK"]
    fixable = sum(1 for r in leaks for g in r["global_sites"] if g["symbol_bound_in_func"])
    nofix = sum(1 for r in leaks for g in r["global_sites"] if not g["symbol_bound_in_func"])
    rep = {"templates": paths, "n_keys": len(keys), "verdicts": dict(verdicts), "n_leak_keys": len(leaks),
           "n_global_sites_fixable_in_place": fixable, "n_global_sites_needing_restructure": nofix, "rows": rows}
    pathlib.Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.json).write_text(json.dumps(rep, indent=1))

    print(f"\nverdicts: {dict(verdicts)}")
    print(f"GLOBAL_LEAK keys: {len(leaks)}   global read sites: {fixable} fixable in place, {nofix} need a per-sym check added")
    print(f"\ntop {a.top} leak keys by global-site count:")
    for r in sorted(leaks, key=lambda x: -x["n_global"])[:a.top]:
        tabs = ",".join(r["switch_tabs"][:2]) or ",".join(r["filter_tabs"][:2])
        print(f"  {r['key']:46s} global={r['n_global']:3d} psym={r['n_psym']:3d}  tabs={tabs[:34]:36s}")
        for g in r["global_sites"][:3]:
            print(f"        {g['module']}:{g['line']} in {g['func'][:34]}  sym_in_func={g['symbol_bound_in_func']}")
    print(f"\nwrote {a.json}")
    return 1 if len(leaks) > a.max_leaks else 0


if __name__ == "__main__":
    sys.exit(main())
