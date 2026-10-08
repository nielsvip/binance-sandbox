"""tools/v15_missed_trend: missed-trend detection, verified-combo sequencing, requeue (no engine)."""

import json
import os

import pytest

from tools import v15_missed_trend as MT

HTML = "/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/AGLDUSDT_SHORT_bhm12p30_gain2p_t17_30d_matrix.html"


def slide_series(top=0.2292, bot=0.1926, n0=60, n1=120):
    """Flat, spike to top, linear slide to bot, flat — the AGLD shape (SHORT)."""
    up = [0.20 + (top - 0.20) * i / 10 for i in range(10)]
    dn = [top - (top - bot) * i / n1 for i in range(n1 + 1)]
    return [0.20] * n0 + up + dn + [bot] * 20


def test_short_slide_flagged():
    close = slide_series()
    top_bar = 70
    trades = [
        {
            "be": top_bar + 100 + i * 5,
            "bx": top_bar + 101 + i * 5,
            "pnl_pct": 0.3,
            "exit_reason": "EXIT_VELOCITY_WT x",
        }
        for i in range(3)
    ]
    rep = MT.scan_report("AGLDUSDT_SHORT", close, trades, 1.94, 2.95)
    assert rep["revise"] is True
    assert rep["trends"][0]["b0"] == top_bar
    assert abs(rep["trends"][0]["move_pct"] - 15.97) < 0.3
    assert rep["trends"][0]["late_by_bars"] == 100
    assert rep["churn"]["median_hold"] == 1
    assert any("TIM_LOW" in r for r in rep["reasons"])


def test_captured_slide_not_flagged():
    close = slide_series()
    trades = [
        {"be": 72, "bx": 190, "pnl_pct": 14.0, "exit_reason": "DAYTRADE_TARGET x"}
    ]
    rep = MT.scan_report("AGLDUSDT_SHORT", close, trades, 45.0, 14.0)
    assert rep["trends"] and rep["trends"][0]["cover"] > 0.9
    assert rep["revise"] is False


def test_high_tim_small_moves_not_flagged():
    close = [100 + (i % 5) * 0.2 for i in range(200)]
    trades = [
        {"be": i, "bx": i + 3, "pnl_pct": 0.2, "exit_reason": "DAYTRADE_TARGET x"}
        for i in range(0, 200, 10)
    ]
    rep = MT.scan_report("AGLDUSDT_SHORT", close, trades, 45.0, 3.0)
    assert rep["trends"] == [] and rep["revise"] is False


def test_low_tim_but_no_big_move_not_flagged():
    close = [100 + (i % 5) * 0.2 for i in range(200)]
    rep = MT.scan_report("AGLDUSDT_SHORT", close, [], 5.0, 0.5)
    assert rep["revise"] is False


def test_long_side_mirror():
    close = [100 - i * 0.1 for i in range(50)] + [95 + i * 0.2 for i in range(60)]
    rep = MT.scan_report("AAVEUSDC_LONG", close, [], 3.0, 0.0)
    assert rep["revise"] is True
    assert rep["trends"][0]["move_pct"] > 8.0


def test_agld_chart_regression():
    if not os.path.exists(HTML):
        pytest.skip("AGLD chart not in repo")
    hi = MT.html_inputs(HTML)
    assert len(hi["close"]) == 2881 and len(hi["trades"]) == 17
    rep = MT.scan_report(
        "AGLDUSDT_SHORT",
        hi["close"],
        hi["trades"],
        hi["meta"]["tim"],
        hi["meta"]["gain"],
        hi["labels"],
    )
    assert rep["revise"] is True
    big = rep["trends"][0]
    assert (big["b0"], big["b1"]) == (1757, 2874)
    assert abs(big["move_pct"] - 15.97) < 0.05
    assert big["cover"] < 0.10 and big["late_by_bars"] == 201
    assert (
        rep["churn"]["median_hold"] == 1
        and rep["churn"]["top_exit"] == "EXIT_VELOCITY_WT"
    )


def test_verify_combo_keeps_gain_up_tim_flat_only():
    def ev(ov):
        g = 1.0 + sum(ov.values())
        return g, 10 + len(ov), 5.0 + len(ov), True

    kept, g, t, log, final = MT.verify_combo(
        [("A=1", {"a": 1}), ("B=2", {"b": 2}), ("C=0", {"c": 0})], {}, ev, 1.0, 5.0
    )
    assert (
        [k for k, _ in kept] == ["A=1", "B=2"]
        and abs(g - 4.0) < 1e-9
        and final == {"a": 1, "b": 2}
    )
    assert [e["kept"] for e in log] == [True, True, False]


def test_verify_combo_rejects_tim_drop_and_invalid():
    def ev(ov):
        if "bad" in ov:
            return 9.0, 10, 1.0, True
        if "inv" in ov:
            return 9.0, 10, 9.0, False
        return 2.0, 10, 6.0, True

    kept, g, t, log, _ = MT.verify_combo(
        [("BAD=1", {"bad": 1}), ("INV=1", {"inv": 1}), ("OK=1", {"ok": 1})],
        {},
        ev,
        1.0,
        5.0,
    )
    assert [k for k, _ in kept] == ["OK=1"]


def test_priors_partial_shape():
    log = [
        {
            "switch": "WT_15M_BOUNCE_OPEN_ENABLED=True",
            "gain_before": 1.0,
            "gain_after": 2.5,
            "valid": True,
            "kept": True,
            "tab": "ENTRY_REVERSAL_BOUNCE",
        },
        {
            "switch": "MODE=crypto",
            "gain_before": 1.0,
            "gain_after": 9.0,
            "valid": True,
            "kept": True,
            "tab": "ENTRY_REVERSAL_BOUNCE",
        },
    ]
    out = MT.priors_partial("AGLDUSDT_SHORT", log)
    assert out["_symsides"]["CRYPTO_SHORT"] == ["AGLDUSDT_SHORT"]
    keys = list(out["CRYPTO_SHORT"])
    assert (
        len(keys) == 1
        and keys[0].startswith("ENTRY_REVERSAL_BOUNCE\tswitch\t")
        and out["CRYPTO_SHORT"][keys[0]] == [1.5]
    )


def test_requeue_dry_run_and_refusal(tmp_path):
    cell, hist = tmp_path / "cell", tmp_path / "hist"
    cell.mkdir()
    (cell / "XUSDT_SHORT_bhm1_gain2p_t3_30d_matrix.xlsx").write_text("x")
    prog = tmp_path / "XUSDT_SHORT_v14_progress.json"
    prog.write_text(json.dumps({"symside": "XUSDT_SHORT", "verdict": "BEST_EFFORT"}))
    r = MT.run_requeue(
        "XUSDT_SHORT",
        str(prog),
        str(tmp_path / "base.json"),
        str(cell),
        str(hist),
        dry_run=True,
    )
    assert r["requeued"] is False and r["dry_run"] is True and len(r["moves"]) == 2
    assert (cell / "XUSDT_SHORT_bhm1_gain2p_t3_30d_matrix.xlsx").exists()
    prog.write_text(json.dumps({"symside": "XUSDT_SHORT", "verdict": "QUALIFIED"}))
    r2 = MT.run_requeue(
        "XUSDT_SHORT",
        str(prog),
        str(tmp_path / "base.json"),
        str(cell),
        str(hist),
        dry_run=True,
    )
    assert r2["requeued"] is False and "QUALIFIED" in r2["reason"]


def test_missing_spec_names_lower_high():
    spec = MT.missing_spec(
        "AGLDUSDT_SHORT",
        {"b0": 1757, "b1": 2874, "move_pct": 15.97},
        labels=None,
        close=None,
    )
    assert (
        spec["status"] == "PROPOSED_NEW_SWITCH"
        and "lower high" in spec["rule"]
        and "1757->2874" in spec["rule"]
    )


def test_screen_worker_is_pool_picklable():
    import pickle

    assert pickle.loads(pickle.dumps(MT._screen_w)) is MT._screen_w
    assert "<locals>" not in MT._screen_w.__qualname__
