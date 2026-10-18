"""The window never freezes (night 11): background.call runs slow work off the window's thread and hands the result
(or the error, in plain words) back to it. Run: python -m pytest tools/app/tests/test_background.py"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.ui import background as BG  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_slow_work_runs_off_the_window_thread_and_its_result_comes_back_to_it(qapp):
    main = threading.get_ident()
    seen = {}

    def slow():
        seen["worker"] = threading.get_ident()
        time.sleep(0.5)
        return 42

    def done(v):
        seen["result"] = v
        seen["done_in"] = threading.get_ident()
    t0 = time.perf_counter()
    BG.call(slow, done)
    assert time.perf_counter() - t0 < 0.05             # call() itself returns at once
    ticks = 0
    while "result" not in seen and time.perf_counter() - t0 < 5:
        qapp.processEvents()
        ticks += 1
        time.sleep(0.005)
    assert seen["result"] == 42 and seen["worker"] != main and seen["done_in"] == main
    assert ticks > 40                                  # the window kept answering the whole time


def test_an_error_comes_back_in_words_not_as_a_crash(qapp):
    got = []

    def bad():
        raise FileNotFoundError("no such folder")
    BG.call(bad, lambda v: got.append(("ok", v)), lambda e: got.append(("err", e)))
    t0 = time.perf_counter()
    while not got and time.perf_counter() - t0 < 5:
        qapp.processEvents()
        time.sleep(0.005)
    assert got == [("err", "FileNotFoundError: no such folder")]
