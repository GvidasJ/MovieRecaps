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

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import Tool
from ..core import jobs as J
from ..core import newvideo as NV
from ..core import paths
from ..ui import motion as M
from ..ui import theme
from ..ui import widgets as W
from ..ui.glide_list import GlideList, scroll_to_top
from ..ui.titles_block import TitlesBlock

_ids = itertools.count(1)
ROLE_NAMES = {"competitor": "Competitor", "raw": "RAW"}

# item states
QUEUED, WORKING, READY, FAILED, STOPPED, SENT = "queued", "working", "ready", "failed", "stopped", "sent"
GATED, DONE = "gated", "done"

# step states
S_WAIT, S_RUN, S_DONE, S_FAIL, S_NOTE, S_SKIP = "waiting", "running", "done", "failed", "note", "skipped"
S_ASK = "ask"                     # amber, the bar empty: the step needs something from you (the headline)

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
    torrent_job: Any = None                 # the torrent part's TorrentJob while the RAW downloads
    torrent_widget: Any = None              # its picker / progress card inside the row
    said_pick: bool = False                 # the row says the queue asks which is the RAW

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = {k: Step() for k, _ in self.step_list()}

    def step_list(self) -> list[tuple[str, str]]:
        return STEPS_JUST if self.kind == NV.JUST_CUTS else STEPS_FULL

    def order_time(self) -> float:
        """When it counts as new for the order shown: a finished row when its last step finished (the one finished
        last on top, as the queue), any other row when it was added."""
        if self.group() == NV.FINISHED:
            ends = [s.finished.timestamp() for s in self.steps.values() if isinstance(s.finished, datetime)]
            if ends:
                return max(ends)
        return float(self.added)

    def group(self) -> int:
        if self.state == WORKING or any(s.state == S_RUN for s in self.steps.values()):
            return NV.RUNNING
        if self.state in (DONE, FAILED, STOPPED):
            return NV.FINISHED
        return NV.WAITING


def waits_for_pick(it: "Item") -> bool:
    """The full edit sent to the Match cuts queue waits for YOU there: the two videos are about as long and their
    names do not say which is the RAW (section 4, the amber "Check which is the RAW" card). Found in the e2e run of
    day 12: the row said "it starts when the queue gets to it" for ever."""
    j = it.job
    return it.state == SENT and j is not None and J.needs_check(j)


def off_thread(fn, done, failed=None) -> None:
    """``ui.background.call`` -- the one way this page runs a disk write off the window's thread (tests replace it to
    run at once)."""
    from ..ui import background as BG
    BG.call(fn, done, failed)


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
                self._titles(p, comp, out, list(tc.get("lines") or []))
        conv = self._project().data.get("steps", {}).get("convert") or {}
        for role in ROLE_NAMES:
            st = conv.get(role) or {}
            if isinstance(st, dict) and st:
                self._say(role, 1.0, flow.convert_text(st))

    def _titles(self, p, comp: Path, out: dict, ocr_lines: list | None = None) -> None:
        """Section 7: the headline written on the competitor's video (the vision model, its own process; broken
        where the video breaks it -- the title check's OCR lines say where; the OCR's lines when the model reads
        nothing), saved with the post's title; the folder named from them when its name says nothing."""
        from ..core import titles as TT
        it = self.item
        self._say("competitor", 1.0, "reading the headline on the video ...")
        head = TT.read_on_video(comp)
        head = TT.settle(head, ocr_lines)
        post = it.post_title if it.post_title is not None else (p.data.get("post_title") or "")
        if not post:            # "Add a competitor" (its link was not read by _create): the title yt-dlp gave it
            comp_info = p.data.get("competitor") if isinstance(p.data.get("competitor"), dict) else {}
            post = TT.post_title({"title": comp_info.get("title") or ""}) if comp_info.get("url") else ""
        out["video_headline"], out["post_title"] = head, post
        try:
            TT.save(p, post, head)
            if p.data.get("generic_name") or TT.useless(p.folder.name.split("_", 1)[-1].replace("-", " ")):
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
        self.setFont(W.mono(10, 600, 10))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(84)
        # the box is painted (its colours fade 0.3 s to a new kind, like the reference's .det transition); the
        # stylesheet only keeps the padding and the 1 px border's room, so the size never changes
        self.setStyleSheet("border: 1px solid transparent; background: transparent; padding: 3px 8px;")
        self.kind = None
        self._from = self._to = DET_COLOURS[""]
        self.fade = M.Fader(self, self._faded)
        self.set_kind("")
        self.fade.set(1.0)

    def colours(self) -> tuple[QColor, QColor, QColor]:
        """(text, border, background) now -- part-way while it fades."""
        t = self.fade.value
        return tuple(M.mix(QColor(a), QColor(b), t) for a, b in zip(self._from, self._to))  # type: ignore[return-value]

    def set_kind(self, kind: str, text: str | None = None) -> None:
        self.kind = kind
        new = DET_COLOURS.get(kind, DET_COLOURS[""])
        self.setText(text if text is not None else NV.LABELS.get(kind, "EMPTY"))
        if new != self._to:
            self._from = tuple(c.name(QColor.NameFormat.HexArgb) for c in self.colours())
            self._to = new
            self.fade.set(0.0)
            self.fade.to(1.0)

    def _faded(self) -> None:
        fg = self.colours()[0]
        if self.styleSheet().find(fg.name(QColor.NameFormat.HexArgb)) < 0:
            self.setStyleSheet(f"color: {fg.name(QColor.NameFormat.HexArgb)}; border: 1px solid transparent; "
                               "background: transparent; padding: 3px 8px;")
        self.update()

    def paintEvent(self, e) -> None:                      # noqa: N802
        _fg, line, bg = self.colours()
        p = QPainter(self)
        W.box(p, QRectF(self.rect()), bg, line)
        p.end()
        super().paintEvent(e)


class HeadlineBox(QPlainTextEdit):
    """The two-line headline box: Enter starts line 2 (a second Enter does nothing), emoji welcome."""
    changed = Signal(str)

    def __init__(self, placeholder: str = "The fact that prison changed\nhim this much is hilarious 🔒😭"):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        fm = self.fontMetrics()
        self.setFixedHeight(fm.lineSpacing() * 2 + 22)
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
        self.name.setFont(W.mono(9.5, 600, 10))
        lay.addWidget(self.name)
        self.bar = W.GlideBar(4)
        lay.addWidget(self.bar)
        self.small = QLabel("")
        self.small.setFont(W.mono(10.5))
        self.small.setMinimumHeight(14)
        lay.addWidget(self.small)

    def set(self, st: Step) -> None:
        colour = {S_RUN: theme.BLUE, S_DONE: theme.GREEN, S_FAIL: theme.RED, S_NOTE: theme.AMBER,
                  S_ASK: theme.AMBER}.get(st.state, theme.DIM)
        self.name.setStyleSheet(f"color: {colour};")
        self.bar.state({S_RUN: "running", S_DONE: "pass", S_FAIL: "fail", S_NOTE: "note", S_ASK: "note"}.get(
            st.state, "waiting"))
        self.bar.set(0.0 if st.state == S_ASK else
                     1.0 if st.state in (S_DONE, S_NOTE) and st.share <= 0 else st.share)
        if st.state == S_ASK:
            txt = st.text
        elif st.state == S_RUN:
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
        self.extras = QVBoxLayout()                 # the torrent's file picker / progress card, while there
        self.extras.setSpacing(8)
        self.lay.addLayout(self.extras)
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

    def add_extra(self, w: QWidget | None, remove: QWidget | None = None) -> None:
        """A card shown inside the row for a while (the torrent's picker, then its progress card)."""
        if remove is not None:
            self.extras.removeWidget(remove)
            remove.setParent(None)
            remove.deleteLater()
        if w is not None:
            self.extras.addWidget(w)

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
        """Opens the project folder -- in the background (it may be on the shared folder over the network)."""
        folder = self.item.project
        if folder is None:
            return

        def work() -> None:
            if Path(folder).exists():
                os.startfile(str(folder))                         # noqa: S606
        off_thread(work, None, lambda e: self.page.toast(f"The folder could not be opened: {e}", "fail"))

    # -- what it shows --
    def refresh(self) -> None:
        it = self.item
        if set(self.cells) != {k for k, _ in it.step_list()}:
            self._build_steps()
        self.name.setText(it.name or (it.raw_url or str(it.raw_file or "") or torrent_name(it.raw_torrent))[:90])
        self.chan.setText(it.channel or "")
        self.chan.setStyleSheet(f"color: {theme.channel_colour(it.channel)};")
        just = it.kind == NV.JUST_CUTS
        self.kind.set("Just cuts" if just else "Full Match cuts", (theme.CORAL, "#2a1812", "#5a2f22"))
        word, cols = self._pill()
        self.pill.set(word, cols)
        for k, c in self.cells.items():
            c.set(it.steps.get(k) or Step())
        raw = it.raw_url or (Path(it.raw_file).name if it.raw_file else "") or \
            ("torrent " + torrent_name(it.raw_torrent))
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
                               (f"color: {theme.AMBER};" if it.gate == G_HEADLINE or it.default_template or
                                waits_for_pick(it) else ""))
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
            lab = (it.result or {}).get("label", "")
            return (lab if lab in ("WRONG PAIR", "PICTURE NOT FOUND", "NOT IN THIS RAW") else "FAIL"), None
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
        if it.state == GATED or waits_for_pick(it):
            return ("Check which is the RAW", theme.pill_colours("note")) if waits_for_pick(it) else \
                ("waiting for you", None)
        if it.group() == NV.RUNNING:
            return "running", None
        return "waiting", None


def torrent_name(source: str | None) -> str:
    """A torrent RAW as a row shows it: a magnet's own name (dn=, "Some.Show.S01E02"), a .torrent file's name --
    never the whole magnet link."""
    s = (source or "").strip().strip('"')
    if not s:
        return ""
    if s.lower().startswith("magnet:"):
        import urllib.parse
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(s).query)
        dn = (q.get("dn") or [""])[0].strip()
        if dn:
            return dn[:90]
        bt = next((x for x in q.get("xt", []) if x.lower().startswith("urn:btih:")), "")
        return f"magnet {bt[9:21]}…" if bt else "magnet link"
    return Path(s).name[:90]


def theme_month(dt: datetime) -> str:
    from ..core.history import MONTHS
    return MONTHS[dt.month - 1]


# ---------------------------------------------------------------------------------------------------------------------
# the list: newest on top, rows glide to their places
# ---------------------------------------------------------------------------------------------------------------------

class RowList(GlideList):
    """The Download / New video rows (day 12, integrated): the Match cuts queue's own ``ui.glide_list.GlideList``, so
    both lists behave the same -- a new row appears at its place, the others glide (0.45 s on the soft curve), a row
    that changes state glides to its new place, a row that grows or shrinks moves the rows under it smoothly.
    ``set_rows(rows)`` = the rows in the order shown (rows not given leave the list, new ones join at their place)."""

    def __init__(self):
        super().__init__(spacing=12)
        self.setObjectName("Transparent")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @property
    def rows(self) -> list[QWidget]:
        return self.items()

    def set_rows(self, rows: list[QWidget]) -> None:
        for r in self.items():
            if r not in rows:
                self.remove(r)
        for k, r in enumerate(rows):
            if r not in self.items():
                self.insert(r, k)
        self.set_order(rows)


# ---------------------------------------------------------------------------------------------------------------------
# turn off the PC
# ---------------------------------------------------------------------------------------------------------------------

def _torrent_active(job) -> bool:
    """A torrent download under way: just started, downloading or waiting for peers (paused by you, finished, failed
    or removed: not)."""
    if job is None:
        return False
    st = str((getattr(job, "status", None) or {}).get("state") or "")
    return st in ("", "downloading", "stalled")


class ShutdownWarning(QDialog):
    """"Everything is done -- the PC turns off in 60 s" with Cancel. At 0 it asks core.shutdown (in the background)
    and turns the PC off, or -- Premiere unsaved, a render running -- says so and asks again every 30 s. Work that
    starts again meanwhile (``working()``: the page's anything_working) closes it without turning anything off --
    at each second, at each retry and once more right before the PC is turned off; ``stopped_for_work`` tells the
    page, which shows a fresh 60 s warning when that work is done."""
    stopped_for_work = Signal()

    def __init__(self, parent: QWidget, seconds: int = 60, working=None):
        super().__init__(parent)
        self.setWindowTitle("Turn off the PC")
        self.setModal(False)
        self.left = int(seconds)
        self.working = working if working is not None else getattr(parent, "anything_working", None)
        self.stop_now = False           # read by the background turn_off right before the PC is turned off
        self.for_work = False           # closed because work started again
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
        self.stop_now = True
        self.timer.stop()

    def _busy(self) -> bool:
        try:
            return bool(self.working and self.working())
        except Exception:  # noqa: BLE001 - cannot tell: count it as work (never turn off in doubt)
            return True

    def stop_for_work(self) -> None:
        """New work started: closed without turning the PC off ("Turn off the PC" stays ticked)."""
        if self.cancelled or self.for_work:
            return
        self.for_work = self.stop_now = True
        self.timer.stop()
        self.hide()                     # (hide, not reject: Cancel would untick "Turn off the PC")
        self.stopped_for_work.emit()

    def _tick(self) -> None:
        if self.cancelled or self.stop_now:
            return
        if self._busy():
            self.stop_for_work()
            return
        self.left -= 1
        if self.left > 0:
            self._show()
            return
        self.timer.stop()
        self.text.setText("Turning the PC off ...")
        from ..core import shutdown as SD
        from ..ui import background as BG
        BG.call(lambda: SD.turn_off(stop=lambda: self.stop_now), self._after, self._failed)

    def _failed(self, err: str) -> None:
        kind, _, msg = str(err).partition(": ")
        if not (msg and kind.isidentifier()):            # plain words only: no "SomeError:" in front
            msg = str(err)
        self._after([f"the PC could not be turned off: {msg}"])

    def _after(self, blockers: list) -> None:
        if self.cancelled or not blockers:
            return
        from ..core import shutdown as SD
        if self.for_work or SD.STOPPED_BY_WORK in blockers:
            self.stop_for_work()
            return
        self.text.setText("Not turned off yet")
        self.note.setText("; ".join(blockers) + ". It tries again every 30 s -- press Cancel to keep it on.")
        self.left = 30
        QTimer.singleShot(30000, self._retry)

    def _retry(self) -> None:
        if self.cancelled or self.stop_now:
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
        self.list = RowList()
        self.list.inserted.connect(lambda _w: scroll_to_top(self))      # a new row -> the list's top (as the queue)
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
        names = sorted(theme.CHANNEL_COLOURS, key=str.lower)   # the three channels at once; the channels folder is
        self.ch = ChannelButtons(names, NV.last_channel(names))  # read in the background (_check_channels: importing
        self.ch.changed.connect(NV.set_last_channel)            # it and reading the disk took ~150 ms here)
        form.lay.addLayout(self._field("Channel", self.ch, []))
        self._check_channels()
        self.head = HeadlineBox()
        self.head.changed.connect(lambda _t: self._head_ready())
        self.d_head = DetLabel()
        form.lay.addLayout(self._field("Headline (Enter = line 2)", self.head, [self.d_head]))
        self.yt = QLineEdit()
        self.yt.setPlaceholderText("the title you'll upload with")
        form.lay.addLayout(self._field("YouTube title (optional)", self.yt, []))
        self.drop = W.DropZone("Drop a video file or a .torrent here", ["RAW first, then the competitor"], big=False)
        self.drop.dropped.connect(self._dropped)
        form.lay.addWidget(self.drop)
        bot = QHBoxLayout()
        bot.setSpacing(12)
        mode = QVBoxLayout()
        mode.setSpacing(2)
        k = QLabel("WILL MAKE")
        k.setFont(W.mono(9.5, 600, 10))
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
        k.setFont(W.mono(9.5, 600, 10))
        k.setStyleSheet(f"color: {theme.MUTED};")
        k.setFixedWidth(150)
        k.setWordWrap(True)
        row.addWidget(k, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(w, 1)
        for r in right:
            row.addWidget(r, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _check_channels(self) -> None:
        """The channels folder's own names (read in the background); the buttons are made again only when they
        differ from the three built in."""
        from ..ui import background as BG

        def got(names) -> None:
            names = list(names or [])
            if not names or set(names) == set(self.ch.buttons):
                return
            old = self.ch
            new = ChannelButtons(names, NV.last_channel(names) if old.value not in names else old.value)
            new.changed.connect(NV.set_last_channel)
            lay = old.parentWidget().layout() if old.parentWidget() is not None else None
            if lay is None:
                return
            for i in range(lay.count()):
                sub = lay.itemAt(i).layout()
                if sub is not None and sub.indexOf(old) >= 0:
                    sub.replaceWidget(old, new)
                    new.show()
                    break
            old.setParent(None)
            old.deleteLater()
            self.ch = new
        BG.call(self._channel_names, got, lambda _e: None)

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
        if self.msg.text().startswith("RAW: ") and not self.raw.text().endswith(self.msg.text()[5:]):
            self.msg.setText("")                          # the dropped file's note, now that something else is typed

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
        # a .torrent is the torrent's source (its path in the RAW field: TORRENT), not a video file to copy
        self.raw_file = None if str(f).lower().endswith(".torrent") else f
        self.raw.setText(str(f))
        self.msg.setStyleSheet("")
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
        bits = ["; ".join(probs[:6])] if probs else []
        bits += [f"{len(added)} video(s) added"] if added else []
        self.msg.setText("  ·  ".join(bits) or "nothing to add: write one video per line")
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
        """A torrent RAW: the torrent part's file picker inside the row (ui.torrent_panel.TorrentPicker: the files,
        sizes, seeders, free space, the biggest video ticked, Start), then its progress card (TorrentCard of the
        TorrentJob); when the download has finished, its video is the RAW's file and the row goes on like a dropped
        file (placed into the project folder, then Convert ...). Without that part in this app, said plainly."""
        try:
            from ..ui import torrent_panel as TP
        except Exception:  # noqa: BLE001 - not in this build
            self._fail(it, "download", "torrent support is coming -- for now download it yourself and drop the video "
                                       "file into RAW")
            return
        job = it.torrent_job
        if job is not None and (job.status or {}).get("state") in ("failed", "stalled", "paused"):
            it.state, it.error = WORKING, ""                 # Try again on a download that stopped: the same job
            it.message = "Trying the torrent again"
            self._step(it, "download", S_RUN, None, "downloading")
            job.resume()
            self._order()
            return
        self._drop_torrent(it)
        save_dir = self._torrent_dir()
        try:
            picker = TP.TorrentPicker(it.raw_torrent, save_dir)
        except Exception as e:  # noqa: BLE001
            self._fail(it, "download", f"the torrent could not be opened: {NV.plain_error(e)}")
            return
        row = self.rows[it.id]
        row.add_extra(picker)
        it.torrent_widget = picker
        it.state = WORKING
        it.started = it.started or datetime.now()
        it.message = "Choose the video in the torrent, then press Start"
        self._step(it, "download", S_RUN, 0.0, "choose the file")
        self._order()

        def failed(text: str) -> None:
            self._drop_torrent(it)
            self._fail(it, "download", text or "the torrent could not be read")

        def cancelled() -> None:
            self._drop_torrent(it)
            it.state, it.failed_step, it.error = STOPPED, "download", "stopped by you"
            self._step(it, "download", S_WAIT, 0.0, "stopped")
            self._refresh(it)
            self._order()

        def chosen(indexes) -> None:
            try:
                job = picker.start_job(list(indexes))
            except Exception as e:  # noqa: BLE001
                failed(NV.plain_error(e))
                return
            self._drop_torrent(it)
            self._attach_torrent(it, job, TP)
        picker.chosen.connect(chosen)
        picker.failed.connect(failed)
        picker.cancelled.connect(cancelled)

    def _drop_torrent(self, it: Item) -> None:
        """The torrent's picker / progress card out of the row (a new try, a cancel, a removed download)."""
        w, it.torrent_widget = it.torrent_widget, None
        row = self.rows.get(it.id)
        if w is not None and row is not None:
            try:
                row.add_extra(None, remove=w)
            except RuntimeError:                           # already deleted
                pass

    def _attach_torrent(self, it: Item, job, TP) -> None:
        """A running torrent download on its row: the progress card inside the row; its numbers move the Download
        bar; finished -> its video is the RAW's file and the row goes on (Convert waits for the button unless
        Automatic is ON); "nobody sharing" (stalled) only pauses it -- the card's Resume tries again; a failure stops
        the row red (Try again resumes the same download); Remove on the card stops the row. The row's video is kept
        in the app's folder under the job's id, so after a restart the download continues on a row again."""
        card = TP.TorrentCard(job)
        self.rows[it.id].add_extra(card)
        it.torrent_job, it.torrent_widget = job, card
        it.message = "Downloading the torrent"
        self._refresh(it)

        def keep() -> None:
            if job.id:
                fields = {k: getattr(it, k) for k in NV.ROW_FIELDS}
                from ..ui import background as BG
                BG.call(lambda: NV.keep_torrent_row(job.id, fields))

        def forget() -> None:
            if job.id:
                from ..ui import background as BG
                jid = job.id
                BG.call(lambda: NV.forget_torrent_row(jid))

        def progress(st: dict) -> None:
            if not getattr(job, "_mr_kept", False) and job.id:
                job._mr_kept = True                        # noqa: SLF001 - once the id is known
                keep()
            if it.torrent_job is job and it.state == WORKING:
                self._torrent_progress(it, st)

        def failed(text: str) -> None:
            if it.torrent_job is not job:
                return
            if (job.status or {}).get("state") == "stalled":
                it.message = (text or NV.NO_PEERS) + " -- press Resume on the card to try again"
                self._step(it, "download", S_RUN, None, "nobody sharing")
                return
            self._fail(it, "download", text or "the torrent stopped")

        def finished(path: str) -> None:
            if it.torrent_job is not job:
                return
            forget()
            QTimer.singleShot(4000, lambda: self._drop_torrent(it) if it.torrent_widget is card else None)
            if path:
                self.torrent_finished(it, Path(path))
            else:
                self._fail(it, "download", "the torrent finished but holds no video file")

        def removed() -> None:
            if it.torrent_job is not job:
                return
            forget()
            it.torrent_job = None
            self._drop_torrent(it)
            if it.state == WORKING:
                it.state, it.failed_step, it.error = STOPPED, "download", "the download was removed"
                self._step(it, "download", S_WAIT, 0.0, "removed")
                self._refresh(it)
                self._order()
        job.progress.connect(progress)
        job.failed.connect(failed)
        job.finished.connect(finished)
        job.removed.connect(removed)

    def _torrent_progress(self, it: Item, st: dict) -> None:
        try:
            share = float(st.get("share", 0.0) or 0.0)
        except (TypeError, ValueError):
            share = 0.0
        self._worked = True
        if st.get("state") == "paused":
            it.message = "Paused -- press Resume on the card"
        else:
            it.message = " · ".join(x for x in (f"{st.get('percent', int(share * 100))} %", st.get("speed_text", ""),
                                                 f"{st.get('seeders', 0)} seeders", st.get("eta_text", "")) if x)
        self._step(it, "download", S_RUN, share * 0.98, "paused" if st.get("state") == "paused" else "downloading")

    def torrent_finished(self, it: Item, video: Path) -> None:
        """The torrent's video is downloaded: it is the RAW's file (placed into the project like a dropped file)."""
        it.raw_file = video
        it.raw_torrent = ""
        it.torrent_job = None
        self.queue(it, ("download",))

    def restore_torrents(self) -> None:
        """At the app's start (the main window calls it once): the torrent downloads that were not finished go on,
        each on a row again (its video -- competitor, channel, headline ... -- from the app's small torrent_rows.json;
        a download with no row kept becomes a row of its own, Just cuts unless a competitor is added). Nothing is
        started when there is no unfinished torrent (aria2c is not even started)."""
        from ..ui import background as BG

        def work():
            return NV.torrent_rows() if NV.unfinished_torrents() else None

        def got(rows) -> None:
            if rows is None:
                return
            try:
                from ..ui import torrent_panel as TP
            except Exception:  # noqa: BLE001 - not in this build
                return
            TP.restore_jobs(lambda jobs: self._restored(jobs, rows, TP))
        BG.call(work, got, lambda _e: None)

    def _restored(self, jobs: list, rows: dict, TP) -> None:
        for job in jobs:
            if any(i.torrent_job is not None and i.torrent_job.id == job.id for i in self.items):
                continue
            f = dict(rows.get(job.id) or {})
            comp_file = f.get("comp_file")
            it = Item(f.get("competitor_url") or "", raw_torrent=f.get("raw_torrent") or job.name or "torrent",
                      channel=f.get("channel") or "", title=f.get("title") or "", mode=f.get("mode") or J.FAST,
                      speed=float(f.get("speed") or 100.0), upload_title=f.get("upload_title") or "",
                      comp_file=Path(comp_file) if comp_file else None,
                      kind=f.get("kind") or (NV.FULL if (f.get("competitor_url") or comp_file) else NV.JUST_CUTS),
                      edit=bool(f.get("edit", True)))
            if f.get("added"):
                it.added = float(f["added"])
            it.name = job.name or ""
            it.state, it.started = WORKING, datetime.now()
            self.items.append(it)
            self.rows[it.id] = VideoRow(self, it)
            self._step(it, "download", S_RUN, 0.0, "continues after the restart")
            job._mr_kept = True                             # noqa: SLF001 - already kept
            self._attach_torrent(it, job, TP)
        if jobs:
            self._order(scroll_top=True)
            self._counts()
            self.toast(f"{len(jobs)} torrent download(s) continue ✓")

    @staticmethod
    def _torrent_dir() -> Path:
        """Where torrents are saved (the torrent part's Settings -> "Save torrents to"; this computer's default)."""
        try:
            from ..core import torrent as T
            return Path(T.default_dir())
        except Exception:  # noqa: BLE001 - that part not in this build
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
                    if stage == "cuts" and isinstance(res.get("result"), dict):
                        self._learn_hook(it, res["result"])     # a Just cuts run that failed: problems_queue.md
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
        if it.project is None:                 # no project.json to keep it: History's own log (a small append)
            from ..core import history as H
            H.log_event(f"{stage.capitalize()} failed", it.raw_url or str(it.raw_file or it.raw_torrent or "")[:120],
                        it.error, H.FAIL, NV.one_line(it.title), it.channel)
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
            self._step(it, "template", S_ASK, 0.0, "needs a headline")
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
                lambda e: self.toast(f"The template could not be made: {NV.plain_error(e)}", "fail"), work=True)
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
        folder = it.project

        def work() -> str:                                # project.json written off the window's thread
            try:
                from ..core import projects as PJ
                p = PJ.load(folder)
                p.update(competitor=upd, kind=NV.FULL)
                NV.keep_just_cuts(p)                      # its edit step is the full edit's from now on
                return ""
            except Exception as e:  # noqa: BLE001
                return NV.plain_error(e)

        def done(err: str) -> None:
            if err:
                self.toast(f"The project could not be changed: {err}", "fail")
                return
            if it not in self.items or it.kind == NV.FULL:
                return
            it.kind = NV.FULL
            it.result = {}
            it.default_template = False
            it.post_title = it.video_headline = None
            it.roles = ("competitor",)
            it.steps = {k2: Step() for k2, _ in it.step_list()}
            self.rows[it.id].refresh()
            self.queue(it, ("download",))
            self.toast("Competitor added -- the RAW is not downloaded again")

        off_thread(work, done, lambda e: self.toast(f"The project could not be changed: {NV.plain_error(e)}",
                                                    "fail"))
        return True

    def set_upload_title(self, it: Item, text: str) -> None:
        it.upload_title = " ".join((text or "").split())
        if it.project is not None:
            folder, text = it.project, it.upload_title

            def work() -> None:                           # project.json + title.txt: off the window's thread
                try:
                    from ..core import board
                    board.set_upload_title(folder, text)
                except Exception:  # noqa: BLE001
                    pass
            off_thread(work, None)
        if it.job is not None:
            it.job.upload_title = it.upload_title
        self.toast("YouTube title set ✓")

    def send_to_edit(self, it: Item, start: bool = False) -> None:
        """The ready video into the Match cuts queue (its run folder: the project's 'edit'), started by itself."""
        from ..core import flow
        from ..core import projects as PJ
        mc = self.ctx.pages.get("match_cuts") if hasattr(self.ctx, "pages") else None
        if mc is None:
            self._fail(it, "edit", "the Match cuts queue is not there")
            return
        if it.state == SENT and it.job is None:
            return                                        # being sent already (a second press)
        # project.json + title.txt written and read back (a network folder on the laptop): off the window's thread
        # (day 12 click-through: 60-140 ms here). SENT at once: "Turn off the PC" counts it as work meanwhile.
        it.state = SENT
        it.job = None
        self._step(it, "edit", S_RUN, 0.0, "going into the Match cuts queue")
        folder, channel, title, fast, speed = it.project, it.channel, it.title, it.mode == J.FAST, it.speed

        def work():
            try:
                p = PJ.load(folder)
                p.update(channel=channel or None, title=title or None)
                save_title(p.folder, title)
                raw_name = (p.data.get("raw") or {}).get("file") if isinstance(p.data.get("raw"), dict) else None
                if raw_name and not flow.chosen_roles(p.data):
                    # the New video form's RAW field is your choice (day 12, section 4): a 30 s RAW for a 26 s Short
                    # runs instead of waiting amber in the queue (the conversion already made the longer one the RAW
                    # when the two links were in the wrong fields)
                    flow.set_roles(p.folder, raw_name, "form")
                    p = PJ.load(folder)
                return flow.edit_job(p, fast, speed)
            except Exception as e:  # noqa: BLE001 - said on the row in plain words
                return NV.plain_error(e)

        def done(job) -> None:
            if it.state != SENT or it.job is not None or it not in self.items:
                return                                    # removed or changed meanwhile
            if isinstance(job, str):
                self._fail(it, "edit", f"could not make the edit's job: {job}")
                return
            job.channel, job.title, job.headline = it.channel, it.title, it.headline
            job.upload_title = it.upload_title
            it.job = job
            self._step(it, "edit", S_RUN, 0.0, "in the Match cuts queue")
            mc.add_project_job(job, autostart=start)
            self._order()

        off_thread(work, done, lambda e: self._fail(it, "edit", f"could not make the edit's job: "
                                                               f"{NV.plain_error(e)}"))
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
            elif j.state == J.STOPPED:                   # stopped in the queue before it ran
                self._finish(it, {"label": "STOPPED"})
            elif waits_for_pick(it) != it.said_pick:     # the queue asks which is the RAW (or you picked):
                it.said_pick = waits_for_pick(it)        # said on the row
                self._refresh(it)
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
        else:                             # day 13: a stop says why in plain words and what to do (reason_line)
            self._fail(it, key, res.get("reason_line") or (res.get("failures") or [lab])[0])
        if it.kind == NV.JUST_CUTS and lab != "STOPPED":
            self._learn_hook(it, res)
        self._refresh(it)
        self._order()

    def _learn_hook(self, it: Item, res: dict) -> None:
        """A Just cuts run ended (the full edits end in the Match cuts queue, which calls the same hook): a FAIL or an
        OK with a note goes into problems_queue.md like every run; no "Waiting for your fix" card (no competitor:
        nothing to learn)."""
        try:
            from types import SimpleNamespace

            from .learn_tool import after_edit
            snap = SimpleNamespace(project=it.project, name=it.name, run_dir=res.get("run_dir"), raw=it.raw_file,
                                   competitor=None, channel=it.channel, title=it.title)
            after_edit(self.ctx, snap, dict(res), waiting_card=False)
        except Exception:  # noqa: BLE001 - the hook never breaks the row
            pass

    # -- the row's buttons --
    def retry(self, it: Item) -> None:
        """Try again: continues from the step that failed (the done ones are kept)."""
        stage = it.failed_step or "download"
        it.error = ""
        it.result = {}
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
        w = it.torrent_widget
        if it.torrent_job is None and w is not None and hasattr(w, "cancel"):     # the torrent's file list: Cancel
            w.cancel()
            return
        mine = self.current is not None and self.current.item.id == it.id
        if it.torrent_job is not None and not mine:
            it.torrent_job.pause()                    # a torrent download: paused (Resume on its card goes on)
            it.message = "Paused -- press Resume on the card"
            self._refresh(it)
            return
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

    def _xml_path(self, it: Item) -> Path | None:
        rd = it.result.get("run_dir") if it.result else None
        if not rd and it.job is not None and getattr(it.job, "run_dir", None):
            rd = it.job.run_dir
        return Path(rd) / "1_edit.xml" if rd else None

    def xml_of(self, it: Item) -> Path | None:
        """The finished edit's 1_edit.xml, when it is there. Never a disk look on the window's thread (the row's
        refresh asks often and the run folder may be on the shared folder over the network): the first ask looks in
        the background and refreshes the row; the answer is remembered."""
        f = self._xml_path(it)
        if f is None:
            return None
        seen = getattr(self, "_xml_seen", None)
        if seen is None:
            seen = self._xml_seen = {}
        key = str(f)
        if key in seen:
            return f if seen[key] else None
        seen[key] = False                                   # asked once; "there" when the look says so

        def got(there) -> None:
            seen[key] = bool(there)
            if there:
                self._refresh(it)
        off_thread(f.is_file, got, lambda _e: seen.pop(key, None))
        return f if seen.get(key) else None                 # (a test's off_thread answers at once)

    def open_premiere(self, it: Item) -> None:
        xml = self._xml_path(it)
        if xml is None:
            self.toast("The edit's 1_edit.xml is not there", "fail")
            return
        from .match_cuts_tool import open_in_premiere

        def work():
            if not xml.is_file():
                return "The edit's 1_edit.xml is not there"
            return open_in_premiere(xml)
        self.toast("Opening in Premiere…")
        off_thread(work, lambda r: self.toast(str(r)) if r else None,
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
        if any(_torrent_active(i.torrent_job) for i in self.items):
            return True
        from ..ui import background as BG
        if BG.working():                # publishing to the shared folder, Clean up, Change headline ... (work=True)
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
            stop = getattr(self.warning, "stop_for_work", None)
            if stop is not None and self.warning.isVisible():
                stop()                                  # work started again during the warning: not now
            return
        if not self._worked or (self.warning is not None and self.warning.isVisible()):
            return
        self._worked = False
        self.warning = self.shutdown_dialog(self)
        self.warning.rejected.connect(lambda: self.offpc.setChecked(False))
        sig = getattr(self.warning, "stopped_for_work", None)
        if sig is not None:
            sig.connect(self._stopped_for_work)
        self.warning.start()

    def _stopped_for_work(self) -> None:
        """The warning closed because work started again: a fresh 60 s warning once that work is done."""
        self._worked = True

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
            if waits_for_pick(it):
                return ("Which one is the RAW? The two videos are about as long -- pick the RAW on its row in the "
                        "Match cuts tab, then the edit starts")
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
        """The rows in the order shown -- the queue's rules: running on top, then waiting newest first, then finished
        with the one finished last on top (Item.order_time); they glide there. A new row scrolls the page to the top
        (the list's ``inserted``, as in the queue; ``scroll_top`` does it anyway)."""
        ordered = NV.order(self.items, added=lambda i: i.order_time())
        self.list.set_rows([self.rows[i.id] for i in ordered if i.id in self.rows])
        self._counts()                                    # "N video(s) · N running · N waiting for you" kept true
        if scroll_top:
            scroll_to_top(self)

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
