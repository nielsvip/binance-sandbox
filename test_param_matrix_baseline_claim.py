from tools import param_matrix_daemon as daemon


class _NoRowCursor:
    def fetchone(self):
        return None


class _NoBaselineConnection:
    def execute(self, *_args, **_kwargs):
        return _NoRowCursor()


def test_sibling_baseline_claim_is_contention_not_failure(monkeypatch):
    monkeypatch.setattr(
        daemon.psc, "matrix_contract_fingerprint", lambda *_: "current-fp"
    )
    monkeypatch.setattr(
        daemon.psc, "matrix_contract_fingerprints", lambda *_: {"current-fp"}
    )
    monkeypatch.setattr(daemon, "claim", lambda *_: False)

    result = daemon.ensure_safe_baseline(
        {"con": _NoBaselineConnection()}, "MU", "LONG"
    )

    assert result is daemon.BASELINE_IN_PROGRESS
