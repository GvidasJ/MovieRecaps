"""Day 17, task 4: every failed or stopped row -- a download stop, a WRONG PAIR, a convert stop -- has BOTH "Try with
another RAW" (keeps the competitor, headline, YouTube title, channel) and "Try with another competitor" (keeps the
RAW -- no new download --, headline, YouTube title, channel), on the queue, New video and History.

Run: python -m pytest tools/app/tests/test_another_comp_day17.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import another_raw as AR  # noqa: E402
from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402

HEAD = "The fact that the teacher assumed\nthe worst about him is hilarious 🏫😭"
YT = "The Most Clueless Teacher In Comedy History 🤣"
CH = "MikeMemesYou"


@pytest.fixture
def qt(monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.ui import background as BG
    from mr_app.ui import motion as Mo
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    monkeypatch.setattr(BG, "call", lambda fn, done=None, failed=None, work=False: (done or (lambda _r: None))(fn()))
    yield app
    Mo.set_enabled(True)


def _pages(qt):
    from mr_app.tools import download_tool as D
    ctx = SimpleNamespace(notify=lambda t, x: None, toast=lambda text, kind="ok": None, selected=[])
    ctx.select = ctx.selected.append
    page = D.DownloadPage(ctx)
    ctx.pages = {"download": page}
    return page, ctx


def _project(tmp_path, result="WRONG PAIR", error="WRONG PAIR: this competitor doesn't come from this RAW"):
    p = PJ.create(tmp_path, name="teacher")
    comp = p.folder / "Video by memezar [Ddm3LHisOm6].mp4"
    comp.write_bytes(b"c")
    raw = tmp_path / "Torrents" / "Everybody Hates Chris - S03E11.mkv"          # the download, in place
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"r")
    p.update(channel=CH, title=HEAD, upload_title=YT,
             competitor={"url": "https://www.instagram.com/reel/Ddm3LHisOm6/", "file": comp.name},
             raw={"source_file": str(raw), "file": raw.name, "title": raw.stem})
    p.set_step("edit", PJ.FAILED, result=result, error=error, mode="fast", speed=100.0, failures=[error])
    return PJ.load(p.folder), comp, raw


def _kept_raw(page, raw: Path):
    assert page.raw.text() == str(raw) and page.raw_file == raw              # the file: no new download
    assert page.comp.text() == "" and page.comp_file is None
    assert page.head.text() == HEAD and page.yt.text() == YT and page.ch.value == CH
    assert "Try with another competitor" in page.msg.text() and "the RAW" in page.msg.text()
    assert "no new download" in page.msg.text()


def _kept_comp(page, comp: Path):
    assert page.raw.text() == "" and page.raw_file is None
    assert page.comp_file == comp and page.head.text() == HEAD and page.yt.text() == YT and page.ch.value == CH


def _close(qt, *ws):
    for w in ws:
        getattr(w, "poll", SimpleNamespace(stop=lambda: None)).stop()
        w.deleteLater()
    qt.processEvents()


def test_which_rows_get_both_buttons():
    assert AR.failed_or_stopped("stopped", None)
    assert AR.failed_or_stopped("done", {"label": "WRONG PAIR"}) and AR.failed_or_stopped("done", {"label": "FAIL"})
    for ok in ("PASS", "OK with a note", "Cuts ready"):
        assert not AR.failed_or_stopped("done", {"label": ok})
    assert not AR.failed_or_stopped("done", {})


def test_a_wrong_pair_on_the_queue_shows_both_and_each_fills_in_everything(qt, tmp_path, monkeypatch):
    from mr_app.tools import match_cuts_tool as M
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0)
    p, comp, raw = _project(tmp_path)
    page_d, ctx = _pages(qt)
    ctx.set_strip_queue = lambda n, running: None
    q = M.MatchCutsPage(ctx)
    ctx.pages["match_cuts"] = q
    try:
        j = J.Job("teacher", comp, p.folder / raw.name, project=p.folder, channel=CH, title=HEAD)
        j.state, j.result, j.run_dir = J.DONE, {"label": "WRONG PAIR"}, tmp_path
        q._add_row(j)                                                              # noqa: SLF001
        row = q.rows[j.id]
        row.refresh()
        assert row.b_another.isVisibleTo(row) and row.b_another_comp.isVisibleTo(row)
        assert row.b_another_comp.text() == "Try with another competitor"
        row.b_another_comp.click()
        assert ctx.selected == ["download"]
        _kept_raw(page_d, raw)
        row.b_another.click()
        _kept_comp(page_d, comp)
        j.result = {"label": "FAIL", "stop": "the disk is full"}                   # any stop: both too
        row.refresh()
        assert row.b_another.isVisibleTo(row) and row.b_another_comp.isVisibleTo(row)
        j.result = {"label": "PASS"}
        row.refresh()
        assert not row.b_another.isVisibleTo(row) and not row.b_another_comp.isVisibleTo(row)
    finally:
        _close(qt, q, page_d)


def test_a_download_stop_and_a_convert_stop_on_new_video_show_both(qt, tmp_path):
    from mr_app.tools import download_tool as D
    page, ctx = _pages(qt)
    try:
        # a download stop: the torrent RAW never came -- the competitor file and the texts are kept, the magnet too
        comp = tmp_path / "Video by memezar [Ddm3LHisOm6].mp4"
        comp.write_bytes(b"c")
        it = D.Item("https://www.instagram.com/reel/Ddm3LHisOm6/", channel=CH, title=HEAD,
                    raw_torrent="magnet:?xt=urn:btih:ABC&dn=Everybody.Hates.Chris.S03", comp_file=comp)
        it.upload_title, it.state, it.failed_step, it.error = YT, D.STOPPED, "download", "the download was removed"
        page.rows[it.id] = D.VideoRow(page, it)
        row = page.rows[it.id]
        row.refresh()
        assert row.b_another_raw.isVisibleTo(row) and row.b_another_comp.isVisibleTo(row)
        row.b_another_comp.click()
        assert page.raw.text().startswith("magnet:") and page.comp.text() == ""
        assert page.head.text() == HEAD and page.yt.text() == YT and page.ch.value == CH
        row.b_another_raw.click()
        assert page.raw.text() == "" and page.comp_file == comp and page.yt.text() == YT
        # a convert stop: the RAW is here (a file), the project knows everything
        p, comp2, raw = _project(tmp_path / "b", result="FAIL", error="the convert check: the sound is 3 s short")
        it2 = D.Item("https://www.instagram.com/reel/Ddm3LHisOm6/", channel=CH, title=HEAD, raw_file=raw,
                     project=p.folder)
        it2.state, it2.failed_step, it2.error = D.FAILED, "convert", "the sound is 3 s short"
        page.rows[it2.id] = D.VideoRow(page, it2)
        row2 = page.rows[it2.id]
        row2.refresh()
        assert row2.b_another_raw.isVisibleTo(row2) and row2.b_another_comp.isVisibleTo(row2)
        row2.b_another_comp.click()
        _kept_raw(page, raw)
        row2.b_another_raw.click()
        _kept_comp(page, comp2)
        it2.state, it2.result = D.DONE, {"label": "PASS"}
        row2.refresh()
        assert not row2.b_another_raw.isVisibleTo(row2) and not row2.b_another_comp.isVisibleTo(row2)
    finally:
        _close(qt, page)


def test_history_has_both_on_any_failed_edit(qt, tmp_path):
    from mr_app.tools import history_tool as HT
    p, comp, raw = _project(tmp_path, result="FAIL", error="the run stopped: the disk is full")
    page_d, ctx = _pages(qt)
    h = HT.HistoryPage(ctx)
    try:
        h.set_projects([dict(p.data, _folder=str(p.folder))])
        items = [h.items.itemAt(i).widget() for i in range(h.items.count())]
        withb = [w for w in items if getattr(w, "b_comp", None) is not None]
        assert len(withb) == 1 and withb[0].b_retry is not None
        withb[0].b_comp.click()
        assert ctx.selected == ["download"]
        _kept_raw(page_d, raw)
    finally:
        _close(qt, h, page_d)
