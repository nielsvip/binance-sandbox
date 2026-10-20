"""USER LAW 2026-10-20: coverage metric, catch-up hysteresis, throughput line, V18_CATCHUP launch flag."""

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_fleet_scheduler as S

NOW = datetime.datetime(2026, 10, 20, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(ss, age_h, lag_min, host="niels", ts_last=None):
    last = ts_last if ts_last is not None else NOW - age_h * 3600 - 3600
    return [ss, _iso(NOW - age_h * 3600), host, last, (last + lag_min * 60) * 1e9, None]


def test_coverage_counts_fresh_board_on_fresh_npz_only():
    allowed = ["AAAUSDT_LONG", "AAAUSDT_SHORT", "BBBUSDT_LONG", "CCCUSDT_LONG"]
    rows = [_row("AAAUSDT_LONG", 2, 7), _row("AAAUSDT_SHORT", 2, 45), _row("BBBUSDT_LONG", 30, 5)]
    c = S._coverage(allowed, rows, NOW)
    assert c["n"] == 4 and c["covered"] == 1 and c["coverage"] == 0.25
    assert c["published_24h"] == 2
    pc = c["per_cat"]["CRYPTO_LONG"]
    assert pc["n"] == 3 and pc["covered"] == 1 and pc["never_or_old"] == 2
    assert c["per_cat"]["CRYPTO_SHORT"]["published_but_npz_stale"] == 1


def test_newest_manifest_per_side_wins():
    rows = [_row("AAAUSDT_LONG", 30, 5), _row("AAAUSDT_LONG", 1, 5)]
    assert S._coverage(["AAAUSDT_LONG"], rows, NOW)["covered"] == 1


def test_manifest_npz_age_field_wins():
    r = _row("AAAUSDT_LONG", 1, 90)
    r[5] = 3.0
    assert S._coverage(["AAAUSDT_LONG"], [r], NOW)["covered"] == 1


def test_empty_universe_is_full_coverage():
    assert S._coverage([], [], NOW)["coverage"] == 1.0


def test_hysteresis_does_not_flap():
    m = False
    seq = []
    for cov in (0.9, 0.79, 0.85, 0.94, 0.95, 0.9, 0.7):
        m = S._select_catchup_mode(m, cov)
        seq.append(m)
    assert seq == [False, True, True, True, False, False, True]


def test_throughput_per_host_and_both_sides():
    rows = [_row("AAAUSDT_LONG", 0.2, 5), _row("AAAUSDT_SHORT", 0.5, 5, host="s2"), _row("BBBUSDT_LONG", 0.5, 5), _row("CCCUSDT_LONG", 3, 5)]
    t = S._throughput(rows, NOW)
    assert t["published_per_host"] == {"s1": 2, "s2": 1}
    assert t["symbols_both_sides"] == 1 and t["target_per_host"] == 12


def test_side_cmd_carries_flag_only_in_catchup_mode():
    host = {"name": "s1", "root": "~/binance-sandbox", "template_dir": "SPREADSHEETS"}
    S._CATCHUP_MODE[0] = False
    assert "V18_CATCHUP=1" not in S.side_cmd(host, "AAA", "LONG", "30D", "/tmp/p", 1, 3)
    S._CATCHUP_MODE[0] = True
    try:
        assert "V18_CATCHUP=1" in S.side_cmd(host, "AAA", "LONG", "30D", "/tmp/p", 1, 3)
    finally:
        S._CATCHUP_MODE[0] = False


def test_qualifying_age_ignores_boards_on_stale_npz():
    rows = [_row("AAAUSDT_LONG", 5, 90), _row("AAAUSDT_LONG", 50, 5)]
    a = S._qualifying_ages(["AAAUSDT_LONG", "BBBUSDT_LONG"], rows, NOW)
    assert round(a["AAAUSDT_LONG"]) == 50 and a["BBBUSDT_LONG"] is None


def test_catchup_rules_any_9d_and_trading_4d():
    h = 24.0
    ok = {"A_LONG": 3 * h, "B_LONG": 8 * h}
    assert S._catchup_rules(ok, {"A_LONG"})["mode"] is False
    assert S._catchup_rules({**ok, "C_LONG": 9.5 * h}, set())["mode"] is True
    r = S._catchup_rules({**ok, "D_LONG": 4.5 * h}, {"D_LONG"})
    assert r["mode"] is True and r["n_trading_over"] == 1
    assert S._catchup_rules({**ok, "D_LONG": 4.5 * h}, set())["mode"] is False
    n = S._catchup_rules({"E_LONG": None}, set())
    assert n["mode"] is True and n["max_age_any_h"] == "never"


def test_refresh_priority_classes_and_cadence():
    h = 24.0
    ages = {"V_LONG": 10 * h, "T_LONG": 5 * h, "G1_LONG": 7.0, "G2_LONG": 13.0, "G3_LONG": 2.0, "L_LONG": 3 * h, "N_LONG": None}
    live = {"G1_LONG": {"pnl_usd": 100.0}, "G2_LONG": {"pnl_usd": 50.0}, "G3_LONG": {"pnl_usd": 10.0}, "L_LONG": {"pnl_usd": -5.0}}
    pr, table, cad = S._refresh_priority(ages, live, {"T_LONG"})
    order = sorted(pr, key=lambda k: pr[k])
    assert order[0] == "N_LONG" or pr["N_LONG"][0] == 0
    assert pr["T_LONG"][0] == 0 and pr["V_LONG"][0] == 0 and pr["T_LONG"] < pr["V_LONG"]
    assert pr["G1_LONG"][0] == 1 and pr["G2_LONG"][0] == 1
    assert pr["G3_LONG"][0] == 2
    assert pr["L_LONG"][0] == 2
    assert [t["sym_side"] for t in table][:3] == ["G1_LONG", "G2_LONG", "G3_LONG"]
    assert table[0]["cadence_h"] == 6.0 and table[1]["cadence_h"] == 12.0 and table[2]["cadence_h"] == 24.0


def test_place_order_age_key_orders_violators_first():
    key = lambda b: {"OLD": (0, 0.0, -300.0), "MID": (1, 0.0, 0.0)}.get(b, (2, 0.0, 0.0))
    seq = S._place_order([], [], ["ZZZ", "MID", "OLD"], {}, age_key=key)
    assert [s for s, _ in seq][:2] == ["OLD", "MID"]


def _fixture20():
    h = 24.0
    ages = {}
    live = {}
    trading = set()
    for i in range(20):
        ss = f"S{i:02d}_LONG"
        ages[ss] = [None, 12 * h, 2.0, 5 * h, 30.0, 8.0, 3 * h, 11 * h, 100.0, 7.0][i % 10] if i < 10 else [1.0, 5.0, 26.0, 50.0, 6 * h, 10 * h, 7.0, 13.0, None, 40.0][i - 10]
    live.update({"S02_LONG": {"pnl_usd": 90.0}, "S05_LONG": {"pnl_usd": 40.0}, "S10_LONG": {"pnl_usd": 20.0}, "S13_LONG": {"pnl_usd": 5.0}, "S04_LONG": {"pnl_usd": -3.0}})
    trading.update({"S03_LONG", "S04_LONG", "S14_LONG"})
    return ages, live, trading


def test_order_fixture_20_sym_sides():
    ages, live, trading = _fixture20()
    pr, table, cad = S._refresh_priority(ages, live, trading)
    syms = [k.rsplit("_", 1)[0] for k in ages]
    key = lambda b: pr[b + "_LONG"]
    seq = [s for s, _ in S._place_order([], [], syms, {}, age_key=key)]
    classes = [pr[s + "_LONG"][0] for s in seq]
    assert classes == sorted(classes)
    viol = [s for s in seq if pr[s + "_LONG"][0] == 0]
    assert viol[:2] == ["S14", "S03"] and viol.index("S00") > 1  # trading 4d+ violators first (oldest first), then 9d-only violators incl. never-boarded
    assert pr["S03_LONG"][0] == 0 and pr["S03_LONG"][1] == 0.0  # trading, 5 d
    assert pr["S15_LONG"][0] == 0 and pr["S15_LONG"][1] == 1.0  # 10 d, not trading
    assert S._rule_label(pr["S03_LONG"]) == "violator-trading-4d" and S._rule_label(pr["S15_LONG"]) == "violator-9d"
    assert S._rule_label(pr["S02_LONG"], cad.get("S02_LONG")).startswith("gainer-due-6h") or pr["S02_LONG"][0] == 2
    assert all(c in (0, 1, 2) for c in classes)
    due = [s for s in seq if pr[s + "_LONG"][0] == 1]
    gains = [live[s + "_LONG"]["pnl_usd"] for s in due]
    assert gains == sorted(gains, reverse=True)
    rest = [s for s in seq if pr[s + "_LONG"][0] == 2]
    ra = [ages[s + "_LONG"] for s in rest]
    assert ra == sorted(ra, reverse=True)


def test_order_audit_flags_unexplained_skip_only():
    order = {"s1": ["A", "B", "C", "D"]}
    explain = lambda hn, sy: {"B": "host-skip: mem guard"}.get(sy)
    ok = S._order_audit(order, [("s1", "C")], explain, lambda s: "oldest", lambda s: 1.0)
    assert [v["unlaunched_ahead"] for v in ok["violations"]] == [["A"]]
    ok2 = S._order_audit(order, [("s1", "A"), ("s1", "C")], explain, lambda s: "oldest", lambda s: 1.0)
    assert ok2["violations"] == [] and ok2["checks"][1]["skipped_ahead"] == {"B": "host-skip: mem guard"}
    assert ok2["top"]["s1"][0]["sym"] == "A"


def test_trading_file_sides(tmp_path):
    (tmp_path / "v15_trading_symsides.txt").write_text("AAPL_LONG\nAAPL_SHORT\n")
    (tmp_path / "v15_priority_front.txt").write_text("MA,QQQ\n")
    got = S._trading_file_sides(str(tmp_path))
    assert got == {"AAPL_LONG", "AAPL_SHORT", "MA_LONG", "MA_SHORT", "QQQ_LONG", "QQQ_SHORT"}
