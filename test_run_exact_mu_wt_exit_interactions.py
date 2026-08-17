import json
from pathlib import Path

import pytest

from tools import run_exact_mu_wt_exit_interactions as lane


def test_common_floor_is_explicit_wt_force_16k_all_exits_off() -> None:
    values = lane.common_entry_floor()
    assert values["WT_3M_FORCE_OPEN_ENABLED"] is True
    assert values["WT_3M_FORCE_OPEN_BUILD_TO_TARGET"] is True
    assert values["WT_3M_FORCE_OPEN_TARGET_USD"] == 16000.0
    assert values["WT_3M_FORCE_OPEN_USE_SMA200"] is True
    assert values["MTF_EXIT_USE_COMPOUND"] is False
    assert values["MTF_DC_REJECT_EXIT_ENABLED"] is False
    assert values["MTF_WT_CROSS_EXIT_ENABLED"] is False
    assert values["MTF_GR_EXIT_GATE_ENABLED"] is False
    assert values["STRUCTURAL_RANGE_SHIFT_EXIT"] is False


def test_variants_change_only_declared_exit_fields() -> None:
    pair = lane.recipes()
    control = pair["control"]
    assert set(pair) == {
        "control",
        "dc_1h_n5",
        "wt_15m_gr3",
        "srs_bb1h",
        "dc_wt",
        "dc_srs",
    }
    for name, values in pair.items():
        changed = {
            key for key in set(control) | set(values)
            if control.get(key) != values.get(key)
        }
        assert changed <= lane.EXIT_DIFFERENCE_ALLOWLIST, name


def test_mtf_family_interdependencies_are_explicit() -> None:
    pair = lane.recipes()
    dc = pair["dc_1h_n5"]
    assert dc["MTF_EXIT_USE_COMPOUND"] is True
    assert dc["MTF_DC_REJECT_EXIT_ENABLED"] is True
    assert dc["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert dc["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5

    wt = pair["wt_15m_gr3"]
    assert wt["MTF_EXIT_USE_COMPOUND"] is True
    assert wt["MTF_WT_CROSS_EXIT_ENABLED"] is True
    assert wt["MTF_WT_CROSS_EXIT_TF"] == "15m"
    assert wt["MTF_GR_EXIT_GATE_ENABLED"] is True
    assert wt["MTF_GR_EXIT_MIN_TFS"] == 3

    srs = pair["srs_bb1h"]
    assert srs["MTF_EXIT_USE_COMPOUND"] is False
    assert srs["STRUCTURAL_RANGE_SHIFT_EXIT"] is True
    assert srs["STRUCTURAL_RANGE_SHIFT_TF"] == "bb_1h"


def test_manifest_is_isolated_and_capital_contract_is_exact() -> None:
    result = lane.manifest()
    assert result["matrix_written"] is False
    assert result["live_written"] is False
    assert result["promotion_allowed"] is False
    assert result["capital_usd_per_fold"] == 10000.0
    assert result["side_bh_deployed_usd_per_fold"] == 2000.0
    assert result["strategy_capacity_usd"] == 16000.0
    assert result["stock_round_trip_cost_pct"] == 0.05
    assert result["tim_contract"]["band_pct"] == [20.0, 60.0]
    assert result["selection"]["gr_only_omitted"]
    known = result["known_full_window_entry_floor"]["wt_force_true"]
    assert known["overrides_sha256"] == result["common_entry_floor_sha256"]
    assert known["capture_vs_side_bh"] == 6.652
    assert known["time_in_mkt_pct"] == 99.9722


def test_trade_file_audit_rejects_cost_or_side_pollution(tmp_path: Path) -> None:
    path = tmp_path / "trades.jsonl"
    rows = [
        {
            "position_side": "LONG",
            "pnl_pct": 1.0,
            "pnl_usd": 20.0,
            "round_trip_cost_pct": 0.05,
        },
        {
            "position_side": "SHORT",
            "pnl_pct": 1.0,
            "pnl_usd": 20.0,
            "round_trip_cost_pct": 0.05,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    selected, audit = lane.read_trade_file(path)
    assert len(selected) == 1
    assert audit["opposite_side_pnl_rows"] == 1
    assert audit["stock_cost_contract_ok"] is True

    rows[0]["round_trip_cost_pct"] = 0.8
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    _, audit = lane.read_trade_file(path)
    assert audit["stock_cost_contract_ok"] is False


def _leg(variant: str, gain: float, tim: float, bh: float = 5.0) -> dict:
    return {
        "variant": variant,
        "fold": 1,
        "metrics": {
            "acc_gain_pct": gain,
            "bh_pct": bh,
            "time_in_mkt_pct": tim,
        },
        "lifecycle": {"real_closes": 3, "reentry_violations": 0},
        "capacity_respected": True,
        "expected_capacity_saturation": True,
        "capacity": {"max_open_notional": 16000.0},
        "file_audit": {
            "stock_cost_contract_ok": True,
            "opposite_side_pnl_rows": 0,
        },
        "reason_counts": {},
    }


def test_strict_gate_requires_2x_bh_control_and_runtime_tim_band() -> None:
    control = _leg("control", 9.0, 99.97)
    good = lane.fold_comparison(_leg("dc_wt", 11.0, 50.0), control)
    assert good["strict_pass"] is True

    assert lane.fold_comparison(
        _leg("dc_wt", 9.5, 50.0), control
    )["strict_pass"] is False
    assert lane.fold_comparison(
        _leg("dc_wt", 11.0, 70.0), control
    )["strict_pass"] is False
    assert lane.fold_comparison(
        _leg("dc_wt", 10.0, 50.0), control
    )["strict_pass"] is False
    negative_bh_control = _leg("control", -2.0, 99.97, bh=-3.0)
    assert lane.fold_comparison(
        _leg("dc_wt", 1.0, 50.0, bh=-3.0), negative_bh_control
    )["strict_pass"] is True
    assert lane.fold_comparison(
        _leg("dc_wt", -1.0, 50.0, bh=-3.0), negative_bh_control
    )["strict_pass"] is False


def test_run_requires_control_but_rejects_unknown_before_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        lane.psc,
        "matrix_contract_fingerprint",
        lambda _symbol, _side: lane.EXPECTED_C4_FINGERPRINT,
    )
    with pytest.raises(ValueError, match="unknown variants"):
        lane.run_all(
            tmp_path,
            1,
            variants=["invented"],
            min_avail_mb=0,
        )
    with pytest.raises(ValueError, match="unknown or empty folds"):
        lane.run_all(
            tmp_path,
            1,
            variants=["control"],
            fold_numbers=[4],
            min_avail_mb=0,
        )


def test_markdown_renders_negative_bh_as_cash_gate() -> None:
    report = {
        "contract_fingerprint": lane.EXPECTED_C4_FINGERPRINT,
        "npz": {"sha256": "abc"},
        "aggregate": {
            "control": {
                "folds": 1,
                "return_pct_equal_capital_mean": 1.0,
                "side_bh_pct_equal_capital_mean": -2.0,
                "return_multiple_vs_side_bh": None,
                "time_in_mkt_pct_span_weighted": 99.0,
                "real_closes_sum": 0,
            }
        },
        "comparison": {},
    }
    assert "cash gate" in lane.render_markdown(report)


def test_partial_variant_directory_without_bound_receipt_is_not_a_result(
    tmp_path: Path,
) -> None:
    variant_dir = tmp_path / "fold_3" / "wt_15m_gr3"
    variant_dir.mkdir(parents=True)
    (variant_dir / "engine.log").write_text("partial")
    assert lane.reusable_receipt(
        variant_dir / "receipt.json",
        contract_fingerprint=lane.EXPECTED_C4_FINGERPRINT,
        npz_sha256="npz",
        overrides_sha256="override",
        fold=lane.FOLDS[2],
    ) is None

    receipt = {
        "contract": lane.CONTRACT,
        "contract_fingerprint": lane.EXPECTED_C4_FINGERPRINT,
        "npz_sha256": "npz",
        "overrides_sha256": "override",
        "window": lane.FOLDS[2],
    }
    (variant_dir / "receipt.json").write_text(json.dumps(receipt))
    assert lane.reusable_receipt(
        variant_dir / "receipt.json",
        contract_fingerprint=lane.EXPECTED_C4_FINGERPRINT,
        npz_sha256="npz",
        overrides_sha256="different",
        fold=lane.FOLDS[2],
    ) is None
