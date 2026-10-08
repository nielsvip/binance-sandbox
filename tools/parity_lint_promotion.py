"""Parity lint for per_sym promotions: live must trade EXACTLY what vec decided.

UNTESTED 2026-10-03 (no shell in session) — run pytest + a manual probe before trusting.

Usage:
    python3 tools/parity_lint_promotion.py --sym-side COTIUSDT_SHORT \\
        --overrides-json /tmp/coti_cumulative.json [--reasons GOLDEN_RULE_LONG_mult5 ...]
    python3 tools/parity_lint_promotion.py --scan-sources   # M7 source guard

Checks a candidate override set (e.g. progress cumulative_overrides) for every
known vec-live divergence BEFORE upsert into per_sym_store:
  M1  no key may be in the MASTER-2 mask (_NON_VEC_KNOBS_EZ, ez_manage.py) —
      live forces those OFF, so a vec proof that relied on them is void live.
  M2  every reason the set can emit must be vec-achievable
      (vec_paths/vec_parity_gate.py) — else STRICT_VEC_PARITY_MODE blocks it.
  M3  no 3m/5m selectors or _3M/_5M switches ON — vec's min-decision-TF guard
      clamps them, live has no twin (values would diverge).
  M4  sweep-only overrides (data/sweep_cat_overrides.json) the set does not pin
      — vec proofs assumed the sweep value, live runs the live value.
  M5  conflicts_quick_vs_live (data/per_sym_settings.json _meta) intersecting
      the set — documented quick/live default disagreements.
  M6  dep-master completeness (data/switch_dependencies.json): every sub-knob in
      the set must bring the masters vec forced ON, else live runs the sub-knob
      without its master. Masters already ON in live cat_side defaults pass.
  M7  NPZ freshness (--progress-json): the proof's npz_id stamp must match the
      current S1 NPZ manifest (data/npz_manifest_s1.json); stale proofs
      (ALGO rot) fail closed. No --progress-json: warning only.

Exit 0 = clean, 1 = errors, 2 = warnings only. JSON report on stdout.
"""
import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_ast_set(path, name):
    try:
        tree = ast.parse(Path(path).read_text())
    except Exception as e:
        return None, f"ast parse fail {path}: {e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return set(ast.literal_eval(node.value)), ""
                    except Exception:
                        pass
                    try:
                        v = node.value
                        if isinstance(v, ast.Call) and getattr(v.func, "id", "") in ("frozenset", "set", "tuple", "list") and v.args:
                            return set(ast.literal_eval(v.args[0])), ""
                        return None, f"non-literal {name}: {type(v).__name__}"
                    except Exception as e:
                        return None, f"literal_eval fail {name}: {e}"
    return None, f"{name} not found in {path}"


def _load_ast_tuple(path, names):
    toks = []
    try:
        tree = ast.parse(Path(path).read_text())
    except Exception as e:
        return None, f"ast parse fail {path}: {e}"
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    try:
                        toks.extend(ast.literal_eval(node.value))
                        found.add(t.id)
                    except Exception as e:
                        return None, f"literal_eval fail {t.id}: {e}"
    missing = set(names) - found
    if missing:
        return None, f"missing {sorted(missing)} in {path}"
    return [str(t).upper() for t in toks], ""


LOW_TF_KEY_RE = None
LOW_TF_ATTR_RE = None


def _low_tf_res():
    global LOW_TF_KEY_RE, LOW_TF_ATTR_RE
    if LOW_TF_KEY_RE is None:
        import re as _re
        LOW_TF_KEY_RE = _re.compile(r"""["']([A-Za-z0-9_]*_(1m|3m|5m)(_prev\d*)?)["']""")
        LOW_TF_ATTR_RE = _re.compile(r"""\.([A-Za-z_][A-Za-z0-9_]*_(1m|3m|5m))\b""")
    return LOW_TF_KEY_RE, LOW_TF_ATTR_RE


def _func_spans(path):
    import ast as _ast
    try:
        tree = _ast.parse(Path(path).read_text())
    except Exception:
        return []
    spans = []
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None) or 10 ** 9
            spans.append((node.lineno, end, node.name))
    return spans


def _enclosing(spans, lineno):
    best = None
    for s, e, n in spans:
        if s <= lineno <= e and (best is None or s > best[0]):
            best = (s, e, n)
    return best[2] if best else "<module>"


def scan_sources():
    """M7: every live 1m/3m/5m read must sit behind the MASTER-2 value guard.

    Fails while the ii() funnel lacks the strip (Edit 1 of
    patches/PENDING_unlock_master2_valueguard_20261003.md). Reports all
    low-TF read sites per file:function for the phase-2 trace. *_prev keys are
    reported separately: vec guard leaves them raw too (both-raw =
    parity-neutral), so they are informational, not errors.
    """
    key_re, attr_re = _low_tf_res()
    errors, warnings, sites, prev_sites = [], [], [], []
    for fname in ("ez_manage.py", "ez_positions_quick.py"):
        p = ROOT / fname
        try:
            lines = p.read_text().splitlines()
        except Exception as e:
            errors.append(f"M7 {fname} unreadable: {e}")
            continue
        spans = _func_spans(p)
        for i, line in enumerate(lines, 1):
            for m in list(key_re.finditer(line)) + [None]:
                if m is None:
                    for am in attr_re.finditer(line):
                        sites.append({"file": fname, "line": i,
                                      "func": _enclosing(spans, i), "key": am.group(1)})
                    continue
                key = m.group(1)
                if m.group(3):
                    prev_sites.append({"file": fname, "line": i,
                                       "func": _enclosing(spans, i), "key": key})
                else:
                    sites.append({"file": fname, "line": i,
                                  "func": _enclosing(spans, i), "key": key})
    try:
        ezm = (ROOT / "ez_manage.py").read_text()
        ii_start = ezm.index("async def ii(")
        ii_body = ezm[ii_start:ii_start + 30000]
        if "guard_npz" not in ii_body and "_parity_strip_low_tf" not in ii_body:
            errors.append("M7 ii() funnel UNGUARDED: no guard_npz/strip call (apply Edit 1, then re-run)")
        ps_start = ezm.index("def _psym_get(")
        ps_body = ezm[ps_start:ps_start + 30000]
        if "_parity_clamp_tf_value" not in ps_body and "parity_clamp" not in ps_body:
            errors.append("M7 _psym_get() has no TF-selector clamp (apply Edit 2, then re-run)")
    except ValueError as e:
        errors.append(f"M7 funnel locate fail: {e}")
    except Exception as e:
        errors.append(f"M7 scan fail: {e}")
    by_func = {}
    for s in sites:
        by_func.setdefault(f"{s['file']}:{s['func']}", 0)
        by_func[f"{s['file']}:{s['func']}"] += 1
    ok = not errors
    print(json.dumps({"ok": ok, "mode": "scan-sources",
                      "n_strip_key_sites": len(sites),
                      "n_prev_key_sites": len(prev_sites),
                      "sites_by_func": dict(sorted(by_func.items(), key=lambda kv: -kv[1])[:40]),
                      "errors": errors, "warnings": warnings}, indent=1))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side")
    ap.add_argument("--overrides-json")
    ap.add_argument("--reasons", nargs="*", default=[])
    ap.add_argument("--scan-sources", action="store_true")
    ap.add_argument("--progress-json", default=None)
    args = ap.parse_args()
    if args.scan_sources:
        return scan_sources()
    if not args.sym_side or not args.overrides_json:
        print(json.dumps({"ok": False, "errors": ["--sym-side and --overrides-json required (or --scan-sources)"], "warnings": []}))
        return 1
    errors, warnings = [], []

    try:
        ov = json.loads(Path(args.overrides_json).read_text())
        if not isinstance(ov, dict):
            raise ValueError("top level must be an object")
    except Exception as e:
        print(json.dumps({"ok": False, "errors": [f"overrides load: {e}"], "warnings": []}))
        return 1

    mask, err = _load_ast_set(ROOT / "ez_manage.py", "_NON_VEC_KNOBS_EZ")
    if mask is None:
        errors.append(f"M1 unavailable: {err}")
    else:
        hit = sorted(set(ov) & set(mask))
        if hit:
            errors.append(f"M1 MASTER2-masked keys in set (live forces OFF): {hit}")

    toks, err = _load_ast_tuple(ROOT / "vec_paths" / "vec_parity_gate.py",
                                ("VEC_ENTRY_TOKENS", "VEC_EXIT_TOKENS", "VEC_EXIT_TOKENS_STOCKS"))
    if toks is None:
        errors.append(f"M2 unavailable: {err}")
    else:
        for r in args.reasons:
            ru = str(r).upper()
            if not any(t in ru for t in toks):
                errors.append(f"M2 reason not vec-achievable (PARITY LOCK would block): {r}")
        if not args.reasons:
            warnings.append("M2 no --reasons given: route check skipped (pass the set's emit reasons)")

    low = []
    for k, v in ov.items():
        ku = str(k).upper()
        if "_3M" in ku or "_5M" in ku:
            if str(v).strip().lower() not in ("0", "false", "off", "no", "", "none"):
                low.append(f"{k}={v}")
        if isinstance(v, str) and v.strip().lower() in ("3m", "5m"):
            low.append(f"{k}={v}")
        if isinstance(v, (list, tuple)) and any(str(x).strip().lower() in ("3m", "5m") for x in v):
            low.append(f"{k}={v}")
    if low:
        errors.append(f"M3 low-TF refs (vec clamps, live does not): {sorted(low)}")

    try:
        sweep = (json.loads((ROOT / "data" / "sweep_cat_overrides.json").read_text()).get("overrides") or {})
    except Exception:
        sweep = {}
    for k, v in sweep.items():
        if k not in ov:
            errors.append(f"M4 sweep-only {k}={v} assumed by vec proof, live runs live value (set does not pin it)")
        elif ov[k] != v:
            warnings.append(f"M4 {k}: set pins {ov[k]!r}, vec sweep ran {v!r} underneath (proof ran pinned value — ok if sheet evals used the pin)")

    try:
        meta = json.loads((ROOT / "data" / "per_sym_settings.json").read_text()).get("_meta", {})
        conflicts = meta.get("conflicts_quick_vs_live", {})
    except Exception:
        conflicts = {}
    sym, _, side = str(args.sym_side).upper().rpartition("_")
    for cat, keys in (conflicts or {}).items():
        if side and side not in str(cat).upper():
            continue
        hit = sorted(set(ov) & set(keys or []))
        if hit:
            warnings.append(f"M5 {cat} quick/live conflicts intersect set: {hit}")

    try:
        deps = json.loads((ROOT / "data" / "switch_dependencies.json").read_text())
        masters = deps.get("masters", {})
    except Exception:
        masters = None
    try:
        _csd = json.loads((ROOT / "data" / "per_sym_settings.json").read_text())
    except Exception:
        _csd = {}
    def _live_default_on(master, sym_side):
        s = str(sym_side or "").upper()
        _side = "LONG" if s.endswith("_LONG") else "SHORT"
        _base = s.rsplit("_", 1)[0]
        _crypto = _base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
        _cat = ("CRYPTO" if _crypto else "STOCKS") + "_" + _side
        _dv = (_csd.get(_cat) or {}).get(master, None)
        if _dv is None:
            return None
        if isinstance(_dv, bool):
            return _dv
        if isinstance(_dv, (int, float)):
            return bool(_dv)
        return str(_dv).strip().upper() not in ("", "OFF", "FALSE", "NONE", "0")
    if masters is None:
        warnings.append("M6 unavailable: data/switch_dependencies.json unreadable")
    else:
        missing = {}
        for k, v in ov.items():
            if str(v).strip().upper() == "OFF":
                continue
            for m in masters.get(k) or []:
                # Vec _expand_dependencies forces masters ON only when NOT pinned
                # in the set (m not in ov). A set-pinned master (even OFF) is
                # honored identically by vec and live -> no divergence either way.
                if m in ov:
                    continue
                if _live_default_on(m, args.sym_side) is not True:
                    missing.setdefault(k, []).append(m)
        if missing:
            errors.append(f"M6 sub-knobs without vec-forced masters (live would miss them): {missing}")

    if args.progress_json:
        try:
            _pj = json.loads(Path(args.progress_json).read_text())
            _nid = _pj.get("npz_id") or {}
            _man = json.loads((ROOT / "data" / "npz_manifest_s1.json").read_text()).get("files") or {}
            _sym, _, _ = str(args.sym_side).upper().rpartition("_")
            _cur = _man.get(_sym) or _man.get(_sym.replace("USDT", "").replace("USDC", "")) or {}
            if not _nid:
                warnings.append("M7 no npz_id stamp in progress JSON (unstamped proof; re-run stamps it)")
            elif not _cur:
                warnings.append(f"M7 {args.sym_side} not in S1 NPZ manifest (cannot judge freshness)")
            elif str(_nid.get("mtime_ns")) != str(_cur.get("mtime_ns")) or str(_nid.get("size")) != str(_cur.get("size")):
                errors.append(f"M7 STALE NPZ: proof measured on {_nid.get('md5', '?')[:12]} mtime={_nid.get('mtime_ns')} size={_nid.get('size')}, S1 current mtime={_cur.get('mtime_ns')} size={_cur.get('size')} — re-validate, do not promote")
        except Exception as _m7e:
            warnings.append(f"M7 unavailable: {_m7e}")
    else:
        warnings.append("M7 skipped (no --progress-json: NPZ freshness unchecked)")

    ok = not errors
    print(json.dumps({"ok": ok, "sym_side": args.sym_side, "n_overrides": len(ov),
                      "errors": errors, "warnings": warnings}, indent=1))
    return 0 if ok and not warnings else (1 if errors else 2)


if __name__ == "__main__":
    sys.exit(main())
