"""OBLIGATORY_GAP_CLOSE (USER 2026-10-13): bad overnight gappers MUST close
at the open (hedge PROHIBITED), then reopen via normal gates + gap-fill
reentry. Tests the pure decision only — no event loop, no broker."""
import tradier_manage as tm


def test_long_gap_down_fires():
    fire, gap, adv = tm._ob_gap_should_close(True, 97.0, 100.0, 2.0)
    assert fire is True
    assert abs(gap + 3.0) < 1e-9
    assert abs(adv - 3.0) < 1e-9


def test_long_gap_up_no_fire():
    fire, gap, adv = tm._ob_gap_should_close(True, 103.0, 100.0, 2.0)
    assert fire is False
    assert adv < 0


def test_short_gap_up_fires():
    fire, gap, adv = tm._ob_gap_should_close(False, 103.0, 100.0, 2.0)
    assert fire is True
    assert abs(adv - 3.0) < 1e-9


def test_short_gap_down_no_fire():
    assert tm._ob_gap_should_close(False, 97.0, 100.0, 2.0)[0] is False


def test_below_threshold_no_fire():
    assert tm._ob_gap_should_close(True, 98.5, 100.0, 2.0)[0] is False
    assert tm._ob_gap_should_close(True, 98.0, 100.0, 2.0)[0] is True


def test_bad_inputs_never_fire():
    assert tm._ob_gap_should_close(True, 0.0, 100.0, 2.0)[0] is False
    assert tm._ob_gap_should_close(True, 97.0, 0.0, 2.0)[0] is False
    assert tm._ob_gap_should_close(True, 97.0, 100.0, 0.0)[0] is False
    assert tm._ob_gap_should_close(True, None, None, 2.0)[0] is False


def test_knobs_present_and_on():
    from config_tradier import TradierConfig
    cfg = TradierConfig()
    assert cfg.OB_GAP_CLOSE_ENABLED is True
    assert float(cfg.OB_GAP_CLOSE_MIN_ADVERSE_PCT) == 2.0


def test_hedge_loop_stays_off():
    from config_tradier import TradierConfig
    cfg = TradierConfig()
    assert cfg.OBLIGATORY_HEDGE_ENABLED is False
    assert cfg.OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED is False
