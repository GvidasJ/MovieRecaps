"""The Download tab (night 10): paste the competitor's link and the RAW's link (or drop the RAW file), pick the channel
and type the title; each video gets its own project folder (input\\downloads\\<date>_<short name>, project.json in
it) and goes through download (yt-dlp: H.264 + AAC MP4 at the best resolution; VP9 / AV1 only when it is bigger) ->
convert (kept / rewrapped / visually lossless H.264, checked, the original deleted) -> the competitor's headline read
and your title checked -> your channel's template -> the edit (the Match cuts queue, started by itself when you ask).

Several videos can be queued; they are prepared one after the other (the two files of one video one after the other,
each with its own progress bar). Errors are said in plain words (private video, region blocked, the link is wrong).
Every step is core.flow's, so the command line (steps.py) and part 2's board do exactly the same."""
from __future__ import annotations

import itertools
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLineEdit,
                               QVBoxLayout, QWidget)

from . import Tool
from ..core import jobs as J
from ..core import paths
from ..ui import theme
from ..ui import widgets as W

_ids = itertools.count(1)
ROLE_NAMES = {"competitor": "Competitor", "raw": "RAW"}

# item states
QUEUED, WORKING, READY, FAILED, STOPPED, SENT = "queued", "working", "ready", "failed", "stopped", "sent"


@dataclass
class Item:
    """One video in the Download queue (a project folder once its link was read)."""
    competitor_url: str
    raw_url: str = ""
    raw_file: Path | None = None
    channel: str = ""
    title: str = ""
    mode: str = J.FAST
    speed: float = 100.0
    edit: bool = True                       # when ready: into the Match cuts queue, started by itself
    state: str = QUEUED
    project: Path | None = None
    name: str = ""
    files: dict = field(default_factory=lambda: {r: [0.0, "waiting"] for r in ROLE_NAMES})
    headline: str | None = None
    message: str = ""
    error: str = ""
    upload_title: str = ""                  # night 11: the title the Short is uploaded with (optional)
    id: int = field(default_factory=lambda: next(_ids))


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


class PrepareThread(QThread):
    """One video's preparation in the background (core.flow): read the links, make the project folder, download,
    convert, read the competitor's headline, make the template. Signals: ``named(id, folder)``, ``progress(id, role,
    share, text)``, ``done(id, result dict)`` -- {'ok': bool, 'error': plain words, 'headline', 'check'}."""
    named = Signal(int, str)
    progress = Signal(int, str, float, str)
    done = Signal(int, dict)

    def __init__(self, item: Item):
        super().__init__()
        self.item = item
        self.cancel = threading.Event()

    def run(self) -> None:
        from ..core import flow
        from ..core import projects as PJ
        it = self.item
        say = lambda role, share, text: self.progress.emit(it.id, role, float(share), str(text))   # noqa: E731
        try:
            if it.project is None:
                p = self._create(say)
            else:
                p = PJ.load(it.project)
            flow.download_step(p, say, self.cancel) if p.step("download").get("state") != PJ.DONE else None
            if self.cancel.is_set():
                raise flow.StepError("stopped by you")
            flow.convert_step(p, say, self.cancel) if p.step("convert").get("state") != PJ.DONE else None
            if self.cancel.is_set():
                raise flow.StepError("stopped by you")
            say("competitor", 1.0, "reading its headline ...")
            tc = None
            try:
                tc = flow.title_check_step(p, title=it.title or "")
            except Exception as e:  # noqa: BLE001 - a help, never a stop
                tc = {"competitor_headline": "", "message": f"the headline could not be read ({e})"}
            conv = p.reload().get("steps", {}).get("convert") or {}
            for role in ROLE_NAMES:
                st = conv.get(role) or {}
                say(role, 1.0, flow.convert_text(st) if isinstance(st, dict) and st else "ready")
            self.done.emit(it.id, {"ok": True, "headline": (tc or {}).get("competitor_headline"),
                                   "check": (tc or {}).get("message", ""), "project": str(p.folder)})
        except Exception as e:  # noqa: BLE001 - said in plain words
            msg = "stopped by you" if self.cancel.is_set() else (str(e) or type(e).__name__)
            self.done.emit(it.id, {"ok": False, "error": msg, "project": str(it.project) if it.project else ""})

    def _create(self, say):
        """Read the competitor's link (its title names the folder) and the RAW's, check the free space, make the
        project folder."""
        from ..core import download as D
        from ..core import flow, space
        it = self.item
        say("competitor", 0.0, "reading the link ...")
        try:
            li = D.probe_link(it.competitor_url, cancel=self.cancel)
        except D.DownloadError as e:
            say("competitor", 0.0, f"FAILED: {e.plain}")
            raise flow.StepError(f"competitor: {e.plain}") from e
        need = estimate_bytes(li)
        say("competitor", 0.0, f"{li.title[:70]} -- {D.clock(li.duration)} ({li.why})")
        if it.raw_url:
            say("raw", 0.0, "reading the link ...")
            try:
                lr = D.probe_link(it.raw_url, cancel=self.cancel)
            except D.DownloadError as e:
                say("raw", 0.0, f"FAILED: {e.plain}")
                raise flow.StepError(f"RAW: {e.plain}") from e
            need += estimate_bytes(lr)
            say("raw", 0.0, f"{lr.title[:70]} -- {D.clock(lr.duration)} ({lr.why})")
        elif it.raw_file is not None:
            n = space.size(it.raw_file)
            need += 0 if space.same_drive(it.raw_file, paths.projects_root()) else n     # copied, not linked
            try:                                              # converted next to it first: room for the new file
                from ..core import convert as C
                how = C.plan(C.inspect(it.raw_file)).how
                need += {"encode": 3 * n, "rewrap": n}.get(how, 0)
            except Exception:  # noqa: BLE001 - not readable: the convert step says so
                pass
            say("raw", 0.0, f"the file {Path(it.raw_file).name} ({D.size_text(n)})")
        root = paths.projects_root()
        free = space.free_bytes(root)
        want = int(need) + space.GB                        # (estimate_bytes counts the conversion's copy too)
        if free is not None and need and free < want:
            raise flow.StepError(f"not enough free space on {space.drive_name(root)}: these videos need about "
                                 f"{want / space.GB:.1f} GB while they are downloaded and converted, only "
                                 f"{free / space.GB:.1f} GB is free")
        p = flow.new_project(it.competitor_url, it.raw_url or None, it.raw_file, it.channel or None,
                             it.title or None, root=root, name=li.title)
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


class FileLine(QWidget):
    """'Competitor  ▬▬▬▬▬▬  34 % -- 12.3 MB of 80 MB ...' for one of a video's two files."""

    def __init__(self, role: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        name = W.label(ROLE_NAMES[role], "Small")
        name.setFixedWidth(84)
        lay.addWidget(name)
        self.bar = W.GlideBar(3)
        self.bar.setFixedWidth(220)
        lay.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = W.label("", "Muted", wrap=True, select=True)
        lay.addWidget(self.text, 1)

    def set(self, share: float, text: str) -> None:
        share = max(0.0, min(1.0, share))
        bad = text.startswith("FAILED")
        self.bar.state("fail" if bad else ("pass" if share >= 1.0 else ("running" if share > 0 else "waiting")))
        self.bar.set(share)
        self.text.setText(text)
        self.text.setStyleSheet(f"color: {theme.RED};" if bad else "")


class ItemRow(W.Card):
    """A video in the Download queue: its name and state, the two files' progress, the headline and the title check,
    its template, and the buttons."""

    def __init__(self, page: "DownloadPage", item: Item):
        super().__init__()
        self.page, self.item = page, item
        top = QHBoxLayout()
        top.setSpacing(12)
        self.name = W.label("", "Name")
        top.addWidget(self.name, 1)
        self.state = W.StateLabel()
        top.addWidget(self.state, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lay.addLayout(top)
        self.lines = {r: FileLine(r) for r in ROLE_NAMES}
        for r in ROLE_NAMES:
            self.lay.addWidget(self.lines[r])
        self.head = W.label("", "Small", wrap=True, select=True)
        self.lay.addWidget(self.head)
        self.tpl = W.label("", "Small", wrap=True, select=True)
        self.lay.addWidget(self.tpl)
        self.title_box = W.TitleBox(item.title)
        self.title_box.copied.connect(lambda _t: getattr(self.page.ctx, "toast", lambda *a: None)("Title copied ✓"))
        self.lay.addWidget(self.title_box)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.b_tpl = W.Button("Template", "Small")
        self.b_folder = W.Button("Open folder", "Small")
        self.b_edit = W.Button("Make the edit", "Small")
        self.b_retry = W.Button("Try again", "Small")
        self.b_stop = W.Button("Stop", "Small")
        self.b_remove = W.Button("Remove", "Ghost")
        for b in (self.b_tpl, self.b_folder, self.b_edit, self.b_retry, self.b_stop):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.b_remove)
        self.b_tpl.clicked.connect(self._template)
        self.b_folder.clicked.connect(self._folder)
        self.b_edit.clicked.connect(lambda: self.page.send_to_edit(self.item, start=True))
        self.b_retry.clicked.connect(lambda: self.page.retry(self.item))
        self.b_stop.clicked.connect(lambda: self.page.stop_item(self.item))
        self.b_remove.clicked.connect(lambda: self.page.remove_item(self.item))
        self.lay.addLayout(btns)
        self.refresh()

    def _folder(self) -> None:
        if self.item.project is not None and Path(self.item.project).exists():
            os.startfile(str(self.item.project))                  # noqa: S606

    def _template(self) -> None:
        from ..ui.template_editor import TemplateDialog
        it = self.item
        comp = None
        if it.project is not None:
            try:
                from ..core import projects as PJ
                comp = PJ.load(it.project).file("competitor")
            except Exception:  # noqa: BLE001
                comp = None
        d = TemplateDialog(self, it.channel, it.title, it.headline, comp if comp and comp.is_file() else None,
                           it.name or "video")
        d.ed.headline_found.connect(lambda h: setattr(it, "headline", h))
        if d.exec():
            it.channel, it.title = d.ed.channel(), d.ed.title()
            if it.project is not None:
                try:
                    from ..core import projects as PJ
                    PJ.load(it.project).update(channel=it.channel or None, title=it.title or None)
                    save_title(it.project, it.title)
                except Exception:  # noqa: BLE001
                    pass
            self.refresh()

    def refresh(self) -> None:
        it = self.item
        self.name.setText(it.name or it.competitor_url[:80])
        words = {QUEUED: ("waiting", "in the queue", theme.AMBER), WORKING: ("running", it.message or "working",
                                                                             theme.BLUE),
                 READY: ("PASS", "ready -- both files are in the project folder", theme.GREEN),
                 FAILED: ("FAIL", it.error, theme.RED), STOPPED: ("STOPPED", "stopped by you", theme.DIM),
                 SENT: ("PASS", it.message if it.message.startswith("in the Match cuts") else
                        "ready -- in the Match cuts queue", theme.GREEN)}
        w, note, colour = words[it.state]
        self.state.set(w if w in ("waiting", "running") else w, note, colour)
        for r, (share, text) in it.files.items():
            self.lines[r].set(share, text)
        if it.headline is None:
            self.head.setText("Competitor's headline: read after the download")
        elif not it.headline:
            self.head.setText("Competitor's headline: none found")
        else:
            msg = ""
            if it.title:
                from ..core import title_check as T
                c = T.check(it.title, it.headline)
                msg = f"  ·  {'TOO CLOSE -- ' if c.warn else ''}{c.message}"
            self.head.setText(f"Competitor's headline: “{it.headline}”{msg}")
        self.tpl.setText(f"Template: {it.channel} -- “{it.title}”" if it.channel and it.title else
                         (f"Template: {it.channel} -- NO TITLE yet (press Template)" if it.channel else
                          "Template: default (templates\\default.png), no watermark"))
        self.title_box.set(it.title)
        busy = it.state == WORKING
        self.b_stop.setVisible(busy)
        self.b_retry.setVisible(it.state in (FAILED, STOPPED))
        self.b_edit.setVisible(it.state == READY)
        self.b_folder.setVisible(it.project is not None)
        self.b_remove.setEnabled(not busy)


class DownloadPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.items: list[Item] = []
        self.rows: dict[int, ItemRow] = {}
        self.current: PrepareThread | None = None
        self.raw_file: Path | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(16)
        lay.addWidget(W.SectionHeader("Download"))
        lay.addWidget(W.label("Paste the competitor's link and the RAW's link (or drop the RAW file). Both are "
                              "downloaded in the best quality (H.264 MP4; VP9 / AV1 only when bigger, then converted "
                              "without visible loss), checked, and put in one folder per video -- then your template "
                              "and the edit. Ctrl+V anywhere pastes a link here.", "Muted", wrap=True))
        form = W.Card(hover=False)
        self.comp = QLineEdit()
        self.comp.setPlaceholderText("https://www.youtube.com/shorts/...")
        self.raw = QLineEdit()
        self.raw.setPlaceholderText("https://www.youtube.com/watch?v=...   (or drop the RAW file below)")
        self.raw.textEdited.connect(self._raw_typed)
        form.lay.addWidget(W.label("Competitor's link", "Small"))
        form.lay.addWidget(self.comp)
        form.lay.addWidget(W.label("RAW's link -- or its file", "Small"))
        rr = QHBoxLayout()
        rr.addWidget(self.raw, 1)
        pick = W.Button("Choose the RAW file...")
        pick.clicked.connect(self._pick_raw)
        rr.addWidget(pick)
        form.lay.addLayout(rr)
        self.drop = W.DropZone("Drop the RAW file here", ["instead of a link"], big=False)
        self.drop.setToolTip("It goes into the video's folder: linked on the same drive (no copy), else copied")
        self.drop.dropped.connect(self._dropped)
        form.lay.addWidget(self.drop)
        from ..ui.template_editor import NONE, channel_names
        opts = [(NONE, "Default")] + [(n, n) for n in channel_names()]
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(W.label("Channel", "Small"))
        self.ch = W.Segmented(opts, NONE)
        row.addWidget(self.ch)
        row.addStretch(1)
        form.lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(W.label("Title", "Small"))
        self.title = QLineEdit()
        self.title.setPlaceholderText("your headline (emojis allowed) -- or later, with the Template button")
        row2.addWidget(self.title, 1)
        form.lay.addLayout(row2)
        row2b = QHBoxLayout()
        row2b.addWidget(W.label("YouTube title", "Small"))
        self.yt = QLineEdit()
        self.yt.setPlaceholderText("optional: the title you upload the Short with -- Copy shows it everywhere, and "
                                   "the Uploaded check finds it")
        row2b.addWidget(self.yt, 1)
        form.lay.addLayout(row2b)
        row3 = QHBoxLayout()
        row3.setSpacing(12)
        self.auto = QCheckBox("Then make the edit by itself (the Match cuts queue starts)")
        self.auto.setChecked(True)
        row3.addWidget(self.auto)
        self.mode = W.Segmented([(J.FAST, "Fast"), (J.THOROUGH, "Thorough")], J.FAST)
        row3.addWidget(self.mode)
        self.speed = W.Segmented([("100", "100 %"), ("125", "125 %")], "100")
        row3.addWidget(self.speed)
        row3.addStretch(1)
        add = W.Button("Add to the queue", "Primary")
        add.clicked.connect(self.add)
        row3.addWidget(add)
        form.lay.addLayout(row3)
        self.msg = W.label("", "Muted", wrap=True, select=True)
        form.lay.addWidget(self.msg)
        lay.addWidget(form)
        lay.addSpacing(10)
        self.count = W.label("", "Small")
        lay.addWidget(W.SectionHeader("Queue", [self.count]))
        self.list = QVBoxLayout()
        self.list.setSpacing(12)
        lay.addLayout(self.list)
        self.empty = W.label("Nothing here yet. Paste two links above.", "Dim")
        lay.addWidget(self.empty)
        lay.addSpacing(10)
        self.where = W.label("", "Dim", wrap=True, select=True)
        lay.addWidget(self.where)
        lay.addStretch(1)
        self._where()

    # -- the form --
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

    def paste_link(self, text: str) -> bool:
        """Ctrl+V anywhere: a link goes into the competitor's field (the RAW's when that one is filled already).
        False when the text is not a link."""
        t = (text or "").strip().strip('"')
        if not (t.startswith("http://") or t.startswith("https://") or t.startswith("www.") or "youtu" in t):
            return False
        if not self.comp.text().strip():
            self.comp.setText(t)
            self.comp.setFocus()
        elif not self.raw.text().strip():
            self.raw.setText(t)
            self.raw.setFocus()
        else:
            self.comp.setText(t)
        self.msg.setText("link pasted -- add the other link (or drop the RAW file), pick the channel, type the title")
        return True

    def _raw_typed(self, _t: str) -> None:
        if self.raw_file is not None and self.raw.text() != str(self.raw_file):
            self.raw_file = None

    def _pick_raw(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "The RAW file", str(paths.repo() / "input"),
                                           "Videos (" + " ".join(f"*{e}" for e in sorted(J.VIDEO_EXTS)) + ")")
        if f:
            self._set_raw_file(Path(f))

    def _dropped(self, ps: list) -> None:
        vids = [Path(p) for p in ps if J.is_video(Path(p))]
        if vids:
            self._set_raw_file(vids[0])
        else:
            self.msg.setText("that is not a video file")

    def _set_raw_file(self, f: Path) -> None:
        self.raw_file = f
        self.raw.setText(str(f))
        self.msg.setText(f"RAW file: {f.name}")

    def add(self) -> None:
        from ..core import download as D
        comp = D.clean_link(self.comp.text())
        raw_file = self.raw_file if self.raw_file is not None and self.raw.text() == str(self.raw_file) else None
        typed = self.raw.text().strip().strip('"')
        if raw_file is None and typed and J.is_video(Path(typed)):  # a file's path pasted instead of a link
            raw_file = Path(typed)
        raw = "" if raw_file is not None else D.clean_link(self.raw.text())
        probs = [f"Competitor: {p}" for p in [D.link_problem(comp)] if p]
        if raw_file is None:
            probs += [f"RAW: {p}" for p in [D.link_problem(raw) if raw else "paste the RAW's link or drop its file"]
                      if p]
        if probs:
            self.msg.setText("; ".join(probs))
            self.msg.setStyleSheet(f"color: {theme.RED};")
            return
        self.msg.setStyleSheet("")
        it = Item(comp, raw, raw_file, self.ch.value, self.title.text().strip(), self.mode.value,
                  float(self.speed.value), self.auto.isChecked())
        it.upload_title = self.yt.text().strip()
        self.items.append(it)
        row = ItemRow(self, it)
        self.rows[it.id] = row
        self.list.addWidget(row)
        self.comp.clear()
        self.raw.clear()
        self.raw_file = None
        self.title.clear()
        self.yt.clear()
        self.msg.setText("added -- you can paste the next one")
        self._counts()
        self._next()

    # -- the queue --
    def _counts(self) -> None:
        n = len(self.items)
        busy = sum(1 for i in self.items if i.state in (QUEUED, WORKING))
        self.count.setText(f"{n} video(s)" + (f"  ·  {busy} to prepare" if busy else "") if n else "")
        self.empty.setVisible(not self.items)

    def _next(self) -> None:
        if self.current is not None:
            return
        it = next((i for i in self.items if i.state == QUEUED), None)
        if it is None:
            return
        it.state = WORKING
        it.error = ""
        th = PrepareThread(it)
        th.named.connect(self._named)
        th.progress.connect(self._progress)
        th.done.connect(self._done)
        self.current = th
        self.rows[it.id].refresh()
        self._counts()
        th.start()

    def _item(self, item_id: int) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def _named(self, item_id: int, folder: str) -> None:
        it = self._item(item_id)
        if it is not None:
            it.project, it.name = Path(folder), Path(folder).name
            self.rows[it.id].refresh()

    def _progress(self, item_id: int, role: str, share: float, text: str) -> None:
        it = self._item(item_id)
        if it is None or role not in it.files:
            return
        it.files[role] = [share, text]
        it.message = f"{ROLE_NAMES[role]}: {text}"
        self.rows[it.id].refresh()

    def _done(self, item_id: int, res: dict) -> None:
        th, self.current = self.current, None
        if th is not None:
            th.wait(2000)
        it = self._item(item_id)
        if it is not None:
            if res.get("ok"):
                it.state = READY
                it.headline = res.get("headline") or ""
                if it.edit:
                    self.send_to_edit(it, start=True)
            else:
                it.state = STOPPED if res.get("error") == "stopped by you" else FAILED
                it.error = res.get("error") or "failed"
            self.rows[it.id].refresh()
            self.ctx.notify("Download: " + (it.name or "a video"),
                            "ready" if res.get("ok") else f"failed: {it.error}")
        self._counts()
        self._next()

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
            it.state, it.error = FAILED, f"could not make the edit's job: {e}"
            self.rows[it.id].refresh()
            return
        mc = self.ctx.pages.get("match_cuts")
        if mc is None:
            return
        waits = bool(it.channel and not it.title)          # a channel's template without its headline: not by itself
        mc.add_project_job(job, autostart=start and not waits)
        it.state = SENT
        if waits:
            it.message = "in the Match cuts queue -- type the title there (Template) and press Start"
            mc.say(f"{job.name}: type its title (Template on its row), then press Start")
        self.rows[it.id].refresh()

    def retry(self, it: Item) -> None:
        it.state = QUEUED
        it.error = ""
        it.files = {r: [0.0, "waiting"] for r in ROLE_NAMES}
        if it.project is not None:                         # a failed step runs again; the done ones are kept
            try:
                from ..core import projects as PJ
                p = PJ.load(it.project)
                for s in ("download", "convert"):
                    if p.step(s).get("state") != PJ.DONE:
                        p.set_step(s, PJ.WAITING)
            except Exception:  # noqa: BLE001
                pass
        self.rows[it.id].refresh()
        self._counts()
        self._next()

    def stop_item(self, it: Item) -> None:
        if self.current is not None and self.current.item.id == it.id:
            self.current.cancel.set()
            it.message = "stopping ..."
            self.rows[it.id].refresh()

    def remove_item(self, it: Item) -> None:
        if it.state == WORKING:
            return
        self.items = [i for i in self.items if i.id != it.id]
        row = self.rows.pop(it.id, None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self._counts()

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
