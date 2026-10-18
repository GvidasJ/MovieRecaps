"""The taskbar progress (mr_app.core.taskbar): never an exception -- a hidden Qt window on the offscreen platform (no
real taskbar), broken window handles, another thread, not Windows -- and what is sent when (the calls counted with a
stand-in for the COM call). Seen working on the real taskbar: work/t26/core-b/taskbar_live.py.
Run: python -m pytest tools/app/tests/test_taskbar.py"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import taskbar as T   # noqa: E402

WORDS = ("normal", "error", "paused", "busy", "none", "NORMAL", " Error ", "bogus", "", None)
SHARES = (0, 0.25, 1, 1.5, -2, float("nan"), float("inf"), "0.5", "x", None)


def exercise(bar: T.TaskbarProgress) -> None:
    for s in SHARES:
        bar.set(s)
    for w in WORDS:
        bar.state(w)
    bar.set(0.7)
    bar.clear()
    bar.close()
    bar.set(0.2)                                   # after close: nothing
    bar.state("error")


def test_a_hidden_qt_window_without_a_real_taskbar_never_raises():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = QtWidgets.QWidget()
    bar = T.TaskbarProgress(int(w.winId()))
    exercise(bar)
    w.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("hwnd", [0, None, "abc", -1, 2 ** 70, 123456789, 0xDEADBEEF, 3.7])
def test_a_broken_window_handle_never_raises(hwnd):
    bar = T.TaskbarProgress(hwnd)
    exercise(bar)
    if hwnd in (0, None, "abc", 2 ** 70) or sys.platform != "win32":
        assert not bar.available


def test_a_real_native_window_without_a_button_never_raises():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    user32 = ctypes.WinDLL("user32")
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    hwnd = user32.CreateWindowExW(0, "STATIC", "core-b test", 0, 0, 0, 10, 10, None, None, None, None)
    assert hwnd
    try:
        bar = T.TaskbarProgress(hwnd)
        if bar.available:                                  # the taskbar answered: HrInit gave S_OK
            assert bar.last_hr == 0
            bar.set(0.5)
            assert isinstance(bar.last_hr, int)
        exercise(bar)
    finally:
        user32.DestroyWindow(hwnd)


def test_not_windows_is_a_quiet_no_op(monkeypatch):
    monkeypatch.setattr(T.sys, "platform", "linux")
    bar = T.TaskbarProgress(123456)
    assert not bar.available and bar.last_hr is None
    exercise(bar)


def test_the_taskbar_list_ids():
    g = T._guid(T.CLSID_TASKBARLIST)
    assert (g.Data1, g.Data2, g.Data3, bytes(g.Data4)) == (0x56FDF344, 0xFD6D, 0x11D0,
                                                            bytes.fromhex("958A006097C9A090"))
    i = T._guid(T.IID_ITASKBARLIST3)
    assert (i.Data1, i.Data2, i.Data3, bytes(i.Data4)) == (0xEA1AFB91, 0x9E28, 0x4B86,
                                                            bytes.fromhex("90E99E9F8A5EEFAF"))
    assert T.STATES == {"none": 0, "busy": 1, "normal": 2, "error": 4, "paused": 8}


@pytest.fixture
def counted(monkeypatch):
    """A TaskbarProgress whose COM calls are counted, not made: [(method number, args)]."""
    monkeypatch.setattr(T.sys, "platform", "linux")          # nothing real is made
    bar = T.TaskbarProgress(4242)
    calls = []

    def fake(index, argtypes, *args):
        calls.append((index, args))
        bar.last_hr = 0
        return 0
    bar._call = fake
    bar._obj = ctypes.c_void_p(1)
    bar.available = True
    return bar, calls


def test_what_is_sent(counted):
    bar, calls = counted
    bar.set(0.42)
    assert calls == [(T._SET_VALUE, (4242, 420, 1000))]
    bar.set(0.4204)                                          # the same thousandth: not again
    bar.set(0.42)
    assert len(calls) == 1
    bar.set(2.0)
    bar.set(-1)
    assert calls[1:] == [(T._SET_VALUE, (4242, 1000, 1000)), (T._SET_VALUE, (4242, 0, 1000))]
    del calls[:]
    bar.state("error")
    bar.state("ERROR")                                       # the same state: not again
    bar.state("bogus")
    assert calls == [(T._SET_STATE, (4242, 4))]
    bar.set(0.0)                                             # the same value in the error colour: not again
    assert len(calls) == 1
    bar.clear()
    bar.set(0.0)                                             # no bar now: the value shows it again
    assert calls[1:] == [(T._SET_STATE, (4242, 0)), (T._SET_VALUE, (4242, 0, 1000))]
    del calls[:]
    bar.state("busy")
    bar.set(0.5)
    bar.state("paused")
    assert calls == [(T._SET_STATE, (4242, 1)), (T._SET_VALUE, (4242, 500, 1000)), (T._SET_STATE, (4242, 8))]


def test_a_failed_call_is_tried_again_next_time(counted):
    bar, calls = counted

    def failing(index, argtypes, *args):
        calls.append((index, args))
        return -2147467259                                   # E_FAIL: e.g. the button is not there yet
    bar._call = failing
    bar.set(0.3)
    bar.set(0.3)
    assert len(calls) == 2                                   # not remembered as sent
    bar._call = lambda *a: (_ for _ in ()).throw(OSError("access violation"))
    bar.set(0.9)                                             # an exception inside: quiet
    bar.state("error")


def test_calls_from_another_thread_are_ignored(counted):
    bar, calls = counted
    th = threading.Thread(target=lambda: (bar.set(0.5), bar.state("error"), bar.clear(), bar.close()))
    th.start()
    th.join(10)
    assert calls == [] and bar.available
    bar.set(0.5)
    assert len(calls) == 1


def test_close_clears_the_bar_and_lets_go(counted):
    bar, calls = counted
    bar.set(0.5)
    bar.close()
    assert [c[0] for c in calls] == [T._SET_VALUE, T._SET_STATE, T._RELEASE]
    assert not bar.available
    bar.set(0.7)
    assert len(calls) == 3
