"""GlideList (day 12): a vertical list of widgets that GLIDE to their new places -- never jump -- when their order
changes, a widget joins or leaves, or one of them grows or shrinks ("newest on top everywhere": a row that changes
state glides to its new place).

The widgets are placed by hand (no QLayout): each one is as wide as the list and as tall as it wants
(``heightForWidth``, else its ``sizeHint``), one under the other with ``spacing`` px between; the list is exactly as tall
as all of them. A move is the app's own animation (motion: 0.45 s on the soft curve; Settings' "Animations off" makes
it instant), retargeted smoothly when the order changes again mid-glide.

API (used by the Match cuts queue; the Download / New video rows can use it the same way)::

    lst = GlideList(spacing=10)
    lst.insert(w, 0)            # a new widget at a place (0 = the top); the others glide to make room
    lst.remove(w)               # out of the list (not deleted: setParent(None) / deleteLater() is yours); the rest glide
    lst.set_order([w3, w1, w2]) # the same widgets in a new order: each glides to its new place
    lst.items()                 # the widgets in the order shown
    lst.target_y(w)             # where a widget is going (its y when the glide ends)
    lst.gliding()               # True while something moves
    lst.relayout(animate=True)  # after a widget changed its size by itself (done for you on its LayoutRequest)
    lst.inserted                # Signal(QWidget): a widget joined (e.g. scroll the page to the top: scroll_to_top())

``scroll_to_top(widget)`` glides the scroll area a widget sits in back to its top (the queue does it when a row
appears)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QSize, QTimer, QVariantAnimation, Signal
from PySide6.QtWidgets import QScrollArea, QWidget

from . import motion as Mo


class GlideList(QWidget):
    inserted = Signal(QWidget)

    def __init__(self, parent: QWidget | None = None, spacing: int = 10, ms: int = Mo.GLIDE_MS,
                 curve: QEasingCurve = Mo.SOFT):
        super().__init__(parent)
        self.spacing = int(spacing)
        self.ms = int(ms)
        self.curve = curve
        self._items: list[QWidget] = []
        self._anims: dict[int, QVariantAnimation] = {}
        self._targets: dict[int, int] = {}
        self._placed: set[int] = set()
        self._height = 0
        self._pending = False
        self._width = -1

    # -- the list --
    def items(self) -> list[QWidget]:
        return list(self._items)

    def count(self) -> int:
        return len(self._items)

    def insert(self, w: QWidget, index: int = 0) -> None:
        """``w`` joins at ``index`` (0 = the top; past the end = the bottom): it appears in its place, the widgets
        under it glide down."""
        if w in self._items:
            self._items.remove(w)
        index = max(0, min(int(index), len(self._items)))
        self._items.insert(index, w)
        w.setParent(self)
        w.installEventFilter(self)
        w.show()
        self.relayout()
        self.inserted.emit(w)

    def append(self, w: QWidget) -> None:
        self.insert(w, len(self._items))

    def remove(self, w: QWidget) -> None:
        """``w`` leaves the list (it stays a child until you delete it); the widgets under it glide up."""
        if w not in self._items:
            return
        self._items.remove(w)
        w.removeEventFilter(self)
        a = self._anims.pop(id(w), None)
        if a is not None:
            a.stop()
            a.deleteLater()
        self._targets.pop(id(w), None)
        self._placed.discard(id(w))
        self.relayout()

    def set_order(self, widgets) -> None:
        """The list's widgets in a new order (widgets not in the list are ignored; widgets left out keep their
        relative order at the end): each one glides to its new place."""
        want = [w for w in widgets if w in self._items]
        rest = [w for w in self._items if w not in want]
        new = want + rest
        if new == self._items:
            return
        self._items = new
        self.relayout()

    def target_y(self, w: QWidget) -> int:
        return self._targets.get(id(w), w.y())

    def gliding(self) -> bool:
        return any(a.state() == QVariantAnimation.State.Running for a in self._anims.values())

    # -- placing --
    def _height_of(self, w: QWidget, width: int) -> int:
        if w.hasHeightForWidth():
            h = w.heightForWidth(width)
            if h > 0:
                return h
        return max(1, w.sizeHint().height())

    def relayout(self, animate: bool = True) -> None:
        """Every widget to its place: new ones (and everything when ``animate`` is False or animations are off) at
        once, the others gliding there."""
        self._pending = False
        width = max(1, self.width())
        y = 0
        for w in self._items:
            h = self._height_of(w, width)
            if w.width() != width or w.height() != h:
                w.resize(width, h)
            self._targets[id(w)] = y
            self._place(w, y, animate)
            y += h + self.spacing
        total = max(0, y - self.spacing) if self._items else 0
        for w in self._items:                             # a row still gliding up (the one above it shrank) is not
            a = self._anims.get(id(w))                    # cut off at the list's new end: the list shrinks after it
            if a is not None and a.state() == QVariantAnimation.State.Running:
                total = max(total, w.y() + w.height())
        if total != self._height:
            self._height = total
            self.setFixedHeight(total)
            self.updateGeometry()
        self.update()

    def _place(self, w: QWidget, y: int, animate: bool) -> None:
        key = id(w)
        a = self._anims.get(key)
        first = key not in self._placed
        self._placed.add(key)
        if first or not animate or not Mo.enabled() or not self.isVisible():
            if a is not None:
                a.stop()
            if w.y() != y or w.x() != 0:
                w.move(0, y)
            return
        if a is not None and a.state() == QVariantAnimation.State.Running and int(a.endValue()) == y:
            return                                        # already going there
        if w.y() == y and (a is None or a.state() != QVariantAnimation.State.Running):
            return
        if a is None:
            a = QVariantAnimation(self)
            a.setEasingCurve(self.curve)
            a.valueChanged.connect(lambda v, ww=w: self._step(ww, v))
            a.finished.connect(self._later)               # then the list's height to its end (see relayout)
            self._anims[key] = a
        a.stop()
        a.setStartValue(float(w.y()))
        a.setEndValue(float(y))
        a.setDuration(self.ms)
        a.start()

    def _step(self, w: QWidget, v) -> None:
        try:
            w.move(0, int(round(float(v))))
        except RuntimeError:                              # the widget was deleted mid-glide
            return
        self.update()

    def _later(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self.relayout)

    # -- Qt --
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.LayoutRequest and obj in self._items:
            self._later()
        return False

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.LayoutRequest:
            self._later()
        return super().event(e)

    def resizeEvent(self, e) -> None:                     # noqa: N802
        super().resizeEvent(e)
        if self.width() != self._width:                   # a new width: new heights, at once
            self._width = self.width()
            self.relayout(animate=False)

    def showEvent(self, e) -> None:                       # noqa: N802
        super().showEvent(e)
        self.relayout(animate=False)

    def sizeHint(self) -> QSize:                          # noqa: N802
        return QSize(super().sizeHint().width(), self._height)

    def minimumSizeHint(self) -> QSize:                   # noqa: N802
        return QSize(0, self._height)


def scroll_area_of(w: QWidget | None) -> QScrollArea | None:
    while w is not None:
        if isinstance(w, QScrollArea):
            return w
        w = w.parentWidget()
    return None


def scroll_to_top(w: QWidget, ms: int = Mo.GLIDE_MS) -> None:
    """The scroll area ``w`` is in glides back to its top (at once with animations off)."""
    sa = scroll_area_of(w)
    if sa is None:
        return
    bar = sa.verticalScrollBar()
    if bar.value() == bar.minimum():
        return
    if not Mo.enabled():
        bar.setValue(bar.minimum())
        return
    a = getattr(sa, "_mr_scroll_anim", None)
    if a is None:
        a = QVariantAnimation(sa)
        a.setEasingCurve(Mo.SOFT)
        a.valueChanged.connect(lambda v: bar.setValue(int(round(float(v)))))
        sa._mr_scroll_anim = a                            # noqa: SLF001
    a.stop()
    a.setStartValue(float(bar.value()))
    a.setEndValue(float(bar.minimum()))
    a.setDuration(ms)
    a.start()
