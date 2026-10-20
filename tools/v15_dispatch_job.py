#!/usr/bin/env python3
"""Detached dispatch job (USER 2026-10-13: "the npz goes WITH THE PUSH of the sym_side, calculated on s1 before dispatching").
The fleet scheduler tick has a hard 110 s cron timeout; a synchronous NPZ rebuild (minutes per stock symbol once the US
market is open) killed every tick from 13:44Z and the fleet launched nothing. The tick now enqueues this job instead:
  1. NPZ rebuild + md5-verified push on S1 (per-symbol single flight + V15_NPZ_PREP_SLOTS concurrent builds, default 8),
  2. ssh launch of the pilot(s) on the target host — ONLY after step 1 (JIT law: NPZ fresh right before load).
ok=False from the prep tool = sym_side must not launch (stale tail it could not heal) -> skip, the scheduler retries later.
Prep tool crash / lock wait timeout = launch with what the host has (USER 2026-10-13: "work with what you have").
Every outcome is one JSON line in /tmp/v15_dispatch.log."""
import argparse, datetime, fcntl, hashlib, json, os, pathlib, re, signal, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = pathlib.Path(os.environ.get("V15_DISPATCH_LOG", "/tmp/v15_dispatch.log"))
NPZ_FAIL_LEDGER = pathlib.Path(os.environ.get("V15_NPZ_FAIL_LEDGER", str(ROOT / "data" / "v15_npz_failures.jsonl")))


def _npz_identity(symbol, npz_path=None):
    """{sha, bars, sessions, last_bar} for an NPZ. Session count mirrors the pilot/engine gate
    (tools/opt/evaluate_v12.py: distinct UTC calendar days over finite timestamps for stocks).
    Never raises — missing/unreadable fields stay None (capture must not break a dispatch)."""
    p = pathlib.Path(npz_path) if npz_path else ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
    rec = {"symbol": symbol, "npz": str(p), "sha": None, "bars": None, "sessions": None, "last_bar": None}
    try:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        rec["sha"] = h.hexdigest()
    except OSError:
        return rec
    try:
        import numpy as np
        z = np.load(p, allow_pickle=False)
        ts = np.asarray(z["timestamps"]).ravel()
        rec["bars"] = int(ts.size)
        raw = ts[np.isfinite(ts)]
        if raw.size:
            secs = raw / (1000.0 if float(raw[-1]) > 1e11 else 1.0)
            rec["last_bar"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(secs[-1])))
            if not str(symbol).upper().endswith(("USDT", "USDC")):
                rec["sessions"] = int(len(np.unique(secs.astype("datetime64[s]").astype("datetime64[D]"))))
    except Exception:
        pass
    return rec


def _ledger_npz(rec):
    """USER 2026-10-15 (06.2): NPZ failure/push ledger — CAPTURE ONLY, never gates a launch.
    Every pushed NPZ and every reported pilot NPZ failure lands here as one JSON line
    {ts, event, symbol, sha, bars, sessions, last_bar, ...} so the short-stock-NPZ gate
    (pending 06 item 2) can later join failures to exact NPZ identities."""
    try:
        lp = pathlib.Path(os.environ.get("V15_NPZ_FAIL_LEDGER", str(NPZ_FAIL_LEDGER)))
        with open(lp, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rec}) + "\n")
    except OSError:
        pass


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def _acquire_slot(n_slots, wait_s):
    """Counting semaphore over flock files: at most n_slots NPZ builds at once (a stock build is ~20 single-core
    minutes; the old single global lock queued every dispatch of the fleet behind one build -> idle fleet)."""
    deadline = time.time() + wait_s
    while True:
        for i in range(max(1, n_slots)):
            f = open(f"/tmp/v15_npz_prep.slot{i}.lock", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return f
            except OSError:
                f.close()
        if time.time() >= deadline:
            return None
        time.sleep(5)


def prep(symbol, push):
    """Per-symbol single flight (same symbol never built twice at once) + fleet-wide build slots."""
    t0 = time.time()
    sym_lock = open(f"/tmp/v15_npz_prep.{symbol.upper()}.lock", "w")
    wait_s = float(os.environ.get("V15_NPZ_LOCK_WAIT_S", "1500"))
    try:
        fcntl.flock(sym_lock, fcntl.LOCK_EX)
    except OSError:
        pass
    slot = _acquire_slot(int(os.environ.get("V15_NPZ_PREP_SLOTS", "8")), wait_s)
    if slot is None:
        return {"ok": None, "reason": "slot-wait-timeout", "prep_s": round(time.time() - t0, 1)}
    cmd = [sys.executable, str(ROOT / "tools" / "v15_npz_prep.py"), "--symbol", symbol]
    if push:
        cmd += ["--push", push]
    # USER 2026-10-19: subprocess.run's timeout kills only the DIRECT child, so v15_npz_prep's multiprocessing workers
    # survived as orphans — two of them (VALE, RBLX) spun at 98% CPU for 10,664 s, nearly 3x the 2400 s timeout, taking
    # two of S1's 16 cores and leaving the host running ONE board at load 24.5. The prep now runs in its own session
    # and a timeout kills the whole process group, so an over-running rebuild can never choke the fleet again.
    _to = float(os.environ.get("V15_NPZ_PREP_TIMEOUT_S", "2400"))
    try:
        _p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT), start_new_session=True)
        try:
            _out, _err = _p.communicate(timeout=_to)
            lines = [l for l in (_out or "").strip().splitlines() if l.strip()]
            res = json.loads(lines[-1]) if lines else {"ok": None, "reason": f"no-output rc={_p.returncode} {(_err or '')[-160:]}"}
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(_p.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                _p.kill()
            _p.wait(timeout=30)
            res = {"ok": None, "reason": f"prep-timeout {_to:.0f}s (process group killed)"}
    except subprocess.TimeoutExpired:
        res = {"ok": None, "reason": "prep-timeout"}
    except (OSError, ValueError) as e:
        res = {"ok": None, "reason": f"prep-error {e}"[:120]}
    finally:
        slot.close()
        sym_lock.close()
    res["prep_s"] = round(time.time() - t0, 1)
    return res


PREP_NO_REBUILD_ACTIONS = ("frozen", "regen_failed", "regen_blocked", "stale-npz")  # stale-npz (USER LAW 2026-10-20): the source has no newer bar yet; a rebuild cannot make one, next tick retries
PREP_ALERT_ACTIONS = ("regen_failed", "regen_blocked")


def prep_disposition(pr):
    """Pure. THE single meaning of a prep verdict, owned by the module that produces it (prep() above).

    USER 2026-10-15: a verdict of None must NEVER launch. Measured on s1 /tmp/v15_dispatch.log (966 records):
    889 launches carried prep.ok=True (97.8%, the accompanying-NPZ law working), but 20 (2.2%) launched with
    ok=None under the old `if res.get("ok") is False` gate plus the "work with what you have" rule in the
    header — slot-wait-timeout (XOM, AGCO), regen_blocked by the bar-loss-overwrite guard (CLS 24181 bars,
    AG 2017), and prep crashes `no-output rc=-15` (CVX, WDAY: the prep itself was SIGTERMed).

    -> (launch_ok, kind, reason, may_rebuild), kind in ('ok', 'block', 'retry', 'alert'):
      ok     launch
      block  ok=False for a reason a rebuild may fix -> defer, rebuild once per cooldown
      retry  transient (frozen / push failed / lock / slot wait / timeout / reaped prep) -> next tick, NO rebuild
      alert  regen_failed / regen_blocked -> surface it; a rebuild cannot fix a data problem, never spin
    `pr is None` is retry, never a pass. tools/v15_npz_prep.py's `action` is the discriminator (it returns
    ok=None for frozen, push-failed, regen_failed<48h and regen_blocked<48h); the reason text is a fallback."""
    if pr is None:
        return False, "retry", "prep did not run (no output / crashed)", False
    ok = pr.get("ok")
    action = str(pr.get("action") or "")
    reason = str(pr.get("reason") or "") or action or "no reason given"
    if action in PREP_ALERT_ACTIONS:
        return False, "alert", reason, False
    if ok is True:
        return True, "ok", reason, False
    if action in PREP_NO_REBUILD_ACTIONS or "push failed" in reason.lower():
        return False, "retry", reason, False
    if ok is False:
        return False, "block", reason, True
    return False, "retry", reason, False


NY_TZ = "America/New_York"


def us_rth(now):
    """True inside US regular trading hours (09:30-16:00 ET, weekdays). Clock injected by the caller."""
    import zoneinfo

    t = now.astimezone(zoneinfo.ZoneInfo(NY_TZ))
    return t.weekday() < 5 and datetime.time(9, 30) <= t.time() < datetime.time(16, 0)


def npz_tail_limit_h(symbol, now=None, rth=None):
    """Stocks freshness bar, SESSION-RELATIVE (USER 2026-10-15: "run stocks on the latest market hour bar npz",
    and "no new klines are produced after 20:00utc" — so a post-close NPZ rebuilt to the session's final bar IS
    current and must not be called stale).

    An absolute hour count is wrong across weekends and holidays: Saturday 03:00 UTC with a Friday 20:00 last bar
    is 31 h old and perfectly current, yet a fixed 20 h limit would block it. So the cutoff comes from
    tools/npz_cutoffs.last_rth_close_bar — the same authority tail_currency() uses, which already handles live RTH
    (latest closed 15m bar), post-close (today's 16:00 ET) and weekend/holiday (the previous session's close) —
    and is then expressed as the `max_tail_age_h` that tools/v15_npz_preflight.verify already accepts. No change
    to that module is needed.

    -> hours, or None to use the module's own limit (crypto always: its 1.5 h rule is already bar-accurate).
    Pure (clock injected). V15_SCHED_STOCK_TAIL_GRACE_H (default 0.5 = 2 bars) absorbs builder/push lag;
    V15_SCHED_STOCK_TAIL_H forces a fixed hour count if the session rule ever has to be bypassed."""
    sym = str(symbol or "").upper()
    if sym.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")):
        return None
    forced = os.environ.get("V15_SCHED_STOCK_TAIL_H")
    if forced:
        try:
            return round(float(forced), 2)
        except (TypeError, ValueError):
            pass
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.npz_cutoffs import last_rth_close_bar
        cutoff = float(last_rth_close_bar(now))
    except Exception:
        return None  # no calendar -> fall back to the module's own limit rather than inventing one
    if not cutoff:
        return None
    try:
        grace = float(os.environ.get("V15_SCHED_STOCK_TAIL_GRACE_H", "0.5") or 0.5)
    except (TypeError, ValueError):
        grace = 0.5
    return round(max(0.5, (now.timestamp() - cutoff) / 3600.0 + grace), 2)


def npz_preflight(symbol, window_days=30, check_freshness=True):
    """Verify the NPZ that will ACCOMPANY this task — on S1, the source the push copies from — AFTER the prep
    and BEFORE the pilot is launched. The prep's own ok=True is not sufficient: measured
    `v15_npz_prep.py --symbol ATOMUSDT --dry-run` returned ok=true/action=would_regen while reporting a tail
    24.0 bars (6 h) behind the latest closed 15m bar.
    -> (ok, reason, res). A missing module fails OPEN (logged) so a dependency that has not been pushed yet
    cannot stop the fleet; a real verdict is authoritative and fails CLOSED."""
    if os.environ.get("V15_SCHED_NPZ_PREFLIGHT", "1") != "1":
        return True, "preflight-off", {}
    try:
        if str(ROOT / "tools") not in sys.path:
            sys.path.insert(0, str(ROOT / "tools"))
        import v15_npz_preflight as _pf
    except Exception as e:
        return True, f"preflight-unavailable ({type(e).__name__})", {}
    try:
        res = _pf.verify(symbol, window_days=window_days, check_freshness=check_freshness,
                         max_tail_age_h=(npz_tail_limit_h(symbol) if check_freshness else None))
    except Exception as e:
        return True, f"preflight-crash ({type(e).__name__})", {}
    try:
        if not res.get("ok"):
            _pf.record_deferral(res)
    except Exception:
        pass
    reasons = [str(x) for x in (res.get("reasons") or [])]
    ok = bool(res.get("ok"))
    # A STALE TAIL IS NOT A VETO WHILE THE FLEET IS CONFIGURED TO RUN STALE.
    # Measured 2026-10-15 04:3x-05:0xZ: enforcing freshness here blocked EVERY crypto dispatch (ENAUSDC,
    # FILUSDC, SNXUSDT, SOLUSDC, COTIUSDT, DUSKUSDT, JASMYUSDT — tails 7.1-8.7 h after the job's own refresh)
    # and 0 crypto boards launched. The scheduler's own gate already decides this question:
    # V15_SCHED_RUN_STALE=1 (was the default; USER LAW 2026-10-20 made 0 the default: stale never launches) means "run on older bars rather than idle the fleet", and that is a
    # fleet policy, not this gate's call. So: older bars -> record the deferral (the rebuild still gets asked
    # for) and launch; a STRUCTURAL defect or a thin/holed build -> always block, because that is the silent
    # 0-trades producer the user asked to stop. V15_SCHED_RUN_STALE=0 restores a hard freshness veto.
    if not ok and reasons and all(r.startswith("stale tail:") for r in reasons):
        if os.environ.get("V15_SCHED_RUN_STALE", "0") == "1":
            return True, "DEGRADED-STALE-TAIL(allowed by V15_SCHED_RUN_STALE=1): " + "; ".join(reasons)[:220], res
    return ok, "; ".join(reasons)[:300], res


def _dedup_lock(tag):
    """One live job per dispatch tag (a dead tick that never saved dispatch_q re-dispatched the same tag every 2 min)."""
    f = open("/tmp/v15_dispatch_job." + "".join(c if c.isalnum() else "_" for c in tag) + ".lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def _all_30d(tag):
    legs = tag.split(":", 2)[-1].split("+") if tag.count(":") >= 2 else []
    return bool(legs) and all(l[1:].startswith("30D") for l in legs)


def tail_refresh(symbol):
    """USER 2026-10-13: "the new test ALWAYS runs on the latest npz, never on old info". Append only the missing closed
    15m bars to S1's NPZ with tools/npz_bar_refresh.py (builder's own indicator code, guard + atomic replace; ~9 s per
    stock symbol) instead of a full-history rebuild. A frozen symbol (unfinished board / running pilot) is never touched."""
    try:
        sys.path.insert(0, str(ROOT))
        from tools.v15_npz_freeze import is_frozen
        if is_frozen(symbol):
            return {"tail": "frozen"}
    except ImportError:
        pass
    mode = "crypto" if symbol.endswith(("USDT", "USDC")) else "stocks"
    t0 = time.time()
    try:
        r = subprocess.run(["/home/niels/.conda/envs/binance_env_mac/bin/python", str(ROOT / "tools" / "npz_bar_refresh.py"), "--symbols", symbol, "--mode", mode, "--w15", "2500", "--workers", "2", "--no-sentiment", "--no-fng"], capture_output=True, text=True, timeout=float(os.environ.get("V15_TAIL_REFRESH_TIMEOUT_S", "300")), cwd=str(ROOT))
        st = "INSTALLED" if '"status": "INSTALLED"' in (r.stdout or "") else ("current" if r.returncode == 0 else f"rc={r.returncode}")
        if "locked" in (r.stdout or ""):
            st = "LOCK_BLOCKED"
    except subprocess.TimeoutExpired:
        st = "timeout"
    # 2026-10-16: "current" used to be inferred from rc=0 alone. The 24/7 --loop held the one global lock, so EVERY
    # top-up printed "locked (previous run active)", exited 0, and was logged as `tail current (0.2s)` before the
    # stale NPZ was pushed to the worker and md5-"verified". The tail is now judged by the NPZ itself.
    out = {"tail": st, "tail_s": round(time.time() - t0, 1)}
    try:
        from tools.npz_bar_refresh import npz_last_ts
        from tools.npz_cutoffs import BAR, last_closed_bar, last_closed_stock_bar
        last = npz_last_ts(str(ROOT / "backtest_v8" / "indicators"), symbol)
        cut = last_closed_bar() if mode == "crypto" else last_closed_stock_bar()
        out["tail_last_ts"] = last
        out["tail_bars_behind"] = None if last is None else int((int(cut) - int(last)) // BAR)
        if out["tail_bars_behind"] is not None and out["tail_bars_behind"] > int(os.environ.get("V15_TAIL_MAX_BARS_BEHIND", "1")) and st != "INSTALLED":
            out["tail"] = f"STALE_{out['tail_bars_behind']}_BARS({st})"
    except Exception as e:
        out["tail_verify_err"] = f"{type(e).__name__}: {e}"[:120]
    return out


def verify_launch_tail(symbol, tr):
    """USER LAW 2026-10-20 "all new calculations calculate up to the latest kline bar, max 15m old in the npz": the JIT tail refresh is
    mandatory and its RESULT is verified by the NPZ itself (not rc, not mtime). Returns (ok, info) with npz_last_bar_utc / npz_age_min
    (minutes behind the latest closed bar, same definition as v15_pilot.npz_age_stamp). Not ok -> the caller skips the launch and counts it
    with this tail age. V15_SCHED_RUN_STALE=1 or a frozen symbol (an unfinished board keeps its own NPZ) is the only way past a stale verdict."""
    info = {}
    try:
        sys.path.insert(0, str(ROOT))
        from tools.npz_bar_refresh import npz_last_ts
        from tools.npz_cutoffs import MAX_NPZ_AGE_MIN, npz_age_min
        mode = "crypto" if symbol.endswith(("USDT", "USDC")) else "stocks"
        last = npz_last_ts(str(ROOT / "backtest_v8" / "indicators"), symbol)
        if last is None:
            return True, {"npz_last_bar_utc": None, "npz_age_min": None, "verify": "no-npz-ts"}
        age = npz_age_min(last, mode)
        info = {"npz_last_bar_ts": int(last), "npz_last_bar_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(last))), "npz_age_min": age, "limit_min": MAX_NPZ_AGE_MIN}
        if age is not None and age > MAX_NPZ_AGE_MIN and os.environ.get("V15_SCHED_RUN_STALE", "0") != "1":
            return False, info
    except Exception as e:
        return True, {"verify": f"err {type(e).__name__}: {e}"[:120]}
    return True, info


def fast_push(symbol, push):
    """S1 NPZ exists -> tail-refresh it to the latest closed bar, push it (rsync, md5 verify) and launch; None -> fall back to the builder (no NPZ on S1)."""
    live = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
    if not live.exists() and os.environ.get("V15_NPZ_PEER_SEED", "1") != "0":
        # 2026-10-14: S1 had lost ~390 NPZs that s2 still holds; seed from the peer and tail-refresh (seconds) instead of a 40-min full build
        seed = live.with_name(f".{symbol}.npz.peerseed")
        for peer in [h for h in os.environ.get("V15_NPZ_PEERS", "10.0.0.4").split(",") if h]:
            try:
                rc = subprocess.run(["rsync", "-a", "--timeout=60", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=8", f"{peer}:binance-sandbox/backtest_v8/indicators/{symbol}.npz", str(seed)], capture_output=True, timeout=180).returncode
                if rc == 0 and seed.exists() and seed.stat().st_size > 100000:
                    os.replace(seed, live)
                    break
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                seed.unlink(missing_ok=True)
    if not live.exists():
        return None
    tr = tail_refresh(symbol)  # USER LAW 2026-10-20: mandatory (V15_DISPATCH_TAIL_REFRESH=0 no longer skips it)
    from tools.npz_cutoffs import last_closed_bar as _lcb_w
    _wait_end = min(time.time() + float(os.environ.get("V15_TAIL_WAIT_S", "240")), _lcb_w() + 900 + 600)  # wait only while the newest bar can still be propagating (<= 10 min after its close)
    while tr.get("tail") != "frozen" and not verify_launch_tail(symbol, tr)[0] and time.time() < _wait_end:
        time.sleep(30)  # the newest closed bar is still propagating Mac -> S1; re-ask the source, re-install, re-verify
        tr = tail_refresh(symbol)
    if tr.get("tail") == "frozen":
        # a board on this symbol is unfinished/running: its host keeps the NPZ it started on (never push a different one)
        _fv = verify_launch_tail(symbol, tr)[1]
        log({"event": "tail-verified", "symbol": symbol, "ok": None, "tail": "frozen", **_fv})
        return {"ok": True, "action": "frozen-keep", "reason": "frozen symbol: target keeps its board's NPZ", "prep_s": 0.0, "npz_last_bar_utc": _fv.get("npz_last_bar_utc"), "npz_age_min": _fv.get("npz_age_min")}
    if not symbol.endswith(("USDT", "USDC")):
        # 2026-10-14 (BIBLE §109): never ship a short/holed stock NPZ to a 30D board (alert data/alerts/stock_npz_gap_<SYM>.json)
        try:
            sys.path.insert(0, str(ROOT))
            from tools.stock_npz_gap_guard import gate_npz
            gap_ok, gap_info = gate_npz(symbol, live, "v15_dispatch_job.fast_push")
        except ImportError as exc:
            gap_ok, gap_info = True, {"reason": f"guard unavailable: {exc}"}
        if not gap_ok:
            log({"event": "stock-npz-gap-refused", "symbol": symbol, "gap": gap_info})
            _ledger_npz({"event": "gap-refused", **_npz_identity(symbol), "reason": str(gap_info.get("reason"))[:160]})
            return {"ok": False, "action": "gap-refused", "reason": f"STOCK_NPZ_GAP {gap_info.get('reason')}", "prep_s": 0.0}
    _tv_ok, _tv = verify_launch_tail(symbol, tr)
    log({"event": "tail-verified", "symbol": symbol, "ok": _tv_ok, "tail": tr.get("tail"), "tail_s": tr.get("tail_s"), **_tv})
    if not _tv_ok:
        _ledger_npz({"event": "stale-refused", **_npz_identity(symbol), "npz_age_min": _tv.get("npz_age_min")})
        return {"ok": False, "action": "stale-npz", "reason": f"STALE_NPZ {symbol} last bar {_tv.get('npz_last_bar_utc')} is {_tv.get('npz_age_min')} min behind the latest closed bar (> {_tv.get('limit_min')} min) after the JIT tail refresh ({tr.get('tail')})", "prep_s": tr.get("tail_s", 0.0), **{k: _tv.get(k) for k in ("npz_last_bar_utc", "npz_age_min")}}
    t0 = time.time()
    if not push:
        _ledger_npz({"event": "push", "action": "fast30d-local", **_npz_identity(symbol), "tail": tr.get("tail")})
        return {"ok": True, "action": "fast30d-local", "reason": f"S1 NPZ tail {tr.get('tail')}", "prep_s": tr.get("tail_s", 0.0), "npz_last_bar_utc": _tv.get("npz_last_bar_utc"), "npz_age_min": _tv.get("npz_age_min")}
    h = hashlib.md5(live.read_bytes()).hexdigest()
    dst = f"{push}:binance-sandbox/backtest_v8/indicators/{symbol}.npz"
    try:
        subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new", str(live), dst], timeout=300, check=True)
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", push, f"md5sum binance-sandbox/backtest_v8/indicators/{symbol}.npz"], capture_output=True, text=True, timeout=60)
        ok = r.stdout.split()[0] == h if r.stdout else False
    except (subprocess.SubprocessError, OSError, IndexError):
        ok = False
    _ledger_npz({"event": "push", "action": "fast30d-push", **_npz_identity(symbol), "verified": bool(ok)})
    return {"ok": True if ok else None, "action": "fast30d-push", "reason": f"tail {tr.get('tail')} ({tr.get('tail_s')}s); pushed S1 NPZ md5 {h[:8]} verified={ok}", "prep_s": round(time.time() - t0, 1), "npz_last_bar_utc": _tv.get("npz_last_bar_utc"), "npz_age_min": _tv.get("npz_age_min")}


def ship_prev_best(symbol, tag, push):
    """USER 2026-10-13: the first baseline is ALWAYS the best of last round's best settings and the cat_side defaults.
    Write the top distinct published sets of every 30D side in this dispatch from S1's per_sym_history (orchestrator
    publish history) to ~/v15_prev_best/{ss}.json and push them with the job; the pilot re-measures them at baseline."""
    import sqlite3
    db = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
    out_dir = pathlib.Path(os.path.expanduser("~/v15_prev_best"))
    out_dir.mkdir(parents=True, exist_ok=True)
    sides = [("LONG" if l[0] == "L" else "SHORT") for l in tag.split(":", 2)[-1].split("+") if l[1:].startswith("30D")]
    files = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        for side in sides:
            ss = f"{symbol}_{side}"
            rows = con.execute("select gain_pct, overrides_json, updated_at from per_sym_history where sym_side=? and overrides_json is not null and gain_pct is not null order by gain_pct desc limit 40", (ss,)).fetchall()
            sets, seen = [], set()
            for g, oj, ts in rows:
                try:
                    ov = json.loads(oj)
                except ValueError:
                    continue
                if not isinstance(ov, dict) or len(ov) < 5:
                    continue
                key = json.dumps(ov, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                sets.append({"gain_recorded": g, "updated_at": ts, "overrides": ov})
                if len(sets) >= 5:
                    break
            if sets:
                f = out_dir / f"{ss}.json"
                f.write_text(json.dumps({"sym_side": ss, "sets": sets, "source": "per_sym_history", "written_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
                files.append(str(f))
        con.close()
    except Exception as e:
        log({"event": "prev_best_warn", "symbol": symbol, "err": str(e)[:160]})
        return 0
    if push and files:
        try:
            subprocess.run(["ssh", "-o", "BatchMode=yes", push, "mkdir -p ~/v15_prev_best"], timeout=30)
            subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15"] + files + [f"{push}:v15_prev_best/"], timeout=120)
        except subprocess.SubprocessError:
            pass
    return len(files)


AUTOTUNE_KNOB_RE = re.compile(r"^V15_V17_(COMBO_SHARE(_WINNER)?|SHARE_(SURGEON|SURGEON_WINNER|FOCUS|ABLATION|RETEST)|P3_MAX_S_(WINNER|MEDIOCRE|LOSER|LOST_CAUSE)|P_MIN)$")
AUTOTUNE_BOUNDS = {"SHARE": (0.05, 0.95), "P3_MAX_S": (30.0, 3600.0), "P_MIN": (0.001, 0.05)}


def _autotune_exports(tag, path=None):
    """V17 KPI auto-tuner reader hook (BIBLE "V17 KPI + AUTO-TUNE"): `export K=V; ` prefix for the remote launch command from
    data/kpi/v17_autotune.json (tools/v17_kpi.py --autotune). Only whitelisted pinpoint knobs, finite numbers inside hard
    bounds, apply=true, file <= V17_AUTOTUNE_MAX_AGE_D (3) days old, a 30D leg in the tag; V17_AUTOTUNE_APPLY=0 = off. Never
    the 0.3 rule (V15_V17_MIN_LIFT_PM) or the absolute cap (V15_V17_P3_MAX_S). An inline VAR=… in the scheduler's own env
    string still wins (it is assigned after the export). Any problem -> '' (pilot defaults)."""
    if os.environ.get("V17_AUTOTUNE_APPLY", "1") == "0" or (tag and ":" in tag and "30D" not in tag):
        return "", None
    if os.environ.get("V15_SCHED_GATE_ENV", "1") != "0" and (ROOT / "data" / "v15_gate_env.json").exists():
        # USER 2026-10-16: autotune values never reach production unbenched — once the gate publishes a launch env, autotune
        # output goes in as a gate candidate (v15_version_gate.py submit --env K=V), not as a dispatch export.
        return "", {"skipped": "gate env authoritative (data/v15_gate_env.json)"}
    p = pathlib.Path(path or (ROOT / "data" / "kpi" / "v17_autotune.json"))
    try:
        doc = json.loads(p.read_text())
        if not doc.get("apply") or time.time() - p.stat().st_mtime > float(os.environ.get("V17_AUTOTUNE_MAX_AGE_D", "3")) * 86400:
            return "", None
        out = {}
        for k, v in (doc.get("env") or {}).items():
            if not AUTOTUNE_KNOB_RE.match(str(k)) or isinstance(v, bool):
                continue
            x = float(v)
            lo, hi = next(b for key, b in AUTOTUNE_BOUNDS.items() if key in k)
            if x == x and lo <= x <= hi:
                out[k] = round(x, 6)
    except (OSError, ValueError, TypeError, StopIteration):
        return "", None
    if not out:
        return "", None
    return "".join(f"export {k}={v}; " for k, v in sorted(out.items())), {"generated_utc": doc.get("generated_utc"), "n": len(out)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--push", default="", help="target ssh host for the md5-verified NPZ push ('' = local host)")
    ap.add_argument("--via", default="", help="ssh target that runs the launch command")
    ap.add_argument("--cmd-file", default="", help="file holding the remote launch shell command (deleted after use)")
    ap.add_argument("--tag", default="")
    ap.add_argument("--npz-failure", action="store_true", help="USER 2026-10-15 (06.2): record a pilot NPZ failure {npz sha, session count, timestamp} to the failure ledger and exit 0. CAPTURE ONLY — never gates anything. The pilot wrapper calls this when a board dies with the NPZ session error.")
    ap.add_argument("--npz", default="", help="NPZ file the failure is attributed to (default: backtest_v8/indicators/<SYMBOL>.npz)")
    ap.add_argument("--error", default="", help="pilot error text (e.g. 'NPZ has only 20 distinct stock sessions (need 30)')")
    a = ap.parse_args()
    if a.npz_failure:
        rec = _npz_identity(a.symbol, a.npz or None)
        rec.update({"event": "pilot-npz-failure", "error": (a.error or "")[:300]})
        _ledger_npz(rec)
        return 0
    for req in ("via", "cmd_file"):
        if not getattr(a, req):
            ap.error(f"--{req.replace('_', '-')} is required without --npz-failure")
    cmd_path = pathlib.Path(a.cmd_file)
    remote = cmd_path.read_text()
    _guard = _dedup_lock(a.tag or a.symbol)
    if _guard is None:
        log({"event": "dup-skip", "symbol": a.symbol, "tag": a.tag})
        cmd_path.unlink(missing_ok=True)
        return 0
    res = None
    if os.environ.get("V15_DISPATCH_30D_FAST", "1") == "1":  # every window: tail-refresh + push (365D/REPAIR included)
        # USER 2026-10-13: "YOU DONT NEED A 365D NPZ FOR A 30D BACKTEST" — a full-history rebuild took 10+ min per stock
        # symbol and stalled every launch. 30D boards launch at once on S1's current NPZ (md5-verified push to the target);
        # the builder only runs when S1 has no NPZ for the symbol at all.
        res = fast_push(a.symbol, a.push)
    if res is None:
        res = prep(a.symbol, a.push)
    # USER 2026-10-15: ok=None is NOT a pass (see prep_disposition). The old gate only blocked ok=False, so
    # 2.2% of launches went out on an NPZ nobody had verified. Skipping here costs one tick; the scheduler
    # requeues the symbol and the next GOOD symbol takes the slot.
    _l_ok, _l_kind, _l_why, _ = prep_disposition(res)
    if not _l_ok:
        log({"event": "skip", "symbol": a.symbol, "tag": a.tag, "kind": _l_kind, "why": _l_why, "prep": res})
        cmd_path.unlink(missing_ok=True)
        return 3
    # ...and verify the bytes that will actually travel, freshness INCLUDED. Exit 3 = do not launch, the same
    # convention tools/v15_npz_prep.py and tools/v15_npz_preflight.py use.
    _pf_ok, _pf_why, _pf_res = npz_preflight(a.symbol, 30, check_freshness=True)
    if _pf_ok and _pf_why.startswith("DEGRADED"):
        log({"event": "npz-degraded", "symbol": a.symbol, "tag": a.tag, "why": _pf_why,
             "tail_age_h": _pf_res.get("tail_age_h"), "max_tail_age_h": _pf_res.get("max_tail_age_h")})
    if not _pf_ok:
        log({"event": "skip", "symbol": a.symbol, "tag": a.tag, "kind": "npz-preflight", "why": _pf_why,
             "bars": _pf_res.get("bars"), "span_days": _pf_res.get("span_days"), "tail_age_h": _pf_res.get("tail_age_h"), "prep": res})
        cmd_path.unlink(missing_ok=True)
        return 3
    if os.environ.get("V15_PREV_BEST_SHIP", "1") == "1":
        res["prev_best_files"] = ship_prev_best(a.symbol, a.tag, a.push)
    at_pre, at_info = _autotune_exports(a.tag)
    _wm = re.search(r"--workers (\d+)", remote)
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", a.via, at_pre + remote], timeout=60)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = "ssh-timeout (job may still have started; pgrep dedups)"
    log({"event": "launched", "symbol": a.symbol, "tag": a.tag, "via": a.via, "ssh_rc": rc, "prep": res, "workers": int(_wm.group(1)) if _wm else None, "autotune": at_info})
    cmd_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
