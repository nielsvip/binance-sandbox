#!/usr/bin/env python3
"""excluded_tokenized_stocks — central deny list for Binance TradFi perps (USER 2026-10-13).

USER: "AAPLUSDT ETC ARE NOT TRADEABLE AT ALL — GET THEM OUT OF EVERY SYSTEM".
Deny file: data/excluded_tokenized_stocks.json (symbols = every fapi contract with contractType TRADIFI_PERPETUAL /
underlyingSubType TradFi: tokenized equities, ETFs, pre-IPO, commodity/FX). Matches CRYPTO symbols only
(AAPLUSDT, AAPLUSDT_LONG, flz:AAPLUSDT) — the Tradier stock ticker AAPL is never denied.

Library:  from excluded_tokenized_stocks import load_denied, is_denied, filter_syms
CLI:
  python tools/excluded_tokenized_stocks.py build [--exchangeinfo F]   # rebuild deny file from Binance exchangeInfo (union, never shrinks)
  python tools/excluded_tokenized_stocks.py enforce [--apply]          # strip denied symbols from symbol lists / queues / universe files
  python tools/excluded_tokenized_stocks.py scan [--root R ...]        # list artefact files/dirs named after denied symbols
  python tools/excluded_tokenized_stocks.py archive --dest D [--root R ...] [--apply]   # MOVE them to D + manifest.json
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DENY_FILE = ROOT / "data" / "excluded_tokenized_stocks.json"
_CACHE = {}
QUOTES = ("USDT", "USDC")


def _deny_path(root=None):
    env = os.environ.get("EXCLUDED_TOKENIZED_STOCKS_FILE")
    if env:
        return pathlib.Path(env)
    return pathlib.Path(root) / "data" / "excluded_tokenized_stocks.json" if root else DENY_FILE


def load_denied(root=None):
    """frozenset of denied crypto symbols (mtime-cached). Missing/corrupt file -> empty set + stderr warning."""
    p = _deny_path(root)
    try:
        m = p.stat().st_mtime
    except OSError:
        print(f"[excluded_tokenized_stocks] WARN deny file missing: {p}", file=sys.stderr)
        return frozenset()
    hit = _CACHE.get(str(p))
    if hit and hit[0] == m:
        return hit[1]
    try:
        s = frozenset(str(x).strip().upper() for x in json.loads(p.read_text()).get("symbols", []) if str(x).strip())
    except Exception as e:
        print(f"[excluded_tokenized_stocks] WARN deny file unreadable {p}: {e}", file=sys.stderr)
        return hit[1] if hit else frozenset()
    _CACHE[str(p)] = (m, s)
    return s


def symbol_of(item):
    """'flz:AAPLUSDT_LONG' / 'AAPLUSDT_SHORT' / 'aaplusdt' -> 'AAPLUSDT'."""
    s = str(item).strip().split(":", 1)[-1].upper()
    return re.sub(r"_(LONG|SHORT)$", "", s)


def is_denied(item, root=None):
    return symbol_of(item) in load_denied(root)


def filter_syms(items, root=None):
    deny = load_denied(root)
    return [x for x in items if symbol_of(x) not in deny]


def _token_re(deny):
    if not deny:
        return None
    alt = "|".join(sorted((re.escape(s) for s in deny), key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])({alt})(?![A-Za-z0-9])", re.IGNORECASE)


def name_hits(name, root=None, _rx={}):
    deny = load_denied(root)
    key = hash(deny)
    if key not in _rx:
        _rx.clear()
        _rx[key] = _token_re(deny)
    rx = _rx[key]
    if rx is None:
        return None
    m = rx.search(name)
    return m.group(1).upper() if m else None


def build(exchangeinfo=None, root=None):
    import urllib.request
    if exchangeinfo:
        d = json.loads(pathlib.Path(exchangeinfo).read_text())
    else:
        with urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30) as r:
            d = json.loads(r.read())
    p = _deny_path(root)
    cur = json.loads(p.read_text()) if p.exists() else {"symbols": [], "evidence": {}}
    ev = cur.get("evidence", {})
    added = []
    for s in d.get("symbols", []):
        if s.get("contractType") == "TRADIFI_PERPETUAL" or "TradFi" in (s.get("underlyingSubType") or []):
            k = s["symbol"].upper()
            if k not in ev:
                added.append(k)
                eq = s.get("underlyingType") in ("EQUITY", "HK_EQUITY", "KR_EQUITY", "CN_EQUITY", "PREMARKET")
                ev[k] = {"source": f"Binance fapi/v1/exchangeInfo {dt.date.today().isoformat()}", "contractType": s.get("contractType"), "underlyingType": s.get("underlyingType"),
                         "underlyingSubType": s.get("underlyingSubType"), "status": s.get("status"), "baseAsset": s.get("baseAsset"),
                         "category": "equity/ETF/pre-IPO" if eq else "tradfi_non_equity (commodity/FX) - same TRADIFI_PERPETUAL contract type"}
    cur["evidence"] = ev
    cur["symbols"] = sorted(set(cur.get("symbols", [])) | set(ev))
    cur["equity_symbols"] = sorted(k for k, v in ev.items() if str(v.get("category", "")).startswith("equity"))
    cur["tradfi_non_equity_symbols"] = sorted(k for k, v in ev.items() if not str(v.get("category", "")).startswith("equity"))
    cur["updated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=1))
    os.replace(tmp, p)
    return added


def _strip(obj, deny, keep=frozenset()):
    """remove denied symbols from a JSON list/dict (keys) recursively one level; returns (new, removed). Items in keep stay."""
    removed = []
    if isinstance(obj, list):
        new = []
        for x in obj:
            if isinstance(x, str) and symbol_of(x) in deny and x not in keep:
                removed.append(x)
            elif isinstance(x, dict) and any(isinstance(x.get(k), str) and symbol_of(x[k]) in deny for k in ("sym_side", "symbol", "key", "ss")):
                removed.append(json.dumps(x)[:80])
            else:
                new.append(x)
        return new, removed
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if symbol_of(k) in deny and k not in keep:
                removed.append(k)
                continue
            if isinstance(v, (list, dict)) and k in LIST_KEYS:
                v, r = _strip(v, deny, keep)
                removed += r
            new[k] = v
        return new, removed
    return obj, removed


LIST_KEYS = {"symbols", "sym_sides", "allowed_sym_sides", "crypto", "pinned", "sides", "queue", "unfinished", "keys", "items", "order", "exclude_none"}


def enforce_targets(root):
    root = pathlib.Path(root)
    t = sorted(root.glob("symbols_*.json")) + [root / "tradeable_keys.json", root / "tradeable_keys.bak.json", root / "universe_persistence.json"]
    t += sorted((root / "data").glob("v15_unfinished_*.json")) + [root / "data" / "v15_sched_pinned_sides.json"]
    t += [root / "data" / "daily_universe" / "last_good.json"] + sorted((root / "data" / "daily_universe").glob("20??????.json"))[-3:]
    t += [pathlib.Path.home() / "v15_priority_syms.txt", root / "data" / "open_position_sym_sides.json"]
    return [p for p in t if p.exists() and not p.name.startswith("symbols_trb_")]  # symbols_trb_* are stock-only + NEVER EDITABLE (LOCKED_FILES)


def open_position_keys(root):
    """'acc:SYM_SIDE' keys with |positionAmt|>0 in the live {acc}/{long,short}_positions.json (read-only). Their tradeable key is
    never stripped: the live system keeps open positions tradeable so they stay managed/closeable (never touch positions)."""
    out = set()
    for f in pathlib.Path(root).glob("*/*_positions.json"):
        if f.name not in ("long_positions.json", "short_positions.json"):
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for k, v in (d.items() if isinstance(d, dict) else []):
            try:
                if isinstance(v, dict) and abs(float(v.get("positionAmt") or 0)) > 0:
                    out.add(str(k))
            except (TypeError, ValueError):
                continue
    return out


def enforce(root=ROOT, apply=False, backup_dir=None, daily_backup=False):
    root = pathlib.Path(root)
    deny = load_denied(root)
    keep = frozenset(open_position_keys(root))
    ts = dt.datetime.now().strftime("%Y%m%d%H%M")
    bdir = pathlib.Path(backup_dir or root / "backups")
    report = {}
    for p in enforce_targets(root):
        try:
            raw = p.read_text()
        except Exception:
            continue
        if p.suffix == ".txt":
            toks = [t.strip() for t in re.split(r"[,\n]", raw) if t.strip()]
            removed = [t for t in toks if symbol_of(t.split()[0]) in deny]
            sep = "," if "," in raw else "\n"
            new_raw = sep.join(t for t in toks if t not in removed) + "\n"
        else:
            try:
                obj = json.loads(raw)
            except Exception:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(raw.strip())
                except Exception as e:
                    report[str(p)] = f"UNPARSEABLE {e}"
                    continue
            new, removed = _strip(obj, deny, keep if p.name.startswith(("tradeable_keys", "universe_persistence")) else frozenset())
            new_raw = json.dumps(new, indent=1 if "\n" in raw else None)
        if removed:
            report[str(p)] = removed
            if apply:
                bdir.mkdir(parents=True, exist_ok=True)
                bk = bdir / f"before_excl_tokenized_{p.name}_{ts[:8] if daily_backup else ts}{p.suffix}"
                if not bk.exists():
                    shutil.copy2(p, bk)
                tmp = p.with_name(p.name + ".excl_tmp")
                tmp.write_text(new_raw)
                os.replace(tmp, p)
    return report


SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "backups_code"}
CODE_EXT = {".py", ".sh", ".md", ".pyc", ".plist", ".service", ".timer", ".js", ".ts"}
SKIP_NAMES = {"excluded_tokenized_stocks.json", "binance_fapi_exchangeinfo_20261013.json"}


def scan(roots, root_for_deny=None, max_depth=8):
    """[(path, symbol, is_dir)] — files/dirs whose NAME carries a denied symbol token. Directories named after a symbol are returned whole."""
    hits = []
    for r in roots:
        r = pathlib.Path(os.path.expanduser(str(r)))
        if not r.exists():
            continue
        base_depth = len(r.parts)
        for dp, dns, fns in os.walk(r):
            if len(pathlib.Path(dp).parts) - base_depth >= max_depth:
                dns[:] = []
            keep = []
            for d in dns:
                if d in SKIP_DIRS or d.startswith("excluded_tokenized_stocks"):
                    continue
                s = name_hits(d, root_for_deny)
                if s:
                    hits.append((os.path.join(dp, d), s, True))
                else:
                    keep.append(d)
            dns[:] = keep
            for f in fns:
                if f in SKIP_NAMES or os.path.splitext(f)[1].lower() in CODE_EXT:
                    continue
                s = name_hits(f, root_for_deny)
                if s:
                    hits.append((os.path.join(dp, f), s, False))
    return hits


def archive(roots, dest, apply=False, root_for_deny=None, exclude_re=None, keep_syms_re=None, keep_syms=()):
    """MOVE hits to dest/files/<abs path> + manifest.json. exclude_re: path regex never moved. keep_syms within paths matching
    keep_syms_re are never moved (e.g. symbols with a live open position keep their live klines caches)."""
    dest = pathlib.Path(os.path.expanduser(str(dest)))
    hits = scan(roots, root_for_deny)
    ex = re.compile(exclude_re) if exclude_re else None
    kr = re.compile(keep_syms_re) if keep_syms_re else None
    keep_syms = {str(k).upper() for k in keep_syms}
    skipped = [h for h in hits if (ex and ex.search(h[0])) or (h[1] in keep_syms and (kr is None or kr.search(h[0])))]
    hits = [h for h in hits if h not in skipped]
    man = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "deny_file": str(_deny_path(root_for_deny)), "roots": [str(r) for r in roots], "applied": apply, "moved": [], "errors": []}
    per = {}
    for src, sym, is_dir in hits:
        rel = os.path.abspath(src).lstrip("/")
        dst = dest / "files" / rel
        rec = {"src": src, "dst": str(dst), "symbol": sym, "dir": is_dir}
        try:
            rec["bytes"] = os.path.getsize(src) if not is_dir else sum(f.stat().st_size for f in pathlib.Path(src).rglob("*") if f.is_file())
        except Exception:
            rec["bytes"] = None
        if apply:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src, dst)
            except Exception as e:
                man["errors"].append({"src": src, "err": str(e)[:200]})
                continue
        man["moved"].append(rec)
        per[sym] = per.get(sym, 0) + 1
    man["skipped"] = [{"src": p, "symbol": sym} for p, sym, _d in skipped]
    man["count"] = len(man["moved"])
    man["bytes"] = sum(r.get("bytes") or 0 for r in man["moved"])
    man["per_symbol"] = dict(sorted(per.items()))
    if apply:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(json.dumps(man, indent=1))
    return man


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--exchangeinfo")
    e = sp.add_parser("enforce")
    e.add_argument("--apply", action="store_true")
    e.add_argument("--root", default=str(ROOT))
    e.add_argument("--loop", type=int, default=0, help="seconds between passes (enforcer daemon; live writers re-add symbols until their code reads the deny)")
    s = sp.add_parser("scan")
    s.add_argument("--root", action="append")
    a_ = sp.add_parser("archive")
    a_.add_argument("--root", action="append")
    a_.add_argument("--dest", required=True)
    a_.add_argument("--apply", action="store_true")
    a_.add_argument("--exclude-re")
    a_.add_argument("--keep-syms", default="")
    a_.add_argument("--keep-syms-re")
    a = ap.parse_args()
    if a.cmd == "build":
        print("added", build(a.exchangeinfo))
    elif a.cmd == "enforce" and a.loop:
        import time
        while True:
            try:
                rep = enforce(a.root, a.apply, daily_backup=True)
                if rep:
                    print(dt.datetime.now(dt.timezone.utc).isoformat(), json.dumps({k: (v if isinstance(v, str) else v[:12]) for k, v in rep.items()}), flush=True)
            except Exception as ex:
                print(f"[enforce] error {ex}", flush=True)
            time.sleep(a.loop)
    elif a.cmd == "enforce":
        print(json.dumps(enforce(a.root, a.apply), indent=1))
    elif a.cmd == "scan":
        h = scan(a.root or [str(ROOT)])
        for p, sym, d in h:
            print(("D " if d else "F ") + sym + " " + p)
        print(f"[scan] {len(h)} hits", file=sys.stderr)
    elif a.cmd == "archive":
        m = archive(a.root or [str(ROOT)], a.dest, a.apply, exclude_re=a.exclude_re, keep_syms_re=a.keep_syms_re, keep_syms=[x for x in a.keep_syms.split(",") if x])
        print(json.dumps({k: m[k] for k in ("count", "bytes", "per_symbol", "errors", "applied")}, indent=1))
        print(f"[archive] skipped {len(m['skipped'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
