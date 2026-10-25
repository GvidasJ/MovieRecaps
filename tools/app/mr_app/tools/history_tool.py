"""The History tab (night 11): a timeline of everything -- link added, downloaded, converted, template made, edit made
(PASS / a note / failed), fixed, rendered, uploaded -- with its date and time, the channel's colour, and a search box
(a title, a channel, a step). Built from every project's project.json (core.history), so the PC and the laptop see
the same history."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from . import Tool
from ..ui import theme
from ..ui import widgets as W

SHOW_MAX = 500


class _Item(QWidget):
    """One event: the channel-coloured marker on the line, the time, what happened, the title and channel."""

    def __init__(self, ev, page=None):
        super().__init__()
        self.ev = ev
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.retry = bool(getattr(ev, "retry", False)) and bool(getattr(ev, "folder", "") or getattr(ev, "extra", ""))
        # day 16: an edit that failed because of its RAW keeps its YouTube title and headline here, to reuse
        yt = (getattr(ev, "upload_title", "") or "").strip()
        head = (getattr(ev, "title", "") or "").replace("\n", " / ").strip()
        self.line3 = " · ".join(x for x in ((f"YouTube: {yt}" if yt else ""), (f"Headline: {head}" if head else ""))
                                if x) if self.retry else ""
        self.setFixedHeight(76 if self.line3 else 58)
        self.b_retry = self.b_comp = self.b_again = None
        if self.retry and page is not None:
            from ..core import another_raw as AR
            self.b_again = W.Button("Try again", "Small", parent=self)          # run 18: every failure
            self.b_again.setToolTip("Try this step again (a download, a conversion, a template or the edit), with "
                                    "the same RAW, competitor, headline, YouTube title and channel")
            self.b_again.clicked.connect(lambda: page.try_again(ev))
            self.b_retry = W.Button(AR.BUTTON, "Small", parent=self)
            self.b_retry.setToolTip(AR.TIP)
            self.b_retry.clicked.connect(lambda: page.try_another_raw(ev))
            self.b_comp = W.Button(AR.BUTTON_COMP, "Small", parent=self)      # day 17: both on every failed edit
            self.b_comp.setToolTip(AR.TIP_COMP)
            self.b_comp.clicked.connect(lambda: page.try_another_raw(ev, "competitor"))
        t = (getattr(ev, "title", "") or "").replace("\n", " / ")
        shown = getattr(ev, "shown", "") or t or getattr(ev, "name", "")     # day 13: the video's title is the name
        name = getattr(ev, "name", "")
        tip = "\n".join(x for x in (getattr(ev, "detail", ""), f"Folder: {name}" if name and name != shown else "")
                        if x)
        if tip:
            self.setToolTip(tip)
        self.line2 = " · ".join(x for x in (shown, getattr(ev, "channel", "")) if x)

    def sizeHint(self) -> QSize:                          # noqa: N802
        return QSize(600, self.height())

    def resizeEvent(self, e) -> None:                     # noqa: N802
        if self.b_retry is not None:
            self.b_retry.adjustSize()
            self.b_retry.move(self.width() - self.b_retry.width() - 4, 6)
        if self.b_comp is not None:
            self.b_comp.adjustSize()
            self.b_comp.move(self.width() - self.b_retry.width() - self.b_comp.width() - 10, 6)
        if self.b_again is not None:
            self.b_again.adjustSize()
            self.b_again.move(self.width() - self.b_retry.width() - self.b_comp.width() - self.b_again.width() - 16,
                              6)
        super().resizeEvent(e)

    def paintEvent(self, _e) -> None:                     # noqa: N802
        p = QPainter(self)
        ev = self.ev
        p.fillRect(QRectF(5, 0, 1, self.height()), QColor(theme.LINE2))          # the timeline
        mark = QRectF(1, 8, 9, 9)
        p.fillRect(mark, QColor(theme.PANEL))
        p.setPen(QPen(QColor(theme.channel_colour(getattr(ev, "channel", "")) if getattr(ev, "channel", "") else
                             theme.MUTED), 2))
        p.drawRect(mark.adjusted(1, 1, -1, -1))
        x = 24.0
        p.setFont(W.mono(11))
        p.setPen(QColor(theme.DIM))
        at = getattr(ev, "at", None)
        try:
            from ..core import history
            stamp = history.when_text(at) if at else ""
        except Exception:  # noqa: BLE001
            stamp = at.strftime("%d %b %H:%M").lstrip("0") if at else ""
        p.drawText(QPointF(x, 16), stamp)
        p.setFont(W.font(13, 600))
        p.setPen(QColor({"note": theme.AMBER, "fail": theme.RED}.get(getattr(ev, "kind", ""), theme.TEXT)))
        p.drawText(QPointF(x, 34), getattr(ev, "what", ""))
        p.setFont(W.font(12))
        p.setPen(QColor(theme.MUTED))
        fm = QFontMetrics(W.font(12))
        p.drawText(QPointF(x, 51), fm.elidedText(self.line2, Qt.TextElideMode.ElideRight, int(self.width() - x - 8)))
        if self.line3:
            p.setPen(QColor(theme.DIM))
            p.drawText(QPointF(x, 69), fm.elidedText(self.line3, Qt.TextElideMode.ElideRight,
                                                     int(self.width() - x - 8)))
        p.end()


class HistoryPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.events: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(16)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search a title or channel")
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(lambda _t: self._show())
        self.count = W.label("", "Small")
        lay.addWidget(W.SectionHeader("History", [self.count, self.search]))
        self.list_box = QWidget()
        self.list_box.setObjectName("Transparent")
        self.items = QVBoxLayout(self.list_box)
        self.items.setContentsMargins(0, 4, 0, 0)
        self.items.setSpacing(0)
        lay.addWidget(self.list_box)
        self.empty = W.label("Nothing yet.", "Dim")
        lay.addWidget(self.empty)
        lay.addStretch(1)

    def set_projects(self, projects: list[dict]) -> None:
        """Every project's project.json data (the board's cards bring them)."""
        try:
            from ..core import history
            self.events = history.events(projects)
        except Exception:  # noqa: BLE001 - nothing to show
            self.events = []
        self._show()

    def _show(self) -> None:
        q = self.search.text().strip()
        evs = self.events
        if q:
            try:
                from ..core import history
                evs = history.search(evs, q)
            except Exception:  # noqa: BLE001
                evs = [e for e in evs if q.lower() in f"{e.title} {e.channel} {e.what} {e.name}".lower()]
        while self.items.count():
            it = self.items.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for ev in evs[:SHOW_MAX]:
            self.items.addWidget(_Item(ev, self))
        self.empty.setVisible(not evs)
        self.count.setText(f"{len(evs)} event(s)" if evs else "")


    def try_another_raw(self, ev, what: str = "raw") -> None:
        """Day 16: "Try with another RAW" on a failed edit: New video with that project's title, headline, channel
        and competitor (its project.json read off the window's thread). Day 17: ``what`` "competitor": the RAW kept
        instead of the competitor."""
        from ..core import another_raw as AR
        from ..ui import background as BG
        dl = (getattr(self.ctx, "pages", None) or {}).get("download")
        fill = getattr(dl, "prefill_another_comp" if what == "competitor" else "prefill_another_raw", None)
        if dl is None or fill is None:
            return

        def got(k) -> None:
            sel = getattr(self.ctx, "select", None)
            if sel is not None:
                sel("download")
            fill(k)
        if not getattr(ev, "folder", "") and getattr(ev, "extra", ""):      # run 18: a failure with no project
            got(_kept_of_logged(ev))
            return
        BG.call(lambda: AR.kept_from_project(ev.folder), got, lambda _e: None)

    def try_again(self, ev) -> str:
        """Run 18: "Try again" on any failure: the New video page tries the step again (a project's row -- made from
        its project.json when it has none there; a failure with no project: the same video added again)."""
        import json
        dl = (getattr(self.ctx, "pages", None) or {}).get("download")
        if dl is None:
            return ""
        sel = getattr(self.ctx, "select", None)
        if sel is not None:
            sel("download")
        if getattr(ev, "folder", ""):
            said = dl.retry_project(ev.folder, getattr(ev, "step", "") or "")
        else:
            try:
                said = dl.retry_logged(json.loads(getattr(ev, "extra", "") or "{}"))
            except ValueError:
                said = "nothing to try again"
        toast = getattr(dl, "toast", None)
        if toast is not None and said:
            toast(f"Try again: {said}", "ok" if said.startswith(("trying", "added")) else "note")
        return said


def _kept_of_logged(ev):
    """Run 18: what a logged failure (no project folder) keeps, for the two "Try with another ..." buttons."""
    import json

    from ..core import another_raw as AR
    try:
        d = json.loads(getattr(ev, "extra", "") or "{}")
    except ValueError:
        d = {}
    from pathlib import Path
    raw, comp = str(d.get("raw") or ""), str(d.get("competitor") or "")
    rf = Path(raw) if raw and Path(raw).is_file() else None
    cf = Path(comp) if comp and Path(comp).is_file() else None
    try:
        speed = float(d.get("speed") or 100.0)
    except (TypeError, ValueError):
        speed = 100.0
    return AR.Kept(title=str(d.get("headline") or ""), upload_title=str(d.get("upload_title") or ""),
                   channel=str(d.get("channel") or ""), competitor_file=cf, competitor_url="" if cf else comp,
                   mode=str(d.get("mode") or ""), speed=speed, why="FAIL", raw_file=rf,
                   raw_url="" if rf or raw.startswith("magnet:") or raw.lower().endswith(".torrent") else raw,
                   raw_torrent=raw if (raw.startswith("magnet:") or raw.lower().endswith(".torrent")) else "",
                   raw_name=Path(raw).name if raw else "", comp_name=Path(comp).name if comp else "")


class HistoryTool(Tool):
    key = "history"
    title = "History"

    def page(self, ctx) -> QWidget:
        return HistoryPage(ctx)
