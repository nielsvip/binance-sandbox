"""tools/v15_graph_search: group ablation, culprit removal, fault paths, prune and safety on a synthetic additive evaluator (no NPZ, no engine)."""
import time

from tools import v15_diagnose_repair as DR
from tools import v15_graph_search as GS

# synthetic engine: (trades, tim, gain, dd) per key=value; EXIT_BAD_* are a culprit group (on by default, removing helps)
EFFECTS = {
    ("EXIT_BAD_A_ENABLED", False): (0, 6.0, 3.0, -1.0),
    ("EXIT_BAD_B_ENABLED", False): (0, 4.0, 2.0, -1.0),
    ("EXIT_GOOD_ENABLED", False): (0, 10.0, -6.0, 6.0),       # same group, removing it hurts -> must be re-added
    ("ENTRY_X_ENABLED", True): (25, 8.0, 1.0, 1.0),
    ("CONFIRM_HTF_FILTER", True): (-10, -5.0, 2.0, -2.0),
    ("NOISE_ENABLED", True): (0, 0.0, 0.0, 0.0),               # inert: must be pruned if ever picked up
    ("HARD_STOP_GUARD", False): (30, 5.0, 20.0, 5.0),          # safety loosen: never applied
    ("ABLATION_DISABLE_REENTRY", True): (0, 0.0, 50.0, 0.0),   # P0: never applied
}
DEFAULTS = {"EXIT_BAD_A_ENABLED": True, "EXIT_BAD_B_ENABLED": True, "EXIT_GOOD_ENABLED": True, "ENTRY_X_ENABLED": False,
            "CONFIRM_HTF_FILTER": False, "NOISE_ENABLED": False, "HARD_STOP_GUARD": True, "ABLATION_DISABLE_REENTRY": False}
GRAPH = {"switch": {"EXIT_BAD_A_ENABLED": {"lifecycle": "EXIT_CLOSE", "family": "EXIT_CLOSE:BAD", "code_family": "EXIT_CLOSE", "off": "False"},
                    "EXIT_BAD_B_ENABLED": {"lifecycle": "EXIT_CLOSE", "family": "EXIT_CLOSE:BAD", "code_family": "EXIT_CLOSE", "off": "False"},
                    "EXIT_GOOD_ENABLED": {"lifecycle": "EXIT_CLOSE", "family": "EXIT_CLOSE:GOOD", "code_family": "EXIT_CLOSE", "off": "False"},
                    "ENTRY_X_ENABLED": {"lifecycle": "ENTRY_OPEN", "family": "ENTRY_OPEN:X", "code_family": "ENTRY_OPEN", "off": "False"},
                    "CONFIRM_HTF_FILTER": {"lifecycle": "ENTRY_GATE", "family": "ENTRY_GATE:HTF", "code_family": "ENTRY_GATE", "off": "False"},
                    "NOISE_ENABLED": {"lifecycle": "GLOBAL", "family": "GLOBAL:NOISE", "code_family": None, "off": "False"},
                    "HARD_STOP_GUARD": {"lifecycle": "EXIT_CLOSE", "family": "EXIT_CLOSE:HARD", "code_family": "EXIT_CLOSE", "off": "False"}},
         "groups": {"TAB:EXIT_STRUCTURAL": ["EXIT_BAD_A_ENABLED", "EXIT_BAD_B_ENABLED", "EXIT_GOOD_ENABLED", "HARD_STOP_GUARD"],
                    "TAB:ENTRY_CONFIRMATION_GATES": ["CONFIRM_HTF_FILTER"], "LIFECYCLE:ENTRY_OPEN": ["ENTRY_X_ENABLED"]},
         "yellow": {"ENTRY_X_ENABLED": ["CONFIRM_HTF_FILTER"]}, "prior": {}, "exit_precedence": []}


def fake_eval(ov):
    tr, tim, g, dd = 40, 50.0, 1.0, 10.0
    for k, v in ov.items():
        e = EFFECTS.get((k, v))
        if e:
            tr += e[0]; tim += e[1]; g += e[2]; dd += e[3]
    valid = tr >= 10 and tim <= 80 and dd <= 30
    fp = str(sorted((k, str(v)) for k, v in ov.items() if (k, v) in EFFECTS))
    return {"gain_pct": g, "trades": tr, "tim_pct": tim, "max_dd_pct": dd, "wr_pct": 50.0, "valid": valid, "invalid_reason": "" if valid else "synthetic",
            "bh_pct": 2.0, "behavior_fingerprint": fp, "ledger": [{"type": "CLOSE", "exit_reason": "EXIT_BAD x", "entry_reason": "B12", "bars_held": 30, "pnl_pct": 0.1}] * tr}


def _ctx(calls):
    cands = []
    for i, (k, v) in enumerate(EFFECTS):
        cands.append({"tab": "EXIT_STRUCTURAL" if "EXIT" in k or "STOP" in k else "ENTRY_CONFIRMATION_GATES", "row": 3 + i, "switch": k, "cand": v, "ov": {k: v}, "orange": False, "blocked": ""})

    def eval_many(items, phase, cum_before, deadline):
        calls.extend(ov for _l, ov, _c in items)
        return [(fake_eval(ov), "") for _l, ov, _c in items]
    return {"defaults": DEFAULTS, "sanitize": lambda ov: dict(ov), "same_val": lambda a, b: str(a).lower() == str(b).lower(), "candidates": cands,
            "base_overrides": {}, "base_res": fake_eval({}), "bh": 2.0, "deadline": time.time() + 20.0, "eval_many": eval_many, "eval_ledger": fake_eval,
            "eval_365": lambda ov: (fake_eval(ov), 365.0), "qualifies_365": lambda r, s: (bool(r and r["valid"] and r["gain_pct"] > 0), [] if (r and r["valid"] and r["gain_pct"] > 0) else ["gain"]),
            "graph": GRAPH, "log": lambda m: None, "touch": lambda m: None}


def test_graph_search_removes_culprits_keeps_good_members_and_respects_safety():
    calls = []
    rep = GS.run(_ctx(calls))
    best = rep["best_overrides"]
    assert rep["accepted"]
    assert best.get("EXIT_BAD_A_ENABLED") is False and best.get("EXIT_BAD_B_ENABLED") is False
    assert best.get("EXIT_GOOD_ENABLED", True) is True          # re-added / never removed
    assert best.get("HARD_STOP_GUARD", True) is True            # safety gate never loosened
    assert best.get("ABLATION_DISABLE_REENTRY", False) is False  # ablation measured only
    assert "NOISE_ENABLED" not in rep["changes"]                 # pruned
    assert any(ov.get("ABLATION_DISABLE_REENTRY") is True for ov in calls)  # it WAS measured (investigation)
    assert any(a["group"] == "TAB:EXIT_STRUCTURAL" for a in rep["ablation"])
    m = DR.metrics(fake_eval(best))
    assert DR.compliant(m) and m["gain"] > DR.metrics(fake_eval({}))["gain"]


def test_dkey_prefers_365_qualified():
    m = DR.metrics(fake_eval({}))
    assert GS.dkey(m, {"gain": 1.0, "valid": True, "tim": 50, "dd": 5}, True) > GS.dkey(m, {"gain": 50.0, "valid": False, "tim": 50, "dd": 50}, False)
