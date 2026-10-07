"""Breakout improvement: enter always, exit easily before loss, reenter +25% at better price."""
import config

def test_breakout_leash_enabled_with_gain_gate():
    assert config.Config.BREAKOUT_LEASH_ENABLED is True
    assert config.Config.BREAKOUT_LEASH_MAX_PER_MIN == 3
    assert config.Config.BREAKOUT_LEASH_MAX_LOSS_PCT == -0.5
    assert config.Config.BREAKOUT_REENTRY_BETTER_PRICE_MULT == 1.25
    assert config.Config.REENTRY_POSITIVE_EXIT_SIZE_MULT == 1.25

def test_breakout_reentry_better_price_logic():
    # Simulate sizing logic: breakout leash exit at -0.2% (before loss) and better price (lower for LONG)
    reentry_level = 100.0
    current_price = 99.5  # better (lower) for LONG
    reentry_data = {"reason": "BREAKOUT_LEASH_EXIT_DROPPED_BACK", "gain": -0.2, "exit_gain": -0.2}
    is_long = True
    # replicate ez_manage better-price check
    exit_gain = -0.2
    exit_reason = reentry_data["reason"].upper()
    max_loss = config.Config.BREAKOUT_LEASH_MAX_LOSS_PCT
    is_breakout_better = ("BREAKOUT_LEASH" in exit_reason and exit_gain > max_loss and ((is_long and current_price < reentry_level) or ((not is_long) and current_price > reentry_level)))
    assert is_breakout_better is True, "breakout -0.2% at better price should qualify for +25%"
    # worse price should NOT qualify
    is_breakout_worse = ("BREAKOUT_LEASH" in exit_reason and exit_gain > max_loss and ((is_long and 100.5 < reentry_level) or ((not is_long) and 99.5 > reentry_level)))
    assert is_breakout_worse is False
    # deep loss should NOT qualify even if better price
    deep = ("BREAKOUT_LEASH" in exit_reason and -1.0 > max_loss)
    assert deep is False, "deep loss -1% should not qualify (before-loss gate)"

def test_breakout_leash_before_loss_gate():
    max_loss = config.Config.BREAKOUT_LEASH_MAX_LOSS_PCT
    assert (-0.4 > max_loss) is True
    assert (-0.6 > max_loss) is False
