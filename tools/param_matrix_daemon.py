#!/usr/bin/env python3
"""param_matrix_daemon.py — 24/7 parallel filler of the REAL param matrix (USER 2026-07-21).

"whoever writes to LAB_MATRIX changes to the other matrix that tests useful params" —
this daemon fills param_results_stocks.db (param_cells) — the store behind
SWITCH_MATRIX_TRB.xlsx and PARAM_BASELINE_STOCKS.xlsx — with FAITHFUL Tier-2 engine
runs (persym_baseline_campaign.run_symbol → real backtest_v8_engine, real live code),
one (symbol, param, value) unit at a time, N workers in parallel.

Differences vs the */30 cron campaign (which stays on):
  * parallel workers instead of one serialized pass (the cron's flock+run_seq was the
    6-hours-per-few-rows bottleneck)
  * PRIORITY symbols first — ARM MU NVDA ROKU AXTI HAO MNTS TTD get EVERY cell before
    the rest of symbols_trb_long/short
  * FULL manifest (no --param-limit 24)
Same store, same campaign name, same already_tested dedupe, same honest fail semantics
(engine died -> no cell, retried later). Canonical pooled CSVs remain the cron's job.

Usage (S1): PSC_CAMPAIGN=stocks_repaired_20260730_c5 python tools/param_matrix_daemon.py --tag w1
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import sqlite3
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path

os.environ.setdefault("PSC_CAMPAIGN", "stocks_repaired_20260730_c5")
SBX = Path(os.environ.get("V8_SBX", "/home/niels/binance-sandbox"))
if not SBX.exists():
    SBX = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SBX))
sys.path.insert(0, str(SBX / "tools"))
import persym_baseline_campaign as psc  # noqa: E402  (the faithful runner + metrics)
import param_results_store as prs  # noqa: E402
import universe_registry as ur  # noqa: E402
from backtest_data_contract import audit_npz  # noqa: E402
from sweep_value_semantics import executable_values, validate_test_values  # noqa: E402
import exact_wiring_gate as wiring_gate  # noqa: E402
import grouped_combo_scheduler as combo_scheduler  # noqa: E402
import matrix_live_progress  # noqa: E402
import matrix_resume_gate  # noqa: E402

CLAIM_DB = SBX / "data" / "param_matrix_claims.db"
INTERDEPENDENCY_METADATA = (
    SBX / "data" / "reports" / "switch_lab_catalog_20260729.json  # alias: SWITCH_MATRIX_INTERDEPENDENCY_20260729.json kept for backwards compat"
)
WORKER_MANIFEST = SBX / "data" / "matrix_worker_manifest.json"
MATRIX_PAUSE_FILE = SBX / "data" / "MATRIX_WORKERS_PAUSED"
PROGRESS_SNAPSHOT = Path(
    os.environ.get(
        "MATRIX_PROGRESS_SNAPSHOT",
        str(matrix_live_progress.default_snapshot_path(SBX)),
    )
)
PRIORITY_SYMS = ["MU", "HAO", "NVDA", "VT"]  # USER 2026-07-21 evening: the first four keys are
# MU_LONG, HAO_SHORT, NVDA_LONG, VT_LONG — worked ONE ticker at a time (see tools/matrix_focus.py)
BASELINE_FAIL_LIMIT = 5  # consecutive safe-baseline failures before this worker gives up
BASELINE_BACKOFF_CAP_S = 900  # 15min cap (2026-07-29: was a flat 60s spin for HOURS on TTD_SHORT)
BASELINE_IN_PROGRESS = object()
# psc.run_symbol (persym_baseline_campaign.py) raises SystemExit from TWO distinct checks, both
# reachable from this daemon's only two run_symbol call sites (ensure_safe_baseline, run_unit):
#   STARTUP_GAP        — source/NPZ changed BEFORE this call even started (checked at the top of
#                         run_symbol, no subprocess spawned yet, nothing to quarantine).
#   MID_RUN_QUARANTINE — source/NPZ changed WHILE the engine subprocess was running (checked
#                         after it exits; the stale jsonl/audit/stamp files are renamed with a
#                         .contract_changed_during_run. suffix BEFORE this raises).
# 2026-07-29: confirmed via /home/niels/logs/param_matrix_rm1.log that a worker started BEFORE
# this fix was deployed (pid 2688641, running the pre-reexec code in memory) hit STARTUP_GAP and
# died with the bare uncaught message (no re-exec) — expected for a process that predates the
# fix, not a gap in the fix itself. Both reasons are named here explicitly so a future reader
# never has to guess which one fired from a bare exception string.
CONTRACT_EXIT_STARTUP_GAP = "matrix contract source changed after worker start"
CONTRACT_EXIT_MID_RUN = "matrix contract changed during engine run"
FULL_C5_CAMPAIGN = "stocks_repaired_20260730_c5"
TEMP_ONE_YEAR_C5_CAMPAIGN = "stocks_repaired_20260730_c5_1yr"
FULL_WINDOW_MIN_YEARS = 2.0
RECENT_FULL_EXACT_LOOKBACK_DAYS = 31
GROUPED_COMBO_PARAM = "GROUP_COMBO"
GROUPED_RECIPE_MARKER = "__GROUPED_RECIPE__"
GROUPED_COMBO_HISTORICAL_SEED_CAMPAIGNS = (
    "stocks_repaired_20260725_c2",
    "stocks_repaired_20260725_c1",
)


class ProductionProgressHeartbeat:
    """Truthful daemon-owned progress; never inferred from report/sync freshness."""

    def __init__(
        self,
        slot,
        worker_id,
        symbol,
        side,
        *,
        path=PROGRESS_SNAPSHOT,
        interval_seconds=5.0,
        throughput_window_seconds=600,
        throughput_query_interval_seconds=30.0,
        db_path=prs.DB_PATH,
        manifest_path=WORKER_MANIFEST,
        autostart=True,
    ):
        self.slot = int(slot)
        self.worker_id = str(worker_id)
        self.path = Path(path)
        self.interval_seconds = max(2.0, float(interval_seconds))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.symbol = str(symbol or "—").upper()
        self.side = str(side or "—").upper()
        self.db_path = Path(db_path)
        self.manifest_path = Path(manifest_path)
        self.throughput_window_seconds = max(
            60, int(throughput_window_seconds)
        )
        self.throughput_query_interval_seconds = max(
            2.0, float(throughput_query_interval_seconds)
        )
        self._accepted_per_minute = 0.0
        self._throughput_status = "UNAVAILABLE"
        self._throughput_source = "param_results_stocks.db:param_cells"
        self._last_throughput_query_at = float("-inf")
        self._rejected = 0
        self._quarantined = 0
        self._current = {
            "parameter": "—",
            "value": "—",
            "symbol": self.symbol,
            "side": self.side,
            "combination": "—",
            "stage": "STARTUP",
            "status": "INITIALIZING",
            "worker_cells_remaining": None,
        }
        self._thread = None
        if autostart:
            self._thread = threading.Thread(
                target=self._loop,
                name=f"matrix-progress-{self.slot}",
                daemon=True,
            )
            self._thread.start()

    def _refresh_authoritative_throughput(self, now, *, force=False):
        if (
            not force
            and now - self._last_throughput_query_at
            < self.throughput_query_interval_seconds
        ):
            return
        self._last_throughput_query_at = now
        try:
            payload = matrix_resume_gate.accepted_exact_throughput(
                self.db_path,
                self.manifest_path,
                window_seconds=self.throughput_window_seconds,
                now_epoch=now,
                symbol=self.symbol,
                side=self.side,
            )
            rate = payload.get("accepted_exact_cells_per_minute")
            if payload.get("status") != "PASS" or rate is None:
                self._accepted_per_minute = 0.0
                self._throughput_status = str(
                    payload.get("reason") or "UNAVAILABLE"
                )
            else:
                self._accepted_per_minute = max(0.0, float(rate))
                self._throughput_status = "AUTHORITATIVE_EXACT_PASS"
            self._throughput_source = str(
                payload.get("source")
                or "param_results_stocks.db:param_cells"
            )
        except Exception as exc:
            # Fail closed: an unavailable acceptance query is zero/unknown,
            # never a write-derived or freshness-derived positive number.
            self._accepted_per_minute = 0.0
            self._throughput_status = f"UNAVAILABLE:{type(exc).__name__}"

    def set_current(self, **updates):
        with self._lock:
            for field, value in updates.items():
                if value is not None:
                    self._current[field] = value
        self.emit()

    def set_remaining(self, remaining):
        with self._lock:
            self._current["worker_cells_remaining"] = (
                None if remaining is None else max(0, int(remaining))
            )
        self.emit()

    def persisted(self):
        """A DB write is not acceptance; force a fresh exact-PASS query."""
        with self._lock:
            self._current["status"] = "PERSISTED_AWAITING_EXACT_PASS_QUERY"
        self.emit(force_throughput_refresh=True)

    def rejected(self, count=1, *, status="REJECTED"):
        with self._lock:
            self._rejected += max(0, int(count))
            self._current["status"] = status
        self.emit()

    def quarantined(self, count=1, *, status="QUARANTINED"):
        with self._lock:
            self._quarantined += max(0, int(count))
            self._current["status"] = status
        self.emit()

    def emit(self, now_epoch=None, *, force_throughput_refresh=False):
        now = time.time() if now_epoch is None else float(now_epoch)
        with self._lock:
            self._refresh_authoritative_throughput(
                now, force=force_throughput_refresh
            )
            current = dict(self._current)
            accepted_per_minute = self._accepted_per_minute
            rejected = self._rejected
            quarantined = self._quarantined
        matrix_live_progress.update_worker(
            self.path,
            self.slot,
            worker_id=self.worker_id,
            parameter=current.get("parameter"),
            value=current.get("value"),
            symbol=current.get("symbol"),
            side=current.get("side"),
            combination=current.get("combination"),
            stage=current.get("stage"),
            status=current.get("status"),
            worker_cells_remaining=current.get("worker_cells_remaining"),
            accepted_cells_per_minute=accepted_per_minute,
            accepted_throughput_status=self._throughput_status,
            accepted_throughput_source=self._throughput_source,
            accepted_window_seconds=self.throughput_window_seconds,
            rejected_cells=rejected,
            quarantined_cells=quarantined,
            now_epoch=now,
        )

    def _loop(self):
        while not self._stop.wait(self.interval_seconds):
            try:
                self.emit()
            except Exception as exc:
                print(
                    f"[{self.worker_id}] progress heartbeat failed: {exc!r}",
                    flush=True,
                )

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1.0)


def progress_slot_for_tag(manifest_path, tag):
    """Resolve the stable 1..6 slot from current manifest order."""
    try:
        payload = json.loads(Path(manifest_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    matches = [
        index
        for index, row in enumerate(payload.get("workers") or [], start=1)
        if isinstance(row, dict) and str(row.get("tag")) == str(tag)
    ]
    if len(matches) != 1 or not 1 <= matches[0] <= matrix_live_progress.WORKER_SLOTS:
        return None
    return matches[0]


def _progress(ctx):
    return ctx.get("progress")


def evidence_campaigns_for_dedupe(campaign):
    """Return campaigns that may already satisfy a logical matrix cell.

    The temporary one-year continuation fills gaps only. It must not rerun or
    replace a valid full-window c5 cell. Full-window work never treats shorter
    evidence as complete.
    """
    if campaign == TEMP_ONE_YEAR_C5_CAMPAIGN:
        return (FULL_C5_CAMPAIGN, TEMP_ONE_YEAR_C5_CAMPAIGN)
    return (campaign,)


def _cell_value_aliases(value):
    """Return canonical and legacy spellings used in ``value_json``."""
    aliases = {str(value)}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        parsed = value
    aliases.add(str(parsed))
    try:
        aliases.add(json.dumps(parsed, sort_keys=True, separators=(",", ":")))
        aliases.add(json.dumps(value, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError):
        pass
    return tuple(sorted(aliases))


def _canonical_override_value(value):
    """Type-sensitive JSON identity for one persisted override value."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None


def _historical_override_provenance(
    param,
    expected_value,
    db_overrides_json,
    artifact_overrides,
):
    """Compare a DB receipt with its immutable override artifact.

    Protected historical artifacts may contain settings added after the DB row
    was written. The canonical DB mapping must therefore be an exact,
    type-sensitive subset of the artifact mapping. Artifact-only keys are
    classified as legacy provenance; any missing or mismatched DB key fails.
    """
    try:
        db_overrides = (
            json.loads(db_overrides_json)
            if isinstance(db_overrides_json, str)
            else db_overrides_json
        )
    except (TypeError, json.JSONDecodeError):
        return {
            "valid": False,
            "reason": "DB_OVERRIDES_INVALID",
            "artifact_only_keys": (),
        }
    if not isinstance(db_overrides, dict) or not isinstance(
        artifact_overrides, dict
    ):
        return {
            "valid": False,
            "reason": "OVERRIDES_NOT_MAPPINGS",
            "artifact_only_keys": (),
        }
    for name, db_value in db_overrides.items():
        if name not in artifact_overrides:
            return {
                "valid": False,
                "reason": f"ARTIFACT_MISSING_DB_KEY:{name}",
                "artifact_only_keys": (),
            }
        if _canonical_override_value(db_value) != _canonical_override_value(
            artifact_overrides[name]
        ):
            return {
                "valid": False,
                "reason": f"DB_ARTIFACT_VALUE_MISMATCH:{name}",
                "artifact_only_keys": (),
            }

    param = str(param)
    parsed_value = expected_value
    if isinstance(expected_value, str):
        try:
            parsed_value = json.loads(expected_value)
        except json.JSONDecodeError:
            pass
    if param not in HELPER_PARAMS:
        if param not in db_overrides or param not in artifact_overrides:
            return {
                "valid": False,
                "reason": "TESTED_PARAM_MISSING",
                "artifact_only_keys": (),
            }
        expected_canonical = _canonical_override_value(parsed_value)
        if (
            _canonical_override_value(db_overrides[param])
            != expected_canonical
            or _canonical_override_value(artifact_overrides[param])
            != expected_canonical
        ):
            return {
                "valid": False,
                "reason": "TESTED_PARAM_VALUE_MISMATCH",
                "artifact_only_keys": (),
            }
    elif not db_overrides:
        # STOP_PACK/TF_EXCLUDE values name a mapping rather than a literal
        # override key. The exception is warranted only when the DB records a
        # non-empty mapping and that complete mapping is proven above.
        return {
            "valid": False,
            "reason": "HELPER_MAPPING_EMPTY",
            "artifact_only_keys": (),
        }

    artifact_only = tuple(sorted(set(artifact_overrides) - set(db_overrides)))
    return {
        "valid": True,
        "reason": (
            "LEGACY_ARTIFACT_SUPERSET"
            if artifact_only
            else "EXACT_OVERRIDE_MATCH"
        ),
        "artifact_only_keys": artifact_only,
    }


@lru_cache(maxsize=65536)
def _historical_exact_receipt_valid(
    root,
    campaign,
    symbol,
    side,
    param,
    expected_value,
    db_overrides_json,
    source_file,
    contract_fingerprint,
    trades_fingerprint,
    expected_trades,
):
    """Validate the immutable exact ledger/audit pair behind an old DB row."""
    prefix = f"param_matrix_daemon/{campaign}/"
    source_file = str(source_file or "")
    if not source_file.startswith(prefix):
        return False
    tag = source_file[len(prefix) :]
    if not tag or "/" in tag or tag in {".", ".."}:
        return False
    cell_dir = (
        Path(root)
        / "data"
        / "sweep_results"
        / f"persym_campaign_{campaign}_trades"
        / tag
    )
    audit_path = cell_dir / f"audit__{symbol}.json"
    ledger_path = cell_dir / f"cell__{symbol}.jsonl"
    override_path = cell_dir / f"override__{symbol}.json"
    if (
        not audit_path.is_file()
        or not ledger_path.is_file()
        or not override_path.is_file()
    ):
        return False
    try:
        audit = json.loads(audit_path.read_text())
        overrides = json.loads(override_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    override_provenance = _historical_override_provenance(
        param,
        expected_value,
        db_overrides_json,
        overrides,
    )
    if not override_provenance["valid"]:
        return False
    if audit.get("status") != "PASS":
        return False
    if audit.get("contract_fingerprint") != contract_fingerprint:
        return False
    audit_trade_fingerprint = audit.get("trade_fingerprint")
    if (
        audit_trade_fingerprint
        and audit_trade_fingerprint != trades_fingerprint
    ):
        return False
    ledger_trades = 0
    try:
        with ledger_path.open() as handle:
            for line in handle:
                trade = json.loads(line)
                trade_side = str(
                    trade.get("position_side") or trade.get("side") or ""
                ).upper()
                if trade_side == str(side).upper() and trade.get("pnl_pct") is not None:
                    ledger_trades += 1
    except (OSError, json.JSONDecodeError):
        return False
    return ledger_trades > 0 and ledger_trades == int(expected_trades or 0)


def has_recent_full_exact_evidence(
    con,
    symbol,
    side,
    param,
    value,
    *,
    now=None,
    lookback_days=RECENT_FULL_EXACT_LOOKBACK_DAYS,
):
    """Return whether recent valid >=2yr exact evidence owns this cell.

    This exception applies only to the temporary one-year continuation.  It
    deliberately accepts a different historical exact fingerprint: a later
    source refresh must not cause the short lane to recompute and displace a
    sound full-window result.  Vector, invalid, unaudited, zero-close, and
    shorter-window evidence never suppresses work.
    """
    if psc.CAMPAIGN != TEMP_ONE_YEAR_C5_CAMPAIGN:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(
        days=int(lookback_days)
    )
    aliases = _cell_value_aliases(value)
    placeholders = ",".join("?" for _ in aliases)
    candidates = con.execute(
        "SELECT c.campaign,c.value_json,c.overrides_json,c.source_file,"
        "c.contract_fingerprint,c.trades_fingerprint,c.trades "
        "FROM param_cells c "
        "JOIN key_baseline b ON b.mode=c.mode AND b.symbol=c.symbol "
        "AND b.side=c.side AND b.campaign=c.campaign "
        "WHERE c.mode=? AND c.symbol=? AND c.side=? AND c.param=? "
        f"AND c.value_json IN ({placeholders}) "
        "AND c.campaign<>? AND c.campaign NOT LIKE '%_1yr' "
        "AND COALESCE(c.tier,'ENGINE')='ENGINE' "
        "AND c.validation_status='PASS' "
        "AND c.contract_fingerprint IS NOT NULL "
        "AND COALESCE(c.real_closes,0)>0 AND c.ts>=? "
        "AND b.years>=?",
        (
            psc.MODE,
            str(symbol).upper(),
            str(side).upper(),
            str(param),
            *aliases,
            psc.CAMPAIGN,
            cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            FULL_WINDOW_MIN_YEARS,
        ),
    ).fetchall()
    return any(
        _historical_exact_receipt_valid(
            str(SBX),
            campaign,
            str(symbol).upper(),
            str(side).upper(),
            str(param),
            value_json,
            overrides_json,
            source_file,
            contract_fingerprint,
            trades_fingerprint,
            trades,
        )
        for (
            campaign,
            value_json,
            overrides_json,
            source_file,
            contract_fingerprint,
            trades_fingerprint,
            trades,
        ) in candidates
    )


@lru_cache(maxsize=1)
def _matrix_knob_registry():
    path = SBX / "data" / "knob_registry.json"
    raw = json.loads(path.read_text())
    registry = raw.get("tradier")
    if not isinstance(registry, dict):
        raise RuntimeError(f"MATRIX_KNOB_REGISTRY_INVALID: {path}")
    return registry


@lru_cache(maxsize=1)
def _matrix_activation_dependencies():
    try:
        payload = json.loads(INTERDEPENDENCY_METADATA.read_text())
        return {
            str(row["param"]): tuple(
                str(dep)
                for dep in (row.get("activation_dependencies") or [])
                if not str(dep).startswith("CONTRACT_")
            )
            for row in payload.get("paths", [])
            if isinstance(row, dict) and row.get("param")
        }
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"MATRIX_ACTIVATION_DEPENDENCIES_INVALID: {exc}"
        ) from exc


def _enable_activation_dependencies(param, effective, seen=None):
    """Recursively turn on explicit parent switches for one isolated path."""
    seen = set() if seen is None else seen
    if param in seen:
        return
    seen.add(param)
    registry = _matrix_knob_registry()
    for parent in _matrix_activation_dependencies().get(param, ()):
        member = registry.get(parent, {})
        kind = member.get("kind")
        off = member.get("off_value")
        default = member.get("default")
        if kind == "bool":
            effective[parent] = not bool(off)
        elif default is not None and default != off:
            effective[parent] = default
        else:
            raise RuntimeError(
                f"MATRIX_ACTIVATION_PARENT_HAS_NO_ON_VALUE: {param}->{parent}"
            )
        _enable_activation_dependencies(parent, effective, seen)


@lru_cache(maxsize=1)
def _matrix_ladder_floor_cached():
    """Audited stage-0 contract: ordinary ladder, with every strategy exit off.

    The old safe baseline passed an empty override, so all live-default exits
    competed at once.  A cell then changed one knob on top of that churn rather
    than adding one path to the B&H/ladder floor.  Import lazily to avoid the
    exposure_ladder -> persym_baseline_campaign import cycle at module load.
    """
    import exposure_ladder

    floor = dict(exposure_ladder.all_exits_off())
    # A hold-only control must also disable competing entry/augment/re-entry
    # families.  Leaving WT_3M_FORCE_OPEN (and similar readers) live made the
    # MU baseline request multi-unit adds which were then clipped by the live
    # per-fire sizing path: 23 clamps, 0.4859 requested/fill ratio, and a
    # permanently unavailable exact worker despite the $16k account ceiling
    # itself being respected.  The engine supplies the single canonical $2k
    # seed and mandatory post-exit reclaim independently; an isolated cell or
    # grouped recipe re-enables only the entry family it is testing.
    floor.update(exposure_ladder.all_entries_off())
    # These newer band/ladder controls are not all named like exits, so the
    # generic pattern-based off set leaves them live.  The repaired matrix
    # baseline is a hold-only B&H control; otherwise ordinary ladder/E02 can
    # close the seed and leave a reclaim obligation latched at the data end.
    floor.update(
        {
            "LR_BAND_ENTRY_ENABLED": False,
            "LR_BAND_ENTRY_PRIORITY": False,
            "LR_BAND_REGIME_ENABLED": False,
            "LR_BAND_LADDER_ENABLED": False,
            "LR_BAND_LADDER_ORDINARY_PARITY_ENABLED": False,
            "LR_BAND_E02_EXIT_ENABLED": False,
            "BREAKOUT_SIZE_LADDER_ENABLED": False,
        }
    )
    required = {
        "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED": False,
        "WT_CROSSUNDER_FINAL_ENABLED": False,
        "MTF_DC_REJECT_EXIT_ENABLED": False,
        "LONG_STRUCT_EXIT_TF": "None",
        "SHORT_STRUCT_EXIT_TF": "None",
        "WT_DC_EXIT_THRESHOLD": 9999.0,
        "WT_3M_FORCE_OPEN_ENABLED": False,
        "WT_DC_ENTRY_THRESHOLD": 9999.0,
        "TRA_WT_DC_ENTRY_THRESHOLD": 9999.0,
    }
    wrong = {
        name: (floor.get(name), expected)
        for name, expected in required.items()
        if floor.get(name) != expected
    }
    if wrong or len(floor) < 100:
        raise RuntimeError(
            f"MATRIX_LADDER_FLOOR_INCOMPLETE: n={len(floor)} wrong={wrong}"
        )
    return floor


def matrix_ladder_floor_overrides():
    """Return a fresh copy so callers cannot mutate the cached floor."""
    return dict(_matrix_ladder_floor_cached())


def matrix_cell_overrides(pname, cell_overrides):
    """Add one cell/family to the all-exits-off ladder floor.

    Companion parameters (thresholds, TFs, filters) are meaningless while
    their family master remains off.  Registry family membership is therefore
    used to enable only that exit family's boolean masters before applying the
    requested cell value.  Other exit families remain disabled.
    """
    effective = matrix_ladder_floor_overrides()
    _enable_activation_dependencies(pname, effective)
    row = _matrix_knob_registry().get(pname, {})
    if row.get("group") == "EXIT":
        family = row.get("family")
        if family:
            for name, member in _matrix_knob_registry().items():
                if (
                    member.get("family") == family
                    and member.get("group") == "EXIT"
                    and member.get("role") == "MAIN_SWITCH"
                    and member.get("kind") == "bool"
                ):
                    effective[name] = not bool(member.get("off_value", False))
    _apply_mtf_exit_companion_overrides(pname, effective)
    effective.update(cell_overrides)
    return effective


def is_grouped_recipe_cell(pname, cell_overrides):
    return (
        str(pname) == GROUPED_COMBO_PARAM
        and isinstance(cell_overrides, dict)
        and isinstance(cell_overrides.get(GROUPED_RECIPE_MARKER), dict)
    )


def grouped_recipe_effective_overrides(cell_overrides):
    """Validate and unwrap one scheduler recipe for the exact engine."""
    marker = (cell_overrides or {}).get(GROUPED_RECIPE_MARKER)
    overrides = marker.get("overrides") if isinstance(marker, dict) else None
    recipe_id = marker.get("recipe_id") if isinstance(marker, dict) else None
    if (
        not isinstance(recipe_id, str)
        or not recipe_id.startswith("gcr-")
        or not isinstance(overrides, dict)
        or not overrides
    ):
        raise RuntimeError("GROUPED_RECIPE_INVALID")
    effective = matrix_ladder_floor_overrides()
    effective.update(overrides)
    return effective


def _recipe_overlay_from_effective(effective):
    """Remove the common ladder floor without losing activation companions."""
    floor = matrix_ladder_floor_overrides()
    missing = object()
    overlay = {}
    for name, value in effective.items():
        baseline = floor.get(
            name, getattr(wiring_gate.TradierConfig, name, missing)
        )
        if baseline is missing or value != baseline:
            overlay[name] = value
    return overlay


def matrix_recipe_master_enabled_override(pname, symbol=None, side=None):
    """Return the master state the isolated exact cell actually executes.

    The generic wiring gate inspects the live/default config.  That is correct
    for ad-hoc probes, but the repaired matrix deliberately enables a disabled
    family master around each sub-setting.  Feeding the live ``False`` into
    the preflight gate would reject that sub-setting before the engine ever
    sees the isolated recipe.
    """
    master = wiring_gate.master_state(pname, symbol, side)
    master_name = master.get("master")
    if not master_name:
        return None
    effective = matrix_cell_overrides(pname, {})
    if master_name not in effective:
        return None
    return bool(effective[master_name])


def matrix_cell_is_baseline_alias(pname, cell_overrides):
    """Whether the complete isolated recipe is identical to the ladder floor.

    Comparing only ``cell_overrides[pname]`` is wrong for sub-settings: their
    default threshold/TF is still a real path test because
    :func:`matrix_cell_overrides` also enables the disabled family master.
    Compare every effective override against the floor or the config fallback
    so only a genuinely identical recipe is elided.
    """
    if is_grouped_recipe_cell(pname, cell_overrides):
        return False
    registry_row = _matrix_knob_registry().get(pname, {})
    if (
        registry_row.get("group") == "EXIT"
        and registry_row.get("role") == "MAIN_SWITCH"
        and registry_row.get("kind") == "bool"
        and pname in cell_overrides
        and cell_overrides[pname] == registry_row.get("off_value")
    ):
        # Companion TF/lookback values are behaviorally irrelevant when the
        # only enabled family master is explicitly set back to its off value.
        # Counting those companions as a recipe difference wastes an exact
        # run and paints a guaranteed baseline fingerprint as an inert cell.
        return True
    floor = matrix_ladder_floor_overrides()
    effective = matrix_cell_overrides(pname, cell_overrides)
    missing = object()
    for name, value in effective.items():
        baseline_value = floor.get(
            name, getattr(wiring_gate.TradierConfig, name, missing)
        )
        if baseline_value is missing or value != baseline_value:
            return False
    return True


def reexec_self(reason, worker):
    """Catch-and-reload for BOTH psc.run_symbol contract-exit reasons (STARTUP_GAP and
    MID_RUN_QUARANTINE above). Whichever fired, persym_baseline_campaign.py has already done
    the correct thing on its side (MID_RUN_QUARANTINE: renamed the stale in-flight result before
    raising; STARTUP_GAP: nothing ran yet, nothing to quarantine) — this function's only job is
    making sure the WORKER does not exit and stay dead (2026-07-28 23:06: 8 workers died this way
    on a config_tradier.py sync and nothing relaunched them). _MATRIX_PROCESS_SOURCE_SIGNATURE
    and matrix_contract_fingerprint() are computed once at module import, so the only correct
    reload is a fresh interpreter — os.execv keeps the same PID (claims in CLAIM_DB stay valid)
    and re-imports every contract file from disk, so the next pass is fingerprinted against the
    NEW contract, never the stale one."""
    if CONTRACT_EXIT_STARTUP_GAP in reason:
        kind = "STARTUP_GAP"
    elif CONTRACT_EXIT_MID_RUN in reason:
        kind = "MID_RUN_QUARANTINE"
    else:
        kind = "UNKNOWN_SYSTEMEXIT"
    print(
        f"[{worker}] contract-reload trigger={kind}: {reason} — any in-flight result was already "
        "quarantined by persym_baseline_campaign.py before this raised; re-exec'ing a fresh "
        "interpreter to reload the contract (never continuing under a stale fingerprint)",
        flush=True,
    )
    sys.stdout.flush()
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except OSError as exc:
        print(f"[{worker}] re-exec FAILED ({exc!r}) — exiting for the watchdog to relaunch", flush=True)
        sys.exit(1)


def claims():
    con = sqlite3.connect(str(CLAIM_DB), timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS claims (unit TEXT PRIMARY KEY, worker TEXT, ts REAL)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS wiring_gate_receipts "
        "(receipt TEXT PRIMARY KEY, ts REAL)"
    )
    con.commit()
    return con


def release_dead_claims(ccon):
    """Drop claims held by workers that no longer exist.

    2026-07-21: claims are named 'pmx:<tag>:<pid>' and honoured for 1800s. After every fleet
    restart the dead generation's claims kept the whole queue locked for up to 30 minutes, so
    a freshly launched fleet sat idle instead of running engines (measured: 14 workers, 2 live
    engines). A claim whose PID is gone is not work in progress — it is debris."""
    try:
        rows = ccon.execute("SELECT unit, worker FROM claims").fetchall()
    except sqlite3.OperationalError:
        return 0
    dead = []
    for unit, worker in rows:
        pid = str(worker).rsplit(":", 1)[-1]
        if pid.isdigit() and not Path(f"/proc/{pid}").exists():
            dead.append(unit)
    for i in range(0, len(dead), 500):
        try:
            chunk = dead[i:i + 500]
            ccon.execute(f"DELETE FROM claims WHERE unit IN ({','.join('?' * len(chunk))})", chunk)
            ccon.commit()
        except sqlite3.OperationalError:
            break
    return len(dead)


def ordered_syms(first=None):
    syms = [s for s in (first or [])]
    for s in PRIORITY_SYMS + _universe_syms():
        if s not in syms:
            syms.append(s)
    return syms


def _universe_syms():
    out = []
    for fn in ("symbols_trb_long.json", "symbols_trb_short.json"):
        p = SBX / fn
        if p.exists():
            for s in json.loads(p.read_text()):
                if s.upper() not in out:
                    out.append(s.upper())
    return out


def drop_verdicts():
    """USER 2026-07-21: params useless on all tested keys move to the BACKGROUND of the
    queue (still filled eventually — never removed). Source: PARAM_KEEP_DROP.csv."""
    p = SBX / "data" / "reports" / "PARAM_KEEP_DROP.csv"
    dropped = set()
    if p.exists():
        import csv as _csv
        try:
            for r in _csv.DictReader(open(p)):
                if r.get("verdict") in ("DROP_INERT", "DROP_NEG"):
                    dropped.add(r["param"])
        except Exception:
            pass
    return dropped


def useless_knobs():
    """Only cohort-receipted lifecycle retirements may leave the work-list.

    A RECONNECT/DEGENERATE observation is diagnostic evidence, not a skip
    token.  Bible §16.24 requires an exact current-contract 1/5 cohort (or a
    explicitly justified 1/10 exception) across both applicable directions.
    Older ``reconnect``/``degenerate`` arrays stay visible for audit but are
    deliberately ignored here.
    """
    p = SBX / "data" / "useless_knobs.json"
    if not p.exists():
        return set()
    try:
        d = json.loads(p.read_text())
        retired = set()
        for receipt in d.get("approved_retirements", []):
            if not isinstance(receipt, dict):
                continue
            try:
                param = str(receipt["param"])
                population = int(receipt["eligible_symbol_sides"])
                tested = {
                    str(key).upper()
                    for key in receipt["tested_symbol_sides"]
                }
                fraction = float(receipt["cohort_fraction"])
                directions = {
                    key.rsplit("_", 1)[1]
                    for key in tested
                    if "_" in key
                }
            except (KeyError, TypeError, ValueError):
                continue
            required = (
                (population + 4) // 5 if fraction == 0.20
                else (population + 9) // 10 if fraction == 0.10
                else None
            )
            if (
                receipt.get("campaign") != psc.CAMPAIGN
                or required is None
                or population < 2
                or len(tested) < max(2, required)
                or len(directions) < 2
                or receipt.get("exact_current_contract") is not True
                or receipt.get("all_action_fingerprints_unique") is not True
                or receipt.get("all_capital_receipts_normalized_2000") is not True
                or receipt.get("all_lifecycle_roles_tested") is not True
                or (
                    fraction == 0.10
                    and not str(receipt.get("one_tenth_exception_reason") or "").strip()
                )
            ):
                continue
            retired.add(param)
        return retired
    except Exception:
        return set()


DIAGNOSTIC_ONLY_PARAMS = {
    "DC_LOW4_STOP_ENABLED",
    "R1_DC_LOW4_3M_EMERGENCY_ENABLED",
}
HELPER_PARAMS = frozenset({"STOP_PACK", "TF_EXCLUDE"})
# Exact c2/c4 evidence is hypothesis priority, never promotion evidence.  These
# three families produced the strongest pilot deltas and are therefore the
# first c5 lifecycle probes: SRS for MU/NVDA/VT, MTF_DC_REJECT for TTD and
# WT_DC_EXIT for ACN.  Their current c5 rows must still pass every exact gate.
HISTORICAL_EXIT_PRIORITY_FAMILIES = (
    "STRUCTURAL_RANGE_SHIFT",
    "MTF_DC_REJECT",
    "WT_DC_EXIT",
)
ENTRY_INERT_MIN_TIM_PCT = 1.0
ENTRY_BINDING_LAYERS = frozenset({"FAMILY_MASTER", "ENTRY_SOURCE"})
SOURCE_BACKED_BINDINGS = frozenset(
    {"DIRECT_LIVE_CONFIG_READ", "TOKEN_ONLY_LIVE_REFERENCE"}
)
# c5 companion repair (orchestration only; DO NOT copy these edges into
# switch_lab_catalog_20260729.json  # alias: SWITCH_MATRIX_INTERDEPENDENCY_20260729.json kept for backwards compat because that file is hashed into
# the current exact contract).  all_exits_off() deliberately writes TF=None.
# Re-enabling an MTF family without restoring its live companion defaults
# therefore creates a nominal "enabled" cell that can never read indicators.
MTF_EXIT_COMPANION_OVERRIDES = {
    "MTF_DC_REJECT": {
        "MTF_EXIT_USE_COMPOUND": True,
        "MTF_DC_REJECT_EXIT_TF": "1h",
        "MTF_DC_REJECT_EXIT_LOOKBACK": 5,
    },
    "MTF_BB_REJECT": {
        "MTF_EXIT_USE_COMPOUND": True,
        "MTF_BB_REJECT_EXIT_TF": "1h",
        "MTF_BB_REJECT_EXIT_LOOKBACK": 5,
    },
    "MTF_WT_CROSS": {
        "MTF_EXIT_USE_COMPOUND": True,
        "MTF_WT_CROSS_EXIT_TF": "15m",
        # Despite the switch name, the evaluator branch requires BOTH the WT
        # direction and the GR/WT multi-TF gate.
        "MTF_GR_EXIT_GATE_ENABLED": True,
        "MTF_GR_EXIT_MIN_TFS": 3,
    },
}
MTF_EXIT_EXPECTED_HASHED_DEPENDENCIES = {
    "MTF_DC_REJECT_EXIT_ENABLED": ("MTF_EXIT_USE_COMPOUND",),
    "MTF_BB_REJECT_EXIT_ENABLED": (),
    "MTF_WT_CROSS_EXIT_ENABLED": (),
}


def mtf_exit_companion_overrides(param):
    """Return the audited live-default companion recipe for one MTF family."""
    registry = _matrix_knob_registry()
    row = registry.get(str(param), {})
    family = row.get("family")
    required = MTF_EXIT_COMPANION_OVERRIDES.get(family)
    if not required:
        return {}
    masters = [
        name
        for name, member in registry.items()
        if member.get("family") == family
        and member.get("group") == "EXIT"
        and member.get("role") == "MAIN_SWITCH"
    ]
    if len(masters) != 1:
        raise RuntimeError(
            f"MTF_COMPANION_MASTER_INVALID: {family} masters={masters}"
        )
    master = masters[0]
    expected_dependencies = MTF_EXIT_EXPECTED_HASHED_DEPENDENCIES.get(
        master
    )
    actual_dependencies = _matrix_activation_dependencies().get(
        master,
        (),
    )
    if expected_dependencies is None or tuple(actual_dependencies) != tuple(
        expected_dependencies
    ):
        raise RuntimeError(
            f"MTF_COMPANION_HASHED_METADATA_DRIFT: {family} "
            f"expected={expected_dependencies} actual={actual_dependencies}"
        )
    wrong = {}
    for name, value in required.items():
        member = registry.get(name)
        configured = getattr(wiring_gate.TradierConfig, name, object())
        if not isinstance(member, dict):
            wrong[name] = "missing registry row"
        elif member.get("default") != value:
            wrong[name] = {
                "registry_default": member.get("default"),
                "required": value,
            }
        elif configured != value:
            wrong[name] = {
                "config_default": configured,
                "required": value,
            }
    if wrong:
        raise RuntimeError(
            "MTF_COMPANION_DEFAULT_DRIFT: "
            + json.dumps(wrong, sort_keys=True, default=str)
        )
    return dict(required)


MATRIX_FAMILY_COMPANION_OVERRIDES = {
    "WT_3M_FORCE": {
        # The exact ladder starts with one $2,000 seed.  The live default
        # target is also $2,000, so BUILD_TO_TARGET correctly reports "no
        # room" before the enabled/ladder/multiplier probes can execute.
        # Use the already-audited $16k backtest strategy-capacity ceiling as a
        # matrix-only isolation sentinel.  This never changes live config.
        "WT_3M_FORCE_OPEN_BUILD_TO_TARGET": True,
        "WT_3M_FORCE_OPEN_TARGET_USD": 16000.0,
        "WT_3M_FORCE_OPEN_USE_SMA200": True,
    },
    "DELTA_EXIT": {
        # all_exits_off() nulls both selector fields and raises the no-loss
        # threshold to an unreachable sentinel.  Re-enabling only the boolean
        # therefore cannot reach DeltaTracker's decision branch.
        "DELTA_ENGINE_ENABLED": True,
        "DELTA_EXIT_TF": "15m",
        "DELTA_EXIT_TYPE": "speed_decay",
        "NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.01,
    },
    "MTF_EXIT_USE": {
        # This is an umbrella master, not an exit by itself.  Give its isolated
        # true probe one representative, already-audited child.  The explicit
        # false value is still recognized as a ladder-floor baseline alias.
        "MTF_DC_REJECT_EXIT_ENABLED": True,
        "MTF_DC_REJECT_EXIT_TF": "1h",
        "MTF_DC_REJECT_EXIT_LOOKBACK": 5,
    },
    "WT_DC_EXIT": {
        # all_exits_off() disables this boolean family with numeric sentinels
        # too. Re-enabling the master without restoring them is not executable.
        "WT_DC_EXIT_THRESHOLD": 30,
        "WT_DC_EXIT_STALE_MAX_S": 600,
    },
    "STDEV_REJECT_EXIT": {
        # The STDEV branch reads bb_pct_b_{TF} and is nested inside the WT/DC
        # scorer-HOLD branch. Keep WT/DC reachable but impossible to fire so
        # any close remains attributable to STDEV_REJECT.
        "STDEV_REJECT_EXIT_TF": "D",
        "WT_DC_EXIT_ENABLED": True,
        "WT_DC_EXIT_THRESHOLD": 9999.0,
        "WT_DC_EXIT_STALE_MAX_S": 600,
    },
    "STDEV_BB_RZ": {
        "STDEV_BB_RZ_EXIT_TF": "D",
        "WT_DC_EXIT_ENABLED": True,
        "WT_DC_EXIT_THRESHOLD": 9999.0,
        "WT_DC_EXIT_STALE_MAX_S": 600,
    },
}


def exit_family_companion_overrides(param):
    """Return audited companions needed by one isolated matrix recipe.

    The historical function name is retained because workers may already have
    imported it.  Companions can now repair entry or exit recipe binding.
    """
    registry = _matrix_knob_registry()
    family = (registry.get(str(param)) or {}).get("family")
    required = dict(mtf_exit_companion_overrides(param))
    required.update(MATRIX_FAMILY_COMPANION_OVERRIDES.get(family, {}))
    wrong = {}
    for name, value in required.items():
        member = registry.get(name)
        configured = getattr(wiring_gate.TradierConfig, name, object())
        is_isolation_sentinel = (
            (
                family in {"STDEV_REJECT_EXIT", "STDEV_BB_RZ"}
                and name == "WT_DC_EXIT_THRESHOLD"
                and value == 9999.0
            )
            or (
                family == "WT_3M_FORCE"
                and name == "WT_3M_FORCE_OPEN_TARGET_USD"
                and value == 16000.0
            )
        )
        if not isinstance(member, dict):
            wrong[name] = "missing registry row"
        elif not is_isolation_sentinel and member.get("default") != value:
            wrong[name] = {
                "registry_default": member.get("default"),
                "required": value,
            }
        elif not is_isolation_sentinel and configured != value:
            wrong[name] = {
                "config_default": configured,
                "required": value,
            }
    if wrong:
        raise RuntimeError(
            "EXIT_COMPANION_DEFAULT_DRIFT: "
            + json.dumps(wrong, sort_keys=True, default=str)
        )
    return required


def _apply_mtf_exit_companion_overrides(param, effective):
    """Restore companions nulled by the ladder floor before the test value."""
    effective.update(exit_family_companion_overrides(param))


def mtf_companion_receipt_valid(param, overrides):
    """Whether a stored exact row used the repaired exit companion recipe."""
    required = exit_family_companion_overrides(param)
    if not required:
        return True
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides)
        except json.JSONDecodeError:
            return False
    return isinstance(overrides, dict) and all(
        overrides.get(name) == value for name, value in required.items()
    )


def wrong_account_namespace(param, account=None):
    """Return True for a knob owned by another Tradier account.

    Historical ``TRA_*`` and control-account ``TRC_*`` settings were being
    exact-tested inside the TRB matrix.  They cannot affect a TRB run and
    accounted for 37 already-proven MU reconnect cells.
    """
    account = str(account or psc.ACCOUNT).upper()
    if account == "TRB":
        return param.startswith(("TRA_", "TRC_"))
    if account == "TRC":
        return param.startswith(("TRA_", "TRB_"))
    if account == "TRA":
        return param.startswith(("TRB_", "TRC_"))
    return False


def is_manifest_cell(cell):
    """Helper packs are diagnostics, not manifest path coverage."""
    return str(cell[0]) not in HELPER_PARAMS


def baseline_is_entry_inert(metrics):
    """Only an effectively flat ladder floor is entry-inert.

    Zero real closes is the expected all-exits-off Stage-0 result, not evidence
    of a dead entry path. A highly exposed seed/ladder floor is exactly what
    exit families need in order to create comparable closes.
    """
    metrics = metrics or {}
    tim_pct = float(metrics.get("time_in_mkt_pct", 0) or 0)
    return tim_pct < ENTRY_INERT_MIN_TIM_PCT


def load_interdependency_rows(metadata_path, manifest_path):
    """Load the reviewed dependency inventory or fail the safe lane closed.

    Entry-inert scheduling relies on executable path roles, not name guesses.
    A missing file, malformed row, or manifest path without metadata therefore
    stops the lane instead of silently reopening exit/filter/helper work.
    """
    metadata_path = Path(metadata_path)
    manifest_path = Path(manifest_path)
    try:
        payload = json.loads(metadata_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            "INTERDEPENDENCY_METADATA_REQUIRED: cannot schedule an entry-inert "
            f"safe-contract baseline without {metadata_path}: {exc}"
        )
    paths = payload.get("paths") if isinstance(payload, dict) else None
    if not isinstance(paths, list):
        raise SystemExit(
            "INTERDEPENDENCY_METADATA_REQUIRED: metadata has no paths list"
        )
    rows = {}
    for row in paths:
        if not isinstance(row, dict) or not row.get("param"):
            raise SystemExit(
                "INTERDEPENDENCY_METADATA_REQUIRED: malformed path row"
            )
        rows[str(row["param"])] = row
    try:
        manifest_payload = json.loads(manifest_path.read_text())
        manifest_params = {
            str(name)
            for name, spec in manifest_payload.get("params", {}).items()
            if isinstance(spec, dict)
            and bool(spec.get("sweepable"))
            and "OPTION" not in str(name)
        }
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            "INTERDEPENDENCY_METADATA_REQUIRED: cannot validate metadata "
            f"against {manifest_path}: {exc}"
        )
    missing = sorted(manifest_params - set(rows))
    if missing:
        preview = ",".join(missing[:5])
        raise SystemExit(
            "INTERDEPENDENCY_METADATA_REQUIRED: dependency inventory is "
            f"missing {len(missing)} manifest path(s): {preview}"
        )
    return rows


def load_worker_priority_policy(
    manifest_path,
    tag,
    symbol,
    side,
    *,
    campaign,
    contract_version,
    contract_fingerprint,
):
    """Load one fail-closed current-contract scheduling policy from the manifest.

    The manifest changes queue order only.  It cannot weaken the exact engine
    identity or promote a result: campaign, contract version, key, current
    fingerprint, and the explicit no-promotion flag must all match first.
    A tag absent from the manifest keeps the caller's CLI behavior so ad-hoc
    diagnostic workers remain possible and visible to the fleet audit.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"MATRIX_WORKER_MANIFEST_INVALID: {exc}") from exc
    matches = [
        row
        for row in (payload.get("workers") or [])
        if isinstance(row, dict) and str(row.get("tag")) == str(tag)
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise SystemExit(
            f"MATRIX_WORKER_MANIFEST_INVALID: tag {tag!r} appears {len(matches)} times"
        )
    row = matches[0]
    observed_key = (
        str(row.get("symbol") or "").upper(),
        str(row.get("side") or "").upper(),
    )
    expected_key = (str(symbol).upper(), str(side).upper())
    checks = {
        "campaign": (payload.get("campaign"), campaign),
        "matrix_contract_version": (
            payload.get("matrix_contract_version"),
            contract_version,
        ),
        "worker_key": (observed_key, expected_key),
        "expected_contract_fingerprint": (
            row.get("expected_contract_fingerprint"),
            contract_fingerprint,
        ),
        "no_live_promotion": (payload.get("no_live_promotion"), True),
    }
    mismatches = {
        name: {"manifest": actual, "runtime": expected}
        for name, (actual, expected) in checks.items()
        if actual != expected
    }
    if mismatches:
        raise SystemExit(
            "MATRIX_WORKER_MANIFEST_CONTRACT_MISMATCH: "
            + json.dumps(mismatches, sort_keys=True, default=str)
        )
    roots = tuple(
        str(value)
        for value in (row.get("priority_roots") or [])
        if str(value)
    )
    if not roots:
        raise SystemExit(
            f"MATRIX_WORKER_MANIFEST_INVALID: {tag} has no priority_roots"
        )
    return {
        "priority_roots": roots,
        "all_tiers": bool(row.get("all_tiers", True)),
        "selection_mode": str(
            row.get("selection_mode") or "DEPENDENCY_PACKS_ONLY"
        ),
    }


def _depends_on_priority_root(param, roots, dependency_rows, seen=None):
    if param in roots:
        return True
    seen = set() if seen is None else set(seen)
    if param in seen:
        return False
    seen.add(param)
    row = dependency_rows.get(param) or {}
    return any(
        dep in dependency_rows
        and _depends_on_priority_root(dep, roots, dependency_rows, seen)
        for dep in (row.get("activation_dependencies") or [])
    )


def dependency_pack_params(priority_roots, dependency_rows):
    """Return roots, their dependent knobs, and their activation ancestors.

    Descendants are discovered only from the requested roots.  Activation
    ancestors are then added without expanding through their other children,
    which prevents a shared parent such as ``MTF_EXIT_USE_COMPOUND`` from
    silently reopening an unrelated broad OFAT queue.
    """
    roots = {str(value) for value in priority_roots if str(value)}
    missing = sorted(roots - set(dependency_rows))
    if missing:
        raise SystemExit(
            "MATRIX_PRIORITY_ROOT_UNKNOWN: " + ",".join(missing)
        )
    non_exit = sorted(
        root
        for root in roots
        if (dependency_rows.get(root) or {}).get("group") != "EXIT"
    )
    if non_exit:
        raise SystemExit(
            "MATRIX_PRIORITY_ROOT_NOT_EXIT: " + ",".join(non_exit)
        )
    selected = {
        param
        for param in dependency_rows
        if _depends_on_priority_root(param, roots, dependency_rows)
    }
    ancestors = list(selected)
    while ancestors:
        param = ancestors.pop()
        for dep in (
            (dependency_rows.get(param) or {}).get(
                "activation_dependencies"
            )
            or []
        ):
            if dep in dependency_rows and dep not in selected:
                selected.add(dep)
                ancestors.append(dep)
    return selected


def prioritize_dependency_pack_cells(cells, priority_roots, dependency_rows):
    """Select and order meaningful exact exit dependency packs.

    Family masters and activation parents lead; source/filter values follow in
    reviewed precedence order.  Within each stage every parameter receives its
    two maximally separated wiring-smoke values before any parameter receives a
    third grid value.  This keeps the exact gate useful without letting a
    five-value range on one early parameter starve every later path.

    Helper STOP_PACK/TF_EXCLUDE rows and unrelated ENTRY/SIZING OFAT cells are
    deliberately absent.  This function changes scheduling only: it neither
    fabricates results nor writes to the result store.
    """
    roots = {str(value) for value in priority_roots if str(value)}
    selected = dependency_pack_params(roots, dependency_rows)
    filtered = [
        cell
        for cell in cells
        if is_manifest_cell(cell) and str(cell[0]) in selected
    ]
    present = {str(cell[0]) for cell in filtered}
    missing = sorted(roots - present)
    if missing:
        raise SystemExit(
            "MATRIX_PRIORITY_ROOT_NOT_EXECUTABLE: " + ",".join(missing)
        )

    def param_sort_key(pname):
        pname = str(pname)
        row = dependency_rows.get(pname) or {}
        is_ancestor = pname not in roots and any(
            root in selected
            and _depends_on_priority_root(root, {pname}, dependency_rows)
            for root in roots
        )
        if is_ancestor:
            stage = 0
        elif pname in roots:
            stage = 1
        else:
            stage = 2
        return (
            stage,
            int(row.get("precedence_order") or 999),
            str(row.get("family") or ""),
            pname,
        )

    # ``all_cells`` deliberately puts the two maximally separated non-default
    # values first for every parameter.  Preserve that intra-parameter order,
    # but schedule the first two as a smoke block before any middle/default
    # tail values.  Stages remain strict: activation ancestors complete before
    # family masters, and masters complete before dependent settings.
    by_param = {}
    for cell in filtered:
        by_param.setdefault(str(cell[0]), []).append(cell)
    ordered_params = sorted(by_param, key=param_sort_key)
    out = []
    for stage in (0, 1, 2):
        stage_params = [
            pname
            for pname in ordered_params
            if param_sort_key(pname)[0] == stage
        ]
        for pname in stage_params:
            out.extend(by_param[pname][:2])
        max_tail = max(
            (len(by_param[pname]) - 2 for pname in stage_params),
            default=0,
        )
        for tail_index in range(max_tail):
            for pname in stage_params:
                values = by_param[pname]
                index = tail_index + 2
                if index < len(values):
                    out.append(values[index])
    return out


def prioritize_compatibility_cells(cells, dropped, dependency_rows=None):
    """Order broad exact work by complete action lifecycle.

    The c5 candidate gate correctly rejects a strategy that never closes.  The
    old alphabetical compatibility order nevertheless sent ENTRY/filter/sizing
    cells first on top of the all-exits-off hold control.  Those cells could not
    exercise EXIT→REENTER and therefore burned engine hours on guaranteed
    ``no real close`` failures.

    Lead with EXIT families (their safe-contract recipes include the required
    reclaim companions), then explicit REENTER/RECLAIM, ENTRY, AUGMENT, REDUCE,
    sizing and unresolved cross-cutting work.  As with dependency packs, give
    every parameter two maximally separated smoke values before letting a
    long numeric grid monopolize the queue.  This changes scheduling only.
    """
    dependency_rows = dependency_rows or {}

    def phase(pname):
        pname = str(pname)
        upper = pname.upper()
        row = dependency_rows.get(pname) or {}
        group = str(row.get("group") or "OTHER").upper()
        family = str(row.get("family") or "").upper()
        if group == "EXIT":
            if family in HISTORICAL_EXIT_PRIORITY_FAMILIES:
                return 0
            # Ablations disable existing actions; they do not introduce the
            # first close into an all-exits-off control and are therefore tail
            # diagnostics, not close-producing lifecycle starters.
            if family.startswith("ABLATION_"):
                return 8
            return 1
        if "REENTRY" in upper or "REENTER" in upper or "RECLAIM" in upper:
            return 2
        if group == "ENTRY" and "AUGMENT" not in upper and "REDUCE" not in upper:
            return 3
        if "AUGMENT" in upper or "PYRAMID" in upper or "ADD_" in upper:
            return 4
        if "REDUCE" in upper or "PARTIAL" in upper or "TRIM" in upper:
            return 5
        if group == "SIZING":
            return 6
        return 7

    def param_key(pname):
        row = dependency_rows.get(str(pname)) or {}
        return (
            str(pname) in HELPER_PARAMS,
            str(pname) in dropped,
            phase(pname),
            int(row.get("precedence_order") or 999),
            str(row.get("family") or ""),
            str(pname),
        )

    by_param = {}
    for cell in cells:
        by_param.setdefault(str(cell[0]), []).append(cell)
    params = sorted(by_param, key=param_key)
    out = []
    # Helpers and structurally dropped rows remain at the tail, while each
    # normal action phase gets a breadth-first two-value wiring block.
    for helper_or_dropped in (False, True):
        selected = [
            pname
            for pname in params
            if bool(
                pname in HELPER_PARAMS or pname in dropped
            )
            == helper_or_dropped
        ]
        for action_phase in range(9):
            phase_params = [
                pname for pname in selected if phase(pname) == action_phase
            ]
            for pname in phase_params:
                out.extend(by_param[pname][:2])
            max_tail = max(
                (len(by_param[pname]) - 2 for pname in phase_params),
                default=0,
            )
            for tail_index in range(max_tail):
                for pname in phase_params:
                    values = by_param[pname]
                    index = tail_index + 2
                    if index < len(values):
                        out.append(values[index])
    return out


def is_source_backed_entry_binding_probe(cell, dependency_rows):
    """True only for an exact-testable entry source/master with source evidence."""
    if not is_manifest_cell(cell):
        return False
    pname = str(cell[0])
    row = (dependency_rows or {}).get(pname)
    if not isinstance(row, dict):
        return False
    upper = pname.upper()
    consumed_by = row.get("consumed_by") or {}
    return (
        row.get("group") == "ENTRY"
        and row.get("precedence_layer") in ENTRY_BINDING_LAYERS
        and row.get("role") not in {"FILTER", "CONDITION", "TF"}
        and "REENTRY" not in upper
        and "RECLAIM" not in upper
        and row.get("binding_evidence") in SOURCE_BACKED_BINDINGS
        and bool(consumed_by.get("live"))
        and "EXACT_V8" in (row.get("screen_backends") or [])
        and row.get("deployment_scope") != "NONDEPLOYABLE_NO_LIVE_READ"
    )


def gate_cells_for_safe_baseline(cells, baseline_metrics, dependency_rows):
    """Restrict an entry-inert key to probes that can make entries exist."""
    if not baseline_is_entry_inert(baseline_metrics):
        return list(cells)
    return [
        cell
        for cell in cells
        if is_source_backed_entry_binding_probe(cell, dependency_rows)
    ]


def all_cells(manifest_path, all_tiers=False, side=None, include_diagnostics=False):
    cells = [("STOP_PACK", name, cfg) for name, cfg in psc.STOP_PACKS.items()]
    cells += [("TF_EXCLUDE", name.replace("TF_EXCLUDE_", ""), cfg) for name, cfg in psc.TF_EXCLUDE_PACKS.items()]
    # USER 2026-07-21 vectorize-everything: VEC_SCREEN-tier params are screened by the
    # vectorized lane (vec_screen_daemon via v8_vec_sweep) — the slow Tier-2 engine only
    # spends minutes-per-cell on params that NEED the real engine (ENGINE_SCREEN/LIVECALL).
    # --all-tiers overrides that split: the vec screen has been caught silently no-opping knobs
    # it does not implement (WT_DC_ENTRY_THRESHOLD defaults to 0.0 there vs 45 live, so every
    # override >=35 returned 0 trades at every value), so a key that must be COMPLETE and
    # trustworthy gets every manifest param measured by the real engine.
    import json as _json
    tiers = {}
    try:
        tiers = {k: (v.get("sweep_tier") if isinstance(v, dict) else None)
                 for k, v in _json.loads(Path(manifest_path).read_text()).get("params", {}).items()}
    except Exception:
        pass
    dead = useless_knobs()
    for pname, values in psc.load_params(manifest_path, 0):
        if wrong_account_namespace(pname):
            continue
        if pname in DIAGNOSTIC_ONLY_PARAMS and not include_diagnostics:
            continue  # preserved in reports; explicit opt-in only, never blind matrix fill
        if not all_tiers and tiers.get(pname) == "VEC_SCREEN":
            continue
        if "OPTION" in pname:
            continue  # USER 2026-07-21: not trading options — do not test options params yet
        if pname in dead:
            continue  # proven unable to return a number — see tools/prune_useless_knobs.py
        # A LONG-only focus key cannot be moved by a SHORT-side knob (and vice versa): the
        # engine run costs the same 12 minutes and informs the other side only.
        if side and f"_{'SHORT' if side == 'LONG' else 'LONG'}" in pname.upper():
            continue
        default = getattr(wiring_gate.TradierConfig, pname, None)
        validation = validate_test_values(pname, default, values)
        executable = executable_values(validation)
        if executable is None:
            continue
        # Two maximally separated non-default values are the wiring smoke.
        # Middle values wait until those prove the knob changes a schedule.
        nondefault = [value for value in executable if value != default]
        smoke_order = []
        if nondefault:
            smoke_order.append(nondefault[0])
        if len(nondefault) > 1 and nondefault[-1] != nondefault[0]:
            smoke_order.append(nondefault[-1])
        ordered_values = smoke_order + [
            value for value in nondefault if value not in smoke_order
        ]
        if default in executable:
            ordered_values.append(default)
        for v in ordered_values:
            # value_json is a DB contract, not merely a display label.  Preserve
            # JSON type information so booleans round-trip as true/false instead
            # of the invalid Python strings "True"/"False".
            cells.append(
                (
                    pname,
                    json.dumps(v, sort_keys=True, separators=(",", ":")),
                    {pname: v},
                )
            )
    return cells


def grouped_combo_cells(con, symbol, side, dependency_rows):
    """Plan grouped recipes from current receipt-valid exact path results."""
    accepted = sorted(psc.matrix_contract_fingerprints(symbol, side))
    if not accepted:
        return []
    current_campaigns = evidence_campaigns_for_dedupe(psc.CAMPAIGN)
    campaigns = tuple(
        dict.fromkeys(
            current_campaigns + GROUPED_COMBO_HISTORICAL_SEED_CAMPAIGNS
        )
    )
    rows = con.execute(
        "SELECT c.rowid,c.campaign,c.param,c.value_json,"
        "c.delta_gain_mo_vs_bh,c.overrides_json,c.contract_fingerprint,"
        "c.trades_fingerprint,c.ts,c.source_file,c.trades,b.years "
        "FROM param_cells c JOIN key_baseline b ON "
        "b.mode=c.mode AND b.campaign=c.campaign AND b.symbol=c.symbol "
        "AND b.side=c.side WHERE c.mode=? "
        f"AND c.campaign IN ({','.join('?' for _ in campaigns)}) "
        "AND c.symbol=? AND c.side=? AND c.tier='ENGINE' "
        "AND c.validation_status='PASS' AND c.real_closes>0 "
        "AND c.capital_accounting_version=? "
        "AND c.contract_fingerprint IS NOT NULL "
        "AND c.trades_fingerprint IS NOT NULL "
        "AND c.param NOT IN (?,?,?)",
        (
            psc.MODE,
            *campaigns,
            symbol,
            side,
            psc.CAPITAL_ACCOUNTING_VERSION,
            "STOP_PACK",
            "TF_EXCLUDE",
            GROUPED_COMBO_PARAM,
        ),
    ).fetchall()
    best = {}
    full_window_by_param = {}
    for (
        rowid,
        campaign,
        param,
        value_json,
        delta_bh,
        overrides_json,
        contract_fp,
        trades_fp,
        ts,
        source_file,
        trades,
        years,
    ) in rows:
        is_current = campaign in current_campaigns
        if is_current:
            if contract_fp not in accepted:
                continue
        else:
            historical_contract_ok = (
                (
                    campaign == "stocks_repaired_20260725_c1"
                    and str(contract_fp).startswith(
                        "tradier-matrix-c1-20260725:"
                    )
                )
                or (
                    campaign == "stocks_repaired_20260725_c2"
                    and str(contract_fp).startswith(
                        (
                            "tradier-matrix-c2-20260725:",
                            "tradier-matrix-exec-c3-20260729:",
                            "tradier-matrix-exec-c4-20260729:",
                        )
                    )
                )
            )
            if (
                not historical_contract_ok
                or float(years or 0) < FULL_WINDOW_MIN_YEARS
                or not _historical_exact_receipt_valid(
                    str(SBX),
                    campaign,
                    str(symbol).upper(),
                    str(side).upper(),
                    str(param),
                    value_json,
                    overrides_json,
                    source_file,
                    contract_fp,
                    trades_fp,
                    trades,
                )
            ):
                continue
        metadata = dependency_rows.get(str(param)) or {}
        action_group = combo_scheduler.classify_action_group(metadata)
        if action_group is None:
            continue
        try:
            stored_effective = json.loads(overrides_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(stored_effective, dict):
            continue
        if is_current:
            effective = stored_effective
        else:
            try:
                requested_value = json.loads(str(value_json))
            except (TypeError, json.JSONDecodeError):
                requested_value = value_json
            # Historical rows prioritize a value; they do not smuggle an old
            # production baseline into c5. Rebuild the path with today's
            # isolated safe-contract activation and companion recipe.
            effective = matrix_cell_overrides(
                str(param), {str(param): requested_value}
            )
        overlay = _recipe_overlay_from_effective(effective)
        if not overlay:
            continue
        priority = float(delta_bh) if delta_bh is not None else float("-inf")
        candidate = combo_scheduler.Arm(
            param=str(param),
            value_json=str(value_json),
            overrides=overlay,
            action_group=action_group,
            family=str(metadata.get("family") or ""),
            timeframe=str(metadata.get("timeframe") or ""),
            priority=priority,
            receipt=(
                f"{'current' if is_current else 'historical-not-current'}:"
                f"{campaign}:{rowid}:{contract_fp}:{trades_fp}:{ts or ''}"
            ),
        )
        old = best.get(str(param))
        candidate_full = float(years or 0) >= FULL_WINDOW_MIN_YEARS
        old_full = full_window_by_param.get(str(param), False)
        if old is None or (candidate_full, candidate.priority, candidate.arm_id) > (
            old_full,
            old.priority,
            old.arm_id,
        ):
            # Frozen dataclasses intentionally reject ad-hoc provenance fields.
            # Keep full-window ownership separately while retaining Arm purity.
            best[str(param)] = candidate
            full_window_by_param[str(param)] = candidate_full

    completed = set()
    for (value_json,) in con.execute(
        "SELECT value_json FROM param_cells WHERE mode=? "
        f"AND campaign IN ({','.join('?' for _ in current_campaigns)}) "
        "AND symbol=? AND side=? AND param=? AND tier='ENGINE' "
        "AND validation_status='PASS' AND capital_accounting_version=? "
        f"AND contract_fingerprint IN ({','.join('?' for _ in accepted)})",
        (
            psc.MODE,
            *current_campaigns,
            symbol,
            side,
            GROUPED_COMBO_PARAM,
            psc.CAPITAL_ACCOUNTING_VERSION,
            *accepted,
        ),
    ):
        try:
            payload = json.loads(value_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("recipe_id"):
            completed.add(str(payload["recipe_id"]))

    recipes = combo_scheduler.plan_recipes(
        f"{symbol}_{side}",
        best.values(),
        completed_recipe_ids=completed,
    )
    return [
        (
            GROUPED_COMBO_PARAM,
            recipe.logical_value_json,
            {
                GROUPED_RECIPE_MARKER: {
                    "recipe_id": recipe.recipe_id,
                    "overrides": dict(recipe.overrides),
                    "member_receipts": [
                        member.receipt for member in recipe.members
                    ],
                    "logical_value_json": recipe.logical_value_json,
                }
            },
        )
        for recipe in recipes
    ]


def only_params(cells, requested):
    """Restrict a one-shot worker to explicit canonical parameter names."""
    names = {
        value.strip().upper()
        for value in str(requested or "").split(",")
        if value.strip()
    }
    if not names:
        return cells
    return [cell for cell in cells if str(cell[0]).upper() in names]


def _audit_db_fields(audit):
    result = (audit or {}).get("result", {})
    return {
        "validation_status": (audit or {}).get("status"),
        "contract_fingerprint": (audit or {}).get("contract_fingerprint"),
        "real_closes": int(float(result.get("real_closes", 0) or 0)),
        "mtm_count": int(float(result.get("mtm_count", 0) or 0)),
        "opens_long": int(float(result.get("opens_long", 0) or 0)),
        "opens_short": int(float(result.get("opens_short", 0) or 0)),
        "requested_fill_ratio": float(result.get("requested_fill_ratio", 0) or 0),
        "size_clamp_count": int(float(result.get("size_clamp_count", 0) or 0)),
        "reentry_pending": int(float(result.get("reentry_pending", 0) or 0)),
        "reentry_violations": int(float(result.get("reentry_violations", 0) or 0)),
        "result_audit_json": json.dumps(audit, sort_keys=True),
    }


def ensure_safe_baseline(ctx, sym, side):
    """Create the exact-contract, side-isolated B&H-seeded baseline for one key.

    ``BASELINE_IN_PROGRESS`` means another live worker owns the exact same
    baseline claim.  It is contention, not evidence that an engine/audit run
    failed, and callers must not advance their persistent-failure counter.
    """
    con = ctx["con"]
    fp = psc.matrix_contract_fingerprint(sym, side)
    accepted = sorted(psc.matrix_contract_fingerprints(sym, side))
    placeholders = ",".join("?" for _ in accepted)
    row = con.execute(
        "SELECT gain_per_mo,trades_fingerprint,real_closes,time_in_mkt_pct "
        "FROM key_baseline "
        "WHERE mode=? AND campaign=? AND symbol=? AND side=? "
        "AND validation_status IN ('PASS','PASS_CONTROL_NO_CLOSE') "
        "AND capital_accounting_version=? "
        f"AND contract_fingerprint IN ({placeholders})",
        (
            psc.MODE,
            psc.CAMPAIGN,
            sym,
            side,
            psc.CAPITAL_ACCOUNTING_VERSION,
            *accepted,
        ),
    ).fetchone()
    if row:
        ctx["base"][f"{sym}_{side}"] = row[0]
        ctx["base_fp"][f"{sym}_{side}"] = row[1]
        ctx["base_metrics"][f"{sym}_{side}"] = {
            "real_closes": row[2],
            "time_in_mkt_pct": row[3],
        }
        return True
    if not claim(ctx, f"BASELINE|{sym}|{side}|{fp}"):
        return BASELINE_IN_PROGRESS
    tag = f"__BASELINE_SAFE__{side}"
    baseline_overrides = matrix_ladder_floor_overrides()
    by_side = psc.run_symbol(
        sym,
        baseline_overrides,
        tag,
        timeout=ctx["args"].timeout,
        min_avail=ctx["args"].min_avail,
        side=side,
        require_matrix_contract=True,
        allow_no_real_close_control=True,
    )
    if by_side is None:
        return False
    audit = psc.load_matrix_run_audit(tag, sym)
    trades = by_side[side]
    years, bh_long = psc.sym_years_and_bh(sym, psc.START)
    if years is None or bh_long is None:
        print(f"[{ctx['worker']}] {sym}_{side}: baseline missing B&H inputs", flush=True)
        return False
    m = psc.capital_key_metrics(
        trades, years, bh_long, side, (audit or {}).get("result", {})
    )
    trade_fp = prs.trades_fingerprint(
        trades, contract_version=psc.MATRIX_CONTRACT_VERSION
    )
    prs.upsert_baseline(
        con,
        {
            "mode": psc.MODE,
            "account": psc.ACCOUNT,
            "symbol": sym,
            "side": side,
            "campaign": psc.CAMPAIGN,
            "ts": psc.now_iso(),
            "window_start": psc.START,
            "years": round(years, 6),
            **m,
            "overrides_json": json.dumps(
                baseline_overrides, sort_keys=True, separators=(",", ":")
            ),
            "stamp": psc.stamp(),
            "source_file": f"param_matrix_daemon/{psc.CAMPAIGN}/{tag}",
            "tier": "ENGINE",
            "trades_fingerprint": trade_fp,
            **_audit_db_fields(audit),
        },
    )
    con.commit()
    ctx["base"][f"{sym}_{side}"] = m["gain_per_mo"]
    ctx["base_fp"][f"{sym}_{side}"] = trade_fp
    ctx["base_metrics"][f"{sym}_{side}"] = {
        "real_closes": _audit_db_fields(audit)["real_closes"],
        "time_in_mkt_pct": m.get("time_in_mkt_pct"),
    }
    _delta_vs_bh = m.get("delta_gain_mo_vs_bh")
    _capture_vs_bh = m.get("capture_vs_bh")
    if (
        _delta_vs_bh is not None
        and float(_delta_vs_bh) > 0
        and _capture_vs_bh is not None
        and float(_capture_vs_bh) >= 2.0
    ):
        _performance_verdict = "MEETS_2X_BH_RESEARCH_BAR"
    elif _delta_vs_bh is not None and float(_delta_vs_bh) > 0:
        _performance_verdict = "ABOVE_BH_BUT_BELOW_2X_NONPROMOTABLE"
    else:
        _performance_verdict = "BELOW_BH_DISCARD_EVIDENCE"
    print(
        f"[{ctx['worker']}] baseline {sym}_{side}: gain/mo={m['gain_per_mo']} "
        f"bh/mo={m['bh_per_mo']} "
        f"structural_validation={audit.get('status') if audit else 'MISSING'} "
        f"performance_verdict={_performance_verdict}",
        flush=True,
    )
    return True


def run_unit(ctx, sym, pname, vlabel, ovr):
    """One faithful Tier-2 engine unit: claim, run, ingest both sides. Returns wrote-any.

    Every row is stamped tier='ENGINE' and carries the realised trade fingerprint, so the
    exporter can tell knob effect from tier gap and from an override the code never read."""
    a = ctx["args"]
    sides = [a.side] if a.safe_contract else [
        s for s in ("LONG", "SHORT") if not a.side or s == a.side
    ]
    need = [s for s in sides if not ctx["tested_local"](s, pname, vlabel)]
    if not need:
        return False
    grouped_recipe = is_grouped_recipe_cell(pname, ovr)
    if ctx["args"].safe_contract:
        if matrix_cell_is_baseline_alias(pname, ovr):
            alias = {
                "verdict": "BASELINE_ALIAS_NOT_EXECUTED",
                "skip_remaining_exact": True,
                "reason": "complete isolated recipe equals accepted baseline",
            }
            record_gate_event(
                ctx, sym, ctx["args"].side, pname, vlabel, alias
            )
            return False
        gate = (
            {
                "verdict": "GROUPED_COMBO_MEMBERS_RECEIPT_VALID",
                "skip_remaining_exact": False,
                "reason": (
                    "members selected only from current-fingerprint exact PASS "
                    "rows with current capital accounting"
                ),
            }
            if grouped_recipe
            else exact_gate_verdict(ctx, sym, ctx["args"].side, pname)
        )
        if gate["skip_remaining_exact"]:
            record_gate_event(ctx, sym, ctx["args"].side, pname, vlabel, gate)
            # A two-corner wiring smoke can prioritize investigation, but it
            # is not proof that a path/combination loses across every TF and
            # the full registered setting range.  Preserve the RED diagnostic
            # and continue exact execution; only the literal baseline alias
            # above is a duplicate recipe that can be omitted safely.
    display_value = json.dumps(
        next(iter(ovr.values()), vlabel),
        sort_keys=True,
        separators=(",", ":"),
    )
    if grouped_recipe:
        recipe_id = ovr[GROUPED_RECIPE_MARKER]["recipe_id"]
        tag = f"{GROUPED_COMBO_PARAM}__{recipe_id}"
    else:
        tag = re.sub(
            r"[^A-Za-z0-9_.=-]+", "_", f"{pname}__{display_value}"
        )[:120]
    smoke_unit = None
    if (
        ctx["args"].safe_contract
        and gate["verdict"] == "NEEDS_TWO_VALUE_SMOKE"
    ):
        smoke_unit = f"SMOKE|{sym}|{ctx['args'].side}|{pname}"
        if not claim(ctx, smoke_unit):
            return False
    if not claim(ctx, f"{sym}|{tag}"):
        if smoke_unit:
            release_claim(ctx, smoke_unit)
        return False
    if grouped_recipe:
        record_grouped_recipe_provenance(
            ctx, sym, ctx["args"].side, ovr[GROUPED_RECIPE_MARKER]
        )
    try:
        if grouped_recipe:
            effective_ovr = grouped_recipe_effective_overrides(ovr)
        else:
            effective_ovr = (
                matrix_cell_overrides(pname, ovr)
                if ctx["args"].safe_contract
                else ovr
            )
        heartbeat = _progress(ctx)
        if heartbeat:
            if grouped_recipe:
                combination = str(ovr[GROUPED_RECIPE_MARKER]["recipe_id"])
            else:
                companions = sorted(
                    name for name in effective_ovr if name != pname
                )
                combination = "+".join([str(pname), *companions])
            heartbeat.set_current(
                parameter=pname,
                value=vlabel,
                symbol=sym,
                side=(a.side if a.safe_contract else ",".join(need)),
                combination=combination,
                stage="ENGINE_V8",
                status="TESTING",
            )
        by_side = psc.run_symbol(
            sym,
            effective_ovr,
            tag,
            timeout=a.timeout,
            min_avail=a.min_avail,
            side=(a.side if a.safe_contract else None),
            require_matrix_contract=a.safe_contract,
        )
        if by_side is None:
            if heartbeat:
                heartbeat.rejected(status="REJECTED_ENGINE_NO_RESULT")
            return False
        wrote = ingest(
            ctx, sym, by_side, need, pname, vlabel, effective_ovr, tag
        )
        return commit_unit(ctx, sym, tag, wrote)
    finally:
        if smoke_unit:
            release_claim(ctx, smoke_unit)


def exact_gate_verdict(ctx, sym, side, pname):
    """Classify one current-contract parameter without historical leakage."""
    fp = psc.matrix_contract_fingerprint(sym, side)
    base = ctx["base_fp"].get(f"{sym}_{side}")
    rows = []
    for (
        value_json,
        value_num,
        trade_fp,
        inert,
        status,
        overrides_json,
    ) in ctx["con"].execute(
        "SELECT value_json,value_num,trades_fingerprint,inert,"
        "validation_status,overrides_json "
        "FROM param_cells WHERE mode=? AND campaign=? AND symbol=? AND side=? "
        "AND param=? AND tier='ENGINE' AND contract_fingerprint=? "
        "AND capital_accounting_version=? "
        "AND validation_status IN (?,?,?)",
        (
            psc.MODE,
            psc.CAMPAIGN,
            sym,
            side,
            pname,
            fp,
            psc.CAPITAL_ACCOUNTING_VERSION,
            *wiring_gate.VALID_STATUSES,
        ),
    ):
        if not mtf_companion_receipt_valid(pname, overrides_json):
            continue
        rows.append(
            {
                "value": wiring_gate._parse_value(value_json, value_num),
                "value_json": value_json,
                "fingerprint": trade_fp,
                "inert": bool(inert),
                "validation_status": status,
            }
        )
    return wiring_gate.classify_param(
        pname,
        rows,
        base,
        psc.MATRIX_NPZ_DIR / f"{sym}.npz",
        symbol=sym,
        side=side,
        master_enabled_override=matrix_recipe_master_enabled_override(
            pname, sym, side
        ),
    )


def record_gate_event(ctx, sym, side, pname, vlabel, verdict):
    """Append one auditable receipt per current contract/verdict.

    Workers revisit the finite queue forever.  Persisting a unique receipt in
    the claims DB prevents that loop from turning a cheap preflight rejection
    into an unbounded JSONL writer.
    """
    path = SBX / "data/reports/EXACT_WIRING_GATE_EVENTS.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    contract_fingerprint = psc.matrix_contract_fingerprint(sym, side)
    receipt = "|".join(
        (
            psc.CAMPAIGN,
            contract_fingerprint,
            sym,
            side,
            pname,
            str(verdict.get("verdict")),
        )
    )
    try:
        ctx["ccon"].execute(
            "INSERT OR IGNORE INTO wiring_gate_receipts(receipt,ts) VALUES (?,?)",
            (receipt, time.time()),
        )
        inserted = ctx["ccon"].execute("SELECT changes()").fetchone()[0]
        ctx["ccon"].commit()
    except sqlite3.OperationalError:
        try:
            ctx["ccon"].rollback()
        except sqlite3.OperationalError:
            pass
        return
    if not inserted:
        return
    row = {
        "ts": psc.now_iso(),
        "worker": ctx["worker"],
        "campaign": psc.CAMPAIGN,
        "symbol": sym,
        "side": side,
        "param": pname,
        "value": vlabel,
        "contract_fingerprint": contract_fingerprint,
        **verdict,
    }
    with path.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        fh.flush()
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def record_grouped_recipe_provenance(ctx, sym, side, marker):
    """Write one compact, deduplicated constituent-receipt sidecar."""
    recipe_id = marker["recipe_id"]
    contract_fingerprint = psc.matrix_contract_fingerprint(sym, side)
    receipt = "|".join(
        (
            "GROUPED_RECIPE",
            psc.CAMPAIGN,
            contract_fingerprint,
            sym,
            side,
            recipe_id,
        )
    )
    try:
        ctx["ccon"].execute(
            "INSERT OR IGNORE INTO wiring_gate_receipts(receipt,ts) VALUES (?,?)",
            (receipt, time.time()),
        )
        inserted = ctx["ccon"].execute("SELECT changes()").fetchone()[0]
        ctx["ccon"].commit()
    except sqlite3.OperationalError:
        try:
            ctx["ccon"].rollback()
        except sqlite3.OperationalError:
            pass
        return
    if not inserted:
        return
    path = SBX / "data/reports/GROUPED_COMBO_RECIPE_RECEIPTS.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": psc.now_iso(),
        "campaign": psc.CAMPAIGN,
        "symbol": sym,
        "side": side,
        "recipe_id": recipe_id,
        "contract_fingerprint": contract_fingerprint,
        "logical_value_json": marker["logical_value_json"],
        "member_receipts": marker["member_receipts"],
    }
    with path.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def release_claim(ctx, unit):
    try:
        ctx["ccon"].execute("DELETE FROM claims WHERE unit=?", (unit,))
        ctx["ccon"].commit()
    except sqlite3.OperationalError:
        try:
            ctx["ccon"].rollback()
        except sqlite3.OperationalError:
            pass


def claim(ctx, unit):
    """Take the work claim for one unit. False = someone else has it (or the DB is busy)."""
    ccon, worker = ctx["ccon"], ctx["worker"]
    try:
        # The old SELECT + INSERT OR REPLACE was not atomic. Two workers could both observe
        # "missing", then each replace the row and run the same full-history engine against
        # the same cache files. BEGIN IMMEDIATE serializes the decision; never steal a live
        # worker's unit.
        ccon.execute("BEGIN IMMEDIATE")
        row = ccon.execute("SELECT worker, ts FROM claims WHERE unit=?", (unit,)).fetchone()
        # Do not steal an exact unit merely because it has legitimately run
        # longer than the old fixed 30-minute lease.
        lease_seconds = max(1800, int(ctx["args"].timeout) * 2)
        if row and row[0] != worker and time.time() - row[1] < lease_seconds:
            ccon.rollback()
            return False
        if row:
            ccon.execute(
                "UPDATE claims SET worker=?,ts=? WHERE unit=?",
                (worker, time.time(), unit),
            )
        else:
            ccon.execute(
                "INSERT INTO claims(unit,worker,ts) VALUES (?,?,?)",
                (unit, worker, time.time()),
            )
        ccon.commit()
        return True
    except sqlite3.OperationalError:
        try:
            ccon.rollback()
        except sqlite3.OperationalError:
            pass
        time.sleep(2)
        return False


def commit_unit(ctx, label, tag, wrote):
    """Commit a unit's rows. A commit that never lands is a FAILURE, never a result."""
    con, worker = ctx["con"], ctx["worker"]
    try:
        prs.busy_retry(con.commit, tries=120, delay=5)
    except sqlite3.OperationalError as exc:
        print(f"[{worker}] {label} {tag}: COMMIT FAILED after 10min ({exc}) — "
              f"{wrote} row(s) DISCARDED, unit will be retried", flush=True)
        if _progress(ctx):
            _progress(ctx).rejected(
                max(1, wrote), status="REJECTED_COMMIT_FAILED"
            )
        return False
    print(f"[{worker}] {label} {tag}: rows={wrote}", flush=True)
    if _progress(ctx):
        if wrote > 0:
            # A changed DB row may still be FAILED or audit-only. Never derive
            # accepted throughput from `wrote`; the heartbeat re-queries only
            # manifest-exact current-campaign ENGINE/PASS logical cells.
            _progress(ctx).persisted()
        else:
            _progress(ctx).rejected(status="REJECTED_NO_DB_CHANGE")
    return wrote > 0


def ingest(ctx, sym, by_side, need, pname, vlabel, ovr, tag):
    """Score one symbol's trades and write its cells. Returns rows written (uncommitted)."""
    con = ctx["con"]
    row_st = psc.artifact_stamp(tag, sym, ctx["st"])
    sym_years, bh_long = psc.sym_years_and_bh(sym, psc.START)
    sym_years = sym_years or ctx["years"]
    wrote = 0
    for side in need:
        sym_intervals = psc.collapse_intervals(ctx["intervals"].get((sym, side), []), ctx["censor"])
        uni_trades = [t for t in by_side[side] if psc.is_in_intervals(t.get("entry_ts", 0), sym_intervals)]
        if not sym_intervals and f"{sym}_{side}" not in ctx["keys"]:
            uni_trades = by_side[side]  # priority key outside registry universe: record full-window trades
        uni_rets = [float(t["pnl_pct"]) for t in uni_trades]
        if sym_intervals:
            u_start = max(psc.START, str(sym_intervals[0][0])[:10])
            u_years, u_bh = psc.sym_years_and_bh(sym, u_start)
            u_years = u_years or sym_years
        else:
            u_years, u_bh = sym_years, bh_long
        audit = psc.load_matrix_run_audit(tag, sym) if ctx["args"].safe_contract else None
        if ctx["args"].safe_contract:
            m = psc.capital_key_metrics(
                uni_trades,
                u_years,
                u_bh if u_bh is not None else bh_long,
                side,
                (audit or {}).get("result", {}),
            )
        else:
            m = psc.hold_metrics(
                uni_trades,
                u_years,
                psc.key_metrics(
                    uni_rets,
                    u_years,
                    u_bh if u_bh is not None else bh_long,
                    side,
                ),
            )
        bkey = sym + "_" + side
        dvb = (m["gain_per_mo"] - ctx["base"][bkey]) if bkey in ctx["base"] else None
        fp = prs.trades_fingerprint(
            uni_trades, contract_version=psc.MATRIX_CONTRACT_VERSION
        )
        # An override that leaves the trade list bit-identical to the baseline did not reach the
        # code. Report 0, flag inert=1 — never let a stale-baseline offset masquerade as a gain.
        is_inert = 1 if ctx["base_fp"].get(bkey) and fp == ctx["base_fp"][bkey] else 0
        if is_inert:
            dvb = 0.0
        try:
            vnum = float(vlabel)
        except ValueError:
            vnum = None
        changes_before = con.total_changes
        prs.insert_cell(con, {"mode": psc.MODE, "symbol": sym, "side": side, "campaign": psc.CAMPAIGN,
                              "param": pname, "value_json": vlabel, "value_num": vnum, **m,
                              "delta_vs_baseline_gain_mo": (round(dvb, 4) if dvb is not None else None),
                              "ts": psc.now_iso(), "overrides_json": json.dumps(ovr), "stamp": row_st,
                              "tier": "ENGINE", "baseline_stamp": ctx["base_fp"].get(bkey),
                              "trades_fingerprint": fp, "inert": is_inert,
                              "replace_same_contract_receipt": bool(
                                  exit_family_companion_overrides(pname)
                              ),
                              "replace_same_contract_invalid": bool(
                                  ctx["args"].safe_contract
                              ),
                              **(_audit_db_fields(audit) if audit else {}),
                              "source_file": f"param_matrix_daemon/{psc.CAMPAIGN}/{tag}"})
        # insert_cell commits internally. Count only a new/replaced logical
        # cell, never a duplicate/no-op attempt, as accepted production.
        if con.total_changes > changes_before:
            wrote += 1
    return wrote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="w1")
    ap.add_argument("--first", default="", help="comma-separated symbols this worker processes first (dedicated lane)")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--min-avail", type=int, default=8000)
    ap.add_argument("--once", action="store_true")
    ap.add_argument(
        "--progress-slot",
        type=int,
        choices=range(1, matrix_live_progress.WORKER_SLOTS + 1),
        help="stable 1..6 port-5077 worker slot (production launchers supply this)",
    )
    ap.add_argument(
        "--only-param",
        default="",
        help=(
            "comma-separated exact parameters for a focused current-contract "
            "worker; keeps the canonical daemon argv/ownership contract"
        ),
    )
    ap.add_argument(
        "--priority-roots",
        default="",
        help=(
            "comma-separated EXIT master parameters; schedule only their "
            "dependency packs (parents, masters, then dependent values)"
        ),
    )
    ap.add_argument(
        "--worker-manifest",
        default=str(WORKER_MANIFEST),
        help=(
            "current c5 worker selection manifest; a matching --tag supplies "
            "priority_roots and must match campaign/key/fingerprint exactly"
        ),
    )
    ap.add_argument("--all-tiers", action="store_true",
                    help="also measure VEC_SCREEN-tier params with the real engine (a key that "
                         "must be COMPLETE cannot rely on a screen that silently no-ops knobs)")
    ap.add_argument(
        "--include-diagnostics",
        action="store_true",
        help=(
            "explicitly retest diagnostic-only dc_low4 emergency paths; excluded by default "
            "because their preserved evidence is losing churn, not profit-taking research"
        ),
    )
    ap.add_argument("--side", default="", choices=["", "LONG", "SHORT"],
                    help="focus side: drops opposite-side knobs from the work-list")
    ap.add_argument("--only", default="",
                    help="restrict this worker to these symbols entirely (default: whole universe "
                         "after the --first lane drains)")
    ap.add_argument(
        "--safe-contract",
        action="store_true",
        help=(
            "required repaired lane: side-isolated, B&H-seeded, current NPZ contract, "
            "capital-weighted accounting and mandatory re-entry telemetry"
        ),
    )
    ap.add_argument(
        "--grouped-combos",
        action="store_true",
        help=(
            "run only deterministic within/cross action-group recipes built "
            "from receipt-valid exact path cells; safe-contract priority keys only"
        ),
    )
    a = ap.parse_args()
    if MATRIX_PAUSE_FILE.is_file():
        matrix_live_progress.pause_all_workers(
            PROGRESS_SNAPSHOT, status=matrix_live_progress.PAUSED_STATUS
        )
        print(
            f"matrix workers paused by {MATRIX_PAUSE_FILE}; no work started",
            flush=True,
        )
        return
    first = [s.strip().upper() for s in a.first.split(",") if s.strip()]
    only = [s.strip().upper() for s in a.only.split(",") if s.strip()]
    priority_roots = tuple(
        value.strip()
        for value in a.priority_roots.split(",")
        if value.strip()
    )
    worker = f"pmx:{a.tag}:{os.getpid()}"
    progress = None
    if a.safe_contract:
        if not a.side or len(only) != 1:
            raise SystemExit("--safe-contract requires exactly one --only symbol and --side")
        # 2026-07-30 USER: temporary shortened-window diagnostic campaign runs the
        # identical safe-contract recipe (same matrix_cell_overrides/gate logic) as
        # the c5 campaign, just with PSC_START moved forward. Allowed explicitly by
        # name -- never a silent wildcard -- so an unrelated campaign can't slip in.
        _SAFE_CONTRACT_ALLOWED_CAMPAIGNS = {
            "stocks_repaired_20260730_c5",
            "stocks_repaired_20260730_c5_1yr",
        }
        if psc.CAMPAIGN not in _SAFE_CONTRACT_ALLOWED_CAMPAIGNS:
            raise SystemExit(
                "--safe-contract requires the current c5 campaign (or its "
                "PSC_START-shortened stocks_repaired_20260730_c5_1yr diagnostic "
                "variant); set PSC_CAMPAIGN=stocks_repaired_20260730_c5"
            )
        progress_slot = a.progress_slot or progress_slot_for_tag(
            a.worker_manifest, a.tag
        )
        if progress_slot is not None:
            progress = ProductionProgressHeartbeat(
                progress_slot,
                worker,
                only[0],
                a.side,
                manifest_path=a.worker_manifest,
            )
        else:
            print(
                f"[{worker}] no unique 1..6 progress slot for tag={a.tag}; "
                "worker will not overwrite another slot",
                flush=True,
            )
        contract = audit_npz(
            only[0],
            str(psc.MATRIX_NPZ_DIR / f"{only[0]}.npz"),
            profile="ladder",
            start=psc.START,
        )
        if not contract.valid:
            if progress:
                progress.set_current(
                    parameter="—",
                    value="—",
                    combination="—",
                    stage="DATA_AUDIT",
                    status="QUARANTINED_INVALID_NPZ",
                )
                progress.quarantined(status="QUARANTINED_INVALID_NPZ")
                progress.stop()
            quarantine = SBX / "data" / "reports" / f"MATRIX_QUARANTINE_{only[0]}_{a.side}.json"
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            quarantine.write_text(
                json.dumps(
                    {
                        "ts": psc.now_iso(),
                        "symbol": only[0],
                        "side": a.side,
                        "status": "QUARANTINED_INVALID_NPZ",
                        "errors": list(contract.errors),
                        "warnings": list(contract.warnings),
                        "stats": contract.stats,
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
            raise SystemExit(f"{only[0]}_{a.side} quarantined: {contract.errors}")
        worker_policy = load_worker_priority_policy(
            a.worker_manifest,
            a.tag,
            only[0],
            a.side,
            campaign=psc.CAMPAIGN,
            contract_version=psc.MATRIX_CONTRACT_VERSION,
            contract_fingerprint=psc.matrix_contract_fingerprint(
                only[0], a.side
            ),
        )
        if worker_policy:
            if priority_roots:
                raise SystemExit(
                    "MATRIX_PRIORITY_SOURCE_CONFLICT: use either "
                    "--priority-roots or a matching worker manifest tag"
                )
            if a.only_param:
                raise SystemExit(
                    "MATRIX_PRIORITY_SOURCE_CONFLICT: worker manifest "
                    "dependency packs cannot be combined with --only-param"
                )
            priority_roots = worker_policy["priority_roots"]
            a.all_tiers = bool(worker_policy["all_tiers"])
            if worker_policy["selection_mode"] != "DEPENDENCY_PACKS_ONLY":
                raise SystemExit(
                    "MATRIX_WORKER_MANIFEST_INVALID: selection_mode must be "
                    "DEPENDENCY_PACKS_ONLY"
                )
            print(
                f"[pmx:{a.tag}] c5 priority manifest: "
                f"{only[0]}_{a.side} roots={','.join(priority_roots)} "
                f"(dependency packs only; no live promotion)",
                flush=True,
            )
    if a.grouped_combos:
        if not a.safe_contract:
            raise SystemExit("--grouped-combos requires --safe-contract")
        key = f"{only[0]}_{a.side}" if only and a.side else ""
        if key not in combo_scheduler.PRIORITY_KEYS:
            raise SystemExit(
                "--grouped-combos is limited to the ranked top-ten-per-side "
                "plus IBIT_LONG/PLTR_SHORT and unfinished MU_LONG/NVDA_LONG "
                f"pilots; got {key or 'missing key'}"
            )
        if priority_roots or a.only_param:
            raise SystemExit(
                "--grouped-combos cannot be combined with dependency roots "
                "or --only-param"
            )
    manifest = str(SBX / f"data/param_sweep_manifest_{psc.MODE}.json")
    baseline_fail_count = 0
    while True:
        if MATRIX_PAUSE_FILE.is_file():
            if progress:
                progress.stop()
            matrix_live_progress.pause_all_workers(
                PROGRESS_SNAPSHOT, status=matrix_live_progress.PAUSED_STATUS
            )
            print(
                f"[{worker}] paused by {MATRIX_PAUSE_FILE}; exiting after current unit",
                flush=True,
            )
            return
        # NEVER die on a lock (2026-07-21: hourly param_matrix rebuild holds a minutes-long
        # write txn; workers must outwait it, not crash-loop through the watchdog)
        try:
            con = prs.connect()
            ccon = claims()
        except sqlite3.OperationalError as e:
            print(f"[{worker}] connect busy ({e}) — retry in 30s", flush=True)
            time.sleep(30)
            continue
        try:
            baseline_rows = list(
                con.execute(
                    "SELECT symbol,side,gain_per_mo,real_closes,time_in_mkt_pct "
                    "FROM key_baseline WHERE mode=? AND campaign=? "
                    "AND capital_accounting_version=?",
                    (
                        psc.MODE,
                        psc.CAMPAIGN,
                        psc.CAPITAL_ACCOUNTING_VERSION,
                    ),
                )
            )
            base = {r[0] + "_" + r[1]: r[2] for r in baseline_rows}
            base_metrics = {
                r[0] + "_" + r[1]: {
                    "real_closes": r[3],
                    "time_in_mkt_pct": r[4],
                }
                for r in baseline_rows
            }
        except sqlite3.OperationalError as e:
            print(f"[{worker}] db busy at pass start ({e}) — retry in 30s", flush=True)
            con.close()
            ccon.close()
            time.sleep(30)
            continue
        freed = release_dead_claims(ccon)
        if freed:
            print(f"[{worker}] released {freed} claims held by dead workers", flush=True)
        keys = (
            {f"{only[0]}_{a.side}"}
            if a.safe_contract
            else set(psc.universe_keys())
        )
        intervals = ur.membership_intervals(psc.ACCOUNT)
        censor = psc.registry_censor_start(intervals)
        years = psc.years_since(psc.START)
        st = psc.stamp()
        cells = all_cells(manifest, a.all_tiers, a.side, a.include_diagnostics)
        cells = only_params(cells, a.only_param)
        if a.grouped_combos:
            dependency_rows = load_interdependency_rows(
                INTERDEPENDENCY_METADATA, manifest
            )
            cells = grouped_combo_cells(
                con, only[0], a.side, dependency_rows
            )
            print(
                f"[{worker}] {only[0]}_{a.side}: grouped-combination "
                f"queue={len(cells)} (within groups, cross groups, LOO)",
                flush=True,
            )
        elif priority_roots:
            dependency_rows = load_interdependency_rows(
                INTERDEPENDENCY_METADATA, manifest
            )
            cells = prioritize_dependency_pack_cells(
                cells, priority_roots, dependency_rows
            )
        else:
            dropped = drop_verdicts()
            dependency_rows = load_interdependency_rows(
                INTERDEPENDENCY_METADATA, manifest
            )
            cells = prioritize_compatibility_cells(
                cells, dropped, dependency_rows
            )
            # Even the compatibility queue no longer leads with diagnostic
            # STOP_PACK/TF_EXCLUDE helpers.  They remain eventually runnable
            # but cannot consume the first exact-engine hours.
        did_any = False
        import json as _json

        # --all-tiers asks for ENGINE coverage of every param, so a Tier-1 vec cell sitting in
        # the slot must NOT count as tested — otherwise the flag is inert (2026-07-21: 14 workers
        # sat idle because 560 MU_LONG vec cells made every vec-screened param look done).
        tier_clause = " AND tier='ENGINE'" if a.all_tiers else ""

        def tested_local_for(symbol, s, pname, vlabel):
            # local-only dedupe — prs.already_tested falls through to a per-call JOIN on the
            # 1.9GB central DB on every miss, which starved the workers (2026-07-21).
            for _ in range(3):
                try:
                    if a.safe_contract:
                        if has_recent_full_exact_evidence(
                            con, symbol, s, pname, vlabel
                        ):
                            return True
                        accepted = sorted(
                            psc.matrix_contract_fingerprints(symbol, s)
                        )
                        fp_placeholders = ",".join("?" for _ in accepted)
                        evidence_campaigns = evidence_campaigns_for_dedupe(
                            psc.CAMPAIGN
                        )
                        campaign_placeholders = ",".join(
                            "?" for _ in evidence_campaigns
                        )
                        rows = con.execute(
                            "SELECT overrides_json FROM param_cells "
                            f"WHERE mode=? AND campaign IN ({campaign_placeholders}) "
                            "AND symbol=? AND side=? AND param=? AND value_json IN (?,?) "
                            f"AND contract_fingerprint IN ({fp_placeholders}) "
                            "AND capital_accounting_version=? "
                            "AND validation_status='PASS' "
                            f"{tier_clause}",
                            (
                                psc.MODE,
                                *evidence_campaigns,
                                symbol,
                                s,
                                pname,
                                _json.dumps(vlabel),
                                str(vlabel),
                                *accepted,
                                psc.CAPITAL_ACCOUNTING_VERSION,
                            ),
                        ).fetchall()
                        return any(
                            mtf_companion_receipt_valid(pname, row[0])
                            for row in rows
                        )
                    return con.execute(
                        "SELECT 1 FROM param_cells WHERE mode=? AND symbol=? AND side=? AND param=? "
                        f"AND value_json IN (?,?){tier_clause} LIMIT 1",
                        (psc.MODE, symbol, s, pname, _json.dumps(vlabel), str(vlabel))).fetchone() is not None
                except sqlite3.OperationalError:
                    time.sleep(5)
            return True  # db persistently busy — treat as tested THIS PASS, retried next pass
        base_fp = {r[0] + "_" + r[1]: r[2] for r in con.execute(
            "SELECT symbol, side, trades_fingerprint FROM key_baseline "
            "WHERE mode=? AND campaign=? AND capital_accounting_version=?",
            (
                psc.MODE,
                psc.CAMPAIGN,
                psc.CAPITAL_ACCOUNTING_VERSION,
            ))}
        ctx = {"con": con, "ccon": ccon, "base": base, "base_fp": base_fp,
               "base_metrics": base_metrics, "keys": keys,
               "intervals": intervals, "censor": censor, "years": years, "st": st,
               "worker": worker, "tested_local_for": tested_local_for, "args": a,
               "progress": progress}
        if a.safe_contract:
            try:
                baseline_ok = ensure_safe_baseline(ctx, only[0], a.side)
            except SystemExit as exc:
                # ensure_safe_baseline's psc.run_symbol call is the earliest per-pass chance to
                # hit CONTRACT_EXIT_STARTUP_GAP (contract changed while this worker was idle
                # between passes, before any subprocess even started this pass) as well as
                # CONTRACT_EXIT_MID_RUN (changed while the baseline engine run was in flight).
                con.close()
                ccon.close()
                reexec_self(str(exc), worker)
            if baseline_ok is BASELINE_IN_PROGRESS:
                # A sibling is already producing this exact fingerprint.  The
                # old boolean return folded claim contention into a real
                # baseline failure, so three MU/two NVDA workers repeatedly
                # reached BASELINE_PERSISTENTLY_UNAVAILABLE while the owning
                # engine was still healthy and running.
                con.close()
                ccon.close()
                print(
                    f"[{worker}] baseline already in progress for "
                    f"{only[0]}_{a.side}; wait without counting a failure",
                    flush=True,
                )
                if progress:
                    progress.set_current(
                        parameter="—",
                        value="—",
                        combination="—",
                        stage="BASELINE",
                        status="WAITING_BASELINE_CLAIM",
                    )
                time.sleep(60)
                continue
            if baseline_ok:
                baseline_fail_count = 0
            else:
                baseline_fail_count += 1
                con.close()
                ccon.close()
                if baseline_fail_count >= BASELINE_FAIL_LIMIT:
                    # --safe-contract enforces exactly one --only symbol + --side (argparse
                    # check above), so a broken baseline for that key IS this worker's entire
                    # job — there is no "other queued work for this tag" to fall back to.
                    # 2026-07-28: matrix_rt1 (TTD_SHORT) spun "retry in 60s" on a permanently
                    # broken baseline ("no intended-side trade/MTM record") for HOURS, burning
                    # a slot on known-broken work instead of surfacing the failure. Exit with a
                    # distinct message so the watchdog log makes the root cause (baseline data,
                    # not transient contention) visible instead of relaunching blindly forever.
                    raise SystemExit(
                        f"BASELINE_PERSISTENTLY_UNAVAILABLE: {only[0]}_{a.side} failed "
                        f"{baseline_fail_count} consecutive passes — this worker's only job is "
                        "this key; investigate the baseline (not a transient retry), do not "
                        "just relaunch"
                    )
                backoff = min(60 * (2 ** (baseline_fail_count - 1)), BASELINE_BACKOFF_CAP_S)
                print(
                    f"[{worker}] safe baseline unavailable for {only[0]}_{a.side} "
                    f"(consecutive fail {baseline_fail_count}/{BASELINE_FAIL_LIMIT}); "
                    f"backoff {backoff}s",
                    flush=True,
                )
                time.sleep(backoff)
                continue
            baseline_key = f"{only[0]}_{a.side}"
            baseline_metrics_for_key = ctx["base_metrics"].get(baseline_key, {})
            if baseline_is_entry_inert(baseline_metrics_for_key):
                dependency_rows = load_interdependency_rows(
                    INTERDEPENDENCY_METADATA, manifest
                )
                before = len(cells)
                cells = gate_cells_for_safe_baseline(
                    cells, baseline_metrics_for_key, dependency_rows
                )
                print(
                    f"[{worker}] {baseline_key}: BASELINE_ENTRY_INERT "
                    f"real_closes={baseline_metrics_for_key.get('real_closes')} "
                    f"TIM={baseline_metrics_for_key.get('time_in_mkt_pct')}%; "
                    f"entry binding probes={len(cells)}/{before}, downstream and "
                    "helper rows blocked",
                    flush=True,
                )
        for sym in (only or ordered_syms(first)):
            def tested_local(s, pname, vlabel, _sym=sym):
                return tested_local_for(_sym, s, pname, vlabel)
            ctx["tested_local"] = tested_local
            if progress and a.safe_contract:
                remaining_now = sum(
                    1
                    for pname, vlabel, cell_ovr in cells
                    if is_manifest_cell((pname, vlabel, cell_ovr))
                    and not matrix_cell_is_baseline_alias(pname, cell_ovr)
                    and not tested_local(a.side, pname, vlabel)
                )
                progress.set_remaining(remaining_now)
            for pname, vlabel, ovr in cells:
                try:
                    did_any |= run_unit(ctx, sym, pname, vlabel, ovr)
                except SystemExit as exc:
                    # run_unit's psc.run_symbol call can hit either CONTRACT_EXIT_STARTUP_GAP
                    # (contract already stale by the time this cell's call started) or
                    # CONTRACT_EXIT_MID_RUN (changed while this cell's engine subprocess ran) —
                    # both propagate here uncaught (SystemExit is not an Exception subclass) —
                    # 2026-07-28: this killed 8 workers permanently on one config sync.
                    # Quarantine (MID_RUN_QUARANTINE case) already happened inside run_symbol;
                    # this only reloads the worker so it doesn't stay dead.
                    if progress:
                        if CONTRACT_EXIT_MID_RUN in str(exc):
                            progress.quarantined(
                                status="QUARANTINED_CONTRACT_CHANGED"
                            )
                        else:
                            progress.rejected(
                                status="REJECTED_CONTRACT_STARTUP_GAP"
                            )
                    reexec_self(str(exc), worker)
                except Exception as exc:
                    # NEVER die on one bad unit — a single uncaught OperationalError out of
                    # insert_cell took down all 10 workers for hours (2026-07-21, 0 engine cells).
                    print(f"[{worker}] {sym} {pname}={vlabel}: EXC {exc!r} — unit skipped", flush=True)
                    if progress:
                        progress.rejected(status="REJECTED_EXCEPTION")
                    time.sleep(2)
        con.close()
        ccon.close()
        if a.once:
            break
        if not did_any:
            # 2026-07-21: this was a flat 1800s. With the whole fleet on ONE ticker, most workers
            # lose the claim race every pass and slept 30 MINUTES while units freed up around them
            # (measured: 14 workers alive 60min with 3s of CPU each, 3 engines running, memory
            # fine). A pass that finds nothing claimable is normal contention, not an empty queue:
            # re-scan promptly while work remains, and only back off when the ticker is genuinely
            # done. The scan is a few thousand indexed lookups — cheap next to a 9-minute run.
            remaining = 0
            try:
                rcon = prs.connect()
                for sym in (only or ordered_syms(first))[:1]:
                    for pname, vlabel, _o in cells:
                        if not is_manifest_cell((pname, vlabel, _o)):
                            continue
                        if (
                            a.safe_contract
                            and matrix_cell_is_baseline_alias(pname, _o)
                        ):
                            continue
                        if a.safe_contract:
                            if has_recent_full_exact_evidence(
                                rcon, sym, a.side, pname, vlabel
                            ):
                                continue
                            accepted = sorted(
                                psc.matrix_contract_fingerprints(
                                    sym, a.side
                                )
                            )
                            fp_placeholders = ",".join(
                                "?" for _ in accepted
                            )
                            evidence_campaigns = evidence_campaigns_for_dedupe(
                                psc.CAMPAIGN
                            )
                            campaign_placeholders = ",".join(
                                "?" for _ in evidence_campaigns
                            )
                            receipts = rcon.execute(
                                "SELECT overrides_json FROM param_cells "
                                f"WHERE mode=? AND campaign IN ({campaign_placeholders}) "
                                "AND symbol=? "
                                "AND side=? AND param=? "
                                "AND value_json IN (?,?) "
                                f"AND contract_fingerprint IN ({fp_placeholders}) "
                                "AND capital_accounting_version=? "
                                "AND validation_status='PASS' "
                                f"{tier_clause}",
                                (
                                    psc.MODE,
                                    *evidence_campaigns,
                                    sym,
                                    a.side,
                                    pname,
                                    _json.dumps(vlabel),
                                    str(vlabel),
                                    *accepted,
                                    psc.CAPITAL_ACCOUNTING_VERSION,
                                ),
                            ).fetchall()
                            done = any(
                                mtf_companion_receipt_valid(pname, row[0])
                                for row in receipts
                            )
                        else:
                            done = (
                                rcon.execute(
                                    "SELECT 1 FROM param_cells WHERE mode=? "
                                    "AND campaign=? AND symbol=? AND side=? "
                                    "AND param=? AND value_json IN (?,?)"
                                    f"{tier_clause} LIMIT 1",
                                    (
                                        psc.MODE,
                                        psc.CAMPAIGN,
                                        sym,
                                        a.side,
                                        pname,
                                        _json.dumps(vlabel),
                                        str(vlabel),
                                    ),
                                ).fetchone()
                                is not None
                            )
                        if not done:
                            remaining += 1
                rcon.close()
            except sqlite3.OperationalError:
                remaining = 1  # unknown -> assume work remains and re-scan soon
            nap = 60 if remaining else 1800
            print(f"[{worker}] pass claimed nothing; {remaining} units still open — sleeping {nap}s", flush=True)
            if progress:
                progress.set_remaining(remaining)
                progress.set_current(
                    parameter="—",
                    value="—",
                    combination="—",
                    stage="QUEUE",
                    status=("WAITING_FOR_CLAIM" if remaining else "COMPLETE"),
                )
            time.sleep(nap)
    if progress:
        progress.stop()


if __name__ == "__main__":
    main()
