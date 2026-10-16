"""A tab for a tool that comes later (the room for the downloader and the converter)."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import Tool
from ..ui import widgets as W


class ComingSoon(Tool):
    ready = False

    def __init__(self, key: str, title: str, what: str):
        self.key, self.title, self.what = key, title, what

    def page(self, ctx) -> QWidget:
        w = QWidget()
        w.setObjectName("Page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 40, 0, 40)
        t = W.label(self.title, "Title")
        lay.addWidget(t)
        lay.addWidget(W.label(self.what, "Lead", wrap=True))
        lay.addWidget(W.tag("coming soon", red=True))
        lay.addStretch(1)
        return w
