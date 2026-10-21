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

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QScrollArea, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import paths
from . import background as BG
from . import chrome
from . import health
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
        self.version_line = health.VersionLine()            # day 13: the programs differ from the project's
        self.version_line.fix.connect(self.fix_versions)
        self.version_line.open_log.connect(self.open_versions_log)
        root.addWidget(self.version_line)
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
        QTimer.singleShot(1500, self.restore_torrents)    # day 12: unfinished torrent downloads go on, on their rows
        # day 13: the free space in the strip (every 2 minutes, after each run / download / conversion) and the
        # versions check once at the start -- both in the background
        self._space_reading = self._space_again = False
        self._was_busy = False
        self._asking_cleanup = False
        self._fixing = False
        self._fix_log = None                               # %APPDATA%\MovieRecaps\setup_app.log (made by Fix now)
        self._space_timer = QTimer(self)
        self._space_timer.timeout.connect(self.refresh_space)
        self._space_timer.start(_space_mod().REFRESH_MS)
        self._busy_timer = QTimer(self)
        self._busy_timer.timeout.connect(self._watch_busy)
        self._busy_timer.start(3000)
        self._soon = QTimer(self)                          # "a run ended": the free space read again, once
        self._soon.setSingleShot(True)
        self._soon.timeout.connect(self.refresh_space)
        self._soon.start(500)
        self._ver_timer = QTimer(self)
        self._ver_timer.setSingleShot(True)
        self._ver_timer.timeout.connect(self.check_versions)
        self._line_close = QTimer(self)                    # "Fixed ✓" stays 5 s, then the line glides closed
        self._line_close.setSingleShot(True)
        self._line_close.timeout.connect(self._close_fixed_line)
        if _start_checks():
            self._ver_timer.start(4000)
        self._rename_timer = QTimer(self)                 # day 13: renames after a title change that had to wait
        self._rename_timer.timeout.connect(self.retry_renames)
        self._rename_timer.start(60_000)
        QTimer.singleShot(12_000, self.retry_renames)
        if _start_checks():                               # night 15: folders named with emoji (made before)
            QTimer.singleShot(20_000, self.plain_names)   # get their plain names, once
        self._publishing: dict[str, int] = {}             # folders being copied to the shared folder right now
        try:                                              # (a rename waits for the whole copy)
            from ..core import renames
            renames.register_busy(self.uses_folder)
        except Exception:  # noqa: BLE001
            pass

    def restore_torrents(self) -> None:
        """Once at the start: the Download page gives every unfinished torrent download its row back (nothing -- not
        even aria2c -- is started when there is none)."""
        dl = self.pages.get("download")
        if dl is not None and hasattr(dl, "restore_torrents"):
            try:
                dl.restore_torrents()
            except Exception:  # noqa: BLE001 - never a crash at the start
                pass

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
            with self.publishing(folder):
                return publish.publish_or_queue(folder, root, local)

        def done(r) -> None:
            res, note = r
            if res is None:
                self.toast(note or f"Saved on this computer: {root} can't be reached -- it moves there when it's back",
                           "note")
                self.refresh_save_place()
            else:
                self.toast(f"Saved to {root} ✓")
                self.synced(folder)                          # day 13: both copies now share one set of titles
            b = self.pages.get("board")
            if b is not None:
                b.reload()

        def failed(err: str) -> None:
            kind, _, msg = str(err).partition(": ")
            if not (msg and kind.isidentifier()):            # plain words only: no "SomeError:" in front
                kind, msg = "", str(err)
            if kind == "NothingToPublish":                   # no finished edit (yet): nothing to try again
                self.toast(f"Not saved to the shared folder: {msg}", "note")
                return
            try:
                from ..core import publish
                publish.queue_pending(local, folder, root, msg)
            except Exception:  # noqa: BLE001
                pass
            self.toast(f"Not saved to the shared folder yet ({msg}) -- tried again later", "note")
        BG.call(work, done, failed, work=True)

    def retry_pending(self) -> None:
        """Projects kept here while the shared folder was away: moved there now when it is back."""
        root = _save_root(self.settings)
        local = paths.projects_root()

        def work():
            from ..core import projects as PJ
            from ..core import publish
            if not publish.reachable(root, 5.0):
                return None
            done = []
            waiting = publish.pending(local)
            if waiting:                               # work ("Turn off the PC" waits) only while it publishes
                with BG.counted_work():
                    with self.publishing(*[x["folder"] for x in waiting]):     # (day 13: no rename meanwhile)
                        done = list(publish.retry_pending(local, root))
            for p in PJ.list_projects(local):         # finished before (or on another night) but never published
                edit = (p.data.get("steps") or {}).get("edit") or {}
                if edit.get("state") == PJ.DONE and not p.data.get("published"):
                    try:
                        with BG.counted_work():
                            with self.publishing(p.folder):
                                res, _note = publish.publish_or_queue(p.folder, root, local)
                    except Exception:  # noqa: BLE001 - the next pass tries again
                        continue
                    if res is not None:
                        done.append(res)
            return done

        def done(res) -> None:
            if res:
                self.toast(f"{len(res)} project(s) moved to {root} ✓")
                for r in res:                                # day 13: both copies share one set of titles
                    if getattr(r, "dest", None) is not None:
                        self.synced(r.dest)
                self.refresh_save_place()
                b = self.pages.get("board")
                if b is not None:
                    b.reload()
        BG.call(work, done, lambda _e: None)          # counted as work only while it publishes (counted_work)

    # -- day 13: a project renamed after its title (core.renames) --
    def title_changed(self, folder, explicit: bool = False, then=None) -> None:
        """A title or a headline was saved (``explicit``: Rename to title was pressed): the project's folder and
        files follow it -- in the background; the pages hear of every folder that moved; a rename that has to wait
        (an edit running, Premiere using it ...) is tried again every minute."""
        if not folder:
            return
        hub, local = _save_root(self.settings), paths.projects_root()

        def work():
            from ..core import renames as RN
            return RN.request(folder, hub=hub, local_root=local, explicit=explicit)

        def done(res) -> None:
            self.renamed(res, explicit)
            if then is not None:
                then(res)

        def failed(err: str) -> None:
            if explicit:
                self.toast(f"It could not be renamed: {err}", "fail")
        BG.call(work, done=done, failed=failed, work=True)   # ("Turn off the PC" waits for a rename half-way)

    def publishing(self, *folders):
        """``with self.publishing(folder): ...`` -- these folders are being copied to the shared folder (a rename
        waits for the whole copy: core.renames asks ``uses_folder``)."""
        import contextlib
        import os

        @contextlib.contextmanager
        def mark():
            keys = [os.path.normcase(os.path.abspath(str(f))) for f in folders if f]
            for k in keys:
                self._publishing[k] = self._publishing.get(k, 0) + 1     # (two copies of one folder: counted)
            try:
                yield
            finally:
                for k in keys:
                    left = self._publishing.get(k, 1) - 1
                    if left > 0:
                        self._publishing[k] = left
                    else:
                        self._publishing.pop(k, None)
        return mark()

    def uses_folder(self, folder) -> str:
        """Why a project folder must not be renamed now (core.renames.BUSY): it is being copied to the shared folder."""
        import os
        k = os.path.normcase(os.path.abspath(str(folder)))
        return "it is being copied to the shared folder" if getattr(self, "_publishing", {}).get(k) else ""

    def synced(self, folder) -> None:
        """Day 13: a project was just copied to the shared folder -- its two copies take one set of titles (as
        publishing merged them) and remember it as the base a later title change is seen from (core.renames); its
        names follow the title when they can (its edit is not in Premiere yet). Quiet unless it renames something."""
        if folder is not None and callable(getattr(self, "title_changed", None)):
            self.title_changed(folder)

    def renamed(self, res, said: bool = False) -> None:
        """What a rename did: the pages follow every folder that moved; a toast says it (a rename that waits or keeps
        the name says why; "nothing to do" only when you pressed Rename to title)."""
        for old, new in list(getattr(res, "moved", None) or []):
            self.project_moved(old, new)
        state = getattr(res, "state", "")
        text = str(getattr(res, "text", "") or "")
        if state == "renamed":
            self.toast(f"Renamed after its title ✓  {Path(res.folder).name}")
        elif state in ("wait", "kept"):
            self.toast(text[:220], "note")
        elif said and text:
            self.toast(text[:220], "info")
        for p in self.pages.values():
            fn = getattr(p, "titles_changed", None)
            if callable(fn):
                try:
                    fn(Path(res.folder))
                except Exception:  # noqa: BLE001 - a page that cannot follow shows it at its next refresh
                    pass
        b = self.pages.get("board")
        if b is not None and hasattr(b, "reload"):
            b.reload()

    def project_moved(self, old, new) -> None:
        """A project folder was renamed: every page's rows point at the new place."""
        for p in self.pages.values():
            fn = getattr(p, "moved", None)
            if callable(fn):
                try:
                    fn(Path(old), Path(new))
                except Exception:  # noqa: BLE001
                    pass

    def retry_renames(self) -> None:
        """The renames that waited, tried again (only when there are any)."""
        hub, local = _save_root(self.settings), paths.projects_root()

        def work():
            from ..core import renames as RN
            if not RN.pending():
                return []
            with BG.counted_work() as _renaming:          # (work only while there are renames to try)
                return RN.retry_pending(hub=hub, local_root=local)

        def done(results) -> None:
            for res in results or []:
                if getattr(res, "state", "") != "wait":
                    self.renamed(res)
                elif getattr(res, "moved", None):         # half done (the shared copy moved): the pages follow,
                    for old, new in res.moved:            # quietly (it says so when the rest is done)
                        self.project_moved(old, new)
                    b = self.pages.get("board")
                    if b is not None and hasattr(b, "reload"):
                        b.reload()
        BG.call(work, done, lambda _e: None)

    def plain_names(self) -> None:
        """Night 15: the projects whose folder still has emoji in its name (Premiere's XML import fails on them)
        renamed to their plain names once, in the background (core.renames.plain_sweep: a folder a Premiere project
        plays keeps its name; one in use waits)."""
        hub, local = _save_root(self.settings), paths.projects_root()

        def work():
            from ..core import renames as RN
            with BG.counted_work() as _renaming:
                return RN.plain_sweep(hub=hub, local_root=local)

        def done(results) -> None:
            for res in results or []:
                if getattr(res, "moved", None) or getattr(res, "state", "") == "renamed":
                    self.renamed(res)
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
        sl.setSpacing(12)                                  # (24 between items: 12 + a stretch + 12)
        self.strip_gpu = health.strip_label("GPU …")       # day 13: shortened with "…" in a narrow window
        self.strip_prem = health.strip_label("Premiere …")
        self.strip_save = health.strip_label("Save to …")
        self.clock = chrome.Timecode()
        self.replay = chrome.LinkButton("replay intro")
        self.replay.clicked.connect(self.play_intro)
        self.settings_btn = chrome.LinkButton("settings")
        self.settings_btn.clicked.connect(self.open_settings)
        self.strip_space = health.SpaceStrip(self._space_folders())   # day 13: "Free C: 64.2 GB · D: 2.6 TB"
        self.strip_space.clicked.connect(self.clean_up_now)
        for w in (self.strip_gpu, self.strip_prem, self.strip_save, self.strip_space):
            sl.addWidget(w)
            sl.addStretch(1)
        sl.addWidget(self.clock)
        sl.addSpacing(12)
        sl.addWidget(self.replay)
        sl.addSpacing(12)
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
        _preload()                    # (day 13) before any page starts background work: see _preload
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
            ok, why = None, ""
            try:
                from ..core import publish
                ok = publish.reachable(root, 4.0)
                if not ok:
                    why = publish.why_unreachable(root, 4.0) or ""
            except Exception:  # noqa: BLE001 - the module may be missing on an old checkout
                ok = None
            return str(root), ok, why

        def got(r) -> None:
            root, ok, why = r
            if ok is False:
                self.strip_save.setText(f"Save to {root} -- not reachable, saving here")
                self.strip_save.setToolTip(why or "the shared folder cannot be reached now: finished projects stay on "
                                                  "this computer and move there when it is back")
                self.strip_save.setStyleSheet(f"color: {theme.AMBER};")
            else:
                self.strip_save.setText(f"Save to {root}")
                self.strip_save.setStyleSheet(f"color: {theme.DIM};")
        BG.call(work, got)

    # -- day 13: the free space (core.space) --
    def _space_folders(self) -> list:
        try:
            return _space_mod().strip_folders(_save_root(self.settings), paths.projects_root())
        except Exception:  # noqa: BLE001 - never a crash in the strip
            return [("C:", "C:\\")]

    def refresh_space(self) -> None:
        """The strip's free space, read in the background (a network folder that does not answer gives up after 8
        s); one read at a time -- a call while one runs reads again right after it."""
        if self._space_reading:
            self._space_again = True
            return
        self._space_reading = True
        folders = list(self.strip_space.folders)

        def got(drives) -> None:
            self._space_reading = False
            self.strip_space.set_drives(drives)
            if self._space_again:
                self._space_again = False
                self._soon.start(0)

        def bad(_e) -> None:
            self._space_reading = False
        BG.call(lambda: _space_mod().read_strip(folders), got, bad)

    def space_changed(self) -> None:
        """A run, a download or a conversion ended (the pages call it): the free space read again in a moment."""
        self._soon.start(1500)

    def _watch_busy(self) -> None:
        """Every 3 s (no disk, no network: the pages' own flags): when the last run / download / conversion ends,
        the free space is read again."""
        busy = any(getattr(p, "busy", lambda: False)() for p in self.pages.values())
        if self._was_busy and not busy:
            self.space_changed()
        self._was_busy = busy

    def offer_cleanup(self, text: str) -> None:
        """A page did not start something for lack of room: the numbers, and Clean up (the Board's)."""
        if self._asking_cleanup:
            return
        self._asking_cleanup = True
        try:
            yes = health.ask_cleanup(self, text)
        finally:
            self._asking_cleanup = False
        self.space_changed()
        if yes:
            self.clean_up_now()

    def clean_up_now(self) -> None:
        """The Board's Clean up (it looks first and asks before it removes anything)."""
        self.select("board")
        b = self.pages.get("board")
        if b is not None and hasattr(b, "clean_up"):
            b.clean_up()

    # -- day 13: the versions this computer has (core.versions) --
    def check_versions(self) -> None:
        """Once at the start, in the background: the check runs in a process of its own (the venv's Python) -- a
        broken package can never touch the window; anything not as the project expects -> the amber line."""
        def work():
            from ..core import versions as V
            return V.check_in_process(paths.python_exe())

        def got(probs) -> None:
            if self._fixing:
                return
            if probs:
                self.version_line.show_problems(probs)
            else:
                self.version_line.close_line()
        BG.call(work, got, lambda _e: None)

    def fix_versions(self) -> None:
        """Fix now: setup_app.py --from-app in the background (its output into setup_app.log), then the check again:
        "Fixed ✓" or the plain reason (Open log). Not while a video is made, downloaded or converted (pip would
        replace files a run is using)."""
        if self._fixing:
            return
        busy = [p for p in self.pages.values() if getattr(p, "busy", lambda: False)()]
        if busy or BG.working():
            self.version_line.show_failed("Fix now waits: a video is being made, downloaded or converted -- press "
                                          "Try again when they are done")
            return
        self._fixing = True
        self.version_line.show_fixing()
        sig = _Said()
        sig.text.connect(lambda t: self.version_line.show_fixing(t) if self._fixing else None)

        def fix():
            from ..core import versions as V
            log = self._fix_log = _app_data() / "setup_app.log"
            code, why = V.run_setup(paths.python_exe(), log, sig.text.emit)
            return code, why, V.check_in_process(paths.python_exe())

        def fixed(r) -> None:
            self._fixing = False
            _sig_keep.discard(sig)
            _code, why, probs = r
            if not probs:
                self.version_line.show_fixed()
                self.toast("Fixed ✓ -- this computer has what the project expects")
                self._line_close.start(5000)
                return
            from ..core import versions as V
            self.version_line.problems = list(probs)
            hand = next((p.hand for p in probs if p.hand), "")
            closed = any(p.closed for p in probs)
            tail = (" -- close MovieRecaps, then run: .venv\\Scripts\\python tools\\app\\setup_app.py" if closed
                    else f" -- {hand}" if hand else (f" ({why})" if why else ""))
            self.version_line.show_failed("Not everything could be fixed: " + V.summary(probs, 2) + tail)

        def broke(e: str) -> None:
            self._fixing = False
            _sig_keep.discard(sig)
            self.version_line.show_failed(f"Fix now could not run ({e}) -- see the log")
        _sig_keep.add(sig)
        BG.call(fix, fixed, broke, work=True)            # work: "Turn off the PC" waits for pip

    def _close_fixed_line(self) -> None:
        if self.version_line.state == health.FIXED:
            self.version_line.close_line()

    def open_versions_log(self) -> None:
        """Fix now's log in Notepad (opened in the background: starting a program can take a moment)."""
        log = self._fix_log

        def work():
            p = log or (_app_data() / "setup_app.log")
            if not p.is_file():
                return False
            os.startfile(str(p))                      # noqa: S606 - the user's own log, in their editor
            return True
        BG.call(work, lambda ok: None if ok else self.toast("No log yet: press Fix now first", "note"),
                lambda _e: None)

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
            self.strip_space.set_folders(self._space_folders())        # another save folder: maybe another drive
            self.refresh_space()
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


class _Said(QObject):
    """A line said from a worker thread (Fix now's "installing ...") -- a queued signal: shown in the window's
    thread."""
    text = Signal(str)


_sig_keep: set = set()


def _space_mod():
    from ..core import space
    return space


def _preload() -> None:
    """Import -- on the window's thread, before the pages are built -- the modules one page imports in the background
    while another page imports them on this thread: the Download page reads the channels' names in a worker
    (template_editor -> core.channels -> core.headline, a slow import) while the Template page imports the same
    modules here. Day 13: a test froze with the two threads waiting for each other's import locks (Python 3.14),
    with the window built -- the same race exists at every start of the app. The Template page imports them on this
    thread anyway, so this costs nothing extra."""
    try:
        from ..core import channels  # noqa: F401
        from . import template_editor  # noqa: F401
    except Exception:  # noqa: BLE001 - a missing optional package: the pages say so themselves
        pass


def _start_checks() -> bool:
    """The version check at the start -- not inside the tests (they call it with fake readers)."""
    import os
    import sys
    return "pytest" not in sys.modules and not os.environ.get("MOVIERECAPS_NO_START_CHECKS")


def _app_data():
    """This computer's small state files (the quote picked for this part of the day ...): %APPDATA%\\MovieRecaps
    (MOVIERECAPS_APPDATA moves it: the tests)."""
    import os
    from pathlib import Path
    env = os.environ.get("MOVIERECAPS_APPDATA", "").strip()
    d = Path(env) if env else Path(os.environ.get("APPDATA") or Path.home()) / "MovieRecaps"
    d.mkdir(parents=True, exist_ok=True)
    return d
