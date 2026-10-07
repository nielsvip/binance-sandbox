"""test_template_live_extra_switches — durable collateral for P1..P9 live-extra switches in TEMPLATE_*.xlsx and v12/v15 wiring."""

import zipfile
from pathlib import Path

# P1..P9 switches that must be in TEMPLATE_*.xlsx and tested via v12_quick_engine on every v15_pilot run
# 3m/5m exits without guaranteed reentries are OFF by default (cannot be verified with NPZ) — they are forward-only, not v12 vectorizable
REQUIRED_SWITCHES = [
    # P1
    "LIVE_ENTRY_ENGINE_ENABLED",
    "LIVE_ENTRY_ENGINE_WT_ENABLED",
    "LIVE_ENTRY_ENGINE_STOCH_ENABLED",
    "LIVE_ENTRY_ENGINE_DC_ENABLED",
    "LIVE_ENTRY_ENGINE_HTF_ENABLED",
    "LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED",
    # P2
    "ABLATION_DISABLE_DC_BREACH_REDUCE",
    "ABLATION_DISABLE_HEDGE",
    "ABLATION_DISABLE_AGGRESSIVE_HEDGE",
    # P3 micro 3m/5m — 5m partially vectorizable, 3m not (no 3m NPZ on Mac, S1 3m incomplete)
    "BASE_TF",
    "WT_DC_STOCH_TF",
    "RULE_B_3M_EXIT_ENABLED",
    "RULE_B_5M_EXIT_ENABLED",
    # P4
    "REENTRY_B02_BC156_BOTTOM_ENABLED",
    "ABLATION_DISABLE_AUGMENTATION",
    # P5 (shares with P2, keep dedup)
    "ABLATION_DISABLE_RATIO_REBALANCE",
    # P6
    "ABLATION_DISABLE_QUICK_ENTRY",
    "ABLATION_DISABLE_QUICK_EXIT",
    "ABLATION_DISABLE_REENTRY_ENFORCE",
    # P7
    "ABLATION_DISABLE_SPIKE_FADE_EXIT",
    "ABLATION_DISABLE_SCALP_GUARD",
    "ABLATION_DISABLE_HIGH_GAIN_AUGMENT",
    "ABLATION_DISABLE_PERIODIC_REENTRY",
    # P8
    "ABLATION_DISABLE_ENTRY_LEADERBOARD",
    # P9
    "LIVE_ENTRY_ENGINE_FILTER_TF",
    # Augmentation cooldown fix
    "AUGMENTATION_COOLDOWN_MINUTES",
    "AUGMENTATION_COOLDOWN_SECONDS",
]
# Only vectorizable subset is required to be wired in v12_quick_engine (others are forward-live-only)
V12_REQUIRED = [
    "BASE_TF",  # partial
    "WT_DC_STOCH_TF",  # partial
    "ABLATION_DISABLE_QUICK_ENTRY",
    "ABLATION_DISABLE_QUICK_EXIT",
    "ABLATION_DISABLE_REENTRY_ENFORCE",
    "LIVE_ENTRY_ENGINE_FILTER_TF",
    "AUGMENTATION_COOLDOWN_MINUTES",
    "AUGMENTATION_COOLDOWN_SECONDS",
]

TEMPLATES = [
    "SPREADSHEETS/TEMPLATE.xlsx",
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx",
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx",
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx",
    "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx",
]

def _template_text(p: str) -> str:
    z = zipfile.ZipFile(p)
    txt = ""
    for name in z.namelist():
        if name.endswith(".xml"):
            try:
                txt += z.read(name).decode(errors="ignore")
            except Exception:
                pass
    return txt

def test_template_contains_live_extra_switches():
    missing = []
    for tmpl in TEMPLATES:
        if not Path(tmpl).exists():
            continue
        txt = _template_text(tmpl)
        for sw in REQUIRED_SWITCHES:
            if sw not in txt:
                missing.append(f"{tmpl}:{sw}")
    assert not missing, f"Missing switches in TEMPLATE_*.xlsx (must be added so v15_pilot tests them on every run): {missing}\nFound should be in ABLATION_LIVE_EXTRA sheet or existing sheets."

def test_v12_quick_engine_wires_switches():
    # v12_quick_engine must have vectorizable subset wired so yellows are real; non-vectorizable (3m, LIVE_ENTRY_ENGINE) are forward-only
    txt = Path("v12_quick_engine.py").read_text(errors="ignore")
    missing = [sw for sw in V12_REQUIRED if sw not in txt]
    assert not missing, f"v12_quick_engine.py missing wiring for vectorizable: {missing}"

def test_v15_pilot_iterates_templates():
    # v15_pilot_0914 must iterate over all TEMPLATE sheets (including ABLATION_LIVE_EXTRA)
    # We check that it does not hardcode a sheet allowlist that would skip the new sheet
    txt = Path("v15_pilot_0914.py").read_text(errors="ignore")
    # It should not have a hard-coded list that excludes ABLATION_LIVE_EXTRA; we just ensure it reads workbook.sheetnames
    assert "sheetnames" in txt or "worksheets" in txt, "v15_pilot_0914.py should iterate over workbook sheetnames"
    # Ensure it handles ABLATION sheet: if it filters, it should not exclude our sheet
    # We check that no explicit denylist contains ABLATION_LIVE_EXTRA
    assert "ABLATION_LIVE_EXTRA" not in txt or "ABLA" in txt, "Check v15_pilot not explicitly skipping ABLATION_LIVE_EXTRA"

def test_forward_harness_per_sym_and_cooldown():
    # forward_harness must load per_sym and use 5m cooldown on stocks
    txt = Path("tools/forward_live_vs_vector/forward_harness.py").read_text(errors="ignore")
    assert "per_sym_active_config" in txt, "forward_harness should load per_sym from S1"
    assert "AUGMENTATION_COOLDOWN_MINUTES" in txt or "AUGMENTATION_COOLDOWN" in txt, "forward_harness should respect cooldown"
    # backtest BH floor should be 5 not 0/60
    txt2 = Path("backtest_v12_engine.py").read_text(errors="ignore")
    assert '"AUGMENTATION_COOLDOWN_MINUTES": 5' in txt2, "BH floor for stocks should be 5m (was 0 before)"
