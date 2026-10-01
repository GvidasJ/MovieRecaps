"""Worker-pool hang protection (DESIGN D7): native-thread hygiene before forking, the pool watchdog (stuck /
dead workers -> the remaining tasks run in this process with identical results), progress lines and the stage
heartbeat.

The stuck / dead workers are simulated: the item function blocks (or kills its own process) only when it runs
in a worker process, never in the parent, so the serial fallback finishes the call.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import sys
import time

import numpy as np
import pytest

from match_cuts import common
from match_cuts import visual_match as vm


def _work(state, x):
    """Module-level (picklable) item function: a value of OpenCV's RNG (seeded before every item, like RANSAC
    in the real workers) + where it ran."""
    import cv2
    v = np.zeros(1, np.float64)
    cv2.randu(v, 0.0, 1.0)
    return (x, state["mul"] * x, float(v[0]), os.getpid())


def _stuck(state, x):
    """Blocks forever on item state['bad'] in a WORKER process (never in the parent)."""
    if x == state["bad"] and os.getpid() != state["parent"]:
        time.sleep(3600)
    return _work(state, x)


def _dies(state, x):
    """The worker that gets item state['bad'] is killed (as the OS out-of-memory killer would do)."""
    if x == state["bad"] and os.getpid() != state["parent"]:
        if hasattr(signal, "SIGKILL"):
            os.kill(os.getpid(), signal.SIGKILL)
        os._exit(3)
    return _work(state, x)


ITEMS = list(range(48))


def _expected():
    return [r[:3] for r in vm.parallel_map(_work, ITEMS, 1, {"mul": 7}, seed=5)]


@pytest.fixture
def watchdog(monkeypatch):
    """Short watchdog limits, fresh failure bookkeeping, no spawn pool left behind."""
    monkeypatch.delenv(vm.START_METHOD_ENV, raising=False)
    monkeypatch.setitem(common.POOL_WATCHDOG, "stall_s", 4.0)
    monkeypatch.setitem(common.POOL_WATCHDOG, "poll_s", 0.1)
    monkeypatch.setitem(common.POOL_WATCHDOG, "max_failures", 2)
    saved = (vm._POOL_FAILURES[0], vm._FORK_UNSAFE[0])
    vm._POOL_FAILURES[0], vm._FORK_UNSAFE[0] = 0, ""
    try:
        yield
    finally:
        vm._POOL_FAILURES[0], vm._FORK_UNSAFE[0] = saved
        vm.shutdown_workers(kill=True)


@pytest.mark.parametrize("method", ["fork", "spawn"])
def test_stuck_worker_is_stopped_and_the_rest_runs_here(watchdog, monkeypatch, caplog, method):
    if method == "fork" and "fork" not in mp.get_all_start_methods():
        pytest.skip("no fork on this platform")
    monkeypatch.setenv(vm.START_METHOD_ENV, method)
    before = dict(vm.POOL_STATS)
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        res = vm.parallel_map(_stuck, ITEMS, 3, {"mul": 7, "bad": 13, "parent": os.getpid()}, seed=5)
    dt = time.monotonic() - t0
    assert [r[:3] for r in res] == _expected()                    # bit-identical to the inline run
    assert res[13][3] == os.getpid()                               # the stuck item was finished here
    assert vm.POOL_STATS["watchdog"] == before["watchdog"] + 1
    assert vm.POOL_STATS[method] == before[method] + 1
    assert "no result for" in caplog.text and "stopped the worker pool" in caplog.text
    assert dt < 60, dt                                              # never waits for the stuck task
    if method == "spawn":
        assert vm._POOL["pool"] is None                             # a stopped pool is never reused


@pytest.mark.parametrize("method", ["fork", "spawn"])
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_dead_worker_is_detected_without_waiting_for_the_timeout(watchdog, monkeypatch, caplog, method):
    """multiprocessing.Pool replaces a killed worker but loses its task: without the watchdog the call never
    returns. The dead process is noticed at the next poll, long before the stall timeout."""
    if method == "fork" and "fork" not in mp.get_all_start_methods():
        pytest.skip("no fork on this platform")
    monkeypatch.setenv(vm.START_METHOD_ENV, method)
    monkeypatch.setitem(common.POOL_WATCHDOG, "stall_s", 600.0)
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        res = vm.parallel_map(_dies, ITEMS, 3, {"mul": 7, "bad": 20, "parent": os.getpid()}, seed=5)
    assert [r[:3] for r in res] == _expected()
    assert "exited unexpectedly" in caplog.text
    assert time.monotonic() - t0 < 120


def test_pools_are_disabled_after_repeated_failures(watchdog, monkeypatch, caplog):
    monkeypatch.setenv(vm.START_METHOD_ENV, "fork" if "fork" in mp.get_all_start_methods() else "spawn")
    monkeypatch.setitem(common.POOL_WATCHDOG, "stall_s", 2.0)
    state = {"mul": 7, "bad": 3, "parent": os.getpid()}
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        vm.parallel_map(_stuck, ITEMS, 3, state, seed=5)
        vm.parallel_map(_stuck, ITEMS, 3, state, seed=5)
    assert "later steps of this run will not use worker pools" in caplog.text
    before = dict(vm.POOL_STATS)
    res = vm.parallel_map(_stuck, ITEMS, 3, state, seed=5)       # would block in a worker: runs here now
    assert [r[:3] for r in res] == _expected() and {r[3] for r in res} == {os.getpid()}
    assert vm.POOL_STATS["pools_disabled"] == before["pools_disabled"] + 1


@pytest.mark.skipif("fork" not in mp.get_all_start_methods() or not sys.platform.startswith("linux"),
                    reason="fork + /proc census are Linux-only")
def test_native_threads_surviving_the_fork_switch_to_spawn(watchdog, monkeypatch, caplog):
    """When the census right after forking still finds native threads, that fork pool is discarded unused and
    this call and every later one use spawn workers (identical results)."""
    monkeypatch.setattr(vm, "native_threads", lambda: 3)
    before = dict(vm.POOL_STATS)
    with caplog.at_level(logging.WARNING, logger="match_cuts"):
        res = vm.parallel_map(_work, ITEMS, 3, {"mul": 7}, seed=5)
    assert [r[:3] for r in res] == _expected()
    assert os.getpid() not in {r[3] for r in res}                  # ran in (spawn) workers
    assert vm.POOL_STATS["fork_unsafe"] == before["fork_unsafe"] + 1
    assert vm.POOL_STATS["spawn"] == before["spawn"] + 1 and vm.POOL_STATS["fork"] == before["fork"]
    assert "native thread(s) were running" in caplog.text and vm._FORK_UNSAFE[0]
    monkeypatch.setattr(vm, "native_threads", lambda: 0)
    vm.parallel_map(_work, ITEMS, 3, {"mul": 7}, seed=5)            # stays on spawn for the rest of the run
    assert vm.POOL_STATS["spawn"] == before["spawn"] + 2 and vm.POOL_STATS["fork"] == before["fork"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc thread census is Linux-only")
def test_fork_pool_runs_without_native_threads_in_the_parent(watchdog):
    """The real census in this test process: hygiene leaves no native thread alive across the fork, so the
    fork pool is kept (the package sets DUCC0_NUM_THREADS=1, OpenCV is set to one thread, PyAV's scaler is
    released, OpenBLAS stops its own threads around fork)."""
    import cv2
    cv2.setNumThreads(4)
    cv2.GaussianBlur(np.random.rand(600, 600).astype(np.float32), (9, 9), 2)     # OpenCV pool threads exist
    before = dict(vm.POOL_STATS)
    res = vm.parallel_map(_work, ITEMS, 3, {"mul": 7}, seed=5)
    assert [r[:3] for r in res] == _expected()
    assert vm.POOL_STATS["fork_unsafe"] == before["fork_unsafe"], vm._FORK_UNSAFE
    assert vm.POOL_STATS["fork"] == before["fork"] + 1
    assert cv2.getNumThreads() == 4                                 # restored after the pool


def test_package_never_starts_the_ducc_fft_pool():
    import match_cuts  # noqa: F401 - the import sets it
    assert os.environ.get("DUCC0_NUM_THREADS") == "1"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc thread census is Linux-only")
def test_release_native_threads_frees_pyav_scaler_threads():
    av = pytest.importorskip("av")
    import av.video.frame as vfm
    if not hasattr(vfm, "_thread_local"):
        pytest.skip("this PyAV keeps no per-thread scaler")
    common.release_native_threads()
    base = common.native_threads()
    frame = av.VideoFrame.from_ndarray(np.zeros((720, 1280, 3), np.uint8), format="bgr24")
    frame.reformat(format="yuv420p").to_ndarray(format="bgr24")
    assert vfm._thread_local.reformatter is not None
    with_scaler = common.native_threads()
    common.release_native_threads()
    assert vfm._thread_local.reformatter is None
    assert common.native_threads() <= with_scaler
    assert common.native_threads() <= base


@pytest.mark.skipif("fork" not in mp.get_all_start_methods() or not hasattr(signal, "SIGKILL"),
                    reason="needs fork + SIGKILL")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_close_pool_never_blocks_on_a_broken_pool():
    """An idle pool worker holds the task queue's lock while it waits for work. Killed there (as the OS
    out-of-memory killer does), it leaves that lock held forever: the replacement workers block on it and
    Pool.terminate() waits for it too. close_pool gives up after wait_s, kills the workers and leaves the
    cleanup to a daemon thread."""
    pool = mp.get_context("fork").Pool(2)
    assert pool.map(abs, [-1, -2, -3]) == [1, 2, 3]       # both workers started and idle
    for p in common.pool_workers(pool):
        os.kill(p.pid, signal.SIGKILL)
        p.join(5)
    time.sleep(0.5)                                        # the pool replaces them; they wait on the dead lock
    try:
        t0 = time.monotonic()
        clean = common.close_pool(pool, wait_s=1.0)
        assert time.monotonic() - t0 < 10
        assert clean is False
        for p in common.pool_workers(pool):
            p.join(5)
            assert p.exitcode is not None                  # the replacement workers were killed too
    finally:
        pool._inqueue._rlock.release()                     # let the abandoned cleanup thread finish


def test_close_pool_on_a_healthy_pool_is_clean():
    pool = mp.get_context("spawn").Pool(1)
    assert pool.apply(abs, (-3,)) == 3
    assert common.close_pool(pool) is True


def test_watched_results_progress_lines(monkeypatch, caplog):
    """Long pool calls log '<stage>: <name>: done/total tasks done (s)' at least every progress_s."""
    monkeypatch.setitem(common.POOL_WATCHDOG, "progress_s", 0.2)
    monkeypatch.setitem(common.POOL_WATCHDOG, "poll_s", 0.05)

    class SlowIt:
        def __init__(self):
            self.i = 0

        def next(self, timeout=None):
            time.sleep(0.15)
            self.i += 1
            return self.i
    with caplog.at_level(logging.INFO, logger="match_cuts"), common.stage_heartbeat("S5.3 refine"):
        got = list(common.watched_results(SlowIt(), 6, "eval", count=lambda r: 2, total_items=12))
    assert got == [1, 2, 3, 4, 5, 6]
    assert "S5.3 refine: eval:" in caplog.text and "/12 tasks done" in caplog.text


def test_watched_results_stall_raises(monkeypatch):
    monkeypatch.setitem(common.POOL_WATCHDOG, "stall_s", 0.3)
    monkeypatch.setitem(common.POOL_WATCHDOG, "poll_s", 0.05)

    class Never:
        def next(self, timeout=None):
            time.sleep(timeout or 0)
            raise mp.TimeoutError

    with pytest.raises(common.PoolFailure, match="no result for"):
        list(common.watched_results(Never(), 3, "x"))


def test_stage_heartbeat_breaks_silence(monkeypatch, caplog):
    monkeypatch.setitem(common.POOL_WATCHDOG, "progress_s", 0.3)
    with caplog.at_level(logging.INFO, logger="match_cuts"):
        with common.stage_heartbeat("S9 verify"):
            assert common.current_stage() == "S9 verify"
            time.sleep(1.2)
        n = caplog.text.count("S9 verify: still running")
        time.sleep(0.7)                                             # stopped with the stage
    assert n >= 2 and caplog.text.count("S9 verify: still running") == n
    assert common.current_stage() == ""


def test_heartbeat_reports_the_open_progress_counter(monkeypatch, caplog):
    """A silent period gives ONE line: the open counter's 'done/total' (not a bare 'still running')."""
    monkeypatch.setitem(common.POOL_WATCHDOG, "progress_s", 0.3)
    with caplog.at_level(logging.INFO, logger="match_cuts"), common.stage_heartbeat("S5.3 refine"):
        with common.Progress("ecc", 40) as prog:
            prog.done = 12
            time.sleep(0.8)                                         # no step() calls: only the heartbeat logs
    assert "S5.3 refine: ecc: 12/40 tasks done" in caplog.text
    assert "still running" not in caplog.text
    assert not common._ACTIVE


def test_task_names():
    assert vm._task_name(vm._search_worker) == "search"
    assert vm._task_name(_work) == "work"
    from match_cuts import refine
    assert vm._task_name(refine._w_eval) == "eval"


def test_stage_heartbeat_quiet_while_the_stage_logs(monkeypatch, caplog):
    monkeypatch.setitem(common.POOL_WATCHDOG, "progress_s", 0.4)
    with caplog.at_level(logging.INFO, logger="match_cuts"), common.stage_heartbeat("S5.2 visual search"):
        for _ in range(8):
            common.log.info("working")
            time.sleep(0.1)
    assert "still running" not in caplog.text


def test_pool_settings_from_config():
    from match_cuts.config import Config
    cfg = Config()
    keys = cfg.analysis_params()
    for k in ("pool_stall_timeout_s", "pool_max_failures", "progress_log_s"):
        assert hasattr(cfg, k) and k not in keys                    # never part of the analysis cache keys
    saved = dict(common.POOL_WATCHDOG)
    try:
        common.configure_pools(stall_s=120, progress_s=15, max_failures=1)
        assert common.POOL_WATCHDOG["stall_s"] == 120 and common.POOL_WATCHDOG["progress_s"] == 15
        assert common.POOL_WATCHDOG["max_failures"] == 1
    finally:
        common.POOL_WATCHDOG.update(saved)
