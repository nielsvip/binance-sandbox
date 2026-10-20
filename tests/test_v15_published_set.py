"""The published-set resolver must never substitute another set for the one a board was published with.

Guards the 2026-10-20 defect: `tools/v15_trade_parity.load_overrides` returned the Mac's own (older, different) lifecycle_pilot
set, or `{}` when none existed, with no check against the board manifest's `overrides_md5`. Measured on the real tree, the old
loader matched the published md5 0 of 4 times (MSFT_LONG 787 wrong switches, NVDA_SHORT 0 switches, BWXT_SHORT 791 vs 881,
TRGP_LONG 4 vs 876), and re-running the BWXT stale set produced 72 trades / +10.91% against the published board's 31 trades /
+31.91% on the SAME NPZ and the SAME 1099-bar window.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import v15_published_set as PS


def _md5(overrides) -> str:
    return hashlib.md5(json.dumps(overrides, sort_keys=True, default=str).encode()).hexdigest()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A self-contained board: one manifest, one cache dir, one lifecycle_pilot dir."""
    sheets = tmp_path / "sheets"
    cache = tmp_path / "cache"
    lifecycle = tmp_path / "data" / "reports" / "lifecycle_pilot"
    for directory in (sheets, cache, lifecycle):
        directory.mkdir(parents=True)
    published = {"WT_15M_BOUNCE_OPEN_ENABLED": False, "DC_EDGE_SIZING_MAX_MULT": 2.5, "EXIT_VELOCITY_WT_ENABLED": True}
    manifest = {
        "symside": "ZZZ_LONG",
        "final_name": "ZZZ_LONG_bh1_gain9_t12_30d_matrix.xlsx",
        "published_utc": "2026-10-20T01:00:00Z",
        "overrides_md5": _md5(published),
        "npz_md5": "deadbeef" * 4,
        "metrics": {"gain_pct": 9.0, "trades": 12},
        "provenance": {"npz_sha": "deadbeef" * 4, "npz_window": {"n": 999}},
    }
    (sheets / "ZZZ_LONG_bh1_gain9_t12_30d_matrix_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(PS, "SHEET_DIRS", (sheets,))
    monkeypatch.setattr(PS, "CACHE_DIR", cache)
    monkeypatch.setattr(PS, "ROOT", tmp_path)
    monkeypatch.setattr(PS, "PER_SYM_DB", tmp_path / "nonexistent.db")
    return {"published": published, "lifecycle": lifecycle, "cache": cache, "md5": _md5(published)}


def test_resolves_the_set_whose_md5_matches_the_manifest(tree):
    (tree["lifecycle"] / "ZZZ_LONG_v14_progress.json").write_text(json.dumps({"cumulative_overrides": tree["published"]}))
    resolved = PS.resolve_published_set("ZZZ_LONG")
    assert resolved["overrides"] == tree["published"]
    assert resolved["overrides_md5"] == tree["md5"]
    assert resolved["n_overrides"] == 3


def test_refuses_a_stale_set_that_looks_plausible(tree):
    """The exact old defect: a full, plausible, DIFFERENT set must be refused, not returned."""
    stale = dict(tree["published"])
    stale["DC_EDGE_SIZING_MAX_MULT"] = 1.0
    assert _md5(stale) != tree["md5"]
    (tree["lifecycle"] / "ZZZ_LONG_v14_progress.json").write_text(json.dumps({"cumulative_overrides": stale}))
    with pytest.raises(PS.PublishedSetNotFound) as err:
        PS.resolve_published_set("ZZZ_LONG")
    assert tree["md5"] in str(err.value)
    assert "a DIFFERENT set" in str(err.value)


def test_refuses_an_empty_set_instead_of_returning_bare_defaults(tree):
    (tree["lifecycle"] / "ZZZ_LONG_v14_progress.json").write_text(json.dumps({"cumulative_overrides": {}}))
    with pytest.raises(PS.PublishedSetNotFound):
        PS.resolve_published_set("ZZZ_LONG")


def test_refuses_when_nothing_was_published(tree):
    with pytest.raises(PS.PublishedSetNotFound) as err:
        PS.resolve_published_set("NOSUCH_LONG")
    assert "no publish manifest" in str(err.value)


def test_cache_is_only_trusted_when_its_md5_still_matches(tree):
    (tree["cache"] / f"ZZZ_LONG_{tree['md5']}.json").write_text(json.dumps({"TAMPERED": 1}))
    with pytest.raises(PS.PublishedSetNotFound):
        PS.resolve_published_set("ZZZ_LONG")


def test_variant_progress_files_are_searched(tree):
    """.npzprev_/.hollowprev_ snapshots are legitimate carriers of an earlier publish's set."""
    (tree["lifecycle"] / "ZZZ_LONG_v14_progress.json").write_text(json.dumps({"cumulative_overrides": {"X": 1}}))
    (tree["lifecycle"] / "ZZZ_LONG_v14_progress.json.npzprev_20261019.json").write_text(json.dumps({"cumulative_overrides": tree["published"]}))
    assert PS.resolve_published_set("ZZZ_LONG")["overrides_md5"] == tree["md5"]


def test_unreachable_window_is_reported_not_blamed_on_the_engine(tree, tmp_path):
    """A board published against an earlier NPZ generation is structurally unreachable — the 30-session window is anchored to
    the NPZ's last bar and there is no offset parameter."""
    npz = tmp_path / "backtest_v8" / "indicators"
    npz.mkdir(parents=True)
    (npz / "ZZZ.npz").write_bytes(b"a different npz generation")
    reach = PS.npz_reachable("ZZZ_LONG", PS.latest_manifest("ZZZ_LONG"))
    assert reach["reachable"] is False
    assert "UNREACHABLE_WINDOW" in reach["reason"]


def test_overrides_md5_matches_the_pilots_own_hash():
    """v15_pilot.py:10189 and tools/v15_published_selection.overrides_md5 use this exact serialisation."""
    overrides = {"B": 2, "A": True, "C": 1.5}
    assert PS.overrides_md5(overrides) == hashlib.md5(json.dumps(overrides, sort_keys=True, default=str).encode()).hexdigest()


def test_trade_parity_loader_fails_closed(tree, monkeypatch):
    stale = dict(tree["published"])
    stale["EXIT_VELOCITY_WT_ENABLED"] = False
    (tree["lifecycle"] / "ZZZ_LONG_v14_progress.json").write_text(json.dumps({"cumulative_overrides": stale}))
    monkeypatch.setenv("V15_PARITY_SET_HOSTS", "")
    monkeypatch.delenv("V15_PARITY_ALLOW_STALE_SET", raising=False)
    from tools import v15_trade_parity as TP

    with pytest.raises(RuntimeError) as err:
        TP.load_overrides("ZZZ_LONG", None)
    assert "refusing to run parity on an unidentified set" in str(err.value)
