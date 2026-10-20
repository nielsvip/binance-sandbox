"""V18 E4 (doc 17 §4.1): INERT_IDENTITY settlement. A main-pass cell whose exact materialised set + baseline was already
booked in the delta log is settled from that same cached engine result: no new delta-log line, one ROW_SETTLED line per
row. Pure book logic is unit-tested; the wiring in v15_pilot._spec_fill_workbook is pinned statically (the pilot runs on
the fleet; byte-identity ON vs OFF is proven on the S2 frozen NPZ, see docs/v15_pending/19_*)."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_kill_switch_default_off(monkeypatch):
    monkeypatch.delenv("V18_IDENTITY_SETTLE", raising=False)
    assert V.identity_settle_on() is False
    monkeypatch.setenv("V18_IDENTITY_SETTLE", "1")
    assert V.identity_settle_on() is True
    monkeypatch.setenv("V18_IDENTITY_SETTLE", "0")
    assert V.identity_settle_on() is False


def test_settle_only_inside_a_row_and_only_after_a_booked_eval():
    b = V.IdentityBook()
    assert b.settle(11, 5.0, "naked") is None  # no row open: never settles (START / sacred / V17 phases log normally)
    b.begin_row("T!3:A=1", lambda: 99)
    assert b.settle(11, 5.0, "naked") is None  # nothing booked yet: the first consumption is logged
    b.note(11, 5.0, "T!3:naked")
    assert b.settle(11, 5.0, "F=1") == "INERT_IDENTITY→T!3:naked"
    assert b.settle(11, 4.0, "F=2") is None  # another baseline = another delta: never an identity
    summ = b.end_row()
    assert summ["n"] == 1 and summ["settled"] == {"INERT_IDENTITY": 0, "INERT_IDENTITY_SHARED": 1}
    assert summ["shared_evals"] == ["T!3:naked"] and summ["cells"] == {"F=1": "INERT_IDENTITY→T!3:naked"}
    assert b.row is None and b.totals["cells_settled"] == 1 and b.totals["rows_settled"] == 1


def test_chain_identity_is_labelled_plain():
    b = V.IdentityBook()
    b.note(7, 1.5, "T!2:naked")
    b.begin_row("T!4:B=2", lambda: 7)
    assert b.settle(7, 1.5, "F=x") == "INERT_IDENTITY"
    s = b.end_row()
    assert s["settled"]["INERT_IDENTITY"] == 1 and s["settled"]["INERT_IDENTITY_SHARED"] == 0


def test_first_tag_is_kept():
    b = V.IdentityBook()
    b.note(1, 0.0, "first")
    b.note(1, 0.0, "second")
    assert b.lookup(1, 0.0) == "first"


def test_empty_row_returns_none_and_chain_fn_failure_is_safe():
    b = V.IdentityBook()
    b.begin_row("k", lambda: 1)
    assert b.end_row() is None
    b.note(3, 0.0, "x")

    def boom():
        raise RuntimeError("x")
    b.begin_row("k2", boom)
    assert b.settle(3, 0.0, "c").startswith("INERT_IDENTITY→")


def test_row_settled_record_shape():
    b = V.IdentityBook()
    b.note(5, 2.0, "t")
    b.begin_row("k", lambda: 0)
    b.settle(5, 2.0, "F=1")
    rec = V.row_settled_record({"ts": "t", "sym_side": "X_LONG", "sheet": "S", "row": 3}, b.end_row())
    assert rec["label"] == "ROW_SETTLED" and rec["n_settled"] == 1 and rec["settled"]["INERT_IDENTITY_SHARED"] == 1
    assert rec["sym_side"] == "X_LONG" and rec["cells"] == {"F=1": "INERT_IDENTITY→t"}


def test_pilot_wiring_pins():
    # flag object only exists when ON; OFF = None -> every hook is a no-op
    assert "_v18_ids = _V18P.IdentityBook() if (_V18P is not None and _V18P.identity_settle_on()) else None" in SRC
    # the settle short-circuit sits ONLY on the cache-hit branch of _get and returns the exact cached result
    m = re.search(r"if ck in _EVAL_CACHE:\n\s+res, cached = _EVAL_CACHE\[ck\], True\n\s+if _v18_ids is not None and _v18_ids\.settle\(hash\(ck\), cum_before, label\) is not None:\n\s+return res, err", SRC)
    assert m, "identity settle must return the cached result of the exact key"
    assert "_v18_ids.note(hash(ck), cum_before, f\"{sname}!{rr}:{label}\")" in SRC
    # one book per main-pass row: opened after the skip branch, closed right before _row_done
    assert SRC.index("_v18_ids.begin_row(key, _v18_chain_hash)") < SRC.index("_v18_settle_row(sname, rr, switch, cand, key, cumulative_before)")
    assert "progress[\"v18_identity\"] = dict(_v18_ids.totals)" in SRC
    # V18_* keys of the gate flags file are injected like V15_* (the gate decides production defaults)
    assert 'str(k).startswith(("V15_", "V18_"))' in SRC
    # missing helper module = all E4 switches OFF, never a crash
    assert "from tools import v15_v18_planner as _V18P\nexcept Exception:\n    _V18P = None" in SRC


def test_identity_settle_reproduces_serial_results():
    # a toy engine + toy row loop: the numbers a row consumes are identical with and without the book
    cache, log_on, log_off = {}, [], []

    def eng(ov):
        return {"gain_pct": float(sum(ov.values()))}

    def get(ov, cum, label, book, log):
        ck = tuple(sorted(ov.items()))
        if ck in cache:
            res = cache[ck]
            if book is not None and book.settle(hash(ck), cum, label) is not None:
                return res
        else:
            res = eng(ov)
            cache[ck] = res
        log.append(label)
        if book is not None:
            book.note(hash(ck), cum, label)
        return res
    rows = [({"a": 1}, [{"a": 1, "f": 0}, {"a": 1, "g": 0}]), ({"a": 1}, [{"a": 1, "f": 0}]), ({"b": 2}, [{"b": 2}])]
    out = {}
    for name, book, log in (("off", None, log_off), ("on", V.IdentityBook(), log_on)):
        cache.clear()
        vals = []
        for naked, cells in rows:
            if book is not None:
                book.begin_row(str(naked), lambda: 0)
            vals.append(get(naked, 0.0, "naked", book, log)["gain_pct"])
            vals += [get(c, 0.0, str(c), book, log)["gain_pct"] for c in cells]
            if book is not None:
                book.end_row()
        out[name] = vals
    assert out["on"] == out["off"]
    assert len(log_on) < len(log_off)
