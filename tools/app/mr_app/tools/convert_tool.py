"""The Convert tab (night 10): drop any video to make it Premiere-friendly, like Shutter Encoder but with FFmpeg
(core.convert): already H.264 (or another codec Premiere handles well) -> only rewrapped to MP4, no re-encode (100 %
lossless); VP9 / AV1 / anything Premiere handles badly -> visually lossless H.264 (the graphics card when it gives the
same quality, else the processor), the resolution, frame rate, colours and sound kept. The original stays until the
new file is checked (the same frame count and length), then it is deleted (switch that off below). The Download tab
does the same by itself after every download."""
from __future__ import annotations

import itertools
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from . import Tool
from ..core import jobs as J
from ..core import paths
from ..ui import widgets as W

_ids = itertools.count(1)


@dataclass
class ConvItem:
    path: Path
    state: str = "waiting"          # waiting | running | done | failed | stopped
    share: float = 0.0
    text: str = "waits for its turn"
    result: dict = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_ids))


def result_text(res) -> str:
    """A convert.ConvertResult in plain words."""
    how = getattr(res, "how", "")
    name = Path(getattr(res, "path", "")).name
    ck = getattr(res, "checks", None) or {}
    frames = ck.get("frames_out") or ck.get("frames") or ""
    if isinstance(frames, (list, tuple)) and len(frames) == 2:       # [the original's, the new file's]
        frames = f"{frames[1]} of {frames[0]}"
    checked = f", checked: {frames} frames" if frames else ", checked"
    gone = "; the original is deleted" if getattr(res, "deleted_original", False) else ""
    took = getattr(res, "seconds", None)
    took = f" ({took:.0f} s)" if isinstance(took, (int, float)) else ""
    if how == "keep":
        return f"already fine (H.264 MP4): kept as it is -- {name}"
    if how == "rewrap":
        return f"rewrapped to MP4, no re-encode (lossless){checked}{gone} -- {name}{took}"
    if how == "encode":
        enc = getattr(res, "encoder", "") or "H.264"
        src = str(getattr(res, "from_codec", "") or "").upper()
        return f"{src} converted to visually lossless H.264 ({enc}){checked}{gone} -- {name}{took}"
    return f"done -- {name}"


class ConvertThread(QThread):
    progress = Signal(int, float, str)
    done = Signal(int, bool, str)

    def __init__(self, item: ConvItem, delete_original: bool, gpu: str):
        super().__init__()
        self.item, self.delete, self.gpu = item, delete_original, gpu
        self.cancel = threading.Event()

    def run(self) -> None:
        from ..core import convert as C
        it = self.item
        try:
            log = paths.work_dir() / "convert_logs" / f"{it.path.stem[:60]}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            res = C.convert_file(it.path, delete_original=self.delete, gpu=self.gpu, cancel=self.cancel,
                                 progress=lambda share, text="": self.progress.emit(it.id, float(share), str(text)),
                                 log_path=log)
            self.done.emit(it.id, True, result_text(res))
        except Exception as e:  # noqa: BLE001 - said in plain words
            self.done.emit(it.id, False, "stopped by you" if self.cancel.is_set() else (str(e) or type(e).__name__))


class ConvRow(W.Card):
    def __init__(self, page: "ConvertPage", item: ConvItem):
        super().__init__()
        self.page, self.item = page, item
        top = QHBoxLayout()
        self.name = W.label(item.path.name, "Name")
        self.name.setToolTip(str(item.path))
        top.addWidget(self.name, 1)
        self.state = W.StateLabel()
        top.addWidget(self.state)
        self.lay.addLayout(top)
        self.bar = W.GlideBar(3)
        self.lay.addWidget(self.bar)
        btns = QHBoxLayout()
        self.b_folder = W.Button("Open folder", "Small")
        self.b_folder.clicked.connect(lambda: os.startfile(str(self.item.path.parent)))   # noqa: S606
        self.b_stop = W.Button("Stop", "Small")
        self.b_stop.clicked.connect(lambda: self.page.stop_item(self.item))
        self.b_remove = W.Button("Remove", "Ghost")
        self.b_remove.clicked.connect(lambda: self.page.remove_item(self.item))
        btns.addWidget(self.b_folder)
        btns.addWidget(self.b_stop)
        btns.addStretch(1)
        btns.addWidget(self.b_remove)
        self.lay.addLayout(btns)
        self.refresh()

    def refresh(self) -> None:
        it = self.item
        word = {"waiting": "waiting", "running": "running", "done": "PASS", "failed": "FAIL",
                "stopped": "STOPPED"}[it.state]
        self.state.set(word, it.text)
        self.bar.state(word)
        self.bar.set(it.share)
        self.bar.setVisible(it.state in ("running", "done"))
        self.b_stop.setVisible(it.state == "running")
        self.b_remove.setEnabled(it.state != "running")


class ConvertPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.items: list[ConvItem] = []
        self.rows: dict[int, ConvRow] = {}
        self.current: ConvertThread | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(16)
        lay.addWidget(W.SectionHeader("Convert"))
        lay.addWidget(W.label("Drop any video: H.264 is only rewrapped to MP4 (no re-encode, lossless); VP9, AV1 and "
                              "other codecs Premiere handles badly become visually lossless H.264 -- the same "
                              "resolution, frame rate, colours and sound. The new file is checked (frames and "
                              "length) before the original goes.", "Muted", wrap=True))
        self.drop = W.DropZone("Drop videos here", ["one or many, or a folder (every video in it) -- converted one "
                                                    "after the other, next to the original"],
                               [("Choose videos...", self._pick)])
        self.drop.dropped.connect(self.add_paths)
        lay.addWidget(self.drop)
        opts = QHBoxLayout()
        self.delete = QCheckBox("Delete the original when the new file is checked")
        self.delete.setChecked(True)
        opts.addWidget(self.delete)
        opts.addSpacing(18)
        opts.addWidget(W.label("Encoder", "Small"))
        self.gpu = W.Segmented([("auto", "Auto"), ("cpu", "Processor"), ("gpu", "Graphics card")], "auto")
        self.gpu.setToolTip("Auto: the graphics card (NVENC) when it gives the same quality as the processor "
                            "(measured), else the processor -- only for files that need a re-encode")
        opts.addWidget(self.gpu)
        opts.addStretch(1)
        lay.addLayout(opts)
        self.count = W.label("", "Small")
        lay.addWidget(W.SectionHeader("Queue", [self.count]))
        self.list = QVBoxLayout()
        self.list.setSpacing(12)
        lay.addLayout(self.list)
        self.empty = W.label("Nothing here yet. Drop a video above.", "Dim")
        lay.addWidget(self.empty)
        lay.addStretch(1)
        self._counts()

    def _pick(self) -> None:
        fs, _ = QFileDialog.getOpenFileNames(self, "Videos to convert", str(paths.repo() / "input"),
                                             "Videos (" + " ".join(f"*{e}" for e in sorted(J.VIDEO_EXTS)) + ")")
        if fs:
            self.add_paths([Path(f) for f in fs])

    def add_paths(self, ps: list) -> None:
        vids: list[Path] = []
        for p in map(Path, ps):
            if p.is_dir():
                vids += J.videos_in(p)
            elif J.is_video(p):
                vids.append(p)
        queued = {i.path.resolve() for i in self.items if i.state in ("waiting", "running")}
        for v in vids:
            if v.resolve() in queued:
                continue
            it = ConvItem(v)
            self.items.append(it)
            row = ConvRow(self, it)
            self.rows[it.id] = row
            self.list.addWidget(row)
        self._counts()
        self._next()

    def _counts(self) -> None:
        n = len(self.items)
        self.count.setText(f"{n} video(s)" if n else "")
        self.empty.setVisible(not self.items)

    def _next(self) -> None:
        if self.current is not None:
            return
        it = next((i for i in self.items if i.state == "waiting"), None)
        if it is None:
            return
        it.state, it.text = "running", "starting ..."
        th = ConvertThread(it, self.delete.isChecked(), self.gpu.value)
        th.progress.connect(self._progress)
        th.done.connect(self._done)
        self.current = th
        self.rows[it.id].refresh()
        th.start()

    def _item(self, i: int) -> ConvItem | None:
        return next((x for x in self.items if x.id == i), None)

    def _progress(self, item_id: int, share: float, text: str) -> None:
        it = self._item(item_id)
        if it is not None:
            it.share, it.text = share, text or it.text
            self.rows[it.id].refresh()

    def _done(self, item_id: int, ok: bool, text: str) -> None:
        th, self.current = self.current, None
        if th is not None:
            th.wait(2000)
        it = self._item(item_id)
        from ..core import space
        from ..ui import health
        if it is not None:
            it.state = "done" if ok else ("stopped" if text == "stopped by you" else "failed")
            it.text, it.share = text, 1.0 if ok else it.share
            self.rows[it.id].refresh()
            if not ok and space.is_space_error(text):        # day 13: no room -- the numbers, and Clean up
                QTimer.singleShot(0, self, lambda t=text, n=it.path.name: health.offer_cleanup(self.ctx, self,
                                                                                             f"{n}: {t}"))
        health.space_changed(self.ctx)                       # day 13: the strip's free space read again
        if not any(i.state == "waiting" for i in self.items):
            done = sum(1 for i in self.items if i.state == "done")
            self.ctx.notify("Convert: done", f"{done} of {len(self.items)} converted")
        self._next()

    def stop_item(self, it: ConvItem) -> None:
        if self.current is not None and self.current.item.id == it.id:
            self.current.cancel.set()

    def remove_item(self, it: ConvItem) -> None:
        if it.state == "running":
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


class ConvertTool(Tool):
    key = "convert"
    title = "Convert"

    def page(self, ctx) -> QWidget:
        return ConvertPage(ctx)
