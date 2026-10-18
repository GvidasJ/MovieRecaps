"""Night 11's motion rules, checked on the real widgets (no screen: Qt's offscreen platform):

* a hover only fades -- in about 0.3 s, out about 0.45-0.5 s -- and nothing moves or changes size;
* "Animations off" (Settings) makes every change instant;
* a progress bar glides, even to 100 %, never jumps;
* a page glides in and the real page is back afterwards; the tab's underline grows on the new tab;
* the intro lasts at most 1.3 s and everything in it is centred.

Run: python -m pytest tools/app/tests/test_motion.py"""
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
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QEnterEvent  # noqa: E402

from mr_app.ui import motion as M  # noqa: E402
from mr_app.ui import widgets as W  # noqa: E402


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
    from PySide6.QtCore import QEvent
    QtWidgets.QApplication.sendEvent(w, QEvent(QEvent.Type.Leave))


def test_a_fader_fades_in_in_about_0_3_s_and_out_in_about_0_48_s(qapp):
    host = QtWidgets.QWidget()
    f = M.Fader(host, lambda: None)
    f.to(1.0)
    pump(qapp, 120)
    assert 0.1 < f.value < 0.95                    # part-way: it fades, it does not jump
    pump(qapp, 320)
    assert f.value == pytest.approx(1.0)
    f.to(0.0)
    pump(qapp, 300)
    assert 0.0 < f.value < 0.6                     # the way out is slower (0.48 s)
    pump(qapp, 350)
    assert f.value == pytest.approx(0.0)


def test_the_soft_curve_is_the_references_cubic_bezier(qapp):
    c = M.SOFT
    assert c.valueForProgress(0.0) == pytest.approx(0.0)
    assert c.valueForProgress(1.0) == pytest.approx(1.0)
    assert 0.6 < c.valueForProgress(0.5) < 0.9     # cubic-bezier(.4,0,.2,1) is ~0.77 half-way (fast, then soft)


def test_a_hovered_button_card_and_drop_zone_fade_and_never_move_or_grow(qapp):
    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    b = W.Button("Start", "Primary", kbd="Space")
    c = W.Card()
    d = W.DropZone("Drop videos here", ["a line"])
    for w in (b, c, d):
        lay.addWidget(w)
    box.resize(500, 400)
    box.show()
    pump(qapp, 30)
    before = [w.geometry() for w in (b, c, d)]
    for w in (b, c, d):
        enter(w)
    pump(qapp, 100)
    mid = [w.hover.value for w in (b, c, d)]
    assert all(0.05 < v < 0.98 for v in mid)       # fading, not switched
    pump(qapp, 400)
    assert [w.hover.value for w in (b, c, d)] == [pytest.approx(1.0)] * 3
    assert [w.geometry() for w in (b, c, d)] == before
    for w in (b, c, d):
        leave(w)
    pump(qapp, 650)
    assert [w.geometry() for w in (b, c, d)] == before
    box.close()


def test_animations_off_changes_everything_at_once(qapp):
    M.set_enabled(False)
    b = W.Button("Start")
    enter(b)
    assert b.hover.value == 1.0
    bar = W.GlideBar()
    bar.set(0.7)
    assert bar.shown == pytest.approx(0.7)


def test_the_progress_bar_glides_even_to_100_and_never_jumps(qapp):
    bar = W.GlideBar()
    bar.resize(300, 3)
    bar.jump(0.3)
    bar.set(1.0)
    pump(qapp, 60)
    assert 0.3 < bar.shown < 0.9                   # on its way
    seen = [bar.shown]
    for _ in range(80):                            # the reference's glide: ~1.4 s from 30 % to 100 %
        pump(qapp, 30)
        seen.append(bar.shown)
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)
    assert max(b - a for a, b in zip(seen, seen[1:])) < 0.25


def test_a_status_pill_says_its_word_and_pulses_only_while_running(qapp):
    p = W.Pill("waiting")
    assert p.text() == "WAITING"
    p.set("running")
    assert p.pulse.running()
    p.set("PASS")
    assert not p.pulse.running() and p.text() == "PASS"
    p.set("WRONG PAIR")
    pump(qapp, 600)
    assert p._now()[0] == QColor("#e5484d")                                # noqa: SLF001 - red, faded in


def test_the_page_glides_in_and_the_real_page_is_back_after(qapp):
    from mr_app.ui import tabs as T
    st = T.PageStack()
    a, b = QtWidgets.QLabel("a"), QtWidgets.QLabel("b")
    st.addWidget(a)
    st.addWidget(b)
    st.resize(400, 300)
    st.show()
    pump(qapp, 20)
    st.glide_to(1, from_right=True)
    assert st.currentWidget() is b and b.graphicsEffect() is not None      # the picture glides, the page waits
    pump(qapp, 120)
    assert 0.0 < st.glide.value < 1.0
    pump(qapp, 600)
    assert b.graphicsEffect() is None and st.glide.value == pytest.approx(1.0)
    st.close()


def test_the_tab_underline_grows_on_the_new_tab_and_shrinks_on_the_old(qapp):
    from mr_app.ui import tabs as T
    bar = T.TabBar(["Match cuts", "Board", "Progress", "History"])
    bar.show()
    sizes = [t.size() for t in bar.tabs]
    bar.select(2, emit=True)
    pump(qapp, 150)
    assert 0.0 < bar.tabs[2].sel.value < 1.0 and 0.0 < bar.tabs[0].sel.value < 1.0
    pump(qapp, 500)
    assert bar.tabs[2].sel.value == pytest.approx(1.0) and bar.tabs[0].sel.value == pytest.approx(0.0)
    assert [t.size() for t in bar.tabs] == sizes
    bar.close()


def test_the_intro_is_under_1_3_s_and_centred(qapp):
    from mr_app.ui import chrome
    from mr_app.ui import theme
    assert chrome.INTRO_MS <= 1300
    host = QtWidgets.QWidget()
    host.resize(900, 600)
    host.show()
    intro = chrome.Intro(host)
    intro.play()
    intro._a.stop()                                                        # noqa: SLF001 - hold a frame
    intro.t = 900.0                                                        # everything in, before the fade-out
    img = intro.grab().toImage()
    bg = QColor(theme.BG)
    xs, ys = [], []
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if abs(c.red() - bg.red()) + abs(c.green() - bg.green()) + abs(c.blue() - bg.blue()) > 30:
                xs.append(x)
                ys.append(y)
    assert xs and ys
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    assert abs(cx - img.width() / 2.0) <= 4 and abs(cy - img.height() / 2.0) <= 6
    host.close()
