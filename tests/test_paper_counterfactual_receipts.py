import tools.paper_forward_comprehensive as scorer

def event(timestamp, kind="exit", name="DC_BASIS_3M_REDUCE", side="LONG"):
    return {"ts": "2026-10-31T00:00:00+00:00", "position_key": f"paper:TEST_{side}", "venue": "crypto", "account": "paper", "fn": name, "kind": kind, "action": "CLOSE" if kind == "exit" else "OPEN", "_t": timestamp, "_venue": "crypto", "_fam": name}

def test_callback_preserves_score_and_original_event(monkeypatch):
    monkeypatch.setattr(scorer, "close_at", lambda *args, **kwargs: (101.0, 900.0))
    monkeypatch.setattr(scorer, "real_closes", lambda *args: [(1100.0, 100.0)])
    observations = [event(1000.0), event(1001.0)]
    records = []
    expected = scorer.score(observations, 16)
    assert scorer.score(observations, 16, action_records=records) == expected
    assert [record["status"] for record in records] == ["SCORED", "DEDUPLICATED"]
    assert records[0]["delta_fraction"] == 0.01
    assert records[0]["comparison"]["baseline_ts"] == 1100.0
    assert records[0]["comparison"]["native_execution_validated"] is False
    assert records[0]["comparison"]["price_basis"] == "15m_bar_proxy"
    assert records[0]["event"] == {key: value for key, value in observations[0].items() if not key.startswith("_")}

def test_pending_outcomes_are_null_and_loss_is_retained(monkeypatch):
    monkeypatch.setattr(scorer, "close_at", lambda *args, **kwargs: (99.0, 900.0))
    monkeypatch.setattr(scorer, "real_closes", lambda *args: [(1100.0, 100.0)])
    records = []
    scorer.score([event(1000.0)], 16, action_records=records)
    assert records[0]["delta_fraction"] == -0.01
    monkeypatch.setattr(scorer, "real_closes", lambda *args: [])
    records = []
    scorer.score([event(1000.0)], 16, action_records=records)
    assert records[0]["status"] == "PENDING_BASELINE"
    assert records[0]["delta_fraction"] is None
    monkeypatch.setattr(scorer, "close_at", lambda *args, **kwargs: (None, None) if kwargs.get("offset") else (99.0, 900.0))
    records = []
    scorer.score([event(1000.0, kind="entry")], 16, action_records=records)
    assert records[0]["status"] == "PENDING_HORIZON"
    assert records[0]["delta_fraction"] is None
