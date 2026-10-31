"""Persist conservative source-level path reachability notes beside paper inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from contextlib import contextmanager

try:
    from .paper_scenario_db import ScenarioDB
except ImportError:
    from paper_scenario_db import ScenarioDB


KNOWN_FINDINGS = (
    {
        "venue": "crypto", "switch_name": "HEDGE_ACCOUNTS", "status": "UNREACHABLE",
        "path_scope": "same-symbol hedge OPEN only",
        "finding_code": "HEDGE_EXECUTOR_EARLY_RETURN",
        "summary": "Account-list changes cannot produce a hedge open while the current same-symbol hedge executor returns False before its later implementation.",
        "dependencies": ["HEDGE_MODE", "OBLIGATORY_HEDGE_ENABLED", "account membership"],
        "blockers": ["execute_same_symbol_hedge has an unconditional early False return", "HEDGE_MODE and OBLIGATORY_HEDGE_ENABLED default false", "HEDGE_ACCOUNTS defaults empty"],
        "source_refs": ["ez_positions_quick.py:14591-14595", "config.py:337-339", "config.py:847-849", "config.py:3647-3649"],
    },
    {
        "venue": "crypto", "switch_name": "SCALP_ACCOUNTS", "status": "UNREACHABLE",
        "path_scope": "SCALP V2 account-gated entry/exit only",
        "finding_code": "SCALP_MODE_AND_ACCOUNT_LIST_REQUIRED",
        "summary": "Changing only the account list is inert under the current false SCALP_MODE and empty SCALP_ACCOUNTS defaults.",
        "dependencies": ["SCALP_MODE", "SCALP_ACCOUNTS contains the account"],
        "blockers": ["SCALP_MODE defaults false", "SCALP_ACCOUNTS defaults empty"],
        "source_refs": ["config.py:376-394", "ez_positions_quick.py:28962-28964", "ez_positions_quick.py:25528-25530"],
    },
    {
        "venue": "crypto", "switch_name": "SCALP_V3_ENABLED", "status": "UNREACHABLE",
        "path_scope": "SCALP V3 account-gated entry/exit only",
        "finding_code": "SCALP_V3_ACCOUNT_LIST_REQUIRED",
        "summary": "An enable-only candidate cannot reach V3 entry/exit consumers while SCALP_V3_ACCOUNTS is empty.",
        "dependencies": ["SCALP_V3_ENABLED", "SCALP_V3_ACCOUNTS contains the account", "scanner input and order-book path"],
        "blockers": ["SCALP_V3_ENABLED defaults false", "SCALP_V3_ACCOUNTS defaults empty"],
        "source_refs": ["config.py:449-454", "ez_positions_quick.py:29073-29075", "ez_positions_quick.py:25604-25607", "ez_manage.py:67805-67819"],
    },
    {
        "venue": "crypto", "switch_name": "MOMENTUM_WATCHDOG_ENABLED", "status": "NO_OP",
        "path_scope": "candidate switch consumer only",
        "finding_code": "NOOP_READ_ONLY_SWITCH",
        "summary": "The switch is read without an actionable consumer; watchdog force-open paths use separate switches.",
        "dependencies": [],
        "blockers": ["Current read is a no-op; separate MOMENTUM_SMA_WATCHDOG_ENABLED and WATCHDOG_DC_FORCE_OPEN_ENABLED paths own force-open behavior"],
        "source_refs": ["ez_manage.py:68732-68733"],
    },
    {
        "venue": "crypto", "switch_name": "TRADEABLE_KEYS_MANDATORY_ENABLED", "status": "NO_OP",
        "path_scope": "candidate switch consumer only",
        "finding_code": "NOOP_READ_ONLY_SWITCH",
        "summary": "The switch is a no-op read; the separate position-specific switch has an actionable consumer.",
        "dependencies": [],
        "blockers": ["Do not infer the position-specific path from this switch"],
        "source_refs": ["ez_manage.py:68734-68735", "ez_manage.py:50859-50912"],
    },
    {
        "venue": "crypto", "switch_name": "TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "position-specific gate only",
        "finding_code": "POSITION_GATE_NOT_RECEIPT_PROVEN",
        "summary": "A source consumer exists, but the inventory has no path-specific receipt proving the gate resolved or fired.",
        "dependencies": ["position state", "tradeable-key lookup", "gate result"],
        "blockers": ["Run receipts currently do not establish candidate-specific consumer invocation"],
        "source_refs": ["ez_manage.py:50859-50912"],
    },
    {
        "venue": "crypto", "switch_name": "SCALP_V3_BOOST_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "ranking-score adjustment only",
        "finding_code": "RANKING_INVOCATION_UNPROVEN",
        "summary": "A ranking-score consumer exists, but invocation and score propagation through the forward worker are unverified.",
        "dependencies": ["3m frame with required rows and columns", "nonzero boost weight", "volume and return thresholds", "forward-worker ranking invocation and propagation"],
        "blockers": ["No path-specific execution receipt proves ranking was invoked or its score consumed"],
        "source_refs": ["ez_rankings.py:4229-4259"],
    },
    {
        "venue": "crypto", "switch_name": "DC_BASIS_3M_REDUCE_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "3m Donchian reduce only",
        "finding_code": "SUB15M_MASTER_MASK",
        "summary": "The live consumer is below the sub-15m master; the manager forces this child switch false while that master is off.",
        "dependencies": ["LTF_SUB15M_TRADING_ENABLED", "paper-mask release evidence", "DC breach input"],
        "blockers": ["LTF_SUB15M_TRADING_ENABLED defaults false", "SUB15M_BLOCKED_SWITCHES forces this switch false"],
        "source_refs": ["config.py:7245-7249", "ez_manage.py:8517-8525", "ez_manage.py:8631-8638", "ez_manage.py:60926"],
    },
    {
        "venue": "crypto", "switch_name": "LIVE_5m_trading_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "sub-15m timeframe master only",
        "finding_code": "SUB15M_MASTER_POLICY",
        "summary": "This is a parent timeframe-availability gate; its effective value and downstream parity context must be proven in the run receipt.",
        "dependencies": ["effective master getter", "PARITY_MIN_DECISION_TF", "run-specific paper release"],
        "blockers": ["Source default is false; plan settings snapshots do not contain this key"],
        "source_refs": ["config.py:7212-7236", "ez_manage.py:767-769", "ez_positions_quick.py:26225-26226"],
    },
    {
        "venue": "crypto", "switch_name": "WRONG_SIDE_ABS_KILL_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "opposite-side absolute kill only",
        "finding_code": "SUB15M_CHILD_GATE",
        "summary": "A consumer exists, but it is unreachable while the sub-15m master remains off.",
        "dependencies": ["LTF_SUB15M_TRADING_ENABLED", "opposite-side absolute-kill condition"],
        "blockers": ["SUB15M_BLOCKED_SWITCHES forces the child false while the master is off"],
        "source_refs": ["ez_manage.py:8521-8525", "ez_manage.py:59247-59254"],
    },
    {
        "venue": "crypto", "switch_name": "BB_FROZEN_STOP_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "frozen-stop exit only",
        "finding_code": "POSITION_EXIT_CONSUMER_CONDITIONAL",
        "summary": "A direct exit consumer exists, but path reachability requires an open position and an eligible frozen-stop condition.",
        "dependencies": ["open position", "frozen-stop condition", "effective getter"],
        "blockers": ["No path-specific receipt currently proves consumer entry"],
        "source_refs": ["ez_manage.py:55694-55702"],
    },
    {
        "venue": "crypto", "switch_name": "BREAKOUT_LEASH_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "position exit for eligible breakout provenance only",
        "finding_code": "POSITION_PROVENANCE_CONDITIONAL",
        "summary": "A direct exit consumer exists, but requires an open position carrying eligible breakout provenance.",
        "dependencies": ["open position", "breakout/golden-rule/DC/BB/stdev entry provenance", "effective getter"],
        "blockers": ["No path-specific receipt currently proves eligible position provenance"],
        "source_refs": ["ez_manage.py:56262-56269"],
    },
    {
        "venue": "stocks", "switch_name": "TRC_LIVE_5M_TRADING_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "TRC-only 5m master override only",
        "finding_code": "TRC_ACCOUNT_ROUTE_CONDITIONAL",
        "summary": "The TRC-namespaced flag maps to LIVE_5m_trading_ENABLED only when the manager target list is exactly ['trc']; default mixed-account construction does not establish this route.",
        "dependencies": ["target_accounts exactly ['trc']", "TRC getter applied", "timeframe master and parity context"],
        "blockers": ["Default target list is ['trc','trb']", "worker account-route and getter evidence are absent"],
        "source_refs": ["tradier_manage.py:42799-42810", "tradier_manage.py:43213-43229"],
    },
    {
        "venue": "stocks", "switch_name": "RED_ZONE_TRADIER_GATE_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "Tradier OPEN gate only",
        "finding_code": "NONVECTORIZABLE_PARITY_MASK",
        "summary": "The Tradier consumer is masked false by PARITY_DISABLE_NON_VECTORIZABLE before per-symbol values unless the paper-specific release is proven.",
        "dependencies": ["PARITY_DISABLE_NON_VECTORIZABLE effective value", "paper-mask release evidence", "fresh OI cache and wall", "eligible reason"],
        "blockers": ["Default parity master is true and _cfg forces this switch false", "OI cache freshness and reason gates are independent"],
        "source_refs": ["config_tradier.py:3554-3557", "tradier_manage.py:7855-7859", "tradier_manage.py:26808-26813"],
    },
    {
        "venue": "stocks", "switch_name": "STOCKS_FRESH_ENTRY_TREND_GATES_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "Tradier fresh OPEN gates only",
        "finding_code": "NONVECTORIZABLE_PARITY_MASK",
        "summary": "The fresh-entry consumer is masked false by PARITY_DISABLE_NON_VECTORIZABLE before per-symbol values unless the paper-specific release is proven.",
        "dependencies": ["PARITY_DISABLE_NON_VECTORIZABLE effective value", "paper-mask release evidence", "OPEN action", "individual data and gate results"],
        "blockers": ["Default parity master is true and _cfg forces this switch false", "The consumer is OPEN-only and has additional gates"],
        "source_refs": ["config_tradier.py:3554-3557", "tradier_manage.py:7855-7859", "tradier_manage.py:26383-26415"],
    },
    {
        "venue": "stocks", "switch_name": "DC_BASIS_3M_REDUCE_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "Tradier sub-15m reduce only",
        "finding_code": "SUB15M_MASTER_MASK",
        "summary": "The Tradier sub-15m policy maps this child switch to false while LTF_SUB15M_TRADING_ENABLED is off; a stock consumer is not established by the current source trace.",
        "dependencies": ["LTF_SUB15M_TRADING_ENABLED", "paper-mask release evidence", "consumer invocation"],
        "blockers": ["LTF_SUB15M_TRADING_ENABLED defaults false", "Tradier SUB15M_BLOCKED_SWITCHES forces this switch false", "No direct Tradier consumer was found in the source audit"],
        "source_refs": ["config_tradier.py:3861-3863", "tradier_manage.py:7786-7820"],
    },
    {
        "venue": "stocks", "switch_name": "LIVE_5m_trading_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "sub-15m timeframe master only",
        "finding_code": "SUB15M_MASTER_POLICY",
        "summary": "This parent timeframe-availability gate defaults false; effective getter and forward-paper release must be proven for each run.",
        "dependencies": ["effective master getter", "PARITY_MIN_DECISION_TF", "run-specific paper release"],
        "blockers": ["Source default is false; plan settings snapshots do not contain this key"],
        "source_refs": ["config_tradier.py:1602-1611", "tradier_manage.py:2822"],
    },
    {
        "venue": "stocks", "switch_name": "REENTRY_GOLDEN_BLOCK_ENABLED", "status": "DEPENDENCY_UNVERIFIED",
        "path_scope": "Tradier reentry extension block only",
        "finding_code": "REENTRY_CONSUMER_CONDITIONAL",
        "summary": "A Tradier reentry consumer exists, but it also requires GOLDEN_RULE_ENABLED and a breakout-extension condition; receipts do not yet prove that path.",
        "dependencies": ["GOLDEN_RULE_ENABLED", "reentry action", "price above 1h Donchian high or Bollinger upper"],
        "blockers": ["Snapshot default is false and no path-specific consumer receipt exists"],
        "source_refs": ["config_tradier.py:5221-5224", "tradier_manage.py:42557-42568"],
    },
)

SOURCE_RUNTIME_ID = "259e13fdcf88a81ca1d091b3fa102be04ca87a727c4339897be0b806aaf79f82"
SOURCE_RUNTIME_RELATIVE = Path("data/paper_forward_lab/runtime") / SOURCE_RUNTIME_ID


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_hashes(root: Path, refs: list[str]) -> dict[str, str]:
    output = {}
    runtime_root = root / SOURCE_RUNTIME_RELATIVE
    for ref in refs:
        name = ref.split(":", 1)[0]
        path = runtime_root / name
        if path.is_file():
            output[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return output


def _ensure_schema(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS paper_forward_path_coverage_audit (
            venue TEXT NOT NULL,
            switch_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK(status IN ('UNVERIFIED','NO_OP','UNREACHABLE','DEPENDENCY_UNVERIFIED')),
            finding_code TEXT,
            path_scope TEXT NOT NULL DEFAULT 'unspecified',
            summary TEXT,
            dependencies_json TEXT NOT NULL DEFAULT '[]',
            blockers_json TEXT NOT NULL DEFAULT '[]',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            source_hashes_json TEXT NOT NULL DEFAULT '{}',
            finding_sha256 TEXT,
            audited_at_utc TEXT NOT NULL,
            source_runtime_id TEXT NOT NULL DEFAULT 'unverified',
            coverage_proven INTEGER NOT NULL DEFAULT 0 CHECK(coverage_proven=0),
            PRIMARY KEY(venue,switch_name)
        )
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_forward_path_coverage_audit)")}
    if "path_scope" not in columns:
        connection.execute("ALTER TABLE paper_forward_path_coverage_audit ADD COLUMN path_scope TEXT NOT NULL DEFAULT 'unspecified'")
    if "source_runtime_id" not in columns:
        connection.execute("ALTER TABLE paper_forward_path_coverage_audit ADD COLUMN source_runtime_id TEXT NOT NULL DEFAULT 'unverified'")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_paper_forward_path_coverage_status ON paper_forward_path_coverage_audit(status,venue)")
    connection.execute("DROP VIEW IF EXISTS paper_forward_path_coverage")
    connection.execute("""
        CREATE VIEW paper_forward_path_coverage AS
        SELECT a.venue, a.switch_name, a.status, a.finding_code, a.path_scope, a.summary,
               a.dependencies_json, a.blockers_json, a.source_refs_json,
               a.source_hashes_json, a.finding_sha256, a.audited_at_utc,
               a.source_runtime_id,
               'STATIC_SOURCE_ONLY; COUNTS_UNFILTERED_BY_CODE_HASH; DO_NOT_APPLY_TO_OTHER_RELEASES' AS interpretation_scope,
               0 AS coverage_proven,
               (SELECT COUNT(DISTINCT s.scenario_id)
                  FROM paths p JOIN scenarios s ON s.path_id=p.path_id
                 WHERE p.venue=a.venue AND p.switch_name=a.switch_name) AS scenario_count,
               (SELECT COUNT(DISTINCT r.run_id)
                  FROM paths p JOIN scenarios s ON s.path_id=p.path_id
                  JOIN runs r ON r.scenario_id=s.scenario_id
                 WHERE p.venue=a.venue AND p.switch_name=a.switch_name) AS associated_run_count,
               (SELECT COUNT(DISTINCT r.run_id)
                  FROM paths p JOIN scenarios s ON s.path_id=p.path_id
                  JOIN runs r ON r.scenario_id=s.scenario_id
                 WHERE p.venue=a.venue AND p.switch_name=a.switch_name AND r.evidence_kind='catalog_only') AS planned_run_count,
               (SELECT COUNT(DISTINCT r.run_id)
                  FROM paths p JOIN scenarios s ON s.path_id=p.path_id
                  JOIN runs r ON r.scenario_id=s.scenario_id
                 WHERE p.venue=a.venue AND p.switch_name=a.switch_name AND r.evidence_kind='independent_forward') AS independent_forward_run_count,
               (SELECT COUNT(DISTINCT f.receipt_id)
                  FROM paths p JOIN scenarios s ON s.path_id=p.path_id
                  JOIN runs r ON r.scenario_id=s.scenario_id
                  JOIN forward_receipts f ON f.run_id=r.run_id
                 WHERE p.venue=a.venue AND p.switch_name=a.switch_name) AS associated_forward_receipt_count,
               (SELECT COUNT(DISTINCT d.decision_id)
                  FROM paths p JOIN scenarios s ON s.path_id=p.path_id
                  JOIN runs r ON r.scenario_id=s.scenario_id
                  JOIN decisions d ON d.run_id=r.run_id
                 WHERE p.venue=a.venue AND p.switch_name=a.switch_name) AS associated_decision_count,
               (SELECT COUNT(DISTINCT x.result_id)
                  FROM paths p JOIN scenarios s ON s.path_id=p.path_id
                  JOIN runs r ON r.scenario_id=s.scenario_id
                  JOIN results x ON x.run_id=r.run_id
                 WHERE p.venue=a.venue AND p.switch_name=a.switch_name) AS associated_result_count,
               'PATH_COVERAGE_UNVERIFIED; NOT_A_COVERAGE_CLAIM' AS result_interpretation
          FROM paper_forward_path_coverage_audit a
    """)


def refresh_path_coverage(db: ScenarioDB, *, root: str | Path | None = None, findings=KNOWN_FINDINGS) -> dict:
    """Materialize known source findings; all other catalog paths remain UNVERIFIED."""
    root_path = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    now = _utc_now()
    inserted_defaults = 0
    updated_findings = 0
    with db.transaction():
        _ensure_schema(db.connection)
        before = db.connection.total_changes
        db.connection.execute("""
            INSERT OR IGNORE INTO paper_forward_path_coverage_audit
                (venue,switch_name,status,audited_at_utc)
            SELECT DISTINCT venue,switch_name,'UNVERIFIED',?
              FROM paths WHERE TRIM(switch_name)<>''
        """, (now,))
        inserted_defaults = db.connection.total_changes - before
        for finding in findings:
            refs = list(finding.get("source_refs", []))
            source_hashes = _source_hashes(root_path, refs)
            content = {
                "venue": finding["venue"], "switch_name": finding["switch_name"],
                "status": finding["status"], "finding_code": finding["finding_code"],
                "path_scope": finding.get("path_scope", "unspecified"),
                "summary": finding["summary"], "dependencies": finding.get("dependencies", []),
                "blockers": finding.get("blockers", []), "source_refs": refs,
                "source_hashes": source_hashes,
            }
            missing_sources = sorted({ref.split(":", 1)[0] for ref in refs} - set(source_hashes))
            if missing_sources:
                content["status"] = "UNVERIFIED"
                content["finding_code"] = "SOURCE_EVIDENCE_UNAVAILABLE"
                content["summary"] = "Immutable runtime source evidence is incomplete; no reachability classification was applied."
                content["blockers"] = list(content["blockers"]) + ["Missing runtime source files: " + ", ".join(missing_sources)]
            content["source_runtime_id"] = SOURCE_RUNTIME_ID
            content_hash = _digest(content)
            prior = db.connection.execute("SELECT finding_sha256 FROM paper_forward_path_coverage_audit WHERE venue=? AND switch_name=?", (content["venue"], content["switch_name"])).fetchone()
            db.connection.execute("""
                INSERT INTO paper_forward_path_coverage_audit
                    (venue,switch_name,status,finding_code,path_scope,summary,dependencies_json,blockers_json,
                     source_refs_json,source_hashes_json,finding_sha256,audited_at_utc,source_runtime_id,coverage_proven)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(venue,switch_name) DO UPDATE SET
                    status=excluded.status, finding_code=excluded.finding_code, path_scope=excluded.path_scope,
                    summary=excluded.summary,
                    dependencies_json=excluded.dependencies_json, blockers_json=excluded.blockers_json,
                    source_refs_json=excluded.source_refs_json, source_hashes_json=excluded.source_hashes_json,
                    finding_sha256=excluded.finding_sha256, audited_at_utc=excluded.audited_at_utc,
                    source_runtime_id=excluded.source_runtime_id,
                    coverage_proven=0
                WHERE paper_forward_path_coverage_audit.finding_sha256 IS NOT excluded.finding_sha256
            """, (content["venue"], content["switch_name"], content["status"], content["finding_code"], content["path_scope"], content["summary"], _canonical(content["dependencies"]), _canonical(content["blockers"]), _canonical(refs), _canonical(source_hashes), content_hash, now, content["source_runtime_id"]))
            if prior is None or prior[0] != content_hash:
                updated_findings += 1
    rows = db.query("SELECT status,COUNT(*) AS count FROM paper_forward_path_coverage GROUP BY status ORDER BY status")
    return {
        "path_rows": db.query("SELECT COUNT(*) FROM paper_forward_path_coverage_audit")[0][0],
        "scenario_count": db.query("SELECT COUNT(DISTINCT s.scenario_id) FROM paths p JOIN scenarios s ON s.path_id=p.path_id WHERE TRIM(p.switch_name)<>''")[0][0],
        "planned_run_count": db.query("SELECT COUNT(DISTINCT r.run_id) FROM paths p JOIN scenarios s ON s.path_id=p.path_id JOIN runs r ON r.scenario_id=s.scenario_id WHERE TRIM(p.switch_name)<>'' AND r.evidence_kind='catalog_only'")[0][0],
        "independent_forward_run_count": db.query("SELECT COUNT(DISTINCT r.run_id) FROM paths p JOIN scenarios s ON s.path_id=p.path_id JOIN runs r ON r.scenario_id=s.scenario_id WHERE TRIM(p.switch_name)<>'' AND r.evidence_kind='independent_forward'")[0][0],
        "coverage_proven_rows": db.query("SELECT COUNT(*) FROM paper_forward_path_coverage_audit WHERE coverage_proven<>0")[0][0],
        "status_counts": {row["status"]: row["count"] for row in rows},
        "new_default_rows": inserted_defaults,
        "new_or_changed_findings": updated_findings,
    }


class _CoverageDB:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def query(self, sql: str, params=()):
        return self.connection.execute(sql, params).fetchall()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--db", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    db_path = args.db or root / "data" / "paper_forward_lab" / "scenarios.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = _CoverageDB(db_path)
    try:
        output = refresh_path_coverage(db, root=root)
    finally:
        db.connection.close()
    print(json.dumps(output, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
