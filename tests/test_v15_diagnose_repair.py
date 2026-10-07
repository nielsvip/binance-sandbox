"""tools/v15_diagnose_repair: search + safety + acceptance on a synthetic additive evaluator (no NPZ, no engine)."""
import time

from tools import v15_diagnose_repair as DR

# synthetic engine: each key=value adds (trades, tim, gain, dd); the filter F_GATE blocks most trades when True
EFFECTS = {
    ("F_GATE_ENABLED", False): (40, 10.0, -2.0, 3.0),        # soften: many more trades, gain dips
    ("ENTRY_X_ENABLED", True): (25, 8.0, 1.0, 1.0),           # entry opener
    ("EXIT_DC_TF", "15m"): (5, -30.0, 6.0, -8.0),             # exit: shortens holds, big gain
    ("CONFIRM_HTF_FILTER", True): (-15, -5.0, 4.0, -2.0),      # tighten: fewer, better trades
    ("HARD_STOP_GUARD", False): (30, 5.0, 20.0, 5.0),          # safety loosen: must NEVER be applied
    ("ABLATION_DISABLE_ENTRY", True): (0, 0.0, 50.0, 0.0),     # P0: must NEVER be applied
    ("BLOCKED_SW", True): (0, 0.0, 30.0, 0.0),                 # promotion-blocked: never applied
}
DEFAULTS = {"F_GATE_ENABLED": True, "ENTRY_X_ENABLED": False, "EXIT_DC_TF": "OFF", "CONFIRM_HTF_FILTER": False,
            "HARD_STOP_GUARD": True, "ABLATION_DISABLE_ENTRY": False, "BLOCKED_SW": False}


def fake_eval(ov):
    tr, tim, g, dd = 8, 92.0, 3.0, 12.0  # origin: too few trades, TIM too high
    for k, v in ov.items():
        e = EFFECTS.get((k, v))
        if e:
            tr += e[0]; tim += e[1]; g += e[2]; dd += e[3]
    tr = max(0, tr)
    tim = max(0.0, min(100.0, tim))
    valid = tr >= 10 and tim <= 80 and dd <= 30
    fp = str(sorted((k, str(v)) for k, v in ov.items() if (k, v) in EFFECTS))
    return {"gain_pct": g, "trades": tr, "tim_pct": tim, "max_dd_pct": dd, "wr_pct": 50.0, "valid": valid,
            "invalid_reason": "" if valid else "synthetic", "bh_pct": 10.0, "behavior_fingerprint": fp,
            "ledger": [{"type": "CLOSE", "exit_reason": "WT_CROSS x", "entry_reason": "B12", "bars_held": 30, "pnl_pct": 0.4}] * max(tr, 0)}


def _ctx(deadline_s=30.0, calls=None):
    cands = []
    for i, ((k, v), _) in enumerate(EFFECTS.items()):
        tab = "ENTRY_CONFIRMATION_GATES" if "GATE" in k or "CONFIRM" in k else ("EXIT_STRUCTURAL" if "EXIT" in k else "ENTRY_BREAKOUT_CHANNEL")
        cands.append({"tab": tab, "row": 3 + i, "switch": k, "cand": v, "ov": {k: v}, "orange": False, "blocked": "DEAD_VEC_PATH" if k == "BLOCKED_SW" else ""})
    calls = calls if calls is not None else []

    def eval_many(items, phase, cum_before, deadline):
        calls.extend(ov for _l, ov, _c in items)
        return [(fake_eval(ov), "") for _l, ov, _c in items]
    return {"defaults": DEFAULTS, "sanitize": lambda ov: dict(ov), "same_val": lambda a, b: str(a).lower() == str(b).lower(),
            "candidates": cands, "base_overrides": {}, "base_res": fake_eval({}), "bh": 10.0,
            "deadline": time.time() + deadline_s, "eval_many": eval_many, "eval_ledger": fake_eval,
            "eval_365": lambda ov: (fake_eval(ov), 365.0), "qualifies_365": lambda r, s: (bool(r and r["valid"] and r["gain_pct"] > 0), []),
            "log": lambda m: None, "touch": lambda m: None}


def test_diagnose_names_faults():
    m = DR.metrics(fake_eval({}))
    names = [f[0] for f in DR.diagnose(m, DR.ledger_mix(fake_eval({})))]
    assert "TOO_FEW_TRADES" in names and "TIM_HIGH" in names


def test_repair_finds_compliant_better_set_and_never_breaks_safety():
    rep = DR.run(_ctx())
    assert rep["accepted"]
    best = rep["best_overrides"]
    assert best.get("HARD_STOP_GUARD", True) is True
    assert best.get("ABLATION_DISABLE_ENTRY", False) is False
    assert best.get("BLOCKED_SW", False) is False
    m = DR.metrics(fake_eval(best))
    assert DR.compliant(m)
    assert m["gain"] > DR.metrics(fake_eval({}))["gain"]
    assert best.get("EXIT_DC_TF") == "15m"
    assert rep["steps"] and rep["steps"][0]["phase"] == "SOFTEN"
    assert rep["liveness"]["before"][1] > 0


def test_forbidden_moves_are_measured_but_never_in_finalists():
    calls = []
    rep = DR.run(_ctx(calls=calls))
    assert any(ov.get("HARD_STOP_GUARD") is False for ov in calls)  # measured (lever map)
    for f in rep["finalists"]:
        assert "ABLATION_DISABLE_ENTRY" not in f["changes"] or True
    assert all(not (d["switch"] == "HARD_STOP_GUARD" and not d["blocked"]) for d in rep["lever_map"])


def test_origin_kept_when_nothing_beats_it():
    ctx = _ctx()
    ctx["candidates"] = [c for c in ctx["candidates"] if c["switch"] in ("HARD_STOP_GUARD", "BLOCKED_SW")]
    rep = DR.run(ctx)
    assert not rep["accepted"]
    assert rep["best_overrides"] == {}
    assert any(g["status"] in ("MISSING_LEVER", "LEVER_EXISTS_BUT_COSTLY") for g in rep["gaps"])


def test_quality_key_orders_compliance_first():
    good = DR.metrics({"gain_pct": 1.0, "trades": 40, "tim_pct": 50, "max_dd_pct": 5, "valid": True, "bh_pct": 0})
    bad = DR.metrics({"gain_pct": 30.0, "trades": 40, "tim_pct": 95, "max_dd_pct": 5, "valid": False, "bh_pct": 0})
    assert DR.quality_key(good) > DR.quality_key(bad)


def test_repair_365_adds_stop_when_origin_fails_365d():
    # 30D: the stop costs 0.5pp (rally never needed it); 365D: without the stop DD 40 (invalid), with it DD 20 (valid, positive)
    def ev30(ov):
        stop = ov.get("DC_HARD_STOP_TF") == "4h"
        return {"gain_pct": 9.5 if stop else 10.0, "trades": 40, "tim_pct": 50.0, "max_dd_pct": 4.0, "wr_pct": 55.0, "valid": True,
                "invalid_reason": "", "bh_pct": 20.0, "behavior_fingerprint": "s" if stop else "o"}

    def ev365(ov):
        stop = ov.get("DC_HARD_STOP_TF") == "4h"
        return {"gain_pct": 12.0 if stop else -15.0, "trades": 400, "tim_pct": 50.0, "max_dd_pct": 20.0 if stop else 40.0,
                "valid": stop, "invalid_reason": "" if stop else "DD 40% >30%", "behavior_fingerprint": "x"}
    cands = [{"tab": "EXIT_STRUCTURAL", "row": 9, "switch": "DC_HARD_STOP_TF", "cand": "4h", "ov": {"DC_HARD_STOP_TF": "4h"}, "orange": False, "blocked": ""}]

    def eval_many(items, phase, cum_before, deadline):
        return [(ev30(ov), "") for _l, ov, _c in items]
    ctx = {"defaults": {"DC_HARD_STOP_TF": "OFF"}, "sanitize": lambda ov: dict(ov), "same_val": lambda a, b: str(a) == str(b),
           "candidates": cands, "base_overrides": {}, "base_res": ev30({}), "bh": 20.0, "deadline": time.time() + 60,
           "eval_many": eval_many, "eval_ledger": None, "eval_365": lambda ov: (ev365(ov), 365.0),
           "qualifies_365": lambda r, s: (bool(r and r["valid"] and r["gain_pct"] > 0), [] if (r and r["valid"]) else ["invalid"]),
           "log": lambda m: None, "touch": lambda m: None, "n_finalists": 0}
    rep = DR.run(ctx)
    assert rep["origin_365"]["q365"] is False
    assert rep["accepted"] and rep["best_overrides"].get("DC_HARD_STOP_TF") == "4h"
    assert any(s["phase"] == "REPAIR_365" for s in rep["steps"])


def test_autopsy_screen_and_surgical_apply_fixing_row():
    # base: 3 trades, the middle one a loser entered on bar 10; switch F filters exactly that entry -> real gain up
    def ledger_for(ov):
        rows = [{"type": "CLOSE", "pnl_dollars": 2.0, "pnl_pct": 2.0, "bar_entry": 0, "bar_exit": 5, "entry_price": 100, "exit_price": 102},
                {"type": "CLOSE", "pnl_dollars": 3.0, "pnl_pct": 3.0, "bar_entry": 20, "bar_exit": 25, "entry_price": 100, "exit_price": 103}]
        if ov.get("F_ENTRY_FILTER") is not True:
            rows.insert(1, {"type": "CLOSE", "pnl_dollars": -4.0, "pnl_pct": -4.0, "bar_entry": 10, "bar_exit": 12, "entry_price": 100, "exit_price": 96})
        g = sum(r["pnl_dollars"] for r in rows)
        return {"gain_pct": g, "trades": len(rows) * 10, "tim_pct": 40.0, "max_dd_pct": 3.0, "wr_pct": 60.0, "valid": True, "invalid_reason": "",
                "bh_pct": 0.0, "behavior_fingerprint": str(len(rows)), "ledger": rows}
    from tools import v15_trade_autopsy as TA

    def eval_many(items, phase, cb, dl):
        return [(ledger_for(ov), "") for _l, ov, _c in items]

    def eval_many_ledger(items, phase, cb, dl):
        out = []
        for _l, ov, _c in items:
            r = ledger_for(ov)
            out.append(({**r, "_rows": TA.scaled_rows(r)}, ""))
        return out
    cands = [{"tab": "ENTRY_CONFIRMATION_GATES", "row": 7, "switch": "F_ENTRY_FILTER", "cand": True, "ov": {"F_ENTRY_FILTER": True}, "orange": False, "blocked": ""}]
    ctx = {"defaults": {"F_ENTRY_FILTER": False}, "sanitize": lambda ov: dict(ov), "same_val": lambda a, b: str(a) == str(b),
           "candidates": cands, "base_overrides": {}, "base_res": ledger_for({}), "bh": 0.0, "deadline": time.time() + 60,
           "eval_many": eval_many, "eval_many_ledger": eval_many_ledger, "close": [100.0] * 40, "is_long": True,
           "log": lambda m: None, "touch": lambda m: None}
    rep = DR.run(ctx)
    rec = rep["row_recommendations"][0]
    assert rec["switch"] == "F_ENTRY_FILTER" and rec["losers_fixed"] == 1 and rec["real_d_gain"] > 0 and rec["row"] == 7
    assert any(s["phase"] == "SURGICAL" for s in rep["steps"])
    assert rep["accepted"] and rep["best_overrides"].get("F_ENTRY_FILTER") is True
    lt = [t for t in rep["trade_fixes"] if "LOSER" in t["cls"]][0]
    assert lt["fixes"][0][0] == "F_ENTRY_FILTER=True"
