import sys
sys.path.insert(0, '/Users/niels/Documents/binance')

from v15_pilot import sanitize_overrides, get_defaults_for_symside


def test_sanitize_converts_string_overrides():
    defaults = get_defaults_for_symside('SNDK_LONG')
    tests = {
        'WT_15M_BOUNCE_OPEN_ENABLED': 'False',
        'RZ_BOT_BB_THRESHOLD': '0.1875',
        'BB_PULLBACK_GATE_TF': 'D',
        'LH_HL_FILTER_REQUIRE_BOTH': 'False_ALT',
        'BOUNCE_AUGMENT_K_D_THRESHOLD': '20.0',
        'REENTRY_MANDATORY': 'True',
    }
    san, warns = sanitize_overrides(tests, defaults)
    assert san['WT_15M_BOUNCE_OPEN_ENABLED'] is False
    assert san['REENTRY_MANDATORY'] is True
    assert san['RZ_BOT_BB_THRESHOLD'] == 0.1875 and isinstance(san['RZ_BOT_BB_THRESHOLD'], float)
    assert san['BOUNCE_AUGMENT_K_D_THRESHOLD'] == 20.0 and isinstance(san['BOUNCE_AUGMENT_K_D_THRESHOLD'], float)
    assert san['BB_PULLBACK_GATE_TF'] == 'D'
    assert san['LH_HL_FILTER_REQUIRE_BOTH'] is False


def test_sanitize_alt_stripped_and_numeric():
    defaults = get_defaults_for_symside('SNDK_LONG')
    tests = {
        'LH_HL_FILTER_REQUIRE_BOTH': 'False_ALT',
        'MI_TF_AGREE_MIN': '0.0_ALT',
        'RZ_BOT_BB_THRESHOLD': '0.1125',
    }
    san, _ = sanitize_overrides(tests, defaults)
    assert san['LH_HL_FILTER_REQUIRE_BOTH'] is False
    # numeric string with _ALT should still convert to float and strip ALT
    assert san['RZ_BOT_BB_THRESHOLD'] == 0.1125


def test_v15_skip_live_verify_env_present():
    import pathlib
    p = pathlib.Path('/Users/niels/Documents/binance/v15_pilot.py')
    txt = p.read_text()
    assert 'V15_SKIP_LIVE_VERIFY' in txt
    assert 'SKIPPED via V15_SKIP_LIVE_VERIFY' in txt
