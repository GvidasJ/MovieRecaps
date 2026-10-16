"""The Match cuts page itself (no screen: Qt's offscreen platform): drops read one after the other, the warning before
the start. Run: python -m pytest tools/app/tests"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.tools import match_cuts_tool as M      # noqa: E402


class _LaterWorker:
    """A Worker that finishes only when the test says so (``done()``): drops that overlap."""
    started: list = []

    def __init__(self, fn):
        self.fn, self._ok, self._bad = fn, [], []
        self.finished_with = SimpleNamespace(connect=self._ok.append)
        self.failed = SimpleNamespace(connect=self._bad.append)

    def start(self):
        _LaterWorker.started.append(self)

    def done(self):
        res = self.fn()
        for f in self._ok:
            f(res)


@pytest.fixture()
def page(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(M, "Worker", _LaterWorker)
    lengths = {"raw.mp4": 1281.0, "comp.mp4": 12.0, "alone.mp4": 30.0, "episode.mp4": 900.0}
    monkeypatch.setattr(M, "seconds_of", lambda p: lengths.get(Path(p).name))
    _LaterWorker.started = []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda title, text: None)
    p = M.MatchCutsPage(ctx)
    yield p
    p.deleteLater()
    app.processEvents()


def _file(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_a_video_dropped_while_the_last_drop_is_still_read_meets_its_partner(page, tmp_path):
    raw = _file(tmp_path / "a" / "episode.mp4")
    comp = _file(tmp_path / "b" / "comp.mp4")
    page.add_paths([raw])                            # the RAW alone: it will wait for its partner ...
    page.add_paths([comp])                           # ... dropped before the first drop is read
    assert len(_LaterWorker.started) == 1            # one drop at a time
    _LaterWorker.started[0].done()
    assert page.waiting == raw and not page.jobs
    assert len(_LaterWorker.started) == 2            # now the second drop, with the RAW that waits
    _LaterWorker.started[1].done()
    assert page.waiting is None and len(page.jobs) == 1
    assert (page.jobs[0].competitor, page.jobs[0].raw) == (comp, raw)


def test_a_pair_then_a_lone_video_the_lone_one_waits(page, tmp_path):
    pair = [_file(tmp_path / "v1" / "raw.mp4"), _file(tmp_path / "v1" / "comp.mp4")]
    alone = _file(tmp_path / "v2" / "alone.mp4")
    page.add_paths(pair)
    page.add_paths([alone])
    for _ in range(2):
        _LaterWorker.started[-1].done()
    assert len(page.jobs) == 1 and page.waiting == alone                 # (the pair's result does not drop it)


def test_the_warning_says_the_competitor_longer_than_the_raw_once(page, tmp_path, monkeypatch):
    shown = []

    class Box:
        class Icon:
            Warning = 0

        class ButtonRole:
            AcceptRole, RejectRole = 0, 1

        def __init__(self, parent):
            self.buttons = []

        def setWindowTitle(self, t):
            pass

        def setIcon(self, i):
            pass

        def setText(self, t):
            pass

        def setInformativeText(self, t):
            shown.append(t)

        def addButton(self, text, role):
            self.buttons.append(text)
            return text

        def exec(self):
            pass

        def clickedButton(self):
            return "Cancel"
    monkeypatch.setattr(M, "QMessageBox", Box)
    job = SimpleNamespace(name="video4", problems=lambda: ["the competitor (0:26) is longer than the RAW (0:12) -- "
                                                           "swapped? (the swap button changes them)"])
    probs = [f"{job.name}: {p}" for p in job.problems()]
    page._checked(probs, {"video4": ["the competitor (0:26) is longer than the RAW (0:12): the RAW must be the full "
                                     "episode", "competitor comp.mp4 (0:26), RAW raw.mp4 (0:12)"]})
    text = shown[0]
    assert text.count("longer than the RAW") == 1 and "swapped?" in text and "competitor comp.mp4 (0:26)" in text
    assert not page.running
