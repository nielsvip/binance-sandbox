import json

import pytest

from tools.v15_preopen_7d_launch import _active_symbols, build_launch
from tools.v15_preopen_7d_stage import digest


def test_launcher_isolates_paths_and_preserves_30d_start(tmp_path):
    from tools import v15_preopen_7d_launch as launch

    ss = "AAAUSDT_SHORT"
    template = launch.ROOT / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx"
    if not template.exists():
        pytest.skip("template unavailable")
    source = {"X": 1}
    queue = {"accounts": {"fin": [ss], "trc": []},
             "sym_sides": {ss: {"manifest_ok": True, "cat_side": "CRYPTO_SHORT", "overrides": source}}}
    cmd, env, folder = build_launch(queue, ss, tmp_path, 7, "python3")
    assert cmd[cmd.index("--window-days") + 1] == "7"
    assert cmd[cmd.index("--workers") + 1] == "7"
    assert env["V15_7D_SOURCE_SHA256"] == digest(source)
    assert json.loads((folder / "start_30d.json").read_text()) == source
    assert env["V15_PREV_BEST_START"] == "0"
    assert env["V15_DIAG_REPAIR_S"] == "150"
    assert "V15_SKIP_LIVE_AT_DONE" not in env
    assert str(folder / "out") == env["V15_OUT_DIR"]


def test_launcher_rejects_unverified_source(tmp_path):
    ss = "AAAUSDT_SHORT"
    queue = {"accounts": {"fin": [ss], "trc": []},
             "sym_sides": {ss: {"manifest_ok": False, "cat_side": "CRYPTO_SHORT", "overrides": {"X": 1}}}}
    with pytest.raises(ValueError, match="verified"):
        build_launch(queue, ss, tmp_path, 7, "python3")


def test_slot_guard_counts_distinct_pilot_symbols_only():
    assert _active_symbols([
        "/env/bin/python -u /work/v15_pilot.py --sym-side AAPL_LONG --workers 7",
        "/env/bin/python -u /work/v15_pilot.py --sym-side AAPL_SHORT --workers 7",
        "/env/bin/python -u /work/v15_pilot_7d.py --sym-side ABBV_LONG --workers 7",
        "bash -c /env/bin/python -u /work/v15_pilot.py --sym-side FAKE_LONG",
    ]) == {"AAPL_LONG", "AAPL_SHORT", "ABBV_LONG"}
