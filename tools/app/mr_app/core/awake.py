"""17 Nov: the computer stays awake while the app works.

Until now nothing told Windows that a run was going on: with no key or mouse touched, a computer on a power plan that
sleeps after some minutes went to sleep in the middle of an edit, a Topaz piece or a copy to the shared folder -- the
work "stopped for some reason" and went on only after someone woke the computer (the laptop: also its link to the PC's
shared folder was gone then).

``keep(True)`` asks Windows not to sleep by itself (SetThreadExecutionState: ES_CONTINUOUS | ES_SYSTEM_REQUIRED; the
screen may still turn off); ``keep(False)`` gives that back. Both must be called from the same thread (the window's):
the request belongs to the thread and ends with it, so a closed or crashed app never keeps the computer awake.
Closing the laptop's lid or pressing its power button still sleeps it -- that is your call, not the idle timer's.
Does nothing on a computer that is not Windows. MOVIERECAPS_KEEP_AWAKE=0 turns it off."""
from __future__ import annotations

import os
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_on = False


def enabled() -> bool:
    return os.environ.get("MOVIERECAPS_KEEP_AWAKE", "").strip() != "0"


def is_on() -> bool:
    return _on


def _ask(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        fn = ctypes.windll.kernel32.SetThreadExecutionState
        fn.restype = ctypes.c_uint32
        fn.argtypes = [ctypes.c_uint32]
        return bool(fn(flags))
    except Exception:  # noqa: BLE001 - staying awake is best effort
        return False


def keep(on: bool) -> bool:
    """The computer is kept awake (``on``) or may sleep again. Only a change asks Windows; True when it changed."""
    global _on
    on = bool(on) and enabled()
    if on == _on:
        return False
    _ask(ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS)
    _on = on
    return True
