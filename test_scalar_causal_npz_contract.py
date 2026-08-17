import hashlib

import numpy as np

from tools import backtest_data_contract
from tools import run_vector_approx_scalar as runner


def _audit(symbol: str, profile: str, path: str, *, valid: bool = True):
    return backtest_data_contract.Audit(
        symbol=symbol,
        profile=profile,
        path=path,
        valid=valid,
        errors=[] if valid else ["stale recent parent"],
    )


def test_recent_parent_freshness_rejects_stale_npz_tail():
    base = np.arange(100, dtype=np.int64) * 300 + 10_000_000
    fresh = base - 3600
    stale = fresh.copy()
    stale[-50:] = base[-50:] - 40 * 86400

    accepted = backtest_data_contract.Audit("TTD", "core", "TTD.npz")
    backtest_data_contract._require_recent_parent_freshness(
        base, fresh, "1h", accepted
    )
    assert accepted.valid is True

    rejected = backtest_data_contract.Audit("TTD", "core", "TTD.npz")
    backtest_data_contract._require_recent_parent_freshness(
        base, stale, "1h", rejected
    )
    assert rejected.valid is False
    assert "stale recent parent" in rejected.errors[0]


def test_validated_causal_npz_contract_is_profile_and_hash_bound(
    tmp_path, monkeypatch
):
    npz_path = tmp_path / "causal_v1" / "TTD.npz"
    npz_path.parent.mkdir()
    npz_path.write_bytes(b"versioned causal npz fixture")
    calls = []

    def fake_audit(symbol, path, profile, start):
        calls.append((symbol, path, profile, start))
        return _audit(symbol, profile, str(path))

    monkeypatch.setattr(runner.npz_data_contract, "audit_npz", fake_audit)
    contract = runner._validated_causal_npz_contract(
        "TTD", npz_path, "2025-07-21"
    )

    assert contract["calculation_allowed"] is True
    assert contract["profiles_required"] == ["floor", "core", "ladder"]
    assert [call[2] for call in calls] == ["floor", "core", "ladder"]
    assert contract["npz_sha256"] == hashlib.sha256(npz_path.read_bytes()).hexdigest()
    assert contract["npz_path"] == str(npz_path.resolve())


def test_stale_raw_requires_explicit_validated_npz_opt_in(tmp_path, monkeypatch):
    npz_path = tmp_path / "causal_v1" / "ACN.npz"
    npz_path.parent.mkdir()
    npz_path.write_bytes(b"causal")
    raw = {
        "symbol": "ACN",
        "data_contract_status": "DATA_UNAVAILABLE",
        "calculation_allowed": False,
        "failures": ["1h_STALE", "4h_STALE"],
    }
    monkeypatch.setattr(
        runner.timeframe_freshness, "audit_symbol", lambda _symbol: dict(raw)
    )
    monkeypatch.setattr(
        runner.npz_data_contract,
        "audit_npz",
        lambda symbol, path, profile, start: _audit(symbol, profile, str(path)),
    )

    blocked = runner._resolve_data_contract(
        "ACN",
        npz_path,
        "2025-07-21",
        allow_validated_causal_npz=False,
    )
    assert blocked["calculation_allowed"] is False

    allowed = runner._resolve_data_contract(
        "ACN",
        npz_path,
        "2025-07-21",
        allow_validated_causal_npz=True,
    )
    assert allowed["calculation_allowed"] is True
    assert allowed["data_contract_status"] == "PASS_VALIDATED_CAUSAL_NPZ"
    assert allowed["raw_freshness_failures"] == ["1h_STALE", "4h_STALE"]
    assert allowed["causal_npz_contract"]["npz_sha256"]


def test_failed_profile_does_not_bypass_stale_raw(tmp_path, monkeypatch):
    npz_path = tmp_path / "causal_v1" / "MRVL.npz"
    npz_path.parent.mkdir()
    npz_path.write_bytes(b"causal")
    monkeypatch.setattr(
        runner.timeframe_freshness,
        "audit_symbol",
        lambda _symbol: {
            "data_contract_status": "DATA_UNAVAILABLE",
            "calculation_allowed": False,
            "failures": ["4h_STALE"],
        },
    )
    monkeypatch.setattr(
        runner.npz_data_contract,
        "audit_npz",
        lambda symbol, path, profile, start: _audit(
            symbol, profile, str(path), valid=profile != "ladder"
        ),
    )

    contract = runner._resolve_data_contract(
        "MRVL",
        npz_path,
        "2025-07-21",
        allow_validated_causal_npz=True,
    )
    assert contract["calculation_allowed"] is False
    assert "PROFILE_LADDER_FAILED" in "|".join(
        contract["causal_npz_contract"]["errors"]
    )


def test_default_npz_directory_is_never_an_override_authority(monkeypatch):
    path = runner.DEFAULT_NPZ / "MU.npz"
    monkeypatch.setattr(
        runner.npz_data_contract,
        "audit_npz",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy/default NPZ must not be audited as override authority")
        ),
    )
    contract = runner._validated_causal_npz_contract(
        "MU", path, "2025-07-21"
    )
    assert contract["calculation_allowed"] is False
    assert contract["errors"][0].startswith("NON_VERSIONED_NPZ_DIRECTORY:")
