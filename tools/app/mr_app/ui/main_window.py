"""The window: a thin strip on top (this computer's GPU, Premiere, the output folder), the bar with the brand and one
tab per tool, the tool's page over the grid background; Windows notifications from the tray."""
from __future__ import annotations

import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import paths, premiere
from . import theme
from . import widgets as W

CONTENT_W = 1040                    # the page's width (the reference: a centred column)


def gpu_name() -> str:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                           text=True, timeout=10, creationflags=0x08000000)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else "no NVIDIA GPU"
    except Exception:  # noqa: BLE001
        return "no NVIDIA GPU"


class MainWindow(QMainWindow):
    def __init__(self, tools):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1240, 900)
        self.setMinimumSize(980, 640)
        icon = QIcon(str(paths.ICON_ICO)) if paths.ICON_ICO.is_file() else QIcon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()
        bg = W.GridBackground()
        self.setCentralWidget(bg)
        root = QVBoxLayout(bg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # the thin strip on top (the reference's "accounts in stock · instant delivery")
        strip = QFrame()
        strip.setObjectName("Strip")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(24, 5, 24, 5)
        self.strip_items = [W.label("", "StripText") for _ in range(4)]
        for i, lb in enumerate(self.strip_items):
            if i:
                sl.addStretch(1)
            sl.addWidget(lb)
        root.addWidget(strip)
        # the bar: brand + one tab per tool
        nav = QFrame()
        nav.setObjectName("Nav")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(0, 10, 0, 10)
        inner = QHBoxLayout()
        inner.setSpacing(6)
        logo = QLabel()
        if paths.ICON_PNG.is_file():
            logo.setPixmap(QIcon(str(paths.ICON_PNG)).pixmap(22, 22))
        inner.addWidget(logo)
        brand = W.label(APP_NAME, "Brand")
        brand.setFont(W.font(16, 700, -1))
        inner.addWidget(brand)
        inner.addStretch(1)
        self.stack = QStackedWidget()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.pages = {}
        for k, t in enumerate(tools):
            b = QPushButton(t.title + ("" if t.ready else "  ·  soon"))
            b.setObjectName("NavTab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip("" if t.ready else f"{t.title}: coming later")
            self.group.addButton(b, k)
            inner.addWidget(b)
            page = t.page(self)
            self.pages[t.key] = page
            self.stack.addWidget(self._scroll(page))
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        self.group.button(0).setChecked(True)
        nl.addStretch(1)
        box = QWidget()
        box.setObjectName("Transparent")
        box.setFixedWidth(CONTENT_W)
        box.setLayout(inner)
        nl.addWidget(box)
        nl.addStretch(1)
        root.addWidget(nav)
        root.addWidget(self.stack, 1)
        self._fill_strip()

    def _scroll(self, page: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("Transparent")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(24, 0, 24, 0)
        hl.addStretch(1)
        page.setMaximumWidth(CONTENT_W)
        page.setMinimumWidth(min(CONTENT_W, 900))
        hl.addWidget(page, 100)
        hl.addStretch(1)
        sa.setWidget(holder)
        return sa

    def _fill_strip(self) -> None:
        exe = premiere.find()
        self.strip_items[0].setText(f"GPU  {gpu_name()}")
        self.strip_items[1].setText(f"Premiere  {exe.parent.name if exe else 'not found'}")
        self.strip_items[2].setText(f"Output  {paths.output_dir()}")
        self.set_strip_queue(0, False)

    # -- for the tools --
    def select(self, key: str) -> None:
        """Show the tab of that tool (its key: download, convert, template, match_cuts)."""
        keys = list(self.pages)
        if key in keys:
            i = keys.index(key)
            self.group.button(i).setChecked(True)
            self.stack.setCurrentIndex(i)

    def set_strip_queue(self, n: int, running: bool) -> None:
        self.strip_items[3].setText(f"Queue  {n} video(s)" + ("  ·  running" if running else ""))

    def notify(self, title: str, text: str) -> None:
        """A Windows notification (the tray's balloon: a toast on Windows 10 / 11)."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.showMessage(title, text, self.windowIcon(), 15000)
        if not self.isActiveWindow():
            from PySide6.QtWidgets import QApplication
            QApplication.alert(self, 0)

    def showEvent(self, e) -> None:                       # noqa: N802
        W.dark_title_bar(self)
        super().showEvent(e)

    def closeEvent(self, e) -> None:                      # noqa: N802
        busy = [p for p in self.pages.values() if getattr(p, "busy", lambda: False)()]
        if busy:                                          # a run, a download or a conversion still going
            r = QMessageBox.question(self, "Still working",
                                     "A video is still being made, downloaded or converted. Stop it and close the "
                                     "app?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            for p in busy:
                p.stop()
        self.tray.hide()
        super().closeEvent(e)
