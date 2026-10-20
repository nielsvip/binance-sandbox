#!/usr/bin/env python3
"""v15_published_set — resolve the EXACT set a published board/chart was computed with, and prove the chart reproduces from it.

WHY THIS EXISTS (measured 2026-10-20). A published chart is written by `tools/opt/hires_chart.generate_hires`, which calls
`tools/opt/lifecycle_pilot.evaluate_month` -> `v12_quick_engine.simulate_one`: the SAME vector engine the chain decides on. The
chart is therefore reproducible by construction. What is NOT reproducible is finding its set again:

  * `v15_pilot` writes the chart and the manifest from the same `cumulative_overrides` variable, but the manifest sidecar carries
    only `overrides_md5` — never the set itself.
  * the set lives in the PUBLISHING WORKER's run directory (`~/v15_run33_20261019/progress/{SS}_v14_progress.json`) and in that
    host's `per_sym_history` publish rows. Neither is synced to the Mac.
  * the Mac's `data/reports/lifecycle_pilot/{SS}_v14_progress.json` is a DIFFERENT, older set (measured: BWXT_SHORT 791 switches
    md5 2a6c4da3 vs the published 881 switches md5 7ae4203e; TRGP_LONG 4 switches; LNG_LONG 53), and the Mac's per_sym_store.db
    holds 0 `fleet_orchestrator_publish` rows.

So "re-run the board's own recorded set" on the Mac silently tested a stale set and reported the chart as irreproducible:
BWXT_SHORT published 31 trades / +31.91%, the stale set gives 72 trades / +10.91% on the SAME NPZ and the SAME 1099-bar window.
Nothing warned. This module makes that failure mode impossible: a set is returned only when its md5 equals the board manifest's
`overrides_md5`, otherwise it raises and names every source it tried.

USE
    # resolve + prove, pulling the set from the publishing hosts and caching it locally
    python tools/v15_published_set.py --symside BWXT_SHORT --hosts s1-int,s2 --verify

    # library
    from tools.v15_published_set import resolve_published_set
    r = resolve_published_set("BWXT_SHORT")        # raises PublishedSetNotFound if the md5 cannot be matched
    r["overrides"], r["overrides_md5"], r["source"], r["manifest"]

WINDOW SEMANTICS (why some charts can never be reproduced again). `--window-days 30` on stocks means the last 30 TRADING SESSIONS
anchored to the NPZ's LAST BAR (`lifecycle_pilot.exact_month_slice`, `unique[-30]`); crypto means 30 calendar days from the last
bar. There is no offset parameter. Once the NPZ gains a session the window slides, so a chart published against an earlier NPZ is
structurally unreachable. The manifest records `npz_md5` and `provenance.npz_window`, so this tool reports UNREACHABLE_WINDOW
instead of pretending the engine disagrees. Measured 2026-10-20: BWXT_SHORT/TRGP_LONG/LNG_LONG still on their published NPZ,
AAPL_LONG/ULTA_LONG/LEXX_SHORT already past it.

READ-ONLY. Never writes a published chart, board or manifest; the only thing it writes is its own cache of resolved sets under
data/reports/published_sets/.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET_DIRS = (
    ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL",
    ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL_FINAL",
    ROOT / "SPREADSHEETS",
)
CHART_DIRS = (
    ROOT / "data" / "reports" / "charts_1M",
    ROOT / "data" / "reports" / "charts_1Y",
    ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL",
)
CACHE_DIR = ROOT / "data" / "reports" / "published_sets"
PER_SYM_DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
PUBLISH_SOURCE = "fleet_orchestrator_publish"
# The publishing hosts keep the set in their run dirs and their own publisher db; the Mac keeps neither.
HOST_DB_PATHS = ("binance-sandbox/data/hourly_reconfig/per_sym_store.db", "binance/data/hourly_reconfig/per_sym_store.db")
HOST_RUN_GLOB = "~/v15_*/progress/%s_v14_progress.json"


class PublishedSetNotFound(RuntimeError):
    """The set whose md5 matches the board manifest could not be found — never substitute another set."""


def overrides_md5(overrides) -> str:
    """The pilot's own canonical hash (v15_pilot.py:10189 and tools/v15_published_selection.overrides_md5)."""
    return hashlib.md5(json.dumps(dict(overrides or {}), sort_keys=True, default=str).encode()).hexdigest()


def _ts(value) -> float:
    try:
        import datetime

        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def board_manifests(symside: str) -> list:
    """Every publish manifest for this sym_side, NEWEST PUBLISH FIRST."""
    seen, out = set(), []
    for directory in SHEET_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.glob(f"{symside}_*_manifest.json"):
            try:
                man = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if str(man.get("symside", "")).upper() != symside.upper():
                continue
            key = (str(man.get("overrides_md5")), str(man.get("published_utc")))
            if key in seen:
                continue
            seen.add(key)
            man["_manifest_path"] = str(path)
            out.append(man)
    out.sort(key=lambda m: _ts(m.get("published_utc")), reverse=True)
    return out


def latest_manifest(symside: str) -> dict:
    mans = board_manifests(symside)
    if not mans:
        raise PublishedSetNotFound(f"{symside}: no publish manifest under {[str(d) for d in SHEET_DIRS]} — nothing was published for this sym_side, so there is no set to resolve")
    return mans[0]


def _cache_path(symside: str, md5: str) -> Path:
    return CACHE_DIR / f"{symside}_{md5}.json"


def _from_cache(symside: str, want: str):
    path = _cache_path(symside, want)
    if not path.is_file():
        return None
    try:
        overrides = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if overrides_md5(overrides) != want:
        return None
    return overrides, f"cache:{path.relative_to(ROOT)}"


def _from_db(db: Path, symside: str, want: str):
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "select updated_at, overrides_json from per_sym_history where sym_side = ? and json_extract(meta_json, '$.source') = ? order by updated_at desc",
            (symside.upper(), PUBLISH_SOURCE),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    for updated_at, overrides_json in rows:
        try:
            overrides = json.loads(overrides_json or "{}")
        except ValueError:
            continue
        if overrides and overrides_md5(overrides) == want:
            return overrides, f"per_sym_history:{db.name}@{updated_at}"
    return None


def _progress_candidates(symside: str):
    base = ROOT / "data" / "reports" / "lifecycle_pilot"
    if not base.is_dir():
        return
    patterns = (f"{symside}_v14_progress.json", f"{symside}_v14_progress.json.*.json", f"*/{symside}_v14_progress.json")
    seen = set()
    for pattern in patterns:
        for path in base.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            yield path


def _from_progress(symside: str, want: str):
    for path in _progress_candidates(symside):
        try:
            overrides = json.loads(path.read_text()).get("cumulative_overrides") or {}
        except (OSError, ValueError):
            continue
        if overrides and overrides_md5(overrides) == want:
            return overrides, f"progress:{path.relative_to(ROOT)}"
    return None


def _host_script(symside: str, want: str) -> str:
    """Scan a publishing host for the set with this md5: its run dirs first, then its publisher db. Prints one JSON line."""
    return (
        "python3 - <<'V15PSEOF'\n"
        "import glob, hashlib, json, os, sqlite3, sys\n"
        f"SS = {symside.upper()!r}\n"
        f"WANT = {want!r}\n"
        f"DBS = {list(HOST_DB_PATHS)!r}\n"
        "def md5(o):\n"
        "    return hashlib.md5(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()\n"
        "home = os.path.expanduser('~')\n"
        "found = None\n"
        f"pats = [os.path.expanduser({HOST_RUN_GLOB!r} % SS), home + '/binance-sandbox/data/reports/lifecycle_pilot/**/' + SS + '_v14_progress.json']\n"
        "cands = []\n"
        "for p in pats:\n"
        "    try:\n"
        "        cands.extend(glob.glob(p, recursive=True))\n"
        "    except Exception:\n"
        "        pass\n"
        "for p in sorted(set(cands), key=lambda x: -os.path.getmtime(x) if os.path.exists(x) else 0):\n"
        "    try:\n"
        "        ov = json.load(open(p)).get('cumulative_overrides') or {}\n"
        "    except Exception:\n"
        "        continue\n"
        "    if ov and md5(ov) == WANT:\n"
        "        found = {'source': 'progress:' + p, 'overrides': ov}\n"
        "        break\n"
        "if found is None:\n"
        "    for rel in DBS:\n"
        "        db = os.path.join(home, rel)\n"
        "        if not os.path.isfile(db):\n"
        "            continue\n"
        "        try:\n"
        "            c = sqlite3.connect('file:' + db + '?mode=ro', uri=True)\n"
        "            rows = c.execute(\"select updated_at, overrides_json from per_sym_history where sym_side = ? and json_extract(meta_json, '$.source') = 'fleet_orchestrator_publish' order by updated_at desc\", (SS,)).fetchall()\n"
        "            c.close()\n"
        "        except Exception:\n"
        "            continue\n"
        "        for upd, ovj in rows:\n"
        "            try:\n"
        "                ov = json.loads(ovj or '{}')\n"
        "            except Exception:\n"
        "                continue\n"
        "            if ov and md5(ov) == WANT:\n"
        "                found = {'source': 'per_sym_history:' + rel + '@' + str(upd), 'overrides': ov}\n"
        "                break\n"
        "        if found:\n"
        "            break\n"
        "sys.stdout.write('V15PS' + json.dumps(found or {}) + 'V15PS')\n"
        "V15PSEOF\n"
    )


def _from_host(host: str, symside: str, want: str, timeout: int = 180):
    cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, _host_script(symside, want)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"V15PS(\{.*\})V15PS", proc.stdout, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    overrides = payload.get("overrides") or {}
    if not overrides or overrides_md5(overrides) != want:
        return None
    return overrides, f"{host}:{payload.get('source')}"


def resolve_published_set(symside: str, want_md5: str = None, hosts=(), cache: bool = True, manifest: dict = None) -> dict:
    """The set whose md5 equals the board manifest's `overrides_md5` — or an exception. NEVER a substitute.

    Order: local cache, local publisher db, local lifecycle_pilot progress JSONs (incl. .npzprev/.hollowprev variants and
    per-host subdirs), then each host in `hosts` (its run dirs, then its publisher db). A host hit is cached locally so the
    board becomes reproducible on this machine from then on.
    """
    symside = symside.upper()
    if manifest is None and not want_md5:
        manifest = latest_manifest(symside)
    want = str(want_md5 or (manifest or {}).get("overrides_md5") or "").strip()
    if len(want) != 32:
        raise PublishedSetNotFound(f"{symside}: manifest {(manifest or {}).get('_manifest_path')} carries no usable overrides_md5 ({want!r}) — the publishing set cannot be identified")
    tried = []
    for label, finder in (
        ("cache", lambda: _from_cache(symside, want)),
        ("per_sym_history(local)", lambda: _from_db(PER_SYM_DB, symside, want)),
        ("lifecycle_pilot(local)", lambda: _from_progress(symside, want)),
    ):
        tried.append(label)
        hit = finder()
        if hit:
            overrides, source = hit
            return {"symside": symside, "overrides": overrides, "overrides_md5": want, "n_overrides": len(overrides), "source": source, "manifest": manifest, "tried": tried}
    for host in hosts:
        tried.append(f"host:{host}")
        hit = _from_host(host, symside, want)
        if hit:
            overrides, source = hit
            if cache:
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    _cache_path(symside, want).write_text(json.dumps(overrides, indent=1, sort_keys=True, default=str))
                except OSError:
                    pass
            return {"symside": symside, "overrides": overrides, "overrides_md5": want, "n_overrides": len(overrides), "source": source, "manifest": manifest, "tried": tried}
    stale = _stale_local_md5s(symside)
    raise PublishedSetNotFound(
        f"{symside}: no set with the published md5 {want} (manifest {Path(str((manifest or {}).get('_manifest_path') or '?')).name}). "
        f"Tried {tried}. The Mac's own lifecycle_pilot sets hash to {stale or '[none]'} — a DIFFERENT set, which is why re-running "
        f"from them reports the chart as irreproducible. The publishing host keeps it in ~/v15_<run>/progress/{symside}_v14_progress.json "
        f"and in its per_sym_history publish rows: re-run with --hosts s1-int,s2 (or pass hosts=(...)) to fetch and cache it."
    )


def _stale_local_md5s(symside: str) -> list:
    out = []
    for path in _progress_candidates(symside):
        try:
            overrides = json.loads(path.read_text()).get("cumulative_overrides") or {}
        except (OSError, ValueError):
            continue
        if overrides:
            out.append(f"{overrides_md5(overrides)[:10]}({len(overrides)} switches, {path.name})")
    return out[:4]


def chart_trades(symside: str, manifest: dict) -> dict:
    """The TRADES array the published chart actually drew, found by the manifest's own final_name."""
    final_name = str(manifest.get("final_name") or "")
    html_name = final_name.replace(".xlsx", ".html")
    for directory in CHART_DIRS:
        path = directory / html_name
        if path.is_file():
            raw = path.read_bytes()
            match = re.search(rb"const TRADES\s*=\s*(\[.*?\]);", raw, re.S)
            if not match:
                return {"path": str(path), "trades": None, "error": "no const TRADES array in the chart"}
            rows = json.loads(match.group(1))
            labels = re.search(rb"const LABELS\s*=\s*(\[.*?\]);", raw, re.S)
            return {
                "path": str(path),
                "trades": rows,
                "n_trades": len(rows),
                "pnl_dollars": round(sum(float(t.get("pnl_dollars") or 0.0) for t in rows), 6),
                "bars": len(json.loads(labels.group(1))) if labels else None,
            }
    return {"path": None, "trades": None, "error": f"no published chart named {html_name} under {[str(d) for d in CHART_DIRS]}"}


def npz_reachable(symside: str, manifest: dict) -> dict:
    """Whether this host's NPZ is still the one the board was computed on — the window is anchored to its LAST BAR."""
    symbol = symside.rsplit("_", 1)[0]
    path = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
    want = str(manifest.get("npz_md5") or (manifest.get("provenance") or {}).get("npz_sha") or "")
    out = {"npz_path": str(path), "npz_md5_published": want, "npz_window_published": (manifest.get("provenance") or {}).get("npz_window")}
    if not path.is_file():
        out.update(npz_md5_local=None, reachable=False, reason="NPZ_MISSING")
        return out
    local = hashlib.md5(path.read_bytes()).hexdigest()
    out["npz_md5_local"] = local
    out["reachable"] = bool(want) and local == want
    if not out["reachable"]:
        out["reason"] = "UNREACHABLE_WINDOW: this host's NPZ is no longer the published one; the 30-session/30-day window is anchored to the NPZ's last bar and there is no offset parameter"
    return out


_LEDGER_KEYS = ("bar_entry", "bar_exit", "entry_price", "exit_price", "entry_reason", "exit_reason", "pnl_dollars", "ts", "type")


def _norm(row: dict) -> dict:
    out = {}
    for key in _LEDGER_KEYS:
        value = row.get(key)
        out[key] = round(float(value), 8) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    return out


def verify(symside: str, hosts=(), cache: bool = True) -> dict:
    """Resolve the published set, re-run the chart's OWN generator path, and compare against the chart's embedded TRADES."""
    symside = symside.upper()
    result = {"symside": symside}
    try:
        manifest = latest_manifest(symside)
    except PublishedSetNotFound as exc:
        return {**result, "verdict": "NO_MANIFEST", "detail": str(exc)}
    result["manifest"] = Path(str(manifest.get("_manifest_path"))).name
    result["published_utc"] = manifest.get("published_utc")
    result["published_metrics"] = manifest.get("metrics")
    try:
        resolved = resolve_published_set(symside, hosts=hosts, cache=cache, manifest=manifest)
    except PublishedSetNotFound as exc:
        return {**result, "verdict": "SET_NOT_FOUND", "detail": str(exc)}
    result.update(set_source=resolved["source"], overrides_md5=resolved["overrides_md5"], n_overrides=resolved["n_overrides"])
    reach = npz_reachable(symside, manifest)
    result["npz"] = reach
    chart = chart_trades(symside, manifest)
    result["chart"] = {k: v for k, v in chart.items() if k != "trades"}
    if not reach.get("reachable"):
        result["verdict"] = "UNREACHABLE_WINDOW"
        return result
    sys.path.insert(0, str(ROOT))
    from tools.opt.lifecycle_pilot import evaluate_month, _config_and_month_npz
    from tools.opt.hires_chart import _split_ledger
    import numpy as np

    window_days = int((manifest.get("provenance") or {}).get("window_days") or 30)
    evaluated = evaluate_month(symside, dict(resolved["overrides"]), include_ledger=True, window_days=window_days)
    ledger_all = evaluated.get("execution_ledger") or []
    _cfg, npz, _sym, _is_long, _mode, _tok, _win = _config_and_month_npz(symside, dict(resolved["overrides"]), window_days=window_days)
    exec_ts = np.asarray(npz.get("timestamps"), dtype=float).tolist()
    mine, _events = _split_ledger(ledger_all, exec_ts)
    result["recomputed"] = {
        "gain_pct": evaluated.get("gain_pct"), "bh_pct": evaluated.get("bh_pct"), "trades": evaluated.get("trades"),
        "tim_pct": evaluated.get("tim_pct"), "max_dd_pct": evaluated.get("max_dd_pct"),
        "bars": (evaluated.get("window") or {}).get("bars"), "drawn_trades": len(mine),
        "pnl_dollars": round(sum(float(t.get("pnl_dollars") or 0.0) for t in mine), 6),
    }
    if chart.get("trades") is None:
        result["verdict"] = "NO_CHART"
        return result
    same = [_norm(t) for t in chart["trades"]] == [_norm(t) for t in mine]
    result["row_for_row_identical"] = same
    result["verdict"] = "REPRODUCED" if same else "LEDGER_MISMATCH"
    if not same:
        a, b = [_norm(t) for t in chart["trades"]], [_norm(t) for t in mine]
        result["differing_rows"] = sum(1 for i in range(max(len(a), len(b))) if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None))
    return result


def _symsides_from_manifests(limit: int = 0) -> list:
    found = {}
    for directory in SHEET_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.glob("*_manifest.json"):
            try:
                man = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            ss = str(man.get("symside") or "").upper()
            if not ss:
                continue
            stamp = _ts(man.get("published_utc"))
            if stamp >= found.get(ss, 0.0):
                found[ss] = stamp
    order = [ss for ss, _s in sorted(found.items(), key=lambda kv: -kv[1])]
    return order[:limit] if limit else order


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve a published board's exact set and prove its chart reproduces from it")
    ap.add_argument("--symside", action="append", default=[], help="sym_side (repeatable)")
    ap.add_argument("--all", action="store_true", help="every sym_side that has a publish manifest, newest publish first")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--hosts", default="", help="comma-separated publishing hosts to fetch the set from (e.g. s1-int,s2)")
    ap.add_argument("--verify", action="store_true", help="re-run the chart's own generator and compare ledgers")
    ap.add_argument("--no-cache", action="store_true", help="do not cache a set fetched from a host")
    ap.add_argument("--json", default="", help="write the full report here")
    args = ap.parse_args()
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    symsides = [s.upper() for s in args.symside] or (_symsides_from_manifests(args.limit) if args.all else [])
    if not symsides:
        ap.error("pass --symside SS (repeatable) or --all")
    report, counts = [], {}
    for ss in symsides:
        if args.verify:
            row = verify(ss, hosts=hosts, cache=not args.no_cache)
        else:
            try:
                resolved = resolve_published_set(ss, hosts=hosts, cache=not args.no_cache)
                row = {"symside": ss, "verdict": "SET_RESOLVED", "overrides_md5": resolved["overrides_md5"], "n_overrides": resolved["n_overrides"], "set_source": resolved["source"]}
            except PublishedSetNotFound as exc:
                row = {"symside": ss, "verdict": "SET_NOT_FOUND", "detail": str(exc)}
        report.append(row)
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        rec = row.get("recomputed") or {}
        pub = row.get("published_metrics") or {}
        extra = ""
        if rec:
            extra = f" published trades={pub.get('trades')} gain={pub.get('gain_pct')} | recomputed trades={rec.get('trades')} gain={rec.get('gain_pct')} drawn={rec.get('drawn_trades')} bars={rec.get('bars')}"
        print(f"{row['verdict']:18s} {ss:16s} set={str(row.get('overrides_md5'))[:10]} n={row.get('n_overrides')} src={row.get('set_source')}{extra}", flush=True)
        if row.get("detail"):
            print(f"                   {row['detail']}", flush=True)
    print(f"\nSUMMARY {json.dumps(counts, sort_keys=True)}", flush=True)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"counts": counts, "rows": report}, indent=1, default=str))
        print(f"wrote {args.json}", flush=True)
    return 0 if not counts.get("LEDGER_MISMATCH") else 1


if __name__ == "__main__":
    raise SystemExit(main())
