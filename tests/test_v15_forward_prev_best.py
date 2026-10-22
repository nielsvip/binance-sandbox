import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "v15_pilot.py"


def _load_funcs():
    # import only the pure functions (the pilot module has heavy import side effects); exec the two defs from source
    src = SRC.read_text()
    ns = {}
    import datetime, os
    ns.update({"Path": Path, "json": json, "os": os, "datetime": datetime})
    for name in ("forward_prev_best_candidates", "forward_pick_prev", "forward_unseen_window"):
        m = re.search(rf"\ndef {name}\(.*?(?=\n\n\ndef |\n\n\nclass |\Z)", src, re.S)
        assert m, name
        exec(m.group(0), ns)
    return ns


def test_prev_best_sets_become_forward_candidates(tmp_path):
    ns = _load_funcs()
    (tmp_path / "ABC_LONG.json").write_text(json.dumps({"sym_side": "ABC_LONG", "source": "per_sym_history", "sets": [
        {"gain_recorded": 12.5, "updated_at": "2026-10-21T23:38:28Z", "overrides": {"SW": "1", "JOINT": "A=1 + B=2"}},
        {"gain_recorded": 3.0, "overrides": {}},  # no overrides -> skipped
        {"gain_recorded": 9.0, "npz_last_bar_ts": 1761000000.0, "overrides": {"SW": "2"}},
    ]}))
    out = ns["forward_prev_best_candidates"]("ABC_LONG", prev_best_dir=tmp_path)
    assert len(out) == 2
    a = out[0]
    assert a["src"] == "prev_best:per_sym_history" and a["ts_source"] == "updated_at_proxy" and a["final_gain"] == 12.5
    assert a["overrides"] == {"SW": "1"}  # ' + ' joint strings dropped like the npzprev branch
    assert abs(a["ts_last"] - 1792625908.0) < 1  # 2026-10-21T23:38:28Z
    assert out[1]["ts_source"] == "npz_last_bar_ts" and out[1]["ts_last"] == 1761000000.0
    assert ns["forward_prev_best_candidates"]("NOPE_LONG", prev_best_dir=tmp_path) == []


def test_pick_prev_uses_the_proxy_with_the_one_hour_gap(tmp_path):
    ns = _load_funcs()
    (tmp_path / "ABC_LONG.json").write_text(json.dumps({"sets": [{"gain_recorded": 1.0, "updated_at": "2026-10-21T23:38:28Z", "overrides": {"SW": "1"}}]}))
    cands = ns["forward_prev_best_candidates"]("ABC_LONG", prev_best_dir=tmp_path)
    cur = 1792625908.0 + 86400 * 2  # current NPZ ends two days later
    picked = ns["forward_pick_prev"](cands, cur)
    assert picked is not None and picked["ts_source"] == "updated_at_proxy"
    assert ns["forward_unseen_window"](picked["ts_last"], cur) in (1, 7, 30)
    assert ns["forward_pick_prev"](cands, 1792625908.0 + 600) is None  # 10 min later = not a refresh


def test_forward_record_carries_prev_ts_source():
    src = SRC.read_text()
    assert '"prev_ts_source": _prev.get("ts_source") or "npzprev"' in src
    assert "_out = list(forward_prev_best_candidates(symside))" in src
