import importlib.util
from collections import defaultdict
from pathlib import Path

from tools import v18_brain as brain


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "data/reports/v22_director_20261031" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


counterfactual = load("ema_counterfactual_test", "frozen_tab_ema_counterfactual.py")
diagnostic = load("ema_diagnostic_test", "frozen_prefix_f0_diagnostic.py")


def test_reconstruction_proves_actual_full_frozen_state_and_detects_changed_tab_value():
    model = brain.CatBrain("CRYPTO_LONG", brain.Buckets(), diagnostic.CUT)
    ema = defaultdict(lambda: 0.02)
    for index, delta in enumerate((0.4, -0.4, 0.0, 0.3)):
        event = {"e": "row", "s": "BTCUSDC_LONG", "b": "BTCUSDC_LONG|2026-10-09T00:00:00Z", "sh": "ENTRY_REVERSAL_BOUNCE", "k": "BB_BOUNCE_ENTRY_TF=4h", "n": [delta, 1, 0.0, None, None, None, None, None, 1.0, 1], "y": []}
        model.handle(event, "2026-10-09T00:00:00Z")
        counterfactual.update_prefix_ema(brain, event, ema)
    model.finish()
    state = brain._state_dump({"meta": {}, "brains": {"CRYPTO_LONG": model}, "tidx": {}})["brains"]["CRYPTO_LONG"]
    expected = diagnostic.fingerprint(model)
    assert counterfactual.reconstructed_fingerprint(state, ema) == expected
    assert state["ema"] == {key: value for key, value in ema.items() if isinstance(key, str)}
    ema[("BTCUSDC_LONG", "ENTRY_REVERSAL_BOUNCE")] += 0.01
    assert counterfactual.reconstructed_fingerprint(state, ema) != expected


def test_invalid_or_cached_naked_does_not_update_ema():
    ema = defaultdict(lambda: 0.02)
    event = {"e": "row", "s": "BTCUSDC_LONG", "sh": "ENTRY_REVERSAL_BOUNCE", "n": [1.0, 0, 0.0, None, None, None, None, None, 1.0, 1]}
    counterfactual.update_prefix_ema(brain, event, ema)
    event["n"][1], event["n"][-1] = 1, 0
    counterfactual.update_prefix_ema(brain, event, ema)
    assert dict(ema) == {"BTCUSDC_LONG": 0.02, ("BTCUSDC_LONG", "ENTRY_REVERSAL_BOUNCE"): 0.02}
