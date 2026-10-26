"""The edit's sound on its row (run 19, task 2).

1_edit.xml brings no extra Volume any more: each A1 clip comes into Premiere at the RAW's own level (Volume 0 dB) and
gets its Clip Gain, the Hard Limiter (2 dB) and Amplify (+3 dB) when the app's save step writes the project (Open in
Premiere -> Ctrl+S -> the app closes Premiere, writes, opens it again: core.premiere.song_watch). Until then -- or when
it can't (another project open, a message in Premiere ...) -- the row says so plainly, under its buttons: amber "the
sound isn't set yet: press Ctrl+S in Premiere" with why, green "the sound is set: ..." once done. Nothing shows for an
edit the app did not open in Premiere since it started."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel

from . import theme
from . import widgets as W

POLL_MS = 2000


class SoundLine(QLabel):
    """One line under a row: core.premiere.sound_note for the row's 1_edit.xml, looked at every POLL_MS."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SoundLine")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setFont(W.mono(11))
        self._xml: Path | None = None
        self._last: tuple | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self.hide()

    def set_xml(self, xml: str | Path | None) -> None:
        """The row's 1_edit.xml (None: nothing to show)."""
        self._xml = Path(xml) if xml else None
        if self._xml is None:
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        try:
            from ..core import premiere
            note = premiere.sound_note(self._xml) if self._xml is not None else None
        except Exception:  # noqa: BLE001 - a line is a help
            note = None
        key = (note or {}).get("state"), (note or {}).get("text")
        if key == self._last:
            return
        self._last = key
        if not note or not note.get("text"):
            self.hide()
            return
        from ..core.premiere import SET
        done = note.get("state") == SET
        self.setText(("✓ " if done else "") + str(note["text"]))
        self.setStyleSheet(f"color: {theme.GREEN if done else theme.AMBER}; background: transparent;")
        self.show()
