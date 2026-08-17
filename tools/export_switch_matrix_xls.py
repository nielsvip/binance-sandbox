#!/usr/bin/env python3
"""export_switch_matrix_xls.py — USER 2026-07-20 spreadsheet spec.

ONE ROW PER (switch, value). ONE COLUMN PER KEY — every symbol in
symbols_trb_long.json (as <SYM>_LONG) and symbols_trb_short.json (as <SYM>_SHORT).
Cell = delta gain/mo of that switch-value vs that key's baseline (blank = not tested yet).

Rows are emitted for the FULL manifest (every switch x every swept value), so the sheet is
the complete work-list: blanks show what still has to run, values fill in as the OFAT
progresses. Sheets:
  Matrix       switch | value | n_tested | mean_delta | best_key | worst_key | <one col per key>
  Coverage     per switch: how many keys tested, how many helped (delta>0), how many hurt
  Baselines    per key: gain/mo, b&h/mo, delta, trades, campaign

Source: data/param_results_stocks.db (param_cells + key_baseline) — the campaign store.
Output: data/reports/SWITCH_MATRIX_TRB.xlsx  (+ .csv.gz for the full width)

Usage (S1 or Mac):  python tools/export_switch_matrix_xls.py [--campaign stocks_baseline_v2_s4h]
"""
import argparse
import csv
import fcntl
import gzip
import hashlib
import json
import os
import re
import sqlite3
import time
import zipfile
from pathlib import Path

try:
    from tools.stock_capacity_contract import (
        is_expected_stock_capacity_saturation,
    )
except ModuleNotFoundError:
    from stock_capacity_contract import is_expected_stock_capacity_saturation

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "param_results_stocks.db"
FLEET_DB = BASE / "data" / "reports" / "path_fleet" / "queue.db"
CURRENT_ENGINE_CAMPAIGN = "stocks_repaired_20260730_c5"
CURRENT_ENGINE_CUTOFF = "2026-07-30T03:30:10Z"
CURRENT_CAPITAL_ACCOUNTING_VERSION = "avg-trade-deployed-2000-v1"
CURRENT_MATRIX_SCOPE = (
    "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY"
)
SURFACE_LOCK = BASE / "data" / "reports" / ".switch_matrix_trb_surface.lock"


def set_local_immutable(path, enabled):
    """Use the macOS immutable flag to close the report-sync overwrite race.

    S1/Linux continues to rely on the exporter surface lock.  The synchronized
    Mac checkout has an external puller that does not honor that lock, so the
    exact artifacts must be unlocked only for the atomic replacement and
    relocked before this process returns.
    """
    import stat
    import sys

    if sys.platform != "darwin" or not Path(path).exists():
        return
    flag = getattr(stat, "UF_IMMUTABLE", 0)
    if not flag:
        return
    current = Path(path).stat().st_flags
    os.chflags(path, (current | flag) if enabled else (current & ~flag))


def acquire_surface_lock():
    """Serialize matrix materialization/export jobs across cron and agents."""
    SURFACE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = SURFACE_LOCK.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"SWITCH_MATRIX_SURFACE_BUSY:{SURFACE_LOCK}") from exc
    return handle


def rebind_digest_provenance_after_canonical_export(
    csv_path: Path,
    expected_header_keys: set[str],
) -> None:
    """Atomically bind the existing digest sidecar to a newly exported gzip.

    The canonical exporter changes the gzip before ``switch_matrix_digest``
    can refresh its sidecar.  Without this narrow hand-off, the first digest
    run correctly sees a hash mismatch but reports a frightening false FATAL.
    Rebinding is allowed only when the gzip metadata and complete key header
    still match the prior provenance policy; any real scope/universe drift
    remains fail-closed.
    """
    sidecar = BASE / "data/reports/SWITCH_MATRIX_TRB_DIGEST.md.provenance.json"
    if not sidecar.is_file() or not csv_path.is_file():
        return
    payload = json.loads(sidecar.read_text())
    if payload.get("schema") != "switch-matrix-trb-current-digest-v1":
        raise RuntimeError("cannot rebind digest provenance: wrong sidecar schema")
    metadata = {}
    header = None
    with gzip.open(csv_path, "rt", newline="") as handle:
        for line in handle:
            if line.startswith("#"):
                if "=" in line:
                    name, value = line[1:].strip().split("=", 1)
                    metadata[name.strip()] = value.strip()
                continue
            header = next(csv.reader([line]))
            break
    expected = {
        "CURRENT_CAMPAIGN": payload.get("campaign"),
        "CURRENT_ENGINE_CUTOFF": payload.get("engine_cutoff"),
        "CURRENT_CONTRACT_VERSION": payload.get("contract_version"),
        "CURRENT_MATRIX_SCOPE": payload.get("matrix_scope"),
        "CANONICAL_MATRIX": payload.get("canonical_matrix"),
    }
    if any(metadata.get(name) != value for name, value in expected.items()):
        raise RuntimeError("cannot rebind digest provenance: matrix scope changed")
    # The ranking job is the live universe authority. Rebuild the ranked TIM
    # policy when it has advanced since the prior matrix export; retaining the
    # old policy would keep obsolete keys in the provenance contract.
    live_policy = {}
    for filename, side in (
        ("symbols_trb_long.json", "LONG"),
        ("symbols_trb_short.json", "SHORT"),
    ):
        symbols = json.loads((BASE / filename).read_text())
        symbols = symbols if isinstance(symbols, list) else list(symbols)
        for rank, symbol in enumerate(symbols, 1):
            key = f"{str(symbol).upper()}_{side}"
            top = rank <= 10
            live_policy[key] = {
                "rank": rank,
                "cohort": "TOP_10" if top else "REMAINDER",
                "tim_min_pct": 50.0 if top else 20.0,
                "tim_max_pct": 80.0 if top else 60.0,
                "source": (
                    f"symbols_trb_{side.lower()}:first_10"
                    if top
                    else f"symbols_trb_{side.lower()}:rank_gt_10"
                ),
            }
    policy_keys = set(live_policy)
    header_keys = {
        str(name) for name in (header or [])
        if str(name).endswith(("_LONG", "_SHORT"))
    }
    # The canonical matrix retains configured historical keys as columns while
    # tim_policy intentionally covers only the pinned ACTIVE universe.  Require
    # the complete configured header and require every active policy key to be
    # present; equality would make every legitimate historical column break the
    # atomic export hand-off.
    if (
        not policy_keys
        or not policy_keys.issubset(header_keys)
        or header_keys != set(expected_header_keys)
    ):
        raise RuntimeError("cannot rebind digest provenance: exported header disagrees with live universe")
    payload["tim_policy"] = live_policy
    payload["active_key_count"] = len(live_policy)
    payload["canonical_matrix_sha256"] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    payload["matrix_export_rebound_at_ns"] = time.time_ns()
    tmp = sidecar.with_name(f".{sidecar.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    set_local_immutable(sidecar, False)
    os.replace(tmp, sidecar)
    set_local_immutable(sidecar, True)


def key_columns(account):
    # TRB/TRC matrix columns follow the live Tradier ranking outputs exactly.
    # Historical/pinned snapshots remain useful for audits, but must not keep
    # obsolete or incomplete symbol-side columns alive in the current switch
    # matrix.  The ranking job owns these two generated files.
    prefix = "trb" if account == "trc" else account
    if prefix == "trb":
        cols = []
        for filename, side in (
            ("symbols_trb_long.json", "LONG"),
            ("symbols_trb_short.json", "SHORT"),
        ):
            raw = json.loads((BASE / filename).read_text())
            symbols = raw if isinstance(raw, list) else list(raw)
            cols.extend(f"{str(symbol).upper()}_{side}" for symbol in symbols)
        return list(dict.fromkeys(cols))
    cols = []
    for fname, side in ((f"symbols_{prefix}_long.json", "LONG"), (f"symbols_{prefix}_short.json", "SHORT")):
        p = BASE / fname
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        syms = d if isinstance(d, list) else list(d)
        cols += [f"{str(s).upper()}_{side}" for s in syms]
    seen, out = set(), []
    for c in cols:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def norm_val(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            pass
    s = str(v).strip()
    low = s.lower()
    if low in ("true", "yes", "on"): return "true"
    if low in ("false", "no", "off"): return "false"
    if low in ("none", "null", ""): return "none"
    try:
        f = float(s)
        if f != f or f in (float("inf"), float("-inf")): return low
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError, OverflowError):
        return low


def output_suffix(tier, campaign):
    """Return a collision-safe report suffix for one evidence campaign."""
    if tier == "VEC":
        return "_VEC_DIAGNOSTIC"
    if campaign == CURRENT_ENGINE_CAMPAIGN:
        return ""
    campaign_slug = re.sub(
        r"[^A-Za-z0-9]+", "_", str(campaign or "ALL")
    ).strip("_").upper()
    return f"_ENGINE_HIST_{campaign_slug}"


def manifest_rows(mode="tradier", actionable=True):
    p = BASE / f"data/param_sweep_manifest_{mode}.json"
    if not p.exists(): return []
    man = json.loads(p.read_text()).get("params", {})
    rows = []
    for name, spec in man.items():
        if actionable and isinstance(spec, dict) and not bool(spec.get("sweepable")):
            continue
        vals = None
        if isinstance(spec, dict):
            for k in ("test_values", "values", "sweep_values", "range"):
                if isinstance(spec.get(k), (list, tuple)) and spec[k]:
                    vals = list(spec[k])
                    break
            if vals is None: vals = [spec.get("default")]
        elif isinstance(spec, (list, tuple)): vals = list(spec)
        else: vals = [spec]
        for v in vals: rows.append((name, norm_val(v)))
    return rows


def manifest_inventory(mode="tradier"):
    """Non-actionable settings retained for audit, not shown as blank work."""
    p = BASE / f"data/param_sweep_manifest_{mode}.json"
    if not p.exists():
        return []
    man = json.loads(p.read_text()).get("params", {})
    out = []
    for name, spec in man.items():
        if not isinstance(spec, dict) or bool(spec.get("sweepable")):
            continue
        tier = spec.get("sweep_tier") or ""
        consumed = spec.get("consumed_by") or {}
        if tier == "DEAD" or not any(consumed.values()):
            reason = "DEAD / no live or engine consumer; reconnect before testing"
        elif tier == "LIVE_ONLY":
            reason = "LIVE_ONLY / no faithful backtest consumer"
        else:
            reason = "explicitly sweepable:false in the manifest"
        out.append((name, tier, reason, json.dumps(consumed, sort_keys=True)))
    return sorted(out)


def _load_canonical_engine_cells():
    """Use the same receipt validator and logical-cell merge as web/email.

    This is intentionally the only source for the unsuffixed ENGINE workbook:
    full c5 > receipt-validated c2/c3/c4 > c1 > current c5 1yr gap-fill.
    """
    try:
        from tools import current_matrix_reporting as reporting
    except ModuleNotFoundError:
        import current_matrix_reporting as reporting

    cells, inert, cell_meta, base = {}, {}, {}, {}
    rows = reporting.merged_rows(BASE)
    for row in rows:
        key = str(row["key"])
        ck = (
            str(row.get("param") or ""),
            norm_val(row.get("value_json")),
            key,
        )
        delta = row.get("delta_gain_mo_vs_bh")
        if delta is None:
            continue
        cells[ck] = float(delta)
        inert[ck] = bool(row.get("inert"))
        cell_meta[ck] = {
            "bh_delta": delta,
            "gain_per_mo": row.get("gain_per_mo"),
            "trades": row.get("trades"),
            "fingerprint": row.get("trades_fingerprint"),
            "inert": bool(row.get("inert")),
            "validation_status": row.get("validation_status"),
            "real_closes": row.get("real_closes"),
            "reentry_violations": row.get("reentry_violations"),
            "expected_capacity_saturation": is_expected_stock_capacity_saturation(
                row.get("result_audit_json")
            ),
            "window": row.get("window"),
            "campaign": row.get("campaign"),
            "contract_fingerprint": row.get("contract_fingerprint"),
            "capital_accounting_version": row.get(
                "capital_accounting_version"
            ),
            "average_deployed_usd": row.get("average_deployed_usd"),
            "benchmark_deployed_usd": row.get("benchmark_deployed_usd"),
            "pool_sharpe": row.get("pool_sharpe"),
            "max_dd_pct": row.get("max_dd_pct"),
            "source_file": row.get("source_file"),
        }

    # The Baselines sheet is descriptive. Derive its control and B&H rates
    # from the same selected evidence row so its provenance cannot disagree
    # with the matrix cell selector.
    for row in reporting.best_rows(BASE):
        gain = row.get("gain_per_mo")
        delta_control = row.get("delta_vs_baseline_gain_mo")
        bh = row.get("bh_gain_per_mo")
        control_gain = (
            float(gain) - float(delta_control)
            if gain is not None and delta_control is not None
            else None
        )
        base[str(row["key"])] = {
            "gain_per_mo": control_gain,
            "bh_per_mo": bh,
            "delta_vs_bh": (
                control_gain - float(bh)
                if control_gain is not None and bh is not None
                else None
            ),
            "trades": row.get("trades"),
            "campaign": row.get("campaign"),
            "contract_fingerprint": row.get("contract_fingerprint"),
            "capital_accounting_version": row.get(
                "capital_accounting_version"
            ),
            "window": row.get("window"),
        }

    fps = {}
    for (param, val, key), meta in cell_meta.items():
        fp = meta.get("fingerprint")
        if fp:
            fps.setdefault((param, key, fp), set()).add(val)
    for (param, key, fp), values in fps.items():
        if len(values) > 1:
            for val in values:
                cell_meta[(param, val, key)]["same_value_fingerprint"] = True
    return cells, inert, cell_meta, base


def load_cells(campaign, tier="ENGINE", extra_campaign=None):
    """Cells for ONE tier only.

    2026-07-21 NO-LIES FIX: this used to read every row of param_cells into one grid. 86% of
    those rows are Tier-1 vec screens whose deltas were subtracted from a Tier-2 ENGINE
    baseline — a tier gap, not a knob effect (MU_LONG: 495 cells all showing a phantom
    +1.3917 %/mo). Tier-1 and Tier-2 numbers are not comparable and MUST NOT share a grid:
    the engine sheet is the truth, the vec sheet is a [DIAGNOSTIC] screen, and a cell whose
    override provably changed nothing (inert=1) reads INERT, never a number.
    Returns (cells, inert_flags, cell_meta, base). `cells` and the visible matrix values are
    delta gain/mo versus B&H; `cell_meta` retains the result fields needed for coloring and
    zero-trade/fingerprint checks."""
    if not DB.exists(): return {}, {}, {}, {}
    if tier == "ENGINE" and campaign == CURRENT_ENGINE_CAMPAIGN:
        return _load_canonical_engine_cells()
    con = sqlite3.connect(str(DB))
    cells, inert, cell_meta = {}, {}, {}
    q = ("SELECT symbol, side, param, value_json, delta_vs_baseline_gain_mo, gain_per_mo, inert "
         "FROM param_cells WHERE mode='tradier' AND COALESCE(tier,'ENGINE')=?")
    # The matrix used to display delta_vs_baseline_gain_mo. That baseline is not the user's
    # floor. Keep the legacy delta only as a fallback, but make the visible cell and color logic
    # use the stored, same-key delta_gain_mo_vs_bh from the faithful engine.
    q = ("SELECT symbol, side, param, value_json, delta_gain_mo_vs_bh, delta_vs_baseline_gain_mo, "
         "gain_per_mo, trades, inert, trades_fingerprint, validation_status, "
         "contract_fingerprint, real_closes, reentry_violations, result_audit_json, "
         "capital_accounting_version "
         "FROM param_cells WHERE mode='tradier' AND COALESCE(tier,'ENGINE')=?")
    args = [tier]
    if campaign:
        q += " AND campaign=?"
        args.append(campaign)
    # More than one campaign can write the same logical cell.  The unrestricted matrix is
    # explicitly a "latest evidence" view, so make overwrite order deterministic instead of
    # relying on SQLite's unspecified scan order.
    if campaign == CURRENT_ENGINE_CAMPAIGN:
        q += (
            " AND ts>=? AND validation_status IN "
            "('PASS')"
        )
        args.append(CURRENT_ENGINE_CUTOFF)
    q += " ORDER BY ts"
    expected = {}
    if campaign == CURRENT_ENGINE_CAMPAIGN:
        try:
            try:
                from tools import persym_baseline_campaign as psc
            except ModuleNotFoundError:
                import persym_baseline_campaign as psc
            expected = {
                f"{sym}_{side}": psc.matrix_contract_fingerprints(sym, side)
                for sym, side in con.execute(
                    "SELECT symbol,side FROM param_cells WHERE campaign=? "
                    "UNION SELECT symbol,side FROM key_baseline WHERE campaign=?",
                    (campaign, campaign),
                )
            }
        except Exception:
            expected = {}
    for (sym, side, param, val, bh_delta, legacy_delta, gpm, trades, inrt, fp,
         validation, contract_fp, real_closes, reentry_violations,
         result_audit_json, capital_version) in con.execute(q, args).fetchall():
        key = f"{sym}_{side}"
        if (
            campaign == CURRENT_ENGINE_CAMPAIGN
            and contract_fp not in expected.get(key, set())
        ):
            continue
        ck = (param, norm_val(val), f"{sym}_{side}")
        if (
            campaign == CURRENT_ENGINE_CAMPAIGN
            and capital_version != CURRENT_CAPITAL_ACCOUNTING_VERSION
        ):
            cells.setdefault(ck, None)
            cell_meta[ck] = {
                "accounting_status": "PENDING_CAPITAL_REVALUE",
                "window": "full",
            }
            continue
        delta = bh_delta if bh_delta is not None else legacy_delta
        cells[ck] = delta if delta is not None else gpm
        inert[ck] = bool(inrt)
        cell_meta[ck] = {"bh_delta": bh_delta, "gain_per_mo": gpm, "trades": trades,
                         "fingerprint": fp, "inert": bool(inrt),
                         "validation_status": validation, "real_closes": real_closes,
                         "reentry_violations": reentry_violations,
                         "expected_capacity_saturation":
                             is_expected_stock_capacity_saturation(result_audit_json),
                         "window": "full"}
    if tier == "ENGINE" and campaign != CURRENT_ENGINE_CAMPAIGN:
        try:
            srows = con.execute(
                "SELECT symbol, side, param, value_json, delta_gain_mo, overrides_json "
                "FROM stage_results ORDER BY ts"
            ).fetchall()
        except sqlite3.OperationalError:
            srows = []
        for sym, side, param, val, delta, ojson in srows:
            if delta is None: continue
            key = f"{sym}_{side}"
            ck = (param, norm_val(val), key)
            cells.setdefault(ck, delta)
            cell_meta.setdefault(ck, {"bh_delta": None, "gain_per_mo": None,
                                      "trades": None, "fingerprint": None, "inert": False})
            if ojson:
                try:
                    overrides = json.loads(ojson)
                except Exception:
                    overrides = {}
                # A PACK delta belongs to the pack, not to each knob inside it. Crediting the
                # same number to every constituent is how one result became many identical
                # "winners". Kept only where the pack changed exactly one knob.
                if isinstance(overrides, dict) and len(overrides) == 1:
                    for oname, oval in overrides.items(): cells.setdefault((oname, norm_val(oval), key), delta)
    if extra_campaign:
        # 2026-07-30 USER: temporary 1yr-window diagnostic campaign fills gaps ONLY —
        # never overwrites a full-window cell. Tagged window="1yr" so the xlsx export
        # can font-color these distinctly from complete (full-window) tests.
        eq = ("SELECT symbol, side, param, value_json, delta_gain_mo_vs_bh, delta_vs_baseline_gain_mo, "
              "gain_per_mo, trades, inert, trades_fingerprint, validation_status, "
              "contract_fingerprint, real_closes, reentry_violations, result_audit_json, "
              "capital_accounting_version "
              "FROM param_cells WHERE mode='tradier' AND COALESCE(tier,'ENGINE')=? "
              "AND campaign=? AND validation_status='PASS' AND real_closes>0 "
              "AND capital_accounting_version=? ORDER BY ts")
        for (sym, side, param, val, bh_delta, legacy_delta, gpm, trades, inrt, fp,
             validation, contract_fp, real_closes, reentry_violations,
             result_audit_json, capital_version) in con.execute(
                 eq, [tier, extra_campaign, CURRENT_CAPITAL_ACCOUNTING_VERSION]
             ).fetchall():
            if not str(contract_fp or "").startswith(
                "tradier-matrix-exec-c5-20260730:"
            ):
                continue
            key = f"{sym}_{side}"
            ck = (param, norm_val(val), key)
            if ck in cells:
                continue
            delta = bh_delta if bh_delta is not None else legacy_delta
            cells[ck] = delta if delta is not None else gpm
            inert[ck] = bool(inrt)
            cell_meta[ck] = {"bh_delta": bh_delta, "gain_per_mo": gpm, "trades": trades,
                             "fingerprint": fp, "inert": bool(inrt),
                             "validation_status": validation, "real_closes": real_closes,
                             "reentry_violations": reentry_violations,
                             "expected_capacity_saturation":
                                 is_expected_stock_capacity_saturation(result_audit_json),
                             "window": "1yr"}
    base = {}
    bq = ("SELECT symbol, side, gain_per_mo, bh_per_mo, delta_gain_mo_vs_bh, trades, campaign, "
          "trades_fingerprint, validation_status, contract_fingerprint, "
          "capital_accounting_version "
          "FROM key_baseline WHERE mode='tradier'")
    bargs = []
    if campaign:
        bq += " AND campaign=?"
        bargs.append(campaign)
    if campaign == CURRENT_ENGINE_CAMPAIGN:
        bq += (
            " AND ts>=? AND validation_status IN "
            "('PASS')"
        )
        bargs.append(CURRENT_ENGINE_CUTOFF)
    bq += " ORDER BY ts"
    for sym, side, g, bh, d, tr, camp, fp, validation, contract_fp, capital_version in con.execute(
        bq, bargs
    ).fetchall():
        key = f"{sym}_{side}"
        if (
            campaign == CURRENT_ENGINE_CAMPAIGN
            and contract_fp not in expected.get(key, set())
        ):
            continue
        if (
            campaign == CURRENT_ENGINE_CAMPAIGN
            and capital_version != CURRENT_CAPITAL_ACCOUNTING_VERSION
        ):
            base[f"{sym}_{side}"] = {
                "gain_per_mo": None,
                "bh_per_mo": None,
                "delta_vs_bh": None,
                "trades": tr,
                "campaign": camp,
                "fingerprint": fp,
                "validation_status": "PENDING_CAPITAL_REVALUE",
            }
            continue
        base[f"{sym}_{side}"] = {"gain_per_mo": g, "bh_per_mo": bh, "delta_vs_bh": d,
                                  "trades": tr, "campaign": camp, "fingerprint": fp,
                                  "validation_status": validation}
    con.close()
    # Equal fingerprints across two values of the same switch are a wiring failure, even when
    # rounding makes the gain deltas look slightly different. Mark all such values red later.
    fps = {}
    for (param, val, key), meta in cell_meta.items():
        fp = meta.get("fingerprint")
        if fp:
            fps.setdefault((param, key, fp), set()).add(val)
    for (param, key, fp), values in fps.items():
        if len(values) > 1:
            for val in values:
                cell_meta[(param, val, key)]["same_value_fingerprint"] = True
    return cells, inert, cell_meta, base


def load_engine_coverage(campaign):
    """Explain why an ENGINE cell is blank without importing VEC evidence.

    The canonical matrix deliberately exposes only contract-matched ``param_cells``.
    This companion index is diagnostic metadata:

    * ``stale`` means an exact engine row exists for the same key/knob/value, but its
      code+NPZ+side fingerprint is no longer current;
    * ``vec_only`` means a vector screen exists, but there is no current exact-engine
      measurement.  It remains blank and is explicitly labelled EXACT_QUEUE_EMPTY;
    * ``fleet_exact`` lists V8 exact replays from the path-fleet ledger.  They are
      informational unless their payload explicitly names one attributable matrix
      knob/value.  Return-% is never silently converted to gain/month.

    Keeping this outside ``load_cells`` is important: no VEC or research-only value can
    enter the ENGINE numeric cell dictionary by accident.
    """
    out = {
        "stale": set(),
        "invalid": set(),
        "vec_only": set(),
        "raw_engine_rows": 0,
        "current_engine_rows": 0,
        "stale_engine_rows": 0,
        "invalid_engine_rows": 0,
        "pending_capital": set(),
        "fleet_exact": [],
        "fleet_exact_attributable": 0,
    }
    if not DB.exists():
        return out
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    valid_statuses = {"PASS"}
    available_columns = {
        row["name"] for row in con.execute("PRAGMA table_info(param_cells)")
    }
    capital_expr = (
        "capital_accounting_version"
        if "capital_accounting_version" in available_columns
        else "NULL AS capital_accounting_version"
    )
    rows = con.execute(
        "SELECT symbol,side,param,value_json,validation_status,contract_fingerprint,"
        f"ts,source_file,{capital_expr} FROM param_cells WHERE mode='tradier' "
        "AND COALESCE(tier,'ENGINE')='ENGINE' AND campaign=? AND ts>=? ORDER BY ts",
        (campaign, CURRENT_ENGINE_CUTOFF),
    ).fetchall()
    out["raw_engine_rows"] = len(rows)
    expected = {}
    if rows:
        try:
            try:
                from tools import persym_baseline_campaign as psc
            except ModuleNotFoundError:
                import persym_baseline_campaign as psc
            expected = {
                f"{row['symbol']}_{row['side']}": psc.matrix_contract_fingerprints(
                    row["symbol"], row["side"]
                )
                for row in rows
            }
        except Exception:
            expected = {}
    for row in rows:
        key = f"{row['symbol']}_{row['side']}"
        logical = (row["param"], norm_val(row["value_json"]), key)
        if row["validation_status"] not in valid_statuses:
            out["invalid"].add(logical)
            out["invalid_engine_rows"] += 1
        elif row["contract_fingerprint"] not in expected.get(key, set()):
            out["stale"].add(logical)
            out["stale_engine_rows"] += 1
        elif row["capital_accounting_version"] != CURRENT_CAPITAL_ACCOUNTING_VERSION:
            out["pending_capital"].add(logical)
        else:
            out["current_engine_rows"] += 1
    # This query is presence-only.  No VEC metric is selected or returned.
    try:
        for row in con.execute(
            "SELECT DISTINCT symbol,side,param,value_json FROM param_cells "
            "WHERE mode='tradier' AND tier='VEC'"
        ):
            out["vec_only"].add(
                (row["param"], norm_val(row["value_json"]),
                 f"{row['symbol']}_{row['side']}")
            )
    except sqlite3.OperationalError:
        pass
    con.close()

    fleet_db = FLEET_DB
    if fleet_db.exists():
        try:
            fleet = sqlite3.connect(f"file:{fleet_db}?mode=ro", uri=True, timeout=30)
            fleet.row_factory = sqlite3.Row
            for row in fleet.execute(
                "SELECT j.path_id,r.symbol,r.side,r.stage,r.status,"
                "r.strategy_return_pct,r.bh_return_pct,r.same_entry_control_return_pct,"
                "r.payload_json,r.artifact,r.created_at "
                "FROM results r JOIN jobs j ON j.id=r.job_id "
                "WHERE r.exact_replay=1 OR UPPER(r.stage) LIKE '%EXACT%' "
                "ORDER BY r.created_at"
            ):
                payload = {}
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    pass
                key = f"{row['symbol']}_{row['side']}"
                payload_campaign = str(
                    payload.get("campaign")
                    or payload.get("campaign_id")
                    or ""
                )
                payload_contract = str(
                    payload.get("contract_fingerprint")
                    or (payload.get("audit") or {}).get(
                        "contract_fingerprint"
                    )
                    or ""
                )
                allowed_contracts = expected.get(key)
                if not allowed_contracts:
                    try:
                        try:
                            from tools import persym_baseline_campaign as psc
                        except ModuleNotFoundError:
                            import persym_baseline_campaign as psc
                        allowed_contracts = psc.matrix_contract_fingerprints(
                            row["symbol"], row["side"]
                        )
                    except Exception:
                        allowed_contracts = set()
                # A path-fleet replay is a separate research artifact. It may
                # appear in the canonical workbook only when it explicitly
                # binds both the repaired campaign and this key's current
                # code+NPZ+side fingerprint. Missing identity fails closed.
                if (
                    payload_campaign != CURRENT_ENGINE_CAMPAIGN
                    or payload_contract not in allowed_contracts
                ):
                    continue
                param = payload.get("matrix_param") or payload.get("param")
                value = payload.get("matrix_value", payload.get("value_json"))
                attributable = bool(
                    payload.get("attributable")
                    and param is not None
                    and value is not None
                )
                if attributable:
                    out["fleet_exact_attributable"] += 1
                out["fleet_exact"].append({
                    "path_id": row["path_id"], "key": key,
                    "stage": row["stage"], "status": row["status"],
                    "strategy_return_pct": row["strategy_return_pct"],
                    "bh_return_pct": row["bh_return_pct"],
                    "same_entry_control_return_pct": row["same_entry_control_return_pct"],
                    "matrix_param": param, "matrix_value": value,
                    "attributable": attributable, "artifact": row["artifact"],
                    "created_at": row["created_at"],
                })
            fleet.close()
        except sqlite3.OperationalError:
            pass
    return out


def include_observed_rows(rows, cells, tier):
    """Add measured values that are absent from the current manifest.

    A current-contract ENGINE result is exact evidence and must remain visible
    even when it belongs to a generated campaign pack such as ``STOP_PACK``
    rather than a standalone config knob.  VEC-only unknowns remain excluded
    because they are diagnostic and are not permission to expand the
    actionable inventory.
    """
    out = list(rows)
    known = set(out)
    actionable_names = {param for param, _value in out}
    out += sorted(
        {
            (param, value)
            for (param, value, _key) in cells
            if (param, value) not in known
            and (tier == "ENGINE" or param in actionable_names)
        }
    )
    return out


_REG = None


def registry():
    """tools/knob_registry.py output: {mode: {knob: {family, group, role, ...}}}"""
    global _REG
    if _REG is None:
        p = BASE / "data" / "knob_registry.json"
        if not p.exists():
            raise RuntimeError(
                f"required knob registry missing: {p}; refusing to silently destroy family/role grouping"
            )
        _REG = json.loads(p.read_text()).get("tradier", {})
    return _REG


_VERIFIED_DESCRIPTIONS = {
    "WT_3M_FORCE_OPEN_ENABLED": (
        "ENTRY — on stocks, opens/adds when price is on the correct 200-SMA/EMA side and "
        "5m WT is favorable; generally more fills and time-in-market; off=False; per-symbol."
    ),
    "WT_3M_FORCE_OPEN_BUILD_TO_TARGET": (
        "ENTRY/SIZING — permits repeated adds until target notional is reached; generally "
        "more add events and exposure; off=False."
    ),
    "WT_3M_FORCE_OPEN_BYPASS_GATES": (
        "ENTRY BYPASS — skips several execution/risk gates; generally more actual fills; "
        "off=False; currently global-only despite the per-symbol master."
    ),
    "WT_3M_FORCE_OPEN_DIST_PCT": (
        "ENTRY FILTER — minimum distance beyond the 200-SMA required by WT force-open; "
        "higher values generally mean fewer qualifying opens."
    ),
    "MTF_DC_REJECT_EXIT_ENABLED": (
        "EXIT — after a Donchian breakout, closes on rejection back inside the selected band; "
        "generally more closes/lower exposure; off=False; requires MTF_EXIT_USE_COMPOUND=True."
    ),
    "MTF_DC_REJECT_EXIT_LOOKBACK": (
        "EXIT TIMING — maximum Donchian rejection window in bars after price "
        "moves outside the selected band; live stocks read it in "
        "TradierManage.evaluate_stop and exact c5 restores it to 5 when the "
        "isolated MTF DC family is tested. Higher values keep the rejection "
        "setup armed longer; requires MTF_EXIT_USE_COMPOUND=True."
    ),
    "MTF_BB_REJECT_EXIT_LOOKBACK": (
        "EXIT TIMING — number of bars retained for the Bollinger tag/fail "
        "rejection window; live stocks read it in TradierManage.evaluate_stop "
        "and exact c5 restores it to 5 for isolated MTF BB tests. Higher "
        "values allow a later rejection; requires MTF_EXIT_USE_COMPOUND=True."
    ),
    "DC_LOW4_STOP_ENABLED": (
        "ENTRY-QUALITY FAILURE DIAGNOSTIC / LOSING-CHURN EXIT EVIDENCE — closes a LONG below "
        "dc_low4_5m (SHORT above dc_high4_5m). This is a tight loss/emergency exit used to "
        "expose entries that fail almost immediately; it is NOT a recommended top or "
        "profit-taking exit. Below-B&H measurements remain gray discard evidence; off=False."
    ),
    "R1_DC_LOW4_3M_EMERGENCY_ENABLED": (
        "ENTRY-QUALITY FAILURE DIAGNOSTIC / LOSING-CHURN EXIT EVIDENCE — despite the legacy "
        "'3M' config name, stocks use frozen dc_low4_5m/dc_high4_5m. A breach closes the failed "
        "entry at a loss; it is NOT a top/profit-taking path. Use its results to diagnose entry "
        "quality and churn, never to recommend profit capture; off=False; per-symbol."
    ),
    "PARTIAL_PROFIT_LOCK_ENABLED": (
        "EXIT — reduces part of a winner, installs a breakeven-buffer stop, then may close the "
        "remainder; more order events, but completed-trade/exposure effect is empirical; off=False."
    ),
    "STRUCTURAL_RANGE_SHIFT_EXIT": (
        "EXIT — stock LONG entered above the selected upper BB/DC boundary closes on a retest "
        "near that boundary when 1h+15m stochastic are overbought and 5m stochastic turns down; "
        "SHORT is the true mirror (entry below lower boundary, oversold, 5m turns up). Generally "
        "more closes/lower exposure; off=False; currently global-only. Exact stocks use the real "
        "Tradier evaluator as sole decision owner; vector stock parity repaired 2026-07-29."
    ),
    "LONG_STRUCT_EXIT_TF": (
        "CURRENT DIRECT EXIT — selects the timeframe whose lower-high+lower-low break closes a "
        "LONG immediately. The armed structural-break → WT1 rebound-top → subsequent lower-price "
        "sequence is a VEC-REJECTED BASELINE (structural_wt_rebound_20260726T062654Z; median "
        "alpha -4.70pp, 0/6 valid folds beat B&H), with NO Tier-2 result. Keep it out of matrix "
        "promotion. A frozen profit-gated grid made 9/9 exits winners but beat B&H in only 1/4 "
        "validation folds (median alpha -1.04pp) because delayed E10 reclaim chased price. "
        "Resting-reclaim execution remains research-only and the reentry obligation must remain "
        "latched until reopened. A MU structural probe made 2.0006x B&H but is rejected because "
        "the comparable frozen ladder + 4h N30 E02 control made 6.4117x B&H. off='None'; "
        "per-symbol."
    ),
    "SHORT_STRUCT_EXIT_TF": (
        "CURRENT DIRECT EXIT — selects the timeframe whose higher-high+higher-low break closes a "
        "SHORT immediately. The mirrored armed-break → WT1 bottom → subsequent higher-price "
        "sequence has NO Tier-2 result; the tested LONG baseline was VEC-REJECTED "
        "(structural_wt_rebound_20260726T062654Z). Do not infer SHORT performance from LONG; "
        "profit/MFE-gated variants remain research-only and reentry must stay latched until "
        "reopened. off='None'; per-symbol."
    ),
    "GOLDEN_RULE_REQUIRE_ACTIVATION": (
        "ENTRY CONDITION — requires the Golden Rule activation state before the qualifying entry; "
        "False loosens that requirement and may permit more entries; signal-dependent; Tier-2 "
        "fingerprint currently unchanged between True/False, so reconnect before promotion."
    ),
    "MTF_ARMED_ENTRY_ENABLED": (
        "ENTRY — permits entries only after the multi-timeframe armed state is established; "
        "generally increases qualifying entries when enabled; off=False; requires MTF arm state."
    ),
    "BB_PULLBACK_GATE_ENABLED": (
        "ENTRY FILTER — requires a Bollinger pullback condition before entry; enabling can reduce "
        "entries, while disabling admits more signals; off=False; signal-dependent."
    ),
}

_DC_LOW4_DIAGNOSTIC_PARAMS = {
    "DC_LOW4_STOP_ENABLED",
    "R1_DC_LOW4_3M_EMERGENCY_ENABLED",
}


def describe_knob(param):
    """Conservative human description; numeric polarity is never guessed."""
    if param in _VERIFIED_DESCRIPTIONS:
        return _VERIFIED_DESCRIPTIONS[param]
    # Glossary expansions for mouse-over tooltips
    if param.startswith("CT_CHOP"):
        return (
            "CT = Counter Trend — Chop filter (4H) — detects sideways chop (ADX_4H <20, BB width <2%, volume flat). "
            "When enabled, BLOCKS new entries in choppy regimes to avoid whipsaw; when disabled, allows choppy entries "
            "(more trades, more noise). Live: tradier_manage.py: evaluate_entry() → _is_chop_4h() checks ADX_4H + bb_width_4h; "
            "if chop true, gate returns BLOCK. No live trade if blocked."
        )
    if param.startswith("CT_"):
        return (
            "CT = Counter Trend — Counter-trend entry family. Gating params like CT_15M_MOMENTUM_GATE_ENABLED "
            "require bullish 15m momentum when entering long. Live: tradier_manage.py per-bar gate; Vector: v8_quick_engine.py "
            "checks wt_momentum_state_15m etc. When enabled, filters entries."
        )
    if param.startswith("ABLATION_DISABLE"):
        family = param.replace("ABLATION_DISABLE_", "")
        return (
            f"Ablation = ablation study — disables a subsystem to measure its P&L contribution; when enabled, that family ({family}) "
            f"is turned off to see if it was helping or hurting. Live: _apply_research_only_live_gates() blocks that family's entries. "
            f"Vector: v8_quick_engine _apply_auto_wired_params hash-flips 14% bars. Enable to measure contribution."
        )
    if param.startswith("CLENOW"):
        return (
            "Clenow = Andreas Clenow momentum/trend filter (stocks, 12mo momentum + volatility) — trend-following, 12-mo momentum + volatility stop; "
            "True = require Clenow trend (price above SMA200 and momentum score above threshold), False = ignore. "
            "Live: tradier_manage.py _apply_research_only_live_gates checks price vs SMA200 and clenow_score vs CLENOW_GATE_MIN_SCORE. "
            "Vector: v8_quick_engine checks close vs sma200_D and rsi_1h vs threshold."
        )
    if param.startswith("FORMATION_"):
        labels = {
            "HEAD_SHOULDERS": "head-and-shoulders / inverse head-and-shoulders",
            "DOUBLE_TOP_BOTTOM": "double-top / double-bottom",
            "WEDGE": "rising / falling wedge",
            "TRIANGLE": "ascending / descending triangle",
            "FLAG_PENNANT": "bull / bear flag or pennant",
            "CUP_HANDLE": "cup-and-handle / inverse cup-and-handle",
            "TREND_STRUCTURE": "higher-high+higher-low / lower-high+lower-low structure",
        }
        if param == "FORMATION_TFS":
            return "ENTRY/EXIT TF — comma-separated completed-kline timeframes evaluated by every classic-formation path."
        if param == "FORMATION_MIN_SCORE":
            return "ENTRY/EXIT FILTER — minimum normalized causal formation confidence; higher values produce fewer signals."
        if param == "FORMATION_POSITION_SIZE_MULT":
            return "ENTRY SIZING — multiplier applied only to formation-path opening size; does not change detection."
        if param == "FORMATION_EXIT_MIN_GAIN_PCT":
            return "EXIT FILTER — minimum position gain required before an enabled opposing formation may close the position."
        for token, label in labels.items():
            if token in param:
                if param.endswith("_ENTRY_ENABLED"):
                    return (
                        f"ENTRY — opens LONG on bullish {label} confirmation and SHORT on bearish confirmation; "
                        "causal vector/NPZ/live-kline path; off=False; per-symbol."
                    )
                if param.endswith("_EXIT_ENABLED"):
                    return (
                        f"EXIT — closes LONG on bearish {label} confirmation and SHORT on bullish confirmation; "
                        "causal vector/NPZ/live-kline path; off=False; per-symbol."
                    )
    info = registry().get(param, {})
    group = str(info.get("group") or "OTHER").upper()
    role = str(info.get("role") or "SETTING").replace("_", " ").lower()
    scope = "per-symbol" if info.get("per_sym") else "global-only"
    off = info.get("off_value")
    words = param.replace("_", " ").lower()
    if "BYPASS" in param:
        effect = "bypasses a condition; enabling usually permits more actions"
    elif "COOLDOWN" in param or "MIN_HOLD" in param:
        effect = "timing constraint; increasing it usually reduces action frequency"
    elif group == "ENTRY" and info.get("kind") == "bool":
        effect = "entry path/filter; enabling a path usually increases opens, while enabling a filter can reduce them"
    elif group == "EXIT" and info.get("kind") == "bool":
        effect = "exit path/filter; enabling a path usually increases closes and lowers exposure"
    elif group == "SIZING" or any(x in param for x in ("SIZE", "MULT", "CAPITAL", "BUDGET")):
        effect = "primarily changes position quantity, not signal count"
    elif any(x in param for x in ("THRESHOLD", "_MIN", "_MAX", "LOOKBACK", "_TF")):
        effect = "selector/threshold; trade-count direction is signal-dependent and must be measured"
    else:
        effect = f"controls {words}; trade-count direction must be measured"
    off_text = f"; off={off!r}" if off is not None else ""
    return f"{group} {role.upper()} — {effect}{off_text}; {scope}."


def matrix_cell_state(param, meta):
    """Color one measured cell without losing diagnostic evidence.

    A measured result below the same-key B&H floor is always gray discard
    evidence. Positive absolute gain does not make a below-B&H stock result a
    viable/white candidate.
    """
    if meta is None:
        return None
    validation_ok = (
        meta.get("validation_status") == "PASS"
        or bool(meta.get("expected_capacity_saturation"))
    )
    bh_delta = meta.get("bh_delta")
    trades = meta.get("trades")
    if (
        (trades is not None and trades < 1)
        or meta.get("inert")
        or meta.get("same_value_fingerprint")
        or not validation_ok
        or (meta.get("real_closes") or 0) < 1
        or (meta.get("reentry_violations") or 0) > 0
    ):
        return "red"
    if bh_delta is None:
        return "white"
    if abs(float(bh_delta)) <= 0.00005:
        return "red"
    if float(bh_delta) > 0:
        return "green"
    return "gray"


def write_vector_scalar_diagnostics_sheet(wb, payload):
    """Append the isolated amber/italic VEC evidence sheet."""
    from openpyxl.styles import Font, PatternFill

    ws = wb.create_sheet("VEC Scalar Diagnostics")
    warning = (
        "VEC_DIAGNOSTIC ONLY — amber/italic; exact completion credit=false; "
        "ENGINE ranking=false; promotion=false. MOVED means exact replay "
        "priority, never an exact result."
    )
    ws.append([warning])
    headers = [
        "key", "status", "param", "value", "gain/mo diagnostic",
        "delta vs B&H diagnostic", "trades", "single-key trade Sharpe",
        "max DD %", "evidence class", "vector adapter", "source group",
        "action group", "approx confidence", "mismatch class",
        "source rank", "proxy field", "proxy value", "exact replay priority",
        "generated UTC", "tier",
        "exact_completion_credit", "engine_ranking_allowed",
        "db_engine_write_allowed", "live_config_write_allowed",
        "promotion_allowed",
    ]
    ws.append(headers)
    if not payload or not payload.get("available"):
        ws.append([
            "UNAVAILABLE: " + str((payload or {}).get("reason") or "no receipt")
        ])
    else:
        for item in payload.get("rows") or []:
            ws.append([
                item.get("key"), item.get("status"), item.get("param"),
                item.get("value_json"),
                item.get("gain_per_mo_diagnostic"),
                item.get("delta_gain_mo_vs_bh_diagnostic"),
                item.get("trades"),
                item.get("single_key_trade_sharpe_diagnostic"),
                item.get("max_dd_pct"),
                item.get("vector_evidence_class"),
                item.get("vector_adapter"),
                item.get("source_group"), item.get("action_group"),
                item.get("approximation_confidence"),
                item.get("approximation_mismatch_class"),
                item.get("source_rank"), item.get("proxy_field"),
                item.get("proxy_value"),
                item.get("status") == "MOVED",
                item.get("generated_at"), item.get("tier"),
                item.get("exact_completion_credit"),
                item.get("engine_ranking_allowed"),
                item.get("db_engine_write_allowed"),
                item.get("live_config_write_allowed"),
                item.get("promotion_allowed"),
            ])
    amber_font = Font(color="9A6B00", italic=True)
    amber_fill = PatternFill("solid", fgColor="FFF2CC")
    for row_cells in ws.iter_rows():
        for cell in row_cells:
            cell.font = amber_font
            cell.fill = amber_fill
    ws.freeze_panes = "A3"
    from openpyxl.utils import get_column_letter
    final_column = get_column_letter(len(headers))
    ws.auto_filter.ref = f"A2:{final_column}{max(ws.max_row, 2)}"
    return ws


def apply_vector_approx_overlay(cell, item):
    """Render one non-authoritative VEC_APPROX metric into a blank XLS cell."""
    if (
        cell.value is not None
        or not item
        or item.get("vector_evidence_class") != "VEC_APPROX"
        or (
            item.get("delta_gain_mo_vs_bh_diagnostic") is None
            and item.get("status") != "DATA_UNAVAILABLE"
        )
        or item.get("exact_completion_credit") is not False
        or item.get("engine_ranking_allowed") is not False
        or item.get("promotion_allowed") is not False
        or item.get("live_config_write_allowed") is not False
        or item.get("db_engine_write_allowed") is not False
    ):
        return False
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill

    # USER/BIBLE invariant: an amber result is still a result.  It must not
    # bypass the same uniqueness/activity checks as an exact cell.  The old
    # exporter painted hundreds of unrelated cells with one baseline/proxy
    # number, even when their action fingerprints were identical.  Preserve
    # the audit on the blank cell, but never display the repeated number as a
    # completed matrix value.
    uniqueness = str(item.get("result_uniqueness_status") or "")
    if item.get("status") != "DATA_UNAVAILABLE" and uniqueness != "PASS":
        cell.value = None
        cell.fill = PatternFill("solid", fgColor="FFC7CE")
        cell.font = Font(color="9C0006")
        cell.comment = Comment(
            "\n".join(
                (
                    "VECTOR RESULT QUARANTINED — underlying ENGINE cell remains blank.",
                    f"uniqueness status: {uniqueness or 'NOT_AUDITED'}",
                    f"quarantine reasons: {','.join(item.get('result_uniqueness_reasons') or [])}",
                    f"collision count: {int(item.get('result_collision_count') or 0)}",
                    "A numeric cell requires MOVED, nonzero, real close/activity telemetry, "
                    "and a metric plus action fingerprint unique within this symbol/side.",
                )
            ),
            "Codex vector uniqueness guard",
        )
        return True

    cell.value = (
        "DATA_UNAVAILABLE"
        if item.get("status") == "DATA_UNAVAILABLE"
        else float(item["delta_gain_mo_vs_bh_diagnostic"])
    )
    cell.fill = PatternFill("solid", fgColor="FFF2CC")
    cell.font = Font(color="9A6B00", italic=True)
    cell.comment = Comment(
        "\n".join(
            (
                "VEC_APPROX diagnostic overlay — the underlying ENGINE "
                "cell is blank.",
                "Exact completion credit: false",
                "ENGINE ranking allowed: false",
                "DB ENGINE write allowed: false",
                "Live config write allowed: false",
                "Promotion allowed: false",
                "Required next stage: EXACT_V8",
                f"status: {item.get('status')}",
                f"exact replay priority: {item.get('status') == 'MOVED'}",
                f"confidence: {item.get('approximation_confidence')}",
                f"mismatch: {item.get('approximation_mismatch_class')}",
                f"source rank: {item.get('source_rank')}",
                f"proxy field: {item.get('proxy_field')}",
                f"proxy value: {item.get('proxy_value')}",
                f"result uniqueness: {item.get('result_uniqueness_status')}",
                "action fingerprint: "
                f"{item.get('action_fingerprint') or item.get('behavior_fingerprint') or item.get('trades_fingerprint') or item.get('ledger_sha256')}",
            )
        ),
        "Codex VEC_APPROX audit",
    )
    return True


def enforce_display_unique_nonzero(cell, identity, seen_values, *, invalid_fill=None):
    """Fail closed on a zero, non-finite, or per-symbol repeated result.

    Never perturb a value to manufacture uniqueness. Raw evidence remains in
    its receipt/provenance surface; the decision cell becomes unresolved.
    """
    import math
    from openpyxl.comments import Comment

    value = cell.value
    reason = None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        reason = "NON_NUMERIC"
    elif not math.isfinite(float(value)):
        reason = "NON_FINITE"
    elif float(value) == 0.0:
        reason = "PROHIBITED_ZERO"
    elif float(value) in seen_values:
        reason = f"PROHIBITED_DUPLICATE_OF:{seen_values[float(value)]}"
    if reason is None:
        seen_values[float(value)] = identity
        return True, None
    cell.value = None
    if invalid_fill is not None:
        cell.fill = invalid_fill
    cell.comment = Comment(
        "Result withheld from the decision surface. " + reason
        + ". Re-test this exact physical cell; do not add epsilon or copy a baseline.",
        "Matrix integrity gate",
    )
    return False, reason


def load_recovered_pilot_v8_cells(base):
    """Load hash-bound V8 rows and overlay completed native-precision reruns.

    The immutable DB archive contains the historical evidence exactly as it
    was stored (including the old four-decimal persistence loss).  Completed
    no-DB C5 reruns may replace only their matching logical cell in memory.
    The combined surface is then re-audited from scratch for nonzero numeric
    uniqueness within each symbol/side; no epsilon or synthetic value is ever
    introduced.
    """
    from collections import Counter
    import hashlib
    import math

    archive = base / "data/reports/PILOT_V8_CELLS_IMMUTABLE.jsonl"
    receipt_path = base / "data/reports/PILOT_V8_CELLS_IMMUTABLE_RECEIPT.json"
    if not archive.is_file() or not receipt_path.is_file():
        return {}, {"available": False, "reason": "full-precision pilot recovery missing"}
    receipt = json.loads(receipt_path.read_text())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if receipt.get("status") != "PASS" or digest != receipt.get("archive_sha256"):
        raise RuntimeError("PILOT_V8_CELLS_IMMUTABLE hash/receipt mismatch")
    index = {}
    with archive.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if (
                item.get("evidence_class") not in {
                    "RECOVERED_HISTORICAL_V8_DB_ROW",
                    # Backward-compatible read of the pre-audit archive label.
                    "RECOVERED_FULL_PRECISION_V8_DB_ROW",
                }
                or item.get("immutable_preservation") is not True
                or item.get("promotion_allowed") is not False
            ):
                raise RuntimeError("invalid full-precision pilot V8 row")
            logical = (
                str(item.get("param") or ""),
                norm_val(item.get("value_json")),
                str(item.get("key") or ""),
            )
            if logical in index:
                raise RuntimeError(
                    "PILOT_V8_DUPLICATE_LOGICAL_IDENTITY_IN_SELECTED_ARCHIVE:"
                    f"{logical}"
                )
            index[logical] = item

    rerun_status_path = base / "data/reports/PILOT_V8_FULL_PRECISION_RERUN_STATUS.json"
    rerun_results_path = base / "data/reports/PILOT_V8_FULL_PRECISION_RERUN_RESULTS.jsonl"
    rerun_audit = {
        "available": False,
        "accepted_pass_rows": 0,
        "reason": "completed native-precision rerun receipt missing",
    }
    if rerun_status_path.is_file() and rerun_results_path.is_file():
        rerun_status = json.loads(rerun_status_path.read_text())
        rerun_audit = {
            "available": True,
            "status": rerun_status.get("status"),
            "deployment_sha256": rerun_status.get("deployment_sha256"),
            "accepted_pass_rows": 0,
            "ledger_rows_preserved": 0,
            "pass_ledger_rows": 0,
            "out_of_selection_pass_rows": 0,
            "non_authoritative_deployment_rows": 0,
            "verified_cross_deployment_pass_rows": 0,
            "duplicate_authoritative_logical_pass_rows": 0,
            "results_sha256": hashlib.sha256(
                rerun_results_path.read_bytes()
            ).hexdigest(),
        }
        if rerun_status.get("source_archive_sha256") != digest:
            raise RuntimeError("full-precision rerun/archive hash mismatch")
        if rerun_status.get("status") in {"PASS", "FAIL_PARTIAL"}:
            selected_cells = {}
            selected_contracts = {}
            for selected in rerun_status.get("selection") or []:
                logical = (
                    str(selected.get("param") or ""),
                    norm_val(selected.get("value_json")),
                    str(selected.get("key") or ""),
                )
                selected_cells[logical] = str(
                    selected.get("logical_run_identity") or ""
                )
                selected_contracts[logical] = str(
                    selected.get("target_contract_fingerprint") or ""
                )
            passed_by_logical = {}
            for line in rerun_results_path.read_text().splitlines():
                if not line.strip():
                    continue
                rerun = json.loads(line)
                rerun_audit["ledger_rows_preserved"] += 1
                if rerun.get("status") == "PASS":
                    rerun_audit["pass_ledger_rows"] += 1
                status_deployment = str(
                    rerun_status.get("deployment_sha256") or ""
                )
                result_deployment = str(
                    rerun.get("deployment_sha256") or ""
                )
                if rerun.get("status") != "PASS":
                    continue
                logical = (
                    str(rerun.get("param") or ""),
                    norm_val(rerun.get("value_json")),
                    str(rerun.get("key") or ""),
                )
                if selected_cells and logical not in selected_cells:
                    rerun_audit["out_of_selection_pass_rows"] += 1
                    continue
                target_contract = selected_contracts.get(logical) or ""
                if (
                    target_contract
                    and str(rerun.get("new_contract_fingerprint") or "")
                    != target_contract
                ):
                    rerun_audit["out_of_selection_pass_rows"] += 1
                    continue
                if logical not in index:
                    raise RuntimeError(
                        f"full-precision rerun logical cell absent from archive: {logical}"
                    )
                selected_identity = selected_cells.get(logical) or ""
                result_identity = str(
                    rerun.get("logical_run_identity")
                    or rerun.get("run_identity")
                    or ""
                )
                # Runner/reporting deployments may change without changing
                # the exact engine contract.  Preserve an earlier PASS only
                # when both its stable logical identity and its actual engine
                # fingerprint match the current hash-bound selection.  Old
                # deployment-only receipts lacking either binding remain
                # non-authoritative.
                if (
                    status_deployment
                    and result_deployment != status_deployment
                ):
                    if (
                        not selected_identity
                        or not target_contract
                        or result_identity != selected_identity
                        or str(rerun.get("new_contract_fingerprint") or "")
                        != target_contract
                    ):
                        rerun_audit[
                            "non_authoritative_deployment_rows"
                        ] += 1
                        continue
                    rerun_audit[
                        "verified_cross_deployment_pass_rows"
                    ] += 1
                # V2 receipts bind the selected setting directly.  Legacy V1
                # receipts used deployment-scoped run_identity, so their exact
                # logical cell tuple is the compatibility binding.
                if (
                    selected_identity
                    and rerun.get("logical_run_identity")
                    and result_identity != selected_identity
                ):
                    raise RuntimeError(
                        "full-precision rerun logical identity mismatch"
                    )
                identity = selected_identity or repr(logical)
                prior = passed_by_logical.get(identity)
                if prior is not None:
                    rerun_audit[
                        "duplicate_authoritative_logical_pass_rows"
                    ] += 1
                    prior_row = prior[1]
                    comparable = (
                        "delta_gain_mo_vs_bh_full_precision",
                        "gain_per_mo_full_precision",
                        "acc_gain_pct_full_precision",
                        "new_trades_fingerprint",
                        "new_contract_fingerprint",
                        "execution_identity",
                    )
                    if any(
                        prior_row.get(field) != rerun.get(field)
                        for field in comparable
                    ):
                        raise RuntimeError(
                            "PILOT_V8_CONFLICTING_AUTHORITATIVE_RERUN_RESULTS:"
                            f"{logical}"
                        )
                    continue
                passed_by_logical[identity] = (logical, rerun)

            for logical, rerun in passed_by_logical.values():
                item = dict(index[logical])
                item.update(
                    {
                        "delta_gain_mo_vs_bh": rerun.get(
                            "delta_gain_mo_vs_bh_full_precision"
                        ),
                        "gain_per_mo": rerun.get("gain_per_mo_full_precision"),
                        "acc_gain_pct": rerun.get("acc_gain_pct_full_precision"),
                        "trades": rerun.get("trades"),
                        "time_in_mkt_pct": rerun.get("time_in_mkt_pct"),
                        "trades_fingerprint": rerun.get(
                            "new_trades_fingerprint"
                        ),
                        "contract_fingerprint": rerun.get(
                            "new_contract_fingerprint"
                        ),
                        "native_precision_rerun": True,
                        "native_precision_run_identity": (
                            rerun.get("logical_run_identity")
                            or rerun.get("run_identity")
                        ),
                        "native_precision_execution_identity": rerun.get(
                            "execution_identity"
                        ),
                        "native_precision_deployment_sha256": rerun.get(
                            "deployment_sha256"
                        ),
                    }
                )
                index[logical] = item
                rerun_audit["accepted_pass_rows"] += 1

    numeric_counts = Counter()
    for item in index.values():
        try:
            number = float(item.get("delta_gain_mo_vs_bh"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number != 0.0:
            numeric_counts[(str(item.get("key") or ""), number)] += 1
    display_counts = Counter()
    for item in index.values():
        try:
            number = float(item.get("delta_gain_mo_vs_bh"))
        except (TypeError, ValueError):
            number = float("nan")
        collision_count = (
            numeric_counts[(str(item.get("key") or ""), number)]
            if math.isfinite(number) and number != 0.0
            else 0
        )
        if not math.isfinite(number):
            status = "REJECT_NONFINITE"
        elif number == 0.0:
            status = "REJECT_ZERO"
        elif collision_count != 1:
            status = "REJECT_DUPLICATE_WITHIN_SYMBOL_SIDE"
        else:
            status = "PASS_UNIQUE_NONZERO"
        item["physical_display_status"] = status
        item["result_collision_count_within_symbol_side"] = collision_count
        display_counts[status] += 1

    return index, {
        **receipt,
        "available": True,
        "verified_archive_sha256": digest,
        "native_precision_reruns": rerun_audit,
        "combined_display_reaudit": dict(sorted(display_counts.items())),
    }


def apply_recovered_pilot_v8_overlay(cell, item):
    """Render only re-audited, per-key unique/nonzero recovered V8 rows."""
    if cell.value is not None or not item:
        return False
    if item.get("physical_display_status") != "PASS_UNIQUE_NONZERO":
        return False
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill

    try:
        value = float(item["delta_gain_mo_vs_bh"])
    except (KeyError, TypeError, ValueError):
        return False
    cell.value = value
    cell.fill = PatternFill("solid", fgColor="BDD7EE")
    cell.font = Font(color="1F4E78", bold=True)
    evidence_label = (
        "NATIVE-PRECISION C5 RERUN of immutable S1 V8 evidence."
        if item.get("native_precision_rerun")
        else "IMMUTABLE S1 V8 DB RESULT (historical stored precision)."
    )
    cell.comment = Comment(
        "\n".join((
            evidence_label,
            f"campaign: {item.get('campaign')}",
            f"timestamp: {item.get('ts')}",
            f"source: {item.get('source_file')}",
            f"trades fingerprint: {item.get('trades_fingerprint')}",
            f"gain/month: {item.get('gain_per_mo')}",
            f"delta gain/month vs B&H: {item.get('delta_gain_mo_vs_bh')}",
            f"native-precision rerun: {bool(item.get('native_precision_rerun'))}",
            "Precedence: current c5 ENGINE > this recovered V8 > preserved workbook > vector.",
            "Vector output is prohibited from overwriting this cell.",
        )),
        "Immutable S1 V8 recovery",
    )
    return True


def load_preserved_v8_cells(base):
    """Load the source-hashed immutable V8 workbook recovery archive.

    These rows are exact historical V8 observations recovered from the old
    PARAM_BASELINE workbook.  They are display/provenance evidence only until
    their original receipt is recovered or they are replayed.  They always
    outrank vector evidence and can never replace a current receipt-valid
    ENGINE cell.
    """
    import hashlib

    archive = base / "data/reports/PRESERVED_V8_CELLS.jsonl"
    receipt_path = base / "data/reports/PRESERVED_V8_CELLS_RECEIPT.json"
    if not archive.exists() or not receipt_path.exists():
        return {}, {"available": False, "reason": "preserved archive missing"}
    receipt = json.loads(receipt_path.read_text())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != str(receipt.get("archive_sha256") or ""):
        raise RuntimeError("PRESERVED_V8_CELLS archive hash does not match receipt")
    import math
    from collections import defaultdict

    index = {}
    with archive.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if (
                item.get("evidence_class") != "PRESERVED_V8_WORKBOOK_ROW"
                or item.get("immutable_preservation") is not True
                or item.get("promotion_allowed") is not False
            ):
                raise RuntimeError("invalid row in PRESERVED_V8_CELLS archive")
            logical = (
                str(item.get("param") or ""),
                norm_val(item.get("value_json")),
                str(item.get("key") or ""),
            )
            index[logical] = dict(item)

    # The immutable archive was originally classified with a workbook-global
    # numeric uniqueness rule.  Re-audit its values in memory under the user's
    # corrected scope: one symbol/side across Entry/Exit/Sizing/Other.  The raw
    # JSONL and hash remain untouched.
    metric_groups = defaultdict(list)
    for logical, item in index.items():
        try:
            value = float(item.get("delta_gain_mo_vs_bh"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value != 0.0:
            metric_groups[(logical[2], value)].append(logical)
    status_counts = defaultdict(int)
    for logical, item in index.items():
        item["preservation_original_display_status"] = item.get(
            "preservation_display_status"
        )
        reasons = []
        try:
            value = float(item.get("delta_gain_mo_vs_bh"))
        except (TypeError, ValueError):
            value = float("nan")
        if not math.isfinite(value):
            reasons.append("NON_FINITE_RESULT")
        elif value == 0.0:
            reasons.append("PROHIBITED_ZERO")
        elif len(metric_groups[(logical[2], value)]) != 1:
            reasons.append("DUPLICATE_RESULT_WITHIN_SYMBOL_SIDE")
        item["preservation_display_status"] = "PASS" if not reasons else "QUARANTINED"
        item["preservation_quarantine_reasons"] = reasons
        item["result_collision_count"] = (
            len(metric_groups.get((logical[2], value), []))
            if math.isfinite(value) and value != 0.0 else 0
        )
        status_counts[item["preservation_display_status"]] += 1
    return index, {
        **receipt,
        "available": True,
        "verified_archive_sha256": digest,
        "reaudited_uniqueness_scope": "WITHIN_SYMBOL_SIDE_ACROSS_FOUR_PATH_SHEETS",
        "reaudited_status_counts": dict(status_counts),
        "raw_archive_modified": False,
    }


def apply_preserved_v8_overlay(cell, item):
    """Render immutable historical V8 evidence without granting promotion."""
    if cell.value is not None or not item:
        return False
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill

    try:
        value = float(item["delta_gain_mo_vs_bh"])
    except (KeyError, TypeError, ValueError):
        return False
    quarantined = item.get("preservation_display_status") != "PASS"
    # Quarantined history belongs in Evidence Provenance, not in the numeric
    # decision surface. Returning False lets behavior-unique vector evidence
    # compete; otherwise the physical cell stays visibly unresolved.
    if quarantined:
        return False
    # Raw historical numbers remain immutable in Evidence Provenance/AllCells,
    # but a duplicate must not masquerade as a completed numeric matrix cell.
    cell.value = value
    cell.fill = PatternFill("solid", fgColor="D9EAF7")
    cell.font = Font(color="1F4E78")
    reasons = ",".join(item.get("preservation_quarantine_reasons") or [])
    cell.comment = Comment(
        "\n".join(
            (
                "IMMUTABLE HISTORICAL V8 RESULT recovered from PARAM_BASELINE_STOCKS.",
                f"campaign: {item.get('campaign')}",
                f"timestamp: {item.get('ts')}",
                f"source: {item.get('source_file')}",
                f"gain/month: {item.get('gain_per_mo')}",
                f"delta gain/month vs B&H: {item.get('delta_gain_mo_vs_bh')}",
                f"display status: {item.get('preservation_display_status')}",
                f"quarantine reasons: {reasons or 'none'}",
                f"numeric collision count: {int(item.get('result_collision_count') or 0)}",
                "Precedence: current receipt-valid ENGINE > this preserved V8 > vector.",
                "This value cannot be overwritten by vector output and cannot promote live "
                "until receipt recovery or exact replay validates the current contract.",
            )
        ),
        "Codex immutable V8 recovery",
    )
    return True


def audit_vector_overlay_uniqueness(index):
    """Annotate amber rows and fail closed on broadcast/proxy collisions.

    The comparison scope is one symbol/side because B&H and strategy returns
    are key-specific.  Both the published number and behavior/action schedule
    must be unique.  This deliberately implements the user's strict original
    contract: two equal result numbers are not two filled cells.
    """
    import copy
    import math
    from collections import defaultdict

    audited = {logical: copy.deepcopy(item) for logical, item in index.items()}
    metric_groups = defaultdict(list)
    fingerprint_groups = defaultdict(list)
    param_logicals = defaultdict(list)
    metric_by_logical = {}
    fingerprint_by_logical = {}
    for logical, item in audited.items():
        key = str(logical[2])
        param_logicals[(key, str(logical[0]))].append(logical)
        metric = item.get("delta_gain_mo_vs_bh_diagnostic")
        try:
            metric_number = float(metric)
        except (TypeError, ValueError):
            metric_number = float("nan")
        if math.isfinite(metric_number):
            # Matrix values are exported to useful precision.  Near-identical
            # floats must not evade the collision guard through machine noise.
            metric_groups[(key, round(metric_number, 10))].append(logical)
            metric_by_logical[logical] = round(metric_number, 10)
        fingerprint = (
            item.get("action_fingerprint")
            or item.get("behavior_fingerprint")
            or item.get("trades_fingerprint")
            or item.get("ledger_sha256")
        )
        if fingerprint:
            fingerprint_groups[(key, str(fingerprint))].append(logical)
            fingerprint_by_logical[logical] = str(fingerprint)

    # Strict user contract: no two displayed numeric vector cells for one
    # symbol/side may share either the published result or the action ledger.
    # The sole exception requested on 2026-08-01 is one bounded two-value
    # plateau inside a >=5-value axis (for example 0 and 20 match while
    # 40/60/80 are all behavior-unique). A second plateau, a three-value
    # plateau, a short axis, or a cross-parameter collision still fails.
    allowed_metric_plateaus = set()
    allowed_fingerprint_plateaus = set()
    allowed_plateaus = []
    for (key, param), axis in sorted(param_logicals.items()):
        measured = [
            logical for logical in axis
            if logical in metric_by_logical and logical in fingerprint_by_logical
        ]
        if len(measured) < 5:
            continue
        axis_metrics = defaultdict(list)
        axis_fingerprints = defaultdict(list)
        for logical in measured:
            axis_metrics[metric_by_logical[logical]].append(logical)
            axis_fingerprints[fingerprint_by_logical[logical]].append(logical)
        metric_dups = [group for group in axis_metrics.values() if len(group) > 1]
        fingerprint_dups = [
            group for group in axis_fingerprints.values() if len(group) > 1
        ]
        if (
            len(metric_dups) != 1
            or len(fingerprint_dups) != 1
            or len(metric_dups[0]) != 2
            or len(fingerprint_dups[0]) != 2
            or set(metric_dups[0]) != set(fingerprint_dups[0])
        ):
            continue
        pair = set(metric_dups[0])
        metric = metric_by_logical[metric_dups[0][0]]
        fingerprint = fingerprint_by_logical[fingerprint_dups[0][0]]
        # A match elsewhere on the same symbol/side is not a local plateau.
        if (
            set(metric_groups[(key, metric)]) != pair
            or set(fingerprint_groups[(key, fingerprint)]) != pair
        ):
            continue
        allowed_metric_plateaus.update(pair)
        allowed_fingerprint_plateaus.update(pair)
        allowed_plateaus.append({
            "key": key,
            "param": param,
            "values": sorted(str(logical[1]) for logical in pair),
            "metric": metric,
            "behavior_fingerprint": fingerprint,
            "measured_axis_values": len(measured),
        })

    collision_metrics = {
        logical: len(group)
        for group in metric_groups.values()
        if len(group) > 1
        for logical in group
        if logical not in allowed_metric_plateaus
    }
    collision_fingerprints = {
        logical: len(group)
        for group in fingerprint_groups.values()
        if len(group) > 1
        for logical in group
        if logical not in allowed_fingerprint_plateaus
    }
    reason_counts = defaultdict(int)
    passed = 0
    for logical, item in audited.items():
        reasons = []
        status = str(item.get("status") or "")
        if status == "DATA_UNAVAILABLE":
            item["result_uniqueness_status"] = "NOT_APPLICABLE_DATA_UNAVAILABLE"
            item["result_uniqueness_reasons"] = []
            item["result_collision_count"] = 0
            continue
        if status != "MOVED":
            reasons.append(f"STATUS_{status or 'MISSING'}")
        metric = item.get("delta_gain_mo_vs_bh_diagnostic")
        try:
            metric_number = float(metric)
        except (TypeError, ValueError):
            metric_number = float("nan")
        if not math.isfinite(metric_number):
            reasons.append("MISSING_OR_NONFINITE_METRIC")
        elif abs(metric_number) <= 1e-12:
            reasons.append("ZERO_RESULT")
        if logical in collision_metrics:
            reasons.append("DUPLICATE_RESULT_NUMBER")
        fingerprint = (
            item.get("action_fingerprint")
            or item.get("behavior_fingerprint")
            or item.get("trades_fingerprint")
            or item.get("ledger_sha256")
        )
        if not fingerprint:
            reasons.append("MISSING_ACTION_FINGERPRINT")
        elif logical in collision_fingerprints:
            reasons.append("DUPLICATE_ACTION_FINGERPRINT")
        real_closes = item.get("real_closes")
        if real_closes is None:
            real_closes = item.get("close_actions")
        if real_closes is None:
            real_closes = item.get("trades")
        try:
            close_count = int(real_closes)
        except (TypeError, ValueError):
            close_count = 0
        if close_count <= 0:
            reasons.append("ZERO_REAL_CLOSES")
        reasons = list(dict.fromkeys(reasons))
        for reason in reasons:
            reason_counts[reason] += 1
        item["result_uniqueness_status"] = "PASS" if not reasons else "QUARANTINED"
        item["result_uniqueness_reasons"] = reasons
        item["result_collision_count"] = max(
            collision_metrics.get(logical, 0),
            collision_fingerprints.get(logical, 0),
        )
        passed += int(not reasons)
    report = {
        "schema_version": 1,
        "contract": "STRICT_PER_KEY_UNIQUE_METRIC_AND_ACTION_NO_DUPLICATES",
        "input_rows": len(audited),
        "passed_numeric_overlays": passed,
        "quarantined_numeric_overlays": sum(
            item.get("result_uniqueness_status") == "QUARANTINED"
            for item in audited.values()
        ),
        "data_unavailable_rows": sum(
            item.get("status") == "DATA_UNAVAILABLE"
            for item in audited.values()
        ),
        "reason_counts": dict(sorted(reason_counts.items())),
        "metric_collision_groups": sum(len(group) > 1 for group in metric_groups.values()),
        "fingerprint_collision_groups": sum(
            len(group) > 1 for group in fingerprint_groups.values()
        ),
        "allowed_isolated_metric_plateau_groups": len(allowed_plateaus),
        "allowed_isolated_fingerprint_plateau_groups": len(allowed_plateaus),
        "allowed_plateaus": allowed_plateaus,
        "allowed_plateau_rule": (
            "ONE_TWO_VALUE_PAIR_PER_5PLUS_VALUE_PARAMETER_AXIS; "
            "ALL_OTHER_VALUES_UNIQUE; NO_CROSS_PARAMETER_COLLISION"
        ),
        "matrix_numeric_fill_requires_pass": True,
        "engine_cells_modified": False,
    }
    return audited, report


def audit_post_precedence_vector_overlays(
    matrix,
    matrix_states,
    matrix_vector_overlays,
    matrix_preserved_v8_overlays,
    keys,
    matrix_recovered_pilot_v8_overlays=None,
):
    """Re-audit only vector values that can actually become numeric cells.

    The source audit may legitimately accept one two-value plateau on a
    five-value axis. Exact or preserved-V8 precedence can later hide three of
    those values, leaving only a duplicate pair in the serialized workbook.
    Re-running the same uniqueness contract on the post-precedence display set
    quarantines that pair instead of failing the entire current workbook.
    """
    display = {}
    slots = {}
    if matrix_recovered_pilot_v8_overlays is None:
        matrix_recovered_pilot_v8_overlays = [
            [None for _ in keys] for _ in matrix
        ]
    for row_index, row in enumerate(matrix):
        param = str(row[1] or row[0])
        value = norm_val(row[2])
        for key_index, key in enumerate(keys):
            item = matrix_vector_overlays[row_index][key_index]
            if (
                matrix_states[row_index][key_index] is not None
                or (
                    matrix_recovered_pilot_v8_overlays[row_index][key_index]
                    and matrix_recovered_pilot_v8_overlays[row_index][key_index].get(
                        "physical_display_status"
                    ) == "PASS_UNIQUE_NONZERO"
                )
                or (
                    matrix_preserved_v8_overlays[row_index][key_index]
                    and matrix_preserved_v8_overlays[row_index][key_index].get(
                        "preservation_display_status"
                    ) == "PASS"
                )
                or not item
                or item.get("status") == "DATA_UNAVAILABLE"
                or item.get("result_uniqueness_status") != "PASS"
            ):
                continue
            logical = (param, value, key)
            display[logical] = item
            slots[logical] = (row_index, key_index)
    audited, report = audit_vector_overlay_uniqueness(display)
    for logical, item in audited.items():
        row_index, key_index = slots[logical]
        matrix_vector_overlays[row_index][key_index] = item
    report["scope"] = "POST_EXACT_RECOVERED_AND_PRESERVED_V8_DISPLAY_PRECEDENCE"
    return report


def load_active_vector_cell_audit_overlay(base):
    """Load the largest hash-verified strict scalar audit as an amber overlay.

    The 2026-08-01 causal-recalculation lane is separate from the older
    scalar-gap coverage receipt. Without this loader the exporter silently
    ignored its audited rows and kept reporting the obsolete eight accepted
    values. Recomputing from hash-bound ledgers makes edits fail closed.
    """
    import hashlib

    reports = sorted(
        (base / "data/reports").glob("TRB_ACTIVE_VECTOR_CELL_AUDIT_*.json"),
        key=lambda path: path.name,
    )
    if not reports:
        return {}, {"available": False, "reason": "strict audit missing"}
    try:
        try:
            from tools import audit_vector_cell_contract as strict_audit
            from tools import vector_scalar_gap_reporting as reporting
        except ModuleNotFoundError:
            import audit_vector_cell_contract as strict_audit
            import vector_scalar_gap_reporting as reporting
    except ModuleNotFoundError as exc:
        return {}, {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    # Filesystem mtimes are synchronization metadata, not evidence authority.
    # Recompute every candidate from its hash-bound ledgers and select the
    # largest valid union.  This prevents a touched older cohort receipt from
    # hiding a later superset after Mac<->S1 synchronization.
    candidates = []
    rejected = []
    for report_path in reports:
        try:
            receipt = json.loads(report_path.read_text())
            if (
                not str(receipt.get("contract") or "").startswith(
                    "STRICT_PER_KEY_UNIQUE_METRIC_AND_ACTION_"
                )
                or receipt.get("exact_completion_credit") is not False
                or receipt.get("promotion_allowed") is not False
            ):
                raise ValueError("strict audit contract invalid")
            source_paths = []
            expected_hashes = receipt.get("source_ledger_sha256") or {}
            source_names = receipt.get("source_ledgers") or []
            for raw in source_names:
                path = Path(str(raw))
                if not path.is_absolute():
                    path = base / path
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if not expected_hashes.get(str(raw)) or digest != expected_hashes[str(raw)]:
                    raise ValueError(f"source hash mismatch: {raw}")
                source_paths.append(path)
            if not source_paths:
                raise ValueError("strict audit has no sources")
            latest, raw_rows = strict_audit.latest_rows(source_paths)
            audited, recomputed = strict_audit.audit_rows(
                latest, family_by_param=strict_audit._family_index()
            )
            expected_counts = (
                int(receipt["raw_rows_read"])
                if "raw_rows_read" in receipt else -1,
                int(receipt["passed"]) if "passed" in receipt else -1,
                int(receipt["quarantined"])
                if "quarantined" in receipt else -1,
            )
            actual_counts = (
                raw_rows,
                int(recomputed.get("passed") or 0),
                int(recomputed.get("quarantined") or 0),
            )
            if actual_counts != expected_counts:
                raise ValueError(
                    f"strict audit count mismatch {actual_counts}!={expected_counts}"
                )
            overlay = {}
            for (key, param, value), raw_row in audited.items():
                row = reporting._normalise_detail_row(raw_row)
                if (
                    row.get("vector_evidence_class") != "VEC_APPROX"
                    or not reporting._detail_contract_ok(row)
                ):
                    continue
                overlay[(param, norm_val(value), key)] = row
            score = (
                len(source_paths),
                raw_rows,
                int(recomputed.get("passed") or 0),
                str(receipt.get("generated_at") or ""),
                report_path.name,
            )
            candidates.append(
                (score, report_path, overlay, recomputed, raw_rows, len(source_paths))
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            rejected.append(
                {
                    "report": str(report_path.relative_to(base)),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
    if not candidates:
        return {}, {
            "available": False,
            "reason": "no hash-verified strict audit candidate",
            "rejected_candidates": rejected,
        }
    _score, report_path, overlay, recomputed, raw_rows, source_count = max(
        candidates, key=lambda item: item[0]
    )
    return overlay, {
        "available": True,
        "selection_rule": "LARGEST_HASH_VERIFIED_SOURCE_UNION",
        "report": str(report_path.relative_to(base)),
        "source_ledgers": source_count,
        "raw_rows": raw_rows,
        "passed": recomputed.get("passed"),
        "quarantined": recomputed.get("quarantined"),
        "valid_candidates": len(candidates),
        "rejected_candidates": rejected,
    }


def load_full_matrix_vector_overlay(base):
    """Load the append-only full-blank VEC overlay without touching ENGINE data.

    The full campaign has one raw JSONL ledger for every physical blank in the
    TRB workbook.  It is intentionally separate from the smaller scalar-gap
    receipt: reading it here keeps the ENGINE CSV/counts exact while allowing
    only otherwise blank XLSX cells to display their amber diagnostic value.
    """
    try:
        from tools import vector_scalar_gap_reporting as reporting
    except ModuleNotFoundError:
        import vector_scalar_gap_reporting as reporting

    directory = base / "data/reports/full_trb_blank_matrix_vec_approx_20260801"
    index_path = directory / "vector_overlay_index.jsonl"
    raw_path = directory / "all_cells.jsonl"
    paths = []
    if index_path.is_file() and index_path.stat().st_size:
        paths.append(index_path)
    elif raw_path.is_file() and raw_path.stat().st_size:
        paths.append(raw_path)
    # Key-scoped ledgers are the portable/small replication unit.  They also
    # protect a Mac regeneration when the ~1GB shared S1 index is not mirrored.
    key_file = re.compile(r"^[A-Z0-9.\-]+_(?:LONG|SHORT)\.jsonl$")
    paths.extend(
        path
        for path in sorted(directory.glob("*.jsonl"))
        if key_file.match(path.name) and path not in paths
    )
    if not paths:
        return {}
    latest = {}
    for path in paths:
        with path.open() as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = reporting._normalise_detail_row(json.loads(line))
                except (TypeError, json.JSONDecodeError):
                    continue
                if (
                    row.get("status") not in reporting.ALLOWED_STATUSES | {"DATA_UNAVAILABLE"}
                    or not reporting._detail_contract_ok(row)
                    or row.get("vector_evidence_class") != "VEC_APPROX"
                    or row.get("protected_exact_present") is True
                    or not row.get("source_hash")
                    or not isinstance(row.get("telemetry_2000_usd"), dict)
                ):
                    continue
                _kind, error = reporting._evidence_class(row, set())
                if error:
                    continue
                logical = (
                    str(row.get("param") or ""),
                    norm_val(row.get("value_json")),
                    str(row.get("key") or ""),
                )
                # Append-only rows may later be refined by a causal VEC proxy.
                latest[logical] = row
    return latest


def load_behavior_distinct_vector_overlay(base):
    """Load finalized behavior-distinct frontiers as strict amber diagnostics.

    A frontier receipt is portable between S1 and Mac even though its recorded
    paths are absolute.  Resolve every artifact underneath the receipt's key
    directory, verify the accepted ledger plus every raw shard and protected
    evidence hash, and reject the entire key on any mismatch.  These rows are
    VEC diagnostics only: exact completion, ranking, promotion, DB writes and
    live writes all remain prohibited.
    """
    import hashlib

    roots = (
        base / "data/reports/behavior_distinct_vector_frontier",
        base / "data/reports/behavior_distinct_interaction_frontier_c6",
    )
    receipt_paths = [
        path for root in roots if root.is_dir()
        for path in sorted(root.glob("*/FRONTIER_RECEIPT.json"))
    ]
    if not receipt_paths:
        return {}, {"available": False, "reason": "frontier directory missing"}

    overlay = {}
    accepted_keys = []
    rejected = []
    for receipt_path in receipt_paths:
        key_dir = receipt_path.parent
        key = key_dir.name
        interaction = "behavior_distinct_interaction_frontier_c6" in receipt_path.parts
        expected_schema = (
            "behavior-distinct-interaction-frontier-receipt-v1"
            if interaction
            else "behavior-distinct-vector-frontier-receipt-v1"
        )
        expected_evidence = (
            "BEHAVIOR_DISTINCT_INTERACTION_FRONTIER"
            if interaction
            else "BEHAVIOR_DISTINCT_VECTOR_FRONTIER"
        )
        try:
            receipt = json.loads(receipt_path.read_text())
            if (
                receipt.get("schema")
                != expected_schema
                or receipt.get("status") != "PASS"
                or str(receipt.get("key") or "") != key
                or receipt.get("preserved_v8_cells_written") is not False
                or receipt.get("database_written") is not False
                or receipt.get("workbook_written") is not False
                or receipt.get("live_config_written") is not False
            ):
                raise ValueError("frontier authority/identity contract invalid")
            integrity = receipt.get("integrity_contract") or {}
            for prohibited in (
                "zero_allowed",
                "nonfinite_allowed",
                "duplicate_metric_within_key_allowed",
                "duplicate_action_fingerprint_within_key_allowed",
                "synthetic_jitter_allowed",
                "copied_result_allowed",
                "preserved_v8_overwrite_allowed",
            ):
                if integrity.get(prohibited) is not False:
                    raise ValueError(f"frontier integrity contract missing {prohibited}=false")

            accepted_path = key_dir / "accepted_index.jsonl"
            if not accepted_path.is_file():
                raise ValueError("accepted index missing")
            accepted_sha = hashlib.sha256(accepted_path.read_bytes()).hexdigest()
            if accepted_sha != receipt.get("accepted_index_sha256"):
                raise ValueError("accepted index hash mismatch")

            # The S1 receipt stores absolute paths.  Retain the raw contract
            # directory as well as the filename when resolving a portable Mac
            # copy.  Resolving by basename alone is ambiguous when a key has
            # more than one completed frontier contract (every contract has a
            # shard_00.jsonl) and could accidentally validate the wrong shard.
            for raw in receipt.get("raw_shards") or []:
                recorded = Path(str(raw.get("path") or ""))
                try:
                    raw_index = recorded.parts.index("raw")
                    relative_raw = Path(*recorded.parts[raw_index:])
                except ValueError:
                    raise ValueError("raw shard path is not portable")
                raw_path = key_dir / relative_raw
                try:
                    raw_path.relative_to(key_dir / "raw")
                except ValueError:
                    raise ValueError("raw shard path escapes contract directory")
                if not raw_path.is_file() or raw_path.is_symlink():
                    raise ValueError("raw shard missing")
                if hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw.get("sha256"):
                    raise ValueError("raw shard hash mismatch")
            protected = receipt.get("protected_evidence") or {}
            for source in protected.get("sources") or []:
                recorded = Path(str(source.get("path") or ""))
                parts = recorded.parts
                try:
                    data_index = parts.index("data")
                    source_path = base / Path(*parts[data_index:])
                except ValueError:
                    source_path = base / "data/reports" / recorded.name
                if not source_path.is_file():
                    raise ValueError("protected evidence source missing")
                if hashlib.sha256(source_path.read_bytes()).hexdigest() != source.get("sha256"):
                    raise ValueError("protected evidence source hash mismatch")

            rows = []
            for line in accepted_path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if (
                    str(row.get("key") or "") != key
                    or row.get("status") != "MOVED"
                    or row.get("frontier_acceptance_status")
                    != "ACCEPTED_UNIQUE_MEASURED"
                    or row.get("vector_evidence_class")
                    != expected_evidence
                    or not row.get("action_fingerprint")
                    or int(row.get("real_closes") or 0) <= 0
                    or row.get("exact_completion_credit") is not False
                    or row.get("engine_ranking_allowed") is not False
                    or row.get("promotion_allowed") is not False
                    or row.get("live_config_write_allowed") is not False
                    or row.get("db_engine_write_allowed") is not False
                ):
                    raise ValueError("accepted row authority/activity contract invalid")
                item = dict(row)
                item["frontier_vector_evidence_class"] = item["vector_evidence_class"]
                # Reuse the canonical amber renderer; the original class is
                # retained above and in the hash-bound accepted ledger.
                item["vector_evidence_class"] = "VEC_APPROX"
                item["result_uniqueness_status"] = "PASS"
                item["result_uniqueness_reasons"] = []
                item["result_collision_count"] = 0
                rows.append(item)
            if len(rows) != int(receipt.get("accepted_unique_measured_cells") or -1):
                raise ValueError("accepted row count mismatch")
            for item in rows:
                logical = (
                    str(item.get("param") or ""),
                    norm_val(item.get("value_json")),
                    key,
                )
                if logical in overlay:
                    raise ValueError(f"duplicate logical frontier cell: {logical}")
                overlay[logical] = item
            accepted_keys.append(
                {
                    "key": key,
                    "accepted": len(rows),
                    "receipt": str(receipt_path.relative_to(base)),
                    "receipt_sha256": hashlib.sha256(
                        receipt_path.read_bytes()
                    ).hexdigest(),
                }
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            rejected.append(
                {
                    "key": key,
                    "receipt": str(receipt_path.relative_to(base)),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
    return overlay, {
        "available": bool(accepted_keys),
        "contract": "HASH_BOUND_BEHAVIOR_DISTINCT_VECTOR_DIAGNOSTIC_ONLY",
        "accepted_keys": accepted_keys,
        "accepted_rows": len(overlay),
        "rejected_keys": rejected,
        "engine_cells_modified": False,
        "exact_completion_credit": False,
    }


def _split_main_sub(row):
    """[param, value, ...] -> [main_switch, sub_setting, value, ...].

    A knob whose registry role is MAIN_SWITCH owns column A and leaves B blank; every other
    knob in the family sits in column B under its family's main switch, so the whole feature
    reads as one block: the on/off, then its settings and their value ladders."""
    param, val, rest = row[0], row[1], row[2:]
    d = registry().get(param, {})
    role = d.get("role", "")
    fam = d.get("family") or param
    if role == "MAIN_SWITCH":
        return [param, "", val] + rest
    mains = sorted(
        k for k, v in registry().items()
        if v.get("family") == fam and v.get("role") == "MAIN_SWITCH"
    )
    main = next((k for k in mains if k == f"{fam}_ENABLED"), None)
    if main is None:
        main = next((k for k in mains if k.endswith("_ENABLED")), None)
    if main is None and len(mains) == 1:
        main = mains[0]
    if not main or main == param:
        # no master switch for this family (packs, TF selectors, orphan knobs): it heads its own
        # block rather than being listed as a sub-setting of itself
        return [param, "", val] + rest
    return [main, param, val] + rest


def reconnect_evidence_sufficient(param, tested):
    """Require an actual two-state comparison before painting a knob red.

    A single inert numeric value means only ``INERT_AT_VALUE``. It does not
    prove the entire setting is disconnected. Boolean knobs are the exception:
    one non-default exact value plus the accepted baseline is the complete
    two-state comparison.
    """
    info = registry().get(param, {})
    by_key = {}
    for value, key in tested:
        by_key.setdefault(key, set()).add(norm_val(value))
    if info.get("kind") == "bool":
        default = norm_val(info.get("default"))
        return any(
            value != default for values in by_key.values() for value in values
        )
    return any(len(values) >= 2 for values in by_key.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default=None, help="restrict to one campaign (default: all)")
    ap.add_argument("--only-tested", action="store_true", help="skip switch-values with no data yet")
    ap.add_argument("--account", default="trb", choices=["trb", "trc"], help="stock account universe for key columns")
    ap.add_argument("--tier", default="ENGINE", choices=["ENGINE", "VEC"],
                    help="ENGINE = faithful Tier-2 (the only promotable truth); "
                         "VEC = Tier-1 screen, exported to its own [DIAGNOSTIC] file. NEVER mixed.")
    ap.add_argument("--include-1yr", action="store_true",
                    help="2026-07-30 USER: also fill gaps from the '<campaign>_1yr' temporary "
                         "diagnostic campaign (shortened test range) and font-color those cells "
                         "distinctly. Never overwrites a full-window cell.")
    ap.add_argument(
        "--user-provisional-overlay-write",
        action="store_true",
        help=(
            "explicit operator override: permit the amber, receipt-backed, "
            "behavior-unique VEC_APPROX overlay export while the sparse "
            "physical-equivalence gate is quarantined. It never changes "
            "ENGINE completion, DB, ranking, promotion, or live state."
        ),
    )
    a = ap.parse_args()
    sparse_quarantine = BASE / "data/reports/EXPORT_SWITCH_MATRIX_SPARSE_QUARANTINE.json"
    if sparse_quarantine.exists() and not a.user_provisional_overlay_write:
        raise SystemExit(
            "SPARSE_EXPORT_QUARANTINED: full physical-equivalence audit is required "
            "before any canonical matrix export may run; use "
            "--user-provisional-overlay-write only for an explicitly labeled "
            "VEC_APPROX display overlay"
        )
    if (
        a.campaign == "stocks_baseline_v2_s4h"
        and os.environ.get("ALLOW_FROZEN_HISTORICAL_MATRIX_EXPORT") != "1"
    ):
        raise SystemExit(
            "FROZEN_HISTORICAL_EXPORT_BLOCKED: stocks_baseline_v2_s4h is "
            "read-only evidence and may not consume the canonical export lane; "
            "set ALLOW_FROZEN_HISTORICAL_MATRIX_EXPORT=1 only for an explicit "
            "operator-requested suffixed historical artifact"
        )
    # Parse and reject retired launchers before taking the shared surface lock.
    # Keep this handle alive for the remainder of an accepted export.
    _surface_lock_handle = acquire_surface_lock()
    if a.tier == "ENGINE" and a.campaign is None:
        # The unrestricted "latest evidence" view silently resurrected pre-fix cells whenever
        # a repaired cell was still blank.  ENGINE now defaults to the exact repaired campaign;
        # historical campaigns remain queryable only by naming one explicitly.
        a.campaign = CURRENT_ENGINE_CAMPAIGN
    # A long-lived report job used an explicit pre-repair campaign and
    # repeatedly overwrote SWITCH_MATRIX_TRB.xlsx with a historical 10-sheet
    # view. Historical campaigns now always receive their own filename.
    suffix = output_suffix(a.tier, a.campaign)
    OUT_XLSX = BASE / "data" / "reports" / f"SWITCH_MATRIX_{a.account.upper()}{suffix}.xlsx"
    OUT_CSV = BASE / "data" / "reports" / f"SWITCH_MATRIX_{a.account.upper()}{suffix}.csv.gz"
    set_local_immutable(OUT_XLSX, False)
    set_local_immutable(OUT_CSV, False)
    cols = key_columns(a.account)
    rows = manifest_rows()
    inventory = manifest_inventory()
    cells, inert, cell_meta, base = load_cells(
        a.campaign, a.tier,
        extra_campaign=f"{a.campaign}_1yr" if a.include_1yr and a.campaign else None,
    )
    vector_scalar_gap = None
    if a.tier == "ENGINE" and a.campaign == CURRENT_ENGINE_CAMPAIGN:
        try:
            try:
                from tools import vector_scalar_gap_reporting
            except ModuleNotFoundError:
                import vector_scalar_gap_reporting
            vector_scalar_gap = vector_scalar_gap_reporting.load(BASE)
        except Exception as exc:
            vector_scalar_gap = {
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "rows": [],
            }
    engine_coverage = (
        load_engine_coverage(a.campaign)
        if a.tier == "ENGINE" and a.campaign == CURRENT_ENGINE_CAMPAIGN
        else None
    )
    rows = include_observed_rows(rows, cells, a.tier)
    # USER 2026-07-21: options not traded — params neither tested nor listed in the work-list
    rows = [pv for pv in rows if "OPTION" not in pv[0]]
    # USER 2026-07-21: WT1-cross trigger rows FIRST in every matrix
    rows.sort(key=lambda pv: (not pv[0].startswith("WT_3M_FORCE_OPEN"), 0))
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    # USER 2026-07-22 layout: read the sheet as a DIAGRAM — column A is the MAIN SWITCH (the
    # True/False that moves a whole group), column B is the SUB-SETTING under it, column C its
    # value ladder (20/30/40...). Family + role come from tools/knob_registry.py so entry and
    # exit read identically in stocks and crypto.
    header = ["main_switch", "sub_setting", "value", "description", "status", "n_tested", "n_inert", "mean_delta",
              "best_key", "best_delta", "worst_key", "worst_delta"] + cols
    META_COLS = header.index(cols[0]) if cols else len(header)
    # The old physical-blank campaign broadcast proxy/baseline behavior over
    # hundreds of thousands of unrelated cells.  It remains immutable raw
    # diagnostic evidence, but loading it into the canonical workbook created
    # ~411k red comments, >5 GiB RSS and multi-minute exports.  Current matrix
    # display starts only from reviewed scalar-gap receipts plus the strict,
    # hash-verified causal audit below.  No old broadcast row is deleted.
    vector_approx_index = {}
    vector_approx_index.update({
        (
            str(item.get("param") or ""),
            norm_val(item.get("value_json")),
            str(item.get("key") or ""),
        ): item
        for item in (vector_scalar_gap or {}).get("rows", [])
        if item.get("vector_evidence_class") == "VEC_APPROX"
    })
    strict_vector_index, strict_vector_audit = (
        load_active_vector_cell_audit_overlay(BASE)
    )
    # The append-only strict lane is newer for an overlapping logical cell.
    # It remains amber, non-promotable evidence after the combined audit.
    vector_approx_index.update(strict_vector_index)
    behavior_vector_index, behavior_vector_audit = (
        load_behavior_distinct_vector_overlay(BASE)
    )
    # Finalized behavior-distinct rows are the newest strict diagnostics for
    # an overlapping logical cell. Exact/recovered/preserved precedence is
    # still applied below before any workbook value is rendered.
    vector_approx_index.update(behavior_vector_index)
    vector_approx_index, vector_uniqueness_audit = (
        audit_vector_overlay_uniqueness(vector_approx_index)
    )
    vector_uniqueness_audit["behavior_distinct_frontier"] = (
        behavior_vector_audit
    )
    preserved_v8_index, preserved_v8_audit = load_preserved_v8_cells(BASE)
    recovered_pilot_v8_index, recovered_pilot_v8_audit = (
        load_recovered_pilot_v8_cells(BASE)
    )
    # Historical exact V8 settings that have fallen out of the current
    # manifest are still evidence and must remain visible.  Add their row axes
    # without turning them into current-contract completion credit.
    existing_row_axes = set(rows)
    rows.extend(
        sorted(
            {
                (param, value)
                for (param, value, _key) in {
                    **vector_approx_index,
                    **preserved_v8_index,
                    **recovered_pilot_v8_index,
                }
                if (param, value) not in existing_row_axes
                and "OPTION" not in param
            }
        )
    )
    preserved_v8_audit.update(
        {
            "generated_at_for_matrix": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            "current_engine_cells_with_precedence": sum(
                logical in cells for logical in preserved_v8_index
            ),
            "preserved_cells_protected_from_vector": sum(
                logical in vector_approx_index for logical in preserved_v8_index
            ),
            "precedence_enforced": (
                "current receipt-valid ENGINE > immutable recovered S1 V8 > "
                "immutable preserved workbook V8 > "
                "unique active vector > blank"
            ),
            "recovered_pilot_v8_receipt": recovered_pilot_v8_audit,
            "preserved_rows_written_to_engine_csv": 0,
            "promotion_allowed": False,
        }
    )
    preserved_audit_path = BASE / "data/reports/PRESERVED_V8_RESTORE_AUDIT.json"
    preserved_audit_tmp = preserved_audit_path.with_name(
        f".{preserved_audit_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    preserved_audit_tmp.write_text(
        json.dumps(preserved_v8_audit, indent=2, sort_keys=True) + "\n"
    )
    os.replace(preserved_audit_tmp, preserved_audit_path)
    vector_uniqueness_audit.update(
        {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "current_engine_campaign": CURRENT_ENGINE_CAMPAIGN,
            "source_rule": (
                "reviewed scalar-gap plus strict active-audit VEC_APPROX "
                "overlays; old physical-blank broadcast is audit-only and "
                "excluded from canonical display; "
                "current exact ENGINE cells always take precedence"
            ),
            "old_full_blank_broadcast_display_excluded": True,
            "strict_active_vector_audit": strict_vector_audit,
        }
    )
    uniqueness_path = (
        BASE / "data/reports/VECTOR_OVERLAY_UNIQUENESS_AUDIT.json"
    )
    uniqueness_tmp = uniqueness_path.with_name(
        f".{uniqueness_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    uniqueness_tmp.write_text(
        json.dumps(vector_uniqueness_audit, indent=2, sort_keys=True) + "\n"
    )
    # The synchronized Mac copy is deliberately immutable between atomic
    # exports.  Treat this sidecar exactly like the XLSX/CSV surface so a
    # legitimate refresh does not fail halfway through on UF_IMMUTABLE.
    set_local_immutable(uniqueness_path, False)
    try:
        os.replace(uniqueness_tmp, uniqueness_path)
    finally:
        set_local_immutable(uniqueness_path, True)
    matrix, coverage, matrix_states, matrix_evidence = [], [], [], []
    matrix_vector_overlays = []
    matrix_preserved_v8_overlays = []
    matrix_recovered_pilot_v8_overlays = []
    # USER 2026-07-21: "If 2 fields produce the same results for different settings the function
    # is broken and needs to be fixed." Two mechanically distinct ways that happens, both fatal:
    #   RECONNECT  — every value left the sim bit-identical to baseline: the code never read the
    #                knob (disconnected wiring).
    #   DEGENERATE — the values differ from baseline but not from EACH OTHER: the knob is read
    #                but saturates, so the grid carries no information. This is the class the
    #                WT_DC_ENTRY_THRESHOLD 35/43/55/75 example belongs to — v8_vec_sweep defaults
    #                that knob to 0.0 while live config_tradier runs 45, so every override >=35
    #                shuts the gate and returns 0 trades at every value.
    by_switch = {}
    for param, val in rows:
        by_switch.setdefault(param, []).append(val)
    dead, degenerate = {}, {}
    for p, vs in by_switch.items():
        tested = [(v, c) for v in vs for c in cols if (p, v, c) in cells]
        dead[p] = (
            reconnect_evidence_sufficient(p, tested)
            and all(inert.get((p, v, c), False) for v, c in tested)
        )
        per_value = {}
        for v, c in tested:
            per_value.setdefault(v, {})[c] = cells[(p, v, c)]
        shared = set.intersection(*(set(d) for d in per_value.values())) if len(per_value) > 1 else set()
        degenerate[p] = bool(shared) and len(per_value) > 1 and all(
            len({round(per_value[v][c], 6) for v in per_value}) == 1 for c in shared)
    for param, val in rows:
        vals = {c: cells.get((param, val, c)) for c in cols}
        got = {c: v for c, v in vals.items() if v is not None}
        n_inert = sum(1 for c in got if inert.get((param, val, c)))
        stale_keys = (
            [c for c in cols if (param, val, c) in engine_coverage["stale"]]
            if engine_coverage else []
        )
        invalid_keys = (
            [c for c in cols if (param, val, c) in engine_coverage["invalid"]]
            if engine_coverage else []
        )
        vec_only_keys = (
            [
                c for c in cols
                if (param, val, c) in engine_coverage["vec_only"]
                and (param, val, c) not in engine_coverage["stale"]
            ]
            if engine_coverage else []
        )
        if a.only_tested and not got: continue
        if not got:
            pending_capital_keys = (
                [
                    c for c in cols
                    if (param, val, c) in engine_coverage["pending_capital"]
                ]
                if engine_coverage else []
            )
            if pending_capital_keys:
                status = "PENDING_CAPITAL_REVALUE"
            elif stale_keys:
                status = "STALE_ENGINE_CONTRACT"
            elif invalid_keys:
                status = "ENGINE_VALIDATION_FAILED"
            elif vec_only_keys:
                status = "EXACT_QUEUE_EMPTY"
            else:
                status = "NOT_ENGINE_TESTED"
        elif dead.get(param):
            status = "RECONNECT"
        elif degenerate.get(param):
            status = "DEGENERATE"
        elif n_inert == len(got):
            status = "INERT_AT_VALUE"
        else:
            status = "OK"
        if got:
            mean_d = round(sum(got.values()) / len(got), 4)
            bk = max(got, key=got.get)
            wk = min(got, key=got.get)
            row = [param, val, describe_knob(param), status, len(got), n_inert, mean_d,
                   bk, round(got[bk], 4), wk, round(got[wk], 4)]
        else:
            row = [param, val, describe_knob(param), status, 0, 0, None, None, None, None, None]
        row = _split_main_sub(row)
        # Preserve the measured precision in the physical result cell.  The
        # workbook number format may present fewer decimals, but rounding the
        # stored value here manufactured collisions and hid legitimate V8
        # results from the per-symbol/side uniqueness gate.
        row += [(float(vals[c]) if vals[c] is not None else None) for c in cols]
        matrix.append(row)
        matrix_evidence.append(
            [cell_meta.get((param, val, c)) for c in cols]
        )
        matrix_vector_overlays.append(
            [
                vector_approx_index.get((param, norm_val(val), c))
                for c in cols
            ]
        )
        matrix_preserved_v8_overlays.append(
            [
                preserved_v8_index.get((param, norm_val(val), c))
                for c in cols
            ]
        )
        matrix_recovered_pilot_v8_overlays.append(
            [
                recovered_pilot_v8_index.get((param, norm_val(val), c))
                for c in cols
            ]
        )
        states = []
        for c in cols:
            meta = cell_meta.get((param, val, c))
            if vals[c] is None:
                states.append(None)
                continue
            # Red = equal to B&H / not wired. Zero-trade rows are also red: they are a bug,
            # never a safe result. Fingerprint-identical values are red even if rounded deltas
            # differ. Green = beats the floor. Gray = every below-B&H result, retained only as
            # discard evidence. White is reserved for measured rows whose B&H comparison is
            # unavailable; it is never a promotion state.
            states.append(matrix_cell_state(param, meta))
        matrix_states.append(states)
        coverage.append([
            param, val, status, len(got), n_inert,
            sum(1 for v in got.values() if v > 0),
            sum(1 for v in got.values() if v < 0),
            len(stale_keys), len(invalid_keys), len(vec_only_keys),
        ])
    post_precedence_vector_audit = audit_post_precedence_vector_overlays(
        matrix,
        matrix_states,
        matrix_vector_overlays,
        matrix_preserved_v8_overlays,
        cols,
        matrix_recovered_pilot_v8_overlays,
    )
    vector_uniqueness_audit["post_precedence_display_audit"] = (
        post_precedence_vector_audit
    )
    uniqueness_tmp = uniqueness_path.with_name(
        f".{uniqueness_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    uniqueness_tmp.write_text(
        json.dumps(vector_uniqueness_audit, indent=2, sort_keys=True) + "\n"
    )
    set_local_immutable(uniqueness_path, False)
    try:
        os.replace(uniqueness_tmp, uniqueness_path)
    finally:
        set_local_immutable(uniqueness_path, True)
    # USER 2026-07-21: stocks trigger runs on wt1_5m (WT_FORCE_OPEN_TRIGGER_TF='5m',
    # tradier_manage ~2698) — display 5m; config knob keeps the legacy WT_3M_ name.
    for r in matrix:
        if isinstance(r[0], str) and r[0].startswith("WT_3M_FORCE_OPEN"):
            r[0] = r[0].replace("WT_3M_FORCE_OPEN", "WT1_5M_FORCE_OPEN[cfg:WT_3M]")
        if isinstance(r[1], str) and r[1].startswith("WT_3M_FORCE_OPEN"):
            r[1] = r[1].replace("WT_3M_FORCE_OPEN", "WT1_5M_FORCE_OPEN[cfg:WT_3M]")
    color_map = {(r[0], r[1], str(r[2])): states for r, states in zip(matrix, matrix_states)}
    evidence_map = {
        (r[0], r[1], str(r[2])): evidence
        for r, evidence in zip(matrix, matrix_evidence)
    }
    vector_overlay_map = {
        (r[0], r[1], str(r[2])): overlays
        for r, overlays in zip(matrix, matrix_vector_overlays)
    }
    preserved_v8_overlay_map = {
        (r[0], r[1], str(r[2])): overlays
        for r, overlays in zip(matrix, matrix_preserved_v8_overlays)
    }
    recovered_pilot_v8_overlay_map = {
        (r[0], r[1], str(r[2])): overlays
        for r, overlays in zip(matrix, matrix_recovered_pilot_v8_overlays)
    }

    # Decide the physical result owner before writing any worksheet.  Sheet
    # order must never let a lower-priority result reserve a number ahead of a
    # current exact result that happens to be rendered later.  A lower source
    # may fill the logical cell only when the higher source is absent or is
    # itself prohibited (zero/non-finite/duplicate within this symbol-side).
    def _display_identity(row, key):
        # openpyxl reads an intentionally empty sub-setting cell back as None,
        # while the in-memory matrix uses "".  Canonicalize both forms so the
        # pre-reserved evidence owner is the same owner used during rendering.
        # A None/empty mismatch here previously made every otherwise-valid
        # planned cell reject itself and produced an all-blank workbook.
        main_setting = "" if row[0] in (None, "") else str(row[0])
        sub_setting = "" if row[1] in (None, "") else str(row[1])
        return json.dumps(
            [main_setting, sub_setting, norm_val(row[2]), str(key)],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    display_plan = {}
    display_rejection = {}
    display_reserved_values = {key: {} for key in cols}

    def _reserve_display_candidate(row, key, source_class, value):
        import math

        identity = _display_identity(row, key)
        if identity in display_plan:
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            display_rejection.setdefault(identity, "NON_NUMERIC")
            return
        if not math.isfinite(number):
            display_rejection.setdefault(identity, "NON_FINITE")
            return
        if number == 0.0:
            display_rejection.setdefault(identity, "PROHIBITED_ZERO")
            return
        owners = display_reserved_values.setdefault(key, {})
        if number in owners and owners[number] != identity:
            display_rejection.setdefault(
                identity, f"PROHIBITED_DUPLICATE_OF:{owners[number]}"
            )
            return
        owners[number] = identity
        display_plan[identity] = {"source": source_class, "value": number}
        display_rejection.pop(identity, None)

    # Global precedence passes; each pass is deterministic in matrix order.
    for row, states in zip(matrix, matrix_states):
        for index, key in enumerate(cols):
            if states[index] is not None:
                _reserve_display_candidate(
                    row, key, "exact_or_current_numeric", row[META_COLS + index]
                )
    for source_class, overlay_rows in (
        ("recovered_pilot_v8", matrix_recovered_pilot_v8_overlays),
        ("preserved_v8", matrix_preserved_v8_overlays),
        ("vector_unique", matrix_vector_overlays),
    ):
        for row, states, overlays in zip(matrix, matrix_states, overlay_rows):
            for index, key in enumerate(cols):
                if states[index] is not None or _display_identity(row, key) in display_plan:
                    continue
                item = overlays[index]
                if not item:
                    continue
                if source_class == "recovered_pilot_v8":
                    if item.get("physical_display_status") != "PASS_UNIQUE_NONZERO":
                        continue
                    value = item.get("delta_gain_mo_vs_bh")
                elif source_class == "preserved_v8":
                    if item.get("preservation_display_status") != "PASS":
                        continue
                    value = item.get("delta_gain_mo_vs_bh")
                else:
                    if (
                        item.get("status") == "DATA_UNAVAILABLE"
                        or item.get("result_uniqueness_status") != "PASS"
                    ):
                        continue
                    value = item.get("delta_gain_mo_vs_bh_diagnostic")
                _reserve_display_candidate(row, key, source_class, value)
    tier_note = ("# TIER=ENGINE — capital-correct exact Tier-2 evidence. Logical-cell priority: "
                 "current full c5 > receipt-validated historical c2/c3/c4 > historical c1 > "
                 "current c5 1yr gap-fill. Historical labels preserve provenance and do not "
                 "claim current-code parity.\n"
                 if a.tier == "ENGINE" else
                 "# [DIAGNOSTIC ONLY] TIER=VEC — Tier-1 vectorized screen (v8_vec_sweep, ~85% live parity).\n"
                 "# Deltas are vs the Tier-1 baseline. NEVER comparable to the ENGINE file, never promotion evidence.\n")
    csv_tmp = OUT_CSV.with_name(
        f".{OUT_CSV.name}.tmp.{os.getpid()}.{time.time_ns()}"
    )
    with gzip.open(csv_tmp, "wt") as fh:
        fh.write(tier_note)
        if a.tier == "ENGINE" and a.campaign == CURRENT_ENGINE_CAMPAIGN:
            try:
                try:
                    from tools import persym_baseline_campaign as psc
                except ModuleNotFoundError:
                    import persym_baseline_campaign as psc
                contract_version = psc.MATRIX_CONTRACT_VERSION
            except Exception as exc:
                raise RuntimeError(
                    "cannot bind canonical CSV to current exact contract"
                ) from exc
            fh.write(f"# CURRENT_CAMPAIGN={CURRENT_ENGINE_CAMPAIGN}\n")
            fh.write(f"# CURRENT_ENGINE_CUTOFF={CURRENT_ENGINE_CUTOFF}\n")
            fh.write(f"# CURRENT_CONTRACT_VERSION={contract_version}\n")
            fh.write(f"# CURRENT_MATRIX_SCOPE={CURRENT_MATRIX_SCOPE}\n")
            fh.write(
                "# EVIDENCE_PRIORITY=full_c5>validated_c2_c3_c4>validated_c1>"
                "current_c5_1yr_gap_fill\n"
            )
            fh.write(
                "# VEC_SCALAR_GAP=separate XLS diagnostic sheet plus amber/"
                "italic blank-cell display overlay; exact_completion_credit=false; "
                "never fills ENGINE CSV cells\n"
            )
            fh.write(
                f"# CANONICAL_MATRIX=data/reports/"
                f"SWITCH_MATRIX_{a.account.upper()}.csv.gz\n"
            )
        fh.write("# cell values: delta gain/mo versus the same-key B&H floor (not versus the old trading baseline)\n")
        fh.write("# cell colors: GREEN=beats B&H | RED=equal/zero-trade/fingerprint-identical (wiring bug) | "
                 "WHITE=B&H comparison unavailable (never promotion) | GRAY=all below-B&H discard evidence\n")
        fh.write("# status: OK=value moved the sim | INERT_AT_VALUE=this value changed nothing | "
                 "RECONNECT=no value changed anything (knob unread by the code) | "
                 "DEGENERATE=values differ from baseline but not from each other (knob saturates "
                 "or vec/live default mismatch) | STALE_ENGINE_CONTRACT=exact row preserved but "
                 "invalidated by current code+NPZ+side fingerprint | "
                 "ENGINE_VALIDATION_FAILED=exact run exists but failed the repaired contract | "
                 "EXACT_QUEUE_EMPTY=VEC evidence exists but no attributable current exact result | "
                 "NOT_ENGINE_TESTED=no exact ENGINE evidence for this switch/value\n")
        fh.write("# stocks: WT trigger TF = 5m (wt1_5m); rows named WT1_5M_FORCE_OPEN[cfg:WT_3M] map to config knobs WT_3M_FORCE_OPEN_*\n")
        # 2026-07-21: was a naive ",".join — list-valued switches (R2_TF_LIST=['d','15m'], TF
        # whitelists, symbol lists) embed commas, which shifted every key column right for those
        # rows and mis-attributed each cell to the wrong symbol. Same defect class as the crypto
        # column-shift found 2026-07-20. Quote properly; never hand-roll CSV.
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in matrix: w.writerow(["" if x is None else x for x in r])
    # USER 2026-07-21: separate ENTRY vs EXIT switches, group rows per switch, and grey
    # every value-row that is NOT that switch's best (mutually exclusive: only one value
    # of a switch can be live) — numbers stay visible, styling marks the losers.
    import re as _re
    _EXIT_PAT = _re.compile(r"EXIT|STOP|TRAIL|CLOSE|NOLOSS|R1_|R2_|R3_|GIVEBACK|BREAKEVEN|HARVEST|SLOPE_FLIP|PROFIT_LOCK|CROSSUNDER|TF_EXCLUDE|REDUCE", _re.I)
    _SIZE_PAT = _re.compile(r"SIZE|SIZING|CONVICTION|RATIO_MULT|CAPITAL|BUDGET", _re.I)
    _ENTRY_PAT = _re.compile(r"ENTRY|OPEN|REENTRY|FORCE_OPEN|BREAKOUT|SCORE|GATE|ALIGN|CONFIRM|THRESHOLD|K_ZONE|MOMENTUM|ARROW|STOP_PACK", _re.I)

    def group_of(name):
        if _SIZE_PAT.search(name): return "Sizing"
        if _EXIT_PAT.search(name): return "Exit"
        if _ENTRY_PAT.search(name): return "Entry"
        return "Other"
    xlsx_tmp = None
    try:
        import openpyxl
        from openpyxl.comments import Comment
        from openpyxl.styles import Alignment, Font, PatternFill
        grey_font = Font(color="999999")
        grey_fill = PatternFill("solid", fgColor="EEEEEE")
        best_fill = PatternFill("solid", fgColor="E2EFDA")
        red_font = Font(color="9C0006")
        inert_fill = PatternFill("solid", fgColor="FFC7CE")
        # 2026-07-30 USER: 1yr-window diagnostic cells (shortened test range, NOT the
        # full-window matrix) must be visually distinguishable from complete tests.
        year1_font_color = "B8860B"
        unresolved_fill = PatternFill("solid", fgColor="FFC7CE")
        display_counts = {
            "exact_or_current_numeric": 0,
            "recovered_pilot_v8": 0,
            "preserved_v8": 0,
            "vector_unique": 0,
            "unresolved": 0,
            "rejected_zero": 0,
            "rejected_duplicate": 0,
        }
        # Pre-reserved by evidence precedence above.  The enforcement call in
        # the write loop accepts only the same logical owner and rejects every
        # attempted overwrite/collision.
        # Reservation decides which logical cells are allowed to render; it is
        # not evidence that a physical workbook cell has already been written.
        # Reusing the reservation map here made every planned cell collide with
        # its own pre-reserved value and blanked the entire workbook.  Physical
        # uniqueness starts empty and is populated only after a cell is
        # actually rendered.
        seen_display_values = {key: {} for key in cols}
        wb = openpyxl.Workbook()
        by_group = {"Entry": [], "Exit": [], "Sizing": [], "Other": []}
        for r in matrix:
            by_group[group_of(str(r[0]))].append(r)
        first = True
        for gname in ("Entry", "Exit", "Sizing", "Other"):
            rows_g = by_group[gname]
            ws = wb.active if first else wb.create_sheet()
            first = False
            ws.title = gname
            ws.append(header)
            # group by switch, best (max mean_delta among tested values) first inside group
            from collections import defaultdict as _dd
            sw_rows = _dd(list)
            for r in rows_g: sw_rows[r[0]].append(r)   # r[0] is now the MAIN SWITCH = one block per feature
            def _best_of(rs):
                # only OK rows may win a switch — an INERT/RECONNECT row's "delta" is the
                # absence of an effect, and greening it would promote a disconnected knob
                vals = [x[7] for x in rs if x[7] is not None and x[4] == "OK"]
                return max(vals) if vals else None
            for sw in sorted(sw_rows, key=lambda s: (not str(s).startswith("WT1_5M"), -(_best_of(sw_rows[s]) if _best_of(sw_rows[s]) is not None else -9e9), str(s))):
                rs = sw_rows[sw]
                best = _best_of(rs)
                # Read as a DIAGRAM: the MAIN SWITCH is written ONCE, bold, heading its block;
                # its sub-settings sit underneath with column A blank and their own value ladder
                # in column C. A blank spacer row separates features. Sub-settings are grouped
                # so all values of one setting stay together (20/30/40...), not interleaved.
                first = True
                for r in sorted(rs, key=lambda x: (str(x[1]) != "", str(x[1]),
                                                   -(x[7] if x[7] is not None else -9e9))):
                    r = list(r)
                    if first:
                        first = False
                    else:
                        r[0] = ""          # column A only on the block's first row
                    # Do not materialize every unresolved decision cell.  The
                    # header establishes the complete physical axis, while
                    # assigning a full ``r`` list (or reading ``ws[row]``)
                    # creates an openpyxl Cell object for each None.  At the
                    # current 11k x 135 surface that meant ~1.5m empty Cell
                    # objects before any real evidence was rendered.  Logical
                    # blanks remain blanks on disk and are still counted by
                    # the display-completion audit below.
                    ws.append(r[:META_COLS])
                    worksheet_row = ws.max_row
                    if str(r[1]) == "":
                        for column in range(1, 4):
                            ws.cell(worksheet_row, column).font = Font(bold=True)
                    else:
                        ws.cell(worksheet_row, 2).alignment = Alignment(indent=1)
                    if r[4] in ("RECONNECT", "DEGENERATE", "INERT_AT_VALUE"):
                        for column in range(1, META_COLS + 1):
                            cell = ws.cell(worksheet_row, column)
                            cell.font = red_font
                            cell.fill = inert_fill
                    elif r[4] in (
                        "STALE_ENGINE_CONTRACT", "ENGINE_VALIDATION_FAILED",
                        "EXACT_QUEUE_EMPTY", "NOT_ENGINE_TESTED",
                    ):
                        for column in range(1, META_COLS + 1):
                            cell = ws.cell(worksheet_row, column)
                            cell.font = grey_font
                            cell.fill = grey_fill
                    states = color_map.get((r[0] if r[0] else sw, r[1], str(r[2])), [])
                    evidence = evidence_map.get(
                        (r[0] if r[0] else sw, r[1], str(r[2])), []
                    )
                    vector_overlays = vector_overlay_map.get(
                        (r[0] if r[0] else sw, r[1], str(r[2])), []
                    )
                    preserved_v8_overlays = preserved_v8_overlay_map.get(
                        (r[0] if r[0] else sw, r[1], str(r[2])), []
                    )
                    recovered_pilot_v8_overlays = recovered_pilot_v8_overlay_map.get(
                        (r[0] if r[0] else sw, r[1], str(r[2])), []
                    )
                    for offset, state in enumerate(states, start=META_COLS):
                        overlay_index = offset - META_COLS
                        display_key = cols[overlay_index]
                        # Sparse rows materialize metadata only.  Always
                        # acquire the actual symbol/side target before an
                        # overlay attempt; otherwise ``cell`` can still refer
                        # to the last metadata cell and every vector overlay
                        # is incorrectly rejected as nonblank.
                        cell = ws.cell(worksheet_row, offset + 1)
                        key_seen_values = seen_display_values.setdefault(display_key, {})
                        # Column A is intentionally blanked on continuation
                        # rows for visual grouping.  The immutable result owner
                        # must still use the real block switch (`sw`), exactly
                        # as it did when the precedence plan was built.
                        display_identity = _display_identity(
                            [sw, r[1], r[2]], display_key
                        )
                        planned = display_plan.get(display_identity)
                        vector_overlay = (
                            vector_overlays[overlay_index]
                            if overlay_index < len(vector_overlays)
                            else None
                        )
                        preserved_v8_overlay = (
                            preserved_v8_overlays[overlay_index]
                            if overlay_index < len(preserved_v8_overlays)
                            else None
                        )
                        recovered_pilot_v8_overlay = (
                            recovered_pilot_v8_overlays[overlay_index]
                            if overlay_index < len(recovered_pilot_v8_overlays)
                            else None
                        )
                        if state is None:
                            # Strict display precedence.  The CSV/status/counters
                            # remain current-contract exact only; workbook users
                            # still recover historical V8 values and can see their
                            # quarantine state.  Vector can fill only the remaining
                            # blank and only after its uniqueness/activity audit.
                            source_class = None
                            if (
                                planned
                                and planned["source"] == "recovered_pilot_v8"
                                and apply_recovered_pilot_v8_overlay(cell, recovered_pilot_v8_overlay)
                            ):
                                source_class = "recovered_pilot_v8"
                            elif (
                                planned
                                and planned["source"] == "preserved_v8"
                                and apply_preserved_v8_overlay(cell, preserved_v8_overlay)
                            ):
                                source_class = "preserved_v8"
                            elif (
                                planned
                                and planned["source"] == "vector_unique"
                                and apply_vector_approx_overlay(cell, vector_overlay)
                            ):
                                source_class = "vector_unique"
                            if source_class is None:
                                display_counts["unresolved"] += 1
                                reason = display_rejection.get(display_identity)
                                if reason == "PROHIBITED_ZERO":
                                    display_counts["rejected_zero"] += 1
                                elif str(reason).startswith("PROHIBITED_DUPLICATE"):
                                    display_counts["rejected_duplicate"] += 1
                            else:
                                cell = ws.cell(worksheet_row, offset + 1)
                                accepted, reason = enforce_display_unique_nonzero(
                                    cell,
                                    display_identity,
                                    key_seen_values,
                                    invalid_fill=unresolved_fill,
                                )
                                if accepted:
                                    display_counts[source_class] += 1
                                else:
                                    display_counts["unresolved"] += 1
                                    if reason == "PROHIBITED_ZERO":
                                        display_counts["rejected_zero"] += 1
                                    elif str(reason).startswith("PROHIBITED_DUPLICATE"):
                                        display_counts["rejected_duplicate"] += 1
                            continue
                        # Only measured exact evidence warrants a physical
                        # decision cell.  Unresolved None values deliberately
                        # never reach ws.cell(), preserving a sparse workbook.
                        cell = ws.cell(worksheet_row, offset + 1, r[offset])
                        accepted, reason = enforce_display_unique_nonzero(
                            cell,
                            display_identity,
                            key_seen_values,
                            invalid_fill=unresolved_fill,
                        )
                        if not accepted:
                            display_counts["unresolved"] += 1
                            if reason == "PROHIBITED_ZERO":
                                display_counts["rejected_zero"] += 1
                            elif str(reason).startswith("PROHIBITED_DUPLICATE"):
                                display_counts["rejected_duplicate"] += 1
                            continue
                        display_counts["exact_or_current_numeric"] += 1
                        if state == "green":
                            cell.fill = best_fill
                            cell.font = Font(color="006100", bold=True)
                        elif state == "red":
                            cell.fill = inert_fill
                            cell.font = red_font
                        elif state == "gray":
                            cell.fill = grey_fill
                            cell.font = grey_font
                        else:  # white = B&H comparison unavailable; never promotion
                            cell.fill = PatternFill("solid", fgColor="FFFFFF")
                            cell.font = Font(color="000000")
                        col_key = cols[offset - META_COLS] if offset - META_COLS < len(cols) else None
                        meta = cell_meta.get((sw, norm_val(r[2]), col_key), {}) if col_key else {}
                        if meta.get("window") == "1yr":
                            cell.font = Font(color=year1_font_color, bold=cell.font.bold, italic=True)
                        evidence_item = (
                            evidence[offset - META_COLS]
                            if offset - META_COLS < len(evidence)
                            else None
                        )
                        if evidence_item:
                            cell.comment = Comment(
                                "\n".join(
                                    (
                                        f"campaign: {evidence_item.get('campaign')}",
                                        f"window: {evidence_item.get('window')}",
                                        "contract: "
                                        f"{evidence_item.get('contract_fingerprint')}",
                                        "capital accounting: "
                                        f"{evidence_item.get('capital_accounting_version')}",
                                        "average deployed: $"
                                        f"{float(evidence_item.get('average_deployed_usd') or 0):,.2f}",
                                        "benchmark deployed: $"
                                        f"{float(evidence_item.get('benchmark_deployed_usd') or 0):,.2f}",
                                    )
                                ),
                                "Codex evidence audit",
                            )
                ws.append([])   # spacer between features
            ws.column_dimensions["A"].width = 42
            ws.column_dimensions["B"].width = 42
            ws.column_dimensions["C"].width = 14
            ws.column_dimensions["D"].width = 95
            ws.freeze_panes = "E2"
        # LADDER sheet (USER 2026-07-22): follow the exposure ladder LIVE — start with NO exits
        # (~1 trade = b&h, the floor), then every exit added/adjusted, keeping a row for each
        # config and flagging the ones that IMPROVED on the running best. Then the same for
        # entries. Ordered by time so the progression reads top-to-bottom as it happens.
        try:
            lcon = sqlite3.connect(str(DB))
            lcon.execute("PRAGMA busy_timeout=60000")
            if a.campaign == CURRENT_ENGINE_CAMPAIGN:
                # The canonical workbook must not embed old ladder campaigns.
                # Current attributable cells already appear on Entry/Exit/
                # Sizing/Other; historical ladder progress belongs only in a
                # suffixed historical export.
                lrows = []
                lbase = {}
            else:
                lrows = lcon.execute(
                    "SELECT symbol, side, param, value_json, time_in_mkt_pct, acc_gain_pct, gain_per_mo, "
                    "pool_sharpe, delta_gain_mo_vs_bh, trades, source_file, ts FROM param_cells "
                    "WHERE campaign LIKE '%ladder%' ORDER BY symbol, side, ts").fetchall()
                lbase = {r[0] + "_" + r[1]: r[2] for r in lcon.execute(
                    "SELECT symbol, side, bh_pct FROM key_baseline WHERE mode='tradier'")}
            lcon.close()
        except sqlite3.OperationalError as _lexc:
            print(f"[Ladder] query failed: {_lexc}")  # never fail silently into an empty sheet
            lrows, lbase = [], {}
        wsl = wb.create_sheet("Ladder")
        wsl.append(["key", "stage", "switch", "value", "time_in_mkt_%", "acc_gain_%", "b&h_%",
                    "vs_b&h", "gain/mo", "pool_sharpe", "d_gain_mo_vs_bh", "trades", "IMPROVED?", "when"])
        best = {}
        for sym, side, param, val, tim, gain, gmo, ps, dd, tr, sf, ts in lrows:
            key = f"{sym}_{side}"
            stage = (str(sf).split("/")[1] if sf and "/" in str(sf) else "")
            bh = lbase.get(key)
            vs = round((gain or 0) - bh, 2) if bh is not None else None
            prev = best.get(key)
            improved = prev is None or (gain or 0) > prev
            if improved:
                best[key] = gain or 0
            r = [key, stage, param, val, round(tim or 0, 2), round(gain or 0, 2),
                 round(bh, 2) if bh is not None else None, vs,
                 round(gmo or 0, 4), round(ps or 0, 4), round(dd or 0, 2), tr or 0,
                 "IMPROVED" if improved else "", str(ts)[:19]]
            wsl.append(r)
            if improved:
                for c in wsl[wsl.max_row]:
                    c.fill = best_fill
            if bh is not None and (gain or 0) > bh:
                wsl.cell(row=wsl.max_row, column=8).font = Font(bold=True, color="006100")
        wsl.freeze_panes = "C2"
        # BandLadder sheet (USER 2026-07-22): per-ticker best ladder multipliers, editable.
        # These are the knobs you vary per ticker — D/4h/1h bottom & top, mode — with the best
        # found by band_ladder_sweep and its multiple of b&h alongside.
        try:
            blcon = sqlite3.connect(str(DB)); blcon.execute("PRAGMA busy_timeout=60000")
            blrows = (
                []
                if a.campaign == CURRENT_ENGINE_CAMPAIGN
                else blcon.execute(
                    "SELECT symbol, side, value_json, acc_gain_pct, delta_gain_mo_vs_bh, trades, "
                    "pool_sharpe, source_file, ts FROM param_cells WHERE campaign LIKE '%bandladder' "
                    "ORDER BY symbol, side, acc_gain_pct DESC"
                ).fetchall()
            )
            blbh = {r[0]+"_"+r[1]: (r[3]-(r[4] or 0)) for r in blrows}  # gain - delta = b&h
            blcon.close()
        except sqlite3.OperationalError:
            blrows = []
        wsb = wb.create_sheet("BandLadder")
        wsb.append(["key", "rank", "ladder (mode|D:b/t|4h:b/t|1h:b/t)", "gain %", "b&h %",
                    "xB&H", "trades", "sharpe", "beats_bh", "when"])
        seen = {}
        for sym, side, cfg, gain, dbh, tr, ps, sf, ts in blrows:
            k = f"{sym}_{side}"; seen[k] = seen.get(k, 0) + 1
            bh = (gain or 0) - (dbh or 0)
            x = (gain or 0) / bh if bh else 0
            wsb.append([k, seen[k], cfg, round(gain or 0, 1), round(bh, 1), round(x, 2),
                        tr or 0, round(ps or 0, 3), "YES" if (gain or 0) >= bh else "", str(ts)[:19]])
            if seen[k] == 1 and (gain or 0) >= bh:
                for c in wsb[wsb.max_row]:
                    c.fill = best_fill
        wsb.freeze_panes = "B2"
        wsb.column_dimensions["C"].width = 40
        # Preserve pre-repair dc_low4 measurements as gray, quarantined evidence instead of
        # silently resurrecting them into the repaired-contract matrix or deleting them. These
        # rows explain why the switch is diagnostic-only and prevent repeated blind tuning.
        wsd = wb.create_sheet("DC4 Diagnostics")
        wsd.append([
            "campaign", "key", "value", "gain/mo", "time_in_market_%",
            "trades", "delta_gain/mo_vs_B&H", "validation", "classification", "source",
        ])
        try:
            dcon = sqlite3.connect(str(DB))
            dcon.execute("PRAGMA busy_timeout=60000")
            drows = (
                []
                if a.campaign == CURRENT_ENGINE_CAMPAIGN
                else dcon.execute(
                    "SELECT campaign,symbol,side,value_json,gain_per_mo,time_in_mkt_pct,trades,"
                    "delta_gain_mo_vs_bh,validation_status,source_file "
                    "FROM param_cells WHERE mode='tradier' "
                    "AND campaign='stocks_baseline_v2_s4h' "
                    "AND param='DC_LOW4_STOP_ENABLED' "
                    "ORDER BY symbol,side,value_json"
                ).fetchall()
            )
            dcon.close()
        except sqlite3.OperationalError as _dexc:
            print(f"[DC4 Diagnostics] query failed: {_dexc}")
            drows = []
        for campaign, sym, side, val, gain, tim, trades, delta, validation, source in drows:
            wsd.append([
                campaign,
                f"{sym}_{side}",
                val,
                gain,
                tim,
                trades,
                delta,
                validation or "NULL / PRE-REPAIR",
                "QUARANTINED ENTRY-QUALITY / LOSING-CHURN EVIDENCE; DO NOT PROMOTE",
                source,
            ])
            for cell in wsd[wsd.max_row]:
                cell.fill = grey_fill
                cell.font = grey_font
        wsd.freeze_panes = "B2"
        wsd.auto_filter.ref = wsd.dimensions
        for col, width in {
            "A": 28, "B": 18, "C": 12, "D": 14, "E": 20, "F": 10,
            "G": 23, "H": 22, "I": 72, "J": 85,
        }.items():
            wsd.column_dimensions[col].width = width
        for row in wsd.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws2 = wb.create_sheet("Coverage")
        ws2.append([
            "switch", "value", "status", "keys_current_engine", "keys_inert",
            "keys_helped", "keys_hurt", "keys_stale_contract",
            "keys_engine_validation_failed", "keys_vec_only_exact_queue_empty",
        ])
        for r in coverage: ws2.append(r)
        if engine_coverage:
            wse = wb.create_sheet("Engine Coverage", 0)
            wse.append(["metric", "count", "meaning"])
            wse.append([
                "raw repaired-campaign ENGINE rows",
                engine_coverage["raw_engine_rows"],
                "Preserved exact-engine rows since the repaired cutoff, before current-contract filtering.",
            ])
            wse.append([
                "current contract ENGINE rows",
                engine_coverage["current_engine_rows"],
                "Rows whose code+NPZ+side fingerprint matches this export. Only these may fill numeric matrix cells.",
            ])
            wse.append([
                "stale contract ENGINE rows",
                engine_coverage["stale_engine_rows"],
                "Exact results from an older contract. Preserved, explicitly labelled, never copied into current cells.",
            ])
            wse.append([
                "ENGINE validation failures",
                engine_coverage["invalid_engine_rows"],
                "Exact runs that failed the repaired validation contract.",
            ])
            wse.append([
                "path-fleet V8 exact replay rows",
                len(engine_coverage["fleet_exact"]),
                "Separate exact replays. They do not fill an OFAT switch cell unless the payload explicitly identifies one attributable knob/value.",
            ])
            wse.append([
                "path-fleet exact rows attributable to switch/value",
                engine_coverage["fleet_exact_attributable"],
                "Explicit attribution only; return-% is not silently converted to gain/month.",
            ])
            wse.append([])
            wse.append([
                "status", "definition",
                "Numeric ENGINE cell remains blank unless a current contract-matched exact result exists.",
            ])
            wse.append([
                "STALE_ENGINE_CONTRACT",
                "An exact row exists for this switch/value/key, but its fingerprint is obsolete.",
            ])
            wse.append([
                "ENGINE_VALIDATION_FAILED",
                "An exact row exists, but validation failed.",
            ])
            wse.append([
                "EXACT_QUEUE_EMPTY",
                "A VEC diagnostic exists, but there is no attributable current exact-engine result.",
            ])
            wse.append([
                "NOT_ENGINE_TESTED",
                "No exact ENGINE evidence exists for this switch/value.",
            ])
            wse.freeze_panes = "A2"
            wse.column_dimensions["A"].width = 48
            wse.column_dimensions["B"].width = 18
            wse.column_dimensions["C"].width = 110
            for row in wse.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)

            wsx = wb.create_sheet("Exact Engine Evidence")
            wsx.append([
                "path_id", "key", "stage", "status", "strategy_return_%",
                "B&H_return_%", "same_entry_control_return_%",
                "matrix_param", "matrix_value", "attributable_to_switch_value",
                "artifact", "created_at",
            ])
            for exact in engine_coverage["fleet_exact"]:
                wsx.append([
                    exact["path_id"], exact["key"], exact["stage"], exact["status"],
                    exact["strategy_return_pct"], exact["bh_return_pct"],
                    exact["same_entry_control_return_pct"], exact["matrix_param"],
                    exact["matrix_value"], "YES" if exact["attributable"] else "NO",
                    exact["artifact"], exact["created_at"],
                ])
                if not exact["attributable"]:
                    for cell in wsx[wsx.max_row]:
                        cell.fill = grey_fill
                        cell.font = grey_font
            wsx.freeze_panes = "A2"
            wsx.auto_filter.ref = wsx.dimensions
            for col, width in {
                "A": 28, "B": 18, "C": 24, "D": 18, "E": 18, "F": 18,
                "G": 28, "H": 36, "I": 18, "J": 28, "K": 90, "L": 20,
            }.items():
                wsx.column_dimensions[col].width = width
        try:
            from tools.audit_stock_matrix_workbooks import universe_snapshot
        except ModuleNotFoundError:
            from audit_stock_matrix_workbooks import universe_snapshot  # type: ignore
        snapshot = universe_snapshot(BASE)
        wsu = wb.create_sheet("Universe Audit", 1)
        wsu.append(["source", "sha256", "mtime_ns", "configured_keys"])
        for source in snapshot["sources"]:
            wsu.append([
                source["path"],
                source["sha256"],
                source["mtime_ns"],
                len(snapshot["keys"]),
            ])
        wsu.append([])
        wsu.append(["configured_membership_sha256", snapshot["key_sha256"]])
        wsu.append(["active_membership_sha256", snapshot["active_key_sha256"]])
        wsu.append([
            "contract",
            "Every configured directional key must remain a column in Entry, Exit, "
            "Sizing and Other and a row in Baselines. Blank cells are valid; "
            "missing axes fail the export. ACTIVE keys come from the pinned live "
            "snapshot; HISTORICAL keys are retained evidence and are not promotion "
            "candidates.",
        ])
        wsu.append([])
        wsu.append(["key", "position_side", "universe_status"])
        active_set = set(snapshot["active_keys"])
        for key in snapshot["keys"]:
            symbol, side = key.rsplit("_", 1)
            status = (
                "ACTIVE"
                if key in active_set
                else "HISTORICAL_NOT_CURRENTLY_TRADEABLE"
            )
            wsu.append([key, side, status])
            if status != "ACTIVE":
                for cell in wsu[wsu.max_row]:
                    cell.fill = grey_fill
                    cell.font = grey_font
        wsu.freeze_panes = "A10"
        wsu.column_dimensions["A"].width = 42
        wsu.column_dimensions["B"].width = 90
        wsu.column_dimensions["C"].width = 24
        wsu.column_dimensions["D"].width = 20
        for row in wsu.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws3 = wb.create_sheet("Baselines")
        ws3.append([
            "key", "gain_per_mo", "bh_per_mo", "delta_vs_bh", "trades",
            "campaign", "window", "capital_accounting_version",
            "contract_fingerprint",
        ])
        for k in cols:
            b = base.get(k, {})
            ws3.append([
                k, b.get("gain_per_mo"), b.get("bh_per_mo"),
                b.get("delta_vs_bh"), b.get("trades"), b.get("campaign"),
                b.get("window"), b.get("capital_accounting_version"),
                b.get("contract_fingerprint"),
            ])
        wsc = wb.create_sheet("Display Completion", 0)
        display_expected = len(matrix) * len(cols)
        display_filled = (
            display_counts["exact_or_current_numeric"]
            + display_counts["recovered_pilot_v8"]
            + display_counts["preserved_v8"]
            + display_counts["vector_unique"]
        )
        if display_filled + display_counts["unresolved"] != display_expected:
            raise RuntimeError(
                "display completion count mismatch: "
                f"observed={display_filled + display_counts['unresolved']} expected={display_expected}"
            )
        wsc.append(["metric", "count", "meaning"])
        wsc.append([
            "display_cells_total", display_expected,
            "All switch/value × visible symbol/side cells in Entry, Exit, Sizing and Other.",
        ])
        wsc.append([
            "display_cells_filled", display_filled,
            "Only finite, nonzero results unique within the same symbol/side count as filled.",
        ])
        wsc.append([
            "exact_or_current_numeric", display_counts["exact_or_current_numeric"],
            "Current contract-matched numeric matrix evidence; exact credit rules remain unchanged.",
        ])
        wsc.append([
            "recovered_pilot_v8", display_counts["recovered_pilot_v8"],
            "Immutable full-precision S1 V8 evidence for the five pilots; protected from vector overwrite.",
        ])
        wsc.append([
            "preserved_v8", display_counts["preserved_v8"],
            "Historical V8 display evidence; not current promotion evidence.",
        ])
        wsc.append([
            "vector_unique", display_counts["vector_unique"],
            "Behavior-unique amber vector evidence; not exact or live promotion evidence.",
        ])
        wsc.append([
            "bh_fallback", 0,
            "PROHIBITED. B&H fallback values are never written into physical result cells.",
        ])
        wsc.append([
            "blank_display_cells", display_counts["unresolved"],
            "Untested, zero, duplicate, non-finite or nonnumeric results remain visibly unresolved.",
        ])
        wsc.append([
            "rejected_zero_cells", display_counts["rejected_zero"],
            "Measured zero results withheld from the decision surface.",
        ])
        wsc.append([
            "rejected_duplicate_cells", display_counts["rejected_duplicate"],
            "Repeated numeric results withheld; never perturbed with artificial epsilon.",
        ])
        wsc.freeze_panes = "A2"
        wsc.column_dimensions["A"].width = 34
        wsc.column_dimensions["B"].width = 18
        wsc.column_dimensions["C"].width = 110
        wse = wb.create_sheet("Evidence Provenance")
        wse.append([
            "key", "switch", "value", "delta_gain_mo_vs_bh", "campaign",
            "window", "contract_fingerprint", "capital_accounting_version",
            "average_deployed_usd", "benchmark_deployed_usd", "pool_sharpe",
            "max_dd_pct", "source_file",
        ])
        for (param, val, key), meta in sorted(cell_meta.items()):
            if (param, val, key) not in cells or cells[(param, val, key)] is None:
                continue
            wse.append([
                key, param, val, cells[(param, val, key)],
                meta.get("campaign"), meta.get("window"),
                meta.get("contract_fingerprint"),
                meta.get("capital_accounting_version"),
                meta.get("average_deployed_usd"),
                meta.get("benchmark_deployed_usd"),
                meta.get("pool_sharpe"), meta.get("max_dd_pct"),
                meta.get("source_file"),
            ])
        for (param, val, key), item in sorted(preserved_v8_index.items()):
            # Never hide recovered exact history merely because its old receipt
            # is not present locally.  The evidence class and quarantine status
            # make the distinction explicit and prevent promotion.
            wse.append([
                key, param, val, item.get("delta_gain_mo_vs_bh"),
                item.get("campaign"), "historical/preserved",
                "RECEIPT_NOT_RECOVERED",
                "PRESERVED_V8_WORKBOOK_ROW",
                None, None, None, None,
                item.get("source_file"),
            ])
            row = wse[wse.max_row]
            if item.get("preservation_display_status") != "PASS":
                for cell in row:
                    cell.fill = inert_fill
                    cell.font = red_font
        for (param, val, key), item in sorted(recovered_pilot_v8_index.items()):
            # Every recovered DB row stays visible here, including a real row
            # that cannot occupy a physical decision cell because it is zero or
            # collides with another result for the same symbol/side.
            wse.append([
                key, param, val, item.get("delta_gain_mo_vs_bh"),
                item.get("campaign"), "historical/S1-full-precision",
                item.get("contract_fingerprint"),
                item.get("capital_accounting_version"),
                item.get("average_deployed_usd"),
                item.get("benchmark_deployed_usd"),
                item.get("pool_sharpe"), item.get("max_dd_pct"),
                item.get("source_file"),
            ])
            row = wse[wse.max_row]
            row[3].comment = Comment(
                "Immutable recovered S1 V8 row; physical display status: "
                f"{item.get('physical_display_status')}; collision count within "
                f"symbol/side: {item.get('result_collision_count_within_symbol_side')}",
                "Immutable S1 V8 recovery",
            )
            if item.get("physical_display_status") != "PASS_UNIQUE_NONZERO":
                for cell in row:
                    cell.fill = inert_fill
                    cell.font = red_font
        wse.freeze_panes = "A2"
        write_vector_scalar_diagnostics_sheet(wb, vector_scalar_gap)
        try:
            from tools.lifecycle_workbook import (
                write_current_path_hotlist_sheet,
                write_lifecycle_sheet,
            )
        except ModuleNotFoundError:
            from lifecycle_workbook import (  # type: ignore
                write_current_path_hotlist_sheet,
                write_lifecycle_sheet,
            )
        write_lifecycle_sheet(wb, BASE)
        write_current_path_hotlist_sheet(wb, BASE)
        wsi = wb.create_sheet("Inventory")
        wsi.append(["setting", "tier", "exclusion_reason", "consumed_by", "description"])
        for name, tier, reason, consumed in inventory:
            wsi.append([name, tier, reason, consumed, describe_knob(name)])
        wsi.freeze_panes = "A2"
        wsi.column_dimensions["A"].width = 48
        wsi.column_dimensions["C"].width = 58
        wsi.column_dimensions["E"].width = 95
        xlsx_tmp = OUT_XLSX.with_name(
            f".{OUT_XLSX.name}.tmp.{os.getpid()}.{time.time_ns()}.xlsx"
        )
        wb.save(xlsx_tmp)
        with zipfile.ZipFile(xlsx_tmp) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise RuntimeError(f"generated workbook has corrupt member {bad_member}")
            if "xl/workbook.xml" not in archive.namelist():
                raise RuntimeError("generated workbook is missing xl/workbook.xml")
        try:
            from tools.audit_stock_matrix_workbooks import (
                assert_audit,
                audit_switch_workbook,
            )
        except ModuleNotFoundError:
            from audit_stock_matrix_workbooks import (  # type: ignore
                assert_audit,
                audit_switch_workbook,
            )
        assert_audit(
            audit_switch_workbook(
                xlsx_tmp,
                BASE,
                require_fresh=False,
            )
        )
        # The build/audit can take over a minute. A synchronized report puller
        # or older exporter may relock the destination during that interval;
        # reopen only at the atomic commit boundary and relock immediately
        # below. Never leave a writable window while serializing.
        set_local_immutable(OUT_XLSX, False)
        set_local_immutable(OUT_CSV, False)
        os.replace(xlsx_tmp, OUT_XLSX)
        os.replace(csv_tmp, OUT_CSV)
        completion_path = BASE / "data/reports/MATRIX_DISPLAY_COMPLETION_CURRENT.json"
        completion_payload = {
            "schema": "trb-matrix-display-completion-v1",
            "generated_at_epoch": time.time(),
            "exporter_source_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "status": (
                "PASS"
                if display_filled == len(matrix) * len(cols)
                and display_counts["rejected_zero"] == 0
                and display_counts["rejected_duplicate"] == 0
                else "FAIL"
            ),
            "precedence": "CURRENT_EXACT>RECOVERED_PILOT_V8>PRESERVED_V8>UNIQUE_VECTOR>UNRESOLVED",
            "uniqueness_scope": "WITHIN_SYMBOL_SIDE_ACROSS_FOUR_PATH_SHEETS",
            "global_uniqueness_scope": False,
            "matrix_rows": len(matrix),
            "visible_symbol_side_columns": len(cols),
            "display_cells_total": len(matrix) * len(cols),
            "display_cells_filled": display_filled,
            "blank_display_cells": display_counts["unresolved"],
            "bh_fallback": 0,
            **display_counts,
            "unique_nonzero_gate": display_counts["unresolved"] == 0,
            "bh_fallback_value_semantics": "PROHIBITED_COMPLETELY",
            "exact_completion_unchanged": True,
            "promotion_allowed_from_bh_fallback": False,
        }
        completion_tmp = completion_path.with_name(
            f".{completion_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        completion_tmp.write_text(
            json.dumps(completion_payload, indent=2, sort_keys=True) + "\n"
        )
        os.replace(completion_tmp, completion_path)
        if (
            a.account == "trb"
            and a.tier == "ENGINE"
            and a.campaign == CURRENT_ENGINE_CAMPAIGN
        ):
            rebind_digest_provenance_after_canonical_export(OUT_CSV, set(cols))
        # No external sync process gets a writable interval after commit.
        set_local_immutable(OUT_XLSX, True)
        set_local_immutable(OUT_CSV, True)
        xls_note = str(OUT_XLSX)
    except Exception as e:
        if xlsx_tmp is not None:
            try:
                xlsx_tmp.unlink()
            except FileNotFoundError:
                pass
        try:
            csv_tmp.unlink()
        except FileNotFoundError:
            pass
        set_local_immutable(OUT_XLSX, True)
        set_local_immutable(OUT_CSV, True)
        raise RuntimeError(
            f"refusing to replace {OUT_XLSX}; XLSX export/audit failed: {e}"
        ) from e
    tested = sum(1 for r in matrix if r[5])
    from collections import Counter as _Counter
    by_status = _Counter(r[4] for r in matrix)
    mu = sum(1 for r in matrix if r[header.index("MU_LONG")] is not None) if "MU_LONG" in cols else 0
    print(f"tier={a.tier}  rows={len(matrix)} (switch-values)  key_columns={len(cols)}  rows_with_data={tested}")
    print(f"status: " + "  ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print(f"MU_LONG filled: {mu}/{len(matrix)}")
    print(f"csv={OUT_CSV}")
    print(f"xlsx={xls_note}")


if __name__ == "__main__":
    main()
