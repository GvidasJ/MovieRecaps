"""Work off the window's thread (night 11: "the window must never freeze"). ``call(fn, done, failed)`` runs ``fn()`` in
Qt's thread pool and hands its result to ``done`` (or the error, in plain words, to ``failed``) back in the
window's thread. Anything that reads a disk, a network folder, a video or the internet goes through here."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_live: set = set()


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
            self.sig.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.sig.done.emit(res)


def call(fn: Callable[[], Any], done: Callable[[Any], None] | None = None,
         failed: Callable[[str], None] | None = None) -> None:
    """Run ``fn`` in the background; ``done(result)`` / ``failed(text)`` run in the window's thread afterwards."""
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
