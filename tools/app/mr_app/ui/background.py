"""Work off the window's thread (night 11: "the window must never freeze"). ``call(fn, done, failed)`` runs ``fn()`` in
Qt's thread pool and hands its result to ``done`` (or the error, in plain words, to ``failed``) back in the
window's thread. Anything that reads a disk, a network folder, a video or the internet goes through here."""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_live: set = set()
_work_lock = threading.Lock()
_work = 0                       # jobs flagged as work (publish, Clean up, Change headline ...) not finished yet


class _Sig(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Run(QRunnable):
    def __init__(self, fn: Callable[[], Any], sig: _Sig):
        super().__init__()
        self.fn, self.sig = fn, sig
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            res = self.fn()
        except Exception as e:  # noqa: BLE001 - said to the caller in plain words
            self._say(self.sig.failed, f"{type(e).__name__}: {e}")
            return
        self._say(self.sig.done, res)

    @staticmethod
    def _say(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:          # the window closed while this ran: nobody to tell
            pass


def working() -> int:
    """How many background jobs flagged ``work=True`` are running now ("Turn off the PC" waits for them)."""
    with _work_lock:
        return _work


@contextlib.contextmanager
def counted_work():
    """Inside a background job: count only this part as work (``working()``) -- e.g. the 5-minute retry of waiting
    projects counts while it publishes something, not while it only looks whether there is anything to do."""
    global _work
    with _work_lock:
        _work += 1
    try:
        yield
    finally:
        with _work_lock:
            _work -= 1


def _counted(fn: Callable[[], Any]) -> Callable[[], Any]:
    global _work
    with _work_lock:
        _work += 1

    def run():
        global _work
        try:
            return fn()
        finally:
            with _work_lock:
                _work -= 1
    return run


def call(fn: Callable[[], Any], done: Callable[[Any], None] | None = None,
         failed: Callable[[str], None] | None = None, work: bool = False) -> None:
    """Run ``fn`` in the background; ``done(result)`` / ``failed(text)`` run in the window's thread afterwards.
    ``work=True``: a job that changes files for a while (publishing a project, Clean up, Change headline ...) --
    counted by ``working()`` from now until ``fn`` returns, so the PC is not turned off under it."""
    if work:
        fn = _counted(fn)
    sig = _Sig()
    _live.add(sig)

    def finish(cb, arg) -> None:
        _live.discard(sig)
        if cb is not None:
            try:
                cb(arg)
            except RuntimeError:                       # the widget it updates was closed meanwhile
                pass
    sig.done.connect(lambda r: finish(done, r))
    sig.failed.connect(lambda e: finish(failed, e))
    QThreadPool.globalInstance().start(_Run(fn, sig))


def pool_size() -> int:
    return QThreadPool.globalInstance().maxThreadCount()
