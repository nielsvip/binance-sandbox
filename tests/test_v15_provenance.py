"""Task 07.1 provenance (USER 2026-10-15): per finished board {engine_md5, npz sha of window,
template md5, published set hash, final_gain} into progress JSON + publish manifest."""
import hashlib
import json

import v15_pilot as P


def _h(ov):
    return hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()


def test_provenance_keys():
    nid = {"md5": "abc123", "path": "/x/FOO.npz", "ts_first": 1.0, "ts_last": 2.0, "n": 10, "size": 5, "mtime_ns": 7}
    pr = P.board_provenance("FOO_LONG", {"K": "1"}, 3.25, template_path=None, npz_id=nid)
    assert pr["npz_sha"] == "abc123"
    assert pr["npz_window"] == {"ts_first": 1.0, "ts_last": 2.0, "n": 10, "size": 5, "mtime_ns": 7}
    assert pr["npz_name"] == "FOO.npz"
    assert pr["published_set_hash"] == _h({"K": "1"})
    assert pr["final_gain"] == 3.25
    assert pr["template_md5"] is None
    assert isinstance(pr["engine_md5"], str) and len(pr["engine_md5"]) == 32


def test_provenance_hash_matches_manifest_convention():
    ov = {"B": 2, "A": "x"}
    pr = P.board_provenance("S_LONG", ov, 0.0)
    assert pr["published_set_hash"] == _h(ov)


def test_provenance_template_md5(tmp_path):
    t = tmp_path / "TEMPLATE_X.xlsx"
    t.write_bytes(b"fake-template-bytes")
    pr = P.board_provenance("S_LONG", {}, 1.0, template_path=str(t), npz_id=None)
    assert pr["template_md5"] == hashlib.md5(b"fake-template-bytes").hexdigest()
    assert pr["npz_sha"] is None and pr["npz_name"] is None


def test_provenance_never_raises():
    pr = P.board_provenance("S_LONG", None, None, template_path="/nonexistent/x.xlsx", npz_id="garbage")
    assert pr["published_set_hash"] == _h({})
    assert pr["final_gain"] is None and pr["template_md5"] is None
