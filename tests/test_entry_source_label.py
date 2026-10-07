"""ENTRY_SOURCE_LABEL (2026-10-06 USER: ENTRY_SIGNAL fallback banned).

Every vec OPEN must name the switch + settings that fired it. The generic
'ENTRY_SIGNAL' fallback ("entry because of entry") is replaced by a resolver:
pre-loop non-B OR-masks (dead_b/dead_a/mu/tvs) are captured with their knob
settings, in-loop fires carry _fire_src provenance, and anything unattributed
lands on loud 'ENTRY_SOURCE_UNKNOWN' (never silent).
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import v12_quick_engine as V

V12 = (ROOT / "v12_quick_engine.py").read_text()


def _npz(n=150, stoch_k_1h=None):
    close = np.full(n, 100.0)
    ts = np.arange(n, dtype=float) * 900 + 1_700_000_000
    d = {
        "close": close, "close_15m": close, "timestamps": ts, "timestamp_15m": ts,
        "open_15m": close, "high_15m": close, "low_15m": close, "volume_15m": np.ones(n),
        "stoch_k_15m": np.full(n, 50.0), "stoch_d_15m": np.full(n, 50.0),
        "stoch_k_1h": np.full(n, 50.0) if stoch_k_1h is None else np.asarray(stoch_k_1h, dtype=float),
        "stoch_d_1h": np.full(n, 50.0),
        "stoch_k_4h": np.full(n, 50.0), "stoch_d_4h": np.full(n, 50.0),
        "stoch_k_D": np.full(n, 50.0), "stoch_d_D": np.full(n, 50.0),
        "wt1_15m": np.zeros(n), "wt2_15m": np.zeros(n),
        "wt1_1h": np.zeros(n), "wt2_1h": np.zeros(n),
        "wt1_4h": np.zeros(n), "wt2_4h": np.zeros(n),
        "wt1_D": np.zeros(n), "wt2_D": np.zeros(n),
    }
    return d


def _open_reasons(r):
    out = []
    rows = r.get("ledger") or []
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("trades") or []
    for t in rows:
        if not isinstance(t, dict):
            continue
        if str(t.get("type") or "").upper() == "OPEN":
            out.append(str(t.get("reason") or t.get("entry_reason") or ""))
    return out


def test_no_entry_signal_assignment_left():
    for i, line in enumerate(V12.split("\n")):
        s = line.strip()
        if s.startswith("#"):
            continue
        assert "= 'ENTRY_SIGNAL'" not in line and '= "ENTRY_SIGNAL"' not in line, f"line {i + 1}: {line[:100]}"


def test_or_source_capture_sites():
    assert V12.count("_entry_or_srcs.append") >= 5
    for label in ("DC_BREAKOUT_TF_EXPANDED_", "STOCH_XTREME_ENTRY_", "SMFI_DIV_ENTRY_",
                  "VWAP_STRETCH_ENTRY_PCT", "FUNDING_CROWD_ENTRY_Z", "OI_SURGE_ENTRY_PCT",
                  "RSI2_XTREME_ENTRY_", "MU_REENTRY_TOL", "_fire_src"):
        assert label in V12, label


def test_in_loop_provenance_tags():
    for tag in ("HTF_WT_CHURN_REENTRY_", "TARGET_DC_REENTRY", "SELL_TOP_RECROSS_REENTRY",
                "EPQ_DC_BREAKOUT_REENTRY", "EPQ_B16_SMA200_PULLBACK", "EPQ_MANDATORY_PRICE_CROSS",
                "OBLIGATORY_", "LEGACY_PSR_REENTRY", "SRS_", "REENTRY_TIER_"):
        assert tag in V12, tag


def test_gr_override_keyed_on_attribution():
    assert "_res_attributed" in V12
    assert "if _res_attributed and _grd_entry_fire" in V12


def test_stoch_xtreme_labels_switch_and_tf(monkeypatch):
    n = 150
    k1h = np.full(n, 50.0)
    k1h[50:] = 10.0  # oversold -> STOCH_XTREME long fires
    monkeypatch.setattr(V, "compute_reentry_blocks", lambda *a, **k: {})  # B-blocks silent -> resolver path forced
    cfg = V.QuickConfig()
    cfg.STOCH_XTREME_ENTRY_ENABLED = True
    cfg.STOCH_XTREME_ENTRY_TF = "1h"
    r = V.simulate_one(_npz(n, k1h), "TESTUSDT", True, cfg)
    assert r is not None and "trades" in r
    reasons = _open_reasons(r)
    assert reasons, "expected at least one OPEN from the stoch-xtreme OR-source"
    assert not any("ENTRY_SIGNAL" in x for x in reasons), reasons[:5]
    assert not any("ENTRY_SOURCE_UNKNOWN" in x for x in reasons), reasons[:5]
    assert any(x.startswith("STOCH_XTREME_ENTRY_1H") for x in reasons), reasons[:5]


def test_mu_reentry_labels_switch_and_tol(monkeypatch):
    n = 150
    d = _npz(n)
    d["dc_low_4h"] = np.full(n, 99.0)  # px 100 <= 99*1.02 -> MU lvl fires everywhere
    d["dc_low_1h"] = np.full(n, 99.0)
    monkeypatch.setattr(V, "compute_reentry_blocks", lambda *a, **k: {})  # B-blocks silent -> resolver path forced
    cfg = V.QuickConfig()
    cfg.MU_CORRECTION_REENTRY_ENABLED = True
    cfg.MU_CORRECTION_REENTRY_DC_TOL_PCT = 2.0
    cfg.MU_CORRECTION_SYMBOLS = "TESTUSDT"
    r = V.simulate_one(d, "TESTUSDT", True, cfg)
    assert r is not None and "trades" in r
    reasons = _open_reasons(r)
    assert reasons, "expected at least one OPEN from the MU OR-source"
    assert not any("ENTRY_SIGNAL" in x for x in reasons), reasons[:5]
    assert not any("ENTRY_SOURCE_UNKNOWN" in x for x in reasons), reasons[:5]
    assert any(x.startswith("MU_REENTRY_TOL2PCT") for x in reasons), reasons[:5]
