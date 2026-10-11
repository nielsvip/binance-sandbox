"""SPS $45 sizing semantics (USER 2026-10-11): base $45, 365D-confirmed gainers to 4x ($180),
losers and unconfirmed-365 trade always but small (never blocked).

Light by design: source-regex pins + pure helper tests + live-JSON invariants. No engine imports.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import v15_persym_size_tiers as tiers_mod

ROOT = Path(__file__).resolve().parent.parent


def _read(name):
    return (ROOT / name).read_text()


def _fnum(src, name):
    m = re.search(rf"{name}:\s*float\s*=\s*\(\s*([0-9.]+)", src)
    assert m, f"{name} missing from config.py"
    return float(m.group(1))


def test_sps_base_is_45_class_and_mode_dicts():
    src = _read("config.py")
    assert _fnum(src, "START_POSITION_SIZE") == 45.0
    n = len(re.findall(r'"START_POSITION_SIZE": 45\.0,  # USER 2026-10-11', src))
    assert n == 2, f"light + base dicts must carry SPS 45 (USER tag), found {n}"


def test_quickconfig_sps_tandem_is_45():
    src = _read("v12_quick_engine.py")
    m = re.search(r"START_POSITION_SIZE:\s*float\s*=\s*\(\s*([0-9.]+)", src)
    assert m and float(m.group(1)) == 45.0, "QuickConfig SPS must mirror config 45.0"


def test_light_caps_fit_gainer_steps():
    src = _read("config.py")
    assert '"MAX_ORDER_VALUE_FIN": 180.0,  # USER 2026-10-11' in src
    assert src.count('"MAX_POSITION_SIZE_MEN": 360.0,') == 2, "light + base MEN totals 360"
    assert src.count('"MAX_POSITION_SIZE_FIN": 360.0,') == 2, "light + base FIN totals 360"


def test_untested_flat_mult_declared_and_used():
    src = _read("config.py")
    assert _fnum(src, "UNTESTED_FLAT_SPS_MULT") == 0.25
    live = _read("ez_manage.py")
    assert 'getattr(config, "UNTESTED_FLAT_SPS_MULT", 0.25)' in live
    assert "_ufs.flat_sps_quantity(_oc_base * _oc_fmult, current_price)" in live


def test_size_ranked_requires_365d_confirmation():
    m, t = tiers_mod._size_ranked({"g30": 24.0, "g365": 35.0}, 4.0, 20.0, 0.5)
    assert t == "RANKED" and m == 4.0, f"confirmed gainer must reach 4x, got {m} {t}"
    m, t = tiers_mod._size_ranked({"g30": 24.0, "g365": None}, 4.0, 20.0, 0.5)
    assert (m, t) == (0.5, "RANKED_NO365_SMALL"), f"unconfirmed must trade small, got {m} {t}"
    m, t = tiers_mod._size_ranked({"g30": 24.0, "g365": -3.0}, 4.0, 20.0, 0.5)
    assert (m, t) == (0.5, "RANKED_NEG365_SMALL"), f"365D-negative must trade small, got {m} {t}"
    m, t = tiers_mod._size_ranked({"g30": 1.4, "g365": 12.9}, 4.0, 20.0, 0.5)
    assert t == "RANKED" and 1.0 < m < 4.0, f"small confirmed gainer scales, got {m}"


def test_live_tiers_invariants():
    p = ROOT / "data" / "persym_size_tiers.json"
    if not p.exists():
        raise AssertionError("persym_size_tiers.json missing — regen via tools/v15_persym_size_tiers.py")
    d = json.loads(p.read_text())
    ts = d["tiers"]
    assert d["_meta"]["rules"]["max_mult"] == 4.0
    assert d["_meta"]["rules"]["unconfirmed_mult"] == 0.5
    for ss, v in ts.items():
        assert 0.25 <= v["mult"] <= 4.0, f"{ss} mult {v['mult']} outside [0.25, 4.0]"
        assert v["tier"] != "RANKED_NEG365_CAP1", f"{ss} still on retired CAP1 tier"
        if v["tier"] == "RANKED":
            assert (v.get("g365") or 0) > 0, f"{ss} RANKED without 365D confirmation"
    sps = _fnum(_read("config.py"), "START_POSITION_SIZE")
    assert sps * 0.25 >= 5.0, "smallest tier must clear the $5 futures notional"


def test_effective_step_arithmetic():
    sps = _fnum(_read("config.py"), "START_POSITION_SIZE")
    hard = _fnum(_read("config.py"), "OPEN_CEIL_HARD_MAX_USD")
    assert min(sps * 4.0, hard) == 180.0, "confirmed gainer step must be $180"
    assert sps * 0.25 == 11.25, "loser/untested step must be $11.25 (trades, small)"
    assert sps * 0.5 == 22.5, "unconfirmed-365 step must be $22.50 (trades, small)"
