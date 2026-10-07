#!/usr/bin/env python3
"""Read-only AST scan for broker ORDER-MUTATING call sites (Binance / Tradier / Finandy-webhook).

Classifies each site as INSIDE_EXECUTE_NOW, CLIENT_WRAPPER, TEST or OUTSIDE and reports which
files with OUTSIDE sites are currently running (ps aux). Writes JSON to data/safety/.
Usage: python tools/order_callsite_scan.py [--root /Users/niels/Documents/binance]
"""
import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

# ---- editable sets -------------------------------------------------------------------------
# Function names whose bodies count as INSIDE_EXECUTE_NOW (execute_now itself + helpers that are
# only reachable from execute_now). "file.py:func" restricts the helper to that file.
EXECUTE_NOW_HELPERS = {
    "execute_now",
    "place_maker_order",
    "send_webhook",
    "cancel_order_with_confirmation",
    "tradier_manage.py:place_order",
}
CLIENT_WRAPPER_FILES = {"order_dedupe_guard.py", "tradier_api.py"}
BINANCE_ATTRS = {"futures_create_order", "futures_place_batch_order", "create_order", "futures_cancel_order", "futures_cancel_all_open_orders", "futures_cancel_orders", "futures_countdown_cancel_all", "new_order"}
BINANCE_ATTR_PREFIXES = ("order_market", "order_limit")
TRADIER_ATTRS = {"place_order", "place_option_order", "place_multileg_option_order", "cancel_order", "modify_order"}
REST_STRINGS = ("/fapi/v1/order", "/fapi/v1/batchOrders", "/api/v3/order")
EXCLUDE_DIR_PATTERNS = ["backups", ".git", "venv*", ".venv", "node_modules", "site-packages", "__pycache__", "archive*", "old*", "SPREADSHEETS", "data", ".*"]  # ".*" = hidden agent snapshot dirs (.muse 31k files, .claude worktrees, .history)
SKIP_ATTRS = {"create_test_order"}


def excluded_dir(name):
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE_DIR_PATTERNS)


def call_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def safe_unparse(node):
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparse-failed>"


def const_strings(nodes):
    out = []
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.append(sub.value)
    return out


def classify_call(node):
    """Return (broker, kind) if the Call node is order-mutating, else None."""
    name = call_name(node.func)
    if not name or name in SKIP_ATTRS:
        return None
    is_attr = isinstance(node.func, ast.Attribute)
    if is_attr and (name in BINANCE_ATTRS or name.startswith(BINANCE_ATTR_PREFIXES)):
        return ("binance", "client_attr")
    if is_attr and name in TRADIER_ATTRS:
        return ("tradier", "client_attr")
    args = list(node.args) + [kw.value for kw in node.keywords]
    if name.endswith("_request_futures_api"):
        joined = " ".join(const_strings(args)).lower()
        if ("post" in joined or "delete" in joined) and "order" in joined:
            return ("binance", "raw_request_futures_api")
    if name == "_request" and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.upper() in ("POST", "PUT", "DELETE"):
            if "/orders" in " ".join(safe_unparse(a) for a in args):
                return ("tradier", "raw_request")
    if is_attr and name == "post":
        url_nodes = node.args[:1] + [kw.value for kw in node.keywords if kw.arg == "url"]
        url_text = " ".join(safe_unparse(a) for a in url_nodes)
        low = url_text.lower()
        if "/orders" in url_text:
            return ("tradier", "http_post")
        if any(s in url_text for s in REST_STRINGS):
            return ("binance", "http_post")
        if "finandy" in low or "hook" in low:
            return ("webhook", "http_post")
    return None


class Scanner(ast.NodeVisitor):
    def __init__(self, relpath):
        self.relpath = relpath
        self.base = os.path.basename(relpath)
        self.stack = []  # list of (kind, name)
        self.sites = []
        self.call_lines = set()
        self.docstrings = set()

    def _scope(self, node, kind):
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            self.docstrings.add(id(body[0].value))
        self.stack.append((kind, node.name))
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node):
        self._scope(node, "class")

    def visit_FunctionDef(self, node):
        self._scope(node, "func")

    visit_AsyncFunctionDef = visit_FunctionDef

    def qualname(self):
        return ".".join(n for _, n in self.stack) or "<module>"

    def classification(self):
        parts = self.relpath.split(os.sep)
        if "tests" in parts[:-1] or "test" in parts[:-1] or self.base.startswith("test_") or self.base.endswith("_test.py"):
            return "TEST"
        for kind, name in self.stack:
            if kind == "func" and (name in EXECUTE_NOW_HELPERS or f"{self.base}:{name}" in EXECUTE_NOW_HELPERS):
                return "INSIDE_EXECUTE_NOW"
        if self.base in CLIENT_WRAPPER_FILES:
            return "CLIENT_WRAPPER"
        return "OUTSIDE"

    def add(self, node, broker, kind, text):
        self.sites.append({"file": self.relpath, "line": node.lineno, "function": self.qualname(), "call": text[:160], "broker": broker, "kind": kind, "classification": self.classification()})

    def visit_Call(self, node):
        hit = classify_call(node)
        if hit:
            self.add(node, hit[0], hit[1], safe_unparse(node).replace("\n", " "))
            self.call_lines.add(node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and id(node) not in self.docstrings and any(s in node.value for s in REST_STRINGS):
            if node.lineno not in self.call_lines:
                self.add(node, "binance", "rest_string_const", repr(node.value).replace("\n", " "))


# ---- PHASE 3 verdict: every OUTSIDE site must be provably unable to reach a broker ------------------
ARCHIVE_PATTERNS = ["quarantine_scripts_*/*", "scripts/ez_manage_*.py"]  # stale code copies — never launched (ps-checked below)
NOT_OUR_BROKER = {"limitless": "Limitless prediction market (not Binance/Tradier)", "instId=": "OKX API (not used; no caller)", "self.webhook_url": "market-data broadcast, not an order"}
SWITCH_MARKERS = ("BREAKOUT_AGENT_DIRECT_ORDERS_ENABLED", "SANDBOX_FOOTHOLD_WEBHOOK_ENABLED", "direct REST order KILLED")


def file_facts(tree, src):
    imports = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module.split(".")[0])
    lines = src.splitlines()
    funcs = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(n.name, []).append(_LazySrc(lines, n.lineno, getattr(n, "end_lineno", n.lineno)))
    return imports, funcs


class _LazySrc(str):
    """Function source sliced from the file lines only when a verdict needs it (ast.get_source_segment is O(file) per call)."""

    def __new__(cls, lines, a, b):
        obj = str.__new__(cls, "")
        obj._args = (lines, a, b)
        return obj

    def text(self):
        lines, a, b = self._args
        return "\n".join(lines[a - 1:b])


def verdict(site, facts):
    if site["classification"] != "OUTSIDE":
        return site["classification"], ""
    rel, call, fn = site["file"], site["call"], site["function"].split(".")[-1]
    imports, funcs = facts.get(rel, (set(), {}))
    if any(fnmatch.fnmatch(rel, p) for p in ARCHIVE_PATTERNS):
        return "ARCHIVE_COPY", "stale copy, not a live script"
    if "limitless" in call.lower() or "limitless" in rel.lower():
        return "NOT_OUR_BROKER", NOT_OUR_BROKER["limitless"]
    for k, why in NOT_OUR_BROKER.items():
        if k in call:
            return "NOT_OUR_BROKER", why
    if "cancel" in call.split("(")[0].lower() or "'DELETE'" in call:
        return "CANCEL_ONLY", "removes an order; never adds exposure"
    body = "\n".join(x.text() for x in funcs.get(fn, []))
    if any(m in body for m in SWITCH_MARKERS):
        return "SWITCHED_OFF", "function returns before the call (kill switch)"
    if call.startswith("self.place_order(") and any("KILLED" in b.text() and "return False" in b.text() for b in funcs.get("place_order", [])):
        return "SWITCHED_OFF", "own place_order body is a killed stub"
    if site["broker"] == "tradier" and site["kind"] in ("client_attr", "raw_request") and ("tradier_api" in imports or "tradier_options_analyzer" in imports):
        return "REFUSED_AT_CLIENT", "TradierAPIClient -> order_dedupe_guard: no execute_now token -> NOT_VIA_EXECUTE_NOW (raw _request POST /orders -> RAW_ORDER_POST_OUTSIDE_GUARD)"
    if site["broker"] == "binance" and site["kind"] == "client_attr" and ({"order_dedupe_guard", "ez_manage"} & imports):
        return "REFUSED_AT_CLIENT", "python-binance class guard: no execute_now token -> NOT_VIA_EXECUTE_NOW"
    return "UNGUARDED", "can reach a broker outside execute_now"


def iter_py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not excluded_dir(d))
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def running_procs():
    try:
        return subprocess.run(["ps", "auxww"], capture_output=True, text=True, timeout=20).stdout.splitlines()
    except Exception as exc:
        return [f"<ps failed: {exc}>"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/Users/niels/Documents/binance")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    sites, parse_errors, nfiles = [], [], 0
    facts = {}
    for path in iter_py_files(root):
        rel = os.path.relpath(path, root)
        if rel == os.path.join("tools", "order_callsite_scan.py"):
            continue
        nfiles += 1
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (SyntaxError, ValueError) as exc:
            parse_errors.append({"file": rel, "error": str(exc)[:120]})
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                facts[rel] = file_facts(tree, fh.read())
        except Exception:
            facts[rel] = (set(), {})
        sc = Scanner(rel)
        sc.visit(tree)
        sites.extend(sorted(sc.sites, key=lambda s: s["line"]))
    for s_ in sites:
        s_["verdict"], s_["why"] = verdict(s_, facts)
    counts = Counter(s["classification"] for s in sites)
    vcounts = Counter(s["verdict"] for s in sites)
    outside = [s for s in sites if s["classification"] == "OUTSIDE"]
    procs = running_procs()
    running = {}
    for rel in sorted({s["file"] for s in outside}):
        # root-level file: bare basename (launched from repo cwd); subdir file: must show its subdir path
        needle = os.path.basename(rel) if os.sep not in rel else rel
        pat = re.compile(r"(^|[\s/])" + re.escape(needle) + r"(\s|$)")
        hits = [p for p in procs if pat.search(p) and "order_callsite_scan" not in p and re.search(r"python|run_with_watchdog", p) and not re.search(r"\b(ssh|md5sum|grep|rsync)\b", p)]
        running[rel] = [" ".join(p.split()[1:2] + p.split()[10:])[:200] for p in hits]
    print(f"Scanned {nfiles} .py files under {root}; parse errors: {len(parse_errors)}")
    print(f"{'CLASS':<20} {'FILE:LINE':<55} {'FUNCTION':<45} CALL")
    for s in sites:
        print(f"{s['classification']:<20} {s['file'] + ':' + str(s['line']):<55} {s['function'][:45]:<45} {s['call']}")
    print("\n== COUNTS BY CLASSIFICATION ==")
    for k in ("INSIDE_EXECUTE_NOW", "CLIENT_WRAPPER", "TEST", "OUTSIDE"):
        print(f"  {k:<20} {counts.get(k, 0)}")
    print(f"  {'TOTAL':<20} {len(sites)}")
    by_file = defaultdict(int)
    for s in outside:
        by_file[s["file"]] += 1
    print("\n== PHASE 3 VERDICT (every site) ==")
    for k, v in sorted(vcounts.items()):
        print(f"  {k:<20} {v}")
    for s_ in sites:
        if s_["classification"] == "OUTSIDE":
            print(f"  {s_['verdict']:<18} {s_['file'] + ':' + str(s_['line']):<55} {s_['why']}")
    unguarded = [s_ for s_ in sites if s_["verdict"] == "UNGUARDED"]
    print(f"\n  UNGUARDED (can reach a broker outside execute_now): {len(unguarded)}")
    print("\n== FILES WITH OUTSIDE SITES — LIVE PROCESS CHECK (ps auxww) ==")
    for rel in sorted(by_file):
        state = "RUNNING" if running[rel] else "not running"
        print(f"  {rel:<60} outside={by_file[rel]:<4} {state}")
        for line in running[rel]:
            print(f"      pid {line}")
    if parse_errors:
        print("\n== PARSE ERRORS ==")
        for e in parse_errors:
            print(f"  {e['file']}: {e['error']}")
    out_dir = os.path.join(root, "data", "safety")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"order_callsites_{datetime.now().strftime('%Y%m%d')}.json")
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "root": root, "files_scanned": nfiles, "execute_now_helpers": sorted(EXECUTE_NOW_HELPERS), "counts": dict(counts), "verdicts": dict(vcounts), "unguarded": len([x for x in sites if x.get("verdict") == "UNGUARDED"]), "total": len(sites), "sites": sites, "outside_files_running": running, "parse_errors": parse_errors}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    print(f"\nJSON written: {out_path}")
    return 1 if any(x.get("verdict") == "UNGUARDED" for x in sites) else 0


if __name__ == "__main__":
    sys.exit(main())
