"""Day 12, helper "extra": the small UI pieces of today that had no test of their own -- the titles block's "Use as
YouTube title" and its plain words, the list's glide back to the top, a Learn column's count and a card that folds
away, the torrent card's health bars (real widgets, Qt's offscreen platform)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.ui import motion as Mo  # noqa: E402


@pytest.fixture()
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    Mo.set_enabled(True)


def _pump(app, ms):
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.005)


def test_titles_block_use_as_title_gives_one_line_and_says_plainly_what_it_could_not_read(qapp):
    from mr_app.ui import titles_block as TB
    b = TB.TitlesBlock()
    got = []
    b.use_title.connect(got.append)
    b.use_headline.connect(lambda t: got.append(("headline", t)))
    assert b.post_text.text() == TB.READING and b.head_text.text() == TB.READING
    b.use_as_title()
    b.use_as_headline()
    assert got == []                                                      # nothing read yet: nothing used
    assert not b.b_use.isEnabled() and not b.b_title.isEnabled()
    b.set("", "")
    assert b.post_text.text() == TB.NOTHING_POST and b.head_text.text() == TB.NOTHING_VIDEO
    assert [enabled for *_x, enabled in b.menu_items()] == [False, False]
    b.set("When the joke lands 😭", "He did NOT just say\n  that 😭")
    assert b.head_text.text() == "He did NOT just say / that 😭"
    b.use_as_title()
    b.use_as_headline()
    assert got == ["He did NOT just say that 😭", ("headline", "He did NOT just say\n  that 😭")]
    assert [enabled for *_x, enabled in b.menu_items()] == [True, True]


def test_titles_block_helpers_know_day12_projects_and_just_cuts():
    from mr_app.ui import titles_block as TB
    assert not TB.has_titles(None) and not TB.has_titles({}) and TB.has_titles({"post_title": ""})
    assert TB.is_just_cuts({"kind": "just_cuts"}) and TB.is_just_cuts({"steps": {"edit": {"kind": "just_cuts"}}})
    assert not TB.is_just_cuts({"kind": "full"}) and not TB.is_just_cuts(None)


def _scrolled(qapp):
    sa = QtWidgets.QScrollArea()
    sa.setWidgetResizable(True)
    inner = QtWidgets.QWidget()
    inner.setMinimumHeight(3000)
    lay = QtWidgets.QVBoxLayout(inner)
    child = QtWidgets.QLabel("row")
    lay.addWidget(child)
    sa.setWidget(inner)
    sa.resize(300, 300)
    sa.show()
    qapp.processEvents()
    sa.verticalScrollBar().setValue(1500)
    return sa, child


def test_scroll_to_top_at_once_without_animations_and_gliding_with_them(qapp):
    from mr_app.ui import glide_list as GL
    sa, child = _scrolled(qapp)
    assert GL.scroll_area_of(child) is sa and GL.scroll_area_of(QtWidgets.QWidget()) is None
    GL.scroll_to_top(QtWidgets.QWidget())                                 # not in a scroll area: nothing happens
    Mo.set_enabled(False)
    GL.scroll_to_top(child)
    assert sa.verticalScrollBar().value() == 0
    Mo.set_enabled(True)
    sa.verticalScrollBar().setValue(1500)
    GL.scroll_to_top(child, ms=200)
    _pump(qapp, 60)
    mid = sa.verticalScrollBar().value()
    assert 0 < mid < 1500                                                 # on its way: a glide, not a jump
    _pump(qapp, 400)
    assert sa.verticalScrollBar().value() == 0
    GL.scroll_to_top(child)                                               # at the top already: nothing to do
    sa.close()


def test_a_learn_column_counts_its_cards_and_a_closed_card_folds_then_goes(qapp):
    from mr_app.tools import learn_board as LB
    Mo.set_enabled(False)
    col = LB.LCol("Waiting for your fix", "Nothing waiting")
    col.resize(320, 600)
    col.show()
    col.set_count(0)
    assert col.badge.isHidden()
    pressed = []
    col.show_cards([{"key": "a", "title": "Card A", "lines": ["one"], "buttons": [("Skip", lambda: pressed.append(1))]},
                    {"key": "b", "title": "Card B"}])
    assert col.count() == 2 and col.badge.text() == "2" and not col.badge.isHidden()
    card = col.cards["a"]
    card.buttons["Skip"].click()
    assert pressed == [1]
    col.remove("a")
    assert card.closing and not card.buttons["Skip"].isEnabled() and col.count() == 1 and col.badge.text() == "1"
    col.remove("b")
    assert col.count() == 0 and col.badge.isHidden()
    _pump(qapp, 50)
    assert not col.empty.isHidden()                       # the last card folded away: the column says it is empty
    col.close()


def test_closing_a_learn_card_twice_folds_it_once(qapp):
    from mr_app.tools import learn_board as LB
    Mo.set_enabled(False)
    card = LB.LCard("k", "A card", buttons=[("Skip", lambda: None)])
    card.open()
    gone = []
    card.close_card(lambda: gone.append(1))
    card.close_card(lambda: gone.append(2))                               # a second close does nothing
    _pump(qapp, 50)
    assert gone == [1] and card.maximumHeight() == 0


def test_health_bars_light_zero_to_five(qapp):
    from mr_app.ui import torrent_panel as TP
    h = TP.HealthBars()
    for n, want in ((-3, 0), (2, 2), (9, 5), ("4", 4)):
        h.set(n)
        assert h.value == want
    h.grab()                                                              # paints without an error
