"""7 Nov (your call): less on the screen.

* No upload counter (the motivation strip) over the Match cuts queue, and no Goals row in Settings.
* No Board and no History tab (nor in Ctrl+K). The History page is not made at all.
* The Board's page is still made and works out of sight (never shown): the automatic "Uploaded" check marks a Short
  with the same title quietly -- so a finished project still becomes only its zip once its Short is up -- and leaves
  a near match alone (nobody would see "Is this the same video?"); Clean up still runs, and a toast says it looks.

Run: python -m pytest tools/app/tests/test_less_on_screen_7nov.py"""
from __future__ import annotations

import json
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

from mr_app.core import board as B  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.tools import board_tool as BT  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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
def window(qapp, tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"save_to": str(tmp_path / "shared"), "intro": False}), encoding="utf-8")
    (tmp_path / "shared").mkdir()
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(f))
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(tmp_path / "local"))
    from mr_app.tools import tools
    from mr_app.ui.main_window import MainWindow
    win = MainWindow(tools())
    win.show()
    qapp.processEvents()
    yield win
    win.hide()
    win.deleteLater()
    qapp.processEvents()


def test_two_tabs_no_history_page_and_nothing_in_ctrl_k(window):
    win = window
    assert [t.text for t in win.tabbar.tabs] == ["Match cuts", "Progress"]
    assert "history" not in win.pages
    names = [n for n, _k, _fn in win.command_actions()]
    assert "Board" not in names and "History" not in names and "Learn" in names


def test_the_queue_has_no_upload_counter(window):
    from mr_app.ui.motivation_strip import MotivationStrip
    mc = window.pages["match_cuts"]
    assert not hasattr(mc, "motivation") and not mc.findChildren(MotivationStrip)
    assert not window.findChildren(MotivationStrip)


def test_the_board_works_out_of_sight_and_is_never_shown(qapp, window):
    b = window.pages["board"]
    assert b.on_screen is False and not b.isWindow()        # a hidden part of the window, never a window of its own
    qapp.processEvents()
    assert not b.isVisible()
    window.select("board")                                   # (on no tab: nothing happens)
    assert not b.isVisible()


def test_clean_up_out_of_sight_says_it_looks_and_runs(window, monkeypatch):
    b = window.pages["board"]
    ran, said = [], []
    monkeypatch.setattr(b, "clean_up", lambda: ran.append(True))
    monkeypatch.setattr(window, "toast", lambda text, kind="ok": said.append(text))
    window.clean_up_now()
    assert ran == [True] and said and "Clean up" in said[0]


# -- the Uploaded check out of sight --
@pytest.fixture
def board(qapp, tmp_path, monkeypatch):
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    toasts: list = []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    page = BT.BoardPage(ctx)
    page._up_timer.stop()                                    # noqa: SLF001 - no Shorts check in tests
    page.check_uploads = lambda force=False: None
    page.on_screen = False                                   # as main_window makes it: on no tab
    yield SimpleNamespace(page=page, local=local, toasts=toasts, app=qapp)
    page.deleteLater()
    qapp.processEvents()


def _project(root: Path, name: str, title: str) -> Path:
    p = PJ.create(root, name=name)
    for s in ("download", "convert"):
        p.set_step(s, PJ.DONE)
    p.update(channel="LouLaughs", title=title)
    return p.folder


def test_out_of_sight_an_exact_upload_is_marked_quietly_and_a_near_one_waits(board):
    a = _project(board.local, "clip one", "The funniest pharmacy scene")
    b = _project(board.local, "clip two", "The worst messenger")
    page = board.page
    assert pump_until(board.app, lambda: len(page.cards) == 2)
    up = SimpleNamespace(id="exact1", url="https://www.youtube.com/shorts/exact1", channel="LouLaughs",
                         title="The funniest pharmacy scene")
    near = SimpleNamespace(id="near1", url="https://www.youtube.com/shorts/near1", channel="LouLaughs",
                           title="The worst messengers")
    page._uploads(SimpleNamespace(exact=[(str(a), up)], near=[(str(b), near, 0.8)], offline=False))   # noqa: SLF001
    assert pump_until(board.app, lambda: B.card_of(a).column == "uploaded")
    assert (B.card_of(a).uploaded or {}).get("id") == "exact1"
    assert page.asks.count() == 0                            # no question nobody would see
    assert B.card_of(b).column != "uploaded"                 # the near one waits
    board.app.processEvents()
    assert board.toasts == []                                # and no count said
