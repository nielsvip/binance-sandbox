from tools import audit_repaired_matrix_fleet as fleet


def _proc(pid, ppid, sid, argv):
    return {"pid": pid, "ppid": ppid, "sid": sid, "argv": argv}


def test_manifest_workers_required_but_safe_extras_remain_valid_owners():
    processes = {
        10: _proc(
            10,
            1,
            10,
            [
                "python",
                "tools/param_matrix_daemon.py",
                "--tag",
                "rm1",
                "--only",
                "MU",
                "--side",
                "LONG",
                "--safe-contract",
            ],
        ),
        20: _proc(
            20,
            1,
            20,
            [
                "python",
                "tools/param_matrix_daemon.py",
                "--tag",
                "rn1",
                "--only",
                "NVDA",
                "--side",
                "LONG",
                "--safe-contract",
            ],
        ),
    }
    workers, owners, extras, failures = fleet.classify_workers(
        processes, {"rm1": ("MU", "LONG")}
    )
    assert failures == []
    assert owners == {10, 20}
    assert extras == [
        {
            "tag": "rn1",
            "pid": 20,
            "ppid": 1,
            "sid": 20,
            "symbol": "NVDA",
            "side": "LONG",
            "manifested": False,
        }
    ]
    assert workers["rm1"][0]["manifested"] is True


def test_missing_manifest_worker_still_fails():
    _, _, _, failures = fleet.classify_workers(
        {}, {"rm1": ("MU", "LONG")}
    )
    assert failures == ["rm1: expected one worker, observed 0"]


def test_user_systemd_supervised_safe_worker_is_independently_owned():
    processes = {
        1410: _proc(1410, 1, 1410, ["/usr/lib/systemd/systemd", "--user"]),
        30: _proc(
            30,
            1410,
            30,
            [
                "python",
                "tools/param_matrix_daemon.py",
                "--tag",
                "smoke",
                "--only",
                "MU",
                "--side",
                "LONG",
                "--safe-contract",
                "--once",
            ],
        ),
    }
    _, owners, extras, failures = fleet.classify_workers(
        processes, {"rm1": ("MU", "LONG")}
    )
    assert owners == {30}
    assert extras[0]["tag"] == "smoke"
    assert failures == ["rm1: expected one worker, observed 0"]
