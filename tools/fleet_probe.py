#!/usr/bin/env python3
"""fleet_probe — one-shot host census for tools/fleet_dispatch.py (USER 2026-10-27 rewrite).

Runs ON a worker, fed over ssh stdin (`ssh host PY - <json-args> < fleet_probe.py`), so a freshly
cloned or newly added server needs no deploy step to be seen. Prints ONE JSON object:
  host, ips, mem_avail_mb, disk_free_mb, ncpu, load1, pdir, repo,
  running {SS: etime_s}, progress {SS: {fg, complete, ts, rows, mtime}},
  tails {SYM: last_bar_ts}, launches {SS: record}, impossible {SS: mtime}, start_refused {SS: log mtime} (pilot exited at start on BASELINE-PERSYM-SNAPSHOT-INVALID / IMPOSSIBLE-SKIP / SOFT-SKIP / PROHIBITED; not a death).
`complete` uses tools/v15_progress_board.is_complete — the SAME rule the harvester uses, so the
dispatcher never skips a board the harvester refuses (the 2026-10-27 publish deadlock). Never raises.
"""
import glob
import json
import math
import os
import sqlite3
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
SB = os.path.join(HOME, "binance-sandbox")
CACHE = "/tmp/fleet_probe_cache.json"
LAUNCH_DIR = os.path.join(HOME, "fleet_dispatch", "launches")
PILOT_RE = "^[^ ]*python[^ ]* -u ([^ ]*/)?v15_pilot(_7d|_no_publish_resume)?[.]py --sym-side "


def _meminfo():
    out = {}
    try:
        for line in open("/proc/meminfo"):
            key, _, rest = line.partition(":")
            out[key] = int(rest.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def _running():
    res = {}
    try:
        txt = subprocess.run(["pgrep", "-af", PILOT_RE], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return res
    for line in txt.splitlines():
        parts = line.split()
        if "--sym-side" not in parts or parts.index("--sym-side") + 1 >= len(parts):
            continue
        symside = parts[parts.index("--sym-side") + 1]
        try:
            etime = int(open("/proc/%s/stat" % parts[0]).read().rsplit(")", 1)[1].split()[19])
            etime = int(time.time() - (_boot() + etime / os.sysconf("SC_CLK_TCK")))
        except Exception:
            etime = 0
        res[symside] = max(etime, res.get(symside, 0))
    return res


def _boot():
    for line in open("/proc/stat"):
        if line.startswith("btime"):
            return int(line.split()[1])
    return 0


def _live_drops(venue, now, tails):
    """Same active SQL set, same forward vector state, latest available NPZ bar."""
    if venue not in ("crypto", "stocks"):
        return []
    db = os.path.join(SB, "data", "hourly_reconfig", "per_sym_store.db")
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2)
        try:
            baseline = {ss: (updated, gain if gain is not None else acc) for ss, updated, gain, acc in con.execute("SELECT sym_side,updated_at,gain_30d,acc_gain_pct FROM per_sym_active")}
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []
    expected_crypto = (int(now) // 900 - 1) * 900
    rows = []
    for path in glob.glob(os.path.join(SB, "data", "forward_state", "*.json")):
        try:
            state = json.load(open(path))
            ss = str(state.get("sym_side") or os.path.basename(path)[:-5]).upper()
            symbol = ss.rsplit("_", 1)[0]
            crypto = symbol.endswith(("USDT", "USDC", "USD1"))
            if crypto != (venue == "crypto") or state.get("ok") is not True or state.get("window_days") != 30 or state.get("src") != "per_sym_store.active":
                continue
            active = baseline.get(ss)
            if not active or state.get("settings_updated_at") != active[0] or active[1] is None:
                continue
            tail = int(state.get("npz_tail") or 0)
            if venue == "crypto" and tail != expected_crypto:
                continue
            if venue == "stocks" and (now - tail > 5 * 86400 or tail < int(tails.get(symbol) or 0)):
                continue
            current, base = float(state["gain_pct"]), float(active[1])
            if not math.isfinite(current) or not math.isfinite(base) or base - current <= 1e-9:
                continue
            rows.append({"symside": ss, "drop_pp": round(base - current, 6), "baseline_gain_pct": base, "current_gain_pct": current, "settings_updated_at": active[0], "settings_sha256": state.get("settings_sha256"), "npz_tail": tail})
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            continue
    return sorted(rows, key=lambda row: (-row["drop_pp"], row["symside"]))


def _complete_fn(pdir):
    try:
        os.environ["V15_PROGRESS_DIR"] = pdir
        sys.path.insert(0, os.path.join(SB, "tools"))
        import v15_progress_board as board
        return board.is_complete
    except Exception:
        return None


def main():
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    syms = set(args.get("syms") or [])
    now = time.time()
    mem = _meminfo()
    try:
        st = os.statvfs(HOME)
        disk = st.f_bavail * st.f_frsize // (1 << 20)
    except OSError:
        disk = 0
    try:
        ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
    except Exception:
        ips = []
    try:
        pdir = open(os.path.join(HOME, "v15_current_progress_dir.txt")).readline().strip()
    except OSError:
        pdir = ""
    out = {"host": socket.gethostname().split(".")[0], "ips": ips, "ts": now,
           "mem_avail_mb": mem.get("MemAvailable", 0), "mem_total_mb": mem.get("MemTotal", 0),
           "swap_free_mb": mem.get("SwapFree", 0), "disk_free_mb": disk, "ncpu": os.cpu_count() or 0,
           "load1": os.getloadavg()[0], "pdir": pdir,
           "repo": os.path.isfile(os.path.join(SB, "v15_pilot.py")),
           "running": _running(), "progress": {}, "tails": {}, "launches": {}, "impossible": {}, "start_refused": {},
           "start_refused_why": {}, "snapshot_age_s": None, "live_drops": []}
    # 2026-10-28 maxsets: age of the Mac live per_sym snapshot the pilot validates at start (BASELINE-PERSYM-SNAPSHOT-INVALID
    # when older than V15_LIVE_SNAPSHOT_MAX_AGE_S); the dispatcher withholds launches it would refuse instead of burning attempts
    try:
        out["snapshot_age_s"] = int(now - float(json.load(open(os.path.join(SB, "data", "live_baseline_snapshots", "manifest.json")))["generated_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        cache = json.load(open(CACHE))
    except (OSError, ValueError):
        cache = {}
    new_cache = {}
    is_complete = None
    if pdir.startswith(os.path.join(HOME, "v15_run")) and os.path.isdir(pdir):
        for path in glob.glob(os.path.join(pdir, "*_v14_progress.json")):
            symside = os.path.basename(path)[:-len("_v14_progress.json")]
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = "%s|%d|%d" % (path, st.st_mtime_ns, st.st_size)
            rec = cache.get(key)
            if not isinstance(rec, dict):
                try:
                    data = json.load(open(path))
                except (OSError, ValueError):
                    continue
                npz_id = data.get("npz_id") if isinstance(data.get("npz_id"), dict) else {}
                rec = {"fg": data.get("final_gain") is not None,
                       "ts": int(float(data.get("npz_last_bar_ts") or npz_id.get("ts_last") or 0)),
                       "rows": len(data.get("done") or {}), "mtime": int(st.st_mtime),
                       "zero": bool(data.get("zero_trades_diagnostic")), "complete": None,
                       "verdict": str(data.get("verdict") or "")}
                if rec["fg"] or rec["zero"]:
                    if is_complete is None:
                        is_complete = _complete_fn(pdir) or False
                    if is_complete:
                        try:
                            rec["complete"] = bool(is_complete(symside))
                        except Exception:
                            rec["complete"] = None
            new_cache[key] = rec
            out["progress"][symside] = rec
    ind = os.path.join(SB, "backtest_v8", "indicators")
    for sym in syms:
        path = os.path.join(ind, sym + ".npz")
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = "%s|%d|%d" % (path, st.st_mtime_ns, st.st_size)
        tail = cache.get(key)
        if not isinstance(tail, int):
            try:
                import numpy as np
                ts = np.load(path, mmap_mode="r")["timestamps"]
                tail = int(float(ts[-1]) / (1000.0 if float(ts[-1]) > 1e11 else 1.0))
            except Exception:
                tail = 0
        new_cache[key] = tail
        out["tails"][sym] = tail
    out["live_drops"] = _live_drops(args.get("live_venue"), now, out["tails"])
    try:
        tmp = "%s.%d" % (CACHE, os.getpid())
        json.dump(new_cache, open(tmp, "w"))
        os.replace(tmp, CACHE)
    except OSError:
        pass
    for path in glob.glob(os.path.join(LAUNCH_DIR, "*.json")):
        try:
            out["launches"][os.path.basename(path)[:-5]] = json.load(open(path))
        except (OSError, ValueError):
            pass
    for symside, rec in out["launches"].items():
        log = "/tmp/sweep_%s_30D.log" % symside
        try:
            if now - float(rec.get("ts") or 0) > 7200 or os.path.getmtime(log) < float(rec.get("ts") or 0):
                continue
            with open(log, "rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 8192))
                tail = handle.read().decode("utf-8", "replace")
            # 2026-10-28: a tombstoned/finished board exits at start on [IMPOSSIBLE-SKIP]/[SOFT-SKIP]/[PROHIBITED]; S1 was
            # quarantined for hours by six such exits counted as deaths
            # also a normal end that leaves no final_gain: REDO scheduled after a compliance repair, the closing
            # "[spec-fill] workbook complete" line, or a stale-NPZ refusal (s5 quarantined 16:0xZ on AMD_LONG + SNOW_LONG)
            for marker in ("BASELINE-PERSYM-SNAPSHOT-INVALID", "[IMPOSSIBLE-SKIP]", "[SOFT-SKIP]", "[PROHIBITED]", "[STALE_NPZ_SKIPPED]", "[spec-fill] workbook complete", "[REDO] "):
                if marker in tail and not any(m in tail.rsplit(marker, 1)[1] for m in ("[spec-row]", "[PUBLISH]", "[slow-cell]")):
                    out["start_refused"][symside] = int(os.path.getmtime(log))
                    out["start_refused_why"][symside] = marker
                    break
        except (OSError, ValueError, TypeError):
            pass
    for path in glob.glob(os.path.join(SB, "SPREADSHEETS", "V15_V16_IMPOSSIBLE", "*_*")):
        name = os.path.basename(path)
        for side in ("_LONG_", "_SHORT_"):
            if side in name:
                symside = name.split(side)[0] + side[:-1]
                try:
                    out["impossible"][symside] = max(out["impossible"].get(symside, 0), int(os.path.getmtime(path)))
                except OSError:
                    pass
                break
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": "%s: %s" % (type(error).__name__, error)}))
