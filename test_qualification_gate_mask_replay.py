import numpy as np

from tools import run_qualification_gate_mask_replay as replay


def test_negative_strategy_cannot_qualify_by_beating_negative_bh(monkeypatch):
    """Bible §16.43 needs positive return in every required fold."""
    n = 30
    close = np.empty(n, dtype=float)
    for index in range(0, n, 2):
        entry = 100.0 + 50.0 * (index // 2)
        close[index] = entry
        close[index + 1] = entry * 1.10  # every SHORT laboratory trade loses
    mask = np.zeros(n, dtype=bool)
    mask[::2] = True
    exit_mask = np.zeros(n, dtype=bool)
    exit_mask[1::2] = True
    score = np.ones(n, dtype=np.float32)
    family = np.zeros(n, dtype=np.int8)

    def formation_mask(_arrays, *, action, **_kwargs):
        return (mask if action == "ENTRY" else exit_mask), score, family

    monkeypatch.setattr(replay, "formation_vector_mask", formation_mask)
    monkeypatch.setattr(replay.v8, "_tradier_counter_trend_entry_allowed_vec", lambda *_a, **_k: np.ones(n, dtype=bool))
    monkeypatch.setattr(replay, "completed_parent_audit", lambda *_a, **_k: {"pass": True})
    evidence = replay.replay_window(
        "TEST", "SHORT", {"close": close}, np.arange(n, dtype=np.int64) + 1,
        object(), "15m", 0.0,
    )

    assert evidence["trades"] == 15
    assert evidence["return_pct"] < 0
    assert evidence["alpha_vs_bh_pp"] > 0  # it still beats its negative B&H
    assert "RETURN_NOT_STRICTLY_POSITIVE" in evidence["strict_failures"]
    assert evidence["strict_pass"] is False
