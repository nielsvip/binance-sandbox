"""2026-10-28: a holdout-confirmed (D) board must not lose the kept-best comparison to a pre-D greedy in-sample final; the greedy file
is archived under _superseded_greedy/, never deleted. Two confirmed boards keep the class-then-gain rule."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.environ.get("V15_PILOT_UNDER_TEST"):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("v15_pilot", os.environ["V15_PILOT_UNDER_TEST"])
    P = importlib.util.module_from_spec(_spec)
    sys.modules["v15_pilot"] = P
    _spec.loader.exec_module(P)
else:
    import v15_pilot as P  # noqa: E402

OLD = "ETN_SHORT_bh11_gain34_t52_30d_matrix.xlsx"
NEW = "ETN_SHORT_bh11_gain31_t50_30d_matrix.xlsx"


def _zip(path):
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        for i in range(12):
            z.writestr(f"xl/p{i}.xml", "<x/>")


def _final(d, name, confirmed=None):
    (d / name).write_bytes(b"x")
    if confirmed is not None:
        (d / name.replace(".xlsx", "_manifest.json")).write_text(json.dumps({"publish_class": "QUALIFIED", "holdout_confirmed": confirmed}))


def test_confirmed_candidate_replaces_greedy_best(tmp_path):
    _final(tmp_path, OLD)  # pre-D: no manifest key
    wb = tmp_path / "board.xlsx"
    _zip(wb)
    active, won = P._apply_best_publish(tmp_path, "ETN_SHORT", NEW, 31.12, "QUALIFIED", wb, cand_confirmed=True)
    assert (active, won) == (NEW, True)
    assert (tmp_path / NEW).read_bytes() == wb.read_bytes()
    assert (tmp_path / "_superseded_greedy" / OLD).exists()
    assert not (tmp_path / OLD).exists()


def test_unconfirmed_candidate_keeps_old_rule(tmp_path):
    _final(tmp_path, OLD)
    wb = tmp_path / "board.xlsx"
    _zip(wb)
    active, won = P._apply_best_publish(tmp_path, "ETN_SHORT", NEW, 31.12, "QUALIFIED", wb)
    assert (active, won) == (OLD, False)


def test_two_confirmed_boards_compare_gain(tmp_path):
    _final(tmp_path, OLD, confirmed=True)
    win, bn, bg, note = P._rank_final_gain(tmp_path, "ETN_SHORT", NEW, 31.12, "QUALIFIED", True)
    assert (win, bn) == (False, OLD)
