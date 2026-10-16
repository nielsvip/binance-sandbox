"""Locks in the 2026-10-15 dataflow verdicts for the three filters the pilot PAINTED but DROPPED on the
WT-cross-exit rows (WT_CROSS_EXIT_APPLIES_TO_WINNERS / _LOSERS, TEMPLATE_STOCKS_LONG!EXIT_VELOCITY).

Each verdict is DROP, and each is proven here the way BACKTEST_BIBLE §18/§19 demands — by dataflow, never by a
grep count. The point of the file is that the next agent cannot "rescue" these names on a `\\bNAME\\b` hit: a
rescue has to break a test that names the exact missing predicate or the exact missing NPZ field.

Run: python3 -I -m pytest tests/test_unwired_wt_cross_exit_filters.py -q
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = (
    "EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF",
    "EXIT_TO_REDUCE_ADAPTER_FILTER_TF",
    "MANDATORY_REENTRY_WT_FILTER_TF_MODE",
)
# The live-side coverage farms: every read inside them is `if bool(_cfg/getattr(NAME, ...)): _ = 1` and NONE of the
# functions has a caller, so such a read is not a consumer (same list as tools/v15_deadvec_wiring_audit.LIVE_DEAD_FUNCS).
LIVE_FARMS = ("_ensure_ez_all", "_ensure_tradier_all", "_full_coverage_read", "_full_coverage_read_ez")


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(errors="replace")


def _func_ranges(src: str, names):
    lines = src.splitlines()
    defs = [(i, m.group(1)) for i, l in enumerate(lines) if (m := re.match(r"^def (\w+)", l))]
    out = []
    for idx, (i, nm) in enumerate(defs):
        if nm in names:
            out.append((i, defs[idx + 1][0] if idx + 1 < len(defs) else len(lines)))
    return out


def test_all_three_stay_in_vec_unwired():
    """They are skipped via data/vec_unwired.json -> v15_pilot.UNWIRED_FILTERS, with the proof recorded in-file."""
    d = json.loads(_src("data/vec_unwired.json"))
    for name in TARGETS:
        assert name in d["filters"], f"{name} must stay in vec_unwired filters — see _batch4_20261015"
    assert "_batch4_20261015" in d, "the dataflow proof note must stay with the keys it justifies"


def test_live_coverage_farms_have_no_callers():
    """The whole 'live-wired' claim for the two EXIT_* names rests on these farms, so prove they are dead."""
    blob = "".join(
        _src(f) for f in ("ez_manage.py", "tradier_manage.py", "ez_positions_quick.py", "backtest_v12_engine.py")
    )
    for fn in ("_ensure_ez_all", "_ensure_tradier_all"):
        calls = re.findall(rf"(?<!def ){fn}\s*\(", blob)
        assert not calls, f"{fn} gained a caller — re-audit every switch excluded because its only live read is there"


def test_exit_scorer_and_adapter_have_no_live_consumer():
    """Their only live reads sit inside the uncalled farms (ez_manage.py:63899/63907, tradier_manage.py:60174/60186)."""
    for rel in ("ez_manage.py", "tradier_manage.py", "ez_positions_quick.py"):
        src = _src(rel)
        farms = _func_ranges(src, LIVE_FARMS)
        for name in ("EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF", "EXIT_TO_REDUCE_ADAPTER_FILTER_TF"):
            for i, ln in enumerate(src.splitlines()):
                if name not in ln:
                    continue
                in_farm = any(a <= i < b for a, b in farms)
                in_registry = re.match(r"^\s*['\"]" + name + r"['\"],?\s*$", ln) is not None
                assert in_farm or in_registry, f"{rel}:{i + 1} is a NEW live read of {name} — re-decide the verdict"


def test_exit_scorer_and_adapter_have_no_vec_read():
    """v12_quick_engine must not read them outside the inert blocks, and no vec_decisions module may read them."""
    src = _src("v12_quick_engine.py")
    for name in ("EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF", "EXIT_TO_REDUCE_ADAPTER_FILTER_TF"):
        hits = [
            i + 1
            for i, ln in enumerate(src.splitlines())
            if re.search(rf'(?:cfg\.{name}\b|getattr\(\s*cfg\s*,\s*"{name}")', ln)
        ]
        assert not hits, f"v12_quick_engine now reads {name} at {hits} — vectorise it properly or re-prove the drop"
    import vec_decisions.generic_filter_tf as G

    for name in ("EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF", "EXIT_TO_REDUCE_ADAPTER_FILTER_TF"):
        assert name not in G.FILTER_TF_MAP, f"{name} joined FILTER_TF_MAP — it is no longer inert"
    import vec_decisions.twin_yellow_filters as T

    for name in ("EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF", "EXIT_TO_REDUCE_ADAPTER_FILTER_TF"):
        assert T.REGISTRY[name]["kind"] == "none", f"{name} gained a twin kind — wire it or revert"


def test_exit_to_reduce_adapter_is_vec_only_and_tfless():
    """The named module is a CLOSE->REDUCE event-label adapter: no live path, no TF term, not in the sweep engine."""
    mod = _src("vec_paths/exit_to_reduce_adapter.py")
    assert not re.search(r"_TF\b|FILTER_TF", mod), "the adapter gained a TF term — re-decide the verdict"
    for rel in ("v12_quick_engine.py", "backtest_v12_engine.py", "ez_manage.py", "tradier_manage.py"):
        assert "exit_to_reduce_adapter" not in _src(rel), f"{rel} now imports the adapter — re-audit"


def test_tight_breakout_scorer_needs_sub_15m_fields_the_npz_lacks():
    """The live family (ez_positions_quick.rate:6433) is real but hardcodes 1m/3m TFs and live staleness."""
    mod = _src("vec_paths/exit_tight_breakout_scorer.py")
    for need in ("k_1m", "k_1m_prev", "d_1m", "k_3m", "d_3m", "true_lag"):
        assert need in mod, f"{need} left the live predicate — re-derive what a vectorisation would need"
    assert "FILTER_TF" not in mod, "the live scorer still must not read any FILTER_TF knob"


def test_mandatory_reentry_wt_painted_tf_options_are_identical_in_vec():
    """The two PAINTED options ('15m_only', '5m_or_15m') cannot be distinguished without 5m WT in the NPZ.

    Pure-function proof on the real twin: identical verdicts for every bar of a synthetic series, for both sides.
    A 5m WT field in the NPZ (or a 5m leg in the twin) must break this test, because then the axis is measurable
    and the switch has to come OUT of vec_unwired and be delta-tested instead.
    """
    import numpy as np

    import vec_decisions.mandatory_reentry_wt_vec as M

    rng = np.random.default_rng(20261015)
    n = 400
    pre = {
        "w1": rng.normal(0, 30, n),
        "w2": rng.normal(0, 30, n),
        "vel": rng.normal(0, 2, n),
        "bull": rng.random(n) < 0.1,
        "bear": rng.random(n) < 0.1,
    }

    class _Cfg:
        MANDATORY_REENTRY_WT_FILTER_ENABLED = True
        MANDATORY_REENTRY_WT_FILTER_MIN_TFS = 1
        MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP = False
        MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO = 0.90
        MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY = 0.0
        MANDATORY_REENTRY_WT_FILTER_TF_MODE = "15m_only"

    a, b = _Cfg(), _Cfg()
    b.MANDATORY_REENTRY_WT_FILTER_TF_MODE = "5m_or_15m"
    for is_long in (True, False):
        va = [M.wt_gate_ok(a, is_long, i, pre) for i in range(n)]
        vb = [M.wt_gate_ok(b, is_long, i, pre) for i in range(n)]
        assert va == vb, "the two painted TF options now differ in vec — remove the key from vec_unwired and sweep it"
        assert any(va) and not all(va), "degenerate fixture: the gate must both pass and fail somewhere"


def test_mandatory_reentry_wt_has_a_real_live_consumer_and_a_real_vec_call_site():
    """DROP here is about an unmeasurable axis, NOT about an unwired switch — keep both halves provably reachable."""
    tr = _src("tradier_manage.py")
    assert re.search(
        r'tf_mode=_cfg_auto\(\s*\n?\s*"MANDATORY_REENTRY_WT_FILTER_TF_MODE"', tr
    ), "the live tf_mode read moved — re-locate it before trusting this verdict"
    assert "if not _xb_wt_ok:" in tr, "the live gate no longer suppresses the reentry — re-read process_position"
    v12 = _src("v12_quick_engine.py")
    assert "vec_decisions.mandatory_reentry_wt_vec.precompute" in v12
    assert "vec_decisions.mandatory_reentry_wt_vec.leg_ok" in v12
