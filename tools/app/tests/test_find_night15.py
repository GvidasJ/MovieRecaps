"""Night 15, task 11: search -- the torrent's file list (a live filter, episodes written any usual way, "Tick only
these", "12 of 24") and Ctrl+F on every page (Esc clears it)."""
from __future__ import annotations

import pytest

from mr_app.core import find as F

EPISODES = ["Show.S09E04.1080p.mkv", "Show.S09E05.1080p.mkv", "Show.S01E05.mkv", "show 9x06.mkv",
            "Show - Episode 7.mp4", "Show.S09E05.sample.mkv", "Show.S09E15.mkv", "Extras.nfo"]


@pytest.mark.parametrize("query, want", [
    ("S09E05", ["Show.S09E05.1080p.mkv", "Show.S09E05.sample.mkv"]),
    ("s9e5", ["Show.S09E05.1080p.mkv", "Show.S09E05.sample.mkv"]),
    ("9x05", ["Show.S09E05.1080p.mkv", "Show.S09E05.sample.mkv"]),
    ("9x06", ["show 9x06.mkv"]),
    ("ep 5", ["Show.S09E05.1080p.mkv", "Show.S01E05.mkv", "Show.S09E05.sample.mkv"]),   # any season
    ("episode 7", ["Show - Episode 7.mp4"]),
    ("e15", ["Show.S09E15.mkv"]),
    ("sample s09", ["Show.S09E05.sample.mkv"]),                                          # words, any order
    ("NFO", ["Extras.nfo"]),
    ("", EPISODES),
])
def test_what_a_search_finds(query, want):
    assert [n for n in EPISODES if F.matches(query, n)] == want


def test_the_count_line_and_plain_text():
    assert F.count_line(12, 24) == "12 of 24" and F.count_line(24, 24) == "24 files" and F.count_line(1, 1) == "1 file"
    assert F.plain_text("<b>Video</b>&nbsp;done &amp; <span style='x'>ok</span>").split() == ["Video", "done", "&", "ok"]
    assert F.episode_query("S09E05") == (9, 5) and F.episode_query("ep 5") == (None, 5)
    assert F.episode_query("dad school") is None and F.episode_of("Family Guy - S11E01 - Into Fat Air") == (11, 1)


@pytest.fixture()
def qt():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.ui import motion as Mo
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    yield app
    Mo.set_enabled(True)


def test_the_torrent_list_filters_ticks_only_these_and_counts(qt, tmp_path):
    import test_torrent as TT
    from mr_app.ui import torrent_panel as TP
    files = {f"Show/{n}": 1000 + k for k, n in enumerate(EPISODES)}
    p = tmp_path / "x.torrent"
    p.write_bytes(TT._folder_torrent(tmp_path, files))                     # noqa: SLF001
    eng, clock = TT.FakeAria2(), TT.Clock()
    from mr_app.core import settings as S
    from mr_app.core import torrent as T
    sess = T.TorrentSession(tmp_path / "state", engine=eng, settings=S.Settings(), clock=clock,
                            sleep=lambda _s: None, free=lambda d: 500 * TT.GB, scrape=lambda ih, tr: 3)
    w = TP.TorrentPicker(str(p), tmp_path / "dl", sess=sess, run=TT._sync)  # noqa: SLF001
    assert w.find_box.isVisibleTo(w) and w.find_count.text() == "8 files"
    w.find_box.setText("S09E05")
    vis = [r.file.path for r in w.rows if r.isVisibleTo(w)]
    assert sorted(x.split("/")[-1] for x in vis) == ["Show.S09E05.1080p.mkv", "Show.S09E05.sample.mkv"]
    assert w.find_count.text() == "2 of 8" and w.only_btn.isEnabled()
    w.only_btn.click()
    assert sorted(r.file.path.split("/")[-1] for r in w.rows if r.checked()) == ["Show.S09E05.1080p.mkv",
                                                                               "Show.S09E05.sample.mkv"]
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    QApplication.sendEvent(w.find_box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert w.find_box.text() == "" and w.find_count.text() == "8 files"
    assert len([r for r in w.rows if r.checked()]) == 2               # the ticks stay as chosen
    w.cancel()


def test_ctrl_f_finds_on_the_page_and_esc_clears(qt):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
    from mr_app.ui.find_bar import FindBar, find_in
    page = QWidget()
    lay = QVBoxLayout(page)
    for t in ("American Dad -- running", "<b>Family Guy</b> S11E01 done", "Blue Mountain State"):
        lay.addWidget(QLabel(t))
    lay.addWidget(QPushButton("Open the Family Guy folder"))
    hidden = QLabel("Family Guy hidden")
    lay.addWidget(hidden)
    hidden.hide()
    page.show()
    assert [w.text() for w in find_in(page, "family guy")] == ["<b>Family Guy</b> S11E01 done",
                                                              "Open the Family Guy folder"]
    assert len(find_in(page, "S11E01")) == 1 and find_in(page, "") == []
    bar = FindBar(lambda: page)
    bar.open_bar()
    bar.box.setText("family")
    assert bar.count.text() == "1 of 2"
    QApplication.sendEvent(bar.box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert bar.count.text() == "2 of 2"
    bar.box.setText("nothing like this")
    assert bar.count.text() == "none"
    QApplication.sendEvent(bar.box, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert bar.box.text() == "" and not bar.isVisible()
    page.deleteLater()


def test_ctrl_f_on_the_real_window_searches_the_page_shown(qt):
    from mr_app.tools import tools
    from mr_app.ui import main_window as MW
    win = MW.MainWindow(tools())
    try:
        win.show()
        win.select("match_cuts")
        win._key_find()                                                    # noqa: SLF001 - Ctrl+F
        assert win.find_bar.isVisible() and win.find_bar.box.hasFocus() or win.find_bar.isVisible()
        win.find_bar.box.setText("drop videos")
        assert win.find_bar.hits and win.find_bar.count.text().endswith(f"of {len(win.find_bar.hits)}")
        win.select("history")
        win.find_bar.box.setText("drop videos")                            # another page: its own words
        assert all(win.pages["history"].isAncestorOf(w) for w in win.find_bar.hits)
        win.find_bar.close_bar()
        assert not win.find_bar.isVisible()
    finally:
        win.close()
