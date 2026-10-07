"""v15_final_naming — ONE place that builds/parses bh/gain matrix filenames.

USER 2026-10-03: the filename gain is the ACTUAL final gain as INTEGER percent,
and the fraction slot carries the trade count instead:
  {SYM_SIDE}_bh{bh:.2f}_gain{gain:int}p_t{trades}_30d_matrix.xlsx
e.g. FANG_LONG_bh1p58_gain6p_t66_30d_matrix.xlsx
BH keeps cents (unchanged). Callers MUST pass fresh-verified actuals — this
module formats, it never measures. Parser accepts NEW + OLD (cents, no trades)
names so readers keep working across the cutover.
"""

import math
import re

_NEW_RE = re.compile(r"^(.+)_bh(m?[0-9]+p[0-9]+)_gain(m?[0-9]+p(?:[0-9]+)?)(_t([0-9]+))?_([0-9]+)d_matrix\.(xlsx|html)$")


def fmt_pct2(v) -> str:
    return f"{float(v):.2f}".replace("-", "m").replace(".", "p")


def fmt_gain_int(v) -> str:
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"refusing non-finite gain in filename: {v!r}")
    sign = "m" if f < 0 else ""
    return f"{sign}{int(abs(f))}p"


def final_matrix_name(symside: str, bh, gain, trades, window_days: int = 30, ext: str = "xlsx") -> str:
    t = int(trades)
    if t < 0:
        raise ValueError(f"refusing negative trades in filename: {trades!r}")
    return f"{symside}_bh{fmt_pct2(bh)}_gain{fmt_gain_int(gain)}_t{t}_{int(window_days)}d_matrix.{ext}"


def chart_name_for(matrix_name: str) -> str:
    return matrix_name.replace(".xlsx", ".html")


def _decode(num: str) -> float:
    neg = num.startswith("m")
    v = float(num[1:].replace("p", ".") if neg else num.replace("p", "."))
    return -v if neg else v


def parse_final_matrix_name(name: str):
    m = _NEW_RE.match(str(name).strip())
    if not m:
        return None
    symside, bh_s, gain_s, _, trades_s, window_s, ext = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6), m.group(7)
    try:
        bh, gain = _decode(bh_s), _decode(gain_s)
    except Exception:
        return None
    return {"symside": symside, "bh": bh, "gain": gain, "trades": int(trades_s) if trades_s is not None else None, "window_days": int(window_s), "ext": ext, "fmt": "new" if trades_s is not None else "old"}
