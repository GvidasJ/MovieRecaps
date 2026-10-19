"""Night 10: the new tabs themselves (no screen: Qt's offscreen platform; no network, no tool runs -- the background
work is stood in for): the window's tabs in the order of the work, the Download tab's link checks and its hand-over
to Match cuts (the project's edit job, started by itself, a channel without a title waiting), the Convert tab's
queue, the template editor's title check, and the Match cuts row's template line."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import jobs as J, projects as P  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _video(path: Path, seconds: float) -> Path:
    from match_cuts.common import ffmpeg_bin
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=160x284:rate=30:d={seconds}", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


class _Ctx:
    def __init__(self):
        self.pages, self.notes, self.strip = {}, [], None

    def notify(self, title, text):
        self.notes.append((title, text))

    def set_strip_queue(self, n, running):
        self.strip = (n, running)


def test_the_window_has_the_four_tabs_and_every_page_under_one(qapp):
    """Night 11: Match cuts (Queue, Learn) · Board · Progress (Download, Convert, Template) · History."""
    from mr_app.tools import tools
    from mr_app.ui import main_window as MW
    win = MW.MainWindow(tools())
    assert [t.text for t in win.tabbar.tabs] == ["Match cuts", "Board", "Progress", "History"]
    assert set(win.pages) == {"match_cuts", "learn", "board", "download", "convert", "template", "history"}
    win.select("template")
    assert win.tabbar.index == 2 and win.stack.currentIndex() == 2
    assert win.areas[2].current_key() == "template" and win.areas[2].sub.index == 2
    win.select("learn")
    assert win.tabbar.index == 0 and win.areas[0].current_key() == "learn"
    win.close()


def test_download_says_what_is_wrong_with_a_link_and_queues_a_right_one(qapp, monkeypatch, tmp_path):
    from mr_app.tools import download_tool as D
    started = []

    class FakeThread:
        def __init__(self, item):
            self.item = item
            self.named = SimpleNamespace(connect=lambda f: None)
            self.progress = SimpleNamespace(connect=lambda f: None)
            self.done = SimpleNamespace(connect=lambda f: None)

        def start(self):
            started.append(self.item)
    monkeypatch.setattr(D, "PrepareThread", FakeThread)
    page = D.DownloadPage(_Ctx())
    page.comp.setText("youtube.com/shorts/short")                         # an id must have 11 characters
    page.raw.setText("https://www.youtube.com/watch?v=ABCDEFGHIJK")
    page.add()
    assert "Competitor: the link is wrong" in page.msg.text() and not page.items
    page.comp.setText("https://www.youtube.com/shorts/abcdefghijk")
    page.raw.setText("")
    page.add()
    assert "RAW: paste the RAW's link or drop its file" in page.msg.text()
    raw = _video(tmp_path / "raw.mp4", 2)
    page._set_raw_file(raw)                                                # noqa: SLF001 - as a drop does
    page.ch.set("LouLaughs") if "LouLaughs" in page.ch._b else None       # noqa: SLF001
    page.title.setText("The fact that dad became the tour guide is hilarious 😭")
    page.add()
    assert len(page.items) == 1 and started and started[0].raw_file == raw and not started[0].raw_url
    assert started[0].title.endswith("😭") and page.comp.text() == "" and page.title.text() == ""


def test_a_ready_video_goes_to_match_cuts_and_starts_unless_its_title_is_missing(qapp, tmp_path, monkeypatch):
    from mr_app.tools import download_tool as D
    from mr_app.tools.match_cuts_tool import MatchCutsPage
    ctx = _Ctx()
    mc = MatchCutsPage(ctx)
    starts = []
    monkeypatch.setattr(mc, "start", lambda confirm=True: starts.append(confirm))
    ctx.pages["match_cuts"] = mc
    page = D.DownloadPage(ctx)
    p = P.create(tmp_path, competitor_url="https://youtu.be/abcdefghijk", raw_url="https://youtu.be/ABCDEFGHIJK")
    _video(p.folder / "c.mp4", 1)
    _video(p.folder / "r.mp4", 3)
    p.update(competitor={"file": "c.mp4"}, raw={"file": "r.mp4"})
    it = D.Item("https://youtu.be/abcdefghijk", channel="LouLaughs", title="My title", project=p.folder,
                name=p.folder.name, state=D.READY, headline="Their headline")
    page.items.append(it)
    page.rows[it.id] = D.ItemRow(page, it)
    page.send_to_edit(it, start=True)
    job = mc.jobs[-1]
    import time
    t0 = time.perf_counter()                       # day 12: it starts once its lengths are read (in the background)
    while not starts and time.perf_counter() - t0 < 10:
        qapp.processEvents()
        time.sleep(0.01)
    assert it.state == D.SENT and starts == [False]                        # started by itself, no question asked
    assert job.project == p.folder and job.out_dir == p.folder and job.run_name == "edit"
    assert (job.channel, job.title, job.headline) == ("LouLaughs", "My title", "Their headline")
    assert P.load(p.folder).data["title"] == "My title"
    it2 = D.Item("https://youtu.be/abcdefghijk", channel="LouLaughs", title="", project=p.folder, state=D.READY)
    page.items.append(it2)
    page.rows[it2.id] = D.ItemRow(page, it2)
    page.send_to_edit(it2, start=True)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.0:
        qapp.processEvents()
        time.sleep(0.01)
    assert starts == [False] and "type the title" in it2.message           # a channel without a title waits


def test_convert_queues_the_videos_of_a_drop_and_shows_the_result(qapp, tmp_path, monkeypatch):
    from mr_app.tools import convert_tool as C
    runs = []

    class FakeThread:
        def __init__(self, item, delete_original, gpu):
            self.item, self.delete, self.gpu = item, delete_original, gpu
            self._done = []
            self.progress = SimpleNamespace(connect=lambda f: None)
            self.done = SimpleNamespace(connect=self._done.append)

        def start(self):
            runs.append((self.item.path.name, self.delete, self.gpu))
            for f in self._done:
                f(self.item.id, True, "rewrapped to MP4, no re-encode (lossless), checked")

        def wait(self, ms):
            return True
    monkeypatch.setattr(C, "ConvertThread", FakeThread)
    page = C.ConvertPage(_Ctx())
    a = _video(tmp_path / "in" / "a.mkv", 1)
    b = _video(tmp_path / "in" / "b.webm", 1)
    (tmp_path / "in" / "notes.txt").write_text("x")
    page.add_paths([tmp_path / "in"])
    assert [r[0] for r in runs] == ["a.mkv", "b.webm"] and runs[0][1:] == (True, "auto")
    assert all(i.state == "done" and i.text.startswith("rewrapped") for i in page.items)
    page.add_paths([a])                                                   # done ones can be converted again
    assert len(page.items) == 3


def test_the_template_editor_checks_the_title_against_the_competitors_headline(qapp, monkeypatch):
    from mr_app.ui import template_editor as TE
    monkeypatch.setattr(TE, "channel_names", lambda: [])
    ed = TE.TemplateEditor("", "", headline="When dad tries to be the tour guide")
    ed.t.setPlainText("The tour guide is when dad tries to be")
    assert ed.check.word.text() == "TOO CLOSE" and "change your title" in ed.check.note.text()
    ed.t.setPlainText("Nobody asked dad for a tour")
    assert ed.check.word.text() == "OK"
    ed.set_headline("")
    assert "none found" in ed.comp.text()
    assert "templates\\default.png" in ed.info.text()                      # no channel: the default template


def test_the_match_cuts_row_shows_and_edits_the_template(qapp, tmp_path):
    from mr_app.tools.match_cuts_tool import JobRow, MatchCutsPage
    page = MatchCutsPage(_Ctx())
    j = J.Job("video4", tmp_path / "c.mp4", tmp_path / "r.mp4", channel="Flick707", title="When dad tries")
    row = JobRow(page, j)
    assert row.tpl_line.text().startswith("Template: Flick707") and "+ watermark" in row.tpl_line.text()
    assert row.tpl.isEnabled()
    j.state = J.RUNNING
    row.refresh()
    assert not row.tpl.isEnabled()
