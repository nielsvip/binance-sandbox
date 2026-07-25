from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import vec_band_ladder_walkforward as ladder


def test_remembered_ladder_is_clipped_to_eight_x():
    assert ladder.ladder_mult(0.0, 10.0, 6.0, "linear") == 8.0
    assert ladder.ladder_mult(1.0, 10.0, 6.0, "linear") == 6.0


def test_below_lower_band_is_zero_and_above_is_top():
    assert ladder.ladder_mult(-0.01, 6.0, 4.0, "linear") == 0.0
    assert ladder.ladder_mult(1.01, 6.0, 4.0, "linear") == 4.0


def test_center_plateau_matches_live_function_shape():
    assert ladder.ladder_mult(0.2, 6.0, 4.0, "center_plateau") == 6.0
    assert ladder.ladder_mult(0.5, 6.0, 4.0, "center_plateau") == 6.0
    assert ladder.ladder_mult(1.0, 6.0, 4.0, "center_plateau") == 4.0


def test_block_generator_preserves_valid_pairs():
    curves = ladder._curves(17, 30)
    assert len(curves) >= 30
    for curve in curves:
        for tf in ladder.TF_ORDER:
            bottom, top = curve.pair(tf)
            assert bottom >= top >= 0
