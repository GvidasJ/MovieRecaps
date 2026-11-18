"""18 Nov ("it must not stop"): an error the app caught and went on from is still written down.

The app catches errors in many places so that one bad step never closes the window -- but a caught error that nobody
wrote down is a video that stopped "without saying why". ``note`` appends it, with its traceback, to this computer's
crash.log (%APPDATA%\\MovieRecaps: the same file the hard crashes go to, app.py), so the reason can be read
afterwards. It never raises and never shows anything."""
from __future__ import annotations

import threading
import time
import traceback

_LOCK = threading.Lock()
FILE = "crash.log"


def note(where: str, error: BaseException | None = None, text: str = "") -> None:
    """One entry: ``where`` (plain words: which step, which video), the error's traceback and / or ``text``."""
    try:
        from . import settings as S
        d = S.app_dir()
        d.mkdir(parents=True, exist_ok=True)
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__)) if error is not None else ""
        with _LOCK, open(d / FILE, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} caught, the app went on: {where}\n{text}\n{tb}\n")
    except Exception:  # noqa: BLE001 - a note is never a new problem
        pass
