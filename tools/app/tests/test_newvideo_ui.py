"""Day 12, the Download tab's "New video" form and rows on the real widgets (no screen: Qt's offscreen platform; the
slow stages are fakes that run at once): the labels and the "Will make" line, steps that wait for the buttons unless
Automatic is ON, the headline asked for on the row or the default template, a failed step's red pill and Try again
from that step, Just cuts and Add a competitor, newest on top, and "Turn off the PC" (never really)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import flow  # noqa: E402
from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import newvideo as NV  # noqa: E402
from mr_app.core import projects as P  # noqa: E402
from mr_app.tools import download_tool as D  # noqa: E402
from mr_app.ui import motion as Mo  # noqa: E402

YT = "https://youtu.be/AbCdEf12345"
COMP = "https://youtube.com/shorts/Zx9Yw8Vu7Ts"


class FakeQueue:
    def __init__(self):
        self.jobs = []
        self.running = False

    def add_project_job(self, job, autostart=False):
        self.jobs.append((job, autostart))

    def busy(self):
        return False


@pytest.fixture()
def page(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))       # History's own log: this test's
    calls: list = []
    fail: dict = {}

    def stage(name):
        def run(self, out):
            calls.append((name, self.item.id, self.default_template))
            if fail.pop(name, None):
                raise flow.StepError(f"{name} broke: the disk is full")
            if name == "download" and self.item.project is None:
                p = P.create(tmp_path / "projects", competitor_url=self.item.competitor_url or None,
                             raw_url=self.item.raw_url or None)
                self.item.project = p.folder
                self.named.emit(self.item.id, str(p.folder))
            if name == "convert" and (self.item.competitor_url or self.item.comp_file):
                out["post_title"], out["video_headline"] = "The moment he realised", "Line one\nline two 😭"
            if name == "template" and self.default_template and not self.item.title:
                out["template"] = "default"
            if name == "cuts":
                out["result"] = {"label": "PASS", "cuts": 37, "run_dir": str(tmp_path / "cuts")}
        return run
    for s in ("download", "convert", "template", "cuts"):
        monkeypatch.setattr(D.StageThread, f"_{s}", stage(s))
    monkeypatch.setattr(D.StageThread, "start", lambda self: self.run())       # at once, in this thread
    monkeypatch.setattr(flow, "edit_job", lambda p, fast, speed: J.Job("x", Path("c.mp4"), Path("r.mp4")))
    queue = FakeQueue()
    toasts: list = []
    ctx = SimpleNamespace(pages={"match_cuts": queue}, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    p = D.DownloadPage(ctx)
    p.calls, p.fail, p.queue_page, p.toasts = calls, fail, queue, toasts
    p.resize(1000, 800)
    yield p
    Mo.set_enabled(True)
    p.poll.stop()
    p.deleteLater()
    app.processEvents()


def _fill(page, raw, comp="", head=""):
    page.raw.setText(raw)
    page.comp.setText(comp)
    page.head.setPlainText(head)


def test_the_labels_and_the_will_make_line_follow_what_is_pasted(page):
    _fill(page, YT)
    assert page.d_raw.text() == "YOUTUBE" and page.d_comp.text() == "EMPTY"
    assert page.will.text() == "Just cuts (every scene change)"
    page.comp.setText(COMP)
    assert page.will.text() == "Full Match cuts edit"
    _fill(page, "magnet:?xt=urn:btih:X")
    assert page.d_raw.text() == "TORRENT" and page.will.text().startswith("Needs a competitor")
    assert page.add() is None and "torrent needs a competitor" in page.msg.text()     # not allowed: asked for
    page.head.setPlainText("two\nlines")
    assert page.d_head.text() == "READY"


def test_the_headline_box_takes_two_lines_at_most(page):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    def enter():
        page.head.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier,
                                          "\r"))
    page.head.insertPlainText("line one")
    enter()
    page.head.insertPlainText("line two 😭")
    enter()                                                      # a third line: no
    assert page.head.toPlainText() == "line one\nline two 😭"


def test_automatic_off_every_step_waits_for_its_button_and_the_headline_is_asked_on_the_row(page):
    _fill(page, YT, COMP)
    it = page.add()
    row = page.rows[it.id]
    assert it.state == D.GATED and it.gate == D.G_CONVERT
    assert it.steps["download"].state == D.S_DONE and it.steps["convert"].state == D.S_WAIT
    assert page.now_text(it) == "Downloaded — press Convert when you want"
    assert not row.b_go.isHidden() and row.b_go.text() == "Convert"
    row.b_go.click()
    assert it.gate == D.G_SEND and row.b_go.text() == "Send to Match cuts"
    assert it.video_headline == "Line one\nline two 😭" and not row.titles.isHidden()
    row.b_go.click()                                             # no headline, Automatic OFF: asked on the row
    assert it.gate == D.G_HEADLINE and it.steps["template"].state == D.S_ASK
    assert row.cells["template"].small.text() == "needs a headline" and row.cells["template"].bar.target == 0.0
    assert not row.ask.isHidden() and row.b_ask.text() == "Make the template"
    assert "Waiting for the headline" in page.now_text(it)
    row.ask_head.setPlainText("When she finally\ngot the part 💀😭")
    row.b_ask.click()
    assert ("template", it.id, False) in page.calls
    assert it.title == "When she finally\ngot the part 💀😭"
    job, autostart = page.queue_page.jobs[-1]
    assert autostart and job.title == it.title and it.state == D.SENT
    job.state, job.result = J.DONE, {"label": "PASS", "run_dir": ""}
    page._poll()                                                 # noqa: SLF001
    assert it.state == D.DONE and it.steps["edit"].state == D.S_DONE and row.pill.word() == "PASS"


def test_automatic_on_runs_by_itself_and_no_headline_uses_the_default_template(page):
    page.auto.click()
    assert NV.automatic() is True                                # remembered
    _fill(page, YT, COMP)
    it = page.add()
    assert [c[0] for c in page.calls] == ["download", "convert", "template"]
    assert page.calls[-1][2] is True                             # the default template
    assert it.default_template and it.steps["template"].state == D.S_NOTE
    job, _ = page.queue_page.jobs[-1]
    job.state, job.result = J.DONE, {"label": "PASS"}
    page._poll()                                                 # noqa: SLF001
    row = page.rows[it.id]
    assert "default template" in row.pill.word() and "default template used" in page.now_text(it)


def test_an_edit_that_ends_wrong_pair_is_red_with_its_own_word_and_a_stopped_one_says_so(page):
    page.auto.click()
    _fill(page, YT, COMP, "a headline")
    a = page.add()
    _fill(page, YT.replace("Ab", "Bb"), COMP, "a headline")
    b = page.add()
    ja, jb = page.queue_page.jobs[0][0], page.queue_page.jobs[1][0]
    ja.state, ja.result = J.DONE, {"label": "WRONG PAIR", "failures": ["This competitor doesn't seem to come from "
                                                                      "this RAW"]}
    jb.state = J.STOPPED                                          # stopped in the queue before it ran
    page._poll()                                                  # noqa: SLF001
    assert a.state == D.FAILED and page.rows[a.id].pill.word() == "WRONG PAIR"
    assert "doesn't seem to come from this RAW" in page.now_text(a) and not page.rows[a.id].b_retry.isHidden()
    assert b.state == D.STOPPED and page.rows[b.id].pill.word() == "STOPPED"


def test_turning_automatic_on_lets_the_waiting_rows_go_on_at_once(page):
    _fill(page, YT, COMP)
    a = page.add()
    _fill(page, YT.replace("Ab", "Bb"), COMP)
    b = page.add()
    assert a.gate == b.gate == D.G_CONVERT
    page.auto.click()
    assert a.state == b.state == D.SENT
    assert sum(1 for c in page.calls if c[0] == "convert") == 2


def test_a_failed_step_stops_the_row_red_and_try_again_continues_from_that_step(page):
    page.auto.click()
    page.fail["convert"] = True
    _fill(page, YT, COMP, "a headline")
    it = page.add()
    row = page.rows[it.id]
    assert it.state == D.FAILED and it.failed_step == "convert" and row.pill.word() == "FAIL"
    assert it.steps["convert"].state == D.S_FAIL and "disk is full" in page.now_text(it)
    assert not row.b_retry.isHidden()
    n_download = sum(1 for c in page.calls if c[0] == "download")
    row.b_retry.click()
    assert sum(1 for c in page.calls if c[0] == "download") == n_download        # not downloaded again
    assert it.state == D.SENT and it.steps["convert"].state == D.S_DONE


def test_a_video_that_fails_before_its_folder_is_made_is_still_in_history_with_its_date(page):
    from mr_app.core import history as H
    page.fail["download"] = True
    _fill(page, YT, COMP)
    it = page.add()
    assert it.state == D.FAILED and it.project is None
    evs = [e for e in H.events([]) if e.what == "Download failed"]
    assert evs and evs[0].name == YT and "disk is full" in evs[0].detail and evs[0].kind == H.FAIL


def test_just_cuts_then_add_a_competitor_makes_it_a_full_run_without_a_new_download(page, tmp_path):
    page.auto.click()
    _fill(page, YT)
    it = page.add()
    assert it.kind == NV.JUST_CUTS and [c[0] for c in page.calls] == ["download", "convert", "cuts"]
    row = page.rows[it.id]
    assert it.state == D.DONE and row.kind.word() == "Just cuts" and row.pill.word() == "Cuts ready"
    assert page.now_text(it).startswith("Cut at 37 scene changes")
    assert not row.b_comp.isHidden()
    row.b_comp.click()
    row.ask_comp.setText(COMP)
    row.b_ask.click()
    assert it.kind == NV.FULL and it.roles == () and it.state == D.SENT
    assert [c[0] for c in page.calls][3:] == ["download", "convert", "template"]
    assert P.load(it.project).data["competitor"]["url"] == COMP


def test_a_magnet_uses_the_torrent_parts_picker_and_its_video_becomes_the_raw(page, tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    import mr_app.ui as UI

    class Picker(QtWidgets.QWidget):
        chosen = Signal(list)
        cancelled = Signal()
        failed = Signal(str)

        def __init__(self, source, save_dir):
            super().__init__()
            self.source, self.save_dir = source, save_dir

        def start_job(self, indexes):
            return Job()

    class Job(QObject):
        progress = Signal(dict)
        finished = Signal(str)
        failed = Signal(str)

    class Card(QtWidgets.QWidget):
        def __init__(self, job):
            super().__init__()
    fake = SimpleNamespace(TorrentPicker=Picker, TorrentCard=Card)
    monkeypatch.setitem(sys.modules, "mr_app.ui.torrent_panel", fake)
    monkeypatch.setattr(UI, "torrent_panel", fake, raising=False)
    page.auto.click()
    _fill(page, "magnet:?xt=urn:btih:EXAMPLE&dn=Example.Movie", COMP, "a headline")
    it = page.add()
    row = page.rows[it.id]
    picker = row.extras.itemAt(0).widget()
    assert isinstance(picker, Picker) and picker.source.startswith("magnet:")
    assert it.state == D.WORKING and it.steps["download"].state == D.S_RUN and not page.calls
    jobs = []
    orig = Picker.start_job
    monkeypatch.setattr(Picker, "start_job", lambda self, ix: jobs.append(orig(self, ix)) or jobs[-1])
    picker.chosen.emit([0])
    assert isinstance(row.extras.itemAt(0).widget(), Card)
    jobs[0].progress.emit({"share": 0.5, "percent": 50, "speed_text": "38 MB/s", "seeders": 398})
    assert it.steps["download"].share == pytest.approx(0.49) and "398 seeders" in page.now_text(it)
    video = tmp_path / "Example.Movie.mkv"
    video.write_bytes(b"x")
    jobs[0].finished.emit(str(video))
    assert it.raw_file == video and not it.raw_torrent
    assert [c[0] for c in page.calls] == ["download", "convert", "template"] and it.state == D.SENT


def test_without_the_torrent_part_a_magnet_says_so_plainly(page, monkeypatch):
    monkeypatch.setitem(sys.modules, "mr_app.ui.torrent_panel", None)       # an import of it fails
    _fill(page, "magnet:?xt=urn:btih:EXAMPLE", COMP)
    it = page.add()
    assert it.state == D.FAILED and "torrent support is coming" in page.now_text(it)


def test_the_real_stages_name_the_folder_from_the_post_then_the_headline_on_the_video(tmp_path, monkeypatch):
    """StageThread's own download / convert (the links read, the folder made, the titles read and saved, the
    folder renamed) with the slow parts faked."""
    from mr_app.core import download as DL
    from mr_app.core import titles as TT
    root = tmp_path / "projects"
    monkeypatch.setattr(D.paths, "projects_root", lambda: root)
    li = SimpleNamespace(title="Video by darkhumor", duration=40.0, why="best", info={
        "title": "Video by darkhumor", "description": "#fyp #viral @darkhumor"})
    lr = SimpleNamespace(title="Family Guy S10E18", duration=1300.0, why="best", info={})
    monkeypatch.setattr(DL, "probe_link", lambda url, cancel=None: li if "shorts" in url else lr)
    monkeypatch.setattr(D, "estimate_bytes", lambda li: 0)

    def fake_download(p, say, cancel, roles=None):
        for role in ("competitor", "raw"):
            (p.folder / f"{role}.mp4").write_bytes(b"x")
            p.update(**{role: {"file": f"{role}.mp4"}})
    monkeypatch.setattr(flow, "download_step", fake_download)
    monkeypatch.setattr(flow, "convert_step", lambda p, say, cancel, roles=None: None)
    monkeypatch.setattr(flow, "title_check_step", lambda p, title="": {
        "competitor_headline": "They stopped making comedy like this",
        "lines": ["They stopped making", "comedy like this"], "message": ""})
    monkeypatch.setattr(TT, "READER", lambda video: "They stopped making comedy like this 😭\nThis")
    it = D.Item(COMP, raw_url=YT, channel="Flick707")
    got = []
    th = D.StageThread(it, ("download", "convert"))
    th.done.connect(lambda i, res: got.append(res))
    th.run()
    assert got and got[0]["ok"], got
    assert got[0]["post_title"] == "" and got[0]["video_headline"] == "They stopped making\ncomedy like this 😭"
    assert it.project.name.endswith("_they-stopped-making-comedy-like-this")
    d = P.load(it.project).data
    assert d["video_headline"].startswith("They stopped making\n") and d["generic_name"] is True
    assert (it.project / TT.TITLES_TXT).is_file() and len(list(root.iterdir())) == 1


def test_newest_on_top_running_first_then_waiting_then_finished(page):
    _fill(page, YT, COMP)
    a = page.add()                                               # waits for Convert
    _fill(page, YT.replace("Ab", "Bb"), COMP)
    b = page.add()                                               # waits too, newer
    order = [r.item.id for r in page.list.rows]
    assert order == [b.id, a.id]
    page.rows[a.id].b_go.click()                                 # a converts: still waiting (for Send), newest first
    page.remove_item(b)
    assert [r.item.id for r in page.list.rows] == [a.id]
    ys = [r.y() for r in page.list.rows]
    assert ys == sorted(ys)


def test_turn_off_the_pc_after_the_last_work_shows_the_warning_never_the_real_shutdown(page, monkeypatch):
    from mr_app.core import shutdown as SD
    monkeypatch.setattr(SD, "SHUTDOWN", lambda: (_ for _ in ()).throw(AssertionError("never in a test")))
    made = []

    class FakeWarning:
        def __init__(self, parent):
            made.append(self)
            self.rejected = SimpleNamespace(connect=lambda fn: None)

        def start(self):
            self.started = True

        def isVisible(self):                                     # noqa: N802
            return True
    page.shutdown_dialog = FakeWarning
    page.offpc.setChecked(True)
    page._check_shutdown()                                       # noqa: SLF001 - nothing ran: no warning
    assert not made
    page.auto.click()
    _fill(page, YT)
    page.add()                                                   # Just cuts runs and finishes
    page._check_shutdown()                                       # noqa: SLF001
    assert len(made) == 1 and made[0].started
    page._check_shutdown()                                       # noqa: SLF001 - once
    assert len(made) == 1


def test_the_warning_dialog_counts_down_and_cancel_keeps_the_pc_on(page, monkeypatch):
    from mr_app.core import shutdown as SD
    off = []
    monkeypatch.setattr(SD, "SHUTDOWN", lambda: off.append(1))
    w = D.ShutdownWarning(page, seconds=2)
    w.timer.stop()
    w._tick()                                                    # noqa: SLF001
    assert "1 s" in w.text.text()
    w.reject()
    w._tick()                                                    # noqa: SLF001
    assert w.cancelled and off == []


def test_the_titles_block_fills_the_headline_box_and_the_youtube_title(page):
    from mr_app.ui.titles_block import TitlesBlock
    t = TitlesBlock()
    got = []
    t.use_headline.connect(lambda s: got.append(("h", s)))
    t.use_title.connect(lambda s: got.append(("t", s)))
    t.set(None, None)
    assert t.post_text.text() == "reading ..." and not t.b_use.isEnabled()
    t.set("", "Line one\nline two 😭")
    assert t.post_text.text().startswith("nothing readable") and t.head_text.text() == "Line one / line two 😭"
    t.b_use.click()
    t.b_title.click()
    assert got == [("h", "Line one\nline two 😭"), ("t", "Line one line two 😭")]
