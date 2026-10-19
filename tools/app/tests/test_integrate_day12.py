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


# ---------------------------------------------------------------------------------------------------------------------
# section 6c: a REAL torrent through the New video form (aria2c on 127.0.0.1 only, a tracker run here)
# ---------------------------------------------------------------------------------------------------------------------

class FakeQueue:
    def __init__(self):
        self.jobs = []
        self.running = False

    def add_project_job(self, job, autostart=False):
        self.jobs.append((job, autostart))

    def busy(self):
        return False


def _tiny_video(path: Path, seconds_: float = 2.0, size: str = "320x240") -> Path:
    import subprocess

    from match_cuts.common import ffmpeg_bin
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=10:d={seconds_}", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _new_video_page(tmp_path, monkeypatch):
    from mr_app.tools import download_tool as D
    app()
    Mo.set_enabled(False)
    monkeypatch.setattr(D.DownloadPage, "_torrent_dir", staticmethod(lambda: tmp_path / "torrents"))
    toasts = []
    ctx = SimpleNamespace(pages={"match_cuts": FakeQueue()}, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    page = D.DownloadPage(ctx)
    page.toasts = toasts
    page.resize(1000, 900)
    page.show()
    return page


def _aria2c_missing() -> bool:
    try:
        from mr_app.core import torrent as T
        return T.find_aria2c() is None
    except Exception:  # noqa: BLE001
        return True


@pytest.mark.skipif(_aria2c_missing(), reason="aria2c.exe is not installed (run setup_app.py)")
def test_a_real_local_torrent_goes_through_the_new_video_row(tmp_path, monkeypatch):
    """A magnet of a tiny real video, shared by one aria2c on 127.0.0.1: the picker inside the row (BIGGEST VIDEO
    ticked) -> Start -> the progress card in the row -> finished -> the video is the RAW, placed into the project ->
    the row waits for Convert (Automatic OFF); Automatic ON then lets it go on to Convert at once."""
    import base64
    import urllib.parse

    from mr_app.core import torrent as T
    from mr_app.core import torrent_selftest as TS
    from mr_app.tools import download_tool as D
    from mr_app.ui import torrent_panel as TP
    monkeypatch.setenv("MOVIERECAPS_TORRENTS", str(tmp_path / "tstate"))
    seed_dir, seed_state, get_state = tmp_path / "seed", tmp_path / "ss", tmp_path / "gs"
    for d in (seed_dir, seed_state, get_state):
        d.mkdir()
    raw = _tiny_video(seed_dir / "Tiny.Episode.mp4", 3.0)
    (seed_dir / "notes.txt").write_text("a small extra file", encoding="utf-8")
    comp = _tiny_video(tmp_path / "comp" / "short.mp4", 1.0, "180x320")
    tracker = TS.LocalTracker()
    off = ["--enable-dht=false", "--bt-enable-lpd=false", "--bt-tracker-interval=2"]
    seeder = T.Aria2(seed_state, listen_port=T._free_port(), extra_args=off)   # noqa: SLF001
    getter = T.Aria2(get_state, listen_port=T._free_port(), extra_args=off)    # noqa: SLF001
    page = None
    try:
        tor = T.make_torrent(seed_dir, [tracker.url])
        meta0 = T.read_torrent(tor)
        seeder.start()
        seeder.call("aria2.addTorrent", base64.b64encode(tor).decode(), [],
                    {"dir": str(tmp_path), "check-integrity": "true", "seed-ratio": "0.0",
                     "bt-tracker-interval": "2"})
        TP.set_session(T.TorrentSession(get_state, engine=getter, probe_minutes=1.5))
        page = _new_video_page(tmp_path, monkeypatch)
        if page.auto.isChecked():
            page.auto.click()
        magnet = f"magnet:?xt=urn:btih:{meta0.info_hash}&dn=tiny&tr={urllib.parse.quote(tracker.url, safe='')}"
        page.raw.setText(magnet)
        page.comp.setText(str(comp))
        assert page.d_raw.text() == "TORRENT" and page.will.text() == "Full Match cuts edit"
        it = page.add()
        assert it is not None and it.state == D.WORKING
        row = page.rows[it.id]
        picker = it.torrent_widget
        assert isinstance(picker, TP.TorrentPicker) and picker.parentWidget() is not None
        assert row.isAncestorOf(picker)                                        # inside the row
        assert pump_until(lambda: picker.meta is not None, 60), picker.note.text()
        ticked = [r.file.path for r in picker.rows if r.checked()]
        assert len(ticked) == 1 and ticked[0].endswith("Tiny.Episode.mp4")     # the biggest video, ticked
        assert len(picker.rows) == 2
        picker.start_btn.click()
        assert pump_until(lambda: isinstance(it.torrent_widget, TP.TorrentCard), 20)
        card = it.torrent_widget
        assert row.isAncestorOf(card)
        assert pump_until(lambda: it.raw_file is not None, 60), page.now_text(it)
        assert Path(it.raw_file).read_bytes() == raw.read_bytes()
        assert pump_until(lambda: it.state == D.GATED, 60), (it.state, page.now_text(it), it.error)
        assert it.gate == D.G_CONVERT and not row.b_go.isHidden() and row.b_go.text() == "Convert"
        assert it.project is not None and (Path(it.project) / "project.json").is_file()
        assert "Tiny.Episode" in str(PJ.load(it.project).data.get("raw"))
        assert pump_until(lambda: it.torrent_widget is None, 8)               # the green card goes after a moment
        converted = []
        monkeypatch.setattr(D.StageThread, "_convert", lambda self, out: converted.append(self.item.id))
        page.auto.click()                                                      # Automatic ON: it goes on at once
        assert pump_until(lambda: bool(converted), 20)
        assert converted == [it.id]
    finally:
        if page is not None:
            if page.auto.isChecked():
                page.auto.click()
            page.poll.stop()
            page.close()
            page.deleteLater()
        TP.set_session(None)
        getter.stop()
        seeder.stop()
        tracker.close()
        Mo.set_enabled(True)


def test_torrent_rows_come_back_after_a_restart(tmp_path, monkeypatch):
    """The main window asks the Download page to restore the torrents at start: each unfinished download gets its
    row back with the video it belonged to (competitor, channel, headline) and its progress card."""
    import sys as _sys

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QWidget

    from mr_app.core import newvideo as NV
    from mr_app.tools import download_tool as D
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    NV.keep_torrent_row("abcd1234abcd1234", {"competitor_url": "https://www.tiktok.com/@a/video/9",
                                            "channel": "LouLaughs", "title": "Line one\nline two",
                                            "kind": NV.FULL, "raw_torrent": "magnet:?xt=urn:btih:AA", "x": 1})
    assert NV.torrent_rows()["abcd1234abcd1234"]["channel"] == "LouLaughs"

    class Job(QObject):
        progress = Signal(dict)
        finished = Signal(str)
        failed = Signal(str)
        removed = Signal()

        def __init__(self, jid, name):
            super().__init__()
            self.id, self.name, self.status = jid, name, {}

    class Card(QWidget):
        def __init__(self, job):
            super().__init__()
            self.job = job
    made = [Job("abcd1234abcd1234", "Some.Movie"), Job("ffff0000ffff0000", "Other.Movie")]
    fake = SimpleNamespace(TorrentCard=Card, restore_jobs=lambda cb: cb(made))
    monkeypatch.setitem(_sys.modules, "mr_app.ui.torrent_panel", fake)
    import mr_app.ui as UI
    monkeypatch.setattr(UI, "torrent_panel", fake, raising=False)
    monkeypatch.setattr(NV, "unfinished_torrents", lambda: True)
    page = _new_video_page(tmp_path, monkeypatch)
    try:
        page.restore_torrents()
        assert pump_until(lambda: len(page.items) == 2)
        by = {i.torrent_job.id: i for i in page.items}
        a, b = by["abcd1234abcd1234"], by["ffff0000ffff0000"]
        assert a.channel == "LouLaughs" and a.title == "Line one\nline two" and a.kind == NV.FULL
        assert a.competitor_url.endswith("/video/9") and a.state == D.WORKING
        assert isinstance(a.torrent_widget, Card) and page.rows[a.id].isAncestorOf(a.torrent_widget)
        assert b.kind == NV.JUST_CUTS and b.name == "Other.Movie"              # no row kept: a row of its own
        made[0].progress.emit({"share": 0.25, "percent": 25, "seeders": 3, "state": "downloading"})
        assert a.steps["download"].share == pytest.approx(0.245)
        made[0].removed.emit()                                                 # Remove on the card: the row stops
        assert a.state == D.STOPPED and a.torrent_widget is None
        assert pump_until(lambda: "abcd1234abcd1234" not in NV.torrent_rows())
        made[1].failed.emit("disk full")
        assert b.state == D.FAILED
    finally:
        page.poll.stop()
        page.close()
        page.deleteLater()
        Mo.set_enabled(True)


def test_the_main_window_restores_torrents_at_start():
    import inspect

    from mr_app.ui import main_window as MW
    assert "restore_torrents" in inspect.getsource(MW.MainWindow)


# ---------------------------------------------------------------------------------------------------------------------
# sections 8 + 2: every way an edit finishes reaches the Learn hook
# ---------------------------------------------------------------------------------------------------------------------

class QueueThread:
    """JobThread without the tool (the queue's), ``finish(res)`` ends it."""
    made: list = []

    def __init__(self, job, converted, out_dir):
        from PySide6.QtCore import QObject, Signal

        class Sig(QObject):
            progress = Signal(int, str, float)
            done = Signal(int, dict)
        self._sig = Sig()
        self.progress, self.done = self._sig.progress, self._sig.done
        self.job = job
        QueueThread.made.append(self)

    def start(self) -> None:
        pass

    def wait(self, _ms=0) -> bool:
        return True

    def stop(self) -> None:
        self.done.emit(self.job.id, {"label": "STOPPED"})


def test_an_edit_sent_from_new_video_finishing_in_the_queue_gets_its_waiting_card(tmp_path, monkeypatch):
    import json

    from mr_app.core import waiting
    from mr_app.tools import download_tool as D
    from mr_app.tools import match_cuts_tool as M
    app()
    Mo.set_enabled(False)
    hub = tmp_path / "hub"
    hub.mkdir()
    QueueThread.made = []
    monkeypatch.setattr(M, "JobThread", QueueThread)
    monkeypatch.setattr(M, "seconds_of", lambda p: 1300.0 if "raw" in Path(p).name else 50.0)
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": None, hub_folder=lambda: hub, pages={})
    mc = M.MatchCutsPage(ctx)
    dl = D.DownloadPage(ctx)
    ctx.pages.update(match_cuts=mc, download=dl)
    try:
        p = project(tmp_path / "projects", channel="LouLaughs")
        it = D.Item("https://www.tiktok.com/@a/video/1", raw_url="https://youtu.be/AbCdEf12345",
                    channel="LouLaughs", title="my\nheadline")
        it.project, it.name = p.folder, p.folder.name
        dl.items.append(it)
        dl.rows[it.id] = D.VideoRow(dl, it)
        dl.send_to_edit(it, start=True)
        assert it.state == D.SENT
        assert pump_until(lambda: len(QueueThread.made) == 1)
        th = QueueThread.made[0]
        run = p.folder / "edit"
        (run / "extras").mkdir(parents=True)
        th.done.emit(th.job.id, {"label": "PASS", "run_dir": str(run), "seconds": 3.0})
        assert pump_until(lambda: (hub / waiting.FILE).is_file())
        items = json.loads((hub / waiting.FILE).read_text(encoding="utf-8"))["items"]
        assert len(items) == 1 and items[0]["channel"] == "LouLaughs" and items[0]["state"] == "waiting"
        dl._poll()                                                             # noqa: SLF001 - the row follows
        assert it.state == D.DONE
    finally:
        dl.poll.stop()
        for w in (mc, dl):
            w.deleteLater()
        Mo.set_enabled(True)
        app().processEvents()


def test_a_just_cuts_run_that_fails_goes_into_the_problems_list_but_never_waits_for_a_fix(tmp_path, monkeypatch):
    from mr_app.core import problems, waiting
    from mr_app.tools import download_tool as D
    from mr_app.core import newvideo as NV
    app()
    hub = tmp_path / "hub"
    hub.mkdir()
    ctx = SimpleNamespace(notify=lambda t, x: None, toast=lambda text, kind="ok": None, hub_folder=lambda: hub,
                          pages={})
    dl = D.DownloadPage(ctx)
    try:
        p = PJ.create(tmp_path / "projects", name="cutsonly")
        run = p.folder / "cuts"
        (run / "extras").mkdir(parents=True)
        (run / "extras" / "just_cuts_console.log").write_text("ERROR the RAW could not be read\n", encoding="utf-8")
        it = D.Item("", raw_url="https://youtu.be/AbCdEf12345", kind=NV.JUST_CUTS)
        it.project, it.name = p.folder, p.folder.name
        dl.items.append(it)
        dl.rows[it.id] = D.VideoRow(dl, it)
        dl._done(it.id, {"ok": False, "stage": "cuts", "error": "the RAW could not be read",           # noqa: SLF001
                         "result": {"label": "FAIL", "failures": ["the RAW could not be read"], "run_dir": str(run)}})
        assert it.state == D.FAILED
        assert pump_until(lambda: (hub / problems.FILE).is_file())
        assert "cutsonly" in (hub / problems.FILE).read_text(encoding="utf-8")
        it2 = D.Item("", raw_url="https://youtu.be/AbCdEf12345", kind=NV.JUST_CUTS)
        it2.project, it2.name = p.folder, p.folder.name
        dl.items.append(it2)
        dl.rows[it2.id] = D.VideoRow(dl, it2)
        dl._finish(it2, {"label": "PASS", "cuts": 12, "run_dir": str(run)})                          # noqa: SLF001
        pump_until(lambda: False, 0.5)
        assert not (hub / waiting.FILE).is_file()                              # nothing to learn from Just cuts
    finally:
        dl.poll.stop()
        dl.deleteLater()
        app().processEvents()
