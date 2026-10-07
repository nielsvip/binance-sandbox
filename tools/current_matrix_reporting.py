#!/usr/bin/env python3
"""Read-only reporting adapter for capital-correct exact stock evidence.

It prefers full c5 evidence, then receipt-validated historical c2/c3/c4 and c1
evidence, then one-year c5 gap evidence. Historical evidence retains its
original contract provenance and is not represented as current-code parity.
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


CURRENT_CAMPAIGNS = (
    "stocks_repaired_20260730_c5",
    "stocks_repaired_20260725_c2",
    "stocks_repaired_20260725_c1",
    "stocks_repaired_20260730_c5_1yr",
)
VALID_STATUSES = ("PASS",)
CAPITAL_ACCOUNTING_VERSION = "avg-trade-deployed-2000-v1"
def _number(value: Any, default: float = float("-inf")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def reporting_root(base: Path | str | None = None) -> Path:
    override = os.environ.get("MATRIX_REPORTING_ROOT")
    if override:
        return Path(override)
    def valid(candidate: Path) -> bool:
        db = candidate / "data" / "param_results_stocks.db"
        if not db.is_file() or db.stat().st_size <= 0:
            return False
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                return (
                    con.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='param_cells'"
                    ).fetchone()
                    is not None
                    and con.execute("SELECT 1 FROM param_cells LIMIT 1").fetchone()
                    is not None
                )
            finally:
                con.close()
        except sqlite3.Error:
            return False

    sandbox = Path(
        os.environ.get("MATRIX_REPORTING_SANDBOX_ROOT", "/home/niels/binance-sandbox")
    )
    if valid(sandbox):
        return sandbox
    if base is not None and valid(Path(base)):
        return Path(base)
    return Path(base) if base is not None else Path(__file__).resolve().parent.parent


def _rows(root: Path) -> list[dict[str, Any]]:
    db = root / "data" / "param_results_stocks.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        columns = {
            row["name"] for row in con.execute("PRAGMA table_info(param_cells)")
        }
        if "capital_accounting_version" not in columns:
            return []
        placeholders = ",".join("?" for _ in CURRENT_CAMPAIGNS)
        status_placeholders = ",".join("?" for _ in VALID_STATUSES)
        rows = con.execute(
            f"""
            SELECT rowid AS result_rowid, symbol, side, campaign, param,
                   value_json, pool_sharpe, trades, acc_gain_pct,
                   gain_per_mo, delta_gain_mo_vs_bh,
                   delta_vs_baseline_gain_mo, ts, overrides_json, source_file,
                   time_in_mkt_pct, tier, trades_fingerprint,
                   validation_status, contract_fingerprint, real_closes
                   , capital_accounting_version, benchmark_deployed_usd,
                   average_deployed_usd, capital_normalization_factor,
                   raw_pnl_usd, normalized_pnl_usd, max_dd_pct
              FROM param_cells
             WHERE campaign IN ({placeholders})
               AND tier='ENGINE'
               AND validation_status IN ({status_placeholders})
               AND real_closes > 0
               AND capital_accounting_version=?
            """,
            (
                *CURRENT_CAMPAIGNS,
                *VALID_STATUSES,
                CAPITAL_ACCOUNTING_VERSION,
            ),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def _artifact_paths(root: Path, row: dict[str, Any]) -> dict[str, Path] | None:
    source = str(row.get("source_file") or "")
    prefix = f"param_matrix_daemon/{row.get('campaign')}/"
    if not source.startswith(prefix):
        return None
    tag = source[len(prefix) :]
    if not tag or "/" in tag or tag in {".", ".."}:
        return None
    symbol = str(row.get("symbol") or "").upper()
    cell_dir = (
        root
        / "data"
        / "sweep_results"
        / f"persym_campaign_{row['campaign']}_trades"
        / tag
    )
    return {
        "ledger": cell_dir / f"cell__{symbol}.jsonl",
        "audit": cell_dir / f"audit__{symbol}.json",
        "override": cell_dir / f"override__{symbol}.json",
    }


def _artifact_valid(root: Path, row: dict[str, Any]) -> tuple[bool, str]:
    paths = _artifact_paths(root, row)
    if paths is None:
        return False, "UNMAPPED_SOURCE_FILE"
    if not paths["ledger"].is_file():
        return False, "LEDGER_MISSING"
    if not paths["audit"].is_file():
        return False, "AUDIT_MISSING"
    if not paths["override"].is_file():
        return False, "OVERRIDE_MISSING"
    try:
        audit = json.loads(paths["audit"].read_text())
    except (OSError, json.JSONDecodeError):
        return False, "AUDIT_INVALID"
    if audit.get("status") != "PASS":
        return False, f"AUDIT_{audit.get('status') or 'UNKNOWN'}"
    if audit.get("contract_fingerprint") != row.get("contract_fingerprint"):
        return False, "AUDIT_CONTRACT_MISMATCH"
    try:
        artifact_override = json.loads(paths["override"].read_text())
        stored_override = json.loads(row.get("overrides_json") or "{}")
    except (OSError, json.JSONDecodeError):
        return False, "OVERRIDE_INVALID"
    if json.dumps(
        artifact_override, sort_keys=True, separators=(",", ":")
    ) != json.dumps(stored_override, sort_keys=True, separators=(",", ":")):
        return False, "OVERRIDE_DB_MISMATCH"
    audit_trade_fp = audit.get("trade_fingerprint")
    if audit_trade_fp and audit_trade_fp != row.get("trades_fingerprint"):
        return False, "AUDIT_TRADE_FINGERPRINT_MISMATCH"
    ledger_trades = []
    try:
        with paths["ledger"].open() as handle:
            for line in handle:
                try:
                    trade = json.loads(line)
                except json.JSONDecodeError:
                    return False, "LEDGER_INVALID_JSON"
                trade_side = str(
                    trade.get("position_side") or trade.get("side") or ""
                ).upper()
                if (
                    trade_side == str(row.get("side") or "").upper()
                    and trade.get("pnl_pct") is not None
                ):
                    ledger_trades.append(trade)
    except OSError:
        return False, "LEDGER_UNREADABLE"
    if len(ledger_trades) != int(row.get("trades") or 0):
        try:
            try:
                from tools import persym_baseline_campaign as psc
            except (ModuleNotFoundError, ImportError):
                import persym_baseline_campaign as psc
            intervals = psc.ur.membership_intervals(psc.ACCOUNT)
            collapsed = psc.collapse_intervals(
                intervals.get(
                    (
                        str(row.get("symbol") or "").upper(),
                        str(row.get("side") or "").upper(),
                    ),
                    [],
                ),
                psc.registry_censor_start(intervals),
            )
            filtered = [
                trade
                for trade in ledger_trades
                if psc.is_in_intervals(trade.get("entry_ts", 0), collapsed)
            ]
            if len(filtered) == int(row.get("trades") or 0):
                ledger_trades = filtered
        except Exception:
            pass
    if len(ledger_trades) != int(row.get("trades") or 0):
        return False, (
            f"LEDGER_COUNT_MISMATCH_DB_{int(row.get('trades') or 0)}"
            f"_LEDGER_{len(ledger_trades)}"
        )
    try:
        try:
            from tools import param_results_store as prs
        except (ModuleNotFoundError, ImportError):
            import param_results_store as prs
        contract = str(row.get("contract_fingerprint") or "")
        computed = prs.trades_fingerprint(
            ledger_trades,
            contract_version=contract if contract.startswith(
                "tradier-matrix-exec-c5"
            ) else None,
        )
    except Exception as exc:
        return False, f"LEDGER_FINGERPRINT_ERROR_{type(exc).__name__}"
    if computed != row.get("trades_fingerprint"):
        return False, "LEDGER_FINGERPRINT_DB_MISMATCH"
    return True, "PASS"


def _latest_fingerprint_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["campaign"]),
                str(row["symbol"]).upper(),
                str(row["side"]).upper(),
            )
        ].append(row)
    out = []
    for key_rows in grouped.values():
        latest = max(key_rows, key=lambda row: str(row.get("ts") or ""))
        fingerprint = latest.get("contract_fingerprint")
        out.extend(
            row
            for row in key_rows
            if row.get("contract_fingerprint") == fingerprint
        )
    return out


def _current_contract_fingerprints(
    rows: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], set[str]]:
    """Compute today's exact code+NPZ+side identities for observed keys.

    Matching a row to its own audit receipt proves that the row was internally
    coherent when written; it does not prove that it is valid for the code and
    frozen NPZ installed now.  The canonical matrix guard and exporter use
    ``persym_baseline_campaign.matrix_contract_fingerprints`` for that stronger
    test, so every reporting surface must use the same boundary.
    """
    try:
        try:
            from tools import persym_baseline_campaign as psc
        except (ModuleNotFoundError, ImportError):
            import persym_baseline_campaign as psc

        keys = {
            (
                str(row.get("symbol") or "").upper(),
                str(row.get("side") or "").upper(),
            )
            for row in rows
        }
        return {
            key: set(psc.matrix_contract_fingerprints(*key))
            for key in sorted(keys)
            if all(key)
        }
    except Exception as exc:
        raise RuntimeError(
            f"cannot compute current repaired matrix fingerprints: {exc}"
        ) from exc


def current_rows(
    base: Path | str | None = None,
    *,
    require_ledger: bool = True,
) -> list[dict[str, Any]]:
    root = reporting_root(base)
    raw_rows = _rows(root)
    if not raw_rows:
        return []
    current_c5_rows = [
        row
        for row in raw_rows
        if row.get("campaign")
        in {
            "stocks_repaired_20260730_c5",
            "stocks_repaired_20260730_c5_1yr",
        }
    ]
    current_contracts = (
        _current_contract_fingerprints(current_c5_rows)
        if current_c5_rows
        else {}
    )
    try:
        try:
            from tools import persym_baseline_campaign as psc
        except (ModuleNotFoundError, ImportError):
            import persym_baseline_campaign as psc
    except Exception as exc:
        raise RuntimeError(f"cannot load exact contract allowlists: {exc}") from exc

    def accepted(row):
        key = (
            str(row.get("symbol") or "").upper(),
            str(row.get("side") or "").upper(),
        )
        contract = str(row.get("contract_fingerprint") or "")
        campaign = row.get("campaign")
        if campaign in {
            "stocks_repaired_20260730_c5",
            "stocks_repaired_20260730_c5_1yr",
        }:
            return contract in current_contracts.get(key, set())
        if campaign == "stocks_repaired_20260725_c1":
            return contract.startswith("tradier-matrix-c1-20260725:")
        if campaign == "stocks_repaired_20260725_c2":
            return contract.startswith(
                (
                    "tradier-matrix-c2-20260725:",
                    "tradier-matrix-exec-c3-20260729:",
                    "tradier-matrix-exec-c4-20260729:",
                )
            )
        return False

    rows = [row for row in raw_rows if accepted(row)]
    out = []
    for row in rows:
        valid, reason = _artifact_valid(root, row)
        row["artifact_status"] = reason
        paths = _artifact_paths(root, row)
        row["trades_path"] = str(paths["ledger"]) if paths else None
        row["audit_path"] = str(paths["audit"]) if paths else None
        row["override_path"] = str(paths["override"]) if paths else None
        if require_ledger and not valid:
            continue
        out.append(row)
    return out


def merged_rows(
    base: Path | str | None = None,
    *,
    symbol: str | None = None,
    side: str | None = None,
) -> list[dict[str, Any]]:
    """Return the capital-correct canonical logical-cell matrix.

    Evidence priority is full c5, then receipt-validated c2/c3/c4, then c1,
    then one-year c5.  Lower-priority evidence only fills a missing
    ``(key, param, value)`` cell.
    """
    symbol = str(symbol or "").upper()
    side = str(side or "").upper()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in current_rows(base):
        row_symbol = str(row["symbol"]).upper()
        row_side = str(row["side"]).upper()
        if symbol and row_symbol != symbol:
            continue
        if side and row_side != side:
            continue
        grouped[(row_symbol, row_side)].append(row)
    selected = []
    for (row_symbol, row_side), candidates in grouped.items():
        by_logical_cell: dict[tuple[str, str], list[dict[str, Any]]] = (
            defaultdict(list)
        )
        for row in candidates:
            by_logical_cell[
                (str(row.get("param") or ""), str(row.get("value_json") or ""))
            ].append(row)
        merged = []
        for cell_rows in by_logical_cell.values():
            campaign_rows = defaultdict(list)
            for row in cell_rows:
                campaign_rows[str(row["campaign"])].append(row)
            chosen_campaign = next(
                (
                    campaign
                    for campaign in CURRENT_CAMPAIGNS
                    if campaign_rows.get(campaign)
                ),
                None,
            )
            if chosen_campaign is None:
                continue
            merged.append(
                max(
                    campaign_rows[chosen_campaign],
                    key=lambda row: (
                        (
                            4
                            if str(row.get("contract_fingerprint") or "").startswith(
                                "tradier-matrix-exec-c5"
                            )
                            else 3
                            if str(row.get("contract_fingerprint") or "").startswith(
                                "tradier-matrix-exec-c4"
                            )
                            else 2
                            if str(row.get("contract_fingerprint") or "").startswith(
                                "tradier-matrix-exec-c3"
                            )
                            else 1
                        ),
                        str(row.get("ts") or ""),
                        int(row.get("result_rowid") or 0),
                    ),
                )
            )
        for row in merged:
            row = dict(row)
            campaign = str(row["campaign"])
            gain_mo = row.get("gain_per_mo")
            delta_mo = row.get("delta_gain_mo_vs_bh")
            row["key"] = f"{row_symbol}_{row_side}"
            row["bh_gain_per_mo"] = (
                float(gain_mo) - float(delta_mo)
                if gain_mo is not None and delta_mo is not None
                else None
            )
            row["window"] = "1yr" if campaign.endswith("_1yr") else "full"
            selected.append(row)
    return sorted(
        selected,
        key=lambda row: (
            row["key"],
            str(row.get("param") or ""),
            str(row.get("value_json") or ""),
        ),
    )


def best_rows(
    base: Path | str | None = None,
    *,
    symbol: str | None = None,
    side: str | None = None,
) -> list[dict[str, Any]]:
    """Return one capital-correct, ledger-backed best row per symbol/side."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in merged_rows(base, symbol=symbol, side=side):
        grouped[str(row["key"])].append(row)
    selected = []
    for key, rows in grouped.items():
        best = max(
            rows,
            key=lambda row: (
                _number(row.get("delta_gain_mo_vs_bh")),
                _number(row.get("gain_per_mo")),
                int(row.get("trades") or 0),
                str(row.get("ts") or ""),
            ),
        )
        selected.append(dict(best))
    return sorted(selected, key=lambda row: row["key"])


def read_best_trades(
    symbol: str,
    side: str | None = None,
    base: Path | str | None = None,
) -> dict[str, Any]:
    rows = best_rows(base, symbol=symbol, side=side)
    payload_rows = []
    all_trades = []
    for row in rows:
        trades = []
        try:
            with Path(row["trades_path"]).open() as handle:
                for line in handle:
                    try:
                        trade = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    trade_side = str(
                        trade.get("position_side") or trade.get("side") or ""
                    ).upper()
                    if trade_side != str(row["side"]).upper():
                        continue
                    if trade.get("pnl_pct") is None:
                        continue
                    trade["_side"] = trade_side
                    trade["_campaign"] = row["campaign"]
                    trade["_param"] = row["param"]
                    trades.append(trade)
        except OSError:
            trades = []
        row = dict(row)
        row["ledger_trade_count"] = len(trades)
        row["ledger_count_matches_db"] = len(trades) == int(row.get("trades") or 0)
        payload_rows.append(row)
        all_trades.extend(trades)
    all_trades.sort(
        key=lambda trade: (
            int(trade.get("entry_ts") or 0),
            int(trade.get("exit_ts") or 0),
        )
    )
    return {
        "symbol": str(symbol).upper(),
        "side": str(side or "BOTH").upper(),
        "selection_rule": (
            "require a capital-versioned exact receipt; prefer full-window c5, "
            "then receipt-validated historical c2/c3/c4, then c1 full-window, "
            "then c5_1yr gap-fill; historical provenance is not current-code "
            "parity; require "
            "avg-trade deployed-capital normalization to $2,000; "
            "maximize gain/month delta versus side-aware B&H; require exact "
            "ENGINE PASS plus matching audit receipt and co-located ledger"
        ),
        "rows": payload_rows,
        "trades": all_trades,
        "n": len(all_trades),
    }


def summary(base: Path | str | None = None) -> dict[str, Any]:
    rows = current_rows(base)
    best = best_rows(base)
    by_campaign = {}
    for campaign in CURRENT_CAMPAIGNS:
        campaign_rows = [row for row in rows if row["campaign"] == campaign]
        by_campaign[campaign] = {
            "rows": len(campaign_rows),
            "keys": len(
                {
                    (row["symbol"], row["side"])
                    for row in campaign_rows
                }
            ),
            "latest_ts": max(
                (str(row.get("ts") or "") for row in campaign_rows),
                default=None,
            ),
        }
    qualifiers = [
        row
        for row in best
        if float(row.get("gain_per_mo") or 0) > 2.0
        and float(row.get("delta_gain_mo_vs_bh") or 0) > 0.0
    ]
    return {
        "campaigns": by_campaign,
        "best_rows": best,
        "qualifiers": qualifiers,
        "qualifier_rule": "gain/month >2% and gain/month delta versus B&H >0",
        "reporting_root": str(reporting_root(base)),
    }


def html_section(base: Path | str | None = None, *, limit: int = 12) -> str:
    report = summary(base)
    rows = sorted(
        report["best_rows"],
        key=lambda row: (
            _number(row.get("delta_gain_mo_vs_bh")),
            _number(row.get("gain_per_mo")),
        ),
        reverse=True,
    )[:limit]
    campaign_text = " · ".join(
        f"<code>{html.escape(name)}</code>: {item['rows']} exact rows / "
        f"{item['keys']} keys, latest {html.escape(item['latest_ts'] or 'none')}"
        for name, item in report["campaigns"].items()
    )
    table_rows = []
    for row in rows:
        cls = (
            "g"
            if float(row.get("gain_per_mo") or 0) > 2.0
            and float(row.get("delta_gain_mo_vs_bh") or 0) > 0.0
            else "r"
        )
        table_rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td>"
            "<td class='%s'>%+.4f%%</td><td class='%s'>%+.4f pp/mo</td>"
            "<td>%+.3f</td><td>%.2f%%</td><td>$%.2f</td>"
            "<td>%.2f%%</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                html.escape(str(row["key"])),
                html.escape(str(row["window"])),
                html.escape(f"{row['param']}={row['value_json']}"),
                cls,
                float(row.get("gain_per_mo") or 0),
                cls,
                float(row.get("delta_gain_mo_vs_bh") or 0),
                float(row.get("pool_sharpe") or 0),
                float(row.get("max_dd_pct") or 0),
                float(row.get("average_deployed_usd") or 0),
                float(row.get("time_in_mkt_pct") or 0),
                int(row.get("trades") or 0),
                html.escape(
                    f"{row.get('campaign')} / "
                    f"{row.get('capital_accounting_version')}"
                ),
                html.escape(str(row.get("ts") or "")),
            )
        )
    body = (
        "<table><tr><th>key</th><th>window</th><th>best tested cell</th>"
        "<th>gain/mo</th><th>&Delta; vs B&amp;H</th><th>pool Sharpe</th>"
        "<th>max DD</th><th>avg deployed/trade</th><th>TIM</th>"
        "<th>trades</th><th>campaign / accounting</th>"
        "<th>tested UTC</th></tr>%s</table>"
        % "".join(table_rows)
        if table_rows
        else "<p class='r'>No ledger-backed current c5 ENGINE result is available.</p>"
    )
    return (
        "<div class='box'><p><b>Current c5 exact-result lineage:</b> %s</p>"
        "<p><b>%d</b> symbol/sides currently meet the user promotion bar "
        "(%s). This is a test-result count, not a claim that those configs are "
        "present in a live active_config file.</p>%s"
        "<p style='font-size:11px;color:#888'>Selection is per symbol/side: "
        "merge by logical cell in the order full c5, validated c2/c3/c4, c1, "
        "then current c5 1yr gap-fill; then maximize gain/month delta versus "
        "side-aware B&amp;H. Every row "
        "requires an exact ENGINE PASS, matching audit fingerprint, and co-located "
        "trade ledger.</p></div>"
        % (
            campaign_text,
            len(report["qualifiers"]),
            html.escape(report["qualifier_rule"]),
            body,
        )
    )
