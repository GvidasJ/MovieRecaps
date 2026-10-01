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
from pathlib import Path

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
_REAL_THREADS_AFTER_FORK = vm._threads_after_fork


def _expected():
    return [r[:3] for r in vm.parallel_map(_work, ITEMS, 1, {"mul": 7}, seed=5)]


@pytest.fixture
def watchdog(monkeypatch):
    """Short watchdog limits, fresh failure bookkeeping, no spawn pool left behind."""
    monkeypatch.delenv(vm.START_METHOD_ENV, raising=False)
    monkeypatch.setitem(common.POOL_WATCHDOG, "stall_s", 4.0)
    monkeypatch.setitem(common.POOL_WATCHDOG, "poll_s", 0.1)
    monkeypatch.setitem(common.POOL_WATCHDOG, "max_failures", 2)
    monkeypatch.setattr(vm, "_threads_after_fork", lambda: 0)      # census: its own tests
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
    monkeypatch.setattr(vm, "_threads_after_fork", _REAL_THREADS_AFTER_FORK)
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


_CENSUS_SCRIPT = r"""
import json, os, sys, threading
import numpy as np
import match_cuts                                   # sets DUCC0_NUM_THREADS before any FFT
from match_cuts import common, visual_match as vm
import cv2, av, scipy.fft
from scipy.optimize import linprog

def native():
    return len(os.listdir("/proc/self/task")) - threading.active_count()

def work(state, x):
    return x * 2

out = {"start": native()}
cv2.setNumThreads(4)
cv2.GaussianBlur(np.random.rand(600, 600).astype(np.float32), (9, 9), 2)            # OpenCV pool threads
frame = av.VideoFrame.from_ndarray(np.zeros((720, 1280, 3), np.uint8), format="bgr24")
frame.reformat(format="yuv420p").to_ndarray(format="bgr24")                         # PyAV swscale slice threads
scipy.fft.rfft(np.random.rand(1 << 16))                                             # ducc FFT pool (never started)
linprog(c=[1, 1], A_ub=[[-1, -1]], b_ub=[-1], bounds=[(0, None)] * 2, method="highs")   # HiGHS scheduler
np.random.rand(600, 600) @ np.random.rand(600, 600)                                 # OpenBLAS threads
out["before_pool"] = native()
res = vm.parallel_map(work, list(range(40)), 3, {}, seed=1)
out.update(ok=res == [2 * x for x in range(40)], stats=vm.POOL_STATS, unsafe=vm._FORK_UNSAFE[0],
           cv2_threads=cv2.getNumThreads())
print(json.dumps(out))
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc thread census is Linux-only")
def test_fork_pool_runs_without_native_threads_in_the_parent(tmp_path):
    """The real census in a fresh process that has used every native thread pool the pipeline touches (OpenCV,
    PyAV's scaler, scipy FFT, HiGHS, OpenBLAS): the hygiene leaves no native thread alive across the fork, so the
    fork pool is kept (DUCC0_NUM_THREADS=1 at import, OpenCV set to one thread, PyAV's scaler released, HiGHS's
    scheduler reset, OpenBLAS stops its own threads around fork). Run in a subprocess: earlier tests of this
    session may leave threads of their own."""
    import json
    import subprocess
    script = tmp_path / "census.py"
    script.write_text(_CENSUS_SCRIPT, encoding="utf-8")
    env = dict(os.environ)
    env.pop(vm.START_METHOD_ENV, None)
    env.pop("DUCC0_NUM_THREADS", None)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(vm.__file__).resolve().parents[1]), env.get("PYTHONPATH", "")])
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", env=env,
                       timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["before_pool"] > 0                                   # the libraries really had threads running
    assert out["ok"] and out["stats"]["fork"] == 1 and out["stats"]["fork_unsafe"] == 0, out
    assert out["unsafe"] == ""
    assert out["cv2_threads"] == 4                                  # restored after the pool


def _zncc_task(state, x):
    """A score the size of a real box ROI (60000 px): its last bits depend on OpenBLAS's thread count."""
    from match_cuts import scoring
    rng = np.random.default_rng(x)
    v = rng.normal(100, 30, 60000).astype(np.float32)
    m = rng.normal(100, 30, (7, 60000)).astype(np.float32)
    return scoring.zncc_rows(v, m).tobytes()


@pytest.mark.skipif(common.blas_ctl() is None, reason="numpy's OpenBLAS is not controllable here")
def test_pool_tasks_run_single_threaded_blas_whatever_the_parent_uses(watchdog, monkeypatch):
    """Every parallel_map task (inline, fork, spawn) runs with one OpenBLAS thread: the same arithmetic on every
    path and machine. (A dot product of >= ~20000 elements is split between OpenBLAS threads, so its last bits
    follow the thread count -- and it was 7x slower for these sizes.)"""
    items = list(range(8))
    with common.single_thread_blas():
        want = [_zncc_task({}, x) for x in items]
    old = common.set_blas_threads(4)
    try:
        assert vm.parallel_map(_zncc_task, items, 1, {}, seed=1) == want            # inline
        assert vm.parallel_map(_zncc_task, items, 3, {}, seed=1) == want            # fork (Linux)
        monkeypatch.setenv(vm.START_METHOD_ENV, "spawn")
        assert vm.parallel_map(_zncc_task, items, 3, {}, seed=1) == want            # spawn
        assert common.blas_ctl()[1]() == 4                                          # the parent's setting is kept
    finally:
        if old is not None:
            common.set_blas_threads(old)


def test_single_thread_blas_restores_and_set_returns_previous():
    ctl = common.blas_ctl()
    if ctl is None:
        assert common.set_blas_threads(1) is None
        return
    old = common.set_blas_threads(3)
    try:
        assert ctl[1]() == 3
        with common.single_thread_blas():
            assert ctl[1]() == 1
        assert ctl[1]() == 3
        assert common.set_blas_threads(2) == 3
    finally:
        common.set_blas_threads(old)


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
