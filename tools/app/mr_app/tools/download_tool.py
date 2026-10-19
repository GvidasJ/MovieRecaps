"""The Download tab (night 10; day 12: ONE "New video" form). Paste the RAW -- a YouTube link, a magnet link / .torrent
or a video file, recognised by itself (a small YOUTUBE / TORRENT / FILE label) -- and, optionally, the competitor (a
link or a file); pick the channel (remembered), type the headline (two lines: Enter = line 2) and the YouTube title.
The "Will make" line says what it will make before anything is pressed:

* RAW + competitor -> the **Full Match cuts edit**: download both, convert both, template, the Match cuts edit;
* only a RAW (a link or a file) -> **Just cuts**: download, convert, then a Premiere edit of the whole video cut at
  every scene change (match_cuts.just_cuts); **Add a competitor** on the finished row makes it a full run without
  downloading the RAW again;
* a torrent without its competitor is not allowed: the form asks for the competitor.

Each video is a row with one gliding bar per step and its finish time (green done, blue running). The steps WAIT FOR
YOUR BUTTONS unless **Automatic** is ON (remembered): Download finished -> Convert / Open folder; Convert finished ->
Send to Match cuts (or Make the cuts) / Open folder; then the template and the edit run by themselves. The row's
bottom line says what it waits for. No headline: Automatic ON uses the channel's default template as it is (amber
"default template", **Change headline** makes only template.png again and swaps it into the edit); Automatic OFF: the
Template step turns amber "needs a headline" with the two-line box and **Make the template**. A failed step stops the
row with a red pill and **Try again** continues from that step. "Turn off the PC when everything is done": after the
last download / convert / edit in the whole app, a 60-second warning with Cancel, then the PC turns off (never while
Premiere has unsaved work or a render runs).

Rows: running first, then waiting (newest first), then finished (newest first); a row that changes place glides; a
new row scrolls the list to the top. The run order stays the order the videos were added. Every step is core.flow's,
so the command line (steps.py) and the board do exactly the same."""
from __future__ import annotations

import itertools
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QThread, QTimer,
                            Signal)
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import Tool
from ..core import jobs as J
from ..core import newvideo as NV
from ..core import paths
from ..ui import motion as M
from ..ui import theme
from ..ui import widgets as W
from ..ui.titles_block import TitlesBlock

_ids = itertools.count(1)
ROLE_NAMES = {"competitor": "Competitor", "raw": "RAW"}

# item states
QUEUED, WORKING, READY, FAILED, STOPPED, SENT = "queued", "working", "ready", "failed", "stopped", "sent"
GATED, DONE = "gated", "done"

# step states
S_WAIT, S_RUN, S_DONE, S_FAIL, S_NOTE, S_SKIP = "waiting", "running", "done", "failed", "note", "skipped"

STEPS_FULL = [("download", "Download"), ("convert", "Convert ×2"), ("template", "Template"), ("edit", "Edit")]
STEPS_JUST = [("download", "Download"), ("convert", "Convert"), ("cuts", "Cuts")]

# what a row waits for
G_CONVERT, G_SEND, G_HEADLINE, G_COMP = "convert", "send", "headline", "competitor"


@dataclass
class Step:
    state: str = S_WAIT
    share: float = 0.0
    text: str = ""
    finished: datetime | None = None


@dataclass
class Item:
    """One video in the Download / New video list (a project folder once its link was read)."""
    competitor_url: str
    raw_url: str = ""
    raw_file: Path | None = None
    channel: str = ""
    title: str = ""                         # YOUR headline (a line break: line 1 / line 2)
    mode: str = J.FAST
    speed: float = 100.0
    edit: bool = True                       # (night 10) when ready: into the Match cuts queue
    state: str = QUEUED
    project: Path | None = None
    name: str = ""
    files: dict = field(default_factory=lambda: {r: [0.0, "waiting"] for r in ROLE_NAMES})
    headline: str | None = None             # the competitor's headline as the title check read it (OCR)
    message: str = ""
    error: str = ""
    upload_title: str = ""                  # night 11: the title the Short is uploaded with (optional)
    id: int = field(default_factory=lambda: next(_ids))
    # day 12
    kind: str = NV.FULL                     # FULL / JUST_CUTS
    raw_torrent: str = ""                   # a magnet link or a .torrent file (the torrent engine downloads it)
    comp_file: Path | None = None           # the competitor as a file
    added: float = field(default_factory=time.time)
    started: datetime | None = None
    steps: dict = field(default_factory=dict)
    gate: str = ""                          # what the row waits for (G_*), "" = nothing
    failed_step: str = ""
    post_title: str | None = None           # section 7: None = not read yet
    video_headline: str | None = None
    default_template: bool = False
    job: Any = None                         # the Match cuts queue's job (the full edit)
    result: dict = field(default_factory=dict)
    roles: tuple = ()                       # only these files (Add a competitor: ("competitor",))
    rename_note: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = {k: Step() for k, _ in self.step_list()}

    def step_list(self) -> list[tuple[str, str]]:
        return STEPS_JUST if self.kind == NV.JUST_CUTS else STEPS_FULL

    def group(self) -> int:
        if self.state == WORKING or any(s.state == S_RUN for s in self.steps.values()):
            return NV.RUNNING
        if self.state in (DONE, FAILED, STOPPED):
            return NV.FINISHED
        return NV.WAITING


def save_title(folder, title: str | None) -> None:
    """The title in the project's title.txt too (night 11: "I forget titles" -- the board and every row show it)."""
    if not folder or not (title or "").strip():
        return
    try:
        from ..core import board
        board.set_title(Path(folder), title)
    except Exception:  # noqa: BLE001 - the title is in project.json anyway
        pass


def estimate_bytes(li) -> int:
    """The room a link needs (download.LinkInfo): its chosen formats' size (or bitrate x length) -- about 4x that
    when the picture is VP9 / AV1 and will be converted (the original and a visually lossless H.264 up to ~3x as
    big sit side by side until the check: a 1.08 GB 4K VP9 became 2.9 GB tonight), else 1.1x."""
    try:
        from ..core import download as D
        ch = D.pick_formats(li.info.get("formats") or ([li.info] if li.info.get("url") else []))
        size = sum(D._expected(f, li.duration) for f in ch.parts)        # noqa: SLF001 - the same package
        return int(size * (1.1 if ch.video_kind in ("h264", "hevc") else 4.0))
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------------------------------------------------
# the work: one stage (or several) of one video, in the background
# ---------------------------------------------------------------------------------------------------------------------

class StageThread(QThread):
    """Stages of one video in the background (core.flow): "download" (read the links, make the project folder,
    download / place the files), "convert" (both converted; the competitor's headline read; the titles from the post
    and on the video), "template" (the channel's template with your headline -- or, ``default``, the channel's
    default template as it is), "cuts" (Just cuts). Signals: ``named(id, folder)``, ``progress(id, role, share,
    text)``, ``done(id, result)`` -- {'ok', 'stage' (the failed one), 'error' (plain words), 'headline' ...}."""
    named = Signal(int, str)
    progress = Signal(int, str, float, str)
    done = Signal(int, dict)

    def __init__(self, item: Item, stages: tuple[str, ...] = ("download", "convert", "template"),
                 default_template: bool = False):
        super().__init__()
        self.item = item
        self.stages = tuple(stages)
        self.default_template = default_template
        self.cancel = threading.Event()
        self.stage = ""

    def run(self) -> None:
        from ..core import flow
        it = self.item
        out: dict = {"ok": True, "stages": list(self.stages)}
        try:
            for st in self.stages:
                self.stage = st
                if self.cancel.is_set():
                    raise flow.StepError("stopped by you")
                getattr(self, f"_{st}")(out)
            out["project"] = str(it.project) if it.project else ""
            self.done.emit(it.id, out)
        except Exception as e:  # noqa: BLE001 - said in plain words
            msg = "stopped by you" if self.cancel.is_set() else NV.plain_error(e)
            self.done.emit(it.id, {**out, "ok": False, "error": msg, "stage": self.stage,
                                   "project": str(it.project) if it.project else ""})

    def _say(self, role: str, share: float, text: str) -> None:
        self.progress.emit(self.item.id, role, float(share), str(text))

    def _project(self):
        from ..core import projects as PJ
        return PJ.load(self.item.project)

    # -- the stages --
    def _download(self, out: dict) -> None:
        from ..core import flow
        from ..core import projects as PJ
        it = self.item
        p = self._create() if it.project is None else PJ.load(it.project)
        if it.roles:
            flow.download_step(p, self._say, self.cancel, roles=tuple(it.roles))
        elif p.step("download").get("state") != PJ.DONE:
            flow.download_step(p, self._say, self.cancel)

    def _convert(self, out: dict) -> None:
        from ..core import flow
        from ..core import projects as PJ
        it = self.item
        p = self._project()
        if it.roles:
            flow.convert_step(p, self._say, self.cancel, roles=tuple(it.roles))
        elif p.step("convert").get("state") != PJ.DONE:
            flow.convert_step(p, self._say, self.cancel)
        if self.cancel.is_set():
            raise flow.StepError("stopped by you")
        p = self._project()
        comp = p.file("competitor")
        if comp is not None and comp.is_file() or it.kind == NV.FULL:
            self._say("competitor", 1.0, "reading its headline ...")
            try:
                tc = flow.title_check_step(p, title=it.title or "")
            except Exception as e:  # noqa: BLE001 - a help, never a stop
                tc = {"competitor_headline": "", "message": f"the headline could not be read ({e})"}
            out["headline"], out["check"] = tc.get("competitor_headline"), tc.get("message", "")
            if comp is not None and comp.is_file():
                self._titles(p, comp, out)
        conv = self._project().data.get("steps", {}).get("convert") or {}
        for role in ROLE_NAMES:
            st = conv.get(role) or {}
            if isinstance(st, dict) and st:
                self._say(role, 1.0, flow.convert_text(st))

    def _titles(self, p, comp: Path, out: dict) -> None:
        """Section 7: the headline written on the competitor's video (the vision model, its own process), saved with
        the post's title; the folder named from them when its name says nothing."""
        from ..core import titles as TT
        it = self.item
        self._say("competitor", 1.0, "reading the headline on the video ...")
        head = TT.read_on_video(comp)
        post = it.post_title if it.post_title is not None else (p.data.get("post_title") or "")
        out["video_headline"], out["post_title"] = head, post
        try:
            TT.save(p, post, head)
            if TT.useless(it.name.split("_", 1)[-1].replace("-", " ")) or p.data.get("generic_name"):
                p2, note = TT.rename_folder(p, head, post)
                out["rename"] = note
                if p2.folder != p.folder:
                    it.project = p2.folder
                    self.named.emit(it.id, str(p2.folder))
        except Exception as e:  # noqa: BLE001 - the titles are a help
            out["rename"] = f"the titles could not be saved ({e})"

    def _template(self, out: dict) -> None:
        from ..core import flow
        it = self.item
        if not it.channel:
            return
        p = self._project()
        if (it.title or "").strip():
            self._say("raw", 1.0, "making your channel's template ...")
            try:
                flow.template_step(p, it.channel, it.title)
            except Exception as e:  # noqa: BLE001
                if it.kind == NV.FULL and not self.default_template and self.stages == ("download", "convert",
                                                                                         "template"):
                    return                                  # (night 11: the edit makes it again at its start)
                raise
            out["template"] = "made"
        elif self.default_template:
            self._say("raw", 1.0, "no headline: the channel's default template ...")
            flow.default_template_step(p, it.channel)
            out["template"] = "default"

    def _cuts(self, out: dict) -> None:
        from ..core import flow
        p = self._project()
        p.update(channel=self.item.channel or None, title=self.item.title or None)
        res = flow.just_cuts_step(p, self._say, self.cancel)
        out["result"] = res
        if res.get("label") not in ("PASS", "OK with a note"):
            raise flow.StepError((res.get("failures") or ["the cuts were not made"])[0])

    def _create(self):
        """Read the links (the competitor's title names the folder), check the free space, make the project
        folder."""
        from ..core import download as D
        from ..core import flow, space
        from ..core import titles as TT
        it = self.item
        need = 0
        name = ""
        info: dict = {}
        if it.competitor_url:
            self._say("competitor", 0.0, "reading the link ...")
            try:
                li = D.probe_link(it.competitor_url, cancel=self.cancel)
            except D.DownloadError as e:
                self._say("competitor", 0.0, f"FAILED: {e.plain}")
                raise flow.StepError(f"competitor: {e.plain}") from e
            need += estimate_bytes(li)
            info = dict(getattr(li, "info", None) or {})
            info.setdefault("title", li.title)
            self._say("competitor", 0.0, f"{li.title[:70]} -- {D.clock(li.duration)} ({li.why})")
            it.post_title = TT.post_title(info)
            name = it.post_title or li.title
        elif it.comp_file is not None:
            it.post_title = ""
            name = Path(it.comp_file).stem
        if it.raw_url:
            self._say("raw", 0.0, "reading the link ...")
            try:
                lr = D.probe_link(it.raw_url, cancel=self.cancel)
            except D.DownloadError as e:
                self._say("raw", 0.0, f"FAILED: {e.plain}")
                raise flow.StepError(f"RAW: {e.plain}") from e
            need += estimate_bytes(lr)
            self._say("raw", 0.0, f"{lr.title[:70]} -- {D.clock(lr.duration)} ({lr.why})")
            if not name:
                name = lr.title
        elif it.raw_file is not None:
            n = space.size(it.raw_file)
            need += 0 if space.same_drive(it.raw_file, paths.projects_root()) else n     # copied, not linked
            try:                                              # converted next to it first: room for the new file
                from ..core import convert as C
                how = C.plan(C.inspect(it.raw_file)).how
                need += {"encode": 3 * n, "rewrap": n}.get(how, 0)
            except Exception:  # noqa: BLE001 - not readable: the convert step says so
                pass
            self._say("raw", 0.0, f"the file {Path(it.raw_file).name} ({D.size_text(n)})")
            if not name:
                name = Path(it.raw_file).stem
        root = paths.projects_root()
        free = space.free_bytes(root)
        want = int(need) + space.GB                        # (estimate_bytes counts the conversion's copy too)
        if free is not None and need and free < want:
            raise flow.StepError(f"the disk is full: these videos need about {want / space.GB:.1f} GB on "
                                 f"{space.drive_name(root)} while they are downloaded and converted, only "
                                 f"{free / space.GB:.1f} GB is free")
        generic = TT.useless(name)
        p = flow.new_project(it.competitor_url, it.raw_url or None, it.raw_file, it.channel or None,
                             it.title or None, root=root, name=None if generic else name,
                             just_cuts=it.kind == NV.JUST_CUTS, competitor_file=it.comp_file)
        p.update(post_title=it.post_title, generic_name=generic, kind=it.kind)
        it.project = p.folder
        save_title(p.folder, it.title)
        if it.upload_title:
            try:
                from ..core import board
                board.set_upload_title(p.folder, it.upload_title)
            except Exception:  # noqa: BLE001 - kept in the row; Edit on the board sets it again
                pass
        self.named.emit(it.id, str(p.folder))
        return p


class PrepareThread(StageThread):
    """(night 10's name) download -> convert -> template of one video in one go."""


# ---------------------------------------------------------------------------------------------------------------------
# small widgets of the form
# ---------------------------------------------------------------------------------------------------------------------

DET_COLOURS = {NV.YOUTUBE: ("#ff8a8d", "#5a2226", theme.RED_SOFT), NV.TORRENT: ("#c4b5fd", "#3b2f63", "#1a1530"),
               NV.FILE: (theme.BLUE, theme.BLUE_LINE, theme.BLUE_SOFT), NV.LINK: (theme.BLUE, theme.BLUE_LINE,
                                                                                 theme.BLUE_SOFT),
               "ok": (theme.GREEN, theme.GREEN_LINE, theme.GREEN_SOFT), "": (theme.DIM, theme.LINE2, "transparent")}


class DetLabel(QLabel):
    """The small label after a field: YOUTUBE / TORRENT / FILE / LINK / EMPTY (the reference's ``.det``)."""

    def __init__(self, text: str = "EMPTY"):
        super().__init__(text)
        self.setFont(W.mono(9.5, 600, 1.0))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(84)
        self.kind = None
        self.set_kind("")

    def set_kind(self, kind: str, text: str | None = None) -> None:
        self.kind = kind
        fg, line, bg = DET_COLOURS.get(kind, DET_COLOURS[""])
        self.setText(text if text is not None else NV.LABELS.get(kind, "EMPTY"))
        self.setStyleSheet(f"color: {fg}; border: 1px solid {line}; background-color: {bg}; padding: 3px 8px;")


class HeadlineBox(QPlainTextEdit):
    """The two-line headline box: Enter starts line 2 (a second Enter does nothing), emoji welcome."""
    changed = Signal(str)

    def __init__(self, placeholder: str = "The fact that prison changed\nhim this much is hilarious 🔒😭"):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        fm = self.fontMetrics()
        self.setFixedHeight(fm.lineSpacing() * 2 + 18)
        self.textChanged.connect(lambda: self.changed.emit(self.text()))

    def keyPressEvent(self, e) -> None:                    # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and "\n" in self.toPlainText():
            return                                           # two lines at most
        super().keyPressEvent(e)

    def insertFromMimeData(self, src) -> None:            # noqa: N802 - a pasted headline keeps two lines at most
        txt = NV.headline_text(src.text()) if src.hasText() else ""
        if txt:
            if "\n" in self.toPlainText():
                txt = txt.replace("\n", " ")
            self.insertPlainText(txt)

    def text(self) -> str:
        return NV.headline_text(self.toPlainText())

    def set_text(self, text: str) -> None:
        self.setPlainText(NV.headline_text(text))


class DotButton(W.Button):
    """A channel's toggle with its colour dot (the reference's ``.seg2 .dot``)."""

    def __init__(self, name: str):
        super().__init__("    " + name, "Seg")
        self.channel = name
        self.setCheckable(True)

    def paintEvent(self, e) -> None:                      # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        tw = QFontMetrics(self._font()).horizontalAdvance(self.text())
        x = (self.width() - tw) / 2 + 2
        p.fillRect(QRectF(x, self.height() / 2 - 3.5, 7, 7), QColor(theme.channel_colour(self.channel)))
        p.end()


class ChannelButtons(QWidget):
    changed = Signal(str)

    def __init__(self, names: list[str], value: str):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.buttons: dict[str, DotButton] = {}
        for n in names:
            b = DotButton(n)
            b.clicked.connect(lambda _=False, k=n: self.set(k, emit=True))
            lay.addWidget(b)
            self.buttons[n] = b
        lay.addStretch(1)
        self.value = ""
        self.set(value)

    def set(self, value: str, emit: bool = False) -> None:
        self.value = value if value in self.buttons else (next(iter(self.buttons), ""))
        for k, b in self.buttons.items():
            b.setChecked(k == self.value)
        if emit:
            self.changed.emit(self.value)


class AutoSwitch(W.Button):
    """"Automatic: ON / OFF" with its small switch (the reference's ``#autoBtn``): green when ON. The knob glides
    (a state change, not a hover)."""

    def __init__(self, on: bool = False):
        super().__init__("", "Mini")
        self.setCheckable(True)
        self.knob = M.Fader(self, self.update, 350, 350)
        self.setChecked(on)
        self.knob.set(1.0 if on else 0.0)
        self.toggled.connect(lambda v: (self.knob.to(1.0 if v else 0.0), self._words()))
        self._words()

    def _words(self) -> None:
        self.setText("      Automatic: " + ("ON" if self.isChecked() else "OFF"))

    def sizeHint(self) -> QSize:                          # noqa: N802
        s = super().sizeHint()
        return QSize(s.width() + 8, max(s.height(), 26))

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        on = self.isChecked()
        k = self.knob.value
        h = self.hover.value
        W.box(p, r, M.mix(M.mix("#141414", theme.GREEN_SOFT, k), "#1a1a1a", h * 0.3),
              M.mix(theme.LINE2, theme.GREEN_LINE, k))
        sw = QRectF(10, r.center().y() - 7, 26, 14)
        p.fillRect(sw, M.mix("#2a2a2a", theme.GREEN, k))
        p.fillRect(QRectF(sw.left() + 2 + 12 * k, sw.top() + 2, 10, 10), M.mix("#888888", "#ffffff", k))
        p.setFont(self._font())
        p.setPen(M.mix(theme.MUTED, theme.GREEN, k) if on or k > 0 else QColor(theme.MUTED))
        p.drawText(QRectF(sw.right() + 8, r.top(), r.width() - sw.right() - 8, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "Automatic: " + ("ON" if on else "OFF"))
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# a row: one video and its steps
# ---------------------------------------------------------------------------------------------------------------------

def _hm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else ""


class StepCell(QWidget):
    """One step: its name, its own gliding bar and its finish time ("12:41" green when done, "34 %" blue while it
    runs, amber "needs a headline", red "failed")."""

    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("Transparent")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self.name = QLabel(label.upper())
        self.name.setFont(W.mono(9.5, 600, 1.0))
        lay.addWidget(self.name)
        self.bar = W.GlideBar(4)
        lay.addWidget(self.bar)
        self.small = QLabel("")
        self.small.setFont(W.mono(10.5))
        self.small.setMinimumHeight(14)
        lay.addWidget(self.small)

    def set(self, st: Step) -> None:
        colour = {S_RUN: theme.BLUE, S_DONE: theme.GREEN, S_FAIL: theme.RED, S_NOTE: theme.AMBER}.get(st.state,
                                                                                                      theme.DIM)
        self.name.setStyleSheet(f"color: {colour};")
        self.bar.state({S_RUN: "running", S_DONE: "pass", S_FAIL: "fail", S_NOTE: "note"}.get(st.state, "waiting"))
        self.bar.set(1.0 if st.state in (S_DONE, S_NOTE) and st.share <= 0 else st.share)
        if st.state == S_RUN:
            txt = f"{int(round(100 * st.share))} %" if st.share > 0 else (st.text or "starting")
        elif st.state in (S_DONE, S_NOTE):
            txt = (st.text + " · " if st.text else "") + _hm(st.finished) if st.text or st.finished else "done"
        elif st.state == S_FAIL:
            txt = "failed"
        elif st.state == S_SKIP:
            txt = st.text or "not needed"
        else:
            txt = st.text
        self.small.setText(txt)
        self.small.setStyleSheet(f"color: {colour if st.state != S_WAIT else theme.DIM};")


class VideoRow(W.Card):
    """A video in the list (the reference's ``.pipe``): its name, channel and kind; one bar per step; the pair; the
    two real titles; what it waits for and its buttons."""

    def __init__(self, page: "DownloadPage", item: Item):
        super().__init__(margins=(16, 14, 16, 14), spacing=10)
        self.page, self.item = page, item
        top = QHBoxLayout()
        top.setSpacing(10)
        self.name = W.label("", "Name")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.name, 1)
        self.chan = W.label("", "Small")
        top.addWidget(self.chan, 0, Qt.AlignmentFlag.AlignVCenter)
        self.kind = W.Pill("Full Match cuts")
        top.addWidget(self.kind, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pill = W.Pill("waiting")
        top.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lay.addLayout(top)
        self.steps_row = QHBoxLayout()
        self.steps_row.setSpacing(6)
        self.cells: dict[str, StepCell] = {}
        self.lay.addLayout(self.steps_row)
        self._build_steps()
        self.pair = W.label("", "Dim", wrap=True, select=True)
        self.lay.addWidget(self.pair)
        self.titles = TitlesBlock()
        self.titles.use_headline.connect(self._use_headline)
        self.titles.use_title.connect(self._use_title)
        self.titles.copied.connect(lambda _t: self.page.toast("Copied ✓"))
        self.lay.addWidget(self.titles)
        self.mine = W.TitleBox(item.title, "Headline")
        self.mine.copied.connect(lambda _t: self.page.toast("Headline copied ✓"))
        self.lay.addWidget(self.mine)
        # what it waits for: the headline box / the competitor's link
        self.ask = QWidget()
        self.ask.setObjectName("Transparent")
        al = QHBoxLayout(self.ask)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)
        self.ask_head = HeadlineBox("Headline -- press Enter for line 2 (emoji OK)")
        al.addWidget(self.ask_head, 1)
        self.ask_comp = QLineEdit()
        self.ask_comp.setPlaceholderText("the competitor's link, or its file's path")
        al.addWidget(self.ask_comp, 1)
        self.b_ask = W.Button("Make the template", "Primary")
        self.b_ask.clicked.connect(self._ask_go)
        al.addWidget(self.b_ask, 0, Qt.AlignmentFlag.AlignVCenter)
        self.b_ask_cancel = W.Button("Cancel", "Ghost")
        self.b_ask_cancel.clicked.connect(self._ask_cancel)
        al.addWidget(self.b_ask_cancel, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lay.addWidget(self.ask)
        self._asking = ""                    # "headline" / "change" / "competitor"
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.started = QLabel("")
        self.started.setFont(W.mono(11))
        self.started.setStyleSheet(f"color: {theme.DIM};")
        foot.addWidget(self.started)
        foot.addStretch(1)
        self.now = W.label("", "Muted", wrap=True, select=True)
        self.now.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        foot.addWidget(self.now, 3)
        self.lay.addLayout(foot)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.b_go = W.Button("Convert", "Primary")
        self.b_go.clicked.connect(lambda: self.page.press(self.item))
        self.b_prem = W.Button("Open in Premiere", "Primary")
        self.b_prem.clicked.connect(lambda: self.page.open_premiere(self.item))
        self.b_change = W.Button("Change headline", "Small")
        self.b_change.clicked.connect(lambda: self._start_ask("change"))
        self.b_comp = W.Button("Add a competitor", "Small")
        self.b_comp.clicked.connect(lambda: self._start_ask("competitor"))
        self.b_folder = W.Button("Open folder", "Small")
        self.b_folder.clicked.connect(self._folder)
        self.b_retry = W.Button("Try again", "Small")
        self.b_retry.clicked.connect(lambda: self.page.retry(self.item))
        self.b_stop = W.Button("Stop", "Small")
        self.b_stop.clicked.connect(lambda: self.page.stop_item(self.item))
        self.b_remove = W.Button("Remove", "Ghost")
        self.b_remove.clicked.connect(lambda: self.page.remove_item(self.item))
        for b in (self.b_go, self.b_prem, self.b_change, self.b_comp, self.b_folder, self.b_retry, self.b_stop):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.b_remove)
        self.lay.addLayout(btns)
        self.refresh()

    def _build_steps(self) -> None:
        while self.steps_row.count():
            w = self.steps_row.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
        self.cells = {}
        for k, label in self.item.step_list():
            c = StepCell(label)
            self.cells[k] = c
            self.steps_row.addWidget(c, 1)

    # -- asking (the headline box, Change headline, Add a competitor) --
    def _start_ask(self, what: str) -> None:
        self._asking = what
        if what == "change":
            self.ask_head.set_text(self.item.title)
        self.refresh()
        (self.ask_comp if what == "competitor" else self.ask_head).setFocus()

    def _ask_cancel(self) -> None:
        self._asking = ""
        self.refresh()

    def _ask_go(self) -> None:
        what = self._asking or ("headline" if self.item.gate == G_HEADLINE else "")
        if what == "competitor":
            if self.page.add_competitor(self.item, self.ask_comp.text()):
                self._asking = ""
                self.ask_comp.clear()
        elif what == "change":
            if self.page.change_headline(self.item, self.ask_head.text()):
                self._asking = ""
        else:
            self.page.make_template(self.item, self.ask_head.text())
        self.refresh()

    def _use_headline(self, text: str) -> None:
        self.ask_head.set_text(text)
        if self.item.state == DONE and self.item.kind == NV.FULL or self.item.default_template:
            self._start_ask("change")
        elif not self._asking and self.item.gate != G_HEADLINE:
            self.item.title = NV.headline_text(text)        # used by the template step when it comes
            self.page.toast("Headline set -- the template uses it")
        self.refresh()

    def _use_title(self, text: str) -> None:
        self.page.set_upload_title(self.item, text)

    def _folder(self) -> None:
        if self.item.project is not None and Path(self.item.project).exists():
            os.startfile(str(self.item.project))                  # noqa: S606

    # -- what it shows --
    def refresh(self) -> None:
        it = self.item
        if set(self.cells) != {k for k, _ in it.step_list()}:
            self._build_steps()
        self.name.setText(it.name or (it.raw_url or str(it.raw_file or it.raw_torrent or ""))[:90])
        self.chan.setText(it.channel or "")
        self.chan.setStyleSheet(f"color: {theme.channel_colour(it.channel)};")
        just = it.kind == NV.JUST_CUTS
        self.kind.set("Just cuts" if just else "Full Match cuts", (theme.CORAL, "#2a1812", "#5a2f22"))
        word, cols = self._pill()
        self.pill.set(word, cols)
        for k, c in self.cells.items():
            c.set(it.steps.get(k) or Step())
        raw = it.raw_url or (Path(it.raw_file).name if it.raw_file else "") or it.raw_torrent[:60]
        comp = it.competitor_url or (Path(it.comp_file).name if it.comp_file else "")
        self.pair.setText(f"RAW: {raw}" + (f"  ·  COMP: {comp}" if comp else "  ·  no competitor (Just cuts)"))
        has_comp = bool(comp)
        self.titles.setVisible(has_comp)
        if has_comp:
            self.titles.set(it.post_title, it.video_headline)
        self.mine.set(it.title)
        self.mine.setVisible(bool((it.title or "").strip()))
        asking = self._asking or (G_HEADLINE if it.gate == G_HEADLINE else "")
        self.ask.setVisible(bool(asking))
        self.ask_head.setVisible(asking in (G_HEADLINE, "change"))
        self.ask_comp.setVisible(asking == "competitor")
        self.b_ask.setText({"competitor": "Add and make the full edit", "change": "Change the template"}.get(
            asking, "Make the template"))
        self.b_ask_cancel.setVisible(asking in ("competitor", "change"))
        self.started.setText(f"Started {it.started.day} {theme_month(it.started)} {_hm(it.started)}"
                             if it.started else "")
        self.now.setText(self.page.now_text(it))
        self.now.setStyleSheet(f"color: {theme.RED};" if it.state == FAILED else
                               (f"color: {theme.AMBER};" if it.gate == G_HEADLINE or it.default_template else ""))
        busy = it.state == WORKING
        gate_word = {G_CONVERT: "Convert", G_SEND: "Make the cuts" if just else "Send to Match cuts"}.get(it.gate)
        self.b_go.setVisible(bool(gate_word) and it.state == GATED)
        if gate_word:
            self.b_go.setText(gate_word)
        done_ok = it.state == DONE and it.result.get("label") in ("PASS", "OK with a note")
        self.b_prem.setVisible(done_ok and self.page.xml_of(it) is not None)
        self.b_change.setVisible(done_ok and bool(it.channel) and self._asking != "change")
        self.b_comp.setVisible(done_ok and just and self._asking != "competitor")
        self.b_stop.setVisible(busy)
        self.b_retry.setVisible(it.state in (FAILED, STOPPED))
        self.b_folder.setVisible(it.project is not None)
        self.b_remove.setEnabled(not busy and not (it.job is not None and it.job.state == J.RUNNING))

    def _pill(self) -> tuple[str, tuple[str, str, str] | None]:
        it = self.item
        if it.state == FAILED:
            return "FAIL", None
        if it.state == STOPPED:
            return "STOPPED", None
        if it.state == DONE:
            lab = it.result.get("label", "PASS")
            word = ("Cuts ready" if it.kind == NV.JUST_CUTS and lab == "PASS" else lab)
            if it.default_template:
                return f"{word} · default template", theme.pill_colours("note")
            return word, theme.pill_colours(lab)
        if it.gate == G_HEADLINE:
            return "needs a headline", theme.pill_colours("note")
        if it.state == GATED:
            return "waiting for you", None
        if it.group() == NV.RUNNING:
            return "running", None
        return "waiting", None


def theme_month(dt: datetime) -> str:
    from ..core.history import MONTHS
    return MONTHS[dt.month - 1]


# ---------------------------------------------------------------------------------------------------------------------
# the list: newest on top, rows glide to their places
# ---------------------------------------------------------------------------------------------------------------------

class GlideList(QWidget):
    """Rows stacked top to bottom in a given order; a row that changes place glides there (0.45 s, the app's GLIDE
    curve), a new one fades in at its place. Heights follow the rows' own (word-wrapped) layouts."""
    GAP = 12

    def __init__(self):
        super().__init__()
        self.setObjectName("Transparent")
        self.rows: list[QWidget] = []
        self._anims: dict[int, QPropertyAnimation] = {}
        self._pending = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_rows(self, rows: list[QWidget], animate: bool = True) -> None:
        new = [r for r in rows if r not in self.rows]
        for r in self.rows:
            if r not in rows:
                r.removeEventFilter(self)
        self.rows = list(rows)
        for r in new:
            r.setParent(self)
            r.installEventFilter(self)
            r.show()
        self.relayout(animate, fresh=new)

    def eventFilter(self, obj, e) -> bool:                # noqa: N802
        if e.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show, QEvent.Type.Hide) and obj in self.rows:
            self._later()
        return False

    def _later(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, lambda: self.relayout(True))

    def _height(self, r: QWidget, w: int) -> int:
        lay = r.layout()
        if lay is not None and lay.hasHeightForWidth():
            return max(lay.totalHeightForWidth(w), r.minimumSizeHint().height())
        return r.sizeHint().height()

    def relayout(self, animate: bool = True, fresh: list | None = None) -> None:
        self._pending = False
        w = max(100, self.width())
        y = 0
        for r in self.rows:
            h = self._height(r, w)
            r.resize(w, h)
            target = QPoint(0, y)
            if r.pos() != target:
                if animate and M.enabled() and r not in (fresh or []) and r.isVisible():
                    a = self._anims.get(id(r))
                    if a is None:
                        a = QPropertyAnimation(r, b"pos", self)
                        a.setDuration(M.GLIDE_MS)
                        a.setEasingCurve(QEasingCurve(M.GLIDE))
                        self._anims[id(r)] = a
                    if a.endValue() != target or a.state() != a.State.Running:
                        a.stop()
                        a.setStartValue(r.pos())
                        a.setEndValue(target)
                        a.start()
                else:
                    a = self._anims.get(id(r))
                    if a is not None:
                        a.stop()
                    r.move(target)
            y += h + self.GAP
        self.setFixedHeight(max(0, y - self.GAP))

    def resizeEvent(self, e) -> None:                     # noqa: N802
        super().resizeEvent(e)
        self.relayout(False)


# ---------------------------------------------------------------------------------------------------------------------
# turn off the PC
# ---------------------------------------------------------------------------------------------------------------------

class ShutdownWarning(QDialog):
    """"Everything is done -- the PC turns off in 60 s" with Cancel. At 0 it asks core.shutdown (in the background)
    and turns the PC off, or -- Premiere unsaved, a render running -- says so and asks again every 30 s."""

    def __init__(self, parent: QWidget, seconds: int = 60):
        super().__init__(parent)
        self.setWindowTitle("Turn off the PC")
        self.setModal(False)
        self.left = int(seconds)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        self.text = W.label("", "Name", wrap=True)
        lay.addWidget(self.text)
        self.note = W.label("", "Muted", wrap=True)
        lay.addWidget(self.note)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_cancel = W.Button("Cancel", "Primary")
        self.b_cancel.clicked.connect(self.reject)
        row.addWidget(self.b_cancel)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.cancelled = False
        self.rejected.connect(self._cancel)
        self._show()

    def start(self) -> None:
        self.show()
        self.timer.start()

    def _show(self) -> None:
        self.text.setText(f"Everything is done -- the PC turns off in {self.left} s")
        self.note.setText("Press Cancel to keep it on.")

    def _cancel(self) -> None:
        self.cancelled = True
        self.timer.stop()

    def _tick(self) -> None:
        if self.cancelled:
            return
        self.left -= 1
        if self.left > 0:
            self._show()
            return
        self.timer.stop()
        self.text.setText("Turning the PC off ...")
        from ..core import shutdown as SD
        from ..ui import background as BG
        BG.call(SD.turn_off, self._after, lambda e: self._after([f"it could not be turned off ({e})"]))

    def _after(self, blockers: list) -> None:
        if self.cancelled or not blockers:
            return
        self.text.setText("Not turned off yet")
        self.note.setText("; ".join(blockers) + ". It tries again every 30 s -- press Cancel to keep it on.")
        self.left = 30
        QTimer.singleShot(30000, self._retry)

    def _retry(self) -> None:
        if self.cancelled:
            return
        self.left = 1
        self._tick()


# ---------------------------------------------------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------------------------------------------------

class DownloadPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.items: list[Item] = []
        self.rows: dict[int, VideoRow] = {}
        self.current: StageThread | None = None
        self.work: list[tuple[int, tuple[str, ...], bool]] = []    # (item id, stages, default template), in order
        self.raw_file: Path | None = None
        self.comp_file: Path | None = None
        self._worked = False            # something ran since "Turn off the PC" was ticked
        self.warning: ShutdownWarning | None = None
        self.shutdown_dialog = ShutdownWarning          # (a test puts a fake here)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(16)
        self.auto = AutoSwitch(NV.automatic())
        self.auto.toggled.connect(self._auto_toggled)
        self.offpc = QCheckBox("Turn off the PC when everything is done")
        self.offpc.toggled.connect(self._offpc_toggled)
        lay.addWidget(W.SectionHeader("New video", [self.auto, self.offpc]))
        lay.addWidget(W.label("Paste the RAW (a YouTube link, a magnet link or .torrent, or a video file) and, if you "
                              "have it, the competitor. Ctrl+V anywhere pastes a link here.", "Muted", wrap=True))
        self._form(lay)
        lay.addSpacing(10)
        self.count = W.label("", "Small")
        lay.addWidget(W.SectionHeader("Videos", [self.count]))
        self.list = GlideList()
        lay.addWidget(self.list)
        self.empty = W.label("Nothing here yet. Paste a link above.", "Dim")
        lay.addWidget(self.empty)
        lay.addSpacing(10)
        self.where = W.label("", "Dim", wrap=True, select=True)
        lay.addWidget(self.where)
        lay.addStretch(1)
        self.poll = QTimer(self)
        self.poll.setInterval(1000)
        self.poll.timeout.connect(self._poll)
        self.poll.start()
        self._where()
        self._detect()
        self._counts()

    # -- the form --
    def _form(self, lay: QVBoxLayout) -> None:
        form = W.Card(hover=False, margins=(16, 14, 16, 14), spacing=12)
        self.raw = QLineEdit()
        self.raw.setPlaceholderText("YouTube link, magnet link, or drop a .torrent / video file")
        self.raw.textChanged.connect(lambda _t: self._detect())
        self.raw.textEdited.connect(self._raw_typed)
        self.d_raw = DetLabel()
        pick = W.Button("File...", "Small")
        pick.clicked.connect(self._pick_raw)
        form.lay.addLayout(self._field("RAW", self.raw, [pick, self.d_raw]))
        self.comp = QLineEdit()
        self.comp.setPlaceholderText("Competitor Short link or file")
        self.comp.textChanged.connect(lambda _t: self._detect())
        self.comp.textEdited.connect(self._comp_typed)
        self.d_comp = DetLabel()
        pick2 = W.Button("File...", "Small")
        pick2.clicked.connect(self._pick_comp)
        form.lay.addLayout(self._field("Competitor (optional)", self.comp, [pick2, self.d_comp]))
        names = self._channel_names()
        self.ch = ChannelButtons(names, NV.last_channel(names))
        self.ch.changed.connect(NV.set_last_channel)
        form.lay.addLayout(self._field("Channel", self.ch, []))
        self.head = HeadlineBox()
        self.head.changed.connect(lambda _t: self._head_ready())
        self.d_head = DetLabel()
        form.lay.addLayout(self._field("Headline (Enter = line 2)", self.head, [self.d_head]))
        self.yt = QLineEdit()
        self.yt.setPlaceholderText("the title you'll upload with")
        form.lay.addLayout(self._field("YouTube title (optional)", self.yt, []))
        self.drop = W.DropZone("Drop a video file or a .torrent here", ["the first goes into RAW, the next into "
                                                                         "Competitor"], big=False)
        self.drop.dropped.connect(self._dropped)
        form.lay.addWidget(self.drop)
        bot = QHBoxLayout()
        bot.setSpacing(12)
        mode = QVBoxLayout()
        mode.setSpacing(2)
        k = QLabel("WILL MAKE")
        k.setFont(W.mono(9.5, 600, 1.0))
        k.setStyleSheet(f"color: {theme.DIM};")
        mode.addWidget(k)
        self.will = QLabel("—")
        self.will.setFont(W.display(22, 1))
        self.will.setStyleSheet(f"color: {theme.CORAL};")
        mode.addWidget(self.will)
        bot.addLayout(mode)
        bot.addStretch(1)
        self.mode = W.Segmented([(J.FAST, "Fast"), (J.THOROUGH, "Thorough")], J.FAST)
        bot.addWidget(self.mode)
        self.speed = W.Segmented([("100", "100 %"), ("125", "125 %")], "100")
        bot.addWidget(self.speed)
        self.b_batch = W.Button("Many at once", "Small")
        self.b_batch.setCheckable(True)
        self.b_batch.toggled.connect(lambda on: (self.batch_box.setVisible(on), self.b_add_all.setVisible(on)))
        bot.addWidget(self.b_batch)
        self.b_make = W.Button("Make the video", "Primary")
        self.b_make.clicked.connect(self.add)
        bot.addWidget(self.b_make)
        form.lay.addLayout(bot)
        self.batch_box = QPlainTextEdit()
        self.batch_box.setPlaceholderText("One video per line:  RAW link | competitor link\n"
                                          "https://youtu.be/AbCdEf12345 | https://youtube.com/shorts/Zx9Yw8Vu7Ts")
        self.batch_box.setFixedHeight(110)
        self.batch_box.setVisible(False)
        form.lay.addWidget(self.batch_box)
        self.b_add_all = W.Button("Add them all", "Primary")
        self.b_add_all.clicked.connect(self.add_batch)
        self.b_add_all.setVisible(False)
        form.lay.addWidget(self.b_add_all, 0, Qt.AlignmentFlag.AlignRight)
        self.msg = W.label("", "Muted", wrap=True, select=True)
        form.lay.addWidget(self.msg)
        lay.addWidget(form)

    @staticmethod
    def _field(name: str, w: QWidget, right: list) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        k = QLabel(name.upper() if "(" not in name else name.split("(")[0].upper() + "(" + name.split("(", 1)[1])
        k.setFont(W.mono(9.5, 600, 1.0))
        k.setStyleSheet(f"color: {theme.MUTED};")
        k.setFixedWidth(150)
        k.setWordWrap(True)
        row.addWidget(k, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(w, 1)
        for r in right:
            row.addWidget(r, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    @staticmethod
    def _channel_names() -> list[str]:
        from ..ui.template_editor import channel_names
        return channel_names() or list(theme.CHANNEL_COLOURS)

    def _where(self) -> None:
        """Where the folders go, the free space and yt-dlp's version -- read in the background (a subprocess and a
        disk: never in the window's thread)."""
        root = paths.projects_root()
        self.where.setText(f"The video folders go to {root}")

        def work():
            from ..core import space
            try:
                from ..core import download as D
                v = D.installed_version()
            except Exception:  # noqa: BLE001
                v = "?"
            return space.summary([root]), v

        def got(r) -> None:
            self.where.setText(f"The video folders go to {root} ({r[0]})  ·  yt-dlp {r[1]} (updated at most once a day "
                               "before a download)")
        from ..ui import background as BG
        BG.call(work, got)

    def toast(self, text: str, kind: str = "ok") -> None:
        fn = getattr(self.ctx, "toast", None)
        if fn is not None:
            try:
                fn(text, kind)
            except Exception:  # noqa: BLE001
                pass

    def _detect(self) -> None:
        rk, ck = NV.kind_of(self.raw.text()), NV.kind_of(self.comp.text())
        self.d_raw.set_kind(rk)
        self.d_comp.set_kind(ck)
        self.will.setText(NV.WILL_MAKE[NV.will_make(rk, ck)])
        self.will.setStyleSheet(f"color: {theme.AMBER if NV.will_make(rk, ck) == NV.NEEDS_COMP else theme.CORAL};")

    def _head_ready(self) -> None:
        if self.head.text():
            self.d_head.set_kind("ok", "READY")
        else:
            self.d_head.set_kind("", "EMPTY")

    def paste_link(self, text: str) -> bool:
        """Ctrl+V anywhere: a link goes into the RAW's field (the competitor's when the RAW is filled already).
        False when the text is not a link."""
        t = (text or "").strip().strip('"')
        k = NV.kind_of(t)
        if k not in (NV.YOUTUBE, NV.LINK, NV.TORRENT) and not t.startswith("www."):
            return False
        if not self.raw.text().strip():
            self.raw.setText(t)
            self.raw.setFocus()
        elif not self.comp.text().strip():
            self.comp.setText(t)
            self.comp.setFocus()
        else:
            self.comp.setText(t)
        self.msg.setStyleSheet("")
        self.msg.setText("link pasted -- add the competitor if you have it, pick the channel, type the headline")
        return True

    def _raw_typed(self, _t: str) -> None:
        if self.raw_file is not None and self.raw.text() != str(self.raw_file):
            self.raw_file = None

    def _comp_typed(self, _t: str) -> None:
        if self.comp_file is not None and self.comp.text() != str(self.comp_file):
            self.comp_file = None

    def _filter(self) -> str:
        return "Videos and torrents (" + " ".join(f"*{e}" for e in sorted(J.VIDEO_EXTS)) + " *.torrent)"

    def _pick_raw(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "The RAW file", str(paths.repo() / "input"), self._filter())
        if f:
            self._set_raw_file(Path(f))

    def _pick_comp(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "The competitor's file", str(paths.repo() / "input"),
                                           "Videos (" + " ".join(f"*{e}" for e in sorted(J.VIDEO_EXTS)) + ")")
        if f:
            self.comp_file = Path(f)
            self.comp.setText(str(self.comp_file))

    def _dropped(self, ps: list) -> None:
        got = [Path(p) for p in ps if J.is_video(Path(p)) or str(p).lower().endswith(".torrent")]
        if not got:
            self.msg.setText("that is not a video file or a .torrent")
            return
        for f in got:
            if not self.raw.text().strip():
                self._set_raw_file(f)
            elif not self.comp.text().strip() and J.is_video(f):
                self.comp_file = f
                self.comp.setText(str(f))

    def _set_raw_file(self, f: Path) -> None:
        self.raw_file = f
        self.raw.setText(str(f))
        self.msg.setText(f"RAW: {f.name}")

    # -- adding --
    def _item_from(self, raw_text: str, comp_text: str, raw_file: Path | None = None,
                   comp_file: Path | None = None) -> tuple[Item | None, str]:
        """An Item from the form's / a batch line's two texts, or (None, the problem in plain words)."""
        from ..core import download as D
        rk = NV.FILE if raw_file is not None else NV.kind_of(raw_text)
        ck = NV.FILE if comp_file is not None else NV.kind_of(comp_text)
        made = NV.will_make(rk, ck)
        if made is None:
            return None, "paste the RAW first: a YouTube link, a magnet link / .torrent, or a video file"
        if made == NV.NEEDS_COMP:
            return None, "a torrent needs a competitor -- add the competitor's link or file"
        it = Item("", channel=self.ch.value, title=self.head.text(), mode=self.mode.value,
                  speed=float(self.speed.value), kind=made)
        if rk == NV.FILE:
            f = raw_file or Path(NV._clean(raw_text))                      # noqa: SLF001
            if not f.is_file():
                return None, f"RAW: the file is not there: {f}"
            it.raw_file = f
        elif rk == NV.TORRENT:
            it.raw_torrent = NV._clean(raw_text)                           # noqa: SLF001
        else:
            link = D.clean_link(raw_text)
            p = D.link_problem(link)
            if p:
                return None, f"RAW: {p}"
            it.raw_url = link
        if ck == NV.FILE:
            f = comp_file or Path(NV._clean(comp_text))                    # noqa: SLF001
            if not f.is_file():
                return None, f"Competitor: the file is not there: {f}"
            it.comp_file = f
        elif ck:
            link = D.clean_link(comp_text)
            p = D.link_problem(link)
            if p:
                return None, f"Competitor: {p}"
            it.competitor_url = link
        if it.raw_url and it.competitor_url and it.raw_url == it.competitor_url:
            return None, "the RAW and the competitor are the same link"
        it.__post_init__()
        it.steps = {k: Step() for k, _ in it.step_list()}
        return it, ""

    def add(self) -> Item | None:
        """"Make the video": the form's video into the list (its first step starts by itself)."""
        raw_file = self.raw_file if self.raw_file is not None and self.raw.text() == str(self.raw_file) else None
        comp_file = self.comp_file if self.comp_file is not None and self.comp.text() == str(self.comp_file) else None
        it, prob = self._item_from(self.raw.text(), self.comp.text(), raw_file, comp_file)
        if it is None:
            self.msg.setText(prob)
            self.msg.setStyleSheet(f"color: {theme.RED};")
            return None
        it.upload_title = self.yt.text().strip()
        self.msg.setStyleSheet("")
        for w in (self.raw, self.comp, self.yt):
            w.clear()
        self.head.clear()
        self.raw_file = self.comp_file = None
        self._add_item(it)
        self.msg.setText("added -- you can paste the next one")
        return it

    def add_batch(self) -> list[Item]:
        """Many at once: one line per video, "RAW link | competitor link"."""
        pairs, probs = NV.parse_batch(self.batch_box.toPlainText())
        added = []
        for pr in pairs:
            it, prob = self._item_from(pr.raw, pr.competitor)
            if it is None:
                probs.append(f"line {pr.line}: {prob}")
                continue
            self._add_item(it)
            added.append(it)
        if added and not probs:
            self.batch_box.clear()
        self.msg.setStyleSheet(f"color: {theme.RED};" if probs else "")
        self.msg.setText(("; ".join(probs[:6]) + ("  ·  " if added else "")) if probs else "" +
                         (f"{len(added)} video(s) added" if added else ""))
        return added

    def _add_item(self, it: Item) -> None:
        self.items.append(it)
        row = VideoRow(self, it)
        self.rows[it.id] = row
        self._order(scroll_top=True)
        self._counts()
        if it.raw_torrent:
            self.start_torrent(it)
        else:
            self.queue(it, ("download",))

    # -- torrents (6c is another part's: its engine is called here) --
    def start_torrent(self, it: Item) -> None:
        """A torrent RAW: the torrent part's file picker and download (mr_app.ui.torrent_panel / core.torrent);
        when it has finished, its video is the RAW's file and the row goes on like a dropped file. Without that
        part in this app, said plainly."""
        try:
            from ..ui import torrent_panel as TP                          # noqa: F401
        except Exception:  # noqa: BLE001 - not in this build
            self._fail(it, "download", "torrent support is coming -- for now download it yourself and drop the video "
                                       "file into RAW")
            return
        save_dir = self._torrent_dir()
        try:
            picker = TP.TorrentPicker(it.raw_torrent, save_dir)
        except Exception as e:  # noqa: BLE001
            self._fail(it, "download", f"the torrent could not be opened: {NV.plain_error(e)}")
            return
        row = self.rows[it.id]
        row.lay.insertWidget(2, picker)
        self._step(it, "download", S_RUN, 0.0, "choose the file")
        it.message = "choose the video in the torrent, then Start"

        def chosen(files) -> None:
            try:
                job = TP.start_job(it.raw_torrent, save_dir, files) if hasattr(TP, "start_job") else None
            except Exception as e:  # noqa: BLE001
                self._fail(it, "download", NV.plain_error(e))
                return
            if job is None:
                return
            prog = getattr(job, "progress", None)
            if prog is not None:
                prog.connect(lambda share, *_a: self._step(it, "download", S_RUN, float(share), "downloading"))
            fin = getattr(job, "finished", None)
            if fin is not None:
                fin.connect(lambda path: self.torrent_finished(it, Path(path)))
        if hasattr(picker, "chosen"):
            picker.chosen.connect(chosen)

    def torrent_finished(self, it: Item, video: Path) -> None:
        """The torrent's video is downloaded: it is the RAW's file (placed into the project like a dropped file)."""
        it.raw_file = video
        it.raw_torrent = ""
        self.queue(it, ("download",))

    @staticmethod
    def _torrent_dir() -> Path:
        v = NV.get_pref("torrent_dir", "")
        return Path(v) if v else Path(r"D:\Shared\Downloads\Torrents")

    # -- the work queue: one stage of one video at a time, in the order they were asked --
    def queue(self, it: Item, stages: tuple[str, ...], default_template: bool = False) -> None:
        it.state = QUEUED
        it.gate = ""
        it.error = ""
        self.work.append((it.id, tuple(stages), default_template))
        self._refresh(it)
        self._next()

    def _next(self) -> None:
        if self.current is not None:
            return
        while self.work:
            item_id, stages, default = self.work.pop(0)
            it = self._item(item_id)
            if it is None or it.state != QUEUED:
                continue
            it.state = WORKING
            it.started = it.started or datetime.now()
            self._worked = True
            th = StageThread(it, stages, default)
            th.named.connect(self._named)
            th.progress.connect(self._progress)
            th.done.connect(self._done)
            self.current = th
            self._step(it, self._display_step(it, stages[0]), S_RUN, 0.0, "starting")
            self._order()
            th.start()
            return
        self._counts()

    def _display_step(self, it: Item, stage: str) -> str:
        if stage == "template" and it.kind == NV.JUST_CUTS:
            return "cuts"
        return stage

    def _item(self, item_id: int) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def _named(self, item_id: int, folder: str) -> None:
        it = self._item(item_id)
        if it is not None:
            it.project, it.name = Path(folder), Path(folder).name
            self._refresh(it)

    def _progress(self, item_id: int, role: str, share: float, text: str) -> None:
        it = self._item(item_id)
        if it is None or self.current is None or self.current.item.id != item_id:
            return
        if role in it.files:
            it.files[role] = [share, text]
        stage = self._display_step(it, self.current.stage or self.current.stages[0])
        roles = ("competitor", "raw") if (it.competitor_url or it.comp_file) and not it.roles else ("raw",)
        if it.roles:
            roles = tuple(it.roles)
        total = share
        if stage in ("download", "convert") and len(roles) == 2 and role in roles:
            total = (roles.index(role) + share) / 2.0
        it.message = f"{ROLE_NAMES.get(role, role)}: {text}"
        self._step(it, stage, S_RUN, total, text)

    def _step(self, it: Item, key: str, state: str, share: float | None = None, text: str | None = None) -> None:
        st = it.steps.setdefault(key, Step())
        st.state = state
        if share is not None:
            st.share = max(0.0, min(1.0, share))
        if text is not None:
            st.text = text
        if state in (S_DONE, S_NOTE):
            st.share = 1.0
            st.finished = datetime.now()
        self._refresh(it)

    def _done(self, item_id: int, res: dict) -> None:
        th, self.current = self.current, None
        if th is not None:
            th.wait(2000)
        it = self._item(item_id)
        if it is not None:
            if "headline" in res:
                it.headline = res.get("headline") or ""
            if "video_headline" in res:
                it.video_headline = res.get("video_headline") or ""
            if "post_title" in res:
                it.post_title = res.get("post_title") or ""
            if res.get("rename"):
                it.rename_note = res["rename"]
            if res.get("ok"):
                for st in res.get("stages") or []:
                    key = self._display_step(it, st)
                    if st == "template" and it.kind == NV.FULL:
                        if res.get("template") == "default":
                            it.default_template = True
                            self._step(it, "template", S_NOTE, 1.0, "default template")
                        else:
                            self._step(it, "template", S_DONE, 1.0, "")
                    elif st == "template":
                        continue
                    else:
                        self._step(it, key, S_DONE, 1.0, "")
                if res.get("result"):
                    it.result = dict(res["result"])
                self.after(it, tuple(res.get("stages") or ()), res)
            else:
                stage = res.get("stage") or "download"
                if res.get("error") == "stopped by you":
                    it.state = STOPPED
                    it.error = "stopped by you"
                    it.failed_step = stage
                    self._step(it, self._display_step(it, stage), S_WAIT, None, "stopped")
                else:
                    self._fail(it, stage, res.get("error") or "failed")
            self._refresh(it)
        self._order()
        self._counts()
        self._next()

    def _fail(self, it: Item, stage: str, error: str) -> None:
        it.state = FAILED
        it.error = NV.plain_error(error)
        it.failed_step = stage
        self._step(it, self._display_step(it, stage), S_FAIL, None, "failed")
        try:
            self.ctx.notify("New video: " + (it.name or "a video"), f"failed: {it.error}")
        except Exception:  # noqa: BLE001
            pass
        self._order()

    # -- what comes after a step: wait for the button, or go on by itself (Automatic) --
    def after(self, it: Item, stages: tuple[str, ...], res: dict | None = None) -> None:
        last = stages[-1] if stages else ""
        auto = self.auto.isChecked()
        if last == "download":
            if auto:
                self.queue(it, ("convert",))
            else:
                self._gate(it, G_CONVERT)
        elif last == "convert":
            it.roles = ()
            if auto:
                self.proceed(it)
            else:
                self._gate(it, G_SEND)
        elif last == "template":
            self.send_to_edit(it, start=True)
        elif last == "cuts":
            self._finish(it, it.result or {"label": "PASS"})

    def _gate(self, it: Item, gate: str) -> None:
        it.state = GATED
        it.gate = gate
        self._refresh(it)
        self._order()

    def press(self, it: Item) -> None:
        """The row's big button: Convert, or Send to Match cuts / Make the cuts."""
        if it.gate == G_CONVERT:
            self.queue(it, ("convert",))
        elif it.gate == G_SEND:
            self.proceed(it)

    def proceed(self, it: Item) -> None:
        """After the conversion: Just cuts -> the cuts; the full edit -> the template (your headline; none: the
        default template with Automatic ON, else the row asks for the headline), then the edit."""
        it.gate = ""
        if it.kind == NV.JUST_CUTS:
            self.queue(it, ("cuts",))
            return
        if not it.channel:
            self._step(it, "template", S_SKIP, 0.0, "no channel: default frame")
            self.send_to_edit(it, start=True)
            return
        if (it.title or "").strip():
            self.queue(it, ("template",))
        elif self.auto.isChecked():
            self.queue(it, ("template",), default_template=True)
        else:
            self._step(it, "template", S_NOTE, 0.0, "needs a headline")
            it.steps["template"].share = 0.0
            self._gate(it, G_HEADLINE)

    def make_template(self, it: Item, text: str) -> None:
        """"Make the template" on a row that waits for its headline."""
        text = NV.headline_text(text)
        if not text:
            self.toast("Type the headline first", "note")
            return
        it.title = text
        if it.project is not None:
            save_title(it.project, text)
        self._step(it, "template", S_WAIT, 0.0, "")
        self.queue(it, ("template",))

    def change_headline(self, it: Item, text: str) -> bool:
        """Change headline: only template.png again, swapped into the edit (no new run) -- in the background."""
        text = NV.headline_text(text)
        if not text or it.project is None:
            self.toast("Type the headline first", "note")
            return False
        from ..core import flow
        from ..core import projects as PJ
        from ..ui import background as BG
        folder, ch = Path(it.project), it.channel

        def done(r) -> None:
            it.title = text
            it.default_template = False
            self._step(it, "template" if it.kind == NV.FULL else "cuts", S_DONE, 1.0, "")
            self.toast("New template made and swapped into the edit ✓")
            self._refresh(it)

        BG.call(lambda: flow.change_headline(PJ.load(folder), text, ch), done,
                lambda e: self.toast(f"The template could not be made: {NV.plain_error(e)}", "fail"))
        return True

    def add_competitor(self, it: Item, text: str) -> bool:
        """Add a competitor to a finished Just cuts row: the full run without downloading the RAW again."""
        from ..core import download as D
        k = NV.kind_of(text)
        if not k or k == NV.TORRENT:
            self.toast("Paste the competitor's link or its file's path", "note")
            return False
        if it.project is None:
            return False
        upd: dict = {}
        if k == NV.FILE:
            f = Path(NV._clean(text))                                      # noqa: SLF001
            if not f.is_file():
                self.toast(f"The file is not there: {f}", "fail")
                return False
            it.comp_file = f
            upd = {"source_file": str(f), "url": None}
        else:
            link = D.clean_link(text)
            p = D.link_problem(link)
            if p:
                self.toast(p, "fail")
                return False
            it.competitor_url = link
            from ..core.projects import link_id
            upd = {"url": link, "id": link_id(link)}
        try:
            from ..core import projects as PJ
            pj = PJ.load(it.project)
            pj.update(competitor=upd, kind=NV.FULL)
        except Exception as e:  # noqa: BLE001
            self.toast(f"The project could not be changed: {NV.plain_error(e)}", "fail")
            return False
        it.kind = NV.FULL
        it.result = {}
        it.default_template = False
        it.post_title = it.video_headline = None
        it.roles = ("competitor",)
        it.steps = {k2: Step() for k2, _ in it.step_list()}
        self.rows[it.id].refresh()
        self.queue(it, ("download",))
        self.toast("Competitor added -- the RAW is not downloaded again")
        return True

    def set_upload_title(self, it: Item, text: str) -> None:
        it.upload_title = " ".join((text or "").split())
        if it.project is not None:
            try:
                from ..core import board
                board.set_upload_title(it.project, it.upload_title)
            except Exception:  # noqa: BLE001
                pass
        if it.job is not None:
            it.job.upload_title = it.upload_title
        self.toast("YouTube title set ✓")

    def send_to_edit(self, it: Item, start: bool = False) -> None:
        """The ready video into the Match cuts queue (its run folder: the project's 'edit'), started by itself."""
        from ..core import flow
        from ..core import projects as PJ
        try:
            p = PJ.load(it.project)
            p.update(channel=it.channel or None, title=it.title or None)
            save_title(p.folder, it.title)
            job = flow.edit_job(p, it.mode == J.FAST, it.speed)
            job.channel, job.title, job.headline = it.channel, it.title, it.headline
            job.upload_title = it.upload_title
        except Exception as e:  # noqa: BLE001
            self._fail(it, "edit", f"could not make the edit's job: {NV.plain_error(e)}")
            return
        mc = self.ctx.pages.get("match_cuts") if hasattr(self.ctx, "pages") else None
        if mc is None:
            self._fail(it, "edit", "the Match cuts queue is not there")
            return
        it.job = job
        it.state = SENT
        self._step(it, "edit", S_RUN, 0.0, "in the Match cuts queue")
        mc.add_project_job(job, autostart=start)
        self._order()

    def _poll(self) -> None:
        """The edits in the Match cuts queue: their bars and results; and "Turn off the PC"."""
        for it in self.items:
            j = it.job
            if j is None or it.state != SENT:
                continue
            if j.state == J.RUNNING:
                self._step(it, "edit", S_RUN, float(j.percent or 0.0), j.progress or "")
                self._worked = True
            elif j.state in (J.DONE, J.STOPPED) and j.result:
                self._finish(it, dict(j.result))
            self._order()
        self._check_shutdown()

    def _finish(self, it: Item, res: dict) -> None:
        it.result = res
        lab = res.get("label", "FAIL")
        key = "cuts" if it.kind == NV.JUST_CUTS else "edit"
        if lab in ("PASS", "OK with a note"):
            it.state = DONE
            self._step(it, key, S_DONE if lab == "PASS" else S_NOTE, 1.0,
                        (f"{res.get('cuts')} cuts" if res.get("cuts") is not None else ""))
            self.toast("That's a wrap — edit made ✓" if it.kind == NV.FULL else "Cuts ready ✓")
            if it.kind == NV.JUST_CUTS:
                fn = getattr(self.ctx, "project_done", None)
                if fn is not None and it.project is not None:
                    try:
                        fn(type("J", (), {"project": it.project})())
                    except Exception:  # noqa: BLE001
                        pass
        elif lab == "STOPPED":
            it.state = STOPPED
            it.failed_step = key
            self._step(it, key, S_WAIT, None, "stopped")
        else:
            self._fail(it, key, (res.get("failures") or [lab])[0])
        self._refresh(it)
        self._order()

    # -- the row's buttons --
    def retry(self, it: Item) -> None:
        """Try again: continues from the step that failed (the done ones are kept)."""
        stage = it.failed_step or "download"
        it.error = ""
        if stage == "edit":
            self._step(it, "edit", S_WAIT, 0.0, "")
            self.send_to_edit(it, start=True)
            return
        if it.project is not None and stage in ("download", "convert"):
            try:
                from ..core import projects as PJ
                p = PJ.load(it.project)
                for s in ("download", "convert"):
                    if p.step(s).get("state") != PJ.DONE:
                        p.set_step(s, PJ.WAITING)
            except Exception:  # noqa: BLE001
                pass
        if stage == "download" and it.raw_torrent:
            self.start_torrent(it)
            return
        self._step(it, self._display_step(it, stage), S_WAIT, 0.0, "")
        self.queue(it, (stage,), default_template=it.default_template)

    def stop_item(self, it: Item) -> None:
        if self.current is not None and self.current.item.id == it.id:
            self.current.cancel.set()
            it.message = "stopping ..."
            self._refresh(it)

    def remove_item(self, it: Item) -> None:
        if it.state == WORKING:
            return
        self.items = [i for i in self.items if i.id != it.id]
        self.work = [w for w in self.work if w[0] != it.id]
        row = self.rows.pop(it.id, None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self._order()
        self._counts()

    def xml_of(self, it: Item) -> Path | None:
        rd = it.result.get("run_dir") if it.result else None
        if not rd and it.job is not None and getattr(it.job, "run_dir", None):
            rd = it.job.run_dir
        if rd and (Path(rd) / "1_edit.xml").is_file():
            return Path(rd) / "1_edit.xml"
        return None

    def open_premiere(self, it: Item) -> None:
        xml = self.xml_of(it)
        if xml is None:
            self.toast("The edit's 1_edit.xml is not there", "fail")
            return
        from ..ui import background as BG
        from .match_cuts_tool import open_in_premiere
        self.toast("Opening in Premiere…")
        BG.call(lambda: open_in_premiere(xml), lambda r: self.toast(str(r)) if r else None,
                lambda e: self.toast(f"Premiere: {e}", "fail"))

    # -- Automatic, turn off the PC --
    def _auto_toggled(self, on: bool) -> None:
        NV.set_automatic(on)
        self.toast("Automatic ON — every step runs by itself" if on else "Automatic OFF — it waits for your buttons",
                   "ok" if on else "note")
        if on:
            for it in list(self.items):                    # rows already waiting continue at once
                if it.state != GATED:
                    continue
                if it.gate == G_CONVERT:
                    self.queue(it, ("convert",))
                elif it.gate == G_SEND:
                    self.proceed(it)
                elif it.gate == G_HEADLINE:
                    self.queue(it, ("template",), default_template=True)

    def _offpc_toggled(self, on: bool) -> None:
        self._worked = on and self.anything_working()
        self.toast("The PC turns off when every video is done (60 s warning)" if on else "The PC stays on",
                   "note" if on else "ok")

    def anything_working(self) -> bool:
        """A download, a conversion or an edit running or queued to run by itself -- here or in any tab."""
        if self.current is not None or any(i.state in (QUEUED, WORKING) for i in self.items):
            return True
        if any(i.state == SENT for i in self.items):
            return True
        pages = getattr(self.ctx, "pages", {}) or {}
        for k, p in pages.items():
            if p is self:
                continue
            try:
                if getattr(p, "busy", lambda: False)():
                    return True
            except Exception:  # noqa: BLE001
                continue
        mc = pages.get("match_cuts")
        if mc is not None and getattr(mc, "running", False):
            return True
        return False

    def _check_shutdown(self) -> None:
        if not self.offpc.isChecked():
            return
        if self.anything_working():
            self._worked = True
            return
        if not self._worked or (self.warning is not None and self.warning.isVisible()):
            return
        self._worked = False
        self.warning = self.shutdown_dialog(self)
        self.warning.rejected.connect(lambda: self.offpc.setChecked(False))
        self.warning.start()

    # -- what the rows show, their order --
    def now_text(self, it: Item) -> str:
        if it.state == FAILED:
            return f"Stopped at {it.failed_step or 'a step'}: {it.error}"
        if it.state == STOPPED:
            return "stopped by you -- Try again continues from that step"
        if it.gate == G_CONVERT:
            return "Downloaded — press Convert when you want"
        if it.gate == G_SEND:
            return ("Converted — press Make the cuts when you want" if it.kind == NV.JUST_CUTS else
                    "Converted — press Send to Match cuts when you want")
        if it.gate == G_HEADLINE:
            return "Waiting for the headline — download and convert are done"
        if it.state == DONE:
            lab = it.result.get("label", "")
            if it.kind == NV.JUST_CUTS:
                n = it.result.get("cuts")
                text = (f"Cut at {n} scene changes — open it in Premiere" if n is not None else
                        "Cuts ready — open it in Premiere")
            else:
                text = "Edit made — open it in Premiere" if lab == "PASS" else \
                    f"Edit made, with a note: {(it.result.get('notes') or [''])[0][:160]}"
            if it.default_template:
                text += " · default template used"
            return text
        if it.state == SENT:
            return "The edit runs in the Match cuts queue" if it.job is not None and it.job.state == J.RUNNING else \
                "In the Match cuts queue — it starts when the queue gets to it"
        if it.state == QUEUED:
            return "waiting for the video before it"
        if it.state == WORKING:
            return it.message or "working"
        return ""

    def _refresh(self, it: Item) -> None:
        row = self.rows.get(it.id)
        if row is not None:
            row.refresh()

    def _order(self, scroll_top: bool = False) -> None:
        ordered = NV.order(self.items)
        self.list.set_rows([self.rows[i.id] for i in ordered if i.id in self.rows])
        if scroll_top:
            sa = self.parent()
            while sa is not None and not isinstance(sa, QScrollArea):
                sa = sa.parent()
            if sa is not None:
                sa.verticalScrollBar().setValue(0)

    def _counts(self) -> None:
        n = len(self.items)
        busy = sum(1 for i in self.items if i.group() == NV.RUNNING)
        wait = sum(1 for i in self.items if i.state == GATED)
        bits = [f"{n} video(s)"] + ([f"{busy} running"] if busy else []) + ([f"{wait} waiting for you"] if wait else [])
        self.count.setText("  ·  ".join(bits) if n else "")
        self.empty.setVisible(not self.items)

    def busy(self) -> bool:
        return self.current is not None

    def stop(self) -> None:
        if self.current is not None:
            self.current.cancel.set()


class DownloadTool(Tool):
    key = "download"
    title = "Download"

    def page(self, ctx) -> QWidget:
        return DownloadPage(ctx)
