"""Day 16, task 1: "Try with another RAW" -- a run that failed because of its RAW keeps the YouTube title, the
headline, the channel and the competitor; New video only asks for the new RAW. History keeps a failed project's
title and text (from its project.json, so after a restart too).

Run: python -m pytest tools/app/tests/test_another_raw_day16.py"""
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
from mr_app.core import history as H  # noqa: E402
from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402

WRONG = ("WRONG PAIR: This competitor doesn't seem to come from this RAW (sound: 0 of 4 pieces found (0 %); picture: 0 "
         "of 16 frames found (0 %); checked in 16 s).")
TED2 = ("RuntimeError: conform raw: C:\\x\\Ted.2.2015.x265.mp4: could not decode source frames [1, 2, 3]... by PTS")
HEAD = "The fact that she thought her dad\nwould care is hilarious 📱😭"
YT = "When Your Dad Takes Your Phone Away 📱😭"
REAL = Path("D:/Shared/laptop_cases/2026-10-20_When Your Dad Takes Your Phone Away 📱😭")


def test_which_results_failed_because_of_the_raw():
    for lab in ("WRONG PAIR", "PICTURE NOT FOUND", "NOT IN THIS RAW"):
        assert AR.failed_by_raw({"label": lab})
    assert AR.failed_by_raw({"label": "FAIL", "stop": TED2})                      # a RAW that can't be read
    assert AR.failed_by_raw({"label": "FAIL", "reason_kind": "damaged video",
                             "reason_detail": "conform raw: Invalid data found when processing input"})
    assert not AR.failed_by_raw({"label": "FAIL", "reason_kind": "damaged video",
                                 "reason_detail": "the competitor: Invalid data found when processing input"})
    for res in ({"label": "PASS"}, {"label": "OK with a note"}, {"label": "STOPPED"}, None,
                {"label": "FAIL", "reason_kind": "disk full", "stop": "No space left on device"}):
        assert not AR.failed_by_raw(res)


def _project(tmp_path, result="WRONG PAIR", error=WRONG, comp=True):
    p = PJ.create(tmp_path, name="dad phone")
    if comp:
        (p.folder / "Video by wasted [DdVZePAga2I].mp4").write_bytes(b"x")
    p.update(channel="MikeMemesYou", title=HEAD, upload_title=YT,
             competitor={"url": "https://www.instagram.com/reel/DdVZePAga2I/", "file": "Video by wasted [DdVZePAga2I].mp4"},
             raw={"file": "Dr..Dolittle.2.2001.1080p.BluRay.x264.AAC5.1-[YTS.GG - YTS.BZ].mp4"})
    p.set_step("edit", PJ.FAILED, result=result, error=error, mode="fast", speed=125.0,
               failures=[f"the run stopped (exit 2): {error}"])
    return PJ.load(p.folder)


def test_a_failed_project_keeps_its_title_text_channel_and_competitor(tmp_path):
    p = _project(tmp_path)
    assert AR.edit_failed_by_raw(p.data)
    k = AR.kept_from_project(p.folder)
    assert (k.title, k.upload_title, k.channel) == (HEAD, YT, "MikeMemesYou")
    assert k.competitor_file == p.folder / "Video by wasted [DdVZePAga2I].mp4"
    assert k.competitor_url.endswith("DdVZePAga2I/") and k.mode == "fast" and k.speed == 125.0
    assert k.why == "WRONG PAIR" and "YTS.GG" in k.raw_name
    msg = AR.form_message(k)
    assert "the YouTube title, the headline, the channel and the competitor kept" in msg and "new RAW" in msg
    gone = _project(tmp_path / "b", comp=False)                     # the clip is gone: its link instead
    k2 = AR.kept_from_project(gone.folder)
    assert k2.competitor_file is None and k2.has_competitor
    ted = _project(tmp_path / "c", result="FAIL", error=TED2)
    assert AR.edit_failed_by_raw(ted.data)
    ok = _project(tmp_path / "d", result="FAIL", error="the disk is full")
    assert not AR.edit_failed_by_raw(ok.data)


def test_history_keeps_the_failed_projects_title_and_text(tmp_path):
    p = _project(tmp_path)
    evs = H.events([dict(p.data, _folder=str(p.folder))], log=False)
    fail = [e for e in evs if e.what.startswith("Edit failed")]
    assert len(fail) == 1 and fail[0].retry and fail[0].folder == str(p.folder)
    assert fail[0].upload_title == YT and fail[0].title == HEAD
    assert H.search(evs, "takes your phone") and fail[0] in H.search(evs, "takes your phone")
    assert not any(e.retry for e in evs if e is not fail[0])


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


def _download_page(qt):
    from mr_app.tools import download_tool as D
    ctx = SimpleNamespace(notify=lambda t, x: None, toast=lambda text, kind="ok": None, selected=[])
    ctx.select = ctx.selected.append
    page = D.DownloadPage(ctx)
    ctx.pages = {"download": page}
    return page, ctx


def _filled(page, comp: Path):
    assert page.raw.text() == "" and page.raw_file is None
    assert page.comp.text() == str(comp) and page.comp_file == comp
    assert page.head.text() == HEAD and page.yt.text() == YT
    assert page.ch.value == "MikeMemesYou"
    assert "kept" in page.msg.text() and "new RAW" in page.msg.text()


def test_the_queue_row_button_fills_new_video_with_everything_but_the_raw(qt, tmp_path, monkeypatch):
    from mr_app.tools import match_cuts_tool as M
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0)
    p = _project(tmp_path)
    page_d, ctx = _download_page(qt)
    ctx.set_strip_queue = lambda n, running: None
    q = M.MatchCutsPage(ctx)
    ctx.pages["match_cuts"] = q
    try:
        raw = tmp_path / "r.mp4"
        raw.write_bytes(b"x")
        comp = p.folder / "Video by wasted [DdVZePAga2I].mp4"
        j = J.Job("dad", comp, raw, project=p.folder, channel="MikeMemesYou", title=HEAD)
        j.state, j.result, j.run_dir = J.DONE, {"label": "WRONG PAIR", "stop": WRONG}, tmp_path
        q._add_row(j)                                                              # noqa: SLF001
        row = q.rows[j.id]
        row.refresh()
        assert row.b_another.isVisibleTo(row) and row.b_another.text() == "Try with another RAW"
        row.b_another.click()
        assert ctx.selected == ["download"]
        _filled(page_d, comp)
        j.result = {"label": "PASS"}
        row.refresh()
        assert not row.b_another.isVisibleTo(row)
    finally:
        for w in (q, page_d):
            getattr(w, "poll", SimpleNamespace(stop=lambda: None)).stop()
            w.deleteLater()
        qt.processEvents()


def test_the_history_button_fills_new_video_too(qt, tmp_path):
    from mr_app.tools import history_tool as HT
    p = _project(tmp_path)
    page_d, ctx = _download_page(qt)
    h = HT.HistoryPage(ctx)
    try:
        h.set_projects([dict(p.data, _folder=str(p.folder))])
        items = [h.items.itemAt(i).widget() for i in range(h.items.count())]
        withb = [w for w in items if w.b_retry is not None]
        assert len(withb) == 1 and "YouTube: " + YT in withb[0].line3 and "Headline: " in withb[0].line3
        withb[0].b_retry.click()
        assert ctx.selected == ["download"]
        _filled(page_d, p.folder / "Video by wasted [DdVZePAga2I].mp4")
    finally:
        page_d.poll.stop()
        for w in (h, page_d):
            w.deleteLater()
        qt.processEvents()


@pytest.mark.skipif(not (REAL / "project.json").is_file(), reason="the real Dolittle WRONG PAIR project is on the PC")
def test_the_real_dolittle_wrong_pair_project(qt):
    """The real project (the YTS 'Dr. Dolittle 2' that holds the 1998 film): read only, nothing is written."""
    k = AR.kept_from_project(REAL)
    assert AR.edit_failed_by_raw(PJ.read_json(REAL / "project.json"))
    assert k.title == HEAD and k.upload_title == YT and k.channel == "MikeMemesYou"
    assert k.competitor_file is not None and k.competitor_file.is_file() and k.why == "WRONG PAIR"
    page_d, _ctx = _download_page(qt)
    try:
        page_d.prefill_another_raw(k)
        _filled(page_d, k.competitor_file)
    finally:
        page_d.poll.stop()
        page_d.deleteLater()
        qt.processEvents()
