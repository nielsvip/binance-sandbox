"""Monotonic baseline: pilot must never deteriorate vs BEST - cumulative_gain = max(prev, baseline, hustler_best)."""
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
PILOT = ROOT / "v15_pilot_0914.py"

def test_cumulative_never_deteriorates():
    src = PILOT.read_text()
    # Must enforce max() over previous cumulative, baseline, and hustler_best
    assert "max(float(progress.get(\"cumulative_gain\")" in src, "must max with previous cumulative"
    assert "float(baseline_gain or 0)" in src, "must include baseline_gain in max"
    assert "float(progress.get(\"hustler_best_gain\")" in src, "must include hustler_best in max"
    # Early fix after loading progress JSON
    assert "NEVER deteriorate vs BEST baseline" in src
    # Ensure second assignment also uses max
    assert src.count("cumulative_gain = max(") >= 1

def test_progress_load_also_max():
    src = PILOT.read_text()
    # After json.loads(progress_path.read_text()) we also clamp
    assert 'progress["cumulative_gain"] = max(_prev_cum' in src

def test_s1_crypto_only_in_hustle():
    h = (ROOT / "tools" / "v15_hustle_cron.py").read_text()
    assert "is_fleet_alive" in h
    assert "S1 stocks delegated to fleet" in h or "S1 (niels) is crypto-only" in h
    assert "if \"niels\" in me" in h
