"""E1 2026-10-20 (USER: "/history/ reasons carrying a bare VEC_EXACT tag would be absolutely useless"): structured provenance in /history/ for both venues.

Contracts: (1) the entry-block -> switch table equals the engine's own compute_reentry_blocks AST (drift guard) and every mapped key exists in the
repo's switch universe; (2) set_id is a stable sha256 of the canonical set; (3) attribution never guesses: unmapped / OR-member / converge labels say
UNATTRIBUTED:<reason>; (4) crypto + stocks history writers put `prov` + the |SW=..|F=.. tail on OPEN and CLOSE when HISTORY_PROV_ENABLED, and are
byte-identical to today when it is off; (5) the tail is appended AFTER ' |VEC_EXACT' so every `in` test keeps working; (6) metadata only: attribution
does not mutate the set and the twin's act minus `prov` is identical with the switch on and off.
"""
import ast
import asyncio
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vec_decisions import history_prov as H  # noqa: E402

SET = {
    "MODE": "crypto",
    "K_ZONE_ENTRY_ENABLED": True,
    "K_ZONE_LONG_THRESHOLD": 35,
    "K_ZONE_SHORT_THRESHOLD": 10,
    "EXIT_VELOCITY_WT_ENABLED": True,
    "EXIT_VELOCITY_WT_TFS": "1h,4h",
    "EXIT_VELOCITY_WT_MIN_TFS": 1,
    "WT_LOWER_CROSS_EXIT_TF": "1h",
    "WT_4H_VEL_EXIT_ENABLED": False,
    "WT_EXHAUST_EXIT_ENABLED": True,
    "WT_DC_EXIT_ENABLED": False,
    "WRONG_SIDE_ABS_KILL_ENABLED": False,
    "MU_CORRECTION_EXIT_ENABLED": False,
    "ATR_TRAIL_FILTER_TF": "15m",
    "BB_PULLBACK_GATE_ENABLED": True,
}


def _engine_blocks():
    src = (ROOT / "v12_quick_engine.py").read_text()
    t = ast.parse(src)
    fn = [n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == "compute_reentry_blocks"][0]

    def keys(test):
        out = []
        for n in ast.walk(test):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "getattr" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
                out.append(n.args[1].value)
            elif isinstance(n, ast.Attribute) and getattr(n.value, "id", None) == "cfg":
                out.append(n.attr)
        return out

    res = {}

    def vs(node, stack):
        if isinstance(node, ast.If):
            for b in node.body:
                vs(b, stack + [keys(node.test)])
            for b in node.orelse:
                vs(b, stack)
            return
        if isinstance(node, ast.Assign):
            for tg in node.targets:
                if isinstance(tg, ast.Subscript) and getattr(tg.value, "id", None) == "blocks" and isinstance(tg.slice, ast.Constant):
                    r = res.setdefault(tg.slice.value, [])
                    for s in stack:
                        for k in s:
                            if k not in r:
                                r.append(k)
        for ch in ast.iter_child_nodes(node):
            if hasattr(ch, "body") or isinstance(ch, (ast.Assign, ast.If)):
                vs(ch, stack)

    for s in fn.body:
        vs(s, [])
    return res


def test_entry_block_table_equals_engine_ast():
    assert _engine_blocks() == H.ENTRY_BLOCK_KEYS


def test_every_mapped_key_exists_in_the_switch_universe():
    import config
    import config_tradier
    import v12_quick_engine as V

    sw = json.loads((ROOT / "data" / "SWITCH_BIBLE.json").read_text())["switches"]
    keys = set()
    for v in H.ENTRY_BLOCK_KEYS.values():
        keys.update(v)
    specs = [s for _, s in H._LABEL_SPECS] + list(H.ENTRY_BLOCK_OVERRIDES.values())
    for sp in specs:
        for f in ("switch", "requires", "any", "params"):
            x = sp.get(f)
            keys.update([x] if isinstance(x, str) else (x or []))
        keys.update((sp.get("by_venue") or {}).values())
    keys = {k for k in keys if not k.startswith("UNATTRIBUTED") and k not in ("NONE", "MODE")}
    missing = [k for k in sorted(keys) if k not in sw and not hasattr(V.QuickConfig, k) and not hasattr(config.Config, k) and not hasattr(config_tradier.TradierConfig, k)]
    assert not missing, missing


def test_set_id_is_stable_and_order_independent():
    a = H.set_id(SET)
    assert a == H.set_id(dict(reversed(list(SET.items()))))
    assert len(a) == 64 and a == H.set_id(copy.deepcopy(SET))
    s2 = dict(SET)
    s2["K_ZONE_LONG_THRESHOLD"] = 36
    assert H.set_id(s2) != a


def test_attribution_exact_and_never_guessed():
    before = copy.deepcopy(SET)
    p = H.attribute("B_KZONE |VEC_EXACT", "AUGMENT", "LONG", "crypto", SET, "X_LONG")
    assert p["switch"] == "K_ZONE_ENTRY_ENABLED" and p["cand"] is True and p["params"]["K_ZONE_LONG_THRESHOLD"] == 35
    assert p["src"] == "VEC_EXACT" and p["tf"] == "15m" and p["set_id"] == H.set_id(SET) and p["stage"] == "AUGMENT"
    p = H.attribute("MAKER_PROFIT_EXIT_EXIT_VELOCITY_WT against-long g1.20% |VEC_EXACT", "REDUCE", "LONG", "crypto", SET, "X_LONG")
    assert p["switch"] == "EXIT_VELOCITY_WT_ENABLED" and p["params"]["EXIT_VELOCITY_WT_TFS"] == "1h,4h"
    p = H.attribute("WT_LOWER_CROSS_EXIT wt_15m higher wt+price", "REDUCE", "LONG", "crypto", SET, "X_LONG")
    assert p["switch"] == "WT_LOWER_CROSS_EXIT_TF" and p["cand"] == "1h"
    p = H.attribute("STATEFUL_PORTED_EXIT g1.0% age20m", "REDUCE", "LONG", "crypto", SET, "X_LONG")
    assert p["switch"].startswith("UNATTRIBUTED:") and p["cands"] == ["WT_EXHAUST_EXIT_ENABLED=True"]
    p = H.attribute("TOTALLY_NEW_LABEL_9", "OPEN", "LONG", "crypto", SET, "X_LONG")
    assert p["switch"] == "UNATTRIBUTED:label_not_in_verified_table"
    p = H.attribute("VEC_CONVERGE_OPEN vec-holds-live-flat |VEC_EXACT", "AUGMENT", "LONG", "crypto", SET, "X_LONG")
    assert p["src"] == "EXECUTION" and p["switch"].startswith("UNATTRIBUTED:converge")
    for lab, src in (("FIXED_STOP", "FIXED_STOP"), ("VEC_STALE_FLATTEN paper-flat-live-holds-no-act", "EXECUTION"), ("VEC_EXIT_OWED paper-flat-live-holds-retry", "EXECUTION")):
        p = H.attribute(lab + " |VEC_EXACT", "REDUCE", "LONG", "crypto", SET, "X_LONG")
        assert p["src"] == src and p["switch"] == "NONE", lab
    assert SET == before


def test_venue_split_switches():
    assert H.attribute("MULTI_TF_EXIT s2/3 g1%", "CLOSE", "LONG", "crypto", {}, "")["switch"] == "MULTI_TF_EXIT_ENABLED"
    assert H.attribute("MULTI_TF_EXIT s2/3 g1%", "CLOSE", "LONG", "stocks", {}, "")["switch"] == "MULTI_TF_EXIT_ENABLED_TRADIER"
    assert H.attribute("VEC_EXACT_OPEN_B_SRS_ENTRY", "OPEN", "LONG", "stocks", {}, "")["switch"] == "STRUCTURAL_RANGE_SHIFT_EXIT"


def test_tail_format_and_vec_tag_still_matches():
    p = {"switch": "K_ZONE_ENTRY_ENABLED", "filters": ["A_FILTER_TF=15m", "B_ENABLED=True"]}
    t = H.with_tail("B_KZONE |VEC_EXACT", p)
    assert t == "B_KZONE |VEC_EXACT|SW=K_ZONE_ENTRY_ENABLED|F=A_FILTER_TF=15m,B_ENABLED=True"
    assert " |VEC_EXACT" in t and "|VEC_EXACT" in t
    assert H.with_tail(t, p) == t
    assert H.human_tail({"switch": "NONE", "filters": []}) == "|SW=NONE|F=-"


def test_kill_switch_exists_in_both_configs_and_env_overrides(monkeypatch):
    import config
    import config_tradier

    assert config.Config.HISTORY_PROV_ENABLED is True and config_tradier.TradierConfig.HISTORY_PROV_ENABLED is True
    monkeypatch.setenv("HISTORY_PROV_ENABLED", "0")
    assert H.enabled(config.Config) is False
    monkeypatch.setenv("HISTORY_PROV_ENABLED", "1")
    assert H.enabled(SimpleNamespace(HISTORY_PROV_ENABLED=False)) is True
    monkeypatch.delenv("HISTORY_PROV_ENABLED")
    assert H.enabled(SimpleNamespace(HISTORY_PROV_ENABLED=False)) is False


# ───────────────────────────── crypto history writer ─────────────────────────────
def _crypto_service(tmp_path, flag):
    ps = importlib.import_module("ez_positions_service")
    stub = SimpleNamespace(_history_dedup={}, _HISTORY_DEDUP_WINDOW_S=1.0, config=SimpleNamespace(DATA_DIR=tmp_path, HISTORY_PROV_ENABLED=flag), redis_manager=None)
    return ps.PositionService._append_to_history, stub


def _last(tmp_path, acct, ss):
    return [json.loads(x) for x in (tmp_path / "history" / acct / f"{ss}.jsonl").read_text().splitlines()]


def _order(pk, qty, tagged_reason_text, prov):
    """what the wire does inside the dispatch task: tagged_reason() set the ContextVar, place_maker_order called note_order_reason."""
    import positions_truth as pt
    from live_twins import vec_provenance as vp

    vp.set_history_prov(tagged_reason_text, prov)
    pt.note_order_reason(pk, qty, "MAKER_PROFIT_EXIT_" + tagged_reason_text if prov and prov.get("ev") in ("REDUCE", "CLOSE") else tagged_reason_text)


def test_crypto_history_off_is_identical_to_today(tmp_path, monkeypatch):
    import positions_truth as pt

    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn, stub = _crypto_service(tmp_path, False)
    pt._RECENT_ORDER_REASONS.clear()
    pt.note_order_reason("men:X_LONG", 5.0, "B_KZONE |VEC_EXACT")
    asyncio.run(fn(stub, "men:X_LONG", "OPEN", 5.0, 10.0, {"snapshot": {"price": 10.0}}))
    ev = _last(tmp_path, "men", "X_LONG")[0]
    assert "prov" not in ev and ev["reason"] == "B_KZONE |VEC_EXACT" and set(ev) == {"ts", "type", "qty", "price", "value", "reason", "indicators"}


def test_crypto_open_and_close_carry_prov(tmp_path, monkeypatch):
    import positions_truth as pt
    from live_twins import vec_exact as vx

    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn, stub = _crypto_service(tmp_path, True)
    pt._RECENT_ORDER_REASONS.clear()
    pt._RECENT_ORDER_PROV.clear()
    monkeypatch.setattr(vx, "_CFG", [SimpleNamespace(HISTORY_PROV_ENABLED=True)])
    a_open = {"n": 1, "type": "OPEN", "reason": "B_KZONE", "reason_x": 1}
    a_open["prov"] = H.attribute(a_open["reason"], "OPEN", "LONG", "crypto", SET, "X_LONG")
    a_close = {"n": 2, "type": "CLOSE", "reason": "EXIT_VELOCITY_WT against-long g-0.50%"}
    a_close["prov"] = H.attribute(a_close["reason"], "CLOSE", "LONG", "crypto", SET, "X_LONG")

    async def go():
        for a, qty, t, px in ((a_open, 5.0, "OPEN", 10.0), (a_close, 5.0, "CLOSE", 9.5)):
            r = vx.tagged_reason(a)
            pt.note_order_reason("men:X_LONG", qty, ("MAKER_PROFIT_EXIT_" + r) if t == "CLOSE" else r)
            await fn(stub, "men:X_LONG", t, qty, px, {})

    asyncio.run(go())
    o, c = _last(tmp_path, "men", "X_LONG")
    assert o["prov"]["switch"] == "K_ZONE_ENTRY_ENABLED" and o["reason"].endswith("|VEC_EXACT|SW=K_ZONE_ENTRY_ENABLED|F=" + (",".join(o["prov"]["filters"]) or "-"))
    assert c["prov"]["switch"] == "EXIT_VELOCITY_WT_ENABLED" and "|SW=EXIT_VELOCITY_WT_ENABLED|F=" in c["reason"] and c["reason"].startswith("MAKER_PROFIT_EXIT_")
    assert o["prov"]["set_id"] == c["prov"]["set_id"] == H.set_id(SET)


def test_crypto_unmatched_vec_fill_is_unattributed_not_guessed(tmp_path, monkeypatch):
    import positions_truth as pt

    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn, stub = _crypto_service(tmp_path, True)
    pt._RECENT_ORDER_REASONS.clear()
    pt._RECENT_ORDER_PROV.clear()
    pt.note_order_reason("men:Y_LONG", 3.0, "B12 |VEC_EXACT")  # no prov registered for it
    asyncio.run(fn(stub, "men:Y_LONG", "OPEN", 3.0, 1.0, {}))
    ev = _last(tmp_path, "men", "Y_LONG")[0]
    assert ev["prov"]["switch"] == "UNATTRIBUTED:vec_prov_not_matched_to_fill" and ev["reason"].endswith("|SW=UNATTRIBUTED:vec_prov_not_matched_to_fill|F=-")


def test_crypto_fixed_stop_and_broker_events(tmp_path, monkeypatch):
    import positions_truth as pt

    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn, stub = _crypto_service(tmp_path, True)
    pt._RECENT_ORDER_REASONS.clear()
    pt._RECENT_ORDER_PROV.clear()

    async def go():
        pt.note_order_reason("men:Z_LONG", 2.0, "MAKER_PROFIT_EXIT_FIXED_STOP |VEC_EXACT")
        await fn(stub, "men:Z_LONG", "CLOSE", 2.0, 5.0, {})
        await fn(stub, "men:Z_LONG", "OPEN", 1.5, 5.0, {"reason": "BROKER_RECONCILED order=1"})

    asyncio.run(go())
    fs, br = _last(tmp_path, "men", "Z_LONG")
    assert fs["prov"]["src"] == "FIXED_STOP" and fs["prov"]["switch"] == "NONE" and fs["reason"].endswith("|SW=NONE|F=-")
    assert br["prov"]["src"] == "BROKER" and br["reason"] == "BROKER_RECONCILED order=1"


# ───────────────────────────── stocks history writer ─────────────────────────────
def _stock_mgr(tmp_path, flag):
    tm = importlib.import_module("tradier_manage")
    stub = SimpleNamespace(config=SimpleNamespace(DATA_DIR=tmp_path))
    return tm, stub


def test_stock_history_off_identical_then_on_carries_prov(tmp_path, monkeypatch):
    tm, stub = _stock_mgr(tmp_path, False)
    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn = tm.TradierTradeManager._append_to_history
    H._DISPATCH.clear()
    monkeypatch.setattr(tm.config, "HISTORY_PROV_ENABLED", False, raising=False)
    asyncio.run(fn(stub, "trb:AAPL_LONG", "OPEN", 2.0, 100.0, "VEC_EXACT_OPEN_B_SRS_ENTRY"))
    off = _last(tmp_path, "trb", "AAPL_LONG")[0]
    assert "prov" not in off and off["reason"] == "VEC_EXACT_OPEN_B_SRS_ENTRY" and set(off) == {"ts", "type", "qty", "price", "value", "reason", "indicators"}
    monkeypatch.setattr(tm.config, "HISTORY_PROV_ENABLED", True, raising=False)
    sset = dict(SET, MODE="tradier")
    po = H.attribute("B_SRS_ENTRY", "OPEN", "LONG", "stocks", sset, "AAPL_LONG")
    pc = H.attribute("EXIT_VELOCITY_WT against-long g-0.10%", "CLOSE", "LONG", "stocks", sset, "AAPL_LONG")
    H.register_dispatch("trb:AAPL_LONG", "VEC_EXACT_OPEN_B_SRS_ENTRY", po, "OPEN", tm.config)
    H.register_dispatch("trb:AAPL_LONG", "VEC_EXACT_CLOSE_EXIT_VELOCITY_WT against-long g-0.10%", pc, "CLOSE", tm.config)
    asyncio.run(fn(stub, "trb:AAPL_LONG", "OPEN", 2.0, 100.0, "VEC_EXACT_OPEN_B_SRS_ENTRY"))
    asyncio.run(fn(stub, "trb:AAPL_LONG", "CLOSE", 2.0, 99.0, "VEC_EXACT_CLOSE_EXIT_VELOCITY_WT against-long g-0.10%"))
    _, o, c = _last(tmp_path, "trb", "AAPL_LONG")
    assert o["prov"]["switch"] == "STRUCTURAL_RANGE_SHIFT_EXIT" and o["reason"].startswith("VEC_EXACT_OPEN_B_SRS_ENTRY|SW=STRUCTURAL_RANGE_SHIFT_EXIT|F=")
    assert c["prov"]["switch"] == "EXIT_VELOCITY_WT_ENABLED" and "|SW=EXIT_VELOCITY_WT_ENABLED|F=" in c["reason"]
    assert o["prov"]["set_id"] == c["prov"]["set_id"] == H.set_id(sset)


def test_stock_native_and_broker_events_classified(tmp_path, monkeypatch):
    tm, stub = _stock_mgr(tmp_path, True)
    monkeypatch.setattr(tm.config, "HISTORY_PROV_ENABLED", True, raising=False)
    fn = tm.TradierTradeManager._append_to_history
    asyncio.run(fn(stub, "trb:MU_SHORT", "CLOSE", 1.0, 1.0, "BROKER_SYNC_CLOSE"))
    asyncio.run(fn(stub, "trb:MU_SHORT", "CLOSE", 1.0, 1.0, "FIXED_STOP_SHORT_px12.0"))
    b, f = _last(tmp_path, "trb", "MU_SHORT")
    assert b["prov"]["src"] == "BROKER" and b["reason"] == "BROKER_SYNC_CLOSE"
    assert f["prov"]["src"] == "FIXED_STOP" and f["prov"]["switch"] == "NONE"


# ───────────────────────────── metadata-only proof ─────────────────────────────
def test_tagged_reason_is_byte_identical_with_flag_on_or_off(monkeypatch):
    from live_twins import vec_exact as vx

    a = {"n": 1, "type": "OPEN", "reason": "B12", "prov": {"switch": "X"}}
    monkeypatch.setattr(vx, "_CFG", [SimpleNamespace(HISTORY_PROV_ENABLED=False)])
    off = vx.tagged_reason(a)
    monkeypatch.setattr(vx, "_CFG", [SimpleNamespace(HISTORY_PROV_ENABLED=True)])
    on = vx.tagged_reason(a)
    assert off == on == "B12 |VEC_EXACT"


def test_note_order_reason_ring_unchanged_by_prov(monkeypatch):
    import positions_truth as pt

    from live_twins import vec_provenance as vp

    vp._HIST_PROV.set(None)
    pt._RECENT_ORDER_REASONS.clear()
    pt._RECENT_ORDER_PROV.clear()
    pt.note_order_reason("men:Q_LONG", 4.0, "B12 |VEC_EXACT")
    assert pt.match_recent_order_reason("men:Q_LONG", 4.0) == "B12 |VEC_EXACT"
    assert pt.match_recent_order_prov("men:Q_LONG", 4.0) is None


def test_attribute_never_raises_on_garbage():
    for bad in (None, 5, "", object()):
        p = H.attribute(bad, None, None, None, None, "")
        assert isinstance(p, dict) and "switch" in p


def test_leaked_block_never_crosses_position_keys():
    import positions_truth as pt
    from live_twins import vec_provenance as vp

    pt._RECENT_ORDER_REASONS.clear()
    pt._RECENT_ORDER_PROV.clear()
    vp.set_history_prov("B12 |VEC_EXACT", {"switch": "REENTRY_B12_WT_MOM_ENABLED"})
    tok = vp.mark("men:A_LONG", 1.0, "OPEN")
    try:
        pt.note_order_reason("men:B_LONG", 4.0, "B12 |VEC_EXACT")
        assert pt.match_recent_order_prov("men:B_LONG", 4.0) is None
        pt.note_order_reason("men:A_LONG", 4.0, "B12 |VEC_EXACT")
        assert pt.match_recent_order_prov("men:A_LONG", 4.0)["switch"] == "REENTRY_B12_WT_MOM_ENABLED"
    finally:
        vp.reset(tok)
        vp._HIST_PROV.set(None)


def test_venue_is_decided_by_symbol_not_by_set_mode():
    assert H.venue_of("BONKUSDC", {}) == "crypto" and H.venue_of("1000PEPEUSDT", {}) == "crypto"
    assert H.venue_of("CRWD", {"MODE": "crypto"}) == "stocks" and H.venue_of("AMD", {}) == "stocks"


def test_unserialisable_prov_never_loses_the_history_line(tmp_path, monkeypatch):
    import positions_truth as pt

    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn, stub = _crypto_service(tmp_path, True)
    pt._RECENT_ORDER_REASONS.clear()
    pt._RECENT_ORDER_PROV.clear()
    bad = {"switch": "S", "filters": [], "cand": object()}
    pt.note_order_reason("men:W_LONG", 2.0, "B12 |VEC_EXACT")
    pt._RECENT_ORDER_PROV["men:W_LONG"] = [(__import__("time").time(), 2.0, "B12 |VEC_EXACT", bad)]
    asyncio.run(fn(stub, "men:W_LONG", "OPEN", 2.0, 1.0, {}))
    ev = _last(tmp_path, "men", "W_LONG")[0]
    assert "prov" not in ev and ev["qty"] == 2.0 and ev["reason"].startswith("B12 |VEC_EXACT")


# ───────────── r2: exits that inherited an ENTRY label (coordinator 2026-10-20) + stock broker echoes ─────────────
def test_exit_with_inherited_entry_label_is_not_attributed_to_the_entry_switch(tmp_path, monkeypatch):
    import positions_truth as pt
    from live_twins import vec_provenance as vp

    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn, stub = _crypto_service(tmp_path, True)
    pt._RECENT_ORDER_REASONS.clear()
    pt._RECENT_ORDER_PROV.clear()
    vp._RECENT_VEC_EXITS.clear()
    # no matched order (position vanished from the API), decision-context fallback handed back the previous line's entry reason
    ctx = {"reason": "B14 |VEC_EXACT"}
    asyncio.run(fn(stub, "ang:V_SHORT", "REDUCE", 241.0, 0.047, ctx, source="api_absence_confirmed"))
    ev = _last(tmp_path, "ang", "V_SHORT")[0]
    p = ev["prov"]
    assert p["src"] == "EXTERNAL" and p["switch"] == "UNATTRIBUTED:exit_without_bot_order" and p["reason_inherited"] is True and p["inherited_label"] == "B14" and p["detected_by"] == "api_absence_confirmed"
    assert "REENTRY_B14_HA_TREND_ENABLED" not in json.dumps(p)
    # a vec exit was dispatched for the key shortly before: used, with its basis stated
    xp = H.attribute("EXIT_VELOCITY_WT against-short g0.1%", "CLOSE", "SHORT", "crypto", SET, "V_SHORT")
    vp.note_vec_exit("ang:V2_SHORT", "CLOSE", "EXIT_VELOCITY_WT against-short g0.1% |VEC_EXACT", xp)
    asyncio.run(fn(stub, "ang:V2_SHORT", "REDUCE", 10.0, 0.047, {"reason": "B14 |VEC_EXACT"}, source="detected"))
    p2 = _last(tmp_path, "ang", "V2_SHORT")[0]["prov"]
    assert p2["switch"] == "EXIT_VELOCITY_WT_ENABLED" and p2["attribution_basis"].startswith("LAST_VEC_EXIT") and p2["reason_inherited"] and p2["inherited_label"] == "B14"


def test_entry_label_detector():
    for r in ("B14 |VEC_EXACT", "B_KZONE", "REENTRY_HIGHER_LOW", "VEC_CONVERGE_OPEN vec-holds-live-flat |VEC_EXACT", "MAKER_PROFIT_EXIT_B12 |VEC_EXACT", "HARDCODED_RALLY_REENTRY"):
        assert H.is_entry_label(r), r
    for r in ("EXIT_VELOCITY_WT against-long g1%", "WT_CROSS_EXIT_1h_bull", "FIXED_STOP", "STATEFUL_PORTED_EXIT g1%"):
        assert not H.is_entry_label(r), r


def test_tagged_reason_registers_vec_exit_only_for_exits(monkeypatch):
    from live_twins import vec_exact as vx
    from live_twins import vec_provenance as vp

    vp._RECENT_VEC_EXITS.clear()
    monkeypatch.setattr(vx, "_CFG", [SimpleNamespace(HISTORY_PROV_ENABLED=True)])
    tok = vp.mark("ang:Q_LONG", 1.0, "CLOSE")
    try:
        vx.tagged_reason({"n": 1, "type": "CLOSE", "reason": "WT_CROSS_EXIT_1h_bull", "prov": {"switch": "WT_CROSS_EXIT_ENABLED"}})
        vx.tagged_reason({"n": 2, "type": "OPEN", "reason": "B12", "prov": {"switch": "X"}})
    finally:
        vp.reset(tok)
    assert vp.recent_vec_exit("ang:Q_LONG")["switch"] == "WT_CROSS_EXIT_ENABLED"
    assert len(vp._RECENT_VEC_EXITS["ang:Q_LONG"]) == 1


def test_stock_broker_echo_states_whether_a_bot_event_precedes(tmp_path, monkeypatch):
    tp = importlib.import_module("tradier_positions")
    cls = [c for c in vars(tp).values() if isinstance(c, type) and hasattr(c, "append_to_position_history_file")][0]
    stub = SimpleNamespace(config=SimpleNamespace(DATA_DIR=tmp_path, HISTORY_PROV_ENABLED=True), redis_manager=None)
    monkeypatch.delenv("HISTORY_PROV_ENABLED", raising=False)
    fn = cls.append_to_position_history_file
    asyncio.run(fn(stub, "trb:ZZ_LONG", "CLOSE", 1.0, 10.0, {"reason": "BROKER_SYNC_CLOSE"}))
    asyncio.run(fn(stub, "trb:YY_LONG", "CLOSE", 1.0, 10.0, {"reason": "DAYTRADE_TARGET dc_15m_high"}))
    asyncio.run(fn(stub, "trb:YY_LONG", "CLOSE", 1.0, 10.0, {"reason": "Broker sync"}))
    a = _last(tmp_path, "trb", "ZZ_LONG")[0]["prov"]
    assert a["src"] == "BROKER" and a["echo_of_bot_event"] is False and a["switch"] == "UNATTRIBUTED:broker_fill_without_bot_event"
    y = _last(tmp_path, "trb", "YY_LONG")
    assert "prov" not in y[0] and y[1]["prov"]["echo_of_bot_event"] is True and y[1]["prov"]["switch"] == "NONE"
