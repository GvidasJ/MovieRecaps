"""Day 12's new widgets keep night 11's motion rules (checked on the real widgets, offscreen):

* a hover only fades -- in about 0.3 s, out about 0.48 s -- and nothing moves or changes size: the torrent picker's
  file rows, the Learn columns' cards, the "Automatic" switch, the titles block's buttons, the Match cuts pair line's
  Swap and the amber check card's pick buttons;
* Settings' "Animations off" makes them instant: the hovers, a Learn card opening, the switch's knob, a GlideList
  reorder and its scroll to the top;
* the Download row's "Open folder" and 1_edit.xml look never touch the disk on the window's thread.

Run: python -m pytest tools/app/tests/test_motion_day12.py"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402

from mr_app.core import torrent as T  # noqa: E402
from mr_app.tools import learn_board as LB  # noqa: E402
from mr_app.ui import motion as M  # noqa: E402
from mr_app.ui import widgets as W  # noqa: E402
from mr_app.ui.glide_list import GlideList  # noqa: E402
from mr_app.ui.titles_block import TitlesBlock  # noqa: E402
from mr_app.ui.torrent_panel import FileRow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def animations_on():
    M.set_enabled(True)
    yield
    M.set_enabled(True)


def pump(app, ms: float) -> None:
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000.0 < ms:
        app.processEvents()
        time.sleep(0.003)


def enter(w) -> None:
    QtWidgets.QApplication.sendEvent(w, QEnterEvent(QPointF(3, 3), QPointF(3, 3), QPointF(3, 3)))


def leave(w) -> None:
    QtWidgets.QApplication.sendEvent(w, QEvent(QEvent.Type.Leave))


def _widgets():
    """The new hoverable widgets, each with its hover Fader."""
    from mr_app.tools.download_tool import AutoSwitch
    row = FileRow(T.TFile(0, "Movie.2024.1080p.mkv", 4_000_000_000), True, True)
    card = LB.LCard("k", "video14 Meet the Blacks", ["fixed .prproj waiting"], [("Open", lambda: None)],
                    channel="LouLaughs", warn=True)
    card.reveal.set(1.0)
    plain = LB.LCard("p", "A problem", ["FAIL: XML PERSON"], [("Open log", lambda: None)])
    plain.reveal.set(1.0)
    auto = AutoSwitch(False)
    tb = TitlesBlock("The moment he realised", "The fact that prison changed\nhim this much")
    swap = W.Button("⇄ Swap", "Mini")
    pick = W.Button("raw.mp4 is the RAW")
    return [row, card, plain, auto, tb.copy_post, tb.b_use, tb.b_title, swap, pick], tb


def test_new_widgets_hover_only_fades_in_0_3_s_out_0_48_s_and_nothing_moves(qapp):
    ws, tb = _widgets()
    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    for w in ws[:4] + [tb, ws[7], ws[8]]:
        lay.addWidget(w)
    box.resize(700, 900)
    box.show()
    pump(qapp, 60)
    before = [w.geometry() for w in ws]
    for w in ws:
        enter(w)
    pump(qapp, 110)
    mid = [w.hover.value for w in ws]
    assert all(0.05 < v < 0.98 for v in mid), mid           # fading, not switched
    pump(qapp, 330)
    assert [w.hover.value for w in ws] == [pytest.approx(1.0)] * len(ws)
    assert [w.geometry() for w in ws] == before
    for w in ws:
        leave(w)
    pump(qapp, 30)
    for w in ws:                                             # Button checks the mouse a moment after leaving
        if isinstance(w, W.Button):
            w.hover.to(0.0)
            w.light.to(0.0)
    pump(qapp, 270)
    out = [w.hover.value for w in ws]
    assert all(0.0 < v < 0.6 for v in out), out              # the way out is slower (0.48 s)
    pump(qapp, 350)
    assert [w.hover.value for w in ws] == [pytest.approx(0.0)] * len(ws)
    assert [w.geometry() for w in ws] == before
    box.close()


def test_animations_off_makes_the_new_hovers_instant(qapp):
    M.set_enabled(False)
    ws, _tb = _widgets()
    for w in ws:
        enter(w)
    assert [w.hover.value for w in ws] == [1.0] * len(ws)
    for w in ws:
        leave(w)
        if isinstance(w, W.Button):
            w.hover.to(0.0)
    assert [w.hover.value for w in ws] == [0.0] * len(ws)


def test_animations_off_a_learn_card_opens_and_closes_at_once(qapp):
    M.set_enabled(False)
    c = LB.LCard("k", "title", ["a line"])
    c.resize(260, 80)
    c.open()
    assert c.reveal.value == 1.0
    assert c.maximumHeight() == LB.NO_MAX
    assert c.graphicsEffect().opacity() == pytest.approx(1.0)
    done = []
    c.close_card(lambda: done.append(1))
    assert c.reveal.value == 0.0 and done == [1]


def test_a_learn_card_opens_by_fading_not_jumping(qapp):
    c = LB.LCard("k", "title", ["a line"])
    c.resize(260, 80)
    c.open()
    pump(qapp, 120)
    assert 0.05 < c.reveal.value < 0.98
    pump(qapp, 300)
    assert c.reveal.value == pytest.approx(1.0)


def test_animations_off_the_automatic_switch_knob_moves_at_once(qapp):
    from mr_app.tools.download_tool import AutoSwitch
    a = AutoSwitch(False)
    a.setChecked(True)
    pump(qapp, 80)
    assert 0.0 < a.knob.value < 1.0                          # on: it glides
    M.set_enabled(False)
    b = AutoSwitch(False)
    b.setChecked(True)
    assert b.knob.value == 1.0                               # off: at once


class _Row(QtWidgets.QWidget):
    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(100, 40)


def test_animations_off_a_glide_list_reorders_at_once(qapp):
    M.set_enabled(False)
    lst = GlideList(spacing=10)
    ws = []
    for _ in range(3):
        w = _Row()
        ws.append(w)
        lst.append(w)
    lst.resize(300, 200)
    lst.show()
    pump(qapp, 20)
    lst.set_order([ws[2], ws[0], ws[1]])
    assert not lst.gliding()
    assert [w.y() for w in (ws[2], ws[0], ws[1])] == [0, 50, 100]
    lst.close()


def test_with_animations_on_a_glide_list_glides(qapp):
    lst = GlideList(spacing=10)
    ws = []
    for _ in range(3):
        w = _Row()
        ws.append(w)
        lst.append(w)
    lst.resize(300, 200)
    lst.show()
    pump(qapp, 20)
    lst.set_order([ws[2], ws[0], ws[1]])
    assert lst.gliding()
    pump(qapp, 120)
    assert 0 < ws[2].y() < 100                               # part-way: it glides, it does not jump
    pump(qapp, 500)
    assert [w.y() for w in (ws[2], ws[0], ws[1])] == [0, 50, 100]
    lst.close()


def test_download_row_disk_looks_are_off_the_window_thread(qapp, monkeypatch, tmp_path):
    """xml_of never stats on the window's thread (the row's refresh asks often; the run folder can be on the network):
    it asks off_thread once, remembers the answer and refreshes the row."""
    from types import SimpleNamespace

    from mr_app.tools import download_tool as D
    pending = []
    monkeypatch.setattr(D, "off_thread", lambda fn, done, failed=None: pending.append((fn, done)))
    run = tmp_path / "run"
    run.mkdir()
    (run / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    refreshed = []
    page = SimpleNamespace(_refresh=lambda it: refreshed.append(it))
    page._xml_path = lambda it: D.DownloadPage._xml_path(page, it)
    it = SimpleNamespace(result={"run_dir": str(run)}, job=None)
    assert D.DownloadPage.xml_of(page, it) is None           # not known yet: asked in the background
    assert len(pending) == 1
    assert D.DownloadPage.xml_of(page, it) is None           # asked once only
    assert len(pending) == 1
    fn, done = pending.pop()
    done(fn())
    assert refreshed == [it]
    assert D.DownloadPage.xml_of(page, it) == run / "1_edit.xml"
    assert pending == []
