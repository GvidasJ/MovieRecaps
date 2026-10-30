"""Start the app: ``MovieRecaps.pyw`` (the desktop shortcut), or ``python tools\\app\\MovieRecaps.pyw --shortcut`` to
make the desktop shortcut (one line on the laptop)."""
from __future__ import annotations

import ctypes
import sys


def _crash_log() -> None:
    """30 Oct: the app closed by itself twice with no word -- now every crash leaves its reason in
    %APPDATA%\\MovieRecaps\\crash.log: a hard crash (faulthandler: every thread's stack), an error nobody caught (in
    any thread), and one line when the app starts and when it closes normally."""
    import faulthandler
    import os
    import threading
    import time
    import traceback
    from pathlib import Path
    try:
        d = Path(os.environ.get("APPDATA") or Path.home()) / "MovieRecaps"
        d.mkdir(parents=True, exist_ok=True)
        f = open(d / "crash.log", "a", encoding="utf-8", buffering=1)          # noqa: SIM115 - open for the app's life
    except OSError:
        return
    stamp = lambda: time.strftime("%Y-%m-%d %H:%M:%S")                        # noqa: E731
    f.write(f"\n=== {stamp()} the app started (pid {os.getpid()})\n")
    faulthandler.enable(file=f, all_threads=True)

    def hook(kind, tb_text):
        try:
            f.write(f"--- {stamp()} {kind}\n{tb_text}\n")
        except Exception:  # noqa: BLE001
            pass
    old = sys.excepthook
    sys.excepthook = lambda t, v, tb: (hook("error (main thread)", "".join(traceback.format_exception(t, v, tb))),
                                       old(t, v, tb))
    threading.excepthook = lambda a: hook(f"error (thread {getattr(a.thread, 'name', '?')})",
                                          "".join(traceback.format_exception(a.exc_type, a.exc_value,
                                                                             a.exc_traceback)))
    import atexit
    atexit.register(lambda: hook("the app closed normally", ""))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _crash_log()
    if "--shortcut" in argv:
        from .core import shortcut
        p = shortcut.make()
        print(f"desktop shortcut: {p}")
        return 0
    try:                                     # the app's own taskbar icon and notification sender (not python's)
        from . import APP_ID
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001 - not Windows
        pass
    from PySide6.QtWidgets import QApplication
    from .ui import theme
    try:                                     # run 18: the look first (Mono + red, or Settings -> Classic look)
        from .core import settings as S
        theme.apply_look(S.load().look)
    except Exception:  # noqa: BLE001 - never a crash at the start: the new look
        theme.apply_look("mono")
    from .tools import tools
    from .ui import widgets as W
    from .ui.main_window import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MovieRecaps")
    fam = theme.load_fonts()
    theme.FONT = fam
    theme.mono_fonts()
    fam = theme.FONT
    app.setFont(W.font(13, 400, family=fam))
    app.setStyleSheet(theme.qss(fam))
    theme.warm_fonts(app.font())             # the emoji font's first lookup (~0.5 s) before the window, not on a page
    win = MainWindow(tools())
    win.show()
    if "--tab" in argv:                      # open on that tab (download, convert, template, match_cuts)
        win.select(argv[argv.index("--tab") + 1])
    if "--screenshot" in argv:               # (tests: a picture of the window, then quit)
        from PySide6.QtCore import QTimer
        out = argv[argv.index("--screenshot") + 1]
        QTimer.singleShot(1500, lambda: (win.grab().save(out), app.quit()))
    return app.exec()
