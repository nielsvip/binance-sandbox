"""2026-10-07 USER KILL: TIER2_FORCED (time-based reopen) DELETED everywhere.

Reentry is ALWAYS technical (TIER1 cross / TIER2 chase / PRICE_CROSS_BACK /
EMA200_1H_BOUNCE) and ONLY on trend continuation. Age alone must never reopen,
in live code or vec twins, crypto or stocks.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vec_decisions import reentry_tiers as rt
from vec_decisions import stocks_reentry_sources as srs


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _base_cfg():
    return Cfg(
        REENTRY_MIN_GAP_MINUTES=0.0,
        REENTRY_RALLY_K15M_MAX=100.0,
        REENTRY_EXHAUSTED_PARTIAL_ENABLED=True,
        REENTRY_TIER2_PRICE_PCT=0.003,
        REENTRY_TIER2_MIN_MINUTES=10.0,
        TRADIER_REENTRY_HARDCOOL_MIN=15.0,
        PRICE_CROSS_BACK_REENTRY_ENABLED=True,
        PRICE_CROSS_BACK_MAX_AGE_MIN=525_600_000.0,
        PRICE_CROSS_BACK_BAND_PCT=0.3,
    )


def test_crypto_no_time_reopen_despite_huge_age():
    cfg = _base_cfg()
    assert rt.tier_fire(True, 90.0, 100.0, 5000.0, cfg, k15=50.0, k15_prev=50.0, d15=50.0) is None
    assert rt.tier_fire(False, 110.0, 100.0, 5000.0, cfg, k15=50.0, k15_prev=50.0, d15=50.0) is None


def test_crypto_technical_tiers_survive():
    cfg = _base_cfg()
    assert rt.tier_fire(True, 100.2, 100.0, 30.0, cfg, k15=50.0, k15_prev=50.0, d15=50.0) == "TIER1"
    # NOTE: at default TIER2_PRICE_PCT=0.3% the TIER2 leg is shadowed (0.3% move always crosses 0.1% first -> TIER1).
    # TIER2 is reachable only when the chase band sits inside the cross band:
    cfg.REENTRY_TIER2_PRICE_PCT = 0.0005
    assert rt.tier_fire(True, 100.06, 100.0, 30.0, cfg, k15=60.0, k15_prev=50.0, d15=50.0) == "TIER2"
    assert rt.tier_fire(False, 99.94, 100.0, 30.0, cfg, k15=40.0, k15_prev=50.0, d15=50.0) == "TIER2"


def test_crypto_helpers_have_no_forced():
    assert rt.tier_score("TIER2_FORCED") == 0.0
    assert rt.tier_reason("TIER2_FORCED", True, 100.0, 100.0, 50.0, 500.0) == ""


def test_stocks_no_time_reopen_despite_huge_age():
    cfg = _base_cfg()
    assert srs.fires(cfg, True, 90.0, 0, 100.0, 5000.0, None, None) == ""
    assert srs.fires(cfg, False, 110.0, 0, 100.0, 5000.0, None, None) == ""


def test_stocks_technical_paths_survive():
    cfg = _base_cfg()
    assert srs.fires(cfg, True, 100.2, 0, 100.0, 30.0, None, None) == "PRICE_CROSS_BACK"


def test_no_forced_references_in_wired_files():
    for rel in ("ez_positions_quick.py", "vec_decisions/reentry_tiers.py",
                "tradier_manage.py", "vec_decisions/stocks_reentry_sources.py",
                "v12_quick_engine.py"):
        for i, line in enumerate((ROOT / rel).read_text().split("\n"), 1):
            if "TIER2_FORCED" in line:
                assert "USER KILL" in line or "KILLED" in line, f"{rel}:{i}: {line.strip()[:100]}"
