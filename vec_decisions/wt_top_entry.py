"""WT-top + divergence entry predicate (USER 2026-10-08: aggressive 15m/1h/4h entries).

A TOP is a completed-bar WT1/WT2 cross (bear crossunder = SHORT top, bull
crossover = LONG low) confirmed by structure: the top bar prints a lower high
(SHORT) / higher low (LONG) than the prior completed bar. TOPS_AND_LOWS adds
the matching second print (lower low / higher high). Optional layers: WT
divergence (regular/hidden, same trigger TF) and HTF cross confirmation.
Stateless by design (current + previous completed bar only) so live snapshots
and NPZ rows evaluate identically; see test_wt_top_entry.py parity test.
"""
TF_SUFFIX = {"15m": "15m", "1h": "1h", "4h": "4h"}
STRUCT_MODES = ("TOPS_ONLY", "TOPS_AND_LOWS")
DIV_MODES = ("OFF", "REG", "HIDDEN", "REG_OR_HIDDEN")
HTF_TFS = ("4h", "D")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _flag(v):
    if isinstance(v, str):
        return v.strip().upper() in ("1", "TRUE", "YES", "Y", "BULL", "BEAR")
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def _safe_get(npz, key, n, default=0.0):
    try:
        v = npz[key]
    except (KeyError, TypeError):
        return [default] * n
    try:
        out = list(v)
    except TypeError:
        return [default] * n
    if len(out) < n:
        out = out + [default] * (n - len(out))
    return out[:n]


def resolve_wt_top_spec(cfg):
    enabled = bool(getattr(cfg, "WT_TOP_ENTRY_ENABLED", False))
    tf = str(getattr(cfg, "WT_TOP_ENTRY_TF", "OFF") or "OFF").strip().lower()
    tf = {"15m": "15m", "1h": "1h", "4h": "4h"}.get(tf, "OFF")
    sm = str(getattr(cfg, "WT_TOP_ENTRY_MODE", "TOPS_ONLY") or "").strip().upper()
    sm = sm if sm in STRUCT_MODES else "TOPS_ONLY"
    dm = str(getattr(cfg, "WT_TOP_ENTRY_DIV_MODE", "OFF") or "").strip().upper()
    dm = dm if dm in DIV_MODES else "OFF"
    raw = str(getattr(cfg, "WT_TOP_ENTRY_HTF_CONFIRM_TF", "OFF") or "OFF").strip()
    up = raw.upper().replace(" ", "")
    if up == "BOTH":
        htf = ["4h", "D"]
    elif up in ("OFF", ""):
        htf = []
    else:
        htf = [t for t in (p.upper().replace("4H", "4h") for p in raw.split(",")) if t in HTF_TFS]
    return {"enabled": enabled, "tf": tf, "struct_mode": sm, "div_mode": dm, "htf": htf}


def check_wt_top_entry(spec, ind, is_long):
    if not spec.get("enabled") or spec.get("tf") not in TF_SUFFIX:
        return (False, "OFF")
    if spec.get("struct_mode") not in STRUCT_MODES or spec.get("div_mode") not in DIV_MODES:
        return (False, "bad-mode")
    sfx = TF_SUFFIX[spec["tf"]]
    want = "bull" if is_long else "bear"
    if not _flag(ind.get(f"wt_cross_{want}_{sfx}", 0)):
        return (False, "no-cross")
    hi, hip = _num(ind.get(f"high_{sfx}")), _num(ind.get(f"high_{sfx}_prev"))
    lo, lop = _num(ind.get(f"low_{sfx}")), _num(ind.get(f"low_{sfx}_prev"))
    if is_long:
        if lo is None or lop is None or not lo > lop:
            return (False, "no-higher-low")
        if spec.get("struct_mode") == "TOPS_AND_LOWS" and (hi is None or hip is None or not hi > hip):
            return (False, "no-higher-high")
    else:
        if hi is None or hip is None or not hi < hip:
            return (False, "no-lower-top")
        if spec.get("struct_mode") == "TOPS_AND_LOWS" and (lo is None or lop is None or not lo < lop):
            return (False, "no-lower-low")
    dm = spec.get("div_mode", "OFF")
    if dm != "OFF":
        reg = _flag(ind.get(f"div_reg_{want}_wt_{sfx}", 0))
        hid = _flag(ind.get(f"div_hid_{want}_wt_{sfx}", 0))
        ok = (reg or hid) if dm == "REG_OR_HIDDEN" else (reg if dm == "REG" else hid)
        if not ok:
            return (False, f"no-div-{dm}")
    for h in spec.get("htf", []):
        if h in HTF_TFS and _flag(ind.get(f"wt_cross_{want}_{h}", 0)):
            break
    else:
        if spec.get("htf"):
            return (False, "no-htf")
    return (True, "WT_TOP")


def build_wt_top_mask(npz, n, is_long, spec, safe):
    out = [False] * n
    if not spec.get("enabled") or spec.get("tf") not in TF_SUFFIX:
        return out
    if spec.get("struct_mode") not in STRUCT_MODES or spec.get("div_mode") not in DIV_MODES:
        return out
    sfx = TF_SUFFIX[spec["tf"]]
    want = "bull" if is_long else "bear"
    cross = safe(npz, f"wt_cross_{want}_{sfx}", n, 0)
    hi = safe(npz, f"high_{sfx}", n, float("nan"))
    lo = safe(npz, f"low_{sfx}", n, float("nan"))
    dm = spec.get("div_mode", "OFF")
    reg = safe(npz, f"div_reg_{want}_wt_{sfx}", n, 0) if dm in ("REG", "REG_OR_HIDDEN") else None
    hid = safe(npz, f"div_hid_{want}_wt_{sfx}", n, 0) if dm in ("HIDDEN", "REG_OR_HIDDEN") else None
    htf_cols = {h: safe(npz, f"wt_cross_{want}_{h}", n, 0) for h in (spec.get("htf") or []) if h in HTF_TFS}
    both = spec.get("struct_mode") == "TOPS_AND_LOWS"
    for i in range(1, n):
        try:
            if float(cross[i]) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        a, ap, b, bp = _num(hi[i]), _num(hi[i - 1]), _num(lo[i]), _num(lo[i - 1])
        if is_long:
            if b is None or bp is None or not b > bp:
                continue
            if both and (a is None or ap is None or not a > ap):
                continue
        else:
            if a is None or ap is None or not a < ap:
                continue
            if both and (b is None or bp is None or not b < bp):
                continue
        if dm != "OFF":
            rok = hok = False
            try:
                rok = reg is not None and float(reg[i]) > 0
            except (TypeError, ValueError):
                pass
            try:
                hok = hid is not None and float(hid[i]) > 0
            except (TypeError, ValueError):
                pass
            ok = (rok or hok) if dm == "REG_OR_HIDDEN" else (rok if dm == "REG" else hok)
            if not ok:
                continue
        if htf_cols:
            hit = False
            for col in htf_cols.values():
                try:
                    if float(col[i]) > 0:
                        hit = True
                        break
                except (TypeError, ValueError):
                    continue
            if not hit:
                continue
        out[i] = True
    return out
