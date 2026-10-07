"""Option typing / domain / damage rules for tools/v15_template_restructure_v3.py (AGENT B phase 2, 2026-10-01).
Source of truth for a switch's TYPE = the venue config value (config.Config / config_tradier.TradierConfig, QuickConfig as fallback).
Classes: BOOL | BOOLNUM (numeric 0/1 flag stored as int/float in config) | INT | FLOAT | TF (timeframe or comma list) | ENUM (str) | STRUCT | UNKNOWN.
"""
import collections
import re

TF_TOKENS = {"OFF", "3M", "5M", "15M", "30M", "1H", "2H", "4H", "D", "W", "M1", "1D", "1W"}
TF_CANON = {"OFF": "OFF", "3M": "3m", "5M": "5m", "15M": "15m", "30M": "30m", "1H": "1h", "2H": "2h", "4H": "4h", "D": "D", "1D": "D", "W": "W", "1W": "W"}
FLAG_NAME = re.compile(r"(ENABLED|MANDATORY|REQUIRE|ALLOW|BYPASS|ENFORCE|_ONLY$|^USE_|_USE_|_BLOCK$|DISABLE|SINGLE_GATE|_ON$|_FLIP$)")
BOOL_STR = {"true": True, "false": False}
NUMERIC_TOK = re.compile(r"(MIN|MAX|TFS|VELOCITY|RATIO|THRESHOLD|PCT|SIZE|AGE|SEC|BARS|SCORE|MULT|TIER|MODE|LOOKBACK|WINDOW|COUNT|FRAC|BAND|GAIN|LOSS|HOURS|MINUTES|STOCH|DC_POS|DELTA|K15|K3M|FLIP)")
SIZING = re.compile(r"^(START_POSITION_SIZE|MIN_POSITION_SIZE|MAX_POSITION_SIZE.*|MAX_ORDER_VALUE.*|ORDER_VALUE.*|.*_SIZE_USD|.*NOTIONAL.*|BREAKOUT_SIZE|BALANCE_FLOOR_USD|GOLDEN_RULE_BASE_USD)$")


def other_ref_is_bool(name, other_vals):
    return isinstance(other_vals.get(name), bool)


def classify(name, ref, other_ref=None):
    if ref is None:
        return "UNKNOWN"
    if isinstance(ref, (list, dict, tuple, set)):
        return "STRUCT"
    if isinstance(ref, bool):
        return "BOOL"
    if isinstance(ref, (int, float)):
        if ref in (0, 1) and (isinstance(other_ref, bool) or (FLAG_NAME.search(name) and not NUMERIC_TOK.search(name))):
            return "BOOLNUM"
        return "INT" if isinstance(ref, int) else "FLOAT"
    if isinstance(ref, str):
        up = ref.strip().upper()
        toks = [t for t in up.split(",") if t]
        if toks and all(t in TF_TOKENS for t in toks):
            return "TF"
        if up in ("TRUE", "FALSE"):
            return "BOOL"
        return "ENUM"
    return "UNKNOWN"


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except Exception:
        return None


def normalize(cls, ref, opt):
    """-> (ok, typed value, reason). The typed value is what is written to column B."""
    s = "" if opt is None else str(opt).strip()
    if cls == "BOOL":
        if isinstance(opt, bool):
            return True, opt, ""
        if s.lower() in BOOL_STR:
            return True, BOOL_STR[s.lower()], "RETYPED" if not isinstance(opt, bool) else ""
        n = _num(opt)
        if n in (0.0, 1.0):
            return True, bool(n), "RETYPED_NUMERIC_BOOL"
        return False, opt, f"NOT_BOOL: '{s}'"
    if cls == "BOOLNUM":
        if isinstance(opt, bool):
            n = 1.0 if opt else 0.0
        elif s.lower() in BOOL_STR:
            n = 1.0 if BOOL_STR[s.lower()] else 0.0
        else:
            n = _num(opt)
        if n in (0.0, 1.0):
            return True, (int(n) if isinstance(ref, int) else float(n)), "" if isinstance(opt, type(ref)) and not isinstance(opt, bool) else "RETYPED"
        return False, opt, f"NOT_NUMERIC_BOOL: '{s}' (field is a 0/1 flag)"
    if cls in ("INT", "FLOAT"):
        n = _num(opt)
        if n is None:
            return False, opt, f"NOT_NUMERIC: '{s}'"
        if cls == "INT":
            if abs(n - round(n)) > 1e-9:
                return False, opt, f"NON_INTEGRAL: {s} for int field"
            v = int(round(n))
        else:
            v = float(n)
        same = isinstance(opt, type(v)) and not isinstance(opt, bool)
        return True, v, "" if same else "RETYPED"
    if cls == "TF":
        if isinstance(opt, bool) or _num(opt) is not None and s not in ("", "0"):
            return False, opt, f"NOT_TF: '{s}'"
        toks = [t.strip() for t in s.split(",") if t.strip()]
        if not toks or any(t.upper() not in TF_CANON and t.upper() not in TF_TOKENS for t in toks):
            return False, opt, f"NOT_TF: '{s}'"
        v = ",".join(TF_CANON.get(t.upper(), t) for t in toks)
        return True, v, "" if v == s else "RETYPED"
    if cls == "ENUM":
        if isinstance(opt, bool) or _num(opt) is not None:
            return False, opt, f"NOT_ENUM_STR: '{s}'"
        if s.lower().endswith("_alt"):
            return False, opt, f"INVENTED_ALT: '{s}'"
        return True, s, "" if isinstance(opt, str) and s == opt else "RETYPED"
    return True, opt, ""


def key_of(v):
    """canonical dedupe key of a typed option"""
    if isinstance(v, bool):
        return "b:" + str(v)
    if isinstance(v, (int, float)):
        return "n:" + repr(float(v))
    return "s:" + str(v)


def foreign_numeric(d, o):
    """True when option o cannot belong to a parameter whose default is d (sign flip or > 20x / < 1/20x)."""
    if d is None or o is None or d == 0 or o == 0 or abs(d) >= 1e5:   # >=1e5 default = "no cap" sentinel (e.g. MAX_AUGMENTS_PER_POSITION 999999)
        return False
    if d * o < 0:
        return True
    r = abs(o / d)
    return r > 20 or r < 0.05


def ladder(cls, default, have, want=4):
    """default-anchored ladder used ONLY to refill a group that lost its options (flagged GENERATED_LADDER)"""
    out = []
    if default is None or default == 0 or abs(default) >= 1e5 or cls not in ("INT", "FLOAT"):
        return out
    for m in (0.5, 0.75, 1.25, 1.5, 2.0):
        if len(have) + len(out) >= want:
            break
        v = default * m
        v = int(round(v)) if cls == "INT" else float(f"{v:.6g}")
        if cls == "INT" and v == default:
            continue
        if key_of(v) not in {key_of(x) for x in have} | {key_of(x) for x in out}:
            out.append(v)
    return out


def tokens(name):
    return {t for t in re.split(r"[^A-Z0-9]+", str(name).upper()) if t}
