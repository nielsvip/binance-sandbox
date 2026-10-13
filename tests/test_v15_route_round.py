"""Route round (enlarged PINPOINT, USER 2026-10-13) — pure logic pinned with a synthetic engine.

Synthetic engine: gain = base + f(set). A filter HTF_GATE_ENABLED=True masks the entry path NEW_ENTRY_ENABLED (the path
only binds when the gate is off); MOM3_FILTER_TF=1h is a good filter; DAYTRADE_DC_TARGET_TF=OFF is a good exit drop.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import v15_route_round as R  # noqa: E402

DEFAULTS = {"HTF_GATE_ENABLED": True, "MOM3_FILTER_TF": "OFF", "NEW_ENTRY_ENABLED": False, "DAYTRADE_DC_TARGET_TF": "1h", "EXIT_MIN_HOLD": 4, "COOLDOWN_BARS": 6, "ENTRY_LH_TRIGGER_ENABLED": False}


def _eff(ov, k):
    return ov.get(k, DEFAULTS[k])


def engine(ov):
    g, trades = 1.0, 20
    gate = R.same(_eff(ov, "HTF_GATE_ENABLED"), True)
    if R.same(_eff(ov, "MOM3_FILTER_TF"), "1h"):
        g += 2.0
    if R.same(_eff(ov, "NEW_ENTRY_ENABLED"), True):
        if gate:
            pass  # masked: never fires
        else:
            g += 5.0
            trades += 15
    if not gate:
        g -= 1.0  # removing the gate alone hurts
        trades += 5
    if R.same(_eff(ov, "DAYTRADE_DC_TARGET_TF"), "OFF"):
        g += 1.5
    hold = _eff(ov, "EXIT_MIN_HOLD")
    g += {2: -0.2, 3: 0.1, 4: 0.0, 5: 0.4, 6: 0.3, 8: -0.1}.get(int(hold), -0.5)
    if R.same(_eff(ov, "ENTRY_LH_TRIGGER_ENABLED"), True):
        g += 0.7
    fp = R.move_key({k: _eff(ov, k) for k in DEFAULTS if not (k == "NEW_ENTRY_ENABLED" and gate)})
    return {"gain_pct": g, "trades": trades, "tim_pct": 40.0, "valid": True, "behavior_fingerprint": fp, "max_dd_pct": 3.0}


def _ctx(running=None, **kw):
    calls = {"n": 0, "batches": 0, "promos": []}

    def eval_batch(items):
        calls["batches"] += 1
        calls["n"] += len(items)
        return [(engine(ov), "") for _l, ov in items]

    ctx = {
        "symside": "TEST_LONG", "cat_side": "CRYPTO_LONG", "tier": "WINNER",
        "running": dict(running or {}), "gain": engine(running or {})["gain_pct"], "defaults": DEFAULTS,
        "filter_names": {"HTF_GATE_ENABLED", "MOM3_FILTER_TF"},
        "path_rows": [("ENTRY_BREAKOUT_CHANNEL", "NEW_ENTRY_ENABLED", True), ("EXIT_STRUCTURAL", "DAYTRADE_DC_TARGET_TF", "OFF"), ("EXIT_STRUCTURAL", "EXIT_MIN_HOLD", 5)],
        "options": {"MOM3_FILTER_TF": ["OFF", "15m", "1h", "4h", "D"], "HTF_GATE_ENABLED": [True, False], "EXIT_MIN_HOLD": [2, 3, 4, 5, 6, 8]},
        "rowless": [("ENTRY_LH_TRIGGER_ENABLED", True)], "effective_keys": ["EXIT_MIN_HOLD", "MOM3_FILTER_TF"],
        "eval_batch": eval_batch, "accept": lambda res, err, d, c: (not err) and res["valid"] and res["trades"] >= 10,
        "promote": lambda ov, res, d, how, label, c: calls["promos"].append((how, label, round(d, 4))),
        "left": lambda: True, "frac_left": lambda: 1.0, "know": {}, "log": lambda m: None, "top_k": 8,
        "parse": lambda v, d: (v == "True") if isinstance(d, bool) else (int(v) if isinstance(d, int) else v), "chain_trades": 20,
    }
    ctx.update(kw)
    return ctx, calls


def test_classify_and_off_value():
    assert R.classify_key("MOM3_FILTER_TF") == "filter"
    assert R.classify_key("HTF_DIRECTION_GATE_ENABLED") == "filter"
    assert R.classify_key("REENTRY_PULL1_ENABLED", "REENTRY_WINDOWED") == "reentry"
    assert R.classify_key("EXIT_VELOCITY_WT_ENABLED", "EXIT_VELOCITY") == "exit"
    assert R.classify_key("ANYTHING", "ENTRY_BREAKOUT_CHANNEL") == "entry"
    assert R.classify_key("X", "ENTRY_CONFIRMATION_GATES") == "filter"
    assert R.off_value("MOM3_FILTER_TF", "1h") == "OFF"
    assert R.off_value("MOM3_FILTER_TF", "OFF") is None
    assert R.off_value("HTF_GATE_ENABLED", True) is False
    assert R.off_value("SOME_THRESHOLD", 3.5) is None
    assert R.off_value("GOLDEN_RULE_ENABLED", True) is None  # sacred never ablated
    assert R.active_filters({"MOM3_FILTER_TF": "1h"}, DEFAULTS, {"MOM3_FILTER_TF", "HTF_GATE_ENABLED"}) == {"HTF_GATE_ENABLED": False, "MOM3_FILTER_TF": "OFF"}


def test_grid_values():
    assert R.grid_values(4, [2, 3, 4, 5], is_int=True)[:3] == [2, 3, 5]
    assert 8 in R.grid_values(4, [], is_int=True) and 4 not in R.grid_values(4, [], is_int=True)
    assert R.grid_values(True) == [False]
    assert set(R.grid_values("1h")) == {"OFF", "15m", "4h", "D"}
    assert R.canon(2.0) == R.canon(2) == "2" and R.canon("true") == "True" and R.canon("off") == "OFF"


def test_batched_greedy_equals_serial_choice_and_is_honest():
    cands = [{"label": f"MOM3={v}", "delta": {"MOM3_FILTER_TF": v}, "key": "MOM3_FILTER_TF"} for v in ("15m", "1h", "4h")]
    cands.append({"label": "DT=OFF", "delta": {"DAYTRADE_DC_TARGET_TF": "OFF"}, "key": "DAYTRADE_DC_TARGET_TF"})
    batches = []

    def eb(items):
        batches.append(len(items))
        return [(engine(ov), "") for _l, ov in items]
    r = R.batched_greedy(cands, {}, engine({})["gain_pct"], eb, lambda res, err, d, c: True, lambda: True)
    assert [p["label"] for p in r["promotions"]] == ["MOM3=1h", "DT=OFF"]
    assert abs(r["gain"] - engine(r["ov"])["gain_pct"]) < 1e-12  # the reported gain IS the exact set's eval
    assert batches[0] == 4  # one parallel batch for all candidates


def test_route_round_unmasks_path_masked_by_filter(tmp_path):
    ctx, calls = _ctx(running={"MOM3_FILTER_TF": "1h"})
    out = R.run_route_round(ctx)
    labels = [p["label"] for p in out["promotions"]]
    # NEW_ENTRY is 0 on the filtered set (masked by HTF gate); the round finds it on the stripped base and grafts it with the gate off
    assert any(l.startswith("GRAFT:NEW_ENTRY_ENABLED=True-HTF_GATE_ENABLED") for l in labels), labels
    assert any(u["key"] == "NEW_ENTRY_ENABLED" for u in out["unmask"])
    assert any(l.startswith("ROWLESS:ENTRY_LH_TRIGGER_ENABLED") for l in labels), labels
    assert out["final_gain"] > out["entry_gain"] + 5.0
    assert abs(out["final_gain"] - engine(out["final_ov"])["gain_pct"]) < 1e-12
    assert out["board"]["masked_by"].get("NEW_ENTRY_ENABLED=True") == ["HTF_GATE_ENABLED"]


def test_budget_respected():
    ctx, calls = _ctx(left=lambda: False)
    out = R.run_route_round(ctx)
    assert calls["n"] == 0 and out["promotions"] == []


def test_knowledge_merge_lock_and_replay(tmp_path):
    ctx, _ = _ctx(running={"MOM3_FILTER_TF": "1h"})
    out = R.run_route_round(ctx)
    b = dict(out["board"], key="k1")
    R.save_board("CRYPTO_LONG", b, tmp_path)
    R.save_board("CRYPTO_LONG", b, tmp_path)  # idempotent
    b2 = dict(out["board"], key="k2", symside="OTHER_LONG")
    know = R.save_board("CRYPTO_LONG", b2, tmp_path)
    assert know["boards"]["n"] == 2
    mk = "DAYTRADE_DC_TARGET_TF=OFF"
    assert know["moves"][mk]["pos"] == 2
    assert any(s["neg"] > 0 for s in know["moves"].values())  # negatives recorded (no survivorship bias)
    assert mk in R.known_moves(know)
    # a fresh board replays proven moves first (S0)
    ctx2, calls2 = _ctx(know=know)
    out2 = R.run_route_round(ctx2)
    assert out2["promotions"][0]["how"] == "S0_KNOWN"
    # fleet rebuild from ledgers reproduces the same counts
    res = R.rebuild_from_ledgers([str(tmp_path / "ledger_CRYPTO_LONG.jsonl")], tmp_path / "rebuilt")
    assert res == {"CRYPTO_LONG": 2}


def test_rowless_keys_deny_list(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"checks": {"COVERAGE": ["ENTRY_LH_TRIGGER_ENABLED: wired live+vec (x) but in no template", "DELTA_EXIT_TF: wired live+vec (x) but in no template", "DG_HTF_ALIGN_REQUIRE_1H: wired live+vec", "MAX_ORDER_VALUE_FIN: wired live+vec", "FOO: wired live only"]}}))
    assert R.rowless_keys(p) == ["ENTRY_LH_TRIGGER_ENABLED"]
    assert R.rowless_candidates(["ENTRY_LH_TRIGGER_ENABLED", "EXIT_MIN_HOLD"], DEFAULTS, {}) == [("ENTRY_LH_TRIGGER_ENABLED", True), ("EXIT_MIN_HOLD", 2), ("EXIT_MIN_HOLD", 8)]


def test_repo_rowless_list_has_lh_trigger_and_no_denied():
    keys = R.rowless_keys()
    assert "ENTRY_LH_TRIGGER_ENABLED" in keys
    assert not any(k.startswith(("DELTA_", "DG_", "MAX_ORDER_VALUE", "GOLDEN_RULE_")) for k in keys)
