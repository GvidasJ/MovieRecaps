"""The channel template editor (night 10): pick the channel, type the title (emojis allowed; Enter = your own line
break), see the template live, and the competitor's headline next to your title with the title check (a warning at
80 % the same words, in any order). Used by the Template tab and, in a dialog, by a video's row (Match cuts,
Download)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import theme
from . import widgets as W

PREVIEW_W = 300                    # the preview's width on screen (px; 1080x1920 scaled)
NONE = ""                          # "no channel": the default template, no watermark


def channel_names() -> list[str]:
    try:
        from ..core import channels as Ch
        return [c.name for c in Ch.channels()]
    except Exception:  # noqa: BLE001 - no channels folder: only the default
        return []


def to_pixmap(im) -> QPixmap:
    """A PIL image -> QPixmap (RGBA)."""
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    q = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(q.copy())


class _Render(QThread):
    """One preview render in the background: ``done(pixmap or None, info dict)``."""
    done = Signal(object, dict)

    def __init__(self, channel: str, title: str, width: int):
        super().__init__()
        self.channel, self.title, self.width = channel, title, width

    def run(self) -> None:
        info: dict = {}
        try:
            from ..core import channels as Ch
            from ..core import flow
            ch = flow.find_channel(self.channel)
            if ch is None:
                self.done.emit(None, {"error": f"no channel {self.channel!r}"})
                return
            im = Ch.preview(ch, self.title, width=self.width)
            info = layout_info(self.title, ch)
            self.done.emit(im, info)
        except Exception as e:  # noqa: BLE001 - shown in the editor
            self.done.emit(None, {"error": f"{type(e).__name__}: {e}"})


def layout_info(title: str, ch=None) -> dict:
    """{'lines', 'shrunk', 'why'} of a title (headline.layout; in ``ch``'s headline style when given) for the
    editor's line under the title."""
    try:
        from ..core import headline as H
        if ch is not None:
            from ..core import channels as Ch
            lay = Ch.headline_layout(ch, title)
        else:
            lay = H.layout(title)
        return {"lines": [str(x) for x in getattr(lay, "lines", [])], "shrunk": bool(getattr(lay, "shrunk", False)),
                "why": str(getattr(lay, "why", "") or ""), "scale": float(getattr(lay, "scale", 1.0) or 1.0),
                "summary": lay.summary() if hasattr(lay, "summary") else "",
                "widths": list(getattr(lay, "widths_1080", []) or []),
                "warnings": list(getattr(lay, "warnings", []) or [])}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


class TemplateEditor(QWidget):
    """Channel + title + live preview + the competitor's headline and the title check. ``changed()`` after every
    edit; ``channel()`` / ``title()`` read them."""
    changed = Signal()
    headline_found = Signal(str)

    def __init__(self, channel: str = "", title: str = "", headline: str | None = None, competitor: Path | None = None,
                 drop_competitor: bool = False):
        super().__init__()
        self.setObjectName("Transparent")
        self._headline = headline
        self._sure = True
        self._competitor = competitor
        self._render: _Render | None = None
        self._dirty = False
        self._reader = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(W.label("Channel", "Small"))
        names = channel_names()
        opts = [(NONE, "Default")] + [(n, n) for n in names]
        self.ch = W.Segmented(opts, channel if channel in names else NONE)
        self.ch.setToolTip("Your channel's template (its picture, name and @handle) with your headline, and its "
                           "watermark on top. Default: templates\\default.png as before, no watermark.")
        self.ch.changed.connect(self._edited)
        chr_ = QHBoxLayout()
        chr_.addWidget(self.ch)
        chr_.addStretch(1)
        left.addLayout(chr_)
        left.addSpacing(4)
        left.addWidget(W.label("Title  (emojis allowed -- Win + . opens the emoji panel; Enter = your own line break)",
                               "Small"))
        self.t = QPlainTextEdit()
        self.t.setPlaceholderText("The fact that he didn't even think twice is hilarious 😭")
        self.t.setPlainText(title or "")
        self.t.setFixedHeight(84)
        self.t.setFont(W.font(15, 600))
        self.t.textChanged.connect(self._edited)
        left.addWidget(self.t)
        self.info = W.label("", "Muted", wrap=True, select=True)
        left.addWidget(self.info)
        left.addSpacing(6)
        left.addWidget(W.label("The competitor's headline", "Small"))
        self.comp = W.label("", "Muted", wrap=True, select=True)
        left.addWidget(self.comp)
        self.check = W.StateLabel()
        left.addWidget(self.check)
        if drop_competitor:
            dz = W.DropZone("Drop the competitor's video", ["its headline is read (a few seconds) and your title is "
                                                            "checked against it"], [("Choose...", self._pick)],
                            big=False)
            dz.dropped.connect(lambda ps: self.read_competitor(Path(ps[0])))
            left.addWidget(dz)
        left.addStretch(1)
        lay.addLayout(left, 1)
        self.view = QLabel()
        self.view.setFixedSize(PREVIEW_W, int(PREVIEW_W * 16 / 9))
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setStyleSheet(f"background: #2a2a2a; border: 1px solid {theme.LINE_HI};")
        self.view.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.view, 0, Qt.AlignmentFlag.AlignTop)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(140)
        self.timer.timeout.connect(self._refresh)
        self._show_headline()
        self._refresh()
        if self._headline is None and competitor is not None:
            self.read_competitor(competitor)

    # -- values --
    def channel(self) -> str:
        return self.ch.value

    def title(self) -> str:
        return self.t.toPlainText().strip()

    def headline(self) -> str | None:
        return self._headline

    # -- the competitor's headline --
    def _pick(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from ..core import jobs as J
        f, _ = QFileDialog.getOpenFileName(self, "The competitor's video", "",
                                           "Videos (" + " ".join(f"*{e}" for e in sorted(J.VIDEO_EXTS)) + ")")
        if f:
            self.read_competitor(Path(f))

    def read_competitor(self, video: Path) -> None:
        """Read the competitor's headline in the background (a few seconds)."""
        from ..tools.match_cuts_tool import Worker
        self._competitor = video
        self.comp.setText(f"reading the headline of {video.name} ...")

        def work():
            from ..core import title_check as T
            rd = T.read_headline(video)
            return (rd.text if rd is not None else "", "" if rd is not None else T.last_reason(),
                    bool(getattr(rd, "sure", True)) if rd is not None else True)
        self._reader = Worker(work)
        self._reader.finished_with.connect(self._got_headline)
        self._reader.failed.connect(lambda e: self.comp.setText(f"could not read it: {e}"))
        self._reader.start()

    def _got_headline(self, r) -> None:
        text, why, sure = r
        self._headline = text
        self._sure = sure
        self.headline_found.emit(text)
        self._show_headline(why)

    def set_headline(self, text: str | None) -> None:
        self._headline = text
        self._show_headline()

    def _show_headline(self, why: str = "") -> None:
        from ..core import title_check as T
        h = self._headline
        if h is None:
            self.comp.setText("not read yet" + ("" if self._competitor else " -- drop the competitor's video"))
            self.check.set("ready", "", theme.DIM)
            return
        if not h:
            self.comp.setText("none found" + (f" ({why})" if why else ""))
            self.check.set("ready", "nothing to compare with", theme.DIM)
            return
        self.comp.setText(f"“{h}”" + ("" if getattr(self, "_sure", True) else
                                       "   (read from the picture itself -- check it)"))
        c = T.check(self.title(), h)
        if not self.title():
            self.check.set("ready", c.message, theme.DIM)
        elif c.warn:
            self.check.set("TOO CLOSE", c.message, theme.RED)
        else:
            self.check.set("OK", c.message, theme.GREEN)

    # -- the preview --
    def _edited(self, *_a) -> None:
        self._show_headline()
        self.timer.start()
        self.changed.emit()

    def _refresh(self) -> None:
        if not self.channel():
            self.view.setPixmap(QPixmap())
            self.view.setText("Default template\n(templates\\default.png:\nits own headline)")
            self.info.setText("No channel: the edit uses templates\\default.png as before (its own headline), no "
                              "watermark.")
            return
        if self._render is not None and self._render.isRunning():
            self._dirty = True
            return
        self._render = _Render(self.channel(), self.title(), PREVIEW_W * 2)
        self._render.done.connect(self._rendered)
        self._render.start()

    def _rendered(self, im, info: dict) -> None:
        if im is not None:
            pm = to_pixmap(im).scaled(self.view.width(), self.view.height(), Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
            self.view.setText("")
            self.view.setPixmap(pm)
        if info.get("error"):
            self.info.setText(f"the preview failed: {info['error']}")
        elif not self.title():
            self.info.setText("Type the title: it is written in the template's style (122 pt, 2 lines).")
        else:
            lines = info.get("lines") or []
            shown = "  /  ".join(lines)
            widths = " and ".join(f"{w:.0f}" for w in info.get("widths") or [])
            warn = ("  ·  " + "; ".join(info["warnings"])) if info.get("warnings") else ""
            if info.get("shrunk"):
                self.info.setText(f"SHRUNK -- {info.get('why') or info.get('summary') or ''}:  {shown}{warn}")
                self.info.setStyleSheet(f"color: {theme.AMBER};")
            else:
                self.info.setText(f"{info.get('summary') or 'full size'} -- {widths} px wide of the 920 allowed:  "
                                  f"{shown}{warn}")
                self.info.setStyleSheet(f"color: {theme.AMBER};" if warn else "")
        if self._dirty:
            self._dirty = False
            self._refresh()


class TemplateDialog(QDialog):
    """The editor in its own window, for one video (OK keeps the channel and the title)."""

    def __init__(self, parent=None, channel: str = "", title: str = "", headline: str | None = None,
                 competitor: Path | None = None, name: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"{name} -- template" if name else "Template")
        self.resize(980, 660)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        self.ed = TemplateEditor(channel, title, headline, competitor, drop_competitor=competitor is None)
        lay.addWidget(self.ed, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = W.Button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = W.Button("Use this template", "Primary")
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def showEvent(self, e) -> None:                       # noqa: N802
        W.dark_title_bar(self)
        super().showEvent(e)
