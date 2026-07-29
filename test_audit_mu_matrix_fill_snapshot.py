import csv
import gzip
from datetime import datetime, timezone

from tools.audit_mu_matrix_fill_snapshot import audit


def test_matrix_audit_detects_shadow_and_namespace_problems(tmp_path):
    matrix = tmp_path / "matrix.csv.gz"
    digest = tmp_path / "digest.md"
    fields = ["main_switch", "sub_setting", "value", "status", "MU_LONG"]
    rows = [
        ["ENTRY", "RSI_EXIT_LONG_TRADIER", "80", "RECONNECT", "-3.3471"],
        ["ENTRY", "RSI_EXIT_LONG_TRADIER", "120", "RECONNECT", "-3.3471"],
        ["TRC_ENTRY", "", "true", "RECONNECT", "-3.3471"],
        ["STOP_PACK", "", "lean", "OK", "-0.5"],
    ]
    with gzip.open(matrix, "wt", newline="") as handle:
        handle.write("# comment\n")
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)
    digest.write_text("digest\n")

    result = audit(matrix, digest, datetime.now(timezone.utc))

    assert result["counts"]["mu_filled_rows"] == 4
    assert result["counts"]["identical_output_multi_value_keys"] == 1
    assert result["counts"]["wrong_account_rows"] == 1
    assert result["counts"]["out_of_domain_rows"] == 1
    assert result["counts"]["positive_delta_rows"] == 0
    assert result["interpretation"]["suitable_as_live_promotion_evidence"] is False


def test_budget_name_is_not_treated_as_bounded_oscillator(tmp_path):
    matrix = tmp_path / "matrix.csv.gz"
    digest = tmp_path / "digest.md"
    with gzip.open(matrix, "wt", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["main_switch", "sub_setting", "value", "status", "MU_LONG"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "main_switch": "SMFI_LONG_BUDGET",
                "sub_setting": "",
                "value": "3000",
                "status": "OK",
                "MU_LONG": "-1",
            }
        )
    digest.write_text("digest\n")
    result = audit(matrix, digest, datetime.now(timezone.utc))
    assert result["counts"]["out_of_domain_rows"] == 0
