"""Task 05.3 header cache (USER 2026-10-15): cache per-(tab,switch) row-static header discovery.
Pure speedup — exact outputs. The reference below is the pre-edit inline loop rules; precompute+combine
must match it on every combination."""
import v15_pilot as P

SNAME = "TAB1"
HDRS = {
    "ENTRY_WT_DC_ENABLED=True": 12,
    "ENTRY_WT_DC_MODE=ON": 13,
    "KG_ENTRY_ENABLED=True": 14,
    "TABSKIP_FILTER_X=1": 15,
    "ZZZ_NOT_A_FIELD_XYZ_Q=1": 16,
    "TRADES_PER_SYM_PER_DAY_MAX=lots": 17,
    "GR_FILTER_ALL_ENTRIES=True": 18,
    "EXIT_VELOCITY_WT_MIN_TFS=many": 19,
}
SW1 = "ENTRY_WT_DC_THRESHOLD"
SW2 = "ZZZ_NOT_A_FIELD_XYZ_Q_MODE"
SW3 = "TRADES_PER_SYM_FOO"
SW4 = "EXIT_VELOCITY_WT_MODE"
MAND = {"GR_FILTER_ALL_ENTRIES"}
TABLEVEL = {"TABSKIP_FILTER_X"}
DEFAULTS = {}


def _ref(sname, switch, cand, header_map, tab_level, ever_y, mand, defaults, allcols, notes):
    yellow, hdrs, h2f = set(), [], {}
    excl_tl, excl_unw, type_sk = [], [], []
    sw_tok = {t for t in str(switch).upper().split("_") if t}
    for hdr in header_map:
        hdr_base = hdr.split("=", 1)[0].strip()
        via = len(sw_tok & {t for t in hdr_base.upper().split("_") if t}) >= P.YELLOW_MIN_SHARED_TOKENS or f"{sname}\t{str(switch).strip()}={str(cand).strip()}\t{hdr}" in ever_y
        want = via or hdr_base in mand
        mand_only = want and not via
        if hdr.split("=", 1)[0].strip() in tab_level:
            if want:
                yellow.add(hdr)
                excl_tl.append(hdr)
            continue
        if want:
            yellow.add(hdr)
        elif not allcols:
            continue
        filt, opt = hdr.split("=", 1)
        if filt.strip() not in P.known_config_fields():
            continue
        if filt.strip() in P.UNWIRED_FILTERS or filt.strip() in P.UNWIRED_SWITCHES:
            excl_unw.append(hdr)
            continue
        fok, _ = P._cand_compatible(filt.strip(), P._parse_opt_value(opt.strip(), defaults.get(filt.strip())), defaults)
        if not fok:
            type_sk.append(hdr)
            continue
        if mand_only:
            notes.append(hdr_base)
        hdrs.append(hdr)
        h2f[hdr] = {"filter": filt.strip(), "opt": opt.strip()}
    return {"yellow": yellow, "hdrs": hdrs, "h2f": h2f, "excluded_tab_level": excl_tl,
            "excluded_unwired": excl_unw, "type_skipped": type_sk}


def _got(sname, switch, cand, header_map, tab_level, ever_y, mand, defaults, allcols):
    pre = P._header_disc_precompute(sname, switch, header_map, tab_level, mand, defaults)
    return P._header_disc_combine(pre, sname, switch, cand, ever_y, allcols)


def _cases():
    ev1 = {f"{SNAME}\t{SW1}=c1\tGR_FILTER_ALL_ENTRIES=True"}
    ev2 = {f"{SNAME}\t{SW1}=c9\tENTRY_WT_DC_ENABLED=True"}
    for sw in (SW1, SW2, SW3, SW4):
        for cand in ("c1", "OFF"):
            for ever in (set(), ev1, ev2):
                for mand in (MAND, set()):
                    for tl in (TABLEVEL, set()):
                        for allcols in (False, True):
                            yield sw, cand, ever, mand, tl, allcols


def test_combine_matches_reference():
    n = 0
    for sw, cand, ever, mand, tl, allcols in _cases():
        notes_ref, got = [], _got(SNAME, sw, cand, HDRS, tl, ever, mand, DEFAULTS, allcols)
        ref = _ref(SNAME, sw, cand, HDRS, tl, ever, mand, DEFAULTS, allcols, notes_ref)
        assert got["yellow"] == ref["yellow"], (sw, cand)
        assert got["hdrs"] == ref["hdrs"], (sw, cand)
        assert got["h2f"] == ref["h2f"], (sw, cand)
        assert got["excluded_tab_level"] == ref["excluded_tab_level"], (sw, cand)
        assert got["excluded_unwired"] == ref["excluded_unwired"], (sw, cand)
        assert got["type_skipped"] == ref["type_skipped"], (sw, cand)
        assert got["mand_notes"] == notes_ref, (sw, cand)
        n += 1
    assert n == 4 * 2 * 3 * 2 * 2 * 2


def test_ever_only_header_and_mand_note():
    ever = {f"{SNAME}\t{SW1}=c1\tGR_FILTER_ALL_ENTRIES=True"}
    got = _got(SNAME, SW1, "c1", HDRS, TABLEVEL, ever, MAND, DEFAULTS, False)
    assert "GR_FILTER_ALL_ENTRIES=True" in got["yellow"] and "GR_FILTER_ALL_ENTRIES=True" in got["hdrs"]
    assert "GR_FILTER_ALL_ENTRIES" not in got["mand_notes"]
    got2 = _got(SNAME, SW1, "c2", HDRS, TABLEVEL, set(), MAND, DEFAULTS, False)
    assert "GR_FILTER_ALL_ENTRIES=True" in got2["yellow"] and "GR_FILTER_ALL_ENTRIES=True" in got2["hdrs"]
    assert "GR_FILTER_ALL_ENTRIES" in got2["mand_notes"]
    got3 = _got(SNAME, SW1, "c2", HDRS, TABLEVEL, set(), set(), DEFAULTS, False)
    assert "GR_FILTER_ALL_ENTRIES=True" not in got3["yellow"]
    got4 = _got(SNAME, SW4, "c1", HDRS, TABLEVEL, set(), set(), DEFAULTS, False)
    assert "EXIT_VELOCITY_WT_MIN_TFS=many" in got4["type_skipped"]
    assert "EXIT_VELOCITY_WT_MIN_TFS=many" not in got4["hdrs"]


def test_precompute_pure_and_deterministic(monkeypatch):
    monkeypatch.setenv("V15_ALL_FILTER_COLS", "1")
    a = P._header_disc_precompute(SNAME, SW1, HDRS, TABLEVEL, MAND, DEFAULTS)
    monkeypatch.setenv("V15_ALL_FILTER_COLS", "0")
    b = P._header_disc_precompute(SNAME, SW1, HDRS, TABLEVEL, MAND, DEFAULTS)
    assert a == b
    assert P._header_disc_combine(a, SNAME, SW1, "c1", set(), False) == P._header_disc_combine(b, SNAME, SW1, "c1", set(), False)


def test_row_static_wiring_present():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "v15_pilot.py").read_text()
    assert "_header_disc_cache" in src and "V15_HEADER_CACHE" in src
    assert "_header_disc_precompute" in src and "_header_disc_combine" in src
