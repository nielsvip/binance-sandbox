#!/usr/bin/env python3
"""Install the scheduler's 7D pause marker in one worker's 30D launchers."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    args = parser.parse_args()
    root = Path.home() / "binance-sandbox/tools/worker_lanes" / args.host
    guard = "[ -e /tmp/v15_preopen_7d.pause ] && exit 0 # V15_PREOPEN_7D_PAUSE"
    for name in ("self_dispatch.sh", "overload_run.sh"):
        path = root / name
        source = path.read_text()
        if guard not in source:
            shutil.copy2(path, path.with_name(name + ".before_preopen_20261026"))
            assert source.startswith("#!/bin/bash\n")
            path.write_text(source.replace("#!/bin/bash\n", "#!/bin/bash\n" + guard + "\n", 1))
        assert guard in path.read_text()
        print(path)


if __name__ == "__main__":
    main()
