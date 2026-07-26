from tradier_entry_contract import flat_key_needs_evaluation


def test_isolated_wt_dc_routes_flat_key_to_real_process_position():
    assert flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=True,
    )


def test_no_entry_family_does_not_waste_exact_engine_call():
    assert not flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=False,
    )


def test_existing_upstream_routes_remain_additive():
    for field in ("satoshit_ok", "stdev_ok", "wt_force_open_enabled"):
        values = {
            "satoshit_ok": False,
            "stdev_ok": False,
            "wt_force_open_enabled": False,
            "wt_dc_path_enabled": False,
        }
        values[field] = True
        assert flat_key_needs_evaluation(**values)
