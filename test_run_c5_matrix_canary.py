import json
import hashlib

import pytest

from tools import run_c5_matrix_canary as canary
from tools.c5_matrix_contract import c5_contract_fingerprint


def test_c5_canary_requires_differential_stdev_close_and_telemetry(monkeypatch):
    calls = []

    def fake_leg(symbol, side, label, overrides, timeout, min_avail):
        calls.append((label, overrides))
        candidate = label == "stdev_candidate"
        return {
            "status": "OK",
            "trades": [],
            "fingerprint": "candidate" if candidate else "control",
            "telemetry_complete": True,
            "structural_ok": True,
            "gr_trace": {"valid": True},
            "stdev_exit_count": 1 if candidate else 0,
            "result": {"time_in_mkt_long_pct": 40.0},
        }

    monkeypatch.setattr(canary, "_run_leg", fake_leg)
    monkeypatch.setattr(
        canary,
        "c5_contract_fingerprint",
        lambda *_args, **_kwargs: "c5:test",
    )
    monkeypatch.setattr(
        canary,
        "tim_contract",
        lambda *_args, **_kwargs: {
            "tim_min_pct": 20.0,
            "tim_max_pct": 60.0,
        },
    )
    result = canary.run_key("MU", "LONG", 1, 1)
    assert result["pass"]
    assert calls[0][1]["MTF_DC_REJECT_EXIT_ENABLED"] is True
    assert calls[0][1]["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert calls[0][1]["MTF_DC_REJECT_EXIT_LOOKBACK"] == 2
    assert calls[0][1]["PRICE_CROSS_BACK_BAND_PCT"] == 0.0
    assert calls[0][1]["WT_3M_FORCE_OPEN_ENABLED"] is False
    assert calls[0][1]["WT_3M_FORCE_OPEN_BUILD_TO_TARGET"] is False
    assert calls[0][1]["LR_BAND_LADDER_TRIGGER"] == "green"
    assert calls[0][1]["LR_BAND_ENTRY_ENABLED"] is False
    assert calls[0][1]["LR_BAND_ENTRY_PRIORITY"] is False
    assert calls[0][1]["WT_DC_ENTRY_THRESHOLD"] == 9999.0
    assert calls[0][1]["STDEV_REJECT_EXIT_ENABLED"] is False
    assert calls[1][1]["STDEV_REJECT_EXIT_ENABLED"] is True
    assert calls[1][1]["STDEV_REJECT_EXIT_TF"] == "1h"


def test_gr_trace_gate_rejects_stale_or_wrong_side_rows(tmp_path):
    trace = tmp_path / "gr.jsonl"
    trace.write_text(
        json.dumps(
            {
                "trace_version": "gr-htf-handoff-v1",
                "path": "GR_HTF_DIRECT_ENTRY",
                "stage": "scorer_called",
                "symbol": "MU",
                "position_side": "LONG",
            }
        )
        + "\n"
    )
    assert canary._gr_trace_summary(trace, "MU", "LONG")["valid"]
    assert not canary._gr_trace_summary(trace, "MU", "SHORT")["valid"]


def test_contract_fingerprint_hashes_shadow_code_and_active_npz(tmp_path):
    active = tmp_path / "active"
    shadow = tmp_path / "shadow"
    (active / "data/matrix_npz/stocks_repaired_20260725_c2").mkdir(
        parents=True
    )
    shadow.mkdir()
    (active / "engine.py").write_text("active")
    (shadow / "engine.py").write_text("shadow-v1")
    (
        active
        / "data/matrix_npz/stocks_repaired_20260725_c2/MU.npz"
    ).write_bytes(b"frozen")
    first = c5_contract_fingerprint(
        active, "MU", "LONG", ("engine.py",), contract_root=shadow
    )
    (shadow / "engine.py").write_text("shadow-v2")
    second = c5_contract_fingerprint(
        active, "MU", "LONG", ("engine.py",), contract_root=shadow
    )
    assert first != second
    supplied = c5_contract_fingerprint(
        active,
        "MU",
        "LONG",
        ("engine.py",),
        contract_root=shadow,
        npz_sha256=hashlib.sha256(b"frozen").hexdigest(),
    )
    assert supplied == second


def test_contract_fingerprint_fails_closed_without_npz(tmp_path):
    (tmp_path / "engine.py").write_text("engine")
    with pytest.raises(FileNotFoundError):
        c5_contract_fingerprint(
            tmp_path, "MU", "LONG", ("engine.py",)
        )
