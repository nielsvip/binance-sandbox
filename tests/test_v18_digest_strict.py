"""V18 principle 13 (USER 2026-10-20 "the 4 are strictly kept apart"): the pilot reads ONLY data/learn/digest_<CAT_SIDE>.json
of its own cat_side (meta.cat_side asserted at load; a foreign digest is refused), fail-open when absent; another cat_side's
winners are tried only as labelled XPOLL:<from> rows, at most V18_XPOLL_K per board."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()
FIX = ROOT / "tests" / "fixtures" / "v18_digest"


def test_own_cat_side_loads(monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(FIX))
    d, why = V.load_digest("CRYPTO_LONG")
    assert d is not None and why.startswith("ok digest_CRYPTO_LONG.json")
    assert d["meta"]["cat_side"] == "CRYPTO_LONG"


def test_foreign_digest_is_refused(monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(FIX))
    d, why = V.load_digest("STOCKS_SHORT")  # file named STOCKS_SHORT but built for CRYPTO_SHORT
    assert d is None and "CAT_SIDE_MISMATCH" in why


def test_absent_and_unknown_fail_open(monkeypatch, tmp_path):
    monkeypatch.setenv("V18_DIGEST_DIR", str(tmp_path))
    d, why = V.load_digest("CRYPTO_SHORT")
    assert d is None and why.startswith("absent")
    d, why = V.load_digest("FX_LONG")
    assert d is None and "unknown cat_side" in why
    (tmp_path / "digest_CRYPTO_SHORT.json").write_text("{not json")
    d, why = V.load_digest("CRYPTO_SHORT")
    assert d is None and why.startswith("unreadable")


def test_default_dir_is_data_learn(monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    assert V.digest_dir(ROOT) == ROOT / "data" / "learn"


def test_xpoll_only_foreign_cat_sides_capped(monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(FIX))
    d, _ = V.load_digest("CRYPTO_LONG")
    xs = V.xpoll_take(d, "CRYPTO_LONG", 5)
    assert [x["from_cat_side"] for x in xs] == ["CRYPTO_SHORT", "STOCKS_LONG"]  # the self-labelled entry is not cross
    assert len(V.xpoll_take(d, "CRYPTO_LONG", 1)) == 1
    assert V.xpoll_take(d, "CRYPTO_LONG", 0) == []


def test_pilot_reads_only_its_own_cat_side():
    assert "_v18_dg, _v18_dg_why = (_V18P.load_digest(_v18_floor_cs, ROOT) if _v18_phases_on else (None, \"phases off\"))" in SRC
    assert "_V18P.load_digest(map_key_for_symside(new_symside), ROOT)" in SRC
    assert SRC.count("_V18P.load_digest(") == 2
    assert 'f"XPOLL:{frm}:{sw}={c}"' in SRC and '"XPOLL", deadline, ph, accept=_v18_accept, max_prom=kx, book=_v18' in SRC
    assert 'kx = int(os.environ.get("V18_XPOLL_K", "5"))' in SRC
    assert '_v18["xpoll"] = {' in SRC


def test_learn_file_falls_back_to_the_gated_tools_path(tmp_path, monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    monkeypatch.delenv("V18_BRAIN_DIR", raising=False)
    assert V.learn_file("digest_CRYPTO_LONG.json", tmp_path) == tmp_path / "data" / "learn" / "digest_CRYPTO_LONG.json"
    alt = tmp_path / "tools" / "v18_data" / "brain" / "v18_brain_CRYPTO_LONG.json"
    alt.parent.mkdir(parents=True)
    alt.write_text("{}")
    assert V.learn_file("v18_brain_CRYPTO_LONG.json", tmp_path, "brain") == alt
    (tmp_path / "data" / "brain").mkdir(parents=True)
    (tmp_path / "data" / "brain" / "v18_brain_CRYPTO_LONG.json").write_text("{}")
    assert V.learn_file("v18_brain_CRYPTO_LONG.json", tmp_path, "brain") == tmp_path / "data" / "brain" / "v18_brain_CRYPTO_LONG.json"



def test_learn_file_flat_gate_deploy_path(tmp_path, monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    (tmp_path / "tools").mkdir()
    flat = tmp_path / "tools" / "v18_data_learn_needed_filters_CRYPTO_LONG.json"
    flat.write_text("{}")
    assert V.learn_file("needed_filters_CRYPTO_LONG.json", tmp_path) == flat
