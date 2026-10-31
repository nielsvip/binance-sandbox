"""The Mac books annotate the executor's broker positions and never override them (USER 2026-10-30)."""
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vector_direct_mac_positions_test", ROOT / "tools" / "vector_direct_trader.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def trader(tmp_path, book, epoch=None, venue="stocks"):
    alerts, logs = [], []
    obj = module.Trader.__new__(module.Trader)
    obj.venue = venue
    obj.out = tmp_path
    obj.alert = lambda kind, **fields: alerts.append((kind, fields))
    doc = {"source": "mac", "venue": venue, "epoch": epoch if epoch is not None else time.time(),
           "written_at": "2026-10-30T20:20:25Z", "accounts": book}
    (tmp_path / f"mac_positions_{venue}.json").write_text(json.dumps(doc))
    module.log = logs.append
    return obj, alerts, logs


MAC = {"trb": {"LMT_LONG": {"qty": 1.0, "entry_price": 647.14, "opened_at": "2026-10-29T17:15:34Z", "max_gain": 2.81, "gain": -1.53, "last_signal": "OPEN"}}}


def test_lifecycle_is_attached_and_quantity_is_left_alone(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    pos = {"LMT_LONG": {"qty": 1.0, "price": 0.0}}
    obj.annotate_with_mac("trb", pos)
    assert pos["LMT_LONG"]["qty"] == 1.0
    assert pos["LMT_LONG"]["entry"] == 647.14
    assert pos["LMT_LONG"]["opened_at"] == "2026-10-29T17:15:34Z"
    assert pos["LMT_LONG"]["max_gain"] == 2.81
    assert pos["LMT_LONG"]["price"] == 0.0, "the current mark is the broker's, never the Mac's entry"
    assert alerts == []


def test_a_mac_quantity_that_disagrees_rings_but_the_broker_stands(tmp_path):
    obj, alerts, _ = trader(tmp_path, {"trb": {"LMT_LONG": {"qty": 4.0, "entry_price": 647.14}}})
    pos = {"LMT_LONG": {"qty": 1.0}}
    obj.annotate_with_mac("trb", pos)
    assert pos["LMT_LONG"]["qty"] == 1.0 and pos["LMT_LONG"]["mac_qty"] == 4.0
    assert [k for k, _ in alerts] == ["VD_MAC_POSITION_QTY_DISAGREES"]


def test_a_side_only_the_mac_holds_creates_no_position(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    pos = {}
    obj.annotate_with_mac("trb", pos)
    assert pos == {}, "the Mac book must never add a position the broker does not show"
    assert [k for k, _ in alerts] == ["VD_MAC_HOLDS_WHAT_BROKER_DOES_NOT"]


def test_a_broker_side_absent_from_the_mac_book_rings_once(tmp_path):
    obj, alerts, _ = trader(tmp_path, {"trb": {}})
    pos = {"GILD_LONG": {"qty": 1.0}}
    obj.annotate_with_mac("trb", pos)
    obj.annotate_with_mac("trb", pos)
    assert [k for k, _ in alerts] == ["VD_MAC_POSITION_MISSING"]


def test_a_stale_book_is_not_used(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC, epoch=time.time() - 10 * 3600)
    pos = {"LMT_LONG": {"qty": 1.0}}
    obj.annotate_with_mac("trb", pos)
    assert "entry" not in pos["LMT_LONG"] and "mac" not in pos["LMT_LONG"]
    assert [k for k, _ in alerts] == ["VD_MAC_POSITIONS_STALE"]


def test_no_book_is_not_an_error(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    (tmp_path / "mac_positions_stocks.json").unlink()
    pos = {"LMT_LONG": {"qty": 1.0}}
    assert obj.annotate_with_mac("trb", pos) is pos and alerts == []


def test_a_held_position_without_a_resting_stop_rings_critical(tmp_path):
    obj, alerts, logs = trader(tmp_path, MAC)
    obj._ledger = lambda row: None
    obj.stops = SimpleNamespace(resting_level=lambda acct, symbol, side: None)
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert [k for k, _ in alerts] == ["VD_HELD_POSITION_UNPROTECTED"]
    assert alerts[0][1]["severity"] == "CRITICAL"
    obj.protection_audited["trb"] = 0.0
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert len(alerts) == 1, "one bell per episode, not one per pass"


def test_a_protected_position_rings_nothing_and_rearms_the_bell(tmp_path):
    obj, alerts, _ = trader(tmp_path, MAC)
    obj._ledger = lambda row: None
    obj.stops = SimpleNamespace(resting_level=lambda acct, symbol, side: 625.64)
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert alerts == []
    obj.protection_audited["trb"] = 0.0
    obj.stops = SimpleNamespace(resting_level=lambda acct, symbol, side: None)
    obj.audit_protection("trb", {"LMT_LONG": {"qty": 1.0}})
    assert [k for k, _ in alerts] == ["VD_HELD_POSITION_UNPROTECTED"]


def test_an_alternate_root_reads_the_venue_market_mode_not_the_light_fallback(tmp_path, monkeypatch):
    """trc runs --root ~/trc7d_root, which holds no mode file; it must still size like trb (USER 2026-10-30)."""
    home = tmp_path / "home"
    (home / "binance" / "data").mkdir(parents=True)
    now = 1793390000.0
    stamp = module.dt.datetime.fromtimestamp(now - 60, module.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    (home / "binance" / "data" / "market_mode_stocks.json").write_text(
        json.dumps({"mode": "NORMAL_MODE", "market_index": 12.5, "timestamp": stamp}))
    monkeypatch.setattr(module.Path, "home", staticmethod(lambda: home))
    obj = module.Trader.__new__(module.Trader)
    obj.root = tmp_path / "trc7d_root"
    (obj.root / "data").mkdir(parents=True)
    mode, source = obj.stock_mode(now)
    assert mode == "NORMAL_MODE" and "venue tree" in source
    assert module.STOCK_MODE_START[mode] == 600.0

    obj.root = home / "binance"
    mode, source = obj.stock_mode(now)
    assert mode == "NORMAL_MODE" and "root" in source


def test_a_stale_venue_mode_still_falls_back_to_light(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "binance" / "data").mkdir(parents=True)
    old = module.dt.datetime.fromtimestamp(1793390000.0 - 9999, module.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    (home / "binance" / "data" / "market_mode_stocks.json").write_text(json.dumps({"mode": "EXTREME_MODE", "timestamp": old}))
    monkeypatch.setattr(module.Path, "home", staticmethod(lambda: home))
    obj = module.Trader.__new__(module.Trader)
    obj.root = tmp_path / "trc7d_root"
    (obj.root / "data").mkdir(parents=True)
    assert obj.stock_mode(1793390000.0) == ("LIGHT_MODE", "FALLBACK LIGHT (mode file missing or stale)")


def test_all_three_size_knobs_come_from_the_mode_table_not_a_hardcoded_ceiling():
    """START, MAX_ORDER_VALUE and MAX_POSITION_SIZE all follow the market mode (USER 2026-10-30)."""
    assert module.stock_mode_sizing("LIGHT_MODE") == (250.0, 1000.0, 4000.0)
    assert module.stock_mode_sizing("NORMAL_MODE") == (600.0, 2400.0, 12000.0)
    assert module.stock_mode_sizing("EXTREME_MODE") == (1200.0, 4800.0, 18000.0)
    starts = {m: module.stock_mode_sizing(m)[0] for m in module.STOCK_MODE_START}
    assert starts == module.STOCK_MODE_START, "the local START table must not drift from config_tradier"


def test_trc_rebalances_and_trims_exactly_like_trb():
    assert set(module.REBAL_ACCOUNTS) == {"trb", "trc"}


def test_trc_plans_trb_keys_and_never_its_own(tmp_path):
    for name, syms in (("symbols_trb_long", ["AAPL"]), ("symbols_trb_short", ["MSFT"]),
                       ("symbols_trc_long", ["ZZZZ"]), ("symbols_trc_short", ["YYYY"])):
        (tmp_path / f"{name}.json").write_text(json.dumps(syms))
    out = module.universe(tmp_path, "stocks", ["trb", "trc"])
    assert out["trc"] == {"AAPL_LONG", "MSFT_SHORT"} == out["trb"]
    assert not any(s.startswith(("ZZZZ", "YYYY")) for s in out["trc"])


def seven(tmp_path, sides=None, keys=("AAAUSDT_LONG",), epoch=None):
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    obj = module.Trader.__new__(module.Trader)
    obj.venue = "crypto"
    obj.out = tmp_path
    obj.root = tmp_path
    (tmp_path / "tradeable_keys.json").write_text(json.dumps([f"men:{k}" for k in keys] + [f"fin:{k}" for k in keys]))
    if sides is not None:
        (tmp_path / "sevenday_crypto.json").write_text(json.dumps({"epoch": epoch or time.time(), "sides": sides}))
    return obj


def test_fin_cannot_open_without_a_seven_day_result(tmp_path):
    obj = seven(tmp_path)
    ok, why = obj.sevenday_ok("fin", "AAAUSDT_LONG", time.time())
    assert ok is False and "no 7D result" in why


def test_fin_opens_only_a_fresh_seven_day_men_side(tmp_path):
    now = time.time()
    obj = seven(tmp_path, sides={"AAAUSDT_LONG": now - 3600})
    assert obj.sevenday_ok("fin", "AAAUSDT_LONG", now)[0] is True


def test_a_seven_day_result_past_the_thirty_hour_window_is_refused(tmp_path):
    """USER 2026-10-31 raised 24h -> ~30h: the run starts 20:15 daily, so 30 h leaves a cushion for consistency."""
    now = time.time()
    obj = seven(tmp_path, sides={"AAAUSDT_LONG": now - 31 * 3600})
    ok, why = obj.sevenday_ok("fin", "AAAUSDT_LONG", now)
    assert ok is False and "31.0 h old" in why and "limit 30 h" in why
    obj2 = seven(tmp_path / "in", sides={"AAAUSDT_LONG": now - 25 * 3600})
    assert obj2.sevenday_ok("fin", "AAAUSDT_LONG", now)[0] is True, "25 h is inside the 30 h window"


def test_a_side_outside_mens_book_is_refused_even_when_fresh(tmp_path):
    now = time.time()
    obj = seven(tmp_path, sides={"ZZZUSDT_LONG": now}, keys=("AAAUSDT_LONG",))
    ok, why = obj.sevenday_ok("fin", "ZZZUSDT_LONG", now)
    assert ok is False and "not in men's book" in why


def test_an_unparsable_artifact_refuses_rather_than_guesses(tmp_path):
    obj = seven(tmp_path)
    (tmp_path / "sevenday_crypto.json").write_text("{not json")
    assert obj.sevenday_ok("fin", "AAAUSDT_LONG", time.time())[0] is False


def _latch_env(tmp_path, n_sides, drop=9.0):
    """A per_sym_store the latch can read, plus n fresh states whose 30D replay dropped."""
    import sqlite3
    (tmp_path / "data" / "hourly_reconfig").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "vector_direct").mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "data" / "forward_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(tmp_path / "data" / "hourly_reconfig" / "per_sym_store.db"))
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT, updated_at TEXT, gain_30d REAL, acc_gain_pct REAL)")
    bar = module.last_closed_bar(time.time())
    states = {}
    for i in range(n_sides):
        ss = f"S{i}USDT_LONG"
        con.execute("INSERT INTO per_sym_active VALUES (?,?,?,?)", (ss, "2026-10-28T20:37:29Z", 20.0, None))
        states[ss] = {"ok": True, "npz_tail": bar, "gain_pct": 20.0 - drop, "settings_updated_at": "2026-10-28T20:37:29Z"}
    con.commit(); con.close()
    return state_dir, states, bar


def test_a_mass_latch_is_refused_and_nothing_is_closed(tmp_path):
    state_dir, states, bar = _latch_env(tmp_path, 40, drop=18.0)
    rung = []
    out = module.rolling_deterioration_halts(tmp_path, state_dir, states, bar, alert=lambda k, **f: rung.append((k, f)))
    assert out == {}, "a whole-book latch must latch nothing"
    assert not (tmp_path / "data" / "vector_direct" / "deteriorated_crypto.json").exists()
    assert [k for k, _ in rung] == ["DETERIORATION_MASS_LATCH_REFUSED"]
    assert rung[0][1]["would_latch"] == 40 and rung[0][1]["severity"] == "CRITICAL"


def test_an_ordinary_single_deterioration_still_latches(tmp_path):
    state_dir, states, bar = _latch_env(tmp_path, 40, drop=18.0)
    for i, ss in enumerate(sorted(states)):
        if i >= 2:
            states[ss]["gain_pct"] = 20.0  # unchanged: not a deterioration
    rung = []
    out = module.rolling_deterioration_halts(tmp_path, state_dir, states, bar, alert=lambda k, **f: rung.append((k, f)))
    assert len(out) == 2 and rung == []
    assert json.loads((tmp_path / "data" / "vector_direct" / "deteriorated_crypto.json").read_text()).keys() == out.keys()


def test_drop_below_15_pp_keeps_trading_and_releases_prior_latch(tmp_path):
    state_dir, states, bar = _latch_env(tmp_path, 40, drop=18.0)
    for ss in list(states)[1:]:
        states[ss]["gain_pct"] = 20.0
    out = module.rolling_deterioration_halts(tmp_path, state_dir, states, bar)
    assert len(out) == 1
    first = next(iter(out))
    states[first]["gain_pct"] = 6.0
    assert module.rolling_deterioration_halts(tmp_path, state_dir, states, bar) == {}
    assert json.loads((tmp_path / "data" / "vector_direct" / "deteriorated_crypto.json").read_text()) == {}


# ── the 7D rule is the TWO-ARM COMPARISON ONLY: fin vs men, trc vs trb (USER 2026-10-31) ──

def test_only_fin_and_trc_are_ever_gated():
    """USER: "ONLY ... these 2 accounts NEVER ANYWHERE ELSE ... if you start trading 7D on all accounts the system is
    fucked". Gating a control account destroys the comparison it is the control for."""
    assert set(module.SEVENDAY_ONLY_ACCOUNTS) == {"fin", "trc"}
    assert set(module.SEVENDAY_STRICT_ACCOUNTS) == {"fin", "trc"}
    assert module.SEVENDAY_BOOK == {"fin": "men", "trc": "trb"}


def test_the_control_accounts_are_never_touched_by_the_gate(tmp_path):
    now = time.time()
    obj = seven(tmp_path, sides={"AAAUSDT_LONG": now}, keys=("AAAUSDT_LONG",))
    for control in ("men", "ang", "flz", "inf", "tra", "trb"):
        assert obj.sevenday_ok(control, "ZZZUSDT_SHORT", now) == (True, ""), f"{control} must trade its own 30D settings"


def test_each_arm_is_restricted_to_its_controls_book(tmp_path):
    now = time.time()
    obj = seven(tmp_path, sides={"AAAUSDT_LONG": now}, keys=("AAAUSDT_LONG",))
    assert obj.sevenday_ok("fin", "AAAUSDT_LONG", now)[0] is True
    ok, why = obj.sevenday_ok("fin", "NOTINBOOK_LONG", now)
    assert ok is False and "not in men's book" in why


def test_both_arms_stay_flat_when_the_lane_publishes_nothing(tmp_path):
    """Strict: an arm that quietly traded 30D sets would be a duplicate of its control, not a comparison."""
    obj = seven(tmp_path)
    obj.alert = lambda kind, **f: None
    for arm in ("fin", "trc"):
        ok, why = obj.sevenday_ok(arm, "AAAUSDT_LONG", time.time())
        assert ok is False, f"{arm} must stay flat with nothing published, got {why}"


def test_fin_plans_from_its_own_7d_states_never_mens_30d_ones(tmp_path):
    """USER 2026-10-31: fin must TRADE 7D settings; authorising it while feeding it 30D states would duplicate men."""
    assert module.SEVENDAY_STATE_DIRS == {"fin": "forward_state_fin7d"}
    s30 = tmp_path / "data" / "forward_state"
    s7 = tmp_path / "data" / "forward_state_fin7d"
    s30.mkdir(parents=True)
    s7.mkdir(parents=True)
    (s30 / "AWEUSDT_LONG.json").write_text(json.dumps({"ok": True, "src": "30d", "settings_src": "per_sym_store.active"}))
    (s7 / "AWEUSDT_LONG.json").write_text(json.dumps({"ok": True, "src": "7d", "settings_src": "per_sym_store_7d.active"}))
    obj = module.Trader.__new__(module.Trader)
    obj.state_dir = s30
    assert obj.load_states({"AWEUSDT_LONG"})["AWEUSDT_LONG"]["src"] == "30d"
    assert obj.load_states({"AWEUSDT_LONG"}, obj.state_dir.parent / module.SEVENDAY_STATE_DIRS["fin"])["AWEUSDT_LONG"]["src"] == "7d"


def test_a_held_fin_side_outside_the_universe_is_managed_from_its_7d_state(tmp_path):
    s30 = tmp_path / "data" / "forward_state"
    s7 = tmp_path / "data" / "forward_state_fin7d"
    s30.mkdir(parents=True)
    s7.mkdir(parents=True)
    (s7 / "HELD_LONG.json").write_text(json.dumps({"ok": True, "src": "7d"}))
    obj = module.Trader.__new__(module.Trader)
    obj.state_dir = s30
    states = obj.with_held_states({}, {"HELD_LONG": {"qty": 1.0}}, s7)
    assert states["HELD_LONG"]["src"] == "7d"
