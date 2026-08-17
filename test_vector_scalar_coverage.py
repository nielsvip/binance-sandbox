import json

from tools import summarize_vector_scalar_coverage as coverage


def test_coverage_is_separate_and_excludes_retired_adapter(tmp_path):
    rows = [
        {
            "key": "MU_LONG",
            "param": "NATIVE",
            "value_json": "true",
            "status": "MOVED",
            "vector_adapter": None,
        },
        {
            "key": "MU_LONG",
            "param": "BREAKOUT_SIZE_EMA200_T2_PCT",
            "value_json": "1.5",
            "status": "INERT",
            "vector_adapter": "BREAKOUT_SIZE_SMA200_T2_PCT",
        },
        {
            "key": "MU_LONG",
            "param": "BREAKOUT_SIZE_EMA200_T1_PCT",
            "value_json": "1.5",
            "status": "MOVED",
            "vector_adapter": "BREAKOUT_SIZE_SMA200_T1_PCT",
        },
    ]
    (tmp_path / "MU_LONG.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )

    payload = coverage.build(tmp_path)

    assert payload["tier"] == "VEC_DIAGNOSTIC"
    assert payload["exact_completion_credit"] is False
    assert payload["promotion_allowed"] is False
    assert payload["engine_ranking_allowed"] is False
    assert payload["screened_cells"] == 2
    assert payload["per_key"]["MU_LONG"]["moved"] == 1
    assert payload["per_key"]["MU_LONG"]["inert"] == 1
    assert (
        payload["per_key"]["MU_LONG"]["sampled_exact_parity_adapter_cells"]
        == 1
    )
