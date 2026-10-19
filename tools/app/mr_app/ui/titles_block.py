"""The two real titles of a competitor's video (day 12, section 7; the reference's ``.ttl2``), a small reusable block
for the Download / New video rows, the Match cuts queue rows and the board cards:

    TITLE FROM THE POST     The moment he realised ...                    [Copy]
    HEADLINE ON THE VIDEO   The fact that prison changed / him ... 🔒😭    [Copy] [Use as my headline] [Use as YouTube title]

``set(post, headline)``: None = still being read ("reading ..."), "" = nothing readable (said plainly). Signals:
``use_headline(text)`` (fills the two-line headline box, line break kept), ``use_title(text)`` (the YouTube title, one
line), ``copied(text)``. ``from_project(data)`` reads both from a project.json dict."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QSizePolicy, QWidget

from . import motion as M
from . import theme
from . import widgets as W

READING = "reading ..."
NOTHING_POST = "nothing readable in the post"
NOTHING_VIDEO = "no headline readable on the video"


def _key(text: str) -> QLabel:
    k = QLabel(text.upper())
    k.setFont(W.mono(9.5, 600, 1.0))
    k.setStyleSheet(f"color: {M.alpha(theme.CORAL, 0.9).name(QColor.NameFormat.HexArgb)};")
    k.setFixedWidth(150)
    return k


class TitlesBlock(QWidget):
    use_headline = Signal(str)
    use_title = Signal(str)
    copied = Signal(str)

    def __init__(self, post: str | None = None, headline: str | None = None, uses: bool = True):
        super().__init__()
        self.setObjectName("Transparent")
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        self.post_text = W.label("", "Muted")
        self.head_text = W.label("", "Muted")
        for t in (self.post_text, self.head_text):
            t.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            t.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_post = W.Button("Copy", "Copy")
        self.copy_head = W.Button("Copy", "Copy")
        self.b_use = W.Button("Use as my headline", "Copy")
        self.b_title = W.Button("Use as YouTube title", "Copy")
        g.addWidget(_key("Title from the post"), 0, 0)
        g.addWidget(self.post_text, 0, 1)
        g.addWidget(self.copy_post, 0, 2)
        g.addWidget(_key("Headline on the video"), 1, 0)
        g.addWidget(self.head_text, 1, 1)
        g.addWidget(self.copy_head, 1, 2)
        g.addWidget(self.b_use, 1, 3)
        g.addWidget(self.b_title, 1, 4)
        g.setColumnStretch(1, 1)
        self.b_use.setVisible(uses)
        self.b_title.setVisible(uses)
        self.copy_post.clicked.connect(lambda: self._copy(self._post))
        self.copy_head.clicked.connect(lambda: self._copy(" ".join(self._head.split())))
        self.b_use.clicked.connect(lambda: self.use_headline.emit(self._head))
        self.b_title.clicked.connect(lambda: self.use_title.emit(" ".join(self._head.split())))
        self._post, self._head = "", ""
        self.set(post, headline)

    def set(self, post: str | None, headline: str | None) -> None:
        self._post, self._head = post or "", headline or ""
        self._show(self.post_text, post, NOTHING_POST)
        self._show(self.head_text, headline, NOTHING_VIDEO)
        self.copy_post.setEnabled(bool(self._post))
        for b in (self.copy_head, self.b_use, self.b_title):
            b.setEnabled(bool(self._head))

    @staticmethod
    def _show(lab: QLabel, text: str | None, nothing: str) -> None:
        if text is None:
            lab.setText(READING)
            lab.setStyleSheet(f"color: {theme.DIM};")
        elif not text.strip():
            lab.setText(nothing)
            lab.setStyleSheet(f"color: {theme.DIM};")
        else:
            lab.setText(" / ".join(x.strip() for x in text.splitlines() if x.strip()))
            lab.setStyleSheet(f"color: {theme.MUTED};")
        lab.setToolTip(text or "")

    def _copy(self, text: str) -> None:
        if text:
            QApplication.clipboard().setText(text)
            self.copied.emit(text)

    def from_project(self, data: dict) -> None:
        """Both titles from a project.json dict (None while they are not read yet)."""
        self.set(data.get("post_title"), data.get("video_headline"))

    def values(self) -> tuple[str, str]:
        return self._post, self._head
