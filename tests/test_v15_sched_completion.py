"""2026-10-22 spec 22 §6.1 / §7.2(5): round-completion record, one universe, roll guard, selection classes, per-launch budget."""

import ast
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_fleet_scheduler as S

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc).timestamp()
H = 24.0


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(ss, age_h, lag_min=5, mode="slow", host="s2", start=1.0, final=5.0):
    last = NOW - age_h * 3600 - 3600
    return [ss, _iso(NOW - age_h * 3600), host, last, (last + lag_min * 60) * 1e9, None, mode, "md5x", start, final, last]


def _must(carried=0, codes=("DEAD_VEC_PATH",), done=True):
    return {"verdict": None, "final_gain": 5.0, "initial_baseline_gain": 1.0, "result_done_utc": "2026-10-22T11:00:00Z" if done else None, "pilot_md5": "p",
            "must_present": True, "n_items": 100, "n_evaluated": 60, "n_carried_over": carried, "n_refused": 40, "refused_sample_n": 4, "refusal_codes": sorted(codes)}


def _uni(sides):
    return {"sym_sides": sorted(sides), "source": "test", "n": len(sides), "alternatives": {}}


def test_board_budget_rules():
    assert S.board_budget(None, True, True, 0.1) == 0  # never-boarded: full first board
    assert S.board_budget(300.0, False, False, 0.5) == 12000  # > 9 d
    assert S.board_budget(100.0, True, False, 0.5) == 12000  # trading > 4 d
    assert S.board_budget(100.0, False, False, 0.5) == 0  # not trading, < 9 d
    assert S.board_budget(300.0, True, False, 0.95) == 0  # coverage >= 0.95
    assert S.board_budget(300.0, True, False, 0.5, slow_forced=True) == 0
    assert S.board_budget(300.0, False, False, 0.5, evals=500) == 500
    assert S.board_budget(300.0, False, False, 0.5, any_h=float("inf"), trading_h=float("inf")) == 0
    assert S._budget_mode_label(0) == "full" and S._budget_mode_label(12000) == "budget:12000"


def test_refresh_priority_never_class_and_real_violators():
    ages = {"N1_LONG": None, "N2_LONG": None, "NT_LONG": None, "V_LONG": 10 * H, "T_LONG": 5 * H, "G_LONG": 7.0, "R_LONG": 3 * H}
    live = {"G_LONG": {"pnl_usd": 10.0}}
    la = {"N1_LONG": 2000.0, "N2_LONG": 1000.0}
    pr, _, cad = S._refresh_priority(ages, live, {"T_LONG", "NT_LONG"}, never_class=True, last_activity=la)
    order = sorted(pr, key=lambda k: pr[k])
    assert order == ["T_LONG", "V_LONG", "G_LONG", "NT_LONG", "N2_LONG", "N1_LONG", "R_LONG"]
    assert all(pr[k][0] == 0 for k in ("T_LONG", "V_LONG")) and all(pr[k][0] == S._NEVER_CLASS for k in ("N1_LONG", "N2_LONG", "NT_LONG"))
    assert S._rule_label(pr["NT_LONG"]) == "never-boarded-trading" and S._rule_label(pr["N1_LONG"]) == "never-boarded"
    legacy, _, _ = S._refresh_priority(ages, live, {"T_LONG"})
    assert legacy["N1_LONG"][0] == 0  # default unchanged


def test_catchup_rules_ignore_never_when_asked():
    r = S._catchup_rules({"E_LONG": None, "A_LONG": 2 * H}, set(), count_never=False)
    assert r["mode"] is False and r["n_never"] == 1 and r["max_age_any_h"] == 48.0
    r2 = S._catchup_rules({"E_LONG": None, "A_LONG": 10 * H}, set(), count_never=False)
    assert r2["mode"] is True and r2["over_any"] == ["A_LONG"]
    assert S._catchup_rules({"E_LONG": None}, set())["mode"] is True  # legacy default


def test_select_catchup_mode_excludes_never_from_denominator():
    assert S._select_catchup_mode(True, 0.90) is True
    assert S._select_catchup_mode(True, 0.90, n=100, n_never=10) is False  # 90 of 90 boardable covered -> clears
    assert S._select_catchup_mode(False, 0.70, n=100, n_never=20) is False  # 70/80 = 0.875, not < 0.80
    assert S._select_catchup_mode(False, 0.60, n=100, n_never=20) is True  # 60/80 = 0.75 < 0.80
    assert S._select_catchup_mode(False, 0.70) is True  # legacy call unchanged


def test_completion_complete_and_pending_reasons():
    uni = _uni(["AAAUSDT_LONG", "AAAUSDT_SHORT", "BBBUSDT_LONG", "CCCUSDT_LONG", "DDDUSDT_LONG", "EEEUSDT_LONG"])
    rows = [_row("AAAUSDT_LONG", 2), _row("AAAUSDT_SHORT", 2, mode="catchup"), _row("BBBUSDT_LONG", 2), _row("CCCUSDT_LONG", 2, lag_min=90), _row("DDDUSDT_LONG", 2)]
    must = {"s2": {"AAAUSDT_LONG": _must(), "AAAUSDT_SHORT": _must(), "BBBUSDT_LONG": _must(carried=7), "DDDUSDT_LONG": _must(codes=("PARSE", "DEAD_VEC_PATH"))}}
    rec = S.build_round_completion("run35", uni, rows, must, {"EEEUSDT_LONG": "delisted"}, NOW)
    ss = rec["sym_sides"]
    assert ss["AAAUSDT_LONG"]["verdict"] == "COMPLETE" and ss["AAAUSDT_LONG"]["complete_at"] == _iso(NOW - 2 * 3600)
    assert ss["AAAUSDT_LONG"]["must_carried"] == 0 and ss["AAAUSDT_LONG"]["published_gain"] == 5.0 and ss["AAAUSDT_LONG"]["mode"] == "slow"
    assert ss["AAAUSDT_SHORT"]["complete_at"] is None and "no full board" in ss["AAAUSDT_SHORT"]["verdict"]  # catch-up never counts
    assert "must_carried=7" in ss["BBBUSDT_LONG"]["verdict"]
    assert ss["CCCUSDT_LONG"]["must_carried"] == "no_progress_this_round" and ss["CCCUSDT_LONG"]["complete_at"] is None  # stale-NPZ board
    assert ss["DDDUSDT_LONG"]["verdict"].startswith("REFUSED_ONLY") and "PARSE" in ss["DDDUSDT_LONG"]["verdict"]
    assert ss["EEEUSDT_LONG"]["verdict"] == "EXCLUDED"
    assert rec["complete"] is False and rec["n_complete"] == 1 and rec["n_excluded"] == 1 and rec["n_refused_only"] == 1 and rec["n_pending"] == 3
    pc = rec["per_cat_side"]["CRYPTO_LONG"]
    assert pc == {"n": 5, "complete": 1, "pending": 2, "excluded": 1, "refused_only": 1}


def test_completion_true_only_when_all_done():
    uni = _uni(["AAAUSDT_LONG", "BBBUSDT_LONG"])
    rec = S.build_round_completion("run35", uni, [_row("AAAUSDT_LONG", 1)], {"s2": {"AAAUSDT_LONG": _must()}}, {"BBBUSDT_LONG": "no NPZ"}, NOW)
    assert rec["complete"] is True
    rec2 = S.build_round_completion("run35", _uni([]), [], {}, {}, NOW)
    assert rec2["complete"] is False  # an empty universe is never "complete"


def test_completion_not_visible_and_newest_progress_wins():
    uni = _uni(["AAAUSDT_LONG", "BBBUSDT_LONG"])
    must = {"s2": {"AAAUSDT_LONG": _must(carried=3, done=False)}, "s5": {"AAAUSDT_LONG": _must()}}
    rec = S.build_round_completion("run35", uni, [_row("AAAUSDT_LONG", 1), _row("BBBUSDT_LONG", 1)], must, {}, NOW, hosts_not_visible=["s6"])
    assert rec["sym_sides"]["AAAUSDT_LONG"]["progress_host"] == "s5" and rec["sym_sides"]["AAAUSDT_LONG"]["verdict"] == "COMPLETE"
    assert rec["sym_sides"]["BBBUSDT_LONG"]["must_carried"] == "not_visible" and rec["hosts_not_visible"] == ["s6"]


def test_newest_full_board_wins_over_older_and_unfinished_round_board():
    uni = _uni(["AAAUSDT_LONG"])
    rows = [_row("AAAUSDT_LONG", 30, start=0.5, final=2.0), _row("AAAUSDT_LONG", 3, start=1.0, final=6.0), _row("AAAUSDT_LONG", 1, mode="catchup")]
    rec = S.build_round_completion("run35", uni, rows, {"s2": {"AAAUSDT_LONG": _must(done=False)}}, {}, NOW)
    e = rec["sym_sides"]["AAAUSDT_LONG"]
    assert e["complete_at"] == _iso(NOW - 3 * 3600) and e["published_gain"] == 6.0 and e["qualifying_boards"] == {"full": 2, "not_full": 1}
    assert "board unfinished this round" in e["verdict"]


def test_tradeable_universe_one_source_alternatives_counted():
    u = S.tradeable_universe(["AAA_LONG", "bbb_SHORT"], full_sets={"AAA_LONG": {}, "ZZZ_SHORT": {}, "meta": {}}, symbol_level={"AAA", "CCC"})
    assert u["sym_sides"] == ["AAA_LONG", "BBB_SHORT"] and u["n"] == 2 and u["source"].startswith("scheduler allowed_sym_sides")
    assert u["alternatives"]["per_sym_full_sets_keys"] == {"n": 2, "in_chosen": 1, "not_in_chosen": 1}
    assert u["alternatives"]["symbol_level_both_sides"]["n"] == 4 and u["alternatives"]["symbol_level_both_sides"]["in_chosen"] == 1
    u2 = S.tradeable_universe(["AAA_LONG"], full_sets=False, symbol_level=set())
    assert u2["alternatives"]["per_sym_full_sets_keys"]["n"] is None


def test_round_roll_decision_refuses_only_launchable_pending():
    base = {"round": "run35", "generated_at": _iso(NOW - 60), "complete": False, "launchable_known": True,
            "per_cat_side": {"CRYPTO_LONG": {"n": 3, "pending": 2, "refused_only": 0, "complete": 1, "excluded": 0}}}
    ok, msg = S.round_roll_decision({**base, "n_launchable_pending": 2, "launchable_pending": ["A_LONG", "B_LONG"]}, False, NOW)
    assert ok is False and msg.startswith("REFUSED") and '"pending": 2' in msg
    assert S.round_roll_decision({**base, "n_launchable_pending": 2}, True, NOW)[0] is True  # force
    ok2, msg2 = S.round_roll_decision({**base, "n_launchable_pending": 0}, False, NOW)
    assert ok2 is True and "exhausted" in msg2  # incomplete but nothing launchable -> roll
    assert S.round_roll_decision({**base, "complete": True, "n_launchable_pending": 0}, False, NOW)[0] is True
    assert S.round_roll_decision({**base, "n_launchable_pending": 5, "generated_at": _iso(NOW - 7200)}, False, NOW)[0] is True  # stale -> never blocks
    assert S.round_roll_decision({**base, "n_launchable_pending": 5, "launchable_known": False}, False, NOW)[0] is True
    assert S.round_roll_decision(None, False, NOW)[0] is True


def test_round_roll_check_cli_freezes_on_roll(tmp_path, monkeypatch):
    (tmp_path / "v15_current_progress_dir.txt").write_text("/home/niels/v15_run35_20261022/progress\n")
    rep = tmp_path / "reports"
    rep.mkdir()
    monkeypatch.delenv("V15_ROUND_ROLL_FORCE", raising=False)
    fp = rep / "full_course_round_run35.json"
    rec = {"round": "run35", "generated_at": _iso(NOW - 10), "complete": False, "launchable_known": True, "n_launchable_pending": 1, "launchable_pending": ["A_LONG"],
           "per_cat_side": {}, "sym_sides": {"A_LONG": {"verdict": "PENDING: x"}, "B_LONG": {"verdict": "COMPLETE"}}}
    fp.write_text(json.dumps(rec))
    assert S._round_roll_check_cli(home=str(tmp_path), reports=str(rep), now_ts=NOW) == 3
    assert not json.loads(fp.read_text()).get("frozen")
    monkeypatch.setenv("V15_ROUND_ROLL_FORCE", "1")
    assert S._round_roll_check_cli(home=str(tmp_path), reports=str(rep), now_ts=NOW) == 0
    fr = json.loads(fp.read_text())
    assert fr["frozen"] is True and fr["pending_at_freeze"] == ["A_LONG"] and fr["complete"] is False
    assert S.write_round_completion({"round": "run35", "complete": True}, out_dir=rep) is None  # frozen record never overwritten
    assert json.loads(fp.read_text())["complete"] is False


def test_carried_from_previous_round(tmp_path):
    (tmp_path / "full_course_round_run34.json").write_text(json.dumps({"round": "run34", "sym_sides": {"AAAUSDT_LONG": {"verdict": "PENDING: x"}, "BBBUSDT_LONG": {"verdict": "COMPLETE"},
                                                                                                         "ZZZUSDT_LONG": {"verdict": "REFUSED_ONLY: y"}}}))
    carry = S._previous_round_carry("run35", reports=tmp_path)
    assert carry == ("run34", {"AAAUSDT_LONG": "PENDING: x", "ZZZUSDT_LONG": "REFUSED_ONLY: y"})
    rec = S.build_round_completion("run35", _uni(["AAAUSDT_LONG"]), [_row("AAAUSDT_LONG", 1)], {"s2": {"AAAUSDT_LONG": _must()}}, {}, NOW, carried_from_previous=carry)
    assert rec["n_carried_from_previous_round"] == 2 and rec["n_carried_resolved"] == 1
    assert {c["sym_side"]: c["now"] for c in rec["carried_from_previous_round"]} == {"AAAUSDT_LONG": "COMPLETE", "ZZZUSDT_LONG": "NOT_IN_UNIVERSE"}
    assert S._previous_round_carry("run35", reports=tmp_path / "nope") is None


def test_launchable_pending_and_outside_histogram():
    uni = _uni(["AAAUSDT_LONG", "BBBUSDT_LONG", "CCCUSDT_LONG", "DDDUSDT_LONG"])
    must = {"s2": {"DDDUSDT_LONG": _must(codes=("PARSE", "LIVE_DEAD_KEY", "MAXAUG_NEVER_ZERO", "UNWIRED_X", "TYPE_MISMATCH"))}}
    rec = S.build_round_completion("run35", uni, [_row("DDDUSDT_LONG", 1)], must, {}, NOW, launchable_syms={"AAAUSDT", "BBBUSDT"}, running={"BBBUSDT_LONG"})
    assert rec["launchable_pending"] == ["AAAUSDT_LONG"] and rec["running_pending"] == ["BBBUSDT_LONG"] and rec["launchable_known"] is True
    assert rec["refusal_reasons_outside_allowlist"] == {"PARSE": 1} and rec["sym_sides"]["DDDUSDT_LONG"]["verdict"] == "REFUSED_ONLY: non-structural refusal codes PARSE"
    assert S.build_round_completion("run35", uni, [], {}, {}, NOW)["launchable_known"] is False
    for c in ("LIVE_DEAD_KEY", "DEAD_VEC_PATH", "SELF_PAIR", "MAXAUG_NEVER_ZERO", "ABLATION_IS_A_LEVER", "PROTECTED_STOP_OFF", "HARD_SET", "NOT_WIRED_VEC"):
        assert S._is_structural_refusal(c), c
    assert not S._is_structural_refusal("PARSE") and not S._is_structural_refusal("EMPTY") and not S._is_structural_refusal("")


def test_round_id_precedence(tmp_path):
    envp = tmp_path / "env.json"
    envp.write_text(json.dumps({"round": "run34", "env": {"V18_CATCHUP": "0"}}))
    assert S._completion_round_id("/home/niels/v15_run35_20261022/progress", env_path=envp, home=str(tmp_path)) == ("run35", "current_progress_dir", "run34")
    assert S._completion_round_id(None, env_path=envp, home=str(tmp_path))[0] == "run34"
    (tmp_path / "v15_run9_x").mkdir()
    (tmp_path / "v15_run12_x").mkdir()
    assert S._completion_round_id(None, env_path=tmp_path / "missing.json", home=str(tmp_path))[:2] == ("run12", "newest_run_dir")
    assert S._round_forces_slow("run34", envp) is True and S._round_forces_slow("run35", envp) is False


def test_round_exclusions_shapes(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"round": "run35", "exclusions": {"aaa_long": "delisted", "BBB_LONG": ""}}))
    assert S._load_round_exclusions("run35", p) == {"AAA_LONG": "delisted"}
    assert S._load_round_exclusions("run36", p) == {}
    p.write_text(json.dumps({"exclusions": [{"sym_side": "CCC_SHORT", "reason": "no NPZ"}]}))
    assert S._load_round_exclusions("run35", p) == {"CCC_SHORT": "no NPZ"}
    p.write_text(json.dumps({"DDD_LONG": "0 trades full history"}))
    assert S._load_round_exclusions("run35", p) == {"DDD_LONG": "0 trades full history"}
    assert S._load_round_exclusions("run35", tmp_path / "none.json") == {}


def test_write_round_completion_atomic(tmp_path):
    p = S.write_round_completion({"round": "run35", "complete": False}, out_dir=tmp_path)
    assert p.name == "full_course_round_run35.json" and json.loads(p.read_text())["round"] == "run35"
    assert not list(tmp_path.glob("*.tmp*"))
    assert S.write_round_completion({"round": None}, out_dir=tmp_path) is None


def test_side_cmd_budget_env_per_launch():
    host = {"name": "s1", "root": "~/binance-sandbox", "template_dir": "SPREADSHEETS"}
    S._SS_BUDGET.clear()
    S._SS_BUDGET.update({"AAA_LONG": 12000, "AAA_SHORT": 0})
    S._CATCHUP_MODE[0] = True
    try:
        lng = S.side_cmd(host, "AAA", "LONG", "30D", "/tmp/p", 1, 3)
        sht = S.side_cmd(host, "AAA", "SHORT", "30D", "/tmp/p", 1, 3)
        assert "V15_BOARD_BUDGET=12000" in lng and "V18_CATCHUP=1" in lng
        assert "V15_BOARD_BUDGET=0" in sht and "V18_CATCHUP=1" not in sht
    finally:
        S._CATCHUP_MODE[0] = False
        S._SS_BUDGET.clear()


def test_order_audit_class_and_mode_per_launch():
    order = {"s1": ["A", "B"]}
    a = S._order_audit(order, [("s1", "A"), ("s2", "Z")], lambda h, s: None, lambda s: "oldest", lambda s: 1.0, class_of=lambda s: 1.5, mode_of=lambda h, s: {"LONG": "full"})
    assert a["checks"][0]["class"] == 1.5 and a["checks"][0]["mode_per_launch"] == {"LONG": "full"}
    assert set(a["mode_per_launch"]) == {"s1:A", "s2:Z"} and a["top"]["s1"][0]["class"] == 1.5


def test_decision_log_keeps_at_least_7_days(tmp_path):
    p = tmp_path / "d.jsonl"
    st = {}
    for age_d in (20, 10, 6, 1):
        S._append_decision_log(p, {"ts_epoch": NOW - age_d * 86400}, NOW - age_d * 86400, keep_d=14, prune_every_s=1e12, state=st)
    st["decisions_pruned_at"] = 0.0
    S._append_decision_log(p, {"ts_epoch": NOW}, NOW, keep_d=3, state=st)  # keep_d < 7 is raised to 7
    ages = sorted(round((NOW - json.loads(x)["ts_epoch"]) / 86400) for x in p.read_text().splitlines())
    assert ages == [0, 1, 6]


def test_progress_must_host_helper(tmp_path):
    tree = ast.parse(S.HOST_PY)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_progress_must")
    ns = {"json": json}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<host_py>", "exec"), ns)
    f = tmp_path / "X_LONG_v14_progress.json"
    f.write_text(json.dumps({"verdict": None, "final_gain": 3.0, "result_done_utc": "2026-10-22T10:00:00Z", "result_pilot_md5": "pm",
                             "v18": {"must": {"n_items": 10, "n_evaluated": 4, "n_carried_over": 0, "n_refused": 6,
                                              "refused_sample": {"MUST:a": "DEAD_VEC_PATH: x", "MUST:b": "NOT_A_TEMPLATE_KEY:MUST:Y", "MUST:c": "LIVE_DEAD_KEY: z"}}}}))
    m = ns["_progress_must"](str(f))
    assert m["n_items"] == 10 and m["n_carried_over"] == 0 and m["refusal_codes"] == ["DEAD_VEC_PATH", "LIVE_DEAD_KEY", "NOT_A_TEMPLATE_KEY"] and m["must_present"] is True
    f.write_text(json.dumps({"final_gain": 1.0}))
    assert ns["_progress_must"](str(f))["must_present"] is False
    f.write_text("{bad")
    assert ns["_progress_must"](str(f)) is None


def test_mode_none_counts_full_without_catchup_evidence():
    uni = _uni(["AAAUSDT_LONG", "BBBUSDT_LONG", "CCCUSDT_LONG", "DDDUSDT_LONG", "EEEUSDT_LONG"])
    rows = [_row("AAAUSDT_LONG", 2, mode=None), _row("BBBUSDT_LONG", 2, mode=None), _row("CCCUSDT_LONG", 2, mode=None), _row("DDDUSDT_LONG", 2, mode="budget:12000"),
            _row("EEEUSDT_LONG", 2, mode="catchup")]
    pub = NOW - 2 * 3600
    m_ev = dict(_must(), catchup=True, result_done_utc=_iso(pub - 60))
    must = {"s2": {"AAAUSDT_LONG": _must(), "BBBUSDT_LONG": m_ev, "CCCUSDT_LONG": _must(), "DDDUSDT_LONG": _must(), "EEEUSDT_LONG": _must()}}
    launches = {"CCCUSDT_LONG": [(pub - 7200, True)]}
    rec = S.build_round_completion("run35", uni, rows, must, {}, NOW, catchup_launches=launches)
    ss = rec["sym_sides"]
    assert ss["AAAUSDT_LONG"]["verdict"] == "COMPLETE" and ss["AAAUSDT_LONG"]["full_board_rule"] == "mode=None counted full (no catch-up evidence)"
    assert ss["BBBUSDT_LONG"]["complete_at"] is None  # progress shows catch-up, finished at publish time
    assert ss["CCCUSDT_LONG"]["complete_at"] is None  # its launch carried V18_CATCHUP=1
    assert ss["DDDUSDT_LONG"]["complete_at"] is None and ss["EEEUSDT_LONG"]["complete_at"] is None
    later = {"CCCUSDT_LONG": [(pub - 7200, True), (pub - 3600, False)]}
    assert S.build_round_completion("run35", uni, rows, must, {}, NOW, catchup_launches=later)["sym_sides"]["CCCUSDT_LONG"]["verdict"] == "COMPLETE"  # last launch was full
    far = dict(m_ev, result_done_utc=_iso(pub + 10 * 3600))
    assert S.build_round_completion("run35", uni, rows, {"s2": {**must["s2"], "BBBUSDT_LONG": far}}, {}, NOW)["sym_sides"]["BBBUSDT_LONG"]["verdict"] == "COMPLETE"  # evidence is a different board


def test_blind_host_keeps_previous_must_data():
    uni = _uni(["AAAUSDT_LONG"])
    rows = [_row("AAAUSDT_LONG", 1)]
    first = S.build_round_completion("run35", uni, rows, {"s1": {"AAAUSDT_LONG": _must()}}, {}, NOW)
    assert first["sym_sides"]["AAAUSDT_LONG"]["verdict"] == "COMPLETE"
    blind = S.build_round_completion("run35", uni, rows, {"s2": {}}, {}, NOW, hosts_not_visible=["s1"], previous_record=first)
    e = blind["sym_sides"]["AAAUSDT_LONG"]
    assert e["verdict"] == "COMPLETE" and e["must_source"].startswith("previous_record") and blind["n_complete"] == 1
    assert S.build_round_completion("run35", uni, rows, {"s2": {}}, {}, NOW, hosts_not_visible=["s1"])["sym_sides"]["AAAUSDT_LONG"]["must_carried"] == "not_visible"


def test_catchup_launches_from_decision_log(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps({"ts_epoch": NOW - 100, "mode_per_launch": {"s2:AAA": {"class": 0, "mode": {"LONG": "budget:12000+V18_CATCHUP", "SHORT": "full"}}}}) + "\n"
                 + json.dumps({"ts_epoch": NOW - 50, "mode_per_launch": {}}) + "\n" + "{bad\n")
    got = S._catchup_launches(p, NOW)
    assert got == {"AAA_LONG": [(NOW - 100, True)], "AAA_SHORT": [(NOW - 100, False)]}
    assert S._catchup_launches(tmp_path / "none", NOW) == {}


def _host_fn(name):
    tree = ast.parse(S.HOST_PY)
    fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign)) and (getattr(n, "name", None) in (name,) or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_STRUCT_EXIT_MARKERS"))]
    ns = {"json": json}
    exec(compile(ast.Module(body=fns, type_ignores=[]), "<host_py>", "exec"), ns)
    return ns[name]


def test_slow_redo_due_only_catchup_budget_in_slow_pinned_round():
    assert S._slow_redo_due({"complete_at": None, "mode": "catchup"}, True) is True
    assert S._slow_redo_due({"complete_at": None, "mode": "budget:12000"}, True) is True
    assert S._slow_redo_due({"complete_at": None, "mode": "catchup"}, False) is False  # round not slow-pinned
    assert S._slow_redo_due({"complete_at": "2026-10-22T10:00:00Z", "mode": "catchup"}, True) is False  # an older full board completes it
    assert S._slow_redo_due({"complete_at": None, "mode": None}, True) is False and S._slow_redo_due(None, True) is False


def test_round_forces_slow_with_budget_pin(tmp_path):
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"round": "run35", "env": {"V15_BOARD_BUDGET": "0"}}))
    assert S._round_forces_slow("run35", p) is True


def test_struct_exit_marker_host_helper():
    f = _host_fn("_struct_exit_marker")
    assert f("x\n[DEFAULTS-GATE] A_LONG: template T has 1 default violations — NOT running. First: ...\n[PAIR-IN-RAM] early exit\n") == "[DEFAULTS-GATE]"
    assert f("[DEFAULTS-GATE] A_LONG: 3 bold defaults = running default\n[spec-fill] row 3\n") is None
    assert f("STALE_NPZ_SKIPPED: NPZ last bar ...\n") == "STALE_NPZ_SKIPPED"
    assert f("[NO-WRITE] A_LONG no prepared NPZ\n") == "[NO-WRITE]"
    assert f("[PARITY-GATE] A_LONG: hard default disparity — NOT running: x\n") == "[PARITY-GATE]"
    assert f("[PARITY-GATE] A_LONG: defaults symmetric (cached)\n") is None
    assert f("STALE_NPZ_SKIPPED old run\n[DEDUP] acquired\n[spec-fill] workbook complete\n") is None  # a later run did work
    assert f("") is None and f(None) is None


def test_refund_structural_exits_once_per_log_mtime():
    st = {"attempts": {"ADBE_LONG|30D": 3, "BABA_SHORT|30D": 0}}
    ex = {"s2": {"ADBE_LONG": [1000.0, "[DEFAULTS-GATE]"], "BABA_SHORT": [1000.0, "STALE_NPZ_SKIPPED"]}}
    got = S._refund_structural_exits(st, ex)
    assert got == [("s2", "ADBE_LONG", "[DEFAULTS-GATE]", 2)] and st["attempts"]["BABA_SHORT|30D"] == 0  # never below 0
    assert S._refund_structural_exits(st, ex) == [] and st["attempts"]["ADBE_LONG|30D"] == 2  # same log mtime -> no double refund
    ex["s2"]["ADBE_LONG"] = [2000.0, "STALE_NPZ_SKIPPED"]
    assert S._refund_structural_exits(st, ex)[0][3] == 1
    assert S._refund_structural_exits(st, {"s5": None, "s6": {"X_LONG": "bad"}}) == []


def test_base_wait_release_after_one_tick_with_incumbent():
    st = {}
    assert S._base_wait_release("ICXUSDT_SHORT", st, 1000.0, True) is False  # first tick waits
    assert S._base_wait_release("ICXUSDT_SHORT", st, 1121.0, True) is True  # next tick launches
    assert S._base_wait_release("NOINC_SHORT", st, 1000.0, False) is False and S._base_wait_release("NOINC_SHORT", st, 9999.0, False) is False
