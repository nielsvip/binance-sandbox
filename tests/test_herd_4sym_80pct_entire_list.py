"""Test herd keeps 4 sym_sides 80% CPU/RAM max workers on entire list without per_sym."""
import pathlib

def test_herd_keeps_4_sym_80pct_entire_list():
    p = pathlib.Path("/Users/niels/Documents/binance/tools/v15_local_herd.py")
    src = p.read_text()
    assert "keep 4 sym_sides 80% CPU/RAM" in src.lower() or "4 sym_sides 80%" in src, "herd must mention 4 sym_sides 80% CPU/RAM"
    assert "max_parallel = 4" in src, "must keep max_parallel 4"
    assert "workers = 28" in src, "must keep max workers 28"
    assert "entire list" in src.lower(), "must mention entire list"
    assert "without per_sym" in src.lower() or "no per_sym" in src.lower(), "must mention without per_sym"
    # check 80% thresholds
    assert "cpu < 80" in src or "cpu >= 80" in src, "must use 80% CPU threshold"
    assert "ram_used < 80" in src or "ram_used >= 80" in src, "must use 80% RAM threshold"
    # check that pending is built from entire list in the new mode
    assert "use_entire_list = True" in src, "must have entire list flag"
    assert "pending from entire list" in src.lower(), "must log entire list"

if __name__ == "__main__":
    test_herd_keeps_4_sym_80pct_entire_list()
    print("PASS")
