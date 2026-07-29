from tools import param_matrix_daemon as daemon


def test_trb_does_not_exact_test_other_account_namespaces():
    assert daemon.wrong_account_namespace("TRA_MIN_HOLD_MINUTES", "trb")
    assert daemon.wrong_account_namespace("TRC_CONNORS_RSI_ENABLED", "trb")
    assert not daemon.wrong_account_namespace("TRB_MAX_LONG_VALUE", "trb")
    assert not daemon.wrong_account_namespace("WT_3M_FORCE_OPEN_ENABLED", "trb")


def test_trc_does_not_exact_test_trb_namespace():
    assert daemon.wrong_account_namespace("TRB_MAX_LONG_VALUE", "trc")
    assert not daemon.wrong_account_namespace("TRC_CONNORS_RSI_ENABLED", "trc")
