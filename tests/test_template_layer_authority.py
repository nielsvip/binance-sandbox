"""USER LAW 2026-10-29: TEMPLATE_ is the source of truth; no config, v12 or live script may override a switch the template
has for the cat_side. The engine enforces it: an unlayered QuickConfig handed to simulate_one receives the TEMPLATE_{cat_side}
layer for every field still at its raw class value; fields the caller set explicitly are untouched; a layered (stamped) config
is never re-layered (per-sym overrides stay)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import per_sym_settings as PSS  # noqa: E402
import v12_quick_engine as V  # noqa: E402


def _diff_keys(cat_side, ref):
    lay = PSS.defaults(cat_side)
    return {k: v for k, v in lay.items() if hasattr(ref, k) and str(getattr(ref, k)) != str(v)}


def test_raw_crypto_config_gets_template_layer():
    diffs = _diff_keys("CRYPTO_LONG", V.QuickConfig())
    assert diffs, "census: raw QuickConfig differs from TEMPLATE_CRYPTO_LONG somewhere (else the test is vacuous)"
    cfg = V.ensure_template_layer(V.QuickConfig(), "BTCUSDC", True)
    assert getattr(cfg, "_template_layer") == "CRYPTO_LONG"
    for k, v in diffs.items():
        assert str(getattr(cfg, k)) == str(v), k


def test_explicit_caller_value_survives_and_raw_fields_follow_bold():
    diffs = _diff_keys("CRYPTO_SHORT", V.QuickConfig())
    cfg = V.QuickConfig()
    nums = sorted(k for k in diffs if isinstance(getattr(cfg, k), (int, float)) and not isinstance(getattr(cfg, k), bool))
    assert nums, "need a numeric field whose raw value differs from the bold"
    k_any = nums[0]
    probe = getattr(cfg, k_any) + 7.5  # neither the raw value nor the bold: an explicit caller choice
    assert str(probe) != str(diffs[k_any])
    setattr(cfg, k_any, probe)
    out = V.ensure_template_layer(cfg, "ETHUSDC", False)
    assert str(getattr(out, k_any)) == str(probe)
    assert all(str(getattr(out, k)) == str(diffs[k]) for k in diffs if k != k_any)


def test_layered_config_is_not_relayered():
    cfg = V.QuickConfig()
    n = cfg.apply_cat_side_defaults("STOCKS_LONG")
    assert n > 1000 and getattr(cfg, "_template_layer") == "STOCKS_LONG"
    lay = PSS.defaults("STOCKS_LONG")
    k = sorted(k for k in lay if hasattr(cfg, k) and isinstance(getattr(cfg, k), bool))[0]
    setattr(cfg, k, not getattr(cfg, k))  # a per-sym override applied AFTER the layer
    out = V.ensure_template_layer(cfg, "AAPL", True)
    assert out is cfg and getattr(out, k) != lay[k]


def test_stocks_layer_uses_tradier_reference():
    cfg = V.QuickConfig()
    cfg.apply_tradier_defaults()
    out = V.ensure_template_layer(cfg, "AAPL", False)
    assert getattr(out, "_template_layer") == "STOCKS_SHORT"
    assert out.MODE == "tradier"
    diffs = _diff_keys("STOCKS_SHORT", (lambda c: (c.apply_tradier_defaults(), c)[1])(V.QuickConfig()))
    for k, v in diffs.items():
        assert str(getattr(out, k)) == str(v), k


def test_mode_crypto_without_layer_is_never_silent(capsys):
    V._TEMPLATE_LAYER_ANNOUNCED.clear()
    V.ensure_template_layer(V.QuickConfig(), "SOLUSDC", True)
    assert "[TEMPLATE-LAYER-AUTO]" in capsys.readouterr().out
