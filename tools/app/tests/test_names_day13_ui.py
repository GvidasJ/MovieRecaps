"""Day 13, section 4 on the real widgets: the board card, the queue row and History show the video's title as the name
(the folder in a tooltip or a small line), "Rename to title" on a card whose folder is not called after its title --
a button that only fades under the mouse, never moves, and renames in the background -- and the pages follow a
folder that moved (their rows keep working), and say which folders their rows use (a rename waits for them).

Run: python -m pytest tools/app/tests/test_names_day13_ui.py"""
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
from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402

from mr_app.core import board as B  # noqa: E402
from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import names as NM  # noqa: E402
from mr_app.core import premiere as PM  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import renames as RN  # noqa: E402
from mr_app.tools import board_tool as BT  # noqa: E402
from mr_app.tools import match_cuts_tool as MT  # noqa: E402
from mr_app.ui import motion as Mo  # noqa: E402
from mr_app.ui import widgets as W  # noqa: E402

HEAD = "The fact that he\nsaid it couldn't get worse 😭"
SHOWN = "The fact that he said it couldn't get worse 😭"


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
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(PM, "state", lambda: PM.State())
    monkeypatch.setattr(RN, "scan_places", lambda hub: [])
    monkeypatch.setattr(NM, "root_len", lambda *roots: NM.ROOT_LEN)      # (the test's folder is very long)
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
    yield SimpleNamespace(local=local, shared=shared, toasts=toasts, make_page=make_page, app=qapp, ctx=ctx)
    for p in pages:
        p.close()
        p.deleteLater()
    qapp.processEvents()


def card_widgets(page) -> list:
    return [w for col in page.columns.values() for w in getattr(col, "widgets", [])]


def test_an_old_cards_name_is_its_title_and_rename_to_title_renames_it(setup):
    p = PJ.create(setup.local, competitor_url="https://youtu.be/AbCdEf12345", name="video by mindculpt")
    p.set_step("download", PJ.DONE)
    B.set_title(p.folder, HEAD)
    old = p.folder
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(card_widgets(page)) == 1)
    w = card_widgets(page)[0]
    assert w.name_label.text().replace("\u200b", "") == SHOWN               # the title is the name
    assert str(old) in w.name_label.toolTip()                             # the folder in the tooltip
    texts = [lab.text().replace("\u200b", "") for lab in w.findChildren(QtWidgets.QLabel)]
    assert any(t == f"folder: {old.name}" for t in texts)                 # ... and a small line (not called so)
    assert isinstance(w.rename, W.Button) and w.rename.text() == "Rename to title" and w.rename.isVisibleTo(w)
    # under the mouse it only fades (in 0.3 s, out 0.5 s); nothing moves or grows
    assert (w.rename.hover.in_ms, w.rename.hover.out_ms) == (Mo.FADE_IN_MS, Mo.FADE_OUT_MS)
    assert abs(Mo.FADE_IN_MS - 300) <= 50 and abs(Mo.FADE_OUT_MS - 500) <= 50
    assert pump_until(setup.app, lambda: 0 < w.rename.geometry().width() < 400)       # laid out on its card
    g0, c0 = w.rename.geometry(), w.geometry()
    Mo.set_enabled(False)
    try:
        QtWidgets.QApplication.sendEvent(w.rename, QEnterEvent(QPointF(3, 3), QPointF(3, 3), QPointF(3, 3)))
        QtWidgets.QApplication.sendEvent(w, QEnterEvent(QPointF(3, 3), QPointF(3, 3), QPointF(3, 3)))
        setup.app.processEvents()
        assert w.rename.geometry() == g0 and w.geometry() == c0
        QtWidgets.QApplication.sendEvent(w.rename, QEvent(QEvent.Type.Leave))
    finally:
        Mo.set_enabled(True)
    w.rename.click()                                                      # in the background: the window never waits
    want = setup.local / f"{NM.day_of(old.name)}_{SHOWN}"
    assert pump_until(setup.app, lambda: want.is_dir() and any(c.folder == want for c in page.cards))
    assert not old.exists() and any("Renamed after its title" in t for t, _k in setup.toasts)
    assert pump_until(setup.app, lambda: card_widgets(page) and card_widgets(page)[0].rename is None)
    assert NM.uses_titles(PJ.load(want).data)                             # it follows its title from now on


def test_a_card_named_after_its_title_has_no_button_and_a_kept_one_says_why(setup):
    base = NM.folder_base(SHOWN, "2026-10-20")
    a = PJ.create(setup.local, competitor_url="https://youtu.be/AbCdEf12345", folder=base, names=NM.marker("headline"))
    B.set_title(a.folder, HEAD)
    b = PJ.create(setup.local, competitor_url="https://youtu.be/ZyXwVu98765", name="clip two",
                  names=NM.marker("youtube"))
    B.set_upload_title(b.folder, "Kept title 📻")
    want = NM.folder_base("Kept title 📻", NM.day_of(b.folder.name))
    PJ.load(b.folder).update(names={**NM.marker("youtube"), "kept": {"for": want, "why": "your project cop.prproj "
                                                                                      "plays files from it"}})
    page = setup.make_page()
    assert pump_until(setup.app, lambda: len(card_widgets(page)) == 2)
    by = {w.card.folder: w for w in card_widgets(page)}
    assert by[a.folder].rename is None and by[a.folder].name_label.text().replace("\u200b", "") == SHOWN
    kept = by[b.folder]
    assert kept.rename is None
    tips = [lab.toolTip() for lab in kept.findChildren(QtWidgets.QLabel)]
    assert any("keeps its name: your project cop.prproj" in t for t in tips)


@pytest.fixture
def queue(qapp, tmp_path, monkeypatch):
    Mo.set_enabled(False)
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": None)
    monkeypatch.setattr(MT, "seconds_of", lambda p: 30.0)
    p = MT.MatchCutsPage(ctx)
    p.resize(900, 1200)
    yield p
    Mo.set_enabled(True)
    p.deleteLater()
    qapp.processEvents()


def test_the_queue_row_shows_the_title_says_its_folder_is_used_and_follows_a_move(queue, tmp_path):
    base = NM.folder_base(SHOWN, "2026-10-20")
    proj = PJ.create(tmp_path / "local", competitor_url="https://youtu.be/AbCdEf12345", folder=base,
                     names=NM.marker("headline"))
    proj.update(title=HEAD, upload_title="My YouTube title 🎥")
    (proj.folder / "competitor.mp4").write_bytes(b"c")
    (proj.folder / "raw.mp4").write_bytes(b"r")
    j = J.Job(proj.folder.name, proj.folder / "competitor.mp4", proj.folder / "raw.mp4", source="project")
    j.project, j.title = proj.folder, HEAD
    j.comp_s, j.raw_s = 30.0, 600.0
    queue._added_jobs(J.DropResult([j], None, []))                        # noqa: SLF001 - as a drop does
    row = queue.rows[j.id]
    assert row.name.text() == "My YouTube title 🎥"                        # the YouTube title first
    assert f"Folder: {proj.folder.name}" in row.name.toolTip()
    assert queue.uses_folder(proj.folder) == "it waits in the Match cuts queue"
    assert RN.in_use(proj.folder, proj.data) == "it waits in the Match cuts queue"        # what renames asks
    j.state, j.run_dir = J.DONE, proj.folder / "edit"
    j.result = {"label": "PASS", "run_dir": str(proj.folder / "edit")}
    assert queue.uses_folder(proj.folder) == ""
    new = proj.folder.with_name("2026-10-20_My YouTube title 🎥")
    queue.moved(proj.folder, new)
    assert j.project == new and j.run_dir == new / "edit" and j.competitor == new / "competitor.mp4"
    assert j.raw == new / "raw.mp4" and j.result["run_dir"] == str(new / "edit") and j.name == new.name


def test_history_rows_show_the_title_with_the_folder_in_the_tooltip(qapp):
    from mr_app.core import history as H
    from mr_app.tools import history_tool as HT
    data = {"name": "2026-10-19_video-by-mindculpt", "title": HEAD, "upload_title": "Rush Hour's best line 😂",
            "channel": "LouLaughs", "created": "2026-10-19T02:00:00+03:00", "steps": {}}
    evs = H.project_events(data)
    assert evs and evs[0].shown == "Rush Hour's best line 😂" and evs[0].title == HEAD
    assert H.search(evs, "best line") == evs                              # found by the title shown
    item = HT._Item(evs[0])                                                # noqa: SLF001
    assert item.line2.startswith("Rush Hour's best line 😂")
    assert "Folder: 2026-10-19_video-by-mindculpt" in item.toolTip()


def test_the_download_rows_say_their_folder_is_used_and_follow_a_move(tmp_path):
    from mr_app.tools import download_tool as D
    folder = tmp_path / "2026-10-20_x"
    it = D.Item("https://youtube.com/shorts/Zx9Yw8Vu7Ts", raw_url="https://youtu.be/AbCdEf12345")
    it.project, it.name, it.state = folder, folder.name, D.QUEUED
    refreshed = []
    fake = SimpleNamespace(current=None, items=[it], rows={it.id: object()}, _refresh=refreshed.append)
    assert D.DownloadPage.uses_folder(fake, folder) == "a New video row is working on it"
    it.state = D.GATED                                                    # waits for your Convert press: free
    assert D.DownloadPage.uses_folder(fake, folder) == ""
    new = tmp_path / "2026-10-20_The title"
    D.DownloadPage.moved(fake, folder, new)
    assert it.project == new and it.name == new.name and refreshed == [it]


def test_the_window_renames_after_a_title_change_and_retries_what_waited(qapp, tmp_path, monkeypatch):
    from mr_app.core import locks
    from mr_app.ui import main_window as MW
    local, hub = tmp_path / "local", tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(PM, "state", lambda: PM.State())
    monkeypatch.setattr(RN, "scan_places", lambda hub: [])
    monkeypatch.setattr(NM, "root_len", lambda *roots: NM.ROOT_LEN)
    monkeypatch.setattr(MW.BG, "call", lambda work, done=None, failed=None, **k: done(work()) if done else work())
    base = NM.folder_base("First title", "2026-10-20")
    p = PJ.create(local, competitor_url="https://youtu.be/AbCdEf12345", folder=base, names=NM.marker("headline"))
    B.set_title(p.folder, "First title")
    seen, toasts = [], []
    page = SimpleNamespace(moved=lambda o, n: seen.append((o.name, n.name)), titles_changed=lambda f: None)
    win = SimpleNamespace(settings=SimpleNamespace(save_to=str(hub)), pages={"match_cuts": page},
                          toast=lambda t, k="ok": toasts.append((t, k)))
    win.renamed = lambda res, said=False: MW.MainWindow.renamed(win, res, said)
    win.project_moved = lambda o, n: MW.MainWindow.project_moved(win, o, n)
    B.set_upload_title(p.folder, "The YouTube title 🎥")
    lock = locks.WorkLock(p.folder, "edit")
    lock.acquire()
    try:
        MW.MainWindow.title_changed(win, p.folder)                       # an edit runs in it: it waits
        assert toasts[-1][1] == "note" and "waits" in toasts[-1][0] and seen == [] and RN.pending()
    finally:
        lock.release()
    MW.MainWindow.retry_renames(win)                                      # the minute's retry renames it
    want = "2026-10-20_The YouTube title 🎥"
    assert seen == [(base, want)] and (local / want).is_dir() and RN.pending() == []
    assert any("Renamed after its title" in t for t, _k in toasts)


def test_a_new_video_rows_folder_follows_the_headline_after_its_template(tmp_path, monkeypatch):
    from mr_app.core import flow
    from mr_app.tools import download_tool as D
    monkeypatch.setattr(PM, "state", lambda: PM.State())
    monkeypatch.setattr(NM, "root_len", lambda *roots: NM.ROOT_LEN)
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    p = PJ.create(tmp_path / "local", competitor_url="https://youtu.be/AbCdEf12345", name="Video by darkhumor",
                  names=NM.marker(""))                                    # no title yet: the old way's name
    it = D.Item("https://youtu.be/AbCdEf12345", raw_url="https://youtu.be/ZyXwVu98765", channel="LouLaughs",
                title="When it finally\nworks 💀")
    it.project = p.folder
    monkeypatch.setattr(flow, "template_step", lambda proj, ch, title: proj.update(title=title, channel=ch))
    named = []
    th = D.StageThread(it, ("template",))
    th.named.connect(lambda i, f: named.append(f))
    out: dict = {}
    th._template(out)                                                     # noqa: SLF001 - the stage as it runs
    want = p.folder.with_name(f"{NM.day_of(p.folder.name)}_When it finally works 💀")
    assert it.project == want and want.is_dir() and named == [str(want)] and "renamed" in out.get("rename", "")


def test_the_window_tells_every_page_what_moved(qapp):
    from mr_app.ui import main_window as MW
    seen: list = []
    page = SimpleNamespace(moved=lambda o, n: seen.append(("moved", o.name, n.name)),
                           titles_changed=lambda f: seen.append(("titles", f.name)))
    board = SimpleNamespace(moved=lambda o, n: None, reload=lambda: seen.append(("reload",)))
    toasts: list = []
    win = SimpleNamespace(pages={"match_cuts": page, "board": board}, toast=lambda t, k="ok": toasts.append((t, k)))
    win.project_moved = lambda o, n: MW.MainWindow.project_moved(win, o, n)
    res = RN.Result(RN.RENAMED, Path("C:/p/2026-10-20_New"), "renamed", [(Path("C:/p/old"), Path("C:/p/2026-10-20_New"))])
    MW.MainWindow.renamed(win, res)
    assert ("moved", "old", "2026-10-20_New") in seen and ("titles", "2026-10-20_New") in seen and ("reload",) in seen
    assert toasts and "Renamed after its title" in toasts[0][0]
    toasts.clear()
    MW.MainWindow.renamed(win, RN.Result(RN.WAIT, Path("C:/p/x"), "waits: Premiere has x.prproj open"))
    assert toasts == [("waits: Premiere has x.prproj open", "note")]
