"""VETO-FINAL structural pins (2026-10-13): KINDERGARTEN/EMA_9_21 hard vetoes must gate the FINAL entry signal.

Root cause proven on S1: the b6 stocks veto (and crypto KG/E921 vetoes) applied BEFORE
post-veto OR-sources (twin_entries_dead_a/b, twin_yellow_filters, ported_reentry,
gr_htf_direct, wt_top_entry). teda OR restored 187 veto-removed bars on AAPL_SHORT:
TF=D veto mask all-False yet 30 trades. Live vetoes the entry DECISION; the vec now
re-applies stashed veto masks just before the sim loop. Behavioral proof runs on S1
(NPZs live there); these pins guard the structure on Mac.
"""
from pathlib import Path

ENG = Path(__file__).resolve().parent.parent / "v12_quick_engine.py"
SRC = ENG.read_text().splitlines()


def _ln(sub, start=0):
    for i in range(start, len(SRC)):
        if sub in SRC[i]:
            return i
    raise AssertionError(f"missing: {sub}")


def test_stash_init_before_b6():
    init = _ln("_lks_ok_final = None")
    home = _ln("_lks_ok = _lks.pass_mask(")
    assert init < home
    assert "_kg2_ok_final = None" in SRC[init + 1]
    assert "_e921_ok_final = None" in SRC[init + 2]


def test_home_blocks_assign_stashes():
    assert any("_lks_ok_final = np.asarray(_lks_ok" in l for l in SRC)
    assert any("_kg2_ok_final = np.asarray(_kg2_ok" in l for l in SRC)
    assert any("_e921_ok_final = np.asarray(_e921_ok" in l for l in SRC)


def test_final_reapply_after_every_or_before_loop():
    teda = _ln("entry_sig = entry_sig | np.asarray(_teda_entry")
    tyf = _ln("_tyf.apply_yellow_entry_masks(")
    prz = _ln("_prz.apply(")
    wte = _ln("entry_sig = entry_sig | _wte_pre_mask")
    reap = _ln("if _lks_ok_final is not None:")
    loop = _ln("for i in range(n):")
    assert teda < reap and tyf < reap and prz < reap and wte < reap < loop


def test_reapply_is_and_only():
    reap = _ln("if _lks_ok_final is not None:")
    block = "\n".join(SRC[reap:reap + 6])
    assert "|" not in block
    assert block.count("entry_sig = entry_sig & _") == 3


def test_single_masks_append_per_veto():
    joinsrc = "\n".join(SRC)
    assert joinsrc.count("_entry_filter_masks.append(_lks_ok)") == 1
    assert joinsrc.count("_entry_filter_masks.append(_kg2_ok)") == 1
    assert joinsrc.count("_entry_filter_masks.append(np.asarray(_e921_ok, dtype=bool))") == 1
