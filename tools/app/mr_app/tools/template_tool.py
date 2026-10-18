"""The Template tab (night 10): a channel's template with your headline, live -- pick the channel, type the title,
see it; drop the competitor's video to read its headline and check your title against it; save the PNG (1080x1920,
the transparent video window kept). The same editor opens from a video's row (Match cuts, Download), and the edit
then uses the template as its frame with the channel's watermark on top.

A new channel is a new folder in assets\\channels\\<Channel>\\ (template.psd + watermark.png): no code change."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from . import Tool
from ..core import paths
from ..ui import widgets as W
from ..ui.template_editor import TemplateEditor


class TemplatePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 22, 0, 40)
        lay.setSpacing(16)
        lay.addWidget(W.SectionHeader("Channel template"))
        lay.addWidget(W.label("Your channel's template with your headline, in the exact style of its Photoshop file "
                              "(NeueMontreal Bold 122 pt, Faux Bold, 2 lines, emojis). It replaces the frame in the "
                              "edit (V2), with the channel's watermark on top (Overlay, 75 %).", "Muted", wrap=True))
        card = W.Card(hover=False)
        self.ed = TemplateEditor(drop_competitor=True)
        card.lay.addWidget(self.ed)
        row = QHBoxLayout()
        self.save = W.Button("Save the PNG...", "Primary")
        self.save.clicked.connect(self._save)
        row.addWidget(self.save)
        self.msg = W.label("", "Muted", wrap=True, select=True)
        row.addWidget(self.msg, 1)
        card.lay.addLayout(row)
        lay.addWidget(card)
        lay.addSpacing(8)
        lay.addWidget(W.SectionHeader("Channels", [W.tag("assets\\channels")]))
        self.channels = W.label("", "Muted", wrap=True, select=True)
        lay.addWidget(self.channels)
        lay.addStretch(1)
        self._list_channels()

    def _list_channels(self) -> None:
        try:
            from ..core import channels as Ch
            rows = [f"{c.name}  ·  {getattr(c, 'link', '') or ''}" + ("  ·  Shorts only" if getattr(c, "shorts_only",
                                                                                                    False) else "")
                    for c in Ch.channels()]
        except Exception as e:  # noqa: BLE001
            rows = [f"could not read the channels: {e}"]
        rows.append(f"A new channel: a new folder in {paths.channels_dir()} with its template.psd and watermark.png.")
        self.channels.setText("\n".join(rows))

    def _save(self) -> None:
        ch, title = self.ed.channel(), self.ed.title()
        if not ch:
            self.msg.setText("Choose a channel first (Default is templates\\default.png itself).")
            return
        f, _ = QFileDialog.getSaveFileName(self, "Save the template", str(paths.repo() / "input" /
                                                                          f"{ch}_template.png"), "PNG (*.png)")
        if not f:
            return
        from ..core import channels as Ch
        from ..core import flow
        try:
            res = Ch.make_template(flow.find_channel(ch), title, Path(f))
        except Exception as e:  # noqa: BLE001
            self.msg.setText(f"could not save it: {type(e).__name__}: {e}")
            return
        info = flow.template_info(res)
        self.msg.setText(f"saved {f}" + (f" -- SHRUNK: {info['why']}" if info.get("shrunk") else ""))
        try:
            os.startfile(str(Path(f).parent))                   # noqa: S606
        except OSError:
            pass


class TemplateTool(Tool):
    key = "template"
    title = "Template"

    def page(self, ctx) -> QWidget:
        return TemplatePage(ctx)
