"""test_no_max_switch — max_switches must be deleted everywhere.

Every SWITCH and every yellow/orange delta must be calculated. The
--max-switches limiter truncated sheets to 5 rows and must never return.
"""
import pathlib
import subprocess
import py_compile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Files that previously contained the limiter and must now be clean
CORE_FILES = [
    ROOT / "v15_pilot.py",
    ROOT / "tools" / "v15_pilot_sheet_runner.py",
    ROOT / "tools" / "simple_switch_filter_calculator.py",
    ROOT / "tools" / "simple_switch_filter_full_v2.py",
]

OPT_GLOB = list((ROOT / "tools" / "opt").glob("*.py"))


def test_core_files_contain_no_max_switch():
    for p in CORE_FILES:
        assert p.exists(), f"missing {p}"
        txt = p.read_text(errors="ignore")
        low = txt.lower()
        assert "max_switch" not in low, f"{p.name} still contains max_switch"
        assert "max-switch" not in low, f"{p.name} still contains max-switch"


def test_opt_files_contain_no_max_switch():
    offenders = []
    for p in OPT_GLOB:
        if "worktrees" in str(p) or "backups" in str(p):
            continue
        txt = p.read_text(errors="ignore")
        if "max_switch" in txt.lower() or "max-switch" in txt.lower():
            offenders.append(p.name)
    assert not offenders, f"opt files still contain max_switch limiter: {offenders}"


def test_no_max_switch_anywhere_in_repo():
    out = subprocess.run(
        ["grep", "-rn", "max.switch", str(ROOT), "--include=*.py"],
        capture_output=True, text=True,
    )
    bad = [
        l for l in out.stdout.splitlines()
        if "worktrees" not in l and "/backups/" not in l and "__pycache__" not in l
        and "/tests/" not in l and "quarantine" not in l and "immutable" not in l and "/old/" not in l
    ]
    assert not bad, f"grep still finds max_switch limit: {bad[:5]}"


def test_v15_calculates_every_row():
    """v15 must not slice rows/trials and must not have WIP max_switches guard."""
    src = (ROOT / "v15_pilot.py").read_text()
    assert "args.max_switches" not in src, "v15 still reads args.max_switches"
    assert "rows[:args" not in src, "v15 still slices rows"
    assert "trials[:args" not in src, "v15 still slices trials"
    # must still calculate deltas for every row
    assert "delta_best" in src
    assert "variant_best" in src
    assert "cumulative_gain" in src


def test_simple_switch_no_limit():
    src = (ROOT / "tools" / "simple_switch_filter_calculator.py").read_text()
    assert "args.max_switches" not in src
    assert "switches[:args" not in src


def test_files_compile():
    for p in CORE_FILES:
        py_compile.compile(str(p), doraise=True)
    for p in OPT_GLOB:
        if "worktrees" in str(p):
            continue
        py_compile.compile(str(p), doraise=True)
