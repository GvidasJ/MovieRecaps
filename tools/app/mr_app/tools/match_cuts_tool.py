"""The Match cuts tab: drop videos, a queue run one by one, each video's result, and learning from your fix.

Every job runs exactly as ``python -m match_cuts batch`` runs a video (core.run): this tab only starts the tool and
reads what it writes."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout,
                               QWidget)

from . import Tool
from ..core import jobs as J
from ..core import learnkit, paths, premiere, space
from ..core import progress as P
from ..core import results as R
from ..ui import theme
from ..ui import widgets as W


# ---------------------------------------------------------------------------------------------------------------------
# background work (never in the window's thread: it would freeze)
# ---------------------------------------------------------------------------------------------------------------------

class Worker(QThread):
    """Runs ``fn()`` in the background; ``finished_with(result)`` or ``failed(text)``."""
    finished_with = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.finished_with.emit(self.fn())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class JobThread(QThread):
    """One job (core.run.JobRun): ``progress(job id, text, share)`` every 2 s, then ``done(job id, result)``."""
    progress = Signal(int, str, float)
    done = Signal(int, dict)

    def __init__(self, job: J.Job, converted: dict, out_dir: Path):
        super().__init__()
        self.job = job
        self.converted = converted
        self.out_dir = out_dir
        self.run_ = None
        self.estimate = 0.0

    def run(self) -> None:
        from ..core.run import JobRun
        try:
            from match_cuts import batch
            self.estimate = batch.estimate_s(Path(self.job.competitor), Path(self.job.raw), self.job.mode == J.FAST)
        except Exception:  # noqa: BLE001
            self.estimate = 0.0
        try:
            self.run_ = JobRun(self.job, out_dir=self.out_dir, converted=self.converted)
            self.job.run_dir = self.run_.run_dir
            self.run_.start()
        except Exception as e:  # noqa: BLE001
            self.done.emit(self.job.id, {"label": R.FAIL, "failures": [f"the tool could not start: {e}"]})
            return

        def poll(r) -> None:
            pr = P.parse(r.progress_text())
            self.progress.emit(self.job.id, pr.text(), P.share_done(r.elapsed(), pr.left_s, self.estimate))
        try:
            res = self.run_.wait(poll)
        except Exception as e:  # noqa: BLE001
            res = {"label": R.FAIL, "failures": [f"{type(e).__name__}: {e}"]}
        self.done.emit(self.job.id, res)

    def stop(self) -> None:
        if self.run_ is not None:
            self.run_.stop()


def seconds_of(p: Path) -> float | None:
    """A video's length (the tool's own quick probe), cached."""
    key = (str(p), p.stat().st_mtime if p.exists() else 0)
    if key in _LEN:
        return _LEN[key]
    try:
        from match_cuts.cli import quick_probe
        v = float(quick_probe(p)["duration"])
    except Exception:  # noqa: BLE001
        v = None
    _LEN[key] = v
    return v


_LEN: dict = {}


def fmt_s(s: float | None) -> str:
    if not s:
        return ""
    s = int(round(s))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


# ---------------------------------------------------------------------------------------------------------------------
# one row of the queue
# ---------------------------------------------------------------------------------------------------------------------

class JobRow(W.Card):
    """A video in the queue: its name and files, Fast / Thorough, its speed, the progress, the result and its
    buttons."""

    def __init__(self, page: "MatchCutsPage", job: J.Job):
        super().__init__()
        self.page, self.job = page, job
        top = QHBoxLayout()
        top.setSpacing(12)
        self.name = W.label(job.name, "Name")
        self.files = W.label("", "Small")
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(self.name)
        names.addWidget(self.files)
        top.addLayout(names, 1)
        self.mode = W.Segmented([(J.FAST, "Fast"), (J.THOROUGH, "Thorough")], job.mode)
        self.mode.setToolTip("Fast: about 3 times quicker (the default). Thorough: looks at every frame -- slower.")
        self.mode.changed.connect(self._mode)
        self.speed = QPushButton()
        self.speed.setObjectName("Seg")
        self.speed.setFont(W.font(11, 600, 6))
        self.speed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speed.setToolTip("The speed of the finished edit: click for 100 % / 125 % (your two channels). A "
                              "speed.txt in the video's folder sets it too.")
        self.speed.clicked.connect(self._speed)
        self.swap = QPushButton("Swap")
        self.swap.setObjectName("Small")
        self.swap.setToolTip("The RAW and the competitor change places")
        self.swap.clicked.connect(self._swap)
        self.remove = QPushButton("Remove")
        self.remove.setObjectName("Ghost")
        self.remove.clicked.connect(lambda: self.page.remove_job(self.job))
        for w in (self.mode, self.speed, self.swap, self.remove):
            top.addWidget(w, 0, Qt.AlignmentFlag.AlignTop)
        self.lay.addLayout(top)
        self.state = W.StateLabel()
        self.time = W.label("", "Small")
        st = QHBoxLayout()
        st.setSpacing(10)
        st.addWidget(self.state, 1)
        st.addWidget(self.time, 0, Qt.AlignmentFlag.AlignRight)
        self.lay.addLayout(st)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.lay.addWidget(self.bar)
        self.btns = QHBoxLayout()
        self.btns.setSpacing(8)
        self.b_folder = QPushButton("Open folder")
        self.b_prem = QPushButton("Open in Premiere")
        self.b_report = QPushButton("Show report")
        for b in (self.b_folder, self.b_prem, self.b_report):
            b.setObjectName("Small")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btns.addWidget(b)
        self.btns.addStretch(1)
        self.b_folder.clicked.connect(self._open_folder)
        self.b_prem.clicked.connect(self._open_premiere)
        self.b_report.clicked.connect(lambda: ReportDialog(self.job, self.page).exec())
        self.lay.addLayout(self.btns)
        self.refresh()

    # -- actions --
    def _mode(self, v: str) -> None:
        self.job.mode = v
        self.page.update_counts()

    def _speed(self) -> None:
        self.job.speed = 125.0 if abs(self.job.speed - 100.0) < 1e-6 else 100.0
        self.job.speed_from = "you"
        self.refresh()

    def _swap(self) -> None:
        self.job.swap()
        self.refresh()

    def _open_folder(self) -> None:
        d = self.job.run_dir
        if d is not None and Path(d).exists():
            os.startfile(str(d))                          # noqa: S606

    def _open_premiere(self) -> None:
        d = self.job.run_dir
        xml = Path(d) / "1_edit.xml" if d else None
        if xml is None or not xml.is_file():
            QMessageBox.information(self, "Open in Premiere", "This run has no 1_edit.xml.")
            return
        self.page.say(f"{self.job.name}: opening in Premiere Pro ... (when it is not running, it starts first -- about a "
                      "minute)")
        w = Worker(lambda: premiere.open_xml(xml))          # Premiere's start is waited for: never in the window's thread
        w.finished_with.connect(self.page.say)
        w.failed.connect(lambda e: self.page.say(f"Premiere could not be opened: {e}"))
        self.page.workers.append(w)
        w.start()

    # -- looks --
    def refresh(self) -> None:
        j = self.job
        self.name.setText(j.name)
        if j.complete:
            self.files.setText(f"competitor  {j.competitor.name}  {J.clock(j.comp_s)}     RAW  {j.raw.name}  "
                               f"{J.clock(j.raw_s)}")
            self.files.setToolTip(f"competitor: {j.competitor}\nRAW: {j.raw}")
        busy = j.state in (J.RUNNING, J.DONE, J.STOPPED)
        self.mode.set(j.mode)
        self.mode.setEnabled(not busy)
        self.speed.setText(f"{j.speed:g} %")
        self.speed.setEnabled(not busy)
        self.swap.setEnabled(not busy and j.complete)
        self.remove.setEnabled(j.state != J.RUNNING)
        res = j.result or {}
        if j.state == J.READY:
            probs = j.problems()
            self.state.set("ready", "; ".join(probs) if probs else
                           ("from " + j.source if j.source.startswith("folder") else "waits for Start"),
                           theme.AMBER if probs else None)
        elif j.state == J.RUNNING:
            self.state.set("running", j.progress or "starting")
        elif j.state in (J.DONE, J.STOPPED):
            lab = res.get("label", R.FAIL)
            self.state.set(lab, R.short(res))
        self.time.setText(fmt_s(res.get("seconds")) if j.state in (J.DONE, J.STOPPED) else "")
        self.bar.setVisible(j.state == J.RUNNING)
        self.bar.setValue(int(1000 * j.percent))
        done = j.state in (J.DONE, J.STOPPED) and j.run_dir is not None
        for b in (self.b_folder, self.b_prem, self.b_report):
            b.setVisible(done)
        self.b_prem.setEnabled(done and (Path(j.run_dir) / "1_edit.xml").is_file() if done else False)


class ReportDialog(QDialog):
    """The run's result in plain words: why it failed or its note, what to check by hand, doubtful captions."""

    def __init__(self, job: J.Job, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{job.name} -- report")
        self.resize(820, 620)
        r = job.result or {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        head = W.StateLabel()
        head.set(r.get("label", "?"), f"{job.name} -- {fmt_s(r.get('seconds'))}")
        lay.addWidget(head)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(False)
        parts = []

        def sec(title: str, rows) -> None:
            rows = [x for x in rows if x]
            if rows:
                parts.append(f"<p style='color:{theme.TEXT};font-weight:700;margin-top:10px'>{title}</p>" +
                             "".join(f"<div style='color:{theme.MUTED};margin-left:8px'>{esc(x)}</div>" for x in rows))
        sec("Why it failed", r.get("failures") or [])
        sec("Note", r.get("notes") or [])
        sec("Check by hand (from the run's end summary)", [h.strip() for h in r.get("hand") or []])
        sec("Captions written as they are -- check them", r.get("doubtful") or [])
        sec("Run folder", [r.get("run_dir", "")])
        sec("Command", [r.get("command", "")])
        tb.setHtml("".join(parts) or "<p>No report: the run did not finish.</p>")
        lay.addWidget(tb, 1)
        row = QHBoxLayout()
        for text, path in (("Open report.md", r.get("report")), ("Open the run's log", r.get("log"))):
            b = QPushButton(text)
            b.setEnabled(bool(path) and Path(path).is_file())
            b.clicked.connect(lambda _=False, p=path: os.startfile(str(p)))   # noqa: S606
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def showEvent(self, e) -> None:                       # noqa: N802
        W.dark_title_bar(self)
        super().showEvent(e)


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------------------------------------------------
# learn from your fix
# ---------------------------------------------------------------------------------------------------------------------

class LearnBox(W.Card):
    """Drop your fixed .prproj and the run folder it was made from: copied to finished\\<name>\\<name>_fixed.prproj
    and output\\laptop_<name>; then the LEARN line to copy into the next night's list."""

    def __init__(self, page: "MatchCutsPage"):
        super().__init__(hover=False)
        self.page = page
        self.project: Path | None = None
        self.run: Path | None = None
        self.names: list[str] = []
        self.worker = None
        drop = W.DropZone("Drop your fix here", ["your fixed Premiere project (.prproj) and the tool's output folder it "
                                                 "was made from (output\\video4 ...) -- together or one after the other"],
                          [("Choose project...", self._pick_project), ("Choose output folder...", self._pick_run)],
                          big=False)
        drop.dropped.connect(self.take)
        self.lay.addWidget(drop)
        self.info = W.label("", "Muted", wrap=True, select=True)
        self.lay.addWidget(self.info)
        self.done_plan = None                     # the copy just made (its notes are no warning any more)
        self.msg = W.label("", "Muted", wrap=True, select=True)
        self.msg.hide()
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(W.label("Name", "Small"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("video11")
        self.name.setMaximumWidth(220)
        self.name.textChanged.connect(self._show)
        row.addWidget(self.name)
        self.go = QPushButton("Copy into the project")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._copy)
        row.addWidget(self.go)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.lay.addWidget(self.bar)
        self.line = QPlainTextEdit()
        self.line.setReadOnly(True)
        self.line.setMaximumHeight(96)
        self.line.hide()
        self.lay.addWidget(self.line)
        self.copy_line = QPushButton("Copy the LEARN line")
        self.copy_line.hide()
        self.copy_line.clicked.connect(self._copy_line)
        self.lay.addWidget(self.copy_line, 0, Qt.AlignmentFlag.AlignLeft)
        self.lay.addWidget(self.msg)
        self._show()

    def say(self, text: str) -> None:
        self.msg.setText(text)
        self.msg.setVisible(bool(text))

    def _pick_project(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Your fixed project", str(paths.repo()), "Premiere project (*.prproj)")
        if f:
            self.take([Path(f)])

    def _pick_run(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "The tool's output folder of that video", str(paths.output_dir()))
        if d:
            self.take([Path(d)])

    def take(self, items: list) -> None:
        pr, run, notes = learnkit.sort_drop(items)
        self.project = pr or self.project
        self.run = run or self.run
        if (pr or run) and not self.name.text().strip():
            self.name.setText(learnkit.name_from(self.project, self.run))
        elif pr is not None:
            self.name.setText(learnkit.name_from(self.project, self.run))
        self.done_plan = None
        self.say("; ".join(notes))
        self._show()

    def _plan(self):
        if self.project is None or self.run is None:
            return None
        return learnkit.plan(self.project, self.run, self.name.text().strip() or None)

    def _show(self) -> None:
        bits = [f"Project: {self.project}" if self.project else "Project: drop your fixed .prproj",
                f"Output folder: {self.run}" if self.run else "Output folder: drop the run folder (with 1_edit.xml)"]
        pl = self._plan()
        done = pl is not None and self.done_plan is not None and pl.name == self.done_plan.name
        if pl is not None and done:
            bits += [f"Copied to:  {learnkit.rel(pl.project_to)}   and   {learnkit.rel(pl.run_to)}"]
        elif pl is not None:
            bits += [f"Copies to:  {learnkit.rel(pl.project_to)}   and   {learnkit.rel(pl.run_to)}"]
            bits += [f"Note: {p}" for p in pl.problems()]
        self.info.setText("\n".join(bits))
        self.go.setEnabled(pl is not None and not done and self.worker is None
                           and not any("not there" in p or "not a" in p for p in pl.problems()))

    def _copy(self) -> None:
        pl = self._plan()
        if pl is None:
            return
        probs = [p for p in pl.problems() if "replaced" in p]
        if probs and QMessageBox.question(self, "Replace?", "\n".join(probs) + "\n\nReplace them?") != \
                QMessageBox.StandardButton.Yes:
            return
        self.bar.show()
        self.bar.setValue(0)
        self.go.setEnabled(False)
        prog = {"v": 0.0}

        def work():
            learnkit.copy(pl, lambda a, b: prog.__setitem__("v", a / b if b else 1.0))
            return pl
        t = QTimer(self)
        t.timeout.connect(lambda: self.bar.setValue(int(1000 * prog["v"])))
        t.start(300)
        self.worker = Worker(work)
        self.worker.finished_with.connect(lambda p: self._copied(p, t))
        self.worker.failed.connect(lambda e: self._copy_failed(e, t))
        self.worker.start()

    def _copied(self, pl, timer) -> None:
        timer.stop()
        self.worker = None
        self.bar.hide()
        self.done_plan = pl
        if pl.name not in self.names:
            self.names.append(pl.name)
        self.line.setPlainText(learnkit.learn_line(self.names))
        self.line.show()
        self.copy_line.show()
        self.say("done -- copy the LEARN line into the next night's list")
        self._show()

    def _copy_failed(self, err: str, timer) -> None:
        timer.stop()
        self.worker = None
        self.bar.hide()
        QMessageBox.warning(self, "Copy failed", err)
        self._show()

    def _copy_line(self) -> None:
        QApplication.clipboard().setText(self.line.toPlainText())
        self.say("the LEARN line is copied -- paste it into the next night's list")


# ---------------------------------------------------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------------------------------------------------

class MatchCutsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.jobs: list[J.Job] = []
        self.rows: dict[int, JobRow] = {}
        self.waiting: Path | None = None
        self.drops: list[list] = []               # drops not read yet: one after the other (add_paths)
        self.running = False
        self.current: JobThread | None = None
        self.converted: dict = {}
        self.t_start = 0.0
        self.workers: list[QThread] = []
        self.out_dir = paths.output_dir()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 34, 0, 40)
        lay.setSpacing(16)
        # hero
        title = QLabel(f"Match <span style='color:{theme.RED}'>cuts</span>")
        title.setObjectName("Title")
        title.setFont(W.font(44, 800, -3))
        lay.addWidget(title)
        lay.addWidget(W.label("Drop a RAW and a competitor video. The tool rebuilds the competitor's edit from the RAW "
                              "for Premiere: 1_edit.xml and 2_captions.srt.", "Lead", wrap=True))
        lay.addSpacing(6)
        # drop zone
        self.drop = W.DropZone("Drop videos here",
                               ["a RAW and a competitor = one video  ·  a folder of folders (video1, video2 ...) = "
                                "one video each  ·  one video alone waits for the next one"],
                               [("Choose videos...", self._pick_files), ("Choose a folder...", self._pick_folder)])
        self.drop.dropped.connect(self.add_paths)
        lay.addWidget(self.drop)
        self.wait_line = QWidget()
        wl = QHBoxLayout(self.wait_line)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(W.Square(theme.AMBER))
        self.wait_text = W.label("", "Muted")
        wl.addWidget(self.wait_text, 1)
        cancel = QPushButton("Remove")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self._drop_waiting)
        wl.addWidget(cancel)
        self.wait_line.hide()
        lay.addWidget(self.wait_line)
        # queue
        self.count = W.label("", "Small")
        self.b_start = QPushButton("Start")
        self.b_start.setObjectName("Primary")
        self.b_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_start.clicked.connect(self.start)
        self.b_stop = QPushButton("Stop")
        self.b_stop.clicked.connect(self.stop)
        self.b_clear = QPushButton("Clear finished")
        self.b_clear.setObjectName("Ghost")
        self.b_clear.clicked.connect(self.clear_finished)
        lay.addSpacing(10)
        lay.addWidget(W.SectionHeader("Queue", [self.count, self.b_clear, self.b_stop, self.b_start]))
        self.status = W.label("", "Muted", wrap=True, select=True)
        self.status.hide()
        lay.addWidget(self.status)
        self.list = QVBoxLayout()
        self.list.setSpacing(12)
        lay.addLayout(self.list)
        self.empty = W.label("The queue is empty. Drop videos above.", "Dim")
        lay.addWidget(self.empty)
        # learn
        lay.addSpacing(18)
        lay.addWidget(W.SectionHeader("Learn from your fix", [W.tag("for the nightly LEARN item")]))
        self.learn = LearnBox(self)
        lay.addWidget(self.learn)
        lay.addStretch(1)
        self.update_counts()

    # -- adding videos --
    def _pick_files(self) -> None:
        fs, _ = QFileDialog.getOpenFileNames(self, "A RAW and a competitor", str(paths.repo() / "input"),
                                             "Videos (" + " ".join(f"*{e}" for e in sorted(J.VIDEO_EXTS)) + ")")
        if fs:
            self.add_paths([Path(f) for f in fs])

    def _pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "A folder of video folders", str(paths.repo() / "input"))
        if d:
            self.add_paths([Path(d)])

    def add_paths(self, items: list) -> None:
        """A drop: sorted into jobs in the background (the videos' lengths are read). Drops are read one after the
        other, so a video waiting for its partner meets the next drop even when it comes before this one is read."""
        self.drops.append(list(items))
        if len(self.drops) == 1:
            self._read_drop()

    def _read_drop(self) -> None:
        """The oldest unread drop, with the video waiting now."""
        if not self.drops:
            return
        from match_cuts import batch
        items, waiting = self.drops[0], self.waiting
        self.say("reading the videos ...")

        def work():
            return J.jobs_from_drop(items, seconds_of, waiting, find_videos=batch.find_videos,
                                    speed_of=lambda folder: batch.video_speed(folder))
        w = Worker(work)
        w.finished_with.connect(self._added)
        w.failed.connect(self._drop_failed)
        self.workers.append(w)
        w.start()

    def _drop_failed(self, err: str) -> None:
        self.say(f"could not read the drop: {err}")
        self.drops.pop(0)
        self._read_drop()

    def _added(self, res: J.DropResult) -> None:
        self.drops.pop(0)
        self._added_jobs(res)
        self._read_drop()

    def _added_jobs(self, res: J.DropResult) -> None:
        J.name_jobs(res.jobs, [j.name for j in self.jobs])
        for j in res.jobs:
            self.jobs.append(j)
            row = JobRow(self, j)
            self.rows[j.id] = row
            self.list.addWidget(row)
        self.waiting = res.waiting
        if self.waiting is not None:
            s = seconds_of(self.waiting)
            self.wait_text.setText(f"{self.waiting.name} ({J.clock(s)}) waits for its partner: drop the other video "
                                   "(the RAW or the competitor)")
        self.wait_line.setVisible(self.waiting is not None)
        msg = []
        if res.jobs:
            msg.append(f"{len(res.jobs)} video(s) added")
        msg += res.notes
        self.say("; ".join(msg) if msg else "")
        self.update_counts()
        if self.running and self.current is None:
            self._next()

    def _drop_waiting(self) -> None:
        self.waiting = None
        self.wait_line.hide()

    def remove_job(self, job: J.Job) -> None:
        if job.state == J.RUNNING:
            return
        self.jobs = [j for j in self.jobs if j.id != job.id]
        row = self.rows.pop(job.id, None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self.update_counts()

    def clear_finished(self) -> None:
        for j in [j for j in self.jobs if j.state in (J.DONE, J.STOPPED)]:
            self.remove_job(j)

    # -- the queue --
    def ready(self) -> list[J.Job]:
        return [j for j in self.jobs if j.state == J.READY]

    def update_counts(self) -> None:
        n_ready = len(self.ready())
        n_done = sum(1 for j in self.jobs if j.state in (J.DONE, J.STOPPED))
        bits = [f"{len(self.jobs)} video(s)"] + ([f"{n_ready} to run"] if n_ready else []) + \
               ([f"{n_done} finished"] if n_done else [])
        self.count.setText("  ·  ".join(bits) if self.jobs else "")
        self.empty.setVisible(not self.jobs)
        self.b_start.setEnabled(not self.running and n_ready > 0)
        self.b_stop.setEnabled(self.running)
        self.b_clear.setEnabled(n_done > 0)
        self.ctx.set_strip_queue(len(self.jobs), self.running)

    def say(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def start(self) -> None:
        jobs = self.ready()
        if not jobs:
            return
        probs = [f"{j.name}: {p}" for j in jobs for p in j.problems()]
        if self.waiting is not None:
            probs.append(f"{self.waiting.name}: one video alone -- no RAW (or no competitor) for it; it will not run")
        self.say("checking the videos ...")
        self.b_start.setEnabled(False)
        from match_cuts import batch
        pairs = [(j.name, Path(j.competitor), Path(j.raw)) for j in jobs if j.complete]
        runs = [(j.name, Path(j.competitor), Path(j.raw), self.out_dir / j.name) for j in jobs if j.complete]
        w = Worker(lambda: (batch.input_problems(pairs), space.problems(runs)))
        w.finished_with.connect(lambda r: self._checked(probs, r[0], r[1]))
        w.failed.connect(lambda _e: self._checked(probs, {}, []))
        self.workers.append(w)
        w.start()

    def _checked(self, probs: list, tool: dict, room: list | None = None) -> None:
        for name, found in (tool or {}).items():
            mine = [p for p in probs if p.startswith(f"{name}: ")]
            probs += [f"{name}: {p}" for p in found      # (the competitor longer than the RAW: said once, the app's way)
                      if not ("longer than the RAW" in p and any("longer than the RAW" in m for m in mine))]
        probs += list(room or [])                         # night 10: a copy into another drive's run folder needs room
        if probs:
            box = QMessageBox(self)
            box.setWindowTitle("Before the start")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("Some videos look wrong:" if len(probs) > len(room or []) else "Not enough free space:")
            box.setInformativeText("\n".join(f"- {p}" for p in probs[:20]) + "\n\nStart anyway?")
            start = box.addButton("Start anyway", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not start:
                self.say("not started")
                self.update_counts()
                return
        for j in self.ready():
            if not j.complete:
                j.state = J.STOPPED
                j.result = {"label": R.FAIL, "failures": ["only one video: no RAW or no competitor"]}
                self.rows[j.id].refresh()
        self.running = True
        self.t_start = time.time()
        self.say("running -- you can add more videos; they join the queue")
        self._next()

    def _next(self) -> None:
        nxt = next((j for j in self.jobs if j.state == J.READY and j.complete), None)
        if nxt is None:
            self._finished()
            return
        nxt.state = J.RUNNING
        nxt.progress, nxt.percent = "starting", 0.0
        self.rows[nxt.id].refresh()
        th = JobThread(nxt, self.converted, self.out_dir)
        th.progress.connect(self._progress)
        th.done.connect(self._done)
        self.current = th
        self.update_counts()
        th.start()

    def _progress(self, job_id: int, text: str, share: float) -> None:
        j = next((j for j in self.jobs if j.id == job_id), None)
        if j is None:
            return
        j.progress, j.percent = text, share
        self.rows[j.id].refresh()

    def _done(self, job_id: int, res: dict) -> None:
        j = next((j for j in self.jobs if j.id == job_id), None)
        th = self.current
        self.current = None
        if th is not None:
            th.wait(2000)
        if j is not None:
            j.result = res
            j.state = J.STOPPED if res.get("label") == R.STOPPED else J.DONE
            if res.get("run_dir"):
                j.run_dir = Path(res["run_dir"])
            if j.id in self.rows:
                self.rows[j.id].refresh()
        if self.running:
            self._next()
        else:
            self.update_counts()

    def _finished(self) -> None:
        was = self.running
        self.running = False
        self.update_counts()
        if not was:
            return
        done = [j for j in self.jobs if j.state in (J.DONE, J.STOPPED) and j.result]
        counts: dict[str, int] = {}
        for j in done:
            counts[j.result.get("label", "?")] = counts.get(j.result.get("label", "?"), 0) + 1
        text = ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing ran"
        took = fmt_s(time.time() - self.t_start)
        self.say(f"the queue is done: {text} ({took})")
        self.ctx.notify("Match cuts: the queue is done", f"{text} -- {took}")

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self.current is not None:
            self.current.stop()
        self.say("stopped -- the videos not run yet stay in the queue")
        self.update_counts()

    def busy(self) -> bool:
        return self.running and self.current is not None


class MatchCutsTool(Tool):
    key = "match_cuts"
    title = "Match cuts"

    def page(self, ctx) -> QWidget:
        return MatchCutsPage(ctx)
