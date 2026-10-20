"""V18_SET_SHA (store join, 2026-10-20): BOARD_CTX / ROW_DONE / ROW_SETTLED carry set_sha256 = sha256 of the canonical full
sanitized set in force; every restart books its start set once (progress start_sets[ts] -> sha, sets_by_sha[sha] -> dict)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_sha_is_canonical_order_free():
    a = V.set_sha256({"B": 1, "A": True, "C": {"x": [1, 2]}})
    assert a == V.set_sha256({"C": {"x": [1, 2]}, "A": True, "B": 1}) and len(a) == 64


def test_pilot_wiring_pins():
    assert '_v18_setsha_on = bool(_V18P is not None and _V18P.flag_on("V18_SET_SHA"))' in SRC
    assert '{**({"set_sha256": _v18_start_sha} if _v18_setsha_on else {}), "ts": utcnow(), "sym_side": new_symside, "label": "BOARD_CTX"' in SRC
    assert '{**({"set_sha256": _v18_chain_sha()} if _v18_setsha_on else {}), "ts": utcnow(), "sym_side": new_symside, "nav": nav_mode, "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "label": "ROW_DONE"' in SRC
    assert 'progress.setdefault("start_sets", {})[utcnow()] = {"sha": _v18_start_sha' in SRC
    assert 'progress.setdefault("sets_by_sha", {}).setdefault(_v18_start_sha, _v18_start_set)' in SRC
