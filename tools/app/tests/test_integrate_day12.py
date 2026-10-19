"""Day 12, the integration helper: the pieces built in parallel joined so the spec holds everywhere.

* Section 7: the competitor's two real titles ("Title from the post", "Headline on the video" with Copy, Use as my
  headline, Use as YouTube title) on the Match cuts queue rows and the board cards too (compact there), and "Just
  cuts" on both; "Use as my headline" sets the project's headline before the edit, waits while it runs, and asks
  before Change headline once an edit is made.

Run: python -m pytest tools/app/tests/test_integrate_day12.py"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import flow  # noqa: E402
from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.ui import motion as Mo  # noqa: E402


def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump_until(cond, timeout_s: float = 8.0) -> bool:
    a = app()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        a.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    a.processEvents()
    return bool(cond())


def project(root: Path, name: str = "clip", **fields) -> PJ.Project:
    p = PJ.create(root, name=name)
    (p.folder / "competitor.mp4").write_bytes(b"c")
    (p.folder / "raw.mp4").write_bytes(b"r")
    p.update(competitor={"file": "competitor.mp4", "url": "https://www.tiktok.com/@x/video/1"},
             raw={"file": "raw.mp4"}, **fields)
    p.set_step("convert", PJ.DONE, competitor={"how": "keep", "file": "competitor.mp4"},
               raw={"how": "keep", "file": "raw.mp4"})
    return p


# ---------------------------------------------------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------------------------------------------------

def test_use_as_my_headline_rules_follow_the_edit_step():
    assert flow.headline_action({}) == flow.USE_SET
    assert flow.headline_action({"steps": {"edit": {"state": "waiting"}}}) == flow.USE_SET
    assert flow.headline_action({"steps": {"edit": {"state": "failed"}}}) == flow.USE_SET
    assert flow.headline_action({"steps": {"edit": {"state": "running"}}}) == flow.USE_RUNNING
    assert flow.headline_action({"steps": {"edit": {"state": "done"}}}) == flow.USE_CHANGE


def test_set_headline_writes_the_project_and_title_txt(tmp_path):
    p = project(tmp_path)
    r = flow.set_headline(p.folder, "line one\nline two")
    assert r["title"] == "line one\nline two" and r["template"] is False      # no template made before: none now
    assert PJ.load(p.folder).data["title"] == "line one\nline two"
    assert "line one" in (p.folder / "title.txt").read_text(encoding="utf-8")


def test_titles_block_compact_has_a_use_menu_and_the_same_signals():
    from mr_app.ui.titles_block import TitlesBlock, has_titles, is_just_cuts
    app()
    t = TitlesBlock(compact=True)
    t.from_project({"post_title": "The post", "video_headline": "Top line\nBottom line"})
    assert t.b_more.isVisibleTo(t) and not t.b_use.isVisibleTo(t) and not t.b_title.isVisibleTo(t)
    got = []
    t.use_headline.connect(lambda x: got.append(("h", x)))
    t.use_title.connect(lambda x: got.append(("t", x)))
    items = dict((i[0], i[1]) for i in t.menu_items())
    items["Use as my headline"]()
    items["Use as YouTube title"]()
    assert got == [("h", "Top line\nBottom line"), ("t", "Top line Bottom line")]
    t.resize(130, 200)
    assert t.heightForWidth(130) > 0 if t.hasHeightForWidth() else True
    assert has_titles({"video_headline": ""}) and not has_titles({"competitor": {"url": "https://x"}})
    assert is_just_cuts({"kind": "just_cuts"}) and is_just_cuts({"steps": {"edit": {"kind": "just_cuts"}}})
    assert not is_just_cuts({"kind": "full"})


# ---------------------------------------------------------------------------------------------------------------------
# the Match cuts queue rows
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def queue(tmp_path, monkeypatch):
    from mr_app.tools import match_cuts_tool as M
    a = app()
    Mo.set_enabled(False)
    toasts = []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append((text, kind)), pages={})
    monkeypatch.setattr(M, "seconds_of", lambda p: 46.0 if "raw" in Path(p).name else 20.0)
    page = M.MatchCutsPage(ctx)
    page.toasts = toasts
    page.resize(1000, 1200)
    yield page
    Mo.set_enabled(True)
    page.deleteLater()
    a.processEvents()


def test_queue_row_shows_the_two_titles_and_use_as_my_headline_sets_the_project(queue, tmp_path):
    p = project(tmp_path, post_title="Funny post", video_headline="He did WHAT\nat the party")
    j = flow.edit_job(p)
    queue.add_project_job(j)
    row = queue.rows[j.id]
    assert row.titles.isVisibleTo(row)
    assert row.titles.values() == ("Funny post", "He did WHAT\nat the party")
    assert not row.kind.isVisibleTo(row)
    row.titles.b_use.click()
    assert j.title == "He did WHAT\nat the party"
    assert pump_until(lambda: PJ.load(p.folder).data.get("title") == "He did WHAT\nat the party")
    row.titles.b_title.click()
    assert pump_until(lambda: PJ.load(p.folder).data.get("upload_title") == "He did WHAT at the party")


def test_queue_row_use_as_my_headline_while_running_and_after_the_edit(queue, tmp_path, monkeypatch):
    p = project(tmp_path, video_headline="New top\nnew bottom", channel="LouLaughs")
    j = flow.edit_job(p)
    j.channel = "LouLaughs"
    queue.add_project_job(j)
    j.state = J.RUNNING
    assert queue.use_headline(j, "New top\nnew bottom") == flow.USE_RUNNING
    assert j.title != "New top\nnew bottom"
    j.state, j.result = J.DONE, {"label": "PASS"}
    called = []
    monkeypatch.setattr(flow, "change_headline", lambda pj, text, ch=None: called.append((pj.folder, text, ch)) or {})
    queue.ask_yes = lambda *_a: False
    assert queue.use_headline(j, "New top\nnew bottom") == "" and not called
    queue.ask_yes = lambda *_a: True
    assert queue.use_headline(j, "New top\nnew bottom") == flow.USE_CHANGE
    assert pump_until(lambda: bool(called))
    assert called[0][1:] == ("New top\nnew bottom", "LouLaughs")
    assert pump_until(lambda: j.title == "New top\nnew bottom")


def test_queue_row_says_just_cuts(queue, tmp_path):
    p = project(tmp_path, kind="just_cuts")
    j = flow.edit_job(p)
    queue.add_project_job(j)
    assert queue.rows[j.id].kind.isVisibleTo(queue.rows[j.id])


# ---------------------------------------------------------------------------------------------------------------------
# the board cards
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def board(tmp_path, monkeypatch):
    from mr_app.tools import board_tool as BT
    a = app()
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    toasts = []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    holder = SimpleNamespace(local=local, toasts=toasts, page=None)

    def make():
        page = BT.BoardPage(ctx)
        page._up_timer.stop()                               # noqa: SLF001
        page.check_uploads = lambda force=False: None
        page.resize(1400, 900)
        page.show()
        holder.page = page
        return page
    holder.make = make
    yield holder
    if holder.page is not None:
        holder.page.close()
        holder.page.deleteLater()
    a.processEvents()


def _card_widget(page, folder):
    for col in page.columns.values():
        for w in col.widgets:
            if Path(w.card.folder).name == Path(folder).name:
                return w
    return None


def test_board_card_shows_the_titles_compact_and_just_cuts(board):
    p1 = project(board.local, "titled", post_title="Post words", video_headline="On the video\nsecond line")
    p2 = PJ.create(board.local, name="cutsonly")             # Just cuts: no competitor
    p2.update(kind="just_cuts")
    page = board.make()
    assert pump_until(lambda: len(page.cards) == 2)
    w1, w2 = _card_widget(page, p1.folder), _card_widget(page, p2.folder)
    assert w1.titles is not None and w1.titles.compact
    assert w1.titles.values() == ("Post words", "On the video\nsecond line")
    assert w1.kind is None
    assert w2.kind is not None and w2.titles is None
    # nothing on the card is wider than the card (no text cut off at the side)
    for child in w1.titles.findChildren(QtWidgets.QWidget):
        if child.isVisibleTo(w1):
            assert child.geometry().right() <= w1.titles.width() + 1, child


def test_board_card_use_as_my_headline(board, monkeypatch):
    p = project(board.local, "fresh", video_headline="Read on\nthe video")
    page = board.make()
    assert pump_until(lambda: len(page.cards) == 1)
    w = _card_widget(page, p.folder)
    w.titles.use_as_headline()                              # no edit yet: the project's headline is set
    assert pump_until(lambda: PJ.load(p.folder).data.get("title") == "Read on\nthe video")
    card = page.cards[0]
    card.data = {"steps": {"edit": {"state": "running"}}}
    assert page.use_headline(card, "x") == flow.USE_RUNNING
    card.data = {"steps": {"edit": {"state": "done"}}}
    card.channel = "LouLaughs"
    called = []
    monkeypatch.setattr(flow, "change_headline", lambda pj, text, ch=None: called.append(text) or {})
    page.ask_yes = lambda *_a: False
    assert page.use_headline(card, "y") == "" and not called
    page.ask_yes = lambda *_a: True
    assert page.use_headline(card, "y") == flow.USE_CHANGE
    assert pump_until(lambda: called == ["y"])
