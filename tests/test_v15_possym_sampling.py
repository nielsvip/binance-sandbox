"""Selective compute lock-in: possym sampling probabilities + enablement (USER 2026-10-07).

Every future pilot run must evaluate only a pos_sym-weighted sample (0->1/20, 1->1/10, 2->1/6,
3->1/2, >=4 always) with a deterministic per-row draw, and zero-formula skip defaults ON.
Fleet layers (verified live 2026-10-07): scheduler env V15_POSSYM_SAMPLING=1 on new launches +
data/possym_sampling.flag=1 on s1/s2/s5 as fallback. This file locks the pilot-side contract.
"""
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import v15_pilot as P  # noqa: E402


def test_prob_table_exact():
    assert P._POSSYM_P == {0: 1.0 / 20, 1: 1.0 / 10, 2: 1.0 / 6, 3: 1.0 / 2}


def test_decide_always_compute_cases(monkeypatch):
    monkeypatch.setattr(P, "_POSSYM_MIN_N", 3)
    assert P._possym_decide("A_LONG", "T", "K", "r1", 4, 99)[0] is True
    assert P._possym_decide("A_LONG", "T", "K", "r1", 99, 99)[0] is True
    assert P._possym_decide("A_LONG", "T", "K", "r1", 0, 2)[0] is True
    assert P._possym_decide("A_LONG", "T", "K", "r1", 1, None)[0] is True
    c, b, p, u = P._possym_decide("A_LONG", "T", "K", "r1", 0, 99, new=True)
    assert (c, b, p, u) == (True, "new_row", None, None)


def test_decide_buckets_and_prob(monkeypatch):
    monkeypatch.setattr(P, "_POSSYM_MIN_N", 3)
    c, b, p, u = P._possym_decide("A_LONG", "T", "K", "r1", 2, 99)
    assert (b, p) == ("pos=2", 1.0 / 6) and isinstance(c, bool) and 0.0 <= u < 1.0
    c, b, p, u = P._possym_decide("A_LONG", "T", "K", "r1", None, 99)
    assert (b, p) == ("pos=None", 1.0 / 20)


def test_draw_deterministic():
    a = P._possym_decide("A_LONG", "T", "K", "round7", 0, 99)
    b = P._possym_decide("A_LONG", "T", "K", "round7", 0, 99)
    assert a == b
    assert P._possym_draw("A_LONG", "T", "K", "r1") == P._possym_draw("A_LONG", "T", "K", "r1")


def test_pos0_rate_band(monkeypatch):
    monkeypatch.setattr(P, "_POSSYM_MIN_N", 3)
    n, hit = 2000, 0
    for i in range(n):
        if P._possym_decide("A_LONG", "T", f"SW{i}=v", "round7", 0, 99)[0]:
            hit += 1
    assert 0.01 < hit / n < 0.12, hit / n


def test_enabled_env_wins(monkeypatch):
    monkeypatch.setenv("V15_POSSYM_SAMPLING", "1")
    assert P._possym_enabled("run99") is True
    monkeypatch.setenv("V15_POSSYM_SAMPLING", "0")
    assert P._possym_enabled("run99") is False


def test_zero_skip_default_on(monkeypatch):
    monkeypatch.delenv("V15_ZERO_FORMULA_SKIP", raising=False)
    assert P._zero_enabled() is True
    monkeypatch.setenv("V15_ZERO_FORMULA_SKIP", "0")
    assert P._zero_enabled() is False


def test_sampler_input_files_exist_fleet_contract():
    assert (ROOT / "data" / "possym_sampling.flag").read_text().strip() == "1"
