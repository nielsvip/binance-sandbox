"""switch_types — THE one validator for switch values (USER 2026-10-13: "'0.0' on a true/false switch — that is TERRIBLE ...
just make sure it can never happen again"). BIBLE §96.

coerce(field, value, venue=None) -> (ok, value, reason)

Field kind comes from the LIVE venue config default (config.Config for crypto, config_tradier.TradierConfig for stocks),
falling back to the other venue and then v12_quick_engine.QuickConfig (annotation + default). QuickConfig annotates most
fields `float` (lax), so it never overrides a live bool/int/str type. Kinds:
  bool    True/False; 0/1/0.0/1.0 numbers and 'true'/'false'/'1'/'0'/'1.0'/'0.0'/'yes'/'no' strings coerce; anything else
          ('2.0', '0.5', 'OFF', 30, '0.0_ALT') is REFUSED (ambiguous — never guessed).
  bool01  a bool on one venue encoded as a 0.0/1.0 float on the other (HTF_GATE_D_MANDATORY): same inputs as bool, output is
          the venue-native type (bool or 0.0/1.0); 0.5/2.0 refused.
  int     ints; integral floats / numeric strings coerce; non-integral refused (rounding would invent a value) unless
          allow_quick_float=True and QuickConfig declares the field float (the registrar's documented float-kept path).
  float   numbers / numeric strings; bools refused (True on a float threshold is a column swap).
  tf      timeframe word(s): every token of the value (split on , + _ space) in TF_TOKENS; False -> 'OFF'; True/numbers refused.
  str     enum words: str only; bools/numbers refused; numeric or bool-word strings refused unless the default itself is one.
  dict    FILTER_EXEMPTIONS-style {path: [filters]} (switch_parity.coerce_structured rules) / JSON object string.
  list    list/tuple / JSON array string.
  unknown no surface declares the field: accepted unchanged with reason 'untyped' (strict_unknown=True refuses).
A raw string containing '=' (K=V display pollution) is refused for every scalar kind.

CLI: python3 tools/switch_types.py --scan [DIR ...] [--alert-file data/alerts/SWITCH_TYPE_ALERT.json] [--json OUT]
     exits 1 (and writes the alert file) when any template row/header carries a value the validator refuses or that is
     not canonically typed (e.g. '0.0' string on a bool field, False on a TF field, '25' string on an int field).
"""
import argparse
import ast
import dataclasses
import datetime
import json
import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SWITCH_TYPES_ROOT") or Path(__file__).resolve().parents[1])
TF_TOKENS = frozenset({"OFF", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "D", "1d", "3d", "W", "1w", "M"})
TF_EXTRA_WORDS = frozenset({"BOTH", "ALL", "ANY", "EITHER"})  # documented TF-field options (e.g. WT_TOP_ENTRY_HTF_CONFIRM_TF "BOTH" = 4h OR D)
_TF_UPPER = {t.upper(): t for t in TF_TOKENS}
_TF_SPLIT = re.compile(r"[,+_\s]+")
_BOOL_TRUE = ("true", "1", "1.0", "yes")
_BOOL_FALSE = ("false", "0", "0.0", "no")
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
TEMPLATE_FILES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
_CACHE = {}


def _norm_venue(venue):
    v = str(venue or "").upper()
    if v.startswith("CRYPTO") or v in ("EZ", "BINANCE"):
        return "CRYPTO"
    if v.startswith("STOCK") or v in ("TRADIER",):
        return "STOCKS"
    return None


def _dc_defaults(cls):
    out = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            out[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:
            try:
                out[f.name] = f.default_factory()
            except Exception:
                out[f.name] = None
    return out


def _ast_defaults(path, cls):
    out, ann = {}, {}
    try:
        tree = ast.parse(Path(path).read_text())
    except Exception:
        return out, ann
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == cls:
            for s in n.body:
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                    ann[s.target.id] = ast.unparse(s.annotation) if hasattr(ast, "unparse") else ""
                    try:
                        out[s.target.id] = ast.literal_eval(s.value) if s.value is not None else None
                    except Exception:
                        out[s.target.id] = _ast_factory(s.value)
    return out, ann


def _ast_factory(node):
    """field(default=<lit>) / field(default_factory=dict|list|lambda: <lit>) -> value; anything else None."""
    try:
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "field":
            for kw in node.keywords:
                if kw.arg == "default":
                    return ast.literal_eval(kw.value)
                if kw.arg == "default_factory":
                    v = kw.value
                    if isinstance(v, ast.Name) and v.id in ("dict", "list", "set", "tuple"):
                        return {"dict": {}, "list": [], "set": set(), "tuple": ()}[v.id]
                    if isinstance(v, ast.Lambda):
                        return ast.literal_eval(v.body)
    except Exception:
        return None
    return None


def _surfaces(root=None):
    """{'CRYPTO': {field: default}, 'STOCKS': {...}, 'QUICK': {...}, 'QUICK_ANN': {field: 'float'|...}} (cached per root)."""
    root = Path(root or ROOT)
    key = str(root)
    if key in _CACHE:
        return _CACHE[key]
    surf = {"CRYPTO": {}, "STOCKS": {}, "QUICK": {}, "QUICK_ANN": {}}
    specs = (("CRYPTO", "config", "Config", "config.py"), ("STOCKS", "config_tradier", "TradierConfig", "config_tradier.py"), ("QUICK", "v12_quick_engine", "QuickConfig", "v12_quick_engine.py"))
    added = str(root) not in sys.path
    if added:
        sys.path.insert(0, str(root))
    try:
        for name, mod, cls, fn in specs:
            got = False
            if os.environ.get("SWITCH_TYPES_AST_ONLY") != "1":
                try:
                    m = __import__(mod)
                    if str(Path(getattr(m, "__file__", "")).resolve().parent) == str(root.resolve()):
                        c = getattr(m, cls)
                        surf[name] = _dc_defaults(c)
                        if name == "QUICK":
                            surf["QUICK_ANN"] = {f.name: (f.type.__name__ if isinstance(f.type, type) else str(f.type)) for f in dataclasses.fields(c)}
                        got = True
                except Exception:
                    got = False
            if not got:
                d, ann = _ast_defaults(root / fn, cls)
                surf[name] = d
                if name == "QUICK":
                    surf["QUICK_ANN"] = ann
    finally:
        if added:
            try:
                sys.path.remove(str(root))
            except ValueError:
                pass
    _CACHE[key] = surf
    return surf


def _is_tf_word(s, extras=False):
    if extras and str(s).strip().upper() in TF_EXTRA_WORDS:
        return True
    toks = [t for t in _TF_SPLIT.split(str(s).strip()) if t]
    return bool(toks) and all(t in TF_TOKENS or t.upper() in _TF_UPPER for t in toks)


def _numeric_str(s):
    try:
        float(str(s).strip())
        return True
    except Exception:
        return False


def _tf_named(field):
    return bool(re.search(r"(^|_)H?TFS?($|_)", str(field or "")))


def _kind_of_default(d, field=""):
    if isinstance(d, bool):
        return "bool"
    if isinstance(d, int):
        return "int"
    if isinstance(d, float):
        return "float"
    if isinstance(d, str):
        s = d.strip()
        if s and _is_tf_word(s) and (s.upper() != "OFF" or _tf_named(field)):
            return "tf"
        return "str"
    if isinstance(d, dict):
        return "dict"
    if isinstance(d, (list, tuple, set, frozenset)):
        return "list"
    return "unknown"


def field_spec(field, venue=None, root=None):
    """{'kind', 'default', 'venue', 'quick_float'} for one field (venue CRYPTO/STOCKS/cat_side or None)."""
    s = _surfaces(root)
    v = _norm_venue(venue)
    order = [v, "STOCKS" if v == "CRYPTO" else "CRYPTO"] if v else ["CRYPTO", "STOCKS"]
    src, d = None, None
    for o in order:
        if field in s[o] and s[o][field] is not None:
            src, d = o, s[o][field]
            break
    qann = str(s["QUICK_ANN"].get(field) or "")
    qf = qann in ("float", "<class 'float'>")
    if src is None:
        if field in s["QUICK"] and s["QUICK"][field] is not None:
            qd = s["QUICK"][field]
            k = "float" if (qf and isinstance(qd, (int, float)) and not isinstance(qd, bool)) else _kind_of_default(qd, field)
            return {"kind": k, "default": qd, "venue": "QUICK", "quick_float": qf}
        if field in s["CRYPTO"] or field in s["STOCKS"] or field in s["QUICK"]:
            return {"kind": "unknown", "default": None, "venue": None, "quick_float": qf}
        return {"kind": "unknown", "default": None, "venue": None, "quick_float": qf, "absent": True}
    k = _kind_of_default(d, field)
    other = s["STOCKS" if src == "CRYPTO" else "CRYPTO"].get(field)
    qd = s["QUICK"].get(field)
    if k in ("dict", "list") and qd is not None and _kind_of_default(qd, field) in ("bool", "int", "float"):
        return {"kind": "grey", "default": d, "venue": src, "quick_float": qf, "quick_default": qd}
    if k == "float" and isinstance(other, bool) and float(d) in (0.0, 1.0):
        k = "bool01"
    elif k == "bool" and isinstance(other, float) and not isinstance(other, bool) and float(other) in (0.0, 1.0):
        k = "bool01"
    return {"kind": k, "default": d, "venue": src, "quick_float": qf}


def _as_bool(value):
    if isinstance(value, bool):
        return True, value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return False, None
        if float(value) == 1.0:
            return True, True
        if float(value) == 0.0:
            return True, False
        return False, None
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _BOOL_TRUE:
            return True, True
        if s in _BOOL_FALSE:
            return True, False
    return False, None


def coerce(field, value, venue=None, *, allow_quick_float=False, strict_unknown=False, root=None):
    """(ok, coerced_value, reason). ok=False => the value must NOT be written/promoted/evaluated; reason says why."""
    field = str(field or "").strip()
    spec = field_spec(field, venue, root)
    k = spec["kind"]
    if value is None:
        return (False, None, f"{field}: empty value")
    if isinstance(value, str) and "=" in value and k not in ("dict", "list"):
        _lh, _rh = value.split("=", 1)
        if _lh.strip() and _lh.strip() == _rh.strip():
            ok, cv, why = coerce(field, _rh.strip(), venue, allow_quick_float=allow_quick_float, strict_unknown=strict_unknown, root=root)
            return (ok, cv, (why + "; " if why else "") + f"'X=X' dup typo {value!r} collapsed")
        return (False, value, f"{field}: {value!r} contains '=' (K=V display text, never a value)")
    if k == "unknown":
        if strict_unknown:
            return (False, value, f"{field}: not a field of config.py / config_tradier.py / QuickConfig")
        return (True, value, "untyped")
    if k == "grey":
        qd = spec["quick_default"]
        qk = _kind_of_default(qd, field)
        if qk == "bool":
            ok, cv = _as_bool(value)
        elif isinstance(value, bool) or not (isinstance(value, (int, float)) or (isinstance(value, str) and _numeric_str(value))):
            ok, cv = False, None
        else:
            ok, cv = True, float(value) if qk == "float" else value
        if ok and qk == "int":
            ok = float(cv).is_integer()
            cv = int(float(cv)) if ok else cv
        if ok and float(cv) == float(qd):
            return (True, cv, "grey field: QuickConfig default (no-op)")
        return (False, value, f"{field}: grey field (live {type(spec['default']).__name__}, vec scalar {qd!r}) — only the QuickConfig default {qd!r} is a valid row value, got {value!r}")
    if k in ("bool", "bool01"):
        ok, b = _as_bool(value)
        if not ok:
            return (False, value, f"{field}: {type(value).__name__} {value!r} is not a bool (expects True/False; 0/1 only)")
        if k == "bool01" and isinstance(spec["default"], float) and not isinstance(spec["default"], bool):
            return (True, 1.0 if b else 0.0, "")
        return (True, b, "")
    if k == "int":
        if isinstance(value, bool):
            return (False, value, f"{field}: bool {value!r} refused for an int field")
        if isinstance(value, int):
            return (True, value, "")
        fv = None
        if isinstance(value, float):
            fv = value
        elif isinstance(value, str) and _numeric_str(value):
            fv = float(value.strip())
        if fv is None or math.isnan(fv) or math.isinf(fv):
            return (False, value, f"{field}: {type(value).__name__} {value!r} is not an int")
        if fv.is_integer():
            return (True, int(fv), "")
        if allow_quick_float and spec["quick_float"]:
            return (True, fv, "float-kept (QuickConfig declares float)")
        return (False, value, f"{field}: {value!r} is not integral (int field; rounding would invent a value)")
    if k == "float":
        if isinstance(value, bool):
            return (False, value, f"{field}: bool {value!r} refused for a float field")
        if isinstance(value, (int, float)):
            if isinstance(value, float) and math.isnan(value):
                return (False, value, f"{field}: NaN")
            return (True, float(value), "")
        if isinstance(value, str) and _numeric_str(value) and value.strip().lower() not in ("nan",):
            return (True, float(value.strip()), "")
        return (False, value, f"{field}: {type(value).__name__} {value!r} is not a number")
    if k == "tf":
        if value is False:
            return (True, "OFF", "False on a TF field = OFF")
        if not isinstance(value, str):
            return (False, value, f"{field}: {type(value).__name__} {value!r} is not a timeframe (TF field)")
        s = value.strip()
        if not _is_tf_word(s, extras=True):
            return (False, value, f"{field}: {value!r} is not a timeframe word ({'/'.join(sorted(TF_TOKENS))})")
        return (True, s, "")
    if k == "str":
        if not isinstance(value, str):
            return (False, value, f"{field}: {type(value).__name__} {value!r} refused for a text/enum field")
        d = str(spec["default"] if spec["default"] is not None else "")
        s = value.strip()
        if _numeric_str(s) and not _numeric_str(d):
            return (False, value, f"{field}: numeric {value!r} refused for a text/enum field (default {d!r})")
        if s.lower() in ("true", "false") and d.strip().lower() not in ("true", "false"):
            return (False, value, f"{field}: bool-word {value!r} refused for a text/enum field (default {d!r})")
        return (True, value, "")
    if k == "dict":
        v = value
        if isinstance(v, str):
            # USER 2026-10-17: json.loads alone refused every Python-repr dict that reaches the gate as a
            # string — "{'REENTRY': ['EMA_9_21_FILTER_ENABLED']}" (single quotes) is not JSON. Because
            # switch_parity refuses the WHOLE sym_side on one bad value ("type-gate refused ALL (1 bad)"), a
            # sym_side carrying the string form can register NO change at all. Measured on the 2026-10-17
            # 02:51 refresh: 27 of 33 refusals were this quoting difference. Not claimed beyond that — the
            # refused sym_sides checked (ONDOUSDT_LONG, ZROUSDT_LONG, 1000FLOKIUSDT_SHORT) were already
            # live-equal to their boards, so what was blocked there was a repeated no-op.
            # ast.literal_eval parses literals ONLY (no names, calls or attribute access, so nothing executes),
            # and anything that is still not a literal dict keeps refusing exactly as before: fail-closed.
            try:
                v = json.loads(v) if v.strip() else {}
            except Exception:
                try:
                    v = ast.literal_eval(v) if v.strip() else {}
                except Exception:
                    return (False, value, f"{field}: unparseable dict string {value!r}"[:200])
        if not isinstance(v, dict):
            return (False, value, f"{field}: {type(value).__name__} {value!r} is not a dict")
        if field == "FILTER_EXEMPTIONS":
            out = {}
            for path, fl in v.items():
                if isinstance(fl, str):
                    fl = [x.strip() for x in fl.split(",")]
                if not isinstance(fl, (list, tuple, set)):
                    return (False, value, f"{field}[{path!r}]: filters must be a list")
                lst = []
                for f in fl:
                    f = str(f).strip()
                    if f and f not in lst:
                        lst.append(f)
                if str(path).strip() and lst:
                    out[str(path).strip()] = lst
            return (True, out, "")
        return (True, v, "")
    if k == "list":
        v = value
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                return (False, value, f"{field}: unparseable list string {value!r}"[:200])
        if not isinstance(v, (list, tuple, set, frozenset)):
            return (False, value, f"{field}: {type(value).__name__} {value!r} is not a list")
        return (True, list(v), "")
    return (True, value, "")


def canonical(field, value, venue=None, root=None):
    """True when the raw value is already stored in its field's native type (what a template cell should hold)."""
    ok, cv, _ = coerce(field, value, venue, root=root)
    if not ok:
        return False
    k = field_spec(field, venue, root)["kind"]
    if k in ("bool", "bool01"):
        return isinstance(value, bool) or (k == "bool01" and isinstance(value, (int, float)) and value in (0, 1))  # xlsx stores 1.0 as 1
    if k == "grey":
        return isinstance(value, bool) if isinstance(cv, bool) else (isinstance(value, (int, float)) and not isinstance(value, bool))
    if k == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if k == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if k == "tf":
        return isinstance(value, str) and value == cv
    if k == "str":
        return isinstance(value, str)
    return True


def to_cell(field, value, venue=None, root=None):
    """(ok, cell_value, reason): the typed value a TEMPLATE cell should hold. Floats that are written as integers ('25' on a
    float field) stay ints (same text, no key churn); everything else is the coerced native value."""
    ok, cv, why = coerce(field, value, venue, root=root)
    if not ok:
        return (False, None, why)
    k = field_spec(field, venue, root)["kind"]
    if k in ("float", "grey") and isinstance(cv, float) and not isinstance(cv, bool):
        raw = value.strip() if isinstance(value, str) else value
        if isinstance(raw, int) and not isinstance(raw, bool):
            return (True, raw, why)
        if isinstance(raw, str) and re.fullmatch(r"-?\d+", raw):
            return (True, int(raw), why)
    return (True, cv, why)


def same_value(a, b):
    """Typed equality used for duplicate detection: True == 1.0 for bool01, 'D' == 'D', 2 == 2.0; '2' (text) != 2."""
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if isinstance(a, (bool, int, float)) and isinstance(b, (bool, int, float)):
        return abs(float(a) - float(b)) < 1e-12
    return a == b


def check_overrides(overrides, venue=None, **kw):
    """({field: coerced}, {field: reason}) — the fail-closed helper writers/registrars call on a whole override set."""
    good, bad = {}, {}
    for k, v in dict(overrides or {}).items():
        ok, cv, why = coerce(k, v, venue, **kw)
        if ok:
            good[k] = cv
        else:
            bad[k] = why
    return good, bad


def _venue_of_path(path):
    n = Path(path).name.upper()
    return "STOCKS" if "STOCKS" in n else ("CRYPTO" if "CRYPTO" in n else None)


def scan_workbook(path, root=None):
    """Every SWITCH_SHEETS row (A=switch, B=value) + every yellow filter header 'FILTER=opt' whose value is refused or not
    canonically typed. Returns a list of findings dicts (read-only)."""
    import openpyxl
    venue = _venue_of_path(path)
    out = []
    wb = openpyxl.load_workbook(str(path), read_only=True)
    try:
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            hdr = None
            isd = None
            for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if i == 2:
                    hdr = list(row)
                    isd = next((j for j, h in enumerate(hdr) if str(h or "").strip().lower() == "is_default"), None)
                    for j, h in enumerate(hdr):
                        if isinstance(h, str) and "=" in h and j > 1:
                            f, opt = h.split("=", 1)
                            f = f.strip()
                            if field_spec(f, venue, root)["kind"] == "unknown":
                                continue
                            ok, cv, why = coerce(f, opt, venue, root=root)
                            if not ok:
                                out.append({"file": str(path), "tab": tab, "row": 2, "col": j + 1, "kind": "header", "field": f, "value": opt, "bold": None, "ok": False, "coerced": None, "reason": why})
                    continue
                a = row[0] if row else None
                if not isinstance(a, str) or not re.fullmatch(r"[A-Z0-9_]{3,}", a.strip()):
                    continue
                v = row[1] if len(row) > 1 else None
                if v is None:
                    continue
                f = a.strip()
                ok, cv, why = coerce(f, v, venue, root=root)
                bold = bool(isd is not None and isd < len(row) and str(row[isd] or "").strip().upper() == "YES")
                if ok and canonical(f, v, venue, root):
                    continue
                out.append({"file": str(path), "tab": tab, "row": i, "col": 2, "kind": "row", "field": f, "value": v, "bold": bold, "ok": ok, "coerced": cv if ok else None, "reason": why or f"not canonical ({type(v).__name__} for {field_spec(f, venue, root)['kind']})"})
    finally:
        wb.close()
    return out


def template_paths(dirs=None, root=None):
    root = Path(root or ROOT)
    dirs = dirs or [root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM", root / "SPREADSHEETS"]
    return [Path(d) / fn for d in dirs for fn in TEMPLATE_FILES.values() if (Path(d) / fn).exists()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scan", nargs="*", help="template dirs (default: SPREADSHEETS/TEMPLATE_FINAL_NORM + SPREADSHEETS)")
    ap.add_argument("--alert-file", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--coerce", nargs=3, metavar=("FIELD", "VALUE", "VENUE"), help="one-off check (VALUE parsed as JSON when possible)")
    a = ap.parse_args(argv)
    if a.coerce:
        f, raw, ven = a.coerce
        try:
            val = json.loads(raw)
        except Exception:
            val = raw
        ok, cv, why = coerce(f, val, ven)
        print(json.dumps({"field": f, "value": val, "venue": ven, "spec": {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v)) for k, v in field_spec(f, ven).items()}, "ok": ok, "coerced": cv, "reason": why}, default=str))
        return 0 if ok else 1
    paths = template_paths([Path(d) for d in a.scan] if a.scan else None)
    finds = []
    for p in paths:
        finds.extend(scan_workbook(p))
    by_file = {}
    for x in finds:
        d = by_file.setdefault(x["file"], {"refused": 0, "noncanonical": 0, "bold": 0})
        d["refused" if not x["ok"] else "noncanonical"] += 1
        d["bold"] += 1 if x.get("bold") else 0
    rep = {"ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "files": [str(p) for p in paths], "n_findings": len(finds), "by_file": by_file, "findings": finds}
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(rep, indent=1, default=str))
    for f, d in by_file.items():
        print(f"[switch_types] {f}: refused={d['refused']} noncanonical={d['noncanonical']} (bold {d['bold']})")
    print(f"[switch_types] scanned {len(paths)} templates: {len(finds)} mistyped value(s)")
    if a.alert_file:
        ap_ = Path(a.alert_file)
        if finds:
            ap_.parent.mkdir(parents=True, exist_ok=True)
            ap_.write_text(json.dumps({"status": "ALERT", "what": "mistyped switch values in templates (BIBLE §96) — fix via tools/v15_switch_add.py --fix-types", **{k: rep[k] for k in ("ts", "n_findings", "by_file")}, "sample": finds[:50]}, indent=1, default=str))
            print(f"[switch_types] ALERT written -> {ap_}")
        elif ap_.exists():
            ap_.unlink()
    return 1 if finds else 0


if __name__ == "__main__":
    sys.exit(main())
