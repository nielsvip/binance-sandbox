#!/usr/bin/env python3
"""Exit-engine parity monitor (2026-09-28).

Aggregates the `module=="exit_engine"` rows that the execute_now instrumentation
writes to data/live_vs_vec_compare.jsonl, over a lookback window, and reports:

  * LEAKS  — leak=True rows: a gate-DISABLED family still fired = illegal trade
             under parity. These are the holes to close (or, if the path is
             actually good, to promote into the vectorized engine + defaults).
  * GATE_ON — mapped families whose gate is ON (legitimately allowed live).
  * UNMAPPED — families with no gate mapping (ULTIMATE_DC/MTF_*/DC_BREACH/
             ALL_ALL_GREEN/HTF_AGAINST ...): candidate good-paths to evaluate.

Also reports liveness (is the instrumentation producing rows at all?) so we can
tell restart-pending / broker-sync-stale from genuine parity.

Read-only. No config, no trade code. Run standalone or from cron.
"""
import json, sys, collections, datetime, pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
LEDGER = BASE / "data" / "live_vs_vec_compare.jsonl"
REPORT = BASE / "data" / "reports" / "exit_engine_parity_monitor_latest.md"
REPORT_JSON = BASE / "data" / "reports" / "exit_engine_parity_monitor_latest.json"
# 2026-10-06 (USER "SWITCHES in ALL SCRIPTS -> AUTOMATIC PARITY"): rows ez_manage logs as UNMAPPED (its in-process
# _EXIT_REASON_FAMILY_GATE covers 17 prefixes) are judged here against tools/forward_parity/families.py: family -> gate switch,
# resolved per sym_side like live _psym_get (per_sym_store full_config/overrides > cat_side default > config.Config default).
# Verdicts: LEAK (switch OFF for that sym_side yet fired), GATE_ON, SAFETY (exempt by design), UNGATED (no switch exists =
# live-side hook missing), UNMAPPED (family not in the registry).
try:
    from tools.forward_parity import families as _FAM
except Exception:
    _FAM = None
_CFG = None
_GATE_CACHE = {}


def _gate_value(position_key: str, knob: str, scope: str = "psym"):
    global _CFG
    k = (position_key if scope == "psym" else "*", knob)
    if k in _GATE_CACHE:
        return _GATE_CACHE[k]
    v = None
    try:
        ss = position_key.split(":", 1)[1]
        symbol, side = ss.rsplit("_", 1)
        if scope == "psym":
            try:
                import per_sym_store as _pss
                v = _pss.get_knob(symbol, side, knob, None)
            except Exception:
                v = None
        if v is None:
            if _CFG is None:
                import config as _c
                _CFG = _c.Config()
            v = getattr(_CFG, knob, None)
    except Exception:
        v = None
    _GATE_CACHE[k] = v
    return v


def _judge_unmapped(e: dict):
    """(verdict, family, knob, value) for a row ez_manage could not map."""
    if _FAM is None:
        return "UNMAPPED", "?", None, None
    m = _FAM.classify(e.get("live_reason", ""))
    fam = m["family"]
    if fam.startswith("UNCLASSIFIED"):
        return "UNMAPPED", fam, None, None
    if m["gate_kind"] == "safety":
        return "SAFETY", fam, None, None
    knob = m["gate_knob"]
    if not knob:
        return ("UNGATED" if m["gate_kind"] == "none" else "GATE_UNVERIFIED"), fam, None, None
    if knob == "STRUCT_EXIT_TF":
        knob = "SHORT_STRUCT_EXIT_TF" if str(e.get("position_side", "")).upper() == "SHORT" else "LONG_STRUCT_EXIT_TF"
    scope = getattr(_FAM, "GATE_SCOPE", {}).get(knob)
    if scope is None:
        return "GATE_UNVERIFIED", fam, knob, _gate_value(str(e.get("position_key", "")), knob, "global")
    val = _gate_value(str(e.get("position_key", "")), knob, scope)
    off = _FAM.gate_disabled(val, m["gate_kind"])
    if off is True:
        return "LEAK", fam, knob, val
    if off is False:
        return "GATE_ON", fam, knob, val
    return "UNGATED", fam, knob, val


def _prefix(reason: str) -> str:
    r = (reason or "").strip()
    for sep in ("_px", "_g0", "_g-", "_wt3m", "_k1", "_lvl", "_@", "_3m", "_pct"):
        i = r.find(sep)
        if i > 0:
            r = r[:i]
    return r[:38] or "?"


def run(lookback_hours: float = 48.0) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=lookback_hours)
    # 2026-10-06 director: monitors restart clean at the PARITY_VEC_EXACT switch (data/forward_parity/regime.json); older rows archived
    try:
        _rg = json.loads((BASE / "data" / "forward_parity" / "regime.json").read_text())
        _rs = datetime.datetime.fromisoformat(str(_rg.get("start_utc", "")).replace("Z", "+00:00"))
        if _rs > cutoff:
            cutoff = _rs
    except Exception:
        _rg = {}
    leaks = collections.Counter()
    leak_reasons = collections.Counter()
    gate_on = collections.Counter()
    unmapped = collections.Counter()
    ungated = collections.Counter()
    unverified = collections.Counter()
    safety = collections.Counter()
    leak_keys = collections.Counter()
    vec_exact = collections.Counter()
    actions = collections.Counter()
    n_exit = 0
    last_ts = None
    if not LEDGER.exists():
        return {"error": f"ledger missing: {LEDGER}"}
    with LEDGER.open() as f:
        for line in f:
            line = line.strip()
            if not line or '"exit_engine"' not in line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("module") != "exit_engine":
                continue
            ts = e.get("timestamp", "")
            try:
                dt = datetime.datetime.fromisoformat(ts)
                if dt < cutoff:
                    continue
            except Exception:
                pass
            n_exit += 1
            last_ts = ts if (last_ts is None or ts > last_ts) else last_ts
            fam = e.get("family", "OTHER")
            actions[str(e.get("action"))] += 1
            if "|VEC_EXACT" in str(e.get("live_reason", "")):
                vec_exact[str(e.get("action"))] += 1
            elif e.get("leak"):
                leaks[fam] += 1
                leak_reasons[_prefix(e.get("live_reason", ""))] += 1
                leak_keys[f"{fam} {e.get('position_key')}"] += 1
            elif e.get("gate_knob"):
                gate_on[fam] += 1
            else:
                verdict, fam2, knob, val = _judge_unmapped(e)
                if verdict == "LEAK":
                    leaks[f"{fam2} ({knob}={val})"] += 1
                    leak_reasons[_prefix(e.get("live_reason", ""))] += 1
                    leak_keys[f"{fam2} {e.get('position_key')}"] += 1
                elif verdict == "GATE_ON":
                    gate_on[f"{fam2} ({knob})"] += 1
                elif verdict == "SAFETY":
                    safety[fam2] += 1
                elif verdict == "GATE_UNVERIFIED":
                    unverified[f"{fam2} ({knob}={val})"] += 1
                elif verdict == "UNGATED":
                    ungated[fam2] += 1
                else:
                    unmapped[_prefix(e.get("live_reason", ""))] += 1
    return {
        "now": now.isoformat(), "lookback_hours": lookback_hours,
        "n_exit_rows": n_exit, "last_exit_ts": last_ts,
        "leaks": leaks, "leak_reasons": leak_reasons,
        "gate_on": gate_on, "unmapped": unmapped, "actions": actions,
        "ungated": ungated, "safety": safety, "leak_keys": leak_keys, "unverified": unverified, "vec_exact": vec_exact, "since": cutoff.isoformat(),
    }


def render(r: dict) -> str:
    if r.get("error"):
        return f"# exit-engine parity monitor\n\nERROR: {r['error']}\n"
    L = []
    L.append("# Exit-engine parity monitor")
    L.append(f"\ngenerated: {r['now']} · lookback: {r['lookback_hours']}h")
    L.append(f"\n**exit_engine rows in window: {r['n_exit_rows']}** · since {r.get('since')} · last: {r['last_exit_ts']}")
    L.append(f"\nVEC_EXACT-decided orders (the vec engine decided; not gate-judged): {dict(r.get('vec_exact', {}))} — every OTHER row below is a native live path firing under PARITY_VEC_EXACT_MODE")
    if r["n_exit_rows"] == 0:
        L.append("\n⚠️ No exit_engine rows yet. Either (a) ez_manage not restarted since the "
                 "2026-09-28 instrumentation edit (running procs hold old code), or (b) no "
                 "execute_now calls happened. Restart ez_manage to activate; check broker-sync.")
    total_leak = sum(r["leaks"].values())
    L.append(f"\n## 🔴 LEAKS (gate-disabled families that fired = illegal trades): {total_leak}")
    if r["leaks"]:
        for fam, c in r["leaks"].most_common():
            L.append(f"- **{fam}**: {c}")
        L.append("\ntop leak reasons:")
        for rs, c in r["leak_reasons"].most_common(12):
            L.append(f"  - {c:5d}  {rs}")
        L.append("\n→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine "
                 "+ set the gate default ON in config so best cat_side stays default. If BAD, close "
                 "the code gate so it honors the disabled knob.")
    else:
        L.append("- none — no gate-disabled family fired. ✅ parity holds for mapped exits.")
    L.append(f"\n## ✅ GATE_ON (legitimately allowed live, has gate): {sum(r['gate_on'].values())}")
    for fam, c in r["gate_on"].most_common():
        L.append(f"- {fam}: {c}")
    if r.get("leak_keys"):
        L.append("\ntop leaking keys:")
        for k, c in r["leak_keys"].most_common(15):
            L.append(f"  - {c:5d}  {k}")
    L.append(f"\n## ⛔ UNGATED (family fires live with NO switch — live-side switch hook missing): {sum(r.get('ungated', {}).values())}")
    for fam, c in r.get("ungated", collections.Counter()).most_common(20):
        L.append(f"- {c:5d}  {fam}")
    L.append(f"\n## ❓ GATE_UNVERIFIED (switch named but its live read site/scope not yet verified — no verdict claimed): {sum(r.get('unverified', {}).values())}")
    for fam, c in r.get("unverified", collections.Counter()).most_common(15):
        L.append(f"- {c:5d}  {fam}")
    L.append(f"\n## 🛟 SAFETY (exempt by design: broker sync / margin / liquidation): {sum(r.get('safety', {}).values())}")
    for fam, c in r.get("safety", collections.Counter()).most_common(10):
        L.append(f"- {c:5d}  {fam}")
    L.append(f"\n## ❔ UNMAPPED (family not in tools/forward_parity/families.py — add it): "
             f"{sum(r['unmapped'].values())}")
    for rs, c in r["unmapped"].most_common(20):
        L.append(f"- {c:5d}  {rs}")
    L.append(f"\n## actions in window: {dict(r['actions'])}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    hrs = float(sys.argv[1]) if len(sys.argv) > 1 else 48.0
    res = run(hrs)
    md = render(res)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(md)
    if not res.get("error"):
        REPORT_JSON.write_text(json.dumps({"now": res["now"], "lookback_hours": res["lookback_hours"], "n_exit_rows": res["n_exit_rows"], "last_exit_ts": res["last_exit_ts"], "n_leaks": sum(res["leaks"].values()), "n_gate_on": sum(res["gate_on"].values()), "n_ungated": sum(res["ungated"].values()), "n_safety": sum(res["safety"].values()), "n_unmapped": sum(res["unmapped"].values()), "n_unverified": sum(res["unverified"].values()), "since": res["since"], "vec_exact_by_action": dict(res["vec_exact"]), "native_rows": res["n_exit_rows"] - sum(res["vec_exact"].values()), "unverified_by_family": dict(res["unverified"]), "leaks_by_family": dict(res["leaks"]), "leak_keys": dict(res["leak_keys"].most_common(50)), "gate_on_by_family": dict(res["gate_on"]), "ungated_by_family": dict(res["ungated"]), "safety_by_family": dict(res["safety"]), "unmapped": dict(res["unmapped"].most_common(50)), "actions": dict(res["actions"])}, indent=1, default=str))
    try:
        from tools.forward_parity import dashboard as _dash
        _dash.build()
    except Exception as _de:
        print(f"[dashboard] rebuild failed: {_de}")
    print(md)
    print(f"\n[written] {REPORT}")
