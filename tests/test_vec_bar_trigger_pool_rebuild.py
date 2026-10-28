import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.vec_bar_trigger import submit_with_rebuild


def ok_job(job):
    return ("ok", job)


def die_job(job):
    os._exit(1)


def test_broken_pool_is_rebuilt_on_submit():
    ctx = mp.get_context("spawn")
    pools = [ProcessPoolExecutor(max_workers=1, mp_context=ctx)]
    try:
        try:
            pools[0].submit(die_job, 1).result(timeout=60)
        except BrokenProcessPool:
            pass
        broken = False
        try:
            pools[0].submit(ok_job, 2)
        except BrokenProcessPool:
            broken = True
        assert broken
        old = pools[0]
        future = submit_with_rebuild(pools, 0, ok_job, 3)
        assert future.result(timeout=60) == ("ok", 3)
        assert pools[0] is not old
        assert submit_with_rebuild(pools, 0, ok_job, 4).result(timeout=60) == ("ok", 4)
    finally:
        for pool in pools:
            pool.shutdown(wait=False, cancel_futures=True)
