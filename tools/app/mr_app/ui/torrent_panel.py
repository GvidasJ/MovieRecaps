"""Torrents in the window (day 12, section 6c): the thin Qt layer over core/torrent.py.

* ``TorrentPicker(source, save_dir=None)`` -- the picker card (docs\\ui_preview.html ``.tcard``): it reads the file
  list (a magnet's is fetched from the peers, in the background), then shows "N files · total · seeders · free on D:",
  the files with tick boxes and sizes, the BIGGEST VIDEO ticked, "Save to …", "N GB selected", Start download /
  Cancel. Signals: ``chosen(list of file indexes)`` after Start (the free space was checked), ``cancelled()``,
  ``failed(plain message)``; ``meta`` / ``save_dir`` for the job; ``start_job()`` makes the ``TorrentJob``.
* ``TorrentJob(job_id)`` -- one download, polled once a second in the background. Signals: ``progress(dict)`` (the
  numbers of ``TorrentSession.poll``), ``finished(video path)``, ``failed(plain message)``, ``removed()``.
  ``TorrentJob.begin(meta, indexes, save_dir)`` starts one; ``restore_jobs(callback)`` gives back, after an app
  restart, the downloads that were not finished.
* ``TorrentCard(job)`` -- the progress card (``.tstats``): Seeders / Peers / Speed / Done / Time left, the health bars,
  a gliding bar, Pause/Resume, Remove; a green border when it is done.

Everything slow (aria2c, the network, the disk) runs through ui/background.call; a poll is one local call."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget)

from ..core import torrent as T
from . import background as BG
from . import motion as M
from . import theme
from . import widgets as W

Runner = Callable[[Callable[[], Any], Callable[[Any], None] | None, Callable[[str], None] | None], None]

_session: T.TorrentSession | None = None
_session_lock = threading.Lock()


def session() -> T.TorrentSession:
    """The app's one torrent session (made on first use; call it in the background: it reads the jobs file)."""
    global _session
    with _session_lock:
        if _session is None:
            _session = T.TorrentSession()
        return _session


def set_session(s: T.TorrentSession | None) -> None:
    """The tests' fake session (None: back to the real one, made on next use)."""
    global _session
    with _session_lock:
        _session = s


def shutdown() -> None:
    """At the app's end: aria2c writes its progress files and stops (it would stop with the app anyway)."""
    s = _session
    if s is not None:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


_hooked = False


def _hook_quit() -> None:
    """Once, in the window's thread: when the app quits, aria2c saves its progress files and stops."""
    global _hooked
    if _hooked:
        return
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(shutdown)
        _hooked = True


def _plain(e: str) -> str:
    """A background error as the user reads it: TorrentError's message as it is."""
    return e.split(": ", 1)[1] if e.startswith("TorrentError: ") else e


def _bg(fn, done=None, failed=None) -> None:
    BG.call(fn, done, failed)


# ---------------------------------------------------------------------------------------------------------------------
# small pieces
# ---------------------------------------------------------------------------------------------------------------------

class HealthBars(QWidget):
    """Five thin bars of growing height; ``set(n)`` lights the first n green (more seeders, more bars)."""

    def __init__(self):
        super().__init__()
        self.value = 0
        self.setFixedSize(5 * 4 + 4 * 2, 14)

    def set(self, n: int) -> None:
        n = max(0, min(5, int(n)))
        if n != self.value:
            self.value = n
            self.update()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        for i in range(5):
            h = 4 + i * 2.5
            p.fillRect(QRectF(i * 6, self.height() - h, 4, h), QColor(theme.GREEN if i < self.value else "#2a2a2a"))
        p.end()


class _Stat(QWidget):
    """A small uppercase label over a big Bebas number (the reference's ``.tstats div``)."""

    def __init__(self, title: str, colour: str = theme.TEXT, widget: QWidget | None = None):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        t = QLabel(title.upper())
        t.setFont(W.mono(9.5, 600, 12))
        t.setStyleSheet(f"color: {theme.DIM};")
        lay.addWidget(t)
        if widget is not None:
            self.value = None
            lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        else:
            self.value = QLabel("0")
            self.value.setFont(W.display(22, 3))
            self.value.setStyleSheet(f"color: {colour};")
            lay.addWidget(self.value)

    def set(self, text: str) -> None:
        if self.value is not None and self.value.text() != text:
            self.value.setText(text)


class FileRow(QWidget):
    """One file: a tick box, the name (with the coral BIGGEST VIDEO label), the size. Under the mouse its background
    and border fade in (nothing moves)."""
    toggled = Signal()

    def __init__(self, f: T.TFile, checked: bool, biggest: bool):
        super().__init__()
        self.file = f
        self.hover = M.Fader(self, self.update)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        self.box = QCheckBox()
        self.box.setChecked(checked)
        self.box.toggled.connect(lambda _on: self.toggled.emit())
        lay.addWidget(self.box, 0, Qt.AlignmentFlag.AlignVCenter)
        self.name = QLabel(f.path)
        self.name.setFont(W.font(12.5))
        self.name.setStyleSheet(f"color: {theme.TEXT};")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name.setToolTip(f.path)
        lay.addWidget(self.name, 1)
        self.big = None
        if biggest:
            self.big = QLabel("BIGGEST VIDEO")
            self.big.setFont(W.mono(9.5, 600, 10))
            self.big.setStyleSheet(f"color: {theme.CORAL};")
            lay.addWidget(self.big, 0, Qt.AlignmentFlag.AlignVCenter)
        self.size = W.mono_label(T.human_size(f.size), theme.MUTED)
        lay.addWidget(self.size, 0, Qt.AlignmentFlag.AlignVCenter)

    def checked(self) -> bool:
        return self.box.isChecked()

    def mousePressEvent(self, e) -> None:                 # noqa: N802 - the whole row ticks the box
        if e.button() == Qt.MouseButton.LeftButton:
            self.box.toggle()
        super().mousePressEvent(e)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        h = self.hover.value
        if h <= 0:
            return
        p = QPainter(self)
        W.box(p, QRectF(self.rect()), M.alpha("#181818", h), M.alpha(theme.LINE, h))
        p.end()


def _title(text: str) -> QLabel:
    t = QLabel(text)
    t.setFont(W.font(14, 600))
    t.setStyleSheet(f"color: {theme.TEXT};")
    t.setWordWrap(True)
    return t


def _foot(left: str = "", right: str = "") -> tuple[QHBoxLayout, QLabel, QLabel]:
    row = QHBoxLayout()
    row.setSpacing(10)
    a, b = W.mono_label(left), W.mono_label(right)
    a.setWordWrap(True)
    row.addWidget(a, 1)
    row.addWidget(b, 0, Qt.AlignmentFlag.AlignRight)
    return row, a, b


# ---------------------------------------------------------------------------------------------------------------------
# the picker
# ---------------------------------------------------------------------------------------------------------------------

class TorrentPicker(W.Card):
    """Read a magnet / .torrent, show its files, let the user tick them, then Start (``chosen``) or Cancel."""
    chosen = Signal(list)
    cancelled = Signal()
    failed = Signal(str)
    ready = Signal()                       # the file list is shown

    def __init__(self, source: str | Path, save_dir: str | Path | None = None,
                 sess: T.TorrentSession | None = None, run: Runner | None = None):
        super().__init__(margins=(16, 14, 16, 14), spacing=10)
        self.source = str(source)
        self.save_dir = Path(save_dir) if save_dir else None
        self.meta: T.Meta | None = None
        self.free: int | None = None
        self.rows: list[FileRow] = []
        self._sess = sess
        self._run = run or _bg
        self._cancel = threading.Event()
        self._t0 = time.monotonic()
        _hook_quit()
        self.head = _title("Reading the torrent…")
        self.lay.addWidget(self.head)
        self.info = W.mono_label("")
        self.info.setWordWrap(True)
        self.lay.addWidget(self.info)
        self.bar = W.GlideBar(3)
        self.bar.state("running")
        self.lay.addWidget(self.bar)
        self.files = QVBoxLayout()
        self.files.setSpacing(4)
        self.lay.addLayout(self.files)
        frow, self.save_label, self.sel_label = _foot()
        self.lay.addLayout(frow)
        self.note = W.label("", "Dim", wrap=True)
        self.note.hide()
        self.lay.addWidget(self.note)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.start_btn = W.Button("Start download", "Primary")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn = W.Button("Cancel")
        self.cancel_btn.clicked.connect(self.cancel)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.cancel_btn)
        btns.addStretch(1)
        self.lay.addLayout(btns)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._waiting)
        self._clock.start()
        self._waiting()
        self._load()

    def sess(self) -> T.TorrentSession:
        return self._sess or session()

    # -- reading the file list -----------------------------------------------------------------------------------------

    def _waiting(self) -> None:
        if self.meta is not None:
            self._clock.stop()
            return
        s = int(time.monotonic() - self._t0)
        if T.kind_of(self.source) == "magnet":
            self.info.setText(f"Asking the peers for the file list… {s // 60}:{s % 60:02d}")
        else:
            self.info.setText("Reading the .torrent file…")

    def _load(self) -> None:
        src, cancel = self.source, self._cancel
        save = self.save_dir

        def work():
            sess = self.sess()
            meta = sess.probe(src, cancel=cancel)
            d = save or T.default_dir(settings=sess.settings)
            return meta, d, sess.free(Path(d))
        self._run(work, self._loaded, self._load_failed)

    def _load_failed(self, e: str) -> None:
        if self._cancel.is_set():
            return
        msg = _plain(e)
        self._clock.stop()
        self.head.setText("This torrent could not be read")
        self.info.setText("")
        self.bar.state("fail")
        self.bar.jump(1.0)
        self._say(msg, theme.RED)
        self.failed.emit(msg)

    def _loaded(self, res) -> None:
        if self._cancel.is_set():
            return
        meta, d, free = res
        self.meta, self.save_dir, self.free = meta, Path(d), free
        self._clock.stop()
        self.bar.hide()
        self.head.setText(meta.name)
        big = meta.biggest_video()
        # the biggest video first, then the rest from big to small (the reference's order)
        for f in sorted(meta.files, key=lambda f: (f.index != big, -f.size, f.index)):
            row = FileRow(f, f.index == big, f.index == big)
            row.toggled.connect(self._update)
            self.files.addWidget(row)
            self.rows.append(row)
        seeds = "seeders unknown" if meta.seeders is None else \
            f"<span style='color:{theme.GREEN}'>{meta.seeders} seeder{'s' if meta.seeders != 1 else ''}</span>"
        free_t = T.human_size(free) if free is not None else "not reachable"
        n = len(meta.files)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        drive = T.drive_name(self.save_dir)
        self.info.setText(f"{n} file{'s' if n != 1 else ''} · {T.human_size(meta.total)} · {seeds} · "
                          f"free on {drive}{'' if drive.endswith(':') else ':'} {free_t}")
        self.save_label.setText(f"Save to {self.save_dir}")
        self._update()
        self.ready.emit()

    # -- choosing ------------------------------------------------------------------------------------------------------

    def indexes(self) -> list[int]:
        return [r.file.index for r in self.rows if r.checked()]

    def selected_size(self) -> int:
        return sum(r.file.size for r in self.rows if r.checked())

    def _update(self) -> None:
        n = self.selected_size()
        self.sel_label.setText(f"{T.human_size(n) if n else 'nothing'} selected")
        ok = bool(self.indexes())
        if ok and self.free is not None and n + T.margin(n) > self.free:
            self._say(f"Not enough free space on {T.drive_name(self.save_dir)}: {T.human_size(n)} chosen, "
                      f"{T.human_size(self.free)} free.", theme.AMBER)
        else:
            self._say("")
        self.start_btn.setEnabled(ok)

    def _say(self, text: str, colour: str = theme.DIM) -> None:
        self.note.setText(text)
        self.note.setStyleSheet(f"color: {colour};")
        self.note.setVisible(bool(text))

    def _start(self) -> None:
        if self.meta is None:
            return
        ix, meta, d = self.indexes(), self.meta, self.save_dir
        self.start_btn.setEnabled(False)

        def work():
            return self.sess().check_space(meta, ix, d)

        def ok(_free) -> None:
            self.chosen.emit(ix)

        def bad(e: str) -> None:
            self._say(_plain(e), theme.RED)
            self.start_btn.setEnabled(True)
        self._run(work, ok, bad)

    def cancel(self) -> None:
        self._cancel.set()
        self._clock.stop()
        self.cancelled.emit()

    def start_job(self, indexes: list[int] | None = None) -> "TorrentJob":
        """The download of the chosen files (after ``chosen``)."""
        assert self.meta is not None
        return TorrentJob.begin(self.meta, indexes if indexes is not None else self.indexes(), self.save_dir,
                                sess=self._sess, run=self._run)


# ---------------------------------------------------------------------------------------------------------------------
# a running download
# ---------------------------------------------------------------------------------------------------------------------

class TorrentJob(QObject):
    """One download, polled every ``interval_ms`` in the background (a poll is skipped while the last one runs)."""
    progress = Signal(dict)
    finished = Signal(str)
    failed = Signal(str)
    removed = Signal()

    def __init__(self, job_id: str = "", sess: T.TorrentSession | None = None, run: Runner | None = None,
                 interval_ms: int = 1000, name: str = ""):
        super().__init__()
        self.id = job_id
        self.name = name
        self.status: dict = {}
        self._sess = sess
        self._run = run or _bg
        self._busy = False
        self._said = ""                       # the last state told (finished / failed once each)
        _hook_quit()
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.poll)
        if job_id:
            self.timer.start()

    def sess(self) -> T.TorrentSession:
        return self._sess or session()

    @classmethod
    def begin(cls, meta: T.Meta, indexes: list[int], save_dir: str | Path | None,
              sess: T.TorrentSession | None = None, run: Runner | None = None) -> "TorrentJob":
        """Start downloading (in the background); ``failed`` says why it could not (e.g. the disk is full)."""
        job = cls(sess=sess, run=run, name=meta.name)

        def work():
            return job.sess().start(meta, indexes, save_dir)

        def ok(jid: str) -> None:
            job.id = jid
            job.timer.start()
            job.poll()

        def bad(e: str) -> None:
            # the start failed: the job's state says so (else "" reads as "just starting" -- busy for ever, and
            # "Turn off the PC" would never fire)
            job.status = {"state": "failed", "error": _plain(e), "name": job.name}
            job._tell("failed", _plain(e))
        job._run(work, ok, bad)
        return job

    def poll(self) -> None:
        if not self.id or self._busy:
            return
        self._busy = True
        jid = self.id

        def done(st: dict) -> None:
            self._busy = False
            self._got(st)

        def bad(e: str) -> None:
            self._busy = False
            if "no longer in the list" in e:
                self.timer.stop()
                self.removed.emit()
        self._run(lambda: self.sess().poll(jid), done, bad)

    def _got(self, st: dict) -> None:
        self.status = st
        self.name = st.get("name", self.name)
        self.progress.emit(st)
        state = st.get("state")
        if state == "finished":
            self.timer.stop()
            self._tell("finished", st.get("video", ""))
        elif state in ("failed", "stalled"):
            self._tell(state, st.get("error", ""))
        else:
            self._said = ""

    def _tell(self, state: str, text: str) -> None:
        key = f"{state}:{text}"
        if key == self._said:
            return
        self._said = key
        if state == "finished":
            self.finished.emit(text)
        else:
            self.failed.emit(text)

    def pause(self) -> None:
        if self.id:
            self._run(lambda: self.sess().pause(self.id), lambda _r: self.poll(), lambda e: self.failed.emit(_plain(e)))

    def resume(self) -> None:
        if self.id:
            self._said = ""
            self.timer.start()
            self._run(lambda: self.sess().resume(self.id), lambda _r: self.poll(),
                      lambda e: self._tell("failed", _plain(e)))

    def remove(self) -> None:
        self.timer.stop()
        if not self.id:
            self.removed.emit()
            return
        self._run(lambda: self.sess().remove(self.id), lambda _r: self.removed.emit(),
                  lambda e: self.removed.emit())


def restore_jobs(callback: Callable[[list[TorrentJob]], None], sess: T.TorrentSession | None = None,
                 run: Runner | None = None) -> None:
    """After an app restart: the downloads that were not finished, running again (paused ones stay paused)."""
    run = run or _bg

    def work():
        s = sess or session()
        ids = s.restore()
        return [(i, (s.job(i).name if s.job(i) else "")) for i in ids]
    run(work, lambda pairs: callback([TorrentJob(i, sess=sess, run=run, name=n) for i, n in pairs]),
        lambda _e: callback([]))


class TorrentCard(W.Card):
    """The progress card of one ``TorrentJob``: live numbers, the bar, Pause/Resume, Remove; green when done."""

    def __init__(self, job: TorrentJob):
        super().__init__(margins=(16, 14, 16, 14), spacing=10)
        self.job = job
        self._done = False
        top = QHBoxLayout()
        top.setSpacing(10)
        self.head = _title(job.name or "Starting the download…")
        top.addWidget(self.head, 1)
        self.pill = W.Pill("running")
        self.pill.set("DOWNLOADING", theme.pill_colours("running"))
        top.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        self.lay.addLayout(top)
        self.bar = W.GlideBar(3)
        self.bar.state("running")
        self.lay.addWidget(self.bar)
        self.stats = QWidget()
        self.stats.setObjectName("Transparent")
        g = QGridLayout(self.stats)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        self.seeders = _Stat("Seeders", theme.GREEN)
        self.peers = _Stat("Peers", theme.BLUE)
        self.speed = _Stat("Speed")
        self.done = _Stat("Done")
        self.eta = _Stat("Time left")
        self.health = HealthBars()
        self.health_stat = _Stat("Health", widget=self.health)
        for i, w in enumerate((self.seeders, self.peers, self.speed, self.done, self.eta, self.health_stat)):
            g.addWidget(w, 0, i)
            g.setColumnStretch(i, 1)
        self.speed.set("0 MB/s")
        self.done.set("0 %")
        self.eta.set("—")
        self.lay.addWidget(self.stats)
        frow, self.foot_left, self.foot_right = _foot("", "")
        self.lay.addLayout(frow)
        self.note = W.label("", "Dim", wrap=True)
        self.note.hide()
        self.lay.addWidget(self.note)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.pause_btn = W.Button("Pause")
        self.pause_btn.clicked.connect(self._pause_or_resume)
        self.remove_btn = W.Button("Remove")
        self.remove_btn.clicked.connect(self._remove)
        self.open_btn = W.Button("Open folder")
        self.open_btn.clicked.connect(self._open)
        self.open_btn.hide()
        for b in (self.pause_btn, self.remove_btn, self.open_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        self.lay.addLayout(btns)
        job.progress.connect(self.show_status)
        job.failed.connect(self._failed)
        job.removed.connect(self._removed)
        if job.status:
            self.show_status(job.status)

    def show_status(self, st: dict) -> None:
        state = st.get("state", "")
        video = str(st.get("video") or "")
        self.head.setText(Path(video).name if video else (st.get("name") or self.job.name or ""))
        self.bar.set(float(st.get("share", 0.0)))
        self.seeders.set(str(st.get("seeders", 0)))
        self.peers.set(str(st.get("peers", 0)))
        self.speed.set(st.get("speed_text", "0 MB/s"))
        self.done.set(f"{st.get('percent', 0)} %")
        self.eta.set(st.get("eta_text", "—"))
        self.health.set(int(st.get("health", 0)))
        added = time.strftime("%H:%M", time.localtime(st.get("added") or time.time()))
        size = T.human_size(st.get("size", 0))
        if state == "finished":
            self._finished(st)
            return
        sharing = "keeps sharing for a while when finished" if st.get("will_share") else "stops sharing when finished"
        self.foot_left.setText(f"{size} · Added {added}")
        self.foot_right.setText(sharing)
        if state == "paused":
            self.pill.set("PAUSED", theme.pill_colours("waiting"))
            self.bar.state("waiting")
            self.pause_btn.setText("Resume")
            self._say(st.get("note", ""), theme.MUTED)      # day 13: a file already there, checked / renamed
        elif state in ("stalled", "failed"):
            self.pill.set("STOPPED" if state == "stalled" else "FAILED",
                          theme.pill_colours("note" if state == "stalled" else "FAIL"))
            self.bar.state("note" if state == "stalled" else "fail")
            self.pause_btn.setText("Resume")
            self._say(st.get("error", ""), theme.AMBER if state == "stalled" else theme.RED)
        else:
            self.pill.set("DOWNLOADING", theme.pill_colours("running"))
            self.bar.state("running")
            self.pause_btn.setText("Pause")
            self._say(st.get("note", ""), theme.MUTED)

    def _finished(self, st: dict) -> None:
        if self._done:
            return
        self._done = True
        self.bar.state("pass")
        self.bar.set(1.0)
        self.pill.set("DOWNLOADED", theme.pill_colours("PASS"))
        self.stats.hide()
        self.pause_btn.hide()
        self.remove_btn.hide()
        self.open_btn.show()
        when = time.strftime("%H:%M", time.localtime(st.get("finished_at") or time.time()))
        self.foot_left.setText(f"{T.human_size(st.get('size', 0))} · finished {when} · "
                               + ("still sharing" if st.get("sharing") else "sharing stopped"))
        folder = str(st.get("save_dir", ""))
        self.foot_right.setToolTip(folder)
        self.foot_right.setText(folder if len(folder) <= 48 else folder[:20] + "…" + folder[-27:])
        self._say(st.get("note", ""), theme.MUTED)          # day 13: "already complete -- nothing downloaded again"
        self._line, self._line_hot = theme.GREEN_LINE, theme.GREEN
        self.update()

    def _say(self, text: str, colour: str) -> None:
        self.note.setText(text)
        self.note.setStyleSheet(f"color: {colour};")
        self.note.setVisible(bool(text))

    def _failed(self, msg: str) -> None:
        if not self.job.id:                      # it never started (no space, a broken file)
            self.pill.set("FAILED", theme.pill_colours("FAIL"))
            self.bar.state("fail")
            self.pause_btn.hide()
        self._say(msg, theme.RED)

    def _pause_or_resume(self) -> None:
        if self.pause_btn.text() == "Pause":
            self.pause_btn.setText("Resume")
            self.pill.set("PAUSED", theme.pill_colours("waiting"))
            self.job.pause()
        else:
            self.pause_btn.setText("Pause")
            self.pill.set("DOWNLOADING", theme.pill_colours("running"))
            self.note.hide()
            self.job.resume()

    def _remove(self) -> None:
        self.remove_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.job.remove()

    def _removed(self) -> None:
        self.hide()
        self.deleteLater()

    def _open(self) -> None:
        path = self.job.status.get("video") or self.job.status.get("save_dir") or ""
        if path:
            self.job._run(lambda: open_folder(path), None, None)   # noqa: SLF001 - a network folder can be slow


def open_folder(path: str | Path) -> None:
    """Explorer with the file selected (or the folder open)."""
    p = Path(path)
    try:
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p if p.is_dir() else p.parent))  # type: ignore[attr-defined]
    except OSError:
        pass
