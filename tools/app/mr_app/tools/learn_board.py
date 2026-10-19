"""The Learn tab's three columns (day 12, the reference's ``.lgrid`` / ``.lcol`` / ``.lc``): "Waiting for your fix",
"Problems to fix tonight" and "Suggestions from last night" -- each a small mono heading with a red count, then its
cards, newest first.

* A card (``LCard``) is a flat panel: its border fades a little lighter under the mouse (0.3 s in, 0.48 s out) --
  nothing moves or grows on hover. An amber card (a rendered project waiting for its fix) has an amber border.
* A new card opens (its height and its opacity from 0, so the cards below glide down) and a card that goes closes the
  same way -- no jumps. A card whose key stays is updated in place.
* A card can take dropped files (``on_drop``): the waiting cards take your fixed .prproj -- no glow, only the drop.
* The columns sit side by side when there is room (3 x 260 px), else one under the other."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import (QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)

from ..ui import motion as M
from ..ui import theme
from ..ui import widgets as W

MIN_COL_W = 260
GAP = 12
OPEN_MS, CLOSE_MS = 300, 400
NO_MAX = 16777215


class LCard(QWidget):
    """One card: a bold title, an optional channel chip, small dim lines, small buttons ``[(text, fn)]``."""

    def __init__(self, key: str, title: str, lines: Sequence[str] = (), buttons: Sequence[tuple[str, Callable]] = (),
                 channel: str = "", warn: bool = False, on_drop: Callable[[list], None] | None = None):
        super().__init__()
        self.key = key
        self.warn = warn
        self.on_drop = on_drop
        self.closing = False
        self.setObjectName("LCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.hover = M.Fader(self, self.update)
        self.reveal = M.Fader(self, self._revealed, OPEN_MS, CLOSE_MS)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._then: Callable[[], None] | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)
        self.title = W.label("", wrap=True, select=True)
        self.title.setFont(W.font(13, 600))
        self.title.setStyleSheet(f"color: {theme.TEXT};")
        lay.addWidget(self.title)
        self.chan_box = QHBoxLayout()
        self.chan_box.setContentsMargins(0, 0, 0, 0)
        self.chan_box.addStretch(1)
        lay.addLayout(self.chan_box)
        self.chan: QWidget | None = None
        self.small = W.label("", wrap=True, select=True)
        self.small.setFont(W.font(11.5))
        lay.addWidget(self.small)
        self.btns = QHBoxLayout()
        self.btns.setContentsMargins(0, 2, 0, 0)
        self.btns.setSpacing(6)
        lay.addLayout(self.btns)
        self.buttons: dict[str, W.Button] = {}
        if on_drop is not None:
            self.setAcceptDrops(True)
        self.fill(title, lines, buttons, channel, warn)

    # -- content --
    def fill(self, title: str, lines: Sequence[str] = (), buttons: Sequence[tuple[str, Callable]] = (),
             channel: str = "", warn: bool = False) -> None:
        """New content for the same card (no animation: only what changed is redrawn)."""
        self.warn = warn
        self.title.setText(title)
        if self.chan is not None:
            self.chan.setParent(None)
            self.chan.deleteLater()
            self.chan = None
        if channel:
            self.chan = W.channel_chip(channel, 11.5, theme.MUTED)
            self.chan.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.chan_box.insertWidget(0, self.chan, 0, Qt.AlignmentFlag.AlignLeft)
        self.small.setText("\n".join(x for x in lines if x))
        self.small.setStyleSheet(f"color: {theme.AMBER if warn else theme.DIM};")
        while self.btns.count():
            it = self.btns.takeAt(0)
            if it.widget() is not None:
                it.widget().setParent(None)
                it.widget().deleteLater()
        self.buttons = {}
        for text, fn in buttons:
            b = W.Button(text, "Mini")
            b.clicked.connect(lambda _c=False, f=fn: f())
            self.btns.addWidget(b)
            self.buttons[text] = b
        self.btns.addStretch(1)
        self.update()

    # -- opening and closing (the cards below glide) --
    def _natural(self) -> int:
        lay = self.layout()
        w = max(self.width(), 200)
        if lay.hasHeightForWidth():
            return lay.totalHeightForWidth(w)
        return lay.totalSizeHint().height()

    def _revealed(self) -> None:
        v = self.reveal.value
        self._fx.setOpacity(v)
        if v >= 1.0 and not self.closing:
            self.setMaximumHeight(NO_MAX)
        else:
            self.setMaximumHeight(max(0, int(round(self._natural() * v))))
        if self.closing and v <= 0.0 and self.reveal.target <= 0.0:
            then, self._then = self._then, None
            if then is not None:
                then()

    def open(self) -> None:
        self.setMaximumHeight(0)
        self.reveal.to(1.0)

    def close_card(self, then: Callable[[], None] | None = None) -> None:
        """Closes (fades and folds), then ``then()``."""
        if self.closing:
            return
        self.closing = True
        self._then = then
        self.setMaximumHeight(self._natural() if self.reveal.value >= 1.0 else self.maximumHeight())
        for b in self.buttons.values():
            b.setEnabled(False)
        self.reveal.to(0.0)

    def dim(self, text: str = "") -> None:
        """A wish taken: the card stays faded (the reference's opacity .4) until the next look takes it away."""
        self._fx.setOpacity(0.4)
        for b in self.buttons.values():
            b.setEnabled(False)
        if text:
            self.small.setText(text)

    # -- hover: the border only fades --
    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        h = self.hover.value
        line = M.mix(theme.AMBER_LINE, theme.AMBER, 0.35 * h) if self.warn else M.mix(theme.LINE, theme.LINE2, h)
        W.box(p, QRectF(self.rect()), theme.PANEL, line)
        p.end()

    # -- drops --
    def dragEnterEvent(self, e) -> None:                  # noqa: N802
        if self.on_drop is not None and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:                       # noqa: N802
        if self.on_drop is None:
            return
        got = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if got:
            e.acceptProposedAction()
            self.on_drop(got)


class LCol(QWidget):
    """A column: 'WAITING FOR YOUR FIX [2]', then its cards (newest first), or a dim line when there are none."""

    def __init__(self, title: str, empty: str):
        super().__init__()
        self.setObjectName("Transparent")
        # (the width follows the page: the columns never hold the window wider -- narrow, they stack instead)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.head = QLabel(title.upper())
        self.head.setFont(W.mono(10.5, 600, 12))
        self.head.setStyleSheet(f"color: {theme.DIM};")
        head.addWidget(self.head)
        self.badge = QLabel("")
        self.badge.setFont(W.mono(10, 700))
        self.badge.setStyleSheet(f"background: {theme.RED}; color: #ffffff; padding: 1px 6px;")
        self.badge.hide()
        head.addWidget(self.badge)
        head.addStretch(1)
        lay.addLayout(head)
        self.empty = W.label(empty, "", wrap=True)
        self.empty.setFont(W.font(12))
        self.empty.setStyleSheet(f"color: {theme.DIM};")
        lay.addWidget(self.empty)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body)
        lay.addStretch(1)
        self.cards: dict[str, LCard] = {}

    def count(self) -> int:
        return len([c for c in self.cards.values() if not c.closing])

    def set_count(self, n: int) -> None:
        self.badge.setText(str(n))
        self.badge.setVisible(n > 0)

    def show_cards(self, specs: Sequence[dict]) -> None:
        """The column's cards, in this order: ``specs`` = [{key, title, lines, buttons, channel, warn, on_drop}].
        A card whose key is there already is filled again; a new one opens; one not in ``specs`` closes."""
        want = [s["key"] for s in specs]
        for k in [k for k in self.cards if k not in want]:
            self.remove(k)
        for i, s in enumerate(specs):
            c = self.cards.get(s["key"])
            args = dict(title=s.get("title", ""), lines=s.get("lines", ()), buttons=s.get("buttons", ()),
                        channel=s.get("channel", ""), warn=bool(s.get("warn")))
            if c is not None and not c.closing:
                c.fill(**args)
                c.on_drop = s.get("on_drop")
                c.setAcceptDrops(c.on_drop is not None)
            else:
                c = LCard(s["key"], on_drop=s.get("on_drop"), **args)
                self.cards[s["key"]] = c
                c.open()
            self._place(c, i)
        self.set_count(len(specs))
        self.empty.setVisible(not specs)

    def _place(self, c: LCard, index: int) -> None:
        """Put ``c`` at ``index`` among the open cards (the closing ones keep their place while they fold)."""
        live = [self.body.itemAt(i).widget() for i in range(self.body.count())]
        live = [w for w in live if w is not None and not getattr(w, "closing", False) and w is not c]
        target = live[index] if index < len(live) else None
        if c.parent() is self and self.body.indexOf(c) >= 0:
            self.body.removeWidget(c)
        pos = self.body.indexOf(target) if target is not None else self.body.count()
        self.body.insertWidget(pos, c)
        c.show()

    def remove(self, key: str) -> None:
        c = self.cards.pop(key, None)
        if c is None:
            return

        def gone() -> None:
            self.body.removeWidget(c)
            c.setParent(None)
            c.deleteLater()
            self._show_empty()                            # the last card has folded away: "Nothing waiting ..."
        c.close_card(gone)
        self.set_count(self.count())
        self._show_empty()

    def _show_empty(self) -> None:
        self.empty.setVisible(self.count() == 0 and not any(
            getattr(self.body.itemAt(i).widget(), "closing", False) for i in range(self.body.count())))


class LearnColumns(QWidget):
    """The three columns side by side (or one under the other when the page is narrow)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Transparent")
        self.waiting = LCol("Waiting for your fix", "Nothing waiting: a finished edit shows up here by itself.")
        self.problems = LCol("Problems to fix tonight", "No problems: every run was fine (or skipped).")
        self.suggestions = LCol("Suggestions from last night", "No suggestions yet: the next night run writes some.")
        self.cols = [self.waiting, self.problems, self.suggestions]
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(GAP)
        self.grid.setVerticalSpacing(GAP + 6)
        self._wide: bool | None = None
        self._arrange(True)

    def _arrange(self, wide: bool) -> None:
        if wide == self._wide:
            return
        self._wide = wide
        for c in self.cols:
            self.grid.removeWidget(c)
        for i, c in enumerate(self.cols):
            if wide:
                self.grid.addWidget(c, 0, i)
            else:
                self.grid.addWidget(c, i, 0)
        for i in range(3):
            self.grid.setColumnStretch(i, 1 if (wide or i == 0) else 0)

    def resizeEvent(self, e) -> None:                     # noqa: N802
        self._arrange(e.size().width() >= 3 * MIN_COL_W + 2 * GAP)
        super().resizeEvent(e)
