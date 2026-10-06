#!/usr/bin/env python3
"""switch_parity — single registrar + verifier for switch VALUE parity.

Every promoted switch value is registered in ONE call so no surface can ever
disagree about what a sym_side runs (USER 2026-10-06):

  register_workbook_result(sym_side, overrides, evidence)

writes atomically, in this order:

  1. SQLite primary  (data/hourly_reconfig/per_sym_store.db, per_sym_active +
     per_sym_history) via per_sym_store.upsert — this is what live
     ez_manage._psym_get / tradier_manage._cfg read FIRST.
  2. per_sym JSON book (per_sym_active_config.json crypto,
     per_sym_active_config_stocks.json stocks) — live JSON fallback + the file
     the vector engine's per-sym layer reads.
  3. trb/active_config.json overlay (stocks, update-if-exists, same shape as
     tools/golive_final.py) — stocks live _cfg reads it before the books for
     unscoped lookups.
  4. data/parity_promotions.jsonl ledger — one JSON line per registration
     (evidence, engine, template md5, round) for audit.
  5. post-verify re-read: JSON overrides == SQLite overrides == full_config
     merge. Any divergence fails LOUD (returned in the report, never silent).

Gates (fail-closed, never loosened — BACKTEST_BIBLE §51/§58):
  * >= 1 VALID promoted positive this workbook (evidence n_promoted).
  * fresh full-set 30D qualifies: valid + TIM 20-80 + >= 10 trades + DD <= 30
    (via valid) + gain > 0 — mirrors v15_pilot._qualifies_30d.
  * no secret keys (API_KEY/SECRET/TOKEN/PASSWORD/...) — refuse ALL.
  * every override value must type-coerce against its cat_side default
    (bool/int/float/str families; dict/list values refused) — refuse ALL on
    any mismatch, because a partial set is unproven.
  * existing _NEG_BLOCK is preserved unless lift_block=True with 365D
    evidence (values register everywhere immediately; live-trading enablement
    keeps its own 365D/parity gates).

verify_symside(sym_side) re-checks stored-surface agreement any time.
verify_default_surfaces(cat_side) audits the DEFAULT layer per cat_side:
  TEMPLATE bold == cat_side_defaults_4 == venue global (config.py crypto /
  config_tradier.py stocks) == QuickConfig (+apply_tradier_defaults overlay),
  minus the curated exclusion set (BACKTEST_BIBLE §17.3/§31 + the §64
  ablation exemption). Per-sym positives NEVER overwrite globals (different
  syms want different values); globals move only via the cross-sym path.
sync_default_surfaces(cat_side, apply) plans (and, with --confirm-unlocked,
  applies) template-bold -> global edits. The three global files are LOCKED;
  applying requires an explicit user unlock — dry-run needs none.

Env: SWITCH_PARITY_REGISTER=0 disables registration (proof runs must set it).
  SWITCH_PARITY_ROOT redirects all paths (tests). PER_SYM_STORE_DB (see
  per_sym_store.py) redirects SQLite. CAT_SIDE_DEFAULTS_PATH redirects the
  cat_side JSON (see cat_side_defaults.py).
"""

from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
SECRET_RE = re.compile(
    r"API_KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|ACCOUNT_ID|PRIVATE", re.I
)
TIM_MIN, TIM_MAX = 20.0, 80.0
FLOOR_TRADES_30D = 10
TAG_PREFIX = "v15_30D"

# Curated exclusions for DEFAULT-surface sync/verify (BACKTEST_BIBLE §17.3/§31).
# These legitimately differ between template bold / cat_side / venue global /
# QuickConfig and must never be force-synced.
EXCLUDE_EXACT = frozenset(
    {
        "BASE_PATH",
        "BASE_TF",
        "MODE",
        "ATR_PARITY_EQUITY_BASE_USD",
        "START_POSITION_SIZE",
        "MAX_ORDER_VALUE",
        "MAX_AUGMENTS_PER_POSITION",
        "LIVE_ENTRY_ENGINE_ENABLED",
        "LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED",
        "MTF_ARMED_ENTRY_ENABLED",
        "REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED",
        "CAT_SIDE_DEFAULTS_ENABLED",
    }
)
EXCLUDE_SUFFIX = (
    "_PATH",
    "_FILE",
    "_DIR",
    "_CACHE",
    "_URL",
    "_HOST",
    "_PORT",
    "_WEBHOOK",
    "_CHANNEL",
    "_EMAIL",
    "_KEY",
    "_SECRET",
)
EXCLUDE_PREFIX = ("ABLATION_DISABLE_",)
EXCLUDE_SUBSTR = ("_LIVE_MONITOR_", "LIVE_5M_TRADING")

# Builder P0-FOSSIL pins (tools/build_cat_side_defaults_4.py): the builder forces
# cat = live truth for these (side,key) pairs (SHORT-entry killers). Cat-fill must
# never overwrite them — the template bolds are stale, template lane owns them.
FOSSIL_HELD = frozenset(
    {
        ("CRYPTO_SHORT", "MOM3_FILTER_TF"),
        ("STOCKS_SHORT", "MOM3_FILTER_TF"),
        ("CRYPTO_SHORT", "VIGILANCE_GUARD_ENABLED"),
        ("STOCKS_SHORT", "WT_CROSSUNDER_FINAL_ENABLED"),
    }
)


def _ROOT() -> Path:
    return Path(os.environ.get("SWITCH_PARITY_ROOT") or Path(__file__).resolve().parent)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")


def is_crypto_sym(symbol: str) -> bool:
    return str(symbol or "").upper().endswith(CRYPTO_SUFFIXES)


def cat_side_of(sym_side: str) -> str:
    base, _, side = str(sym_side or "").upper().rpartition("_")
    side = "LONG" if side == "LONG" else "SHORT"
    cat = "CRYPTO" if is_crypto_sym(base) else "STOCKS"
    return f"{cat}_{side}"


def _book_path(sym_side: str, root: Path = None) -> Path:
    root = root or _ROOT()
    base = str(sym_side).rsplit("_", 1)[0]
    name = (
        "per_sym_active_config.json"
        if is_crypto_sym(base)
        else "per_sym_active_config_stocks.json"
    )
    return root / "data" / "hourly_reconfig" / name


def _cat_defaults(cat_side: str) -> dict:
    sys.path.insert(0, str(_ROOT()))
    import cat_side_defaults as _csd

    try:
        return dict(_csd.defaults(cat_side) or {})
    except Exception:
        return {}


def _template_md5(cat_side: str, root: Path = None) -> str:
    root = root or _ROOT()
    try:
        p = root / "SPREADSHEETS" / f"TEMPLATE_{cat_side}.xlsx"
        return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else ""
    except Exception:
        return ""


def coerce_like(value, ref):
    """(ok, coerced_or_reason) — value must be consumable as ref's type family."""
    if (
        isinstance(ref, dict)
        or isinstance(ref, list)
        or isinstance(ref, tuple)
        or isinstance(ref, set)
    ):
        return (False, f"dict/list-typed field (grey group, never promoted)")
    if ref is None:
        return (True, value)
    if isinstance(ref, bool):
        if isinstance(value, bool):
            return (True, value)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) in (0.0, 1.0)
        ):
            return (True, bool(value))
        if isinstance(value, str) and value.strip().lower() in (
            "true",
            "false",
            "1",
            "0",
            "yes",
            "no",
        ):
            return (True, value.strip().lower() in ("true", "1", "yes"))
        return (False, f"type {type(value).__name__}={value!r} not bool-coercible")
    if isinstance(ref, int) and not isinstance(ref, bool):
        if isinstance(value, bool):
            return (False, f"bool {value!r} refused for int field")
        if isinstance(value, int):
            return (True, value)
        if isinstance(value, float) and float(value).is_integer():
            return (True, int(value))
        if isinstance(value, str):
            try:
                return (
                    (True, int(float(value.strip())))
                    if float(value.strip()).is_integer()
                    else (False, f"{value!r} not integral")
                )
            except Exception:
                return (False, f"{value!r} not numeric")
        return (False, f"type {type(value).__name__}={value!r} not int-coercible")
    if isinstance(ref, float) and not isinstance(ref, bool):
        if isinstance(value, bool):
            return (False, f"bool {value!r} refused for float field")
        if isinstance(value, (int, float)):
            return (True, float(value))
        if isinstance(value, str):
            try:
                return (True, float(value.strip()))
            except Exception:
                return (False, f"{value!r} not numeric")
        return (False, f"type {type(value).__name__}={value!r} not float-coercible")
    if isinstance(ref, str):
        if isinstance(value, str):
            return (True, value)
        return (
            False,
            f"type {type(value).__name__}={value!r} refused for str field (TF-word/numeric swap)",
        )
    return (True, value)


def qualifies_30d(ev: dict):
    """(ok, reasons) — mirrors v15_pilot._qualifies_30d on evidence numbers."""
    r = []
    ev = ev or {}
    if not ev.get("valid"):
        r.append(f"invalid:{ev.get('invalid_reason') or '?'}")
    try:
        t = int(ev.get("trades") or 0)
    except Exception:
        t = 0
    if t < FLOOR_TRADES_30D:
        r.append(f"trades {t}<{FLOOR_TRADES_30D}")
    try:
        tim = float(ev.get("tim_pct") or 0.0)
    except Exception:
        tim = 0.0
    if not (TIM_MIN <= tim <= TIM_MAX):
        r.append(f"TIM {tim:.1f} outside [{TIM_MIN:.0f},{TIM_MAX:.0f}]")
    g = ev.get("gain_pct")
    try:
        g = float(g) if g is not None else None
    except Exception:
        g = None
    if g is None or g <= 0:
        r.append(f"gain {g}")
    return (len(r) == 0, r)


def _atomic_write_json(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".sp.tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    json.loads(tmp.read_text())
    os.replace(tmp, path)


def register_workbook_result(
    sym_side: str,
    overrides: dict,
    evidence: dict,
    lift_block: bool = False,
    dry_run: bool = False,
    tag: str = None,
    root: Path = None,
) -> dict:
    """Register a workbook-end positive result on every surface. See module doc."""
    root = Path(root) if root else _ROOT()
    sys.path.insert(0, str(root))
    rep = {"sym_side": sym_side, "registered": False, "dry_run": bool(dry_run)}
    if os.environ.get("SWITCH_PARITY_REGISTER", "1") == "0":
        rep["reason"] = "SWITCH_PARITY_REGISTER=0 (proof/audit mode)"
        return rep
    sym_side = str(sym_side or "").strip().upper()
    rep["sym_side"] = sym_side
    parts = sym_side.rsplit("_", 1)
    if len(parts) != 2 or parts[1] not in ("LONG", "SHORT"):
        rep["reason"] = f"bad sym_side {sym_side!r}"
        return rep
    symbol, side = parts
    cat_side = cat_side_of(sym_side)
    rep["cat_side"] = cat_side
    overrides = dict(overrides or {})
    if not overrides:
        rep["reason"] = "empty overrides (nothing proved)"
        return rep
    ev = dict(evidence or {})
    rep["evidence"] = {
        k: ev.get(k)
        for k in (
            "n_promoted",
            "valid",
            "gain_pct",
            "trades",
            "tim_pct",
            "max_dd_pct",
            "pool_sharpe",
            "bh_pct",
            "baseline_gain",
        )
    }
    try:
        n_prom = int(ev.get("n_promoted") or 0)
    except Exception:
        n_prom = 0
    if n_prom < 1:
        rep["reason"] = f"n_promoted={n_prom} (no VALID positive branch this workbook)"
        return rep
    ok_q, q_reasons = qualifies_30d(ev)
    if not ok_q:
        rep["reason"] = f"final set not qualified: {'; '.join(q_reasons)}"
        return rep
    secrets = [k for k in overrides if SECRET_RE.search(k)]
    if secrets:
        rep["reason"] = f"secret keys refused (ALL): {secrets[:6]}"
        return rep
    snap = _cat_defaults(cat_side)
    if not snap:
        rep["reason"] = (
            f"empty cat_side snapshot for {cat_side} (defaults unreadable — refusing)"
        )
        return rep
    coerced, bad = {}, {}
    for k, v in overrides.items():
        if k not in snap:
            bad[k] = "not a cat_side/config key"
            continue
        ok_c, cv = coerce_like(v, snap[k])
        if ok_c:
            coerced[k] = cv
        else:
            bad[k] = cv
    if bad:
        rep["reason"] = (
            f"type-gate refused ALL ({len(bad)} bad): {dict(list(bad.items())[:6])}"
        )
        return rep
    book = _book_path(sym_side, root)
    old_entry, old_tag = {}, ""
    try:
        raw0 = json.loads(book.read_text()) if book.exists() else {}
        old_entry = raw0.get(sym_side, {}) if isinstance(raw0, dict) else {}
        old_tag = str((old_entry or {}).get("winning_tag") or "")
    except Exception:
        pass
    keep_block = "_NEG_BLOCK" in old_tag and not lift_block
    new_tag = tag or f"{TAG_PREFIX}_{_today()}"
    if keep_block and "_NEG_BLOCK" not in new_tag:
        new_tag = f"{new_tag}_NEG_BLOCK"
    rep["tag"] = new_tag
    rep["neg_block_preserved"] = bool(keep_block)
    try:
        gain = float(ev.get("gain_pct"))
        base = float(
            ev.get("baseline_gain") if ev.get("baseline_gain") is not None else 0.0
        )
        bh = float(ev.get("bh_pct") if ev.get("bh_pct") is not None else 0.0)
    except Exception:
        gain, base, bh = 0.0, 0.0, 0.0
    meta = {
        "winning_tag": new_tag,
        "wsharpe": ev.get("pool_sharpe"),
        "pool_sharpe": ev.get("pool_sharpe"),
        "trades": ev.get("trades"),
        "max_dd_pct": ev.get("max_dd_pct"),
        "acc_gain_pct": gain,
        "gain_vs_bh": gain - bh,
        "bh_pct": bh,
        "delta_30d": gain - base,
        "gain_30d": gain,
        "baseline_30d": base,
        "tim_pct": ev.get("tim_pct"),
        "valid": True,
        "campaign_ts": time.time(),
        "source": f"switch_parity workbook-end {sym_side} n_promoted={n_prom}",
        "engine_md5": ev.get("engine_md5") or "",
        "npz_id": ev.get("npz_id") or "",
        "n_promoted": n_prom,
        "promoted_keys": sorted(coerced),
        "lift_block": bool(lift_block),
    }
    full = dict(snap)
    full.update(coerced)
    rep["n_keys"] = len(coerced)
    rep["n_snapshot"] = len(snap)
    rep["n_full"] = len(full)
    if dry_run:
        rep["reason"] = "dry_run (gates passed, nothing written)"
        rep["would_tag"] = new_tag
        return rep
    try:
        bdir = root / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        if old_entry:
            bp = (
                bdir
                / f"before_parity_{sym_side}_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
            )
            bp.write_text(
                json.dumps(
                    {
                        sym_side: old_entry,
                        "_meta": {
                            "backed_up_at": _now_iso(),
                            "reason": f"switch_parity register {new_tag}",
                        },
                    },
                    indent=2,
                    default=str,
                )
            )
            rep["backup"] = bp.name
    except Exception as _be:
        rep["reason"] = f"backup failed (refusing): {_be}"
        return rep
    try:
        import per_sym_store as _pss

        tpl_md5 = ev.get("template_md5") or _template_md5(cat_side, root)
        dround = ev.get("defaults_round") or os.environ.get("V15_DEFAULTS_ROUND", "")
        _pss.upsert(
            sym_side,
            dict(coerced),
            dict(snap),
            dict(full),
            dict(meta),
            template_md5=tpl_md5,
            defaults_round=dround,
            json_path=book,
        )
        rep["sqlite"] = True
        rep["book"] = book.name
    except Exception as _ue:
        rep["reason"] = f"upsert failed: {_ue}"
        return rep
    if not is_crypto_sym(symbol):
        try:
            trb = root / "data" / "hourly_reconfig" / "trb" / "active_config.json"
            if trb.exists():
                trobj = json.loads(trb.read_text())
                if isinstance(trobj, dict) and isinstance(trobj.get(sym_side), dict):
                    e = trobj[sym_side]
                    e["prev_overrides_parity_{}".format(_today())] = e.get("overrides")
                    e.update(
                        overrides=dict(coerced),
                        gain_pct=round(gain, 4),
                        trades=int(ev.get("trades") or 0),
                        source=f"switch_parity {new_tag}",
                        promoted_at=_now_iso(),
                        live=not keep_block,
                    )
                    _atomic_write_json(trb, trobj)
                    rep["trb_overlay"] = "updated"
                else:
                    rep["trb_overlay"] = "absent (book+SQLite carry scoped reads)"
            else:
                rep["trb_overlay"] = "no file (book+SQLite carry scoped reads)"
        except Exception as _te:
            rep["trb_overlay"] = f"warn: {_te}"
    try:
        led = root / "data" / "parity_promotions.jsonl"
        led.parent.mkdir(parents=True, exist_ok=True)
        with open(led, "a") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": _now_iso(),
                        "sym_side": sym_side,
                        "cat_side": cat_side,
                        "tag": new_tag,
                        "n_keys": len(coerced),
                        "keys": sorted(coerced),
                        "evidence": rep["evidence"],
                        "lift_block": bool(lift_block),
                        "defaults_round": ev.get("defaults_round")
                        or os.environ.get("V15_DEFAULTS_ROUND", ""),
                    }
                )
                + "\n"
            )
        rep["ledger"] = led.name
    except Exception as _le:
        rep["ledger"] = f"warn: {_le}"
    try:
        rep["verify"] = verify_symside(sym_side, root=root)
        if not rep["verify"].get("ok"):
            rep["reason"] = f"post-verify FAILED: {rep['verify'].get('mismatches')[:4]}"
            return rep
    except Exception as _ve:
        rep["reason"] = f"post-verify error: {_ve}"
        return rep
    try:
        Path("/tmp/per_sym_live_promoted.flag").write_text(
            f"{sym_side} {_now_iso()} {new_tag}"
        )
    except Exception:
        pass
    rep["registered"] = True
    return rep


def verify_symside(sym_side: str, root: Path = None) -> dict:
    """Stored-surface agreement for one sym_side: book JSON vs SQLite."""
    root = Path(root) if root else _ROOT()
    sys.path.insert(0, str(root))
    rep = {"sym_side": sym_side, "ok": False, "mismatches": []}
    book = _book_path(sym_side, root)
    try:
        raw = json.loads(book.read_text()) if book.exists() else {}
        entry = raw.get(sym_side, {}) if isinstance(raw, dict) else {}
    except Exception as _e:
        rep["mismatches"].append(f"book unreadable: {_e}")
        return rep
    if not isinstance(entry, dict) or not entry.get("overrides"):
        rep["mismatches"].append("book entry missing/empty overrides")
        return rep
    try:
        import per_sym_store as _pss

        sql_ov = _pss.get_overrides(sym_side)
        sql_full = _pss.get_full_config(sym_side)
    except Exception as _e:
        rep["mismatches"].append(f"sqlite unreadable: {_e}")
        return rep
    if not isinstance(sql_ov, dict):
        rep["mismatches"].append("sqlite overrides row missing")
        return rep
    jov = entry["overrides"]
    if set(jov) != set(sql_ov):
        rep["mismatches"].append(
            f"override key sets differ: json_only={sorted(set(jov) - set(sql_ov))[:8]} sql_only={sorted(set(sql_ov) - set(jov))[:8]}"
        )
    else:
        for k in jov:
            if json.dumps(jov[k], sort_keys=True, default=str) != json.dumps(
                sql_ov[k], sort_keys=True, default=str
            ):
                rep["mismatches"].append(
                    f"value differs {k}: json={jov[k]!r} sql={sql_ov[k]!r}"
                )
                if len(rep["mismatches"]) >= 12:
                    break
    if isinstance(sql_full, dict):
        for k, v in sql_ov.items():
            if k not in sql_full or json.dumps(
                sql_full[k], sort_keys=True, default=str
            ) != json.dumps(v, sort_keys=True, default=str):
                rep["mismatches"].append(f"full_config disagrees on override {k}")
                if len(rep["mismatches"]) >= 16:
                    break
    else:
        rep["mismatches"].append("sqlite full_config missing")
    rep["n_overrides"] = len(sql_ov)
    rep["tag"] = entry.get("winning_tag")
    rep["ok"] = len(rep["mismatches"]) == 0
    return rep


def _excluded(key: str) -> str:
    if SECRET_RE.search(key):
        return "secret"
    if key in EXCLUDE_EXACT:
        return "exact-infra/live-only/normalization"
    if key.startswith(EXCLUDE_PREFIX):
        return "ablation-exempt (§64: live proving state never leaks into backtest defaults)"
    if key.endswith(EXCLUDE_SUFFIX):
        return "infra-suffix"
    for s in EXCLUDE_SUBSTR:
        if s in key:
            return "live-only-substr"
    return ""


def _same_val(a, b) -> bool:
    if a is b or a == b:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        try:
            return {True: 1.0, False: 0.0}.get(a, a) == {True: 1.0, False: 0.0}.get(
                b, b
            ) or float(a) == float(b)
        except Exception:
            return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except Exception:
            return False
    return str(a) == str(b)


def _venue_values(stocks: bool, root: Path) -> tuple:
    sys.path.insert(0, str(root))
    import v12_quick_engine as V

    qc = V.QuickConfig()
    if stocks:
        qc.apply_tradier_defaults()
    quick_vals = {
        k: getattr(qc, k) for k in dir(qc) if k.isupper() and not k.startswith("_")
    }
    if stocks:
        import config_tradier as CT

        live = CT.TradierConfig()
    else:
        import config as C

        live = C.Config()
    live_vals = {}
    for k in dir(live):
        if k.isupper() and not k.startswith("_"):
            try:
                live_vals[k] = getattr(live, k)
            except Exception:
                pass
    return live_vals, quick_vals


def verify_default_surfaces(
    cat_side: str = None, root: Path = None, template_dir: str = "SPREADSHEETS"
) -> dict:
    """DEFAULT-layer audit per cat_side: TEMPLATE bold vs cat_side JSON vs venue global vs QuickConfig."""
    root = Path(root) if root else _ROOT()
    sys.path.insert(0, str(root))
    sides = (cat_side,) if cat_side else CAT_SIDES
    rep = {
        "compared": 0,
        "match": 0,
        "mismatches": [],
        "excluded": 0,
        "missing_from_cat": [],
        "per_side": {},
        "template_dir": template_dir,
        "bolds": {},
    }
    try:
        cat_file = json.loads((root / "data" / "cat_side_defaults_4.json").read_text())
    except Exception as _e:
        rep["error"] = f"cat_side file unreadable: {_e}"
        return rep
    import v15_pilot as P

    _bolds_cache = {}
    _prom_cache = {}

    def _bolds_for(cs2):
        if cs2 not in _bolds_cache:
            st2 = cs2.startswith("STOCKS")
            lv2, qv2 = _venue_values(st2, root)
            ty2 = dict(qv2)
            ty2.update(lv2)
            try:
                import per_sym_store as _pss3

                pr2 = set(
                    ((_pss3.get_cat_side_promotions() or {}).get(cs2) or {}).keys()
                )
            except Exception:
                pr2 = set()
            _prom_cache[cs2] = pr2
            try:
                b2, _ = P.template_bold_defaults(
                    root / template_dir / f"TEMPLATE_{cs2}.xlsx", ty2, ty2, pr2
                )
            except Exception:
                b2 = {}
            _bolds_cache[cs2] = b2
        return _bolds_cache[cs2]

    for cs2 in CAT_SIDES:
        rep["bolds"][cs2] = {
            k: b
            for k, b in _bolds_for(cs2).items()
            if isinstance(b, (bool, int, float, str)) or b is None
        }
    for cs in sides:
        stocks = cs.startswith("STOCKS")
        live_vals, quick_vals = _venue_values(stocks, root)
        typed = dict(quick_vals)
        typed.update(live_vals)
        try:
            import per_sym_store as _pss2

            promos_sql = _pss2.get_cat_side_promotions() or {}
            promoted = set((promos_sql.get(cs) or {}).keys())
        except Exception:
            promoted = set()
        tpath = root / template_dir / f"TEMPLATE_{cs}.xlsx"
        try:
            bolds, bad = P.template_bold_defaults(tpath, typed, typed, promoted)
        except Exception as _e:
            rep["mismatches"].append(
                {
                    "cat_side": cs,
                    "key": "*",
                    "class": "template-unreadable",
                    "detail": str(_e)[:200],
                }
            )
            continue
        _bolds_cache[cs] = bolds
        rep["bolds"][cs] = {
            k: b
            for k, b in bolds.items()
            if isinstance(b, (bool, int, float, str)) or b is None
        }
        for b in bad or []:
            rep["mismatches"].append(
                {
                    "cat_side": cs,
                    "key": "*",
                    "class": "template-default-violation",
                    "detail": str(b)[:240],
                }
            )
        other = (
            ("CRYPTO_SHORT" if cs == "CRYPTO_LONG" else "CRYPTO_LONG")
            if not stocks
            else ("STOCKS_SHORT" if cs == "STOCKS_LONG" else "STOCKS_LONG")
        )
        other_bolds = _bolds_for(other)
        cat_map = cat_file.get(cs) or {}
        n_m = n_ok = 0
        for k, bv in sorted(bolds.items()):
            rep["compared"] += 1
            ex = _excluded(k)
            if ex:
                rep["excluded"] += 1
                continue
            if k not in cat_map:
                rep["missing_from_cat"].append(f"{cs}:{k}")
                continue
            cv, gv, qv = (
                cat_map[k],
                live_vals.get(k, "<absent>"),
                quick_vals.get(k, "<absent>"),
            )
            row = {
                "cat_side": cs,
                "key": k,
                "bold": bv,
                "cat": cv,
                "global": gv,
                "quick": qv,
                "class": "",
            }
            ob = other_bolds.get(k, "<absent>")
            row["other_bold"] = ob
            row["global_differs"] = gv != "<absent>" and not _same_val(bv, gv)
            row["quick_differs"] = qv != "<absent>" and not _same_val(bv, qv)
            if not _same_val(bv, cv):
                row["class"] = "bold-vs-cat"
            elif gv == "<absent>" and qv == "<absent>":
                row["class"] = "not-in-configs"
            elif ob != "<absent>" and not _same_val(bv, ob):
                row["class"] = "side-split-cat-truth"
            elif row["global_differs"]:
                row["class"] = "bold-vs-global"
            elif row["quick_differs"]:
                row["class"] = "bold-vs-quick"
                distinct = set()
                for s2 in CAT_SIDES:
                    v2 = rep["bolds"].get(s2, {}).get(k, "<absent>")
                    if v2 != "<absent>":
                        distinct.add(json.dumps(v2, sort_keys=True, default=str))
                if len(distinct) > 2:
                    row["class"] = "fallback-split"
            if row["class"]:
                rep["mismatches"].append(row)
                n_m += 1
            else:
                rep["match"] += 1
                n_ok += 1
        rep["per_side"][cs] = {"match": n_ok, "mismatch": n_m}
    return rep


def _render_value(bold, old_src: str) -> str:
    if isinstance(bold, bool):
        return "True" if bold else "False"
    if isinstance(bold, int) and not isinstance(bold, bool):
        return str(bold)
    if isinstance(bold, float):
        return repr(float(bold))
    if isinstance(bold, str):
        q = '"' if old_src.lstrip().startswith('"') else "'"
        esc = bold.replace("\\", "\\\\").replace(q, "\\" + q)
        return f"{q}{esc}{q}"
    return json.dumps(bold)


def sync_default_surfaces(
    cat_side: str = None,
    apply: bool = False,
    confirm_unlocked: bool = False,
    root: Path = None,
    template_dir: str = "SPREADSHEETS",
    keys: list = None,
) -> dict:
    """Plan (default) or apply template-bold -> global edits. Apply needs explicit unlock."""
    root = Path(root) if root else _ROOT()
    rep = {"planned": [], "needs_manual": [], "applied": [], "error": ""}
    if apply and not confirm_unlocked:
        rep["error"] = (
            "refusing: global files are LOCKED — re-run with --confirm-unlocked after the user unlocks config.py/config_tradier.py/v12_quick_engine.py"
        )
        return rep
    audit = verify_default_surfaces(cat_side, root=root, template_dir=template_dir)
    want = set(keys or [])
    if audit.get("error"):
        rep["error"] = audit["error"]
        return rep
    seen = set()
    _overlay_cache = {}

    def _overlay_range(lines):
        key = id(lines)
        if key not in _overlay_cache:
            try:
                a0 = next(
                    i
                    for i, ln in enumerate(lines)
                    if re.match(r"\s*def apply_tradier_defaults\(self\):", ln)
                )
            except StopIteration:
                a0 = None
            if a0 is None:
                a1 = 0
                a0 = 0
            else:
                a1 = len(lines)
                for i in range(a0 + 1, len(lines)):
                    if lines[i].strip() == "":
                        continue
                    if not lines[i].startswith("        "):
                        a1 = i
                        break
            _overlay_cache[key] = (a0, a1)
        return _overlay_cache[key]

    def _plan_edit(fpath, pat, kind, m, cs, k, bv):
        if (fpath.name, k) in seen:
            return
        try:
            lines = fpath.read_text().splitlines(keepends=True)
        except Exception as _e:
            rep["needs_manual"].append({**m, "why": f"unreadable {fpath.name}: {_e}"})
            return
        hits = [i for i, ln in enumerate(lines) if pat.match(ln.rstrip("\n"))]
        if kind == "quick-overlay":
            a0, a1 = _overlay_range(lines)
            hits = [i for i in hits if a0 < i < a1]
        if len(hits) != 1:
            rep["needs_manual"].append(
                {**m, "why": f"{kind}: {len(hits)} matching lines (need exactly 1)"}
            )
            return
        i = hits[0]
        old = pat.match(lines[i].rstrip("\n"))
        new_val = _render_value(bv, old.group(2))
        if old.group(2).strip() == new_val.strip():
            return
        seen.add((fpath.name, k))
        rep["planned"].append(
            {
                "file": fpath.name,
                "line": i + 1,
                "key": k,
                "cat_side": cs,
                "kind": kind,
                "old": old.group(2).strip()[:80],
                "new": new_val[:80],
            }
        )

    for m in audit["mismatches"]:
        if m.get("class") not in ("bold-vs-global", "bold-vs-quick"):
            continue
        if want and m.get("key") not in want:
            continue
        cs, k, bv = m["cat_side"], m["key"], m["bold"]
        if not isinstance(bv, (bool, int, float, str)):
            rep["needs_manual"].append({**m, "why": "non-scalar bold"})
            continue
        stocks = cs.startswith("STOCKS")
        if m.get("global_differs"):
            fpath = root / ("config_tradier.py" if stocks else "config.py")
            _plan_edit(
                fpath,
                re.compile(rf"^(\s*{re.escape(k)}\s*:[^=]+=\s*)(.+?)(\s*(#.*)?)$"),
                "global-field",
                m,
                cs,
                k,
                bv,
            )
        if m.get("quick_differs"):
            v12 = root / "v12_quick_engine.py"
            try:
                vlines = v12.read_text().splitlines(keepends=True)
            except Exception as _e:
                rep["needs_manual"].append(
                    {**m, "why": f"unreadable v12_quick_engine.py: {_e}"}
                )
                continue
            a0, a1 = _overlay_range(vlines)
            has_overlay = any(
                re.match(rf"\s*self\.{re.escape(k)}\s*=", vlines[i])
                for i in range(a0, a1)
            )
            if stocks and has_overlay:
                _plan_edit(
                    v12,
                    re.compile(rf"^(\s*self\.{re.escape(k)}\s*=\s*)(.+?)(\s*(#.*)?)$"),
                    "quick-overlay",
                    m,
                    cs,
                    k,
                    bv,
                )
            else:
                allb = audit.get("bolds", {})
                pin = [
                    allb.get(s2, {}).get(k, "<absent>")
                    for s2 in (
                        ("CRYPTO_LONG", "CRYPTO_SHORT") if has_overlay else CAT_SIDES
                    )
                ]
                if any(p != "<absent>" and not _same_val(p, bv) for p in pin):
                    rep["needs_manual"].append(
                        {
                            **m,
                            "why": "raw shared with another cat_side at another value — needs overlay line",
                        }
                    )
                    continue
                _plan_edit(
                    v12,
                    re.compile(rf"^(\s*{re.escape(k)}\s*:[^=]+=\s*)(.+?)(\s*(#.*)?)$"),
                    "quick-field",
                    m,
                    cs,
                    k,
                    bv,
                )
    if apply and rep["planned"]:
        by_file = {}
        for p in rep["planned"]:
            by_file.setdefault(p["file"], []).append(p)
        for fname, edits in by_file.items():
            fpath = root / fname
            bp = root / "backups" / f"before_parity_sync_{_today()}_{fname}"
            try:
                shutil.copy2(fpath, bp)
                lines = fpath.read_text().splitlines(keepends=True)
                for p in edits:
                    i = p["line"] - 1
                    mline = re.match(
                        r"^(\s*(?:self\.)?{}[^=]*=\s*)(.+?)(\s*(#.*)?)$".format(
                            re.escape(p["key"])
                        ),
                        lines[i].rstrip("\n"),
                    )
                    if not mline:
                        raise RuntimeError(f"line drift {fname}:{p['line']} {p['key']}")
                    eol = "\n" if lines[i].endswith("\n") else ""
                    lines[i] = f"{mline.group(1)}{p['new']}{mline.group(3) or ''}{eol}"
                fpath.write_text("".join(lines))
                import py_compile

                py_compile.compile(str(fpath), doraise=True)
                rep["applied"].append(
                    {"file": fname, "n": len(edits), "backup": bp.name}
                )
            except Exception as _e:
                try:
                    shutil.copy2(bp, fpath)
                except Exception:
                    pass
                rep["error"] = f"{fname}: {_e} (restored from {bp.name})"
                return rep
    return rep


def sync_cat_keys(
    cat_side: str = None,
    keys: list = None,
    apply: bool = False,
    root: Path = None,
    template_dir: str = "SPREADSHEETS",
) -> dict:
    """Fill stale/missing cat_side defaults from template bolds (targeted, per-key guarded).

    Covers ONLY `bold-vs-cat` rows and `missing_from_cat` keys: value = template
    bold. Each key independently: fossil-held pairs skipped (builder re-pins
    them), secrets refused, type-coerce failure skips that key loudly. Writes
    the JSON file (backup first, atomic) + kv dual-write, then verifies re-read.
    Dry-run by default; --apply writes. Template files are never touched.
    """
    root = Path(root) if root else _ROOT()
    rep = {
        "planned": [],
        "skipped": [],
        "fossils_held": [],
        "applied": False,
        "error": "",
    }
    audit = verify_default_surfaces(cat_side, root=root, template_dir=template_dir)
    if audit.get("error"):
        rep["error"] = audit["error"]
        return rep
    want = set(keys or [])
    targets = {}
    for m in audit.get("mismatches", []):
        if m.get("class") != "bold-vs-cat":
            continue
        if want and m.get("key") not in want:
            continue
        targets[(m["cat_side"], m["key"])] = m["bold"]
    for mc in audit.get("missing_from_cat", []):
        try:
            cs, k = mc.split(":", 1)
        except ValueError:
            continue
        if want and k not in want:
            continue
        bv = (audit.get("bolds", {}).get(cs, {}) or {}).get(k, "<absent>")
        if bv != "<absent>":
            targets[(cs, k)] = bv
    if not targets:
        return rep
    sys.path.insert(0, str(root))
    import v12_quick_engine as V

    for (cs, k), bv in sorted(targets.items()):
        if (cs, k) in FOSSIL_HELD:
            rep["fossils_held"].append(
                f"{cs}:{k} (builder P0-FOSSIL pin — template lane owns the bold)"
            )
            continue
        if SECRET_RE.search(k):
            rep["skipped"].append(f"{cs}:{k} secret refused")
            continue
        if not isinstance(bv, (bool, int, float, str)):
            rep["skipped"].append(f"{cs}:{k} non-scalar bold {bv!r}")
            continue
        qc = V.QuickConfig()
        if cs.startswith("STOCKS"):
            qc.apply_tradier_defaults()
        ref = getattr(qc, k, None)
        if ref is None:
            try:
                if cs.startswith("STOCKS"):
                    import config_tradier as CT

                    ref = getattr(CT.TradierConfig(), k, None)
                else:
                    import config as C

                    ref = getattr(C.Config(), k, None)
            except Exception:
                ref = None
        if ref is not None:
            ok_c, cv = coerce_like(bv, ref)
            if not ok_c:
                rep["skipped"].append(f"{cs}:{k} coerce refused: {cv}")
                continue
            bv = cv
        rep["planned"].append({"cat_side": cs, "key": k, "value": bv})
    if not apply or not rep["planned"]:
        return rep
    cat_path = root / "data" / "cat_side_defaults_4.json"
    try:
        raw0 = cat_path.read_text()
        cat = json.loads(raw0)
    except Exception as _e:
        rep["error"] = f"cat file unreadable: {_e}"
        return rep
    try:
        bdir = root / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        bp = (
            bdir
            / f"before_parity_catfill_{_today()}_{datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S')}.json"
        )
        bp.write_text(raw0)
        rep["backup"] = bp.name
    except Exception as _e:
        rep["error"] = f"backup failed (refusing): {_e}"
        return rep
    for p in rep["planned"]:
        cat.setdefault(p["cat_side"], {})[p["key"]] = p["value"]
    try:
        _atomic_write_json(cat_path, cat)
        import per_sym_store as _pss

        _pss.kv_put(_pss.KV_CAT_SIDE_DEFAULTS_4, cat)
        back = json.loads(cat_path.read_text())
        bad = [
            p
            for p in rep["planned"]
            if back.get(p["cat_side"], {}).get(p["key"], "<absent>") != p["value"]
        ]
        if bad:
            rep["error"] = f"post-verify FAILED: {bad[:4]}"
            return rep
        rep["applied"] = True
    except Exception as _e:
        try:
            cat_path.write_text(raw0)
        except Exception:
            pass
        rep["error"] = f"{_e} (restored)"
    return rep


def startup_gate(cat_side: str, template_path: str = None, root: Path = None) -> dict:
    """Pilot startup gate: refuse to run when THIS cat_side has hard default disparity.

    Enforces (refuse): bold-vs-global, bold-vs-quick — the obligated surfaces.
    Warns only (template-lane backlog): bold-vs-cat, missing_from_cat,
    template-default-violation, side-split-cat-truth, fallback-split.
    Result cached in /tmp keyed by template+config md5 (re-verify on change).
    SWITCH_PARITY_GATE=off skips (loud); anything else enforces.
    """
    root = Path(root) if root else _ROOT()
    rep = {
        "cat_side": cat_side,
        "ok": True,
        "cached": False,
        "hard": [],
        "warnings": [],
    }
    if os.environ.get("SWITCH_PARITY_GATE", "enforce") == "off":
        rep["warnings"].append(
            "SWITCH_PARITY_GATE=off (gate skipped — disparity allowed)"
        )
        return rep
    tpath = (
        Path(template_path)
        if template_path
        else root / "SPREADSHEETS" / f"TEMPLATE_{cat_side}.xlsx"
    )
    if tpath.name != f"TEMPLATE_{cat_side}.xlsx":
        rep["warnings"].append(
            f"non-standard template name {tpath.name} — gate audits by cat_side file"
        )
        tpath = root / "SPREADSHEETS" / f"TEMPLATE_{cat_side}.xlsx"
    try:
        sig = hashlib.md5(tpath.read_bytes()).hexdigest()
        for f in (
            "config.py",
            "config_tradier.py",
            "v12_quick_engine.py",
            "data/cat_side_defaults_4.json",
        ):
            p = root / f
            sig += hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else "x"
    except Exception as _e:
        rep["warnings"].append(
            f"gate files unreadable ({_e}) — cannot verify, allowing run"
        )
        return rep
    cache = Path(f"/tmp/switch_parity_gate_{cat_side}.json")
    try:
        c = json.loads(cache.read_text())
        if c.get("sig") == sig:
            rep.update(
                ok=c.get("ok", True),
                cached=True,
                hard=c.get("hard", []),
                warnings=c.get("warnings", []),
            )
            return rep
    except Exception:
        pass
    try:
        audit = verify_default_surfaces(
            cat_side,
            root=root,
            template_dir=(
                str(tpath.parent.relative_to(root))
                if root in tpath.parents
                else "SPREADSHEETS"
            ),
        )
    except Exception as _e:
        rep["warnings"].append(f"gate audit crashed ({_e}) — allowing run")
        return rep
    for m in audit.get("mismatches", []):
        if m.get("class") in ("bold-vs-global", "bold-vs-quick"):
            rep["hard"].append(
                f"{m.get('key')}: bold={m.get('bold')!r} global={m.get('global')!r} quick={m.get('quick')!r}"
            )
        elif m.get("class") in ("bold-vs-cat", "template-default-violation"):
            rep["warnings"].append(
                f"{m.get('class')} {m.get('key')}: {str(m.get('detail', m.get('cat')))[:120]}"
            )
    for mc in audit.get("missing_from_cat", [])[:10]:
        rep["warnings"].append(f"missing_from_cat {mc}")
    rep["ok"] = len(rep["hard"]) == 0
    try:
        cache.write_text(
            json.dumps(
                {
                    "sig": sig,
                    "ok": rep["ok"],
                    "hard": rep["hard"],
                    "warnings": rep["warnings"][:20],
                    "at": _now_iso(),
                }
            )
        )
    except Exception:
        pass
    return rep


ORDER_CALLS = ("queue_trade_action", "execute_trade_action", "execute_now", "_dispatch_reentry_guaranteed", "_ez_queue_trade_action", "_crypto_eta")
CFG_NAMES = ("config", "config_obj", "_ezm_base_config", "cfg", "self.config", "trade_manager.config")
LIVE_ORDER_FILES = ("ez_manage.py", "ez_positions_quick.py")


def _order_function_switch_reads(path: Path) -> dict:
    """AST scan: {KEY: [function names]} for every config switch read inside a function that issues orders
    (calls queue_trade_action / execute_trade_action / execute_now / _dispatch_reentry_guaranteed).
    Reads = getattr(<config-like>, 'KEY', ...), <config-like>.KEY, _psym_get/_psym_cs_get(sym, side, 'KEY', ...)."""
    import ast

    tree = ast.parse(path.read_text(errors="ignore"))
    out = {}

    def _name(n):
        if isinstance(n, ast.Name):
            return n.id
        if isinstance(n, ast.Attribute):
            b = _name(n.value)
            return f"{b}.{n.attr}" if b else n.attr
        return ""

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        if not any(_name(c.func).split(".")[-1] in ORDER_CALLS for c in calls):
            continue
        keys = set()
        _fallback_ids = set()
        for c in calls:  # getattr(config, K) used only as the default argument of _psym_get/_psym_cs_get is a fallback, not a global read
            if _name(c.func).split(".")[-1] in ("_psym_get", "_psym_cs_get"):
                for a_ in c.args[3:]:
                    for sub in ast.walk(a_):
                        _fallback_ids.add(id(sub))
        for c in calls:
            if id(c) in _fallback_ids:
                continue
            f = _name(c.func)
            if f == "getattr" and len(c.args) >= 2 and _name(c.args[0]) in CFG_NAMES and isinstance(c.args[1], ast.Constant) and isinstance(c.args[1].value, str):
                keys.add((c.args[1].value, "global"))
            elif f.split(".")[-1] in ("_psym_get", "_psym_cs_get") and len(c.args) >= 3 and isinstance(c.args[2], ast.Constant) and isinstance(c.args[2].value, str):
                keys.add((c.args[2].value, "cat_side"))
        for n in ast.walk(fn):
            if isinstance(n, ast.Attribute) and _name(n.value) in CFG_NAMES and n.attr.isupper():
                keys.add((n.attr, "global"))
        for k, kind in keys:
            if k.isupper() and len(k) > 2:
                out.setdefault(k, []).append(f"{fn.name}[{kind}]")
    return out


def verify_live_order_switches(root: Path = None, cat_sides=("CRYPTO_LONG", "CRYPTO_SHORT")) -> dict:
    """2026-10-06 director (parity hole): every config.py switch read by a live opener/closer (a function that issues orders in
    ez_manage / ez_positions_quick) must exist in QuickConfig AND in the TEMPLATE-bold layer (cat_side_defaults_4, built from the
    TEMPLATE bolds) with the same value as the live global. HARD findings: missing_in_quickconfig, missing_in_template,
    value_mismatch (live global vs QuickConfig effective value vs cat_side). Curated exclusions (secrets, infra, §64 ablation)
    are reported separately, never as HARD. QuickConfig values are the effective dataclass values (duplicate field names: last wins)."""
    root = Path(root) if root else _ROOT()
    live_vals, quick_vals = _venue_values(False, root)
    try:
        cat_file = json.loads((root / "data" / "cat_side_defaults_4.json").read_text())
    except Exception as _e:
        return {"error": f"cat_side file unreadable: {_e}"}
    reads = {}
    for f in LIVE_ORDER_FILES:
        p = root / f
        if p.exists():
            for k, fns in _order_function_switch_reads(p).items():
                reads.setdefault(k, set()).update(f"{f}:{x}" for x in fns)
    rep = {"order_switches": 0, "hard": [], "excluded": [], "not_config": 0}
    for k in sorted(reads):
        if k not in live_vals:
            rep["not_config"] += 1
            continue
        rep["order_switches"] += 1
        ex = _excluded(k) or ("parity/bridge infra master (live-only by design)" if k.startswith(("PARITY_VEC_EXACT_", "VEC_DRIVEN_")) or k == "EXIT_ENGINE_PARITY_LOG_ENABLED" else "")
        if ex:
            rep["excluded"].append({"key": k, "why": ex})
            continue
        lv = live_vals[k]
        if callable(lv) or isinstance(lv, (dict, list, set, tuple, Path)):
            continue
        kinds = {x.rsplit("[", 1)[-1].rstrip("]") for x in reads[k]}
        prob = []
        if k not in quick_vals and not all(k in (cat_file.get(cs) or {}) for cs in cat_sides):
            prob.append("missing_in_quickconfig_and_template")
        if "global" in kinds and "cat_side" in kinds and any(k in (cat_file.get(cs) or {}) and not _same_val(lv, (cat_file.get(cs) or {})[k]) for cs in cat_sides):
            prob.append("live_inconsistent_read(global+cat_side sites disagree)")
        for cs in cat_sides:
            cv = cat_file.get(cs) or {}
            vec_eff = cv[k] if k in cv else quick_vals.get(k, "<missing>")
            live_eff = cv[k] if (kinds == {"cat_side"} and k in cv) else lv
            if not _same_val(live_eff, vec_eff):
                prob.append(f"{cs}: live_eff={live_eff!r} vec_eff={vec_eff!r}")
        if prob:
            rep["hard"].append({"key": k, "live": lv, "problems": prob, "read_in": sorted(reads[k])[:4]})
        elif k not in quick_vals:
            rep.setdefault("soft_missing_quickconfig", []).append(k)
    rep["n_hard"] = len(rep["hard"])
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="switch_parity")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_reg = sub.add_parser(
        "register", help="register one sym_side result (usually called by v15_pilot)"
    )
    p_reg.add_argument("--sym-side", required=True)
    p_reg.add_argument(
        "--overrides", required=True, help="JSON dict of promoted overrides"
    )
    p_reg.add_argument(
        "--evidence", required=True, help="JSON dict of fresh full-set metrics"
    )
    p_reg.add_argument("--lift-block", action="store_true")
    p_reg.add_argument("--dry-run", action="store_true")
    p_ver = sub.add_parser(
        "verify-sym", help="stored-surface agreement for one sym_side"
    )
    p_ver.add_argument("--sym-side", required=True)
    p_def = sub.add_parser("verify-defaults", help="DEFAULT-layer audit per cat_side")
    p_def.add_argument("--cat-side", default=None)
    p_def.add_argument("--template-dir", default="SPREADSHEETS")
    p_def.add_argument(
        "--strict", action="store_true", help="exit 1 on any hard mismatch"
    )
    p_def.add_argument("--show", type=int, default=25)
    p_gate = sub.add_parser(
        "gate", help="startup-gate check for one cat_side (cron-friendly)"
    )
    p_gate.add_argument("--cat-side", required=True)
    p_gate.add_argument("--template", default=None)
    p_syn = sub.add_parser(
        "sync-defaults", help="plan/apply template-bold -> global edits"
    )
    p_syn.add_argument("--cat-side", default=None)
    p_syn.add_argument("--template-dir", default="SPREADSHEETS")
    p_syn.add_argument(
        "--keys", default=None, help="comma-separated key filter for targeted sync"
    )
    p_syn.add_argument("--apply", action="store_true")
    p_syn.add_argument("--confirm-unlocked", action="store_true")
    p_lvs = sub.add_parser(
        "verify-live-switches", help="every switch read by a live crypto opener/closer exists in QuickConfig + TEMPLATE with the live value"
    )
    p_lvs.add_argument("--strict", action="store_true", help="exit 1 on any HARD finding")
    p_lvs.add_argument("--show", type=int, default=40)
    p_lvs.add_argument("--json", default=None, help="write the full report here")
    p_cat = sub.add_parser(
        "sync-cat", help="fill stale/missing cat_side defaults from template bolds"
    )
    p_cat.add_argument("--cat-side", default=None)
    p_cat.add_argument("--template-dir", default="SPREADSHEETS")
    p_cat.add_argument(
        "--keys", default=None, help="comma-separated key filter for targeted sync"
    )
    p_cat.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "verify-live-switches":
        rep = verify_live_order_switches()
        if a.json:
            Path(a.json).write_text(json.dumps(rep, indent=1, default=str))
        print(f"LIVE_ORDER_SWITCHES order_switches={rep.get('order_switches')} hard={rep.get('n_hard')} excluded={len(rep.get('excluded') or [])}")
        for h in (rep.get("hard") or [])[: a.show]:
            print(f"  HARD {h['key']} live={h['live']!r} {h['problems']} read_in={h['read_in']}")
        return 1 if (a.strict and rep.get("n_hard")) else 0
    if a.cmd == "register":
        rep = register_workbook_result(
            a.sym_side,
            json.loads(a.overrides),
            json.loads(a.evidence),
            lift_block=a.lift_block,
            dry_run=a.dry_run,
        )
        print(json.dumps(rep, indent=1, default=str))
        return 0 if rep.get("registered") or a.dry_run else 1
    if a.cmd == "verify-sym":
        rep = verify_symside(a.sym_side)
        print(json.dumps(rep, indent=1, default=str))
        return 0 if rep.get("ok") else 1
    if a.cmd == "verify-defaults":
        rep = verify_default_surfaces(a.cat_side, template_dir=a.template_dir)
        hard = [
            m
            for m in rep.get("mismatches", [])
            if m.get("class") in ("bold-vs-global", "bold-vs-quick")
        ]
        tviol = [
            m
            for m in rep.get("mismatches", [])
            if m.get("class") == "template-default-violation"
        ]
        print(
            json.dumps(
                {
                    "compared": rep.get("compared"),
                    "match": rep.get("match"),
                    "excluded": rep.get("excluded"),
                    "missing_from_cat": len(rep.get("missing_from_cat", [])),
                    "hard_mismatch": len(hard),
                    "template_violations": len(tviol),
                    "per_side": rep.get("per_side"),
                    "sample": hard[: a.show],
                    "tsample": tviol[:5],
                },
                indent=1,
                default=str,
            )
        )
        return 1 if (a.strict and (hard or tviol)) else 0
    if a.cmd == "gate":
        rep = startup_gate(a.cat_side, template_path=a.template)
        print(json.dumps(rep, indent=1, default=str))
        return 0 if rep.get("ok") else 1
    if a.cmd == "sync-defaults":
        rep = sync_default_surfaces(
            a.cat_side,
            apply=a.apply,
            confirm_unlocked=a.confirm_unlocked,
            template_dir=a.template_dir,
            keys=(a.keys.split(",") if a.keys else None),
        )
        print(json.dumps(rep, indent=1, default=str))
        return 0 if not rep.get("error") else 1
    if a.cmd == "sync-cat":
        rep = sync_cat_keys(
            a.cat_side,
            keys=(a.keys.split(",") if a.keys else None),
            apply=a.apply,
            template_dir=a.template_dir,
        )
        print(json.dumps(rep, indent=1, default=str))
        return 0 if not rep.get("error") else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
