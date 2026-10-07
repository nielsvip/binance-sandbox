"""KG LAW 2026-09-21: kindergarten HTF+LTF filters must never be skipped in v15_pilot_0914 or herd.

BMNR/IBIT -38% vs BH was structural KG gap (HTF_TREND_VETO, MTF, TOP_OF_RANGE, GR, ADX, BB etc
were in disabled 80%-skip set). If KG untested all 67 must rerun. This test locks that."""

import pathlib
import re


PILOT = pathlib.Path("v15_pilot_0914.py")
HERD = pathlib.Path("tools/v15_local_herd.py")

KG_SUBSTRINGS = (
    "HTF_", "MTF_", "MTS_", "WT_", "W15M",
    "TOP_OF_RANGE", "GR_FILTER", "GR_",
    "ADX_", "BB_SQUEEZE", "COUNTER_TREND", "DELTA_REENTRY", "EXIT_BLOCKER", "MANDATORY_REENTRY",
)

KG_EXACT = {
    "HTF_TREND_VETO_ENABLED", "MTF_ARMED_ENTRY_ENABLED", "TOP_OF_RANGE_BLOCK_ENABLED",
    "GR_FILTER_ALL_ENTRIES", "OPEN_RATE_BREAKER_ENABLED", "COUNTER_TREND_ADD_BLOCK_ENABLED",
    "DELTA_REENTRY_FILTER_ENABLED", "EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED",
    "WT_15M_BOUNCE_OPEN_ENABLED", "ADX_RANGING_THRESHOLD",
}


def test_pilot_defines_kg_never_skip():
    src = PILOT.read_text()
    assert "KG_NEVER_SKIP" in src, "KG_NEVER_SKIP set missing"
    assert "_is_kg_never_skip" in src, "_is_kg_never_skip helper missing"
    # must contain HTF and MTF markers
    for marker in ("HTF_TREND_VETO_ENABLED", "MTF_ARMED_ENTRY_ENABLED", "TOP_OF_RANGE_BLOCK_ENABLED", "GR_FILTER_ALL_ENTRIES"):
        assert marker in src, f"KG set missing {marker}"
    # helper must check substrings
    for sub in ("HTF_", "MTF_", "WT_", "TOP_OF_RANGE", "GR_FILTER"):
        assert sub in src, f"KG helper missing substring {sub}"


def test_pilot_cycle_helper_guards_kg():
    src = PILOT.read_text()
    # _process_0914_row_helper must not skip KG: first branch must be _is_kg_never_skip check before disabled logic
    assert "KG LAW" in src
    # find helper
    idx = src.index("def _process_0914_row_helper")
    helper = src[idx: idx + 3000]
    assert "_is_kg_never_skip(switch)" in helper, "helper must call _is_kg_never_skip(switch)"
    # disabled checks must be inside elif/else after KG guard, not before
    assert helper.index("_is_kg_never_skip") < helper.index("disabled_per_category"), "KG guard must precede disabled check"
    assert helper.index("_is_kg_never_skip") < helper.index("disabled_switches"), "KG guard must precede global disabled"


def test_pilot_sequential_loop_guards_kg():
    src = PILOT.read_text()
    # sequential for (r, switch, cand) in rows: must also guard KG
    # look for the sequential block near _is_w15m_seq
    assert "_is_w15m_seq" in src
    # should contain KG check setting _is_w15m_seq True for KG
    assert "_is_kg_never_skip(switch)" in src
    # at least two occurrences (cycle + sequential)
    assert src.count("_is_kg_never_skip(switch)") >= 2, "both cycle and sequential must guard KG"


def test_pilot_purges_kg_from_disabled_sets():
    src = PILOT.read_text()
    assert "[KG-purge]" in src or "KG-purge" in src, "must purge KG from disabled_per_category/disabled_switches after load"
    assert "disabled_switches = {s for s in disabled_switches if not _is_kg_never_skip" in src
    assert "disabled_per_category[_k] = {s for s in disabled_per_category[_k] if not _is_kg_never_skip" in src


def test_pilot_compiles():
    import py_compile
    py_compile.compile(str(PILOT), doraise=True)
    py_compile.compile(str(HERD), doraise=True)


def test_disabled_file_does_not_lock_kg_when_filtered():
    # simulate filter logic: KG entries would be removed even if present in JSON
    import importlib.util, pathlib
    # load helper logic inline without importing pilot (avoid heavy imports)
    KG_NEVER_SKIP = {
        "HTF_TREND_VETO_ENABLED", "MTF_ARMED_ENTRY_ENABLED", "TOP_OF_RANGE_BLOCK_ENABLED",
        "GR_FILTER_ALL_ENTRIES", "OPEN_RATE_BREAKER_ENABLED", "COUNTER_TREND_ADD_BLOCK_ENABLED",
        "DELTA_REENTRY_FILTER_ENABLED", "EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED",
    }
    def _is_kg_never_skip(sw: str) -> bool:
        if sw in KG_NEVER_SKIP:
            return True
        for _kg in KG_SUBSTRINGS:
            if _kg in sw:
                return True
        return False
    # pretend disabled file lists HTF + random AUGMENT
    disabled = {"HTF_TREND_VETO_ENABLED", "AUGMENT_MIN_GAIN_5", "MTF_ARMED_ENTRY_ENABLED", "WT_15M_BOUNCE_OPEN_ENABLED"}
    filtered = {s for s in disabled if not _is_kg_never_skip(s)}
    assert "AUGMENT_MIN_GAIN_5" in filtered
    assert "HTF_TREND_VETO_ENABLED" not in filtered
    assert "MTF_ARMED_ENTRY_ENABLED" not in filtered
    assert "WT_15M_BOUNCE_OPEN_ENABLED" not in filtered
    # structural gate: if KG were not filtered, BMNR gap would repeat
    assert len(filtered) == 1, "only non-KG should survive"
