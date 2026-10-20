"""The Match cuts tab's queue (night 11's look): drop videos, a queue run one by one, each video's result.

Every job runs exactly as ``python -m match_cuts batch`` runs a video (core.run): this tab only starts the tool and
reads what it writes. A row shows its slate (SCENE 01 · TAKE 1 · the date), the name and channel, a status pill, the
title with a Copy button, a progress bar that glides, the step it is in, and the date and time of every step. Rows
can be dragged to change the order; right-click a row for more; Space starts, Del removes the row under the mouse."""
from __future__ import annotations

import copy
import os
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QCursor, QDrag, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QMessageBox, QTextBrowser, QVBoxLayout, QWidget)

from . import Tool
from ..core import jobs as J
from ..core import paths, premiere, space
from ..core import progress as P
from ..core import results as R
from ..ui import motion as Mo
from ..ui import theme
from ..ui import widgets as W
from ..ui.glide_list import GlideList, scroll_to_top

ROW_MIME = "application/x-movierecaps-job"


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
        self.stop_asked = False             # day 12: Stop (or Swap) pressed before the tool started

    def run(self) -> None:
        from ..core import flow
        from ..core.run import JobRun
        j = self.job
        if j.complete and (j.comp_s is None or j.raw_s is None):    # the lengths decide --force-inputs (J.forced)
            j.comp_s = seconds_of(Path(j.competitor)) if j.comp_s is None else j.comp_s
            j.raw_s = seconds_of(Path(j.raw)) if j.raw_s is None else j.raw_s
        try:
            from match_cuts import batch
            self.estimate = batch.estimate_s(Path(self.job.competitor), Path(self.job.raw), self.job.mode == J.FAST)
        except Exception:  # noqa: BLE001
            self.estimate = 0.0
        lock = None
        if self.job.project is not None:      # night 11: two computers never make one project's edit at once
            try:
                from ..core import locks
                lock = locks.WorkLock(self.job.project, "edit")
                lock.acquire()
            except Exception as e:  # noqa: BLE001 - LockedError says who holds it, in plain words
                if type(e).__name__ == "LockedError":
                    self.done.emit(self.job.id, {"label": R.FAIL, "failures": [str(e)]})
                    return
                lock = None                   # (the locks module missing on an old checkout: go on without)
        try:
            self._run(flow, lock)
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:  # noqa: BLE001
                    pass

    def _run(self, flow, lock) -> None:
        from ..core.run import JobRun
        if self.stop_asked:
            self.done.emit(self.job.id, {"label": R.STOPPED})
            return
        try:                         # night 10: the channel's template with the title as the frame, its watermark
            frame, watermark = flow.template_for_job(self.job)
        except Exception as e:  # noqa: BLE001
            self.done.emit(self.job.id, {"label": R.FAIL, "failures": [f"the template could not be made: {e}"]})
            return
        try:
            self.run_ = JobRun(self.job, out_dir=self.out_dir, converted=self.converted, frame=frame,
                               watermark=watermark)
            self.job.run_dir = self.run_.run_dir
            self.run_.start()
            if self.stop_asked:
                self.run_.stop()
        except Exception as e:  # noqa: BLE001
            self.done.emit(self.job.id, {"label": R.FAIL, "failures": [f"the tool could not start: {e}"]})
            return
        if self.job.project is not None:
            try:
                from ..core import projects as PJ
                PJ.load(self.job.project).set_step("edit", PJ.RUNNING, run_dir=str(self.run_.run_dir),
                                                   mode=self.job.mode, speed=self.job.speed)
            except Exception:  # noqa: BLE001 - the status is a convenience
                pass

        def poll(r) -> None:
            pr = P.parse(r.progress_text())
            share = P.share_done(r.elapsed(), pr.left_s, self.estimate)
            bits = [pr.step or "starting", f"{100 * share:.0f} %"]
            if pr.left_s is not None:
                bits.append(P.left_text(pr.left_s))
            self.progress.emit(self.job.id, " · ".join(bits), share)
        try:
            res = self.run_.wait(poll)
        except Exception as e:  # noqa: BLE001
            res = {"label": R.FAIL, "failures": [f"{type(e).__name__}: {e}"]}
        if self.job.project is not None:
            try:
                from ..core import projects as PJ
                flow.record_edit(PJ.load(self.job.project), res)
            except Exception:  # noqa: BLE001
                pass
        self.done.emit(self.job.id, res)

    def stop(self) -> None:
        self.stop_asked = True
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


def when(dt: datetime | None) -> str:
    """'18 Oct 02:44' -- the date format of every step everywhere in the app."""
    return dt.strftime("%d %b %H:%M").lstrip("0") if dt else "—"


def _iso(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s)).astimezone().replace(tzinfo=None) if s else None
    except ValueError:
        return None


def template_line(j: J.Job) -> str:
    """The row's template line: 'Template: LouLaughs -- "my title" (+ watermark) -- 42 % the same words ...'."""
    if not j.channel:
        return "Template: default (templates\\default.png), no watermark"
    if j.title:
        t = f"Template: {j.channel}"
    elif getattr(j, "project", None) is not None:      # day 12: a project without a headline -> the channel's default
        t = f"Template: {j.channel} -- its default template (no headline; press Template to type one)"
    else:
        t = f"Template: {j.channel} -- NO TITLE yet (press Template)"
    t += " + watermark"                      # (the title itself is in the row's title box)
    if j.headline:
        try:
            from ..core import title_check as T
            c = T.check(j.title, j.headline)
            t += f"  ·  {c.message}" + ("  !!" if c.warn else "")
        except Exception:  # noqa: BLE001
            pass
    if j.project is not None:
        t += f"  ·  project {Path(j.project).name}"
    return t


def short_file_name(name: str, limit: int = 48) -> str:
    """A long downloaded file name ("Before Tobey Maguire Was Spider-Man He Had One Job 🕷️📺 [f18B0Accoig].mp4")
    shortened in the middle so the row's pick buttons and pair line show its start AND its end (the link id and the
    type); the full path is in the tooltip. Day 12 e2e run: the pick button showed "fore Tobey ... is the RA"."""
    if len(name) <= limit:
        return name
    tail = name[-min(len(name) // 2, max(14, limit // 3)):]
    m = re.search(r"\s*\[[^\]]{4,20}\]\.\w{2,4}$", name)       # " [id].mp4" kept whole
    if m and len(m.group(0)) < limit - 12:
        tail = m.group(0).lstrip()
    head = name[:max(8, limit - len(tail) - 1)].rstrip(" -_.,")
    return f"{head}…{tail}"


def pair_text(j: J.Job) -> str:
    """Day 12: which file is which, plain: 'RAW: episode.mp4 (22:41) · COMP: short.mp4 (0:58)' ('…' while a length
    is being read, '?' when it cannot be read)."""
    def ln(s: float | None) -> str:
        return "…" if s is None and getattr(j, "reading_lengths", False) else J.clock(s)
    raw = f"RAW: {Path(j.raw).name} ({ln(j.raw_s)})" if j.raw is not None else "RAW: — (drop it)"
    comp = f"COMP: {Path(j.competitor).name} ({ln(j.comp_s)})" if j.competitor is not None else "COMP: — (drop it)"
    return f"{raw} · {comp}"


def pair_html(j: J.Job) -> str:
    """pair_text in the mock-up's colours (.pair): RAW in coral, COMP in the info blue, the names bright, the lengths
    dim."""
    def part(word: str, colour: str, f: Path | None, s: float | None) -> str:
        n = esc(short_file_name(Path(f).name)) if f is not None else "—"
        ln = "…" if s is None and getattr(j, "reading_lengths", False) else J.clock(s)
        return (f"<span style='color:{colour};font-weight:600;letter-spacing:1px'>{word}</span>&nbsp;"
                f"<span style='color:{theme.TEXT}'>{n}</span>&nbsp;<span style='color:{theme.DIM}'>({ln})</span>")
    return (part("RAW", theme.CORAL, j.raw, j.raw_s) + f"<span style='color:{theme.DIM}'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
            + part("COMP", theme.BLUE, j.competitor, j.comp_s))


def template_problems(j: J.Job) -> list[str]:
    """What looks wrong with a job's template before the start: no title, a title too close to the competitor's
    headline."""
    if not j.channel:
        return []
    if not (j.title or "").strip():
        if getattr(j, "project", None) is not None:     # day 12: the channel's default template is used, as it is
            return []
        return ["the channel's template has no title yet: press Template on its row and type it"]
    if j.headline:
        try:
            from ..core import title_check as T
            c = T.check(j.title, j.headline)
            if c.warn:
                return [f"your title is too close to the competitor's headline: {c.message}"]
        except Exception:  # noqa: BLE001
            pass
    return []


def open_in_premiere(xml: Path) -> str:
    """Open a run's 1_edit.xml in Premiere -- first pointed at its media as THIS computer sees them (a project made on
    the other computer, opened from the shared folder: D:\\Shared on the PC, \\\\192.168.101.11\\Shared on the laptop)."""
    try:
        from ..core import publish
        publish.relink_edit(Path(xml).parent)
    except Exception:  # noqa: BLE001 - an older checkout without it: the XML as it is
        pass
    return premiere.open_xml(Path(xml))


def _toast(ctx, text: str, kind: str = "ok") -> None:
    fn = getattr(ctx, "toast", None)
    if fn is not None:
        try:
            fn(text, kind)
        except Exception:  # noqa: BLE001
            pass


def step_dates(j: J.Job) -> list[tuple[str, datetime | None]]:
    """The row's dates: a project's steps (Downloaded, Converted, Template, Edit), else Added and Edit. (A project's
    YouTube title is read on the way: ``j.upload_title``.)"""
    out: list[tuple[str, datetime | None]] = []
    if j.project is not None:
        try:
            from ..core import projects as PJ
            data = PJ.load(j.project).data
            st = data.get("steps") or {}
            for key, name in (("download", "Downloaded"), ("convert", "Converted"), ("template", "Template")):
                s = st.get(key) or {}
                out.append((name, _iso(s.get("finished")) if s.get("state") == "done" else None))
            j.upload_title = data.get("upload_title") or ""
            from ..ui.titles_block import has_titles, is_just_cuts
            j.titles_known = has_titles(data)              # day 12: the competitor's two real titles
            j.post_title, j.video_headline = data.get("post_title"), data.get("video_headline")
            j.just_cuts = is_just_cuts(data)
        except Exception:  # noqa: BLE001 - shown without them
            pass
    else:
        out.append(("Added", getattr(j, "added", None)))
    out.append(("Edit", getattr(j, "edited", None)))
    return out


def ask_upload_title(parent, current: str) -> str | None:
    """'The title you upload the Short with' -- None when cancelled."""
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, "YouTube title", "The title you upload this Short with (emojis allowed):",
                                    text=current or "")
    return text.strip() if ok else None


def save_upload_title(ctx, folder, text: str, then=None) -> None:
    """The YouTube title into the project (project.json + title.txt), in the background."""
    from ..ui import background as BG

    def work():
        from ..core import board
        board.set_upload_title(Path(folder), text)

    def done(_r) -> None:
        _toast(ctx, "YouTube title saved ✓")
        if then is not None:
            then()
        b = (getattr(ctx, "pages", None) or {}).get("board")
        if b is not None and hasattr(b, "reload"):
            b.reload()
    BG.call(work, done, lambda e: _toast(ctx, f"Could not save it: {e}", "fail"))


# ---------------------------------------------------------------------------------------------------------------------
# one row of the queue
# ---------------------------------------------------------------------------------------------------------------------

class JobRow(QFrame):
    """A video in the queue (module docstring). Under the mouse: a red left edge, a dark red border, a warmer
    background, a faint glow (painted by the list behind it), the title box lighting up and a faint warm light --
    all fading. Running: a slowly circling red border. Done: one green sweep ("that's a wrap")."""

    def __init__(self, page: "MatchCutsPage", job: J.Job):
        super().__init__()
        self.setObjectName("JobRow")
        self.page, self.job = page, job
        self.hover = Mo.Fader(self, self._hot)
        self.light = Mo.Fader(self, self.update, Mo.SWEEP_IN_MS, Mo.SWEEP_OUT_MS)
        self.ring = Mo.Loop(self, self.update, 3200)
        self.wrap = Mo.Once(self, self.update, 1100)
        self.gone = Mo.Fader(self, self._fading, 1, 350)
        self._press: QPointF | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self.slate = QLabel()
        self.slate.setFont(W.mono(10.5, 400, 12))
        lay.addWidget(self.slate)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.name = W.label(job.name, "Name")
        self.name.setFont(W.font(15, 700))
        top.addWidget(self.name, 0, Qt.AlignmentFlag.AlignVCenter)
        self.chan_box = QHBoxLayout()
        top.addLayout(self.chan_box)
        top.addStretch(1)
        self.kind = W.Pill("Just cuts")          # day 12: a Just cuts project (project.json kind == "just_cuts")
        self.kind.set("Just cuts", (theme.CORAL, "#2a1812", "#5a2f22"))
        self.kind.hide()
        top.addWidget(self.kind, 0, Qt.AlignmentFlag.AlignTop)
        self.pill = W.Pill("ready")
        top.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        self.title_box = W.TitleBox(job.title)
        self.title_box.copied.connect(lambda _t: _toast(self.page.ctx, "Title copied ✓"))
        lay.addWidget(self.title_box)
        self.yt_box = W.TitleBox("", "YouTube", editable=True, empty="the title you upload it with -- Edit")
        self.yt_box.copied.connect(lambda _t: _toast(self.page.ctx, "YouTube title copied ✓"))
        self.yt_box.edit_clicked.connect(self._edit_upload_title)
        lay.addWidget(self.yt_box)
        from ..ui.titles_block import TitlesBlock    # day 12: the competitor's two real titles (project.json)
        self.titles = TitlesBlock()
        self.titles.copied.connect(lambda _t: _toast(self.page.ctx, "Copied ✓"))
        self.titles.use_headline.connect(lambda t: self.page.use_headline(self.job, t))
        self.titles.use_title.connect(self._use_title)
        self.titles.hide()
        lay.addWidget(self.titles)
        pl = QHBoxLayout()                       # day 12: which file is which -- RAW: name (length) · COMP: name (length)
        pl.setSpacing(10)
        self.pair = QLabel()
        self.pair.setFont(W.mono(11.5))
        self.pair.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pl.addWidget(self.pair, 0, Qt.AlignmentFlag.AlignVCenter)
        self.swap = W.Button("⇄ Swap", "Mini")
        self.swap.setToolTip("Swap RAW and competitor (a run already started stops and starts again with the files "
                             "switched -- nothing is copied again)")
        self.swap.clicked.connect(self._swap)
        pl.addWidget(self.swap, 0, Qt.AlignmentFlag.AlignVCenter)
        pl.addStretch(1)
        lay.addLayout(pl)
        self.check = QWidget()                   # day 12: "Check which is the RAW" -- two buttons, your pick
        self.check.setObjectName("Transparent")
        cl = QVBoxLayout(self.check)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        self.check_note = W.label("The two videos are almost the same length, so I need you to say which is which. "
                                  "It does not start before you choose.", "Muted", wrap=True)
        cl.addWidget(self.check_note)
        cb = QHBoxLayout()
        cb.setSpacing(8)
        self.pick_a, self.pick_b = W.Button(""), W.Button("")
        self.pick_a.clicked.connect(lambda: self._pick(0))
        self.pick_b.clicked.connect(lambda: self._pick(1))
        cb.addWidget(self.pick_a)
        cb.addWidget(self.pick_b)
        cb.addStretch(1)
        cl.addLayout(cb)
        lay.addWidget(self.check)
        self.check.hide()
        self._pick_files: list[Path] = []
        self.bar = W.GlideBar(3)
        lay.addWidget(self.bar)
        stp = QHBoxLayout()
        self.step = W.mono_label("", theme.MUTED, 12)
        self.step.setWordWrap(True)
        self.mode_txt = W.mono_label("", theme.MUTED, 12)
        stp.addWidget(self.step, 1)
        stp.addWidget(self.mode_txt, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(stp)
        self.dates = QLabel()
        self.dates.setFont(W.mono(11))
        self.dates.setWordWrap(True)
        lay.addWidget(self.dates)
        info = QHBoxLayout()
        info.setSpacing(18)
        self.tpl_line = W.label("", "Dim", wrap=True, select=True)
        info.addWidget(self.tpl_line, 1)
        lay.addLayout(info)
        self.note = W.label("", "Muted", wrap=True, select=True)
        lay.addWidget(self.note)
        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self.mode = W.Segmented([(J.FAST, "Fast"), (J.THOROUGH, "Thorough")], job.mode)
        self.mode.setToolTip("Fast: about 3 times quicker (the default). Thorough: looks at every frame -- slower.")
        self.mode.changed.connect(self._mode)
        self.speed = W.Button("", "Seg")
        self.speed.setToolTip("The speed of the finished edit: click for 100 % / 125 % (your two channels). A "
                              "speed.txt in the video's folder sets it too.")
        self.speed.clicked.connect(self._speed)
        self.tpl = W.Button("Template", "Small")
        self.tpl.setToolTip("Your channel's template with your title (a live preview, the competitor's headline next "
                            "to it) -- the edit's frame, with the channel's watermark on top")
        self.tpl.clicked.connect(self._template)
        for w in (self.mode, self.speed, self.tpl):
            ctl.addWidget(w)
        ctl.addStretch(1)
        self.b_retry = W.Button("Try again", "Small")     # day 13: a run that stopped by itself (a full disk, a crash)
        self.b_retry.setToolTip("Run this video again (the same mode, as its next take) -- after you did what the "
                                "line above says")
        self.b_retry.clicked.connect(self._retry)
        self.b_retry.hide()
        self.b_folder = W.Button("Open folder", "Small")
        self.b_prem = W.Button("Open in Premiere", "Small")
        self.b_report = W.Button("Show report", "Small")
        self.remove = W.Button("Remove", "Ghost")
        for b in (self.b_retry, self.b_folder, self.b_prem, self.b_report, self.remove):
            ctl.addWidget(b)
        self.b_folder.clicked.connect(self._open_folder)
        self.b_prem.clicked.connect(self._open_premiere)
        self.b_report.clicked.connect(lambda: ReportDialog(self.job, self.page).exec())
        self.remove.clicked.connect(lambda: self.page.remove_job(self.job))
        lay.addLayout(ctl)
        self._dates_cache: list | None = None
        self._last_state = None
        self.refresh()

    # -- hover --
    def _hot(self) -> None:
        self.title_box.row_hot.to(self.hover.target)
        self.yt_box.row_hot.to(self.hover.target)
        self.update()
        lst = self.parentWidget()
        if lst is not None:
            g = self.geometry().adjusted(-32, -32, 32, 40)
            lst.update(g)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        self.light.to(1.0)
        self.page.hovered = self
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        self.light.to(0.0)
        if self.page.hovered is self:
            self.page.hovered = None
        super().leaveEvent(e)

    # -- drag to change the order --
    def mousePressEvent(self, e) -> None:                 # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:                  # noqa: N802
        if self._press is not None and (e.position() - self._press).manhattanLength() > \
                QApplication.startDragDistance():
            self._press = None
            if self.job.state == J.READY:            # only waiting rows change the run order
                self._drag()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:               # noqa: N802
        self._press = None
        super().mouseReleaseEvent(e)

    def _drag(self) -> None:
        drag = QDrag(self)
        md = QMimeData()
        md.setData(ROW_MIME, str(self.job.id).encode("ascii"))
        drag.setMimeData(md)
        pm = self.grab()
        drag.setPixmap(pm.scaledToWidth(min(pm.width(), 560), Qt.TransformationMode.SmoothTransformation))
        drag.setHotSpot(drag.pixmap().rect().center())
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.4)
        self.setGraphicsEffect(eff)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.setGraphicsEffect(None)
            self.page.queue.clear_slot()

    def contextMenuEvent(self, e) -> None:                # noqa: N802
        from ..ui.menu import FadeMenu
        j = self.job
        done = j.run_dir is not None and Path(j.run_dir).exists()
        xml = done and (Path(j.run_dir) / "1_edit.xml").is_file()
        yt = getattr(j, "upload_title", "") or ""
        items = [("Open folder", self._open_folder, False, bool(done or j.project)),
                 ("Open in Premiere", self._open_premiere, False, bool(xml)),
                 ("Copy title", (self.yt_box._copy if yt else self.title_box._copy), False,       # noqa: SLF001
                  bool(yt or j.title)),
                 ("Swap RAW and competitor", self._swap, False, j.complete),
                 ("Run again (Thorough)", lambda: self.page.run_again(j), False, j.complete), None,
                 ("Remove", lambda: self.page.remove_job(j), True, j.state != J.RUNNING)]
        res = j.result or {}
        if res.get("label") == R.WRONG_PAIR or str(res.get("stop") or "").lower().startswith("the inputs look wrong"):
            # the quick pair check, or night 6's input checks ("almost as long as the RAW"), can be wrong: your call
            items.insert(5, ("Run anyway (skip the input checks)", lambda: self.page.run_again(j, j.mode, force=True),
                             False, j.complete))
        FadeMenu(self, items).popup(e.globalPos())

    # -- actions --
    def _mode(self, v: str) -> None:
        self.job.mode = v
        self.refresh()
        self.page.update_counts()

    def _speed(self) -> None:
        self.job.speed = 125.0 if abs(self.job.speed - 100.0) < 1e-6 else 100.0
        self.job.speed_from = "you"
        self.refresh()

    def _swap(self) -> None:
        self.page.swap_job(self.job)

    def _retry(self) -> None:
        """Try again (day 13): a run that stopped by itself -- the same video once more, in the same mode."""
        j = self.job
        if j.complete and j.state in (J.DONE, J.STOPPED):
            self.page.run_again(j, j.mode)

    def _pick(self, k: int) -> None:
        if k < len(self._pick_files):
            self.page.pick_raw(self.job, self._pick_files[k])

    def _template(self) -> None:
        from ..ui.template_editor import TemplateDialog
        j = self.job
        d = TemplateDialog(self, j.channel, j.title, j.headline, Path(j.competitor) if j.competitor else None, j.name)
        d.ed.headline_found.connect(lambda h: setattr(j, "headline", h))
        if d.exec():
            j.channel, j.title = d.ed.channel(), d.ed.title()
            j.headline = d.ed.headline() if d.ed.headline() is not None else j.headline
            if j.project is not None:
                from ..ui import background as BG
                folder, channel, title = j.project, j.channel, j.title

                def work() -> None:                       # project.json + title.txt: off the window's thread
                    from ..core import projects as PJ
                    PJ.load(folder).update(channel=channel or None, title=title or None)
                    from ..core import board
                    board.set_title(folder, title)                 # (was page.title_saved, on the window thread)
                BG.call(work, None, lambda _e: None)
            self.refresh()

    def _edit_upload_title(self) -> None:
        j = self.job
        if j.project is None:
            return
        text = ask_upload_title(self, getattr(j, "upload_title", ""))
        if text is None:
            return
        j.upload_title = text
        self.yt_box.set(text)
        save_upload_title(self.page.ctx, j.project, text)

    def _use_title(self, text: str) -> None:
        """Day 12: "Use as YouTube title" from the headline on the competitor's video."""
        j = self.job
        if j.project is None or not text:
            return
        j.upload_title = text
        self.yt_box.set(text)
        save_upload_title(self.page.ctx, j.project, text)

    def _open_folder(self) -> None:
        d = self.job.run_dir if self.job.run_dir is not None and Path(self.job.run_dir).exists() else self.job.project
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
        w = Worker(lambda: open_in_premiere(xml))          # Premiere's start is waited for: never in the window's thread
        w.finished_with.connect(self.page.say)
        w.failed.connect(lambda e: self.page.say(f"Premiere could not be opened: {e}"))
        self.page.workers.append(w)
        w.start()

    # -- looks --
    def set_scene(self, n: int, runs: int | None = None) -> None:
        """The slate: SCENE (its place in the queue) · TAKE · the date, and for a waiting row when it runs (day 12:
        the rows are shown newest first, so the run order is said on each: RUNS 1ST = the next one)."""
        j = self.job
        self.scene_n, self.runs = n, runs
        day = (getattr(j, "added", None) or datetime.now()).strftime("%d.%m.%Y")
        c = theme.CORAL
        run = ""
        if j.state == J.READY:
            run = (f"&nbsp;&nbsp;&nbsp;&nbsp;<span style='color:{theme.AMBER}'>WAITS FOR YOUR CHOICE</span>"
                   if J.needs_check(j) else
                   f"&nbsp;&nbsp;&nbsp;&nbsp;RUNS <span style='color:{c}'>{J.ordinal(runs).upper()}</span>"
                   if runs else "")
        self.slate.setText(f"<span style='color:{theme.DIM}'>SCENE <span style='color:{c}'>{n:02d}</span>"
                           f"&nbsp;&nbsp;&nbsp;&nbsp;TAKE <span style='color:{c}'>{getattr(j, 'take', 1)}</span>"
                           f"&nbsp;&nbsp;&nbsp;&nbsp;{day}{run}</span>")

    def runs_text(self) -> str:
        """'runs 2nd' (a waiting row), 'waits for your choice', or ''."""
        if self.job.state != J.READY:
            return ""
        if J.needs_check(self.job):
            return "waits for your choice"
        r = getattr(self, "runs", None)
        return f"runs {J.ordinal(r)}" if r else ""

    def _fading(self) -> None:
        eff = self.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            eff.setOpacity(1.0 - self.gone.value)

    def fade_out(self, then) -> None:
        if not Mo.enabled():
            then()
            return
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        self.gone.to(1.0)
        QTimer.singleShot(360, then)

    def refresh(self) -> None:
        j = self.job
        self.name.setText(j.name)
        if getattr(self, "_chan", None) != j.channel:
            self._chan = j.channel
            while self.chan_box.count():
                it = self.chan_box.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            if j.channel:
                self.chan_box.addWidget(W.channel_chip(j.channel, 12))
        self.pair.setText(pair_html(j))
        self.pair.setToolTip(f"RAW: {j.raw}\ncompetitor: {j.competitor}" if j.complete else "")
        self.swap.setVisible(j.complete)
        self.swap.setEnabled(j.complete and not getattr(j, "restart_swapped", False))
        checking = J.needs_check(j)
        self.check.setVisible(checking)
        if checking:
            self._pick_files = sorted([Path(j.raw), Path(j.competitor)], key=lambda f: f.name.lower())
            for b, f in zip((self.pick_a, self.pick_b), self._pick_files):
                s = j.raw_s if f == Path(j.raw) else j.comp_s
                b.setText(f"{short_file_name(f.name)} ({J.clock(s)}) is the RAW")
                b.setToolTip(str(f))
        busy = j.state in (J.RUNNING, J.DONE, J.STOPPED)
        self.mode.set(j.mode)
        self.mode.setEnabled(not busy)
        self.speed.setText(f"{j.speed:g} %")
        self.speed.setEnabled(not busy)
        self.tpl.setEnabled(not busy)
        for w in (self.mode, self.speed, self.tpl):      # a running / finished row: its results instead
            w.setVisible(not busy)
        self.tpl_line.setText(template_line(j))
        self.title_box.set(j.title)
        self.remove.setEnabled(j.state != J.RUNNING)
        self.mode_txt.setText("Fast" if j.mode == J.FAST else "Thorough")
        res = j.result or {}
        if j.state == J.READY and checking:
            prep = getattr(j, "preparing", "")
            self.pill.set("Check which is the RAW", (theme.AMBER, theme.AMBER_SOFT, theme.AMBER_LINE))
            self.step.setText(prep or "waits for your choice: which file is the RAW?")
            self.step.setStyleSheet(f"color: {theme.AMBER};")
            self.note.setText("")
            self.bar.state("waiting")
        elif j.state == J.READY:
            probs = j.problems()
            prep = getattr(j, "preparing", "")
            self.pill.set("waiting")
            if getattr(j, "restart_swapped", False):
                prep = prep or "starting again with the RAW and the competitor switched ..."
            self.step.setText(prep or ("waiting for Start" if not probs else "; ".join(probs)))
            self.step.setStyleSheet(f"color: {theme.AMBER if probs else theme.MUTED};")
            self.note.setText("from " + j.source if j.source.startswith("folder") else "")
            self.bar.state("waiting")
        elif j.state == J.RUNNING:
            self.pill.set("running")
            self.step.setText("stopping, to start again with the RAW and the competitor switched ..."
                              if getattr(j, "restart_swapped", False) else (j.progress or "starting"))
            self.step.setStyleSheet(f"color: {theme.MUTED};")
            self.note.setText("")
            self.bar.state("running")
            self.bar.set(j.percent)
        elif j.state in (J.DONE, J.STOPPED):
            lab = res.get("label", R.FAIL)
            self.pill.set(lab)
            took = fmt_s(res.get("seconds"))
            self.step.setText("done" + (f" in {took}" if took else "") if lab in R.GOOD else
                              ("stopped by you" if lab == R.STOPPED else "stopped"))
            self.step.setStyleSheet(f"color: {theme.MUTED};")
            self.note.setText(R.short(res) if lab not in (R.PASS,) else "")
            self.bar.state(lab)
            self.bar.set(1.0 if lab in R.GOOD else j.percent)
        if j.state == J.RUNNING:
            self.ring.start()
        else:
            self.ring.stop()
        if self._last_state != j.state or self._dates_cache is None:
            self._dates_cache = step_dates(j)
            self._last_state = j.state
        self.dates.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(
            f"<span style='color:{theme.DIM}'>{n} </span><span style='color:{theme.MUTED}'>{when(d)}</span>"
            for n, d in self._dates_cache))
        self.yt_box.setVisible(j.project is not None)          # a YouTube title belongs to a project
        self.yt_box.set(getattr(j, "upload_title", "") or "")
        known = j.project is not None and bool(getattr(j, "titles_known", False))
        self.titles.setVisible(known)
        if known:
            self.titles.set(getattr(j, "post_title", None), getattr(j, "video_headline", None))
        self.kind.setVisible(bool(getattr(j, "just_cuts", False)))
        done = j.state in (J.DONE, J.STOPPED) and j.run_dir is not None
        for b in (self.b_folder, self.b_prem, self.b_report):
            b.setVisible(done)
        self.b_retry.setVisible(j.state in (J.DONE, J.STOPPED) and j.complete and R.stopped_by_itself(res))
        self.b_prem.setEnabled(done and (Path(j.run_dir) / "1_edit.xml").is_file() if done else False)
        self.note.setVisible(bool(self.note.text()))
        self.remove.setVisible(j.state != J.RUNNING)
        self.update()

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        h = self.hover.value
        if self.check.isVisibleTo(self):                     # day 12: the amber "Check which is the RAW" card
            W.box(p, r, theme.AMBER_SOFT, theme.AMBER_LINE)
        else:
            W.box(p, r, Mo.mix(theme.PANEL, "#1a1617", h), Mo.mix(theme.LINE, theme.WINE_LINE, h))
        if h > 0:
            p.fillRect(QRectF(0, 0, 2, r.height()), Mo.alpha(theme.RED, h))
        W.sweep(p, r.adjusted(1, 1, -1, -1), self.light.value)
        if self.ring.running():
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = QConicalGradient(r.center(), -360.0 * self.ring.value)     # the reference: transparent 0-62 %,
            g.setColorAt(0.0, QColor(229, 72, 77, 0))                       # red at 80 %, coral at 90 %
            g.setColorAt(0.62, QColor(229, 72, 77, 0))
            g.setColorAt(0.80, QColor(theme.RED))
            g.setColorAt(0.90, QColor(theme.CORAL))
            g.setColorAt(1.0, QColor(255, 122, 89, 0))
            p.setPen(QPen(QBrush(g), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(0.75, 0.75, -0.75, -0.75))
        if self.wrap.active:
            x = -r.width() + 2 * r.width() * self.wrap.value
            g2 = QLinearGradient(QPointF(x, 0), QPointF(x + r.width(), 0))
            g2.setColorAt(0.0, QColor(63, 185, 80, 0))
            g2.setColorAt(0.5, QColor(63, 185, 80, 41))
            g2.setColorAt(1.0, QColor(63, 185, 80, 0))
            p.fillRect(r.adjusted(1, 1, -1, -1), QBrush(g2))
        p.end()


class QueueList(GlideList):
    """The rows in the order shown (day 12: running on top, then waiting newest first, then finished newest first --
    core.jobs.display_order), each gliding to its new place; the faint red glow of the row under the mouse painted
    behind them and the slot a dragged row would land in (a red line -- only among the waiting rows: dragging changes
    the run order)."""

    def __init__(self, page: "MatchCutsPage"):
        super().__init__(spacing=10)
        self.setObjectName("Transparent")
        self.page = page
        self.setAcceptDrops(True)
        self._slot: int | None = None

    def rows(self) -> list[JobRow]:
        return [w for w in self.items() if isinstance(w, JobRow)]

    def waiting_rows(self) -> list[JobRow]:
        return [r for r in self.rows() if r.job.state in (J.READY, J.WAITING)]

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        for row in self.rows():
            if row.hover.value > 0:
                W.soft_shadow(p, QRectF(row.geometry()), theme.RED, 0.10 * row.hover.value)
        if self._slot is not None:
            rows = self.waiting_rows()
            if rows:
                y = (self.target_y(rows[self._slot]) - 5 if self._slot < len(rows)
                     else self.target_y(rows[-1]) + rows[-1].height() + 5)
                p.fillRect(QRectF(0, y - 1, self.width(), 2), QColor(theme.RED))
        p.end()

    def _slot_at(self, y: float) -> int:
        """The slot among the WAITING rows (0 = above the top waiting row) nearest to ``y``."""
        rows = self.waiting_rows()
        for i, r in enumerate(rows):
            if y < self.target_y(r) + r.height() / 2:
                return i
        return len(rows)

    def dragEnterEvent(self, e) -> None:                  # noqa: N802
        if e.mimeData().hasFormat(ROW_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:                   # noqa: N802
        if e.mimeData().hasFormat(ROW_MIME):
            e.acceptProposedAction()
            self._slot = self._slot_at(e.position().y())
            self.update()

    def dragLeaveEvent(self, _e) -> None:                 # noqa: N802
        self.clear_slot()

    def dropEvent(self, e) -> None:                       # noqa: N802
        if not e.mimeData().hasFormat(ROW_MIME):
            return
        e.acceptProposedAction()
        job_id = int(bytes(e.mimeData().data(ROW_MIME)).decode("ascii"))
        slot = self._slot_at(e.position().y())
        self.clear_slot()
        self.page.drop_row(job_id, slot)

    def clear_slot(self) -> None:
        self._slot = None
        self.update()


class AskCard(W.Card):
    """'Which one is the RAW?' -- two videos about as long, the names not saying: click the RAW."""

    def __init__(self, page: "MatchCutsPage", a: Path, b: Path, sa: float | None, sb: float | None, decide,
                 why: str = ""):
        super().__init__(hover=False, line=theme.AMBER_LINE, margins=(16, 14, 16, 14))
        self.page = page
        t = W.label("Which one is the RAW?")
        t.setFont(W.font(15, 700))
        self.lay.addWidget(t)
        self.lay.addWidget(W.label((why + " -- " if why else "The two videos are about as long and their names "
                                    "don't say. ") + "Click the RAW (the episode or movie); the other one is the "
                                   "competitor.", "Muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        for f, s, other in ((a, sa, b), (b, sb, a)):
            btn = W.Button(f"{f.name}  ({J.clock(s)})")
            btn.setToolTip(str(f))
            btn.clicked.connect(lambda _=False, raw=f, comp=other: (decide(raw, comp), self.close_card()))
            row.addWidget(btn)
        row.addStretch(1)
        no = W.Button("Neither -- remove", "Ghost")
        no.clicked.connect(self.close_card)
        row.addWidget(no)
        self.lay.addLayout(row)

    def close_card(self) -> None:
        self.setParent(None)
        self.deleteLater()


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
        if r.get("reason_line"):                          # day 13: a stop -- why in plain words, then what to do
            sec("Why it stopped", [r.get("reason") or ""])
            sec("What to do", [r.get("todo") or ""])
            detail = str(r.get("reason_detail") or "")
            sec("From the log", [detail] if detail != str(r.get("reason") or "") else [])
        else:
            sec("Why it failed", r.get("failures") or [])
        sec("Note", r.get("notes") or [])
        sec("Check by hand (from the run's end summary)", [h.strip() for h in r.get("hand") or []])
        sec("Captions written as they are -- check them", r.get("doubtful") or [])
        sec("Run folder", [r.get("run_dir", "")])
        sec("Command", [r.get("command", "")])
        tb.setHtml("".join(parts) or "<p>No report: the run did not finish.</p>")
        lay.addWidget(tb, 1)
        row = QHBoxLayout()
        for text, path in (("Open report.md", r.get("report")), ("Open log", r.get("log"))):
            b = W.Button(text)
            b.setEnabled(bool(path) and Path(path).is_file())
            b.clicked.connect(lambda _=False, p=path: os.startfile(str(p)))   # noqa: S606
            row.addWidget(b)
        row.addStretch(1)
        close = W.Button("Close", "Primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def showEvent(self, e) -> None:                       # noqa: N802
        W.dark_title_bar(self)
        super().showEvent(e)


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        self.hovered: JobRow | None = None
        self._run_jobs: list[int] = []            # the jobs of this Start (the taskbar's progress)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 18, 0, 40)
        lay.setSpacing(18)
        from ..ui.motivation_strip import MotivationStrip
        self.motivation = MotivationStrip()
        self.motivation.goal_hit.connect(self._goal_hit)
        lay.addWidget(self.motivation)
        self.drop = W.DropZone("Drop videos here",
                               ["a RAW and a competitor = one video  ·  a folder of folders = one video each  ·  one "
                                "video alone waits for the next one"],
                               [("Choose videos...", self._pick_files), ("Choose a folder...", self._pick_folder)])
        self.drop.dropped.connect(self.add_paths)
        lay.addWidget(self.drop)
        self.wait_line = QWidget()
        self.wait_line.setObjectName("Transparent")
        wl = QHBoxLayout(self.wait_line)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(W.Square(theme.AMBER))
        self.wait_text = W.label("", "Muted")
        wl.addWidget(self.wait_text, 1)
        cancel = W.Button("Remove", "Ghost")
        cancel.clicked.connect(self._drop_waiting)
        wl.addWidget(cancel)
        self.wait_line.hide()
        lay.addWidget(self.wait_line)
        self.asks = QVBoxLayout()
        self.asks.setSpacing(10)
        lay.addLayout(self.asks)
        self.count = W.label("", "Small")
        self.b_start = W.Button("Start", "Primary", kbd="Space")
        self.b_start.clicked.connect(lambda: self.start())
        self.b_stop = W.Button("Stop")
        self.b_stop.clicked.connect(self.stop)
        self.b_clear = W.Button("Clear finished", "Ghost")
        self.b_clear.clicked.connect(self.clear_finished)
        lay.addWidget(W.SectionHeader("Queue", [self.count, self.b_clear, self.b_stop, self.b_start]))
        self.status = W.label("", "Muted", wrap=True, select=True)
        self.status.hide()
        lay.addWidget(self.status)
        self.queue = QueueList(self)
        self.list = self.queue                       # (the rows as shown: self.queue.rows())
        self.queue.inserted.connect(lambda _w: scroll_to_top(self))     # day 12: a new row -> the list's top
        lay.addWidget(self.queue)
        self.empty = W.label("The queue is empty. Drop videos above.", "Dim")
        lay.addWidget(self.empty)
        legend = QLabel(self._legend())
        legend.setObjectName("Dim")
        legend.setWordWrap(True)
        lay.addWidget(legend)
        lay.addStretch(1)
        self.update_counts()

    @staticmethod
    def _legend() -> str:
        k = (f"<span style='font-family:{theme.MONO};font-size:10px;color:{theme.MUTED};border:1px solid {theme.LINE2};"
             f"background:#111111'>&nbsp;%s&nbsp;</span>")
        return (f"<span style='color:{theme.DIM}'>Drag rows to change the order · right-click a row for more · "
                f"{k % 'Space'} start · {k % 'Del'} remove the row under the mouse · {k % 'Ctrl'}+{k % 'V'} paste a "
                "link</span>")

    # -- the motivation strip --
    def show_uploads(self, uploads, settings=None) -> None:
        s = settings or getattr(self.ctx, "settings", None)
        self.motivation.refresh(uploads, int(getattr(s, "daily_goal", 6) or 6),
                                int(getattr(s, "monthly_goal", 150) or 150))

    def goal_hit(self) -> bool:
        return bool(getattr(self.motivation, "hit", False))

    def _goal_hit(self, n: int) -> None:
        _toast(self.ctx, f"Daily goal hit — {n} Shorts today ✓")
        fn = getattr(self.ctx, "refresh_quote", None)
        if fn is not None:
            fn(True)

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
            res = J.jobs_from_drop(items, seconds_of, waiting, find_videos=batch.find_videos,
                                   speed_of=lambda folder: batch.video_speed(folder))
            if res.waiting is not None:
                seconds_of(res.waiting)          # read here, so the window only meets the cached length
            for ask in getattr(res, "ask", None) or []:
                for f in list(getattr(ask, "videos", None) or ask)[:2]:
                    seconds_of(Path(f))
            return res
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
            self._add_row(j)
            self._projectify(j)
        for pair in getattr(res, "ask", None) or []:
            self._ask(pair)
        self.waiting = res.waiting
        if self.waiting is not None:
            s = seconds_of(self.waiting)
            self.wait_text.setText(f"{self.waiting.name} ({J.clock(s)}) waits for its partner: drop the other video "
                                   "(the RAW or the competitor)")
        self.wait_line.setVisible(self.waiting is not None)
        msg = []
        if res.jobs:
            msg.append(f"{len(res.jobs)} video(s) added")
        asked = {(f"{q.name}: " if getattr(q, "name", "") else "") + str(getattr(q, "why", ""))
                 for q in (getattr(res, "ask", None) or [])}
        msg += [n for n in res.notes if n not in asked]         # a question is on its card, not said twice
        self.say("; ".join(msg) if msg else "")
        self.update_counts()
        if self.running and self.current is None:
            self._next()

    def _add_row(self, j: J.Job) -> JobRow:
        if getattr(j, "added", None) is None:
            j.added = datetime.now()
        if not hasattr(j, "take"):
            j.take = 1
        self.jobs.append(j)
        self._read_lengths(j)
        row = JobRow(self, j)
        self.rows[j.id] = row
        order = [x.id for x in J.display_order(self.jobs)]
        self.queue.insert(row, order.index(j.id))          # in its place at once; the rows under it glide down
        self._reorder()
        return row

    def _read_lengths(self, j: J.Job) -> None:
        """Day 12: the pair line's lengths (and the 30 % rule) -- read in the background; the row does not start
        before they are in."""
        if not j.complete or (j.comp_s is not None and j.raw_s is not None) or getattr(j, "reading_lengths", False):
            return
        j.reading_lengths = True
        comp, raw = Path(j.competitor), Path(j.raw)

        def work():
            return seconds_of(comp), seconds_of(raw)

        def got(r) -> None:
            j.reading_lengths = False
            if j.complete and Path(j.competitor) == comp and Path(j.raw) == raw:
                j.comp_s = r[0] if j.comp_s is None else j.comp_s
                j.raw_s = r[1] if j.raw_s is None else j.raw_s
            elif j.complete and Path(j.competitor) == raw and Path(j.raw) == comp:      # swapped meanwhile
                j.comp_s = r[1] if j.comp_s is None else j.comp_s
                j.raw_s = r[0] if j.raw_s is None else j.raw_s
            self._lengths_in(j)

        def bad(_e) -> None:
            j.reading_lengths = False
            self._lengths_in(j)
        from ..ui import background as BG
        BG.call(work, got, bad)

    def _lengths_in(self, j: J.Job) -> None:
        row = self.rows.get(j.id)
        if row is not None:
            row.refresh()
        self._reorder()
        self.update_counts()
        if J.needs_check(j):
            self.say(f"{j.name}: the two videos are almost the same length -- say which one is the RAW on its row")
            j.want_start = getattr(j, "want_start", False) or getattr(j, "autostart", False)
        elif getattr(j, "autostart", False) and not self.running:
            j.autostart = False
            self.start(confirm=False)
        if self.running and self.current is None:
            self._next()

    def _reorder(self) -> None:
        """Day 12: every row to its place in the order shown (core.jobs.display_order: running, then waiting newest
        first, then finished newest first) -- gliding there -- and the slates' numbers (SCENE: its place in the run
        order; RUNS 1ST ...: when a waiting row runs)."""
        rows = [self.rows[j.id] for j in J.display_order(self.jobs) if j.id in self.rows]
        self.queue.set_order(rows)
        self._renumber()

    def _projectify(self, j: J.Job) -> None:
        """Night 11: a RAW and a competitor dropped from anywhere always end up in a project -- a project folder
        with both files (copies of their own: core.jobs.project_for_job -- a hard link would change with your file if
        another video were later copied over its name), the edit into its edit folder, the board showing it. Done in
        the background on a copy of the job; the row says so and the job waits for it. Folder-of-folders (batch)
        drops and projects stay as they are."""
        in_project = j.complete and (Path(j.competitor).parent / "project.json").is_file()   # a project folder dropped
        if j.project is not None or not j.complete or not hasattr(J, "project_for_job") \
                or not (str(j.source).startswith("drop") or in_project):
            return
        j.preparing = "putting both files into the video's project folder ..."
        prog = {"text": ""}
        work_job = copy.copy(j)
        given = (Path(j.competitor), Path(j.raw))

        def say(role: str, share: float, text: str) -> None:
            prog["text"] = f"{'RAW' if role == 'raw' else 'competitor'}: {text}"

        def work():
            return J.project_for_job(work_job, None, progress=say), work_job

        t = QTimer(self)

        def tick() -> None:
            if prog["text"]:
                j.preparing = f"putting both files into the project ... {prog['text']}"
                row = self.rows.get(j.id)
                if row is not None:
                    row.refresh()
        t.timeout.connect(tick)
        t.start(300)

        def done(r) -> None:
            t.stop()
            folder, wj = r
            switched = (Path(j.competitor), Path(j.raw)) == (given[1], given[0])    # swapped / picked meanwhile
            if switched:
                j.competitor, j.raw = Path(wj.raw), Path(wj.competitor)
            else:
                j.competitor, j.raw = wj.competitor, wj.raw
            j.project = wj.project
            j.out_dir, j.run_name, j.source = wj.out_dir, wj.run_name, wj.source
            j.preparing = ""
            if j.roles_chosen and (switched or not wj.roles_chosen):
                self._save_roles(j, "picked")              # the project's roles as you chose them
            self._after_prepare(j, f"both files are in the project {Path(folder).name}")

        def failed(err: str) -> None:
            t.stop()
            j.preparing = ""                          # it still runs, from the files where they are
            self._after_prepare(j, f"not put into a project ({err}) -- it runs from the files where they are")
        from ..ui import background as BG
        BG.call(work, done, failed)

    def _after_prepare(self, j: J.Job, note: str) -> None:
        row = self.rows.get(j.id)
        if row is not None:
            row._dates_cache = None                     # noqa: SLF001 - its project's dates now
            row.refresh()
        self.say(f"{j.name}: {note}")
        b = (getattr(self.ctx, "pages", None) or {}).get("board")
        if b is not None and hasattr(b, "reload"):
            b.reload()
        if self.running and self.current is None:
            self._next()

    def _ask(self, ask) -> None:
        """A drop whose videos the names and lengths cannot tell apart: the question card "Which one is the RAW?"
        (``ask``: core.jobs.Ask -- its videos, why, and the job(s) once you picked -- or a plain pair)."""
        try:
            vids = [Path(v) for v in (getattr(ask, "videos", None) or ask)]
            a, b = vids[0], vids[1]
        except Exception:  # noqa: BLE001
            return
        if len(vids) == 2:          # day 12: a row like any other, amber, "Check which is the RAW" -- your pick
            sa, sb = seconds_of(a), seconds_of(b)          # (read with the drop, in the background: cached)
            raw, comp = (a, b) if (sa or 0.0) >= (sb or 0.0) else (b, a)
            if hasattr(ask, "jobs"):
                jobs = ask.jobs(raw)
            else:
                jobs = [J.Job(getattr(ask, "name", "") or J.job_name(comp, raw), comp, raw,
                              source=getattr(ask, "source", "drop") or "drop")]
            for j in jobs:
                j.comp_s, j.raw_s = seconds_of(Path(j.competitor)), seconds_of(Path(j.raw))
                j.ask_why = getattr(ask, "why", "")
            self._added_jobs(J.DropResult(jobs, self.waiting, []))
            return

        def decide(raw: Path, comp: Path) -> None:
            if hasattr(ask, "jobs"):
                jobs = ask.jobs(raw, seconds_of)
            else:
                j = J.Job(J.job_name(comp, raw), comp, raw)
                j.comp_s, j.raw_s = seconds_of(comp), seconds_of(raw)
                jobs = [j]
            self._added_jobs(J.DropResult(jobs, self.waiting, [f"{raw.name} is the RAW (your choice)"]))
        card = AskCard(self, a, b, seconds_of(a), seconds_of(b), decide, getattr(ask, "why", ""))
        self.asks.addWidget(card)

    def _renumber(self) -> None:
        places = J.run_places(self.jobs)
        for i, j in enumerate(self.jobs, start=1):
            row = self.rows.get(j.id)
            if row is not None:
                row.set_scene(i, places.get(j.id))

    def _drop_waiting(self) -> None:
        self.waiting = None
        self.wait_line.hide()

    def remove_job(self, job: J.Job) -> None:
        if job.state == J.RUNNING:
            return
        self.jobs = [j for j in self.jobs if j.id != job.id]
        row = self.rows.pop(job.id, None)

        def gone() -> None:
            if row is not None:
                self.queue.remove(row)                     # the rows under it glide up
                row.setParent(None)
                row.deleteLater()
            self._renumber()
            self.update_counts()
        if row is not None and row.isVisible():
            row.fade_out(gone)
        else:
            gone()

    def remove_row_under_mouse(self) -> None:
        """Del: the row under the mouse goes (not the running one)."""
        w = QApplication.widgetAt(QCursor.pos())
        while w is not None and not isinstance(w, JobRow):
            w = w.parentWidget()
        if isinstance(w, JobRow) and w.job.state != J.RUNNING:
            self.remove_job(w.job)
            _toast(self.ctx, "Removed")

    def move_job(self, job_id: int, slot: int) -> None:
        """The job to place ``slot`` of the RUN order (0 = the first; the queue's list): the next video to run
        changes; the rows glide to their places as shown."""
        j = next((x for x in self.jobs if x.id == job_id), None)
        if j is None:
            return
        old = self.jobs.index(j)
        if slot > old:
            slot -= 1
        if slot == old:
            return
        self.jobs.pop(old)
        self.jobs.insert(slot, j)
        self._reorder()
        _toast(self.ctx, "Order changed ✓")

    def drop_row(self, job_id: int, slot: int) -> None:
        """A waiting row dragged to ``slot`` among the waiting rows AS SHOWN (0 = the top one): it stays where you
        dropped it, and its run place changes with it (the waiting rows are shown in the run order upside down --
        core.jobs.move_in_display). Running and finished rows are never moved."""
        before = [j.id for j in self.jobs]
        self.jobs = J.move_in_display(self.jobs, job_id, slot)
        if [j.id for j in self.jobs] != before:
            self._reorder()
            _toast(self.ctx, "Order changed ✓")

    # -- day 12: RAW vs competitor, always your choice --
    def _save_roles(self, j: J.Job, how: str) -> None:
        """Your choice into the project's project.json (its roles switched when needed; nothing copied again), in
        the background."""
        if j.project is None or j.raw is None:
            return
        folder, raw_name = Path(j.project), Path(j.raw).name

        def work():
            from ..core import flow
            return flow.set_roles(folder, raw_name, how)
        from ..ui import background as BG
        BG.call(work, None, lambda e: self.say(f"{j.name}: your choice could not be saved in project.json ({e})"))

    def pick_raw(self, j: J.Job, raw: Path) -> None:
        """The amber card's "<file> is the RAW": the two switch when needed, your choice is remembered (the job and
        project.json), and the row may run now -- with the input checks skipped when the two are about as long
        (core.jobs.forced: your choice counts as Run anyway)."""
        try:
            J.choose_raw(j, Path(raw))
        except ValueError as e:
            self.say(str(e))
            return
        self._save_roles(j, "picked")
        row = self.rows.get(j.id)
        if row is not None:
            row.refresh()
        self._reorder()
        self.update_counts()
        _toast(self.ctx, f"{Path(raw).name} is the RAW ✓")
        if self.running and self.current is None:
            self._next()
        elif not self.running and (getattr(j, "want_start", False) or getattr(j, "autostart", False)):
            j.want_start = j.autostart = False
            self.start(confirm=False)

    def swap_job(self, j: J.Job) -> None:
        """⇄ Swap (and right-click "Swap RAW and competitor"): the RAW and the competitor change places -- your
        choice, remembered like a pick. A running video stops and starts again at once with the files switched (the
        files are already in its project folder: nothing is copied again; project.json's roles switch too). A
        finished one comes back as its next take with the files switched."""
        if not j.complete:
            return
        if j.state == J.RUNNING:
            if getattr(j, "restart_swapped", False):
                return
            j.restart_swapped = True
            row = self.rows.get(j.id)
            if row is not None:
                row.refresh()
            _toast(self.ctx, "Swapped — it starts again with the RAW and the competitor switched")
            if self.current is not None and self.current.job.id == j.id:
                self.current.stop()
            return
        if j.state in (J.DONE, J.STOPPED):
            self.run_again(j, j.mode, swap=True)
            return
        J.choose_raw(j, Path(j.competitor))
        self._save_roles(j, "swapped")
        row = self.rows.get(j.id)
        if row is not None:
            row.refresh()
        self._reorder()
        self.update_counts()
        _toast(self.ctx, "Swapped — RAW and competitor switched")
        if self.running and self.current is None:
            self._next()

    def run_again(self, job: J.Job, mode: str | None = None, force: bool = False, swap: bool = False) -> None:
        """Right-click "Run again (Thorough)": the same video once more, thorough, as its next take. "Run anyway"
        after a WRONG PAIR or a stop on the input checks (``force``): the same mode, those checks skipped
        (--force-inputs)."""
        j = J.Job(job.name, job.competitor, job.raw, mode=mode or J.THOROUGH, speed=job.speed, comp_s=job.comp_s,
                  raw_s=job.raw_s, source=job.source, channel=job.channel, title=job.title, headline=job.headline,
                  project=job.project, out_dir=job.out_dir, run_name=job.run_name, force_inputs=force,
                  roles_chosen=job.roles_chosen, roles_how=getattr(job, "roles_how", ""))
        j.take = getattr(job, "take", 1) + 1
        if swap:                                          # day 12: Swap on a finished row
            J.choose_raw(j, Path(j.competitor))
            self._save_roles(j, "swapped")
        J.name_jobs([j], [x.name for x in self.jobs])
        self._add_row(j)
        self.update_counts()
        _toast(self.ctx, "Queued again -- the RAW and the competitor switched" if swap else
               "Queued again -- the input checks skipped" if force else
               f"Queued again ({'Fast' if j.mode == J.FAST else 'Thorough'})")
        if self.running and self.current is None:
            self._next()

    def clear_finished(self) -> None:
        for j in [j for j in self.jobs if j.state in (J.DONE, J.STOPPED)]:
            self.remove_job(j)

    def title_saved(self, job: J.Job) -> None:
        """A project's title was typed on its row: title.txt next to project.json too."""
        try:
            from ..core import board
            board.set_title(job.project, job.title)
        except Exception:  # noqa: BLE001 - the board's module (night 11) may be missing
            pass

    def use_headline(self, job: J.Job, text: str) -> str:
        """Day 12: "Use as my headline" (the headline read on the competitor's video) on a queue row. Before the
        edit: the row's and the project's headline are set (template.png, when made already, is made again; the
        edit's start makes it from the headline anyway). While it runs: nothing changes (said). After an edit was
        made: asked first, then Change headline -- only template.png again, swapped into the edit, no new run.
        Returns what it did (flow.USE_*, or "" when nothing / cancelled)."""
        from ..core import flow
        from ..core import projects as PJ
        from ..ui import background as BG
        text = "\n".join(x.strip() for x in (text or "").splitlines() if x.strip())[:200]
        if not text:
            return ""
        if job.state == J.RUNNING:
            _toast(self.ctx, "The edit is running -- use the headline when it has finished", "note")
            return flow.USE_RUNNING
        made = job.state in (J.DONE, J.STOPPED) and (job.result or {}).get("label") in R.GOOD
        if made and job.project is not None:
            if not job.channel:
                _toast(self.ctx, "This video has no channel, so it has no template to change", "note")
                return ""
            if not self.ask_yes("Use as my headline",
                                f"Make the template again with this headline and swap it into the finished edit?\n\n"
                                f"{text}\n\nNo new run -- only template.png changes."):
                return ""
            folder, ch = Path(job.project), job.channel

            def changed(_r) -> None:
                job.title = text
                self._refresh_job(job)
                _toast(self.ctx, "New template made and swapped into the edit ✓")
                self._reload_board()
            BG.call(lambda: flow.change_headline(PJ.load(folder), text, ch), changed,
                    lambda e: _toast(self.ctx, f"The template could not be made: {e}", "fail"), work=True)
            return flow.USE_CHANGE
        job.title = text
        self._refresh_job(job)
        if job.project is not None:
            folder = Path(job.project)

            def done(r) -> None:
                _toast(self.ctx, "Headline set ✓" + (" -- template made again" if (r or {}).get("template") else
                                                     " -- the template uses it"))
                self._reload_board()
            BG.call(lambda: flow.set_headline(folder, text), done,
                    lambda e: _toast(self.ctx, f"The headline could not be saved: {e}", "fail"), work=True)
        else:
            _toast(self.ctx, "Headline set ✓ -- the template uses it")
        return flow.USE_SET

    def ask_yes(self, title: str, text: str) -> bool:
        """A Yes / No question (a test replaces it)."""
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _refresh_job(self, job: J.Job) -> None:
        row = self.rows.get(job.id)
        if row is not None:
            row.refresh()
        dl = (getattr(self.ctx, "pages", None) or {}).get("download")     # the same video's Download row, if any
        for it in getattr(dl, "items", None) or []:
            if getattr(it, "job", None) is job:
                it.title = job.title
                try:
                    dl.rows[it.id].refresh()
                except (AttributeError, KeyError, RuntimeError):
                    pass

    def _reload_board(self) -> None:
        b = (getattr(self.ctx, "pages", None) or {}).get("board")
        if b is not None and hasattr(b, "reload"):
            b.reload()

    # -- the queue --
    def ready(self) -> list[J.Job]:
        """The waiting jobs that may run (not the ones waiting for your choice of the RAW)."""
        return [j for j in self.jobs if j.state == J.READY and not J.needs_check(j)]

    def update_counts(self) -> None:
        n_ready = len(self.ready())
        n_check = sum(1 for j in self.jobs if J.needs_check(j))
        n_done = sum(1 for j in self.jobs if j.state in (J.DONE, J.STOPPED))
        bits = [f"{len(self.jobs)} video(s)"] + ([f"{n_ready} to run"] if n_ready else []) + \
               ([f"{n_check} to check"] if n_check else []) + ([f"{n_done} finished"] if n_done else [])
        self.count.setText("  ·  ".join(bits) if self.jobs else "")
        self.empty.setVisible(not self.jobs)
        self.b_start.setEnabled(not self.running and n_ready > 0)
        self.b_stop.setEnabled(self.running)
        self.b_clear.setEnabled(n_done > 0)
        self.ctx.set_strip_queue(len(self.jobs), self.running)

    def say(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def run_dir_of(self, j: J.Job) -> Path:
        """Where a job's run folder goes (a project's: its own folder, 'edit')."""
        return Path(j.out_dir or self.out_dir) / (j.run_name or j.name)

    def add_project_job(self, job: J.Job, autostart: bool = False) -> None:
        """A video from the Download tab (its project folder ready: downloaded and converted) joins the queue; with
        ``autostart`` the queue starts (no question asked: what looks wrong is said on the status line)."""
        J.name_jobs([job], [j.name for j in self.jobs])
        job.autostart = bool(autostart)          # day 12: started once its lengths are read -- unless the two are
        self._added_jobs(J.DropResult([job], self.waiting, []))      # about as long (then it waits for your choice)
        if autostart and not getattr(job, "reading_lengths", False):
            self._lengths_in(job)

    def start(self, confirm: bool = True) -> None:
        for j in self.jobs:                              # day 12: they run once you said which one is the RAW
            if J.needs_check(j):
                j.want_start = True
        jobs = self.ready()
        if not jobs or self.running:
            return
        probs = [f"{j.name}: {p}" for j in jobs for p in j.problems() + template_problems(j)]
        if self.waiting is not None:
            probs.append(f"{self.waiting.name}: one video alone -- no RAW (or no competitor) for it; it will not run")
        self.say("checking the videos ...")
        self.b_start.setEnabled(False)
        from match_cuts import batch
        pairs = [(j.name, Path(j.competitor), Path(j.raw)) for j in jobs if j.complete and not J.forced(j)]
        runs = [(j.name, Path(j.competitor), Path(j.raw), self.run_dir_of(j)) for j in jobs if j.complete]
        short = [j.name for j in jobs if j.complete and J.short_raw(j)]      # the form chose the RAW (day 12)
        w = Worker(lambda: (batch.input_problems(pairs, short), space.problems(runs)))
        w.finished_with.connect(lambda r: self._checked(probs, r[0], r[1], confirm))
        w.failed.connect(lambda _e: self._checked(probs, {}, [], confirm))
        self.workers.append(w)
        w.start()

    def _checked(self, probs: list, tool: dict, room: list | None = None, confirm: bool = True) -> None:
        for name, found in (tool or {}).items():
            mine = [p for p in probs if p.startswith(f"{name}: ")]
            probs += [f"{name}: {p}" for p in found      # (the competitor longer than the RAW: said once, the app's way)
                      if not ("longer than the RAW" in p and any("longer than the RAW" in m for m in mine))]
        probs += list(room or [])                         # night 10: a copy into another drive's run folder needs room
        if probs and confirm:                             # started by itself (Download tab): said, not asked
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
        self._reorder()
        self.running = True
        self.t_start = time.time()
        self._run_jobs = [j.id for j in self.jobs if j.state == J.READY and j.complete and not J.needs_check(j)]
        self.say("running -- you can add more videos; they join the queue" +
                 ("" if confirm or not probs else "  ·  worth a look: " + "; ".join(probs[:4])))
        self._next()

    def _next(self, first: J.Job | None = None) -> None:
        """The next video in the run order (``first``: this one now -- a video that started again after Swap)."""
        nxt = first if first is not None and J.runnable(first) else \
            next((j for j in self.jobs if J.runnable(j)), None)
        if nxt is None:
            if any(j.state == J.READY and j.complete and (getattr(j, "preparing", "") or
                                                          getattr(j, "reading_lengths", False)) for j in self.jobs):
                self.update_counts()                  # a video's files are still being put into its project
                return
            if self.swap_waiting():
                self.update_counts()                  # a swapped video waits for its old run to end: not done yet
                return
            self._finished()
            return
        if nxt.id not in self._run_jobs:
            self._run_jobs.append(nxt.id)
        nxt.state = J.RUNNING
        nxt.progress, nxt.percent = "starting", 0.0
        self.rows[nxt.id].refresh()
        self._reorder()                               # it glides to the top
        th = JobThread(nxt, self.converted, self.out_dir)
        th.progress.connect(self._progress)
        th.done.connect(self._done)
        self.current = th
        self.update_counts()
        self._taskbar()
        th.start()

    def _progress(self, job_id: int, text: str, share: float) -> None:
        j = next((j for j in self.jobs if j.id == job_id), None)
        if j is None:
            return
        j.progress, j.percent = text, share
        self.rows[j.id].refresh()
        self._taskbar()

    def _taskbar(self) -> None:
        fn = getattr(self.ctx, "set_progress", None)
        if fn is None or not self._run_jobs:
            return
        mine = [j for j in self.jobs if j.id in self._run_jobs]
        if not mine:
            return
        done = sum(1.0 if j.state in (J.DONE, J.STOPPED) else (j.percent if j.state == J.RUNNING else 0.0)
                   for j in mine)
        fn(max(0.0, min(1.0, done / len(mine))))

    def _done(self, job_id: int, res: dict) -> None:
        j = next((j for j in self.jobs if j.id == job_id), None)
        th = self.current
        self.current = None
        if th is not None:
            th.wait(2000)
        if j is not None and getattr(j, "restart_swapped", False):
            self._restart_when_free(j, th)
            return
        if j is not None:
            j.result = res
            j.state = J.STOPPED if res.get("label") == R.STOPPED else J.DONE
            j.edited = datetime.now()
            if res.get("run_dir"):
                j.run_dir = Path(res["run_dir"])
            row = self.rows.get(j.id)
            if row is not None:
                row.refresh()
                if res.get("label") in R.GOOD:
                    row.wrap.play()
            self._reorder()                           # it glides to the top of the finished rows
            more = any(x.state == J.READY and x.complete for x in self.jobs)
            lab = res.get("label", "")
            if more:
                _toast(self.ctx, "Video done ✓" if lab == R.PASS else ("Done, with a note" if lab == R.OK_NOTE else
                                                                       f"{j.name}: {lab}"),
                       "ok" if lab == R.PASS else ("note" if lab == R.OK_NOTE else "fail"))
            fn = getattr(self.ctx, "project_done", None)
            if fn is not None and j.project is not None and lab in R.GOOD:
                fn(j)
            from .learn_tool import after_edit        # day 12: problems_queue.md / "Waiting for your fix"
            after_edit(self.ctx, j, res)
        self._taskbar()
        if self.running:
            self._next()
        else:
            self.update_counts()

    RESTART_POLL_MS = 250
    RESTART_TRIES = 2400                            # 10 min: the old run's thread always ends long before

    def _restart_when_free(self, j: J.Job, th, tries: int = 0) -> None:
        """The swapped run starts again only once the old run's thread has ended -- it releases the project's edit
        lock last, so a restart before that could find the lock still there."""
        done = getattr(th, "isFinished", None)
        if th is not None and done is not None and not done() and tries < self.RESTART_TRIES:
            QTimer.singleShot(self.RESTART_POLL_MS, lambda: self._restart_when_free(j, th, tries + 1))
            return
        self._restart_swapped(j)

    def _restart_swapped(self, j: J.Job) -> None:
        """Swap pressed while it ran: its run has stopped -- the same row starts again at once with the RAW and the
        competitor switched (the same files: nothing copied again; project.json's roles switched)."""
        j.restart_swapped = False
        J.choose_raw(j, Path(j.competitor))
        j.state, j.result, j.progress, j.percent, j.run_dir = J.READY, {}, "", 0.0, None
        j.take = getattr(j, "take", 1) + 1
        self._save_roles(j, "swapped")
        row = self.rows.get(j.id)
        if row is not None:
            row.refresh()
        self._reorder()
        self._taskbar()
        if self.running and self.current is None:
            self._next(first=j)
        else:
            self.update_counts()        # (another video started meanwhile: this one runs after it)

    def _finished(self) -> None:
        was = self.running
        self.running = False
        self.update_counts()
        if not was:
            return
        done = [j for j in self.jobs if j.id in self._run_jobs and j.state in (J.DONE, J.STOPPED) and j.result]
        counts: dict[str, int] = {}
        for j in done:
            counts[j.result.get("label", "?")] = counts.get(j.result.get("label", "?"), 0) + 1
        text = ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing ran"
        took = fmt_s(time.time() - self.t_start)
        self.say(f"the queue is done: {text} ({took})")
        n = len(done)
        QTimer.singleShot(700, lambda: _toast(self.ctx, f"That's a wrap — {n} video{'s' if n != 1 else ''}"))
        self.ctx.notify("Match cuts: the queue is done", f"{text} -- {took}")
        self._run_jobs = []
        fn = getattr(self.ctx, "set_progress", None)
        if fn is not None:
            fn(None)

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self.current is not None:
            self.current.stop()
        self.say("stopped -- the videos not run yet stay in the queue")
        self.update_counts()

    def swap_waiting(self) -> bool:
        """A video swapped while it ran waits for its old run's thread to end before it starts again."""
        return any(getattr(j, "restart_swapped", False) for j in self.jobs)

    def busy(self) -> bool:
        return self.running and (self.current is not None or self.swap_waiting())


class MatchCutsTool(Tool):
    key = "match_cuts"
    title = "Match cuts"

    def page(self, ctx) -> QWidget:
        return MatchCutsPage(ctx)
