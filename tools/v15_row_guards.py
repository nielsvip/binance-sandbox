"""v15_row_guards — RULE#1..#4 enforcement helpers (USER 2026-10-03).

RULE#1: every row evaluates ALL its yellow filter cells over only the current
  row's switch (switch=cand + that one filter vs cumulative_before).
RULE#2: a yellow cell holds ONLY a genuinely calculated delta — never a 0.0
  placeholder for invalid/timeout/missing. Uncalculated = blank + red + reason.
RULE#3: a row is not complete until every yellow cell is calculated. A verdict
  of settled-invalid (trade floor / TIM / DD vomit gates) counts as calculated;
  timeouts, eval errors and missing results do not — the row stays pending.
RULE#4: done rows are stamped with the NPZ identity they were measured on. The
  ONLY legitimate 0.00 delta is re-running the identical calc over the identical
  NPZ; a changed NPZ invalidates frozen rows (refill, never reuse), because a
  0.00 delta on a changed NPZ is impossible.

Pure logic + thin openpyxl writers. No engine imports — safe to unit-test.
"""
from __future__ import annotations

import math


def is_real_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def yellows_complete(relevant_hdrs, evaluated_hdrs) -> list:
    """RULE#3 gate: every relevant yellow must have a genuinely evaluated delta.

    evaluated_hdrs = headers whose engine eval returned (valid or settled-invalid
    verdict — see invalid_settled). Returns the missing (uncalculated) headers.
    """
    try:
        ev = set(evaluated_hdrs or [])
    except Exception:
        ev = set()
    return [h for h in (relevant_hdrs or []) if h not in ev]


_UNSETTLED_MARKERS = ("timeout", "prepare failed", "no npz", "eval error", "batch error", "stall", "exception", "traceback", "woke to next cell")


def invalid_settled(reason) -> bool:
    """RULE#3: settled-invalid (deterministic gate verdict) counts as calculated.

    Trade-floor / TIM / DD vomit verdicts are real engine verdicts — retrying is
    pointless. Timeouts, prepare failures and eval errors are NOT verdicts.
    """
    if reason is None:
        return False
    r = str(reason).lower()
    if not r.strip():
        return False
    return not any(m in r for m in _UNSETTLED_MARKERS)


def mark_unevaluated(ws, row, col, reason="not-calculated") -> bool:
    """RULE#2: an uncalculated yellow is blank + red, NEVER 0.0. Reason to logs."""
    if ws is None or not col:
        return False
    try:
        from openpyxl.styles import Font as _Font
        from openpyxl.styles import PatternFill as _PF
        c = ws.cell(row=row, column=col)
        c.value = None
        c.fill = _PF(start_color="FF0000", end_color="FF0000", fill_type="solid")
        c.font = _Font(name="Arial", size=10, bold=True, color="FFFFFF")
        return True
    except Exception:
        return False


_MD5_CACHE = {}


def _cached_npz_md5(path, mtime_ns, size):
    """md5 of the NPZ file, cached per (path, mtime_ns, size). Fail-open None.

    2026-10-04 comparability stamp: S1-vs-S5 NPZ bytes diverge (1165 vs 945 keys)
    with gain divergence at equal trade counts — every result must carry the exact
    bytes it measured on. Additive only: npz_changed() compares a fixed 6-key list
    and ignores md5, so no board ever invalidates from this field. short_npz_id()
    format unchanged (row strings stable).
    """
    key = (path, mtime_ns, size)
    if key in _MD5_CACHE:
        return _MD5_CACHE[key]
    try:
        import hashlib as _hl
        h = _hl.md5()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        _MD5_CACHE[key] = h.hexdigest()
    except Exception:
        _MD5_CACHE[key] = None
    return _MD5_CACHE[key]


def npz_identity_for_symside(symside, indicators_dir=None, root=None):
    """RULE#4: identity of the NPZ a row would be measured on.

    {path, mtime_ns, size, n, ts_first, ts_last}. Timestamps read via mmap (cheap).
    Without numpy, falls back to stat-only identity (still detects any change).
    """
    import pathlib as _pl
    base = str(symside or "")
    for tail in ("_LONG", "_SHORT"):
        if base.endswith(tail):
            base = base[: -len(tail)]
            break
    base = base.strip().upper()
    cands = []
    if indicators_dir is not None:
        cands.append(_pl.Path(indicators_dir))
    if root is not None:
        cands.append(_pl.Path(root) / "backtest_v8" / "indicators")
    cands.append(_pl.Path(__file__).resolve().parents[1] / "backtest_v8" / "indicators")
    try:
        cands.append(_pl.Path.home() / "binance-sandbox" / "backtest_v8" / "indicators")
    except Exception:
        pass
    names = [f"{base}.npz"]
    if base.endswith("USDT") and len(base) > 4:
        names.append(f"{base[:-4]}.npz")
    for d in cands:
        for n in names:
            try:
                p = d / n
                if p.is_file():
                    st = p.stat()
                    ident = {"path": str(p), "mtime_ns": int(st.st_mtime_ns), "size": int(st.st_size), "n": None, "ts_first": None, "ts_last": None, "md5": _cached_npz_md5(str(p), int(st.st_mtime_ns), int(st.st_size))}
                    try:
                        import numpy as _np
                        with _np.load(str(p), mmap_mode="r", allow_pickle=True) as z:
                            keys = list(z.files)
                            tk = "timestamps" if "timestamps" in keys else next((k for k in ("timestamp_3m", "timestamp_5m") if k in keys), None)
                            if tk is not None:
                                ts = _np.asarray(z[tk], dtype="float64")
                                fin = ts[_np.isfinite(ts) & (ts > 0)]
                                ident["n"] = int(ts.size)
                                if fin.size:
                                    ident["ts_first"] = float(fin[0])
                                    ident["ts_last"] = float(fin[-1])
                    except Exception:
                        pass
                    return ident
            except Exception:
                continue
    return None


def npz_changed(old, new) -> bool:
    """RULE#4: True only when both identities exist and any field differs."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    for k in ("path", "mtime_ns", "size", "n", "ts_first", "ts_last"):
        if old.get(k) != new.get(k):
            return True
    return False


def incomplete_rows(done) -> list:
    """RULE#3: done keys whose row never completed (uncalculated yellows remain).

    Only rows explicitly stamped complete=False count — legacy rows without the
    flag are trusted (backward compatible across the cutover).
    """
    out = []
    try:
        for k, v in (done or {}).items():
            if isinstance(v, dict) and v.get("complete") is False:
                out.append(k)
    except Exception:
        pass
    return out


def short_npz_id(npz_id) -> str:
    try:
        if not isinstance(npz_id, dict):
            return "npz?"
        tl = npz_id.get("ts_last")
        n = npz_id.get("n")
        return f"ts{tl}_n{n}" if tl is not None else f"mt{npz_id.get('mtime_ns')}_sz{npz_id.get('size')}"
    except Exception:
        return "npz?"


def zero_audit_line(sheet, r, switch, filt, hdr, vg, cum, trades, npz_short) -> str:
    return f"[ZERO-DELTA-AUDIT] {sheet}!{r} {switch}" + (f"+{filt}" if filt else "") + f" hdr={hdr} vg={vg} cum={cum} trades={trades} {npz_short} — exact 0.00 from a real eval is near-impossible; verify this cell"


# ── HOLLOW-BOARD SCAN (USER 2026-10-03 hollow-fix: a row must never advance
# without its per-switch yellow filters calculated) ──
# Structural skips are settled verdicts (the row/cell cannot be honestly
# calculated — re-running re-skips identically). Policy skips (sampling /
# tab-level era) are NOT verdicts — the row stays pending and is refilled.
# ZERO_FORMULA (USER 2026-10-06): evidence-condemned never-positive rows/cells,
# skipped + booked for formula fix. Settled (never refilled) but NOT obsolete.
STRUCTURAL_SKIP_PREFIXES = ("NOT_WIRED_VEC", "TYPE_MISMATCH", "NOT_IN_CONFIG", "INVENTED_ALT", "NO_CANDIDATE", "DEAD_VEC", "LIVE_ONLY", "NO_LIVE_PATH", "ZERO_FORMULA")
POLICY_FIX_ID = "hollowfix-20261003"


def is_structural_skip_reason(reason) -> bool:
    r = str(reason or "").strip()
    return any(r.startswith(p) for p in STRUCTURAL_SKIP_PREFIXES)


def is_policy_skip_reason(reason) -> bool:
    # Structural skips never use the SKIPPED prefix (NOT_WIRED/TYPE_MISMATCH/...).
    return str(reason or "").strip().startswith("SKIPPED")


def yellow_want_tokens(switch, filt_base, min_shared: int = 2) -> bool:
    """Pure twin of the pilot's _want token rule (no ever-yellow file: it is empty by design)."""
    try:
        sw_tok = {t for t in str(switch or "").upper().split("_") if t}
        f_tok = {t for t in str(filt_base or "").upper().split("_") if t}
        return len(sw_tok & f_tok) >= int(min_shared)
    except Exception:
        return False


def _parse_done_key(key: str):
    """'TAB!row:SWITCH=cand' -> (tab, switch). None when unparseable."""
    try:
        tab, rest = str(key).split("!", 1)
        rkey = rest.split(":", 1)[1]
        return tab.strip(), rkey.split("=", 1)[0].strip()
    except Exception:
        return None


def row_needs_recalc(rec, key=None, cat_side=None, tablevel_spec=None, assume_tablevel_on: bool = True):
    """Why a done-record must be recalculated, or None when it stands.

    Catches: corrupt records, complete=False rows, policy skips (SKIPPED_*),
    cell-sampled rows, tab-level-excluded rows (new stamp), unsettled-None
    rows (delta None + empty reason: pre-fix identity rows never evaluated
    and crashed/timeout rows — heal to 0.0-or-values on rebuild), and pre-fix
    evaluated rows whose switch token-overlaps a tab-level filter base of its
    tab (those columns were excluded without any record — the 3-day hollow
    run). Structural skips and stamped-clean rows stand.
    """
    if not isinstance(rec, dict):
        return "corrupt-record"
    if rec.get("complete") is False:
        return "incomplete-pending"
    reason = str(rec.get("reason") or "")
    if is_policy_skip_reason(reason):
        return f"policy-skip:{reason[:40]}"
    if rec.get("sampled_out_filters"):
        try:
            return f"sampled-cells:{len(rec.get('sampled_out_filters') or [])}"
        except Exception:
            return "sampled-cells"
    if rec.get("tab_level_excluded"):
        try:
            return f"tablevel-cells:{len(rec.get('tab_level_excluded') or [])}"
        except Exception:
            return "tablevel-cells"
    # USER 2026-10-03 unsettled-None healer: s1 census (191,452 nondelta rows, 34 reason prefixes) proves
    # the ONLY unsettled class is delta-None + empty reason (90,315 pre-fix identity/crash rows — never
    # evaluated). All 33 other prefixes are settled (ZERO_TRADES / v12-prepared / override-incompatible),
    # structural, or policy — they stand or are caught above. Rebuild heals these to 0.0-or-values.
    if rec.get("delta") is None and not reason:
        return "unsettled-none:pre-fix-identity-or-crash"
    if is_structural_skip_reason(reason):
        return None
    if isinstance(rec.get("policy"), dict):
        return None
    if not assume_tablevel_on:
        return None
    parsed = _parse_done_key(key or "")
    if parsed is None:
        return None
    try:
        cats = (tablevel_spec or {}).get("cats") or {}
        bases = (cats.get(cat_side or "") or {}).get(parsed[0]) or {}
    except Exception:
        bases = {}
    if not bases:
        return None
    for base in bases:
        if yellow_want_tokens(parsed[1], base):
            return f"tablevel-era:{parsed[0]}:{parsed[1]}"
    return None


def scan_board_for_hollow(done, cat_side=None, tablevel_spec=None, assume_tablevel_on: bool = True) -> dict:
    """All done-keys that must be recalculated. Pure — no workbook needed.

    Returns {"drop": [keys in done order], "reasons": {key: why}, "tally": {why-class: n}, "total": n}.
    """
    drop, reasons, tally = [], {}, {}
    try:
        items = list((done or {}).items())
    except Exception:
        items = []
    for k, v in items:
        why = row_needs_recalc(v, key=k, cat_side=cat_side, tablevel_spec=tablevel_spec, assume_tablevel_on=assume_tablevel_on)
        if why is not None:
            drop.append(k)
            reasons[k] = why
            tally[why.split(":")[0]] = tally.get(why.split(":")[0], 0) + 1
    return {"drop": drop, "reasons": reasons, "tally": tally, "total": len(items)}


def is_gate_casualty(prog) -> bool:
    """An IMPOSSIBLE tombstone caused by the RULE#3 completeness gate (refillable), as opposed
    to a genuine strategy verdict (DD vomit / gain<BH — stands forever). The gate's marker is
    RULE#3/uncalculated-yellows; strategy quarantines never carry it."""
    try:
        if not isinstance(prog, dict) or prog.get("verdict") != "IMPOSSIBLE":
            return False
        blob = str(prog.get("impossible_reasons")) + str(prog.get("not_compliant") or "")
        return ("RULE#3" in blob) or ("uncalculated yellows" in blob)
    except Exception:
        return False


def tried_settled(res, err) -> bool:
    """A cell/naked eval SETTLES (never blocks publish) when the engine returned a verdict —
    a result object with no transport error — including deterministic rejections, invalids and
    zero-trade verdicts. Retrying a cached deterministic verdict is pointless. Only a MISSING
    verdict (timeout/exception: err set) stays pending for refill/retry."""
    try:
        if err:
            return False
        return res is not None
    except Exception:
        return False


def policy_stamp(possym_on: bool, tablevel_on: bool, unwired_audit_id=None, pilot_id: str = POLICY_FIX_ID) -> dict:
    """Per-row policy stamp: proves which skip policies were armed when measured."""
    try:
        return {"ps": 1 if possym_on else 0, "tl": 1 if tablevel_on else 0, "uw": unwired_audit_id, "pilot": pilot_id}
    except Exception:
        return {"ps": 0, "tl": 0, "uw": None, "pilot": pilot_id}
