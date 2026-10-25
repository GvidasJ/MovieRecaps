"""Settings (night 11): where finished projects are saved ("Save projects to": PC D:\\Shared\\Projects, laptop
\\\\192.168.101.11\\Shared\\Projects by default), which computer this is, animations on/off, the intro, the daily and
monthly goals, how often the Shorts are checked. Saved per computer (core/settings.py)."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QSpinBox, QVBoxLayout)

from . import background as BG
from . import theme
from . import widgets as W


class SettingsDialog(QDialog):
    def __init__(self, win, settings):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Settings")
        self.resize(760, 560)
        self.s = copy.deepcopy(settings)
        self.result_settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(14)
        lay.addWidget(W.SectionHeader("Settings"))
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(14)
        r = 0
        # where finished projects go
        g.addWidget(W.label("Save projects to", "Small"), r, 0, Qt.AlignmentFlag.AlignTop)
        box = QVBoxLayout()
        row = QHBoxLayout()
        self.save = QLineEdit(str(getattr(self.s, "save_to", "") or ""))
        self.save.setPlaceholderText(str(self._default_root()))
        self.save.textChanged.connect(lambda _t: self._check_root())
        row.addWidget(self.save, 1)
        pick = W.Button("Browse…", "Small")
        pick.clicked.connect(self._browse)
        row.addWidget(pick)
        dflt = W.Button("This computer's default", "Small")
        dflt.clicked.connect(lambda: self.save.setText(""))
        row.addWidget(dflt)
        box.addLayout(row)
        self.root_note = W.label("", "Dim", wrap=True)
        box.addWidget(self.root_note)
        box.addWidget(W.label("Only finished projects go there (the edit, template.png, project.json, logs, "
                              "title.txt); downloads, conversions and caches stay on this computer. When the folder "
                              "cannot be reached, a project is kept here and moved there when it is back.", "Dim",
                              wrap=True))
        g.addLayout(box, r, 1)
        r += 1
        # which computer
        g.addWidget(W.label("This computer", "Small"), r, 0)
        mrow = QHBoxLayout()
        self.machine = W.Segmented([("auto", "Auto"), ("pc", "PC"), ("laptop", "Laptop")],
                                   str(getattr(self.s, "machine", "auto") or "auto"))
        self.machine.changed.connect(lambda _v: self._machine_changed())
        mrow.addWidget(self.machine)
        mrow.addSpacing(12)
        self.machine_note = W.label("", "Dim")
        mrow.addWidget(self.machine_note, 1)
        g.addLayout(mrow, r, 1)
        r += 1
        # torrents (day 12)
        g.addWidget(W.label("Save torrents to", "Small"), r, 0, Qt.AlignmentFlag.AlignTop)
        tbox = QVBoxLayout()
        trow = QHBoxLayout()
        self.tdir = QLineEdit(str(getattr(self.s, "torrent_dir", "") or ""))
        self.tdir.setPlaceholderText(str(self._default_tdir()))
        trow.addWidget(self.tdir, 1)
        tpick = W.Button("Browse…", "Small")
        tpick.clicked.connect(self._browse_tdir)
        trow.addWidget(tpick)
        tdflt = W.Button("This computer's default", "Small")
        tdflt.clicked.connect(lambda: (self.tdir.setText(""), self._check_tdir()))
        trow.addWidget(tdflt)
        tbox.addLayout(trow)
        self.tdir_note = W.label("", "Dim", wrap=True)     # day 17: "reachable" (and writable), as for projects
        tbox.addWidget(self.tdir_note)
        self.tdir.editingFinished.connect(self._tidy_tdir)
        srow = QHBoxLayout()
        srow.addWidget(W.label("Keep sharing after a download", "Dim"))
        self.share = W.Segmented([("on", "On"), ("off", "Off")],
                                 "on" if getattr(self.s, "torrent_keep_sharing", False) else "off")
        srow.addWidget(self.share)
        srow.addWidget(W.label("Off: sharing stops as soon as the download is finished.", "Dim"), 1)
        tbox.addLayout(srow)
        g.addLayout(tbox, r, 1)
        r += 1
        # motion
        g.addWidget(W.label("Animations", "Small"), r, 0)
        arow = QHBoxLayout()
        self.anim = W.Segmented([("on", "On"), ("off", "Off")], "on" if getattr(self.s, "animations", True) else "off")
        arow.addWidget(self.anim)
        arow.addSpacing(18)
        arow.addWidget(W.label("Intro at the start", "Small"))
        self.intro = W.Segmented([("on", "On"), ("off", "Off")], "on" if getattr(self.s, "intro", True) else "off")
        arow.addWidget(self.intro)
        replay = W.Button("Replay intro", "Small")
        replay.clicked.connect(self._replay)
        arow.addWidget(replay)
        arow.addStretch(1)
        g.addLayout(arow, r, 1)
        r += 1
        # run 18: the look -- Mono + red (new), or the classic one as before
        g.addWidget(W.label("Classic look", "Small"), r, 0)
        lrow = QHBoxLayout()
        self.look = W.Segmented([("on", "On"), ("off", "Off")],
                                "on" if getattr(self.s, "look", "mono") == "classic" else "off")
        lrow.addWidget(self.look)
        lrow.addWidget(W.label("On: the look before run 18 (Off: Mono + red) -- it changes when the app opens again",
                               "Dim"))
        lrow.addStretch(1)
        g.addLayout(lrow, r, 1)
        r += 1
        # goals
        g.addWidget(W.label("Goals", "Small"), r, 0)
        grow = QHBoxLayout()
        self.daily = QSpinBox()
        self.daily.setRange(1, 50)
        self.daily.setValue(int(getattr(self.s, "daily_goal", 6) or 6))
        self.monthly = QSpinBox()
        self.monthly.setRange(1, 2000)
        self.monthly.setValue(int(getattr(self.s, "monthly_goal", 150) or 150))
        grow.addWidget(W.label("Shorts a day", "Dim"))
        grow.addWidget(self.daily)
        grow.addSpacing(18)
        grow.addWidget(W.label("Shorts a month", "Dim"))
        grow.addWidget(self.monthly)
        grow.addStretch(1)
        g.addLayout(grow, r, 1)
        r += 1
        g.addWidget(W.label("Uploaded check", "Small"), r, 0)
        urow = QHBoxLayout()
        self.every = QSpinBox()
        self.every.setRange(5, 600)
        self.every.setValue(int(getattr(self.s, "upload_check_minutes", 30) or 30))
        urow.addWidget(W.label("read the three Shorts tabs every", "Dim"))
        urow.addWidget(self.every)
        urow.addWidget(W.label("minutes (and at the start)", "Dim"))
        urow.addStretch(1)
        g.addLayout(urow, r, 1)
        lay.addLayout(g)
        lay.addStretch(1)
        self.where = W.label("", "Dim", wrap=True, select=True)
        lay.addWidget(self.where)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = W.Button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = W.Button("Save", "Primary")
        ok.clicked.connect(self._ok)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self._machine_changed()
        try:
            from ..core import settings as S
            self.where.setText(f"These settings are this computer's own: {S.path()}")
        except Exception:  # noqa: BLE001
            self.where.setText("")

    def _default_root(self):
        try:
            from ..core import machine
            kind = self.machine.value if hasattr(self, "machine") and self.machine.value != "auto" else None
            return machine.default_save_root(kind or machine.which(self.s))
        except Exception:  # noqa: BLE001
            from ..core import paths
            return paths.projects_root()

    def _default_tdir(self):
        try:
            from ..core import machine, torrent
            kind = self.machine.value if hasattr(self, "machine") and self.machine.value != "auto" else None
            s = copy.copy(self.s)
            s.torrent_dir = ""
            return torrent.default_dir(kind or machine.which(self.s), settings=s)
        except Exception:  # noqa: BLE001
            return ""

    def _browse_tdir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save torrents to",
                                             self.tdir.text().strip() or str(self._default_tdir()))
        if d:
            self.tdir.setText(d)
            self._tidy_tdir()

    def _tidy_tdir(self) -> None:
        """Day 17: "/" made "\\" by itself, then the folder checked: reachable and writable (in the background)."""
        from ..core import settings as S
        t = S.normalize_folder(self.tdir.text())
        if t != self.tdir.text():
            self.tdir.setText(t)
        self._check_tdir()

    def _tdir(self):
        from pathlib import Path
        t = self.tdir.text().strip().strip('"')
        return Path(t) if t else Path(str(self._default_tdir()))

    def _check_tdir(self) -> None:
        folder = self._tdir()
        self.tdir_note.setText(f"checking {folder} …")
        self.tdir_note.setStyleSheet(f"color: {theme.DIM};")

        def work():
            from ..core import publish, torrent
            if not publish.reachable(folder, 4.0):
                return "away"
            try:
                folder.mkdir(parents=True, exist_ok=True)
                probe = folder / ".movierecaps_write_test.tmp"
                probe.write_bytes(b"x")
                probe.unlink()
            except OSError:
                return "readonly"
            return "network" if torrent.is_network(folder) else "ok"

        def got(how) -> None:
            if str(self._tdir()) != str(folder):
                return
            text, col = {
                "ok": (f"{folder} -- reachable and can be written", theme.GREEN),
                "network": (f"{folder} -- reachable and can be written (a network folder: a file whose path would "
                            "pass Windows' 260 characters is saved under a shorter name)", theme.GREEN),
                "readonly": (f"{folder} -- reachable, but it can't be written (no permission?): downloads would "
                             "stop", theme.AMBER),
                "away": (f"{folder} -- not reachable now: a torrent can't start until it is back (or choose "
                         "another folder)", theme.AMBER)}.get(how, (f"{folder}", theme.DIM))
            self.tdir_note.setText(text)
            self.tdir_note.setStyleSheet(f"color: {col};")
        BG.call(work, got, lambda e: self.tdir_note.setText(f"could not check it: {e}"))

    def _machine_changed(self) -> None:
        self.s.machine = self.machine.value
        try:
            from ..core import machine
            self.machine_note.setText(machine.describe(self.s))
        except Exception:  # noqa: BLE001
            self.machine_note.setText("")
        self.save.setPlaceholderText(str(self._default_root()))
        if hasattr(self, "tdir"):
            self.tdir.setPlaceholderText(str(self._default_tdir()))
        self._check_root()
        if hasattr(self, "tdir_note"):
            self._check_tdir()

    def _root(self):
        from pathlib import Path
        t = self.save.text().strip().strip('"')
        return Path(t) if t else self._default_root()

    def _check_root(self) -> None:
        root = self._root()
        self.root_note.setText(f"checking {root} …")
        self.root_note.setStyleSheet(f"color: {theme.DIM};")

        def work():
            from ..core import publish
            return publish.reachable(root, 4.0)

        def got(ok) -> None:
            if str(self._root()) != str(root):
                return
            if ok:
                self.root_note.setText(f"{root} -- reachable")
                self.root_note.setStyleSheet(f"color: {theme.GREEN};")
            else:
                self.root_note.setText(f"{root} -- not reachable now: finished projects stay on this computer and "
                                       "move there when it is back")
                self.root_note.setStyleSheet(f"color: {theme.AMBER};")
        BG.call(work, got, lambda e: self.root_note.setText(f"could not check it: {e}"))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save projects to", str(self._root()))
        if d:
            self.save.setText(d)

    def _replay(self) -> None:
        try:
            self.win.play_intro()
        except Exception:  # noqa: BLE001
            pass

    def _ok(self) -> None:
        s = self.s
        from ..core import settings as S0
        s.save_to = S0.normalize_folder(self.save.text())         # day 17: "/" -> "\" by itself
        s.machine = self.machine.value
        s.animations = self.anim.value == "on"
        s.intro = self.intro.value == "on"
        s.daily_goal = int(self.daily.value())
        s.monthly_goal = int(self.monthly.value())
        s.upload_check_minutes = int(self.every.value())
        s.torrent_dir = S0.normalize_folder(self.tdir.text())
        s.torrent_keep_sharing = self.share.value == "on"
        look = "classic" if self.look.value == "on" else "mono"
        changed_look = look != getattr(s, "look", "mono")
        s.look = look
        try:
            from ..core import settings as S
            S.save(s)
        except Exception as e:  # noqa: BLE001
            self.where.setText(f"could not save the settings: {e}")
            self.where.setStyleSheet(f"color: {theme.RED};")
            return
        self.result_settings = s
        self.look_changed = changed_look              # (the window says: open the app again to see it)
        self.accept()

    def showEvent(self, e) -> None:                       # noqa: N802
        W.dark_title_bar(self)
        super().showEvent(e)
