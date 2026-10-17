#!/usr/bin/env python3
"""board_name_truth_guard — a published board's file name must carry the gain its own chart page proves.

USER 2026-10-17: "make sure this can never happen again ... the gain number needs to have commissions included and be
the exact sum of all trades shown on the html page".

SINGLE SOURCE OF TRUTH (per *_30d_matrix.html page):
    G = sum(pnl_dollars of every closed trade in `const TRADES`, excluding unrealised / FINAL_MTM marks)
        / peak capital * 100
    peak capital = the "peak $X" printed in the header. The header prints it rounded to whole dollars, so the exact
    value is re-derived from the page's own TRADES + countable EVENTS (event replay: OPEN sets, AUGMENT adds, REDUCE
    subtracts, a realised close resets) and must round to the printed "$X" (else HEADER_PEAK_MISMATCH).
    pnl_dollars are already net of commissions (crypto round trip 0.04%, half per leg; tradier commission-free).

RULES
  * name gain token != G rounded to 2 decimals (gain12p38 / gainm3p15)  -> rename the triplet (html, xlsx, manifest
    and their .superseded twins) to the true name, backup first, manifest gets "name_truth".
  * header gain != G (+-0.01) or header peak != page peak -> QUARANTINE: the board (every copy on this host) is moved
    to <dir>/_quarantine_name_truth/ under its true name, never published, ALERT line in the log.
  * a page inside a quarantine dir that is now consistent (e.g. re-rendered with tools.opt.hires_chart.generate_hires) moves
    back to the publish dir with its companions (UNQUARANTINE).
  * copies without their own page (xlsx in SPREADSHEETS root, manifest archives, sync arrivals) follow the old->new map
    (persisted in the state file): renamed, or deleted after backup when the true name already exists there. This is
    what makes a sync unable to resurrect an old name.
  * maps are shared between hosts: every run writes data/reports/board_name_truth_map.json (this host's own entries) and
    reads data/reports/board_name_truth_peers/*.json (other hosts' maps, copied by tools/board_name_truth_map_sync.sh), so
    a copy whose page lives only on another host is still renamed / quarantined here.
  * files younger than --min-age seconds are left alone (a publish may be mid-flight).
Only board files are ever touched. Backups: <root>/backups/board_name_truth/<UTC>.tar (original relative paths).
Idempotent. --dry-run plans without touching anything.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import socket
import sys
import tarfile
import time
from pathlib import Path

NAME_RE = re.compile(r"^(?P<ss>[A-Z0-9]+_(?:LONG|SHORT))_bh(?P<bh>m?[0-9]+p[0-9]+)_gain(?P<g>m?[0-9]+p[0-9]*)(?P<t>_t[0-9]+)?_30d_matrix(?P<suf>.*)$")
SCAN_SUBDIRS = ("SPREADSHEETS", "data/reports/charts_1M", "data/reports/charts_1Y", "data/manifest_archive_mac", "spreadheeets")
SCAN_FLAT = ("", "data")  # board copies dropped loose in the root or data/ (not recursive: those trees are huge)
SKIP_DIRS = {"backups", ".git", "node_modules", "__pycache__"}
QDIR = "_quarantine_name_truth"
ANCHOR_SUFFIXES = {".html": (".xlsx", "_manifest.json"), ".html.superseded": (".xlsx.superseded", "_manifest.json.superseded")}
MTM_PREFIXES = ("FINAL_MTM", "WINDOW_END_MTM", "MTM_FINAL", "OPEN_AT_WINDOW_END")
HDR_GAIN_RE = re.compile(r"gain\s+(-?[\d.]+)%")
HDR_PEAK_RE = re.compile(r"peak\s+\$(-?[\d.]+)")
GAIN_TOL = 0.01
LOG = Path.home() / "logs" / "board_name_truth_guard.log"


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg: str, dry: bool) -> None:
    line = f"{now_utc()} {socket.gethostname()} {'[DRY] ' if dry else ''}{msg}"
    print(line, flush=True)
    if dry:
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def fmt_token(v: float) -> str:
    r = round(float(v), 2)
    if r == 0:
        r = 0.0
    return ("m" if r < 0 else "") + f"{abs(r):.2f}".replace(".", "p")


def is_unrealised(row) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("unrealised") is True or row.get("is_mtm") is True:
        return True
    return any(str(row.get(k) or "").upper().startswith(MTM_PREFIXES) for k in ("exit_reason", "reason"))


def js_const(text: str, name: str):
    key = "const %s =" % name
    i = text.find(key)
    if i < 0:
        return None
    j = text.index("=", i) + 1
    k = text.find(";\n", j)
    if k < 0:
        return None
    try:
        return json.loads(text[j:k].strip())
    except ValueError:
        return None


def event_peaks(rows) -> dict:
    """Both event replays the chart header has ever printed: "cost" = cost-basis peak (tools/opt/evaluate_v12._peak_concurrent
    since 2026-10-17, engine 4532fd52 / evaluator 784cf47d) and "price" = |qty| x latest fill price (earlier pages). Sweep fallback
    when the page has no OPEN events."""
    evs = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        ty = str(t.get("type") or "").upper()
        try:
            ts = float(t.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ty in ("OPEN", "AUGMENT", "REDUCE"):
            try:
                p, q = float(t.get("price") or 0.0), float(t.get("qty") or 0.0)
            except (TypeError, ValueError):
                continue
            if p <= 0 or q == 0 or q != q or p != p:
                continue
            evs.append((ts, 0 if ty == "OPEN" else 1, ty, abs(q), p))
        elif "pnl_dollars" in t:
            evs.append((ts, 2, "CLOSE", 0.0, 0.0))
    if not any(e[2] == "OPEN" for e in evs):
        sw = sweep_peak([t for t in rows if isinstance(t, dict) and "pnl_dollars" in t])
        return {"cost": sw, "price": sw}
    evs.sort(key=lambda e: (e[0], e[1]))
    qty = cost = pk_cost = pk_price = 0.0
    for _ts, _rk, ty, q, p in evs:
        if ty == "OPEN":
            qty, cost = q, q * p
        elif ty == "AUGMENT":
            qty += q
            cost += q * p
        elif ty == "REDUCE":
            r = min(q, qty)
            if qty > 0:
                cost -= cost * (r / qty)
            qty = max(0.0, qty - r)
        else:
            qty, cost = 0.0, 0.0
            continue
        pk_cost = max(pk_cost, cost)
        pk_price = max(pk_price, qty * p)
    return {"cost": float(pk_cost), "price": float(pk_price)}


def event_peak(rows) -> float:
    return event_peaks(rows)["cost"]


def sweep_peak(trades) -> float:
    ev = []
    for t in trades:
        try:
            d = float(t.get("deployed") or 0.0)
        except (TypeError, ValueError):
            continue
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        if d <= 0 or be is None or bx is None:
            continue
        ev.append((int(be), d))
        ev.append((int(bx), -d))
    ev.sort(key=lambda x: (x[0], -x[1]))
    cur = peak = 0.0
    for _b, delta in ev:
        cur += delta
        peak = max(peak, cur)
    return float(peak)


def max_dd(trades, base: float) -> float:
    if base <= 0:
        return 0.0
    rows = sorted(trades, key=lambda t: (int(t.get("bar_exit") or 0), int(t.get("bar_entry") or 0)))
    equity = peak = float(base)
    worst = 0.0
    for t in rows:
        equity += float(t.get("pnl_dollars") or 0.0)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)
    return min(100.0, worst)


def page_truth(path: Path) -> dict:
    """Everything the guard decides on, computed from the page alone."""
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        return {"status": "UNREADABLE", "why": str(exc)[:120]}
    trades = js_const(text, "TRADES")
    if not isinstance(trades, list):
        return {"status": "NO_LEDGER", "why": "no parseable const TRADES"}
    events = js_const(text, "EVENTS") or []
    closed = [t for t in trades if isinstance(t, dict) and t.get("pnl_dollars") is not None and not is_unrealised(t)]
    countable_events = [e for e in events if isinstance(e, dict) and not e.get("unrealised")]
    pnl = sum(float(t.get("pnl_dollars") or 0.0) for t in closed)
    i = text.find('<div class="metrics">')
    seg = text[i: text.find("</div>", i)] if i >= 0 else text[:6000]
    mg, mp = HDR_GAIN_RE.search(seg), HDR_PEAK_RE.search(seg)
    hdr_gain = float(mg.group(1)) if mg else None
    hdr_peak = float(mp.group(1)) if mp else None
    pks = event_peaks(closed + countable_events)
    # the header's printed "peak $X" decides which replay the page was written with; cost basis when it matches or is absent
    peak_method = "cost"
    if hdr_peak is not None and abs(round(pks["cost"]) - hdr_peak) > 1.0 and abs(round(pks["price"]) - hdr_peak) <= 1.0:
        peak_method = "price"
    peak = pks[peak_method]
    out = {"sum_pnl_dollars": pnl, "peak": peak, "peak_header": hdr_peak, "peak_sweep": sweep_peak(closed), "header_gain": hdr_gain, "closed_trades": len(closed), "peak_method": peak_method, "mtm_rows_excluded": sum(1 for t in trades if isinstance(t, dict) and is_unrealised(t))}
    if peak <= 0:
        out.update(status="NO_PEAK", why="page peak is 0")
        return out
    g = pnl / peak * 100.0
    out.update(G=g, max_dd_pct=max_dd(closed, peak), token=fmt_token(g))
    reasons = []
    if hdr_gain is None or abs(round(g, 2) - hdr_gain) > GAIN_TOL + 1e-9:
        reasons.append(f"HEADER_GAIN_NE_G header={hdr_gain} G={g:.4f}")
    if hdr_peak is None or abs(round(peak) - hdr_peak) > 1.0:
        reasons.append(f"HEADER_PEAK_NE_PAGE header=${hdr_peak} page=${peak:.2f}")
    out["status"] = "QUARANTINE" if reasons else "OK"
    out["why"] = "; ".join(reasons)
    return out


class Guard:
    def __init__(self, roots, dry: bool, min_age: int, state_path: Path):
        self.roots = [Path(r).expanduser().resolve() for r in roots]
        self.dry = dry
        self.min_age = min_age
        self.state_path = state_path
        self.state = {"map": {}, "quarantined": {}, "cache": {}}
        try:
            self.state.update(json.loads(state_path.read_text()))
        except (OSError, ValueError):
            pass
        self.peers_loaded = 0
        for pf in sorted((state_path.parent / "board_name_truth_peers").glob("*.json")):
            try:
                peer = json.loads(pf.read_text())
            except (OSError, ValueError):
                continue
            for k, v in (peer.get("map") or {}).items():
                if isinstance(v, dict) and v.get("new") and k not in self.state["map"]:
                    self.state["map"][k] = dict(v, via_peer=pf.name)
            for k, v in (peer.get("quarantined") or {}).items():
                self.state["quarantined"].setdefault(k, dict(v, via_peer=pf.name) if isinstance(v, dict) else v)
            self.peers_loaded += 1
        self.tars = {}
        self.actions = []
        self.alerts = []
        self.stats = {"pages": 0, "ok": 0, "renamed_stems": 0, "quarantined_stems": 0, "no_ledger": 0, "young_skipped": 0, "unquarantined_stems": 0}

    # ---------- scan ----------
    def scan(self):
        files = []
        for root in self.roots:
            for sub in SCAN_FLAT:
                base = root / sub if sub else root
                try:
                    names = os.listdir(base)
                except OSError:
                    continue
                for f in names:
                    m = NAME_RE.match(f)
                    if m and ".tmp" not in m.group("suf") and (base / f).is_file():
                        files.append((root, base / f, m))
            for sub in SCAN_SUBDIRS:
                base = root / sub
                if not base.is_dir():
                    continue
                for d, ds, fs in os.walk(base):
                    ds[:] = [x for x in ds if x not in SKIP_DIRS]
                    for f in fs:
                        m = NAME_RE.match(f)
                        if not m or ".tmp" in m.group("suf"):
                            continue
                        files.append((root, Path(d) / f, m))
        return files

    def root_of(self, p: Path) -> Path:
        for r in self.roots:
            try:
                p.relative_to(r)
                return r
            except ValueError:
                continue
        return self.roots[0]

    # ---------- side effects ----------
    def backup(self, p: Path) -> None:
        if self.dry:
            return
        root = self.root_of(p)
        if root not in self.tars:
            bdir = root / "backups" / "board_name_truth"
            bdir.mkdir(parents=True, exist_ok=True)
            self.tars[root] = tarfile.open(bdir / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{os.getpid()}.tar", "a")
        self.tars[root].add(str(p), arcname=str(p.relative_to(root)), recursive=False)

    def _act(self, kind: str, src: Path, dst: Path | None = None, note: str = "") -> None:
        self.actions.append({"action": kind, "src": str(src), "dst": str(dst) if dst else None, "note": note})
        log(f"{kind} {src}{' -> ' + str(dst) if dst else ''}{' | ' + note if note else ''}", self.dry)

    def move(self, src: Path, dst: Path, note: str = "", truth: dict | None = None) -> None:
        if src == dst:
            return
        if dst.exists():
            self.delete(src, f"true name exists at {dst.name}; {note}")
            return
        self._act("RENAME" if src.parent == dst.parent else "MOVE", src, dst, note)
        if self.dry:
            return
        self.backup(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        st = src.stat()
        os.rename(src, dst)
        if dst.name.endswith(("_manifest.json", "_manifest.json.superseded")) and truth is not None:
            self.stamp_manifest(dst, src.name, truth, st)

    def delete(self, src: Path, note: str) -> None:
        self._act("DELETE", src, None, note)
        if self.dry:
            return
        self.backup(src)
        src.unlink()

    def stamp_manifest(self, p: Path, old_name: str, truth: dict, st) -> None:
        try:
            man = json.loads(p.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(man, dict):
            return
        claimed = (man.get("metrics") or {}).get("gain_pct")
        new_xlsx = p.name.replace("_manifest.json", ".xlsx")
        if man.get("final_name") and man.get("final_name") != new_xlsx:
            man.setdefault("final_name_before_name_truth", man.get("final_name"))
            man["final_name"] = new_xlsx
        man["name_truth"] = {"G": truth.get("G"), "sum_pnl_dollars": truth.get("sum_pnl_dollars"), "peak": truth.get("peak"), "peak_header": truth.get("peak_header"), "header_gain": truth.get("header_gain"), "max_dd_pct": truth.get("max_dd_pct"), "closed_trades": truth.get("closed_trades"), "old_name": old_name, "page": truth.get("page"), "status": truth.get("status"), "checked_utc": now_utc()}
        man["nolies_correction"] = {"claimed_gain_pct": claimed, "true_gain_pct": truth.get("G"), "true_max_dd_pct": truth.get("max_dd_pct"), "window": "board", "method": "sum(page TRADES pnl_dollars) / page peak (event replay = header peak)", "measured_utc": now_utc(), "evaluator_md5": "40479fd2c1a99840b37bd4c15f42693b"}
        tmp = p.with_name(p.name + ".nametruth.tmp")
        tmp.write_text(json.dumps(man, indent=1, default=str))
        os.replace(tmp, p)
        os.utime(p, (st.st_atime, st.st_mtime))

    # ---------- core ----------
    def truth_for(self, p: Path) -> dict:
        st = p.stat()
        key = str(p)
        c = self.state["cache"].get(key)
        if c and c.get("size") == st.st_size and c.get("mtime") == st.st_mtime:
            return c["truth"]
        t = page_truth(p)
        t["page"] = str(p)
        self.state["cache"][key] = {"size": st.st_size, "mtime": st.st_mtime, "truth": t}
        return t

    def run(self) -> dict:
        now = time.time()
        files = self.scan()
        groups = {}
        for root, p, m in files:
            stem = p.name[: p.name.index("_30d_matrix")]
            groups.setdefault((p.parent, stem), {})[m.group("suf")] = p
        newmap = {}
        true_stems = set()
        handled = set()
        # pass 1: every page decides its own triplet
        order = sorted(groups.items(), key=lambda kv: (0 if kv[0][0].name == "V15_V16_CELL_BY_CELL" else 1, str(kv[0][0])))
        for (d, stem), sufs in order:
            for asuf, companions in ANCHOR_SUFFIXES.items():
                page = sufs.get(asuf)
                if page is None:
                    continue
                members = [page] + [sufs[c] for c in companions if c in sufs]
                if asuf == ".html":
                    members += [p for s, p in sufs.items() if s not in ANCHOR_SUFFIXES and s not in (".xlsx", "_manifest.json", ".xlsx.superseded", "_manifest.json.superseded")]
                if any(now - x.stat().st_mtime < self.min_age for x in members):
                    self.stats["young_skipped"] += 1
                    handled.update(members)
                    continue
                self.stats["pages"] += 1
                t = self.truth_for(page)
                if t.get("status") in ("NO_LEDGER", "UNREADABLE", "NO_PEAK"):
                    self.stats["no_ledger"] += 1
                    continue  # unprovable page: its copies still follow the map in pass 2
                handled.update(members)
                m = NAME_RE.match(page.name)
                new_stem = f"{m.group('ss')}_bh{m.group('bh')}_gain{t['token']}{m.group('t') or ''}"
                quarantine = t["status"] == "QUARANTINE"
                in_q = QDIR in page.parts
                true_stems.add(new_stem)
                newmap.setdefault(stem, {"new": new_stem, "G": t["G"], "sum_pnl_dollars": t["sum_pnl_dollars"], "peak": t["peak"], "peak_header": t.get("peak_header"), "header_gain": t.get("header_gain"), "max_dd_pct": t.get("max_dd_pct"), "closed_trades": t.get("closed_trades"), "page": str(page), "status": t["status"]})
                if quarantine:
                    self.state["quarantined"][new_stem] = {"why": t["why"], "utc": now_utc(), "page": str(page)}
                if in_q and not quarantine:
                    # re-rendered page is consistent: back to the publish dir under its true name
                    self.stats["unquarantined_stems"] += 1
                    self.state["quarantined"].pop(new_stem, None)
                    self.state["quarantined"].pop(stem, None)
                    for x in members:
                        self.move(x, d.parent / x.name.replace(stem + "_30d_matrix", new_stem + "_30d_matrix", 1), f"UNQUARANTINE G={t['G']:.4f} name={stem}", dict(t))
                    continue
                if new_stem == stem and (not quarantine or in_q):
                    self.stats["ok"] += 1
                    continue
                if quarantine and not in_q:
                    self.stats["quarantined_stems"] += 1
                    self.alerts.append(f"QUARANTINE {page} {t['why']}")
                    log(f"ALERT QUARANTINE {page.name}: {t['why']}", self.dry)
                    dest_dir = d / QDIR
                else:
                    self.stats["renamed_stems"] += 1
                    dest_dir = d
                for x in members:
                    self.move(x, dest_dir / x.name.replace(stem + "_30d_matrix", new_stem + "_30d_matrix", 1), f"G={t['G']:.4f} name={stem}", dict(t))
        # merge map (pages seen this run win over remembered entries)
        for old, info in newmap.items():
            if info["new"] != old:
                self.state["map"][old] = info
            elif info["status"] == "OK":
                self.state["map"].pop(old, None)
        # pass 2: copies without their own page follow the map (old names from syncs die here)
        for (d, stem), sufs in groups.items():
            for suf, p in sufs.items():
                if p in handled or not p.exists():
                    continue
                if now - p.stat().st_mtime < self.min_age:
                    continue
                info = self.state["map"].get(stem)
                if info and stem in true_stems and QDIR not in p.parts:
                    continue  # a page proves this name true (old-format names can collide) — never chain-rename it away
                target_stem = info["new"] if info else stem
                q = target_stem in self.state["quarantined"] and QDIR not in p.parts
                if target_stem == stem and not q:
                    continue
                dest_dir = d / QDIR if q else d
                self.move(p, dest_dir / p.name.replace(stem + "_30d_matrix", target_stem + "_30d_matrix", 1), f"map {stem} -> {target_stem}{' (quarantined)' if q else ''}", dict(info or {}))
        for tf in self.tars.values():
            tf.close()
        live = {str(p) for _r, p, _m in files}
        self.state["cache"] = {k: v for k, v in self.state["cache"].items() if k in live}
        if not self.dry:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(self.state, default=str))
            os.replace(tmp, self.state_path)
            mp = self.state_path.with_name("board_name_truth_map.json")
            tmp = mp.with_name(mp.name + ".tmp")
            tmp.write_text(json.dumps({"host": socket.gethostname(), "utc": now_utc(), "map": {k: v for k, v in self.state["map"].items() if not v.get("via_peer")}, "quarantined": {k: v for k, v in self.state["quarantined"].items() if not (isinstance(v, dict) and v.get("via_peer"))}}, default=str))
            os.replace(tmp, mp)
        summary = {"utc": now_utc(), "host": socket.gethostname(), "dry_run": self.dry, "roots": [str(r) for r in self.roots], "files_scanned": len(files), **self.stats, "peer_maps": self.peers_loaded, "actions": len(self.actions), "alerts": len(self.alerts)}
        log("SUMMARY " + json.dumps(summary), self.dry)
        return {"summary": summary, "actions": self.actions, "alerts": self.alerts}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", action="append", help="repo/sandbox root (repeatable); default = this file's repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-age", type=int, default=600, help="seconds a board file must be untouched before it is judged")
    ap.add_argument("--state", default=None, help="state json (map/quarantine/cache); default <first root>/data/reports/board_name_truth_state.json")
    ap.add_argument("--report", default=None, help="write the full actions/alerts json here")
    a = ap.parse_args(argv)
    roots = a.root or [str(Path(__file__).resolve().parent.parent)]
    state = Path(a.state) if a.state else Path(roots[0]).expanduser() / "data" / "reports" / "board_name_truth_state.json"
    lock_path = Path(state).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lf = open(lock_path, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another board_name_truth_guard run holds the lock — exiting", flush=True)
        return 0
    res = Guard(roots, a.dry_run, a.min_age, state).run()
    if a.report:
        Path(a.report).write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
