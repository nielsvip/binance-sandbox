#!/usr/bin/env python3
"""per_sym_minute_absorb — same-minute absorption of finished boards into live.

USER 2026-10-24: every final backtest result from ANY server must be absorbed
into the Mac live per_sym store the same minute it lands — no exceptions.
The pilot's immediate law (v15_pilot.py `_publish = True`, 2026-10-21) already
upserts the LOCAL store at campaign end on every host; this daemon bridges the
cross-host gap (worker finish -> Mac live) that the 3x/day published refresh
leaves open for up to ~10 h.

Design (per-sym scope ONLY — never touches defaults/templates/config):
- every minute, pull per_sym_active rows newer than the per-host watermark
  from S1/S5/S4 via a read-only python3-over-ssh query (no sqlite3 CLI there);
- apply a row only when its updated_at is NEWER than the Mac row (never
  regresses; applies go through the same per_sym_store.upsert the pilot uses,
  which writes SQLite primary + JSON backup + history + memo invalidate);
- tier write-through + promote flag, mirroring the pilot LIVE-PUT tail.
- --audit: one-shot coverage report over the tradeable universe.

Single-flight via cron + v15_lock_run.sh. Never raises out (fail-open).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

HOSTS = ("s1-pub", "s5", "10.0.0.4", "10.0.0.6")
REMOTE_DB = "/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_store.db"
WM_PATH = ROOT / "data" / "per_sym_absorb_watermark.json"
AUDIT_PATH = ROOT / "data" / "reports" / "per_sym_absorb_audit.json"
FLAG_PATH = Path("/tmp/per_sym_live_promoted.flag")
SSH = (
    "ssh",
    "-S",
    "none",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=accept-new",
)
FULL_COLS = (
    "sym_side,updated_at,winning_tag,campaign_ts,overrides_json,"
    "defaults_snapshot_json,full_config_json,template_md5,defaults_round,meta_json"
)
REMOTE_PY = (
    "import sqlite3,json,sys;"
    "con=sqlite3.connect('file:" + REMOTE_DB + "?mode=ro',uri=True,timeout=20);"
    "mode=sys.argv[1];"
    'rows=(con.execute("SELECT '
    + FULL_COLS
    + ' FROM per_sym_active WHERE sym_side IN (%s)"%\',\'.join(\'?\'*max(1,len(sys.argv[2:]))) ,tuple(sys.argv[2:])) if mode=="get" else con.execute("SELECT sym_side,MAX(updated_at) FROM per_sym_active GROUP BY sym_side"));'
    "print(json.dumps([list(r) for r in rows],default=str))"
)
MAX_ROWS_PER_HOST = 25


def log(msg):
    print(
        f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] [absorb] {msg}",
        flush=True,
    )


def load_wm():
    try:
        d = json.loads(WM_PATH.read_text())
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {h: "2000-01-01T00:00:00Z" for h in HOSTS}


def save_wm(wm):
    tmp = WM_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(wm, indent=2))
    tmp.replace(WM_PATH)


def remote_rows(host, mode, *args):
    try:
        r = subprocess.run(
            (*SSH, host, "python3", "-", mode, *args),
            input=REMOTE_PY,
            capture_output=True,
            text=True,
            timeout=40,
        )
        if r.returncode != 0:
            log(f"{host} query failed: {r.stderr.strip()[:160]}")
            return None
        return json.loads(r.stdout or "[]")
    except Exception as e:
        log(f"{host} query error: {str(e)[:160]}")
        return None


def mac_row(sym_side):
    import per_sym_store as pss

    con = pss._connect()
    r = con.execute(
        "SELECT updated_at, winning_tag FROM per_sym_active WHERE sym_side=?",
        (sym_side,),
    ).fetchone()
    return (r[0], r[1]) if r else (None, None)


def absorb():
    import per_sym_store as pss

    wm = load_wm()
    applied, skipped, errors = 0, 0, 0
    mac_all = {
        r[0]: r[1]
        for r in pss._connect().execute(
            "SELECT sym_side, updated_at FROM per_sym_active"
        )
    }
    for host in HOSTS:
        maxima = remote_rows(host, "max")
        if maxima is None:
            errors += 1
            continue
        need = []
        for ss, mx in maxima:
            if not mx:
                continue
            mac_ts = mac_all.get(ss)
            if mac_ts is None or mx > mac_ts:
                need.append(ss)
            if len(need) >= MAX_ROWS_PER_HOST:
                break
        if not need:
            if maxima:
                wm[host] = max(wm.get(host, ""), max(mx for _, mx in maxima if mx))
            continue
        rows = remote_rows(host, "get", *need)
        if rows is None:
            errors += 1
            continue
        for ss, ts, _tag, _cts, ov_j, snap_j, full_j, tmd5, dround, meta_j in rows:
            wm[host] = max(wm.get(host, ""), ts or "")
            try:
                mac_ts = mac_all.get(ss)
                if mac_ts and (ts or "") <= mac_ts:
                    skipped += 1
                    continue
                ov = json.loads(ov_j or "{}")
                snap = json.loads(snap_j or "{}") or None
                full = json.loads(full_j or "{}") or None
                meta = json.loads(meta_j or "{}")
                meta.setdefault("absorbed_from", host)
                meta.setdefault(
                    "absorbed_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                )
                pss.upsert(
                    ss,
                    ov,
                    snap,
                    full,
                    meta,
                    template_md5=tmd5 or None,
                    defaults_round=dround or None,
                )
                try:
                    from tools.v15_persym_size_tiers import upsert_workbook_tier as wbt

                    wbt(
                        ss,
                        meta.get("gain_30d", meta.get("acc_gain_pct", 0)),
                        g365=meta.get("acc_gain_pct"),
                        trades_30d=None,
                        tim_30d=None,
                    )
                except Exception as te:
                    log(f"{ss} tier warn: {str(te)[:120]}")
                try:
                    FLAG_PATH.write_text(f"{ss} {meta['absorbed_at']} absorbed:{host}")
                except Exception:
                    pass
                if not ss.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")):
                    try:
                        tp = ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json"
                        tp.parent.mkdir(parents=True, exist_ok=True)
                        tall = json.loads(tp.read_text()) if tp.exists() else {}
                        tmeta = tall.pop("_meta", None) if isinstance(tall, dict) else None
                        entry = dict(meta)
                        entry["overrides"] = ov
                        tall[ss] = entry
                        if tmeta is not None:
                            tall["_meta"] = tmeta
                        ttmp = tp.with_suffix(".json.tmp")
                        ttmp.write_text(json.dumps(tall, indent=2, default=str))
                        ttmp.replace(tp)
                    except Exception as te:
                        log(f"{ss} trb mirror warn: {str(te)[:120]}")
                applied += 1
                mac_all[ss] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                log(f"ABSORBED {ss} from {host} src={ts} mac_had={mac_ts}")
            except Exception as e:
                errors += 1
                log(f"{ss} apply error: {str(e)[:200]}")
    save_wm(wm)
    log(f"pass done applied={applied} skipped={skipped} errors={errors}")
    return applied, skipped, errors


def audit():
    import per_sym_store as pss

    uni = set()
    try:
        for k in json.loads(Path("tradeable_keys.json").read_text()):
            uni.add(k.split(":", 1)[-1])
    except Exception as e:
        log(f"universe read warn: {e}")
    con = pss._connect()
    mac_live = {
        r[0]: r[1]
        for r in con.execute("SELECT sym_side, updated_at FROM per_sym_active")
    }
    mac_hist = {}
    for ss, mx in con.execute(
        "SELECT sym_side, MAX(updated_at) FROM per_sym_history GROUP BY sym_side"
    ):
        mac_hist[ss] = mx
    uni |= set(mac_live)
    src_max = {}
    for host in HOSTS:
        rows = remote_rows(host, "max", "")
        if not rows:
            continue
        for ss, mx in rows:
            if mx and (ss not in src_max or mx > src_max[ss][0]):
                src_max[ss] = (mx, host)
    out = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_universe": 0,
        "n_current": 0,
        "behind": [],
        "missing": [],
        "no_board": [],
    }
    out["n_universe"] = len(uni)
    for ss in sorted(uni):
        live = mac_live.get(ss)
        latest, whence = live, "mac-live"
        if mac_hist.get(ss) and (latest is None or mac_hist[ss] > latest):
            latest, whence = mac_hist[ss], "mac-history"
        if ss in src_max and (latest is None or src_max[ss][0] > latest):
            latest, whence = src_max[ss][0], f"worker:{src_max[ss][1]}"
        if live is None and latest is None:
            out["no_board"].append(ss)
        elif live is None:
            out["missing"].append({"sym_side": ss, "board": latest, "at": whence})
        elif latest and latest > live:
            out["behind"].append(
                {"sym_side": ss, "live": live, "board": latest, "at": whence}
            )
        else:
            out["n_current"] += 1
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUDIT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(AUDIT_PATH)
    log(
        f"AUDIT universe={out['n_universe']} current={out['n_current']} "
        f"behind={len(out['behind'])} missing={len(out['missing'])} no_board={len(out['no_board'])}"
    )
    for b in out["behind"][:15]:
        log(f"BEHIND {b['sym_side']} live={b['live']} board={b['board']} at={b['at']}")
    for m in out["missing"][:15]:
        log(f"MISSING {m['sym_side']} board={m['board']} at={m['at']}")
    return out


if __name__ == "__main__":
    if "--audit" in sys.argv[1:]:
        audit()
    else:
        absorb()
