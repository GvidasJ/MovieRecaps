"""The Board tab on the real widgets (night 11, item 6): every project in its column with the date of each step, a
card dropped on a column moves there (the slot opens first), a pinned card stays on top, Clean up asks first and
never offers the edit or the project's own files, and a near-match Short asks "Is this the same video?".

Run: python -m pytest tools/app/tests/test_board_ui.py"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QByteArray, QMimeData, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402

from mr_app.core import board as B  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.tools import board_tool as BT  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def pump_until(app, cond, timeout_s: float = 8.0) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(cond())


@pytest.fixture
def setup(qapp, tmp_path, monkeypatch):
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    toasts: list = []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    pages: list = []

    def make_page():
        page = BT.BoardPage(ctx)
        page._up_timer.stop()                                   # noqa: SLF001 - no Shorts check in tests
        page.check_uploads = lambda force=False: None
        page.resize(1400, 900)
        page.show()
        pages.append(page)
        return page
    yield SimpleNamespace(local=local, shared=shared, toasts=toasts, make_page=make_page, app=qapp)
    for p in pages:
        p.close()
        p.deleteLater()
    qapp.processEvents()


def project(root: Path, name: str, steps=("download",), channel: str = "LouLaughs", title: str = "") -> Path:
    p = PJ.create(root, name=name)
    for s in steps:
        p.set_step(s, PJ.DONE)
    if channel or title:
        p.update(channel=channel, title=title)
    return p.folder


def names(page, key: str) -> list[str]:
    return [w.card.name for w in getattr(page.columns[key], "widgets", [])]


def test_every_project_is_in_its_column_with_the_date_of_each_step(setup):
    a = project(setup.local, "clip one", ("download",))
    b = project(setup.local, "clip two", ("download", "convert"), title="When she finally got the part 💀😭")
    c = project(setup.local, "clip three", ())
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(page.cards) == 3)
    assert names(page, "link") == [c.name]
    assert names(page, "downloaded") == [a.name]
    assert names(page, "converted") == [b.name]
    w = page.columns["converted"].widgets[0]
    texts = [lab.text() for lab in w.findChildren(QtWidgets.QLabel)]
    assert any("Downloaded" in t and "Converted" in t for t in texts)                 # every step's date
    assert any("When she finally got the part" in t for t in texts)                    # and the title
    assert page.columns["converted"].n.text() == "1"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")        # (PySide marks QDragEnterEvent(QPoint ...) old)
def test_a_card_dropped_on_a_column_opens_a_slot_and_moves_there(setup):
    a = project(setup.local, "clip one", ("download",))
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(page.cards) == 1)
    col = page.columns["rendered"]
    mime = QMimeData()
    mime.setData(BT.CARD_MIME, QByteArray(str(a).encode("utf-8")))
    enter = QDragEnterEvent(QPoint(20, 40), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(col, enter)
    assert enter.isAccepted() and col.spacer.isVisible()
    assert pump_until(setup.app, lambda: col.spacer.height() > 30, 2.0)             # the slot grows (it glides)
    drop = QDropEvent(QPointF(20, 40), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(col, drop)
    assert pump_until(setup.app, lambda: names(page, "rendered") == [a.name])
    assert B.card_of(a).column == "rendered" and "rendered" in B.card_of(a).dates
    assert ("Moved to Rendered ✓", "ok") in setup.toasts
    assert pump_until(setup.app, lambda: not col.spacer.isVisible(), 2.0)            # the slot closes again


def test_a_pinned_card_stays_at_the_top_of_its_column(setup):
    old = project(setup.local, "older clip", ("download",))
    new = project(setup.local, "newer clip", ("download",))
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(page.cards) == 2)
    first = names(page, "downloaded")[0]
    other = old if first == new.name else new
    card = next(c for c in page.cards if c.folder == other)
    page.pin(card)
    assert pump_until(setup.app, lambda: names(page, "downloaded")[:1] == [other.name])
    assert B.card_of(other).pinned


def test_clean_up_asks_first_shows_the_size_and_never_offers_the_edit_or_the_project_files(setup, monkeypatch):
    a = project(setup.local, "clip one", ("download",))
    (a / "edit").mkdir()
    (a / "edit" / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    (a / "edit" / "stopped.part").write_bytes(b"e" * 10)                   # inside the edit: never offered
    (a / "template.png").write_bytes(b"png")
    (a / "title.txt").write_text("a title", encoding="utf-8")
    junk = a / "video.mp4.part"
    junk.write_bytes(b"j" * 2048)
    old = time.time() - 3 * 3600
    for f in (junk, a / "edit" / "stopped.part"):
        os.utime(f, (old, old))
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(page.cards) == 1)
    asked: list = []
    answer = [QtWidgets.QMessageBox.StandardButton.No]
    monkeypatch.setattr(BT.QMessageBox, "question", lambda _w, _t, text, *a, **k: (asked.append(text), answer[0])[1])
    page.clean_up()
    assert pump_until(setup.app, lambda: asked and page.b_clean.isEnabled())
    text = asked[0]
    assert "video.mp4.part" in text and "2.0 KB" in text.replace("2 KB", "2.0 KB")
    assert "stopped.part" not in text and "1_edit.xml" not in text
    assert "Never touched" in text
    assert junk.is_file()                                                      # No: nothing removed
    answer[0] = QtWidgets.QMessageBox.StandardButton.Yes
    page.clean_up()
    assert pump_until(setup.app, lambda: not junk.exists())
    for keep in ("edit/1_edit.xml", "edit/stopped.part", "template.png", "title.txt", "project.json"):
        assert (a / keep).is_file(), keep


def test_a_near_match_asks_once_and_yes_moves_the_card_to_uploaded_with_the_shorts_date(setup):
    a = project(setup.local, "clip one", ("download", "convert"), title="The commercial behind the meme 🍫")
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(page.cards) == 1)

    class Near(tuple):
        pass
    short = SimpleNamespace(id="abc123", url="https://www.youtube.com/shorts/abc123", channel="LouLaughs",
                            title="The Commercial Behind The Nestle Crunch Meme 🍫😭")
    item = Near((str(a), short, 0.81))
    item.at = datetime(2026, 10, 18, 2, 34)
    item.why = "the same 🍫, uploaded after the edit"
    page._uploads(SimpleNamespace(exact=[], near=[item], offline=False))            # noqa: SLF001
    asks = [page.asks.itemAt(i).widget() for i in range(page.asks.count())]
    assert len(asks) == 1 and isinstance(asks[0], BT.AskUpload)
    texts = " ".join(lab.text() for lab in asks[0].findChildren(QtWidgets.QLabel))
    assert "Is this the same video?" in texts and "81 % alike" in texts and "Why I think so" in texts
    page._uploads(SimpleNamespace(exact=[], near=[item], offline=False))            # noqa: SLF001
    assert page.asks.count() == 1                                                  # asked once
    yes = [b for b in asks[0].findChildren(QtWidgets.QPushButton) if b.text().startswith("Yes")][0]
    yes.click()
    assert pump_until(setup.app, lambda: B.card_of(a).column == "uploaded")
    card = B.card_of(a)
    assert card.dates["uploaded"].replace(tzinfo=None) == datetime(2026, 10, 18, 2, 34)    # (kept with its offset)
    assert card.uploaded and card.uploaded.get("id") == "abc123" and card.uploaded.get("how") == "you"
    assert pump_until(setup.app, lambda: names(page, "uploaded") == [a.name])


def test_no_answers_no_and_that_short_is_never_asked_again(setup):
    a = project(setup.local, "clip one", ("download",))
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(page.cards) == 1)
    short = SimpleNamespace(id="zzz", url="", channel="Flick707", title="Something else")
    page._uploads(SimpleNamespace(exact=[], near=[(str(a), short, 0.75)], offline=False))   # noqa: SLF001
    ask = page.asks.itemAt(0).widget()
    no = [b for b in ask.findChildren(QtWidgets.QPushButton) if b.text() == "No"][0]
    no.click()
    assert pump_until(setup.app, lambda: "zzz" in B.card_of(a).not_same)
    assert B.card_of(a).column == "downloaded"


def test_offline_says_so_quietly(setup):
    page = setup.make_page()
    page._uploads(SimpleNamespace(offline=True))                                    # noqa: SLF001
    assert "no internet" in page.note.text()
