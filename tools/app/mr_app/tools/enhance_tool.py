"""The Enhance page (run 20, on the Progress tab): the laptop's side of "enhance only the scene's piece".

With Settings' "Enhance pieces for the PC with Topaz" on (the laptop's default), the page's worker (core.enhance.Worker)
looks at the Enhance folder every 30 s and when the app starts: a piece the PC put there that nobody has taken is
taken and enhanced with Topaz Video AI, and the page says "Enhancing <name> on this laptop · 34 % · about 12 min
left" -- or, on a failure, why in plain words (the PC reads the same words from <name>_ENHANCE_FAILED.txt). "Test
Topaz" enhances 2 seconds of a test clip and says whether it worked."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import Tool
from ..ui import background as BG
from ..ui import theme
from ..ui import widgets as W


class _Relay(QObject):
    changed = Signal(object)            # the worker's thread -> the window's thread


class EnhancePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.worker = None
        self.relay = _Relay()
        self.relay.changed.connect(lambda _j: self.show_state())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(14)
        lay.addWidget(W.SectionHeader("Enhance on this laptop"))
        lay.addWidget(W.label("The PC puts the scene's piece (the competitor's length + 20 s) in the Enhance folder; this laptop "
                              "takes it by itself, runs Topaz Video AI on it (Proteus 2x, Apollo 60 fps, H.265 "
                              "Main10 180 Mb/s) and puts the enhanced piece back next to it. The PC then makes the "
                              "edit with it.", "Muted", wrap=True))
        card = W.Card(hover=False)
        self.now = W.label("", "Name", wrap=True, select=True)
        card.lay.addWidget(self.now)
        self.bar = W.label("", "Dim", wrap=True, select=True)
        card.lay.addWidget(self.bar)
        lay.addWidget(card)
        self.where = W.label("", "Dim", wrap=True, select=True)
        lay.addWidget(self.where)
        self.topaz = W.label("Topaz Video AI: looking for it…", "Dim", wrap=True, select=True)
        lay.addWidget(self.topaz)
        row = QHBoxLayout()
        self.b_test = W.Button("Test Topaz", "Small")
        self.b_test.clicked.connect(self.test_topaz)
        row.addWidget(self.b_test)
        self.b_look = W.Button("Look now", "Small")
        self.b_look.clicked.connect(lambda: self.worker.wake() if self.worker is not None else None)
        row.addWidget(self.b_look)
        self.test_line = W.label("", "Dim", wrap=True, select=True)
        row.addWidget(self.test_line, 1)
        lay.addLayout(row)
        lay.addWidget(W.SectionHeader("Done here lately"))
        self.done = W.label("Nothing yet.", "Dim", wrap=True, select=True)
        lay.addWidget(self.done)
        lay.addStretch(1)
        self.tick = QTimer(self)
        self.tick.setInterval(2000)
        self.tick.timeout.connect(self.show_state)
        self.tick.start()
        self.settings_changed(getattr(ctx, "settings", None))
        self._find_topaz()

    # -- the worker --
    def _settings(self):
        return getattr(self.ctx, "settings", None)

    def _folder(self):
        from ..core import enhance as E
        return E.share_dir(self._settings())

    def _edit_running(self) -> str:
        """29 Oct: Topaz never starts while this computer runs an edit (both on one graphics card: out of memory)."""
        pages = getattr(self.ctx, "pages", None) or {}
        mc = pages.get("match_cuts") if hasattr(pages, "get") else None
        try:
            return "the edit running on this computer is done" if mc is not None and mc.busy() else ""
        except Exception:  # noqa: BLE001
            return ""

    def settings_changed(self, s) -> None:
        from ..core import enhance as E
        if E.enhances(s):
            if self.worker is None:
                self.worker = E.Worker(self._folder, self.relay.changed.emit, hold=self._edit_running)
            self.worker.start()
        elif self.worker is not None:
            if not self.worker.busy:                  # (a piece being enhanced is finished first)
                self.worker.stop()
                self.worker = None
        self.show_state()

    def show_state(self) -> None:
        from ..core import enhance as E
        s = self._settings()
        on = E.enhances(s)
        self.where.setText(f"Enhance folder: {self._folder()}" + ("" if on else
                           "  ·  this computer does not enhance pieces (Settings -> Enhance pieces for the PC with "
                           "Topaz)"))
        w = self.worker
        jobs = list(w.jobs) if w is not None else []
        cur = next((j for j in reversed(jobs) if j.state == "enhancing"), None)
        if cur is not None:
            self.now.setText(cur.line())
            self.now.setStyleSheet("")
            n = int(round(20 * cur.share))
            self.bar.setText("█" * n + "░" * (20 - n))
        elif not on:
            self.now.setText("Off on this computer")
            self.now.setStyleSheet("")
            self.bar.setText("")
        elif w is not None and w.note:
            self.now.setText(f"Waiting: {w.note}")
            self.now.setStyleSheet(f"color: {theme.AMBER};")
            self.bar.setText("")
        else:
            self.now.setText(f"Nothing to enhance -- the Enhance folder is looked at every {int(E.SCAN_S)} s")
            self.now.setStyleSheet("")
            self.bar.setText("")
        old = [j for j in reversed(jobs) if j.state != "enhancing"][:8]
        self.done.setText("\n".join(j.line() for j in old) or "Nothing yet.")
        if old and old[0].state == "failed":
            self.done.setStyleSheet(f"color: {theme.RED};")
        else:
            self.done.setStyleSheet("")

    def _find_topaz(self) -> None:
        def work():
            from ..core import enhance as E
            return E.find_topaz()

        def got(tz) -> None:
            if tz is not None:
                self.topaz.setText(tz.line())
                self.topaz.setStyleSheet("" if tz.found else f"color: {theme.AMBER};")
        BG.call(work, got, lambda _e: None)

    def test_topaz(self) -> None:
        """2 seconds of a test clip through Topaz (in the background): did it work?"""
        self.b_test.setEnabled(False)
        self.test_line.setText("Testing Topaz on 2 seconds of a test clip…")
        self.test_line.setStyleSheet("")

        def work():
            from ..core import enhance as E
            return E.test_topaz()

        def got(r) -> None:
            r = r or {}
            self.b_test.setEnabled(True)
            self.test_line.setText(r.get("text") or "")
            self.test_line.setStyleSheet(f"color: {theme.GREEN if r.get('ok') else theme.RED};")

        BG.call(work, got, lambda e: got({"ok": False, "text": f"{type(e).__name__}: {e}"}))

    # -- the window asks before it closes --
    def busy(self) -> bool:
        return self.worker is not None and self.worker.busy

    def stop(self) -> None:
        """Topaz stopped (the piece is free again: the next start takes it again)."""
        if self.worker is not None:
            self.worker.stop()

    closing = stop                      # the app closes


class EnhanceTool(Tool):
    key = "enhance"
    title = "Enhance"

    def page(self, ctx) -> QWidget:
        return EnhancePage(ctx)
