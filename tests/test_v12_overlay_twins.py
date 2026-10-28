"""test_v12_overlay_twins — no value-identical tradier-overlay twins; MAX_AUGMENTS no-cap mandate.

Source-level (no engine import): parses QuickConfig field defaults vs
apply_tradier_defaults() assigns in v12_quick_engine.py, and the
the deleted MAX_AUGMENTS_PER_POSITION global (removed 2026-10-15; see tests/test_maxaug_deleted.py).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V12 = ROOT / "v12_quick_engine.py"
CFG = ROOT / "config.py"


def _overlay_region(lines):
    a0 = next(i for i, ln in enumerate(lines) if re.match(r"\s*def apply_tradier_defaults\(self\):", ln))
    a1 = len(lines)
    for i in range(a0 + 1, len(lines)):
        if lines[i].strip() == "":
            continue
        if not lines[i].startswith("        "):
            a1 = i
            break
    return (a0, a1)


def _join_wrapped(lines):
    """2026-10-28: black wraps `X: bool = (` / `self.X = (` onto the next line, so the old
    line-at-a-time regex captured the literal "(" as the value on BOTH sides and reported them
    value-identical. That produced 4 false positives (EMA_9_21_FILTER_ENABLED, EMA_DIST_SIZING_ENABLED,
    GAP_RISK_EXIT_ENABLED, HTF_GATE_SIGNALS_SMA200D) whose real values are correct and DIFFER.
    Collapse a trailing "(" into the following lines so the comparison sees the real value."""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.rstrip().endswith("("):
            depth = ln.count("(") - ln.count(")")
            buf = [ln.rstrip()]
            j = i + 1
            while j < len(lines) and depth > 0:
                nxt = re.sub(r"\s*#.*$", "", lines[j]).strip()
                buf.append(nxt)
                depth += nxt.count("(") - nxt.count(")")
                j += 1
            out.append(" ".join(buf))
            # keep line numbering stable for the region finder
            out.extend("" for _ in range(j - i - 1))
            i = j
            continue
        out.append(ln)
        i += 1
    return out


def _value(s):
    s = re.sub(r"\s*#.*$", "", s).strip()
    s = s.strip("()").strip()
    return s.strip("'\"")


def _base_and_overlay():
    raw = V12.read_text().splitlines()
    a0, a1 = _overlay_region(raw)
    lines = _join_wrapped(raw)
    base = {}
    for ln in lines[:a0] + lines[a1:]:
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*:[^=]+=\s*(.+?)$", ln.strip())
        if m:
            base[m.group(1)] = _value(m.group(2))
    ov = {}
    for ln in lines[a0:a1]:
        m = re.match(r"^\s*self\.([A-Z][A-Z0-9_]*)\s*=\s*(.+?)$", ln.strip())
        if m:
            ov[m.group(1)] = _value(m.group(2))
    return (base, ov)


def test_no_value_identical_overlay_twins():
    base, ov = _base_and_overlay()
    same = sorted(k for k, v in ov.items() if k in base and base[k] == v)
    assert same == [], f"value-identical overlay twins (delete the overlay line): {same}"


