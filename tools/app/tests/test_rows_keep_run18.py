"""Run 18, task 4: the Match cuts queue and the New video list stay when the app closes -- every row (running, paused,
failed, stopped) with all its inputs (RAW, competitor, headline, YouTube title, channel, mode, speed) and its stop
message comes back as it was, with its buttons; and History has Try again + both "Try with another ..." on every
failure (a Convert failed too, and a failure that never got a project folder). The app's pages are made twice here
(closed, then opened again); the runs and downloads are not started.

Run: python -m pytest tools/app/tests/test_rows_keep_run18.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import pause as PZ  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import rows_keep as RK  # noqa: E402

HEAD = "The fact that the teacher assumed\nthe worst about him is hilarious 🏫😭"
YT = "The Most Clueless Teacher In Comedy History 🤣"
CH = "MikeMemesYou"
MAGNET = "magnet:?xt=urn:btih:ABC&dn=Everybody.Hates.Chris.S03"


@pytest.fixture
def qt(monkeypatch, tmp_path):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.ui import background as BG
    from mr_app.ui import motion as Mo
    monkeypatch.setenv("MOVIERECAPS_KEEP_ROWS", "1")
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    monkeypatch.setattr(BG, "call", lambda fn, done=None, failed=None, work=False: (done or (lambda _r: None))(fn()))
    from mr_app.tools import match_cuts_tool as M
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0)
    yield app
    Mo.set_enabled(True)


def _open(qt):
    """The app opened: its queue and New video pages (the queue first, as the window makes them; New video's kept
    rows come back with the torrents, 1.5 s after the start -- here at once)."""
    from mr_app.tools import download_tool as D
    from mr_app.tools import match_cuts_tool as M
    ctx = SimpleNamespace(notify=lambda t, x: None, toast=lambda text, kind="ok": None, selected=[],
                          set_strip_queue=lambda n, running: None)
    ctx.select = ctx.selected.append
    q = M.MatchCutsPage(ctx)
    dl = D.DownloadPage(ctx)
    ctx.pages = {"match_cuts": q, "download": dl}
    dl.restore_torrents()
    return ctx, q, dl


def _close(qt, *pages):
    """The app closed: every page's closing() (the main window's closeEvent), then the pages are gone."""
    for p in pages:
        p.closing()
    for p in pages:
        getattr(p, "poll", SimpleNamespace(stop=lambda: None)).stop()
        p.deleteLater()
    qt.processEvents()


def _project(root: Path, name: str) -> PJ.Project:
    p = PJ.create(root, name=name)
    comp = p.folder / "Video by memezar [Ddm3LHisOm6].mp4"
    comp.write_bytes(b"c")
    raw = p.folder / "Everybody Hates Chris - S03E07.mkv"
    raw.write_bytes(b"r")
    p.update(channel=CH, title=HEAD, upload_title=YT,
             competitor={"url": "https://www.instagram.com/reel/Ddm3LHisOm6/", "file": comp.name},
             raw={"source_file": str(raw), "file": raw.name, "title": raw.stem})
    return PJ.load(p.folder)


def test_a_failed_convert_a_stopped_download_and_a_running_edit_all_come_back_with_their_buttons(qt, tmp_path):
    from mr_app.tools import download_tool as D
    ctx, q, dl = _open(qt)
    # 1. a failed convert (New video): its project, Thorough, 125 %
    p1 = _project(tmp_path / "p", "convert")
    p1.set_step("download", PJ.DONE)
    p1.set_step("convert", PJ.FAILED, error="the convert check: the sound is 3 s short")
    it1 = D.Item("https://www.instagram.com/reel/Ddm3LHisOm6/", channel=CH, title=HEAD, mode=J.THOROUGH, speed=125.0,
                 project=p1.folder, name="convert", raw_file=p1.folder / "Everybody Hates Chris - S03E07.mkv")
    it1.upload_title = YT
    dl.items.append(it1)
    dl.rows[it1.id] = D.VideoRow(dl, it1)
    dl._step(it1, "download", D.S_DONE, 1.0, "")                                     # noqa: SLF001
    dl._fail(it1, "convert", "the convert check: the sound is 3 s short")           # noqa: SLF001
    # 2. a stopped download (a torrent RAW, the competitor a file)
    comp2 = tmp_path / "Video by gigglefarming [Db55ZBXS6YT].mp4"
    comp2.write_bytes(b"c")
    it2 = D.Item("", channel="Flick707", title="The fact that he called\njust to laugh is hilarious 📞😭",
                 raw_torrent=MAGNET, comp_file=comp2)
    it2.state, it2.failed_step, it2.error = D.STOPPED, "download", "stopped by you"
    dl.items.append(it2)
    dl.rows[it2.id] = D.VideoRow(dl, it2)
    # 3. a running edit: its New video row sent to the queue, its queue row running (the close pauses it: the run
    #    folder keeps its note -- night 15's pause_for_close)
    p3 = _project(tmp_path / "p", "edit")
    for s in ("download", "convert", "template"):
        p3.set_step(s, PJ.DONE)
    run3 = p3.folder / "edit"
    PZ.mark(run3, "S5.3 refine", "Placing every frame exactly")
    j3 = J.Job("edit", p3.folder / "Video by memezar [Ddm3LHisOm6].mp4", p3.folder / "Everybody Hates Chris - S03E07.mkv",
               project=p3.folder, channel=CH, title=HEAD, mode=J.FAST, speed=100.0, run_dir=run3)
    j3.upload_title = YT
    q._add_row(j3)                                                                  # noqa: SLF001
    j3.state, j3.progress_plain = J.RUNNING, "Placing every frame exactly"
    it3 = D.Item("https://www.instagram.com/reel/Ddm3LHisOm6/", channel=CH, title=HEAD, project=p3.folder,
                 name="edit")
    it3.state, it3.job = D.SENT, j3
    dl.items.append(it3)
    dl.rows[it3.id] = D.VideoRow(dl, it3)
    j3.state = J.PAUSED                                     # what the close does to the running video (pause_for_close)
    _close(qt, q, dl)
    saved = json.loads((tmp_path / "appdata" / RK.QUEUE_FILE).read_text(encoding="utf-8"))
    assert [r["name"] for r in saved["rows"]] == ["edit"]

    ctx, q, dl = _open(qt)                                   # the app opened again
    try:
        # the queue: the edit is back, paused, with all its inputs; Resume goes on from where it was
        assert [(j.name, j.state) for j in q.jobs] == [("edit", J.PAUSED)]
        j = q.jobs[0]
        assert (j.channel, j.title, j.upload_title, j.mode, j.speed) == (CH, HEAD, YT, J.FAST, 100.0)
        assert j.competitor.name == "Video by memezar [Ddm3LHisOm6].mp4" and j.raw.name.endswith("S03E07.mkv")
        assert j.run_dir == run3 and q.rows[j.id].b_resume.isVisibleTo(q.rows[j.id])
        # New video: the three rows, as they were
        by = {i.name or i.raw_torrent: i for i in dl.items}
        a, b, c = by["convert"], by[MAGNET], by["edit"]
        assert a.state == D.FAILED and a.failed_step == "convert" and "3 s short" in a.error
        assert (a.channel, a.title, a.upload_title, a.mode, a.speed) == (CH, HEAD, YT, J.THOROUGH, 125.0)
        assert a.steps["download"].state == D.S_DONE and a.steps["convert"].state == D.S_FAIL
        assert b.state == D.STOPPED and b.failed_step == "download" and b.error == "stopped by you"
        assert b.comp_file == comp2 and b.channel == "Flick707" and b.title.startswith("The fact that he called")
        assert c.state == D.SENT and c.job is j                                       # finds its queue row again
        for it in (a, b):
            row = dl.rows[it.id]
            row.refresh()
            assert row.b_retry.isVisibleTo(row) and row.b_another_raw.isVisibleTo(row)
            assert row.b_another_comp.isVisibleTo(row)
        row = dl.rows[a.id]
        row.b_another_comp.click()                                  # the buttons work: the RAW kept, no new download
        assert dl.raw_file == a.raw_file and dl.head.text() == HEAD and dl.yt.text() == YT
    finally:
        _close(qt, q, dl)


def test_a_step_going_when_the_app_closed_comes_back_stopped_at_that_step(qt, tmp_path):
    from mr_app.tools import download_tool as D
    ctx, q, dl = _open(qt)
    it = D.Item("https://www.youtube.com/shorts/abc", raw_url="https://www.youtube.com/watch?v=xyz", channel=CH,
                title=HEAD)
    dl.items.append(it)
    dl.rows[it.id] = D.VideoRow(dl, it)
    dl._step(it, "download", D.S_DONE, 1.0, "")                                      # noqa: SLF001
    dl._step(it, "convert", D.S_RUN, 0.4, "converting the RAW")                      # noqa: SLF001
    it.state = D.WORKING
    _close(qt, q, dl)
    ctx, q, dl = _open(qt)
    try:
        [back] = dl.items
        assert back.state == D.STOPPED and back.failed_step == "convert" and back.error == RK.CLOSED_WHILE_WORKING
        assert back.steps["download"].state == D.S_DONE and back.steps["convert"].state == D.S_WAIT
        row = dl.rows[back.id]
        row.refresh()
        assert row.b_retry.isVisibleTo(row)
        queued = []
        dl.queue = lambda item, stages, default_template=False: queued.append((item.id, stages))
        dl.retry(back)                                                     # Try again: on from the convert step
        assert queued == [(back.id, ("convert",))]
    finally:
        _close(qt, q, dl)


def test_a_queue_row_that_could_not_be_paused_comes_back_failed_with_try_again(qt, tmp_path):
    for f in ("c.mp4", "r.mp4"):
        (tmp_path / f).write_bytes(b"x")
    ctx, q, dl = _open(qt)
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4", channel=CH, title=HEAD, mode=J.THOROUGH, speed=125.0)
    q._add_row(j)                                                                   # noqa: SLF001
    j.state = J.RUNNING                                    # its run had not made its folder yet: nothing to resume
    w = J.Job("waiting", tmp_path / "c.mp4", tmp_path / "r.mp4")
    q._add_row(w)                                                                   # noqa: SLF001
    _close(qt, q, dl)
    ctx, q, dl = _open(qt)
    try:
        by = {x.name: x for x in q.jobs}
        a = by["video4"]
        assert a.state == J.DONE and a.result["label"] == "FAIL" and RK.CLOSED_WHILE_RUNNING in a.result["failures"]
        assert (a.mode, a.speed, a.channel, a.title) == (J.THOROUGH, 125.0, CH, HEAD)
        row = q.rows[a.id]
        row.refresh()
        assert row.b_retry.isVisibleTo(row) and row.b_another.isVisibleTo(row)
        assert by["waiting"].state == J.READY                                   # a waiting row waits again
    finally:
        _close(qt, q, dl)


def test_history_has_try_again_and_both_buttons_on_every_failure(qt, tmp_path):
    from mr_app.core import history as H
    from mr_app.tools import history_tool as HT
    p = _project(tmp_path / "p", "convert")
    p.set_step("download", PJ.DONE)
    p.set_step("convert", PJ.FAILED, error="the convert check: the sound is 3 s short")
    H.log_event("Download failed", "https://www.youtube.com/watch?v=gone", "the video is private", H.FAIL,
                "one line", CH, inputs={"raw": "https://www.youtube.com/watch?v=gone",
                                        "competitor": "https://www.instagram.com/reel/Ddm3LHisOm6/",
                                        "headline": HEAD, "upload_title": YT, "channel": CH, "mode": J.FAST,
                                        "speed": 100.0, "kind": "full", "step": "download"})
    ctx, q, dl = _open(qt)
    h = HT.HistoryPage(ctx)
    try:
        h.set_projects([dict(p.data, _folder=str(p.folder))])
        items = [h.items.itemAt(i).widget() for i in range(h.items.count())]
        withb = [w for w in items if getattr(w, "b_again", None) is not None]
        whats = sorted(w.ev.what for w in withb)
        assert whats == ["Convert failed", "Download failed"]                    # no button was there before
        for w in withb:
            assert w.b_retry is not None and w.b_comp is not None
        conv = next(w for w in withb if w.ev.what == "Convert failed")
        queued = []
        dl.queue = lambda item, stages, default_template=False: queued.append((item.project, stages))
        conv.b_again.click()                                     # Try again: the project's row, from the convert
        assert ctx.selected[-1] == "download" and queued == [(p.folder, ("convert",))]
        [row_it] = [i for i in dl.items if i.project == p.folder]
        assert (row_it.channel, row_it.title, row_it.upload_title) == (CH, HEAD, YT)
        logged = next(w for w in withb if w.ev.what == "Download failed")
        added = []
        dl.add = lambda: added.append((dl.raw.text(), dl.comp.text(), dl.head.text(), dl.yt.text())) or None
        logged.b_again.click()                                   # the same video added again (no project folder)
        assert added == [("https://www.youtube.com/watch?v=gone", "https://www.instagram.com/reel/Ddm3LHisOm6/",
                          HEAD, YT)]
        logged.b_comp.click()                                    # another competitor: the RAW's link kept
        assert dl.raw.text() == "https://www.youtube.com/watch?v=gone" and dl.comp.text() == ""
        assert dl.head.text() == HEAD and dl.yt.text() == YT
    finally:
        _close(qt, q, dl)
        h.deleteLater()
        qt.processEvents()


def test_off_by_the_switch_and_a_list_never_saved_reads_the_paused_videos(qt, tmp_path, monkeypatch):
    assert RK.load_queue() is None and RK.load_newvideo() is None                  # nothing saved yet
    monkeypatch.setenv("MOVIERECAPS_KEEP_ROWS", "0")
    assert not RK.save_queue([]) and RK.load_queue() is None
