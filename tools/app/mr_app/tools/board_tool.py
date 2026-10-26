"""The Board tab (night 11): every video project from A to Z -- Link → Downloaded → Converted → Template → Edit made →
Fixed → Rendered → Uploaded -- for the PC and the laptop at once (the shared folder, core.board).

* Cards move by themselves as the steps finish; drag one to any column (a slot opens where it will land); ★ keeps
  it at the top of its column. Each card shows the channel's dot, the name, the title (Copy) and the date and time of
  every step ("Downloaded 18 Oct 02:44").
* Above the board, a small chart per channel: uploads per day this week (Mon-Sun), "N this week".
* Clean up removes finished junk (temp files, leftover copies) -- it asks first and shows the size; it never touches
  an edit, template.png, project.json or title.txt. Day 12, one RAW file per run: an older run that holds a 5.1 RAW
  twice (raw_ae.mp4 / raw.mp4 next to raw_stereo.mp4, which its 1_edit.xml plays) is checked in the background
  (core.rawfiles: raw_stereo opens as the same video, nothing plays the old copy -- your projects are only read) and
  the old copy is offered with the space it frees; it goes only on Yes. Run 19, one RAW per project (core.rawcopies):
  an older run whose edit plays only the piece of the movie, with the whole movie unused next to it, is offered too --
  on Yes its edit is moved onto the whole movie (checked frame for frame) and the piece goes.
* Every 30 minutes (and at the start) the three channels' Shorts tabs are read (core.uploads): a Short with the same
  title moves its card to Uploaded with the date; a near match asks first ("Is this the same video?").
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMessageBox,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import Tool
from ..core import paths
from ..ui import background as BG
from ..ui import motion as Mo
from ..ui import theme
from ..ui import widgets as W

CARD_MIME = "application/x-movierecaps-card"
COLUMNS = [("link", "Link"), ("downloaded", "Downloaded"), ("converted", "Converted"), ("template", "Template"),
           ("edit", "Edit made"), ("fixed", "Fixed"), ("rendered", "Rendered"), ("uploaded", "Uploaded")]
TOP_LINES = {"link": theme.LINE2, "downloaded": theme.BLUE_LINE, "converted": "#2d4f86", "template": "#6b3a2a",
             "edit": theme.AMBER_LINE, "fixed": "#3d2f6b", "rendered": "#2f5f38", "uploaded": theme.GREEN}
COL_W = 140                        # 8 columns fit a 1280 px window; wider windows stretch them
RELOAD_MS = 60_000


def _columns():
    try:
        from ..core import board
        return list(getattr(board, "COLUMNS", COLUMNS))
    except Exception:  # noqa: BLE001
        return COLUMNS


def when(dt: datetime | None) -> str:
    return dt.strftime("%d %b %H:%M").lstrip("0") if dt else ""


_LONG_WORD = re.compile(r"[^\W_]{11,}")


def soft_breaks(text: str) -> str:
    """Where a card's name may break in a narrow column (zero-width spaces): after _ and -, and inside a word of 11+
    letters or digits ("Questionable" was cut to "Questional" in a 1280 px window, day 13) -- never inside an emoji."""
    text = str(text).replace("_", "_​").replace("-", "-​")
    return _LONG_WORD.sub(lambda m: "​".join(m.group(0)), text)


# ---------------------------------------------------------------------------------------------------------------------
# the weekly chart per channel
# ---------------------------------------------------------------------------------------------------------------------

class WeekChart(W.Card):
    DAYS = "MTWTFSS"

    def __init__(self, channel: str):
        super().__init__(hover=True, margins=(12, 10, 12, 10), spacing=8)
        self.channel = channel
        self.counts = [0] * 7
        self.grow = Mo.Once(self, self.update, 900, Mo.GLIDE)
        self.setFixedHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._first = True

    def set_counts(self, counts: list[int]) -> None:
        counts = [int(x) for x in (list(counts) + [0] * 7)[:7]]
        if counts != self.counts or self._first:
            self.counts = counts
            if self._first:
                self._first = False
                self.grow.play()
            self.update()

    def paintEvent(self, e) -> None:                      # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        self.paint_box(p, r)
        col = QColor(theme.channel_colour(self.channel))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(12, 15, 7, 7))
        p.setFont(W.font(12, 600))
        p.setPen(QColor(theme.TEXT))
        p.drawText(QPointF(25, 22), self.channel)
        total = sum(self.counts)
        t = f"{total} this week"
        p.setFont(W.mono(11))
        p.setPen(QColor(theme.MUTED))
        p.drawText(QPointF(r.width() - 12 - QFontMetrics(W.mono(11)).horizontalAdvance(t), 22), t)
        top, bottom = 32.0, r.height() - 24.0
        n = 7
        gap = 4.0
        w = (r.width() - 24 - gap * (n - 1)) / n
        mx = max(4, max(self.counts) if self.counts else 0)
        today = datetime.now().weekday()
        for i, c in enumerate(self.counts):
            k = self.grow.value if self.grow.active else 1.0
            k = max(0.0, min(1.0, k * 1.0 - i * 0.05)) if self.grow.active else 1.0
            h = max(2.0, (bottom - top) * c / mx) * (k if self.grow.active else 1.0)
            bar = QRectF(12 + i * (w + gap), bottom - h, w, h)
            cc = QColor(col)
            cc.setAlphaF(0.85 if c else 0.25)
            p.fillRect(bar, cc)
            p.setFont(W.mono(9.5))
            p.setPen(QColor(theme.CORAL if i == today else theme.DIM))
            p.drawText(QRectF(bar.left(), bottom + 4, w, 14), Qt.AlignmentFlag.AlignCenter, self.DAYS[i])
        p.end()


# ---------------------------------------------------------------------------------------------------------------------
# a card
# ---------------------------------------------------------------------------------------------------------------------

class BoardCard(QFrame):
    """A project on the board. Under the mouse its border turns dark red with a faint glow, the viewfinder corners
    fade in and the faint light crosses it -- nothing moves. Drag it to another column."""

    def __init__(self, page: "BoardPage", card):
        super().__init__()
        self.setObjectName("BoardCard")
        self.page, self.card = page, card
        self.hover = Mo.Fader(self, self._hot)
        self.light = Mo.Fader(self, self.update, 1000, 1000)
        self.appear = Mo.Fader(self, self._appearing, 300, 300)
        self._press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        shown = getattr(card, "display", "") or card.name   # day 13: the video's title is the name
        short = shown if len(shown) < 60 else shown[:58].rstrip() + "…"
        name = QLabel(soft_breaks(short))                  # it may break after _ and -, and inside a long word
        name.setFont(W.font(13, 700))
        name.setWordWrap(True)
        name.setToolTip(f"{shown}\nFolder: {card.folder}" if shown != card.name else str(card.folder))
        self.name_label = name
        top.addWidget(name, 1)
        self.pin = W.Button("★", "Ghost")
        self.pin.setToolTip("Pin to the top of its column")
        self.pin.setStyleSheet("")
        self.pin.clicked.connect(lambda: self.page.pin(self.card))
        top.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        self.rename = None
        target = getattr(card, "rename_to", "") or ""
        kept = getattr(card, "kept", "") or ""
        if target or kept:                                # day 13: the folder is not called after the title (yet)
            fl = QLabel(f"folder: {card.folder.name}".replace("_", "_​").replace("-", "-​"))
            fl.setFont(W.mono(10))
            fl.setWordWrap(True)
            fl.setStyleSheet(f"color: {theme.DIM};")
            fl.setToolTip(f"keeps its name: {kept}" if kept else f"Rename to title makes it: {target}")
            lay.addWidget(fl)
        if target:
            self.rename = W.Button("Rename to title", "Mini")
            self.rename.setToolTip(f"Renames the folder and the files the app made to:\n{target}\n(never while an "
                                   "edit, a copy to the shared folder or Premiere uses them -- then it waits)")
            self.rename.clicked.connect(lambda: self.page.rename_to_title(self.card))
            rl = QHBoxLayout()
            rl.addWidget(self.rename)
            rl.addStretch(1)
            lay.addLayout(rl)
        from ..ui.titles_block import TitlesBlock, has_titles, is_just_cuts
        data = getattr(card, "data", None) or {}
        lay.addWidget(W.channel_chip(card.channel or "no channel", 11))
        self.kind = None
        if is_just_cuts(data):                            # day 12: a Just cuts project (its own line: cards are narrow)
            self.kind = W.Pill("Just cuts")
            self.kind.set("Just cuts", (theme.CORAL, "#2a1812", "#5a2f22"))
            kl = QHBoxLayout()
            kl.addWidget(self.kind)
            kl.addStretch(1)
            lay.addLayout(kl)
        title = (card.title or "").strip()
        yt = (getattr(card, "upload_title", "") or "").strip()
        tl = QHBoxLayout()
        tl.setSpacing(6)
        tt = QLabel(" / ".join(x.strip() for x in title.splitlines() if x.strip()) or "no title yet")
        tt.setWordWrap(True)
        tt.setFont(W.font(11.5))
        tt.setStyleSheet(f"color: {theme.TEXT if title else theme.DIM};")
        tl.addWidget(tt, 1)
        if title or yt:
            cp = W.Button("Copy", "Copy")
            cp.setToolTip("Copies the YouTube title (when you set one), else the headline")
            cp.clicked.connect(self._copy)
            tl.addWidget(cp, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(tl)
        if yt:
            ytl = QLabel(f"<span style='color:{theme.CORAL}'>YOUTUBE</span>&nbsp; {yt}")
            ytl.setWordWrap(True)
            ytl.setFont(W.font(11))
            ytl.setStyleSheet(f"color: {theme.MUTED};")
            lay.addWidget(ytl)
        self.titles = None
        if has_titles(data):                              # day 12: the competitor's two real titles, compact
            self.titles = TitlesBlock(compact=True)
            self.titles.from_project(data)
            self.titles.copied.connect(lambda _t: self.page.toast("Copied ✓"))
            self.titles.use_headline.connect(lambda t: self.page.use_headline(self.card, t))
            self.titles.use_title.connect(self._use_title)
            lay.addWidget(self.titles)
        dates = QLabel(self._dates())
        dates.setFont(W.mono(10.5))
        dates.setWordWrap(True)
        dates.setStyleSheet(f"color: {theme.DIM};")
        lay.addWidget(dates)
        self.zip_line = None
        zb = getattr(card, "zip_bytes", None)
        if zb is not None:                                # day 16: the zip to send -- the whole project folder
            from ..core.share_zip import size_text, zip_path
            self.zip_line = QLabel(f"zip {size_text(zb)}")
            self.zip_line.setFont(W.mono(10.5))
            self.zip_line.setStyleSheet(f"color: {theme.DIM};")
            self.zip_line.setToolTip(f"{zip_path(card.folder)}\nthe whole project folder, to send")
            lay.addWidget(self.zip_line)
        lock = getattr(card, "lock", None)
        if lock:
            lk = W.label(f"{lock.get('computer', 'another computer')} is working on it ({lock.get('what', '')})",
                         "Dim", wrap=True)
            lk.setStyleSheet(f"color: {theme.AMBER};")
            lay.addWidget(lk)
        if getattr(card, "error", ""):
            er = W.label(card.error, "Dim", wrap=True)
            er.setStyleSheet(f"color: {theme.RED};")
            lay.addWidget(er)
        if getattr(card, "where", "") == "local" and getattr(card, "column", "") in ("edit", "fixed", "rendered",
                                                                                      "uploaded"):
            lk = W.label("on this computer only (not in the shared folder yet)", "Dim", wrap=True)
            lk.setStyleSheet(f"color: {theme.AMBER};")
            lay.addWidget(lk)
        self._pinned(bool(getattr(card, "pinned", False)))

    def _pinned(self, on: bool) -> None:
        self.pin.setText("★")
        self.pin_on = on
        self.pin.update()

    def _dates(self) -> str:
        c = self.card
        names = dict(_columns())
        rows = []
        for key, _label in _columns():
            d = (getattr(c, "dates", None) or {}).get(key)
            if d is None:
                continue
            extra = ""
            if key == "edit" and getattr(c, "edit_result", ""):
                extra = f" · {c.edit_result}"
            rows.append(f"{names.get(key, key)} {when(d)}{extra}")
        return "<br>".join(rows) or "no step done yet"

    def _use_title(self, text: str) -> None:
        """Day 12: "Use as YouTube title" from the headline on the competitor's video."""
        from .match_cuts_tool import save_upload_title
        if text:
            save_upload_title(self.page.ctx, self.card.folder, text)

    def _copy(self) -> None:
        yt =(getattr(self.card, "upload_title", "") or "").strip()
        t = yt or " ".join(x.strip() for x in (self.card.title or "").splitlines() if x.strip())
        QApplication.clipboard().setText(t)
        self.page.toast("YouTube title copied ✓" if yt else "Title copied ✓")

    def contextMenuEvent(self, e) -> None:                # noqa: N802
        from ..ui.menu import FadeMenu
        from .match_cuts_tool import ask_upload_title, open_in_premiere, save_upload_title
        c = self.card
        edit = sorted(Path(c.folder).glob("edit*/1_edit.xml"), key=lambda p: p.stat().st_mtime) \
            if Path(c.folder).is_dir() else []

        def set_yt() -> None:
            text = ask_upload_title(self, getattr(c, "upload_title", ""))
            if text is not None:
                save_upload_title(self.page.ctx, c.folder, text)

        page = self.page                                  # (this card may be rebuilt while Premiere opens)

        def said(msg) -> None:
            msg = str(msg)
            page.status.setText(msg)                      # the whole sentence (what to do next) ...
            page.toast(msg.split(" -- ")[0][:150], "info")   # ... and its first part

        def premiere() -> None:
            BG.call(lambda: open_in_premiere(edit[-1]), said,
                    lambda err: page.toast(f"Premiere could not be opened: {err}", "fail"))
        items = [("Open folder", lambda: os.startfile(str(c.folder)), False, Path(c.folder).exists()),  # noqa: S606
                 ("Open in Premiere", premiere, False, bool(edit)),
                 ("Copy title", self._copy, False, bool(c.title or getattr(c, "upload_title", ""))),
                 ("Set the YouTube title…", set_yt),
                 ("Rename to title", lambda: self.page.rename_to_title(c), False,
                  bool(getattr(c, "rename_to", ""))),
                 *(([None] + self.titles.menu_items()) if self.titles is not None else []),
                 None,
                 ("Unpin" if getattr(c, "pinned", False) else "Pin to the top ★", lambda: self.page.pin(c))]
        FadeMenu(self, items).popup(e.globalPos())

    def resizeEvent(self, e) -> None:                     # noqa: N802
        """Tall enough for its word-wrapped lines at the width it got (the column's layouts do not pass the height
        for a width down by themselves: the dates and titles were cut off at the bottom)."""
        super().resizeEvent(e)
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            h = lay.totalHeightForWidth(self.width())
            if h > 0 and self.minimumHeight() != h:
                self.setMinimumHeight(h)

    def _hot(self) -> None:
        self.update()
        par = self.parentWidget()
        if par is not None:
            par.update(self.geometry().adjusted(-30, -30, 30, 40))

    def _appearing(self) -> None:
        eff = self.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            eff.setOpacity(self.appear.value)
            if self.appear.value >= 1.0:
                self.setGraphicsEffect(None)

    def fade_in(self) -> None:
        if not Mo.enabled():
            return
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        self.appear.set(0.0)
        self.appear.to(1.0)

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        self.light.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        self.light.to(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:                 # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:                  # noqa: N802
        if self._press is not None and (e.position() - self._press).manhattanLength() > \
                QApplication.startDragDistance():
            self._press = None
            drag = QDrag(self)
            md = QMimeData()
            md.setData(CARD_MIME, str(self.card.folder).encode("utf-8"))
            drag.setMimeData(md)
            pm = self.grab()
            drag.setPixmap(pm)
            drag.setHotSpot(e.position().toPoint())
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.45)
            self.setGraphicsEffect(eff)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            page = self.page
            page.dragging = True                           # no rebuild of the cards while one is in the air
            try:
                drag.exec(Qt.DropAction.MoveAction)
            finally:
                page.dragging = False
                try:
                    self.setGraphicsEffect(None)
                    self.setCursor(Qt.CursorShape.OpenHandCursor)
                except RuntimeError:                       # the card was rebuilt meanwhile
                    pass
                page.clear_slots()
                if getattr(page, "_pending_cards", None) is not None:
                    cards, page._pending_cards = page._pending_cards, None
                    page.show_cards(cards)
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, _e) -> None:          # noqa: N802
        d = Path(self.card.folder)
        if d.exists():
            os.startfile(str(d))                          # noqa: S606

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        h = self.hover.value
        W.box(p, r, Mo.mix(theme.PANEL2, "#1d1a1a", h), Mo.mix(theme.LINE2, theme.WINE_LINE, h))
        W.sweep(p, r.adjusted(1, 1, -1, -1), self.light.value, peak=0.03)
        W.corners(p, r, h)
        if getattr(self, "pin_on", False):
            p.setPen(QColor(theme.LOU))
            p.setFont(W.font(13))
            pr = QRectF(self.pin.geometry())
            p.fillRect(pr, Mo.mix(theme.PANEL2, "#1d1a1a", h))
            p.drawText(pr, Qt.AlignmentFlag.AlignCenter, "★")
        p.end()


class BoardColumn(QFrame):
    """A column: its name and count, a thin coloured top line, its cards; a dragged card opens a slot where it will
    land (it grows smoothly) and the column warms up."""

    def __init__(self, page: "BoardPage", key: str, label: str):
        super().__init__()
        self.setObjectName("BoardColumn")
        self.page, self.key, self.label = page, key, label
        self.setAcceptDrops(True)
        self.hover = Mo.Fader(self, self.update, Mo.FADE_IN_MS, 500)
        self.over = Mo.Fader(self, self.update, 300, 500)
        self.slot = Mo.Fader(self, self._slot_grow, 260, 200, Mo.GLIDE)
        self.setMinimumWidth(COL_W)
        self.setMaximumWidth(COL_W + 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel(label.upper())
        self.title.setFont(W.font(11, 600, 8))
        self.title.setStyleSheet(f"color: {theme.MUTED};")
        self.n = QLabel("0")
        self.n.setFont(W.font(11, 600))
        self.n.setStyleSheet(f"color: {theme.MUTED};")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.n)
        lay.addLayout(head)
        self.cards_lay = QVBoxLayout()
        self.cards_lay.setSpacing(8)
        lay.addLayout(self.cards_lay)
        self.spacer = QWidget()
        self.spacer.setFixedHeight(0)
        self.spacer.setObjectName("Transparent")
        lay.addStretch(1)
        self.setMinimumHeight(220)
        self._slot_at = 0

    def set_cards(self, cards: list, fade: set | None = None) -> None:
        while self.cards_lay.count():
            it = self.cards_lay.takeAt(0)
            w = it.widget()
            if w is not None and w is not self.spacer:     # the drop slot is the column's own: kept
                w.setParent(None)
                w.deleteLater()
        self.widgets = []
        for c in cards:
            w = BoardCard(self.page, c)
            self.cards_lay.addWidget(w)
            self.widgets.append(w)
            if fade and str(c.folder) in fade:
                w.fade_in()
        if self.slot.target > 0.0:                         # a card is being dragged over it: the slot stays open
            self.cards_lay.insertWidget(min(self._slot_at, self.cards_lay.count()), self.spacer)
        else:
            self.spacer.hide()
            self.spacer.setFixedHeight(0)
        self.n.setText(str(len(cards)))

    def _slot_grow(self) -> None:
        self.spacer.setFixedHeight(int(round(70 * self.slot.value)))
        self.update()

    def enterEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:                      # noqa: N802
        self.hover.to(0.0)
        super().leaveEvent(e)

    def dragEnterEvent(self, e) -> None:                  # noqa: N802
        if e.mimeData().hasFormat(CARD_MIME):
            e.acceptProposedAction()
            self.over.to(1.0)
            pinned = sum(1 for w in getattr(self, "widgets", []) if getattr(w, "pin_on", False))
            self._slot_at = pinned
            self.cards_lay.insertWidget(pinned, self.spacer)
            self.spacer.show()
            self.slot.to(1.0)

    def dragMoveEvent(self, e) -> None:                   # noqa: N802
        if e.mimeData().hasFormat(CARD_MIME):
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e) -> None:                 # noqa: N802
        self.clear_slot()

    def clear_slot(self) -> None:
        self.over.to(0.0)
        self.slot.to(0.0)
        QTimer.singleShot(220, self._remove_spacer)

    def _remove_spacer(self) -> None:
        if self.slot.target <= 0.0:
            self.cards_lay.removeWidget(self.spacer)
            self.spacer.hide()
            self.spacer.setFixedHeight(0)

    def dropEvent(self, e) -> None:                       # noqa: N802
        if not e.mimeData().hasFormat(CARD_MIME):
            return
        e.acceptProposedAction()
        folder = bytes(e.mimeData().data(CARD_MIME)).decode("utf-8")
        self.clear_slot()
        self.page.move(folder, self.key, self.label)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        h, o = self.hover.value, self.over.value
        fill = Mo.mix(Mo.mix(theme.PANEL, "#161616", h), "#1b1415", o)
        W.box(p, r, fill, Mo.mix(theme.LINE, theme.WINE_LINE, o))
        p.fillRect(QRectF(0, 0, r.width(), 2), QColor(TOP_LINES.get(self.key, theme.LINE2)))
        for w in getattr(self, "widgets", []):
            try:
                if w.hover.value > 0:
                    W.soft_shadow(p, QRectF(w.geometry()), theme.RED, 0.14 * w.hover.value)
            except RuntimeError:
                pass
        if self.spacer.isVisible() and self.spacer.height() > 2:
            g = QRectF(self.spacer.geometry()).adjusted(0, 0, 0, 0)
            pen = QPen(Mo.alpha(theme.RED, 0.7 * self.slot.value), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRect(g.adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()


class AskUpload(W.Card):
    """'Is this the same video?' -- a Short whose title is close to a project's, not the same: Yes / No."""

    def __init__(self, page: "BoardPage", card, short, score: float):
        super().__init__(hover=False, line=theme.AMBER_LINE, margins=(16, 12, 16, 12))
        self.page, self.card, self.short = page, card, short
        t = W.label("Is this the same video?")
        t.setFont(W.font(14, 700))
        self.lay.addWidget(t)
        ct = " / ".join(x.strip() for x in (card.title or "").splitlines() if x.strip())
        self.lay.addWidget(W.label(f"Your project: “{ct}”  ({card.name})", "Muted", wrap=True))
        self.lay.addWidget(W.label(f"On {getattr(short, 'channel', '')}'s Shorts: “{getattr(short, 'title', '')}”  "
                                   f"({100 * score:.0f} % alike)", "Muted", wrap=True))
        why = getattr(page, "_why", "")
        if why:
            self.lay.addWidget(W.label(f"Why I think so: {why}", "Dim", wrap=True))
        row = QHBoxLayout()
        yes = W.Button("Yes -- it is uploaded", "Primary")
        yes.clicked.connect(lambda: self.page.answer_upload(self, True))
        no = W.Button("No")
        no.clicked.connect(lambda: self.page.answer_upload(self, False))
        row.addWidget(yes)
        row.addWidget(no)
        row.addStretch(1)
        self.lay.addLayout(row)


# ---------------------------------------------------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------------------------------------------------

class BoardPage(QWidget):
    changed = Signal(list)                 # the cards, after every load
    cleanup_progress = Signal(str)         # Clean up's progress, said from its worker thread

    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.cards: list = []
        self.datas: list = []
        self._sig = None
        self._moved: set = set()
        self._loading = False
        self._checking = False
        self.asked: set = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(16)
        self.note = W.label("Drag a card to any column", "Small")
        self.b_refresh = W.Button("Refresh", "Small")
        self.b_refresh.clicked.connect(self.reload)
        self.b_check = W.Button("Check the Shorts now", "Small")
        self.b_check.clicked.connect(lambda: self.check_uploads(force=True))
        self.b_clean = W.Button("Clean up", "Small")
        self.b_clean.clicked.connect(self.clean_up)
        lay.addWidget(W.SectionHeader("Projects", [self.note, self.b_refresh, self.b_check, self.b_clean]))
        self.status = W.label("", "Dim", wrap=True, select=True)
        lay.addWidget(self.status)
        self.cleanup_progress.connect(self.status.setText)
        self.asks = QVBoxLayout()
        self.asks.setSpacing(10)
        lay.addLayout(self.asks)
        week = QHBoxLayout()
        week.setSpacing(10)
        self.weeks = {}
        for ch in ("LouLaughs", "MikeMemesYou", "Flick707"):
            wc = WeekChart(ch)
            self.weeks[ch] = wc
            week.addWidget(wc)
        lay.addLayout(week)
        self.cols_box = QWidget()
        self.cols_box.setObjectName("Transparent")
        cl = QHBoxLayout(self.cols_box)
        cl.setContentsMargins(0, 0, 0, 4)
        cl.setSpacing(10)
        self.columns: dict[str, BoardColumn] = {}
        for key, label in _columns():
            c = BoardColumn(self, key, label)
            self.columns[key] = c
            cl.addWidget(c)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(self.cols_box)
        sa.setMinimumHeight(520)
        self.cols_scroll = sa
        lay.addWidget(sa, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.reload)
        self._timer.start(RELOAD_MS)
        self._up_timer = QTimer(self)
        self._up_timer.timeout.connect(self.check_uploads)
        self._up_timer.start(60_000)
        self._first = True
        QTimer.singleShot(400, self.reload)

    # -- helpers --
    def toast(self, text: str, kind: str = "ok") -> None:
        fn = getattr(self.ctx, "toast", None)
        if fn is not None:
            fn(text, kind)

    def _roots(self):
        local = paths.projects_root()
        try:
            from ..core import settings
            shared = settings.save_root(getattr(self.ctx, "settings", None))
        except Exception:  # noqa: BLE001
            shared = None
        return shared, local

    # -- loading --
    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        shared, local = self._roots()

        def work():
            from ..core import board
            cards = board.load_cards(shared, local)
            datas = [dict(c.data, _folder=str(c.folder)) for c in cards              # (for the History tab; day 16:
                     if isinstance(getattr(c, "data", None), dict)]                 # its folder, Try another RAW)
            return cards, datas

        def got(r) -> None:
            cards, self.datas = r
            self._loading = False
            self.show_cards(cards)
            if self._first:
                self._first = False
                QTimer.singleShot(1500, lambda: self.check_uploads(force=True))

        def bad(err: str) -> None:
            self._loading = False
            self.status.setText(f"the board could not be read: {err}")
        BG.call(work, got, bad)

    def show_cards(self, cards: list) -> None:
        if getattr(self, "dragging", False):              # shown when the card being dragged lands
            self._pending_cards = list(cards)
            return
        self.cards = list(cards)
        sig = tuple((str(c.folder), c.column, c.title, c.pinned, tuple(sorted((k, str(v)) for k, v in
                                                                           (c.dates or {}).items())),
                     bool(getattr(c, "lock", None)), getattr(c, "edit_result", ""), getattr(c, "error", ""),
                     getattr(c, "display", ""), getattr(c, "rename_to", ""), getattr(c, "kept", ""),
                     getattr(c, "upload_title", ""))
                    for c in self.cards)
        if sig != self._sig:
            self._sig = sig
            try:
                from ..core import board
                by_col = board.ordered(self.cards)
            except Exception:  # noqa: BLE001
                by_col = {}
                for c in self.cards:
                    by_col.setdefault(c.column, []).append(c)
            fade, self._moved = self._moved, set()
            for key, col in self.columns.items():
                col.set_cards(by_col.get(key, []), fade)
        self._weekly()
        self.status.setText(f"{len(self.cards)} project(s)" if self.cards else
                            "No projects yet: paste a link in Download (Ctrl+V) or drop videos on Match cuts.")
        self.changed.emit(self.cards)

    def _weekly(self) -> None:
        try:
            from ..core import board, motivation
            ups = board.uploads(self.cards)
            per = motivation.week(ups, datetime.now())
        except Exception:  # noqa: BLE001
            return
        for ch, wc in self.weeks.items():
            wc.set_counts(per.get(ch, [0] * 7))

    def clear_slots(self) -> None:
        for c in self.columns.values():
            c.clear_slot()

    # -- moves --
    def move(self, folder: str, key: str, label: str) -> None:
        card = next((c for c in self.cards if str(c.folder) == folder), None)
        if card is not None and card.column == key:
            return

        def work():
            from ..core import board
            board.move(Path(folder), key)

        def done(_r) -> None:
            self._moved.add(folder)
            self.toast(f"Moved to {label} ✓" if key != "uploaded" else "Uploaded ✓")
            self.reload()
        BG.call(work, done, lambda e: self.toast(f"Could not move it: {e}", "fail"))

    def pin(self, card) -> None:
        on = not bool(getattr(card, "pinned", False))

        def work():
            from ..core import board
            board.pin(Path(card.folder), on)
        BG.call(work, lambda _r: (self.toast("Pinned ★" if on else "Unpinned"), self.reload()),
                lambda e: self.toast(f"Could not pin it: {e}", "fail"))

    def use_headline(self, card, text: str) -> str:
        """Day 12: "Use as my headline" on a card (the headline read on the competitor's video), the same rules as a
        queue row (core.flow.headline_action): no edit made yet -> the project's headline is set (template.png made
        again when it was made before); the edit running (or another computer working on it) -> nothing changes,
        said; an edit made -> asked first, then Change headline (only template.png again, swapped into the edit)."""
        from ..core import flow
        from ..core import projects as PJ
        text = "\n".join(x.strip() for x in (text or "").splitlines() if x.strip())[:200]
        if not text:
            return ""
        folder = Path(card.folder)
        what = flow.headline_action(getattr(card, "data", None) or {})
        if what == flow.USE_RUNNING or getattr(card, "lock", None):
            self.toast("The edit is running -- use the headline when it has finished", "note")
            return flow.USE_RUNNING
        if what == flow.USE_CHANGE:
            if not card.channel:
                self.toast("This video has no channel, so it has no template to change", "note")
                return ""
            if not self.ask_yes("Use as my headline",
                                f"Make the template again with this headline and swap it into the finished edit?\n\n"
                                f"{text}\n\nNo new run -- only template.png changes."):
                return ""
            ch = card.channel
            BG.call(lambda: flow.change_headline(PJ.load(folder), text, ch),
                    lambda _r: (self.toast("New template made and swapped into the edit ✓"), self.reload(),
                                self._title_changed(folder)),
                    lambda e: self.toast(f"The template could not be made: {e}", "fail"), work=True)
            return flow.USE_CHANGE
        BG.call(lambda: flow.set_headline(folder, text),
                lambda r: (self.toast("Headline set ✓" + (" -- template made again" if (r or {}).get("template")
                                                          else "")), self.reload(), self._title_changed(folder)),
                lambda e: self.toast(f"The headline could not be saved: {e}", "fail"), work=True)
        return flow.USE_SET

    # -- day 13: names = the video's title --
    def _title_changed(self, folder) -> None:
        from .match_cuts_tool import title_changed
        title_changed(self.ctx, folder)

    def rename_to_title(self, card) -> None:
        """"Rename to title" on a card: the folder and the files the app made are renamed after the video's title (an
        old project too) -- in the background; when something uses them it waits and is done later by itself."""
        folder = Path(card.folder)
        fn = getattr(self.ctx, "title_changed", None)
        if callable(fn):
            fn(folder, explicit=True)
            return
        shared, local = self._roots()                     # (no window: the page does it itself -- the tests)

        def work():
            from ..core import renames
            return renames.request(folder, hub=shared, local_root=local, explicit=True)

        def done(res) -> None:
            self.last_rename = res
            self.toast(f"Renamed after its title ✓  {Path(res.folder).name}" if res.renamed else res.text,
                       "ok" if res.renamed else "note")
            self.reload()
        BG.call(work, done, lambda e: self.toast(f"It could not be renamed: {e}", "fail"))

    def moved(self, _old, _new) -> None:
        """A project folder was renamed: the board is read again."""
        self.reload()

    def ask_yes(self, title: str, text: str) -> bool:
        """A Yes / No question (a test replaces it)."""
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    # -- the automatic "Uploaded" check --
    def check_uploads(self, force: bool = False) -> None:
        if self._checking or not self.cards:
            return
        s = getattr(self.ctx, "settings", None)
        minutes = int(getattr(s, "upload_check_minutes", 30) or 30)
        try:
            from ..core import uploads
            state = uploads.UploadCheck()
            if not force and not state.due(datetime.now(), minutes):
                return
        except Exception:  # noqa: BLE001 - the module (night 11) is missing
            return
        self._checking = True
        cards = list(self.cards)
        self.b_check.setEnabled(False)

        def work():
            return state.run(cards, datetime.now())

        def got(res) -> None:
            self._checking = False
            self.b_check.setEnabled(True)
            self._uploads(res)

        def bad(err: str) -> None:
            self._checking = False
            self.b_check.setEnabled(True)
            self.status.setText(f"the Shorts could not be checked: {err}")
        BG.call(work, got, bad)

    def _uploads(self, res) -> None:
        if res is None or getattr(res, "offline", False):
            self.note.setText("Shorts not checked: no internet -- tried again later")
            return
        exact = list(getattr(res, "exact", []) or [])
        near = list(getattr(res, "near", []) or [])
        self.note.setText(f"Shorts checked {datetime.now().strftime('%H:%M')}")
        if exact:
            def work():
                done = []
                for m in exact:
                    card_key, short = m[0], m[1]
                    card = next((c for c in cards if str(c.folder) == str(card_key) or c.name == card_key), None)
                    if card is None:
                        continue
                    at = getattr(m, "at", None) or datetime.now()      # (the check read the Short's own date)
                    _mark_uploaded(card.folder, short, at, "auto")
                    done.append(card)
                return done
            cards = list(self.cards)
            BG.call(work, lambda done: (self.toast(f"Uploaded ✓ {len(done)} video(s) found on the channels")
                                        if done else None, self.reload()))
        for item in near:
            card_key, short, score = item[0], item[1], float(item[2]) if len(item) > 2 else 0.0
            key = (str(card_key), getattr(short, "id", ""))
            if key in self.asked:
                continue
            card = next((c for c in self.cards if str(c.folder) == str(card_key) or c.name == card_key), None)
            if card is None:
                continue
            self.asked.add(key)
            self._why = getattr(item, "why", "") or ""
            ask = AskUpload(self, card, short, score)
            ask.at = getattr(item, "at", None)
            self.asks.addWidget(ask)
            self.toast("A Short looks like one of your videos -- see the Board", "note")

    def answer_upload(self, ask: AskUpload, yes: bool) -> None:
        card, short = ask.card, ask.short
        ask.setParent(None)
        ask.deleteLater()

        at = getattr(ask, "at", None)

        def work():
            if yes:
                from ..core import uploads
                _mark_uploaded(card.folder, short, at or uploads.upload_time(short) or datetime.now(), "you")
            else:
                _not_same(card.folder, getattr(short, "id", ""))
        BG.call(work, lambda _r: (self.toast("Uploaded ✓" if yes else "Noted: not the same video"), self.reload()))

    # -- clean up --
    def _cleanup_say(self, text: str) -> None:
        """Clean up's progress, from its worker thread (a queued signal: the label changes in the window's thread)."""
        try:
            self.cleanup_progress.emit(str(text))
        except RuntimeError:                   # the page closed meanwhile
            pass

    def clean_up(self) -> None:
        cards = list(self.cards)
        local = paths.projects_root()
        self.b_clean.setEnabled(False)
        before = self.status.text()
        self.status.setText("Clean up: looking for leftovers ...")
        say = self._cleanup_say

        def work():
            from ..core import board, rawcopies, rawfiles, rawkeep
            junk = board.cleanup_plan(cards, local_root=local)
            # day 12: older runs of a 5.1 RAW hold the RAW twice -- checked here, removed only after you say yes
            raws = rawfiles.plan(cards, local_root=local, say=say)
            # day 13: older runs that play a big converted raw_stereo copy -- the original RAW instead, after yes
            big = rawkeep.plan(cards, local_root=local, say=say)
            # run 19: older runs that hold a RAW nothing plays (the movie next to the piece the edit plays)
            one = rawcopies.plan(cards, local_root=local, say=say)
            return junk, raws, big, one

        def got(res) -> None:
            self.b_clean.setEnabled(True)
            self.status.setText(before)
            junk, raws, big, one = res
            ok = [r for r in raws if r.ok]
            kept = [r for r in raws if not r.ok]
            okb = [b for b in big if b.ok]
            keptb = [b for b in big if not b.ok]
            okc = [c for c in one if c.ok]
            keptc = [c for c in one if not c.ok]
            if not junk and not ok and not okb and not okc:
                self.toast("Nothing to clean up ✓" + (f" ({len(kept)} old run(s) keep two RAW files: see above)"
                                                        if kept else ""))
                if kept or keptb or keptc:
                    self.status.setText("\n".join(t for t in (self._kept_text(kept) if kept else "",
                                                             self._kept_big_text(keptb) if keptb else "",
                                                             self._kept_one_text(keptc) if keptc else "") if t))
                return
            from ..core import publish
            total = (sum(int(getattr(j, "size", 0) or 0) for j in junk) + sum(r.freed for r in ok)
                     + sum(b.freed for b in okb) + sum(max(0, c.freed) for c in okc))
            lines = [f"{publish.plain_size(getattr(j, 'size', 0))}  {Path(j.path).name}  ({getattr(j, 'why', '')})"
                     for j in junk[:25]]
            more = f"\n... and {len(junk) - 25} more" if len(junk) > 25 else ""
            text = ""
            if junk:
                text += f"Remove {len(junk)} finished leftover file(s)?\n\n" + "\n".join(lines) + more + "\n\n"
            if ok:
                text += ("One RAW file per run -- these older runs hold the RAW twice. Their raw_stereo file opens "
                         "and is the same video (the same frames, stereo sound), and nothing plays the old copy, so "
                         "the old copy can go:\n\n" + "\n".join(r.line() for r in ok[:25])
                         + (f"\n... and {len(ok) - 25} more" if len(ok) > 25 else "") + "\n\n")
            if kept:
                text += self._kept_text(kept) + "\n\n"
            if okb:
                text += ("Keep the original RAW -- these older runs play a big converted copy (raw_stereo). The "
                         "original RAW is still on this computer and is the very file the run was made from, so the "
                         "edit can play it (V1) with a small stereo sound file (A1) -- the same frames and sound -- "
                         "and the big copy can go:\n\n" + "\n".join(b.line() for b in okb[:25])
                         + (f"\n... and {len(okb) - 25} more" if len(okb) > 25 else "") + "\n\n")
            if keptb:
                text += self._kept_big_text(keptb) + "\n\n"
            if okc:
                pieces = [c for c in okc if c.kind == "piece"]
                text += ("One RAW per project -- these projects hold a RAW that nothing plays."
                         + (" Their edit plays only the piece of the movie the run analysed (a bug until run 19: the "
                            "song on A2 stopped the edit's move onto the movie), with the whole movie unused next to "
                            "it: Clean up moves each edit onto the whole movie, every clip at its own frame (checked "
                            "against the movie), and the piece goes -- the project then holds its RAW once."
                            if pieces else "")
                         + (" The others: the unused copy goes." if len(pieces) < len(okc) else "")
                         + "\n\n" + "\n".join(c.line() for c in okc[:25])
                         + (f"\n... and {len(okc) - 25} more" if len(okc) > 25 else "") + "\n\n")
            if keptc:
                text += self._kept_one_text(keptc) + "\n\n"
            moved = okb or any(c.kind == "piece" for c in okc)
            text += (f"Space saved: {publish.plain_size(total)}.\n\nNever touched: your Premiere projects (a run one "
                     "of them plays keeps its copy), template.png, project.json, title.txt. "
                     + ("Changed for the runs above: only their own 1_edit.xml / extras XMLs (they play the original "
                        "RAW / the whole movie now)." if moved else "Never touched either: the edits themselves (their "
                                                                   "XMLs, raw_stereo, the captions)."))
            r = QMessageBox.question(self, "Clean up", text)
            if r != QMessageBox.StandardButton.Yes:
                return
            self.b_clean.setEnabled(False)

            def work2():
                from ..core import board, rawcopies, rawfiles, rawkeep
                freed, errors = board.cleanup(junk) if junk else (0, [])
                f2, e2 = rawfiles.remove(ok, say=say)
                f3, e3 = rawkeep.replace(okb, say=say) if okb else (0, [])
                f4, e4 = rawcopies.remove(okc, say=say) if okc else (0, [])
                return freed + f2 + f3 + max(0, f4), errors + e2 + e3 + e4

            def done(res2) -> None:
                self.b_clean.setEnabled(True)
                self.status.setText("\n".join(res2[1][:6]))
                self.toast(f"Freed {publish.plain_size(res2[0])} ✓"
                           + (f" ({len(res2[1])} could not be removed)" if res2[1] else ""))
                self.reload()
            BG.call(work2, done, lambda e: (self.b_clean.setEnabled(True), self.status.setText(""),
                                            self.toast(f"Clean up failed: {e}", "fail")), work=True)
        BG.call(work, got, lambda e: (self.b_clean.setEnabled(True), self.status.setText(before),
                                      self.toast(f"Clean up failed: {e}", "fail")))

    @staticmethod
    def _kept_text(kept) -> str:
        """The old runs that keep both RAW files, and why (plain words)."""
        return "Kept (both RAW files stay):\n" + "\n".join(f"  {r.where}: " + "; ".join(r.problems[:2])
                                                          for r in kept[:10])

    @staticmethod
    def _kept_one_text(kept) -> str:
        """Run 19: the projects that keep a RAW nothing plays, and why (plain words)."""
        return ("Kept (the RAW nothing plays stays):\n"
                + "\n".join(f"  {c.where}: " + "; ".join(c.problems[:1]) for c in kept[:10])
                + (f"\n  ... and {len(kept) - 10} more" if len(kept) > 10 else ""))

    @staticmethod
    def _kept_big_text(kept) -> str:
        """The old runs that keep their big converted copy, and why (plain words)."""
        return ("Kept (the big converted copy stays):\n"
                + "\n".join(f"  {b.where}: " + "; ".join(b.problems[:1]) for b in kept[:10])
                + (f"\n  ... and {len(kept) - 10} more" if len(kept) > 10 else ""))

    def settings_changed(self, _s) -> None:
        self._sig = None
        self.reload()


def _mark_uploaded(folder, short, at: datetime, how: str) -> None:
    """The card to Uploaded with the Short's date, its id and link (core.board)."""
    from ..core import board
    board.mark_uploaded(Path(folder), at, video_id=getattr(short, "id", "") or "", url=getattr(short, "url", "") or "",
                        title=getattr(short, "title", "") or "", how=how)


def _not_same(folder, video_id: str) -> None:
    """You said No: that Short is never asked about for this project again."""
    from ..core import board
    board.not_same(Path(folder), video_id)


class BoardTool(Tool):
    key = "board"
    title = "Board"

    def page(self, ctx) -> QWidget:
        return BoardPage(ctx)
