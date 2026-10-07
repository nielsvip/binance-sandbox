"""v15_obligate — obligate-calc guards for the s5 experiment pilot variant.

S5-ONLY (USER 2026-10-03): wired into s5_only/v15_pilot.py, deployed to s5
(10.0.0.5) only. Mac v15_pilot.py is untouched. Pure stdlib so tests/ can
import it without the engine.

Rules enforced:
1. Error-zeros refused: an eval result carrying an engine/future error reason
   is NEVER a number, even if gain_pct/trades look populated (closes the
   tools/opt/v12_pilot.py gain-0.0-on-error hole at the pilot level).
2. Zero provenance: a numeric 0.0 delta is returned only for a clean eval
   (no future error, gain present, trades > 0). Anything else -> None +
   reason, which the pilot renders as RED/blank, never 0.0.
3. Code-stamp resume gate: stamps below let the pilot refuse to trust done
   rows computed under different code (engine cuts silently stale old 0s).
4. far_value: second-opinion probe values for nonbinding-zero rows.
"""

import hashlib
import os
from pathlib import Path

EPS = 1e-9

_ERROR_TOKENS = ("error", "err ", "err:", "except", "traceback", "timeout", "timed out", "prepare failed", "stall", "killed", "oom", "cancelled", "no result")


def is_error_reason(reason) -> bool:
    r = str(reason or "").strip()
    if not r:
        return False
    rl = r.lower()
    if rl.startswith("err") or rl.startswith("timeout"):
        return True
    return any(t in rl for t in _ERROR_TOKENS)


def _bump(stats, key):
    if stats is not None:
        stats[key] = int(stats.get(key, 0)) + 1


def delta_vs_obligate(res, err, cum_before: float, stats=None):
    """(delta, promotable, reason). Drop-in strict twin of the pilot _delta_vs."""
    reason_raw = str((res or {}).get("invalid_reason") or "")
    if err or is_error_reason(reason_raw):
        _bump(stats, "error_refused")
        return None, False, str(err or reason_raw or "no result")[:40]
    if not res or res.get("gain_pct") is None:
        _bump(stats, "no_result")
        return None, False, str(reason_raw or "no result")[:40]
    if int(res.get("trades") or 0) == 0:
        _bump(stats, "zero_trades")
        return None, False, "ZERO_TRADES"
    d = float(res.get("gain_pct")) - float(cum_before)
    d = 0.0 if abs(d) < EPS else d
    if d == 0.0:
        _bump(stats, "proven_zero")
    if not res.get("valid"):
        _bump(stats, "invalid_kept")
        return d, False, str(reason_raw or "invalid")[:40]
    _bump(stats, "ok")
    return d, True, ""


_TF_CYCLE = ("15m", "1h", "4h", "D", "W")


def far_value(field, cand_parsed, default):
    """A second-opinion probe value for the same switch, or None if none exists."""
    c = cand_parsed
    if isinstance(c, bool):
        return (not c)
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return c * 10 + 1 if isinstance(c, int) else c * 10.0 + 0.5
    if isinstance(c, str):
        s = c.strip()
        if s.lower() in ("true", "false"):
            return s.lower() != "true"
        if s.upper() in ("OFF", "ON"):
            return "ON" if s.upper() == "OFF" else "OFF"
        for tf in _TF_CYCLE:
            if tf != s:
                return tf
        return s + "_PROBE"
    if c is None and isinstance(default, bool):
        return True
    return None


def _md5_file(path) -> str:
    try:
        return hashlib.md5(Path(path).read_bytes()).hexdigest()
    except Exception:
        return f"missing:{Path(path).name}"


def code_stamp(root, pilot_file, template_path, defaults_round: str) -> dict:
    root = Path(root)
    return {"pilot_md5": _md5_file(pilot_file), "engine_md5": _md5_file(root / "v12_quick_engine.py"), "eval_md5": _md5_file(root / "tools" / "opt" / "v12_pilot.py"), "template_md5": _md5_file(template_path) if template_path else "no-template", "defaults_round": str(defaults_round or "")}


def stamp_mismatch(old, new) -> list:
    old, new = (old or {}), (new or {})
    return sorted(k for k in new if old.get(k) != new.get(k))


def obligate_env_active() -> bool:
    return os.environ.get("V15_OBLIGATE_CALC", "1") == "1"
