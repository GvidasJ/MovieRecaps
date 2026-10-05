"""Stage 5.2 -- visual candidate search (DESIGN.md §5 visual_match.py).

A competitor frame is matched to RAW with keypoints, never with shot detection:

* :class:`RawIndex` holds SIFT descriptors of sampled RAW proxy frames (uint8 storage -- SIFT values
  are integers <= 255, so this is lossless -- cast to float32 for a FLANN kd-tree). Voting is
  cluster-aware (:meth:`RawIndex.query`): a plain Lowe ratio across index frames is never used,
  because adjacent index frames hold near-duplicate descriptors.
* :func:`search_frame` verifies the voted candidates of ONE competitor frame: pairwise Lowe ratio 0.75
  against a single RAW frame, ``cv2.estimateAffinePartial2D`` RANSAC estimated RAW -> comp (§2.2),
  the flip hypothesis against the SIFT features of ``cv2.flip(raw, 1)`` (the result of
  ``from_cv_matrix(M, flip=False)`` IS the canonical Sim for ``flip_h=True``), and masked ZNCC of the
  warped candidate (``scoring``). Accepted matches are re-estimated against the best EXACT RAW frame
  near the index frame (``_reestimate``: coarse-to-fine ECC, :func:`refine.ecc_measure`, on jb-1..jb+1 from
  the RANSAC Sim and its derotated version; a rotation must beat a theta = 0 refit) before they become
  :class:`Anchor`s.
  SIFT features of a mirrored image are an exact permutation of the originals (precise-upscale SIFT,
  :func:`mirror_features`), so flip votes and flipped verification need no second SIFT pass.
* :func:`sparse_search` runs :func:`search_frame` every ``cfg.comp_search_stride`` competitor frames
  (audio-restricted first, global fallback) in a worker pool.

Shared helpers used by ``refine`` live here too: :class:`AllowedMasks` (box & ~static & ~overlay
masks), :func:`parallel_map` (deterministic worker pool: fork on Linux, spawn on Windows / macOS or with
``MATCH_CUTS_START_METHOD=spawn``; bit-identical results either way), :func:`proxy_id` (cache
identities).
"""
from __future__ import annotations

import atexit
import hashlib
import math
import multiprocessing as mp
import os
import pickle
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from . import common as _common
from . import scoring
from .common import (POOL_WATCHDOG, Cache, DecisionLog, PoolFailure, Progress, close_pool, file_hash, fps_str, log,
                     native_threads, null_dlog, params_hash, pool_workers, progress_name, release_native_threads,
                     seed_everything, single_thread_blas, stage_key, watched_results)
from .geometry import Sim, from_cv_matrix
from .model import AudioHints, Layout, Proxy

__all__ = ["RawIndex", "Anchor", "search_frame", "sparse_search", "run_searches", "AllowedMasks", "parallel_map",
           "proxy_id", "box_roi", "audio_window", "detect_sift", "mirror_features", "start_method",
           "shutdown_workers"]


# ---------------------------------------------------------------------------------------------
# Deterministic worker pools (fork on Linux, spawn elsewhere -- DESIGN D7)
# ---------------------------------------------------------------------------------------------

_WSTATE: dict[str, Any] = {}
START_METHOD_ENV = "MATCH_CUTS_START_METHOD"
# how parallel_map calls ran in THIS process (diagnostics + tests): inline / fork / spawn pools,
# spawn requests that fell back to inline (unpicklable state or an unguarded __main__), pools the watchdog
# stopped, fork pools discarded because native threads survived the fork, calls run inline after too many stops
POOL_STATS: dict[str, int] = {"inline": 0, "fork": 0, "spawn": 0, "spawn_fallback": 0, "spawn_mem_capped": 0,
                              "watchdog": 0, "fork_unsafe": 0, "pools_disabled": 0}
_WARNED: set[str] = set()

# Spawn workers cannot share the parent's FLANN kd-tree (fork shares it copy-on-write): every worker that
# unpickles a RawIndex loads its own tree -- private memory, measured ~740-780 B per descriptor (4
# randomised kd-trees), ~1.5 GB at cfg.index_max_descriptors = 2M. The spawn pool size is therefore capped
# by the available RAM when the state holds a RawIndex (_spawn_mem_cap); the descriptors themselves are
# memmapped (page cache, shared by all workers) and counted once.
SPAWN_TREE_BYTES_PER_DESC = 800
SPAWN_WORKER_BASE_BYTES = 300 << 20        # interpreter + numpy / OpenCV + per-task working set
SPAWN_MEM_MARGIN_BYTES = 768 << 20         # kept free for the parent / OS (at least; or 10 % of available)
# Every other spawn pool is capped too: a worker on refine's tasks holds ~0.85 GB of private memory (measured on
# the Deadpool clip: 30 workers, 28 GB) -- with After Effects holding 47 GB, 30 such workers left Windows 4 GB.
SPAWN_WORKER_EST_BYTES = 1 << 30
_MEM_CAPS: dict[tuple, int] = {}           # (index identity or 'any', descriptors, requested workers) -> cap (once)


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        log.warning(msg, *args)


def _invoke(item: Any) -> Any:
    """Pool entry point: runs ``state['__fn__'](state, item)`` with a per-item RNG seed."""
    st = _WSTATE
    seed_everything(st["__seed__"])
    return st["__fn__"](st, item)


def _fork_available() -> bool:
    return "fork" in mp.get_all_start_methods()


def _platform() -> str:
    import sys
    return sys.platform


def start_method() -> str:
    """'fork' or 'spawn': fork only on Linux (on macOS fork is unsafe with system frameworks, Windows
    has none); ``MATCH_CUTS_START_METHOD=fork|spawn`` overrides (fork falls back to spawn where the
    platform lacks it)."""
    env = os.environ.get(START_METHOD_ENV, "").strip().lower()
    if env == "spawn":
        return "spawn"
    if env == "fork":
        if _fork_available():
            return "fork"
        _warn_once("env_fork", "%s=fork: the fork start method is unavailable on this platform - using spawn",
                   START_METHOD_ENV)
        return "spawn"
    if env:
        _warn_once("env_bad", "%s=%r ignored (expected 'fork' or 'spawn')", START_METHOD_ENV, env)
    return "fork" if _platform().startswith("linux") and _fork_available() else "spawn"


def _spawn_safe() -> bool:
    """Spawned workers re-import the parent's ``__main__``: safe for ``python -m pkg`` entry points
    (incl. pytest), console-script launchers, a guarded script (``if __name__ == "__main__":``) or no
    main file (interactive / ``-c``)."""
    import sys
    m = sys.modules.get("__main__")
    f = getattr(m, "__file__", None)
    if not f:
        return True
    spec = getattr(m, "__spec__", None)
    if spec is not None and str(getattr(spec, "name", "")).endswith("__main__"):
        return True
    try:
        txt = Path(f).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "__name__" in txt and "__main__" in txt


# One persistent spawn pool per process (spawn start-up + imports cost ~0.3-1 s per pool, and refine
# calls parallel_map dozens of times): state reaches the workers through a pickle file per call, loaded
# once per worker and call (token), so memmapped proxies are re-opened, never copied.
_POOL: dict[str, Any] = {"pool": None, "n": 0, "dir": None, "pid": None}
_CALL_SEQ = [0]
_SPAWN_TOKEN: list[str | None] = [None]


def _spawn_init() -> None:
    """Spawn worker initializer: one OpenCV thread per worker (as in the fork path; safe after spawn)."""
    import cv2
    cv2.setNumThreads(1)


def _spawn_invoke(task: tuple[str, str, Any]) -> Any:
    """Spawn worker entry: (token, state file, item). The state is (re)loaded when the token changes; a
    state without a RawIndex (refine's S5.3 calls) drops this worker's cached FLANN trees, so they are not
    held (~800 B per descriptor each) through the rest of the run."""
    global _WSTATE
    token, path, item = task
    if _SPAWN_TOKEN[0] != token:
        _WSTATE = {}                             # release the previous call's state (memmaps) first
        _SPAWN_TOKEN[0] = None
        with open(path, "rb") as f:
            fn, state, seed = pickle.load(f)
        if _INDEX_CACHE and _state_index(state) is None:
            _INDEX_CACHE.clear()
            _release_memory()
        st = dict(state)
        st["__fn__"] = fn
        st["__seed__"] = int(seed)
        _WSTATE = st
        _SPAWN_TOKEN[0] = token
    return _invoke(item)


def _release_memory() -> None:
    """Best effort: collect garbage and hand freed heap pages back to the OS (glibc malloc_trim)."""
    import gc
    gc.collect()
    if _platform().startswith("linux"):
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):  # pragma: no cover - non-glibc
            pass


def _state_index(state: dict) -> "RawIndex | None":
    """The RawIndex a parallel_map state carries (top level or one container level down), else None."""
    for v in state.values():
        if isinstance(v, RawIndex):
            return v
        if isinstance(v, dict):
            v = list(v.values())
        if isinstance(v, (list, tuple)):
            for x in v:
                if isinstance(x, RawIndex):
                    return x
    return None


def _available_ram() -> int | None:
    """Bytes of RAM available to new processes without swapping, or None when unknown: Linux
    /proc/meminfo MemAvailable, Windows GlobalMemoryStatusEx: the smaller of ullAvailPhys and ullAvailPageFile
    (the commit charge left -- Windows refuses any allocation past the commit limit, free RAM or not: with
    After Effects holding 27 GB, 34 GB RAM was free but only 22 GB could be committed, and 28 workers of 1 GB
    each failed with "Insufficient memory"), macOS vm_stat free + inactive + speculative + purgeable pages,
    else POSIX SC_AVPHYS_PAGES."""
    plat = _platform()
    try:
        if plat.startswith("linux"):
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
        elif plat.startswith("win"):
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MS()
            ms.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):  # type: ignore[attr-defined]
                return int(min(ms.ullAvailPhys, ms.ullAvailPageFile))
            return None
        elif plat == "darwin":
            import re
            import subprocess
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=5).stdout
            m = re.search(r"page size of (\d+) bytes", out)
            page = int(m.group(1)) if m else 4096
            pages = 0
            for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
                m = re.search(re.escape(key) + r":\s+(\d+)", out)
                pages += int(m.group(1)) if m else 0
            if pages > 0:
                return pages * page
        if hasattr(os, "sysconf"):
            n = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            return int(n) if n > 0 else None
    except Exception:  # noqa: BLE001 - a failed probe only means "unknown" (no cap)
        return None
    return None


def _pool_private_bytes() -> int:
    """Private memory (Windows: commit charge) of this process's spawn-pool workers -- released when the pool is
    replaced by one of another size; 0 when unknown or not on Windows (fork workers share the parent's pages)."""
    pool = _POOL["pool"]
    if pool is None or _POOL["pid"] != os.getpid() or not _platform().startswith("win"):
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k32.OpenProcess.restype = wintypes.HANDLE
        total = 0
        for p in pool_workers(pool):
            h = k32.OpenProcess(0x1000, False, int(p.pid))          # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                continue
            try:
                c = _PMC()
                c.cb = ctypes.sizeof(_PMC)
                if k32.K32GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                    total += int(c.PagefileUsage)
            finally:
                k32.CloseHandle(h)
        return total
    except Exception:  # noqa: BLE001 - unknown: count nothing
        return 0


def _spawn_mem_cap(workers: int, state: dict) -> int:
    """Spawn pool size for ``state``: at most the workers that fit in the memory available to new processes (the
    available RAM -- on Windows also the commit charge left -- plus what this process's own pool workers hold,
    released when the pool is replaced) less a margin: SPAWN_WORKER_EST_BYTES a worker, or, when the state holds a
    :class:`RawIndex`, SPAWN_TREE_BYTES_PER_DESC x descriptors + SPAWN_WORKER_BASE_BYTES a worker with the
    memmapped descriptors counted once; >= 1. The available RAM already excludes the parent (which holds its own
    tree). Decided once per (index or any state, workers) in this process -- later calls would otherwise count
    the memory the pool's workers already hold -- and logged once."""
    if workers <= 1:
        return workers
    idx = _state_index(state)
    n_desc = int(len(idx.desc)) if idx is not None else 0
    ident = ((str(idx.key) or f"id{id(idx)}") if idx is not None else "any", n_desc, int(workers))
    if ident in _MEM_CAPS:
        return _MEM_CAPS[ident]
    cap = int(workers)
    avail = _available_ram()
    if avail is not None and (idx is None or n_desc > 0):
        avail += _pool_private_bytes()
        tree = idx is not None and not getattr(idx, "flann_free", False)
        if tree:
            per_worker = SPAWN_TREE_BYTES_PER_DESC * n_desc + SPAWN_WORKER_BASE_BYTES
            shared = n_desc * 128 * (1 + 4)              # uint8 + float32 descriptor memmaps (page cache)
        else:                                            # no index, or a GPU-searched one: no tree in the workers
            per_worker, shared = SPAWN_WORKER_EST_BYTES, n_desc * 128
        margin = max(SPAWN_MEM_MARGIN_BYTES, avail // 10)
        cap = int(max(1, min(int(workers), (avail - margin - shared) // per_worker)))
        if cap < workers:
            POOL_STATS["spawn_mem_capped"] += 1
            if tree:
                log.warning("spawn workers: %d instead of %d for the RAW index search - each spawn worker loads "
                            "its own FLANN tree (%.2f GB for %d descriptors + %.2f GB base) and %.1f GB RAM is "
                            "available%s", cap, workers, SPAWN_TREE_BYTES_PER_DESC * n_desc / 1e9, n_desc,
                            SPAWN_WORKER_BASE_BYTES / 1e9, avail / 1e9, " (running single-process)" if cap <= 1 else "")
            else:
                log.warning("spawn workers: %d instead of %d - each worker process needs about %.1f GB and %.1f GB of "
                            "memory is available (other programs hold the rest)%s", cap, workers,
                            per_worker / 1e9, avail / 1e9, " (running single-process)" if cap <= 1 else "")
        else:
            log.debug("spawn workers: %d (RAW index %d descriptors, %.1f GB available)", workers, n_desc, avail / 1e9)
    _MEM_CAPS[ident] = cap
    return cap


def _spawn_pool(n: int):
    p = _POOL
    if p["pool"] is not None and (p["n"] != n or p["pid"] != os.getpid()):
        shutdown_workers()
    if p["pool"] is None:
        import tempfile
        p["dir"] = tempfile.mkdtemp(prefix="match_cuts_pool_")
        p["pool"] = mp.get_context("spawn").Pool(n, initializer=_spawn_init)
        p["n"], p["pid"] = n, os.getpid()
        log.debug("spawn pool: %d workers", n)
    return p["pool"], p["dir"]


def shutdown_workers(kill: bool = False) -> None:
    """Terminate the persistent spawn pool of this process (also run at interpreter exit). Never blocks for
    long (:func:`common.close_pool`): a broken pool's workers are killed (``kill``: at once)."""
    p = _POOL
    pool, d, pid = p["pool"], p["dir"], p["pid"]
    p.update(pool=None, n=0, dir=None, pid=None)
    if pool is not None and pid == os.getpid():
        close_pool(pool, kill=kill)
    if d and pid == os.getpid():
        import shutil
        shutil.rmtree(d, ignore_errors=True)


atexit.register(shutdown_workers)


def _prepare_spawn_state(state: dict) -> None:
    """Objects that can make their pickled form cheaper do it before pickling (RawIndex: trained FLANN
    tree + descriptors written next to its cached npz so workers load / memmap them)."""
    for v in state.values():
        prep = getattr(v, "prepare_spawn", None)
        if callable(prep) and not isinstance(v, type):
            prep()


def _spawn_payload(fn: Callable, state: dict, seed: int) -> bytes | None:
    """Pickled (fn, state, seed) for spawn workers, or None (with a warning) when that is impossible:
    ``fn`` and every callable in ``state`` must be module-level, and ``__main__`` must be import-safe."""
    name = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"
    if not _spawn_safe():
        _warn_once("unsafe_main", "parallel_map: __main__ is not guarded by `if __name__ == \"__main__\":` - "
                   "spawn workers disabled, running single-process")
        return None
    try:
        _prepare_spawn_state(state)
        return pickle.dumps((fn, state, int(seed)), protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:  # PicklingError, AttributeError (local objects), TypeError (handles)
        _warn_once("pickle:" + name, "parallel_map(%s): state is not picklable for spawn workers (%s: %s) - "
                   "running single-process", name, type(e).__name__, e)
        return None


def parallel_map(fn: Callable[[dict, Any], Any], items: Sequence[Any], workers: int, state: dict,
                 seed: int, chunksize: int | None = None, min_items: int = 8, label: str | None = None) -> list[Any]:
    """Apply ``fn(state, item)`` to every item; results in INPUT order (deterministic).

    Start method (:func:`start_method`): 'fork' on Linux, 'spawn' elsewhere, override
    ``MATCH_CUTS_START_METHOD=fork|spawn``. ``common.seed_everything(seed)`` runs before every item and
    every worker runs one OpenCV thread, so results are bit-identical for inline (workers <= 1), fork
    and spawn runs and do not depend on which worker processed which item.

    * fork: big read-only objects in ``state`` (memmapped proxies, the FLANN index) are inherited
      copy-on-write, never pickled; only items and results are pickled. Native thread pools are stopped
      before forking (:func:`_fork_map`); when the census after forking still finds native threads, that
      pool is discarded and this call and every later one use spawn workers.
    * spawn: ``fn`` and ``state`` are pickled once per call into a file the workers of a persistent
      per-process pool load once per call. ``fn`` and callables in ``state`` must be module-level;
      ``model.Proxy`` pickles as its memmapped file (re-opened in the worker), :class:`RawIndex` as its
      cached npz + trained FLANN tree + memmapped descriptors (:meth:`RawIndex.prepare_spawn`),
      :class:`AllowedMasks` with its extra masks packed. Unpicklable state falls back to a
      single-process loop with a warning. Each spawn worker holds its OWN FLANN tree (~800 B per
      descriptor), so with a RawIndex in ``state`` the pool is capped by the available RAM
      (:func:`_spawn_mem_cap`, logged; single-process when only one worker fits) and a later state
      without a RawIndex drops the workers' trees.
    * watchdog (both): results are collected with :func:`common.watched_results`; a pool that delivers no
      result for ``common.POOL_WATCHDOG['stall_s']`` seconds or loses a worker process is stopped with a
      warning and the tasks without a result run in this process (same results). After
      ``POOL_WATCHDOG['max_failures']`` such stops, later calls run in this process. Long calls log progress
      (``label``, default the function name) every ``POOL_WATCHDOG['progress_s']`` seconds.
    """
    global _WSTATE
    items = list(items)
    prev_state = _WSTATE
    _WSTATE = dict(state)
    _WSTATE["__fn__"] = fn
    _WSTATE["__seed__"] = int(seed)
    name = label or _task_name(fn)
    try:
        if workers <= 1 or len(items) < max(2, min_items):
            POOL_STATS["inline"] += 1
            return _inline(items, name)
        if _POOL_FAILURES[0] >= int(POOL_WATCHDOG["max_failures"]) > 0:
            POOL_STATS["inline"] += 1
            POOL_STATS["pools_disabled"] += 1
            return _inline(items, name)
        if start_method() == "spawn" or _FORK_UNSAFE[0]:
            return _spawn_map(fn, items, int(workers), state, seed, chunksize, name)
        res = _fork_map(items, int(workers), chunksize, name)
        if res is None:                          # native threads survived the fork: spawn workers instead
            return _spawn_map(fn, items, int(workers), state, seed, chunksize, name)
        return res
    finally:
        _WSTATE = prev_state


# Watchdog bookkeeping of THIS process: pools stopped by the watchdog, and the reason fork is unsafe here
# (native threads survived a fork) -- then every later call uses spawn workers.
_POOL_FAILURES = [0]
_FORK_UNSAFE: list[str] = [""]


def _chunk(n_items: int, workers: int, chunksize: int | None) -> int:
    n = min(workers, n_items)
    return chunksize or max(1, min(16, n_items // (n * 6) or 1))


def _inline(items: list, name: str, done: dict[int, Any] | None = None) -> list[Any]:
    """Run the items (those without a result in ``done``) in this process, in input order, with progress."""
    done = {} if done is None else done
    todo = [i for i in range(len(items)) if i not in done]
    with Progress(name, len(items)) as prog, single_thread_blas():
        prog.done = len(items) - len(todo)
        for i in todo:
            done[i] = _invoke(items[i])
            prog.step()
    return [done[i] for i in range(len(items))]


def _task_name(fn: Callable) -> str:
    """Short progress label of an item function: '_search_worker' -> 'search', '_w_eval' -> 'eval'."""
    name = str(getattr(fn, "__name__", "tasks")).strip("_")
    for pre in ("w_",):
        name = name[len(pre):] if name.startswith(pre) else name
    for suf in ("_worker",):
        name = name[:-len(suf)] if name.endswith(suf) else name
    return name or "tasks"


def _invoke_chunk(chunk: list[tuple[int, Any]]) -> list[tuple[int, Any]]:
    """Fork-pool entry point: a chunk of (input index, item) -> (input index, result) (results arrive out of
    order; the pool's own chunking cannot be used -- only chunksize-1 iterators take a timeout). Every task runs
    with single-threaded OpenBLAS, as in the parent (``common.single_thread_blas``: same arithmetic on every path
    and machine)."""
    with single_thread_blas():
        return [(i, _invoke(item)) for i, item in chunk]


def _spawn_invoke_chunk(chunk: list[tuple[str, str, int, Any]]) -> list[tuple[int, Any]]:
    """Spawn-pool entry point: a chunk of (token, state file, input index, item)."""
    with single_thread_blas():
        return [(i, _spawn_invoke((token, path, item))) for token, path, i, item in chunk]


def _collect(pool: Any, func: Callable, tasks: list, cs: int, name: str, kind: str
             ) -> tuple[dict[int, Any], str | None]:
    """{input index: result} of the pool's tasks (sent in chunks of ``cs``), and the watchdog's reason when it
    stopped the collection early."""
    procs = pool_workers(pool)
    chunks = [tasks[j:j + cs] for j in range(0, len(tasks), cs)]
    out: dict[int, Any] = {}
    it = pool.imap_unordered(func, chunks)
    try:
        for res in watched_results(it, len(chunks), name, procs, what=f"{kind} pool", count=len,
                                   total_items=len(tasks)):
            for i, r in res:
                out[i] = r
    except PoolFailure as e:
        return out, str(e)
    return out, None


def _finish_after_failure(items: list, out: dict[int, Any], name: str, reason: str) -> list[Any]:
    """Watchdog stop: log it, count it, and compute the tasks without a result in this process."""
    _POOL_FAILURES[0] += 1
    POOL_STATS["watchdog"] += 1
    left = len(items) - len(out)
    log.warning("%s: %s - stopped the worker pool; running the remaining %d of %d tasks in this process "
                "(identical results, only slower)%s", progress_name(name), reason, left, len(items),
                "; later steps of this run will not use worker pools"
                if _POOL_FAILURES[0] >= int(POOL_WATCHDOG["max_failures"]) > 0 else "")
    return _inline(items, name, out)


def _threads_after_fork() -> int:
    """Native threads alive in this process right after forking (0 = none, or unknown on this platform).
    Re-checked briefly, so a Python thread that is just exiting is not counted."""
    n = 0
    for attempt in range(4):
        n = native_threads() or 0
        if n == 0:
            return 0
        time.sleep(0.05)
    return n


def _fork_map(items: list, workers: int, chunksize: int | None, name: str) -> list[Any] | None:
    """The fork-pool path of :func:`parallel_map` (``_WSTATE`` already holds fn + state + seed); None when
    native threads survived the fork (the pool is discarded unused; the caller uses spawn workers)."""
    if _POOL["pool"] is not None:
        shutdown_workers()                   # never fork while a spawn pool's handler threads run
    import cv2
    import gc
    prev_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)                     # stops OpenCV's pool threads (setNumThreads in a child deadlocks)
    release_native_threads()                 # PyAV's swscale slice threads
    # Collect cyclic garbage in the PARENT and freeze everything that exists now, so a forked child
    # never runs a destructor of an inherited object. Verified deadlock otherwise: a stray PyAV
    # decoder (frame-threaded) reached by the child's GC calls avcodec_free_context, which waits on
    # decoder threads that do not exist in the child (futex hang in pthread_cond_destroy).
    t0 = time.perf_counter()
    gc.collect()
    gc.freeze()
    pool = None
    tm: dict[str, float] = {"gc": time.perf_counter() - t0}
    try:
        n = min(int(workers), len(items))
        cs = _chunk(len(items), workers, chunksize)
        t1 = time.perf_counter()
        with _common.FORK_LOCK:
            pool = mp.get_context("fork").Pool(n)
        alive = _threads_after_fork()
        tm["fork"] = time.perf_counter() - t1
        if alive:
            _FORK_UNSAFE[0] = f"{alive} native thread(s) were running when the worker pool forked"
            POOL_STATS["fork_unsafe"] += 1
            log.warning("worker pools: %s (a forked worker could inherit their locks and hang) - using spawn "
                        "workers for the rest of this run (identical results)", _FORK_UNSAFE[0])
            close_pool(pool, kill=True)
            pool = None
            return None
        POOL_STATS["fork"] += 1
        tasks = list(enumerate(items))
        t2 = time.perf_counter()
        out, reason = _collect(pool, _invoke_chunk, tasks, cs, name, "fork")
        tm["run"] = time.perf_counter() - t2
        if reason is None:
            return [out[i] for i in range(len(items))]
        close_pool(pool, kill=True)
        pool = None
        return _finish_after_failure(items, out, name, reason)
    finally:
        t3 = time.perf_counter()
        if pool is not None:
            close_pool(pool)
        gc.unfreeze()
        cv2.setNumThreads(prev_threads)
        tm["close"] = time.perf_counter() - t3
        log.debug("parallel_map(%s): %d tasks on %d fork workers, chunk %d: %s", name, len(items),
                  min(int(workers), len(items)), _chunk(len(items), workers, chunksize),
                  ", ".join(f"{k} {v:.3f} s" for k, v in tm.items()))


def _spawn_map(fn: Callable, items: list, workers: int, state: dict, seed: int,
               chunksize: int | None, name: str = "tasks") -> list[Any]:
    workers = _spawn_mem_cap(workers, state)
    if workers <= 1:                              # the parent already holds the tree: no worker copy
        POOL_STATS["inline"] += 1
        return _inline(items, name)
    payload = _spawn_payload(fn, state, seed)
    if payload is None:
        POOL_STATS["spawn_fallback"] += 1
        POOL_STATS["inline"] += 1
        return _inline(items, name)
    POOL_STATS["spawn"] += 1
    pool, d = _spawn_pool(workers)
    _CALL_SEQ[0] += 1
    token = f"{os.getpid()}-{_CALL_SEQ[0]}-{time.monotonic_ns()}"
    path = os.path.join(d, f"state-{_CALL_SEQ[0]}.pkl")
    with open(path, "wb") as f:
        f.write(payload)
    cs = _chunk(len(items), workers, chunksize)
    try:
        tasks = [(token, path, i, it) for i, it in enumerate(items)]
        out, reason = _collect(pool, _spawn_invoke_chunk, tasks, cs, name, "spawn")
    except BaseException:
        shutdown_workers(kill=True)               # never reuse a pool after a failure / interrupt
        raise
    finally:
        try:
            os.remove(path)
        except OSError:  # pragma: no cover - removed with the pool directory at exit
            pass
    if reason is None:
        return [out[i] for i in range(len(items))]
    shutdown_workers(kill=True)
    return _finish_after_failure(items, out, name, reason)


# ---------------------------------------------------------------------------------------------
# Identities for cache keys
# ---------------------------------------------------------------------------------------------

def _hash_array(a: np.ndarray | None) -> str:
    if a is None:
        return "none"
    h = hashlib.blake2b(digest_size=12)
    arr = np.asarray(a)
    h.update(str(arr.dtype).encode())
    h.update(str(arr.shape).encode())
    if arr.ndim >= 1 and arr.size > (1 << 22):
        for i in range(arr.shape[0]):           # stream big (memmapped) arrays row by row
            h.update(np.ascontiguousarray(arr[i]).tobytes())
    else:
        h.update(np.ascontiguousarray(arr).tobytes())
    return h.hexdigest()


def proxy_id(p: Proxy) -> str:
    """Stable identity of a proxy for cache keys: media file hash (or pixel hash for in-memory
    proxies) + geometry + fps + sparse index map."""
    parts: list[Any] = [p.role, int(p.n), list(p.size), list(p.full_size), [float(r) for r in p.ratio],
                        fps_str(p.fps)]
    path = Path(p.path) if p.path else None
    if path is not None and path.is_file():
        parts.append(file_hash(path))
    else:
        parts.append(_hash_array(p.frames))
    parts.append(_hash_array(p.index_map) if p.index_map is not None else "dense")
    return params_hash(*parts)


def layout_id(layout: Layout | None) -> str:
    if layout is None:
        return "none"
    d = layout.to_dict()
    extra = []
    for f in (layout.static_mask_file, layout.overlay_mask_file):
        extra.append(file_hash(f) if f and Path(f).is_file() else "")
    d.pop("static_mask_file", None)
    d.pop("overlay_mask_file", None)
    return params_hash(d, extra)


def overlays_id(overlays: Any) -> str:
    if overlays is None:
        return "none"
    h = hashlib.blake2b(digest_size=12)
    try:
        frames = sorted(int(k) for k in overlays.frames())
    except Exception:  # pragma: no cover - unknown overlay container
        return params_hash(repr(type(overlays)))
    for k in frames:
        m = overlays.get(k)
        if m is None:
            continue
        h.update(str(k).encode())
        h.update(np.packbits(np.asarray(m, bool)).tobytes())
    return h.hexdigest()


def hints_id(hints: AudioHints | None) -> str:
    if hints is None:
        return "none"
    return params_hash([_hash_array(np.asarray(getattr(hints, f))) for f in
                        ("comp_t", "raw_t", "speed", "conf", "psr", "peak")], hints.window, hints.hop)


# ---------------------------------------------------------------------------------------------
# Masks and ROI
# ---------------------------------------------------------------------------------------------

def box_roi(layout: Layout | None, comp: Proxy) -> tuple[int, int, int, int]:
    """Integer (x, y, w, h) ROI of the video box at competitor PROXY resolution (whole frame if no box)."""
    w, h = comp.size
    if layout is None or layout.box is None:
        return (0, 0, int(w), int(h))
    b = layout.box.scaled(float(comp.ratio[0]), float(comp.ratio[1]))
    x0, y0 = max(0, int(math.floor(b.x))), max(0, int(math.floor(b.y)))
    x1, y1 = min(int(w), int(math.ceil(b.x + b.w))), min(int(h), int(math.ceil(b.y + b.h)))
    if x1 <= x0 or y1 <= y0:
        return (0, 0, int(w), int(h))
    return (x0, y0, x1 - x0, y1 - y0)


def _box_coverage_local(layout: Layout | None, comp: Proxy, ss: int = 4) -> np.ndarray:
    """Rounded-box coverage [h, w] at proxy res (CORNER convention, ss x ss super-sampling)."""
    w, h = comp.size
    if layout is None or layout.box is None:
        return np.ones((int(h), int(w)), np.float32)
    rx, ry = float(comp.ratio[0]), float(comp.ratio[1])
    b = layout.box
    x0, y0, x1, y1 = b.x * rx, b.y * ry, (b.x + b.w) * rx, (b.y + b.h) * ry
    r = max(0.0, min(b.corner_radius * (rx + ry) / 2.0, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    offs = (np.arange(ss) + 0.5) / ss
    xs = (np.arange(int(w))[:, None] + offs[None, :]).reshape(-1)
    ys = (np.arange(int(h))[:, None] + offs[None, :]).reshape(-1)
    inx = (xs >= x0) & (xs <= x1)
    iny = (ys >= y0) & (ys <= y1)
    cx = np.clip(xs, x0 + r, x1 - r)
    cy = np.clip(ys, y0 + r, y1 - r)
    dx = (xs - cx)[None, :]
    dy = (ys - cy)[:, None]
    inside = iny[:, None] & inx[None, :]
    if r > 0:
        inside &= (dx * dx + dy * dy) <= r * r
    return inside.reshape(int(h), ss, int(w), ss).mean(axis=(1, 3)).astype(np.float32)


class AllowedMasks:
    """Per-frame bool masks [h, w] at comp proxy res of pixels usable for keypoints and scores:
    box coverage >= 0.99 AND NOT static AND NOT dilated overlay(k) AND NOT extra(k).

    Uses ``layout.allowed_mask`` when the layout module is importable (``use_layout_module=True``),
    else the identical local definition (DESIGN §5 layout.allowed_mask). ``extra`` holds masks added by
    the overlay pass 2 of refine (True = excluded). Masks are recomputed on every call (no memo): the
    overlay container may be updated in place (refine's pass 2 ``union``), and a stale memo would make
    results depend on which process computed a frame first (inline vs forked workers).
    """

    def __init__(self, layout: Layout | None, overlays: Any, comp: Proxy, cfg, use_layout_module: bool = True,
                 base_fn: Callable[[int], np.ndarray] | None = None):
        import cv2
        self.layout, self.overlays, self.comp, self.cfg = layout, overlays, comp, cfg
        self.extra: dict[int, np.ndarray] = {}
        self._base_fn = base_fn
        self._layout_fn = None
        if base_fn is None and use_layout_module and layout is not None:
            try:
                from .layout import allowed_mask as _am  # lazy: written by another agent
                self._layout_fn = _am
            except ImportError:
                self._layout_fn = None
        cov = _box_coverage_local(layout, comp)
        base = cov >= 0.99
        if layout is not None and layout.static_mask_file and Path(layout.static_mask_file).is_file():
            st = np.load(layout.static_mask_file).astype(bool)
            if st.shape == base.shape:
                base &= ~st
            else:
                log.warning("static mask %s has shape %s != proxy %s - ignored", layout.static_mask_file,
                            st.shape, base.shape)
        self.base = base
        d = int(getattr(cfg, "overlay_dilate_px", 3))
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1)) if d > 0 else None

    def _compute(self, k: int, extra: bool = True) -> np.ndarray:
        import cv2
        if self._base_fn is not None:
            m = np.asarray(self._base_fn(k), bool).copy()
        elif self._layout_fn is not None:
            m = np.asarray(self._layout_fn(self.layout, self.overlays, k, self.comp), bool).copy()
        else:
            m = self.base.copy()
            ov = self.overlays.get(k) if self.overlays is not None else None
            if ov is not None and np.any(ov):
                ov = np.asarray(ov, np.uint8)
                if self._kernel is not None:
                    ov = cv2.dilate(ov, self._kernel)
                m &= ov == 0
        ex = self._extra_mask(int(k)) if extra else None
        if ex is not None:
            m &= ~ex
        return m

    def __call__(self, k: int) -> np.ndarray:
        return self._compute(int(k))

    def layout_only(self, k: int) -> np.ndarray:
        """The mask without refine's pass-2 residual masks (``extra``): residuals of the match being judged mask
        its own mismatch away, so tests that must not depend on that match (the RAW identity test) use this."""
        return self._compute(int(k), extra=False)

    def _extra_mask(self, k: int) -> np.ndarray | None:
        ex = self.extra.get(k)
        if ex is None:
            packed = self.__dict__.get("_extra_packed")
            if packed and k in packed:
                shape, bits = packed.pop(k)
                ex = np.unpackbits(bits, count=int(shape[0]) * int(shape[1])).reshape(shape).astype(bool)
                self.extra[k] = ex
        return ex

    def add_extra(self, k: int, mask: np.ndarray) -> None:
        """Exclude more pixels at frame k (overlay pass 2)."""
        k = int(k)
        mask = np.asarray(mask, bool)
        cur = self._extra_mask(k)
        self.extra[k] = mask if cur is None else (cur | mask)

    # -- pickling (spawn workers): extra masks travel bit-packed and are unpacked on first use --------
    def __getstate__(self) -> dict:
        d = dict(self.__dict__)
        packed = dict(d.pop("_extra_packed", None) or {})
        for k, m in self.extra.items():
            m = np.asarray(m, bool)
            packed[int(k)] = (tuple(int(s) for s in m.shape), np.packbits(m.ravel()))
        d["extra"] = {}
        d["_extra_packed"] = packed
        return d

    def __setstate__(self, d: dict) -> None:
        self.__dict__.update(d)


def mask_bbox(mask: np.ndarray | None, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """Bounding box (x, y, w, h) of True pixels (whole image if None/empty)."""
    h, w = shape[:2]
    if mask is None:
        return (0, 0, int(w), int(h))
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return (0, 0, int(w), int(h))
    return (int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1))


# ---------------------------------------------------------------------------------------------
# SIFT
# ---------------------------------------------------------------------------------------------

_SIFT: dict[int, Any] = {}


def _sift(nfeatures: int):
    """cv2.SIFT with ``enable_precise_upscale=True``: the default 2x upscale shifts every keypoint by
    0.25 px (measured: SIFT of the mirrored image is then offset by 0.5 px); the precise variant is
    unbiased, exactly mirror-symmetric (see mirror_features) and equally fast."""
    import cv2
    s = _SIFT.get(int(nfeatures))
    if s is None:
        try:
            s = cv2.SIFT_create(int(nfeatures), enable_precise_upscale=True)
        except TypeError:  # pragma: no cover - OpenCV < 4.8
            s = cv2.SIFT_create(int(nfeatures))
        _SIFT[int(nfeatures)] = s
    return s


def detect_sift(img: np.ndarray, mask: np.ndarray | None, nfeatures: int,
                roi: tuple[int, int, int, int] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """SIFT on ``img`` (uint8 gray), restricted to ``mask`` (bool/uint8, full image) and, for speed, to
    the crop ``roi`` (x, y, w, h). Returns (pts float32 [n, 2] in OpenCV pixel-centre coordinates of the
    FULL image, desc uint8 [n, 128])."""
    x0, y0 = 0, 0
    sub, msub = img, mask
    if roi is not None:
        x0, y0, w, h = roi
        sub = img[y0:y0 + h, x0:x0 + w]
        msub = mask[y0:y0 + h, x0:x0 + w] if mask is not None else None
    m8 = None if msub is None else (np.asarray(msub, bool).astype(np.uint8) * 255)
    kps, desc = _sift(nfeatures).detectAndCompute(np.ascontiguousarray(sub), m8)
    if desc is None or len(kps) == 0:
        return np.zeros((0, 2), np.float32), np.zeros((0, 128), np.uint8)
    pts = np.array([kp.pt for kp in kps], np.float32) + np.array([x0, y0], np.float32)
    return pts, np.clip(np.rint(desc), 0, 255).astype(np.uint8)


# SIFT descriptors of a horizontally mirrored image are an exact permutation of the originals
# (OpenCV layout 4 x 4 cells x 8 orientation bins): the cell rows reverse and orientation bin o -> -o
# (verified: median relative difference 0.0 against SIFT run on cv2.flip(img, 1), keypoints mirrored).
_MIRROR_IDX = np.arange(128).reshape(4, 4, 8)[::-1][:, :, (-np.arange(8)) % 8].reshape(-1)


def mirror_features(pts: np.ndarray, desc: np.ndarray, width: int) -> tuple[np.ndarray, np.ndarray]:
    """SIFT features of ``cv2.flip(img, 1)`` from the features of ``img`` (image ``width`` px): keypoint
    x -> width - 1 - x (OpenCV pixel-centre coordinates), descriptors permuted (``_MIRROR_IDX``)."""
    p = np.array(pts, np.float32, copy=True)
    if len(p):
        p[:, 0] = (width - 1) - p[:, 0]
    return p, np.ascontiguousarray(desc[:, _MIRROR_IDX])


# ---------------------------------------------------------------------------------------------
# RAW index
# ---------------------------------------------------------------------------------------------

def _index_worker(state: dict, frames: list[int]) -> list[tuple[int, np.ndarray, np.ndarray]]:
    raw: Proxy = state["raw"]
    out = []
    for j in frames:
        pts, desc = detect_sift(np.asarray(raw.get(j)), None, state["nfeat"])
        out.append((int(j), pts, desc))
    return out


class RawIndex:
    """SIFT descriptors of sampled RAW proxy frames with cluster-aware voting (DESIGN §5)."""

    def __init__(self, frames: np.ndarray, desc: np.ndarray, owner: np.ndarray, pts: np.ndarray,
                 offsets: np.ndarray, raw_fps, step: int, cfg, key: str = "", npz_path: str = ""):
        self.frames = np.asarray(frames, np.int32)          # sampled RAW frame indices (sorted)
        self.desc = np.asarray(desc, np.uint8)              # [N, 128] uint8 (lossless SIFT values)
        self.owner = np.asarray(owner, np.int32)            # [N] RAW frame of each descriptor
        self.pts = np.asarray(pts, np.float32)              # [N, 2] OpenCV coords at RAW proxy res
        self.offsets = np.asarray(offsets, np.int64)        # [F + 1] descriptor ranges per sampled frame
        self.fps = raw_fps
        self.step = int(step)
        self.key = key
        self.npz_path = str(npz_path or "")                 # cache file (stage 'raw_index') when cached
        self.knn = int(getattr(cfg, "index_knn", 24))
        self.ratio = float(getattr(cfg, "index_ratio", 0.8))
        self.far = float(getattr(cfg, "index_far_s", 2.0)) * float(raw_fps)
        self.seed = int(getattr(cfg, "seed", 12345))
        self.flann_free = False                             # the GPU searches it (exact, gpu.KnnIndex in this process):
                                                            #   no FLANN tree anywhere, the workers get the neighbours
        self._flann = None
        self._gpu = None                                    # gpu.KnnIndex (this process only, never pickled)
        self._data32: np.ndarray | None = None
        self._spawn_files: dict[str, str] | None = None     # set by prepare_spawn (cached index only)

    # -- pickling for spawn workers (DESIGN D7) ---------------------------------------------------
    _SCALARS = ("fps", "step", "key", "npz_path", "knn", "ratio", "far", "seed", "flann_free")

    def prepare_spawn(self) -> None:
        """Called by :func:`parallel_map` before pickling for spawn workers. Trains FLANN here (once) and,
        for a cached index, writes next to its npz the uint8 and float32 descriptors (``.desc.npy`` /
        ``.desc32.npy``, kept: content-addressed by the index key, re-checked against a strided sample)
        and this process's trained tree (``.flann``), so every worker memmaps the descriptors (one copy in
        the page cache instead of one per worker) and loads the identical tree instead of re-training it
        (without a usable ``.flann`` the worker re-trains with the index seed: the same tree, slower).
        A GPU-searched index (``flann_free``) writes only the uint8 descriptors: no tree is trained or loaded."""
        import cv2
        if not self.flann_free:
            self.ensure_built()
        if self._spawn_files is not None or not self.npz_path or not Path(self.npz_path).is_file():
            return
        base = Path(self.npz_path)
        stem = base.name[:-len(".npz")] if base.name.endswith(".npz") else base.name
        paths = {"desc": base.with_name(stem + ".desc.npy"), "desc32": base.with_name(stem + ".desc32.npy"),
                 "flann": base.with_name(stem + ".flann")}
        step = max(1, len(self.desc) // 1024)
        sides = (("desc", self.desc),) if self.flann_free else (("desc", self.desc), ("desc32", self._data32))
        try:
            for name, arr in sides:
                p = paths[name]
                ok = False
                if p.is_file():
                    try:
                        mm = np.load(p, mmap_mode="r")
                        ok = (mm.shape == arr.shape and mm.dtype == arr.dtype
                              and np.array_equal(mm[::step], arr[::step]) and np.array_equal(mm[-1:], arr[-1:]))
                        del mm
                    except (OSError, ValueError):
                        ok = False
                if not ok:
                    tmp = p.with_name(p.name + f".{os.getpid()}.tmp.npy")
                    np.save(tmp, np.ascontiguousarray(arr))
                    os.replace(tmp, p)
        except OSError as e:
            log.warning("RAW index: spawn side files not written (%s) - workers receive the descriptors", e)
            return
        if self.flann_free:
            self._spawn_files = {"desc": str(paths["desc"])}
            return
        files = {"desc": str(paths["desc"]), "desc32": str(paths["desc32"])}
        # the tree is re-saved once per process: workers must load exactly the tree trained here
        tmp = paths["flann"].with_name(paths["flann"].name + f".{os.getpid()}.tmp")
        try:
            self._flann.save(str(tmp))
            if tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, paths["flann"])
                files["flann"] = str(paths["flann"])
        except (OSError, cv2.error) as e:           # e.g. a non-ASCII path on Windows (OpenCV file API)
            log.info("RAW index: FLANN tree not saved (%s) - spawn workers re-train it (same seed)", e)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        self._spawn_files = files

    def __reduce__(self):
        st: dict[str, Any] = {k: getattr(self, k) for k in self._SCALARS}
        if self._spawn_files is not None:
            st["files"] = dict(self._spawn_files)
        else:
            st.update(frames=self.frames, desc=self.desc, owner=self.owner, pts=self.pts, offsets=self.offsets)
        return (_restore_index, (st,))

    # -- construction ---------------------------------------------------------------------------
    @staticmethod
    def build(raw: Proxy, cfg, cache: Cache | None) -> "RawIndex":
        """SIFT every RAW proxy frame (cfg.raw_index_every_frame, with the GPU search) or every round(raw_fps /
        index_fps) frames (index_fps = raw_index_fps_short for RAW <= 10 min, else raw_index_fps_long); nfeatures per
        frame lowered so the total stays below cfg.index_max_descriptors. Cached (stage 'raw_index', npz) by proxy
        identity + parameters. With cfg.gpu (and a usable GPU) it is searched exactly on the GPU (:meth:`neighbours`)."""
        from . import gpu
        fps = float(raw.fps)
        duration = raw.n / fps if fps > 0 else 0.0
        index_fps = cfg.raw_index_fps_short if duration <= 600.0 else cfg.raw_index_fps_long
        on_gpu = bool(getattr(cfg, "gpu", False)) and gpu.available() is None
        every = bool(getattr(cfg, "raw_index_every_frame", False)) and on_gpu
        step = 1 if every else max(1, int(round(fps / float(index_fps))))
        frames = [j for j in range(0, raw.n, step) if raw.has(j)]
        if not frames:
            raise ValueError("RawIndex.build: the RAW proxy holds no index frames")
        nfeat = int(min(cfg.sift_nfeatures, max(32, cfg.index_max_descriptors // max(1, len(frames)))))
        key = stage_key("raw_index", proxy_id(raw), cfg.analysis_params(), step, nfeat, "sift_precise_upscale")

        def compute() -> dict[str, np.ndarray]:
            workers = cfg.resolved_workers()
            chunks = [frames[i:i + 8] for i in range(0, len(frames), 8)]
            res = parallel_map(_index_worker, chunks, workers, {"raw": raw, "nfeat": nfeat}, cfg.seed,
                               chunksize=1, min_items=2)
            flat = [r for chunk in res for r in chunk]
            descs = [d for _, _, d in flat]
            ptss = [p for _, p, _ in flat]
            counts = np.array([len(d) for d in descs], np.int64)
            owner = np.repeat(np.array([j for j, _, _ in flat], np.int32), counts)
            offsets = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)
            return {
                "frames": np.array([j for j, _, _ in flat], np.int32),
                "desc": np.concatenate(descs).astype(np.uint8) if descs else np.zeros((0, 128), np.uint8),
                "pts": np.concatenate(ptss).astype(np.float32) if ptss else np.zeros((0, 2), np.float32),
                "owner": owner, "offsets": offsets,
                "fps": np.array([raw.fps.numerator, raw.fps.denominator], np.int64),
                "step": np.array(step), "nfeat": np.array(nfeat),
            }

        data = cache.npz("raw_index", key, compute) if cache is not None else compute()
        npz_path = str(cache.path("raw_index", key, ".npz")) if cache is not None else ""
        idx = RawIndex(data["frames"], data["desc"], data["owner"], data["pts"], data["offsets"], raw.fps,
                       int(data["step"]), cfg, key, npz_path=npz_path)
        idx.flann_free = on_gpu
        log.info("RAW index: %d frames (step %d), %d descriptors (%.1f MB uint8), searched %s", len(idx.frames),
                 idx.step, len(idx.desc), idx.desc.nbytes / 1e6,
                 f"exactly on the GPU ({gpu.device_name()})" if on_gpu else "with FLANN kd-trees (approximate, CPU)")
        return idx

    # -- GPU search (exact) -------------------------------------------------------------------------
    def neighbours(self, queries: Sequence[np.ndarray]) -> list[tuple[np.ndarray, np.ndarray]]:
        """The exact index_knn nearest neighbours of each query set on the GPU: [(indices [m, k], squared distances
        [m, k])] (gpu.KnnIndex, uploaded once per process)."""
        from . import gpu
        if self._gpu is None:
            self._gpu = gpu.KnnIndex(np.asarray(self.desc))
        return self._gpu.search(queries, int(min(self.knn, len(self.desc))))

    def close_gpu(self) -> None:
        """Free the GPU copy (after the searches of S5.2 / S5.3)."""
        if self._gpu is not None:
            self._gpu.close()
            self._gpu = None

    # -- FLANN ----------------------------------------------------------------------------------
    def ensure_built(self) -> None:
        """Train the FLANN kd-tree (seeded, deterministic: the same tree in every process). Call before
        forking workers."""
        if self._flann is not None:
            return
        import cv2
        if self._data32 is None or len(self._data32) != len(self.desc):
            self._data32 = np.ascontiguousarray(self.desc, dtype=np.float32)   # kept alive: FLANN references it
        seed_everything(self.seed)
        self._flann = cv2.flann_Index(self._data32, dict(algorithm=1, trees=4))

    def frame_features(self, j: int) -> tuple[np.ndarray, np.ndarray] | None:
        """(pts, desc) stored for index frame j, or None if j is not an index frame."""
        i = int(np.searchsorted(self.frames, j))
        if i >= len(self.frames) or int(self.frames[i]) != int(j):
            return None
        a, b = int(self.offsets[i]), int(self.offsets[i + 1])
        return self.pts[a:b], self.desc[a:b]

    # -- voting ---------------------------------------------------------------------------------
    def votes(self, desc: np.ndarray, window: tuple[int, int] | None = None,
              nn: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
        """Cluster-aware vote vector over index frames (smoothed over +-1 index frame).

        For each query descriptor: k = index_knn nearest neighbours; f0 = frame of the first one; the
        ratio denominator is the first neighbour whose frame is > index_far_s away from f0 (NOT the
        second neighbour, which is usually a near-duplicate from an adjacent index frame). If
        d1 < index_ratio * d_far (or no far neighbour exists), every neighbour within index_far_s of f0
        with distance <= 1.1 d1 votes with weight 1 / cluster size. ``nn``: the neighbours of ``desc``
        already found (:meth:`neighbours`, exact on the GPU); else FLANN finds them (approximate)."""
        F = len(self.frames)
        v = np.zeros(F, np.float64)
        if desc is None or len(desc) == 0 or len(self.desc) == 0:
            return v
        q = np.ascontiguousarray(desc, dtype=np.float32)
        if nn is None and self.flann_free:
            nn = self.neighbours([q])[0]                    # a direct call: the GPU search here, one query set
        if nn is not None:
            ind, d2 = nn
            k = int(np.asarray(ind).shape[1]) if np.asarray(ind).ndim == 2 else int(min(self.knn, len(self.desc)))
        else:
            self.ensure_built()
            k = int(min(self.knn, len(self.desc)))
            ind, d2 = self._flann.knnSearch(q, k, params=dict(checks=64))
        ind = np.asarray(ind, np.int64).reshape(len(q), k)
        dist = np.sqrt(np.maximum(np.asarray(d2, np.float64).reshape(len(q), k), 0.0))
        valid = ind >= 0
        own = self.owner[np.clip(ind, 0, None)].astype(np.int64)
        f0 = own[:, :1]
        near = np.abs(own - f0) <= self.far
        farm = (~near) & valid
        has_far = farm.any(axis=1)
        first_far = np.argmax(farm, axis=1)
        d_far = dist[np.arange(len(q)), first_far]
        passed = valid[:, 0] & (~has_far | (dist[:, 0] < self.ratio * d_far))
        cluster = near & valid & (dist <= 1.1 * dist[:, :1] + 1e-6) & passed[:, None]
        sizes = cluster.sum(axis=1)
        w = np.where(sizes > 0, 1.0 / np.maximum(sizes, 1), 0.0)
        pos = np.searchsorted(self.frames, own)          # owner frames are index frames
        np.add.at(v, pos[cluster], np.repeat(w, sizes))
        outside = None
        if window is not None:
            j0, j1 = window
            outside = (self.frames < j0) | (self.frames >= j1)
            v[outside] = 0.0
        sm = v.copy()
        sm[1:] += v[:-1]
        sm[:-1] += v[1:]
        if outside is not None:
            sm[outside] = 0.0
        return sm

    def query(self, desc: np.ndarray, top: int, window: tuple[int, int] | None = None,
              nn: tuple[np.ndarray, np.ndarray] | None = None) -> list[tuple[int, float]]:
        """Candidate RAW frames for one set of query descriptors: [(raw frame, votes)], best first.

        Peaks (local maxima) of the smoothed cluster-aware vote vector, restricted to RAW frames in
        ``window`` = [j0, j1) when given. Deterministic ordering (votes desc, frame asc). ``nn``: see :meth:`votes`."""
        sm = self.votes(desc, window, nn)
        if not np.any(sm > 0):
            return []
        left = np.concatenate([[-np.inf], sm[:-1]])
        right = np.concatenate([sm[1:], [-np.inf]])
        peaks = np.flatnonzero((sm > 0) & (sm >= left) & (sm > right))
        order = sorted(peaks.tolist(), key=lambda i: (-sm[i], int(self.frames[i])))
        return [(int(self.frames[i]), float(sm[i])) for i in order[:max(1, int(top))]]


_INDEX_CACHE: OrderedDict[tuple, RawIndex] = OrderedDict()


def _restore_index(st: dict) -> RawIndex:
    """Unpickle a :class:`RawIndex` in a spawn worker (see :meth:`RawIndex.prepare_spawn`). The FLANN tree
    is loaded (or re-trained with the index seed) HERE, before the worker seeds the item, so no item
    sees an RNG re-seed mid-way. Content-addressed indexes (non-empty key) are kept per process, so the
    persistent pool loads each tree once per worker, not once per call."""
    import cv2
    files = st.get("files")
    free = bool(st.get("flann_free"))       # GPU-searched: the neighbours come with the tasks, no tree here
    ident = (str(st["key"]), (files.get("flann") or files.get("desc32") or files["desc"]) if files else "", free)
    idx = _INDEX_CACHE.get(ident) if st["key"] else None
    if idx is None:
        idx = RawIndex.__new__(RawIndex)
        idx._flann, idx._data32, idx._spawn_files, idx._gpu = None, None, None, None
        if files:
            with np.load(st["npz_path"], allow_pickle=False) as z:
                idx.frames = np.asarray(z["frames"], np.int32)
                idx.owner = np.asarray(z["owner"], np.int32)
                idx.pts = np.asarray(z["pts"], np.float32)
                idx.offsets = np.asarray(z["offsets"], np.int64)
            idx.desc = np.load(files["desc"], mmap_mode="r")
            if not free:
                idx._data32 = np.load(files["desc32"], mmap_mode="r")
                if files.get("flann") and len(idx._data32):
                    try:
                        fl = cv2.flann_Index()
                        if fl.load(idx._data32, files["flann"]):
                            idx._flann = fl
                    except cv2.error:                  # unreadable tree file: re-train (same seed, same tree)
                        idx._flann = None
        else:
            idx.frames, idx.desc, idx.owner = st["frames"], st["desc"], st["owner"]
            idx.pts, idx.offsets = st["pts"], st["offsets"]
        for k in RawIndex._SCALARS:
            setattr(idx, k, st.get(k, False) if k == "flann_free" else st[k])
        if len(idx.desc) and not free:
            idx.ensure_built()                  # no-op when the tree was loaded
        if st["key"]:
            _INDEX_CACHE[ident] = idx
            while len(_INDEX_CACHE) > 2:
                _INDEX_CACHE.popitem(last=False)
    else:
        for k in RawIndex._SCALARS:            # query parameters travel with every call
            setattr(idx, k, st.get(k, False) if k == "flann_free" else st[k])
    return idx


# ---------------------------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------------------------

@dataclass
class Anchor:
    """A verified match of one competitor frame (DESIGN §5)."""
    k: int
    raw: int
    flip: bool
    sim: Sim
    inliers: int
    inlier_ratio: float
    votes: float
    zncc: float
    source: str = "global"        # 'global' | 'audio' | 'rescue' | 'line'; '<source>_near' = a RANSAC near-miss
                                  # (join-only); '<source>_gray' = a full RANSAC match whose ZNCC lies in the gray zone
                                  # [none_thresh, accept) -- evidence for an UNRESOLVED frame, never an anchor (FX-08)
    time_ambiguous: bool = False  # the best two (RAW frame, Sim) hypotheses of jb-1..jb+1 within anchor_time_delta

    def to_dict(self) -> dict:
        return {"k": int(self.k), "raw": int(self.raw), "flip": bool(self.flip), "sim": self.sim.to_dict(),
                "inliers": int(self.inliers), "inlier_ratio": float(self.inlier_ratio),
                "votes": float(self.votes), "zncc": float(self.zncc), "source": self.source,
                "time_ambiguous": bool(self.time_ambiguous)}

    @staticmethod
    def from_dict(d: dict) -> "Anchor":
        return Anchor(int(d["k"]), int(d["raw"]), bool(d["flip"]), Sim.from_dict(d["sim"]), int(d["inliers"]),
                      float(d["inlier_ratio"]), float(d["votes"]), float(d["zncc"]), str(d.get("source", "global")),
                      bool(d.get("time_ambiguous", False)))


_FLIP_FEAT: OrderedDict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = OrderedDict()


def _raw_features(raw: Proxy, index: RawIndex | None, j: int, flip: bool, nfeat: int) -> tuple[np.ndarray, np.ndarray]:
    """SIFT of RAW proxy frame j or of cv2.flip(raw_j, 1): from the index storage when j is an index frame
    (the mirror via :func:`mirror_features`), else computed (per-process LRU)."""
    import cv2
    if index is not None:
        f = index.frame_features(j)
        if f is not None:
            return mirror_features(f[0], f[1], raw.size[0]) if flip else f
    key = (int(j), int(flip))
    hit = _FLIP_FEAT.get(key)
    if hit is not None:
        _FLIP_FEAT.move_to_end(key)
        return hit
    img = np.asarray(raw.get(j))
    if flip:
        img = cv2.flip(img, 1)
    feat = detect_sift(img, None, nfeat)
    _FLIP_FEAT[key] = feat
    if len(_FLIP_FEAT) > 64:
        _FLIP_FEAT.popitem(last=False)
    return feat


def _ransac(comp_pts: np.ndarray, comp_desc: np.ndarray, raw_pts: np.ndarray, raw_desc: np.ndarray,
            cfg) -> tuple[np.ndarray | None, int, int]:
    """Pairwise Lowe ratio (cfg.lowe_ratio) against ONE RAW frame + estimateAffinePartial2D RAW -> comp.
    Returns (M 2x3 or None, inliers, good matches)."""
    import cv2
    if len(comp_desc) < 3 or len(raw_desc) < 3:
        return None, 0, 0
    bf = cv2.BFMatcher(cv2.NORM_L2)
    mm = bf.knnMatch(comp_desc.astype(np.float32), raw_desc.astype(np.float32), k=2)
    good = [p[0] for p in mm if len(p) == 2 and p[0].distance < cfg.lowe_ratio * p[1].distance]
    if len(good) < 3:
        return None, 0, len(good)
    src = np.float32([raw_pts[g.trainIdx] for g in good])       # RAW (or flipped RAW) proxy, cv coords
    dst = np.float32([comp_pts[g.queryIdx] for g in good])      # comp proxy, cv coords
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=float(cfg.ransac_reproj_px),
                                         maxIters=2000, confidence=0.995, refineIters=10)
    if M is None or inl is None:
        return None, 0, len(good)
    return M, int(inl.sum()), len(good)


class _Scorer:
    """Scores RAW candidates for one competitor frame under a Sim (thin wrapper over scoring.py)."""

    def __init__(self, comp_img: np.ndarray, roi: tuple[int, int, int, int], allowed: np.ndarray | None,
                 raw: Proxy, comp_ratio: tuple[float, float], cfg):
        self.region = scoring.prepare_comp(comp_img, roi, allowed, cfg.score_blur, with_grad=cfg.grad_weight > 0)
        self.raw, self.cr, self.cfg = raw, tuple(comp_ratio), cfg
        self.W = float(raw.full_size[0])
        self.rr = tuple(raw.ratio)

    def scores(self, js: Sequence[int], sim: Sim, flip: bool) -> np.ndarray:
        js = [int(j) for j in js]
        ok = [j for j in js if self.raw.has(j)]
        out = np.full(len(js), np.nan)
        if not ok:
            return out
        s = scoring.score_candidates(self.region, [np.asarray(self.raw.get(j)) for j in ok], sim, flip, self.W,
                                     self.rr, self.cr, blur=self.cfg.score_blur, grad_weight=self.cfg.grad_weight)
        pos = {j: i for i, j in enumerate(ok)}
        for i, j in enumerate(js):
            if j in pos:
                out[i] = s[pos[j]]
        return out


def _nanargmax(a: np.ndarray) -> int:
    if a.size == 0 or not np.any(np.isfinite(a)):
        return -1
    return int(np.nanargmax(np.where(np.isfinite(a), a, -np.inf)))


def _reestimate(img: np.ndarray, raw: Proxy, jb: int, sim: Sim, flip: bool, allowed: np.ndarray | None,
                roi: tuple[int, int, int, int], comp: Proxy, cfg) -> tuple[int, Sim, float, bool]:
    """(RAW frame, Sim, masked ZNCC, time_ambiguous) of an anchor (DESIGN §5 visual_match, FX-03 step 1).

    For RAW jb-1..jb+1 (jb = the argmax under the RANSAC Sim) the framing is measured coarse-to-fine
    (refine.ecc_measure) from the RANSAC Sim and from its derotated version, so a wrong neighbour frame cannot
    win just because ECC started from a rotation it fitted to compensate the time error. A rotation is kept
    only when the best free result beats the best theta = 0 refit (scale + translation only, every frame
    again) by more than 3 * soft_delta_max; otherwise the theta = 0 hypothesis decides the frame. The anchor
    is time-ambiguous when the runner-up frame of the deciding hypothesis class is within anchor_time_delta
    (evidence for refine's time-line grouping, never a rejection)."""
    from .refine import ecc_measure   # lazy: refine imports this module
    W, rr, cr = float(raw.full_size[0]), tuple(raw.ratio), tuple(comp.ratio)
    centre = np.array([(roi[0] + roi[2] / 2.0) / cr[0], (roi[1] + roi[3] / 2.0) / cr[1]])
    derot = None
    if abs(sim.theta_deg) > 0.05:
        p = sim.inverse().apply(centre)[0]
        derot = Sim(sim.s, 0.0, float(centre[0] - sim.s * p[0]), float(centre[1] - sim.s * p[1]))
    js = [j for j in (jb - 1, jb, jb + 1) if raw.has(j)]
    free = {j: ecc_measure(img, np.asarray(raw.get(j)), sim, flip, W, rr, cr, allowed, cfg, roi=roi,
                           starts=[derot] if derot is not None else ()) for j in js}

    def ranked(res: dict) -> list[int]:
        return sorted(res, key=lambda j: (-(res[j].z if np.isfinite(res[j].z) else -np.inf), abs(j - jb), j))
    order = ranked(free)
    pool = free
    rot_min = float(getattr(cfg, "rotation_min_deg", 0.2))
    if abs(free[order[0]].sim.theta_deg) > 0.5 * rot_min:
        locked = {j: ecc_measure(img, np.asarray(raw.get(j)), free[j].sim, flip, W, rr, cr, allowed, cfg, roi=roi,
                                 lock_theta=True, phase=False) for j in js}
        order0 = ranked(locked)
        zf, z0 = free[order[0]].z, locked[order0[0]].z
        if not (np.isfinite(zf) and (not np.isfinite(z0) or zf > z0 + 3.0 * float(cfg.soft_delta_max))):
            pool, order = locked, order0
    best = pool[order[0]]
    second = pool[order[1]].z if len(order) > 1 else float("nan")
    amb = bool(np.isfinite(second) and np.isfinite(best.z) and
               best.z - second <= float(getattr(cfg, "anchor_time_delta", 0.003)))
    return int(order[0]), best.sim, float(best.z), amb


def _verify_candidate(k: int, img: np.ndarray, comp: Proxy, raw: Proxy, allowed: np.ndarray | None,
                      roi: tuple[int, int, int, int], scorer: "_Scorer", cfg, j: int, flip: bool, votes: float,
                      M: np.ndarray, n_inl: int, ratio: float, src: str, radius: int,
                      report: list | None, gray: list | None = None) -> Anchor | None:
    """The anchor test of one RANSAC candidate (RAW frame j, matrix M): the Sim is re-estimated against the best
    EXACT RAW frame within ``radius`` of j (score j-radius..j+radius under the RANSAC Sim -> jb, then
    :func:`_reestimate` over jb-1..jb+1) and accepted only when its masked ZNCC >= match_thresh - anchor_zncc_slack.
    A rejected candidate whose ZNCC still reaches none_thresh is appended to ``gray`` (source '<src>_gray') when
    given: geometric evidence that the frame's content is in RAW at a gray-zone score (FX-08)."""
    W = float(raw.full_size[0])
    try:
        sim = from_cv_matrix(M, False, W, tuple(raw.ratio), tuple(comp.ratio))
    except ValueError:
        return None
    if not (0.02 < sim.s < 50.0):
        return None
    js = [i for i in range(j - radius, j + radius + 1) if raw.has(i)]
    sc = scorer.scores(js, sim, flip)
    ib = _nanargmax(sc)
    if ib < 0:
        if report is not None:
            report.append({"k": int(k), "raw": int(j), "flip": bool(flip), "reason": "no_score"})
        return None
    jb, sim2, z2, ambiguous = _reestimate(img, raw, js[ib], sim, flip, allowed, roi, comp, cfg)
    if not np.isfinite(z2) or z2 < cfg.match_thresh - cfg.anchor_zncc_slack:
        if report is not None:
            report.append({"k": int(k), "raw": int(jb), "flip": bool(flip), "reason": "zncc",
                           "zncc": None if not np.isfinite(z2) else round(float(z2), 4), "inliers": n_inl})
        if gray is not None and np.isfinite(z2) and z2 >= float(cfg.none_thresh):
            gray.append(Anchor(int(k), int(jb), bool(flip), sim2, int(n_inl), float(ratio), float(votes), float(z2),
                               src + "_gray", bool(ambiguous)))
        return None
    return Anchor(int(k), int(jb), bool(flip), sim2, int(n_inl), float(ratio), float(votes), float(z2), src,
                  bool(ambiguous))


_LINE_FEAT: OrderedDict[tuple[int, int, int], tuple[np.ndarray, np.ndarray]] = OrderedDict()


def _line_raw_features(raw: Proxy, j: int, flip: bool, nfeat: int) -> tuple[np.ndarray, np.ndarray]:
    """SIFT of RAW proxy frame j (or its mirror) with ``nfeat`` features, computed on the frame itself (never the
    index's budget-limited features), per-process LRU."""
    import cv2
    key = (int(j), int(flip), int(nfeat))
    hit = _LINE_FEAT.get(key)
    if hit is not None:
        _LINE_FEAT.move_to_end(key)
        return hit
    img = np.asarray(raw.get(j))
    if flip:
        img = cv2.flip(img, 1)
    feat = detect_sift(img, None, nfeat)
    _LINE_FEAT[key] = feat
    if len(_LINE_FEAT) > 96:
        _LINE_FEAT.popitem(last=False)
    return feat


def line_search(k: int, comp: Proxy, raw: Proxy, allowed: np.ndarray | None, cfg, js: Sequence[int], flip: bool,
                roi: tuple[int, int, int, int] | None = None, report: list | None = None,
                source: str = "line", feats: dict | None = None) -> list[Anchor]:
    """Line-constrained re-search of competitor frame k (FX-08 'search before giving up'): pairwise SIFT + RANSAC
    against EACH RAW frame of ``js`` -- a neighbouring run's predicted window, a handful of frames instead of the
    whole RAW -- with relaxed acceptance (>= near_miss_inliers inliers at an inlier ratio >= line_search_min_ratio;
    the global min_inliers is unchanged). Such a match is only a CANDIDATE: the anchor test (Sim re-estimated over
    the exact RAW frames around it, masked ZNCC >= match_thresh - anchor_zncc_slack, :func:`_verify_candidate`)
    decides, best-inlier candidates first, at most ``line_search_verify`` of them (candidates within refine_radius
    of an accepted anchor converge onto it). RAW features are computed on the frames themselves with
    line_search_nfeatures (a RAW-only overlay such as a legal disclaimer takes part of a small budget). Returns
    verified anchors best-first (source ``source``). ``feats``: precomputed {(j, flip): RAW features} (the same
    :func:`_line_raw_features` values, computed once per batch by :func:`run_line_searches`)."""
    img = np.asarray(comp.get(k))
    h, w = img.shape[:2]
    if roi is None:
        roi = mask_bbox(allowed, (h, w))
    nfeat = int(getattr(cfg, "line_search_nfeatures", 2 * int(cfg.sift_nfeatures)))
    cpts, cdesc = detect_sift(img, allowed, nfeat, roi)
    n_min = int(getattr(cfg, "near_miss_inliers", 6))
    r_min = float(getattr(cfg, "line_search_min_ratio", 0.6))
    if len(cdesc) < max(3, n_min):
        if report is not None:
            report.append({"k": int(k), "reason": "few_keypoints", "n": int(len(cdesc))})
        return []
    cands = []
    for j in sorted({int(j) for j in js if raw.has(int(j))}):
        pre = feats.get((j, bool(flip))) if feats else None
        rpts, rdesc = pre if pre is not None else _line_raw_features(raw, j, flip, nfeat)
        M, n_inl, n_good = _ransac(cpts, cdesc, rpts, rdesc, cfg)
        ratio = n_inl / n_good if n_good else 0.0
        if M is None or n_inl < n_min or ratio < r_min:
            continue
        cands.append((j, M, n_inl, ratio))
    if not cands:
        if report is not None:
            report.append({"k": int(k), "reason": "no_candidate", "window": [min(js), max(js)] if js else None})
        return []
    scorer = _Scorer(img, roi, allowed, raw, comp.ratio, cfg)
    radius = int(cfg.refine_radius)
    found: dict[tuple[int, bool], Anchor] = {}
    tried = 0
    for j, M, n_inl, ratio in sorted(cands, key=lambda c: (-c[2], -c[3], c[0])):
        if tried >= int(getattr(cfg, "line_search_verify", 3)):
            break
        if any(abs(a.raw - j) <= radius for a in found.values()):
            continue
        tried += 1
        a = _verify_candidate(k, img, comp, raw, allowed, roi, scorer, cfg, j, flip, float(n_inl), M, n_inl, ratio,
                              source, radius, report)
        if a is not None and ((a.raw, a.flip) not in found or a.zncc > found[(a.raw, a.flip)].zncc):
            found[(a.raw, a.flip)] = a
    return sorted(found.values(), key=lambda a: (-a.zncc, a.raw, a.flip))


def _line_worker(state: dict, task: tuple[int, tuple[int, ...], bool]) -> tuple[int, list[dict], list]:
    k, js, flip = task
    rep: list = []
    anchors = line_search(int(k), state["comp"], state["raw"], state["allowed"](int(k)), state["cfg"], js, bool(flip),
                          roi=state["roi"], report=rep, feats=state.get("line_feats"))
    return int(k), [a.to_dict() for a in anchors[:int(getattr(state["cfg"], "anchors_per_frame", 3))]], rep[:12]


def _line_feat_worker(state: dict, task: tuple[int, bool]) -> tuple[np.ndarray, np.ndarray]:
    """SIFT features of RAW frame j (or its mirror) for line searches (:func:`_line_raw_features`)."""
    j, flip = task
    return _line_raw_features(state["raw"], int(j), bool(flip), int(state["nfeat"]))


def run_line_searches(comp: Proxy, raw: Proxy, allowed: Callable[[int], np.ndarray], roi: tuple[int, int, int, int],
                      tasks: Sequence[tuple[int, Sequence[int], bool]], cfg) -> list[tuple[int, list[Anchor], list]]:
    """:func:`line_search` on many (k, RAW window, flip) tasks in a worker pool. Input order kept."""
    items = [(int(k), tuple(int(j) for j in js), bool(fl)) for k, js, fl in tasks]
    workers = cfg.resolved_workers()
    # the RAW frames' SIFT features first, once each (neighbouring competitor frames search largely the same RAW
    # window: computed per task, every worker recomputed them), then the searches read them from the state
    nfeat = int(getattr(cfg, "line_search_nfeatures", 2 * int(cfg.sift_nfeatures)))
    need = sorted({(int(j), bool(fl)) for _k, js, fl in items for j in js if raw.has(int(j))})
    feats = dict(zip(need, parallel_map(_line_feat_worker, need, workers, {"raw": raw, "nfeat": nfeat}, cfg.seed,
                                        label="line search features")))
    state = {"comp": comp, "raw": raw, "cfg": cfg, "allowed": allowed, "roi": roi, "line_feats": feats}
    res = parallel_map(_line_worker, items, workers, state, cfg.seed, min_items=4)
    return [(k, [Anchor.from_dict(d) for d in ads], rep) for k, ads, rep in res]


def search_frame(k: int, comp: Proxy, raw: Proxy, index: RawIndex, allowed: np.ndarray | None, cfg,
                 window: tuple[int, int] | None = None, source: str = "global",
                 roi: tuple[int, int, int, int] | None = None,
                 report: list | None = None, near_miss: bool = False, pre: dict | None = None) -> list[Anchor]:
    """Find verified RAW matches (normal and flipped) of competitor frame k (DESIGN §5). ``pre``: this frame's
    SIFT features (``cpts``, ``cdesc``) and the index neighbours of them and of their mirror (``nn``, ``nn_f``),
    found beforehand (:func:`run_searches`, the GPU search) -- the same values this function would find.

    ``near_miss``: when no candidate passes, RANSAC near-misses (near_miss_inliers <= inliers < min_inliers,
    ratio >= min_inlier_ratio) that pass the same re-estimation and ZNCC test are returned with source
    '<source>_near'. They are NOT anchors of their own: refine lets them only join an existing track whose RAW
    time line they continue (FX-03 step 3; min_inliers itself is unchanged). When nothing passes at all, the full
    RANSAC matches whose re-estimated ZNCC lies in the gray zone [none_thresh, accept) are returned with source
    '<source>_gray' (at most two): refine scores the frames around them as evidence for UNRESOLVED, never as a
    match (FX-08 -- a sharpened / motion-blurred / processed picture of RAW content is not NOT-IN-RAW).

    SIFT inside ``allowed`` (bool [h, w] at comp proxy res; None = whole frame); the mirrored frame's
    descriptors (a permutation, :func:`mirror_features`) only vote; candidates = cluster-aware index
    votes (restricted to RAW ``window`` = [j0, j1) when given). Each candidate index frame j is
    verified: Lowe 0.75 against that single RAW frame (flip hypothesis: SIFT features of
    cv2.flip(raw_j, 1) against the UNFLIPPED comp keypoints), RANSAC RAW -> comp,
    ``inliers >= cfg.min_inliers`` and ``ratio >= cfg.min_inlier_ratio``; then the Sim is re-estimated
    against the best EXACT RAW frame within the index spacing (score j-R..j+R under the Sim -> jb, then
    :func:`_reestimate` over jb-1..jb+1) and accepted only if its masked ZNCC >=
    match_thresh - anchor_zncc_slack. Returns anchors best-first (one per (raw, flip)); rejected
    candidates are appended to ``report`` (evidence for the decision log) when given.
    """
    img = np.asarray(comp.get(k))
    h, w = img.shape[:2]
    if roi is None:
        roi = mask_bbox(allowed, (h, w))
    cpts, cdesc = (pre["cpts"], pre["cdesc"]) if pre is not None else detect_sift(img, allowed, cfg.sift_nfeatures, roi)
    near_min = int(getattr(cfg, "near_miss_inliers", 0) or 0) if near_miss else 0
    if len(cdesc) < max(3, min(cfg.min_inliers, near_min) if near_min else cfg.min_inliers):
        if report is not None:
            report.append({"k": int(k), "reason": "few_keypoints", "n": int(len(cdesc))})
        return []
    cdesc_f = cdesc[:, _MIRROR_IDX]          # = SIFT of the mirrored frame (votes for the flip hypothesis)
    top = int(cfg.vote_top_candidates)
    cands = [(j, False, v) for j, v in index.query(cdesc, top, window, nn=pre.get("nn") if pre else None)]
    cands += [(j, True, v) for j, v in index.query(cdesc_f, top, window, nn=pre.get("nn_f") if pre else None)]
    if not cands:
        if report is not None:
            report.append({"k": int(k), "reason": "no_votes"})
        return []
    cands.sort(key=lambda c: (-c[2], c[0], c[1]))
    vmax = cands[0][2]
    min_frac = float(getattr(cfg, "vote_min_frac", 0.2))
    cands = [c for c in cands if c[2] >= min_frac * vmax][:top]

    scorer = _Scorer(img, roi, allowed, raw, comp.ratio, cfg)
    radius = max(int(cfg.refine_radius), index.step // 2 + 1)
    found: dict[tuple[int, bool], Anchor] = {}

    gray: list[Anchor] = []

    def verify(j: int, flip: bool, votes: float, M: np.ndarray, n_inl: int, ratio: float, src: str) -> Anchor | None:
        return _verify_candidate(k, img, comp, raw, allowed, roi, scorer, cfg, j, flip, votes, M, n_inl, ratio, src,
                                 radius, report, gray if src == source else None)

    pending: list[tuple] = []
    for j, flip, votes in cands:
        if any(a.flip == flip and abs(a.raw - j) <= radius + 1 for a in found.values()):
            continue      # would re-estimate onto an already accepted exact frame
        rpts, rdesc = _raw_features(raw, index, j, flip, cfg.sift_nfeatures)
        M, n_inl, n_good = _ransac(cpts, cdesc, rpts, rdesc, cfg)
        ratio = n_inl / n_good if n_good else 0.0
        if M is None or n_inl < cfg.min_inliers or ratio < cfg.min_inlier_ratio:
            if report is not None:
                report.append({"k": int(k), "raw": int(j), "flip": bool(flip), "votes": round(votes, 2),
                               "reason": "ransac", "inliers": n_inl, "good": n_good})
            if near_min and M is not None and near_min <= n_inl < cfg.min_inliers and ratio >= cfg.min_inlier_ratio:
                pending.append((j, flip, votes, M, n_inl, ratio))
            continue
        a = verify(j, flip, votes, M, n_inl, ratio, source)
        if a is not None and ((a.raw, a.flip) not in found or a.zncc > found[(a.raw, a.flip)].zncc):
            found[(a.raw, a.flip)] = a
    if not found and pending:
        # near-misses (verified like anchors, at most the two with the most inliers): join-only evidence
        near: dict[tuple[int, bool], Anchor] = {}
        for j, flip, votes, M, n_inl, ratio in sorted(pending, key=lambda p: (-p[4], -p[2], p[0], p[1]))[:2]:
            a = verify(j, flip, votes, M, n_inl, ratio, source + "_near")
            if a is not None and ((a.raw, a.flip) not in near or a.zncc > near[(a.raw, a.flip)].zncc):
                near[(a.raw, a.flip)] = a
        if near:
            return sorted(near.values(), key=lambda a: (-a.zncc, a.raw, a.flip))
    if not found and gray:
        best: dict[tuple[int, bool], Anchor] = {}
        for a in gray:
            if (a.raw, a.flip) not in best or a.zncc > best[(a.raw, a.flip)].zncc:
                best[(a.raw, a.flip)] = a
        return sorted(best.values(), key=lambda a: (-a.zncc, -a.inliers, a.raw, a.flip))[:2]
    return sorted(found.values(), key=lambda a: (-a.zncc, a.raw, a.flip))


# ---------------------------------------------------------------------------------------------
# Sparse search
# ---------------------------------------------------------------------------------------------

def audio_window(hints: AudioHints | None, k: int, comp_fps, raw_fps, cfg, raw_n: int) -> tuple[int, int] | None:
    """RAW frame window [j0, j1) of +- cfg.audio_restrict_s around the confident audio hint nearest to
    competitor frame k (None when no confident hint covers k)."""
    if hints is None or len(hints.comp_t) == 0:
        return None
    conf = hints.confident(cfg.audio_min_conf)
    if not np.any(conf):
        return None
    t = float(k) / float(comp_fps)
    idx = np.flatnonzero(conf)
    d = np.abs(np.asarray(hints.comp_t)[idx] - t)
    i = int(idx[int(np.argmin(d))])
    if float(d.min()) > max(float(hints.window), 2.0 * float(hints.hop)):
        return None
    sp = float(hints.speed[i]) if np.isfinite(hints.speed[i]) else 1.0
    rt = float(hints.raw_t[i]) + sp * (t - float(hints.comp_t[i]))
    j = int(math.floor(rt * float(raw_fps) + 1e-9))
    r = int(math.ceil(cfg.audio_restrict_s * float(raw_fps)))
    j0, j1 = max(0, j - r), min(int(raw_n), j + r + 1)
    return (j0, j1) if j1 > j0 else None


def _search_worker(state: dict, task: tuple) -> tuple[int, list[dict], list]:
    k, window, source = task[:3]
    pre = task[3] if len(task) > 3 else None          # SIFT + GPU neighbours found beforehand (run_searches)
    comp, raw, index, cfg = state["comp"], state["raw"], state["index"], state["cfg"]
    allowed = state["allowed"](k)
    roi = state["roi"]
    rep: list = []
    anchors: list[Anchor] = []
    if window is not None:
        anchors = search_frame(k, comp, raw, index, allowed, cfg, window=window, source="audio", roi=roi, report=rep,
                               near_miss=True, pre=pre)
    weak = ("_near", "_gray")
    if not anchors or all(a.source.endswith(weak) for a in anchors):
        glob = search_frame(k, comp, raw, index, allowed, cfg, window=None, source=source, roi=roi, report=rep,
                            near_miss=True, pre=pre)
        rank = lambda aa: 0 if not aa else (2 if not all(a.source.endswith(weak) for a in aa) else  # noqa: E731
                                            (1 if any(a.source.endswith("_near") for a in aa) else 0.5))
        if glob and rank(glob) > rank(anchors):
            anchors = glob
    keep = int(getattr(cfg, "anchors_per_frame", 3))
    return int(k), [a.to_dict() for a in anchors[:keep]], rep[:12]


def _sift_worker(state: dict, k: int) -> tuple[np.ndarray, np.ndarray]:
    """search_frame's SIFT of competitor frame k (the same call, the same values)."""
    cfg = state["cfg"]
    return detect_sift(np.asarray(state["comp"].get(int(k))), state["allowed"](int(k)), cfg.sift_nfeatures,
                       state["roi"])


SEARCH_PARTS = 6           # run_searches: the GPU finds a part's neighbours while the CPU workers search the part before
SEARCH_PART_MIN = 200      # ... at least this many frames to a part (fewer: one part, as before)


def run_searches(comp: Proxy, raw: Proxy, index: RawIndex, allowed: Callable[[int], np.ndarray],
                 roi: tuple[int, int, int, int], hints: AudioHints | None, frames: Iterable[int], cfg,
                 source: str = "global") -> list[tuple[int, list[Anchor], list]]:
    """search_frame on many frames in a worker pool (audio window first, then global). Input order kept.

    A GPU-searched index (``index.flann_free``): the frames' SIFT features first (worker pool), then the exact
    neighbours of all of them and of their mirrors on the GPU in this process (one batch), then the searches with
    those -- the workers never hold a kd-tree (an every-frame index would need ~800 B per descriptor in each)."""
    workers = cfg.resolved_workers()
    tasks: list[tuple] = [(int(k), audio_window(hints, k, comp.fps, raw.fps, cfg, raw.n), source) for k in frames]
    state = {"comp": comp, "raw": raw, "index": index, "cfg": cfg, "allowed": allowed, "roi": roi}
    if not (index.flann_free and tasks):
        index.ensure_built()
        res = parallel_map(_search_worker, tasks, workers, state, cfg.seed, min_items=4)
        return [(k, [Anchor.from_dict(d) for d in ads], rep) for k, ads, rep in res]
    feats = parallel_map(_sift_worker, [t[0] for t in tasks], workers,
                         {"comp": comp, "cfg": cfg, "allowed": allowed, "roi": roi}, cfg.seed, min_items=4,
                         label="search features")

    def neighbours(part: list[int]) -> list[tuple[np.ndarray, np.ndarray]]:
        return index.neighbours([feats[i][1] for i in part] +
                                [np.ascontiguousarray(feats[i][1][:, _MIRROR_IDX]) for i in part])
    # Task 9: the next part's neighbours on the GPU (a thread of this process) while the workers search the part
    # before on the CPU -- each query's neighbours do not depend on the others searched with it, and every frame's
    # search is seeded on its own, so the results are the same as in one part
    n_parts = max(1, min(SEARCH_PARTS, len(tasks) // max(1, SEARCH_PART_MIN)))
    size = -(-len(tasks) // n_parts)
    parts = [list(range(p, min(p + size, len(tasks)))) for p in range(0, len(tasks), size)]
    out: list = []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as gpu:
        nxt = gpu.submit(neighbours, parts[0])
        for p, part in enumerate(parts):
            nn = nxt.result()
            if p + 1 < len(parts):
                nxt = gpu.submit(neighbours, parts[p + 1])
            m = len(part)
            ptasks = [(tasks[i][0], tasks[i][1], tasks[i][2],
                       {"cpts": feats[i][0], "cdesc": feats[i][1], "nn": nn[q], "nn_f": nn[m + q]})
                      for q, i in enumerate(part)]
            out.extend(parallel_map(_search_worker, ptasks, workers, state, cfg.seed, min_items=4,
                                    label="search" if len(parts) == 1 else f"search {p + 1}/{len(parts)}"))
    return [(k, [Anchor.from_dict(d) for d in ads], rep) for k, ads, rep in out]


def sparse_search(comp: Proxy, raw: Proxy, layout: Layout | None, overlays: Any, index: RawIndex,
                  hints: AudioHints | None, cfg, dlog: DecisionLog | None, frames: Iterable[int] | None = None,
                  cache: Cache | None = None, allowed_fn: Callable[[int], np.ndarray] | None = None) -> list[Anchor]:
    """Anchors for every cfg.comp_search_stride-th competitor frame (or ``frames``) (DESIGN §5).

    Audio-restricted (+- cfg.audio_restrict_s around a confident hint) first, global fallback; frames
    whose video region is uniform (std < cfg.uniform_std) are skipped. Runs in a worker pool (memmaps
    shared, seeded per item, results in input order). Cached as JSON (stage 'sparse_search') when a
    ``cache`` is given. ``allowed_fn`` overrides the default :class:`AllowedMasks`. Returns anchors
    sorted by (k, -zncc).
    """
    dlog = dlog or null_dlog()
    if frames is None:
        frames = list(range(0, comp.n, max(1, int(cfg.comp_search_stride))))
    else:
        frames = sorted(set(int(k) for k in frames))
    allowed = allowed_fn or AllowedMasks(layout, overlays, comp, cfg)
    roi = box_roi(layout, comp)
    key = stage_key("sparse_search", proxy_id(comp), proxy_id(raw), index.key, layout_id(layout),
                    overlays_id(overlays), hints_id(hints), cfg.analysis_params(), frames,
                    "custom_allowed" if allowed_fn is not None else "")

    def compute() -> list[dict]:
        todo = []
        skipped = []
        for k in frames:
            m = allowed(k)
            _, sd = scoring.region_stats(np.asarray(comp.get(k)), roi, m)
            if not np.isfinite(sd) or sd < cfg.uniform_std:
                skipped.append(k)
                continue
            todo.append(k)
        if skipped:
            dlog.record("visual_match", "skip_uniform", frames=skipped)
        out: list[dict] = []
        for k, anchors, rep in run_searches(comp, raw, index, allowed, roi, hints, todo, cfg, source="global"):
            if anchors:
                dlog.record("visual_match", "anchor", comp_frame=k,
                            evidence=[{"raw": a.raw, "flip": a.flip, "zncc": round(a.zncc, 4), "inliers": a.inliers,
                                       "ratio": round(a.inlier_ratio, 3), "votes": round(a.votes, 2),
                                       "source": a.source, "sim": a.sim.to_dict()} for a in anchors],
                            rejected=rep)
            else:
                dlog.record("visual_match", "no_anchor", comp_frame=k, rejected=rep)
            out.extend(a.to_dict() for a in anchors)
        return out

    data = cache.json("sparse_search", key, compute) if cache is not None else compute()
    anchors = [Anchor.from_dict(d) for d in data]
    anchors.sort(key=lambda a: (a.k, -a.zncc, a.raw, a.flip))
    log.info("sparse search: %d anchors on %d/%d searched frames", len(anchors), len({a.k for a in anchors}),
             len(frames))
    return anchors
