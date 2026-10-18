"""The window (night 11, the look and motion of docs\\ui_preview.html):

* a thin mono strip on top -- this computer's graphics card, Premiere, where projects are saved, a timecode clock,
  "replay intro", "settings";
* the brand (the match-cut logo, Movie in white, Recaps in red) and the tabs Match cuts · Board · Progress · History
  (Match cuts holds the Queue and Learn, Progress holds Download, Convert and Template);
* the quote line; the pages over the fading grid; the toast; the intro.

Keys: Space starts the queue, Del removes the queue row under the mouse, Ctrl+V pastes a YouTube link into Download
(only when no text field has the keyboard). The taskbar button fills with the overall progress. Everything slow
runs in the background (background.py)."""
from __future__ import annotations

import subprocess

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QScrollArea, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import paths
from . import background as BG
from . import chrome
from . import motion as M
from . import tabs as T
from . import theme
from . import widgets as W

CONTENT_W = 1040                    # the pages' column (the reference: a centred column)
WIDE_W = 1680                       # the Board's
TABS = (("Match cuts", ("match_cuts", "learn")), ("Board", ("board",)),
        ("Progress", ("download", "convert", "template")), ("History", ("history",)))
SUB_TITLES = {"match_cuts": "Queue", "learn": "Learn", "download": "Download", "convert": "Convert",
              "template": "Template", "board": "Board", "history": "History"}
WIDE = {"board"}


def gpu_name() -> str:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                           text=True, timeout=10, creationflags=0x08000000)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else "no NVIDIA GPU"
    except Exception:  # noqa: BLE001
        return "no NVIDIA GPU"


def _column(widget: QWidget, width: int = CONTENT_W, margins: tuple[int, int] = (24, 24)) -> QWidget:
    """``widget`` in a centred column at most ``width`` wide."""
    holder = QWidget()
    holder.setObjectName("Transparent")
    hl = QHBoxLayout(holder)
    hl.setContentsMargins(margins[0], 0, margins[1], 0)
    hl.addStretch(1)
    widget.setMaximumWidth(width)
    hl.addWidget(widget, 100)
    hl.addStretch(1)
    return holder


class Area(QWidget):
    """One tab's area: its small sub-tabs (when it holds several pages) and the pages, each scrolling on its own."""

    def __init__(self, keys: tuple[str, ...], pages: dict):
        super().__init__()
        self.setObjectName("Transparent")
        self.keys = [k for k in keys if k in pages]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.sub = None
        if len(self.keys) > 1:
            self.sub = T.TabBar([SUB_TITLES.get(k, k) for k in self.keys], small=True, spacing=2)
            row = QWidget()
            row.setObjectName("Transparent")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 14, 0, 0)
            rl.addWidget(self.sub)
            rl.addStretch(1)
            lay.addWidget(_column(row))
        self.stack = T.PageStack()
        for k in self.keys:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.Shape.NoFrame)
            page = pages[k]
            page.setMinimumWidth(min(CONTENT_W, 900))
            sa.setWidget(_column(page, WIDE_W if k in WIDE else CONTENT_W))
            self.stack.addWidget(sa)
        lay.addWidget(self.stack, 1)
        if self.sub is not None:
            self.sub.changed.connect(lambda i, old: self.stack.glide_to(i, i > old))

    def show_key(self, key: str) -> None:
        if key in self.keys:
            i = self.keys.index(key)
            if self.sub is not None:
                old = self.sub.index
                self.sub.select(i)
                self.stack.glide_to(i, i > old)
            else:
                self.stack.setCurrentIndex(i)

    def current_key(self) -> str:
        return self.keys[self.stack.currentIndex()] if self.keys else ""


class MainWindow(QMainWindow):
    def __init__(self, tools):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 920)
        self.setMinimumSize(980, 640)
        icon = QIcon(str(paths.ICON_ICO)) if paths.ICON_ICO.is_file() else QIcon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()
        self.settings = _load_settings()
        M.set_enabled(bool(getattr(self.settings, "animations", True)))
        bg = W.GridBackground()
        self.setCentralWidget(bg)
        root = QVBoxLayout(bg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._strip())
        root.addWidget(self._header(tools))
        self.quote = chrome.QuoteLine()
        self.quote.clicked.connect(self.next_quote)
        root.addWidget(_column(self.quote))
        root.addWidget(self.stack, 1)
        self.toast_w = W.Toast(bg)
        self.intro = chrome.Intro(bg)
        self._queue = (0, False)
        self._taskbar = None
        self._shortcuts()
        self._fill_strip()
        self.hub = None                                   # the shared files' folder, found in the background
        self._qb = None
        self.find_hub()
        self._quote_timer = QTimer(self)
        self._quote_timer.timeout.connect(self.refresh_quote)
        self._quote_timer.start(60_000)
        board = self.pages.get("board")
        if board is not None and hasattr(board, "changed"):
            board.changed.connect(self._board_changed)
        self._pending_timer = QTimer(self)                # finished projects kept here while the share was away
        self._pending_timer.timeout.connect(self.retry_pending)
        self._pending_timer.start(5 * 60_000)
        QTimer.singleShot(8000, self.retry_pending)

    # -- the board feeds the motivation strip and the history --
    def _board_changed(self, cards: list) -> None:
        mc, hist, board = self.pages.get("match_cuts"), self.pages.get("history"), self.pages.get("board")
        try:
            from ..core import board as B
            ups = B.uploads(cards)
        except Exception:  # noqa: BLE001
            ups = []
        if mc is not None and hasattr(mc, "show_uploads"):
            mc.show_uploads(ups, self.settings)
        if hist is not None and hasattr(hist, "set_projects"):
            hist.set_projects(list(getattr(board, "datas", []) or []))

    # -- section 2: a finished project goes to the shared folder --
    def project_done(self, job) -> None:
        """A project's edit is made: its finished parts go to "Save projects to" (in the background); when that
        folder cannot be reached the project stays here, an amber note says so, and it moves there later."""
        folder = getattr(job, "project", None)
        if folder is None:
            return
        root = _save_root(self.settings)
        local = paths.projects_root()

        def work():
            from ..core import publish
            return publish.publish_or_queue(folder, root, local)

        def done(r) -> None:
            res, note = r
            if res is None:
                self.toast(note or f"Saved on this computer: {root} can't be reached -- it moves there when it's back",
                           "note")
                self.refresh_save_place()
            else:
                self.toast(f"Saved to {root} ✓")
            b = self.pages.get("board")
            if b is not None:
                b.reload()

        def failed(err: str) -> None:
            try:
                from ..core import publish
                publish.queue_pending(local, folder, root, err)
            except Exception:  # noqa: BLE001
                pass
            self.toast(f"Not saved to the shared folder yet ({err}) -- tried again later", "note")
        BG.call(work, done, failed)

    def retry_pending(self) -> None:
        """Projects kept here while the shared folder was away: moved there now when it is back."""
        root = _save_root(self.settings)
        local = paths.projects_root()

        def work():
            from ..core import projects as PJ
            from ..core import publish
            if not publish.reachable(root, 5.0):
                return None
            done = list(publish.retry_pending(local, root)) if publish.pending(local) else []
            for p in PJ.list_projects(local):         # finished before (or on another night) but never published
                edit = (p.data.get("steps") or {}).get("edit") or {}
                if edit.get("state") == PJ.DONE and not p.data.get("published"):
                    try:
                        res, _note = publish.publish_or_queue(p.folder, root, local)
                    except Exception:  # noqa: BLE001 - the next pass tries again
                        continue
                    if res is not None:
                        done.append(res)
            return done

        def done(res) -> None:
            if res:
                self.toast(f"{len(res)} project(s) moved to {root} ✓")
                self.refresh_save_place()
                b = self.pages.get("board")
                if b is not None:
                    b.reload()
        BG.call(work, done, lambda _e: None)

    def find_hub(self) -> None:
        """Where the shared files are (the save folder, else this computer's): looked up in the background (a network
        folder that is not there can take seconds to say so), then the quote line starts."""
        def got(folder) -> None:
            self.hub = folder
            self._qb = None
            self.refresh_quote()
            for p in self.pages.values():
                if hasattr(p, "hub_changed"):
                    p.hub_changed(folder)
        BG.call(lambda: _hub(self.settings), got)

    def hub_folder(self):
        """The shared files' folder (this computer's projects folder until the lookup is done)."""
        return self.hub or paths.projects_root()

    # -- building --
    def _strip(self) -> QWidget:
        strip = QFrame()
        strip.setObjectName("Strip")
        strip.setStyleSheet(f"QFrame#Strip {{ border-bottom: 1px solid {theme.LINE}; background: transparent; }}")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(14, 6, 14, 6)
        sl.setSpacing(24)
        self.strip_gpu = W.mono_label("GPU …")
        self.strip_prem = W.mono_label("Premiere …")
        self.strip_save = W.mono_label("Save to …")
        self.clock = chrome.Timecode()
        self.replay = chrome.LinkButton("replay intro")
        self.replay.clicked.connect(self.play_intro)
        self.settings_btn = chrome.LinkButton("settings")
        self.settings_btn.clicked.connect(self.open_settings)
        for w in (self.strip_gpu, self.strip_prem, self.strip_save):
            sl.addWidget(w)
            sl.addStretch(1)
        sl.addWidget(self.clock)
        sl.addWidget(self.replay)
        sl.addWidget(self.settings_btn)
        return strip

    def _header(self, tools) -> QWidget:
        head = QFrame()
        head.setObjectName("Head")
        head.setStyleSheet(f"QFrame#Head {{ border-bottom: 1px solid {theme.LINE}; background: transparent; }}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 14, 0, 14)
        inner = QWidget()
        inner.setObjectName("Transparent")
        il = QHBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(12)
        il.addWidget(W.brand())
        il.addStretch(1)
        self.pages: dict = {}
        self.tools = {}
        for t in tools:
            self.tools[t.key] = t
            self.pages[t.key] = t.page(self)
        self.areas = [Area(keys, self.pages) for _title, keys in TABS]
        self.tabbar = T.TabBar([title for title, _keys in TABS])
        il.addWidget(self.tabbar)
        hl.addWidget(_column(inner))
        self.stack = T.PageStack()
        for a in self.areas:
            self.stack.addWidget(a)
        self.tabbar.changed.connect(lambda i, old: self.stack.glide_to(i, i > old))
        return head

    def _shortcuts(self) -> None:
        for keys, fn in (("Space", self._key_start), ("Delete", self._key_delete), ("Ctrl+V", self._key_paste)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)

    def _typing(self) -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit))

    def _key_start(self) -> None:
        mc = self.pages.get("match_cuts")
        if mc is not None and not self._typing() and hasattr(mc, "start"):
            mc.start()

    def _key_delete(self) -> None:
        mc = self.pages.get("match_cuts")
        if mc is not None and not self._typing() and hasattr(mc, "remove_row_under_mouse"):
            mc.remove_row_under_mouse()

    def _key_paste(self) -> None:
        if self._typing():
            return
        text = (QApplication.clipboard().text() or "").strip()
        dl = self.pages.get("download")
        if dl is None or not text:
            return
        if hasattr(dl, "paste_link") and dl.paste_link(text):
            self.select("download")
            self.toast("Link added to Download ✓")
        else:
            self.toast("The clipboard holds no link", "note")

    # -- the strip --
    def _fill_strip(self) -> None:
        def work():
            from ..core import premiere
            exe = premiere.find()
            return gpu_name(), (exe.parent.name if exe else "not found")

        def got(r) -> None:
            self.strip_gpu.setText(f"GPU {r[0]}")
            self.strip_prem.setText(f"Premiere {r[1]}")
        BG.call(work, got)
        self.refresh_save_place()

    def refresh_save_place(self) -> None:
        """'Save to D:\\Shared\\Projects' -- amber when it cannot be reached now (projects then stay on this computer
        and move there later)."""
        def work():
            root = _save_root(self.settings)
            ok = None
            try:
                from ..core import publish
                ok = publish.reachable(root, 4.0)
            except Exception:  # noqa: BLE001 - the module may be missing on an old checkout
                ok = None
            return str(root), ok

        def got(r) -> None:
            root, ok = r
            if ok is False:
                self.strip_save.setText(f"Save to {root} -- not reachable, saving here")
                self.strip_save.setStyleSheet(f"color: {theme.AMBER};")
            else:
                self.strip_save.setText(f"Save to {root}")
                self.strip_save.setStyleSheet(f"color: {theme.DIM};")
        BG.call(work, got)

    # -- for the tools --
    def select(self, key: str) -> None:
        """Show the page of that tool (its key: match_cuts, learn, board, download, convert, template, history)."""
        for i, a in enumerate(self.areas):
            if key in a.keys:
                old = self.tabbar.index
                self.tabbar.select(i)
                if i != old:
                    self.stack.glide_to(i, i > old)
                a.show_key(key)
                return

    def current(self) -> str:
        return self.areas[self.stack.currentIndex()].current_key()

    def set_strip_queue(self, n: int, running: bool) -> None:
        self._queue = (n, running)

    def toast(self, text: str, kind: str = "ok") -> None:
        self.toast_w.show_text(text, kind)

    def notify(self, title: str, text: str) -> None:
        """A Windows notification (the tray's balloon: a toast on Windows 10 / 11)."""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.showMessage(title, text, self.windowIcon(), 15000)
        if not self.isActiveWindow():
            QApplication.alert(self, 0)

    def set_progress(self, share: float | None, state: str = "normal") -> None:
        """The taskbar button's progress: ``share`` 0..1, None = none."""
        tb = self._taskbar_obj()
        if tb is None:
            return
        try:
            if share is None:
                tb.clear()
            else:
                tb.state(state)
                tb.set(share)
        except Exception:  # noqa: BLE001 - the taskbar is a nicety
            pass

    def _taskbar_obj(self):
        if self._taskbar is None:
            try:
                from ..core import taskbar
                self._taskbar = taskbar.TaskbarProgress(int(self.winId()))
            except Exception:  # noqa: BLE001
                self._taskbar = False
        return self._taskbar or None

    # -- quotes (quotes.txt in the shared folder; every call in the background: the file may be on the network) --
    def _quote_book(self):
        """The quote book (made in a worker thread: it touches the shared folder); None until the hub is known."""
        if self.hub is None:
            return None
        if self._qb is None:
            from ..core import quotes
            qpath = quotes.ensure_quotes_file(self.hub)
            self._qb = quotes.QuoteBook(qpath, _app_data() / "quote_state.json")
        return self._qb

    def _ask_quote(self, how: str, goal: bool = False) -> None:
        from datetime import datetime

        def work():
            qb = self._quote_book()
            if qb is None:
                return None
            now = datetime.now()
            return qb.goal(now) if goal else (qb.next(now) if how == "next" else qb.current(now))

        def got(q) -> None:
            if q is None:
                return
            text = q.text
            g = int(getattr(self.settings, "daily_goal", 6) or 6)
            if goal and g != 6:                       # "That's 6. ..." is written for the default goal
                import re
                text = re.sub(r"\b6\b", str(g), text)
            self.quote.show_quote("Goal hit" if goal else q.part, text, q.author or "")
        BG.call(work, got, lambda _e: None)

    def refresh_quote(self, goal: bool | None = None) -> None:
        mc = self.pages.get("match_cuts")
        hit = goal if goal is not None else bool(getattr(mc, "goal_hit", lambda: False)())
        self._ask_quote("current", goal=hit)

    def next_quote(self) -> None:
        self._ask_quote("next")

    def set_badge(self, key: str, text: str) -> None:
        """A small red count on a sub-tab (Learn: new fixes found)."""
        for a in self.areas:
            if key in a.keys and a.sub is not None:
                a.sub.tabs[a.keys.index(key)].set_badge(text)

    # -- intro, settings --
    def play_intro(self) -> None:
        self.intro.play()

    def open_settings(self) -> None:
        from .settings_dialog import SettingsDialog
        d = SettingsDialog(self, self.settings)
        if d.exec():
            self.settings = d.result_settings
            M.set_enabled(bool(getattr(self.settings, "animations", True)))
            self.clock.restart()
            self.refresh_save_place()
            self.find_hub()
            for p in self.pages.values():
                if hasattr(p, "settings_changed"):
                    p.settings_changed(self.settings)
            self.toast("Settings saved ✓")

    def showEvent(self, e) -> None:                       # noqa: N802
        W.dark_title_bar(self)
        super().showEvent(e)
        if not getattr(self, "_shown", False):
            self._shown = True
            if bool(getattr(self.settings, "intro", True)):
                QTimer.singleShot(0, self.play_intro)

    def resizeEvent(self, e) -> None:                     # noqa: N802
        super().resizeEvent(e)
        if self.intro.isVisible():
            self.intro.setGeometry(self.centralWidget().rect())
        self.toast_w._place()                              # noqa: SLF001 - its own placement

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
        for p in self.pages.values():
            if hasattr(p, "closing"):
                try:
                    p.closing()
                except Exception:  # noqa: BLE001
                    pass
        self.set_progress(None)
        self.tray.hide()
        super().closeEvent(e)


# ---------------------------------------------------------------------------------------------------------------------
# settings and the hub folder (core/settings.py; tolerant while it is missing)
# ---------------------------------------------------------------------------------------------------------------------

def _load_settings():
    try:
        from ..core import settings
        return settings.load()
    except Exception:  # noqa: BLE001
        from types import SimpleNamespace
        return SimpleNamespace(animations=True, intro=True, daily_goal=6, monthly_goal=150, save_to="",
                               machine="auto", upload_check_minutes=30)


def _save_root(s):
    try:
        from ..core import settings
        return settings.save_root(s)
    except Exception:  # noqa: BLE001
        return paths.projects_root()


def _hub(s):
    """The folder of the shared files (learn_queue.md, requests.md, standing_rules.md, quotes.txt): the save folder
    when it can be reached, else this computer's projects folder."""
    root = _save_root(s)
    try:
        from ..core import publish
        if publish.reachable(root, 3.0):
            root.mkdir(parents=True, exist_ok=True)
            return root
    except Exception:  # noqa: BLE001
        pass
    local = paths.projects_root()
    local.mkdir(parents=True, exist_ok=True)
    return local


def _app_data():
    """This computer's small state files (the quote picked for this part of the day ...): %APPDATA%\\MovieRecaps
    (MOVIERECAPS_APPDATA moves it: the tests)."""
    import os
    from pathlib import Path
    env = os.environ.get("MOVIERECAPS_APPDATA", "").strip()
    d = Path(env) if env else Path(os.environ.get("APPDATA") or Path.home()) / "MovieRecaps"
    d.mkdir(parents=True, exist_ok=True)
    return d
