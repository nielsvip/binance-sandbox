#!/usr/bin/env python3
import sys

sys.path.insert(0, "tools")
import param_matrix_daemon as daemon

parameter = "WT_DC_ENTRY_THRESHOLD"
daemon.all_cells = lambda *args, **kwargs: [
    (parameter, "35", {parameter: 35}),
    (parameter, "75", {parameter: 75}),
    (parameter, "45", {parameter: 45}),
]
daemon.main()
