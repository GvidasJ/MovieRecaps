"""The two real titles of a competitor's video (day 12, section 7; the reference's ``.ttl2``), a small reusable block
for the Download / New video rows, the Match cuts queue rows and the board cards:

    TITLE FROM THE POST     The moment he realised ...                    [Copy]
    HEADLINE ON THE VIDEO   The fact that prison changed / him ... 🔒😭    [Copy] [Use as my headline] [Use as YouTube title]

``compact=True`` (the board cards, ~140 px wide; the reference's narrow ``.ttl2``): each key on its own line with its
Copy button, the text under it (wrapped); the headline's two "Use as ..." are in a small "Use…" menu.

``set(post, headline)``: None = still being read ("reading ..."), "" = nothing readable (said plainly). Signals:
``use_headline(text)`` (fills the two-line headline box, line break kept), ``use_title(text)`` (the YouTube title, one
line), ``copied(text)``. ``from_project(data)`` reads both from a project.json dict; ``has_titles(data)`` says whether
a project has anything to show (a project without a competitor -- Just cuts -- has none)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import motion as M
from . import theme
from . import widgets as W

READING = "reading ..."
NOTHING_POST = "nothing readable in the post"
NOTHING_VIDEO = "no headline readable on the video"


def _key(text: str, width: int | None = 150) -> QLabel:
    k = QLabel(text.upper())
    k.setFont(W.mono(9.5, 600, 1.0))
    k.setStyleSheet(f"color: {M.alpha(theme.CORAL, 0.9).name(QColor.NameFormat.HexArgb)};")
    if width:
        k.setFixedWidth(width)
    return k


def has_titles(data: dict | None) -> bool:
    """Whether a project.json dict has the competitor's titles to show: day 12's titles step wrote them (a text, or
    "" = nothing readable). Projects made before day 12 and Just cuts (no competitor) have none -- the block is not
    shown there (it would say "reading ..." for ever)."""
    data = data or {}
    return "post_title" in data or "video_headline" in data


def is_just_cuts(data: dict | None) -> bool:
    """project.json says Just cuts (``kind`` or the edit step's ``kind``)."""
    data = data or {}
    if data.get("kind") in ("just_cuts", "full"):       # the project's kind now (Add a competitor makes it "full";
        return data["kind"] == "just_cuts"              # its edit step still says "just_cuts" from the cuts run)
    edit = (data.get("steps") or {}).get("edit") or {}
    return isinstance(edit, dict) and edit.get("kind") == "just_cuts"


class TitlesBlock(QWidget):
    use_headline = Signal(str)
    use_title = Signal(str)
    copied = Signal(str)

    def __init__(self, post: str | None = None, headline: str | None = None, uses: bool = True,
                 compact: bool = False):
        super().__init__()
        self.setObjectName("Transparent")
        self.compact = bool(compact)
        self.post_text = W.label("", "Muted")
        self.head_text = W.label("", "Muted")
        for t in (self.post_text, self.head_text):
            t.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_post = W.Button("Copy", "Copy")
        self.copy_head = W.Button("Copy", "Copy")
        self.b_use = W.Button("Use as my headline", "Copy")
        self.b_title = W.Button("Use as YouTube title", "Copy")
        self.b_more = W.Button("Use…", "Copy")             # compact only: the two "Use as ..." in a small menu
        self.b_more.setToolTip("Use as my headline / Use as YouTube title")
        if self.compact:
            self._build_compact()
        else:
            self._build_wide()
        self.b_use.setVisible(uses and not self.compact)
        self.b_title.setVisible(uses and not self.compact)
        self.b_more.setVisible(uses and self.compact)
        self.copy_post.clicked.connect(lambda: self._copy(self._post))
        self.copy_head.clicked.connect(lambda: self._copy(" ".join(self._head.split())))
        self.b_use.clicked.connect(self.use_as_headline)
        self.b_title.clicked.connect(self.use_as_title)
        self.b_more.clicked.connect(self._menu)
        self._post, self._head = "", ""
        self.set(post, headline)

    def _build_wide(self) -> None:
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        for t in (self.post_text, self.head_text):
            t.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        g.addWidget(_key("Title from the post"), 0, 0)
        g.addWidget(self.post_text, 0, 1)
        g.addWidget(self.copy_post, 0, 2)
        g.addWidget(_key("Headline on the video"), 1, 0)
        g.addWidget(self.head_text, 1, 1)
        g.addWidget(self.copy_head, 1, 2)
        g.addWidget(self.b_use, 1, 3)
        g.addWidget(self.b_title, 1, 4)
        g.setColumnStretch(1, 1)

    def _build_compact(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(3)
        for n, (key, text, btns) in enumerate((("Title from the post", self.post_text, [self.copy_post]),
                                               ("Headline on the video", self.head_text,
                                                [self.copy_head, self.b_more]))):
            if n:
                v.addSpacing(4)
            k = _key(key, None)                            # the key on its own line (a card is ~120 px wide) ...
            k.setFont(W.mono(8.5, 600, 0.5))
            k.setWordWrap(True)
            v.addWidget(k)
            text.setWordWrap(True)                         # ... the text under it ...
            text.setFont(W.font(11))
            v.addWidget(text)
            row = QHBoxLayout()                            # ... and its buttons under that
            row.setSpacing(4)
            for b in btns:
                row.addWidget(b)
            row.addStretch(1)
            v.addLayout(row)

    def set(self, post: str | None, headline: str | None) -> None:
        self._post, self._head = post or "", headline or ""
        self._show(self.post_text, post, NOTHING_POST)
        self._show(self.head_text, headline, NOTHING_VIDEO)
        self.copy_post.setEnabled(bool(self._post))
        for b in (self.copy_head, self.b_use, self.b_title, self.b_more):
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

    def use_as_headline(self) -> None:
        if self._head:
            self.use_headline.emit(self._head)

    def use_as_title(self) -> None:
        if self._head:
            self.use_title.emit(" ".join(self._head.split()))

    def menu_items(self) -> list:
        """The compact block's "Use…" menu (also offered in a board card's right-click menu)."""
        return [("Use as my headline", self.use_as_headline, False, bool(self._head)),
                ("Use as YouTube title", self.use_as_title, False, bool(self._head))]

    def _menu(self) -> None:
        from .menu import FadeMenu
        FadeMenu(self, self.menu_items()).popup(self.b_more.mapToGlobal(self.b_more.rect().bottomLeft()))

    def from_project(self, data: dict) -> None:
        """Both titles from a project.json dict (None while they are not read yet)."""
        self.set(data.get("post_title"), data.get("video_headline"))

    def values(self) -> tuple[str, str]:
        return self._post, self._head
