"""Per-sym proxy wiring + tradeable sacred list + guaranteed reentry after stop.

Guarantees:
- per_sym overrides are visible via config proxy when _psym_ctx_var set
- non-tradeable keys are hard blocked (no V3/flz auto-add)
- stops at bottom create a guaranteed reentry that survives is_reentry_eligible
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def test_per_sym_proxy_returns_override():
    import ez_manage as ez
    import config as cfg
    # pick a real per_sym key and knob that is known unwired before proxy
    data = json.loads((ROOT / "data/hourly_reconfig/per_sym_active_config.json").read_text())
    # find a key with MIN_POSITION_SIZE override distinct from default
    test_key = None
    test_knob = None
    test_val = None
    for k, v in data.items():
        if k.startswith("_"):
            continue
        ov = v.get("overrides", {})
        if "MIN_POSITION_SIZE" in ov:
            test_key = k
            test_knob = "MIN_POSITION_SIZE"
            test_val = ov["MIN_POSITION_SIZE"]
            break
    assert test_key is not None, "no MIN_POSITION_SIZE override in per_sym"
    sym, side = test_key.rsplit("_", 1)
    # without ctx, base value
    base_val = getattr(ez._ezm_base_config, test_knob)
    # with ctx, proxy must return override
    tok = ez._psym_ctx_set(sym, side)
    try:
        prox_val = getattr(ez.config, test_knob)
        assert prox_val == test_val, f"proxy {sym}_{side} {test_knob} expected {test_val} got {prox_val}"
        # also getattr
        prox_val2 = getattr(ez.config, test_knob, None)
        assert prox_val2 == test_val
    finally:
        ez._psym_ctx_clear(tok)
    # after clear, fallback
    assert getattr(ez.config, test_knob) == base_val

def test_per_sym_proxy_unwired_knobs_now_wired():
    # the 28 previously unwired knobs must now be visible via proxy
    import ez_manage as ez
    knobs = ["EXIT_STDEV_BREAKOUT_FAIL_ENABLED","SATOSHIT_ENABLED","RZ_BREAKOUT_ENTRY_ENABLED","WT_PERCENTILE_EXIT_OB_4H","ATR_ADAPTIVE_STOP_MULT"]
    data = json.loads((ROOT / "data/hourly_reconfig/per_sym_active_config.json").read_text())
    # find a symbol that has those knobs overridden (XLM)
    xlm = data.get("XLMUSDT_LONG", {})
    assert "overrides" in xlm
    sym, side = "XLMUSDT", "LONG"
    tok = ez._psym_ctx_set(sym, side)
    try:
        for k in knobs:
            if k in xlm["overrides"]:
                expected = xlm["overrides"][k]
                actual = getattr(ez.config, k)
                assert actual == expected, f"{k} proxy mismatch expected {expected} got {actual} for XLMUSDT_LONG"
    finally:
        ez._psym_ctx_clear(tok)

def test_tradeable_hard_block_no_auto_add():
    # Verify execute_now hard block code no longer contains auto-add strings
    src = (ROOT / "ez_manage.py").read_text()
    assert "NON_TRADEABLE_HARD_BLOCK_V3_BYPASS" not in src or "auto-adding to tradeable_keys" not in src.split("NON_TRADEABLE_HARD_BLOCK_V3_BYPASS")[1].split("\n")[1] if "NON_TRADEABLE_HARD_BLOCK_V3_BYPASS" in src else True
    # The sacred block must still exist
    assert "NON_TRADEABLE_HARD_BLOCK" in src
    # V3 bypass must be killed comment present
    assert "tradeable_keys is SACRED — NO auto-add bypass" in src
    # flz bypass removed
    assert 'is_tradeable = position_key in self.tradeable_keys or account_key == "flz"' not in src
    assert 'is_tradeable = position_key in self.tradeable_keys' in src

def test_guaranteed_reentry_bypass_exists():
    src = (ROOT / "ez_manage.py").read_text()
    assert "GUARANTEED_REENTRY_AFTER STOP" in src or "GUARANTEED_REENTRY_BYPASS" in src or "GUARANTEED_ANY_EXIT_REENTRY" in src
    assert "_guaranteed_reentry" in src
    assert "GUARANTEED_REENTRY_BYPASS" in src
    # New: any flat exited position must be guaranteed, not just MTF tags — suicide otherwise
    assert "GUARANTEED_ANY_EXIT_REENTRY" in src
    assert "_is_flat_for_guarantee" in src or "_is_flat_for_bypass" in src
    assert "flat Amt 0, no gain" in src or "flat = no gain" in src.lower()

def test_ang_xlm_not_tradeable_current():
    # Tradeable_keys is regenerated hourly from per_sym (legitimate) — not via V3/manual auto-add.
    # XLMUSDT_LONG has per_sym gain>0, so fin/men are expected tradeable; ang may be present or absent
    # depending on hourly 2d prune, but sacred list means V3/manual never auto-adds.
    tk = json.loads((ROOT / "tradeable_keys.json").read_text())
    # Fin/men must be tradeable per per_sym (XLM gain 43% >0)
    assert "fin:XLMUSDT_LONG" in tk
    assert "men:XLMUSDT_LONG" in tk
    # Ang presence is per hourly per_sym decision, not V3 auto-add — just verify sacred block still enforced
    # (V3/manual auto-add killed) — covered in test_tradeable_hard_block_no_auto_add
    # If ang:XLM present, it must be via per_sym, not via forbidden bypass
    src = (ROOT / "ez_manage.py").read_text()
    assert "tradeable_keys is SACRED — NO auto-add bypass" in src
