"""20 Nov (job 4, pause and resume): a long step keeps its finished tasks on disk and the next run of the same step
(Resume, Try again after the PC was off) only does the tasks that are missing -- with the same results, bit for bit,
as a run that never stopped (match_cuts/checkpoint.py, visual_match.parallel_map).

Run: python -m pytest tools/match_cuts/tests/test_checkpoint_20nov.py"""
from __future__ import annotations

import os

import numpy as np
import pytest

from match_cuts import checkpoint as CK
from match_cuts import visual_match as vm

ITEMS = list(range(40))


def _work(state, x):
    """A task: OpenCV's RNG (seeded before every task, as RANSAC in the real workers) and an array."""
    import cv2
    v = np.zeros(3, np.float64)
    cv2.randu(v, 0.0, 1.0)
    return (x, state["mul"] * x, v, np.full((4, 4), x, np.float32))


def _stops_at(state, x):
    """As the app's Pause does it (the process is killed): the run ends at task state['stop'] -- in this
    process (inline); a file counts every task that was really computed."""
    if x == state["stop"]:
        raise KeyboardInterrupt("paused")
    with open(state["count"], "a", encoding="utf-8") as f:
        f.write(f"{x}\n")
    return _work(state, x)


def _same(a, b) -> bool:
    return len(a) == len(b) and all(x[0] == y[0] and x[1] == y[1] and np.array_equal(x[2], y[2]) and
                                    np.array_equal(x[3], y[3]) for x, y in zip(a, b))


def test_a_step_stopped_in_the_middle_goes_on_from_there_with_the_same_results(tmp_path):
    want = vm.parallel_map(_work, ITEMS, 1, {"mul": 7}, seed=5)              # no checkpoint: the plain run
    count = tmp_path / "count.txt"
    state = {"mul": 7, "stop": 25, "count": str(count)}
    with pytest.raises(KeyboardInterrupt):
        with CK.scope(tmp_path / "cache", "sparse_search", "k1"):
            vm.parallel_map(_stops_at, ITEMS, 1, state, seed=5)
    assert len(count.read_text().split()) == 25                            # tasks 0-24 done, then "paused"
    assert list((tmp_path / "cache" / "partial").iterdir())                # ... and kept
    state["stop"] = -1                                                     # the resumed run
    with CK.scope(tmp_path / "cache", "sparse_search", "k1") as s:
        got = vm.parallel_map(_stops_at, ITEMS, 1, state, seed=5)
    assert s.loaded == 25 and s.total == 40
    assert len(count.read_text().split()) == 25 + 15                       # only the 15 missing ones ran
    assert _same(got, want)                                                # the same, bit for bit
    assert not (tmp_path / "cache" / "partial" / "sparse_search-k1").exists()    # done: the checkpoint goes


def test_worker_processes_keep_their_results_too_and_a_resume_is_the_same(tmp_path, monkeypatch):
    monkeypatch.setattr(CK, "FLUSH_N", 4)                                  # (small records, as a long step's)
    want = vm.parallel_map(_work, ITEMS, 1, {"mul": 3}, seed=2)
    # a whole run in worker processes, "stopped" after it (the step's result never got into the cache)
    sc = CK.Scope(tmp_path / "p", "refine")
    CK._STACK.append(sc)                                                   # noqa: SLF001
    try:
        first = vm.parallel_map(_work, ITEMS, 3, {"mul": 3}, seed=2)
    finally:
        CK._STACK.remove(sc)                                               # noqa: SLF001
    assert _same(first, want)
    files = list((tmp_path / "p").iterdir())
    assert len(files) == 1
    # half of it was lost (the kill came while it wrote): the rest is computed again, in workers
    data = files[0].read_bytes()
    files[0].write_bytes(data[: len(data) // 2])
    with CK.scope(tmp_path, "refine", "x") as s:
        s.dir = tmp_path / "p"
        again = vm.parallel_map(_work, ITEMS, 3, {"mul": 3}, seed=2)
    assert 0 < s.loaded < 40
    assert _same(again, want)


def test_other_tasks_or_another_call_of_the_step_are_never_mixed_up(tmp_path):
    with CK.scope(tmp_path, "refine", "k"):
        a = vm.parallel_map(_work, ITEMS, 1, {"mul": 1}, seed=1)
        b = vm.parallel_map(_work, ITEMS, 1, {"mul": 2}, seed=1)          # the same tasks, a second time
        c = vm.parallel_map(_work, ITEMS[:10], 1, {"mul": 1}, seed=1)
    assert [r[1] for r in b] == [2 * x for x in ITEMS]                    # the second call: its own results
    assert [r[1] for r in a] == ITEMS and [r[1] for r in c] == ITEMS[:10]


def test_the_second_call_of_a_step_is_told_from_the_first_on_resume(tmp_path):
    """The same tasks given twice in one step (refine's passes): on resume each call gets its own results back."""
    count = tmp_path / "n.txt"
    s1 = {"mul": 1, "stop": -1, "count": str(count)}
    s2 = {"mul": 5, "stop": 30, "count": str(count)}
    with pytest.raises(KeyboardInterrupt):
        with CK.scope(tmp_path / "c", "refine", "k"):
            vm.parallel_map(_stops_at, ITEMS, 1, s1, seed=1)
            vm.parallel_map(_stops_at, ITEMS, 1, s2, seed=1)
    s2["stop"] = -1
    with CK.scope(tmp_path / "c", "refine", "k") as s:
        a = vm.parallel_map(_stops_at, ITEMS, 1, s1, seed=1)
        b = vm.parallel_map(_stops_at, ITEMS, 1, s2, seed=1)
    assert s.loaded == 40 + 30
    assert [r[1] for r in a] == ITEMS and [r[1] for r in b] == [5 * x for x in ITEMS]


def test_switched_off_nothing_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv(CK.ENV, "0")
    with CK.scope(tmp_path, "refine", "k") as s:
        vm.parallel_map(_work, ITEMS, 1, {"mul": 1}, seed=1)
    assert s is None and not (tmp_path / "partial").exists()


def test_no_step_open_nothing_is_kept_and_nothing_changes(tmp_path):
    assert CK.current() is None
    got = vm.parallel_map(_work, ITEMS, 1, {"mul": 7}, seed=5)
    assert _same(got, vm.parallel_map(_work, ITEMS, 1, {"mul": 7}, seed=5))


def test_a_record_cut_off_by_the_kill_is_dropped_and_the_rest_is_read(tmp_path):
    c = CK.Call(tmp_path / "x.bin")
    for i in range(10):
        c.add(i, {"v": i})
        c.flush()
    c.flush()
    data = (tmp_path / "x.bin").read_bytes()
    (tmp_path / "x.bin").write_bytes(data[:-3])                            # the last record half written
    got = CK.Call(tmp_path / "x.bin").load()
    assert got == {i: {"v": i} for i in range(9)}
    c2 = CK.Call(tmp_path / "x.bin")
    c2.load()
    c2.add(9, {"v": 9})
    c2.flush()
    assert CK.Call(tmp_path / "x.bin").load() == {i: {"v": i} for i in range(10)}     # appended after the good part
