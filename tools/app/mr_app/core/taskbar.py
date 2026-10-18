"""WINDOWS TASKBAR PROGRESS: the app's taskbar button fills with the overall progress (ITaskbarList3, through ctypes
only -- no comtypes, no new package).

    bar = TaskbarProgress(int(window.winId()))   # after window.show(): the taskbar button must exist
    bar.set(0.42)                                # 42 % (green)
    bar.state("error") / "paused" / "busy" / "normal" / "none"
    bar.clear()                                  # no bar

Call it from the GUI thread: the COM object belongs to the thread that made it, and calls from any other thread are
ignored. Before the button exists (the window not shown yet), after Explorer restarts, and on a window without a
button, the calls do nothing; the next change of the value shows again. Every call is a quiet no-op when it cannot
work: not Windows, no taskbar (Qt's offscreen platform, a service), a broken window handle -- nothing is raised and
nothing is logged louder than debug."""
from __future__ import annotations

import ctypes
import logging
import math
import sys
import threading
import uuid
from functools import lru_cache

log = logging.getLogger(__name__)

CLSID_TASKBARLIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_ITASKBARLIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"
CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
# TBPFLAG: TBPF_NOPROGRESS, TBPF_INDETERMINATE, TBPF_NORMAL, TBPF_ERROR, TBPF_PAUSED
STATES = {"none": 0x0, "busy": 0x1, "normal": 0x2, "error": 0x4, "paused": 0x8}
TOTAL = 1000                     # the share is sent in thousandths: unchanged thousandths are not sent again
# the interface's table of methods: IUnknown 0 QueryInterface, 1 AddRef, 2 Release; ITaskbarList 3 HrInit, 4 AddTab,
# 5 DeleteTab, 6 ActivateTab, 7 SetActiveAlt; ITaskbarList2 8 MarkFullscreenWindow; ITaskbarList3 9 SetProgressValue,
# 10 SetProgressState, 11 RegisterTab ...
_RELEASE, _HRINIT, _SET_VALUE, _SET_STATE = 2, 3, 9, 10


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


@lru_cache(maxsize=1)
def _ole32():
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    return ole32


_VALUE_ARGS = (ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_ulonglong)     # (hwnd, completed, total)
_STATE_ARGS = (ctypes.c_void_p, ctypes.c_int)                               # (hwnd, TBPFLAG)


class TaskbarProgress:
    """The progress on the taskbar button of the window ``hwnd`` (int(widget.winId()) of the shown main window).
    ``available``: the taskbar answered (ITaskbarList3 made and initialised); ``last_hr``: the last call's HRESULT
    (0 = done)."""

    def __init__(self, hwnd: int):
        self.hwnd = 0
        self.available = False
        self.last_hr: int | None = None
        self._obj: ctypes.c_void_p | None = None
        self._thread = threading.get_ident()
        self._value: int | None = None
        self._state: str | None = None
        try:
            self.hwnd = int(hwnd or 0)
        except (TypeError, ValueError, OverflowError):
            return
        if sys.platform != "win32" or not self.hwnd:
            return
        try:
            self._create()
        except Exception as e:  # noqa: BLE001 - no taskbar: a quiet no-op
            log.debug("taskbar progress not available: %s", e)
            self._obj, self.available = None, False

    # -- COM ----------------------------------------------------------------------------------------------------------

    def _create(self) -> None:
        ole32 = _ole32()
        ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)   # S_OK, S_FALSE (Qt did it already) or another mode
        clsid, iid = _guid(CLSID_TASKBARLIST), _guid(IID_ITASKBARLIST3)
        obj = ctypes.c_void_p()
        hr = int(ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER, ctypes.byref(iid),
                                        ctypes.byref(obj)))
        self.last_hr = hr
        if hr < 0 or not obj.value:
            return
        self._obj = obj
        if self._call(_HRINIT, ()) < 0:
            self._release()
            return
        self.available = True

    def _call(self, index: int, argtypes: tuple, *args) -> int:
        """The interface's method number ``index`` (this, *args) -> its HRESULT."""
        vtbl = ctypes.cast(self._obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
        hr = int(fn(self._obj, *args))
        self.last_hr = hr
        return hr

    def _release(self) -> None:
        if self._obj is not None:
            try:
                self._call(_RELEASE, ())
            except Exception:  # noqa: BLE001
                pass
        self._obj, self.available = None, False

    def _usable(self) -> bool:
        return self.available and self._obj is not None and threading.get_ident() == self._thread

    # -- the window's calls -------------------------------------------------------------------------------------------

    def set(self, share: float) -> None:
        """The bar at ``share`` (0..1) of the button: shown (normal) when there was none or it was busy; it keeps the
        error / paused colour. The same thousandth again is not sent again."""
        if not self._usable():
            return
        try:
            f = float(share)
            if not math.isfinite(f):
                return
            v = int(round(min(1.0, max(0.0, f)) * TOTAL))
            if v == self._value and self._state in ("normal", "error", "paused"):
                return
            if self._call(_SET_VALUE, _VALUE_ARGS, self.hwnd, v, TOTAL) >= 0:
                self._value = v
                if self._state in (None, "none", "busy"):
                    self._state = "normal"
        except Exception as e:  # noqa: BLE001
            log.debug("taskbar progress: %s", e)

    def state(self, word: str) -> None:
        """'normal' (green), 'error' (red), 'paused' (yellow), 'busy' (the moving bar, no share) or 'none' (no bar);
        any other word: nothing."""
        if not self._usable():
            return
        w = str(word or "").strip().lower()
        flag = STATES.get(w)
        if flag is None or w == self._state:
            return
        try:
            if self._call(_SET_STATE, _STATE_ARGS, self.hwnd, flag) >= 0:
                self._state = w
                if w in ("none", "busy"):
                    self._value = None
        except Exception as e:  # noqa: BLE001
            log.debug("taskbar progress: %s", e)

    def clear(self) -> None:
        """No bar on the button."""
        self.state("none")

    def close(self) -> None:
        """Let the taskbar object go (the bar is cleared first). Later calls do nothing."""
        if not self._usable():
            return
        self.clear()
        self._release()
