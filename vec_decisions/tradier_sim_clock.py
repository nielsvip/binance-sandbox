"""Frozen replay clock injection for the native Tradier decision module."""


def patch_tradier_sim_time(sim_time, mode):
    if mode == "tradier":
        import tradier_manage

        tradier_manage.time = sim_time
