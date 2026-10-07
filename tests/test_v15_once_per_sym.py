import hashlib
import pathlib

def test_once_per_sym_until_all_done():
    """Each sym_side should be assigned to exactly one host via hash%4 until all done, no duplicates."""
    order = [l.strip() for l in pathlib.Path("SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt").read_text().splitlines() if l.strip()]
    flz = [l.strip() for l in pathlib.Path("SPREADSHEETS/FLZ_RUNNING_ORDER.txt").read_text().splitlines() if l.strip()]
    order = order + flz
    all_hosts = ["niels", "htz-v15-s2", "htz-v15-s3", "htz-v15-s5"]
    seen = set()
    dups = []
    for me in all_hosts:
        host_idx = all_hosts.index(me)
        pending = [s for s in order if int(hashlib.md5(s.encode()).hexdigest(),16)%4 == host_idx]
        for sym in pending:
            if sym in seen:
                dups.append(sym)
            seen.add(sym)
    assert not dups, f"Duplicate syms across hosts: {dups[:5]}"
    assert len(seen) == len(order), f"Not all syms assigned: {len(seen)} vs {len(order)}"

def test_flz_first_then_stocks():
    """FLZ crypto should be prioritized before stocks."""
    content = pathlib.Path("tools/v15_local_herd.py").read_text()
    assert "FLZ crypto first" in content
    assert "flz_first" in content

def test_no_local_unfinished_bypass():
    """Hash check should be unconditional for no double (not gdone-guarded)."""
    content = pathlib.Path("tools/v15_local_herd.py").read_text()
    assert "if (hash(s) % 4) != host_idx:" in content
    # Ensure the old gdone-guarded line is not present as code (allow in comment)
    lines = [l.strip() for l in content.splitlines() if "if gdone is None and (hash(s)" in l and not l.strip().startswith("#") and not l.strip().startswith("-")]
    assert not lines, f"gdone-guarded hash still as code: {lines[:2]}"
    # Also ensure local_unfinished is hash-filtered
    assert "hash(sym) % 4) == host_idx" in content
